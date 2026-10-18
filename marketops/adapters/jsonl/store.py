"""JSONL 实现：一行一条 JSON，人类可以直接 ``grep``。

## 它现在的位置是什么

B5 之后 SQLite 是**主存**，本模块不再是流水线的默认选择。它留下来是因为它有
SQLite 没有的那一个性质：**不需要任何工具就能读**。审计一份三个月前的产出时，
``grep grounds@`` 与「先装 sqlite3、再想起表名」是两种成本 —— 而当产出要交给人
（或交给一个只拿到目录的第三方）时，这个差别是决定性的。

所以它的角色是**导出与审计通道**，不是备用主存。两者的判据也不同：

* 这里：可读、可 grep、损坏时报错（不静默跳过）。
* SQLite：并发下不超支、崩溃后不丢、事务性。

### 它明确**不**保证的事

写入不是原子的、也不是加锁的。``put_route`` 的「先全量读、再看是否已存在、
最后追加」在单进程下正确，两个进程同时写会各写各的。**这正是 §8 要求原子预留
的文件格式做不到的那件事** —— 所以它不再是主存。要并发正确，用
``adapters/sqlite``。

``put_evidence`` 的幂等检查读的是**整个账号文件**：账号的证据多到一定程度时，
写第 N 条要先把 N-1 条全读一遍。这是上面那条取舍的另一个代价，一起记在这里。

## 这个通道**绑在一个工作区上**，而且这是它唯一说得清的事

本实现持有 ``Settings``，所以它的工作区是**给定的**：``settings.profile``，也就是
``RoleGuard`` 用来判权的那个值。三条判据都从它来：

* **写**：对象的 ``workspace`` 与它不同 → 拒绝（这一行不属于这个部署）；
* **读**：文件里某一行的 ``workspace`` 与它不同 → **拒绝并点名那一行**（文件 + 行号），
  不是跳过那一行、返回剩下的。理由与 ``adapters/sqlite/evidence_store.py``
  那一节逐字相同：「这一行不属于你」与「这里没有这一行」在调用点长成同一个空结果，
  越权读就成了一次静默成功；
* **缺这一格**（v1 写下的行）：与「写着别人的名字」分开报，并且报的是**怎么修**
  （``python -m marketops.adapters.sqlite.migrate`` 会把库与存档一起迁）。

SQLite 那一侧没有这条「给定」：它只拿到一个路径（那样才能在临时文件上被测出来），
所以它的绑定由**第一行写入**建立、记在库里（``db.py::bind_workspace``）。两个实现在
这一点上的一致之处是判据本身 —— 一个库/一个目录只装一个工作区的产出，不一致就拒绝。
"""

from __future__ import annotations

import json
from pathlib import Path

from ...config import Settings
from ...domain.entities import RouteOpportunity
from ...domain.errors import EvidenceError
from ...domain.evidence import Evidence
from ...domain.route_records import (
    CanonicalContactRoute,
    RouteObservation,
    RouteRejection,
    fold_observation,
    identity_mismatch,
)
from ...domain.util import check_canonical_id
from ...ports.store import EvidenceStore

_ENCODING = "utf-8"


class LocalEvidenceStore(EvidenceStore):
    """基于本地 JSONL 的 Evidence Store。

    目录布局（全部来自 ``Settings``，不含任何硬编码路径）::

        <data_root>/state/evidence/<account_id>.jsonl
        <data_root>/state/routes/<account_id>.jsonl
        <data_root>/state/route-opportunities/<account_id>.jsonl
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    def put_evidence(self, evidence: Evidence) -> None:
        self._require_own(evidence.workspace, where=f"evidence[{evidence.evidence_id}]")
        path = self._path_for(self._settings.evidence_dir, evidence.account_id)
        self._append(path, evidence.model_dump(mode="json"), key="evidence_id")

    def list_evidence(self, account_id: str) -> list[Evidence]:
        path = self._path_for(self._settings.evidence_dir, account_id)
        return [Evidence.model_validate(row) for row in self._read_owned(path)]

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        """按 id 取。**要扫目录**：本实现的文件名是 ``account_id``，而查询键是
        ``evidence_id``，两者之间没有索引 —— 代价与理由写在 ``ports/store.py``
        那条「三个实现的代价不一样」里。

        跨文件撞上同一个 id 时报错，不取第一个：本实现与另外两个共享
        「同 id 配不同内容必须显性失败」这条不变量（见 ``ports/store.py``），
        而「取先扫到的那一个」会让这条不变量在**读**这一侧消失 —— 写的时候
        两个文件各自合法，读的时候答案取决于 ``sorted`` 的顺序。

        ``None`` 只有一个意思：**没有这个 id 的行**。别人的行在这里是
        :class:`EvidenceError`（``_read_owned``），不是「没找到」。
        """
        found: list[dict] = []
        for path in sorted(self._settings.evidence_dir.glob("*.jsonl")):
            found.extend(
                row
                for row in self._read_owned(path)
                if row.get("evidence_id") == evidence_id
            )
        if not found:
            return None
        first = found[0]
        for other in found[1:]:
            if other != first:
                raise EvidenceError(
                    f"evidence_id={evidence_id!r} 在不止一个账号文件里出现，且内容不同。\n"
                    "这个 id 是内容寻址的，所以同一个 id 配不同内容只可能意味着 id "
                    "被手工指定过。"
                )
        return Evidence.model_validate(first)

    def record_route(self, observation: RouteObservation) -> CanonicalContactRoute:
        """追加一条观察，再把**当前投影**写成一行。

        两件事在同一个方法里，理由见 ``ports/store.py``：分开就是「历史里有一条、
        投影里没有」的窗口。本实现给不了跨进程的原子性（下面那节解释了为什么，
        也解释了为什么它不是主存），但它至少不给**单进程**留这个窗口 ——
        而单进程正是这个通道被用到的唯一场合（导出、审计、离线核对）。

        ## 投影那一份是**重写**，不是追加

        ``<data_root>/state/routes/<account_id>.jsonl`` 从此是「每个规范键一行」
        的当前读法文件。追加式会让它慢慢长成一份观察日志 —— 而那正是要离开的
        形状，只不过换了个目录住。重写的代价是写第 N 个键要把文件读一遍，
        与 ``put_evidence`` 那一条同源（见模块 docstring），记在这里而不是藏起来。

        行按 ``canonical_key`` 排序写出：同一份存档在两台机器上导出两次必须
        逐字节相同，否则「对一下这两个文件」这件事没法做。
        """
        self._require_own(observation.workspace, where="route_observation")
        history = self._path_for(
            self._settings.route_observations_dir, observation.account_id
        )
        self._append(history, observation.model_dump(mode="json"), key="observation_id")

        existing = self._projection_for(observation.canonical_key)
        folded = fold_observation(existing, observation, at=observation.observed_at)
        path = self._path_for(self._settings.routes_dir, observation.account_id)
        self._upsert(path, folded.projection.model_dump(mode="json"), key="canonical_key")
        return folded.projection

    def list_routes(self, account_id: str) -> list[CanonicalContactRoute]:
        path = self._path_for(self._settings.routes_dir, account_id)
        rows = sorted(self._read_owned(path), key=lambda row: str(row.get("canonical_key")))
        return [CanonicalContactRoute.model_validate(row) for row in rows]

    def list_route_observations(self, account_id: str) -> list[RouteObservation]:
        path = self._path_for(self._settings.route_observations_dir, account_id)
        rows = [RouteObservation.model_validate(row) for row in self._read_owned(path)]
        return sorted(rows, key=lambda item: (item.observed_at, item.observation_id))

    def put_route_rejection(self, rejection: RouteRejection) -> None:
        """一张收据一行。**身份必须一致，改写账换新的**（见 :meth:`RouteRejection.identity`）。

        走的是 :meth:`_upsert` 而不是 :meth:`_append`：收据不是追加的历史（那是观察），
        它是「这一条候选在本库里被拒过」的唯一一行。此前那一版走 ``_append``，
        而 ``_append`` 在同键上要求**逐字节相同** —— 于是同一份材料第二次被结算
        （``run_id`` 变了，而 ``rejection_id`` 的原像里没有它）就撞上一个
        ``EvidenceError``。幂等的承诺是「再算一遍不会多出一行」，不是「再算一遍会抛
        异常」；要抛的那一种情况是 ``rejection_id`` 被手工指定过 —— 那由
        :meth:`_reject_rewritten_identity` 判，它比的是身份，不是整行。
        """
        self._require_own(rejection.workspace, where="route_rejection")
        path = self._path_for(self._settings.route_rejections_dir, rejection.account_id)
        self._reject_rewritten_identity(path, rejection)
        self._upsert(path, rejection.model_dump(mode="json"), key="rejection_id")

    def list_route_rejections(self, account_id: str) -> list[RouteRejection]:
        path = self._path_for(self._settings.route_rejections_dir, account_id)
        rows = [RouteRejection.model_validate(row) for row in self._read_owned(path)]
        return sorted(rows, key=lambda item: (item.occurred_at, item.rejection_id))

    def _projection_for(self, canonical_key: str) -> CanonicalContactRoute | None:
        """按规范键找当前投影 —— **跨账号文件找**，与 ``get_evidence`` 同一个理由：
        这个通道的分片键是 ``account_id``，而查询键是 ``canonical_key``。

        规范键把工作区与账号都算进了原像，所以正常情况下它只会出现在一个文件里。
        真出现在两个文件里时按 ``get_evidence`` 那条口径处理不了（投影本来就会
        随时间变，两份不同的投影不是「id 被手工指定」），所以这里取**时间上更晚的
        那一条** —— 与折叠自己的规则一致：当前读法是时间序的最后一次，不是第一个
        被扫到的。
        """
        found: list[CanonicalContactRoute] = []
        for path in sorted(self._settings.routes_dir.glob("*.jsonl")):
            found.extend(
                CanonicalContactRoute.model_validate(row)
                for row in self._read_owned(path)
                if row.get("canonical_key") == canonical_key
            )
        if not found:
            return None
        return max(found, key=lambda item: (item.last_observed_at, item.last_observation_id))

    @staticmethod
    def _upsert(path: Path, payload: dict, *, key: str) -> None:
        """一行一条、按 ``key`` 唯一：同键替换，新键插入，写回时按键排序。

        与 ``_append`` 分开而不是给它加一个 ``replace=True``：追加是「记住又发生了
        一次」，替换是「现在是什么」。一个布尔参数会让这两件事在调用点长得一样 ——
        而它们在这里被分开，正是因为把它们混在一起是这一片要修的那个 bug。
        """
        rows = [row for row in LocalEvidenceStore._read(path) if isinstance(row, dict)]
        kept = [row for row in rows if row.get(key) != payload.get(key)]
        kept.append(payload)
        kept.sort(key=lambda row: str(row.get(key)))
        body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in kept)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding=_ENCODING, newline="\n")
        except OSError as exc:
            raise EvidenceError(f"写入联系路线失败（{path}）：{exc}") from exc

    @staticmethod
    def _reject_rewritten_identity(path: Path, rejection: RouteRejection) -> None:
        """盘上已经有这张收据时，确认它的**身份**逐位相同 —— 改写账那几格不在此列。

        对比用的字段清单来自领域层（:func:`~marketops.domain.route_records.identity_mismatch`），
        不在这里挑：哪几格算身份是一个业务判定，而三个适配器各挑一遍就会挑出三个
        答案。这一层只负责把差异说成**这一侧**的那句话 —— 带上文件名与行号，因为
        ``route_rejection`` 的文件名是账号，一个账号文件里可能有上百行。
        """
        for lineno, row in _read_numbered(path):
            if row.get("rejection_id") != rejection.rejection_id:
                continue
            drift = identity_mismatch(RouteRejection.model_validate(row), rejection)
            if drift:
                raise EvidenceError(
                    f"{path} 第 {lineno} 行已有 rejection_id={rejection.rejection_id!r}，"
                    f"但身份与本次不同：{'、'.join(drift)}。\n"
                    "这个 id 是内容寻址的（工作区、账号、原因码、候选、来源页、时间），"
                    "而上面这几格都在原像里 —— 同一个 id 配不同的它们，只可能意味着 id "
                    "被手工指定过。\n"
                    "（恰相反，run_id / work_item_id / rule 属于改写账：它们不在原像里，"
                    "同一张收据被再写一次时会被换掉。）"
                )
            return

    def put_opportunity(self, opportunity: RouteOpportunity) -> None:
        self._require_own(opportunity.workspace, where="route_opportunity")
        path = self._path_for(self._settings.opportunities_dir, opportunity.account_id)
        self._append(path, opportunity.model_dump(mode="json"), key=None)

    def list_opportunities(self, account_id: str) -> list[RouteOpportunity]:
        path = self._path_for(self._settings.opportunities_dir, account_id)
        return [RouteOpportunity.model_validate(row) for row in self._read_owned(path)]

    # ------------------------------------------------------------------
    def _require_own(self, workspace: str, *, where: str) -> None:
        """这条要写进来的东西属不属于本部署。不属于 → :class:`EvidenceError`。

        ``where`` 是**写**这一侧的定位串：对象上大多没有行号可指（那一行还不存在），
        所以给的是对象的类型与 id。读那一侧的定位串带文件与行号（见 ``_read_owned``）。
        """
        if workspace == self._settings.profile:
            return
        raise EvidenceError(
            f"{where} 的 workspace={workspace!r}，而这个部署的工作区是 "
            f"{self._settings.profile!r}。\n"
            "这个目录（与它的文件）属于一个工作区：混装之后「按 account_id 取到的"
            "这一行是谁的」没有答案，而它的表现不是报错 —— 是两批行被静静地混在"
            "一起返回。\n"
            "要用另一个工作区，用另一个 data_root（或换一个部署配置）。"
        )

    def _read_owned(self, path: Path) -> list[dict]:
        """读一个文件，并**逐行**确认它属于本部署。**不属于就整份拒绝。**

        不是「跳过那一行、返回剩下的」：跳过之后，一个只有别人那些行的账号返回的是
        空列表，而它与「这个账号没有证据」在调用点长得一样 —— 越权读于是成了一次
        静默成功。所以这里报错，并且点名**文件与行号**（人可以直接去看那一行）。

        行号是承重的：``evidence`` 的文件名是账号，一个账号文件里可能有上百行，
        「第几行」是唯一能把话说清的位置。
        """
        rows: list[dict] = []
        for lineno, row in _read_numbered(path):
            workspace = row.get("workspace")
            if workspace != self._settings.profile:
                raise EvidenceError(self._foreign_line(path, lineno, workspace))
            rows.append(row)
        return rows

    def _foreign_line(self, path: Path, lineno: int, workspace: object) -> str:
        if workspace is None:
            return (
                f"{path} 第 {lineno} 行没有 workspace（血统一格）—— 它是本片之前"
                "写下的行，或者被手工删过那一格。\n"
                f"本部署的工作区是 {self._settings.profile!r}，而这一行属于谁没有答案，"
                "所以它**不被读出来**。\n"
                "修：先备份，再跑一次迁移（它给证得出来的历史行盖章、证不出来的点名"
                "拒绝）：\n"
                "    python -m marketops.adapters.sqlite.migrate\n"
                "迁移默认同时处理状态库与这些 JSONL 存档目录。"
            )
        return (
            f"{path} 第 {lineno} 行写着 workspace={workspace!r}，而本部署的工作区是 "
            f"{self._settings.profile!r}。\n"
            "这一行**不被返回**，也不被跳过 —— 跳过会让「这条不属于你」与「没有这一条」"
            "在调用点长成同一个空结果。"
        )

    @staticmethod
    def _path_for(directory: Path, account_id: str) -> Path:
        """账号 id → 文件名。**文件名就是 id 本身**，没有第二种写法。

        ``slugify`` 本身不是单射（``Foo`` 与 ``foo`` 得到同一个 ``foo.jsonl``），
        所以一一对应不是在这里保证的，而是在**模型层**：``check_canonical_id``
        让每一个带 ``account_id`` 的模型只接受「已经是文件名」的值，于是走到这里
        的 id 都是 ``slugify`` 的不动点。

        校验放在模型层而不是这里的理由，写在当时的 docstring 里，值得留着：
        只堵 ``_path_for`` 只保护这一个实现，而 ``adapters/sqlite`` 按 ``account_id``
        原样做键、不做任何折叠 —— 两个实现看到的 id 空间本来就不同，修在一处
        会有第三个实现重新打开它。

        ### 而这一层**仍然要拦一次**，理由和上面那条不冲突

        模型层守的是 **``put_*``**：那些入参是对象，``account_id`` 已经被校验过。
        但 **``list_*`` 的入参是一个裸字符串** —— 它不经过任何模型。少了这一道，
        ``list_evidence("Foo")`` 会去读 ``foo.jsonl``，读到的是一份**别人的**存档，
        而它看起来只是「证据更多了」。

        所以这里的拒绝不是重复防线，是**唯一**守读取边界的那一道。两者拦的是
        同一个字符串空间的不同入口，不是同一件事做两遍。

        界用 ``FILENAME_ID_MAX_LENGTH`` 而不是字面量：``max_length`` 与校验是
        同一个参数，落盘这一侧自己钉一个数就会出现「校验按 60、落盘按 40」。
        """
        try:
            canonical = check_canonical_id(account_id, "account_id")
        except ValueError as exc:
            # 存储层不抛裸 ``ValueError``：调用方按 ``EvidenceError`` 处理损坏与
            # 写入失败，一个半路冒出来的 ``ValueError`` 会绕过它们的处置。
            raise EvidenceError(str(exc)) from exc
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{canonical}.jsonl"

    @staticmethod
    def _append(path: Path, payload: dict, *, key: str | None) -> None:
        if key is not None:
            existing = {
                row.get(key)
                for row in LocalEvidenceStore._read(path)
                if isinstance(row, dict)
            }
            if payload.get(key) in existing:
                # id 是内容寻址的：同一 id 配不同内容只可能是 id 被手工指定过。
                # 静默保留先写的那条，会让「证据写进去了」与「我以为写进去的这条」
                # 变成两件事（三个实现都必须在这一点上一致）。
                _reject_conflicting_payload(path, payload, key)
                return
        try:
            with path.open("a", encoding=_ENCODING, newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as exc:
            raise EvidenceError(f"写入证据失败（{path}）：{exc}") from exc

    @staticmethod
    def _read(path: Path) -> list[dict]:
        return [row for _, row in _read_numbered(path)]


def _read_numbered(path: Path) -> list[tuple[int, dict]]:
    """一个文件 → ``(行号, 这一个对象)``。空行不算一行。

    行号是**给报错用的**：本实现的「一行」与「第几行」是人唯一能直接去核的位置
    （``grep -n`` 就有），所以它必须一路带到拒绝消息里 —— 一条只说文件名的拒绝
    在一个上百行的账号文件上等于什么都没说。

    损坏在这一层就报（与 ``_read`` 原来的行为一致）：静默跳过一行坏 JSON 会让
    「这一行坏了」与「这一行不存在」在统计上同形。
    """
    if not path.exists():
        return []
    rows: list[tuple[int, dict]] = []
    with path.open("r", encoding=_ENCODING) as handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append((lineno, json.loads(stripped)))
            except json.JSONDecodeError as exc:
                raise EvidenceError(
                    f"证据文件损坏：{path} 第 {lineno} 行不是合法 JSON。"
                    "人工修复后再继续 —— 静默跳过会污染后续所有统计。"
                ) from exc
    return rows


def _reject_conflicting_payload(path: Path, payload: dict, key: str) -> None:
    """同一 ``key`` 已经存在时，确认旧行内容与新的完全一致。"""
    for row in LocalEvidenceStore._read(path):
        if isinstance(row, dict) and row.get(key) == payload.get(key) and row != payload:
            raise EvidenceError(
                f"{path} 中已有 {key}={payload.get(key)!r} 的行，但内容与本次不同。\n"
                "这个 id 是内容寻址的，所以同一个 id 配不同内容只可能意味着 id 被手工指定过。"
            )


__all__ = ["LocalEvidenceStore"]

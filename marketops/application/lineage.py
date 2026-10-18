"""一行**历史**产出属于哪个工作区：**证明**，不是猜。

## 这一格此前不存在，而缺口不是一个字段

``evidence`` / ``contact_route`` / ``route_opportunity`` 三张表此前只有
``account_id``，而账号这一层没有 Scene 这一维 —— 于是「这一行属于哪个 Scene」在盘上
没有任何一格可以回答。一个部署只服务一个 Scene 曾经是唯一在挡这件事的东西：一句话，
不是一条判据。今天这三行事实都带 ``workspace``（见 ``domain/evidence.py`` 与
``domain/entities.py``），但**已经写在盘上的那些行**没有这一格，而它们不能靠改一行
DDL 补上 —— 补什么值是本文档要回答的问题。

## 为什么默认值在这里是**错的**

给历史行填「当前部署的 Scene」是一条看起来最省事的路，而它把「证明」换成了「假设」：
一个从别的部署复制过来的库、一个被两个 Scene 共用的库、一个拿错了
``MARKETOPS_DATA_ROOT`` 的进程，都会把自己的名字写到别人的事实上，**且不留痕迹**。
所以这里的接口不是 ``guess()``，是 :meth:`InputProof.source_for`：它要么返回**一个
说得出名字的来源**，要么返回 ``None``（= 证不出来）。调用方（迁移）对 ``None`` 的
处置是**拒绝并点名**，不是挑一个值（见 ``adapters/sqlite/migrate.py``）。

## 本部署自己的输入是**唯一**可用的证据源

「这一行是不是本部署写的」在离线情况下只有一个可查的方向：**它说的是不是本部署被
授权研究过的对象**。本部署被授权研究什么，写在它的输入目录里（``Settings.inputs_dir``）
—— 已知账号清单、来源回执、以及发现期写下的工件。三样都是本部署自己的产物，三样都在
同一次受理里被引用过（``application/inputs.py`` 的 ``subjects_for``）。

于是三档证明，**从强到弱**：

============================  ==================================================
``roster-subject``            这一行的 ``account_id`` 是某一份**清单/回执**点名的
                              主体 —— 本部署被要求研究过这个账号。
``artifact-evidence-id``      这一行的 ``evidence_id``（或它挂的 ``evidence_ids``
                              里的某一个）被本部署的**发现工件**逐字写下来过
                              （``discovery_run._artifact_row`` 的 ``evidence_ids``
                              / ``title_evidence_id``）。这一档最强：工件是**同一次
                              运行**写的，它点名的那一行证据就是它写下的那一行。
``artifact-subject``          这一行的 ``account_id`` 等于某一行工件里的
                              ``discovered_host``（旧工件写的是 ``official_domain``，
                              同一句话的两个年份）按 :func:`candidate_id_for` 派生
                              出来的候选 id。弱一档（它是**一步推导**，不是工件
                              逐字写下的），但它证明的是同一件事：这台主机进过本
                              部署的发现池。
============================  ==================================================

## 它证明什么、**不**证明什么（边界写在明处）

证明的是：**这一行的主语在本部署自己的输入里出现过**。因此「本部署写过这一行」是
一个说得出的结论，而「另一个部署写过它」不是 —— 除非那个部署与本部署共享同一批
输入**且**共享同一个状态库。后者是本条判据挡不住的：两个 Scene 共用一份清单、一个
库时，行与行在内容上无法区分（这正是「一个部署只服务一个 Scene」那句话在挡的东西，
而本仓今天没有执行器去禁掉共享）。本条判据能做的是**让这件事有一个说得出口的
答案**，而不是让它是空的。

另有一条**逐库**的旁证，由迁移去核：同一个库的 ``research_job.scene_id`` 记录着
这个库服务过的 Scene。它与请求的 profile 不一致时，迁移整份拒绝 —— 一个库的作业
记录说是别人的，那它的每一行都不该被本部署盖章。

## 读不出来的输入要**点名**，不是跳过

一份读不出来的清单、一份损坏的工件、一行不是 JSON 的行：它们是**本来可能证明某一
行**的东西。静默跳过它们，`None` 就会被读成「这份输入没有证明它」，而事实是「这份
输入没被读出来」—— 两种情形在迁移的拒绝消息里必须分得开，所以它们各自成一个元组
（:attr:`InputProof.unreadable`），并被写进拒绝消息。

## 为什么它读**文件**而不是读库

因为这些输入就是文件（``Settings.inputs_dir`` 下的产物）。从库里读「本部署被授权
研究过谁」会是一个循环论证：库正是要被打上血统的那一份东西。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.errors import ConfigError
from .cohorts import load_accounts
from .discovery_run import ARTIFACT_SUFFIX, candidate_id_for
from .motherpool import RECEIPT_SUFFIX, load_receipt

#: 证明来源的名字。**进报告**，所以它们是可读的句子片段而不是整数编号：运维看到的
#: 那行字要说得出「这 42 行是靠什么被证明的」。
PROOF_ROSTER_SUBJECT = "roster-subject"
PROOF_ARTIFACT_EVIDENCE_ID = "artifact-evidence-id"
PROOF_ARTIFACT_SUBJECT = "artifact-subject"

#: 清单的后缀。与 ``application/inputs.py`` 的 ``KNOWN_ACCOUNTS_SUFFIX`` 同源 ——
#: 那一份是「调用方给的名单名之后再拼上的后缀」，这里没有名字可收，只能扫目录。
_ACCOUNTS_SUFFIXES: tuple[str, ...] = (".yaml", ".yml")

#: 工件那一行里**逐字**写着证据 id 的两格（``discovery_run._artifact_row``）。
#: 只认这两格是有意的：扫描器**不是**一个「从任意 JSON 里抽 id」的启发式，它只认
#: 本仓自己的产物的形状。多认一格就等于多一条没人复查过的推断。
_EVIDENCE_ID_KEYS: tuple[str, ...] = ("evidence_ids", "title_evidence_id")


@dataclass(frozen=True)
class InputProof:
    """本部署自己的输入给出的一份**可查的**证明表。

    ``subjects`` / ``artifact_evidence_ids`` / ``artifact_subjects`` 三个集合都是
    「本部署自己的输入点名过的东西」，而 :meth:`source_for` 是它们上面唯一的判据。
    分开成三个集合而不是合成一个，是因为**证明的强弱不同**：合成之后，报告里那句
    「有 42 行是靠工件逐字写下的证据 id 证明的」就说不出来了。

    ``sources`` 与 ``unreadable`` 都是给报告用的（读了哪些文件、哪些读不出来），
    它们**不参与判定** —— 一份读不出来的输入不会让任何一行变得更可证或更不可证。
    """

    subjects: frozenset[str] = field(default_factory=frozenset)
    artifact_evidence_ids: frozenset[str] = field(default_factory=frozenset)
    artifact_subjects: frozenset[str] = field(default_factory=frozenset)
    sources: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()

    def source_for(self, *, account_id: str, evidence_ids: tuple[str, ...]) -> str | None:
        """这一行**能不能**被证明属于本部署，以及靠什么。

        Args:
            account_id: 这一行的账号键（三张表都有这一格）。
            evidence_ids: 这一行挂着的证据 id。证据行传它自己的 ``evidence_id``；
                路线与机会传 ``evidence_ids``（可以是空的 —— 空的时候只剩另外两档）。

        Returns:
            证明来源的名字（``roster-subject`` / ``artifact-evidence-id`` /
            ``artifact-subject``），或 ``None``（= **证不出来**）。
            ``None`` 不是「它属于别的部署」，是「按本部署自己的输入证不出来」。

        顺序即强弱：先问「这个账号是不是本部署被授权研究的」，再问「这一行证据是不是
        本部署自己的工件逐字写下的」，最后才落到「这个账号派生自本部署发现过的一台
        主机」。反过来排也能得到非 ``None`` 的同一批行，但报告里那个数字会变大 ——
        读的人看不出它有多少是靠最强的那一档来的。
        """
        if account_id and account_id in self.subjects:
            return PROOF_ROSTER_SUBJECT
        if any(ref and ref in self.artifact_evidence_ids for ref in evidence_ids):
            return PROOF_ARTIFACT_EVIDENCE_ID
        if account_id and account_id in self.artifact_subjects:
            return PROOF_ARTIFACT_SUBJECT
        return None

    def describe(self) -> str:
        """一句话说清这份证明表是从什么里来的。进拒绝消息与迁移报告。"""
        lines = [
            f"读了 {len(self.sources)} 份输入："
            f"{len(self.subjects)} 个主体、"
            f"{len(self.artifact_evidence_ids)} 个工件点名过的证据 id、"
            f"{len(self.artifact_subjects)} 个工件派生出的候选 id。",
        ]
        if self.sources:
            lines.append("来源：" + "、".join(self.sources))
        if self.unreadable:
            lines.append(
                f"⚠ 另有 {len(self.unreadable)} 份输入**读不出来**（它们不在上面那些数里，"
                "而其中的某一份本来可能证明某一行）："
            )
            lines.extend(f"  - {line}" for line in self.unreadable)
        return "\n".join(lines)


def load_input_proof(inputs_dir: Path) -> InputProof:
    """扫一遍本部署的输入目录，产出 :class:`InputProof`。

    **不抛异常**：一份读不出来的输入进 :attr:`InputProof.unreadable`。理由是这一层
    没有「哪一个输入是必须存在的」—— 输入目录可能只装了清单、也可能只装了工件，
    两种都正常。要抛的话，「这个部署的输入目录是空的」与「这个部署的输入全都读不出来」
    会在调用点同形，而它们要处置的是不同的事。

    目录本身不存在时返回一份**空**的证明表（并记一条 ``unreadable``）：空目录能让
    每一行都证不出来，而「证不出来」正是调用方要的那条答案 —— 它不需要一个异常。
    """
    subjects: set[str] = set()
    artifact_evidence_ids: set[str] = set()
    artifact_subjects: set[str] = set()
    sources: list[str] = []
    unreadable: list[str] = []

    if not inputs_dir.is_dir():
        return InputProof(unreadable=(f"{inputs_dir} 不是一个目录（不存在，或不是一个目录）",))

    for path in sorted(inputs_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(RECEIPT_SUFFIX):
            _read_receipt(inputs_dir, name, subjects=subjects, sources=sources, unreadable=unreadable)
        elif name.endswith(_ACCOUNTS_SUFFIXES):
            _read_accounts(path, subjects=subjects, sources=sources, unreadable=unreadable)
        elif name.endswith(ARTIFACT_SUFFIX):
            _read_artifact(
                path,
                evidence_ids=artifact_evidence_ids,
                subjects=artifact_subjects,
                sources=sources,
                unreadable=unreadable,
            )
    return InputProof(
        subjects=frozenset(subjects),
        artifact_evidence_ids=frozenset(artifact_evidence_ids),
        artifact_subjects=frozenset(artifact_subjects),
        sources=tuple(sources),
        unreadable=tuple(unreadable),
    )


def _read_accounts(
    path: Path,
    *,
    subjects: set[str],
    sources: list[str],
    unreadable: list[str],
) -> None:
    """一份已知账号清单 → 它点名的账号 id。

    走 :func:`~marketops.application.cohorts.load_accounts` 而不是自己 ``yaml.safe_load``：
    那份读入器**校验** ``account_id`` 的规范形式，而一个上游形状的文件（内部 slug、
    空 id、带大写）在这里**读不出来**正是我们要的答案 —— 它不能证明任何一行，而它
    进 ``unreadable`` 之后，「这份文件没被读出来」在拒绝消息里看得见。
    """
    try:
        accounts = load_accounts(path)
    except (ConfigError, ValueError) as exc:
        unreadable.append(f"{path.name}：{str(exc).splitlines()[0]}")
        return
    subjects.update(account.account_id for account in accounts)
    sources.append(path.name)


def _read_receipt(
    inputs_dir: Path,
    name: str,
    *,
    subjects: set[str],
    sources: list[str],
    unreadable: list[str],
) -> None:
    """一份来源回执 → 它 ``resolved`` 的那些行的 ``row_id``。

    那些 ``row_id`` 就是 ``application/inputs.py`` 认作主体的那个值（见
    ``_receipt_dossiers``），所以一份回执读进来的池子，它的每一行在库里都长这样。
    文件名去掉后缀就是回执名 —— 回执名由内容派生，扫目录的人手上只有文件名。
    """
    try:
        receipt = load_receipt(inputs_dir, name[: -len(RECEIPT_SUFFIX)])
    except (ConfigError, ValueError) as exc:
        unreadable.append(f"{name}：{str(exc).splitlines()[0]}")
        return
    subjects.update(row.row_id for row in receipt.resolved_rows)
    sources.append(name)


def _read_artifact(
    path: Path,
    *,
    evidence_ids: set[str],
    subjects: set[str],
    sources: list[str],
    unreadable: list[str],
) -> None:
    """一份发现工件（``.jsonl``）→ 它逐字写下的证据 id，与它覆盖过的候选 id。

    逐行读，一行坏掉只让**那一行**读不出来（它的行号进 ``unreadable``），不让整份
    工件作废：其余行的证据仍然成立，而整份作废会让「本来能证明的行」变多。
    """
    bad: list[str] = []
    read_any = False
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as exc:
        unreadable.append(f"{path.name}：{exc}")
        return
    with handle:
        for lineno, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except json.JSONDecodeError:
                bad.append(f"{path.name} 第 {lineno} 行不是合法 JSON")
                continue
            if not isinstance(row, dict):
                bad.append(f"{path.name} 第 {lineno} 行不是一个对象")
                continue
            read_any = True
            for key in _EVIDENCE_ID_KEYS:
                raw = row.get(key)
                if isinstance(raw, str):
                    # ``evidence_ids`` 是逗号连接的一串（见 ``_artifact_row``）。
                    evidence_ids.update(part for part in raw.split(",") if part)
            # 两台列名都读，因为**盘上有两个年份的工件**：`official_domain` 是旧代码
            # 写的，2026-09-26 那次审计之后写的是 `discovered_host`。两者在工件里
            # **说的是同一句话**（被取回来的那台主机），所以这里不是宽容读法，
            # 是承认两个年份的同一份观察。
            #
            # 只读新列名会让旧工件覆盖过的候选一个都算不出来 —— 于是
            # 「这一轮跑了多少主体」在报告上变成 0，而那一行本该非空。
            for key in ("discovered_host", "official_domain"):
                host = row.get(key)
                if isinstance(host, str) and host:
                    subjects.add(candidate_id_for(host))
                    break
    if read_any:
        sources.append(path.name)
    if bad:
        unreadable.extend(bad)


__all__ = [
    "PROOF_ARTIFACT_EVIDENCE_ID",
    "PROOF_ARTIFACT_SUBJECT",
    "PROOF_ROSTER_SUBJECT",
    "InputProof",
    "load_input_proof",
]

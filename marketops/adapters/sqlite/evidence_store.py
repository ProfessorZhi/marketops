"""SQLite 证据存储：**主存**。

## 它替代 JSONL 的那一件事

``adapters/jsonl/store.py`` 的写入是「全量读、判断、追加」—— 正确性依赖「只有一个
进程在写」。这条依赖从来没有被任何东西执行过，而在 §8 的部署形态里（多个 worker、
Quay 的任务与 CLI 并发）它必然被违反，违反的现象是两条写入都成功、其中一条被
后写的覆盖掉，且**不留任何痕迹**。

这里的正确性不依赖调用方。``evidence_id`` 是主键，所以「同一个 id 写两次」在
数据库层就不可能变成两行；而幂等与冲突的区别（内容相同 → 幂等；内容不同 → 报错）
由 ``ON CONFLICT DO NOTHING`` 之后的**一次读取**来判定：

    INSERT ... ON CONFLICT (evidence_id) DO NOTHING;
    SELECT payload WHERE evidence_id = ?;   -- 无论刚才是谁写的，现在这一行就是事实

用 ``DO NOTHING`` 而不是「先 SELECT 再决定 INSERT」是刻意的：后者在两个进程同时
到达时，两边都读到「不存在」，然后一边的 INSERT 撞主键抛 ``IntegrityError`` ——
而那是一个**别人抢先**的正常结局被报成异常。``DO NOTHING`` 让它不需要被判定。

两条语句包在同一个写事务里（``db.py`` 的 ``write_transaction``）。按上面的推理，
即使不包也是正确的，包起来是为了「读到的那一行」与「据此下的判断」属于同一个快照
—— 这条纪律在账本那边是承重的，在这里保持同一个形状，读的人不需要分别判断哪里
可以松。

## 为什么 payload 是一列 JSON 文本，不是摊平的列

把 ``Evidence`` 的字段摊平成列，等于把**契约**钉死在这个存储上：契约是 domain 的
事（它有独立的 JSON Schema，见 ``marketops/schema.py`` 与 ``contracts/``），加一个
字段就变成「改一次表 + 一次迁移」。留成文本，契约的演进与存储的演进解耦。

代价写在明处：**按 payload 里的字段查询需要 ``json_extract``，没有索引**。当前没有
这样的查询 —— 三个 ``list_*`` 都只按 ``account_id`` 取。等到出现第一个「按
literal_value 查」的需求时再回来看这一句，而不是现在先把列摊开。

## 路线与机会累积，不去重

与 ``ports/store.py`` 的幂等边界一致：重复的一条路线更可能意味着一次没被合并的
重复抓取，那件事应该被看见。所以它们的 ``seq`` 只保证插入顺序，不做唯一性判定。

## 读的另一半：**不属于这个工作区的行会被拒绝，而不是被跳过**

``evidence`` / ``contact_route`` / ``route_opportunity`` 的每一行现在都带 ``workspace``
（见 ``domain/evidence.py``），而库里另有一格**部署绑定**（``schema_meta`` 的
``workspace``，见 ``db.py::bind_workspace``）。两个值不一致时这一行不属于这个库。

处置是**报错**，不是「过滤掉它、返回剩下的」。差别不是风格：过滤之后
``list_evidence("kent-004")`` 在「这个账号不属于你」与「这个账号没有证据」两种情形
下返回的是**同一个空列表**，而报告那一层分不开它们 —— 越权读因此变成一次静默成功。
这一条判据只在库里出现「绑定与行不一致」时才可能触发（写路径构造不出来：第一行写入
就绑，且拒绝别的值），也就是说它挡的是一个从别处复制过来的库、一次手工改过的库。

``get_evidence`` 的 ``None`` 因此只有一个意思：**没有这个 id 的行**。它不再是
「有这一行但不给你看」——后者是 :class:`EvidenceError`。
"""

from __future__ import annotations

from pathlib import Path

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
from ...domain.util import utc_now
from ...ports.store import EvidenceStore
from .db import (
    Connection,
    bind_workspace,
    ensure_schema,
    owned,
    session,
    workspace_binding,
    write_transaction,
)
from .payload import dump_payload as _dump
from .payload import load_payload as _load


class SqliteEvidenceStore(EvidenceStore):
    """基于 SQLite 的证据、路线与路线机会存储。

    构造参数是 ``db_path`` 而不是 ``Settings``：与 ``SqliteBudgetLedger`` 同一个
    理由 —— 存储必须能在没有配置、没有 ``os.environ`` 的情况下对着一个临时文件
    被测出来。

    建表在 ``__init__`` 里做（``ensure_schema`` 幂等）。这是本模块与
    ``adapters/jsonl/store.py`` 的另一个差别：那边目录不存在就建目录、文件不存在
    就当作空，**没有任何时刻能回答「这个位置能不能写」**；这里构造即验证，一个
    不可写或版本不匹配的库在第一个调用点就报错，而不是在跑了半小时之后。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    def put_evidence(self, evidence: Evidence) -> None:
        payload = evidence.model_dump(mode="json")
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, evidence.workspace)
            conn.execute(
                "INSERT INTO evidence (evidence_id, account_id, payload, stored_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (evidence_id) DO NOTHING",
                (
                    evidence.evidence_id,
                    evidence.account_id,
                    _dump(payload),
                    utc_now().isoformat(),
                ),
            )
            row = conn.execute(
                "SELECT payload FROM evidence WHERE evidence_id = ?",
                (evidence.evidence_id,),
            ).fetchone()
            if _load(row["payload"], evidence.evidence_id) != payload:
                raise EvidenceError(
                    f"已有 evidence_id={evidence.evidence_id!r} 的证据，但内容与本次不同。\n"
                    "这个 id 是内容寻址的，所以同一个 id 配不同内容只可能意味着 id "
                    "被手工指定过。"
                )

    def list_evidence(self, account_id: str) -> list[Evidence]:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT evidence_id, payload FROM evidence WHERE account_id = ? ORDER BY rowid",
                (account_id,),
            ).fetchall()
        return [
            Evidence.model_validate(
                owned(
                    _load(row["payload"], row["evidence_id"]),
                    where=f"evidence[{row['evidence_id']}]",
                    bound=bound,
                    # 证据那一侧的契约按 ``EvidenceError`` 处理（ports/store.py）。
                    # 判词只有一份，异常类型是调用方的事 —— 见 db.owned 的 error 参数。
                    error=EvidenceError,
                )
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    def get_evidence(self, evidence_id: str) -> Evidence | None:
        """按 id 取。``evidence_id`` 是主键，所以这是 O(1) —— 见 ``ports/store.py``
        那条「三个实现的代价不一样」。

        ``None`` 只有一个意思：**没有这个 id 的行**。这一行存在但不属于本库时是
        :class:`EvidenceError`（见模块 docstring「读的另一半」）。
        """
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM evidence WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
        if row is None:
            return None
        return Evidence.model_validate(
            owned(
                _load(row["payload"], evidence_id),
                where=f"evidence[{evidence_id}]",
                bound=bound,
                error=EvidenceError,
            )
        )

    def record_route(self, observation: RouteObservation) -> CanonicalContactRoute:
        """追加观察 + 折叠 + upsert 投影，**同一个写事务**。

        ## 为什么这三步必须在一起，而这正是这张表能从 ``seq`` 换成主键的理由

        「历史里有一条、投影里没有」是一个能被构造出来的中间状态：进程在两次写之间
        死掉就留下了它，而这条路线从此在 ``list_routes`` 里不存在 ——
        它看起来像是那次抓取没发生过。一个事务把它关掉。

        ## 并发

        ``BEGIN IMMEDIATE`` 下两个 worker 交同一个观察是**串行**的：第一个写进去并
        折叠，第二个读到投影、算出同样的折叠结果（观察相同 → 折叠的结论相同），
        然后走 ``ON CONFLICT DO UPDATE`` 写回同一份内容。所以结果与只交一次逐位相同。
        ``observation`` 那一张表上按主键 ``DO NOTHING``，投影那一张上 ``DO UPDATE``
        —— 前者是「同一个观察不重复记」，后者是「同一个键只有一行」。两者的区别是
        结构和历史的分野，落成了两句不同的 SQL。

        ## 读到的那一行与据此下的判断属于同一个快照

        ``SELECT`` 与 ``INSERT`` 在同一个事务里，所以「当前投影是什么」这个问题在
        折叠的整个过程中只有一个答案。放在事务外面的话，两个 worker 都会读到同一个
        旧投影，各自算出 ``revision = N+1``，然后一个覆盖另一个 ——
        两条观察进去、一个版本号，而报告上一切正常。
        """
        payload = observation.model_dump(mode="json")
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, observation.workspace)
            conn.execute(
                "INSERT INTO route_observation "
                "(observation_id, canonical_key, account_id, workspace, observed_at, "
                " payload, stored_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (observation_id) DO NOTHING",
                (
                    observation.observation_id,
                    observation.canonical_key,
                    observation.account_id,
                    observation.workspace,
                    observation.observed_at.isoformat(),
                    _dump(payload),
                    utc_now().isoformat(),
                ),
            )
            row = conn.execute(
                "SELECT payload FROM route_observation WHERE observation_id = ?",
                (observation.observation_id,),
            ).fetchone()
            if _load(row["payload"], observation.observation_id) != payload:
                raise EvidenceError(
                    f"已有 observation_id={observation.observation_id!r} 的路线观察，"
                    "但内容与本次不同。\n"
                    "这个 id 是内容寻址的，所以同一个 id 配不同内容只可能意味着 id "
                    "被手工指定过。"
                )

            current = self._projection_row(conn, observation.canonical_key)
            folded = fold_observation(current, observation, at=observation.observed_at)
            projection = folded.projection
            conn.execute(
                "INSERT INTO contact_route "
                "(canonical_key, account_id, workspace, payload, stored_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (canonical_key) DO UPDATE SET "
                "payload = excluded.payload, stored_at = excluded.stored_at",
                (
                    projection.canonical_key,
                    projection.account_id,
                    projection.workspace,
                    _dump(projection.model_dump(mode="json")),
                    utc_now().isoformat(),
                ),
            )
        return projection

    @staticmethod
    def _projection_row(conn: Connection, canonical_key: str) -> CanonicalContactRoute | None:
        """按规范键取当前投影。**主键**，所以是 O(1) —— 与 JSONL 那份的扫目录相对。"""
        row = conn.execute(
            "SELECT payload FROM contact_route WHERE canonical_key = ?", (canonical_key,)
        ).fetchone()
        if row is None:
            return None
        return CanonicalContactRoute.model_validate(_load(row["payload"], canonical_key))

    def list_routes(self, account_id: str) -> list[CanonicalContactRoute]:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT canonical_key, payload FROM contact_route "
                "WHERE account_id = ? ORDER BY canonical_key",
                (account_id,),
            ).fetchall()
        return [
            CanonicalContactRoute.model_validate(
                owned(
                    _load(row["payload"], f"contact_route[{row['canonical_key']}]"),
                    where=f"contact_route[{row['canonical_key']}]",
                    bound=bound,
                    error=EvidenceError,
                )
            )
            for row in rows
        ]

    def list_route_observations(self, account_id: str) -> list[RouteObservation]:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT observation_id, payload FROM route_observation "
                "WHERE account_id = ? ORDER BY observed_at, observation_id",
                (account_id,),
            ).fetchall()
        return [
            RouteObservation.model_validate(
                owned(
                    _load(row["payload"], f"route_observation[{row['observation_id']}]"),
                    where=f"route_observation[{row['observation_id']}]",
                    bound=bound,
                    error=EvidenceError,
                )
            )
            for row in rows
        ]

    def put_route_rejection(self, rejection: RouteRejection) -> None:
        """一张收据一行。**身份必须一致，改写账换新的**（见 :meth:`RouteRejection.identity`）。

        ## 这里此前是「插入或忽略，然后要求逐字节相同」

        那一版在**第二次**写入时就炸：同一个 ``rejection_id``（原像里没有 run_id）
        配上另一个 ``run_id``，于是重试一次、两个 worker 同时跑同一批、维护轮次重看
        同一页，都会撞上 ``EvidenceError``。而 ``rejection_id`` 里不放 ``run_id``
        正是为了这几件事不各写一行 —— 幂等的承诺是「再算一遍不会多出一行」，
        不是「再算一遍会抛异常」。

        所以：身份那几格对不上才拒（那时 ``rejection_id`` 是被手工指定过的），
        记账那几格直接在原行上改写。表里那四个派生列（``account_id`` /
        ``workspace`` / ``code`` / ``occurred_at``）全在身份那一侧，因此身份通过时
        它们必然与盘上一致，``DO UPDATE`` 只换 ``payload`` 与 ``stored_at``。
        """
        payload = rejection.model_dump(mode="json")
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, rejection.workspace)
            row = conn.execute(
                "SELECT payload FROM route_rejection WHERE rejection_id = ?",
                (rejection.rejection_id,),
            ).fetchone()
            if row is not None:
                stored = RouteRejection.model_validate(
                    _load(row["payload"], rejection.rejection_id)
                )
                drift = identity_mismatch(stored, rejection)
                if drift:
                    raise EvidenceError(
                        f"已有 rejection_id={rejection.rejection_id!r} 的拒绝记录，"
                        f"但身份与本次不同：{'、'.join(drift)}。\n"
                        "这个 id 是内容寻址的（工作区、账号、原因码、候选、来源页、时间），"
                        "而上面这几格都在原像里 —— 同一个 id 配不同的它们，只可能意味着 "
                        "id 被手工指定过。\n"
                        "（恰相反，run_id / work_item_id / rule 属于改写账：它们不在原像里，"
                        "同一张收据被再写一次时会被换掉。）"
                    )
            conn.execute(
                "INSERT INTO route_rejection "
                "(rejection_id, account_id, workspace, code, occurred_at, payload, stored_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (rejection_id) DO UPDATE SET "
                "payload = excluded.payload, stored_at = excluded.stored_at",
                (
                    rejection.rejection_id,
                    rejection.account_id,
                    rejection.workspace,
                    rejection.code.value,
                    rejection.occurred_at.isoformat(),
                    _dump(payload),
                    utc_now().isoformat(),
                ),
            )

    def list_route_rejections(self, account_id: str) -> list[RouteRejection]:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT rejection_id, payload FROM route_rejection "
                "WHERE account_id = ? ORDER BY occurred_at, rejection_id",
                (account_id,),
            ).fetchall()
        return [
            RouteRejection.model_validate(
                owned(
                    _load(row["payload"], f"route_rejection[{row['rejection_id']}]"),
                    where=f"route_rejection[{row['rejection_id']}]",
                    bound=bound,
                    error=EvidenceError,
                )
            )
            for row in rows
        ]

    def put_opportunity(self, opportunity: RouteOpportunity) -> None:
        self._append(
            "route_opportunity",
            opportunity.account_id,
            opportunity.workspace,
            opportunity.model_dump(mode="json"),
        )

    def list_opportunities(self, account_id: str) -> list[RouteOpportunity]:
        return [
            RouteOpportunity.model_validate(row)
            for row in self._read("route_opportunity", account_id)
        ]

    # ------------------------------------------------------------------
    # 表名只在这两个私有方法里被拼接，且两张表在 DDL 里写死。
    # 不把它做成参数是为了拒绝「调用方传表名」这种形状 —— SQL 表名不能参数化，
    # 一个能传表名的接口迟早会拿到一个来自配置的字符串。
    def _append(self, table: str, account_id: str, workspace: str, payload: dict) -> None:
        """绑 + 插在**同一个写事务**里。

        之前这里是裸 ``INSERT``（``isolation_level=None`` 下即自动提交）：一行写一半
        才发现绑不上是构造不出来的状态。加事务是为了绑定与那一行**同时**生效 ——
        库在第一个写入之后就绑好了，而不是「写进去了，但绑还没写上」。
        """
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, workspace)
            conn.execute(
                f"INSERT INTO {table} (account_id, payload, stored_at) VALUES (?, ?, ?)",
                (account_id, _dump(payload), utc_now().isoformat()),
            )

    def _read(self, table: str, account_id: str) -> list[dict]:
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                f"SELECT seq, payload FROM {table} WHERE account_id = ? ORDER BY seq",
                (account_id,),
            ).fetchall()
        return [
            owned(
                _load(row["payload"], f"{table}[{row['seq']}]"),
                where=f"{table}[seq={row['seq']}]",
                bound=bound,
                error=EvidenceError,
            )
            for row in rows
        ]


__all__ = ["SqliteEvidenceStore"]

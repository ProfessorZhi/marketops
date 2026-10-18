"""SQLite 的 ``SceneStore`` / ``MaintenanceStore``。

## 这个文件里唯一真正难的东西是 ``put_record``

其余每一个方法都是「一行 INSERT / 一行 SELECT 再 ``model_validate``」。``put_record``
不是，因为它要在一个读写之间做判断，而那个判断决定了**两个控制面会不会同时改同一个
场景的规则**。

它的形状是 ``UPDATE ... WHERE scene_id = ? AND revision = ?`` 加 ``rowcount``：

    rows = conn.execute("UPDATE scene SET ... WHERE scene_id = ? AND revision = ?",
                        (..., expected)).rowcount
    if rows == 0:  # 只有这里能区分三种原因，所以要再读一次
        ...

``rowcount`` 为 0 有**三种**原因，而它们对调用方的处置完全不同：这个场景不存在（去
创建）、存在但 revision 不是我以为的那个（有人先写了，重读再来）、以及**存在但不是
我以为的工作区**（一次越权写入或一次配错根的部署）。合成一句「冲突了」，读的人无法
判断该去创建、该重读、还是该停下来查根目录 —— 所以这里分开报。

## 一条只有本表需要的交叉校验

``scene`` 表把 ``revision`` / ``state`` / ``latest_version`` 摊成了列（CAS 的 ``WHERE``
子句要用它们），而**领域对象存在 ``payload`` 里**。这与 ``evidence`` 表的取舍相同，
但这里多出一个 ``evidence`` 没有的风险：那三列不只是索引，它们是**判定依据**。如果
有人手工改了库、让列与 payload 不一致，CAS 就会拿一个错的 revision 去比 —— 而它会
**成功**。所以读回来时逐字段比一次：不一致就报 ``StoreError``（停机对账），而不是
拿一个自己都不自洽的记录继续跑。

## 为什么版本表的幂等判定用 ``frozen_sha256`` 而不是整块 payload

与 ``evidence_store`` 同一条纪律（内容相同 → 幂等，不同 → 报错），但比较的**量具**
是版本自己的摘要：``SceneVersion.frozen_sha256`` 涵盖了这一版绑定的每一个字段。用整块
payload 比也可以，但那样报错时说不清「哪里变了」；用摘要比则能对着库直接看两行。

## 回执：认领是「写不进就是没拿到」，而**这里**知道写进没写进

``claim`` 的并发正确性不靠调用方：``(dedupe_key, attempt)`` 是主键，所以两个并发驱动
不可能都写进同一条；写入落在 ``BEGIN IMMEDIATE`` 里，所以「读到最新一行」与「写下一行」
之间没有别人能插进来。

因此「我拿到了吗」的答案由**本方法**给出（``ClaimOutcome.won``），而不是让调用方拿
``claimed_at`` 去比。曾经那样写过一版，理由是「两个驱动不可能请求同一个时刻」——
那个理由是错的：``now`` 在这一层是**参数**而不是时钟，一个可控时钟的测试、一个秒级
粒度的调度器、或一次重放都会让两个驱动带着同一个时刻来抢同一格，于是两个都认为自己
拿到了。真正知道答案的只有执行那次插入的人。

``rowcount != 1`` 于是是一次**必须失败关闭**的意外：同一事务里刚读到「不存在或 FAILED」，
插入却说这一行已存在 —— 那意味着有写者绕过了本库的写事务序。

## 为什么这里可以把时刻直接格式化进 ORDER BY

因为领域层的校验器已经把它们规范到 UTC 了（``domain/util.py::to_utc``）。这不是一条
可以省略的前提：ISO-8601 字符串的大小顺序只在偏移量相同时才等于瞬间的先后，而
``2026-09-23T09:00:00+08:00`` 排在 ``2026-09-23T02:00:00+00:00`` **之后**却是同一个瞬间
—— 于是「取最近一次观测」会取到一个更早的，而没有任何一处会红。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ...domain.errors import SceneConflictError, StoreError
from ...domain.maintenance import (
    Aspect,
    ChangeSignal,
    ClaimOutcome,
    DueReason,
    MaintenanceClaim,
    MaintenanceState,
    Observation,
)
from ...domain.scene import SceneRecord, SceneVersion
from ...ports.scenes import MaintenanceStore, SceneStore
from .db import ensure_schema, session, write_transaction
from .payload import dump_payload as _dump
from .payload import load_payload as _load

#: ``scene`` 表里那几个「不只是索引」的列。读回来时逐个与 payload 比。
_RECORD_COLUMNS = ("workspace", "state", "latest_version", "revision")

#: 回执表的**全部**字段都是列（CAS、去重与「失败过几次」都要按它们查），所以读回来
#: 是从列重建，而不是解一块 payload —— 那张表没有 payload 列，也不该有：一份内容存
#: 两处，两处不一致时没有一处会红。
_CLAIM_COLUMNS = (
    "dedupe_key, attempt, workspace, scene_id, scene_version, subject_kind, subject_id, "
    "aspect, reason, period, state, claimed_at, finished_at, result_ref, failure"
)


def _claim_from_row(row) -> MaintenanceClaim:  # noqa: ANN001 - sqlite3.Row
    return MaintenanceClaim(
        dedupe_key=row["dedupe_key"],
        attempt=row["attempt"],
        workspace=row["workspace"],
        scene_id=row["scene_id"],
        scene_version=row["scene_version"],
        subject_kind=row["subject_kind"],
        subject_id=row["subject_id"],
        aspect=Aspect(row["aspect"]),
        reason=DueReason(row["reason"]),
        period=row["period"],
        state=MaintenanceState(row["state"]),
        claimed_at=datetime.fromisoformat(row["claimed_at"]),
        finished_at=(
            None if row["finished_at"] is None else datetime.fromisoformat(row["finished_at"])
        ),
        result_ref=row["result_ref"],
        failure=row["failure"],
    )


def _record_row_to_payload(row) -> dict:  # noqa: ANN001 - sqlite3.Row
    return _load(row["payload"], row["scene_id"])


def _check_record_columns(row, record: SceneRecord) -> None:  # noqa: ANN001
    """摊平出来的列必须与 payload 逐字段一致。见模块 docstring 的交叉校验一节。"""
    expected = {
        "workspace": record.workspace,
        "state": record.state.value,
        "latest_version": record.latest_version,
        "revision": record.revision,
    }
    for column in _RECORD_COLUMNS:
        if row[column] != expected[column]:
            raise StoreError(
                f"scene={row['scene_id']!r} 的列 {column!r} 是 {row[column]!r}，"
                f"而 payload 里是 {expected[column]!r}。\n"
                "这两处由同一个写事务写入，不一致只可能意味着库被外部改过。"
                "CAS 用的正是这些列 —— 继续跑会让一次乐观锁比较拿错依据。"
            )


class SqliteSceneStore(SceneStore):
    """Scene 的版本与当前状态。构造参数是 ``db_path``，不是 ``Settings``。

    与其余 SQLite 适配器同一个理由：存储必须能在没有配置、没有 ``os.environ`` 的
    情况下对着一个临时文件被测出来。建表在 ``__init__`` 里做（``ensure_schema`` 幂等）
    —— 构造即验证，一个不可写或版本不匹配的库在第一个调用点就报错。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------ 版本
    def put_version(self, version: SceneVersion) -> None:
        payload = version.model_dump(mode="json")
        with session(self._path) as conn, write_transaction(conn):
            conn.execute(
                "INSERT INTO scene_version "
                "(scene_id, version, workspace, frozen_sha256, created_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (scene_id, version) DO NOTHING",
                (
                    version.scene_id,
                    version.version,
                    version.workspace,
                    version.frozen_sha256,
                    version.created_at.isoformat(),
                    _dump(payload),
                ),
            )
            row = conn.execute(
                "SELECT workspace, frozen_sha256, payload FROM scene_version "
                "WHERE scene_id = ? AND version = ?",
                (version.scene_id, version.version),
            ).fetchone()
            if row["frozen_sha256"] != version.frozen_sha256:
                raise StoreError(
                    f"已有 scene_id={version.scene_id!r} version={version.version} 的版本，"
                    f"但摘要与本次不同（库里 {row['frozen_sha256'][:16]}…，"
                    f"本次 {version.frozen_sha256[:16]}…）。\n"
                    "版本号是这一版规则的摘要，所以同号不同内容只可能意味着版本号被手工"
                    "指定过。**不覆盖** —— 覆盖会让按版本号引用它的工作单元静默换了规则。"
                )
            if row["workspace"] != version.workspace:
                raise StoreError(
                    f"scene_id={version.scene_id!r} version={version.version} 已属于工作区 "
                    f"{row['workspace']!r}，本次声称的是 {version.workspace!r}。"
                )

    def get_version(self, scene_id: str, version: int) -> SceneVersion | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT payload FROM scene_version WHERE scene_id = ? AND version = ?",
                (scene_id, version),
            ).fetchone()
        if row is None:
            return None
        return SceneVersion.model_validate(_load(row["payload"], f"{scene_id}@{version}"))

    def versions(self, scene_id: str) -> tuple[SceneVersion, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT version, payload FROM scene_version WHERE scene_id = ? "
                "ORDER BY version",
                (scene_id,),
            ).fetchall()
        return tuple(
            SceneVersion.model_validate(_load(row["payload"], f"{scene_id}@{row['version']}"))
            for row in rows
        )

    # ------------------------------------------------------------ 状态
    def put_record(self, record: SceneRecord, *, expected_revision: int | None) -> None:
        if expected_revision is None:
            if record.revision != 0:
                raise StoreError(
                    f"首次创建 scene={record.scene_id!r} 时 revision 必须是 0，"
                    f"本次是 {record.revision}。\n"
                    "带着非零 revision 创建，会让一次「这条记录已经存在」变成"
                    "「我写的是第 n 版」—— 而库里那一条的 revision 与它毫无关系。"
                )
            self._insert_record(record)
            return

        if record.revision != expected_revision + 1:
            raise StoreError(
                f"scene={record.scene_id!r} 的写入 revision 必须是 "
                f"{expected_revision + 1}（读到的 {expected_revision} 加一），"
                f"本次是 {record.revision}。\n"
                "这一条挡的是「改完对象直接写回」：那跳过了一次 revision 自增，"
                "于是并发的另一个写者会**成功** —— 而它以为自己在比的那个版本"
                "从来没存在过。状态变更必须走 SceneRecord 上的那几个方法。"
            )
        self._update_record(record, expected_revision=expected_revision)

    def _insert_record(self, record: SceneRecord) -> None:
        with session(self._path) as conn, write_transaction(conn):
            inserted = conn.execute(
                "INSERT INTO scene (scene_id, workspace, state, latest_version, "
                "effective_version, created_at, updated_at, pause_reason, revision, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (scene_id) DO NOTHING",
                (
                    record.scene_id,
                    record.workspace,
                    record.state.value,
                    record.latest_version,
                    record.effective_version,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    None if record.pause_reason is None else record.pause_reason.value,
                    record.revision,
                    _dump(record.model_dump(mode="json")),
                ),
            ).rowcount
            if inserted:
                return
            # **任何**已存在的行都是冲突，包括内容完全一样的那一种。
            # 用 payload 比对来判定「是不是同一次创建重试」是错的：那条判据会把
            # 「我以为这是第一次创建」与「这确实是第一次创建」变成同一件事，
            # 而前者正是一次并发重复立项。
            row = conn.execute(
                "SELECT workspace, revision FROM scene WHERE scene_id = ?",
                (record.scene_id,),
            ).fetchone()
            assert row is not None  # ON CONFLICT DO NOTHING 只在已有行时为 0
            raise SceneConflictError(
                f"scene={record.scene_id!r} 已经存在（工作区 {row['workspace']!r}，"
                f"revision={row['revision']}），本次调用声称它是首次创建。"
            )

    def _update_record(self, record: SceneRecord, *, expected_revision: int) -> None:
        with session(self._path) as conn, write_transaction(conn):
            changed = conn.execute(
                "UPDATE scene SET workspace = ?, state = ?, latest_version = ?, "
                "effective_version = ?, created_at = ?, updated_at = ?, pause_reason = ?, "
                "revision = ?, payload = ? WHERE scene_id = ? AND revision = ?",
                (
                    record.workspace,
                    record.state.value,
                    record.latest_version,
                    record.effective_version,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    None if record.pause_reason is None else record.pause_reason.value,
                    record.revision,
                    _dump(record.model_dump(mode="json")),
                    record.scene_id,
                    expected_revision,
                ),
            ).rowcount
            if changed:
                return
            # 三种原因分别报 —— 见模块 docstring。
            current = conn.execute(
                "SELECT workspace, revision FROM scene WHERE scene_id = ?",
                (record.scene_id,),
            ).fetchone()
            if current is None:
                raise SceneConflictError(
                    f"scene={record.scene_id!r} 不存在，本次调用以为它在第 "
                    f"{expected_revision} 版。一个字都没写。"
                )
            raise SceneConflictError(
                f"scene={record.scene_id!r} 现在是第 {current['revision']} 版，"
                f"本次调用读到的是第 {expected_revision} 版。一个字都没写。\n"
                f"库里那条属于工作区 {current['workspace']!r}，本次写的是 "
                f"{record.workspace!r} —— 若两者不同，这更可能是一次配错根目录的写入。"
            )

    def get_record(self, scene_id: str) -> SceneRecord | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT scene_id, workspace, state, latest_version, revision, payload "
                "FROM scene WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()
        if row is None:
            return None
        record = SceneRecord.model_validate(_record_row_to_payload(row))
        _check_record_columns(row, record)
        return record

    def records(self, *, workspace: str | None = None) -> tuple[SceneRecord, ...]:
        sql = (
            "SELECT scene_id, workspace, state, latest_version, revision, payload "
            "FROM scene"
        )
        params: tuple[str, ...] = ()
        if workspace is not None:
            sql += " WHERE workspace = ?"
            params = (workspace,)
        sql += " ORDER BY scene_id"
        with session(self._path) as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[SceneRecord] = []
        for row in rows:
            record = SceneRecord.model_validate(_record_row_to_payload(row))
            _check_record_columns(row, record)
            out.append(record)
        return tuple(out)


class SqliteMaintenanceStore(MaintenanceStore):
    """观测、变更信号与复核回执。"""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------ 观测
    def put_observation(self, observation: Observation) -> None:
        """同一 ``(工作区, 主体, aspect, 时刻, 来源)`` 幂等。

        用 ``INSERT OR IGNORE`` 而不是「先查再插」：重复写入在这里是**正常**的
        （同一格被两个来源在同一次复核里各自记了一遍），把它报成异常会让正常的
        重复变成一次停机。而它**不会**掩盖内容冲突 —— 主键相同的两行内容必然相同，
        因为 payload 里除了主键就只有从主键推不出来的字段，而那些字段
        （``source_ref`` 已在主键里）不参与内容差异。
        """
        payload = observation.model_dump(mode="json")
        with session(self._path) as conn, write_transaction(conn):
            conn.execute(
                "INSERT OR IGNORE INTO observation "
                "(workspace, subject_kind, subject_id, aspect, observed_at, source_ref, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    observation.workspace,
                    observation.subject_kind,
                    observation.subject_id,
                    observation.aspect.value,
                    observation.observed_at.isoformat(),
                    observation.source_ref,
                    _dump(payload),
                ),
            )

    def observations(
        self, *, workspace: str, subject_kind: str | None = None
    ) -> tuple[Observation, ...]:
        sql = (
            "SELECT workspace, subject_kind, subject_id, aspect, observed_at, payload "
            "FROM observation WHERE workspace = ?"
        )
        params: tuple[str, ...] = (workspace,)
        if subject_kind is not None:
            sql += " AND subject_kind = ?"
            params = (workspace, subject_kind)
        sql += " ORDER BY subject_kind, subject_id, aspect, observed_at, source_ref"
        with session(self._path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(
            Observation.model_validate(
                _load(row["payload"], f"observation[{row['subject_id']}/{row['aspect']}]")
            )
            for row in rows
        )

    # ------------------------------------------------------------ 信号
    def put_signal(self, signal: ChangeSignal) -> None:
        payload = signal.model_dump(mode="json")
        with session(self._path) as conn, write_transaction(conn):
            conn.execute(
                "INSERT OR IGNORE INTO change_signal "
                "(workspace, subject_kind, subject_id, aspect, signalled_at, reason, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    signal.workspace,
                    signal.subject_kind,
                    signal.subject_id,
                    signal.aspect.value,
                    signal.signalled_at.isoformat(),
                    signal.reason,
                    _dump(payload),
                ),
            )

    def signals(self, *, workspace: str) -> tuple[ChangeSignal, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT payload FROM change_signal WHERE workspace = ? "
                "ORDER BY signalled_at, subject_kind, subject_id, aspect",
                (workspace,),
            ).fetchall()
        return tuple(
            ChangeSignal.model_validate(_load(row["payload"], f"change_signal[{workspace}]"))
            for row in rows
        )

    # ------------------------------------------------------------ 回执
    def claim(self, claim: MaintenanceClaim) -> ClaimOutcome:
        if claim.state is not MaintenanceState.CLAIMED:
            raise StoreError(
                f"claim 只接受 CLAIMED 的回执，本次是 {claim.state.value}"
                f"（{claim.dedupe_key!r}）。"
                "带一条终态回执来认领，等于要求存储替调用方把一次结束写进一次开始。"
            )
        with session(self._path) as conn, write_transaction(conn):
            existing = self._latest_claim(conn, claim.dedupe_key)
            if existing is not None:
                self._check_same_cell(existing, claim)
            # **只有 FAILED 才产生下一条**。``CLAIMED`` 是「有人在跑」——
            # 第二次认领是本次调用没拿到它，不是它又跑了一遍（那会变成两件昂贵的
            # 事同时在做、账上只显示两次）。``DONE`` 是「已经做完了」。
            if existing is None:
                attempt = 1
            elif existing.state is MaintenanceState.FAILED:
                attempt = existing.attempt + 1
            else:
                return ClaimOutcome(claim=existing, won=False)
            cursor = conn.execute(
                "INSERT INTO maintenance_claim "
                "(dedupe_key, attempt, workspace, scene_id, scene_version, subject_kind, "
                "subject_id, aspect, reason, period, state, claimed_at, finished_at, "
                "result_ref, failure) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '', '') "
                "ON CONFLICT (dedupe_key, attempt) DO NOTHING",
                (
                    claim.dedupe_key,
                    attempt,
                    claim.workspace,
                    claim.scene_id,
                    claim.scene_version,
                    claim.subject_kind,
                    claim.subject_id,
                    claim.aspect.value,
                    claim.reason.value,
                    claim.period,
                    MaintenanceState.CLAIMED.value,
                    claim.claimed_at.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                # 走到这里说明 ``(dedupe_key, attempt)`` 这一行**已经存在**，而本次
                # 事务刚刚读到的最新一行是「不存在或 FAILED」—— 两句话不可能同时为真，
                # 除非有另一个写者绕过了 ``BEGIN IMMEDIATE``。
                # 这里必须失败关闭：放它过去就是「两个执行者都认为自己在跑同一次尝试」，
                # 而账上是两行 attempt 相同、内容不同的回执。
                raise StoreError(
                    f"回执 {claim.dedupe_key!r} attempt={attempt} 插入时已存在，"
                    "而同一事务里刚读到的最新一行不是它。\n"
                    "这意味着有一个写者绕过了本库的写事务序（BEGIN IMMEDIATE）。"
                    "认领是「写不进就是没拿到」，所以在这一点上不能继续。"
                )
            stored = self._latest_claim(conn, claim.dedupe_key)
            if stored is None:  # pragma: no cover - 上面刚写过
                raise StoreError(f"回执写入后读不回来：{claim.dedupe_key!r}")
            return ClaimOutcome(claim=stored, won=True)

    def finish(self, claim: MaintenanceClaim) -> None:
        if claim.state is MaintenanceState.CLAIMED:
            raise StoreError(
                f"finish 只接受终态回执，本次是 CLAIMED（{claim.dedupe_key!r}）。"
                "把一条回执原地改回 CLAIMED 会让「它被认领过」这件事从记录里消失。"
            )
        with session(self._path) as conn, write_transaction(conn):
            changed = conn.execute(
                "UPDATE maintenance_claim SET state = ?, finished_at = ?, result_ref = ?, "
                "failure = ? WHERE dedupe_key = ? AND attempt = ? AND state = ?",
                (
                    claim.state.value,
                    None if claim.finished_at is None else claim.finished_at.isoformat(),
                    claim.result_ref,
                    claim.failure,
                    claim.dedupe_key,
                    claim.attempt,
                    MaintenanceState.CLAIMED.value,
                ),
            ).rowcount
            if changed:
                return
            row = conn.execute(
                f"SELECT {_CLAIM_COLUMNS} FROM maintenance_claim "
                "WHERE dedupe_key = ? AND attempt = ?",
                (claim.dedupe_key, claim.attempt),
            ).fetchone()
            if row is None:
                raise StoreError(
                    f"没有 dedupe_key={claim.dedupe_key!r} attempt={claim.attempt} 这条回执，"
                    "本次却要结掉它。\n"
                    "回执只能由认领产生 —— 一条『凭空结掉』的记录会让一件没做过的工作"
                    "在报告里显示为做完。"
                )
            stored = _claim_from_row(row)
            if stored.model_dump(mode="json") == claim.model_dump(mode="json"):
                return  # 同一个终态写第二次：幂等
            raise StoreError(
                f"dedupe_key={claim.dedupe_key!r} attempt={claim.attempt} 已经是 "
                f"{stored.state.value}（finished_at={stored.finished_at}），"
                f"本次要写成 {claim.state.value}。\n"
                "两次不同的结束意味着有两个执行者都以为自己在跑同一次尝试 —— "
                "静默取一个会让另一次的结果连同它花掉的钱一起消失。"
            )

    def claims(self, scene_id: str) -> tuple[MaintenanceClaim, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                f"SELECT {_CLAIM_COLUMNS} FROM maintenance_claim WHERE scene_id = ? "
                "ORDER BY claimed_at, subject_kind, aspect, attempt",
                (scene_id,),
            ).fetchall()
        return tuple(_claim_from_row(row) for row in rows)

    # ------------------------------------------------------------ 内部
    def _latest_claim(self, conn, dedupe_key: str) -> MaintenanceClaim | None:  # noqa: ANN001
        row = conn.execute(
            f"SELECT {_CLAIM_COLUMNS} FROM maintenance_claim WHERE dedupe_key = ? "
            "ORDER BY attempt DESC LIMIT 1",
            (dedupe_key,),
        ).fetchone()
        if row is None:
            return None
        return _claim_from_row(row)

    @staticmethod
    def _check_same_cell(existing: MaintenanceClaim, claim: MaintenanceClaim) -> None:
        """``dedupe_key`` 必须真的指同一格。

        键是 ``sha256`` 摘要，所以「同一个键指了不同的格」不可能是算出来的 ——
        只可能是有人手工构造了一条回执。它挡的失败很具体：那一次认领会安静地
        落在**别人的**格子上，而报告里那一格显示为「刚核过」。
        """
        fields = (
            "workspace",
            "scene_id",
            "scene_version",
            "subject_kind",
            "subject_id",
            "period",
        )
        for name in fields:
            if getattr(existing, name) != getattr(claim, name):
                raise StoreError(
                    f"dedupe_key={claim.dedupe_key!r} 已经属于 "
                    f"{getattr(existing, name)!r}（{name}），本次要认领的是 "
                    f"{getattr(claim, name)!r}。\n"
                    "键是内容寻址的，同一个键指了两格意味着记录是手工构造的。"
                )
        if existing.aspect != claim.aspect:
            raise StoreError(
                f"dedupe_key={claim.dedupe_key!r} 已经属于 aspect="
                f"{existing.aspect.value!r}，本次是 {claim.aspect.value!r}。"
            )


__all__ = ["SqliteMaintenanceStore", "SqliteSceneStore"]

"""``WorkstreamStore`` / ``EffectiveSceneStore`` 的 SQLite 实现。

三张表已经建好（``db.py`` 的 ``_TARGET_DDL``，本片**不改 DDL**），列与领域对象的
对应关系逐表写在下面 —— 那三份对应关系是这一模块的全部内容，因为「缺列就交回」
的意思是：任何一格在表上没有**列**的字段，都由 ``payload`` 承载，而**读回时不许
静默相信 payload**。

## 为什么读回时要拿扁平列与 payload 比一遍

``adapters/sqlite/scenes.py::_check_record_columns`` 是同一条，那里的注释逐字说了
理由，这里只是照做：扁平列是为了「要用它查」（按工作区列、按 revision 做 CAS），
payload 是整份对象。两者不一致只有两种来源 —— 有人手工改过库，或者某一次写入
只更新了一边。两种都让「按列查出来的」与「按对象读出来的」说的是两件事，
而**盘上一切正常**：一次 CAS 会照着一个对象上根本不存在的 revision 判成功或失败。

## 三张表的列 → 字段

``workstream``：扁平列 ``workstream_id / workspace / scene_id / state / revision /
created_at / updated_at / pause_reason`` 全部是「要用它查」或「CAS 要比」的那几格
（按工作区列、按 scene 列、revision 是 CAS 的量、state/pause_reason 是状态迁移的
输入）；``launch_id / scene_version / ordinal / name / queue / run_id /
next_wake_at`` 进 payload。

``launch_record``：扁平列是那条 UNIQUE 的四格加 ``created_at``；``workstream_id /
run_id / request_digest / launched_by`` 进 payload。**那条 UNIQUE 就是幂等的载体**
（任务书 §5 承重点 1 逐字：「判据的载体是 ``launch_record`` 那条 UNIQUE + 一条读回
路径，两半都要测」），所以四格必须是真列，不能藏在 payload 里 —— 藏在 JSON 里的
「唯一」在 SQLite 上是一个谁也拦不住的约定。

``effective_scene``：扁平列 ``scene_id / version / workspace / digest /
created_at``；``compiled_from / at / compiler / cells / not_reported / conflicts``
进 payload。**没有 ``state`` 列是对的**：它是不可变的，一个 state 列在那里只会
让人以为它可以被改。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ...domain.errors import StoreError
from ...domain.workstream import EffectiveScene, LaunchRecord, Workstream, effective_scene_digest
from ...ports.workstreams import EffectiveSceneStore, WorkstreamStore
from .db import bind_workspace, ensure_schema, session, write_transaction
from .payload import dump_payload as _dump
from .payload import load_payload as _load

#: ``workstream`` 表里那几个「不只是索引」的列。读回来时逐个与 payload 比。
_WORKSTREAM_COLUMNS = ("workspace", "scene_id", "state", "revision")


def _bind(conn: sqlite3.Connection, workspace: str) -> None:
    """把这个库绑到 ``workspace``。**必须在写事务里调用。**

    三张表（``workstream`` / ``launch_record`` / ``effective_scene``）都在
    ``db.py::OWNED_TABLES`` 里 —— 那一份的注释写明了理由：它们的每一行都写着
    ``workspace``。而 ``ensure_schema`` 有反向的那一条判据：**有带所有者的行却
    没有绑定 ⇒ 当场拒绝打开**（那一批行属于谁没有答案）。

    ## 少了这一句的后果是实测出来的，而且它跨层

    2026-09-29 实测：``start_workstream`` 在一只**干净的**库上第一次跑，第 3 步
    冻结有效场景就写了 ``effective_scene`` 一行 —— 而绑定从没写过。第 4 步受理要
    ``runs()``，``SqliteRunStore.__init__`` 里那一句 ``ensure_schema`` 于是退
    ``StoreError``：

        状态库 … 里已经有研究产出，却没有工作区绑定（schema_meta 里没有 workspace）。

    也就是说：一次**成功的**冻结，把同一个库对之后每一个 store 的打开都变成了
    拒绝 —— 包括 ``get_workstream`` 这类纯读（组合根交给处理器的是工厂，每次调用都
    新建一个 store）。回执上那一行是真的写下去了，而库从此打不开。

    ## 三处写路径都调它，包括幂等那一支

    ``put_launch`` / ``put_workstream``（插入那一半）/ ``put_scene`` 是这三张表
    唯一的落盘口，而三条都在写事务里。``_update``（CAS 那一半）**不调** —— 与
    ``runs.py::stop`` / ``review.py::resolve`` 两处逐字同一条理由：它改的是一行
    已经在盘上的行，而那一行落盘那次已经把库绑好了。

    类型是 ``StoreError`` 而不是 ``bind_workspace`` 的缺省 ``EvidenceError``：与
    ``adapters/sqlite/runs.py`` / ``fetch_attempt.py`` / ``pool.py`` 三处逐字同一条
    —— 判词一份，类型归主语。
    """
    bind_workspace(conn, workspace, error=StoreError)

#: ``effective_scene`` 的那几格。``digest`` 是**算出来的**，所以它也在这里 ——
#: 一行被就地改过而摘要没跟上，只有在读回时重算一遍才发现得了。
_SCENE_COLUMNS = ("workspace", "digest")


def _select_launch(
    conn,
    *,
    workspace: str,
    scene_id: str,
    scene_version: int,
    idempotency_key: str,
) -> LaunchRecord | None:
    """按四格业务键取那一行，**读回来的是盘上的字面**。

    这一条被三个地方用：``get_launch``、``put_launch`` 的两条路。抽出来不是为了
    少写一遍 SQL，而是为了「写成功之后交出去的那一行」与「读路径交出去的那一行」
    是**同一个查询**的结果 —— 两处各写一遍的那天，一条把入参对象直接回给调用方的
    实现会与这一条分家，而它在报告上与真正的读回一模一样（端口那一份的 docstring
    逐字要的就是「不是入参对象」：直接回入参的实现在「另一处已经写过了」这件事上
    什么也证明不了）。
    """
    row = conn.execute(
        "SELECT payload FROM launch_record WHERE workspace = ? AND scene_id = ? "
        "AND scene_version = ? AND idempotency_key = ?",
        (workspace, scene_id, scene_version, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    return LaunchRecord.model_validate(
        _load(row["payload"], f"launch_record/{workspace}/{scene_id}@{scene_version}")
    )


def _compare(row, record: Workstream) -> None:
    """扁平列与 payload 对不上就报错，并点名是**哪一列**。"""
    flat = {
        "workspace": row["workspace"],
        "scene_id": row["scene_id"],
        "state": row["state"],
        "revision": row["revision"],
    }
    loaded = {
        "workspace": record.workspace,
        "scene_id": record.scene_id,
        "state": record.state.value,
        "revision": record.revision,
    }
    differ = [name for name in _WORKSTREAM_COLUMNS if flat[name] != loaded[name]]
    if differ:
        raise StoreError(
            f"workstream {record.workstream_id!r} 的扁平列与 payload 对不上："
            f"{', '.join(differ)}。\n"
            "  两处说的是同一件事，而按列查（列表、CAS）读的是列那一份、按对象读的是"
            "payload 那一份 —— 不一致时一次状态迁移会照着一个对象上根本不存在的值判"
            "成败，而盘上看起来一切正常。\n"
            "  处置是查这一行是怎么写进去的（这个库只有本模块写）。"
        )


class SqliteWorkstreamStore(WorkstreamStore):
    """``workstream`` 与 ``launch_record`` 两张表。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        # 与 ``SqliteSceneStore`` / ``SqliteOutreachStore`` 等每一个 SQLite 存储
        # 逐字同一条：建表在构造时做，而且是幂等的。**少了这一句的后果只在空库上
        # 现形**（本机实测：契约测试第一条就在 ``no such table: launch_record``
        # 上红），而它看起来像「这一层的判据写错了」—— 一条与本模块无关的错。
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    # LaunchRecord
    # ------------------------------------------------------------------
    def put_launch(self, record: LaunchRecord) -> LaunchRecord:
        payload = _dump(record.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            _bind(conn, record.workspace)
            inserted = conn.execute(
                "INSERT INTO launch_record (launch_id, workspace, scene_id, scene_version, "
                "idempotency_key, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (workspace, scene_id, scene_version, idempotency_key) "
                "DO NOTHING",
                (
                    record.launch_id,
                    record.workspace,
                    record.scene_id,
                    record.scene_version,
                    record.idempotency_key,
                    record.created_at.isoformat(),
                    payload,
                ),
            ).rowcount
            # **两条路都读回来**，不把入参对象交出去：刚插进去的那一条与
            # 「另一台机器上已经写过了」的那一条在这一层必须是同一个形状的答案，
            # 否则「返回值就是盘上那一行」这句话只在冲突路径上成立 —— 而那正是
            # 端口那一份点名的形态（直接回入参的实现证明不了任何事）。
            existing = _select_launch(
                conn,
                workspace=record.workspace,
                scene_id=record.scene_id,
                scene_version=record.scene_version,
                idempotency_key=record.idempotency_key,
            )
            assert existing is not None  # 刚写过 / DO NOTHING 只在已有行时为 0
            if inserted:
                return existing
            # 键撞上了。比对**内容**再判是重发还是冲突：少了读回，
            # 「重发回同一次启动」这句话没有落点；少了比对，
            # 拿同一个键交另一份请求会静默拿回第一次那一行。
            if existing.request_digest == record.request_digest:
                return existing
            raise StoreError(
                f"启动 {existing.launch_id!r} 已经用这个键记过了，而这一次交上来的"
                f"内容不是同一份：盘上是 {existing.request_digest}，本次是 "
                f"{record.request_digest}。\n"
                "  「同一个请求回同一个 launch」说的是**同一个请求**：键相同而内容"
                "不同是一次冲突，静默拿回第一次那一行会让调用方以为自己开的那一轮"
                "跑的是他要的东西。要改内容就换一个键。"
            )

    def get_launch(
        self,
        *,
        workspace: str,
        scene_id: str,
        scene_version: int,
        idempotency_key: str,
    ) -> LaunchRecord | None:
        with session(self._path) as conn:
            return _select_launch(
                conn,
                workspace=workspace,
                scene_id=scene_id,
                scene_version=scene_version,
                idempotency_key=idempotency_key,
            )

    # ------------------------------------------------------------------
    # Workstream
    # ------------------------------------------------------------------
    def next_ordinal(self, scene_id: str) -> int:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT MAX(CAST(json_extract(payload, '$.ordinal') AS INTEGER)) AS top "
                "FROM workstream WHERE scene_id = ?",
                (scene_id,),
            ).fetchone()
        top = row["top"] if row is not None else None
        return 1 if top is None else int(top) + 1

    def put_workstream(
        self, stream: Workstream, *, expected_revision: int | None
    ) -> None:
        if expected_revision is None:
            if stream.revision != 1:
                raise StoreError(
                    f"首次创建 workstream={stream.workstream_id!r} 时 revision 必须是 1，"
                    f"本次是 {stream.revision}。\n"
                    "带着别的 revision 创建，会让「这个 id 已经存在」与「我写的是"
                    "第 n 版」变成同一件事 —— 而库里那一条的 revision 与它无关。"
                )
            self._insert(stream)
            return

        if stream.revision != expected_revision + 1:
            raise StoreError(
                f"workstream={stream.workstream_id!r} 的写入 revision 必须是 "
                f"{expected_revision + 1}（读到的 {expected_revision} 加一），本次是 "
                f"{stream.revision}。\n"
                "这一条挡的是「改完对象直接写回」：那跳过了一次自增，于是并发的另一个"
                "写者会**成功** —— 而它以为自己在比的那个版本从来没存在过。"
            )
        self._update(stream, expected_revision=expected_revision)

    def _insert(self, stream: Workstream) -> None:
        with session(self._path) as conn, write_transaction(conn):
            _bind(conn, stream.workspace)
            inserted = conn.execute(
                "INSERT INTO workstream (workstream_id, workspace, scene_id, state, "
                "revision, created_at, updated_at, pause_reason, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (workstream_id) DO NOTHING",
                _row(stream),
            ).rowcount
            if inserted:
                return
            row = conn.execute(
                "SELECT workspace, revision FROM workstream WHERE workstream_id = ?",
                (stream.workstream_id,),
            ).fetchone()
            assert row is not None
            raise StoreError(
                f"workstream={stream.workstream_id!r} 已经存在（工作区 "
                f"{row['workspace']!r}，revision={row['revision']}），而本次调用声称"
                "它是首次创建。\n"
                "业务键是 ``<scene_id>#<ordinal>``，而它唯一的来源是"
                "``next_ordinal`` —— 两个写者同时读到同一个序号时，**只有一个人**"
                "该拿到这个 id，而这件事只有存储答得了。"
            )

    def _update(self, stream: Workstream, *, expected_revision: int) -> None:
        with session(self._path) as conn, write_transaction(conn):
            changed = conn.execute(
                "UPDATE workstream SET workspace = ?, scene_id = ?, state = ?, revision = ?, "
                "created_at = ?, updated_at = ?, pause_reason = ?, payload = ? "
                "WHERE workstream_id = ? AND revision = ?",
                (
                    stream.workspace,
                    stream.scene_id,
                    stream.state.value,
                    stream.revision,
                    stream.created_at.isoformat(),
                    stream.updated_at.isoformat(),
                    stream.pause_reason,
                    _dump(stream.model_dump(mode="json")),
                    stream.workstream_id,
                    expected_revision,
                ),
            ).rowcount
            if changed:
                return
            # 三种原因分别报 —— 与 ``scenes.py::_update_record`` 逐字同一条纪律：
            # 「不存在」与「过期了」的处置相反（去查 id / 去重读再重试）。
            current = conn.execute(
                "SELECT workspace, revision FROM workstream WHERE workstream_id = ?",
                (stream.workstream_id,),
            ).fetchone()
            if current is None:
                raise StoreError(
                    f"workstream={stream.workstream_id!r} 不存在，而本次调用以为它在第 "
                    f"{expected_revision} 版。一个字都没写。"
                )
            raise StoreError(
                f"workstream={stream.workstream_id!r} 现在是第 {current['revision']} 版，"
                f"而本次调用读到的是第 {expected_revision} 版。一个字都没写。\n"
                "  这是 CAS 的拒绝：过期的那一次不覆盖新的一次，因为覆盖之后"
                "「有人先改了」这件事在盘上不留痕。处置是重新读一遍再决定。"
            )

    def get_workstream(self, workstream_id: str) -> Workstream | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT workspace, scene_id, state, revision, payload FROM workstream "
                "WHERE workstream_id = ?",
                (workstream_id,),
            ).fetchone()
        if row is None:
            return None
        stream = Workstream.model_validate(
            _load(row["payload"], f"workstream/{workstream_id}")
        )
        _compare(row, stream)
        return stream

    def workstreams(self, *, workspace: str) -> tuple[Workstream, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT workstream_id, workspace, scene_id, state, revision, payload "
                "FROM workstream WHERE workspace = ? ORDER BY workstream_id",
                (workspace,),
            ).fetchall()
        found = []
        for row in rows:
            stream = Workstream.model_validate(
                _load(row["payload"], f"workstream/{row['workstream_id']}")
            )
            _compare(row, stream)
            found.append(stream)
        return tuple(found)


def _row(stream: Workstream) -> tuple:
    """领域对象 → 那一行的九个值。**只有这一个地方拼行**。"""
    return (
        stream.workstream_id,
        stream.workspace,
        stream.scene_id,
        stream.state.value,
        stream.revision,
        stream.created_at.isoformat(),
        stream.updated_at.isoformat(),
        stream.pause_reason,
        _dump(stream.model_dump(mode="json")),
    )


class SqliteEffectiveSceneStore(EffectiveSceneStore):
    """``effective_scene`` 一张表。**没有 update 路径** —— 见端口那一份的 docstring。"""

    def __init__(self, path: Path) -> None:
        self._path = path
        # 同 :class:`SqliteWorkstreamStore.__init__`。
        with session(self._path) as conn:
            ensure_schema(conn)

    def put_scene(self, scene: EffectiveScene) -> None:
        payload = _dump(scene.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            _bind(conn, scene.workspace)
            inserted = conn.execute(
                "INSERT INTO effective_scene (scene_id, version, workspace, digest, "
                "created_at, payload) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (scene_id, version) DO NOTHING",
                (
                    scene.scene_id,
                    scene.version,
                    scene.workspace,
                    scene.digest,
                    scene.at.isoformat(),
                    payload,
                ),
            ).rowcount
            if inserted:
                return
            row = conn.execute(
                "SELECT workspace, digest, payload FROM effective_scene "
                "WHERE scene_id = ? AND version = ?",
                (scene.scene_id, scene.version),
            ).fetchone()
            assert row is not None
            existing = EffectiveScene.model_validate(
                _load(row["payload"], f"effective_scene/{scene.scene_id}@{scene.version}")
            )
            # 先判盘上那一行**自己**自不自洽，再判它与这一次是不是同一份。两件事
            # 混在一起报的话，「有人改过这一行」会被说成「你交的内容不对」，而
            # 处置相反（前者去查是谁改的，后者去查这一次编译）。
            _check_scene_row(row, existing)
            if existing.digest == scene.digest and row["digest"] == scene.digest:
                # 逐字相同 ⇒ 幂等。它出现的场合是**重发同一次编译**（同一个
                # ``preview_effective_scene`` 被调两次），而那不是一个错误。
                return
            raise StoreError(
                f"effective_scene {scene.scene_id!r} 第 {scene.version} 版已经写过了，"
                f"而这一次的内容不是同一份：盘上是 {row['digest']}，本次是 "
                f"{scene.digest}。\n"
                "  这一版是**不可变**的（B02-b）：同一 ``(scene, version)`` 在两次"
                "部署之间成了两份不同的东西时，引用它的每一处都看不出来 —— 而"
                "「这一版当时编译成什么」这个问题的答案会随读它的时刻变。\n"
                "  处置是发一版新的 Scene 再编译，不是改写这一版。"
            )

    def get_scene(self, scene_id: str, version: int) -> EffectiveScene | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT workspace, digest, payload FROM effective_scene "
                "WHERE scene_id = ? AND version = ?",
                (scene_id, version),
            ).fetchone()
        if row is None:
            return None
        scene = EffectiveScene.model_validate(
            _load(row["payload"], f"effective_scene/{scene_id}@{version}")
        )
        _check_scene_row(row, scene)
        return scene

    def versions(self, scene_id: str) -> tuple[int, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT version FROM effective_scene WHERE scene_id = ? ORDER BY version",
                (scene_id,),
            ).fetchall()
        return tuple(int(row["version"]) for row in rows)

    def scenes(self, *, workspace: str) -> tuple[EffectiveScene, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT scene_id, version, workspace, digest, payload FROM effective_scene "
                "WHERE workspace = ? ORDER BY scene_id, version",
                (workspace,),
            ).fetchall()
        found = []
        for row in rows:
            scene = EffectiveScene.model_validate(
                _load(row["payload"], f"effective_scene/{row['scene_id']}@{row['version']}")
            )
            _check_scene_row(row, scene)
            found.append(scene)
        return tuple(found)


def _check_scene_row(row, scene: EffectiveScene) -> None:
    """行上的两格（工作区、摘要）与对象上的必须一致。

    ``digest`` 单独判，而且判两次：与**列**比一次（防有人只改了列），与
    **重算**比一次（防有人把列和 payload 一起改了 —— 那一次会成功写进去，
    而它此后读起来完全正常）。
    """
    recomputed = effective_scene_digest(scene)
    if row["workspace"] != scene.workspace:
        raise StoreError(
            f"effective_scene {scene.scene_id!r}@{scene.version} 的 workspace 列是 "
            f"{row['workspace']!r}，而负载里是 {scene.workspace!r}。\n"
            "  这一格是跨工作区可见性判定的依据 —— 两处不一致时，一次「读不到别人的"
            "场景」会按列判成功而按对象判失败（或反过来），而两者都不会报错。"
        )
    if row["digest"] != scene.digest or recomputed != scene.digest:
        raise StoreError(
            f"effective_scene {scene.scene_id!r}@{scene.version} 的摘要对不上：列上是 "
            f"{row['digest']}，负载里是 {scene.digest}，按内容重算是 {recomputed}。\n"
            "  这一行被就地改过（或者写的时候只更新了一边）。摘要在这里的作用正是"
            "回答「这一版还是编译时的那一份吗」，而它对不上意味着那个问题的答案是"
            "「不是」—— 而盘上此前看不出来。"
        )

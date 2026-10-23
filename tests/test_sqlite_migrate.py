"""v1 → v3：一条**真实**的迁移，与它拒绝的那几种库。

两步各占一节：``v1 → v2`` 是**补血统**（给三张表里每一行证明一个归属），``v2 → v3`` 是
**换词表**（把维护三张表的 ``aspect`` 从五个面换成七个面）。两步的判据不同 —— 前者要的
是「证得出来」，后者要的是「认得出来」—— 所以它们拒的那些库也不同，各写各的。

## 这个文件为什么在 ``tests/`` 而不是在 ``marketops/adapters/sqlite/`` 旁边

因为它钉的是一条**一次性动作**的两半，而两半都不是「函数算得对不对」：

1. **它给哪些行盖章** —— 答案不来自被迁移的那些行，来自本部署自己的输入
   （``application/lineage.py``）。所以判据必须拿一份**真的**输入去跑，而那份输入
   的形状由生产者决定（清单由 ``cohorts.load_accounts`` 读、工件行由
   ``discovery_run._artifact_row`` 写）—— 测试里照着抄一份键名，会在生产者改名的
   那一刻静默失效：迁移仍然「成功」，只是再也没有一行证得出来。所以下面的工件走
   **真的行构造器**（那两行 import 一个下划线名是刻意的），而被迁移的那些行走**真的
   写路径**、再统一下调成 v1 的样子。
2. **它拒绝什么、以及拒绝之后盘上还剩什么** —— 一个失败的迁移若留下半份盖章的库，
   比不迁移更坏（报告里「迁移过」之后就没有第二个人会回头看那些行了）。判据问的是
   「一个字都没改」与「库里没有留下备份」，而不是异常类型。

## 这个文件里没有回执那一档

三档证明里的 ``roster-subject`` 与 ``artifact-evidence-id`` 在这里各有一条真路径。
回执那一档走的是同一个 ``subjects`` 集合（见 ``application/lineage._read_receipt``），
而要造一份真的回执得先跑一遍母池读入（``read_motherpool`` + ``write_receipt``：回执 id
是内容寻址的、读回时会被验），那件事有它自己的判据（``tests/test_motherpool.py``）。
**这一档在这个文件里没有覆盖** —— 这是明说的缺口，不是「已经收口」。

## 换词表那一节里，哪些行是手工造的

造一个 v2 的库分两步：真写路径造出库（血统、绑定、幂等形状都是真的），再把版本改回 2，
然后手工插进几行**旧词表**的 ``aspect``。手工的那一批是**必要的**而不仅仅是省事：今天的
写路径造不出它们（模型里已经没有那几个词了），而「今天的代码写不出来的东西」正是这一步
要处置的对象 —— 拿今天的写路径去造夹具，等于测「迁移会不会原样不改」。
"""


from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import (
    CONFIG_KEY_COLUMNS,
    LINEAGE_TABLES,
    ROUTE_TABLE_BY_NAME,
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    WORKSPACE_META_KEY,
    connect,
    session,
    set_meta,
    workspace_binding,
    write_transaction,
)
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.migrate import (
    MigrationReport,
    _admitted_kinds,
    _columns_of,
    _plan_job_kind,
    _table_sql,
    main,
    migrate,
)
from marketops.adapters.sqlite.outreach import SqliteOutreachStore
from marketops.adapters.sqlite.payload import dump_payload, load_payload
from marketops.adapters.sqlite.scenes import SqliteMaintenanceStore
from marketops.application.discovery_run import (
    DiscoverySite,
    _artifact_row,
    candidate_id_for,
)
from marketops.config import Settings
from marketops.domain.entities import ContactRoute, RouteOpportunity
from marketops.domain.enums import FetchOutcome, Grade, JobInputKind, SourceKind
from marketops.domain.errors import StoreError
from marketops.domain.evidence import Evidence, evidence_id
from marketops.domain.maintenance import Aspect, due_dedupe_key
from marketops.domain.outreach import (
    ApprovedClaim,
    OutreachStage,
    TemplateFamily,
    WritingPolicy,
)
from marketops.domain.route_records import (
    RouteObservation,
    canonical_route_key,
    observation_id_for,
)
from marketops.domain.util import sha256_text
from tests.conftest import DEFAULT_PROFILE
from tests.fakes import synthetic_dossiers, synthetic_scene_files

#: 这个测试所模拟的那个部署的工作区。与 ``settings`` 夹具的 ``profile`` 是同一个值
#: （它由 ``DEFAULT_PROFILE`` 来）—— 迁移盖上去的就是这一格。
WORKSPACE = DEFAULT_PROFILE

# --- 这一轮的数据 -----------------------------------------------------------------

#: 本部署被**授权研究**的那个账号：清单点名的（roster 那一档）。
ROSTER_ACCOUNT = "example-garden-club"

GARDEN = "garden.example.org"
MEADOW = "meadow.example.org"
#: 发现期给一台主机派生出来的候选 id。工件那一行写着它，所以它也是**主语**。
GARDEN_ID = candidate_id_for(GARDEN)
MEADOW_ID = candidate_id_for(MEADOW)
#: 一份工件里没有出现过的主机 —— 它派生的 id 谁也证明不了（存档那条拒绝判据用它）。
GHOST_ID = candidate_id_for("ghost.example.org")

FETCHED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

#: 页面字节的摘要。真实取值来自 ``document.sha256``（``evidence_ingest`` 把原样的那个
#: 值喂给 ``evidence_id``）。这里它的内容无关紧要 —— 承重的是「同一个账号 + 同一处
#: 字面 + 同一份字节 → 同一个 id」，那件事由 ``tests/test_models_invariants.py`` 守。
DIGEST = "0417165166c18fd3c544175412562dfa101e5ed546c6723a585c293d9c488f08"

#: 一个 v1 库里该有的行数，按表。
V1_ROWS: dict[str, int] = {"evidence": 3, "contact_route": 2, "route_opportunity": 1}

GARDEN_NAME = "Example Garden Club"
GARDEN_BODY = "Grounds and gardens"
ROSTER_LITERAL = "Established 1897"
GARDEN_URL = f"https://{GARDEN}/contact"


def _document_evidence(account_id: str, literal: str, *, url: str) -> Evidence:
    """一条文档级证据，形状与 ``evidence_ingest.document_evidence`` 写下的那一行同源。"""
    return Evidence(
        workspace=WORKSPACE,
        evidence_id=evidence_id(
            account_id=account_id,
            source_url=url,
            field=None,
            literal_value=literal,
            digest=DIGEST,
        ),
        account_id=account_id,
        source_url=url,
        source_kind=SourceKind.FETCHED_TEXT,
        literal_value=literal,
        content_sha256=DIGEST,
        fetched_at=FETCHED_AT,
        field=None,
    )


def _v1_line(evidence: Evidence) -> str:
    """存档里一行，**v1 的样子**：写得进去的每一格都在，除了血统那一格。

    只有这一种行需要手工构造（库那一侧是「写成 v2 再下调」，见
    ``_downgrade_to_v1``）—— 因为这几条判据问的就是「一个 v1 的存档被读/被迁时会
    怎样」，而今天没有任何写路径还会产出这样的行。
    """
    payload = evidence.model_dump(mode="json")
    payload.pop("workspace", None)
    return json.dumps(payload, ensure_ascii=False) + "\n"


# --- 造一个 v1 库 -----------------------------------------------------------------


def _write_inputs(settings: Settings) -> None:
    """把本部署自己的输入摆好：一份发现工件 + 一份已知账号清单。"""
    title = _document_evidence(GARDEN_ID, GARDEN_NAME, url=GARDEN_URL)
    body = _document_evidence(GARDEN_ID, GARDEN_BODY, url=GARDEN_URL)
    other = _document_evidence(MEADOW_ID, "Meadow Trust", url=f"https://{MEADOW}/about")
    sites = (
        DiscoverySite(
            site=GARDEN,
            candidate_id=GARDEN_ID,
            url=GARDEN_URL,
            query="gardening club devon",
            rank=1,
            fetch_outcome=FetchOutcome.OK,
            name=GARDEN_NAME,
            evidence_ids=(title.evidence_id, body.evidence_id),
            title_evidence_id=title.evidence_id,
        ),
        DiscoverySite(
            site=MEADOW,
            candidate_id=MEADOW_ID,
            url=f"https://{MEADOW}/about",
            query="gardening club devon",
            rank=2,
            fetch_outcome=FetchOutcome.OK,
            name="Meadow Trust",
            evidence_ids=(other.evidence_id,),
            title_evidence_id=other.evidence_id,
        ),
    )
    (settings.inputs_dir / "migration-test-probe.jsonl").write_text(
        "".join(
            json.dumps(_artifact_row(site, region="Devon"), ensure_ascii=False, sort_keys=True)
            + "\n"
            for site in sites
        ),
        encoding="utf-8",
    )
    (settings.inputs_dir / "migration-test-roster.yaml").write_text(
        "accounts:\n"
        f"  - account_id: {ROSTER_ACCOUNT}\n"
        "    organisation: Example Garden Club Ltd\n"
        "    cohort: known_success\n"
        "    domain: example.co.uk\n",
        encoding="utf-8",
    )


def _record_route(
    store: SqliteEvidenceStore, route: ContactRoute, *, observed_at: datetime
) -> None:
    """按**今天的写路径**记下一条路线 —— 一层观察，不是旧形状的那一行。

    观察落盘之后还要 ``_downgrade_routes_to_v4`` 把它搬回旧形状（见那里）：这一步要
    造的是一个 v4 的库。

    ``observed_at`` 是显式参数，不从 ``route.first_seen_at`` 取：那是**两个**时刻
    （「我们什么时候看到那一页」与「第一次看到它依据的材料」），而旧形状里只剩后面
    那一处可查 —— v4 → v5 那一步处置的正是这件事。
    """
    key = canonical_route_key(
        workspace=route.workspace, account_id=route.account_id, email=route.email
    )
    store.record_route(
        RouteObservation(
            observation_id=observation_id_for(
                workspace=route.workspace,
                account_id=route.account_id,
                canonical_key=key,
                observed_at=observed_at,
                route=route,
            ),
            canonical_key=key,
            workspace=route.workspace,
            account_id=route.account_id,
            observed_at=observed_at,
            route=route,
        )
    )


def _write_rows(settings: Settings) -> None:
    """三张表各写几行，**全部走真的写路径**（于是这个库是 v5 的、且带着绑定）。

    那两条路线给的 ``first_seen_at`` 就是 ``FETCHED_AT`` —— 真实路径上 ``bind`` 是从
    材料那一行折出这一格的（``moments.first_seen_at``），而 v4 → v5 正是拿它当
    ``observed_at``。夹具里两格给同一个值是有意的：这样搬回 v4 再迁上来，观察的
    ``observation_id`` 必须**逐位不变**，而那是一条能红的判据。
    """
    store = SqliteEvidenceStore(settings.database_path)
    store.put_evidence(_document_evidence(GARDEN_ID, GARDEN_NAME, url=GARDEN_URL))
    store.put_evidence(_document_evidence(GARDEN_ID, GARDEN_BODY, url=GARDEN_URL))
    store.put_evidence(
        _document_evidence(ROSTER_ACCOUNT, ROSTER_LITERAL, url="https://example.co.uk/about")
    )
    _record_route(
        store,
        ContactRoute(
            workspace=WORKSPACE,
            account_id=ROSTER_ACCOUNT,
            email="office@example.co.uk",
            first_seen_at=FETCHED_AT,
        ),
        observed_at=FETCHED_AT,
    )
    _record_route(
        store,
        ContactRoute(
            workspace=WORKSPACE,
            account_id=GARDEN_ID,
            email=f"hello@{GARDEN}",
            evidence_ids=[
                _document_evidence(GARDEN_ID, GARDEN_NAME, url=GARDEN_URL).evidence_id
            ],
            first_seen_at=FETCHED_AT,
        ),
        observed_at=FETCHED_AT,
    )
    store.put_opportunity(
        RouteOpportunity(workspace=WORKSPACE, account_id=GARDEN_ID, role_title="Head Gardener")
    )


#: v4 那张 ``contact_route`` 的建表语句。**抄一份在这里是刻意的**：这份代码里已经没有
#: 它了（v5 把它拆成了两张表），而这个夹具要造的正是「上一个版本写下的库」。从今天的
#: ``ROUTE_TABLE_BY_NAME`` 取的话，造出来的是一个 v5 库，那一步就永远不会被跑到 ——
#: 而它正是这一段唯一要测的东西。
_V4_ROUTE_TABLE = """CREATE TABLE contact_route (
    seq        INTEGER PRIMARY KEY,
    account_id TEXT NOT NULL,
    payload    TEXT NOT NULL,
    stored_at  TEXT NOT NULL
)"""

_V4_ROUTE_INDEX = (
    "CREATE INDEX contact_route_by_account ON contact_route (account_id, seq)"
)


def _downgrade_routes_to_v4(db_path: Path) -> None:
    """把路线那几张表改回 **v4 的样子**。

    v4 的形状是：``contact_route`` 一张**追加式**表（``seq`` 主键，payload 就是一条
    ``ContactRoute``），没有 ``route_observation``，也没有 ``route_rejection``。

    搬回去的内容是**真的**：每一条观察按它自己那一格 ``route`` 还原成一行 —— 旧表里的
    一行本来就**是**一次观察，这正是 v5 要认领的那件事。所以 v4 → v5 那一步在这个库上
    处置的是真行，而不是几行手写的假数据。

    ``seq`` 按 ``(observed_at, observation_id)`` 排出来：那是**今天**这一版知道的顺序，
    而旧库的 ``seq`` 只说「谁先插进去」。它在这里只是让夹具确定 —— 那一步虽然按
    ``seq`` 遍历，但折叠与遍历顺序无关。
    """
    with session(db_path) as conn, write_transaction(conn):
        rows = conn.execute(
            "SELECT observation_id, account_id, payload, stored_at FROM route_observation"
            " ORDER BY observed_at, observation_id"
        ).fetchall()
        legacy = [
            (
                str(row["account_id"]),
                dump_payload(
                    load_payload(
                        row["payload"], f"route_observation[{row['observation_id']}]"
                    )["route"]
                ),
                str(row["stored_at"]),
            )
            for row in rows
        ]
        for table in ("route_rejection", "route_observation", "contact_route"):
            conn.execute(f"DROP TABLE {table}")
        # 两句分开执行、不用 ``executescript``：后者会**先隐式提交**当前事务，于是
        # 上面那三句 DROP 与这里建表不在同一个事务里 —— 中途失败留下的是一个连
        # ``contact_route`` 都没有的库，而它看起来像「夹具写坏了」。
        conn.execute(_V4_ROUTE_TABLE)
        conn.execute(_V4_ROUTE_INDEX)
        conn.executemany(
            "INSERT INTO contact_route (account_id, payload, stored_at) VALUES (?, ?, ?)",
            legacy,
        )


def _downgrade_to_v1(db_path: Path) -> None:
    """把库改回 **v1 的样子**：三张表每一行去掉 ``workspace``、版本写回 1、绑定删掉。

    这样造出来的库与「本片之前那个构建写下的库」逐字同形：内容是真的（真的写路径、
    真的 ``stored_at`` / ``seq`` / 幂等形状），缺的那一格也是真的缺。手工写 SQL 造
    几行假数据做不到这一点 —— 那样测的是「迁移会不会数行」，不是「迁移会不会读一
    个真的库」。

    路线那几张表**先搬回 v4 的形状**（``_downgrade_routes_to_v4``）：一个 v1 的库当然
    也没有 v5 那两张新表，而下面那个循环按 ``seq`` 读 ``contact_route`` —— 形状不对
    的话它读的是另一个东西。
    """
    _downgrade_routes_to_v4(db_path)
    with session(db_path) as conn, write_transaction(conn):
        for table in LINEAGE_TABLES:
            key = "evidence_id" if table == "evidence" else "seq"
            rows = conn.execute(f"SELECT {key} AS k, payload FROM {table}").fetchall()
            for row in rows:
                payload = load_payload(row["payload"], f"{table}[{row['k']}]")
                payload.pop("workspace", None)
                conn.execute(
                    f"UPDATE {table} SET payload = ? WHERE {key} = ?",
                    (dump_payload(payload), row["k"]),
                )
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))
        set_meta(conn, SCHEMA_VERSION_KEY, "1")


def _build_v1(settings: Settings) -> None:
    """一个**真实的** v1 库和它自己的输入。"""
    _write_inputs(settings)
    _write_rows(settings)
    _downgrade_to_v1(settings.database_path)


# --- 读盘 -------------------------------------------------------------------------


def _version_of(db_path: Path) -> int | None:
    """版本那一格。``None`` = 这一格不在（v1 的库没有它）。"""
    with session(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
        ).fetchone()
    return None if row is None else int(row[0])


def _version_of_a_backup(backup: Path) -> int | None:
    """备份那一份的版本格 —— **只读且 ``immutable=1``**。

    读一份备份不得在它旁边留下 ``-wal`` / ``-shm``：一个「只是读了一下」就多出两个
    伴生文件的备份，与它被备份的那一刻已经不是同一份字节了，而那是备份存在的唯一
    理由。**活库那一侧不能这么读**：它跑在 WAL 上，``immutable=1`` 会让 SQLite 看不见
    ``-wal`` 里已提交的那一部分 —— 于是刚迁完的库读出来还是迁移前的版本，而那看起来
    像「迁移没生效」。
    """
    conn = sqlite3.connect(f"file:{backup}?mode=ro&immutable=1", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (SCHEMA_VERSION_KEY,)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else int(row[0])


#: 一张表在**这一版代码**里可能的键列，按「先看哪个」的顺序。
#:
#: ``contact_route`` 在这两种库里键不一样（v4 是 ``seq``、v5 是 ``canonical_key``），
#: 而这个文件里每一处读盘都要**两种库都读得了** —— 判据的一半拿迁移**前**的库当输入，
#: 另一半拿迁移**后**的。写死 ``seq`` 的话，后半那些用例会以
#: ``no such column: seq`` 的形式红，而那读起来像「库坏了」。
_KEY_COLUMNS: tuple[str, ...] = ("evidence_id", "canonical_key", "seq")


def _row_key(conn: sqlite3.Connection, table: str) -> str:
    """这张表此刻在盘上的键列名。三选一，都不在时让 SQLite 自己炸出来。"""
    columns = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    for candidate in _KEY_COLUMNS:
        if candidate in columns:
            return candidate
    return _KEY_COLUMNS[0]


def _rows_of(db_path: Path) -> dict[str, list[dict]]:
    """三张表里的每一行（原样解出来的 payload），带它在这一行里的键。"""
    rows: dict[str, list[dict]] = {}
    with session(db_path) as conn:
        for table in LINEAGE_TABLES:
            key = _row_key(conn, table)
            found = conn.execute(
                f"SELECT {key} AS k, payload FROM {table} ORDER BY {key}"  # noqa: S608
            ).fetchall()
            rows[table] = [
                {"key": str(row["k"]), **load_payload(row["payload"], table)} for row in found
            ]
    return rows


def _workspaces_in(db_path: Path) -> set[object]:
    """这些行**写着**的工作区（``None`` 表示那一格不在）。"""
    return {row.get("workspace") for table in _rows_of(db_path).values() for row in table}


def _lineage_rows(db_path: Path) -> list[str]:
    """**带着血统**的那些行的位置（``表[键]``）。一个 v1 的库这里是空表。

    比「工作区取值集合」更直接：``{None}`` 既可能是「一行都没有」也可能是「每一行都
    没有那一格」，而这一条只说后者 —— 拒绝之后要看的是**那几行原样还在、且都还缺着
    那一格**，不是「这些行的工作区是空」。
    """
    return [
        f"{table}[{row['key']}]"
        for table, rows in _rows_of(db_path).items()
        for row in rows
        if "workspace" in row
    ]


# --- 那道闸：一个 v1 库不许被直接打开 ---------------------------------------------


def test_the_store_refuses_a_v1_database_and_names_the_command(settings: Settings) -> None:
    """v1 的库被这一版代码打开时**当场拒绝**，并且说得出怎么修。

    不这么做的话，那些行会在 ``Evidence`` 的模型校验上炸成一句 pydantic 的
    ``ValidationError`` —— 一个「看起来像库坏了」的失败，而事实是「这个库还没迁移」。
    两种处置（找运维 / 跑一次迁移）在报告里必须分得开。
    """
    _build_v1(settings)

    with pytest.raises(StoreError, match="marketops.adapters.sqlite.migrate"):
        SqliteEvidenceStore(settings.database_path)


# --- (a) 证得出来的行：盖章 + 备份 + 事后读得出来 ---------------------------------


def test_the_migration_stamps_every_provable_row_and_writes_the_binding(
    settings: Settings,
) -> None:
    """一份 v1 库 + 本部署自己的输入 → 每一行都盖上本部署，且**出处分得开**。"""
    _build_v1(settings)

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert report.already_current is False
    assert (report.from_version, report.to_version) == (1, SCHEMA_VERSION)
    assert report.rows == V1_ROWS
    # 三档证明各自证明了几行。分开报是有用的：``artifact-evidence-id`` 是**同一次
    # 运行**写下的工件逐字点名的那一行（最强），``artifact-subject`` 是**一步推导**
    # （最弱）。合成一个数之后，读的人看不出有多少行是靠最弱那一档来的。
    assert report.proof_counts == {
        "artifact-evidence-id": 3,
        "roster-subject": 2,
        "artifact-subject": 1,
    }

    # 每一行都盖上了本部署 —— 而没有一行是「缺省 = 当前部署」盖出来的：六行里有四行
    # 的出处是工件，把工件从输入里拿掉它们就证不出来（见下面那条拒绝判据）。
    assert _workspaces_in(settings.database_path) == {WORKSPACE}
    assert _version_of(settings.database_path) == SCHEMA_VERSION
    with session(settings.database_path) as conn:
        assert workspace_binding(conn) == WORKSPACE

    # 备份就在库旁边，而且**逐字是迁移前那个东西**：版本 1、每一行都还没有那一格、
    # 行数一样。读它用的是 immutable 那一手，所以它旁边不会多出 -wal / -shm。
    backup = report.backup
    assert backup is not None
    assert backup.parent == settings.database_path.parent
    assert backup.name.startswith(f"{settings.database_path.name}.v1-")
    assert _version_of_a_backup(backup) == 1
    assert _lineage_rows(backup) == []
    assert {table: len(rows) for table, rows in _rows_of(backup).items()} == V1_ROWS
    assert list(backup.parent.glob(f"{backup.name}-*")) == []

    # 迁移之后这些行**读得出来**，而且读出来的就是盖上去的那个值。这一步是整件事的
    # 终点：迁移不是为了把库改绿，是为了让这些行在这一版代码里可用。
    store = SqliteEvidenceStore(settings.database_path)
    assert store.list_evidence(ROSTER_ACCOUNT)[0].workspace == WORKSPACE
    assert len(store.list_routes(ROSTER_ACCOUNT)) == 1
    assert store.list_opportunities(GARDEN_ID)[0].role_title == "Head Gardener"

    # 报告是给人读的那一份：它必须说得出「迁移前备份在哪」与「靠什么证明的」。
    rendered = report.render()
    assert str(backup) in rendered
    assert "artifact-evidence-id 3 行" in rendered


# --- (b) 证不出来的行：整份拒绝、点名、一个字都不改 -------------------------------


def test_a_row_the_inputs_cannot_prove_refuses_the_whole_run_and_is_named(
    settings: Settings, tmp_path: Path
) -> None:
    """**一行证不出来就整份拒绝** —— 而且点名那一行。

    这里的输入里**少了工件**（只剩清单）：于是清单那一档的两行仍然证得出来，工件那
    一档的四行证不出来。**半信半疑地盖一部分比不盖更坏** —— 报告里「迁移完成」之后，
    剩下那些行就没有第二个人会回头看它们了。所以拒绝的粒度是整份，不是那一行。
    """
    _build_v1(settings)
    without_artifact = tmp_path / "inputs-without-the-artifact"
    without_artifact.mkdir()
    source = settings.inputs_dir / "migration-test-roster.yaml"
    (without_artifact / source.name).write_text(source.read_text(encoding="utf-8"), "utf-8")

    with pytest.raises(StoreError) as excinfo:
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=without_artifact)

    message = str(excinfo.value)
    assert "4 行" in message, message
    # 点名：表 + 主键，以及「它为什么证不出来」（账号 + 引用）。运维拿着这一句就能直接
    # 去看那一行，不需要先把库导出来。
    assert "evidence[" in message and "contact_route[" in message
    assert GARDEN_ID in message
    # 而且它说得出**手上有什么**：「0 个工件点名过的证据 id」正是这四行证不出来的
    # 原因，也是运维要放回去的那份输入。
    assert "0 个工件点名过的证据 id" in message

    # 一个字都没改：版本还是 1、每一行都还没有那一格、库旁边没有备份。
    assert _version_of(settings.database_path) == 1
    assert _lineage_rows(settings.database_path) == []
    assert list(settings.database_path.parent.glob("*.bak")) == []


def test_the_archive_is_refused_by_the_same_proof_and_names_file_and_line(
    settings: Settings,
) -> None:
    """JSONL 存档那一侧：**同一份证明**、**同一个拒绝粒度**，点名文件与行号。

    存档与库是同一批写入的两种落盘（见 ``adapters/jsonl/store.py``），所以给它们找
    第二套证明来源就会有两条判据 —— 而它们在某一行的判定上不一致时没有一处会红。
    拒绝时点名的是 ``文件 + 行号``：存档的文件名是账号，一个文件里可能有上百行，
    「第几行」是唯一能把话说清的位置。
    """
    _build_v1(settings)
    directory = settings.evidence_dir
    provable = directory / f"{ROSTER_ACCOUNT}.jsonl"
    provable.write_text(
        _v1_line(
            _document_evidence(
                ROSTER_ACCOUNT, ROSTER_LITERAL, url="https://example.co.uk/about"
            )
        ),
        encoding="utf-8",
    )
    # 第二行谁也证明不了：这个账号不在清单里，也没有任何工件点过它。
    stranger = directory / f"{GHOST_ID}.jsonl"
    stranger.write_text(
        _v1_line(_document_evidence(GHOST_ID, "Ghost", url="https://ghost.example.org/")),
        encoding="utf-8",
    )
    before = {path: path.read_bytes() for path in (provable, stranger)}

    with pytest.raises(StoreError) as excinfo:
        migrate(
            settings.database_path,
            profile=WORKSPACE,
            inputs_dir=settings.inputs_dir,
            archive_dirs=(directory,),
        )

    message = str(excinfo.value)
    assert str(stranger) in message and "第 1 行" in message, message
    # 存档与库**都没有**动：存档那一侧是预检（在任何写入之前），库那一侧是计划
    # （同样在任何写入之前）。一处失败不该留下两个半成品。
    assert {path: path.read_bytes() for path in (provable, stranger)} == before
    assert list(directory.glob("*.bak")) == []
    assert _version_of(settings.database_path) == 1
    assert _lineage_rows(settings.database_path) == []


# --- 成功那一侧的另一半：存档被盖上，且重跑不再改 ---------------------------------


def test_the_archive_is_stamped_and_a_second_run_changes_nothing(
    settings: Settings,
) -> None:
    """一次迁移把库与存档一起盖上；**重跑是核对，不是再盖一遍**。

    重跑之所以必须说得出「未改动」，是因为它在真实运维里是最常发生的那一次：上一次
    中途失败，或者人只是想再看一眼报告。一个把存档又盖一遍的实现会让每一次重跑都在
    盘上留下一份新的备份，而「哪一份是迁移前」就没有答案了。
    """
    _build_v1(settings)
    directory = settings.evidence_dir
    archive = directory / f"{ROSTER_ACCOUNT}.jsonl"
    original = _v1_line(
        _document_evidence(ROSTER_ACCOUNT, ROSTER_LITERAL, url="https://example.co.uk/about")
    )
    archive.write_text(original, encoding="utf-8")

    first = migrate(
        settings.database_path,
        profile=WORKSPACE,
        inputs_dir=settings.inputs_dir,
        archive_dirs=(directory,),
    )

    assert len(first.archive) == 1
    assert first.archive[0].unchanged is False
    assert first.archive[0].rows == 1
    backup = first.archive[0].backup
    assert backup is not None and backup.parent == directory
    assert backup.read_text(encoding="utf-8") == original
    stamped = archive.read_text(encoding="utf-8")
    assert json.loads(stamped)["workspace"] == WORKSPACE

    again = migrate(
        settings.database_path,
        profile=WORKSPACE,
        inputs_dir=settings.inputs_dir,
        archive_dirs=(directory,),
    )

    assert again.already_current is True
    assert again.backup is None
    assert [item.unchanged for item in again.archive] == [True]
    assert archive.read_text(encoding="utf-8") == stamped
    assert list(directory.glob("*.bak")) == [backup]


# --- 别人的库：绑定说了算 ---------------------------------------------------------


def test_a_database_bound_to_another_workspace_is_refused(settings: Settings) -> None:
    """一个已经绑在别的工作区上的库：**换一个 profile 也迁不动它**。

    「这个库是谁的」是逐库的事实，不是这一次调用可以决定的参数。不拒的话，一次拿错
    了配置的运行会把本部署的名字盖到别人的库上 —— 而那次写入之后，盘上就再也没有
    东西说得出原来那个库属于谁了。
    """
    _build_v1(settings)
    migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    with pytest.raises(StoreError, match="绑在工作区"):
        migrate(settings.database_path, profile="another-scene", inputs_dir=settings.inputs_dir)

    assert _workspaces_in(settings.database_path) == {WORKSPACE}
    with session(settings.database_path) as conn:
        assert workspace_binding(conn) == WORKSPACE


def test_a_database_holding_rows_without_a_binding_is_refused(settings: Settings) -> None:
    """第三个闸：**有行、没绑定**的库不被认领。

    写路径构造不出这个状态（第一行写入就绑，见 ``db.bind_workspace``），迁移也构造不
    出（盖章、版本与绑定在**同一个**写事务里）。所以它只可能来自一次手工修改，或一次
    没做完的操作。不认领它的理由是那句话本身：「这些行属于谁」没有答案 —— 而按「当前
    部署」去读，就是把别人的事实读成自己的。

    它和上面两条是一条线上的三个位置：版本那条管「这个库还没被处理过」，绑定那条管
    「这个库已经有主了」，这一条管「库里有东西、但没人认领」。
    """
    _write_inputs(settings)
    _write_rows(settings)
    assert sum(len(rows) for rows in _rows_of(settings.database_path).values()) == sum(
        V1_ROWS.values()
    )
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))

    with pytest.raises(StoreError, match="没有工作区绑定"):
        SqliteEvidenceStore(settings.database_path)


def test_a_database_whose_jobs_belong_to_another_scene_is_refused(settings: Settings) -> None:
    """库自己的 ``research_job.scene_id`` 说它服务的是别人 → 一行都不盖。

    它只在**拒绝**的方向上有用（一个库可以没有任何作业），而拒绝正是需要的：作业的
    ``scene_id`` 是受理那一刻冻住的，所以它是这个库**自己**关于「我在给谁用」的记录。
    """
    _build_v1(settings)
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute(
            "INSERT INTO research_job (job_id, scene_id, input_kind, input_ref,"
            " idempotency_key, request_sha256, state, reason, created_at, updated_at)"
            " VALUES ('job-1', 'some-other-scene', 'known_accounts', 'roster-1',"
            " 'key-1', ?, 'queued', '', '2026-09-15T00:00:00+00:00',"
            " '2026-09-15T00:00:00+00:00')",
            ("0" * 64,),
        )

    with pytest.raises(StoreError, match="some-other-scene"):
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert _version_of(settings.database_path) == 1
    assert _lineage_rows(settings.database_path) == []


def test_a_missing_database_is_not_created(settings: Settings) -> None:
    """迁移**不建库**：建一个空的 v2 库由第一次写入自己完成。

    两件事的目的相反：迁移是「给已经存在的历史行**证明**一个归属」，而建库是「从今天
    开始记」。让前者顺手把后者做了，一个拿错了 ``MARKETOPS_DATA_ROOT`` 的运行就会在
    别人的目录里建出一个空库，而那看起来完全正常。
    """
    missing = settings.data_root / "state" / "not-created-yet.db"
    assert not missing.exists()

    with pytest.raises(StoreError, match="不建库"):
        migrate(missing, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert not missing.exists()


# --- 命令行：成功与失败各一个退出码 -----------------------------------------------


def test_the_command_line_entry_point_migrates_and_prints_the_report(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``python -m marketops.adapters.sqlite.migrate`` —— 拒绝消息里点名的就是这一条。

    **四个取值一个都不传**：库路径、工作区、输入目录全走部署配置。这不是省事，而是这条
    命令的正确用法 —— 迁移要用的那个「可信来源」就是部署配置本身，一个每次都要人手抄
    一遍的路径迟早会被抄成另一台机器的。
    """
    _build_v1(settings)
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(settings.data_root))
    monkeypatch.setenv("MARKETOPS_PROFILE", WORKSPACE)

    code = main(["--no-archive"])

    assert code == 0
    printed = capsys.readouterr().out
    # 「从哪一版起步」那一格是这一次的**输入**（这个库是 v1），而终点跟着代码走 ——
    # 所以这里写 ``SCHEMA_VERSION`` 而不是 3：把终点写死，下一次升版本这条断言的失败
    # 说的是「报告印错了」，而它其实只是「代码升了」。起点仍写死，那才是这条用例问的。
    assert f"1 → {SCHEMA_VERSION}" in printed
    assert _version_of(settings.database_path) == SCHEMA_VERSION
    assert _lineage_rows(settings.database_path) != []


def test_the_command_line_needs_no_deployment_config_when_everything_is_given(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """三个取值给全时**不读部署配置** —— 因为正经用法之一是在另一台机器上迁。

    ``load_settings`` 在 ``MARKETOPS_DATA_ROOT`` 缺失时直接抛（配置必须显式注入，
    没有缺省，这是对的）。但「把这个库拷到另一台机器上迁完带回来」是一条正经的处置
    路径，而那台机器上没有这个变量 —— 读它会把一次可控的救援变成一句「环境没配好」。

    所以：**一个都不缺**时才不读；缺一个就回落，而且回落来的每一个值照样要过同一串
    逐行证明（证明与配置无关，它来自输入）。
    """
    _build_v1(settings)
    monkeypatch.delenv("MARKETOPS_DATA_ROOT", raising=False)
    monkeypatch.delenv("MARKETOPS_PROFILE", raising=False)

    code = main(
        [
            "--db",
            str(settings.database_path),
            "--profile",
            WORKSPACE,
            "--inputs-dir",
            str(settings.inputs_dir),
            "--no-archive",
        ]
    )

    assert code == 0
    assert _version_of(settings.database_path) == SCHEMA_VERSION
    assert f"1 → {SCHEMA_VERSION}" in capsys.readouterr().out


def test_the_command_line_reports_a_refusal_without_a_traceback(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """拒绝走的是 ``stderr`` + 退出码 1，不是一句 Python 回溯。

    一次「这一行证不出来」的拒绝是**一个结果**：处置是把它读一遍，然后去处置那一行。
    包在回溯里会让它看起来像代码坏了 —— 而它恰恰是这条判据在工作。

    这里也顺带钉住了 ``--inputs-dir`` 这个覆盖口：拿错了输入目录是**最可能**的那次
    误用（换了一台机器、路径抄错），而它的表现必须是「整份拒绝」而不是「少盖了几行」。
    """
    _build_v1(settings)
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(settings.data_root))
    monkeypatch.setenv("MARKETOPS_PROFILE", WORKSPACE)
    empty = tmp_path / "no-inputs-at-all"
    empty.mkdir()

    code = main(["--inputs-dir", str(empty), "--no-archive"])

    captured = capsys.readouterr()
    assert code == 1
    assert "证不出血统" in captured.err
    assert captured.out == ""
    assert _version_of(settings.database_path) == 1
    assert _lineage_rows(settings.database_path) == []


# --- v2 → v3：换词表（§11 的七个面） ------------------------------------------------
#
# 这一节模拟的是**另一个历史状态**：库是 v2 的（血统已经在），而那三张维护表里存着
# **旧词表**的 aspect。与上面那一节的区别在于「哪些东西是先于这一次运行存在的」——
# 上面缺的是血统，这里缺的是词。


#: v2 的五个面，按它们**当年**的样子逐字写下来。
#:
#: **不从 ``migrate.ASPECT_RENAMES`` / ``ASPECT_SPLITS`` 导**：那样这份判据就是照抄被测
#: 代码，它红了只说明两边一起改了。这里写的是**历史**（当年盘上真的存着这几个词），而被
#: 测代码说的是「这几个词该怎么处置」—— 两者必须由两个地方写下来。
V2_NAMES = ("source_supply", "responsibility", "asset_fact", "role_email")

#: 收据那一侧要有 ``scene_id``：迁移不改它，但它决定 ``claims(scene_id)`` 能不能读回来。
V2_SCENE = "example-scene"

V2_OBSERVED_AT = "2026-09-01T00:00:00+00:00"
V2_SIGNALLED_AT = "2026-09-02T00:00:00+00:00"
V2_CLAIMED_AT = "2026-09-03T00:00:00+00:00"

#: 三张表各写了几行旧词的行 —— 这一段的两条断言（改了几行、删了几行）都要对上它。
V2_ASPECT_ROWS = {
    # 一个换名字的、两个被拆开的。
    "observation": ("source_supply", "asset_fact", "role_email"),
    # 一个换名字的、一个被拆开的（拆开的那一条会变成两行）。
    "change_signal": ("responsibility", "asset_fact"),
    # 一个换名字的、一个被拆开的（被拆开的这一行**删掉**）。
    "maintenance_claim": ("source_supply", "role_email"),
}


def _observation_payload(aspect: str) -> dict:
    """一行 v2 观测的 payload。**形状抄的是那一年的模型**，所以它逐字手写。"""
    return {
        "workspace": WORKSPACE,
        "subject_kind": "account",
        "subject_id": ROSTER_ACCOUNT,
        "aspect": aspect,
        "observed_at": V2_OBSERVED_AT,
        "source_ref": "run-1",
    }


def _signal_payload(aspect: str) -> dict:
    return {
        "workspace": WORKSPACE,
        "subject_kind": "account",
        "subject_id": ROSTER_ACCOUNT,
        "aspect": aspect,
        "signalled_at": V2_SIGNALLED_AT,
        "reason": "staff page changed",
    }


def _v2_key(aspect: str, *, attempt: int = 1) -> str:
    """一行 v2 回执的键 —— 用**今天这个函数**算。

    这不是「照抄被测代码」：键的定义两百行之前就在 ``domain/maintenance.py`` 里，而迁移
    要验的正是「存量那一列等于它自己那几列算出来的值」。这里用同一个函数造一份**对的**
    键，才可能造出该被接受的那一行；不对的那些由测试自己故意写歪。
    """
    return due_dedupe_key(
        scene_id=V2_SCENE,
        scene_version=1,
        subject_kind="account",
        subject_id=ROSTER_ACCOUNT,
        aspect=aspect,
        period=f"2026-09-0{attempt}",
    )


def _write_v2_aspect_rows(db_path: Path) -> None:
    """三张维护表各写几行**旧词表**的行，形状就是 v2 那个构建写下的。

    今天的写路径**造不出**这些行（模型里已经没有那几个词了），所以它们只能手工构造 ——
    而「今天的代码写不出来的东西」正是这个夹具要模拟的对象。库的其余部分仍然是真的
    （血统、绑定、幂等形状都由真写路径造出来），只有 ``aspect`` 那一批与版本是历史的。
    """
    with session(db_path) as conn, write_transaction(conn):
        for aspect in V2_ASPECT_ROWS["observation"]:
            conn.execute(
                "INSERT INTO observation (workspace, subject_kind, subject_id, aspect,"
                " observed_at, source_ref, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    WORKSPACE,
                    "account",
                    ROSTER_ACCOUNT,
                    aspect,
                    V2_OBSERVED_AT,
                    "run-1",
                    dump_payload(_observation_payload(aspect)),
                ),
            )
        for aspect in V2_ASPECT_ROWS["change_signal"]:
            conn.execute(
                "INSERT INTO change_signal (workspace, subject_kind, subject_id, aspect,"
                " signalled_at, reason, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    WORKSPACE,
                    "account",
                    ROSTER_ACCOUNT,
                    aspect,
                    V2_SIGNALLED_AT,
                    "staff page changed",
                    dump_payload(_signal_payload(aspect)),
                ),
            )
        for aspect in V2_ASPECT_ROWS["maintenance_claim"]:
            conn.execute(
                "INSERT INTO maintenance_claim (dedupe_key, attempt, workspace, scene_id,"
                " scene_version, subject_kind, subject_id, aspect, reason, period, state,"
                " claimed_at, finished_at, result_ref, failure)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _v2_key(aspect),
                    1,
                    WORKSPACE,
                    V2_SCENE,
                    1,
                    "account",
                    ROSTER_ACCOUNT,
                    aspect,
                    "cadence_elapsed",
                    "2026-09-01",
                    "done",
                    V2_CLAIMED_AT,
                    V2_CLAIMED_AT,
                    "run-1",
                    "",
                ),
            )


def _downgrade_to_v2(db_path: Path) -> None:
    """版本写回 2。**只改这一格** —— v2 与 v3 之间的差别只有词，没有列。

    路线那一侧照旧搬回 v4 的形状，理由同 ``_downgrade_to_v1``。
    """
    _downgrade_routes_to_v4(db_path)
    with session(db_path) as conn, write_transaction(conn):
        set_meta(conn, SCHEMA_VERSION_KEY, "2")


def _build_v2(settings: Settings) -> None:
    """一个**真实的** v2 库：血统齐全、绑定在、三张维护表里存着旧词。"""
    _write_inputs(settings)
    _write_rows(settings)
    _write_v2_aspect_rows(settings.database_path)
    _downgrade_to_v2(settings.database_path)


def _aspect_rows(db_path: Path) -> dict[str, list[str]]:
    """三张维护表里每一行的 ``aspect``，按面排。**只读那一列。**

    它是**旁证**：承重的那几条断言走真的读路径（``SqliteMaintenanceStore``），而直接查
    值能在读路径恰好把某些行藏起来时说出「那一行其实还在」。回执那张表没有 payload 列，
    所以这里只取 ``aspect`` —— 拿同一段代码去解三种不同的行形状，正是把「一张表的列」与
    「三张表一共有什么」混成一件事。
    """
    found: dict[str, list[str]] = {}
    with session(db_path) as conn:
        for table in ("observation", "change_signal", "maintenance_claim"):
            rows = conn.execute(f"SELECT aspect FROM {table} ORDER BY aspect").fetchall()
            found[table] = [str(row["aspect"]) for row in rows]
    return found


def _aspects_in_a_backup(backup: Path, table: str) -> list[str]:
    """备份里某一张表的 ``aspect`` 列 —— **只读且 ``immutable=1``**。

    与 :func:`_version_of_a_backup` 同一个理由（见那里的 docstring）：读一份备份不得在
    它旁边留下 ``-wal`` / ``-shm``。这里复用它，而不是另开一种读法 —— 两处读法不同的话，
    其中一处会悄悄把备份变旧，而「备份是迁移前那一刻的字节」是它存在的唯一理由。
    """
    conn = sqlite3.connect(f"file:{backup}?mode=ro&immutable=1", uri=True)
    try:
        rows = conn.execute(f"SELECT aspect FROM {table} ORDER BY aspect").fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


# --- 那道闸：一个 v2 库也不许被直接打开 -------------------------------------------


def test_the_store_refuses_a_v2_database_and_says_what_it_owes(settings: Settings) -> None:
    """v2 的库被这一版代码打开时**当场拒绝**，而且说得出它欠着哪一步。

    这条判据是 §15(c) 那一句的落地：启动检查必须直接告诉操作员「库是 v2、代码期望
    v3、去跑那条命令」，不能长成「工具坏了」。v2 缺的不是血统（那一格在），是**词表**
    —— 打开它读维护那三张表会在 ``Aspect("asset_fact")`` 上炸成一句 ValueError。
    """
    _build_v2(settings)

    with pytest.raises(StoreError) as excinfo:
        SqliteEvidenceStore(settings.database_path)

    message = str(excinfo.value)
    assert "版本是 2" in message and f"代码期望 {SCHEMA_VERSION}" in message
    assert "marketops.adapters.sqlite.migrate" in message
    # 「欠着什么」要具体到那些**存过的词**：四个旧词一个都不能少 —— 只说「词表变了」的
    # 消息帮不了运维判断这个库该不该迁。而**新**词不在这里列：那份对照是迁移的事，
    # 在报告里逐表给出；在这个消息里再抄一份，两份就会漂开。
    for name in V2_NAMES:
        assert name in message, message


# --- 换词表：改名字、拆两半、删除，各自都读得回来 -----------------------------------


def test_the_vocabulary_is_rewritten_and_every_row_is_readable_again(
    settings: Settings,
) -> None:
    """一个 v2 的库走完这一步之后，三张维护表里**每一行都读得出来**。

    断言走的是**真的读路径**（``SqliteMaintenanceStore``）而不是直接查库：迁移的目的
    不是把值改成好看的，是让这些行在这一版代码里可用。读路径只认 payload（列只当索引），
    所以「列对了、payload 没改」这种半成品在这里会当场露出来。
    """
    _build_v2(settings)

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert (report.from_version, report.to_version) == (2, SCHEMA_VERSION)
    assert report.already_current is False
    # 第一步没跑：这个库的血统是齐的，所以它没有回执可报。
    assert report.rows == {} and report.proof_counts == {}
    assert report.aspects is not None
    assert report.aspects.renamed == {
        "observation": 1,
        "change_signal": 1,
        "maintenance_claim": 1,
    }
    # 拆成两条的那一张：原行改成第一半，再插一行第二半 —— 所以是两行。
    assert report.aspects.split == {"change_signal": 2}
    # 被拆开又**没有对应的一格**的那两张：删掉，逐表数出来。
    assert report.aspects.dropped == {"observation": 2, "maintenance_claim": 1}

    # 备份就在库旁边，名字里写的是**这次从哪个版本起步**，而且逐字是迁移前那个东西。
    backup = report.backup
    assert backup is not None
    assert backup.name.startswith(f"{settings.database_path.name}.v2-")
    assert _version_of_a_backup(backup) == 2
    # 备份里那一批旧词**原样还在** —— 「删除」是可逆的，而这一句是它的凭据。读它用的是
    # immutable 那一手，所以它旁边不会多出 -wal / -shm。
    assert _aspects_in_a_backup(backup, "observation") == [
        "asset_fact",
        "role_email",
        "source_supply",
    ]
    assert list(backup.parent.glob(f"{backup.name}-*")) == []

    assert _version_of(settings.database_path) == SCHEMA_VERSION

    store = SqliteMaintenanceStore(settings.database_path)
    seen = store.observations(workspace=WORKSPACE)
    assert [item.aspect for item in seen] == [Aspect.SOURCE_COVERAGE]
    assert seen[0].observed_at.isoformat() == V2_OBSERVED_AT
    # 那一格**没变的东西**逐字没变：迁移动的是 aspect，不是它旁边的事实。
    assert seen[0].source_ref == "run-1"

    signals = store.signals(workspace=WORKSPACE)
    assert sorted(item.aspect.value for item in signals) == [
        "account_relation",
        "asset_attribute",
        "asset_identity",
    ]
    # 拆出来的两条**除 aspect 外逐字相同**：同一时刻、同一理由、同一主语。
    split = [item for item in signals if item.aspect is not Aspect.ACCOUNT_RELATION]
    assert {item.signalled_at for item in split} == {datetime.fromisoformat(V2_SIGNALLED_AT)}
    assert {item.reason for item in split} == {"staff page changed"}

    claims = store.claims(V2_SCENE)
    assert [item.aspect for item in claims] == [Aspect.SOURCE_COVERAGE]
    assert claims[0].state.value == "done"
    # 键**重算过**：同一个 (场景, 版本, 主语, 面, 周期) 在今天算出来必须是它自己那把。
    assert claims[0].dedupe_key == _v2_key("source_coverage")

    # 报告说得出删了几行 —— 一句只说「换名字多少行」的报告会让删除变成没人知道的事。
    rendered = report.render()
    assert "词表（v2 → v3）" in rendered and "删除" in rendered


def test_a_second_run_at_v3_reports_nothing_changed_and_keeps_one_backup(
    settings: Settings,
) -> None:
    """重跑是核对，不是再改一遍 —— 也不再产生第二份备份。

    「哪一份是迁移前」只能有一个答案。第二步与第一步共享这一条判据。
    """
    _build_v2(settings)
    first = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)
    after = _aspect_rows(settings.database_path)

    again = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert again.already_current is True
    assert again.backup is None
    assert again.aspects is None
    assert _aspect_rows(settings.database_path) == after
    assert list(settings.database_path.parent.glob("*.bak")) == [first.backup]


# --- 拒绝的两条：认不出来的词、与「不是它自己说的那个东西」的两行 -------------------


def test_an_aspect_word_nobody_knows_refuses_the_whole_run(settings: Settings) -> None:
    """一个**不在 v2 词表里**的词：整份拒绝、点名那一行、一个字都不改。

    判据与第一步的「证不出血统」同形，但**认的什么不同**：那一步要的是证明，这一步要的
    是「认得出来」。两者都不能猜 —— 猜出来的面会被读成一条事实（「这一格刚核过」）。
    """
    _build_v2(settings)
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute(
            "INSERT INTO observation (workspace, subject_kind, subject_id, aspect,"
            " observed_at, source_ref, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                WORKSPACE,
                "account",
                ROSTER_ACCOUNT,
                "hedgerow_condition",
                V2_OBSERVED_AT,
                "run-9",
                dump_payload(_observation_payload("hedgerow_condition")),
            ),
        )
    before = _aspect_rows(settings.database_path)

    with pytest.raises(StoreError) as excinfo:
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    message = str(excinfo.value)
    assert "认不出来" in message and "hedgerow_condition" in message
    # 点名的是**哪一行**：表 + 它在库里的地址（rowid）。
    assert "observation[rowid=" in message

    assert _version_of(settings.database_path) == 2
    assert _aspect_rows(settings.database_path) == before
    assert list(settings.database_path.parent.glob("*.bak")) == []


def test_a_column_that_disagrees_with_its_own_payload_refuses(settings: Settings) -> None:
    """列里一个面、payload 里另一个面 → 那一行不是它自己说的那个东西，整份拒绝。

    读路径**只认 payload**（列只当索引），所以一份不一致的行今天读得出来、却与它自己的
    索引列说着两件事。迁移不去猜哪一半是真的：它要改的正是这两处的**同一个**值。
    """
    _build_v2(settings)
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute(
            "UPDATE observation SET payload = ? WHERE aspect = ?",
            (dump_payload(_observation_payload("responsibility")), "source_supply"),
        )

    with pytest.raises(StoreError) as excinfo:
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert "不是它自己说的那个东西" in str(excinfo.value)
    assert _version_of(settings.database_path) == 2
    assert list(settings.database_path.parent.glob("*.bak")) == []


def test_a_claim_whose_key_does_not_match_its_own_columns_refuses(settings: Settings) -> None:
    """回执存着的键与它自己那几列算出来的不一致 → 整份拒绝。

    ``dedupe_key`` 是 ``sha256`` 摘要，所以验不了的那一行不可能是正常写出来的：它要么
    是手工造的，要么来自一个键算法不同的构建。两种情况都不知道该按哪一格去重 —— 而
    「重算一个」等于替它选一个格子，那正是它可能本来要指着的**别人**的格子。
    """
    _build_v2(settings)
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute(
            "UPDATE maintenance_claim SET dedupe_key = ? WHERE aspect = ?",
            ("0" * 32, "source_supply"),
        )

    with pytest.raises(StoreError) as excinfo:
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert "不是它自己说的那个格子" in str(excinfo.value)
    assert _version_of(settings.database_path) == 2
    assert list(settings.database_path.parent.glob("*.bak")) == []


# --- 一个 v1 的库：两步一起走 -------------------------------------------------------


def test_a_v1_database_walks_both_steps_in_one_run(settings: Settings) -> None:
    """v1 的库一次运行走完两步 —— 一次备份、一个事务、一个版本号。

    没有任何一个中间状态（血统补齐了、词表还没换）是有人想要的，所以这条命令不提供
    「只迁到 v2」。断言这一点的位置在这里：**版本是 3**，而备份是 **v1** 那份。
    """
    _build_v1(settings)
    _write_v2_aspect_rows(settings.database_path)

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert (report.from_version, report.to_version) == (1, SCHEMA_VERSION)
    assert report.already_current is False
    assert report.rows == V1_ROWS  # 第一步真的跑了
    assert report.aspects is not None and report.aspects.renamed == {
        "observation": 1,
        "change_signal": 1,
        "maintenance_claim": 1,
    }
    assert report.backup is not None
    assert report.backup.name.startswith(f"{settings.database_path.name}.v1-")
    assert _version_of_a_backup(report.backup) == 1

    # 两步的产物同时可用：血统读得出来，词表也读得出来。
    store = SqliteEvidenceStore(settings.database_path)
    assert store.list_evidence(ROSTER_ACCOUNT)[0].workspace == WORKSPACE
    assert [item.aspect for item in SqliteMaintenanceStore(settings.database_path).claims(V2_SCENE)] == [
        Aspect.SOURCE_COVERAGE
    ]


def test_every_older_version_has_its_own_reason_written_down() -> None:
    """每一个比代码旧的版本，都要有一句写下来的「它欠着什么」。

    ``ensure_schema`` 对旧版本走的是 :data:`_VERSION_NOTES` 逐版本查表；查不到就退成
    一句笼统的「版本旧了」。那句笼统的话本身没错，但它把「这个库是怎么来的、还值不值得
    迁」这个**运维要判断的东西**答没了 —— 而 `_VERSION_NOTES` 存在的全部理由就是答这一问。

    于是这里的断言不是「note 写得好不好」（那测不出来），而是**覆盖关系**：下次谁把
    ``SCHEMA_VERSION`` 从 3 推到 4 却忘了给 v3 写一句，转红的会是这一条，而不是某个
    用户手里那句读不出信息的拒绝消息。

    从模块里 import 一个下划线名是刻意的：这张表没有公开别名，而给测试专门加一个
    「公开」的转发等于让测试去测那个转发而不是这张表。诊断表就是这么用的 —— 与
    ``discovery_run._artifact_row`` 在文件顶部被直接 import 同一个道理。
    """
    from marketops.adapters.sqlite.db import _VERSION_NOTES

    assert set(_VERSION_NOTES) == set(range(1, SCHEMA_VERSION))


# --- §15(c)：写完之后的回读 --------------------------------------------------------
#
# 这一节钉的是那条命令的**第五步**：写完从盘上读回来，核「它真是我们打算留下的那个
# 库吗」。上面每一节问的都是「能不能迁」（预检）或「迁了没有」（报告），而这三句话
# 都不等于「盘上现在是那个样子」—— 中间隔着一次提交，以及三处按 ``rowid`` 落地的
# UPDATE（计划按 rowid 说「改这一行」，而 rowid 是这次运行看见的那个库里的地址）。
#
# 五格各有一条用例，而**每一格都是把库改坏之后跑回读**，不是把预检再跑一遍：这些
# 形状没有任何写路径能产出（今天的代码写不出一个版本格是 v2 的 v3 库），所以它们只能
# 手工造 —— 与这一节上面那两节手工造旧词行是同一个理由。
#
# 从模块里 import 两个下划线名是刻意的（同 ``_VERSION_NOTES`` 那条）：回读的那一步没有
# 公开入口，给它专门加一个转发等于测那个转发。``_StampedRow`` 是回读要看的**计划**的
# 形状，测试要么造一份它、要么把整条命令重跑一遍 —— 前者小得多。
from marketops.adapters.sqlite.migrate import (  # noqa: E402
    OBSERVED_FROM_FIRST_SEEN,
    OBSERVED_FROM_STORED_AT,
    _declared_index_names,
    _StampedRow,
    _verify_migrated,
)


def _migrate_v1(settings: Settings):
    """把一个 v1 的库真迁到这一版，返回报告（回读已经在这一步里跑过了）。"""
    _build_v1(settings)
    return migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)


def _migrate_v2(settings: Settings):
    """把一个 v2 的库真迁到这一版（只走第二步），返回报告。"""
    _build_v2(settings)
    return migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)


def _break(db_path: Path, sql: str, params: tuple[object, ...]) -> None:
    """把迁移**之后**的库改坏一处。手工直改 —— 见这一节开头那句。"""
    with session(db_path) as conn, write_transaction(conn):
        conn.execute(sql, params)


def _readback(settings: Settings, report: MigrationReport, **fields: object) -> None:
    """按**这一次运行自己留下的计划**回读。

    回读要核的那三样（版本、绑定、旧词表、逐行的章、重建过的表）里，后两样核的是
    **这次写库用的那份计划**：重建过没有、哪些行该盖什么章。所以这里把 ``report``
    自己的 ``revisions`` 与 ``backup`` 递进去，而不是让每个用例各造一份 —— 一份手工
    造的计划核出来的是「我造的那份对不对」，与盘上那个库无关（``stamped`` 那几格
    是刻意的例外：它们造的正是「计划说改了、盘上没改」这个形状，而那正是要测的）。

    两次断言是**前提**而不是判据：``revisions`` 是 ``None`` 表示这一次根本没走第三步，
    ``backup`` 是 ``None`` 表示它没写过库 —— 那种情况下这条回读没有可核的东西，
    而它会以 ``TypeError`` / 一句空消息的形式失败得看不出原因。
    """
    assert report.revisions is not None, "前提：这一次真的走了第三步"
    assert report.routes is not None, "前提：这一次真的走了第四步"
    assert report.jobs is not None, "前提：这一次真的走了第五步"
    assert report.refs is not None, "前提：这一次真的走了第六步"
    assert report.backup is not None, "前提：这一次真的写过库"
    _verify_migrated(
        settings.database_path,
        profile=WORKSPACE,
        rebuild=report.revisions,
        split=report.routes,
        jobs=report.jobs,
        refs=report.refs,
        backup=report.backup,
        **fields,  # type: ignore[arg-type]
    )


def test_a_migrated_database_is_read_back_and_the_readings_ride_in_the_report(
    settings: Settings,
) -> None:
    """迁完之后那一行**读数**：每一格带数字，且它进 ``report.render()``。

    读数由回读那一步自己算出来（数了哪些格、各多少行），不是另写的一句声明 —— 一份
    没核对过的回执与一份核对过且一致的回执必须是两份读得出差别的东西（§15(b) 那一课）。
    """
    report = _migrate_v1(settings)

    assert report.verified.startswith("回读核对")
    assert f"版本 {SCHEMA_VERSION}" in report.verified
    assert "工作区绑定" in report.verified
    for table, count in V1_ROWS.items():
        assert f"{table} {count} 行" in report.verified, (
            f"{table} 的盖章行数不在读数里 —— 那一格数字是这一步自己数出来的，"
            "少了它这份读数就少一格"
        )
    assert "旧词表残留 0 处" in report.verified
    assert "回读核对" in report.render()
    assert report.verified in report.render()


def test_the_readback_refuses_a_version_that_is_not_the_one_that_was_written(
    settings: Settings,
) -> None:
    """版本那一格不是写下去的那个值 ⇒ 拒绝。

    预检与 ``ensure_schema`` 读的就是这一格，所以一个版本格没落地的库会让**下一个
    进程**把一次成功的迁移读成「还没迁」—— 而它读不出别的东西。
    """
    report = _migrate_v1(settings)
    assert report.backup is not None
    _break(
        settings.database_path,
        "UPDATE schema_meta SET value = ? WHERE key = ?",
        ("2", SCHEMA_VERSION_KEY),
    )

    with pytest.raises(StoreError) as excinfo:
        _readback(settings, report, stamped=None)

    message = str(excinfo.value)
    assert "版本那一格是 '2'" in message
    assert str(report.backup) in message, "回滚要指得出备份在哪"
    assert "回滚" in message


def test_the_readback_refuses_a_binding_that_belongs_to_someone_else(
    settings: Settings,
) -> None:
    """绑定被写成别人的 Scene ⇒ 拒绝。

    这一格是「这个库属于谁」的唯一一句机器可读的话。它错了之后，下一步是这个库的
    产出被当成**自己**的读进来。

    ``stamped`` 传一个**空的计划**而不是 ``None``：``None`` 表示「这一次没走第一步」，
    而绑定恰恰只在走了第一步时才是这次写下去的 —— 空计划是「走了第一步、一行都没盖」
    这个形状里最小的那个，绑定那一格照样被核。
    """
    report = _migrate_v1(settings)
    assert report.backup is not None
    _break(
        settings.database_path,
        "UPDATE schema_meta SET value = ? WHERE key = ?",
        ("someone-elses-scene", WORKSPACE_META_KEY),
    )

    with pytest.raises(StoreError, match="工作区绑定读回来是 'someone-elses-scene'"):
        _readback(settings, report, stamped={})


def test_the_readback_refuses_a_row_whose_stamp_did_not_land(settings: Settings) -> None:
    """计划里要盖章的那一行，章没上去 ⇒ 拒绝，并点名是哪一行。

    这是 ``_apply_db`` 唯一一次被人**读回来**核对：否则它对这条命令的贡献只有「计划
    是对的」，而计划对了、写没写上是两件事。
    """
    report = _migrate_v1(settings)
    assert report.backup is not None
    key = _rows_of(settings.database_path)["evidence"][0]["key"]
    with session(settings.database_path) as conn:
        raw = conn.execute(
            "SELECT payload FROM evidence WHERE evidence_id = ?", (key,)
        ).fetchone()["payload"]
    payload = load_payload(raw, key)
    assert payload.pop("workspace") == WORKSPACE, "前提：这一行本来盖着章"
    _break(
        settings.database_path,
        "UPDATE evidence SET payload = ? WHERE evidence_id = ?",
        (dump_payload(payload), key),
    )

    with pytest.raises(StoreError) as excinfo:
        _readback(
            settings,
            report,
            stamped={"evidence": [_StampedRow("evidence", str(key), WORKSPACE, "roster", {})]},
        )

    assert f"evidence[{key}]：章读回来是 None" in str(excinfo.value)


def test_the_readback_refuses_an_old_vocabulary_word_that_survived(settings: Settings) -> None:
    """三张表里还留着一个 v2 的词 ⇒ 拒绝，并说清是哪张表、哪个词、几行。

    换名字的没改全、``drop`` 的没删掉，都长这样。这一格在预检里看不出来 —— 预检读的
    是**迁移前**的词表，而这里读的是迁移后的。"""
    report = _migrate_v1(settings)
    assert report.backup is not None
    _break(
        settings.database_path,
        "INSERT INTO observation (workspace, subject_kind, subject_id, aspect,"
        " observed_at, source_ref, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            WORKSPACE,
            "account",
            ROSTER_ACCOUNT,
            "source_supply",
            V2_OBSERVED_AT,
            "run-1",
            dump_payload(_observation_payload("source_supply")),
        ),
    )

    with pytest.raises(StoreError) as excinfo:
        _readback(settings, report, stamped=None)

    message = str(excinfo.value)
    assert "observation：v2 的词还在（source_supply 1 行）" in message


def test_the_readback_refuses_a_receipt_key_that_no_longer_matches_its_columns(
    settings: Settings,
) -> None:
    """回执的键与它自己那几列算出来的不一致 ⇒ 拒绝。

    ``due_dedupe_key`` 是那一列的唯一算法，而这次写入改过它。混行（键来自另一行）
    在这里是最后一处能被拦住的地方 —— 拦不住的话，下一次复核会因为一个不属于它的
    键而**不**发生。
    """
    report = _migrate_v2(settings)
    assert report.backup is not None
    _break(
        settings.database_path,
        "UPDATE maintenance_claim SET dedupe_key = ? WHERE rowid = 1",
        ("0" * 64,),
    )

    with pytest.raises(StoreError, match="maintenance_claim\\[rowid=1\\]：存着的键"):
        _readback(settings, report, stamped=None)


def test_a_v2_only_run_does_not_claim_to_have_checked_stamps(settings: Settings) -> None:
    """只走第二步的那一次，读数里**不许**出现「逐行的章」与「工作区绑定」。

    这两格在那一次运行里没有被写（库本来就是 v2），所以回读它们核对的还是迁移前那个
    读数。一句「都核过了」把没核的格也算进去，正是这份读数存在的反面。
    """
    report = _migrate_v2(settings)

    assert report.already_current is False
    assert report.verified.startswith("回读核对")
    assert "逐行的章" not in report.verified
    assert "工作区绑定" not in report.verified
    assert "旧词表残留 0 处" in report.verified, "但其他几格照样核了"


def test_a_run_that_writes_nothing_says_so_instead_of_leaving_the_readback_blank(
    settings: Settings,
) -> None:
    """已经是这一版的那一次：``verified`` **不空着**，而是明说「没有可回读的东西」。

    留白读起来像「也核过、没问题」，而这一次根本没写库。
    """
    first = _migrate_v2(settings)
    assert first.already_current is False

    again = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert again.already_current is True
    assert "没有可回读的东西" in again.verified
    assert "没有可回读的东西" in again.render()


# --- 第三块形状：三张配置表从「一个 id 一行」到「一个 id 多行」（§7） ------------------
#
# 这一节的输入同样**只能手工造**（今天没有任何写路径会产出 v3 的那三张表），但它与
# v2 那一段有一处不同：v2 → v3 之间的差别是**词**（同一列的取值换了名字，所以
# ``_downgrade_to_v2`` 只改版本那一格），v3 → v4 之间的差别是**列**（主键从 id 一格
# 变成 (id, 版) 两格）。所以下调不能只改版本那一格 —— 得把那三张表**真的建回旧形状**。
#
# 为什么非改形状不可，见 ``db._VERSION_NOTES[3]``：v3 的形状一个 id 装得下一行，
# 于是「发第二版」就是覆盖第一版，而一条已经生成的草稿记的正是那个 id。

#: v3 那三张表**除 payload 外**的列，按建表语句里的顺序。抄的是「哪几列、谁是主键」，
#: 不是那三句语句的排版 —— 主键写成列级约束还是表级约束在 SQLite 里是同一个形状
#: （``PRAGMA table_info`` 报的是同一格），而这里要造的是形状，不是那份源码。
_V3_CONFIG_COLUMNS: dict[str, tuple[str, ...]] = {
    "outreach_family": ("family_id", "stage"),
    "outreach_writing_policy": ("policy_id",),
    "outreach_claim": ("claim_id",),
}

#: 夹具那条说法的批准时刻。**与 ``FETCHED_AT`` 分开**：拿取证的时刻当批准的时刻，
#: 会让这一行在报告里说一句它没说过的话。
CLAIM_APPROVED_AT = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)


def _write_config_rows(settings: Settings) -> None:
    """三张配置表各写一行，**全部走真的写路径**。

    正文里那句说法逐字写着「测试夹具」：它不属于任何一个真实产品（§7 逐字禁止把
    编出来的 AcmeCorp 文案当成已批准内容），而这一行要证明的只是「它搬得过去」。
    一条读起来像真实批准文案的夹具，会被下一个读代码的人当成一条真说法。
    """
    store = SqliteOutreachStore(settings.database_path)
    store.put_writing_policy(
        WritingPolicy(policy_id="policy-fixture-stage-1", revision=1)
    )
    store.put_family(
        TemplateFamily(
            family_id="family-fixture-stage-1",
            revision=1,
            stage=OutreachStage.STAGE_1,
            role_class="grounds_manager",
            locale="en-GB",
            product="fixture-product",
            writing_policy_id="policy-fixture-stage-1",
        )
    )
    store.put_claim(
        ApprovedClaim(
            claim_id="claim-fixture",
            revision=1,
            text="（测试夹具）这条说法不属于任何真实产品 —— 它只用来证明这一行搬得过去。",
            source_ref="test-fixture-not-an-approval",
            approved_by="operator:test-fixture",
            approved_at=CLAIM_APPROVED_AT,
        )
    )


def _downgrade_config_tables_to_v3(db_path: Path) -> None:
    """把三张配置表建回 **v3 的形状**，并把版本写回 3。

    payload 里那一格也去掉 —— 那正是 v3 的写入路径会写下的字节。留下来的行**逐字是
    走真的写路径写进去的**（真的 ``stored_at``、真的序列化格式），手工拼几行 JSON
    做不到这一点：那样测的是「迁移会不会数行」，而这里要问的是「迁移会不会读一个
    真的库」。

    一个 id 下有第二版时**当场拒绝**，而不是只搬第一版：v3 的形状确实装不下第二版，
    而一句「装不下」若落成一次静默丢弃，这个夹具就会让「行数对不上」这一类失败
    在这里先被自己掩盖掉。

    路线那一侧同时搬回 v4 的形状（``_downgrade_routes_to_v4``）：一个 v3 的库里
    ``contact_route`` 当然也还是旧的形状，而这一步要造的是**整个库**在那个版本上的
    样子，不是只有那三张配置表。
    """
    _downgrade_routes_to_v4(db_path)
    with session(db_path) as conn, write_transaction(conn):
        for name, columns in _V3_CONFIG_COLUMNS.items():
            key = CONFIG_KEY_COLUMNS[name]
            present = conn.execute(
                f"SELECT {', '.join(columns)}, payload FROM {name} ORDER BY {key}"
            ).fetchall()
            conn.execute(f"DROP TABLE {name}")
            conn.execute(
                f"CREATE TABLE {name} ("
                + ", ".join(f"{column} TEXT" for column in columns)
                + f", payload TEXT NOT NULL, PRIMARY KEY ({key}))"
            )
            placeholders = ", ".join("?" for _ in range(len(columns) + 1))
            for row in present:
                payload = load_payload(row["payload"], f"{name}[{row[key]}]")
                assert payload.get("revision") == 1, (
                    f"{name}[{row[key]}] 的 payload 里是第 {payload.get('revision')!r} 版，"
                    "而 v3 的形状装不下第二版 —— 这份夹具只放得下第一版"
                )
                payload.pop("revision")
                conn.execute(
                    f"INSERT INTO {name} ({', '.join(columns)}, payload)"
                    f" VALUES ({placeholders})",
                    (*[row[column] for column in columns], dump_payload(payload)),
                )
        set_meta(conn, SCHEMA_VERSION_KEY, "3")


def _build_v3(settings: Settings) -> None:
    """一个**真实的** v3 库：血统齐全、词表是新的，只有那三张配置表还是旧形状。"""
    _write_inputs(settings)
    _write_rows(settings)
    _write_config_rows(settings)
    _downgrade_config_tables_to_v3(settings.database_path)


def _config_table_shape(db_path: Path, table: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """那张表的 ``(列, 主键列)``，按建表顺序。``PRAGMA`` 报的就是它自己。"""
    with session(db_path) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return (
        tuple(str(row["name"]) for row in rows),
        tuple(str(row["name"]) for row in sorted(rows, key=lambda row: row["pk"]) if row["pk"]),
    )


def _config_rows(db_path: Path, table: str) -> list[dict]:
    """那张表的每一行（原样解出来的 payload），**不经过读路径**。

    读路径只回最新一版（那是对的：调用方要的是「这一族现在是什么」）。而「旧版还在
    不在」这个问题读路径答不了 —— 拿一个只回最新的接口去证明「旧版还在」，会得到一句
    两个方向都读得通的结论。
    """
    with session(db_path) as conn:
        found = conn.execute(
            f"SELECT revision AS rev, payload FROM {table} ORDER BY revision"
        ).fetchall()
    return [
        {"revision": int(row["rev"]), **load_payload(row["payload"], table)} for row in found
    ]


def test_a_v3_database_keeps_every_configuration_row_and_gains_a_version_column(
    settings: Settings,
) -> None:
    """一个 v3 的库走完第三步之后：每一行都在、读得回来、形状是新的。

    三句断言各自回答一件不同的事，而且**缺一句这件事就没被证明**：

    * 行还在 —— 重建是 ``改名 → 建回来 → 搬行 → 删旧的``，少搬一步就丢行，而丢了行
      之后紧接着的那次写入照样成功（直到有人去查一族模板）；
    * 走**真的读路径**读得回来且 ``revision == 1`` —— 只查那一列证明不了 payload 被
      补过，而读路径只认 payload；
    * 形状真的是 ``(id, revision)`` 主键 —— 一句「重建过了」在报告里与「重建成了别的
      形状」长得一样。这一格还有一条**承重**的旁证：迁完之后同一个 id 能写下第二版。
    """
    _build_v3(settings)
    before = _config_table_shape(settings.database_path, "outreach_family")
    assert before[1] == ("family_id",), "前提：这个库的那三张表还是 v3 的形状"

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert (report.from_version, report.to_version) == (3, SCHEMA_VERSION)
    assert report.revisions is not None
    assert report.revisions.rebuilt == (
        "outreach_family",
        "outreach_writing_policy",
        "outreach_claim",
    )
    assert report.revisions.moved == {
        "outreach_family": 1,
        "outreach_writing_policy": 1,
        "outreach_claim": 1,
    }
    # 报告说得出这件事，而且**带数字** —— 一句只写「重建过了」的报告在丢行的时候
    # 与一行没丢的时候长得一样。
    rendered = report.render()
    assert "配置表的主键（v3 → v4）" in rendered
    assert "outreach_family 1 行" in rendered

    store = SqliteOutreachStore(settings.database_path)
    family = store.families()
    assert len(family) == 1
    assert family[0].family_id == "family-fixture-stage-1"
    assert family[0].revision == 1
    # 那一格**没变的东西**逐字没变：迁移动的是形状，不是它旁边那几格配置。
    assert family[0].locale == "en-GB" and family[0].product == "fixture-product"
    assert family[0].stage is OutreachStage.STAGE_1

    policy = store.writing_policies()
    assert [(item.policy_id, item.revision) for item in policy] == [
        ("policy-fixture-stage-1", 1)
    ]
    claim = store.approved_claims()
    assert [(item.claim_id, item.revision) for item in claim] == [("claim-fixture", 1)]
    assert claim[0].approved_at == CLAIM_APPROVED_AT
    assert claim[0].approved_by == "operator:test-fixture"

    after = _config_table_shape(settings.database_path, "outreach_family")
    assert after == (
        ("family_id", "revision", "stage", "payload"),
        ("family_id", "revision"),
    )
    # 承重的那一句：**同一族能写下第二版了**。这一格正是 v3 的形状给不出的东西，
    # 而它是「改话术要发下一版」这条纪律在盘上的前提。
    second = TemplateFamily(
        family_id="family-fixture-stage-1",
        revision=2,
        stage=OutreachStage.STAGE_1,
        role_class="grounds_manager",
        locale="en-GB",
        product="fixture-product",
        writing_policy_id="policy-fixture-stage-1",
        note="第二版：改一句措辞",
    )
    store.put_family(second)
    assert [item.revision for item in store.families()] == [2], (
        "读路径只回**最新**一版，而写路径把两版都留着"
    )
    assert len(_config_rows(settings.database_path, "outreach_family")) == 2, (
        "第一版仍然在盘上 —— 否则「历史修订不可变」只是一句话"
    )


def test_a_v3_row_that_cannot_be_read_as_revision_one_refuses_the_whole_run(
    settings: Settings,
) -> None:
    """一行搬过去读不出来 ⇒ **整份拒绝**，一个字都不改，并点名是哪一行。

    ``_plan_revisions`` 的第二条判据：``payload`` 里已经带着 ``revision`` 那一格，而
    这张表还没有那一列。这种行只可能来自手工改过的库，或者一个用别的模型写过的部署
    —— 而它读出来会是一句「这一版的内容与它自己的版号说着两件事」。搬到新形状里之后
    这个矛盾**反而消失了**（列有了，值还是那个），所以这里是最后一处拦得住它的地方。
    """
    _build_v3(settings)
    _break(
        settings.database_path,
        "UPDATE outreach_claim SET payload = ? WHERE claim_id = ?",
        (
            dump_payload({**_claim_payload(settings.database_path), "revision": 2}),
            "claim-fixture",
        ),
    )

    with pytest.raises(StoreError) as excinfo:
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    message = str(excinfo.value)
    assert "claim-fixture" in message
    assert "还没有 ``revision`` 那一列" in message
    assert "一行都没写" in message
    # 一个字节都没改：版本还是 3，形状还是旧的。
    assert _version_of(settings.database_path) == 3
    assert _config_table_shape(settings.database_path, "outreach_claim")[1] == ("claim_id",)


def _claim_payload(db_path: Path) -> dict:
    """``outreach_claim`` 那一行 payload 现读出来的那几格（这一行是 v3 形状的）。

    从盘上现读，而不是在这里再拼一份：这一行是 ``_write_config_rows`` 走真的写路径
    写下的，抄一份在这里的话，两条路会在某次改了字段之后分家 —— 而那时这一条用例
    验的是我抄的那一份。
    """
    with session(db_path) as conn:
        row = conn.execute(
            "SELECT payload FROM outreach_claim WHERE claim_id = ?", ("claim-fixture",)
        ).fetchone()
    return load_payload(row["payload"], "outreach_claim[claim-fixture]")


# --- 第四块形状：``contact_route`` 从事件流拆成两层（§6 / B11） ----------------------
#
# 前三块改的是**词**（v2 → v3）与**列**（v3 → v4）。这一块改的是**这张表是什么**：
# v4 的 ``contact_route`` 是一份追加日志，一行一次写入，主键是插入序号；v5 是两层 ——
# ``route_observation`` 是不可变历史（一行一次观察），``contact_route`` 是当前投影
# （一个规范键一行）。所以下调不是改几列，而是把那三张表 DROP 掉再建回旧形状
# （``_downgrade_routes_to_v4``）。
#
# 搬回去的内容是**真的**：旧表里的一行本来**就是**一次观察 —— 那正是 v5 要认领的
# 那件事。而旧库里救不回来的是**顺序**：``seq`` 是插入顺序，折叠的「谁更新」按
# ``(observed_at, observation_id)`` 判。所以那一步按 ``seq`` 遍历、按那个顺序折叠，
# 而得到的当前读法与遍历顺序无关（见 ``_plan_routes`` 的 docstring）。

#: 夹具那个 v4 库里该有的旧行数 —— 一个账号一行，是**事件流**的样子。
V4_ROUTE_ROWS = 2


def _downgrade_routes_and_set_v4(db_path: Path) -> dict[str, str]:
    """把路线那几张表搬回 v4 的形状、版本写回 4，返回**拆之前**的每一条观察。

    返回值是 ``观察 id → payload 原文``，它是好几条判据的输入：v4 → v5 那一步从头重算
    ``observation_id`` 与 payload，而它算出来的必须与写路径当时落下的**逐字节相同**
    —— 不同就说明那一步挑的 ``observed_at`` 不是「我们第一次看到这条路线所依据的材料」
    那一刻，而报告里「迁了 N 条观察」这句话照样成立，没有人看得出来。

    版本那一格在这里手工写到 4：``_downgrade_routes_to_v4`` 只动那三张路线表（另两步
    的调用方各自负责自己的版本格），而一个版本仍写着 5、形状却是 v4 的库是**第三种**
    形状 —— 预检会以「既不认得出 v5、也不认得出 v4」把它拒掉，那与这里要测的东西无关。
    """
    with session(db_path) as conn:
        before = {
            str(row["observation_id"]): str(row["payload"])
            for row in conn.execute("SELECT observation_id, payload FROM route_observation")
        }
    _downgrade_routes_to_v4(db_path)
    _break(
        db_path,
        "UPDATE schema_meta SET value = ? WHERE key = ?",
        ("4", SCHEMA_VERSION_KEY),
    )
    return before


def _build_v4(
    settings: Settings, *, extra: tuple[tuple[ContactRoute, datetime], ...] = ()
) -> dict[str, str]:
    """一个**真实的** v4 库，返回它拆之前那几条观察。

    ``extra`` 是在同一台写路径上再记几条观察（同一个账号可以有第二条路线、或者同一条
    路线被看到第二次）—— 折叠与「两行是同一条观察」这两件事都只能这样造出来。
    """
    _write_inputs(settings)
    _write_rows(settings)
    store = SqliteEvidenceStore(settings.database_path)
    for route, observed_at in extra:
        _record_route(store, route, observed_at=observed_at)
    return _downgrade_routes_and_set_v4(settings.database_path)


def _legacy_rows(db_path: Path, *, account_id: str) -> list[tuple[int, dict, str]]:
    """v4 那张 ``contact_route`` 里属于某个账号的每一行，按 ``seq``：``(seq, payload, stored_at)``。

    从盘上现读，而不是在这里再拼一份：这些行走过真的写路径（真的 ``stored_at``、真的
    序列化格式），抄一份在这里的话，两条路会在某次改了字段之后分家 —— 而那时这条用例
    验的是我抄的那一份。

    按 ``seq`` 排是**承重**的：旧形状里同一个规范键可以有多行（一个账号被看过两次就是
    两行），而 ``WHERE account_id = ?`` 不加排序时返回哪一行由 SQLite 决定。第一版这里
    就踩了这个坑 —— 一条取「同一个账号那一行」的用例取到了另一行，报出来的是
    「前提：取到的就是最新那一条」失败，看着像夹具造错了，其实是用例自己没说要哪一行。
    """
    with session(db_path) as conn:
        rows = conn.execute(
            "SELECT seq, payload, stored_at FROM contact_route WHERE account_id = ?"
            " ORDER BY seq",
            (account_id,),
        ).fetchall()
    return [
        (
            int(row["seq"]),
            load_payload(row["payload"], f"contact_route[{account_id}]"),
            str(row["stored_at"]),
        )
        for row in rows
    ]


def _legacy_row(db_path: Path, *, account_id: str) -> tuple[int, dict, str]:
    """那个账号在旧表里**唯一**的那一行。多于一行时当场断言失败 —— 见上一条。"""
    rows = _legacy_rows(db_path, account_id=account_id)
    assert len(rows) == 1, f"前提：{account_id} 在 v4 的路线表里恰好一行，实际 {len(rows)} 行"
    return rows[0]


def _indexes_on(db_path: Path, table: str) -> set[str]:
    """那张表上挂着的索引名。``PRAGMA`` 报的就是它自己。"""
    with session(db_path) as conn:
        rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return {str(row["name"]) for row in rows}


def test_a_v4_database_is_split_into_history_and_the_current_reading(settings: Settings) -> None:
    """一个 v4 的库走完第四步之后：旧行成了历史、当前读法一行一个键、而**读数没变**。

    四句断言各自回答一件不同的事，缺一句这件事就没被证明：

    * 旧行数、重复数、观察数、投影数**四个数**都在报告里且对得上 —— 「重建过了」这句
      话在丢行的时候与一行没丢的时候长得一样；
    * ``observation_id`` 与 payload 与写路径当时落下的**逐字节相同** —— v4 里
      ``observed_at`` 只剩两处可查，选错那一档时这一条会红，而报告照样说「迁成功」；
    * 拆完之后这条路线**读出来是什么**与拆之前逐字段相同 —— 迁移动的是这张表的形状，
      不是那一行说的那条路线；
    * 旧表与它的索引真的不在了 —— 索引占着名字的话，新表那句
      ``CREATE INDEX IF NOT EXISTS`` 会被**静默跳过**，而表照样建得出来。
    """
    _write_inputs(settings)
    _write_rows(settings)
    before_reading = SqliteEvidenceStore(settings.database_path).list_routes(ROSTER_ACCOUNT)[0]
    before = _downgrade_routes_and_set_v4(settings.database_path)
    assert len(before) == V4_ROUTE_ROWS

    assert _config_table_shape(settings.database_path, "contact_route") == (
        ("seq", "account_id", "payload", "stored_at"),
        ("seq",),
    ), "前提：这个库的路线表还是 v4 的形状（一行一次写入）"
    assert _config_table_shape(settings.database_path, "route_observation") == ((), ()), (
        "前提：v4 里没有历史那张表"
    )

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert (report.from_version, report.to_version) == (4, SCHEMA_VERSION)
    assert report.routes is not None
    assert report.routes.rebuilt is True
    assert report.routes.legacy_rows == V4_ROUTE_ROWS
    assert report.routes.repeated == 0
    assert report.routes.observed_from == {OBSERVED_FROM_FIRST_SEEN: V4_ROUTE_ROWS}
    assert {
        row["observation_id"]: row["payload"] for row in report.routes.observations
    } == before, "计划里的观察必须与拆之前那条写路径落下的逐字节相同"
    assert len(report.routes.projections) == V4_ROUTE_ROWS

    rendered = report.render()
    assert "路线表的拆表（v4 → v5）" in rendered
    assert f"的 {V4_ROUTE_ROWS} 行搬成" in rendered
    assert f"的 {V4_ROUTE_ROWS} 条观察" in rendered
    assert f"折成 {V4_ROUTE_ROWS} 行当前读法" in rendered
    assert "同一条" not in rendered, "这个库里没有重复行，报告就不该提合并"

    # 形状真的是两层了。一句「重建过了」与「重建成了别的形状」在报告里长得一样。
    assert _config_table_shape(settings.database_path, "contact_route") == (
        ("canonical_key", "account_id", "workspace", "payload", "stored_at"),
        ("canonical_key",),
    )
    assert _config_table_shape(settings.database_path, "route_observation") == (
        (
            "observation_id",
            "canonical_key",
            "account_id",
            "workspace",
            "observed_at",
            "payload",
            "stored_at",
        ),
        ("observation_id",),
    )
    # 旧表搬走了、删掉了，而它的索引**没有**留在盘上占名字。
    with session(settings.database_path) as conn:
        parked = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("_pre_v5_contact_route",),
        ).fetchone()
    assert parked is None, "搬开的旧表还在盘上"
    # 隐式索引（``TEXT PRIMARY KEY`` 自己那一个）不算：它由 SQLite 生成、名字不可选，
    # 而去掉它之后剩下的必须**恰好**是建表语句声明的那些 —— 少一个说明索引没建成
    # （搬开的旧索引还占着名字，那句 ``CREATE INDEX IF NOT EXISTS`` 被静默跳过了），
    # 多一个说明盘上还挂着一个这份 DDL 不认识的索引。
    explicit = {
        name
        for name in _indexes_on(settings.database_path, "contact_route")
        if not name.startswith("sqlite_autoindex_")
    }
    assert explicit == set(_declared_index_names(ROUTE_TABLE_BY_NAME["contact_route"]))

    # 走**真的读路径**读回来：形状对了不等于读得出来，而读路径只认 payload。
    after = SqliteEvidenceStore(settings.database_path)
    readings = after.list_routes(ROSTER_ACCOUNT)
    assert len(readings) == 1
    assert readings[0] == before_reading, "拆表改动了这条路线读出来是什么"
    history = after.list_route_observations(ROSTER_ACCOUNT)
    assert [item.observed_at for item in history] == [FETCHED_AT]
    assert history[0].observation_id in readings[0].observation_ids
    assert readings[0].updated_at == readings[0].last_observed_at, (
        "投影的最后改写时刻是**那条观察的**观察时刻，不是折叠那一刻的墙钟"
    )
    # 拒收那张表在 v4 里根本不存在 —— 读它一次是「这一步真的把它建出来了」的最小证据。
    assert after.list_route_rejections(ROSTER_ACCOUNT) == []


def test_two_readings_of_one_route_stay_two_observations_and_one_projection(
    settings: Settings,
) -> None:
    """同一个键下的两条观察：历史里两条、投影里一行，而当前读法是**新**的那一条。

    这正是拆表要做的事，也是它**有损**的地方。三条断言各挡一件事：

    * 历史条数不缩水 —— 丢了的话「这个账号被看过两次」这句话就没有了；
    * 投影只有一行 —— 旧表的形状让同一个键可以有多行，而 ``list_routes`` 按账号读，
      多行会让同一条路线在报告里出现两次；
    * 当前读法是最新那一条（``role_title`` 换过，所以这两条读得出区别）—— 按 ``seq``
      顺序「最后一条赢」在旧库里近似成立，而 v5 的判据是
      ``(observed_at, observation_id)``；下面这个夹具把 ``seq`` 的顺序与观察时刻的
      顺序**故意调反**，所以这一条真的在测折叠，而不是在测遍历顺序。
    """
    later = FETCHED_AT + timedelta(days=3)
    _build_v4(
        settings,
        extra=(
            (
                ContactRoute(
                    workspace=WORKSPACE,
                    account_id=ROSTER_ACCOUNT,
                    email="office@example.co.uk",
                    role_title="Head Gardener",
                    first_seen_at=FETCHED_AT,
                ),
                later,
            ),
        ),
    )
    rows = _legacy_rows(settings.database_path, account_id=ROSTER_ACCOUNT)
    assert len(rows) == 2, "前提：这个账号在旧表里是两行"
    older, newer = rows  # 下调按 ``(observed_at, observation_id)`` 排出来，所以新的在后
    assert older[1].get("role_title") == ""
    assert newer[1].get("role_title") == "Head Gardener"
    # 把两条的 ``seq`` 对调 —— 于是「按插入顺序最后一条赢」会得出**旧**那一条，而折叠
    # 必须按观察时刻挑出新的那一条。先映射到负数再落位：``seq`` 是主键，直接对调会在
    # 语句执行到一半时撞上唯一约束。
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute(
            "UPDATE contact_route SET seq = -seq WHERE account_id = ?", (ROSTER_ACCOUNT,)
        )
        for target, source in ((older[0], newer[0]), (newer[0], older[0])):
            conn.execute(
                "UPDATE contact_route SET seq = ? WHERE seq = ?", (target, -source)
            )

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert report.routes is not None
    assert report.routes.legacy_rows == V4_ROUTE_ROWS + 1
    assert report.routes.repeated == 0
    assert len(report.routes.projections) == V4_ROUTE_ROWS

    store = SqliteEvidenceStore(settings.database_path)
    readings = store.list_routes(ROSTER_ACCOUNT)
    assert len(readings) == 1
    assert readings[0].route.role_title == "Head Gardener", (
        "投影取的是观察时刻最新那一条，不是插入顺序最后那一条"
    )
    assert readings[0].revision == 2
    assert len(readings[0].observation_ids) == 2
    assert readings[0].previous_grade is Grade.X, "被换掉的那一个要留在账上"
    assert len(store.list_route_observations(ROSTER_ACCOUNT)) == 2
    # 正控：另一个账号那条路线与上面这些改动无关，它还是一行一次观察。
    assert len(store.list_route_observations(GARDEN_ID)) == 1
    assert store.list_routes(GARDEN_ID)[0].revision == 1


def test_two_identical_legacy_rows_are_one_observation_and_the_report_says_so(
    settings: Settings,
) -> None:
    """旧表里两行逐字相同 ⇒ 并成一条观察，而且**数出来**写进报告。

    这不是边角料：同一个账号被反复绑定而路线一次都没变过时，旧库里那两行就是逐字
    相同的（旧形状一行是一次**写入**）。而新形状里观察的身份**由内容定** ——
    ``observation_id_for`` 的输入里没有 ``seq`` —— 于是那两行在今天的模型里就是同一条
    观察，``record_route`` 对同一条观察重放的回答也正是「什么都不做」。

    不并的后果是一个**主键撞车**的 ``sqlite3`` 错误，包在「写入这一步失败了」里 ——
    那句话说的是磁盘或约束，读起来像运维问题，而真原因是这一步自己重了两条。并了而
    不说，则是把一次合并藏进一次对账里（§19）。所以两条判据一起：结论对，而且报得出。

    这个库里少掉的那一条观察是**下调**造成的（v4 的形状装不下「同一份内容、两个观察
    时刻」，夹具把两条搬回旧表时它们就只剩一行了），不是这一步丢的。

    ## 为什么这一条在修之前是**红的**

    ``legacy_rows == observations`` 是这里唯一一个看起来「显然成立」的等式，而它正是
    缺陷本身：``observations`` 靠 ``observation_id`` 唯一，而旧表的行靠 ``seq`` 唯一。
    两个 id 空间不一样大，所以「一行进去一条观察出来」是一句**关于两个空间的假设**，
    不是一条不变量。
    """
    row = ContactRoute(
        workspace=WORKSPACE,
        account_id=ROSTER_ACCOUNT,
        email="office@example.co.uk",
        first_seen_at=FETCHED_AT,
    )
    _build_v4(settings, extra=((row, FETCHED_AT + timedelta(days=1)),))

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert report.routes is not None
    assert report.routes.legacy_rows == V4_ROUTE_ROWS + 1
    assert report.routes.repeated == 1
    assert len(report.routes.observations) == V4_ROUTE_ROWS
    assert len(report.routes.projections) == V4_ROUTE_ROWS
    assert "另有 1 行与前面的行给出的是**同一条**观察" in report.render()

    store = SqliteEvidenceStore(settings.database_path)
    assert len(store.list_route_observations(ROSTER_ACCOUNT)) == 1
    assert store.list_routes(ROSTER_ACCOUNT)[0].revision == 1
    # 旧行守恒：每一行要么成了一条观察，要么与前面某一行并成了同一条。丢行的样子
    # 恰恰是这两个数悄悄不等 —— 而盘上那几行总得有个去处。
    assert (
        len(report.routes.observations) + report.routes.repeated
        == report.routes.legacy_rows
    )


def test_the_two_places_an_observation_time_can_come_from_are_counted_apart(
    settings: Settings,
) -> None:
    """两个 ``observed_at`` 档位分开数、分开报；退让档取的是那一行真的 ``stored_at``。

    旧形状里没有第三处可查：要么是路线自己记下的 ``first_seen_at``（**要的就是它**），
    要么是该行的 ``stored_at``（落库墙钟 —— 总是偏晚，但它是真实发生过的时刻）。把两者
    混成一个数字之后，「多少条是退让来的」这个问题就没有答案了 —— 而那正是运维要拿
    它判断的东西。

    正控在同一个库里：另一个账号那一行**记着** ``first_seen_at``，它必须落在第一档。
    少了它，一个「整批都退让、数字照样对得上」的实现也会绿。
    """
    _build_v4(settings)
    seq, payload, stored_at = _legacy_row(settings.database_path, account_id=ROSTER_ACCOUNT)
    assert payload.pop("first_seen_at", None) is not None, "前提：这一行本来记着那一格"
    _break(
        settings.database_path,
        "UPDATE contact_route SET payload = ? WHERE seq = ?",
        (dump_payload(payload), seq),
    )

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert report.routes is not None
    assert report.routes.observed_from == {
        OBSERVED_FROM_FIRST_SEEN: 1,
        OBSERVED_FROM_STORED_AT: 1,
    }
    rendered = report.render()
    assert "其中 1 条的观察时刻取自路线自己的" in rendered
    assert "1 条退回该行的" in rendered

    store = SqliteEvidenceStore(settings.database_path)
    fallback = store.list_route_observations(ROSTER_ACCOUNT)
    assert len(fallback) == 1
    assert fallback[0].observed_at == datetime.fromisoformat(stored_at), (
        "退让档取的是那一行自己的 stored_at，不是一个补上去的现在"
    )
    kept = store.list_route_observations(GARDEN_ID)
    assert [item.observed_at for item in kept] == [FETCHED_AT], (
        "正控：记着 first_seen_at 的那一行没有跟着一起退让"
    )


def test_a_v4_row_that_does_not_read_as_a_route_refuses_the_whole_run(
    settings: Settings,
) -> None:
    """一行搬过去读不出来 ⇒ **整份拒绝**，一个字都不改，并点名是哪一行。

    与前三步同一个理由：搬过去而读不出来，失败会推迟到服务启动之后某一次读，那时它
    长得像「库坏了」—— 而报告里那句「迁移完成」已经没人回头看了。
    """
    _build_v4(settings)
    seq, payload, _stored = _legacy_row(settings.database_path, account_id=ROSTER_ACCOUNT)
    payload.pop("email")
    _break(
        settings.database_path,
        "UPDATE contact_route SET payload = ? WHERE seq = ?",
        (dump_payload(payload), seq),
    )

    with pytest.raises(StoreError) as excinfo:
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    message = str(excinfo.value)
    assert f"contact_route[seq={seq}]" in message
    assert "email" in message
    assert "整份拒绝，一行都没写" in message
    # 一个字节都没改：版本还是 4，形状还是旧的。
    assert _version_of(settings.database_path) == 4
    assert _config_table_shape(settings.database_path, "contact_route")[1] == ("seq",)


def test_a_v4_row_whose_account_column_disagrees_with_its_payload_refuses(
    settings: Settings,
) -> None:
    """索引列与 payload 说着两个账号 ⇒ 拒绝。

    这一行今天**读得出来**（读路径只认 payload），却与它自己的索引列说着两件事 ——
    而按账号读那条路走的是索引列。搬进新形状里之后这个矛盾**反而消失了**（两处都由
    payload 算出来），所以这里是最后一处拦得住它的地方。
    """
    _build_v4(settings)
    seq, _payload, _stored = _legacy_row(settings.database_path, account_id=ROSTER_ACCOUNT)
    _break(
        settings.database_path,
        "UPDATE contact_route SET account_id = ? WHERE seq = ?",
        (MEADOW_ID, seq),
    )

    with pytest.raises(StoreError) as excinfo:
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    message = str(excinfo.value)
    assert f"contact_route[seq={seq}]" in message
    assert "列里的账号是" in message
    assert "两者必须是同一个" in message
    assert _version_of(settings.database_path) == 4


def test_a_v4_database_with_no_routes_says_it_was_split_with_zero_rows(
    settings: Settings,
) -> None:
    """旧表一行都没有的那一次：**照拆**，而报告说的是「0 行」。

    「拆过了、零行」与「没有可拆的东西」是两句不同的话（``RouteSplit.note`` 存在的
    全部理由）。空表仍然是 v4 的形状，所以它照拆不误 —— 而报告若不写数字，这一次与
    「这一步根本没跑」在输出里长得一样。
    """
    _build_v4(settings)
    _break(settings.database_path, "DELETE FROM contact_route", ())

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert report.routes is not None
    assert report.routes.rebuilt is True
    assert report.routes.legacy_rows == 0
    assert report.routes.observations == ()
    assert report.routes.projections == ()
    assert "的 0 行搬成" in report.render()
    assert _config_table_shape(settings.database_path, "contact_route")[1] == ("canonical_key",)
    assert SqliteEvidenceStore(settings.database_path).list_routes(ROSTER_ACCOUNT) == []


# --- 第五块形状：``research_job`` 换一份 ``input_kind`` 的词表（§B12 的 cohort 输入） --
#
# 前四块改的是**词**（v2 → v3）、**列**（v3 → v4）、**这张表是什么**（v4 → v5）。这一块
# 改的是**一处约束**：``research_job.input_kind`` 那一格的 CHECK 是一份词表，而 v5 写下
# 的那一份只有三个成员（这一版代码的 ``JobInputKind`` 有四个，第四个是按冻结名单提交研
# 究）。表本身读得出来、写已知的那三种也全都正常 —— 坏的只有那一处，所以它比前几个版本
# 都安静（``db._VERSION_NOTES[5]`` 记的就是这句话）。
#
# ``ALTER TABLE`` 没有改约束这一项，所以这一步是「按新形状建一张 → 搬行 → 丢旧的 → 改名」，
# 而它是五步里**唯一一处 DROP 一张被别人引用着的表**（``work_item.job_id``）—— 外键强制
# 因此必须在事务**之外**关掉。下面那个夹具（``_rebuild_job_table``）走的是同一个配方：
# 不这么写，它自己就造不出来。
#
# 「这一版代码认得的词 = ``JobInputKind``」这句话由 ``tests/test_sqlite_job_kind.py`` 钉住。
# 这里钉的是另一半：**盘上那张表真的被换成了那一份**，而且换的过程一行都没丢。

#: v5 的 ``input_kind`` 那一格写着的约束。**抄一份在这里是刻意的**：这份代码里已经没有它
#: 了（v6 那一份多列了一个词），而这个夹具要造的正是「上一个版本写下的库」。从今天的
#: ``JOB_TABLE_BY_NAME`` 取的话，造出来的是一个 v6 库，这一步就永远不会被跑到 —— 而它
#: 正是这一段唯一要测的东西。
_V5_INPUT_KIND = (
    "CHECK (input_kind IN ('source_receipt', 'known_accounts', 'discovery_scope'))"
)

#: 一份**更宽**的词表：它认得这一版代码的全部四个词，外加一个谁也不认识的。用它可以造出
#: 「约束读得出来、而**行**搬不过去」那一档 —— ``_plan_job_kind`` 对着它算得出两边各认
#: 得什么，于是在写入之前就能逐行点名。
_WIDER_INPUT_KIND = (
    "CHECK (input_kind IN ('source_receipt', 'known_accounts', 'discovery_scope',"
    " 'cohort', 'manual_domains'))"
)


#: 编译那个作业要的两样：一串身份、一份规则快照。与 ``tests/test_job_contract.py``
#: 用的是同一对夹具（``tests/fakes.py``）—— 自己再写一份的话，「同一个作业编译两次」
#: 那几条幂等判据会变成「夹具每次都造一份新的，于是相等是碰巧的」。
DOSSIERS = synthetic_dossiers(ROSTER_ACCOUNT)
SCENE_FILES = synthetic_scene_files()


def _rebuild_job_table(db_path: Path, *, constraint: str) -> None:
    """把 ``research_job`` 换成「同样的列、不同的 ``input_kind`` 约束」的那张表。

    ``constraint`` 是写在 ``input_kind`` 那一列后面的那一段（含 ``CHECK``，或空串表示
    **没有约束**）—— 三个拒绝用例各要一种盘上的读数：v5 那三个词、一份更宽的词表、
    以及读不出词表的那一种。

    ## 为什么外键必须在事务之外关

    ``work_item.job_id`` 引用着这张表，而 ``DROP TABLE`` 在有引用行时会被拒；更要紧的是
    ``PRAGMA foreign_keys`` 在事务里是**空操作**（文档逐字如此，实测亦然）。所以这两句
    在 ``BEGIN`` 之前，与 ``migrate()`` 里那一步同一条理由 —— 这不是实现细节：不这么写，
    这个夹具根本造不出「上一个版本写下的库」。

    ## 为什么是「建新的 → 搬 → 丢旧的 → 改名」而不是「改旧名 → 建回来」

    ``ALTER TABLE ... RENAME`` 会把**引用它的那一句**一起改写成指着搬开的那张表
    （``work_item.job_id REFERENCES research_job`` → ``REFERENCES _v5_research_job``），
    接着删掉搬开的那张之后，外键就指向一张不存在的表 —— 而盘上一切正常。迁移那一步
    的 docstring 逐字写了这一条，夹具不能用一条迁移自己都不敢用的配方。
    """
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "CREATE TABLE _v5_research_job (\n"
                "    job_id          TEXT PRIMARY KEY,\n"
                "    scene_id        TEXT NOT NULL,\n"
                f"    input_kind      TEXT NOT NULL {constraint},\n"
                "    input_ref       TEXT NOT NULL,\n"
                "    idempotency_key TEXT NOT NULL,\n"
                "    request_sha256  TEXT NOT NULL,\n"
                "    state           TEXT NOT NULL CHECK (state IN\n"
                "                        ('queued', 'paused', 'blocked', 'cancelled')),\n"
                "    reason          TEXT NOT NULL DEFAULT '',\n"
                "    created_at      TEXT NOT NULL,\n"
                "    updated_at      TEXT NOT NULL,\n"
                "    UNIQUE (scene_id, idempotency_key)\n"
                ")"
            )
            columns = ", ".join(_columns_of(conn, "research_job"))
            conn.execute(
                f"INSERT INTO _v5_research_job ({columns})"
                f" SELECT {columns} FROM research_job"
            )
            conn.execute("DROP TABLE research_job")
            conn.execute("ALTER TABLE _v5_research_job RENAME TO research_job")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def _job_row(db_path: Path, job_id: str) -> dict[str, str]:
    """作业那一行，**不经过读路径**读出来。``research_job`` 在 v5 的库上开不了
    （``open_job_store`` 那道闸会拒），所以迁移前的读数只能这样取。"""
    with session(db_path) as conn:
        row = conn.execute(
            "SELECT scene_id, input_kind, input_ref, idempotency_key, request_sha256,"
            " state FROM research_job WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    assert row is not None, f"夹具里没有 {job_id} 那一行"
    return {key: str(row[key]) for key in row.keys()}


def _build_v5(
    settings: Settings, *, constraint: str = _V5_INPUT_KIND
) -> tuple[str, str]:
    """一个**真实的** v5 库：作业与工作单元都走真的写路径，再把那一格换回旧词表、版本写回 5。

    返回 ``(job_id, work_item_id)``。

    作业与工作单元**不是手写的行**：经 ``SqliteJobStore.admit`` 与 ``compile_work_items``
    写下，所以「搬完之后它们还在吗」这一问处置的是真行 —— 而重建唯一会丢的东西就是行。
    工作单元带 ``work_item_snapshot``（``compile_work_items`` 在同一个写事务里写的），
    于是「引用它的那一行还指得到吗」也有真行可核。

    版本那一格在这里手工写到 5：``_rebuild_job_table`` 只动那一张表（另几步的调用方各自
    负责自己的版本格），而一个版本仍写着 6、形状却是 v5 的库是**第三种**形状 —— 预检会
    以「既不认得出 v6、也不认得出 v5」把它拒掉，那与这里要测的东西无关。
    """
    _write_inputs(settings)
    _write_rows(settings)
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=WORKSPACE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref=ROSTER_ACCOUNT,
        idempotency_key="v5-key-1",
        request_sha256=sha256_text("v5-request"),
    )
    (item,) = store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)
    _rebuild_job_table(settings.database_path, constraint=constraint)
    _break(
        settings.database_path,
        "UPDATE schema_meta SET value = ? WHERE key = ?",
        ("5", SCHEMA_VERSION_KEY),
    )
    return job.job_id, item.work_item_id


def test_a_v5_database_widens_the_input_kind_vocabulary_and_keeps_every_row(
    settings: Settings,
) -> None:
    """一个 v5 的库走完第五步之后：那一格认得四个词了，而**行一行不少、引用还指得到**。

    四句断言各自回答一件不同的事，缺一句这件事就没被证明：

    * 报告里**原来认得的词**与**现在认得的词**都写出来了 —— 「重建过了」这句话在把
      CHECK 又搬回旧词表的时候与改对了的时候长得一样；
    * 盘上那张表此刻认得的词 == 这一版代码的词表 —— 报告说的是计划，这一格才是盘上的；
    * 作业那一行**逐字段没变**，而且走**真的读路径**读得回来、它那一批工作单元还在 ——
      重建是 ``建新的 → 搬 → 丢旧的 → 改名``，少搬一步就丢行，而丢了行之后紧接着的那次
      ``get`` 照样回 ``None``（与「这个作业本来就不存在」长得一样）；
    * ``PRAGMA foreign_key_check`` 干净 —— 这一步的外键强制在事务期间是关的，「搬漏了会
      被拦下来」在这里不成立，所以完整性要**自己**核一次。
    """
    job_id, item_id = _build_v5(settings)
    before = _job_row(settings.database_path, job_id)
    with session(settings.database_path) as conn:
        assert _admitted_kinds(_table_sql(conn, "research_job")) == (
            "source_receipt",
            "known_accounts",
            "discovery_scope",
        ), "前提：这个库的作业表还是 v5 的词表"

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert (report.from_version, report.to_version) == (5, SCHEMA_VERSION)
    assert report.jobs is not None
    assert report.jobs.present is True
    assert report.jobs.rebuilt is True
    assert report.jobs.rows == 1, "这一次只该搬一个作业"
    assert report.jobs.admitted_on_disk == (
        "source_receipt",
        "known_accounts",
        "discovery_scope",
    )
    assert report.jobs.admitted_declared == tuple(kind.value for kind in JobInputKind)
    rendered = report.render()
    assert "研究请求的输入种类（v5 → v6）" in rendered
    assert "重建了 research_job 1 行" in rendered
    assert "source_receipt、known_accounts、discovery_scope、cohort" in rendered

    with session(settings.database_path) as conn:
        # 盘上那一格认得的词 == 这一版代码的词表。**这一格才是判据**：报告说的是计划。
        assert _admitted_kinds(_table_sql(conn, "research_job")) == tuple(
            kind.value for kind in JobInputKind
        )
        # 搬行用的那张临时表不在了。它在的话，下一次重建会以「这个名字已经有主」拒绝，
        # 而那时报出来的是一句与本次无关的话。
        assert _columns_of(conn, "research_job_v6") == ()
        # 外键完整性自己核一次（这一步在事务里关掉了 SQLite 的那层检查）。
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # 形状还是那十列、主键还是 job_id —— 「重建过了」与「重建成了别的形状」在报告里同形。
    assert _config_table_shape(settings.database_path, "research_job") == (
        (
            "job_id",
            "scene_id",
            "input_kind",
            "input_ref",
            "idempotency_key",
            "request_sha256",
            "state",
            "reason",
            "created_at",
            "updated_at",
        ),
        ("job_id",),
    )
    assert _job_row(settings.database_path, job_id) == before, "搬行改动了作业那一行"

    # 走**真的读路径**读回来：形状对了不等于读得出来，而读路径只认这几列。
    after = SqliteJobStore(settings.database_path)
    read_back = after.get(job_id)
    assert read_back is not None, "迁移之后这个作业读不回来了"
    assert read_back.input_kind is JobInputKind.KNOWN_ACCOUNTS
    assert [item.work_item_id for item in after.work_items(job_id)] == [item_id]

    # 这一步要修的那件事本身：这一版代码的第四个词现在写得进去了。
    # 反面（v5 那张表**写不进**它）由 ``tests/test_sqlite_job_kind.py`` 钉住 —— 两半
    # 分开写是因为那一半只需要一段建表语句，不需要一个数据库。
    widened = after.admit(
        scene_id=WORKSPACE,
        input_kind=JobInputKind.COHORT,
        input_ref="coh-0000000000000000",
        idempotency_key="v6-key-1",
        request_sha256=sha256_text("v6-request"),
    )
    assert widened.input_kind is JobInputKind.COHORT


def test_a_second_run_at_v6_says_the_vocabulary_already_matches(settings: Settings) -> None:
    """重跑是核对，不是再改一遍 —— 而它读回来的词表就是它上一次写下去的那一份。

    「哪一份是迁移前」只能有一个答案（与第二步共享这一条判据），而这一步多一条别的步骤
    没有的：它的判据是「盘上的词 ≠ 这一份」，所以重跑必须**读得出**上一次写下去的那
    一份 —— 读不出来时它会把一次已经迁完的库再重建一遍，而那一次重建是白做的、
    并且会在报告里写成「补上了」。
    """
    job_id, _ = _build_v5(settings)
    first = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)
    after = _job_row(settings.database_path, job_id)

    again = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert again.already_current is True
    assert again.backup is None
    assert again.jobs is None, "版本已经是 v6 时这一步不该有可重建的东西"
    assert _job_row(settings.database_path, job_id) == after, "重跑改动了作业那一行"
    assert list(settings.database_path.parent.glob("*.bak")) == [first.backup]


def test_a_job_table_whose_check_cannot_be_read_refuses_the_whole_run(
    settings: Settings,
) -> None:
    """盘上那一格**读不出词表**（没有 CHECK）→ 整份拒绝，一行都不写。

    ``_admitted_kinds`` 认不出来时**不猜**「大概是想说那几个」：重建会把那一格的约束换成
    代码这一份，而换之前没有任何一处证明过它在盘上原本说的是什么。它可能被手工改过，
    也可能来自一个约束更宽的部署 —— 而这两种的处置不同，猜一个就等于把这个问题删掉。

    断言的是「一个字都没改」与「没有留下备份」，而不是异常类型：一个失败的迁移若留下
    半份动过的库，比不迁移更坏（报告里「迁移过」之后就没有第二个人会回头看那些行了）。
    """
    job_id, _ = _build_v5(settings, constraint="")
    before = _job_row(settings.database_path, job_id)

    with pytest.raises(StoreError, match="读不出它认得的词"):
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert _version_of(settings.database_path) == 5
    assert _job_row(settings.database_path, job_id) == before
    assert list(settings.database_path.parent.glob("*.bak")) == []


def test_a_job_row_outside_the_new_vocabulary_refuses_the_whole_run(
    settings: Settings,
) -> None:
    """有一行的 ``input_kind`` 不在这一版代码的词表里 → 整份拒绝，并**逐行点名**。

    这一档来自一个**约束更宽的部署**（这个词在那个库里写得进去）。搬过去的那一句会撞在
    新约束上 —— 而那时报出来的是一句 CHECK 撞车，看起来像存储坏了。所以它在**计划**里
    先算完：迁移到一半的库比没迁的库更坏，而「迁移到一半」在报告里与「迁完了」同形。
    """
    job_id, _ = _build_v5(settings, constraint=_WIDER_INPUT_KIND)
    _break(
        settings.database_path,
        "INSERT INTO research_job (job_id, scene_id, input_kind, input_ref,"
        " idempotency_key, request_sha256, state, reason, created_at, updated_at)"
        " VALUES ('job-wider', ?, 'manual_domains', 'x', 'key-wider', ?, 'queued', '',"
        " '2026-09-15T00:00:00+00:00', '2026-09-15T00:00:00+00:00')",
        (WORKSPACE, "0" * 64),
    )

    with pytest.raises(StoreError, match="manual_domains"):
        migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert _version_of(settings.database_path) == 5
    assert _job_row(settings.database_path, job_id)["input_kind"] == "known_accounts"
    assert list(settings.database_path.parent.glob("*.bak")) == []
    with session(settings.database_path) as conn:
        assert _admitted_kinds(_table_sql(conn, "research_job")) == (
            "source_receipt",
            "known_accounts",
            "discovery_scope",
            "cohort",
            "manual_domains",
        ), "拒绝之后盘上那一格不该被动过"


def test_a_missing_job_table_is_planned_as_nothing_to_do(settings: Settings) -> None:
    """这张表不在盘上时，这一步的计划是「没有可重建的东西」—— 而不是「重建零行」。

    两条路**都要**留一条说得出口的理由，而它们不同：``present=False`` 是「这张表不在
    盘上」（``replay_ddl`` 会按这一版代码的形状建它），``rebuilt=False`` 是「它在，而且
    认得的词与代码一致」。报告里都读作「一行都没有重建」，而运维要看的正是理由。

    这一档走 ``_plan_job_kind`` 本身，不经过 ``migrate()``：那条命令在计划**之前**先跑
    ``replay_ddl``，于是缺表这一支在它那里到不了（缺了会被当场按新形状建出来，然后读成
    「词表已经一致」）。从模块里 import 一个下划线名是刻意的 —— 与 ``_VERSION_NOTES``
    那一条同一个理由：给测试专门加一个公开转发，等于让测试去测那个转发。
    """
    _build_v5(settings)
    with session(settings.database_path) as conn, write_transaction(conn):
        conn.execute("DROP TABLE work_item_snapshot")
        conn.execute("DROP TABLE work_item")
        conn.execute("DROP TABLE research_job")

    with session(settings.database_path) as conn:
        plan = _plan_job_kind(conn, db_path=settings.database_path)

    assert plan.present is False
    assert plan.rebuilt is False
    assert plan.empty is True
    assert plan.rows == 0
    assert plan.admitted_on_disk == ()
    assert plan.admitted_declared == tuple(kind.value for kind in JobInputKind)
    assert "没有可重建的东西" in plan.describe()


# ----------------------------------------------------------------------
# 第六步：execution_ref 的那一列改名
# ----------------------------------------------------------------------
#: 改名前那一列在 v6 的库上叫什么。这里写的是一个**占位名**，不是历史名：这一步的
#: 计划逻辑按「映射表上那唯一一个不认识的列」识别它（理由在 ``_plan_execution_ref``
#: 的 docstring），所以判据不必知道历史上那个名字 —— 知道它反而会把判据钉在一个
#: 已经被删掉的词上。
_LEGACY_EXECUTION_REF_COLUMN = "legacy_task_ref"


def _build_v6(settings: Settings, *, rename: bool = True) -> tuple[str, str]:
    """一个**真实的** v6 库：作业、工作单元、派发映射都走真写路径，然后把映射那
    一列改成旧名、版本写回 6。

    行不是手写的：``map_execution`` 写下的是迁移真正要保住的那一行。改名用 SQLite
    自己的 ``RENAME COLUMN``（它会把索引与引用一起搬），所以这个夹具与一个历史库在
    形状上只差那一列的名字 —— 而差的正是这一步要处置的东西。

    前两行同 ``_build_v5``：先让一个真的写路径建库并写下工作区绑定。少了绑定，
    迁移会在门口以 ``StoreError`` 拒掉，而那条判据与这里要测的改名无关。
    """
    _write_inputs(settings)
    _write_rows(settings)
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=WORKSPACE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref=ROSTER_ACCOUNT,
        idempotency_key="v6-key-ref",
        request_sha256=sha256_text("v6-request-ref"),
    )
    (item,) = store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)
    store.map_execution(item.work_item_id, dispatch_task_id="TASK-777")
    if rename:
        _break(
            settings.database_path,
            "ALTER TABLE execution_ref RENAME COLUMN dispatch_task_id TO "
            f"{_LEGACY_EXECUTION_REF_COLUMN}",
            (),
        )
    _break(
        settings.database_path,
        "UPDATE schema_meta SET value = ? WHERE key = ?",
        ("6", SCHEMA_VERSION_KEY),
    )
    return job.job_id, item.work_item_id


def test_a_v6_mapping_table_gets_the_new_column_and_keeps_its_rows(
    settings: Settings,
) -> None:
    """v6 → v7 改的是**列名**，不是数据：那一行还在，读写路径都回到新名字上。

    三句断言各自回答一件不同的事：

    * 报告说它找到了哪一列并用它改成了什么 —— 「改过了」与「本来就叫这个」在报告里
      长得一样，而两者的处置不同；
    * 盘上的列集合里不再有旧名、且有新名 —— 报告说的是计划，这一格才是盘上的；
    * 走**真的读路径**（``execution_ref``）读回来的是那一行的值 —— 列名对了不等于
      读得出来，而读路径只认新名字。
    """
    _, item_id = _build_v6(settings)
    with session(settings.database_path) as conn:
        assert _LEGACY_EXECUTION_REF_COLUMN in _columns_of(conn, "execution_ref"), (
            "前提：这个库的映射表还是旧列名"
        )

    report = migrate(settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir)

    assert (report.from_version, report.to_version) == (6, SCHEMA_VERSION)
    assert report.refs is not None
    assert report.refs.present is True
    assert report.refs.renamed is True
    assert report.refs.found_column == _LEGACY_EXECUTION_REF_COLUMN
    assert report.refs.rows == 1
    rendered = report.render()
    assert _LEGACY_EXECUTION_REF_COLUMN in rendered
    assert "dispatch_task_id" in rendered

    with session(settings.database_path) as conn:
        columns = _columns_of(conn, "execution_ref")
    assert _LEGACY_EXECUTION_REF_COLUMN not in columns
    assert "dispatch_task_id" in columns

    ref = SqliteJobStore(settings.database_path).execution_ref(item_id)
    assert ref is not None, "迁移之后那一行读不回来了"
    assert ref.dispatch_task_id == "TASK-777"


def test_a_v6_database_with_the_new_column_already_plans_nothing(
    settings: Settings,
) -> None:
    """一个版本写着 6、列名却已经是新名字的库：这一步什么也不做，且**说得出口**。

    「改过了」与「本来就叫这个」在报告里必须分开。后者不发 ``ALTER`` —— 这一条
    用 ``rename=False`` 造一个只在版本号上停在 6 的库，于是 ``refs.renamed`` 为假，
    而 ``present`` 仍为真：它确实看过那张表。
    """
    _build_v6(settings, rename=False)

    report = migrate(
        settings.database_path, profile=WORKSPACE, inputs_dir=settings.inputs_dir
    )

    assert (report.from_version, report.to_version) == (6, SCHEMA_VERSION)
    assert report.refs is not None
    assert report.refs.present is True
    assert report.refs.renamed is False
    assert report.refs.empty is True
    # ``rows`` 记的是**这一步搬了几行**，不是表里有几行：没发 ALTER 就是 0。表里那
    # 一行由回读那一格核（``execution_ref 1 行``），两者不是同一个数。
    assert report.refs.rows == 0
    assert "execution_ref 1 行" in report.verified

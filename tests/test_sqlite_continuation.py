"""``adapters/sqlite/`` 续跑那一节独有的判据：**持久化、跨连接、原子性、自愈**。

## 为什么这些不放进契约测试

``tests/test_continuation_contract.py`` 跑的是**两个实现都该满足**的性质。下面这四组
只有落盘实现能回答，而把它们写进契约就只会给它开一个「内存除外」的例外 —— 契约里
出现第一个例外之后，第二个就不会有人再争论了。

## 这一份里真正承重的是**三件**契约测试结构上问不了的事

1. **跨连接。** CAS 在同一个进程、同一个 store 对象上满足得很廉价：一个只比较内存
   里那份副本的实现照样通过。它成立的真实条件是两个写者**各持一条连接**、各自提交，
   而数据库层挡住第二个 —— 所以这里的每个写者是一个独立的 ``SqliteJobStore`` /
   ``SqliteWakeupRegistry``，对着同一个文件。

2. **原子落账的**回滚**方向。** 契约测试里 ``test_a_refused_settlement_leaves_no_intent``
   判的是**判定**拒绝（作业已取消，量到那句 ``raise`` 就返回），而这里判的是**写入
   途中**失败：``admit`` 的 INSERT 与它欠下的那行（``continuation_rows.owe`` 里的
   INSERT）在同一个 ``BEGIN IMMEDIATE`` 里，所以第二句抛出去时第一句必须一起消失。
   故障是注入的（让 outbox 那条 INSERT 抛），因为正常路径上写不出「一半成功」—— 而一个
   把 ``owe`` 挪到事务外面的实现会在这里红，且只在崩溃时表现成「受理成功、下一步永久
   丢失」。契约测试问不了它：内存实现没有事务，回滚与不回滚是同一件事。

   注入点跟着那份 INSERT 走：它现在是 ``continuation_rows`` 里的模块级函数（因为
   ``ReviewCaseStore.record_resolution`` 是它的第二个调用方），所以补丁打在那个模块上。
   打在 ``jobs`` 上不会报错 —— 它会**静默地不生效**，于是这两条判据变成两条普通的
   成功路径断言。

3. **免迁移自愈。** 整份 DDL 每次 ``ensure_schema`` 都重放，所以「盘上还没有这两张表」
   自愈。这正是「加表不改 ``SCHEMA_VERSION``」那条规则换来的东西，所以判据要看**两半**：
   表回来了，而版本号**没有动**。

## 时刻

写动词（``claim_continuation`` / ``finish_continuation``）收 ``now``，这里一律取那一行
自己的 ``due_at``（与契约测试逐字同一条理由）。``admit`` / ``settle`` / ``set_state``
**不收** ``now``：它们盖的是写入那一刻的墙钟，所以涉及它们的判据判的是**落在这一次
调用之间**，不是等于某个常量。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from marketops.adapters.sqlite import continuation_rows as rows_module
from marketops.adapters.sqlite import jobs as jobs_module
from marketops.adapters.sqlite.continuation_rows import CONTINUATION_COLUMNS
from marketops.adapters.sqlite.db import (
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
    connect,
    get_meta,
    session,
    write_transaction,
)
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.wakeup import SqliteWakeupRegistry
from marketops.domain.continuation import (
    BLOCKED_BACKOFF,
    OWNER_STALE_AFTER,
    BlockedOn,
    ContinuationClaim,
    ContinuationKind,
    ContinuationState,
    continuation_id,
    intents_for_admission,
)
from marketops.domain.enums import AttemptState, JobInputKind, StopReason
from marketops.domain.errors import ContinuationError, JobStateError
from marketops.domain.findings import Finding, Findings
from marketops.domain.util import sha256_text, utc_now
from tests.fakes import synthetic_dossiers, synthetic_scene_files

UTC = timezone.utc
SCENE = "uk-acme-natural-grass"
KEY = "discovery-v1-arm-b"
WORKSPACE = "ws-continuation"
HOLDER = "controller-a"
SUBJECTS = ("acct-0001", "acct-0002")
DOSSIERS = synthetic_dossiers(*SUBJECTS)
SCENE_FILES = synthetic_scene_files()

#: 一次有产出的结算。判据里出现的那条 observation 是给结算自己用的，与续跑那一行无关。
FOUND = Findings(
    observations=(Finding(statement="这一轮确认了天然草", evidence_refs=(sha256_text("p")[:32],)),),
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "state" / "marketops.db"


def _request(tag: str = "r1") -> str:
    return sha256_text(tag)


def _admit(store: SqliteJobStore, *, tag: str = "r1", key: str = KEY) -> str:
    """受理一个作业，返回它的 ``job_id``（也就是它欠下的派发那一行的业务键）。"""
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.DISCOVERY_SCOPE,
        input_ref="devon-2026-09",
        idempotency_key=key,
        request_sha256=_request(tag),
    )
    return job.job_id


def _dispatch_id(job_id: str) -> str:
    return continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, job_id)


def _row(store: SqliteJobStore, cid: str):
    row = store.continuation(cid)
    assert row is not None
    return row


def _claim(
    store: SqliteJobStore,
    cid: str,
    *,
    holder: str = HOLDER,
    epoch: int = 1,
    now: datetime | None = None,
) -> ContinuationClaim:
    """认领。``now`` 缺省取那一行的 ``due_at``（见模块 docstring 的最后一段）。"""
    at = _row(store, cid).due_at if now is None else now
    return store.claim_continuation(cid, holder=holder, epoch=epoch, now=at)


def _finish(
    store: SqliteJobStore,
    cid: str,
    state: ContinuationState,
    *,
    holder: str = HOLDER,
    epoch: int = 1,
    now: datetime | None = None,
    **kwargs,
):
    at = _row(store, cid).due_at if now is None else now
    return store.finish_continuation(
        cid, state=state, holder=holder, epoch=epoch, now=at, **kwargs
    )


def _leased(db_path: Path) -> tuple[SqliteJobStore, str, str]:
    """受理 → 编译 → 派发 → 租出，返回 ``(store, attempt_id, work_item_id)``。"""
    store = SqliteJobStore(db_path)
    job_id = _admit(store)
    items = store.compile_work_items(job_id, DOSSIERS, scene=SCENE_FILES)
    store.map_execution(items[0].work_item_id, quay_task_id="TASK-001")
    attempt = store.lease(items[0].work_item_id, holder="worker-a")
    return store, attempt.attempt_id, items[0].work_item_id


# ----------------------------------------------------------------------
# 持久化：每一个动词各自跨一次新构造
# ----------------------------------------------------------------------
def test_an_intent_survives_a_new_store(db_path: Path) -> None:
    """欠下的那一行是**盘上**的东西，不是某个 store 对象的内存。

    一个把 outbox 存在进程里的实现在同一进程内测起来完全正常，而它失败的样子正是
    §10 要消灭的那一件：控制器重启之后「关掉聊天仍续下一批」不会发生，且报告上
    什么都看不出来（受理成功了、作业在盘上、只是没有人会再来）。
    """
    job_id = _admit(SqliteJobStore(db_path))
    cid = _dispatch_id(job_id)

    row = _row(SqliteJobStore(db_path), cid)
    assert row.business_key == job_id
    assert row.kind is ContinuationKind.DISPATCH_BATCH
    assert row.state is ContinuationState.PENDING
    assert (row.holder, row.epoch) == ("", 0)
    assert row.attempts == 0
    assert row.blocked_on is None


def test_a_claim_survives_a_new_store(db_path: Path) -> None:
    """认领是**一次 CAS 的结果**，所以它必须落到盘上 —— 否则另一个进程的眼睛里
    这一行仍然是 PENDING，而它会再领一次（同一件昂贵的事做两遍）。"""
    cid = _dispatch_id(_admit(SqliteJobStore(db_path)))
    assert _claim(SqliteJobStore(db_path), cid).won

    row = _row(SqliteJobStore(db_path), cid)
    assert row.state is ContinuationState.CLAIMED
    assert (row.holder, row.epoch) == (HOLDER, 1)


def test_a_blocked_row_keeps_its_reason_and_its_backoff(db_path: Path) -> None:
    """``blocked_on`` 读回来必须是**闭集成员**，不是字符串。

    它在盘上是一个字符串（枚举存 ``.value``），而分类读的是成员。读回来的路上少一次
    ``BlockedOn(...)``，比较就会静默地永远为假 —— 于是「暂停导致的没做成」在报告里
    变成「别的原因」，而 ``ControllerState`` 的判词跟着错。
    """
    cid = _dispatch_id(_admit(SqliteJobStore(db_path)))
    store = SqliteJobStore(db_path)
    assert _claim(store, cid).won
    _finish(
        store,
        cid,
        ContinuationState.BLOCKED,
        reason="场景被运维暂停",
        blocked_on=BlockedOn.PAUSE,
    )

    row = _row(SqliteJobStore(db_path), cid)
    assert row.state is ContinuationState.BLOCKED
    assert row.blocked_on is BlockedOn.PAUSE
    assert row.reason == "场景被运维暂停"
    # 被挡住之后隔一段再看一眼：不留退避的话控制器每一轮都会为同一道关着的门开一次
    # 执行，而它在账上只显示「看了很多次」。
    assert row.due_at == row.updated_at + BLOCKED_BACKOFF
    assert row.is_open


def test_the_wakeup_owner_survives_a_new_registry(db_path: Path) -> None:
    """归属是**一行**，不是一份名单 —— 所以它同样必须跨进程可见。

    拿在手里的归属只活在一个进程的内存里，等于没有归属：第二个进程起来时看不见
    任何东西，于是它**也会**拿到一个，而两个触发器各自都认为自己是对的。
    """
    at = utc_now()
    owner = SqliteWakeupRegistry(db_path).acquire(WORKSPACE, holder=HOLDER, now=at)
    assert owner is not None

    current = SqliteWakeupRegistry(db_path).current(WORKSPACE)
    assert current == owner
    assert (current.holder, current.epoch) == (HOLDER, 1)


def test_a_release_survives_a_new_registry(db_path: Path) -> None:
    """让出是**删掉那一行**（下一任从 ``epoch=1`` 开始），不是把心跳改旧。

    改旧的话，下一任读到的是一行「过期」，于是它会以为自己顶掉了一个崩溃的进程 ——
    而实际发生的是上一个进程好好地说了一声再见。这个区别只在报告里看得见，而报告是
    运维判断「要不要去看一眼」的唯一依据。
    """
    at = utc_now()
    first = SqliteWakeupRegistry(db_path)
    owner = first.acquire(WORKSPACE, holder=HOLDER, now=at)
    assert owner is not None
    assert first.release(WORKSPACE, holder=HOLDER, epoch=owner.epoch)

    second = SqliteWakeupRegistry(db_path)
    assert second.current(WORKSPACE) is None
    successor = second.acquire(WORKSPACE, holder="controller-b", now=at + timedelta(seconds=1))
    assert successor is not None
    assert successor.epoch == 1


# ----------------------------------------------------------------------
# 跨连接：CAS 与「只有那一代能结账」
# ----------------------------------------------------------------------
def test_two_connections_racing_one_intent_only_one_wins(db_path: Path) -> None:
    """两个**各持一条连接**的写者同时领同一行，只有一个 ``won``。

    两个都赢意味着同一件昂贵的事做两遍（§8B 逐字禁止），而两边都以为自己是对的。
    挡住第二个的是 ``BEGIN IMMEDIATE`` + 读-判-写同一事务；这里判的正是它是否真的
    在数据库层成立，而不是在某个 store 对象的副本上成立。
    """
    cid = _dispatch_id(_admit(SqliteJobStore(db_path)))
    a, b = SqliteJobStore(db_path), SqliteJobStore(db_path)

    first = _claim(a, cid, holder="controller-a")
    second = _claim(b, cid, holder="controller-b")

    assert first.won
    assert not second.won
    # 输的那一个拿回的必须是**盘上那一行**（别人的），不是它自己以为要写的那个。
    assert second.continuation.holder == "controller-a"
    assert second.continuation.epoch == 1
    assert _row(b, cid).state is ContinuationState.CLAIMED


def test_another_connection_cannot_finish_what_it_did_not_claim(db_path: Path) -> None:
    """结账的三格（``holder`` / ``epoch`` / ``CLAIMED``）必须都在，少一格就不是本人。

    只判 ``CLAIMED`` 会让任何一个看见这一行的进程都能替它结账；只判 ``holder`` 会让
    一次换代之后回来的旧控制器（它的 ``holder`` 与新一代**逐字相同**）把结果盖在
    别人的上面。
    """
    cid = _dispatch_id(_admit(SqliteJobStore(db_path)))
    assert _claim(SqliteJobStore(db_path), cid, holder="controller-a").won
    other = SqliteJobStore(db_path)

    with pytest.raises(ContinuationError):
        _finish(other, cid, ContinuationState.DONE, holder="controller-b")

    with pytest.raises(ContinuationError):
        # 同名、**不同代**：一模一样的 ``holder``，epoch 差一。
        _finish(other, cid, ContinuationState.DONE, holder="controller-a", epoch=2)

    assert _row(other, cid).state is ContinuationState.CLAIMED


def test_a_replaced_epoch_cannot_heartbeat_from_another_connection(db_path: Path) -> None:
    """被顶掉的那一代的 ``heartbeat`` 必须返回 ``False``。

    它必须**能**返回 ``False``：一次换代之后旧控制器唯一诚实的答复是「你已经不是了」。
    写成一个不带 ``epoch`` 条件的 ``UPDATE`` 会让它永远自我感觉良好 —— 那是
    「并行双触发器」最省事的产生方式。
    """
    at = utc_now()
    first = SqliteWakeupRegistry(db_path)
    owner = first.acquire(WORKSPACE, holder=HOLDER, now=at)
    assert owner is not None

    # 一个**活着的**持有者顶不掉：只会拿到 ``None``。
    assert first.acquire(WORKSPACE, holder="controller-b", now=at) is None
    assert first.current(WORKSPACE) == owner

    stale_at = at + OWNER_STALE_AFTER + timedelta(seconds=1)
    successor = SqliteWakeupRegistry(db_path).acquire(
        WORKSPACE, holder="controller-b", now=stale_at
    )
    assert successor is not None
    assert successor.epoch == owner.epoch + 1
    # 顶掉时 ``acquired_at`` 换成这一代的起点，不是留着上一代的。
    assert successor.acquired_at == stale_at

    assert not first.heartbeat(WORKSPACE, holder=HOLDER, epoch=owner.epoch, now=stale_at)
    assert first.heartbeat(
        WORKSPACE, holder="controller-b", epoch=successor.epoch, now=stale_at
    )


def test_two_connections_acquiring_at_once_share_one_row(db_path: Path) -> None:
    """两个新进程同时起：只能有一个拿到归属，另一个拿到 ``None``（然后等一会儿再来）。

    这条判据的**第二半**才是要紧的：输的那个不是「拿到一个自己的归属」，因为那样
    两个进程都在驱动同一批共享资源（模型预算、执行器、Quay 任务）。
    """
    at = utc_now()
    a, b = SqliteWakeupRegistry(db_path), SqliteWakeupRegistry(db_path)

    first = a.acquire(WORKSPACE, holder="controller-a", now=at)
    second = b.acquire(WORKSPACE, holder="controller-b", now=at)

    assert first is not None
    assert second is None
    assert b.current(WORKSPACE) == first


# ----------------------------------------------------------------------
# 原子性：outbox 与业务事件在同一个写事务里
# ----------------------------------------------------------------------
def test_an_outbox_failure_takes_the_job_row_with_it(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**受理与它欠下的下一步是同一步。** §10 逐字：「业务事件受理与 continuation
    intent 原子落账」。

    判据的形状：让 outbox 那条 INSERT 抛，于是 ``admit`` 抛；然后再读盘 ——
    **作业行也不得留下**。只判 `admit` 抛是不够的：一个先插入作业、再在事务外
    ``owe`` 的实现同样会抛，而它留下的盘面是「受理成功、下一步永久丢失」——
    那正是 R2 那一行要消灭的东西，且报告上什么都看不出来。

    故障是**注入**的，因为正常路径上写不出「一半成功」；注入的这一句就是那个把
    ``owe`` 挪出事务的实现会红的来源。
    """

    def _boom(conn, row) -> None:
        raise RuntimeError("注入：outbox 那条写失败了")

    monkeypatch.setattr(rows_module, "insert_continuation", _boom)

    store = SqliteJobStore(db_path)
    with pytest.raises(RuntimeError):
        _admit(store)

    assert store.get(jobs_module.job_id(SCENE, KEY)) is None
    assert store.continuations(scene_ids=[SCENE]) == ()


def test_a_settlement_that_cannot_owe_leaves_the_attempt_leased(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一条判据落在**结算**那一侧：三行写与它欠下的那一行同生共死。

    一次被**拒绝**的结算走不到 ``owe``（``test_a_refused_settlement_leaves_no_intent``
    判的是那个方向）。这里判的是写**已经走到** ``owe`` 才失败的那一步 —— 那时
    ``job_attempt`` / ``work_item`` / ``job_settlement`` 三行已经写下去了，而它们
    必须跟着一起回滚。

    留下它们而丢掉欠账，盘面是「这件做完了、没人会来复核」：一次结算的全部产出都在，
    只是下一步永远不会发生。
    """
    store, attempt_id, work_item_id = _leased(db_path)

    def _boom(conn, row) -> None:
        raise RuntimeError("注入：outbox 那条写失败了")

    monkeypatch.setattr(rows_module, "insert_continuation", _boom)
    with pytest.raises(RuntimeError):
        store.settle(attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)

    assert store.settlement(work_item_id) is None
    assert store.attempts(work_item_id)[0].state is AttemptState.LEASED
    assert [
        row.kind for row in store.continuations(scene_ids=[SCENE])
    ] == [ContinuationKind.DISPATCH_BATCH]


def test_owing_an_intent_that_is_already_there_leaves_that_row_alone(db_path: Path) -> None:
    """**「没有才写」目前是一条够不着的第二道闸，而这条判据是它唯一的执行器。**

    从端口出发的判据覆盖不了它，这是**实测**出来的：``admit`` 在
    ``existing is not None`` 处直接 ``return``，``settle`` 在 ``_already_done`` 处
    直接 ``return`` —— 重发**根本走不到** ``owe``。所以
    ``test_admitting_the_same_request_twice_still_owes_one`` 与
    ``test_the_same_settlement_twice_owes_one`` 守的是那两道上游短路，不是这一道。
    （这两条已按「重发落在那一行被做掉之后」加强，判的是那两道短路**没有**动
    continuation 行；那一半是真的，但它不是这一条。）

    那为什么还要留着它、还要给它执行器：``owe`` 是**跨端口共享的写入口**，§9 的
    「裁决后自动重评」就是从另一个端口
    （``ReviewCaseStore.record_resolution``）走进来的，而**第四个产出者的重发没有任何
    上游短路替它挡**。这条判据因此直接对着 ``owe`` 判，不从任何一个端口绕。

    做成 ``INSERT OR REPLACE``（或先删后写）的表现是那一行被退回 ``PENDING`` ——
    「做完的事又欠了一遍」，控制器下一轮把同一批件派第二遍，而盘面上只显示
    「还有一件没做完」。
    """
    store = SqliteJobStore(db_path)
    job_id = _admit(store)
    cid = _dispatch_id(job_id)
    _claim(store, cid)
    done = _finish(store, cid, ContinuationState.DONE)

    with session(db_path) as conn, write_transaction(conn):
        rows_module.owe(conn, intents_for_admission(job_id, SCENE), at=utc_now())

    assert _row(store, cid) == done, (
        "重放把做完的那一行退回了队列 —— 下一轮 tick 会把同一批件再派一次"
    )


# ----------------------------------------------------------------------
# 免迁移自愈：加表不改版本号
# ----------------------------------------------------------------------
def test_dropping_the_continuation_tables_is_healed_by_the_next_store(db_path: Path) -> None:
    """整份 DDL 每次 ``ensure_schema`` 都重放，所以「盘上还没有这两张表」自愈。

    这正是「加表不改 ``SCHEMA_VERSION``」那条规则换来的东西：一个已经跑着的库不需要
    一次迁移就能得到这两张表 —— 而它救的也不只是升级那一刻，手工 ``DROP``、
    半途而废的迁移、从别的快照恢复都落到这里。

    ## 自愈救的是**表**，不是**行**

    掉表当然带走了行，所以这条判据判的是「**接下来还能记账**」，不是「行还在」——
    写成后者的话它会因为一个真实的丢失而变绿，而那正是判据最不该做的事。
    下面三句就是那三件事，一句比一句更靠近真实用法：

    1. 重放之后这两张表都在（``continuation`` 与 ``continuation_owner``）；
    2. **重发同一个受理不重新欠一遍**（``owe`` 是「没有才写」，而作业行还在）——
       所以掉表之后的那个洞不会被一次重发无声地补上，
       它只能在**下一次业务事件**里恢复；
    3. 一次**新的**受理照常欠下它的下一步。
    """
    job_id = _admit(SqliteJobStore(db_path))
    SqliteWakeupRegistry(db_path).acquire(WORKSPACE, holder=HOLDER, now=utc_now())

    with connect(db_path) as conn, write_transaction(conn):
        conn.execute("DROP TABLE continuation")
        conn.execute("DROP TABLE continuation_owner")

    healed = SqliteJobStore(db_path)
    assert healed.continuation(_dispatch_id(job_id)) is None
    assert _admit(healed, tag="r1") == job_id  # 重发：不重新受理，也不重新欠
    assert healed.continuation(_dispatch_id(job_id)) is None

    other = _admit(healed, tag="r2", key=f"{KEY}-2")
    assert healed.continuation(_dispatch_id(other)) is not None

    registry = SqliteWakeupRegistry(db_path)
    assert registry.current(WORKSPACE) is None  # 表在，只是没有行
    assert registry.acquire(WORKSPACE, holder=HOLDER, now=utc_now()) is not None


def test_self_healing_does_not_move_the_schema_version(db_path: Path) -> None:
    """自愈**不是**一次迁移，所以版本号一格不动。

    这是上面那条的**另一半**，而且它挡的是一个具体的诱惑：既然新加了两张表，顺手
    在自愈那条路径上也盖一次章（``UNINITIALIZED`` 那一支就在隔壁）看起来更「整齐」。
    代价是所有已经在跑的库会在下一次启动时被一个**不是代码期望值**的版本判掉，
    而 ``replay_ddl`` 本来就已经把表补上了。

    ## 它判的不是「有没有人改过 ``SCHEMA_VERSION``」

    改常量这件事判据管不着：版本号前后取值相同——都是常量。它判的是**自愈路径没有
    写版本**，也就是「掉表重放之后，这个库仍然是它原来的那一版」。所以这条与上面那条
    是配对的：只判「表回来了」，一个顺手盖章的实现会全绿；只判「版本没动」，
    一个根本不重放 DDL 的实现会全绿。
    """
    SqliteJobStore(db_path)
    with connect(db_path) as conn:
        before = get_meta(conn, SCHEMA_VERSION_KEY)
    assert before == str(SCHEMA_VERSION)

    with connect(db_path) as conn, write_transaction(conn):
        conn.execute("DROP TABLE continuation")

    SqliteJobStore(db_path)  # 重放 DDL
    with connect(db_path) as conn:
        assert get_meta(conn, SCHEMA_VERSION_KEY) == before


# ----------------------------------------------------------------------
# 表形状：列就是那些列，且这张表没有工作区
# ----------------------------------------------------------------------
def test_the_continuation_table_has_exactly_the_flattened_columns(db_path: Path) -> None:
    """DDL 的列与 ``CONTINUATION_COLUMNS`` 必须**逐格相同**。

    两处各写一遍列名，加一列只会让其中一处漏掉 —— 而漏掉的形态是：少在 DDL 里加，
    ``INSERT`` 当场报错（还好）；少在 ``CONTINUATION_COLUMNS`` 里加，那一格
    **悄悄写空**，读回来时是一个默认值，报告上看起来完全正常。

    这条判据在这片之后更要紧了一层：``CONTINUATION_COLUMNS`` 已经不只服务于本模块的
    ``INSERT``，它同时是 ``jobs.py::_write_continuation`` 那句 ``UPDATE`` 的列序。
    列序漂移在那里的形态不是写空，而是**把值写进相邻的一格**。

    顺带钉住这张表**没有** ``workspace``：``continuation_id`` 由
    ``(scene_id, kind, business_key)`` 算出，而 ``scene_id`` 已经决定了工作区。
    再存一列就是同一个事实的第二个产地，它漂移的形态是「一行属于两个工作区」或
    「一个工作区看不见自己欠的东西」—— 两者都不报错。
    """
    SqliteJobStore(db_path)

    with connect(db_path) as conn:
        columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(continuation)")
        }
    assert columns == set(CONTINUATION_COLUMNS)
    assert "workspace" not in columns
    assert "payload" not in columns


def test_the_owner_table_is_keyed_by_workspace_alone(db_path: Path) -> None:
    """一个工作区在任一时刻**最多一个**持有者 —— 这是主键约束本身，不是纪律。

    做成一张历史表（谁什么时候持有过）的话，「现在的持有者是谁」要靠一次 ``ORDER BY``
    加一句读代码的人自己相信的排序来回答，而那个答案会随并发插入漂移。
    """
    SqliteJobStore(db_path)

    with connect(db_path) as conn:
        info = conn.execute("PRAGMA table_info(continuation_owner)").fetchall()
    keys = [str(row["name"]) for row in info if int(row["pk"]) > 0]
    assert keys == ["workspace"]


def test_a_tampered_row_is_refused_on_read_not_skipped(db_path: Path) -> None:
    """两个方向各判一次：**枚举值不认识**与**两格对不上**。

    后者是模型在构造时守的那条不变式（``blocked_on`` 非空 ⟺ ``state is BLOCKED``）。
    列可以单独改，而分类读的正是 ``blocked_on``、报告读的正是 ``state`` —— 两者不
    一致时「为什么没做」与「有没有被挡住」各说各话。读回来时重跑一次校验，是这条
    不变式唯一的执行器（盘上的行不经过任何写路径）。

    两条都落在 ``ValueError`` 上：``ContinuationState(row["state"])`` 抛的是它，
    而 pydantic 的 ``ValidationError`` 也是它的子类。
    """
    store = SqliteJobStore(db_path)
    cid = _dispatch_id(_admit(store))

    with connect(db_path) as conn, write_transaction(conn):
        conn.execute(
            "UPDATE continuation SET state = 'no_such_state' WHERE continuation_id = ?",
            (cid,),
        )
    with pytest.raises(ValueError):
        store.continuation(cid)
    with pytest.raises(ValueError):
        # 「列一张表」不能变成绕过检查的那条路。
        store.continuations(scene_ids=[SCENE])

    with connect(db_path) as conn, write_transaction(conn):
        conn.execute(
            "UPDATE continuation SET state = 'pending', blocked_on = 'pause' "
            "WHERE continuation_id = ?",
            (cid,),
        )
    with pytest.raises(ValueError):
        store.continuation(cid)


# ----------------------------------------------------------------------
# 判据的判据
# ----------------------------------------------------------------------
def test_the_errors_we_assert_on_are_the_ones_this_layer_raises() -> None:
    """上面那几条 ``pytest.raises`` 不许因为「抛的是别的异常」而假绿。

    ``ContinuationError`` 与 ``JobStateError`` 都不是 ``ValueError``：前者是
    「这一行不归你」这类**状态**拒绝，后者是「作业不受理结果」——把它们混成一个
    基类会让「读到一个篡改的行」与「结账结错了行」在调用方那里无法区分，而它们的
    处置正好相反（一个停机对账，一个停手）。
    """
    assert not issubclass(ContinuationError, ValueError)
    assert not issubclass(JobStateError, ValueError)
    assert not issubclass(ContinuationError, JobStateError)
    # 而「读到一个篡改的行」确实落在 ValueError 上（枚举与模型校验两处都在这一支），
    # 所以上面两条 `pytest.raises(ValueError)` 是**有意**选它，不是随手挑了个宽的。
    assert issubclass(ValidationError, ValueError)

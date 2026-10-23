"""``JobStore`` 的续跑意图那一节（§10 continuation/outbox）与 ``WakeupRegistry`` 的契约判据。

## 这里守的失败只有一种，但它有三张**都不说话**的脸

``docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md`` §10 逐字：**「业务事件受理与 continuation
intent 原子落账」** 与 **「唯一唤醒 owner，不能并行双触发器」**。前一句守三张脸：

1. **事件在盘上、它欠下的下一步不在** —— 「跑完之后还有下一批」这件事永久丢失，
   而它正是 R2 那一行「关掉聊天仍续下一批」的全部内容。报告上什么都看不出来：
   受理成功了、作业在盘上、只是没有人会再来。
2. **事件根本没发生，而那一行在** —— 一次被拒绝的结算留下一行「欠着一次复核」，
   于是控制器为一件不存在的事去复核。账上只显示「多了一次复核」。
3. **同一件事欠下两行** —— 同一件昂贵的事做两遍（§8B 逐字禁止），
   而账上只显示「多了一件」。

第一张脸由**共写**挡住（``admit``/``settle``/``set_state`` 与它们欠下的行在同一个写
事务里），第二张由**拒绝不留行**挡住，第三张不是靠纪律而是靠**主键**：
``continuation_id`` 是从 ``(scene_id, kind, business_key)`` 算出来的内容寻址 id，
所以「同一个事件第二次落账」不是写不进的问题，是**没有第二行可写**。

## 第二张脸的判据是**两个方向**的一对

``test_a_refused_settlement_leaves_no_intent`` 与
``test_a_settlement_owes_exactly_one_recheck`` 是同一件事的两半。只有前一半的话，
一个**什么都不写**的实现会全绿 —— 那种实现恰好保住了「不留行」，代价是
「关掉聊天仍续下一批」永远不会发生。

## 为什么时刻是「一次调用之间」而不是一个定值

``admit`` / ``settle`` / ``set_state`` **不收 ``now``**：它们是存储层的写动词，
盖的是**写入那一刻**的墙钟（与 ``application/`` 那一层「``now`` 是参数不是时钟」
不矛盾 —— 那一层的时刻由调用方给，为的是让一整轮用同一个时刻；一条刚落下的意图
没有这个问题，它只有一个时刻说得通）。所以本文件里凡是判「它现在就该被看一眼」
的地方，判的都是**落在这一次调用之间**，而不是等于某个常量。

## 为什么两个实现跑同一份

``SqliteJobStore`` / ``InMemoryJobStore`` 与 ``SqliteWakeupRegistry`` /
``InMemoryWakeupRegistry``。它在这里通过而在 SQLite 那里失败，才是「这是一个契约」
有意义的样子（B3 立的判据：端口在第二个实现出现时才提取，这里正好两个）。

## 刻意**不**在这里的判据

* **并发**。真正守得住靠的是 ``BEGIN IMMEDIATE``，那是 SQLite 的性质，不是契约的
  性质（内存那份没有锁，也不打算有）。与 ``tests/test_job_contract.py`` 逐字同一条。
* **跨进程**。同上。
* **控制器怎么用它们**（选哪些、什么时候结账、被顶掉了怎么办）——
  那是 ``tests/test_continuation_controller.py`` 的事。这里只判「存储层是不是一个
  忠实的载体」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryJobStore, InMemoryWakeupRegistry
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.wakeup import SqliteWakeupRegistry
from marketops.domain.continuation import (
    BLOCKED_BACKOFF,
    MAX_ATTEMPTS,
    OPEN_STATES,
    OWNER_STALE_AFTER,
    BlockedOn,
    Continuation,
    ContinuationClaim,
    ContinuationKind,
    ContinuationState,
    after_failure,
    continuation_id,
    intents_for_settlement,
    open_continuation,
    retry_delay,
)
from marketops.domain.enums import JobInputKind, JobState, StopReason
from marketops.domain.errors import ContinuationError, JobStateError
from marketops.domain.findings import Finding, Findings
from marketops.domain.job import ResearchJob, WorkItem
from marketops.domain.subject import SUBJECT_KIND_ACCOUNT
from marketops.domain.util import sha256_text, utc_now
from marketops.ports.continuation import WakeupRegistry
from marketops.ports.jobs import JobStore
from tests.fakes import synthetic_dossiers, synthetic_scene_files

UTC = timezone.utc
SCENE = "uk-acme-natural-grass"
KEY = "discovery-v1-arm-b"
SUBJECTS = ("acct-0001", "acct-0002")
DOSSIERS = synthetic_dossiers(*SUBJECTS)
SCENE_FILES = synthetic_scene_files()
WORKSPACE = "ws-continuation"
HOLDER = "controller-a"

#: 一次有产出的结算。续跑那一行与产出内容无关（它只说「欠着一次复核」），
#: 所以这里用最小的一份 —— 判据里出现的那条 observation 是给结算自己用的。
FOUND = Findings(
    observations=(Finding(statement="这一轮确认了天然草", evidence_refs=(sha256_text("p")[:32],)),),
)


def _request(tag: str = "r1") -> str:
    return sha256_text(tag)


#: 契约判据要跑过哪些实现。
IMPLEMENTATION_PARAMS = ("sqlite", "memory")


def test_the_contract_runs_against_every_implementation() -> None:
    """实现清单缩水时「两个实现都通过」会变成「一个实现通过」。"""
    assert set(IMPLEMENTATION_PARAMS) == {"sqlite", "memory"}


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> JobStore:
    if request.param == "sqlite":
        return SqliteJobStore(tmp_path / "marketops.db")
    return InMemoryJobStore()


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def registry(request: pytest.FixtureRequest, tmp_path: Path) -> WakeupRegistry:
    if request.param == "sqlite":
        return SqliteWakeupRegistry(tmp_path / "wakeup.db")
    return InMemoryWakeupRegistry()


# ----------------------------------------------------------------------
# 夹具
# ----------------------------------------------------------------------
def _admitted(store: JobStore, *, key: str = KEY, tag: str = "r1") -> ResearchJob:
    return store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.DISCOVERY_SCOPE,
        input_ref="devon-2026-09",
        idempotency_key=key,
        request_sha256=_request(tag),
    )


def _dispatched(store: JobStore) -> tuple[ResearchJob, WorkItem]:
    job = _admitted(store)
    items = store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)
    store.map_execution(items[0].work_item_id, dispatch_task_id="TASK-001")
    return job, items[0]


def _of_kind(store: JobStore, kind: ContinuationKind) -> tuple[Continuation, ...]:
    rows = store.continuations(scene_ids=(SCENE,))
    return tuple(row for row in rows if row.kind is kind)


def _row(store: JobStore, cid: str) -> Continuation:
    row = store.continuation(cid)
    assert row is not None, f"{cid} 应当还在盘上"
    return row


def _dispatch_id(store: JobStore) -> str:
    job = _admitted(store)
    return continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, job.job_id)


def _claim(
    store: JobStore,
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
    store: JobStore,
    cid: str,
    state: ContinuationState,
    *,
    holder: str = HOLDER,
    epoch: int = 1,
    now: datetime | None = None,
    **kwargs,
) -> Continuation:
    at = _row(store, cid).due_at if now is None else now
    return store.finish_continuation(
        cid, state=state, holder=holder, epoch=epoch, now=at, **kwargs
    )


# ----------------------------------------------------------------------
# 一、谁欠下什么：受理与结算各欠一件
# ----------------------------------------------------------------------
def test_admission_owes_exactly_one_dispatch_intent(store: JobStore) -> None:
    """受理欠下的是「把它还没交出去的件交给执行器」，**且现在就该被看一眼**。

    ``due_at == created_at`` 不是细节：一条刚被欠下的意图定在未来，会让「刚受理的
    作业」在状态页上显示成「还没到期」—— 而那是**另一件事**（周期还没到）。
    """
    before = utc_now()
    job = _admitted(store)
    after = utc_now()
    rows = _of_kind(store, ContinuationKind.DISPATCH_BATCH)

    assert len(rows) == 1
    row = rows[0]
    assert row.continuation_id == continuation_id(
        SCENE, ContinuationKind.DISPATCH_BATCH, job.job_id
    )
    assert row.scene_id == SCENE
    assert row.business_key == job.job_id
    assert row.state is ContinuationState.PENDING
    assert row.due_at == row.created_at
    assert before <= row.due_at <= after
    assert (row.epoch, row.holder, row.attempts) == (0, "", 0)
    # 派发这件事是**整个作业**的，不是某一个主体的 —— 编一个出来会让「这一件是
    # 关于谁的」有一个看起来有答案的空答案。
    assert (row.subject_kind, row.subject_ref) == ("", "")


def test_admitting_the_same_request_twice_still_owes_one(store: JobStore) -> None:
    """重发是正常的（重连、重试都走到这里），而它不得欠下第二行。

    **承重点在「重发发生在那一行已经被做掉之后」。** 两次受理之间什么都不发生时，
    「只有一行」在 ``INSERT OR REPLACE`` 下也成立 —— 写进去的还是同一行同一个
    ``PENDING``，判据看不出差别。真正要挡住的是重发把**做完的那一行退回队列**：
    那是「同一个事件欠下的下一步只有一行」这句空话的具体表现，而它的后果是下一轮
    tick 把同一批件**派第二遍**（真的花钱、真的再调一次模型）。

    所以断言的是**整行一格不动**，不是行数。行数只证明「没有多出一行」，
    而这一条要证明的是「没有少掉那一行已经走到的状态」。
    """
    job = _admitted(store)
    cid = continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, job.job_id)
    _claim(store, cid)
    done = _finish(store, cid, ContinuationState.DONE)

    _admitted(store)

    rows = _of_kind(store, ContinuationKind.DISPATCH_BATCH)
    assert len(rows) == 1
    assert rows[0] == done, (
        f"重发动了那一行 —— 下一轮 tick 会把同一批件再派一次：{rows[0]!r} 应等于 {done!r}"
    )


def test_a_settlement_owes_exactly_one_recheck(store: JobStore) -> None:
    """结算欠下的是**那个主体**的一次复核 —— §B21「仅相关对象等人」的输入侧。

    主体两格非空，所以一条被挡住的意图只带它自己的主体，它旁边那些主体的意图照常跑。
    把主体合成一个「本批次」的粗引用，会让这一条从结构上做不到。
    """
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)

    rows = _of_kind(store, ContinuationKind.MAINTENANCE_TICK)
    assert len(rows) == 1
    assert rows[0].business_key == item.work_item_id
    assert rows[0].subject_kind == SUBJECT_KIND_ACCOUNT
    assert rows[0].subject_ref == SUBJECTS[0]
    assert rows[0].state is ContinuationState.PENDING


def test_the_same_settlement_twice_owes_one(store: JobStore) -> None:
    """**重发一次结算不等于第二个事件。** 业务键取 ``work_item_id`` 而不是 attempt
    id，正是为了这一条：用 attempt id 会让同一次结算的重发算成两件事。

    与受理那条同一个承重点：重发落在**那一行已经被做掉之后**。做完的复核被退回队列
    的后果比受理那条更贵 —— 同一个主体被重看第二遍，而一次复核是真的去抓页面的。
    """
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)
    cid = continuation_id(SCENE, ContinuationKind.MAINTENANCE_TICK, item.work_item_id)
    _claim(store, cid)
    done = _finish(store, cid, ContinuationState.DONE)

    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)

    rows = _of_kind(store, ContinuationKind.MAINTENANCE_TICK)
    assert len(rows) == 1
    assert rows[0] == done, (
        f"重发把做完的复核退回了队列 —— 那个主体会被重看第二遍：{rows[0]!r} 应等于 {done!r}"
    )


def test_a_refused_settlement_leaves_no_intent(store: JobStore) -> None:
    """**被拒绝的结算不留行。** 这是「原子落账」那半句唯一可验证的判据。

    一行「欠着一次复核」而那次结算根本没发生，会让控制器为一件不存在的事去复核；
    账上只显示「多了一次复核」。而这一条只有**配上**
    ``test_a_settlement_owes_exactly_one_recheck`` 才有意义：一个什么都不写的实现
    同样能让它全绿。
    """
    job, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.set_state(job.job_id, JobState.CANCELLED, expected=JobState.QUEUED, reason="ops")

    with pytest.raises(JobStateError):
        store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)
    assert _of_kind(store, ContinuationKind.MAINTENANCE_TICK) == ()


def test_a_settlement_without_a_subject_snapshot_owes_an_unattributed_recheck() -> None:
    """说不出来就不写 —— 与 ``WorkItemSnapshot.subject`` 逐字同一条。

    编一个主体出来会让「这一件是关于谁的」有一个看起来有答案的空答案，而那个答案
    会被用来把一条复核局限到**别的**主体上。

    这条判据在**领域纯函数**上判，不在两个实现上各判一遍：``subject=None`` 到两格空
    是 :func:`intents_for_settlement` 的决定，而两个实现都只调它。
    """
    without = intents_for_settlement("item-1", SCENE)[0]
    with_ = intents_for_settlement("item-1", SCENE, subject=DOSSIERS[0])[0]

    assert (without.subject_kind, without.subject_ref) == ("", "")
    assert (with_.subject_kind, with_.subject_ref) == (SUBJECT_KIND_ACCOUNT, SUBJECTS[0])


# ----------------------------------------------------------------------
# 二、作业状态变了，欠着的那一步跟着变
# ----------------------------------------------------------------------
def test_pausing_a_job_blocks_its_dispatch_intent_as_a_pause(store: JobStore) -> None:
    """暂停**不消耗** attempts，且理由类型是闭集里的 ``PAUSE``。

    分类读的是 ``blocked_on`` 那一格，不是那句话 —— 于是「暂停要人去按一个按钮」
    与「缺执行器要运维补接线」在状态页上不会长得一样。
    """
    job = _admitted(store)
    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="operator")

    row = _of_kind(store, ContinuationKind.DISPATCH_BATCH)[0]
    assert row.state is ContinuationState.BLOCKED
    assert row.blocked_on is BlockedOn.PAUSE
    assert row.attempts == 0
    assert row.reason == "operator"
    assert row.updated_at >= row.created_at


def test_resuming_puts_the_dispatch_intent_back_in_the_queue(store: JobStore) -> None:
    """恢复**不接受理由**（``set_state`` 自己判死），所以这一条也只能不带理由 ——
    而它正好是判据的一部分：一条恢复不出自任何人的判断。"""
    job = _admitted(store)
    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="operator")
    store.set_state(job.job_id, JobState.QUEUED, expected=JobState.PAUSED)

    row = _of_kind(store, ContinuationKind.DISPATCH_BATCH)[0]
    assert row.state is ContinuationState.PENDING
    assert row.blocked_on is None
    assert row.reason == ""


def test_a_claimed_intent_is_not_rearmed_by_a_pause(store: JobStore) -> None:
    """``CLAIMED`` 的行在**一个控制器手里**：它可能已经在跑一次外部调用了。

    把它从别人手里改走，那两个写入者就都会以为自己是对的 —— §8B 逐字禁止的
    duplicate expensive work。要改它的是**认领者自己**（它在结账时会看到作业停了）。
    """
    cid = _dispatch_id(store)
    assert _claim(store, cid).won
    job = store.get(_row(store, cid).business_key)
    assert job is not None

    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="operator")

    row = _row(store, cid)
    assert row.state is ContinuationState.CLAIMED
    assert row.blocked_on is None
    assert (row.holder, row.epoch) == (HOLDER, 1)


def test_cancelling_a_job_closes_its_dispatch_intent(store: JobStore) -> None:
    """取消之后这一行**已经没有可做的了** —— 留着它会让状态页永远显示
    「还有未决工作」，而没有任何东西欠着。

    另一条同样要守的：**没有给它开第二条执行路径**。写这一格的是事件（受理的
    反面），不是控制器 —— 它认得出的只是「已经没什么可做」。
    """
    job = _admitted(store)
    store.set_state(job.job_id, JobState.CANCELLED, expected=JobState.QUEUED, reason="ops")

    row = _of_kind(store, ContinuationKind.DISPATCH_BATCH)[0]
    assert row.state is ContinuationState.DONE
    assert row.is_terminal


def test_cancelling_while_paused_also_closes_it(store: JobStore) -> None:
    """``BLOCKED → DONE`` 是事件写者的第二条边：暂停期间被取消同样是「没什么可做」。"""
    job = _admitted(store)
    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="operator")
    store.set_state(job.job_id, JobState.CANCELLED, expected=JobState.PAUSED, reason="ops")

    assert _of_kind(store, ContinuationKind.DISPATCH_BATCH)[0].state is ContinuationState.DONE


def test_a_rearmed_row_keeps_the_identity_the_event_gave_it(store: JobStore) -> None:
    """``why`` 说的是**为什么会有这一行**，它与执行记录是两半 —— 事件不会改写它。

    一个原地改写业务键（或那句 why）的实现会让「这一行是不是我以为的那一行」有
    一个说不清的答案。
    """
    job = _admitted(store)
    before = _of_kind(store, ContinuationKind.DISPATCH_BATCH)[0]
    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="operator")
    store.set_state(job.job_id, JobState.QUEUED, expected=JobState.PAUSED)

    after = _of_kind(store, ContinuationKind.DISPATCH_BATCH)[0]
    assert after.why == before.why
    assert after.continuation_id == before.continuation_id
    assert after.created_at == before.created_at


# ----------------------------------------------------------------------
# 三、认领是一次 CAS
# ----------------------------------------------------------------------
def test_claiming_a_pending_intent_wins_once_and_only_once(store: JobStore) -> None:
    cid = _dispatch_id(store)

    first = _claim(store, cid)
    second = _claim(store, cid, holder="controller-b", epoch=7)

    assert first.won and first.state is ContinuationState.CLAIMED
    assert (first.continuation.holder, first.epoch) == (HOLDER, 1)
    # 没拿到不是错误：拿回来的是**盘上那一行**（别人刚领走的），不是我以为的那一行。
    assert second.won is False
    assert (second.continuation.holder, second.epoch) == (HOLDER, 1)


def test_an_intent_that_is_not_due_yet_cannot_be_claimed(store: JobStore) -> None:
    """到期判定是「此刻或之前」。没到期的行领不走，**且它自己会说为什么**
    —— ``won=False`` 有三种来源，答案在返回的那一行里（``state`` 与 ``due_at``）。"""
    cid = _dispatch_id(store)
    due = _row(store, cid).due_at

    claim = _claim(store, cid, now=due - timedelta(seconds=1))

    assert claim.won is False
    assert claim.state is ContinuationState.PENDING
    assert claim.continuation.due_at == due


def test_a_blocked_intent_can_be_claimed_again(store: JobStore) -> None:
    """**挡它的那件事还在不在，只有走一遍才答得出来。**

    缺执行器的部署补上执行器之后，盘上没有任何一个事件会说「现在可以了」——
    那不是一次业务事件，是一次接线。所以被挡住的行按一个下界排进队列、被领起来
    重新问一遍。
    """
    job = _admitted(store)
    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="operator")
    cid = continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, job.job_id)

    claim = _claim(store, cid)

    assert claim.won is True
    # 领起来就不再有「被什么挡着」—— 它还挡着的话，这一次执行会重新记一遍。
    assert claim.continuation.blocked_on is None
    assert claim.continuation.reason == ""


def test_claiming_an_unknown_intent_is_refused(store: JobStore) -> None:
    with pytest.raises(ContinuationError):
        store.claim_continuation("0" * 32, holder=HOLDER, epoch=1, now=utc_now())


@pytest.mark.parametrize("holder,epoch", [("", 1), ("   ", 1), (HOLDER, 0), (HOLDER, -1)])
def test_a_malformed_claimant_is_refused(store: JobStore, holder: str, epoch: int) -> None:
    """空持有者无法被追问；epoch 0 与「没有持有者」不可区分 —— 两者都必须在
    写之前就判死，而不是写下一行谁也认不出来的记录。"""
    cid = _dispatch_id(store)

    with pytest.raises(ValueError):
        store.claim_continuation(cid, holder=holder, epoch=epoch, now=utc_now())


# ----------------------------------------------------------------------
# 四、结账：只有认领者能结，落点按次数
# ----------------------------------------------------------------------
def test_only_the_claimant_can_settle_it(store: JobStore) -> None:
    """CAS 的两格是 ``(holder, epoch)``。**不是你的**必须是一个**错误**，不是一个
    ``False`` —— 后者与「已经做完了」在调用点不可区分，而处置完全相反。"""
    cid = _dispatch_id(store)
    assert _claim(store, cid).won

    with pytest.raises(ContinuationError):
        _finish(store, cid, ContinuationState.DONE, holder="controller-b")
    with pytest.raises(ContinuationError):
        # **同一个 holder、不同的一代**：这正是 epoch 存在的理由（一次崩溃重启的
        # 控制器的 holder 可能与上一次逐字相同）。
        _finish(store, cid, ContinuationState.DONE, epoch=2)
    assert _row(store, cid).state is ContinuationState.CLAIMED


def test_a_row_that_is_not_claimed_cannot_be_settled(store: JobStore) -> None:
    cid = _dispatch_id(store)

    with pytest.raises(ContinuationError):
        _finish(store, cid, ContinuationState.DONE)


def test_settling_with_the_right_claim_lands_it(store: JobStore) -> None:
    cid = _dispatch_id(store)
    assert _claim(store, cid).won
    at = _row(store, cid).due_at + timedelta(minutes=1)

    landed = _finish(
        store, cid, ContinuationState.DONE, now=at, reason="派出去了 5 件"
    )

    assert landed.state is ContinuationState.DONE
    assert landed.reason == "派出去了 5 件"
    assert landed.updated_at == at
    assert landed.is_terminal


def test_a_failure_lands_on_the_retry_ladder_not_straight_on_failed(store: JobStore) -> None:
    """**执行器报 FAILED，盘上不一定落 FAILED。**

    「试过三次、要人看一眼」与「刚挂了第一次、等 60 秒再试」的处置完全不同，而
    合成一个词会让它们在报告里长得一样。落点由 ``after_failure`` 决定，**存储层算
    attempts**（让调用方传一个数，两个调用点迟早会对「什么算一次尝试」给出两个答案）。
    """
    cid = _dispatch_id(store)
    now = _row(store, cid).due_at
    landed = _row(store, cid)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        assert _claim(store, cid, now=now).won
        landed = _finish(
            store, cid, ContinuationState.FAILED, now=now, reason="执行器起不来"
        )
        expected = after_failure(attempt)
        assert landed.state is expected
        assert landed.attempts == attempt
        assert landed.reason == "执行器起不来"
        if expected is ContinuationState.PENDING:
            assert landed.due_at == now + retry_delay(attempt)
        else:
            # 终态不再排下一次 —— 它是「要人看一眼」，不是「再等等」。
            assert landed.due_at == now
        now = landed.due_at

    assert landed.state is ContinuationState.FAILED
    assert _claim(store, cid, now=now).won is False


def test_blocking_costs_no_attempts(store: JobStore) -> None:
    """一次暂停不是一次尝试。少了这一条，一个暂停一周的场景会被判成重试了一万次，
    而它会**因此**永远走不到人手里（次数用尽 → 终态）。"""
    cid = _dispatch_id(store)
    assert _claim(store, cid).won
    now = _row(store, cid).due_at

    landed = _finish(
        store,
        cid,
        ContinuationState.BLOCKED,
        now=now,
        reason="这个部署没有装执行器",
        blocked_on=BlockedOn.CAPACITY,
    )

    assert landed.attempts == 0
    assert landed.state is ContinuationState.BLOCKED
    assert landed.blocked_on is BlockedOn.CAPACITY
    assert landed.due_at == now + BLOCKED_BACKOFF


def test_blocking_without_a_reason_type_is_refused(store: JobStore) -> None:
    """分类读的是 ``blocked_on`` 那一格。少了它，「暂停」与「缺容量」在状态页上
    是同一格 —— 而一个要人等按钮、一个要运维补接线。"""
    cid = _dispatch_id(store)
    assert _claim(store, cid).won

    with pytest.raises(ValueError):
        _finish(store, cid, ContinuationState.BLOCKED, reason="x")
    with pytest.raises(ValueError):
        _finish(store, cid, ContinuationState.BLOCKED, blocked_on=BlockedOn.PAUSE)
    with pytest.raises(ValueError):
        _finish(store, cid, ContinuationState.FAILED)


def test_a_blank_checkpoint_keeps_the_previous_one(store: JobStore) -> None:
    """「走到哪儿了」不是一个会被清掉的东西 —— 一次只报理由的调用不该顺手把进度抹掉。"""
    cid = _dispatch_id(store)
    assert _claim(store, cid).won
    _finish(store, cid, ContinuationState.CLAIMED, checkpoint="派到第 3 件")

    kept = _finish(store, cid, ContinuationState.CLAIMED, reason="还在跑")
    assert kept.checkpoint == "派到第 3 件"
    assert kept.state is ContinuationState.CLAIMED

    replaced = _finish(store, cid, ContinuationState.CLAIMED, checkpoint="派到第 5 件")
    assert replaced.checkpoint == "派到第 5 件"


def test_an_illegal_transition_is_refused(store: JobStore) -> None:
    """一条非法迁移是一句要读的拒绝，而不是一个悄悄写下的状态。"""
    cid = _dispatch_id(store)
    assert _claim(store, cid).won
    _finish(store, cid, ContinuationState.DONE, reason="做完了")

    with pytest.raises(ContinuationError):
        _finish(store, cid, ContinuationState.PENDING)


# ----------------------------------------------------------------------
# 五、读：顺序、分格、空场景
# ----------------------------------------------------------------------
def test_continuations_come_back_ordered_and_narrowed(store: JobStore) -> None:
    """顺序是契约的一部分：控制器每一轮从最该做的那一件开始，而一个由存储层随便给的
    顺序会让两次 tick 在**上限截断**之下做出不同的选择 —— 报告上只会显示「这一轮
    做了别的几件」。"""
    job = _admitted(store)
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)
    # 把派发那一件领起来，好让两行**分别**在两个状态上 —— 否则筛选那半句
    # （``states``）在两条都 PENDING 的盘上无论对不对都会通过。
    cid = continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, job.job_id)
    assert _claim(store, cid).won

    everything = store.continuations(scene_ids=(SCENE,))
    assert {row.kind for row in everything} == set(ContinuationKind)
    assert [row.due_at for row in everything] == sorted(row.due_at for row in everything)
    assert len({row.continuation_id for row in everything}) == len(everything)

    claiming = store.continuations(scene_ids=(SCENE,), states=(ContinuationState.CLAIMED,))
    assert {row.kind for row in claiming} == {ContinuationKind.DISPATCH_BATCH}
    waiting = store.continuations(scene_ids=(SCENE,), states=(ContinuationState.PENDING,))
    assert {row.kind for row in waiting} == {ContinuationKind.MAINTENANCE_TICK}
    # 两行都欠着，所以「只给 PENDING」必须比「全都给」少一行 —— 少了这一句，
    # 一个**忽略** ``states`` 的实现会让上面两条里的一条通过（另一条也会，
    # 只要筛选恰好没被用到）。
    assert len(waiting) < len(everything)

    assert store.continuations(scene_ids=("de-acme-natural-grass",)) == ()


def test_an_empty_scene_list_is_not_every_scene(store: JobStore) -> None:
    """「不给」与「给空」是一对必须分开的东西。``scene_ids=()`` 说的是「这些场景」，
    而一个都不给就是没有场景 —— 它**不是**「全部」。"""
    _admitted(store)

    assert store.continuations(scene_ids=()) == ()
    assert store.continuation_counts(scene_ids=()) == {state: 0 for state in ContinuationState}


def test_counts_always_carry_every_state(store: JobStore) -> None:
    """五格**恒在**。缺格与 0 是两件事：报告里「这一格没有」与「没人知道这一格」
    读起来一样，而后者会让 ``classify`` 的输入少一格 —— 它按那六个数分格，
    少一个数就会把每一件都归错。"""
    assert store.continuation_counts(scene_ids=(SCENE,)) == {
        state: 0 for state in ContinuationState
    }

    _admitted(store)
    counts = store.continuation_counts(scene_ids=(SCENE,))
    assert set(counts) == set(ContinuationState)
    assert counts[ContinuationState.PENDING] == 1
    assert counts[ContinuationState.DONE] == 0
    assert sum(counts[state] for state in OPEN_STATES) == 1


def test_an_unknown_intent_reads_as_none_not_an_error(store: JobStore) -> None:
    assert store.continuation("0" * 32) is None


def test_the_same_event_derives_the_same_id() -> None:
    """幂等由**主键**保证，而不是靠「写之前先查一下」—— 后者在两个进程之间有一个
    真实的窗口，而那个窗口里发生的事是「同一个事件欠下两行下一步」。"""
    key = sha256_text("job-for-id-derivation")
    again = continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, key)
    assert again == continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, key)
    assert again != continuation_id(SCENE, ContinuationKind.MAINTENANCE_TICK, key)
    assert again != continuation_id("de-x", ContinuationKind.DISPATCH_BATCH, key)


def test_open_continuation_starts_from_the_intent_alone() -> None:
    """初始值只有一个产地（两个实现都调它），所以「一条新意图长什么样」只有一处说。"""
    from marketops.domain.continuation import ContinuationIntent

    intent = ContinuationIntent(
        scene_id=SCENE,
        kind=ContinuationKind.DISPATCH_BATCH,
        business_key="b" * 32,
        why="因为",
    )
    now = utc_now()
    row = open_continuation(intent, now=now)

    assert row.state is ContinuationState.PENDING
    assert row.due_at == now
    assert (row.epoch, row.holder, row.attempts, row.blocked_on) == (0, "", 0, None)
    assert row.why == "因为"
    assert row.is_open and not row.is_terminal


# ----------------------------------------------------------------------
# 六、唤醒归属：一个工作区一行，换代靠 epoch
# ----------------------------------------------------------------------
def test_the_first_acquire_takes_the_first_epoch(registry: WakeupRegistry) -> None:
    now = utc_now()
    owner = registry.acquire(WORKSPACE, holder=HOLDER, now=now)

    assert owner is not None
    assert owner.epoch == 1
    assert owner.holder == HOLDER
    assert owner.acquired_at == now
    assert owner.heartbeat_at == now


def test_a_live_holder_is_not_displaced(registry: WakeupRegistry) -> None:
    """**同名不算「是我」。** 一个进程不需要第二次拿到自己已经拿着的东西，而
    「同名即是我」正好让两个同名进程互相看不见 —— 那就是两个触发器。"""
    now = utc_now()
    registry.acquire(WORKSPACE, holder=HOLDER, now=now)

    assert registry.acquire(WORKSPACE, holder=HOLDER, now=now + timedelta(seconds=1)) is None
    current = registry.current(WORKSPACE)
    assert current is not None and current.epoch == 1


def test_a_stale_holder_is_displaced_with_a_new_epoch(registry: WakeupRegistry) -> None:
    """换代时 ``epoch`` 加一，而 ``acquired_at`` **跟着重置** —— 留着上一代的会让
    报告里那个时刻被读成「他什么时候接手的」。"""
    start = utc_now()
    registry.acquire(WORKSPACE, holder=HOLDER, now=start)
    later = start + OWNER_STALE_AFTER + timedelta(seconds=1)

    owner = registry.acquire(WORKSPACE, holder="controller-b", now=later)

    assert owner is not None
    assert owner.epoch == 2
    assert owner.holder == "controller-b"
    assert owner.acquired_at == later
    current = registry.current(WORKSPACE)
    assert current is not None and (current.epoch, current.holder) == (2, "controller-b")


def test_heartbeat_needs_the_exact_holder_and_epoch(registry: WakeupRegistry) -> None:
    """少了 ``epoch``，一次换代之后回来的旧持有者会把心跳写回去 —— 他的 ``holder``
    与新一代可以逐字相同。返回 ``False`` 是**回滚信号**，不是一个可以忽略的日志。"""
    start = utc_now()
    registry.acquire(WORKSPACE, holder=HOLDER, now=start)
    beat = start + timedelta(seconds=10)

    assert registry.heartbeat(WORKSPACE, holder=HOLDER, epoch=1, now=beat) is True
    assert registry.heartbeat(WORKSPACE, holder=HOLDER, epoch=2, now=beat) is False
    assert registry.heartbeat(WORKSPACE, holder="controller-b", epoch=1, now=beat) is False
    current = registry.current(WORKSPACE)
    assert current is not None
    assert current.heartbeat_at == beat
    assert current.epoch == 1


def test_release_frees_the_workspace(registry: WakeupRegistry) -> None:
    start = utc_now()
    registry.acquire(WORKSPACE, holder=HOLDER, now=start)

    assert registry.release(WORKSPACE, holder=HOLDER, epoch=1) is True
    assert registry.current(WORKSPACE) is None
    assert registry.release(WORKSPACE, holder=HOLDER, epoch=1) is False

    again = registry.acquire(WORKSPACE, holder="controller-b", now=start + timedelta(seconds=1))
    assert again is not None and again.holder == "controller-b"


def test_current_is_none_when_nobody_holds_it(registry: WakeupRegistry) -> None:
    """``current`` 不回一个布尔「有没有人」—— 它回**那个人**（或者 ``None``）。
    一个布尔答不出「是我吗」，而那个问题在控制器每一轮都要问。"""
    assert registry.current(WORKSPACE) is None

    registry.acquire(WORKSPACE, holder=HOLDER, now=utc_now())
    current = registry.current(WORKSPACE)
    assert current is not None and current.holder == HOLDER
    assert registry.current("ws-other") is None


def test_two_workspaces_do_not_share_an_owner(registry: WakeupRegistry) -> None:
    """粒度是**工作区**：两个控制器各自驱动同一个工作区下的不同场景，仍然是两个
    触发器在推同一批共享资源。按场景发放归属会让「四个场景各一个控制器」看起来是
    四个正当的持有者。"""
    now = utc_now()
    first = registry.acquire("ws-a", holder=HOLDER, now=now)
    second = registry.acquire("ws-b", holder=HOLDER, now=now)

    assert first is not None and second is not None
    assert first.epoch == 1 and second.epoch == 1

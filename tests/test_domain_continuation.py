"""续跑意图：**一条意图的形状**、**判词的级联**、**重试的阶梯**，以及**那一代的归属**。

本文件守四条边界，每一条对应的失败都**不会报错**，只会让报告说的与盘上的不一样：

1. **一条意图的两半必须同时成立。** ``subject_kind``/``subject_ref``、``blocked_on`` 与
   ``BLOCKED``、``CLAIMED`` 与 ``holder``/``epoch`` —— 只有一半时那一行说不出「是关于
   谁的」或「是谁在做」，而它会被当成说得出来那样读。
2. **判词是一条级联，不是一张对照表。** 六个数只有几种「整齐」形状的假设在**混合**输入
   上是错的，而混合是常态：一次上限截断之后尾巴是 ``due_now``、后面还有 ``waiting_due``。
   按对照表判，那些输入给出的不是错答案而是**异常** —— 而那正是最需要看一眼状态页的时刻。
3. **重试有次数界限。** ``after_failure`` 与 ``retry_delay`` 是「异常恢复有次数界限」
   那句话的执行器：没有它们，一个每次都说「下次再说」的执行器会永远重排下去，
   而账上每一次都写得像正常。
4. **归属是判据，不是定时器。** ``is_stale`` 被调用一次只回答一个问题，
   它不收回任何东西 —— 会自己收回的归属，会把一个只是卡了一下的控制器顶掉，
   而那个控制器回来之后仍然以为自己持有（§8B 逐字禁止的 duplicate expensive work）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from marketops.domain.continuation import (
    BLOCKED_BACKOFF,
    CONTINUATION_TRANSITIONS,
    MAX_ATTEMPTS,
    OPEN_STATES,
    OWNER_STALE_AFTER,
    RETRY_BACKOFF,
    RETRY_BACKOFF_CAP,
    TERMINAL_STATES,
    BlockedOn,
    Continuation,
    ContinuationClaim,
    ContinuationIntent,
    ContinuationKind,
    ContinuationState,
    ControllerInputs,
    ControllerState,
    WakeupOwner,
    after_failure,
    classify,
    continuation_id,
    intents_for_admission,
    intents_for_settlement,
    open_continuation,
    retry_delay,
)
from marketops.domain.subject import SUBJECT_KIND_ACCOUNT


UTC = timezone.utc
PLUS_EIGHT = timezone(timedelta(hours=8))
T0 = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
SCENE = "uk-acme-natural-grass"

#: ``classify`` 读的那六个桶。**这张表是穷尽性判据的一半** —— 另一半见
#: ``test_the_bucket_list_is_the_whole_input``。
_BUCKETS = (
    "in_flight",
    "due_now",
    "waiting_due",
    "blocked_paused",
    "blocked_capacity",
    "blocked_human",
)


def _row(**overrides) -> Continuation:
    payload = {
        "continuation_id": "a" * 32,
        "scene_id": SCENE,
        "kind": ContinuationKind.DISPATCH_BATCH,
        "business_key": "b" * 32,
        "due_at": T0,
        "created_at": T0,
        "updated_at": T0,
    }
    payload.update(overrides)
    return Continuation(**payload)


# ======================================================================
# 一、判词的级联
# ======================================================================
def test_an_empty_workspace_is_not_the_same_as_a_finished_one() -> None:
    """「这个市场做完了」与「这个市场从来没动过」是两个空。差别只有历史 ——
    而少了这一个布尔，报告上它们就是同一个空。"""
    assert classify(ControllerInputs()) is ControllerState.NO_WORK
    assert classify(ControllerInputs(ever_done=True)) is ControllerState.COMPLETE


def test_a_live_round_outranks_every_reason_why_others_wait() -> None:
    """``RESEARCHING`` 答的是「此刻**在**发生什么」。一个暂停的场景里若有在途的
    合法结果，那一轮**确实**在研究 —— 暂停不会因此消失，它在 ``blocked_paused`` 里。"""
    assert (
        classify(
            ControllerInputs(
                in_flight=1, blocked_paused=3, due_now=2, blocked_human=1
            )
        )
        is ControllerState.RESEARCHING
    )


def test_every_reason_has_a_state_of_its_own() -> None:
    """每一格都答得出「为什么不是下面那一格」：暂停要人按一个按钮、等人要去处理一件
    待办、缺容量要运维补接线、没到期**什么都不许做**。"""
    assert classify(ControllerInputs(blocked_paused=2)) is ControllerState.PAUSED
    assert classify(ControllerInputs(blocked_human=2)) is ControllerState.WAITING_ON_HUMAN
    assert (
        classify(ControllerInputs(blocked_capacity=2)) is ControllerState.MISSING_CAPACITY
    )
    assert classify(ControllerInputs(waiting_due=2)) is ControllerState.WAITING_DUE


def test_the_order_between_reasons_is_fixed_and_has_reasons() -> None:
    """优先级不是风格，每一对都有一个理由：

    * 暂停压过其余：场景暂停时别的处置都做不了 —— 恢复是一个按钮，而它按下去之前
      补执行器、处理待办都不会让这一轮多跑一件；
    * 等**人**压过缺**容量**：一件待办被裁决之后**可能**连带把缺容量也解掉
      （比如一条提议批准之后场景才能开），反过来不成立。
    """
    assert (
        classify(ControllerInputs(blocked_paused=1, blocked_human=1))
        is ControllerState.PAUSED
    )
    assert (
        classify(ControllerInputs(blocked_human=1, blocked_capacity=1))
        is ControllerState.WAITING_ON_HUMAN
    )
    assert (
        classify(ControllerInputs(blocked_capacity=1, waiting_due=1))
        is ControllerState.MISSING_CAPACITY
    )


def test_a_capped_round_is_an_answer_not_a_crash() -> None:
    """**这条是本文件唯一一条为一次真实缺陷而写的判据。**

    上限截断之后的盘**必然**是混合的：尾巴上那几件已经到期却没被做掉（``due_now``），
    而更后面还有没到期的（``waiting_due``）。按「六个数里恰好一格非零」判，这个输入
    一格都归不进去 —— 于是 ``classify`` 抛异常，而这一次抛在一个**刚刚正常跑完的
    上限轮次**末尾，状态页整个炸掉。

    级联对每一个非负组合都有答案，且六个数一并报出，所以「到期没做掉 1 件、另有 2 件
    没到期」这件事一个字都没丢 —— 丢掉的只是那条拒绝。
    """
    assert (
        classify(ControllerInputs(due_now=1, waiting_due=2)) is ControllerState.WAITING_DUE
    )
    assert (
        classify(ControllerInputs(due_now=1, blocked_capacity=2))
        is ControllerState.MISSING_CAPACITY
    )
    assert (
        classify(ControllerInputs(waiting_due=1, blocked_paused=2))
        is ControllerState.PAUSED
    )


def test_the_bucket_list_is_the_whole_input() -> None:
    """穷尽性的一半：``classify`` 读的那六个桶就是 ``ControllerInputs`` 的全部
    输入。加第七个桶而没接线，那条件会被静默读成 ``WAITING_DUE``。"""
    fields = set(ControllerInputs.__dataclass_fields__) - {"ever_done"}
    assert fields == set(_BUCKETS)


def test_every_bucket_alone_is_a_state_of_its_own() -> None:
    """穷尽性的另一半：**每一个桶单独非零都必须得出一个非空的判词。**

    一个没人读的桶在单桶输入上会掉到最后那一格（``WAITING_DUE``），而
    ``NO_WORK``/``COMPLETE`` 更糟 —— 那是「什么都没有」，它会让人停止看这一页。
    """
    for name in _BUCKETS:
        inputs = ControllerInputs(**{name: 1})
        assert inputs.open_total == 1, name
        assert classify(inputs) not in (
            ControllerState.NO_WORK,
            ControllerState.COMPLETE,
        ), name


def test_the_open_total_is_the_sum_of_the_parts() -> None:
    """``open_total`` 不单列一格（见 ``ControllerInputs`` 的 docstring）——
    所以它必须等于那六项之和，否则「未决总数」会是第二个真相来源。"""
    inputs = ControllerInputs(
        in_flight=1,
        due_now=2,
        waiting_due=3,
        blocked_paused=4,
        blocked_capacity=5,
        blocked_human=6,
    )
    assert inputs.open_total == 21


# ======================================================================
# 二、重试的阶梯
# ======================================================================
def test_the_ladder_starts_at_the_base_and_doubles() -> None:
    assert retry_delay(0) == RETRY_BACKOFF
    assert retry_delay(1) == RETRY_BACKOFF
    assert retry_delay(2) == RETRY_BACKOFF * 2
    assert retry_delay(3) == RETRY_BACKOFF * 4


def test_a_negative_attempt_count_is_not_a_zero_delay() -> None:
    """「还没试过就重排」不该得到一个零延迟 —— 那会让循环在同一秒里转很多圈，
    而每一次在账上都长得像一次正常的重排。"""
    assert retry_delay(-1) == RETRY_BACKOFF
    assert retry_delay(-1) > timedelta(0)


def test_the_ladder_is_capped() -> None:
    """没有上界，翻倍在第六次就是一小时、第十次就是一天 —— 而一个「等得越久越久」的
    重试在这张表上读起来完全正常。"""
    assert retry_delay(20) == RETRY_BACKOFF_CAP
    assert retry_delay(MAX_ATTEMPTS) <= RETRY_BACKOFF_CAP


def test_failing_lands_on_the_ladder_and_stops_at_the_top() -> None:
    """``attempts`` 是**加过这一次之后**的数 —— 这条判据把它钉住，因为差一位的
    形态是「少试一次」或者「多试一次」，两个在账上都只显示次数不同。"""
    assert after_failure(1) is ContinuationState.PENDING
    assert after_failure(MAX_ATTEMPTS - 1) is ContinuationState.PENDING
    assert after_failure(MAX_ATTEMPTS) is ContinuationState.FAILED
    assert after_failure(MAX_ATTEMPTS + 5) is ContinuationState.FAILED


# ======================================================================
# 三、一条意图的两半必须同时成立
# ======================================================================
def test_half_a_subject_is_refused() -> None:
    with pytest.raises(ValidationError):
        _row(subject_kind=SUBJECT_KIND_ACCOUNT)
    with pytest.raises(ValidationError):
        _row(subject_ref="acct-0001")


def test_a_full_subject_is_fine_and_blank_whitespace_is_not() -> None:
    row = _row(subject_kind=SUBJECT_KIND_ACCOUNT, subject_ref="acct-0001")
    assert (row.subject_kind, row.subject_ref) == (SUBJECT_KIND_ACCOUNT, "acct-0001")

    with pytest.raises(ValidationError):
        _row(subject_kind=" account", subject_ref="acct-0001")


def test_blocked_and_its_reason_type_are_one_thing() -> None:
    """分类读的是 ``blocked_on``、报告读的是 ``state``。两者不一致时
    「为什么没做」与「有没有被挡住」各说各话。"""
    blocked = _row(state=ContinuationState.BLOCKED, blocked_on=BlockedOn.PAUSE, reason="停")
    assert blocked.blocked_on is BlockedOn.PAUSE

    with pytest.raises(ValidationError):
        _row(state=ContinuationState.BLOCKED, reason="停")
    with pytest.raises(ValidationError):
        _row(state=ContinuationState.PENDING, blocked_on=BlockedOn.PAUSE)


def test_a_blocked_row_must_say_why() -> None:
    with pytest.raises(ValidationError):
        _row(state=ContinuationState.BLOCKED, blocked_on=BlockedOn.CAPACITY, reason="  ")


def test_a_claimed_row_must_have_both_halves_of_who() -> None:
    """缺了任何一格，控制器换代之后就认不出这一行是不是自己领的 —— 而它是一行
    **谁也收不回来**的在途件。"""
    assert _row(state=ContinuationState.CLAIMED, holder="controller-a", epoch=1).epoch == 1

    with pytest.raises(ValidationError):
        _row(state=ContinuationState.CLAIMED, epoch=1)
    with pytest.raises(ValidationError):
        _row(state=ContinuationState.CLAIMED, holder="controller-a")


def test_the_identity_three_are_never_blank() -> None:
    for field in ("continuation_id", "scene_id", "business_key"):
        with pytest.raises(ValidationError):
            _row(**{field: ""})
        with pytest.raises(ValidationError):
            _row(**{field: " x"})


def test_times_must_carry_a_zone_and_are_normalised() -> None:
    """naive 时间在两个部署之间会被读成不同的时刻，而到期判定正是拿它减 ``now``。"""
    with pytest.raises(ValidationError):
        _row(due_at=datetime(2026, 9, 23, 1, 0))

    elsewhere = datetime(2026, 9, 23, 9, 0, tzinfo=PLUS_EIGHT)
    assert _row(due_at=elsewhere).due_at == T0


def test_due_is_this_instant_or_before() -> None:
    row = _row(due_at=T0)
    assert row.is_due(T0)
    assert row.is_due(T0 + timedelta(microseconds=1))
    assert not row.is_due(T0 - timedelta(microseconds=1))


def test_a_closed_row_is_never_due() -> None:
    """到期判定读的是「还欠着吗」—— 一条做完的行没有「到不到期」这个问题，
    而一个答「到期了」的实现会让控制器反复去领一件已经做完的事。"""
    done = _row(state=ContinuationState.DONE, due_at=T0)
    assert done.is_open is False and done.is_terminal is True
    assert not done.is_due(T0 + timedelta(days=365))


def test_the_two_state_sets_partition_the_enum() -> None:
    """未决与终态是**恰好**两半。留下第三个既不未决也不终态的状态，会让
    「还有未决工作吗」这个问题有一个答不出来的成员。"""
    assert OPEN_STATES | TERMINAL_STATES == set(ContinuationState)
    assert OPEN_STATES & TERMINAL_STATES == set()


def test_terminal_states_have_no_way_out() -> None:
    """一条做完了的意图不会被谁重新打开 —— 「恢复从 checkpoint 继续」说的是未决的
    那些，不是要做完的再来一遍。"""
    assert CONTINUATION_TRANSITIONS[ContinuationState.DONE] == frozenset()
    assert CONTINUATION_TRANSITIONS[ContinuationState.FAILED] == frozenset()

    for state, targets in CONTINUATION_TRANSITIONS.items():
        assert state not in targets, state


def test_the_event_writer_edges_are_in_the_table() -> None:
    """事件写者（受理/结算/``set_state``）那四条边各对应一件真事。少一条的形态是
    「暂停了一个作业，而它欠着的那一步照跑」或者「取消了，而状态页永远显示还有
    未决工作」。"""
    pending = CONTINUATION_TRANSITIONS[ContinuationState.PENDING]
    assert ContinuationState.BLOCKED in pending  # 作业被暂停
    assert ContinuationState.DONE in pending  # 作业被取消：已经没什么可做
    assert ContinuationState.CLAIMED in pending  # 控制器领走

    blocked = CONTINUATION_TRANSITIONS[ContinuationState.BLOCKED]
    assert ContinuationState.PENDING in blocked  # 恢复
    assert ContinuationState.DONE in blocked  # 暂停期间被取消

    claimed = CONTINUATION_TRANSITIONS[ContinuationState.CLAIMED]
    assert claimed == {
        ContinuationState.PENDING,
        ContinuationState.BLOCKED,
        ContinuationState.DONE,
        ContinuationState.FAILED,
    }


def test_the_transition_table_has_a_row_for_every_state() -> None:
    """缺一行会在查表时 ``KeyError`` —— 那是一个**在写的路上**才炸的错误。"""
    assert set(CONTINUATION_TRANSITIONS) == set(ContinuationState)


def test_may_move_to_reads_the_table() -> None:
    claimed = _row(state=ContinuationState.CLAIMED, holder="c", epoch=1)
    assert claimed.may_move_to(ContinuationState.DONE)
    assert claimed.may_move_to(ContinuationState.PENDING)
    assert not claimed.may_move_to(ContinuationState.CLAIMED)


def test_the_backoffs_are_not_calibrated_claims() -> None:
    """这三个常量是**工程缺省**，不是业务事实。这条判据把它们的关系钉住（重试有界），
    而不是钉住它们的具体取值 —— 钉住取值会让人以为那个数是被批准过的。"""
    assert RETRY_BACKOFF > timedelta(0)
    assert BLOCKED_BACKOFF > timedelta(0)
    assert RETRY_BACKOFF_CAP >= RETRY_BACKOFF
    assert OWNER_STALE_AFTER > RETRY_BACKOFF, "归属的判据必须明显长于一次 tick 的间隔"


# ======================================================================
# 四、内容寻址
# ======================================================================
def test_the_id_is_a_function_of_all_three_parts() -> None:
    base = continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, "b" * 32)
    assert base == continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, "b" * 32)
    assert base != continuation_id("de-x", ContinuationKind.DISPATCH_BATCH, "b" * 32)
    assert base != continuation_id(SCENE, ContinuationKind.MAINTENANCE_TICK, "b" * 32)
    assert base != continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, "c" * 32)
    assert len(base) == 32


def test_an_identity_that_says_nothing_is_refused() -> None:
    with pytest.raises(ValueError):
        continuation_id("", ContinuationKind.DISPATCH_BATCH, "b" * 32)
    with pytest.raises(ValueError):
        continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, "")


# ======================================================================
# 五、两个产出者，一进一出
# ======================================================================
def test_admission_owes_the_dispatch_of_the_whole_job() -> None:
    (intent,) = intents_for_admission("job-1", SCENE)
    assert intent.kind is ContinuationKind.DISPATCH_BATCH
    assert intent.business_key == "job-1"
    # 派发是整个作业的，不是某一个主体的 —— 编一个出来会让「这一件是关于谁的」
    # 有一个看起来有答案的空答案。
    assert (intent.subject_kind, intent.subject_ref) == ("", "")
    assert intent.why


def test_settlement_owes_a_recheck_of_the_subject_it_named() -> None:
    (intent,) = intents_for_settlement("item-1", SCENE)
    assert intent.kind is ContinuationKind.MAINTENANCE_TICK
    assert intent.business_key == "item-1"
    assert (intent.subject_kind, intent.subject_ref) == ("", "")


def test_both_producers_agree_that_due_at_is_not_theirs_to_say() -> None:
    """``due_at`` 由落账那一刻的存储层填。让产出者收一个 ``at``，等于让它有机会说出
    一个它其实不知道的值 —— 而那个值会决定这一件**什么时候**被做。"""
    for intent in (
        intents_for_admission("job-1", SCENE)[0],
        intents_for_settlement("item-1", SCENE)[0],
    ):
        assert not hasattr(intent, "due_at")


def test_opening_an_intent_fills_the_execution_half_from_defaults() -> None:
    intent = ContinuationIntent(
        scene_id=SCENE,
        kind=ContinuationKind.MAINTENANCE_TICK,
        business_key="item-1",
        subject_kind=SUBJECT_KIND_ACCOUNT,
        subject_ref="acct-0001",
        why="因为",
    )
    row = open_continuation(intent, now=T0)

    assert row.continuation_id == continuation_id(
        SCENE, ContinuationKind.MAINTENANCE_TICK, "item-1"
    )
    assert row.state is ContinuationState.PENDING
    assert (row.due_at, row.created_at, row.updated_at) == (T0, T0, T0)
    assert (row.epoch, row.holder, row.attempts, row.blocked_on, row.reason) == (
        0,
        "",
        0,
        None,
        "",
    )
    assert row.why == "因为"
    assert row.is_due(T0), "一条刚被欠下的意图**现在就该被看一眼**"


def test_the_claim_outcome_carries_the_row_itself() -> None:
    """``won=False`` 有三种来源、处置相同，所以它只有一个布尔、没有原因码 ——
    要问是哪一种，读拿回来的那一行。"""
    row = _row(state=ContinuationState.CLAIMED, holder="controller-a", epoch=3)
    lost = ContinuationClaim(continuation=row, won=False)

    assert (lost.state, lost.epoch) == (ContinuationState.CLAIMED, 3)


# ======================================================================
# 六、归属：判据在调用方手里
# ======================================================================
def _owner(**overrides) -> WakeupOwner:
    payload = {
        "workspace": "ws-natural-grass",
        "holder": "controller-a",
        "epoch": 1,
        "acquired_at": T0,
        "heartbeat_at": T0,
    }
    payload.update(overrides)
    return WakeupOwner(**payload)


def test_an_epoch_starts_at_one_and_zero_is_not_a_holder() -> None:
    """0 与「没有持有者」不可区分，而它必须可以与后者分开 —— 一条 ``epoch=0`` 的
    在途件谁也认不出来。"""
    assert _owner(epoch=1).epoch == 1
    with pytest.raises(ValidationError):
        _owner(epoch=0)
    with pytest.raises(ValidationError):
        _owner(epoch=-3)


def test_a_blank_holder_cannot_be_asked_anything() -> None:
    with pytest.raises(ValidationError):
        _owner(holder="   ")
    with pytest.raises(ValidationError):
        _owner(workspace="")


def test_a_heartbeat_before_the_acquire_is_two_writes_in_the_wrong_order() -> None:
    with pytest.raises(ValidationError):
        _owner(acquired_at=T0, heartbeat_at=T0 - timedelta(seconds=1))
    assert _owner(acquired_at=T0, heartbeat_at=T0).heartbeat_at == T0


def test_staleness_is_a_predicate_with_an_exact_boundary() -> None:
    """边界是 ``>=``：恰好一个 ``OWNER_STALE_AFTER`` 没跳就算**可能**死了。
    差一个比较符的形态是「卡了一下的控制器被永远留在归属上」。"""
    owner = _owner(heartbeat_at=T0)

    assert not owner.is_stale(T0 + OWNER_STALE_AFTER - timedelta(microseconds=1))
    assert owner.is_stale(T0 + OWNER_STALE_AFTER)
    assert owner.is_stale(T0 + OWNER_STALE_AFTER * 10)


def test_staleness_can_be_measured_with_another_bound() -> None:
    """上界是参数，因为「多久算死」是一次部署的决定，不是一个常量。"""
    owner = _owner(heartbeat_at=T0)
    assert owner.is_stale(T0 + timedelta(seconds=5), after=timedelta(seconds=5))
    assert not owner.is_stale(T0 + timedelta(seconds=4), after=timedelta(seconds=5))


def test_asking_whether_it_is_stale_changes_nothing() -> None:
    """**这条是那一节的全部要点。** 会自己收回的归属会把一个只是卡了一下的控制器
    顶掉，而它回来之后仍然以为自己持有 —— 于是同一批工作被两个驱动各推一遍。
    问一次「你还活着吗」不得改动任何一格。"""
    owner = _owner(heartbeat_at=T0)
    before = owner.model_dump()

    assert owner.is_stale(T0 + OWNER_STALE_AFTER * 100) is True
    assert owner.model_dump() == before
    assert (owner.epoch, owner.holder, owner.heartbeat_at) == (1, "controller-a", T0)


def test_the_owner_is_frozen() -> None:
    with pytest.raises(ValidationError):
        _owner().epoch = 2

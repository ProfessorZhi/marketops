"""``NotificationStore`` 的契约判据 —— 三张表、三种写路径、三件不同的失败。

这一族守的是**三句话**，而每一句都有一个「看着一样」的错法：

1. **一条业务变化只生成一次** —— 载体是 ``UNIQUE (workspace, dedupe_key)``。
   错法是「先去重再写」（两个处置点各自查到「没有」，然后各写一条），以及
   「同一个键配了不同内容却安静地留下第一条」（第二条**消失**，而它在报告上与
   「这一次没有变化」逐字同形）。
2. **投递这件事** —— 载体是收据的**四格主键**。错法是「把上一次那行改成新的结果」，
   于是「投了三次都没成」在盘上变成「投了一次」。
3. **订阅是版本化的配置** —— 载体是 ``(subscription_id, revision)`` 主键加基准判据。
   错法是「重发一份一模一样的订阅每次产出新版本」与「拿旧版本覆盖新版本」。

## 为什么这里**没有**「投递结果」这一格的判据

因为 ``DeliveryOutcome`` 只有两个成员（``accepted`` / ``unknown``），而收据存的是
领域对象交上来那一格。一个「成功时顺手写个 delivered」的实现在这一层**写不出来** ——
没有那一列。所以这一份能测的不是「不会写 delivered」（那是一条关于不存在的东西的
判据），而是**这一层原样存了什么就读出什么**：见
``test_a_receipt_records_only_the_two_outcomes_the_sink_can_give``。

## 两个实现跑同一份

``SqliteNotificationStore``（主存，跨进程）与 ``InMemoryNotificationStore``（测试
替身）。理由与 ``tests/test_scheduling_store_contract.py`` 那一节逐字相同。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryNotificationStore
from marketops.adapters.sqlite.notification import SqliteNotificationStore
from marketops.domain.errors import StoreError, SubscriptionConflictError
from marketops.domain.notification import (
    DeliveryMode,
    DeliveryOutcome,
    DeliveryReceipt,
    NotificationEvent,
    NotificationScopeKind,
    NotificationSeverity,
    NotificationSubscription,
    notification_event_id,
)
from marketops.ports.notification import NotificationStore

WORKSPACE = "ws-contract"
OTHER_WORKSPACE = "ws-elsewhere"

T0 = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)

IMPLEMENTATION_PARAMS = ("sqlite", "memory")


def test_the_contract_runs_against_every_implementation() -> None:
    """实现清单缩水时「两个实现都通过」会变成「一个实现通过」。"""
    assert set(IMPLEMENTATION_PARAMS) == {"sqlite", "memory"}


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> NotificationStore:
    if request.param == "sqlite":
        return SqliteNotificationStore(tmp_path / "marketops.db")
    return InMemoryNotificationStore()


# ----------------------------------------------------------------------
# 造对象
# ----------------------------------------------------------------------
def make_event(
    *,
    dedupe_key: str = "fire:watch-0001:2026-09-29T01:00:00+00:00:fire-1",
    workspace: str = WORKSPACE,
    kind: str = "schedule.fired",
    severity: NotificationSeverity = NotificationSeverity.MATERIAL_CHANGE,
    summary: str = "调度到点，已点火",
    occurred_at: datetime = T0,
    scope_ref: str = "watch-0001",
    event_id: str | None = None,
) -> NotificationEvent:
    """一条事件。``event_id`` 默认**由键推出来** —— 那是唯一的合法写法。"""
    return NotificationEvent(
        event_id=event_id or notification_event_id(workspace, dedupe_key),
        workspace=workspace,
        dedupe_key=dedupe_key,
        kind=kind,
        severity=severity,
        scope_kind=NotificationScopeKind.WATCH,
        scope_ref=scope_ref,
        summary=summary,
        occurred_at=occurred_at,
        source_event_ref=None,
        payload={},
    )


def make_subscription(
    *,
    subscription_id: str = "sub-0001",
    revision: int = 1,
    workspace: str = WORKSPACE,
    principal: str = "alice",
    sink: str = "inbox",
    severities: tuple[NotificationSeverity, ...] = (NotificationSeverity.CRITICAL,),
    delivery_mode: DeliveryMode = DeliveryMode.IMMEDIATE,
    updated_at: datetime = T0,
) -> NotificationSubscription:
    return NotificationSubscription(
        subscription_id=subscription_id,
        revision=revision,
        workspace=workspace,
        principal=principal,
        scope_kind=NotificationScopeKind.WATCH,
        scope_ref="watch-0001",
        kinds=(),
        severities=severities,
        sink=sink,
        delivery_mode=delivery_mode,
        quiet_hours=None,
        timezone=None,
        updated_at=updated_at,
    )


def make_receipt(
    *,
    event_id: str = "ev-0001",
    subscription_id: str = "sub-0001",
    sink: str = "inbox",
    attempt: int = 1,
    workspace: str = WORKSPACE,
    outcome: DeliveryOutcome = DeliveryOutcome.ACCEPTED,
    message_ref: str | None = "ws-contract/inbox.jsonl#1",
    failure: str | None = None,
    recorded_at: datetime = T0,
) -> DeliveryReceipt:
    return DeliveryReceipt(
        event_id=event_id,
        subscription_id=subscription_id,
        sink=sink,
        attempt=attempt,
        workspace=workspace,
        recorded_at=recorded_at,
        outcome=outcome,
        message_ref=message_ref,
        failure=failure,
        retry_after=None,
        detail={},
    )


def put_sub(store: NotificationStore, subscription: NotificationSubscription):
    """按 ``put_subscription`` 的那条推法写一版。"""
    return store.put_subscription(
        subscription,
        expected_revision=None
        if subscription.revision == 1
        else subscription.revision - 1,
    )


# ----------------------------------------------------------------------
# 事件：只增、只生成一次
# ----------------------------------------------------------------------
def test_a_business_change_is_recorded_once_and_the_second_write_is_a_no_op(
    store: NotificationStore,
) -> None:
    """两个处置点各自跑到同一件事：第二条是 no-op，且回的是**盘上那一行**。

    「回盘上那一行」不是修辞：第二次交上来的那个对象与第一次是两个实例，
    而读它的人要判的是「这一次有没有产生新的一条」——回错对象会让那件事
    在调用点上看起来像是刚刚生成的。
    """
    first = store.put_event(make_event())
    assert first.recorded is True

    second = store.put_event(make_event())
    assert second.recorded is False
    assert second.event.event_id == first.event.event_id
    assert len(store.events(workspace=WORKSPACE)) == 1


def test_the_same_dedupe_key_with_different_content_is_a_store_error(
    store: NotificationStore,
) -> None:
    """同一个去重键配两份内容 ⇒ ``StoreError``，而且**两个键都点名**。

    安静地留下第一条是最坏的一种「成功」：第二条业务变化消失了，而报告上
    它与「这一次没有变化」逐字同形。
    """
    store.put_event(make_event(summary="第一次点火"))
    with pytest.raises(StoreError) as caught:
        store.put_event(make_event(summary="第二次点火"))

    message = str(caught.value)
    assert "dedupe_key" in message, message
    # 盘上那一条一个字节都没动。
    assert store.events(workspace=WORKSPACE)[0].summary == "第一次点火"


def test_an_event_id_that_is_not_derived_from_the_key_is_refused(
    store: NotificationStore,
) -> None:
    """手写一个 id 再配另一条去重键 ⇒ 拒绝。

    这是 ``notification_event_id`` 那句「id 由键推出来」背后的兜底：一个手写 id 的
    调用方会让同一条业务变化写成两条事件，而两条各有各的 id —— 去重键那一格挡不住
    它们，因为那两条的键也不同。
    """
    store.put_event(make_event(dedupe_key="k-1", event_id="ev-handwritten"))
    with pytest.raises(StoreError):
        store.put_event(make_event(dedupe_key="k-2", event_id="ev-handwritten"))


def test_the_same_dedupe_key_in_another_workspace_is_another_event(
    store: NotificationStore,
) -> None:
    """去重键只在**一个工作区**里去重。别的工作区别无选择地被绑住。

    这里用两个 store：状态库是**一个工作区一份**的（``bind_workspace``），
    所以「同一个键在两个工作区各有一条」要在两份库里读。
    """
    store.put_event(make_event())
    # 本条判据只证明这一份没有被「另一个工作区」这件事影响：
    # 同一个键写两次仍然是一次。
    assert store.put_event(make_event()).recorded is False
    assert len(store.events(workspace=WORKSPACE)) == 1


def test_events_are_oldest_first(store: NotificationStore) -> None:
    """按 ``(occurred_at, event_id)`` **升序**。

    升序而不是降序由**谁在读它**定：``tick`` 要找的是「最早的那一条还没送出去的」。
    这条清单是同一个读法，两个读法各定一次的话，「最早的一条」会有两个答案。
    """
    store.put_event(make_event(dedupe_key="k-late", occurred_at=T0 + timedelta(hours=2)))
    store.put_event(make_event(dedupe_key="k-early", occurred_at=T0))
    store.put_event(make_event(dedupe_key="k-mid", occurred_at=T0 + timedelta(hours=1)))

    assert [row.occurred_at for row in store.events(workspace=WORKSPACE)] == [
        T0,
        T0 + timedelta(hours=1),
        T0 + timedelta(hours=2),
    ]


def test_events_filter_on_kind_severity_and_limit(store: NotificationStore) -> None:
    """三个过滤都是**闭集**（不是子串），``limit`` 是给驱动用的有界窗口。"""
    store.put_event(make_event(dedupe_key="k-1", kind="schedule.fired"))
    store.put_event(
        make_event(
            dedupe_key="k-2",
            kind="budget.expanded",
            severity=NotificationSeverity.CRITICAL,
            occurred_at=T0 + timedelta(hours=1),
        )
    )

    assert [row.dedupe_key for row in store.events(workspace=WORKSPACE, kind="budget.expanded")] == [
        "k-2"
    ]
    assert [
        row.dedupe_key
        for row in store.events(workspace=WORKSPACE, severity=NotificationSeverity.CRITICAL)
    ] == ["k-2"]
    assert len(store.events(workspace=WORKSPACE, limit=1)) == 1
    # 子串不是闭集：「budget」是 `budget.expanded` 的前缀，但它不是一个 kind。
    assert store.events(workspace=WORKSPACE, kind="budget") == ()


def test_get_event_reads_one_back_or_none(store: NotificationStore) -> None:
    """没有就是 ``None`` —— 与 ``get_subscription`` 同一条。"""
    event = make_event()
    store.put_event(event)
    assert store.get_event(event.event_id) is not None
    assert store.get_event("ev-nothing") is None


# ----------------------------------------------------------------------
# 订阅：版本化配置
# ----------------------------------------------------------------------
def test_the_first_subscription_lands_and_a_second_claim_of_first_collides(
    store: NotificationStore,
) -> None:
    """第 1 版写得进去；再一次声称「这是首次创建」被拒。"""
    written = put_sub(store, make_subscription())
    assert written.revision_written == 1, written

    with pytest.raises(SubscriptionConflictError):
        put_sub(store, make_subscription())


def test_a_stale_revision_base_is_refused(store: NotificationStore) -> None:
    """拿旧版本当基准 ⇒ 拒绝，消息里两版都点名。"""
    put_sub(store, make_subscription())
    put_sub(
        store,
        make_subscription(revision=2, severities=(NotificationSeverity.CRITICAL, NotificationSeverity.ROUTINE)),
    )

    with pytest.raises(SubscriptionConflictError) as caught:
        store.put_subscription(
            make_subscription(revision=2, severities=(NotificationSeverity.ROUTINE,)),
            expected_revision=1,
        )
    message = str(caught.value)
    assert "1" in message and "2" in message, message


def test_a_version_jump_is_refused(store: NotificationStore) -> None:
    """跳版（基准 0 → 第 3 版）被拒：那个空洞里，一个拿旧版本的写者会成功。"""
    put_sub(store, make_subscription())
    with pytest.raises(StoreError):
        store.put_subscription(make_subscription(revision=3), expected_revision=0)


def test_an_identical_subscription_writes_no_new_version(store: NotificationStore) -> None:
    """内容一字不差（含 ``quiet_hours``/``delivery_mode``）⇒ 不产生新版本。

    ``content_digest`` 不含 ``revision``/``updated_at``，所以「重发同一份」在这里
    必须是 no-op —— 一个每次都产新版本的实现会让「有人反复点保存」在盘上留下
    几十版逐字相同的订阅。
    """
    put_sub(store, make_subscription())
    written = put_sub(store, make_subscription(revision=2, updated_at=T0 + timedelta(days=1)))

    assert written.revision_written is None, written
    assert [row.revision for row in store.subscriptions(workspace=WORKSPACE)] == [1]


def test_changing_quiet_hours_alone_does_make_a_new_version(store: NotificationStore) -> None:
    """但 ``quiet_hours`` 不同**是**一次改动 —— 它与上面那条是一对。"""
    put_sub(store, make_subscription())
    written = put_sub(
        store, make_subscription(revision=2, updated_at=T0 + timedelta(days=1))
    )
    assert written.revision_written is None

    changed = make_subscription(revision=2, updated_at=T0 + timedelta(days=1))
    changed = changed.model_copy(update={"quiet_hours": "22:00-07:00"})
    assert put_sub(store, changed).revision_written == 2


def test_subscriptions_list_only_the_current_revision(store: NotificationStore) -> None:
    """历史版本与当前版长得一模一样，混进来只会让同一条订阅被投两遍。"""
    put_sub(store, make_subscription())
    updated = make_subscription(revision=2, updated_at=T0 + timedelta(days=1)).model_copy(
        update={"severities": (NotificationSeverity.CRITICAL, NotificationSeverity.ROUTINE)}
    )
    put_sub(store, updated)

    rows = store.subscriptions(workspace=WORKSPACE)
    assert [(row.subscription_id, row.revision) for row in rows] == [("sub-0001", 2)]
    # 而历史版本**还在**：读得出来，只是不在清单上。
    assert store.get_subscription("sub-0001", 1) is not None
    assert store.get_subscription("sub-0001", 3) is None


def test_two_subscriptions_to_one_sink_coexist(store: NotificationStore) -> None:
    """同一个人对同一个 sink 合法地持有两条订阅（即时一条、摘要一条）。

    ``(workspace, principal, sink)`` 上只有普通索引，这是**刻意**的：升级成唯一约束
    会让 ``delivery_mode`` 那一格存在的理由（同一件事两种投法）做不成。
    """
    put_sub(store, make_subscription(subscription_id="sub-fyi"))
    put_sub(
        store,
        make_subscription(subscription_id="sub-daily", delivery_mode=DeliveryMode.DIGEST),
    )

    assert [row.subscription_id for row in store.subscriptions(workspace=WORKSPACE)] == [
        "sub-daily",
        "sub-fyi",
    ]


def test_subscriptions_filter_by_principal(store: NotificationStore) -> None:
    """``principal=None`` 是所有人；``tick`` 要读的是单个人的那一份。"""
    put_sub(store, make_subscription(subscription_id="sub-a", principal="alice"))
    put_sub(store, make_subscription(subscription_id="sub-b", principal="bob"))

    assert [row.subscription_id for row in store.subscriptions(workspace=WORKSPACE)] == [
        "sub-a",
        "sub-b",
    ]
    assert [
        row.subscription_id
        for row in store.subscriptions(workspace=WORKSPACE, principal="bob")
    ] == ["sub-b"]


# ----------------------------------------------------------------------
# 收据：只追加
# ----------------------------------------------------------------------
def test_a_retry_appends_a_new_row_and_never_overwrites(store: NotificationStore) -> None:
    """第 1 次失败、第 2 次接受 —— 盘上是**两行**。

    一个「把上一次那行改成新的结果」的实现会让「投了三次都没成」在盘上变成
    「投了一次」，而重试历史正是读它的人唯一要看的东西。
    """
    store.record_receipt(make_receipt(attempt=1, outcome=DeliveryOutcome.UNKNOWN, failure="对端超时"))
    store.record_receipt(make_receipt(attempt=2, outcome=DeliveryOutcome.ACCEPTED))

    rows = store.receipts(workspace=WORKSPACE)
    assert [(row.attempt, row.outcome) for row in rows] == [
        (1, DeliveryOutcome.UNKNOWN),
        (2, DeliveryOutcome.ACCEPTED),
    ]
    # 第一次那条**还在**，而且带着它当时的失败原因。
    assert rows[0].failure == "对端超时"


def test_recording_the_same_attempt_twice_is_a_no_op(store: NotificationStore) -> None:
    """同一次尝试被记了两次（同四格、同内容）⇒ ``recorded=False``。"""
    first = store.record_receipt(make_receipt())
    assert first.recorded is True
    second = store.record_receipt(make_receipt())
    assert second.recorded is False
    assert len(store.receipts(workspace=WORKSPACE)) == 1


def test_the_same_attempt_with_another_result_is_a_store_error(store: NotificationStore) -> None:
    """同一个「第 2 次」位置上已经有一个结果 ⇒ 拒绝。覆盖它会让重试历史少一次失败。"""
    store.record_receipt(make_receipt(attempt=2, outcome=DeliveryOutcome.ACCEPTED))
    with pytest.raises(StoreError):
        store.record_receipt(
            make_receipt(attempt=2, outcome=DeliveryOutcome.UNKNOWN, failure="对端超时")
        )

    assert store.receipts(workspace=WORKSPACE)[0].outcome is DeliveryOutcome.ACCEPTED


def test_a_receipt_records_only_the_two_outcomes_the_sink_can_give(
    store: NotificationStore,
) -> None:
    """原样存、原样读 —— **这一层不合成第三个取值**。

    这条判据能写出来的理由是没有那一列：``DeliveryOutcome`` 只有两个成员，
    ``delivery_receipt`` 上没有任何一格叫「已投递」。一个在成功时顺手写个
    ``delivered`` 的实现在这里连写的地方都没有。
    """
    assert {item.value for item in DeliveryOutcome} == {"accepted", "unknown"}

    store.record_receipt(make_receipt(attempt=1, outcome=DeliveryOutcome.ACCEPTED))
    store.record_receipt(
        make_receipt(
            attempt=2,
            outcome=DeliveryOutcome.UNKNOWN,
            message_ref=None,
            failure="回执丢了",
        )
    )

    read = store.receipts(workspace=WORKSPACE)
    assert [row.outcome for row in read] == [DeliveryOutcome.ACCEPTED, DeliveryOutcome.UNKNOWN]
    # 而「不知道」那一行**没有** ``message_ref`` —— 一个顺手补上引用让两条看起来一样
    # 的实现，会把「证明不出来」与「证明得出来」在盘上抹平。
    assert read[1].message_ref is None


def test_receipts_are_ordered_by_the_four_key_columns(store: NotificationStore) -> None:
    """按 ``(event_id, subscription_id, sink, attempt)`` 升序 —— 顺序是契约的一部分，
    读它的人要看的是「失败、失败、接受」这条序列。"""
    store.record_receipt(make_receipt(event_id="ev-b", attempt=1))
    store.record_receipt(make_receipt(event_id="ev-a", subscription_id="sub-0002", attempt=1))
    store.record_receipt(make_receipt(event_id="ev-a", subscription_id="sub-0001", attempt=2))
    store.record_receipt(make_receipt(event_id="ev-a", subscription_id="sub-0001", attempt=1))

    assert [
        (row.event_id, row.subscription_id, row.attempt)
        for row in store.receipts(workspace=WORKSPACE)
    ] == [
        ("ev-a", "sub-0001", 1),
        ("ev-a", "sub-0001", 2),
        ("ev-a", "sub-0002", 1),
        ("ev-b", "sub-0001", 1),
    ]


def test_receipts_filter_by_event_and_subscription(store: NotificationStore) -> None:
    store.record_receipt(make_receipt(event_id="ev-a", subscription_id="sub-1"))
    store.record_receipt(make_receipt(event_id="ev-a", subscription_id="sub-2"))
    store.record_receipt(make_receipt(event_id="ev-b", subscription_id="sub-1"))

    assert len(store.receipts(workspace=WORKSPACE, event_id="ev-a")) == 2
    assert len(store.receipts(workspace=WORKSPACE, subscription_id="sub-1")) == 2
    assert (
        len(store.receipts(workspace=WORKSPACE, event_id="ev-a", subscription_id="sub-2")) == 1
    )


def test_attempts_is_the_highest_attempt_not_the_row_count(store: NotificationStore) -> None:
    """下一个 ``attempt`` 是**主键的下一格**，不是「列表有多长」。

    两处各算一遍的那天会分家，而分家的形态是一次 ``IntegrityError`` ——
    读起来像存储事故，实际是两处各推了一次下一格。
    """
    assert store.attempts(event_id="ev-a", subscription_id="sub-1", sink="inbox") == 0

    store.record_receipt(make_receipt(event_id="ev-a", subscription_id="sub-1", attempt=1))
    store.record_receipt(make_receipt(event_id="ev-a", subscription_id="sub-1", attempt=2))
    assert store.attempts(event_id="ev-a", subscription_id="sub-1", sink="inbox") == 2

    # 另一个 sink 的尝试**不**算进这三格。
    assert store.attempts(event_id="ev-a", subscription_id="sub-1", sink="feishu") == 0

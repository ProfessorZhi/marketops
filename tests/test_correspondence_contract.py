"""收信侧契约：**先压制、后分类、再落库、最后才推游标** —— 由本文件执行。

## 这里守的四件事，每一件都有它自己的失败形状

1. **分类器不得翻掉一条抑制**（``test_classification_cannot_override_an_existing_suppression``）。
   一个读缓存的实现在盘上完全正常，而它的病是「他刚投诉完、我们又给他发了一封」
   —— 与 ``tests/test_outreach_store_contract.py`` 里那条
   ``test_suppression_is_a_query_not_a_cache`` 是同一条判据的两层。
2. **分类失败不得丢掉原件**（``test_a_classifier_that_raises_still_lands_the_original``）。
   §7 逐字：「分类失败不得丢失原件」。丢掉之后，重试没有东西可重试 ——
   而记录上它与「这一批没有来信」长得一样。
3. **游标不得越过没落库的那一条**（``test_a_persist_failure_stops_the_cursor_before_that_item``）。
   §7 逐字：「先持久化、再推进游标」。越过一条，就等于**丢掉它**。
4. **重放不得产生第二条交接**（``test_a_replayed_batch_writes_no_second_handoff``）。
   webhook 重试、游标回退、两个驱动并发都会造出重放，而它的症状（销售收到两条
   同一封回信的交接）在报告上看起来只是「多了一条」。

## 两个实现，同一组判据

``pytest.fixture(params=["sqlite", "memory"])`` 与
``tests/test_outreach_store_contract.py`` 是同一个形状：``sqlite`` 是主存，
``memory`` 是测试替身。呈现这一层的契约的不是 ``application/correspondence.py``
自己的声明，而是**同一组判据同时看着两个存储实现**。

**不联网。** 分类器一律是测试替身（``_FixedClassifier``）：这一层唯一认识的
分类器形状是 ``ports.correspondence.ReplyClassifier``，而它一个网络调用都没有。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryOutreachStore
from marketops.adapters.sqlite.outreach import SqliteOutreachStore
from marketops.application.correspondence import CONSERVATIVE_HOLD, ingest
from marketops.domain.errors import StoreError
from marketops.domain.outreach import (
    KIND_DISPOSITION,
    SUPPRESSING_KINDS,
    CorrespondenceDisposition,
    CorrespondenceEvent,
    CorrespondenceKind,
    DraftRef,
    HandoffState,
    MessageState,
    OutboundAttempt,
    OutreachStage,
    RecipientSuppression,
    SalesHandoff,
    SuppressionReason,
    Wave,
    WaveState,
)
from marketops.domain.util import check_canonical_id, sha256_text
from marketops.ports.correspondence import (
    Classification,
    CorrespondenceBatch,
    CorrespondenceItem,
    CursorState,
    ReplyClassifier,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)

WORKSPACE = "ws-example-uk"
WAVE = "wave-0001"
CAMPAIGN = "campaign-0001"
SCENE = "scene-0001"
SENDBOX = "outreach@marketops.example"
RECIPIENT = "buyer@example.com"
OTHER_RECIPIENT = "second@example.com"
SEND_KEY = "send-0001"
OTHER_SEND_KEY = "send-0002"


def _digest(text: str) -> str:
    """一个合法的 64 位摘要。**从内容算**，不是一串固定的 a。

    与 ``tests/test_outreach_store_contract.py`` 的同名函数同一条理由：写死一串
    ``"a" * 64`` 也能过校验，但两处不同的内容会带同一个摘要 —— 而下面好几条判据
    判的就是摘要。
    """
    return sha256_text(text)


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path: Path):
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteOutreachStore(tmp_path / "state" / "marketops.db")
    return InMemoryOutreachStore()


# --- 构造器 -------------------------------------------------------------------


def make_wave(*, wave_id: str = WAVE, **overrides) -> Wave:
    payload = {
        "wave_id": wave_id,
        "campaign_id": CAMPAIGN,
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "state": WaveState.SENDING,
        "manifest_sha256": _digest("manifest-0001"),
        "revision": 0,
        "created_at": T0,
        "updated_at": T0,
    }
    payload.update(overrides)
    return Wave(**payload)


def make_attempt(
    *,
    send_key: str = SEND_KEY,
    recipient: str = RECIPIENT,
    wave_id: str = WAVE,
    **overrides,
) -> OutboundAttempt:
    """一次**已经送出去**的尝试 —— 回信总是对应一次真的发出去了的发送。"""
    payload = {
        "send_key": send_key,
        "workspace": WORKSPACE,
        "wave_id": wave_id,
        "draft_id": "draft-0001",
        "draft_version": 1,
        "draft": DraftRef(draft_id="draft-0001", draft_version=1, body_sha256=_digest("body")),
        "route_ref": "route-0001",
        "account_id": "account-0001",
        "recipient": recipient,
        "sendbox": SENDBOX,
        "stage": OutreachStage.STAGE_1,
        "authorization_id": "auth-0001",
        "manifest_sha256": _digest("manifest-0001"),
        "state": MessageState.DELIVERED,
        "started_at": T0,
        "finished_at": T0,
        "provider_message_id": "out-0001",
    }
    payload.update(overrides)
    return OutboundAttempt(**payload)


def make_item(**overrides) -> CorrespondenceItem:
    payload = {
        "provider_message_id": "msg-0001",
        "sendbox": SENDBOX,
        "raw_ref": "raw/mail/msg-0001.eml",
        "raw_sha256": _digest("raw-message-0001"),
        "received_at": T0,
        "sender": RECIPIENT,
        "subject": "Re: a short note",
        "body": "Thanks for the note.",
        "send_key": SEND_KEY,
    }
    payload.update(overrides)
    return CorrespondenceItem(**payload)


def make_batch(items=(), **overrides) -> CorrespondenceBatch:
    payload = {"items": tuple(items), "cursor": "cursor-0002", "from_cursor": "cursor-0001"}
    payload.update(overrides)
    return CorrespondenceBatch(**payload)


def seed_send(store, *, send_key: str = SEND_KEY, recipient: str = RECIPIENT, wave_id: str = WAVE):
    """库里先有一条「这封信已经发出去了」的事实 —— 回信靠它才关联得上。

    批次只在没有的时候建：``put_wave(expected_revision=None)`` 的承诺是「这是我建
    的第一个」，第二次用它写同一个 ``wave_id`` 是冲突（这是对的 —— 那条判据不看
    内容是否相同）。同一批里的第二封信属于**同一个** batch，所以这里先读一次。
    """
    if store.wave(wave_id) is None:
        store.put_wave(make_wave(wave_id=wave_id), expected_revision=None)
    store.begin_attempt(
        make_attempt(send_key=send_key, recipient=recipient, wave_id=wave_id), at=T0
    )


# --- 测试替身 -----------------------------------------------------------------


class _FixedClassifier(ReplyClassifier):
    """一个**没有网络**的分类器。三种行为：给一个分类、弃权、抛异常。"""

    def __init__(self, kind: CorrespondenceKind | None = None, *, reason: str = "", raises=None,
                 returns=None) -> None:
        self._kind = kind
        self._reason = reason
        self._raises = raises
        self._returns = returns
        self.seen: list[CorrespondenceEvent] = []
        self.calls = 0

    def classify(self, *, event: CorrespondenceEvent) -> Classification:
        self.seen.append(event)
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        if self._returns is not None:
            return self._returns
        return Classification(kind=self._kind, reason=self._reason)


class _RecordingClassifier(_FixedClassifier):
    """分类器的每一次调用都记进同一本流水 —— 与 ``_RecordingStore`` 共用一个 log。"""

    def __init__(self, log: list[tuple[str, str]], **kwargs) -> None:
        super().__init__(**kwargs)
        self._log = log

    def classify(self, *, event: CorrespondenceEvent) -> Classification:
        self._log.append(("classify", event.provider_message_id))
        return super().classify(event=event)


class _RecordingStore:
    """把 ``is_suppressed`` 记进流水的存储。其余方法原样转给真的那个。

    它存在的理由是**次序**：本层的「先压制、后分类」不是一句注释，而是调用次序，
    而次序只有记下来才可判。
    """

    def __init__(self, store, log: list[tuple[str, str]]) -> None:
        self._store = store
        self._log = log

    def is_suppressed(self, recipient: str, *, at) -> bool:
        self._log.append(("is_suppressed", recipient))
        return self._store.is_suppressed(recipient, at=at)

    def __getattr__(self, name):
        return getattr(self._store, name)


class _FlakyStore:
    """第 ``fail_on_call`` 次 ``put_event`` 抛。**它守的是游标。**"""

    def __init__(self, store, *, fail_on_call: int) -> None:
        self._store = store
        self._limit = fail_on_call
        self.calls = 0

    def put_event(self, event):
        self.calls += 1
        if self.calls == self._limit:
            raise StoreError("模拟：盘写不进去了")
        return self._store.put_event(event)

    def __getattr__(self, name):
        return getattr(self._store, name)


# ======================================================================
# 端口那一半：条目与批次的形状
# ======================================================================


def test_an_item_that_the_source_did_not_classify_is_allowed() -> None:
    """``kind=None`` 是**常态**，不是漏填。

    webhook 递过来的原文里没有分类，而「不知道」绝不能被迫编造：一个要求 ``kind``
    必填的契约会逼着 webhook 实现把每封信都标成 ``HUMAN_REPLY``，而那个默认值恰好
    是最贵的那个（它会产出销售交接）。
    """
    item = make_item(kind=None)
    assert item.kind is None


def test_a_kind_that_is_not_a_member_of_the_closed_set_is_refused() -> None:
    """一个字符串形式的分类会让下游的映射表查不到它。

    判据在**构造时**而不是查表时：查表时抛出来的 ``KeyError`` 会在一批处理到一半
    的时候发生，而那时前面几条已经落库了。
    """
    with pytest.raises(ValueError, match="CorrespondenceKind"):
        make_item(kind="human_reply")


def test_an_item_without_an_original_is_refused() -> None:
    """原件的那两样（引用 + 摘要）是**必填**的 —— §7：「分类失败不得丢失原件」。"""
    with pytest.raises(ValueError, match="raw_ref"):
        make_item(raw_ref="   ")
    with pytest.raises(ValueError, match="raw_sha256"):
        make_item(raw_sha256="not-a-digest")


def test_a_sendbox_that_is_not_normalised_is_refused() -> None:
    """去重键是 ``(sendbox, provider_message_id)``，两个写法的同一个信箱会漏去重。"""
    with pytest.raises(ValueError, match="sendbox"):
        make_item(sendbox="Outreach@MarketOps.Example")


def test_a_dead_cursor_is_not_an_empty_batch() -> None:
    """**两个不同的结果，两个字段说得出来。**

    「提供方作废了游标」与「这一段没有来信」在报告上必须分得开：前者的处置是
    重新对齐，后者是继续往前读。压成一个 ``bool`` 的实现会让调用方只能猜，而猜错
    的方向是**丢掉一段来信**。
    """
    dead = make_batch((), cursor="", cursor_state=CursorState.INVALIDATED, note="提供方说这个游标太旧了")
    assert dead.cursor_state is CursorState.INVALIDATED
    assert dead.cursor == ""
    assert dead.items == ()

    empty = make_batch(())
    assert empty.cursor_state is CursorState.ADVANCED


def test_a_dead_cursor_may_not_also_carry_a_new_one() -> None:
    """「这个游标不算数了」与「这是下一个游标」同时为真时，调用方选哪个都有一半错。"""
    with pytest.raises(ValueError, match="不得给出新游标"):
        make_batch((), cursor="cursor-0003", cursor_state=CursorState.INVALIDATED, note="旧的作废了")
    with pytest.raises(ValueError, match="必须说明为什么"):
        make_batch((), cursor="", cursor_state=CursorState.INVALIDATED)
    with pytest.raises(ValueError, match="不得同时给条目"):
        make_batch(
            (make_item(),), cursor="", cursor_state=CursorState.INVALIDATED, note="旧的作废了"
        )


def test_abstention_is_a_named_conclusion_not_an_empty_one() -> None:
    """弃权必须说得出为什么。

    一条不写理由的弃权与一次分类器崩溃在记录上长得一样 —— 而前者的处置是
    「换个模型再看看」，后者是「先修好它」。
    """
    with pytest.raises(ValueError, match="弃权必须说明为什么"):
        Classification()

    abstained = Classification(reason="正文只有一句问候，看不出意图")
    assert abstained.abstained is True
    assert abstained.kind is None

    decided = Classification(kind=CorrespondenceKind.HUMAN_REPLY, reason="对方在问价格")
    assert decided.abstained is False


# ======================================================================
# 应用那一半：三种「不抑制」的落法
# ======================================================================


def test_the_source_kind_is_not_second_guessed(store) -> None:
    """来源自己给了分类的条目**不调分类器**：那是一条事实，不是一次推断。

    一封退信是收信服务器按 RFC 回的 —— 让一个模型「再确认一下」，只是给了它一次
    推翻确定事实的机会。而 ``AUTO_REPLY`` 的处置是 ``RECORD_ONLY``：§7 逐字要求
    自动回复**不是**一次有效的人类回复，因此既不停跟进也不产生交接。
    """
    seed_send(store)
    classifier = _FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY)

    receipt = ingest(
        make_batch((make_item(kind=CorrespondenceKind.AUTO_REPLY),)),
        store=store,
        classifier=classifier,
        workspace=WORKSPACE,
        at=T1,
    )

    assert classifier.calls == 0, "来源已经给了分类，分类器不该被调用"
    assert receipt.advanced is True
    assert receipt.cursor == "cursor-0002"
    assert [event.disposition for event in receipt.ingested] == [
        CorrespondenceDisposition.RECORD_ONLY
    ]
    assert store.handoffs(workspace=WORKSPACE) == ()
    assert store.suppressions(RECIPIENT) == ()


def test_a_hard_bounce_is_not_a_human_reply(store) -> None:
    """一封退信停不了跟进、也不该产生交接。

    它只有一种落法：按 ``KIND_DISPOSITION`` 走，而那张表把它记成 ``SUPPRESS`` ——
    见下一条。这一条守的是「分类器不参与」，所以断言的是**没有交接**。
    """
    seed_send(store)
    classifier = _FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY)

    receipt = ingest(
        make_batch((make_item(kind=CorrespondenceKind.HARD_BOUNCE),)),
        store=store,
        classifier=classifier,
        workspace=WORKSPACE,
        at=T1,
    )

    assert classifier.calls == 0
    assert store.handoffs(workspace=WORKSPACE) == ()
    assert receipt.ingested[0].disposition is CorrespondenceDisposition.SUPPRESS


def test_a_valid_human_reply_produces_a_handoff_with_evidence(store) -> None:
    """§7 逐字：「有效人类回信 → 停止相关自动跟进、产出一条有证据的 SalesHandoff」。

    三件事各自落在哪：``disposition=HANDOFF``（这就是「停止自动跟进」在本仓的
    落地）、一条 ``PENDING_LOCAL`` 的交接、以及**非空**的 ``evidence_ids``
    （``SalesHandoff`` 会拒绝空的）。``reply_body`` 逐字保留 —— 交接的核心就是
    让销售读原文。
    """
    seed_send(store)
    body = "Yes — could you send the numbers for our two pitches?"
    classifier = _FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY, reason="对方在问具体的事")

    receipt = ingest(
        make_batch((make_item(subject="Re: numbers", body=body),)),
        store=store,
        classifier=classifier,
        workspace=WORKSPACE,
        at=T1,
    )

    assert classifier.calls == 1
    assert receipt.ingested[0].disposition is CorrespondenceDisposition.HANDOFF
    assert receipt.ingested[0].handoff_state is HandoffState.PENDING_LOCAL

    handoffs = store.handoffs(workspace=WORKSPACE)
    assert len(handoffs) == 1
    handoff = handoffs[0]
    assert handoff.event_id == receipt.ingested[0].event_id
    assert handoff.workspace == WORKSPACE
    assert handoff.scene_id == SCENE
    assert handoff.campaign_id == CAMPAIGN
    assert handoff.account_id == "account-0001"
    assert handoff.route_ref == "route-0001"
    assert handoff.recipient == RECIPIENT
    assert handoff.reply_body == body, "回信正文必须逐字保留"
    assert handoff.state is HandoffState.PENDING_LOCAL
    assert handoff.evidence_ids, "SalesHandoff 会拒绝空证据"
    for evidence_id in handoff.evidence_ids:
        # 证据 id 是可复算的规范 id（见 application/correspondence.py 的
        # 「关于证据 id」）：它指向那条原件，而不是一枚伪造出来的 Evidence 行。
        assert check_canonical_id(evidence_id, "evidence_id") == evidence_id
        assert evidence_id.startswith("correspondence-")

    assert store.suppressions(RECIPIENT) == (), "一次有效回信不是抑制"


def test_a_refusal_stops_followup_without_suppressing(store) -> None:
    """明确拒绝 → ``STOP_FOLLOWUP``，**不进抑制**。

    ## 这里钉的是「停跟进」与「进抑制」是两条规则

    拒绝的处置是 ``STOP_FOLLOWUP``，所以本层不写抑制行（抑制是跨 Campaign 长期
    有效的事实，而拒绝是关于这次接洽的 —— 理由写在
    ``application/correspondence.py`` 的模块 docstring 里）。而它**照样要停止这个人
    后续的自动外联**，那件事落在 ``application/outreach.py::stopped_by_reply`` 上，
    由 ``tests/test_outreach_contract.py`` 钉住。

    下面这一条断言的是 domain 的两张表**不可能漂**：``SUPPRESSING_KINDS`` 由处置表
    推出来，而这个分类的处置不是 ``SUPPRESS``，所以它不在抑制名单里。这曾经是一个
    真实的矛盾（集合里有它、处置表里没有），修法不是把两张都改对，是让它只剩一个
    来源。
    """
    assert (
        KIND_DISPOSITION[CorrespondenceKind.REFUSAL] is CorrespondenceDisposition.STOP_FOLLOWUP
    )
    assert CorrespondenceKind.REFUSAL not in SUPPRESSING_KINDS
    assert SUPPRESSING_KINDS == frozenset(
        kind
        for kind, disposition in KIND_DISPOSITION.items()
        if disposition is CorrespondenceDisposition.SUPPRESS
    )

    seed_send(store)
    receipt = ingest(
        make_batch((make_item(kind=CorrespondenceKind.REFUSAL),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.ingested[0].disposition is CorrespondenceDisposition.STOP_FOLLOWUP
    assert store.suppressions(RECIPIENT) == (), "拒绝不是抑制"
    assert store.handoffs(workspace=WORKSPACE) == ()


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (CorrespondenceKind.UNSUBSCRIBE, SuppressionReason.UNSUBSCRIBE),
        (CorrespondenceKind.COMPLAINT, SuppressionReason.COMPLAINT),
        (CorrespondenceKind.HARD_BOUNCE, SuppressionReason.HARD_BOUNCE),
    ],
    ids=lambda value: value.value,
)
def test_unsubscribe_complaint_and_hard_bounce_suppress_immediately(store, kind, reason) -> None:
    """三种分类**立刻**落一行持久抑制，理由映射到 ``SuppressionReason``。

    理由不是从 ``CorrespondenceKind`` 里「顺手拿来用」的：两者是两个闭集
    （``SuppressionReason`` 还有 ``OPERATOR``，业务方手工加的那一种），而它们的
    取值今天同名。
    """
    seed_send(store)
    receipt = ingest(
        make_batch((make_item(kind=kind),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.ingested[0].disposition is CorrespondenceDisposition.SUPPRESS
    assert receipt.ingested[0].suppression_note == ""
    rows = store.suppressions(RECIPIENT)
    assert len(rows) == 1
    assert rows[0].reason is reason
    assert rows[0].source_ref == receipt.ingested[0].event_id
    assert rows[0].suppressed_at == T1
    assert store.is_suppressed(RECIPIENT, at=T1) is True
    assert store.handoffs(workspace=WORKSPACE) == ()


def test_a_soft_bounce_is_recorded_and_never_suppresses(store) -> None:
    """软退信 → ``RECORD_ONLY``。**一个满了的邮箱不是一个人离开了。**

    这一条与上一条成对：漏掉软退信的抑制，症状是这个人再也不会被联系到；
    多写一条软退信的抑制，症状是同样的事 —— 而记录上两条都显示「分类正确」。
    """
    seed_send(store)
    receipt = ingest(
        make_batch((make_item(kind=CorrespondenceKind.SOFT_BOUNCE),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.ingested[0].disposition is CorrespondenceDisposition.RECORD_ONLY
    assert store.suppressions(RECIPIENT) == ()
    assert store.handoffs(workspace=WORKSPACE) == ()


# ======================================================================
# 应用那一半：分类的三种不成
# ======================================================================


def test_a_classifier_that_raises_still_lands_the_original(store) -> None:
    """§7 逐字：「分类失败不得丢失原件」—— 而理由必须说得出**是哪一种失败**。

    ``分类失败``（模型抛了 / 返回了读不懂的东西）与 ``弃权``（模型说它分不出来）
    是两件事：前者的处置是「先修好它」，后者是「换个办法再看看」。压成一个
    ``MANUAL_REVIEW`` 的话，读队列的人分不出该去修哪个。
    """
    seed_send(store)
    item = make_item(body="Please take me off this list.")
    classifier = _FixedClassifier(raises=RuntimeError("模型网关 502"))

    receipt = ingest(
        make_batch((item,)),
        store=store,
        classifier=classifier,
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.advanced is True, "分类失败不是落库失败：游标照常前进"
    assert classifier.calls == 1
    landed = receipt.ingested[0]
    assert landed.disposition is CorrespondenceDisposition.MANUAL_REVIEW
    assert landed.hold_reason.startswith("分类失败"), "理由必须开门见山说清是哪一种失败"
    assert "502" in landed.hold_reason

    events = store.events(workspace=WORKSPACE)
    assert len(events) == 1
    assert events[0].raw_ref == item.raw_ref
    assert events[0].raw_sha256 == item.raw_sha256
    assert events[0].body == item.body
    assert store.handoffs(workspace=WORKSPACE) == ()
    assert store.suppressions(RECIPIENT) == ()


def test_an_abstaining_classifier_is_neither_a_reply_nor_a_record(store) -> None:
    """弃权**不得**当作 ``HUMAN_REPLY``（会产出交接），也**不得**当作
    ``RECORD_ONLY``（会把一封「请不要再联系我」记一笔就放下）。

    它落到 ``MANUAL_REVIEW`` —— 而为了让读队列的人知道是分类器弃权，理由里
    逐字带上分类器给的那句话。
    """
    seed_send(store)
    classifier = _FixedClassifier(kind=None, reason="正文只有一句含糊的话")

    receipt = ingest(
        make_batch((make_item(),)),
        store=store,
        classifier=classifier,
        workspace=WORKSPACE,
        at=T1,
    )

    landed = receipt.ingested[0]
    assert landed.disposition is CorrespondenceDisposition.MANUAL_REVIEW
    assert landed.hold_reason.startswith("分类器弃权"), "与「分类失败」的理由不得混成一句"
    assert "正文只有一句含糊的话" in landed.hold_reason
    assert store.events(workspace=WORKSPACE)[0].hold_reason == landed.hold_reason
    assert store.handoffs(workspace=WORKSPACE) == ()


def test_a_classifier_returning_something_unreadable_is_a_failure_not_an_abstention(store) -> None:
    """读不懂的返回值与一次崩溃是同一件事。

    一个「按属性取值、取不到就当 None」的调用方会在这里**静默弃权** ——
    而那时读队列的人看到的是「模型拿不准」，于是一封本来可以自动处置的回信被
    送进人工队列，理由还是错的。
    """
    seed_send(store)
    classifier = _FixedClassifier(returns=CorrespondenceKind.HUMAN_REPLY)  # type: ignore[arg-type]

    receipt = ingest(
        make_batch((make_item(),)),
        store=store,
        classifier=classifier,
        workspace=WORKSPACE,
        at=T1,
    )

    assert "分类失败" in receipt.ingested[0].hold_reason
    assert "Classification" in receipt.ingested[0].hold_reason


# ======================================================================
# 应用那一半：关联不上、替代联系人、抑制的边界
# ======================================================================


@pytest.mark.parametrize(
    "send_key",
    ["", "Not A Key!"],
    ids=["empty", "malformed"],
)
def test_a_reply_that_cannot_be_linked_is_kept_for_a_human(store, send_key: str) -> None:
    """关联不上（``send_key`` 为空或形状用不了）→ 落库 + ``MANUAL_REVIEW``，理由说清
    「不知道对应哪封信」。

    **绝不能丢弃**（§7）。丢弃的代价不是「少一条记录」，而是那封「请不要再联系我」
    被记都不记一笔 —— 而它在报告上看起来与「这一批没有来信」逐字相同。
    """
    seed_send(store)
    item = make_item(send_key=send_key)

    receipt = ingest(
        make_batch((item,)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    landed = receipt.ingested[0]
    assert landed.disposition is CorrespondenceDisposition.MANUAL_REVIEW
    assert "关联不上" in landed.hold_reason
    assert "哪一封信" in landed.hold_reason or "形状" in landed.hold_reason

    events = store.events(workspace=WORKSPACE)
    assert len(events) == 1
    assert events[0].send_key == "", "形状不对的 send_key 不得写进事件"
    assert events[0].raw_sha256 == item.raw_sha256
    assert store.handoffs(workspace=WORKSPACE) == ()
    assert store.suppressions(RECIPIENT) == ()


def test_a_send_key_that_matches_no_attempt_is_kept_for_a_human(store) -> None:
    """``send_key`` 有、但库里读不到那次发送 → 同样交给人，理由不同。"""
    receipt = ingest(
        make_batch((make_item(send_key="send-9999"),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert "关联不到任何一次发送" in receipt.ingested[0].hold_reason
    assert receipt.ingested[0].disposition is CorrespondenceDisposition.MANUAL_REVIEW


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # 两个条件同时成立 → 人工审核。
        (
            "Sorry, I'm leaving the club. Please contact our new grounds lead "
            "at successor@example.com and they'll take it from here.",
            CorrespondenceDisposition.MANUAL_REVIEW,
        ),
        # 只有线索短语（套话），没有别的地址 → 按正常路走。
        (
            "Thanks. Please contact us if anything changes on your side.",
            CorrespondenceDisposition.HANDOFF,
        ),
        # 只有地址（签名/页脚到处都是），没有线索短语 → 按正常路走。
        (
            "Sounds interesting. My details are below.\n--\nbuyer@example.com",
            CorrespondenceDisposition.HANDOFF,
        ),
        # 线索 + 两个别的地址 → **不认**（这一条是刻意的漏）。
        (
            "Please contact either deputy@example.com or successor@example.com.",
            CorrespondenceDisposition.HANDOFF,
        ),
    ],
    ids=[
        "cue-and-one-address",
        "cue-only",
        "address-only",
        "cue-and-two-addresses-is-a-deliberate-miss",
    ],
)
def test_an_alternate_contact_goes_to_a_human_and_is_detected_conservatively(
    store, body: str, expected: CorrespondenceDisposition
) -> None:
    """§7 逐字：「字面上的替代联系人 → awaiting_Member_A_confirmation，**不自动重发或
    新建外联**」。

    判据是**保守的**：字面线索短语 **且** 恰好一个与发件人不同的地址，两个条件
    同时成立才算。宁可不认（那封回信走它本来的处置）也不要猜 —— 而下面三条反例
    是这条判据的**正对照**：它们证明「认出来了」不是靠一句套话或者一个签名地址。

    ``cue-and-two-addresses`` 那一条是**刻意的漏**，它值得单独说一句：正文里有
    两个人的地址时，「该换给谁」正是这里不能猜的那件事（而猜错的后果是把销售
    引到一个错的联系人身上）。所以它按正常路走。判据只负责**不猜**，不负责认全。
    """
    seed_send(store)
    receipt = ingest(
        make_batch((make_item(body=body),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    landed = receipt.ingested[0]
    assert landed.disposition is expected
    if expected is CorrespondenceDisposition.MANUAL_REVIEW:
        assert "替代联系人" in landed.hold_reason
        assert "人工确认" in landed.hold_reason
        assert store.handoffs(workspace=WORKSPACE) == (), "不得自动新建外联"
        assert store.suppressions(RECIPIENT) == ()
    else:
        assert landed.disposition is CorrespondenceDisposition.HANDOFF


def test_classification_cannot_override_an_existing_suppression(store) -> None:
    """**本文件的第 1 条判据。** 一个已经在抑制名单上的收件人的来信，不能因为被
    分成 ``HUMAN_REPLY`` 而复活。

    抑制是**查**出来的（``store.is_suppressed(recipient, at=at)``），而且查在
    分类**之前**：分类器在这里没有否决权 —— 它能让这条记录带着理由去人工那里，
    仅此而已。一条「分类结果优先」的实现，症状是那个刚投诉完的人**又收到一封信**，
    而盘上它看起来完全正常。
    """
    seed_send(store)
    store.put_suppression(
        RecipientSuppression(
            recipient=RECIPIENT,
            reason=SuppressionReason.COMPLAINT,
            source_ref="ticket-0001",
            suppressed_at=T0,
        )
    )

    receipt = ingest(
        make_batch((make_item(),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.holds[0].already_suppressed is True
    assert receipt.holds[0].disposition is CorrespondenceDisposition.MANUAL_REVIEW
    landed = receipt.ingested[0]
    assert landed.disposition is CorrespondenceDisposition.MANUAL_REVIEW
    assert "已经在抑制名单上" in landed.hold_reason
    assert store.handoffs(workspace=WORKSPACE) == (), "抑制不因一次分类结果而失效"
    assert len(store.suppressions(RECIPIENT)) == 1, "也没有写第二条抑制"


def test_suppression_is_asked_at_the_moment_it_is_told_to_ask(store) -> None:
    """``at`` 是真的传给了存储的 —— 不去读一份「他是不是被抑制过」的影子状态。

    同一个人、同一条抑制、两次不同的 ``at``：解除之前算数，之后不算数。一个
    读缓存的实现答不出第二个问题，而它答错的后果是**这个人被解除之后永远收不到
    信**（或者反过来：解除了却还被压着）。
    """
    seed_send(store)
    store.put_suppression(
        RecipientSuppression(
            recipient=RECIPIENT,
            reason=SuppressionReason.COMPLAINT,
            source_ref="ticket-0001",
            suppressed_at=T0,
            released_at=T1,
        )
    )

    before = ingest(
        make_batch((make_item(provider_message_id="msg-before"),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T0 + timedelta(minutes=1),
    )
    after = ingest(
        make_batch((make_item(provider_message_id="msg-after"),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1 + timedelta(minutes=1),
    )

    assert before.holds[0].already_suppressed is True
    assert after.holds[0].already_suppressed is False
    assert before.ingested[0].disposition is CorrespondenceDisposition.MANUAL_REVIEW
    assert after.ingested[0].disposition is CorrespondenceDisposition.HANDOFF


# ======================================================================
# 应用那一半：次序与游标
# ======================================================================


def test_hold_happens_before_the_classifier_runs(store) -> None:
    """§7 逐字：「新到的人类回信应先保守地暂停后续外联，**然后再分类**」。

    落地成一条**可判的次序**：这一批的每一条都在分类器被碰到之前问过存储
    「这个人在抑制名单上吗」。一个「边分类边压制」的实现，在第一条与第二条之间
    会对第二条先分类 —— 而它在报告上完全正常。
    """
    seed_send(store, send_key=SEND_KEY, recipient=RECIPIENT)
    seed_send(store, send_key=OTHER_SEND_KEY, recipient=OTHER_RECIPIENT)

    log: list[tuple[str, str]] = []
    watched = _RecordingStore(store, log)
    classifier = _RecordingClassifier(log, kind=CorrespondenceKind.SOFT_BOUNCE)

    ingest(
        make_batch(
            (
                make_item(provider_message_id="msg-a"),
                make_item(provider_message_id="msg-b", send_key=OTHER_SEND_KEY),
            )
        ),
        store=watched,
        classifier=classifier,
        workspace=WORKSPACE,
        at=T1,
    )

    first_classify = log.index(("classify", "msg-a"))
    assert ("is_suppressed", RECIPIENT) in log[:first_classify], "压制要跑在分类之前"
    assert ("is_suppressed", OTHER_RECIPIENT) in log[:first_classify], (
        "整批的压制都要跑完，才轮到分类 —— 不是『一条压制、一条分类』地交替"
    )
    assert [event for event in log if event[0] == "classify"] == [
        ("classify", "msg-a"),
        ("classify", "msg-b"),
    ]


def test_the_classifier_is_handed_the_already_held_event(store) -> None:
    """分类器拿到的是一条**已经压制过**的事件，而且原件逐字在上面。

    这条正面回答了「先压制、后分类」到底压了什么：处置是保守的那一格
    （``MANUAL_REVIEW``），理由是 :data:`CONSERVATIVE_HOLD` 那句话；而 ``body`` /
    ``raw_ref`` / ``raw_sha256`` 一个不少 —— 分类器读的就是那份原件。
    """
    seed_send(store)
    item = make_item(body="Could you send the pricing?")
    classifier = _FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY)

    ingest(
        make_batch((item,)),
        store=store,
        classifier=classifier,
        workspace=WORKSPACE,
        at=T1,
    )

    held = classifier.seen[0]
    assert held.disposition is CorrespondenceDisposition.MANUAL_REVIEW
    assert held.hold_reason == CONSERVATIVE_HOLD
    assert held.kind is CorrespondenceKind.HUMAN_REPLY, "闭集里没有「不知道」，保守取最贵的那一格"
    assert held.body == item.body
    assert held.raw_ref == item.raw_ref
    assert held.raw_sha256 == item.raw_sha256
    assert held.workspace == WORKSPACE


def test_the_hold_stands_when_the_classifier_blows_up(store) -> None:
    """分类器抛异常时，**压制已经生效**（它就是上一条落地的形状）。

    这一条与 ``test_a_classifier_that_raises_still_lands_the_original`` 是一对：
    那条问「原件还在吗」，这条问「压住的那一步在分类之前就已经成立了吗」。
    """
    seed_send(store)
    store.put_suppression(
        RecipientSuppression(
            recipient=RECIPIENT,
            reason=SuppressionReason.UNSUBSCRIBE,
            source_ref="event-0000",
            suppressed_at=T0,
        )
    )
    log: list[tuple[str, str]] = []
    watched = _RecordingStore(store, log)
    classifier = _RecordingClassifier(log, raises=RuntimeError("模型网关 502"))

    receipt = ingest(
        make_batch((make_item(),)),
        store=watched,
        classifier=classifier,
        workspace=WORKSPACE,
        at=T1,
    )

    assert log[0] == ("is_suppressed", RECIPIENT), "压制是这一条上的第一个动作"
    assert log[1] == ("classify", "msg-0001")
    assert receipt.holds[0].already_suppressed is True
    assert "已经在抑制名单上" in receipt.holds[0].note
    assert receipt.ingested[0].disposition is CorrespondenceDisposition.MANUAL_REVIEW


def test_a_failure_stops_the_cursor_before_that_item(store) -> None:
    """§7 逐字：「先持久化、再推进游标」—— 而某一条没落库时，游标**不得越过它**。

    越过一条没落库的事件，就等于**丢掉它**：下一次读从它后面开始，那封信再也不
    会出现。所以回执必须答得出「停在哪一条」，而 ``cursor`` 退回这一批**读出来的
    那一格**（``from_cursor``），不是提供方给的那一格。
    """
    seed_send(store, send_key=SEND_KEY, recipient=RECIPIENT)
    seed_send(store, send_key=OTHER_SEND_KEY, recipient=OTHER_RECIPIENT)
    flaky = _FlakyStore(store, fail_on_call=2)

    receipt = ingest(
        make_batch(
            (
                make_item(provider_message_id="msg-a"),
                make_item(provider_message_id="msg-b", send_key=OTHER_SEND_KEY),
            )
        ),
        store=flaky,
        classifier=_FixedClassifier(kind=CorrespondenceKind.SOFT_BOUNCE),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.advanced is False
    assert receipt.cursor == "cursor-0001", "游标退回这一批读出来的那一格"
    assert receipt.from_cursor == "cursor-0001"
    assert receipt.stopped_at, "停在哪一条必须写出来"
    assert receipt.stopped_at != receipt.ingested[0].event_id, "停的是**没落库**的那一条"
    assert "StoreError" in receipt.failure
    assert len(receipt.ingested) == 1, "停下来的那一条不算落库"
    assert [event.provider_message_id for event in store.events(workspace=WORKSPACE)] == ["msg-a"]


def test_an_empty_batch_moves_nothing_but_the_cursor(store) -> None:
    """空批次是一个**正常的结论**（「这一段没有来信」），它只推游标。"""
    receipt = ingest(
        make_batch(()),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.holds == ()
    assert receipt.ingested == ()
    assert receipt.advanced is True
    assert receipt.cursor == "cursor-0002"
    assert store.events(workspace=WORKSPACE) == ()


def test_a_dead_cursor_is_not_a_failed_batch(store) -> None:
    """游标作废 ≠ 这一批没落库。**两个字段分开说。**

    ``cursor_state`` 说「这个游标不算数了」，``stopped_at`` 说「哪一条没落库」。
    合成一个字段的实现在这里只能二选一，而两个方向的猜错都很贵：一个会让调用方
    接着用一个作废的游标，另一个会让它重读一整段。
    """
    receipt = ingest(
        make_batch(
            (), cursor="", cursor_state=CursorState.INVALIDATED, note="提供方说这个游标太旧了"
        ),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.cursor_state is CursorState.INVALIDATED
    assert receipt.cursor == ""
    assert receipt.stopped_at == ""
    assert receipt.advanced is True


def test_at_must_carry_a_timezone(store) -> None:
    """naive 的「现在」被拒绝 —— 与 ``application/maintenance.py`` 同一条。"""
    with pytest.raises(ValueError, match="必须带时区"):
        ingest(
            make_batch(()),
            store=store,
            classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
            workspace=WORKSPACE,
            at=datetime(2026, 9, 23, 1, 0),
        )


# ======================================================================
# 应用那一半：重放
# ======================================================================


def test_a_replayed_batch_writes_no_second_handoff(store) -> None:
    """**本文件的第 4 条判据。** 同一封回信重放 → 一条交接，不是两条。

    webhook 重试、游标回退、两个驱动并发都会造出重放，而第二条交接的症状（销售
    收到两条同一封回信的交接）在报告上只是「多了一条」。更坏的是
    ``put_handoff`` 是**覆盖写**：拿它当幂等手段会把一条已经被销售接下的交接悄悄
    打回 ``PENDING_LOCAL``。所以本层按 ``event_id`` 先查库里那一条，并把它的状态
    **原样报回去**。
    """
    seed_send(store)
    classifier = _FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY)
    item = make_item()

    first = ingest(
        make_batch((item,)), store=store, classifier=classifier, workspace=WORKSPACE, at=T1
    )
    assert first.ingested[0].replayed is False

    # 销售侧已经接手了这条交接。
    original = store.handoffs(workspace=WORKSPACE)[0]
    notified = SalesHandoff(
        **{
            **original.model_dump(),
            "state": HandoffState.NOTIFIED,
            "external_row_id": "row-0001",
            "external_version": "v1",
        }
    )
    store.put_handoff(notified)

    later = T1 + timedelta(days=2)
    second = ingest(
        make_batch((item,)), store=store, classifier=classifier, workspace=WORKSPACE, at=later
    )

    assert second.ingested[0].replayed is True
    assert second.ingested[0].handoff_id == original.handoff_id
    assert second.ingested[0].handoff_state is HandoffState.NOTIFIED, (
        "重放不得把一条已经被接下的交接打回待办"
    )
    assert len(store.events(workspace=WORKSPACE)) == 1, "重放不写第二次事件"
    assert len(store.handoffs(workspace=WORKSPACE)) == 1, "重放不写第二条交接"
    assert store.handoffs(workspace=WORKSPACE)[0].state is HandoffState.NOTIFIED
    assert store.handoffs(workspace=WORKSPACE)[0].evidence_ids == original.evidence_ids


def test_a_replayed_suppression_is_not_a_second_row(store) -> None:
    """同一封退订重放 → 一行抑制，不是两行。

    存储层把同一个 ``(收件人, 理由)`` 的**第二次不同内容**的写入判成
    ``StoreError``（一条解除之后再收到同一理由的投诉是两条事实，它拒绝就地改写）。
    一个不做这一步查询的实现，会在重放时抛 —— 而游标因此永远推不动，这一批会
    卡死在自己身上。
    """
    seed_send(store)
    item = make_item(kind=CorrespondenceKind.UNSUBSCRIBE)

    first = ingest(
        make_batch((item,)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )
    second = ingest(
        make_batch((item,)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1 + timedelta(days=2),
    )

    assert first.advanced is True and second.advanced is True, "重放不该把这一批卡死"
    assert second.ingested[0].replayed is True
    assert "已经在抑制名单上" in second.ingested[0].suppression_note
    assert len(store.suppressions(RECIPIENT)) == 1
    assert len(store.events(workspace=WORKSPACE)) == 1


def test_the_same_key_with_a_different_original_is_a_conflict_not_a_replay(store) -> None:
    """同一个去重键、**不同的原件** → 冲突，不是重放。

    先读库里那一条的判据是 ``raw_sha256``（它回答的正是「是不是同一份原件」）。
    一个按去重键直接认成重放的实现会**静默合并**两封不同的信 —— 而它的症状是
    「某一封信不见了」，在报告上什么都没有。
    """
    seed_send(store)
    first = ingest(
        make_batch((make_item(body="第一封"),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.SOFT_BOUNCE),
        workspace=WORKSPACE,
        at=T1,
    )
    assert first.advanced is True

    conflicted = ingest(
        make_batch(
            (
                make_item(
                    body="另一封，同一个提供方消息 id",
                    raw_ref="raw/mail/msg-0001-b.eml",
                    raw_sha256=_digest("raw-message-0001-b"),
                ),
            )
        ),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.SOFT_BOUNCE),
        workspace=WORKSPACE,
        at=T1 + timedelta(minutes=1),
    )

    assert conflicted.advanced is False
    assert conflicted.stopped_at
    assert conflicted.failure, "一个键装了两份原件必须报出来 —— 静默合并比崩溃难查得多"
    assert conflicted.ingested == (), "冲突不是一次成功的落库"
    assert len(store.events(workspace=WORKSPACE)) == 1
    assert store.events(workspace=WORKSPACE)[0].raw_sha256 == _digest("raw-message-0001"), (
        "库里那一条仍然是先到的那份原件"
    )


# ======================================================================
# 应用那一半：词汇表漂了的时候
# ======================================================================


def test_a_classification_with_no_suppression_reason_is_refused(store, monkeypatch) -> None:
    """分类与抑制理由两张表漂开时**当场抛**，不默默写下一个理由错位的抑制。

    ``CorrespondenceKind`` 与 ``SuppressionReason`` 是两个闭集，而抑制行的理由按
    **同名取值**取（``SuppressionReason(kind.value)``）。今天两者逐字对齐，所以这一条
    要靠改一张表来触发 —— 而「不可达的分支没人测过」正是它值得被测的理由：它的工作是
    **在下一次有人往分类表里加成员、而忘了加理由的时候响**。

    抛的是 ``ValueError`` 而不是 ``StoreError``，这是两条不同的结论：前者说的是
    「这个东西在**领域词汇表**上就不成立」，重试多少次都一样，所以它一路抛到调用方；
    后者是存储拒绝了一次写入，``ingest`` 会停在那一条并把原文留给人看。
    """
    seed_send(store)
    monkeypatch.setitem(
        KIND_DISPOSITION, CorrespondenceKind.SOFT_BOUNCE, CorrespondenceDisposition.SUPPRESS
    )

    with pytest.raises(ValueError, match="soft_bounce"):
        ingest(
            make_batch((make_item(kind=CorrespondenceKind.SOFT_BOUNCE),)),
            store=store,
            classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
            workspace=WORKSPACE,
            at=T1,
        )

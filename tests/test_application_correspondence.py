"""``_link`` 的**第二、第三条路**：按对方回显的那一封 / 按投递状态通知关联。

``tests/test_correspondence_contract.py`` 守的是收信那一层的其余全部契约
（先压制后分类、分类失败不丢原件、游标不越过没落库的那一条、重放不写第二条交接、
抑制不被翻掉……）以及 ``_link`` 的**第一条路**（``send_key``）。本文件只补上这两条
新增的路和它们的端到端：两处分开不是分家，是因为这两条路的判据要求的**装置**比
其余那些多一样东西 —— 一封真的发出去过、真的被捕获下来的信。

## 这一条路修的是什么

在它之前，一封回信能不能关联上，取决于**来源有没有把 ``send_key`` 填进来**。
而真实存在的那个来源（``adapters/mail_capture_source.py``）填不出来：``send_key``
是**我们**去重用的键，它从不出现在信里（见 ``domain/outreach.py`` 的
``OutboundAttempt``）。它填得出来的是 ``in_reply_to`` —— 而我们发信时把**我们自己
生成**的那个 ``Message-ID`` 记在 ``OutboundAttempt.provider_message_id`` 上
（``application/outreach.py`` 的 ``_deliver_one``：两个传输实现都返回我们生成的
那一个）。于是两条格子在两端各自都建好了，中间那一格是空的，每一封读回来的回信
都落进人工审核。

## 端到端那一条是**真的发了一封信**

``test_a_reply_that_echoes_our_own_message_id_links_back_to_that_attempt`` 起的是一台
真的在听的回环捕获服务（``127.0.0.1``、端口 ``0``，§6 允许的隔离验证），用
``MailCaptureTransport`` 真的把那封信交给它，拿回执上的 ``Message-ID`` 去建那条
``OutboundAttempt``（与 ``application/outreach.py`` 写的是同一个字段），再让对方的
回信真的落进同一个目录、被新的来源读回来、被 ``_link`` 关联上。

三个模块各自绿是不够的：这条判据只有在**两端接上**时才成立，而「接上」这件事
正是本次改动的全部内容。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.generator import BytesGenerator
from email.message import EmailMessage
from pathlib import Path

import pytest

from marketops.adapters.mail_capture import MailCaptureServer, read_index
from marketops.adapters.mail_capture_source import MailCaptureSource
from marketops.adapters.mail_capture_transport import MailCaptureTransport
from marketops.adapters.memory import InMemoryOutreachStore
from marketops.adapters.reply_classifier import RuleBasedReplyClassifier
from marketops.application.correspondence import ingest
from marketops.application.outreach import stopped_by_reply
from marketops.domain.outreach import (
    CorrespondenceDisposition,
    CorrespondenceEvent,
    CorrespondenceKind,
    DraftRef,
    ManifestEntry,
    MessageState,
    OutreachStage,
    OutboundAttempt,
    Wave,
    WaveState,
)
from marketops.domain.util import normalise_email, sha256_text, utc_now
from marketops.ports.correspondence import (
    Classification,
    CorrespondenceBatch,
    CorrespondenceItem,
    ReplyClassifier,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=5)

WORKSPACE = "ws-example-uk"
WAVE = "wave-0001"
CAMPAIGN = "campaign-0001"
SCENE = "scene-0001"
SENDER = "outreach@example.invalid"
RECIPIENT = "buyer@example.invalid"
OTHER_RECIPIENT = "second@example.invalid"
SEND_KEY = "send-0001"
OTHER_SEND_KEY = "send-0002"
OUR_MESSAGE_ID = "<our-letter-0001@example.invalid>"
OTHER_MESSAGE_ID = "<our-letter-0002@example.invalid>"

BODY = "Hello — a short note about your site.\n"


# ----------------------------------------------------------------------
# 构造器（与 tests/test_correspondence_contract.py 同形，本文件自足）
# ----------------------------------------------------------------------


def _digest(text: str) -> str:
    """一个合法的 64 位摘要。**从内容算**，不是一串固定的 a。"""
    return sha256_text(text)


def _wire(message: EmailMessage) -> bytes:
    """一条消息 → 它在网线上的那些字节（CRLF 行尾）。"""
    buffer = io.BytesIO()
    BytesGenerator(buffer, policy=policy.default).flatten(message, linesep="\r\n")
    return buffer.getvalue()


def make_wave(*, wave_id: str = WAVE) -> Wave:
    return Wave(
        wave_id=wave_id,
        campaign_id=CAMPAIGN,
        workspace=WORKSPACE,
        scene_id=SCENE,
        state=WaveState.SENDING,
        manifest_sha256=_digest("manifest-0001"),
        revision=0,
        created_at=T0,
        updated_at=T0,
    )


def make_attempt(
    *,
    send_key: str = SEND_KEY,
    recipient: str = RECIPIENT,
    wave_id: str = WAVE,
    provider_message_id: str = OUR_MESSAGE_ID,
) -> OutboundAttempt:
    """一次**已经送出去**的尝试 —— 回信总是对应一次真的发出去了的发送。

    ``provider_message_id`` 就是 ``application/outreach.py`` 在投递成功后写下的那
    一格（我们自己生成的 ``Message-ID``），也是本文件里那条新路要用的那一格。
    """
    return OutboundAttempt(
        send_key=send_key,
        workspace=WORKSPACE,
        wave_id=wave_id,
        draft_id="draft-0001",
        draft_version=1,
        draft=DraftRef(draft_id="draft-0001", draft_version=1, body_sha256=_digest("body")),
        route_ref="route-0001",
        account_id="account-0001",
        recipient=recipient,
        sendbox=SENDER,
        stage=OutreachStage.STAGE_1,
        authorization_id="auth-0001",
        manifest_sha256=_digest("manifest-0001"),
        state=MessageState.DELIVERED,
        started_at=T0,
        finished_at=T0,
        provider_message_id=provider_message_id,
    )


def make_item(**overrides) -> CorrespondenceItem:
    """一条读回来的回信。**默认带着对方回显的那一封**，不带 ``send_key``。"""
    payload = {
        "provider_message_id": "msg-0001",
        "sendbox": SENDER,
        "raw_ref": "raw/mail/msg-0001.eml",
        "raw_sha256": _digest("raw-message-0001"),
        "received_at": T0,
        "sender": RECIPIENT,
        "subject": "Re: a short note",
        "body": "Thanks for the note.",
        "in_reply_to": OUR_MESSAGE_ID,
    }
    payload.update(overrides)
    return CorrespondenceItem(**payload)


def make_batch(items=(), **overrides) -> CorrespondenceBatch:
    payload = {"items": tuple(items), "cursor": "2", "from_cursor": "1"}
    payload.update(overrides)
    return CorrespondenceBatch(**payload)


def seed_send(
    store,
    *,
    send_key: str = SEND_KEY,
    recipient: str = RECIPIENT,
    provider_message_id: str = OUR_MESSAGE_ID,
) -> OutboundAttempt:
    """库里先有一条「这封信已经发出去了」的事实 —— 回信靠它才关联得上。"""
    if store.wave(WAVE) is None:
        store.put_wave(make_wave(), expected_revision=None)
    attempt = make_attempt(
        send_key=send_key, recipient=recipient, provider_message_id=provider_message_id
    )
    store.begin_attempt(attempt, at=T0)
    return attempt


class _FixedClassifier(ReplyClassifier):
    """一个**没有网络**的分类器：说一句话，或者弃权。"""

    def __init__(self, kind: CorrespondenceKind | None = None, *, reason: str = "") -> None:
        self._kind = kind
        self._reason = reason

    def classify(self, *, event: CorrespondenceEvent) -> Classification:
        return Classification(kind=self._kind, reason=self._reason)


class _CountingStore:
    """记下 ``attempts_by_recipient`` 被问了几次。**它守的是两个前置条件。**

    「没有引用」与「没有发件人」时**不应该发生那次查询**：后者尤其要紧 —— 用空发件
    人去查全库历史，会用「我不知道他是谁」换来一次匹配，而任何一次这样的匹配都是猜。
    两次调用**看起来**都没有结论，所以只判结论的测试分不出这两种实现。
    """

    def __init__(self, store) -> None:
        self._store = store
        self.by_recipient_calls = 0

    def attempts_by_recipient(self, recipient: str):
        self.by_recipient_calls += 1
        return self._store.attempts_by_recipient(recipient)

    def __getattr__(self, name):
        return getattr(self._store, name)


def link(item: CorrespondenceItem, store) -> str:
    """跑一次 ``ingest``，返回那条事件的 ``hold_reason``。

    走**真**的那条路（``ingest`` 而不是内部的 ``_link``）：关联的结果只有从这里才
    看得到全貌 —— 关联上了是 ``HANDOFF``，关联不上是 ``MANUAL_REVIEW`` + 一句理由，
    而那句理由就是给人看的东西。
    """
    receipt = ingest(
        make_batch((item,)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )
    return receipt.ingested[0].hold_reason


@dataclass
class _Rig:
    """端到端用的一组东西：**那一台**捕获服务、它的目录、以及它的两个前门。

    ``server`` 留在手上不是为了好看：对方的回信必须从那台**已经在听**的服务的
    ``ingest`` 进来。另起一台同 root 的服务会让它的序号从头数（它不知道盘上有
    东西），于是两封信用同一个 ``seq`` —— 而游标正是按 ``seq`` 走的。
    """

    server: MailCaptureServer

    @property
    def root(self) -> Path:
        return self.server.root

    @property
    def source(self) -> MailCaptureSource:
        return MailCaptureSource(self.root, sendbox=SENDER)

    def entry(self, *, recipient: str = RECIPIENT) -> ManifestEntry:
        return ManifestEntry(
            draft=DraftRef(draft_id="draft-0001", draft_version=1, body_sha256=sha256_text(BODY)),
            route_ref="route-0001",
            account_id="account-0001",
            recipient=normalise_email(recipient),
            sendbox=SENDER,
            stage=OutreachStage.STAGE_1,
            family_id="family-0001",
            writing_policy_id="policy-0001",
            evidence_ids=("evidence-0001",),
        )


@pytest.fixture
def rig(tmp_path: Path):
    """一台真的在听的回环捕获服务 + 一个真在用的传输。"""
    server = MailCaptureServer(tmp_path / "capture", host="127.0.0.1", port=0).start()
    try:
        yield _Rig(server=server), MailCaptureTransport(server, from_address=SENDER)
    finally:
        server.stop()


def _reply_bytes(*, in_reply_to: str, body: str, message_id: str = "<reply-0001@example.invalid>") -> bytes:
    """对方的回信。**``In-Reply-To`` 抄的就是我们那封信的 ``Message-ID``。**

    真实世界里对方是点「回复」写的这封信；这里把那件事写成一行，因为它是这条路的
    **全部前提**：两个字符串逐字相等。
    """
    message = EmailMessage(policy=policy.default)
    message["From"] = RECIPIENT
    message["To"] = SENDER
    message["Subject"] = "Re: a short note"
    message["Message-ID"] = message_id
    message["In-Reply-To"] = in_reply_to
    message["References"] = in_reply_to
    message.set_content(body)
    return _wire(message)


# ======================================================================
# 端到端：真的发一封、真的读回来、真的关联上
# ======================================================================


def test_a_reply_that_echoes_our_own_message_id_links_back_to_that_attempt(rig) -> None:
    """**发一封 → 对方的回信落盘 → 读回来 → 关联到那一次尝试。**

    这一条是本文件存在的理由。它走的每一步都是真的：

    1. ``MailCaptureTransport`` 把信交给一台**在听**的回环捕获服务，回执上带回来
       的是**我们自己生成**的 ``Message-ID``；
    2. 那一个字符串被写进 ``OutboundAttempt.provider_message_id`` —— 与
       ``application/outreach.py`` 的 ``_deliver_one`` 写的是同一格；
    3. 对方的回信（``In-Reply-To`` 抄着它）落进**同一个目录**；
    4. ``MailCaptureSource`` 从游标之后读回来，``in_reply_to`` **逐字**等于那个
       ``Message-ID``；
    5. ``ingest`` 把它关联到那一次尝试，事件记下**那一次尝试的** ``send_key``。

    第 5 步的结论在事件行上看得见：关联上了 → 分类器说的 ``HUMAN_REPLY`` 按表走到
    ``HANDOFF``；关联不上就会退成 ``MANUAL_REVIEW``，而理由里写着「关联不上」。
    """
    rigged, transport = rig
    receipt = transport.send(
        entry=rigged.entry(), subject="a short note", body=BODY, from_address=SENDER
    )
    assert receipt.accepted and receipt.message_id, "这封信没有真的被收下"

    # 游标这一格在端到端里也要真的用一次：捕获目录里**也有我们自己发出去的那一封**
    # （传输与来源共用同一个目录），所以「从头读」会把我们自己的信也读回来。
    everything = read_index(rigged.root)
    assert [row.message_id for row in everything] == [receipt.message_id]

    delivered = _deliver_reply(
        rigged, _reply_bytes(in_reply_to=receipt.message_id, body=BODY)
    )
    assert delivered.seq == everything[-1].seq + 1, "回信应当落在我们那一封的后面"

    source = rigged.source
    # 从头读：目录里第一封就是我们自己发出去的那一封（sender 是我们）。
    whole = source.read()
    assert len(whole.items) == 2, "捕获目录里应当有我们那一封与对方的回信"
    assert whole.items[0].sender == SENDER, "第一封是我们自己发出去的"
    assert whole.items[0].in_reply_to == ""

    # 从游标之后读：只有对方那一封 —— 这正是游标存在的理由。
    batch = source.read(cursor=str(everything[-1].seq))
    assert len(batch.items) == 1
    item = batch.items[0]
    assert item.sender == RECIPIENT
    assert item.in_reply_to == receipt.message_id, (
        "读回来的引用与回执上那个 Message-ID 不是同一个字符串 —— 这条路的全部前提就是它们逐字相等"
    )

    store = InMemoryOutreachStore()
    attempt = seed_send(store, provider_message_id=receipt.message_id)
    landed = ingest(
        batch,
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=utc_now() + timedelta(seconds=1),
    )

    assert landed.holds[0].correspondent == RECIPIENT, "关联不上时会退回发件人，两者不同"
    assert "关联不上" not in landed.holds[0].note, landed.holds[0].note
    assert landed.ingested[0].disposition is CorrespondenceDisposition.HANDOFF, (
        f"没有关联上：{landed.ingested[0].hold_reason}"
    )
    events = store.events(workspace=WORKSPACE)
    assert len(events) == 1
    assert events[0].send_key == attempt.send_key, (
        "事件记的不是那一次尝试的 send_key —— stopped_by_reply 就是按它找事件的"
    )
    handoffs = store.handoffs(workspace=WORKSPACE)
    assert len(handoffs) == 1
    assert handoffs[0].reply_body == item.body, "交接里的正文不是来源读回来的那一份逐字原文"
    assert BODY.strip() in handoffs[0].reply_body


def _deliver_reply(rigged: _Rig, raw: bytes):
    """把对方的回信收进**那一台**捕获服务。**只走 ingest 前门**（与 SMTP 前门同一个入口）。"""
    return rigged.server.ingest(raw, mail_from=RECIPIENT, recipients=(SENDER,))


def test_the_linked_reply_suppresses_and_stops_the_next_wave(rig) -> None:
    """端到端的后果那一半：**一条退订回信真的抑制了这个人**。

    关联上了却没有后果，等于没关联：§7 要求「退订/投诉 → 立即持久抑制」，而抑制行
    的收件人取自**那一次发送**（不是发件人那一格）—— 只有 ``send_key`` / 引用这一
    条链接上了，收件人才是那个真的收到过信的人。

    ``stopped_by_reply`` 也要真的读得到它：出信侧按那个事实筛人，少了它，一个说过
    「退订」的人会在下一批里照常收到信，而每一行记录都显示处置正确。
    """
    rigged, transport = rig
    receipt = transport.send(
        entry=rigged.entry(), subject="a short note", body=BODY, from_address=SENDER
    )
    _deliver_reply(
        rigged,
        _reply_bytes(in_reply_to=receipt.message_id, body="Please unsubscribe me from your list."),
    )
    cursor = str(read_index(rigged.root)[0].seq)
    batch = rigged.source.read(cursor=cursor)

    store = InMemoryOutreachStore()
    attempt = seed_send(store, provider_message_id=receipt.message_id)
    landed = ingest(
        batch,
        store=store,
        classifier=RuleBasedReplyClassifier(),
        workspace=WORKSPACE,
        at=utc_now() + timedelta(seconds=1),
    )

    assert landed.ingested[0].kind is CorrespondenceKind.UNSUBSCRIBE
    assert landed.ingested[0].disposition is CorrespondenceDisposition.SUPPRESS
    assert store.events(workspace=WORKSPACE)[0].send_key == attempt.send_key
    rows = store.suppressions(RECIPIENT)
    assert len(rows) == 1, f"没有落抑制行：{landed.ingested[0].suppression_note}"
    assert store.is_suppressed(RECIPIENT, at=utc_now()) is True
    stop_reason = stopped_by_reply(store=store, workspace=WORKSPACE, recipient=RECIPIENT)
    assert stop_reason, "出信侧读不到这条回信 —— 说过退订的人会在下一批里照常收到信"
    assert CorrespondenceKind.UNSUBSCRIBE.value in stop_reason


# ======================================================================
# 那条路的判据：恰好一条才认
# ======================================================================


def test_a_reference_that_matches_no_attempt_goes_to_a_human() -> None:
    """引用在库里找不到 —— 落库 + 人工审核，理由说得出来「我们这边没有那一封」。

    这与「没有带 ``send_key``」是两条不同的线索：前者说我们这边没有那一封，
    后者说来源没给证据。给成一句会让排查指向错的方向。
    """
    store = InMemoryOutreachStore()
    seed_send(store, provider_message_id=OTHER_MESSAGE_ID)

    reason = link(make_item(in_reply_to=OUR_MESSAGE_ID), store)

    assert "关联不上" in reason
    assert OTHER_MESSAGE_ID in reason or OUR_MESSAGE_ID in reason
    assert "投递历史" in reason
    assert store.suppressions(RECIPIENT) == ()
    assert store.handoffs(workspace=WORKSPACE) == ()


def test_two_attempts_sharing_one_message_id_go_to_a_human() -> None:
    """**两条尝试写着同一个 Message-ID** 时不给结论 —— 库里没有答案，挑一个是猜。

    ``provider_message_id`` 由 ``(正文摘要, 收件人)`` 派生（``stable_message_id``），
    所以同一封信在两个批次里发给同一个人会**合法地**共用它。这时猜错的那一次会让
    停跟进与交接挂到另一封上（``stopped_by_reply`` 按 ``send_key`` 找事件）。
    """
    store = InMemoryOutreachStore()
    seed_send(store, send_key=SEND_KEY, provider_message_id=OUR_MESSAGE_ID)
    seed_send(store, send_key=OTHER_SEND_KEY, provider_message_id=OUR_MESSAGE_ID)

    reason = link(make_item(in_reply_to=OUR_MESSAGE_ID), store)

    assert "关联不上" in reason
    assert "2" in reason, f"理由没有说清楚有几条候选：{reason!r}"
    assert SEND_KEY in reason and OTHER_SEND_KEY in reason, "理由没有列出候选的 send_key"


def test_a_unique_reference_links_even_with_many_other_attempts() -> None:
    """同一个发件人的**其他**投递不影响这条判据：判的是「恰好一条写着这个标识」。

    这一条与上一条是一对：只有上一条时，一个「有候选就返回 None」的实现也是绿的
    —— 而它会让每个有历史的收件人都关联不上，也就是这条路等于没修。
    """
    store = InMemoryOutreachStore()
    seed_send(store, send_key=SEND_KEY, provider_message_id=OUR_MESSAGE_ID)
    seed_send(store, send_key=OTHER_SEND_KEY, provider_message_id=OTHER_MESSAGE_ID)

    receipt = ingest(
        make_batch((make_item(in_reply_to=OUR_MESSAGE_ID),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.ingested[0].disposition is CorrespondenceDisposition.HANDOFF
    events = store.events(workspace=WORKSPACE)
    assert [event.send_key for event in events] == [SEND_KEY]
    assert len(store.handoffs(workspace=WORKSPACE)) == 1


def test_without_a_reference_the_reason_names_both_headers() -> None:
    """没有引用时**直接返回**，而且理由点得出是哪两个头字段是空的。"""
    store = InMemoryOutreachStore()
    watched = _CountingStore(store)
    seed_send(store, provider_message_id=OUR_MESSAGE_ID)

    reason = link(make_item(in_reply_to=""), watched)

    assert "In-Reply-To" in reason and "References" in reason, reason
    assert watched.by_recipient_calls == 0, (
        "没有引用还是去查了投递历史 —— 那次查询的结论只能由别的东西决定"
    )


def test_without_a_sender_nothing_is_searched() -> None:
    """没有发件人时**不查**投递历史。

    按引用找要先知道去**谁**的投递历史里找。用空发件人去查全库，会用「我不知道他是
    谁」换来一次匹配 —— 而任何一次这样的匹配都是猜出来的关联，处置却会照常执行。
    """
    store = InMemoryOutreachStore()
    watched = _CountingStore(store)
    seed_send(store, provider_message_id=OUR_MESSAGE_ID)

    reason = link(make_item(sender=""), watched)

    assert "发件人" in reason, reason
    assert watched.by_recipient_calls == 0, (
        "发件人为空还是去查了 —— 那次查询的候选来自全库，不是这个人"
    )


# ======================================================================
# 两条路的次序：send_key 在前
# ======================================================================


def test_a_well_formed_send_key_still_wins_over_the_reference() -> None:
    """两条路都能走通时，``send_key`` 赢 —— **次序不能换**。

    ``send_key`` 是我们自己的去重键，一次命中就是一个确定的事实；引用匹配要求
    「发件人的历史里恰好一次写着这个 Message-ID」，它比前者多两个前提。把引用放在
    前面，会把一个**本来能精确关联**的事件降级成一次模糊匹配 —— 而「降级」的症状
    不是红，是关联到了**隔壁那一次发送**：处置照常执行，跟进却停在了另一个人
    那一封上。

    这里的装置就是那个症状：两条尝试各自成立，事件带的 ``send_key`` 指着 A，
    而 ``in_reply_to`` 指着 B。判据是事件记下的 ``send_key``。
    """
    store = InMemoryOutreachStore()
    seed_send(store, send_key=SEND_KEY, provider_message_id=OUR_MESSAGE_ID)
    seed_send(store, send_key=OTHER_SEND_KEY, provider_message_id=OTHER_MESSAGE_ID)

    receipt = ingest(
        make_batch((make_item(send_key=SEND_KEY, in_reply_to=OTHER_MESSAGE_ID),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.ingested[0].disposition is CorrespondenceDisposition.HANDOFF
    assert [event.send_key for event in store.events(workspace=WORKSPACE)] == [SEND_KEY], (
        "引用那条路先被问了 —— 一个能精确关联的事件被降级成了模糊匹配"
    )


@pytest.mark.parametrize(
    "send_key",
    ["", "Not A Key!"],
    ids=["empty", "malformed"],
)
def test_a_send_key_that_cannot_be_used_falls_through_to_the_reference(send_key: str) -> None:
    """``send_key`` 为空或形状用不了 → **接着走引用那条路**，而不是就地放弃。

    「形状不对的 ``send_key``」与「没有 ``send_key``」是同一件事的两面（见
    ``_link_by_send_key``），两者都不该让这一条线索消失。这正是这条路要修的那个
    真实场景：捕获目录读回来的条目**一格 ``send_key`` 都没有**。
    """
    store = InMemoryOutreachStore()
    seed_send(store, provider_message_id=OUR_MESSAGE_ID)

    receipt = ingest(
        make_batch((make_item(send_key=send_key, in_reply_to=OUR_MESSAGE_ID),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.ingested[0].disposition is CorrespondenceDisposition.HANDOFF, (
        receipt.ingested[0].hold_reason
    )
    assert [event.send_key for event in store.events(workspace=WORKSPACE)] == [SEND_KEY], (
        "形状不对的 send_key 不得写进事件 —— 事件记的是那一次尝试的键"
    )


def test_a_send_key_that_names_no_attempt_falls_through_to_the_reference() -> None:
    """``send_key`` 有、形状也对，但库里读不到那次发送 → **也接着走引用那条路**。

    这是两条路之间的一个**刻意的取舍**：``send_key`` 指向的那一次发送读不到，通常
    意味着这条事件来自另一个 workspace（它自己的发送记录不在这里）—— 而它与本地
    某一次发送的 ``Message-ID`` 对得上时，那条线索仍然是真的。两条路都失败才交给人，
    而那时理由里**两条**都在（见下一条判据）。
    """
    store = InMemoryOutreachStore()
    seed_send(store, provider_message_id=OUR_MESSAGE_ID)

    receipt = ingest(
        make_batch((make_item(send_key="send-9999", in_reply_to=OUR_MESSAGE_ID),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.ingested[0].disposition is CorrespondenceDisposition.HANDOFF
    assert [event.send_key for event in store.events(workspace=WORKSPACE)] == [SEND_KEY]


def test_a_failed_link_reports_both_halves_of_the_reason() -> None:
    """两条路都没成时，理由里**两条**都要有。

    只报一条的实现在排查时会指向错的方向：「没有带 ``send_key``」与「按引用也没
    关联上」是两条不同的线索（前者说来源没给证据，后者说我们这边没有那一封）。
    """
    store = InMemoryOutreachStore()
    seed_send(store, provider_message_id=OTHER_MESSAGE_ID)

    reason = link(make_item(send_key="send-9999", in_reply_to=OUR_MESSAGE_ID), store)

    assert "关联不到任何一次发送" in reason, reason
    assert "按引用再关联一次也没成" in reason, reason
    assert "关联不上" in reason


# ======================================================================
# 边界：本层**不**判的那一件事
# ======================================================================


def test_the_literal_alternate_contact_is_judged_here_not_by_the_classifier() -> None:
    """「请联系我的同事」由 ``_decide`` 的最后一步降级 —— 分类器那边弃权。

    分类器那一侧的判据在 ``tests/test_reply_classifier.py``（它对这段正文弃权）。
    这一条判的是**另一半**：关联**成功**了、分类器说这是 ``HUMAN_REPLY``（按表该
    产出交接），而正文里字面地给了另一个联系人时，处置退成人工审核。

    §7 逐字：「字面上的替代联系人 → awaiting_Member_A_confirmation，不自动重发或新建
    外联」。判据是两件事同时成立（一个线索短语 + 恰好一个与发件人不同的地址），
    两处都判会让「换不换人」有两个会分岔的答案。
    """
    store = InMemoryOutreachStore()
    seed_send(store, provider_message_id=OUR_MESSAGE_ID)
    body = (
        "I've moved teams — please contact my colleague at successor@example.invalid, "
        "they'll take it from here."
    )

    receipt = ingest(
        make_batch((make_item(body=body, in_reply_to=OUR_MESSAGE_ID),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    landed = receipt.ingested[0]
    assert landed.disposition is CorrespondenceDisposition.MANUAL_REVIEW
    assert "successor@example.invalid" in landed.hold_reason
    assert store.handoffs(workspace=WORKSPACE) == (), "替代联系人被自动换人了"
    # 而它**仍然是关联上的**：事件带着那一次尝试的键，出信侧照样能停这一条跟进。
    assert store.events(workspace=WORKSPACE)[0].send_key == SEND_KEY


# ======================================================================
# 第三条路：一份投递状态通知说的那一对事实
# ======================================================================

MAILER_DAEMON = "MAILER-DAEMON@example.invalid"


def _dsn_bytes(
    *, recipient: str, original_message_id: str, status: str = "5.1.1", message_id: str = "<dsn-0001@example.invalid>"
) -> bytes:
    """一份 RFC 3464 的投递状态通知，**按收信服务器会写下的形状**拼字节。

    手工拼是刻意的：本仓没有**生成** DSN 的能力（我们没有收信服务器那一侧），而
    这条判据读的正是对面会写下的东西。标准库的 ``EmailMessage`` 拼不出
    ``message/delivery-status`` 那一段的块结构。

    ``From`` 是 ``MAILER-DAEMON@…``（真实世界的形状），而**没有** ``In-Reply-To``
    —— 两件事都不是这一层可以挑剔的：它们就是这封退信的样子，也正是「按引用关联」
    这条路对退信结构性走不通的原因。
    """
    return (
        f"From: {MAILER_DAEMON}\r\n"
        f"To: {SENDER}\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\n"
        f"Message-ID: {message_id}\r\n"
        "Auto-Submitted: auto-replied\r\n"
        "Content-Type: multipart/report; report-type=delivery-status;"
        ' boundary="BOUNDARY-1"\r\n'
        "MIME-Version: 1.0\r\n"
        "\r\n"
        "--BOUNDARY-1\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
        "This is the mail system at host example.invalid.\r\n"
        "\r\n"
        "--BOUNDARY-1\r\n"
        "Content-Type: message/delivery-status\r\n"
        "\r\n"
        "Reporting-MTA: dns; example.invalid\r\n"
        "\r\n"
        f"Final-Recipient: rfc822; {recipient}\r\n"
        "Action: failed\r\n"
        f"Status: {status}\r\n"
        f"Original-Message-ID: {original_message_id}\r\n"
        "\r\n"
        "--BOUNDARY-1--\r\n"
    ).encode("utf-8")


def _bounce_item(
    *,
    recipient: str = RECIPIENT,
    message_id: str = OUR_MESSAGE_ID,
    kind: CorrespondenceKind = CorrespondenceKind.HARD_BOUNCE,
):
    """一条从捕获目录读回来的退信条目 —— 形状与 ``MailCaptureSource`` 读出来的那份相同。

    ``sender`` 是那台机器，**不是**被退的人；``in_reply_to`` 是空串。这两格不是
    为了方便才这么填的：它们正是收信服务器写下的那两格。``kind`` 由来源给出（结构
    判得出来），所以这些判据断言的都不是「分类器说了什么」。
    """
    return make_item(
        sender=MAILER_DAEMON,
        subject="Undelivered Mail Returned to Sender",
        in_reply_to="",
        bounce_recipient=recipient,
        bounce_original_message_id=message_id,
        kind=kind,
    )


def test_a_hard_bounce_from_the_capture_dir_suppresses_the_address_that_failed(rig) -> None:
    """**发一封 → 退信落盘 → 读回来 → 抑制写在那个没收到信的地址上。**

    这一条是这条路存在的理由，而它走的每一步都是真的：``MailCaptureTransport``
    把信交给一台在听的回环捕获服务并带回**我们自己生成**的 ``Message-ID``；那份
    ``5.1.1`` 的通知真的落进同一个目录、由 ``MailCaptureSource`` 从游标之后读回来；
    ``ingest`` 靠通知里的 ``Final-Recipient`` 与 ``Original-Message-ID`` 关联上那一次
    发送，写下抑制行。

    在它之前这条路上**没有**任何一步是通的：通知没有 ``send_key``（那是我们的去重键，
    从不出现在信里），也没有 ``In-Reply-To``（那是人回信时的头字段），而按发件人
    地址去找投递历史查的是一台邮件守护进程 —— 恒零条。于是一封硬退信落进人工审核，
    而 §7 要的「硬退信 → 按 SOP 抑制并保留证据」一条都不产生。
    """
    rigged, transport = rig
    receipt = transport.send(
        entry=rigged.entry(), subject="a short note", body=BODY, from_address=SENDER
    )
    assert receipt.accepted and receipt.message_id, "这封信没有真的被收下"

    delivered = rigged.server.ingest(
        _dsn_bytes(recipient=RECIPIENT, original_message_id=receipt.message_id),
        mail_from=MAILER_DAEMON,
        recipients=(SENDER,),
    )
    batch = rigged.source.read(cursor=str(delivered.seq - 1))

    assert len(batch.items) == 1
    item = batch.items[0]
    assert item.sender == MAILER_DAEMON, "退信的发件人是那台机器，不是被退的人"
    assert item.in_reply_to == "", "收信服务器不回显 In-Reply-To —— 这正是这条路必须存在的理由"
    assert item.bounce_recipient == RECIPIENT
    assert item.bounce_original_message_id == receipt.message_id, (
        "通知里那一封与我们发出去时写下的 Message-ID 不是同一个字符串 —— 这条路的前提就是它们逐字相等"
    )

    store = InMemoryOutreachStore()
    attempt = seed_send(store, provider_message_id=receipt.message_id)
    landed = ingest(
        batch,
        store=store,
        # 来源自己给了分类，所以这个分类器**不该被问到**：断言 ``kind`` 是
        # HARD_BOUNCE 就是它的执行器（问到的话它会说 HUMAN_REPLY）。
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=utc_now() + timedelta(seconds=1),
    )

    assert landed.ingested[0].kind is CorrespondenceKind.HARD_BOUNCE
    assert landed.ingested[0].disposition is CorrespondenceDisposition.SUPPRESS
    assert "关联不上" not in landed.holds[0].note, landed.holds[0].note
    assert store.events(workspace=WORKSPACE)[0].send_key == attempt.send_key, (
        "事件记的不是那一次尝试的 send_key —— stopped_by_reply 就是按它找事件的"
    )
    assert len(store.suppressions(RECIPIENT)) == 1, (
        f"没有落抑制行：{landed.ingested[0].suppression_note}"
    )
    assert store.is_suppressed(RECIPIENT, at=utc_now()) is True
    assert store.is_suppressed(MAILER_DAEMON, at=utc_now()) is False, (
        "抑制写在了那台机器身上 —— 被抑制的必须是**我们发过的那个地址**"
    )
    stop_reason = stopped_by_reply(store=store, workspace=WORKSPACE, recipient=RECIPIENT)
    assert stop_reason, "出信侧读不到这条退信 —— 那个不存在的地址会在下一批里照常收到信"
    assert CorrespondenceKind.HARD_BOUNCE.value in stop_reason


def test_a_soft_bounce_links_but_never_suppresses() -> None:
    """软的退信**关联得上，但不抑制** —— 一个满了的邮箱不是一个人离开了。

    这是本次改动最需要一条判据的地方：在这条路之前，没有任何一封退信能产生抑制行
    （关联不上），所以「软退不抑制」只由一张表保证；现在两条都能关联上了，**唯一**
    挡在中间的就是 ``KIND_DISPOSITION`` 那一格。``stop_followup`` 也不该被它触发：
    ``stops_further_outreach`` 明确把 ``SOFT_BOUNCE`` 排除在外。
    """
    store = InMemoryOutreachStore()
    seed_send(store)

    receipt = ingest(
        make_batch((_bounce_item(kind=CorrespondenceKind.SOFT_BOUNCE),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.ingested[0].kind is CorrespondenceKind.SOFT_BOUNCE
    assert receipt.ingested[0].disposition is CorrespondenceDisposition.RECORD_ONLY
    assert store.events(workspace=WORKSPACE)[0].send_key == SEND_KEY, "软退信也该关联上"
    assert store.suppressions(RECIPIENT) == (), "软的退信把人拉进了永久抑制名单"
    assert stopped_by_reply(store=store, workspace=WORKSPACE, recipient=RECIPIENT) == ""


def test_a_bounce_for_an_address_we_never_mailed_goes_to_a_human() -> None:
    """通知里的收件人我们**从来没发过信** → 人工审核，什么都不写。

    这条是「抑制只写在我们发过的那个地址上」的另一半：一份别人的 DSN（转发进来的、
    另一个系统发的）不能让我们去抑制一个陌生人，也不能因为「看起来像退信」就停掉
    任何人的跟进。理由要点得出「我们没给这个地址发过信」。
    """
    store = InMemoryOutreachStore()
    seed_send(store)

    receipt = ingest(
        make_batch((_bounce_item(recipient=OTHER_RECIPIENT),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    assert receipt.ingested[0].disposition is CorrespondenceDisposition.MANUAL_REVIEW
    assert "从来没有给" in receipt.ingested[0].hold_reason
    assert store.suppressions(OTHER_RECIPIENT) == ()
    assert store.suppressions(RECIPIENT) == (), "被牵连的是另一个人的地址"


def test_a_bounce_that_cannot_say_which_send_goes_to_a_human() -> None:
    """两次发送写着同一个 ``Message-ID`` 时**不给结论** —— 挑一个是猜。

    与 ``test_two_attempts_sharing_one_message_id_go_to_a_human`` 逐字同一条理由
    （``stable_message_id`` 由 ``(正文摘要, 收件人)`` 派生，同一封信在两个批次里发给
    同一个人会**合法地**共用它），只是入口不同。抑制**没有**被写下去是这条判据的
    重点：这里的收件人是对的，写下去不会伤到别人，但「退的是哪一封」会成为一条
    猜出来的事实，而 ``stopped_by_reply`` 按 ``send_key`` 找事件。
    """
    store = InMemoryOutreachStore()
    seed_send(store, send_key=SEND_KEY, provider_message_id=OUR_MESSAGE_ID)
    seed_send(store, send_key=OTHER_SEND_KEY, provider_message_id=OUR_MESSAGE_ID)

    receipt = ingest(
        make_batch((_bounce_item(),)),
        store=store,
        classifier=_FixedClassifier(kind=CorrespondenceKind.HUMAN_REPLY),
        workspace=WORKSPACE,
        at=T1,
    )

    reason = receipt.ingested[0].hold_reason
    assert receipt.ingested[0].disposition is CorrespondenceDisposition.MANUAL_REVIEW
    assert "2" in reason and SEND_KEY in reason and OTHER_SEND_KEY in reason, reason
    assert store.suppressions(RECIPIENT) == ()
    # 事件**照旧落库**（关联不上不是错误：原件与理由都必须留下来给人看），
    # 只是它没有 ``send_key`` —— 那正是 ``stopped_by_reply`` 找不到它的原因。
    events = store.events(workspace=WORKSPACE)
    assert len(events) == 1 and events[0].send_key == "", "猜出来的关联被写进了事件"

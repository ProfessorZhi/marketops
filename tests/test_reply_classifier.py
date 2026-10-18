"""词表分类器：**命中才说话，否则弃权** —— 由本文件执行。

## 这里守的四件事

1. **它永远不答 ``HUMAN_REPLY``**（``test_a_warm_human_reply_is_an_abstention`` 与
   那一组语料）。一个「拿不准就猜 HUMAN_REPLY」的分类器，会把每一次读不懂变成
   一条销售交接 —— 而 §7 逐字要求的是「模糊回信 → manual_review_hold」。
   弃权在这里是**常态输出**，不是失败。
2. **拒绝对退订的边界与 ``domain/`` 那张表一致**
   （``test_a_refusal_is_not_an_unsubscribe`` 与它的反向那一条）。抑制是跨 Campaign
   长期有效的，拒绝是关于这次接洽的；把一次「这次不用了」记成「永远不得联系」的
   代价是那个人再也不会被联系到，而记录上一切正常。
3. **次序不是本模块写死的**（``test_the_ranking_is_computed_from_the_domain_tables``）。
   抑制 > 停跟进 > 只记录 三个数从 ``SUPPRESSING_KINDS`` 与
   ``stops_further_outreach`` 算出来，所以 domain 哪天改了一个分类的处置，
   这里跟着动，而不是各自演化。
4. **次序的**方向**是失败关闭**（``test_a_body_that_reads_like_two_things_takes_the_severe_one``）。
   一句同时像退订又像拒绝的话，按退订处置：多一条抑制行的代价是少联系一个人才，
   少一条的代价是**继续给一个说过不要的人发信**。

## 它不认识的东西，这里也一条都不许它认识

「字面上的替代联系人」（「请联系我的同事」）**不是**本分类器的活：那条判据住在
``application/correspondence.py::_alternate_contact``，由 ``_decide`` 的最后一步
执行。本文件里那一条判据检查的是**它仍然弃权** —— 两处都判会让「换不换人」有两个
答案。

**没有网络、没有时钟、没有文件。** 构造出来的是一个 ``CorrespondenceEvent``
（协议里的那条事件），``raw_ref`` 指向一个**不存在**的路径也不影响结论 ——
读文件是来源那一层的事。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketops.adapters.reply_classifier import (
    TEXT_RULES,
    RuleBasedReplyClassifier,
    TextRule,
    _ORDERED_RULES,
    _priority,
)
from marketops.domain.outreach import (
    SUPPRESSING_KINDS,
    CorrespondenceDisposition,
    CorrespondenceEvent,
    CorrespondenceKind,
    stops_further_outreach,
)
from marketops.domain.util import sha256_text
from marketops.ports.correspondence import Classification

UTC = timezone.utc
T0 = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
WORKSPACE = "ws-example-uk"
SENDBOX = "outreach@marketops.example"
SENDER = "buyer@example.invalid"

#: 分类器拿到的**永远是这一条形状**：``application/correspondence.py::_kind_of``
#: 造的占位事件 —— 处置是保守的那一格，``hold_reason`` 说的是「先压住、再分类」，
#: 而 ``kind`` 是那个会被当作缺省值写下来的 ``HUMAN_REPLY``。
CONSERVATIVE_HOLD = "先保守地暂停后续外联，然后再分类（§7）"


def make_event(*, body: str = "", **overrides) -> CorrespondenceEvent:
    """一条已经压制过的、待分类的事件。**只给这个判据要的那几格。**"""
    payload = {
        "event_id": "event-0001",
        "workspace": WORKSPACE,
        "sendbox": SENDBOX,
        "kind": CorrespondenceKind.HUMAN_REPLY,
        "provider_message_id": "msg-0001",
        "sender": SENDER,
        "subject": "Re: a short note",
        "body": body,
        # 一个**不存在**的路径：本分类器不读文件。它读了的话，这条判据会在
        # 「原件读不到」上红，而那不是它要测的东西 —— 但那正是重点：
        # 一个会去读文件的分类器，会让「分类失败」与「原件不见了」混成一件事。
        "raw_ref": "/nonexistent/isolation-check/msg-0001.eml",
        "raw_sha256": sha256_text(body or "empty"),
        "received_at": T0,
        "ingested_at": T0,
        "disposition": CorrespondenceDisposition.MANUAL_REVIEW,
        "hold_reason": CONSERVATIVE_HOLD,
    }
    payload.update(overrides)
    return CorrespondenceEvent(**payload)


@pytest.fixture
def classifier() -> RuleBasedReplyClassifier:
    return RuleBasedReplyClassifier()


def classify(classifier: RuleBasedReplyClassifier, body: str, **overrides) -> Classification:
    return classifier.classify(event=make_event(body=body, **overrides))


# ======================================================================
# 那张表本身
# ======================================================================


def test_the_rule_table_is_a_frozen_module_level_constant() -> None:
    """**唯一的表**：模块级、冻结、可遍历。

    散在 ``if`` 里的词更容易漏 —— 而漏掉的那个词不会让任何东西变红，只会让一类
    回信悄悄落进人工审核（或者更坏：落进一个错误的分类）。一张能被人一眼看全的
    表是这条判据能被写出来的前提。
    """
    assert isinstance(TEXT_RULES, tuple), "表必须是不可变的元组，不是 list"
    assert len(TEXT_RULES) >= 4, "四类规则：退订、投诉、拒绝、自动回复"
    for rule in TEXT_RULES:
        assert isinstance(rule, TextRule)
        assert rule.kind in set(CorrespondenceKind)
        assert rule.cues, f"{rule.label} 一个词都没有 —— 这条规则永远不会命中"
        assert rule.guards.strip(), f"{rule.label} 没说它守的是哪句要求"


def test_every_rule_says_which_requirement_it_guards() -> None:
    """每一条规则都说得出它守的那句话，而且**是 §7/domain 里的那一句**。

    一条说不出自己守哪句要求的规则，在一次误判之后无从判断该改的是词表还是那句话
    本身 —— 于是它只会被删掉，而被删掉的那一类回信没有任何东西接着。
    """
    for rule in TEXT_RULES:
        assert "§7" in rule.guards or "domain" in rule.guards, (
            f"{rule.label} 的 guards 没有指向它执行的那条要求：{rule.guards!r}"
        )


def test_the_cues_are_lowercase_because_the_match_is_casefolded() -> None:
    """词表必须是小写。

    比对发生在 ``str.casefold()`` 之后，所以一个含大写的词**永远不会命中** ——
    它不会红，只会静默失效，而失效的那一类回信落进弃权。
    """
    for rule in TEXT_RULES:
        for cue in rule.cues:
            assert cue == cue.casefold(), f"{rule.label} 里有一个含大写的词：{cue!r}"
            assert cue.strip() == cue, f"{rule.label} 里有一个带首尾空白的词：{cue!r}"


def test_the_ranking_is_computed_from_the_domain_tables() -> None:
    """三个提级从 ``domain/`` 算出来，**不是第二张表**。

    写死一张 ``{UNSUBSCRIBE: 0, REFUSAL: 1, ...}`` 同样能让今天的测试全绿，而它与
    ``KIND_DISPOSITION`` 是两个各自演化的答案：某天有人把一个分类的处置改成
    ``SUPPRESS``，抑制行会多出来，而这里的提级不会跟着动 —— 于是一封同时读到两种
    信号的正文会按**旧的**严重程度判，且没有任何一处会红。
    """
    for kind in CorrespondenceKind:
        expected = 2
        if kind in SUPPRESSING_KINDS:
            expected = 0
        elif stops_further_outreach(kind):
            expected = 1
        assert _priority(kind) == expected, f"{kind.value} 的提级与 domain 对不上"

    # 而且表**真的**按它排过：一份没排过的表在「两种信号同时出现」时按书写次序判。
    ranks = [_priority(rule.kind) for rule in _ORDERED_RULES]
    assert ranks == sorted(ranks), "规则没有按提级排过 —— 次序是一句注释，不是判据"
    assert {rule.kind for rule in _ORDERED_RULES} == {rule.kind for rule in TEXT_RULES}, (
        "排序丢了规则（或者凭空多了一条）"
    )


# ======================================================================
# 四类规则各自命中
# ======================================================================


def test_an_unsubscribe_request_is_read_as_an_unsubscribe(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """点名了**名单 / 订阅**这个对象的退出要求 → ``UNSUBSCRIBE``（进持久抑制）。"""
    for body in (
        "Please unsubscribe me from your list.",
        "take me off your mailing list please",
        "请把我退订，谢谢",
        "帮我退出名单",
    ):
        answer = classify(classifier, body)
        assert answer.kind is CorrespondenceKind.UNSUBSCRIBE, body
        assert "unsubscribe" in str(answer.reason).lower() or "名单" in answer.reason


def test_a_complaint_is_read_as_a_complaint(classifier: RuleBasedReplyClassifier) -> None:
    """投诉 → ``COMPLAINT``（同样进抑制，另外留一条人看的记录）。"""
    for body in ("This is spam.", "I will report you.", "我要投诉你们", "这是骚扰"):
        answer = classify(classifier, body)
        assert answer.kind is CorrespondenceKind.COMPLAINT, body


def test_a_stop_letter_without_a_list_is_a_refusal(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """「不要再给我发信」是 ``REFUSAL``（停跟进），**不是** ``UNSUBSCRIBE``（抑制）。

    边界是**有没有点到名单 / 订阅那个对象**。代价是不对称的：一条错的抑制行让这个
    人**永远**收不到信，而记录上一切正常（``RecipientSuppression`` 的 docstring
    逐字）；一条错的拒绝只让这一次接洽停下，而人看得见事件行。
    """
    for body in (
        "不要再给我发信了。",
        "Please do not contact me again.",
        "I'm not interested, thanks.",
        "我们不需要，谢谢",
    ):
        answer = classify(classifier, body)
        assert answer.kind is CorrespondenceKind.REFUSAL, body

    assert CorrespondenceKind.REFUSAL not in SUPPRESSING_KINDS, (
        "REFUSAL 进了抑制名单 —— 一次「这次不用了」被记成了「永远不得联系」"
    )


def test_an_out_of_office_note_is_an_auto_reply(classifier: RuleBasedReplyClassifier) -> None:
    """没有 RFC 3834 头的自动回复措辞 → ``AUTO_REPLY``（唯一不停跟进的一类）。"""
    for body in (
        "I am currently out of office and will reply when I return.",
        "This is an automated response.",
        "我现在不在办公室，回来再联系。",
    ):
        answer = classify(classifier, body)
        assert answer.kind is CorrespondenceKind.AUTO_REPLY, body
        assert stops_further_outreach(answer.kind) is False, (
            "自动回复被判成停跟进了 —— 那是 §7 里明说不该发生的事"
        )


def test_a_body_that_reads_like_two_things_takes_the_severe_one(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """一句同时像退订又像拒绝的话按**退订**处置；同时像投诉又像拒绝的按投诉。

    次序的方向是失败关闭：多一条抑制行的代价是少联系一个人才；少一条的代价是
    **继续给一个说过不要的人发信**，而每一行记录都显示处置正确。
    """
    both_unsubscribe_and_refusal = "Unsubscribe me. I'm not interested."
    assert classify(classifier, both_unsubscribe_and_refusal).kind is (
        CorrespondenceKind.UNSUBSCRIBE
    )

    both_complaint_and_refusal = "Stop contacting me — this is spam."
    assert classify(classifier, both_complaint_and_refusal).kind is CorrespondenceKind.COMPLAINT

    # 而「只像拒绝」的那一句仍然是拒绝：次序不是「一律取最重的那个分类」。
    assert classify(classifier, "I'm not interested.").kind is CorrespondenceKind.REFUSAL


# ======================================================================
# 弃权：本模块的常态输出
# ======================================================================


@pytest.mark.parametrize(
    "body",
    [
        "Tuesday afternoon works for me — shall we say 3pm?",
        "Thanks, I'll take a look and get back to you next week.",
        "可以，下周二下午聊聊。",
        "Who should I talk to about this?",
        "",  # 与上面几条走的是不同的一步，理由不同（见下一条判据）
    ],
    ids=["warm-en", "vague-en", "warm-zh", "a-question", "empty"],
)
def test_a_warm_human_reply_is_never_a_guess(
    classifier: RuleBasedReplyClassifier, body: str
) -> None:
    """**一封内容相关的真人回信落进弃权，一个分类都不给。**

    这是本模块最重要的那条判据：词表读不出「相关」。「可以，下周二下午聊聊」与
    「这是一封读不懂的信」在词表上长得一样 —— 于是唯一不猜的答案是**说不出**。
    猜 ``HUMAN_REPLY`` 会把每一封读不懂的信变成一条销售交接（§7 里最贵的处置），
    猜别的每一个分类都带着一条会被执行的副作用。
    """
    answer = classify(classifier, body)
    assert answer.abstained, f"读出了 {answer.kind}：{answer.reason}"
    assert answer.kind is None
    assert answer.reason.strip(), "弃权必须说得出为什么"


def test_an_empty_body_abstains_for_its_own_reason(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """空正文与「没有命中任何规则」是**两条不同的理由**。

    它们处置一样（人工审核），而线索完全不同：前者说内容可能在附件里、在 HTML
    那一段里；后者说词表不够用。写成一模一样的理由会让读队列的人无从判断该补的是
    解析还是词表。
    """
    empty = classify(classifier, "")
    no_hit = classify(classifier, "Tuesday afternoon works for me.")
    assert empty.abstained and no_hit.abstained
    assert empty.reason != no_hit.reason
    assert "空的" in empty.reason or "空" in empty.reason

    # 只有空白字符也算空 —— 一个按 ``strip()`` 判的实现与一个按 ``== ""`` 判的
    # 实现在这里分岔，而「一行空格」是真实存在的正文形状（引用拆行）。
    assert classify(classifier, "  \n\t ").reason == empty.reason


def test_the_subject_decides_nothing(classifier: RuleBasedReplyClassifier) -> None:
    """判据只看**正文**。

    主题是对方可以随手改的东西（``CorrespondenceEvent`` 的类 docstring 逐字：
    去重不靠主题）；而一个把主题也扫进去的实现会让一封主题里写着「Re: unsubscribe
    confirmation」的正常回信被判成退订 —— 那是永久抑制。
    """
    answer = classify(classifier, "Tuesday afternoon works.", subject="UNSUBSCRIBE ME NOW")
    assert answer.abstained, f"主题参与判据了：{answer.kind}"


def test_a_pasted_bounce_text_is_not_a_bounce(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """粘贴了一段退信原文的回信**不是**一封退信。

    退信是**收信服务器按 RFC 回的**，它的判据是结构（``multipart/report;
    report-type=delivery-status`` 里的 ``Status:``），由来源层判 —— 判决那一条判据
    的是 ``tests/test_mail_capture_source.py``。这里若按正文里的 ``Status: 5.1.1``
    判，一封「你看，你们的系统给我回了这个」的真人回信会被记成一个不存在的地址，
    处置是永久抑制。
    """
    pasted = (
        "FYI — I got this back when I replied to you earlier:\n"
        "Status: 5.1.1\nAction: failed\nFinal-Recipient: rfc822; buyer@example.invalid\n"
    )
    answer = classify(classifier, pasted)
    assert answer.kind not in (
        CorrespondenceKind.HARD_BOUNCE,
        CorrespondenceKind.SOFT_BOUNCE,
    ), "按正文判了退信 —— 那不是这一层的判据"


def test_the_literal_alternate_contact_is_not_judged_here(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """「请联系我的同事」**弃权**，由 ``_decide`` 的最后一步判。

    §7 逐字：「字面上的替代联系人 → awaiting_Member_A_confirmation，不自动重发或新建
    外联」。那条判据住在 ``application/correspondence.py::_alternate_contact``，
    判据是「一个线索短语**并且**恰好一个与发件人不同的地址」两件事同时成立。
    两处都判会让「换不换人」有两个答案，而这两个答案是会分岔的。
    """
    for body in (
        "Please contact my colleague at colleague@example.invalid — I've moved teams.",
        "请把我的同事 X 的电话给我。",
    ):
        answer = classify(classifier, body)
        assert answer.abstained, f"{body!r} 被判成了 {answer.kind}"


# ======================================================================
# 结构自报优先
# ======================================================================


def test_a_structural_self_report_is_taken_as_given(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """来源给了分类就照用，**不读正文**。

    来源的结构性判据（一封退信是收信服务器按 RFC 回的）比一张词表强；让词表去
    「再确认一下」只是给了它一次推翻确定事实的机会。
    """
    answer = classify(
        classifier,
        "Tuesday afternoon works for me.",
        kind=CorrespondenceKind.HARD_BOUNCE,
    )
    assert answer.kind is CorrespondenceKind.HARD_BOUNCE
    assert "来源" in answer.reason


def test_the_callers_placeholder_is_not_a_signal(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """``HUMAN_REPLY`` **不是**一个来源自报 —— 它是调用方的占位符。

    ``_kind_of`` 造那条交给分类器的事件时，``kind`` 那一格填的正是 ``HUMAN_REPLY``
    （它要构造一个合法的 ``CorrespondenceEvent``，而那一格是必填的）。把它当成
    「来源说了这是真人回信」，会让**每一个**分类器调用都直接返回 ``HUMAN_REPLY``
    —— 也就是把每一封来信都变成一条销售交接。
    """
    # 默认构造的这一格就是 HUMAN_REPLY（见 make_event）：正文该被判。
    assert make_event(body="unsubscribe me").kind is CorrespondenceKind.HUMAN_REPLY
    answer = classify(classifier, "Please unsubscribe me.")
    assert answer.kind is CorrespondenceKind.UNSUBSCRIBE, (
        "调用方的占位符被当成了来源的自报"
    )

    # 而一封真的真人回信在这个占位符下仍然是弃权 —— 两条一起才说明这一格
    # 既没有被忽略（上一条），也没有被当成信号（这一条）。
    assert classify(classifier, "Tuesday works.").abstained


# ======================================================================
# 纯度：无 I/O、无时钟、可复算
# ======================================================================


def test_the_same_event_always_gets_the_same_answer(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """同一个事件永远得到同一个结论 —— 这是它比模型实现**多**出来的那一条性质。"""
    event = make_event(body="Please unsubscribe me from your list.")
    first = classifier.classify(event=event)
    second = classifier.classify(event=event)
    assert first == second

    # 另一条同内容的**新**事件（不同的 id、不同的原件路径、不同的时刻）也得到
    # 同一个结论：判据只认正文，其余几格一个都不参与。
    other = make_event(
        body="Please unsubscribe me from your list.",
        event_id="event-0002",
        provider_message_id="msg-0002",
        raw_ref="/nonexistent/isolation-check/msg-0002.eml",
    )
    assert classifier.classify(event=other).kind is first.kind


def test_it_reads_no_original_and_keeps_no_state(
    classifier: RuleBasedReplyClassifier,
) -> None:
    """``raw_ref`` 指向一个不存在的路径也照样分类 —— 本模块不读文件、不留状态。

    读文件是**来源层**的事（``adapters/mail_capture_source.py``）。一个在读文件时
    抛的分类器，会让「分类失败」与「原件读不到」在记录上混成一件事 ——
    而前者的处置是「修分类器」，后者是「先看看盘上的东西还在不在」。
    """
    body = "Please take me off your list."
    absent = make_event(body=body, raw_ref="/nonexistent/isolation-check/gone.eml")
    assert classifier.classify(event=absent).kind is CorrespondenceKind.UNSUBSCRIBE

    # 无状态：一次命中不会让下一次的结论变。用同一个实例交替地问一句话与一段
    # 无关的话，两次都要是对的那一个 —— 一个把上一次的结论缓存下来的实现在这里
    # 会给出「上一次的那个分类」。
    for _ in range(3):
        assert classifier.classify(event=make_event(body=body)).kind is (
            CorrespondenceKind.UNSUBSCRIBE
        )
        assert classifier.classify(event=make_event(body="Tuesday works.")).abstained

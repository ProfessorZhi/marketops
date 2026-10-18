"""收件人判据接在传输层前面（§6 三道闸的第三道，接线那一步）。

## 这一层判的是「判据有没有被执行」，不是「判据对不对」

判据本身（什么算在名单上、空名单什么意思、域名条目怎么比）由
``tests/test_security_egress.py`` 守着，那是纯函数的判据。本文件问的是另一件事：
**那个纯函数真的站在门口吗**。一个写得完全正确的 ``check_recipient``，如果在
接线时没有被人包上去，整套测试照样全绿，而每一封信照发 —— 这是本仓
``test_adapters_hold_the_vendors.py`` 模块 docstring 里那种「契约成了摆设」的失败
换一个位置重演。

所以下面所有判据的对照面都是**一个记账的假传输**：放行的那些要求它真的被调到，
被拒的那些要求它**一次都没有**被调到。「没被调到」这件事只由一个记账对象说得出来，
而这里每一次拒绝旁边都有一条放行，用来证明这个记账对象不是永远记零。

## 两个方向的错法都要有判据

名单拒掉一封信时，这一层可以错两次，而两次的方向相反：

* 把该拒的**放过去** —— 由「内层没有被调用」那组守着。
* 把不该拒的**换一种说法拒掉**：比如把内层的异常吞掉、改写成自己的 ``refused``。
  后者在报告上看起来只是一次「被拒」，而那一封信可能**已经出去了**（抛的那一类
  失败正是「可能出去了」）。所以下面有一条判据要求内层的异常原样穿过去。
"""

from __future__ import annotations

import pytest

from marketops.adapters.guarded_mail import GuardedMailTransport
from marketops.domain.outreach import DraftRef, ManifestEntry, OutreachStage
from marketops.domain.util import normalise_email, sha256_text
from marketops.ports.outreach import MailTransport, SendReceipt
from marketops.security.egress import parse_allowlist

SENDER = "ops@corp.example"
ALLOWED = "someone@acme.example"
STRANGER = "stranger@elsewhere.example"
BODY = "hello"
SUBJECT = "hello"

#: 与 ``test_security_egress.py`` 同一份形状：一条地址条目 + 一条域名条目。
#: 两条落在**不同**的域上，理由见那个文件里 ``LIST`` 的注释。
LIST = parse_allowlist(f"{SENDER}, @acme.example")


def make_entry(*, recipient: str) -> ManifestEntry:
    """清单里的一行。只构造它要的那几格，其余按最小合法值给。

    ``draft`` 每次新建而不是共用一个模块级常量：``DraftRef`` 是 pydantic 模型，
    共享实例会让「谁改了它」这一类问题在测试之间传播。
    """
    return ManifestEntry(
        draft=DraftRef(draft_id="draft-0001", draft_version=1, body_sha256=sha256_text(BODY)),
        route_ref="route-0001",
        account_id="test-account-001",
        recipient=normalise_email(recipient),
        sendbox=SENDER,
        stage=OutreachStage.STAGE_1,
        family_id="family-0001",
        writing_policy_id="policy-0001",
        evidence_ids=("evidence-0001",),
    )


class _Spy(MailTransport):
    """记账的传输。**放行的那一组判据要它真的被调到。**

    ``receipt=None`` 时它**抛 AssertionError**，而不是返回一个假的受理：被拒的
    那一组判据里，被调用到就是失败，而一个「返回点什么」的实现会让那条判据
    依赖返回值的形状，而不是依赖「它有没有被叫」这件真正要测的事。
    """

    def __init__(self, *, receipt: SendReceipt | None = None, sender: str = SENDER) -> None:
        self.calls: list[dict[str, object]] = []
        self.status_calls: list[str] = []
        self._receipt = receipt
        self._sender = sender

    def send(
        self, *, entry: ManifestEntry, subject: str, body: str, from_address: str
    ) -> SendReceipt:
        self.calls.append(
            {"entry": entry, "subject": subject, "body": body, "from_address": from_address}
        )
        if self._receipt is None:
            raise AssertionError("内层传输被调用了 —— 名单没有拦住这一封")
        return self._receipt

    def status(self, message_id: str) -> SendReceipt:
        self.status_calls.append(message_id)
        return SendReceipt(outcome="unknown", accepted=False, reason=f"spy asked about {message_id}")

    @property
    def sender_identity(self) -> str:
        return self._sender


def _run(
    guard: GuardedMailTransport, *, recipient: str = STRANGER
) -> SendReceipt:
    return guard.send(
        entry=make_entry(recipient=recipient),
        subject=SUBJECT,
        body=BODY,
        from_address=SENDER,
    )


# ----------------------------------------------------------------------
# 拒绝：内层一次都没有被调到
# ----------------------------------------------------------------------
def test_a_recipient_off_the_list_never_reaches_the_inner_transport() -> None:
    spy = _Spy()
    guard = GuardedMailTransport(spy, allowlist=LIST)

    receipt = _run(guard)

    assert receipt.outcome == "refused"
    assert not receipt.accepted
    assert spy.calls == [], (
        "名单之外的收件人被交给了内层 —— 那一刻信已经在路上了，"
        "而这一层存在的全部理由就是它不该被交出去"
    )


def test_the_refusal_says_that_we_refused_it_and_not_the_provider() -> None:
    """理由里必须点名是谁拒的，且 ``raw_response`` 必须是空的。

    ``refused`` 在另一个实现里是**服务端**回的（``SmtpMailTransport`` 把
    ``SMTPRecipientsRefused`` 的原文放进去）。这里提供方从头到尾没有说过一个字；
    把我们的拒绝写成一句没有主语的短句，读报告的人会去服务端的应答里找一个不存在
    的错误码。``raw_response`` 那一格同理：写进去的任何内容都是**编造对方的原话**。
    """
    guard = GuardedMailTransport(_Spy(), allowlist=LIST)

    receipt = _run(guard)

    assert "收件人允许列表拒绝了这一封" in receipt.reason
    assert "not_allowed" in receipt.reason, (
        "理由码没进文本 —— 报告里那一条于是与「地址写坏了」长得一样，"
        "而两者的处置相反（去找批准人 / 去改配置）"
    )
    assert STRANGER in receipt.reason
    assert receipt.raw_response == ""
    assert receipt.message_id == ""


def test_the_refusal_is_a_receipt_and_not_a_raise() -> None:
    """**返回**，不抛。抛的处置是 ``DELIVERY_UNKNOWN``，而那是另一件事。

    ``application/outreach.py::dispatch_wave`` 对异常记 ``DELIVERY_UNKNOWN``，
    理由是「抛 = 可能已经出去了，先对账，不得重发」。被这一层拒掉的信
    **结构上没有可能出去**：内层没有被调用过。记成 ``UNKNOWN`` 会在账上留下一句
    「这封信也许在对方手里」，而后续对账会拿一个不存在的投递去问提供方。
    """
    guard = GuardedMailTransport(_Spy(), allowlist=LIST)

    receipt = _run(guard)  # 抛出的话这一行就红了

    assert isinstance(receipt, SendReceipt)
    assert receipt.outcome == "refused"


def test_an_empty_allowlist_refuses_every_recipient() -> None:
    """空名单 = 谁都不许，而**这一条与下面那条放行成对**。

    一个「名单空就照发」的实现会在一次漏配之后把每一封信都发出去，而记录上一切
    正常 —— 这正是本仓在两处（``Principal.workspaces``、``check_recipient``）都
    把缺省选在「停」这一边的理由。
    """
    spy = _Spy()
    guard = GuardedMailTransport(spy, allowlist=frozenset())

    receipt = _run(guard, recipient=SENDER)  # 一个**本来**在名单上的地址

    assert receipt.outcome == "refused"
    assert "no_allowlist" in receipt.reason
    assert spy.calls == []


# ----------------------------------------------------------------------
# 放行：判据的对照面
# ----------------------------------------------------------------------
def test_an_allowed_recipient_reaches_the_inner_transport_and_its_receipt_is_returned() -> None:
    """放行时内层被调到，且**回话逐字是内层那一个**。

    断言的是同一个对象（``is``）而不是「等价的回话」：这一层没有任何东西可以
    替提供方说。它若重拼一个 ``SendReceipt``，``message_id`` 或 ``raw_response``
    里就会有本层自己造的内容 —— 而那一格是事后对账唯一的键。
    """
    inner_receipt = SendReceipt(
        outcome="accepted", accepted=True, message_id="<abc@corp.example>", raw_response="250 Ok"
    )
    spy = _Spy(receipt=inner_receipt)
    guard = GuardedMailTransport(spy, allowlist=LIST)

    receipt = _run(guard, recipient=ALLOWED)

    assert receipt is inner_receipt
    assert len(spy.calls) == 1


def test_the_entry_is_handed_over_untouched() -> None:
    """转交的是**同一个**清单项，不是一份规范化过的副本。

    ``ports/outreach.py`` 要求传输层不得改写正文，而「改写」也包括顺着地址做一次
    规范化：``check_recipient`` 内部会规范化它比对用的字符串，但那个字符串不该
    变成投递用的那一份。收件人信封由 ``entry.recipient`` 决定，本层不碰它。
    """
    spy = _Spy(receipt=SendReceipt(outcome="accepted", accepted=True, message_id="<x@corp.example>"))
    guard = GuardedMailTransport(spy, allowlist=LIST)
    entry = make_entry(recipient=ALLOWED)

    guard.send(entry=entry, subject=SUBJECT, body=BODY, from_address=SENDER)

    call = spy.calls[0]
    assert call["entry"] is entry
    assert call["subject"] == SUBJECT
    assert call["body"] == BODY
    assert call["from_address"] == SENDER


def test_a_domain_entry_lets_the_address_through() -> None:
    """域名条目在**接线之后**也生效 —— 判据接上之后语义没有变窄。"""
    spy = _Spy(receipt=SendReceipt(outcome="accepted", accepted=True, message_id="<y@corp.example>"))
    guard = GuardedMailTransport(spy, allowlist=LIST)

    receipt = _run(guard, recipient="Another.Person@acme.example")

    assert receipt.accepted
    assert len(spy.calls) == 1


def test_the_guard_judges_each_letter_on_its_own() -> None:
    """逐封判，不是逐批判。

    一批里混着一个不该收信的人时，整批被拒与逐封拒在报告上长得完全不同：前者
    会让同一个 Wave 里其余那些**本来能发**的人一起停下来，而理由是别人的。
    """
    spy = _Spy(receipt=SendReceipt(outcome="accepted", accepted=True, message_id="<z@corp.example>"))
    guard = GuardedMailTransport(spy, allowlist=LIST)

    first = _run(guard, recipient=STRANGER)
    second = _run(guard, recipient=ALLOWED)

    assert (first.outcome, second.outcome) == ("refused", "accepted")
    assert len(spy.calls) == 1


# ----------------------------------------------------------------------
# 穿过去的东西与本类**不**提供的东西
# ----------------------------------------------------------------------
def test_an_inner_failure_passes_through_instead_of_becoming_a_refusal() -> None:
    """内层的异常**原样穿过去**，不被改写成 ``refused``。

    这是与上面那组相反方向的错法，而它更隐蔽：把一次连接中断记成「被允许列表
    拒绝」，账上是一行读起来完全正常的失败，而那一封信**可能已经出去了**。抛是
    这一层能给的最诚实的一种回答 —— 契约写明了调用方对抛的处置（先对账）。
    """

    class _Exploding(_Spy):
        def send(self, **kwargs: object) -> SendReceipt:
            raise ConnectionResetError("连接断了，而这可能发生在提交之后")

    guard = GuardedMailTransport(_Exploding(), allowlist=LIST)

    with pytest.raises(ConnectionResetError):
        _run(guard, recipient=ALLOWED)


def test_status_is_asked_of_the_inner_transport() -> None:
    """对账问的是内层，且这一层**不**判名单 —— 理由写在方法的 docstring 里。

    名单会变：今天不在名单上的人，可能正是那一封按当时的名单发出去的信的收件人。
    在这里再判一次，会让一次对账问答成「被允许列表拒绝」，而被问的那封信早就
    出去了 —— 把**当下的**策略读成**过去的**事实。
    """
    spy = _Spy()
    guard = GuardedMailTransport(spy, allowlist=LIST)

    receipt = guard.status("<abc@corp.example>")

    assert spy.status_calls == ["<abc@corp.example>"]
    assert receipt.outcome == "unknown"


def test_sender_identity_is_the_inner_transports_identity() -> None:
    """报的是内层的发送箱。调换两者的后果是可诊断的失配。

    调用方拿清单上的发送箱与 ``sender_identity`` 比对，不一致就拒绝发送。包装层
    报一个自己的名字，会让那个比对当场失配，而失配在报告上长得像一次配置错误。
    """
    guard = GuardedMailTransport(_Spy(sender="real@corp.example"), allowlist=LIST)

    assert guard.sender_identity == "real@corp.example"


def test_the_raw_transport_is_not_on_the_public_surface() -> None:
    """本类不把内层交出去。

    这条判据守的是模块 docstring 里那句「接线的那一方不要同时留着内层传输的
    引用」—— 它没有别的执行器。而**自然的实现会提供这个入口**：调试与测试都想要
    它。所以这里断的是「公开属性里没有任何一个是那个内层传输」，不是「属性不存在」。
    """
    spy = _Spy()
    guard = GuardedMailTransport(spy, allowlist=LIST)

    exposed = [
        name
        for name in dir(guard)
        if not name.startswith("_") and getattr(guard, name) is spy
    ]

    assert exposed == [], (
        f"{exposed} 把内层传输交出去了 —— 于是「闸门在接线那一步」这句话"
        "可以被任何一处拿到它的代码绕过，而绕过它的那一行看起来完全正常"
    )


def test_the_allowlist_in_force_is_readable() -> None:
    """当下生效的那份名单读得出来。放行判据的对照面在这一侧。

    一份写着「这一轮拒了 3 位收件人」的报告，如果没有任何一处说得出**比对的是
    哪份名单**，那 3 条就无法核对 —— 而核对它正是决定「去改配置还是去找批准人」
    的那一步。
    """
    guard = GuardedMailTransport(_Spy(), allowlist=LIST)

    assert guard.allowlist == LIST

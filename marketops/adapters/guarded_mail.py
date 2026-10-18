"""``GuardedMailTransport`` —— 在**每一封信**出门之前问一次收件人判据。

## 它守着三道闸里的哪一道

§6 逐字要求测试模式「从网络出口、凭据、收件人允许列表三个层面防止真实外发」。
判据的本体在 ``marketops/security/egress.py``（纯函数，不碰网络）；本模块是**接线
那一步** —— 它把那个纯函数放到传输层的前面，于是「允许」不再是一句可以由调用方
选择执行的话。

三道闸的分工与各自的失败方式写在 ``security/egress.py`` 的模块 docstring 里，
不在这里重抄。这里只写这一层独有的两件事。

## 为什么是**装饰器**，不是每个实现里各写一遍

``MailTransport`` 现在已经有两个实现（``smtp_mail`` / ``mail_capture_transport``），
而判据如果写进实现里，第三个实现出现时**没有任何一条判据会响**：它照样实现 ABC、
照样被注入、照样通过 ``isinstance``，只是不再过名单。这与
``tests/test_adapters_hold_the_vendors.py`` 的模块 docstring 记的是同一种失败 ——
「端口还在、契约还在，只是从此没有人再从它走」。

包一层则不同：**闸门在接线那一步，且只在那里**。实现有几个、将来是谁写的，都不
改变「出门之前问了名单」这件事。

## 拒绝是 ``refused``，**不是抛**

这一格是本模块最容易被写错的地方，而两种写错的方向都有人会选：

* **抛** —— 调用方（``application/outreach.py::dispatch_wave``）对抛出的异常记
  ``DELIVERY_UNKNOWN``，理由是「抛 = 可能已经出去了，先对账，不得重发」。而被
  名单拒掉的这一封信**结构上没有可能出去**：内层传输一次都没有被调用。用
  ``UNKNOWN`` 记它，等于在账上写一句「这封信也许在对方那里」—— 而后续的对账会
  拿一个不存在的投递去问提供方，问不到，于是那条记录永远悬着。
* **当成 ``accepted``** —— 更坏，且不在这里讨论。

所以它返回 ``outcome="refused"``：三个取值里唯一一个意思是「确定没出去，且说得
出为什么」的。``application`` 把它翻成 ``MessageState.FAILED``，那一行于是在报告
里以「被拒 + 理由」的样子出现，而不是以「状态未知」的样子挂着。

**但理由里必须点名是谁拒的。** ``refused`` 在另一个实现里来自服务端
（``SmtpMailTransport`` 把 ``SMTPRecipientsRefused`` 的原文放进去），而这里提供方
从头到尾没有说过一个字。把我们的拒绝写成没有主语的短句，读报告的人会去服务端的
日志里找一个不存在的错误码 —— 所以 :meth:`GuardedMailTransport.send` 拼的那句话
以「收件人允许列表拒绝了这一封」开头，且 ``raw_response`` 留空（一个字的提供方
原话都没有，因为提供方没说话）。

## 空名单**可以构造**，而它的意思是「谁都不许」

一个「空名单就别构造了」的构造期拒绝看起来更严，代价却是把这一格推开：写下配置
的人会拿到一条启动期的异常，而修它的最短路径是往名单里塞一条**不是**批准的东西
（``*``、一个域名、或者随手一个地址）好让服务起来 —— 那正是这份名单最不该发生的
一次编辑。留它可构造，一次漏配的表现就是**每一位收件人各被拒一次、理由逐条写清**，
而那是一条会被人去改配置的反馈。

``security/egress.py`` 里那条判据本身也是这么定的（空 = 停），两处方向一致。

## 它住在 ``adapters/``，而它**不持厂商**

这是个诚实的位置说明：本模块实现一个端口，而端口的实现住在 ``adapters/``；但它
一行厂商 SDK 都不 import，所以 ``tests/test_adapters_hold_the_vendors.py`` 的**组合
根**判据看不见它（那条判据算的是「通往厂商的闭包」，闭包外的模块不在它的射程内）。
``mail_capture_transport.py`` 与 ``mail_capture.py`` 是同样的情形，不是新事。

因此有一条只能写在这里、没有执行器的规矩：**接线的那一方不要同时留着内层传输的
引用。** 组合根构造它、把内层交给它、然后手上只剩它 —— 否则「闸门在接线那一步」
这句话就可以被后加的一行绕过，而绕过它的那一行看起来完全正常。
:class:`GuardedMailTransport` 因此**不**提供取回内层的公开入口（见
``test_the_raw_transport_is_not_on_the_public_surface``）。
"""

from __future__ import annotations

from ..domain.outreach import ManifestEntry
from ..ports.outreach import MailTransport, SendReceipt
from ..security.egress import RecipientVerdict, check_recipient

__all__ = ["GuardedMailTransport"]


class GuardedMailTransport(MailTransport):
    """包住另一个 ``MailTransport``，逐封问一次收件人允许列表。

    Args:
        inner: 真正投递的那一个。**它不因为被包住就变安全** —— 谁手里有它，谁就
            绕过了这道闸；本类不把它交出去，接线那一方也应当只留包装后的这一个。
        allowlist: ``security/egress.parse_allowlist`` 读出来的那一份。空集是合法
            输入，意思是「谁都不许」（见模块 docstring）。
    """

    def __init__(self, inner: MailTransport, *, allowlist: frozenset[str]) -> None:
        self._inner = inner
        self._allowlist = allowlist

    @property
    def allowlist(self) -> frozenset[str]:
        """本闸门当下生效的那份名单（只读）。

        它不是内层传输，交出去安全。存在的理由是报告：一份写着「这一轮拒了 3 位
        收件人」的报告，如果没有一处说得出**比对的是哪份名单**，那 3 条就无法核对
        —— 而核对它正是决定「该去改配置还是该去找批准人」的那一步。
        """
        return self._allowlist

    def send(
        self,
        *,
        entry: ManifestEntry,
        subject: str,
        body: str,
        from_address: str,
    ) -> SendReceipt:
        """先问名单，再交给内层。**被拒时内层一次都不会被调用。**

        ``entry`` / ``subject`` / ``body`` / ``from_address`` 在放行时**原样**转交
        （不是等价的副本）：``ports/outreach.py`` 的契约要求这一层不得改写正文，而
        「改写」包括顺着地址做一次规范化。``check_recipient`` 内部会规范化它拿到的
        字符串做比对，但比对用的字符串不会变成投递用的那一份 —— 一封信的收件人
        信封由 ``entry.recipient`` 决定，本层不碰它。
        """
        verdict = check_recipient(entry.recipient, allowlist=self._allowlist)
        if not verdict.allowed:
            return _refusal(entry.recipient, verdict)
        return self._inner.send(
            entry=entry, subject=subject, body=body, from_address=from_address
        )

    def status(self, message_id: str) -> SendReceipt:
        """原样问内层。**这一层不判名单**，而理由是一次真实的历史问题。

        名单是一份**会变**的配置：今天不在名单上的人，可能正是那一封**去年按当时的
        名单发出去**的信的收件人。在这里再判一次名单，会让一次对账问答成「被允许
        列表拒绝」—— 而被问的那封信早就出去了。那是把一条**当下的**策略读成
        **过去的**事实，且读法是错的。
        """
        return self._inner.status(message_id)

    @property
    def sender_identity(self) -> str:
        """内层的发送箱。**不是本类的身份** —— 本类没有身份。

        调换两者的后果是可诊断的：调用方拿清单上的发送箱与它比对，不一致就拒绝
        发送（``ports/outreach.py``）。包装层报一个自己的名字，会让那个比对当场
        失配，而失配的原因在报告上长得像一次配置错误。
        """
        return self._inner.sender_identity


def _refusal(address: str, verdict: RecipientVerdict) -> SendReceipt:
    """把一次判定翻成回话。**理由码进文本，且主语是我们。**

    ``refusal`` 在契约里是非空的（``RecipientVerdict`` 的约定），所以这里可以直接
    取它的 ``value``；写成 ``None`` 时的兜底会让一个契约违反变成一句读起来正常的
    报告。
    """
    refusal = verdict.refusal.value if verdict.refusal is not None else "unknown"
    return SendReceipt(
        outcome="refused",
        accepted=False,
        reason=(
            f"收件人允许列表拒绝了这一封（{refusal}）：{verdict.detail}\n"
            f"被拒的收件人是 {address!r}。\n"
            "这一条来自**我们**这一侧，提供方没有参与这次判定 —— 它没有收到过这封"
            "信，也没有回话，所以 raw_response 是空的。"
        ),
    )

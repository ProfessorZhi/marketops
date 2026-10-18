"""``MailCaptureTransport`` —— ``MailTransport`` 的第二个实现，直接对一个同进程的
捕获服务写。

## 它守的失败，与 ``smtp_mail`` **不同**

``smtp_mail.py`` 守的是「字节真的离开了这个进程」。本模块守的是另一件事：
**同一组判据在第二个实现上还成不成立**。本仓的纪律是「端口在第二个实现出现时才
提取」—— 一个只有单一实现的端口，它的契约是按那一个实现的形状长出来的，而这一点
在第二个实现落地之前**看不出来**：ABC 还在、注入还在、``isinstance`` 还在。

第二个实现因此不是装饰。它必须与第一个**真的不同**，所以这一条不起 TCP：它的远端
是这个进程里的一个对象，于是它有一个第一个实现**结构上不可能有**的能力 ——
它可以直接被问「你收到了什么」（``read_index``）。而它的失败也来自这一点：
一个与远端同进程的传输层可以把「远端的内存状态」当成投递事实，于是它必须比第一个
实现更小心地**不**回答自己答不出的问题（见 :meth:`MailCaptureTransport.status`）。

## 它与 ``smtp_mail`` 共用「这封信长什么样」，不共用「怎么送」

``compose_message`` / ``to_wire_bytes`` 从 ``smtp_mail`` 引入而不是各写一份：
共用的是**报文本身**（主题、正文、Message-ID 的派生），而两个实现真正的差别在
投递那一段。各写一份拼接的代价不是重复，而是「同一个 Message-ID」变成一个巧合 ——
而巧合在第三个实现出现的那一天会以「两封信带同一个标识、而第三封信带另一个」的
样子暴露出来，那时没有人会想到问题在拼接那一层。

它**不继承** ``SmtpMailTransport``：那是另一个实现，不是它的特例。
"""

from __future__ import annotations

from ..domain.outreach import ManifestEntry
from ..ports.outreach import MailTransport, SendReceipt
from .mail_capture import MailCaptureServer, ReceivedMessage, RecipientRefused
from .smtp_mail import compose_message, to_wire_bytes


class MailCaptureTransport(MailTransport):
    """把信直接交给一个同进程的 :class:`~marketops.adapters.mail_capture.MailCaptureServer`。

    Args:
        capture: 捕获服务。**不需要**它已经在听：不在听时 ``send`` 会抛
            （见 :class:`~marketops.adapters.mail_capture.MailCaptureUnavailable`），
            而「服务停了之后不是受理」这件事正是判据的一部分。
        from_address: 发送箱地址，同时是 :attr:`sender_identity` 的答案。
    """

    def __init__(self, capture: MailCaptureServer, *, from_address: str) -> None:
        if "@" not in from_address:
            raise ValueError(
                f"from_address={from_address!r} 不像一个邮箱地址。\n"
                "Message-ID 的右半边取自它的域，而一个没有域的发送箱地址会让那个"
                "标识指向一个不存在的地方 —— 对方回信时唯一的线索就是它。"
            )
        self._capture = capture
        self._from_address = from_address

    @property
    def capture(self) -> MailCaptureServer:
        """它写的那台捕获服务。测试需要它来读回收到的东西。"""
        return self._capture

    def send(
        self,
        *,
        entry: ManifestEntry,
        subject: str,
        body: str,
        from_address: str,
    ) -> SendReceipt:
        """把信交给捕获服务。**只有拒绝名单是 ``refused``，其余照抛。**

        与第一个实现同一条纪律（``ports/outreach.py``）：服务没在听时**抛**，
        不返回一个结论。这里特别值得写下来的是：一个同进程的实现可以很容易地
        在服务没在听时「顺手把消息记在某处」，而那就变成了一次**没有任何东西
        收到**的受理 —— 盘上它与一次真的投递长得一模一样。
        """
        message = compose_message(
            entry=entry, subject=subject, body=body, from_address=from_address
        )
        try:
            received = self._capture.ingest(
                to_wire_bytes(message),
                mail_from=from_address,
                recipients=(entry.recipient,),
            )
        except RecipientRefused as exc:
            return SendReceipt(outcome="refused", accepted=False, reason=str(exc))
        return SendReceipt(
            outcome="accepted",
            accepted=True,
            message_id=received.message_id,
            raw_response=_capture_reply(received),
        )

    def status(self, message_id: str) -> SendReceipt:
        """这个后端也**答不出来** —— 而且它答不出的理由比第一个实现更值得写下来。

        捕获服务**确实**在 ``index.jsonl`` 里留着那个 message id，所以这里完全
        可以「查到」它。但那回答的是「**我们**捕获到了这封信」，不是「**对方**收到
        了这封信」—— 后者才是这个接口问的事（``ports/outreach.py`` 里
        ``ACCEPTED`` 与 ``DELIVERED`` 是两个取值，正是为这件事分的）。

        一个在索引里查到就把 ``accepted`` 返回的实现，会让「投递事实」这个词在
        两种实现上各指一件事，而它在报告上看起来只是「查得到」。
        """
        return SendReceipt(
            outcome="unknown",
            accepted=False,
            reason=(
                f"这个后端不支持按 message id 查询（问的是 {message_id!r}）："
                "捕获服务只记录「我们收下了这一封信」，它没有对面那一侧的事实 —— "
                "索引里查得到那个标识，回答的是「我们捕获到了它」，不是「对方收到了它」。\n"
                "所以这里回的是「我不知道」，不是「它没发出去」。"
            ),
        )

    @property
    def sender_identity(self) -> str:
        """构造时给的那个发送箱地址。"""
        return self._from_address


def _capture_reply(received: ReceivedMessage) -> str:
    """这个远端「说了什么」，逐字。

    **不编一条 ``250``**。第一个实现那一格是服务端原样回的话；这里没有一句话
    可以逐字留 —— 远端是一个对象，它的回答就是它记下的那两样（序号与落盘路径）。
    编一条 ``250 2.0.0 Ok: queued as …`` 会让「提供方说了什么」在两种实现上看起来
    一样，而它们不是一样的：对账的人据此会去找一条根本不存在的应答。
    """
    return f"captured seq={received.seq} path={received.path}"


__all__ = ["MailCaptureTransport"]

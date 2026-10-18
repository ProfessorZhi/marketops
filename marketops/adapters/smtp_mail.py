"""``SmtpMailTransport`` —— ``MailTransport`` 的第一个实现，直接对 SMTP 说话。

## 它守的失败，与另一个实现**不同**

``mail_capture_transport.py`` 守的是「同一组判据在第二个实现上还成不成立」——
它的远端在这个进程里，它可以被问「你收到了什么」。本模块守的是另一件事：
**字节真的离开了这个进程**。它是全仓唯一一个把内容交给外界的实现，因此它最大的
失败不是「发错了」，而是「**替调用方猜了一个结论**」：

* 一次「连不上」与一次「对面可能收了」在实现里长得几乎一样，而它们的处置相反
  （前者可以重试，后者要先对账）。所以这里只把**两个名字明确**的拒绝
  （``SMTPRecipientsRefused`` / ``SMTPSenderRefused``）读成 ``refused``，
  其余**一律抛**（``ports/outreach.py`` 写明了这件事）。抛不等于 ``UNKNOWN``，
  但调用方会把抛出的记成 ``DELIVERY_UNKNOWN`` —— 那是安全的那一边，
  而实现分不清时替它猜，猜错的代价是一个人收到两封信。

## 默认超时**不能**是 ``None``

``smtplib`` 的 ``timeout`` 缺省就是 ``None``，意思是「永不超时」。一个卡住的连接
在那一格上会占住整轮执行，而现象是「跑着跑着不动了」—— 它比一次失败难查得多，
因为它不产生任何一条记录。所以这里的超时是一个**显式的数**，而且 ``None`` 会被
**拒绝**，不是被替换成默认值：把 ``None`` 静默换成 10 秒的实现，会让写下 ``None``
的人以为他要的「不超时」生效了。

## Message-ID 是稳定的，但**不得**被读成去重

它由 ``entry.draft.body_sha256`` 与 ``entry.recipient`` 派生，域取 ``from_address``
的域。§6 要求稳定的 Message-ID —— 稳定的意思是「同一封信发给同一个人，重试时
带的是同一个标识」，于是对方与我们自己都能把两次尝试认成同一件事。

但它**不是去重的执行器**：去重落在 ``OutboundAttempt.send_key`` 上，那是我们
自己算的、与对方无关的键。两个不同的系统各自保证一件事，合并它们等于把「对方的
服务器怎么想」当成「我们发过没有」。所以：两个收件人地址不同而正文相同的两封信，
Message-ID **不同**（收件人在派生里）；而同一封信发两次，Message-ID **相同**，
哪怕第二次被服务端收下了 —— 那正是需要被看见的情形，不是需要被去重的情形。

## 附件：本层**不读磁盘**

``ManifestEntry.draft`` 上没有附件字段（``OutreachDraft.attachments`` 是
``(文件名, 内容摘要)``，那是一个摘要，不是内容）。所以本实现**不添加附件** ——
它不是「暂时没做」，而是这一层拿不到内容：拿两个摘要拼不出一个字节。要带附件，
附件必须在**正文侧**就已经是一封完整的报文（由调用方保证），因为一个会去按名字
打开文件、把内容读进来再发出去的传输层，会让「批准的是哪一份内容」这个问题的
答案取决于**发送那一刻磁盘上有什么**。

## 凭据只在 TLS 上出示，而且出示方式**没有**一个"自动"

``SMTP`` 的连接可以完全不认证（``host:port``），也可以协商 TLS 之后用
``AUTH`` 登录。这一层两种都做，但**哪一种不由它猜**：``security`` 与
``username``/``password`` 是一对参数，给一半就构造不出来。

一个「有凭据就自动 STARTTLS」的实现看起来更省事，它把一次取舍藏进了一次运行时
判断：同一份配置在中继支持 STARTTLS 时把口令发在 TLS 上、在不支持时**退回明文**
（或者直接抛）—— 而这两种行为在配置里长得一模一样，在报告上甚至都是「发送成功」
或「发送失败」。所以这里让写下配置的人**点明**是 ``starttls`` 还是 ``ssl``
（``config.py`` 的 ``SMTP_SECURITY_MODES``），并且：

* 中继不宣告 STARTTLS 时 ``smtplib`` 抛，**不退回明文**；
* 登录发生在 TLS **之后**（``_connect`` 里的顺序就是这件事的执行器）。

## 它**绝不**做的事

不探测地址：没有 ``VRFY``、没有 ``EXPN``、没有「先试着连一下看这个地址存不存在」。
``ports/outreach.py`` 明说这个端口上没有那个动词；这里也没有那条代码路径。
"""

from __future__ import annotations

import io
import smtplib
from email.generator import BytesGenerator
from email.message import EmailMessage

from ..domain.outreach import ManifestEntry
from ..domain.util import sha256_text
from ..ports.outreach import MailTransport, SendReceipt

#: 一条 SMTP 连接的默认超时（秒）。**不是** ``None`` —— 见模块 docstring：
#: 一个永不超时的连接会占住整轮执行，而它不产生任何一条记录。
DEFAULT_TIMEOUT_SECONDS = 10.0

#: Message-ID 本地部分的长度（十六进制字符）。32 位十六进制 = 128 位，
#: 与 ``sha256`` 的前半段一样宽 —— 它只是一个标识，不是一条安全边界。
_MESSAGE_ID_LOCAL_CHARS = 32

#: 两种可以出示凭据的连接方式。**这一份是实现的真相**（``_connect`` 按它分支），
#: ``config.py`` 的 ``SMTP_SECURITY_MODES`` 是配置面上的同一份闭集 —— 它必须
#: 在**装载期**就能拒掉拼错的值，而装载期不 import 任何 adapter。
#:
#: 两份之间有一条等式判据（``tests/test_smtp_credentials.py``）：加第三种方式时，
#: 只改这里会让配置面上它永远拒绝 → 那条判据红。
SECURITY_MODES: frozenset[str] = frozenset({"starttls", "ssl"})


def stable_message_id(entry: ManifestEntry, *, from_address: str) -> str:
    """这封信的稳定标识。**派生**，不落盘、不随机。

    派生自 ``(正文摘要, 收件人)`` 两样，理由逐条：

    * 正文摘要而不是 ``draft_id``：一封被改过的正文必须得到一个**不同**的标识，
      否则「授权之后有人改了正文」这件事在对方那一侧完全没有痕迹 —— 而对方
      唯一的线索就是 Message-ID。
    * 收件人：同一份正文发给两个人是两封信。少了它，两个人会收到逐字相同、
      连标识都相同的信，而任何按标识去重的中间环节都会把第二封吃掉。
    * 域取自 ``from_address``：标识的右半边在协议上应当是我们能收到回信的那一侧，
      而「我们是谁」这件事的答案在发送箱地址上，不在正文里。

    为什么是**派生**而不是随机：随机的标识让「重试时带的是同一个标识」变成一句
    没人能执行的约定 —— 第二次尝试会拿到一个新标识，而对账那一步就再也没有一个
    可以问对方的键了。
    """
    local = sha256_text(f"{entry.draft.body_sha256}\n{entry.recipient}")[:_MESSAGE_ID_LOCAL_CHARS]
    return f"<{local}@{_domain_of(from_address)}>"


def compose_message(
    *, entry: ManifestEntry, subject: str, body: str, from_address: str
) -> EmailMessage:
    """把这封信拼成一封 ``EmailMessage``。**不改正文一个字节。**

    ``To`` 取 ``entry.recipient``（清单里规范化过的那一个），``From`` 取调用方
    传进来的地址，而不是 ``entry.sendbox``：两者是否一致由**调用方**在发送之前
    判定（``ports/outreach.py`` 的 ``sender_identity`` 就是为这件事存在的）。
    在这里悄悄用 ``entry.sendbox`` 覆盖掉实际发送箱，会让「清单上写的」与
    「实际用的」不一致这件事在盘上永远看不见 —— 而那正是要看见的那件事。
    """
    message = EmailMessage()
    message["To"] = entry.recipient
    message["From"] = from_address
    message["Subject"] = subject
    message["Message-ID"] = stable_message_id(entry, from_address=from_address)
    message.set_content(body, subtype="plain", charset="utf-8")
    return message


def to_wire_bytes(message: EmailMessage) -> bytes:
    """一封 ``EmailMessage`` 在线上会是的那些字节（CRLF 行尾）。

    这是 ``smtplib.SMTP.send_message`` 内部做的那件事，抄在这里是为了让
    ``mail_capture_transport`` 能产出**逐字节相同**的东西：两个实现差的是投递
    那一段，不是「这封信长什么样」。各行一份拼接的实现会让「同一个 Message-ID」
    变成两个实现各自的巧合，而那种巧合在第三个实现出现时不会有人发现。
    """
    buffer = io.BytesIO()
    BytesGenerator(buffer).flatten(message, linesep="\r\n")
    return buffer.getvalue()


class SmtpMailTransport(MailTransport):
    """对 ``host:port`` 上的 SMTP 服务说话。

    Args:
        host: 默认 ``127.0.0.1``。默认值取回环是**故意的**：这个参数的缺省值
            决定了「一次配置漏写」会连到哪里，而漏写时连到回环是本仓唯一安全的
            方向（测试用的捕获服务就在那里）。
        port: **必须显式给**，没有默认值。一个默认的 25 会让一次漏写变成一次真的
            投递尝试 —— 而那一次尝试的后果是不可撤销的。
        from_address: 发送箱地址，同时是 :attr:`sender_identity` 的答案。
        timeout: 连接与单条命令的超时，见模块 docstring。
        username / password: 中继的凭据。**两样必须同时给**，而且必须与
            ``security`` 一起给 —— 见模块 docstring：一个读进来却不出示的口令
            不构成一道防线。
        security: ``"starttls"`` 或 ``"ssl"``。给定它就必须同时给凭据（一个
            不登录的 TLS 连接只能靠网络位置放行，而那正是这一对参数要排掉的
            那件事）。

    Raises:
        ValueError: 上面几条任意一条不成立。**构造期**抛，不是第一次发信时抛：
            一个把口令存在内存里、直到第一封信才发现自己没法出示它的传输层，
            它的失败点在报告上是「投递失败」，而真实原因是接线。
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int,
        from_address: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        username: str | None = None,
        password: str | None = None,
        security: str | None = None,
    ) -> None:
        if timeout is None:
            raise ValueError(
                "timeout 不得为 None。\n"
                "``smtplib`` 里 None 的意思是「永不超时」，而一个卡住的连接会占住"
                "整轮执行 —— 它的现象是「跑着跑着不动了」，不产生任何一条记录。"
                "这里不去把它静默换成一个默认值：那会让写下 None 的人以为他要的"
                "「不超时」生效了。"
            )
        if timeout <= 0:
            raise ValueError(f"timeout={timeout!r} 必须为正")
        if not 1 <= port <= 65535:
            raise ValueError(
                f"port={port!r} 不是一个可以连接的端口。\n"
                "客户端一侧的 0 没有意义：内核只在**绑定**时分配端口，"
                "connect 到 0 是一次永远失败的连接 —— 所以这里拒掉它，"
                "而不是让它变成一条看不出原因的 OSError。"
            )
        if "@" not in from_address:
            raise ValueError(
                f"from_address={from_address!r} 不像一个邮箱地址。\n"
                "Message-ID 的右半边取自它的域，而一个没有域的发送箱地址会让那个"
                "标识指向一个不存在的地方 —— 对方回信时唯一的线索就是它。"
            )
        if username is None and password is not None:
            raise ValueError(
                "只给了 password，没有 username。\n"
                "  两样是一对：一个没有用户名的登录在 SMTP 上发不出去，而把这里"
                "读成「用空用户名登录」会让一次配置漏写变成一次真的认证尝试。"
            )
        if username is not None and password is None:
            raise ValueError(
                f"username={username!r} 有，password 没有。\n"
                "  两样是一对（见上一条）。**刻意不做「空口令也算给了」**：那会让"
                "「口令漏配」与「这个中继真的没有口令」在行为上一样。"
            )
        if username is not None and security is None:
            raise ValueError(
                "有凭据，却没有说明凭什么方式出示（security=None）。\n"
                "  这里的缺省不会是一个安全的猜：不写明时，一个会自己去试 "
                "STARTTLS 的实现和一个直接明文登录的实现在**同一份配置**下行为不同，"
                "而两者在报告上都只写「发送成功/失败」。\n"
                "  写 starttls 或 ssl 之间的哪一个，是写下这份配置的人的决定。"
            )
        if security is not None and username is None:
            raise ValueError(
                f"security={security!r} 有，凭据没有。\n"
                "  一个不登录的 TLS 连接只能靠**网络位置**被放行，而那一层"
                "（出口）已经由别的判据管着了 —— 在这里承认它等于把两层折成一层。"
            )
        if security is not None and security not in SECURITY_MODES:
            raise ValueError(
                f"security={security!r} 不是可识别的方式。\n"
                f"  取值只有两个：{'、'.join(sorted(SECURITY_MODES))}。"
            )
        self._host = host
        self._port = port
        self._from_address = from_address
        self._timeout = timeout
        self._username = username
        self._password = password
        self._security = security

    def send(
        self,
        *,
        entry: ManifestEntry,
        subject: str,
        body: str,
        from_address: str,
    ) -> SendReceipt:
        """发一封。**只有两个名字明确的拒绝会被读成 ``refused``，其余照抛。**

        见模块 docstring 与 ``ports/outreach.py``：一次超时、一次连接中断、一次
        服务端在 ``DATA`` 上回 5xx，在这里都**抛**。它们不是同一种失败，而实现
        分不清时不该替调用方猜 —— 调用方把抛出的记成 ``DELIVERY_UNKNOWN``，
        那一边不会让人收到两封信。
        """
        message = compose_message(
            entry=entry, subject=subject, body=body, from_address=from_address
        )
        client = self._connect()
        try:
            response = client.send_message(
                message, from_addr=from_address, to_addrs=[entry.recipient]
            )
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as exc:
            # ``str(exc)`` 逐字带着服务端给的那段文本（代码 + 原文），
            # 所以这里不解析它 —— 解析会把「服务端换一种措辞」变成一次投递失败。
            return SendReceipt(outcome="refused", accepted=False, reason=str(exc))
        finally:
            _close_quietly(client)
        return SendReceipt(
            outcome="accepted",
            accepted=True,
            # 我们自己生成的那一个，不是从应答里抠出来的。理由：它是**我们**用来
            # 对账的键，而抠应答的实现会在服务端换一种措辞时得到一个空标识 ——
            # 那时这一封信在盘上就没有键了。服务端把它回显回来了，这件事由
            # raw_response 逐字留证，也由契约测试对着落盘的 .eml 核对。
            message_id=str(message["Message-ID"]),
            raw_response=_render_responses(response),
        )

    def _connect(self) -> smtplib.SMTP:
        """建一条**已经按配置协商过 TLS、已经登录**的连接。

        这个函数短，但它守的顺序是这一整个类里最要紧的一条：**登录在 TLS 之后**。
        反过来写（``AUTH`` 之后再 ``starttls``）在一台支持两样的中继上照样能跑完，
        而它在线上把那句口令留在了明文段里 —— 一次看起来完全正常的投递。

        三件事各自对应配置里的一格，没有一格是从别的格里推出来的：

        * ``security="ssl"`` → ``SMTP_SSL``（连接本身就是 TLS）；
        * ``security="starttls"`` → 明连接 + ``starttls()``。中继不宣告 STARTTLS
          时 ``smtplib`` 抛 ``SMTPNotSupportedError`` —— **这就是我们要的**：
          一次「发不出去」好过一次「发出去了，而口令在明文里」。
        * 凭据 → ``login``。构造期已经保证了它与 ``security`` 同时存在。
        """
        if self._security == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self._host, self._port, timeout=self._timeout
            )
        else:
            client = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
            if self._security == "starttls":
                client.starttls()
        if self._username is not None:
            # ``password`` 在构造期就被保证不是 None（只给一样会被拒），所以这里
            # 那一格不需要再兜一次 —— 兜底的值（``or ""``）会把一次内部矛盾
            # 变成一次空口令登录尝试。
            assert self._password is not None
            client.login(self._username, self._password)
        return client

    def status(self, message_id: str) -> SendReceipt:
        """这个后端**答不出来** —— 于是它说「我不知道」。

        不是因为懒：SMTP 上没有一条「这封信现在怎么样了」的查询命令。投递确认
        是一条**进来的**回执（``CorrespondenceEvent``），不是我们能问出来的东西。
        拿一个 ``refused`` 冒充它是最坏的一种答法（它会让调用方去重发一封可能
        已经到达的信），抛则是另一种（调用点要为一次正常的「查不到」写 ``try``，
        而契约写明了查不到返回 ``UNKNOWN``）。
        """
        return SendReceipt(
            outcome="unknown",
            accepted=False,
            reason=(
                f"这个后端不支持按 message id 查询（问的是 {message_id!r}）："
                "SMTP 没有查询投递状态的命令，投递确认只能作为一条**进来的**回执到达。\n"
                "所以这里回的是「我不知道」，不是「它没发出去」—— 后者会让调用方重发，"
                "而这封信可能已经到达。"
            ),
        )

    @property
    def sender_identity(self) -> str:
        """构造时给的那个发送箱地址。"""
        return self._from_address


def _render_responses(responses: dict[str, tuple[int, bytes]]) -> str:
    """``send_message`` 的返回值，摊成一行。**逐字留着服务端说的那句话。**

    ``send_message`` 返回的**不是**一段 bytes，而是 ``{收件人: (代码, 原文)}``
    —— 这里逐条摊开，因为一条连接上可能有多个收件人，而把它们拼成一句会让
    「哪一个人被怎么答的」丢掉。原文那一半用 ``latin-1`` 解（SMTP 的应答是
    7 位 ASCII，但服务端偶尔会回非 ASCII 字节；用 ``utf-8`` 解会在那里抛，
    而抛的地方恰好是**已经受理之后**的收尾，那会把一次成功读成一次失败）。

    **这一格是收件人级的应答，不是 ``DATA`` 的应答。** ``send_message`` 不回传
    ``DATA`` 那一步的原文，而「对方把这一封信排进了队列」那句话恰恰在 ``DATA``
    的应答里。所以对账的人**不得**把这里当成投递凭据去找一个队列号：一个在这里
    找不到 ``queued as`` 的人，如果据此判定投递没发生，就会重发一封已经出去的信。
    这一段是承重的 —— 它记的是「服务端在收件人这一层说了什么」，仅此而已。
    """
    return "; ".join(
        f"{recipient}: {code} {text.decode('latin-1', errors='replace').strip()}"
        for recipient, (code, text) in responses.items()
    )


def _domain_of(address: str) -> str:
    """取地址的域。**只做切分**，不校验、不规范化。"""
    _, separator, domain = address.rpartition("@")
    return domain if separator else address


def _close_quietly(client: smtplib.SMTP) -> None:
    """关连接，**并且让关连接的失败不改变已经拿到的结论**。

    一封信已经受理了，而退出时的 ``QUIT`` 没说完（服务端提前关了连接、或者它在
    这一步超时），不该让调用方把它记成 ``DELIVERY_UNKNOWN`` —— 那会引发一次多余
    的对账，而对账要问的这个后端恰好答不出任何东西。吞掉的只有**关闭**这一步的
    异常；``send`` 本身的异常一律照抛。
    """
    try:
        client.quit()
    except Exception:
        try:
            client.close()
        except Exception:
            pass


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "SmtpMailTransport",
    "compose_message",
    "stable_message_id",
    "to_wire_bytes",
]

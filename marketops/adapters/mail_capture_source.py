"""``MailCaptureSource`` —— 从**我们自己的捕获目录**里读回信。

## 这个来源的「远端」是磁盘上的一个目录

``adapters/mail_capture.py`` 的 :class:`~marketops.adapters.mail_capture.MailCaptureServer`
每收下一封信就写下两份事实：``<root>/<seq>--<摘要>.eml`` 与 ``<root>/index.jsonl``
里的一行。本模块把那两份事实读回成
:class:`~marketops.ports.correspondence.CorrespondenceItem`。

它是 ``CorrespondenceSource`` 的第一个实现，也是「发出去 → 对方回 → 我们读到」
这条链在本仓**唯一可以端到端跑通**的那一半：另一半是
``adapters/mail_capture_transport.py``（§6 允许的隔离验证，只走回环）。所以它
**不联网**：远端就是这个进程脚下的一份目录，``read`` 里没有任何一行会去解析一个
主机名。

## 游标推到哪里，与「去重」由谁负责 —— 两件事分开

``application/correspondence.py`` 用 ``(sendbox, provider_message_id)`` 去重
（``CorrespondenceEvent.dedupe_key``），而 :func:`~marketops.application.correspondence._persist`
**认得出来重放**：同一份原件第二次到达时它不写第二条事件、不写第二条交接，回执里
那一格叫 ``replayed``。

于是这里的取舍是：**游标只需要回答「读到哪里了」，不回答「落库了没有」**。理由是
后果的方向 —— 一份「读到了但还没落库」的影子台账住进这一层之后，它与真库漂了
没有任何执行器会响，而漂的那一天的症状是「一段时间里的来信**不再被读**」，报告上
只显示「最近没有来信」。

**同一批被重复读一次会发生什么**：库里认得出重复。第二次读出来的是同一批
``(sendbox, provider_message_id)``，``_persist`` 逐条判重放，写下去的是同样的处置；
事件只有一条，交接只有一条，抑制行不会被改。换言之，「不重复」这件事的执行器在
**存储层**，不在这里 —— 所以这里的游标可以放心前推（``cursor`` 取读到的那一批里
**最大**的 ``seq``），而调用方在一次落库失败时也不需要来这里回退什么：
``application/correspondence.py`` 会把回执里的 ``cursor`` 退回 ``from_cursor``，
重读那一批的动作由**它**决定。

## 摘要必须核对：不一致时**作废**这一批，不跳过、不抛

索引行里的 ``sha256`` 是**索引说**的，``.eml`` 的字节是**事实**。两者不一致的
时候有三种做法，三种的后果完全不同：

* **跳过这一行**（最坏）：对方的一封信被丢掉，而报告上只显示「这一批读完了」。
  静默跳过是这三条里唯一一条**没有任何痕迹**的。
* **抛**：它把「索引与原件对不上」这件事记成了一次读取失败。而这是一次
  **读得成**的读取 —— 文件都在、都能打开，只是它们的说法不一致。抛出去的代价是
  调用方只能看见一个异常，看不见「作废的原因」这个结构化的事实。
* **作废**（本实现）：返回一个 :attr:`~marketops.ports.correspondence.CursorState.INVALIDATED`
  的批次，``note`` 里写着哪一行、两个摘要各是什么。语义也对得上：这一批里
  **没有一条**能被信任（连不一致那一条**之前**的那些也不行 —— 一份索引都能与自己的
  字节对不上，说明这份目录已经不是我们上次读的那一份了），所以条目一个都不给、
  新游标也不给，处置是**重新对齐再重读**。

三条硬约束（``CorrespondenceBatch.__post_init__``）在这里是被真的满足的，不是
「小心别违反」：作废批次里 ``items`` 是空的、``cursor`` 是空串、``note`` 非空。

**同一类里还有三种**，都走同一条路（都不静默跳过）：索引指向的 ``.eml`` 不在盘上、
索引行本身读不出来（坏 JSON、缺字段、日期不是日期）、以及邮件的 MIME 结构解析不了。
它们共有的形状是**记录与事实对不上**。

## 索引文件不存在 ⇒ **空批次**，不是异常

理由逐字来自 :func:`~marketops.adapters.mail_capture.read_index` 的 docstring：
「还没有收到任何东西」是一个正常的读取结果。所以索引不在时返回的是一个
``items`` 为空、游标**不动**的批次，而不是抛 —— 一个把「还没收到信」读成异常的
来源，会让每一次部署的第一轮 tick 都以失败告终。

## ``kind`` 只在**结构性可判**时给

分类是另一个端口的活（``ports/correspondence.py.ReplyClassifier``）。这里只回答
两个收信协议**自己**就写着答案的问题：

* RFC 3464 的投递状态通知（``multipart/report; report-type=delivery-status``）里
  的 ``Status:`` 字段：``5.x.x`` → ``HARD_BOUNCE``，``4.x.x`` → ``SOFT_BOUNCE``。
  一封通知里**混着** 4.x 与 5.x（多收件人）时不给 —— 那时「哪个收件人被怎么退了」
  要靠人读，而猜错的方向是把一个临时失败记成地址不存在（于是那个人被永久抑制）。
* RFC 3834 的 ``Auto-Submitted: auto-replied`` / ``auto-generated`` →
  ``AUTO_REPLY``。只认这两个取值：``auto-notified`` 与其余任何取值**一律不给**
  （见 :data:`_AUTO_SUBMITTED_VALUES` 的注释）。

其余一律 ``None``。一个「拿不准就标 AUTO_REPLY」的实现会把一封人写的回信记成
机器回的（处置 ``RECORD_ONLY``：既不停止跟进，也不产出交接）。

## ``in_reply_to`` 原样带过来，**不在这里**翻译成 ``send_key``

``send_key`` 是 ``OutboundAttempt.send_key``，对方看不到它；而这一格是对方**回显**
回来的 ``Message-ID``（尖括号也留着）。翻译需要一次存储查询，而这一层没有存储 ——
翻译发生在 ``application/correspondence.py::_link`` 里。两格不合并的理由写在
:attr:`~marketops.ports.correspondence.CorrespondenceItem.in_reply_to` 的 docstring。

## 已知边界（写下来，因为它是一个**真的漏**）

索引是这个来源的**记录**：一个 ``.eml`` 在盘上而索引里没有对应行（写盘被打断在
「先落 .eml、再追加索引行」两步之间）时，本模块**看不见它**。这里不做「把目录里
多出来的文件也读进来」：一条不在索引里的原件连「投给了哪个信箱、属于哪一批」都没有
答案，而这一层没有任何地方可以放「一条读不出来的行」。它会留在磁盘上等人看一眼
（``mail_capture`` 的写盘顺序就是为这件事定的），而不会变成一条来路不明的事件。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.message import Message
from pathlib import Path

from ..domain.outreach import CorrespondenceKind
from ..domain.util import normalise_email, sha256_bytes, to_utc
from ..ports.correspondence import (
    CorrespondenceBatch,
    CorrespondenceItem,
    CorrespondenceSource,
    CursorState,
)
from .mail_capture import SEQ_WIDTH, ReceivedMessage, read_index

#: 报文里一个 ``Message-ID`` 的形状（含尖括号）。**刻意宽松**：这里只回答
#: 「这一段里有没有一个像标识的东西」，不回答它是不是一个合法的 addr-spec ——
#: 后者要一个真的语法分析器，而对方写坏一个标识的代价不该是丢掉这一条线索。
_MESSAGE_ID_IN_HEADER = re.compile(r"<[^<>]+>")

#: ``Auto-Submitted`` 里算「这封信是自动发的」的取值（RFC 3834）。
#:
#: 只有这两个。``auto-notified``（同一份 RFC 里的第三个取值）**刻意不给**：
#: 一份「通知」与一份「回复」在处置上不是同一件事，而这一层只判得动「来源自己
#: 声明它是机器发的回执」这两个写明了的取值。其余任何取值（包括 ``no``）都
#: 交给分类器去读正文 —— 失败关闭的方向是**少判**，不是多判。
_AUTO_SUBMITTED_VALUES = frozenset({"auto-replied", "auto-generated"})

#: DSN 的 ``report-type`` 参数（RFC 3464）。
_DSN_REPORT_TYPE = "delivery-status"


class _Void(Exception):
    """这一批**不能**被判成「读到了这些」。``note`` 是给人读的原因。

    它是一个内部信号，不是契约的一部分：它一出现，``read`` 就把它翻译成
    :attr:`~marketops.ports.correspondence.CursorState.INVALIDATED` 的批次。
    用异常在这里传递而不是逐层返回 ``None``：作废的判断点有四处（摘要不一致、
    原件不在、索引行读不出来、解析不了），四处都要带着**不同**的原因，而
    ``None`` 装不下原因。

    ``__init__`` 把 note 存成属性而不是塞进 ``args``：读到它的人要的是那句话，
    而不是一个异常类型。
    """

    def __init__(self, note: str) -> None:
        self.note = note
        super().__init__(note)


class MailCaptureSource(CorrespondenceSource):
    """从 ``MailCaptureServer`` 落下的那些字节里读一批。见模块 docstring。

    Args:
        root: 捕获目录（``index.jsonl`` 与 ``*.eml`` 都在这里）。**给绝对路径**：
            它会进每一条条目的 ``raw_ref``，而一个相对的引用会跟着**读它的人**的
            工作目录解析，于是同一份记录在两台机器上指向两份不同的原件。
        sendbox: 这个目录是**哪个信箱**（规范化地址）。**调用方给的，不是从消息里
            猜的** —— 与 ``application/correspondence.py::ingest`` 的 ``workspace``
            同一条纪律：提供方消息 id 只在 ``(sendbox, id)`` 上唯一，而
            「这是哪个信箱」是我们这边的归属，不是报文里的一句话。目录里有什么
            （我们的传输层在隔离验证里也往同一个目录投）**不改变**这个答案。

    Raises:
        ValueError: ``sendbox`` 不是规范化形式（与 ``CorrespondenceItem`` 的判据
            同一条）。在这里抛而不是在第一次读的时候抛：前者是接线时的一次失败，
            后者是**读到一半**的一次失败 —— 那时前面几条已经进了这一批。
    """

    def __init__(self, root: str | Path, *, sendbox: str) -> None:
        if not sendbox or normalise_email(sendbox) != sendbox:
            raise ValueError(
                f"sendbox={sendbox!r} 不是规范化形式（形如 {normalise_email(sendbox)!r}）。\n"
                "去重键是 (sendbox, provider_message_id)：两个写法不同的同一个信箱会让"
                "同一封信去重不掉 —— 症状是那个人收到两次回复。"
            )
        self._root = Path(root)
        self._sendbox = sendbox

    @property
    def root(self) -> Path:
        """捕获目录。**读它、不写它** —— 本模块没有一行写盘。"""
        return self._root

    @property
    def sendbox(self) -> str:
        return self._sendbox

    def _position(self, cursor: str) -> int:
        """游标 → 起点 ``seq``。空串是 ``0``（从头读）；其余必须是一个十进制整数。"""
        text = cursor.strip()
        if not text:
            return 0
        try:
            value = int(text)
        except ValueError:
            raise ValueError(
                f"cursor={cursor!r} 不是上一次那一批最后一条的 seq。\n"
                "读成一个宽容的默认值（比如从头读）会让「游标被写坏了」与「这一段"
                "没有来信」在回执上长得一样 —— 而前者会让同一批信被反复重读。"
            ) from None
        if value < 0:
            raise ValueError(
                f"cursor={cursor!r} 是负数：seq 从 1 起（0 表示「还没有读过任何一封」）。"
            )
        return value

    def _void(self, cursor: str, note: str) -> CorrespondenceBatch:
        """一个**作废**的批次。三条硬约束见模块 docstring。

        ``from_cursor`` 仍然带着「这一批本来是从哪里读的」：它回答的是
        「作废之前读到哪了」，而**不是**一个可以拿去重试的游标 —— 它正是那个
        被作废的东西。处置是重新对齐（从 ``cursor=""`` 重读），不是重试它。
        """
        return CorrespondenceBatch(
            items=(),
            cursor="",
            cursor_state=CursorState.INVALIDATED,
            from_cursor=cursor,
            note=note,
        )

    def read(self, *, cursor: str = "") -> CorrespondenceBatch:
        """从 ``cursor`` 开始读一批，读到的每一条都带着原件的那两样。

        Args:
            cursor: 上一次那一批最后一条的 ``seq``（字符串）。空串 = 从头读。
                比索引里的最大 ``seq`` 还大的游标是合法的，结果是**空批次**：
                那是「这一段没有来信」，不是错误，也不是游标作废。

        Returns:
            这一批的结论。三种落法：``ADVANCED`` 带条目（或空批次，游标不动）、
            ``INVALIDATED``（记录与事实对不上，见模块 docstring）。

        Raises:
            ValueError: ``cursor`` 的形状用不了。它与「这一批读不成」是两件事：
                前者重试多少次都一样，所以它抛；后者见下。
            Exception: 索引文件存在但**打不开**（权限、I/O）。**这与空批次是不同
                的事**（``ports/correspondence.py::read``）：一个把读不成当成
                「没有来信」的实现在报告上完全正常，而它的病是**漏收**。
        """
        start = self._position(cursor)
        try:
            messages = read_index(self._root)
        except FileNotFoundError as error:
            # 索引里有指向不存在原件的行（``read_index`` 读的是**行里那个路径**）。
            return self._void(
                cursor,
                "索引里有一行指向一封不在盘上的原件 —— 这份索引与它的字节已经不是"
                f"同一份记录了（{error}）。",
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            # 坏 JSON、缺字段、不是日期的日期。**不跳过那一行**：跳过之后我们连
            # 「它本来是什么」都不知道，而游标还要从它上面跨过去。
            return self._void(
                cursor,
                f"索引文件读不了（{type(error).__name__}: {error}）—— 一份读不出内容的"
                "索引不能用来定「读到哪里了」。",
            )

        items: list[CorrespondenceItem] = []
        last_seq = start
        for message in messages:
            if message.seq <= start:
                continue
            try:
                items.append(self._item(message))
            except _Void as void:
                return self._void(cursor, void.note)
            # 取**读到的那一批里最大的** seq，不是最后一行的：索引的顺序由
            # ``mail_capture`` 的锁保证，而这里不依赖它 —— 一个取「最后一行」的实现
            # 在索引被手工重排之后会把游标往回推，于是同一批被反复重读，报告上只显示
            # 「读到了很多信」。
            last_seq = max(last_seq, message.seq)

        return CorrespondenceBatch(
            items=tuple(items),
            # 读到东西才推游标。``items`` 为空时交给 ``cursor`` 原值 —— 「游标不动」
            # 与「推到 0」在下一次读取时指向同一段，但只有前者在报告上说得清
            # 「这一段没有来信」。
            cursor=str(last_seq) if items else cursor,
            cursor_state=CursorState.ADVANCED,
            from_cursor=cursor,
        )

    def _item(self, message: ReceivedMessage) -> CorrespondenceItem:
        """一条索引行 → 一条端口条目。**摘要在这里核**（见模块 docstring）。"""
        raw = message.body_bytes
        actual = sha256_bytes(raw)
        if actual != message.sha256:
            raise _Void(
                f"索引第 {message.seq} 行说这一封的摘要是 {message.sha256[:12]}…，"
                f"而 {message.path.name} 的字节算出来是 {actual[:12]}…。"
                "两份说法不一致时这一批不算数：跳过它等于把对方的一封信丢掉，"
                "而报告上只显示「这一批读完了」。"
            )
        parsed = message_from_bytes(raw, policy=policy.default)
        # 一份投递状态通知里的那两格**只在这里读一次**，然后整封信的行为都靠它们：
        # 缺了它们，`5.1.1` 与「一封没头没脑的来信」在这一层长得一样。
        bounced, bounce_of = _bounce_fields(parsed)
        return CorrespondenceItem(
            provider_message_id=_provider_message_id(message),
            sendbox=self._sendbox,
            raw_ref=_raw_ref(message.path, self._root),
            # 用**核过的那一个**，而不是索引说的那个：条目上的摘要回答的是
            # 「原件是不是还是这一份」，而它必须来自字节。
            raw_sha256=actual,
            received_at=_received_at(message.received_at),
            sender=normalise_email(message.from_address),
            subject=message.subject,
            body=_body_text(parsed),
            in_reply_to=_in_reply_to(parsed),
            bounce_recipient=bounced,
            bounce_original_message_id=bounce_of,
            kind=_structural_kind(parsed),
        )


# ----------------------------------------------------------------------
# 索引行 → 条目上的那几格
# ----------------------------------------------------------------------


def _provider_message_id(message: ReceivedMessage) -> str:
    """这一条的提供方标识。**没有 ``Message-ID`` 时退到捕获服务自己的那个凭据。**

    ``CorrespondenceItem`` 要求它非空（去重键的一半），而对方是**可以不写**
    ``Message-ID`` 的 —— 一封这样的信如果没有一个标识，它要么被丢掉（不可接受），
    要么每次重读都变成一条新事件（同一个人被回复两次）。

    ``seq-NNNNNN`` 不是我编的：``mail_capture`` 的 ``DATA`` 应答对同一件事用的就是
    这个写法（``received.message_id or f"seq-{seq:06d}"``），而 ``seq`` 是捕获服务
    在收下那一刻分配的、单调的、也写在文件名里的。所以它是**这个捕获目录**对
    「哪一封信」的答案 —— 它不等于对方的标识，而一个没有标识的人本来就只有这一个
    能区分两次投递的东西。

    复用 ``SEQ_WIDTH`` 而不是重写宽度：两个写法哪天分岔了，症状是「应答里说的那个
    凭据在事件里查不到」。
    """
    identifier = message.message_id.strip()
    if identifier:
        return identifier
    return f"seq-{message.seq:0{SEQ_WIDTH}d}"


def _raw_ref(path: Path, root: Path) -> str:
    """原件的引用。**绝对路径**，理由见 :class:`MailCaptureSource` 的 Args。

    索引行里的路径是捕获服务按 ``root`` 拼出来的，所以调用方给绝对 root 时它已经
    是绝对的。给相对 root 时它是相对的 —— 那就按**同一个 root** 拼回绝对，而不是
    留在那里：一个相对的 ``raw_ref`` 会跟着读它的人的工作目录解析，而「同一份记录
    在两台机器上指向两份不同的原件」这种坏法不会让任何一条判据变红。
    """
    if path.is_absolute():
        return str(path)
    return str(root / path)


def _received_at(value: datetime) -> datetime:
    """事件**发生**的瞬间。**naive 的按 UTC 解释，不按本地时区。**

    索引行里是一个 ISO 串，它可能带偏移量（捕获服务写的是 ``utc_now()``，一定带），
    也可能不带（有人手工写了索引、或者别的写入方）。不带时**必须**挑一个解释，而
    挑「本地时区」的那一个会让同一个库在两台机器上读出两个不同的瞬间 ——
    本机与部署机的时区不同，于是「这封信是什么时候到的」变成一个与**读它的人**
    有关的答案。

    UTC 是唯一一个与读的人无关的解释，也与 ``domain/util.to_utc`` 之后所有时刻的
    归宿一致（库里存的是 UTC）。它有一个已知的代价：一份真的按本地时间写的 naive
    串会被读早（北京时间的 ``09:00`` 变成 UTC 的 ``09:00``）。那个偏差**不会**
    被这里掩盖成一次失败 —— 它是「两个时刻差 8 小时」这一件看得见的事。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return to_utc(value)


# ----------------------------------------------------------------------
# 正文、引用、结构性分类
# ----------------------------------------------------------------------


def _body_text(message: Message) -> str:
    """正文：**text/plain 优先，没有则退回 HTML 的原文**。

    退回 HTML 的原文而**不**把它转成文本：加工过的正文与原件对不上，而这一格
    最终的归宿是销售交接里那句「逐字保留」（``SalesHandoff.reply_body``）——
    一个被转过的正文会让读它的人以为对方就是这么写的。

    正文**不被解释**（§7 逐字：回信正文是数据，永远不是指令）。这个函数只做
    「哪一段是正文」与「按它自己声明的编码解出来」两件事。
    """
    plain, html = _first_text_parts(message)
    if plain is not None:
        return plain
    if html is not None:
        return html
    return ""


def _first_text_parts(message: Message) -> tuple[str | None, str | None]:
    """整棵 MIME 树里**第一个** text/plain 与第一个 text/html 的内容。

    ``message/delivery-status`` 那一段整个跳过：它的子块在默认 policy 下也长得像
    ``text/plain``，而它们不是正文 —— 它们是机器字段（``Status:`` / ``Action:``），
    把它们当成正文会让一封退信的正文变成两行状态码。

    只取每一个类型的**第一个**：``multipart/alternative`` 的两个分支表达的是同一件
    事，而「第二个 plain」在别处意味着另一个人说的话（转发的原文）。
    """

    def walk(part: Message) -> tuple[str | None, str | None]:
        content_type = part.get_content_type()
        if content_type == "message/delivery-status":
            return None, None
        if part.is_multipart():
            found: tuple[str | None, str | None] = (None, None)
            payload = part.get_payload()
            for child in payload if isinstance(payload, list) else ():
                plain, html = walk(child)
                found = (found[0] if found[0] is not None else plain,
                         found[1] if found[1] is not None else html)
            return found
        if content_type == "text/plain":
            return _part_text(part), None
        if content_type == "text/html":
            return None, _part_text(part)
        return None, None

    return walk(message)


def _part_text(part: Message) -> str:
    """一个叶子部分的内容，按它自己声明的字符集解。

    ``errors="replace"`` 而不是抛：一段解不出来的字节不该让**整封信**读不成 ——
    原件在盘上（``raw_ref`` / ``raw_sha256`` 指着它），而缺几个字比丢掉一封信便宜。

    字符集名不认识时（``charset=nonsense``）退回 UTF-8：``email`` 的
    ``get_content()`` 在这一格上会抛 ``LookupError`` —— 而抛在一封**已经收下**的
    信上，是本模块最不该有的失败形状。
    """
    payload = part.get_payload(decode=True)
    if payload is None:
        # 极少数畸形报文（payload 既不是字节也不是列表）。退回原文的 str 形态，
        # 而不是空串：空串读起来像「这封信没有正文」，而它是有正文的。
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _in_reply_to(message: Message) -> str:
    """对方那封信**引用**的那一封：``In-Reply-To``，没有就看 ``References`` 的最后一个。

    值**原样**带着尖括号取出来（它是一个 ``Message-ID``，不是它的内容），也**不**在
    这里把它与 ``send_key`` 合并 —— 见模块 docstring。

    取最后一个的理由在 ``References`` 上是 RFC 5322 的语义（最后一个是直接父件）；
    ``In-Reply-To`` 上沿用同一条，是因为发信客户端偶尔会把整条 ``References`` 抄进
    ``In-Reply-To``，而那时最后一个仍然是直接父件。

    取不到时返回空串（不是 ``None``）：``CorrespondenceItem.in_reply_to`` 的空串
    有一个确切的意思 —— 「对方没引用任何一封」，而那是 ``_link`` 要区别对待的一件事。
    """
    for name in ("In-Reply-To", "References"):
        found = _MESSAGE_ID_IN_HEADER.findall(_header(message, name))
        if found:
            return found[-1]
    return ""


def _structural_kind(message: Message) -> CorrespondenceKind | None:
    """这条事件**自己声明的**分类，或者 ``None``（要人去读正文）。

    次序是承重的：DSN 先判。一封 DSN 也可能带 ``Auto-Submitted``，而「这是一份投递
    状态通知」比「这是机器发的」强 —— 前者说的是**哪个地址怎么了**（处置会落到抑制
    名单上），后者只说这封信不是人写的。
    """
    statuses = _delivery_statuses(message)
    if statuses is not None:
        return _bounce_kind(statuses)
    if _is_auto_submitted(message):
        return CorrespondenceKind.AUTO_REPLY
    return None


def _status_blocks(message: Message) -> tuple[Message, ...] | None:
    """一份 DSN 里的全部投递状态块；**不是** DSN 时返回 ``None``。

    ``None`` 与「空元组」在这里是两个不同的答案：前者是「这不是一份投递状态通知」
    （于是要接着看别的结构性信号），后者是「它声称是一份，但里面一块都没有」
    （于是判不出来，交给人）。

    一份 ``delivery-status`` 里有两块：整封报文的（``Reporting-MTA`` 那一块），与
    **每个收件人**各一块的。``Status:`` / ``Final-Recipient:`` / ``Original-Message-ID:``
    都长在后面这些块上 —— 而**一块都不漏**是刻意的：有的实现把 ``Status:`` 写在
    前一块里，漏读一块的后果是把一封硬退信读成「判不出来」。
    """
    if message.get_content_type() != "multipart/report":
        return None
    report_type = (message.get_param("report-type") or "").strip().lower()
    if report_type != _DSN_REPORT_TYPE:
        return None
    blocks: list[Message] = []
    for part in message.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        payload = part.get_payload()
        for block in payload if isinstance(payload, list) else ():
            blocks.append(block)
    return tuple(blocks)


def _delivery_statuses(message: Message) -> tuple[str, ...] | None:
    """这些块上的全部 ``Status:`` 值；**不是** DSN 时返回 ``None``。"""
    blocks = _status_blocks(message)
    if blocks is None:
        return None
    statuses: list[str] = []
    for block in blocks:
        value = block.get("Status")
        if value:
            statuses.append(str(value).strip())
    return tuple(statuses)


def _bounce_fields(message: Message) -> tuple[str, str]:
    """一份 DSN 说的**是哪一个人**、**是哪一封**；判不出来时给空串。

    ## 为什么这两格必须从 DSN 里读出来

    它们是这条事件**唯一**两处能证明「我们发过的那一封信投递失败了」的东西。
    没有它们，一封 ``5.1.1`` 的通知在库里就只是「一封发件人是 ``MAILER-DAEMON@…``、
    没带 ``In-Reply-To``、也没带 ``send_key`` 的来信」—— 关联的两条路一条都走不通，
    于是它落进人工审核，而 §7 要的抑制与停跟进**一条都不产生**：那个不存在的地址
    在下一批里照常被写进计划。

    ## 多收件人时不给（与 ``_bounce_kind`` 拒绝 4.x/5.x 混着同一条理由）

    一封通知里有两个 ``Final-Recipient`` 时，「我们发过的**那一个**地址」要靠人读
    原文 —— 挑一个的代价是把抑制写在另一个人身上。``Original-Message-ID`` 独立地
    取「恰好一个」：一份两收件人的报告里它可能只有一个值（那封信确实是同时发给
    两个人的），这时它仍然是真的，所以照样交出去。

    ## 形态

    ``Final-Recipient`` 是 ``<address-type>; <address>``（RFC 3464 §2.3.1），所以
    **先按 ``;`` 切**再规范化 —— 顺序反过来，``normalise_email`` 会把
    ``rfc822; buyer@…`` 的 ``rfc822; `` 当成本地部分的一部分（它按 ``@`` 切），
    得到一个看着像地址、其实谁都不是的字符串。
    """
    blocks = _status_blocks(message)
    if not blocks:
        return "", ""
    recipients = {
        _recipient_of(block.get("Final-Recipient")) for block in blocks
    } - {""}
    originals = {_message_id_of(block.get("Original-Message-ID")) for block in blocks} - {""}
    return (
        recipients.pop() if len(recipients) == 1 else "",
        originals.pop() if len(originals) == 1 else "",
    )


def _recipient_of(value: object) -> str:
    """``Final-Recipient`` 的值 → 一个规范化地址；读不出来时给空串。

    空串进不了 :func:`_bounce_fields` 的集合（那里减掉了它），因为「读不出来」与
    「读出来是空」在那一层必须是同一个答案：都表示这一格没有可用的事实。
    """
    if not value:
        return ""
    address = str(value).split(";", 1)[-1].strip()
    return normalise_email(address)


def _message_id_of(value: object) -> str:
    """``Original-Message-ID`` 的值 → 那个 ``Message-ID``（尖括号原样留着）。

    与 :func:`_in_reply_to` 取同一个形状，理由也一样：它是一个 ``Message-ID``，
    不是一个字符串字面量 —— 用 ``_MESSAGE_ID_IN_HEADER`` 取出来的东西才能与
    ``OutboundAttempt.provider_message_id`` 逐字相等。
    """
    if not value:
        return ""
    found = _MESSAGE_ID_IN_HEADER.findall(str(value))
    return found[-1] if found else ""


def _bounce_kind(statuses: tuple[str, ...]) -> CorrespondenceKind | None:
    """``Status:`` 值 → 硬退 / 软退；判不出来时 ``None``。

    ``5.x.x`` 是永久失败（地址不存在），``4.x.x`` 是临时失败（邮箱满、服务器暂时
    不可用）。两者的处置**相反**（硬退进持久抑制、软退不抑制），所以混着出现时
    一律不给：一封多收件人的通知里有一个 5.x 与一个 4.x 时，「我们发过的**那一个**
    地址」是哪一个要靠人读原文，而猜错的方向是把一个满了的邮箱记成一个人离开了。
    """
    hard = [status for status in statuses if status.startswith("5")]
    soft = [status for status in statuses if status.startswith("4")]
    if hard and not soft:
        return CorrespondenceKind.HARD_BOUNCE
    if soft and not hard:
        return CorrespondenceKind.SOFT_BOUNCE
    return None


def _is_auto_submitted(message: Message) -> bool:
    """``Auto-Submitted`` 是不是那两个「自动发的」取值之一（RFC 3834）。

    取值后面可能有注释（``auto-replied (was: ...)``）或参数，所以只比**第一个
    词**。``auto-notified`` 与其余任何取值都给 ``False``：多判一类的代价是一封人写
    的回信被记成机器回的（处置 ``RECORD_ONLY``，既不停止跟进也不产出交接），而
    那一格恰好是 §7 里最贵的两格里的一格。
    """
    value = _header(message, "Auto-Submitted").strip().casefold()
    token = re.split(r"[\s;]", value, maxsplit=1)[0] if value else ""
    return token in _AUTO_SUBMITTED_VALUES


def _header(message: Message, name: str) -> str:
    """读一个头字段，取不到返回空串。

    与 ``mail_capture._header_value`` 同一条：**缺一个头字段不是把整封信丢掉的
    理由**。区别是这里读的是整棵解析过的树（那一边只读头部的字节，因为它是**收**
    信的那一侧，报文还没被解释过）。
    """
    value = message.get(name)
    return "" if value is None else str(value).strip()


__all__ = ["MailCaptureSource"]

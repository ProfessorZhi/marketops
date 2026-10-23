"""``MailCaptureServer`` —— 一个**只服务回环**的最小 SMTP 捕获服务。

## 为什么要一段真服务，而不是一个 mock

``MailTransport`` 的两个实现（``smtp_mail.py`` / ``mail_capture_transport.py``）
必须被同一组判据问同样的问题。对 ``smtp_mail`` 来说，「问」的唯一方式是把字节真的
送出去，所以对端必须存在。本仓不允许把真实邮箱接进测试（机器策略与 §6 都禁止），
于是对端只能是我们自己在 ``127.0.0.1`` 上起的这一个。

一个 mock 掉的传输层可以给出全绿的结果，而它证明的是**调用顺序**，不是「一封信
真的能被收下」—— 后者要有一个真的按报文规矩说话、真的把字节写到盘上的对端才被
证明。这也是本类不写成 fake 的理由：fake 的对端没有协议，于是「我们发出去的字节
在协议上成不成立」这个问题在测试里没有位置。

## 它**不做**什么，逐条都是刻意的

* **不实现 ``VRFY`` / ``EXPN``**，并且明着回 ``502``。§6 逐字禁止 SMTP 地址探测。
  回 ``502``（命令本身不被支持）而不是 ``500``（命令拼错了）是有意的：``500``
  会让读的人以为是自己写错了命令，而 ``502`` 说的是「这里没有这个功能」——
  日后有人想加它时，会先读到这句话。
* 不中继、不投递、不连任何外部主机。收到的字节只落在调用方给的那个目录里。
* 不对收件人做任何**探测**：只按调用方给的**拒绝名单**比对。名单是配置，不是一个
  「这个地址存不存在」的判断 —— 本服务做不出后一个判断，也不该有那个能力。

## 绑定地址没有默认值

``host`` 与 ``port`` 都**必须显式传入**。没有默认值，也没有 ``0.0.0.0`` 那种
「先跑起来再说」的缺省 —— ``0.0.0.0`` / ``::`` / ``*`` 在这里被**直接拒绝**，
而不是写在文档里请人别用。理由是一条已经记过的教训（``本机服务只绑回环那条教训``）：
一个默认绑全部网卡的服务在共享主机上就是别人也能投信的口子，而它的症状是
「测试全绿」外加「这台机器上有一个没人知道的服务在听」。

``port=0`` 是**推荐**的用法（让内核分配），因为并行的测试与并行的 Agent 不会因此
撞车。真实端口在 :meth:`MailCaptureServer.start` 之后读 :attr:`port` —— ``0``
不是一个可以拿去连接的端口，所以调用点必须在 ``start()`` 之后读它。

## 两个落盘物的分工，与 ``page_mirror`` 同一条

``.eml`` 的**文件名的职责是定位**（``<收到顺序 6 位>--<message-id 的 hash16>.eml``），
**字节的职责是校验**（索引行里的 ``sha256``）。这不是风格：按内容命名会让「同一封
信被重放两次」落回同一个文件名，于是第二封静默覆盖第一封，而索引里仍然有两行 ——
两行指向同一份字节，且没有任何一处看得出中间有一份被盖掉了。这条分工与
``adapters/page_mirror.py`` 里那条逐字同一条。

写盘顺序也是判据的一部分：**先落 ``.eml``，再追加索引行**。反过来的话，索引里会
出现一条指向不存在文件的记录，而那条记录是唯一能证明它出过事的东西。
"""

from __future__ import annotations

import json
import socket
import socketserver
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes
from email.header import decode_header, make_header
from pathlib import Path

from ..domain.util import normalise_email, sha256_bytes, sha256_text, utc_now

#: 索引文件名。``jsonl`` 而不是 ``json``：一封信一行，追加写，收到一半被打断时
#: 前面那些行仍然可读。
INDEX_FILENAME = "index.jsonl"

#: 收到顺序在文件名里的宽度。定长是为了让目录列表按名字排就是按收到顺序排。
SEQ_WIDTH = 6

#: 一条命令行的长度上限。超了按 ``500`` 回 —— 一个不设上限的 ``readline``
#: 会被一条没有换行的命令撑满内存，而症状是整台测试机变慢。
MAX_COMMAND_BYTES = 4096

#: 读 ``DATA`` 时一次 ``readline`` 的上限。**不是**一行的上限：超长的行会被切成
#: 几块，拼回来是逐字节相同的（点去重只对**行首**生效，见 :meth:`_read_data`）。
MAX_DATA_CHUNK_BYTES = 64 * 1024

#: 一封信的大小上限。一个从网络上收字节的服务不设上限就等着被一封大信撑爆。
DEFAULT_MAX_MESSAGE_BYTES = 5 * 1024 * 1024

#: 一条连接的闲置超时。没有它，一个连上就不说话的客户端会占住一个线程直到
#: 进程结束 —— 对测试是「跑不完」，对运维是「句柄泄漏」。
DEFAULT_CONNECTION_TIMEOUT_SECONDS = 10.0

#: 服务在问候语与 ``EHLO`` 应答里报出的名字。用 ``.invalid``（RFC 2606 保留）：
#: 这个名字不指向任何真实主机，所以把它抄进别处的人不会抄到一个能解析的名字。
DEFAULT_SERVER_NAME = "marketops-mail-capture.invalid"

#: 会被拒绝的绑定地址。``0.0.0.0`` 与 ``::`` 是「所有网卡」，``*`` 是它们的写法。
_WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::", "::0", "*"})


class MailCaptureUnavailable(RuntimeError):
    """这台捕获服务没有在听，因此这次投递**没有发生**。

    它**不**翻译成任何一个 ``SendReceipt.outcome``，因为「没有发生」与「不知道有没有
    发生」的处置不同：前者可以直接重试，后者要先对账。传输层抛出来，调用方按它
    自己的纪律记（``application/outreach.py`` 记成 ``DELIVERY_UNKNOWN``，那是安全的
    那一边）。
    """


class RecipientRefused(Exception):
    """捕获服务按**拒绝名单**拒了一个收件人。SMTP 那一侧的对应物是 ``550``。

    单独一个异常类型（而不是返回一个布尔）是为了让「明确被拒」在传输层里有一个
    **可捕获的名字**：``refused`` 与会抛出的那些失败处置相反（一个可以重试，
    一个不可以），靠读返回值区分它们的实现迟早会把两者混成一种。
    """

    def __init__(self, recipients: Sequence[str]) -> None:
        self.recipients: tuple[str, ...] = tuple(recipients)
        super().__init__(
            "收件人被这台捕获服务的拒绝名单挡住"
            f"（名单是配置，不是一次地址存在性判断）：{', '.join(self.recipients)}"
        )


@dataclass(frozen=True)
class ReceivedMessage:
    """捕获服务收下的一封信。

    ``body_bytes`` 是**收下来的那些字节本身**（报文原文，逐字节），不是它的长度：
    长度在索引里叫 ``bytes``，是一个整数。两者分开是有意的 —— 一个字段同时表示
    「有多长」与「是什么」的那种写法，会让「索引说 12 字节」与「文件里真的是那
    12 字节」变成同一件事，而它们不是。
    """

    seq: int
    message_id: str
    from_address: str
    to_addresses: tuple[str, ...]
    subject: str
    body_bytes: bytes
    sha256: str
    path: Path
    received_at: datetime


class MailCaptureServer:
    """一个可以被测试直接起停的 SMTP 捕获服务。

    它**不是** mock：它说 SMTP，它把字节写到盘上，它有一个真的监听套接字。它
    唯一不做的事是把那些字节交给这个进程之外的任何人。

    Args:
        root: 落盘目录。``.eml`` 与 ``index.jsonl`` 都写在这里。调用方给绝对路径。
        host: 绑定的地址。**必须显式传入**，且不接受通配地址（见模块 docstring）。
        port: 绑定的端口。**必须显式传入**；``0`` = 让内核分配，推荐测试用这个。
        reject_recipients: 拒绝名单。比对前过 ``normalise_email``，所以
            ``Blocked@Example.invalid`` 与 ``blocked@example.invalid`` 是同一个人。
        server_name: 问候语里报出的名字。
        connection_timeout_seconds: 一条连接的闲置超时。
        max_message_bytes: 一封信的大小上限，超了回 ``552``。
    """

    def __init__(
        self,
        root: str | Path,
        *,
        host: str,
        port: int,
        reject_recipients: Iterable[str] = (),
        server_name: str = DEFAULT_SERVER_NAME,
        connection_timeout_seconds: float = DEFAULT_CONNECTION_TIMEOUT_SECONDS,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        if host in _WILDCARD_HOSTS:
            raise ValueError(
                f"host={host!r} 是通配地址，本服务不接受。\n"
                "一个绑在全部网卡上的捕获服务，在共享主机上就是别人也能投信的口子，"
                "而它的症状是「测试全绿」加上「这台机器上有一个没人知道的服务在听」。"
                "测试里请用 127.0.0.1。"
            )
        if not 0 <= port <= 65535:
            raise ValueError(f"port={port!r} 不是一个端口号（0 表示让内核分配）")
        self._root = Path(root)
        self._host = host
        self._port = port
        self._name = server_name
        self._timeout = connection_timeout_seconds
        self._max_message_bytes = max_message_bytes
        self._rejected = frozenset(normalise_email(item) for item in reject_recipients)
        #: 序号与索引行共用一把锁：索引行的顺序因此**就是**序号的顺序，而
        #: 「收到顺序」这件事在两个并发连接下才有意义。
        self._lock = threading.Lock()
        self._seq = 0
        self._listener: _CaptureRequestServer | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        #: 最近一次真的绑上的端口。``None`` = 从没起过。见 :attr:`port`。
        self._bound_port: int | None = None

    # --- 起停 ---------------------------------------------------------------

    def start(self) -> "MailCaptureServer":
        """起服务。**幂等**：已经在听时直接返回自身。"""
        if self._listener is not None:
            return self
        self._root.mkdir(parents=True, exist_ok=True)
        listener = _CaptureRequestServer((self._host, self._port), _SMTPHandler, capture=self)
        self._listener = listener
        self._bound_port = int(listener.server_address[1])
        self._running = True
        thread = threading.Thread(
            target=listener.serve_forever,
            # 轮询周期调小到 50ms：``shutdown()`` 要等 ``serve_forever`` 的循环下一轮
            # 才发现停止标志，用默认的 0.5s 会让每一次 ``stop()`` 都慢半秒 ——
            # 而测试会反复起停。
            kwargs={"poll_interval": 0.05},
            name=f"mail-capture-{self.port}",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return self

    def stop(self) -> None:
        """停服务并**立刻**释放端口。**幂等**：再调一次什么也不做。"""
        self._running = False
        listener, self._listener = self._listener, None
        thread, self._thread = self._thread, None
        if listener is not None:
            listener.shutdown()
            # server_close() 关掉监听套接字 —— 端口是在这一刻释放的。测试会反复
            # 起停，所以「关掉」必须真的意味着可以立刻重新绑定，而不是等一个
            # 不确定的回收周期。
            listener.server_close()
        if thread is not None:
            thread.join(timeout=5.0)

    def __enter__(self) -> "MailCaptureServer":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    # --- 事实 ---------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        """真实端口：**它真的绑上的那一个**，起过之后就一直报它。

        ``start()`` 之前返回**请求的**那个（可能是 ``0``）；起过之后返回内核分配的
        那个，而且 ``stop()`` 之后**仍然**报它。

        为什么停掉之后不回落到请求值（``0``）：因为 ``0`` 是一个**会静默成功**的
        值。一条判据「端口在 ``stop()`` 之后被释放了」最自然的写法是
        ``MailCaptureServer(root, host=..., port=server.port)`` —— 若 ``port`` 在
        停掉之后回落到 ``0``，这一句会成功，而它成功的原因是内核随便分了另一个
        端口，与「释放了没有」毫无关系。于是这条判据在一个**没有**释放端口的实现
        上也是绿的，而它绿得看不出来。报出真实端口之后，同一句话问的就是真的
        那个端口 —— 没释放就绑不上，判据这才成立。

        ``0`` 仍然不是一个可以拿去连接的端口：这个属性说的是「它绑过哪里」，
        不是「现在有人在听」。后者由 :attr:`running` 回答。
        """
        if self._bound_port is not None:
            return self._bound_port
        return self._port

    @property
    def server_name(self) -> str:
        return self._name

    @property
    def connection_timeout_seconds(self) -> float:
        return self._timeout

    @property
    def max_message_bytes(self) -> int:
        return self._max_message_bytes

    @property
    def running(self) -> bool:
        return self._running

    @property
    def rejected(self) -> tuple[str, ...]:
        """拒绝名单，规范化之后，升序。用于断言与排错。"""
        return tuple(sorted(self._rejected))

    @property
    def received_count(self) -> int:
        return self._seq

    def is_rejected(self, address: str) -> bool:
        """这个收件人在拒绝名单里吗。**只比名单，不查、不验证、不试探。**"""
        return normalise_email(address) in self._rejected

    # --- 收下一封信 ---------------------------------------------------------

    def ingest(
        self, raw: bytes, *, mail_from: str, recipients: Sequence[str]
    ) -> ReceivedMessage:
        """把一封信收下并落盘。**SMTP 处理函数与同进程传输都走这一个入口。**

        叫 ``ingest`` 而不叫 ``deliver``：本服务**不投递**，它只是收下并落盘。一个
        叫 ``deliver`` 的方法会让读的人以为投递发生了 —— 而 ``ports/outreach.py``
        花了整段 docstring 区分「对面接收了这次连接」（``ACCEPTED``）与「对面那个
        邮箱收下了」（``DELIVERED``），在这里用一个含糊的动词把两者抹平，是这层
        最不该犯的错。

        两个前门共用一个入口不是省事：它让「走 SMTP 收到的字节」与「同进程送进来的
        字节」在盘上**逐字节是同一件事** —— 两个实现因此可以用同一条判据比。

        抛出：
            :class:`MailCaptureUnavailable` —— 服务没在听，这次投递没有发生。
            :class:`RecipientRefused` —— 收件人在拒绝名单里。
        """
        if not self._running:
            raise MailCaptureUnavailable(
                f"这台捕获服务没有在听（root={self._root}）：这次投递**没有发生**。\n"
                "它与「不知道有没有发生」不是同一件事：后者要按对账处置，前者可以"
                "直接重试 —— 所以这里抛出来，让调用方自己决定记成哪一个，"
                "而不是替他返回一个看起来像结论的东西。"
            )
        refused = tuple(item for item in recipients if self.is_rejected(item))
        if refused:
            raise RecipientRefused(refused)

        received_at = utc_now()
        message_id = _header_value(raw, "Message-ID")
        subject = _header_value(raw, "Subject")
        digest = sha256_bytes(raw)
        with self._lock:
            self._seq += 1
            seq = self._seq
            path = self._root / _file_name(seq, message_id)
            # 先落 .eml、再追加索引行 —— 顺序见模块 docstring。
            path.write_bytes(raw)
            row = {
                "seq": seq,
                "message_id": message_id,
                "from": mail_from,
                "to": list(recipients),
                "subject": subject,
                "bytes": len(raw),
                "sha256": digest,
                "path": str(path),
                "received_at": received_at.isoformat(),
            }
            with (self._root / INDEX_FILENAME).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        return ReceivedMessage(
            seq=seq,
            message_id=message_id,
            from_address=mail_from,
            to_addresses=tuple(recipients),
            subject=subject,
            body_bytes=raw,
            sha256=digest,
            path=path,
            received_at=received_at,
        )


def read_index(root: str | Path) -> tuple[ReceivedMessage, ...]:
    """把 ``<root>/index.jsonl`` 读回来，按**收到顺序**。

    索引文件不存在时返回空元组 —— 「还没有收到任何东西」是一个正常的读取结果，
    不是一次错误。让调用点为此写 ``try`` 的实现，会把「没有」与「读不到」合并成
    同一条分支。

    ``body_bytes`` 是**从文件读回来的**，不是从索引行里读的：索引里的 ``bytes``
    是一个计数。把那个整数读成正文，会让下面这条判据失去意义 ——
    「索引里的 ``sha256`` 与文件字节一致」只有在正文真的来自文件时才能被问出来。
    """
    directory = Path(root)
    index = directory / INDEX_FILENAME
    if not index.is_file():
        return ()
    messages: list[ReceivedMessage] = []
    for line in index.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        path = Path(row["path"])
        messages.append(
            ReceivedMessage(
                seq=int(row["seq"]),
                message_id=row["message_id"],
                from_address=row["from"],
                to_addresses=tuple(row["to"]),
                subject=row["subject"],
                body_bytes=path.read_bytes(),
                sha256=row["sha256"],
                path=path,
                received_at=datetime.fromisoformat(row["received_at"]),
            )
        )
    return tuple(messages)


def _file_name(seq: int, message_id: str) -> str:
    """``<收到顺序 6 位>--<message-id 的 hash16>.eml``。

    名字里的摘要取的是 **Message-ID**，不是正文的字节 —— 见模块 docstring：
    名字的职责是**定位**（这是哪一封信），字节的职责是**校验**（``sha256``）。

    ``message_id`` 为空时摘要退化成空串的 sha256，而文件名仍然唯一，因为序号在
    前面。顺序取的是**先序号、后摘要**：把摘要放前面会让按名字排序变成按 message id
    排序，而那不是「收到顺序」—— 目录列表于是不再对应索引行的顺序。
    """
    return f"{seq:0{SEQ_WIDTH}d}--{sha256_text(message_id)[:16]}.eml"


def _header_value(raw: bytes, name: str) -> str:
    """从原始报文里读一个头字段。**只读头，不做 MIME 解析。**

    只在头部里找：一个正文里写着 ``Message-ID:`` 的段落不是一封信的标识，而按
    整份字节找字段的实现会把它读成标识 —— 于是一封正文里引用了别的信件的回信，
    会被记成那封被引用的信。

    取不到时返回空串（不是 ``None``，也不抛）：捕获服务收下的东西**可能是任何字节**
    —— 它是一段照着协议收字节的服务，不是一份校验器。缺一个头字段时把整封信丢掉，
    会让「测试里的对端为什么不收」变成一个很难查的问题。
    """
    divisor = raw.find(b"\r\n\r\n")
    if divisor < 0:
        divisor = raw.find(b"\n\n")
    head = raw if divisor < 0 else raw[:divisor]
    value = message_from_bytes(head).get(name)
    if value is None:
        return ""
    return str(make_header(decode_header(value))).strip()


def _address(argument: str, prefix: str) -> str | None:
    """从 ``FROM:<a@b>`` / ``TO:a@b`` 里取出地址；形状不对返回 ``None``。

    ``<>``（空反向路径，RFC 5321 允许）返回**空串**而不是 ``None``：它是一条合法
    的 ``MAIL FROM``，把它读成语法错误会让一次合法的投递在捕获侧变成 ``501``，
    而 ``501`` 在传输那一侧是一条 ``SMTPDataError``（抛），不是 ``refused``。
    两者的区别在调用点是有后果的。

    取出地址**之后**什么也不做：不查、不补全、不试探。这里只有一个字符串切分。
    """
    head, separator, tail = argument.partition(":")
    if not separator or head.strip().upper() != prefix:
        return None
    tail = tail.strip()
    if tail.startswith("<"):
        end = tail.find(">")
        if end < 0:
            return None
        return tail[1:end].strip()
    return tail.split(" ", 1)[0].strip()


class _CaptureRequestServer(socketserver.ThreadingTCPServer):
    """线程化的 TCP 服务端。三格设置各挡一件事。

    * ``daemon_threads`` + ``block_on_close = False``：``stop()`` **不等**一条还挂在
      半路上的客户端连接。等它会让一条卡住的测试连接把整个测试会话挂住，而症状是
      「测试跑不完」—— 那比一条失败更难查。
    * ``allow_reuse_address``：测试会反复在同一个端口上起停，而刚关掉的服务端
      套接字不该让下一次绑定失败。
    """

    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[socketserver.BaseRequestHandler],
        *,
        capture: MailCaptureServer,
    ) -> None:
        self.capture = capture
        # 地址族按**地址的形状**选，不按一个全局常量：一个 IPv6 字面量在 AF_INET
        # 上绑不起来，而绑不起来的症状是一条看不出原因的 ``OSError``。
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        super().__init__(address, handler)


class _SMTPHandler(socketserver.StreamRequestHandler):
    """一行一条命令的 SMTP 子集。**没有 VRFY / EXPN 的分支**（见模块 docstring）。"""

    server: _CaptureRequestServer
    #: 应答**当场**出去。``StreamRequestHandler`` 的默认就是 0，写在这里是因为
    #: 它是承重的：一个缓冲住的应答会让客户端一直等，而现象是「测试挂住」。
    wbufsize = 0

    def handle(self) -> None:
        try:
            self._converse()
        except OSError:
            # 客户端读到一半就走了、或者闲置到了超时。连接的事不是投递的事，
            # 而此刻已经没有人可以收一条 4xx 了 —— 所以回的是「安静地结束」，
            # 不是一张会混进 pytest 输出的 traceback。
            return

    def _converse(self) -> None:
        capture = self.server.capture
        self.connection.settimeout(capture.connection_timeout_seconds)
        self._reply(220, f"{capture.server_name} capture ready")
        mail_from: str | None = None
        recipients: list[str] = []
        while True:
            raw_line = self.rfile.readline(MAX_COMMAND_BYTES)
            if not raw_line:
                return
            command = raw_line.decode("latin-1").rstrip("\r\n")
            verb, _, argument = command.partition(" ")
            verb = verb.upper()
            argument = argument.strip()

            if verb in ("VRFY", "EXPN"):
                # 502 而不是 500：见模块 docstring。这里**没有**任何查名单、
                # 试探或回显地址的分支，回话里也不能有那个地址。
                self._reply(502, f"5.5.1 {verb} is not implemented here")
            elif verb == "EHLO":
                self._reply_multiline(250, capture.server_name, ("HELP",))
            elif verb == "HELO":
                self._reply(250, capture.server_name)
            elif verb == "NOOP":
                self._reply(250, "2.0.0 Ok")
            elif verb == "RSET":
                mail_from, recipients = None, []
                self._reply(250, "2.0.0 Ok")
            elif verb == "QUIT":
                self._reply(221, "2.0.0 Bye")
                return
            elif verb == "MAIL":
                address = _address(argument, "FROM")
                if address is None:
                    self._reply(501, "5.5.4 syntax: MAIL FROM:<address>")
                else:
                    mail_from, recipients = address, []
                    self._reply(250, "2.1.0 Ok")
            elif verb == "RCPT":
                address = _address(argument, "TO")
                if address is None or not address:
                    self._reply(501, "5.5.4 syntax: RCPT TO:<address>")
                elif mail_from is None:
                    self._reply(503, "5.5.1 need MAIL before RCPT")
                elif capture.is_rejected(address):
                    self._reply(550, f"5.1.1 <{address}> recipient rejected by this service")
                else:
                    recipients.append(address)
                    self._reply(250, "2.1.5 Ok")
            elif verb == "DATA":
                mail_from, recipients = self._data(mail_from, recipients)
            else:
                self._reply(500, "5.5.2 command unrecognized")

    def _data(
        self, mail_from: str | None, recipients: list[str]
    ) -> tuple[str | None, list[str]]:
        """收一封信。返回**清空之后**的会话状态（或者原样，如果这一步没成）。"""
        if mail_from is None or not recipients:
            self._reply(503, "5.5.1 need MAIL and RCPT before DATA")
            return mail_from, recipients
        self._reply(354, "End data with <CR><LF>.<CR><LF>")
        payload = self._read_data()
        if payload is None:
            self._reply(552, "5.3.4 message exceeds this service's size limit")
            return None, []
        try:
            received = self.server.capture.ingest(
                payload, mail_from=mail_from, recipients=tuple(recipients)
            )
        except RecipientRefused:
            self._reply(550, "5.1.1 recipient rejected by this service")
            return None, []
        # ``message_id`` 为空时也要回一个可读的凭据（对方可能没写 Message-ID），
        # 但不编一个假的 Message-ID —— 那会让「我们从没收到过这个标识」这件事
        # 在日志里看起来发生过。
        token = received.message_id or f"seq-{received.seq:0{SEQ_WIDTH}d}"
        self._reply(250, f"2.0.0 Ok: queued as {token}")
        return None, []

    def _read_data(self) -> bytes | None:
        """读到单独一行的 ``.`` 为止，并做点去重。超过上限返回 ``None``。

        点去重按 **RFC 5321**：行首的一个 ``.`` 被去掉。实际会出现的是 ``..``
        （正文里本来就以点开头的那一行），而写成「只处理 ``..``」的实现会在正文
        以 ``.foo`` 开头时少去一个点 —— 那一行于是悄悄变了样，而没有人会红。

        ``at_line_start`` 这个变量是承重的：``readline`` 带上限之后，一个超长的行
        会被切成几块，其中一块可能正好以 ``.`` 开头。不跟踪行首的话，正文里一个
        被切在边界上的点会被当成点去重去掉一个。
        """
        limit = self.server.capture.max_message_bytes
        chunks: list[bytes] = []
        total = 0
        at_line_start = True
        oversized = False
        while True:
            chunk = self.rfile.readline(MAX_DATA_CHUNK_BYTES)
            if not chunk:
                raise ConnectionError("客户端在 DATA 中间断开了")
            if at_line_start and chunk in (b".\r\n", b".\n"):
                break
            if at_line_start and chunk.startswith(b"."):
                chunk = chunk[1:]
            at_line_start = chunk.endswith(b"\n")
            total += len(chunk)
            if total > limit:
                # 超限之后继续读到终止行（否则剩下那一段会被当成命令解析），
                # 但不再往内存里堆 —— 一个上限只有在超出那一刻就停止占用时才是上限。
                oversized = True
                chunks = []
                continue
            if not oversized:
                chunks.append(chunk)
        return None if oversized else b"".join(chunks)

    def _reply(self, code: int, text: str) -> None:
        self.wfile.write(f"{code} {text}\r\n".encode("ascii", "replace"))

    def _reply_multiline(self, code: int, first: str, rest: Sequence[str]) -> None:
        lines = [first, *rest]
        last = len(lines) - 1
        payload = "".join(
            f"{code}-{line}\r\n" if index < last else f"{code} {line}\r\n"
            for index, line in enumerate(lines)
        )
        self.wfile.write(payload.encode("ascii", "replace"))


__all__ = [
    "INDEX_FILENAME",
    "MailCaptureServer",
    "MailCaptureUnavailable",
    "ReceivedMessage",
    "RecipientRefused",
    "read_index",
]

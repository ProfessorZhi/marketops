"""``SmtpMailTransport`` 的凭据那一半：**口令只在 TLS 上出示，而且出示方式不是猜的**。

## 为什么这一组判据单独一个文件

``tests/test_outreach_mail_contract.py`` 测的是**端口契约**（两个实现在同一组判据
下行为一致），而它构造两个实现时都不给凭据 —— 那是对的：那里测的是「一封信长什么
样」与「投递结论怎么读」。凭据是另一件事：它是 §6 三层防线里的第二层，而它的失败
方式与投递失败长得很像（一次 ``send`` 抛了），所以它必须有自己的一组判据。

## 这一组里最要紧的一条

:func:`test_credentials_are_never_presented_on_a_plaintext_connection`。它不是
「构造器会拒一个坏参数」，而是**接线的一条行为**：把一份带凭据的传输指向一台不
宣告 STARTTLS 的中继时，客户端**一个字节的凭据都不许说出去**。

它用的那台中继刻意宣告 ``AUTH``（见 :class:`_PlaintextRelay`）。这不是装饰：
一台两样都不宣告的中继**测不出**这一条 —— 一个「STARTTLS 不成就算了、接着
``login``」的实现在那里会因为服务端不支持 AUTH 而在同一个地方抛同一种异常，
判据绿，而它绿的原因与「口令有没有进明文段」毫不相干。
"""

from __future__ import annotations

import socket
import smtplib
import threading
from pathlib import Path

import pytest

from marketops.adapters.smtp_mail import SECURITY_MODES, SmtpMailTransport
from marketops.config import SMTP_SECURITY_MODES, ConfigError, load_settings
from marketops.domain.outreach import DraftRef, ManifestEntry, OutreachStage
from marketops.domain.util import normalise_email, sha256_text

SENDER = "outreach@example.invalid"
#: 一个正常的收件人。``.invalid`` 是 RFC 2606 保留域，它**不解析**。
RECIPIENT = "buyer@example.invalid"
SECRET = "not-a-real-password"
SUBJECT = "一次交流"
BODY = "一封测试信。\n"


# --- 构造期就拒：接线的一半不是接线 -------------------------------------------


def test_the_two_tls_modes_agree_with_the_configuration_face() -> None:
    """配置面与实现面的闭集必须逐字相同。

    两份的存在理由不同（配置要在**装载期**拒掉拼错的值，而装载期不 import 任何
    adapter；实现要按它分支），所以它们不能合成一份。但它们必须有判据相连：
    只给实现加第三种方式，配置面上它永远进不来 —— 那时这条判据红，而红的正是
    「加了却没人能配」这件事。
    """
    assert SECURITY_MODES == SMTP_SECURITY_MODES


def _transport(**overrides: object) -> SmtpMailTransport:
    """一份**合法**的构造参数，可逐格覆盖。"""
    values: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 587,
        "from_address": SENDER,
        "username": "sender@example.invalid",
        "password": SECRET,
        "security": "starttls",
    }
    values.update(overrides)
    return SmtpMailTransport(**values)  # type: ignore[arg-type]


def test_a_password_without_a_username_is_refused() -> None:
    with pytest.raises(ValueError, match="username"):
        _transport(username=None, password=SECRET)


def test_a_username_without_a_password_is_refused() -> None:
    """**空口令不算给了口令**：那会让「漏配」与「这个中继真的没有口令」同形。"""
    with pytest.raises(ValueError, match="password"):
        _transport(password=None)


def test_credentials_without_a_declared_mode_are_refused() -> None:
    """有口令却不说明凭什么方式出示 —— 缺省不会是一个安全的猜。"""
    with pytest.raises(ValueError, match="security=None"):
        _transport(security=None)


def test_a_tls_mode_without_credentials_is_refused() -> None:
    """一个不登录的 TLS 连接只能靠网络位置被放行，而那正是这一对要排掉的事。"""
    with pytest.raises(ValueError, match="凭据"):
        _transport(username=None, password=None, security="starttls")


def test_an_unknown_mode_is_refused_by_name() -> None:
    with pytest.raises(ValueError, match="starttls"):
        _transport(security="plain")


def test_a_typo_in_the_configuration_is_refused_at_load_not_at_first_send(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """写坏的取值在**装载期**抛。

    落回 ``None`` 会让一个拼错的值表现为「没配这一格」，而两句话的处置不同
    （一个是去改拼写、一个是去补配置）。大小写也不折：一个只在大小写上不同的
    配置项是下一台机器上的一处静默分歧。
    """
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARKETOPS_SMTP_SECURITY", "starlts")
    with pytest.raises(ConfigError, match="MARKETOPS_SMTP_SECURITY"):
        load_settings()

    monkeypatch.setenv("MARKETOPS_SMTP_SECURITY", "StartTLS")
    with pytest.raises(ConfigError, match="MARKETOPS_SMTP_SECURITY"):
        load_settings()


def test_an_unset_mode_is_none_and_names_itself_as_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """未设置 = ``None`` = 这一样还没配，而不是「配了一个空字符串」。"""
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARKETOPS_SMTP_SECURITY", "")
    settings = load_settings()

    assert settings.smtp_security is None
    assert "smtp_security" in settings.outbound_missing


# --- 行为：口令不出现在明文段里 -------------------------------------------------


class _PlaintextRelay:
    """一台**只说明文**的中继，它把客户端说过的每一个字节记下来。

    它宣告 ``AUTH`` 而**不**宣告 ``STARTTLS`` —— 这个组合就是那条判据要的局面：
    凭据在协议上是**可以**出示的，唯一拦着它的是一次从未发生的 TLS 协商。
    宣告 ``AUTH`` 的理由写在模块 docstring 里，一句话：不宣告它就测不出这一条。

    它**不收信**（没有 ``DATA`` 分支，也不写盘）：这一条判据问的不是「信有没有
    落地」，而是「凭据有没有上过明文线」—— 后者更强，前者由
    ``tests/test_outreach_mail_contract.py`` 那组负责。
    """

    def __init__(self) -> None:
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self._listener = listener
        self.port = int(listener.getsockname()[1])
        self._said = bytearray()
        #: 客户端不再说话时置位。判据等它，而不是自己睡一会儿。
        self.finished = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    #: 收尾要等多久。**它是一段「静默」的长度，不是一段「用时」的长度** ——
    #: 每一次读到字节都会重新计时，所以只要客户端还在说，这个窗口就不会关。
    #:
    #: 为什么要用它而不是「等到对端断开」：断开不一定真的到达（异常对象的
    #: traceback 会一直握着 ``_connect`` 那一帧，而 ``client`` 是那一帧的局部
    #: 变量 —— 套接字因此可以比这次会话活得更久）。等断开就会变成等满整个
    #: 超时，而那时**红的是一条同步判据，不是那条主句**：一次红的理由对不上
    #: 它的判词，下一个读它的人会以为「凭据没进明文段」这件事没人管。
    _QUIET_SECONDS = 1.0

    def _serve(self) -> None:
        try:
            connection, _ = self._listener.accept()
        except OSError:
            self.finished.set()
            return
        with connection:
            connection.settimeout(self._QUIET_SECONDS)
            try:
                connection.sendall(b"220 plaintext-relay.invalid capture ready\r\n")
                self._greet(connection)
                self._converse(connection)
            except OSError:
                pass
        self.finished.set()

    def _greet(self, connection: socket.socket) -> None:
        """等到第一行命令（``ehlo``），然后回一条**宣告 AUTH、不宣告 STARTTLS** 的应答。"""
        greeting = bytearray()
        while b"\r\n" not in greeting:
            chunk = self._read(connection)
            if chunk is None:
                self._said += greeting
                return
            greeting += chunk
        self._said += greeting
        connection.sendall(
            b"250-plaintext-relay.invalid\r\n250-AUTH PLAIN LOGIN\r\n250 HELP\r\n"
        )

    def _converse(self, connection: socket.socket) -> None:
        """把剩下的每一句话都记下来，各回一条认证失败，直到客户端安静下来。"""
        while True:
            chunk = self._read(connection)
            if chunk is None:
                return
            self._said += chunk
            # 任何后续命令都回一条认证失败。一个正常的实现根本走不到这里
            # （它在协商那一步就抛了）；一个「退回明文」的实现会在这里收到
            # 535，然后按 ``smtplib`` 的机制列表换下一种再试一次 —— 它试了
            # 几次不重要，重要的是那几条命令已经进了 ``_said``。
            connection.sendall(b"535 5.7.8 authentication refused by this service\r\n")

    @staticmethod
    def _read(connection: socket.socket) -> bytes | None:
        """读一段。``None`` = 该收尾了（对端断开、出错、或者不再说话）。"""
        try:
            chunk = connection.recv(4096)
        except OSError:
            return None
        return chunk or None

    @property
    def said(self) -> bytes:
        """客户端说过的字节。**只在 :attr:`finished` 之后读它才是完整的。**"""
        return bytes(self._said)

    def stop(self) -> None:
        self._listener.close()


def _entry() -> ManifestEntry:
    return ManifestEntry(
        draft=DraftRef(
            draft_id="draft-0001", draft_version=1, body_sha256=sha256_text(BODY)
        ),
        route_ref="route-0001",
        account_id="test-account-001",
        recipient=normalise_email(RECIPIENT),
        sendbox=SENDER,
        stage=OutreachStage.STAGE_1,
        family_id="family-0001",
        writing_policy_id="policy-0001",
        evidence_ids=("evidence-0001",),
    )


def test_credentials_are_never_presented_on_a_plaintext_connection() -> None:
    """中继不宣告 STARTTLS 时，凭据**一个字节都不说出去**。

    三件事分开断言，因为它们各自可以单独不成立：

    1. ``EHLO`` 说过 —— 否则「没说 AUTH」可能只是因为根本没连上，判据空转；
    2. 抛的是 ``SMTPNotSupportedError`` —— 失败的原因是「对端不支持 TLS」，
       而不是别的任何一种（认证被拒、超时、DNS）。判词必须对得上理由；
    3. ``AUTH`` 与 ``MAIL FROM`` 都没说过、正文也没出现过 —— 这一条是主句。

    第 3 条是「口令只在 TLS 上出示」的执行器。一个「STARTTLS 不成就算了」的
    实现在这里会红，而它在**别的任何一条判据下都不会红**：信发出去了，报告上
    写着发送成功。
    """
    relay = _PlaintextRelay()
    try:
        transport = SmtpMailTransport(
            host="127.0.0.1",
            port=relay.port,
            from_address=SENDER,
            username="sender@example.invalid",
            password=SECRET,
            security="starttls",
        )
        with pytest.raises(smtplib.SMTPNotSupportedError):
            transport.send(
                entry=_entry(), subject=SUBJECT, body=BODY, from_address=SENDER
            )
        assert relay.finished.wait(timeout=10.0), (
            "中继没有把这次会话记完（它要等一段静默才收尾），所以下面读到的东西不"
            "完整 —— 而在一段不完整的话里判「没说 AUTH」是一次假绿。"
        )
    finally:
        relay.stop()

    said = relay.said.decode("latin-1")
    # ``smtplib`` 发的命令动词是小写的（``ehlo`` / ``auth`` / ``mail``），所以按
    # 大写比对 —— 一个「大小写敏感地找 AUTH」的写法会在**正确**的实现上判「没说
    # AUTH」，而它在错误的实现上同样判「没说 AUTH」：那条判据永远绿。
    said_upper = said.upper()
    assert "EHLO" in said_upper, f"这次会话根本没开始过，后面的断言都是空转：{said!r}"
    assert "AUTH" not in said_upper, (
        "客户端在一条从未协商过 TLS 的连接上尝试出示凭据。\n"
        f"  TLS 协商没有成功，凭据就不该被说出来；它说了这一段：{said!r}"
    )
    assert "MAIL FROM" not in said_upper, f"口令没能出示，而这一封信还是往外发了：{said!r}"
    assert BODY.encode() not in relay.said, "正文进了明文段 —— 这一封信已经在路上了。"

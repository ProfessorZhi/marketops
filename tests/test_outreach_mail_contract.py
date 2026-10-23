"""``MailTransport`` 契约：**同一组判据，对着两个实现各跑一遍**。

## 为什么两个实现各自绿是不夠的

``ports/outreach.py`` 抽出 ``MailTransport`` 时点名的理由是「它是唯一一个真的会把
字节交给外界的接缝」。但端口本身不构成契约 —— 构成契约的是**同一组判据同时看着
两个实现**。本仓已有的先例是 ``tests/test_evidence_store_contract.py``（三个实现）、
``tests/test_budget_contract.py`` 与 ``tests/test_scene_store_contract.py``（各两个）。

两个实现：

* ``adapters/smtp_mail.py`` —— 真的说 SMTP，走一个真的监听套接字。
* ``adapters/mail_capture_transport.py`` —— 直接调同进程捕获服务的入口，不起 TCP。

``@pytest.fixture(params=...)`` 把实现名放进测试 id，红了的时候一眼看得见是哪一个。

## 「远端不在」这件事，两个实现各有各的**实现方式**

负对照（``test_a_far_end_that_is_not_there_is_never_an_acceptance``）判的是
「远端不在时不得回一个受理」。这句话在两个实现里的**做法**不同：一个连的是一个
刚绑过、随即停止监听的端口，另一个拿的是一台从未 ``start()`` 的捕获服务。

做法不同不是妥协，而正是这条判据在测的东西：它测的是「远端不在」这件事，
不是「TCP 端口关着」这件事。把判据写成「连一个关闭的端口」，第二个实现就只能
去假装自己也有一张网卡 —— 而一个为了通过判据而长出一张假网卡的实现，恰好是这组
测试要挡的那种东西。

## 一切路径都在 ``tmp_path`` 下

```.eml`` 与 ``index.jsonl`` 都落在 ``tmp_path`` 里那个显式传给捕获服务的目录下。
本机的 ``/tmp`` 是共享的（``memory: tmp-is-shared-with-other-users-on-this-host``），
而写死的路径会让并行的两个测试互相盖掉对方的东西 —— 那时红的是「内容不对」，
不是「撞车了」。
"""

from __future__ import annotations

import email
import socket
from dataclasses import dataclass
from email import policy
from pathlib import Path

import pytest

from marketops.adapters.mail_capture import (
    MailCaptureServer,
    read_index,
)
from marketops.adapters.mail_capture_transport import MailCaptureTransport
from marketops.adapters.smtp_mail import SmtpMailTransport
from marketops.domain.outreach import DraftRef, ManifestEntry, OutreachStage
from marketops.domain.util import normalise_email, sha256_bytes, sha256_text
from marketops.ports.outreach import MailTransport

#: 发送箱。``.invalid`` 是 RFC 2606 保留的顶层域：它**不解析**，所以这个地址
#: 不可能指向任何一个真实的人。
SENDER = "outreach@example.invalid"
#: 一个正常的收件人。
ALLOWED = "buyer@example.invalid"
#: 一个被捕获服务拒掉的收件人（拒绝名单是构造参数，见 :meth:`_Rig.pair`）。
BLOCKED = "blocked@example.invalid"

SUBJECT = "一次关于场地维护的交流"
BODY = "您好，\n\n我们注意到贵司的场地维护计划，想简单聊一次。\n\n—— 示例公司\n"


def make_entry(*, recipient: str, body: str = BODY) -> ManifestEntry:
    """清单里的一行。**只构造它要的那几格**，其余按最小合法值给。"""
    return ManifestEntry(
        draft=DraftRef(
            draft_id="draft-0001",
            draft_version=1,
            body_sha256=sha256_text(body),
        ),
        route_ref="route-0001",
        account_id="test-account-001",
        recipient=normalise_email(recipient),
        sendbox=SENDER,
        stage=OutreachStage.STAGE_1,
        family_id="family-0001",
        writing_policy_id="policy-0001",
        evidence_ids=("evidence-0001",),
    )


def send(transport: MailTransport, *, recipient: str = ALLOWED, body: str = BODY):
    """按契约的调用形状发一封。**调用点写一次**，免得两个实现各写一遍参数表。"""
    return transport.send(
        entry=make_entry(recipient=recipient, body=body),
        subject=SUBJECT,
        body=body,
        from_address=SENDER,
    )


def _bound_but_not_listening() -> tuple[socket.socket, int]:
    """一个 ``bind`` 了、但**没有** ``listen`` 的套接字与它的端口。

    这样拿到的端口有两个性质，两个都是需要的：

    * 连上去必然被拒（没有 ``listen`` 的端口回了 RST），
    * 而且**在套接字关掉之前没有别人能绑它** —— 用「bind 完随即 close」拿到的
      端口会被并行的另一个进程抢走，那时负对照连上的是别人的服务，
      而它红了会看起来像代码坏了。
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    return probe, probe.getsockname()[1]


@dataclass
class _Rig:
    """一次测试里用到的那一组东西：捕获服务、传输、以及各自的「远端不在」。"""

    impl: str
    base: Path

    def __post_init__(self) -> None:
        self.servers: list[MailCaptureServer] = []
        self.probes: list[socket.socket] = []

    @property
    def root(self) -> Path:
        """落盘目录。**显式传给捕获服务**，不在服务里有缺省值。"""
        return self.base / "capture"

    def pair(self, *, reject_recipients: tuple[str, ...] = ()):
        """起一台捕获服务，并给它配一个**当前实现**的传输。"""
        server = MailCaptureServer(
            self.root,
            host="127.0.0.1",
            # 端口 0 = 让内核分配。两个实现都拿得到真实端口（对同进程那个实现
            # 来说这无关紧要，但它拿到的仍然是同一台服务）。
            port=0,
            reject_recipients=reject_recipients,
        )
        server.start()
        self.servers.append(server)
        return server, self.transport_for(server)

    def transport_for(self, server: MailCaptureServer) -> MailTransport:
        if self.impl == "smtp":
            return SmtpMailTransport(host="127.0.0.1", port=server.port, from_address=SENDER)
        return MailCaptureTransport(server, from_address=SENDER)

    def far_end_missing(self) -> MailTransport:
        """一个远端**不在**的传输。见模块 docstring：两个实现的做法不同。"""
        if self.impl == "smtp":
            probe, port = _bound_but_not_listening()
            self.probes.append(probe)
            return SmtpMailTransport(host="127.0.0.1", port=port, from_address=SENDER)
        # 落盘目录**先建好**，再拿一台从未 start() 的服务。这一句不是多余的准备：
        # 目录不存在的话，一个「服务没在听也照样受理」的实现会在写盘那一步撞上
        # ``FileNotFoundError``，于是这条判据仍然是绿的 —— 而它绿的原因是
        # **目录不存在**，与「服务在不在」毫无关系。判据必须测它要测的那件事：
        # 目录在、可写、一切正常，唯一的差别只在于这台服务没有在听。
        absent_root = self.base / "far-end-absent"
        absent_root.mkdir(parents=True, exist_ok=True)
        idle = MailCaptureServer(absent_root, host="127.0.0.1", port=0)
        self.servers.append(idle)
        return MailCaptureTransport(idle, from_address=SENDER)

    def close(self) -> None:
        for server in self.servers:
            server.stop()
        for probe in self.probes:
            probe.close()


@pytest.fixture(params=["smtp", "capture"])
def mail_rig(request: pytest.FixtureRequest, tmp_path: Path):
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    rig = _Rig(impl=request.param, base=tmp_path)
    try:
        yield rig
    finally:
        rig.close()


# --- 一封正常的信 -------------------------------------------------------------


def test_an_accepted_letter_lands_in_the_directory_it_was_given(mail_rig: _Rig) -> None:
    """一封正常的信真的被服务收下，而且落在**显式给的那个目录**里。

    这里同时断四件事，它们各自是一次真实的失败：

    * 受理带回了标识（没有它，对账那一步没有一个可以问对方的键）；
    * ``.eml`` 落在 ``tmp_path`` 底下（不是当前目录、不是某个缺省目录）；
    * 索引里那一条的 ``sha256`` 与**文件字节**一致（索引是不是在替一个不存在的
      文件作证）；
    * 文件名以收到顺序开头（目录列表按名字排就是按收到顺序排）。
    """
    server, transport = mail_rig.pair()
    receipt = send(transport)

    assert receipt.outcome == "accepted"
    assert receipt.accepted is True
    assert receipt.message_id, "受理必须带回一个标识"

    index = read_index(mail_rig.root)
    assert len(index) == 1, f"索引里应当恰好有一条，实际 {len(index)}"
    received = index[0]

    assert received.message_id == receipt.message_id
    assert received.to_addresses == (ALLOWED,)
    assert received.path.parent == mail_rig.root
    assert received.path.name.startswith(f"{received.seq:0{6}d}--")
    assert received.path.suffix == ".eml"
    assert received.sha256 == sha256_bytes(received.path.read_bytes())
    assert received.body_bytes == received.path.read_bytes()

    # 收下来的字节是一封**能解析**的信：主题原样、正文是 text/plain 且内容没变。
    parsed = email.message_from_bytes(received.body_bytes, policy=policy.default)
    assert parsed.get_content_type() == "text/plain"
    assert parsed["To"] == ALLOWED
    assert parsed["From"] == SENDER
    assert received.subject == SUBJECT
    # 正文里的换行在盘上是 ``\r\n``，不是 ``\n`` —— 报文（RFC 5322）的行尾就是
    # CRLF，而这里比的是**收下来的那些字节**。不把两端折到同一个行尾约定上，
    # 这条判据要么永远红，要么逼出一个「悄悄把 \r\n 换成 \n 再落盘」的实现 ——
    # 而那个实现改的是**收下来的字节**，于是索引里的 ``sha256`` 与文件对不上了，
    # 上面那条判据会在另一个地方红，且看不出与这里有关系。
    assert parsed.get_content().replace("\r\n", "\n").strip() == BODY.strip()
    assert parsed["Message-ID"] == receipt.message_id


# --- 明确被拒 -----------------------------------------------------------------


def test_a_recipient_the_service_refuses_comes_back_as_refused(mail_rig: _Rig) -> None:
    """明确被拒 → ``refused``，带原因，而且**什么也没落盘**。

    最后那一半与前面两半同样重要：一封被拒的信在盘上留下一个 ``.eml``，会让
    「被拒」与「受理了」在事后看起来是同一件事 —— 而它们的处置相反。
    """
    _, transport = mail_rig.pair(reject_recipients=(BLOCKED,))
    receipt = send(transport, recipient=BLOCKED)

    assert receipt.outcome == "refused"
    assert receipt.accepted is False
    assert receipt.reason.strip(), "明确被拒必须写明原因"
    assert read_index(mail_rig.root) == ()


def test_the_same_transport_still_accepts_an_allowed_recipient(mail_rig: _Rig) -> None:
    """拒绝名单不是「全都拒」—— 否则上面那条判据在一个恒拒的实现上也是绿的。"""
    _, transport = mail_rig.pair(reject_recipients=(BLOCKED,))
    assert send(transport, recipient=BLOCKED).outcome == "refused"
    assert send(transport, recipient=ALLOWED).outcome == "accepted"


# --- 服务停了之后 -------------------------------------------------------------


def test_a_stopped_service_is_never_an_acceptance(mail_rig: _Rig) -> None:
    """服务停掉之后再发：**抛**或者 ``unknown``，绝不是 ``accepted``，也绝不是 ``refused``。

    为什么 ``refused`` 在这里同样是错的：它是**可以重试**的那一个取值，而此刻
    我们连「有没有出去」都不知道。一个把失败读成 ``refused`` 的实现，会让调用方
    去重发一封可能已经到达的信 —— 而盘上那看起来只是「重试了一次」。
    """
    server, transport = mail_rig.pair()
    server.stop()

    outcome = "（抛了）"
    try:
        outcome = send(transport).outcome
    except Exception:
        pass

    assert outcome != "accepted", "服务已经停了，却报了一次受理"
    assert outcome != "refused", "分不清时不得读成可以重试的那一个取值"


# --- 查询 ---------------------------------------------------------------------


def test_status_answers_unknown_without_raising(mail_rig: _Rig) -> None:
    """``status`` 对两个实现都返回 ``unknown``，带原因，且**不抛**。

    问的是一个**刚刚**受理过的标识：一个在索引里查得到就回 ``accepted`` 的实现
    会在这里红 —— 而它红得对，「我们捕获到了它」不是「对方收到了它」。
    """
    _, transport = mail_rig.pair()
    receipt = send(transport)

    answered = transport.status(receipt.message_id)
    assert answered.outcome == "unknown"
    assert answered.accepted is False
    assert answered.reason.strip(), "查不到必须说得出为什么"

    # 一个从来没发过的标识，答的是同一件事 —— 不是抛。
    assert transport.status("<never-sent@example.invalid>").outcome == "unknown"


# --- 身份 ---------------------------------------------------------------------


def test_sender_identity_is_the_address_it_was_built_with(mail_rig: _Rig) -> None:
    """「我们用的是哪个发送箱」不能在部署时变成一个秘密。"""
    _, transport = mail_rig.pair()
    assert transport.sender_identity == SENDER


# --- 稳定的 Message-ID --------------------------------------------------------


def test_the_same_letter_carries_the_same_message_id_twice(mail_rig: _Rig) -> None:
    """§6 要求稳定的 Message-ID：同一封信发两次，带的是**同一个**标识。

    这条判据**看不见另一个实现**（参数化的 fixture 一次只看一个），所以它单独
    不足以证明两个实现派生得一样 —— 那件事由「派生只有一处定义」保证
    （``smtp_mail.stable_message_id``，两个实现都用它）。
    """
    _, transport = mail_rig.pair()
    first = send(transport)
    second = send(transport)

    assert first.message_id == second.message_id
    assert [item.message_id for item in read_index(mail_rig.root)] == [
        first.message_id,
        first.message_id,
    ]


# --- 负对照 -------------------------------------------------------------------


def test_a_far_end_that_is_not_there_is_never_an_acceptance(mail_rig: _Rig) -> None:
    """负对照：远端**不在**时，得到的必须是抛出的异常或 ``unknown``。

    没有这一条，上面所有「受理」的判据在一个永远回 ``accepted`` 的实现上全绿 ——
    而那正是最坏的一种实现：它会让调用方以为一封信出去了，于是不去重试，
    于是那封信永远没有出去。
    """
    transport = mail_rig.far_end_missing()

    outcome = "（抛了）"
    try:
        outcome = send(transport).outcome
    except Exception:
        pass

    assert outcome != "accepted"
    assert outcome != "refused"


# --- VRFY 真的被 502 ----------------------------------------------------------


def test_vrfy_and_expn_are_refused_by_the_service(tmp_path: Path) -> None:
    """直连套接字手工发 ``VRFY`` / ``EXPN``：两条都必须被 ``502``。

    回 ``502``（命令本身不被支持）而不是 ``500``（命令拼错了）是有意的 —— 见
    ``adapters/mail_capture.py`` 的模块 docstring。这里同时断回话里**没有**那个
    地址：一条 502 带着地址回来的服务，等于把探测接口换了个状态码。
    """
    server = MailCaptureServer(tmp_path / "capture", host="127.0.0.1", port=0)
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=5) as connection:
            stream = connection.makefile("rb")
            assert stream.readline().startswith(b"220 ")
            for verb in ("VRFY", "EXPN"):
                connection.sendall(f"{verb} {ALLOWED}\r\n".encode("ascii"))
                reply = stream.readline()
                assert reply.startswith(b"502 "), f"{verb} 得到的是 {reply!r}"
                assert ALLOWED.encode("ascii") not in reply, (
                    f"{verb} 的回话里带着那个地址 —— 那已经是一个探测接口了"
                )
            connection.sendall(b"HELO tester.example.invalid\r\n")
            assert stream.readline().startswith(b"250 ")
    finally:
        server.stop()


def test_the_capture_service_binds_where_it_was_told_and_nowhere_else(
    tmp_path: Path,
) -> None:
    """绑定地址与端口都来自参数，而且**通配地址被直接拒绝**。

    这条守的是一条已经记过的教训（本机服务默认只允许绑回环）：一个默认绑
    全部网卡的服务在共享主机上就是别人也能投信的口子，而它的症状是「测试全绿」
    外加「机器上有一个没人知道的服务在听」。
    """
    for wildcard in ("0.0.0.0", "::", ""):
        with pytest.raises(ValueError):
            MailCaptureServer(tmp_path / "capture", host=wildcard, port=0)

    server = MailCaptureServer(tmp_path / "capture", host="127.0.0.1", port=0)
    server.start()
    try:
        assert server.port != 0, "start() 之后必须报出内核分配的那个端口"
        with socket.create_connection(("127.0.0.1", server.port), timeout=5):
            pass
    finally:
        server.stop()

    # 停掉之后端口**立刻**释放：同一个端口可以马上被重新绑定。
    released = server.port
    # 这一句不是废话：``port`` 若在 ``stop()`` 之后回落到请求值 ``0``，下面那次
    # 重新绑定会成功，而成功的原因只是内核又随便分了一个 —— 于是这条判据在一个
    # **没有**释放端口的实现上也是绿的。先钉住它是真的那个端口，判据才成立。
    assert released != 0, "stop() 之后必须仍然报出真的绑过的那个端口"

    again = MailCaptureServer(tmp_path / "capture", host="127.0.0.1", port=released)
    again.start()
    try:
        assert again.port == released
    finally:
        again.stop()

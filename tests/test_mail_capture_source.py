"""``MailCaptureSource`` —— **捕获目录读回来的东西是不是那封信**，由本文件执行。

## 这里守的四件事，每一件都有它自己的失败形状

1. **摘要必须核**（``test_a_file_whose_bytes_no_longer_match_the_index_invalidates``）。
   索引说一套、字节是另一套时，静默跳过是唯一**不留痕迹**的处置：对方的一封信被
   丢掉，而报告上写着「这一批读完了」。本文件的这一条**真的改写了盘上的字节**
   —— 一个只在内存里改一个字段的测试证明不了这件事，因为要防的正是「盘上的字节
   与记录对不上」。
2. **索引不在 ⇒ 空批次，不是异常**（``test_a_missing_index_is_an_empty_batch``）。
   逐字来自 ``mail_capture.read_index`` 的 docstring：「还没有收到任何东西」是一个
   正常的读取结果。
3. **游标只回答「读到哪里了」，不回答「落库了没有」**
   （``test_the_store_recognises_a_replayed_batch_not_this_layer``）。同一批读两次
   会得到同样的条目 —— 去重的执行器在存储层（``dedupe_key``），不在这里。两个
   层次各管一半，而管错的代价是「读到了但没落库」的影子台账。
4. **结构判得出来的才给 ``kind``**（``test_a_dsn_status_decides_hard_and_soft``、
   ``test_auto_submitted_decides_an_auto_reply``）。退信与自动回复是收信协议自己
   写着答案的两件事；其余一律 ``None``，因为分类是另一个端口的活。

## 两个前门都用

``MailCaptureServer.ingest`` 收下的字节（真的过一遍 ``.eml`` 落盘与索引追加）与
手工写下的 ``index.jsonl`` 都出现：前者测「我们收下什么就读回什么」，后者测那些
**捕获服务不会自己产生**的输入（naive 的时刻、空的 message id、坏掉的索引行、
坏掉的摘要）。第二条路是必须的 —— 一个只喂「本仓自己写出来的索引」的测试，测的
是这一层与它自己的一致性。

**不联网。** 捕获服务绑 ``127.0.0.1``、端口 ``0``（§6 允许的隔离验证），落盘目录
是 ``tmp_path``（本机的 ``/tmp`` 是共享的，写死的路径会让并行的测试互相盖掉对方的
东西 —— 那时红的是「内容不对」，不是「撞车了」）。
"""

from __future__ import annotations

import ast
import io
import json
from datetime import datetime, timedelta, timezone
from email import policy
from email.generator import BytesGenerator
from email.message import EmailMessage
from pathlib import Path

import pytest

from marketops.adapters.mail_capture import (
    INDEX_FILENAME,
    SEQ_WIDTH,
    MailCaptureServer,
    read_index,
)
from marketops.adapters.mail_capture_source import MailCaptureSource
from marketops.adapters.memory import InMemoryOutreachStore
from marketops.adapters.reply_classifier import RuleBasedReplyClassifier
from marketops.application.correspondence import ingest
from marketops.domain.outreach import KIND_DISPOSITION, CorrespondenceKind
from marketops.domain.util import sha256_bytes, utc_now
from marketops.ports.correspondence import CorrespondenceBatch, CursorState

UTC = timezone.utc
SENDBOX = "outreach@marketops.example"
SENDER = "buyer@example.invalid"
WORKSPACE = "ws-example-uk"
MESSAGE_ID = "<reply-0001@example.invalid>"

#: 一条回信的主体。**刻意是一句人写的、内容相关的话**：它必须读得出来是正文，
#: 也必须不比别的句子多任何一点结构性含义（于是它会走到分类器的弃权那一步）。
REPLY_BODY = "Thanks for the note — Tuesday afternoon works for me.\n"


# ----------------------------------------------------------------------
# 构造：字节、索引行、捕获服务
# ----------------------------------------------------------------------


def _wire(message: EmailMessage) -> bytes:
    """一条消息 → 它在网线上的那些字节。**用标准库生成，不手拼字符串。**

    手拼一份报文很容易写出一行超长的头、或者把 LF 与 CRLF 两种换行混着用，
    而那些畸形恰好会让下游的解析行为变得不可预测 —— 于是测试红的理由是
    「我拼错了报文」，与它要守的那条判据无关。
    """
    buffer = io.BytesIO()
    BytesGenerator(buffer, policy=policy.default).flatten(message, linesep="\r\n")
    return buffer.getvalue()


def _letter(*, body: str = REPLY_BODY, message_id: str = MESSAGE_ID, **headers: str) -> bytes:
    """一封普通的信。``headers`` 里给的都是头字段。

    ``Message-ID`` 有一个缺省值而不是留空：捕获服务在索引里记的是报文里那一个
    （``_header_value(raw, "Message-ID")``），留空的话每封信的 ``message_id`` 都
    是空串，而「索引里那一格是什么」就没被任何一条判据碰到。
    """
    message = EmailMessage(policy=policy.default)
    message["From"] = headers.pop("From", f"Buyer <{SENDER}>")
    message["To"] = headers.pop("To", SENDBOX)
    message["Subject"] = headers.pop("Subject", "Re: a short note")
    message["Message-ID"] = message_id
    for name, value in headers.items():
        message[name.replace("_", "-")] = value
    message.set_content(body)
    return _wire(message)


def _alternative_letter(*, plain: str, html: str) -> bytes:
    """一封 ``multipart/alternative``：两种格式表达同一件事。"""
    message = EmailMessage(policy=policy.default)
    message["From"] = f"Buyer <{SENDER}>"
    message["To"] = SENDBOX
    message["Subject"] = "Re: a short note"
    message["Message-ID"] = MESSAGE_ID
    message.set_content(plain)
    message.add_alternative(html, subtype="html")
    return _wire(message)


def _html_only_letter(*, html: str) -> bytes:
    """只有 HTML 的那一种。**正文退回它的原文，不做转换** —— 见模块 docstring。"""
    message = EmailMessage(policy=policy.default)
    message["From"] = f"Buyer <{SENDER}>"
    message["To"] = SENDBOX
    message["Subject"] = "Re: a short note"
    message["Message-ID"] = MESSAGE_ID
    message.set_content(html, subtype="html")
    return _wire(message)


def _dsn(*, status: str, original_message_id: str = "<our-letter@marketops.example>") -> bytes:
    """一封 RFC 3464 的投递状态通知（DSN）。

    手工拼这几行是刻意的：本仓没有**生成** DSN 的能力（我们没有收信服务器那一侧），
    而这条判据读的正是收信服务器会写下的形状。用标准库的 ``EmailMessage`` 拼不出
    ``message/delivery-status`` 那一段的块结构，所以这里直接写字节。
    """
    head = (
        "From: MAILER-DAEMON@example.invalid\r\n"
        f"To: {SENDBOX}\r\n"
        "Subject: Undelivered Mail Returned to Sender\r\n"
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
        "Final-Recipient: rfc822; buyer@example.invalid\r\n"
        "Action: failed\r\n"
        f"Status: {status}\r\n"
        f"Original-Message-ID: {original_message_id}\r\n"
        "\r\n"
        "--BOUNDARY-1\r\n"
        "Content-Type: message/rfc822\r\n"
        "\r\n"
        "From: we@marketops.example\r\n"
        f"Message-ID: {original_message_id}\r\n"
        "Subject: a short note\r\n"
        "\r\n"
        "Hello.\r\n"
        "--BOUNDARY-1--\r\n"
    )
    return head.encode("utf-8")


def _row(
    *,
    seq: int,
    path: Path,
    message_id: str = MESSAGE_ID,
    mail_from: str = SENDER,
    subject: str = "Re: a short note",
    received_at: str = "2026-09-23T09:00:00+00:00",
    sha256: str | None = None,
    raw: bytes | None = None,
) -> dict:
    """一条索引行。**字段与 ``MailCaptureServer.ingest`` 写下的那九个逐字相同**
    —— 少一个都会让 ``read_index`` 抛 ``KeyError``，而那时红的是「我少写了一个键」。
    """
    body = raw if raw is not None else path.read_bytes()
    return {
        "seq": seq,
        "message_id": message_id,
        "from": mail_from,
        "to": [SENDBOX],
        "subject": subject,
        "bytes": len(body),
        "sha256": sha256 if sha256 is not None else sha256_bytes(body),
        "path": str(path),
        "received_at": received_at,
    }


def _write_index(root: Path, rows: list[dict]) -> Path:
    """手工写一份索引。**不经过捕获服务** —— 见模块 docstring。"""
    root.mkdir(parents=True, exist_ok=True)
    index = root / INDEX_FILENAME
    index.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return index


def _plant(root: Path, raw: bytes, *, seq: int, name: str = "000001--planted.eml", **row) -> Path:
    """把一封原件与它的索引行一起摆好。返回原件的路径。"""
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(raw)
    _write_index(root, [_row(seq=seq, path=path, raw=raw, **row)])
    return path


@pytest.fixture
def capture(tmp_path: Path):
    """一台真的在听的捕获服务（回环、端口由内核给）。"""
    server = MailCaptureServer(tmp_path / "capture", host="127.0.0.1", port=0).start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def source(capture: MailCaptureServer) -> MailCaptureSource:
    """读那台捕获服务落下的东西。"""
    return MailCaptureSource(capture.root, sendbox=SENDBOX)


@pytest.fixture
def planted(tmp_path: Path) -> MailCaptureSource:
    """一个**手工摆出来**的捕获目录。见模块 docstring：另一些输入捕获服务造不出来。"""
    return MailCaptureSource(tmp_path / "planted", sendbox=SENDBOX)


def _deliver(server: MailCaptureServer, raw: bytes, *, mail_from: str = SENDER):
    """收下一封信 —— 走 ``ingest``，与 SMTP 前门是同一个入口。"""
    return server.ingest(raw, mail_from=mail_from, recipients=(SENDBOX,))


# ----------------------------------------------------------------------
# 一封收下来的信，读回来是什么
# ----------------------------------------------------------------------


def test_a_captured_letter_comes_back_verified(capture: MailCaptureServer, source) -> None:
    """收下来的字节、索引里的两样、与读回来的条目三者是同一件事。

    这条判据一次断四个字段，因为它们**一起**才构成「条目指向的是那份原件」：
    ``raw_ref`` 指向的文件真的在、``raw_sha256`` 是那些字节的摘要、
    ``provider_message_id`` 是索引里记的那一个、``sender`` 是地址。
    """
    received = _deliver(capture, _letter())
    batch = source.read()

    assert isinstance(batch, CorrespondenceBatch)
    assert batch.cursor_state is CursorState.ADVANCED
    assert len(batch.items) == 1, "收了一封信却读回来不是一条"
    item = batch.items[0]
    assert item.provider_message_id == received.message_id == MESSAGE_ID
    assert item.raw_sha256 == sha256_bytes(received.body_bytes)
    assert Path(item.raw_ref).is_absolute(), "raw_ref 是相对路径 —— 它会跟着读它的人解析"
    assert Path(item.raw_ref).read_bytes() == received.body_bytes
    assert item.sender == SENDER
    assert item.sendbox == SENDBOX
    assert item.received_at.tzinfo is not None
    assert REPLY_BODY.strip() in item.body


def test_a_letter_without_a_message_id_is_not_dropped(capture: MailCaptureServer, source) -> None:
    """对方没写 ``Message-ID`` 时，退到捕获服务自己的那个凭据（``seq-NNNNNN``）。

    ``CorrespondenceItem`` 要求 ``provider_message_id`` 非空（它是去重键的一半），
    所以「没有标识」这件事必须有一个答案。两种错法各有一条路：丢掉这一封（对方
    的一封信没了），或者每次重读都给它一个新标识（同一个人被回复两次）。这里取
    的是第三条 —— 捕获服务在 ``DATA`` 应答里**对同一件事用的就是那个写法**
    （``received.message_id or f"seq-{received.seq:06d}"``）。
    """
    plain = (
        b"From: buyer@example.invalid\r\n"
        b"To: outreach@marketops.example\r\n"
        b"Subject: no identifier here\r\n"
        b"\r\n"
        b"Thanks.\r\n"
    )
    received = _deliver(capture, plain)
    assert received.message_id == "", "这封信本来就不该带 Message-ID"

    batch = source.read()
    assert len(batch.items) == 1
    # 宽度复用 ``SEQ_WIDTH``（不是重写一个 6）：两处分岔的症状是「应答里说的那个
    # 凭据在事件里查不到」—— 一条不会有任何判据变红的坏法。
    assert batch.items[0].provider_message_id == f"seq-{received.seq:0{SEQ_WIDTH}d}"


def test_a_naive_timestamp_is_read_as_utc(planted: MailCaptureSource) -> None:
    """索引里的 naive ISO 串按 **UTC** 解释，不按本机时区。

    挑「本地时区」的那一个解释会让同一个库在两台机器上读出两个不同的瞬间 ——
    「这封信是什么时候到的」于是与**读它的人**有关。这条判据因此同时断两件事：
    它是带时区的，而且**没有被平移**（``09:00`` 读回来还是 ``09:00``）。
    """
    _plant(planted.root, _letter(), seq=1, received_at="2026-09-23T09:00:00")

    item = planted.read().items[0]
    assert item.received_at.tzinfo is not None, "naive 的时刻不得进入条目"
    assert item.received_at.utcoffset() == timedelta(0)
    assert item.received_at.hour == 9, (
        "naive 的串被按本机时区平移了 —— 同一个库在两台机器上会读出两个瞬间"
    )


def test_an_aware_timestamp_is_kept_as_the_same_instant(planted: MailCaptureSource) -> None:
    """带偏移量的串读回来是**同一个瞬间**（不是同一个墙上时间）。

    这一条与上一条是**一对**：只有上一条时，一个「把所有时刻都按墙上时间读」的
    实现也是绿的 —— 而它会把 ``+08:00`` 的九点读成九点 UTC（差八小时）。
    """
    _plant(planted.root, _letter(), seq=1, received_at="2026-09-23T09:00:00+08:00")

    assert planted.read().items[0].received_at == datetime(2026, 9, 23, 1, 0, tzinfo=UTC)


def test_the_body_is_the_plain_part_of_an_alternative(planted: MailCaptureSource) -> None:
    """``multipart/alternative`` 取 text/plain 那一段，不取 HTML 那一段。"""
    _plant(
        planted.root,
        _alternative_letter(plain="Tuesday works.\n", html="<p>Tuesday works.</p>"),
        seq=1,
    )
    item = planted.read().items[0]
    assert "Tuesday works." in item.body
    assert "<p>" not in item.body


def test_an_html_only_letter_falls_back_to_its_raw_html(planted: MailCaptureSource) -> None:
    """只有 HTML 时退回它的**原文**，不做 HTML→文本的处理。

    加工过的正文与原件对不上，而这一格最终的归宿是销售交接里那句「逐字保留」
    （``SalesHandoff.reply_body``）—— 一个被转过的正文会让读它的人以为对方就是
    这么写的。
    """
    _plant(planted.root, _html_only_letter(html="<p>Tuesday <b>works</b>.</p>"), seq=1)
    assert planted.read().items[0].body.strip() == "<p>Tuesday <b>works</b>.</p>"


def test_a_body_with_a_charset_nobody_knows_still_comes_back(planted: MailCaptureSource) -> None:
    """一段声明了不存在的字符集的正文仍然读得回来（替换字符，不抛）。

    一条正文解不出来不该让**整封信**读不成：原件在盘上（``raw_ref`` 指着它），
    而缺几个字比丢掉一封信便宜。
    """
    raw = (
        b"From: buyer@example.invalid\r\n"
        b"To: outreach@marketops.example\r\n"
        b"Subject: odd charset\r\n"
        b'Content-Type: text/plain; charset="nonsense-charset"\r\n'
        b"\r\n"
        b"Tuesday works.\r\n"
    )
    _plant(planted.root, raw, seq=1)
    assert "Tuesday works." in planted.read().items[0].body


# ----------------------------------------------------------------------
# 引用：原样带过来，不在这里翻译
# ----------------------------------------------------------------------


def test_in_reply_to_comes_back_verbatim_with_its_angle_brackets(
    planted: MailCaptureSource,
) -> None:
    """``In-Reply-To`` **原样**取出来（尖括号留着），不翻译成 ``send_key``。

    这一格回答的是「对方回显了哪一封」，而 ``send_key`` 是我们的去重键、对方从来看
    不到它 —— 翻译需要一次存储查询，而这一层没有存储（翻译住在
    ``application/correspondence.py::_link``）。
    """
    _plant(planted.root, _letter(In_Reply_To="<our-letter@marketops.example>"), seq=1)
    item = planted.read().items[0]
    assert item.in_reply_to == "<our-letter@marketops.example>"
    assert item.send_key == "", "这一层把引用翻译成了 send_key —— 那是 _link 的活"


def test_references_is_used_when_in_reply_to_is_missing(planted: MailCaptureSource) -> None:
    """没有 ``In-Reply-To`` 时取 ``References`` 里的**最后一个**（直接父件）。

    RFC 5322 的语义里最后一个是直接父件；取第一个的实现会把对方的回信关联到这条
    线索里**最早**的那一封 —— 而那一次发送可能是几个月前的。
    """
    _plant(
        planted.root,
        _letter(References="<first@x.invalid> <second@x.invalid> <third@x.invalid>"),
        seq=1,
    )
    assert planted.read().items[0].in_reply_to == "<third@x.invalid>"


def test_no_reference_at_all_is_an_empty_string(planted: MailCaptureSource) -> None:
    """两格都没有时是空串（不是 ``None``）：空串有一个确切的意思 —— 对方没引用。"""
    _plant(planted.root, _letter(), seq=1)
    assert planted.read().items[0].in_reply_to == ""


# ----------------------------------------------------------------------
# 结构判得出来的分类
# ----------------------------------------------------------------------


def test_a_dsn_status_decides_hard_and_soft(planted: MailCaptureSource, tmp_path: Path) -> None:
    """RFC 3464 的 ``Status:`` 字段 → 硬退 / 软退。**两者处置相反。**"""
    _plant(planted.root, _dsn(status="5.1.1"), seq=1)
    hard = planted.read().items[0]
    assert hard.kind is CorrespondenceKind.HARD_BOUNCE
    assert KIND_DISPOSITION[hard.kind].value == "suppress"

    soft_source = MailCaptureSource(tmp_path / "soft", sendbox=SENDBOX)
    _plant(soft_source.root, _dsn(status="4.2.2"), seq=1)
    soft = soft_source.read().items[0]
    assert soft.kind is CorrespondenceKind.SOFT_BOUNCE
    assert KIND_DISPOSITION[soft.kind].value == "record_only", (
        "软退信进了抑制名单 —— 一个满了的邮箱不是一个人离开了"
    )


def test_a_dsn_with_two_different_statuses_does_not_guess(planted: MailCaptureSource) -> None:
    """一封通知里混着 4.x 与 5.x 时**不给分类**（交给人读）。

    多收件人的退信里，「我们发过的**那一个**地址是哪一个」要靠人读原文，而猜错的
    方向是把一个满了的邮箱记成一个人离开了 —— 那就是一条永久抑制。
    """
    mixed = _dsn(status="5.1.1").replace(
        b"Status: 5.1.1\r\n",
        b"Status: 5.1.1\r\n\r\nFinal-Recipient: rfc822; other@example.invalid\r\n"
        b"Action: delayed\r\nStatus: 4.2.2\r\n",
    )
    _plant(planted.root, mixed, seq=1)
    assert planted.read().items[0].kind is None


def test_a_dsn_carries_the_address_that_failed_and_the_message_it_reports(
    planted: MailCaptureSource,
) -> None:
    """一份 DSN 的**被退地址**与**它报告的那一封**要读出来，而不是留在字节里。

    这两格是这封退信唯一能证明「我们发出去的那一封信投递失败了」的东西。没有它们，
    一封 ``5.1.1`` 的通知在库里只是一封「没有 ``send_key``、也没有 ``In-Reply-To``
    的来信」：前者是我们要去重用的键、从不出现在信里，后者的头字段是**人**回信时
    回显上一封的方式，收信服务器不写。于是它落进人工审核，§7 要的抑制与停跟进
    **一条都不产生** —— 而那个不存在的地址在下一批里照常被写进计划。

    断言的形状与 ``test_a_captured_letter_comes_back_verified`` 一致：**值本身**，
    不是「非空」。``Final-Recipient`` 是 ``<type>; <address>``，所以它还顺带钉住
    那个类型前缀是被切掉的（``normalise_email`` 按 ``@`` 切，切错会得到一个看着
    像地址、其实谁都不是的字符串）。
    """
    _plant(planted.root, _dsn(status="5.1.1"), seq=1)

    item = planted.read().items[0]

    assert item.bounce_recipient == "buyer@example.invalid"
    assert item.bounce_original_message_id == "<our-letter@marketops.example>"


def test_a_dsn_naming_two_recipients_says_the_address_is_not_known(
    planted: MailCaptureSource,
) -> None:
    """两个 ``Final-Recipient`` 时**不给地址** —— 挑一个的方向是把抑制写错人。

    与 ``test_a_dsn_with_two_different_statuses_does_not_guess`` 是同一条理由长在
    另一格上：那一格管分类，这一格管「抑制谁」。两者都拒绝，因为原文只有人能读。
    """
    two = _dsn(status="5.1.1").replace(
        b"Status: 5.1.1\r\n",
        b"Status: 5.1.1\r\n\r\nFinal-Recipient: rfc822; other@example.invalid\r\n"
        b"Action: failed\r\nStatus: 5.1.1\r\n",
    )
    _plant(planted.root, two, seq=1)

    item = planted.read().items[0]

    assert item.bounce_recipient == ""
    assert item.kind is CorrespondenceKind.HARD_BOUNCE, (
        "两个都是 5.x 时分类仍然确定 —— 不确定的只是「我们发过的那一个是哪一个地址」"
    )
    assert item.bounce_original_message_id == "<our-letter@marketops.example>", (
        "那一封是 singular 的，它照旧是真的：这封信确实是我们那一封的报告"
    )


def test_a_plain_reply_carries_neither_bounce_field(planted: MailCaptureSource) -> None:
    """一封人写的回信两格都是空串 —— 它们只对投递状态通知有意义。

    判据必须是**结构**而不是「正文里有没有『退信』两个字」：一封讲退信流程的人写信
    与一份真的 DSN 在这一层必须分开，否则一段正文就能把一个人推进永久抑制。
    """
    _plant(planted.root, _letter(body="Our mail server keeps bouncing your letters.\n"), seq=1)

    item = planted.read().items[0]

    assert item.bounce_recipient == ""
    assert item.bounce_original_message_id == ""
    assert item.kind is None


def test_auto_submitted_decides_an_auto_reply(planted: MailCaptureSource, tmp_path: Path) -> None:
    """RFC 3834 的 ``Auto-Submitted`` → ``AUTO_REPLY``；其余取值一律不给。

    ``auto-notified`` 是同一个 RFC 里的第三个取值，而它**不是**「回复」——
    多判一类的代价是一封人写的回信被记成机器回的（处置 ``RECORD_ONLY``：
    既不停止跟进，也不产出交接）。
    """
    for index, value in enumerate(
        ("auto-replied", "auto-generated", "auto-replied (was: something else)")
    ):
        root = tmp_path / f"auto-{index}"
        _plant(root, _letter(Auto_Submitted=value), seq=1)
        item = MailCaptureSource(root, sendbox=SENDBOX).read().items[0]
        assert item.kind is CorrespondenceKind.AUTO_REPLY, value

    for index, value in enumerate(("auto-notified", "no")):
        root = tmp_path / f"not-auto-{index}"
        _plant(root, _letter(Auto_Submitted=value), seq=1)
        item = MailCaptureSource(root, sendbox=SENDBOX).read().items[0]
        assert item.kind is None, f"{value} 不该被判成自动回复"


def test_a_plain_human_letter_has_no_kind(planted: MailCaptureSource) -> None:
    """一封普通的回信 **不给分类** —— 分类是另一个端口的活。

    一个「读不出结构就当成人写的回信」的来源，会让每一封来信都带着一条会被执行的
    处置，而那一格恰好是最贵的（``HANDOFF``）。
    """
    _plant(planted.root, _letter(body="Tuesday works for me. Shall we say 3pm?"), seq=1)
    assert planted.read().items[0].kind is None


# ----------------------------------------------------------------------
# 摘要：不一致时作废这一批，不跳过
# ----------------------------------------------------------------------


def test_a_file_whose_bytes_no_longer_match_the_index_invalidates(
    planted: MailCaptureSource,
) -> None:
    """**真的改写盘上的字节**，然后看这一批是不是被作废了。

    这是本文件里唯一一条必须碰磁盘的判据：索引说一套、字节是另一套时，
    「跳过它」是唯一不留痕迹的处置 —— 对方的一封信被丢掉，而报告上写着「这一批
    读完了」。作废的批次里**一条条目都不给**：一份索引与自己的字节都对不上时，
    它前面那些行也不再可信。
    """
    path = _plant(planted.root, _letter(), seq=1)
    original = path.read_bytes()
    path.write_bytes(original.replace(b"Re: a short note", b"Re: a short note EDITED"))
    assert path.read_bytes() != original, "测试没有真的改到盘上的字节"

    batch = planted.read()
    assert batch.cursor_state is CursorState.INVALIDATED
    assert batch.items == (), "作废的批次不得同时给条目"
    assert batch.cursor == "", "作废的批次不得给新游标"
    assert "1" in batch.note and "摘要" in batch.note, (
        f"作废的理由没有指出是哪一行、为什么：{batch.note!r}"
    )


def test_a_row_pointing_at_a_missing_file_invalidates(planted: MailCaptureSource) -> None:
    """索引里有一行指向一封不在盘上的原件 —— 也是**记录与事实对不上**。

    与摘要不一致同一条处置：跳过它等于把那一封丢掉，而丢掉之后连「它本来是什么」
    都不知道。
    """
    path = _plant(planted.root, _letter(), seq=1)
    path.unlink()

    batch = planted.read()
    assert batch.cursor_state is CursorState.INVALIDATED
    assert batch.items == ()
    assert "不在盘上" in batch.note, batch.note


def test_a_broken_index_line_invalidates_rather_than_being_skipped(
    planted: MailCaptureSource,
) -> None:
    """索引里有一行读不出来时，**它前面那条已经读到的**也不给。

    这是「不静默跳过」最容易被写成「跳过」的那一格：坏行的**前一行**是一条好条目，
    一个 ``continue`` 的实现会把它交出去、把游标推过坏行 —— 而下一批从坏行后面
    开始读，那一行永远没有人再看一眼。
    """
    good = _plant(planted.root, _letter(), seq=1)
    with (planted.root / INDEX_FILENAME).open("a", encoding="utf-8") as handle:
        handle.write("{ this is not json }\n")
    assert good.is_file()

    batch = planted.read()
    assert batch.cursor_state is CursorState.INVALIDATED
    assert batch.items == (), "坏行前面那条好条目被交出去了 —— 那正是静默跳过"
    assert batch.cursor == ""


# ----------------------------------------------------------------------
# 游标
# ----------------------------------------------------------------------


def test_a_missing_index_is_an_empty_batch(source: MailCaptureSource) -> None:
    """索引不在 ⇒ ``items`` 为空、游标**不动**、状态 ``ADVANCED``。

    逐字来自 ``mail_capture.read_index`` 的 docstring：「还没有收到任何东西」是一个
    正常的读取结果。一个把「还没收到信」读成异常的来源，会让每一次部署的第一轮
    tick 都以失败告终。
    """
    assert not (source.root / INDEX_FILENAME).exists()
    batch = source.read()
    assert batch.items == ()
    assert batch.cursor == "", "空批次不该把游标推到某个位置"
    assert batch.cursor_state is CursorState.ADVANCED, (
        "「还没有来信」与「这个游标作废了」是两件事，处置完全不同"
    )


def test_the_cursor_only_advances_over_what_was_read(capture: MailCaptureServer, source) -> None:
    """游标是**读到的那一批里最大的 seq**，读不到东西时原地不动。"""
    first = _deliver(capture, _letter(message_id="<reply-0001@example.invalid>"))
    second = _deliver(
        capture, _letter(body="A second note.\n", message_id="<reply-0002@example.invalid>")
    )

    whole = source.read()
    assert [item.provider_message_id for item in whole.items] == [
        first.message_id,
        second.message_id,
    ]
    assert whole.cursor == str(second.seq)
    assert whole.from_cursor == ""

    nothing = source.read(cursor=whole.cursor)
    assert nothing.items == ()
    assert nothing.cursor == whole.cursor, "空批次把游标推走了（或者推回了 0）"
    assert nothing.from_cursor == whole.cursor

    only_second = source.read(cursor=str(first.seq))
    assert [item.provider_message_id for item in only_second.items] == [second.message_id]
    assert only_second.from_cursor == str(first.seq)


def test_a_cursor_beyond_the_end_is_an_empty_batch_not_an_error(
    capture: MailCaptureServer, source
) -> None:
    """游标比索引里最大的还大是合法的 —— 那是「这一段没有来信」。"""
    _deliver(capture, _letter())
    batch = source.read(cursor="9999")
    assert batch.items == ()
    assert batch.cursor_state is CursorState.ADVANCED


def test_a_cursor_that_is_not_a_seq_is_refused(source: MailCaptureSource) -> None:
    """游标形状不对时**抛**，不读成「从头开始」。

    宽容地读成从头读，会让「游标被写坏了」与「这一段没有来信」在回执上长得一样
    —— 而前者会让同一批信被反复重读。
    """
    with pytest.raises(ValueError) as caught:
        source.read(cursor="cursor-0002")
    assert "seq" in str(caught.value)
    with pytest.raises(ValueError):
        source.read(cursor="-1")


# ----------------------------------------------------------------------
# 两个层次各管一半：去重不在这里
# ----------------------------------------------------------------------


def test_the_store_recognises_a_replayed_batch_not_this_layer(
    capture: MailCaptureServer, source: MailCaptureSource
) -> None:
    """**同一批读两次**：来源给出同样的条目，而「不重复」由存储层认出来。

    ``application/correspondence.py`` 用 ``(sendbox, provider_message_id)`` 去重，
    并且**认得出来重放**（回执里那一格叫 ``replayed``）。所以这一层的游标可以放心
    前推：它回答的只是「读到哪里了」，不回答「落库了没有」—— 一份「读到了但还没
    落库」的影子台账住在这里的话，它与真库漂了不会有任何执行器会响。

    这条判据把两半**连起来**看：读两次、收两次，库里只有一条事件、一条交接都没有
    （正文是一句普通的回信 → 分类器弃权 → 人工审核），而第二次的回执说
    ``replayed``。
    """
    _deliver(capture, _letter())
    store = InMemoryOutreachStore()
    classifier = RuleBasedReplyClassifier()

    def once():
        batch = source.read()
        receipt = ingest(
            batch,
            store=store,
            classifier=classifier,
            workspace=WORKSPACE,
            at=utc_now() + timedelta(seconds=1),
        )
        return batch, receipt

    batch, first = once()
    again, second = once()

    assert [item.provider_message_id for item in again.items] == [
        item.provider_message_id for item in batch.items
    ], "这一层自己做了去重 —— 那么「落库了没有」就有了两个答案"
    assert first.ingested[0].replayed is False
    assert second.ingested[0].replayed is True, "存储层没有认出这是同一份原件"
    assert len(store.events(workspace=WORKSPACE)) == 1, (
        "同一份原件在库里落成了两条事件 —— 去重键没有生效，而这一层不该替它兜住"
    )
    assert second.ingested[0].event_id == first.ingested[0].event_id
    assert store.handoffs(workspace=WORKSPACE) == ()


# ----------------------------------------------------------------------
# 形状与边界
# ----------------------------------------------------------------------


def test_a_sendbox_that_is_not_normalised_is_refused_at_construction(tmp_path: Path) -> None:
    """构造时就抛 —— 不是第一次读的时候抛。

    去重键是 ``(sendbox, provider_message_id)``：两个写法不同的同一个信箱会让同一
    封信去重不掉，而症状是那个人收到两次回复。在构造时抛是接线时的一次失败，
    在读的时候抛是**读到一半**的失败（那时前面几条已经进了这一批）。
    """
    with pytest.raises(ValueError) as caught:
        MailCaptureSource(tmp_path, sendbox="Outreach@MarketOps.Example")
    assert "规范化" in str(caught.value)
    with pytest.raises(ValueError):
        MailCaptureSource(tmp_path, sendbox="")


def test_the_source_reads_and_never_writes(capture: MailCaptureServer, source) -> None:
    """读一批**不改变盘上的任何东西** —— 索引与原件逐字节不变。"""
    _deliver(capture, _letter())
    index = capture.root / INDEX_FILENAME
    before_index = index.read_bytes()
    before_files = sorted(path.name for path in capture.root.iterdir())

    source.read()

    assert index.read_bytes() == before_index
    assert sorted(path.name for path in capture.root.iterdir()) == before_files


def test_read_index_and_this_source_agree_on_what_is_on_disk(
    capture: MailCaptureServer, source: MailCaptureSource
) -> None:
    """这一层读的是 ``read_index`` 的结论，不是它自己的第二遍解析。"""
    _deliver(capture, _letter())
    on_disk = read_index(capture.root)
    items = source.read().items
    assert [item.provider_message_id for item in items] == [row.message_id for row in on_disk]
    assert [item.raw_sha256 for item in items] == [row.sha256 for row in on_disk]


def test_the_two_new_adapters_import_no_vendor_themselves() -> None:
    """**不联网、不引第三方库** —— 由本判据执行，不是靠自觉。

    两个新模块只准 import 标准库里那几个纯计算的模块与本仓的模块。这条判据不重复
    ``test_adapters_hold_the_vendors.py``（那一条问的是「谁 import 得到厂商」），
    它问的是**这两个文件自己**有没有把一条隐式的路接上：一个 ``import socket``
    在这里会让「读一批」变成一次可能有远端参与的动作，而它在测试里看不出来。
    """
    root = Path(__file__).resolve().parents[1]
    allowed_stdlib = {
        "dataclasses",
        "datetime",
        "email",
        "json",
        "pathlib",
        "re",
        "typing",
    }
    for name in ("mail_capture_source", "reply_classifier"):
        tree = ast.parse((root / "marketops" / "adapters" / f"{name}.py").read_text("utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        outside = sorted(imported - allowed_stdlib - {"__future__"})
        assert not outside, f"{name}.py import 了本判据不认识的东西：{outside}"

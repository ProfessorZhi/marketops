"""``TextPdfParser`` 的判据：**正文抽取**这一半（债表第 3 条）。

## 为什么这个文件此前不存在

``tests/test_parsers_report_links.py`` 的头一段写着「HTML 抽取这一组**不**用替身……
这里写进文件、读回来，走的是真的那条路」。**PDF 那半走的不是那条路**：当时与
``TextPdfParser`` 同形的唯一对象是 ``tests/test_mcp_research_tools.py`` 里的一个替身
parser，而它测的是服务面回执怎么读 ``extracts_links``，不是 pypdf。于是
``TextPdfParser.parse`` 里的每一个分支（空白页、扫描件阈值、无页面、非 PDF 字节）
都没人跑过。

这不只是一条覆盖率的欠账。它是本仓**唯一一处决定「这份 PDF 该不该交给 MinerU」**
的地方，而判错的两个方向都有代价：把真扫描件读成「这个站点没有邮箱」会污染 §10 的
net-new C/D 指标；把一份正常文档推进 MinerU 会去下几百 MB 的权重。

## 样本是**真的 PDF 字节**，但它**不是**一份真实站点的 PDF

两句话都得说，混起来会得出一个错的结论：

* 写进盘上的是**真的 PDF** —— 真的对象字典、真的 xref 表、真的内容流，pypdf 按它
  自己的读法读。没有一处 monkeypatch，没有替身。
* 但它是**手写的合成样本**，只覆盖 ASCII 正文 + 标准 Type1 字体 + 未压缩内容流。
  真实站点的 PDF 有压缩流、嵌入子集字体、CID 编码、多栏排版。所以它能证明「这条
  代码路径怎么走」，**不能**证明「真实世界的 PDF 都读得对」。

由此有一条**明确不被授权**的事：这**不**构成 ``TextPdfParser`` docstring 里那句
「本构建没有一份真实的 PDF 样本去校准那条读法」所说的样本。PDF 的链接住在
``/Annots`` 的 URI action 里，那条读法要的正是真实站点的编码多样性 —— 下面没有一条
判据碰它，``extracts_links`` 照旧是 ``False``。

## 一个从 40 开始的阈值

``SCANNED_PAGE_TEXT_THRESHOLD`` 是「这一页有没有可用文本」的判据，所以下面每一个
「有文本的页」都**必须真的超过 40 个字符**：短于它的页会被算成空白页，而那时红的是
「阈值坏了」的断言，实际上坏的是样本。写这一组时第一版样本就踩了它 ——
33 个字符的一页被算成空白，`BODY_A` 因此是刻意写长的。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from marketops.adapters.text_pdf import (
    SCANNED_PAGE_RATIO,
    SCANNED_PAGE_TEXT_THRESHOLD,
    TextPdfParser,
)
from marketops.domain.enums import SourceKind
from marketops.domain.errors import ParseError

#: 一段**足够长**的正文（> ``SCANNED_PAGE_TEXT_THRESHOLD`` 个字符，见模块 docstring）。
BODY_A = "Contact the grounds team about the natural grass pitch maintenance contract."
BODY_B = "Bookings and fixtures are handled by the club secretary, not the grounds team."


def text_page(body: str) -> str:
    """一页的内容流：一个 ``Tj`` 文本对象。

    ``body`` 里的括号与反斜杠会被转义 —— PDF 字符串的定界符就是圆括号，一个没转义
    的 ``(`` 会让整页读不出来，而那种失败看起来像「解析器坏了」。
    """
    escaped = body.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"


#: 一页没有任何文字对象。pypdf 对它的 ``extract_text()`` 是空串。
BLANK_PAGE = " "


def _pdf(*pages: str, title: str = "") -> bytes:
    """把每页的内容流拼成一份最小可读的 PDF 字节串。

    手写而不是引一个生成库：本仓的运行依赖只有五个（``pyproject.toml``），
    为了测试再引一个 PDF 写作库，会让「这份样本是什么」变成一个需要装依赖才能
    复核的问题。这里的对象表与 xref 表在几十行之内，读的人可以自己数。

    ``pages`` 为空时给出一个 ``/Count 0`` 的目录 —— 那是「没有页面」那条分支的输入。

    ``title`` 非空时给这份 PDF 加一个带 ``/Title`` 的 ``/Info`` 字典，用来问
    「``TextPdfParser`` 会不会去读它」（答案是不读，判据在
    ``test_a_pdf_info_title_is_not_read_as_the_documents_name``）。
    """
    page_numbers = [3 + index for index in range(len(pages))]
    font_number = 3 + len(pages)
    content_numbers = [font_number + 1 + index for index in range(len(pages))]

    body = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}

    def emit(number: int, payload: bytes) -> None:
        offsets[number] = len(body)
        body.extend(b"%d 0 obj\n" % number + payload + b"\nendobj\n")

    emit(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = b" ".join(b"%d 0 R" % number for number in page_numbers)
    emit(2, b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(pages)))
    for index, _ in enumerate(pages):
        emit(
            page_numbers[index],
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
            b"<< /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (font_number, content_numbers[index]),
        )
    emit(font_number, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, page in enumerate(pages):
        raw = page.encode("latin-1")
        emit(content_numbers[index], b"<< /Length %d >>\nstream\n" % len(raw) + raw + b"\nendstream")

    info_reference = b""
    if title:
        info_number = font_number + len(pages) + 1
        escaped = title.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        emit(info_number, b"<< /Title (%s) >>" % escaped.encode("latin-1"))
        info_reference = b" /Info %d 0 R" % info_number

    xref_offset = len(body)
    total = max(offsets) + 1
    body.extend(b"xref\n0 %d\n" % total + b"0000000000 65535 f \n")
    for number in range(1, total):
        body.extend(b"%010d 00000 n \n" % offsets[number])
    body.extend(
        b"trailer\n<< /Size %d /Root 1 0 R%s >>\nstartxref\n%d\n%%%%EOF\n"
        % (total, info_reference, xref_offset)
    )
    return bytes(body)


def _write(tmp_path: Path, data: bytes, name: str = "document.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- 走通的那条路 -------------------------------------------------------------


def test_a_text_pdf_yields_its_text_and_a_digest_of_the_file(tmp_path: Path) -> None:
    """一份能读的 PDF：正文、身份字段、以及**文件字节**的摘要。

    ``sha256`` 那一条是有方向的：摘要必须是**文件**的，不是抽出来的文本的。
    ``ParsedDocument`` 的 docstring 说它「指向文件字节，让证据可以回溯到具体的
    镜像文件」—— 而「同一段文字来自两份不同的 PDF」是两件事。所以这里拿
    ``hashlib`` 自己算，不调本仓那个 helper：被断言的是这条约束，不是那个函数的
    实现。
    """
    data = _pdf(text_page(BODY_A))
    doc = TextPdfParser().parse(_write(tmp_path, data))

    assert doc.text == BODY_A
    assert doc.parser == "pypdf"
    assert doc.source_kind is SourceKind.FETCHED_PDF
    assert doc.media_type == "application/pdf"
    assert doc.page_count == 1
    assert doc.sha256 == hashlib.sha256(data).hexdigest()
    assert doc.sha256 != hashlib.sha256(BODY_A.encode()).hexdigest()
    # 一页干净的 PDF 不带 warning。**恒非空**的 warning 是噪音，而噪音的下场是
    # 真正那条「结果可能不完整」也一起没人看 —— 与恒真的健康旗同一类东西。
    assert doc.warnings == ()


def test_pages_are_joined_in_document_order(tmp_path: Path) -> None:
    """多页按**文档顺序**拼，页与页之间留一个空行。

    空行不是排版偏好：literal extraction 在全文上跑，两页之间不留边界会让
    「上一页最后一行 + 下一页第一行」被读成一个连续的片段。
    """
    doc = TextPdfParser().parse(_write(tmp_path, _pdf(text_page(BODY_A), text_page(BODY_B))))

    assert doc.page_count == 2
    assert doc.text == f"{BODY_A}\n\n{BODY_B}"


# --- 两个阈值：一条拒绝扫描件，一条只发警告 -----------------------------------


def test_a_page_shorter_than_the_threshold_counts_as_blank(tmp_path: Path) -> None:
    """阈值是**证据**判据，不是排版判据：短于它的页算空白页。

    两侧各断言一次。只测「短的那页触发警告」会被一个「凡有文字就算有文本」的实现
    满足 —— 而那个实现会让真扫描件里夹着的一行页码把整份文档放行。

    39 与 41 是刻意的：``< 40`` 是空白的定义，所以边界的那一个字符落在哪一侧，
    由这一条钉住。
    """
    short = SCANNED_PAGE_TEXT_THRESHOLD - 1
    long = SCANNED_PAGE_TEXT_THRESHOLD + 1

    counted_blank = TextPdfParser().parse(
        _write(tmp_path, _pdf(text_page("x" * long), text_page("y" * short)), name="short.pdf")
    )
    assert counted_blank.warnings, "短页没有被算成空白页 —— 扫描件检测会从这里漏"
    assert "1/2" in counted_blank.warnings[0]

    not_blank = TextPdfParser().parse(
        _write(tmp_path, _pdf(text_page("x" * long), text_page("z" * short + "zz")), name="long.pdf")
    )
    assert not_blank.warnings == (), "刚好过线的一页被算成了空白页"


def test_the_ratio_decides_between_warning_and_handing_it_to_mineru(tmp_path: Path) -> None:
    """同一个「一半空白」，2/4 收下并警告，3/4 拒绝交给 MinerU。

    钉的是 ``SCANNED_PAGE_RATIO`` 这个**取舍**，两个方向都有代价：调高会放过真的
    扫描件（污染 §10 的 net-new C/D），调低会让一份一半页是插图的正常文档天天走
    MinerU（那条路要下模型权重）。只断言一侧的实现，另一侧的改动就只是「测试还绿」。
    """
    accepted = TextPdfParser().parse(
        _write(
            tmp_path,
            _pdf(text_page(BODY_A), BLANK_PAGE, text_page(BODY_B), BLANK_PAGE),
            name="half.pdf",
        )
    )
    assert accepted.page_count == 4
    assert accepted.text.strip(), "收下了却没有正文 —— 那这条「收下」没有意义"
    assert "2/4" in accepted.warnings[0]

    refused = _write(
        tmp_path, _pdf(text_page(BODY_A), BLANK_PAGE, BLANK_PAGE, BLANK_PAGE), name="most.pdf"
    )
    with pytest.raises(ParseError) as refusal:
        TextPdfParser().parse(refused)
    # 断言的是**这一支**被走到，不是「报文里出现了某个词」：两条拒绝分支的报文里
    # 都有「疑似扫描件」，分得开它们的是下半句。只查那个词的话，把分支写反、
    # 或者把两条合成一条，这一条照样绿。
    assert "应交给 MinerU fallback" in str(refusal.value)
    assert "3/4" in str(refusal.value)
    assert SCANNED_PAGE_RATIO > 2 / 4, "阈值如果落到 2/4 以下，上面那条「收下」就该红了"


def test_a_pdf_with_no_text_at_all_is_refused_by_the_other_branch(tmp_path: Path) -> None:
    """一页文本都没有：走的是**另一条**拒绝分支。

    两条分支的处置不同 —— 「全是空白页」是这份文档本身的问题，「大部分空白」是
    「也许 MinerU 还能救」。合成一条会让审计读不出是哪一种，而这两句话对应两种
    不同的下一步。
    """
    with pytest.raises(ParseError) as refusal:
        TextPdfParser().parse(_write(tmp_path, _pdf(BLANK_PAGE, BLANK_PAGE)))

    assert "全部空白" in str(refusal.value)
    assert "应交给 MinerU fallback" not in str(refusal.value)


# --- 两条失败路：它们必须**变成域错误** ----------------------------------------


def test_a_pdf_with_no_pages_is_refused(tmp_path: Path) -> None:
    """``/Count 0`` 的目录：拒绝，并说清是「没有页面」而不是「没有文本」。"""
    with pytest.raises(ParseError, match="没有页面"):
        TextPdfParser().parse(_write(tmp_path, _pdf()))


def test_a_file_that_is_not_a_pdf_becomes_a_domain_error(tmp_path: Path) -> None:
    """**不要让 pypdf 的异常类型漏出去。**

    调用方（``document_router``）只认 ``ParseError``。一个漏出来的 ``PdfStreamError``
    会穿过 router 的 except 冒到 pipeline 顶层，而它长得像「解析器崩了」，处置是
    改代码；实际情形是「这次抓回来的东西不是 PDF」，处置是换 parser 或记一笔
    blocked。这条判据钉的就是那次翻译。
    """
    path = _write(tmp_path, b"this is not a pdf at all", name="bad.pdf")

    with pytest.raises(ParseError, match="PDF 解析失败") as refusal:
        TextPdfParser().parse(path)
    assert path.name in str(refusal.value), "报文里没有文件名 —— 多份 PDF 一起失败时读不出是哪一份"


def test_can_parse_answers_by_suffix_and_by_content_type() -> None:
    """两条入口都能认 PDF，且大小写不敏感（抓回来的扩展名不总是小写）。"""
    parser = TextPdfParser()

    assert parser.can_parse(Path("document.pdf")) is True
    assert parser.can_parse(Path("DOCUMENT.PDF")) is True
    assert parser.can_parse(Path("no-extension"), content_type="application/pdf") is True
    assert parser.can_parse(Path("page.html")) is False
    # ``content_type`` 优先于扩展名：一份名字里没有 .pdf 但服务端声明了 PDF 的
    # 响应是常见的（重写过的 URL）。
    assert parser.can_parse(Path("download.aspx"), content_type="application/pdf") is True


# --- 「这份材料给自己写下的名字」这一格 ---------------------------------------------------


def test_a_pdf_with_no_info_title_says_so_with_an_empty_string(tmp_path: Path) -> None:
    """一份**没有** ``/Title`` 的 PDF ⇒ ``""``。

    这一条是下面那条的对照。只写下面那条时，「恒返回 ``""``」这个实现同样成立，
    而它混淆的是两件处置相反的事：这份材料没给自己写名字 vs 没人去看过。
    """
    doc = TextPdfParser().parse(_write(tmp_path, _pdf(text_page(BODY_A))))

    assert doc.title == ""


def test_a_pdf_info_title_is_not_read_as_the_documents_name(tmp_path: Path) -> None:
    """**``/Title`` 不是这一页给自己的名字。** 所以带着它的 PDF 也读成 ``""``。

    这份样本里的 ``/Title`` 就是本仓拒读它的那个典型：写作工具留下的字符串。
    真站点上传的 PDF 十份里有九份的 ``/Title`` 长这样，而其中没有一份是「这一页
    声明自己叫什么」。

    要放开这一格，先有**真实站点**的 PDF 样本、再写一条判据说清楚哪些 ``/Title``
    算数（``tests/test_pdf_parser.py`` 头一段已声明这里的合成样本**不是**那种样本）。
    在这条判据转红之前，改动它的人会先读到这里。
    """
    data = _pdf(text_page(BODY_A), title="Microsoft Word - pitch contract v3 final(2).docx")
    doc = TextPdfParser().parse(_write(tmp_path, data))

    assert doc.title == "", "PDF 的 /Title 是工具痕迹，不是指称对象"
    assert "Microsoft Word" not in doc.text, "它也不该被当作正文的一部分"
    assert doc.text == BODY_A, "正文照旧抽出来（正控）"

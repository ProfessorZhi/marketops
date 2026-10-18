"""``MCP_SERVICE.md`` §5 的「evidence/result ingestion」这一格。

## 这条判据挡的是哪一次事故

第一次真实 research run（kent-001）里，Serper 成功了、HTTP fetch 成功了、镜像
字节也落盘了，而证据库里一行都没有。后果不是「少了一条记录」—— 是 worker 手上
**没有任何可以引用的 id**：``submit_findings`` 的 ``evidence_refs`` 必须非空
（``domain/findings.py``），它一条也填不出来，于是正确地以 ``blocked`` 收场
而不是伪造一个引用。

所以这个文件的第一条判据是「抓到的材料变成一行拿得到 id 的证据」，其余的判据
围着它守边界。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from marketops.application.evidence_ingest import (
    MAX_LITERAL_CHARS,
    MAX_TITLE_CHARS,
    TITLE_LOCATOR,
    WHOLE_DOCUMENT_LOCATOR,
    document_evidence,
    title_evidence,
)
from marketops.domain.enums import ContactField, SourceKind
from marketops.ports.documents import DocumentLink, ParsedDocument
from tests.conftest import DEFAULT_PROFILE

FETCHED_AT = datetime(2026, 9, 22, 10, 30, tzinfo=timezone.utc)
URL = "https://example.co.uk/grounds"
#: 这一批构造用的工作区 = 测试用的那个 profile。这两个入口把它当**参数**收下，
#: 不从环境里读：「一份解析好的文档」不是一个血统声明，而这两个函数是唯一知道
#: 这份文档是在替谁读的地方。
WORKSPACE = DEFAULT_PROFILE


def _document(text: str = "Grounds maintenance is handled in house.", **overrides):
    payload = {
        "source_path": Path("/tmp/mirror/page.html"),
        "sha256": "abc123",
        "media_type": "text/html",
        "parser": "html",
        "title": "Example Grounds Trust",
        "text": text,
        "links": (DocumentLink(url="/contact", text="Contact"),),
        "extracts_links": True,
        "source_kind": SourceKind.FETCHED_HTML,
    }
    payload.update(overrides)
    return ParsedDocument(**payload)


def _evidence(**overrides):
    payload = {
        "workspace": WORKSPACE,
        "account_id": "kent-001",
        "source_url": URL,
        "document": _document(),
        "fetched_at": FETCHED_AT,
        "http_status": 200,
        "tier": "http",
    }
    payload.update(overrides)
    return document_evidence(**payload)


def _title_evidence(**overrides):
    payload = {
        "workspace": WORKSPACE,
        "account_id": "kent-001",
        "source_url": URL,
        "document": _document(),
        "fetched_at": FETCHED_AT,
        "http_status": 200,
        "tier": "http",
    }
    payload.update(overrides)
    return title_evidence(**payload)


# --- 这一格本身 ---------------------------------------------------------------


def test_a_parsed_document_becomes_a_row_a_finding_can_cite() -> None:
    """**这是 P0-2 的那一条。** 抓到的材料有了一行、有了 id、字段来自材料本身。

    ``field is None`` 是刻意的，不是漏填：``ContactField`` 的 docstring 写的是
    「**Contact 上**可以被证据支持的最小字段集」，而这一行不支撑任何 Contact
    字段 —— 它支撑的是「我读到过这一页」。填一个字段进去会让它出现在绑定路径
    的候选里，而绑定判据只看 ``bind_from_text`` 自己抽出来的那些行。
    """
    evidence = _evidence()

    assert evidence is not None
    assert evidence.evidence_id
    assert evidence.field is None
    assert evidence.account_id == "kent-001"
    assert evidence.source_url == URL
    assert evidence.source_kind is SourceKind.FETCHED_HTML
    assert evidence.content_sha256 == "abc123"
    assert evidence.parser == "html"
    assert evidence.fetched_at == FETCHED_AT
    assert evidence.http_status == 200
    assert evidence.tier == "http"


def test_the_literal_is_actually_in_the_source_text() -> None:
    """``literal_value`` 的不变量是「必须是来源文本里的**字面**片段」。

    整段正文是唯一一个不需要任何约定就满足它的取值 —— 挑「标题」或「第一段」
    都要先立一条本仓自己的规矩，而那条规矩在源文档里没有对应物。这条判据把
    「挑出来的恰好是子串」这件事钉住，于是任何一次「顺手截一下、补一句说明」
    的改动都会在这里红。
    """
    document = _document()
    evidence = _evidence(document=document)

    assert evidence is not None
    assert evidence.literal_value in document.text


def test_the_literal_carries_no_surrounding_whitespace() -> None:
    """模型层的校验要求首尾无空白（否则无法与来源做字面比对）。"""
    document = _document("\n\n   Grounds maintenance is handled in house.\n\n\t ")
    evidence = _evidence(document=document)

    assert evidence is not None
    assert evidence.literal_value == evidence.literal_value.strip()
    assert evidence.literal_value
    assert evidence.literal_value in document.text


@pytest.mark.parametrize("text", ["", "   ", "\n\t  \n"])
def test_a_document_with_no_text_yields_none_and_the_caller_must_say_so(text: str) -> None:
    """空文档**不是**一条空证据 —— 它是「这一行没有可引用的东西」。

    返回 ``None`` 而不是造一条 ``literal_value=""`` 的行：``Evidence`` 的校验
    会拒绝后者（「空证据不是证据」），而放宽那条校验去容纳它是把「我读过一页」
    当成结论的依据 —— §4 明令禁止的那种推理。

    ``None`` 让调用方**必须**表态（回执里说清这一页没落成证据），而一条空证据
    会让它在报告里看起来与一条真的完全一样。
    """
    assert _evidence(document=_document(text)) is None


# --- 截断 ---------------------------------------------------------------------


def test_a_long_document_is_truncated_and_the_truncation_is_visible() -> None:
    """§11：不得静默截断。截到 ``MAX_LITERAL_CHARS``，而**截没截**要写在记录里。

    写进 ``page_locator`` 而不是 ``context``：``context`` 的语义是「命中处的
    周围文本」，而整篇文档的那一行**没有**周围文本 —— 往里填一句解释就是把
    「供人工复核的原文」这一格当成备注栏用，读的人不会再信它。
    """
    text = "A" * (MAX_LITERAL_CHARS + 500)
    evidence = _evidence(document=_document(text))

    assert evidence is not None
    assert len(evidence.literal_value) == MAX_LITERAL_CHARS
    assert evidence.literal_value in text
    assert evidence.page_locator != WHOLE_DOCUMENT_LOCATOR
    assert str(len(text)) in evidence.page_locator
    assert str(MAX_LITERAL_CHARS) in evidence.page_locator


def test_an_untruncated_document_says_where_the_row_points() -> None:
    """没截断时位置就是「整篇」—— 与截断的那一形态**分得开**。"""
    evidence = _evidence()

    assert evidence is not None
    assert evidence.page_locator == WHOLE_DOCUMENT_LOCATOR


def test_the_receipt_bound_on_one_page_of_text_is_the_same_everywhere() -> None:
    """同一页正文在**两个回执**里的上界必须一致。

    比的是 ``research.py`` 的 ``text_preview``（抓取回执里的正文预览）与
    ``tools.py`` 的 ``_evidence_item``（证据回执里的 ``literal_value`` 预览）：
    两者引用的是**同一段正文**。上界漂开之后，调用方会把小的那个读成「全部」、
    大的那个读成「节选」—— 而两份都是截断，两份都不是全部。

    这里**不再**比 ``MAX_LITERAL_CHARS``。它原来是回执上界，于是它也是那个必须
    一致的数字；现在它是**存储**上界（只防病态页面，见它的 docstring），与回执
    无关 —— 两条判据比的东西不同，所以这一条换了主语而不是换了个数字。

    做成判据而不是让 ``application/`` 去 import ``adapters/`` 里的常量，是因为那
    会让分层反向（``adapters/`` import ``interfaces/`` 才是本仓反复写下要避免的
    形状，这一条是它的镜像）。
    """
    from marketops.adapters.mcp_server.research import MAX_PREVIEW_CHARS
    from marketops.adapters.mcp_server.tools import MAX_LITERAL_PREVIEW_CHARS

    assert MAX_LITERAL_PREVIEW_CHARS == MAX_PREVIEW_CHARS

    # 而存储那一格必须**不小于**回执那一格：小于的话，绑定层拿到的材料比回执里
    # 能看到的还少，而报告上只会显示「这轮的路线少了一些」。
    assert MAX_LITERAL_CHARS >= MAX_PREVIEW_CHARS


def test_truncation_does_not_leave_a_trailing_space() -> None:
    """在空格处切开会给 ``literal_value`` 留一个尾空白 —— 而那会被模型层拒绝。

    这条判据挡的是「切片之后忘了 ``rstrip``」：它只在正文的第 N 个字符恰好是
    空白时才会发生，而那时候报出来的是模型层的一句 ``literal_value 首尾不得有
    空白``，看起来像数据有问题。
    """
    text = "A" * (MAX_LITERAL_CHARS - 1) + "   " + "B" * 50
    evidence = _evidence(document=_document(text))

    assert evidence is not None
    assert evidence.literal_value == evidence.literal_value.strip()


# --- 身份 ---------------------------------------------------------------------


def test_the_same_document_on_the_same_account_yields_the_same_row() -> None:
    """同一页重复抓取是**幂等**的：id 相同，第二次写入不追加第二行。

    这正是 ``put_evidence`` 的那条边界能成立的前提 —— 它按 ``evidence_id``
    去重，所以 id 一旦不稳定，「重复抓取」就会在报告上长成「证据更多了」。
    """
    first, second = _evidence(), _evidence()

    assert first is not None and second is not None
    assert first.evidence_id == second.evidence_id


def test_two_accounts_reading_one_page_get_two_rows() -> None:
    """同一页、两个账号 → 两行。``account_id`` 进 id 的原像是承重的。

    共用一行会让第二个账号读到第一个账号的申请记录，而报告上两边都正常 ——
    §10 的 false attribution 最便宜的一种。
    """
    kent, devon = _evidence(account_id="kent-001"), _evidence(account_id="devon-001")

    assert kent is not None and devon is not None
    assert kent.evidence_id != devon.evidence_id


def test_two_pages_under_one_account_get_two_rows() -> None:
    first = _evidence(source_url="https://example.co.uk/grounds")
    second = _evidence(
        source_url="https://example.co.uk/grounds", document=_document("Another page entirely.")
    )

    assert first is not None and second is not None
    assert first.evidence_id != second.evidence_id


def test_the_row_is_binding_eligible_but_binds_nothing() -> None:
    """``binding_eligible`` 说的是**来源层级够格**，不是「这一行绑过谁」。

    抓来的 HTML 本来就在 ``BINDING_ELIGIBLE_SOURCES`` 里，所以文档级的行也是
    ``True``。把这一格读成「这一行可以被当 Contact 依据」的代码会绕过
    ``bind_from_text`` —— 而绑定判据只在那一处。两者的分界是 ``field``：绑定用
    的行一定有字段，文档级的行一定没有。
    """
    evidence = _evidence()

    assert evidence is not None
    assert evidence.binding_eligible is True
    assert evidence.field is None
    assert evidence.field is not ContactField.EMAIL


# --- §9：这一页是什么时候的 --------------------------------------------------------


def test_the_document_row_carries_the_page_publication_time() -> None:
    """抓取这条路与绑定那条路抽的是**同一份判据**。

    两个生产者（``domain/bind.py`` 与这里）各自实现一遍「这一页什么时候发的」，
    漂移的表现是同一个 ``source_url`` 下两种证据行对同一个问题给出两个答案 ——
    其中一个是 ``None``，读起来是「这一页没写」。判据只住一处：
    ``domain/extract.py`` 的 ``page_publication_time``。
    """
    document = _document("Published: 1 March 2026\n\nGrounds are maintained in house.")
    evidence = _evidence(document=document)

    assert evidence is not None
    assert evidence.source_published_at == datetime(2026, 3, 1, tzinfo=timezone.utc)


def test_a_document_without_a_publication_date_says_none() -> None:
    evidence = _evidence()

    assert evidence is not None
    assert evidence.source_published_at is None


def test_the_title_row_reads_the_publication_time_off_the_same_page() -> None:
    """标题那一行与正文那一行同属一页，所以发布时刻是同一个值。"""
    document = _document("Published: 1 March 2026", title="Grounds Team")
    evidence = _title_evidence(document=document)

    assert evidence is not None
    assert evidence.source_published_at == datetime(2026, 3, 1, tzinfo=timezone.utc)


# --- 标题那一行：材料**自己写下的**名字 ---------------------------------------------


def test_the_title_row_carries_the_name_the_material_wrote_for_itself() -> None:
    """``<title>`` 那一行：字面就是标题，位置说得清是哪一处。

    ``field is None`` 与正文那一行同一条理由 —— 它不支撑任何 ``Contact`` 字段。
    而它与正文那一行的分界不在 ``field``（两行都是 ``None``），在 ``page_locator``：
    一个是 ``document-title``，一个是 ``whole-document``。
    """
    evidence = _title_evidence()

    assert evidence is not None
    assert evidence.literal_value == "Example Grounds Trust"
    assert evidence.page_locator == TITLE_LOCATOR
    assert evidence.field is None
    assert evidence.parser == "html"
    assert evidence.source_url == URL
    assert evidence.content_sha256 == "abc123"


def test_the_title_row_and_the_document_row_read_the_same_bytes_but_not_the_same_place() -> None:
    """两行指向**同一份字节**（同一个 ``sha256``），位置不同。

    这条判据把「两行不是同一件事的两种写法」钉在字段上：合并成一行的实现会让
    ``page_locator`` 只剩一个取值，而那时「这个名字是从哪读来的」就答不出来了。
    """
    body, name = _evidence(), _title_evidence()

    assert body is not None and name is not None
    assert body.content_sha256 == name.content_sha256
    assert body.source_url == name.source_url
    assert body.page_locator != name.page_locator
    assert body.page_locator == WHOLE_DOCUMENT_LOCATOR


def test_the_title_is_not_necessarily_inside_the_body_text() -> None:
    """标题那一行的字面**不保证**出现在正文里 —— 这正是它要单独成行的原因。

    这个夹具就是那种情形：``<title>Example Grounds Trust</title>`` 落在正文抽取
    之外。所以「把标题塞进正文那一行的 ``context``」这个改法不是风格问题：正文
    那一行会被 ``MAX_LITERAL_CHARS`` 截断，截断之后标题可能根本不在它引用的那段
    字面里，而「这条指称对象读自哪里」就再也验不了了。
    """
    document = _document()

    assert document.title == "Example Grounds Trust"
    assert document.title not in document.text

    evidence = _title_evidence(document=document)

    assert evidence is not None
    assert evidence.literal_value == document.title


@pytest.mark.parametrize("title", ["", "   ", "\n\t \n"])
def test_a_document_with_no_title_yields_none(title: str) -> None:
    """没有标题 ⇒ ``None``，与正文那一行同一条纪律。

    ``None`` 让调用方**必须**表态，而一条 ``literal_value=""`` 的行会被
    ``Evidence`` 的校验拒掉（「空证据不是证据」）—— 放宽那条校验去容纳它，就是
    把「我读过一页」当成结论的依据。
    """
    assert _title_evidence(document=_document(title=title)) is None


def test_a_long_title_is_truncated_and_the_truncation_is_visible() -> None:
    """超长标题截到 ``MAX_TITLE_CHARS``，而**截没截**要写在位置里。

    挑这个界不是省空间：三百字符以上的那一格已经不是标题了，是解析出了别的
    东西（一封被塞进 ``<title>`` 的错误页正文）。与正文那一行同一条纪律 ——
    §11 不得静默截断。
    """
    title = "A" * (MAX_TITLE_CHARS + 50)
    evidence = _title_evidence(document=_document(title=title))

    assert evidence is not None
    assert len(evidence.literal_value) == MAX_TITLE_CHARS
    assert evidence.literal_value in title
    assert evidence.literal_value == evidence.literal_value.strip()
    assert evidence.page_locator != TITLE_LOCATOR
    assert str(len(title)) in evidence.page_locator
    assert str(MAX_TITLE_CHARS) in evidence.page_locator


def test_an_untitled_document_row_says_where_it_points() -> None:
    """没截断时位置就是 ``document-title`` —— 与截断的那一形态分得开。"""
    evidence = _title_evidence()

    assert evidence is not None
    assert evidence.page_locator == TITLE_LOCATOR


def test_the_two_rows_collide_when_a_page_says_nothing_but_its_name() -> None:
    """一页的正文**逐字等于**它的标题时，两行的 ``evidence_id`` 相撞。

    这不是一条「请修好它」的判据 —— 相撞是内容寻址 + 两行 ``field`` 都为 ``None``
    的**必然**结果，而处置权在调用方（``application/discovery_run.py``：只写一行）。
    这里钉的是那句 docstring 里的话确实成立，以及**相撞能被认出来**：两行的
    ``page_locator`` 不同，所以「同一个 id 配两份内容」这件事在 ``put_evidence``
    那一条边界上是可判的，而不是一个静默的覆盖。

    写成 `assert id 不同` 才会是错的：那要求 ``evidence_id`` 把位置也算进去，而
    「同一份材料、同一个位置、同一段字面 ⇒ 同一行」正是幂等性依赖的那条性质。
    """
    bare = "<html><head><title>Riverside</title></head><body>Riverside</body></html>"
    document = _document(text="Riverside", title="Riverside")
    assert bare  # 这一页在真实站点上存在，形状如上

    body, name = _evidence(document=document), _title_evidence(document=document)

    assert body is not None and name is not None
    assert body.evidence_id == name.evidence_id, "内容寻址：同样的字面就是同样的 id"
    assert body.page_locator != name.page_locator, "相撞可判 —— 位置把两行分开"

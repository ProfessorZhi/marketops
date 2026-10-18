"""``ParsedDocument.title``：**这份材料给自己写下的名字**。

## 为什么这一格值得一个文件

``application/discovery_run.py`` 拿 ``title`` 当候选行的**指称对象** —— 池子里
一行的名字就是这一页自己写下的标题。这个值一旦是「照印象编的」或「某个看起来
无害的默认值」，每一行都会带着一个**看起来像主体**的字符串，而它没有任何材料
支撑：不报错、不缺字段，只是把一个没人读过的名字写进了工件，然后逐级流进
受理、配对、报告。

所以 ``ParsedDocument.title`` **必填、没有默认值**（``ports/documents.py`` 写了
理由）。而「必填」只保证**有人写了**，不保证写的是**读自材料**的那一个 ——
本文件守的是后半句。

## 普查在 ``marketops/`` 的源码上进行，行为判据各自住在它的格式旁边

标题的产出者只有三个（HTML / PDF / MinerU），另有一处**搬运**（``document_router``
在采纳 fallback 时重建文档）。所以：

* 本文件做**普查**：AST 扫过 ``marketops/``，把每一处构造点与它写的那个表达式
  一起列出来。新增一处构造点会转红 —— 那时要回答的是「它写的标题读自哪里」。
* 行为判据按格式放：PDF 那条在 ``tests/test_pdf_parser.py``（真的 PDF 字节在
  那里），MinerU 那条在这里（它的 markdown 是替身给的），HTML 这一组在这里。

## 一条**有意**的取舍，逐字钉住

``_title_in`` 的 docstring 写了三条取舍（不解码实体、剥标签、折叠空白），三条
在这里都有判据。有意写下来的取舍与被忘掉的行为，在代码上长得一样，区别只在于
有没有一条会在它被改掉时转红的话。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from marketops.adapters import mineru_adapter
from marketops.adapters.text_pdf import HtmlDocumentParser
from marketops.adapters.mineru_adapter import MinerUDocumentParser
from marketops.application.document_router import DocumentRouter
from marketops.domain.errors import ParseError
from marketops.ports.documents import DocumentParser, ParsedDocument

REPO = Path(__file__).resolve().parents[1]

#: 一个**看起来**有标题的 HTML：``<script>`` 里还有一条假的。
#: 假的那一条与真的那一条在字节上长得一样，区别只在浏览器认不认它。
SCRIPT_TITLE_PAGE = (
    "<html><head>"
    "<script>document.write('<title>Loaded Later</title>')</script>"
    "<title>Riverside Sports Association</title>"
    "</head><body><h1>Riverside</h1><p>Grounds maintenance.</p></body></html>"
)


def _html_document(tmp_path: Path, html: str) -> ParsedDocument:
    path = tmp_path / "page.html"
    path.write_text(html, encoding="utf-8")
    return HtmlDocumentParser().parse(path)


def _html_title(tmp_path: Path, html: str) -> str:
    return _html_document(tmp_path, html).title


# ----------------------------------------------------------------------
# 普查：每一处构造点，以及它写的那个表达式
# ----------------------------------------------------------------------
#: 相对路径 → 该文件里每一处 ``ParsedDocument(...)`` 喂给 ``title`` 的**表达式**
#: （``ast.unparse`` 的写法），多处的按出现顺序。
#:
#: 这份表**不是**在数数。它把「唯一一处会产出非空标题的地方」写成了一句可执行
#: 的话：只有 ``text_pdf`` 的 HTML 那一条从一个**读自材料**的值取标题，其余三处
#: 要么是空串，要么是逐字搬运。任何一个新构造点、任何一次「顺手让它也有个名字」，
#: 都会在这里转红。
#:
#: 记的是**表达式**而不是作者当时敲的引号：``ast.unparse`` 会把 ``""`` 归一成
#: ``''``。判据要的是「这一格读自哪里」，把引号风格也钉住只会让一次无关的重排
#: 转红，然后被人顺手改掉这一行 —— 那正是判据失效的常见路径。
TITLE_EXPRESSIONS: dict[str, list[str]] = {
    "marketops/adapters/text_pdf.py": ["_title_in(without_scripts)", "''"],
    "marketops/adapters/mineru_adapter.py": ["''"],
    "marketops/application/document_router.py": ["document.title"],
}


def _construction_sites() -> dict[str, list[ast.Call]]:
    """``marketops/`` 里每一处 ``ParsedDocument(...)`` 调用，按文件分组。

    走 AST 而不是 grep：``ParsedDocument(`` 这个串也会出现在 docstring 与注释里，
    而一次被注释骗过的普查会让人去改一段没有构造点的文件。
    """
    found: dict[str, list[ast.Call]] = {}
    for path in sorted((REPO / "marketops").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ParsedDocument"
        ]
        if calls:
            found[str(path.relative_to(REPO))] = sorted(calls, key=lambda node: node.lineno)
    return found


def test_the_ast_walker_finds_the_construction_sites_at_all() -> None:
    """普查器自身的正控：它**确实**找得到东西。

    没有这一条，一次坏掉的遍历（正则换了、目录换了）会让下面两条判据在
    **空**的人口上成立 —— 而「每一处都写对了」在一个人都没有时是真话。
    """
    sites = _construction_sites()

    assert set(sites) == set(TITLE_EXPRESSIONS), (
        "构造点的名单变了：多出来的那处要回答「它写的标题读自哪里」，"
        "少掉的那处说明有人删掉了一条产出路径"
    )
    assert sum(len(calls) for calls in sites.values()) >= 4


def test_every_construction_site_writes_the_title_it_is_expected_to_write() -> None:
    """每一处写的那个表达式，逐字对上上面那张表。

    ``document_router`` 那一条是有方向的一条：它**逐字搬** ``document.title``。
    写成 ``""`` 的后果是「这份 PDF 经过 MinerU 之后没有名字了」—— 而报告上
    看不出来，因为那一行本来就可能没有名字。
    """
    for path, calls in _construction_sites().items():
        written = []
        for call in calls:
            keyword = next((kw for kw in call.keywords if kw.arg == "title"), None)
            assert keyword is not None, (
                f"{path}:{call.lineno} 没有按关键字传 ``title``。"
                "位置传参会让「谁写了这一格」在一次 grep 里查不到。"
            )
            written.append(ast.unparse(keyword.value))

        assert written == TITLE_EXPRESSIONS[path], (
            f"{path} 写给 ``title`` 的东西变了：{written}。"
            "标题只能读自材料自己写下的那一处 —— 见本文件头一段。"
        )


# ----------------------------------------------------------------------
# HTML：标题本身
# ----------------------------------------------------------------------
def test_the_title_comes_from_the_title_element_verbatim(tmp_path: Path) -> None:
    """**这是 ``discovery_run`` 那一条链的根。** 名字来自 ``<title>``，逐字。"""
    document = _html_document(
        tmp_path,
        "<html><head><title>Riverside Sports Association</title></head>"
        "<body><h1>别的什么</h1><p>正文。</p></body></html>",
    )

    assert document.title == "Riverside Sports Association"
    assert document.title != document.text.splitlines()[0], (
        "标题不是正文的第一行 —— 两者在这个夹具里刻意不同"
    )


def test_the_title_element_is_found_with_attributes_and_uppercase(tmp_path: Path) -> None:
    """``<TITLE lang="en">`` 也是标题 —— 真站点上两种写法都有。"""
    assert (
        _html_title(
            tmp_path, '<HTML><HEAD><TITLE lang="en">Club</TITLE></HEAD><BODY>x</BODY></HTML>'
        )
        == "Club"
    )


def test_a_title_written_by_script_is_not_the_title(tmp_path: Path) -> None:
    """``document.write('<title>…')`` 里的那一个不是标题。

    浏览器不把它当标题，而它在文本里与真的那一个长得完全一样 —— 所以判据要的是
    **真的那一个**，不是「文件里出现的第一个 ``<title>``」。
    """
    assert _html_title(tmp_path, SCRIPT_TITLE_PAGE) == "Riverside Sports Association"


def test_an_unclosed_title_stops_at_the_next_title_close(tmp_path: Path) -> None:
    """漏写的 ``</title>`` 停在**下一个** ``</title>``，不越过文档里已有的那一对。

    与 ``_ANCHOR`` 逐字同一条纪律。这条判据的用处不是「这个结果好」—— 它不好：
    ``Second`` 那一整段被吞进了标题。它是**有界**的不好。夹具因此**必须有两个
    已闭合的标题**：只有一个 ``</title>`` 时，贪婪与非贪婪得到同一个结果，这条
    判据就成了一个换不掉任何东西的绿（``Second`` 那一条是分辨力本身）。
    """
    title = _html_title(
        tmp_path,
        "<html><head><title>Riverside<head></head>"
        "<title>Second</title></head><body><h1>正文标题</h1></body></html>",
    )

    assert title == "Riverside Second", "吞掉的范围停在下一个 ``</title>``"
    assert "正文标题" not in title, "不越过文档里已有的那一对"


def test_a_title_with_no_closing_tag_at_all_is_empty(tmp_path: Path) -> None:
    """整篇都没有 ``</title>`` ⇒ ``""``，而不是「一路吃到文档末尾」。

    这是上一条的**另一半**，也是更要紧的那一半：有界的错让人看得见（标题里多出
    一截），无界的错看起来完全正常 —— 整篇正文被吸进标题，然后它成了一行的名字。
    """
    document = _html_document(
        tmp_path,
        "<html><head><title>Riverside</head><body><h1>正文标题</h1>"
        "<p>Grounds maintenance and bookings for the season.</p></body></html>",
    )

    assert document.title == ""
    assert "正文标题" not in document.title


def test_a_title_that_is_only_tags_is_empty_rather_than_a_strip_of_whitespace(
    tmp_path: Path,
) -> None:
    """剥完标签什么都不剩 ⇒ ``""``，而不是一串空白。

    一串空白在池子里是一条**名字是空白**的行，而 ``usable_name`` 会拒它 ——
    于是这一行无声地从池子里消失，看起来像「这一页没被取到」。
    """
    assert (
        _html_title(
            tmp_path, "<html><head><title><span></span></title></head><body>x</body></html>"
        )
        == ""
    )


def test_no_title_element_gives_the_empty_string(tmp_path: Path) -> None:
    """没有 ``<title>`` ⇒ ``""``。**不许拿别的东西顶上**（第一行、``<h1>``、主机名）。"""
    document = _html_document(
        tmp_path, "<html><head></head><body><h1>正文标题</h1><p>正文。</p></body></html>"
    )

    assert document.title == ""
    assert document.text, "正文仍然要抽出来 —— 标题空着不等于这一页读不出东西"


# ----------------------------------------------------------------------
# HTML：三条有意写下来的取舍
# ----------------------------------------------------------------------
def test_nested_tags_inside_the_title_are_stripped(tmp_path: Path) -> None:
    """取舍一：标签剥掉。

    ``<title>Foo <b>Bar</b></title>`` 不是合法 HTML，但它出现在真实站点上，而带着
    标签的名字会一路进到候选行的指称对象里。
    """
    assert (
        _html_title(
            tmp_path, "<html><head><title>Foo <b>Bar</b> Baz</title></head><body>x</body></html>"
        )
        == "Foo Bar Baz"
    )


def test_whitespace_in_the_title_is_collapsed_to_single_spaces(tmp_path: Path) -> None:
    """取舍二：空白折叠。标题里的换行是排版的产物，不是名字的一部分。"""
    title = _html_title(
        tmp_path,
        "<html><head><title>\n  Riverside\n  Sports   Association\n</title></head><body>x</body></html>",
    )

    assert title == "Riverside Sports Association"


def test_html_entities_are_left_verbatim(tmp_path: Path) -> None:
    """取舍三：**不解码**实体。

    解码要先立一条本仓自己的规矩（用哪张实体表、认不认数字实体），而那条规矩在
    源材料里没有对应物 —— 逐字留着仍然是一个可引用的字面。这条判据的理由不是
    「实体解码很难」，是**它是一次决定**：有一天真的解码了，这一条会转红，而那
    正是让人去读那段 docstring 的时机。
    """
    assert (
        _html_title(
            tmp_path, "<html><head><title>Bells &amp; Whistles</title></head><body>x</body></html>"
        )
        == "Bells &amp; Whistles"
    )


# ----------------------------------------------------------------------
# MinerU：一份 markdown 没有「这一页给自己的名字」这一格
# ----------------------------------------------------------------------
def _mineru_document(tmp_path: Path) -> ParsedDocument:
    """跑一遍 MinerU adapter 的 ``parse``，markdown 由**调用点**上的替身给出。

    替身只打在 ``_run_mineru`` 上（它本来就是为「校准调用点」单独分出来的那一格），
    ``parse`` 本身以及它构造 ``ParsedDocument`` 的那一段走的是真的那条路。
    """
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really - any bytes will do here")

    return MinerUDocumentParser(tmp_path / "mineru").parse(pdf)


def test_a_markdown_heading_is_not_the_documents_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**第一个标题行是正文的一部分，不是材料的标题。** 所以写 ``""``。

    这条判据模拟的正是「顺手补上」的那个改动：拿 markdown 的第一个 ``#`` 行当标题，
    看起来完全合理，而它产出的名字与 ``<title>`` 产出的那一类根本不是一件事 ——
    前者是模型重排正文时写下的第一个加粗句子，后者是页面自己声明自己的名字。两者
    混进同一格之后，池子里就再也分不出「谁说的」。
    """
    monkeypatch.setattr(mineru_adapter, "_mineru_do_parse", object)
    monkeypatch.setattr(
        mineru_adapter,
        "_run_mineru",
        lambda path, output_dir: "# Riverside Sports Association\n\nGrounds maintenance.",
    )

    document = _mineru_document(tmp_path)

    assert document.parser == "mineru"
    assert document.used_fallback is True, "这一份确实经过了 fallback（正控）"
    assert document.text.startswith("# Riverside Sports Association"), "正文原样搬过来"
    assert document.title == "", (
        "markdown 的第一个标题行是正文的一部分 —— 拿它当标题会把一次模型重排"
        "读成「这一页给自己的名字」"
    )


def test_a_mineru_document_reports_that_it_does_not_look_for_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一份文档里，``extracts_links`` 与 ``title`` 是两条**不同**的读数。

    这里不是重复 ``test_parsers_report_links.py``：那条判据读的是 PDF **读取失败**
    那条路径上的拒绝，这一条读的是**成功**路径。走通的这一条此前没人跑过。
    """
    monkeypatch.setattr(mineru_adapter, "_mineru_do_parse", object)
    monkeypatch.setattr(mineru_adapter, "_run_mineru", lambda path, output_dir: "Grounds.")

    document = _mineru_document(tmp_path)

    assert document.links == ()
    assert document.extracts_links is False


# ----------------------------------------------------------------------
# document_router：采纳 fallback 时标题要跟着过来
# ----------------------------------------------------------------------
class _FailingPrimary(DocumentParser):
    """能处理这份文件、但读不出来 —— 「交给 fallback」的触发条件。"""

    name = "failing-primary"
    extracts_links = False

    def can_parse(self, path: Path, *, content_type: str = "") -> bool:
        return True

    def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
        raise ParseError("扫描件，交给 fallback")


class _NamedFallback(DocumentParser):
    """一份**有名字**的 fallback 文档。今天没有这样的 parser 在跑 —— 这一条钉的
    是那个重建点的搬运属性，而不是某个现存实现的行为。"""

    name = "named-fallback"
    extracts_links = False

    def can_parse(self, path: Path, *, content_type: str = "") -> bool:
        return True

    def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
        return ParsedDocument(
            source_path=path,
            sha256="0" * 64,
            media_type="application/pdf",
            parser=self.name,
            title="Riverside Sports Association",
            text="Grounds maintenance.",
            links=(),
            extracts_links=self.extracts_links,
        )


def test_the_router_carries_the_title_across_the_fallback_rebuild(tmp_path: Path) -> None:
    """``document_router`` 重建 fallback 文档时，``title`` **逐字搬过来**。

    它重建的是「谁产的、算不算 fallback」两个字段，不是内容。漏掉这一句的后果与
    漏掉 ``links`` 那句一样，但更难看见：报告上那一行只会「没有名字」，而它本来
    就可能没有名字。今天四个构造点里只有一个会产出非空标题，所以这条判据今天
    靠一个替身成立 —— 它是**给将来那一次**准备的，而将来那一次不会有别的提示。
    """
    path = tmp_path / "document.pdf"
    path.write_bytes(b"%PDF-1.4 placeholder")

    router = DocumentRouter(parsers=[_FailingPrimary()], fallbacks=[_NamedFallback()])
    document = router.parse(path, content_type="application/pdf")

    assert document.used_fallback is True
    assert document.title == "Riverside Sports Association"
    assert document.parser == "named-fallback"

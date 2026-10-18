"""每个 parser 都得对「这份文档里有哪几条链接」表态（``ports/documents.py``）。

## 为什么这件事需要一个文件

``docs/MCP_SERVICE.md`` §5 要求每一页记录它的**链接**，§3 要求 ``crawl_site`` 的
逐页回执带链接。而这条路上有一个安静的错误形状：**空元组**。一个「没有抽链接的
parser」与「这一页真的没有链接」在报告上长得一模一样，而两者的处置相反 ——
前者要去补抽取，后者是一份关于站点的结论。

所以 ``ParsedDocument.links`` 是**必填、没有默认值**的（声明了却没人填的字段比
没有这个字段更坏），并且每个 parser 还要用一个类属性说清「我抽不抽链接」。
「每个」这两个字要有执行器：``test_every_parser_declares_whether_it_looks_for_links``
遍历所有 ``DocumentParser`` 子类，缺声明的会被点名。

## 判据为什么用真实字节

HTML 抽取这一组**不**用替身：``<a href=/bare>``（不带引号）、``<a href='x'>``
（单引号）、``<a href="">``（空目标）在正则的边界上各不相同，而「照我印象里的
HTML 长什么样」编出来的替身会把被测代码的边界挪到替身作者想到的地方去。这里写
进文件、读回来，走的是真的那条路。

## 链接**不**被解析成绝对地址

``DocumentLink.url`` 是 ``href`` 的逐字值。判据里有一条专门守着这件事：它会在
「有人顺手 ``urljoin`` 一下」时转红 —— 那样做看起来无害，但解析规则是**文档与
页面 URL 的联合函数**（同一份 ``<a href="contact">`` 在两个 URL 下指的是两条不同
的链接，还要看文档里的 ``<base href>``），而 ``parse(path, *, content_type)``
拿不到后面那一半。
"""

from __future__ import annotations

from pathlib import Path

from marketops.adapters.mineru_adapter import MinerUDocumentParser
from marketops.adapters.text_pdf import HtmlDocumentParser, TextPdfParser
from marketops.ports.documents import DocumentParser, ParsedDocument

#: 一页刻意写得难看的 HTML：四种 href 写法、一条空目标、一条只有图标没有文字的、
#: 一条重复的、一条压根没有 href 的，外加 ``<script>`` 里的一条**假链接**。
LINKS_PAGE = """<html><head><title>Club</title>
<script>document.write('<a href="/from-script">假的</a>')</script>
<style>a::after { content: "</a>"; }</style>
</head><body>
<h1>Club</h1>
<p>Contact us for details.</p>
<a href="/contact-us">Contact us</a>
<a href='https://other.example/forms'>Forms</a>
<a href=/bare-attribute>Bare</a>
<a href="">Empty target</a>
<a href="   ">Blank target</a>
<a href="/logo"><img src="/logo.png" alt="logo"></a>
<a href="/contact-us">Contact us again</a>
<a>No href at all</a>
</body></html>"""


def _html_document(tmp_path: Path, html: str = LINKS_PAGE) -> ParsedDocument:
    path = tmp_path / "page.html"
    path.write_text(html, encoding="utf-8")
    return HtmlDocumentParser().parse(path)


# ----------------------------------------------------------------------
# 每个 parser 都表态
# ----------------------------------------------------------------------
def _all_parser_classes() -> list[type[DocumentParser]]:
    """``DocumentParser`` 的**全部**子类，递归。

    ``__subclasses__()`` 只看已经 import 进来的模块，所以下面那条判据自己 import
    了三个真实实现 —— 不 import 的那条路会得到一个空的人口，而空的人口让
    「每个都表态了」变成一句关于真空的真话。
    """
    found: list[type[DocumentParser]] = []
    stack: list[type[DocumentParser]] = [DocumentParser]
    while stack:
        for child in stack.pop().__subclasses__():
            found.append(child)
            stack.append(child)
    return found


def _undeclared(classes: list[type]) -> list[str]:
    """没有在自己类体里声明 ``extracts_links`` 的那些。

    看的是 ``vars(cls)``（自己的 ``__dict__``）而不是 ``cls.extracts_links``：
    继承来的值答不了「作者有没有想过这件事」，而这两件事的差别正是本文件要守的
    东西 —— 基类的 ``False`` 与「忘了写」在属性读取上无法区分。
    """
    return [cls.__name__ for cls in classes if "extracts_links" not in vars(cls)]


def test_every_parser_declares_whether_it_looks_for_links() -> None:
    """每个 parser 子类都必须写明自己抽不抽链接，且名单要看得见。

    后半截（名单）是这条判据的**人口**：一个「谁都没表态」的检查在扫描范围缩到
    空之后同样会绿，而它当时什么都没证明。
    """
    classes = _all_parser_classes()
    names = {cls.__name__ for cls in classes}
    for expected in ("HtmlDocumentParser", "TextPdfParser", "MinerUDocumentParser"):
        assert expected in names, (
            f"{expected} 不在扫描范围里 —— 每个 parser 表态这件事，"
            "在这条判据下会变成一句关于真空的真话"
        )

    missing = _undeclared(classes)
    assert missing == [], (
        f"这些 parser 没有对「这份文档里有哪几条链接」表态：{missing}。"
        "继承来的缺省值会让「这一页没有链接」与「这个 parser 没抽」同形。"
    )


def test_the_declaration_check_catches_a_parser_that_says_nothing() -> None:
    """正对照：上面那个检查真的会红。

    用一个**普通类**而不是 ``DocumentParser`` 的子类来证明 —— 子类会被上面那条
    判据的遍历捡到，于是这个对照组自己变成一次全局失败，判据红得与它要证明的
    事情无关。这里要证明的只是 ``vars()`` 那个检查不是恒真的。
    """

    class Undeclared:
        pass

    assert _undeclared([Undeclared]) == ["Undeclared"]
    assert _undeclared([HtmlDocumentParser]) == []


# ----------------------------------------------------------------------
# HTML：逐字抽 href
# ----------------------------------------------------------------------
def test_the_html_parser_reports_the_hrefs_verbatim(tmp_path: Path) -> None:
    """四种 href 写法都抽得出来，``<script>`` / ``<style>`` 里的不算。

    最后那半句是本条判据的另一半：抽取若跑在去掉 ``<script>`` / ``<style>`` 之前
    的字节上，一条 ``document.write('<a href=...>')`` 也会成为一条链接，而它在
    真实页面上到处都是。那一条链接指向的地址**不存在**，而报告上它与别的链接
    没有区别。
    """
    document = _html_document(tmp_path)

    assert [link.url for link in document.links] == [
        "/contact-us",
        "https://other.example/forms",
        "/bare-attribute",
        "/logo",
        "/contact-us",
    ]


def test_a_script_that_writes_an_anchor_is_not_a_link(tmp_path: Path) -> None:
    """把上一条的「不算」单独写出来 —— 它是一条**排除**判据，值得有自己的名字。

    只写「抽出了这五条」时，一个把 script 也算进去、又恰好漏掉两条真链接的实现
    同样会红，红的原因却指不到 ``<script>`` 上。
    """
    document = _html_document(
        tmp_path,
        "<html><body><h1>t</h1>"
        "<script>document.write('<a href=\"/from-script\">假</a>')</script>"
        "<a href=\"/real\">真</a></body></html>",
    )

    assert [link.url for link in document.links] == ["/real"]


def test_the_anchor_text_is_kept_and_an_image_link_keeps_its_empty_text(
    tmp_path: Path,
) -> None:
    """锚文本折叠空白后留下；没有文字的图片链接**留着**，它的文本是空串。

    空文本不是「没有链接」—— 图标按钮、图片链接都是这个样子，而它指向的地址
    正是最有用的那一半。丢掉它会让这类链接从报告上整体消失。

    文本按**位置**断言而不是按 URL 做字典：同一个 href 出现两次时，字典会把
    「页眉那条链的文本」与「页脚那条链的文本」合成一个 —— 而那正是本文件要区分的
    两件事（不合并、不去重）。
    """
    document = _html_document(tmp_path)

    assert [link.text for link in document.links] == [
        "Contact us",
        "Forms",
        "Bare",
        "",
        "Contact us again",
    ]
    assert document.links[3].url == "/logo", "第 3 条正是那个只有图片的链接"


def test_an_unclosed_anchor_stops_at_the_next_closing_tag(tmp_path: Path) -> None:
    """漏了 ``</a>`` 时，锚**停在下一个 ``</a>``**，不越过文档里已有的那一对。

    这条判据的用处不是「这个结果是好的」—— 它不好：嵌套的 ``/later`` 被吞掉，
    ``/early`` 的锚文本多吃了两个字。它是**有界**的不好。若是允许「一路吃到文档里
    最后一个 ``</a>``」，同一次标签写错会把后面**所有**链接的正文吸进一条锚文本，
    而页面上其余的链接整体消失 —— 报告上剩下的那一条地址看着完全正常。

    夹具因此**必须有两个已闭合的锚**：只有一个 ``</a>`` 时，贪婪与非贪婪得到的是
    同一个结果，这条判据就成了一个换不掉任何东西的绿（``/other`` 那一条是分辨力
    本身）。三件事一起钉：吞掉的范围停在下一个 ``</a>``、后面那条链接还在、文本
    恰好是「Early」加被吞掉的「Later」。
    """
    document = _html_document(
        tmp_path,
        '<html><body><h1>t</h1><a href="/early">Early<span>s</span> '
        '<a href="/later">Later</a> then <a href="/other">Other</a></body></html>',
    )

    assert [link.url for link in document.links] == ["/early", "/other"], (
        "吞掉的范围停在下一个闭合标签 —— 页面上其余的链接不该跟着消失"
    )
    assert document.links[0].text == "Early s Later"


def test_a_duplicate_href_appears_once_per_anchor_in_document_order(
    tmp_path: Path,
) -> None:
    """同一个 href 出现在页眉与页脚 ⇒ 两条。

    这里**不去重**：本函数报的是「这份文档里的锚元素」，而合并规则一旦写错，两个
    不同的链接会被并成一个 —— 报告上什么都看不出来。（``site_crawl`` 那边合并
    候选 URL 时才需要去重，那是另一件事：候选池是一份集合。）
    """
    document = _html_document(tmp_path)
    urls = [link.url for link in document.links]

    assert urls.count("/contact-us") == 2
    assert urls.index("/contact-us") < urls.index("/logo"), "顺序是文档顺序"


def test_an_href_with_no_target_is_not_a_link(tmp_path: Path) -> None:
    """``href=""`` / ``href="   "`` / 没有 ``href`` 的 ``<a>`` 都不产出链接。

    前两者指的是**文档自己**（模板里漏填的下场），第三条没有目标。它们都不是
    「页面上的另一条链接」，收进来只会让链接清单变长而多出来的那些指向本页。
    """
    document = _html_document(tmp_path)

    assert "" not in [link.url for link in document.links]
    assert "   " not in [link.url for link in document.links]
    assert len(document.links) == 5, "页面上有 9 个 <a>，其中 5 个是真链接"


def test_the_html_parser_does_not_resolve_a_relative_href(tmp_path: Path) -> None:
    """**逐字**留下 ``href``，不解析成绝对地址。

    这条判据会在「有人顺手 ``urljoin`` 一下」时转红。那样做看起来无害，但解析
    规则是文档与页面 URL 的联合函数：``parse(path, *, content_type)`` 拿不到这份
    字节是从哪个 URL 取回来的，也看不到 ``<base href>``。逐字记下来，由同时知道
    两者的那一层去转换 —— 这也让「原文」永远不会在链路上丢掉。
    """
    path = tmp_path / "page.html"
    path.write_text('<html><body><p>x</p><base href="https://other.example/en/">'
                    '<a href="contact">Contact</a></body></html>', encoding="utf-8")

    document = HtmlDocumentParser().parse(path)

    assert [link.url for link in document.links] == ["contact"], (
        "解析器把相对地址变成了别的东西 —— 它不知道这份字节的页面 URL"
    )


def test_the_html_parser_says_it_looks_for_links(tmp_path: Path) -> None:
    """HTML 的表态是「抽」，而且**那份表态跟着文档一起出来**。

    两半都要：类属性是「作者想过这件事」的声明，``ParsedDocument.extracts_links``
    才是读回执的人手里唯一有的事实（``DocumentRouter`` 采纳 fallback 时会重建一个
    ``ParsedDocument``，那一刻解析器对象已经不在手边）。与下面那条成对：只写另一半
    时，一个全部声明为假的实现同样会让「PDF 说不抽」通过。
    """
    assert HtmlDocumentParser.extracts_links is True
    assert _html_document(tmp_path).extracts_links is True


def test_the_pdf_and_mineru_parsers_say_they_do_not_look_for_links() -> None:
    """PDF 与 MinerU 的表态是「不抽」—— 而且是**明说的**，不是缺省掉的。

    PDF 里的链接住在注释里（``/Annots`` 的 URI action），MinerU 的 markdown 里
    有 ``[文字](地址)``：两者都抽得出来，本构建都没抽。所以这里的 ``False`` 不是
    「它们没有链接」，而是「没人看过」—— 报告上必须读得出这个区别。
    """
    # 这里的 ``False`` 是**正文**抽取与链接抽取的边界：前者在
    # ``tests/test_pdf_parser.py`` 里有真的 PDF 字节做执行器，后者没有 ——
    # 那条判据不构成「链接读法有样本」的证据，这一行也不因此改口。
    assert TextPdfParser.extracts_links is False
    assert MinerUDocumentParser.extracts_links is False


def test_a_parser_that_does_not_look_for_links_says_so_on_the_document(
    tmp_path: Path,
) -> None:
    """只声明在类上不算数：**文档**上那一个才是回执读得到的。

    这条判据守的是「两处不可能各说各的」：解析器写的是
    ``extracts_links=self.extracts_links``，所以改类上那一个，文档跟着变；而一条
    手写 ``extracts_links=False`` 的构造点会在下面转红。用一个声明为「抽」但一条
    链接也没有的替身 parser 来测 —— 它同时说明这个字段与 ``links`` 是否为空**无关**。
    """

    class Claims(DocumentParser):
        name = "claims"
        extracts_links = True

        def can_parse(self, path: Path, *, content_type: str = "") -> bool:
            return True

        def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
            return ParsedDocument(
                source_path=path,
                sha256="e" * 64,
                media_type="text/html",
                parser=self.name,
                title="",
                text="正文",
                links=(),
                extracts_links=self.extracts_links,
            )

    document = Claims().parse(tmp_path / "x.html")

    assert document.links == ()
    assert document.extracts_links is True, (
        "「这一页没有链接」与「没人看过这一页」的区别就落在这个字段上"
    )

"""轻量解析器：HTML 与「文本型 PDF」。

这两者覆盖了公开网页与公司文档的绝大多数情况，且**没有重依赖**。
只有当它们明确失败（扫描件 / 复杂排版）时才考虑 MinerU —— 见 ``router``。
"""

from __future__ import annotations

import re
from pathlib import Path

from ..domain.enums import SourceKind
from ..domain.errors import ParseError
from .hashing import sha256_file
from ..ports.documents import DocumentLink, DocumentParser, ParsedDocument

_BLOCK_SEPARATORS = re.compile(
    r"</(p|div|li|tr|h1|h2|h3|h4|h5|h6|section|article|table|blockquote)>",
    re.IGNORECASE,
)
_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
#: 一个锚元素。要求有 ``</a>``：漏了闭合标签的写法于是停在**下一个** ``</a>``，
#: 而不是一路吃到文档末尾 —— 一次标签写错的后果被限制在文档自己已有的一对标签内。
#: 代价是中间那条链接会被吞掉，所以这仍然是一个错，只是**有界**的错；今天的实际
#: 行为由 ``test_an_unclosed_anchor_stops_at_the_next_closing_tag`` 钉住。
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.IGNORECASE | re.DOTALL)
#: ``<a>`` 的 href 三种写法：双引号、单引号、不带引号（HTML5 允许）。
_HREF = re.compile(r"""\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
#: 文档自己写下的标题。与 ``_ANCHOR`` 逐字同一条纪律：要求有闭合标签，于是一次
#: 漏写的 ``</title>`` 停在**下一个**闭合标签，而不是一路吃到文档末尾 —— 错的
#: 后果被限制在文档自己已有的一对标签之内。
_TITLE = re.compile(r"<title\b[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
_BLANK_LINES = re.compile(r"\n{3,}")
_INLINE_WS = re.compile(r"[ \t ]+")

#: 单页提取出的字符数低于此值，高度怀疑是扫描件。
SCANNED_PAGE_TEXT_THRESHOLD = 40
#: 判定「整份 PDF 是扫描件」的空白页占比阈值。
SCANNED_PAGE_RATIO = 0.7


class HtmlDocumentParser(DocumentParser):
    """HTML → 纯文本 + 链接。

    保留换行结构（块级标签转换为换行），因为 literal email 的上下文
    （它出现在「Contact」段落还是页脚）对人工复核很重要。

    链接也是从同一份字节里抽的，而且是**正则**抽的 —— 本模块从落地起就没有
    BeautifulSoup，而 ``<a href=…>`` 这一条的形状不值得为它引一个解析器：
    ``<script>`` / ``<style>`` 已经被清掉了，剩下的锚元素是纯文本里最好认的东西。

    「没有 BeautifulSoup」这句话今天的执行器是
    ``tests/test_declared_dependencies.py``：``bs4`` 既不在 ``pyproject`` 里、也不在
    本仓的任何一处 import 里，两个方向都查。

    它此前指向的证据里有一处是**反过来的**：``pyproject`` 当时声明着
    beautifulsoup4，``interfaces/cli.py`` 的能力探针也报告着它 —— 同一个仓里有两处
    为「HTML 是 BeautifulSoup 解析的」作证，而没有任何一处会对照本模块的代码。
    （``adapters/__init__.py`` 那张表是站在真话这一边的，写的是「无 ``bs4``」。）
    """

    name = "html"
    source_kind = SourceKind.FETCHED_HTML
    extracts_links = True

    def can_parse(self, path: Path, *, content_type: str = "") -> bool:
        if "html" in content_type.lower():
            return True
        return path.suffix.lower() in {".html", ".htm", ".xhtml"}

    def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
        raw = _read_text(path)
        if not raw.strip():
            raise ParseError(f"HTML 文件为空：{path.name}")

        # 链接与正文抽的是**同一份**去过 script/style 的字节：在原始字节上抽会把
        # ``document.write('<a href=...>')`` 里的地址也收成一条链接，而那条地址
        # 指向的页面并不存在 —— 报告上它与别的链接没有区别。
        without_scripts = _SCRIPT_STYLE.sub(" ", raw)
        with_breaks = _BLOCK_SEPARATORS.sub("\n", without_scripts)
        text = _TAG.sub(" ", with_breaks)
        text = _INLINE_WS.sub(" ", text)
        text = "\n".join(line.strip() for line in text.splitlines())
        text = _BLANK_LINES.sub("\n\n", text).strip()

        if not text:
            raise ParseError(f"HTML 未提取到可见文本：{path.name}")

        return ParsedDocument(
            source_path=path,
            sha256=sha256_file(path),
            media_type="text/html",
            parser=self.name,
            title=_title_in(without_scripts),
            text=text,
            links=_links_in(without_scripts),
            extracts_links=self.extracts_links,
            source_kind=self.source_kind,
            page_count=1,
        )


class TextPdfParser(DocumentParser):
    """文本型 PDF → 纯文本（pypdf）。

    对扫描件不硬撑：如果绝大多数页面提不出文本，抛 ``ParseError`` 并说明原因，
    由 router 决定是否交给 MinerU。硬撑的结果是产出空文本，最后被误当成
    「这个站点没有邮箱」—— 那会污染 §10 的 net-new C/D 指标。

    **本解析器不看链接**（``extracts_links = False``）。PDF 里的链接住在注释里
    （``/Annots`` 的 URI action），pypdf 读得到，但本构建没有一份真实的 PDF 样本
    去校准那条读法 —— 与 MinerU 那个调用点同一条纪律：没有样本的抽取会产出没人
    验证过的地址，而它会一路走进证据。所以这里明说「没抽」，让报告上的空链接
    读成欠账而不是「这份 PDF 没有链接」。

    **正文抽取那一半**（下面这个 ``parse`` 的每一个分支 —— 页面合并、两个扫描件
    阈值、无页面、非 PDF 字节）的执行器是 ``tests/test_pdf_parser.py``。那里的样本
    是**真的 PDF 字节**：手写的对象表与 xref 表，pypdf 按自己的读法读，没有一处替身。
    但它是**合成**的（ASCII 正文 + 标准 Type1 字体 + 未压缩内容流），所以它能证明
    「这条路径怎么走」，**不能**证明「真实站点的 PDF 都读得对」。上面那句「没有一份
    真实的 PDF 样本」说的是**链接**读法 —— 本条**不构成**它的样本，``extracts_links``
    因此照旧是 ``False``。
    """

    name = "pypdf"
    source_kind = SourceKind.FETCHED_PDF
    extracts_links = False

    def can_parse(self, path: Path, *, content_type: str = "") -> bool:
        if "pdf" in content_type.lower():
            return True
        return path.suffix.lower() == ".pdf"

    def parse(self, path: Path, *, content_type: str = "") -> ParsedDocument:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            from ..domain.errors import ParserUnavailableError

            raise ParserUnavailableError("pypdf 未安装，无法解析文本型 PDF") from exc

        try:
            reader = PdfReader(str(path))
            pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:  # noqa: BLE001 - pypdf 的异常类型不稳定
            raise ParseError(f"PDF 解析失败（{path.name}）：{type(exc).__name__}: {exc}") from exc

        if not pages:
            raise ParseError(f"PDF 没有页面：{path.name}")

        blank_pages = sum(1 for text in pages if len(text.strip()) < SCANNED_PAGE_TEXT_THRESHOLD)
        text = "\n\n".join(page.strip() for page in pages if page.strip()).strip()

        if not text:
            raise ParseError(
                f"PDF 未提取到任何文本，疑似扫描件（{path.name}，{len(pages)} 页全部空白）"
            )
        if blank_pages / len(pages) >= SCANNED_PAGE_RATIO:
            raise ParseError(
                f"PDF 疑似扫描件：{blank_pages}/{len(pages)} 页无可用文本（{path.name}）。"
                "应交给 MinerU fallback。"
            )

        return ParsedDocument(
            source_path=path,
            sha256=sha256_file(path),
            media_type="application/pdf",
            parser=self.name,
            # 这一格写 ``""`` 而且**只**写 ``""``：PDF 的 ``/Title`` 不是这一页
            # 给自己的名字，它通常是写作工具留下的字符串（``Microsoft Word - …``），
            # 拿它当指称对象会把一次工具痕迹读成一个主体。而本构建没有一份真实
            # PDF 样本去校准那条读法 —— 同 ``extracts_links`` 对链接的态度：
            # 没有样本的抽取会产出没人看过的东西，而它看起来像一条记录。
            # 要放开它，先有一份样本，再写一条测试。
            title="",
            text=text,
            # 显式写出来：``extracts_links`` 是 False，而这两个字段都是必填的。
            # 空的链接在这里不是「这份 PDF 没有链接」——是一个没人做过的抽取。
            links=(),
            extracts_links=self.extracts_links,
            source_kind=self.source_kind,
            page_count=len(pages),
            warnings=(
                (f"{blank_pages}/{len(pages)} 页无文本，结果可能不完整",) if blank_pages else ()
            ),
        )


def _title_in(html: str) -> str:
    """这份材料**自己写下的**标题。取不到返回 ``""``。

    三条取舍，每一条都是有意写下来的：

    * **不解码 HTML 实体**。本仓没有实体表，也不会为这一格引一个解析器（同
      ``_links_in`` 对 ``href`` 的态度）。于是 ``&amp;`` 逐字留着 —— 它照样是一个
      可引用的字面，而**解码**才是需要先立一条规矩的那一步。
    * **标签剥掉**。``<title>Foo <b>Bar</b></title>`` 不是合法 HTML，但它出现在
      真实站点上，而带着标签的名字会一路进到候选行的指称对象里。
    * **空白折叠成一个空格**。标题里的换行是排版的产物，不是名字的一部分。

    取的是**已去掉 ``<script>`` / ``<style>`` 的字节**：``document.write('<title>…')``
    里的那个标题并没有被浏览器当作标题，而它与真的那一个在文本里长得一样。
    """
    match = _TITLE.search(html)
    if match is None:
        return ""
    return " ".join(_TAG.sub(" ", match.group(1)).split())


def _links_in(html: str) -> tuple[DocumentLink, ...]:
    """从（已去掉 ``<script>`` / ``<style>`` 的）HTML 里抽锚元素。

    三条**有意**的取舍，每一条都对应 ``tests/test_parsers_report_links.py`` 里的
    一条判据：

    * **不解析** href（相对地址原样留下）。解析要看页面 URL 与 ``<base href>``，
      两者都不在这个函数的输入里 —— 见 ``DocumentLink``。
    * **不去重**：同一个 href 出现在页眉与页脚就是两条。这里报的是「文档里的
      锚元素」，而合并规则一旦写错，两个不同的链接会被并成一个。
    * **跳过没有目标的**：``href=""`` / ``href="   "`` 指的是文档自己（模板里漏填
      的下场），没有 ``href`` 的 ``<a>`` 没有目标。它们都不是「页面上的另一条
      链接」。
    """
    links: list[DocumentLink] = []
    for attrs, inner in _ANCHOR.findall(html):
        match = _HREF.search(attrs)
        if match is None:
            continue
        url = next((group for group in match.groups() if group), "").strip()
        if not url:
            continue
        # 锚文本：先去掉内层标签（``<span>``、``<img>``），再把空白折成一个空格。
        # 只有图片的链接因此得到空串 —— 那是一个真实取值，链接本身照样留下。
        links.append(DocumentLink(url=url, text=" ".join(_TAG.sub(" ", inner).split())))
    return tuple(links)


def _read_text(path: Path) -> str:
    """读取文本文件，显式 UTF-8 优先，避免平台默认编码差异。"""
    data = path.read_bytes()
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


__all__ = ["HtmlDocumentParser", "TextPdfParser", "SCANNED_PAGE_TEXT_THRESHOLD"]

# Synchronized internal copy of marketops/adapters/xlsx.py from integration/marketops-real-research-runtime-20261015.
# Temporary self-contained packaging until the canonical exporter is made importable from the client installation.
# Keep its OOXML writer unchanged; all business column definitions live in research_delivery.py.
r"""把一张表写成 XLSX —— 只用手边的 ``zipfile`` 与 ``xml.sax.saxutils``。

## 为什么不装一个库

本仓的依赖表是**对系统的第一份描述**（``tests/test_declared_dependencies.py``
两个方向都判：声明了没人用会红、用了没声明也会红）。为了导出一种文件格式而引进
一棵依赖树，代价不是一次安装：那张表从此要说「我们靠 X 渲染表格」，而 X 的升级、
漏洞与许可证都跟着进来。OOXML 里「一张有表头、有列宽、能按列筛选的工作表」是
一小组固定的部件，生成它不需要一个库 —— 需要的是一份**说得出哪些字节是承重的**
清单，那份清单就是下面这几十行。

## 写出来的文件要能被*读*过，才算写出来了

一个 XLSX 是 ZIP，而「ZIP 里塞了几段 XML」与「表格软件打得开」不是一回事。
中间隔着的正是本模块唯一容易写错的三处，三处都会产出**字节上完全正常、
却打不开**的文件：

* **XML 1.0 不接受的控制字符**。XML 的 ``Char`` 产生式只允许 ``\t`` ``\n`` ``\r``
  三个小于 0x20 的字符，外加孤立的代理码位与 ``\ufffe`` / ``\uffff`` 一律不允许。
  ``ElementTree`` 会把它们**原样吐出来**，``tostring`` 也不为此抛任何异常 ——
  于是出来的是一份格式不合法的 XML。证据正文是从网页上抄下来的，那里出现
  控制字符是常态，所以清洗是**承重**的，不是美容（:func:`_xml_safe`）。
* **工作表名的形状**。超过 31 个字、含 ``[ ] : * ? / \`` 或与别名重名，表格软件
  拒绝打开**整个文件**，而不只是拒绝那一页。
* **单元格文本的上限**。一格最多 32767 个字符，而一份证据摘录可以更长。

所以 :func:`render_workbook` 不在返回前检查自己的输出 —— 它**构造**的时候就产不出
上面三种形状：名字与长度在 :class:`Sheet` 构造时判，控制字符在写之前 :func:`_xml_safe`
滤掉。判据在 ``tests/test_xlsx_writer.py``，读回来逐格比对，并与同一批行的 CSV
导出对账：两条互不相干的序列化从同一份行得出同样的值，比任何一条单独的自洽都硬。

## 为什么是拼字符串，不是 ``ElementTree``

``ElementTree`` 想把元素写进默认命名空间，得先 ``register_namespace("", NS)``，
而那是**进程级**的全局状态：本仓别处（``adapters/sitemap_frontier.py`` 一族）也在
用 ``ElementTree`` 解析与序列化文档，一次注册会改变**它们**的渲染结果。这个模块
是库代码，不是一次性脚本，所以它不碰那份全局表。字符串拼接 + :func:`escape` /
:func:`quoteattr` 得到的字节是可预测的，而那两个字面量写在这里、读得出来。
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Union
from xml.sax.saxutils import escape, quoteattr

__all__ = ["MAX_CELL_CHARS", "MAX_SHEET_NAME", "Sheet", "render_workbook"]

#: 一格里放得下的字符数。表格软件的上限是 32767，这里留一截给截断记号，
#: 免得「刚好 32767 字的摘录 + 记号」又越过去 —— 那种越界只在**最长的**那一份
#: 材料上现形，而它恰好是最需要被读全的一份。
MAX_CELL_CHARS = 32_700

#: 工作表名的长度上限，表格软件的硬限制。
MAX_SHEET_NAME = 31

#: 一份工作簿里最多能有几页，以及一页最多几列 —— 都是表格软件的硬限制。
MAX_SHEETS = 255
MAX_COLUMNS = 16_384

#: 没给列宽时用多宽，以及列宽的上限。
DEFAULT_WIDTH = 14.0
MAX_WIDTH = 255.0

#: 工作表名里**不许出现**的字符。表格软件把它们留给区域与公式，遇到就拒绝打开
#: 整个文件（一个字节都没坏，只是它不开）。
_SHEET_NAME_FORBIDDEN = "[]:*?/\\"

CellValue = Union[str, int, float, bool, None]

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"

#: ``[Content_Types].xml`` 里要用到的四段 MIME 名。写成常量而不是散在下面的
#: f-string 里：那四行里每一段都嵌着一个长 URL，散着写会读成「一坨字」，
#: 而它们各自是一个**必须逐字对**的标识。
_CT_RELS = "application/vnd.openxmlformats-package.relationships+xml"
_CT_BOOK = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
_CT_STYLES = "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"
_CT_SHEET = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"

#: 一页工作表的样式索引。三档就够用，而多出来的档位每加一格都要在
#: ``styles.xml`` 里多一个 ``cellXfs`` —— 那份表是按下标引用的，插错一位
#: 会让**所有**单元格换一种样子，而它不报错。
_STYLE_BODY = 0
_STYLE_HEADER = 1
_STYLE_BAND = 2


def _xml_safe(text: str) -> str:
    r"""决定**哪些字符真的会落进文件里** —— 两件事，都因为格式本身。

    ## 一、XML 1.0 不接受的字符要去掉

    留下的判据逐字是 XML 的 ``Char`` 产生式：``\t`` / ``\n`` / ``\r``、
    ``0x20``–``0xD7FF``、``0xE000``–``0xFFFD``、``0x10000``–``0x10FFFF``。
    代理码位 ``0xD800``–``0xDFFF`` 单独排除（它们在 Python 里合法、在 XML 里
    不是 —— 一段被网页响应体截断的 UTF-16 正好会带着它们）。不去掉的话得到的
    是一份**格式不合法**的 XML，而表格软件拒绝打开整个文件。

    ## 二、换行要在这里归一化，因为读数**一定**会被归一化

    这一条不是选择，是 XML 1.0 §2.11 的规定：任何合规的解析器都必须把 ``\r\n``
    与孤立的 ``\r`` 读成 ``\n``。于是「把 ``\r`` 原样写进去」并不保存了它 ——
    它只是让**写进去的字节**与**任何人读出来的值**不一样，而两边的差在盘上
    看不出来（这正是本仓最防的那类情形：一个只有拿另一个量具才现形的差异）。
    所以这里就把 ``\r\n`` / ``\r`` 归一成 ``\n``，让写侧说得出读侧会看到什么。

    （这条是测试读出来的：第一版**没有**归一化，于是
    ``test_a_control_character_in_the_text_does_not_get_written`` 读回来的
    ``\r`` 变成了 ``\n``。当时的第一反应是改测试的期望值 —— 那会让这条差异
    从「写侧的一个错误声明」变成「两条各说各话的期望」，也就是让它消失。）

    除这两件以外**一个字符都不动**：不转义（那是 :func:`escape` 的事）、不截断
    （那是 :func:`_cell_text` 的事）。
    """
    kept = "".join(char for char in text if _is_xml_char(ord(char)))
    return kept.replace("\r\n", "\n").replace("\r", "\n")


def _is_xml_char(code: int) -> bool:
    if 0xD800 <= code <= 0xDFFF:
        return False
    return code in (0x09, 0x0A, 0x0D) or 0x20 <= code <= 0xD7FF or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF


def _cell_text(value: CellValue) -> str:
    """一格的值 → 写进 ``<t>`` 的那串字。"""
    text = _xml_safe(str(value))
    if len(text) <= MAX_CELL_CHARS:
        return text
    return text[:MAX_CELL_CHARS] + " …[本格截断]"


def _column_name(index: int) -> str:
    """1 → ``A``、27 → ``AA``。列号是 1 起的，与 Excel 的 A1 记法一致。"""
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


@dataclass(frozen=True)
class Sheet:
    """一页工作表：表头、行、列宽。

    三件事在这里被判，判的理由都是「不判的话，坏掉的是**整个文件**」：

    * ``name`` 的长度与字符集（见模块 docstring 第二段）；
    * 每一行的格数**必须等于表头的格数** —— 少一格会让这一行整体左移，多一格
      会让它整体右移。阅读的人按列读，于是「张三的邮箱」那一格显示的是李四的
      邮箱，而盘上的字节一个都没错。这是本仓最在意的那类错误（错误绑定），
      所以它在这里是一个构造期的拒绝，不是一句提醒；
    * ``widths`` 只准比表头短（后面的列用默认宽度），不许比表头长。
    """

    name: str
    headers: tuple[str, ...]
    rows: tuple[tuple[CellValue, ...], ...] = ()
    widths: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        cleaned = self.name.strip()
        if not cleaned:
            raise ValueError("工作表名不能是空的，也不能只有空白。")
        if len(cleaned) > MAX_SHEET_NAME:
            raise ValueError(
                f"工作表名 {self.name!r} 有 {len(cleaned)} 个字，上限是 {MAX_SHEET_NAME}。\n"
                "  超了的话表格软件拒绝打开整个文件，而不只是跳过这一页。"
            )
        bad = sorted({char for char in cleaned if char in _SHEET_NAME_FORBIDDEN})
        if bad:
            raise ValueError(
                f"工作表名 {self.name!r} 含表格软件留作他用的字符：{bad}（{_SHEET_NAME_FORBIDDEN!r}）。"
            )
        if not self.headers:
            raise ValueError(f"工作表 {self.name!r} 没有表头 —— 一行都没有的页没有可读的列。")
        if len(self.headers) > MAX_COLUMNS:
            raise ValueError(
                f"工作表 {self.name!r} 有 {len(self.headers)} 列，上限是 {MAX_COLUMNS}。"
            )
        if len(self.widths) > len(self.headers):
            raise ValueError(
                f"工作表 {self.name!r} 给了 {len(self.widths)} 个列宽，却只有 "
                f"{len(self.headers)} 列 —— 多出来的那个列宽指不到任何一列。"
            )
        for index, row in enumerate(self.rows, start=2):
            if len(row) != len(self.headers):
                raise ValueError(
                    f"工作表 {self.name!r} 第 {index} 行有 {len(row)} 格，表头有 "
                    f"{len(self.headers)} 格。\n"
                    "  少一格会让这一行整体左移、多一格整体右移 —— 而按列读的人会把"
                    "邻格的邮箱读成这一格的人的。所以这里拒绝，不补齐也不截断。"
                )

    @property
    def width_of(self) -> tuple[float, ...]:
        """逐列的实际宽度，短的补默认值。"""
        filled = list(self.widths)
        while len(filled) < len(self.headers):
            filled.append(DEFAULT_WIDTH)
        return tuple(filled)


def _row_xml(
    number: int,
    values: tuple[CellValue, ...] | tuple[str, ...],
    *,
    style: int,
) -> tuple[str, list[tuple[str, str]]]:
    """一行的 XML，外加这一行里认出来的超链接 ``(格址, URL)``。"""
    cells: list[str] = []
    links: list[tuple[str, str]] = []
    for index, value in enumerate(values, start=1):
        ref = f"{_column_name(index)}{number}"
        if isinstance(value, bool):
            cells.append(f'<c r="{ref}" s="{style}" t="b"><v>{1 if value else 0}</v></c>')
            continue
        if isinstance(value, (int, float)):
            cells.append(f'<c r="{ref}" s="{style}"><v>{value}</v></c>')
            continue
        if value is None:
            # 空着就是空着：不写 ``<v>`` 也不写 ``<t>``。这一格与「一格写着空串」
            # 在表上看起来一样，而它们的来源不同 —— 后者是有人写了一个空串进来。
            cells.append(f'<c r="{ref}" s="{style}"/>')
            continue
        text = _cell_text(value)
        cells.append(
            f'<c r="{ref}" s="{style}" t="inlineStr"><is>'
            f'<t xml:space="preserve">{escape(text)}</t></is></c>'
        )
        if text.startswith(("https://", "http://")) and "\n" not in text:
            links.append((ref, text))
    return f'<row r="{number}">' + "".join(cells) + "</row>", links


def _sheet_xml(sheet: Sheet, index: int) -> tuple[str, str | None]:
    """一页的 XML，以及（有超链接时）它自己的 rels 部件。"""
    last_column = _column_name(len(sheet.headers))
    last_row = len(sheet.rows) + 1
    span = f"A1:{last_column}{last_row}"

    columns = "".join(
        f'<col min="{position}" max="{position}" width="{min(width, MAX_WIDTH)}" customWidth="1"/>'
        for position, width in enumerate(sheet.width_of, start=1)
    )
    row_parts: list[str] = []
    links: list[tuple[str, str]] = []
    header, header_links = _row_xml(1, sheet.headers, style=_STYLE_HEADER)
    row_parts.append(header)
    links.extend(header_links)
    for number, row in enumerate(sheet.rows, start=2):
        # 隔行一个底色：一行里四五段长摘录挨在一起时，读者的眼睛要靠它找回行首。
        style = _STYLE_BAND if number % 2 == 0 else _STYLE_BODY
        part, row_links = _row_xml(number, row, style=style)
        row_parts.append(part)
        links.extend(row_links)

    hyperlinks = ""
    relationships: str | None = None
    if links:
        entries = []
        rels = []
        for position, (ref, url) in enumerate(links, start=1):
            rel_id = f"rId{position}"
            entries.append(f'<hyperlink ref="{ref}" r:id="{rel_id}"/>')
            rels.append(
                f'<Relationship Id="{rel_id}" Type="{_REL}/hyperlink" '
                f"Target={quoteattr(url)} TargetMode=\"External\"/>"
            )
        hyperlinks = "<hyperlinks>" + "".join(entries) + "</hyperlinks>"
        relationships = f'<Relationships xmlns="{_PKG}">' + "".join(rels) + "</Relationships>"

    xml = (
        f'<worksheet xmlns="{_MAIN}" xmlns:r="{_REL}">'
        f'<dimension ref="{span}"/>'
        '<sheetViews><sheetView workbookViewId="0">'
        # 冻结表头那一行：往下翻的时候列名还在。
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        # 行高**不写死**：写死会把换行的长摘录按那个高度裁掉，而不写时表格软件
        # 会按内容自动撑开。
        '<sheetFormatPr defaultRowHeight="15"/>'
        f"<cols>{columns}</cols>"
        "<sheetData>" + "".join(row_parts) + "</sheetData>"
        f'<autoFilter ref="{span}"/>'
        f"{hyperlinks}"
        '<pageMargins left="0.3" right="0.3" top="0.4" bottom="0.4" header="0.2" footer="0.2"/>'
        '<pageSetup paperSize="9" orientation="landscape" fitToWidth="1"/>'
        "</worksheet>"
    )
    del index  # 页码只进文件名，不进内容 —— 工作表在自己的 XML 里不自报序号。
    return xml, relationships


#: 样式表。三档 ``cellXfs``（正文 / 表头 / 隔行底色），全部顶对齐 + 自动换行。
#:
#: ``fills`` 的前两格（``none`` / ``gray125``）是保留项，不是多余的：表格软件要求
#: 它们占据下标 0 与 1，删掉会让后面每一格的颜色**整体前移一位** —— 表头会变成
#: 透明的，而文件照开不误。
_STYLES = (
    f'<styleSheet xmlns="{_MAIN}">'
    '<fonts count="2">'
    '<font><sz val="11"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
    "</fonts>"
    '<fills count="4">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF19354A"/>'
    '<bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F6F8"/>'
    '<bgColor indexed="64"/></patternFill></fill>'
    "</fills>"
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)

_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'


def render_workbook(sheets: list[Sheet] | tuple[Sheet, ...]) -> bytes:
    """把若干页写成一份 XLSX 的字节。

    返回字节而不是写盘：写到哪里是**调用方**的决定（受管存储的根是部署配置，
    不是这个格式适配器的知识）。同一批字节因此可以在测试里被读回来比对。
    """
    if not sheets:
        raise ValueError("一份工作簿至少要有一页。")
    if len(sheets) > MAX_SHEETS:
        raise ValueError(f"给了 {len(sheets)} 页，上限是 {MAX_SHEETS}。")
    seen: set[str] = set()
    for sheet in sheets:
        lowered = sheet.name.strip().casefold()
        if lowered in seen:
            raise ValueError(
                f"两页用同一个名字 {sheet.name!r}（大小写不敏感地比）。\n"
                "  表格软件拒绝重名，而重名的两份又说不清哪一份是后写的 —— 拒绝，"
                "不自动加后缀：改名会让按名字引用它的人找不到它。"
            )
        seen.add(lowered)

    content_types = [
        f'<Default Extension="rels" ContentType="{_CT_RELS}"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        f'<Override PartName="/xl/workbook.xml" ContentType="{_CT_BOOK}"/>',
        f'<Override PartName="/xl/styles.xml" ContentType="{_CT_STYLES}"/>',
    ]
    workbook_sheets = []
    workbook_rels = []
    for index, sheet in enumerate(sheets, start=1):
        # 名字也过一遍 :func:`_xml_safe`：它印在**属性**里，而一个孤立的代理码位
        # 在属性里与在文本里一样让整份 XML 不合法。
        workbook_sheets.append(
            f'<sheet name={quoteattr(_xml_safe(sheet.name.strip()))} sheetId="{index}" '
            f'r:id="rId{index}"/>'
        )
        workbook_rels.append(
            f'<Relationship Id="rId{index}" Type="{_REL}/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="{_CT_SHEET}"/>'
        )

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        # ``[Content_Types].xml`` 写在最前：按流式读的解析器要求它先到。
        archive.writestr(
            "[Content_Types].xml",
            _DECLARATION + f'<Types xmlns="{_CONTENT_TYPES}">' + "".join(content_types) + "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            _DECLARATION
            + f'<Relationships xmlns="{_PKG}"><Relationship Id="rId1" '
            f'Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            _DECLARATION
            + f'<workbook xmlns="{_MAIN}" xmlns:r="{_REL}"><sheets>'
            + "".join(workbook_sheets)
            + "</sheets></workbook>",
        )
        archive.writestr("xl/styles.xml", _DECLARATION + _STYLES)
        for index, sheet in enumerate(sheets, start=1):
            xml, relationships = _sheet_xml(sheet, index)
            archive.writestr(f"xl/worksheets/sheet{index}.xml", _DECLARATION + xml)
            if relationships is not None:
                archive.writestr(
                    f"xl/worksheets/_rels/sheet{index}.xml.rels",
                    _DECLARATION + relationships,
                )
        workbook_rels.append(
            f'<Relationship Id="rIdStyles" Type="{_REL}/styles" Target="styles.xml"/>'
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            _DECLARATION + f'<Relationships xmlns="{_PKG}">' + "".join(workbook_rels) + "</Relationships>",
        )
    return buffer.getvalue()

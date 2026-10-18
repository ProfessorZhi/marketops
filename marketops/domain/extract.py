"""literal extraction —— 只做**字面**抽取。

需求原文：「literal email extraction」。

因此本模块**只有**正则匹配，没有任何生成逻辑：

* 不猜测邮箱（不拼 ``first.last@domain``）；
* 不解码混淆写法（``name (at) domain`` 只记录、不还原）；
* 不做 SMTP 验证；
* 不做无标签的姓名识别（误报率太高，会直接污染 false-attribution 指标）。

姓名只在**显式标签**后抽取（``Contact: Jane Doe``、``Mr Jane Doe``）。
角色只在 profile 提供的字面词表中匹配；词表为 UNKNOWN 时产出为空 ——
这是刻意的，宁可少抽，不要编造。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

#: 常规邮箱形态。
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

#: 混淆写法（``name (at) example (dot) com``）。本阶段**不解码**，只登记供人工复核。
OBFUSCATED_EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+\s*(?:\(at\)|\[at\]|\s+at\s+)\s*[A-Za-z0-9.\-]+\s*"
    r"(?:\(dot\)|\[dot\]|\s+dot\s+)\s*[A-Za-z]{2,}",
    re.IGNORECASE,
)

#: 姓氏前的敬称（``Mr Jane Doe``）。敬称后面跟的是空格，不是冒号。
#: 大小写不敏感 —— 但只对敬称本身，姓名本体仍然要求首字母大写。
SALUTATION = r"(?i:mr|mrs|ms|miss|dr|prof)"

#: **同一行内**的空白。``\s`` 会匹配换行，而姓名本体里的换行是一条串行的边界。
#:
#: 用 ``\s`` 的后果在一条两行的卡片上就能看到：
#:
#:     Contact: Jane Doe
#:     Grounds Manager — jane.doe@example.co.uk
#:
#: ``_NAME_BODY`` 的 ``(?:\s+[A-Z][a-z'\-]+){0,2}`` 会把下一行的 ``Grounds``
#: 吃进姓名里，于是 ``person_name`` 是 ``"Jane Doe\nGrounds"`` —— 一个不存在的人，
#: 而 ``Person`` 的 id 由这个名字派生，所以它同时是一个不存在的身份。抽取器当时
#: 没被发现，是因为 ``context`` 会把换行折成空格（``" ".join(split())``），
#: 于是那一段读起来完全正常。
#:
#: 标签与姓名之间的那一格仍然允许换行（``Contact:`` 单独一行、姓名在下一行是
#: 常见的写法），那是**两处**之间的间隔，不是姓名内部。
SAME_LINE_WS = r"[^\S\n]+"

#: 姓名本体：首词首字母必须大写，最多三个词，**且三个词在同一行**。
_NAME_BODY = r"([A-Z][a-z]+(?:" + SAME_LINE_WS + r"[A-Z][a-z'\-]+){0,2})"

#: 显式标签后的姓名。
#:
#: 三处刻意的写法：
#:
#: 1. 标签用 ``(?i:...)`` **局部**忽略大小写，姓名本体不忽略 —— 否则
#:    ``Contact: sales (at) example (dot) co.uk`` 会把 ``sales`` 当成一个人名，
#:    进而产出一个姓名为 "sales" 的候选联系人。姓名必须真的首字母大写。
#: 2. 敬称单独成一条分支（``Mr Jane Doe``）。要求敬称后跟冒号会漏掉最常见的写法。
#: 3. 姓名**内部**不许跨行（见 :data:`SAME_LINE_WS`）。
LABELLED_NAME_PATTERN = re.compile(
    r"(?i:contact|name|owner|manager|director|buyer|purchasing|sales)"
    r"\s*[:\-–]\s*" + _NAME_BODY + r"|" + SALUTATION + r"\.?" + SAME_LINE_WS + _NAME_BODY
)

#: 上下文窗口：命中点左右各取多少字符。
CONTEXT_RADIUS = 120


@dataclass(frozen=True)
class LiteralHit:
    """一处字面命中。``context`` 是给人看的，不参与自动化判定。"""

    value: str
    context: str
    offset: int
    kind: str  # "email" | "name" | "role" | "obfuscated_email" | "publication_time"


def _context(text: str, start: int, end: int) -> str:
    left = max(0, start - CONTEXT_RADIUS)
    right = min(len(text), end + CONTEXT_RADIUS)
    return " ".join(text[left:right].split())


# --- 条目窗口：两处命中是不是落在同一条上 --------------------------------------
#
# §9 禁止的读法里有一条最容易悄悄发生：**一页取姓名、另一页取邮箱，中间没有任何
# 关系证据就缝成一条路线**。这一节给出「关系证据」里最弱、也最不需要别的输入的
# 那一种 —— **两处命中在文本里挨着**。它不解释任何业务含义，只回答「这两处是不是
# 印在一起」，因此属于「字面抽取」而不是生成。
#
# **为什么是「行」而不是「段」。** 绑定的输入是解析器交出来的纯文本
# （``ParsedDocument.text``），段落边界在那份文本里只剩换行，而换行的**数量**取决于
# 源文件怎么排版：``<p>a</p><p>b</p>`` 一行写下来与每个标签各占一行写下来，段数一样、
# 空行数不一样（``text_pdf.HtmlDocumentParser`` 把块级标签变成 ``\n``，但源文件自己的
# 缩进换行也留在里面）。让空行参与比较，「隔了几条」就会随缩进变 —— 同一张卡片在压缩
# 过的 HTML 里算挨着、在缩进过的 HTML 里算隔开，而这两份 HTML 是同一个页面。
# 所以这里**只数非空行**：空行是排版，不是内容。

#: 条目窗口：命中所在的那一条，以及紧邻的**一条**条目行。
#:
#: 这个 ``1`` 不是调出来的阈值，而是「同一张卡片上的下一个字段行」这件事的下界：
#:
#: * 取 0 —— 要求人与邮箱印在**同一行**。``Contact: Jane Doe`` 与 ``Email: jane@…``
#:   各占一行是最常见的写法之一，取 0 会让这些页面**一条路线都产不出来**，而它们
#:   恰恰是关系最清楚的那一类。§9·4 里「没找到 ≠ 没有」反对的正是这种方向的错。
#: * 取 2 及以上 —— 会把「上一段正文里的具名」也算成这张卡片上的人。一段正文
#:   恰好隔开两条条目时，那个数就开始替页面说话。
#:
#: 它还顺带吸收了另一件事：姓名命中的 ``offset`` 落在**标签**上
#: （``LABELLED_NAME_PATTERN`` 从 ``Contact:`` 开始匹配），而标签与姓名值之间
#: 允许有一个换行。隔一条的容差让那一处写法仍然算挨着。
ENTRY_WINDOW_BLOCKS = 1


def block_ordinal(text: str, offset: int) -> int | None:
    """``offset`` 落在**第几条非空行**上（0 起）。空行不占位。

    落在空行上时返回 ``None`` —— 那不是一条命中的合法位置（命中是正则匹配出来的，
    至少有一个非空白字符），所以这里不替它挑一个邻近的条目：返回值会被调用方读成
    「它跟谁挨着」，而那个判断不能建立在一个猜测上。
    """
    line_start = text.rfind("\n", 0, offset) + 1
    line_end = text.find("\n", line_start)
    line = text[line_start:] if line_end == -1 else text[line_start:line_end]
    if not line.strip():
        return None
    return sum(1 for line in text[:line_start].split("\n") if line.strip())


def in_the_same_entry(text: str, left: int, right: int) -> bool:
    """两处命中是不是落在同一张卡片的条目窗口里。

    ``False`` 覆盖三种情形，而它们对调用方的意思是一样的 —— **我们不能声称它们
    印在一起**：两处相隔超过 :data:`ENTRY_WINDOW_BLOCKS` 条、任一处落在空行上。
    """
    left_block = block_ordinal(text, left)
    right_block = block_ordinal(text, right)
    if left_block is None or right_block is None:
        return False
    return abs(left_block - right_block) <= ENTRY_WINDOW_BLOCKS


def extract_emails(text: str) -> list[LiteralHit]:
    """抽取字面邮箱。按字面值去重，保留首次出现的位置。"""
    hits: list[LiteralHit] = []
    seen: set[str] = set()
    for match in EMAIL_PATTERN.finditer(text):
        value = match.group(0)
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            LiteralHit(
                value=value,
                context=_context(text, match.start(), match.end()),
                offset=match.start(),
                kind="email",
            )
        )
    return hits


def extract_obfuscated_emails(text: str) -> list[LiteralHit]:
    """登记混淆写法，但**不还原**。

    返回它们是为了让报告能列出「这个站点用 JS 混淆邮箱」这一事实，
    而不是把它们静默丢掉、让结果看起来像「这里没有邮箱」。
    """
    hits: list[LiteralHit] = []
    for match in OBFUSCATED_EMAIL_PATTERN.finditer(text):
        hits.append(
            LiteralHit(
                value=match.group(0).strip(),
                context=_context(text, match.start(), match.end()),
                offset=match.start(),
                kind="obfuscated_email",
            )
        )
    return hits


def extract_labelled_names(text: str) -> list[LiteralHit]:
    """只在显式标签后抽取姓名。无标签的姓名识别本阶段不做。"""
    hits: list[LiteralHit] = []
    seen: set[str] = set()
    for match in LABELLED_NAME_PATTERN.finditer(text):
        # 两条分支各有一个捕获组，只有命中的那条非 None。
        value = (match.group(1) or match.group(2) or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        hits.append(
            LiteralHit(
                value=value,
                context=_context(text, match.start(), match.end()),
                offset=match.start(),
                kind="name",
            )
        )
    return hits


def extract_roles(text: str, role_terms: tuple[str, ...]) -> list[LiteralHit]:
    """按 profile 提供的字面词表匹配角色。

    ``role_terms`` 为空（UNKNOWN）时返回空列表 —— 不猜、不内建通用词表。
    """
    if not role_terms:
        return []

    hits: list[LiteralHit] = []
    seen: set[str] = set()
    for term in role_terms:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        for match in pattern.finditer(text):
            key = match.group(0).lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                LiteralHit(
                    value=match.group(0),
                    context=_context(text, match.start(), match.end()),
                    offset=match.start(),
                    kind="role",
                )
            )
    hits.sort(key=lambda hit: hit.offset)
    return hits


# --- 发布时刻 ------------------------------------------------------------------
#
# §9 要求记下来源**自己声明**的发布时刻，理由写在本模块那句「只做字面抽取」的
# 反面：一个没有这一格的系统分不出「2021 年的一个说法」与「今天的一个说法」。
#
# 抽取它只有一条纪律：**日期要挨着它自己的标签**，而标签是谁、要看得清。
#
# 为什么两个词表都要写，而不是「只找发布类字样、再找紧跟着的日期」：
#
#     Published. Last updated 3 April 2026
#
# 这句话里 ``Published`` 之后 32 个字符内确实有一个日期，但那一天的日期**属于
# ``Last updated``**。只看发布词就会把它读成发布时间 —— 这正是 §9 点名禁止的
# 缝法（把 A 处的日期配在 B 处的标题上），而它的方向是**危险的那一边**：
# 一份 2019 年写的页面在 2026 年被改过一句，那条 2019 年的事实会因此看起来
# 是当前的，于是它不会被重验。
#
# 所以判据是**竞争**而不是**白名单**：一个日期属于它前面最近的那个标签，那个
# 标签若不是发布类，这个日期就不算发布时刻。``updated`` / ``modified`` /
# ``revised`` 之流因此不需要被「排除」，它们是**另一个说法**，而那个说法赢了。

#: 自述**发布**的字样。``published_time``（``<meta>`` 里那种机器可读的写法）
#: 也落在里面 —— 它与句子是同一件事的两种写法，而结构化那一份更可靠。
PUBLISHING_LABEL_PATTERN = r"\b(?i:published|publishing|publication|posted|issued)"

#: 一个**日期标签**：发布类，或与它争同一个日期的另一类。
#:
#: ``created`` 在第二支里，而它与 ``updated`` 的理由**不相同**：``created`` 是
#: 建页时刻，它只会**早于**页面上的内容（做不出「让旧事实看起来更新」这件事），
#: 把它当发布时刻的代价是另一个方向的 —— 一个 2010 年建的页面 2026 年换掉了
#: 整份名单，锚点仍是 2010，一条新事实会被算成过期，于是去重验一件刚看过的事。
#: 那个方向安全，但它制造的是**假的工作**，所以同样不收。
DATE_LABEL_PATTERN = re.compile(
    r"(?P<publishing>" + PUBLISHING_LABEL_PATTERN + r")"
    r"|(?P<competing>\b(?i:updated?|update|modified|revised|review|reviewed|created|changed))"
)

#: 月份词表。**先长后短**：``march`` 必须排在 ``mar`` 前面，否则 ``march``
#: 只会被吃掉前三个字母、留下一个 ``ch`` 去和后面的年份较劲。
_MONTH = (
    r"january|february|march|april|may|june|july|august|september|october|"
    r"november|december|sept|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
)

#: 一个**日期**字面。四种形态：
#:
#: 1. ISO 8601 带时刻；2. ISO 8601 只有日期；
#: 3. ``1 March 2026`` / ``1st of March 2026``；4. ``March 1, 2026``。
#:
#: **其余一律不认**，其中最容易问的是 ``03/04/2026``：它是三月四日还是四月三日
#: 取决于写的人，而读的人分不出。两者相差不到一个月，可它要回答的问题是「这个
#: 说法是什么时候的」—— 一个不知道自己说哪一天的锚点不叫精度低，叫**没有锚点**，
#: 所以这里连匹配都不匹配（而不是匹配了再打折）。
#:
#: 只有月份没有日（``March 2026``）同样不匹配：日期是这一格里唯一可用的精度，
#: 缺了它就只能自己挑一天，而那一天是编出来的。
DATE_LITERAL_PATTERN = re.compile(
    r"(?P<iso>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)"
    r"|(?P<iso_date>\d{4}-\d{2}-\d{2}(?!\d))"
    r"|(?P<day_first>\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?(?:" + _MONTH + r")\s+\d{4}(?!\d))"
    r"|(?P<month_first>(?:" + _MONTH + r")\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}(?!\d))",
    re.IGNORECASE,
)

#: 标签到日期之间允许多远。
#:
#: 这个数是**样式**的性质（``</td><td>`` 有多长、``_time" content="`` 有多长），
#: 不是这份材料的性质。32 个字符容得下上面那两类结构化间距与 ``</td><td>``
#: 这类表格排版，容不下两句话。
MAX_LABEL_GAP = 32

#: ISO 形态的**严格**版，给 :func:`parse_publication_time` 用。
#:
#: 为什么不直接交给 ``datetime.fromisoformat`` 兜底：它比抽取器宽
#: （``20260301`` 与 ISO 周日期都收），于是「解析器认什么」这件事就没有判据了 ——
#: 而每多认一种写法，就多一个数字序列有机会被读成一天。
_ISO_LITERAL_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?",
    re.IGNORECASE,
)

#: 月份名 → 月号。全名与三字母缩写都收（``Sept`` 是英国的常见缩写）。
_MONTH_NUMBERS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_MONTH_FIRST_PATTERN = re.compile(
    r"(" + _MONTH + r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", re.IGNORECASE
)
_DAY_FIRST_PATTERN = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + _MONTH + r")\s+(\d{4})", re.IGNORECASE
)


def extract_publication_times(text: str) -> list[LiteralHit]:
    """页面自述的**发布时刻**字面，按出现顺序。

    一处日期只有在「它前面最近的那个日期标签是发布类」「两者相距
    :data:`MAX_LABEL_GAP` 以内」时才算。没有标签的日期一律不算 —— 页面上到处
    都是日期，而一个没有说明自己是什么的日期，正是什么都不能证明。

    返回的是**命中**（可能有 0 处、1 处或更多），不是结论。唯一那个可用的时刻
    由 :func:`page_publication_time` 给。
    """
    labels = [
        (match.start(), match.end(), "publishing" if match.lastgroup == "publishing" else "competing")
        for match in DATE_LABEL_PATTERN.finditer(text)
    ]

    hits: list[LiteralHit] = []
    for match in DATE_LITERAL_PATTERN.finditer(text):
        prior = [item for item in labels if item[1] <= match.start()]
        if not prior:
            continue
        _, label_end, kind = prior[-1]
        if kind != "publishing":
            continue
        if match.start() - label_end > MAX_LABEL_GAP:
            continue
        hits.append(
            LiteralHit(
                value=match.group(0),
                context=_context(text, match.start(), match.end()),
                offset=match.start(),
                kind="publication_time",
            )
        )
    return hits


def parse_publication_time(literal: str) -> datetime | None:
    """把一个日期字面解析成一个**时刻**；说不出唯一那一个时刻时给 ``None``。

    只有日期、没有时刻的字面落在**这一天的最早时刻**（``00:00:00Z``）。这个
    选择不是四舍五入：页面说的是哪一天，而那一天里最早的瞬间是唯一一个**所有**
    读法都同意的时刻。往早里取，误差只会让这条事实显得更旧 —— 另一个方向才有
    代价（一个还成立的说法被算成过期的，代价是多做一次重验；反过来是它被当成
    当前的，代价是没人再去核）。上界是一天，而现行的刷新窗口都在**几十天**这个
    量级上（日数以 ``maintenance.RefreshPlan.uncalibrated_default()`` 为准 ——
    这里刻意不重复那些数字：一份散文里的计数没有执行器，写两遍就会漂两次）。

    解析不出来时给 ``None`` 而**不抛** —— 与 ``literal_value`` 那一层同样的
    理由：抽到一处说不清楚的日期是这份材料的一个性质，不是一个需要中断的故障。
    """
    value = " ".join(literal.split()).strip(" .,")
    if not value:
        return None

    if _ISO_LITERAL_PATTERN.fullmatch(value):
        try:
            parsed = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    match = _DAY_FIRST_PATTERN.fullmatch(value)
    if match:
        day, month_name, year = match.group(1), match.group(2), match.group(3)
    else:
        match = _MONTH_FIRST_PATTERN.fullmatch(value)
        if not match:
            return None
        month_name, day, year = match.group(1), match.group(2), match.group(3)

    month = _MONTH_NUMBERS.get(month_name.lower())
    if month is None:
        return None
    try:
        return datetime(int(year), month, int(day), tzinfo=timezone.utc)
    except ValueError:
        # ``31 February`` 这类：它不是一个日期，所以它定时不了任何东西。
        return None


def page_publication_time(text: str) -> datetime | None:
    """这一页自述的**唯一**发布时刻；说不出唯一一个时给 ``None``。

    ``None`` 有三种来路，而它们**共用同一个结果**：页面一处发布日期都没写、
    写了但解析不出来、写了**不止一个互不相同的日期**。

    第三条是这一格与前面几条不同之处，值得写下来：一页上出现两个不同的发布
    日期（索引页、改版过的页面、转载）时，**挑一个**就是在替这份材料认领
    「哪一天管这一处内容」—— 而依据不在页面上。§9 禁止的正是这种缝法，
    只不过它缝的是页面与页面；同一页上的两处日期之间没有依据时，同一条纪律
    说的也是不挑。

    一处日期写了两次（同一句话在页头和页脚各出现一次）不算两个说法：按**解析
    出来的时刻**去重，而不是按字面去重，于是 ``1 March 2026`` 与 ``2026-03-01``
    这两处同一天的写法也合成一处。
    """
    instants = {
        parsed
        for hit in extract_publication_times(text)
        if (parsed := parse_publication_time(hit.value)) is not None
    }
    if len(instants) != 1:
        return None
    return instants.pop()


__all__ = [
    "LiteralHit",
    "extract_emails",
    "extract_obfuscated_emails",
    "extract_labelled_names",
    "extract_publication_times",
    "extract_roles",
    "page_publication_time",
    "parse_publication_time",
    "CONTEXT_RADIUS",
    "DATE_LABEL_PATTERN",
    "DATE_LITERAL_PATTERN",
    "MAX_LABEL_GAP",
]

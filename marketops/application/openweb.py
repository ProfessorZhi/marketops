"""开放网络查询展开器 —— **还不知道任何域名**时的检索式从这里来。

本仓今天唯一生成检索式的地方是 ``marketops/application/queries.py::build_queries``，
而它收一个已存在的 ``AccountRecord``。于是「按 SOP 的模板去发现一个**新**账号」
这件事一直表达不出来：唯一能表达它的词表在 ``profiles/*/profile.yaml`` 的
``search.query_templates`` 里，而它既没有消费者、也没有展开器。
声明在仓库里、行为没跟上，与没有声明在输出上是一样的 —— 这一层补的就是它。

**这一族是开放网络检索。** ``profile.yaml`` 的 ``search.query_templates`` 就是它：
目的不是补内容，是发现**新账号**。站内检索那一族（给**已知账号**发现
ContactRoute URL 的那种）不在本仓 —— ``profiles/`` 里没有它的声明，``marketops/``
里没有它的消费者，本模块不为它预留任何东西。两族的差别不是「哪一族更全」，
而是**这一条检索式需不需要一个已知账号**。所以本模块的输入里没有
``AccountRecord`` 这个参数 —— 不是省掉了，是它在这一层不存在。

三件事是这一层存在的理由：

1. **确定性。** 同样的 profile + 同样的冻结值 ⇒ 同样的序列，与词表的声明顺序
   无关，也与 ``dict``/``set`` 的遍历顺序无关。顺序不是装饰：预算是有限的，
   谁先出第二条决定了这几条检索式装的是哪批取值。一次词表重排若悄悄改变了
   序列，报告里只会表现为「净新增有点不一样」。
2. **绑冻结值。** ``{region}`` 只由调用方传入的冻结值（manifest 的
   ``geographic_scope``）绑定 —— 见下面「为什么 ``{region}`` 不来自 profile」。
3. **失败关闭。** 空词表抛错，不返回空序列；渲染不出的模板跳过并记录，
   不照发。见下面两条。
"""

# --- 为什么 ``{region}`` 不来自 profile ---------------------------------------
#
# ``{region}`` 由调用方传入的冻结值绑定（实验 manifest 的 ``geographic_scope``），
# **不读 ``search.regions``**。理由是实测出来的，不是风格问题。
#
# 这份 profile 的 ``search`` 映射里原本有**两个** ``regions`` 键：一行
# ``regions: [GB]``，以及后面那份 ``[England, Scotland, Wales, Northern Ireland]``。
# PyYAML 保留最后一个，于是 ``search.regions`` 取到的是四个**构成国名**，前者成了
# 死声明 —— 那个死键已在 ``95c102e`` 里删掉（删除前对过 HEAD 与工作树的生效值），
# 今天盘上只剩四个构成国名这一份。
#
# 后果比「重复键」严重：``{region}`` 要的是**地区词**（一次实验绑一个，例如某个
# 郡/地区），而活在盘上的是**国家名**。展开器一旦读它，两条臂就会各自从这份词表里
# 选地理范围 —— 而实验规格要求两支臂绑**同一个冻结值**，否则比较的就不再是发现
# 能力。所以：取值从参数进来，``search.regions`` 这一栏不读。
#
# --- 渲染不出的模板：跳过并记录，不照发 ---------------------------------------
#
# 一条 ``{venue_type} <冻结值> contact`` 不会报错 —— 它只会返回一堆无关结果，
# 然后把「我们其实没搜」伪装成「Google 上没有」。而静默丢同样不行：「跑了 3 条」
# 与「想跑 4 条但缺 1 个词表」在报告里必须是两句话，所以跳过的模板连同**缺哪个
# 占位符**一起记进 :attr:`OpenWebPlan.skipped`。
#
# --- 这一层不做什么 -----------------------------------------------------------
#
# * **不产出候选。** 一条检索式不是一条候选：它还没有 URL、没有页面证据。
#   ``AccountCandidate`` 由 runner 物化，本层只告诉它发哪几条。
# * **不抓取、不落盘、不产生证据。** 检索 provider 与抓取器都由调用方持有。
# * **不设预算。** 能跑几条是一个**决定**，属于调用方：上限来自 profile 的
#   ``search.max_queries``，由 ``marketops/application/budget.py`` 在起飞前扣
#   （调用方不得静默扩大它）。本层给的是**有序的完整序列**：谁先出第二条已经由
#   轮转定死，所以「被预算砍掉的是哪一种线索」是可复现的，而不是循环的自然长度。

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.errors import ConfigError
from ..domain.profile import ProfileBundle
from ..domain.sources import SourceMethod, resolve_source_method

#: 开放网络模板里允许出现的占位符。**本族自己的闭集，不与其他任何一族共用。**
#:
#: 为什么不能与一个「站内检索」族的校验器共用一张表（那一族不在本仓，但这条
#: 边界现在就成立）：**两个方向都会错，而错法不同。**
#:
#: 1. 那一族认识的是账号事实（``{domain}`` / ``{organisation}`` / …），本族的三个
#:    占位符一个都不在它的表里。共用等于本族一条模板都装载不了 —— 不是「用起来
#:    别扭」，是根本发不出第一条检索式。
#: 2. 反过来，共用会把本族的合法写法并进去，于是 ``{domain}`` 在开放网络模板里
#:    变成**合法**占位符。而这一族是在**还没有账号**的时候发检索式的：账号事实被
#:    注入发现期检索式，检索式看起来完全正常、返回一堆结果、报告里没有任何东西
#:    变红 —— **一次静默的查询注入**。两族的边界不是「谁更严格」，
#:    是「这一层有没有账号」。
#:
#: 表里的三个占位符各自都有一条模板在声明它，且都能在没有账号的情况下取值：
#: ``{role}`` 取 role-matrix 的正面词表，``{venue_type}`` 取 profile 的场馆类型
#: 词表，``{region}`` 取调用方传入的冻结值。
OPEN_WEB_PLACEHOLDERS: frozenset[str] = frozenset({"role", "venue_type", "region"})

#: **账号侧**的占位符：它们只有在已知账号上才取得到值。
#:
#: 这张表不参与取值，只参与**报错**：一个账号侧占位符出现在开放网络模板里是
#: 注入，不是一个拼错的占位符，报错时说清这一点比说「未知占位符」有用得多。
ACCOUNT_SIDE_PLACEHOLDERS: frozenset[str] = frozenset(
    {
        "domain",
        "organisation",
        "country",
        "venue_name",
        "responsible_entity",
        "location",
    }
)

#: 一条模板最多展开多少条检索式。**这是规划阶段的内存护栏，不是预算。**
#:
#: 今天是本仓唯一一个展开器，所以这个界只定义在一处 —— 这正是要的。站内检索
#: 那一族落地时应当**导入这一个常量**，而不是再写一个 512：重复一个字面量必然会
#: 漂移，而漂移的那一次会让两条规划路径的代价不一样，且没有任何东西会变红。
#: 被这个界截掉的部分记在 ``OpenWebPlan.truncated`` 里，不是静默丢。
MAX_EXPANSIONS_PER_TEMPLATE = 512

#: 开放网络检索式在来源注册表里的发现方法。
#:
#: 展开器产出的是检索式，不是候选（候选由 runner 物化，本层不产出 —— 见本模块
#: 「这一层不做什么」）。但**这一层知道自己的来源是哪一个**，所以它给出注册表
#: 里的那个成员，而不是让下游抄一个 id：``marketops/domain/candidate.py::new_candidate``
#: 收的就是这个成员。``search.engine.v1`` 属于 ``public_search`` 族 ——
#: 检索能**扩池**，这正是发现期要的能力；站内检索（``known_domain``）只补内容。
OPEN_WEB_SOURCE_METHOD: SourceMethod = resolve_source_method("search.engine.v1")

_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")

#: 占位符名的形状。宽松的括号匹配 + 严格的形状校验：``{Role}`` 与 ``{role`` 都要
#: 在装载期被抓住，而不是被当成普通文本留在检索式里。
_PLACEHOLDER_NAME = re.compile(r"[a-z_]+")


@dataclass(frozen=True)
class OpenWebQuery:
    """一条开放网络检索式，以及它是从哪条模板渲染出来的。"""

    text: str
    #: 模板原文（声明顺序里的那一条）。模板是**匿名的单行文本**，没有 id ——
    #: 它自己的文本就是它的身份。
    template: str


@dataclass(frozen=True)
class SkippedOpenWebTemplate:
    """一条**没有**被渲染的模板，以及为什么。

    「跳过」必须留下记录：否则「我们搜了 3 条」与「我们想搜 4 条但缺 1 个词表」
    在报告里长得一样。
    """

    template: str
    #: 目前只有 ``no_vocabulary``：这条模板要的取值维度没有任何取值。
    reason: str
    missing: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "reason": self.reason,
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class OpenWebPlan:
    """一次开放网络展开的结果：**有序**的检索序列，加它自己的审计信息。"""

    queries: tuple[OpenWebQuery, ...]
    templates_declared: int
    #: 这一轮绑定的冻结值。写进计划是为了让报告能回答「这几条检索式是在哪个
    #: 地理范围里搜的」—— 两条臂绑的是不是同一个值，靠它比对。
    frozen_region: str
    skipped: tuple[SkippedOpenWebTemplate, ...] = ()
    #: 撞上展开上限、因而没有全部进入序列的模板。
    truncated: tuple[str, ...] = ()
    source_method: SourceMethod = OPEN_WEB_SOURCE_METHOD

    @property
    def is_empty(self) -> bool:
        return not self.queries

    def template_counts(self) -> dict[str, int]:
        """每个模板贡献了几条 —— 「这几条是同一种模板的变体」要看得出来。"""
        counts: dict[str, int] = {}
        for query in self.queries:
            counts[query.template] = counts.get(query.template, 0) + 1
        return counts

    def summary(self) -> dict[str, Any]:
        return {
            "templates_declared": self.templates_declared,
            "queries_planned": len(self.queries),
            "frozen_region": self.frozen_region,
            "from_template": self.template_counts(),
            "truncated": list(self.truncated),
            "skipped": [item.as_dict() for item in self.skipped],
            "source_method": self.source_method.id,
            "source_family": self.source_method.family,
        }


def build_open_web_queries(
    bundle: ProfileBundle,
    *,
    frozen_region: str,
) -> OpenWebPlan:
    """把 profile 的开放网络模板展开成**有序**的检索序列。

    Args:
        bundle: 当前 profile。模板与场馆类型词表都从这里读。
        frozen_region: 绑定到 ``{region}`` 的**冻结值**（实验 manifest 的
            ``geographic_scope``）。必填，且没有缺省值 —— 一次实验的两条臂
            必须传同一个值，所以它不该有「不传就用别的」的余地。
            **它不来自 profile**：见模块文档里的「为什么 ``{region}`` 不来自 profile」。

    Returns:
        ``OpenWebPlan``：渲染好的检索序列，加上被跳过、被截断的模板记录。

    Raises:
        ConfigError: 模板表为空、模板用了本族不允许的占位符、冻结值不可用、
            或者所有模板都被跳过（零条检索式不是「没有候选」，它是一个错误）。
    """
    region = _frozen_value(frozen_region)
    templates = load_open_web_templates(bundle)
    vocabulary: dict[str, tuple[str, ...]] = {
        "region": (region,),
        "venue_type": load_venue_types(bundle),
        "role": _canonical_terms(bundle.role_terms, where="role-matrix 的正面角色词表"),
    }

    skipped: list[SkippedOpenWebTemplate] = []
    cursors: list[tuple[str, list[str], bool]] = []
    for template in templates:
        placeholders = _placeholders(template)
        slots = [vocabulary.get(name, ()) for name in placeholders]
        if any(not values for values in slots):
            missing = tuple(
                name for name, values in zip(placeholders, slots) if not values
            )
            # 渲染不出的模板**跳过并记录**，不照发（见模块文档里那一节）：
            # 一条 `{venue_type} <冻结值> contact` 不会报错，它只会浪费一次预算，
            # 并把「我们其实没搜」记成「搜了没结果」。
            skipped.append(
                SkippedOpenWebTemplate(template=template, reason="no_vocabulary", missing=missing)
            )
            continue
        expansions, hit_bound = _expand(template, placeholders, slots)
        cursors.append((template, expansions, hit_bound))

    planned: list[OpenWebQuery] = []
    seen: set[str] = set()
    cursor_at: dict[int, int] = {position: 0 for position in range(len(cursors))}
    exhausted: set[int] = set()

    # **轮转展开，不是逐模板耗尽。** 一条模板的笛卡尔积可能比别的模板大一个数量级
    # （``{role}`` 有几十个取值，``{venue_type}`` 也有十个），逐模板展开会让它吃掉
    # 整条序列，于是「我们搜了 4 种线索」是假话 —— 那时序列里全是同一种模板的变体。
    # 轮转保证一条不变量：**任何模板都不会在别的模板之前贡献第二条。**
    # 模板为外层、场馆类型为内层（``_expand`` 的进位顺序让声明在后的占位符变得更快）。
    progressed = True
    while progressed:
        progressed = False
        for position, (template, expansions, _) in enumerate(cursors):
            if position in exhausted:
                continue
            # 跳过已经被别的模板渲染出同样文本的展开。
            while cursor_at[position] < len(expansions) and expansions[cursor_at[position]] in seen:
                cursor_at[position] += 1
            if cursor_at[position] >= len(expansions):
                exhausted.add(position)
                continue
            text = expansions[cursor_at[position]]
            cursor_at[position] += 1
            seen.add(text)
            planned.append(OpenWebQuery(text=text, template=template))
            progressed = True

    if not planned:
        # **零条检索式是一个错误，不是「没有候选」。** 一份「跑了 0 条」的报告与
        # 一份「这个市场没有可发现的组织」的报告读起来是一回事，而它们要做的事
        # 完全不同（改 profile / 改结论）。一份空的计划没有任何东西可以追问它。
        raise ConfigError(
            "开放网络展开器一条检索式都产不出来。\n"
            f"  模板 {len(templates)} 条，全部被跳过："
            f"{[item.as_dict() for item in skipped]}。\n"
            "  空序列不是「没有候选」—— 它在这里是「词表或模板不可用」，"
            "两件事在报告里长得一样，所以必须在这里就分开。"
        )

    return OpenWebPlan(
        queries=tuple(planned),
        templates_declared=len(templates),
        frozen_region=region,
        skipped=tuple(skipped),
        truncated=tuple(template for template, _, hit_bound in cursors if hit_bound),
    )


# --- 从 profile 读取模板与词表 ------------------------------------------------


def _search_section(bundle: ProfileBundle) -> Mapping[str, Any]:
    profile = bundle.profile
    if profile is None:
        raise ConfigError(
            "profile 没有装载 —— 开放网络模板只能从 profiles/<id>/profile.yaml 读。\n"
            "本族没有通用回退模板：回退会让「按 SOP 的模板检索」变成「按引擎自己"
            "想的模板检索」，而报告里看不出区别。"
        )
    section = profile.raw.get("search")
    return section if isinstance(section, Mapping) else {}


def load_open_web_templates(bundle: ProfileBundle) -> tuple[str, ...]:
    """读取并校验 profile 声明的开放网络模板，保持**声明顺序**。

    模板是**匿名的单行文本**（``- "{venue_type} {region} contact"``）：它没有 id、
    没有 priority、没有 purpose。一份带这些字段的映射声明不属于本族 —— 两族的
    声明形状刻意不共用，共用一个校验器正是 ``{domain}`` 变成合法写法的那条缝。

    空声明**不是**合法的：这一族没有通用模板可回退，
    所以「没有模板」只能是一个错误。
    """
    section = _search_section(bundle)
    declared = section.get("query_templates")
    if declared is None:
        raise ConfigError(
            "profile 的 search.query_templates 不存在 —— 开放网络这一族没有可展开的声明。\n"
            "  这一族没有通用回退模板：缺了它就发不出第一条检索式。"
        )
    if not isinstance(declared, list):
        raise ConfigError("search.query_templates 必须是列表")
    if not declared:
        raise ConfigError(
            "search.query_templates 是空列表。\n"
            "  空列表与「这个 profile 不做开放网络发现」是两种不同的东西：前者是"
            "有人写了又没填，后者是漏了。两种都不该被静默读成「没有候选」。"
        )
    for index, item in enumerate(declared):
        _validate_template(item, index=index)
    return tuple(str(item).strip() for item in declared)


def load_venue_types(bundle: ProfileBundle) -> tuple[str, ...]:
    """``{venue_type}`` 的取值表。住在 profile 里，不在引擎里。"""
    section = _search_section(bundle)
    declared = section.get("venue_types")
    if declared is None:
        return ()
    if not isinstance(declared, list):
        raise ConfigError("search.venue_types 必须是列表")
    return _canonical_terms(declared, where="search.venue_types")


def _validate_template(item: Any, *, index: int) -> None:
    """校验一条模板。**任何**取值失败都在这里失败，不留到渲染期。"""
    if not isinstance(item, str):
        raise ConfigError(
            f"search.query_templates[{index}] 必须是一行文本，实际是 {type(item).__name__}。\n"
            "  本族只认一行文本；带 id / text / priority / purpose 的映射声明属于"
            "另一族，两族的声明形状刻意不共用。"
        )
    text = item.strip()
    if not text:
        raise ConfigError(f"search.query_templates[{index}] 为空")
    _placeholders(text, index=index)


def _placeholders(text: str, *, index: int | None = None) -> tuple[str, ...]:
    """模板里的占位符（声明顺序，保序去重）。校验不过直接抛。

    ``{domain}`` 这类**账号侧**占位符与 ``{venue_typ}`` 这类拼错是两种错误，
    报错时分开说：前者是注入，后者是笔误。
    """
    tokens = _PLACEHOLDER.findall(text)
    where = f"search.query_templates[{index}]" if index is not None else "查询模板"
    remainder = _PLACEHOLDER.sub("", text)
    if "{" in remainder or "}" in remainder:
        raise ConfigError(
            f"{where} 的花括号不配对：{text!r}。\n"
            "  不配对的括号不会被下面的校验看到，它会原样进入检索式 —— "
            "而一条带花括号的检索式不会报错，只会返回一堆无关结果。"
        )
    if not tokens:
        raise ConfigError(
            f"{where} 不含任何占位符：{text!r}。\n"
            "  它对每个地理范围都会渲染成同一条检索式 —— 那是一个常量，不是模板，"
            "而且它绕过了冻结值的绑定。"
        )
    for token in tokens:
        if not _PLACEHOLDER_NAME.fullmatch(token):
            raise ConfigError(
                f"{where} 的占位符写法不认识：{{{token}}}（在 {text!r} 里）。\n"
                f"  占位符名只允许小写字母与下划线。已知的：{sorted(OPEN_WEB_PLACEHOLDERS)}。"
            )
        if token in ACCOUNT_SIDE_PLACEHOLDERS:
            raise ConfigError(
                f"{where} 用了账号侧占位符 {{{token}}}：{text!r}。\n"
                "  开放网络这一族是在**还没有账号**的时候发检索式的：这个占位符在这里"
                "取不到值，而一个与其他族共用的校验器会让它变成**合法**写法 —— "
                "账号事实被塞进发现期检索式，检索式看起来完全正常，"
                "报告里没有任何东西会变红。"
            )
        if token not in OPEN_WEB_PLACEHOLDERS:
            raise ConfigError(
                f"{where} 用了本族不认识的占位符 {{{token}}}：{text!r}。\n"
                f"  开放网络模板允许的占位符只有 {sorted(OPEN_WEB_PLACEHOLDERS)}。\n"
                "  一个拼错的占位符不会报错 —— 它会原样进入检索式，"
                "然后被当成「Google 上没有」记录下来。"
            )
    return tuple(dict.fromkeys(tokens))


def _canonical_terms(values: Iterable[Any], *, where: str) -> tuple[str, ...]:
    """把一份取值表规范化：去掉空白、去重、**按码位升序**。

    排序是「确定性」的另一半：取值表的**声明顺序不承载语义**，把它当语义会让一次
    重排悄悄改变检索序列 —— 而报告上只表现为「净新增有点不一样」。发现期的一条
    检索式也不针对某个岗位，所以这里不引入任何按岗位排序的口径。

    取值里出现花括号直接抛：那会让一条取值在检索式里长得像**占位符**，
    而它不经任何校验。
    """
    terms: list[str] = []
    for item in values:
        text = str(item).strip()
        if not text:
            continue
        if "{" in text or "}" in text:
            raise ConfigError(
                f"{where} 的取值 {text!r} 里含花括号。\n"
                "  它会被原样拼进检索式，看起来像一个没渲染出来的占位符 —— "
                "而没有任何东西会校验它。"
            )
        if text not in terms:
            terms.append(text)
    return tuple(sorted(terms))


def _frozen_value(value: Any) -> str:
    """校验调用方传入的冻结值：必须是一个可用的单值。"""
    if not isinstance(value, str):
        raise ConfigError(
            f"冻结的地理值必须是一个字符串，实际是 {type(value).__name__}（{value!r}）。\n"
            "  它是 manifest 里冻结的那一个值，不是一份候选清单 —— 两条臂各自"
            "从清单里挑一个，比较的就不再是发现能力。"
        )
    text = value.strip()
    if not text:
        raise ConfigError(
            "冻结的地理值为空 —— 缺省的「空」不是一个地理范围，"
            "它只会渲染出一堆少了地理限定的检索式，而报告里看不出区别。"
        )
    if "{" in text or "}" in text:
        raise ConfigError(
            f"冻结的地理值 {text!r} 里含花括号 —— 它会被原样拼进检索式，"
            "看起来像一个没渲染出来的占位符，而它不经任何校验。"
        )
    return text


def _expand(
    template: str,
    placeholders: tuple[str, ...],
    slots: Sequence[Sequence[str]],
) -> tuple[list[str], bool]:
    """展开一条模板：笛卡尔积，**声明在后的占位符变化更快**。

    进位顺序是「轮转」能成立的前提：一轮换一个取值，轮内几条共用一个场馆类型。
    返回 ``(展开结果, 是否撞上上限)``。
    """
    results: list[str] = []
    hit_bound = False
    indices = [0] * len(slots)
    while True:
        text = template
        for name, values, index in zip(placeholders, slots, indices):
            text = text.replace("{" + name + "}", values[index])
        results.append(text)
        position = len(slots) - 1
        while position >= 0:
            indices[position] += 1
            if indices[position] < len(slots[position]):
                break
            indices[position] = 0
            position -= 1
        if position < 0:
            break
        if len(results) >= MAX_EXPANSIONS_PER_TEMPLATE:
            hit_bound = True
            break
    return results, hit_bound


__all__ = [
    "ACCOUNT_SIDE_PLACEHOLDERS",
    "MAX_EXPANSIONS_PER_TEMPLATE",
    "OPEN_WEB_PLACEHOLDERS",
    "OPEN_WEB_SOURCE_METHOD",
    "OpenWebPlan",
    "OpenWebQuery",
    "SkippedOpenWebTemplate",
    "build_open_web_queries",
    "load_open_web_templates",
    "load_venue_types",
]

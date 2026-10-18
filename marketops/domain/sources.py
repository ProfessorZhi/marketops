"""``source_method_id`` 的**闭集**注册表 —— 每一条候选都必须回得到它的发现方法。

形状是一个 ``dict[str, 成员]``：没有插件发现、没有 entry point、没有 ``importlib``。
加一个来源要改这个文件，这是一个**特性**：它让「这一轮能有哪些来源」成为可审计的、
进了版本控制的事实。

# 为什么必填

一份候选池由**多种方法**产出的行拼成：检索结果、站内检索、站点结构、研究阶段
封存的清单、以及上游交进来的登记册导出。按 SOP L82 逐字：「搜索结果、单页新闻或
单一 contact 页只能作补充证据，不能当全量母池。」**检索**产出的那些行属于
**补充证据**，而 SOP L94 的母池最低字段第一行就是 ``source_method_id / source URL``。
每一行都带它，报告才不会被读成母池 —— 而那是 SOP 唯一明令禁止的读法。
一条没有出处的候选，与「随手挑的一组候选」在报告里长得一样。

「哪些行来自检索」这个问题的答案**只有这一栏能回答**：池子本身不带标记，
它是被这一栏分类出来的。所以它必填，而且取值是闭集。

# 为什么是闭集

因为「来源族多样性」这个指标要有依据才存在。id 一旦可以是自由文本，
每个拼写变体都是一个新族，指标只会显示「多样性很高」而没有任何东西支撑它。
未知 id **失败关闭**（抛 ``ConfigError``），而不是被接受或忽略。

**成员与族是两件事，刻意不耦合。** id 描述「这一行是怎么被发现的」，
写进证据之后就**冻结**了（改 id 等于让已经记下来的行换一种来源）；
族是**分析用的分类**，将来可能被细分或重新归并，那时不该逼着 id 跟着改。
所以族是成员身上一个独立的字段，而不是 id 的前缀。

# 值的纪律

* **市场中性**：id 与族都不含任何具体市场取值（场馆类型、地区、岗位词）。
  那些住在 ``profiles/*/`` 里。由 ``tests/test_no_hardcoded_market_knowledge.py``
  对 ``marketops/`` 整包强制执行。
* **厂商中性**：id 描述**怎么找到的**，不是**用谁找到的**。把 provider 名写进
  id（``search.<vendor>.v1`` 这类）会在换一个 provider 时改掉指标，并且让
  历史上已经记下来的那些行变成另一种来源。由 ``tests/test_discovery_sources.py``
  的 ``test_no_vendor_name_appears_in_an_id`` 钉住。

# 一层不在这里的东西 —— **未决项**（不是设计，是这一段没做完的部分）

SOP L82 说的「每个来源有独立 ``source_method_id``」指的是**具体的来源**
（某个目录、某个登记册、某个 API）。那些是市场知识，属于 profile 一侧，
不属于引擎；而且还没有一条被验证过，所以**不在这里** —— 一个没有成员的
族，或者一个没有任何东西能产出的成员，比没有它更容易被当成已经做完。
这里只覆盖「引擎自己能产出的发现方法」。

**这条缝与「发现期允许研究阶段发现新来源」这个设想之间有一处张力，
记录下来留给来源清单真正落地的那一轮。** 一个研究 Skill 可以发现**新的来源族**
（新的目录、登记册、聚合站），而那些来源按划分属于 ``profiles/*/`` 一侧的市场
知识 —— 它们既不是「引擎自己能产出的发现方法」（本模块的范围），也不是**任何**
注册表今天能表达的东西：本模块的 id 描述「这一行是怎么被发现的」，而客户/市场
层面的来源清单是**数据**，随注册与运营变化，跟 ``profiles/*/`` 的其它词表一样。

两边今天**没有对齐**，而且还没有验证过任何来源族，所以：

* **不要**凭想象往这张表里加成员 —— 那是臆造，会把「一个没人验证过的来源」
  写成一条已经存在的方法，而报告上它看起来与验证过的一样。
* **也不要**把族名当成能自由扩展的：它是闭集（:data:`SOURCE_FAMILIES`），
  因为「来源族多样性」这个指标要有一个固定的分母才存在。
* 那一轮要做的是先决定**谁来拥有来源清单**（引擎还是 profile），再决定
  这张表是「引擎方法的闭集」还是「引擎方法 ∪ profile 声明的来源」——
  后者的每一次扩表都必须由一次证据（这个来源真的产出过一行）触发。

## 这一节的现状（2026-09-23，母池入口落地时更新）

上一条的**触发条件已经满足了一次**：Member-C 自己说过母池是什么
（``sources/member-c-private`` 的 pos30：「纯 osm 只有代码 id，很少能跑到具体
场地名称，**所以还需要在 osm 基础上补齐**」），而母池入口的实现必须给每一行
一个说得出来的来源方法 —— 于是加了 ``import.register.v1``：它描述的是
「上游交进来一份公开登记册导出、本仓把它读成回执」，**引擎自己能产出**，
落在上面那条「引擎方法的闭集」的范围里。

**那一轮要决定的事**（谁来拥有**具体来源**的清单）仍然没做，也不该在这一轮做：
``import.register.v1`` 里没有、也不会有「哪一份登记册」—— 那是市场侧数据，
属于 ``profiles/*/`` 一侧，而 profile 今天仍然没有任何地方能声明它。
上面那条张力因此原样留着，只是现在少了一处「什么都表达不出来」的空白。

**「凭想象加成员」的判据没有变**，而且它现在有一个可核对的先例：
``import.register.v1`` 的触发是一次真实的、写在 primary source 里的需求。
下一个成员同样要拿得出一条这样的需求，否则它只是把「一个没人验证过的来源」
写成一条已存在的方法。

生产者的构造入口是 ``marketops/domain/candidate.py::new_candidate``：它收这张表里的
一个成员并由它派生 id，因此绕过注册表写一个来源 id 在类型层面写不出来。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .errors import ConfigError


@dataclass(frozen=True)
class SourceMethod:
    """一个发现方法：它的 id、它属于哪一族、以及它能回答什么。

    ``answers`` 是一句给人读的话（「这一行是怎么来的、它能支持什么结论」）——
    报告里出现一个 id 而没有人说得清它意味着什么，那个 id 就只是个装饰。
    """

    #: 冻结的发现方法 id。
    id: str
    #: 它属于哪一族。必须是 :data:`SOURCE_FAMILIES` 里的一个。
    family: str
    #: 它能回答什么 / 它的边界在哪里。一句话。
    answers: str


#: 族的**闭集**。一个拼错的族名会变成一个凭空多出来的族，于是多样性虚高。
#:
#: 三族的区别是「这一行是从哪儿来的」，不是「它好不好」：
#: ``public_search`` 能扩池（找到此前不知道的单位）；``known_domain`` 只在
#: 已知单位内部补内容，扩不了池；``research`` 是研究阶段封存的清单。
#:
#: ``public_search`` 的判据是**能不能扩池**，不是**有没有经过一次检索** ——
#: 一份上游公开登记册的导出读进来之后，这个池子确实变大了，而那正是这一族
#: 在报告里承担的那件事。族是分析用的分类（见上），所以这一句改的是
#: **分类的边界**，不是任何 id 的取值：id 一个都没有动。
SOURCE_FAMILIES: frozenset[str] = frozenset({"public_search", "known_domain", "research"})

#: 唯一权威的注册表。``AccountCandidate.source_method_id`` 的取值必须在这里。
SOURCE_METHODS: dict[str, SourceMethod] = {
    "search.engine.v1": SourceMethod(
        id="search.engine.v1",
        family="public_search",
        answers=(
            "这一行来自面向公共网络的检索结果。它能回答「这个单位此前不在池子里」；"
            "它不能回答「这个池子覆盖了多少」—— 检索的覆盖是估计不出来的。"
        ),
    ),
    "import.register.v1": SourceMethod(
        id="import.register.v1",
        family="public_search",
        answers=(
            "这一行来自**上游交进来的一份公开登记册导出**（按对象类型与 ID 编号的那些，"
            "以及各种表格导出），本仓把它读成一份来源回执。它能回答「这个单位此前不在"
            "池子里」；它**不能**回答「这个池子覆盖了多少」—— 一份登记的完整边界由"
            "登记方定义，不由这一行的有无推出。它与 ``search.engine.v1`` 同族"
            "（都能扩池），但**不是同一种方法**：没有一次查询发生过，所以任何"
            "「检索了几次」「覆盖了多少查询」的读数在这一行上都为真不了。\n"
            "具体是**哪一份**登记册，是市场侧的数据，属于 ``profiles/*/`` 一侧，"
            "不在这个 id 里 —— 本模块的 id 只描述「引擎是怎么拿到这一行的」。"
        ),
    ),
    "search.engine.site_scoped.v1": SourceMethod(
        id="search.engine.site_scoped.v1",
        family="known_domain",
        answers=(
            "这一行来自把检索限定在**已知域名**内。它能给已知单位补内容；"
            "它**不能**扩池 —— 一个还没被发现的单位不会因此出现。"
        ),
    ),
    "crawl.site.v1": SourceMethod(
        id="crawl.site.v1",
        family="known_domain",
        answers=(
            "这一行来自已知域名自身的站点结构（robots / sitemap）。与站内检索同族："
            "补内容，不扩池。"
        ),
    ),
    "research.skill.v1": SourceMethod(
        id="research.skill.v1",
        family="research",
        answers=(
            "这一行来自研究阶段封存的候选清单。它能回答"
            "「这一行在研究清单上」；它不能回答「研究是怎么找到它的」——"
            "那属于研究阶段的记录，不在这一层。"
        ),
    ),
}


def resolve_source_method(source_method_id: str) -> SourceMethod:
    """把一个 ``source_method_id`` 解析成注册表里的成员。

    Raises:
        ConfigError: id 不在注册表里。**失败关闭**：静默接受一个未注册的 id，
            等于让「来源」这一栏可以随便写，而「来源族多样性」仍然会显示成一个数。
    """
    try:
        return SOURCE_METHODS[source_method_id]
    except KeyError:
        raise ConfigError(
            f"未知的来源方法 {source_method_id!r}；已注册的只有 {sorted(SOURCE_METHODS)}。"
            "来源方法是一个闭集 —— 想加新的，先改 marketops/domain/sources.py。"
        ) from None


def source_family(source_method_id: str) -> str:
    """这个发现方法属于哪一族。未知 id 同样失败关闭。"""
    return resolve_source_method(source_method_id).family


def source_family_counts(source_method_ids: Iterable[str]) -> dict[str, int]:
    """按族统计候选行数 —— 「来源族多样性」这个指标唯一算得出来的依据。

    未知 id 抛错而不是跳过：少掉的那一行看起来像「这个来源没有产出」。
    """
    counts: dict[str, int] = {}
    for method_id in source_method_ids:
        family = source_family(method_id)
        counts[family] = counts.get(family, 0) + 1
    return counts


__all__ = [
    "SOURCE_FAMILIES",
    "SOURCE_METHODS",
    "SourceMethod",
    "resolve_source_method",
    "source_family",
    "source_family_counts",
]

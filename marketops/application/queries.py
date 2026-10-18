"""查询构造。

这里的模板刻意是**通用**的（organization / contact / email / site: / filetype:pdf）。
市场专属的查询模板属于业务规则，而 SOP 已经把它声明在
``profiles/<profile>/profile.yaml`` 的 ``search`` 段
（``query_templates`` / ``venue_types`` / ``regions``）。

**通用模板仍然不读 ``query_templates``。** 那一族模板里的 ``{role}`` /
``{venue_type}`` / ``{region}`` 是**开放网络发现**那一轮的原料，展开器住在
``application/openweb.py``（自带占位符闭集），不在这里 —— 两族模板共用一台校验器
会让 ``{domain}`` 变成发现期检索式里的合法占位符，也就是允许把账号事实注入一次
看起来完全正常的检索。

**但 ``search.locales`` 从本模块起有了消费者**（``market_locale``）：上面那两个
通用模板答不出「这次检索问的是哪个市场」，而那一格必须由声明给出。见下面
:func:`market_query`。

接线时请不要删掉通用模板：它们是「profile 未提供规则」时的安全默认，也是跨
profile 可比的基准线。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.errors import ConfigError
from ..domain.evidence import AccountRecord
from ..domain.profile import ProfileBundle
from ..domain.util import (
    check_market_country,
    check_market_language,
    primary_language_subtag,
)
from ..ports.search import SearchQuery


@dataclass(frozen=True)
class MarketLocale:
    """一次检索问的**市场**：哪个国家的索引 + 哪种语言。

    这两个值不是从检索式里推出来的，也不是 provider 的缺省值 —— 它们来自
    profile 的声明（``country`` 与 ``search.locales``），并且**必须写出来**。
    理由写在 ``ports/search.py::SearchQuery`` 的 docstring 里：缺省值会让「我们
    问的是美国索引」这句话在回执上完全看不见，而看起来一切正常。

    两个字段各自校验（形状与大小写，见 ``domain/util.py``）：这一格会进幂等键与
    镜像文件名，所以 ``gb`` 与 ``GB`` 是两个不同的值、两条不同的账。
    """

    country: str
    language: str

    def __post_init__(self) -> None:
        check_market_country(self.country, "MarketLocale.country")
        check_market_language(self.language, "MarketLocale.language")


def market_locale(bundle: ProfileBundle) -> MarketLocale:
    """从 profile 的声明里读出**这个部署问哪个市场**。

    **读不到就抛，不猜。** 三处声明各有各的缺法，所以各有各的回执：profile 没有
    声明块、``country`` 空着、``search.locales`` 一条都没写 —— 它们都是「这份
    profile 说不清它问的是哪个市场」，而这句话必须在这里说出来。回退到
    provider 缺省（美国索引、英文）会让一次英国检索静默地按另一个市场跑，报告上
    看不出任何区别。

    **一条查询只能问一种语言。** ``search.locales`` 声明了多种语言时抛
    ``ConfigError``，而不是取第一条：取第一条是**静默丢掉一条已声明的东西**，
    而按 N 种语言展开会让「一条检索式」变成 N 次计费调用 —— 那要动的是额度与账本
    （每个语言各预留一次），属于另一处改动，不该由这里悄悄做掉。装载期允许
    ``locales: [en, fr]``（那是「这个 profile 需要两种语言」的诚实记录），
    使用期拒绝它，回执点名缺的那件事。
    """
    declared = bundle.profile
    if declared is None:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 没有声明块，所以这次检索说不出它问的是"
            "哪个市场。\n"
            "``country`` 与 ``search.locales`` 都在那一块里，而它们没有缺省值 ——"
            "回退到 provider 的缺省国家会让一次按 A 市场的检索静默地按 B 市场跑，"
            "而两次的回执逐字同形。"
        )

    country = declared.country.strip()
    if not country:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 的 country 是空的 —— 这次检索说不出它"
            "问的是哪个国家的索引。\n"
            "它必须是两位大写 ISO-3166 国家码（见 domain/util.py 的 "
            "``check_market_country``）。"
        )
    # 形状在这里查，字段名点的是 **profile 那一格**而不是 ``MarketLocale``：
    # 写坏它的人在 profile.yaml 里，回执必须指向那里（与 ``search_locales`` 同一条）。
    # ``check_market_country`` 抛 ``ValueError``（它是一个值校验器），而这里是一次
    # **声明读坏了** —— 换报成 ``ConfigError``，否则调用方会把它当成「传进去的值
    # 不合法」去改代码，而该改的是 profile.yaml。
    try:
        check_market_country(country, "profile.country")
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    locales = declared.search_locales
    if not locales:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 没有声明 search.locales —— 这次检索"
            "说不出它问的是哪种语言的索引。\n"
            "空着与「不需要语言」不是同一件事：不写这一格，检索式就会按 provider "
            "那一侧的缺省语言发出，而回执上看不出来。"
        )

    primaries = tuple(dict.fromkeys(primary_language_subtag(tag) for tag in locales))
    if len(primaries) > 1:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 的 search.locales 声明了多种语言"
            f"（{'、'.join(primaries)}），而一次检索只问得了一种。\n"
            "按 N 种语言展开意味着一条检索式变成 N 次计费调用 —— 那要连额度预留与"
            "账本一起改（每个语言各预留一次），不能在这里悄悄做掉：报告上的"
            "「发出去几条」会凭空变成原来的 N 倍。"
        )

    return MarketLocale(country=country, language=primaries[0])


def market_query(
    bundle: ProfileBundle, *, text: str, scope: str, purpose: str = "general"
) -> SearchQuery:
    """**唯一**一条把检索式变成 :class:`SearchQuery` 的路。

    ``country`` / ``language`` 两个必填格只从 :func:`market_locale` 来。直接在别处
    构造 ``SearchQuery`` 也能过（两个字段是必填的，缺一个就构造不出来），但那时
    填进去的是什么就只有那个调用点知道 —— 而这两个格决定的是幂等键、镜像文件名
    与 provider 的国家/语言参数。所以「除本模块之外不许直接构造」这条纪律有一个
    执行器：``tests/test_search_market_locale.py`` 按 AST 扫全仓的
    ``SearchQuery(...)`` 调用点，本文件之外一个都不许有。

    Args:
        bundle: 当前 profile —— 市场从它的声明里读，不从参数里收。
        text: 检索式。市场不进检索式（``gl``/``hl`` 由适配器发），所以这里只收文本。
        scope: 这一行记在谁的账上（``SearchQuery.scope`` 有校验）。
        purpose: 事后统计用（「哪些检索式真正产出了东西」）。
    """
    locale = market_locale(bundle)
    return SearchQuery(
        text=text,
        scope=scope,
        country=locale.country,
        language=locale.language,
        purpose=purpose,
    )


def build_queries(
    account: AccountRecord,
    bundle: ProfileBundle,
    *,
    include_site_queries: bool = True,
) -> list[SearchQuery]:
    """为目标账号构造检索式。

    Args:
        account: 目标组织。
        bundle: 当前 profile。**没有缺省值** —— 市场那两格只能从它读出来，而
            ``bundle=None`` 的缺省形状正好是「这次检索不用说出它问的是哪个市场」，
            也就是让一次按英国问、一次按美国问的回执逐字同形。
        include_site_queries: 有 ``domain`` 时是否加 ``site:`` 限定检索。
    """
    organisation = account.organisation.strip()
    if not organisation:
        return []

    scope = account.account_id
    queries: list[SearchQuery] = [
        market_query(bundle, text=organisation, scope=scope, purpose="general"),
        market_query(
            bundle, text=f"{organisation} contact", scope=scope, purpose="contact"
        ),
        market_query(
            bundle, text=f"{organisation} email", scope=scope, purpose="contact"
        ),
    ]

    domain = account.domain.strip().lower()
    if include_site_queries and domain:
        queries.extend(
            [
                market_query(
                    bundle,
                    text=f"site:{domain} contact",
                    scope=scope,
                    purpose="contact",
                ),
                market_query(
                    bundle,
                    text=f"site:{domain} filetype:pdf",
                    scope=scope,
                    purpose="pdf",
                ),
            ]
        )

    # 去重且保序：profile 模板接入后可能与通用模板产生重复。
    seen: set[str] = set()
    unique: list[SearchQuery] = []
    for query in queries:
        if query.text in seen:
            continue
        seen.add(query.text)
        unique.append(query)
    return unique


__all__ = ["MarketLocale", "build_queries", "market_locale", "market_query"]

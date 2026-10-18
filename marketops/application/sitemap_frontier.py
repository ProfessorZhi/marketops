"""站点地图前沿：robots.txt → 站点地图 → profile 的页面优先级。

``crawl_site`` 与 ``fetch_document`` 的分工一句话说完：后者取一个 URL 的字节，
本模块决定去取**哪些** URL，而且**只**决定这一件事。

## 为什么「怎么取字节」不在这里

取字节只有一条路，它已经存在：``application/acquisition.py`` 的
``DocumentAcquisition``（§10 的统一入口，含 SSRF 判据、升级判据、落盘与分类）。
为多页抓取写第二条取字节的实现，就是本仓明文禁止的**两份真实实现** —— 它们会
漂移，而漂移的那一份不会报错，只会在某个站上悄悄给出另一种结果。

所以本模块的类型里**没有** ``body_path``、没有 tier、没有 ``FetchResult``。它读网
全部经过注入的 ``HttpFetcher``（robots.txt 与站点地图各一次），因此在**零网络、
零磁盘**下可以把整条路径跑完 —— ``tests/test_sitemap_frontier.py`` 里没有一条判据
联网。

## 为什么它住在 ``application/`` 而不是 ``adapters/``

它不 import 任何厂商模块，也不碰磁盘与进程。按 ``adapters/__init__.py`` 开头那条
判据 —— 「把它 import 进来会不会需要磁盘、网络或一个进程？会，就是 adapter」——
它是**策略**，与同住这一层的 ``document_router`` 同类（那一条判例写在
``application/__init__.py`` 里）。谁给它 ``HttpFetcher`` 是接线，在
``interfaces/composition.py``。

本模块也是 ``application/`` 里**第一个** import ``marketops.security`` 的。这不需要
破例：``security/network.py`` 的 ``check_target`` 是那条判据（不允许被信任的输入，
不因为「服务内部产生的」而自动可信）的唯一实现，而 ``security/__init__.py`` 自述
它不属于任何一层、就是给各层调的。在计划阶段先判一次的收益是报告能把一条私网地址
记成 ``unsafe_target``，而不是让它消失在 fetcher 的拒绝里。``application/acquisition.py``
那句「安全判据不在这里重新实现」仍然成立 —— 这里调的是同一个函数，不是第二份判据。

## 计划是**候选**，不是授权

:meth:`SitemapFrontier.plan` 的产物是一份建议。逐条 URL 的最终放行在每一层各有
其人：``../security/network.py`` 的 ``check_target``（安全边界，在发请求之前硬拦）、
robots 策略（站点的访问规则）、以及 profile 的 ``page_discovery_hints``（业务优先
级）。凡是**没进计划**的 URL，本模块都要求说得出是哪一条拦下的 —— 见
:class:`SkipReason`，它存在的理由就是「静默丢掉」在本仓是不可接受的行为。

## 它消费两条此前没有执行器的声明

``page_discovery_hints`` 是「先抓哪些 URL」的分级表，装进 ``ProfileBundle`` 之后
一直没有消费者（``docs/SOP_MAPPING.md`` §3 记着这件事）；``compliance.respect_robots_txt``
更彻底 —— 唯一提到它的地方是一条断言它自己是 ``True`` 的测试
（``tests/test_profiles.py``）。**一条边界没有执行器就只是自觉**，本模块是这两条
的落地。

## 与 v1 的 ``search/crawlplan.py``：语义回收，外加修掉它一个真缺陷

框架照旧：robots.txt → 站点地图（含 sitemapindex）→ 按 hints 分级排序 → 计划。
输入换了一个：那一份要一个 ``AccountRecord``，本模块要一个 URL 种子（研究期工具
不认识账号）。

**修掉的那一条**：v1 只读 robots.txt 里的 ``Sitemap:`` 行，**完全不看
``Disallow``** —— 它把 robots.txt 当站点地图索引，不当访问策略。那在「一次抓一个
已知域名的主页」的语境里勉强说得过去，在一个抓多页的工具里不成立：不读 Disallow
的多页抓取就是爬虫。所以这里有 :func:`parse_robots` 与 :meth:`RobotsPolicy.allows`。

## robots.txt 的三个反直觉之处，逐条写下来

1. **只应用 ``User-agent: *`` 那一组。** 我们发的是一个浏览器 UA，按 token 去匹配
   ``User-agent: Googlebot`` 那种组是自欺 —— 那一组不是写给我们的，照它执行会得到
   一个站点从未对我们表达过的许可。``*`` 组是站点对所有人的声明，唯一诚实的选择。
   未实现的（记在这里，因为它们不是遗漏而是取舍）：``Crawl-delay`` / ``Request-rate``
   （非 RFC 9309 规范，且本次抓取本身有页数上限，不是负载来源）。
2. **取不到 robots 时，两种失败的结论相反。** 404 表示这个站**没有** robots 文件
   （RFC 9309 §2.3.1.3：视为没有规则）；而 5xx / 超时 / 被 WAF 拦下表示**取不到**，
   按 §2.3.1.4 假设全站拒绝。合成一句「robots 读不到」会让「没有这个文件」与
   「这个站在拒我们」得到同一个处置，而后者正是不该继续抓的那一种。
3. **robots.txt 自己不受 robots 约束**（否则无法启动），所以读它这一步不过 ``allows``，
   但仍然过 ``check_target``（它是 ``HttpxFetcher`` 发的请求，判据在那里）。

## 同站怎么判：只看主机名，不做公共后缀

``example.com`` 与 ``www.example.com`` 算同一个站（去掉一个前导 ``www.`` 再比）。
子域**不算** —— ``sport.example.com`` 是另一个站，它有它自己的 robots.txt，而按种子
那一份去执行它等于用一个站点没有表达过的规则替它表态。想要它，就把它当一个种子
再抓一次。

一个写下来的假设：那两个拼写指向不同 robots 文件的部署是可能的，而那时我们按
**种子那个拼写**取到的那一份执行。不做公共后缀表是取舍，不是疏漏。

## 本轮**不做** URL 规范化

去重按 URL 原始字符串。``?page=1`` 与 ``?page=1&`` 是两条，就像 ``http.py`` 的
镜像按字节精确命名一样。理由与 ``docs/SOP_MAPPING.md`` 记的那次实测相同：规范化
的规则一旦写错，它会把两个不同的页面合成一个，而报告上什么都不会显示。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from ..domain.enums import FetchOutcome
from ..ports.acquisition import FetchRequest, FetchResult, HttpFetcher
from ..security.network import check_target

#: 一次计划最多抓几份站点地图文档（含 sitemapindex 的子图）。站点地图可以指向
#: 上千个分片，不封顶的话「一次有界抓取」会变成一次小型爬站。
MAX_SITEMAP_DOCUMENTS = 5

#: 一个站点地图最多看多少条 URL。超过就截断并**记下来** —— 计划本来也装不下，
#: 但「有多少条没看」与「这个站只有这么多页」必须分得开。
MAX_POOLED_URLS = 5000

_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


# ----------------------------------------------------------------------
# robots.txt
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class RobotsPolicy:
    """一台主机的访问策略。**只有 ``User-agent: *`` 那一组**（见文件头）。

    ``deny_all`` 不是「有一条 ``Disallow: /``」——那会由 ``rules`` 自然得出。
    它是**整份策略不可信**：取不到 robots 且原因是服务端/传输问题，或站点在
    robots 这一跳就把我们拦了。它比任何一条具体规则都强，因为那时我们连
    「站点声明了什么」都不知道。
    """

    sitemaps: tuple[str, ...] = ()
    #: ``(pattern, allow)``，按文件出现顺序。
    rules: tuple[tuple[str, bool], ...] = ()
    deny_all: bool = False

    def allows(self, url: str) -> bool:
        """这条 URL 允不允许抓。最长匹配胜出，等长时 **Allow 胜**（RFC 9309 §2.2.2）。"""
        if self.deny_all:
            return False
        path = _path_and_query(url)
        best_length = -1
        best_allow = True
        for pattern, allow in self.rules:
            if not _rule_matches(pattern, path):
                continue
            if len(pattern) > best_length:
                best_length, best_allow = len(pattern), allow
            elif len(pattern) == best_length:
                # 等长 —— Allow 胜。这一条是 RFC 明文规定的，而按「先出现的胜」
                # 实现会让同一个站的两份 robots 得到相反的结论。
                best_allow = best_allow or allow
        return best_allow


def _path_and_query(url: str) -> str:
    """robots 规则匹配的那一段：路径 + 查询串（含前导 ``/``）。"""
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    return f"{path}?{parts.query}" if parts.query else path


def _rule_matches(pattern: str, path: str) -> bool:
    """一条规则命不命中。``*`` 是通配，结尾的 ``$`` 是锚定。"""
    body = pattern
    anchored = body.endswith("$")
    if anchored:
        body = body[:-1]
    if not body.startswith("/"):
        # 规范里规则路径应当以 ``/`` 开头。不这么写的站点不少，而补一个斜杠是
        # 唯一能让它表达出作者意图的读法（否则 ``private`` 永远匹配不上 `/private`，
        # 那条 Disallow 会静默变成「什么也没禁」）。
        body = "/" + body
    expression = re.escape(body).replace(r"\*", ".*")
    return re.compile(expression + (r"\Z" if anchored else r".*"), re.DOTALL).match(path) is not None


def parse_robots(text: str) -> RobotsPolicy:
    """读一份 robots.txt。

    只取两类东西：``Sitemap:`` 行（整份文件级，不受组约束），与 ``User-agent: *``
    组里的 ``Allow`` / ``Disallow``。空值的 ``Disallow:`` 表示「什么都不禁」，
    不是一个「禁止空路径」的规则 —— 把它读成规则会让一个标准写法变成全站拒绝。
    """
    sitemaps: list[str] = []
    rules: list[tuple[str, bool]] = []
    in_star_group = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            # 出现在任何 user-agent 之前的规则不属于任何组（RFC 9309 §2.2.1），
            # 因此 ``in_star_group`` 的初值是 False 而不是 True。
            in_star_group = value == "*"
        elif key == "sitemap":
            if value and value not in sitemaps:
                sitemaps.append(value)
        elif key in ("allow", "disallow"):
            if not in_star_group or not value:
                continue
            rules.append((value, key == "allow"))
    return RobotsPolicy(sitemaps=tuple(sitemaps), rules=tuple(rules))


# ----------------------------------------------------------------------
# 站点地图
# ----------------------------------------------------------------------
def parse_sitemap(text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """解析站点地图，返回 ``(页面 URL, 子站点地图, 压缩包)``。

    只认 ``<loc>``，不校验命名空间 —— 站点地图的方言多到不值得为每一种写分支，
    而要的信息只有 URL 本身。顺序按文件里的出现顺序保留：站点地图的作者通常把
    重要页面排在前面，这个先验是免费的。

    第三项单独列出 ``.gz``：一个压缩的站点地图按文本读会得到一堆二进制噪音，
    而噪音里**没有** ``<loc>``，于是它会被读成「这份站点地图是空的」。说不出来的
    那一种要记下来，不能读成一堆看不出问题的垃圾。
    """
    pages: list[str] = []
    children: list[str] = []
    gzipped: list[str] = []
    for match in _LOC.findall(text):
        location = match.strip()
        if not location:
            continue
        lowered = location.lower()
        if lowered.endswith(".gz"):
            gzipped.append(location)
        elif lowered.endswith(".xml"):
            children.append(location)
        else:
            pages.append(location)
    return tuple(pages), tuple(children), tuple(gzipped)


def sitemap_candidates(origin: str, policy: RobotsPolicy) -> tuple[str, ...]:
    """先看 robots 声明的，读不到就退回约定位置。

    退回是**有据**的：``/sitemap.xml`` 是 robots 出现之前的约定，绝大多数站即使
    不写 ``Sitemap:`` 也仍然提供它。但退回不能变成编造 —— 抓不到就是抓不到，
    记进 ``gaps``，不记成「该站没有可抓页面」。
    """
    if policy.sitemaps:
        return policy.sitemaps
    return (f"{origin}/sitemap.xml",) if origin else ()


# ----------------------------------------------------------------------
# profile 的页面优先级
# ----------------------------------------------------------------------
def _match_target(url: str) -> tuple[str, str]:
    """把 URL 折成被匹配的字符串，以及它的路径。

    匹配 ``netloc + path``（去掉 scheme 与查询）：profile 里的 glob 是以 ``*/``
    开头的路径形态，而带 ``www.`` 的拼写也属于同一个站。
    """
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return f"{netloc}{path}".lower(), path.lower()


def _pattern_matches(pattern: str, target: str, path: str) -> bool:
    """一条 glob 是否命中。

    ``*`` 是唯一通配符。``*/`` 单独处理：它指的是**站点根**，而按通配符直译会
    命中任何以 ``/`` 结尾的路径 —— 那几乎等于「全部」，会让「首页」那条提示变成
    一条把整站放进最高优先级的规则。
    """
    normalized = pattern.strip()
    if not normalized:
        return False
    if normalized in {"*/", "*"}:
        return path in {"", "/"}
    expression = re.escape(normalized).replace(r"\*", ".*")
    return re.match(expression + r"\Z", target) is not None


def match_tier(url: str, hints: Mapping[str, Sequence[str]]) -> tuple[str, str] | None:
    """URL 落在哪一级，以及命中的是哪条 glob。没命中返回 ``None``。

    **按 profile 声明的键顺序**找，命中即返回 —— 优先级顺序是 ``profile.yaml``
    里那几行的顺序，不是代码里另写一份的常量。一份把 ``critical`` 写在 ``high``
    前面的 profile 会得到那个顺序，而这是 intent：分级表是业务声明，它的顺序
    属于声明本身。

    命中即返回意味着一条同时命中两级的 URL 取更靠前的那级，于是分级是 URL 的
    函数，而不是遍历顺序的函数。
    """
    target, path = _match_target(url)
    for tier, patterns in hints.items():
        for pattern in patterns:
            if _pattern_matches(str(pattern), target, path):
                return str(tier), str(pattern)
    return None


# ----------------------------------------------------------------------
# 主机名
# ----------------------------------------------------------------------
def host_of(url: str) -> str:
    """URL 的主机名，小写，去掉尾点。拿不到就空串。"""
    try:
        return (urlsplit(url.strip()).hostname or "").lower().rstrip(".")
    except ValueError:  # pragma: no cover - urlsplit 对畸形端口会抛
        return ""


def site_key(host: str) -> str:
    """站点的身份：去掉**一个**前导 ``www.``。

    只去一个：``www.www.example.com`` 是另一个主机名，把它也折进来会让「同一站」
    的判据变成一条没有边界的后缀规则。
    """
    candidate = (host or "").strip().lower().rstrip(".")
    return candidate[4:] if candidate.startswith("www.") else candidate


def same_site(seed_host: str, url: str) -> bool:
    """``url`` 是不是 ``seed_host`` 那个站上的。"""
    return bool(seed_host) and site_key(host_of(url)) == site_key(seed_host)


def origin_of(url: str) -> str:
    """``scheme://host``（无路径、无查询）。拿不到主机名就空串。"""
    host = host_of(url)
    if not host:
        return ""
    scheme = (urlsplit(url.strip()).scheme or "https").lower()
    return f"{scheme}://{host}"


# ----------------------------------------------------------------------
# 计划的形状
# ----------------------------------------------------------------------
class SkipReason(str, Enum):
    """一条 URL **没有**进计划的原因。闭集。

    每一条都对应一种不同的处置，所以它们不能合成一个「跳过」：

    * ``BY_ROBOTS`` —— 站点明确说了不要抓。**不是**「这个站没有这个页面」。
    * ``OFF_SITE`` —— 不是这个站的页面（聚合域名、社交主页、别家的站）。
    * ``UNSAFE_TARGET`` —— 安全判据拒了（私网 / 保留名字空间 / 数字形态主机名）。
    * ``DUPLICATE`` —— 同一个 URL 在池子里出现了不止一次（站点地图把自己列进去
      是常事，所以这个计数为 1 不代表有问题）。
    * ``UNMATCHED`` —— 没有任何一条 ``page_discovery_hints`` 命中。它的长期读数
      是「profile 的提示够不够用」。
    * ``BEYOND_LIMIT`` —— 命中且有优先级，但排在本次的上限之外。这一条与
      ``UNMATCHED`` 必须分开：前者是「这次的额度不够」，后者是「规则里没有它」。

    ``BEYOND_LIMIT`` 与 ``CrawlPlan.truncated`` 是同一件事的两面（一个是计数、
    一个是那句理由），而**两者都必须存在**：只有计数时读不出是哪一条上限设的，
    只有理由时读不出丢了多少条。
    """

    BY_ROBOTS = "by_robots"
    OFF_SITE = "off_site"
    UNSAFE_TARGET = "unsafe_target"
    DUPLICATE = "duplicate"
    UNMATCHED = "unmatched"
    BEYOND_LIMIT = "beyond_limit"


@dataclass(frozen=True)
class CrawlTarget:
    """计划里的一条 URL。

    ``tier`` 是命中的优先级键（profile 的 ``page_discovery_hints`` 里那个词），
    种子那一行是空串 —— 种子不是按提示选出来的，它是调用方**指定的**，
    给它编一个优先级等于伪造一条规则。
    """

    url: str
    tier: str = ""
    #: 为什么它在计划里：命中的 glob，或 ``seed``。
    why: str = ""


@dataclass(frozen=True)
class CrawlPlan:
    """一份确定性的抓取候选清单，附带足够的审计信息。

    ``tier_hits`` **保留计数为零的那些优先级**：某一级一条都没命中，正是
    「profile 里那条提示在这个站上没有对应页面」的直接读数，而按非零过滤会把
    它删成一个看不出区别的空缺。

    ``truncated`` 是上限截断的**理由**（空串表示没有截断）。计划被腰斩而不留
    痕迹时，报告上「这个站只有这么多页」与「我们只看了这么多页」同形。
    """

    seed: str
    targets: tuple[CrawlTarget, ...] = ()
    #: 抓了几份站点地图文档（含 sitemapindex 的子图）。
    sitemap_documents: int = 0
    #: 站点地图里见到的 URL 总数（去重之后）。
    pooled: int = 0
    #: 各优先级命中条数，按 profile 声明的顺序。
    tier_hits: Mapping[str, int] = field(default_factory=dict)
    #: 没进计划的各种原因的计数，键是 :class:`SkipReason` 的值。
    skipped: Mapping[str, int] = field(default_factory=dict)
    truncated: str = ""
    #: 说得出为什么的那些事（robots 取不到、站点地图是空的……）。
    gaps: tuple[Mapping[str, Any], ...] = ()

    @property
    def urls(self) -> tuple[str, ...]:
        return tuple(target.url for target in self.targets)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "urls": list(self.urls),
            "url_count": len(self.targets),
            "sitemap_documents": self.sitemap_documents,
            "pooled": self.pooled,
            "tier_hits": dict(self.tier_hits),
            "skipped": dict(self.skipped),
            "truncated": self.truncated,
            "gaps": [dict(gap) for gap in self.gaps],
        }


# ----------------------------------------------------------------------
# 前沿
# ----------------------------------------------------------------------
class SitemapFrontier:
    """robots.txt + 站点地图 + profile 的页面优先级。

    Args:
        http: 抓 robots.txt 与站点地图文档用的抓取器。**注入的**，而且必须是
            页面抓取用的那一个：两处各起一个 fetcher，会让超时、UA、重试策略
            变成一条没人观察的差异，而它只在这条路径上生效。
        hints: ``profile.page_discovery_hints``。**空表是拒绝，不是「不过滤」**：
            没有分级就没有「先抓哪些」的规则，于是任何顺序都无从解释。
    """

    name = "sitemap"

    def __init__(self, *, http: HttpFetcher, hints: Mapping[str, Sequence[str]]) -> None:
        self._http = http
        self._hints: dict[str, tuple[str, ...]] = {
            str(tier): tuple(str(pattern) for pattern in patterns)
            for tier, patterns in (hints or {}).items()
        }

    def plan(self, seed: str, *, limit: int, account_id: str = "") -> CrawlPlan:
        """产出至多 ``limit`` 条计划。

        Args:
            seed: 调用方指定的入口 URL。它**不是**一条授权：它会和站点地图里的
                URL 走同一套判据（robots、安全、同站）。
            limit: 上限。由服务端给，不是调用方的参数 —— 调用方请求的是「至多
                多少页」，而那个数字的**上界**住在服务面的处理器里
                （``adapters/mcp_server/research.py``），不在调用载荷里。
            account_id: 这次抓取**记在谁的名下**，也是镜像的目录名。空串表示
                不属于任何账号（落进 ``unnamed``），含义与
                ``FetchRequest.scope`` 一致。

        Returns:
            一份 ``CrawlPlan``。任何「计划是空的」都必须带一条 ``gaps`` ——
            本方法**不得**让「什么都没抓到」表现为一份没有任何说明的空计划。
        """
        seed = (seed or "").strip()
        if limit < 1:
            raise ValueError("limit 必须至少为 1；上限从 0 开始的计划不是一份计划")

        # 1. 种子的形状与安全。**在任何 I/O 之前** —— 一个不安全的目标与
        #    「本机装了没有」无关，也不该先花一次请求去发现。
        verdict = check_target(seed) if seed else None
        if verdict is None or not verdict.allowed:
            return CrawlPlan(
                seed=seed,
                gaps=(
                    {
                        "gap": "seed_refused",
                        "detail": "种子 URL 被安全判据拒绝，因此一次请求都没有发出。"
                        + (verdict.detail if verdict is not None else "url 为空"),
                    },
                ),
            )

        # 2. 没有分级表就拒绝。放在 I/O 之前是为了让「这个部署没声明提示」与
        #    「这个站的站点地图是空的」不会在报告上同形 —— 前者是配置问题，
        #    后者是站点事实，处置完全不同。
        if not self._hints:
            return CrawlPlan(
                seed=seed,
                gaps=(
                    {
                        "gap": "no_page_hints",
                        "detail": "profile 没有声明 page_discovery_hints，所以没有"
                        "「先抓哪些页面」的规则 —— 抓取顺序无从解释，因此不抓。",
                    },
                ),
            )

        seed_host = host_of(seed)
        origin = origin_of(seed)
        policy, gaps = self._robots(origin, account_id=account_id)

        # 3. 站点声明了拒绝（或策略取不到）时**连站点地图都不去读** —— 读站点地图
        #    也是抓取。这里停下来是这份文件唯一一处「直接返回」的策略判断。
        if policy.deny_all:
            return CrawlPlan(seed=seed, gaps=gaps)

        pooled, sitemap_gaps, documents = self._walk_sitemaps(policy, origin, account_id)
        gaps = gaps + sitemap_gaps

        targets, skipped, tier_hits = self._classify(
            seed=seed,
            seed_host=seed_host,
            pooled=pooled,
            policy=policy,
            limit=limit,
        )

        truncated = ""
        if skipped.get(SkipReason.BEYOND_LIMIT.value):
            truncated = (
                f"limit={limit}：命中的 URL 多于本次上限，"
                f"{skipped[SkipReason.BEYOND_LIMIT.value]} 条没有进计划"
            )

        return CrawlPlan(
            seed=seed,
            targets=tuple(targets),
            sitemap_documents=documents,
            pooled=len(pooled),
            tier_hits=tier_hits,
            skipped=skipped,
            truncated=truncated,
            gaps=gaps + tuple(self._empty_plan_gaps(seed, pooled, targets, skipped)),
        )

    # ------------------------------------------------------------------
    def _robots(self, origin: str, *, account_id: str) -> tuple[RobotsPolicy, tuple[dict, ...]]:
        """读 robots.txt。三种结局，**两种失败的结论相反**（见文件头第 2 条）。"""
        if not origin:
            return RobotsPolicy(), ()
        url = f"{origin}/robots.txt"
        text, result = self._read(url, account_id=account_id, purpose="robots")

        if result.outcome in (FetchOutcome.OK, FetchOutcome.EMPTY_SHELL):
            # 拿到了就按它执行。正文是垃圾（比如一个 HTML 错误页）时解析出零条
            # 规则 —— 「这个文件里没有声明」与「这个文件不存在」都等于没有规则，
            # 这是 RFC 的读法，也是唯一不把一次笔误升级成全站拒绝的读法。
            return parse_robots(text), ()
        if result.outcome is FetchOutcome.NOT_FOUND:
            return RobotsPolicy(), (
                {
                    "gap": "robots_absent",
                    "detail": f"{url} 返回 404 —— 这个站没有 robots.txt，"
                    "按 RFC 9309 §2.3.1.3 视为没有规则（不是「全站拒绝」）。",
                },
            )
        if result.outcome is FetchOutcome.BLOCKED:
            return RobotsPolicy(deny_all=True), (
                {
                    "gap": "robots_blocked",
                    "detail": f"{url} 被拦下（{result.block_reason}）—— 站点在 robots 这一跳"
                    "就拒绝了我们，因此不继续。理由码不是「没有这个文件」，"
                    "它是「这个站在拒我们」。",
                },
            )
        return RobotsPolicy(deny_all=True), (
            {
                "gap": "robots_unavailable",
                "detail": f"{url} 取不到（{result.outcome.value}"
                f"{': ' + result.error if result.error else ''}）—— 按 RFC 9309 §2.3.1.4 "
                "假设全站拒绝：连站点声明了什么都读不到时，继续抓是我们替它做的决定。",
            },
        )

    def _walk_sitemaps(
        self, policy: RobotsPolicy, origin: str, account_id: str
    ) -> tuple[list[str], tuple[dict, ...], int]:
        """按 robots 的声明（或约定位置）走站点地图，收集 URL 池。"""
        gaps: list[dict[str, Any]] = []
        pooled: list[str] = []
        seen: set[str] = set()
        queue = list(sitemap_candidates(origin, policy))
        visited: set[str] = set()
        documents = 0

        while queue and documents < MAX_SITEMAP_DOCUMENTS:
            location = queue.pop(0)
            if location in visited:
                continue
            visited.add(location)
            text, result = self._read(location, account_id=account_id, purpose="sitemap")
            documents += 1
            # ``OK`` 一定有镜像（``http.py`` 只对 OK / EMPTY_SHELL 落盘），
            # 所以这一个条件就够：没有镜像即读不到正文。
            if result.body_path is None:
                gaps.append(
                    {
                        "gap": "sitemap_unreachable",
                        "detail": f"{location}: {result.outcome.value}"
                        + (f" ({result.error})" if result.error else ""),
                    }
                )
                continue
            pages, children, gzipped = parse_sitemap(text)
            for url in pages:
                if url not in seen:
                    seen.add(url)
                    pooled.append(url)
            if len(pooled) >= MAX_POOLED_URLS:
                gaps.append(
                    {
                        "gap": "pool_truncated",
                        "detail": f"站点地图给出的 URL 超过 {MAX_POOLED_URLS} 条，"
                        "已在池子这一层截断（不等同于「该站只有这么多页」）。",
                    }
                )
                break
            for child in children:
                if child not in visited:
                    queue.append(child)
            for archive in gzipped:
                gaps.append(
                    {
                        "gap": "sitemap_gzip_unsupported",
                        "detail": f"{archive} 是压缩的站点地图；按文本读会得到二进制噪音，"
                        "而噪音里没有 <loc> —— 它会被读成「这份站点地图是空的」。因此跳过并记下。",
                    }
                )
            if not pages and not children and not gzipped:
                gaps.append({"gap": "sitemap_empty", "detail": location})

        if queue and documents >= MAX_SITEMAP_DOCUMENTS:
            gaps.append(
                {
                    "gap": "sitemap_documents_capped",
                    "detail": f"站点地图文档超过 {MAX_SITEMAP_DOCUMENTS} 份，"
                    f"还剩 {len(queue)} 份没有读 —— 这是一个上限，不是这个站只有这么多。",
                }
            )
        return pooled, tuple(gaps), documents

    def _classify(
        self,
        *,
        seed: str,
        seed_host: str,
        pooled: Iterable[str],
        policy: RobotsPolicy,
        limit: int,
    ) -> tuple[list[CrawlTarget], dict[str, int], dict[str, int]]:
        """把 URL 池分成「进计划」与「六种没进」，再按声明的优先级排序。"""
        skipped: dict[str, int] = {}
        tier_hits: dict[str, int] = {tier: 0 for tier in self._hints}
        by_tier: dict[str, list[CrawlTarget]] = {tier: [] for tier in self._hints}
        seed_targets: list[CrawlTarget] = []
        seen: set[str] = set()

        def skip(reason: SkipReason) -> None:
            skipped[reason.value] = skipped.get(reason.value, 0) + 1

        for url in (seed, *pooled):
            if url in seen:
                skip(SkipReason.DUPLICATE)
                continue
            seen.add(url)

            # 顺序是有意的：安全判据在最前（它拦下的目标一次请求都不会发出去），
            # 然后是同站，然后是 robots，最后才是业务分级。把 robots 放到安全
            # 之后是因为一条不安全的目标根本不该被拿去问「站点允不允许」——
            # 而反过来排会让「私网地址」在报告上表现成「robots 不允许」。
            if not check_target(url).allowed:
                skip(SkipReason.UNSAFE_TARGET)
                continue
            if not same_site(seed_host, url):
                skip(SkipReason.OFF_SITE)
                continue
            if not policy.allows(url):
                skip(SkipReason.BY_ROBOTS)
                continue

            if url == seed:
                # 种子由调用方指定，**不按提示分级** —— 给它编一个优先级等于伪造
                # 一条规则。它仍然过了上面三道判据：被指定的 URL 不是一条授权。
                seed_targets.append(CrawlTarget(url=url, tier="", why="seed"))
                continue

            matched = match_tier(url, self._hints)
            if matched is None:
                skip(SkipReason.UNMATCHED)
                continue
            tier, pattern = matched
            tier_hits[tier] = tier_hits.get(tier, 0) + 1
            by_tier.setdefault(tier, []).append(
                CrawlTarget(url=url, tier=tier, why=pattern)
            )

        ordered = list(seed_targets)
        for tier in self._hints:
            ordered.extend(by_tier.get(tier, ()))
        # 没有「未知优先级」这一支要补：``by_tier`` 是按 ``_hints`` 建的，
        # 而 ``match_tier`` 只可能返回 ``_hints`` 里的键。声明里多出来的优先级
        # 因此在计划里必然有一条对应的（可能为空的）格子，不会静默丢掉 URL。

        if len(ordered) > limit:
            skipped[SkipReason.BEYOND_LIMIT.value] = len(ordered) - limit
            ordered = ordered[:limit]
        return ordered, skipped, tier_hits

    @staticmethod
    def _empty_plan_gaps(
        seed: str,
        pooled: list[str],
        targets: list[CrawlTarget],
        skipped: Mapping[str, int],
    ) -> list[dict[str, Any]]:
        """计划为空时**必须**留下的那句话。

        ``no_sitemap_urls`` 按**池子**判，不按计划判：种子几乎总是让计划非空，
        而「站点地图什么也没给出」这件事在那种时候同样要说 —— 它意味着这次抓取
        只有调用方指定的那一个 URL。按计划判会让它几乎永远不出现，而它存在的
        理由正是「不要把一次空的站点地图读成这个站没有可抓页面」。
        """
        if not pooled:
            return [
                {
                    "gap": "no_sitemap_urls",
                    "detail": f"{seed} 的站点地图没给出任何页面 URL。"
                    "按 SOP，搜索结果与单一页面只能作补充证据 —— 这一条也适用："
                    "只凭一次站点地图为空，不能说这个站没有可抓页面。",
                }
            ]
        if targets:
            return []
        return [
            {
                "gap": "all_pooled_urls_skipped",
                "detail": f"池子里有 {len(pooled)} 条 URL，但没有一条进计划："
                + "，".join(f"{reason}={count}" for reason, count in sorted(skipped.items()))
                + "。逐条原因见 skipped —— 「这个站没有可用页面」与「每一条都被某条"
                "规则拦下了」不是同一件事。",
            }
        ]

    def _read(
        self, url: str, *, account_id: str, purpose: str
    ) -> tuple[str, FetchResult]:
        """抓一份文本资源并读出来。返回 ``(文本, 回执)``。

        失败时文本为空串，**判据留给调用方**：robots 的 404 与 5xx 要得到相反的
        结论，所以这里不能把「读不到」压成一个布尔。
        """
        result = self._http.fetch(
            FetchRequest(url=url, scope=account_id, purpose=purpose)
        )
        if result.body_path is None:
            return "", result
        try:
            return result.body_path.read_text(encoding="utf-8", errors="replace"), result
        except OSError as exc:
            return "", FetchResult(
                request=result.request,
                outcome=FetchOutcome.ERROR,
                tier=result.tier,
                final_url=result.final_url,
                error=f"读取镜像失败 {type(exc).__name__}",
                reason=result.reason,
            )


__all__ = [
    "MAX_POOLED_URLS",
    "MAX_SITEMAP_DOCUMENTS",
    "RobotsPolicy",
    "SitemapFrontier",
    "host_of",
    "match_tier",
    "origin_of",
    "parse_robots",
    "parse_sitemap",
    "same_site",
    "site_key",
    "sitemap_candidates",
]

"""**组合根的第二块**：回答「谁来给」，而不是「要什么」。

``interfaces/__init__.py`` 写下了这条规则：只有本层与 ``adapters/`` 自己可以
点名某个具体厂商 adapter。``cli.py`` 是第一个落点，本模块是第二个。

这里的每一个函数都长成同一个形状 —— **参数是端口，返回值是端口的某个具体实现**。
``default_router()`` 与 ``default_acquisition()`` 是今天的两处，因为它们都是
「按运行时情况决定装哪几个实现」的接线（前者：MinerU 装了才接 fallback，没装就只留
轻量 parser；后者：crawl4ai 装了才接浏览器，没装就只有 HTTP 一个 tier）。

## 它为什么不在 ``adapters/document_router.py`` 里（B7a）

那个文件原来同时住着两样东西：``DocumentRouter`` 这个**纯策略**（只认
``DocumentParser`` 端口，不碰磁盘），与 ``default_router()`` 这个**接线**
（函数体内 import ``pypdf`` 与 MinerU）。判据是 ``adapters/__init__.py`` 开头那一句
（import 它需不需要磁盘/网络/进程），两者答案相反，于是它们不该共用一个文件名 ——
否则"策略"会被迫按"接线"的判据来定位。

一个方案曾被认为可行但被否掉：让 ``default_router()`` 留在 ``adapters/`` 然后去
import ``application/``。那样 ``adapters/`` 会依赖 ``application/``，方向反了 ——
本仓的分层是 ``domain ← ports ← application ← adapters/interfaces``，adapter 是最
外圈。

另一个被否掉的方案：把 ``PipelineDeps.router`` 的类型改成 ``DocumentParser``，
这样 pipeline 就只认端口。否掉的理由是**会改判据而不改事实**：
``DocumentRouter`` 并不是 ``DocumentParser`` 的子类（它没有 ``can_parse``、
没有 ``name``、没有 ``source_kind``），把它声明成 ``DocumentParser`` 是让类型
签名称赞一件不成立的事。

## 它不做的事

这里的函数**不读环境变量**（那是 ``config.py`` 的事，``Settings`` 是别人读好后
递进来的）、**不决定上限**（那是冻结的 profile 的事，见 ``application/budget.py``）。
它们只做一件事：把已经决定好的参数翻译成一组具体对象。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from ..application.acquisition import DocumentAcquisition
from ..application.budget import RunBudget
from ..application.document_router import DocumentRouter
from ..application.site_crawl import SiteCrawl
from ..config import Settings
from ..domain.access_governance import AccessGovernor
from ..domain.errors import ConfigError, StoreError
from ..domain.fetch_attempt import MaterialProvenance
from ..ports.acquisition import HttpFetcher
from ..ports.claim_withdrawal import ClaimWithdrawalStore
from ..ports.continuation import WakeupRegistry
from ..ports.correspondence import CorrespondenceSource
from ..ports.decisions import DecisionProvider
from ..ports.dispatch import Dispatcher
from ..ports.documents import DocumentParser
from ..ports.feedback import FeedbackStore
from ..ports.fetch_attempt import FetchAttemptStore
from ..ports.notification import NotificationStore
from ..ports.outreach import MailTransport, OutreachStore
from ..ports.pool import (
    AccountCohortStore,
    AccountPoolStore,
    AccountVenueRelationStore,
    VenueAssetStore,
)
from ..ports.product_catalog import ProductCatalog
from ..ports.review import ReviewCaseStore
from ..ports.runs import RunStore
from ..ports.scenes import MaintenanceStore, SceneStore
from ..ports.scheduling import ScheduleStore
from ..ports.session import SessionBindingStore
from ..ports.store import EvidenceStore
from ..ports.views import ActionItemStore, SavedViewStore, WatchStore
from ..ports.workstreams import EffectiveSceneStore, WorkstreamStore


#: **进程级的来源账。** 全进程一本，不是每个抓取器一本。
#:
#: 采集设计 §5 点名的那条坏结局（「一个同源挑战页上的 20 个账号不得生成 20 个无界
#: 重试套件」）不是靠某一次调用记住的，而是靠**账记在来源上、而来源账只有一本**。
#: 每次构造一份的后果在报告里看不出来：20 个账号各自失败一次 —— 与真正的 20 次独立
#: 失败同形。
#:
#: 写成模块级而不是「由调用方传进来」的，理由是本模块的调用方今天是 CLI 与服务面，
#: 它们在同一个进程里各自构造自己的抓取器；把账交给调用方保管，等于要求每一处调用点
#: 都知道另外几处的存在，而漏掉一处没有任何东西会报错。它是**可变**的（这是全局状态
#: 的全部代价），因此判据要能注入自己的一本 —— 那就是 ``default_http(governor=...)``。
#:
#: 已知边界：它是**进程级**的。多进程部署里每个进程一本账，跨进程共享没有做
#: （B15-d 的运行记录里记了这一格）。
_GOVERNOR = AccessGovernor()


def default_attempt_store(settings: Settings) -> Callable[[], FetchAttemptStore]:
    """访问尝试账（``ports/fetch_attempt.py``）的**工厂**。

    与 ``default_scene_store`` 逐条同构，理由也逐条相同：``sqlite3`` 的连接不跨线程，
    而 ``SqliteFetchAttemptStore.__init__`` 会 ``ensure_schema``（**会建库**）。所以
    这里给的是工厂，建库被推迟到第一次真的记下一笔的时候。

    ## 它与来源账（``_GOVERNOR``）不同：一个落盘，一个在内存里

    来源账是进程级的一本（见 :data:`_GOVERNOR`），而这一本落在
    ``settings.database_path`` 上 —— 同一份文件里还住着证据、场景、池子、作业。
    写成具名函数而不是让 ``default_http`` 里写一行 lambda，理由与
    ``default_maintenance_store`` 那一节同一条：**「服务面拿的是哪一个 store」这件事
    只有在组合根里才看得出**，而几个 store 共用一个库时，看错的那一次表现为
    「记下的东西读不回来」。

    ## 两个抓取器拿到的是**同一本**

    直连那一层与浏览器那一层都往这里记（``default_acquisition`` 把同一个工厂交给
    两者）。两处各建一个 store 的后果在报告里看不出来：同一个 URL 的两条尝试落在
    两个文件上，而「这次一共试过几次」没有一个地方答得出来 —— 与来源账那条
    「20 个账号各自失败一次」是同一类失败。

    一次浏览器渲染里可能有好几次被拒的**子资源**请求，而它们不是「这次抓取的结果」
    —— 它们的形状是 ``FetchResult.egress_refusals``（一次渲染一次抓取一行账），
    不是多行尝试账。``FetchAttempt`` 的 ``settlement`` 那一格说清楚了这件事：
    浏览器这一跳今天取 ``NOT_APPLICABLE``，理由与将来要改的那一处写在
    ``adapters/fetch_attempts.py::attempt_from`` 上。
    """
    from ..adapters.sqlite.fetch_attempt import SqliteFetchAttemptStore

    return lambda: SqliteFetchAttemptStore(settings.database_path)


def default_http(
    settings: Settings,
    *,
    governor: AccessGovernor | None = None,
    attempts: Callable[[], FetchAttemptStore] | None = None,
) -> HttpFetcher:
    """§10 那**唯一**一个直连抓取器。

    抽成一个函数只有一个理由：它是两处**必须同一个**的地方（下面的
    ``default_crawl``），而「必须同一个」写在两个 ``HttpxFetcher(settings)``
    字面量上是维持不住的 —— 第二处是照着第一处抄的，抄的时候没人会看见差异。

    **来源账在这里注入，而且只有这里注入。** 二十个账号、几个 work item、几次运行
    共用的必须是同一本 —— 见 :data:`_GOVERNOR`。``governor=None`` 表示「用进程那本」，
    而不是「新建一本」：一个把新建当缺省的实现，会让「共享」变成只有记得传参的人
    才拿得到的东西。

    **尝试账在这里接上，而且只有这里接上。** 它是 §5「每一次尝试都留一行账」的
    生产者：没有这一格，``FetchAttempt`` 有类、有表、有两个实现、有契约，而**没有
    任何一个真实调用点写出过一行** —— 那正是「只有 schema 没有生产者」。接在此处的
    另一个理由是它必须与 ``governor`` 同一处：两者都只能有一本，分两处接线的那天
    就会有一处漏掉。

    **材料声明写在这里，而且是这一处写 ``LIVE``。** 这个函数是 §10 的**唯一**一个
    真出网的直连抓取器，所以「这个实例抓的是真实的第三方公开页面」是一句在这里
    为真、而且在别处不一定为真的话：一个在测试里注入 ``client=`` 的
    ``HttpxFetcher`` 与被这个函数构造的那个**是同一个类**，它们拿到的材料不同。
    缺省 ``SYNTHETIC`` 是那条规则的另一半 —— 替掉了网络的那一处什么都不用写，
    于是「现场」变成一次必须在组合根上**说出口**的声明，而不是一个凡是构造了
    ``httpx`` 抓取器就自动成立的事实。理由全文见
    ``ports/acquisition.py::Fetcher.material``。

    Args:
        governor: 显式注入一本（判据用）。生产路径上永远是 ``None``。
        attempts: 尝试账本的工厂。``None`` 表示「按设置接上那个落盘的」
            （:func:`default_attempt_store`），**不是**「不记账」—— 与 ``governor``
            那一格同一条纪律：把「不接」当缺省，会让「接上了」变成只有记得传参的人
            才拿得到的东西，而没接上的那个部署在报告里与一个没抓到东西的部署同形。

            要**真的不记账**（大量单测、离线重放）就绕开这一层直接构造
            ``HttpxFetcher(settings)`` —— 那是那个类自己的缺省，也是「一个字节都不
            落盘」唯一该被表达的地方。判据用 ``attempts=`` 注入一个内存实现来观察。
    """
    from ..adapters.http import HttpxFetcher

    return HttpxFetcher(
        settings,
        governor=governor if governor is not None else _GOVERNOR,
        attempts=attempts if attempts is not None else default_attempt_store(settings),
        # 见上面那一段：这一句是这次抓取拿到的是**现场的**第三方页面的声明，
        # 而它只能写在这里 —— 注入 ``client=`` 的构造点替掉了网络，写 ``LIVE``
        # 的话会把我们自己造的页面记成现场覆盖。
        material=MaterialProvenance.LIVE,
    )


def default_acquisition(
    settings: Settings,
    *,
    http: HttpFetcher | None = None,
    allow_browser: bool = True,
    attempts: Callable[[], FetchAttemptStore] | None = None,
) -> DocumentAcquisition:
    """构造 §10 的统一抓取入口：HTTP 必接，浏览器**可用才接**。

    ``allow_browser=False`` 与「装了但依赖缺失」是两条不同的路，落到两种不同的
    回执上（``browser=None`` → ``browser_not_wired``；``available`` 为假 →
    ``browser_unavailable``）。所以这里不把两者压成一个布尔再传出去 ——
    ``allow_browser`` 关掉的是**接线**，依赖在不在由 fetcher 自己答。

    注意 ``--no-browser`` 走的是 ``allow_browser=False``：那是**部署的选择**，
    所以 ``auto`` 会停在 HTTP 结果上、``browser`` 会明确说没接线。它不是
    「这次先试试不用浏览器」——那种语义属于 ``FetchMode.HTTP``，是调用方的选择。

    ## 接浏览器之前要过的那一关

    接上之前读 ``browser.constrains_egress``；为假就抛 ``ConfigError``。采集设计 §7
    逐字把这件事写成了组合根的责任：「若所锁浏览器接口不能在发送前拦截全部必要请求，
    组合根需采用可验证的受管网络层/拦截器，或禁用该模式并明确阻塞；不能只保留
    『最终URL不合规就不存』宣称已安全」。

    所以这一格是**失败关闭**的：一个只会做「事后看看最终 URL」的浏览器实现，
    在这里接不上，而不是接上之后在某份报告里被读成「已受管」。抛而不是降级成
    ``browser=None``，理由与 ``default_dispatcher`` 对「配了一半」的处置同一条：
    一个**配错了的部署**要起不来。降级会让它照常起、照常抓，而唯一能看出区别的
    地方是每一份浏览器回执上少了一个没人读的字段。

    今天这个守卫永远不触发（``Crawl4AIBrowserFetcher`` 是 ``True``），它挡的是
    下一个实现 —— 而那正是把它写在构造期的理由：加一个实现的人不会去读 §7。

    Args:
        http: 直连抓取器。``None`` 表示「按设置造一个」。传进来是为了让
            ``default_crawl`` 能把**同一个**抓取器交给前沿与页面两侧（见那里），
            而不是在部署里换一个 fetcher —— 生产路径上它永远是 ``None``。
        attempts: 尝试账本的工厂，透传给 :func:`default_http` **与浏览器那一层**
            （语义见那里）。只在这里出现是因为生产路径走的是这个函数：CLI、
            controller、服务面四处调用点都是 ``default_acquisition(settings)``，
            没有一处直接调 ``default_http``。没有这一格，``attempts`` 就只有判据
            够得着，而**判据接上的线不是生产接上的线** —— 那个差别在报告里看不出来。
            传了 ``http`` 时它被忽略（``http`` 自带自己的那本）。
    """
    # **只算一次**，两个抓取器拿同一个。写成两处 ``attempts if ... else
    # default_attempt_store(settings)`` 是同一个函数调两次 —— 效果一样（同一个
    # ``database_path``）但读起来像两本账，而下一个改这里的人会以为它们可以不同。
    store_factory = attempts if attempts is not None else default_attempt_store(settings)

    browser = None
    if allow_browser:
        from ..adapters.crawl4ai_fetcher import Crawl4AIBrowserFetcher

        # 不在这里判 ``crawl4ai_available()``：那个判断是 fetcher 的 ``available``
        # 属性，两处各判一次就会出现「接线说装了、抓取说没装」这种漂移。
        #
        # **来源账与尝试账都与直连那一层共担**（``_GOVERNOR`` 与 ``store_factory``）。
        # 这两条不是顺手带上的：浏览器这一层是直连失败之后的**升级路径**，两次请求
        # 落在同一个来源上，而同一个账号的两次尝试落在同一本账上。各拿一本干净的，
        # 就是 §5 那条「20 个账号各自无界重试」与「试了多少次没人答得出」的成因。
        # ``material=LIVE`` 与 ``default_http`` 那一格逐字同一条理由：这里的这一个是
        # 真去渲染第三方公开页面的那一个。判据用 ``egress=`` 立一个本机靶子时拿到的是
        # 那个类的另一个实例，它落回缺省。
        browser = Crawl4AIBrowserFetcher(
            settings,
            governor=_GOVERNOR,
            attempts=store_factory,
            material=MaterialProvenance.LIVE,
        )
        if not browser.constrains_egress:
            raise ConfigError(
                f"{type(browser).__name__} 声明 constrains_egress=False，"
                "不能接进组合根：浏览器会发的请求（主导航、重定向、iframe/worker、"
                "子资源）必须在发出前过判据、且连接钉在判过的地址上"
                "（docs/ACQUISITION_AND_PROVIDER_DESIGN.md §7）。"
                "要做到这一点，或改用一个能约束出口的实现，或用 --no-browser 明说"
                "本部署不用浏览器。"
            )

    return DocumentAcquisition(
        http=(
            http
            if http is not None
            else default_http(settings, attempts=store_factory)
        ),
        browser=browser,
    )


def default_crawl(
    acquisition: DocumentAcquisition,
) -> Callable[[Mapping[str, Sequence[str]]], SiteCrawl]:
    """构造多页抓取：``hints`` 进去，一个接好的 ``SiteCrawl`` 出来。

    收的是**抓取入口**而不是 ``Settings``，这不是风格问题。``SitemapFrontier``
    与 ``DocumentAcquisition`` 各要一个 ``HttpFetcher``，而两处各起一个会让超时、
    UA、重试策略变成一条**没人观察的差异**（``sitemap_frontier`` 的文件头）。
    所以前沿的 fetcher 从 ``acquisition.http`` 取 —— 组合根里只有一个 ``http``
    在流通，两份在类型上不可表达。判据是
    ``tests/test_mcp_entry.py::test_the_frontier_and_the_pages_walk_through_one_fetcher``。

    返回一个**工厂**而不是对象：``hints`` 来自冻结的 profile，而 profile 的装载
    会失败。装记载体的地方在服务面的 handler 里，那里的失败是一条可读的
    ``ToolError``（服务面照常起得来）；如果在这里就把 hints 定下来，一份坏 profile
    会让整个部署起不来 —— 而「起得来但每条调用都说得出为什么」是这一层的既有取向。
    """
    from ..application.sitemap_frontier import SitemapFrontier

    def build(hints: Mapping[str, Sequence[str]]) -> SiteCrawl:
        return SiteCrawl(
            frontier=SitemapFrontier(http=acquisition.http, hints=hints),
            acquisition=acquisition,
        )

    return build


def default_decisions(settings: Settings) -> dict[str, DecisionProvider]:
    """构造本部署的**两个**决策后端，按 ``provider.name`` 索引。

    **不筛 ``ready``。** 两个都构造出来，理由有三条，而它们互相独立：

    * ``get_capabilities`` 要逐臂报「配了没 / 能不能真的产出一条记录」—— 一个被
      筛掉的后端在表上会以**缺席**的形式出现，而缺席读不出「没配钥匙」。
    * 影子**不跳过**没就绪的臂（``application/shadow.py``）：它给出的
      ``UNAVAILABLE`` 是一条结果，滤掉它会让报告从「jev 缺凭据」变成「jev 没参试」。
    * 缺凭据/权重时 ``ready`` 为假，而 ``decide`` 仍然答得上来 —— 它答的是一条带
      理由的 ``UNAVAILABLE``。§4 逐字：缺凭据/checkpoint 是一个 blocker，
      **不是一次伪造的成功推理**。

    构造本身不碰网络、不 import 厂商 SDK（两个 adapter 的 ``configured`` 都用
    ``find_spec``），也不读盘超过四次 ``stat`` —— 所以 ``get_capabilities`` 每被
    读一次就构造一遍是安全的（§2：健康检查不得每次读面板都去调计费后端）。

    **一个进程里只构造一次。** 这不是性能要求：``LayaDecisionProvider`` 每个实例
    各持一个 Router，而同一个 421M 的 checkpoint 装两遍不是慢，是 OOM。所以调用方
    （``interfaces/mcp/__main__.py``）构造一次，把**同一批对象**同时交给
    ``build_server`` 与 ``research_handlers``。
    """
    from ..adapters.jev import JevDecisionProvider
    from ..adapters.laya import LayaDecisionProvider

    return {
        "jev": JevDecisionProvider(api_key=settings.jev_api_key),
        "laya": LayaDecisionProvider(checkpoint_dir=settings.laya_checkpoint_dir),
    }


def advisory_decision_provider(
    settings: Settings, providers: Mapping[str, DecisionProvider]
) -> DecisionProvider | None:
    """assist 模式下那个**唯一**被咨询的后端。``None`` = 本部署没指定。

    ``providers`` 是**传进来的**，不是这里构造的：见 ``default_decisions`` 的最后
    一段（一个进程一个 Router）。而这一点恰好也是这条判据要成立的前提 ——
    「配置点名的那个」与「服务面真的会调的那个」必须是同一个对象。

    Raises:
        ConfigError: 配置点名了一个本部署装不出来的后端。**不回落**：研究轨迹上
            写着「辅助答案来自 jev」而实际来自另一个，是这条回落唯一的产物，
            且没有任何一处看得出来。
    """
    name = (settings.decision_provider or "").strip()
    if not name:
        return None
    provider = providers.get(name)
    if provider is None:
        raise ConfigError(
            f"MARKETOPS_DECISION_PROVIDER 是 {name!r}，而本部署能构造的后端是 "
            f"{sorted(providers)}。\n"
            "  这里不回落到别的后端：那会让研究轨迹上「辅助答案来自哪里」这一栏"
            "写着一个没有发生过的事，而报告上完全看不出来。"
        )
    return provider


def default_dispatcher(settings: Settings) -> Dispatcher | None:
    """外部执行器（§6 的派发路径）。``None`` = 本部署没有执行器。

    ## 三样全没配 与 只配了两样，不是同一件事

    ``None`` 只对应**三样一个都没给**：那是「本部署没有执行器」这条事实，它有
    一个明确的处置 —— 不派发 —— 于是它不是一个异常。``wire_research`` 不接没配
    key 的检索提供方是同一条取向，``build_server`` 的 ``research=None`` 也是：
    **没有的入口优于有入口再加一条注意事项**。

    **只给了一两样则抛 ``ConfigError``。** 那不是一个「没配」的部署，是一个
    **配错了**的部署 —— 三样缺一不可而这里只给了两样，说明有人以为自己配好了。
    按「没配」处置会把它降级成能力表上一格 ``runtime_ready: false``，而那格
    读起来与「本部署没打算派发」逐字相同。这正是 ``Settings.quay_enabled``
    的 docstring 里那句「两样为假是对的 —— 但对运维是两句不同的话」的落点。

    三样齐了而路径本身不合法（相对路径 / 不在盘上）由构造器抛 ``ConfigError`` ——
    与它自己那条纪律一致：「在构造时抛，不在第一次调用时抛」，否则一个「建得起来、
    派发时才炸」的执行器在部署自检里是完全隐形的。本函数不吞它。

    ## ``Settings`` → 构造参数的那次映射只有这一处

    这是本函数存在的第二个理由：若 ``get_capabilities`` 自己也映射一遍，两处把
    哪一样接到哪个参数上搞反时，能力表会报「就绪」而派发用的是另一个文件 ——
    而两份映射在代码里长得一模一样。
    """
    supplied = [
        (name, value)
        for name, value in (
            (_QUAY_CLI_VARIABLE, settings.quay_cli_path),
            (_QUAY_NODE_VARIABLE, settings.quay_node_path),
            (_QUAY_ROOT_VARIABLE, settings.quay_root),
        )
        if value is not None
    ]
    if not supplied:
        return None
    if len(supplied) < 3:
        given = [name for name, _ in supplied]
        missing = [name for name in _QUAY_VARIABLES if name not in given]
        raise ConfigError(
            f"外部执行器配了一半：给全了 {given}，缺 {'、'.join(missing)}。\n"
            "  三样缺一不可（入口脚本、解释器、工作区根）。这里**不**按「没配执行器」"
            "处置：那会让一个配错了的部署在能力表上长成「本部署不派发」，"
            "而这两句话对运维是两件事。"
        )
    from ..adapters.quay_cli import QuayCliDispatcher

    return QuayCliDispatcher(
        cli_path=settings.quay_cli_path,
        node_path=settings.quay_node_path,
        root=settings.quay_root,
        timeout_seconds=settings.quay_timeout_seconds,
    )


#: 三样配置的**变量名**。写在这里是为了让上面那条「配了一半」的消息点名环境变量，
#: 而不是点名 ``Settings`` 的字段 —— 看到那条消息的人手里有环境变量，没有这个类。
_QUAY_CLI_VARIABLE = "MARKETOPS_QUAY_CLI"
_QUAY_NODE_VARIABLE = "MARKETOPS_QUAY_NODE"
_QUAY_ROOT_VARIABLE = "MARKETOPS_QUAY_ROOT"

_QUAY_VARIABLES = (_QUAY_CLI_VARIABLE, _QUAY_NODE_VARIABLE, _QUAY_ROOT_VARIABLE)


def default_scene_store(settings: Settings) -> Callable[[], SceneStore]:
    """Scene 持久化（``ports/scenes.py``）的**工厂**—— 注意返回的是工厂，不是实例。

    ## 为什么不返回实例

    两条理由，都是本仓已经踩过的：

    1. **``sqlite3`` 的连接默认不允许跨线程使用。** 工具处理器可能在别的线程上跑，
       所以本仓的账本、证据库、作业存储、判定记录一律传**工厂**，读一次开一次。
       Scene 存储没有理由做那个例外。类型注解 ``Callable[[], SceneStore]`` 把这件事
       写在签名上：调用方拿到的是一个每次给新连接的东西，而不是一个共享句柄。
    2. **``SqliteSceneStore.__init__`` 会 ensure_schema，也就是会建库。** 而
       ``_compose`` 的契约是「构造服务面需要的一切，**不绑端口、不建运行目录**」；
       在组合阶段就把库建出来，会让一次 ``--check`` 自检留下副作用 —— 自检本该是
       只读的。惰性工厂把建库推迟到第一次真的读写场景时。

    第 2 条另有一个可观测的后果：一个**还没建库**的部署在 ``get_scene`` 里报的是
    ``no_store``，不是「没有立项过」。这两句话的区别由 ``tools._scene_record``
    守住 —— 在存储都不存在时说「这个场景不存在」，是把一次「没接线」答成了一次
    业务结论。
    """
    from ..adapters.sqlite.scenes import SqliteSceneStore

    return lambda: SqliteSceneStore(settings.database_path)


def default_maintenance_store(settings: Settings) -> Callable[[], MaintenanceStore]:
    """增量维护的持久化（``ports/scenes.py::MaintenanceStore``）的**工厂**。

    与 ``default_scene_store`` 逐条同构（惰性、每次新连接、由调用方只构造一次），
    但仍写成第二个函数而不是让调用方直接写 ``lambda: SqliteMaintenanceStore(...)``。
    理由只有一条，而它是可观测的：**两个 store 落在同一个 ``database_path`` 上**，
    而「服务面拿的是哪一个」这件事只有在组合根里才看得出。两处各写一行 lambda，
    改动其一时另一处照旧编译、照旧通过类型检查、照旧跑 —— 直到有人发现
    「记下的观测与读到的场景不在一个库里」。

    它们**是同一条规则的两次应用，不是同一个东西**：``ports/scenes.py`` 把
    「场景」与「维护」分成两个 ABC，于是「这个部署有没有场景存储」与「它有没有
    维护存储」在签名上就该是两件事。合成一个参数会让删掉维护能力必须连场景一起删。
    """
    from ..adapters.sqlite.scenes import SqliteMaintenanceStore

    return lambda: SqliteMaintenanceStore(settings.database_path)


def default_run_store(settings: Settings) -> Callable[[], RunStore]:
    """Run 记录的持久化（``ports/runs.py``）的**工厂**。

    与上两个逐条同构（惰性、每次新连接、由调用方只构造一次），理由也一样是那两条：
    ``sqlite3`` 的连接不能跨线程，而 ``SqliteRunStore.__init__`` 会 ``ensure_schema``
    —— 组合阶段建库会让一次 ``--check`` 自检留下副作用。

    ## 它与 ``default_scene_store`` 的关系，值得单独写一句

    受理要**同时**读两样东西：场景那一版规则（工作区与对象范围从它取）与 Run 记录
    （那一行落在哪里）。两个工厂指向同一个 ``database_path``，所以「服务面拿的是
    哪一份配置」只有在组合根里才看得出 —— 哪一处改成另写一行 lambda，两处就都能
    编译、都能通过类型检查，直到有人发现「受理读的场景与写下那一行的库不是一个」。
    那不是一次读错，是那一行的 ``workspace`` 指向一个**读不到的场景**，而两边
    各自都正常。

    ## 它为什么是第四个 ``default_*_store`` 而 ``__all__`` 不跟着改

    ``__all__`` 在 ``default_scene_store`` 之前就停住了，此后新增的每一个工厂都没进
    去。这是一条**已知的陈旧**（不是本函数新造的口径），而它**没有执行器**：本模块
    自己的调用方一律用显式 import，只有 ``import *`` 那一类用法才会显形，而本仓
    没有那种用法 —— 所以它不会红，只会让「这个模块导出什么」在文件头读起来是旧的。

    改它是一次**导出面**的决定（哪些工厂是给别人用的、哪些是模块内部的接线），与
    Run 无关，所以它留给自己的那一片。**不要**顺手在新工厂旁边补一行 ``__all__``：
    那会让一次这样的调整散在几十片里，而这正是上面那条陈旧变成陈旧的方式。
    """
    from ..adapters.sqlite.runs import SqliteRunStore

    return lambda: SqliteRunStore(settings.database_path)


def default_router(*, mineru_output_dir: Path | None = None) -> DocumentRouter:
    """构造默认路由：HTML + 文本型 PDF 为 primary，MinerU 为 fallback（若可用）。

    「可用」问的是 ``mineru_fallback_ready()``，不是 ``mineru_available()`` ——
    依赖装了吗与它能不能真的产出一份文档是两个问题（见那个模块的文件头）。
    这里问的是**要不要把它接上**，所以答案必须是后者：接上一个每次解析都抛的
    fallback，等于把「这份 PDF 读不出来」写成「这份 PDF 用 MinerU 也读不出来」。
    """
    from ..adapters.mineru_adapter import MinerUDocumentParser, mineru_fallback_ready
    from ..adapters.text_pdf import HtmlDocumentParser, TextPdfParser

    fallbacks: list[DocumentParser] = []
    if mineru_output_dir is not None and mineru_fallback_ready():
        fallbacks.append(MinerUDocumentParser(mineru_output_dir))

    return DocumentRouter([HtmlDocumentParser(), TextPdfParser()], fallbacks)


def default_document_budget(settings: Settings) -> Callable[[str], RunBudget]:
    """抓取额度的入口：``label → RunBudget``。**页面复核要的最后一样接线。**

    它给出去的是 ``application/budget.py::RunBudget.for_documents`` 的一个绑定版本
    ——「一次复核最多看几页」那句话的上限住在这份部署的 profile 里
    （``fetch.max_documents``），**不在这里**：这个函数不决定上限，它只把上限的
    来源（这个部署装载得出来的那份 profile）接上。profile 没声明那一格时
    ``declared_document_ceiling`` 失败关闭，异常逐字抬到调用方 —— 那是 §8 的配置
    事故（未配置不等于无限），要有一条说得出「去写哪一格」的异常。

    ## 为什么返回一个闭包而不是 ``RunBudget`` 本身

    上限是**每个 scope 一份**的，而 scope 要等这一格到期工作被认领之后才拼得出来
    （``application/refresh.py::document_scope``：``<profile>:<主体 id>@<这一轮时刻>``）。
    接线在这一层能回答的只是「上限从哪来、账本在哪」，所以它给的是一个已经绑好这两样
    的函数。

    ## 账本每次开一个新的

    与 ``interfaces/mcp/__main__.py`` 的 ``ledger_factory`` 逐字同一条：``sqlite3``
    的连接默认不允许跨线程使用，而复核可能在别的线程上跑。同一个 scope 的
    ``reserve`` / ``spend`` 用的是**同一个** ``RunBudget``（就是这一趟拿到的那一个），
    所以「预留了、然后花掉」不会分到两个连接上。

    profile **每次调用**装载一次，而不是在这里装好存起来：``load_profile`` 的调用方
    在本仓一律是「用它的那一刻读一次」（``adapters/mcp_server/tools.py`` 那四处逐字
    如此），而一次 tick 里它至多被读两次（两个页面面各一次）。存一份的代价是：这个
    函数一旦被拿去做长驻接线，盘上那份 profile 换了而它照旧用旧的 —— 那是 B22 要
    复核的那些事实的反面。
    """
    from ..adapters.profile_loader import load_profile
    from ..adapters.sqlite.ledger import SqliteBudgetLedger

    def documents(label: str) -> RunBudget:
        return RunBudget.for_documents(
            SqliteBudgetLedger(settings.database_path), load_profile(settings), label
        )

    return documents


def default_outreach_store(settings: Settings) -> Callable[[], OutreachStore]:
    """外联那一段的持久化（``ports/outreach.py::OutreachStore``）的**工厂**。

    与 :func:`default_scene_store` 逐条同构：惰性、每次新连接、同一个
    ``database_path``。仍写成第三个函数而不是让调用方写一行 lambda，理由也逐字
    同一条 —— 四个 store 落在同一个库文件上，而「服务面拿的是哪一个」只有在这里
    才看得出。两处各写一行 lambda，改动其一时另一处照旧编译、照旧跑，直到有人
    发现「授权写进的库与投递读的那个不是同一个」。
    """
    from ..adapters.sqlite.outreach import SqliteOutreachStore

    return lambda: SqliteOutreachStore(settings.database_path)


def default_product_catalog_store(
    settings: Settings,
) -> Callable[[], ProductCatalog]:
    """产品目录（``ports/product_catalog.py::ProductCatalog``）的**工厂**。

    与 :func:`default_scene_store` 逐条同构：惰性、每次新连接、同一个
    ``database_path``。仍写成又一个函数而不是让调用方写一行 lambda，理由也逐字
    同一条 —— 落在这个库文件上的 store 已经有几个，「服务面拿的是哪一个」只有在
    这里才看得出。

    ## 它落在**同一个**库文件上，而这不是顺手

    产品目录与 Scene 之间存在引用（一个产品版本会被草稿回链、一个 Offer 会被 Scene
    引用），而「回链解不解得开」这件事只有在两边同一个库时才判得出来。两个库的形态
    是：登记说这一版在，回链说找不到 —— 而两个回执各自都正常。

    ## 这里没有第二条路（没有单独的 catalog 路径配置）

    ``Settings`` 上没有一个 ``catalog_path``，这一片也不加。加一个的后果不是「多一种
    部署形态」，而是「同一个部署里产品目录与场景可以落在两个库上，而没有任何一处会
    拒绝它」—— 与 :func:`default_scene_store` 那一段的 ``ensure_schema`` 是同一件事
    的两半：库那一侧拒绝混装**工作区**，组合根这一侧不给第二个落点。
    """
    from ..adapters.sqlite.product_catalog import SqliteProductCatalog

    return lambda: SqliteProductCatalog(settings.database_path)


def default_asset_store(settings: Settings) -> Callable[[], VenueAssetStore]:
    """场地资产池（``ports/pool.py::VenueAssetStore``）的**工厂**。

    与 :func:`default_scene_store` 逐条同构：惰性、每次新连接、同一个
    ``database_path``（这个库里落着好几个 store，与 :func:`default_relation_store`
    的解释同一处：组合根是唯一一个「服务面拿的是哪一个」看得出来的地方）。

    **这里原来写的是「第五个落在那个库文件上的 store，理由同前四个」**，而它在这个
    模块里是第四个 —— 那句算术没有任何执行器，读起来却像一句已经核对过的事实。所以
    它被删掉了而不是改成另一个数：下一个落在这儿的 store 会让新数也变旧，而删掉之后
    没有东西可以变旧。

    它的惰性比其他几个更要紧一点：池子是**第一次真的发现一块场地时**才建库的，
    而 ``list_assets`` 那条读路径在没建库时要报 ``no_store`` 而不是「这个市场
    一个场地都没有」—— 后者是一句业务结论，而此刻它只是「没接线」。

    ## 它**不是** ``default_scene_store`` 的第二个命名

    ``VenueAsset`` 上写着 ``workspace``，而 Scene 也写着 ``workspace``，但两者的
    边界不同（``ports/pool.py`` 的模块 docstring 逐条写了）：场景是乐观并发按
    ``revision`` 改，池子是累积并修订按 id 覆盖。把两者合成一个参数，等于说
    「这个部署要么两个都有、要么两个都没有」—— 而资产池是市场那一侧的**长期**
    对象，立项记录是可以整批归档的。

    ## 合并规则在**这里**接上，而且只有这一处

    第二个参数是 ``application.pool.merge_assets``（§4 的业务规则：哪些格子不许被
    抹掉、两个判定不一致时报错）。它由这个组合根一次性地接进 store —— 集合点只有
    这一个，所以「同一个库上出现了两条合并规则」在代码里写不出来。这与
    :func:`default_outreach_transport` 把闸装在构造点上是同一条理由：能传规则的
    接口，迟早会有一个调用点传了另一个规则，而它看起来完全正常。
    """
    from ..adapters.sqlite.pool import SqliteVenueAssetStore
    from ..application.pool import merge_assets

    return lambda: SqliteVenueAssetStore(settings.database_path, merge_assets)


def default_relation_store(settings: Settings) -> Callable[[], AccountVenueRelationStore]:
    """责任关系表（``ports/pool.py::AccountVenueRelationStore``）的**工厂**。

    与 :func:`default_asset_store` 逐条同构：惰性、每次新连接、同一个
    ``database_path``（与前面几个 store 落在同一个库文件上，理由同前：组合根是唯一
    一个「服务面拿的是哪一个」看得出来的地方）、合并规则在这里接上而且只有这一处。

    ## 为什么它是第六个函数，而不是与资产池合成一个

    ``ports/pool.py`` 把这两张表放进一个模块，是因为它们的**幂等边界**相同（累积并
    修订）—— 而幂等边界相同不等于接线相同。合成一个参数会让「这个部署有没有资产池」
    与「它有没有关系表」在签名上变成同一件事，于是删掉一半能力必须连另一半一起删。
    ``ports/scenes.py`` 里的 ``SceneStore`` / ``MaintenanceStore`` 分家时是同一条理由
    （见 :func:`default_maintenance_store`）。

    它另有一条自己的可观测后果：关系表是**只有 §5 那一步**才写的表，而资产池在母池
    回执那一步就写了。一个部署先有资产池、后有责任判定是正常的顺序，所以这两条读路径
    的「有没有接线」必须分别答得出来。

    ## 合并规则

    第二个参数是 ``application.relations.merge_relations``：三条规则（
    ``first_seen_at`` 只往早走 / ``last_verified_at`` 只往晚走 / ``evidence_ids``
    取并集）加上一条 ``StoreError``（折进来的键与库里那一行不一致）。同一个库上出现
    两条合并规则在代码里写不出来，与 :func:`default_asset_store` 同一条理由。
    """
    from ..adapters.sqlite.pool import SqliteAccountVenueRelationStore
    from ..application.relations import merge_relations

    return lambda: SqliteAccountVenueRelationStore(settings.database_path, merge_relations)


def default_account_pool_store(settings: Settings) -> Callable[[], AccountPoolStore]:
    """账号池（``ports/pool.py::AccountPoolStore``）的**工厂**。

    与 :func:`default_asset_store` / :func:`default_relation_store` 逐条同构：惰性、
    每次新连接、同一个 ``database_path``（组合根是唯一一个「服务面拿的是哪一个」看得
    出来的地方）、合并规则在这里接上而且只有这一处。

    ## 为什么它是又一个函数，而不是与前两个合成一族

    ``ports/pool.py`` 把三张表放进一个模块，是因为它们的**幂等边界**相同（累积并修订）
    —— 而幂等边界相同不等于接线相同，这一句在这一族里已经被验过两次了。账号池另有一条
    自己的可观测后果：它是**只有 §6 那一步**才写的表，与资产池（母池回执那一步就写）
    和关系表（§5 那一步才写）都不是同一件事。一个部署先有场地、后有账号是正常的顺序，
    所以三条读路径的「有没有接线」必须分别答得出来。

    ## 合并规则

    第二个参数是 ``application.accounts.merge_accounts``：五条规则（两个时刻各走各的
    方向 / 三个清单格与证据取并集 / ``country`` 的 ``None`` 不参与 / ``status`` 的
    ``discovered`` 不覆盖 / 主语那一格先看到的为准）加上一条 ``StoreError``（折进来的
    键与库里那一行不一致）。同一个库上出现两条合并规则在代码里写不出来。
    """
    from ..adapters.sqlite.pool import SqliteAccountPoolStore
    from ..application.accounts import merge_accounts

    return lambda: SqliteAccountPoolStore(settings.database_path, merge_accounts)


def default_account_cohort_store(settings: Settings) -> Callable[[], AccountCohortStore]:
    """冻结成员名单（``ports/pool.py::AccountCohortStore``）的**工厂**（§B12）。

    与 :func:`default_account_pool_store` 同形（惰性、每次新连接、同一个
    ``database_path``），但**没有第二个构造参数**，而这不是遗漏：这一族里前三张表的
    写是「读—改—写」，所以合并规则必须由组合根接进来；这一张表的写**不读旧行**
    （内容寻址的 id 决定了内容），唯一会变的一格 ``created_at`` 取更早的那一个 ——
    一个就地 ``min``，没有第二种折法可以让人选错（``adapters/sqlite/pool.py::
    SqliteAccountCohortStore`` 的类 docstring）。

    所以「合并规则住在哪里」这件事在这一条上没有分叉可谈：没有规则可挑，也就没有
    「同一个库上出现两条合并规则」这个失败形态。
    """
    from ..adapters.sqlite.pool import SqliteAccountCohortStore

    return lambda: SqliteAccountCohortStore(settings.database_path)


def default_review_store(settings: Settings) -> Callable[[], ReviewCaseStore]:
    """收件箱（``ports/review.py::ReviewCaseStore``）的**工厂**（§12）。

    与 :func:`default_scene_store` 逐条同构：惰性、每次新连接、同一个
    ``database_path``。仍写成又一个函数而不是让调用方写一行 lambda，理由逐字
    同一条 —— 落在这个库文件上的 store 已经有若干个，而「服务面拿的是哪一个」
    只有在这里才看得出。

    ## 它**不是** ``default_scene_store`` 的第二个命名

    两者指向同一个库文件、都带 ``workspace`` 与 ``scene_id``，但边界不同：
    场景是乐观并发按 ``revision`` 改（一次改动**覆盖**当前状态），而收件箱里
    ``human_resolution`` 那张表**只增不改**。合成一个参数等于说「这个部署要么
    两个都有、要么两个都没有」—— 而收件箱是一件独立的能力：一个还没有收件箱
    的部署照常立项、照常派发（``tools._open_refusal_cases`` 与
    ``product_loop._activation_gate`` 都如实报 ``not_wired``，而它们报的是
    「没判」，不是「判成可以」）。

    惰性在这里还有一条可观测的后果：**没建库时读一次收件箱不建库**，于是
    「还没有任何一件待办」与「这里本该有东西」在磁盘上长得不一样。

    它**没有第二个构造参数**：这一族没有「同一把键配两份内容怎么折」的问题
    （``ports/review.py`` 的模块 docstring：``open_case`` 由问题驱动、
    ``record_resolution`` 不做去重），所以「合并规则接哪一个」在这一条上没有分叉。
    """
    from ..adapters.sqlite.review import SqliteReviewCaseStore

    return lambda: SqliteReviewCaseStore(settings.database_path)


def default_feedback_store(settings: Settings) -> Callable[[], FeedbackStore]:
    """反馈环（``ports/feedback.py::FeedbackStore``）的**工厂**（§B23）。

    与 :func:`default_review_store` 逐条同构：惰性、每次新连接、同一个
    ``database_path``。仍写成又一个函数而不是让调用方写一行 lambda，理由逐字
    同一条 —— 落在这个库文件上的 store 已经有若干个，而「服务面拿的是哪一个」
    只有在这里才看得出。

    ## 它**不是** ``default_review_store`` 的第二个命名，而两者有一条真的引用

    ``human_resolution`` 那张表**只增不改**，这一族的三张表也是（见
    ``adapters/sqlite/feedback.py`` 的模块 docstring：三种只增、三条不同的去重
    规则）。形状像，边界却不同：收件箱的一行是**一次待办**，这一族的一行是一句
    反馈 / 一条候选 / 一份提案，三者的幂等键是三个不同的原像。合成一个参数会让
    「这个部署有没有收件箱」与「它有没有反馈环」在签名上变成同一件事 —— 而一个
    还没有反馈环的部署照常立项、照常派发，只是那八条工具落回 ``unbacked_message``。

    那条引用是**读方向**上的，而且是这一族唯一的批准来源：一份补丁的
    ``standing`` 从既有的 ``ReviewCase`` 推（``application/feedback.py`` 的
    ``standing_of``），而**批准本身不在这八条里** —— 它仍然只走
    ``approve_policy_change``。所以传给 :func:`feedback_handlers` 的那个
    ``review`` 工厂必须与 ``build_server`` 收到的 ``review_store`` 是**同一个**
    （``_compose`` 传的就是同一份 ``default_review_store(settings)``）：两个库时
    的形态是「提案说在等一个人」与「收件箱里没有这件案子」同时成立，而两个回执
    各自都正常 —— 一次拒绝给了一个不存在的案号。

    惰性在这里的后果与 :func:`default_review_store` 那一条相同：**没建库时读一次
    反馈不建库**，于是「还没有人给过反馈」与「这里本该有东西」在磁盘上长得不一样。
    """
    from ..adapters.sqlite.feedback import SqliteFeedbackStore

    return lambda: SqliteFeedbackStore(settings.database_path)


def default_evidence_reader(settings: Settings) -> Callable[[], EvidenceStore]:
    """证据的**读面**工厂（§B23 的候选清单要它逐条核引用）。**读一次不建库。**

    ## 它为什么不是 ``default_*_store`` 那一族的形状

    那一族的每一个都「构造即 ``ensure_schema``」，于是它们的惰性工厂挡的是
    **组合期**建库（``_compose`` 的契约是「构造服务面需要的一切，不绑端口、
    不建运行目录」，而 ``--check`` 走的就是那一条路）。这一格挡的是**同一件事的
    更晚一步**：它是一条**只读**路径的落点，而「一次读顺手建出一个空库」会让
    「还没有任何证据」与「这里本该有东西」在磁盘上长得一样 —— 后者正是需要被
    看见的那种故障。这条纪律的原文住在 ``adapters/mcp_server/tools.py`` 的
    ``read_store``，**本函数复用那一处**，不把同一句话再说第二遍：两处各写一份
    「库在不在」的判断，分家之后两边各自都正常，而差别只在一台什么都没写过的
    机器上才显形。

    ## 库不在时它抛，而那条异常是**读物**的一部分

    ``read_store`` 在库文件不存在时回 ``None``（「还没有产物」），而这一格的类型
    是一个工厂，交不出 ``None``。所以这里把它折成一条带正文的 ``StoreError`` ——
    调用点 ``application/feedback.py::_read_evidence`` 接住它，把正文原样放进那
    一条引用的 ``reason``。于是「这个部署还没有证据库」与「这个工作区的证据存储里
    没有这个 id 的行」是两句**不同**的话，而它们各自的下一步（去接线 / 去补证据）
    也不同。折成前者会让一条候选的第二格永远读成一个看起来像业务结论的 ``0``。
    """
    from ..adapters.mcp_server.tools import read_store

    def reader() -> EvidenceStore:
        store = read_store(settings)
        if store is None:
            raise StoreError(
                f"这个部署还没有证据存储（{settings.database_path} 不在盘上）—— "
                "所以这一条引用今天核不了，而不是「这条候选没有证据」。"
                "两者在报告上都长成一个 0，而处置相反：去接线 / 去补证据。"
            )
        return store

    return reader


def default_effective_scene_store(settings: Settings) -> Callable[[], EffectiveSceneStore]:
    """生效场景（``ports/workstreams.py::EffectiveSceneStore``）的**工厂**（§B01）。

    ## 它为什么与 ``default_workstream_store`` 分成两个函数，却仍是同一族的形状

    两个类住在**同一个适配器模块**（``adapters/sqlite/workstreams.py``），共用同一个
    库文件，而它们是两个不同的名词：工作流是「一个持续的目标」，生效场景是**某一版
    声明编译出来的那一份可执行结果**。合成一个参数等于说「这个部署要么两个都有、
    要么两个都没有」—— 而一个还没有工作流的部署照常读场景。

    ## 它是这一族里**唯一**一条「必须交给两处同一个对象」的硬要求

    ``effective_scene_handlers`` 与 ``workstream_handlers`` 都收这一格，而
    ``effective_scene.py`` 的 docstring 逐字要求两者是**同一个工厂**：前者编译、
    后者按编译结果派活，两份各建一次的形态是「预览说这一版能跑」与「派活说这一版
    跑不了」同时成立 —— 两个回执各自都正常，问题只在没人能把它们对起来。所以
    ``_compose`` 里这一格要**先具名一个局部**再交给两处，不能各自就地写一行
    ``lambda``（那两行今天恰好同形，而它们**迟早会不一样**）。

    惰性在这里的后果与这一族其余各条相同：组合期不建库，``--check`` 于是一点
    副作用都没有。
    """
    from ..adapters.sqlite.workstreams import SqliteEffectiveSceneStore

    return lambda: SqliteEffectiveSceneStore(settings.database_path)


def default_workstream_store(settings: Settings) -> Callable[[], WorkstreamStore]:
    """工作流（``ports/workstreams.py::WorkstreamStore``）的**工厂**（§B01）。

    与 :func:`default_effective_scene_store` 同一个适配器模块、同一个库文件，理由
    见上（两个名词，不是一条能力的两个名字）。它今天只有一个消费者
    （``workstream_handlers``），之所以仍写成一个具名函数而不是调用点的一行
    ``lambda``，还是这一族那条理由：落在这个库文件上的 store 已经有若干个，而
    「服务面拿的是哪一个」只有在这里才看得出。
    """
    from ..adapters.sqlite.workstreams import SqliteWorkstreamStore

    return lambda: SqliteWorkstreamStore(settings.database_path)


def default_claim_withdrawal_store(
    settings: Settings,
) -> Callable[[], ClaimWithdrawalStore]:
    """撤回（``ports/claim_withdrawal.py::ClaimWithdrawalStore``）的**工厂**（§B01）。

    ## 它与 ``default_outreach_store`` 是同一次撤回的两半，而不是两个候选

    撤回那两条工具收**两格**：``claims``（领活的那一份，既有
    :func:`default_outreach_store`）与 ``withdrawals``（撤回这件事自己的流水）。
    前者答「这一条主张还在不在」，后者答「谁在什么时候把它撤了、为什么」。
    合成一格会让「这个部署有没有领活」与「它记不记得撤回」变成同一件事 ——
    而那正好是 ``withdraw_claim`` 要分开的两件：一次撤回之后**不得**再拿那一条
    当已批准出处，这条判定要落在流水上，不能落在被撤回的那一行自己身上。
    """
    from ..adapters.sqlite.claim_withdrawal import SqliteClaimWithdrawalStore

    return lambda: SqliteClaimWithdrawalStore(settings.database_path)


def default_saved_view_store(settings: Settings) -> Callable[[], SavedViewStore]:
    """保存的视图（``ports/views.py::SavedViewStore``）的**工厂**（§B24）。

    ## 它是这一族的第二条「必须交给两处同一个对象」

    ``views_handlers``（写与读视图）与 ``followups_handlers``（按视图的到期对象
    跟进）都收这一格，而后者逐字要求它与前者是**同一个工厂**：一个 View 里圈的是
    哪些对象，是``followups`` 判「今天该刷谁」的唯一依据。两份各建一次的形态是
    「这个 View 里有三个对象」与「按这个 View 跟进，今天一个都没有」同时成立。
    所以与 :func:`default_effective_scene_store` 同一条：``_compose`` 里先具名，
    再交给两处。

    同模块的 :func:`default_watch_store` / :func:`default_action_item_store` 是
    另外两个名词（一个 View 是**声明**，Watch 是**订阅**，ActionItem 是**待办**），
    所以它们各自具名而不是合成一个。
    """
    from ..adapters.sqlite.views import SqliteSavedViewStore

    return lambda: SqliteSavedViewStore(settings.database_path)


def default_watch_store(settings: Settings) -> Callable[[], WatchStore]:
    """订阅（``ports/views.py::WatchStore``）的**工厂**（§B24）。

    §17 那条链的顺序是「SavedView → enable Watch → due object → one watch Run」，
    而三样东西分别落在三张表上：View 是圈了哪些对象、Watch 是**谁答应了多久看一次**、
    到期判定读的是**两者合起来**。所以它与 :func:`default_saved_view_store` 分成
    两个函数不是洁癖——一个部署可以有很多 View 而只有一个 Watch，反过来也可以。
    """
    from ..adapters.sqlite.views import SqliteWatchStore

    return lambda: SqliteWatchStore(settings.database_path)


def default_action_item_store(settings: Settings) -> Callable[[], ActionItemStore]:
    """待办（``ports/views.py::ActionItemStore``）的**工厂**（§B24）。

    它与 Watch 分开是「一件订阅」与「一件人要做的事」的分界：订阅到期会自己开一轮
    Run，而待办是**专门等人来关**的（``resolve_action_item`` 那一条）。把两者折在
    一张表上，会让「今天该刷谁」与「今天谁欠一件事」有同一个答案 —— 而 §G2' 的
    最后两步（ActionItem 关闭 → 受影响对象重评）正是靠它们不同才走得通。
    """
    from ..adapters.sqlite.views import SqliteActionItemStore

    return lambda: SqliteActionItemStore(settings.database_path)


def default_schedule_store(settings: Settings) -> Callable[[], ScheduleStore]:
    """排期（``ports/scheduling.py::ScheduleStore``）的**工厂**（§B25）。

    ## 与它同族的两条为什么必须各自具名

    ``scheduling_handlers`` 收**三格**（排期、通知、会话绑定），三条各是一个名词：
    排期答「什么时候该发生」，通知答「发生过的事说给谁听」，会话绑定答「这个客户端
    读到哪了」。三者合成一格会让「这个部署会自己开工」与「它会告诉人」变成同一件
    事 —— 而 §G2' 逐字要求那两件事可分辨（业务 Event **不可重复**、sink 失败
    **不能**触发研究重跑）。
    """
    from ..adapters.sqlite.scheduling import SqliteScheduleStore

    return lambda: SqliteScheduleStore(settings.database_path)


def default_notification_store(settings: Settings) -> Callable[[], NotificationStore]:
    """通知（``ports/notification.py::NotificationStore``）的**工厂**（§B25）。

    这个工厂给的是**事件与投递记录**那一份存储，**sink 表不在这里装配** —— 而这不是
    欠接线：sink 表的接线点在**驱动那一侧**
    （``interfaces/trigger_driver.py::deployment_sinks``），理由逐字写在
    ``adapters/mcp_server/scheduling.py`` 的同名决定里 —— 一次投递是「醒来 → 投出去 →
    退出」那条路上的一步，而不是一次工具调用的事；接进服务面会变成「被悄悄忽略」或
    「某一条工具顺手投一次」，两种都坏。

    那一侧今天注册**一个**出口：``InboxSink``（收件箱 JSONL）。Feishu 那一格仍然空着，
    而那是 §20 逐字定下的 ``BLOCKED_EXTERNAL``，**明说它不是试运行的 blocker**。

    服务面（``_compose``）那一侧**仍然没有** sink 表，而它缺席的形态是端口逐字写下的：
    订阅上写的名字在 ``sinks`` 里找不到时，记一条**记在案的** ``unknown``（理由「这个
    部署没有注册这个 sink」），不是静默跳过。而「空表」与「没接线」在回执上**不同形**
    （前者记 ``unknown``，后者回阶段号）—— 这也是这两处不能合成一处的原因。
    """
    from ..adapters.sqlite.notification import SqliteNotificationStore

    return lambda: SqliteNotificationStore(settings.database_path)


def default_session_binding_store(
    settings: Settings,
) -> Callable[[], SessionBindingStore]:
    """会话绑定（``ports/session.py::SessionBindingStore``）的**工厂**（§B25）。

    它答的是「**这个客户端**读到哪一条了」—— 与通知那一格**刻意分开**：§G2' 逐字
    要求「团队 digest 和 session Inbox 都能从**相同 event cursor** 重算」，而
    「重算得出同一份」这件事要成立，光标就不能是事件的属性。折进通知那一张表，
    会让一个客户端的读取位置变成一件对所有订阅者都发生的事。
    """
    from ..adapters.sqlite.session import SqliteSessionBindingStore

    return lambda: SqliteSessionBindingStore(settings.database_path)


def default_wakeup_registry(settings: Settings) -> Callable[[], WakeupRegistry]:
    """唤醒归属（``ports/continuation.py::WakeupRegistry``）的**工厂**。

    ## 为什么返回工厂，而不是实例

    与 :func:`default_scene_store` 逐条同构，而第 2 条在这里**更硬**：
    ``SqliteWakeupRegistry.__init__`` 会 ``ensure_schema``，也就是**会建库**
    （``adapters/sqlite/wakeup.py`` 逐字如此）。而这条工厂的第一个消费者是
    ``get_continuation_status`` —— 一条**只读**工具，它存在的全部意义就是让人在
    什么都没发生的时候问一句「为什么」。它在自检路径上也被构造一次，而
    ``--check`` 建库是自检不该有的副作用。

    所以：**工厂 + 调用点自己先看库在不在**。两道缺一不可 —— 工厂挡住组合期，
    调用点那道 ``database_path.is_file()`` 挡住「这个部署还什么都没写过」，
    后者报的是 ``no_store`` 而不是「没有控制器在跑」。

    ## 它**不是** ``default_review_store`` / ``default_scene_store`` 的第三个命名

    这一个落在**同一个库文件**上，但它的行与那两个都不同：``continuation_owner``
    一行只答「现在是谁在推、哪一代」，没有内容、没有版本、没有历史，而且它可以被
    ``acquire`` 的 CAS **覆盖**（两次成功认领之后，前一个持有者在盘上不留痕迹）。
    合成一个参数会让「这个部署有没有控制器」与「它有没有场景」在签名上变成同一件
    事 —— 而一个没有控制器的部署照常立项、照常派发，它的那条状态页只是答不出
    「有没有人在推」。
    """
    from ..adapters.sqlite.wakeup import SqliteWakeupRegistry

    return lambda: SqliteWakeupRegistry(settings.database_path)


def default_outreach_transport(settings: Settings) -> MailTransport | None:
    """本部署的外发传输，**或 ``None``**（= 这个部署没有外发路径）。

    ## 它返回的是 ``GuardedMailTransport``，而不是 ``SmtpMailTransport``

    两层是两件事，而这里**只交得出外面那一层**：

    * :class:`SmtpMailTransport` 说的是「怎么连上一台中继」（STARTTLS / 凭据 /
      Message-ID 怎么取）。
    * :class:`GuardedMailTransport` 说的是「这封信许不许发给这个人」——
      收件人允许列表那一层闸。

    返回内层对象会让每一个调用点各自去想「我这次要不要包一层」，而漏掉的那一次
    看起来完全正常：信发出去了。所以这道闸**在构造点就装上**，而且内层对象不被
    交出去 —— 拿得到它的地方就绕得过它。

    ## ``None`` 的两个成因在这里合并成一件事

    ``settings.outbound_missing`` 非空 = 六样没配齐 = 没有外发路径。这与「组合根
    决定不构造」在外发这件事上就是同一件事，所以这里不区分。要说的话由
    ``_compose`` 用 ``outbound_missing`` 说出来（``unavailable_outreach_tools``
    的 ``transport_reason``）。
    """
    missing = settings.outbound_missing
    if missing:
        return None
    from ..adapters.guarded_mail import GuardedMailTransport
    from ..adapters.smtp_mail import SmtpMailTransport

    # ``outbound_missing`` 已经保证了六格非空，但**不在这里断言**：
    # 一份「构造器会拒一个坏参数」的断言在这里挡不下什么，而它会让下面这几行
    # 看起来像是自己保证了自己的前置条件。真正的保证是 `outbound_missing` 与
    # 构造参数**读的是同一批字段** —— 那由 `SmtpMailTransport` 的构造期校验守。
    inner = SmtpMailTransport(
        host=settings.smtp_host or "",
        port=settings.smtp_port or 0,
        from_address=settings.outbound_from_address or "",
        username=settings.smtp_username,
        password=settings.smtp_password,
        security=settings.smtp_security,
    )
    return GuardedMailTransport(inner, allowlist=settings.outbound_allowlist)


def outbound_transport_reason(settings: Settings) -> str:
    """「这个部署为什么没有外发路径」那句话，**点名缺的是哪几样**。

    与 :func:`default_outreach_transport` 分开，因为它的用途不同：前者做决定，
    这一句只说事实，而它要被放进 ``send_wave`` 的**不可用回答**里 —— 运维读到的
    是「缺 MARKETOPS_SMTP_HOST 与 MARKETOPS_SMTP_PASSWORD」，不是「工具没注册」。

    点名的是**字段名**而不是环境变量名：``outbound_missing`` 记的就是字段名，
    而把字段名再翻一次成变量名会多出一张必须与实际读法同步的表。那张表比它
    替代的东西更容易错（``config.py`` 里读这些变量的那十几行就是它）。
    """
    missing = settings.outbound_missing
    if not missing:
        raise ConfigError(
            "这个部署的外发路径是齐的，因此没有「缺哪一样」可报。\n"
            "这一句只在该报的时候被读到 —— 一个无条件拼出来的理由串，会在一台"
            "**有**外发路径的机器上被写进某份报告里，而那句话是假的。"
        )
    return (
        "本部署没有外发路径："
        + "、".join(missing)
        + " 这几样没配齐（见 MARKETOPS_SMTP_* 与 MARKETOPS_OUTBOUND_*）。"
        "配齐之前 send_wave 不存在 —— 一条发不出去的信不该有一格可以点。"
    )


def default_correspondence_source(
    settings: Settings,
) -> CorrespondenceSource | None:
    """本部署的**来信来源**，或 ``None``（= 没有这一条工具）。

    ## 配了一半是**事故**，不是「没配」

    ``MARKETOPS_MAIL_CAPTURE_DIR`` 给了而没有 ``MARKETOPS_OUTBOUND_FROM`` 时抛
    ``ConfigError``。理由不是完整性洁癖，是那个组合在运行时的表现：

    ``MailCaptureSource`` 要一个 ``sendbox`` 才算得出「这条回信是回给哪一次的」
    （提供方消息 id 只在 ``(信箱, id)`` 上唯一）。没有发送箱时它要么报错、要么
    把归属猜成空 —— 而后者会让一次收信在报告上看起来完成了，实际每一条都关联不上
    任何一次发送。这与 ``default_dispatcher`` 对 ``MARKETOPS_QUAY_*`` 「配了一半」
    的处置**逐字同一条**（那边也抛，理由写在它的 docstring 里）。
    """
    if settings.mail_capture_dir is None:
        return None
    if settings.outbound_from_address is None:
        raise ConfigError(
            "MARKETOPS_MAIL_CAPTURE_DIR 配了，而 MARKETOPS_OUTBOUND_FROM 没有。\n"
            "  来信的归属靠发送箱：提供方消息 id 只在（信箱, id）上唯一，而"
            "「这条回信回的是哪一次」正是从那一对算出来的。缺了它，收信会每一条"
            "都关联不上任何一次发送，而报告上写着收信成功。\n"
            "  要么补上发送箱，要么把来信来源整个去掉 —— 两者都是完整的部署，"
            "这一种不是。"
        )
    from ..adapters.mail_capture_source import MailCaptureSource

    return MailCaptureSource(
        Path(settings.mail_capture_dir), sendbox=settings.outbound_from_address
    )


__all__ = [
    "advisory_decision_provider",
    "default_account_cohort_store",
    "default_account_pool_store",
    "default_acquisition",
    "default_asset_store",
    "default_correspondence_source",
    "default_crawl",
    "default_decisions",
    "default_dispatcher",
    "default_document_budget",
    "default_http",
    "default_maintenance_store",
    "default_outreach_store",
    "default_outreach_transport",
    "default_relation_store",
    "default_router",
    "outbound_transport_reason",
]

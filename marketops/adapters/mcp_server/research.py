"""Phase D/E 的**研究侧**工具：``search_web``、``fetch_document``、``crawl_site``，
以及产品环那一段的 ``run_discovery``。

``docs/MCP_SERVICE.md`` §3 给了前三条各自的输入形状与必需行为，本模块是那三行的
落点。在此之前研究侧一条工具都没有 —— 服务面只有控制面（Phase C 的五个），
``tools.py`` 的文件头把「为什么当时不建这个文件」写下来了：一个没有消费者的
占位模块会是一份按想象编的接口。今天有了三个消费者，所以文件才出现。

## 第四条不是研究工具，而它住在这里

``run_discovery`` 在目录里是 ``_CONTROL``（``docs/MCP_PRODUCT_LOOP.md`` §2），
研究 worker 既调不动也看不见它。它住在这个模块里，是因为它要的接线与
``search_web`` **逐样相同**（检索提供方、抓取入口、解析路由、额度账本），
而把同一批依赖在第二个模块里再穿一遍，是让「这一段用的哪一个 Serper」有两个
说法。角色由目录决定，不由文件决定 —— 这一条写在 ``security/catalog.py`` 上。

它存在的理由是 §11：用户链路要能**只通过一个 MarketOps MCP** 走完，而发现这一环
原先只有 CLI（``interfaces/cli.py::_cmd_run_discovery`` 的 docstring 论证过它是
运维动词；那一半仍然成立，只是「运维」在服务面上对应的是 controller 这个角色）。

## 这些工具都不自己实现规则

* ``search_web`` 的记账走 ``application/billed_search.py`` —— 与 pipeline
  **同一份**三段式与同一条「付过账的不重发」。上限来自冻结的 profile，**不是
  这个工具的参数**：§8 要求调用方不得静默扩大它，而调用方可能是一个模型。
* ``fetch_document`` 走 ``application/acquisition.py`` —— 与 pipeline **同一个**
  ``fetch_document(mode=...)``。抓取前的安全判据在 ``security/network.py``，由
  每个 fetcher 自己调；本层不重新实现一遍。
* ``crawl_site`` 走 ``application/site_crawl.py`` —— 它把前沿（去哪取）与抓取
  入口（怎么取）接起来。本层只做两件事：**校验调用方给的额度**、把报告投影成
  回执。逐页的字节仍然经由同一个 ``fetch_document``，所以「什么时候该开浏览器」
  在这里也没有第二份实现。
* ``run_discovery`` 走 ``application/discovery_run.py`` —— 与 CLI 的
  ``run-discovery`` **同一个** ``run_discovery`` 与同一个 ``materialize_pool``。
  本层只做三件事：校验这一次调用的形状、把读数投影成有界回执、把运行报告写到
  盘上。展开器、配对、停止理由、工件形状一处都没有第二份实现 —— 两份实现漂移的
  那天，同一次运行会在 CLI 与 MCP 上得出两个池子。

## 本层是「上限」唯一写下来的地方

``pages`` / ``seconds`` 是调用方给的数字，而上界住在这里（``MAX_PAGES_PER_CALL``
等四个常量）。这与 §8 对检索额度的要求是同一条：调用方（可能是一个模型）能请求
「至多多少」，**不能**决定那个上界。越界一律**拒绝**，不截断 —— 截断的代价不是
「少取几页」，而是调用方把那份报告当「取完了」读。

四个上限的校验全部发生在**任何网络 I/O 之前**：一次注定不会取任何页面的调用不该
先替调用方花掉一次 robots 读。判据因此断言的是抓取器的 ``asked`` 是空的，而不是
某句话里出现了哪个词。

这条纪律与 D5/D6a 是同一句：规则只写一处，否则抄错的那一处不会报错。

## 抓到的材料**落成证据**，而不只是回执里的一段预览

``fetch_document`` / ``crawl_site`` 抓到字节之后把它变成一行 durable ``Evidence``
（``application/evidence_ingest.py``），回执里给出那一行的 id。这一步此前**不
存在**：字节落到镜像里了，证据库里却是零行，于是调用方手上没有任何可以引用的
id，而 ``submit_findings`` 的 ``evidence_refs`` 必须非空 —— 一次真实的抓取
正确地以 ``blocked`` 收场，因为没有东西可以引。

``evidence_store_factory`` 因此是**必填**的，不是可选依赖。做成 ``None`` 表示
「本部署不落证据」会让这条缺口原样回来，而且回来的方式是静默的：回执上没有
任何字段说证据没写。这与 ``search=None`` 那个手法不同 —— 那一个是**没有的
入口**（没配 key 的部署调不了检索，调用方拿到一句明确的拒绝），而这一个如果
允许不接线，调用方拿到的是一个看起来正常的抓取回执。

回执里的 ``evidence`` 块是**固定形状**的（``status`` / ``evidence_id`` /
``reason``，见 ``_ingest``），每一个理由各有各的取值。合成一句「没有证据」会让
「这次调用不属于任何账号」与「存储层拒绝了写入」在报告上同形 —— 而两者的处置
一个是换调用方式、一个是修部署。

## 依赖是**注入**的，不是在这里按 settings 构造的

``search`` / ``acquisition`` / ``router`` / ``ledger_factory`` /
``evidence_store_factory`` 全部由构造方传进来（``interfaces/mcp/__main__.py``
是那个构造方）。两个理由：

1. **分层。** 本模块在 ``adapters/`` 里，而 ``interfaces/composition.py`` 在它
   外面 —— ``adapters/`` import ``interfaces/`` 会让分层反向。``default_acquisition()``
   因此不能从这里调用，而「照抄一份」正是本仓最不愿意看到的那种解决办法。
2. **可测。** 判据要能在零网络、零磁盘、零凭据下跑完整条路径。凭据不在时
   服务面**仍然起得来**（见下），这正是最需要被测试的那条路。

``ledger_factory`` 是一个工厂而不是一个账本实例：``sqlite3`` 的连接默认不允许
跨线程使用，而工具处理器可能在别的线程上跑。读一次开一次是本仓已有的先例
（``tools.py::read_store``），这里只是把「怎么开」交给构造方。

## 凭据不在时工具**不接线**，而不是「接线了但每次都失败」

``search_web`` 在预留任何额度**之前**先看有没有检索提供方，没有就给一句明确的
unavailable。少了这一步，一个没接线检索的部署会在每次调用里先扣住一笔额度、
再让 provider 抛错，而那笔预留按 §8 **不会**被退回 —— 于是额度被一次次不存在的
检索吃光，报告上只显示「没有候选」。

「没提供方」在这里是一个 ``None``，不是一个「会抛错的提供方」：``None`` 调不了，
所以这条前置条件无法被漏掉。这与 ``ports/budget.py`` 不放 ``release`` 是同一个
手法 —— 把一件事做成**没有的入口**，而不是「有入口 + 一条注意事项」，
因为前者的默认行为恰好就是正确行为。

连接线由组合根决定（``SerperSearchProvider`` 构造需要一个 API key，而没配 key 的
部署**仍然要起得来** —— 今天它起得来，``get_capabilities`` 报
``serper.configured: false``）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ...application.acquisition import DocumentAcquisition
from ...application.billed_search import BilledSearch
from ...application.budget import RunBudget
from ...application.discovery_run import (
    DiscoveryScope,
    materialize_pool,
    run_discovery as run_open_web_discovery,
)
from ...application.document_router import DocumentRouter
from ...application.evidence_ingest import document_evidence
from ...application.openweb import OPEN_WEB_SOURCE_METHOD
from ...application.queries import market_query
from ...application.site_crawl import PageReceipt, SiteCrawl, SiteCrawlReport
from ...config import Settings
from ...domain.enums import BINDING_ELIGIBLE_SOURCES, QueryOutcome, SourceKind
from ...domain.errors import EvidenceError, MarketOpsError, ParseError
from ...domain.evidence import Evidence
from ...domain.util import check_canonical_id
from ...ports.acquisition import FetchMode, FetchRequest
from ...ports.budget import BudgetLedger
from ...ports.documents import ParsedDocument
from ...ports.search import SearchProvider
from ...ports.store import EvidenceStore
from .tools import (
    Handler,
    plan_step,
    plan_steps,
    require_bundle,
    require_known_step,
)

__all__ = [
    "EVIDENCE_ALREADY_FILED",
    "EVIDENCE_NO_ACCOUNT",
    "EVIDENCE_NO_CONTENT",
    "EVIDENCE_STORE_REFUSED",
    "EVIDENCE_UNPARSED",
    "EVIDENCE_WRITTEN",
    "MAX_CRAWL_SECONDS",
    "MAX_DISCOVERY_LINES",
    "MAX_LINKS_RETURNED",
    "MAX_PAGES_PER_CALL",
    "MAX_PREVIEW_CHARS",
    "MAX_QUERIES_PER_CALL",
    "MAX_QUERY_CHARS",
    "MAX_RESULTS_PER_QUERY",
    "MAX_SEEDS_PER_CALL",
    "MAX_URLS_RETURNED",
    "research_handlers",
]

#: 一次调用最多几条查询。上限由**服务端**定：``queries`` 是调用方给的清单，
#: 而调用方可能是一个模型。
MAX_QUERIES_PER_CALL = 20

#: 每条查询最多要几条结果。同样是服务端事实。
MAX_RESULTS_PER_QUERY = 10

#: 单条查询文本的长度上限。超了**拒绝**，不截断 —— 截断后的查询词是另一个
#: 查询，而它的结果会被当成本次的结果读。
MAX_QUERY_CHARS = 400

#: 解析出的正文最多回多少字符。超出时逐次标 ``text_truncated``。
MAX_PREVIEW_CHARS = 4000

#: ``crawl_site`` 一次调用最多几个种子。每个种子各要一份计划（各读一次 robots
#: 与站点地图 —— ``site_crawl`` 的文件头把这条代价写下来了），所以种子的个数
#: 是这一层唯一能限制「前沿读几遍」的数字。
MAX_SEEDS_PER_CALL = 10

#: 一次调用最多取几页。
MAX_PAGES_PER_CALL = 50

#: 一次调用最多花多少秒。**软界**：一页要花多久取之前不知道，所以最后一次抓取
#: 可以越界，``elapsed_seconds`` 会把它说出来。上界管的是调用方**能请求**多少。
MAX_CRAWL_SECONDS = 300.0

#: 回执里 ``planned_urls`` / ``remaining_urls`` 最多回几条 URL。站点地图可以有
#: 几千条 URL，原样回传会让一次工具调用的回执变成一个几兆的载荷。截断本身没问题
#: —— 问题是截断了不说：``planned_count`` 与 ``remaining_count`` 给的是**准确
#: 总数**，所以「还有多少没取」仍然答得出来（§11：不得静默截断）。
MAX_URLS_RETURNED = 200

#: 一页最多回几条链接。一页可以有几百条（导航、页脚、标签云），而它们的价值随
#: 位置递减。截断要说出来：``links.count`` 是**准确总数**，``links.truncated``
#: 说这一份清单不全（§11：不得静默截断）。
MAX_LINKS_RETURNED = 100

#: 「链接是逐字的」这句话要说在回执里，不能靠读实现 —— 两个工具各用一次。
_LINKS_ARE_VERBATIM = (
    "links.items 里的 url 是文档里**逐字**的 href（可能是 /contact-us 这样的相对"
    "地址，也可能指向别的站）。转换规则取决于文档与页面 URL 两者（还有文档里的"
    "<base href>），本构建**不**替调用方解析：要绝对地址就按同一份回执里的 "
    "final_url 解析，原文在 items[].url 上永远不会丢。"
)
_SNIPPET_IS_NOT_EVIDENCE = (
    "SERP snippet 只用于**发现**：它可能来自缓存、聚合站或另一个站点，"
    "所以它不在 BINDING_ELIGIBLE_SOURCES 里（本条由 domain/bind.py 强制执行，"
    "不是一句约定）。要成为证据必须抓取真实公开页面并在其中发现 literal 值。"
)

#: 回执里 ``evidence.status`` 的**闭集**。每一个取值各有各的处置，合成一句
#: 「没有证据」会让「这次调用不属于任何账号」与「存储层拒绝了写入」在报告上同形。
#:
#: 有 id 的两个（``written`` / ``already-filed``）是**可以引用**的那两个，也是
#: ``reason`` 为空的仅有两个 —— 两条不变量由 ``_evidence_view`` 这一处保证，
#: 判据钉在测试里。
EVIDENCE_WRITTEN = "written"
#: 这次调用没有账号（``account_id`` 为空）。抓取本身合法且成功了，只是没有
#: 可以把这一行记在谁的名下 —— ``Evidence.account_id`` 是必填的规范 id。
EVIDENCE_NO_ACCOUNT = "no-account"
#: 有账号、去写过了，而存储层拒绝了。这是**部署**问题，不是「这一页没内容」。
EVIDENCE_STORE_REFUSED = "store-refused"
#: 这个 id 下**已经有一行同一份材料**了（重复抓取，或并发写入者先到）。
#: 与 ``EVIDENCE_WRITTEN`` 分开是因为它回答的是另一个问题：「这次调用新增了一行
#: 吗」。证据行本身是同一行，``evidence_id`` 一样 —— 引用它没有任何区别。
EVIDENCE_ALREADY_FILED = "already-filed"
#: 没有解析出文档（解析失败，或这次抓取没有落盘字节）。
EVIDENCE_UNPARSED = "unparsed"
#: 解析出了文档，而它没有正文。**不是**一条空证据：``Evidence`` 会拒绝空
#: ``literal_value``，而放宽那条校验是把「我读过一页」当成结论的依据。
EVIDENCE_NO_CONTENT = "no-content"


def research_handlers(
    *,
    settings: Settings,
    search: SearchProvider | None,
    acquisition: DocumentAcquisition,
    router: DocumentRouter,
    ledger_factory: Callable[[], BudgetLedger],
    evidence_store_factory: Callable[[], EvidenceStore],
    crawl: Callable[[Mapping[str, Sequence[str]]], SiteCrawl] | None = None,
) -> dict[str, Handler]:
    """本构建研究侧的处理器，按工具名索引。

    Args:
        settings: 配置。用来定位冻结的 profile。
        search: 检索提供方。``None`` 表示**本部署没有接线检索** —— 构造
            ``SerperSearchProvider`` 需要一个 API key，而一个没配 key 的部署
            仍然要起得来（今天它就起得来，``get_capabilities`` 里
            ``serper.configured`` 为假）。见模块 docstring 最后一段：这是一个
            **没有的入口**，不是「有入口 + 一条注意事项」。
        acquisition: §10 的统一抓取入口。
        router: 文档解析路由（HTML / 文本型 PDF，MinerU 可选 fallback）。
        ledger_factory: 每次调用开一个账本。见模块 docstring。
        evidence_store_factory: 每次调用开一个证据库。**必填**，与 ``search``
            不同：抓到的材料落成证据是这两个工具的正常产出，不是可选接线。
            做成 ``None`` 表示「本部署不落证据」会让 P0-2 那条缺口原样回来，
            而且回来的方式是静默的 —— 回执上不会有一个字段说证据没写。

            为什么是工厂而不是实例：与 ``ledger_factory`` 同一条理由
            （``sqlite3`` 的连接默认不允许跨线程使用）。
        crawl: 多页抓取的**工厂**：``hints`` 进去，一个接好的 ``SiteCrawl``
            出来。``None`` 表示本部署没有接线多页抓取，与 ``search=None`` 是
            同一个手法（没有的入口，而不是「有入口 + 一条注意事项」）。

            为什么是工厂而不是 ``SiteCrawl`` 实例：``hints`` 来自冻结的 profile，
            而 profile 的装载**会失败**。装在这里意味着一次坏 profile 让整个部署
            起不来；装在 ``_crawl_site`` 里，它是一条可读的 ``ToolError``，而服务面
            照常起得来 —— 与 ``search_web`` 那条「凭据不在时工具不接线，而不是
            接线了但每次都失败」是同一个取向。
    """

    def search_web(
        account_id: str,
        queries: list[str],
        limit: int = MAX_RESULTS_PER_QUERY,
        purpose: str = "general",
        source_method: str = "",
    ) -> dict[str, Any]:
        return _search_web(
            settings=settings,
            search=search,
            ledger_factory=ledger_factory,
            account_id=account_id,
            queries=queries,
            limit=limit,
            purpose=purpose,
            source_method=source_method,
        )

    def fetch_document(
        url: str,
        purpose: str = "page",
        account_id: str = "",
        mode: str = FetchMode.AUTO.value,
        source_method: str = "",
    ) -> dict[str, Any]:
        return _fetch_document(
            settings=settings,
            acquisition=acquisition,
            router=router,
            evidence_store_factory=evidence_store_factory,
            url=url,
            purpose=purpose,
            account_id=account_id,
            mode=mode,
            source_method=source_method,
        )

    def crawl_site(
        seeds: list[str],
        purpose: str,
        pages: int = 10,
        seconds: float = 30.0,
        mode: str = FetchMode.AUTO.value,
        account_id: str = "",
        source_method: str = "",
    ) -> dict[str, Any]:
        return _crawl_site(
            settings=settings,
            crawl=crawl,
            router=router,
            evidence_store_factory=evidence_store_factory,
            seeds=seeds,
            purpose=purpose,
            pages=pages,
            seconds=seconds,
            mode=mode,
            account_id=account_id,
            source_method=source_method,
        )

    def run_discovery(
        scope: str,
        region: str,
        limit: int = MAX_RESULTS_PER_QUERY,
        max_candidates: int = 25,
        fetch_pages: bool = True,
    ) -> dict[str, Any]:
        return _run_discovery(
            settings=settings,
            search=search,
            acquisition=acquisition,
            router=router,
            ledger_factory=ledger_factory,
            evidence_store_factory=evidence_store_factory,
            scope=scope,
            region=region,
            limit=limit,
            max_candidates=max_candidates,
            fetch_pages=fetch_pages,
        )

    return {
        "crawl_site": crawl_site,
        "fetch_document": fetch_document,
        # 目录里它是 ``_CONTROL``，而这张表里与它相邻的三条是 ``_RESEARCH``：
        # 角色不是由**表**决定的，是由 ``security/catalog.py`` 决定的。见
        # ``_run_discovery`` 的 docstring：它花的是冻结的检索额度，所以能调它的人
        # 是经营这个市场的那一个，而不是执行一次研究的那一个。
        "run_discovery": run_discovery,
        "search_web": search_web,
    }


# ----------------------------------------------------------------------
# search_web
# ----------------------------------------------------------------------
def _search_web(
    *,
    settings: Settings,
    search: SearchProvider | None,
    ledger_factory: Callable[[], BudgetLedger],
    account_id: str,
    queries: list[str],
    limit: int,
    purpose: str,
    source_method: str,
) -> dict[str, Any]:
    key = _canonical(account_id, "account_id")
    texts = _query_texts(queries)
    if limit < 1 or limit > MAX_RESULTS_PER_QUERY:
        raise ToolError(
            f"limit 必须是 1 到 {MAX_RESULTS_PER_QUERY} 之间的整数，实际拿到 {limit!r}。"
            "上限由服务端定 —— limit 是调用方给的数字，而调用方可能是一个模型。"
        )
    # 与其余参数校验同一段：**在预留任何额度之前**。一个拼错的步骤名若走到下面，
    # 就会先扣住一笔账本额度再被拒，而那笔预留按 §8 不退。
    #
    # 校验分两半，而两半**在不同时刻**问：
    #
    # * **这一格在闭集里**（``require_known_step``）排在这里 —— 「名字拼错了」是
    #   **这次调用**的问题，它必须在「本部署没接检索提供方」那条**部署**判据之前
    #   说出来。反过来的话，调用方按那句话去配了 SERPER_API_KEY，配好之后同一句
    #   拒绝会再出现一次，而两次它都不是真正的原因。
    # * **这一步在这一轮的计划里**（下面 ``plan_step``）排在部署判据之后 —— 它要读
    #   声明，而一个装不出 profile 的部署不该把「没接检索」这句话盖掉。
    #
    # 两半都仍在预留额度之前。第二半是 ``plan_step`` 里做的，它会把第一半重做一遍：
    # 那是一次注册表查表，纯函数，没有第二个实现。
    require_known_step(source_method)
    if search is None:
        # **在预留任何额度之前**。少了这一步，一个没接线检索的部署会在每次调用
        # 里先扣住一笔额度、再让 provider 抛错，而那笔预留按 §8 不会被退回 ——
        # 于是额度被一次次不存在的检索吃光，报告上只显示「没有候选」。
        raise ToolError(
            "本部署没有接线检索提供方，所以这次检索不会发生。"
            "最常见的原因是没配 SERPER_API_KEY（get_capabilities 的 integrations 里"
            "serper.configured 会说明），也可能是这个实例只接了控制面。"
            "这不是一次「检索过、没有结果」——两者在报告上必须分得开。"
            "凭据按 §12 从本机 secret 区装载，不写进仓库、不回显。"
        )

    bundle = require_bundle(settings)
    step = plan_step(source_method, steps=plan_steps(bundle))
    try:
        budget = RunBudget.for_account(ledger_factory(), bundle, key)
    except MarketOpsError as exc:
        # 上限没声明 / 账本读不出来 —— 都是**部署**问题，但必须作为一条结果
        # 回到调用方：抛出去会被 SDK 换成一句通用的 UnexpectedToolError。
        raise ToolError(f"检索额度不可用：{type(exc).__name__}: {exc}") from exc

    billed = BilledSearch(search=search, budget=budget)
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for text in texts:
        receipt = billed.query(
            market_query(bundle, text=text, scope=key, purpose=purpose), limit=limit
        )
        if receipt.outcome is QueryOutcome.FAILED:
            errors.append(f"{text!r}: {type(receipt.error).__name__}: {receipt.error}")
        results.append(_query_receipt(receipt, settings))
        if receipt.outcome is QueryOutcome.EXHAUSTED:
            # 撞穿上限就停：继续只会拿到同一句话 N 遍，而每一次都要多跑一遍
            # 账本事务。剩下的查询在回执里**不出现**，并由 not_sent 说明。
            break

    asked = len(texts)
    sent = sum(1 for item in results if item["outcome"] == QueryOutcome.SENT.value)
    # 「发出去了、而用量不知道」的那几发。它与 queries_sent **不是**互补的：
    # 差的那些里还有 SKIPPED 与 EXHAUSTED（压根没派发，用量确切地是 0）。三件事
    # 分开数，合成一句就会让「去向不明」在报告上重新变成 0。
    unknown = sum(1 for item in results if item["usage_unknown"])
    return {
        "account_id": key,
        "source_method": step,
        "queries": results,
        "queries_asked": asked,
        "queries_sent": sent,
        "queries_usage_unknown": unknown,
        "queries_not_attempted": asked - len(results),
        "errors": errors,
        "budget": _budget_view(budget, settings),
        "discovery_only": True,
        "note": (
            f"hits 里的 snippet 只用于发现，不能作证据。{_SNIPPET_IS_NOT_EVIDENCE}"
            "errors 只记**调用失败**；额度用尽记在对应那一条的 outcome 上"
            "（budget_exhausted）—— 两者不是同一件事，不该合成一句话。"
            "queries_not_attempted 非 0 表示额度用尽后剩下的查询**没有发出**："
            "静默丢掉它们会让「查了没有」与「没查」在报告上同形。"
            "queries_usage_unknown 是**发出去了、而用量不知道**的那几发（请求派发出去"
            "了，回来的是一条异常）：逐条回执里那一格的 api_calls 是 0，而那个 0 **不是"
            "用量**，是「没有结果可以读」。它与 queries_sent **不是互补的** —— 差的那"
            "部分里还有 skipped 与 budget_exhausted，那两种压根没派发，用量确切地是 0。"
            "三件事分开数：合成一句会让「去向不明」重新变成 0。"
            "source_method 是这次调用**服务计划里的哪一步**，取值是服务端的来源方法"
            "闭集（`domain/sources.py`），与 `EffectiveResearchPlan.source_methods` "
            "同一张表 —— 它是「这条 provider 调用因哪一步被选择」那条反查的锚点。"
            "它为 null 表示本次调用**没有声称**服务任何 plan 步骤（发现期，或一次"
            "由人驱动的临时取证），不是「未知」也不是默认值：编一个步骤名会让反查"
            "答出一个不存在的步骤，而报告上看不出异常，所以这一格宁可空着。"
        ),
    }


def _query_texts(queries: list[str]) -> list[str]:
    """校验查询清单。每一条要么原样发出，要么整次调用被拒。"""
    if not isinstance(queries, list) or not queries:
        raise ToolError("queries 必须是一个非空的查询文本列表。空清单不是「没有结果」。")
    if len(queries) > MAX_QUERIES_PER_CALL:
        raise ToolError(
            f"一次调用最多 {MAX_QUERIES_PER_CALL} 条查询，实际 {len(queries)} 条。"
            "上限由服务端定：额度是按账号记的，一次塞进来的查询越多，"
            "越容易在调用方看不见的地方把额度用光。"
        )
    cleaned: list[str] = []
    for item in queries:
        if not isinstance(item, str) or not item.strip():
            raise ToolError(f"查询必须是非空字符串，实际拿到 {item!r}")
        text = item.strip()
        if len(text) > MAX_QUERY_CHARS:
            raise ToolError(
                f"查询文本超过 {MAX_QUERY_CHARS} 字符（实际 {len(text)}）。"
                "这里**不截断**：截断后的查询词是另一个查询，而它的结果会被读成本次的结果。"
            )
        cleaned.append(text)
    return cleaned


def _query_receipt(receipt, settings: Settings) -> dict[str, Any]:
    """一次检索的回执投影。``outcome`` 是四种终态里的一种，**不合并**。

    见 ``domain/enums.QueryOutcome``：把「付过账了」「额度用尽」「调用失败」
    合成一个「没发送」，报告上就再也分不出哪一次运行是该停下来的。

    ``country`` / ``language`` 是**这一次问的市场**，取自那条检索式自己带的格子
    （来源是 profile 的声明，见 ``application/queries.py::market_query``）。它们
    不属于 ``query`` 那一格：检索式文本里没有市场，市场是发请求时附上去的。

    ``usage_unknown`` 回答的是一个 **``outcome`` 回答不了**的问题：这一发**已经
    发出去了**（``dispatched``），而它花了多少**没有人知道**。``outcome: "failed"``
    说得出发过一条请求、回来的是一条异常，但它读不出「这一发的用量去向不明」——
    而那一格的 ``api_calls`` 是 0，看起来与「压根没发」一模一样。规则住在
    ``QueryReceipt.usage_unknown`` 上（判据在 ``tests/test_billed_search.py``），
    这里只把它投影出来，**不重算**：两处各写一遍会漂开，而漂开的那一份会让报告
    重新开始替一次去向不明的调用编一个 0。
    """
    payload: dict[str, Any] = {
        "query": receipt.query.text,
        # 市场那两格逐条回执都要有，哪怕它们在一次调用里是同一个值：回执被单独读到
        # 的时候也必须说得出「这一条问的是哪个国家的索引、哪种语言」。它们不在
        # 检索式里（``gl``/``hl`` 由适配器发），所以只从检索式那一格看不出来 ——
        # 缺了这两格，一次英国检索与一次美国检索的回执**逐字同形**。
        "country": receipt.query.country,
        "language": receipt.query.language,
        "purpose": receipt.query.purpose,
        "outcome": receipt.outcome.value,
        "usage_unknown": receipt.usage_unknown,
        "api_calls": receipt.api_calls,
        "hits": [_hit_view(hit) for hit in receipt.hits],
        "raw_response_ref": _relative_ref(
            receipt.result.raw_response_path if receipt.result is not None else None, settings
        ),
        "from_cache": bool(receipt.result.from_cache) if receipt.result is not None else False,
        "error": "" if receipt.error is None else f"{type(receipt.error).__name__}: {receipt.error}",
        "reservation_id": receipt.reservation.reservation_id if receipt.reservation else None,
    }
    return payload


def _hit_view(hit) -> dict[str, Any]:
    return {
        "url": hit.url,
        "title": hit.title,
        "snippet": hit.snippet,
        "rank": hit.rank,
        "query": hit.query,
        # 由**判据本身**算出来，不写死 False：这条标记与 domain/bind.py 是否
        # 真的拒绝 snippet 是同一个问题，写死的那一份漂移了没人会发现。
        "binding_eligible": SourceKind.SEARCH_SNIPPET in BINDING_ELIGIBLE_SOURCES,
    }


def _relative_ref(path: Path | None, settings: Settings) -> str | None:
    """落盘产物的**相对**引用（检索的原始响应、抓取的身体字节）。

    相对 ``data_root`` 而不是绝对路径：调用方要的是一个能回传的引用，
    不是这台机器的目录布局。引擎在什么位置落盘是部署的实现细节。

    参数是**已经取出来的那个路径**，不是一个回执对象。两个调用方（检索回执与
    抓取结果）各有各的字段名，而「相对谁、落到外面怎么办」只有一条规则 ——
    早先这里收的是回执，于是抓取那一侧传进来时读的是 ``.result``，一个在
    ``FetchResult`` 上不存在的属性。
    """
    if path is None:
        return None
    try:
        return str(path.relative_to(settings.data_root))
    except ValueError:
        # 落到了 data_root 之外 —— 那是配置问题，但引用仍要能给出，
        # 否则调用方看到的是「没有落盘产物」，而真相是「它在别处」。
        return str(path)


def _budget_view(budget: RunBudget, settings: Settings) -> dict[str, Any]:
    """额度现状。``ceiling`` **不是**这次调用的参数，它来自冻结的 profile。

    ## 账本里没有这个 scope 的一行时，**四格都是 ``None``**

    这里原先是 ``spent`` / ``reserved`` 写 ``0``、``ceiling`` / ``remaining`` 写
    ``None`` —— 同一次「没有那一行」在同一格投影里被表示成了两种东西，而
    ``spent: 0`` 读起来是「确实一分没花」，不是「我不知道」。「没有行」与「花了 0」
    在这一层分不出来：账本是按调用写的，一次都没写过的 scope 与一个花了 0 的 scope
    在盘上长得一模一样。所以给 ``None`` —— 本仓的纪律是未知不得被写成 0，
    而 ``0`` 恰好是这里最容易被当成测量的那个值。
    """
    usage = budget.usage()
    item = usage[0] if usage else None
    return {
        "scope": budget.scope,
        "line": budget.line.value,
        "ceiling": item.ceiling if item else None,
        "spent": item.spent if item else None,
        # reserved 单独列出：它是在途（含超时后悬着、等着对账）的量，
        # 与 already spent 的处置完全不同。
        "reserved": item.reserved if item else None,
        "remaining": item.remaining if item else None,
        "note": (
            "ceiling 来自冻结的 profile（search.max_queries），不是本次调用的参数 ——"
            "§8：调用方不得静默扩大上限。reserved 是在途的量，失败或超时的调用"
            "**不会**把它退回来（那时并不知道对方计费没有），所以它可能大于 0"
            f"而本次没有任何一次成功的检索。账本里没有这个 scope 时四格都为 null："
            f"「没有那一行」不是「花了 0」。账本：{settings.database_path.name}"
        ),
    }


# ----------------------------------------------------------------------
# run_discovery
# ----------------------------------------------------------------------


#: 一次 ``run_discovery`` 回执里逐条端出的读数上限（逐条命中、逐条站点、逐条检索式的
#: 回执，各自按这个数截）。**超出的部分不消失** —— 完整报告在盘上（``report_path``），
#: 这里只是把它截到一次工具调用放得下的体积，并在回执里把截断说出来。
#:
#: 与 ``crawl_site`` 的 ``MAX_LINKS_RETURNED`` 同一条纪律：静默截断会让「一共 50 条」
#: 与「有 500 条、你看到的是前 50 条」在回执上长得一模一样，而两者的下一步不同。
MAX_DISCOVERY_LINES = 50


def _run_discovery(
    *,
    settings: Settings,
    search: SearchProvider | None,
    acquisition: DocumentAcquisition,
    router: DocumentRouter,
    ledger_factory: Callable[[], BudgetLedger],
    evidence_store_factory: Callable[[], EvidenceStore],
    scope: str,
    region: str,
    limit: int,
    max_candidates: int,
    fetch_pages: bool,
) -> dict[str, Any]:
    """跑一轮开放网络发现，把池子落成一份可以重新喂进受理的工件。

    ## 它为什么在这张表上（而 CLI 那边写着「它为什么是 CLI」）

    ``interfaces/cli.py::_cmd_run_discovery`` 的 docstring 论证过它是运维动词。
    那条论证有一半仍然成立：跑这一轮的人要有额度、有上限、有理由。它在 MCP 上
    成立的那一半是**角色**——这一条是 ``_CONTROL``，研究 worker 既调不动也看不见
    （``MCP_SERVICE.md`` §6：看不见与调不动都要）。

    而它必须在本表上的理由是 §11 的那一条：用户链路要能**只通过一个 MarketOps MCP**
    走完。发现这一环原先只有 CLI，于是链上第 3 步在干净客户端里根本不存在 ——
    一个「按 §11 不算数」的缺口。所以这里不是给运维多开一个入口，是把链补上。

    ## 花的是**冻结的**额度，不是这次调用的参数

    ``limit`` / ``max_candidates`` 是这一轮的形状，而**额度**（``search.max_queries``）
    来自 profile，调用方给的 ``limit`` 改不了它。撞上它时停下来的是
    ``BUDGET_EXHAUSTED``，与「候选枯竭」是两个不同的读数，都在 ``stops`` 里。

    ## ``region`` 必填、且**没有缺省值**

    与 CLI ``--region`` 逐字同一条理由：它是绑给检索式里 ``{region}`` 的**冻结**值，
    会逐字进工件、进回执、进每一个下游读数。给它一个缺省值（比如 ``GB``）等于替
    这一次运行做了一个它没做过的决定，而那个决定不留痕。

    ## 一行都没产出时**不写工件**

    空工件会被读成「跑了、一行都没成」，而一份空工件连 ``read_motherpool`` 都会拒绝。
    为什么空，答案在 ``stops`` 里，所以回执里 ``pool.written: false`` 加上那几条停止
    理由 —— 不是一次安静的、看起来正常的空池。

    ## 读数是**有界**端出的

    逐条的那几行各自截到 :data:`MAX_DISCOVERY_LINES`，并带上 ``truncated``。完整报告
    在 ``report_path`` 指向的那份 JSON 里，一次工具调用不该被一份 500 行的读数撑爆。
    """
    if search is None:
        # **在预留任何额度之前**。与 ``_search_web`` 同一处、同一条理由：没有接线检索
        # 的部署上，一次「先扣额度、再让 provider 抛错」的调用会把额度一次次吃光，
        # 而报告上只显示「没有候选」。这不是一次「检索过、没有结果」。
        raise ToolError(
            "本部署没有接线检索提供方，所以这一轮发现不会发生。"
            "最常见的原因是没配 SERPER_API_KEY（get_capabilities 的 integrations 里"
            "serper.configured 会说明）。这不是一次「跑过、没有候选」——两者在"
            "回执上必须分得开。凭据按 §12 从本机 secret 区装载，不写进仓库、不回显。"
        )

    try:
        check_canonical_id(scope, "scope")
        frozen = DiscoveryScope(
            scope=scope,
            region=region,
            limit=limit,
            max_candidates=max_candidates,
            fetch_pages=fetch_pages,
        )
    except ValueError as exc:
        # 在 MCP 这一侧，「scope 不是规范形式」是**调用方给错了**，所以它是
        # ToolError；CLI 那一侧同一个事实叫「这一次调用不成立」（退 2）。
        raise ToolError(str(exc)) from exc
    if not frozen.region.strip():
        raise ToolError(
            "region 不能是空白。它是这一轮**冻结**的地区值（绑给检索式里的 "
            "{region}），会逐字进工件与回执。它没有缺省值：编一个等于替这一次运行"
            "做了一个它没做过的决定，而那个决定不留痕。"
        )

    bundle = require_bundle(settings)
    # 与 CLI 同一次序：先建运行目录，再跑。``run_discovery`` 内部还会在**发第一发
    # 查询之前**再问两件事（工件名是否被占用、profile 有没有声明额度），两件都在
    # 花掉钱之前 —— 所以这里不重复它们，只把目录准备好。
    settings.ensure_data_dirs()
    try:
        run = run_open_web_discovery(
            bundle=bundle,
            scope=frozen,
            search=search,
            acquisition=acquisition,
            store=evidence_store_factory(),
            router=router,
            ledger=ledger_factory(),
            inputs_dir=settings.inputs_dir,
        )
    except MarketOpsError as exc:
        # 上限没声明 / 工件名被占用 / 检索式一条都展开不出来 —— 都是「这一轮没跑起来」，
        # 而且都在花钱之前。抛出去会被 SDK 换成一句通用的 UnexpectedToolError。
        raise ToolError(f"这一轮发现没有跑起来：{type(exc).__name__}: {exc}") from exc

    report = run.as_dict()
    report_path = settings.benchmarks_dir / f"{run.run_id}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )

    pool = materialize_pool(settings.inputs_dir, run)
    lines, lines_truncated = _bounded_readings(run.hits)
    sites, sites_truncated = _bounded_readings(run.sites)
    queries, queries_truncated = _bounded_readings(run.queries)
    evidence_ids, evidence_truncated = _bounded_readings(run.evidence_ids)

    payload: dict[str, Any] = {
        # 这一轮是谁的场景。**不是参数** —— 一个部署只能跑它自己那份 profile，
        # 而 ``bundle.profile_id`` 与 ``settings.profile`` 被装载期那条判据钉成
        # 同一个值（adapters/profile_loader.py）。所以调用方没有另一个值可给，
        # 回执里这一格也不是「调用方说的场景」，是**这一次运行真的站在哪个场景上**。
        "scene_id": run.profile_id,
        "run_id": run.run_id,
        "scope": frozen.as_dict(),
        "artifact_ref": run.artifact_ref,
        "report_path": _relative_ref(report_path, settings),
        "pool": {
            "kept": len(run.pool),
            "candidate_ids": [site.candidate_id for site in run.pool],
            "truncation": run.truncation.as_dict(),
            "written": pool is not None,
            "artifact_path": None if pool is None else _relative_ref(pool.artifact_path, settings),
            "receipt_path": None if pool is None else _relative_ref(pool.receipt_path, settings),
            "counts_line": None if pool is None else pool.receipt.counts_line,
            "blocker": None if pool is None else pool.receipt.blocker,
            "why_not_written": (
                None
                if pool is not None
                else (
                    "这一轮一行都没有产出，所以**没有**写任何工件。一份空工件会被读成"
                    "「跑了、一行都没成」，而它其实什么都没声明 —— 连 read_motherpool "
                    "都会拒绝它。为什么空，答案在 stops 里。"
                )
            ),
        },
        "readings": {
            # 检索式那三格：``count`` 是**计划**展开出来的条数（没轮上的也占一行），
            # 而「真的发出去了几条」在 ``by_outcome`` 的 ``sent`` 那一格里。两个数
            # 在额度用尽的轮次上差得很远，所以它们不能合成一个 —— 合成的后果是
            # 「542 条里跑了 20 条」被读成「跑了 542 条」。
            "queries": {
                "count": len(run.queries),
                "by_outcome": run.query_counts(),
                "records": queries,
                "truncated": queries_truncated,
            },
            "hits": {
                "count": len(run.hits),
                "by_outcome": run.hit_counts(),
                "records": lines,
                "truncated": lines_truncated,
            },
            "sites": {
                "count": len(run.sites),
                "named": sum(1 for site in run.sites if site.named),
                "fetched": run.fetch_counts(),
                "records": sites,
                "truncated": sites_truncated,
            },
            "evidence": {
                "count": len(run.evidence_ids),
                "ids": evidence_ids,
                "truncated": evidence_truncated,
            },
            "usage": [
                {
                    "line": usage.line.value,
                    "ceiling": usage.ceiling,
                    "spent": usage.spent,
                    "reserved": usage.reserved,
                    "refunded": usage.refunded,
                }
                for usage in run.usage
            ],
        },
        "qualifications": run.qualification_summary(),
        # 停下来时是**哪一档**，逐条列出、不合并 —— 与 CLI 那一侧印的是同一批值。
        "stops": [stop.value for stop in run.stops],
        "caveats": list(run.caveats),
        "source_method_id": OPEN_WEB_SOURCE_METHOD.id,
        "truncated": {
            "lines": MAX_DISCOVERY_LINES,
            "note": (
                "逐条读数各自截到这个条数；完整报告在 report_path 指向的那份 JSON 里。"
                "截断**不**改变任何一格的 count。"
            ),
        },
    }
    payload["next_step"] = (
        "工件已经落盘，可以把这一轮的读数喂进受理："
        f'submit_research(input_kind="discovery_scope", input_ref="{run.artifact_ref}")。'
        if pool is not None
        else (
            "这一轮没有写出工件，所以没有可以喂进受理的东西。要看的是 stops 与那一节"
            "读数的分布 —— 「跑了、没搜到」与「跑了、一行都没成」是两件事。"
        )
    )
    return payload


def _bounded_readings(rows: Sequence[Any]) -> tuple[list[Any], bool]:
    """截到 :data:`MAX_DISCOVERY_LINES`，并如实说有没有截。

    ``rows`` 里可能是 dataclass（``hits`` / ``sites``）也可能是 dict（``queries``），
    所以这里统一走一次 ``as_dict()`` 当它有，没有就原样留着 —— 投影是**调用点**的事，
    这一层只管数量。

    ## 名字里那个 ``_readings`` 不是修饰

    这个模块里**已经有一条** ``_bounded``（截 :data:`MAX_URLS_RETURNED`，给
    ``crawl_site`` 用）。同名两条函数在 Python 里后者胜、``ruff`` 默认的规则集
    也不报 F811，于是先写的那条会被静默遮掉：``crawl_site`` 的 200 会变成 50，
    套件照绿（没有一条用例的计划 URL 超过 50 条），而回执少了一截没人知道。
    两条函数截的是**不同的东西**，名字就得是两个。
    """
    materialised: list[Any] = [
        row.as_dict() if hasattr(row, "as_dict") else row for row in rows
    ]
    return materialised[:MAX_DISCOVERY_LINES], len(materialised) > MAX_DISCOVERY_LINES


# ----------------------------------------------------------------------
# fetch_document
# ----------------------------------------------------------------------
def _fetch_document(
    *,
    settings: Settings,
    acquisition: DocumentAcquisition,
    router: DocumentRouter,
    evidence_store_factory: Callable[[], EvidenceStore],
    url: str,
    purpose: str,
    account_id: str,
    mode: str,
    source_method: str,
) -> dict[str, Any]:
    if not url.strip():
        raise ToolError("url 不能为空")
    try:
        resolved_mode = FetchMode(mode)
    except ValueError:
        allowed = ", ".join(item.value for item in FetchMode)
        raise ToolError(f"mode 必须是 {allowed} 之一，实际拿到 {mode!r}") from None
    # 参数校验段，**在任何网络 I/O 之前**：一个拼错的步骤名不该先把这次抓取发出去，
    # 再在回执里说它不属于任何步骤。第二半（这一步在不在这一轮的计划里）要读 Scene，
    # 所以这里比从前多一次 profile 装载 —— 一次磁盘读，与下面那次抓取的代价不是一个
    # 量级，而省掉它的形态是「步骤名对得上注册表就放行」，那正是这一片要关掉的口子。
    bundle = require_bundle(settings)
    step = plan_step(source_method, steps=plan_steps(bundle))

    key = _canonical(account_id, "account_id") if account_id.strip() else ""
    # URL 判据**不在这里**：它住在 security/network.py，由每个 fetcher 在发请求
    # 之前调用。在这里再判一次就是一份判据两个实现，而漂移的那一份会表现为
    # 「工具说可以、fetcher 说不可以」（或更糟的反过来）。
    fetched = acquisition.fetch_document(
        FetchRequest(url=url.strip(), scope=key, purpose=purpose), mode=resolved_mode
    )
    final = fetched.final
    # **解析一次**，两个消费者共用：回执上的 document 块与落成证据的那一行。
    # 各解析一次的话，同一份字节会有两次解析结果，而「回执说解析成功、证据说
    # 没有正文」这种不一致在报告上看不出来 —— 两边都「做完了」。
    document, reason = _parse(
        router, body_path=final.body_path, content_type=final.content_type
    )
    return {
        "mode": fetched.mode.value,
        "source_method": step,
        "decision": fetched.decision,
        "attempts": [_attempt_view(attempt, settings) for attempt in fetched.attempts],
        "final": _attempt_view(final, settings),
        "browser_seconds": fetched.browser_seconds,
        "document": _document_view(document, reason),
        "evidence": _ingest(
            store=evidence_store_factory(),
            # 血统取自本进程的配置：这一页是被**这个部署**抓下来的。
            workspace=settings.profile,
            account_id=key,
            # 证据指向**真的读到内容的那一页**：重定向之后请求 URL 与最终 URL
            # 不同，而两者各写一行会让同一份材料有两个身份。
            source_url=final.final_url or final.request.url,
            document=document,
            reason=reason,
            http_status=final.status_code,
            tier=final.tier.value,
        ),
        "note": (
            "attempts 保留走过的**每一次**，不只最终结果：HTTP 那一次是"
            "http_js_required 这类计数的唯一来源，只回最终结果会让"
            "「先 JS_REQUIRED 再浏览器成功」读成「HTTP 一次都没失败过」。"
            "decision 是升级与否的**理由**；未升级与升不了级是两件事。"
            "落盘的产物是 body_path 指向的镜像字节；document 里的正文只是"
            "本次调用的回执，不是产物。evidence 是**落成的那一行证据**："
            "它有 id，可以用 get_evidence 按 id 解回材料，也可以被 "
            "submit_findings 引用 —— document 里那段预览不行。"
            "evidence.status 是闭集，四个取值各有各的处置；status 不是 written "
            "时 reason 非空，而那不是「这一页没有价值」。" + _LINKS_ARE_VERBATIM
            + "source_method 是这次抓取**服务计划里的哪一步**，取值是服务端的来源方法"
            "闭集（`domain/sources.py`），与 `EffectiveResearchPlan.source_methods` "
            "同一张表。它为 null 表示本次调用**没有声称**服务任何 plan 步骤，不是"
            "「未知」也不是默认值。"
        ),
    }


def _ingest(
    *,
    store: EvidenceStore,
    workspace: str,
    account_id: str,
    source_url: str,
    document: ParsedDocument | None,
    reason: str,
    http_status: int | None,
    tier: str,
) -> dict[str, Any]:
    """一份解析好的文档 → 一行 durable 证据，并给出**固定形状**的结果。

    ``workspace`` 是这一行的血统，由调用方从配置里取（``settings.profile``）。
    **这一层不推断它**：这个函数拿到的只有一份解析结果，而解析结果不是一条血统
    声明。取值就是本部署的工作区 —— 一个进程服务的市场只有它自己配置里的那一个。

    回执块三个字段，两条不变量（``tests/test_mcp_research_tools.py`` 钉着）：

    * ``evidence_id`` 非空 ⟺ ``status`` 是有 id 的那两个之一（可以引用）；
    * ``reason`` 非空 ⟺ 那一条**没有**可引用的 id。

    写成三条独立的信息（而不是一个 ``written: bool`` 加一句备注）是因为
    **每一个不写的理由处置都不同**：``no-account`` 要换调用方式，
    ``store-refused`` 要修部署，``unparsed`` / ``no-content`` 是这一页本身的
    性质（一个换一条路，一个换一页）。一个布尔量把它们压平之后，报告上只剩
    「没有证据」。

    ``status`` 是**闭集**而不是异常：一个抛出去的失败会把整份抓取回执带走
    （含已经取回来的字节），而调用方在「这一页没有证据」与「这次调用崩了」
    之间分不出来。存储层的失败只被降级为一条**说得清理由**的结果，不被降级
    成一句「没有证据」—— 两者是不同的 ``status``。
    """
    if not account_id:
        return _evidence_view(
            EVIDENCE_NO_ACCOUNT,
            "这次调用不属于任何账号（account_id 为空），所以这一行没有可记在谁名下"
            "的位置 —— Evidence.account_id 是必填的规范 id，而编一个出来会让证据"
            "落进一个并不存在的账号。抓取本身是成功的，字节落在 body_ref 上。"
            "这不是「这一页没有可引用的内容」，要归档就带上 account_id 再调一次。",
        )
    if document is None:
        return _evidence_view(
            EVIDENCE_UNPARSED,
            f"没有解析出内容，可引用的正文无从谈起：{reason}",
        )
    evidence = document_evidence(
        workspace=workspace,
        account_id=account_id,
        source_url=source_url,
        document=document,
        # 取回时刻**没有**留在 FetchResult 上，所以这里用的是归档时刻，而两者
        # 之间隔着这一次调用（解析 + 一次写入）。写进 docstring 而不是假装它
        # 是取回时刻：这个字段会被用来算「这份证据有多旧」。
        fetched_at=datetime.now(timezone.utc),
        http_status=http_status,
        tier=tier,
    )
    if evidence is None:
        return _evidence_view(
            EVIDENCE_NO_CONTENT,
            "这一页解析出来没有正文（空白或空），所以没有可引用的字面 —— "
            "Evidence.literal_value 不得为空，而放宽那条校验是把「我读过一页」"
            "当成结论的依据。",
        )
    # **写之前先看这个 id 下有没有同一份材料。** 少了这一步，「重复抓取是幂等的」
    # 会在这一层变成一条假话：id 是内容寻址的，所以重抓同一页算出同一个 id，而
    # 那一行带着**上一次**的 ``fetched_at`` —— 同 id 不同 payload，存储按契约
    # 拒绝（它对得对：那正是「id 被手工指定过」的形状）。于是每一次正常的重抓
    # 都会回一句 ``store-refused``，把一个部署故障的样子安在一次成功上。
    if _filed(store, evidence) is not None:
        return _evidence_view(EVIDENCE_ALREADY_FILED, "", evidence_id=evidence.evidence_id)
    try:
        store.put_evidence(evidence)
    except EvidenceError:
        # 并发写入者刚好先到。冲突的含义是「这个 id 下已经有一行」，而重读一次
        # 才知道那一行是不是**这一份材料** —— 是就复用，不是才是真的拒绝。
        if _filed(store, evidence) is not None:
            return _evidence_view(
                EVIDENCE_ALREADY_FILED, "", evidence_id=evidence.evidence_id
            )
        return _evidence_view(
            EVIDENCE_STORE_REFUSED,
            f"证据库拒绝了这次写入：这个 id 下已经有一行**内容不同**的证据"
            f"（{evidence.evidence_id}）。这个 id 是内容寻址的，所以不同内容只"
            "可能意味着它被手工指定过 —— 引用它会把别人的材料当成这一页的。",
        )
    except (MarketOpsError, OSError) as exc:
        # 只接存储层**说过**它会抛的那几类（StoreError / 文件系统）。其余异常照常
        # 往外走：那是一个本层没有分类过的缺陷，把它接住会把它变成一句看起来
        # 正常的「没有证据」。
        return _evidence_view(
            EVIDENCE_STORE_REFUSED,
            f"证据库拒绝了这次写入：{type(exc).__name__}: {exc}。"
            "这一页的字节仍然落在 body_ref 上，而**引用解不开** —— "
            "这是部署问题，不是这一页没有内容。",
        )
    return _evidence_view(EVIDENCE_WRITTEN, "", evidence_id=evidence.evidence_id)


def _filed(store: EvidenceStore, evidence: Evidence) -> Evidence | None:
    """这个 id 下已经有一行**同一份材料**吗；没有则 ``None``。

    只比对三个**身份承载**的字段：来源 URL、字面量、字节散列。不比 ``fetched_at``
    —— 那正是「同一份材料」与「同一行记录」的差别：重抓一遍会把时刻刷新，而材料
    一个字都没变。也比不过 ``context`` / ``page_locator``：它们由解析器给出，
    换一个解析器版本就会变，而那一行证据本身还是同一份。

    读不出来（库损坏 / 打不开）与「没有」都返回 ``None``：这一层能给出的唯一
    处置是「没有可引用的行」，而调用方随后写一次就会拿到那条真正的错误 —— 那
    一条带着 ``StoreError`` 自己的话，比这里编一句准。
    """
    try:
        found = store.get_evidence(evidence.evidence_id)
    except (MarketOpsError, OSError):
        return None
    if found is None:
        return None
    if (found.source_url, found.literal_value, found.content_sha256) != (
        evidence.source_url,
        evidence.literal_value,
        evidence.content_sha256,
    ):
        return None
    return found


def _evidence_view(status: str, reason: str, evidence_id: str = "") -> dict[str, Any]:
    """``evidence`` 块的**唯一**构造点 —— 形状只有一处定义。

    两条不变量在这里成立：``written`` 之外 ``evidence_id`` 恒为空，``written``
    之外 ``reason`` 恒非空。由构造点保证而不是由每个调用方记得填。
    """
    return {"status": status, "evidence_id": evidence_id, "reason": reason}


def _attempt_view(result, settings: Settings) -> dict[str, Any]:
    """一次抓取的投影。§5 要的字段逐项落在这里：尝试/最终 URL、层级、状态、
    时间、内容类型、内容哈希、失败原因。"""
    return {
        "url": result.request.url,
        "tier": result.tier.value,
        "outcome": result.outcome.value,
        "final_url": result.final_url,
        "status_code": result.status_code,
        "content_type": result.content_type,
        "body_ref": _relative_ref(result.body_path, settings),
        "body_sha256": result.body_sha256,
        "body_bytes": result.body_bytes,
        "block_reason": result.block_reason.value if result.block_reason else None,
        "elapsed_ms": result.elapsed_ms,
        "escalated_from": result.escalated_from.value if result.escalated_from else None,
        "reason": result.reason,
        "error": result.error,
    }


def _document_view(document: ParsedDocument | None, reason: str) -> dict[str, Any]:
    """一份**已经解析好的**文档 → 回执块 —— 有界、且**截断可见**。

    收解析结果而不是 ``(body_path, content_type)``：同一个回执里还有第二个消费者
    （落成证据的那一行），而两次解析会让同一次抓取有两个正文 —— 「回执说解析
    成功、证据说没有正文」这种不一致在报告上看不出来，因为两边都做完了。
    """
    if document is None:
        return {"parsed": False, "reason": reason, "links": _links_block(None, reason)}

    text = document.text
    truncated = len(text) > MAX_PREVIEW_CHARS
    return {
        "parsed": True,
        "parser": document.parser,
        "source_kind": document.source_kind.value,
        "content_sha256": document.sha256,
        "media_type": document.media_type,
        "page_count": document.page_count,
        "used_fallback": document.used_fallback,
        "fallback_reason": document.fallback_reason,
        "warnings": list(document.warnings),
        "is_empty": document.is_empty,
        "text_chars": len(text),
        "text_preview": text[:MAX_PREVIEW_CHARS],
        "text_truncated": truncated,
        "links": _links_block(document, ""),
    }


def _parse(router: DocumentRouter, *, body_path, content_type: str):
    """解析一次，返回 ``(文档, 失败理由)`` —— ``None`` 时理由非空。

    **只有这一处调 ``router.parse``**：解析失败是这条链上一次**可分类**的结果
    （SOP L119：它不是 X），不是一次工具崩溃，所以它要走返回值而不是异常。两个
    视图（``document`` 与逐页 ``links``）都从这里拿结果 —— 各写一遍的话，同一个
    解析失败在两条路上会得到两句不同的话。
    """
    if body_path is None:
        return None, "这一次抓取没有落盘字节，没有可解析的内容"
    try:
        return router.parse(body_path, content_type=content_type), ""
    except ParseError as exc:
        return None, f"ParseError: {exc}"


def _links_block(document, reason: str) -> dict[str, Any]:
    """链接块。**形状只有这一处定义** —— 两个工具、三条路（解析成功 / 解析器
    声明不抽 / 压根没解析出来）共用它，所以不会出现「同一个回执里两种链接形状」。

    ``collected`` 为假时 ``reason`` 必须非空：那正是「这一页没有链接」与「没人
    看过这一页的链接」之间的区别，而它们在计数上长得一模一样。
    """
    if document is None:
        return {
            "collected": False,
            "count": 0,
            "items": [],
            "truncated": False,
            "reason": f"没有可解析的内容，链接无从谈起：{reason}",
        }
    if not document.extracts_links:
        return {
            "collected": False,
            "count": 0,
            "items": [],
            "truncated": False,
            "reason": (
                f"{document.parser} 这个解析器不抽取链接，所以这里的空是「没有抽」，"
                "不是「这一页没有链接」。"
            ),
        }
    items = [
        {"url": link.url, "text": link.text} for link in document.links[:MAX_LINKS_RETURNED]
    ]
    return {
        "collected": True,
        "count": len(document.links),
        "items": items,
        "truncated": len(document.links) > MAX_LINKS_RETURNED,
        "reason": "",
    }




# ----------------------------------------------------------------------
# crawl_site
# ----------------------------------------------------------------------
def _crawl_site(
    *,
    settings: Settings,
    crawl: Callable[[Mapping[str, Sequence[str]]], SiteCrawl] | None,
    router: DocumentRouter,
    evidence_store_factory: Callable[[], EvidenceStore],
    seeds: list[str],
    purpose: str,
    pages: int,
    seconds: float,
    mode: str,
    account_id: str,
    source_method: str,
) -> dict[str, Any]:
    """按调用方给的额度，在一个站上取至多 ``pages`` 页。

    七条校验**全部**在任何网络 I/O 之前：一次注定取不到东西的调用，不该先替
    调用方花掉一次 robots 读 —— 那一次读在报告上不留痕迹，而它是真的发生了。
    """
    wanted = _seed_texts(seeds)
    if not purpose.strip():
        raise ToolError("purpose 不能为空：它是这次抓取在报告里的用途，不是可选的备注。")
    try:
        resolved_mode = FetchMode(mode)
    except ValueError:
        allowed = ", ".join(item.value for item in FetchMode)
        raise ToolError(f"mode 必须是 {allowed} 之一，实际拿到 {mode!r}") from None
    if pages < 1 or pages > MAX_PAGES_PER_CALL:
        raise ToolError(
            f"pages 必须是 1 到 {MAX_PAGES_PER_CALL} 之间的整数，实际拿到 {pages!r}。"
            "上限由服务端定 —— pages 是调用方给的数字，而调用方可能是一个模型。"
            "这里**不截断**：截断之后调用方会把这份报告当「计划里都取完了」读，"
            "而真相是「你要的比这里允许的多」。"
        )
    if seconds <= 0 or seconds > MAX_CRAWL_SECONDS:
        raise ToolError(
            f"seconds 必须是大于 0 且不超过 {MAX_CRAWL_SECONDS} 的数，实际拿到 {seconds!r}。"
            "同上：上界由服务端定。墙钟用尽与页数用尽在报告上是两个不同的理由"
            "（再调一次有没有用），所以这个数字要有意义。"
        )
    key = _canonical(account_id, "account_id") if account_id.strip() else ""

    if crawl is None:
        raise ToolError(
            "本部署没有接线多页抓取，所以这次调用不会发生。"
            "这不是一次「抓过了、这个站没有页面」—— 前者要去改部署（接线 "
            "crawl_site），后者才是换种子。两者在报告上必须分得开：一份空报告"
            "读起来正是后者。"
        )

    # 这一步的两半校验（闭集 / 在不在这一轮的计划里）与其余六条一样，**仍在任何网络
    # I/O 之前** —— 一次注定取不到东西的调用不该先替调用方读一次 robots。它排在
    # 「没有接线多页抓取」之后，理由与 ``_search_web`` 那处逐字相同：那时读的是 Scene
    # 声明，而一个装不出 profile 的部署不该把「没接抓取」这句话盖掉。
    bundle = require_bundle(settings)
    step = plan_step(source_method, steps=plan_steps(bundle))
    report = crawl(bundle.profile.page_discovery_hints).crawl(
        wanted,
        purpose=purpose,
        account_id=key,
        pages=pages,
        seconds=seconds,
        mode=resolved_mode,
    )
    return _crawl_view(report, settings, router, evidence_store_factory(), step)


def _seed_texts(seeds: list[str]) -> list[str]:
    """校验种子清单。与 ``_query_texts`` 逐字同形 —— 每一条要么原样发出，要么整次拒绝。

    ``isinstance`` 那一句守着**直接调这个函数**的代码路径（判据里有一条就是这么
    调的）。经服务面的调用在这一层之前就被 schema 挡掉了，而两处都要在：
    schema 是声明的边界，这一句是函数的边界。
    """
    if not isinstance(seeds, list) or not seeds:
        raise ToolError("seeds 必须是一个非空的种子 URL 列表。空清单不是「这个站没有页面」。")
    if len(seeds) > MAX_SEEDS_PER_CALL:
        raise ToolError(
            f"一次调用最多 {MAX_SEEDS_PER_CALL} 个种子，实际 {len(seeds)} 个。"
            "上限由服务端定：每个种子各要一份计划（各读一次 robots 与站点地图），"
            "所以种子的个数就是前沿读几遍这件事的唯一闸。"
        )
    cleaned: list[str] = []
    for item in seeds:
        if not isinstance(item, str) or not item.strip():
            raise ToolError(f"种子必须是非空字符串，实际拿到 {item!r}")
        cleaned.append(item.strip())
    return cleaned


def _crawl_view(
    report: SiteCrawlReport,
    settings: Settings,
    router: DocumentRouter,
    store: EvidenceStore,
    source_method: str | None,
) -> dict[str, Any]:
    """一次多页抓取的投影。

    三个「没取完」的理由（``StopReason``）**原样**出现在 ``stopped_reason`` 上：
    合并成一句的代价是调用方不知道下一步该做什么（再调一次有用，还是该调大预算）。

    逐页回执上的链接是**在这里**加上去的（``SiteCrawl`` 不解析文档，它的文件头
    写着这句话）：每一页的镜像字节交给 ``DocumentRouter``，取回 ``ParsedDocument``
    里的链接。代价是每一页多一次解析 —— §5 要求每一页记录它的链接，而链接只有
    解析才拿得到，所以这是一次**买了东西**的开销，不是顺手多做的一步。

    同一次解析也喂给证据那一格：**每一页各落一行证据**。抓一遍站却只留下一行
    「我抓过这个站」会让被引用的是那次调用，而不是某一页的字节 —— 而报告上
    两者看起来一样。逐页那一行的 id 在各自的 ``evidence`` 块里。
    """
    planned_urls, planned_truncated = _bounded(report.planned_urls)
    remaining_urls, remaining_truncated = _bounded(report.remaining)
    return {
        "seeds": list(report.seeds),
        "purpose": report.purpose,
        "source_method": source_method,
        "account_id": report.account_id,
        "pages_requested": report.pages_requested,
        "seconds_requested": report.seconds_requested,
        "stopped_reason": report.stopped_reason.value,
        "elapsed_seconds": report.elapsed_seconds,
        "planned_count": report.planned,
        "planned_urls": planned_urls,
        "remaining_count": len(report.remaining),
        "remaining_urls": remaining_urls,
        "urls_truncated": planned_truncated or remaining_truncated,
        "receipt_count": len(report.receipts),
        "receipts": [
            _page_view(item, settings, router, store, report.account_id)
            for item in report.receipts
        ],
        "plans": [_plan_view(plan) for plan in report.plans],
        "gaps": [dict(gap) for gap in report.gaps],
        "note": (
            "stopped_reason 是三件事之一，别把它们读成一件：completed 是取完了；"
            "page_ceiling 是页数用尽，**把 remaining_urls 当 seeds 再调一次有用**；"
            "wall_clock 是墙钟用尽，再调一次只会重复同一次超时（该调大 seconds）；"
            "no_targets 是种子没产出可取的 URL，原因在 plans 各自的 gaps 里。"
            "receipts 只含**真的取过**的 URL，remaining_urls 是计划里没取的那些，"
            "两者相加是 planned_count —— remaining 是续抓唯一的输入。"
            "urls_truncated 为真时清单被截到上限，但 planned_count / remaining_count"
            "仍然是**准确总数**（§11：不静默截断）。"
            "每一页的 evidence 块是**那一页**落成的证据行：它有 id，可以用 "
            "get_evidence 按 id 解回材料 —— document 里那段预览不行。"
            "取过却没落成证据的页在 receipts 里，status 与 reason 说得出为什么。"
            + _LINKS_ARE_VERBATIM
            + "source_method 是这次抓取**服务计划里的哪一步**，取值是服务端的来源方法"
            "闭集（`domain/sources.py`），与 `EffectiveResearchPlan.source_methods` "
            "同一张表 —— 它是「这条 provider 调用因哪一步被选择」那条反查的锚点。"
            "它为 null 表示本次调用**没有声称**服务任何 plan 步骤，不是「未知」也"
            "不是默认值。它描述的是**这一次调用**，不是逐页的：同一次抓取里的每一页"
            "服务的是同一步。"
            + "**持久的操作 id 本构建不交付**：§3 要求「超出单次工具调用时长时返回"
            "持久的操作 id」，而那需要一个能承载状态的父对象（执行器 assignment），"
            "是 Phase G 的事。本轮交付的是可续抓（稳定的顺序 + remaining），不是"
            "一个可以回头查的 id —— 这两件事长得像，但不是一回事。"
        ),
    }


def _page_view(
    receipt: PageReceipt,
    settings: Settings,
    router: DocumentRouter,
    store: EvidenceStore,
    account_id: str,
) -> dict[str, Any]:
    """一页一条回执。两组字段来自两个地方，**故意不合并**（见 ``PageReceipt``）：
    ``tier`` / ``why`` 回答「它凭什么在这一次里」，其余回答「它取回来了什么」。

    链接是第三组：它既不在计划里（那是 ``tier`` / ``why``），也不在抓取结果里
    （``PageReceipt`` 只记字节落在哪）—— 它要**解析**才知道。§5 要求每一页记录
    它的链接，所以这一条路必须走到解析器，而 ``collected`` 让「这一页没有链接」
    与「这一页没被解析 / 解析器不抽链接」在回执上分得开。

    解析**一次**，链接与证据共用（见 ``_document_view`` 里的同一条理由）。
    """
    document, reason = _parse(
        router, body_path=receipt.body_path, content_type=receipt.content_type
    )
    return {
        "url": receipt.url,
        "mode": receipt.mode.value,
        "outcome": receipt.outcome.value,
        "fetch_tier": receipt.fetch_tier.value,
        "tier": receipt.tier,
        "why": receipt.why,
        "final_url": receipt.final_url,
        "status_code": receipt.status_code,
        "content_type": receipt.content_type,
        "body_ref": _relative_ref(receipt.body_path, settings),
        "body_sha256": receipt.body_sha256,
        "body_bytes": receipt.body_bytes,
        "elapsed_ms": receipt.elapsed_ms,
        "block_reason": receipt.block_reason.value if receipt.block_reason else None,
        "escalated_from": (
            receipt.escalated_from.value if receipt.escalated_from else None
        ),
        "browser_seconds": receipt.browser_seconds,
        "reason": receipt.reason,
        "error": receipt.error,
        "links": _links_block(document, reason),
        "evidence": _ingest(
            store=store,
            workspace=settings.profile,
            account_id=account_id,
            source_url=receipt.final_url or receipt.url,
            document=document,
            reason=reason,
            http_status=receipt.status_code,
            tier=receipt.fetch_tier.value,
        ),
    }


def _plan_view(plan) -> dict[str, Any]:
    """一份计划，**不含 urls**。

    ``CrawlPlan.as_dict()`` 里带着 ``urls``，而那份清单与顶层 ``planned_urls``
    是同一份东西（都来自 ``_merge``）。同一份清单在一个回执里出现两次，调用方
    就得分出该信哪一份 —— 而两份总有一天会不一致。这里只留审计字段，URL 清单
    只有一份。
    """
    return {
        "seed": plan.seed,
        "seed_url_count": len(plan.targets),
        "sitemap_documents": plan.sitemap_documents,
        "pooled": plan.pooled,
        "tier_hits": dict(plan.tier_hits),
        "skipped": dict(plan.skipped),
        "truncated": plan.truncated,
        "gaps": [dict(gap) for gap in plan.gaps],
    }


def _bounded(items: Sequence[str]) -> tuple[list[str], bool]:
    """截到 ``MAX_URLS_RETURNED``，并**说出**截了没有。

    返回的布尔是 ``urls_truncated`` 的唯一来源：由这一处算，而不是调用方各自
    ``len() > MAX`` 判一次 —— 判两次就会有一天两份答案不一致。
    """
    if len(items) > MAX_URLS_RETURNED:
        return list(items[:MAX_URLS_RETURNED]), True
    return list(items), False


# ----------------------------------------------------------------------
def _canonical(value: str, field: str) -> str:
    """校验一个 id 并返回它。判据不在这里实现（``domain/util.check_canonical_id``）——
    它与写入侧用的是同一个函数，读取层只能**更严**不能更松。"""
    try:
        return check_canonical_id(value or "", field)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc





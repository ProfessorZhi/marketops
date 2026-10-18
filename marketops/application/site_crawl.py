"""有界多页抓取：前沿说去哪，本模块说**取多少、什么时候停、为什么停**。

``crawl_site`` 工具（``docs/MCP_SERVICE.md`` §3）的编排层。它把两件事接起来：

1. ``sitemap_frontier.SitemapFrontier.plan()`` —— 去取哪些 URL、按什么顺序；
2. ``acquisition.DocumentAcquisition.fetch_document()`` —— §10 那条**唯一**的取字节
   的路。

本模块自己不取字节、不解析 HTML、不判定站点规则。它只做三件事：把多个种子的计划
合并成一个队列、在每一次取字节之前过两道闸、把结果整理成一份能续抓的报告。

## 两个上限都是**服务端**给的

``pages`` 与 ``seconds`` 是本次调用的额度，而它们的上界住在服务面的处理器里
（``adapters/mcp_server/research.py``）。这与 §8 对预算的要求是同一条：调用方
（可能是一个模型）能请求「至多多少」，但**不能**决定那个上界。

## 三种停下来要分成三句话

``StopReason`` 是一个闭集，而且这三条对应 ``CrawlPlan`` 里那三种不同的缺失：

* ``COMPLETED`` —— 计划里的 URL 都取过了。
* ``PAGE_CEILING`` —— 达到页数上限。再调用一次（把 ``remaining`` 当种子）**有用**。
* ``WALL_CLOCK`` —— 墙钟用尽。再调用一次只会重复同一次超时；该做的是调大预算。
* ``NO_TARGETS`` —— 没有任何种子产出可取的 URL。原因在 ``plans`` 的 ``gaps`` 里，
  本模块**转述**它而不是自己编一句（「站点说了不要」与「站点没有页面」不是同一件事）。

把它们合成一句「没取完」的代价是调用方不知道下一步该做什么 —— 而报告存在的全部
意义就是回答这个。

## 链接住在服务面，不在这里；持久操作 id 谁都还没有

* **链接。** §3 要求「逐页回执与**链接**」。链接来自解析，而本模块**不解析文档**
  （见下），所以它住在**服务面的回执**上：那一层把每一页的镜像字节交给
  ``DocumentRouter``，取回 ``ParsedDocument.links``。这里要记一笔，因为链接曾经是
  本模块的一条 ``gaps`` —— 它说的是「本报告没有回答这一页有没有链接」，而链接落地
  之后那句话会变成**假话**。一条说谎的 gap 比缺一条 gap 坏得多：读报告的人对
  ``gaps`` 的信任是按条付的，而他没有办法分辨哪一条已经过期。
* **持久的操作 id。** §3 还要求「超出单次工具调用时长时返回持久的操作 id」。这需要
  一个能承载状态的父对象，而 §3 自己也写了这件事的归属：``crawl_site`` 是「从属于
  一个 Quay assignment 的抓取操作……它的状态经父 job/assignment 可发现」——那是
  Phase G。本轮交付的是**可续抓**：稳定的顺序 + ``remaining``。这一条**留在
  note 里**而不是 ``gaps`` 里：它是工具契约上的欠账，不是这一次抓取的性质。

**不跟随链接。** 每页回执里的顺序是「种子 → 站点地图 → profile 的优先级」的函数。
跟随页面上的链接会让顺序变成「抓取历史」的函数 —— 两次运行的顺序不再可比，而顺序
是报告的一部分。

## 一条要写下来的代价：种子之间不共享前沿

每个种子各要一份计划，而计划会读 robots 与站点地图 —— 同一台主机被 N 条种子点到，
就有 N 次 robots 读与 N 次站点地图走。**页面**那一侧是去重的（``_merge``），真正贵
的那一半因此没有重复；而前沿这一侧不去重，因为缓存要有失效策略，而这一层现在没有
时间概念。把它当作已知代价说出来，而不是让它在一次「首页 + 三个具体页面」的调用里
悄悄发生。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..domain.enums import BlockReason, FetchOutcome, FetchTier
from ..ports.acquisition import DocumentFetch, FetchMode, FetchRequest
from .acquisition import DocumentAcquisition
from .sitemap_frontier import MAX_POOLED_URLS, CrawlPlan, CrawlTarget, SitemapFrontier

__all__ = ["PageReceipt", "SiteCrawl", "SiteCrawlReport", "StopReason"]


class StopReason(str, Enum):
    """这一次抓取为什么停下来。闭集 —— 见模块 docstring 为什么不能合并。"""

    COMPLETED = "completed"
    PAGE_CEILING = "page_ceiling"
    WALL_CLOCK = "wall_clock"
    NO_TARGETS = "no_targets"


@dataclass(frozen=True)
class PageReceipt:
    """一页一条回执。

    两组字段来自两个不同的地方，**故意不合并**：``tier`` / ``why`` 回答「这条 URL
    凭什么在这一次里」（来自计划），其余回答「它取回来了什么」（来自抓取）。

    前四个字段**没有默认值**，与 ``FetchResult`` 的 ``outcome`` / ``tier`` 一样。
    给它们默认值就是允许一条「没有分类的回执」存在，而那样一条回执读起来与真的
    一模一样 —— 报告里最贵的那种错误是「这一页是空的」与「这一页没取」同形。
    """

    url: str
    #: 实际用的模式（``auto`` / ``http`` / ``browser``）。
    mode: FetchMode
    outcome: FetchOutcome
    fetch_tier: FetchTier
    #: 命中的优先级键；种子那一行是空串（ ``CrawlTarget`` 里写了理由）。
    tier: str = ""
    #: 为什么它在计划里：命中的 glob，或 ``seed``。
    why: str = ""
    final_url: str = ""
    status_code: int | None = None
    content_type: str = ""
    #: 落盘的镜像。``None`` 表示这次没取到可落盘的字节 —— 与「落到了别处」不同。
    body_path: Path | None = None
    body_sha256: str = ""
    body_bytes: int = 0
    elapsed_ms: int = 0
    block_reason: BlockReason | None = None
    #: 升级来的那一次抓取记着它是从哪种结果升级的；没升级是 ``None``。
    escalated_from: FetchOutcome | None = None
    browser_seconds: float = 0.0
    reason: str = ""
    error: str = ""


@dataclass(frozen=True)
class SiteCrawlReport:
    """一次有界抓取的全部结果。

    ``planned_urls`` 是**合并去重之后**的完整候选（可能多于本次取的），``urls`` 是
    真的取过的那些，``remaining`` 是两者的差 —— 续抓要的就是最后一个。

    计数只留 ``planned``：它答的是「这个站有多少条候选」，而 ``planned_urls`` 是
    元组，调用方自己 ``len()`` 得出来的东西不值得多一个入口。
    """

    seeds: tuple[str, ...] = ()
    purpose: str = ""
    account_id: str = ""
    pages_requested: int = 0
    seconds_requested: float = 0.0
    #: 每个种子一份计划，**含它自己的 gaps** —— 报告的可解释性来自这里。
    plans: tuple[CrawlPlan, ...] = ()
    receipts: tuple[PageReceipt, ...] = ()
    planned_urls: tuple[str, ...] = ()
    remaining: tuple[str, ...] = ()
    stopped_reason: StopReason = StopReason.NO_TARGETS
    elapsed_seconds: float = 0.0
    gaps: tuple[Mapping[str, Any], ...] = ()

    @property
    def urls(self) -> tuple[str, ...]:
        return tuple(item.url for item in self.receipts)

    @property
    def planned(self) -> int:
        return len(self.planned_urls)


class SiteCrawl:
    """把「去哪取」与「怎么取」接起来的有界编排。

    Args:
        frontier: 站点前沿。它自己读 robots.txt 与站点地图（经过注入的抓取器），
            所以本模块拿到的已经是一份**过了安全判据与站点规则**的候选清单。
        acquisition: §10 的统一抓取入口。**逐页的字节都经由它**，包括浏览器升级
            的判定 —— 在这里再写一条「什么时候该开浏览器」就是第二份真实实现。
        clock: 读墙钟。注入是为了让「墙钟用尽」这条判据在**零等待**下跑得出来；
            一个要真的 sleep 三秒的判据，最后会变成一条没人跑的判据。
    """

    def __init__(
        self,
        *,
        frontier: SitemapFrontier,
        acquisition: DocumentAcquisition,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._frontier = frontier
        self._acquisition = acquisition
        self._clock = clock

    # ------------------------------------------------------------------
    def crawl(
        self,
        seeds: Sequence[str],
        *,
        purpose: str,
        account_id: str = "",
        pages: int,
        seconds: float,
        mode: FetchMode = FetchMode.AUTO,
    ) -> SiteCrawlReport:
        """按 ``seeds`` 抓至多 ``pages`` 页、至多花 ``seconds`` 秒。

        墙钟从**这次调用开始**算，前沿读 robots 与站点地图花的时间也在里面：
        它们同样是网络 I/O，排除掉会让一次带五份站点地图的调用用掉远超预算的
        时间，而报告上的 ``elapsed_seconds`` 说它只花了页面那部分。预算是**软界**
        —— 一页要花多久取之前不知道，所以最后一次抓取可以越界，``elapsed_seconds``
        会把它说出来。

        Raises:
            ValueError: ``pages`` 小于 1。上限由服务端给，所以到不了这里；但如果
                到了，静默取一页就是把「这次调用没有页数额度」写成「这次调用取到了
                一页」，而调用方据此后决定要不要续抓 —— 它已经付过一次了。
        """
        if pages < 1:
            raise ValueError(f"pages 必须至少为 1，实际拿到 {pages!r}")

        wanted = tuple(seed for seed in (item.strip() for item in seeds) if seed)
        started = self._clock()

        # 前沿拿到的上限是**池子**的上限，不是这次要取几页。两个理由：「计划是候选，
        # 不是授权」（``SitemapFrontier`` 的文件头），所以候选本来就该比这一次取得
        # 多；而报告要说得出**没取的那些是什么**（``remaining`` 是续抓唯一的输入），
        # 把 ``pages`` 传下去会让 ``remaining`` 永远是空的。取多少由下面那道闸管。
        plans = tuple(
            self._frontier.plan(seed, limit=MAX_POOLED_URLS, account_id=account_id)
            for seed in wanted
        )
        queue, planned_urls = _merge(plans)

        gaps: list[Mapping[str, Any]] = []
        for plan in plans:
            # 转述，不重写：每一条计划里的理由都带着**它是哪个种子**的，措辞原样。
            gaps.extend({**gap, "seed": plan.seed} for gap in plan.gaps)

        if not wanted:
            gaps.append(
                {
                    "gap": "no_seeds",
                    "detail": "这次调用没有给出任何种子，因此没有去取任何 URL。"
                    "空清单不是「这个站没有页面」。",
                }
            )

        receipts: list[PageReceipt] = []
        halted = False
        for target in queue[:pages]:
            if self._clock() - started >= seconds:
                halted = True
                break
            receipts.append(
                _receipt(
                    target,
                    self._acquisition.fetch_document(
                        FetchRequest(url=target.url, scope=account_id, purpose=purpose),
                        mode=mode,
                    ),
                )
            )

        # 停下来的那一句话。循环跑完（没被墙钟打断）时还要再看一眼：队列本身被
        # 页数上限截断过没有 —— 那也是一种「没取完」，只是原因是另一个。
        if halted:
            stopped = StopReason.WALL_CLOCK
        elif len(queue) > pages:
            stopped = StopReason.PAGE_CEILING
        elif queue:
            stopped = StopReason.COMPLETED
        else:
            stopped = StopReason.NO_TARGETS
        if stopped is StopReason.NO_TARGETS:
            gaps.append(
                {
                    "gap": "no_targets",
                    "detail": "种子给出了零条可取 URL，因此这一次没有取任何页面。"
                    "原因在上面转述的、各计划自己的 gaps 里 —— 本模块不替它们"
                    "重写一句话，因为「站点说了不要」与「站点没有页面」不是同一件事。",
                }
            )

        elapsed = self._clock() - started
        return SiteCrawlReport(
            seeds=wanted,
            purpose=purpose,
            account_id=account_id,
            pages_requested=pages,
            seconds_requested=seconds,
            plans=plans,
            receipts=tuple(receipts),
            planned_urls=planned_urls,
            remaining=planned_urls[len(receipts) :],
            stopped_reason=stopped,
            elapsed_seconds=elapsed,
            gaps=tuple(gaps),
        )


# ----------------------------------------------------------------------
def _receipt(target: CrawlTarget, fetched: DocumentFetch) -> PageReceipt:
    """把「计划说的」与「抓回来的」拼成一条回执。

    取 ``fetched.final`` 而不是 ``attempts[-1]``：``DocumentFetch`` 的注释写了为什么
    最终结果就是 ``final``（升级之后结论属于那个 tier，混着取会让 ``tier`` 与
    ``body_path`` 指向两次不同的抓取）。``attempts`` 里那次 HTTP 的失败没有丢，
    它在 ``final.escalated_from`` 上 —— 而这一片不重述它，因为统计「这个站该不该
    进浏览器」的地方是抓取层的回执，不是页面回执。
    """
    result = fetched.final
    return PageReceipt(
        url=target.url,
        mode=fetched.mode,
        outcome=result.outcome,
        tier=target.tier,
        why=target.why,
        fetch_tier=result.tier,
        final_url=result.final_url,
        status_code=result.status_code,
        content_type=result.content_type,
        body_path=result.body_path,
        body_sha256=result.body_sha256,
        body_bytes=result.body_bytes,
        elapsed_ms=result.elapsed_ms,
        block_reason=result.block_reason,
        escalated_from=result.escalated_from,
        browser_seconds=fetched.browser_seconds,
        # ``reason`` 取 ``decision`` 而不是 ``result.reason``：前者的取值来自
        # ``escalation.decide`` 或两条 mode 分支，读起来是「这一次为什么是这个
        # 结果」；后者在多数路径上是空的。页面回执要给下一个人一句可直接读的话。
        reason=fetched.decision or result.reason,
        error=result.error,
    )


def _merge(plans: Sequence[CrawlPlan]) -> tuple[list[CrawlTarget], tuple[str, ...]]:
    """把多份计划按种子顺序拼起来，**同一个 URL 只留第一次出现的那一条**。

    去重按 URL 原始字符串，与前沿的取舍一致（``sitemap_frontier`` 的文件头写了
    为什么不做规范化：规范化规则一旦写错，它会把两个不同的页面合成一个，而报告上
    什么都不会显示）。第二次出现的那条并不只是被丢掉 —— 它本来就在同一份候选池里，
    而池子的计数在计划的 ``pooled`` 上。
    """
    queue: list[CrawlTarget] = []
    seen: set[str] = set()
    for plan in plans:
        for target in plan.targets:
            if target.url in seen:
                continue
            seen.add(target.url)
            queue.append(target)
    return queue, tuple(target.url for target in queue)

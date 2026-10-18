"""站点抓取的编排：两个上限、逐页回执、以及**为什么停下来**。

``docs/MCP_SERVICE.md`` §3 给 ``crawl_site`` 的必需行为是「有界多页工作、逐页回执
与链接；超出工具调用时长时返回持久的操作 id」。这一片交付的是**有界**与**逐页
回执**，另外两件事各自有理由不在这里（见 ``application/site_crawl.py`` 的文件头：
链接要经过一个端口变更，持久操作 id 要等 Phase G 的 job/assignment）。

## 全部零网络

替身是 ``tests/fixtures/http_fakes.py`` 的 ``FakeHttp``（分类交给真的
``assess_http_response``），时钟是同一处的 ``FakeClock``（``SlowHttp`` 是让它在
每次取字节时往前走一格的那个变体）。**墙钟判据必须能在零等待下跑** —— 一个要真的
sleep 三秒的判据，最后会变成一条没人跑的判据。

## 这一组判据在问什么

不是「代码照着我写的样子跑了吗」，而是「那两个上限真的拦得住吗、停下来的时候报告
说得出是哪一条拦的吗」。所以每条上限都配一条**正对照**（没到上限时不许停），
而两个 `StopReason` 之间有一条**成对判据**：它们读起来必须不一样。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

import pytest

from marketops.application.acquisition import DocumentAcquisition
from marketops.application.site_crawl import SiteCrawl, StopReason
from marketops.application.sitemap_frontier import SitemapFrontier
from marketops.domain.enums import FetchOutcome, FetchTier
from marketops.ports.acquisition import FetchMode

from .fixtures.http_fakes import FakeClock, FakeHttp, Reply, SlowHttp, site_replies
from .fixtures.site_content import sitemap_xml

SITE = "https://example.com"
ROBOTS = f"{SITE}/robots.txt"
SITEMAP = f"{SITE}/sitemap.xml"

HINTS: Mapping[str, tuple[str, ...]] = {"high": ("*/contact*",), "low": ("*/",)}


def happy_site(*extra_pages: str) -> dict[str, Reply]:
    """一个站：robots 放行、站点地图给出四页，**加上种子本身**。

    种子的回执由 ``site_replies`` 摆（``SITE`` 与 ``SITE + "/"`` 是两条不同的
    URL，前沿不做规范化，理由写在 ``sitemap_frontier`` 的文件头，而站点地图列
    的是后者）。

    四页里三页落在提示上：两页命中 ``*/contact*``（high），``/`` 命中 ``*/``
    （low）；``/news/1`` 匹配不上任何一级（``*/`` 只匹配**以斜杠结尾**的 URL），
    所以它进不了计划，只进 ``skipped``。
    """
    return site_replies(
        SITE,
        (
            f"{SITE}/",
            f"{SITE}/contact-us",
            f"{SITE}/contact-form",
            f"{SITE}/news/1",
        )
        + extra_pages,
    )


def crawl_of(
    tmp_path: Path,
    replies: Mapping[str, Reply],
    *,
    seeds: tuple[str, ...] = (SITE,),
    pages: int = 10,
    seconds: float = 60.0,
    mode: str = "auto",
    clock: FakeClock | None = None,
    per_page: float = 0.0,
    free: tuple[str, ...] = (ROBOTS, SITEMAP),
):
    """跑一次编排，返回 ``(报告, 抓取器)``。抓取器要留着 —— 它记着被问过什么。"""
    tick = clock if clock is not None else FakeClock()
    http = (
        SlowHttp(tmp_path / "mirrors", replies, clock=tick, per_page=per_page, free=free)
        if per_page
        else FakeHttp(tmp_path / "mirrors", replies)
    )
    frontier = SitemapFrontier(http=http, hints=HINTS)
    crawl = SiteCrawl(
        frontier=frontier,
        acquisition=DocumentAcquisition(http=http),
        clock=tick,
    )
    report = crawl.crawl(
        list(seeds),
        purpose="site",
        account_id="acc-1",
        pages=pages,
        seconds=seconds,
        mode=FetchMode(mode),
    )
    return report, http


# ======================================================================
# 计划说什么，回执就说什么
# ======================================================================
def test_the_pages_come_back_in_the_order_the_plan_gave(tmp_path: Path) -> None:
    """正控：正常路径下顺序是「种子第一，然后按声明的优先级」。

    没有这一条，下面每一条「少了什么」都可能是被一个整个坏掉的编排满足的。

    顺序里有两件事要看得见。一是 ``{SITE}/`` 命中 ``low``：``*/`` 只匹配**以斜杠
    结尾**的 URL，所以 ``/news/1`` 落进 ``unmatched`` 而根本不进计划 —— 不匹配的
    URL 被**计数**，不是被悄悄丢掉。二是 ``{SITE}`` 与 ``{SITE}/`` 是两条不同的
    URL，站点地图又几乎总会列上自己，于是同一个页面取了两次。报告如实记两次：
    「这两条是同一个页面」是一个判断，而这一层不做判断。
    """
    report, _ = crawl_of(tmp_path, happy_site())

    assert report.urls[0] == SITE, "种子由调用方指定，排在第一位"
    assert report.urls[1:3] == (f"{SITE}/contact-us", f"{SITE}/contact-form"), "high 级在前"
    assert report.urls[3] == f"{SITE}/", "命中 low 的那一条排在 high 之后"
    assert f"{SITE}/news/1" not in report.urls, "没有一级提示匹配它，所以不进计划"
    assert report.plans[0].skipped["unmatched"] == 1, "但它要被数出来"
    assert report.stopped_reason is StopReason.COMPLETED


def test_each_receipt_says_what_came_back_from_that_url(tmp_path: Path) -> None:
    """一页一条回执，字段来自**那一次抓取**。"""
    report, _ = crawl_of(tmp_path, happy_site())

    first = report.receipts[0]
    assert first.url == SITE
    assert first.outcome is FetchOutcome.OK
    assert first.fetch_tier is FetchTier.HTTP
    assert first.status_code == 200
    assert first.body_path is not None and first.body_path.exists(), "镜像要真的落盘"
    assert first.body_sha256 and first.body_bytes > 0
    assert first.mode is FetchMode.AUTO, "模式是一个枚举成员，不是一个恰好相等的字符串"


def test_the_receipt_carries_what_the_plan_said_about_that_page(tmp_path: Path) -> None:
    """``tier`` 与 ``why`` 来自计划，不是回执自己编的。

    它们回答的是「这条 URL 凭什么在这一次里」，而抓取结果回答的是「它取回来了
    什么」。两件事分开记，报告才能在「提示没命中」与「页面取不到」之间分辨。
    """
    report, _ = crawl_of(tmp_path, happy_site())

    seed = report.receipts[0]
    assert (seed.tier, seed.why) == ("", "seed")
    contact = report.receipts[1]
    assert contact.tier == "high" and contact.why.startswith("*/contact")


def test_two_seeds_are_crawled_in_the_order_they_were_given(tmp_path: Path) -> None:
    """两个种子 → 先第一个站的全部，再第二个站的。"""
    other = "https://second.example.org"
    replies = happy_site()
    replies.update(
        {
            f"{other}/robots.txt": Reply(f"Sitemap: {other}/sitemap.xml\nUser-agent: *\n"),
            f"{other}/sitemap.xml": Reply(
                sitemap_xml(f"{other}/", f"{other}/contact"), content_type="application/xml"
            ),
            f"{other}/": Reply("<html>x</html>", content_type="text/html"),
            f"{other}/contact": Reply("<html>y</html>", content_type="text/html"),
        }
    )
    report, _ = crawl_of(tmp_path, replies, seeds=(SITE, other))

    assert report.seeds == (SITE, other)
    assert report.urls[4:] == (other, f"{other}/contact", f"{other}/"), (
        "第二个站的页面排在第一个站的全部之后，站内顺序照旧（种子、high、low）"
    )


def test_a_url_planned_by_two_seeds_is_fetched_once(tmp_path: Path) -> None:
    """两条种子落在同一份站点地图上 —— 重叠的部分只取一次，但两条种子都在计划里。

    调用方给的常常是「首页」与「某个具体页面」这种一对 URL，而站点地图几乎总会
    把自己列进去：**重叠是常态，不是边界**。取两次是浪费，也是把同一次抓取记成
    两次；而计划里少一次则会让「这个站有多少条候选」算错。

    前沿**不**跨种子缓存：robots 与站点地图会被读两次，``asked`` 里数得出来。
    那一层不做缓存，因为缓存要有失效策略而它现在没有时间概念 —— 代价是同一主机
    被 N 条种子点到就有 N 次站点地图读。真正贵的是取字节那一侧，那个由这条守住。
    """
    report, http = crawl_of(tmp_path, happy_site(), seeds=(SITE, f"{SITE}/contact-us"))

    assert report.planned_urls == (
        SITE,
        f"{SITE}/contact-us",
        f"{SITE}/contact-form",
        f"{SITE}/",
    ), "第二条计划与第一条重合三条，合起来仍是四条，顺序照旧"
    assert http.asked.count(f"{SITE}/contact-form") == 1, "重叠的那一页只取一次"
    assert http.asked.count(SITEMAP) == 2, "前沿自己不去重 —— 代价要说出来"


# ======================================================================
# 两个上限
# ======================================================================
def test_the_page_ceiling_stops_the_crawl_and_names_itself(tmp_path: Path) -> None:
    report, _ = crawl_of(tmp_path, happy_site(), pages=2)

    assert len(report.receipts) == 2
    assert report.stopped_reason is StopReason.PAGE_CEILING
    assert report.pages_requested == 2


def test_what_the_ceiling_left_out_comes_back_as_remaining(tmp_path: Path) -> None:
    """没取的 URL 要能被续抓 —— 而续抓要的是**URL**，不是「还有 3 条」。

    ``remaining`` 是 ``urls`` 的**补集**，所以这条判据钉的是那个分划：两半拼起来
    正好是候选全体，一条不多一条不少。

    这一支单独看不出 ``remaining`` 是由「取过几条」还是由 ``pages`` 算的 ——
    达到页数上限就意味着**取满了** ``pages`` 条，两个算法在这里给出同一个答案。
    把它们分开的是 ``test_the_wall_clock_stops_the_crawl_and_names_itself``：
    那里在取满之前就停了。
    """
    report, _ = crawl_of(tmp_path, happy_site(), pages=2)

    assert report.remaining == report.planned_urls[2:]
    assert report.remaining, "上限拦下东西时 remaining 不能是空的"
    assert report.urls + report.remaining == report.planned_urls, "取过的与剩下的分划候选全体"


def test_the_wall_clock_stops_the_crawl_and_names_itself(tmp_path: Path) -> None:
    """每页花 10 秒、预算 25 秒 → 取三页，第四页之前停。

    预算是**软界**：一页要花多久是取之前不知道的，所以最后一次抓取可以越界
    （这里 30 秒 > 25 秒）。报告把它说在 ``elapsed_seconds`` 里，而不是假装
    没发生 —— 越界是这条设计的正常输出，不是失败。
    """
    clock = FakeClock()
    report, _ = crawl_of(
        tmp_path, happy_site(), seconds=25.0, clock=clock, per_page=10.0
    )

    assert len(report.receipts) == 3, f"实际取了 {len(report.receipts)} 页"
    assert report.stopped_reason is StopReason.WALL_CLOCK
    assert report.remaining == report.planned_urls[3:]
    assert report.elapsed_seconds > report.seconds_requested, "越界这件事要看得见"


def test_the_budget_covers_the_frontiers_own_reads(tmp_path: Path) -> None:
    """**成对判据**：墙钟从调用开始算，前沿读 robots 与站点地图也算在里面。

    同一份站点、同一个 ``per_page`` 与 ``seconds``，只有「前沿那两次读算不算」
    不同 —— 差的是两页。这一条钉的是 ``started`` 的位置：把它挪到计划之后看起来
    无害（「页面循环的预算当然只管页面」），代价是一次带五份站点地图的调用可以用
    掉远超预算的时间，而报告上的 ``elapsed_seconds`` 会说它只花了页面那部分。
    """
    charged, _ = crawl_of(
        tmp_path / "charged", happy_site(), seconds=25.0, per_page=10.0, free=()
    )
    free, _ = crawl_of(tmp_path / "free", happy_site(), seconds=25.0, per_page=10.0)

    assert free.stopped_reason is charged.stopped_reason is StopReason.WALL_CLOCK
    assert len(free.receipts) == 3, "前沿免费时，三次页面抓取刚好走得完 25 秒"
    assert len(charged.receipts) == 1, "前沿收费时，两次读先花掉 20 秒，只剩一页"


def test_the_clock_standing_still_does_not_stop_anything(tmp_path: Path) -> None:
    """**正对照**：时钟不走时，墙钟那一支一次都不许触发。

    没有这一条，「墙钟拦得住」与「编排每次都在第一页之前停」是同一条绿。
    """
    clock = FakeClock()
    report, _ = crawl_of(tmp_path, happy_site(), seconds=25.0, clock=clock, per_page=0.0)

    assert report.stopped_reason is StopReason.COMPLETED
    assert len(report.receipts) == len(report.planned_urls)


def test_a_budget_that_is_already_gone_fetches_nothing(tmp_path: Path) -> None:
    """预算为 0 时**一次都不取** —— 而不是「先取一页再说」。

    先取一页看起来更「有用」，但那把「这次调用没有预算」写成了「这次调用取到了一
    页」；调用方据此后决定要不要续抓，而它已经付过一次了。
    """
    report, http = crawl_of(tmp_path, happy_site(), seconds=0.0)

    assert report.receipts == ()
    assert report.stopped_reason is StopReason.WALL_CLOCK
    assert not [url for url in http.asked if url not in (ROBOTS, SITEMAP)], "页面一个都没取"


def test_the_two_reasons_a_crawl_can_stop_early_read_differently(tmp_path: Path) -> None:
    """**成对判据**：页数上限与墙钟用尽是两句话。

    合并成一句「没取完」的代价是：调用方不知道续抓**能不能帮上忙** —— 页数上限
    时再调用一次（把剩余当种子）就有用，墙钟用尽时该做的是把预算调大。
    """
    by_pages, _ = crawl_of(tmp_path, happy_site(), pages=2)
    by_clock, _ = crawl_of(tmp_path, happy_site(), seconds=10.0, per_page=10.0)

    assert by_pages.stopped_reason is StopReason.PAGE_CEILING
    assert by_clock.stopped_reason is StopReason.WALL_CLOCK
    assert by_pages.receipts and by_clock.receipts, "两条都要真的取过页，否则不是在比处置"
    assert by_pages.pages_requested == 2 and by_clock.pages_requested == 10


def test_two_seeds_share_one_page_budget(tmp_path: Path) -> None:
    """上限是**这一次调用**的，不是每个种子的。"""
    other = "https://second.example.org"
    replies = happy_site()
    replies.update(
        {
            f"{other}/robots.txt": Reply(f"Sitemap: {other}/sitemap.xml\nUser-agent: *\n"),
            f"{other}/sitemap.xml": Reply(
                sitemap_xml(f"{other}/", f"{other}/contact"), content_type="application/xml"
            ),
            f"{other}/": Reply("<html>x</html>", content_type="text/html"),
            f"{other}/contact": Reply("<html>y</html>", content_type="text/html"),
        }
    )
    report, _ = crawl_of(tmp_path, replies, seeds=(SITE, other), pages=3)

    assert len(report.receipts) == 3
    assert report.stopped_reason is StopReason.PAGE_CEILING
    assert len(report.remaining) == report.planned - 3


# ======================================================================
# 一页失败不是一次崩溃
# ======================================================================
def test_a_page_that_was_blocked_does_not_stop_the_next_one(tmp_path: Path) -> None:
    """**正对照**：一页被拦下，后面那页照取。

    停在这里的代价是「一个站上一页取不到，这个站的其余页面全部读不到」——
    而报告上看起来只是「抓到了一部分」。
    """
    replies = happy_site()
    replies[f"{SITE}/contact-us"] = Reply(body="Just a moment...", status=403)
    report, _ = crawl_of(tmp_path, replies)

    assert report.receipts[1].outcome is FetchOutcome.BLOCKED, "这一页要真的被拦下"
    assert len(report.receipts) == len(report.planned_urls), "后面每一页都要取到"
    assert report.stopped_reason is StopReason.COMPLETED


def test_a_page_that_failed_is_still_a_receipt(tmp_path: Path) -> None:
    """404 也是一条回执 —— 它不是「没有这一页」，而是「这一页取不到」。"""
    replies = happy_site()
    replies[f"{SITE}/contact-form"] = Reply("no such page", status=404)
    report, _ = crawl_of(tmp_path, replies, pages=10)

    missing = [item for item in report.receipts if item.url == f"{SITE}/contact-form"]
    assert missing, "这一页必须在回执里"
    assert missing[0].outcome is FetchOutcome.NOT_FOUND
    assert missing[0].body_path is None, "没取到字节就不该有镜像"


def test_browser_mode_is_asked_of_every_page(tmp_path: Path) -> None:
    """``mode=browser`` 时每一页都走浏览器层级 —— 本部署没接线，于是每页都有理由。

    记 ``BROWSER`` 而不是 ``HTTP`` 是刻意的（见 ``DocumentAcquisition._refused``）：
    这一次**属于浏览器层级**，它只是没能发生。
    """
    report, _ = crawl_of(tmp_path, happy_site(), mode="browser")

    assert report.receipts, "要有回执才谈得上每页都问过"
    assert {item.mode for item in report.receipts} == {FetchMode.BROWSER}, "问的是浏览器"
    assert {item.fetch_tier for item in report.receipts} == {FetchTier.BROWSER}
    assert {item.outcome for item in report.receipts} == {FetchOutcome.ERROR}
    assert {item.reason for item in report.receipts} == {"browser_not_wired"}
    assert report.stopped_reason is StopReason.COMPLETED, "取不到不等于没取过"


# ======================================================================
# 没有目标时
# ======================================================================
def test_a_seed_the_safety_judgement_refuses_fetches_nothing(tmp_path: Path) -> None:
    """私网地址作为种子 → 一次请求都不发（**连 robots 都不发**）。"""
    report, http = crawl_of(tmp_path, happy_site(), seeds=("http://10.0.0.1/x",))

    assert http.asked == [], f"不该有请求，实际发了 {http.asked}"
    assert report.receipts == ()
    assert report.stopped_reason is StopReason.NO_TARGETS
    assert any("seed_refused" == gap.get("gap") for gap in report.gaps)


def test_no_targets_is_its_own_reason_and_carries_the_plans_gaps(tmp_path: Path) -> None:
    """站点整站拒绝（``Disallow: /``）→ 没有目标，而**原因在计划里**。

    「没有目标」与「取了但都失败」是两件事，而且第一种必须转述计划给出的理由 ——
    编排自己编一句「没找到页面」会把「站点说了不要」写成「站点没有页面」。

    被拦下的地方是 ``by_robots`` 这个**计数**，不是 ``deny_all`` 那个布尔：
    ``deny_all`` 说的是「这份策略不可信」（见 ``RobotsPolicy`` 的注释），而
    「有一条 ``Disallow: /``」推不出「整个站都不许读」—— 一条更长的 ``Allow:``
    可以压过它。所以站点地图照读，而这一条判据钉住那个区别。
    """
    replies = happy_site()
    replies[ROBOTS] = Reply(f"Sitemap: {SITEMAP}\nUser-agent: *\nDisallow: /\n")
    report, http = crawl_of(tmp_path, replies)

    assert report.receipts == ()
    assert report.stopped_reason is StopReason.NO_TARGETS
    assert http.asked == [ROBOTS, SITEMAP], "策略不可信时才连站点地图都不读"
    assert report.plans, "计划本身要在报告里"
    plan = report.plans[0]
    assert plan.skipped["by_robots"] == plan.pooled + 1, "池子里每一条加上种子都被拦下"
    assert any(gap.get("gap") == "all_pooled_urls_skipped" for gap in plan.gaps)
    assert any("no_targets" == gap.get("gap") for gap in report.gaps), (
        "编排层也要说一句，否则「种子的计划是空的」与「这次什么都没发生」同形"
    )


def test_no_seeds_at_all_is_its_own_reason(tmp_path: Path) -> None:
    """没有种子 → 没有目标，而报告说得出「这次没有种子」。

    空清单**不是**「这个站没有页面」：前者是调用方给的输入有问题，后者是站点事实，
    而两者的下一步完全不同（去改参数 vs 去查站点地图）。空白字符串按同一条处理 ——
    它是「给了一条种子」和「没给」之间最容易混淆的那个输入。
    """
    for seeds in ((), ("   ",)):
        report, http = crawl_of(tmp_path / str(len(seeds)), happy_site(), seeds=seeds)

        assert report.seeds == ()
        assert report.receipts == ()
        assert report.stopped_reason is StopReason.NO_TARGETS
        assert http.asked == [], "没有种子就不该有任何请求"
        assert any(gap.get("gap") == "no_seeds" for gap in report.gaps)


# ======================================================================
# 不变量
# ======================================================================
def test_remaining_is_empty_exactly_when_nothing_was_left(tmp_path: Path) -> None:
    """``remaining`` 与 ``stopped_reason`` 必须互相印证。"""
    for pages, seconds, per_page in ((10, 60.0, 0.0), (2, 60.0, 0.0), (10, 10.0, 10.0)):
        report, _ = crawl_of(
            tmp_path / f"{pages}-{seconds}-{per_page}",
            happy_site(),
            pages=pages,
            seconds=seconds,
            per_page=per_page,
        )
        finished = report.stopped_reason is StopReason.COMPLETED
        assert (report.remaining == ()) is finished, (
            f"pages={pages} seconds={seconds} 时 remaining 与停止理由对不上"
        )


def test_the_same_seeds_twice_give_the_same_order(tmp_path: Path) -> None:
    """确定性：同样的输入，同样的顺序。

    抓取顺序是报告的一部分（「先抓了哪些页面」），一个每次都变一点的顺序会让
    两次运行的报告无法比较。
    """
    first, _ = crawl_of(tmp_path / "a", happy_site())
    second, _ = crawl_of(tmp_path / "b", happy_site())

    assert first.urls == second.urls


def test_a_ceiling_of_zero_is_refused_rather_than_silently_fetching(tmp_path: Path) -> None:
    """``pages=0`` 是拒绝，不是「那就取一页」。

    上限由服务端给，所以到不了这里；但如果它到了，静默取一页就是把「这次调用没有
    页数额度」写成「这次调用取到了一页」。
    """
    with pytest.raises(ValueError, match="pages"):
        crawl_of(tmp_path, happy_site(), pages=0)


def test_the_links_gap_is_gone_because_the_links_are_here(tmp_path: Path) -> None:
    """链接缺席的那条 gap 已经撤掉了 —— 它现在会**说错话**。

    这条 gap 原来写的是「本报告没有回答这一页有没有链接」。链接落地之后，逐页回执
    真的带上了链接（在服务面，由解析出的 ``ParsedDocument.links`` 得到），于是那句
    话从「补上了一个缺口」变成了「一个说错话的字段」—— 而读报告的人无从分辨这两种
    gap。所以它必须在链接落地的同一个 commit 里消失。

    位置也值得记一笔：链接**不住在** ``SiteCrawlReport`` 上（本模块不解析文档，
    它的文件头写着这句话），它住在服务面那份回执上。这一条只守「缺口撤了」，
    「链接真的在」由 ``tests/test_mcp_research_tools.py`` 守着 —— 两件事在两个
    层次上，分开写才指得到红在哪。
    """
    report, _ = crawl_of(tmp_path, happy_site())

    gaps = {item.get("gap") for item in report.gaps}
    assert "links_not_collected" not in gaps, (
        "链接已经采集了，这条 gap 现在是一句假话"
    )

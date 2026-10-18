"""站点地图前沿：robots 策略、站点地图解析、分级排序、以及 ``plan()`` 的整条路径。

## 全部零网络

替身 ``FakeHttp`` 按一张 URL 表回答，但**分类不是它编的**：它把
``(status, content_type, body)`` 交给真的 ``assess_http_response``，落盘规则也照
``http.py`` 那一条（只有 OK / EMPTY_SHELL 才有镜像）。这样「200 的 WAF 页会被判成
BLOCKED」这类判据测的是真实规则，而不是替身作者的印象。

## 替身住在 ``tests/fixtures/http_fakes.py``

``FakeHttp`` / ``Reply`` 有两个消费者（本文件与 ``tests/test_site_crawl.py``），
所以它们搬去了 ``fixtures/``。抄第二份的代价不是几行代码，而是两份替身会分叉。

## 这一组判据的由来

``page_discovery_hints`` 与 ``compliance.respect_robots_txt`` 此前都没有执行器
（后者唯一被提到的地方是一条断言它自己是 ``True`` 的测试）。这个文件是它们的
执行器，所以下面每一条都在问「那条声明今天真的被用上了吗」，而不是「这段代码
照着我写的样子跑了吗」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from marketops.application.sitemap_frontier import (
    MAX_SITEMAP_DOCUMENTS,
    RobotsPolicy,
    SitemapFrontier,
    SkipReason,
    host_of,
    match_tier,
    origin_of,
    parse_robots,
    parse_sitemap,
    same_site,
    site_key,
    sitemap_candidates,
)
from marketops.domain.enums import FetchOutcome

from .fixtures.http_fakes import FakeHttp, Reply
from .fixtures.site_content import index_xml, sitemap_xml

SITE = "https://example.com"
ROBOTS = f"{SITE}/robots.txt"
SITEMAP = f"{SITE}/sitemap.xml"

HINTS: Mapping[str, tuple[str, ...]] = {
    "high": ("*/contact*", "*/staff*"),
    "medium": ("*/facilities*", "*/about*"),
    "low": ("*/", "*/news*"),
}


def plan_of(tmp_path: Path, replies: Mapping[str, Reply], *, limit: int = 20, seed: str = SITE):
    http = FakeHttp(tmp_path / "mirrors", replies)
    frontier = SitemapFrontier(http=http, hints=HINTS)
    return frontier.plan(seed, limit=limit), http


# ======================================================================
# robots.txt 的解析
# ======================================================================
def test_robots_lines_are_read_into_sitemaps_and_rules() -> None:
    policy = parse_robots(
        "# 注释\n"
        "Sitemap: https://example.com/sitemap.xml\n"
        "Sitemap: https://example.com/news-sitemap.xml\n"
        "Sitemap: https://example.com/sitemap.xml\n"
        "User-agent: *\n"
        "Disallow: /private/\n"
        "Allow: /private/public/\n"
    )
    assert policy.sitemaps == (
        "https://example.com/sitemap.xml",
        "https://example.com/news-sitemap.xml",
    ), "重复的 Sitemap 行要去重，顺序要保留"
    assert policy.rules == (("/private/", False), ("/private/public/", True))
    assert policy.allows("https://example.com/private/x") is False
    assert policy.allows("https://example.com/private/public/x") is True


def test_only_the_star_group_applies() -> None:
    """**只应用 ``User-agent: *`` 那一组。**

    我们发的是一个浏览器 UA。按 token 去匹配 ``User-agent: Googlebot`` 那样的组是
    自欺：那一组不是写给我们的，照它执行会得到一个站点从未对我们表达过的许可。
    这条判据同时钉住两个方向 —— 别的组不许生效，``*`` 组必须生效。
    """
    policy = parse_robots(
        "User-agent: Googlebot\nDisallow: /for-google/\nUser-agent: *\nDisallow: /for-everyone/\n"
    )
    assert policy.allows(f"{SITE}/for-google/x") is True, "把别的爬虫的规则用在了自己身上"
    assert policy.allows(f"{SITE}/for-everyone/x") is False, "``*`` 组没有被应用"


def test_rules_before_any_user_agent_are_ignored() -> None:
    """任何 ``User-agent`` 之前的规则不属于任何组（RFC 9309 §2.2.1）。"""
    assert parse_robots("Disallow: /x\nUser-agent: *\n").rules == ()
    assert parse_robots("Disallow: /x\nUser-agent: *\n").allows(f"{SITE}/x") is True


def test_an_empty_disallow_is_not_a_rule_against_the_empty_path() -> None:
    """``Disallow:`` 空值 = 什么都不禁，不是「禁止空路径」。

    把它读成一条规则是很自然的实现，而后果是全站拒绝 —— 一个标准写法变成一次
    静默的抓取失败。
    """
    assert parse_robots("User-agent: *\nDisallow:\n").rules == ()
    assert parse_robots("User-agent: *\nDisallow:\n").allows(f"{SITE}/any") is True


def test_the_longest_pattern_wins() -> None:
    """更具体的那一条胜出，与它在文件里的先后无关。"""
    policy = parse_robots(
        "User-agent: *\nDisallow: /a\nAllow: /a/b\nDisallow: /a/b/c\n"
    )
    assert policy.allows(f"{SITE}/a/x") is False
    assert policy.allows(f"{SITE}/a/b/x") is True
    assert policy.allows(f"{SITE}/a/b/c/x") is False


def test_a_tie_goes_to_allow() -> None:
    """等长时 **Allow 胜**（RFC 9309 §2.2.2）。

    按「先出现的胜」实现会让同一个站的两份 robots 得到相反的结论，而这两份文件
    在语义上完全一样。
    """
    assert parse_robots("User-agent: *\nDisallow: /a\n").allows(f"{SITE}/a") is False
    assert (
        parse_robots("User-agent: *\nDisallow: /a\nAllow: /a\n").allows(f"{SITE}/a") is True
    )
    assert (
        parse_robots("User-agent: *\nAllow: /a\nDisallow: /a\n").allows(f"{SITE}/a") is True
    )


def test_a_dollar_anchor_stops_at_the_path_end() -> None:
    policy = parse_robots("User-agent: *\nDisallow: /*.pdf$\n")
    assert policy.allows(f"{SITE}/x.pdf") is False
    assert policy.allows(f"{SITE}/x.pdf?download=1") is True, "$ 之外还有查询串"
    assert policy.allows(f"{SITE}/x.pdf.html") is True


def test_a_star_matches_any_sequence() -> None:
    policy = parse_robots("User-agent: *\nDisallow: /*/private\n")
    assert policy.allows(f"{SITE}/a/private") is False
    assert policy.allows(f"{SITE}/private") is True, "规则要求前面还有一段"


def test_a_pattern_without_a_leading_slash_is_still_compared_to_the_path() -> None:
    """``Disallow: private`` 要能拦住 ``/private``。

    不补那个斜杠时，这条规则永远匹配不上，于是它静默变成「什么也没禁」——
    一份写错了一点的 robots 被我们读成一份什么都没说的 robots。
    """
    assert parse_robots("User-agent: *\nDisallow: private\n").allows(f"{SITE}/private") is False


def test_deny_all_overrides_every_rule() -> None:
    """策略不可信时，连 ``Allow`` 都不算数。"""
    policy = RobotsPolicy(
        rules=(("/", True),), deny_all=True
    )
    assert policy.allows(f"{SITE}/anything") is False


# ======================================================================
# 站点地图的解析
# ======================================================================
def test_sitemap_entries_are_split_into_pages_children_and_archives() -> None:
    pages, children, gzipped = parse_sitemap(
        sitemap_xml(f"{SITE}/a", f"{SITE}/b").replace(
            "</urlset>", ""
        )
        + f"<sitemap><loc>{SITE}/child.xml</loc></sitemap>"
        + f"<sitemap><loc>{SITE}/big.xml.gz</loc></sitemap>"
        + "</urlset>"
    )
    assert pages == (f"{SITE}/a", f"{SITE}/b"), "顺序是站点地图作者给先验，不能重排"
    assert children == (f"{SITE}/child.xml",)
    assert gzipped == (f"{SITE}/big.xml.gz",)


def test_a_namespaced_sitemap_is_read_all_the_same() -> None:
    """方言多到不值得为每一种写分支 —— 只要 ``<loc>``。"""
    pages, _, _ = parse_sitemap(
        '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'
        ' xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
        f"<url><loc>{SITE}/contact</loc><image:image><loc>{SITE}/pic.png</loc>"
        "</image:image></url></urlset>"
    )
    assert pages == (f"{SITE}/contact", f"{SITE}/pic.png"), "嵌套的 loc 也在文件顺序里"


def test_the_sitemap_falls_back_to_the_conventional_location() -> None:
    assert sitemap_candidates(SITE, RobotsPolicy()) == (f"{SITE}/sitemap.xml",)
    declared = RobotsPolicy(sitemaps=(f"{SITE}/news.xml",))
    assert sitemap_candidates(SITE, declared) == (f"{SITE}/news.xml",)
    assert sitemap_candidates("", RobotsPolicy()) == (), "没有 origin 就没有可退回的位置"


# ======================================================================
# 分级：顺序是 profile 的声明，不是代码里的常量
# ======================================================================
def test_the_declared_order_decides_not_a_hardcoded_one() -> None:
    """把 ``critical`` 写在 ``high`` 前面，它就该先被抓。

    分级表的顺序属于**声明本身**：一份 profile 可以说 ``critical`` 比 ``high``
    重要，而代码里另写一份 ``("high","medium","low")`` 会让那句话失效，且不留痕。
    """
    hints = {"critical": ("*/urgent*",), "high": ("*/contact*",)}
    assert match_tier(f"{SITE}/contact", hints)[0] == "high"
    assert match_tier(f"{SITE}/urgent", hints)[0] == "critical"

    reordered = {"high": ("*/contact*",), "critical": ("*/urgent*",)}
    assert match_tier(f"{SITE}/contact", reordered)[0] == "high"


def test_the_root_glob_means_the_root() -> None:
    """``*/`` 是首页，不是「任何以斜杠结尾的路径」。

    按通配符直译会让这条提示变成「整站进最高优先级」，而它在 profile 里本来是
    ``low`` 那一级的。
    """
    assert match_tier(f"{SITE}/", {"low": ("*/",)}) == ("low", "*/")
    assert match_tier(f"{SITE}/about/", {"low": ("*/",)}) is None
    assert match_tier(f"{SITE}/contact-us", {"high": ("*/contact*",)}) == (
        "high",
        "*/contact*",
    )


def test_an_unmatched_url_has_no_tier() -> None:
    assert match_tier(f"{SITE}/unlisted", HINTS) is None


def test_hosts_compare_through_one_leading_www() -> None:
    assert site_key("WWW.Example.COM") == "example.com"
    assert site_key("example.com") == "example.com"
    assert site_key("www.www.example.com") == "www.example.com", "只去掉一个"
    assert host_of("https://Example.COM:8443/x") == "example.com"
    assert origin_of("https://example.com/deep/page?a=1") == "https://example.com"


def test_a_subdomain_is_another_site() -> None:
    """子域有它自己的 robots.txt，按种子那一份执行它等于替它表态。"""
    assert same_site("example.com", "https://www.example.com/x") is True
    assert same_site("www.example.com", "https://example.com/x") is True
    assert same_site("example.com", "https://sport.example.com/x") is False, (
        "子域被当成了同一个站 —— 那样它的 robots 规则会被另一个站的文件替掉"
    )
    assert same_site("example.com", "https://aggregator.example.net/x") is False


# ======================================================================
# plan()：整条路径
# ======================================================================
def happy_replies(*extra_pages: str) -> dict[str, Reply]:
    pages = (
        f"{SITE}/contact-us",
        f"{SITE}/staff",
        f"{SITE}/about",
        f"{SITE}/news/1",
        f"{SITE}/",
        f"{SITE}/unlisted-page",
    ) + extra_pages
    return {
        ROBOTS: Reply(f"Sitemap: {SITEMAP}\nUser-agent: *\nDisallow: /dont/\n"),
        SITEMAP: Reply(sitemap_xml(*pages), content_type="application/xml"),
    }


def test_the_plan_is_seed_first_then_declared_tiers(tmp_path: Path) -> None:
    plan, http = plan_of(tmp_path, happy_replies())

    assert plan.urls[0] == SITE, "种子由调用方指定，排在第一位"
    assert plan.targets[0].tier == "" and plan.targets[0].why == "seed"
    assert plan.urls[1:3] == (f"{SITE}/contact-us", f"{SITE}/staff"), "high 级在前"
    assert plan.urls[3:5] == (f"{SITE}/about", f"{SITE}/news/1"), (
        "medium 在 low 之前；同级内保持站点地图的顺序"
    )
    assert plan.urls[5] == f"{SITE}/"
    assert plan.tier_hits == {"high": 2, "medium": 1, "low": 2}, (
        "零命中的优先级也要留着 —— 那是「这条提示在这个站上没有对应页面」的读数"
    )
    assert plan.skipped[SkipReason.UNMATCHED.value] == 1
    assert plan.pooled == 6
    assert plan.sitemap_documents == 1
    assert plan.truncated == ""
    assert http.asked[0] == ROBOTS, "先读 robots，再读站点地图"


def test_a_disallowed_page_is_counted_and_left_out(tmp_path: Path) -> None:
    """robots 拦下的页面**不进计划**，而且它确实是站点地图给出过的。

    对照用的是**同一份站点地图**，只把 Disallow 去掉。这一条是必须的：被拦下的
    那一页若本来就不命中任何提示（那样它也进不了计划），这个用例测的就不是
    robots 而是分级表。
    """
    page = f"{SITE}/staff/secret"
    blocked = happy_replies(page)
    blocked[ROBOTS] = Reply(f"Sitemap: {SITEMAP}\nUser-agent: *\nDisallow: /staff/\n")
    plan, _ = plan_of(tmp_path, blocked)

    assert page not in plan.urls
    assert plan.skipped[SkipReason.BY_ROBOTS.value] == 1

    without = happy_replies(page)
    without[ROBOTS] = Reply(f"Sitemap: {SITEMAP}\nUser-agent: *\n")
    control, _ = plan_of(tmp_path / "control", without)
    assert page in control.urls, (
        "没有 Disallow 时它也不进计划 —— 那么上面那条判据测的不是 robots"
    )


def test_the_task_of_reading_robots_can_still_find_a_sitemap(tmp_path: Path) -> None:
    """没有 ``Sitemap:`` 行时退回约定位置 —— 但要真去取，不是假装。"""
    replies = happy_replies()
    replies[ROBOTS] = Reply("User-agent: *\n")
    plan, http = plan_of(tmp_path, replies)

    assert SITEMAP in http.asked
    assert len(plan.urls) > 1


def test_a_missing_robots_means_no_rules(tmp_path: Path) -> None:
    """404 ⇒ 没有规则，继续抓（RFC 9309 §2.3.1.3）。"""
    replies = happy_replies()
    replies[ROBOTS] = Reply(status=404)
    plan, _ = plan_of(tmp_path, replies)

    assert [gap["gap"] for gap in plan.gaps] == ["robots_absent"]
    assert len(plan.urls) > 1, "404 被读成了「不许抓」"


def test_an_unreadable_robots_means_deny_everything(tmp_path: Path) -> None:
    """5xx / 超时 ⇒ **假设全站拒绝**（§2.3.1.4），而且连站点地图都不去读。

    抓取是抓取 —— 站点连自己的规则都取不到时，继续读它的站点地图是在替它做决定。
    """
    for index, reply in enumerate((Reply(status=500), Reply(transport=FetchOutcome.TIMEOUT))):
        replies = happy_replies()
        replies[ROBOTS] = reply
        plan, http = plan_of(tmp_path / str(index), replies)

        assert plan.urls == ()
        assert http.asked == [ROBOTS], f"{reply} 之后还去抓了别的东西"
        assert [gap["gap"] for gap in plan.gaps] == ["robots_unavailable"]
        assert "全站拒绝" in plan.gaps[0]["detail"]


def test_a_blocked_robots_read_says_blocked_not_missing(tmp_path: Path) -> None:
    """站点在 robots 这一跳就把我们拦下 —— 那是「拒我们」，不是「没有这个文件」。"""
    replies = happy_replies()
    replies[ROBOTS] = Reply("<html><title>Just a moment...</title></html>", content_type="text/html")
    plan, http = plan_of(tmp_path, replies)

    assert plan.urls == ()
    assert http.asked == [ROBOTS]
    assert [gap["gap"] for gap in plan.gaps] == ["robots_blocked"]


def test_an_unsafe_url_never_leaves_the_process(tmp_path: Path) -> None:
    """站点地图里塞一个私网地址 ⇒ 既不入计划，也**一次都没被请求过**。

    「跳过」与「请求过再跳过」在报告上同形，所以这条判据看的是替身被问过什么。
    计到 ``unsafe_target`` 而不是 ``off_site`` 也是判据的一部分：顺序反过来的实现
    会让一个私网地址在报告上表现成「别家的站」。
    """
    plan, http = plan_of(tmp_path, happy_replies("http://169.254.169.254/latest/meta-data/"))

    assert "http://169.254.169.254/latest/meta-data/" not in http.asked
    assert plan.skipped[SkipReason.UNSAFE_TARGET.value] == 1
    assert SkipReason.OFF_SITE.value not in plan.skipped


def test_an_off_site_url_is_counted_and_never_fetched(tmp_path: Path) -> None:
    plan, http = plan_of(tmp_path, happy_replies("https://aggregator.example.net/listing"))

    assert "https://aggregator.example.net/listing" not in http.asked
    assert plan.skipped[SkipReason.OFF_SITE.value] == 1


def test_sitemap_index_children_are_followed(tmp_path: Path) -> None:
    replies = {
        ROBOTS: Reply(f"Sitemap: {SITEMAP}\nUser-agent: *\n"),
        SITEMAP: Reply(
            index_xml(f"{SITE}/s1.xml", f"{SITE}/s2.xml"), content_type="application/xml"
        ),
        f"{SITE}/s1.xml": Reply(sitemap_xml(f"{SITE}/contact-us"), content_type="application/xml"),
        f"{SITE}/s2.xml": Reply(sitemap_xml(f"{SITE}/news/1"), content_type="application/xml"),
    }
    plan, http = plan_of(tmp_path, replies)

    assert f"{SITE}/s1.xml" in http.asked and f"{SITE}/s2.xml" in http.asked
    assert plan.sitemap_documents == 3
    assert f"{SITE}/contact-us" in plan.urls and f"{SITE}/news/1" in plan.urls


def test_the_number_of_sitemap_documents_is_capped(tmp_path: Path) -> None:
    """站点地图可以指向上千个分片 —— 封顶，并且说得出还剩几份没读。"""
    children = tuple(f"{SITE}/s{n}.xml" for n in range(10))
    replies = {
        ROBOTS: Reply(f"Sitemap: {SITEMAP}\nUser-agent: *\n"),
        SITEMAP: Reply(index_xml(*children), content_type="application/xml"),
    }
    for child in children:
        replies[child] = Reply(sitemap_xml(f"{SITE}/contact-{child[-6:-4]}"), content_type="application/xml")

    plan, _ = plan_of(tmp_path, replies)

    assert plan.sitemap_documents == MAX_SITEMAP_DOCUMENTS
    gaps = {gap["gap"] for gap in plan.gaps}
    assert "sitemap_documents_capped" in gaps, "截断了却什么也没说"


def test_a_gzipped_sitemap_is_named_not_read_as_noise(tmp_path: Path) -> None:
    """压缩的站点地图按文本读会得到二进制噪音，而噪音里没有 ``<loc>`` ——
    于是它会被读成「这份站点地图是空的」。那是一个假结论，所以改记一句话。"""
    replies = {
        ROBOTS: Reply(f"Sitemap: {SITEMAP}\nUser-agent: *\n"),
        SITEMAP: Reply(index_xml(f"{SITE}/big.xml.gz"), content_type="application/xml"),
    }
    plan, http = plan_of(tmp_path, replies)

    assert f"{SITE}/big.xml.gz" not in http.asked
    gaps = {gap["gap"] for gap in plan.gaps}
    assert "sitemap_gzip_unsupported" in gaps
    assert "no_sitemap_urls" in gaps, "压缩包跳过之后池子确实是空的 —— 这一句也要说"


def test_the_limit_truncates_and_says_so(tmp_path: Path) -> None:
    plan, _ = plan_of(tmp_path, happy_replies(), limit=3)

    assert len(plan.urls) == 3
    assert plan.skipped[SkipReason.BEYOND_LIMIT.value] == 3
    assert "limit=3" in plan.truncated
    assert plan.tier_hits == {"high": 2, "medium": 1, "low": 2}, (
        "tier_hits 记的是命中条数，不是进计划的条数"
    )


def test_a_refused_seed_costs_nothing(tmp_path: Path) -> None:
    """种子被安全判据拒绝 ⇒ **一条请求都不发**，而且理由说得出是哪一条判据。"""
    plan, http = plan_of(tmp_path, happy_replies(), seed="http://127.0.0.1/admin")

    assert http.asked == []
    assert plan.urls == ()
    assert [gap["gap"] for gap in plan.gaps] == ["seed_refused"]
    assert "127.0.0.1" in plan.gaps[0]["detail"]


def test_a_seed_the_site_disallows_is_not_crawled(tmp_path: Path) -> None:
    """种子**不是**一条授权：它和站点地图里的 URL 走同一套判据。"""
    replies = happy_replies()
    replies[ROBOTS] = Reply(f"Sitemap: {SITEMAP}\nUser-agent: *\nDisallow: /\n")
    plan, _ = plan_of(tmp_path, replies)

    assert plan.urls == ()
    assert plan.skipped[SkipReason.BY_ROBOTS.value] > 0
    assert "all_pooled_urls_skipped" in {gap["gap"] for gap in plan.gaps}


def test_no_page_hints_is_refused_before_any_request(tmp_path: Path) -> None:
    """没有分级表就没有「先抓哪些」的规则 ⇒ 拒绝，而且在 I/O 之前。

    「这个部署没声明提示」与「这个站的站点地图是空的」必须分得开：前者是配置
    问题，后者是站点事实。两者都让计划为空，但只有后者该被写进研究结论。
    """
    http = FakeHttp(tmp_path / "mirrors", happy_replies())
    plan = SitemapFrontier(http=http, hints={}).plan(SITE, limit=20)

    assert http.asked == []
    assert [gap["gap"] for gap in plan.gaps] == ["no_page_hints"]


def test_the_seed_appearing_in_its_own_sitemap_is_counted_once(tmp_path: Path) -> None:
    """站点地图把自己列进去是常事 —— 计数为 1 不代表有问题，但要数得出来。"""
    plan, _ = plan_of(tmp_path, happy_replies(SITE))

    assert plan.urls.count(SITE) == 1
    assert plan.skipped[SkipReason.DUPLICATE.value] == 1


def test_an_empty_sitemap_is_a_gap_not_an_empty_plan(tmp_path: Path) -> None:
    replies = happy_replies()
    replies[SITEMAP] = Reply("<urlset></urlset>", content_type="application/xml")
    plan, _ = plan_of(tmp_path, replies)

    assert plan.urls == (SITE,), "站点地图为空时只剩种子"
    gaps = {gap["gap"] for gap in plan.gaps}
    assert "sitemap_empty" in gaps
    assert "no_sitemap_urls" in gaps


def test_two_plans_of_the_same_site_are_identical(tmp_path: Path) -> None:
    """确定性的可测形态：同一份输入两次跑出同一份计划。

    没有这一条，「分级是 URL 的函数」只是自觉 —— 一个按集合遍历顺序输出的实现
    在同一个进程里也可能给出两种顺序，而报告上的差异会被归因到站点。
    """
    first, _ = plan_of(tmp_path, happy_replies())
    second, _ = plan_of(tmp_path / "again", happy_replies())

    assert first.urls == second.urls
    assert [target.tier for target in first.targets] == [
        target.tier for target in second.targets
    ]


def test_a_plan_with_nothing_in_it_always_carries_a_reason(tmp_path: Path) -> None:
    """空计划必须带一句为什么。

    没有这一条时，一份空计划与一次「这个站确实没有可用页面」在报告上同形 ——
    而后者是一次**断言**，它需要依据。三种造出空计划的路各断言一次。
    """
    # 1. 站点声明了拒绝（robots 取到了，但它是 Disallow: /）—— 池子里有 URL，
    #    而种子也是其中一条被拦下的，所以计划真的空了。
    denied = happy_replies()
    denied[ROBOTS] = Reply(f"Sitemap: {SITEMAP}\nUser-agent: *\nDisallow: /\n")
    plan, _ = plan_of(tmp_path / "denied", denied)
    assert plan.urls == () and plan.gaps
    assert "all_pooled_urls_skipped" in {gap["gap"] for gap in plan.gaps}
    # 种子被**和池子一起**分类，不是被豁免的 —— 这里的种子是 ``https://example.com``
    # 而池子里那条是 ``https://example.com/``，两条不同的 URL，所以是 pooled + 1。
    assert plan.skipped[SkipReason.BY_ROBOTS.value] == plan.pooled + 1, (
        "池子里每一条加上种子本身都该落在 robots 这一条上，否则这句「全被拦下」是假的"
    )

    # 2. 种子本身就被拒 —— 与上面那一种的处置完全不同（一个是「站点说了不要」，
    #    一个是「这条 URL 不安全」），而两者都让计划为空。
    plan, _ = plan_of(tmp_path / "refused", happy_replies(), seed="http://10.0.0.1/x")
    assert plan.urls == () and plan.gaps
    assert [gap["gap"] for gap in plan.gaps] == ["seed_refused"]

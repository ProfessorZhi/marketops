"""``HttpxFetcher`` 每一个出口都留下**恰好一行**尝试账 —— 生产者那一端的判据。

## 这一份守的是什么

``tests/test_fetch_attempt_store_contract.py`` 与 ``tests/test_sqlite_fetch_attempt.py``
判的是**存储**忠不忠实。存储全绿而一个真实的抓取路径从来没有往里写过一个字节 ——
那正是 §16 点名的那种状态：「有类、有表、有两个实现、有契约，而没有生产者」，
而它在报告上与「这一条已经做完了」看起来一模一样。

所以这里的判据全部锚在**计数**上：一次 ``fetch()`` 之后，账上**恰好**一行，
而 ``REQUESTS`` 上恰好是它真的发出的那几跳。计数能证明「某一跳根本没有离开进程」
（被地址判据拒、被预算拦住），而不是「离开了但结果被丢弃了」—— 后者对 §5 那些要求
毫无意义。

## 参数化那张表是这一份的承重处

``CASES`` 里每一个局面是 ``_run`` 的一个出口。加一个出口而忘了写判据时，下面那条
``test_the_case_table_covers_every_exit`` 会红 —— 而它做不到的事要写清楚：它判不了
「新出口在表里有对应行」，只能判「表的规模没有缩水」。真正让新出口当场现形的是
``test_no_exit_forgets_to_leave_a_diagnosis``（那一份不依赖这张表）。

## 三条「此前没有诊断」的出口各有一条判据

``_dns_failure``、``_refused`` 与传输失败那一支此前 ``diagnosis`` 为 ``None``，
于是它们收成的账写着 ``cause=none, action=proceeded`` —— 一句「这一次没有任何异常、
正常继续」。§5 要记下来的正是**没有产出证据的那些尝试**，而它们本该是这张账上
最清楚的一批。这四条判据判的是那句话不再出现。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
import pytest

from marketops.adapters.fetch_attempts import refusal_diagnosis
from marketops.adapters.http import HttpxFetcher
from marketops.adapters.page_mirror import account_page_dir
from marketops.adapters.sqlite.fetch_attempt import SqliteFetchAttemptStore
from marketops.config import Settings
from marketops.domain.access import ActionTaken, SuspectedCause
from marketops.domain.access_governance import AccessPolicy
from marketops.domain.enums import BlockReason, FetchOutcome, FetchTier
from marketops.domain.errors import StoreError
from marketops.domain.fetch_attempt import BodyKind, MethodKind
from marketops.interfaces.composition import (
    default_acquisition,
    default_attempt_store,
    default_http,
)
from marketops.ports.acquisition import FetchRequest, FetchResult
from marketops.ports.dns import DnsFailureKind, HostResolver
from marketops.ports.fetch_attempt import FetchAttemptStore
from marketops.security.network import MAX_REDIRECTS, TargetRefusal

from .fixtures.http_transport import (
    CHALLENGE_BODY,
    PAGE,
    PLAIN_BODY,
    SECOND,
    SITE,
    FailingResolver,
    FakeResolver,
    Route,
    client,
    make_fetcher,
    page,
    redirect,
)

SCOPE = "account-001"
#: 一个真的私网字面量。``check_target`` 在**发请求之前**就该拒它。
LOOPBACK_URL = "http://127.0.0.1/contact"


@pytest.fixture
def ledger(settings: Settings) -> SqliteFetchAttemptStore:
    """**落盘**的那一份，落在 ``settings`` 指的那个库上。

    用它而不是内存那一份：生产接的就是它，而「组合根接上的线是不是这条」是这一份
    要判的事情之一。落盘也顺带让「一次 fetch 只写一行」在**数据库层**成立
    （``attempt_id`` 是主键），而不是靠一个 dict 的样子。
    """
    return SqliteFetchAttemptStore(settings.database_path)


def ledger_factory(store: FetchAttemptStore) -> Callable[[], FetchAttemptStore]:
    """一张只交同一个实例的工厂 —— 就是 ``default_attempt_store`` 的形状。"""
    return lambda: store


# ----------------------------------------------------------------------
# 每一个出口：一次 fetch 恰好一行账，而请求数逐格对得上
# ----------------------------------------------------------------------
RING_START = f"{SITE}/hop-0"


def _ring() -> dict[str, Route]:
    """一条走不到头的链：``MAX_REDIRECTS + 2`` 个地址首尾相接，从 :data:`RING_START` 起。

    最后一跳仍然是 302，所以拒绝发生在**跟随第 6 跳之前** —— ``MAX_REDIRECTS`` 是
    5 而循环跑到 ``hop == 5`` 才判。多铺一格（``hop-6`` 只被指向、从不被登记响应）
    是为了让「链的尽头」不在表里：一旦实现真的多走一步，``client`` 会因为没登记而
    断言失败，而不是静静地走过去。
    """
    hosts = [f"{SITE}/hop-{index}" for index in range(MAX_REDIRECTS + 2)]
    return {
        hosts[index]: redirect(hosts[index], hosts[index + 1])
        for index in range(MAX_REDIRECTS + 1)
    }


@dataclass
class Case:
    """一个出口：它的局面、真的发出过几跳、以及该记成什么样。"""

    routes: dict[str, Route] = field(default_factory=dict)
    url: str = PAGE
    resolver: HostResolver | None = None
    policy: AccessPolicy | None = None
    #: **真的**发出过几跳。被拒 / 解析失败时是 0 —— 这一格是判据的核心。
    requests: int = 1
    outcome: FetchOutcome = FetchOutcome.OK
    cause: SuspectedCause = SuspectedCause.NONE
    action: ActionTaken = ActionTaken.PROCEEDED


CASES = [
    pytest.param(Case(routes={PAGE: page()}), id="200-正常页面"),
    pytest.param(
        Case(
            routes={PAGE: page(status=403)},
            outcome=FetchOutcome.BLOCKED,
            cause=SuspectedCause.ACCESS_DENIED_UNKNOWN,
            action=ActionTaken.STOPPED_METHOD,
        ),
        id="403",
    ),
    pytest.param(
        Case(
            routes={PAGE: page(status=429, headers={"retry-after": "120"})},
            outcome=FetchOutcome.BLOCKED,
            cause=SuspectedCause.RATE_LIMITED,
            action=ActionTaken.DEFERRED_BACKOFF,
        ),
        id="429-带Retry-After",
    ),
    pytest.param(
        Case(
            routes={PAGE: page(status=404, body="<html>gone</html>")},
            # **不是 ``ERROR``**：``assess_http_response`` 的第一条就是 404/410 →
            # ``NOT_FOUND``。两者的区别是承重的 —— ``ERROR`` 说「这一页出问题了」，
            # ``NOT_FOUND`` 说「这个来源没了」，而设计 §5 明令后者**不许**被读成
            # 「这个组织消失了」（``diagnose`` 那一格的人话里逐字写着这句）。
            outcome=FetchOutcome.NOT_FOUND,
            cause=SuspectedCause.SOURCE_GONE,
            action=ActionTaken.STOPPED_METHOD,
        ),
        id="404",
    ),
    pytest.param(
        Case(
            routes={PAGE: page(body=CHALLENGE_BODY)},
            outcome=FetchOutcome.BLOCKED,
            cause=SuspectedCause.WAF_CHALLENGE,
            action=ActionTaken.STOPPED_METHOD,
        ),
        id="挑战页",
    ),
    pytest.param(
        Case(
            routes={PAGE: httpx.ConnectTimeout("测试造的连接超时")},
            outcome=FetchOutcome.TIMEOUT,
            cause=SuspectedCause.NETWORK_UNKNOWN,
            action=ActionTaken.DEFERRED_BACKOFF,
        ),
        id="传输层失败",
    ),
    pytest.param(Case(routes={}, resolver=FailingResolver(DnsFailureKind.NONEXISTENT), requests=0,
                      outcome=FetchOutcome.ERROR, cause=SuspectedCause.SOURCE_GONE,
                      action=ActionTaken.STOPPED_METHOD), id="DNS-名字不存在"),
    pytest.param(Case(routes={}, resolver=FailingResolver(DnsFailureKind.TIMEOUT), requests=0,
                      outcome=FetchOutcome.ERROR, cause=SuspectedCause.NETWORK_UNKNOWN,
                      action=ActionTaken.DEFERRED_BACKOFF), id="DNS-超时"),
    pytest.param(Case(routes={}, resolver=FailingResolver(DnsFailureKind.FAILURE), requests=0,
                      outcome=FetchOutcome.ERROR, cause=SuspectedCause.NETWORK_UNKNOWN,
                      action=ActionTaken.DEFERRED_BACKOFF), id="DNS-问到了别的错"),
    pytest.param(Case(url=LOOPBACK_URL, requests=0, outcome=FetchOutcome.BLOCKED,
                      cause=SuspectedCause.UNSAFE_TARGET,
                      action=ActionTaken.REFUSED_UNSAFE_TARGET), id="URL 是环回地址"),
    pytest.param(Case(routes={PAGE: page()}, resolver=FakeResolver("127.0.0.1"), requests=0,
                      outcome=FetchOutcome.BLOCKED, cause=SuspectedCause.UNSAFE_TARGET,
                      action=ActionTaken.REFUSED_UNSAFE_TARGET), id="域名解析到内网"),
    pytest.param(Case(routes={PAGE: page(status=302)}, outcome=FetchOutcome.BLOCKED,
                      cause=SuspectedCause.UNKNOWN, action=ActionTaken.STOPPED_METHOD),
                 id="302-没有Location"),
    pytest.param(Case(url=RING_START, routes=_ring(), requests=MAX_REDIRECTS + 1,
                      outcome=FetchOutcome.BLOCKED, cause=SuspectedCause.UNKNOWN,
                      action=ActionTaken.STOPPED_METHOD),
                 id="重定向超限"),
    pytest.param(Case(routes={PAGE: redirect(PAGE, SECOND), SECOND: page(url=SECOND)},
                      requests=2), id="302-正常跟随"),
]


def test_the_case_table_covers_every_exit() -> None:
    """表的规模本身是一条判据 —— 它挡的是「删掉一行**没有替代**」。

    它判不了「新加了一个出口而表里没有对应行」：那件事这里做不到，因为表不知道
    ``_run`` 有几个出口。能立刻让新出口现形的是下面那条按**全枚举**判的
    ``test_no_exit_forgets_to_leave_a_diagnosis``。

    写下来是为了让下一个人知道这一条守到哪为止 —— 一条自称「覆盖了所有出口」的
    判据比没有判据更坏。
    """
    ids = [param.id for param in CASES]
    assert len(ids) == len(set(ids)), f"用例名字重复：{ids}"
    assert len(CASES) >= 14, (
        f"出口表缩到 {len(CASES)} 条了。删掉一行等于删掉一个出口的判据，"
        "而剩下的行照样全绿。"
    )


@pytest.mark.parametrize("case", CASES)
def test_every_exit_leaves_exactly_one_attempt(
    settings: Settings,
    ledger: SqliteFetchAttemptStore,
    requests_seen: list[str],
    case: Case,
) -> None:
    """**一次抓取一行账**，而请求数逐格对得上。

    「恰好一行」而不是「至少一行」：两次抓取写两次是正常的，但**一次**抓取写出两行
    只可能来自一个把记账放在循环里的实现，而它的表现是报告上「这个 URL 被试了两次」
    —— 那句话会让排查的人去找一次并不存在的重试。

    ``outcome`` / ``cause`` / ``action`` 一起判，是因为它们各自单独都能被一个错误的
    实现凑出来：一次被地址判据拒掉的抓取在 ``outcome`` 上是 ``BLOCKED``（与一次 403
    同形），而区分它们的正是后两格。
    """
    fetcher = make_fetcher(
        settings,
        case.routes,
        resolver=case.resolver,
        policy=case.policy,
        attempts=ledger_factory(ledger),
    )
    result = fetcher.fetch(FetchRequest(case.url, scope=SCOPE))

    assert len(requests_seen) == case.requests, (
        f"真的发出去了 {len(requests_seen)} 跳（{requests_seen}），期望 {case.requests}。"
        "被地址判据拒掉与被预算拦下的那些**一跳都不该发** —— 计数能证明「没有离开"
        "进程」，而「离开了但结果被丢弃」证明不了这件事"
    )

    rows = ledger.list_attempts()
    assert len(rows) == 1, f"一次抓取留下了 {len(rows)} 行账"
    (row,) = rows

    assert result.outcome is case.outcome
    assert row.suspected_cause is case.cause
    assert row.action_taken is case.action
    assert row.observed_status == result.status_code, (
        "账上的状态码必须就是回执上那一格 —— 两处各算一遍的实现会在某一天分叉，"
        "而分叉之后没有人知道该信哪一个"
    )


# ----------------------------------------------------------------------
# 那一行账里写的是什么
# ----------------------------------------------------------------------
def test_the_row_names_the_attempt_not_the_thing_that_was_fetched(
    settings: Settings, ledger: SqliteFetchAttemptStore, requests_seen: list[str]
) -> None:
    """``url`` 是**问过的**那个，``final_url`` 是**落在的**那个，两格分开。

    合成一格之后，「我们问了 A，它把我们送到 B」与「我们问了 B」在账上同形 ——
    而下一次「A 到底还能不能问」这个问题就没有依据了。

    ``redirect_trace`` 也必须非空：它是「这几次尝试之间隔了什么」的唯一记录。
    """
    fetcher = make_fetcher(
        settings,
        {PAGE: redirect(PAGE, SECOND), SECOND: page(url=SECOND)},
        attempts=ledger_factory(ledger),
    )
    fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    (row,) = ledger.list_attempts()
    assert row.url == PAGE, "请求的地址"
    assert row.final_url == SECOND, "落在的地址"
    assert SECOND in row.redirect_trace
    assert row.method is MethodKind.HTTP
    assert row.scope == SCOPE, "账记在谁名下 —— 与页面镜像的目录名是同一个字符串"
    assert row.workspace == settings.profile
    assert row.attempt_id.startswith("att-")


def test_the_window_on_the_ledger_contains_the_work_it_measured(
    settings: Settings, ledger: SqliteFetchAttemptStore, requests_seen: list[str]
) -> None:
    """``started_at`` 量的是这次抓取的**开始**，不是它的结束。

    这一格有两个身份，两个都要求它是开始：它是 ``attempt_id`` 的种子之一（在
    ``_run`` 返回之后取，会让两次相邻的抓取在秒级精度下算出同一个 id），以及
    ``span = ended_at - started_at`` 是账上唯一回答「这一跳花了多久」的量。

    ## 为什么站点那一跳要真的慢下来

    墙上时钟在这个规模上走得比一次假传输慢得多：不睡的话，两种写法（``_run``
    之前取 / 之后取）算出来的窗口都是零点几毫秒，判据分不出来。睡 20 毫秒之后，
    窗口必须**装得下**实测的那段耗时 —— 「之后取」的写法会得到一个近乎零长的
    窗口，于是它比 ``result.elapsed_ms`` 还短。
    """
    def slow_handler(request: httpx.Request) -> httpx.Response:
        time.sleep(0.02)
        return page()

    fetcher = make_fetcher(settings, {PAGE: page()}, attempts=ledger_factory(ledger))
    fetcher._client = httpx.Client(transport=httpx.MockTransport(slow_handler))  # noqa: SLF001
    result = fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    (row,) = ledger.list_attempts()
    span = row.ended_at - row.started_at
    assert span >= timedelta(milliseconds=result.elapsed_ms), (
        f"账上那一段窗口（{span}）比实测的耗时（{result.elapsed_ms} 毫秒）还短 —— "
        "``started_at`` 是在 ``_run`` 返回之后才取的，而这一格的名字与它作为 "
        "``attempt_id`` 种子的身份都要求它是开始"
    )


def test_a_refused_attempt_records_no_body(settings: Settings, ledger: SqliteFetchAttemptStore) -> None:
    """被拒的那一跳：正文一格都不留，而 ``body_kind`` 是 ``NONE`` 而不是 ``UNKNOWN``。

    两者的差别是这一格存在的全部理由：``NONE`` 说「我们没有得到任何正文」（被拒绝、
    超时、判据拦下），``UNKNOWN`` 说「得到了正文但我们没能归类它」。一次被地址判据
    拒掉的抓取写成 ``UNKNOWN``，读起来像「我们拿到了一堆认不出来的字节」。
    """
    fetcher = make_fetcher(settings, {}, attempts=ledger_factory(ledger))
    fetcher.fetch(FetchRequest(LOOPBACK_URL, scope=SCOPE))

    (row,) = ledger.list_attempts()
    assert row.body_kept is False
    assert row.body_bytes == 0
    assert row.body_kind is BodyKind.NONE


def test_the_body_kind_follows_the_content_type(
    settings: Settings, ledger: SqliteFetchAttemptStore
) -> None:
    """**拿得到正文的三类**各判一次，外加一格 ``csv``。

    ``csv`` 那一格是要点：它没有自己的成员，而它**显然是一次文本响应** ——
    归 ``UNKNOWN`` 会说「我们得到了正文但没能归类它」，而事实是我们归得很好，只是
    去重之后与 ``plain`` 同属 ``TEXT``。

    ## ``pdf`` 刻意**不在**这张表里

    ``BodyKind.PDF`` 今天没有生产者，而那不是这一层能判出来的事：见
    ``test_a_pdf_never_reaches_the_ledger_as_a_pdf`` —— 那一格钉住了缺口本身，
    并写明了它归谁。放进这张表会让它读起来像「四类都通了」。
    """
    fetcher = make_fetcher(
        settings,
        {
            f"{SITE}/a.html": page("<html><body>grass</body></html>", url=f"{SITE}/a.html"),
            f"{SITE}/c.json": page('{"ok": true}', url=f"{SITE}/c.json",
                                   headers={"content-type": "application/json"}),
            f"{SITE}/d.csv": page("name,count\nDevon,3", url=f"{SITE}/d.csv",
                                  headers={"content-type": "text/csv"}),
        },
        attempts=ledger_factory(ledger),
    )
    expected = {
        "a.html": BodyKind.HTML,
        "c.json": BodyKind.JSON,
        "d.csv": BodyKind.TEXT,
    }
    for name in expected:
        fetcher.fetch(FetchRequest(f"{SITE}/{name}", scope=SCOPE))

    found = {
        row.url.rsplit("/", 1)[-1]: row.body_kind for row in ledger.list_attempts()
    }
    assert found == expected


def test_a_pdf_never_reaches_the_ledger_as_a_pdf(
    settings: Settings, ledger: SqliteFetchAttemptStore
) -> None:
    """**已知缺口，带姓名的负责人**：这一层拿到的 PDF 在账上是 ``NONE``。

    这不是判据写错。链条是这样的：``_decode`` 对 ``application/pdf`` **恒**交回
    ``""``（它的 docstring：「PDF 交给 DocumentParser，不在这里当文本解」），
    于是 ``assess_http_response`` 看到的是「非 HTML 且正文为空」 → ``ERROR``，
    于是正文一个字节都不落盘（``_completed`` 只镜像 ``OK`` / ``EMPTY_SHELL``），
    于是 ``_attempt_from`` 的守卫（``body_sha256 and body_bytes > 0``）落 ``NONE``。

    而 ``classify.py`` 里那一支自己的注释写着「非 HTML（PDF / 纯文本 / JSON）：
    **能拿到就是 OK**」—— 也就是说，**实现与它自己的规格相反**：判的是「拿到了
    文本」，而规格说的是「拿到了内容」。所以 ``BodyKind.PDF`` 与
    ``_BODY_KIND_BY_TYPE`` 里的 ``pdf`` 那一格今天都没有生产者。

    ## 为什么在这里钉住而不是在这里修

    修它要改的是 ``assess_http_response`` 的**判据输入**（它今天只收 ``text``，
    收不到「拿到了多少字节」），而那个函数是全仓升级决策的唯一入口，四个调用点
    共用。那是一次分类语义的改动，属于文档解析那一段（``BodyKind`` 的另一半
    —— ``parser_version`` 那一格也归它），不属于「尝试账有没有生产者」这一片。

    ## 这条判据会自己翻红

    一旦有人在正确的那一层修好它（``assess`` 收字节数、``_decode`` 不再伪装成
    空正文），这一条会红 —— 那时要做的**不是**把它改绿，而是把 ``pdf`` 那一格
    加回上面那张表，并把 ``parser_version`` 的生产者一并接上。
    """
    fetcher = make_fetcher(
        settings,
        {f"{SITE}/b.pdf": page("%PDF-1.4 x", url=f"{SITE}/b.pdf",
                               headers={"content-type": "application/pdf"})},
        attempts=ledger_factory(ledger),
    )
    result = fetcher.fetch(FetchRequest(f"{SITE}/b.pdf", scope=SCOPE))

    assert result.outcome is FetchOutcome.ERROR, (
        "服务端明明给了一份 200 的 PDF —— 这个 ERROR 是这一层看不见它造成的，"
        "不是来源的问题"
    )
    (row,) = ledger.list_attempts()
    assert row.suspected_cause is SuspectedCause.CONTENT_UNUSABLE, (
        "诊断把话说对了：这一次的根因是**正文不可用**，而它其实是我们自己"
        "解不出来 —— 这一点在账上看得见，所以缺口不是无声的"
    )
    assert row.body_kind is BodyKind.NONE
    assert row.body_kept is False
    assert row.body_bytes == 10, (
        "字节数**是真的**：服务端那 10 个字节确实回来了。留空或写成 0 会把"
        "「没有正文」与「正文被我们丢掉了」折成同一个读数 —— 而这里丢的是后者"
    )
    assert row.body_sha256 == "", "没有摘要就是没有留存物 —— 两者的刻度必须一致"


def test_the_headers_that_decided_the_verdict_are_kept(
    settings: Settings, ledger: SqliteFetchAttemptStore
) -> None:
    """``Retry-After`` 与挑战头**必须留在账上**。

    这一格回答的是「那天服务端回了什么」，而一份只留状态码的记录答不出
    ``Retry-After: 120`` 这类**决定过处置**的东西 —— 三个月后有人问「当时为什么
    要等两分钟」，答案只有这一格有。
    """
    fetcher = make_fetcher(
        settings,
        {PAGE: page(status=429, headers={"retry-after": "120"})},
        attempts=ledger_factory(ledger),
    )
    fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    (row,) = ledger.list_attempts()
    assert row.response_headers.get("retry-after") == "120"
    assert row.retry_after_s == 120


def test_a_transport_failure_records_no_status_and_no_headers(
    settings: Settings, ledger: SqliteFetchAttemptStore
) -> None:
    """没问到状态码的访问不是一次 ``200``，也不是一次 ``0``。

    ``observed_status`` 留空（``None``）而不是折成一个数 —— ``status_code or 200``
    把三种处境折成同一个读数，而这里是那个错在**账**上的落点。响应头为空字典有确切
    含义：这次没有任何响应头可带，不是「一个白名单头都没有」。
    """
    fetcher = make_fetcher(
        settings, {PAGE: httpx.ConnectTimeout("测试造的连接超时")},
        attempts=ledger_factory(ledger),
    )
    fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    (row,) = ledger.list_attempts()
    assert row.observed_status is None
    assert row.status_unknown is True
    assert row.response_headers == {}
    assert row.error_code, "传输失败的原文要留下 —— 它是分诊的第一句"


def test_a_body_that_cannot_be_decoded_is_not_read_as_a_page(
    settings: Settings, ledger: SqliteFetchAttemptStore, requests_seen: list[str]
) -> None:
    """正文解不开，不是「这一页读了、只是没有联系方式」。

    设计 §11 的承重案例里有一行逐字写着「压缩/编码错误｜记录 unknown/parse_error，
    **不折算 HTTP 200 或零邮箱**」。这一条判的就是那一行。两种折法都会让报告上
    看起来一切正常：把 ``DecodingError`` 折成一个状态码，于是这次访问记成 ``200``；
    或者把解不开的字节当正文交给提取器，于是这个账号记成「没有邮箱」。后者尤其难
    发现 —— 一段 gzip 二进制里当然找不到邮箱，而「找不到」与「读不了」在报告上是
    同一行。

    ## 这个洞能存在，是因为造页面的那个替身**结构上**造不出它

    ``page()`` 用 ``content=`` 造响应，而 ``httpx.Response.__init__`` 会**当场**
    ``read()``。于是「声明 ``content-encoding: gzip`` 再配一段非 gzip 正文」炸的是
    ``page(...)`` 那一行 —— 实测异常从 ``httpx/_models.py`` 里抛出来，测试还没走到
    被测量的那个抓取器。要让解码发生在 ``Client.send`` 里面（真实那一侧就是在这里
    发生的），必须给 ``stream=`` 一个 ``ByteStream``，把读取推迟到发送之后。这一条
    因此**自己造响应**，不复用 ``page()``；这也是它此前一条判据都没有的原因。

    ## 为什么钉 ``DecodingError`` 这个名字

    ``detail`` 里那个类型名由 ``_transport_exchange`` 用 ``type(exc).__name__`` 写出，
    取的是 httpx 的公开类名 ``DecodingError``。这里的断言不是「httpx 会叫这个名字」，
    而是「这一次失败是**解码**那一类，不是超时、不是连接被拒」—— 分类如果用错，
    退避与熔断就记在了一件不会因为等待而变好的事上。zlib 那句英文原文（
    ``incorrect header check``）**不钉**：那是外部库的措辞，会随版本变。
    """
    mislabelled = httpx.Response(
        200,
        headers={
            "content-type": "text/html; charset=utf-8",
            "content-encoding": "gzip",
        },
        stream=httpx.ByteStream(PLAIN_BODY.encode("utf-8")),
        request=httpx.Request("GET", PAGE),
    )
    fetcher = make_fetcher(
        settings, {PAGE: mislabelled}, attempts=ledger_factory(ledger)
    )
    result = fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    # 这一跳真的离开过进程 —— 否则「解码失败」这四个字无从谈起。
    assert PAGE in requests_seen

    # 账上仍然恰好一行，而状态码**没有观测到**：它不该被折成 200。
    (row,) = ledger.list_attempts()
    assert row.observed_status is None
    assert row.status_unknown is True
    assert row.ok is False
    assert row.body_kept is False
    assert row.body_sha256 == "" and row.body_bytes == 0
    assert row.error_code, "解不开的原文要留下 —— 它是分诊的第一句"

    # 回执不是一次读到内容。
    assert result.outcome is FetchOutcome.ERROR
    assert result.status_code is None
    assert result.body_path is None

    # 「记录 unknown」：这一次有诊断，且它不是「没有异常、正常继续」。
    diagnosis = result.diagnosis
    assert diagnosis is not None
    assert diagnosis.cause is not SuspectedCause.NONE
    assert diagnosis.action is not ActionTaken.PROCEEDED
    assert "DecodingError" in diagnosis.detail

    # 盘上没有留下任何一页：没有正文，后面的抽取就**没有输入**，
    # 于是「找不到邮箱」不可能被误读成「这一页没有邮箱」。
    directory = account_page_dir(settings, SCOPE)
    left = sorted(directory.glob("*")) if directory.exists() else []
    assert left == [], f"一次解码失败的响应不该在盘上留下一页：{left}"


# ----------------------------------------------------------------------
# 「取不到就不落镜像」—— 钉**真适配器**那一行，且配一个反面
# ----------------------------------------------------------------------
def test_a_not_found_page_is_not_mirrored_while_a_found_one_is(
    settings: Settings, ledger: SqliteFetchAttemptStore, requests_seen: list[str]
) -> None:
    """``HttpxFetcher`` 里那句 ``outcome in (OK, EMPTY_SHELL)`` 的**生产**那一份。

    ## 为什么这一条非有不可：同一句话此前唯一那个执行器钉的是替身

    ``tests/test_site_crawl.py::test_a_page_that_failed_is_still_a_receipt`` 判的是
    「没取到字节就不该有镜像」，但它走 ``tests/fixtures/http_fakes.py`` 那个替身，
    而且它看的是**回执**那一格（``body_path is None``），不是盘。两次实测
    （MAIN，2026-09-29，逐字记在 ``tests/test_r1_vertical_acceptance.py`` 的对照表
    下方）：

    * 变异**生产侧**那一行（``OK``/``EMPTY_SHELL`` → 恒真）：那条判据**照旧绿** ——
      它根本不经过真适配器；
    * 变异**替身自己**那一行：红在 ``tests/test_site_crawl.py:354``。

    于是它守的是替身重写的那份规则，而生产守卫当时**没有任何判据**。这一条补的就是
    那个洞，它经过真的 ``HttpxFetcher``、并直接读盘。

    ## 两个量具，以及反面那一半为什么非有不可

    「盘上没有」是一个**否定**，而否定可以被一个从不写盘的实现轻易满足。所以下面
    还要判一次**同一台抓取器、同一棵树**下的 200：它必须真的留下至少一页。
    少了后半段，这一条与「镜像功能整个坏掉」在判据上同形。

    ## 它守的那句话

    设计 §5：``NOT_FOUND`` 是「这个来源没了」，**不是**「这个组织消失了」。把 404 的
    正文留在盘上，后面每一层都有机会把它当成一页内容读。
    """
    fetcher = make_fetcher(
        settings,
        {PAGE: page(status=404, body="<html>gone</html>")},
        attempts=ledger_factory(ledger),
    )
    result = fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    assert result.outcome is FetchOutcome.NOT_FOUND
    assert PAGE in requests_seen, (
        "这一跳真的离开过进程 —— 否则「盘上没有」不是镜像判据的功劳，而是这一跳"
        "压根没发生过"
    )
    assert result.body_path is None, "回执那一格先说没有"

    gone = account_page_dir(settings, SCOPE)
    left = sorted(gone.glob("*")) if gone.exists() else []
    assert left == [], f"404 的正文不该被镜像下来：{left}"

    # 反面：同一台抓取器、同一棵树，取到的那些**必须**落下来。
    ok_scope = "account-002"
    found = make_fetcher(settings, {PAGE: page()}, attempts=ledger_factory(ledger))
    found_result = found.fetch(FetchRequest(PAGE, scope=ok_scope))

    assert found_result.outcome is FetchOutcome.OK
    assert found_result.body_path is not None, (
        "取到了却报不出路径 —— 那么上一条的「盘上没有」是空言"
    )
    assert found_result.body_path.is_file(), (
        "回执上给了个路径而盘上没有这个文件 —— 这正是「只比回执」会放过的实现"
    )
    kept = account_page_dir(settings, ok_scope)
    assert sorted(kept.glob("*")), f"取到的那一页没有落盘：{kept}"


# ----------------------------------------------------------------------
# 崩溃点：同一个来源的第三次挫折
# ----------------------------------------------------------------------
def test_the_third_setback_on_one_origin_does_not_crash_the_fetch(
    settings: Settings, ledger: SqliteFetchAttemptStore, requests_seen: list[str]
) -> None:
    """**这一条守的是一个真实的崩溃。**

    ``_exchange`` 把 ``_Exchange`` 交回来（而不是把回执直接返回）的条件是「诊断的
    动作不是 ``DEFERRED_BACKOFF``」，而传输层失败在**同一个来源的第三次**上会打开
    熔断、``record`` 返回 ``CIRCUIT_OPENED`` —— 那也是一次传输层失败，于是
    ``response`` 是 ``None``。此前那里是一句 ``assert response is not None``，
    它把一个**已经量好了**的回执（观测、诊断、处置全在 ``_Exchange`` 里）换成了一次
    ``AssertionError``。

    ## 策略是刻意配的，理由不是「为了让判据好写」

    零退避：缺省退避是 2 秒起，而 ``not_before`` 会让第二次抓取在**等待期未到**上
    被拦下（那是一条合法的路径，但走不到崩溃点）。零退避让「第三次挫折」在当次运行里
    真的发生 —— 也就是说，一个能让这条判据红的实现，在**有**退避的生产策略下同样会崩，
    只是要等到那两秒过去。判据不该依赖一个会随时间变的量。
    """
    policy = AccessPolicy(
        max_attempts_per_fetch=1, backoff_base_s=0.0, backoff_max_s=0.0
    )
    fetcher = make_fetcher(
        settings,
        {PAGE: httpx.ConnectTimeout("测试造的连接超时")},
        policy=policy,
        attempts=ledger_factory(ledger),
    )

    results = [fetcher.fetch(FetchRequest(PAGE, scope=SCOPE)) for _ in range(3)]

    assert len(requests_seen) == 3, "三次抓取各发了一跳（不重试的策略）"
    assert results[-1].diagnosis is not None
    assert results[-1].diagnosis.action is ActionTaken.CIRCUIT_OPENED, (
        "第三次挫折打开了熔断 —— 这一份回执说的正是那件事，而不是一次崩溃"
    )
    assert results[-1].status_code is None, "它仍然是一次没有状态码的失败"

    rows = ledger.list_attempts()
    assert len(rows) == 3, "三次尝试三行账 —— 崩溃那一次就没有账了"
    assert rows[0].action_taken is ActionTaken.CIRCUIT_OPENED, "最新的一行在最前"


# ----------------------------------------------------------------------
# 一个出口都不许忘了带诊断
# ----------------------------------------------------------------------
def test_no_exit_forgets_to_leave_a_diagnosis(
    settings: Settings, ledger: SqliteFetchAttemptStore, requests_seen: list[str]
) -> None:
    """**凡是接了账本的抓取器，每一份回执都必须带诊断。**

    这一条与上面那张出口表**不是**一回事：表判的是「每一个出口记成什么样」，这一条
    判的是「一个都不许漏」。一条新加的出口不会让表变红（表不知道有几个出口），但它会
    让这一条红 —— 前提是它被加进下面这张局面清单。

    浏览器那一层有它自己的同形判据（``tests/test_browser_fetch_attempts.py``），
    两处都判「一个都不许漏」，而不是只判一处：一条出口在这一份清单里缺席，
    在另一份里往往也缺席 —— 而两份清单**都不红**的样子与「都覆盖了」一模一样。

    ## 为什么它是硬的

    ``_attempt_from`` 对 ``diagnosis is None`` 抛 ``StoreError``：一句静默回退会写成
    ``cause=none, action=proceeded``，也就是「这一次没有任何异常」。所以「忘了带诊断」
    在**开了记账**的路径上是一次显式失败。**没开记账的路径上仍然无人知道** ——
    那是这一条守不到的地方，也正是它写成「凡是**接了账本**的抓取器」的理由：那句话
    不是限定条件，是它守着的边界。两个抓取器今天都接了（见 ``default_attempt_store``
    的 docstring），但一个**新**抓取器可以先不接 —— 那时它连这一条都不进。

    ## 清单分成两截，因为正常的那个出口是**合法**的

    一个 200 的正常页面收成 ``cause=none, action=proceeded`` —— 那是一份**有效**的
    诊断，不是一句搪塞。所以它进第一截（必须带诊断）而不进第二截（不许是「一切
    正常」）。把两截合成一条断言，要么会漏掉正常出口那一格，要么就得把那一句
    「没有一个是正常局面」删掉 —— 而后者正是这一条唯一在判的事。
    """
    #: 收成一份「一切正常」的诊断的两个局面。**跟到底的一次重定向也在这里**，
    #: 不是因为重定向本身正常，而是因为回执反映的是**最后一跳**：中间那一跳的诊断
    #: （``cause=none``、人话是「这是一次重定向，来源是通的」）没有单独的出口。
    normal = [
        ({PAGE: page()}, PAGE, None),
        ({PAGE: redirect(PAGE, SECOND), SECOND: page(url=SECOND)}, PAGE, None),
    ]
    abnormal = [
        ({PAGE: page(status=403)}, PAGE, None),
        ({PAGE: page(status=429, headers={"retry-after": "120"})}, PAGE, None),
        ({PAGE: page(body=CHALLENGE_BODY)}, PAGE, None),
        ({PAGE: page(status=302)}, PAGE, None),
        ({PAGE: page(status=500)}, PAGE, None),
        ({PAGE: page(status=404)}, PAGE, None),
        ({}, PAGE, FailingResolver(DnsFailureKind.TIMEOUT)),
        ({}, PAGE, FailingResolver(DnsFailureKind.NONEXISTENT)),
        ({}, PAGE, FailingResolver(DnsFailureKind.FAILURE)),
        ({}, LOOPBACK_URL, None),
        ({PAGE: page()}, PAGE, FakeResolver("10.0.0.7")),
    ]

    for routes, url, resolver in [*normal, *abnormal]:
        fetcher = make_fetcher(
            settings, routes, resolver=resolver, attempts=ledger_factory(ledger)
        )
        result = fetcher.fetch(FetchRequest(url, scope=SCOPE))
        assert result.diagnosis is not None, (
            f"{url} 的这一份回执没有诊断 —— 收账时它会抛 ``StoreError``，"
            "而一份不带诊断的回执在报告上与「一切正常」同形"
        )

    for routes, url, resolver in abnormal:
        fetcher = make_fetcher(
            settings, routes, resolver=resolver, attempts=ledger_factory(ledger)
        )
        result = fetcher.fetch(FetchRequest(url, scope=SCOPE))
        assert not (
            result.diagnosis is not None
            and result.diagnosis.cause is SuspectedCause.NONE
            and result.diagnosis.action is ActionTaken.PROCEEDED
        ), (
            f"{url} 的回执写着「没有任何异常，正常继续」—— 而第二截清单里"
            "没有一个是正常局面"
        )


def test_every_refusal_reason_has_a_decided_disposition() -> None:
    """九个拒绝理由各自去哪一格，**逐条钉住**。

    这一条不是重复 ``fetch_attempts.refusal_diagnosis`` 的实现：它判的是那张表**是一个分区**、
    而且没有成员落在「没想过」上。新加一个 ``TargetRefusal`` 成员时它会红 ——
    「按最保守的 ``UNKNOWN`` 处置」对**运行时**是诚实的答案，对**判据**是撒谎：
    它会让一个没人想过的新拒绝理由看起来像「已经考虑过了」。
    """
    unsafe, unroutable = set(), set()
    for refusal in TargetRefusal:
        cause, action = refusal_diagnosis(refusal)
        if cause is SuspectedCause.UNSAFE_TARGET:
            unsafe.add(refusal)
            assert action is ActionTaken.REFUSED_UNSAFE_TARGET
        else:
            unroutable.add(refusal)
            assert action is ActionTaken.STOPPED_METHOD, (
                "「不许去那里」与「这条链走不通」的处置不同：前者要记下这个目标并"
                "改来源，后者要修的是这一条链"
            )

    assert unsafe == {
        TargetRefusal.SCHEME,
        TargetRefusal.USERINFO,
        TargetRefusal.NO_HOST,
        TargetRefusal.PRIVATE_TARGET,
        TargetRefusal.RESERVED_HOST,
        TargetRefusal.NUMERIC_HOST,
        TargetRefusal.RESOLVES_TO_PRIVATE,
    }
    assert unroutable == {TargetRefusal.REDIRECT_LIMIT, TargetRefusal.REDIRECT_WITHOUT_LOCATION}
    assert unsafe | unroutable == set(TargetRefusal), (
        "九个成员必须各有去处 —— 这张表不许有别的落脚点"
    )


def test_a_refusal_is_recorded_as_unsafe_target(
    settings: Settings, ledger: SqliteFetchAttemptStore, requests_seen: list[str]
) -> None:
    """把上面那条纯函数判据接回到真实的抓取路径上。

    纯函数对了不等于接对了：``_refused`` 有一格 ``block_reason`` 恒为
    ``UNSAFE_TARGET``（那是**这一类结果**的名字），要是收账时读的是它而不是诊断，
    九个理由又会被折回同一格 —— 而上面那条判据照样全绿。
    """
    fetcher = make_fetcher(settings, {}, attempts=ledger_factory(ledger))
    result = fetcher.fetch(FetchRequest(LOOPBACK_URL, scope=SCOPE))

    assert requests_seen == [], "地址判据拒绝的抓取一个请求都不该发"
    assert result.block_reason is BlockReason.UNSAFE_TARGET
    (row,) = ledger.list_attempts()
    assert row.suspected_cause is SuspectedCause.UNSAFE_TARGET
    assert row.action_taken is ActionTaken.REFUSED_UNSAFE_TARGET


# ----------------------------------------------------------------------
# 收口点：没有诊断的回执不许被记成「一切正常」
# ----------------------------------------------------------------------
class _Undiagnosed(HttpxFetcher):
    """一个交回**没有诊断**的回执的抓取器。"""

    def _run(self, request: FetchRequest) -> FetchResult:  # noqa: D102
        return FetchResult(
            request=request, outcome=FetchOutcome.OK, tier=FetchTier.HTTP
        )


class _Exploding(HttpxFetcher):
    """一个在抓取途中抛异常（而不是返回回执）的抓取器。"""

    def _run(self, request: FetchRequest) -> FetchResult:  # noqa: D102
        raise RuntimeError("抓取途中炸了")


def test_a_diagnosis_free_result_is_refused_rather_than_recorded(
    settings: Settings, ledger: SqliteFetchAttemptStore
) -> None:
    """收口点**当场拒绝**一份没有诊断的回执，而调用方拿不到那份回执。

    两件事一起判：抛的是 ``StoreError``（而不是一个静默的回退），而且**账上没有行**。
    一个「记一行 ``cause=none, action=proceeded`` 再返回」的实现会让调用方毫无察觉，
    而报告上那一次抓取读起来像一切正常。
    """
    fetcher = _Undiagnosed(settings, attempts=ledger_factory(ledger))
    with pytest.raises(StoreError, match="诊断"):
        fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    assert ledger.list_attempts() == []


def test_an_exception_on_the_way_through_leaves_no_row(
    settings: Settings, ledger: SqliteFetchAttemptStore
) -> None:
    """``fetch`` 中途抛异常时**不落账**。**这是一条已知边界，不是一条好消息。**

    账记在 ``fetch`` 的返回路径上，所以一份没能算出来的回执（进程被杀、内存耗尽、
    抓取层自己抛）在账上没有行。换句话说：**这张账证明的是「这些尝试发生且有了结论」，
    不是「这些尝试发生过」** —— 一次在途崩溃不留痕。

    写下来是因为「账上没有」会被读成「没有发生」，而这两句话在这张表上确实不同。
    要覆盖它得再引入一次「开始记账 + 收尾」的两段式写入，而那会让这张表上出现
    「没有结论的行」这一新状态；本片**刻意没有**做，理由记在这里。
    """
    fetcher = _Exploding(settings, attempts=ledger_factory(ledger))
    with pytest.raises(RuntimeError, match="抓取途中炸了"):
        fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    assert ledger.list_attempts() == []


# ----------------------------------------------------------------------
# 组合根：账本接在这里，而且只有这里
# ----------------------------------------------------------------------
def test_a_directly_built_fetcher_does_not_create_a_database(
    settings: Settings, requests_seen: list[str]
) -> None:
    """``HttpxFetcher(settings)`` —— **不记账**，而且**不建库**。

    「不记账」是必需的一格（单测与离线重放要它如此），但这一条判的是更硬的那一半：
    **盘上什么都不能多出来**。``SqliteFetchAttemptStore`` 的构造会 ``ensure_schema``，
    也就是会建库 —— 所以这里必须是一个**工厂**，而一个直接构造 store 的实现在这条
    判据上会把库建出来，尽管它的抓取行为与缺省完全一样。
    """
    fetcher = make_fetcher(settings, {PAGE: page()})
    fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    assert requests_seen == [PAGE], "抓取本身照常发生"
    assert not settings.database_path.is_file(), (
        "一个直接构造的抓取器在盘上留下了库 —— 记账的工厂被提前调用了"
    )


def test_the_composition_root_wires_the_ledger_onto_the_real_path(
    settings: Settings, requests_seen: list[str]
) -> None:
    """组合根产出的那个抓取器**真的**把账写在 ``settings`` 指的库上。

    只换掉传输层与解析器，其余全是生产接线：这一条要判的是**线接没接上**，
    而不是 ``httpx`` 能不能发请求。用 ``default_http(settings)`` 直接对真实站点发
    是本仓的一条硬边界（测试不碰真实网络），所以这里注入的是替身 —— 这一点写出来，
    免得下一次有人把这一条读成「组合根的真实网络路径已被验证」。
    """
    fetcher = default_http(settings)
    fetcher._client = client({PAGE: page()})  # noqa: SLF001 - 见上面那段说明
    fetcher._resolver = FakeResolver()  # noqa: SLF001
    fetcher.fetch(FetchRequest(PAGE, scope=SCOPE))

    rows = SqliteFetchAttemptStore(settings.database_path).list_attempts()
    assert len(rows) == 1, "组合根接上的那个账本没有拿到这一行"
    assert rows[0].scope == SCOPE


def test_the_acquisition_entry_point_passes_the_ledger_down(settings: Settings) -> None:
    """§10 那唯一的统一入口也必须拿到账本 —— 生产路径走的是它，不是直连抓取器。

    判据与 ``test_http_access_governance.py`` 里那条 governor 的走同一个形状：
    **显式注入的那一个必须真的被用上**。传了 ``http`` 时 ``attempts`` 被忽略
    （那一格由 ``http`` 自己带着），所以这一条用的是缺省 ``http``。
    """
    sentinel = SqliteFetchAttemptStore(settings.database_path)
    acquisition = default_acquisition(settings, allow_browser=False, attempts=ledger_factory(sentinel))

    assert acquisition.http._attempts is not None, (  # noqa: SLF001
        "统一入口产出的抓取器没有账本工厂 —— 四次生产调用点都是它，于是账会空"
    )
    assert acquisition.http._attempts() is sentinel, (  # noqa: SLF001
        "传下去的不是那一个"
    )


def test_the_default_ledger_factory_is_lazy_and_points_at_the_settings_db(
    settings: Settings,
) -> None:
    """``default_attempt_store`` 交回的是一个**工厂**，不是一本已经建好的账。

    「惰性」在这里有两个都承重的后果：一次只读的调用不该在盘上凭空建出一个库来，
    以及 ``sqlite3`` 的连接不跨线程 —— 一个模块级共享的 store 会在第二个线程上
    报错，或者更糟：静默地绑到**第一份** settings 的库上。
    """
    factory = default_attempt_store(settings)
    assert not settings.database_path.is_file(), "取工厂这一步就不该建库"

    store = factory()
    assert isinstance(store, SqliteFetchAttemptStore)
    assert settings.database_path.is_file(), "真的要用的时候它必须建出来"

    assert factory() is not store, "每调一次给一个新的连接持有者，而不是同一个实例"

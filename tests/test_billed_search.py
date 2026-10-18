"""一次花钱的检索：三段式次序与「付过账的不重发」，**在抽取出来的那一层**测。

## 为什么这个文件与 ``test_pipeline_budget.py`` 不重复

那一份问的是「§8 的线有没有接上 pipeline」（对着跑起来的 pipeline 验，最后落到
账本的行上）。这一份问的是另一件事：**规则本身**在第二个调用方那里会不会被抄错。

pipeline 那侧的红灯只能证明「pipeline 这条路是通的」。服务面的 ``search_web``
走的是同一个 ``BilledSearch``，但它有自己的一次「忘了传账本」的机会 —— 而那
一次不会让 pipeline 的任何一条判据变红。所以这里直接对着 ``BilledSearch`` 测，
用 ``InMemoryBudgetLedger``：零网络、零磁盘、零 profile。

## 幂等键的**字符串**是一条跨版本的判据

``idempotency_key`` 除了拼接没有别的语义，但它决定了「这条查询付过账没有」。
改一个字母，昨天那些已经是 ``spent`` 的预留今天就再也匹配不上 —— 重发的钱花了
而账本上不记（账本开始**少记**），或者更隐蔽：账本上多出一条重复的行。两种都
不会报错。所以这里钉住字面量，而不是钉住「两个调用方算得一样」—— 后者在同仓
同版本里恒真，量不到任何东西。
"""

from __future__ import annotations

import pytest

from marketops.adapters.memory import InMemoryBudgetLedger
from marketops.application.billed_search import BilledSearch, idempotency_key
from marketops.application.budget import RunBudget
from marketops.domain.enums import BudgetLine, QueryOutcome
from marketops.domain.errors import BudgetExhaustedError, SearchError
from marketops.ports.search import SearchHit, SearchProvider, SearchQuery, SearchResult

SCOPE = "uk-acme-natural-grass:acme-001"
CEILING = 3


class RecordingSearch(SearchProvider):
    """记下被发出去的查询；可配置成在第 N 次调用上抛错。"""

    name = "recording"

    def __init__(self, *, fail_on: int | None = None, hits: int = 1) -> None:
        self.sent: list[str] = []
        self.limits: list[int] = []
        self._fail_on = fail_on
        self._hits = hits

    def search(self, query: SearchQuery, *, limit: int = 10) -> SearchResult:
        self.sent.append(query.text)
        self.limits.append(limit)
        if self._fail_on is not None and len(self.sent) == self._fail_on:
            raise SearchError("模拟的连接中断")
        return SearchResult(
            query=query,
            hits=tuple(
                SearchHit(url=f"https://example.co.uk/{index}", title=query.text, rank=index)
                for index in range(self._hits)
            ),
            api_calls=1,
        )


#: 单元测试用的市场。写法与 ``profiles/*/profile.yaml`` 的声明一致（国家两位大写、
#: 语言是主语言子标签）—— ``SearchQuery`` 只收这两种写法。这一组用例问的是账本
#: 的三段式，市场取哪一国不是被测量的量，所以在这里填一个定值。
MARKET = {"country": "GB", "language": "en"}


def _query(text: str = "acme rugby club") -> SearchQuery:
    return SearchQuery(text=text, scope="acme-001", **MARKET)


def _billed(*, ceiling: int = CEILING, fail_on: int | None = None, hits: int = 1):
    ledger = InMemoryBudgetLedger()
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, ceiling, source="test:fixture")
    search = RecordingSearch(fail_on=fail_on, hits=hits)
    return BilledSearch(search=search, budget=RunBudget(ledger=ledger, scope=SCOPE)), search, ledger


# ----------------------------------------------------------------------
# 三段式：reserve → 调用 → spend
# ----------------------------------------------------------------------
def test_a_sent_query_is_reserved_before_it_is_sent_and_spent_after() -> None:
    """起飞之前就已经占着额度，落地之后才算花掉。

    次序**分工**而不是次序本身：本判据只能看结果（发出去了、账上花了 1）。
    「预留发生在调用之前」这件事的证据是另一条 —— 上限约束的是「在途 + 已花」，
    见 ``test_the_reservation_is_what_stops_the_run``（pipeline 那侧，那里有一个
    不预留的正对照）。
    """
    billed, search, ledger = _billed()

    receipt = billed.query(_query())

    assert receipt.outcome is QueryOutcome.SENT
    assert search.sent == ["acme rugby club"]
    usage = ledger.usage(SCOPE)[0]
    assert (usage.spent, usage.reserved) == (1, 0)
    assert ledger.open_reservations(SCOPE) == ()


def test_the_receipt_carries_the_reservation_it_settled() -> None:
    """回执带着那条预留 —— 没有它，「这次调用是哪一行账」只能靠重算 id。"""
    billed, _, _ = _billed()

    receipt = billed.query(_query())

    assert receipt.reservation is not None
    assert receipt.reservation.state.value == "spent"
    assert receipt.reservation.idempotency_key == "search:GB/en/acme rugby club"


def test_the_limit_is_passed_through_to_the_provider() -> None:
    """``limit`` 是本层收下、原样交给提供方的 —— 本层不自己截断结果。"""
    billed, search, _ = _billed()

    billed.query(_query(), limit=5)

    assert search.limits == [5]


# ----------------------------------------------------------------------
# 不重发：付过账的查询
# ----------------------------------------------------------------------
def test_a_query_already_paid_for_is_not_sent_again() -> None:
    """第二次拿到的是上次那条预留 —— 它已经结算过，所以**不重发**。

    正控在同一条判据里：换一个查询文本，同一个 provider 立刻被调用。
    """
    billed, search, _ = _billed()

    first = billed.query(_query())
    second = billed.query(_query())

    assert first.outcome is QueryOutcome.SENT
    assert second.outcome is QueryOutcome.SKIPPED
    assert search.sent == ["acme rugby club"], "付过账的查询被重发了"

    billed.query(_query("another query entirely"))
    assert search.sent == ["acme rugby club", "another query entirely"]


def test_a_skipped_query_costs_nothing_extra() -> None:
    """跳过不是一次「便宜的发送」，是**没有发送**。"""
    billed, _, ledger = _billed()

    billed.query(_query())
    billed.query(_query())

    usage = ledger.usage(SCOPE)[0]
    assert (usage.spent, usage.reserved) == (1, 0)


def test_a_skipped_receipt_still_carries_the_reservation_that_stopped_it() -> None:
    """跳过也有回执与预留：调用方据此能说清「为什么这次没有新结果」。"""
    billed, _, _ = _billed()
    billed.query(_query())

    receipt = billed.query(_query())

    assert receipt.reservation is not None
    assert receipt.reservation.settles_itself is True


# ----------------------------------------------------------------------
# 失败：没有第三个动作
# ----------------------------------------------------------------------
def test_a_failed_query_keeps_holding_the_budget() -> None:
    """调用抛错之后**不退款**、不重试、不改预留（§8：超时后不知道对方计费没有）。"""
    billed, search, ledger = _billed(fail_on=1)

    receipt = billed.query(_query())

    assert receipt.outcome is QueryOutcome.FAILED
    assert isinstance(receipt.error, SearchError)
    usage = ledger.usage(SCOPE)[0]
    assert (usage.spent, usage.reserved) == (0, 1), "失败的调用把额度还回去了"
    assert ledger.remaining(SCOPE, BudgetLine.SEARCH) == CEILING - 1
    assert len(ledger.open_reservations(SCOPE)) == 1, "那笔悬着的额度要能被对账找出来"
    assert search.sent == ["acme rugby club"]


def test_a_failed_query_is_not_retried_within_the_same_call() -> None:
    """失败就是这一次的结果 —— 重试会在没人看见的地方多发一次计费请求。"""
    billed, search, _ = _billed(fail_on=1)

    billed.query(_query())

    assert len(search.sent) == 1


def test_a_failed_receipt_has_no_result_to_read() -> None:
    """失败时 ``result`` 是 ``None``，而 ``api_calls`` 读成 0 —— 不编一个数。"""
    billed, _, _ = _billed(fail_on=1)

    receipt = billed.query(_query())

    assert receipt.result is None
    assert receipt.api_calls == 0
    assert receipt.hits == ()
    assert receipt.sent is False


# ----------------------------------------------------------------------
# 「发出去了吗」与「用量知道吗」：两个问题，必须分得开
# ----------------------------------------------------------------------
def test_a_failed_query_reports_dispatched_with_unknown_usage() -> None:
    """发出去了、但失败的那一发：**发出去过**，而**用量不知道**。

    这两句是压在同一个 ``False``（``sent``）下面的两件事：提供方被进入过
    （``RecordingSearch.sent`` 里留着那一条），所以它**不是一个「没发」的
    回执**；而 ``result is None``，所以它花了多少谁也说不出来 —— 既不等于 0，
    也不等于 ``api_calls``。

    钉住 ``sent is False`` 而**不**追问这两个问题，正是那个缺口：
    「这一发没发出去」与「这一发发了、花了多少不知道」在报告上同色。
    """
    billed, search, _ = _billed(fail_on=1)

    receipt = billed.query(_query())

    assert search.sent == ["acme rugby club"], "这一发没有进入提供方 —— 判据的前提不成立"
    assert receipt.outcome is QueryOutcome.FAILED
    assert receipt.dispatched is True, "已经派发出去的调用被读成「没有发出去」"
    assert receipt.usage_known is False, "一次没有结果的调用被读成「用量已知」"
    assert receipt.usage_unknown is True
    assert receipt.sent is False  # 「发出去了、而用量已知」为假 —— 见 sent 的 docstring


def _answers(receipt) -> tuple[QueryOutcome, bool, bool, bool]:  # noqa: ANN001
    """四种终态在这两个问题上的读数：``(终态, 发出去了吗, 用量知道吗, 去向不明吗)``。"""
    return (receipt.outcome, receipt.dispatched, receipt.usage_known, receipt.usage_unknown)


def test_the_two_questions_separate_the_four_outcomes() -> None:
    """四种终态在这两个问题上的**逐格**读数。

    ``SENT`` 与 ``FAILED`` 在第一个问题上同答「发出去了」，在第二个问题上相反 ——
    这正是 ``sent`` 一个布尔值表达不了的那一格。另外三种「没发」在第二个问题上的
    答案也不是同一个理由：``SKIPPED`` / ``EXHAUSTED`` 的用量**确切地**是 0（压根
    没有派发），不是「不知道」。把它们一律读成「未知」，去向不明与没花钱就会一起
    变成同一句「不知道」。
    """
    billed, _, _ = _billed()  # ceiling=3

    sent = billed.query(_query("first"))
    skipped = billed.query(_query("first"))

    assert _answers(sent) == (QueryOutcome.SENT, True, True, False)
    assert _answers(skipped) == (QueryOutcome.SKIPPED, False, True, False)
    assert _answers(_billed(fail_on=1)[0].query(_query("boom"))) == (
        QueryOutcome.FAILED,
        True,
        False,
        True,
    )
    assert _answers(_billed(ceiling=0)[0].query(_query("never"))) == (
        QueryOutcome.EXHAUSTED,
        False,
        True,
        False,
    )


# ----------------------------------------------------------------------
# 上限：撞穿了就没有这一次调用
# ----------------------------------------------------------------------
def test_exhaustion_is_its_own_outcome_and_nothing_was_sent() -> None:
    """额度撞穿既不是失败也不是跳过：**这次调用根本没发生**。

    三种「没发送」合成一种，报告上「预算耗尽」与「这条以前跑过」就是同一种
    颜色 —— 而前者是一次需要处置的运行中止。
    """
    billed, search, ledger = _billed(ceiling=1)
    billed.query(_query("first"))

    receipt = billed.query(_query("second"))

    assert receipt.outcome is QueryOutcome.EXHAUSTED
    assert isinstance(receipt.error, BudgetExhaustedError)
    assert receipt.reservation is None, "reserve 没成立，就没有预留可以回执"
    assert search.sent == ["first"]
    assert ledger.usage(SCOPE)[0].spent == 1


def test_exhaustion_is_not_a_refusal_of_the_whole_run() -> None:
    """回执而不是异常：调用方拿到的是一个**结果**，停不停由它决定。

    本层抛异常会让「上一条成功、下一条超限」这件事在调用方那里退化成一次普通的
    调用失败 —— 而它的正确处置是停下来并报告，不是继续把剩下的查询发完。
    """
    billed, _, _ = _billed(ceiling=0)

    receipt = billed.query(_query())

    assert receipt.outcome is QueryOutcome.EXHAUSTED


# ----------------------------------------------------------------------
# 身份：幂等键
# ----------------------------------------------------------------------
def test_the_idempotency_key_is_the_literal_both_callers_depend_on() -> None:
    """``search:<国家>/<语言>/<查询文本>`` —— 这个字符串是**跨版本**的身份。

    改它，昨天那些已经是 ``spent`` 的预留今天就匹配不上：重发的钱花了而账本不记，
    或者账本上多出一条重复的行。两种都不会报错，所以这里钉字面量。

    **它改过一次**（2026-09-28，B15-f）：之前是 ``search:<查询文本>``，而那让
    「同一条检索式在两个国家下」算成一条预留 —— 第二次被读成「付过账了」，而它
    从未跑过。市场进身份是**故意的**，代价是那一天之前写下的预留全部匹配不上。
    这在试运行之前是有意接受的（没有生产账本，``READY_FOR_MEMBER_A_TRIAL`` 尚未
    成立）；**试运行开始之后再改这个格式，代价就不是零了** —— 那时要的是先把旧键
    读出来重算，而不是直接换。
    """
    assert idempotency_key(_query("hello")) == "search:GB/en/hello"


def test_two_accounts_do_not_collide_on_the_same_query_text() -> None:
    """同名查询在两个 scope 下是两条预留 —— 隔离靠 scope，不靠键。"""
    ledger = InMemoryBudgetLedger()
    other = "uk-acme-natural-grass:other-002"
    for scope in (SCOPE, other):
        ledger.set_ceiling(scope, BudgetLine.SEARCH, 1, source="test:fixture")
    search = RecordingSearch()

    for scope in (SCOPE, other):
        BilledSearch(search=search, budget=RunBudget(ledger=ledger, scope=scope)).query(_query())

    assert search.sent == ["acme rugby club", "acme rugby club"]
    assert ledger.usage(SCOPE)[0].spent == 1
    assert ledger.usage(other)[0].spent == 1


@pytest.mark.parametrize(
    "text", ["a", "a b", "site:example.co.uk contact", "Cricket Club Devon"]
)
def test_the_key_is_stable_for_any_query_text(text: str) -> None:
    """键只拼身份，不做规范化 —— 规范化过就会有两句话撞成一条预留。

    检索式那一部分逐字进键（含 ``site:`` 里的冒号与点）：拿它去 slug 或小写化，
    ``a b`` 与 ``a-b`` 就会撞成一条。
    """
    assert idempotency_key(_query(text)) == f"search:GB/en/{text}"

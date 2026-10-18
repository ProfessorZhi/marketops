"""一次**花钱**的检索：三段式记账 + 「付过账的不重发」，合成一个动作（§8）。

## 它取代了什么

这段逻辑原来写在 ``application/pipeline.py::_discover`` 的循环体里。今天有了第二个
调用方（服务面的 ``search_web``），而它要**逐字**遵守同一套规则：

* ``reserve`` → 调用 → ``spend`` 三段，中间**没有任何** ``except``；
* 预留已经结算过 ⇒ **不重发**，只记一个「跳过」；
* 失败之后**什么都不做** —— 那段预留停在 ``RESERVED`` 继续扣着额度。

抄第二遍的代价不是几行代码，而是其中两条规则**抄错了不会报错**：

* 幂等键换一个写法（``f"search:{query.text}"`` 而不是 ``f"search:{query.identity}"``），
  「付过账的不重发」当场失效，而症状是账单变多、报告完全正常 —— 反过来，把身份
  少算一格（漏掉市场）会让**两条不同的预决算成一条**，症状是账单变少；
* 失败分支顺手 ``refund`` 一下，看起来更「干净」—— 而 §8 要的正是**不做**这件事，
  因为超时之后你并不知道对方是否已经计费。

所以键的推导（:func:`idempotency_key`）与三段次序都住在这里，两个调用方都从
这里过。判据在 ``tests/test_billed_search.py``，pipeline 那一侧由
``tests/test_pipeline_budget.py`` 原有的十二条继续钉住 —— 抽取是重构，
那些判据一个字都不该改。

## 为什么它是 ``application/`` 里的具体类，不是第四个端口

与 ``DocumentAcquisition`` 同一个理由（``ports/acquisition.py`` 的文件头）：
**等第二个实现出现再提取**。今天这件事只有一种正确做法，为它写 ABC 产出的是
一个永远只有一个子类的抽象。这里有两个**调用方**，不是两个**实现** —— 那是
抽取的充分理由，不是抽象的理由。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.budget import Reservation
from ..domain.enums import QueryOutcome
from ..domain.errors import BudgetExhaustedError, MarketOpsError
from ..ports.search import SearchHit, SearchProvider, SearchQuery, SearchResult
from .budget import RunBudget

__all__ = ["BilledSearch", "QueryReceipt", "idempotency_key"]


def idempotency_key(query: SearchQuery) -> str:
    """一次检索的幂等键：``search:<国家>/<语言>/<查询文本>``。

    **身份是 ``query.identity``，不是查询文本。** 这两者在本函数被写下来的时候
    是一样的；市场两格接进来之后它们不再一样 —— 同一条检索式在 GB/en 与 FR/fr
    下是**两次不同的动作**（返回的是两批页面），合成一条键之后第二次会被读成
    ``EXISTS``/``SKIPPED``（「付过账了」），而它从未跑过。报告上看起来只是
    「这一次很快」。

    这一点必须由本函数而不是调用方决定：键的作用域是账本的 ``scope``
    （``<profile>:<账号>``），所以「同一条查询永远算出同一条预留」在两个调用方
    之间成立，只取决于这个格式有没有被抄成第二种写法。幂等键一旦分家，重复计费
    与「跳过」都会静默消失 —— 见模块 docstring。
    """
    return f"search:{query.identity}"


@dataclass(frozen=True)
class QueryReceipt:
    """一次检索的回执。四种终态，**四种都要能分辨**。

    ``outcome`` 是一个枚举而不是 ``sent: bool``：「没发送」有**三**个不同的原因，
    而它们的处置完全不同 —— 付过账了（正常，重跑时会出现）、额度用尽（该停下
    并报告）、调用失败（该继续下一条）。压成一个布尔值，报告上「预算耗尽」与
    「这条查询以前跑过」就会是同一种颜色。

    ``error`` 是**原始异常对象**，不是一句拼好的话：两个调用方的读者不同
    （pipeline 写进报告的错误行、服务面要一条可编程的错误），所以拼句子这件事
    留在各自的层里。``reservation`` 在 ``EXHAUSTED`` 时是 ``None``（那次预留
    根本没成立），其余三种都带着它 —— 失败的查询靠它才能被 ``open_reservations``
    找出来对账。

    ``outcome`` 回答「这一次的终态是什么」，而报告面还要问两个**不能由它直接
    回答**的问题 —— 「这一发发出去了吗」（:attr:`dispatched`）与「这一发的用量
    知道吗」（:attr:`usage_known`）。``SENT`` 与 ``FAILED`` 在第一个问题上是同一个
    答案，在第二个问题上相反；把这一对压成一个布尔值（``sent``），一次**已经发
    出去、而花了多少没人知道**的检索就会在报告上与「压根没发」同形。
    """

    query: SearchQuery
    outcome: QueryOutcome
    reservation: Reservation | None = None
    result: SearchResult | None = None
    error: MarketOpsError | None = None

    @property
    def sent(self) -> bool:
        """这一发发出去了**并且**它的用量已知 —— 两个问题都答「是」。

        它是 :attr:`dispatched` 与 :attr:`usage_known` 的**合取**，两个分量各自
        可问：``FAILED`` 的那一发也发出去了，而这里同样读成 ``False``，所以
        ``sent is False`` **不是**「没发出去」的证据 —— 那是 ``SKIPPED`` 与
        ``EXHAUSTED``。问「发出去没有」请读 :attr:`dispatched`。
        """
        return self.dispatched and self.usage_known

    @property
    def dispatched(self) -> bool:
        """这一发**发出去了**吗 —— 调用真的进到了提供方里。

        ``SENT`` 与 ``FAILED`` 都算：失败的那一发请求已经派发出去，只是回来的
        是一个异常。``SKIPPED``（付过账，本次没重发）与 ``EXHAUSTED``（上限撞穿，
        调用根本没发生）才是「没有发出去」。

        这一格与 :attr:`usage_known` 是一对，必须一起读：只看 :attr:`api_calls`
        的话，失败那一发读出来的 0 会被当成「没花钱」。
        """
        return self.outcome in (QueryOutcome.SENT, QueryOutcome.FAILED)

    @property
    def usage_known(self) -> bool:
        """这一发的用量**已知**吗。

        只有 ``FAILED`` 答 ``False``：请求派出去了，回来的是异常，所以这一发到底
        花掉多少（发了几跳、对方计费没有）**没有任何地方知道** —— §8 正因此既不
        退款也不重试。另外三种答 ``True``，而理由要对着 :attr:`dispatched` 读：
        ``SENT`` 是结果里带着 ``api_calls``；``SKIPPED`` / ``EXHAUSTED`` 是压根
        没有派发，这一发的用量因此**确切地**是 0，不是「不知道」。

        写成「有结果、或者压根没派发」而不是「终态是 ``SENT``」：前一种写法在
        终态集合将来多出一个「派出去了、没有结果」的成员时答 ``False``（未知），
        后一种会答 ``True``（已知）—— 那等于替一次没人知道结果的调用编一个数。

        这一格是 ``False`` 时，:attr:`api_calls` 读出来的那个 0 **不是用量**，
        它只是「没有结果可以读」。
        """
        return self.result is not None or not self.dispatched

    @property
    def usage_unknown(self) -> bool:
        """**发出去了、而用量不知道** —— 报告面要记的那一格。

        :attr:`dispatched` 与 :attr:`usage_known` 的反面之合取：这一发不是在报告上
        归零的那一发，也不是一个编出来的数，而是一次**去向不明**的调用。
        """
        return self.dispatched and not self.usage_known

    @property
    def api_calls(self) -> int:
        """真实发出的请求数。**不**在跳过 / 失败时编一个 0 以外的数。"""
        return self.result.api_calls if self.result is not None else 0

    @property
    def hits(self) -> tuple[SearchHit, ...]:
        return self.result.hits if self.result is not None else ()


class BilledSearch:
    """把 ``SearchProvider`` 与一个已经播种好的 ``RunBudget`` 绑在一起。

    Args:
        search: 检索提供方（端口，不是实现）。
        budget: 某**一个** ``scope`` 的额度。scope 是账号而不是运行，理由见
            ``application/budget.py``；本类不构造它，因为播种上限是一次显式的、
            会失败关闭的动作，藏在构造函数里会让「这次运行没有上限」变得看不见。
    """

    def __init__(self, *, search: SearchProvider, budget: RunBudget) -> None:
        self._search = search
        self._budget = budget

    def query(self, query: SearchQuery, *, limit: int = 10) -> QueryReceipt:
        """走完三段，返回回执。**本方法不抛**（除编程错误外）。

        异常在这里被读成结果，因为三种异常恰好对应三种必须分开的终态。把它们
        抛给调用方，调用方要么重复一遍这个分类，要么把「额度用尽」也当成一次
        普通的调用失败 —— 后者会让一次该停下来的运行继续把剩下的查询发完。
        """
        try:
            reservation = self._budget.reserve(idempotency_key=idempotency_key(query))
        except BudgetExhaustedError as exc:
            # 这一条**没有**预留：reserve 没成立。所以回执里没有 reservation，
            # 也正因为它，调用方不能靠「有没有预留」来判断「有没有花钱」。
            return QueryReceipt(query=query, outcome=QueryOutcome.EXHAUSTED, error=exc)

        if reservation.settles_itself:
            # 这条查询在更早的一次运行里已经付过账。重发等于花了钱而账本不记；
            # 静默跳过等于让「检索过、没东西」与「压根没检索」在报告上同形。
            # 所以：不重发，但留下一个能被看见的「跳过」。
            return QueryReceipt(query=query, outcome=QueryOutcome.SKIPPED, reservation=reservation)

        try:
            result = self._search.search(query, limit=limit)
        except MarketOpsError as exc:
            # **没有第三个动作**：不 refund、不重试、不改预留。超时或
            # provider-unknown 时你并不知道对方是否已经计费（§8）。
            return QueryReceipt(
                query=query, outcome=QueryOutcome.FAILED, reservation=reservation, error=exc
            )

        return QueryReceipt(
            query=query,
            outcome=QueryOutcome.SENT,
            reservation=self._budget.spend(reservation),
            result=result,
        )

"""``BudgetLedger`` 的契约判据 —— §8「原子预留」的可执行版本。

要守的失败只有一个，但它有三种长相，而且**都不报错**：

1. 一次正在途中的调用不占额度 —— 于是「上限 20 次」允许 20 个调用同时起飞，
   而它们全部会花钱；
2. 一次超时的调用自动退回了额度 —— 你并不知道对方是否已经计费；
3. 一个「忘了配置上限」的科目被当成无限 —— 忘写配置与故意放开不可区分。

三条各有一组判据，外加一条**正对照**（``test_the_two_phase_criterion_actually_
discriminates``）证明这些判据真的会红 —— 一条在判据恒假时仍然全绿的测试，看起来
和一条真的在守事的测试一模一样。

## 为什么两个实现跑同一份

``SqliteBudgetLedger``（跨进程）与 ``InMemoryBudgetLedger``（测试替身）。它在这里
通过而在 SQLite 那里失败，才是「这是一个契约」有意义的样子 —— B3 立的判据是
**端口在第二个实现出现时才提取**，这里正好两个。

## 刻意**不**在这里的判据

* **并发**。上限真的守得住靠的是 ``BEGIN IMMEDIATE``，那是 SQLite 的性质，不是
  契约的性质（内存那份没有锁，也不打算有）。它在
  ``tests/test_budget_ledger.py`` 里，连同一条把「读-改-写会超支」复现出来的
  正对照。
* **版本与迁移**。那是关于**库文件**的，与契约无关。

## 为什么账本对着临时文件测，而不是对着 ``Settings``

``SqliteBudgetLedger`` 收 ``db_path``。用 ``Settings`` 会让每条测试先构造一份
完整配置（还要读环境变量），而账本是纯状态机 —— 它不该需要知道自己是哪个
profile 跑出来的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryBudgetLedger
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.domain.enums import BudgetLine, ReservationState
from marketops.domain.errors import (
    BudgetExhaustedError,
    BudgetNotConfiguredError,
    IdempotencyConflictError,
    ReservationStateError,
)
from marketops.ports.budget import BudgetLedger

SCOPE = "acct-0001:run-a"
OTHER_SCOPE = "acct-0002:run-a"
SOURCE = "frozen-spec"

#: 契约判据要跑过哪些实现。
IMPLEMENTATION_PARAMS = ("sqlite", "memory")


def test_the_contract_runs_against_every_implementation() -> None:
    """实现清单缩水时「两个实现都通过」会变成「一个实现通过」。

    ``params`` 是一个可以被删掉的名字：删掉 ``memory`` 之后整份契约照样全绿，
    而「这是一个契约」这句话就没有依据了 —— 与
    ``tests/test_evidence_store_contract.py`` 里那条同一个道理。
    """
    assert set(IMPLEMENTATION_PARAMS) == {"sqlite", "memory"}


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def ledger(request: pytest.FixtureRequest, tmp_path: Path) -> BudgetLedger:
    """两个实现各跑一遍。新增实现时这里是唯一要改的地方。"""
    if request.param == "sqlite":
        return SqliteBudgetLedger(tmp_path / "marketops.db")
    return InMemoryBudgetLedger()


# ----------------------------------------------------------------------
# 上限是服务端事实
# ----------------------------------------------------------------------
def test_a_missing_ceiling_is_not_unlimited(ledger: BudgetLedger) -> None:
    """未配置**不等于**无限 —— 否则「忘写配置」与「故意放开」行为相同。"""
    with pytest.raises(BudgetNotConfiguredError):
        ledger.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key="k1")


def test_setting_a_ceiling_requires_a_source(ledger: BudgetLedger) -> None:
    """§8：调用方不得**静默**扩大上限。「静默」的反面是「留名」。"""
    with pytest.raises(ValueError):
        ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 10, source="   ")
    assert ledger.usage(SCOPE) == ()


def test_a_ceiling_cannot_be_lowered_below_what_is_occupied(ledger: BudgetLedger) -> None:
    """否则账本会立刻进入「已经超支」，而超支之后所有预留都被拒 —— 像 bug 的合法状态。"""
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 5, source=SOURCE)
    ledger.reserve(SCOPE, BudgetLine.SEARCH, 3, idempotency_key="k1")

    with pytest.raises(BudgetExhaustedError):
        ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 2, source="tighten")


def test_each_line_and_scope_is_accounted_separately(ledger: BudgetLedger) -> None:
    """§8「Comparison mode accounts for each provider separately」的一半。

    搜索跑满不影响文档抓取 —— 一个总数字做不到这件事，而它正是「这次超支是搜索
    跑多了还是模型跑多了」唯一能回答的依据。

    **scope 的隔离是同一条判据的另一半**：两个 scope（两个账号，或者两个实验臂）
    各有各的上限。少了它，「每臂预算独立」就只是报告里的一句声明 —— 两条臂会
    悄悄花同一笔额度。
    """
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 1, source=SOURCE)
    ledger.set_ceiling(SCOPE, BudgetLine.DOCUMENT, 1, source=SOURCE)
    ledger.set_ceiling(OTHER_SCOPE, BudgetLine.SEARCH, 1, source=SOURCE)

    ledger.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key="k1")

    # 同一 scope 的另一条科目、以及另一个 scope 的同一科目，都不受影响。
    ledger.reserve(SCOPE, BudgetLine.DOCUMENT, 1, idempotency_key="k1")
    ledger.reserve(OTHER_SCOPE, BudgetLine.SEARCH, 1, idempotency_key="k1")
    assert ledger.remaining(SCOPE, BudgetLine.SEARCH) == 0
    assert ledger.remaining(SCOPE, BudgetLine.DOCUMENT) == 0
    assert ledger.remaining(OTHER_SCOPE, BudgetLine.SEARCH) == 0


# ----------------------------------------------------------------------
# 两段式：预留占额度，结算才记账
# ----------------------------------------------------------------------
def _holds_before_spending(ledger: BudgetLedger) -> bool:
    """「预留就已经占了额度」这条判据的本体。

    抽成函数是为了让正对照能证明它**真的**会红 —— 见
    ``test_the_two_phase_criterion_actually_discriminates``。
    """
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 3, source=SOURCE)
    ledger.reserve(SCOPE, BudgetLine.SEARCH, 3, idempotency_key="k1")
    try:
        ledger.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key="k2")
    except BudgetExhaustedError:
        return True
    return False


def test_a_reservation_holds_budget_before_anything_is_spent(ledger: BudgetLedger) -> None:
    """这是两段式的全部意义：上限约束的是「在途 + 已花」，不是「已花」。"""
    assert _holds_before_spending(ledger)


class _ReservedIsFree(InMemoryBudgetLedger):
    """一个**错**的实现：只把 ``spent`` 算作占用。

    它的行为是「起飞前不扣额度」，而它看起来完全正常 —— 单次运行、没有并发的
    时候，它的账和正确实现一模一样。正对照用它证明上面那条判据不是恒真。
    """

    def _held(self, scope: str, line: BudgetLine) -> int:
        return self._sum(scope, line, ReservationState.SPENT)


def test_the_two_phase_criterion_actually_discriminates() -> None:
    """正对照：「在途占额度」这条判据在一个只算 ``spent`` 的实现上必须变红。

    没有这一条，上面那条测试在「契约本来就写错了」的情况下依然全绿。
    """
    assert _holds_before_spending(InMemoryBudgetLedger()) is True
    assert _holds_before_spending(_ReservedIsFree()) is False


def test_an_attempt_that_never_settles_keeps_holding_its_budget(ledger: BudgetLedger) -> None:
    """§8 逐字：「在超时或 provider-unknown 时，不要默认释放预算」。

    这里**什么都不做** —— 没有超时分支、没有 except、没有 finally。预留停在
    ``RESERVED`` 继续扣着额度。默认行为恰好是正确的，这正是这条设计想要的：
    要写错，你得显式退款并写明理由。
    """
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 2, source=SOURCE)
    timed_out = ledger.reserve(SCOPE, BudgetLine.SEARCH, 2, idempotency_key="attempt-1")
    # ← 「超时了」—— 没有任何代码在这里运行。

    with pytest.raises(BudgetExhaustedError):
        ledger.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key="attempt-2")

    settled = ledger.get(timed_out.reservation_id)
    assert settled is not None and settled.state is ReservationState.RESERVED
    assert ledger.remaining(SCOPE, BudgetLine.SEARCH) == 0
    assert not timed_out.settles_itself


def test_open_reservations_lists_what_still_needs_reconciling(ledger: BudgetLedger) -> None:
    """不自动退款的配套：必须有一个地方能回答「还有多少额度悬着」。"""
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 4, source=SOURCE)
    hanging = ledger.reserve(SCOPE, BudgetLine.SEARCH, 2, idempotency_key="hanging")
    settled = ledger.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key="settled")
    ledger.spend(settled.reservation_id)

    assert ledger.open_reservations(SCOPE) == (hanging,)
    assert ledger.open_reservations(OTHER_SCOPE) == ()


def test_only_an_explicit_refund_returns_the_budget(ledger: BudgetLedger) -> None:
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 2, source=SOURCE)
    reserved = ledger.reserve(SCOPE, BudgetLine.SEARCH, 2, idempotency_key="k1")
    assert ledger.remaining(SCOPE, BudgetLine.SEARCH) == 0

    ledger.refund(reserved.reservation_id, reason="provider 返回 0 计费，附账单单号 X")
    assert ledger.remaining(SCOPE, BudgetLine.SEARCH) == 2
    assert ledger.open_reservations(SCOPE) == ()


def test_the_refund_reason_is_mandatory(ledger: BudgetLedger) -> None:
    """让「我什么都没查，先把钱要回来」在代码里写不出来。"""
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 2, source=SOURCE)
    reserved = ledger.reserve(SCOPE, BudgetLine.SEARCH, 2, idempotency_key="k1")

    with pytest.raises(ValueError):
        ledger.refund(reserved.reservation_id, reason="   ")

    # 失败不留痕：额度还在挂着，状态没被动过。
    still = ledger.get(reserved.reservation_id)
    assert still is not None and still.state is ReservationState.RESERVED
    assert ledger.remaining(SCOPE, BudgetLine.SEARCH) == 0


def test_settling_twice_does_not_double_count(ledger: BudgetLedger) -> None:
    """同一个结果重发是幂等的（§8：Same request/result retries are idempotent）。"""
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 5, source=SOURCE)
    reserved = ledger.reserve(SCOPE, BudgetLine.SEARCH, 2, idempotency_key="k1")

    first = ledger.spend(reserved.reservation_id)
    second = ledger.spend(reserved.reservation_id)
    assert first == second
    assert ledger.usage(SCOPE)[0].spent == 2


# ----------------------------------------------------------------------
# 幂等键
# ----------------------------------------------------------------------
def test_the_same_idempotency_key_does_not_charge_twice(ledger: BudgetLedger) -> None:
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 5, source=SOURCE)
    first = ledger.reserve(SCOPE, BudgetLine.SEARCH, 2, idempotency_key="same")
    again = ledger.reserve(SCOPE, BudgetLine.SEARCH, 2, idempotency_key="same")

    assert first == again
    assert ledger.usage(SCOPE)[0].reserved == 2


def test_reusing_a_key_with_different_input_is_a_conflict(ledger: BudgetLedger) -> None:
    """幂等键的承诺是「重发不执行两次」，不是「同一个键可以改内容」。"""
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 5, source=SOURCE)
    ledger.reserve(SCOPE, BudgetLine.SEARCH, 2, idempotency_key="same")

    with pytest.raises(IdempotencyConflictError):
        ledger.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key="same")


def test_the_id_namespace_cannot_be_joined_by_a_separator(ledger: BudgetLedger) -> None:
    """``scope`` 与 ``key`` 都可能含分隔符，拼接必须无歧义。

    ``("a:b", "c")`` 与 ``("a", "b:c")`` 在 ``:`` 拼接下是同一条 —— 两个账号
    的额度会共享。用 ASCII 单元分隔符（U+001F）之后它们不同。这条不测实现细节，
    测的是**行为**：两次预留各自占各自的额度。

    它成立的前提是 id 的推导在两处一致（``domain/budget.reservation_id``）——
    id 若由实现自己造（自增、UUID），这条就只是在测一个自增计数器。
    """
    ledger.set_ceiling("a:b", BudgetLine.SEARCH, 1, source=SOURCE)
    ledger.set_ceiling("a", BudgetLine.SEARCH, 1, source=SOURCE)

    first = ledger.reserve("a:b", BudgetLine.SEARCH, 1, idempotency_key="c")
    second = ledger.reserve("a", BudgetLine.SEARCH, 1, idempotency_key="b:c")

    assert first.reservation_id != second.reservation_id
    assert ledger.remaining("a:b", BudgetLine.SEARCH) == 0
    assert ledger.remaining("a", BudgetLine.SEARCH) == 0


# ----------------------------------------------------------------------
# 状态转移
# ----------------------------------------------------------------------
def test_a_spent_reservation_cannot_be_refunded(ledger: BudgetLedger) -> None:
    """已经花掉的钱不能退 —— 那不是记账，是改写用量。"""
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 2, source=SOURCE)
    reserved = ledger.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key="k1")
    ledger.spend(reserved.reservation_id)

    with pytest.raises(ReservationStateError):
        ledger.refund(reserved.reservation_id, reason="想把账要回来")


def test_a_refunded_reservation_cannot_be_spent(ledger: BudgetLedger) -> None:
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 2, source=SOURCE)
    reserved = ledger.reserve(SCOPE, BudgetLine.SEARCH, 1, idempotency_key="k1")
    ledger.refund(reserved.reservation_id, reason="未发起调用")

    with pytest.raises(ReservationStateError):
        ledger.spend(reserved.reservation_id)


def test_settling_an_unknown_reservation_is_refused(ledger: BudgetLedger) -> None:
    with pytest.raises(ReservationStateError):
        ledger.spend("0" * 32)


# ----------------------------------------------------------------------
# 用量分解
# ----------------------------------------------------------------------
def test_usage_separates_spent_from_reserved(ledger: BudgetLedger) -> None:
    """两个数字分开列 —— 「有多少笔还没对账」必须能从账本回答。"""
    ledger.set_ceiling(SCOPE, BudgetLine.SEARCH, 10, source=SOURCE)
    spent = ledger.reserve(SCOPE, BudgetLine.SEARCH, 3, idempotency_key="a")
    ledger.spend(spent.reservation_id)
    ledger.reserve(SCOPE, BudgetLine.SEARCH, 2, idempotency_key="b")
    refunded = ledger.reserve(SCOPE, BudgetLine.SEARCH, 4, idempotency_key="c")
    ledger.refund(refunded.reservation_id, reason="provider 未计费")

    usage = ledger.usage(SCOPE)[0]
    assert (usage.spent, usage.reserved, usage.refunded) == (3, 2, 4)
    assert usage.held == 5
    assert usage.remaining == 5


def test_a_scope_with_no_ceiling_reports_nothing(ledger: BudgetLedger) -> None:
    """``remaining`` 对未配置的科目**抛**，不返回 0。

    返回 0 会让「没配置」与「用完了」是同一个数字，而调用方看到 0 的正常反应
    是「停下来报告预算耗尽」—— 于是忘写配置伪装成一次正常的预算耗尽。
    """
    assert ledger.usage(OTHER_SCOPE) == ()
    with pytest.raises(BudgetNotConfiguredError):
        ledger.remaining(OTHER_SCOPE, BudgetLine.SEARCH)

"""``FetchAttempt`` 这个模型自己守的规矩：**一行账必须在盘上说得通**。

## 为什么这一份与两个抓取器那两份分开

``tests/test_http_fetch_attempts.py`` 与 ``tests/test_browser_fetch_attempts.py`` 判的
是**每一层各自的每一个出口**有没有留下一行（出口表 + 「一个出口都不许漏」）；存储那
一端在 ``tests/test_fetch_attempt_store_contract.py``（三个筛子、幂等、只增不改）。

这一份判的是第三种事：**那一行本身成立不成立**。两层的出口表全绿、每一行都落得下去，
仍然可以是一行**自相矛盾**的记录 —— 每一格各自合法，合起来是假话。那种行不会让任何
一条出口判据转红，因为它不在出口那一层，它在模型这一层。

## 它挡的具体是哪一行

采集设计 §9 与 §5 都要求「未知计费**先对账**」。对账的第一步是**从这一行找到那笔
预留**，所以 ``budget_settlement`` 与 ``budget_reservation_id`` 这两格必须互相说得通：

* ``reserved`` / ``settled`` / ``unknown_pending_reconciliation`` 说「这次调用与一份
  额度有关系」，那就必须指得出是**哪一份**，否则账上多了一行答不出「拿哪一笔去核」
  的记录 —— §5 那条坏结局（同一个来源上二十个账号各自失败一次）长的正是二十行这样
  的记录，而账本里找不出第二十份预留；
* ``not_applicable`` 说「不走预算」，那它就**必须没有**预留号 —— 两者只有一句是真的，
  而「哪些尝试真的押了额度」这个问题的答案会因此多出几行，按那几行去核账的人核的是
  一笔不存在的账。

## 为什么判据要覆盖两个方向，而不是各举一例

因为「一律拒绝」与「一律放行」都能让单方向的判据全绿。下面每一侧都有负例、也都有
**说得通的那个行照样落得下去**的正例（第三组、第四组）。

## 第三组为什么改表而不改枚举

「枚举长出第五个成员而没人决定它归哪一侧」这个局面在本仓**真的出现过**（
``SettlementState`` 的四个成员是分两批加的），所以它得有一条判据。造一个假的第五个
成员做不到（``Enum`` 不能事后长成员），改表是等价且诚实的复现：把某个成员从两张表里
同时移开，模型看到的东西与「枚举新增了一个没被决定的成员」逐字相同。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.domain import fetch_attempt as fetch_attempt_module
from marketops.domain.fetch_attempt import (
    _DOES_NOT_OWE_A_RESERVATION,
    _OWES_A_RESERVATION,
    FetchAttempt,
    MethodKind,
    SettlementState,
)

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)

#: 一份形状上完全正常的预留号 —— 这一份判据**不校验它的形状**（模型 docstring 写了
#: 理由：``settled`` 那一格记的可能是一份供应商标单上的引用），所以用什么值都行。
RESERVATION = "9f2c1e0b8a7d6c5f4e3b2a1908f7e6d5"


def make(**overrides: object) -> FetchAttempt:
    """一条形状完整、其余全取缺省的行。"""
    fields: dict[str, object] = {
        "attempt_id": "att-1",
        "workspace": "ws",
        "method": MethodKind.HTTP,
        "url": "https://example.com/a",
        "started_at": NOW,
        "ended_at": NOW + timedelta(milliseconds=120),
    }
    fields.update(overrides)
    return FetchAttempt(**fields)  # type: ignore[arg-type]


# --- 第一组：那两格是 ``SettlementState`` 的一个分区 --------------------------


def test_the_two_tables_are_a_partition_of_the_settlement_states() -> None:
    """恰好一个分区，且「不欠」的那一边每一条都写下了理由。

    只把「应该是一个分区」写在注释里，下一个人往枚举里加一个成员时就会漏掉这里 ——
    而漏掉的表现是那一行**默默地落进账本**（模型的第三个分支今天会拒绝它，但那条
    分支本身得有东西证明它够得着，见第三组）。

    这一条与 ``tests/test_access_governance.py`` 那条分区判据同形，也照它的先例：
    「不算」的那一边不是一个集合而是一张表，因为每条都得说得出为什么。
    """
    assert _OWES_A_RESERVATION | set(_DOES_NOT_OWE_A_RESERVATION) == set(SettlementState)
    assert not (_OWES_A_RESERVATION & set(_DOES_NOT_OWE_A_RESERVATION))
    for state, why in _DOES_NOT_OWE_A_RESERVATION.items():
        assert why.strip(), f"{state} 在不欠预留号那一边，但没有写下理由"


def test_every_settlement_state_owes_or_explains_the_absence() -> None:
    """四个成员逐个点名 —— 让上面那条分区判据的成员从哪来是可读的。

    分区判据用的是集合运算，枚举删掉一个成员它也会绿（两边一起少）。这一条把成员
    数钉死，两条加起来才覆盖「加」与「删」两个方向。
    """
    assert {s.value for s in SettlementState} == {
        "not_applicable",
        "reserved",
        "settled",
        "unknown_pending_reconciliation",
    }


# --- 第二组：欠一份预留号却什么都没指 ----------------------------------------


@pytest.mark.parametrize("state", sorted(_OWES_A_RESERVATION, key=lambda s: s.value))
def test_a_settlement_that_owes_a_reservation_must_name_one(state: SettlementState) -> None:
    """**负向判据。** 「这次调用押着一份额度」而指不出是哪一份 —— 拒绝。

    §5 那条坏结局（同一个来源上二十个账号各自无界重试）在账上长的就是这样的行：
    每一行都说自己押了额度，而账本里没有第二十份预留可核。这一行落得下去，对账的
    人手上就有一份**看起来完整**的凭据，而它没有下一页。
    """
    with pytest.raises(ValueError, match="budget_reservation_id 是空的"):
        make(budget_settlement=state, budget_reservation_id="")


def test_the_refusal_says_which_state_and_why() -> None:
    """报错要说得出是哪一格、以及为什么 —— 否则它只是一句「不合法」。

    这条判据守的是**诊断**而不是判定：一个只说「invalid」的实现照样能让上面那条
    转红，但下一个撞上它的人得自己把理由重新推一遍，而推错的方向恰好是「那我随便
    填一个号」。
    """
    with pytest.raises(ValueError) as caught:
        make(budget_settlement=SettlementState.RESERVED, budget_reservation_id="")
    message = str(caught.value)
    assert "reserved" in message
    assert "对账" in message


# --- 第三组：不欠预留号却指着一份 --------------------------------------------


@pytest.mark.parametrize("state", sorted(_DOES_NOT_OWE_A_RESERVATION, key=lambda s: s.value))
def test_a_settlement_that_does_not_use_a_budget_must_not_name_one(
    state: SettlementState,
) -> None:
    """**负向判据。** 「这一次不走预算」又指着一份预留 —— 两句话只有一句是真的。

    它比上一组更坏：一个说「没有预留」的行会让「哪些尝试押了额度」这个问题少几行，
    而这一种会**多**几行 —— 按那几行去核账的人核的是一笔不存在的账。
    """
    with pytest.raises(ValueError, match="两者只有一句是真的"):
        make(budget_settlement=state, budget_reservation_id=RESERVATION)


# --- 第四组：说得通的行照样落得下去 ------------------------------------------


@pytest.mark.parametrize("state", sorted(_OWES_A_RESERVATION, key=lambda s: s.value))
def test_owing_a_reservation_is_not_itself_a_refusal(state: SettlementState) -> None:
    """**上面第二组的对照组。** 指着预留号的那些行必须真的构造得出来。

    ``RESERVED`` / ``SETTLED`` / ``UNKNOWN_PENDING_RECONCILIATION`` 今天**没有生产者**
    （采集验收矩阵第 216 行的缺口），所以它们唯一的活证据就是**能不能被构造**。
    一个「一律拒绝」的实现会让第二组全绿 —— 而它同时让这三个成员永远无法落账，
    那正是这一组要挡住的那种「修好了」。
    """
    row = make(budget_settlement=state, budget_reservation_id=RESERVATION)
    assert row.budget_reservation_id == RESERVATION


def test_not_using_a_budget_is_the_shape_the_fetchers_actually_produce() -> None:
    """**第四组的另一半。** 缺省那一格必须是说得通的，因为两层都在往这里落。

    两个抓取器今天都显式传 ``NOT_APPLICABLE`` + 空号（那是**判定不是缺省**），
    各自的出口表里也有对应断言。这一条判的是模型那一侧：那个组合**必须**合法，
    否则一次成功的抓取会在落账时抛 ``ValueError``，而出口表上什么都看不出来。
    """
    row = make()
    assert row.budget_settlement is SettlementState.NOT_APPLICABLE
    assert row.budget_reservation_id == ""


# --- 第五组：枚举新增成员而没人决定它归哪一侧 --------------------------------


def test_an_undecided_member_is_refused_rather_than_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**负向判据。** 一个既不在「欠」也不在「不欠」里的成员 —— 拒绝，不默认放行。

    复现的是「有人往 ``SettlementState`` 加了一个成员、只更新了注释」：改表把某个
    成员从两张表里同时移开，模型看到的东西与那个局面逐字相同。默认放行会让一行
    自相矛盾的记录落进账本，而它在报告上的样子是「账上多了一行」。
    """
    monkeypatch.setattr(fetch_attempt_module, "_OWES_A_RESERVATION", frozenset())
    monkeypatch.setattr(fetch_attempt_module, "_DOES_NOT_OWE_A_RESERVATION", {})
    with pytest.raises(ValueError, match="既不在 _OWES_A_RESERVATION 里"):
        make(budget_settlement=SettlementState.RESERVED, budget_reservation_id=RESERVATION)


def test_an_undecided_member_is_refused_even_when_it_points_at_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同一件事的另一半：**没有预留号**的未决定行也不许落。

    只挡「有号」的那一半，等于把新成员默认归进「不欠」那一侧 —— 而那是判定的反面，
    这个分支存在的理由就是不许有那个缺省。
    """
    monkeypatch.setattr(fetch_attempt_module, "_DOES_NOT_OWE_A_RESERVATION", {})
    with pytest.raises(ValueError, match="既不在 _OWES_A_RESERVATION 里"):
        make(budget_settlement=SettlementState.NOT_APPLICABLE, budget_reservation_id="")

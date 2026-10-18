"""``application/caps.py`` —— 上限截断，逐条钉住，**包括被丢掉的那几条**。

这个模块存在是因为一个具体的、已经发生过的谎：``pipeline._discover`` 原来把候选
URL 切到 ``max_candidates_per_account``，而 ``metrics.candidate_urls`` 记的是**切后**
的值。于是「这次没发现新东西」与「发现了但被上限砍了」在报告上是同一行 ——
前者是市场结论，后者是配置结论，处置完全相反
（``docs/CURRENT_STATE.md`` §12 地雷 #2）。

所以本文件里最重要的一组断言不是「留下几条」，而是**丢掉的是哪几条**：
``dropped_items`` 是**条目本身**（不是下标、不是数量），因为人的下一条问题永远是
「砍掉的是哪几条」，而这个问题只有在顺序确定时才有答案。顺序在这里是**承重**的，
不是实现细节 —— 它由上游的确定性排序定死，``test_the_tail_is_what_gets_dropped``
把这件事钉住。

另外两条边界各自对应一个具体的谎：

* ``cap == len(items)`` 若算成截断，「达到上限」这个信号就会在边界上恒定地谎报一次；
* 负数若静默当成不封顶，一次配置算错就变成「没设上限」，而两者在报告上长得一样
  —— 本仓在这里**失败关闭**（``ConfigError``）。

这些断言全部是纯函数的：不联网、不碰磁盘、不需要 fixture。
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from marketops.application.caps import TruncationDecision, apply_cap
from marketops.domain.errors import ConfigError

ITEMS = ["a", "b", "c", "d", "e"]


# --- 不封顶 -------------------------------------------------------------------


def test_no_cap_keeps_everything_and_says_so() -> None:
    """``None`` = 不封顶，而且报告上必须**看起来**像不封顶。

    ``cap`` 字段保留 ``None`` 本身，不是「0」、也不是一个很大的数：报告读者要能
    一眼把「这次没有上限」与「上限设成了某个数」分开。
    """
    kept, decision = apply_cap(ITEMS, None)

    assert kept == ITEMS
    assert decision.cap is None
    assert decision.kept == len(ITEMS)
    assert decision.dropped == 0
    assert decision.truncated is False
    assert decision.dropped_items == ()


def test_no_cap_returns_a_copy_not_the_callers_list() -> None:
    """返回的是**新列表**。docstring 承诺了，这里钉住。

    调用方拿到列表之后会往下传、可能被就地改；如果它是入参本身，那个改动会回头
    写到上游的候选池上，而 ``TruncationDecision`` 里的数字仍然是切那一刻的数字 ——
    两个东西会开始互相矛盾，且没有任何症状。
    """
    source = list(ITEMS)
    kept, _ = apply_cap(source, None)

    kept.append("篡改")
    kept.pop(0)

    assert source == ITEMS


# --- 截断 ---------------------------------------------------------------------


def test_the_tail_is_what_gets_dropped() -> None:
    """丢的是**尾部**，且丢掉的条目本身按原顺序被保留下来。

    这条是承重的：如果顺序是随机的，``dropped_items`` 就只是噪音；正因为上游的
    排序是确定性的，「砍掉的是哪几条」才是一个可复现、可评审的答案。
    """
    kept, decision = apply_cap(ITEMS, 2)

    assert kept == ["a", "b"]
    assert decision.kept == 2
    assert decision.dropped == 3
    assert decision.cap == 2
    assert decision.truncated is True
    assert decision.dropped_items == ("c", "d", "e"), "丢的是尾部，且是条目本身而不是下标"


def test_cap_zero_keeps_nothing_and_still_reports_every_item() -> None:
    """``cap=0`` 是合法配置（一个都不收），不是「不封顶」。

    它与 ``None`` 的差别必须能在报告上读出来：``cap=0`` 时 ``cap == 0``、``kept == 0``、
    ``dropped == 5``、``truncated is True``；``None`` 时 ``cap is None``、``kept == 5``。
    """
    kept, decision = apply_cap(ITEMS, 0)

    assert kept == []
    assert decision.cap == 0
    assert decision.kept == 0
    assert decision.dropped == len(ITEMS)
    assert decision.truncated is True
    assert decision.dropped_items == tuple(ITEMS)


def test_kept_plus_dropped_is_always_the_input_length() -> None:
    """``kept + dropped`` 恒等于输入长度 —— 每一个上限值都成立。

    两个数字各自都能算出来，但两个都留着：报告里「候选 12 条」与「另外 18 条被上限
    砍掉」是两句话，合成一个「共 30 条」会把上限造成的损失藏进一个更大的数字里。
    """
    for cap in (None, 0, 1, 4, 5, 6, 30):
        kept, decision = apply_cap(ITEMS, cap)
        assert decision.kept + decision.dropped == len(ITEMS), f"cap={cap}"
        assert decision.kept == len(kept), f"cap={cap}"
        assert decision.dropped == len(decision.dropped_items), f"cap={cap}"


# --- 边界：装得下就不算截断 ---------------------------------------------------


def test_a_cap_that_exactly_fits_is_not_a_truncation() -> None:
    """``cap == len`` 恰好装下 —— ``truncated`` 必须是 ``False``。

    算成截断的话，「达到上限」这个信号会在边界上恒定地谎报一次：满额与超限在报告上
    同形，而两者要做的下一件事不同。
    """
    kept, decision = apply_cap(ITEMS, len(ITEMS))

    assert kept == ITEMS
    assert decision.dropped == 0
    assert decision.truncated is False
    assert decision.cap == len(ITEMS), "上限本身要如实写出来，即使一条都没丢"


def test_a_cap_larger_than_the_input_is_not_a_truncation() -> None:
    """上限比池子还大：一条不丢，但仍然如实记下**生效的上限值**。"""
    kept, decision = apply_cap(ITEMS, 30)

    assert kept == ITEMS
    assert decision.dropped == 0
    assert decision.truncated is False
    assert decision.cap == 30


def test_an_empty_input_is_not_a_truncation_even_under_a_zero_cap() -> None:
    """空池子 + ``cap=0``：丢 0 条。「没有候选」与「被上限砍光」不是一回事。"""
    kept, decision = apply_cap([], 0)

    assert kept == []
    assert decision.kept == 0
    assert decision.dropped == 0
    assert decision.truncated is False, "池子本来就是空的，零上限没有砍掉任何东西"


# --- 失败关闭 -----------------------------------------------------------------


def test_a_negative_cap_is_a_configuration_error_not_unlimited() -> None:
    """负数**抛**，不静默当成不封顶。

    旧实现用 ``-1`` 表示不封顶 —— 一个需要注释才读得懂的哨兵值，与「有上限但确实
    很小」共用同一个类型。静默接受负数，会让一次配置算错变成「没设上限」，而两者
    在报告上长得一样（都是 ``cap`` 一格看不懂的数字）；更糟的是上限从此**消失**，
    而没有任何东西会报警。
    """
    with pytest.raises(ConfigError) as excinfo:
        apply_cap(ITEMS, -1)

    message = str(excinfo.value)
    assert "None" in message, "错误信息必须说出不封顶该怎么写，否则它只是拒绝，不是修复"
    assert "-1" in message, "错误信息要带上实际收到的值"


@pytest.mark.parametrize("cap", [-1, -2, -100])
def test_every_negative_cap_is_rejected_including_the_old_sentinel(cap: int) -> None:
    """**每一个**负数都被拒绝 —— 尤其是 ``-1``。

    ``-1`` 是旧实现里「不封顶」的写法。它撞上这条断言，是这次移植**有意**的偏离：
    在旧方言里 ``-1`` 与「上限是 1」共用同一个类型，靠注释区分；在这里它只是一个
    算错了的负数，而正确的写法（``None``）在错误信息里。
    """
    with pytest.raises(ConfigError):
        apply_cap(ITEMS, cap)


# --- 序列化 -------------------------------------------------------------------


def test_the_decision_serialises_and_carries_truncated() -> None:
    """``as_dict()`` 可 JSON 序列化，且 ``truncated`` **写在里面**。

    ``truncated`` 是一个由 ``dropped`` 现算得出的属性，仍然要写出去：报告读者不该
    为了回答「这次被砍了吗」而先记住一个推导规则。``dropped_items`` 是元组，
    序列化后必须是列表。
    """
    _, decision = apply_cap(ITEMS, 2)
    payload = decision.as_dict()

    assert payload == {
        "cap": 2,
        "kept": 2,
        "dropped": 3,
        "truncated": True,
        "dropped_items": ["c", "d", "e"],
    }
    assert isinstance(payload["dropped_items"], list)
    assert json.loads(json.dumps(payload)) == payload


def test_the_untruncated_decision_serialises_too() -> None:
    """不封顶那一支同样可序列化 —— ``cap`` 写 ``None``，不是省略这一格。"""
    _, decision = apply_cap(ITEMS, None)
    payload = decision.as_dict()

    assert payload["cap"] is None
    assert payload["truncated"] is False
    assert payload["dropped_items"] == []


def test_the_decision_is_frozen() -> None:
    """决定是冻结的 —— 它是「当时切掉什么」的记录，不是可变的工作区。"""
    _, decision = apply_cap(ITEMS, 2)

    with pytest.raises(FrozenInstanceError):
        decision.dropped = 0  # type: ignore[misc]


def test_a_hand_built_decision_can_describe_no_cap() -> None:
    """``cap=None`` 是 dataclass 里的合法取值，不是只由 ``apply_cap`` 造得出来。"""
    decision = TruncationDecision(kept=3, dropped=0)

    assert decision.cap is None
    assert decision.truncated is False
    assert decision.as_dict()["cap"] is None

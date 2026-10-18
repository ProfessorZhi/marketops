"""``domain/run_delta.py`` 的形状 —— 一轮 Run 结束时那份回执的**内容判据**。

纯域，不碰存储（落盘与两个实现跑同一份判据在 ``tests/test_run_ending_contract.py``）。

## 这一族里最重的一条：计数是**推导**出来的，不是存下来的

``RunDelta.counts`` 是一个 property，不是字段。这一条必须被执行器钉住，而不是写在
``run_delta.py`` 的 docstring 里 —— 存下来的那一份与处置表分家的那天（有人只加了一条
处置、忘了加计数），报告上两处都读得通，而**没有一处会红**。
:func:`test_the_counts_are_not_a_stored_field` 判的就是这件事。

## 「合法零新增仍能结束」在形状这一层的落点

§B17 条件 2 的后半句要求「**合法零新增仍能以 unchanged/held/blocked 结束**」。
形状这一层要提供的是：一份 ``effects`` 全是 ``UNCHANGED`` 的 Delta 是合法的、
``counts[DISCOVERED]`` 与 ``counts[PROMOTED]`` 都是 0、而 :attr:`RunDelta.touched`
仍然是 ``True`` —— 于是「碰过一堆东西但什么都没新增」在回执上写得出来，而它与
「压根没跑」（``is_empty``）是两件不同的事。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.run import Run, RunBudgetLine, RunSpec
from marketops.domain.run_delta import (
    DeltaCell,
    PoolEffect,
    RunDelta,
    UnaccountedUsage,
)
from marketops.ports.runs import ending_refusal

WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-devon"
KEY = "run-2026-09-28-b"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)


def _spec(**overrides: object) -> RunSpec:
    payload: dict[str, object] = {
        "workspace": WORKSPACE,
        "objective": "补一轮 Devon 的场地",
        "selector": SCENE,
        "context_ref": "a" * 64,
        "plan_ref": "b" * 64,
        "execution_mode": ExecutionMode.MANUAL,
        "window": None,
        "budget": (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "cutoff": CUTOFF,
        "policy_versions": ("grading@1f2e3d",),
        "idempotency_key": KEY,
    }
    payload.update(overrides)
    return RunSpec(**payload)  # type: ignore[arg-type]


def _effect(cell: DeltaCell, key: str, **rest: str) -> PoolEffect:
    """一条处置。``label`` / ``why`` 由调用点显式给 —— 哪几格必须给由类型判。"""
    return PoolEffect(cell=cell, key=key, **rest)


# --- 计数是推导的 -------------------------------------------------------------


def test_the_counts_are_not_a_stored_field() -> None:
    """``counts`` **不是** ``RunDelta`` 上的一个字段，只有 ``effects`` 是。

    这是本文件里最重的一条：计数一旦变成一个可以单独写的字段，它与处置表分家的那
    一天（有人改了一处、忘了另一处）在报告上两边都读得通 —— 而回执的存在理由正是
    「拿去向人交代这一轮做了什么」。一份自己与自己不一致的交代，比一份空白的更坏。
    """
    assert "counts" not in RunDelta.model_fields
    assert {"effects", "recommendations"} <= set(RunDelta.model_fields)


def test_every_cell_is_counted_even_at_zero() -> None:
    """九格恒在，**零也列出来**。``counts[cell]`` 的读法因此不需要先判在不在。

    「不在」与「是 0」在调用点会长得一样，而这件事有后果：报告里「这一轮没有
    ``held``」与「这份 Delta 根本还没有 ``held`` 这一格」会印成同一行。
    """
    counts = RunDelta(effects=(_effect(DeltaCell.HELD, "v-1", why="缺名字"),)).counts

    assert set(counts) == set(DeltaCell)
    assert counts[DeltaCell.HELD] == 1
    assert counts[DeltaCell.DISCOVERED] == 0
    assert len(counts) == 9


def test_the_counts_follow_the_effects() -> None:
    """计数跟着处置走 —— 加一条处置，那一格就动一格。"""
    before = RunDelta(effects=(_effect(DeltaCell.DISCOVERED, "v-1", label="Devon 球场"),))
    after = RunDelta(
        effects=(
            _effect(DeltaCell.DISCOVERED, "v-1", label="Devon 球场"),
            _effect(DeltaCell.DISCOVERED, "v-2", label="Exeter 球场"),
        )
    )

    assert before.counts[DeltaCell.DISCOVERED] == 1
    assert after.counts[DeltaCell.DISCOVERED] == 2


# --- 一个业务键只能有一个处置 -------------------------------------------------


def test_one_key_cannot_have_two_dispositions() -> None:
    """同一个业务键两次处置**报错**，而报文里两个处置词都在。

    这条不是洁癖：切片 c 的双向对账（每一个计数回得到池子里那一行、池子里每一行
    回得到计数）只有在「键 → 处置」是一个**函数**时才成立。允许一个键既
    ``discovered`` 又 ``updated``，对账的两边都会读得通，而多出来的那一行没人知道
    是哪一行。
    """
    with pytest.raises(ValidationError) as caught:
        RunDelta(
            effects=(
                _effect(DeltaCell.DISCOVERED, "v-1", label="Devon 球场"),
                _effect(DeltaCell.UPDATED, "v-1", label="Devon 球场"),
            )
        )

    message = str(caught.value)
    assert "v-1" in message
    assert "discovered" in message and "updated" in message


def test_two_different_keys_are_fine() -> None:
    """不同键各有一条处置 —— 上面那条判据不是「effects 只能有一条」。"""
    delta = RunDelta(
        effects=(
            _effect(DeltaCell.DISCOVERED, "v-1", label="Devon 球场"),
            _effect(DeltaCell.UPDATED, "v-2", label="Exeter 球场"),
        )
    )

    assert len(delta.effects) == 2


def test_a_blank_key_is_refused() -> None:
    """空键报错：它是这一条处置对着的那一行，空着时对账两边都读得通。"""
    with pytest.raises(ValidationError) as caught:
        RunDelta(effects=(PoolEffect(cell=DeltaCell.DISCOVERED, key="   ", label="x"),))

    assert "双向对账" in str(caught.value)


# --- 三格要说得出理由 ---------------------------------------------------------


@pytest.mark.parametrize("cell", [DeltaCell.EXCLUDED, DeltaCell.HELD, DeltaCell.BLOCKED])
def test_the_three_pool_refusals_must_justify_themselves(cell: DeltaCell) -> None:
    """``excluded`` / ``held`` / ``blocked`` 必须带 ``why``。

    三条都在说「它没进池子」，而三条的**处置完全不同**：一条要人去看排除项写得对不对，
    一条要人补齐缺的那样东西，一条是来源那边的问题。不给理由，三者在报告上同形 ——
    与 ``application/caps.py::TruncationDecision``（截断必须被记录）是同一条纪律。
    """
    with pytest.raises(ValidationError) as caught:
        RunDelta(effects=(PoolEffect(cell=cell, key="v-1"),))

    assert cell.value in str(caught.value)
    assert "why" in str(caught.value)


@pytest.mark.parametrize(
    "cell", [DeltaCell.EXCLUDED, DeltaCell.HELD, DeltaCell.BLOCKED]
)
def test_a_justified_refusal_does_not_need_a_name(cell: DeltaCell) -> None:
    """这三格**可以**没有 ``label`` —— 它们说的正是「池子里还没有它的名字」。

    与 ``receipt.py`` 里 ``pending`` 的行带不了 ``resolved_name`` 是同一条纪律。
    """
    delta = RunDelta(effects=(PoolEffect(cell=cell, key="v-1", why="来源那边 502"),))

    assert delta.counts[cell] == 1


# --- 六格要带得出名字 ---------------------------------------------------------


@pytest.mark.parametrize(
    "cell",
    [
        DeltaCell.DISCOVERED,
        DeltaCell.PROMOTED,
        DeltaCell.UPDATED,
        DeltaCell.SUPERSEDED,
        DeltaCell.WITHDRAWN,
        DeltaCell.UNCHANGED,
    ],
)
def test_the_six_pool_facts_must_name_their_object(cell: DeltaCell) -> None:
    """这六格主张的是池子里**一个有名字的对象**上的事实，所以必须带得出那个名字。

    一条只有句柄的处置行读不出那是什么 —— 而回执唯一的用途就是读。
    """
    with pytest.raises(ValidationError) as caught:
        RunDelta(effects=(PoolEffect(cell=cell, key="v-1"),))

    assert cell.value in str(caught.value)
    assert "label" in str(caught.value)


def test_the_two_lists_are_complements() -> None:
    """「必须说理由」与「必须带名字」两组加起来**正好**九格，一个不多一个不少。

    这样加第十格时，两条判据里至少有一条会红 —— 而它红在一个**测试**里，不是在
    一次真实结束的报告里（那里少一个 label 只会让某一行读不出来）。
    """
    must_justify = {DeltaCell.EXCLUDED, DeltaCell.HELD, DeltaCell.BLOCKED}
    must_name = {
        DeltaCell.DISCOVERED,
        DeltaCell.PROMOTED,
        DeltaCell.UPDATED,
        DeltaCell.SUPERSEDED,
        DeltaCell.WITHDRAWN,
        DeltaCell.UNCHANGED,
    }

    assert must_justify | must_name == set(DeltaCell)
    assert must_justify & must_name == set()
    assert len(DeltaCell) == 9


# --- 引用表：非空元素、无重复 -------------------------------------------------


@pytest.mark.parametrize(
    "field", ["routes", "evidence", "artifacts", "reviews", "actions", "usage"]
)
def test_a_reference_list_has_no_blank_element(field: str) -> None:
    """空元素读起来像一条引用 —— 而下游会照着它去取一个不存在的东西。

    这一串逐字列出来的六个格子就是**同一族校验器**的成员（``actions`` 是 §B24 进来
    的第六格）：它由收盘那一趟写回来，而它收的照旧是「一串 id」，所以同一个空元素在
    这一格上与在那五格上是同一件事。
    """
    with pytest.raises(ValidationError) as caught:
        RunDelta(**{field: ("r-1", "  ")})

    assert field in str(caught.value)


@pytest.mark.parametrize(
    "field", ["routes", "evidence", "artifacts", "reviews", "actions", "usage"]
)
def test_a_reference_list_is_not_deduplicated_silently(field: str) -> None:
    """重复的引用**报错**，不在这一层悄悄去重。

    悄悄去重会让「这一轮到底写了几次」读不出来，而那个数是条件 2 对账的一半。
    去重是调用方的事（``application/merge.py::ordered_union`` 是那把尺子）——
    ``actions`` 那一格由 ``open_followups`` 走的就是这把尺子。
    """
    with pytest.raises(ValidationError) as caught:
        RunDelta(**{field: ("r-1", "r-1")})

    assert field in str(caught.value)
    assert "重复" in str(caught.value)


# --- 未记账 -------------------------------------------------------------------


def test_an_unaccounted_row_must_say_who_and_why() -> None:
    """一条说不出「去问谁」或「为什么算不出来」的未记账 = 一条忘了填的记录。"""
    with pytest.raises(ValidationError):
        UnaccountedUsage(provider="  ", calls=3, reason="超时")
    with pytest.raises(ValidationError):
        UnaccountedUsage(provider="serper", calls=3, reason="   ")


def test_zero_unaccounted_calls_is_refused() -> None:
    """``calls`` 至少是 1。**零次的未记账不是一只空着的手**：它求和时贡献 0，
    于是「有几笔没对上」这个数被自己抹平。"""
    with pytest.raises(ValidationError) as caught:
        UnaccountedUsage(provider="serper", calls=0, reason="超时")

    assert "calls" in str(caught.value)


def test_usage_records_reservations_not_amounts() -> None:
    """``usage`` 装的是**预留 id**，不是金额。

    金额只有一个权威（``ports/budget.py`` 那一处 ``SUM``）；在回执里再抄一个数就是
    第二个答案 —— 而两个答案分家的那天，读者会照回执上那个数去做决定。所以这一格
    的类型里没有整数，只有字符串。
    """
    annotation = RunDelta.model_fields["usage"].annotation
    assert annotation == tuple[str, ...], annotation


# --- 建议不是触发器 -----------------------------------------------------------


def test_the_recommendations_are_prose_not_a_trigger() -> None:
    """``recommendations`` 是给人看的散文，**不是**一份能拿去起下一轮 Run 的结构。

    §B17 条件 3 逐字：manual 的 Run 走完之后**保持 idle，绝不自行扩下一批**。一个
    能直接喂回受理面的结构，会让那句话在盘上无处成立 —— 所以这一格在类型上只是
    字符串，而这一条判据把「它至今还是字符串」变成一句可执行的话。
    """
    annotation = RunDelta.model_fields["recommendations"].annotation

    assert annotation == tuple[str, ...], annotation


# --- 空与不空 -----------------------------------------------------------------


def test_nothing_at_all_is_empty_and_untouched() -> None:
    assert RunDelta().is_empty is True
    assert RunDelta().touched is False


def test_a_zero_gain_run_is_touched_but_not_empty() -> None:
    """**「合法零新增」的形状。** 处置全在 ``unchanged`` / ``held`` 上。

    这一轮碰过东西（``touched``），而 ``discovered`` 与 ``promoted`` 都是 0 ——
    §B17 条件 2 要的正是这种结束能被写下来。
    """
    delta = RunDelta(
        effects=(
            _effect(DeltaCell.UNCHANGED, "v-1", label="Devon 球场"),
            _effect(DeltaCell.UNCHANGED, "v-2", label="Exeter 球场"),
            _effect(DeltaCell.HELD, "v-3", why="缺一个名字"),
        )
    )

    assert delta.touched is True
    assert delta.is_empty is False
    assert delta.counts[DeltaCell.DISCOVERED] == 0
    assert delta.counts[DeltaCell.PROMOTED] == 0
    assert delta.counts[DeltaCell.HELD] == 1
    assert delta.keys == ("v-1", "v-2", "v-3")


def test_evidence_only_is_neither_touched_nor_empty() -> None:
    """只抓到证据、没有任何池子处置：``touched`` 是 ``False``，``is_empty`` 也是。

    两个问题必须分开 —— 一个是「池子上动过没有」，一个是「整份回执上有没有一件事
    可查」。合成一个之后，一场只产生证据的研究会在回执上读成「什么都没做」。
    """
    delta = RunDelta(evidence=("e-1", "e-2"), usage=("res-1",))

    assert delta.touched is False
    assert delta.is_empty is False


# --- 结局与 Delta 的配对判据（纯域这一半） ------------------------------------


def test_a_completed_run_must_not_be_empty() -> None:
    """``COMPLETED`` 不能配一份每一格都空的 Delta。报文里要点名那三个词。

    「跑完了，什么都没碰」与「压根没跑」在回执上必须不同 —— 后者根本不该有一行结局。
    """
    refusal = ending_refusal(RunStatus.COMPLETED, RunDelta())

    assert refusal
    assert "completed" in refusal
    assert "unchanged" in refusal and "held" in refusal and "blocked" in refusal


@pytest.mark.parametrize(
    "status", [RunStatus.PARTIAL, RunStatus.FAILED]
)
def test_a_run_that_never_got_going_may_stop_with_nothing(status: RunStatus) -> None:
    """``PARTIAL`` / ``FAILED`` 可以是空的。

    一轮在跑起来之前就断掉，产出确实是零 —— 要求它编点什么出来才是伪造。它与
    ``COMPLETED`` 的区别正是那句结局词本身。
    """
    assert ending_refusal(status, RunDelta()) == ""


def test_a_zero_gain_run_ends_as_completed() -> None:
    """**条件 2 的那半句，正面。** 零新增、但每一行都有归宿 → 允许 ``COMPLETED``。"""
    delta = RunDelta(
        effects=(
            _effect(DeltaCell.UNCHANGED, "v-1", label="Devon 球场"),
            _effect(DeltaCell.BLOCKED, "v-2", why="来源那边 502"),
        )
    )

    assert ending_refusal(RunStatus.COMPLETED, delta) == ""


def test_a_route_only_run_ends_as_completed() -> None:
    """没有一条池子处置、但有路线产出 —— 也是一次 ``COMPLETED``。

    这条判的是上面那条判据**不要求有 effects**：它要的是「有一件事可查」，而
    ``effects`` 只是九格里的第一格。
    """
    assert ending_refusal(RunStatus.COMPLETED, RunDelta(routes=("r-1",))) == ""


# --- Run 的三格配对 -----------------------------------------------------------


def test_a_run_cannot_have_an_ending_without_a_delta() -> None:
    """结局、Delta、停表**同时有或同时无** —— 三个组合各判一次。

    一个「已经结束、但产出还没写」的窗口里，读的人无法区分「它什么都没产出」与
    「产出丢了」，而这两件事的处置相反。
    """
    spec = _spec()
    with pytest.raises(ValueError) as caught:
        Run(
            run_id="0" * 32,
            spec=spec,
            spec_sha256="f" * 64,
            created_at=T0.isoformat(),
            updated_at=T0.isoformat(),
            stop_reason=RunStatus.COMPLETED,
            delta=None,
            stopped_at=None,
        )
    assert "同时有或同时无" in str(caught.value)

    with pytest.raises(ValueError):
        Run(
            run_id="0" * 32,
            spec=spec,
            spec_sha256="f" * 64,
            created_at=T0.isoformat(),
            updated_at=T0.isoformat(),
            stop_reason=None,
            delta=RunDelta(routes=("r-1",)),
            stopped_at=T0.isoformat(),
        )

    with pytest.raises(ValueError):
        Run(
            run_id="0" * 32,
            spec=spec,
            spec_sha256="f" * 64,
            created_at=T0.isoformat(),
            updated_at=T0.isoformat(),
            stop_reason=None,
            delta=None,
            stopped_at=T0.isoformat(),
        )


def test_a_run_with_all_three_is_fine() -> None:
    """三者齐 —— 一个受受理、跑完、有产出的 Run 的正常形状。"""
    stopped = Run(
        run_id="0" * 32,
        spec=_spec(),
        spec_sha256="f" * 64,
        created_at=T0.isoformat(),
        updated_at=T0.isoformat(),
        stop_reason=RunStatus.COMPLETED,
        delta=RunDelta(routes=("r-1",)),
        stopped_at=T0.isoformat(),
    )

    assert stopped.is_stopped is True


def test_a_run_in_flight_is_fine() -> None:
    """三者全无 —— 还在跑。"""
    running = Run(
        run_id="0" * 32,
        spec=_spec(),
        spec_sha256="f" * 64,
        created_at=T0.isoformat(),
        updated_at=T0.isoformat(),
        stop_reason=None,
        delta=None,
        stopped_at=None,
    )

    assert running.is_stopped is False

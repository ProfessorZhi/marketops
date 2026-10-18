"""``domain/run.py`` 的**授权窗口** —— 第十一格的形状，以及它与其余三格的一致性。

纯域，不碰存储、不碰时钟、不碰受理面：这一族判的都是「一份规格自己说不说得拢」。
受理面**拒在哪里**由 ``tests/test_run_contract.py``（两个实现跑同一份判据）与
``tests/test_run_admission.py``（库调用点的拼装）各自判，不在这里。

## 这一族里最重的一条：域层**不**判「``bounded_auto`` 必须带窗口」

:func:`test_a_bounded_auto_without_a_window_is_a_well_formed_spec` 是一条**正面**
判据：它断言那样一份规格**收得下**。读起来像在放松纪律，实际相反 —— 那条纪律今天
住在 ``ports/runs.py::admission_refusal``，而它是**两个实现共用的那一份**。

两处都判的话，「非 manual 模式今天受理不了」这句话就有了两个出处，而**先触发的那
一处**就是用户看到的那一句：域层先炸，端口那一句（e1 时是「窗口还没有读者」，e2 之后
拆成了两句）永远到不了任何人眼前，报告上只剩「请求拼不成规格」—— 而两者的处置完全
不同（前者改参数，后者是去接那个生产者；在 e1 那一版里这两句还读起来更像）。
所以这里钉住域层的边界，让那句判据只有一个出处可退。

代价是 ``RunSpec`` 收得下一份没有窗口的自动规格。这在盘上造不出来：落一行 Run 的
唯一入口是 ``RunStore.admit``，而它先问 ``admission_refusal``。

## 三个阈值、四条一致性，各自钉一句

三个阈值（对象数 / 累计预算 / 截止）是 §B17 条件 4 那句「达到任一 stop 后不创建下一
Run」的**三个被求值项** —— 缺一个，「没设过」与「还没到」就分不开了。四条一致性则
各自是一种处置：模式配错、截止超窗口、信封缺科目（授权范围）、信封超窗口（参数）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from marketops.domain.enums import BudgetLine, ExecutionMode
from marketops.domain.run import (
    AuthorizationWindow,
    RunBudgetLine,
    RunSpec,
    spec_fingerprint,
)

WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-devon"
KEY = "run-2026-09-28-w"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)
#: 窗口的截止。**晚于** :data:`CUTOFF` —— 让「截止超窗口」那一条在默认规格里不触发。
NOT_AFTER = T0 + timedelta(days=30)


def _window(**overrides: object) -> AuthorizationWindow:
    payload: dict[str, object] = {
        "max_objects": 50,
        "budget": (
            RunBudgetLine(line=BudgetLine.SEARCH, ceiling=100),
            RunBudgetLine(line=BudgetLine.DOCUMENT, ceiling=40),
        ),
        "not_after": NOT_AFTER,
    }
    payload.update(overrides)
    return AuthorizationWindow(**payload)  # type: ignore[arg-type]


def _spec(**overrides: object) -> RunSpec:
    """一份最小规格。默认 ``manual`` 且不带窗口 —— 窗口由用例自己带上来。"""
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


# --- 三个阈值都是必填的 -------------------------------------------------------


@pytest.mark.parametrize("max_objects", [0, -1])
def test_a_window_that_authorises_no_object_is_refused(max_objects: int) -> None:
    """``max_objects < 1`` 拒 —— 「一个对象都不许走的窗口**就是没有授权**」。

    0 在 ``RunBudgetLine.ceiling`` 上是合法的（「这一轮不走这条线」），在这里不是：
    两者都是「这个数是 0」，但说的不是一句话。
    """
    with pytest.raises(ValidationError) as caught:
        _window(max_objects=max_objects)

    assert "就是没有授权" in str(caught.value)


def test_an_empty_window_budget_is_refused() -> None:
    """窗口的预算线不得为空 —— 否则「达累计预算时 stop」永远不触发。

    与 ``RunSpec`` 那条「budget 不得为空」同一条理由：**「没设过」与「还没到」必须
    分得开**，而一条线都没有的窗口在回执上与「预算还很宽裕」同形。
    """
    with pytest.raises(ValidationError) as caught:
        _window(budget=())

    assert "不得为空" in str(caught.value)


def test_a_repeated_line_in_the_window_budget_is_refused() -> None:
    """同一个科目出现两次拒 —— 两条上限谁说了算没有答案。"""
    with pytest.raises(ValidationError) as caught:
        _window(
            budget=(
                RunBudgetLine(line=BudgetLine.SEARCH, ceiling=100),
                RunBudgetLine(line=BudgetLine.SEARCH, ceiling=200),
            )
        )

    assert "出现了多次" in str(caught.value)


def test_a_naive_not_after_is_refused() -> None:
    """``not_after`` 必须带时区 —— naive 时刻不得进入本系统。"""
    with pytest.raises(ValidationError) as caught:
        _window(not_after=datetime(2026, 10, 28, 9, 0))

    assert "naive datetime" in str(caught.value)


# --- digest：对内容敏感、对写法不敏感 -----------------------------------------


def test_the_digest_ignores_the_order_of_the_budget_lines() -> None:
    """两条线换个位置算出来是**同一个**摘要。

    否则「这两轮是不是同一条链」会因为一份等价的写法而答「不是」—— 而那时累计在
    两条并行的链上各算一半，于是两个都没到阈值。这是摘要而非链引用那个选择的
    一半；另一半是「不走窗外的路」由 ``RunSpec`` 的一致性判。
    """
    forward = _window()
    backward = _window(budget=tuple(reversed(forward.budget)))

    assert forward.digest == backward.digest


@pytest.mark.parametrize(
    "change",
    [
        {"max_objects": 51},
        {"not_after": NOT_AFTER + timedelta(days=1)},
        {"budget": (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=101),)},
    ],
)
def test_the_digest_changes_with_each_of_the_three_thresholds(change: dict) -> None:
    """三个阈值**各改一次**，摘要都变 —— 三个都在摘要里，没有一个是装饰。

    这条判据的参数表就是 §B17 条件 4 那句「对象数 / 累计预算 / 截止时间**任一**」
    的展开：漏掉任何一个，两轮范围不同的 Run 会被算成同一条链。
    """
    assert _window(**change).digest != _window().digest


# --- 与其余三格的一致性：四条，各自的处置不同 ---------------------------------


@pytest.mark.parametrize("mode", [ExecutionMode.MANUAL, ExecutionMode.WATCH])
def test_only_a_bounded_auto_specification_may_carry_a_window(mode: ExecutionMode) -> None:
    """``manual`` / ``watch`` 带窗口拒 —— 窗口是「这一轮之后可以自己接下一轮」的授权。

    两格的处置不同：``manual`` 逐字是「有人明确提交了这一次」，它带窗口等于在记录里
    写下「这条链可以自己走」；``watch`` 的来源是**到期或信号**，它的阈值该由 due 判定
    回答，而不是由一次受理写下来 —— 而那句话今天**有一处读它**
    （``application/run_admission.py::_require_due``），不是一句待办：切片 f 之后
    ``watch`` 的受理前提正是那份 due 判定，所以「阈值不由窗口写下来」这句话有了主语。
    """
    with pytest.raises(ValidationError) as caught:
        _spec(execution_mode=mode, window=_window())

    assert mode.value in str(caught.value)
    assert "带了授权窗口" in str(caught.value)


def test_a_cutoff_beyond_the_window_is_refused() -> None:
    """本轮截止晚于窗口截止拒 —— 那一轮会在授权已经过期之后还在跑。

    那时「达到截止时间就停」这条判据已经成立（窗口到期），而这一轮自己还没到 ——
    它成了这条链上唯一一个没人管得住的后继者。
    """
    with pytest.raises(ValidationError) as caught:
        _spec(
            execution_mode=ExecutionMode.BOUNDED_AUTO,
            window=_window(not_after=CUTOFF - timedelta(hours=1)),
        )

    assert "超出了它的授权窗口" in str(caught.value)


def test_an_envelope_line_the_window_does_not_cover_is_refused() -> None:
    """本轮信封里有窗口没有的科目拒 —— 那条线在链上没有上限。

    处置是**把窗口补全**（一次授权范围的改变），而不是把本轮的信封减一条：减掉的
    那条线上要做的事并没有变得不需要授权。
    """
    with pytest.raises(ValidationError) as caught:
        _spec(
            execution_mode=ExecutionMode.BOUNDED_AUTO,
            window=_window(budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=100),)),
            budget=(RunBudgetLine(line=BudgetLine.DOCUMENT, ceiling=5),),
        )

    assert "窗口没有的科目" in str(caught.value)


def test_an_envelope_line_above_the_window_is_refused() -> None:
    """本轮单条上限高于窗口同科目拒 —— 这一轮自己就能把整条链花光。

    处置是**把本轮的信封调小**（一次参数调整），与「窗口里缺了这条线」不是同一件事。
    """
    with pytest.raises(ValidationError) as caught:
        _spec(
            execution_mode=ExecutionMode.BOUNDED_AUTO,
            window=_window(budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=100),)),
            budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=101),),
        )

    assert "超过了它的授权窗口" in str(caught.value)


def test_a_specification_inside_its_window_is_well_formed() -> None:
    """正面的那一半：窗口之内的一份自动规格**收得下**。

    缺了这条，上面四条判据在「那一格根本没人填得对」时也是绿的 —— 而那时它们钉的是
    一个空集。
    """
    spec = _spec(
        execution_mode=ExecutionMode.BOUNDED_AUTO,
        window=_window(),
        budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
    )

    assert spec.window is not None
    assert spec.window.max_objects == 50


# --- 域层**不**判的那一条（正面证据） -----------------------------------------


def test_a_bounded_auto_without_a_window_is_a_well_formed_spec() -> None:
    """``bounded_auto`` 配 ``window=None`` **收得下** —— 这**不是**放松纪律。

    「非 manual 模式今天受理不了」那句话的唯一出处是
    ``ports/runs.py::admission_refusal``，服务面与库调用点都从那里读。域层再判一次，
    先炸的就是域层，而端口那一句（e1 时是「窗口还没有读者」，e2 之后拆成了两句）永远
    到不了任何人眼前 —— 报告上只剩「请求拼不成规格」，处置被读成「改参数」而不是
    「去接那个生产者」。

    盘上造不出这样一行：落一行 Run 的唯一入口是 ``RunStore.admit``，它先问
    ``admission_refusal``。这条判据存在的意义是**钉住那条边界在域层的位置** ——
    哪天有人把这条校验器加回 ``RunSpec``（理由听起来会很正当：一份自动规格该有边界），
    这里会红。
    """
    spec = _spec(execution_mode=ExecutionMode.BOUNDED_AUTO)

    assert spec.window is None
    assert spec.execution_mode is ExecutionMode.BOUNDED_AUTO


# --- 窗口进 content 散列 -------------------------------------------------------


def test_the_window_enters_the_spec_digest() -> None:
    """换一个窗口就是**另一次提交** —— 同一个幂等键配它是冲突，不是重发。

    窗口不进散列的话，「重发同一个键」与「换一个窗口再提交一次」会算出同一个摘要，
    于是冲突检查恒不触发：一个 40 个对象的授权与一个 400 个对象的授权会被当成同一次。
    """
    narrow = _spec(execution_mode=ExecutionMode.BOUNDED_AUTO, window=_window(max_objects=50))
    wide = _spec(execution_mode=ExecutionMode.BOUNDED_AUTO, window=_window(max_objects=400))

    assert spec_fingerprint(narrow) != spec_fingerprint(wide)
    assert spec_fingerprint(narrow) == spec_fingerprint(
        _spec(execution_mode=ExecutionMode.BOUNDED_AUTO, window=_window(max_objects=50))
    )


def test_an_equivalently_written_window_is_the_same_submission() -> None:
    """预算线换个位置**不**改变这次提交的摘要。

    这条把「进散列的是 :attr:`AuthorizationWindow.digest`」钉在**可执行**的层面：
    换成一个逐字 ``repr`` 窗口的实现，这份等价写法会算出另一个摘要，而那时「同一个
    键重发」会被读成冲突 —— 报告上只显示「同一个键配了另一份规格」，而两份规格说的
    是同一件事。
    """
    forward = _window()
    backward = _window(budget=tuple(reversed(forward.budget)))

    left = _spec(execution_mode=ExecutionMode.BOUNDED_AUTO, window=forward)
    right = _spec(execution_mode=ExecutionMode.BOUNDED_AUTO, window=backward)

    assert spec_fingerprint(left) == spec_fingerprint(right)

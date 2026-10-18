"""增量维护：**什么时候该重新看一眼哪一格**。

``domain/temporal.py`` 问的是「这条证据今天还能不能用」；本模块问的是另一个
问题：「我们该不该现在去重核一遍」。两者只差一个词，处置却完全不同 ——
前者决定一句话能不能被引用，后者决定要不要真的花一笔钱重新采集。

本文件守四条边界，每一条都对应一种**不会报错、只会多花钱**的失败：

1. **缺一条规则 ≠ 永不过期。** 计划必须七个 aspect 齐全，否则调度器选不出
   那一格，而报告上看不出这是决定还是遗漏。
2. **未标定 ≠ 已批准。** 缺省计划里的那些天数没有一个来自获批策略，所以
   任何「把未标定读成业务事实」的入口都不许存在。
3. **周期里没有 ``now``。** 去重键一旦随 ``now`` 漂移，重启与重复 tick 就会把
   同一格重做一遍，而账单上只显示「核得比较勤」。
4. **选择器不发明主体。** 主体的存在性只有两个来源（看过一眼的、调用方声明的），
   一条指向不存在主体的信号是数据错误，不是一件新工作。

没有这些断言，「维护」会静默退化成「每次 tick 把所有格子重做一遍」——
而那是这套东西唯一会失控的地方。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from marketops.domain.maintenance import (
    ALL_ASPECTS,
    Aspect,
    AspectState,
    AspectStatus,
    ChangeSignal,
    DueReason,
    DueWork,
    MaintenanceClaim,
    MaintenanceState,
    Observation,
    RefreshPlan,
    RefreshRule,
    aspect_states,
    claim_of,
    what_is_due,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
WORKSPACE = "probe-workspace"
SCENE_ID = "probe-scene"
SOURCE_REF = "run-probe-001"
CADENCE = 30


# --- 夹具 -------------------------------------------------------------------


def _plan(*, after_days: int = CADENCE, calibrated: bool = False) -> RefreshPlan:
    """七个 aspect 同一条节奏的计划 —— 让「哪一格到期」只由观测时刻决定。"""
    return RefreshPlan(
        rules=tuple(
            RefreshRule(aspect=aspect, after_days=after_days, calibrated=calibrated)
            for aspect in ALL_ASPECTS
        )
    )


def _observation(**overrides) -> Observation:
    base = dict(
        workspace=WORKSPACE,
        subject_kind="account",
        subject_id="probe-001",
        aspect=Aspect.SOURCE_COVERAGE,
        observed_at=NOW - timedelta(days=1),
        source_ref=SOURCE_REF,
    )
    base.update(overrides)
    return Observation(**base)


def _signal(**overrides) -> ChangeSignal:
    base = dict(
        workspace=WORKSPACE,
        subject_kind="account",
        subject_id="probe-001",
        aspect=Aspect.SOURCE_COVERAGE,
        signalled_at=NOW,
        reason="the operating company changed",
    )
    base.update(overrides)
    return ChangeSignal(**base)


def _claim(**overrides) -> MaintenanceClaim:
    base = dict(
        dedupe_key="a" * 32,
        workspace=WORKSPACE,
        scene_id=SCENE_ID,
        scene_version=1,
        subject_kind="account",
        subject_id="probe-001",
        aspect=Aspect.ACCOUNT_RELATION,
        reason=DueReason.CADENCE_ELAPSED,
        period="cycle-1",
        state=MaintenanceState.CLAIMED,
        claimed_at=NOW,
    )
    base.update(overrides)
    return MaintenanceClaim(**base)


def _select(
    *,
    observations: tuple[Observation, ...] = (),
    signals: tuple[ChangeSignal, ...] = (),
    now: datetime = NOW,
    subjects: tuple[tuple[str, str], ...] = (),
    plan: RefreshPlan | None = None,
    scene_version: int = 1,
) -> tuple[DueWork, ...]:
    return what_is_due(
        scene_id=SCENE_ID,
        scene_version=scene_version,
        plan=_plan() if plan is None else plan,
        observations=observations,
        signals=signals,
        now=now,
        subjects=subjects,
    )


def _cadence(work: tuple[DueWork, ...]) -> tuple[DueWork, ...]:
    """只留下按节奏到期的那些 —— 否则每条断言都要先绕开「从未观测」的噪声。"""
    return tuple(item for item in work if item.reason is DueReason.CADENCE_ELAPSED)


def _signalled(work: tuple[DueWork, ...]) -> tuple[DueWork, ...]:
    return tuple(item for item in work if item.reason is DueReason.CHANGE_SIGNALLED)


# --- 计划：齐全、有序、未标定 -------------------------------------------------


def test_a_plan_missing_an_aspect_is_a_gap_not_an_exemption() -> None:
    """少一条规则的那一格既不会被复核、也不会被报告 —— 与「决定不复核」长得一样。

    所以它在构造期就必须失败，而且消息要点名缺的是哪几个 aspect：
    报告里的一行空白没有信息量，一个列表才有。
    """
    with pytest.raises(ValueError, match="复核计划缺 aspect") as raised:
        RefreshPlan(rules=(RefreshRule(aspect=Aspect.SOURCE_COVERAGE, after_days=CADENCE),))

    for missing in ALL_ASPECTS[1:]:
        assert missing.value in str(raised.value), (
            f"{missing.value} 缺了却没被点名 —— 运营的人无从补起"
        )


def test_the_same_aspect_configured_twice_is_refused() -> None:
    """一个 aspect 配两条节奏，调度器只会用得上一条，而报告无法说明是哪一条。

    多出来的那条不会报错，它只会让「这个格子多久核一次」有两个答案。
    """
    duplicated = (
        RefreshRule(aspect=Aspect.SOURCE_COVERAGE, after_days=CADENCE),
        RefreshRule(aspect=Aspect.SOURCE_COVERAGE, after_days=CADENCE * 2),
        RefreshRule(aspect=Aspect.ASSET_ATTRIBUTE, after_days=CADENCE),
        RefreshRule(aspect=Aspect.ACCOUNT_RELATION, after_days=CADENCE),
        RefreshRule(aspect=Aspect.CONTACT_PERSON_ROLE, after_days=CADENCE),
        RefreshRule(aspect=Aspect.PRODUCT_FIT, after_days=CADENCE),
    )
    with pytest.raises(ValueError, match=r"同一个 aspect 配了多条复核规则：\['source_coverage'\]"):
        RefreshPlan(rules=duplicated)


def test_the_plan_must_come_back_in_the_order_the_report_needs() -> None:
    """顺序不参与语义，但它进报告 —— 一份每次跑都换次序的报告，人眼比不了两次运行。

    这条与上一条不同：上一条拦的是配错，这一条拦的是配对了却没有稳定次序。
    """
    with pytest.raises(ValueError, match="必须按 ALL_ASPECTS 的顺序给出"):
        RefreshPlan(
            rules=tuple(
                RefreshRule(aspect=aspect, after_days=CADENCE)
                for aspect in reversed(ALL_ASPECTS)
            )
        )


def test_the_default_plan_is_complete_and_entirely_uncalibrated() -> None:
    """缺省计划里的天数**不是业务事实** —— 没有一个来自获批策略或留出评估。

    这里按**字面量**断言 ``(aspect, calibrated)``，而不是问 ``is_calibrated``：
    后者只在「全部都没标定」时才为假，于是往缺省里塞进一条已标定的规则
    **不会让它变红**。而缺省计划被读成「已批准」，正是这套调度唯一会失控的地方。

    ## 天数也逐条写下来，因为「照抄父格」是一句声明

    拆成七个面的时候，两对拆出来的格子各自沿用了父格的天数（见
    :meth:`RefreshPlan.uncalibrated_default`）：给两半各编一个不同的数会让报告里
    那一格看起来像有人分别标定过。**那句话在这里有执行器** —— 下面按字面量列出
    七个天数，所以「照抄」不是一句写进 docstring 就没人管的话：
    谁哪天顺手把 ``ASSET_ATTRIBUTE`` 改成 90，这一条会红，而红的正是那句声明。

    ``is_calibrated`` 那条断言不覆盖这件事：天数变了它照样是 ``False``。
    """
    plan = RefreshPlan.uncalibrated_default()

    assert tuple((rule.aspect, rule.after_days, rule.calibrated) for rule in plan.rules) == (
        (Aspect.SOURCE_COVERAGE, 180, False),
        (Aspect.ASSET_IDENTITY, 180, False),
        (Aspect.ASSET_ATTRIBUTE, 180, False),
        (Aspect.ACCOUNT_RELATION, 90, False),
        (Aspect.CONTACT_PERSON_ROLE, 90, False),
        (Aspect.EMAIL_EVIDENCE, 90, False),
        (Aspect.PRODUCT_FIT, 30, False),
    )
    assert plan.is_calibrated is False
    assert plan.uncalibrated == ALL_ASPECTS


def test_one_calibrated_rule_is_enough_to_move_a_plan_off_the_default() -> None:
    """反过来：只要有一条标定过，这份计划就不再等于缺省计划。

    两个方向的断言合起来，才让「未标定」这件事在数据上说得清 —— 只说
    「全部标定才算标定」而不说「一条标定就足够不同」，读的人无法判断
    缺省计划是不是可以悄悄长出一条已批准的规则。
    """
    rules = [rule.model_copy(update={"calibrated": True}) for rule in RefreshPlan.uncalibrated_default().rules]
    plan = RefreshPlan(rules=tuple(rules))

    assert plan.is_calibrated is True
    assert plan.uncalibrated == ()
    assert plan.rules != RefreshPlan.uncalibrated_default().rules


@pytest.mark.parametrize("after_days", [0, -1])
def test_a_cadence_of_zero_days_is_refused_because_it_would_bill_forever(
    after_days: int,
) -> None:
    """0 天不是「立刻」，是「**永远**到期」：每一次 tick 都重核同一格。

    它不会被任何一处报错 —— 只会让账单按 tick 的频率增长，
    而报告上显示的是「这一格核得比较勤」。
    """
    with pytest.raises(ValueError, match="无底洞"):
        RefreshRule(aspect=Aspect.PRODUCT_FIT, after_days=after_days)


# --- 到期：节奏 ---------------------------------------------------------------


def test_a_cell_inside_its_cadence_is_not_selected() -> None:
    """差一天没到期就是没到期。早核一次不会报错，它只是白花一次钱。"""
    work = _select(observations=(_observation(observed_at=NOW - timedelta(days=CADENCE - 1)),))
    assert _cadence(work) == ()


def test_a_cell_exactly_at_its_cadence_is_selected() -> None:
    """到期那一刻就该被选中 —— 边界往哪边倒决定了每一格是早一天还是晚一天。

    断言 ``last_observed_at`` 与 ``source_ref`` 也在：一件该做的维护工作
    必须能回答「上次看到它是什么时候、是哪一次运行看到的」，否则复核无从下手。
    """
    observed = _observation(observed_at=NOW - timedelta(days=CADENCE))
    (item,) = _cadence(_select(observations=(observed,)))

    assert item.reason is DueReason.CADENCE_ELAPSED
    assert item.last_observed_at == NOW - timedelta(days=CADENCE)
    assert item.source_ref == SOURCE_REF
    assert item.scene_id == SCENE_ID
    assert item.scene_version == 1


def test_the_cycle_a_key_belongs_to_does_not_move_when_now_does() -> None:
    """重启或重复 tick 不得把同一格重做一遍 —— 这是去重键存在的全部理由。

    周期是「自上次观测起的第几轮」，**不含 now**。含了的话每次 tick 都是
    一个新周期，去重键形同虚设，而账上只显示「核得比较勤」。
    """
    observed = _observation(observed_at=NOW - timedelta(days=CADENCE + 5))
    early = _select(observations=(observed,), now=NOW)
    late = _select(observations=(observed,), now=NOW + timedelta(days=10))

    assert [item.dedupe_key for item in early] == [item.dedupe_key for item in late]
    assert _cadence(early) == _cadence(late)


def test_the_next_cycle_is_a_genuinely_new_piece_of_work() -> None:
    """整整一轮过去之后那一格确实该再核一次，键必须变。

    它防的是反面：周期被算成「永远是第一轮」，于是这一格从此再也不会被复核 ——
    而报告上看起来一切正常，因为每次都「已经做过了」。
    """
    observed = _observation(observed_at=NOW - timedelta(days=CADENCE))
    first = _select(observations=(observed,))
    second = _select(observations=(observed,), now=NOW + timedelta(days=CADENCE))

    assert [item.period for item in _cadence(first)] == ["cycle-1"]
    assert [item.period for item in _cadence(second)] == ["cycle-2"]
    assert _cadence(first)[0].dedupe_key != _cadence(second)[0].dedupe_key


def test_the_cadence_is_measured_from_the_latest_observation_not_the_first() -> None:
    """维护关心的是「**上次**看到它是什么时候」。

    取最早的那次会让一个刚刚核过的格子永远显示为到期 —— 于是它被反复重核，
    每一次都真的花钱。这个 bug 不会报错：报告上只是「核得比较勤」。
    """
    work = _select(
        observations=(
            _observation(observed_at=NOW - timedelta(days=365)),
            _observation(observed_at=NOW - timedelta(days=1)),
        )
    )
    assert _cadence(work) == ()


# --- 到期：变更信号 -----------------------------------------------------------


def test_a_change_signal_selects_the_cell_before_its_cadence() -> None:
    """周期复核够用但不够及时：场地换了运营方，在 90 天的周期里会被继续当成旧的用。

    信号把「该看的时间」从日历上挪到事件上 —— 所以它必须在那格**还没到期**时
    就选出工作，否则它就只是把日历又写了一遍。
    """
    fresh = _observation(aspect=Aspect.ACCOUNT_RELATION, observed_at=NOW - timedelta(days=1))
    signal = _signal(aspect=Aspect.ACCOUNT_RELATION)
    (item,) = _signalled(_select(observations=(fresh,), signals=(signal,)))

    assert item.aspect is Aspect.ACCOUNT_RELATION
    assert item.last_observed_at == NOW - timedelta(days=1)
    assert item.source_ref == "the operating company changed"


def test_replaying_one_signal_is_still_one_piece_of_work() -> None:
    """重放安全是信号制的前提：驱动重启、消息重投都不得变成两件工作。

    两件工作的代价是这一格被核两次 —— 第二次的结论与第一次一样，
    而账上是两笔。
    """
    fresh = _observation(observed_at=NOW - timedelta(days=1))
    signal = _signal(aspect=Aspect.ACCOUNT_RELATION)
    once = _select(observations=(fresh,), signals=(signal,))
    replayed = _select(observations=(fresh,), signals=(signal, signal))

    assert [item.period for item in _signalled(once)] == [item.period for item in _signalled(replayed)]
    assert [item.dedupe_key for item in once] == [item.dedupe_key for item in replayed]


def test_a_signal_already_answered_by_a_later_observation_selects_nothing() -> None:
    """信号是**事件**，不是状态：看过之后它就不再让这一格到期。

    不这样判的后果不是多做一次，而是**永远多做**。信号不会被消费，于是这一格
    此后每一轮计划都显示为到期，而 tick 每一次都拿不到认领（同一个 ``dedupe_key``
    已经 DONE 过）—— 计划说「该做」、tick 说「没得做」，两份报告互相矛盾，
    而真相是这件事早就办完了。
    """
    answered = _observation(
        aspect=Aspect.ACCOUNT_RELATION, observed_at=NOW + timedelta(hours=1)
    )
    signal = _signal(aspect=Aspect.ACCOUNT_RELATION)

    assert _signalled(_select(observations=(answered,), signals=(signal,))) == ()


def test_a_signal_nobody_has_looked_at_since_stays_due() -> None:
    """反面：还没有人看过的时候，这条信号一直把这一格留在到期清单上。

    与上一条合起来才钉得住判据 —— 只测「看过之后不再到期」的话，一个**从不**
    让信号产生工作的实现照样绿，而那等于信号根本没有生产者之外的用途。
    """
    older = _observation(
        aspect=Aspect.ACCOUNT_RELATION, observed_at=NOW - timedelta(days=1)
    )
    signal = _signal(aspect=Aspect.ACCOUNT_RELATION)

    assert len(_signalled(_select(observations=(older,), signals=(signal,)))) == 1


def test_a_signal_at_a_different_instant_is_a_different_piece_of_work() -> None:
    """两条不同时刻的信号是两次复核：第二次报告的是「又变了」。

    把它们合成一件，第二次变更会在账上消失 —— 而「这一次为什么重跑」
    正是复核报告要回答的问题。**合并的是同一时刻的必要工作，不是同一格的全部信号**：
    这一点由 ``period`` 承担（信号时刻就是周期），而 ``reasons`` 那一格与它无关。
    """
    fresh = _observation(
        aspect=Aspect.ACCOUNT_RELATION, observed_at=NOW - timedelta(days=1)
    )
    first = _select(observations=(fresh,), signals=(_signal(aspect=Aspect.ACCOUNT_RELATION),))
    later = _select(
        observations=(fresh,),
        signals=(
            _signal(aspect=Aspect.ACCOUNT_RELATION, signalled_at=NOW + timedelta(hours=1)),
        ),
    )

    assert _signalled(first)[0].dedupe_key != _signalled(later)[0].dedupe_key


def test_a_first_observation_and_a_signal_are_one_piece_of_work() -> None:
    """**从来没看过**的一格上落了一条变更信号 —— 一件必要重查，两个理由。

    §11 逐字：「同一对象/面上同时**出现首次观察与变更信号**时合并必要工作，
    保留全部原因」。这一格的两个理由是同一件事的两种说法（「还没核过」
    与「有人报了变动」），都指向**那一次**初次复核。

    两行实现下的失败不是多做一次，而是**自相矛盾**：计划说「这一格两件工作」，
    而 tick 只会做一件（第二件的 ``dedupe_key`` 与刚 DONE 的那件不同，
    于是它会真的再花一次钱），报告与账本从此对不上。
    """
    signal = _signal(aspect=Aspect.ACCOUNT_RELATION)
    work = [
        item
        for item in _select(observations=(), signals=(signal,), subjects=(("account", "probe-001"),))
        if item.aspect is Aspect.ACCOUNT_RELATION
    ]

    assert len(work) == 1
    assert work[0].reasons == (DueReason.NEVER_OBSERVED, DueReason.CHANGE_SIGNALLED)
    assert work[0].reason is DueReason.NEVER_OBSERVED, (
        "「从来没核过」比「有人报了变动」更靠前：前者本来就是这一件的全部原因"
    )
    assert work[0].period == "never", "周期取自最强的那个理由，而它不是信号"
    assert work[0].last_observed_at is None


def test_a_signal_about_a_subject_nobody_has_ever_seen_selects_nothing() -> None:
    """一条指向不存在主体的信号是数据错误，处置是回去查它从哪来。

    一个会自动建主体的选择器必然也会**发明**主体：池子里混进一个拼错的 id，
    它就会被排成「一件从来没核过的工作」，而账上只显示「多做了一件」。
    """
    work = _select(
        observations=(_observation(),),
        signals=(_signal(subject_id="probe-nonexistent"),),
    )
    assert [item for item in work if item.subject_id == "probe-nonexistent"] == []


def test_a_cadence_rerun_and_a_signalled_rerun_are_one_piece_of_work() -> None:
    """同一格同时到期又有信号 —— 是**一件**工作，**两个**理由。

    §11 逐字：「同一对象/面上同时出现首次观察与变更信号时**合并必要工作，
    保留全部原因**」。这一格的两个理由是同一件必要重查的两种说法，而必要重查
    只有一次 —— 之前的两行实现会让它被驱动两次，第二次拿不到认领（同一个
    ``dedupe_key`` 已经 DONE 过），于是计划说「该做」、tick 说「没得做」。

    但「信号提前触发的那一次」不能因此在账上消失：它由 ``reasons`` 那一格承担，
    这正是合并**保留全部原因**的意思。合并后 ``period`` 取自最强的、也就是最新的
    那条信号 —— 于是「这一件是信号说的」仍然读得出来。
    """
    aged = _observation(
        aspect=Aspect.ACCOUNT_RELATION, observed_at=NOW - timedelta(days=CADENCE + 5)
    )
    signal = _signal(aspect=Aspect.ACCOUNT_RELATION)
    work = _select(observations=(aged,), signals=(signal,))
    relevant = [item for item in work if item.aspect is Aspect.ACCOUNT_RELATION]

    assert len(relevant) == 1, "一件必要重查被同一格的两个理由拆成了两行"
    merged = relevant[0]
    assert merged.reasons == (DueReason.CHANGE_SIGNALLED, DueReason.CADENCE_ELAPSED), (
        "理由集合不再是「两个都在」，或者不再是强到弱的次序"
    )
    assert merged.period == f"signal-{signal.signalled_at.isoformat()}", (
        "周期没有取自最强的那个理由，于是这一件在新信号到来时会复用旧键"
    )
    # ``due_at`` 取的是**最早**的那条理由：它回答「这一件从什么时候起该做的」。
    # 取最强理由的时刻会让一条早该做的时限工作在报告里显得比实际晚。
    assert merged.due_at == aged.observed_at + timedelta(days=CADENCE)
    assert merged.last_observed_at == aged.observed_at


# --- 到期：主体从哪来 ---------------------------------------------------------


def test_a_declared_subject_nobody_has_looked_at_yet_is_due_on_every_aspect() -> None:
    """刚立项、一格都还没看过的 Scene 需要一个起跑线。

    ``last_observed_at`` 是 ``None`` 而不是某个 epoch：**从未观测**与
    「1970 年观测过」在报告上是两件事，而只有前者能解释这一格为什么是空的。
    """
    work = _select(subjects=(("venue", "probe-venue-001"),))

    assert {item.aspect for item in work} == set(ALL_ASPECTS)
    assert all(item.reason is DueReason.NEVER_OBSERVED for item in work)
    assert all(item.period == "never" for item in work)
    assert all(item.last_observed_at is None for item in work)


def test_the_six_aspects_nobody_looked_at_are_still_due() -> None:
    """主体存在不等于它的每一面都看过。

    证据是一条一条落地的：一个账号的来源覆盖核过了，不等于它的资产身份、资产属性、
    账号关系、联系人与角色、邮箱证据、产品适配也核过了。把「主体已知」读成
    「各面已知」，会让那六格观测永远不发生 —— 而报告上显示的是「未到期」。

    这一条正是把五个面拆成七个面时**没有变宽、只是变准**的那个地方：拆出来的四格
    与被拆的两格一起，仍然各自要有人真的看一眼。
    """
    work = _select(observations=(_observation(observed_at=NOW - timedelta(days=1)),))
    never = [item for item in work if item.reason is DueReason.NEVER_OBSERVED]

    assert {item.aspect for item in never} == set(ALL_ASPECTS) - {Aspect.SOURCE_COVERAGE}
    assert _cadence(work) == ()


# --- 到期：顺序 ---------------------------------------------------------------


def test_the_selection_order_is_stable_and_longest_overdue_first() -> None:
    """「取前 N 件」的选择器如果不稳定，两次运行会做不同的事，而账上各记一次。

    顺序按 ``due_at`` 排，于是拖得最久的一格排在前面 —— 截断时先做的是欠得
    最久的那些，而不是字典序最小的那些。
    """
    observations = (
        _observation(
            subject_kind="venue",
            subject_id="probe-venue-001",
            observed_at=NOW - timedelta(days=100),
        ),
    )
    subjects = (("account", "probe-001"),)

    first = _select(observations=observations, subjects=subjects)
    second = _select(observations=observations, subjects=subjects)

    assert first == second
    keys = [
        (item.due_at, item.subject_kind, item.subject_id, item.aspect.value) for item in first
    ]
    assert keys == sorted(keys)
    assert first[0].subject_kind == "venue", "欠得最久的一格没有排在最前面"
    assert first[0].due_at < first[-1].due_at


# --- 幂等键的构成 -------------------------------------------------------------


def test_the_dedupe_key_is_the_cell_and_the_cycle_and_nothing_else() -> None:
    """键把 scene / 版本 / 主体 / aspect / 周期五样编进去 —— **不多不少**。

    多了 ``due_at`` 或 ``reasons``，同一格在同一周期里会因为「这次是信号触发的」
    而变成第二件工作；少了 ``period``，下一轮复核会被去重掉，
    那一格从此再也不会被看。
    """
    base = dict(
        scene_id=SCENE_ID,
        scene_version=1,
        subject_kind="account",
        subject_id="probe-001",
        aspect=Aspect.ACCOUNT_RELATION,
        reasons=(DueReason.CADENCE_ELAPSED,),
        period="cycle-1",
        due_at=NOW,
        last_observed_at=NOW - timedelta(days=CADENCE),
        source_ref=SOURCE_REF,
    )
    reference = DueWork(**base)

    # 五样之外的字段改了，仍然是同一件工作 —— 包括理由那一格：
    # 理由是「为什么重跑」，键是「哪一件」，两者不是一回事。
    assert DueWork(**{**base, "due_at": NOW + timedelta(days=5)}).dedupe_key == reference.dedupe_key
    assert (
        DueWork(**{**base, "reasons": (DueReason.CHANGE_SIGNALLED,)}).dedupe_key
        == reference.dedupe_key
    )
    assert (
        DueWork(
            **{
                **base,
                "reasons": (DueReason.CHANGE_SIGNALLED, DueReason.CADENCE_ELAPSED),
                "due_at": NOW - timedelta(days=5),
            }
        ).dedupe_key
        == reference.dedupe_key
    )
    assert DueWork(**{**base, "last_observed_at": None}).dedupe_key == reference.dedupe_key
    assert DueWork(**{**base, "source_ref": "another-run"}).dedupe_key == reference.dedupe_key

    # 五样里任何一样变了，就是另一件工作
    assert DueWork(**{**base, "period": "cycle-2"}).dedupe_key != reference.dedupe_key
    assert DueWork(**{**base, "scene_id": "other-scene"}).dedupe_key != reference.dedupe_key
    assert DueWork(**{**base, "scene_version": 2}).dedupe_key != reference.dedupe_key
    assert DueWork(**{**base, "subject_id": "probe-002"}).dedupe_key != reference.dedupe_key
    assert DueWork(**{**base, "aspect": Aspect.PRODUCT_FIT}).dedupe_key != reference.dedupe_key


def test_a_new_scene_version_makes_every_cell_new_work() -> None:
    """换一版规则之后，旧结论不再成立，所以每一格都是新工作。

    版本号不进键的话，改了一版规则会**什么都不发生** —— 池子里还是按旧规则
    核出来的结论，而报告上一切照旧。
    """
    observations = (_observation(observed_at=NOW - timedelta(days=CADENCE), aspect=Aspect.PRODUCT_FIT),)
    old = _cadence(_select(observations=observations, scene_version=1))
    new = _cadence(_select(observations=observations, scene_version=2))

    assert [item.dedupe_key for item in old] != [item.dedupe_key for item in new]


# --- 时间与理由的硬约束 -------------------------------------------------------


def test_a_naive_now_is_refused() -> None:
    """naive 的 now 在两个部署之间会被读成不同的时刻。

    差 8 小时就足以让一格早一天或晚一天到期 —— 而这个差异不会以任何形式报错。
    """
    with pytest.raises(ValueError, match="now 必须带时区"):
        _select(now=datetime(2026, 9, 22, 12, 0))


def test_a_naive_observation_time_is_refused() -> None:
    with pytest.raises(ValueError, match="observed_at 必须带时区"):
        _observation(observed_at=datetime(2026, 9, 22, 12, 0))


def test_a_naive_signal_time_is_refused() -> None:
    with pytest.raises(ValueError, match="signalled_at 必须带时区"):
        _signal(signalled_at=datetime(2026, 9, 22, 12, 0))


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_signal_without_a_reason_is_indistinguishable_from_a_pressed_button(
    reason: str,
) -> None:
    """复核报告要回答的恰恰是「为什么这次会重跑这一格」。

    没有理由的信号在报告里与「有人点了按钮」无法区分，而两者的处置不同：
    前者要复核那一格，后者要问为什么要点。
    """
    with pytest.raises(ValueError, match="变更信号必须写明理由"):
        _signal(reason=reason)


def test_an_observation_belongs_to_a_workspace_whose_name_it_already_is() -> None:
    """工作区是授权链的第一段：观测事实可以跨 Scene 复用，但复用止于工作区边界。

    需要规范化的 workspace 会在落盘时被改写，于是两条不同工作区的记录落到
    同一个文件上 —— 一条别人工作区的事实会看起来像自己的。
    """
    with pytest.raises(ValueError, match="workspace 不是规范形式"):
        _observation(workspace="Probe Workspace")


def test_two_spellings_of_one_instant_are_one_observation() -> None:
    """同一个瞬间的两种写法必须给出同一个时刻。

    本仓把时刻格式化成 ISO-8601 字符串存，而字符串的大小顺序只在偏移量相同
    时才等于瞬间的先后：``09:00+08:00`` 与 ``02:00+00:00`` 是同一个瞬间，
    前者按字符串排却在**后面**。不规范化的话，「取最近一次观测」会取到一个
    更早的，而到期判定从此按它的日期算 —— 没有任何一处会红。
    """
    as_beijing = NOW.astimezone(timezone(timedelta(hours=8)))
    assert as_beijing.isoformat() != NOW.isoformat(), "这条判据要的是两种**不同的写法**"

    assert _observation(observed_at=as_beijing).observed_at == NOW


def test_the_same_signal_instant_spelled_two_ways_is_one_piece_of_work() -> None:
    """重放保护不能靠「写法相同」这条运气。

    信号的周期就是信号时刻本身，所以同一刻写成两种偏移量必须落成同一个键 ——
    否则一条被中间层换过写法重投的信号会变成第二件工作，而报告上说不出
    这两件有什么不同。
    """
    fresh = _observation(aspect=Aspect.ACCOUNT_RELATION, observed_at=NOW - timedelta(days=1))
    as_beijing = NOW.astimezone(timezone(timedelta(hours=8)))

    utc = _select(observations=(fresh,), signals=(_signal(aspect=Aspect.ACCOUNT_RELATION),))
    offset = _select(
        observations=(fresh,),
        signals=(
            _signal(aspect=Aspect.ACCOUNT_RELATION, signalled_at=as_beijing),
        ),
    )
    assert _signalled(utc)[0].dedupe_key == _signalled(offset)[0].dedupe_key


# --- 回执（同时也是租约） -----------------------------------------------------


def test_a_receipt_carries_the_cell_it_is_about() -> None:
    """回执必须能回答「这是哪一格的第几轮」—— 键之外，五样也要跟着带过来。

    只留一个 ``dedupe_key`` 的回执，在报告里是一串散列；而运营要读的是
    「哪个场地的哪一面、第几轮」。缺一样，那一格就要靠人去反查。
    """
    claim = _claim()

    assert (claim.scene_id, claim.scene_version, claim.subject_id, claim.aspect, claim.period) == (
        SCENE_ID,
        1,
        "probe-001",
        Aspect.ACCOUNT_RELATION,
        "cycle-1",
    )
    assert claim.state is MaintenanceState.CLAIMED
    assert claim.claimed_at == NOW
    assert claim.attempt == 1


def test_a_claim_that_is_still_running_cannot_report_an_outcome() -> None:
    """「有人在跑」与「跑完了」在报告上是两格。

    放一条停在 ``CLAIMED`` 却带着产出的回执过去，崩溃留下的那条就与
    「做完了」长得一样，而调度器据此不再重试 —— 那一格从此静默地不更新。
    """
    with pytest.raises(ValueError, match="不得有完成时刻"):
        _claim(finished_at=NOW)
    with pytest.raises(ValueError, match="不得带产出或失败原因"):
        _claim(result_ref="evidence/probe-0001.jsonl")


def test_a_done_receipt_cannot_carry_a_failure_and_a_failed_one_must() -> None:
    """「做完了，什么都没变」是一个**成功**的回执，它与「失败了」必须分得开。

    合成一个 detail 字段之后，第一次有人把失败原因写进 detail，
    统计里失败那一格就永远是绿的。
    """
    with pytest.raises(ValueError, match="DONE 的回执不得带失败原因"):
        _claim(state=MaintenanceState.DONE, finished_at=NOW, failure="fetch timed out")
    with pytest.raises(ValueError, match="FAILED 必须写明原因"):
        _claim(state=MaintenanceState.FAILED, finished_at=NOW)


def test_a_receipt_can_be_retried_until_it_is_done() -> None:
    """``CLAIMED`` **不按时钟**自动失效 —— 与 ``AttemptState`` 同一条纪律。

    猜错「那个 worker 死了」的代价是两件昂贵的事同时在做，而账上只显示
    「做了两次」。所以重试的入口是「不是 DONE」，而不是「已经过了 N 分钟」。
    """
    assert _claim().retryable is True
    assert _claim(state=MaintenanceState.FAILED, finished_at=NOW, failure="network").retryable is True
    assert _claim(state=MaintenanceState.DONE, finished_at=NOW).retryable is False


def test_a_receipt_that_exists_has_been_attempted_at_least_once() -> None:
    """``attempt`` 从 1 起 —— 一条存在的回执一定是被认领过的。

    身份是 ``(dedupe_key, attempt)``：去重身份由键承载，尝试次数由行数承载。
    从 0 起会让「这是第几次」在第一次重试之前没有答案，而
    「这一格核过几次、失败过几次」正是那个不能永远是 1 的问题。
    """
    with pytest.raises(ValueError, match="attempt 从 1 起"):
        _claim(attempt=0)
    with pytest.raises(ValueError, match="没有键的回执无法被去重"):
        _claim(dedupe_key="   ")


def test_claiming_due_work_carries_the_cell_and_why_it_came_due() -> None:
    """``claim_of`` 是「到期」到「被认领」之间唯一的门，它不许丢字段。

    这一条曾经真的是红的：构造函数漏了 ``reason``，而 ``reason`` 是必填 ——
    于是**每一件**到期工作都造不出回执，调度器一件也认领不了。断言读的是
    ``what_is_due`` 真的选出来的那一件，不是手搓的 ``DueWork``，所以「选择器
    与回执构造器之间的字段名漂了」也会红。

    ``reason`` 逐字带过来而不是重判：到这里已经没有选择器看过的那些输入了。
    """
    stale = _observation(observed_at=NOW - timedelta(days=CADENCE + 1))
    cadence = _cadence(_select(observations=(stale,), now=NOW))[0]
    claimed = claim_of(cadence, workspace=WORKSPACE, at=NOW)

    assert claimed.reason is DueReason.CADENCE_ELAPSED
    assert (claimed.dedupe_key, claimed.scene_id, claimed.period) == (
        cadence.dedupe_key,
        cadence.scene_id,
        cadence.period,
    )
    assert (claimed.subject_kind, claimed.subject_id, claimed.aspect) == (
        cadence.subject_kind,
        cadence.subject_id,
        cadence.aspect,
    )
    assert claimed.state is MaintenanceState.CLAIMED
    assert claimed.claimed_at == NOW

    # 被一条变更信号提前拉过来的那一格，回执上要读得出是**另一种**到期。
    fresh = _observation(aspect=Aspect.ACCOUNT_RELATION, observed_at=NOW - timedelta(days=1))
    signalled = _signalled(
        _select(observations=(fresh,), signals=(_signal(aspect=Aspect.ACCOUNT_RELATION),), now=NOW)
    )[0]
    assert claim_of(signalled, workspace=WORKSPACE, at=NOW).reason is DueReason.CHANGE_SIGNALLED


# --- 状态：§11 那五个字段的读法 ----------------------------------------------


def _states(
    *,
    observations: tuple[Observation, ...] = (),
    signals: tuple[ChangeSignal, ...] = (),
    now: datetime = NOW,
    subjects: tuple[tuple[str, str], ...] = (),
    plan: RefreshPlan | None = None,
) -> tuple[AspectState, ...]:
    return aspect_states(
        plan=_plan() if plan is None else plan,
        observations=observations,
        signals=signals,
        now=now,
        subjects=subjects,
    )


def _cell_of(states: tuple[AspectState, ...], aspect: Aspect) -> AspectState:
    (state,) = [s for s in states if s.aspect is aspect]
    return state


def test_a_cell_inside_its_cadence_names_the_next_time_it_will_be_due() -> None:
    """**没到期的格子也有状态** —— 而它给出的「下一次」是可算的，不是一个空。"""
    seen = _observation(observed_at=NOW - timedelta(days=1))

    state = _cell_of(
        _states(observations=(seen,), subjects=(("account", "probe-001"),)),
        Aspect.SOURCE_COVERAGE,
    )

    assert state.status is AspectStatus.CURRENT
    assert state.stale_reason is None, "CURRENT 是「它不旧」，不是「它旧得没有理由」"
    assert state.last_verified_at == NOW - timedelta(days=1)
    assert state.next_due_at == NOW - timedelta(days=1) + timedelta(days=CADENCE)
    assert state.policy_version == _plan().policy_version


def test_a_cell_that_was_never_observed_has_no_next_time_at_all() -> None:
    """它**此刻就是到期的**，而 ``next_due_at`` 仍然是 ``None``。

    不写 ``now``：那样每读一次它就变一次，而「什么都没变」的东西不该有一个每秒都动
    的值。这一格没有过任何节奏，于是没有「下一次」可写 —— 它欠的是**第一次**。
    """
    state = _cell_of(_states(subjects=(("account", "probe-001"),)), Aspect.PRODUCT_FIT)

    assert state.status is AspectStatus.NEVER_OBSERVED
    assert state.stale_reason is DueReason.NEVER_OBSERVED
    assert state.last_verified_at is None
    assert state.next_due_at is None


def test_an_overdue_cell_reports_the_boundary_it_is_sitting_past() -> None:
    """拖了三轮，报的是**本轮逾期的起点** —— 与 ``DueWork.due_at`` 是同一个取值。

    这一条压着两件事：那个取值是哪一个时刻，以及「两份读法共用同一次判定」——
    两处各算一遍的话，第一次改动就会让它们分家，而分家的表现是报告上「这格是新的」
    与队列里「这格要做」并存。

    值**不是**「第一次错过的那一刻」：看这一格拖了三轮，容易被读成「从第一轮起就没
    人管」，而报告要能回答的是「它现在等了多久」。取值低于 ``now`` 是必然的（它已经
    逾期），而与 ``now`` 的差**恒小于一个周期**：那是「本轮等了多久」，
    跨轮的总时长由 ``period="cycle-3"`` 那一侧记，不在这个字段里。
    """
    seen = _observation(observed_at=NOW - timedelta(days=CADENCE * 3 + 1))
    subject = ("account", "probe-001")

    (item,) = [
        work
        for work in _select(observations=(seen,), subjects=(subject,))
        if work.aspect is Aspect.SOURCE_COVERAGE
    ]
    state = _cell_of(_states(observations=(seen,), subjects=(subject,)), Aspect.SOURCE_COVERAGE)

    assert state.status is AspectStatus.DUE
    assert state.stale_reason is DueReason.CADENCE_ELAPSED
    assert state.next_due_at == item.due_at, "两份读法对同一格给出的「到期时刻」必须是同一个值"
    assert state.next_due_at == seen.observed_at + timedelta(days=CADENCE * 3), (
        "第三个周期边界（第 90 天），不是第一个（第 30 天）"
    )
    assert NOW - timedelta(days=CADENCE) < state.next_due_at < NOW


def test_one_cell_keeps_one_work_with_both_reasons() -> None:
    """一格同时「时限到了」与「有人报了变动」时：**工作一件，理由两个**。

    工作那一侧是 §11 定的（「合并必要工作」）：两个理由说的是同一件必要重查，
    而必要重查只有一次。理由那一侧是状态读法定的 —— 状态是给人读的，
    「这一格为什么是旧的」说两个理由等于让读的人自己排优先级；留下按次序取，
    见 ``_STALE_PRECEDENCE``：世界说过话的时候，读的人要看到的是那一句。

    两者并不矛盾：`stale_reason` 是 ``DueWork.reasons`` 的**头一个**，
    而不是「这一格只有一个理由」。
    """
    stale = _observation(observed_at=NOW - timedelta(days=CADENCE * 3))
    signal = _signal(signalled_at=NOW - timedelta(days=2))
    subject = ("account", "probe-001")

    mine = [
        work
        for work in _select(observations=(stale,), signals=(signal,), subjects=(subject,))
        if work.aspect is Aspect.SOURCE_COVERAGE
    ]
    state = _cell_of(
        _states(observations=(stale,), signals=(signal,), subjects=(subject,)),
        Aspect.SOURCE_COVERAGE,
    )

    assert len(mine) == 1, "同一格的两个理由被拆成了两件工作 —— 那正是 §11 要合并的"
    assert mine[0].reasons == (DueReason.CHANGE_SIGNALLED, DueReason.CADENCE_ELAPSED)
    assert state.stale_reason is mine[0].reason is DueReason.CHANGE_SIGNALLED
    assert state.next_due_at == mine[0].due_at
    # ``due_at`` 取的是**最早**的那条理由，于是报告上读得出「这一件从什么时候起
    # 该做」：这里信号比第三个周期边界早两天，所以值班的人看到的是信号那一刻。
    # 取最强理由的时刻会让它显示成周期边界，也就是**比实际更晚**。
    assert state.next_due_at == signal.signalled_at
    assert state.next_due_at < stale.observed_at + timedelta(days=CADENCE * 3)


def test_the_states_and_the_selector_agree_on_every_cell() -> None:
    """同一份判定的两种读法。**对不上的那一格才是缺陷。**

    两边逐格对上不是巧合，也不该被读成「两个函数碰巧一致」：状态那一侧是**照着**
    选择器的结论写的（它调的就是选择器）。这一条压的是那句话本身 —— 将来谁把两边
    各写一遍（比如为了让状态不必算全部到期工作），这一格会转红。
    """
    subject = ("account", "probe-001")
    observations = (
        _observation(observed_at=NOW - timedelta(days=1)),  # 还新
        _observation(aspect=Aspect.ASSET_IDENTITY, observed_at=NOW - timedelta(days=CADENCE * 2)),
        _observation(aspect=Aspect.PRODUCT_FIT, observed_at=NOW - timedelta(days=CADENCE * 2)),
    )
    signals = (
        _signal(aspect=Aspect.PRODUCT_FIT, signalled_at=NOW - timedelta(days=1)),
    )

    states = _states(observations=observations, signals=signals, subjects=(subject,))
    due = _select(observations=observations, signals=signals, subjects=(subject,))

    assert {(s.subject_kind, s.subject_id, s.aspect) for s in states} == {
        (subject[0], subject[1], aspect) for aspect in ALL_ASPECTS
    }, "格子空间是「已知主体 × 全部七个面」，与选择器的口径一致"

    due_cells = {(w.subject_kind, w.subject_id) for w in due}
    assert due_cells == {(subject[0], subject[1])}, "这一份夹具里只有一个主体"
    by_cell: dict[tuple[str, str, Aspect], list[DueWork]] = {}
    for work in due:
        by_cell.setdefault((work.subject_kind, work.subject_id, work.aspect), []).append(work)

    for state in states:
        key = (state.subject_kind, state.subject_id, state.aspect)
        works = by_cell.get(key, [])
        assert (state.status is not AspectStatus.CURRENT) == bool(works), (
            f"{key[2].value}：状态说 {state.status.value}，选择器给了 {len(works)} 件工作"
        )
        if works:
            assert state.stale_reason in {w.reason for w in works}
            if state.status is AspectStatus.DUE:
                assert state.next_due_at == min(w.due_at for w in works)
            else:
                assert state.next_due_at is None, "从未核过时没有「下一次」可写，哪怕它此刻就是到期的"

    # 那一格同时被信号与时限拉过：工作一件，两个理由都在（§11「保留全部原因」）。
    pulled = by_cell[(subject[0], subject[1], Aspect.PRODUCT_FIT)]
    assert [work.reasons for work in pulled] == [
        (DueReason.CHANGE_SIGNALLED, DueReason.CADENCE_ELAPSED)
    ]
    assert pulled[0].reason is _cell_of(states, Aspect.PRODUCT_FIT).stale_reason, (
        "两份读法对「最强的那个理由」必须是同一个"
    )


def test_a_state_cannot_be_current_and_stale_at_the_same_time() -> None:
    """状态与理由是同一件事的两面 —— 自相矛盾的行不许被构造出来。

    读法这一侧最容易出的错不是算错，而是**两个字段各自算一遍**：于是会出现
    「``CURRENT`` 却带着一个理由」这种行。它读起来像「它既新又旧」，而在库里、
    在报告里都不会自己报错。
    """
    with pytest.raises(ValidationError, match="互相矛盾"):
        AspectState(
            subject_kind="account",
            subject_id="probe-001",
            aspect=Aspect.SOURCE_COVERAGE,
            status=AspectStatus.CURRENT,
            stale_reason=DueReason.CADENCE_ELAPSED,
        )


def test_a_never_observed_cell_cannot_carry_a_verification_time() -> None:
    """「从来没核过」与「上次核过是某一天」不能同时成立。"""
    with pytest.raises(ValidationError, match="到底核过没有"):
        AspectState(
            subject_kind="account",
            subject_id="probe-001",
            aspect=Aspect.SOURCE_COVERAGE,
            last_verified_at=NOW,
            status=AspectStatus.NEVER_OBSERVED,
            stale_reason=DueReason.NEVER_OBSERVED,
        )


def test_the_reason_a_state_keeps_is_chosen_from_a_table_that_covers_all_of_them() -> None:
    """``_STALE_PRECEDENCE`` 少写一个理由，症状是**运行时的裸 ValueError**。

    那一句在 ``aspect_states`` 里挂着 ``# pragma: no cover`` —— 它不可达的前提正是
    这一条：这张表覆盖 :class:`DueReason` 的每一个取值，且不重复。少了谁，那一格
    的读法就会在某个真实场景下炸，而不是在这里转红。
    """
    from marketops.domain.maintenance import _STALE_PRECEDENCE

    assert set(_STALE_PRECEDENCE) == set(DueReason)
    assert len(_STALE_PRECEDENCE) == len(DueReason), "重复的成员会让次序失去意义"


def test_the_policy_version_follows_the_numbers_and_nothing_else() -> None:
    """同一组数 ⇒ 同一个版本；改一个数或改一个标定 ⇒ 换一个。

    它回答的是「两次读到的策略是同一份吗」，所以**必须是内容**的函数：一个递增计数
    会让同一条内容在两次部署里拿到两个号（看起来像变过）。
    """
    plan = _plan()

    assert _plan().policy_version == plan.policy_version
    assert len(plan.policy_version) == 16
    assert _plan(after_days=CADENCE + 1).policy_version != plan.policy_version
    assert _plan(calibrated=True).policy_version != plan.policy_version, (
        "「180 天、未标定」与「180 天、已标定」是两份不同的策略："
        "一个说「是这几组数」，一个说「有人认过这几组数」"
    )
    assert _plan(after_days=CADENCE, calibrated=False).policy_version == plan.policy_version


def test_the_states_come_out_in_a_pinned_order() -> None:
    """主体按名字、面按 ``ALL_ASPECTS`` —— 两次读的报告可以逐行比对。"""
    states = _states(subjects=(("account", "probe-b"), ("account", "probe-a")))

    assert len(states) == 2 * len(ALL_ASPECTS)
    assert [state.subject_id for state in states] == ["probe-a"] * 7 + ["probe-b"] * 7
    assert [state.aspect for state in states[:7]] == list(ALL_ASPECTS)

"""切片 d：增量 —— 第二轮复用已沉淀事实，只补新/缺/过期。

权威：``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:256`` 条件 1 逐字：

    同一 Workstream 连续两个 manual Run：第二轮明确复用第一轮已沉淀事实，只补新/
    缺/过期；重复提交同一 idempotency key 不重复池子或成本。

四格里**三格已经在盘上**：``domain/maintenance.py::aspect_states`` 给每一格（含
没到期的）一个 ``AspectStatus``，它的 docstring 逐字说「它是**读法**，不是第二处
判定」。所以本片的判据大多在判同一件事：**这里没有第二处判定**，读的是
``aspect_states`` 与 ``refresh.census_for``。唯一的第二根轴是「盘点」。

本片最承重的一条判据是
:func:`test_a_fresh_subject_with_no_source_is_missing_not_reuse` —— 天真的划分
（``works = NEVER_OBSERVED ∪ DUE``，也就是今天的 ``what_is_due``）会把一个**一条
依据都没有**的主体报成**复用**，而「复用」是一句「有东西可复用」的话。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from marketops.adapters.memory import InMemoryEvidenceStore
from marketops.application.run_increment import (
    _VERDICT_OF_STATUS,
    IncrementCell,
    IncrementVerdict,
    RunIncrement,
    plan_increment,
)
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus, SourceKind
from marketops.domain.evidence import Evidence
from marketops.domain.errors import RunStateError
from marketops.domain.maintenance import (
    ALL_ASPECTS,
    Aspect,
    AspectStatus,
    Observation,
    RefreshPlan,
)
from marketops.domain.run import Run, RunBudgetLine, RunSpec, spec_fingerprint
from marketops.domain.run import run_id as derive_run_id
from marketops.domain.run_delta import RunDelta

WORKSPACE = "ws-increment"
OTHER_WORKSPACE = "ws-elsewhere"
SELECTOR = "selector-alpha"
KEY = "idem-alpha"
ACCOUNT = "account-alpha"
VENUE = "venue-alpha"

CONTEXT_REF = "a" * 64
PLAN_REF = "b" * 64

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
#: 上一轮看过、还没到任何一格的窗口（最短的窗口是 30 天）。
FRESH = NOW - timedelta(days=1)
#: 上一轮看过、已经超过最长的窗口（180 天）。
STALE = NOW - timedelta(days=200)
CUTOFF = NOW + timedelta(days=30)


def _observation(
    aspect: Aspect,
    *,
    subject: str = ACCOUNT,
    kind: str = "account",
    at: datetime = FRESH,
    workspace: str = WORKSPACE,
) -> Observation:
    return Observation(
        workspace=workspace,
        subject_kind=kind,
        subject_id=subject,
        aspect=aspect,
        observed_at=at,
        source_ref="run-previous",
    )


def _fresh(
    *, subject: str = ACCOUNT, kind: str = "account", workspace: str = WORKSPACE
) -> tuple[Observation, ...]:
    """七格都看过、都还没到期 —— 也就是「上一轮已经把这一行沉淀下来了」。"""
    return tuple(
        _observation(a, subject=subject, kind=kind, workspace=workspace)
        for a in ALL_ASPECTS
    )


def _evidence(index: int, kind: SourceKind, *, subject: str = ACCOUNT) -> Evidence:
    return Evidence(
        workspace=WORKSPACE,
        evidence_id=f"ev-{index}",
        account_id=subject,
        source_url=f"https://example.co.uk/page-{index}",
        source_kind=kind,
        literal_value="grounds@example.co.uk",
        content_sha256=f"sha-{index}",
        fetched_at=FRESH,
    )


def _store(*rows: Evidence) -> InMemoryEvidenceStore:
    store = InMemoryEvidenceStore()
    for row in rows:
        store.put_evidence(row)
    return store


class _CountingStore(InMemoryEvidenceStore):
    """数「盘点被问了几次、问的是谁」。

    **它是本片唯一能看见「谁被问了」的量具。** 「到期的那一格不去读盘」与「别的面
    不问盘点」这两句话，除了它没有别的执行器 —— 断言结论相同（都是 MISSING）分不出
    「没问」与「问了、答没有」。
    """

    def __init__(self) -> None:
        super().__init__()
        self.listed: list[str] = []

    def list_evidence(self, account_id: str) -> list[Evidence]:
        self.listed.append(account_id)
        return super().list_evidence(account_id)


def _spec(
    *, workspace: str = WORKSPACE, selector: str = SELECTOR, key: str = KEY
) -> RunSpec:
    return RunSpec(
        workspace=workspace,
        objective="复核场地资产",
        selector=selector,
        context_ref=CONTEXT_REF,
        plan_ref=PLAN_REF,
        execution_mode=ExecutionMode.MANUAL,
        window=None,
        budget=(RunBudgetLine(line=BudgetLine.SEARCH, ceiling=10),),
        cutoff=CUTOFF,
        policy_versions=("refresh-2026-09",),
        idempotency_key=key,
    )


def _run(*, stopped: bool = False, **kwargs: object) -> Run:
    spec = _spec(**kwargs)  # type: ignore[arg-type]
    return Run(
        run_id=derive_run_id(spec.workspace, spec.idempotency_key),
        spec=spec,
        spec_sha256=spec_fingerprint(spec),
        created_at="2026-09-28T11:00:00+00:00",
        updated_at="2026-09-28T11:30:00+00:00",
        stop_reason=RunStatus.PARTIAL if stopped else None,
        delta=RunDelta() if stopped else None,
        stopped_at="2026-09-28T11:30:00+00:00" if stopped else None,
    )


def _plan_increment(**overrides: object) -> RunIncrement:
    kwargs: dict[str, object] = {
        "run": _run(),
        "plan": RefreshPlan.uncalibrated_default(),
        "observations": (),
        "evidence_store": None,
        "now": NOW,
    }
    kwargs.update(overrides)
    return plan_increment(**kwargs)  # type: ignore[arg-type]


def _cells(increment: RunIncrement) -> dict[Aspect, IncrementCell]:
    return {cell.aspect: cell for cell in increment.cells}


def _gap_reason(increment: RunIncrement) -> str:
    return _cells(increment)[Aspect.SOURCE_COVERAGE].reason


# --------------------------------------------------------------------------- #
# 四格是一个划分：每一格恰好一个结论
# --------------------------------------------------------------------------- #


def test_every_cell_of_a_known_subject_gets_exactly_one_verdict() -> None:
    increment = _plan_increment(observations=_fresh(), evidence_store=_store())
    keys = [(c.subject_kind, c.subject_id, c.aspect) for c in increment.cells]
    assert len(keys) == len(ALL_ASPECTS)
    assert len(set(keys)) == len(keys)
    assert {key[2] for key in keys} == set(ALL_ASPECTS)


def test_the_cells_run_in_the_order_aspect_states_produces_them() -> None:
    """次序不在这里定义一遍。

    ``aspect_states`` 的收尾逐字写着「构造次序**就是**返回值次序（``sorted(known)``
    × ``ALL_ASPECTS``），所以这里不再排一遍：排两遍等于把「次序是什么」写成两处，
    而两处迟早会有一处被改动」。本片照抄那条次序。
    """
    increment = _plan_increment(
        observations=_fresh() + _fresh(subject=VENUE, kind="venue"),
        evidence_store=None,
    )
    assert [(c.subject_kind, c.subject_id, c.aspect) for c in increment.cells] == [
        (kind, subject_id, aspect)
        for kind, subject_id in (("account", ACCOUNT), ("venue", VENUE))
        for aspect in ALL_ASPECTS
    ]


def test_a_subject_nobody_declared_and_nobody_observed_is_not_a_cell() -> None:
    """格子空间是闭的（``known = 观测里出现过的 ∪ 显式声明的``）。

    「不在格子里」与「复用」是两件事：一个从未声明、从未观测过的对象**不在这一份
    读法里**，把它读成复用正是切片 c 那个错的反面。
    """
    increment = _plan_increment(observations=_fresh(), evidence_store=_store())
    assert {c.subject_id for c in increment.cells} == {ACCOUNT}


def test_a_declared_subject_with_no_observations_is_still_a_cell() -> None:
    increment = _plan_increment(
        observations=(),
        subjects=(("account", ACCOUNT),),
        evidence_store=_store(),
    )
    assert {c.subject_id for c in increment.cells} == {ACCOUNT}
    assert len(increment.cells) == len(ALL_ASPECTS)


def test_an_increment_over_an_empty_pool_is_an_empty_increment() -> None:
    """一个刚起的部署没有主体，这一份读法就是空的 —— 空不是「都在复用」。"""
    increment = _plan_increment(observations=(), subjects=(), evidence_store=None)
    assert increment.cells == ()
    assert increment.works == ()


# --------------------------------------------------------------------------- #
# 「复用」是一句「有东西可复用」的话
# --------------------------------------------------------------------------- #


def test_a_fresh_subject_with_a_real_source_reuses_the_whole_row() -> None:
    """条件 1 的正面读法：第二轮一格都不用补。"""
    increment = _plan_increment(
        observations=_fresh(),
        evidence_store=_store(_evidence(1, SourceKind.FETCHED_HTML)),
    )
    assert {c.verdict for c in increment.cells} == {IncrementVerdict.REUSE}
    assert increment.works == ()


def test_a_fresh_subject_with_no_source_is_missing_not_reuse() -> None:
    """**本片的承重判据。**

    这一格在钟上处处 CURRENT（上一轮看过、还没到期），而名下**一条能当依据的来源
    都没有**。天真的划分（``works = NEVER_OBSERVED ∪ DUE``，也就是今天的
    ``what_is_due``）会把它整个报成复用 —— 而「复用」是一句「有东西可复用」的话，
    那里没有东西。盘子空着这一件事，钟只会把它推得更远，不会让它露出来。
    """
    increment = _plan_increment(observations=_fresh(), evidence_store=_store())
    cells = _cells(increment)
    assert cells[Aspect.SOURCE_COVERAGE].verdict is IncrementVerdict.MISSING
    assert IncrementVerdict.REUSE not in {
        cell.verdict for aspect, cell in cells.items() if aspect is Aspect.SOURCE_COVERAGE
    }
    assert [c.aspect for c in increment.works] == [Aspect.SOURCE_COVERAGE]


def test_snippets_alone_do_not_close_the_source_gap() -> None:
    """手上的 N 条 ``SEARCH_SNIPPET`` 不是 N 笔待办，它们是一件事。"""
    increment = _plan_increment(
        observations=_fresh(),
        evidence_store=_store(
            *[_evidence(i, SourceKind.SEARCH_SNIPPET) for i in range(1, 4)]
        ),
    )
    assert _cells(increment)[Aspect.SOURCE_COVERAGE].verdict is IncrementVerdict.MISSING
    assert len(increment.works) == 1


@pytest.mark.parametrize(
    "kind",
    [SourceKind.FETCHED_HTML, SourceKind.FETCHED_PDF, SourceKind.FETCHED_TEXT],
    ids=["html", "pdf", "text"],
)
def test_any_bindable_source_kind_closes_the_gap(kind: SourceKind) -> None:
    """判据是「在不在 ``BINDING_ELIGIBLE_SOURCES`` 里」，不是「抓的是哪一种」。"""
    increment = _plan_increment(
        observations=_fresh(), evidence_store=_store(_evidence(1, kind))
    )
    assert _cells(increment)[Aspect.SOURCE_COVERAGE].verdict is IncrementVerdict.REUSE


def test_a_missing_store_is_a_gap_and_not_an_error() -> None:
    """``store`` 为 ``None`` 是「库文件还不存在」的诚实形态 —— 一次**读到了空**，
    不是一次读不到。两者的欠账相同（都是 1 笔），分开的是诊断面。"""
    increment = _plan_increment(observations=_fresh(), evidence_store=None)
    assert _cells(increment)[Aspect.SOURCE_COVERAGE].verdict is IncrementVerdict.MISSING


def test_the_gap_says_whether_the_store_was_there() -> None:
    """库不在与库在而空空如也：欠的账一样，读的人要分得出来。"""
    absent = _plan_increment(observations=_fresh(), evidence_store=None)
    present = _plan_increment(observations=_fresh(), evidence_store=_store())
    assert _gap_reason(absent) != _gap_reason(present)


# --------------------------------------------------------------------------- #
# 判据的优先级：钟先判，盘点只在 CURRENT 上问
# --------------------------------------------------------------------------- #


def test_the_clock_table_covers_every_status_that_is_not_current() -> None:
    """``CURRENT`` 是**唯一**一档还要问盘盘的，其余每一档都必须在那张表里。

    照 ``domain/maintenance.py::_STALE_PRECEDENCE`` 的先例（由判据钉住它覆盖全部
    理由，而不是靠读的人对着枚举数一遍）。少了一档的实现会让它静默落进「复用」——
    而「复用」是一句「有东西可复用」的话。
    """
    assert set(_VERDICT_OF_STATUS) == set(AspectStatus) - {AspectStatus.CURRENT}
    assert _VERDICT_OF_STATUS[AspectStatus.NEVER_OBSERVED] is IncrementVerdict.NEW
    assert _VERDICT_OF_STATUS[AspectStatus.DUE] is IncrementVerdict.EXPIRED


def test_the_census_is_asked_only_about_source_coverage() -> None:
    """别的面不问盘点。

    ``application/refresh.py`` 逐字写着「``SOURCE_COVERAGE`` 那一面的主语就是**我们
    手上有哪些来源**」。拿盘点去问别的面，是把两个主语混成一个。
    """
    store = _CountingStore()
    increment = _plan_increment(
        observations=_fresh(), evidence_store=store
    )
    assert len(increment.cells) == len(ALL_ASPECTS)
    assert store.listed == [ACCOUNT]


def test_the_census_is_not_asked_about_a_cell_that_is_not_current() -> None:
    """到期的那一格由钟说了算，不去读盘 —— 它已经是答案了。"""
    store = _CountingStore()
    increment = _plan_increment(
        observations=(_observation(Aspect.SOURCE_COVERAGE, at=STALE),),
        evidence_store=store,
    )
    assert _cells(increment)[Aspect.SOURCE_COVERAGE].verdict is IncrementVerdict.EXPIRED
    assert store.listed == []


def test_a_cell_that_was_never_observed_is_new_even_with_an_empty_plate() -> None:
    """「新」压过「缺」。

    「缺」的前提是「我们看过、结论是没有」；从没看过的那一格，欠的是**第一次去看**，
    而它同样是空盘子。两句话在这里指向同一件事，但划分必须挑一个 —— 钟那一格是
    关于这一格状态的原话，盘点只在钟说 CURRENT 时才有东西可问。
    """
    increment = _plan_increment(
        observations=(), subjects=(("account", ACCOUNT),), evidence_store=_store()
    )
    assert _cells(increment)[Aspect.SOURCE_COVERAGE].verdict is IncrementVerdict.NEW


# --------------------------------------------------------------------------- #
# 四句必须互不相同
# --------------------------------------------------------------------------- #


def test_the_four_verdicts_are_four_different_things() -> None:
    """一格一种，四句理由必须分得开。

    「处置不同，合成一句会让其中一个永远没人查」（``reuse`` 那一族的同一条纪律）。
    这一份增量里四格同时在场：没看过的、过期的、欠来源的、可以复用的。
    """
    increment = _plan_increment(
        observations=(
            _observation(Aspect.SOURCE_COVERAGE),
            _observation(Aspect.ASSET_IDENTITY),
            _observation(Aspect.ASSET_ATTRIBUTE),
            _observation(Aspect.ACCOUNT_RELATION),
            _observation(Aspect.CONTACT_PERSON_ROLE),
            _observation(Aspect.EMAIL_EVIDENCE, at=STALE),
        ),
        evidence_store=_store(_evidence(1, SourceKind.SEARCH_SNIPPET)),
    )
    cells = _cells(increment)
    assert cells[Aspect.SOURCE_COVERAGE].verdict is IncrementVerdict.MISSING
    assert cells[Aspect.ASSET_IDENTITY].verdict is IncrementVerdict.REUSE
    assert cells[Aspect.EMAIL_EVIDENCE].verdict is IncrementVerdict.EXPIRED
    assert cells[Aspect.PRODUCT_FIT].verdict is IncrementVerdict.NEW
    assert len({c.reason for c in increment.cells}) == 4
    assert all(c.reason for c in increment.cells)


def test_works_is_exactly_the_cells_that_are_not_reuse() -> None:
    increment = _plan_increment(
        observations=_fresh() + _fresh(subject=VENUE, kind="venue"),
        evidence_store=_store(_evidence(1, SourceKind.FETCHED_HTML)),
    )
    assert list(increment.works) == [
        c for c in increment.cells if c.verdict is not IncrementVerdict.REUSE
    ]
    assert [c.subject_id for c in increment.works] == [VENUE]
    assert increment.counts == {
        IncrementVerdict.REUSE: 13,
        IncrementVerdict.NEW: 0,
        IncrementVerdict.MISSING: 1,
        IncrementVerdict.EXPIRED: 0,
    }


def test_every_verdict_is_counted_even_at_zero() -> None:
    """四格都出现，零也出现。

    缺一格零会让「这一轮没有过期的」与「这一轮没测过期」在报告上长得一样 ——
    本仓对这件事的处置写在 ``EvidenceCensus.not_measured`` 上：说不出来就写 null
    并说清为什么，绝不让缺席看起来像零。
    """
    increment = _plan_increment(observations=_fresh(), evidence_store=_store())
    assert set(increment.counts) == set(IncrementVerdict)


# --------------------------------------------------------------------------- #
# 两个时间格照抄 ``AspectState`` 的形状
# --------------------------------------------------------------------------- #


def test_the_two_time_fields_are_empty_exactly_for_the_never_observed_cells() -> None:
    """``aspect_states`` 已经钉住这条形状（``NEVER_OBSERVED`` 的两个时间格都是
    ``None``），本片照抄，不自己定一遍。"""
    increment = _plan_increment(
        observations=(_observation(Aspect.PRODUCT_FIT),), evidence_store=_store()
    )
    assert {c.verdict for c in increment.cells} == {
        IncrementVerdict.NEW,
        IncrementVerdict.REUSE,
    }
    for cell in increment.cells:
        never = cell.verdict is IncrementVerdict.NEW
        assert (cell.last_verified_at is None) is never
        assert (cell.next_due_at is None) is never


def test_a_current_cell_says_when_it_will_be_due_next() -> None:
    plan = RefreshPlan.uncalibrated_default()
    increment = _plan_increment(
        observations=(_observation(Aspect.PRODUCT_FIT),),
        evidence_store=None,
        plan=plan,
    )
    cell = _cells(increment)[Aspect.PRODUCT_FIT]
    assert cell.last_verified_at == FRESH
    assert cell.next_due_at == FRESH + timedelta(
        days=plan.rule_for(Aspect.PRODUCT_FIT).after_days
    )


def test_an_expired_cell_reports_the_start_of_this_rounds_overdue() -> None:
    """报的是**本轮逾期的起点**，不是「拖到第几轮」—— 与 ``DueWork.due_at`` 同一个
    取值（``aspect_states`` 的注释逐字写了这件事）。"""
    increment = _plan_increment(
        observations=(_observation(Aspect.PRODUCT_FIT, at=STALE),), evidence_store=None
    )
    cell = _cells(increment)[Aspect.PRODUCT_FIT]
    assert cell.verdict is IncrementVerdict.EXPIRED
    assert cell.last_verified_at == STALE
    assert cell.next_due_at is not None
    assert cell.next_due_at < NOW


def test_every_cell_carries_the_policy_version_of_the_read() -> None:
    """"§11 要求每一格都带着 ``policy_version``"（``RefreshPlan.policy_version``
    的 docstring 逐字）。于是它跟着格走，不是只在这一份读数上写一次。"""
    plan = RefreshPlan.uncalibrated_default()
    increment = _plan_increment(
        observations=_fresh(), evidence_store=_store(), plan=plan
    )
    assert {c.policy_version for c in increment.cells} == {plan.policy_version}


# --------------------------------------------------------------------------- #
# 开工之前那一句：条件 1 的后半句
# --------------------------------------------------------------------------- #


def test_a_stopped_run_is_refused_before_any_cell_is_produced() -> None:
    """条件 1 的后半句，逐字：「重复提交同一 idempotency key 不重复池子或成本」。

    ``admit`` 是幂等的 —— 同一个键重发拿回的是**同一行**，哪怕那一行已经结束。
    于是「重发同一个键」在**受理**上长得像成功，而它要挡的是第二轮真的又跑一遍：
    池子第二次折、成本第二次花，而报告上第一轮与第二轮逐字相同。挡它的位置只有
    一处 —— 开工之前。
    """
    run = _run(stopped=True)
    with pytest.raises(RunStateError) as caught:
        _plan_increment(run=run, observations=_fresh(), evidence_store=_store())
    assert run.run_id in str(caught.value)


def test_the_refusal_happens_before_the_store_is_read() -> None:
    store = _CountingStore()
    with pytest.raises(RunStateError):
        _plan_increment(
            run=_run(stopped=True), observations=_fresh(), evidence_store=store
        )
    assert store.listed == []


def test_the_increment_names_the_run_it_was_planned_for() -> None:
    run = _run()
    increment = _plan_increment(run=run, observations=_fresh(), evidence_store=_store())
    assert increment.run_id == run.run_id
    assert increment.workspace == run.spec.workspace
    assert increment.selector == run.spec.selector


# --------------------------------------------------------------------------- #
# 差量 3：``run_id`` 指着两个空间
# --------------------------------------------------------------------------- #


def test_the_increment_carries_the_content_addressed_run_id_not_the_label() -> None:
    """差量 3 的处置（``docs/B17_DESIGN_SLICES_2026-09-28.md:35-45``）。

    ``run_id`` 这个名字今天指着两个空间：``domain.run.run_id()`` 的**内容寻址**
    32 位十六进制，与 ``application/pipeline.py::_derived_run_id()`` 造的
    ``run-20260926t101530`` 那种**标签**。本片明确让这一格是前者，**不**把它接到
    ``FetchAttempt`` / ``RunReport`` / ``ResearchRun`` / ``RouteContext`` 那四格上 ——
    那四格继续是「这一趟有名字」。把它们接起来是另一次改动：它要求「哪一轮取过哪
    些字节」真的可归因，而那条链今天拿不到 Run。
    """
    increment = _plan_increment(observations=(_fresh()[0],), evidence_store=None)
    assert increment.run_id == derive_run_id(WORKSPACE, KEY)
    assert len(increment.run_id) == 32
    assert not increment.run_id.startswith("run-")


def test_a_label_shaped_run_id_is_refused() -> None:
    """形状这一格是挡两个空间混在一列里的东西。

    ``test_the_four_id_spaces_do_not_collide`` 不覆盖它 —— 两者形状本来就不撞，
    那不是一次 id 冲突。挡住 ``run-20260926t101530`` 的唯一位置是这一格的校验器。
    """
    with pytest.raises(ValidationError):
        RunIncrement(
            run_id="run-20260926t101530",
            workspace=WORKSPACE,
            selector=SELECTOR,
            at=NOW,
            cells=(),
        )


# --------------------------------------------------------------------------- #
# 这一份读数自己的形状
# --------------------------------------------------------------------------- #


def test_an_increment_cannot_carry_two_verdicts_for_one_cell() -> None:
    """格子是一份**划分**：同一个 ``(主体, 面)`` 有两个结论，就是在这一点上说了两句
    不同的话，而 ``works`` 会把这一格数两遍。"""
    cell = IncrementCell(
        subject_kind="account",
        subject_id=ACCOUNT,
        aspect=Aspect.PRODUCT_FIT,
        verdict=IncrementVerdict.REUSE,
        reason="上一轮看过，窗口未到",
    )
    with pytest.raises(ValidationError):
        RunIncrement(
            run_id=derive_run_id(WORKSPACE, KEY),
            workspace=WORKSPACE,
            selector=SELECTOR,
            at=NOW,
            cells=(cell, cell),
        )


def test_a_cell_must_say_why() -> None:
    with pytest.raises(ValidationError):
        IncrementCell(
            subject_kind="account",
            subject_id=ACCOUNT,
            aspect=Aspect.PRODUCT_FIT,
            verdict=IncrementVerdict.REUSE,
            reason="",
        )


def test_the_increment_is_taken_at_an_aware_instant() -> None:
    with pytest.raises(ValidationError):
        RunIncrement(
            run_id=derive_run_id(WORKSPACE, KEY),
            workspace=WORKSPACE,
            selector=SELECTOR,
            at=datetime(2026, 9, 28, 12, 0),
            cells=(),
        )


def test_observations_from_another_workspace_are_refused() -> None:
    """复用的范围**止于工作区边界**（``Observation.workspace`` 是授权链的第一段）。

    读别人的观测、再把它们记成这一轮的复用，是一次不留痕的跨工作区读 —— 而报告上
    它看起来只是「复用得很充分」。
    """
    with pytest.raises(ValueError):
        _plan_increment(
            observations=_fresh(workspace=OTHER_WORKSPACE), evidence_store=_store()
        )

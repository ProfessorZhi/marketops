"""§B12 的冻结成员名单：**域层的判定**，以及它凭什么承重。

## 这一份测的是什么，不是什么

它测 ``domain/cohort.py`` 一个文件，不碰存储（那一份在
``tests/test_cohort_store_contract.py``）、不碰 MCP（那一份在
``tests/test_mcp_cohorts.py``）。分三份是因为三层的失败形态不同：域层判错是
「名单的内容不对」，存储写错是「同一次冻结落成两份」或「重开就读不到」，
接线写错是「报告说的那一份与真的读的那一份不是同一份」。

## 最承重的那一条：**身份是内容寻址的**

``cohort_id`` 是（工作区, 场景, 选择规则, 成员序列, 每个排除者的原因码）的一个
散列。这条判据在下面被拆成两个方向：

* **进原像的东西改了，id 必须改** —— 成员序列、选择规则、排除原因码。少任何一个，
  两套**不同**的名单会算出一个 id，而 ``put_cohort`` 是一次按 id 的覆盖写：后来
  那一份会**静默地**把前一份顶掉，报告上只显示「一份名单」。
* **没进原像的东西改了，id 必须不变** —— ``reason`` 的措辞、``created_at``、
  成员的 ``organisation``。多任何一个，一次文案改动就会造出一批内容一模一样、
  id 不同的名单，而 §B21 的「重放不重复昂贵工作」正是靠「同一个池子算得出同一个
  id」才成立。

两条都写成**成对**的用例，因为只有一条时反过来那半是没人看着的。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.domain.account import MarketAccount
from marketops.domain.cohort import (
    WORKABLE_STATUSES,
    AccountCohort,
    CohortExclusion,
    CohortExclusionCode,
    CohortMember,
    CohortSelection,
    cohort_id_for,
    exclusion_reason,
    freeze_cohort,
)
from marketops.domain.enums import AccountStatus

UTC = timezone.utc
T0 = datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-0123456789abcdef"


def account(account_id: str, status: AccountStatus, *, organisation: str = "") -> MarketAccount:
    """池子里的一行。只填这一条判据要看的那几格。"""
    return MarketAccount(
        workspace=WORKSPACE,
        account_id=account_id,
        organisation=organisation or f"Example {account_id}",
        status=status,
        evidence_ids=["ev-0000000000000001"],
        first_seen_at=T0,
        last_verified_at=T0,
    )


def frozen(
    rows: tuple[MarketAccount, ...],
    *,
    selection: CohortSelection | None = None,
    scene_id: str = SCENE,
    at: datetime = T0,
) -> AccountCohort:
    return freeze_cohort(
        rows,
        workspace=WORKSPACE,
        scene_id=scene_id,
        selection=selection or CohortSelection(),
        at=at,
    )


# ----------------------------------------------------------------------
# 状态那张表：每一个 AccountStatus 都有归宿
# ----------------------------------------------------------------------
def test_every_account_status_is_either_workable_or_has_an_exclusion_code() -> None:
    """**七个状态，一个都不许掉。**这正是那张表写成表而不是一串 ``if`` 的理由。

    ``freeze_cohort`` 对不在 ``WORKABLE_STATUSES`` 里的状态走
    ``_EXCLUSION_FOR_STATUS.get(status, STATUS_NOT_SELECTED)``。那个 ``.get`` 的
    缺省是一把**单向的**安全网：它保证不崩，但它认不出「有人新加了一个状态而没人
    想过它算不算可研究」—— 新状态会被静默归到「这一轮没选它」，而那是一句关于
    **选择规则**的话，不是关于那个状态的。

    所以这里把两个集合合起来问一遍：每一个 ``AccountStatus`` 要么在
    ``WORKABLE_STATUSES`` 里，要么在 ``_EXCLUSION_FOR_STATUS`` 里。新加一个成员时
    这一条先红，而红的话里点名的是那一个。
    """
    from marketops.domain.cohort import _EXCLUSION_FOR_STATUS

    every = set(AccountStatus)
    classified = set(WORKABLE_STATUSES) | set(_EXCLUSION_FOR_STATUS)
    assert every == classified, (
        f"这些状态既不在 WORKABLE_STATUSES 里、也没有原因码："
        f"{sorted(s.value for s in every - classified)}；"
        f"而这些状态两边都在（同一条判定有两个定义处）："
        f"{sorted(s.value for s in set(WORKABLE_STATUSES) & set(_EXCLUSION_FOR_STATUS))}"
    )


@pytest.mark.parametrize("status", sorted(set(AccountStatus) - set(WORKABLE_STATUSES), key=str))
def test_the_four_unworkable_statuses_each_get_their_own_code(status: AccountStatus) -> None:
    """四个不可研究的状态**各有各的码**，四个码互不相同。

    「它被排除了」是一个数字能表达的事，而 ``ON_HOLD`` 与 ``DUPLICATE`` 指向的
    下一件事完全不同（等人裁决 / 要人看一眼）。把它们折成一个码，读的人就只能
    凭数量猜。
    """
    from marketops.domain.cohort import _EXCLUSION_FOR_STATUS

    cohort = frozen((account("acct-0000000000000001", status),))
    assert cohort.members == ()
    assert [row.code for row in cohort.exclusions] == [_EXCLUSION_FOR_STATUS[status]]


def test_the_four_codes_are_pairwise_distinct() -> None:
    """四个状态给出的四个码互不相同 —— 上一条只保证「各自的表项被用上」。

    只测上一条的话，一张把四个状态全映射到 ``ON_HOLD`` 的表照样绿。
    """
    codes = [frozen((account("acct-0000000000000001", s),)).exclusions[0].code
             for s in set(AccountStatus) - set(WORKABLE_STATUSES)]
    assert len(set(codes)) == len(codes)


# ----------------------------------------------------------------------
# 选谁：只看 status 那一格
# ----------------------------------------------------------------------
@pytest.mark.parametrize("status", WORKABLE_STATUSES, ids=lambda s: s.value)
def test_a_workable_status_lands_in_the_roster(status: AccountStatus) -> None:
    """三个可研究的状态都进名单，且理由就是那一个状态本身。"""
    cohort = frozen((account("acct-0000000000000001", status),))
    assert [m.status for m in cohort.members] == [status]
    assert cohort.exclusions == ()


def test_a_workable_but_unselected_status_is_not_a_verdict() -> None:
    """**能研究 ≠ 这一轮选了它。**两个码分开，两句话分开。

    ``STATUS_NOT_SELECTED`` 说的是一条**选择规则**（换一条它就回来），
    ``NOT_RELEVANT`` 说的是一条**业务结论**（它不相关）。合成一个码，读的人会把
    「这一轮我只想复核已经研究过的那些」读成「系统判它们不相关」。
    """
    rows = (
        account("acct-0000000000000001", AccountStatus.ELIGIBLE),
        account("acct-0000000000000002", AccountStatus.IRRELEVANT),
    )
    cohort = frozen(rows, selection=CohortSelection(statuses=(AccountStatus.RESEARCHED,)))

    by_id = {row.account_id: row for row in cohort.exclusions}
    assert cohort.members == ()
    assert by_id["acct-0000000000000001"].code is CohortExclusionCode.STATUS_NOT_SELECTED
    assert by_id["acct-0000000000000002"].code is CohortExclusionCode.NOT_RELEVANT


def test_an_empty_selection_is_a_stated_zero_not_a_crash() -> None:
    """空的状态集是**合法的**：一份说得出为什么是空的零结果。

    每个账号都有一条 ``status`` 原因码，所以「这一批一个都不研究」在报告上是一句
    有依据的话，而不是一次崩溃或一个空名单。
    """
    rows = (account("acct-0000000000000001", AccountStatus.ELIGIBLE),)
    cohort = frozen(rows, selection=CohortSelection(statuses=()))

    assert cohort.members == ()
    assert [row.code for row in cohort.exclusions] == [CohortExclusionCode.STATUS_NOT_SELECTED]


def test_the_roster_follows_the_order_the_pool_gave() -> None:
    """成员顺序**照抄**调用方给的顺序，不重排。

    它决定 ``ordinal``，因而决定 ``work_item_id``（``application/inputs.py`` 逐字）。
    所以这个函数**不许**自己排一次 —— 池子那一侧已经按 ``account_id`` 稳定排序，
    两处各排一次的话，一次「池子改了排序」与「名单改了排序」在报告上同形。
    """
    rows = (
        account("acct-0000000000000003", AccountStatus.ELIGIBLE),
        account("acct-0000000000000001", AccountStatus.ELIGIBLE),
        account("acct-0000000000000002", AccountStatus.ELIGIBLE),
    )
    cohort = frozen(rows)
    assert [m.account_id for m in cohort.members] == [
        "acct-0000000000000003",
        "acct-0000000000000001",
        "acct-0000000000000002",
    ]


# ----------------------------------------------------------------------
# 上限：砍掉的是尾部，而且砍这件事本身要留痕
# ----------------------------------------------------------------------
def test_the_cap_drops_the_tail_and_says_so_per_row() -> None:
    """``OVER_LIMIT`` 是唯一一个**不由 status 推出**的码，而它必须逐行留下。

    它说的是「下一轮就在名单里」—— 与 ``NOT_RELEVANT`` 完全不同的处置。折成一个
    数字的话，读的人会把一次配置截断当成一次业务结论。
    """
    rows = tuple(
        account(f"acct-000000000000000{index}", AccountStatus.ELIGIBLE) for index in range(1, 5)
    )
    cohort = frozen(rows, selection=CohortSelection(limit=2))

    assert [m.account_id for m in cohort.members] == [
        "acct-0000000000000001",
        "acct-0000000000000002",
    ]
    assert [row.code for row in cohort.exclusions] == [
        CohortExclusionCode.OVER_LIMIT,
        CohortExclusionCode.OVER_LIMIT,
    ]
    assert [row.account_id for row in cohort.exclusions] == [
        "acct-0000000000000003",
        "acct-0000000000000004",
    ]


def test_a_cap_equal_to_the_row_count_is_not_a_truncation() -> None:
    """上限**正好等于**行数是「全都进来了」，不是一次截断。

    与 ``application/caps.py::apply_cap`` 逐字同一条：一个 ``len(items) == cap``
    也报截断的实现，会让每一份满员的名单都声称自己被砍过 —— 而「被砍过」是下一轮
    要重跑的理由。
    """
    rows = tuple(
        account(f"acct-000000000000000{index}", AccountStatus.ELIGIBLE) for index in range(1, 3)
    )
    cohort = frozen(rows, selection=CohortSelection(limit=2))
    assert len(cohort.members) == 2
    assert cohort.exclusions == ()


def test_no_cap_keeps_everything_and_marks_nothing() -> None:
    rows = tuple(
        account(f"acct-000000000000000{index}", AccountStatus.ELIGIBLE) for index in range(1, 4)
    )
    assert len(frozen(rows, selection=CohortSelection(limit=None)).members) == 3


def test_a_negative_cap_is_a_mistake_not_a_very_small_one() -> None:
    """负数在这里就拒。

    静默当成不封顶会让一次配置错误在报告上长得像「没设上限」，而两者指向的下一件事
    完全不同。
    """
    with pytest.raises(ValueError, match="负数"):
        CohortSelection(limit=-1)


def test_a_repeated_status_is_not_a_second_roster() -> None:
    """重复的状态成员去重 —— 否则 ``(ELIGIBLE, ELIGIBLE)`` 与 ``(ELIGIBLE,)`` 会算出
    两个 id，而它们选出来的是同一批行。

    后果不是崩溃：报告上只显示「有两份名单」，而两份内容一模一样。
    """
    once = CohortSelection(statuses=(AccountStatus.ELIGIBLE,))
    twice = CohortSelection(statuses=(AccountStatus.ELIGIBLE, AccountStatus.ELIGIBLE))
    assert once == twice
    assert once == CohortSelection(statuses=(AccountStatus.ELIGIBLE,))


# ----------------------------------------------------------------------
# 身份：进原像的必须改 id
# ----------------------------------------------------------------------
def _id(
    *,
    members: tuple[tuple[str, str], ...],
    exclusions: tuple[tuple[str, str], ...] = (),
    selection: CohortSelection | None = None,
    scene_id: str = SCENE,
) -> str:
    return cohort_id_for(
        workspace=WORKSPACE,
        scene_id=scene_id,
        selection=selection or CohortSelection(),
        members=tuple(
            CohortMember(account_id=aid, organisation=name, status=AccountStatus.ELIGIBLE)
            for aid, name in members
        ),
        exclusions=tuple(
            CohortExclusion(
                account_id=aid,
                organisation="Example",
                status=AccountStatus.HOLD,
                code=CohortExclusionCode(code),
                reason=exclusion_reason(AccountStatus.HOLD, CohortExclusionCode(code)),
            )
            for aid, code in exclusions
        ),
    )


def test_the_id_is_the_same_for_the_same_reading() -> None:
    """同一个池子、同一条规则算两次是同一个 id —— 这就是「重放不重复昂贵工作」。"""
    assert _id(members=(("acct-0000000000000001", "A"),)) == _id(
        members=(("acct-0000000000000001", "A"),)
    )


def test_the_member_order_changes_the_id() -> None:
    """顺序改了就是另一份名单 —— 因为顺序决定 ``ordinal``，因而决定工作单元。"""
    assert _id(members=(("acct-0000000000000001", "A"), ("acct-0000000000000002", "B"))) != _id(
        members=(("acct-0000000000000002", "B"), ("acct-0000000000000001", "A"))
    )


def test_a_new_account_makes_another_roster() -> None:
    """多一个账号就是另一份名单，而旧的那一份还在盘上读得回来。

    于是「这一批当时研究的是谁」永远答得出来。一个「按场景覆盖写」的实现会让这个
    问题只有「现在」这一个答案。
    """
    assert _id(members=(("acct-0000000000000001", "A"),)) != _id(
        members=(("acct-0000000000000001", "A"), ("acct-0000000000000002", "B"))
    )


def test_the_selection_rule_is_half_the_identity() -> None:
    """同一批成员、换一条选择规则 → 另一份名单。

    成员序列一样而规则不同，说明池子在两次读之间变了 —— 而两份名单的「为什么是
    这几行」不是同一句话。
    """
    assert _id(members=(("acct-0000000000000001", "A"),)) != _id(
        members=(("acct-0000000000000001", "A"),),
        selection=CohortSelection(statuses=(AccountStatus.RESEARCHED,)),
    )


def test_the_exclusion_code_is_half_the_identity_too() -> None:
    """排除**码**进原像。

    同一个账号因为「在等人裁决」还是「被判成重复」而不在这份名单里，是两份不同的
    读数 —— 前者下周可能就回来了，后者要人先动一次。
    """
    assert _id(
        members=(), exclusions=(("acct-0000000000000001", "on_hold"),)
    ) != _id(members=(), exclusions=(("acct-0000000000000001", "duplicate"),))


def test_the_workspace_and_the_scene_are_half_the_identity() -> None:
    """同一个池子在两个 Scene 里冻出来的是两份名单。

    名单落在哪个 Scene 决定它派给谁，所以那不是记账。
    """
    assert _id(members=(("acct-0000000000000001", "A"),)) != cohort_id_for(
        workspace="uk-something-else",
        scene_id=SCENE,
        selection=CohortSelection(),
        members=(
            CohortMember(
                account_id="acct-0000000000000001",
                organisation="A",
                status=AccountStatus.ELIGIBLE,
            ),
        ),
        exclusions=(),
    )
    assert _id(members=(("acct-0000000000000001", "A"),)) != _id(
        members=(("acct-0000000000000001", "A"),), scene_id="scene-fedcba9876543210"
    )


# ----------------------------------------------------------------------
# 身份：**不**进原像的必须不改 id
# ----------------------------------------------------------------------
def test_the_wording_of_a_reason_does_not_change_the_id() -> None:
    """``reason`` 是 ``code`` 的**渲染**，不是第二个判据。

    它进原像的话，每一次文案改动都会造出一批内容一模一样、id 不同的名单。
    """
    first = _id(members=(), exclusions=(("acct-0000000000000001", "on_hold"),))
    second = _id(members=(), exclusions=(("acct-0000000000000001", "on_hold"),))
    assert first == second

    # 同一个 code、换一句措辞：直接比原像那一格，因为它才是承重的东西。
    from marketops.domain.cohort import cohort_id_for as build

    def one(reason: str) -> str:
        return build(
            workspace=WORKSPACE,
            scene_id=SCENE,
            selection=CohortSelection(),
            members=(),
            exclusions=(
                CohortExclusion(
                    account_id="acct-0000000000000001",
                    organisation="Example",
                    status=AccountStatus.HOLD,
                    code=CohortExclusionCode.ON_HOLD,
                    reason=reason,
                ),
            ),
        )

    assert one("一句话") == one("另一句话")


def test_the_organisation_of_a_member_does_not_change_the_id() -> None:
    """成员那一行记的名字是**快照**，不是身份。

    它进原像的话，池子里一次改名会让同一个成员算出一份「新名单」，而这份名单的
    成员逐条相同。
    """
    assert _id(members=(("acct-0000000000000001", "旧名字"),)) == _id(
        members=(("acct-0000000000000001", "新名字"),)
    )


def test_the_freeze_time_does_not_change_the_id() -> None:
    """冻下来的时刻不进 id —— 它是记账，不是内容。

    重复冻结同一份读数不该产生第二份名单，而「重冻一次」与「重放一个旧事件」在
    这一格上必须同形。
    """
    rows = (account("acct-0000000000000001", AccountStatus.ELIGIBLE),)
    early = frozen(rows, at=T0)
    late = frozen(rows, at=T0 + timedelta(days=3))
    assert early.cohort_id == late.cohort_id
    assert early.created_at != late.created_at


# ----------------------------------------------------------------------
# 两个校验器
# ----------------------------------------------------------------------
def test_a_row_may_not_be_inside_and_outside_at_once() -> None:
    """派发侧读成员、报告侧读排除 —— 「既在又在」会让报告说它被排除了，而工作单元
    里有它。两份报告各自自洽，没有一处会红。
    """
    members = (
        CohortMember(
            account_id="acct-0000000000000001",
            organisation="A",
            status=AccountStatus.ELIGIBLE,
        ),
    )
    exclusions = (
        CohortExclusion(
            account_id="acct-0000000000000001",
            organisation="A",
            status=AccountStatus.HOLD,
            code=CohortExclusionCode.ON_HOLD,
            reason="x",
        ),
    )
    # ``cohort_id`` 得先算对：身份判据与这一条是**两个**校验器，而一个手工写死的
    # id 会让前一个先抛 —— 那样这一条测的就成了身份判据，重叠那一格没人看着。
    with pytest.raises(ValueError, match="同时在成员里和在排除里"):
        AccountCohort(
            cohort_id=cohort_id_for(
                workspace=WORKSPACE,
                scene_id=SCENE,
                selection=CohortSelection(),
                members=members,
                exclusions=exclusions,
            ),
            workspace=WORKSPACE,
            scene_id=SCENE,
            selection=CohortSelection(),
            members=members,
            exclusions=exclusions,
            created_at=T0,
        )


def test_a_hand_written_id_is_refused() -> None:
    """id 必须**算得出来**。手工指定一个会让同一个池子冻出两份 id 不同的名单。"""
    rows = (account("acct-0000000000000001", AccountStatus.ELIGIBLE),)
    real = frozen(rows)
    with pytest.raises(ValueError, match="内容寻址"):
        AccountCohort(
            cohort_id="coh-0000000000000000",
            workspace=real.workspace,
            scene_id=real.scene_id,
            selection=real.selection,
            members=real.members,
            exclusions=real.exclusions,
            created_at=real.created_at,
        )


def test_created_at_is_normalised_to_utc() -> None:
    """这个时刻会**参与排序**（``list_cohorts`` 的键就是它）。

    ``+08:00`` 写法的更晚时刻会排在一个 ``+00:00`` 的更早时刻前面，于是「哪一份
    名单是当前的」会答一个更早的 —— 而没有一处会红。
    """
    east = timezone(timedelta(hours=8))
    moment = datetime(2026, 9, 26, 9, 0, tzinfo=east)
    cohort = frozen(
        (account("acct-0000000000000001", AccountStatus.ELIGIBLE),), at=moment
    )
    assert cohort.created_at == datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
    assert cohort.created_at.tzinfo is UTC


def test_an_account_id_must_be_canonical_on_both_lists() -> None:
    """两份名单上的 ``account_id`` 过同一条判据 —— 它们**会被按账号检索**
    （``get_cohort`` 的读回就是拿它去池子里对）。

    放宽它，那条读回会把一个规范化的 id 拿去查，查不到 —— 而那在报告上长得像
    「这个账号已经不在池子里了」，一条**业务结论**。
    """
    with pytest.raises(ValueError, match="不是规范形式"):
        CohortMember(account_id="My Account 001", organisation="A", status=AccountStatus.ELIGIBLE)
    with pytest.raises(ValueError, match="不是规范形式"):
        CohortExclusion(
            account_id="My Account 001",
            organisation="A",
            status=AccountStatus.HOLD,
            code=CohortExclusionCode.ON_HOLD,
            reason="x",
        )


def test_the_reason_is_the_code_s_rendering_and_names_the_status() -> None:
    """``reason`` 是 ``code`` 的一个纯函数，且它把 ``status`` 写进去。

    两处各写一句措辞的后果是「同一个码给出两句不同的话」，而读的人只能猜哪句对。
    """
    for code in CohortExclusionCode:
        text = exclusion_reason(AccountStatus.HOLD, code)
        assert text.strip(), f"{code.value} 那句话是空的"
        # ``OVER_LIMIT`` 是唯一不由 status 推出的码，所以只有它不说 status。
        if code is not CohortExclusionCode.OVER_LIMIT:
            assert AccountStatus.HOLD.value in text
    assert len({exclusion_reason(AccountStatus.HOLD, c) for c in CohortExclusionCode}) == len(
        CohortExclusionCode
    )

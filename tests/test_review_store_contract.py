"""``ReviewCaseStore`` 契约：**同一组判据，对着两个实现各跑一遍**。

与 ``tests/test_cohort_store_contract.py`` 逐字同一条理由：端口本身不构成契约 ——
构成契约的是**同一组判据同时看着两个实现**。

* ``adapters/sqlite/review.py::SqliteReviewCaseStore`` —— 主存。
* ``adapters/memory.py::InMemoryReviewCaseStore`` —— 测试替身。

## 这一族里，两个实现真正不同的只有一件事

**锁。** 主存靠 ``BEGIN IMMEDIATE`` 把「读旧行」与「写新行」圈进同一个事务，
内存实现没有锁（它自己的类文档写了这件事）。而下面那些判据里，只有
``test_record_resolution_with_a_stale_version_writes_nothing`` 与并发沾边，
其余全是**关于这两件对象本身**的领域规则：去重、不重开、版本比对、状态相容性。

判据相同而实现不同，正是这一份要钉的东西：**换一个存储不该换掉任何一条判据**。

## 两份判据**刻意不在这里**

1. 「写完重开一个 store 还能读到」与「两个连接同时动同一件」只对持久化实现成立，
   那两条在 ``tests/test_sqlite_review.py`` 里。
2. **跨工作区**：``bind_workspace`` 的拒绝形态两个实现都有，而它属于「一个库只能绑
   一个工作区」这条**部署**判据，不在这一份里。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import InMemoryJobStore, InMemoryReviewCaseStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.review import SqliteReviewCaseStore
from marketops.domain.continuation import ContinuationKind, ContinuationState
from marketops.domain.errors import ReviewConflictError, StoreError
from marketops.domain.review import (
    INTAKE,
    HumanResolution,
    ResolutionOutcome,
    ReviewCase,
    ReviewReason,
    ReviewState,
    case_id_for,
)
from marketops.ports.jobs import JobStore
from marketops.ports.review import ReviewCaseStore

UTC = timezone.utc
T0 = datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
SCENE = "uk-acme-natural-grass"

#: 一件待办默认的那两个选项。**至少两项**是模型的判据（只有一项的不是待办，是通知）。
TWO_WAYS = (ResolutionOutcome.CORRECT_OBJECT, ResolutionOutcome.DISMISS)

#: 生产路径上 ``options`` **真的**是这几个 —— 从 ``INTAKE`` 取，不在这里另写一份。
#: 夹具手写一张生产路径上不存在的表，会让「哪些结果是可选的」这件事有两个说法。
INTAKE_OPTIONS = INTAKE[ReviewReason.IDENTITY_CONFLICT].options


@pytest.fixture(params=["sqlite", "memory"])
def wired(request, tmp_path) -> tuple[ReviewCaseStore, JobStore]:
    """**裁决那一侧与它欠下下一步的那张表**，两个实现各一对。

    ``record_resolution`` 不只推进案件：它同时欠下对受影响对象的一次重评
    （``ports/review.py`` 那一格逐字写了为什么，以及为什么两行必须同生共死）。
    而**欠下的那一行住在另一个端口上**（``JobStore`` 的 outbox），所以判据要同时看得见
    两个 store。

    * SQLite：两者落在**同一个** ``db_path`` 上，所以 ``record_resolution`` 自己开的
      那个写事务同时盖住它们 —— 这正是主存能满足「同生共死」的原因。
    * 内存：那两个字典分属两个对象，所以测试替身**必须拿到** outbox（它的构造参数
      没有缺省值，理由写在那里）。

    参数名进测试 id —— 红了的时候一眼看得见是哪一个。``store`` / ``jobs`` 是它的两个
    半边，取哪一个都不影响参数化（两条都依赖这一条）。
    """
    if request.param == "sqlite":
        db_path = tmp_path / "state" / "marketops.db"
        return SqliteReviewCaseStore(db_path), SqliteJobStore(db_path)
    jobs = InMemoryJobStore()
    return InMemoryReviewCaseStore(jobs=jobs), jobs


@pytest.fixture
def store(wired: tuple[ReviewCaseStore, JobStore]) -> ReviewCaseStore:
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    return wired[0]


@pytest.fixture
def jobs(wired: tuple[ReviewCaseStore, JobStore]) -> JobStore:
    """与 ``store`` **同一块盘**的那个 outbox（同一个 ``db_path`` / 同一个对象）。"""
    return wired[1]


def make_case(
    *,
    subject_ref: str = "kent-003",
    reason: ReviewReason = ReviewReason.IDENTITY_CONFLICT,
    opened_at: datetime = T0,
    state: ReviewState = ReviewState.OPEN,
    options: tuple[ResolutionOutcome, ...] = TWO_WAYS,
    proposed_version: int | None = None,
    **overrides,
) -> ReviewCase:
    """一件形状正确的待办。``case_id`` 由内容算出来 —— 手写一个会被模型拒掉。"""
    fields = {
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "reason": reason,
        "subject_ref": subject_ref,
        "affected_refs": (subject_ref,),
        "options": options,
        "state": state,
        "proposed_version": proposed_version,
        "version": 1,
        "opened_at": opened_at,
        "updated_at": opened_at,
    }
    fields.update(overrides)
    case_id = case_id_for(
        workspace=fields["workspace"],
        scene_id=fields["scene_id"],
        reason=fields["reason"],
        subject_ref=fields["subject_ref"],
    )
    return ReviewCase(case_id=case_id, **fields)


def answer(
    case: ReviewCase,
    outcome: ResolutionOutcome = ResolutionOutcome.CORRECT_OBJECT,
    *,
    at: datetime = T0 + timedelta(hours=1),
    n: int = 1,
    by: str = "member-a",
    proposed_version: int | None = None,
) -> HumanResolution:
    """一条已写下的决定。``n`` 只用来让 ``resolution_id`` 互不相同。"""
    return HumanResolution(
        resolution_id=f"res-{n}",
        case_id=case.case_id,
        workspace=case.workspace,
        outcome=outcome,
        answer="名录上写着 kent-003 就是那家俱乐部",
        by=by,
        roles=("controller",),
        expected_version=case.version,
        at=at,
        proposed_version=proposed_version,
    )


# --- 开案与去重 ---------------------------------------------------------------


def test_the_same_question_opened_twice_leaves_one_case(store: ReviewCaseStore) -> None:
    """同一个 ``(工作区, 场景, 原因, 主体)`` 开两次 **只有一件**，且第二次说得出它是第二次。

    ``opened=False`` 与 ``opened=True`` 返回的案件**看着一样**，而这两次调用的处置
    不同（一次是「刚记下」，一次是「已经在盘上了」）。只看 ``case`` 读不出区别 ——
    所以返回值里两格都必要（``ports/review.py`` 的 ``CaseAdmission``）。
    """
    case = make_case()
    first = store.open_case(case)
    second = store.open_case(case)

    assert (first.opened, second.opened) == (True, False)
    assert first.case == second.case == case
    assert len(store.list_cases()) == 1


def test_a_case_that_was_already_decided_is_reported_not_reopened(
    store: ReviewCaseStore,
) -> None:
    """盘上那件**已经裁决过**时，``open_case`` 把它**原样**报出来，不重开。

    这一条是这一族里唯一能出的静默错误：一件被系统自己重开的案件，会把人的一句
    「不值得处理」在下一个批次里作废，而报告上它看起来只是「又出现了一次」。
    重开是人做的（``ResolutionOutcome.REOPEN``），不是系统。

    所以判据有两半：``opened`` 是 ``False``，**而且**盘上那件仍是关闭的那一件
    （状态、版本、``closed_at`` 都不动）。
    """
    case = make_case()
    store.open_case(case)
    closed = store.record_resolution(answer(case), expected_version=1)

    again = store.open_case(case)

    assert again.opened is False
    assert again.case == closed
    assert again.state is ReviewState.RESOLVED


def test_get_returns_none_only_for_an_absent_id(store: ReviewCaseStore) -> None:
    """``None`` 只有一个意思：**没有这个 id 的案件**。"""
    case = make_case()
    store.open_case(case)

    assert store.get_case(case.case_id) == case
    assert store.get_case("rc-0000000000000000") is None


def test_a_case_comes_back_the_same_as_it_went_in(store: ReviewCaseStore) -> None:
    """读回来的是**同一件**：选项、建议、认领者、快照、证据、策略版本，逐格相同。

    这一条看着像废话，而它挡的是一种很常见的收口方式：把 ``options`` / ``affected_refs``
    现推一遍。那会让「当时给了人哪几个选项」变成「照现在的规则会给哪几个」——
    而审批记录正是要回答前一个问题。
    """
    case = make_case(
        options=(ResolutionOutcome.APPROVE_POLICY_CHANGE, ResolutionOutcome.DISMISS),
        state=ReviewState.AWAITING_APPROVAL,
        proposed_version=13,
        recommendation=ResolutionOutcome.APPROVE_POLICY_CHANGE,
        queue="identity",
        claimed_by="member-a",
        evidence_ids=("ev-1", "ev-2"),
        policy_version=12,
        note="池子里那一行的名称是它自己的内部 id",
    )
    store.open_case(case)

    assert store.get_case(case.case_id) == case


# --- 排序 ---------------------------------------------------------------------


def test_list_puts_pending_first_and_earliest_first(store: ReviewCaseStore) -> None:
    """**未决的在前、早的在前** —— 顺序是契约的一部分（``ports/review.py``）。

    第一行必须是「现在最该看的那一件」。所以一件**更早开、已经关掉**的不许挡在
    一件**刚开的未决**前面 —— 那会让报告的第一行是一件已经处理完的事。
    """
    early_closed = make_case(subject_ref="kent-003", opened_at=T0)
    late_pending = make_case(subject_ref="kent-004", opened_at=T0 + timedelta(hours=1))
    store.open_case(early_closed)
    store.open_case(late_pending)
    store.record_resolution(
        answer(early_closed, at=T0 + timedelta(minutes=30)), expected_version=1
    )

    assert [c.subject_ref for c in store.list_cases()] == ["kent-004", "kent-003"]


def test_two_cases_opened_at_the_same_instant_still_have_one_order(
    store: ReviewCaseStore,
) -> None:
    """同一刻开出来的两件，顺序仍由 ``case_id`` 钉死 —— 不是「由实现决定」。

    ``opened_at`` 是微秒级的 ISO 字符串，一次 tick 里对两个主体各开一件完全正常。
    没有第二根排序键时，``ORDER BY`` 在并列的键上给出的顺序由实现决定：
    「现在最该看的那一件」会在两次调用之间换一个答案，而两份报告都自洽。
    """
    a = make_case(subject_ref="kent-004", opened_at=T0)
    b = make_case(subject_ref="kent-003", opened_at=T0)
    store.open_case(a)
    store.open_case(b)

    assert [c.case_id for c in store.list_cases()] == sorted([a.case_id, b.case_id])


# --- 裁决 ---------------------------------------------------------------------


def test_the_resolutions_of_a_case_come_back_earliest_first(
    store: ReviewCaseStore,
) -> None:
    """早的在前（同刻按 ``resolution_id``）—— ``inbox_flow`` 读的正是这个序列。

    **写进去的顺序与读出来的顺序刻意相反**：先写晚的那一条、再写早的那一条。若实现
    按插入顺序（``rowid``）给结果，这一条会红 —— 而按插入顺序排的表现是
    「这一件等了多少天」在一件被补记过一次历史的案件上答错，且没有任何一处报错。
    """
    case = make_case()
    store.open_case(case)
    store.record_resolution(
        answer(case, ResolutionOutcome.DISMISS, at=T0 + timedelta(hours=3), n=2),
        expected_version=1,
    )
    store.record_resolution(
        answer(case, ResolutionOutcome.REOPEN, at=T0 + timedelta(hours=2), n=3),
        expected_version=2,
    )

    rows = store.list_resolutions(case.case_id)

    assert [r.resolution_id for r in rows] == ["res-3", "res-2"]
    assert rows[0].at < rows[1].at


def test_record_resolution_with_a_stale_version_writes_nothing(
    store: ReviewCaseStore,
) -> None:
    """版本对不上时整次拒绝，**且一个字节都没落盘**。

    两个人同时裁决同一件时，最后写的那个赢而**两边都以为自己成功了** —— 这是这一格
    唯一挡不住就会发生的事。所以判据有三半：抛的是 ``ReviewConflictError``；
    消息里同时有期望与实际；**盘上那件没变**（版本、状态、历史条数都不动）。
    """
    case = make_case()
    store.open_case(case)
    store.record_resolution(
        answer(case, ResolutionOutcome.DISMISS, n=1), expected_version=1
    )

    with pytest.raises(ReviewConflictError) as stale:
        store.record_resolution(
            answer(case, ResolutionOutcome.DISMISS, at=T0 + timedelta(hours=5), n=2),
            expected_version=1,
        )

    message = str(stale.value)
    assert "第 2 版" in message and "第 1 版" in message, (
        "回执里必须同时有期望版本与实际版本 —— 只报「冲突了」的话，读的人无法判断"
        "是自己太慢还是对面在打架"
    )
    assert store.get_case(case.case_id).version == 2
    assert store.get_case(case.case_id).state is ReviewState.DISMISSED
    assert [r.resolution_id for r in store.list_resolutions(case.case_id)] == ["res-1"]


def test_a_decision_about_a_case_that_is_not_there_is_a_conflict(
    store: ReviewCaseStore,
) -> None:
    """案件不在盘上 → ``ReviewConflictError``（「有人先写了」那一族），不是 ``StoreError``。

    处置是「重读、重新决定」，不是「停机对账」。
    """
    case = make_case()

    with pytest.raises(ReviewConflictError):
        store.record_resolution(answer(case), expected_version=1)
    assert store.get_case(case.case_id) is None


def test_an_outcome_the_case_cannot_take_is_refused(
    store: ReviewCaseStore, jobs: JobStore
) -> None:
    """一个结果能不能选，由**案件当前状态**决定，而不是只由 ``options`` 那张表决定。

    两个方向各一条，两条走的是不同的判据，而两条都必须整次拒绝、一个字都不写：

    * ``APPROVE_POLICY_CHANGE`` **不在** ``options`` 里，而它在一件**等批准**的案件上
      是可选的（那是 ``may_decide`` 的第二个例外）。在还开着的那一件上则不可选 ——
      一件还开着的待办等的不是另一个人的批准，是一个人。
    * ``DISMISS`` **在** ``options`` 里，而一件**已经关掉**的案件收不下它。这一条是
      实测出来的：放行的后果不是多一行历史，而是流水账把同一件数两次（``resolved``
      与 ``dismissed`` 各加一，而 ``opened`` 只加了一），§12 那条恒等式当场差一。

    ## 这一条的夹具为什么**不再**手写 ``options=(APPROVE_POLICY_CHANGE, DISMISS)``

    那个形状在生产路径上不存在。``options`` 来自 ``domain.review.INTAKE``（按原因给的
    那几个答案），而提议**不重写它**（``apply_resolution`` 只换状态，不动那张表）。
    夹具替生产路径编一张不存在的表，读起来像「``APPROVE_POLICY_CHANGE`` 本来就该写进
    选项里」—— 而那正是 ``may_decide`` 那条例外**存在**的理由被抹掉的读法。

    ## 末尾那次 ``continuations == ()`` 判的是什么，以及**不**判什么

    判的是被拒之后**一个字都不欠**（§9 那一句的一个面）。**它不是「欠账那一句摆在
    哪儿」的执行器** —— 这件事我实测了三遍，三次都全绿：

    * 陈旧版本那条拒绝发生在拿到 ``case`` 之前，而 ``owe`` 需要 ``case``；
    * 「已记过」那条拒绝撞的是同一个 ``resolution_id`` ⇒ 同一个 ``continuation_id``，
      被 ``owe`` 的「没有才写」吸收；
    * 挪到 ``apply_resolution`` 之前也没用 —— ``intents_for_resolution`` **自己**会先
      算一次 ``apply_resolution``（它要判「这一件还在待办里吗」），所以在任何会被拒的
      输入上它比那一句更早抛。

    于是这条断言对今天的**两个**实现都恒真。留着它是因为它钉住的是盘面（被拒不留
    欠账），而不是因为它在守着那一句的位置；位置那一半由 SQLite 侧的事务性判据守
    （让欠账那一步抛，断言决定与案件一起回滚）。
    """
    still_open = make_case(options=INTAKE_OPTIONS)
    assert ResolutionOutcome.APPROVE_POLICY_CHANGE not in still_open.options
    store.open_case(still_open)

    with pytest.raises(ReviewConflictError) as not_an_option:
        store.record_resolution(
            answer(still_open, ResolutionOutcome.APPROVE_POLICY_CHANGE), expected_version=1
        )
    assert "approve_policy_change" in str(not_an_option.value)
    assert "authorizer" not in str(not_an_option.value), (
        "这一层判的是「这一件现在收不收得下这个结果」，不是「你是谁」—— 角色是目录的事。"
    )
    assert store.get_case(still_open.case_id).version == 1

    # 另一行（另一件待办）：同上一个 ``subject_ref`` 会算出同一个 case_id，而那一条
    # 已经在盘上了 —— 去重是承重的，但它会把这第二个方向测成「版本过期」。
    already_closed = make_case(
        subject_ref="kent-004",
        options=INTAKE_OPTIONS,
        state=ReviewState.RESOLVED,
        closed_at=T0 + timedelta(hours=1),
        version=2,
    )
    store.open_case(already_closed)

    with pytest.raises(ReviewConflictError) as closed:
        store.record_resolution(
            answer(already_closed, ResolutionOutcome.DISMISS), expected_version=2
        )
    assert "REOPEN" in str(closed.value), "拒绝里必须说出**出路**（先重开）"
    assert store.get_case(already_closed.case_id).version == 2

    # 被拒的那两次**一个字都不欠**。两次都判，但承重的是**第一次**：它的拒绝发生在
    # ``case`` 已经读到、``updated`` 还没算出来的时候，而它用的 ``resolution_id`` 是
    # 新的（撞不上盘上任何一行），所以把欠账那一句挪到 ``apply_resolution`` 之前会在
    # 这里留下一行。第二次撞的是**同一个** ``resolution_id``，于是撞上同一个
    # ``continuation_id``，被 ``owe`` 的「没有才写」吸收 —— 它对位置不敏感，因此它
    # 只是顺带判到的那一半。
    assert jobs.continuations(scene_ids=[SCENE]) == (), (
        "被拒的裁决给一件没发生的事留下了欠账 —— 于是永远有一次重评在等一个不存在的决定"
    )


def test_a_resolution_written_twice_is_refused_not_swallowed(
    store: ReviewCaseStore,
) -> None:
    """同一个 ``resolution_id`` 写两次是**报错**，不是幂等。

    这个 id 不是内容寻址的（它是调用方给的一次事件 id），所以撞主键是一次真实的
    重复写入。静默吞掉它，会让「同一件事被记成两条」在盘上读不出来 —— 流水账上
    那一格会多算一次，而两次写的内容可能已经不同了。

    第二次决定用的是 ``REOPEN``（而不是再放下一次）：它**通过了**所有的领域判据
    —— 这件案件确实关着、重开确实改变状态 —— 所以它只会在写那一行历史时撞主键。
    这一条因此同时钉住了两件事：撞主键被报出来，以及**事务回滚**（案件那一行的
    更新也不许留下 —— 判据先过、写时失败时，若只有 INSERT 参与回滚，盘上会出现
    一件「被重开了但没有那条重开记录」的案件）。
    """
    case = make_case()
    store.open_case(case)
    row = answer(case, ResolutionOutcome.DISMISS)
    store.record_resolution(row, expected_version=1)
    assert store.get_case(case.case_id).version == 2

    with pytest.raises(StoreError):
        store.record_resolution(
            row.model_copy(update={"outcome": ResolutionOutcome.REOPEN}),
            expected_version=2,
        )

    assert len(store.list_resolutions(case.case_id)) == 1
    still = store.get_case(case.case_id)
    assert (still.version, still.state) == (2, ReviewState.DISMISSED), (
        "被拒的那一次一个字节都不许落盘 —— 版本被推进或被重开成 OPEN 了，"
        "都会让人以为它成功了"
    )


def test_a_reopen_puts_a_closed_case_back_in_the_pending_front(
    store: ReviewCaseStore,
) -> None:
    """``REOPEN`` 把一件关掉的案件**送回未决**，而它不新开一件。

    新开会让「同一件事问了两遍」在报告上读成「有两件事」—— 而去重键正是为了不让它
    发生。所以判据三半：盘上仍只有两件；它回到了 ``list_cases`` 的**未决那一段**；
    ``closed_at`` 被清掉（一件未决的案件带着一个关闭时刻，会让「它等了多久」答错）。
    """
    # 被重开的那一件**开得更晚**：这样「未决的在前」才是唯一能让它排到前面的判据
    # —— 若实现只按 ``opened_at`` 排，它会排在 ``kent-004`` 之后，而那条断言会红。
    other = make_case(subject_ref="kent-004", opened_at=T0)
    store.open_case(other)
    store.record_resolution(
        answer(other, ResolutionOutcome.DISMISS, at=T0 + timedelta(minutes=1), n=9),
        expected_version=1,
    )
    case = make_case(subject_ref="kent-003", opened_at=T0 + timedelta(hours=2))
    store.open_case(case)
    store.record_resolution(
        answer(case, ResolutionOutcome.DISMISS, at=T0 + timedelta(hours=3), n=1),
        expected_version=1,
    )
    assert [c.subject_ref for c in store.list_cases()] == ["kent-004", "kent-003"], (
        "两件都关着时按打开时刻排 —— 这一段是下面那条断言的前提"
    )

    store.record_resolution(
        answer(case, ResolutionOutcome.REOPEN, at=T0 + timedelta(hours=4), n=2),
        expected_version=2,
    )

    rows = store.list_cases()
    assert len(rows) == 2
    assert [c.subject_ref for c in rows] == ["kent-003", "kent-004"]
    reopened = store.get_case(case.case_id)
    assert reopened.state is ReviewState.OPEN
    assert reopened.closed_at is None


# --- 裁决同时欠下下一步 ---------------------------------------------------------
#
# 这一节判的是 ``ports/review.py::record_resolution`` 那段「它**同时**欠下下一步，
# 而且和上面那两行在同一个写事务里」。两半都要判：
#
# * **欠了什么** —— 由 ``domain/continuation.py::intents_for_resolution`` 决定
#   （策略在领域）。判据是「那个对象欠一次重看」，不是「有一行东西」：一行
#   ``subject_ref`` 为空的重看会照常跑完、报「0 格到期」，与「这一格核过了」逐字相同。
# * **什么时候不欠、什么时候该欠第二次** —— 这两条是这一族里唯一能出的静默错误：
#   一个多欠的实现让重评重复发生（报告上只是「多跑了几趟」），一个少欠的实现让重评
#   永远不发生（报告上什么都没有）。
#
# 「同生共死」（事务那一半）判在 ``tests/test_sqlite_continuation.py``：那里能把那句
# INSERT 打穿，而内存实现没有事务。


def test_a_resolution_owes_one_recheck_of_the_object_it_was_about(
    store: ReviewCaseStore, jobs: JobStore
) -> None:
    """一次裁决欠下**恰好一行**重看，而它写着那个对象是谁。

    ``kind`` 复用 ``MAINTENANCE_TICK`` 而不是新加一个成员：那句「裁决后自动重评」
    要的是一次**按现行规则的重看**（架构 §9：「重评**先用现有事实**，证据不足才重抓」），
    而那正是控制器那一趟 tick 做的事。新加一个 kind 只会让同一个动作有两个执行器，
    而两个执行器漂移的表现是「有一半的重评按旧规则做」。

    ``business_key`` 是 ``resolution_id`` 而不是 ``case_id``：判据在下面那条（重开）。
    """
    case = store.open_case(make_case(subject_ref="kent-006")).case
    resolution = answer(case)

    store.record_resolution(resolution, expected_version=case.version)

    rows = jobs.continuations(scene_ids=[SCENE])
    assert len(rows) == 1
    (owed,) = rows
    assert owed.kind is ContinuationKind.MAINTENANCE_TICK
    assert (owed.scene_id, owed.subject_ref) == (SCENE, "kent-006")
    assert owed.business_key == resolution.resolution_id
    assert owed.state is ContinuationState.PENDING


def test_a_decision_that_leaves_the_case_pending_owes_nothing(
    store: ReviewCaseStore, jobs: JobStore
) -> None:
    """一条只把案件推进 ``AWAITING_APPROVAL`` 的提议**什么都不欠** —— 直到它被批。

    判据分两半，而缺任何一半都会放过一个错的实现：

    * 提议之后**没有**重看。那条规则变更还没有生效，这时候去重评，重评的是**旧**规则
      下的那一格，于是这一趟的唯一产出是一行「看过了、没变」。
    * 批准之后**有**一行，且是一次新的。真正该重评的时刻是它离开待办的那一刻。

    第一半只判「空」是不够的：一个永远不欠的实现（比如把 ``intents_for_resolution``
    整个省掉）也会让它绿。第二半是它的配对。
    """
    case = store.open_case(
        make_case(
            subject_ref="kent-007",
            options=(ResolutionOutcome.PROPOSE_POLICY_CHANGE, ResolutionOutcome.DISMISS),
        )
    ).case
    store.record_resolution(
        answer(case, ResolutionOutcome.PROPOSE_POLICY_CHANGE, proposed_version=2),
        expected_version=case.version,
    )

    assert store.get_case(case.case_id).state is ReviewState.AWAITING_APPROVAL
    assert jobs.continuations(scene_ids=[SCENE]) == ()

    awaiting = store.get_case(case.case_id)
    store.record_resolution(
        answer(
            awaiting,
            ResolutionOutcome.APPROVE_POLICY_CHANGE,
            at=T0 + timedelta(hours=2),
            n=2,
        ),
        expected_version=awaiting.version,
    )

    rows = jobs.continuations(scene_ids=[SCENE])
    assert [row.subject_ref for row in rows] == ["kent-007"]
    assert rows[0].state is ContinuationState.PENDING


def test_a_reopened_case_owes_a_second_recheck(
    store: ReviewCaseStore, jobs: JobStore
) -> None:
    """同一件案件**两次离开待办**是两次决定，各自欠一次重看。

    这一条钉的是 ``business_key`` 那一格。按 ``case_id`` 做键的实现会在这里红：第二行
    撞上第一行的 ``continuation_id``，而 ``owe`` 是「没有才写」—— 于是**第二次重看
    永远不会发生**，而它的表现与「它已经重看过了」逐字相同。

    这也是 ``ResolutionOutcome.REOPEN`` 存在的全部意义（§B20「重启不丢」）：重开这件
    动作把一个已经做过的东西**再交给人看一次**，而它同时必须再被机器看一次。
    """
    case = store.open_case(make_case(subject_ref="kent-008")).case
    store.record_resolution(answer(case, n=1), expected_version=case.version)

    closed = store.get_case(case.case_id)
    reopened = store.record_resolution(
        answer(closed, ResolutionOutcome.REOPEN, at=T0 + timedelta(hours=2), n=2),
        expected_version=closed.version,
    )
    assert reopened.state is ReviewState.OPEN

    store.record_resolution(
        answer(reopened, at=T0 + timedelta(hours=3), n=3),
        expected_version=reopened.version,
    )

    rows = jobs.continuations(scene_ids=[SCENE])
    assert [row.business_key for row in rows] == ["res-1", "res-3"]
    assert {row.subject_ref for row in rows} == {"kent-008"}


def test_a_refused_resolution_owes_nothing(
    store: ReviewCaseStore, jobs: JobStore
) -> None:
    """被拒的那一次**一个字都不欠**。

    ``ports/review.py`` 逐字：「写在事务**之前** —— 一次被拒的裁决会给一件**没发生的事**
    留下一行欠账，于是永远有一次重评在等一个不存在的决定」。SQLite 那一侧由
    ``write_transaction`` 的回滚兜住；内存这一侧没有事务可回滚。

    ## 但它**不是**那个位置的执行器 —— 这一条是实测出来的

    我原先在这里写着「把 ``owe`` 挪到 ``apply_resolution`` 之前，它当场红」。**是假的。**
    ``owe`` 的参数里需要 ``case``，而 ``case`` 由 ``_read_for_update`` 给出 —— 陈旧版本
    那两条 ``raise`` 发生在 ``case`` 存在**之前**，所以欠账那一句无论摆在哪里都走不到
    这条判据的现场。

    所以这条判据今天**恒真**（在实现形状不变的前提下）：它钉住的是盘面（案件没被推进、
    outbox 为空），而不是那一句的位置。内存侧那一句的位置**结构上判不出来**（三条路
    全在它之前，或撞同一个键被「没有才写」吸收），位置那一半由 SQLite 侧的事务性判据守。
    """
    case = store.open_case(make_case(subject_ref="kent-009")).case

    with pytest.raises(ReviewConflictError):
        store.record_resolution(answer(case), expected_version=case.version + 1)

    assert jobs.continuations(scene_ids=[SCENE]) == ()
    assert store.get_case(case.case_id).state is ReviewState.OPEN


# --- 端口上只有六个问法 ---------------------------------------------------------


def test_the_port_has_no_second_way_to_ask_for_a_case() -> None:
    """接口上**只有**六个抽象方法，且 ``__init__`` **没有任何合并规则**。

    ★ 这一条是 ``ports/review.py`` 那段「``open_case`` 是由问题驱动的，不是
    ``put_case``」在**结构层面**的执行器。加一个 ``put_case(case)`` 不会让任何一条
    已有用例转红 —— 它只会让去重落在调用方手里，而「算出正确的 id」正是这一层要替
    它守住的事。

    ``claim`` 是第六个，而它**不是**第七个 ``record_resolution``：它改的是当前状态
    （``owner`` 那一格）、不追加历史，所以它进不了 ``human_resolution`` 那张只增不改
    的表。加一个 ``set_owner(case_id, by)`` 也不会让任何一条已有用例转红 —— 它只会
    把 ``expected_version`` 那条并发判据从这条路上去掉，于是两个人同时领走一件时
    后写的那个静默地顶掉前一个，而他的屏幕上还写着「我在做这一件」。

    判据直接从类上取（``__abstractmethods__``），所以它**不需要**人抄一份方法名清单。
    """
    assert ReviewCaseStore.__abstractmethods__ == {
        "claim",
        "get_case",
        "list_cases",
        "list_resolutions",
        "open_case",
        "record_resolution",
    }, (
        "Review Inbox 的端口上多了一个问法。请先回答两个问题："
        "「同一个问题不新开第二件」由谁保证 —— 它由 ``open_case`` 保证"
        "（``case_id`` 是内容寻址的），不由调用方；以及新动词是不是一次"
        "**只增不改**的历史追加 —— 若是，它该走 ``record_resolution``，"
        "而不是在端口上多一个入口。"
    )


def test_claim_is_not_a_second_way_to_record_a_decision(
    store: ReviewCaseStore,
) -> None:
    """认领只推进**当前状态**，一行历史都不写。

    ``ports/review.py`` 把这条写成了一段散文（「它改的是当前状态，不追加历史」），
    而这一条是那段话在本层的执行器。三个断言各挡一半：

    * ``list_resolutions`` 在认领前后**逐条相同** —— 多写一行历史的表现是流水账上
      那一格多算一次，而恒等式照样成立（``inbox_flow`` 只数它认识的结果），于是
      两份报告各自都自洽。
    * ``state`` / ``claimed_by`` 真的变了 —— 不动它们的表现是「认领」这个动作
      在报告上不存在，而 §12 的 ``owner`` 那一格永远只有一个值。
    * ``version`` 前进了一格 —— 不动版本的表现是「两个人同时领走一件」时后写的
      那个**静默顶掉**前一个（他的屏幕上还写着「我在做这一件」），因为 CAS 比的
      是一个没变过的数。
    """
    case = store.open_case(make_case(subject_ref="kent-005")).case

    claimed = store.claim(
        case.case_id, by="claude-control", at=T0, expected_version=case.version
    )

    assert store.list_resolutions(case.case_id) == ()
    assert (claimed.state, claimed.claimed_by) == (ReviewState.CLAIMED, "claude-control")
    assert claimed.version == case.version + 1


def test_claiming_twice_is_a_conflict_not_a_no_op(store: ReviewCaseStore) -> None:
    """已经被人持着的那一件不能再被认领 —— 包括**同一个人**再领一次。

    第二次认领在**两种**输入下都必须是 ``ReviewConflictError``：

    * 换一个人来领（``worker-2``）—— 顶掉前一个持有者，而他手上没有任何东西
      会变旧。
    * 同一个人再领一次 —— 这不是幂等，是一次**没有改变任何东西**的写：它照样
      +1 版本，于是**另一个**持有者手上的 ``expected_version`` 当场过期，而他
      什么都没做错（``domain.review.claim`` 逐字）。
    """
    case = store.open_case(make_case(subject_ref="kent-006")).case
    store.claim(case.case_id, by="claude-control", at=T0, expected_version=case.version)

    with pytest.raises(ReviewConflictError):
        store.claim(case.case_id, by="worker-2", at=T0, expected_version=case.version + 1)
    with pytest.raises(ReviewConflictError):
        store.claim(
            case.case_id, by="claude-control", at=T0, expected_version=case.version + 1
        )

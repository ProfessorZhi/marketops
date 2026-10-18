"""§3：运维对账 —— 把历史投影对齐，而**不开始任何新的付费工作**。

## 这一套判据守的是一条耦合，不是一段代码

在这一版之前，「把已经结算的那几件的看板推到位」只能从
``WorkDispatch.converge_settled`` 走，而它唯一的调用点在派发轮次之后。于是
运维手里**没有**「只对账」这个动作：要收敛，就得同时把新的活交出去 —— 真实的
worker、真实的 Serper、真实的账单。2026-09-22 那次运行之后，历史作业上留下
一件已结算仍是 ``todo`` 的看板，而当时写不出「只把这一件对齐」。

所以这里最要紧的四条判据钉的不是「对账做对了没有」，而是**它没有做别的**：

1. ``test_dry_run_writes_nothing_at_all`` —— 一次写都不产生。
2. ``test_apply_writes_exactly_what_the_dry_run_planned`` —— 写下去的与预演说
   的是同一份，逐行对得上。
3. ``test_a_live_lease_is_never_released`` —— 陈旧租约只报不改。本仓**没有**
   到期规则，所以「它过期了」这个结论在这里给不出来。
4. ``test_reconciling_never_creates_an_external_task`` —— 对账不派发。

## 判据为什么看 ``outside.transitions`` 而不是看回执

回执是**我们这一侧**写的；``FakeDispatcher.transitions`` 是**外面**记的。一个
「报告里说没写、实际写了」的实现，只有后者看得见 —— 与 ``test_board_sync`` 里
那条「只记目标状态会让 expected 也写成目标的自说自话全绿」是同一条理由。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from marketops.adapters.memory import InMemoryJobStore
from marketops.application.dispatch import INITIAL_EXTERNAL_STATUS, WorkDispatch, board_target, link_labels
from marketops.application.reconcile import (
    ALL_CLASSES,
    DRIFT_KINDS,
    LEASE_DISPOSITIONS,
    ReconcileClass,
    _age_seconds,
    reconcile_job,
)
from marketops.domain.enums import JobInputKind, StopReason
from marketops.domain.errors import DispatchError, JobStateError
from marketops.domain.findings import Findings
from marketops.domain.job import WorkItem
from marketops.domain.util import sha256_text
from marketops.interfaces.cli import build_parser, main
from marketops.ports.dispatch import ExternalTask
from marketops.ports.jobs import JobStore
from marketops.security.catalog import TOOL_CATALOG
from tests.fakes import FakeDispatcher, synthetic_dossiers, synthetic_scene_files

SCENE = "uk-acme-natural-grass"
KEY = "reconcile-v1"
SCENE_FILES = synthetic_scene_files()


class _RefusingDispatcher(FakeDispatcher):
    """读得成、派得动，而在需要时**答不上来** —— 造的是 ``unreadable`` 而不是 ``missing``。

    ``refusing`` 是**在派发之后**才打开的：派发那一步本身也要 ``find``（先查有没有
    别人已经建过），所以一个从第一秒就在抛的执行器连件都交不出去，测的就不是对账了。
    """

    def __init__(self) -> None:
        super().__init__()
        self.refusing = False

    def find(self, *, labels: Sequence[str]) -> tuple[ExternalTask, ...]:
        if self.refusing:
            self.calls.append("find")
            raise DispatchError("执行器起不来")
        return super().find(labels=labels)


# ----------------------------------------------------------------------
# 脚手架
# ----------------------------------------------------------------------
def _compiled(
    store: JobStore, *, subjects: Sequence[str], key: str
) -> tuple[str, tuple[WorkItem, ...]]:
    """受理 + 编译。**不派发、不结算** —— 两件都留给判据自己摆。"""
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026-09",
        idempotency_key=key,
        request_sha256=sha256_text(f"devon-2026-09:{key}"),
    )
    items = store.compile_work_items(
        job.job_id, synthetic_dossiers(*subjects), scene=SCENE_FILES
    )
    return job.job_id, items


def _dispatched(
    store: JobStore,
    outside: FakeDispatcher,
    *,
    subjects: Sequence[str] = ("devon-01",),
    key: str = KEY,
) -> tuple[str, tuple[WorkItem, ...]]:
    """受理 + 编译 + 派发**第一件**。**不结算** —— 目标状态由判据自己给。"""
    job_id, items = _compiled(store, subjects=subjects, key=key)
    WorkDispatch(jobs=store, dispatcher=outside).dispatch(items[0].work_item_id)
    return job_id, items


def _settle(store: JobStore, work_item_id: str, reason: StopReason) -> str:
    """真的走到结算 —— 目标是 ``board_target`` 从停下的那条理由读出来的。"""
    attempt = store.lease(work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=reason, findings=Findings())
    return work_item_id


def _restatus(outside: FakeDispatcher, task_id: str, status: str) -> None:
    """把外面那一行改成另一个状态，**label 一个字节都不动**。"""
    task = outside.tasks[task_id]
    outside.seed(task.external_id, task.labels, status=status)


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def outside() -> FakeDispatcher:
    return FakeDispatcher()


# ----------------------------------------------------------------------
# 闭集
# ----------------------------------------------------------------------
def test_every_class_declares_whether_it_writes_outside() -> None:
    """**唯一会写外面的是 ``board``**，而这句话是从闭集上读出来的。

    一个类别一旦能被 ``--class`` 点名，就得说得出「``--apply`` 时它写不写」——
    否则 ``--class lease --apply`` 读起来像「把陈旧租约收掉」，而实际什么都没做。
    """
    writers = {cls for cls in ALL_CLASSES if cls.writes_outside}
    assert writers == {ReconcileClass.BOARD}


def test_the_classes_are_the_closed_set_the_report_can_name() -> None:
    """``classes`` 那一格报的是**这个**闭集里的名字，顺序稳定。

    稳定是判据的一部分：报告的段序由 ``ALL_CLASSES`` 定，不由调用方给的那一串定。
    一个跟着调用方顺序走的实现，会让同一份状态的两份报告在 diff 里看起来不同。
    """
    assert [cls.value for cls in ALL_CLASSES] == ["board", "lease", "mapping"]
    assert set(DRIFT_KINDS) >= {"none", "would_converge", "external_ahead", "missing", "unreadable"}
    assert "NEEDS_OPERATOR" in LEASE_DISPOSITIONS


# ----------------------------------------------------------------------
# §3 的硬约束：dry-run 不产生任何写
# ----------------------------------------------------------------------
def test_dry_run_writes_nothing_at_all(store, outside) -> None:
    """判据说要写，而外面**一个字节都没动**。

    ``planned_writes`` 非空是这条判据的另一半：一个「什么都不说」的实现也写不出
    东西来，而它不是对账。
    """
    job_id, items = _dispatched(store, outside)
    _settle(store, items[0].work_item_id, StopReason.GOAL_MET)
    before = dict(outside.tasks)

    report = reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=True)

    assert report.dry_run is True
    assert outside.transitions == [], "dry-run 对外发了一次 CAS"
    assert outside.tasks == before, "dry-run 改了外部看板"
    assert report.writes == ()
    assert len(report.planned_writes) == 1, "预演说不出要写什么，那它就不是一次预演"


def test_apply_writes_exactly_what_the_dry_run_planned(store, outside) -> None:
    """预演与真的写是**同一份判据**的两次结果，逐行相同。

    这条判据要的是「预演说会变」与「apply 真的变」不可能分家。分开两次构造的
    实现（预演算一遍、apply 另算一遍）在这里会红 —— 而它们的差别会以「预演说不用
    写、apply 写了」的样子出现在生产上，那正是最坏的一种。
    """
    job_id, items = _dispatched(store, outside)
    _settle(store, items[0].work_item_id, StopReason.GOAL_MET)

    planned = reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=True).planned_writes
    applied = reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=False)

    assert applied.writes == planned
    assert len(outside.transitions) == len(planned) == 1
    external_id, expected, status = outside.transitions[0]
    assert expected == INITIAL_EXTERNAL_STATUS, "CAS 的 expected 端不是读回来的那个值"
    assert status == board_target(store.get_work_item(items[0].work_item_id)) == "done"
    assert external_id in planned[0]


def test_a_second_apply_writes_nothing_and_that_is_the_point(store, outside) -> None:
    """收敛而不是迁移：第二次读到的已经是目标状态，一次 CAS 都不发。

    一次**迁移**式的回传会在第二次撞 CAS 冲突，而冲突报文说的是「有别人先写了」
    —— 一条凭空造出来的并发。
    """
    job_id, items = _dispatched(store, outside)
    _settle(store, items[0].work_item_id, StopReason.GOAL_MET)
    reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=False)
    count = len(outside.transitions)

    twice = reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=False)

    assert len(outside.transitions) == count
    assert twice.writes == ()
    assert twice.planned_writes == ()
    assert twice.board[0].synced is True
    assert twice.board[0].changed is False


# ----------------------------------------------------------------------
# §3 的硬约束：陈旧租约只报不改
# ----------------------------------------------------------------------
def test_a_live_lease_is_never_released(store, outside) -> None:
    """一条活租约 → 一行 ``NEEDS_OPERATOR``，而**库里的状态一个字节都没动**。

    ``--apply`` 也照这条走：本仓没有 ``lease_expires_at`` / ``reap``
    （``ports/jobs.py`` 逐字写着），所以「它过期了」这个结论在这里给不出来 ——
    给得出的是「它已经这么久了」。凭年龄收回一条租约，就是在这个入口现编一条
    到期规则，而现编的那一条不会有任何判据看着它。
    """
    job_id, items = _dispatched(store, outside)
    attempt = store.lease(items[0].work_item_id, holder="claude-worker-2")

    report = reconcile_job(
        jobs=store, dispatcher=outside, job_id=job_id, dry_run=False
    )

    assert len(report.leases) == 1
    row = report.leases[0]
    assert row.attempt_id == attempt.attempt_id
    assert row.holder == "claude-worker-2"
    assert row.disposition == "NEEDS_OPERATOR"
    assert "不会收回它" in row.reason
    assert any(line.startswith(f"lease:{items[0].work_item_id}:") for line in report.needs_operator)
    # 库里那一行仍然是活租约 —— 这是「没有偷偷放」唯一看得见的证据。
    assert [a.state.value for a in store.attempts(items[0].work_item_id)] == ["leased"]


def test_a_lease_that_is_already_settled_is_not_a_live_lease(store, outside) -> None:
    """结算过的租约不在这一节里。否则「干净」与「有东西要人看」会长得一样。"""
    job_id, items = _dispatched(store, outside)
    _settle(store, items[0].work_item_id, StopReason.GOAL_MET)

    report = reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=True)

    assert report.leases == ()
    assert report.needs_operator == ()


def test_the_age_is_reported_and_it_is_measurable(store, outside) -> None:
    """年龄是**注入的当下**减租出时刻，所以「已过 30 小时」这句话可测。

    与 ``None`` 的区别是「读不出来」与「刚刚租出去」—— 后者在报告上看起来完全
    正常。
    """
    job_id, items = _dispatched(store, outside)
    store.lease(items[0].work_item_id, holder="claude-worker-2")
    leased_at = datetime.fromisoformat(store.attempts(items[0].work_item_id)[0].leased_at)
    moment = leased_at + timedelta(hours=30)

    report = reconcile_job(
        jobs=store, dispatcher=outside, job_id=job_id, classes=(ReconcileClass.LEASE,), now=moment
    )

    assert report.leases[0].age_seconds == pytest.approx(30 * 3600)
    assert "30.0 小时" in report.leases[0].reason


# ----------------------------------------------------------------------
# §3 的硬约束：不派发
# ----------------------------------------------------------------------
def test_reconciling_never_creates_an_external_task(store, outside) -> None:
    """第二件**从没派发过**的件，对账完之后仍然没有外部映射。

    这是「不 dispatch 新任务」唯一看得见的证据：对账跑过全部三类之后，那件没被
    交出去的工作单元身上**没有**新增一条映射，外面也没有新增一个任务。
    """
    job_id, items = _dispatched(store, outside, subjects=("devon-01", "devon-02"))
    _settle(store, items[0].work_item_id, StopReason.GOAL_MET)
    # 派发那一步会真的 ``create``，所以账单从这里开始记 —— 对账要证明的是它自己
    # 一次都没再建。
    created_before = len(outside.created_units)
    outside.calls.clear()

    reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=False)

    assert len(outside.created_units) == created_before
    assert "create" not in outside.calls
    assert store.execution_ref(items[1].work_item_id) is None


def test_a_stop_reason_that_does_not_exist_yet_stays_absent(store, outside) -> None:
    """没结算的件，对账跑完仍然没结算 —— 本模块不改业务结算。

    给它补一条停下的理由是**替它写结局**，而那件事本模块没有权力做：外面还在
    做，本地凭什么说它完了。
    """
    job_id, items = _dispatched(store, outside)

    reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=False)

    assert store.get_work_item(items[0].work_item_id).stop_reason is None
    assert board_target(store.get_work_item(items[0].work_item_id)) is None


def test_a_job_that_is_not_admitted_is_refused_not_reported(store, outside) -> None:
    """作业不存在 → 抛，**不是**一份空报告。

    一份「零件、零漂移」的报告读起来像「一切都对」，而它实际说的是「我们什么都没
    看」—— 那正是这个入口最不该制造的东西。
    """
    with pytest.raises(JobStateError):
        reconcile_job(jobs=store, dispatcher=outside, job_id="没有这个作业")


# ----------------------------------------------------------------------
# C 与 D：一次读，两个问题的答案
# ----------------------------------------------------------------------
def test_the_mapping_row_answers_found_and_drift_from_one_read(store, outside) -> None:
    """``found`` 说在不在，``drift`` 说对不对 —— 同一行，同一次读。"""
    job_id, items = _dispatched(store, outside)
    task_id = store.execution_ref(items[0].work_item_id).quay_task_id
    _restatus(outside, task_id, "done")

    report = reconcile_job(
        jobs=store, dispatcher=outside, job_id=job_id, classes=(ReconcileClass.MAPPING,)
    )

    row = report.mappings[0]
    assert (row.found, row.drift, row.observed_status) == (True, "external_ahead", "done")
    assert any(line == f"mapping:{items[0].work_item_id}:external_ahead" for line in report.needs_operator)


def test_an_external_row_that_is_gone_is_missing_not_unreadable(store, outside) -> None:
    """外面那一行没了 → ``missing``。**与「这一次没读成」分开**：一个是外面的
    结论，一个是我们这一侧的结论，而处置不同（去看谁删的 vs 去看执行器）。"""
    job_id, items = _dispatched(store, outside)
    del outside.tasks[store.execution_ref(items[0].work_item_id).quay_task_id]

    report = reconcile_job(
        jobs=store, dispatcher=outside, job_id=job_id, classes=(ReconcileClass.MAPPING,)
    )

    row = report.mappings[0]
    assert (row.found, row.drift) == (False, "missing")
    assert row.error


def test_two_external_rows_with_the_same_label_are_duplicate(store, outside) -> None:
    """外面同一件事有两条 → ``duplicate``。处置是**停下报告**，不是挑一条。

    挑一条会让「这件事被交出去两次」这件事在报告上消失 —— 而那正是 §8B 要找的。
    """
    job_id, items = _dispatched(store, outside)
    labels = link_labels(job_id, items[0].work_item_id)
    outside.seed("EXT-别的", labels, status="ready")

    report = reconcile_job(
        jobs=store, dispatcher=outside, job_id=job_id, classes=(ReconcileClass.MAPPING,)
    )

    assert report.mappings[0].drift == "duplicate"


def test_an_unwired_deployment_reports_unreadable_rather_than_clean(store) -> None:
    """没接线执行器 → 每一行都是 ``unreadable``，``found`` 是 ``None``。

    ``found=False`` 会读成「外面那一条没了」，而那是一个关于外面的结论 —— 这个
    进程**没有资格**下它。同理 ``drift`` 不能是 ``none``（那读成「对得上」）。
    """
    job_id, items = _dispatched(store, FakeDispatcher())
    _settle(store, items[0].work_item_id, StopReason.GOAL_MET)
    # 另起一个只读的部署：映射与结算都还在库里，但这一次没有执行器可以问。
    report = reconcile_job(jobs=store, dispatcher=None, job_id=job_id, dry_run=False)

    assert report.dry_run is False
    assert report.writes == (), "没接线却报了一次写"
    row = report.mappings[0]
    assert row.found is None
    assert row.drift == "unreadable"
    assert "没有接线" in row.error
    # ``board`` 那一段：目标状态照报（它是从库里读的），而同步没有发生 ——
    # 并且那**不是**一次「同步失败」，是这个进程没有可同步的对象。
    assert report.board[0].target_status == "done"
    assert report.board[0].changed is False
    assert "没有接线" in report.board[0].error


def test_a_read_that_itself_fails_is_unreadable_not_missing(store) -> None:
    """执行器**答不上来** → ``unreadable``，与 ``missing`` 是两个诊断。

    这一条走的是 ``_drift`` 那条路（有执行器，但它读不成），与 ``dispatcher=None``
    那条路不同：那一句是「这个进程没有执行器」，这一句是「执行器在，它没答上来」。
    处置不同 —— 一个去看服务面是怎么构造的，一个去看执行器。

    判据是「两个不同的 ``BoardReadKind`` 落到两个不同的 ``drift``」：把
    ``UNREADABLE`` 并进 ``MISSING``，会让一次执行器故障长得像一次人为删除。
    """
    outside = _RefusingDispatcher()
    job_id, items = _dispatched(store, outside)
    outside.refusing = True

    report = reconcile_job(
        jobs=store, dispatcher=outside, job_id=job_id, classes=(ReconcileClass.MAPPING,)
    )

    row = report.mappings[0]
    assert (row.found, row.drift) == (None, "unreadable")
    assert "执行器起不来" in row.error


def test_the_age_guard_reads_a_bad_timestamp_as_unreadable_not_as_zero() -> None:
    """``_age_seconds`` 的三条分支。**直接调私有助手**，因为其中两条经公开面造不出来。

    没有任何 ``JobStore`` 会写下一个不带时区的 `` leased_at``，也没有一个会写一段
    不是时间戳的字符串 —— 那两条是**防的是将来的存储实现**。判据仍然要活着：一个
    「读不出来就当 0」的兜底会让「刚刚租出去」与「读不出来」在报告上长得一样，而
    前者看起来完全正常。
    """
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    assert _age_seconds("不是时间戳", now) is None
    assert _age_seconds("2026-09-22T12:00:00", now) is None, "无时区的时刻被当成本地时间算了"
    assert _age_seconds("2026-09-22T12:00:00+00:00", now) == pytest.approx(24 * 3600)


def test_a_lease_and_a_mapping_share_one_read_of_the_outside(store, outside) -> None:
    """``lease`` 与 ``mapping`` 两段用的是**同一次**读数。

    各自去读一遍会得到两份报告，而它们可以各自说出一个不同的外面的样子 —— 读者
    没有任何依据判断哪一段是新的。
    """
    job_id, items = _dispatched(store, outside)
    store.lease(items[0].work_item_id, holder="claude-worker-2")
    outside.calls.clear()

    reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=True)

    assert outside.calls.count("find") == 1


def test_only_settled_items_get_a_board_conclusion(store, outside) -> None:
    """``board`` 那一段只碰已结算的件 —— 给还在跑的活下结论是抢在它前面写结局。

    而 ``mapping`` 那一段含**全部已映射**的件：外面比本地靠前那类漂移只在它的
    视野里。
    """
    job_id, items = _dispatched(store, outside, subjects=("devon-01", "devon-02"))
    _settle(store, items[0].work_item_id, StopReason.GOAL_MET)

    report = reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=True)

    assert len(report.board) == 1
    assert report.board[0].task_id == store.execution_ref(items[0].work_item_id).quay_task_id
    assert len(report.mappings) == 1, "没派发过的那件没有映射，不该出现在映射报告里"


def test_the_report_names_the_classes_it_actually_ran(store, outside) -> None:
    """点名了一类就跑一类，而报告里的**名字与次序**来自闭集。

    调用方给的顺序不进报告：同一份状态的两份报告不该在 diff 里看起来不同。
    """
    job_id, items = _dispatched(store, outside)

    report = reconcile_job(
        jobs=store,
        dispatcher=outside,
        job_id=job_id,
        classes=(ReconcileClass.MAPPING, ReconcileClass.BOARD),
        dry_run=True,
    )

    assert report.classes == ("board", "mapping")
    assert report.leases == ()


def test_an_empty_class_list_means_all_of_them(store, outside) -> None:
    """不给 ``--class`` 就是三类都做，而不是「一类都不做」。"""
    job_id, items = _dispatched(store, outside)
    store.lease(items[0].work_item_id, holder="claude-worker-2")

    report = reconcile_job(jobs=store, dispatcher=outside, job_id=job_id, dry_run=True)

    assert report.classes == tuple(cls.value for cls in ALL_CLASSES)
    assert report.leases and report.mappings


# ----------------------------------------------------------------------
# 运维面：一个 CLI 动词，**不是**一个服务面工具
# ----------------------------------------------------------------------
def test_the_operator_surface_is_a_cli_verb_and_defaults_to_dry_run() -> None:
    """``reconcile`` 在 CLI 上真的存在，而且 ``--apply`` 不给就是预演。

    ``--apply`` 是**要**出来的那一样：它的调用者是运维，写的是外部看板，而本仓
    的判据看不见外面（``EXTERNAL_STATUSES`` 只管写出去的值在不在闭集里）。所以
    「先预演」是缺省。
    """
    args = build_parser().parse_args(["reconcile", "--job-id", "job-1"])

    assert args.job_id == "job-1"
    assert args.apply is False
    assert args.reconcile_class == []
    assert build_parser().parse_args(["reconcile", "--job-id", "job-1", "--apply"]).apply is True


def test_reconcile_is_not_exposed_on_the_service_face() -> None:
    """它**不**在 ``TOOL_CATALOG`` 里 —— 服务面是给研究员的，这是给运维的。

    挂上去会让「谁能在生产上改外部看板」多出一个主体（任何一个持研究员令牌的
    进程），而它对研究这件事没有任何用处。
    """
    assert "reconcile" not in TOOL_CATALOG


def test_an_unknown_class_is_a_parameter_error_not_a_deployment_error(monkeypatch, capsys) -> None:
    """拼错的 ``--class`` 退 2，且**在装载部署之前**就退。

    这两件事的处置不同：``--class`` 写错了是**这一次调用**不成立，
    ``MARKETOPS_DATA_ROOT`` 没设是**这个部署**没配好。先装载的那一版会让前者在
    一台没配好的机器上报成「没有数据目录」，而人会去修错的那一样。
    """
    monkeypatch.delenv("MARKETOPS_DATA_ROOT", raising=False)

    code = main(["reconcile", "--job-id", "job-1", "--class", "nope"])

    assert code == 2
    assert "不认识的类别" in capsys.readouterr().err

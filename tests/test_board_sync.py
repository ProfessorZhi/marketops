"""G5c：结算之后把结局回传到外部看板 —— ``board_sync``。

## 为什么是「收敛」而不是「迁移」

结算是一次只发生一次的事件，回传是它**之后**的一次外呼，两者不可能原子。于是
这条路必然会被走到第二次：响应丢了、客户端重发、进程重启之后再调一次。一次
**迁移**式的回传在第二次会撞 CAS 冲突（看板上早就是目标状态了），而冲突报文说
的是「有别人先写了」—— **一条凭空造出来的并发**。

所以回传判的是「看板此刻等不等于映射要求的状态」：等就什么都不做，不等才发一次
CAS。``BoardSync.changed`` 与 ``synced`` 因此是**两个**字段 —— 前者说「这一次真的
动了吗」，后者说「现在对上了吗」，而重发恰好落在 ``changed=False, synced=True``
那一格。

## 失败不升级成错误

本地结算与外部回传是两件事，而**前者已经发生**。把回传失败报成一次 ``ToolError``
会让调用方以为产出没落盘，而它落盘了 —— 于是重发，重发拿到 ``replayed: true``。
一个「说错了但做完了」的中间态比一条明确的失败更坏。

所以失败进 ``BoardSync.error``，回执照常返回。

## 替身是那个**记账的** ``FakeDispatcher``

它的 ``transitions`` 记的是三元组而非单个状态：一次 CAS 的**两端**都是判据 ——
只记目标状态，会让「expected 也写成了目标」这种自说自话的迁移全绿。
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence

import pytest

from marketops.adapters.memory import InMemoryJobStore
from marketops.application.dispatch import (
    EXTERNAL_STATUSES,
    INITIAL_EXTERNAL_STATUS,
    STOP_REASON_EXTERNAL_STATUS,
    WorkDispatch,
    board_target,
    link_labels,
)
from marketops.domain.enums import JobInputKind, StopReason
from marketops.domain.errors import DispatchError, JobStateError, LeaseError
from marketops.domain.findings import Findings
from marketops.domain.job import WorkItem
from marketops.domain.util import sha256_text
from marketops.ports.dispatch import ExternalTask
from marketops.ports.jobs import JobStore
from tests.fakes import FakeDispatcher, synthetic_dossiers, synthetic_scene_files

SCENE = "uk-acme-natural-grass"
KEY = "board-sync-v1"

#: 编译时冻住的规则。合成的，理由同 ``tests/test_dispatch_ordering.py``：
#: 快照只是 ``compile_work_items`` 的必填输入，不是被测对象 —— 但**必须是真的一批
#: 字节**，因为版本号是算出来的，而这里要的是一个说得清指称对象的版本。
SCENE_FILES = synthetic_scene_files()


# ----------------------------------------------------------------------
# 脚手架
# ----------------------------------------------------------------------
def _compiled(
    store: JobStore, *, subjects: Sequence[str], key: str
) -> tuple[str, tuple[WorkItem, ...]]:
    """受理 + 编译，**不派发、不结算** —— 三件都留给判据自己摆。"""
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
) -> tuple[str, str]:
    """受理 + 编译 + 派发**第一件**，返回 ``(job_id, work_item_id)``。

    走到「外面已经有这个任务」为止 —— 回传的前提正是那条映射存在。
    """
    job_id, items = _compiled(store, subjects=subjects, key=key)
    WorkDispatch(jobs=store, dispatcher=outside).dispatch(items[0].work_item_id)
    return job_id, items[0].work_item_id


def _settled(store: JobStore, work_item_id: str, reason: StopReason) -> str:
    """把一件已派发的工作单元**真的结算掉**，返回它的 id。

    回传的前提是结算：目标状态从 ``settle`` 落库的那条理由读出来
    （``board_target``）。在结算之前 ``reconcile`` 无话可说 —— 那是这一版修改的
    全部内容，所以脚手架必须真的走到结算，**不能**在调用里塞一个理由了事：塞得
    进去的那个版本正是这次要删掉的东西。
    """
    attempt = store.lease(work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=reason, findings=Findings())
    return work_item_id


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def outside() -> FakeDispatcher:
    return FakeDispatcher()


@pytest.fixture
def sync(store: InMemoryJobStore, outside: FakeDispatcher) -> WorkDispatch:
    return WorkDispatch(jobs=store, dispatcher=outside)


# ----------------------------------------------------------------------
# 映射本身
# ----------------------------------------------------------------------
def test_every_stop_reason_has_a_mapping() -> None:
    """六个成员一个不缺。**这条判据挡的是「加了一个停止理由」那次。**

    ``StopReason`` 是闭集，而映射是它的**第二份**清单。少一个键的后果不是
    ``KeyError`` 那么简单：那个成员走到结算时才会炸，而那已经是产出落盘之后。
    所以判据钉在「集合相等」上，加成员的那次提交会在这里红。
    """
    assert set(STOP_REASON_EXTERNAL_STATUS) == set(StopReason)


def test_the_mapping_only_lands_on_statuses_the_board_knows() -> None:
    """值域必须是外部闭集的子集。**这条判据挡的是一个本仓编出来的状态名。**

    写一个外部没有的词进去，外部会拒绝 —— 但那是在一次真实结算之后。更坏的是
    它可能**不**拒绝：状态名是自由的字符串，一个不存在的词会被原样存下来，于是
    看板上出现一个谁也不认识的状态，而没有任何一方报错。

    闭集本身是从**实现**那侧读的（执行器自己的状态列表 的 ``--status`` 过滤值），
    理由同 ``INITIAL_EXTERNAL_STATUS`` 的 docstring。
    """
    assert set(STOP_REASON_EXTERNAL_STATUS.values()) <= EXTERNAL_STATUSES
    assert INITIAL_EXTERNAL_STATUS in EXTERNAL_STATUSES


def test_work_is_filed_in_the_status_the_executor_scans() -> None:
    """初始状态钉在 ``"ready"`` 上 —— 而这条判据钉的是**一个决定**，不是一条事实。

    边界要说清楚：本仓手上没有那个执行器，所以「它到底扫哪个状态」在这里**判不
    了**。判得了的是「有人改了这个值」—— 把 ``"todo"`` 改回来（或改成任何别的
    词）的那次提交会在这里红，而它**必须**红：``todo`` 是这条链路上唯一一种
    **静默**失效的取值。任务建出来、看板上看得见、谁也不动它、两边都不报错，与
    「执行器根本没接线」逐字同形。

    证据不在这条判据里，在 ``INITIAL_EXTERNAL_STATUS`` 的 docstring 里（2026-09-22
    在 0.11.0 上量到 worker-driver 每轮只扫 ``ready``）。这条测试的职责是让那个
    取值**必须经过一个看得见的地方**才改得动 —— 不是把一条外部事实说成一条本仓
    能自证的断言，那会是一条说谎的判据。
    """
    assert INITIAL_EXTERNAL_STATUS == "ready"


def test_the_two_ends_are_split_by_whether_a_human_is_owed_an_action() -> None:
    """分界线是「还欠不欠人一个动作」，不是「成没成功」。

    ``no_useful_next_action`` 与 ``goal_met`` 同归 ``done``：它说的是「该做的
    做完了，没有下一步」，不是「我卡住了」。把它归进待人工，会让一次性研究最
    常见的诚实终点永久亮在 ``needs-human`` 里 —— 而那盏灯亮久了就没人看了。
    """
    assert STOP_REASON_EXTERNAL_STATUS[StopReason.GOAL_MET] == "done"
    assert STOP_REASON_EXTERNAL_STATUS[StopReason.NO_USEFUL_NEXT_ACTION] == "done"
    for waiting in (
        StopReason.BUDGET_EXHAUSTED,
        StopReason.BLOCKED,
        StopReason.AMBIGUOUS_HOLD,
        StopReason.MANUAL_PAUSE,
    ):
        assert STOP_REASON_EXTERNAL_STATUS[waiting] == "needs-human", waiting


# ----------------------------------------------------------------------
# 收敛
# ----------------------------------------------------------------------
def test_a_settled_item_moves_the_board_to_the_mapped_status(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """正常路径：读一次当下状态，再发一次 CAS，两端都写对。"""
    job_id, work_item_id = _dispatched(store, outside)
    task_id = store.execution_ref(work_item_id).dispatch_task_id
    _settled(store, work_item_id, StopReason.BLOCKED)

    result = sync.reconcile(work_item_id)

    assert outside.transitions == [(task_id, INITIAL_EXTERNAL_STATUS, "needs-human")]
    assert result.synced is True
    assert result.changed is True
    assert result.error is None
    assert result.task_id == task_id
    assert result.target_status == "needs-human"
    assert result.observed_status == INITIAL_EXTERNAL_STATUS
    # 查的是**这一组** label，且两个都在 —— 少一个会读到别人的任务。
    assert outside.find_labels[-1] == link_labels(job_id, work_item_id)


def test_an_already_converged_board_is_not_touched(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """看板已经是目标状态 ⇒ 一次 CAS 都不发。

    这一格就是重发路径：上一次回传成功之后又结算一次（``replayed``）。发 CAS
    会撞冲突，而冲突报文读起来像「有别人先写了」—— 那是本仓最不该制造的一类
    假事实。``changed=False`` 与 ``synced=True`` 一起说清「没动，但已经对了」。
    """
    _, work_item_id = _dispatched(store, outside)
    task_id = store.execution_ref(work_item_id).dispatch_task_id
    _settled(store, work_item_id, StopReason.BLOCKED)
    # 把外面那个任务**就地**改成目标状态：labels 不动，只动状态 —— 模拟「上一次
    # 回传已经成功过一次」。换一个新任务会让这条判据变成「读到了别的任务」。
    outside.tasks[task_id] = ExternalTask(
        external_id=task_id,
        status="needs-human",
        labels=outside.tasks[task_id].labels,
    )

    result = sync.reconcile(work_item_id)

    assert outside.transitions == [], "已经收敛就不该再发一次 CAS"
    assert result.synced is True
    assert result.changed is False
    assert result.observed_status == "needs-human"


def test_reconciling_twice_is_idempotent(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """连着两次，第二次什么都不做 —— 这是「回传可以安全重放」的全部内容。"""
    _, work_item_id = _dispatched(store, outside)
    _settled(store, work_item_id, StopReason.GOAL_MET)

    first = sync.reconcile(work_item_id)
    second = sync.reconcile(work_item_id)

    assert first.changed is True
    assert second.changed is False
    assert second.synced is True
    assert len(outside.transitions) == 1, "第二次不该在外部留下第二次调用"


# ----------------------------------------------------------------------
# 失败路径：一律回报，不抛
# ----------------------------------------------------------------------
def test_a_write_back_failure_is_reported_not_raised(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """回写炸了，回执必须照常给出，且**说清没同步**。

    把这一格报成异常，等于让调用方以为结算也没成 —— 而三行早已落库。于是它
    重发，拿到 ``replayed: true``，而看板永远没被同步过：一个既没成功也没报错
    的中间态。
    """
    _, work_item_id = _dispatched(store, outside)
    _settled(store, work_item_id, StopReason.BLOCKED)

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise DispatchError("执行器起不来")

    outside.find = _explode  # type: ignore[method-assign]

    result = sync.reconcile(work_item_id)

    assert result.synced is False
    assert result.error is not None and "执行器起不来" in result.error
    assert result.changed is False


def test_a_mapping_that_points_at_nothing_is_reported(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """映射在，任务不在外面 —— 说清是**哪一件事**不对，不升级成异常。"""
    _, work_item_id = _dispatched(store, outside)
    _settled(store, work_item_id, StopReason.BLOCKED)
    outside.tasks.clear()

    result = sync.reconcile(work_item_id)

    assert result.synced is False
    assert result.error is not None
    assert result.target_status == "needs-human", "失败也要报出它本来要去哪"


def test_an_item_that_was_never_dispatched_reports_no_task(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """没派发过 ⇒ 外面没有可同步的东西，``task_id`` 是 ``None`` 而不是空串。

    ``None`` 与 ``""`` 在这里的区别是「没有这件事」与「这件事的 id 是空的」，
    而只有前者可能出现。写成 ``""`` 会让调用方以为有一条 id 为空的任务。
    """
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026-09",
        idempotency_key="never-dispatched",
        request_sha256=sha256_text("never-dispatched"),
    )
    items = store.compile_work_items(job.job_id, synthetic_dossiers("devon-01"), scene=SCENE_FILES)

    result = sync.reconcile(items[0].work_item_id)

    assert result.task_id is None
    assert result.synced is False
    assert result.error is not None
    assert outside.calls == [], "没有映射就不该去碰外部"


def test_a_cas_conflict_is_reported_with_the_actual_state(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """别人在我们读过之后改了状态 ⇒ CAS 拒了，外面那个**真实**状态在报文里。

    这一格与「回写失败」分开：冲突说的事情是「你手上这个判断过期了」，它的处置是
    **重读再决定**，不是重试。所以外部报回来的那段报文必须原样留在 ``error`` 里，
    而不是被压成一句「失败了」。

    构造的是**真实的** TOCTOU：``find`` 回报一个陈旧的状态（``todo``），而外面
    实际已经是 ``ready`` —— 于是 CAS 的 ``expected`` 撞上真实值。手写一句冲突
    报文去喂它，测的就只是「我们把那个字符串放回去了吗」。

    顺带钉住 ``observed_status`` 的语义：它报的是**我们读到的**那一个（陈旧值），
    不是外面真实的那一个。想读真实值只有 ``error``。
    """
    _, work_item_id = _dispatched(store, outside)
    task_id = store.execution_ref(work_item_id).dispatch_task_id
    _settled(store, work_item_id, StopReason.BLOCKED)
    outside.tasks[task_id] = ExternalTask(
        external_id=task_id, status="ready", labels=outside.tasks[task_id].labels
    )
    real_find = outside.find

    def _stale(*, labels: Sequence[str]) -> tuple[ExternalTask, ...]:
        return tuple(
            ExternalTask(external_id=task.external_id, status="todo", labels=task.labels)
            for task in real_find(labels=labels)
        )

    outside.find = _stale  # type: ignore[method-assign]

    result = sync.reconcile(work_item_id)

    assert result.synced is False
    assert result.changed is False
    assert result.observed_status == "todo", "报的是我们读到的那一个，不是外面真实的那一个"
    assert result.error is not None and "ready" in result.error, (
        "外面真实的状态必须出现在报文里，否则这一条只能读出一句「失败了」"
    )


# ----------------------------------------------------------------------
# 目标状态**从哪来** —— 这一节的形状是 2026-09-22 那次运行留下的
# ----------------------------------------------------------------------
def test_the_target_is_not_a_parameter_of_reconcile() -> None:
    """``reconcile`` 的参数表里**没有** ``stop_reason`` —— 判据是一行签名。

    它曾经有，而那个参数就是那次失败的成因：进程死在结算与回传之间以后，下一次
    调用**手上没有那个理由**，于是没有任何一条路径能把看板推到位 —— 2026-09-22
    那次靠操作者 drain 收的场，而看板停在了 ``ready`` 上。

    参数回来了那条失败就回来了，而它回来时**不会有任何别的判据变红**：调用方传
    它刚交给 ``settle`` 的那条理由，看起来完全正常。所以这条判据与
    ``tests/test_shadow.py`` 里那句 ``"mode" not in signature(...)`` 同形 ——
    一个被删掉的开关要有一行字看着它别回来。

    ## ``dry_run`` 为什么在集合里

    因为这条判据管的是**「要收敛到哪」只能有一个来源**，而 ``dry_run`` 不是那个
    值：它一个状态名都不是，改不动目标，改的只是「这一次要不要把外面写下去」。
    把它也挡在外面会让运维失去一个只读的预演入口，而那个损失的后果是运维**直接
    对着活库 apply** —— 比多一个模式参数更坏。

    集合仍然是**精确相等**，所以「有人加回一个 ``target`` / ``stop_reason`` /
    ``status``」依旧会红：那三个都是状态名，加一个进来立刻多一个元素。
    """
    assert set(inspect.signature(WorkDispatch.reconcile).parameters) == {
        "self",
        "work_item_id",
        "dry_run",
    }


def test_the_target_reads_the_settled_reason_and_nothing_else(
    store: InMemoryJobStore, outside: FakeDispatcher
) -> None:
    """``board_target`` 读的就是 ``stop_reason`` 那一格：没结算 ⇒ ``None``。

    ``None`` 不是 ``""`` —— 空串是「要收敛到一个名字为空的状态」，而外部闭集里
    没有那个词（``EXTERNAL_STATUSES`` 那条判据钉着这一点）。这一格说的是「还没
    有结局」，它的处置是「先把这件事结算掉」。
    """
    _, work_item_id = _dispatched(store, outside)
    item = store.get_work_item(work_item_id)
    assert item is not None
    assert board_target(item) is None

    _settled(store, work_item_id, StopReason.BUDGET_EXHAUSTED)
    settled = store.get_work_item(work_item_id)
    assert settled is not None
    assert board_target(settled) == "needs-human"


@pytest.mark.parametrize("reason", list(StopReason), ids=lambda member: member.value)
def test_the_target_is_whatever_the_settled_reason_says(
    store: InMemoryJobStore, outside: FakeDispatcher, reason: StopReason
) -> None:
    """六条结局各收敛到各的 —— 一个写死的 ``needs-human`` 会在这个参数化上红。

    判据落在**外面真的变成的样子**上（``outside.tasks[...].status``），不是只落在
    ``BoardSync.target_status`` 上：后者是这一层的自述，而自述与事实分叉正是这里
    要挡的那件事。顺带钉住「目标不可由调用方给」的后果：这条路径上唯一的那个人
    是 ``settle``。
    """
    _, work_item_id = _dispatched(store, outside)
    ref = store.execution_ref(work_item_id)
    assert ref is not None
    _settled(store, work_item_id, reason)

    result = WorkDispatch(jobs=store, dispatcher=outside).reconcile(work_item_id)

    assert result.target_status == STOP_REASON_EXTERNAL_STATUS[reason]
    assert outside.tasks[ref.dispatch_task_id].status == STOP_REASON_EXTERNAL_STATUS[reason]


def test_an_item_that_has_not_settled_has_no_target_and_touches_nothing_outside(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """没结算 ⇒ 没有目标，而且**外部一次都不碰**。

    断 ``outside.calls`` 而不是只断返回值：一个「先读一遍外面再决定」的实现会给出
    一模一样的回执，却在外部留下一次真实调用。没有结局就没有可收敛的目标，这一格
    的处置是「先结算」，不是「再读一次」。
    """
    _, work_item_id = _dispatched(store, outside)
    calls_before = list(outside.calls)

    result = sync.reconcile(work_item_id)

    assert result.target_status is None
    assert result.synced is False
    assert result.changed is False
    assert result.error is not None and "结算" in result.error
    assert outside.calls == calls_before


# ----------------------------------------------------------------------
# 收敛扫描：结算之后没能回传的那些件，由谁收尾
# ----------------------------------------------------------------------
def test_a_sweep_lands_a_settlement_whose_write_back_failed(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """**这一条就是那次运行的形状**：结局落库了，回传那一刻的外呼全失败。

    之后**没有任何人**带着上一次的理由回来 —— ``converge_settled`` 收的是一个
    job id，参数表里没有地方放理由 —— 而看板必须自己走到位。
    """
    job_id, items = _compiled(store, subjects=("devon-01", "devon-02"), key=KEY)
    for item in items:
        sync.dispatch(item.work_item_id)
        _settled(store, item.work_item_id, StopReason.BLOCKED)

    def _explode(**_kwargs: object) -> None:
        raise DispatchError("执行器起不来")

    real_find = outside.find
    outside.find = _explode  # type: ignore[method-assign]
    assert sync.reconcile(items[0].work_item_id).synced is False
    assert {task.status for task in outside.tasks.values()} == {"ready"}, (
        "这一格要的是「库里有结局、外面还是 ready」—— 那正是外部会再派一次的状态"
    )

    outside.find = real_find  # type: ignore[method-assign]
    sweep = sync.converge_settled(job_id)

    assert sweep.considered == 2
    assert sweep.pending == 0, "扫描没把这两件推到位"
    assert sweep.pushed == 2
    assert {task.status for task in outside.tasks.values()} == {"needs-human"}


def test_a_sweep_over_a_converged_job_reads_the_board_and_writes_nothing(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """都到位了 ⇒ ``pushed`` 与 ``pending`` 都是 0，但**读照读**。

    两个零一起读才是「这一趟什么都没做，因为不需要做」—— 只报一个 ``pushed: 0``
    会让「一切正常」与「一件都没扫到」长得一样。所以这里同时断外部**被读过**
    （``considered``）而**没被写过**（``transitions`` 一条不多）：一个什么都不做的
    空扫描会让前半句红。
    """
    job_id, work_item_id = _dispatched(store, outside)
    _settled(store, work_item_id, StopReason.GOAL_MET)
    sync.reconcile(work_item_id)
    reads_before = outside.calls.count("find")
    transitions_before = list(outside.transitions)

    sweep = sync.converge_settled(job_id)

    assert sweep.considered == 1
    assert outside.calls.count("find") == reads_before + 1, "这一趟必须真的去读了"
    assert sweep.pushed == 0 and sweep.pending == 0
    assert outside.transitions == transitions_before


def test_an_item_that_was_never_dispatched_cannot_be_settled(
    store: InMemoryJobStore, outside: FakeDispatcher
) -> None:
    """已结算 ⟹ 已映射 —— 所以 ``unmapped`` 今天恒为 0，这一条钉的是那个蕴含。

    结算只收**活租约**，而租约要求先有 ``ExecutionRef``（``JobStore.lease`` 的
    拒绝报文自己写着这一句），映射又是**写一次**的。于是「已经结算、外面没有映射」
    这个状态**没有输入走得到** —— 与 ``dispatch`` 里那条被省掉的分支同源。

    那 ``BoardConvergence`` 为什么还留着 ``unmapped`` 这一格？因为它是一类**必须
    报得出来**的数字：真到了那一天（有人给映射加了解除入口，或者让 ``settle`` 不收
    租约），一件没有外部映射的结算件会被并进 ``pending`` —— 看板上报「待同步」，
    而外面从来没有过这件事。这条判据就是那一天的红灯。
    """
    _, items = _compiled(store, subjects=("devon-01",), key=KEY)

    with pytest.raises(LeaseError):
        store.lease(items[0].work_item_id, holder="worker-a")

    assert store.execution_ref(items[0].work_item_id) is None


def test_the_sweep_counts_settled_and_unsettled_items_apart(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """结算过的与还没结算的 —— 分开数，而两者的**和**等于这个作业的件数。

    ``unsettled`` 不是「待同步」：外面还在做这件事，替它下一个结论就是抢在它前面
    写结局。并进 ``pending``，一个刚派出去的作业会永远报着待同步 —— 而一个永远
    亮着的数字与一盏永远亮着的灯一样，亮久了就没人看了。"""
    job_id, items = _compiled(store, subjects=("devon-01", "devon-02", "devon-03"), key=KEY)
    sync.dispatch(items[0].work_item_id)
    _settled(store, items[0].work_item_id, StopReason.GOAL_MET)
    # items[1] / items[2] 既没派发也没结算 —— 它们落在 unsettled 那一格。

    sweep = sync.converge_settled(job_id)

    assert (sweep.considered, sweep.unmapped, sweep.unsettled) == (1, 0, 2)
    assert sweep.considered + sweep.unmapped + sweep.unsettled == len(
        store.work_items(job_id)
    ), "三个计数加起来必须等于这个作业的件数，否则它们解释不了任何事"
    assert sweep.pending == 0
    assert sweep.pushed == 1


def test_a_sweep_over_a_job_that_does_not_exist_is_refused(
    store: InMemoryJobStore, outside: FakeDispatcher, sync: WorkDispatch
) -> None:
    """作业不存在是**异常**，不是一格空的收敛 —— 理由同 ``dispatch_job``：
    一个拼错的 job_id 不该看起来像一次成功的空跑，而外面也一次都不该被碰。"""
    with pytest.raises(JobStateError) as caught:
        sync.converge_settled("no-such-job")

    assert "no-such-job" in str(caught.value)
    assert outside.calls == []

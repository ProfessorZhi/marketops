"""**运维对账**：把历史投影对齐，而**不开始任何新的付费工作**。

## 它为什么必须是一个独立入口

在看板收敛这条路上，本仓原来只有 ``WorkDispatch.converge_settled``，而它唯一的
调用点在 ``tools.py::_dispatch_block`` —— 也就是**派发轮次**之后。于是「把已经
结算的那几件的看板推到位」与「再派一件新的活」在**代码上是同一件事**：

* 要收敛，就得走一次派发路径；
* 而派发路径会真的把活交出去 —— 真实 worker、真实 Serper、真实账单。

2026-09-22 那次运行之后，历史作业上留着两件待收敛的看板（一件已结算仍是
``todo``），而「只把这两件对齐」这件事**在当时写不出来**。运维能选的只有
「再花一次钱」或者「手工改外部那一行」—— 后者让外部与本地各自以为自己是对的。

本模块把那个耦合拆开：一个入口，只做对账，**不派发**。

## 硬约束（每一条都由判据钉着，不是自觉）

* **不派发。** 本模块只碰 ``JobStore`` 的读、``Dispatcher.find``（一次读）与
  ``Dispatcher.transition``（一次写）。它**不 import** 派发路径上的任何东西，
  也不构造 ``ExecutionUnit`` —— 没有 unit 就没有 ``create``。
* **不调任何付费 provider。** 这里没有 ``SearchProvider`` / ``DecisionProvider``
  / ``DocumentAcquisition`` 的位置：对账要读的是**外部看板**，那是执行器的状态，
  不是研究的内容。
* **不起 Claude worker。** 同上：本模块不知道 agent runtime 是什么。
* **不改业务结算。** ``JobStore`` 的写动词里它只用一个 ``transition``（外部），
  一个本地写都不做 —— 没有 ``settle``、没有 ``release``、没有 ``set_state``。
* **不偷偷放租约。** 见 ``LEASE`` 那一节：本仓**没有**租约到期规则，所以这里
  给不出「它过期了」这个结论，只给得出「它已经这么久了」。处置一律
  ``NEEDS_OPERATOR``。
* **dry-run 一次写都不产生。** 判据是执行器那一侧 ``transition`` 的调用计数为 0，
  不是「我们没打算写」。
* **apply 的每一次写都列在回执里。** 见 ``ReconcileReport.writes``。

## §3 列了四类对账，本模块实现成**三类**，理由如下

A（已结算 → 看板收敛）、B（悬挂/陈旧租约检测）、C（``execution_ref`` 存在性回读）、
D（看板漂移报告）四类里，**C 与 D 是同一次读上的两个问题**：都要按 label 把外面
那一行读回来，区别只在「读回来之后问什么」（C 问「它在不在」，D 问「它是不是目标
状态」）。拆成两个类会得到两次读，或者第二份读取实现 —— 而第二份读取实现正是
``WorkDispatch._observe`` 的 docstring 里那段「运维看到一份与实际执行不同的板面」
要说的事。所以 C 与 D 合成 ``MAPPING`` 一类，两个问题的答案都在同一行里
（``found`` 与 ``drift``）。

A 与 D 也不重合，理由在 ``BoardObservation`` 的 docstring 里：A 只碰已结算的件
（给还在跑的活下结论是抢在它前面写结局），D 要看全部 —— 「外面已经 ``done``、
本地还没结算」那类漂移只在 D 的视野里。

## 本模块不做的事

* **不修任何一件研究内容。** 对账管的是「本地那张映射与外面那张表对不对得上」，
  不是「那次研究做得对不对」。
* **不删除、不重建外部任务。** ``MISSING`` 的处置是「停下报告」——
  ``_observe`` 的报文里逐字写着为什么不重建。
* **不算账。** 预算与账单在 ``BudgetLedger`` 那一侧；这里一次付费调用都没有，
  所以没有可算的东西。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from ..domain.enums import AttemptState
from ..domain.errors import JobStateError
from ..domain.job import JobAttempt, WorkItem
from ..ports.dispatch import Dispatcher
from ..ports.jobs import JobStore
from .dispatch import (
    BoardObservation,
    BoardRead,
    BoardReadKind,
    BoardSync,
    WorkDispatch,
    board_target,
)

__all__ = [
    "ALL_CLASSES",
    "DRIFT_KINDS",
    "LEASE_DISPOSITIONS",
    "LeaseRow",
    "MappingRow",
    "ReconcileClass",
    "ReconcileReport",
    "reconcile_job",
]


class ReconcileClass(str, Enum):
    """可以对账的三类。**闭集**，而 ``apply`` 只对其中一类有写。

    一个类别一旦能被点名，就必须说得出「它会不会写外面」—— 否则
    ``--class lease --apply`` 读起来像「把陈旧租约收掉」，而实际发生的事是
    「什么都没做」。所以每一条的 docstring 都写明它的写行为。
    """

    #: A：已结算且已映射的件 → 收敛到目标状态。**唯一会写外面的一类。**
    BOARD = "board"
    #: B：活租约 → 逐条报出来，附年龄与外部的读数。**从不写**，见 ``_lease_rows``。
    LEASE = "lease"
    #: C+D：每一件已映射的件的板面读数 —— 那一行在不在、它是不是目标状态。
    #: **从不写**，哪怕加了 ``--apply``：它是一份报告。
    MAPPING = "mapping"

    @property
    def writes_outside(self) -> bool:
        """这一类在 ``--apply`` 下会不会写外面。**是从闭集上读出来的**，不是在
        报告里顺手写的一句话 —— 一处写、一处读，两者不会分家。"""
        return self is ReconcileClass.BOARD


#: 没点名 ``--class`` 时跑的全部类别。顺序是**稳定的**：它决定报告里各段的次序。
ALL_CLASSES: tuple[ReconcileClass, ...] = (
    ReconcileClass.BOARD,
    ReconcileClass.LEASE,
    ReconcileClass.MAPPING,
)


@dataclass(frozen=True)
class LeaseRow:
    """一条活租约，以及「它该不该被收回」这个问题的**答案与理由**。

    ``disposition`` 今天恒为 ``NEEDS_OPERATOR``，而那**不是**一个偷懒的默认值 ——
    它是本仓的一条设计决定在这个入口上的投影：``ports/jobs.py`` 逐字写着
    「没有 ``lease_expires_at`` / ``reap``。租约不按时钟自动失效」，理由也在那里
    （猜错「那个 worker 死了」的代价是两个 worker 跑同一件昂贵的事）。

    于是这个入口**没有**「它过期了」这个结论可以给 —— 给得出的是「它已经这么久
    了」和「外面那一行现在长什么样」。凭这两样收回一条租约，就是在这里现编一条
    到期规则；而现编的那一条不会有任何判据看着它。

    ``age_seconds`` 是 ``None`` **当且仅当** ``leased_at`` 读不出时间戳 ——
    与 ``0.0`` 的区别是「读不出来」与「刚刚租出去」，而后者是一个在报告上看起来
    完全正常的错误答案。
    """

    work_item_id: str
    subject_ref: str
    attempt_id: str
    ordinal: int
    holder: str
    leased_at: str
    age_seconds: float | None
    #: 外面那一行此刻的读数。``None`` = 没读到（原因在 ``external_error``）。
    external_status: str | None
    external_error: str | None
    #: 闭集两员 —— 见类 docstring。``RELEASABLE`` 今天**没有任何生产者**，留着它
    #: 是为了让「本入口不支持自动收回」这件事是**读得出来的**，而不是一个读者要
    #: 去猜「为什么这里只有一种」。一个只有一员的枚举与一句注释的区别就在这里。
    disposition: str
    reason: str


#: ``LeaseRow.disposition`` 的闭集。写成模块常量而不是散在代码里的字面量，
#: 因为报告是机器读的（``--json``），一个拼错的词会在下游静默地不被认识。
LEASE_DISPOSITIONS = ("NEEDS_OPERATOR", "RELEASABLE")


@dataclass(frozen=True)
class MappingRow:
    """一件已映射的件：外面那一行在不在、它是什么状态、它是不是目标状态。

    ``drift`` 是**闭集**，而它把 §3 的 C 与 D 合成一行：``found`` 回答「在不在」，
    ``drift`` 回答「对不对」。两个问题共用同一次读（``BoardRead``），所以它们
    不可能一个说「在」而另一个基于另一份读数说「不在」。
    """

    work_item_id: str
    subject_ref: str
    quay_task_id: str
    stop_reason: str | None
    target_status: str | None
    observed_status: str | None
    found: bool | None
    drift: str
    error: str | None


#: ``MappingRow.drift`` 的闭集。**七员，各自处置不同**：
#:
#: * ``none`` —— 对得上，什么都不用做。
#: * ``would_converge`` —— 已结算、外面不是目标状态；``board`` 那一类会写它。
#: * ``converged`` —— 同上，且这一次真的写了。
#: * ``external_ahead`` —— 本地**还没结算**、外面已经是终态（``done`` /
#:   ``needs-human`` / ``superseded``）。**这一类 ``board`` 看不见**（它只碰已结算
#:   的件），所以它在这里被单独命名：它的处置是「去看那一轮到底跑成什么样了」，
#:   不是自动补一条结算 —— 本模块没有那个权力（不改业务结算）。
#: * ``no_target`` —— 本地还没结算，外面也还在做。正常在途，什么都不用做。
#: * ``missing`` —— 外面按 label 查不到。见 ``BoardReadKind.MISSING``。
#: * ``duplicate`` —— 外面查到不止一条。§8B 的证据，停下报告。
#: * ``unreadable`` —— 这一次读取本身没跑成。**与 ``missing`` 分开**。
DRIFT_KINDS = (
    "none",
    "would_converge",
    "converged",
    "external_ahead",
    "no_target",
    "missing",
    "duplicate",
    "unreadable",
)

#: 外面这一侧「这件事已经结束了」的状态词。**从 ``EXTERNAL_STATUSES`` 里挑的**，
#: 不是另抄一份 —— 挑出来的这三个是「外部那套不会再派它」的那一些（``ready`` /
#: ``todo`` 都还会）。
_TERMINAL_EXTERNAL_STATUSES = frozenset({"done", "needs-human", "superseded"})


@dataclass(frozen=True)
class ReconcileReport:
    """一次对账的**全部**产出。固定形状：六格恒在。

    ## ``writes`` 与 ``board`` 是两笔账，而它们必须对得上

    ``board`` 是逐件的``BoardSync``（含那些读到就已经对了的）；``writes`` 是
    **真的写下去的每一次写**，一条一行。dry-run 下 ``writes`` 恒为空 ——
    它是**回执**，不是计划。计划读 :attr:`planned_writes`。

    两者分开的理由与 ``BoardSync`` 把 ``synced`` / ``changed`` 分开是同一条：
    合成一格之后，「说要写」与「写了」在回执里长得一样，而它们之间恰好隔着
    一次可能失败的 ``transition``。
    """

    job_id: str
    scene_id: str
    dry_run: bool
    classes: tuple[str, ...]
    board: tuple[BoardSync, ...]
    leases: tuple[LeaseRow, ...]
    mappings: tuple[MappingRow, ...]

    @property
    def planned_writes(self) -> tuple[str, ...]:
        """判据说要写的每一次写。**两种模式下都算得出来**，而且是同一个判据算的
        （``WorkDispatch._converge`` 里那唯一一处分支），所以它不是一份预测。"""
        return tuple(
            f"{_write_line(sync)}" for sync in self.board if sync.changed
        )

    @property
    def writes(self) -> tuple[str, ...]:
        """这一次**真的写下去**的每一次写。dry-run 下恒为空。

        它由 ``dry_run`` 与 ``board`` 两样推出来，而不是另记一份：另记一份会造出
        第二个真相来源，而两者不一致时报告里读不出来哪一份是对的。
        """
        return () if self.dry_run else self.planned_writes

    @property
    def needs_operator(self) -> tuple[str, ...]:
        """需要人看一眼的那些行 —— 租约与漂移各算各的，理由都在行里。"""
        rows = [
            f"lease:{row.work_item_id}:{row.attempt_id}"
            for row in self.leases
            if row.disposition == "NEEDS_OPERATOR"
        ]
        rows += [
            f"mapping:{row.work_item_id}:{row.drift}"
            for row in self.mappings
            if row.drift in {"external_ahead", "missing", "duplicate", "unreadable"}
        ]
        return tuple(rows)

    def to_dict(self) -> dict[str, object]:
        """``--json`` 的那一份。**与上面几格逐字对应**，没有第九样东西。"""
        return {
            "job_id": self.job_id,
            "scene_id": self.scene_id,
            "dry_run": self.dry_run,
            "classes": list(self.classes),
            "board": [
                {
                    "task_id": sync.task_id,
                    "target_status": sync.target_status,
                    "observed_status": sync.observed_status,
                    "synced": sync.synced,
                    "changed": sync.changed,
                    "error": sync.error,
                }
                for sync in self.board
            ],
            "leases": [
                {
                    "work_item_id": row.work_item_id,
                    "subject_ref": row.subject_ref,
                    "attempt_id": row.attempt_id,
                    "ordinal": row.ordinal,
                    "holder": row.holder,
                    "leased_at": row.leased_at,
                    "age_seconds": row.age_seconds,
                    "external_status": row.external_status,
                    "external_error": row.external_error,
                    "disposition": row.disposition,
                    "reason": row.reason,
                }
                for row in self.leases
            ],
            "mappings": [
                {
                    "work_item_id": row.work_item_id,
                    "subject_ref": row.subject_ref,
                    "quay_task_id": row.quay_task_id,
                    "stop_reason": row.stop_reason,
                    "target_status": row.target_status,
                    "observed_status": row.observed_status,
                    "found": row.found,
                    "drift": row.drift,
                    "error": row.error,
                }
                for row in self.mappings
            ],
            "planned_writes": list(self.planned_writes),
            "writes": list(self.writes),
            "needs_operator": list(self.needs_operator),
        }


def _write_line(sync: BoardSync) -> str:
    """一次写在回执里长什么样。**四个身份都在**：读它的人手里可能没有本仓。"""
    return (
        f"work item {sync.task_id}："
        f"{sync.observed_status} → {sync.target_status}"
    )


def _lease_rows(
    jobs: JobStore,
    items: Sequence[WorkItem],
    observation: BoardObservation | None,
    *,
    now: datetime,
) -> tuple[LeaseRow, ...]:
    """逐件看有没有活租约。**从不写。**

    ``observation`` 是**传进来的**，不是这里再读一次外面：一件活租约那一行的外部
    状态与 ``MAPPING`` 那一段必须是**同一次读数**，否则一份报告里的两段可以说出
    两个不同的外面的样子 —— 而读者没有任何依据判断哪一段是新的。
    """
    observed_by_item: dict[str, BoardRead] = (
        {item.work_item_id: read for item, read in observation.rows}
        if observation is not None
        else {}
    )

    rows: list[LeaseRow] = []
    for item in items:
        for attempt in jobs.attempts(item.work_item_id):
            if attempt.state is not AttemptState.LEASED:
                continue
            read = observed_by_item.get(item.work_item_id)
            rows.append(_lease_row(item, attempt, read, now=now))
    return tuple(rows)


def _lease_row(
    item: WorkItem, attempt: JobAttempt, read: BoardRead | None, *, now: datetime
) -> LeaseRow:
    """一条活租约 → 一行。**这里的每一句话都是「我们不知道」的一种说法。**"""
    age = _age_seconds(attempt.leased_at, now)
    where = (
        "外部读数：本次没有接线执行器，所以这一格是空的"
        if read is None
        else f"外部那一行此刻读到 {read.observed_status!r}"
        if read.kind is BoardReadKind.OK
        else f"外部那一行没读成（{read.kind.value}）：{read.error}"
    )
    return LeaseRow(
        work_item_id=item.work_item_id,
        subject_ref=item.subject_ref,
        attempt_id=attempt.attempt_id,
        ordinal=attempt.ordinal,
        holder=attempt.holder,
        leased_at=attempt.leased_at,
        age_seconds=age,
        external_status=read.observed_status if read is not None else None,
        external_error=read.error if read is not None else None,
        disposition="NEEDS_OPERATOR",
        reason=(
            f"租约 {attempt.attempt_id} 由 {attempt.holder!r} 持有"
            f"{'，已 ' + _humanize(age) if age is not None else '，租出时刻读不出时间戳'}"
            f"。{where}\n"
            "**本入口不会收回它。** 收回一条租约在本仓需要一个显式动词并写明理由"
            "（``JobStore.release(reason=...)``），而「它是不是陈旧」这个问题本仓"
            "**没有机器可判的答案**：``ports/jobs.py`` 逐字写着没有 "
            "``lease_expires_at`` / ``reap``，因为猜错「那个 worker 死了」的代价是"
            "两个 worker 跑同一件昂贵的事（§8B duplicate expensive work）。\n"
            "所以这一行是 NEEDS_OPERATOR：要收回，得由人给出那条理由 —— 或者给这个"
            "入口一条**有判据的**到期规则，而那是一条独立的决定，不是这里顺手编的。"
        ),
    )


def _age_seconds(leased_at: str, now: datetime) -> float | None:
    """租约年龄。**读不出来返回 ``None``，不是 0** —— 见 ``LeaseRow`` 的 docstring。"""
    try:
        then = datetime.fromisoformat(leased_at)
    except ValueError:
        return None
    if then.tzinfo is None:
        # 没有时区的时刻**不能**与一个有时区的现在相减：Python 抛，而一个「先当
        # 成本地时间再算」的兜底会在跨时区部署上给出一个静默错掉的小时数。
        return None
    return (now - then).total_seconds()


def _humanize(seconds: float | None) -> str:
    if seconds is None:
        return "读不出来"
    if seconds < 120:
        return f"{seconds:.0f} 秒"
    if seconds < 7200:
        return f"{seconds / 60:.0f} 分钟"
    return f"{seconds / 3600:.1f} 小时"


def _mapping_rows(
    items: Sequence[WorkItem],
    jobs: JobStore,
    observation: BoardObservation,
    board_by_item: dict[str, BoardSync],
) -> tuple[MappingRow, ...]:
    """把一份只读读数翻译成逐件的漂移判断。**不问外面第二遍。**"""
    rows: list[MappingRow] = []
    for item, read in observation.rows:
        target = board_target(item)
        rows.append(
            MappingRow(
                work_item_id=item.work_item_id,
                subject_ref=item.subject_ref,
                quay_task_id=read.task_id,
                stop_reason=item.stop_reason.value if item.stop_reason else None,
                target_status=target,
                observed_status=read.observed_status,
                found=None if read.kind is BoardReadKind.UNREADABLE else read.kind is BoardReadKind.OK,
                drift=_drift(item, read, target, board_by_item.get(item.work_item_id)),
                error=read.error,
            )
        )
    return tuple(rows)


def _drift(
    item: WorkItem, read: BoardRead, target: str | None, sync: BoardSync | None
) -> str:
    """一件的漂移种类。**判据的顺序就是处置的优先级** —— 读不成的件没有资格谈
    「对不对」，所以那三种先返回。"""
    if read.kind is BoardReadKind.UNREADABLE:
        return "unreadable"
    if read.kind is BoardReadKind.MISSING:
        return "missing"
    if read.kind is BoardReadKind.DUPLICATE:
        return "duplicate"
    if target is None:
        # 本地没有结局。外面已经是终态 ⇒ 外面比本地靠前。
        return "external_ahead" if read.observed_status in _TERMINAL_EXTERNAL_STATUSES else "no_target"
    if read.observed_status == target:
        return "none"
    # 已经结算、外面不是目标状态。写没写由 ``board`` 那一段的结果定 —— 同一个
    # ``BoardSync`` 对象，所以这两段不可能各说各的。
    if sync is not None and sync.synced and sync.changed:
        return "converged"
    return "would_converge"


def reconcile_job(
    *,
    jobs: JobStore,
    dispatcher: Dispatcher | None,
    job_id: str,
    classes: Sequence[ReconcileClass] = (),
    dry_run: bool = True,
    now: datetime | None = None,
) -> ReconcileReport:
    """把一个作业上的历史投影对账干净。**默认 dry-run。**

    ## 默认是 dry-run，而这是一个刻意的方向

    因为它的调用者是运维，而它写的是**外部那张看板** —— 一次误 apply 的后果是
    外面那几行的状态被改到一个没人要求的取值上，而本仓的判据**看不见外面**
    （``EXTERNAL_STATUSES`` 只管写出去的值在不在闭集里，不管它该不该是那个值）。
    所以「先预演」是缺省，「真的写」要显式要。

    Args:
        jobs: 作业存储。
        dispatcher: 外部执行器。``None`` = 本部署没接线 —— 那时 ``board`` 与
            ``mapping`` 两类各自报一句「读不了外面」，而**不是**静默给一份空报告。
            空报告读起来像「一切都对」，而它实际说的是「我们什么都没看」。
        job_id: 要对账的作业。**必须是作业，不是一个范围字符串**：范围会让人以为
            这里能一把扫全库，而外部那张表里混着别人的工作（``ports/dispatch.py``
            逐字禁止无过滤的列举）。
        classes: 要跑的类别；空 = 全部（``ALL_CLASSES``）。顺序不影响结果 ——
            报告里的次序由 ``ALL_CLASSES`` 决定，不由调用方给的那一串决定。
        dry_run: ``True`` 时一次写都不产生。见 ``ReconcileReport.writes``。
        now: 算租约年龄用的当下时刻。**可注入**是为了让「已过 30 小时」这句话
            可测 —— 一个读系统时钟的实现会在测试里给出一个每天都在变的结果。

    Raises:
        JobStateError: 作业不存在。与 ``WorkDispatch`` 的两个入口一致 ——
            其余失败一律进报告。
    """
    job = jobs.get(job_id)
    if job is None:
        raise JobStateError(
            f"没有这个作业：{job_id!r}。对账的主语是一个已经受理的作业；"
            "作业由 ``JobStore.admit`` 产生。"
        )

    wanted = tuple(classes) if classes else ALL_CLASSES
    moment = now if now is not None else datetime.now(UTC)
    items = jobs.work_items(job_id)

    board: tuple[BoardSync, ...] = ()
    leases: tuple[LeaseRow, ...] = ()
    mappings: tuple[MappingRow, ...] = ()

    #: 执行器没接线时只读一次外面：``observe_board`` 与 ``converge_settled`` 都要
    #: 它，所以这里构造一次、传下去，而不是各自去问「接线了没有」。
    work = WorkDispatch(jobs=jobs, dispatcher=dispatcher) if dispatcher is not None else None

    observation: BoardObservation | None = None
    if work is not None and (
        ReconcileClass.MAPPING in wanted or ReconcileClass.LEASE in wanted
    ):
        # 一次读，两个类别共用 —— 见 ``_lease_rows`` 的 docstring。
        observation = work.observe_board(job_id)

    board_by_item: dict[str, BoardSync] = {}
    if ReconcileClass.BOARD in wanted:
        rows: list[BoardSync] = []
        for item in _settled_mapped(jobs, items):
            sync = (
                _unwired_sync(item)
                if work is None
                else work.reconcile(item.work_item_id, dry_run=dry_run)
            )
            rows.append(sync)
            board_by_item[item.work_item_id] = sync
        board = tuple(rows)

    if ReconcileClass.LEASE in wanted:
        leases = _lease_rows(jobs, items, observation, now=moment)

    if ReconcileClass.MAPPING in wanted:
        if observation is None:
            mappings = tuple(_unwired_mapping(jobs, item) for item in items if _mapped(jobs, item))
        else:
            mappings = _mapping_rows(items, jobs, observation, board_by_item)

    return ReconcileReport(
        job_id=job_id,
        scene_id=job.scene_id,
        dry_run=dry_run,
        classes=tuple(cls.value for cls in ALL_CLASSES if cls in wanted),
        board=board,
        leases=leases,
        mappings=mappings,
    )


def _mapped(jobs: JobStore, item: WorkItem) -> bool:
    return jobs.execution_ref(item.work_item_id) is not None


def _settled_mapped(jobs: JobStore, items: Sequence[WorkItem]) -> tuple[WorkItem, ...]:
    """要收敛的那些件 —— 「已结算**且**已映射」，与 ``converge_settled`` 同一条判据。

    两条都必须有，理由是两个不同的：没结算的**没有目标状态**（``board_target``
    返回 ``None``，给它下一个结论是抢在它前面写结局）；没映射的**外面从来没有过
    这件事**（没有看板可收敛）。

    次序就是 ``work_items`` 的 ``ordinal`` 次序 —— 报告里的行序因此是稳定的，而
    「哪一件漂了」这件事靠的是行里的 ``work_item_id``，不是靠位置：这个过滤恰好会
    跳过未结算的件，所以任何一个按下标去对的写法都是错的。
    """
    return tuple(
        item
        for item in items
        if board_target(item) is not None and jobs.execution_ref(item.work_item_id) is not None
    )


def _unwired_sync(item: WorkItem) -> BoardSync:
    """没接线执行器时 ``board`` 这一类的那一行。**目标状态照报。**

    与 ``_sync_board`` 在同一个处境下说的那句话同源：那不是一次「同步失败」，是
    **这个进程**没有可同步的对象。两件事的处置不同（一个去看外面，一个去看服务面
    是怎么构造的）。
    """
    return BoardSync(
        task_id=None,
        target_status=board_target(item),
        observed_status=None,
        synced=False,
        changed=False,
        error=(
            "外部执行器没有接线（这个进程没有把 Dispatcher 构造出来），"
            "所以这一次没有可同步的看板 —— 这不是「同步失败」。"
        ),
    )


def _unwired_mapping(jobs: JobStore, item: WorkItem) -> MappingRow:
    """同上，``mapping`` 那一类的版本。``drift`` 是 ``unreadable``：**我们不知道
    外面是什么样** —— 而这不是 ``missing``（那是「外面那一行没了」这个结论）。"""
    return MappingRow(
        work_item_id=item.work_item_id,
        subject_ref=item.subject_ref,
        quay_task_id=jobs.execution_ref(item.work_item_id).quay_task_id,  # type: ignore[union-attr]
        stop_reason=item.stop_reason.value if item.stop_reason else None,
        target_status=board_target(item),
        observed_status=None,
        found=None,
        drift="unreadable",
        error="外部执行器没有接线，所以外面一行都没读。",
    )

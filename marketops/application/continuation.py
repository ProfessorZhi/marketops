"""§10 的控制器：把盘上**欠着的下一步**一件件做掉，并如实说出此刻为什么没有更多工作在跑。

## 它存在的那一条

``docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md`` 第 22 行对 R2 的一行定义是**「关掉聊天仍续
下一批，到期只重查需要的部分」**。这两半各自需要一个东西：第一半需要「下一步」在
客户端断开之后仍然**在盘上**（那是 ``adapters/sqlite/jobs.py`` 在受理/结算的同一个写
事务里欠下的那一行），第二半需要一个**没有人在看着也会跑**的执行器。本模块是后者。

## 一轮 tick 的六步，以及每一步的「不做」

1. **收回**（:meth:`ContinuationController._reclaim`）—— 上一代留下的 ``CLAIMED``
   放回队列。不做：不重跑、不猜它做到哪儿了（执行器必须是幂等的，见
   :class:`ContinuationExecutor`）。
2. **读闸门** —— 本工作区的场景，以及各自放不放行派发。不做：不替暂停的场景发明
   理由，那句话逐字来自 ``SceneRecord.dispatch_refusal``。
3. **选** —— ``PENDING``/``BLOCKED`` 且**已到期**、且闸门开着的行，按存储层给的
   ``due_at`` 升序。不做：不自己发明工作。能被执行的只有盘上已有的行 —— 这一条是
   §B21「无任务无模型调用」在**结构上**成立的原因，不是靠某个 if。
4. **领** —— 每件一次 CAS（``claim_continuation``）。不做：不比对时刻来判断「是不是
   我拿到了」，那在两个驱动传同一个 ``now`` 时会给出两个真。
5. **跑** —— 交给注入的 :class:`ContinuationExecutor`。不做：不重写派发与维护，
   它们**已经存在**（``application/dispatch.py``、``application/maintenance.py``），
   这一层只负责「什么时候叫它们、叫完记什么」。
6. **结** —— ``finish_continuation``。落点由存储层按 ``attempts`` 定
   （``domain/continuation.py::after_failure``），所以**执行器报 FAILED，盘上不一定
   是 FAILED**。

## 为什么「为什么没事做」读的是这一轮**之后**的盘

:class:`~marketops.domain.continuation.ControllerInputs` 的六个数是未决行的**恰好**
划分：:func:`_inputs` 里每一行只走一条 ``continue``，所以「这一件算不算做掉了」
这个判断只有一个产地（盘上那一行）。从这一轮的账上推那六个数就要有第二份判断，
而它与盘漂移的形态是「账上说做完了、盘上还欠着」—— 两个都自称是报告。读一遍
**之后**的盘要多花一次查询，换来的是那六个数**不可能**与盘不一致：它们就是从盘上
数出来的。

## 它一次都不碰外部

本模块 import 的东西只有端口与领域。真正的派发（``Dispatcher``）、维护复核
（``build_refresher``）、执行器的存在与否，全部由**组合根**注入。所以「这一轮有没有
花钱」这个问题在**接线**上回答，不在这一层 —— 一个没有装执行器的部署每一步都会得到
``BLOCKED(CAPACITY)``，而不是静默地什么都不做。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime

from ..domain.continuation import (
    OPEN_STATES,
    BlockedOn,
    Continuation,
    ContinuationKind,
    ContinuationState,
    ControllerInputs,
    ControllerState,
    WakeupOwner,
    classify,
)
from ..domain.errors import ConfigError, ContinuationError
from ..domain.scene import SceneRecord
from ..ports.jobs import JobStore
from ..ports.scenes import SceneStore
from .caps import TruncationDecision, apply_cap
from .maintenance import skip_reason

#: 一轮里可以**领**的状态：还没人做、或者被什么挡着（挡着的那件事要**走一遍**才答
#: 得出来，见 ``ports/jobs.py::claim_continuation``）。
_CLAIMABLE: frozenset[ContinuationState] = frozenset(
    {ContinuationState.PENDING, ContinuationState.BLOCKED}
)

#: 执行器**可以**报的那几个状态。``PENDING`` 不在里面 —— 它是 ``after_failure`` 的
#: 落点，不是一个执行器能做的决定；``CLAIMED`` 也不在 —— 见 :class:`ContinuationOutcome`。
_REPORTABLE: frozenset[ContinuationState] = frozenset(
    {ContinuationState.DONE, ContinuationState.BLOCKED, ContinuationState.FAILED}
)


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError(
            "now 必须带时区 —— 见 domain/util.to_utc 的说明。"
            "到期判定正是拿它减 due_at，naive 时间会让「到期了没有」有一个看起来"
            "有答案的空答案。"
        )
    return now.astimezone(UTC)


@dataclass(frozen=True)
class ContinuationOutcome:
    """一次执行的结果 —— **执行器报的**，不是盘上记的。

    ## 只有三个状态可以报，这不是风格

    ``DONE`` / ``BLOCKED`` / ``FAILED`` 三选一。另外两个成员各有各的理由不在这里：

    * ``PENDING`` —— 那是 ``after_failure`` 按 ``attempts`` 决定的**落点**。让执行器
      自己报 ``PENDING``，等于让它绕过「异常恢复有次数界限」那条判据：一个每次都说
      「下次再说」的执行器会永远重排下去，而账上每一次都写得像正常。
    * ``CLAIMED`` —— 端口上留着那条边（长跑的执行器要能报进度而不必先离开再回来），
      但今天**没有任何执行器**会报它：两个种类都是一趟做完的短活。一条没有产出的分支
      在这里就是一条没人验过的判据 —— 等真有长跑执行器的那一天，它会在这里拿到一句
      要读的拒绝，而不是一个静默接受的空转。

    ## 三个不变量在**构造时**就成立

    ``BLOCKED`` ⟺ ``blocked_on`` 非空、``BLOCKED``/``FAILED`` 必须带 ``reason``。
    与 :class:`~marketops.domain.continuation.Continuation` 的模型校验逐条同形 ——
    这里再写一遍是因为它给出的话更早也更好读：存储层抛的是「一次写事务里的那一行
    不合法」，而真正的问题在**执行器**身上。

    ``checkpoint`` 是给**下一次**执行器看的不透明字符串：恢复时原样交回。今天两个
    执行器都不需要它（它们本来就是幂等的），所以它是空的；它在这里而不是在
    ``Continuation`` 上，是因为「这一次走到哪儿了」是执行器的话，不是存储的话。
    """

    state: ContinuationState = ContinuationState.DONE
    reason: str = ""
    checkpoint: str = ""
    blocked_on: BlockedOn | None = None

    def __post_init__(self) -> None:
        if self.state not in _REPORTABLE:
            raise ConfigError(
                f"执行器报了 {self.state.value}，而它能报的只有 "
                f"{sorted(s.value for s in _REPORTABLE)}。\n"
                "PENDING 是失败之后按次数算出来的落点（after_failure），"
                "CLAIMED 是原地写进度 —— 两个都不是「这一次做完了什么」的答案。"
            )
        blocked = self.state is ContinuationState.BLOCKED
        if blocked != (self.blocked_on is not None):
            raise ConfigError(
                f"state={self.state.value} 与 blocked_on={self.blocked_on} 对不上："
                "被挡住时必须说明是哪一类理由（暂停 / 缺容量 / 等人），"
                "而没被挡住时不得留下一个理由 —— 分类读的正是这一格。"
            )
        if blocked or self.state is ContinuationState.FAILED:
            if not self.reason.strip():
                raise ConfigError(
                    f"执行器报了 {self.state.value} 却没有写理由。\n"
                    "报告里这一格就是那句话：没有它，「这一件为什么没做成」只能读成"
                    "「没做成」—— 而需要人看一眼与要运维补接线，处置完全不同。"
                )

    @classmethod
    def done(cls, reason: str = "", *, checkpoint: str = "") -> ContinuationOutcome:
        """做完了。``reason`` 是**这一趟做了什么**，可以留空。"""
        return cls(state=ContinuationState.DONE, reason=reason, checkpoint=checkpoint)

    @classmethod
    def blocked(
        cls, *, reason: str, on: BlockedOn, checkpoint: str = ""
    ) -> ContinuationOutcome:
        """门关着。**这不是一次尝试**，``attempts`` 不动 —— 一个暂停了一周的场景不该
        因为每 60 秒 tick 一次而被判成重试了一万次。"""
        return cls(
            state=ContinuationState.BLOCKED,
            reason=reason,
            checkpoint=checkpoint,
            blocked_on=on,
        )

    @classmethod
    def failed(cls, reason: str, *, checkpoint: str = "") -> ContinuationOutcome:
        """**这一次**失败了。落到盘上是什么由 ``attempts`` 决定：没过上限就退回
        ``PENDING`` 并排下一次，过了才是终态 ``FAILED``。"""
        return cls(state=ContinuationState.FAILED, reason=reason, checkpoint=checkpoint)


class ContinuationExecutor(ABC):
    """**一个种类一个动词** —— 把一条续跑意图真的做掉。

    ## 它必须幂等，这是「崩溃后恢复」的全部前提

    一条 ``CLAIMED`` 的行在控制器换代之后会被**重新执行**（见
    :meth:`ContinuationController._reclaim`）。所以同一个 ``business_key`` 被执行两次
    必须是安全的：``dispatch_batch`` 靠本地映射（``ALREADY_MAPPED`` 原样返回，
    不重派），``maintenance_tick`` 靠维护账的 CAS。**没有这一条，
    「不重复昂贵工作」就只是一个说法。**

    ## 它必须对自己诚实

    做不完就报 ``BLOCKED``，试过、挂了就报 ``FAILED``。把「我不确定」报成 ``DONE``
    会让这一行从盘上消失，而下一批**永远不会**被触发 —— 账上什么都看不出来。

    ## 两个动词分开，而不是一个 ``execute(kind=...)``

    两个种类要的输入不一样（一个是作业，一个是主体），合起来会得到一个「按 kind 变
    形状」的参数表，而调用方看不出自己该填哪几个。分开之后，种类的分派在控制器里
    只有一处，且那处是**穷尽**的（见 :meth:`ContinuationController._execute`）。
    """

    @abstractmethod
    def dispatch_batch(
        self, *, scene_id: str, job_id: str, checkpoint: str, now: datetime
    ) -> ContinuationOutcome:
        """把一个作业**当下该派的活**交出去。

        ``job_id`` 就是那条意图的 ``business_key``。执行器要自己做的是
        ``application/dispatch.py::WorkDispatch`` 的那两趟（派发轮次 + 已结算件的
        看板收敛）；没有装执行器的部署在这里报 ``BLOCKED(CAPACITY)``，
        **不报 DONE**：那批工作确实还没交出去。
        """
        raise NotImplementedError

    @abstractmethod
    def maintenance_tick(
        self,
        *,
        scene_id: str,
        subject_kind: str,
        subject_ref: str,
        checkpoint: str,
        now: datetime,
    ) -> ContinuationOutcome:
        """把**这一个主体**到期的格子重看一眼。

        §B21 逐字要求的「仅相关对象等人」在这里落地：一条被挡住的主体只带它自己的
        ``subject_kind``/``subject_ref``，所以它旁边那些主体的意图照常跑。两格都空
        是一个合法输入（``settle`` 那一次说不出主体是谁），那时执行器 tick 的是
        整个场景 —— 那不是「所有人一起等」，是「这一件没有主体可等」。
        """
        raise NotImplementedError


@dataclass(frozen=True)
class ContinuationStatus:
    """此刻为什么没有更多工作在跑 —— 一次**只读**的判定。

    ``state`` 与 ``inputs`` 一起给：判词是 :func:`~marketops.domain.continuation.classify`
    算出来的，而它读的六个数也一并留着 —— 一个只报判词的状态页在「为什么是
    ``WAITING_DUE``」这个问题上答不出任何东西，而那个问题正是有人来看这一页的原因。
    """

    at: datetime
    workspace: str
    state: ControllerState
    inputs: ControllerInputs

    def as_dict(self) -> dict[str, object]:
        """这一页的 JSON 形状。**一处写，两个调用方读**。

        两个调用方是控制器（``--once`` 跑完把这一页写进 stderr）与服务面的
        ``get_continuation_status``。形状放在这里而不是放在其中一边，是因为
        ``adapters/`` 不能 import ``interfaces/``（分层方向），而两处各搭一遍的
        漂移形态是「工具说 ``WAITING_DUE``、日志说 ``PAUSED``」—— 同一个部署，
        两句互相矛盾的话，而先被读到的那句决定排查方向。

        六个数**不合并**成一个「共 5 件待处理」：它们是六个不同的桶，各自的处置
        不同（等人 / 等接线 / 等到期 / 在跑），合成一个之后状态页在「等谁」这个
        问题上再也答不出来。
        """
        inputs = self.inputs
        return {
            "at": self.at.isoformat(),
            "workspace": self.workspace,
            "state": self.state.value,
            "inputs": {
                "ever_done": inputs.ever_done,
                "in_flight": inputs.in_flight,
                "due_now": inputs.due_now,
                "waiting_due": inputs.waiting_due,
                "blocked_paused": inputs.blocked_paused,
                "blocked_capacity": inputs.blocked_capacity,
                "blocked_human": inputs.blocked_human,
                "open_total": inputs.open_total,
            },
        }


@dataclass(frozen=True)
class ContinuationRun:
    """一轮 tick 的完整账。**七段分开，因为七句话的处置不同。**

    * ``done`` —— 做完了。什么都不用做。
    * ``failed`` —— 真的试过、次数用尽，**要人看一眼**。
    * ``retrying`` —— 挂了一次，已经排上下一次。**不用管**（与上一段分开的理由：
      它们合成的「没做成」会让「再等 60 秒」和「现在就得看」长得一样）。
    * ``blocked`` —— 门关着。要人去按一个按钮，或者运维去补接线。
    * ``superseded`` —— 结账时这一行已经归了新一代。**要立刻停手的那一类**
      （继续做下去就是两个触发器在推同一批资源），理由与 :class:`WakeupOwner` 同一段。
    * ``not_claimed`` —— 没拿到：别人在做，或者已经做完了。**这不是失败。**
    * ``reclaimed`` —— 上一代留下的在途件被收回队列。它自己不是产出，是一个**事实的
      记录**：这一次唤醒接手时盘上还有人在做。

    ``not_ticking`` 与 ``truncation`` 是两笔**没有发生**的账：被闸门挡住的场景，
    以及被上限砍掉的尾巴。后者尤其不能省 —— 「没找到」与「找到了但被上限砍了」
    在报告里长得一模一样（``application/caps.py`` 的模块 docstring）。
    """

    at: datetime
    workspace: str
    holder: str
    epoch: int
    state: ControllerState
    inputs: ControllerInputs
    done: tuple[Continuation, ...] = ()
    failed: tuple[Continuation, ...] = ()
    retrying: tuple[Continuation, ...] = ()
    blocked: tuple[Continuation, ...] = ()
    #: ``(那一行, 为什么)`` —— 形状与 ``not_ticking`` 一致：两格都要能读。
    superseded: tuple[tuple[Continuation, str], ...] = ()
    not_claimed: tuple[Continuation, ...] = ()
    reclaimed: tuple[Continuation, ...] = ()
    not_ticking: tuple[tuple[str, str], ...] = ()
    truncation: TruncationDecision | None = None

    @property
    def status(self) -> ContinuationStatus:
        """这一轮**之后**的判定。理由见模块 docstring。"""
        return ContinuationStatus(
            at=self.at, workspace=self.workspace, state=self.state, inputs=self.inputs
        )

    @property
    def touched(self) -> int:
        """这一轮**真的动过**的行数：做完的、试过挂了的、重排的、被挡住的、被顶掉的。

        ``not_claimed`` 不算（那不是我做的），``reclaimed`` 也不算（那只是把它放回
        队列 —— 它是不是这一轮做掉了，由后面那几段说）。这个数是「这一轮做了几件事」，
        而它**不是**「这个市场一共有几件事」—— 后者问
        ``JobStore.continuation_counts``。两个数字混用，会让一次空转看起来像一次推进。
        """
        return (
            len(self.done)
            + len(self.failed)
            + len(self.retrying)
            + len(self.blocked)
            + len(self.superseded)
        )

    @property
    def ok(self) -> bool:
        """这一轮有没有**要人看**的东西。**空的一轮也是成功的** —— 没有到期的活不是故障。"""
        return not self.failed and not self.superseded


def _inputs(
    rows: tuple[Continuation, ...],
    *,
    gate: dict[str, SceneRecord],
    now: datetime,
    ever_done: bool,
) -> ControllerInputs:
    """把未决的行**逐个**归到六个数里的一格。**每一行恰好进一格。**

    归法就是 ``classify`` 那张优先级表的输入侧：在途的算在途；被挡住的按
    ``blocked_on`` 分三格；还没做的，先看**闸门**（场景暂停是更强的理由 —— 这一行
    没在跑不是因为没到期），再看到期没有。

    先看闸门而不是先看到期，是为了让 ``PAUSED`` 与 ``WAITING_DUE`` 分开：一个暂停的
    场景里那些还没到期的行，若算进 ``waiting_due``，报告会显示「等着呢」，
    而实际发生的事是「决定不让它跑」。
    """
    counted = {
        "in_flight": 0,
        "due_now": 0,
        "waiting_due": 0,
        "blocked_paused": 0,
        "blocked_capacity": 0,
        "blocked_human": 0,
    }
    by_reason = {
        BlockedOn.PAUSE: "blocked_paused",
        BlockedOn.CAPACITY: "blocked_capacity",
        BlockedOn.HUMAN: "blocked_human",
    }
    for row in rows:
        if row.state is ContinuationState.CLAIMED:
            counted["in_flight"] += 1
            continue
        if row.state is ContinuationState.BLOCKED:
            counted[by_reason[row.blocked_on]] += 1
            continue
        record = gate[row.scene_id]
        if not record.accepts_new_work:
            counted["blocked_paused"] += 1
        elif row.is_due(now):
            counted["due_now"] += 1
        else:
            counted["waiting_due"] += 1
    return ControllerInputs(ever_done=ever_done, **counted)


def _read_status(
    *, jobs: JobStore, scenes: SceneStore, workspace: str, now: datetime
) -> ContinuationStatus:
    """**只读**地算一遍此刻的判定。``survey`` 与 ``tick`` 共用它 —— 一处写。"""
    at = _utc(now)
    gate = _gate(scenes, workspace)
    counts = jobs.continuation_counts(scene_ids=tuple(gate))
    rows = jobs.continuations(scene_ids=tuple(gate), states=OPEN_STATES)
    inputs = _inputs(
        rows, gate=gate, now=at, ever_done=counts[ContinuationState.DONE] > 0
    )
    return ContinuationStatus(
        at=at, workspace=workspace, state=classify(inputs), inputs=inputs
    )


def _gate(scenes: SceneStore, workspace: str) -> dict[str, SceneRecord]:
    """本工作区每个场景的**派发闸门**。这个字典的键集就是这一轮要看的全部场景。

    返回字典而不是元组，是因为两个调用点都要按 ``scene_id`` 找那一格（选行时要问
    「它放不放行」）。顺带把顺序钉住了：``records`` 按 ``scene_id`` 升序返回，
    而 ``tuple(gate)`` 保留那个顺序 —— 两次 tick 传给存储层的场景集合因此逐字相同。
    """
    return {record.scene_id: record for record in scenes.records(workspace=workspace)}


def survey(
    *, jobs: JobStore, scenes: SceneStore, workspace: str, now: datetime
) -> ContinuationStatus:
    """**只读**地答「此刻为什么没有更多工作在跑」。

    它与 :meth:`ContinuationController.tick` 共用同一份判定（:func:`_read_status`），
    但它**没有执行器**这个参数 —— 所以「这一页会不会顺手推一轮」这个问题在签名上就
    答完了。控制面的那一条只读工具走的是这里。
    """
    return _read_status(jobs=jobs, scenes=scenes, workspace=workspace, now=now)


class ContinuationController:
    """一个**持有唤醒归属的**控制器。归属由构造参数带进来，不由本类去争。

    ## 为什么归属是构造参数

    因为它是一次竞争的结果（``WakeupRegistry.acquire``），而那次竞争必须在**这个
    对象存在之前**就有答案：一个自己会去 ``acquire`` 的控制器，在拿不到归属时也有
    一个「那我先跑一轮看看」的位置，而那个位置正是 §10 「不能并行双触发器」要消灭的
    东西。拿到归属是调用方的事，本类只认一个事实：``owner`` 说我是谁、是第几代。

    ## 它不心跳

    心跳（``WakeupRegistry.heartbeat``）与「这一轮做了什么」是两件事，且它们的
    节奏不同：一轮可能跑很久，而心跳要按时跳。合成一件的代价是**卡住的那一轮**同时
    卡住了心跳，于是别的控制器会在 90 秒后把它顶掉 —— 而它其实活得好好的。
    所以常驻循环在两轮之间跳，本类不碰。
    """

    def __init__(
        self,
        *,
        jobs: JobStore,
        scenes: SceneStore,
        executor: ContinuationExecutor,
        owner: WakeupOwner,
    ) -> None:
        self._jobs = jobs
        self._scenes = scenes
        self._executor = executor
        self._holder = owner.holder
        self._epoch = owner.epoch
        self._workspace = owner.workspace
        #: 这一代**接手的那一刻**。一条 ``CLAIMED`` 的行若比它还早，它就是上一代
        #: 留下的 —— 见 :meth:`_reclaim`。
        self._owned_since = owner.acquired_at

    # ------------------------------------------------------------------
    def survey(self, now: datetime) -> ContinuationStatus:
        """这一代眼里的只看状态。与模块级的 :func:`survey` 同一份判定。"""
        return _read_status(
            jobs=self._jobs, scenes=self._scenes, workspace=self._workspace, now=now
        )

    def tick(self, now: datetime, *, limit: int | None = None) -> ContinuationRun:
        """跑一轮：收回、选、领、跑、结，然后**从盘上**读一遍判定。

        Args:
            now: 服务端时刻。本层没有时钟。
            limit: 这一轮最多做几件。**作用在整轮上，不是每个场景**：上限要挡的是
                「这一轮花多少」，而一个按场景算的上限会让 K 个场景各做 ``limit``
                件 —— 那正是它想挡的东西。``None`` = 不封顶。

        Returns:
            :class:`ContinuationRun`。**它不是异常驱动的** —— 一件失败、一件被顶掉，
            返回值仍然返回，两者各在 ``failed`` / ``superseded`` 里。
        """
        at = _utc(now)
        gate = _gate(self._scenes, self._workspace)
        scene_ids = tuple(gate)
        reclaimed = self._reclaim(scene_ids, at)

        rows = self._jobs.continuations(scene_ids=scene_ids, states=_CLAIMABLE)
        runnable = [
            row for row in rows if gate[row.scene_id].accepts_new_work and row.is_due(at)
        ]
        if limit is None:
            picked: tuple[Continuation, ...] = tuple(runnable)
            truncation = None
        else:
            kept, truncation = apply_cap(runnable, limit, key=_row_name)
            picked = tuple(kept)

        done: list[Continuation] = []
        failed: list[Continuation] = []
        retrying: list[Continuation] = []
        blocked: list[Continuation] = []
        superseded: list[tuple[Continuation, str]] = []
        not_claimed: list[Continuation] = []

        for row in picked:
            claim = self._jobs.claim_continuation(
                row.continuation_id, holder=self._holder, epoch=self._epoch, now=at
            )
            if not claim.won:
                not_claimed.append(claim.continuation)
                continue
            outcome = self._execute(claim.continuation, at)
            try:
                landed = self._jobs.finish_continuation(
                    row.continuation_id,
                    state=outcome.state,
                    holder=self._holder,
                    epoch=self._epoch,
                    now=at,
                    checkpoint=outcome.checkpoint,
                    reason=outcome.reason,
                    blocked_on=outcome.blocked_on,
                )
            except ContinuationError as error:
                # 结账时它已经不是我们的了：另一代在我做这一件的时候接手了。
                # **不重试、不掩盖** —— 继续做下去就是两个触发器在推同一批资源，
                # 而那正是这一格存在的理由（``WakeupOwner`` 的 docstring）。
                # 剩下的几件照做：一次被顶掉不该让整轮停在这里。
                superseded.append((claim.continuation, str(error)))
                continue
            _segment(landed, done=done, failed=failed, retrying=retrying, blocked=blocked)

        status = _read_status(
            jobs=self._jobs, scenes=self._scenes, workspace=self._workspace, now=at
        )
        return ContinuationRun(
            at=at,
            workspace=self._workspace,
            holder=self._holder,
            epoch=self._epoch,
            state=status.state,
            inputs=status.inputs,
            done=tuple(done),
            failed=tuple(failed),
            retrying=tuple(retrying),
            blocked=tuple(blocked),
            superseded=tuple(superseded),
            not_claimed=tuple(not_claimed),
            reclaimed=reclaimed,
            not_ticking=tuple(
                (scene_id, skip_reason(record))
                for scene_id, record in gate.items()
                if not record.accepts_new_work
            ),
            truncation=truncation,
        )

    # ------------------------------------------------------------------
    def _reclaim(self, scene_ids: tuple[str, ...], at: datetime) -> tuple[Continuation, ...]:
        """把**上一代**留下的在途件放回队列，返回它们被放回时的样子。

        ## 「上一代」怎么认出来：三格任意一格不对就不是我

        * ``holder`` 不是我的 —— 另一个部署/另一个 owner id；
        * ``epoch`` 不是我的 —— 同名的上一代（见 ``Continuation.epoch``）；
        * ``updated_at`` 早于**我接手的时刻** —— 同名的上一代在**干净让出之后**
          换了回来。这一格不是多余的：``WakeupRegistry.release`` 是删行，于是下一代
          ``acquire`` 拿到的 ``epoch`` 又是 1，前两格与上一代**逐字相同**（同一个部署、
          同一个 owner id）。只有时刻分得开它们。

        少了第三格的那个失败是**静默的**：那一行永远挂在 ``CLAIMED`` 上，分类把它
        读成「正在研究」，而实际上没有任何东西在动 —— 一盏恒绿的灯。

        ## 收回**不是**重跑

        它只是把那一行放回 ``PENDING``。放在盘上的是什么由执行器保证（幂等，见
        :class:`ContinuationExecutor`）—— 本方法不猜上一代做到哪儿了，也不试着
        从中途接上：没有 checkpoint 的通用续跑是一个幻觉。

        ``due_at`` 落在 ``now + retry_delay(attempts)``（存储层的规则，与一次失败
        之后的重排同一条），所以收回的行**不在这一轮**被跑掉。这是刻意的：一次崩溃
        之后把所有在途件立刻重跑一遍，是在用「刚重启」这件事制造一次尖峰。
        """
        reclaimed: list[Continuation] = []
        for row in self._jobs.continuations(
            scene_ids=scene_ids, states=(ContinuationState.CLAIMED,)
        ):
            if (
                row.holder == self._holder
                and row.epoch == self._epoch
                and row.updated_at >= self._owned_since
            ):
                continue
            reclaimed.append(
                self._jobs.finish_continuation(
                    row.continuation_id,
                    state=ContinuationState.PENDING,
                    holder=row.holder,
                    epoch=row.epoch,
                    now=at,
                    reason=(
                        f"上一代（holder={row.holder!r} epoch={row.epoch}）在 "
                        f"{row.updated_at.isoformat()} 之后没有再动过这一件，"
                        f"这一代（epoch={self._epoch}）把它放回队列。"
                    ),
                )
            )
        return tuple(reclaimed)

    def _execute(self, row: Continuation, at: datetime) -> ContinuationOutcome:
        """按种类把这一行交给对应的那个动词。**分派只有这一处，且它是穷尽的。**

        执行器抛出的**一切**都被记成 ``FAILED``（带异常原文），与
        ``application/maintenance.py::run_once`` 逐字同一条理由：执行器会去碰网络、
        碰模型，一个没想到的异常类型让整轮在中间断掉的代价，是前面那些已经做完的
        工作在这一轮的账上消失。

        异常文本带上类型名：``KeyError('x')`` 与 ``ValueError('x')`` 是完全不同的
        两件事，而 ``str(error)`` 把它们读成同一句话。
        """
        try:
            if row.kind is ContinuationKind.DISPATCH_BATCH:
                return self._executor.dispatch_batch(
                    scene_id=row.scene_id,
                    job_id=row.business_key,
                    checkpoint=row.checkpoint,
                    now=at,
                )
            if row.kind is ContinuationKind.MAINTENANCE_TICK:
                return self._executor.maintenance_tick(
                    scene_id=row.scene_id,
                    subject_kind=row.subject_kind,
                    subject_ref=row.subject_ref,
                    checkpoint=row.checkpoint,
                    now=at,
                )
        except Exception as error:  # noqa: BLE001 —— 见 docstring
            return ContinuationOutcome.failed(f"{type(error).__name__}: {error}")
        # 种类是闭集，两个成员都在上面 —— 走到这里说明
        # ``domain/continuation.py`` 加了第三个而这一处没跟上。静默跳过它，会让那一条
        # 意图**在账上不存在**（它已经被领走了，落点却没有），而盘上明明欠着。
        raise ConfigError(
            f"不认识的续跑种类 {row.kind!r}（这一行是 {row.continuation_id}）。\n"
            "领域层新增了种类，而控制器没有跟上 —— 这里不跳过它："
            "少一条落点的行在报告里读起来与「这一件没被领走」完全一样。"
        )


def _row_name(row: Continuation) -> str:
    """一条意图在 ``dropped_items`` 里的名字。**人要能读**，所以不是那个哈希 id。"""
    return f"{row.scene_id}/{row.kind.value}/{row.business_key}"


def _segment(
    landed: Continuation,
    *,
    done: list[Continuation],
    failed: list[Continuation],
    retrying: list[Continuation],
    blocked: list[Continuation],
) -> None:
    """把结完账的那一行放进它该去的那一段。**一处写**。

    落点由存储层按 ``attempts`` 定，所以这里读的是**盘上那一行**的 ``state``，不是
    执行器报的那个 —— 报 ``FAILED`` 而落在 ``PENDING`` 是一件正常的事（还有下一次），
    而它必须进 ``retrying`` 而不是 ``failed``：那段是给要人看的东西留的。
    """
    if landed.state is ContinuationState.DONE:
        done.append(landed)
    elif landed.state is ContinuationState.FAILED:
        failed.append(landed)
    elif landed.state is ContinuationState.PENDING:
        retrying.append(landed)
    else:
        blocked.append(landed)


__all__ = [
    "ContinuationController",
    "ContinuationExecutor",
    "ContinuationOutcome",
    "ContinuationRun",
    "ContinuationStatus",
    "survey",
]

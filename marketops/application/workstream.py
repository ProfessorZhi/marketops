"""**B01 的启动面**：把「绑一版已批准的 Scene、建一个工作流、让它第一轮 Run 走既有的
准入路径」串成一次**幂等**的动作。

## 幂等的两半，以及它们各自落在哪一层

任务书 §5 承重点 1 逐字：「判据的载体是 ``launch_record`` 那条 UNIQUE + 一条读回
路径，两半都要测」。两半在这份代码里的位置是：

* **UNIQUE** 在 ``adapters/sqlite/workstreams.py::put_launch`` 的那条
  ``ON CONFLICT (workspace, scene_id, scene_version, idempotency_key) DO NOTHING``
  上 —— DDL 那一层（本片不改 DDL）。
* **读回**在这一层的第 1 步（:func:`start_workstream` 开头那次
  :meth:`~marketops.ports.workstreams.WorkstreamStore.get_launch`）**和**
  ``put_launch`` 内部那一次读回。

两处读回而不是一处，因为两处挡的是两件事：开头那一处挡的是**重发**
（「同一次启动」——连第一轮 Run 都不该再开一遍），``put_launch`` 里那一处挡的是
**两个写者同时交同一个键**（那时两边都已经走过开头那一处，谁赢由存储那一层判）。
少了开头那一处，重发会去跑一遍 ``admit``；少了里面那一处，并发下会落两条记录。
两处都删掉的话，重发会**重建一个工作流**（新的 ordinal ⇒ 新的 workstream_id ⇒
revision 从 1 重来）—— 那正是承重点 1 点名的「不重建」，也是那条变异要打中的东西。

## 第一轮 Run 走的是**既有的**准入路径，而这一层碰不到它

:func:`start_workstream` 收一个 ``admit`` 可调用对象，而不是自己拼 ``RunSpec``、
自己判模式、自己写 Run。那三件事今天住在
``adapters/mcp_server/runs.py``（``_preamble`` / ``_Round.round_of`` /
``application/run_admission.py::admit_run``），**只有一处**。在这里再写一遍会让
「一次提交按什么次序被受理」有两份实现，而两份实现的分岔表现是「同一次提交经两条
路径得到两个不同的结论」—— 报告上两个结论都自洽。

注入而不是 import：``application/`` 不许 import ``adapters/``（本仓的分层纪律，
见 ``application/__init__.py`` 的「这一层与 domain、adapters 的边界」）。所以
**接线在 handler 那一层完成**，而这一层只声明它要什么形状
（:class:`FirstRun`）。

## 「第一轮没被受理」是一个**要说得出来的结果**

照 PSM §4.2 第 5 条：「返回 launch/Scene/job id、**实际排队状态**、原因及何时会
再次醒来；不能只把一个表字段改 active」。所以 ``admit`` 回的是一个
:class:`FirstRun`，它的 ``run_id`` 可以为空 —— 那时 ``why`` 必须写下来，
``queue`` 是 :attr:`~marketops.domain.workstream.WorkstreamQueue.IDLE`，而
``next_wake_at`` 说清「什么时候会再醒」。三格一起才是那句话，少一格都会让
「排队中」与「受理失败」在回执上同形。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from ..domain.errors import ConfigError, EvidenceError, StoreError
from ..domain.scene import SceneRecord
from ..domain.util import SEP, sha256_text, to_utc
from ..domain.workstream import (
    WORKSTREAM_TRANSITIONS,
    LaunchRecord,
    Workstream,
    WorkstreamAction,
    WorkstreamQueue,
    WorkstreamState,
    WorkstreamStateError,
    launch_id_of,
    launch_subject,
    workstream_id,
)
from ..ports.scenes import SceneStore
from ..ports.workstreams import WorkstreamStore

__all__ = [
    "NOTHING_IN_THIS_WORKSPACE",
    "FirstRun",
    "StartResult",
    "WorkstreamListing",
    "WorkstreamReceipt",
    "control_workstream",
    "first_run_key",
    "get_workstream",
    "list_workstreams",
    "request_digest_of",
    "start_workstream",
]


#: 第一轮 Run 的幂等键的**后缀**。见 :func:`first_run_key`。
_FIRST_RUN_SUFFIX = "#first-run"


def first_run_key(
    *, workspace: str, scene_id: str, scene_version: int, idempotency_key: str
) -> str:
    """第一轮 Run 的幂等键 —— **由启动的业务键推出来**，不是调用方给的第二个键。

    这一格是整份设计里最容易被写错的地方，所以把理由写全：

    ``RunSpec.idempotency_key`` 是一个**内容寻址**的量（``domain/run.py::run_id``
    逐字是 ``run_id(workspace, idempotency_key)``）。让启动的调用方**另给**一个键，
    等于允许「同一个启动业务键配两个不同的 Run 键」：那时重发同一份启动会开出
    **第二轮 Run**，而两条记录各自自洽 —— 一条说「这是第 1 次启动」，另一条说
    「这也是第 1 次启动」，盘上却有两轮。推导出来就没有第二种可能。

    ``workspace`` 进签名是为了与 ``run_id`` 那一格对齐（它也是
    ``(workspace, key)`` 的函数）；这一格本身不拼进字符串，因为
    ``launch_subject`` 已经有了场景，而工作区已经在场景里。
    """
    return launch_subject(scene_id, scene_version, idempotency_key) + _FIRST_RUN_SUFFIX


def request_digest_of(
    *,
    workspace: str,
    scene_id: str,
    scene_version: int,
    idempotency_key: str,
    name: str,
    by: str,
) -> str:
    """这一次**请求的全部内容**的摘要（四元组之外的那几格）。

    ``LaunchRecord`` 的 docstring 把这一格与那条业务键分得很清楚：键回答「是不是
    同一次请求」，摘要回答「交上来的内容有没有变」。所以这里装的是**除键以外**
    调用方能影响的那几格 —— 换个 ``name``、换个人来按，都是另一次请求。

    摘要**不装时刻**：同一个请求在两分钟后再交一次仍然是同一个请求，而把时刻装
    进来会让重发在那条读回路径上被判成一次冲突 —— 那正是承重点 1 要挡的反面。
    """
    return sha256_text(
        SEP.join(
            [
                "launch-request",
                workspace,
                scene_id,
                str(scene_version),
                idempotency_key,
                name,
                by,
            ]
        )
    )


@dataclass(frozen=True)
class FirstRun:
    """``admit`` 的返回值：第一轮 Run 被受理了没有，以及没被受理的全部理由。

    ``run_id`` 为空 ⟺ ``queue`` 是 ``IDLE`` ⟺ ``why`` 非空。三条是同一件事的三个
    说法，而 :meth:`__post_init__` 逐条判 —— 一个「没有 run_id 也没有 why」的回执
    在报告上与「排队中」同形，而两者的处置相反（一个等，一个去看为什么）。
    """

    run_id: str
    queue: WorkstreamQueue
    next_wake_at: datetime | None
    why: str = ""

    def __post_init__(self) -> None:
        admitted = self.queue is WorkstreamQueue.RUN_ADMITTED
        if admitted and not self.run_id:
            raise ValueError(
                "queue=RUN_ADMITTED 却没有 run_id。\n"
                "  这一格说的是「第一轮已经被受理」，而受理的产物就是一个 run_id —— "
                "两者不一致时这一条回执声称发生了一件没有落盘的事。"
            )
        if not admitted and self.run_id:
            raise ValueError(
                f"queue={self.queue.value} 却带着 run_id={self.run_id!r}。\n"
                "  「排队中」与「已经有一轮在跑」是两件事，而处置相反：一个等，"
                "一个去看那一轮跑到哪了。"
            )
        if not admitted and not self.why.strip():
            raise ValueError(
                "第一轮没有被受理，却没有 why。\n"
                "  PSM §4.2 第 5 条逐字要「原因及何时会再次醒来」—— 一条没有原因的"
                "回执，读的人分不清是「容量排满了」还是「受理面拒了」，而两者要去"
                "的地方不同。"
            )
        if self.next_wake_at is not None:
            object.__setattr__(self, "next_wake_at", to_utc(self.next_wake_at))


@dataclass(frozen=True)
class StartResult:
    """``start_workstream`` 的回执：三个 id、队列状态、下次唤醒时刻。"""

    launch: LaunchRecord
    workstream: Workstream
    #: 这一次是**重发**（盘上已经有同一业务键的那一行）还是**新起的**。
    #: 它是一格而不是「看回执里有没有 warning」：重发的处置是「什么都不做」，
    #: 而新起之后通常还有下一步（去看那一轮跑到哪了）。
    replayed: bool
    #: 第一轮没有被受理时的那一句原因。受理了就空着。
    why: str = ""

    @property
    def run_id(self) -> str:
        return self.launch.run_id


def _stream_or_refuse(
    store: WorkstreamStore, *, workstream_id_: str, workspace: str
) -> Workstream:
    """按业务键读一个工作流；读不到或不是本工作区的，各自点名。"""
    stream = store.get_workstream(workstream_id_)
    if stream is None:
        raise ConfigError(
            f"没有 {workstream_id_!r} 这个工作流。\n"
            "  业务键的形状是 ``<scene_id>#<ordinal>``，而 ordinal 从 1 起 —— "
            "``<scene_id>`` 本身错了（那是查场景的问题）与 ordinal 错了（那是查"
            "第几次启动的问题）要分开查，所以这里把整个键原样写出来。"
        )
    if stream.workspace != workspace:
        raise EvidenceError(
            f"工作流 {workstream_id_!r} 属于工作区 {stream.workspace!r}，"
            f"而这次读它的是 {workspace!r}。\n"
            "  工作流的读法**按工作区**（存储那一层按 ``workspace`` 列过滤），而"
            "「按业务键读」这条路绕过了那层过滤 —— 业务键上没有工作区那一格"
            "（``Workstream.business_key`` 的 docstring 逐字说了为什么）。"
        )
    return stream


def _dispatch_read(
    stream: Workstream, record: SceneRecord | None
) -> tuple[bool, str]:
    """这个工作流今天放不放新的活，以及不放时的原因。**空串表示放行。**

    两半各说各的话，而合起来是一句话：工作流那一半（``state``）说「这个工作流被
    人停了没有」，场景那一半（``SceneRecord.dispatch_refusal``）说「这个场景被
    停了没有」。两处都是 ``if x:`` 的形态（空串 = 没问题），而不是 ``is None``
    —— ``SceneRecord.dispatch_refusal`` 的 docstring 逐字写了为什么。

    **两半都不许被对方顶替**：一个只看工作流状态的实现在场景被暂停时照常放行，
    而报告上它与「一切正常」同形。所以两句话都留着，各自点名。
    """
    reasons: list[str] = []
    if not stream.accepts_new_work():
        reasons.append(
            f"工作流本身是 {stream.state.value}"
            + (f"（{stream.pause_reason}）" if stream.pause_reason else "（未写明原因）")
        )
    if record is None:
        reasons.append(
            "它绑的那一版场景读不出来（这个工作流引用的 Scene 不在盘上）—— "
            "场景那一半的判定因此*没有*被做出，而不是被判成了放行"
        )
    else:
        refusal = record.dispatch_refusal
        if refusal:
            reasons.append(f"场景那一半说不行：{refusal}")
    if reasons:
        return False, "；".join(reasons) + "。"
    return True, ""


@dataclass(frozen=True)
class WorkstreamReceipt:
    """``get_workstream`` / ``list_workstreams`` 的**一格**。

    「是哪一次启动把它建出来的」那一格是 ``workstream.launch_id``，而不是一份
    嵌进来的 ``LaunchRecord``。「哪一次启动」的身份就是那个 id，而把整条记录嵌
    进来会让这一格在两个地方各有定义（这条工具与 ``start_workstream`` 的回执），
    此后它们会漂。

    ``scene_state`` 与 ``scene_pending_version`` 是**盘上那一版场景**的读数，
    不是这一行的。分成两格是因为它们回答的是两个问题；而 ``scene_state`` 为空串
    表示**读不出来**（那个场景不在盘上）—— 于是那一侧的判定没有被做出，见
    :func:`_dispatch_read` 里那一句「而不是被判成了放行」。
    """

    workstream: Workstream
    scene_state: str
    scene_pending_version: int | None
    dispatch_allowed: bool
    dispatch_refusal: str

    @property
    def workstream_id(self) -> str:
        return self.workstream.workstream_id


@dataclass(frozen=True)
class WorkstreamListing:
    """``list_workstreams`` 的回执。

    ``note`` 说的是**这一份清单读起来该注意什么**，而它有一个非空的最小情形：
    本工作区一个工作流都没有时，``note`` 逐字是
    :data:`NOTHING_IN_THIS_WORKSPACE`。「返回一张空表」与「这里本来就没有」
    在回执上是同形的 —— 而 list 那条 summary 点名的正是这一格：
    「本工作区没有工作流」要**说出来**，不是一个空数组。
    """

    streams: tuple[WorkstreamReceipt, ...]
    note: str = ""

    def __post_init__(self) -> None:
        if not self.streams and not self.note.strip():
            raise ValueError(
                "一份空的工作流清单没有留下任何说明。\n"
                "  「本工作区没有工作流」与「这一层没接上」在回执上同形，而两者的"
                "处置相反（一个是正常结果，一个要去查接线）。所以空表必须带一句话。"
            )


#: 本工作区一个工作流都没有时 ``note`` 的那一句。**逐字**。
NOTHING_IN_THIS_WORKSPACE = "本工作区没有工作流。"


def start_workstream(
    *,
    workspace: str,
    scene_id: str,
    scene_version: int,
    idempotency_key: str,
    name: str,
    at: datetime,
    by: str,
    store: WorkstreamStore,
    scenes: SceneStore,
    admit: Callable[[str, str], FirstRun],
    freeze: Callable[[], object] | None = None,
) -> StartResult:
    """启动一个工作流。**同一个 ``(场景, 版本, 幂等键)`` 再交一次回同一次启动。**

    ``admit`` 收两个参数 —— 第一轮 Run 的幂等键（由 :func:`first_run_key` 推出）与
    **这一次启动建出来的那个工作流的业务键**（``<scene_id>#<ordinal>``），回一个
    :class:`FirstRun`。它由 handler 接到**既有的**准入路径上。

    第二个参数是这一片拆 ``RunSpec.workspace`` 那个重载（承重点 5）的落点：第一轮
    Run 要能说出「我在哪一次启动之下」，而在这一层之外没有任何地方知道那个 ordinal
    —— 它是 ``next_ordinal`` 的下一个读数。所以它必须在**受理之前**算出来，而不是
    在受理之后补记；反过来的形态是「一轮 Run 先被受理，然后有人告诉它属于谁」。

    ``freeze`` 是**冻结那一版有效场景**（冻结表 §3.2：``start_workstream`` 冻结落库）。
    它与 ``admit`` 同一条理由用注入而不是 import：编译要一份 ``ProductCatalog`` 与
    那一组编译输入（``GeoScope`` / ``SalesMotion`` / 产品与方案），而 ``application/``
    够不到 ``adapters/``。缺省 ``None`` = 这一次不冻结（这一层的一个合法用法：绑定与
    受理不需要有效场景），而 handler 那一层**总是**给它。

    次序是有意的，三处都值得说：

    * **冻结在受理之前**。每一次启动绑的是**冻结了的那一版**，所以先有那一版再有它
      之下的第一轮；反过来会让一轮 Run 引用一份还没有落盘的编译结果。
    * **重发在冻结之前**就返回了（第 1 步），所以重发**一行都不写** —— 连一次幂等的
      ``put_scene`` 都不做。「再交一次什么都不发生」这句话在盘上是字面成立的。
    * **模型/版本拒绝（第 2 步）在冻结之前**：一次被拒的启动不留下一份没人启动过的
      有效场景。这是「一个字都没写」那句拒绝的落点。

    七步里有三步是判定，其余是把已经判完的东西落下来。判定的三步都会在拒绝时
    **点名**，因为三种拒绝的处置相反：

    1. **重发**（盘上已有同一业务键的那一行）⇒ 原样回那一次的结果。
       ``replayed=True``，**不重建工作流、不重置预算、不再开一轮 Run**。
    2. **场景不可派发**（暂停 / 归档 / 草稿）⇒ 拒绝，并把 ``dispatch_refusal``
       那句原文交出去。在这里放行会让一次启动在一个被暂停的场景上开出一轮 Run。
    3. **``scene_version`` 与场景记录上生效的那一版对不上** ⇒ 拒绝并**点名两版**。
       这一条对应那条 summary 逐字：「改成与预览时的主体对不上的组合被**点名
       拒绝**」。两类情形有两种处置：``latest_version`` 更大 ⇒ 有人写了一版新的
       （去批准它，或改用旧的那一版）；``effective_version`` 是别的数 ⇒ 传错了。
       合成一句「版本不对」会让这两种处置分不开。
    """
    at = to_utc(at)

    # --- 1. 重发：先读一次业务键 ------------------------------------------
    known = store.get_launch(
        workspace=workspace,
        scene_id=scene_id,
        scene_version=scene_version,
        idempotency_key=idempotency_key,
    )
    if known is not None:
        digest = request_digest_of(
            workspace=workspace,
            scene_id=scene_id,
            scene_version=scene_version,
            idempotency_key=idempotency_key,
            name=name,
            by=by,
        )
        if known.request_digest != digest:
            raise StoreError(
                f"启动 {known.launch_id!r} 已经用这个键记过了，而这一次交上来的内容"
                f"不是同一份：盘上是 {known.request_digest}，本次是 {digest}。\n"
                "  「同一个请求回同一个 launch」说的是**同一个请求**（``name`` 与"
                "按下启动的那个人都在请求里）。要改内容就换一个幂等键 —— 同一个键"
                "配两份内容是一次冲突，静默拿回第一次那一行会让调用方以为他开的"
                "那一轮跑的是他要的东西。"
            )
        stream = _stream_or_refuse(
            store, workstream_id_=known.workstream_id, workspace=workspace
        )
        return StartResult(launch=known, workstream=stream, replayed=True)

    # --- 2. 场景与版本 -----------------------------------------------------
    record = scenes.get_record(scene_id)
    if record is None:
        raise ConfigError(
            f"没有 scene_id={scene_id!r} 这个场景。启动要绑一版**已批准**的场景，"
            "所以它必须在盘上。"
        )
    if record.workspace != workspace:
        raise EvidenceError(
            f"场景 {scene_id!r} 属于工作区 {record.workspace!r}，而这次启动它的是 "
            f"{workspace!r}。一字未写。\n"
            "  工作区取自令牌声明（``SceneVersion.workspace`` 的 docstring 逐字："
            "「对象授权链的第一段」），不是请求参数 —— 所以这一处拒绝的是**一次**"
            "跨工作区的启动，而不是一个写错的参数。"
        )
    if record.dispatch_refusal:
        raise ConfigError(
            f"场景 {scene_id!r} 现在不能派发：{record.dispatch_refusal}。\n"
            "  这一版场景本身是好的，拦下这一次的是它的**状态** —— 而状态的处置是"
            "等人（恢复 / 批准草稿），不是改这次启动的参数。"
        )
    if scene_version != record.effective_version:
        pending = record.pending_version
        hint = (
            f"第 {pending} 版已经写了但还没有生效（``latest_version="
            f"{record.latest_version}``）—— 要启动它先让它生效，或者把 "
            f"scene_version 改成 {record.effective_version}。"
            if pending is not None
            else f"这个场景生效的是第 {record.effective_version} 版。"
            if record.effective_version is not None
            else "这个场景没有生效版本（它不是 ACTIVE）。"
        )
        raise ConfigError(
            f"这次启动说的是第 {scene_version} 版，而场景 {scene_id!r} 生效的是第 "
            f"{record.effective_version} 版。\n"
            f"  {hint}\n"
            "  两版对不上时**不挑**：挑一个跑下去会让回执上的「按哪一版做」与"
            "批准记录里那一版分家，而分家之后每一处引用它的地方都看不出来。"
        )

    # --- 3. 冻结这一版有效场景（§3.2）--------------------------------------
    if freeze is not None:
        freeze()

    # --- 4. 第一轮 Run 走既有的准入路径 ------------------------------------
    # 序号在**受理之前**读：``wid`` 既是这一行工作流的业务键，也是第一轮 Run 的
    # ``RunSpec.workstream``。受理之后再读一次会让「这一轮属于哪一次启动」多出一个
    # 可能的值（两次读之间另一个写者可以建出一个工作流），而两个值都合法。
    ordinal = store.next_ordinal(scene_id)
    wid = workstream_id(scene_id, ordinal)
    run_key = first_run_key(
        workspace=workspace,
        scene_id=scene_id,
        scene_version=scene_version,
        idempotency_key=idempotency_key,
    )
    first = admit(run_key, wid)

    # --- 5. 落记录 ---------------------------------------------------------
    launch = LaunchRecord(
        launch_id=launch_id_of(
            workspace=workspace,
            scene_id=scene_id,
            scene_version=scene_version,
            idempotency_key=idempotency_key,
        ),
        workspace=workspace,
        scene_id=scene_id,
        scene_version=scene_version,
        idempotency_key=idempotency_key,
        workstream_id=wid,
        run_id=first.run_id,
        request_digest=request_digest_of(
            workspace=workspace,
            scene_id=scene_id,
            scene_version=scene_version,
            idempotency_key=idempotency_key,
            name=name,
            by=by,
        ),
        created_at=at,
        launched_by=by,
    )
    stored = store.put_launch(launch)
    if stored.launch_id != launch.launch_id:
        # 另一个写者用同一个键先写下了**同一个请求**（摘要相等，否则 put_launch
        # 会抛）。第一轮 Run 不需要担心：它是内容寻址的，两个写者拿到的是同一个
        # run_id。这里只是把赢家的那一行读回来交出去 —— 不再写第二条记录。
        stream = store.get_workstream(stored.workstream_id)
        if stream is None:
            # 赢家写下记录之后、建工作流之前就不在了。补建是**修复**而不是覆盖：
            # 这一行从来不存在，而 put_workstream 的首次创建路径本来就要求它不存在。
            stream = _stream_for_launch(stored, name=name, at=at, first=first)
            store.put_workstream(stream, expected_revision=None)
        return StartResult(
            launch=stored,
            workstream=stream,
            replayed=False,
            why=first.why,
        )

    stream = _stream_for_launch(stored, name=name, at=at, first=first)
    store.put_workstream(stream, expected_revision=None)
    return StartResult(launch=stored, workstream=stream, replayed=False, why=first.why)


def _stream_for_launch(
    launch: LaunchRecord, *, name: str, at: datetime, first: FirstRun
) -> Workstream:
    """由启动记录推出那个工作流。**新的工作流永远是 RUNNING、revision=1。**

    新起的那个不该带着一个暂停态：一次启动把工作流建成停着的，与「它还没开始」
    在报告上同形，而前者要有人去恢复。
    """
    ordinal = int(launch.workstream_id.rsplit("#", 1)[1])
    return Workstream(
        workstream_id=launch.workstream_id,
        workspace=launch.workspace,
        scene_id=launch.scene_id,
        launch_id=launch.launch_id,
        scene_version=launch.scene_version,
        ordinal=ordinal,
        name=name,
        state=WorkstreamState.RUNNING,
        revision=1,
        queue=first.queue,
        run_id=first.run_id,
        next_wake_at=first.next_wake_at,
        pause_reason="",
        created_at=at,
        updated_at=at,
    )


def _receipt(
    stream: Workstream, *, scenes: SceneStore
) -> WorkstreamReceipt:
    """由一行工作流与它那一版场景，算出回执。**只读，不写。**"""
    record = scenes.get_record(stream.scene_id)
    allowed, refusal = _dispatch_read(stream, record)
    return WorkstreamReceipt(
        workstream=stream,
        scene_state=record.state.value if record is not None else "",
        scene_pending_version=record.pending_version if record is not None else None,
        dispatch_allowed=allowed,
        dispatch_refusal=refusal,
    )


def get_workstream(
    *,
    workspace: str,
    workstream_id: str,
    store: WorkstreamStore,
    scenes: SceneStore,
) -> WorkstreamReceipt:
    """一个工作流的**完整读数**：状态、revision、绑的那一版、由哪一次启动建的，
    以及今天放不放新的活 —— **不放的时候给出原因**。

    「放不放」是一个**判出来的**值，不是一个字段：它要同时问工作流那一半
    （``state``）与场景那一半（``SceneRecord.dispatch_refusal``）。回执里两格都
    在（``dispatch_allowed`` 与 ``dispatch_refusal``），所以读的人不必自己去推。
    """
    stream = _stream_or_refuse(store, workstream_id_=workstream_id, workspace=workspace)
    return _receipt(stream, scenes=scenes)


def list_workstreams(
    *, workspace: str, store: WorkstreamStore, scenes: SceneStore
) -> WorkstreamListing:
    """本工作区的全部工作流。**一个都没有时说出来**，不返回一张空表。

    ``note`` 的两档今天只有一档（空表那一档）：清单非空时没有要特别提示的事，
    而一个恒非空的 ``note`` 会让「这句话有意义」在第一次出现时就已经被稀释了。
    """
    found = store.workstreams(workspace=workspace)
    if not found:
        return WorkstreamListing(streams=(), note=NOTHING_IN_THIS_WORKSPACE)
    return WorkstreamListing(
        streams=tuple(_receipt(stream, scenes=scenes) for stream in found)
    )


def control_workstream(
    *,
    workspace: str,
    workstream_id: str,
    action: WorkstreamAction,
    expected_revision: int,
    reason: str,
    name: str,
    at: datetime,
    store: WorkstreamStore,
) -> Workstream:
    """带类型的暂停 / 恢复 / 改名。**收 ``expected_revision``，过期的改不进去。**

    ## CAS 的载体在存储那一层，而这一层不许自己判一遍

    ``adapters/sqlite/workstreams.py::_update`` 的 ``WHERE workstream_id = ? AND
    revision = ?`` 加 ``rowcount`` 才是 CAS。这一层把 ``revision`` 写成
    ``expected_revision + 1`` 再交下去 —— 而 ``put_workstream`` 会拒掉一个
    「不是读到的那一版加一」的写入，所以「改完对象直接写回」在这一层就红了。

    在这里**先读一遍再比一次** ``expected_revision`` 看着更友好，实际是把 CAS 变成
    了自觉：读与写之间的那个窗口里，另一个写者可以成功，而这次调用照样写下去。

    ## 三档各自的必填参数

    ``PAUSE`` 要 ``reason``（一个没有原因的暂停，三天后没人说得清该不该恢复）；
    ``RENAME`` 要 ``name``；``RESUME`` 两样都不要 —— 恢复是把一个已经被人写下来的
    决定撤销，而那个决定本身就带着原因。**不多要也不不少要**：多要一个参数会让
    调用方填一个没用的值，而那个值此后会被读成「他说过什么」。

    ## 它恰好碰一行，而这不是风格

    这条 summary 逐字：「停一个工作流**不改变另一个**的任何一格（各自一行、各自
    revision），但**共享的来源配额照旧共享** —— 那不是被暂停的那个工作流的属性，
    是按来源算的」。这两句在这一层各有一个落点：

    * 前一句是 :meth:`~marketops.ports.workstreams.WorkstreamStore.put_workstream`
      的形状 —— 它按 ``workstream_id`` 更新**一行**，签名的 ``expected_revision``
      是那一行的 CAS。**这里没有任何一处遍历别的行**，所以「另一个没变」不是一条
      需要遵守的纪律，是没有可写它的路径。
    * 后一句是**这里什么都没有**：来源配额不是一个字段，它按来源算（``RunSpec``
      的预算信封与 ``BudgetLedger`` 那一侧）。暂停一个工作流因此**够不到**它 ——
      而两件事要一起测，只测前一句会放过「把配额也一起冻住」的实现（那要在这里
      多写一行），只测后一句会放过「把两个工作流当成一个」的实现（那要在这里换一个
      更新条件）。
    """
    at = to_utc(at)
    stream = _stream_or_refuse(store, workstream_id_=workstream_id, workspace=workspace)

    target = action.target_state
    if action.requires_reason and not reason.strip():
        raise ConfigError(
            f"{action.value} 要一个原因，而这一次没有给。\n"
            "  一个没有原因的暂停，三天后没人说得清该不该恢复它 —— 而那是"
            "``control_workstream`` 这条工具存在的理由。"
        )
    if action is WorkstreamAction.RENAME and not name.strip():
        raise ConfigError(
            "rename 要一个新名字，而这一次没有给。\n"
            "  名字可以为空串（「没起名字」是一个说得出来的状态），但**改名成空**与"
            "**没打算改名**是两件事：前者是 ``name=\"\"`` 的一次改名，后者是不调这个"
            "工具。把前者当成后者，会让一次改名在盘上不留痕。"
        )
    if target is not None and target is stream.state:
        raise WorkstreamStateError(
            f"工作流 {workstream_id!r} 已经是 {stream.state.value}，"
            f"而这次要把它推到同一格。\n"
            "  三档里只有暂停与恢复会推状态，而「推到自己已经在的那一格」不是一次"
            "迁移：放它过去会让 revision 加一、回执说成功了，而盘上什么都没变 —— "
            "一次没发生的事写进一次成功的回执，此后没人分得清它到底发生过没有。"
        )
    if target is not None and target not in WORKSTREAM_TRANSITIONS[stream.state]:
        raise WorkstreamStateError(
            f"工作流 {workstream_id!r} 不能从 {stream.state.value} 走到 "
            f"{target.value}。\n"
            f"  允许的迁移只有 {sorted(s.value for s in WORKSTREAM_TRANSITIONS[stream.state])}。"
        )

    # 三档各自怎么处置 ``pause_reason``，**逐档写出来**。
    #
    # 原来这里是一句 ``reason if action.requires_reason else ""`` —— 它把「谁该
    # 写原因」折成了一个布尔，而三档里有两档的 ``requires_reason`` 都是 ``False``，
    # 于是**改名**（不要求原因）会顺手把一个暂停中的工作流的 ``pause_reason``
    # 清成空串。2026-09-29 实测的后果分两种，而两种都错：
    #
    # * SQLite：``put_workstream`` 会把那一行读回来重建对象，而
    #   ``Workstream._paused_says_why`` 要求「暂停必须写明原因」—— 于是**写这一步
    #   就抛**，抛的还是一个 ``pydantic.ValidationError``（不是本仓的错误类型），
    #   于是「暂停中的工作流改个名字」在后端根本做不成，而出去的形状是"服务端崩了"。
    # * 内存替身：它把对象原样存着（不重建），于是写成功了 —— 改名**静默地**把
    #   暂停原因清掉。同一个动作，两个实现一个做不成、一个做错了。
    #
    # 所以这一格不按 ``requires_reason`` 推：那一位回答的是**要不要传参**，
    # 不是**这一格该变成什么**。三档各说各的，而 RENAME 那一档说的是"不动它"。
    if action is WorkstreamAction.PAUSE:
        pause_reason = reason
    elif action is WorkstreamAction.RESUME:
        # 恢复时**清掉**原因：一个一边跑一边带着「为什么停着」的工作流，
        # 读的人不知道该不该去处理那个理由（domain 那一层的校验器逐字判这一条）。
        pause_reason = ""
    else:
        pause_reason = stream.pause_reason

    updated = stream.model_copy(
        update={
            "state": target if target is not None else stream.state,
            "pause_reason": pause_reason,
            "name": name if action is WorkstreamAction.RENAME else stream.name,
            "revision": expected_revision + 1,
            "updated_at": at,
        }
    )
    store.put_workstream(updated, expected_revision=expected_revision)
    # 回执是**盘上那一行**，不是刚拼出来的那个对象 —— 与 ``put_launch`` 的返回值
    # 同一条纪律。少了这一步，回执照的是一个可能没写进去的对象来说话。
    after = store.get_workstream(workstream_id)
    if after is None:  # pragma: no cover - put_workstream 成功之后不可能
        raise StoreError(
            f"工作流 {workstream_id!r} 刚写成功却读不回来 —— 存储那一层坏了。"
        )
    return after

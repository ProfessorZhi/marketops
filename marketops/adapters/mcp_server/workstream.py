"""工作流那一族的**四个**处理器（B01 / B02，冻结表 §3.2 的 Workstream 与 LaunchRecord）。

``start_workstream`` 是这一片唯一的写入口：它绑一版已批准的 Scene、落一条
``LaunchRecord``、建出 ``Workstream``，并让第一轮 Run 走**既有的**准入路径；另外三条
（``get_workstream`` / ``list_workstreams`` / ``control_workstream``）是读与带类型的
控制。**一个格子都不在这一层重算**：三个 id、队列状态与下次唤醒时刻由
``application/workstream.py`` 算，这一层只做四件事 —— 取时刻、把「主语不在盘上 /
是别人的」翻成一条拒绝、把存储层的失败翻成一条可读回执、把那一行的投影交出去。

## 为什么这里没有一条 ``Path`` / provider 的引用

启动这一条**不产生任何外部调用**，但它与前两个只读族不同：它**写**，而且写的那一份
（冻结的有效场景）要读一次 ``ProductCatalog`` —— 那是本地的目录，不是 provider。
``preview_effective_scene`` 那一族因此仍然够不到检索 / 抓取端口（判据在
``tests/test_effective_scene_is_offline.py``，它算的是那一族的 import 闭包），而这一族
**故意**够得到 ``runs.py``（受理要账本、维护账与主体来源）—— 它是写路径，那一族的
「零外部调用」判据本来就不覆盖它。

## 一次调用只有一个时刻（这一族比别处多一处要说明）

``_preamble``（``runs.py``）自己取一次 ``utc_now()``，而启动记录与工作流的
``created_at`` / ``updated_at`` 用外层那一个。两次读数的后果不是「差几毫秒」，是
**同一份回执上两个时刻各自自洽而互相说不通**：一条启动记录说它建在 T1，它之下第一轮
Run 说受理在 T2 > T1 —— 反过来（Run 早于建它的那一行）才是真正不可能的那一种。
所以这一层把 ``_Preamble.at`` 换成外层那一个（:func:`dataclasses.replace`），于是
**整次启动只有一个时刻**。换得掉是因为 ``_preamble`` 自己那一刻只被 ``round_of``
读过一次，还没有参与任何已经做完的判定（``plan_report`` 不带 ``at``）。

## 第一轮没被受理时，工作流照样建出来

``FirstRun`` 的三条不变量（``run_id`` 空 ⟺ ``queue`` 是 ``IDLE`` ⟺ ``why`` 非空）就是
为这件事写的：一份**说不清楚**的声明（``HOLD``）不拦下这一次启动，它拦下的是**第一轮
Run**。于是回执上是 PSM §4.2 第 5 条逐字要的三格 —— 实际排队状态、原因、以及何时再次
醒来（``next_wake_at=None`` 说的是「没有人会来叫它」，那是一句**说出来的**话，不是
省略）。反过来把 ``HOLD`` 读成一次拒绝，会让「这个市场开了、但今天这一轮没什么可跑」
与「这个市场没开」在报告上同形，而两者的下一步动作相反。

调用方**写错**的东西（模式、窗口三格、时刻、主体来源、幂等键配另一份内容、空幂等键）
仍然是拒绝 —— 那几条要改的是请求本身，不是等。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ...application.effective_scene import freeze_effective_scene
from ...application.plan_preview import PreviewOutcome
from ...application.run_admission import admit_run
from ...application.workstream import FirstRun, StartResult, WorkstreamReceipt
from ...application.workstream import control_workstream as _control
from ...application.workstream import get_workstream as _read
from ...application.workstream import list_workstreams as _list
from ...application.workstream import start_workstream as _start
from ...config import Settings
from ...domain.enums import ExecutionMode
from ...domain.errors import ConfigError, EvidenceError, StoreError
from ...domain.util import utc_now
from ...domain.workstream import (
    Workstream,
    WorkstreamAction,
    WorkstreamQueue,
    WorkstreamStateError,
)
from ...ports.budget import BudgetLedger
from ...ports.pool import AccountCohortStore, AccountPoolStore
from ...ports.product_catalog import ProductCatalog
from ...ports.runs import RunStore
from ...ports.scenes import MaintenanceStore, SceneStore
from ...ports.workstreams import EffectiveSceneStore, WorkstreamStore
from ._shared import Handler, require_scene, store_failure
from .effective_scene import _refuse, geo_of, motion_of
from .guard import current_principal
from .launch import _require_record, _require_version
from .runs import _ADMISSION_REFUSALS, _held_refusal, _preamble, _translate

__all__ = ["START_COMPILER", "workstream_handlers"]

#: 冻结那一版有效场景时写在 ``EffectiveScene.compiler`` 上的名字。
#:
#: 与 ``PREVIEW_COMPILER`` 分开是有意的：同一份 ``(scene_id, version)`` 由预览那一条
#: 编译出来与由启动这一条编译出来，是**两次动作**，而 ``compiler`` 那一格答的是
#: 「哪一条路径编译了它」。**它不是调用者的身份**（见 ``effective_scene.py`` 的模块
#: docstring 最后一节），所以这里写工具名。
START_COMPILER = "start_workstream"


# ----------------------------------------------------------------------
# 回执形状：三个工具共用的那一半
# ----------------------------------------------------------------------
def _stamp(value: datetime | None) -> str | None:
    """一格时刻的写法。``None`` 原样留着 —— 它是一句话（「没有人会来叫它」）。"""
    return None if value is None else value.isoformat()


def _stream(stream: Workstream) -> dict[str, Any]:
    """``Workstream`` 那一行的投影 —— ``get_workstream`` / ``list_workstreams`` /
    ``control_workstream`` **共用的那一半**。

    三处各写一遍的代价不是那十几行：先漂的是「哪些格子进这份回执」，于是一份说
    ``queue``、另一份不说，而三份都声称自己报的是**同一行**。
    """
    return {
        "workstream_id": stream.workstream_id,
        "workspace": stream.workspace,
        "scene_id": stream.scene_id,
        "scene_version": stream.scene_version,
        "ordinal": stream.ordinal,
        "launch_id": stream.launch_id,
        "name": stream.name,
        "state": stream.state.value,
        "revision": stream.revision,
        "queue": stream.queue.value,
        "run_id": stream.run_id,
        "next_wake_at": _stamp(stream.next_wake_at),
        "pause_reason": stream.pause_reason,
        "created_at": _stamp(stream.created_at),
        "updated_at": _stamp(stream.updated_at),
    }


def _receipt(receipt: WorkstreamReceipt) -> dict[str, Any]:
    """``get_workstream`` 的回执 = 那一行的投影 + **判出来的**那四格。

    ``dispatch_allowed`` 与 ``dispatch_refusal`` 是同一个判断的两个出口，两格都在
    （冻结表那一行说「不能时连原因一起给」）：一个只说「不能」的读数会让人去翻预算表、
    翻复核队列、翻暂停标记，而这三件事各自在哪一页是这一侧已经知道的事。
    """
    return {
        **_stream(receipt.workstream),
        "scene_state": receipt.scene_state,
        "scene_pending_version": receipt.scene_pending_version,
        "dispatch_allowed": receipt.dispatch_allowed,
        "dispatch_refusal": receipt.dispatch_refusal,
    }


def _launch_receipt(result: StartResult) -> dict[str, Any]:
    """``start_workstream`` 的回执：**三个 id 加队列状态与下次唤醒时刻**，外加
    「这一次是重发还是新起的」那一格。

    五格逐字来自 PSM §4.2 第 5 条（``launch``/``Scene``/``job`` id、实际排队状态、
    原因及何时会再次醒来）。``queue`` 与 ``why`` 两格是**并列**的而不是一格：一个
    没有 ``run_id`` 的回执在报告上既可能是「排队中」也可能是「受理失败」，而两者的
    处置相反，所以 ``queue`` 必须是说出来的、``why`` 必须在 ``IDLE`` 上非空
    （``FirstRun`` 的三条不变量判这一条）。

    ``replayed`` 是**新起一次**与**重发一次**的分界：重发时 ``run_id`` / ``state`` /
    ``revision`` 报的是**第一次**那一组，而 ``created_at`` 也是第一次那两个时刻 ——
    它们与 ``replayed`` 一起把「这一次什么都没发生」说清楚。
    """
    launch = result.launch
    stream = result.workstream
    return {
        "launch_id": launch.launch_id,
        "workstream_id": launch.workstream_id,
        "run_id": launch.run_id,
        "scene_id": launch.scene_id,
        "scene_version": launch.scene_version,
        "idempotency_key": launch.idempotency_key,
        "replayed": result.replayed,
        "launched_by": launch.launched_by,
        "created_at": _stamp(launch.created_at),
        "queue": stream.queue.value,
        "why": result.why,
        "state": stream.state.value,
        "revision": stream.revision,
        "ordinal": stream.ordinal,
        "name": stream.name,
        "next_wake_at": _stamp(stream.next_wake_at),
        "updated_at": _stamp(stream.updated_at),
    }


# ----------------------------------------------------------------------
# 当事人
# ----------------------------------------------------------------------
def _launched_by() -> str:
    """这一次启动是谁按下去的。**经传输层不可达**（无权者到不了这条写路径）。

    ``LaunchRecord.launched_by`` 是必填且非空的（它进 ``request_digest_of``：换了人
    就是**另一个请求**），所以这一格必须有一个答案。它取自传输层已确立的身份
    （``guard.current_principal``，Bearer 令牌），**不取自任何参数** —— 一个能由载荷
    填写的「谁批准的」不是证据。

    只要 ``name``，不要 ``roles``：与 ``feedback._actor_name`` 逐字同一条（这一层的
    对象上没有 roles 那一格）。这是本仓第四份同款小函数（``review._actor`` /
    ``product_catalog._principal_name`` / ``feedback._actor_name`` 各一份），而**这一份
    没有去 import 那三个私有的**：它们各自的必填项不同（``review`` 那条还要
    ``roles`` 非空），且分属三个别的切片。合并成一份要动三处别人的文件，而收益只是
    省下这二十行 —— 值得做，但属于 MAIN 的收敛动作，不是这一片顺手做的事。
    """
    principal = current_principal()
    if principal is None:
        raise ToolError(
            "这个动作需要主体：它是「谁按下的」那一格，而主体取自传输层的 Bearer "
            "令牌，不取自任何参数。"
        )
    if not principal.name.strip():
        raise ToolError(
            "这枚令牌没有主体名（sub 与 client_id 都是空的），而一次启动必须记下"
            "「谁按下的」—— 那一格进请求摘要（换了人就是另一个请求）。重铸一枚令牌。"
        )
    return principal.name


# ----------------------------------------------------------------------
# 第一轮 Run 的受理（``admit`` 的落点）
# ----------------------------------------------------------------------
def _first_round(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    runs: Callable[[], RunStore],
    maintenance: Callable[[], MaintenanceStore],
    ledger: Callable[[], BudgetLedger],
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
    scene_id: str,
    run_key: str,
    workstream: str,
    context: Mapping[str, Any],
    cutoff: str,
    budget: Mapping[str, int] | None,
    execution_mode: str,
    window: Mapping[str, Any] | None,
    name: str,
    cohort: str | None,
    at: datetime,
) -> FirstRun:
    """第一轮 Run 走**既有的**准入路径，回 :class:`FirstRun`。

    这一段与 ``submit_run`` 是**同一条**路（``_preamble`` → ``round_of`` →
    ``admit_run``），一处也没有另写：两处各接一次的话，同一份声明在两条入口上会
    得到两份规格，而两份规格的指纹不同 —— 那时「这一轮按哪一份计划跑」在盘上有两个
    答案。

    三处差别，逐条都有理由：

    * ``workstream=`` 那一格：冻结表 §3.2 说 ``RunSpec.workspace`` 今天兼任
      workstream，本片把它拆出来，而**写下它的场合只有这一个**（``admit_run`` 的
      docstring 逐字）。它由 ``start_workstream`` 在受理**之前**算出来（``ordinal``
      的下一个读数），所以第一次启动的第一轮 Run 自己说得清「我在哪一次启动之下」。
    * ``at``：外层那一个（见模块 docstring 那一节）。
    * ``HOLD`` 读成**报告**而不是拒绝：``FirstRun(queue=IDLE, why=…)``。
    """
    try:
        reading = replace(
            _preamble(
                settings=settings, scenes=scenes, scene_id=scene_id, context=context
            ),
            at=at,
        )
        # ``HOLD`` 在第二段之前说话（``_window`` / ``_mode`` / 主体来源都在那一段里）：
        # 一份编不出计划的请求不会被报成「窗口缺了三格」。判词与受理面**同一份**
        # （``_held_refusal`` 的 docstring 写着它有这两个读者）。
        if reading.plan_report.outcome is PreviewOutcome.HOLD:
            return FirstRun(
                run_id="",
                queue=WorkstreamQueue.IDLE,
                next_wake_at=None,
                why=str(_held_refusal(reading.plan_report)),
            )

        this_round = reading.round_of(
            settings=settings,
            maintenance=maintenance,
            accounts=accounts,
            cohorts=cohorts,
            budget=budget,
            cutoff=cutoff,
            idempotency_key=run_key,
            execution_mode=execution_mode,
            window=window,
            name=name,
            cohort=cohort,
        )
        run = admit_run(
            runs(),
            scene=this_round.scene,
            context=this_round.declared,
            plan=this_round.plan,
            # 信封 / 截止 / 幂等键取自那份规格，理与 ``submit_run`` 逐字相同：``round_of``
            # 已经解析过它们，而 ``admit_run`` 内部拿同一组值调 ``spec_of``。
            budget=this_round.spec.budget,
            cutoff=this_round.spec.cutoff,
            idempotency_key=this_round.spec.idempotency_key,
            at=this_round.at,
            execution_mode=this_round.mode,
            window=this_round.window,
            ledger=ledger() if this_round.window is not None else None,
            scene_store=this_round.store,
            maintenance=maintenance()
            if this_round.mode is ExecutionMode.WATCH
            else None,
            subjects=this_round.subjects,
            workstream=workstream,
        )
    except _ADMISSION_REFUSALS as exc:
        # 那四条结果与 pydantic 的形状拒绝共用 ``_translate`` 一份（同一个请求在
        # ``submit_run`` 与 ``start_workstream`` 上必须说同一句话）。
        raise _translate(exc) from exc
    except WorkstreamStateError as exc:
        raise ToolError(str(exc)) from exc

    return FirstRun(
        run_id=run.run_id,
        queue=WorkstreamQueue.RUN_ADMITTED,
        # 醒来的时刻取自受理那一轮**自己的地平线**。``manual`` 上是 ``None`` 且这是
        # 一句话：「没有人会来叫它」—— 手动那一轮是人拉起来的，把它写成 ``cutoff``
        # 会让它在一份唤醒名单上看起来像一个周期任务（``Workstream.next_wake_at``
        # 的 docstring 逐字判这一条）。
        next_wake_at=(
            None
            if this_round.mode is ExecutionMode.MANUAL
            else this_round.spec.cutoff
        ),
        why="",
    )


# ----------------------------------------------------------------------
# 四个处理器
# ----------------------------------------------------------------------
def workstream_handlers(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    catalogs: Callable[[], ProductCatalog],
    workstreams: Callable[[], WorkstreamStore],
    effective: Callable[[], EffectiveSceneStore],
    runs: Callable[[], RunStore],
    maintenance: Callable[[], MaintenanceStore],
    ledger: Callable[[], BudgetLedger],
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
) -> dict[str, Handler]:
    """工作流那四条工具，按工具名索引。

    十个工厂里没有一个是可省的，而理由分三档：

    * ``scenes`` / ``workstreams``：主语与它那一行。缺省会让「这个部署没有存储」与
      「这个工作流不存在」在报告上同形，而两者的处置不同。
    * ``catalogs`` / ``effective``：冻结那一版有效场景的两个输入。``effective``
      **必须与 ``effective_scene_handlers`` 收到的是同一个工厂** —— 冻结的一侧与读的
      一侧指向两个库时，两边各自都正常，而「刚冻结的那一版读不出来」会与「它从来没被
      冻结过」同形。
    * ``runs`` / ``maintenance`` / ``ledger`` / ``accounts`` / ``cohorts``：第一轮那一次
      受理要用（``_first_round``），而它们四个与 ``run_handlers`` 收到的是同一批 ——
      同一轮 Run 走的是同一条准入路径，两处接两个账本会让「这条链花了多少」有两个答案。
      ``accounts`` / ``cohorts`` 保持 ``| None``：只有 ``watch`` 读得着它们，而一个
      没有主体来源的部署仍然跑得动手动那一档（``_subjects`` 会在真需要时说话）。
    """

    def start_workstream(
        context: Mapping[str, Any],
        cutoff: str,
        idempotency_key: str,
        scene_version: int,
        scene_id: str | None = None,
        workstream_name: str = "",
        name: str = "",
        cohort: str | None = None,
        execution_mode: str = "manual",
        window: Mapping[str, Any] | None = None,
        budget: Mapping[str, int] | None = None,
        geo: Mapping[str, Any] | None = None,
        motion: Mapping[str, Any] | None = None,
        product_id: str | None = None,
        product_version: int | None = None,
        offer_id: str | None = None,
        offer_version: int | None = None,
    ) -> dict[str, Any]:
        """接下一次启动：绑一版已批准的 Scene、落记录、建工作流、受理第一轮。

        ## 参数分两段，名字不重样

        前三个（``context`` / ``cutoff`` / ``idempotency_key``）与 ``scene_version``
        是**调用方必须自己说清**的：研究目标、这一轮到什么时候为止、幂等键、按哪一版
        跑。它们没有缺省 —— 一个缺省会让「他没说」与「他说了一个空」同形，而两者在
        ``request_digest_of`` 上是同一份摘要。

        后面那一批与 ``submit_run`` **逐字同名**（``window`` / ``budget`` /
        ``execution_mode`` / ``name`` / ``cohort``）：它们是同一件东西（这一轮怎么跑、
        这一批主体从哪来），两个入口各写一套名字会让同一份声明在两处读成两份。
        于是 ``name`` 在这里也是**主体来源**（一份手写清单的名字），而工作流自己的
        名字叫 ``workstream_name`` —— 两格都叫 ``name`` 会让「我要给工作流起名」与
        「我要指定主体清单」在同一个签名里同形，而写错的那一个不会报错。

        ``workstream_name`` 缺省为空串是对的：「没起名字」是一个说得出来的状态，
        而改名是 ``control_workstream`` 的事（那里 ``name`` 只有一个含义）。

        ## 三条在这一层判的形状

        ``scene_version`` 从 1 起，``idempotency_key`` 非空。两条都不是与盘比对
        （那是应用层的事），是**参数本身成不成立**：

        * 第 0 版不是一版，而一个「没说」的版本号在应用层那一条「与生效版本对不上」
          的拒绝里读起来像「你给错了版本」—— 那是另一件事。
        * 空幂等键会让这一条工具的**重发**判据塌掉：业务键那一列上它退化成
          「每个 (工作区, 场景, 版本) 只有一个启动」，而两次真的不同的启动会互相
          覆盖成一次。它还进不了 ``spec_of`` 的校验（那要等到受理那一步），于是
          关键词是**形状**：在这一层拒，一次都没写。
        """
        chosen = require_scene(settings, scene_id)
        if scene_version < 1:
            raise ToolError(
                f"``scene_version`` 从 1 起，收到的是 {scene_version}。\n"
                "  启动要绑**某一版**已批准的 Scene，而「第 0 版」不是一个版本 —— "
                "把「没说」与「说了第 0 版」合成同一句，会让一次漏传的参数落到"
                "应用层那条「与生效版本对不上」的拒绝上，而那句话说的是另一件事。"
            )
        key = (idempotency_key or "").strip()
        if not key:
            raise ToolError(
                "``idempotency_key`` 是空的。\n"
                "  业务键是 ``(workspace, scene_id, scene_version, idempotency_key)``，"
                "而这一条工具的核心承诺是「同一个键再交一次回同一次启动」。键为空时"
                "它退化成「每个 (工作区, 场景, 版本) 只有一个启动」—— 两次真的不同的"
                "启动会互相覆盖成一次，而报告上两处都自洽。"
            )
        # 一次调用建一份句柄，两处用同一份（受理那一段经 ``lambda`` 拿的也是它）：
        # 建两份的后果与 ``_Preamble.store`` 那段逐字相同 —— 指向同一个库时看着一样，
        # 指向两个库时两处各自都正常。
        scene_book = scenes()
        by = _launched_by()
        at = utc_now()
        wanted = scene_version

        def admit(run_key: str, wid: str) -> FirstRun:
            return _first_round(
                settings=settings,
                scenes=lambda: scene_book,
                runs=runs,
                maintenance=maintenance,
                ledger=ledger,
                accounts=accounts,
                cohorts=cohorts,
                scene_id=chosen,
                run_key=run_key,
                workstream=wid,
                context=context,
                cutoff=cutoff,
                budget=budget,
                execution_mode=execution_mode,
                window=window,
                name=name,
                cohort=cohort,
                at=at,
            )

        def freeze() -> object:
            """冻结这一版有效场景（冻结表 §3.2 那条链的第二个箭头）。

            它**在受理之前**跑（``start_workstream`` 第 3 步），也在应用层那两处版本
            拒绝**之后** —— 所以「按哪一版跑」是这一族先判完的事，一次被拒的启动不会
            留下一份没人启动过的有效场景。编译输入与 ``preview_effective_scene``
            **同一份拼法**（``effective_scene.py`` 的 ``geo_of`` / ``motion_of`` —— 那个模块
            的公开函数，两条路径共用一份定义）：两处各拼
            一份的那天，预览给你看的那份与落库的那份会是两份不同的东西，而两份都自洽。

            ## 编译那一条的拒词在这里就翻（实测过的那条逃逸）

            编译要读产品与方案（``_read_product`` / ``_read_offer``），而**指名了却读不
            回来**在那两处是一条 ``ValueError``（「缺席」与「读不回来」的处置相反，所以
            它们不是同一件事 —— ``effective_scene.py`` 逐字判这一条）。这一族原先只接
            ``ConfigError`` / ``EvidenceError`` / ``WorkstreamStateError`` / ``StoreError``，
            于是那一条落进 ``tools/base.py:135-158`` 的**第二支**：调用方读到的是一句
            ``Error executing tool start_workstream``，而**那句话里点名的产品 / 方案与
            版本号一个字都到不了** —— 那是他唯一能据以改东西的两格。与
            ``effective_scene.py`` 的 :func:`~.effective_scene._refuse` 逐字同一款缺陷、
            同一份翻法。

            翻在这个闭包里而不是把 ``ValueError`` 加进 handler 那个元组：``_start``
            之下还会冒出 ``ValueError`` 的地方就只剩 ``FirstRun`` 的三条不变量
            （``__post_init__``）—— 那是**本仓的编程错误**，一次崩溃不该伪装成一次
            调用方的拒绝。位置也正是它安全的理由：第 3 步在任何写之前（``_start`` 的
            第 1、2 步只读），所以从这里抛出去的每一次拒绝都是「一字未写」。
            """
            record = _require_record(
                scene_book, scene_id=chosen, workspace=settings.profile
            )
            declared = _require_version(
                scene_book,
                scene_id=chosen,
                version=wanted,
                latest_version=record.latest_version,
            )
            try:
                return freeze_effective_scene(
                    store=effective(),
                    scene=declared,
                    record=record,
                    workspace=settings.profile,
                    catalog=catalogs(),
                    at=at,
                    compiler=START_COMPILER,
                    geo=geo_of(geo, at=at, workspace=settings.profile),
                    motion=motion_of(motion, at=at, workspace=settings.profile),
                    product_id=product_id,
                    product_version=product_version,
                    offer_id=offer_id,
                    offer_version=offer_version,
                )
            except (ConfigError, EvidenceError, ValueError) as exc:
                # ``StoreError`` 不在这里翻：它由 handler 那一层的 ``store_failure``
                # 接（同一个形状，那一支的由来写在它自己的 docstring 上）。
                raise _refuse(exc) from exc

        try:
            result = _start(
                workspace=settings.profile,
                scene_id=chosen,
                scene_version=wanted,
                idempotency_key=key,
                name=workstream_name,
                at=at,
                by=by,
                store=workstreams(),
                scenes=scene_book,
                admit=admit,
                freeze=freeze,
            )
        except (ConfigError, EvidenceError, WorkstreamStateError) as exc:
            # 三条都是**结果**，不是崩溃：`ConfigError` 是「没有这个 / 这一版对不上 /
            # 这个场景现在不能派发」，`EvidenceError` 是「找到的那一份是别人的」，
            # `WorkstreamStateError` 是「这一格推不到那一格」。三条都要原样回到工具
            # 结果上 —— 不接它们，SDK 会把一段很准的话压成一句
            # ``Error executing tool start_workstream``（``store_failure`` 的 docstring
            # 记着实测过的这一个形状）。
            raise ToolError(str(exc)) from exc
        except StoreError as exc:
            raise store_failure(exc) from exc
        return _launch_receipt(result)

    def get_workstream(workstream_id: str = "") -> dict[str, Any]:
        """读一个工作流的完整读数。读不到时点名**它要的形状**（``<scene_id>#<序号>``）。"""
        wid = (workstream_id or "").strip()
        if not wid:
            raise ToolError(
                "``workstream_id`` 是空的。\n"
                "  业务键的形状是 ``<scene_id>#<ordinal>``（``start_workstream`` "
                "的回执上有这一格），而 ordinal 从 1 起。"
            )
        try:
            receipt = _read(
                workspace=settings.profile,
                workstream_id=wid,
                store=workstreams(),
                scenes=scenes(),
            )
        except (ConfigError, EvidenceError) as exc:
            raise ToolError(str(exc)) from exc
        except StoreError as exc:
            raise store_failure(exc) from exc
        return _receipt(receipt)

    def list_workstreams() -> dict[str, Any]:
        """本工作区的全部工作流。**一个都没有时那句话在 ``note`` 里**，而它必须被读。

        回执的形状与 ``WorkstreamListing`` 一样只有两格：``workstreams`` 与 ``note``。
        分出 ``note`` 而不是在空表上补一句，理由是这一层的读回面上「读错了工作区」
        「Workstream 这一层没接上」与「这里本来就没有」三件事同形，而 ``note`` 是唯一
        分得开它们的那一格 —— 一个只读 ``workstreams`` 的调用方拿到的是一张空表，
        那正是这条工具要避免的形状。
        """
        try:
            listing = _list(
                workspace=settings.profile, store=workstreams(), scenes=scenes()
            )
        except StoreError as exc:
            raise store_failure(exc) from exc
        return {
            "workstreams": [_receipt(item) for item in listing.streams],
            "note": listing.note,
        }

    def control_workstream(
        workstream_id: str = "",
        action: str = "",
        expected_revision: int = 0,
        reason: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        """带类型的暂停 / 恢复 / 改名。``expected_revision`` 是 CAS 要比的那一格。

        ``action`` 收一个**闭集**里的字符串并在这里当场转成枚举：拼错的动作在
        ``WorkstreamAction`` 上构造期就红，而 ``ToolError`` 里点出闭集 —— 一个
        「落到 else 分支」的实现在拼错时总是能做点什么，而那件事不是任何人要求的。

        ``expected_revision`` 从 1 起，且**必填**（缺省 0 只为了让它出现在参数表里）：
        它是「我读到的是第几版」那一格，而 CAS 的全部内容就是它。给 0 与不给同形，
        所以这一层拒它，理由与 ``get_effective_scene`` 拒 ``version=0`` 逐字同类。

        回执是**盘上那一行**（应用层读回来的那一份）加这一次的请求：``action`` 与
        ``expected_revision`` 与结果并列，读的人因此看得出「要它从第 3 版走到第 4 版」
        与「它现在在第几版」是不是同一件事。
        """
        wid = (workstream_id or "").strip()
        if not wid:
            raise ToolError(
                "``workstream_id`` 是空的。\n"
                "  业务键的形状是 ``<scene_id>#<ordinal>``（``start_workstream`` "
                "的回执上有这一格）。"
            )
        try:
            chosen_action = WorkstreamAction(action)
        except ValueError as exc:
            allowed = " / ".join(item.value for item in WorkstreamAction)
            raise ToolError(
                f"``action`` 收 {allowed}，收到的是 {action!r}。\n"
                "  这一格是**带类型**的那一档：一个拼错的动作名在这里红，而一个"
                "「落到 else 分支」的实现在拼错时照样能做点什么 —— 而那不是任何人"
                "要求的动作。"
            ) from exc
        if expected_revision < 1:
            raise ToolError(
                f"``expected_revision`` 从 1 起，收到的是 {expected_revision}。\n"
                "  它是 CAS 要比的那一格（「我读到的是第几版」）。工作流的第 0 版"
                "不存在，所以 0 与「没给」同形，而两者都会让这一次控制跳过一次比对。"
            )
        try:
            after = _control(
                workspace=settings.profile,
                workstream_id=wid,
                action=chosen_action,
                expected_revision=expected_revision,
                reason=reason,
                name=name,
                at=utc_now(),
                store=workstreams(),
            )
        except (ConfigError, EvidenceError, WorkstreamStateError) as exc:
            raise ToolError(str(exc)) from exc
        except StoreError as exc:
            raise store_failure(exc) from exc
        return {
            **_stream(after),
            "action": chosen_action.value,
            "expected_revision": expected_revision,
        }

    return {
        "start_workstream": start_workstream,
        "get_workstream": get_workstream,
        "list_workstreams": list_workstreams,
        "control_workstream": control_workstream,
    }

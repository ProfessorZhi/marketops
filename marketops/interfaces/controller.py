"""常驻控制器 —— §B21 那个**唯一**的受管唤醒入口。

    marketops-controller --once            跑一轮就走
    marketops-controller                   常驻，按 ``--interval`` 一轮一轮地跑
    python -m marketops.interfaces.controller ...

## 它为什么住在这里，而不是 ``application/``

``application/continuation.py::ContinuationController`` 只说「拿到归属之后怎么跑
一轮」，而**归属怎么来**是另外三件事：谁去争、争不到怎么办、什么时候放手。那三件
都是**这个部署**的性质（进程模型、信号、间隔），而且要**点名**具体 adapter
（``SqliteJobStore`` / ``SqliteWakeupRegistry`` / 派发执行器 / 维护存储），所以它
只能住在组合根这一层 —— 与 ``cli.py`` / ``composition.py`` 同一条理由（B4 的
「只有 ``interfaces/`` 与 ``adapters/`` 自己可以点名某个具体厂商 adapter」）。

它**不 import ``adapters/mcp_server/``**，这一条是承重的：B21 要证的「正常 MCP 提交
之后断开客户端，第二批确实自动执行」里，控制器与 MCP 服务是**两个进程**，而
``mcp_server/`` 那一族要装得上才有 MCP SDK。控制器依赖它就等于「没装 MCP 的部署
跑不了自动续跑」—— 而自动续跑恰恰是那个部署唯一的用处。所以下面那处读证据库的
三行写法与 ``mcp_server/tools.py::read_store`` 逐字同形，但**不**从那里 import。

## 三个动词的顺序就是那一条纪律

``acquire`` → （每轮）``heartbeat`` → ``release``。**``--once`` 也要先 acquire**，
这一条不是洁癖：一次性的那种跑法最容易写成「跑一轮看看」，而它与常驻的那一个推的是
同一批共享资源（模型预算、执行器、Quay 任务）。两种跑法都从同一处 CAS 进来，
「不能并行双触发器」才是一个性质，而不是常驻模式自己的一条自觉。

拿不到归属时**不驱动**，等下一次醒来再 acquire。等是安全的：``acquire`` 本身是一次
CAS，重试它不驱动任何东西（见 ``ports/continuation.py`` 的那一段）。

``heartbeat`` 在两轮**之间**跳，不在控制器里跳（``ContinuationController`` 的
docstring 逐字写了为什么）。它返回 ``False`` 就是「我已经不是持有者了」——那时
**立刻停手**，回去重新争。

## 退出码

``0`` 跑完了 / ``1`` 运行失败 / ``2`` 配置错误 / ``3`` **归属不在我手里** /
``130`` 收到信号。

``3`` 单独占一格，因为它是这五个里唯一一个「什么都没做错」：另一个控制器正在驱动
同一个工作区，而本进程唯一正确的动作是什么都不做。把它并进 ``0`` 会让
``--once`` 在一台已经有了控制器的机器上回「成功」，于是「我跑了一轮」与
「我没跑」在脚本里长得一样。
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime

from ..adapters.page_mirror import page_copier
from ..adapters.sqlite.jobs import SqliteJobStore
from ..adapters.sqlite.wakeup import SqliteWakeupRegistry
from ..application.continuation import (
    ContinuationController,
    ContinuationExecutor,
    ContinuationOutcome,
    ContinuationRun,
    ContinuationStatus,
    survey,
)
from ..application.dispatch import WorkDispatch
from ..application.maintenance import effective_version, run_once
from ..application.refresh import REFRESHABLE_ASPECTS, build_refresher
from ..config import Settings, load_settings
from ..domain.continuation import BlockedOn, WakeupOwner
from ..domain.enums import JobState
from ..domain.errors import ConfigError, MarketOpsError
from ..domain.subject import SUBJECT_KIND_ACCOUNT
from ..domain.util import utc_now
from ..ports.continuation import WakeupRegistry
from ..ports.jobs import JobStore
from ..ports.scenes import MaintenanceStore, SceneStore
from .composition import (
    default_acquisition,
    default_dispatcher,
    default_document_budget,
    default_maintenance_store,
    default_router,
    default_scene_store,
)

#: 两轮之间隔多久。**未标定**：它不表达任何业务节奏，只是「醒来看看」的下界。
#: 一条续跑意图的 ``due_at`` 决定它**能不能**做，这个间隔只决定「多久看一眼」。
#: 所以它取一个不比人的耐心长、也不至于把库读成热点的数。
DEFAULT_INTERVAL_SECONDS = 30.0

#: 一轮最多做几件。**有缺省值而不是 ``None``**，因为常驻模式与 ``--once`` 各自的
#: 「不小心」不一样：``--once`` 跑多了只是这一轮多花点；常驻模式跑多了是**一直**
#: 多花。而 §B21 逐字禁止「用『持续运行』绕无限预算」—— 那个禁令在这一层的执行器
#: 就是这个数。
DEFAULT_LIMIT = 5

#: ``--once`` 拿不到归属时的退出码。见模块 docstring。
EXIT_NOT_OWNER = 3


class LiveContinuationExecutor(ContinuationExecutor):
    """**这个部署**的执行器：两个动词各自接上真的那两趟。

    它是 ``ContinuationExecutor`` 的第一个真实实现 —— 在此之前的两个实现都在测试里
    （``tests/test_continuation_controller.py::FakeExecutor``）。所以「执行器必须
    幂等」那条要求在这里第一次真的有东西要满足，而它满足的方式**不是**在这个类里
    加一个去重表：两个动词各自都是幂等的，理由在下面各自的 docstring 里。

    ## 它不自己判断「该不该做」

    闸门（场景暂停 / 到期 / 上限）在 ``ContinuationController`` 里，一次判完。在这
    一层再判一次，就是同一个判定的第二个产地，而它们漂移的形态是「控制器说可以做、
    执行器说不用做」——盘上那一行会照控制器的话停在 ``CLAIMED`` 上，直到处理器换代
    才被收回。所以这里**只**做两件在闸门之后才能做的事：读那条意图指向的业务对象、
    把活交出去。

    ## 报 ``BLOCKED`` 的三类理由都不是「做不成」

    它们是三类**说得出理由的等待**（暂停 / 缺接线 / 缺主体），与 ``FAILED`` 的处置
    不同：``BLOCKED`` 不吃 ``attempts``（一个暂停了一周的场景不该因为每 30 秒 tick
    一次而被判成重试了一万次），而 ``FAILED`` 吃。判断的界线是**这一次有没有真的
    试过**，不是「做成了没有」。

    三类里只有「缺接线」是在这一层**判**出来的（它是本部署的性质）；暂停与缺主体
    都是从下面抬上来的判词 —— 暂停由 ``WorkDispatch`` 拒、由这里分类，缺主体由
    :meth:`_resolve_subjects` 判。这一层不自己再判一次「该不该做」。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        workspace: str,
        jobs: JobStore,
        scenes: SceneStore,
        dispatcher,
    ) -> None:
        self._settings = settings
        self._workspace = workspace
        self._jobs = jobs
        self._scenes = scenes
        self._dispatcher = dispatcher

    # ------------------------------------------------------------------
    def dispatch_batch(
        self, *, scene_id: str, job_id: str, checkpoint: str, now: datetime
    ) -> ContinuationOutcome:
        """把一个作业当下该派的活交出去。

        ## 幂等靠 ``ALREADY_MAPPED``，不靠这里

        ``WorkDispatch.dispatch_job`` 逐件读本地映射，已经有外部任务的那几件原样
        返回、一次都不碰外面。所以「被收回之后重跑一次」不会造出第二个任务 ——
        这正是 §B21「控制器崩溃后恢复不重复昂贵工作」那一格在派发路径上的全部内容。
        这个类不另加一层去重：两层去重里外那层永远是没被验过的那一层。

        ## 「作业不放行派发」不在这一层判，只在这一层**分类**

        暂停的作业在这一层没有一条自己的分支：``WorkDispatch.dispatch_job`` 在碰外部
        **之前**就按 ``admits_dispatch`` 拒了（一并把「为什么拒」的那句话写在一处，
        ``dispatch.py::_dispatch_refused``），这里读它的 ``blocker``、判那是不是暂停、
        报 ``BLOCKED(PAUSE)``。两个产地会让同一件事有两句话，而它们漂移的那天调用方
        会以为那是两种不同的拒绝 —— 这一条判据由变异测试守着（``if False:`` 换掉下面
        那一处分类，暂停就落进 ``FAILED``）。

        ## 收敛与派发是两笔账，所以两趟都跑

        ``converge_settled`` 把**已经结算的那些件**的看板推到外面。它接在这里而不是
        接在结算那一刻，是因为这一趟问的是「外面现在对不对」——那个问题的答案在一次
        进程死掉之后仍然问得出来（``tests/test_research_dispatch.py`` 与
        ``tools.py::_dispatch_block`` 的备注逐字如此）。
        """
        job = self._jobs.get(job_id)
        if job is None:
            # 一条指向不存在作业的意图在写路径上构造不出来（``admit`` 与它欠下的
            # 那一行在同一个事务里）。走到这里说明盘被人动过 —— 这是要人看一眼的
            # 事，所以报 FAILED 而不是 BLOCKED：三种阻塞理由说的是「等一等就好」。
            return ContinuationOutcome.failed(
                f"续跑意图指向的作业 {job_id} 不在盘上。受理与它欠下的下一步是同一次"
                "写事务落下的，所以这个状态在写路径上构造不出来 —— 请对账。"
            )
        if self._dispatcher is None:
            return ContinuationOutcome.blocked(
                reason=(
                    "本部署没有装外部执行器，所以这一批工作没有被交出去 —— 它们仍然是"
                    "持久的 work item，执行器到位之后会照常派发。\n"
                    "这一格报 BLOCKED 而**不报 DONE**：那批工作确实还没交出去，"
                    "报完成会让这一行从盘上消失，而下一批永远不会被触发。"
                ),
                on=BlockedOn.CAPACITY,
            )

        work = WorkDispatch(jobs=self._jobs, dispatcher=self._dispatcher)
        round_ = work.dispatch_job(job_id)
        if round_.blocker:
            # 派发途中作业又被暂停了一次（循环里那一格），或者它的状态既不放行派发、
            # 也没有被 ``set_state`` 的 ``_rearm`` 收走 —— 后者是一个异常，所以两条
            # 分开处置。重读一次状态而不是复用上面那个 ``job``：那一个说的是**这一趟
            # 开始时**的事，而这里要判的是「途中变了没有」。
            land = self._jobs.get(job_id)
            if land is not None and land.state is JobState.PAUSED:
                return ContinuationOutcome.blocked(reason=round_.blocker, on=BlockedOn.PAUSE)
            return ContinuationOutcome.failed(
                round_.blocker
                + "\n作业的状态既不放行派发、也没有把这条意图收走 —— "
                "请对账（``set_state`` 的 ``_rearm`` 负责收走它）。"
            )

        convergence = work.converge_settled(job_id)
        return ContinuationOutcome.done(
            reason=(
                f"派发 {round_.handed_out} 件（共 {len(round_.receipts)} 件在账上），"
                f"看板收敛 {convergence.pushed}/{convergence.considered} 件"
            ),
            # 进度是给人读的，也是恢复时的起点。它**不是**幂等的凭据 —— 幂等靠上面
            # 那条 ``ALREADY_MAPPED``，把这两件事混起来会让「重跑安全」依赖一个
            # 恰好写对了的字符串。
            checkpoint=f"handed_out={round_.handed_out};pushed={convergence.pushed}",
        )

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

        ## 主体从哪来，以及两格都空时是什么

        两格非空时就是它：§B21「仅相关对象等人」靠这一条成立 —— 一条被挡住的主体
        只带它自己的身份，它旁边那些主体的意图照常跑。

        两格都空是**合法输入**（``settle`` 那一次说不出主体是谁），而本部署在那一种
        输入上给不出主体 —— 于是它报 ``BLOCKED``，理由里写清缺的是哪一个生产者。
        完整推理在 :meth:`_resolve_subjects` 里，因为那是判定所在的地方。

        ## 复核内容：一格本地盘点 + 两格**真的去外面看**

        ``build_refresher`` 按面分派：``source_coverage`` 读一遍本地证据记录、落一份
        盘点；两个页面面（``contact_person_role`` / ``email_evidence``）把这一格名下
        **已知的页面**按 URL 重取回来、与记录里的指纹比一次 —— B22 接上的就是这一条。
        所以这个执行器**会花钱**（抓取额度，记在 ``BudgetLine.DOCUMENT`` 上），也**会
        够不着**（不可达那一格不写观测）。上限来自冻结的 profile（``fetch.max_documents``），
        没声明就失败关闭。发信、碰外部执行器这些事照旧不做。

        ## 接线在**每趟**造一次，而不是在 ``__init__`` 里

        与 :meth:`_evidence_store` 同一条：这个类是常驻的（一次驻留只构造一次），而
        「接线」在它这一层没有「一次」—— 一趟 tick 才是这里的单位。在构造点建好会
        把一份运行时能力做成启动那一刻的快照，而三样里没有一样是启动时才成立的。

        计划（``plan``）取自**生效版本**而不是最新版本：新写了一版没批准，不该改变
        在跑的这件事的节奏（``effective_version`` 逐字如此）。``DRAFT`` 的场景回
        ``None``，那时 ``run_once`` 会跳过它（``not_ticking``），回调一次都不会被调到。
        """
        subjects, blocker = self._resolve_subjects(scene_id, subject_kind, subject_ref)
        if blocker:
            return ContinuationOutcome.blocked(
                reason=blocker,
                on=BlockedOn.CAPACITY,
                checkpoint=checkpoint,
            )

        # 计划与三样接线在 ``build_refresher`` **之前**取：它们是那一趟的输入，
        # 而 ``build_refresher`` 只是把它们绑成一个回调。
        version = effective_version(self._scenes, scene_id)

        # 这一趟**算**一次观测（它数的是主体名下全部证据行），而**写那一行的是**
        # ``run_once`` —— 写观测的权限没有下放到回调手里（``application/refresh.py``
        # 与 ``run_maintenance`` 的备注逐字如此）。所以这里只给回调，不给写口。
        refresh = build_refresher(
            store=self._evidence_store(),
            root=self._settings.maintenance_dir,
            workspace=self._workspace,
            at=now,
            plan=None if version is None else version.refresh,
            acquisition=default_acquisition(self._settings),
            router=default_router(mineru_output_dir=self._settings.mirrors_dir / "mineru"),
            documents=default_document_budget(self._settings),
            # 一次取回的字节要能落进**另一个主体**的目录，才谈得上不重复付费的共享
            # （§6「相同内容给多个 Scene 只付一次获取费用」）。落不下去就不共享 ——
            # 台账缺省那一支，理由在 ``application/reuse.py``。
            copy_page=page_copier(self._settings),
        )
        report = run_once(
            scene_store=self._scenes,
            maintenance_store=self._maintenance_store()(),
            workspace=self._workspace,
            now=now,
            work=refresh,
            scenes=(scene_id,),
            subjects={scene_id: subjects},
            limit=None,
            aspects=REFRESHABLE_ASPECTS,
        )
        if report.not_ticking:
            _, reason = report.not_ticking[0]
            return ContinuationOutcome.blocked(reason=reason, on=BlockedOn.PAUSE)
        attributed = (
            f"主体 {subject_ref}"
            if subject_ref
            else f"本批次全部 {len(subjects)} 个主体"
        )
        if report.failed:
            # 一格挂了不代表整批挂了，但这一条意图**这一次**确实没做成 ——
            # 报 FAILED 让 ``attempts`` 走那条次数界限，而不是每一轮重来。
            #
            # 主语写在**前面**（与成功那一条逐字同形）：这句话是运维在账上看到的
            # 那一行，而只有一串 dedupe_key 的一行答不出「是哪件事挂了」。逐格的
            # 理由（哪几页没看成、因为什么）在维护账上，这一行只负责把人指过去。
            return ContinuationOutcome.failed(
                f"{attributed}：复核失败（共 {len(report.failed)} 格）"
                "：" + "、".join(claim.dedupe_key for claim in report.failed),
                checkpoint=checkpoint,
            )
        return ContinuationOutcome.done(
            reason=(
                f"{attributed}：复核 {report.claimed_count} 格（到期 {report.due_count}），"
                f"记下 {len(report.observations)} 条观测"
            ),
            checkpoint=checkpoint,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_subjects(
        scene_id: str, subject_kind: str, subject_ref: str
    ) -> tuple[tuple[tuple[str, str], ...], str]:
        """这一趟 tick 哪些主体，或一条说得出口的阻塞。``((), 理由)`` 是阻塞。

        ## 两格都空这一格：本部署说不出来，所以报阻塞而不是 tick 一个空集

        ``settle`` 那一次的身份快照不存在时两格都是空的（那是一种合法输入，见
        :meth:`ContinuationExecutor.maintenance_tick`）。「那就 tick 整个场景」需要
        一份**场景的主体名单**，而本部署没有那个生产者：场景文件里没有主体，主体来自
        作业的输入（``input_kind`` / ``input_ref``），而 ``work_item_id → job`` 这一步
        要的是业务键 —— 它不在这个动词的参数表里（``_execute`` 只递 ``scene_id`` 与
        主体两格，因为它只该递这一条意图**携带**的信息）。

        tick 一个空主体集**不行**：``run_once`` 会照常跑完、报「0 格到期」，而那读起来
        与「这一格核过了、没有到期的」逐字相同 —— 于是一件**没做的**事在账上变成了
        一件做完的事。所以这里报 ``BLOCKED(CAPACITY)``：它的处置是「运维补上那个生产
        者」，而那正是本部署该听到的话。它不吃 ``attempts``，所以每一轮都会报同一句
        话，不会变成一个终态的失败。
        """
        if subject_ref:
            return ((subject_kind or SUBJECT_KIND_ACCOUNT, subject_ref),), ""
        return (), (
            f"这一条复核意图没有主体（结算那一次的身份快照不存在），而按场景 {scene_id} "
            "枚举主体在本部署里没有生产者：场景不携带主体，主体来自作业的输入，而这条"
            "意图没有带业务键。\n"
            "  处置：这一批工作单元在编译时没有钉身份快照（``WorkItemSnapshot.subject``），"
            "所以那次结算说不出它是关于谁的。重新编译会带上快照，那时结算欠下的就是一条"
            "带主体的意图；在那之前这一条每一轮都会报同一句话。"
        )

    def _evidence_store(self):
        """证据库；**库还不存在时返回 ``None``**。

        「读一次不建库」与 ``mcp_server/tools.py::read_store`` 逐字同一条：一次复核
        顺手建出一个空库，会让「还没有任何产物」与「这里本该有东西」在盘上长得一样,
        而后者正是需要被看见的那种故障。

        那三行**不**从 ``tools.py`` import —— 理由在模块 docstring 的最后一段：
        控制器必须能在没装 MCP SDK 的部署里跑起来。
        """
        from ..adapters.sqlite.evidence_store import SqliteEvidenceStore

        path = self._settings.database_path
        return None if not path.is_file() else SqliteEvidenceStore(path)

    def _maintenance_store(self) -> Callable[[], MaintenanceStore]:
        return default_maintenance_store(self._settings)


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def _status_payload(status: ContinuationStatus) -> dict[str, object]:
    """只读那一页的 JSON 形状 —— 形状本身住在 ``ContinuationStatus.as_dict``。

    原先它写在本文件里，而服务面的 ``get_continuation_status`` 也要同一份形状。
    ``adapters/`` 不能 import ``interfaces/``（分层方向），所以它搬到了那个
    dataclass 上：两处各搭一遍的漂移形态是「工具说 WAITING_DUE、日志说 PAUSED」。
    """
    return status.as_dict()


def _run_payload(run: ContinuationRun) -> dict[str, object]:
    """一轮做了什么的 JSON 形状。七段各自成行 —— 合成一个「处理了 N 件」会让
    「做成 / 做挂 / 被别人抢走 / 被顶掉」在账上长得一样，而它们的处置各不相同。

    ## 两笔「没有发生」的账也在这里，理由与那七段相反

    ``not_ticking`` 与 ``truncation`` 记的是**没发生的事**：被闸门挡住的场景，以及被
    上限砍掉的尾巴。它们不能省，因为它们的缺席与它们的空值是两件事：

    * 省掉 ``not_ticking``，一个**被暂停的场景**与一个**盘上没活**的队在一轮回执上
      逐字相同（``touched: 0`` 配几张空表）—— 前者要人去按一个按钮，后者什么都不
      用做，而运维只能从这两者里选一件事去做；
    * 省掉 ``truncation``，「没找到」与「找到了但被上限砍了」也逐字相同，那是
      ``application/caps.py`` 存在的全部理由。

    两格的形状都取自产生它们的那两个 dataclass，不在这里另立一种
    （``TruncationDecision.as_dict`` 已经声明了它写进报告的样子）。
    """
    return {
        "at": run.at.isoformat(),
        "workspace": run.workspace,
        "state": run.status.state.value,
        "reclaimed": [row.continuation_id for row in run.reclaimed],
        "done": [
            {"continuation_id": row.continuation_id, "kind": row.kind.value,
             "reason": row.reason}
            for row in run.done
        ],
        "failed": [
            {"continuation_id": row.continuation_id, "kind": row.kind.value,
             "attempts": row.attempts, "reason": row.reason}
            for row in run.failed
        ],
        # 「这一次挂了、但**还要再试**」与「试够了、等人看一眼」是两件事，而它们
        # 原来在这一份回执上**都读不出来**：``failed`` 只装后者，前者落进
        # ``ContinuationRun.retrying``，而那个元组没有被这一份回执转发过。于是
        # 一轮「驱动了一行、它挂了、重排了下一次」在输出里是
        # ``touched: 1`` 配四个空表 —— 恰好是这个函数开头那句要防的形态
        # （「合成一个『处理了 N 件』会让它们长得一样」的反面：**一个都不列**，
        # 而 ``touched`` 说有一件）。
        # §B22 之后它成了**正常路径**：一格页面复核在主体名下还没有来源时会失败，
        # 那条意图于是每一轮重排一次 —— 在此之前只有「复核回调抛了」才走得到这里。
        "retrying": [
            {"continuation_id": row.continuation_id, "kind": row.kind.value,
             "attempts": row.attempts, "due_at": row.due_at.isoformat(),
             "reason": row.reason}
            for row in run.retrying
        ],
        "blocked": [
            {"continuation_id": row.continuation_id, "kind": row.kind.value,
             "on": "" if row.blocked_on is None else row.blocked_on.value,
             "reason": row.reason}
            for row in run.blocked
        ],
        "not_claimed": [row.continuation_id for row in run.not_claimed],
        "superseded": [row.continuation_id for row in run.superseded],
        # 「这个场景这一轮没在跑」—— 形状与 ``retrying`` 那种「一行一句话」同源，
        # 而主语是**场景**而不是续跑行：闸门是按场景关的。
        "not_ticking": [
            {"scene_id": scene_id, "reason": reason}
            for scene_id, reason in run.not_ticking
        ],
        # ``None`` = **这一轮没有上限**（不是「上限是 0」，也不是「装了 0 条」）：
        # 与 ``TruncationDecision.cap`` 的 ``None`` 逐字同一个含义。
        "truncation": None if run.truncation is None else run.truncation.as_dict(),
        "touched": run.touched,
        "ok": run.ok,
    }


def _emit(payload: dict[str, object], *, stream) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream, flush=True)


class _Stopper:
    """收到 SIGINT / SIGTERM 之后**跑完这一轮再退**。

    不是「立刻退」：一轮里可能有几件已经交出去了，而收尾（``release`` 那一句）必须
    发生 —— 一次崩溃与一次说出口的再见是两件事，而 ``stale`` 只区分「还活着」与
    「没在跳」（``WakeupRegistry.release`` 的 docstring 逐字如此）。等这一轮结束的
    代价是几秒，换来的是下一任**立刻**能接手，而不是白等一个
    ``OWNER_STALE_AFTER``。
    """

    def __init__(self) -> None:
        self.asked = False

    def __call__(self, signum, _frame) -> None:
        self.asked = True
        print(f"== 收到信号 {signum}：跑完这一轮就退出 ==", file=sys.stderr, flush=True)


def _build(
    settings: Settings,
) -> tuple[JobStore, WakeupRegistry, SceneStore, LiveContinuationExecutor]:
    """这一个进程要用到的全部接线。**一处** —— 与 ``composition.py`` 那条纪律同构：
    两处各搭一遍的漂移形态是「控制器推的是这个库、工具读的是那个库」。"""
    settings.ensure_data_dirs()
    jobs = SqliteJobStore(settings.database_path)
    registry = SqliteWakeupRegistry(settings.database_path)
    scenes = default_scene_store(settings)()
    executor = LiveContinuationExecutor(
        settings=settings,
        workspace=settings.profile,
        jobs=jobs,
        scenes=scenes,
        dispatcher=default_dispatcher(settings),
    )
    return jobs, registry, scenes, executor


def run_once_cli(
    *,
    settings: Settings,
    holder: str,
    limit: int | None,
    out=None,
) -> int:
    """跑一轮就走。**先 acquire** —— 见模块 docstring 的第二段。"""
    jobs, registry, scenes, executor = _build(settings)
    at = utc_now()
    owner = registry.acquire(settings.profile, holder=holder, now=at)
    if owner is None:
        current = registry.current(settings.profile)
        _emit(
            {
                "drove": False,
                "workspace": settings.profile,
                "reason": (
                    "这个工作区的唤醒归属在别人手里，本进程**不驱动** —— "
                    "两个触发器推同一批共享资源正是 §10 要消灭的那一件。"
                ),
                "owner": None
                if current is None
                else {
                    "holder": current.holder,
                    "epoch": current.epoch,
                    "heartbeat_at": current.heartbeat_at.isoformat(),
                    "stale": current.is_stale(utc_now()),
                },
            },
            stream=out or sys.stdout,
        )
        return EXIT_NOT_OWNER

    try:
        run = ContinuationController(
            jobs=jobs, scenes=scenes, executor=executor, owner=owner
        ).tick(utc_now(), limit=limit)
        _emit(_run_payload(run), stream=out or sys.stdout)
        # 只读那一页跟着跑一次：它答的是「为什么此刻没有更多工作在跑」，而一轮刚
        # 跑完之后正是这个问题最该被回答的时刻。
        _emit(
            _status_payload(
                survey(jobs=jobs, scenes=scenes, workspace=settings.profile, now=utc_now())
            ),
            stream=sys.stderr,
        )
        return 0
    finally:
        registry.release(settings.profile, holder=owner.holder, epoch=owner.epoch)


def run_resident(
    *,
    settings: Settings,
    holder: str,
    limit: int | None,
    interval: float,
    out=None,
    stopper: _Stopper | None = None,
) -> int:
    """常驻：一轮一轮地跑，直到收到信号。

    ## 每一轮的形状是固定的

    心跳 → 没归属就争 → 争到才驱动 → 报 → 睡。

    心跳放在**驱动之前**：上一轮结束时我还拿着归属，而这一轮醒来时它可能已经被
    别人顶掉了（我卡了一轮太久）。先心跳，``False`` 就把 ``owner`` 丢掉、回去重新
    争 —— 于是「被顶掉之后又驱动了一轮」在这段代码里没有位置。

    ## 拿不到归属时不驱动，但也不退出

    ``--once`` 拿不到就退（它没有下一次）；常驻的那一个**等**：另一个控制器可能
    正常退出、可能崩掉，而本进程是那个部署准备好接手的第二个进程。等的时候按同一个
    ``interval`` 重试 —— 一个空转的 acquire 循环会把这一层的开销变成一次持续运行的
    代价（``ports/continuation.py`` 逐字如此）。
    """
    jobs, registry, scenes, executor = _build(settings)
    stop = stopper or _Stopper()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    stream = out or sys.stdout

    owner: WakeupOwner | None = registry.acquire(
        settings.profile, holder=holder, now=utc_now()
    )
    if owner is None:
        print(
            f"== 这个工作区的唤醒归属在别人手里，本进程先等着（每 {interval:g} 秒"
            "重试一次，不驱动任何东西）==",
            file=sys.stderr,
            flush=True,
        )
    try:
        while not stop.asked:
            if owner is not None and not registry.heartbeat(
                settings.profile,
                holder=owner.holder,
                epoch=owner.epoch,
                now=utc_now(),
            ):
                # 上一代被顶掉了。**不停手就是并行双触发器**，所以这里唯一正确的
                # 动作是把手里这一份丢掉，回去按 CAS 重新争。
                print(
                    f"== 归属已经不是我（{owner.holder} 第 {owner.epoch} 代）的了，"
                    "停手，重新争 ==",
                    file=sys.stderr,
                    flush=True,
                )
                owner = None
            if owner is None:
                owner = registry.acquire(settings.profile, holder=holder, now=utc_now())
            if owner is None:
                time.sleep(interval)
                continue

            run = ContinuationController(
                jobs=jobs, scenes=scenes, executor=executor, owner=owner
            ).tick(utc_now(), limit=limit)
            _emit(_run_payload(run), stream=stream)
            time.sleep(interval)
    finally:
        # 干净退出走 ``release``：下一任从 ``epoch=1`` 开始，而不是先白等一个
        # ``OWNER_STALE_AFTER``（那 90 秒的空白会被读成「系统卡了」）。
        if owner is not None:
            registry.release(settings.profile, holder=owner.holder, epoch=owner.epoch)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marketops-controller",
        description=(
            "MarketOps 的受管唤醒入口：一个工作区同一时刻一个触发器。"
            "缺省常驻，加 --once 跑一轮就走。"
        ),
    )
    parser.add_argument(
        "--holder",
        default="marketops-controller",
        help="这一代的名字，进报告用（它**不是**身份证明 —— 身份是那一行本身）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"一轮最多做几件（缺省 {DEFAULT_LIMIT}）；0 表示不封顶",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"两轮之间隔几秒（缺省 {DEFAULT_INTERVAL_SECONDS:g}），只对常驻模式有效",
    )
    parser.add_argument(
        "--once", action="store_true", help="跑一轮就走（同样要先拿到唤醒归属）"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # ``0`` 是「不封顶」这个人话，``None`` 是那个参数的取值 —— 翻译只在这一处。
    limit = args.limit or None
    try:
        settings = load_settings()
        if args.once:
            return run_once_cli(settings=settings, holder=args.holder, limit=limit)
        return run_resident(
            settings=settings,
            holder=args.holder,
            limit=limit,
            interval=args.interval,
        )
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except MarketOpsError as exc:
        print(f"运行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_LIMIT",
    "EXIT_NOT_OWNER",
    "LiveContinuationExecutor",
    "build_parser",
    "main",
    "run_once_cli",
    "run_resident",
]

"""§B17 的 **Run 面** —— 受理（``submit_run``）、读回（``get_run`` /
``get_run_increment``）与结束（``close_run``）。

## 这一条工具为什么存在

``application/run_admission.py`` 把受理写成了一件事，而它在生产代码里此前
**零调用点**：一份能受理、能拒绝、能被契约测试逐条钉住的用例层，只要没有一条
经得起权限链的入口，整条链就只活在测试里。这一片给它接上头。

## 十一格里有六格**不能**由调用方给，这一条工具就是那句话的落地

``RunSpec`` 的十一格里，``workspace`` / ``selector`` 取自**盘上的 Scene**，
``objective`` 取自这份上下文，``context_ref`` / ``plan_ref`` / ``policy_versions``
由它们各自的生产者算出来 —— 这**六格**在这一层只被填，不被问。剩下那五格与**这一条
工具收到的六个参数**不是同一组（``context`` 与 ``objective`` 是同一件事的两头：
调用方交上来的是**声明**，``objective`` 是服务端从它编译出来的那一格），所以两个数
都要写清，否则「六格不能由调用方给」与下面六个入参会被读成一句自相矛盾的话。

**这一条工具收到的六格**是这次提交的内容本身，不是能被顺手替它作证的事实：

* ``context`` —— 要研究什么。
* ``budget`` —— 这一次的被批准额度。
* ``cutoff`` —— 跑到什么时候为止。
* ``idempotency_key`` —— 由调用方给。服务端**不生成**（见 ``domain/run.py::run_id``：
  一旦服务端能自己造键，它就能造出第二个 Run，而「自行扩下一批」正是从那里开始的）。
* ``execution_mode`` —— 缺省 ``manual``。

第六个入参 ``window`` 与上五格同款（它是内容，不是服务端已经知道的事实），只是它
**只在自动模式上出现**：``manual`` / ``watch`` 带上它是错的那一对，由 ``RunSpec``
的跨格校验器拒。e2c 之前这一格不存在 —— 那时没有一处写下过窗口，所以受理面对自动
模式一律拒。

## 「谁有权签一份窗口」不在这一层判，这是一条**记在册的缺口**

把窗口开成一个调用方参数，等于承认了一件事：**今天任何能调 ``submit_run`` 的主体
都能签一份自动授权**。§B17 逐字要求「只有**显式授权窗口存在时**」才按窗口接下一
轮，而这一格满足的是「显式」那一半 —— 窗口写在 ``RunSpec`` 里、进那一行的记录、
读得回来，它**不是**由 ``execution_mode`` 隐含出来的。

缺的那一半是「由谁签」：这层判不了，因为受理面手里只有传输层给的角色，而「这个
角色能不能签一份跨多轮、跨多对象的自动授权」是一条**身份/授权层**的裁定。它排在
B17 之后（B01/B02），本片**不为之造一个占位判据** —— 占位判据的下场是它此后被读
成「这里已经判过」。这一节留在这里的原因同上：缺口要写在**它该在的地方**，而不是
让人从「``window`` 是个参数」推出「所以谁都能签」之后再自己去找那一句。

写下来的那三格（``max_objects`` / ``budget`` / ``not_after``）**三项都必填**，理由
逐字在 ``domain/run.py::AuthorizationWindow``：一个缺席的阈值与「那条判据没触发」
在回执上同形。

**计划由服务端现编，不由调用方交一份。** 这一格与别的几格不是同一种取舍：
``admit_run`` 比的就是「这份计划是不是从这份上下文编出来的」，而调用方交上来的
计划不可能自己证明这一点 —— 受理面只能拿 ``preview_plan`` 现编一份，那份比对
按构造成立。把 ``plan`` 开成一个参数，等于让一次基于 A 上下文的提交被挂上一份
B 的计划，而盘上每一处（回执、Delta、将来的复用判定）都会显示「这一轮是按那份
计划跑的」。

## 两个面，**一段前奏**（切片 g）

``preview_run``（切片 g）与 ``submit_run`` 描述的是**同一轮**，所以两处**不能各
拼一次** ``RunSpec``：漂移的形态是「preview 说这一轮是 X，提交下去的是 Y」，
而两边都跑得过、都自洽、都通得过各自的判据。共用的那一段是
:func:`_preamble` + :meth:`_Preamble.round_of`，它一路走到
``application/run_admission.py::spec_of``（那份规格的**唯一**生产者）为止，
两处拿到的是**逐字同一个指纹**的规格；受理面内部再造一次的那份也是同一个生产者
造的。判据钉在 ``tests/test_run_preview.py``：两个面拼出来的
``spec_fingerprint`` 逐字相等，而且那个 ``at`` 是同一个值。

这一段的**形状**是有意的：``_preamble`` 只走到「计划编出来没有」就返回，而
「模式 / 窗口 / 主体 / 规格」在第二段（``round_of``）。理由是**次序**：今天
``HOLD`` 在 ``_window`` / ``_mode`` 之前说话（一份编不出计划的请求不会被报成
「窗口缺了三格」），而合在一步里会让那个次序反掉 —— 反掉之后两句话都是真的，
只是先说的那一句是次要的。

## ``preview_run``：同一个前奏的**读**面

权威是 §B17 正文那句（``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:253``）：preview
必须先报告「已有事实复用多少、预计补哪些缺口、计划可能调用哪些 provider、
预算/stop、哪些歧义会进入 Review」，而它本身**零 provider 调用**。它落在
``application/run_preview.py``，这一层只做两件事：接上前奏，把它交出去。

**它在 ``HOLD`` 上与受理面说的不是同一句话，这是有意的。** 受理面把 ``HOLD``
读成一次**拒绝**（编不出计划就不受理），而 preview 把同一件事读成一次**报告**：
``outcome='hold'``、``plan=None``，§253 的五项里那三条答得出来的（复用、缺口、
stop）回到 ``not_reported`` 里并各自点名「计划这一次没编出来」。同一个输入在两个
面上得到不同的**第一句话**听起来像是那种要防的漂移，而差别在于两面要答的问题不同：
一面是「受理了吗」，一面是「按现在这份声明，能报告出什么」。不许的是**判据**不同
—— 而判据是有的那一条：两处都调 :func:`_plan_report`，``HOLD`` 与 ``ConfigError``
的分法逐字同一份。

它**不写任何一行**（这是它比 ``submit_run`` 更靠前的地方：连受理都不是），而它
读得比 ``get_run_increment`` 早：那一份要一个**已经受理**的 ``Run``，这一份在写下
那一行**之前**读同一批本地存储。三者的分工与「读回面那两条读的是两种东西」是同一
条纪律的第三个落点。

## 时刻由服务端取

``admit_run`` 要一个 ``at``，而它在这一层由 ``utc_now()`` 取 —— 与产品环
``create_scene`` / ``control_scene`` / ``run_maintenance`` 那几处逐字同款。
一个能由调用方给的受理时刻，会让「截止时刻在不在将来」这条判定取决于调用方
报的是哪个时刻。

## ``plan_preview`` 的两条拒绝**都是结果**，不是崩溃

* ``HOLD``（硬歧义）→ 拒绝，并把 ``held`` 逐条列出来。这一支**不写盘**：一份
  场景都定不下来的声明，编出来的计划是拿保守值顶上的一份，而它此后会被读成
  「这一轮按那份计划跑了」。
* ``ConfigError``（请求落在批准范围之外）→ 拒绝。它是一条治理答案，不是一次
  内部错误；放它逃出去，调用方读到的是一句光秃秃的 ``UnexpectedToolError``。

## 刻意**没有**一个 ``replayed`` 布尔

同一个键重发时，``admit`` 拿回的是**同一行**（幂等由主键保证）。回执里把
``created_at``（那一行写下去的时刻）与 ``admitted_at``（**这一次调用**的受理
时刻）两格都报出来，于是「这一行早就有了」是从 ``created_at < admitted_at``
**读出来**的一句话，不是这一层替调用方下的一条判断。「重发」与「第一次」在那
一格上分得开，而多出来的那个布尔会在下一次有人改受理语义时变成一处没人维护的
口径。

## 受理**不是**执行

它不派发、不创建 work item、不**写**预算账本。一个把这两件事写进同一个入口的实现，
会让 §B17 条件 3 那句「manual 下完成后保持 idle、绝不自行扩下一批」在代码里
无处落脚 —— 因为那时「下一批」是在受理内部自己开的。

**「不写」与「不碰」不是一句话。** 带窗口的自动受理要**读**一次账本：条件 4 的
第二个阈值是「达累计预算」，而累计只有账本答得了（``RunStore`` 按设计读不到金额）。
读的是这条链上那几笔预留的当前状态，一个字都不写回 —— 它是**受理的输入**，不是
执行留下的痕迹。说成「不碰账本」会让下一个读的人以为带上账本的那条路没接线，而去
把它「修」成不读。

## 读回面的两条读的是**两种东西**，所以它们不是一个工具的两个模式

``get_run`` 读的是**盘上那一行**：那一行是什么样，它就报什么样。而
``get_run_increment`` 读回的是**按当下的盘现算的一份**：它的输入
（``RefreshPlan`` ＋ 当轮 ``Observation`` ＋ ``EvidenceStore`` ＋ 变更信号）
没有一格存在那一行上，全都是此刻从场景与维护账里取出来的。同一个 Run 在两分钟
之后调用它，可能拿到一份不同的读数 —— 而这不是缓存失效，是**它答的问题本来
就是**「照今天的盘看，这一轮该做什么」。

那条差别有一处会直接露在回执上：``get_run_increment`` 的 ``at`` 是**这一次调用**
的时刻，它答不出「这一轮当时是怎么算的」—— 那个问题今天没有落点（``RunIncrement``
不被持久化），而本层不为它猜一个形状。

## ``plan_increment`` 的第三条输入是主体，而这一层没有它的来源

格子空间是「观测里出现过的 ∪ 显式声明的」（``domain/maintenance.py`` 的
``_known_subjects``），而它刻意**不自己发现主体**：那里的 docstring 逐字写着
「一个会自己发现主体的选择器必然也会自己**发明**主体」。显式那一半的来源
（一份账号池、或 operator 手写的一份清单）在本模块里没有 —— 它是
``get_maintenance_plan`` 的 ``cohort`` / ``name``，而那要另一套主体解析。

所以本层传 ``subjects=()``：格子空间完全由**已沉淀的观测**决定。一个还没有任何
观测的工作区会拿到一份**零格**的读数 —— 而它必须不可能被读成「没有事要做」。
处置是把喂进去的输入规模一并报出来（``inputs``），并在 ``note`` 里逐字说明这一格
读的是什么：零格配 ``observations=0`` 是「还没有沉淀下来的事实可复用」，而 §B17
条件 1 的主语正是**连续两个 manual Run 的第二个** —— 那时盘上已经有观测了。

## 结束面（``close_run``）与它要的那第五样东西

§B17 条件 2 是「Run 结束有完整 Delta，**业务键双向对账**」。对账的两侧一个是这一轮
交上来的主张、另一个是盘上**真的**发生了什么，而后者只有池子与折入账答得了 ——
所以 ``run_handlers`` 要的第五样是 ``assets``。它加在 e4，与 ``close_run`` 一起来。

同理，**没有池子的部署不受理结束**：``close_run`` 的全部意义是「账户变大之前先对一
遍账」，而一份读不到池子的实现在这一格上只能把两份主张互比一遍 —— 那正是这条判据
要发现的那件事（池子变大了而报告上说没变）在结构上读不出来的形状。

``folded`` 收的是**一串池子句柄**（e4 收窄的那一段写在
``application/run_reconciliation.py``）：收句柄是因为 ``PromotionOutcome`` 在受理解析
面上**造不出来**（它那一格要么是真 ``VenueAsset``，要么是空），而照着回执拼一个出来
等于往回执里填伪造的字段。句柄是这一层与池子**共有**的那一个地址空间，也正好是
``get_run`` 的 ``folds`` 读回来的那一串 —— 读写面成对，中间不需要一次翻译。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ...application import run_reconciliation
from ...application.plan_preview import PlanPreview, PreviewOutcome, preview_plan
from ...application.run_admission import admit_run, spec_of
from ...application.run_increment import plan_increment
from ...application.run_preview import held_report, run_preview
from ...config import Settings
from ...domain.enums import BudgetLine, ExecutionMode, RunStatus
from ...domain.errors import (
    ConfigError,
    IdempotencyConflictError,
    RunFoldLedgerError,
    RunReconciliationError,
    RunSpecError,
    RunStateError,
)
from ...domain.research_context import Ambiguity, ResearchContext
from ...domain.research_plan import EffectiveResearchPlan
from ...domain.run import AuthorizationWindow, Run, RunBudgetLine, RunSpec
from ...domain.run_delta import RunDelta
from ...domain.scene import SceneVersion
from ...domain.util import to_utc, utc_now
from ...ports.budget import BudgetLedger
from ...ports.pool import AccountCohortStore, AccountPoolStore, VenueAssetStore
from ...ports.runs import RunStore, unknown_run_refusal
from ...ports.scenes import MaintenanceStore, SceneStore
from ...ports.views import ActionItemStore
from .product_loop import _plan_context, _subject_source, _subjects
from ._shared import readable as _readable
from .tools import Handler, read_store, require_bundle

__all__ = ["run_handlers"]


def run_handlers(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    maintenance: Callable[[], MaintenanceStore],
    runs: Callable[[], RunStore] | None,
    ledger: Callable[[], BudgetLedger],
    assets: Callable[[], VenueAssetStore],
    followups: Callable[[], ActionItemStore],
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
) -> dict[str, Handler]:
    """本构建的 Run 面处理器，按工具名索引。

    Args:
        settings: 配置。受理只用到它的 ``profile``（本部署服务的那个 Scene）。
        scenes: 场景存储的**工厂**。这一条工具要它来回答「工作区与对象范围是
            什么」—— 那两格是从盘上的 Scene 取来的，不是参数。**不加 ``| None``**：
            没有场景存储的部署没有可受理的对象范围，而一个受理了却没有范围可言的
            Run 在记录上无法与一次真实受理区分。
        maintenance: 维护账的**工厂**（观测与变更信号）。``get_run_increment`` 要它
            来回答「这个工作区里已经沉淀了哪些事实」—— 那一格是现读的，不在 Run
            那一行上。**同样不加 ``| None``**，理由与 ``scenes`` 逐字同款：没有维护
            账的部署读不出这一轮该补什么，而一份**恒为空**的格子集与一份「这里什么
            都还没有」的读数在报告上同形 —— 前者的处置是接线。
        runs: Run 记录的工厂。``None`` 表示这个实例没有落点 —— 那时**这一族工具
            不注册**（``run_handlers`` 返回空表），而不是注册成一条恒回
            「没有这一轮」的实现：一本没接线的账与一本读过的空账在报告上同形，
            而前者的处置是接线、后者是去受理一次（与 ``fetch_attempt_handlers``
            逐字相同的取向）。
        ledger: 预算账本的**工厂**。只有 ``submit_run`` 用得到它，而且只在一次
            **带窗口**的受理上：条件 4 的第二个阈值是「这条链达累计预算」，累计只有
            账本答得了。**不加 ``| None``**，这一格与上面两格不同却得出同一个结论，
            值得写清差别：``scenes`` / ``maintenance`` 缺席时那一族工具**做不了它
            唯一的那件事**；账本缺席时 ``manual`` 受理照旧完整可用，坏掉的只有窗口
            那一条 ── 而那种「一半功能还在」的部署最容易被读成「还行」。一个缺省的
            ``None`` 会把「这个部署忘了接账本」推迟到**调用时**变成一句
            「账本读数没接上」（``ports/runs.py::window_refusal`` 的 fail-closed，
            它是对的），而那是把一次部署错误伪装成一次请求错误。必填让它在这里就炸。
        assets: 场地资产池的**工厂**（``ports/pool.py::VenueAssetStore``）。
            ``close_run`` 要它来答「这一轮折进去的那几行现在在不在」—— 对账的方向一
            逐条问的就是这件事。**同样不加 ``| None``**，而这一格与上面四格的差别值得
            写清：``scenes`` / ``maintenance`` 缺席时那一族工具做不了它唯一的那件事；
            ``ledger`` 缺席时坏掉的只有窗口那一条；池子缺席时 ``submit_run`` 与两条
            读回面**照旧完整可用** —— 坏掉的只有结束面。这正是那句「一半功能还在的
            部署最容易被读成还行」的又一个落点：一个缺省的 ``None`` 会让「忘接池子」
            推迟成 ``close_run`` 那一侧的一句「读不到池子」，而那时这一轮**已经在跑
            了**，它的产出没有一处能结掉。
        followups: 跟进池的**工厂**（``ports/views.py::ActionItemStore``）。
            ``close_run`` 要它来开这一轮产出的待办（§B24 的 ``Action`` 那一半），并把
            它们的 ``action_id`` 写回 Delta 的 ``actions`` 那一格。**同样不加
            ``| None``**：``close_run`` 是**唯一**能让一轮跑完的 Run 结掉的入口，而一个
            缺省值要么把这条入口整个停掉（那一轮永远停在 running），要么让本模块自己
            造一个池子 —— 后者的形态与「接对了」在报告上逐字同形。
            上一版这里就是**就地构造**的（照 ``tools.py::read_store`` 那条先例，理由
            当时写的是「本片不能改 ``__main__.py``」）。改由组合根给，是因为组合根自己
            的注释逐字判过那个形态：同一个 ``Settings.database_path`` 上的两个闭包其实
            等价，但**让它们等价这件事只应该有一个地方说** —— 两处各写一行、将来其中
            一行被改错时，「开了待办」与「待办面读不到它」会指着两个库，而两边的回执
            都正常。这一族今天**两个**消费者（本节、``followups_handlers`` 的 ``items``）
            在 ``__main__.py`` 上拿到的是**同一个**闭包。
        accounts / cohorts: 账号池与冻结成员名单的**工厂**。切片 f 多出来的两条，
            ``submit_run`` 在 ``execution_mode="watch"`` 上要它们来答「这一批主体
            从哪来」（``watch`` 的来源是到期/信号，而那句话是关于**某一批主体**说的，
            ``what_is_due`` 自己发现不了主体）。**``| None`` 而同样不给缺省值** ——
            与 ``product_loop_handlers`` 那两格同款，理由也同一句：「这个部署没有接
            冻结成员名单的存储」是一个**说得出口的答案**（``_subjects`` 照这句话拒，
            并同时给出另一条不需要这两个工厂的路），而给了缺省 ``None`` 之后
            「忘接」与「明说不接」在回执上同形 —— 前者的处置是补一行接线。
            与上面五格**都不一样**的是爆炸半径：它们缺席时坏掉的只有 ``watch`` 那条
            路上的 ``cohort`` 一支，而 ``name`` 那一支读的是一份 operator 手写的
            清单（``inputs_dir`` 下的文件）、一个工厂都不要。所以这一格**不套用**
            「不加 ``| None``」那条规矩：那条规矩的理由是「一半功能还在的部署最容易被
            读成还行」，而这里坏掉的那一半在回执上**点名说出来了**。

    工厂是惰性调用、且在这一条工具**被调用之后**才展开：``_compose`` 的契约是
    「构造服务面需要的一切，不绑端口、不建运行目录」，而 ``SqliteRunStore`` 的
    构造会 ``ensure_schema``，也就是会**建库**。
    """
    if runs is None:
        return {}

    def submit_run(
        context: Mapping[str, Any],
        cutoff: str,
        idempotency_key: str,
        budget: Mapping[str, int] | None = None,
        execution_mode: str = "manual",
        scene_id: str | None = None,
        window: Mapping[str, Any] | None = None,
        name: str = "",
        cohort: str | None = None,
    ) -> dict[str, Any]:
        """受理一次增量：写下这一轮的信封、截止与它读的是哪一份计划。

        这不是「开跑」—— 受理只写下一行 Run，不派发任何东西、不**写**预算账本。
        同一个 ``idempotency_key`` 重发拿回**同一行**（受理是幂等的）；同一个键
        配另一份内容会被拒，因为那承诺的是「重发不会受理两次」，不是「同一个键
        可以改内容」。

        ``window`` 只在 ``execution_mode="bounded_auto"`` 上出现，三格必填
        （``max_objects`` / ``budget`` / ``not_after``）。带它的那一次受理会**读**
        一次预算账本：条件 4 的第二个阈值是「这条链达累计预算」，而累计只有账本
        答得了。读不到的后果是**拒绝**（``window_refusal`` 的 fail-closed），不是
        按 0 继续 —— 按 0 算会让这条链被多放行，而报告上它读起来只是「预算还够」。

        那次读有两种读不到，而它们的处置相反，所以是两句话：**账本上没有这条链的
        上限** ⇒ 一次受理拒绝（改这次请求：补配置或改回 ``manual``）；**这条链的
        Delta 上记着一笔账本读不出来的预留** ⇒ 一次**账目事故**（``RunReconciliationError``
        —— 去查账：读错了库，还是那一行丢了）。后者的报文里点名**是哪一笔**，因为
        只有那一笔能让人去查；它需要的动作不是「改这次请求能在这一层做出来的东西」。

        **谁有权签一份窗口不在这里判**（那属于身份/授权层，B01/B02）：这一层只保证
        窗口是**显式**的 —— 它写在 ``RunSpec`` 里、进那一行的记录、读得回来。见模块
        docstring 那一节。

        ``name`` 与 ``cohort`` 只在 ``execution_mode="watch"`` 上有意义：它们指出
        **这一批主体从哪来**，而 ``watch`` 的来源（到期/信号）就是关于某一批主体说
        的 —— 没有主体，「有没有到期的」这句话没有主语。``submit_run`` 因此在这一条
        路上**收一个主体来源**，而不是自己去挑一份：一个「没给就用最近那一份」的兜底
        会让一次漏传参数变成一次静默的成功，而报告上看不出来跑的是哪一份（
        ``product_loop.py::_subject_source`` 逐字写过这件事）。

        给了它们而模式不是 ``watch`` ⇒ **拒绝**，不是忽略。两个参数在别的模式上一个
        字都不读，而一次被静默吞掉的参数会让调用方以为它约束了什么 —— 那与
        ``window`` 在 ``manual`` 上被拒是同一条理由（那一处由 ``RunSpec`` 的跨格
        校验器挡）。
        """
        preamble = _preamble(
            settings=settings, scenes=scenes, scene_id=scene_id, context=context
        )
        # ``HOLD`` 在**第二段之前**说话（``_window`` / ``_mode`` / 主体来源都在那一段
        # 里）：一份编不出计划的请求不会被报成「窗口缺了三格」。两个面共用同一个判据，
        # 而只有这一面把它读成拒绝 —— 那一句话见 :func:`_held_refusal`。
        if preamble.plan_report.outcome is PreviewOutcome.HOLD:
            raise _held_refusal(preamble.plan_report)

        try:
            this_round = preamble.round_of(
                settings=settings,
                maintenance=maintenance,
                accounts=accounts,
                cohorts=cohorts,
                budget=budget,
                cutoff=cutoff,
                idempotency_key=idempotency_key,
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
                # 信封、截止与幂等键**取自那份规格**，不在这里重算一遍：``round_of``
                # 已经用 ``_envelope`` / ``_moment`` 解析过它们，而 ``admit_run`` 内部
                # 拿同一组值调 ``spec_of`` —— 同一个生产者造的两份规格，指纹逐字相等
                # （``tests/test_run_preview.py`` 钉住这件事，preview 面读的就是它）。
                # 在这里重算一次就是那句话的第二份实现，而它漂移的形态是
                # 「preview 说这一轮是 X、提交下去的是 Y」，两边都自洽、都跑得过。
                budget=this_round.spec.budget,
                cutoff=this_round.spec.cutoff,
                idempotency_key=this_round.spec.idempotency_key,
                at=this_round.at,
                execution_mode=this_round.mode,
                window=this_round.window,
                # 账本**只在有窗口时**展开：没有窗口的受理读不到金额，那次
                # ``ledger()`` 就是一次白建的库。维护账同理，只在 ``watch`` 上展开。
                ledger=ledger() if this_round.window is not None else None,
                scene_store=preamble.store,
                maintenance=maintenance()
                if this_round.mode is ExecutionMode.WATCH
                else None,
                subjects=this_round.subjects,
            )
        except _ADMISSION_REFUSALS as exc:
            # 那一张表与那一段解释（四条结果 + pydantic 的形状拒绝）住在
            # :func:`_translate` 里，两个面共用**同一份** —— 同一个请求在
            # ``submit_run`` 与 ``preview_run`` 上必须说同一句话。
            #
            # ``round_of`` **在这一段里面**是有意的：``spec_of`` 造 `RunSpec` 时的那几条
            # 跨格校验（模式带窗口、空信封、空/带空白幂等键、窗口预算大于信封、
            # ``not_after`` 早于 ``cutoff``）抛的是 pydantic ``ValidationError`` —— 表里
            # 第四条为它写着。把它留在外面，这些**调用方写错**的回话会被 SDK 换成一句
            # 通用的 ``UnexpectedToolError``，而那正是这一层存在要避免的读法。
            # 前面那几个 ``ToolError``（模式、窗口三格、时刻、主体）既不在表里也不是
            # ``ValueError``，所以它们照旧直接从这一层出去，次序与措辞都不受影响。
            raise _translate(exc) from exc

        return _receipt(
            run, admitted_at=this_round.at, recorded=preamble.plan_report.recorded
        )

    def preview_run(
        context: Mapping[str, Any],
        cutoff: str,
        idempotency_key: str,
        budget: Mapping[str, int] | None = None,
        execution_mode: str = "manual",
        scene_id: str | None = None,
        window: Mapping[str, Any] | None = None,
        name: str = "",
        cohort: str | None = None,
    ) -> dict[str, Any]:
        """**动手之前**先看一眼：这一次会落在哪里、会复用多少、要补哪些。

        参数与 ``submit_run`` **逐格相同**，而这不是巧合：两句问的是**同一轮**，
        所以这一层只用一段前奏（:func:`_preamble` + :meth:`_Preamble.round_of`）
        去拼那份规格。漂移的形态是「preview 说这一轮是 X，提交下去的是 Y」——
        两边都跑得过、都通得过各自的判据，而只有把两次调用的规格指纹摆在一起才
        看得出来。有一格是这一条的反面：``submit_run`` 只从那份规格里**取用**它的
        解析结果，不重算（见那一处的注释）。

        它**不写任何一行**：它比 ``submit_run`` 更靠前 —— 连受理都不是。它读的是
        本地存储（场景 / 维护账 / 证据盘点 / Run 记录，有窗口时还有预算账本），
        **一次 provider 调用都不花**（``application/run_preview.py`` 的模块
        docstring 写着那件事为什么是**构造**，而不是一句自觉）。

        **``HOLD`` 在这一条工具上是 ``outcome='hold'``，不是一次拒绝** —— 那是它
        与 ``submit_run`` 唯一一处**有意**的措辞分歧：一面答「受理了吗」，一面答
        「按现在这份声明，能报告出什么」。判据是同一份（两处都调
        :func:`_plan_report`，``HOLD`` 与 ``ConfigError`` 的分法逐字同一个），
        分歧只在把它读成什么。这一支**不读任何存储**：没有计划就没有规格，也就没有
        「这一次能不能受理」与「要补哪些格子」可答。

        三条拒词（到期核验 / 这一份请求 / 这条链）由 ``budget_and_stop.verdict``
        收成五档之一，而它们的**次序就是受理面的次序**：``not_due``（只有 ``watch``
        会走到这一步，而它排在最前）→ ``request_refused`` → ``replay`` →
        ``chain_refused`` → ``open``。最要紧的一格是 ``replay`` 排在
        ``chain_refused`` **前面**：一次重发不会被链的阈值拦下，所以「这条链已经到
        stop 了」与「这次提交会被拒」不是同一句话。链那一步在前一步说了话时是
        ``None``（**没问**），不是空串（问了、过了）。

        这里**没有**「这一次会不会是重发」那个布尔，与受理回执逐字同一条理由
        （见模块 docstring）：报出来的是 ``run_id``（内容寻址的那个键）与
        ``budget_and_stop.existing``（它此刻在不在盘上），判断留给读的人。
        """
        preamble = _preamble(
            settings=settings, scenes=scenes, scene_id=scene_id, context=context
        )
        if preamble.plan_report.outcome is PreviewOutcome.HOLD:
            # 与 ``submit_run`` 同一句判词、同一处先说话，读成的却是另一件事：
            # 那边是拒绝，这边是一份报告（见 docstring）。
            return held_report(plan_report=preamble.plan_report, at=preamble.at).as_dict()

        try:
            this_round = preamble.round_of(
                settings=settings,
                maintenance=maintenance,
                accounts=accounts,
                cohorts=cohorts,
                budget=budget,
                cutoff=cutoff,
                idempotency_key=idempotency_key,
                execution_mode=execution_mode,
                window=window,
                name=name,
                cohort=cohort,
            )

            # 读数取在**那一刻**：``at`` 与 ``submit_run`` 取法逐字相同（``utc_now()``
            # 一次，``round_of`` 之前）—— 一份读数内部有三个判定用同一个时刻，而三个判定
            # 拿三个时刻的话，这份读数会自相矛盾而报告上看不出来。
            observations = maintenance().observations(workspace=this_round.spec.workspace)
            signals = maintenance().signals(workspace=this_round.spec.workspace)

            report = run_preview(
                store=runs(),
                scene=this_round.scene,
                plan_report=preamble.plan_report,
                spec=this_round.spec,
                at=this_round.at,
                observations=observations,
                evidence_store=read_store(settings),
                # 账本与维护账的展开条件与 ``submit_run`` 逐字相同：没有窗口的读数
                # 读不到金额，没有那个模式的读数不看主体。两次 ``ledger()`` 的差别
                # 是一次白建的库，而它在报告上完全看不见。
                ledger=ledger() if this_round.window is not None else None,
                signals=signals,
                subjects=this_round.subjects,
                scene_store=preamble.store,
                maintenance=maintenance()
                if this_round.mode is ExecutionMode.WATCH
                else None,
            )
        except _ADMISSION_REFUSALS as exc:
            # ``RunReconciliationError`` 在这里也是**结果**（同一个请求在两条工具上
            # 说同一句话，见 :func:`_translate`）—— 而且它在这里与在受理面上同样
            # 要紧：那是「这条链的 Delta 上记着一笔账本读不到的预留」，要人做的是
            # 去查账，不是改这次请求。
            raise _translate(exc) from exc
        except ValueError as exc:
            # 走到这里的全是**本层自相矛盾**或**接线错误**，一条都不是调用方能改的：
            # ``RunPreview`` / ``IncrementReading`` 那两组守卫（四样不同生同死、
            # 名字集合与结局对不上、一格两个结论）、``increment_cells`` 的跨工作区
            # 拒绝、以及「规格与场景不是同一版」。它们的措辞都来自库层，这一层一个字
            # 都不改写 —— 处置与 ``get_run_increment`` 那一处**逐字相同**：原样交
            # 出去，好过让 SDK 把它们换成一句通用的 ``UnexpectedToolError``
            # （一次「这份读数自己说不通」会读起来像一次内部崩溃）。
            #
            # 这一支**不是**给「调用方写错了」准备的：调用方写错的那几样
            # （模式、窗口三格、时刻、信封科目）都在 ``round_of`` 里当场拒，各自
            # 带一句指得出字段的话。它们抛的是 ``ToolError``，**不在**上面那张表里、
            # 也不是 ``ValueError`` 的子类，所以 ``round_of`` 虽然在这段 ``try`` 里面，
            # 那几句话不会绕到这里来。
            raise _translate(exc) from exc

        return report.as_dict()

    def get_run(run_id: str) -> dict[str, Any]:
        """读回一轮：那一行的**当前状态**，不是受理那一刻的快照。

        两处与受理面不同：

        * 受理面读的是**盘上的场景**（工作区与对象范围从那里来），读回面读的是
          **盘上的那一行**。所以这一条工具不需要 ``scenes`` —— 而它也不会因为
          场景今天改过就报出与那一行不同的工作区。
        * 受理面报 ``admitted_at``（**这一次调用**的时刻）；读回面报
          ``updated_at``（那一行最后一次被改的时刻）。两格答的都不是同一个问题，
          所以读回面上不出现 ``admitted_at`` —— 一次读没有「受理时刻」可言。
        """
        store = runs()
        run = _one_run(store, run_id, workspace=settings.profile)
        try:
            # 折入账**不是这一行的一列**：它在 ``run_fold`` 那张表上，按 ``run_id``
            # 索引，所以 ``_projection`` 够不着它 —— 受理回执因此没有这一格（那一份
            # 答的是「这一行被写下来了吗」，而它报 ``folds`` 只能是受理当场那份空账，
            # 一个在受理面上恒定的读数）。
            folds = [one.venue_asset_id for one in store.folds_of(run_id)]
        except RunFoldLedgerError as exc:
            # ``folds_of`` 读不出来时**必须抛**，不报一份空表：报空表就是那条假账
            # 本身（折了却没记，而回执上写着「一行都没折」）。三种状态不许塌成两种
            # —— 塌掉的那一种是这一格存在的全部理由。
            #
            # 接住它再换成 ``ToolError``，是因为**不接的话它会被 SDK 换成一句通用的
            # ``UnexpectedToolError``**：一次「账读不出来」会读起来像一次内部崩溃，
            # 而它其实是一条准确的、点名了这一轮的判定 —— 要人去做的是去查库。
            raise ToolError(str(exc)) from exc
        return {
            **_projection(run),
            "folds": folds,
            "note": (
                "这是一条**读**：它没有写任何一行、没有派发任何东西、没有碰预算"
                "账本。\n"
                "``created_at`` 是那一行写下去的时刻，``updated_at`` 是它最后一次"
                "被改的时刻 —— 两个时刻相同，表示这一轮从那以后没有动过。\n"
                "``stop_reason`` 与 ``delta`` 要么同时有、要么同时无（表的 CHECK 与 "
                "``Run`` 的校验器是同一条判据）：一轮没结束的时候，``delta`` 不是"
                "「空的产出」，而是**还没有产出**。\n"
                "``folds`` 是这一轮**真的**折进池子的那几行（池子句柄，按折入次序），"
                "不在那一行上 —— 它由写池子那一次的事务记下。所以它读不出来时这一条"
                "工具有时会失败，而那不是「一次读坏了」：一份空表会说「这一轮什么都没"
                "折」，而池子可能已经变大了。这一串可以原样交给 ``close_run`` 的 "
                "``folded``（两者是同一个地址空间）。"
            ),
        }

    def get_run_increment(run_id: str) -> dict[str, Any]:
        """这一轮**按当下的盘**读一遍：哪些复用、哪些要补新/缺/过期。

        与 ``get_run`` 的结构性差别写在模块 docstring 上：那一条读的是盘上那一行，
        这一条读的是**现算的一份**。两处后果落在这里：

        * 它要 ``scenes``（复核计划是场景生效版本的一格）与 ``maintenance``
          （观测与变更信号），而 ``get_run`` 两样都不要。
        * 它报 ``at`` 而**不报** ``updated_at``：前者是这一份读数取在什么时候，
          后者是那一行最后一次被改的时刻 —— 一次现算没有改动任何一行。

        **``plan_increment`` 里那条跨工作区守卫在这一层到不了**，这不代表它多余：
        它挡的是「把别的观测读成这一轮的复用」，而这里的观测来自
        ``maintenance().observations(workspace=...)`` —— 工作区是**查询条件**，
        不是事后比对。两处各挡一次不冲突（一处是 SQL 的 WHERE，一处是纯函数的
        判据，而那一支的调用方不止这一个）。本层因此不为它造判据：造不出来，
        而一条判不出事的判据在报告上会长得像「这条守卫是活的」。
        """
        run = _one_run(runs(), run_id, workspace=settings.profile)
        # ``scene_id=None`` 是**冻结**的，而这一条与本模块别的入参同一条纪律：
        # ``require_scene`` 只放行 ``settings.profile`` 一个名字，所以一个
        # ``scene_id=`` 参数只有一个合法取值 —— 收下它等于让调用方以为可以指另一个
        # 场景，而那个念头在受理面上已经被拒了。复核计划必须来自**这一轮受理时**
        # 那一份，而今天两处都恒等于本部署的那一个。
        _, _, version, _ = _plan_context(settings=settings, scenes=scenes, scene_id=None)
        if version is None:
            raise ToolError(
                f"Scene {settings.profile!r} 此刻没有生效版本，所以这一轮算不出来。\n"
                "  这一轮**受理过**（受理本来就要求有一份生效版本），所以这不是"
                "「这个市场还没开张」—— 那一句是受理那一侧的话。此刻读不出生效版本，"
                "意思是这个市场被关上了（归档，或记录被改回了 DRAFT）。\n"
                "  复核计划是生效版本的一格：没有它，就没有判「该不该重取」的依据。"
                "而拿一份保守值顶上的计划去算，会让这一份读数看起来是按那份计划算的"
                "—— 一次没有依据的计算就这样进了记录。\n"
                "  请先看这一份 Scene 为什么被关上了（``list_scenes`` / "
                "``get_scene``），而不是把它重新激活再读一次。"
            )
        observations = maintenance().observations(workspace=run.spec.workspace)
        signals = maintenance().signals(workspace=run.spec.workspace)
        at = utc_now()
        try:
            increment = plan_increment(
                run=run,
                plan=version.refresh,
                observations=observations,
                evidence_store=read_store(settings),
                now=at,
                signals=signals,
            )
        except RunStateError as exc:
            # 这一轮已经结束了。这是**结果**（处置是重读这一轮现在到哪一步了），
            # 不是崩溃 —— 与 ``submit_run`` 接住它时逐字同一个理由。
            raise ToolError(str(exc)) from exc
        except ValueError as exc:
            # 两条同住一个名字底下的东西在这里合成一条回执，是**有意的**：
            #
            # * ``plan_increment`` 自己的跨工作区拒绝（裸 ``ValueError``）；
            # * ``RunIncrement`` 拼不出来（``ValidationError``，它是 ``ValueError``
            #   的子类）—— 那是**本层**的自相矛盾（域层给不出一份自洽的读数），
            #   不是调用方能改的东西，所以与上一条同样处置：原样交出去，好过让它
            #   被 SDK 换成一句通用的 ``UnexpectedToolError``。
            #
            # 两条的措辞都来自域层，这一层一个字都不改写。
            raise ToolError(str(exc)) from exc

        return {
            **increment.model_dump(mode="json"),
            # ``counts`` 与 ``cells`` 不是同一件事：``cells`` 是逐格的处置，
            # ``counts`` 是四档各几格、**零也出现**（``RunIncrement.counts`` 的
            # docstring 逐字）。不给它，一档缺席时调用方从 ``cells`` 里读出来的
            # 是「这一档没有」与「这一档是零」的同一句话 —— 而它们是两件事。
            "counts": {
                verdict.value: count for verdict, count in increment.counts.items()
            },
            # 喂进去的两样东西各多少条。这是本片对「零格」那件事的处置：格子空间
            # 由已沉淀的观测决定，所以一份零格的读数必须与「喂进去的是零条」一起读，
            # 否则它会像「这一轮没有事要做」。报的是**输入规模**而不是域层的判断，
            # 所以它不是第二处判定。
            "inputs": {
                "observations": len(observations),
                "signals": len(signals),
            },
            "note": (
                "这是一份**按当下的盘现算**的读数，不是盘上那一行的快照：它的输入"
                "（复核计划、观测、变更信号）没有一格存在 Run 那一行上。同一个 Run "
                "过一会儿再读，可能得到一份不同的读数 —— 而这不是缓存失效。\n"
                "``cells`` 里每一格是**一个 (主体, 面) 的处置**，四档各自对应一种"
                "做法（复用 / 第一次去看 / 去找一条能当依据的来源 / 重取）；"
                "``counts`` 四档都在，缺的那一档是 0。\n"
                "格子空间是「**已沉淀的观测**里出现过的那些主体」—— 这一条工具不接"
                "主体来源（那是 ``get_maintenance_plan`` 的 ``cohort`` / ``name``），"
                "所以 ``cells`` 为空时请与 ``inputs.observations`` 一起读：零格配零观测"
                "是「这里还没有任何沉淀下来的事实可复用」，**不是**「这一轮没有事要做」。"
                "\n"
                "它不判成本与上限、不判上一轮记下的观测是不是真的、也不写任何一行 —— "
                "结束那一轮的产出（Delta）在 ``get_run`` 上，而这一条读的是开工前。"
            ),
        }

    def close_run(
        run_id: str,
        delta: Mapping[str, Any],
        status: str,
        folded: list[str] | None = None,
    ) -> dict[str, Any]:
        """结束这一轮：交一份产出与一个结局，**账平了才停表**。

        §B17 条件 2 逐字是「Run 结束有完整 Delta，业务键双向对账」。这一条工具就是
        那句话的落地，而它做的三件事各自有一处能红的地方：

        * **交付**（``delta``）：这一轮产出了什么，九格（处置 / 路线 / 证据 / 产物 /
          复核 / 待办 / 用量 / 未结 / 建议）。``completed`` 配一份**每一格都空**的
          Delta 会被拒（``ending_refusal``，住在 ``ports/runs.py``）——「这一轮走完了、
          什么都没变」是一批 ``unchanged`` / ``held`` / ``blocked`` 的处置行，它与
          「压根没跑」必须在报告上分得开。``actions`` 那一格**不用交**：它由收盘这一趟
          写回来（这一轮产出的路线要有人去核实转介联系人，那些活儿开成
          ``ActionItem``）—— 一份从 ``get_run`` 读回来的 Delta 带着它，所以它交回来时
          会被原样收下并逐条核对。
        * **结局**（``status``）：``completed`` / ``partial`` / ``failed``。这一格由
          **做事的这一方**给，服务端没有这份答案可以替它说话 —— 而「谁有权宣布一轮做
          完了」是身份/授权层的裁定（B01/B02），与 ``window`` 那一处是同一条记在册的
          缺口。
        * **对账**（``folded`` ＋ 盘上的池子）：
          ``application/run_reconciliation.py::close_run`` 的判据。第一道比的是这一轮
          **自己的折入账**（``RunStore.folds_of``）与这份主张；第二道比的是这份主张与
          Delta；第三道问的是 ``actions`` 里那几个 id 在跟进池里读不读得回来。
          **都过才停表**，否则这一轮留在 running、盘上一个字节都没动（唯一落在停表
          之后的拒绝是「这一轮已经以另一套产出结束过」，而那一条见那个函数的 docstring）。
        * **派活**：``unaccounted`` 之外，这一条工具还会**写**一行 —— 这一轮产出的
          每一条路线开一件「核实转介联系人」的待办（``domain/action_item.py``）。同一主体
          上的第二条被那条 partial unique index 挡住，所以重发同一份产出不会让待办变多，
          拿回来的还是最早那一条的 id。

        ## 为什么 ``folded`` 是一个入参，而它又不是一份「凭据」

        折入是**这一轮跑的过程中**发生的，而那些行由那时写池子的代码产生 —— 本层拿不到
        它们（它手里只有这一次调用的载荷）。所以调用方把「哪些行落了盘」作为一份**主张**
        交上来，而它此后被账核。这与「服务端替它猜一个」是两件事：猜出来的主张在账那一
        侧永远对得上（两边都是猜的），而这一道闸就白设了。

        ``get_run`` 的 ``folds`` 报的正是这一格要的那一串，所以正常的用法是**读回来、
        原样交回去** —— 但交回来的是不是真的，由账判，不由这一串是不是从 ``get_run``
        来的判。

        ## 它**不是**「结束」的第二个入口

        ``RunStore.stop`` 照旧是唯一的写路径，这一条只是给它加了两道前置判据。所以同一
        套结局与产出重放一次拿回**同一行**（``stopped_at`` 不重新取），而换一种结局结束
        同一轮会被拒 —— 那条判据住在存储层，两个实现各调同一份
        （``ports/runs.py::stopping_outcome``）。

        Raises:
            ToolError: 上面每一处的拒绝都原样交出去，逐条点名是哪几条键、属于哪一个
                方向。``RunFoldLedgerError`` 那一句最要紧：账读不出来时**不按零折入
                读完** —— 报一份空账会让这一轮照常停表，而那一份 ``completed`` 在盘上
                与「真的什么都没折」逐字相同。
        """
        store = runs()
        # 两条守卫先走：未知的 id 与跨工作区的行在这一层就拒（两条拒词逐字来自
        # ``ports/runs.py`` 与 ``_one_run``），不进应用层。它们与「账对不上」的处置
        # 相反 —— 前者要换 id 或换一本账，后者要看账 —— 所以不能混成一句话。
        _one_run(store, run_id, workspace=settings.profile)
        at = utc_now()
        try:
            stopped = run_reconciliation.close_run(
                runs=store,
                assets=assets(),
                items=followups(),
                run_id=run_id,
                delta=_delta(delta),
                status=_status(status),
                folded=_claim(folded),
                at=at,
            )
        except (
            RunFoldLedgerError,
            RunReconciliationError,
            RunSpecError,
            RunStateError,
        ) as exc:
            # 四条都是**结果**，处置各不相同，而四条都要**逐字**交出去：
            #
            # * ``RunReconciliationError`` —— 两道闸之一没开。报文里逐键点名是哪几条、
            #   属于哪一个方向（补处置 / 改处置 / 去看为什么没落盘）。
            # * ``RunFoldLedgerError`` —— 账读不出来。**不按零折入读**，也不重试。
            # * ``RunSpecError`` —— ``ending_refusal``（``completed`` 配空产出）。
            # * ``RunStateError`` —— 这一轮已经结束过，而这次带上来的不是先前那一套。
            #
            # 不接住它们，调用方读到的是一句光秃秃的 ``Error executing tool close_run``，
            # 而 ``ports`` 与 ``application`` 两层原本各给了一句很准的话。
            raise ToolError(str(exc)) from exc
        return {
            **_projection(stopped),
            "note": (
                "这一轮**结束了**：``stop_reason`` / ``stopped_at`` / ``delta`` 三格"
                "一起落下（表的 CHECK 与 ``Run`` 的校验器是同一条判据）。\n"
                "它在结束之前核过两遍账：这一轮自己的折入账与 ``folded`` 对了一遍，"
                "``folded`` 与这份 Delta 又对了一遍 —— 两遍都平才停表。所以这一次"
                "返回成功，意思是「池子变大了而报告上说没变」这句话在这一轮上**不成"
                "立**，不是「这次调用没有报错」。\n"
                "重发同一份产出与结局拿回的是**同一行**（``stopped_at`` 不重新取）；"
                "换一种结局结束同一轮会被拒 —— 「同一轮以两种结局结束过」在存储层是"
                "看得出来的。\n"
                "这一份回执**不报** ``folds``：它是盘上另一张表的一格，而值它的那一条"
                "工具是 ``get_run``（两份各报一次，先漂的是「哪一串才算折进去了」，而"
                "两边都声称自己报的是同一件事）。"
            ),
        }

    return {
        "submit_run": submit_run,
        "get_run": get_run,
        "get_run_increment": get_run_increment,
        "close_run": close_run,
        "preview_run": preview_run,
    }


# ----------------------------------------------------------------------
# 受理面那几条**结果** → 一句话（两个面共用）
# ----------------------------------------------------------------------
#: 受理与预览都要把它们当**结果**交给调用方的那几样：四条来自域/端口层的拒绝，
#: 加一条 pydantic 在**拼**一格东西时给的形状拒绝（它是 ``ValueError`` 的子类，
#: 不是 ``RunSpecError`` —— 后者是判定，前者是形状，处置不同）。见 :func:`_translate`。
_ADMISSION_REFUSALS: tuple[type[BaseException], ...] = (
    RunSpecError,
    IdempotencyConflictError,
    RunStateError,
    RunReconciliationError,
    ValidationError,
)


def _translate(exc: BaseException) -> ToolError:
    """一条域层拒词 → 一条 ``ToolError``。**措辞一个字都不改写。**

    两个面共用这一份的后果具体：同一个请求在 ``submit_run`` 与 ``preview_run`` 上
    必须**说同一句话**。两处各接一次异常的那天，漂移的形态是「preview 说得比受理面
    好听」—— 与 ``application/plan_preview.py`` 的模块 docstring 逐字拒过的那件事
    同款（那里拒的是「对同一份判据的两处读法」）。

    逐条的理由，四条都是「调用方要据它改东西，而在这一层只是**传输**」：

    * ``RunSpecError`` —— ``_require_due`` / ``admission_refusal`` / ``window_refusal``
      给的判定（这一批主体上没有到期的 / 这个模式今天受理不了 / 截止时刻不在将来 /
      这条链已经到了一条 stop）。四条里没有一条是崩溃。
    * ``IdempotencyConflictError`` —— 同一个键配了另一份规格。要改的是**键**或者内容。
    * ``RunStateError`` —— 这一轮已经结束了之类的状态拒绝。
    * ``RunReconciliationError`` —— **处置与上面三条相反**：这条链的 Delta 上记着一笔
      账本读不到的预留，要人做的是去**查账**（读错了库，还是那一行丢了），不是改这次
      请求。而它恰恰最不能丢措辞：不接住它，``spent_of`` 那句点名了**是哪一笔**的话
      会被压成一句「受理失败」，于是一次账目事故在回执上读起来像一次正常的拒绝 ——
      而两者的下一步动作相反。它是 ``RunError`` 而不是 ``RunSpecError`` 那一支，
      所以上面那个元组里必须写全，不能靠继承搭便车。

    ``ValidationError`` 是另一支：``str`` 会把调用方给的那段 JSON 整个回显回去，
    所以走 :func:`_readable` 摊成「哪一个字段、为什么」—— 字段名是调用方唯一能据以
    动手的东西。
    """
    if isinstance(exc, ValidationError):
        return ToolError("这一份请求拼不成一轮 Run：\n" + _readable(exc))
    return ToolError(str(exc))


# ----------------------------------------------------------------------
# 两个面共用的那段前奏
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class _Preamble:
    """**第一段**：这一次对着哪一份声明、哪一版场景，计划编出来没有。

    它停在这里而不是一路做到底，理由是**次序**（见 :meth:`round_of` 与 :func:`_preamble`）：
    ``HOLD`` 必须在 ``_window`` / ``_mode`` / 主体来源之前说话。把两段合成一段的
    后果不是「错」，是两份**都真**的话里先说了次要的那一句 —— 一份编不出计划的请求
    会被报成「窗口缺了三格」。

    四个字段**都是这一层从盘上读来的**，没有一格来自调用方：``scene`` 与
    ``scene_id`` 来自 ``_plan_context``（工作区与对象范围的第一段），``declared``
    是从调用方那份 ``context`` 编译出来的声明，``at`` 由服务端取（见模块 docstring
    那一节）。``plan_report`` 是**计划层**的读数 —— 它在 ``HOLD`` 上仍然是一份完整
    的读数（``held`` 逐条点名），这是下面那个分支能说得出话的原因。
    """

    #: 场景存储的**同一份**句柄。``watch`` 的到期核验要它，而 ``_plan_context``
    #: 已经建好它了 —— 再建一份的后果与 ``_Wired`` 那段逐字相同：两份指向同一个库时
    #: 看着一样，指向两个库时两处各自都正常。
    store: SceneStore
    #: 盘上那一版生效的 Scene。
    scene: SceneVersion
    #: 它叫什么（``_plan_context`` 解析出来的那一个名字）。
    scene_id: str
    #: 调用方那份 ``context`` 编译出来的声明。
    declared: ResearchContext
    #: 这一份读数取在什么时候。**一次调用里只有一个**，两个面都用它。
    at: datetime
    #: 计划层的读数。``HOLD`` 时它里面没有计划 —— 那不是一个错误，是这次要报告的事。
    plan_report: PlanPreview

    def round_of(
        self,
        *,
        settings: Settings,
        maintenance: Callable[[], MaintenanceStore],
        accounts: Callable[[], AccountPoolStore] | None,
        cohorts: Callable[[], AccountCohortStore] | None,
        budget: Mapping[str, int] | None,
        cutoff: str,
        idempotency_key: str,
        execution_mode: str,
        window: Mapping[str, Any] | None,
        name: str,
        cohort: str | None,
    ) -> _Round:
        """**第二段**：模式、窗口、主体，直到那份规格（``spec_of``）。

        它**只管拼**，不写盘、不读 Run 记录、不碰账本 —— 那些是各自的调用点按自己的
        条件展开的（``submit_run`` 里受理，``preview_run`` 里读一遍）。这一段的产出
        就是「这一次是哪一轮」的那个答案，两个面据它说的必须是**同一轮**。

        次序**照抄切片 g 之前** ``submit_run`` 的那一段（窗口 → 模式 → 主体 → 信封与
        截止），一处也没挪：三处拒绝各有各的话，而次序决定了同时不成立时先说哪一句。

        调用它之前必须先确认 ``plan_report.outcome`` 不是 ``HOLD`` —— 这一支要一份
        计划才拼得出规格，而 ``HOLD`` 时那一格是 ``None``。那一条不在这里判：判在
        **调用点**，因为两个面把它读成两件不同的事（拒绝 / 报告）。
        """
        plan = self.plan_report.plan
        if plan is None:  # pragma: no cover - 两支都在调用点挡过，这里是第二道
            raise ToolError(
                "这段前奏要一份编出来的计划，而这一份读数里没有。\n"
                "  两个调用点都在 HOLD 上先分了支（``submit_run`` 拒绝、``preview_run`` "
                "直接报告），所以走到这里只可能是有人**新加了一个调用点**而漏了那一支。"
            )

        # 窗口在这一层拼（参数是那份映射）。
        granted = _window(window)
        mode = _mode(execution_mode)

        # 主体来源**只在 watch 上**展开，与上面那一行同一条纪律：别的模式一个字都不
        # 读它们，而每一次调用都去读一份 operator 手写的清单、再开一个维护库，是三条
        # 400 与一次白建的库。
        #
        # 「两个都没给」这一格**不在这里判**：``_subject_source`` 已经说过那句话
        # （逐字：「这一批的主体从哪来没有答案」），在这里再写一遍就是同一句话的
        # 第二份实现。这里只把**为什么 watch 需要一个主体来源**写在受理面那段
        # docstring 里 —— 判断留给那一处，解释留在这里。
        subjects: tuple[tuple[str, str], ...] = ()
        if mode is ExecutionMode.WATCH:
            subjects = _subjects(
                settings,
                source=_subject_source(name, cohort, label="name"),
                scene_id=self.scene_id,
                pool=accounts,
                cohorts=cohorts,
            )
        elif name or cohort is not None:
            # 反过来那一半：给了主体来源而模式不是 watch。**拒，不忽略** ——
            # 两个参数在别的模式上一个字都不读，而一次被静默吞掉的参数会让调用方
            # 以为它约束了什么（它约束的对象在这两格里根本不存在）。与 ``window``
            # 在 ``manual`` 上被拒是同一条理由，那一处由 ``RunSpec`` 的跨格校验器挡。
            raise ToolError(
                f"``{'name' if name else 'cohort'}`` 只在 ``execution_mode='watch'`` "
                f"上有意义，而这一次的模式是 {mode.value!r}。\n"
                "  这两格指出的是 watch 那批主体的来源 —— 它的来源是到期/信号。"
                f"{mode.value!r} 的受理不看主体：``manual`` 说的是「这一轮跑完就停在"
                "那儿」，``bounded_auto`` 说的是「这条授权链自己往下走」，两者的范围"
                "都由 Scene 与窗口定。\n"
                "  处置是把模式改成 'watch'，或者把这个参数去掉。"
            )

        return _Round(
            preamble=self,
            mode=mode,
            window=granted,
            subjects=subjects,
            # ``spec_of`` 是 :class:`~marketops.domain.run.RunSpec` 的**唯一**生产者，
            # 而受理面内部也调它 —— 两处拿同一组值调同一个函数，所以两份规格的指纹
            # 逐字相等。这也是这一格必须在**两段之间**的原因：``_envelope`` /
            # ``_moment`` 的拒绝要发生在窗口与模式之后、受理之前（切片 g 之前它们就在
            # 那个位置 —— ``admit_run`` 的实参表里）。
            spec=spec_of(
                scene=self.scene,
                context=self.declared,
                plan=plan,
                budget=_envelope(budget),
                cutoff=_moment(cutoff, field="cutoff", meaning=_CUTOFF_MEANING),
                idempotency_key=idempotency_key,
                execution_mode=mode,
                window=granted,
            ),
        )


@dataclass(frozen=True)
class _Round:
    """**这一次是哪一轮** —— 两个面共用的那一个答案。

    五格全是拼出来的、没有一格是从盘上新读的（``spec`` 里那几格除外），所以两个面
    在同一个入参下拿到的是**逐字相同**的一份。判据钉在 ``tests/test_run_preview.py``：
    两个面拼出来的 ``spec_fingerprint`` 相等，而且那个 ``at`` 是同一个值。
    """

    preamble: _Preamble
    #: 谁授权跑起来的（``_mode`` 解析过的那一档）。
    mode: ExecutionMode
    #: 这条自动授权链的范围。``manual`` / ``watch`` 上是 ``None``。
    window: AuthorizationWindow | None
    #: 这一批主体（只有 ``watch`` 非空）。
    subjects: tuple[tuple[str, str], ...]
    #: 这一次会写下的那份规格。**``spec_of`` 造的**，不是这一层拼的。
    spec: RunSpec

    # 下面五个属性往第一段**转发**，不是第二份数据 —— 两个面据它读同一份读数，
    # 而「同一个时刻」「同一份声明」在这两处必须真的是同一个对象。
    @property
    def store(self) -> SceneStore:
        return self.preamble.store

    @property
    def scene(self) -> SceneVersion:
        return self.preamble.scene

    @property
    def declared(self) -> ResearchContext:
        return self.preamble.declared

    @property
    def at(self) -> datetime:
        return self.preamble.at

    @property
    def plan(self) -> EffectiveResearchPlan:
        """编出来的那一份计划。``round_of`` 已经确认过它在，所以这里不再 ``assert``。"""
        plan = self.preamble.plan_report.plan
        assert plan is not None  # round_of 的第一道守卫保证
        return plan


def _preamble(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    scene_id: str | None,
    context: Mapping[str, Any],
) -> _Preamble:
    """两个面共用的**第一段**前奏。见 :class:`_Preamble` 与模块 docstring。

    ``version is None``（这个市场还没开张）在这一层拒 —— 两个面都要一份生效版本：
    受理面拿它取工作区与对象范围，preview 面拿它编译计划。一份 DRAFT 记录**受理不了
    任何东西**，也就没有任何一轮可以用来 preview。
    """
    store, _, version, chosen = _plan_context(
        settings=settings, scenes=scenes, scene_id=scene_id
    )
    if version is None:
        raise ToolError(
            f"Scene {chosen!r} 还没有生效版本 —— 这个市场还没开张。\n"
            "  Run 的工作区与对象范围取自**生效的那一版**（授权链的第一段），"
            "所以一份 DRAFT 记录受理不了任何东西。这不是存储故障：\n"
            "  处置是去把这一版立项并激活（``create_scene`` / ``control_scene``），"
            "而「生效版本读不出来」是另一句话（那时要去查存储）。"
        )

    # ``_declaration`` **只调一次**：它产出的那份声明既是计划的输入（``_plan_report``
    # 拿它编译）也是规格的输入（``round_of`` 的 ``spec_of``）。调两次的后果不是「两份
    # 不一样」（同一份入参编译出来的声明逐字相同），而是两处拒绝里先说的那一句取决于
    # 求值次序 —— 一个读的人会去改的地方因此不是坏掉的地方。
    declared = _declaration(context)

    return _Preamble(
        store=store,
        scene=version,
        scene_id=chosen,
        declared=declared,
        # 时刻由服务端取，**一次调用只有一个**：到期核验、截止判定与格子读数都用它，
        # 而三个判定拿三个时刻的读数会自相矛盾且报告上看不出来。
        at=utc_now(),
        plan_report=_plan_report(settings, declared, chosen),
    )


# ----------------------------------------------------------------------
# 入参 → 域对象
# ----------------------------------------------------------------------
def _declaration(context: Mapping[str, Any]) -> ResearchContext:
    """收下一份研究声明，或一条指得出字段的拒绝。

    与 ``_create_scene`` 收 ``budget`` / ``outreach`` 同一条纪律：域模型自己会拒
    （``geo`` 不是两位国家码、目标为空、候选说不出来源），而 ``str(ValidationError)``
    会把调用方给的那段 JSON 整个回显回去 —— ``_readable`` 把它摊成「哪一个字段、
    为什么不行」。
    """
    if not isinstance(context, Mapping):
        raise ToolError(
            f"context 是 {type(context).__name__}，而它必须是一份声明（一个对象）。\n"
            "  一个形状不对的声明在这里能被读成一份空声明，而空声明是硬歧义"
            "（场景那一格没人定）—— 于是「调用方把参数写错了」会被报告成"
            "「这份需求本身说不清」。"
        )
    try:
        return ResearchContext(**context)
    except ValidationError as exc:
        raise ToolError(
            "这份研究声明构造不出来：\n"
            f"{_readable(exc)}\n"
            "  每一格说的都是「这一次要研究什么」，而下游每一处判定都从它推。"
        ) from exc


def _plan_report(settings: Settings, context: ResearchContext, chosen: str) -> PlanPreview:
    """现编一份计划；**越界**变成拒绝，而**硬歧义**原样交回去（切片 g 拆的）。

    ``ConfigError`` 在这里被接住而不是放它逃出去：它答的是「这份请求落在批准范围
    之外」（治理问题）或者「某一格没有人写」（部署问题），两条都是调用方要读到、
    要据它改东西的**结果**。让它抛出去，那一句话会被 SDK 换成一句通用的
    ``UnexpectedToolError``，而真实原因一个字都留不下来。

    ``HOLD`` **不在这一层判**（切片 g 之前它在这里抛）。两个面要的是同一句判词、
    **不同的一句话**：``submit_run`` 把它读成拒绝（:func:`_held_refusal`），
    ``preview_run`` 把它读成一份 ``outcome='hold'`` 的报告。把拒绝留在这里的后果
    不是「两个面不一致」，比那个更细：一份编不出计划的 preview 会**抛**，而 §253
    要的正是「先报告」—— 那时调用方拿到的是一个错误，而不是一份写着「这三项答不了、
    因为计划没编出来」的读数。

    两处**都不重判** ``HOLD`` 与 ``ConfigError`` 的分法：一个是 ``outcome`` 那一格，
    一个是抛出来的 ``ConfigError``，这一层只决定把它们读成什么。
    """
    try:
        return preview_plan(require_bundle(settings), context)
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc


def _held_refusal(preview: PlanPreview) -> ToolError:
    """硬歧义 → **受理面**的那条拒绝。措辞与切片 g 之前逐字相同。

    它不再由 :func:`_plan_report` 抛出，只因为它现在有**两个**读者：受理面把它读成
    拒绝（这一条），而 preview 面把它读成报告（``preview_run`` 那一支）。判词没有
    分成两份 —— ``held`` 那几条逐条点名，两处用的是同一份。
    """
    listed = "\n".join(f"  - {item.dimension.value}: {item.detail}" for item in preview.held)
    return ToolError(
        "这份声明里有几格说不清楚，所以**没有编计划、也没有受理**：\n"
        f"{listed}\n"
        "  这几条是硬歧义：编得出来的那一份是拿保守值顶上的，而它此后会被读成"
        "「这一轮按那份计划跑了」—— 一个没人做过的决定就这样进了记录。\n"
        "  请先把上面那几格答清楚再提交。这一次什么都没写。"
    )


def _mode(declared: str) -> ExecutionMode:
    """``manual`` / ``bounded_auto`` / ``watch``，其余一律拒并报出闭集。

    缺省是 ``manual``，而这一格**就是**切片 e 那句「``manual`` 默认」在服务面上的
    落地：一个能省略的模式，省掉之后的含义必须是**最保守的那个**。这一层不替
    ``ports/runs.py`` 判「这个模式今天走不走得通」—— 那些拒绝住在那里，两个实现都
    调同一份。三个模式今天都受理得下来，而各自有各自的前提：``manual`` 不需要任何
    东西，``bounded_auto`` **要带一个窗口**，``watch`` **要有一条到期的格子**（它的
    来源是到期/信号，那一条判在 ``application/run_admission.py::_require_due``）。
    """
    try:
        return ExecutionMode((declared or "").strip())
    except ValueError as exc:
        known = [member.value for member in ExecutionMode]
        raise ToolError(
            f"不认识的执行模式 {declared!r}。本部署的模式是 {known}。\n"
            "  模式说的是「谁在什么时候决定下一轮」，不是速度、也不是批量大小 ——"
            " 一个能省略而含义不明的取值，会让「这一次是有人提交的」在记录上"
            "没有答案。"
        ) from exc


#: 两个时刻各自答的那一问。逐字进拒词，所以它们住在这里而不是散在三个分支里 ——
#: 同一个时刻在两处被问到、而两处说它是同一件事，是这两句话最容易出的错。
_CUTOFF_MEANING = "这一轮跑到什么时候为止"
_WINDOW_MEANING = "这条链最迟到什么时候"


def _moment(text: str, *, field: str, meaning: str) -> datetime:
    """收下一个 ISO-8601 时刻。带时区是**要求**。

    naive 不得进入本系统（``to_utc`` 逐字）。一个省掉时区的时刻在这里能被
    ``fromisoformat`` 解析出来，却会按服务所在机器的本地时区解释 —— 于是
    「这个时刻是几点」取决于这个服务跑在哪台机器上。

    ``field`` 与 ``meaning`` 由调用方给，因为这一层今天有**两个**时刻入参，而它们
    答的是两件事：``cutoff`` 是这一轮的运行截止，``window.not_after`` 是整条授权链
    的截止。一个把这句写成 ``"cutoff ..."`` 的共用函数，会在 ``not_after`` 写错时
    回一句点着 ``cutoff`` 的报错 —— 而调用方会去改那个字段，改完还是错。
    """
    value = (text or "").strip()
    if not value:
        raise ToolError(
            f"{field} 不能为空。它答的是「{meaning}」—— 空着的时候，"
            "这条阈值永远不触发，而在报告上它与「时间还很充裕」长得一样。"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(
            f"{field} 是 {value!r}，不是一个 ISO-8601 时刻"
            "（例如 2026-09-29T09:00:00+00:00）。"
        ) from None
    try:
        return to_utc(parsed)
    except ValueError as exc:
        raise ToolError(
            f"{field} 是 {value!r}，它没有带时区：{exc}\n"
            f"  一个省掉时区的时刻会被按服务所在机器的本地时区解释，于是「{meaning}」"
            "取决于这个服务跑在哪里。"
        ) from exc


def _envelope(declared: Mapping[str, int] | None) -> tuple[RunBudgetLine, ...]:
    """``{"search": 20, ...}`` → 这一次的信封。

    与 ``product_loop._budget_lines`` 同形，但产出 ``RunBudgetLine`` 而不是
    ``SceneBudgetLine`` —— 那两个类型说的是两件事（「这个场景允许用多少」与
    「这一次允许用多少」），合成一个会让回执答不出「这次超支是场景上限还是本次
    信封」，所以这里不为了少写十行去共用一个。

    ``None``（没给）走 ``RunSpec`` 的 ``budget`` 校验器：**没有上限不是「随便用」**，
    §B17 条件 4 的「达累计预算时 stop」在一条线都没有的信封上永远不触发。
    """
    if declared is None:
        return ()
    known = sorted(line.value for line in BudgetLine)
    lines: list[RunBudgetLine] = []
    for name, ceiling in declared.items():
        try:
            line = BudgetLine(name)
        except ValueError as exc:
            raise ToolError(
                f"不认识的预算科目 {name!r}。本部署的科目是 {known}。\n"
                "  科目是一个闭集：写错一个名字的结果必须是**这一次调用被拒**，"
                "而不是那条额度被静默丢掉 —— 丢掉的额度在报告上读起来像"
                "「这个科目没花钱」。"
            ) from exc
        lines.append(RunBudgetLine(line=line, ceiling=ceiling))
    return tuple(sorted(lines, key=lambda item: item.line.value))


#: 窗口那三格的名字 —— 拒词的清单与构造用的是同一份，不是两处字面量。
_WINDOW_FIELDS = ("max_objects", "budget", "not_after")


def _status(declared: str) -> RunStatus:
    """``completed`` / ``partial`` / ``failed``，其余一律拒并报出闭集。

    与 :func:`_mode` 逐字同款，而两者都是**闭集**入参：这三个词说的是「这一轮算成了
    什么」，一个不在集合里的词如果被静默接受，它此后会被读成一个新的结局 —— 而报告
    上那句与三个真结局长得一样。报出闭集是因为调用方要据此改，而不是猜。
    """
    try:
        return RunStatus((declared or "").strip())
    except ValueError as exc:
        known = [member.value for member in RunStatus]
        raise ToolError(
            f"不认识的结束状态 {declared!r}。本部署的结局是 {known}。\n"
            "  结局说的是**这一轮算成了什么**（做完了 / 做了一半 / 没做成），不是它"
            "要多快、也不是它产出了几行 —— 而一个不在集合里的词此后会被读成一个新的"
            "结局，在报告上它与三个真结局逐字同形。"
        ) from exc


def _claim(declared: list[str] | None) -> tuple[str, ...]:
    """``folded``：要么不给，要么是**一串非空字符串**。

    ``None`` ⇒ ``()``，而那是「这一轮一行都没折」这份**主张** —— 它照旧要过账那一关
    （真的零折入才过得去）。所以这里不把 ``None`` 读成「不知道」：不知道的时候调用方
    应当先去 ``get_run`` 读一次 ``folds``，而不是交一份空主张碰运气。

    三种形状各自都会静默算出一个错的读数，所以三种都要单独拒：

    * **一个裸字符串** —— 它是可迭代的，于是 ``sorted(set("src-…"))`` 会把句柄拆成一个个
      字符。这是最容易漏的一种：它不抛、不空，只是把一个句柄读成了十六个。
    * **一个空串** —— 它会变成一个**永远对不上**的句柄，于是一次正常的结束被报成
      「主张多报了一条」，而调用方拿这个回执去改的地方是错的。
    * **一个非字符串** —— 它进不了那个地址空间（句柄是 ``venue_asset_id``，一个字符
      串），而 ``sorted`` 对异构的表会抛 ``TypeError``：一次请求的形状错误会长成一次
      崩溃。

    ## 三条里只有一条在服务面上有执行器（实测）

    ``folded`` 那一格的出参 schema 是 ``list[str] | None``，于是 ``[None]`` / ``[1]`` /
    裸字符串在进到本函数体**之前**就被框架拒了（``folded.0`` 报 ``Input should be a
    valid string``，整格报 ``Input should be a valid list``）。真正走进下面那两支的只有
    **按值**的那条路：``[""]`` 与 ``[V1, ""]`` 都是合法的 ``list[str]``。与
    :func:`_window` 那一处同一条 —— 按类型的闸是给**库调用方**的第二道，服务面判据
    落在框架那句拒词上。

    这一层**不**做归一化（升序、去重）：那是 ``application/run_reconciliation.py::
    fold_claim`` 的事，而它是主张的唯一规范化点。两处各归一化一次，会让「账与主张对不
    上」在一个读法上成立、在另一个读法上不成立。
    """
    if declared is None:
        return ()
    if isinstance(declared, str) or not isinstance(declared, (list, tuple)):
        raise ToolError(
            f"folded 是 {type(declared).__name__}，而它要的是一串池子句柄"
            "（例如 [\"src-0123456789abcdef\"]）。\n"
            "  一个裸字符串是可迭代的，于是它会被**逐字符**读成一串句柄 —— 不抛、不空，"
            "只是把一条句柄读成了十六条。"
        )
    for index, handle in enumerate(declared):
        if not isinstance(handle, str) or not handle or handle != handle.strip():
            raise ToolError(
                f"folded 第 {index} 项是 {handle!r}，而每一项都要是一个非空、首尾不带"
                "空白的池子句柄。\n"
                "  它是 ``venue_asset_id``（``get_run`` 的 ``folds`` 报的就是这一串），"
                "而一个空串此后会变成一个**永远对不上**的句柄：一次正常的结束会被报成"
                "「主张多报了一条」，而调用方拿那个回执去改的地方是错的。"
            )
    return tuple(declared)


def _delta(declared: Mapping[str, Any]) -> RunDelta:
    """收下一份产出，或一条指得出字段的拒绝。

    与 :func:`_declaration` 同一条纪律：域模型自己会拒（九格里多一格、少一格的名字、
    处置行不合法），而 ``str(ValidationError)`` 会把输入整个回显回去 —— ``_readable``
    把它摊成「哪一个字段、为什么」。

    那道 ``isinstance`` 闸与 ``_window`` 那一处同一个理由：SDK 按工具入参 schema 自己
    校一遍，所以 ``delta="a delta"`` 在进到本函数体之前就被拒了（拒词是 ``Input should
    be a valid dictionary``）。留着它是给**库调用方**的，而它在这里比 ``_window`` 那一
    处更要紧一点：一份形状不对的产出能被读成「每一格都空的那一份」，于是它会撞上
    ``ending_refusal``（``completed`` 不许配空产出）或者被当作一份合法的 ``failed``
    —— 两种读法都在替调用方把一次**形状**错误答成一次**内容**问题。
    """
    if not isinstance(declared, Mapping):
        raise ToolError(
            f"delta 是 {type(declared).__name__}，而它要的是一份产出（一个对象，例如 "
            '{"effects": []}）。\n'
            "  九格各答一件不同的事（处置 / 路线 / 证据 / 产物 / 复核 / 待办 / 用量 / "
            "未结 / 建议），而一个形状不对的产出会被读成**每一格都空的那一份** —— 于是"
            "「这一次请求写错了」会被报告成「这一轮什么都没产出」。"
        )
    try:
        return RunDelta.model_validate(declared)
    except ValidationError as exc:
        raise ToolError(
            "这一份产出拼不成一轮 Run 的 Delta：\n" + _readable(exc)
        ) from exc


def _window(declared: Mapping[str, Any] | None) -> AuthorizationWindow | None:
    """``{"max_objects": 12, "budget": {...}, "not_after": "..."}`` → 这条链的范围。

    ## 三格都必填，而且缺的那一格要**点名**

    ``domain/run.py::AuthorizationWindow`` 逐字：「§B17 条件 4 要求『达累计预算时
    stop』—— 一条线都没有的窗口在那个阈值上永远不触发，而『没设过』与『还没到』
    在回执上同形」。所以这里不替任何一格取缺省值。反过来说，**缺省不等于缺失**：
    整格 ``window`` 没给是「没有自动授权」（走得通，那是 ``manual``），给了但少
    一格是**这一份授权写不完整**（拒）。这两件事的处置相反，所以 ``None`` 与
    「少一格」必须是两条不同的路。

    ## 多写一格也拒

    ``StrictModel`` 本来就会拒，但那句报错是 pydantic 的字段路径；调用方在这里要
    知道的是「你多写了一个名字，而它不会被用到」。**静默接受**是这里最坏的处置：
    一个 ``max_object``（少个 s）会被读成「我写上限了」，而那一格从来没进过任何
    判定 —— 报告上它是「这条链还没到上限」。

    ## ``not_after`` 走 ``_moment``，与 ``cutoff`` 同一份时区要求

    naive 不得进入本系统，两个时刻都不得例外。``field`` 传点分路径
    （``window.not_after``）：它在 JSON 里就是这个名字，调用方拿它去改的那一格与
    报错里写的那一格是同一个。

    ## 下面那道 ``isinstance`` 闸服务面上够不到，而它是**给库调用方**的

    ``window`` 那一格的类型是 ``dict[str, Any] | None``，SDK 按工具入参 schema 自己
    校一遍，所以 ``window="a string"`` 在进到这一函数体**之前**就被拒了（拒词是
    ``Input should be a valid dictionary``）。这道闸因此不是给服务面看的，它是这一
    函数**自己**的契约 —— 库调用方（或将来某个直接拼规格的组合根）拿它解析一份配置
    值时不走框架那条路。

    留着它而不是删掉，理由具体：删掉之后，``str`` 会走到下面 ``name not in declared``
    那一句，而 ``in`` 对字符串是**子串**匹配 —— ``"max_objects" not in "a string"``
    为真，于是它报「窗口缺了三格」，把一次**形状**错误说成一次**内容**错误。
    ``tests/test_mcp_runs.py::test_the_windows_own_shape_guard_still_fires`` 判的就是
    这三样形状（``str`` / ``int`` / ``list``），因为它够不到服务面那条路，只能单独判。
    """
    if declared is None:
        return None
    if not isinstance(declared, Mapping):
        raise ToolError(
            f"window 是 {type(declared).__name__}，而它要的是一个对象，三格："
            f"{list(_WINDOW_FIELDS)}（例如 "
            '{"max_objects": 12, "budget": {"search": 40}, '
            '"not_after": "2026-10-05T00:00:00+00:00"}）。'
        )
    missing = [name for name in _WINDOW_FIELDS if name not in declared]
    if missing:
        raise ToolError(
            f"窗口缺了 {missing} —— 三格都要：{'、'.join(_WINDOW_FIELDS)}。\n"
            "  §B17 条件 4 逐字是「达到对象数/累计预算/截止时间**任一** stop」——"
            "「任一」要求三个都能被求值，所以一个缺席的阈值会让那句话少判一条，"
            "而它在报告上与「那条判据没触发」同形。\n"
            "  缺省不是缺失：整格 window 不给是「没有自动授权」（那时模式是 "
            "'manual'），而给了却少一格是**这一份授权写不完整**。"
        )
    extra = sorted(set(declared) - set(_WINDOW_FIELDS))
    if extra:
        raise ToolError(
            f"窗口多了 {extra} —— 它只收 {list(_WINDOW_FIELDS)}。\n"
            "  多写的那一格**不会**进任何判定，所以静默收下的后果是调用方以为"
            "「我写上限了」，而那一格从来没被读过。"
        )
    try:
        return AuthorizationWindow(
            max_objects=declared["max_objects"],
            budget=_envelope(declared["budget"]),
            not_after=_moment(
                declared["not_after"], field="window.not_after", meaning=_WINDOW_MEANING
            ),
        )
    except ValidationError as exc:
        # 走到这里的是「三格都在，但有一格不成立」（``max_objects`` 小于 1、窗口
        # 的 ``budget`` 为空或同一科目出现两次、``not_after`` 的形状不对）。
        # 与 ``RunSpec`` 那条同款：``_readable`` 摊成「哪一个字段、为什么」，
        # 不摊的话回执是一句光秃秃的 ``Error executing tool submit_run``。
        raise ToolError(
            "这一份授权窗口拼不成一条链的范围：\n" + _readable(exc)
        ) from exc


# ----------------------------------------------------------------------
# 域对象 → 回执
# ----------------------------------------------------------------------
def _receipt(run: Run, *, admitted_at: datetime, recorded: tuple[Ambiguity, ...]) -> dict[str, Any]:
    """受理回执 = 那一行的投影 + **这一次调用**的两格 + 一段说明。

    ``created_at`` 与 ``admitted_at`` 是两格而不是一格：第一次提交它们几乎相同，
    重发时只有后者会变 —— 于是「这一行早就有了」这句话是从这两个时刻**读出来**
    的。这一层刻意不替调用方下这个判断（见模块 docstring）。

    这里**没有**一份完整的 preview 报告（复用多少、补哪些缺口）。那一份
    **不是回执的一格，它是一个面** —— ``preview_run``（切片 g），因为它答的是
    「动手之前会怎样」，而回执答的是「这一行被写下来了吗」。把前者塞进后者，会让
    一次**已经发生**的受理与一次**还没发生**的打算在报告上同形，而两者的下一步
    动作相反。

    这一片停在 ``plan_ref`` 与 ``policy_versions`` —— 它们答的是「这一次读的是哪
    一份计划、按哪几份策略」，而那正是受理这一步真的知道的全部。
    """
    return {
        **_projection(run),
        "admitted_at": admitted_at.isoformat(),
        "recorded": [item.model_dump(mode="json") for item in recorded],
        "note": (
            "受理**不是**执行：这一行只是「这一轮被批准了、范围与信封是什么」，"
            "它没有派发任何东西、没有**写**预算账本、没有开新的工作单元"
            "（带窗口的自动受理**读**过一次账本：它要读这条链的累计，见 ``window`` 那一格）。\n"
            "``created_at`` 是那一行写下去的时刻，``admitted_at`` 是**这一次调用**"
            "的受理时刻 —— 重发同一个键时只有后者会变，所以「这一行早就有了」是从"
            "这两格读出来的一句话，这一层不另报一个布尔。\n"
            "``recorded`` 是**记下来但不拦路**的那几条（软歧义）：计划是在这几处之上"
            "保守编出来的，它为空表示这次没有可报的。"
        ),
    }


def _projection(run: Run) -> dict[str, Any]:
    """``Run`` 那一行的投影 —— 受理回执与读回面**共用的那一半**。

    两份各写一遍的代价不是那十来行：先漂的是「哪些格子进这份回执」，于是一份说
    ``delta``、另一份不说，而两份都声称自己报的是**同一行**。本仓对这件事的纪律
    写在 ``application/validation.py`` 的 docstring 里（那里分的是两种**形状**，
    所以两份都留着；这里只有一种形状，所以只有一份）。

    两格逐字来自 ``Run`` 而不是重算：``stop_reason`` 与 ``delta`` 是同一条判据的
    两半（表的 CHECK + ``Run`` 的校验器），所以这里把它们一起报、一起为空。
    ``stopped_at`` 与 ``stop_reason`` 同理。
    """
    spec = run.spec
    return {
        "run_id": run.run_id,
        "workspace": spec.workspace,
        "selector": spec.selector,
        "objective": spec.objective,
        "execution_mode": spec.execution_mode.value,
        # 窗口与信封**并列报**，不合成一格：那两格说的是两件事（「这一条链一共允许
        # 走多远」与「这一轮允许用多少」），合成一个会让回执答不出「这次超支是链的
        # 上限还是本轮的信封」。``None`` 是「没有自动授权」，读得出来是 manual。
        "window": None if spec.window is None else spec.window.model_dump(mode="json"),
        "budget": [line.model_dump(mode="json") for line in spec.budget],
        "cutoff": spec.cutoff.isoformat(),
        "context_ref": spec.context_ref,
        "plan_ref": spec.plan_ref,
        "policy_versions": list(spec.policy_versions),
        "idempotency_key": spec.idempotency_key,
        "spec_sha256": run.spec_sha256,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "stop_reason": None if run.stop_reason is None else run.stop_reason.value,
        "stopped_at": run.stopped_at,
        "is_stopped": run.is_stopped,
        "delta": None if run.delta is None else run.delta.model_dump(mode="json"),
    }


# ----------------------------------------------------------------------
# 读一行
# ----------------------------------------------------------------------
def _one_run(store: RunStore, run_id: str, *, workspace: str) -> Run:
    """按 id 读一轮，或两条拒绝之一。

    两条拒绝处置相反，所以它们必须是两句话：

    * **读不到** —— ``unknown_run_refusal`` 的逐字报文。它说的是「**没有**这一轮」，
      而那句里逐字写了为什么不能读成「这一轮已经结束了」：后者会把一次写错 id
      读成一次重放。这一句住在 ``ports/runs.py``（两个实现都调同一份），这一层
      只负责**原样交出去** —— 换一句措辞在这里，等于让同一个情形在存储层与
      服务面上有两句话。
    * **读到了、但是别人的** —— 点名两个工作区。``get`` 按 id 取、不按工作区取，
      所以一行属于别的工作区时它照样会被读出来；而本部署服务的是 **profile 那个
      场景**（``submit_run`` 写下去的工作区也恒等于它）。跨工作区的读不留痕：它在
      这一侧看起来只是一次成功的查询。「按账号取到的这一行是谁的」是每一处按 id
      取数都要回答的那一句（``adapters/sqlite/db.py::bind_workspace`` 的那些调用点
      共用的也是这一句）。
    """
    run = store.get(run_id)
    if run is None:
        raise ToolError(unknown_run_refusal(run_id))
    if run.spec.workspace != workspace:
        raise ToolError(
            f"Run {run_id} 属于工作区 {run.spec.workspace!r}，而本部署服务的是 "
            f"{workspace!r}。\n"
            "  这一次读被拒，不是因为它找不到 —— 找到的**是另一本账**。\n"
            "  跨工作区的读不留痕：它在这一侧看起来只是一次成功的查询，而被读到的"
            "那一行此后会被当成本地事实读回去。"
        )
    return run

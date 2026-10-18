"""受理一次 Run （计划 §B17 的**受理面**）。

## 这一层只做一件事：把一次提交变成一份 :class:`~marketops.domain.run.RunSpec`

十二格里，**六格必须由调用方给**，因为它们是这次提交的内容本身：
``execution_mode`` / ``window`` / ``workstream`` / ``budget`` / ``cutoff`` /
``idempotency_key``。

**第十二格 ``workstream`` 与前五格不同款**，值得单独说清（它是 B01 把计划那一格
``workspace/workstream`` 拆开时新增的那一半）：前五格是**每一次提交都有的内容**，
没有缺省就构造不出规格；而 ``workstream`` 的缺省 ``None`` 是一个**说得出来的
事实**（「这一轮不在任何一次启动之下」），它是今天除启动那条路以外每一个调用点的
真实取值。给的场合只有一个：``start_workstream`` 那一条路径。

另外六格**不能由调用方给**，各有各的理由：

* ``objective`` 取自 ``ResearchContext.objective``。「这一轮要弄明白什么」在提交
  之前就已经写在上下文里了；让调用方再打一遍，就有两个字段都自称是那个答案，
  而它们分家的那一天（改了一个没改另一个），记录上没有任何一处会红。
* ``context_ref`` / ``plan_ref`` / ``policy_versions`` 是**内容摘要**，不是路径，
  也不是可以顺手编一个的值：前两者分别由 ``fingerprint_of`` 与
  ``EffectiveResearchPlan.digest`` 生产，后者由 ``policy_versions_of`` 生产 ——
  各一个生产者，这一层只是把它们取过来。
* ``workspace`` 与 ``selector`` 取自**盘上的 Scene**（``SceneVersion.workspace`` /
  ``SceneVersion.scene_id``）。一个由请求参数决定的工作区，等于让调用方自己挑一条
  授权链；而 ``SceneVersion`` 的那两个字段的注释里写着它们是「对象授权链的第一
  段」—— 那一段不能由被授权的对象来申报。

## 计划必须与上下文**对得上**，而不是「两个都收下」

如果只把 ``plan_ref`` 与 ``context_ref`` 分开收，一份从 A 上下文编出来的计划可以
被挂到一次基于 B 上下文的 Run 上 —— 而盘上每一处（回执、Delta、将来的复用判定）
都会显示「这一轮是按那份计划跑的」。所以这里把 ``plan.fingerprint_digest`` 与
``fingerprint_of(context).digest`` 比一次。

**不等时抛 ``ValueError``，不落成一条受理拒绝。** 理由与 ``preview_plan`` 对
「profile 读不出批准范围」的处置相同：处置是**把调用的两样东西配对**，不是把请求
改对再提交，也不是等一个人回答 —— 后两种都是 ``RunSpecError`` 的形状。判据放在
这一层而不是 ``RunSpec`` 里，因为规格拿不到上下文：那份对应关系在域模型里写不出来。

## 拒绝由谁回答

================================================  ==========================================
拒绝                                              由谁回答
================================================  ==========================================
``watch`` 而这一批主体上**没有**到期/信号          :func:`_require_due`（切片 f —— **读盘**）
没带窗口的 ``bounded_auto`` 受理不了               ``ports.runs.admission_refusal``
截止时刻不在将来                                  ``ports.runs.admission_refusal``
这条链已经到了一条 stop                            ``ports.runs.window_refusal``
本次提交的信封是空的                               ``RunSpec`` 的 ``budget`` 校验器
``manual`` / ``watch`` 带了授权窗口                ``RunSpec`` 的跨格校验器
链接不上（链上某笔预留账本没有）                    :func:`spent_of` **抛**
================================================  ==========================================

**中间那三条不在这里判。** 它们是跨实现不能漂移的裁定，住在 ``ports/runs.py``，由
``RunStore.admit`` 调用（两个实现都调同一份）。这一层再判一次，就是同一句话的
第二份实现 —— 而它漂移的那天，正是「两个实现都通过」这句话开始没有依据的那天。

``watch`` 那一条**形状不同**：它要读盘（那份主体上有没有到期的格子），所以它住在
这一层 —— 与 ``spent_of`` 同一类，而 ``spent_of`` 正是「读盘在外、判定在内」的
第一次应用（``ports/runs.py`` 那一节逐字：「链那一条做不到这一点，所以它在事务里」）。
它与 ``ports/runs.py`` 那两条的**次序**是「先读后写」逼出来的，不是一条优先级声明：
这一层要在 ``store.admit`` **之前**判，所以它可能先说话；两条都在说真话，互不依赖。

最后一条与其余六条**形状不同**：它不是一条受理拒绝（处置「改规格」），而是一次
**事故**（两份记录对不上，处置是去查账）。所以它不返回理由串，它抛
:class:`~marketops.domain.errors.RunReconciliationError`。把「读不到账本」也写成一
条受理拒绝，会让一个接错的账本在日志里长得像一次参数写错。

## 受理**不是**执行

``admit`` 只写下一行 Run。它不派发任何东西、不创建 work item、不**写**预算账本
—— 账本在这一层只被**读**过一次（上面那格累计），而那次读的结果是 ``admit`` 的
**输入**，不是它留下的痕迹。一个把「受理」与「开跑」写进同一个函数的入口，会让
§B17 条件 3 那句「manual 下完成后保持 idle、绝不自行扩下一批」在代码里无处落脚
—— 因为那时「下一批」是在受理内部自己开的。

## 时钟从外面进来

``at`` 是受理时刻。它参与判定（截止时刻在不在将来），也写进 ``created_at``。
这一层不读 ``utc_now()``：一个读自己时钟的判据，在测试里只能靠等待来触发。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from ..domain.enums import BudgetLine, ExecutionMode, ReservationState
from ..domain.errors import ConfigError, RunReconciliationError, RunSpecError
from ..domain.research_context import ResearchContext, fingerprint_of
from ..domain.research_plan import EffectiveResearchPlan
from ..domain.run import (
    AuthorizationWindow,
    Run,
    RunBudgetLine,
    RunSpec,
    policy_versions_of,
    spec_fingerprint,
)
from ..domain.scene import SceneVersion
from ..ports.budget import BudgetLedger
from ..ports.runs import ChainProgress, RunStore
from ..ports.scenes import MaintenanceStore, SceneStore
from .maintenance import due_work


def admit_run(
    store: RunStore,
    *,
    scene: SceneVersion,
    context: ResearchContext,
    plan: EffectiveResearchPlan,
    budget: tuple[RunBudgetLine, ...],
    cutoff: datetime,
    idempotency_key: str,
    at: datetime,
    execution_mode: ExecutionMode,
    window: AuthorizationWindow | None = None,
    workstream: str | None = None,
    ledger: BudgetLedger | None = None,
    scene_store: SceneStore | None = None,
    maintenance: MaintenanceStore | None = None,
    subjects: Iterable[tuple[str, str]] = (),
) -> Run:
    """受理一次增量，返回落盘的那一行 Run。

    Args:
        store: Run 记录。两个实现跑同一份契约（``tests/test_run_contract.py``）。
        scene: 盘上那一版 Scene。``workspace`` 与 ``selector`` 从它取 —— 见模块
            docstring 里那一条「不能由调用方给」。
        context: 这次研究要什么。``objective`` 与 ``context_ref`` 从它取。
        plan: 这次按哪一份计划跑。必须是从 ``context`` 编出来的那一份：
            ``plan.fingerprint_digest`` 与上下文的摘要不等就抛（见下）。
        budget: 这一次的信封。**由调用方给** —— 上游还没有按窗口算本轮信封的生产者。
            它与 ``window`` 的 ``budget`` 不是同一件事：这一格说「**这一次**允许用
            多少」，窗口那一格说「**这条链**一共允许用多少」，两者都在（
            ``RunSpec`` 的跨格校验器另判「本轮单条不得高于窗口」）。
        cutoff: 这次跑到什么时候为止。全仓唯一的**运行**截止（另两处同名值见
            ``domain/run.py``）。
        idempotency_key: 由调用方给。服务端不生成 —— 见 ``domain/run.py::run_id``。
        at: 受理时刻。判定与落盘都用它，这一层不读自己的时钟。
        execution_mode: 谁授权跑起来的。**没有缺省值**：一个缺省会让「这次是有人
            提交的」成为调用方不写也成立的事 —— 而三个模式今天各有各的受理前提
            （``manual`` 没有前提，``bounded_auto`` 要一份窗口，``watch`` 要这一批
            主体上有一条到期的格子），凭缺省补上的那一个恰好是最不需要依据的那个。
        window: 这条自动授权链的范围。**有缺省值**（``None``），与上一格不同款，
            理由值得写清：``execution_mode`` 缺省会把「有人提交了这一次」变成不写
            也成立；而 ``window`` 缺省说的是「没有自动授权」—— 那是一份 manual
            规格**本来就有**的事实，不是一次没表达出来的授权。写错了模式却指望
            缺省把窗口补上，由 ``RunSpec`` 那条跨格校验器挡住（``manual`` / ``watch``
            带了窗口就是错的那一对）。
            受理面**拒**没有窗口的自动模式，而**带上窗口的**走得通（见
            ``ports/runs.py::admission_refusal`` 与 ``window_refusal``）。e2c 之前
            ``submit_run`` 恒传 ``None``（那时没有写下窗口的生产者），所以只有库
            调用方试得到非 ``None`` 那一支；现在服务面也收得下它了（那条工具的
            ``window`` 参数）。这一格必须能接非 ``None`` 的理由与那时**同款**：
            受理面那两条判据要**两种都试得到**，否则其中一条漂成
            ``if spec.window is None`` 时没有任何一条用例会红，而那一漂的后果是
            **带着窗口的自动规格被静默受理** —— 盘上多出的那一轮什么都齐全，只是
            没有任何一处问过它累计走到哪了。
        ledger: 预算账本。**只有**这一层用它读 ``window`` 的第三个阈值（累计预算）：
            ``RunStore`` 按设计读不到金额（见 :func:`spent_of`）。
            缺省 ``None`` 与 ``window`` 在场同时发生时**不是**「跳过这一格」——
            ``window_refusal`` 会把「账本读数没接上」显式拒掉。没有窗口时它完全不
            被使用，所以既有调用点不必改。
        scene_store / maintenance: 场景存储与维护账。**只有** ``WATCH`` 用得到它们：
            它的来源是到期/信号，而那句话要读这两本（见 :func:`_require_due`）。
            缺省 ``None``，理由与 ``ledger`` 那一格同款 —— 非 ``watch`` 的调用点
            完全不必知道有这两格，而 ``watch`` 那一侧缺了它们**会被拒**，不是被跳过。
        workstream: 这一轮挂在哪个工作流下（``Workstream.workstream_id``）。缺省
            ``None`` = **不挂在任何一次启动之下**，而那是今天除启动那条路以外所有
            调用点的事实。给的场合只有一个：``start_workstream`` 那一条路径（它的
            handler 把这一格接到 ``admit`` 的闭包里）。这一格在这里**只过路**：
            它不参与任何判定，因为「这个工作流存不存在、绑的是不是同一版场景」是
            启动面判的（``application/workstream.py``），在这里再判一次就是同一句话
            的第二个出处 —— 而两份分岔的表现是「一个 Run 挂在一行读不回来的工作流
            上」，报告上它长得完全正常。
        subjects: 这一批主体（``(subject_kind, subject_id)`` 的序列）。**只有**
            ``WATCH`` 用得到：``what_is_due`` 不知道世界上有谁（它的 docstring 逐字：
            「一个会自己发现主体的选择器必然也会自己发明主体」），而这条 Run 要有的
            那个前提（「有一个对象到期了」）正是关于某一批主体说的。由调用方指出一个
            **来源**（``submit_run`` 的 ``name`` / ``cohort``），不由这一层猜。

    Returns:
        落盘的那一行。同一个 ``(workspace, key)`` 配上同一份内容时，重发拿回的是
        **同一行**（幂等由主键保证，不是靠写之前查一下）。

    Raises:
        ValueError: ``plan`` 不是从 ``context`` 编出来的，或者这份上下文有讲不清的
            地方（``fingerprint_of`` 在硬歧义上拒绝，它自己会说清是哪几条）。
        RunSpecError: ``_require_due`` / ``admission_refusal`` / ``window_refusal``
            给了理由（这一批主体上没有到期的、这个模式今天受理不了、截止时刻不在
            将来、这条链已经到了一条 stop）。
        RunReconciliationError: 链上一轮的 Delta 记着某一笔预留，而账本读不到它 ——
            这种情况不返回 0（见 :func:`spent_of`）。
        IdempotencyConflictError: 同一个键配了另一份规格。
    """
    spec = spec_of(
        scene=scene,
        context=context,
        plan=plan,
        budget=budget,
        cutoff=cutoff,
        idempotency_key=idempotency_key,
        execution_mode=execution_mode,
        window=window,
        workstream=workstream,
    )
    # ``watch`` 的来源：先问「这一批主体上有没有到期/信号」，有才继续。
    #
    # 位置在 ``spec_of`` **之后**、``store.admit`` **之前**，两条理由各不相同：
    # 域层那条跨格校验器（``manual`` / ``watch`` 不得带窗口）必须先说话 —— 它判的
    # 是「这两格说的是不是一件事」，而那比「今天有没有到期」更根本；而这一步必须
    # 在写之前，因为它要**写下的行数**是零（一次先受理再报错的拒绝会留下一行没有
    # 人打算要的 Run）。
    if spec.execution_mode is ExecutionMode.WATCH:
        _require_due(
            scene_store=scene_store,
            maintenance=maintenance,
            scene_id=scene.scene_id,
            at=at,
            subjects=tuple(subjects),
        )
    # 累计预算这一格的读数只在这里取得到：链上有哪几笔预留是**存储**答的
    # （``chain_progress``），而「那几笔各是多少」只有**账本**答得了。两样都不在
    # ``RunStore`` 里 —— 它按设计读不到金额（``domain/run_delta.py::RunDelta`` 逐字：
    # 「金额只有一个权威（``ports/budget.py`` 的那一处 ``SUM``）」）。
    #
    # 没有窗口（``manual``）时不读：那条规格不属于任何一条链，「累计」这句话对
    # 它没有主语。有窗口而没有账本时**也不读** —— 那一格留给 ``window_refusal``
    # 答「读不到账本读数」，在这里悄悄跳过会让「账本没接上」与「链上还没花过钱」
    # 在受理面上同形。
    spent = None
    if window is not None and ledger is not None:
        spent = spent_of(
            ledger,
            store.chain_progress(workspace=spec.workspace, window_digest=window.digest),
        )
    return store.admit(
        spec, spec_sha256=spec_fingerprint(spec), at=at, spent=spent
    )


def due_refusal(
    *,
    scene_store: SceneStore | None,
    maintenance: MaintenanceStore | None,
    scene_id: str,
    at: datetime,
    subjects: tuple[tuple[str, str], ...],
) -> str:
    """这一批主体上**有没有**到期/信号；没有（或判不了）就给理由，空串 = 有（切片 f）。

    §B17 条件 5 逐字：「``watch`` 只为到期/信号对象创建维护 Run。」这一条就是那个
    「**只**为」的执行者 —— 它答「今天有没有那样一个对象」，答「有」才放行。
    缺了它，一个没有依据的 watch Run 与一次真的因到期而起的维护在记录上一模一样
    （见 ``ports/runs.py`` 那一节：这句话在切片 f 之前**由那个纯函数说**，而它说的
    是「来源还没有生产者」—— 那句话在切片 f 之后不再成立）。

    ## 返回字符串而不抛，与另两条拒绝同形（切片 g 改的）

    切片 f 落地时这是一个 ``_require_due``：只抛、不返回。切片 g 的 Run preview 要
    **在受理之前**报出「这一次提交会不会被受理」，而它要报的第 0 条（受理面的次序上
    最靠前的一条，见下）正是这一条 —— 于是这份判据有了第二个调用方，而那个调用方
    要的是一句话，不是一次异常。改成返回字符串，与 :func:`~marketops.ports.runs.admission_refusal`
    和 :func:`~marketops.ports.runs.window_refusal` 同形（那两条的分工逐字是「判据
    一份、两个实现共用、返回字符串而不抛异常」）。

    接住异常来做这件事是**另一条**路（``try: _require_due(...) except RunSpecError``），
    而那正是 ``application/plan_preview.py`` 模块 docstring 拒过的做法：一个把内部
    判定渲染成「接住一次异常」的实现，会让「两处对同一份判据的读法不一致」永远只表现
    为「preview 说得比受理面好听」，而没有任何一处红。这里也一样：preview 报的就是
    ``admit_run`` 判的**同一个函数**的返回值。

    ## 它在受理面次序里的位置（最靠前）

    ``admit_run`` 里它的调用点在 ``spec_of`` **之后**、``store.admit`` **之前** —— 也
    就是**在** :func:`~marketops.ports.runs.admission_refusal` 之前。三条的次序不是
    随手排的：域层那条跨格校验器（``manual`` / ``watch`` 不得带窗口）必须先说话（它判
    的是「这两格说的是不是一件事」，比「今天有没有到期」更根本），而这一步必须在写
    之前（它要写下的行数是零）。preview 照抄这个次序，所以 ``StopVerdict`` 的第一档是
    ``NOT_DUE`` 而不是 ``REQUEST_REFUSED``。

    ## 为什么调 ``due_work`` 而不是直接调 ``what_is_due``

    ``what_is_due`` 是一个**纯函数**（观测、信号、节奏、时刻进去，到期格子出来），
    而 ``due_work`` 多做了两样这一条同样要的事：

    * **场景今天派不派发新工作**（暂停不派发 —— 而这一次受理正是一次由到期驱动的
      派发，所以暂停的场景上受理一个 watch Run 与「暂停只停新的派发」那句话冲突）；
    * **账从哪读**（``record.workspace``，而不是让这一层自己拼一个工作区）。

    两样都是「两个来源、一处派发」那句话的落点：在这里再写一遍
    ``maintenance.observations(workspace=...)``，就是同一句话的第二份实现 —— 而它
    漂移的那天，读者会看到「计划里说有到期的」与「受理时判没有到期的」同时成立。

    ## 三个「拒」各自说清缺的是哪一样

    * **两个存储没递进来** ⇒ 判不了。而判不了既**不能**读成「没有到期」（那会把一个
      没有任何依据的 watch Run 受理下来），也**不能**读成「有到期」（那更没有依据）
      —— 只能拒，并把处置写清。这与 ``spent_of`` 的「算不出来就抛，不返回 0」是
      同一条纪律，只是这里的那条路是「这一次请求走不通」而不是「账目事故」。
    * **场景今天不派发** ⇒ ``due_work`` 自己会说（暂停 / DRAFT），原样交出去，改成
      拒绝只是把它换到本层该有的类型上（``ConfigError`` 说的是「部署写错了」，
      而「这个场景今天暂停」是一个正常状态）。
    * **一条都没有** ⇒ 这一条要的正面事实不存在。

    ## 它读的是**这一批主体**，而那句话必须写在拒词里

    ``what_is_due`` 的 ``subjects`` 是**这一批**是谁的答案。同一本账、同一批观测，
    换一份名单就是另一个答案 —— 所以拒词里必须报出**判了几个主体**：只说「没有到期」
    的话，一个把「名单读空了」与「名单上都没到期」读成同一句话的实现会与它同形，
    而前者的处置是去查那份名单。
    """
    if scene_store is None or maintenance is None:
        return (
            "这一次 watch 受理判不了：它的来源是**到期或信号**，判它要读维护账，"
            "而这一次调用没有把两个存储（``scene_store`` / ``maintenance``）递进来。\n"
            "  判不了**不能**当成「没有到期」受理 —— 一个受理了的 watch Run 在记录上"
            "与一次真的因到期而起的维护长得一模一样；也不能当成「有到期」，那更没有"
            "依据。\n"
            "  处置是把两样递进来（服务面 ``submit_run`` 两样都在场），或者把模式改回"
            " 'manual' —— 后者不需要任何来源。"
        )
    try:
        due, _truncated = due_work(
            scene_store=scene_store,
            maintenance_store=maintenance,
            scene_id=scene_id,
            now=at,
            subjects=subjects,
        )
    except ConfigError as exc:
        # 类型那半换在 :func:`_require_due` 里（``ConfigError`` 说的是「部署写错了」，
        # 而「这个场景今天暂停」是一个正常状态），措辞原样交出去。
        return str(exc)
    if due:
        return ""
    return (
        "执行模式 'watch' 今天受理不了这一次提交：它的来源是**到期或信号**"
        "（``domain/maintenance.py::what_is_due``），而这一批主体（"
        f"{len(subjects)} 个主体）里一条都没有 —— 每一格都还没到自己的节奏，也没有"
        "任何变更信号落在那些观测之后。\n"
        "  这不是「watch 还不能用」：把那个来源接成维护 Run 的正是这一条路，而它"
        "**要有一条到期的格子**才受理。一个没有依据就受理了的 watch Run，在记录上"
        "与一次真的因到期而起的维护长得一样 —— 而它的来源其实是「有人提交了它」。\n"
        "  处置：给它一批真会到期的对象（``name`` / ``cohort`` 指出的那一份名单），"
        "或者把模式改回 'manual' —— 后者说的是「这一轮跑完就停在那儿」，不需要"
        "任何来源。"
    )


def _require_due(
    *,
    scene_store: SceneStore | None,
    maintenance: MaintenanceStore | None,
    scene_id: str,
    at: datetime,
    subjects: tuple[tuple[str, str], ...],
) -> None:
    """受理面要的那一次**拒绝**：判定住在 :func:`due_refusal` 里，这里只换类型。

    受理面拿不到一个字符串（它要么走下去、要么抛），报告面拿不到一次异常（它要把
    那句话写进读数里）—— 所以判定与那两种形状各一处：判定一份，包装两个。

    ``ConfigError`` ⇒ ``RunSpecError`` 这个**类型**的换算也落在这里（``due_refusal``
    返回的是原文，没有类型可换）：``ports/runs.py`` 的三条拒绝（``admission_refusal``
    / ``window_refusal`` / 这一条）对调用方的形状是一致的 —— 一次「这一次请求走不通」
    的结果，不是一次崩溃。
    """
    reason = due_refusal(
        scene_store=scene_store,
        maintenance=maintenance,
        scene_id=scene_id,
        at=at,
        subjects=subjects,
    )
    if reason:
        raise RunSpecError(reason)


def spent_of(
    ledger: BudgetLedger, progress: ChainProgress
) -> Mapping[BudgetLine, int]:
    """这条链**已经花掉多少** —— 逐科目，从账本读回来。

    ## 为什么是「拿 id 去账本读」而不是在 Delta 里记一个数

    ``RunDelta.usage`` 里是**预留 id**，不是金额，那一段的 class docstring 逐字说了
    为什么：「金额只有一个权威（``ports/budget.py`` 的那一处 ``SUM``），在回执里再抄
    一个数就是第二个答案 —— 而两个答案分家的那天，读者会照回执上那个数去做决定。」
    所以这里逐笔 ``ledger.get(rid)`` 求和，是那句话唯一的执行方式。

    ## ``RESERVED`` 算，``REFUNDED`` 不算

    这与 ``BudgetUsage.held``（``spent + reserved``）取的是同一个口径：一笔还扣着
    的预留是**已经承诺出去**的钱，把它读成「还没花」会让一条链在飞行中就能反复
    越过自己的上限。按 §8，失败的调用**不自动退款** —— 于是那些停在 ``RESERVED``
    的行本来就不会再回来了，把它们从累计里去掉等于凭空多出额度。

    反过来说，**只有显式退款过的那一笔不算**（``ReservationState.REFUNDED``），
    因为那是账本自己说「这笔钱没花掉」的**唯一**一句话。

    ## 取不到就抛，不返回 0

    本仓那条纪律（``domain/run_delta.py::UnaccountedUsage`` 也引了它）。一条 Delta 上
    写着 ``reservation_id``，而账本说没有这一笔，只有两种可能：读错了账本，或者那一行
    丢了。两种都让「这条链一共花了多少」**算不出来**，而按 0 继续算的后果是这条链被
    多放行一轮 —— 报告上它读起来只是「预算还够」。

    抛的是 :class:`~marketops.domain.errors.RunReconciliationError`：它管的是「两份
    记录之间的关系」，而这里正是两份各自都读得出来、只是读出来的不是同一件事。

    ## 这一格**不判**「科目在不在窗口里」

    ``window_refusal`` 只按窗口自己那几条线取值，所以这里多出来的科目不会被它看到。
    那不是一个洞：``RunSpec`` 的跨格校验器已经判过「本轮信封不得有窗口没有的科目」，
    而这一轮花在哪条线上由它自己的信封管。在这里再判一次，就是那句话的第三份实现。

    ## 一处**残留的窗口**，说清楚而不是假装没有

    ``progress`` 是**在写事务之外**读的（受理面先读、再把读数当参数递进去），所以
    在「读到累计」与「在事务里拿它判」之间，另一个进程可能刚好结束了一轮 —— 那一轮
    Delta 里的预留不在这份读数里，于是 ``spent`` 偏低。

    偏低的后果是**有界**的，因为同一份 ``window_refusal`` 里 ``open_runs`` 那一条
    才是硬互锁：一轮没结束就开不了下一轮，所以最多多放行**一轮**，而一轮最多花掉它
    自己那份信封。下一轮受理时读到的是更新后的累计。这与「两个都过、合起来超」不是
    同一个形态 —— 那个是任意多轮，这个是信封有界的一轮。
    """
    totals: dict[BudgetLine, int] = {}
    for reservation_id in progress.reservations:
        reservation = ledger.get(reservation_id)
        if reservation is None:
            raise RunReconciliationError(
                f"这条授权链（窗口 {progress.window_digest}）的 Delta 上记着预留 "
                f"{reservation_id!r}，而账本上读不到它。\n"
                "本仓的纪律是**算不出来就抛，不返回 0**：一条把读不到的量记成 0 的"
                "累计，在受理面上与「这条链还没花过钱」一模一样，而两者要人做的事"
                "相反（一个去接账本，一个可以继续走）。按 0 继续算的后果是这条链被多"
                "放行，而报告上它读起来只是「预算还够」。\n"
                "处置是查这一笔预留：是读错了账本（两个部署指向了不同的库），还是"
                "那一行真的丢了。"
            )
        if reservation.state is ReservationState.REFUNDED:
            continue
        totals[reservation.line] = totals.get(reservation.line, 0) + reservation.units
    return totals


def spec_of(
    *,
    scene: SceneVersion,
    context: ResearchContext,
    plan: EffectiveResearchPlan,
    budget: tuple[RunBudgetLine, ...],
    cutoff: datetime,
    idempotency_key: str,
    execution_mode: ExecutionMode,
    window: AuthorizationWindow | None,
    workstream: str | None = None,
) -> RunSpec:
    """:class:`RunSpec` 的**唯一**生产者。见模块 docstring 的两张分工表。

    单独一个函数而不是内联进 :func:`admit_run`：这一段的输入全是「从别处取来的
    值」，与「受理」那一步（写盘、幂等、拒绝）是两件事。合在一起写，读的人会把
    「这份规格是怎么拼出来的」与「受理成不成功」当成同一个问题。

    ``workstream`` 是**过路的一格**，不是这一层判的：它由调用方给，这一层只把它
    放进规格里。「这个工作流在不在盘上、绑的是不是同一版场景」由
    ``application/workstream.py`` 在**受理之前**判 —— 在这里再判一次就是同一句话
    的第二个出处，而两份分岔的表现是一个 Run 挂在一行读不回来的工作流上。

    计划与上下文的对应关系先比、再取 ``context_ref`` —— 顺序是有意的：先算出
    上下文的摘要（它在硬歧义上会抛，而那正是该抛的时候），再拿它与计划自带的
    ``fingerprint_digest`` 比。反过来的话，一份对不上的计划会先被写进
    ``RunSpec``，等取 ``context_ref`` 时才炸 —— 那时异常里说的是上下文，而错的
    是计划。
    """
    context_ref = fingerprint_of(context).digest
    if plan.fingerprint_digest != context_ref:
        raise ValueError(
            f"这一份计划不是从这份上下文编出来的：计划记的上下文摘要是 "
            f"{plan.fingerprint_digest}，而这份上下文算出来是 {context_ref}。\n"
            "两者都会进 Run 的记录，而记录上它们必须说的是同一件事 —— 挂错了的"
            "形态是「这一轮按 A 计划跑了 B 要研究的东西」，此后每一处读回执的人"
            "都会照那份计划理解这一轮做过什么。\n"
            "处置是把两样东西配对（用 ``preview_plan`` 现编一份，或换成编出这份"
            "计划的那份上下文），而不是把请求改对再提交。"
        )
    return RunSpec(
        workspace=scene.workspace,
        workstream=workstream,
        objective=context.objective,
        selector=scene.scene_id,
        context_ref=context_ref,
        plan_ref=plan.digest,
        execution_mode=execution_mode,
        window=window,
        budget=budget,
        cutoff=cutoff,
        policy_versions=policy_versions_of(plan),
        idempotency_key=idempotency_key,
    )


__all__ = ["admit_run", "due_refusal", "spec_of", "spent_of"]

"""收件箱的**编排**：开案、认领、裁决、批准，以及那条闸门（§12、§9）。

## 这一层做什么，以及为什么那些事不在别处

领域（``domain/review.py``）只懂两件对象与它们之间的判据，存储（``ports/review.py``
的两个实现）只懂怎么把它们放上盘。这一层做的是**只有它才能做的两件事**：

* **从一次判定走到一件待办**。``(工作区, 场景, 原因, 主体)`` 四样算出一个内容寻址的
  id、选出一组选项、把对象造出来 —— 也就是 §12 那句门槛（「政策/身份冲突且值得处理的
  才入 Inbox」）落到代码上的那一步。选项不在这里另写一遍：它们在
  ``domain.review.INTAKE`` 上（一处定义，理由是「同一个原因在两个入口下给人两套选项」
  只有并排看才看得出）。
* **把人的一条决定折进去之前要跨过的那些判据**，而它们跨的是**两个对象**：一条批准
  要知道它批的是哪一条提议、提议是谁提的（§9 的「获授权者批准」要求批准的人**不是**
  提议的人），一条提议要知道它挂起的那一版**真的存在**（否则那一版永远等不到批准，
  而它在报告上只是一次普通的规则变更）。

## 那条闸门，以及它为什么在这一层而不是在 ``control_scene`` 里

``awaiting_approval_for`` 是一条**纯函数**：给一串案件与一个版本号，回答「有没有一件
待办正等着它被批准」。用它当闸门是 ``control_scene(activate)`` 那一侧的调用点要做的
事，而这一层提供的是把「一串案件」凑出来的那一半（``store.list_cases()``）——
闸门本身只有一处定义，两个方向都读它。

**这条闸门只拦「由待办提议出来」的那一版。** 一个普通的、由 ``add_scene_version`` 直接
写下的版本照旧由控制器激活（既有行为逐字不变，理由写在
``docs/R1_R2_IMPLEMENTATION_LEDGER.md`` 的「取证结论二」里）：要第二个人批准的是
**一条规则的变更**，不是「把一个 Scene 立项并激活」。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..domain.errors import ConfigError, ReviewConflictError
from ..domain.review import (
    POLICY_APPROVAL_OUTCOMES,
    POLICY_PROPOSAL_OUTCOMES,
    HumanResolution,
    ReviewCase,
    ReviewFlow,
    ReviewReason,
    ReviewState,
    awaiting_approval_for,
    case_id_for,
    inbox_flow,
    intake_for,
)
from ..domain.scene import SceneImpact
from ..domain.util import utc_now
from ..ports.review import CaseAdmission, ReviewCaseStore
from ..ports.scenes import SceneStore
from .scenes import impact_between

#: ``get_review_flow`` 缺省看多长一段。三十天是一个**说得出口**的值：§12 的那条恒等式
#: 是拿来对账的，而对账要的是一段能被单独说清的窗口；窗口由调用方给时以调用方为准。
DEFAULT_FLOW_DAYS = 30


# ------------------------------------------------------------------ 开案
def open_case(*, store: ReviewCaseStore, case: ReviewCase) -> CaseAdmission:
    """把一件待办放上盘。**同一个问题第二次调用不新开、不覆盖**（端口逐字）。

    ``opened=False`` 时盘上那一件的 ``state`` 可能与本次传入的不同，而那个差别正是
    调用方要报出去的东西 —— 一件**已经裁决过**的待办不会被系统自己重开。这一层不替
    调用方把它读成「已经问过了」：那是把人的一句「不值得处理」在下一个批次里静默作废。
    """
    return store.open_case(case)


def case_for(
    *,
    workspace: str,
    scene_id: str,
    reason: ReviewReason,
    subject_ref: str,
    affected_refs: tuple[str, ...],
    evidence_ids: tuple[str, ...] = (),
    policy_version: int | None = None,
    queue: str = "",
    at: datetime | None = None,
) -> ReviewCase:
    """按 §12 那一串字段造一件待办。**选项与建议取自 ``INTAKE``**，不在这里另写。

    ``case_id`` 由 ``(工作区, 场景, 原因, 主体)`` 算出来（``domain.review.case_id_for``），
    而 ``ReviewCase`` 自己会核一遍那个 id 是不是内容的函数 —— 手工指定的 id 会让同一件
    事开出两件待办，于是「一次批准之后不再重复问」在主体那一侧静默失效。

    ``affected_refs`` 与 ``evidence_ids`` **没有缺省之外的来路**：前者是受影响对象的
    **快照**（§12 逐字：开了待办之后对象还可能变，人读到的必须是开案那一刻的那一串），
    后者是判这一件时要看的证据。两个都由调用方当场说清，因为只有它知道这一件是
    从哪一次判定来的 —— 这一层不替它去猜。
    """
    intake = intake_for(reason)
    moment = utc_now() if at is None else at
    return ReviewCase(
        case_id=case_id_for(
            workspace=workspace, scene_id=scene_id, reason=reason, subject_ref=subject_ref
        ),
        workspace=workspace,
        scene_id=scene_id,
        reason=reason,
        subject_ref=subject_ref,
        affected_refs=affected_refs,
        evidence_ids=evidence_ids,
        policy_version=policy_version,
        options=intake.options,
        recommendation=intake.recommendation,
        queue=queue,
        state=ReviewState.OPEN,
        version=1,
        opened_at=moment,
        updated_at=moment,
    )


def open_for(
    *,
    store: ReviewCaseStore,
    workspace: str,
    scene_id: str,
    reason: ReviewReason,
    subject_ref: str,
    affected_refs: tuple[str, ...],
    evidence_ids: tuple[str, ...] = (),
    policy_version: int | None = None,
    queue: str = "",
    at: datetime | None = None,
) -> CaseAdmission:
    """:func:`case_for` + :func:`open_case` 的合成 —— 生产者唯一要调的那一个。"""
    return open_case(
        store=store,
        case=case_for(
            workspace=workspace,
            scene_id=scene_id,
            reason=reason,
            subject_ref=subject_ref,
            affected_refs=affected_refs,
            evidence_ids=evidence_ids,
            policy_version=policy_version,
            queue=queue,
            at=at,
        ),
    )


# ------------------------------------------------------------------ 认领与裁决
def claim_case(
    *, store: ReviewCaseStore, case_id: str, by: str, at: datetime, expected_version: int
) -> ReviewCase:
    """认领一件。判据在 ``domain.review.claim`` 上，并发判据在存储里。"""
    return store.claim(case_id, by=by, at=at, expected_version=expected_version)


def resolve_case(
    *, store: ReviewCaseStore, resolution: HumanResolution, scenes: SceneStore
) -> ReviewCase:
    """把人的一条决定折进它那一件。**一次写事务**（端口逐字）。

    在委托给存储之前，这一层只加一条**跨对象的**判据：一条把案件推进
    ``AWAITING_APPROVAL`` 的提议（``PROPOSE_POLICY_CHANGE``）必须挂在一版**真的存在**
    的策略版本上。

    为什么它必须在写入之前：``control_scene(activate)`` 的闸门按版本号比
    （``awaiting_approval_for``），而一条挂着不存在版本的提议**永远等不到批准** ——
    它在报告上只是一件挂着的事，没有任何一处会红。反过来，一次已经批准而不存在的
    版本会让闸门拦下一个与它无关的版本号。

    ``scenes`` **是必填的**：一个可以省略它的调用点会在「提议挂的是哪一版」这件事上
    静默地什么都不查，而那正是同一份判据下的两个行为。
    """
    if resolution.outcome in POLICY_PROPOSAL_OUTCOMES:
        _require_scene_version(
            scenes, scene_id=_scene_of(store, resolution.case_id), version=resolution.proposed_version
        )
    return store.record_resolution(resolution, expected_version=resolution.expected_version)


def approve_policy_change(
    *,
    store: ReviewCaseStore,
    resolution: HumanResolution,
) -> ReviewCase:
    """``AUTHORIZER`` 批准一条已经写在盘上的规则变更提议。**必须是另一个人。**

    ## 与 ``resolve_case`` 的两处分别

    * **它只接受一种结果**（``APPROVE_POLICY_CHANGE``）。调用方传别的来，是在用一条
      批准的路做一次普通的裁决 —— 而那条路的角色要求是 ``AUTHORIZER``，比裁决宽还是
      窄取决于这一格，所以这里不接受。
    * **它比一次「谁提议的」**。``by`` 取自令牌（``interfaces`` 那一层做的事），而这一层
      拿它与那一件上**最后一条提议**的 ``by`` 比。§9 逐字要「获授权者批准」，而
      ``Role.AUTHORIZER`` 的 docstring 写着这一格的全部价值就是「它不是一个代理能自己
      走到的位置」—— 一个人先提议再批准，那个价值当场归零，而两次调用各自都完全正常。

    角色本身**不在这里判**：那是 ``security/catalog.py`` 的事，一处定义。这一层判的是
    角色判不了的那一半 —— 代码分不出两个令牌是不是铸给了同一个人，但它**读得出**一条
    提议与一次批准上写的是不是同一个名字。
    """
    if resolution.outcome not in POLICY_APPROVAL_OUTCOMES:
        raise ReviewConflictError(
            f"approve_policy_change 只接受 "
            f"{sorted(o.value for o in POLICY_APPROVAL_OUTCOMES)!r} 这一种结果，而这一条"
            f"是 {resolution.outcome.value!r}。\n"
            "普通裁决走 resolve_review_case —— 两条路的角色要求不同，混用会让「谁批准"
            "的」这一格在某一侧变成不设防的。"
        )
    proposal = pending_proposal(store=store, case_id=resolution.case_id)
    if proposal is None:
        raise ReviewConflictError(
            f"{resolution.case_id!r} 上没有一条等着被批准的提议，所以这一条批准没有"
            "对象。一个字都没写。\n"
            "能被批准的只有 ``PROPOSE_POLICY_CHANGE`` 写下的那条提议，而它现在不在盘上"
            "（或者已经被批过了）。"
        )
    if proposal.by == resolution.by:
        raise ReviewConflictError(
            f"{resolution.case_id!r} 这一版是 {proposal.by!r} 提议的，而批准它的也是 "
            f"{proposal.by!r} —— 同一个人不能既提议又批准。\n"
            "§9 要的是「获授权者批准」，而这一格的全部价值就是**它不是一个代理能自己"
            "走到的位置**：一个人先提再批，那个位置当场变成他可以自己走到的，而两次"
            "调用各自都完全正常。\n"
            "换一个主体（另一枚 authorizer 令牌）来批这一条。"
        )
    return store.record_resolution(resolution, expected_version=resolution.expected_version)


def pending_proposal(*, store: ReviewCaseStore, case_id: str) -> HumanResolution | None:
    """这一件上**最后一条**提议。没有提议过就返回 ``None``。

    读的是只增不改的那张历史表，而不是案件上那一格：``ReviewCase.proposed_version``
    会在下一条决定时被清掉（那时「它在等什么」已经不再成立），而「第 13 版当初是谁提的」
    只有历史答得出来 —— 而两个人那一条判据要问的正是这件事。
    """
    proposals = [
        row
        for row in store.list_resolutions(case_id)
        if row.outcome in POLICY_PROPOSAL_OUTCOMES
    ]
    return proposals[-1] if proposals else None


def block_activation(
    *, store: ReviewCaseStore, scene_id: str, version: int
) -> ReviewCase | None:
    """这一版现在等不等一件待办的批准。等就返回那一件，否则 ``None``。

    ``control_scene(activate)`` 的闸门。判据本身在
    ``domain.review.awaiting_approval_for``（一处定义，纯函数），这一层只负责把那串
    案件凑出来。

    **按 ``scene_id`` 过滤**：``proposed_version`` 是一个场景内的序号，两个场景各自
    都有第 3 版。不过滤的话，A 场景的一件待办会拦下 B 场景的第 3 版 —— 而那一版
    看起来只是「激活被拒了」。
    """
    cases = tuple(case for case in store.list_cases() if case.scene_id == scene_id)
    return awaiting_approval_for(cases, version)


def _require_scene_version(scenes: SceneStore, *, scene_id: str, version: int | None) -> None:
    if version is None:
        # 构造不出来（``HumanResolution`` 的校验器保证提议必带版本），所以走到这里
        # 说明有人绕过了那一层 —— 报出来，不兜底。
        raise ConfigError("一条提议没有带它挂起的版本号。")
    if scenes.get_version(scene_id, version) is None:
        raise ConfigError(
            f"scene={scene_id!r} 读不到第 {version} 版，而这一条提议说要挂起它。\n"
            "提议挂在一版不存在的规则上，那一版**永远等不到批准** —— 而它在报告上"
            "只是一件挂着的事，没有任何一处会红。先写出那一版（add_scene_version），"
            "再提议挂起它。"
        )


def _scene_of(store: ReviewCaseStore, case_id: str) -> str:
    case = store.get_case(case_id)
    if case is None:
        raise ReviewConflictError(
            f"{case_id!r} 不在盘上，所以这一条决定没有对象。一个字都没写。"
        )
    return case.scene_id


# ------------------------------------------------------------------ 读
def flow(
    *,
    store: ReviewCaseStore,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> ReviewFlow:
    """一段窗口里的待办流水（§12 的恒等式就是 :attr:`ReviewFlow.reconciles`）。

    **案与事件都读全量**，窗口是这一层用纯函数切的：把窗口下推到存储会让「窗口内开的
    案」与「窗口内发生的事」按两条不同的查询取回来，而恒等式要的是同一批对象上的
    两个时刻 —— 一边按存储的顺序、一边按事件的顺序，差一件都读不出来。

    逐件读它的历史（``list_resolutions``）而不是一次全取：收件箱是一件一件处理的队列，
    件数是人手工处理的量级；为它加一个「全表扫事件」的端口动词，会让那五个动词里多出
    一个只为一个工具存在的读法。
    """
    end = utc_now() if window_end is None else window_end
    start = (end - timedelta(days=DEFAULT_FLOW_DAYS)) if window_start is None else window_start
    cases = store.list_cases()
    resolutions = tuple(
        row for case in cases for row in store.list_resolutions(case.case_id)
    )
    return inbox_flow(
        cases=cases, resolutions=resolutions, window_start=start, window_end=end
    )


def impact(
    scenes: SceneStore, *, scene_id: str, from_version: int, to_version: int
) -> SceneImpact:
    """任意两版之间的影响预览（§B20 逐字要的「读证据/**影响**」）。

    只是一次转发 —— 判据在 ``application/scenes.py::impact_between`` 上（一处定义）。
    这一层不重写它：两处各写一遍的形态是「工具读出来的影响」与「``add_scene_version``
    回执里的影响」各算各的，而两份报告都自洽。
    """
    return impact_between(
        scenes, scene_id=scene_id, from_version=from_version, to_version=to_version
    )


__all__ = [
    "DEFAULT_FLOW_DAYS",
    "approve_policy_change",
    "block_activation",
    "case_for",
    "claim_case",
    "flow",
    "impact",
    "open_case",
    "open_for",
    "pending_proposal",
    "resolve_case",
]

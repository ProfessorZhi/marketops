"""反馈影响预览 —— 一条反馈**若被采纳**会改到什么（§B23）。

## 权威句

``marketops/security/catalog.py`` 的 ``preview_feedback_impact`` 逐字：

    一条反馈**若被采纳**会改到什么：哪些 Run、草稿、对象与策略落在射程内，
    各自会被拿去对着什么重新判定，以及**哪些改动在这一层根本不可用**。
    最后那一格是承重的：它把「做不到」与「没人想过」分开。
    零 provider、零写入 —— 预览不花钱、不留痕

## 最后那一格为什么承重，以及它在这里怎么落

「做不到」与「没人想过」在报告上最容易同形：两者都表现为「这一格是空的」。分开
它们的唯一办法是**给两张不同的清单**：

* :attr:`FeedbackImpact.not_usable` —— **有理由的做不到**。每一条都说得出是哪一条
  判据挡着、以及**要改它得走哪条路**。它不是「暂不支持」，是一句能被核对的话。
* :attr:`FeedbackImpact.not_reported` —— **这一层答不上来**。每一条都点名**谁答得
  上**（哪个模块、哪一片），与 ``application/not_reported.py`` 那一格逐字同一条。

两张清单**不互斥**，而且各自有不同的下一步：前者 ⇒ 换一条路或找一个人，后者 ⇒
去问那个答得上的地方。

## 零 provider 是**构造**，不是自觉

这一层**拿不到**任何检索/抓取端口 —— 签名里没有能收下它的格子，它只用
:class:`~marketops.domain.feedback.FeedbackEvent` 这一个对象与两张常量表。判据是
``tests/test_feedback_is_offline.py`` 里那把 AST 量具（照
``tests/test_mcp_launch_is_offline.py`` 那一族）。

它同样**不写一个字节**：整个模块里没有一次存储调用、没有一次 ``utc_now()`` ——
``at`` 由调用方给（与 ``launch_preview`` 逐字同一条：一次读数里有好几处判定，
各读一次时钟会让这份读数自相矛盾）。「不花钱」这句话在这一层因此不是一句承诺：
**它没有能花钱的东西**。

## 它**不报**射程里有几个 Run

目录行要的是「哪些 Run、草稿、对象与策略落在射程内」，而这一层能答的只有**射程
本身**（:attr:`FeedbackImpact.applied_at` 那一格）与**落在里面的东西是哪几类**
（:attr:`FeedbackImpact.in_range`）。真正枚举它们要一份按作用域查 Run 与草稿的读法，
而那个端口今天不存在（B01 的 ``launch_record`` / ``workstream`` 是它的落点）。

一个数不出来的数**不填一个大概的**：``0`` 会被读成「这条反馈影响不到任何东西」，
而它与「没查」在报告上同形 —— 那正是这一层存在的理由的反面。所以它进
:attr:`FeedbackImpact.not_reported`，并且那一条点名缺的是哪一段读法。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..domain.feedback import (
    POLICY_CELLS,
    PROTECTED_CELL,
    SCOPE_ORDER,
    VERSION_PRODUCING_KINDS,
    FeedbackEvent,
    FeedbackKind,
    Ring,
    Scope,
    ScopeKind,
    protected_of,
)
from .not_reported import NotReported


class ImpactAction(str, Enum):
    """这一条反馈**下一步**该做什么。**五档，逐条对应五种 ``kind`` 的处置。**

    与 ``LaunchPreview.next_action`` 同一条理由：它是由 :func:`feedback_impact`
    **算**出来的 —— 调用方填不进这一格。填得进的那一版会在某次改动之后与
    :attr:`FeedbackImpact.not_usable` 分家，而报告上两者说的必须是同一件事。

    **为什么不是三档（按环）。** 环是粗的：``Ring.FAST`` 底下那两类的下一步不一样
    —— 一条 ``run_instruction`` 只改本 Run/下一 Run 的计划，一条 ``workstream_policy``
    要预览后批准成一版。按环报，读的人会把后者读成「不用批准」。
    """

    #: 事实更正：改动走**已有**的事实/证据路径，这一层只写事件。
    FACT_PATH = "fact_path"
    #: 一轮指示：进本 Run/后续 Run 的计划（在途 attempt 保持冻结）。**不成版。**
    THIS_ROUND_ONLY = "this_round_only"
    #: 它可以变成一条候选 —— 但那也只是**提案**，不是生效。
    CANDIDATE_THEN_PATCH = "candidate_then_patch"
    #: 它可以成为一版 scoped policy 的提案，而**批准是另一个人**的事
    #: （既有的 ``approve_policy_change``）。
    PATCH_THEN_ANOTHER_PERSON = "patch_then_another_person"
    #: 它宽到（或碰到了受保护的那几格到）这一层做不到的地步：要的是一个**人**
    #: 去开一件复核案，而不是这条环里的一步。**这一个盖过上面那四个。**
    A_PERSON_OPENS_A_REVIEW = "a_person_opens_a_review"


#: 五种 ``kind`` → 它下一步那一步。**由** ``domain.feedback.DISPOSITION``
#: 那五行散文**读出来**的落点（逐条对应，判据在 ``tests/test_feedback_domain.py``）。
ACTION_OF_KIND: Mapping[FeedbackKind, ImpactAction] = {
    FeedbackKind.FACT_CORRECTION: ImpactAction.FACT_PATH,
    FeedbackKind.RUN_INSTRUCTION: ImpactAction.THIS_ROUND_ONLY,
    FeedbackKind.WORKSTREAM_POLICY: ImpactAction.PATCH_THEN_ANOTHER_PERSON,
    FeedbackKind.EXPERIENCE_CANDIDATE: ImpactAction.CANDIDATE_THEN_PATCH,
    FeedbackKind.BUSINESS_DEFINITION: ImpactAction.PATCH_THEN_ANOTHER_PERSON,
}


#: 每一档作用域里**落着哪一类东西**，以及它们会被拿去对着什么重新判定。
#:
#: 写成表而不是一串 ``if``：这张表是三档作用域各自的**内容**，而它在报告里要与
#: ``applied_at`` 那一格逐字对得上。散在各处 ``if`` 里的那一份会先漂移。
SCOPE_CONTENT: Mapping[ScopeKind, tuple[str, str]] = {
    ScopeKind.OBJECT: ("对象", "那个对象自己已有的证据与事实行"),
    ScopeKind.RUN: ("Run", "那一轮 Run 的 RunDelta 与它折进池子的那些行"),
    ScopeKind.WORKSTREAM: ("工作流", "那个工作流这一版的声明，以及它开出来的那些 Run"),
    ScopeKind.SEGMENT: ("细分段", "那一段的成员名单与它们各自的判定"),
    ScopeKind.SCENARIO: ("场景", "那一版 Scene 声明的格子，以及挂在它上面的工作流"),
    ScopeKind.GEO: ("地理范围", "在那个范围里的对象，与声明覆盖它的那些场景"),
    ScopeKind.WORKSPACE: ("工作区", "本部署经营的全部对象与全部策略版本"),
}

#: 一格策略**在盘上落在哪**、以及**谁判**（对着什么重新判定）。
#:
#: 只列可提补丁的常规格：受保护的五格进的是 :attr:`FeedbackImpact.not_usable`
#: —— 它们在这一层没有「会被拿去对着什么重新判定」这回事，因为它们改不了。
CELL_READING: Mapping[str, tuple[str, str]] = {
    "scenario": ("SceneVersion.scenario —— 这个场景做哪一类生意", "get_scene_impact"),
    "product": ("SceneVersion.product —— 这个场景卖什么", "get_scene_impact"),
    "source_plan": ("SceneVersion.source_plan —— 从哪些来源取", "get_scene_impact"),
    "budget": ("SceneVersion.budget —— 一行的额度上限", "get_scene_impact"),
    "refresh": ("SceneVersion.refresh —— 复核节奏", "get_scene_impact"),
    "outreach": ("SceneVersion.outreach —— 触达节奏（连同它的标定状态）", "get_scene_impact"),
}


@dataclass(frozen=True)
class Rejudgement:
    """射程里的一类东西，以及它会被拿去**对着什么**重新判定。

    ``judged_by`` 点名的是**读得到那个结论的地方**（一条工具或一个模块），不是
    一个抽象的说法：读的人的下一个问题永远是「那我去哪看」。
    """

    #: 射程里的哪一类东西（「对象」「工作流」「策略格子」……）。
    item: str
    #: 它会被拿去对着什么重新判定。
    reading: str
    #: 谁答得上（一条工具名或一个模块名）。
    judged_by: str

    def as_dict(self) -> dict[str, str]:
        return {"item": self.item, "reading": self.reading, "judged_by": self.judged_by}


@dataclass(frozen=True)
class FeedbackImpact:
    """一条反馈若被采纳的那份读数。**这是 ``preview_feedback_impact`` 的全部内容。**"""

    feedback_id: str
    kind: FeedbackKind
    ring: Ring
    at: datetime
    #: 这条反馈**自己**说的作用域。
    proposed_scope: Scope
    #: **这一次预览问的**那一格。缺省等于 :attr:`proposed_scope`。
    applied_at: Scope
    #: 从 :attr:`scope` 到 :attr:`applied_at` 跨过的每一档（空 = 没有扩大）。
    widening: tuple[ScopeKind, ...]
    #: 射程里的东西会被拿去对着什么重新判定。
    in_range: tuple[Rejudgement, ...]
    #: 这一条反馈**说不出话**的那几处（原话在 :attr:`proposed_scope` / :attr:`at` 上）。
    not_usable: tuple[NotReported, ...]
    #: 这一层答不上来的那几项，各点名谁答得上。
    not_reported: tuple[NotReported, ...]
    #: 下一步。**在 :func:`feedback_impact` 里算出来**（见 :class:`ImpactAction`）。
    next_action: ImpactAction

    @property
    def subject(self) -> str:
        """这一份读数的主语：``feedback:fb-xxxx``。

        与 ``launch_subject`` / ``SubjectKind.subject`` 同一形状：**格式只有这一处
        定义**。它与 ``domain.feedback.feedback_source`` 是同一个写法 —— 那边是
        「这一份补丁是从哪条反馈来的」，这边是「这一份读数是关于哪条反馈的」，两者
        必须是同一个字符串，否则同一件事在报告里有两个名字。
        """
        return f"feedback:{self.feedback_id}"

    @property
    def widened(self) -> bool:
        """这一次预览是不是在一个**比反馈自己说的更宽**的作用域上问的。"""
        return bool(self.widening)

    def as_dict(self) -> dict[str, Any]:
        """写进报告的形状 —— 也是 MCP 只读面交出去的那一份。

        ``at`` 与 ``proposed_scope`` 一起带上（照 ``LaunchPreview.as_dict``）：这是一份**带
        时刻、带射程**的读数，缺了任何一格，「这条反馈到这一刻作用在哪」就没有可
        复核的锚点。
        """
        return {
            "subject": self.subject,
            "feedback_id": self.feedback_id,
            "kind": self.kind.value,
            "ring": self.ring.value,
            "at": self.at.isoformat(),
            "proposed_scope": self.proposed_scope.as_dict(),
            "applied_at": self.applied_at.as_dict(),
            "widened": self.widened,
            "widening": [kind.value for kind in self.widening],
            "wider_than_workspace": self.applied_at.kind is ScopeKind.WORKSPACE,
            "next_action": self.next_action.value,
            "in_range": [row.as_dict() for row in self.in_range],
            "not_usable": [
                {"item": item.item, "reason": item.reason} for item in self.not_usable
            ],
            "not_reported": [
                {"item": item.item, "reason": item.reason} for item in self.not_reported
            ],
        }


def widening_between(proposed_scope: Scope, applied_at: Scope) -> tuple[ScopeKind, ...]:
    """从 ``proposed_scope`` 到 ``applied_at`` **跨过**的那几档，从窄到宽。

    收窄不是一个「扩大」，所以从宽到窄的那些档不进这一格 —— 它们会在报告里被读成
    「扩大了」，而那句话是反的。这一格的全部内容是「有没有比反馈自己说的更宽」。
    """
    start, end = proposed_scope.width, applied_at.width
    if end <= start:
        return ()
    return SCOPE_ORDER[start + 1 : end + 1]


def feedback_impact(
    *,
    event: FeedbackEvent,
    at: datetime,
    applied_at: Scope | None = None,
    changes: Sequence[str] = (),
) -> FeedbackImpact:
    """一条反馈若被采纳的那份读数。**纯函数：不读时钟、不碰存储、不花一次调用。**

    Args:
        event: 那一条反馈。
        at: 这一份读数的时刻。**必填参数**，理由见模块 docstring。
        applied_at: 这一次预览问的作用域。缺省是 :attr:`FeedbackEvent.proposed_scope`
            —— 反馈**自己**说的那一格。一个把缺省填成工作区的实现，会把每一条
            预览都变成一份「影响全局」的读数，而它此后会被当成一条有人主张过的
            全局规则读回去。
        changes: 有人在考虑改的**策略格子**（:data:`POLICY_CELLS` 里的名字）。
            缺省空 —— 那时这一份读数只回答环与射程那两件事。

    Returns:
        一份 :class:`FeedbackImpact`。**它一个字节都不写**，也一个 provider 都不碰。
    """
    applied = event.proposed_scope if applied_at is None else applied_at
    guarded = protected_of(changes)
    unknown = tuple(cell for cell in changes if cell not in POLICY_CELLS)
    blocked = _blocked(applied=applied, guarded=guarded, unknown=unknown)
    return FeedbackImpact(
        feedback_id=event.feedback_id,
        kind=event.kind,
        ring=event.ring,
        at=at,
        proposed_scope=event.proposed_scope,
        applied_at=applied,
        widening=widening_between(event.proposed_scope, applied),
        in_range=_in_range(applied=applied, changes=changes, guarded=guarded),
        not_usable=_unusable(
            kind=event.kind,
            applied=applied,
            guarded=guarded,
            unknown=unknown,
            blocked=blocked,
        ),
        not_reported=_not_reported(applied=applied, changes=changes),
        next_action=_next_action(kind=event.kind, blocked=blocked),
    )


def _blocked(*, applied: Scope, guarded: tuple[str, ...], unknown: tuple[str, ...]) -> bool:
    """这一次预览问的那件事，这一层**做不做得到**。

    一个函数，两个读者（:func:`_unusable` 与 :func:`_next_action`）——
    正是为了它们不各自再判一次。两处各判一次的漂移表现是：``not_usable`` 里写着
    「做不到」，而 ``next_action`` 那格写着一个这一层做不到的下一步。
    """
    return (
        applied.kind is ScopeKind.WORKSPACE or bool(guarded) or bool(unknown)
    )


def _in_range(
    *, applied: Scope, changes: Sequence[str], guarded: tuple[str, ...]
) -> tuple[Rejudgement, ...]:
    """射程里的东西各会被拿去对着什么重新判定。

    **受保护的那几格不进这一格** —— 它们改不了，所以「它们会被拿去对着什么重新
    判定」这句话不成立。把它们一起列出来会让报告里出现一张「会重新判定」的清单，
    而其中几项根本不在这条路上。
    """
    rows: list[Rejudgement] = []
    noun, reading = SCOPE_CONTENT[applied.kind]
    rows.append(
        Rejudgement(
            item=f"{noun}（{applied.label}）",
            reading=reading,
            judged_by=(
                "application/scenes.py::impact_between（工具 get_scene_impact）"
                if applied.kind in {ScopeKind.SCENARIO, ScopeKind.WORKSPACE}
                else "这一层没有按这个作用域枚举的读法 —— 见 not_reported"
            ),
        )
    )
    for cell in POLICY_CELLS:
        if cell in guarded or cell not in changes:
            continue
        where, judged_by = CELL_READING[cell]
        rows.append(
            Rejudgement(
                item=f"策略格子 {cell}",
                reading=f"下一版声明里的 {where}",
                judged_by=f"application/scenes.py::impact_between（工具 {judged_by}）",
            )
        )
    return tuple(rows)


def _unusable(
    *,
    kind: FeedbackKind,
    applied: Scope,
    guarded: tuple[str, ...],
    unknown: tuple[str, ...],
    blocked: bool,
) -> tuple[NotReported, ...]:
    """**有理由的做不到**，逐条点名那条判据与逃逸通道。

    四类，对应四件**不一样**的事：

    * 这一**类**的改动不走这条环（事实更正走既有的事实路径；一轮指示只改下一轮的
      计划）—— 处置是**换一条路**；
    * 它**产生一版**，而这一层只会提案，批准是另一个人 —— 处置是**去那一个人那里**；
    * 这个**射程**这一层到不了（工作区级）—— 处置是**找一个人开案**；
    * 这一**格**受保护（或名字根本不是策略格子）—— 处置同上，但理由完全不同。

    四件事的下一步各不相同，所以它们是分开的几条，而不是一句「不可用」。
    """
    rows: list[NotReported] = []
    if kind is FeedbackKind.FACT_CORRECTION:
        rows.append(
            NotReported(
                item="改用事实更正那条路",
                reason=(
                    "这一层的出口是**策略版本**，而更正一条事实走的是**已有**的"
                    "事实/证据路径（put_evidence → application/evidence_refs.py）。"
                    "让一条事实更正长出一条策略补丁，等于给同一条事实造出第二条"
                    "生效路径 —— 两条路径分家的那天，「这条事实改了没有」会有两个答案。"
                ),
            )
        )
    if kind is FeedbackKind.RUN_INSTRUCTION:
        rows.append(
            NotReported(
                item="改用眼前这一轮那条路",
                reason=(
                    "它改的是**眼前这一轮**的行为，有效期就是这一轮（处置逐字："
                    "「仅未来未派发的本 Run/后续 Run 计划；在途 attempt 保持冻结」）。"
                    "写进策略版本会让一次临时指示在下一轮静默地继续生效 —— 而它在"
                    "报告上只是一条已经生效的规则。"
                ),
            )
        )
    if kind in VERSION_PRODUCING_KINDS:
        rows.append(
            NotReported(
                item="自己把这一版生效",
                reason=(
                    "这一层只到**提案**为止。批准走的是**既有**的 "
                    "application/review.py::approve_policy_change（MCP 侧 "
                    "adapters/mcp_server/review.py，角色 _AUTHORIZE），而且**必须是"
                    "另一个人**。这一片不新开第二条批准路径 —— 一开，「谁批准的这一版」"
                    "就有两个答案。"
                ),
            )
        )
    if applied.kind is ScopeKind.WORKSPACE:
        rows.append(
            NotReported(
                item="在工作区这一层生效",
                reason=(
                    "目录行逐字「**自然语言永远不改全局策略**」。一个工作区级的"
                    "改动要的是一个**人**去开一件复核案，走既有的 "
                    "approve_policy_change（要 authorizer 角色、要另一个人）。"
                    "这一片不新开第二条批准路径。"
                ),
            )
        )
    if guarded:
        where = "；".join(f"{cell}（{PROTECTED_CELL[cell]}）" for cell in guarded)
        rows.append(
            NotReported(
                item=f"改受保护的那几格：{list(guarded)}",
                reason=(
                    f"这五样任何一条反馈路径都不得**自动**扩大。它们落在：{where}。"
                    "所以这不是「校验后放行」，而是这些改动在这一层根本构造不出来。"
                    "要改它们的是一个**人** —— 由他去开一件复核案，而不是这条环里的"
                    "一步。"
                ),
            )
        )
    if unknown:
        rows.append(
            NotReported(
                item=f"改这几格：{list(unknown)}",
                reason=(
                    f"它们不是策略格子（可选的是 {list(POLICY_CELLS)}）。"
                    "自由文本的格子名读不出「会改到什么」，所以这一层不替它猜一个 —— "
                    "也叫不出下一步，所以 next_action 停在「要一个人看」。"
                ),
            )
        )
    return tuple(rows)


def _next_action(*, kind: FeedbackKind, blocked: bool) -> ImpactAction:
    """下一步。**由** :func:`_blocked` **那一处判定算出来**，不另写一份。

    算而不是填：填进来的那一格会在某次改动之后与 :attr:`FeedbackImpact.not_usable`
    分家，而报告上两者说的必须是同一件事。

    ``blocked`` 盖过逐类的处置：这一次预览问的那件事这一层做不到时，
    「这一类通常怎么走」这句话就不再回答「**这一条**下一步是什么」。
    """
    if blocked:
        return ImpactAction.A_PERSON_OPENS_A_REVIEW
    return ACTION_OF_KIND[kind]


def _not_reported(
    *, applied: Scope, changes: Sequence[str]
) -> tuple[NotReported, ...]:
    """这一层答不上来的那几项。**每一条都点名谁答得上。**"""
    rows = [
        NotReported(
            item="射程里到底有几个 Run、几份草稿",
            reason=(
                "按作用域枚举 Run 与草稿要一份**按作用域查 Run 的读法**，而那个端口"
                "今天不存在（它的落点是 B01 的 launch_record / workstream）。"
                "给一个数不出来的数，会让「零个」与「没查」在报告上同形 —— 而后者"
                "正是这一层存在的理由的反面。"
            ),
        ),
        NotReported(
            item="这一步要花多少钱、动多少预算",
            reason=(
                "成本是**编好的计划**那一层的读数（application/plan_preview.py 与 "
                "application/run_preview.py 报的是它）。这一层拿不到计划，也不替它"
                "估一个数。"
            ),
        ),
        NotReported(
            item="下一版声明具体会变成哪几个值",
            reason=(
                "那要一份**已经写下的下一版**（两版比对走 application/scenes.py::"
                "impact_between，工具 get_scene_impact）。预览时它还不存在 —— 它由 "
                "propose_policy_patch → 复核 → 批准那一步才产生。"
            ),
        ),
    ]
    if not changes:
        rows.append(
            NotReported(
                item="这一次没有点名任何策略格子，所以「会改到哪几格」是空的",
                reason=(
                    "空与「它什么都不改」同形。要读后一种结论，请点名 changes；"
                    "不点名时这一份读数只回答**环**与**射程**那两件事。"
                ),
            )
        )
    if applied.kind in {ScopeKind.SEGMENT, ScopeKind.SCENARIO, ScopeKind.GEO}:
        rows.append(
            NotReported(
                item=f"{applied.kind.value} 这一层的成员是怎么划出来的",
                reason=(
                    "这一层只拿得到作用域那一对值（kind + ref），拿不到「谁落在里面」"
                    "的读法。成员名单是**场景声明**那一侧的事（SceneVersion."
                    "admissions / region，工具 get_scene），不是这条环算的。"
                ),
            )
        )
    return tuple(rows)


__all__ = [
    "CELL_READING",
    "SCOPE_CONTENT",
    "FeedbackImpact",
    "ImpactAction",
    "Rejudgement",
    "feedback_impact",
    "widening_between",
]

"""B16-b（3/3）：**编之前先看一眼** —— 这份计划长什么样，以及它凭什么长这样。

## 权威要求有两条，分属两片

* 执行计划 §B16 关闭条件 1 的后半逐字：「同一自然语言任务稳定编译可复现 plan
  digest；**preview 零 provider 调用**。」
* §B17 的 Run preview 逐字：「必须先报告：已有事实复用多少、预计补哪些缺口、
  计划可能调用哪些 provider、预算/stop、哪些歧义会进入 Review。」（同一条在
  §445 的启动流程里再出现一次：「preview ResearchContext / EffectiveResearchPlan
  / 已有池子复用 / 预算 / stop」。）

两者的**主语不同**，这一片只做前一条那半。§B17 说的是 **Run**（一次跑），它要读
共享池才知道「复用多少」；这一层说的是**计划**（一份派生物），它只有声明与上下文
两样输入，答不出任何需要读池子的问题 —— 所以那些问题**逐条**进
:attr:`PlanPreview.not_reported`，每条带一句为什么，而不是被静默省掉。

把答不上来的东西写进报告，与把它省掉，在报告上长得几乎一样：读者看到「preview
报告了五件事」，而其中三件是空的。这是本仓反复写下过的那一类失败（
``application/inputs.py`` 的 ``_NO_PRODUCER``、``domain/review.py`` 的
:data:`~marketops.domain.review.NOT_A_HUMAN_QUESTION`）。

## 零 provider 调用是**构造**，不是纪律

这一层的签名只收两样东西：一份声明（:class:`ProfileBundle`）与一份上下文
（:class:`ResearchContext`）。它拿不到端口、拿不到客户端、拿不到时钟 —— 所以
「preview 不花一次检索」在这里不是一句要人自觉的话，是**它没有可以调用的东西**。
这条要求在上游已经写过一遍（``domain/research_context.py`` 的模块 docstring：
「一个连端口都收不到的编译器花不了钱」），而这一层是那道论证的**第二个**受惠者：
它复用的 ``research_strategy`` 那四个模块同样只收声明。

于是「这条纪律有没有被违反」有一个可执行的答案：``tests/test_plan_preview.py``
扫这个模块的 AST（不许 import 适配层或网络、不许读盘、不许出现端口名），并且拿
一个**已知进口了端口**的模块当正控 —— 否则那条扫描会在「零个文件而报绿」的时候
看起来也是绿的。

## HOLD 是在**编之前**算出来的，不是接住一个异常

``StrategyCompiler.compile`` 在上下文有硬歧义时抛（§B16 关闭条件 4），而它的
docstring 里预告的处置是「``application.plan_preview`` 会把它落成一次 HOLD」。
**落成 HOLD 的机制不是接住那个异常**：这一层先在编之前问同一张表
（:func:`~marketops.domain.research_context.ambiguity_is_hard`），把那几条**逐条
点名**，硬的那些非空就**根本不去编**。

两个理由：

1. **一句「有讲不清的地方」不是一个能答的问题。** 异常里的那几行是给人读的散文，
   而 §253 要的是「哪些歧义会进入 Review」—— 要报出**哪几条**，就得在抛之前先算
   一遍。
2. **接住它等于承认两处对同一张表的读法可能不一致。** 表只有一张
   （``ambiguity_is_hard`` 查的那个 frozenset），``fingerprint_of`` 与本模块读的
   是同一份。真到了两处不一致的那一天，那不是一次 HOLD，那是这一层的判据该抓的
   错 —— 一个把内部不一致渲染成一次「请人回答」的实现，会让那种不一致**永远**只
   表现为「最近老是让人确认」，而没有任何一处红。
   这条一致性本身有判据：对每一个「少一维」的上下文，``compile`` 抛的那几条与
   ``preview_plan`` 判 HOLD 的那几条必须是同一批。

## 越界**不**落成 HOLD

超出批准范围（``ConfigError``）会照原样抛出去。理由写在
``domain/research_context.py`` 的模块 docstring 里：那是治理问题，不是「再问一次
用户」能解决的 —— 用户再说一遍也不会让 ``FR`` 落进一份 ``GB`` 的 profile 里。
两个都读成「这一次没能编出计划」的话，「等一个人回答」与「等一次治理变更」在报告
上同形。这条有判据。

## §253 那五项，逐项对账

==================================  =============================================
§253 里那一项                       这一层怎么处置
==================================  =============================================
已有事实复用多少                    答不上来 —— ``not_reported``（要读池子）
预计补哪些缺口                      答不上来 —— ``not_reported``（要读池子）
计划可能调用哪些 provider           **答一半**：``sources`` 报出计划开出来的每一条
                                    来源方法（逐条带族与「它能回答什么」）；而
                                    「哪条方法由哪个 provider 执行」在
                                    ``not_reported`` 里
预算 / stop                         **答一半**：``as_dict()["plan"]["budget"]``
                                    报出两格额度（``None`` = **没声明**，不是 0）；
                                    stop 条件不在这一份计划的十七格里，进
                                    ``not_reported``
哪些歧义会进入 Review               ``held`` 逐条报出；而「把它们落成待办」进
                                    ``not_reported``（``ReviewReason`` 今天没有
                                    这一类成员，见下）
==================================  =============================================

## 一条在本片发现的缝，写进报告而不是补掉

``domain/research_context.py`` 的模块 docstring 写着硬歧义「生成 ReviewCase」，
而全仓唯一的 ``ReviewCase`` 构造点是 ``application/review.py::case_for``，它的
``reason`` 是 :class:`~marketops.domain.review.ReviewReason` —— 一个只有
``identity_conflict`` / ``evidence_conflict`` 两个成员的闭集，两个都是**定级**冲突。
研究上下文的歧义今天**没有成员、也没有生产者**。

所以这一层能报出「要人回答的是哪几条」（``held``），不能声称它们已经在待办里。
补那一个成员是一件设计活（一条歧义被回答之后落成什么？谁批准？），不属于这一片；
把它**说出来**属于这一片 —— 否则这份 preview 会成为那句没有执行器的话的第二个
复读机。这条缝作为 ``not_reported`` 的一条进报告，有判据钉住它还在。

## 与 ``domain/`` 的分工

规则在 ``domain/research_plan.py``（一格的写者是谁、版本怎么算）与
``domain/research_context.py``（哪几维的歧义要停下来问人）。这里只做两件事：**先问
一遍那张表**，以及把一个结果渲染成报告的形状（``frozen dataclass`` + ``as_dict()``，
与 ``application/caps.py::TruncationDecision`` 同一个形状 —— 那是这一层记录进报告的
既有写法）。

``envelope`` 不作为参数收进来。``StrategyCompiler.compile`` 刻意要求调用方给一份，
因为「哪一份算批准」不该由编译层替人认；而 preview 这一侧，今天**唯一的** envelope
产出者就是 ``envelope_of(bundle)``（它自己的 docstring 这么写着），让调用方再传一遍
只会多一个「传了另一份批准范围」的入口 —— 那份 preview 会报出一份**没有人批准过**的
计划。将来出现 Scene 级的批准范围时，这个签名要长一个参数，那是一件要刻意做出来
的事，不是一个缺省值。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..domain.errors import ConfigError
from ..domain.profile import ProfileBundle
from ..domain.research_context import (
    Ambiguity,
    ResearchContext,
    ambiguities_of,
    ambiguity_is_hard,
    envelope_of,
)
from ..domain.research_plan import EffectiveResearchPlan
from ..domain.sources import SOURCE_METHODS, SourceMethod
from .not_reported import NotReported
from .research_strategy import research_compiler


class PreviewOutcome(str, Enum):
    """这一次预览的结局。**闭集，两个成员。**

    ``str`` 混入是为了报告：``as_dict()`` 里写出来的是 ``"hold"``，不是一个读的人
    要去别处查表的数字。

    为什么不是一个布尔：第三个结局迟早会来（「编得出来，但计划上有一格今天没有
    生产者」就是一个候选），而一个叫 ``ok`` 的布尔在那个时刻会把它读成 ``False``
    —— 于是三种不同的处置在调用方那里只剩一种。
    """

    #: 编出来了。``plan`` 非空，``held`` 为空。
    READY = "ready"
    #: 有几条硬歧义要人先回答。``plan`` 为空 —— 没编，也不假装编过。
    HOLD = "hold"


@dataclass(frozen=True)
class PlanPreview:
    """一次预览的全部产物。

    ``frozen`` 且只由 :func:`preview_plan` 构造（照 ``caps.TruncationDecision``）。
    ``__post_init__`` 把三条「两个字段各写一遍就会有一个自洽的假答案」的组合挡住：

    * 计划缺席与结局 ``HOLD`` 必须同时发生 —— 否则「这一次是没编出来，还是编出来了
      但没人读」在报告上答不出来；
    * ``HOLD`` 必须点名要人回答的是哪几条 —— 一条都说不出时，人拿不到能答的问题，
      而那正是「输入有点模糊，请确认」那种待办；
    * ``READY`` 必须有来源方法可报 —— 一份开了零条来源路的计划在这份 report 上会
      读成「这一次不需要找任何东西」。

    这三条**只有直接构造才能违反**，而 ``tests/test_plan_preview.py`` 逐个构造一遍：
    一个没人能违反的守卫与一个从不响的守卫，在报告上长得一样。
    """

    outcome: PreviewOutcome
    #: 会进 Review 的那几条：硬歧义，**逐条点名**。
    held: tuple[Ambiguity, ...]
    #: 记下来、但不拦路的那几条（软歧义）。关闭条件 4 的另一半：
    #: 软的可以在批准范围内编一份保守计划，但要让人看得出这次是在哪几处之上守的。
    recorded: tuple[Ambiguity, ...]
    #: 编出来的计划。``HOLD`` 时为 ``None``。
    plan: EffectiveResearchPlan | None
    #: 计划开出来的来源方法，按计划上的顺序。``HOLD`` 时为空。
    sources: tuple[SourceMethod, ...]
    not_reported: tuple[NotReported, ...]

    def __post_init__(self) -> None:
        if (self.plan is None) != (self.outcome is PreviewOutcome.HOLD):
            raise ValueError(
                "计划缺席与 HOLD 必须同时发生：HOLD 时不编（``plan is None``），"
                "编出来时 HOLD 不成立。\n"
                "两个字段各写一遍的话，「这一次到底是没编出来，还是编出来了但没人"
                "读」会有一个自洽的假答案 —— 而这两件事的处置不同。"
            )
        if self.outcome is PreviewOutcome.HOLD and not self.held:
            raise ValueError(
                "HOLD 必须点名要人回答的是哪几条。\n"
                "一条都说不出时，人拿到的是一句「输入有点模糊，请确认」—— 而他要答"
                "的问题本来是具体的：换哪一国、哪一批主体、哪个目标。"
            )
        if self.outcome is PreviewOutcome.READY and (self.held or not self.sources):
            raise ValueError(
                "READY 要满足两件事：没有硬歧义（``held`` 为空），以及报得出至少一条"
                "来源方法。\n"
                "一份开了零条来源路的计划在这份报告上会读成「这一次不需要找任何"
                "东西」，而那是 ``research_strategy`` 的失败关闭要拒的形状。"
            )

    def as_dict(self) -> dict[str, Any]:
        """写进报告的形状 —— 也是 MCP 只读面交出去的那一份。

        ``held`` / ``recorded`` 两栏在 ``READY`` 与 ``HOLD`` 下**都写出来**（其中
        一栏为空）：只写非空那一栏的话，读者分不清「这一次没有硬歧义」与「这一栏
        没人想过要填」。
        """
        return {
            "outcome": self.outcome.value,
            "held": [_ambiguity(item) for item in self.held],
            "recorded": [_ambiguity(item) for item in self.recorded],
            "plan": None if self.plan is None else _plan_block(self.plan),
            "sources": [
                {"id": item.id, "family": item.family, "answers": item.answers}
                for item in self.sources
            ],
            "not_reported": [
                {"item": item.item, "reason": item.reason} for item in self.not_reported
            ],
        }


def preview_plan(bundle: ProfileBundle, context: ResearchContext) -> PlanPreview:
    """这一次会按什么跑，编之前先看一眼。

    Args:
        bundle: 这一份部署的声明（``profiles/<id>/`` 读回来的那一个）。
        context: 客户要研究什么。

    Returns:
        一次预览。有硬歧义时 ``outcome`` 是 ``HOLD``、``plan`` 是 ``None``，并把
        那几条**逐条**写进 ``held``。

    Raises:
        ValueError: 这份声明读不出批准范围（``profile.yaml`` 没有声明块或没写
            ``country``）—— 那是 profile 写坏了，不是一次 HOLD。
        ConfigError: 请求落在批准范围之外（治理问题，见模块 docstring），或者某一格
            没有人写。两条都照原样抛：把治理问题渲染成「请人回答一次」会让一份
            越界的请求看起来只是待确认。
    """
    found = ambiguities_of(context)
    held = tuple(item for item in found if ambiguity_is_hard(item.dimension))
    recorded = tuple(item for item in found if not ambiguity_is_hard(item.dimension))

    if held:
        # 不编。**不接住 compile 的那个异常** —— 见模块 docstring：同一张表两处读法
        # 不一致是判据该抓的错，不是一次 HOLD。
        return PlanPreview(
            outcome=PreviewOutcome.HOLD,
            held=held,
            recorded=recorded,
            plan=None,
            sources=(),
            not_reported=NOT_REPORTED,
        )

    plan = research_compiler(bundle).compile(
        bundle=bundle, context=context, envelope=envelope_of(bundle)
    )
    return PlanPreview(
        outcome=PreviewOutcome.READY,
        held=(),
        recorded=recorded,
        plan=plan,
        sources=_methods_of(plan),
        not_reported=NOT_REPORTED,
    )


def _methods_of(plan: EffectiveResearchPlan) -> tuple[SourceMethod, ...]:
    """计划开出来的来源方法 → 注册表里的那几条。

    计划上那一格存的是 id（``domain/sources.py`` 的闭集），报告要的是 id **加上**
    「它能回答什么」—— 一个只有 id 的报告里，``search.engine.v1`` 对读的人是装饰。
    解析不出时抛，不留一个空的族：一个拼错的 id 在那份报告上会读成「这一条没有族」，
    而族是「来源族多样性」那个指标唯一的依据。
    """
    resolved: list[SourceMethod] = []
    for method in plan.source_methods:
        entry = SOURCE_METHODS.get(method)
        if entry is None:
            raise ConfigError(
                f"计划开出来的来源方法 {method!r} 不在注册表里，因此说不出它属于哪一族。\n"
                f"注册表是 {sorted(SOURCE_METHODS)}。\n"
                "这一格的值由 ``research_strategy`` 在装配期解析一次，走到这里还对不上"
                "说明两处对注册表的读法不一致 —— 拿一个空族糊过去，会让「来源族多样性」"
                "在一份拼错 id 的计划上照常报出一个数。"
            )
        resolved.append(entry)
    return tuple(resolved)


def _ambiguity(item: Ambiguity) -> dict[str, str]:
    """一条歧义在报告里的形状：哪一维、说不清的是什么。**不带等级**。

    等级由维度推出来（``ambiguity_is_hard``），两处各写一份的话，报告上会多出一处
    可以把硬歧义标软的地方 —— 与 ``Ambiguity`` 自己没有等级格是同一条理由。
    """
    return {"dimension": item.dimension.value, "detail": item.detail}


def _plan_block(plan: EffectiveResearchPlan) -> dict[str, Any]:
    """计划在报告里的形状。

    分四块：**它是谁**（摘要与两处出处）、**批准了什么**（硬的那九格，只读）、
    **这一次按什么跑**（可写的那八格 + 额度）、**凭什么**（每一格的出处与每一次
    覆盖）。``send_permissions`` 在硬块里也写出来 —— 它按 §7.3 恒为空，而「空」与
    「没人填」在报告上必须分得开：这一格是**声明**为空。
    """
    return {
        "digest": plan.digest,
        "fingerprint_digest": plan.fingerprint_digest,
        "scene_version": plan.scene_version,
        "declared": {
            "geo": plan.geo,
            "jurisdiction": plan.jurisdiction,
            "exclusions": list(plan.exclusions),
            "product_use": plan.product_use,
            "product_claims": list(plan.product_claims),
            "access_permissions": list(plan.access_permissions),
            "send_permissions": list(plan.send_permissions),
        },
        "budget": {
            "search_budget": plan.search_budget,
            "fetch_budget": plan.fetch_budget,
            # ``None`` = **没声明**，不是 0（「一次都不许」）。读者要知道哪几格没有
            # 值，否则一个 None 在他眼里就是一个 0 或一个很大的数。
            "undeclared": [
                name
                for name in ("search_budget", "fetch_budget")
                if getattr(plan, name) is None
            ],
        },
        "strategy": {
            "locales": list(plan.locales),
            "query_families": list(plan.query_families),
            "source_methods": list(plan.source_methods),
            "acquisition_modes": list(plan.acquisition_modes),
            "page_archetypes": [
                {"tier": archetype.tier, "patterns": list(archetype.patterns)}
                for archetype in plan.page_archetypes
            ],
            "role_functions": list(plan.role_functions),
            "role_priorities": list(plan.role_priorities),
            "decision_refs": list(plan.decision_refs),
        },
        "modules": [
            {"module_id": item.module_id.value, "revision": item.revision}
            for item in plan.modules
        ],
        "provenance": [
            {
                "field": item.field.value,
                "module_id": item.module_id,
                "revision": item.revision,
                "reason": item.reason,
            }
            for item in plan.provenance
        ],
        "overrides": [
            {
                "field": item.field.value,
                "by_module": item.by_module.value,
                "by_revision": item.by_revision,
                "overridden_module": item.overridden_module.value,
                "overridden_revision": item.overridden_revision,
                "reason": item.reason,
            }
            for item in plan.overrides
        ],
    }


#: §253 要求 preview 报告、而这一层答不上来的那几项。**逐条带理由**。
#:
#: 每一项都点名持有它的那一侧：前三项里有两项要读池子（§B17 的 Run preview 与
#: ``application/reuse.py``），provider 绑定全仓今天不存在（``domain/sources.py``
#: 的 id 刻意厂商中性），stop 条件不在 ``EffectiveResearchPlan`` 的十七格里，而
#: 「把 hold 落成待办」缺一个 ``ReviewReason`` 成员。
#:
#: 判据钉的是这一份的**内容**（``tests/test_plan_preview.py``）：省掉一条就是省掉
#: 一个「这一层答不上来」的交代，而省掉之后报告看起来只是短了一点。
NOT_REPORTED: tuple[NotReported, ...] = (
    NotReported(
        item="已有事实复用多少",
        reason=(
            "「池子里已经有什么、这一次复用了多少」要读共享池才知道，而这一层只有"
            "声明与上下文两样输入。持有它的是 §B17 的 Run preview 与 "
            "``application/reuse.py`` 的 ReuseDecision（同一个许可范围里同一个 URL "
            "只取一次）。"
        ),
    ),
    NotReported(
        item="预计补哪些缺口",
        reason=(
            "缺口 = 目标与池子的差集，两个词都要读池子。今天连「池子里有多少行」都"
            "没有生产者，所以这一项在报告上只能是一个数不出来的数 —— 报 0 会让"
            "「还没开始研究」读成「这一片没有缺口」。"
        ),
    ),
    NotReported(
        item="哪一条来源方法由哪个 provider 执行",
        reason=(
            "这一层报得出计划**开出来的来源方法**（``sources`` 那一格，逐条带族与"
            "「它能回答什么」），但「哪个方法由哪个 provider 执行」全仓今天没有映射"
            "（``domain/sources.py`` 的 id 刻意厂商中性，理由写在那个模块的头里）。"
            "在这一层编一个 provider 名字，会在换 provider 的那一天让历史读数变成"
            "另一种东西。"
        ),
    ),
    NotReported(
        item="停在哪（stop 条件）",
        reason=(
            "计划上有两格**额度**（``as_dict()['plan']['budget']`` 的 "
            "``search_budget`` / ``fetch_budget``），而 stop 条件是 Run 规格上的"
            "（「合格数上限」「预算用尽」「候选枯竭」是三个不同的结局），不在 "
            "``EffectiveResearchPlan`` 的十七格里。把它读成计划的一个字段，会造出"
            "一格没有任何声明来源的值 —— 而那一格决定这一次跑多久。"
        ),
    ),
    NotReported(
        item="把 hold 的那几条落成待办",
        reason=(
            "``held`` 报得出要人回答的是哪几条，而把它们开成 ReviewCase 的那条路"
            "今天不通：``ReviewReason`` 是闭集，只有 ``identity_conflict`` / "
            "``evidence_conflict`` 两个成员，两个都是**定级**冲突；研究上下文的歧义"
            "没有成员、也没有生产者。``domain/research_context.py`` 的模块 docstring"
            "写着硬的那一半「生成 ReviewCase」—— 那句话今天没有执行器，这一层不替它"
            "背书。补一个成员要顺带定下「歧义被回答之后落成什么」，那是 §B17/B01 "
            "的事。"
        ),
    ),
)


__all__ = [
    "NOT_REPORTED",
    "NotReported",
    "PlanPreview",
    "PreviewOutcome",
    "preview_plan",
]

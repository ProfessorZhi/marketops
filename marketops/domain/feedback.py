"""反馈环的三个对象与它们之间的判据（§B23）。

``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md`` §B23 与
``docs/PRODUCT_LINE_AND_SCENE_MODEL.md`` §9 落下了三件对象，这一份文件落下它们的
形状与**能被执行的**那几条判据。

## 三个环不是三种说法，是三张表

任务书逐字：

* **Fast loop** —— 一条反馈改了眼前这一轮的行为。
* **Fact correction** —— 更正一条事实（它走的是**已有**的事实/证据路径）。
* **Slow loop** —— 重复出现的经验 → ``ExperienceCandidate`` → ``PolicyPatch`` →
  复核 → 下一版 ``EffectiveScene``。

这三句话在这份文件里的落点是 :data:`RING_OF`：五种 ``kind`` 各自属于哪一个环，
是一张**读得出来、也数得出来**的表。它承重在下一跳上 ——
:data:`POLICY_PATCHABLE_KINDS` 由它**算出来**（只有慢环提得起补丁），而不是另外
写一份名单。两处各写一份的那天，一条事实更正会长出一条策略补丁，而两份报告各自
都自洽。判据是 ``tests/test_feedback_domain.py`` 里那条「并集等于五个 kind、并且
只有慢环的那几个能提补丁」。

## 两个「最窄」，一条判据

1. **建议的作用域**（:func:`resolve_scope`）默认取调用方**真的点了名**的那几格里
   最窄的一格；一格都没点名时**拒绝**，而不是退回工作区。退回工作区的形状是
   「一个人随口说了一句，系统替他把作用域填成了全局」—— 而 ``submit_feedback``
   的目录行逐字写着「**自然语言永远不改全局策略**」。
2. **受保护的五格**（:data:`PROTECTED_DIMENSIONS`）任何一条路径都不得自动扩大。
   「自动」这个词的落点是 :func:`protected_of`：任何一份 ``PolicyPatch`` 只要在
   ``changes`` 里点了这五格中的任何一格，**它连构造都构造不出来**。逃逸通道不是
   「换一条路」，而是**人**——由人开的那件复核案走既有的
   ``application/review.py::approve_policy_change``（要另一组角色、要另一个人）。

## 这一层不认识存储，也不认识时钟

:func:`utc_now` 不出现在本模块里：时刻一律由调用方给（``FeedbackEvent.created_at``
与 :class:`PolicyPatch.proposed_at` 都是必填字段，没有缺省）。一个能在模型里被
``utc_now()`` 填上的时刻，会让「这条反馈是什么时候说的」在两次构造之间换一个答案
—— 而它正是 ``get_feedback`` 要与 ``created_at`` 一起报出来的那一格。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator, model_validator

from .base import StrictModel
from .errors import ConfigError
from .util import SEP, check_canonical_id, sha256_text, to_utc

__all__ = [
    "CANDIDATE_ID_PREFIX",
    "FEEDBACK_ID_PREFIX",
    "PATCH_ID_PREFIX",
    "POLICY_CELLS",
    "POLICY_PATCHABLE_KINDS",
    "PROTECTED_CELL",
    "PROTECTED_DIMENSIONS",
    "RING_OF",
    "SCOPE_ORDER",
    "WIDEST_SCOPE",
    "CandidateState",
    "ExperienceCandidate",
    "FeedbackEvent",
    "FeedbackKind",
    "PolicyPatch",
    "PolicyPatchState",
    "Ring",
    "RiskClass",
    "Scope",
    "ScopeKind",
    "candidate_id_for",
    "candidate_source",
    "feedback_id_for",
    "feedback_source",
    "patch_id_for",
    "protected_of",
    "resolve_scope",
    "ring_of",
]


# ------------------------------------------------------------------ 五个 kind
class FeedbackKind(str, Enum):
    """一条反馈**是哪一类**。**闭集，五个成员**，逐字取任务书与 §B23。

    它是闭集而不是自由文本，因为「它属于哪一类」决定了它**走哪一个环** ——
    而那是 :data:`RING_OF` 回答的问题，不是读那句话的人当场猜的。
    """

    #: 更正一条事实。§9 逐字：「修正单个事实不等于发布全局规则」。
    FACT_CORRECTION = "fact_correction"

    #: 对**眼前这一轮**的指示。它改的是这一轮的行为，不产生策略版本。
    RUN_INSTRUCTION = "run_instruction"

    #: 对某个工作流/工作流范围的意见。它是一条**有范围**的策略意见，
    #: 而不是一条已经生效的规则。
    WORKSTREAM_POLICY = "workstream_policy"

    #: 一条**重复出现的经验**。慢环的入口。
    EXPERIENCE_CANDIDATE = "experience_candidate"

    #: 对**业务定义**的意见（谁是客户、哪个国家算在范围内、什么算排除）。
    #: 它可以被**记下来**（人说的话是真的发生了的事），但它不是一条可推广的
    #: 经验 —— 见 :data:`POLICY_PATCHABLE_KINDS` 与那五格受保护的定义。
    BUSINESS_DEFINITION = "business_definition"


class Ring(str, Enum):
    """一条反馈会**改变什么**。闭集，三个成员 —— 三个环各一个。

    它不是 ``kind`` 的别名：两个 ``kind`` 可以落在同一个环上（今天
    ``run_instruction`` 与 ``workstream_policy`` 都改眼前这一轮），而一个环也可以
    只有一个 ``kind``（慢环今天只有 ``experience_candidate`` 与
    ``business_definition``）。把它写成一个枚举而不是三句散文，是因为
    :data:`POLICY_PATCHABLE_KINDS` 要**算**它。
    """

    #: 改了眼前这一轮（或**下一批**）的行为。
    #:
    #: 它里面有两件**不一样**的事（见 :data:`DISPOSITION`）：一条 ``run_instruction``
    #: 只改本 Run/下一 Run 的计划，**不产生任何版本**；一条 ``workstream_policy``
    #: 在预览与批准之后**成为一版 scoped policy**（Workstream/Segment 新版，下一批
    #: 就用它）。把两者放在同一个环上，是因为快环与慢环的分界**不是「有没有版本」**——
    #: 那种分法会把 ``workstream_policy`` 判成慢环，而它恰恰是「快速用于下一批」那
    #: 一句要的那条路。真正的分界是**有没有 replay/evaluation**：一条 scoped 规则由
    #: 上级看过预览后直接批准，一条经验要先累积指标、回放、再评估。
    FAST = "fast_loop"

    #: 更正一条事实。走**已有**的事实/证据路径（``put_evidence`` 与
    #: ``application/evidence_refs.py``），本层不改它，也不另起一套。
    FACT = "fact_correction"

    #: 可重复的经验 → 候选 → 补丁 → 复核 → 下一版 ``EffectiveScene``。
    SLOW = "slow_loop"


#: 五种 ``kind`` → 它走哪一个环。**一张可执行的表**，不是三句散文。
RING_OF: Mapping[FeedbackKind, Ring] = {
    FeedbackKind.FACT_CORRECTION: Ring.FACT,
    FeedbackKind.RUN_INSTRUCTION: Ring.FAST,
    FeedbackKind.WORKSTREAM_POLICY: Ring.FAST,
    FeedbackKind.EXPERIENCE_CANDIDATE: Ring.SLOW,
    FeedbackKind.BUSINESS_DEFINITION: Ring.SLOW,
}

#: 五种 ``kind`` → 它**默认会被怎么处置**。取自
#: ``docs/PRODUCT_LINE_AND_SCENE_MODEL.md`` §9 那张五行表（那一段的说明是
#: 「系统再把它解释成不同处置，而不是把自然语言直接写成全局事实」）。
#:
#: **唯一一处不逐字的地方，以及为什么。** ``workstream_policy`` 那一格原文点名了
#: 批准的人（「由 <姓名> 批准」）。那一格住在**引擎**里，而 ``tests/test_profiles.py``
#: 的 ``_code_string_literals`` 把业务身份词当作「业务规则漏进引擎」在执行 ——
#: 它会逐字命中。放宽那张词表比这一格措辞严重得多（它是这条边界唯一在判事的
#: 执行器），所以这里把**人**收成**一级**（「由上级批准」）：这句处置要说的
#: 是「批准不在本层、由上一层的人做」，而那个人是谁属于 profiles/，不属于这一格。
#: 「另一个人」那一条判据不靠这句话 —— 它在 ``application/feedback.py`` 的
#: ``standing_of`` 里按复核记录判。
#:
#: **为什么环的表之外还要这一张。** 三个环是**粗**的：它回答「这一条改的是哪一层」。
#: 处置回答的是「所以下一步是什么」—— 而 ``Ring.FAST`` 底下那两类的下一步**不一样**
#: （一条指令改下一轮的计划，一条 scoped 规则要预览后批准成一版）。只有环的表，
#: 读的人会把两者读成一件事；只写散文，那五句话会在五处各有一半。
#:
#: 键是闭集，逐条与 :data:`RING_OF` 对齐 —— 加一个 ``kind`` 而忘了这一格，
#: ``tests/test_feedback_domain.py`` 里那条「两张表同键」的判据会响。
DISPOSITION: Mapping[FeedbackKind, str] = {
    FeedbackKind.FACT_CORRECTION: (
        "追加修订/反证，重评受影响对象；不自动泛化"
    ),
    FeedbackKind.RUN_INSTRUCTION: (
        "仅未来未派发的本 Run/后续 Run 计划；在途 attempt 保持冻结"
    ),
    FeedbackKind.WORKSTREAM_POLICY: (
        "preview 影响后由上级批准为 Workstream/Segment policy 新版"
    ),
    FeedbackKind.EXPERIENCE_CANDIDATE: (
        "累积运行指标与样本，作为低/中风险 PolicyPatch 候选"
    ),
    FeedbackKind.BUSINESS_DEFINITION: (
        "可能改变资格/边界，必须可信业务批准，不由模型自发发布"
    ),
}

#: 会**产生一版**的那几类。
#:
#: 这三类算不出来自任何别的表（「产生一版」是那五句散文里的一句意思，不是一处
#: 能查的字段），所以这一格是**写下来的** —— 并且配了两条判据钉住它。
#:
#: * ``tests/test_feedback_domain.py`` 逐条读 :data:`DISPOSITION` 的正文：这三类的
#:   处置里都有一处「新版 / PolicyPatch」，而另外两类一处都没有。改这张集合而不改
#:   那五句话（或反过来），判据当场响 —— 而两张表分家的表现，是一条
#:   ``workstream_policy`` 反馈提不出补丁、或者一条 ``fact_correction`` 提得出。
#: * 同一文件钉住 ``fact_correction`` 与 ``run_instruction`` **永远不在这里**：
#:   它们各自有一条不同的逃逸通道（见 :data:`POLICY_PATCHABLE_KINDS`）。
#:
#: 另有一层形状守卫在本模块 import 时跑：:data:`RING_OF` 与 :data:`DISPOSITION`
#: 必须**同键** —— 漏一个 ``kind`` 的表现是那张表读出来是空的，
#: 而它与「这一类没什么可说的」同形。
VERSION_PRODUCING_KINDS: frozenset[FeedbackKind] = frozenset(
    {
        FeedbackKind.WORKSTREAM_POLICY,
        FeedbackKind.EXPERIENCE_CANDIDATE,
        FeedbackKind.BUSINESS_DEFINITION,
    }
)

if set(RING_OF) != set(DISPOSITION):  # pragma: no cover - 形状守卫
    raise RuntimeError(
        "RING_OF 与 DISPOSITION 必须同键 —— 新加一个反馈类别时，"
        "「它走哪一个环」与「它默认怎么处置」是两件都要写下来的事。\n"
        f"  只在 RING_OF 里：{sorted(k.value for k in set(RING_OF) - set(DISPOSITION))}\n"
        f"  只在 DISPOSITION 里：{sorted(k.value for k in set(DISPOSITION) - set(RING_OF))}"
    )

#: 提得起 ``PolicyPatch`` 的那几类 —— **不是**「慢环那几类」。
#:
#: 这一格原来是按环算的（慢环 ⇒ 提得起补丁），而那是**错的**，错的正是
#: ``workstream_policy``：它落在快环上（因为它不进 replay/evaluation），但它的
#: 处置是「preview 影响后由上级批准为 Workstream/Segment policy **新版**」
#: —— 它产生一版。而 ``propose_policy_patch`` 的目录行逐字是「从一条候选或**一段有
#: 范围的反馈**提一份 ``PolicyPatch``」，那不是慢环的话，是「有范围」那一句。
#:
#: 所以判据换成**它会不会产生一版**（:data:`VERSION_PRODUCING_KINDS`）。
#: 「有没有版本」是这条边界真正在问的事，而它与「有没有 replay」是两条**正交**的轴：
#: 一条 scoped 规则有版本、没有回放；一条经验有回放、有版本；一条指令两个都没有。
#: 按环算会把其中一条判错，而它两侧各自都正常 —— 这正是要单独写出来的理由。
#:
#: 两条**具体**的拒绝各有一句话，写在 ``application/feedback.py`` 的
#: ``propose_patch`` 里（那里报给调用方），因为 `RefusedError` 的正文要向人解释
#: **换哪条路**，而那是编排层的信息：
#:
#: * ``fact_correction`` 走的是**已有**的事实/证据路径。让它长出一条策略补丁，
#:   等于给同一条事实造出第二条生效路径 —— 而两条路径分家的那天，「这条事实改了
#:   没有」会有两个答案。
#: * ``run_instruction`` 改的是**眼前这一轮**。它的有效期就是这一轮，写进策略
#:   版本会让一次临时指示在下一轮静默地继续生效。
POLICY_PATCHABLE_KINDS: frozenset[FeedbackKind] = VERSION_PRODUCING_KINDS


def ring_of(kind: FeedbackKind) -> Ring:
    """这一条反馈走哪一个环。**一处定义**（:data:`RING_OF` 的读法）。

    写成函数而不是让调用点各查一次表：表会漏项的方式是 ``KeyError``，而那是一个
    在这一层不该发生的崩溃；函数把「漏了」变成一条能读的拒绝。
    """
    ring = RING_OF.get(kind)
    if ring is None:  # pragma: no cover - 闭集，除非有人在 RING_OF 里删了一行
        raise ConfigError(
            f"{kind.value!r} 不在 RING_OF 里 —— 新加了一个反馈类别，而「它走哪一个环」"
            "还没有被想过。补一处：它改的是眼前这一轮、一条事实，还是一版策略。"
        )
    return ring


# ------------------------------------------------------------------ 作用域
class ScopeKind(str, Enum):
    """一条反馈**作用在哪一层**。闭集，七个成员，逐字取冻结表 §3.3。

    **次序是承重的**：:data:`SCOPE_ORDER` 从最窄排到最宽，而
    :func:`resolve_scope` 的缺省、预览里的「扩大了几档」都读它。一个一个地抄一遍
    次序（比如在一处写 ``if kind is WORKSPACE``）会让「哪一层更宽」在两处各有一
    份答案，而两者漂移的表现是：一条本该落在对象上的反馈被读成了落在工作区上。
    """

    #: 一个对象（账号、场地、说法……）。
    OBJECT = "object"
    #: 一轮 Run。
    RUN = "run"
    #: 一个工作流。
    WORKSTREAM = "workstream"
    #: 一个细分段（cohort / segment）。
    SEGMENT = "segment"
    #: 一个场景（Scene）。
    SCENARIO = "scenario"
    #: 一个地理范围。
    GEO = "geo"
    #: 整个工作区 —— **本部署经营的全部**。
    WORKSPACE = "workspace"


#: 从最窄到最宽。**唯一一处次序定义。**
SCOPE_ORDER: tuple[ScopeKind, ...] = (
    ScopeKind.OBJECT,
    ScopeKind.RUN,
    ScopeKind.WORKSTREAM,
    ScopeKind.SEGMENT,
    ScopeKind.SCENARIO,
    ScopeKind.GEO,
    ScopeKind.WORKSPACE,
)

#: 缺省的那一格，也就是 ``SCOPE_ORDER[0]``。
#:
#: **这一格的全部内容就是「默认值的宽度」这条判据。** 把这里改成
#: ``ScopeKind.WORKSPACE`` 会让每一条没说作用域的反馈都变成一条全局反馈 ——
#: 而 ``tests/test_feedback_domain.py`` 里那条 negative test 会当场红。
NARROWEST_SCOPE: ScopeKind = SCOPE_ORDER[0]

#: 最宽的那一格。**只有显式点名时才到得了**（:func:`resolve_scope` 的缺省路径
#: 永远不返回它 —— 一格都没点名时它拒绝，而不是退回这里）。
WIDEST_SCOPE: ScopeKind = SCOPE_ORDER[-1]


def _scope_kind_of(value: ScopeKind | str) -> ScopeKind:
    """一个 ``ScopeKind``，或一条说得出「有哪七个」的拒绝。

    ``ValueError`` 的原文（「'x' is not a valid ScopeKind」）在一份工具回执里
    答不出读的人的下一个问题，所以这里把七个成员点名报出来。
    """
    if isinstance(value, ScopeKind):
        return value
    try:
        return ScopeKind(value)
    except ValueError as exc:
        raise ConfigError(
            f"{value!r} 不是一个作用域。可选的是 "
            f"{[kind.value for kind in SCOPE_ORDER]} —— 从最窄排到最宽。"
        ) from exc


class Scope(StrictModel):
    """一条反馈**建议作用在哪一格**：一个类别加一个引用。

    它是一对值，不是两个散字段：``FeedbackEvent`` 上那两格（``scope_kind`` /
    ``scope_ref``）在盘上是扁平列（它们要被用来查），而在对象里是一格 ——
    「类别是 geo 而引用是一个账号 id」这种组合在构造时就报，不留到查询时。
    """

    kind: ScopeKind
    #: 这一格里的哪一个。``geo`` 时是国家码，``workspace`` 时是工作区本身。
    ref: str

    @field_validator("ref")
    @classmethod
    def _ref_is_named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "作用域没有说是哪一个：一个只有类别没有引用的作用域在盘上与"
                "「还没定」同形，而它此后会被当成一个已经定下来的范围读。"
            )
        return value

    @property
    def label(self) -> str:
        """报告里的那一格：``object:acc-0041``。"""
        return f"{self.kind.value}:{self.ref}"

    @property
    def width(self) -> int:
        """它在 :data:`SCOPE_ORDER` 上的位置。**比较宽度只走这一格。**"""
        return SCOPE_ORDER.index(self.kind)

    def as_dict(self) -> dict[str, str]:
        """写进报告的形状。三格一起给，读的人不必自己拼回 label。"""
        return {"kind": self.kind.value, "ref": self.ref, "label": self.label}


def _narrowest_named(
    *,
    target_ref: str | None,
    run_ref: str | None,
    workstream_ref: str | None,
) -> Scope | None:
    """调用方**真的点了名**的那几格里最窄的一格，或 ``None``。

    次序取 :data:`SCOPE_ORDER`，不另写一份：对象 → Run → 工作流。写死成
    ``if target_ref: ... elif run_ref: ...`` 会在这里造出第二处次序定义，而它漂移
    的表现是「同时点名了 Run 与工作流时，作用域落在工作流上」。
    """
    named: Mapping[ScopeKind, str | None] = {
        ScopeKind.OBJECT: target_ref,
        ScopeKind.RUN: run_ref,
        ScopeKind.WORKSTREAM: workstream_ref,
    }
    for kind in SCOPE_ORDER:
        ref = named.get(kind)
        if ref is not None and ref.strip():
            return Scope(kind=kind, ref=ref.strip())
    return None


#: 三格有对应引用字段的作用域，各自对应**哪个字段名**。
#:
#: 它是给报错文案用的，所以它必须与字段本身同源：写成
#: ``f"{kind.value}_ref"`` 在字段还叫 ``subject_ref`` 时恰好对得上（``object_ref``
#: 除外 —— 它从来就没对上过），改名 ``target_ref`` 之后三格全错。一条会把人的
#: 注意力引到不存在字段上的报错，比没有报错更坏：读的人会去 grep 那个名字，
#: 什么也找不到，然后以为是自己看错了版本。
_REF_FIELD_OF: Mapping[ScopeKind, str] = {
    ScopeKind.OBJECT: "target_ref",
    ScopeKind.RUN: "run_ref",
    ScopeKind.WORKSTREAM: "workstream_ref",
}


def resolve_scope(
    *,
    scope_kind: ScopeKind | str | None = None,
    scope_ref: str | None = None,
    target_ref: str | None = None,
    run_ref: str | None = None,
    workstream_ref: str | None = None,
) -> Scope:
    """一条反馈/候选建议的作用域。**缺省是最窄的那一格，不是最宽的那一格。**

    ## 这条判据的全部内容就是「默认值有多宽」

    三种输入，三个结果：

    * **点名了类别**（``scope_kind`` 非空）：照它办，``scope_ref`` 必填。
      点名是一个**人**的动作，它可以是宽的 —— 「这条意见讲的是整个场景」是一句
      说得通的话，而这一层不替说话的人改窄。
    * **没点名，但点名了引用**（``target_ref`` / ``run_ref`` / ``workstream_ref``
      有一个非空）：取**最窄**的那一个。同时点了 Run 与工作流时取 Run。
    * **一格都没点名**：**拒绝**。

    第三条是承重的那一条，所以要说清它**为什么不退回工作区**：``submit_feedback``
    的目录行逐字「**自然语言永远不改全局策略** —— 这一行只写事件」。一个在缺省时
    填 ``workspace`` 的实现，会让「有人随口说了一句」在盘上变成一条工作区级的
    建议 —— 而它此后会被当成一条有人主张过的全局规则读回去。两份报告各自都正常，
    没有一处会响。所以缺省的动作是**拒绝**，并且拒绝里点名要补哪一格。

    Args:
        scope_kind: 点名的作用域类别。``None`` 表示调用方没说。
        scope_ref: 与 ``scope_kind`` 配对的那一个引用。点名时必填。
        target_ref: 这一条讲的是哪个对象。
        run_ref: 这一条讲的是哪一轮 Run。
        workstream_ref: 这一条讲的是哪个工作流。

    Raises:
        ConfigError: 点了名却没说是哪一个；一格都没点名；点了名的作用域与点名的
            引用对不上（``object`` 配 ``acc-1`` 而 ``target_ref`` 是 ``acc-2``）。
    """
    named = _narrowest_named(
        target_ref=target_ref, run_ref=run_ref, workstream_ref=workstream_ref
    )
    if scope_kind is None:
        if named is None:
            raise ConfigError(
                "这条反馈没有说它作用在哪一格上，所以它没有可作用的作用域。\n"
                "  给一个：要么点名 scope_kind + scope_ref（可选的是 "
                f"{[kind.value for kind in SCOPE_ORDER]}），要么点出它讲的是哪一个"
                "对象 / 哪一轮 Run / 哪一个工作流 —— 那时作用域取**最窄**的那一格。\n"
                "  不替你填 workspace：目录行逐字「自然语言永远不改全局策略」。"
                "缺省填成工作区，会让「有人随口说了一句」在盘上变成一条工作区级的"
                "建议，而它此后会被当成一条有人主张过的全局规则读回去。"
            )
        return named

    kind = _scope_kind_of(scope_kind)
    ref = (scope_ref or "").strip()
    if not ref:
        raise ConfigError(
            f"点名了作用域是 {kind.value!r}，却没说是哪一个 —— "
            "一个只有类别没有引用的作用域在盘上与「还没定」同形。"
        )
    scope = Scope(kind=kind, ref=ref)
    if named is not None and kind in {
        ScopeKind.OBJECT,
        ScopeKind.RUN,
        ScopeKind.WORKSTREAM,
    } and scope != named:
        raise ConfigError(
            f"点名的作用域是 {scope.label}，而这一条点名的引用指向 "
            f"{named.label} —— 两个说法对不上。\n"
            "  同一个事实只允许有一处写法：作用域与那几个引用分家的那天，"
            "「这条反馈作用在哪」会有两个答案，而两处各自都正常。"
        )
    return scope


# ------------------------------------------------------------------ 受保护的五格
#: 一份 ``PolicyPatch`` 可以点名的**策略格子**。**闭集。**
#:
#: 前五格**受保护**（见下），后六格是可提补丁的常规格子。次序就是这张表的次序：
#: 报告里按它排列，于是「这份补丁动了什么」在两份报告之间可以逐格比对。
POLICY_CELLS: tuple[str, ...] = (
    # --- 受保护的五格 ---
    "country",
    "customer_definition",
    "hard_exclusion",
    "claim",
    "permissions",
    # --- 可提补丁的常规格 ---
    "scenario",
    "product",
    "source_plan",
    "budget",
    "refresh",
    "outreach",
)

#: 任何一条反馈路径都**不得自动扩大**的五样。措辞取任务书自己的词，不翻译 ——
#: 翻译一次就会在「是哪五样」这件事上出现第二种数法，而两种数法谁对是读不出来的。
#:
#: 「自动」的落点是 :func:`protected_of`：一份 ``PolicyPatch`` 只要在 ``changes``
#: 里点了这五格中的任意一格，它**连构造都构造不出来**（见 ``PolicyPatch`` 的
#: 校验器）。逃逸通道不是换一条路，而是**人** —— 由人开的那件复核案走既有的
#: ``application/review.py::approve_policy_change``（要 ``AUTHORIZER`` 角色、
#: 要另一个人）。这一片不新开第二条批准路径。
PROTECTED_DIMENSIONS: frozenset[str] = frozenset(
    {"country", "customer_definition", "hard_exclusion", "claim", "permissions"}
)

#: 每一格受保护的东西**在盘上落在哪**。写成一张表而不是一句立场，因为它要能被核对：
#: 读的人的下一个问题是「那我去改哪一格」，而「不得扩大 country」不回答它。
PROTECTED_CELL: Mapping[str, str] = {
    "country": "``SceneVersion.region`` —— 这个场景做哪些国家",
    "customer_definition": "``SceneVersion.admissions`` —— 谁算在范围内",
    "hard_exclusion": "``SceneVersion.exclusions`` —— 谁一定不在范围内",
    "claim": "``OutreachStore`` 的**已批准说法**（不是 ``SceneVersion`` 的一格）",
    "permissions": "``SceneVersion.role_policy`` —— 什么角色能做什么",
}

# 表与集合不许分家：少一格的时候要在这里当场断，而不是在运行时被读成一个「可以改」。
if not PROTECTED_DIMENSIONS <= set(POLICY_CELLS):  # pragma: no cover - 断言式守卫
    raise RuntimeError(
        "PROTECTED_DIMENSIONS 里有 POLICY_CELLS 装不下的格子 —— 两处各写一份的"
        "第一个后果就是「受保护的某一样」不在可选格子里，于是它既受保护又提不了。"
    )


def protected_of(changes: Iterable[str]) -> tuple[str, ...]:
    """这一串格子里**受保护**的那几格，按 :data:`POLICY_CELLS` 的次序。

    **一处定义。** ``PolicyPatch`` 的校验器读它，``propose_policy_patch`` 的拒绝
    也读它 —— 两处各写一份（一处 ``in`` 判断、一处列表推导）的形态是其中一处忘了
    更新，而那一处恰好是这条判据唯一被验的地方。

    次序按 ``POLICY_CELLS`` 而不是按入参：入参的次序由调用方随手给，而报告里
    「这份补丁碰到了哪几格受保护的」必须逐字可比。
    """
    wanted = set(changes) & PROTECTED_DIMENSIONS
    return tuple(cell for cell in POLICY_CELLS if cell in wanted)


# ------------------------------------------------------------------ 标识
#: 三个 id 的前缀，与 ``rc``（ReviewCase）/ ``coh``（Cohort）同一族形状。
FEEDBACK_ID_PREFIX = "fb"
CANDIDATE_ID_PREFIX = "ec"
PATCH_ID_PREFIX = "pp"


def feedback_id_for(*, workspace: str, principal: str, idempotency_key: str) -> str:
    """一条反馈的 id —— **它就是幂等键的哈希**。

    这样写有两个后果，而两个都是要的：

    * ``feedback_event`` 的 ``PRIMARY KEY (feedback_id)`` 与
      ``UNIQUE (workspace, principal, idempotency_key)`` **说的是同一件事**，所以
      重发同一句话撞的是同一条行，而不是先撞唯一索引再靠一次读回补救。
    * 「谁说的」进了原像。同一个 ``idempotency_key`` 被两个人用（两人各自的会话
      各起了一个序号）时，它们是两条反馈 —— 而不是一条被第二个人改写的反馈。
    """
    digest = sha256_text(
        SEP.join(("feedback", workspace, principal, idempotency_key))
    )[:16]
    return f"{FEEDBACK_ID_PREFIX}-{digest}"


def candidate_id_for(
    *, workspace: str, source_feedback_ids: Sequence[str], observation: str
) -> str:
    """一条候选的 id —— 由 ``(工作区, 来源反馈, 观察)`` 算出。**不含它想推广到哪。**

    `generalises` 故意**不在**原像里：一条已经写下的候选，「它想推广到哪个范围」
    被改掉而 id 不变，才是看得见的（撞主键 → 内容比对 → 报出来）。把它放进原像
    会让改了范围的那一次**静静地变成另一条候选**，而原来那条还挂在盘上等着被读。

    ``source_feedback_ids`` 取排序后的序列：同一批来源的两种写法是**同一条**候选。
    """
    digest = sha256_text(
        SEP.join(("candidate", workspace, *sorted(source_feedback_ids), observation))
    )[:16]
    return f"{CANDIDATE_ID_PREFIX}-{digest}"


def patch_id_for(*, workspace: str, source_ref: str, target_version: int) -> str:
    """一份补丁的 id —— 由 ``(工作区, 来源, 想产生的那一版)`` 算出。

    ``changes`` 不在原像里，理由与候选那一格逐字相同：来源与目标版本相同、而改的
    格子不同，是**同一份补丁的两种说法**，要靠撞主键 + 内容比对报出来，不能靠多出
    一条 id 静默地并存两条。
    """
    digest = sha256_text(
        SEP.join(("patch", workspace, source_ref, str(target_version)))
    )[:16]
    return f"{PATCH_ID_PREFIX}-{digest}"


def feedback_source(feedback_id: str) -> str:
    """一份补丁的来源写法：``feedback:fb-xxxx``。**一处定义。**"""
    return f"feedback:{feedback_id}"


def candidate_source(candidate_id: str, revision: int) -> str:
    """一份补丁的来源写法：``candidate:ec-xxxx@1``。**一处定义。**

    版本号在内，因为「这份补丁是从那一条候选的哪一版提的」是一句要写下来的话：
    候选是可变的，而补丁的来源引用必须是**当时那一版**。
    """
    return f"candidate:{candidate_id}@{revision}"


# ------------------------------------------------------------------ 反馈事件
class FeedbackEvent(StrictModel):
    """一个人说过的一句话（或指出的一条产物）。**只增不改。**

    §9 与冻结表 §3.3 逐字：这条记录**不可变**。它不是「当前的看法」，而是「他在
    那个时刻说了什么」—— 后来的人要判的是**当初到底说了什么**，所以原话逐字留着
    （``literal_feedback``），`get_feedback` 也只逐字回，不折成摘要。

    ## 四个引用与一个作用域

    ``target_ref`` / ``run_ref`` / ``workstream_ref`` 是这一条**讲的是谁**；
    ``proposed_scope`` 是这一条**建议作用在哪**。两者在 ``object`` / ``run`` /
    ``workstream`` 三格上必须一致（下面的校验器），因为分家之后「这条反馈作用在
    哪」会有两个答案，而两处各自都正常。

    ## 幂等键由调用方给

    ``idempotency_key`` 与它别处同款：服务端生成的话，「同一条反馈被提交了两次」
    与「他真的说了两遍」在盘上无法区分。同一个人重发同一句话拿回**同一行**，
    而不是把一条意见记成两条。
    """

    feedback_id: str
    workspace: str
    #: 说这句话的人。取自**令牌**，不取自任何参数（见 ``adapters/mcp_server``）。
    principal: str
    #: 调用方给的幂等键。**同一个 principal 的同一把键就是同一条反馈。**
    idempotency_key: str
    kind: FeedbackKind
    proposed_scope: Scope
    #: 原话，**逐字**。可以只有一条产物引用而没有原话 —— 见下面那条校验器。
    literal_feedback: str = ""
    #: 一条产物引用（哪份草稿、哪份材料、哪一版回执）。
    artifact_ref: str | None = None
    #: 在哪一次会话里说的。用于把同一段时间里的几条反馈读成一段对话。
    source_session: str = ""
    target_ref: str | None = None
    run_ref: str | None = None
    workstream_ref: str | None = None
    created_at: datetime

    @field_validator("feedback_id", "workspace")
    @classmethod
    def _ids_are_canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        """规范到 UTC：这一格会**参与比较**（``list_feedback`` 新的在前）。"""
        return to_utc(value)

    @model_validator(mode="after")
    def _something_was_said(self) -> "FeedbackEvent":
        """原话与产物引用**至少有一个** —— 两个都空的记录在盘上什么都没说。

        允许「只有一条产物引用」（``literal_feedback=""``，``artifact_ref`` 非空）：§B23
        的输入形状里那两样是并列的，而「把这份草稿留在 hold」这种反馈的正文就是
        它指的是哪一份草稿。反过来，只允许原话也一样成立。
        """
        if not self.literal_feedback.strip() and not (self.artifact_ref or "").strip():
            raise ValueError(
                "这条反馈既没有原话也没有产物引用 —— 它什么都没说。\n"
                "  空的记录在盘上与「他说了一句听不清的话」同形，而读的人只能猜。"
            )
        if not self.principal.strip():
            raise ValueError("principal 是空的 —— 谁说的必须是写下来的。")
        if not self.idempotency_key.strip():
            raise ValueError(
                "idempotency_key 是空的 —— 幂等键由**调用方**给：服务端生成的话，"
                "「同一条反馈被提交了两次」与「他真的说了两遍」在盘上无法区分。"
            )
        return self

    @model_validator(mode="after")
    def _the_scope_agrees_with_the_refs(self) -> "FeedbackEvent":
        """点名的作用域与点名的引用必须一致（``object`` / ``run`` / ``workstream``）。

        三格以外（``segment`` / ``scenario`` / ``geo`` / ``workspace``）没有对应的
        引用字段，所以只查这三格。``workspace`` 另有一条：它的引用必须是**本行自己
        的工作区** —— 一句「这条意见作用于工作区 B」而被写在工作区 A 的行上，
        此后会被 A 的读法当成 A 的事读回去。
        """
        paired = {
            ScopeKind.OBJECT: self.target_ref,
            ScopeKind.RUN: self.run_ref,
            ScopeKind.WORKSTREAM: self.workstream_ref,
        }
        expected = paired.get(self.proposed_scope.kind)
        if expected is not None and self.proposed_scope.ref != expected:
            raise ValueError(
                f"作用域写的是 {self.proposed_scope.label}，而这一条的"
                f"{_REF_FIELD_OF[self.proposed_scope.kind]} 是 {expected!r} —— 两个说法对不上。\n"
                "  同一个事实只允许有一处写法：分家的那天，「这条反馈作用在哪」会有"
                "两个答案，而两处各自都正常。"
            )
        if self.proposed_scope.kind is ScopeKind.WORKSPACE and self.proposed_scope.ref != self.workspace:
            raise ValueError(
                f"作用域写的是工作区 {self.proposed_scope.ref!r}，而这一行属于 "
                f"{self.workspace!r}。\n"
                "  跨工作区的作用域是一条读不回来的主张：它此后会被本工作区的读法"
                "当成本地的事读回去，而写下它的那一刻没有任何一处会响。"
            )
        return self

    def content(self) -> dict[str, object]:
        """这一条**由调用方给定**的内容，**不含时刻**。

        存储层拿它比对「同一个幂等键配的是不是同一句话」：比整行会把每一次**正常
        的重发**都报成冲突（重发当然发生在更晚的时刻），而那只让幂等键失效 ——
        它会从「同一条拿回同一行」退化成「每一条都报错」。
        """
        return self.model_dump(mode="json", exclude={"created_at"})

    @property
    def ring(self) -> Ring:
        """这一条走哪一个环。**读** :func:`ring_of`，不在这里重写一遍表。"""
        return ring_of(self.kind)


# ------------------------------------------------------------------ 经验候选
class CandidateState(str, Enum):
    """一条候选的**当前**状态。

    它是提案的状态，**不是**批准的状态 —— 没有任何一处把它当权威读，读它的是
    补丁的提案者与 ``list_experience_candidates``。
    """

    PROPOSED = "proposed"
    #: 被同 id 的更高版本取代了。
    SUPERSEDED = "superseded"
    #: 提它的人自己收回了。
    WITHDRAWN = "withdrawn"


#: 本片**没有生产者**的那两个状态，以及为什么。写成表而不是让它空着：一个只增
#: 不读的枚举成员与一个「还没实现」的成员在报告上同形，而这张表把它们分开。
#:
#: ``PROPOSED`` 不在表里 —— 它有生产者（``propose_experience_candidate``）。
CANDIDATE_STATES_WITHOUT_PRODUCER: Mapping[CandidateState, str] = {
    CandidateState.SUPERSEDED: (
        "取代走的是**同 id 的更高 revision**，而「哪一版取代了哪一版」由 "
        "``revision`` 那一列回答。今天没有任何一条工具写 revision > 1 —— "
        "一条候选的修订是慢环里紧接着的那一片（候选 → 补丁 → 复核 → 下一版 "
        "EffectiveScene 里的中间那一段），不是本片。"
    ),
    CandidateState.WITHDRAWN: (
        "收回是**提它的人**的动作，而本片没有订它的那条工具。"
        "在那之前，一条不再作数的候选由后面提出的补丁与复核案处置 —— "
        "它留在盘上、状态仍是 proposed，而这不是「它还有效」的意思。"
    ),
}


class RiskClass(str, Enum):
    """这一条推广**贵在哪**。闭集，三个成员。

    它**不判任何事**，而这不是「一个没人读的字段」：它由提案者写下、由
    ``propose_policy_patch`` 原样**带到补丁上**、由 ``list_policy_patches`` 报给
    **要签字的那个人**。一条推广的风险高低只有人能处置 —— 这一层的职责是把它
    送到那个人眼前，而不是替他把风险换算成一条规则。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExperienceCandidate(StrictModel):
    """``ExperienceCandidate`` —— 慢环的第一步。**它是提案，不是批准。**

    冻结表 §3.3 逐字：「候选不是批准」。这条边界靠**两件事**钉住，不靠一句注释：

    * **类型这一侧**：没有任何一处把它读成权限或规则（``ports/feedback.py`` 的
      读法只有清单与按 id 读；它不进任何一条权限判定的输入）。
    * **消费者那一侧**：它唯一的消费者是补丁的提案者，而补丁本身也**不生效**
      —— 生效要另一组角色、另一个人批准（``approve_policy_change``）。

    ## 它不能推广到整个工作区

    ``generalises`` 必须**比工作区窄**。一条「可重复的经验」要推广到本部署经营的
    全部范围，那不是一条经验 —— 那是一句「这里该换个做法」，而它要的是**一个人**
    去开一件复核案，不是在反馈环里被一条聚合出来的候选悄悄做到。这与
    :func:`resolve_scope` 缺省不填 ``workspace`` 是同一条判据的另一半。
    """

    candidate_id: str
    revision: int = Field(default=1, ge=1)
    workspace: str
    state: CandidateState
    #: 观察到了什么。**可重复**是「候选」这个词的全部内容 —— 一次性的意见是反馈，
    #: 不是候选。
    observation: str
    #: 它立在哪几份证据上。**可以为空**，而空与「读不出来」是两件事 ——
    #: ``list_experience_candidates`` 分开报（一条引用了取不到的材料上的候选，
    #: 与一条真的没证据的候选，在盘上不是同一件事）。
    evidence_refs: tuple[str, ...] = ()
    #: 它想推广到哪个范围。**不得是工作区**。
    generalises: Scope
    risk: RiskClass
    #: 这条候选是从哪几条反馈里看出来的。**不得为空** —— 没有反馈的候选是一个
    #: 想法，而想法在这条链上没有入口。
    source_feedback_ids: tuple[str, ...]
    proposed_by: str
    proposed_at: datetime
    updated_at: datetime
    note: str = ""

    @field_validator("candidate_id", "workspace")
    @classmethod
    def _ids_are_canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("proposed_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return to_utc(value)

    @model_validator(mode="after")
    def _generalises_short_of_the_workspace(self) -> "ExperienceCandidate":
        if self.generalises.kind is ScopeKind.WORKSPACE:
            raise ValueError(
                "这条候选想推广到**整个工作区**。\n"
                "  那不是一条可重复的经验，是一句「这里该换个做法」—— 它要的是一个"
                "**人**去开一件复核案，而不是在反馈环里被一条聚合出来的候选做到。\n"
                "  这与「缺省作用域不填 workspace」是同一条判据的另一半：一条反馈"
                "改不了一个工作区，一条候选也推广不到一个工作区。"
            )
        if not self.observation.strip():
            raise ValueError("observation 是空的 —— 没有观察的候选是一个名字。")
        if not self.source_feedback_ids:
            raise ValueError(
                "这条候选没有来源反馈。\n"
                "  候选是**从反馈里看出来**的可重复经验；没有来源的候选是一个想法，"
                "而想法在这条链上没有入口 —— 它此后会被当成「有依据的推广」读。"
            )
        if not self.proposed_by.strip():
            raise ValueError("proposed_by 是空的 —— 谁提的必须是写下来的。")
        return self

    def content(self) -> dict[str, object]:
        """调用方给定的内容，**不含 ``updated_at``**（理由同 ``FeedbackEvent.content``）。"""
        return self.model_dump(mode="json", exclude={"updated_at"})


# ------------------------------------------------------------------ 策略补丁
class PolicyPatchState(str, Enum):
    """一份补丁的**提案侧**状态。**闭集，两个成员。**

    **没有 ``approved``。** 这不是省了一个成员，而是这一片最要紧的一格：
    ``propose_policy_patch`` 的目录行逐字「**批准不在这里** —— 批准是
    ``approve_policy_change``，要 authorizer 角色、要**另一个人**」。批准是那条
    只增不改的历史表上一行 ``APPROVE_POLICY_CHANGE``，而「这一份补丁生效了没有」
    由 ``list_policy_patches`` 从**那件复核案**上**推**出来，不是往这里再写一个
    状态。往这里加一个 ``APPROVED``，就是这一片被要求不要新开的第二条批准路径。
    """

    #: 提出来了，还没挂到任何一件正在等批准的复核案上。
    PROPOSED = "proposed"
    #: 挂在一件 ``awaiting_approval`` 的复核案上，等另一个人批。
    AWAITING_APPROVAL = "awaiting_approval"


class PolicyPatch(StrictModel):
    """``PolicyPatch`` —— 慢环的第二步。**一份提案，永远不是一次生效。**

    ## 它改哪几格，以及那五格它碰不得

    ``changes`` 取 :data:`POLICY_CELLS` 里的名字。只要点了
    :data:`PROTECTED_DIMENSIONS` 里的任何一格，**这一份补丁连构造都构造不出来**
    —— 那是「任何一条路径都不得自动扩大它们」这句话在这一层的执行方式：不是一句
    校验后放行的提示，而是一个不存在的对象。

    ## 监控与回滚路径是必填的

    冻结表与目录行逐字：「**回滚路径与版本一起报**，是因为一个不能回滚的策略版本
    和一个能回滚的，在部署那一刻是两件事」。所以 ``rollback`` 与 ``monitoring``
    都不是缺省空串 —— 一份说不出怎么退回去的补丁，在报告上与一份能退回去的补丁
    长得一样，而它们在被点开批准的那一刻是两件事。

    ## 它挂在哪件复核案上

    ``review_case_id`` 指一件**已经在等批准**的复核案（``PROPOSE_POLICY_CHANGE``
    写下的那条提议）。``None`` 表示这一份还没挂上去，而没挂上去的补丁
    **批准不了** —— 批准只在案件上发生（``approve_policy_change`` 读的是案件上的
    ``pending_proposal``）。这一片不替它开案：``ReviewReason`` 是闭集两个成员，
    为一份补丁新加一个原因要动**共享判据**（``domain/review.py`` 的 ``INTAKE``），
    而那不是这一片的范围。所以这里的形状是「挂到**已有**的那件案上」，缺的那一段
    接线写在报告里。
    """

    patch_id: str
    revision: int = Field(default=1, ge=1)
    workspace: str
    state: PolicyPatchState
    #: 它归到了哪件复核案。``None`` ⟺ ``state is PROPOSED``（下面的校验器）。
    review_case_id: str | None = None
    #: 它从哪来：``feedback:fb-xxxx`` 或 ``candidate:ec-xxxx@1``。
    source_ref: str
    #: 它会改哪几格（:data:`POLICY_CELLS` 里的名字）。
    changes: tuple[str, ...]
    #: 它想产生哪一版 ``EffectiveScene``。
    target_version: int = Field(ge=1)
    #: 那一版带着什么监控。
    monitoring: str
    #: 那一版怎么退回去。
    rollback: str
    #: 由来源候选带过来的风险类别（来源是反馈时由提案者给）。
    risk: RiskClass
    rationale: str
    proposed_by: str
    proposed_at: datetime
    updated_at: datetime

    @field_validator("patch_id", "workspace")
    @classmethod
    def _ids_are_canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("proposed_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return to_utc(value)

    @model_validator(mode="after")
    def _the_cells_are_known_and_unprotected(self) -> "PolicyPatch":
        """``changes`` 非空、不重复、每一格都在闭集里，**且一格格受保护的都没有**。

        受保护那一条是承重的：见 :func:`protected_of` 与
        :data:`PROTECTED_DIMENSIONS` 的 docstring。拒绝里点名**是哪一格**、它在盘上
        落在哪里、以及**人要改它得走哪条路** —— 一句「不允许」不回答读的人的下一
        个问题。
        """
        if not self.changes:
            raise ValueError(
                "这份补丁一格都不改 —— 一份不说明它改什么的提案，在报告上与"
                "「还没想好」同形，而它会占着一件复核案的位置。"
            )
        if len(set(self.changes)) != len(self.changes):
            raise ValueError(f"changes 有重复项：{self.changes!r}")
        unknown = tuple(cell for cell in self.changes if cell not in POLICY_CELLS)
        if unknown:
            raise ValueError(
                f"{list(unknown)!r} 不是策略格子。可选的是 {list(POLICY_CELLS)}。\n"
                "  自由文本的格子名会让「这份补丁动了什么」在报告里读不出来 —— "
                "而它正是批准的人要看的唯一一格。"
            )
        guarded = protected_of(self.changes)
        if guarded:
            where = "\n".join(f"    - {cell}：{PROTECTED_CELL[cell]}" for cell in guarded)
            raise ValueError(
                f"这份补丁碰到了**受保护**的格子：{list(guarded)}。\n{where}\n"
                "  这五样任何一条反馈路径都不得自动扩大 —— 所以这一份不是「校验后"
                "放行」，而是**构造不出来**。\n"
                "  要改它们的是一个**人**：由他去开一件复核案，走既有的 "
                "approve_policy_change（要 authorizer 角色、要另一个人）。"
                "这一片不新开第二条批准路径。"
            )
        if not self.monitoring.strip() or not self.rollback.strip():
            raise ValueError(
                "这份补丁没有写监控或没有写回滚路径。\n"
                "  一个不能回滚的策略版本和一个能回滚的，在**部署那一刻**是两件事，"
                "而在报告上它们长得一样。两者都是必填的。"
            )
        if not self.source_ref.strip():
            raise ValueError("source_ref 是空的 —— 这份补丁是从什么提的必须写下来。")
        if not self.proposed_by.strip():
            raise ValueError("proposed_by 是空的 —— 谁提的必须是写下来的。")
        return self

    @model_validator(mode="after")
    def _the_state_says_whether_it_is_attached(self) -> "PolicyPatch":
        """``review_case_id is None`` ⟺ ``state is PROPOSED``。

        钉住它，是因为 ``state`` 与 ``review_case_id`` 说的必须是同一件事。两个
        说法分家的那天，``list_policy_patches`` 会报出一份「在等某件案子批准」而
        根本没挂上去的补丁 —— 而它此后会一直等下去，没有任何一处会响。
        """
        attached = self.review_case_id is not None
        if attached != (self.state is PolicyPatchState.AWAITING_APPROVAL):
            raise ValueError(
                f"state={self.state.value!r} 而 review_case_id="
                f"{self.review_case_id!r} —— 两者必须同进同退：一份说自己在等批准的"
                "补丁，必须说得出来它在等**哪一件**；一份还没挂上去的补丁说自己在"
                "等批准，会让它一直等下去而没有任何一处会响。"
            )
        return self

    def content(self) -> dict[str, object]:
        """调用方给定的内容，**不含 ``updated_at``**（理由同 ``FeedbackEvent.content``）。"""
        return self.model_dump(mode="json", exclude={"updated_at"})

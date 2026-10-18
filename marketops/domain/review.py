"""人只决策，系统准备和执行 —— §12 的那两件人工对象。

## 这一模块存在，是因为「问人」这件事在仓里没有落点

截至切片 6，一个对象走到「这里需要人看一眼」时，它的命运只有一种：变成作业的
``BLOCKED`` 理由里的一句散文（``application/inputs.py`` 的 ``SubjectResolution.blocker``）。
散文有两个后果，而它们都是这一模块要消掉的东西：

* **同一件事问两遍。** 散文没有身份，于是「这条规则到底该不该这样判」每次出现都是
  一句新话 —— 没有人答过它，也就没有人能回答第二次「上次怎么说的」。
* **答案无处可放。** 人给了答案之后，那个答案活在聊天里。§12 逐字：「人工答案散落、
  待办每轮覆盖」（``docs/research/MEMBER_A_AUTOMATION_EVIDENCE_2026-09-26.md:47``）。

所以这里有两个对象，**与 ``DecisionCase`` / ``DecisionRecord`` 分开**（§B20 逐字：
「不要覆盖现有 Jev ``DecisionCase``」）：那一族记的是**模型**的一次推理，这一族记的
是**人**的一次决定。两者的差别正是报告要回答的问题（谁判的、按什么判的），把它们
存进同一张表会让那个问题没有答案。

## 门槛：不是每个 unknown 都找人（§12 逐字）

原文把该问的和不该问的都点了名：

> 不是每个 unknown 都找人：可低成本补证的交研究，无公开资料的保留 no-route，
> **政策/身份冲突且值得处理的**才入 Inbox。

这句话在本模块里落成 :func:`review_reason_for` —— 一个对 ``GradeReason`` **全域**
的函数，只有 :attr:`ReviewReason.EVIDENCE_CONFLICT` 一个成员会开出待办，其余每一个
成员都在 :data:`NOT_A_HUMAN_QUESTION` 里有一句**写下来的**理由。

全域而不是「列出会开待办的那几个」，是因为反面才是这句话的重点：一个只列开口的映射
读不出「什么被排除了」，而 §12 那半句（交研究 / 保留 no-route）会因此没有落点。
``tests/test_review_gate.py`` 拿 ``GradeReason`` 的全体当判据，让「新加一个判定原因
而没想它要不要问人」当场红 —— 而不是等它悄悄开出一批待办。

## 「值得处理」是判据里的一维，不是一句客气话

``EXCLUDED_ENTRY`` 不进 Inbox，理由不是「它不重要」，而是**它问错了人**：那是一个
规则**命中**，不是没有答案的问题。想让它换个答案就要**改规则**，而改规则走 §9
（提议 → 影响预览 → 获授权者批准），不是逐对象问人。这条句子的实际效果是：系统不会
用一堆同形状的待办把「规则该改了」伪装成「有 30 个案件要处理」。

## 待办是**累计状态**，不是每轮覆盖的窗口

§12 逐字给了这个恒等式：

    pending_start + opened/reopened - resolved/dismissed = pending_end

「另记合并/转移」对应 :attr:`ReviewCase.merged_into`。它在本模块里是
:func:`inbox_flow` —— 一个对**事件序列**的纯函数，而不是对当前快照的计数。差别在
重开：一个案件关掉又重开，快照法会把它读成「一直是同一个未决案件」，于是
``opened`` 与 ``reopened`` 两个数都丢了，而 §B20 的「去重/重启**不丢**」正是要验这两个数。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Mapping

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .base import StrictModel
from .enums import GradeReason
from .errors import ReviewConflictError
from .util import check_canonical_id, sha256_text, to_utc

__all__ = [
    "ANSWERING_OUTCOMES",
    "apply_resolution",
    "awaiting_approval_for",
    "CASE_ID_PREFIX",
    "claim",
    "CLOSED_STATES",
    "INTAKE",
    "intake_for",
    "NOT_A_HUMAN_QUESTION",
    "PENDING_STATES",
    "POLICY_APPROVAL_OUTCOMES",
    "POLICY_PROPOSAL_OUTCOMES",
    "HumanResolution",
    "ResolutionOutcome",
    "ReviewCase",
    "ReviewFlow",
    "ReviewIntake",
    "ReviewReason",
    "ReviewState",
    "case_id_for",
    "dedup_key_for",
    "inbox_flow",
    "is_pending",
    "may_decide",
    "review_reason_for",
]

#: 案件 id 的前缀。与 ``coh-`` / ``acct-`` 同一族形状：内容寻址，前缀说明它是哪一类。
CASE_ID_PREFIX = "rc"

SEP = "\x00"


class ReviewReason(str, Enum):
    """一件待办**为什么**落到人面前。**闭集，两个成员。**

    两个成员对应 §12 逐字点名的两类冲突。第三个成员不存在，是因为那两类
    （可低成本补证的、无公开资料的）的处置是「**不开**待办」—— 它们不是这个枚举的
    成员，而是 :data:`NOT_A_HUMAN_QUESTION` 的成员。把它们塞进同一个枚举会让
    「哪些原因会开待办」变成一颗要靠人读代码才知道的暗雷。
    """

    #: 池子里那一行的「名称」是它自己的内部 id。``kent-003`` 那次事故的形状：上游
    #: 那份清单在名称那一格填的是它的记录键。**人拿得出答案**（那是他自己的名录），
    #: 而研究拿不出 —— 上网查不回来一个内部编号对应哪家组织。
    IDENTITY_CONFLICT = "identity_conflict"

    #: 同一份文档里出现多个邮箱与姓名/角色证据，归属定不下来（``has_conflict``）。
    #: 这是一个**关于证据的**冲突：人知道哪个是对的，而机器只能保守降级。
    EVIDENCE_CONFLICT = "evidence_conflict"


#: 一个判定原因**为什么不问人**。``GradeReason`` 的每一个不开口的成员都必须在
#: 这里有一句话，判据是 ``tests/test_review_gate.py``。
#:
#: 写成映射而不是写在 docstring 里，是因为前者的读者是执行器、后者的读者是自觉 ——
#: 而这张表的完整性恰好是这一片门槛唯一能被验的地方。
NOT_A_HUMAN_QUESTION: Mapping[GradeReason, str] = {
    GradeReason.RULE_MATCHED: (
        "规则命中了 —— 这一格本来就是「自动判完了」。要问的是「这条规则对不对」，"
        "而那走 §9 的提案（提议 → 影响预览 → 获授权者批准），不是逐对象问人。"
    ),
    GradeReason.RULES_UNKNOWN: (
        "§12 的「可低成本补证的交研究」：判不了是因为这条规则要的事实这一版还没"
        "生产出来，补上生产者就有了答案。它是一件**待办的研究任务**，不是待办的人。"
    ),
    GradeReason.NO_ELIGIBLE_EVIDENCE: (
        "§12 的「无公开资料的保留 no-route」：没有合格证据不是「没人知道」，而是"
        "「公开范围内没有」。问人也不会多出一条证据。"
    ),
    GradeReason.EXCLUDED_ENTRY: (
        "规则**命中**了排除名录。它不是一个没有答案的问题，而是一次判定 —— 想让它"
        "换个答案就要改规则（§9）。若逐对象问人，一堆同形状的待办会把「规则该改了」"
        "伪装成「有几十个案件要处理」。"
    ),
}


@dataclass(frozen=True)
class ReviewIntake:
    """一种原因开出来的待办**长什么样**，以及它今天有没有生产者。

    ``options`` / ``recommendation`` 两格在这里而不是在生产者的调用点上：一个案件的
    可选项是**关于这一类问题**的（「改这一个对象 / 放下它 / 改规则」），不是关于**这一次**
    判定的。写两遍的表现是同一个原因在两个入口下给人两套选项，而两边都自洽 —— 只有
    并排看才看得出，并排看的机会不常有。

    ``producer`` 是**敞开**这一格的那个符号，写成 ``模块路径::符号名``。它是这一族唯一的
    「谁在生产」的答案，而 ``tests/test_review_gate.py`` 会把它 **import 回来**：一个
    改名或搬家的生产者会让判据当场红，而不是让这里写着的那句话继续读起来像真的。
    这与 ``application/inputs.py`` 的 ``_NO_PRODUCER`` 是同一条纪律（那里的理由必须
    点名本仓**盘上真的存在**的模块路径）。

    ``no_producer`` 与 ``producer`` **恰好有一个非空**。空的那一侧不是「没想过」，而是
    「另一侧有话说」—— 两格都写会让人读不出这一格到底是哪一种，而这一格里写的正是
    §12 那句门槛（「政策/身份冲突且值得处理的才入 Inbox」）在代码里的形状。
    """

    options: tuple[ResolutionOutcome, ...]
    recommendation: ResolutionOutcome
    producer: str
    no_producer: str


def intake_for(reason: ReviewReason) -> ReviewIntake:
    """这一类原因开出来的待办长什么样。**表里没有时给出一条理由，不抛 ``KeyError``。**

    与 ``application/inputs.py::_no_producer_reason`` 逐字同一条：走到兜底意味着
    ``ReviewReason`` 多了一个成员而这里没有处置它 —— 那是**本模块的缺口**，不是调用方
    给错了。缺口分支抛 :class:`ReviewConflictError` 而不是返回一个猜出来的默认值：
    一个「没想过的原因」配上「随便给的两个选项」会开出一件看起来完全正常的待办。
    """
    declared = INTAKE.get(reason)
    if declared is not None:
        return declared
    raise ReviewConflictError(
        f"{reason.value!r} 不在 INTAKE 里 —— 新加了一个待办原因，而「它开出来的待办"
        "长什么样」还没有被想过。\n"
        "补一处：要么给它一个生产者（在 ``producer`` 里写出那个符号，让判据把它 import "
        "回来核），要么在 ``no_producer`` 里写清缺的是哪一段接线（并点名本仓已有的模块）。"
    )


def review_reason_for(reason: GradeReason) -> ReviewReason | None:
    """这个判定原因要不要问人。**对 ``GradeReason`` 全域，没有兜底分支。**

    返回 ``None`` 时，那一句话住在 :data:`NOT_A_HUMAN_QUESTION` 里 —— 调用点报的
    是那一句，不是这里编的。写成「无兜底分支 + 一张全覆盖的表」而不是
    ``if reason is CONFLICTING_EVIDENCE``，是为了让**新加一个 ``GradeReason`` 成员**
    这件事在这里有一个后果：``tests/test_review_gate.py`` 会红，而不是它悄悄落进
    ``else: return None`` 然后被读成「已经想过了」。
    """
    if reason is GradeReason.CONFLICTING_EVIDENCE:
        return ReviewReason.EVIDENCE_CONFLICT
    if reason not in NOT_A_HUMAN_QUESTION:
        raise KeyError(
            f"{reason!r} 不在 NOT_A_HUMAN_QUESTION 里 —— 新加了一个判定原因，而"
            "「它要不要问人」还没有被想过。\n"
            "两条路：它是政策/身份冲突且值得处理 ⇒ 加进 ReviewReason 并写一个生产者；"
            "否则 ⇒ 在这里写一句「为什么不问人」。\n"
            "不要加一个兜底分支：那会让下一个新成员静默落进「已经想过了」。"
        )
    return None


class ReviewState(str, Enum):
    """一件待办的生命周期。

    **未决的是前三个**（见 :func:`is_pending`）：``OPEN`` 还没人认领、``CLAIMED``
    有人在做、``AWAITING_APPROVAL`` 已经有答案但那个答案是一条**规则变更**，要等
    另一个人批准。

    第三个成员是这一片最要紧的一格：它让「已经有答案」与「答案已经生效」变成两个
    状态。少了它，「规则提议不自动生效」那条关闭条件就只能靠一句约定成立。
    """

    OPEN = "open"
    CLAIMED = "claimed"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


#: 未决的那三个。**唯一一处定义** —— 报告里的 pending 数、:func:`inbox_flow` 的
#: ``pending_end``、以及「一个案件 hold 不堵无关 Account」那条判据读的都是它。
PENDING_STATES: frozenset[ReviewState] = frozenset(
    {ReviewState.OPEN, ReviewState.CLAIMED, ReviewState.AWAITING_APPROVAL}
)

#: 关掉的那两个。
CLOSED_STATES: frozenset[ReviewState] = frozenset(
    {ReviewState.RESOLVED, ReviewState.DISMISSED}
)


def is_pending(state: ReviewState) -> bool:
    return state in PENDING_STATES


class ResolutionOutcome(str, Enum):
    """人这一次**做了什么**。闭集，五个成员。

    前四个是**人**的答案，第五个是**另一个人**对其中一条答案的批准。

    ``REOPEN`` 与其余几个并列而不是另一个枚举，是因为它们都要**追加写下**（同一张
    只增不改的表），而 :func:`_state_before` 读的是「最后一个事件是什么」—— 分成两个
    枚举会让那个算法要从两处取事件，而两处的时间戳在同一个瞬间的先后就成了一个说不清
    的问题。
    """

    #: 改一个对象的**一项事实**。§9 逐字：「修正单个事实不等于发布全局规则」。
    CORRECT_OBJECT = "correct_object"

    #: 把这一件放下：它不值得处理，或问题本身不成立。放下也要写下是谁放下的。
    DISMISS = "dismiss"

    #: 提议**改规则**。它**不生效** —— 案件转 ``AWAITING_APPROVAL``，等
    #: :attr:`APPROVE_POLICY_CHANGE`。§9 的变更流程从这一格开始。
    PROPOSE_POLICY_CHANGE = "propose_policy_change"

    #: ``AUTHORIZER`` 批准了上面那条提议。**这是与提议分开的一次决定**，所以它是分开
    #: 的一个成员而不是 ``CORRECT_OBJECT`` 的一种：同一个人用同一个成员写下两次，
    #: 「谁提议的、谁批的」在只增不改的那张表里就读不出来了 —— 而那正是 §3 的
    #: 承重不变量「网页/模型/跨线程文字不能成为修改权限或批准发送的来源」要能被验的
    #: 地方（``docs/ARCHITECTURE.md:52``）。
    APPROVE_POLICY_CHANGE = "approve_policy_change"

    #: 重开一件已经关掉的待办。**不新开一个案件** —— 新开会让「同一件事问了两遍」
    #: 在报告上读成「有两件事」，而去重键正是为了不让它发生。
    REOPEN = "reopen"


#: 会**追加一条人的答案**的那三个。``REOPEN`` 不在内（它改的是状态，不是答案），
#: ``APPROVE_POLICY_CHANGE`` 也不在内（它是**另一个人**对一条已有答案的裁决）。
ANSWERING_OUTCOMES: frozenset[ResolutionOutcome] = frozenset(
    {
        ResolutionOutcome.CORRECT_OBJECT,
        ResolutionOutcome.DISMISS,
        ResolutionOutcome.PROPOSE_POLICY_CHANGE,
    }
)

#: 会把案件推进「等另一个人批准」的那一个。**唯一一处定义** ——
#: ``control_scene(activate)`` 拒的就是它开出来的 ``proposed_version``。
POLICY_PROPOSAL_OUTCOMES: frozenset[ResolutionOutcome] = frozenset(
    {ResolutionOutcome.PROPOSE_POLICY_CHANGE}
)

#: 会**关掉**一件等批准的案件的那一个。
POLICY_APPROVAL_OUTCOMES: frozenset[ResolutionOutcome] = frozenset(
    {ResolutionOutcome.APPROVE_POLICY_CHANGE}
)


#: 门槛的**执行器所依附的那张表**：``ReviewReason`` 的每一个成员都在这里。
#:
#: 「每个成员要么有生产者、要么有一句写下来的理由」这句话需要一个可以被执行的形状，
#: 否则它就是一段散文。``EVIDENCE_CONFLICT`` 那一格今天写的是 ``no_producer``，而那句
#: 话点名了它缺的那一段接线在哪个模块 —— 它不是「以后再说」，是一句能被核对的具体话。
#:
#: **不要删掉没有生产者的那一行。** 删掉之后 ``intake_for`` 会走它自己的缺口分支
#: （理由与 ``inputs._no_producer_reason`` 逐字同一条：这是本模块的缺口，不是调用方
#: 给错了），而「哪些原因今天开不出待办」这个问题的答案就只剩「读一遍代码」。
#:
#: 位置在 ``POLICY_APPROVAL_OUTCOMES`` 之后不是排版：这张表在**模块导入时**就要
#: 求值 ``ResolutionOutcome`` 的成员，而它在上面才被定义完。
INTAKE: Mapping[ReviewReason, ReviewIntake] = {
    ReviewReason.IDENTITY_CONFLICT: ReviewIntake(
        options=(
            ResolutionOutcome.CORRECT_OBJECT,
            ResolutionOutcome.DISMISS,
            ResolutionOutcome.PROPOSE_POLICY_CHANGE,
        ),
        # 建议「改这一个对象」而不是「放下它」：问人的那句话是「这一行是谁」，而**人
        # 拿得出答案**（那是他自己的名录）。这正是这一格与 ``NO_ELIGIBLE_EVIDENCE`` 的
        # 分别 —— 后者问人也不会多出一条证据。
        recommendation=ResolutionOutcome.CORRECT_OBJECT,
        producer="marketops.adapters.mcp_server.tools::_open_refusal_cases",
        no_producer="",
    ),
    ReviewReason.EVIDENCE_CONFLICT: ReviewIntake(
        options=(
            ResolutionOutcome.CORRECT_OBJECT,
            ResolutionOutcome.DISMISS,
            ResolutionOutcome.PROPOSE_POLICY_CHANGE,
        ),
        recommendation=ResolutionOutcome.CORRECT_OBJECT,
        producer="",
        no_producer=(
            "路线级的 has_conflict 判定发生在**研究 worker 的结算那一趟**"
            "（``marketops/application/contact_routes.py`` 的 ``produce_routes`` → "
            "``marketops/application/grading.py`` 的 ``grade_route``），而它今天只有一个"
            "读者：``marketops/application/pipeline.py`` 里 ``metrics.conflicts`` 那个"
            "计数，没有一处 MCP 工具读它。要把这一格接上，得让 worker 那一趟的结算够得着"
            "**操作者收件箱**（一个控制面存储），而那是一条跨进程边界的接线 —— 它属于"
            "「结算回执怎么把冲突带回控制面」这一件事，不是 B20 这一片能顺手做完的。\n"
            "所以这一格今天**开不出待办**，而这句话是写下来的：报告里 EVIDENCE_CONFLICT "
            "的 0 是「没有生产者」，不是「量过了，是零」。"
        ),
    ),
}


def dedup_key_for(
    *, workspace: str, scene_id: str, reason: ReviewReason, subject_ref: str
) -> str:
    """同一个问题**在盘上的身份**。内容寻址，所以第二遍不新开案件。

    ## 原像里有什么，以及为什么

    ``workspace`` / ``scene_id`` / ``reason`` / ``subject_ref`` 四样 —— **不含**
    打开时刻、不含证据 id、不含措辞。各去掉一个的后果：

    * 去掉 ``workspace``：两个工作区里同名的账号会被读成同一个问题，而它们的答案是
      两个人分别给的（§12 的「跨域批量」拒绝测试正是这一维）。
    * 去掉 ``scene_id``：同一个账号在两个市场里的处置可以不同（一个市场的规则把它排除、
      另一个不排除），合成一件之后只有一个答案，而两个市场都读它。
    * 去掉 ``reason``：同一行「名称是内部 id」与「证据打架」是两件不同的事，答案也
      不同（一个是补名字、一个是定归属）。
    * 去掉 ``subject_ref``：整条规则只有一个案件，于是「一次批准之后不再重复问」会
      被实现成「问一次就够所有人用」—— 而那正是 §12 禁止的「单案裁决自动成为全局
      策略」。

    :func:`case_id_for` 与它是两个值：这个是**内容**（可读的四段拼起来），那个是它的
    哈希（可以直接当文件名/主键的短 id）。两个都留着，是因为「为什么这两件是同一件」
    这个问题的答案必须是可读的 —— 只留哈希的话，报告上只能显示两个不同的 id，还是
    看不出它们为什么不同。

    四段都是普通字符串，所以用 ``SEP`` 拼就够。**一旦某一格变成结构化值**，必须换成
    ``json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))``
    （``cohort_id_for`` 的先例），而不是让它 ``repr()`` 一把 —— 后者带内存地址。
    """
    return SEP.join(
        (
            check_canonical_id(workspace, "workspace"),
            check_canonical_id(scene_id, "scene_id"),
            reason.value,
            check_canonical_id(subject_ref, "subject_ref"),
        )
    )


def case_id_for(
    *, workspace: str, scene_id: str, reason: ReviewReason, subject_ref: str
) -> str:
    """``dedup_key_for`` 的哈希形态。前缀 + 16 位十六进制，与 ``coh-`` 同一族。

    取 16 位（64 位）是按 ``cohort_id_for`` 的先例：这些 id 要被人读、被当主键、被
    写进报告，而这个规模的碰撞概率远低于「两个人同时答同一件案件」的概率。
    """
    digest = sha256_text(
        dedup_key_for(
            workspace=workspace, scene_id=scene_id, reason=reason, subject_ref=subject_ref
        )
    )[:16]
    return f"{CASE_ID_PREFIX}-{digest}"


class ReviewCase(StrictModel):
    """一件要人处理的事。**最小字段**照 §12 逐字列的那一串。

    它**不是一个可变对象**（``StrictModel`` 是冻结的）：每一次状态变化都造一个新的，
    而盘上那一行由一个乐观锁序号 :attr:`version` 守着。§B20 逐字要求「按
    ``expected_version`` 提交裁决」，而这一格就是那个 ``expected_version`` 的来路。

    ## 为什么 ``version`` 与「策略版本」是两种东西

    同名不同物：这里是**乐观锁序号**（每次改动 +1），:attr:`policy_version` 是
    **策略版本号**（人提议出来的，只有被批准才生效）。报告里两者会并排出现，所以后者
    的名字里带 ``policy_`` 前缀，而不是也叫 ``version``。
    """

    case_id: str
    workspace: str
    scene_id: str
    reason: ReviewReason
    #: 这一件是关于谁的。账号 id、被拒的成员 id、或一条路线的键。
    subject_ref: str
    #: 受影响对象的**快照**（§12 逐字要求的那一格）。开了待办之后对象还可能变，
    #: 所以人读到的必须是**开案那一刻**的那一串。
    affected_refs: tuple[str, ...]
    #: 判定这一件时要看的证据，以及**当时的策略版本**。两个一起给，因为
    #: 「这一件是按哪一版规则判的」这个问题少了任何一半都答不上来。
    evidence_ids: tuple[str, ...] = ()
    policy_version: int | None = None
    #: 人可以选的那几项。**闭集、有序、不重复、至少两项** —— 只有一项的话那不是
    #: 一个待办，而是一句通知。
    options: tuple[ResolutionOutcome, ...]
    #: 系统的建议。可以不建议（``None``），但建议了就必须在 ``options`` 里。
    recommendation: ResolutionOutcome | None = None
    #: 认领者与队列（§12 的 ``owner/queue``）。
    queue: str = ""
    claimed_by: str = ""
    state: ReviewState
    #: 同意一件待办**产出的那个策略版本**（``PROPOSE_POLICY_CHANGE`` 之后才非空）。
    #: 它在盘上是「这一版在等批准」的唯一一处证据 —— ``control_scene(activate)``
    #: 拒的就是它。见 ``application/review.py`` 的 ``awaiting_approval_for``。
    proposed_version: int | None = None
    #: 被合并进了哪一件（§12 的「另记合并/转移」），以及**何时**。
    #:
    #: ``merged_at`` 是一个独立字段而不是复用 ``updated_at``：后者会在任何一次改动时
    #: 变（认领、状态迁移都会碰它），而 :func:`inbox_flow` 要拿它当**退出待办的时刻**
    #: 算账。复用的表现是「一次认领把一件合并过的案件挪回了窗口里」，而流水账上只会
    #: 显示合并数变了一次。
    merged_into: str | None = None
    merged_at: datetime | None = None
    #: 乐观锁序号。每次状态/认领/合并变化 +1。
    version: int = Field(ge=1)
    opened_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    note: str = ""

    @field_validator("case_id", "workspace", "scene_id", "subject_ref")
    @classmethod
    def _ids_are_canonical(cls, value: str, info: ValidationInfo) -> str:
        """四个 id 都必须是 ``slugify`` 的不动点 —— 与 ``AccountCohort`` 逐字同一条。

        ``subject_ref`` 在内，是因为它会进 :func:`dedup_key_for` 的原像：一个带空格的
        主体引用会让「同一个问题」在两次调用里算出两个键（一次原文、一次被 slugify），
        而重放就会写出第二件待办。拒绝，而不是**规范化** —— 先例的措辞是
        「不满足就抛，不截断」。
        """
        return check_canonical_id(value, str(info.field_name))

    @field_validator("opened_at", "updated_at", "closed_at", "merged_at")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        """规范到 UTC，理由逐字见 ``domain/util.py::to_utc``：这四个时刻都会**参与
        比较**（:func:`inbox_flow` 的窗口判定、:func:`_state_before` 的事件排序），
        而字符串的大小顺序只在偏移量相同时才等于瞬间的先后。一个 ``+08:00`` 写法的
        更晚时刻会在事件序列里排在一个 ``+00:00`` 的更早时刻**前面**，于是「最后一个
        事件是什么」答的是另一个 —— 而没有一处会红。
        """
        return None if value is None else to_utc(value)

    @model_validator(mode="after")
    def _the_id_is_the_content(self) -> "ReviewCase":
        """``case_id`` 必须算得出来 —— 与 ``AccountCohort`` 的 ``cohort_id`` 逐字同一条。

        一个手工指定的 id 会让同一件事开出两件待办，于是「一次批准之后不再重复问」
        在主体这一侧静默失效，而报告上只显示「有两件待处理」。
        """
        expected = case_id_for(
            workspace=self.workspace,
            scene_id=self.scene_id,
            reason=self.reason,
            subject_ref=self.subject_ref,
        )
        if self.case_id != expected:
            raise ValueError(
                f"case_id 是 {self.case_id!r}，而按（工作区, 场景, 原因, 主体）算出来的是"
                f" {expected!r}。\n"
                "这个 id 是内容寻址的：同一件事问两次必须是同一个 id，否则去重会写出"
                "两件内容相同、id 不同的待办，而「同一个问题不重复问」在那之后就不"
                "成立了。"
            )
        return self

    @model_validator(mode="after")
    def _options_are_a_real_choice(self) -> "ReviewCase":
        """选项至少两项、不重复；建议在选项里。"""
        if len(self.options) < 2:
            raise ValueError(
                f"options 只有 {[o.value for o in self.options]!r} 一项 —— 那不是一件"
                "待办，是一句通知。人只决策，而「只有一个选项」意味着系统已经替人"
                "决定完了。"
            )
        if len(set(self.options)) != len(self.options):
            raise ValueError(f"options 里有重复项：{[o.value for o in self.options]!r}")
        if self.recommendation is not None and self.recommendation not in self.options:
            raise ValueError(
                f"建议是 {self.recommendation.value!r}，而它不在可选项 "
                f"{[o.value for o in self.options]!r} 里 —— 报告会给人一个选不了的"
                "建议。"
            )
        return self

    @model_validator(mode="after")
    def _the_state_and_the_timestamps_agree(self) -> "ReviewCase":
        """状态与 ``closed_at`` 必须一致；关闭之后不许再有认领者。"""
        if (self.state in CLOSED_STATES) != (self.closed_at is not None):
            raise ValueError(
                f"状态是 {self.state.value}、closed_at 是 {self.closed_at!r} —— 两者必须"
                "同一个说法。关闭而没有关闭时刻会让「等待了多久」算不出来；反过来"
                "则会让一件已关闭的案件继续计入 pending。"
            )
        if self.state in CLOSED_STATES and self.claimed_by:
            raise ValueError(
                f"状态是 {self.state.value}，却还挂着认领者 {self.claimed_by!r} —— "
                "关闭时认领必须一起放开，否则「这一格是谁在做」在报告里永远有个答案。"
            )
        if self.merged_into == self.case_id:
            raise ValueError("merged_into 指向自己。")
        return self

    @model_validator(mode="after")
    def _awaiting_approval_has_a_version(self) -> "ReviewCase":
        """``AWAITING_APPROVAL`` 与 ``proposed_version`` 必须同进同退。

        这两格是「有一版规则在等批准」的唯一证据。少一格的表现都是一个**看起来
        正常**的状态：只写状态会让 ``control_scene`` 不知道要拒哪一版（于是提议
        自动生效），只写版本会让这一版静默地等在库里而没有任何一处说得出它在等。
        """
        awaiting = self.state is ReviewState.AWAITING_APPROVAL
        if awaiting != (self.proposed_version is not None):
            raise ValueError(
                f"状态是 {self.state.value}，proposed_version 是 "
                f"{self.proposed_version!r} —— 两者必须同进同退：只有状态会让提议"
                "自动生效，只有版本会让它静默地等。"
            )
        return self

    @model_validator(mode="after")
    def _a_merge_has_a_moment(self) -> "ReviewCase":
        """``merged_into`` 与 ``merged_at`` 必须同进同退（理由见 ``merged_at`` 那一格）。"""
        merged = self.merged_into is not None
        if merged != (self.merged_at is not None):
            raise ValueError(
                f"merged_into 是 {self.merged_into!r}，merged_at 是 {self.merged_at!r}"
                " —— 两者必须同进同退。只有去处在算账时不知道该在哪个时刻把这一件"
                "移出待办；只有时刻则读不出它被并进了哪一件。"
            )
        return self


class HumanResolution(StrictModel):
    """人做过的一次决定。**只增不改。**

    它与 :class:`ReviewCase` 是两个对象而不是一个：案件是**当前**状态（会被覆盖为
    新的一行），决定是**历史**（一行都不许改）。§12 逐字要求「保留历史和恢复进度」，
    而把历史与当前合成一行之后，「这件案件等了多少天」与「它被改过几次」就只能靠
    一个不断被覆盖的时刻回答。

    ``by`` 是**可信 principal**（§12 逐字）：它从**令牌**来，不是参数（先例逐字见
    ``tests/test_mcp_product_loop.py`` 里那条「生效记录的是令牌上的 principal、
    不是 payload 里的」）。``expected_version`` 是人在自己的屏幕上看到的那个
    ``case.version`` —— 提交上来时如果盘上已经不是它了，这一件被拒（陈旧 attempt
    不覆盖新结果，``docs/ARCHITECTURE.md:51``）。
    """

    resolution_id: str
    case_id: str
    workspace: str
    outcome: ResolutionOutcome
    #: 人对这一个对象说了什么。``CORRECT_OBJECT`` 时是改成了什么；``DISMISS`` 时是
    #: 为什么放下；``PROPOSE_POLICY_CHANGE`` 时是提议的说明。
    answer: str
    by: str
    roles: tuple[str, ...]
    expected_version: int = Field(ge=1)
    at: datetime
    #: 提议改规则时**被挂起的那一版策略版本号**（``PROPOSE_POLICY_CHANGE`` 时非空）。
    #:
    #: 它在这一条决定上，而不是在案件上就够：案件那一格（``ReviewCase.proposed_version``）
    #: 是**当前投影**，而这一格是**当时那一次提议**。两者都必要 —— 案件那一格会被下一条
    #: 决定清掉（那时「它在等什么」已经不再成立），而这一格是只增不改的历史，答的是
    #: 「第 13 版当初是谁提的」。
    proposed_version: int | None = None

    @model_validator(mode="after")
    def _a_proposal_carries_the_version_it_suspends(self) -> "HumanResolution":
        """``PROPOSE_POLICY_CHANGE`` 与 ``proposed_version`` 同进同退。

        一条**不说明它挂了哪一版**的提议在盘上是说不出话的：``control_scene(activate)``
        的闸门要按版本号比，而案上了那一格要由它填。反过来，一条「改了单个对象」的决定
        带上一个版本号，会让那一版**永远等不到批准**（没有任何一件待办在等它）——
        而它看起来只是一次普通的规则变更。
        """
        proposing = self.outcome in POLICY_PROPOSAL_OUTCOMES
        if proposing != (self.proposed_version is not None):
            raise ValueError(
                f"结果是 {self.outcome.value!r}，proposed_version 是 "
                f"{self.proposed_version!r} —— 两者必须同进同退：不说明挂了哪一版的"
                "提议会让闸门不知道该拦谁；反过来会让那一版永远等不到批准，而它看起来"
                "只是一次普通的规则变更。"
            )
        return self

    @model_validator(mode="after")
    def _an_answer_is_an_answer(self) -> "HumanResolution":
        """答案不能是空的 —— 空的答案在盘上与「还没答」同形。

        §12 那句「让 Member-A 一次批准之后不再重复填相同答案」的前提是**那个答案被
        存在某处**。一个可以写空的 ``answer`` 会让它变成 ``answer=""``，于是下一次
        读回来是「有答案、但答案是空的」，而调用点只能猜那是哪一种。
        """
        if not self.answer.strip():
            raise ValueError(
                "answer 是空的（或只有空白）—— 空答案在盘上与「还没答」同形，而"
                "下一次读它的人只能猜是哪一种。放下也要写一句为什么。"
            )
        if not self.by.strip():
            raise ValueError("by 是空的 —— 决定是谁做的必须是写下来的。")
        if not self.roles:
            raise ValueError(
                "roles 是空的 —— 判「这个人当年有没有资格做这件事」时，令牌上的角色"
                "是唯一读得到的依据，而它今天已经不可复现了。"
            )
        return self

    @field_validator("at")
    @classmethod
    def _at_is_utc(cls, value: datetime) -> datetime:
        """见 ``ReviewCase._timestamps_are_utc``：这个时刻决定「最后一个事件是谁」。"""
        return to_utc(value)


class ReviewFlow(StrictModel):
    """一段窗口里的待办流水。§12 的恒等式就是它的 :attr:`reconciles`。

    ``merged`` / ``transferred`` 各自单列，因为 §12 逐字说它们「另记」：把它们塞进
    ``resolved`` 会让「人处理了几件」多出几个从来没有人看过的数。

    ``transferred`` 在**本切片恒为 0**：转移（把一件待办交给另一个队列/人）在这一片
    没有生产者，所以没有任何一格能算出它。它留在恒等式里而不删掉，是因为 §12 的
    那句话里两个词并列；删掉它的表现是「将来加转移时，恒等式的形状要改一次」，
    而改恒等式形状正是这一格最不该发生的事。**判据必须写明它是 0**，否则一个恒为 0
    的格子读起来像「量过了，是零」。
    """

    window_start: datetime
    window_end: datetime
    pending_start: int
    opened: int
    reopened: int
    resolved: int
    dismissed: int
    merged: int
    transferred: int
    pending_end: int

    @field_validator("window_start", "window_end")
    @classmethod
    def _bounds_are_utc(cls, value: datetime) -> datetime:
        return to_utc(value)

    @model_validator(mode="after")
    def _window_is_a_window(self) -> "ReviewFlow":
        if self.window_end < self.window_start:
            raise ValueError(
                f"窗口的终点 {self.window_end!r} 早于起点 {self.window_start!r}。"
            )
        return self

    @property
    def reconciles(self) -> bool:
        """§12 那个恒等式成不成立。"""
        return (
            self.pending_start
            + self.opened
            + self.reopened
            - self.resolved
            - self.dismissed
            - self.merged
            == self.pending_end
        )

    @property
    def equation(self) -> str:
        """恒等式的可读形态 —— 对不上时它就是那条错误信息。"""
        return (
            f"{self.pending_start} + {self.opened} + {self.reopened} - "
            f"{self.resolved} - {self.dismissed} - {self.merged} = {self.pending_end}"
        )


def _state_for(outcome: ResolutionOutcome) -> ReviewState:
    """一条决定之后，案件落在哪个状态上。**唯一一处定义。**

    两个读者：:func:`_state_before`（读历史算流水账）与 :func:`apply_resolution`
    （写新状态）。两份映射的表现是「报告说这一件还没处理，而盘上它是关闭的」——
    两者都自洽，只有并排看才看得出不同，而并排看的机会不常有。
    """
    if outcome in POLICY_PROPOSAL_OUTCOMES:
        return ReviewState.AWAITING_APPROVAL
    if outcome is ResolutionOutcome.REOPEN:
        return ReviewState.OPEN
    if outcome is ResolutionOutcome.DISMISS:
        return ReviewState.DISMISSED
    if outcome is ResolutionOutcome.CORRECT_OBJECT:
        return ReviewState.RESOLVED
    if outcome in POLICY_APPROVAL_OUTCOMES:
        return ReviewState.RESOLVED
    raise KeyError(
        f"{outcome!r} 没有归宿 —— 新加了一个处置结果，而「它把案件放到哪个状态」还"
        "没有被想过。不要加兜底分支：那会让下一个新成员静默落进一个没被选过的状态。"
    )


def may_decide(case: ReviewCase, outcome: ResolutionOutcome) -> bool:
    """人在**这一件案件的当前状态**下能不能选这个结果。

    对那三个**答案**（``ANSWERING_OUTCOMES``）来说答案就是「它在不在
    :attr:`ReviewCase.options` 里」—— 那一格是案件作者写给人看的闭集，也是界面要
    渲染的那几项。

    另两个成员**不在选项表里也能选**，而它们是同一条理由的两次应用：两者都是**对一件
    已有答案的东西的处置**，不是作者写给人看的那几项里的一个。

    * :attr:`ResolutionOutcome.REOPEN` 是一句**修正**：一件已经裁决过的案件要重新
      打开，而「重新打开」这个动作在任何一件待办上都是同一个意思。把它塞进 ``options``
      会让作者每写一件待办都要记得加一项 —— 而漏加的表现是**这条路走不通**：
      ``REOPEN`` 会被下面的成员判据拒掉，于是「重启不丢」（§B20）在盘上永远发生不了，
      而报告上看起来只是「没有人重开过」。它只有**已经关掉**的案件能选：一个开着的
      东西没有「重新打开」这个动作。
    * :attr:`ResolutionOutcome.APPROVE_POLICY_CHANGE` 是**另一个人**对一条已经写下的
      提议的批准 —— 而 ``options`` 那张表是给**第一个人**看的（``INTAKE`` 按原因给的
      那三个答案）。理由与 ``REOPEN`` 逐字相同，而它的漏加后果实测过一次：批准
      **根本走不通**（一个人提议之后谁也批不了：``approve_policy_change`` 在存储那一层
      落到这里被拒），而报告上只显示「没有人批准过」—— 一条死路与一件没人做过的事
      在那一格上是同一个样子。它也只有 ``AWAITING_APPROVAL`` 那一件能选：一件还开着的
      待办上没有「批准」这个动作，那正是下面那条「决定必须真的改变状态」的另一半。

    已关掉的那一件**只收 REOPEN**（下面那一行）：`REOPEN` 那一支与它是同一条判据的
    两个方向，写成两支而不是「关掉的案件什么都不能选」是因为**关掉之后唯一还有意义的
    动作就是重开**。少了这一行，一件已裁决的案件可以用**另一个**关闭结果再关一次
    （``RESOLVED`` → ``DISMISSED``），而下面那条「结果状态必须与当前状态不同」当场
    放它过去 —— 它挡的是**同一条**决定放两次，挡不住两条不同的关闭决定。后果不是多一
    行历史，而是 :func:`inbox_flow` 把同一件数两次（``resolved`` 与 ``dismissed`` 各
    加一，``opened`` 只加了一），§12 那条恒等式当场差一，而两份报告各自看上去都自洽。
    """
    if outcome is ResolutionOutcome.REOPEN:
        return case.state in CLOSED_STATES
    if case.state in CLOSED_STATES:
        return False
    if outcome in POLICY_APPROVAL_OUTCOMES:
        return case.state is ReviewState.AWAITING_APPROVAL
    return outcome in case.options


def apply_resolution(case: ReviewCase, resolution: HumanResolution) -> ReviewCase:
    """把一条决定折进一件待办，返回**新的**那一件（原对象不动）。

    写成纯函数而不是存储里的一段 SQL，是因为这几条判据都是**关于那两个对象**的，
    与库无关：它们要么在每一处写入路径上都写一遍（而漏掉一处不报错），要么只写一次。

    五条判据各自挡住一件具体的事：

    * **这条决定不在这件待办的可选项里**（或重开一件还开着的）—— ``options`` 那一格
      因此是承重的。见 :func:`may_decide`。
    * **这条决定不改变状态** —— 见下面「为什么它必须是一次真正的迁移」。
    * **案件不是那个人读到的那个版本** —— 由调用方（存储）在写事务里比 ``version``，
      这一层只看它拿到的那两件东西。
    * **案件的 id 与决定的 id 对不上** —— 一条写错了案件的决定会追加到另一件的历史
      里，而流水账按 ``case_id`` 分组，那一行既不进 opened 也不进 resolved，
      **静默消失**（恒等式照样成立）。
    * **工作区对不上** / **这一件已经被合并走了** —— 见各自的消息。

    ## 为什么它必须是一次真正的迁移

    这条不是洁癖，实测出来的：同一件案件连做两次 ``DISMISS``，库里的状态**没变**，
    而 :func:`inbox_flow` 的 ``dismissed`` 数了两次 —— 恒等式当场变成
    ``1 + 0 + 0 - 0 - 2 - 0 = 0`` 为假（一次最小复现：开一件、放两次、取一段
    覆盖两个时刻的窗口）。流水账那一层**无法**自己发现这件事：它只看得见事件，而
    「这两次放下是同一件事」这个信息只存在于状态里。

    所以判据放在这里，而它的形式是「结果状态必须与当前状态不同」而不是「同一件不许
    放两次」——后者是逐成员的枚举，而前者对新加的成员自动成立。它同时把
    ``inbox_flow`` 的计数变成**状态迁移的计数**，而那正是 §12 那条恒等式成立的前提。

    Raises:
        ReviewConflictError: 上面几条中任意一条不成立。
    """
    if resolution.case_id != case.case_id:
        raise ReviewConflictError(
            f"这条决定是给 {resolution.case_id!r} 的，而它被折进的案件是 "
            f"{case.case_id!r} —— 一个字都没写。"
        )
    if resolution.workspace != case.workspace:
        raise ReviewConflictError(
            f"决定的 workspace 是 {resolution.workspace!r}，案件的是 {case.workspace!r}"
            " —— 一个工作区的答案不能被另一个工作区的案件读到。"
        )
    if case.merged_into is not None:
        raise ReviewConflictError(
            f"{case.case_id!r} 已经并入 {case.merged_into!r}（{case.merged_at}）—— "
            "一件已经交出去的待办不能再被单独裁决：它的答案会落在一件报告上不再显示"
            "的案件里。要改就在宿主那一件上写。"
        )
    if not may_decide(case, resolution.outcome):
        # 拒绝里必须说出**出路**（只报「不能这样做」的回执会让人去改代码），而关掉的
        # 那一件与开着的那一件出路不同：一件已经裁决过的案件要改，先把它重开。
        way_out = (
            f"它已经关了（{case.state.value}），关掉之后唯一还能做的是重开它"
            f"（{ResolutionOutcome.REOPEN.name}）—— 那句话写下了「上一次的答案不再"
            "作数」。"
            if case.state in CLOSED_STATES
            else "要换一个结果，就换一条决定。"
        )
        raise ReviewConflictError(
            f"{resolution.outcome.value!r} 不是 {case.case_id!r} 现在能做的决定"
            f"（它现在的状态是 {case.state.value}，可选项 "
            f"{[o.value for o in case.options]!r}）。\n{way_out}"
        )

    state = _state_for(resolution.outcome)
    if state is case.state:
        raise ReviewConflictError(
            f"{case.case_id!r} 已经是 {case.state.value} 了，而这条 "
            f"{resolution.outcome.value!r} 的结果还是 {state.value} —— 它没有改变任何"
            "东西，所以不能算一次决定。\n"
            "放行的后果不是多一行历史，而是流水账上那一格**多算一次**：同一件案件连放"
            "两次时 dismissed 数到 2，而待办只少了 1 件，恒等式当场不成立，而两份报告"
            "各自看上去都自洽。\n"
            "要改一件已经裁决过的案件，先重开它（REOPEN）—— 那句话写下了「上一次的"
            "答案不再作数」，而重复一条同样的决定没有写下任何东西。"
        )

    closed = state in CLOSED_STATES
    return case.model_copy(
        update={
            "state": state,
            "version": case.version + 1,
            "updated_at": resolution.at,
            # 重开把关闭时刻清掉；其余几个落进关闭态时才写它。``REOPEN`` 是唯一一条
            # 「从关闭回到未决」的路，所以这两个字段不会同时需要。
            "closed_at": resolution.at if closed else None,
            "claimed_by": "" if closed else case.claimed_by,
            "proposed_version": (
                resolution.proposed_version if state is ReviewState.AWAITING_APPROVAL
                else (case.proposed_version if not closed else None)
            ),
        }
    )


def claim(case: ReviewCase, *, by: str, at: datetime) -> ReviewCase:
    """认领一件待办：``OPEN`` → ``CLAIMED``。返回**新的**那一件（原对象不动）。

    ## 为什么这一条存在

    ``ReviewState.CLAIMED`` 在这一条之前**不可达**：``apply_resolution`` 是把案件
    推进状态的唯一一条路，而 ``_state_for`` 里没有任何一个处置结果落在它上面 ——
    于是 §12 的 ``owner`` 那一格只有一个从来不写的值，而 ``PENDING_STATES`` 里
    有一个永远不出现的成员。不可达的枚举成员在报告上读起来像「还没有人认领过」，
    而那与「认领这个动作不存在」是两件事。

    它**不是**一次决定，所以它不是 :class:`ResolutionOutcome` 的一个成员：那一族
    会往只增不改的那张历史表里追加一行，而「谁在看着这一件」是一条会被下一次认领
    覆盖的当前状态。

    ## 三条判据

    * **已经是别人的** —— 两个人同时领走一件，后写的那个会把前一个的认领静默吃掉
      （他的屏幕上还写着「我在做这一件」）。
    * **已经是你自己的** —— 这不是幂等，是一次**没有改变任何东西**的写：版本号 +1
      会让**另一个**持有者手上的 ``expected_version`` 当场过期，而他什么都没做错。
      这与 ``apply_resolution`` 里那条「决定必须真的改变状态」是同一条纪律。
    * **已经关了或已经有人答了** —— ``AWAITING_APPROVAL`` 也不算可认领：它已经有
      答案了，等的**不是**一个做事的人，是另一个人的批准（那走
      ``approve_policy_change``）。把它读成「待认领」会让一件等批准的规则看起来
      像一件没人管的事。

    Raises:
        ReviewConflictError: 上面三条中任意一条不成立。
    """
    if not by.strip():
        raise ReviewConflictError(
            f"{case.case_id!r} 的认领人是空的 —— 「谁在做这一件」在报告里必须有个答案，"
            "而空串与「没有人认领」在那一格上是同一个值。"
        )
    if case.state is not ReviewState.OPEN:
        holder = f"，现在由 {case.claimed_by!r} 持着" if case.claimed_by else ""
        raise ReviewConflictError(
            f"{case.case_id!r} 现在的状态是 {case.state.value}{holder} —— 只有 "
            f"{ReviewState.OPEN.value} 那一件可以被认领。\n"
            "已经关了或已经在等批准的那一件都不是「待认领」：等批准等的是**另一个人"
            "的一次批准**（那走 approve_policy_change），把它读成待认领会让一件等人"
            "的规则看起来像一件没人管的事。"
        )
    return case.model_copy(
        update={
            "state": ReviewState.CLAIMED,
            "claimed_by": by,
            "version": case.version + 1,
            "updated_at": to_utc(at),
        }
    )


def awaiting_approval_for(
    cases: tuple[ReviewCase, ...], version: int
) -> ReviewCase | None:
    """有没有一件待办正**等**着第 ``version`` 版被批准。有就返回它。

    ``control_scene(activate)`` 拿它当闸门：一版**由待办提议出来**的规则不能在它自己
    那件待办还挂着的时候生效。这就是 §9「提议 → 影响预览 → 获授权者批准」里最后那个
    箭头 —— 少了它，提议在同一个 ``propose_policy_change`` 调用里就已经生效了，
    而「批准」那一步没有任何东西依赖它。

    按 ``(state, proposed_version)`` 判而不是只看 ``proposed_version``：后者在案件被
    裁决之后仍留在行上（历史），一个只看版本的闸门会在批准之后继续拒那一版 ——
    而那看起来像「批准没生效」，排查方向完全不同。
    """
    for case in cases:
        if case.state is ReviewState.AWAITING_APPROVAL and case.proposed_version == version:
            return case
    return None


def _events_by_case(
    resolutions: tuple[HumanResolution, ...],
) -> dict[str, list[HumanResolution]]:
    """按案件把事件分组，每组按时刻排序（同刻按 ``resolution_id`` 稳定排序）。

    同刻的先后**必须是确定的**：两个事件写在同一微秒时，按插入顺序还是按 id 排序会
    给出两种「最后一个是哪一个」，而 :func:`_state_before` 读的正是最后一个。
    """
    grouped: dict[str, list[HumanResolution]] = {}
    for row in resolutions:
        grouped.setdefault(row.case_id, []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: (row.at, row.resolution_id))
    return grouped


def _state_before(
    case: ReviewCase, events: list[HumanResolution], at: datetime
) -> ReviewState | None:
    """``case`` 在时刻 ``at`` **之前**那一刻的状态；``None`` 表示那时它还不存在。

    ## 为什么是「之前」而不是「之时」

    :func:`inbox_flow` 的窗口是左闭右开的 ``[start, end)``，所以一个落在边界上的
    事件/开案属于**后**一段。于是 ``pending_start`` 要问的是「在 ``start`` 之前就已
    未决吗」—— 用 ``<=`` 的话，一个刚好在 ``start`` 开的案件会同时被算进
    ``pending_start`` 与 ``opened``，而恒等式两边各多一个、「对得上账」地把一件记成
    两件。

    ## 为什么不是「读一下 ``closed_at``」

    那一格说的是**现在**。一个关掉又重开的案件，它的 ``closed_at`` 被清成了 ``None``
    —— 于是「它在窗口开始时是未决的吗」只能从事件序列回答。快照法在这里会把重开读成
    「从来没关过」，``opened`` 与 ``reopened`` 两个数一起丢掉。

    ## 没有事件可读时的兜底**不是** ``OPEN``

    一个案件带着事件时，「第一条事件之前」它的状态一定是 ``OPEN`` —— 案件是开着出生的，
    而关闭**只能**由一条事件写下。所以那一种情况下兜底成 ``OPEN`` 是对的。

    而没有事件可读时，兜底成 ``OPEN`` 会把一件**已经关掉**的案件读成一直未决。两个后果
    都不是这里能负责的：``pending_end`` 会多一件，而 :func:`inbox_flow` 会把一次
    「把它并进另一件」的数据修复记成「一件待办消失了」—— 恒等式因此少一个数，而两边
    看上去都「只差 1」。所以那一种情况下读 ``closed_at``：它恰好是「没有事件时唯一
    说得清那个案件的时刻」。
    """
    if to_utc(case.opened_at) >= at:
        return None
    latest: HumanResolution | None = None
    for event in events:
        if event.at < at:
            latest = event
        else:
            break
    if latest is None:
        closed_at = case.closed_at
        if closed_at is not None and to_utc(closed_at) < at:
            return case.state
        return ReviewState.OPEN
    return _state_for(latest.outcome)


def _merged_before(case: ReviewCase, at: datetime) -> bool:
    """``case`` 在时刻 ``at`` 之前是否**已经被合并**了。"""
    return case.merged_at is not None and to_utc(case.merged_at) < at


def inbox_flow(
    *,
    cases: tuple[ReviewCase, ...],
    resolutions: tuple[HumanResolution, ...],
    window_start: datetime,
    window_end: datetime,
) -> ReviewFlow:
    """一段窗口的待办流水。**纯函数**，判据因此不必先造一个库。

    窗口是左闭右开的 ``[start, end)``（理由见 :func:`_state_before`）。

    合并的处置有两半，两半都必须对，否则恒等式会以一个看不出来的方式偏一：

    * 一个被合并的案件**从待办里退出**（计入 ``merged`` 一次）。留着不动的表现是
      「有两件待办其实是同一件」，而报告上只显示 pending 多了一件。
    * 退出**只在它当时确实未决时**才记账。把一件早就关掉的案件合并进去是一次数据
      修复，它没有让任何待办消失 —— 给它记一次 ``merged`` 会让恒等式少一个数，
      而两边看上去都「只是差了 1」。

    被合并的案件的事件**不计数**（``continue``）：它的宿主案件有自己的事件序列，
    把两者都算上等于同一件事被处理了两次。
    """
    start, end = to_utc(window_start), to_utc(window_end)
    grouped = _events_by_case(resolutions)

    pending_start = 0
    opened = reopened = resolved = dismissed = merged = transferred = 0
    pending_end = 0

    for case in cases:
        events = grouped.get(case.case_id, [])

        # ── 待办的两个快照（「在窗口开始时 / 结束时是否未决」）──────────────
        before = _state_before(case, events, start)
        after = _state_before(case, events, end)
        if before is not None and is_pending(before) and not _merged_before(case, start):
            pending_start += 1
        if after is not None and is_pending(after) and not _merged_before(case, end):
            pending_end += 1

        # ── 开案（窗口内）────────────────────────────────────────────────
        if start <= to_utc(case.opened_at) < end:
            opened += 1

        # ── 合并（窗口内，且当时确实未决）────────────────────────────────
        if case.merged_at is not None:
            merged_at = to_utc(case.merged_at)
            if start <= merged_at < end:
                at_merge = _state_before(case, events, merged_at)
                if at_merge is not None and is_pending(at_merge):
                    merged += 1

        if case.merged_into is not None:
            continue

        # ── 人做过的事（窗口内）──────────────────────────────────────────
        for event in events:
            if not (start <= event.at < end):
                continue
            if event.outcome is ResolutionOutcome.REOPEN:
                reopened += 1
            elif event.outcome is ResolutionOutcome.CORRECT_OBJECT:
                resolved += 1
            elif event.outcome is ResolutionOutcome.DISMISS:
                dismissed += 1
            elif event.outcome in POLICY_APPROVAL_OUTCOMES:
                # 批准把一件**等批准**的案件移出待办（``AWAITING_APPROVAL`` 是未决的
                # 一个状态），所以它是 resolved 的一种。提议本身**不**记在这里：
                # 它把一个未决案件换成了另一个未决案件，账面上什么都没变。
                resolved += 1
            elif event.outcome in POLICY_PROPOSAL_OUTCOMES:
                pass
            else:
                raise KeyError(
                    f"{event.outcome!r} 在流水账里没有归宿 —— 新加了一个处置结果，"
                    "而「它让待办多了还是少了」还没有被想过。不要加兜底分支。"
                )

    return ReviewFlow(
        window_start=start,
        window_end=end,
        pending_start=pending_start,
        opened=opened,
        reopened=reopened,
        resolved=resolved,
        dismissed=dismissed,
        merged=merged,
        transferred=transferred,
        pending_end=pending_end,
    )

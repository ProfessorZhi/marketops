"""外向链的主语：模板族、写作策略、草稿、 Wave、授权、收发事件、交接。

## 这个模块今天为什么必须存在

在本轮之前，外向链在本仓是**一句话**：``RouteStatus`` 里有
``prepared_pending_authorization`` 这个取值，文档里有 Campaign / Wave / outbox /
SalesHandoff 这些词，而**没有一个对象**。于是「这一批信是谁批准的、批的是哪一份
内容、发出去的是不是那一份」这三个问题在盘上都没有答案。

三个问题各自值一次事故：

* 批准的是 A 稿、发出去的是 B 稿 —— 而两份稿子都「合规」，差别只在收件人看到的
  那一封。授权必须钉在**内容摘要**上，不是钉在 Wave 编号上。
* 第二次执行同一批 —— 于是有些人收到两封。去重必须落在**逻辑发送标识**上，
  而不是「这次跑之前有没有检查过」。
* 一封回信写「请把我从名单里去掉」，而系统的处置是「继续跟进」—— 回信正文
  在这一层是**数据**，它永远不构成一条指令。

## 一条把整个模块分成两半的纪律

**草稿生成侧不得拥有任何发送能力，发送侧不得拥有任何改写正文的能力。**

落在类型上是：:class:`OutreachDraft` 的 ``send_authorized`` 是
``Literal[False]``，而它**没有 setter**、不接受环境变量、不是任何工具的入参；
真正能产生发出的东西是 :class:`OutboundAttempt`，它的构造需要一个
:class:`WaveAuthorization`。于是「让草稿发出去」这件事在这一层写不出来 ——
不是被拦住，是没有那个形状。

这条纪律与 ``domain/entities.py`` 里 ``ContactRoute.send_authorized`` 是**同一条**，
但它在这里必须**独立存在**（本模块不 import 那个字段、也不复用它）：

* 那个约束的语境是研究阶段（``research_only``），解除它的条件是一份业务批准；
* 这条约束的语境是**产品已经能发**之后，挡的是「研究身份顺手发了一封」。
* 两者的解除条件不同，所以共用一个常量会在某一天被一起打开 —— 而那一天
  没有人会看出来，因为**取值**是一样的。

## 三个状态轴，不是一个

一封邮件在任一时刻同时处在三个轴上的某一点，而它们的取值**不能合并**：

* :class:`DraftState` —— 这份**稿子**在生成侧到哪一步了（还没有 / 已生成 / 已作废）；
* :class:`MessageState` —— 这一**次投递**在发送侧到哪一步了
  （未发 / 已受理 / 已投递 / 投递未知 / 被拒 / 已退信）；
* :class:`CorrespondenceKind` —— 对面**回了什么**（人回的 / 自动回复 / 退信 /
  退订 / 投诉）。

合成一个 ``status`` 会立刻产生一个说不出话的情形：一封「已投递、并且对方退订了」
的信。合成之后它只能是其中一个，而另一个消失 —— 消失的恰好是那条**必须被执行**的
处置（退订要立刻进抑制名单）。

## 「已受理」「已投递」「已回信」是三件事

§6 逐字要求分开。落在这里是 :class:`MessageState` 的三个取值，而它们的差别是
**证据强度**，不是进度：

* ``ACCEPTED_BY_PROVIDER`` —— 对面接收了这次连接。它只说明「我们的字节出去了」。
* ``DELIVERED`` —— 收到了一条投递确认。它说明「对面那个邮箱收下了」。
* 回信不在这里 —— 它是 :class:`CorrespondenceEvent`，一个**另一个对象**。
  把回信做成 ``MessageState`` 的一个取值，就得让「这封信的状态」与「这封信引来
  的对话」共用一个字段，而对话比信长。

``DELIVERY_UNKNOWN`` 是一个**必须存在**的取值：一个「对面可能收了、也可能没收」
的结果，处置是**先对账再决定**，不是重发。它与 ``FAILED`` 分开，是因为
``FAILED`` 可以重发，而重发一个 ``DELIVERY_UNKNOWN`` 就是让人收到两封。

## 一条 ``Draft`` 的正文改动是**新的一版**，不是一次编辑

:class:`OutreachDraft` 冻结，``supersedes`` 指着上一版。§5 逐字：「内容更新形成
新版本」，而 §6 的推论是：**任何一次受控内容改动都让上一次授权失效**。
草稿可就地改的实现里，「授权之后有人把正文改了」在记录上不留任何痕迹 ——
因为那条记录只有一个 ``draft_id``，而它指向的是**改过之后**的正文。

于是 :class:`OutreachManifest` 冻的是 ``(draft_id, draft_version,
body_sha256)`` 三元组，而授权绑在 manifest 的摘要上。这不是冗余：摘要算的是
三元组，少了任何一样，改正文就不再改变授权所绑的那个值。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from marketops.domain.base import StrictModel
from marketops.domain.enums import EntryKind, Grade
from marketops.domain.product import ProductRef
from marketops.domain.util import (
    check_canonical_id,
    check_market_country,
    check_market_language,
    primary_language_subtag,
    sha256_text,
    to_utc,
)

#: 正文与主题的长度上限。**不是**排版偏好，是「一次请求不该无限大」：
#: 一个 20 MB 的正文在 SMTP 那一侧会被拒，而拒的理由与「内容不合格」长得一样。
MAX_BODY_CHARS = 200_000
MAX_SUBJECT_CHARS = 400

#: 一次投递尝试在**没有定局**的情况下最多占住多久。
#:
#: 它不是 SMTP 超时（那个由传输层自己管，见 ``ports/outreach.py`` 的
#: ``MailTransport.send``），而是「开出这一行的那个驱动还活着吗」这个问题的
#: 时间下界 —— 一个进程被杀掉之后不会有人来 ``finish_attempt``，**没有任何一次
#: 调用会失败**，那一格就这样永远占着。没有这个界，「这一封不是我的」与
#: 「这一封没人管」在盘上长得一样，而两者的处置相反（等着 / 重开）。
#:
#: 取值来源：它必须**明显长于**一次投递的正常耗时，否则一个慢但对端正常的投递
#: 会被判成僵尸，于是同一个人收到两封；又必须**明显短于**一次调度周期，
#: 否则一次崩溃会让这一封错过它的时间窗。15 分钟对着的是「连接 + 握手 + DATA
#: 最多几分钟」这个量级。它是**未标定的默认值**，不是业务事实 ——
#: 与 ``domain/maintenance.py`` 的 ``RefreshPlan.uncalibrated_default()`` 同一条。
DEFAULT_ATTEMPT_LEASE = timedelta(minutes=15)


class OutreachStage(str, Enum):
    """一封信在**触达序列**里的位置。

    取值就是段数本身（1..4），不是名字。§6 逐字：「touch 2–4 必须严格读已确认的
    上一阶段与允许的等待期/次数」。所以「这是第几段」是一个**必须可比较的量**
    —— 用 ``intro`` / ``nudge`` 这类名字，比较就得先有一张表，而那张表迟早会
    有一处漂移。

    段数之上可以贴业务名（``stage-1`` 常叫「首次接触」），那是模板族的事，
    由 :attr:`TemplateFamily.stage` 之外的一个字段承载 —— 名字属于**话术**，
    不属于流程。
    """

    STAGE_1 = "stage-1"
    STAGE_2 = "stage-2"
    STAGE_3 = "stage-3"
    STAGE_4 = "stage-4"

    @property
    def ordinal(self) -> int:
        """第几段（1 起）。"""
        return int(self.value.rsplit("-", 1)[1])

    @property
    def previous(self) -> "OutreachStage | None":
        """上一段；第一段没有上一段。

        返回 ``None`` 而**不是**自身，是为了让「这是第一段」与「这是第二段」
        在调用点必须分开写 —— 一个返回自身的实现会让「读上一阶段」在没有上一
        阶段时读到这一阶段，于是第一封信被要求「先有已确认的上一阶段」。
        """
        if self.ordinal == 1:
            return None
        return OutreachStage(f"stage-{self.ordinal - 1}")


class DraftState(str, Enum):
    """这份**稿子**在生成侧到哪一步了。与它发没发出去**无关**。"""

    GENERATED = "generated"
    #: 生成不出来的那一类。它与「还没有生成」不同：后者是「还没跑」，
    #: 前者是「跑过了，缺输入」。把两者都叫 ``pending`` 会让一次缺
    #: ``facts_used`` 的失败在监控上永远是「队列里还有活」。
    HELD = "held"
    #: 正文写出来了，但这条路线**还不够直接进发送队列**。
    #:
    #: 它与 :attr:`HELD` 是**两个方向**，合成一个的代价是处置反过来：
    #:
    #: * ``HELD`` —— 「这一稿不能用」。原因在**文本**这一侧（缺一条个性化事实、
    #:   写作者给的东西构造不出草稿）。要做的下一步是**补输入**或换写作者。
    #: * ``REVIEW_ONLY`` —— 「这一稿可以用，但要有一个人看过才许发」。原因在
    #:   **路线**那一侧（绑定没人判过、时效没判过或判成不成立）。要做的下一步是
    #:   **去核那条路线**。
    #:
    #: §8 逐字：「准入、当前性或绑定不够时只给明确标注的 review-only/held 状态，
    #: **不称 outreach-ready**」。这一档就是那句话里的 review-only，而它必须是一个
    #: **状态**而不是一条备注：真正的发送侧（``prepare_wave``）只放行
    #: :attr:`GENERATED`，所以标成这一档的草稿**进不了清单** —— 一个只写在备注里
    #: 的标记挡不住任何人，而它会让人以为挡住了。
    REVIEW_ONLY = "review_only"
    SUPERSEDED = "superseded"
    DISCARDED = "discarded"


class MessageState(str, Enum):
    """**这一次投递**在发送侧到哪一步了。见模块 docstring 的三件事之分。"""

    #: 还没有出去。这是**新建** :class:`OutboundAttempt` 的起始态。
    NOT_SENT = "not_sent"
    #: 对面接收了这次连接。它只说明「我们的字节出去了」。
    ACCEPTED_BY_PROVIDER = "accepted_by_provider"
    #: 收到了一条投递确认。比上面强，但**仍不等于**对方读了。
    DELIVERED = "delivered"
    #: **可能出去了、也可能没出去**（超时、连接中断且没有确定回复）。
    #: 处置是**先对账**，不是重发 —— 重发它就是让人收到两封。
    DELIVERY_UNKNOWN = "delivery_unknown"
    #: 对面明确拒了，或者没出去。**可以重试**（而 ``DELIVERY_UNKNOWN`` 不可以）。
    FAILED = "failed"
    #: 被退信（硬退/软退由 :class:`CorrespondenceKind` 细分）。
    BOUNCED = "bounced"


#: 到达这些状态之后**不得**再发一次同一个逻辑发送。它是主键级去重的执行器：
#: 一个实现若允许从 ``DELIVERY_UNKNOWN`` 直接重发，取值的集合就知道它错了。
SENT_OR_UNKNOWN: frozenset[MessageState] = frozenset(
    {
        MessageState.ACCEPTED_BY_PROVIDER,
        MessageState.DELIVERED,
        MessageState.DELIVERY_UNKNOWN,
        MessageState.BOUNCED,
    }
)


class CorrespondenceKind(str, Enum):
    """对面**回了什么**。闭集，七个成员。

    分类的判据与处置逐条来自 SOP（本模块不发明新的一类）：

    ``HUMAN_REPLY``
        一个真人写的、**内容相关**的回复。处置：**停止**相关的自动跟进，
        并产出一条有证据的 :class:`SalesHandoff`。
    ``REFUSAL``
        明确的拒绝（「不要联系我」「不需要」）。处置：停止跟进。
    ``UNSUBSCRIBE``
        要求退出名单。处置：**立刻**进持久抑制。它与 ``REFUSAL`` 分开，
        因为抑制是一个**跨 Campaign 长期有效**的事实，而拒绝是关于这次接洽的。
    ``COMPLAINT``
        投诉。处置同退订（进抑制），但要**另外**留一条人看的记录。
    ``HARD_BOUNCE``
        地址不存在。处置：按 SOP 抑制，并保留证据。
    ``SOFT_BOUNCE``
        临时失败（邮箱满、服务器暂时不可用）。处置：**不抑制** ——
        一个满了的邮箱不是一个人离开了。
    ``AUTO_REPLY``
        自动回复 / 不在办公室 / 收件回执。**不是**一个有效的人类回复，
        因此既不停跟进也不产生交接（§7 逐字）。
    """

    HUMAN_REPLY = "human_reply"
    REFUSAL = "refusal"
    UNSUBSCRIBE = "unsubscribe"
    COMPLAINT = "complaint"
    HARD_BOUNCE = "hard_bounce"
    SOFT_BOUNCE = "soft_bounce"
    AUTO_REPLY = "auto_reply"


class CorrespondenceDisposition(str, Enum):
    """一条进来的事件**被怎么处置了**。也是闭集。

    它与 :class:`CorrespondenceKind` 分开，因为「对面说了什么」与「我们做了什么」
    是两件事，而它们**不是一对一**：``AUTO_REPLY`` 与 ``SOFT_BOUNCE`` 都落到
    ``RECORD_ONLY``，``HUMAN_REPLY`` 落到 ``HANDOFF``。合成一个字段的那一天，
    「分了类但没处置」就写不出来了 —— 而那正是最常见的失败。
    """

    #: 停止该主体的自动跟进，并产出销售交接。
    HANDOFF = "handoff"
    #: 停止自动跟进，**不**交接（明确拒绝）。
    STOP_FOLLOWUP = "stop_followup"
    #: 进持久抑制名单。
    SUPPRESS = "suppress"
    #: 记录，然后交给人看（拿不准的那一类）。
    MANUAL_REVIEW = "manual_review"
    #: 只记录，不做别的（自动回复、软退信）。
    RECORD_ONLY = "record_only"


#: 分类 → 处置。**唯一**一处把两者对上，而它是一张可遍历的表。
#:
#: §7 逐字：「模糊回信 → manual_review_hold，不默认发外部通知卡」、
#: 「字面上的替代联系人 → awaiting_Member_A_confirmation，不自动重发或新建外联」。
#: 后两者不在这个闭集里 —— 它们是 :class:`CorrespondenceKind` 加一个
#: :class:`CorrespondenceDisposition` 也说不完的情形，所以它们由
#: ``HUMAN_REPLY`` + 一条**具体的** ``hold_reason`` 承载（见 ``application/outreach.py``
#: 的路由函数）：一个说不清的回复与一个「要求换人联系」的回复，都必须落到
#: 人手上前，而它们的理由不同。
KIND_DISPOSITION: dict[CorrespondenceKind, CorrespondenceDisposition] = {
    CorrespondenceKind.HUMAN_REPLY: CorrespondenceDisposition.HANDOFF,
    CorrespondenceKind.REFUSAL: CorrespondenceDisposition.STOP_FOLLOWUP,
    CorrespondenceKind.UNSUBSCRIBE: CorrespondenceDisposition.SUPPRESS,
    CorrespondenceKind.COMPLAINT: CorrespondenceDisposition.SUPPRESS,
    CorrespondenceKind.HARD_BOUNCE: CorrespondenceDisposition.SUPPRESS,
    CorrespondenceKind.SOFT_BOUNCE: CorrespondenceDisposition.RECORD_ONLY,
    CorrespondenceKind.AUTO_REPLY: CorrespondenceDisposition.RECORD_ONLY,
}


#: 这些分类**立刻**产生一条持久抑制。
#:
#: **它由 :data:`KIND_DISPOSITION` 推出来，不是另写一张。** 这里原先是手写的一张
#: 集合，而它漂过一次：``REFUSAL`` 在集合里，处置却是 ``STOP_FOLLOWUP`` —— 同一个
#: 文件里的两张表对同一件事给了两个答案。修法不是把两张都改对（下一次照样会漂），
#: 是让它**只剩一个来源**：一个分类要不要抑制，答案在处置表上已经写着了。
#:
#: 而它同时解释了 ``REFUSAL`` **为什么不在**这里（§7 逐字：「明确拒绝 → 停止跟进，
#: 退订/投诉 → 立即持久抑制」）：抑制是一个**跨 Campaign 长期有效**的事实，而拒绝
#: 是关于这次接洽的。两份都读得通的表，差的是那个人**以后还会不会被联系到**，所以
#: 边界按字面取：只有要求退出名单 / 投诉 / 硬退信进抑制。拒绝走的是另一条路 ——
#: :func:`stops_further_outreach`，它停的是跟进，不是这个人的存在。
#:
#: 做成一张可遍历的表而不是散在分支里，理由不变：一个漏掉 ``COMPLAINT`` 的实现，
#: 症状是那个人继续收到信，而记录上只显示「分类正确」。
SUPPRESSING_KINDS: frozenset[CorrespondenceKind] = frozenset(
    kind
    for kind, disposition in KIND_DISPOSITION.items()
    if disposition is CorrespondenceDisposition.SUPPRESS
)


def stops_further_outreach(kind: CorrespondenceKind) -> bool:
    """这条回信是否**停止这个人后续的自动外联**（跨批次，不只是这一批）。

    §7 逐字：「有效人工回复 → 停止相关自动跟进」、「明确拒绝 → 停止跟进」、
    「新到的人类回信应先保守地暂停后续外联，然后再分类」。三句话指向同一个动作，
    而它必须有**一个**可执行的答案 —— 这是那个答案，出信侧（``plan_wave`` /
    ``dispatch_wave``）按它筛人。

    ## 判据是「处置不是 ``RECORD_ONLY``」，这是一个刻意的方向

    只有两个分类不停：``AUTO_REPLY``（机器回的不是人）与 ``SOFT_BOUNCE``
    （一个满了的邮箱不是一个人离开了）。其余一律停 —— 包括 ``MANUAL_REVIEW``：
    「拿不准，交给人看」的那一刻继续自动发信，恰好是这一条要防的事。

    写成「不等于 ``RECORD_ONLY``」而不是「等于某张停发集合」，是因为两张表的
    **失效方向**不同：``CorrespondenceDisposition`` 日后加一个成员时，前者把它
    当**停**（多停一封，一个人少收一封信），后者把它当**不停**（少停一封，一个
    说不要的人继续收到信）。同一个文件里已经有一次「两个答案都读得通」的教训，
    这里选的是后果可撤销的那一边。
    """
    return KIND_DISPOSITION[kind] is not CorrespondenceDisposition.RECORD_ONLY


class WaveState(str, Enum):
    """一批待发信的状态。**缺省停在** :attr:`PREPARED_PENDING_AUTHORIZATION`。

    ``AUTHORIZED`` 是**一份授权存在**这件事的投影，不是「有人点了头」：
    见 :class:`WaveAuthorization`。
    """

    PREPARED = "prepared"
    PREPARED_PENDING_AUTHORIZATION = "prepared_pending_authorization"
    AUTHORIZED = "authorized"
    #: 授权被撤回，或者内容变了导致上一次授权失效。
    AUTHORIZATION_LAPSED = "authorization_lapsed"
    #: 发送在进行中（有至少一次尝试且未全部终结）。
    SENDING = "sending"
    SETTLED = "settled"
    CANCELLED = "cancelled"


class WaveTransitionError(ValueError):
    """一次非法的 Wave 状态转移。**与 ``ConfigError`` 分开**：处置不同。

    ``ConfigError`` 是「这个部署配错了」——处置是改配置；这是「这一步现在不能做」
    ——处置是**先看为什么**（授权过期了？内容改了？已经在发了？）。
    消息里必须同时有当前状态与目标状态：只报「不能这么转」的回执，读的人无从
    判断是自己顺序错了还是这一批正在被别人处理。
    """


#: 合法转移。做成表而不是散在分支里：终态的出边是**空集**，
#: 而不是「有一条边但被拒绝」。
WAVE_TRANSITIONS: dict[WaveState, frozenset[WaveState]] = {
    WaveState.PREPARED: frozenset(
        {WaveState.PREPARED_PENDING_AUTHORIZATION, WaveState.CANCELLED}
    ),
    WaveState.PREPARED_PENDING_AUTHORIZATION: frozenset(
        {WaveState.AUTHORIZED, WaveState.CANCELLED}
    ),
    WaveState.AUTHORIZED: frozenset(
        {WaveState.SENDING, WaveState.AUTHORIZATION_LAPSED, WaveState.CANCELLED}
    ),
    WaveState.AUTHORIZATION_LAPSED: frozenset(
        {WaveState.PREPARED_PENDING_AUTHORIZATION, WaveState.CANCELLED}
    ),
    WaveState.SENDING: frozenset({WaveState.SETTLED, WaveState.AUTHORIZATION_LAPSED}),
    WaveState.SETTLED: frozenset(),
    WaveState.CANCELLED: frozenset(),
}


class HandoffState(str, Enum):
    """销售交接走到哪一步了。§7 要求把这三件事**分开**。

    分开的理由是它们各自的失败不同：``PENDING_LOCAL`` 堆积意味着**没有通知出去**；
    ``NOTIFIED`` 堆积意味着通知出去了但没人接手（这是销售侧的问题，不是系统的）；
    ``ACCEPTED`` 是唯一一个「这件事有人管了」的状态。
    合成一个 ``SENT`` 会让上面两种堆积在报表上长得一样。
    """

    #: 本仓已经产出这条交接，**还没有**推给任何外部系统。
    PENDING_LOCAL = "pending_local"
    #: 已经推给外部系统，且拿到了写回确认（行 id + 版本）。
    NOTIFIED = "notified"
    #: 销售侧明确接下。
    ACCEPTED = "accepted"


class SuppressionReason(str, Enum):
    """为什么这个收件人进抑制名单。

    与 :class:`CorrespondenceKind` **不合并**，虽然今天它们大体一一对应：
    抑制是一个**跨 Campaign 长期有效**的事实，而它还可以来自别的入口
    （业务方手工要求、监管名单）。合并之后，一次手工抑制就必须伪装成一条回信。
    """

    UNSUBSCRIBE = "unsubscribe"
    REFUSAL = "refusal"
    COMPLAINT = "complaint"
    HARD_BOUNCE = "hard_bounce"
    OPERATOR = "operator"


# --- 触达规则的**声明**：数字住在 Scene 的一个版本上 -------------------------
#
# 这一族模型回答「这个市场的触达节奏是什么」，而它为什么必须在**领域**这一层
# 而不是 ``application/outreach.py``：``SceneVersion`` 是声明规则的唯一载体
# （``RefreshPlan`` 已经确立了这个位置），而 ``domain/`` 不依赖 ``application/``。
# ``application`` 那一侧拿它**换算**成自己用的 ``OutreachRules``。


class StageWait(StrictModel):
    """某一段要求的最短等待期。**下界**，不是目标。

    「至少等这么久」与「正好等这么久」在编排上是两件事：前者允许一次调度晚到
    几小时，后者会把一次正常的时间漂移判成违规。所以这个字段的语义逐字是下界。

    单位是**小时**而不是天：第 2–4 段之间的间隔在业务上常常是「隔一天」与
    「隔两天」的区别，而按天声明会把「隔一天半」这种真实存在的配置逼成一个
    四舍五入 —— 要么声明成 1、要么声明成 2，而两者都不是有人批准过的那个数。
    """

    stage: OutreachStage
    after_hours: int
    #: 这个数字有没有标定过。**默认 False** —— 缺省是「未标定」，不是「已批准」。
    calibrated: bool = False

    @field_validator("after_hours")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                "after_hours 必须 >= 1。0 或负数会让这一段**永远**满足等待期，"
                "于是四封信可以在一个 tick 里连着出去 —— 那正是 §6 说的"
                "「臆造间隔」，只是它以 0 的形式出现，而 0 看起来像「不等」。"
            )
        return value


class StageTouches(StrictModel):
    """某一段之上允许有几封**已确认**的前序段（含前序段本身）。"""

    stage: OutreachStage
    allowed_prior: int
    calibrated: bool = False

    @field_validator("allowed_prior")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("allowed_prior 不得为负：一个负的下限会让这一段永远超限")
        return value


class OutreachPolicyDecl(StrictModel):
    """一个 Scene 版本上声明的触达规则。**每一个数都要人给。**

    ## 为什么在 Scene 上，而不是一份全局配置

    §6 逐字：「touch 2–4 必须严格读已确认的上一阶段与允许的等待期/次数，
    **未配置时不得臆造间隔**」。把节奏放在一份全局配置里，改它的动作在记录上
    不留下任何痕迹 —— 而「这个市场从上个月起改成隔三天了」恰恰是一件需要留痕的
    决定。放在 Scene 的版本里，改节奏就是**新写一版 Scene**，于是它自动获得
    版本边界、批准记录与「哪些已经做过的事要重做」的影响面（``add_scene_version``）。

    ## 它还多带来一件 §6 要的事

    授权绑的是清单摘要，而清单摘要是 ``(draft_id, draft_version, body_sha256)``
    的摘要。节奏改了、清单不变时摘要**不会**变 —— 于是「用一套新节奏去发一批
    按旧节奏批过的信」在盘上看不出来。所以 :meth:`OutreachRules` 的构造点读的是
    **生效的那一版**，而 ``application.dispatch_wave`` 每次都从库里取批量所属的
    Scene。这一条在这里写下来，是因为它是「规则为什么必须挂在版本上」的第二个
    理由，而它比第一个更不容易想到。

    ## 缺省是**未标定**的，而且每一份回执都会说出来

    与 ``RefreshPlan.uncalibrated_default()`` 逐条同构：这几个数不是业务事实，
    是一组明确标注为「未标定」的起点，让系统在拿到获批策略之前也能运转，并且
    **在每一份用到它的回执里都说得出这一点**。改它们只能通过一个新版本，
    所以改动会留痕。
    """

    max_per_wave: int
    max_per_recipient: int
    max_per_account: int = 1
    waits: tuple[StageWait, ...]
    prior_touches: tuple[StageTouches, ...]

    @field_validator("max_per_wave")
    @classmethod
    def _wave_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_per_wave 不得为负")
        return value

    @field_validator("max_per_recipient", "max_per_account")
    @classmethod
    def _at_least_one(cls, value: int, info) -> int:
        if value < 1:
            raise ValueError(
                f"{info.field_name} 不得小于 1。0 会让每一条路线都被判成「超了」，"
                "而报告上只显示「这一批没人可发」—— 与「池子是空的」逐字相同。"
            )
        return value

    @model_validator(mode="after")
    def _one_entry_per_stage(self) -> "OutreachPolicyDecl":
        """等待期与前序段上限**各自**覆盖第 2 段到第 4 段，一条不多一条不少。

        第 1 段**不在**这个集合里，而这不是省略：第一段没有上一段，所以
        「等上一段多久」对它没有意义，也没有前序段可数。给它配一条的效果是让
        它永远走 ``wait_for`` 与 ``prior_touch_budget`` —— 两个都不会被读到，
        而配置里的人会以为自己设了什么。
        """
        for field_name in ("waits", "prior_touches"):
            stages = [row.stage for row in getattr(self, field_name)]
            duplicates = sorted({s.value for s in stages if stages.count(s) > 1})
            if duplicates:
                raise ValueError(f"{field_name} 里同一个 stage 配了多次：{duplicates}")
            if OutreachStage.STAGE_1 in set(stages):
                raise ValueError(
                    f"{field_name} 不得包含 {OutreachStage.STAGE_1.value}："
                    "第一段没有上一段，这一条对它既不会被读到，也不该被配。"
                )
            missing = [
                stage.value
                for stage in (OutreachStage.STAGE_2, OutreachStage.STAGE_3, OutreachStage.STAGE_4)
                if stage not in set(stages)
            ]
            if missing:
                raise ValueError(
                    f"{field_name} 缺段：{missing}。缺一段不是「这一段不设限」，是"
                    "「没配」—— 而没配时编排层会拒绝发这一段（§6：未配置时不得"
                    "臆造间隔）。构造期就要求齐全，好过在第一次发第三段时才发现。"
                )
        return self

    @property
    def uncalibrated(self) -> tuple[str, ...]:
        """还没标定的那几项，**点名**。

        与 ``RefreshPlan.uncalibrated`` 同一条：报告里说得出「哪几格还没标定」，
        人才知道该去批哪一份策略。只说一个布尔会让「全部已标定」与
        「一项已标定、四项没有」在报告上长得一样。
        """
        pending: list[str] = []
        if not all(row.calibrated for row in self.waits):
            pending.extend(
                f"outreach.waits[{row.stage.value}]"
                for row in self.waits
                if not row.calibrated
            )
        if not all(row.calibrated for row in self.prior_touches):
            pending.extend(
                f"outreach.prior_touches[{row.stage.value}]"
                for row in self.prior_touches
                if not row.calibrated
            )
        return tuple(pending)

    @property
    def is_calibrated(self) -> bool:
        """等待期与前序段上限**全部**标定过才算标定过的策略。"""
        return not self.uncalibrated

    @classmethod
    def uncalibrated_default(cls) -> "OutreachPolicyDecl":
        """未标定的缺省策略。

        **这几个数不是业务事实。** 它们的取法只有一条依据：序列上相邻两段之间
        留出一个完整的自然日，让收件人至少有一次在自己的工作周里看到上一封的
        机会。这条依据写在注释里，是因为没有它这几个数就只是随手写的 ——
        而随手写的数被写进代码之后，读的人会把它当成有人批准过的节奏。
        """
        return cls(
            max_per_wave=20,
            max_per_recipient=4,
            max_per_account=1,
            waits=(
                StageWait(stage=OutreachStage.STAGE_2, after_hours=48),
                StageWait(stage=OutreachStage.STAGE_3, after_hours=96),
                StageWait(stage=OutreachStage.STAGE_4, after_hours=144),
            ),
            prior_touches=(
                StageTouches(stage=OutreachStage.STAGE_2, allowed_prior=1),
                StageTouches(stage=OutreachStage.STAGE_3, allowed_prior=2),
                StageTouches(stage=OutreachStage.STAGE_4, allowed_prior=3),
            ),
        )


class TemplateFamily(StrictModel):
    """一族模板：**同一个触达阶段、同一个角色路线**共用的一套配置。

    §5 逐字：「按触达阶段与角色路线组织模板族，共享地区/语言/产品配置」。
    这里的字段就是那句话，一个不多：

    * 阶段（:attr:`stage`）与角色类别（:attr:`role_class`）是**组织维度**；
    * ``locale`` / ``product`` 是**共享配置** —— 它们不属于某一封信，
      属于这一族；
    * :attr:`writing_policy_id` 与 :attr:`approved_claim_ids` 是这一族**允许说
      什么**的边界。

    ## 为什么不是「几百份静态模板」

    §5 第一句逐字：「不要预生成几百份静态模板」。所以这一层存的是**族与策略**，
    正文由 ``application/outreach.py`` 一次一封地生成。预生成的静态模板有一个
    立刻的后果：改一处话术要动几百个文件，于是没人改，于是所有人收到的都是
    三个月前那一版 —— 而盘上它们全都是「已批准」。
    """

    family_id: str
    #: 这一族的第几版。**必填、无缺省** —— 与 ``OutreachDraft.draft_version`` 逐字
    #: 同一条理由：一个缺省值会让「我没说清这是哪一版」变成一次静默的重发。
    #:
    #: 它在 §7 的两个要求上是承重的。**「历史修订不可变」**：一版发出去之后，同一个
    #: ``(family_id, revision)`` 配另一份内容会被存储层拒绝，而理由不是洁癖 —— 一条
    #: 已经生成过的草稿记的是 ``(family_id, revision)``，改掉那一版的内容会让那句
    #: 记录**指向一份它当时没见过的配置**，而盘上看起来一切正常。**「版本化」**：
    #: 改话术的正确动作是发下一版，而不是原地覆盖 —— 于是旧草稿仍然查得到它按哪一版
    #: 写的。
    revision: int = Field(ge=1)
    stage: OutreachStage
    role_class: str
    locale: str
    product: str
    writing_policy_id: str
    #: 这一族**允许引用**的已批准说法。空是合法的（有些族只说事实、不加主张），
    #: 但一个引用了不在这个列表里的说法会被草案层拒 —— 见 ``ApprovedClaim``。
    approved_claim_ids: tuple[str, ...] = ()
    note: str = ""

    @field_validator("family_id", "writing_policy_id")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("locale")
    @classmethod
    def _locale_is_a_language_tag(cls, value: str) -> str:
        """语言标签。**只校验形状**，不校验它是不是一个真实存在的地区。

        ``en-GB`` 这种形状是下游选模板语气的唯一依据，而一个空的 ``locale``
        在这里读作「没指定」—— 那与「就用这个语言」在报告上长得一样。
        """
        if not value or value != value.strip():
            raise ValueError("locale 必填且不得带首尾空白（如 'en-GB'）")
        if "-" not in value or not value.split("-")[0].isalpha():
            raise ValueError(
                f"locale={value!r} 不像一个语言标签（形如 '<语言>-<地区>'）。\n"
                "只校验形状：本层不认识任何一个具体的地区。"
            )
        return value

    @field_validator("role_class")
    @classmethod
    def _role_class_is_not_empty(cls, value: str) -> str:
        """取值词表由 profile 给（``RoleClass`` 与 ``role-matrix.yaml`` 同源）。

        **本层不认识任何一个取值** —— 与 ``marketops/domain/enums.py`` 的
        ``RoleClass`` 分开，是因为那一个的成员是**判定用**的（含 ``EXCLUDED``），
        而这里只是「这一族面向谁」。引用前者会让本模块被迫在 profile 装载之后
        才能构造，而模板族是**配置**，不是判定。
        """
        if not value or value != value.strip():
            raise ValueError("role_class 必填且不得带首尾空白")
        return value


class WritingPolicy(StrictModel):
    """写一封信用什么规矩。**也是配置**，不是判定。

    §5 的几条落在这里：个性化缺失时用已批准的通稿措辞或**停下**（不是猜）；
    正文与收件人分离（写的人不得改收件人 / 发件人 / 权限 / 抑制 / 发送状态）。

    最后那条落在**接口形状**上而不是字段上：本类没有 ``recipient`` / ``sender``
    / ``suppressed`` / ``sent`` 任何一个字段。写的人拿不到它们，于是也改不了 ——
    一个「有字段但约定不改」的实现，在第二个调用点就会有人改。
    """

    policy_id: str
    #: 这一策略的第几版。理由与 :attr:`TemplateFamily.revision` 逐字相同。
    revision: int = Field(ge=1)
    #: 一条正文里必须出现的变量集合。缺一个 → ``HELD``，不是「留空发出去」。
    #: §5 逐字：「校验 UTF-8、转义、未填充变量」。
    required_placeholders: tuple[str, ...] = ()
    #: 允许出现的链接前缀（完整 URL 也行）。空元组 = **一个链接都不许出现**。
    #: 这是失败关闭的方向：一条没写规则的策略不该被读成「随便放」。
    allowed_link_prefixes: tuple[str, ...] = ()
    #: 一封正文的长度上限（字符）。超过 → ``HELD``。
    max_body_chars: int = Field(default=4_000, gt=0, le=MAX_BODY_CHARS)
    #: 这一族语言必须匹配的标签。``""`` = 不检查。
    required_locale: str = ""
    #: 是否必须有调用方用（call-to-action）。**默认必须有**：一封没有 CTA 的
    #: 商业信在报告里与一封正常的信长得一样，而它多半不会有人回。
    require_call_to_action: bool = True
    #: 这一族在**没有**个性化事实时使用的通稿措辞 id。空 = 必须停下（``HELD``）。
    #: §5 逐字：「当个性化缺失时，使用已批准的通稿措辞或停下，而不是猜」。
    generic_wording_id: str = ""
    note: str = ""

    @field_validator("policy_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return check_canonical_id(value, "policy_id")

    @field_validator("allowed_link_prefixes")
    @classmethod
    def _prefixes_are_absolute(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """只接受 ``http://`` / ``https://`` 开头的**绝对**前缀。

        一个相对的或协议相对的（``//host``）前缀在「这个链接允不允许」这个
        判断上会随上下文变，而那个上下文在写策略这一层不存在。
        """
        for value in values:
            if not value.startswith(("http://", "https://")):
                raise ValueError(
                    f"allowed_link_prefixes 里的 {value!r} 不是绝对 URL 前缀。\n"
                    "许可判断必须与上下文无关 —— 而一个相对前缀的含义取决于"
                    "它出现在哪一封正文里。"
                )
        return values


class ApprovedClaim(StrictModel):
    """一条**已批准的说法**：关于产品能说什么、不能说什么。

    §5 逐字：「不要编造痛点、设备、预算、负责人姓名、购买意向或产品能力」。
    「不要编造」这条纪律要能被执行，前提是**允许说的那些话是有限枚举的** ——
    否则判据只能是「模型说它没编」，而那是模型的自证。

    所以这条对象带 :attr:`source_ref`（这句话从哪来）与 :attr:`expires_at`
    （它到什么时候还算数）。没有来源的说法不是「已批准」，是**某个人记得**。

    ## 作用域是**加**在它上面的，不是它原本的读法

    ``docs/PRODUCT_LINE_AND_SCENE_MODEL.md`` §6 逐字：「复用当前 ApprovedClaim
    并增作用域」。三条 ``product_ref`` / ``markets`` / ``locales`` 是这一族里
    新加的，而**空 = 旧读数**（这条说法不限产品、不限市场、不限语言）。

    这个方向不能反。已经写进盘的那些记录没有作用域，而它们**正在被用**；把空
    读作「哪个市场都不算」会让一条早已在用的说法在某次升级的那一刻全部失效，而
    没有任何人做过那次决定。反过来，一条**新**说法只要写上了 ``markets=("GB",)``，
    它在 FR 就不算数 —— 那半句在这里是判据（:meth:`applies_to`），在 PSM 里是
    「地区版不能偷偷继承无效 claim」。

    ## 「退场」与「过期」是两个事件，这里各有各的位

    撤回一条说法走的是**追加一版**（``ports/claim_withdrawal.py`` 逐字：「撤回之后它
    不再算数，是因为每一个消费者都按 ``ApprovedClaim.is_live_at(moment)`` 筛，而
    ``approved_claims()`` 只回每个 id 的**最新**一版」），而 ``withdraw_claim`` 写
    那一版时只做两件事：``revision + 1``、``expires_at`` 落在撤回那一刻
    （``application/claim_withdrawal.py`` 逐字）。

    于是**一次撤回与一次普通的再批准在期限上逐字同形**：都是「最新版不活」。在
    :attr:`retired` 存在之前，盘上没有任何一处说得出一件事是**哪一种** —— 而这两件
    事在查缺面上要求相反的读法：

    * **最新版续过期**（没有撤回）：被引那一版旧于最新版，而那一版自己的期限在端口上
      **读不出来**，所以如实记进 ``not_reported``、**不拿最新版顶替** ——
      ``application/product_readiness.py::_cited_revision_is_unreadable`` 的 docstring
      逐字是这一条（拿最新版顶替会在最新一版续过期的那天把一条**有效**的引用报成过期）。
    * **撤回了**：整条说法退场，指着一条已经退场的说法启动这件事本身不成立，进
      ``invalid``（处置是去改这一条引用，不是补一份、也不是等一次签字）。

    把两者压成一个判据，会让其中一半变成假话。所以这里给它们各自的位：期限那一轴仍然
    读 :attr:`expires_at`，而「退场」读 :attr:`retired`。
    """

    claim_id: str
    #: 这条说法的第几版。**说法改一个字就是新的一版** —— 「话术微调」在这张表上
    #: 与「换了一句话」是同一件事，而引用它的草稿记的是 ``(claim_id, revision)``。
    revision: int = Field(ge=1)
    text: str
    source_ref: str
    approved_by: str
    approved_at: datetime
    #: ``None`` = 不过期。**显式**写 ``None`` 与忘记写这一格在记录上一样，
    #: 所以本仓的用法是：需要长期有效的说法必须显式给 ``None``，而
    #: 忘了写的那一天会被 ``approved_at`` 的存在提醒（两者总是成对出现）。
    expires_at: datetime | None = None
    #: **退场**：这条说法已经被 ``withdraw_claim`` 撤回，而这个位说的**不是**期限。
    #: ``False`` = 旧读数（盘上写在这个位存在之前的记录读出来就是它）。
    #:
    #: 见类 docstring 的「退场与过期是两个事件」那一节：撤回今天走的是追加一版、
    #: ``expires_at`` 落在撤回那一刻，**与一次普通的再批准在期限上不可判别**。
    #: 这一个位是两个事件各自的承载位置 —— 只有它说得出「这条说法退场了」。
    #: 读它的生产代码是 ``application/product_readiness.py::_offer_invalid``
    #: （判一条引用还有没有一个活着的出处）；``is_live_at`` 也读它，于是
    #: ``application/outreach.py`` 那处按期限筛「这一刻有哪些说法可用」的枚举
    #: 自动把退场的那条筛掉，不必在那里再写一遍。
    retired: bool = False
    #: 这条说法是关于**哪一版**产品的。``None`` = 不绑（旧读数）。
    product_ref: ProductRef | None = None
    #: 这条说法在哪些国家市场算数。空 = 不限（旧读数）。
    markets: tuple[str, ...] = ()
    #: 这条说法在哪些**语言**算数（主语言子标签，``en`` 而不是 ``en-GB``）。
    #: 空 = 不限（旧读数）。
    locales: tuple[str, ...] = ()

    @field_validator("claim_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return check_canonical_id(value, "claim_id")

    @field_validator("markets")
    @classmethod
    def _markets_are_country_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(check_market_country(item, "markets") for item in value)

    @field_validator("locales")
    @classmethod
    def _locales_are_language_subtags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(check_market_language(item, "locales") for item in value)

    @field_validator("text", "source_ref", "approved_by")
    @classmethod
    def _non_empty(cls, value: str, info) -> str:
        if not value or value != value.strip():
            raise ValueError(f"{info.field_name} 必填且不得带首尾空白")
        return value

    @field_validator("approved_at", "expires_at")
    @classmethod
    def _aware(cls, value: datetime | None, info) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    def is_live_at(self, at: datetime) -> bool:
        """这条说法在 ``at`` 这一刻还算数吗。

        **到期当天算活着**：``expires_at`` 是「到这一刻为止」，不是「这一刻起失效」。
        两种读法都说得通，所以必须挑一个并写下来 —— 一个每次都由实现决定边界
        的实现，会让「上周过的期」和「今天过的期」在报告上同一时刻翻面。

        这只是问题的一半。**「这条说法在这里算不算数」是 :meth:`applies_to`** ——
        生产代码用它（要理由的那一面是 :meth:`why_not_applicable`），不要单独用这个：
        一个只判期限的调用点会把一条 GB 的说法用在 FR 上，而报告上两者长得一样。

        **退场先于期限**（:attr:`retired`）：被撤回的说法在盘上追加的那一版带着
        ``expires_at`` 落在撤回那一刻，所以「退场 ⇒ 不活」在**由 ``withdraw_claim``
        写出来的那些行上**本来也成立。这里仍然显式判它，是因为不这么写就有两个位在
        说同一件事、而其中之一（期限）可能被手工构造出来的行绕过 —— 那时
        ``application/outreach.py`` 那处按期限筛「这一刻有哪些说法可用」的枚举会把一条
        已经退场的说法当活的选出来，而查缺面按 :attr:`retired` 判它退场了：同一份盘上
        状态、两个答案（见类 docstring 的「退场与过期是两个事件」那一节）。
        """
        if self.retired:
            return False
        if self.expires_at is None:
            return True
        return to_utc(at) <= self.expires_at

    def applies_to(
        self,
        *,
        product: ProductRef | None,
        market: str,
        locale: str,
        at: datetime,
    ) -> bool:
        """这条说法在**这一刻、这个产品、这个市场、这门语言**上作数吗。

        `docs/PRODUCT_LINE_AND_SCENE_MODEL.md` §4.2 第 1 步把「来源、适用市场、
        用途限制、claim 期限和负责人」列成**一次**检查。这里把它做成**一个**问题，
        而不是三四个各自可以忘记的判据 —— 「过期的说法」与「跨市场的说法」在
        报告上是同一格，拆开之后调用点只会记住其中一半。

        **空作用域 = 不限**（见类 docstring：那是旧读数）。三格各自独立判：
        ``product_ref`` 为 ``None`` 时任何产品都能问它，写上了就必须
        ``(product_id, version)`` 逐格相等 —— 换一版产品是一次重新核对，不是继承。

        ``market`` / ``locale`` 不合规范时**抛**而不是返回 ``False``：一个写错的
        市场码与一个「该市场不被覆盖」的业务答案在下游是两件事，而 ``False`` 会让
        它们长得一样。

        **为什么叫 ``applies_to`` 而不叫 ``covers``。** 这个模块里已经有一个
        ``covers``（:meth:`WaveAuthorization.covers`，:1404），它问的是「这份授权
        够不够覆盖这份清单」，而且**返回一条理由字符串**而不是布尔 —— 因为
        「不够」那一个字读不出该做什么。两件不同的事共用一个名字，会让读者每次
        都要回头确认自己看的是哪一个。这里的名字说清了自己的形状：一个**用来筛**
        的说法集合上的谓词（「这条说法适用于这里吗」）。

        **它只是 :meth:`why_not_applicable` 的布尔那一面。** 四轴里的哪一轴不
        成立，那个方法说得出；这一格答不了（``True`` / ``False`` 两个字里没有
        「为什么」，而四种不成立要的下一步各不相同）。所以下面的实现只有一句：
        委托过去，一条判据一处定义。
        """
        return not self.why_not_applicable(
            product=product, market=market, locale=locale, at=at
        )

    def why_not_applicable(
        self,
        *,
        product: ProductRef | None,
        market: str,
        locale: str,
        at: datetime,
    ) -> str:
        """这条说法在这一刻 / 这个产品 / 这个市场 / 这门语言上**不作数的理由**；空串 = 作数。

        四轴是 :meth:`applies_to` 逐字列出的那四条（期限 / 产品 / 市场 / 语言），
        而这里是它们**各自的那句话**。分成两张脸而不是两个实现：``applies_to``
        仍然是一个布尔谓词（它的既有判据在 ``tests/test_domain_product.py`` 上，
        一个字都没改），而这一张是把同一个判定**读出来**给一份回执用的。

        ## 为什么理由是这里的事，而布尔不够

        ``applies_to`` 的 docstring 已经论证过「一次检查」的形状 —— 它反对的是
        **调用方各自去记住其中一半**。而它收回一个 ``False`` 之后，调用方能做的
        只有「这一条说法不适用」这一句，而四种不成立的处置**恰恰不同**：

        * 期限过了 ⇒ 去要一条**新的已批准版本**；
        * 绑的是另一版产品 ⇒ 换一条说法，或者改这一版方案引用的是哪一版产品；
        * 市场不覆盖 ⇒ 给这条说法加一条市场作用域（那是一次登记），或者别在这个
          市场上用它；
        * 语言不覆盖 ⇒ 同上，而这一轴与市场是两格（``locales`` 比的是主语言子标签）。

        一句「不适用」把四件事压成一格，读的人下一步还是得回去翻那条说法自己比对
        —— 而在调用方那里再写一份四轴拆解，正是 ``is_live_at`` 那段 docstring 点名
        要防的「同一句话的两个实现」。所以拆解住在这里，调用方只读那一句。

        ## 返回字符串而不抛（除了参数不合规范）

        ``market`` / ``locale`` 不合规范时**抛**，与 :meth:`applies_to` 逐字同一行
        判断（那一条的 docstring 写了理由：一个写错的市场码与一个「该市场不被覆盖」
        的业务答案在下游是两件事）。其余一律返回值：``""`` 是**作数**，其余是理由
        —— 与 ``ports/runs.py`` 那三条 ``*_refusal`` 同形（判据一份、返回字符串、
        由调用方决定放进哪一种拒绝里）。
        """
        check_market_country(market, "market")
        if not self.is_live_at(at):
            expires = self.expires_at.isoformat() if self.expires_at is not None else "（未写）"
            return (
                f"这一版说法在第 {self.revision} 版上写的期限是 {expires}，而这次读的时刻是"
                f" {to_utc(at).isoformat()} —— 已经过了（到期当天算活着）。"
                "要一条新的已批准版本，而不是把过了期的那一条继续用出去。"
            )
        if self.product_ref is not None and product != self.product_ref:
            return (
                f"这一版说法绑的是 product_id={self.product_ref.product_id!r} "
                f"version={self.product_ref.version}，而这次问的是 {product!r}。"
                "换了产品版本是一次**重新核对**，不是继承。"
            )
        if self.markets and market not in self.markets:
            return (
                f"这一版说法只写在 {list(self.markets)} 这些市场里算数，"
                f"而这次问的是 {market}。"
            )
        if self.locales and primary_language_subtag(locale) not in self.locales:
            return (
                f"这一版说法只写在 {list(self.locales)} 这些语言里算数，"
                f"而这次问的是 {locale}。"
            )
        return ""


class DraftRef(StrictModel):
    """**指向**一版草稿的三元组。它本身不是草稿的内容。

    它是 :class:`OutreachManifest` 的单位，也是「内容改动让授权失效」这条性质
    的载体：摘要算的就是这三个字段，所以改正文必然换一个摘要。

    ``draft_version`` 单独存在（而不是只靠 ``body_sha256``）是因为**两个都要**：
    摘要回答「字节一样吗」，版本回答「这是第几稿」。只有摘要时，「恢复到一个
    旧版本再改回新版本」会得到一个与更早那次相同的摘要，而那是两件事。
    """

    draft_id: str
    draft_version: int = Field(ge=1)
    body_sha256: str

    @field_validator("draft_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return check_canonical_id(value, "draft_id")

    @field_validator("body_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _as_sha256(value, "body_sha256")


class OutreachDraft(StrictModel):
    """一封**写好的**信。生成侧的最后一站，也是发送侧的输入。

    ## 它为什么冻结、为什么有版本

    见模块 docstring：「内容更新形成新版本」。就地可改的草稿会让「授权之后有人
    把正文改了」在记录上不留痕。

    ## ``send_authorized`` 恒为 ``False``，且这不是一句约定

    它没有 setter；它不是任何工具的参数；它不读环境变量。真正能产生发出的东西
    是 :class:`OutboundAttempt`，而那需要一个 :class:`WaveAuthorization`。
    字段本身在**类型**上就不可为真 —— 与 ``ContactRoute.send_authorized`` 同型，
    但**独立存在**，理由见模块 docstring（两者的解除条件不同）。

    ## ``facts_used`` 是**待核实的引用**，不是模型的自证

    §5 逐字：「``facts_used`` 是待核实引用，不是模型自证」。所以它的元素是
    ``(事实名, 证据 id)`` —— 一个**指向证据行**的引用，而不是一句「我用了这些
    事实」的散文。下游可以拿它去 ``EvidenceStore`` 查，查不到就是一次
    ``unverified``，而那是**报告上看得见**的一件事。

    ## 一稿要能自己回答「它是怎么做出来的」——§8 的那张清单

    §8 要求可追溯性至少包括：``draft_id`` / ``account_id`` / ``route_ref`` /
    ``scene_id`` / ``scene_version`` / ``writer_id`` / ``family_id`` +
    版本 / ``writing_policy_id`` + 版本 / ``facts_used`` / 证据 id / 主题与正文的
    摘要 / ``created_at`` / ``state``。**这些全是本类上的字段**，所以从库里读回来
    的一行自己回答得了，不必再去翻那一批的回执。

    加进来的那几格不是「顺手补齐」：``scene_version`` / ``family_revision`` /
    ``writing_policy_revision`` / ``writer_id`` 在本轮之前**没有**。少掉它们的
    后果是具体的：模板族的第二版发布之后，一条旧草稿在记录上指向一份它当时没见过
    的配置；而触达规则改版之后，一条旧草稿只报得出 ``scene_id``，报不出它落在哪一
    版规则上 —— 复盘一次误发时第一个要问的正是那一格。两种都是「盘上一切正常」的
    那种坏。
    """

    draft_id: str
    draft_version: int = Field(ge=1)
    #: 这一稿取代了哪一稿。第 1 版为 ``None`` —— **不是 0**（与 ``SceneVersion``
    #: 同一条：0 是一个合法编号的邻居，而「没有上一版」与「上一版是 0」是两件事）。
    supersedes: int | None = None
    workspace: str
    scene_id: str
    #: 这一稿是在 Scene 的**第几版规则**下写的（编号，不是 profile 目录的字节摘要）。
    #:
    #: ## 与 ``ProfileBundle.scene_version`` **不是同一个东西**
    #:
    #: 研究那一段的 ``scene_version`` 是装载器对 profile 目录每一个文件算出的摘要
    #: （``domain/profile.py:524``），是**字符串**；而这一格是 ``SceneVersion.version``
    #: ——「这个市场的第几版规则」，是**整数**。两个子系统各叫各的名字，而它们出现在
    #: 同一份回执里时看起来一模一样。所以这一格的类型是 ``int``：一个把摘要串填进来
    #: 的实现在构造期就红，而不是在有人拿它去查版本的时候。
    #:
    #: 它为什么必须在这一格上：触达规则（每批几封、隔多久、前序段要几封）写在某一版
    #: Scene 上，而**改规则要走一次改版**。一份从库里读回来的草稿若只说得出
    #: ``scene_id``，那「这封信是按哪一版规则写出来的」就没有答案 —— 而那是复盘一次
    #: 误发时第一个要问的问题。
    scene_version: int = Field(ge=1)
    campaign_id: str
    wave_id: str
    #: 这条草稿是为**哪一条路线**写的。发送侧靠它找到收件人，而不是靠正文里的
    #: 任何东西 —— 从正文里解析收件人的实现，会让一封被改过的正文换一个收件人。
    route_ref: str
    account_id: str
    #: 写这一稿时，那条路线是**哪一类入口**、**哪一级**。两格一起冻结。
    #:
    #: §8 逐字：「C/D 草稿需明确是部门/通用入口，不冒充具名决策人」。这句话的落点
    #: 是**草稿上的这一格**，不是路线上那一格 —— 路线会被下一次定级改写（新证据、
    #: 新规则），而这一稿当时是按哪一级、对着哪一类入口写的，是一个**已经发生过
    #: 的事实**，不随路线变。回查路线会让同一份草稿在两次读之间声称两个不同的等级，
    #: 而那正是 §8 那一句「不能静默换文」要挡的形状。
    #:
    #: 两格的缺省是 ``None`` 而**不是**一个像样的取值（``NAMED_ROLE`` / ``Grade.X``）：
    #: 本仓的纪律是未知不得写成一个像样的值。``None`` 在这里读作「这一版草稿没有
    #: 记下这一格」—— 它出现在按 v4 之前的形状写下的旧行上，与「记过、就是具名角色」
    #: 是两件事，而后者会让一条部门入口的旧稿在报告上看着像写给决策人的。
    entry_kind: EntryKind | None = None
    route_grade: Grade | None = None
    #: 模板族与写作策略**各带版本**，而不是只带 id。
    #:
    #: 三张配置表在 v4 之后按 ``(id, 版本)`` 走，而历史版本不可改：一条已经生成的
    #: 草稿记的正是它当时读到的那两个数。只记 id 的话，第二版一族发布之后，这一稿
    #: 在记录上就指向一份它没见过的配置 —— 而盘上一切正常，这正是最难发现的那种坏。
    family_id: str
    family_revision: int = Field(ge=1)
    writing_policy_id: str
    writing_policy_revision: int = Field(ge=1)
    #: 这一稿是谁写的。取自 ``GenerationReceipt.writer_id``，而那个值由
    #: ``DraftWriter`` 端口**自己**报（``ports/outreach.py`` 逐字：「模型名 + 版本，
    #: 或确定性实现的名字」）—— 模型那一版接进来时，模型与运行时身份就在这一格里，
    #: 不需要再开一格。
    writer_id: str
    stage: OutreachStage
    locale: str
    subject: str
    body: str
    #: 正文与主题各自的摘要。**两个分开**：只有正文摘要时，改主题不会让授权失效。
    body_sha256: str
    subject_sha256: str
    #: 引用过的已批准说法 id。**不是**自由文本。
    claim_ids: tuple[str, ...] = ()
    #: ``(事实名, 证据 id)``。见类 docstring。
    facts_used: tuple[tuple[str, str], ...] = ()
    #: 附件的 ``(文件名, 内容摘要)``。**内容摘要**而不是路径：路径可以指向
    #: 一个后来被替换掉的文件，而摘要不会。
    attachments: tuple[tuple[str, str], ...] = ()
    state: DraftState = DraftState.GENERATED
    #: 生成那一刻冻结的输入摘要 —— §5 的幂等键。**同样的输入再次生成得到同一个
    #: 值**，于是「只给新增的或输入变了的对象生成」这句话有一个可执行的判据。
    generation_key: str
    #: 生成动作自己的回执（指 ``application/outreach.py`` 写下的一条生成记录）。
    #: 它回答「这一稿是谁、按哪一版策略、什么时候生成的」。
    generation_receipt_ref: str
    created_at: datetime
    # --- 本阶段硬约束：这一格不能被打开 ---
    #: 与 ``ContactRoute.send_authorized`` 同型但**独立存在**（理由见模块 docstring）。
    #: ``Literal[False]`` 让「传 True」在 pydantic 那一层就构造不出来 ——
    #: 而那正是它的用处：一个带校验器的 ``bool`` 会在**有人拿到实例以后**才报错，
    #: 而这个在**写出 kwargs 的那一刻**就报错。
    send_authorized: Literal[False] = False

    @field_validator("draft_id", "workspace", "scene_id", "campaign_id", "wave_id", "account_id")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("body_sha256", "subject_sha256", "generation_key")
    @classmethod
    def _digest(cls, value: str, info) -> str:
        return _as_sha256(value, info.field_name)

    @field_validator("writer_id")
    @classmethod
    def _writer_named(cls, value: str) -> str:
        """**必填且非空**，所以「不知道是谁写的」不在这个类型里。

        一个缺省成 ``""`` 的写作者身份与「这一稿是谁写的」这个问题没有答案，在报告
        上长得一模一样 —— 而它们是两件事：前者要去修接线，后者要去问写作者。本仓
        对前者的处置是先例（``ApprovedClaim`` 的 ``approved_by`` 同样必填）：一个
        没有来源的批准不是批准，一个说不出作者的稿子也不该只是一格空白。
        """
        if not value or value != value.strip():
            raise ValueError("writer_id 必填且不得带首尾空白")
        return value

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("body")
    @classmethod
    def _body_fits(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("正文不得为空 —— 一封空的商业信不是一个草稿")
        if len(value) > MAX_BODY_CHARS:
            raise ValueError(f"正文超过 {MAX_BODY_CHARS} 字符")
        return value

    @field_validator("subject")
    @classmethod
    def _subject_fits(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("主题不得为空")
        if "\n" in value or "\r" in value:
            raise ValueError(
                "主题不得含换行。\n"
                "一封主题里带换行的信会在拼报文的那一步变成**两个头**，"
                "而第二个头是调用方写的 —— 那是头部注入。"
            )
        if len(value) > MAX_SUBJECT_CHARS:
            raise ValueError(f"主题超过 {MAX_SUBJECT_CHARS} 字符")
        return value

    @model_validator(mode="after")
    def _supersedes_is_the_previous_one(self) -> "OutreachDraft":
        """``supersedes`` 必须**恰好**是上一版。

        它挡的不是笔误，是**跳版**：第 3 稿写 ``supersedes=1`` 时，
        「第 2 稿去哪了」这个问题没有答案，而盘上看起来一切正常。允许多个
        兄弟姐妹的设计（分支）在**这一层**不需要 —— 本轮的改动是线性的，
        线性的东西用一个整数说得清，说清了就不需要一张图。
        """
        if self.supersedes is None:
            if self.draft_version != 1:
                raise ValueError(
                    f"第 {self.draft_version} 稿必须取代上一稿，supersedes 不得为 None"
                )
            return self
        if self.supersedes < 1:
            raise ValueError(
                f"supersedes={self.supersedes} 不是一个版本号。\n"
                "0 是「还没写过」，不是一个可取代的版本。"
            )
        if self.supersedes != self.draft_version - 1:
            raise ValueError(
                f"第 {self.draft_version} 稿的 supersedes 是 {self.supersedes}，"
                f"而它必须是 {self.draft_version - 1}。\n"
                "跳版会让中间那一稿的去向没有答案，而盘上看起来一切正常。"
            )
        return self


class ManifestEntry(StrictModel):
    """冻结清单里的一行：**发给谁、发什么、什么阶段、为什么够格**。

    §6 逐字：「清单固定 workspace/Scene、账号/路线、规范化收件人、发送箱、
    阶段、正文/附件哈希、模板/策略版本、证据、时间窗、数量、筛选结果与有效期」。

    这一行是那句话里「账号/路线、规范化收件人、阶段、正文/附件哈希、
    模板/策略版本、证据」六样的落地；其余五样在 :class:`OutreachManifest` 上，
    因为它们是**整批**的属性，不是某一行的。
    """

    draft: DraftRef
    route_ref: str
    account_id: str
    #: 规范化之后的收件人。**规范化发生在写进清单时**（``normalise_email``），
    #: 于是「同一个邮箱的两种写法」在清单里就已经是同一个值 ——
    #: 去重发生在**生成清单的那一刻**，而不是发送那一刻。
    recipient: str
    sendbox: str
    stage: OutreachStage
    family_id: str
    writing_policy_id: str
    #: 支撑这一行的证据 id。**空列表在这里被拒** —— 一封没有证据的信与
    #: 一封有证据的信在清单上长得一样，而前者是不该发出去的。
    evidence_ids: tuple[str, ...]

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_is_required(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError(
                "清单的每一行都必须有证据。\n"
                "§6 逐字要求清单固定「证据」这一栏：一封没有出处的信与一封有出处的"
                "信，在清单上看起来完全一样 —— 而前者正是「拿不准就发」的形状。"
            )
        return values

    @field_validator("account_id")
    @classmethod
    def _canonical_account(cls, value: str) -> str:
        """账号 id 是 ``AccountRecord`` 那一个，所以它必须**逐字**是同一个值。

        放宽（或在此就地规范化）会造成一个很安静的后果：清单上那一行指向的账号
        与 ``AccountRecord`` 里那条变成两个不同的 id，而**两处都不会报错** ——
        发送侧按账号查历史时查不到，于是同一个人在两批里各收到一封。
        """
        return check_canonical_id(value, "account_id")

    @field_validator("sendbox")
    @classmethod
    def _sendbox_is_normalised(cls, value: str) -> str:
        """发送箱是一个**地址**，所以它走 ``normalise_email``，不走 ``check_canonical_id``。

        两件事要分开：本模块里绝大多数 id 会被拼成文件名（见 ``check_canonical_id``
        的说明），而发送箱不会 —— 它是我们要在上面找信的那个信箱。
        用错判据会造出一个荒唐的结果：``outreach@marketops.example`` 被拒绝，
        理由是「它会被规范成 outreach-marketops.example」。

        使用与 :class:`~marketops.domain.outreach.RecipientSuppression` 的收件人
        同一条规范化，而不是更严的形状校验：本层不认识任何一个具体的邮箱，
        而一个「看起来不像邮箱就拒绝」的判据会把企业内网的地址挡在外面。
        """
        from marketops.domain.util import normalise_email

        if not value or normalise_email(value) != value:
            raise ValueError(
                f"发送箱 {value!r} 不是规范化形式（形如 "
                f"{normalise_email(value) if value else 'outreach@example.co.uk'!r}）。\n"
                "清单上的发送箱要与实际用的那个逐字相等 —— 两者不同时调用方拒绝发送，"
                "而「差一个大小写」在那条判据下看起来与「换了一个信箱」一模一样。"
            )
        return value

    @field_validator("recipient")
    @classmethod
    def _recipient_is_normalised(cls, value: str) -> str:
        from marketops.domain.util import normalise_email

        if normalise_email(value) != value:
            raise ValueError(
                f"收件人 {value!r} 不是规范化形式（形如 {normalise_email(value)!r}）。\n"
                "规范化必须发生在**写清单**的那一刻，否则「同一个邮箱的两种写法」"
                "会在发送那一刻才被发现是同一个 —— 而那时前面的都已经发出去了。"
            )
        return value


class OutreachManifest(StrictModel):
    """**冻结**下来的一批：发出之前它是什么样，之后它还是什么样。

    它冻的是一批信的**全部可比较属性**。授权绑在 :attr:`manifest_sha256` 上，
    于是「改了一个字再发」在任何一处都留下痕迹。

    这不是冗余的簿记。授权绑在 ``wave_id`` 上的实现里，改正文之后那批信仍然
    「有授权」—— 因为编号没变。摘要算的是**内容**，编号算的是**位置**，
    而 §6 要挡的正是「位置没变所以还算有授权」。
    """

    wave_id: str
    campaign_id: str
    workspace: str
    scene_id: str
    entries: tuple[ManifestEntry, ...]
    #: 允许发送的时间窗（闭区间，UTC）。**两个都要有** —— 只有一个开始时刻
    #: 的清单可以在半年后的凌晨三点发出去。
    window_start: datetime
    window_end: datetime
    #: 这条清单允许发出的**上限条数**。它是「这次批了多少封」的显式记录，
    #: 与 ``len(entries)`` 分开：截断过的那一批必须答得出「本来有多少」。
    allowed_count: int = Field(ge=0)
    #: 筛选结果：被排除的去重键与理由。**空是合法的**，但一次真有排除的
    #: 生成过程必须留下它 —— 否则「22 条候选发出去 20 封」这个问题没有答案。
    excluded: tuple[tuple[str, str], ...] = ()
    #: 这份清单什么时候不再算数。与授权各自的到期分开：一个可以在授权仍有效时
    #: 过期，另一个可以在一份很新的清单上被撤回。
    valid_until: datetime
    created_at: datetime

    @field_validator("wave_id", "campaign_id", "workspace", "scene_id")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("window_start", "window_end", "valid_until", "created_at")
    @classmethod
    def _aware(cls, value: datetime, info) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("entries")
    @classmethod
    def _entries_are_unique_by_draft(
        cls, values: tuple[ManifestEntry, ...]
    ) -> tuple[ManifestEntry, ...]:
        """同一版草稿在清单里只能出现一次。

        同一个 ``(draft_id, draft_version)`` 出现两次意味着**同一封信配了两个
        收件人** —— 于是两个人收到逐字相同的信，而其中一个人不在任何路线里。
        收件人层面的重复由 ``recipient`` 的去重挡，这一条挡的是上一层。
        """
        seen: set[tuple[str, int]] = set()
        for entry in values:
            key = (entry.draft.draft_id, entry.draft.draft_version)
            if key in seen:
                raise ValueError(
                    f"草稿 {key[0]} 第 {key[1]} 版在清单里出现了两次。\n"
                    "同一封信不得配两个收件人 —— 那意味着有人在两行里各写了一个"
                    "地址，而其中一行没有对应的路线。"
                )
            seen.add(key)
        return values

    @model_validator(mode="after")
    def _the_window_and_the_counts_agree(self) -> "OutreachManifest":
        if self.window_end <= self.window_start:
            raise ValueError(
                f"发送窗口的结束（{self.window_end.isoformat()}）不晚于开始"
                f"（{self.window_start.isoformat()}）。"
            )
        if self.valid_until < self.created_at:
            raise ValueError("有效期的终点早于创建时刻 —— 这份清单一出生就是过期的")
        if self.allowed_count > len(self.entries):
            raise ValueError(
                f"清单声明最多发 {self.allowed_count} 条，而里面只有 "
                f"{len(self.entries)} 行。\n"
                "上限大于行数会让「这次批了多少封」这个问题失去答案："
                "读的人会以为有一批被截掉了。"
            )
        return self

    @property
    def manifest_sha256(self) -> str:
        """这一批内容的摘要。**授权绑的就是它**。

        把参与者显式列出来（而不是对整个 JSON 求摘要）是一个取舍：加一个新字段
        时，这里**不会**自动把它算进去 —— 而那正是我要的。一个自动跟着模型演化的
        摘要，会在「有人加了个字段」的那一刻静默换掉所有授权的含义，
        而盘上看起来只是「摘要变了」。

        算进去的必须是**决定发出去是什么样**的那些：谁收、哪一稿、哪个阶段、
        哪个发送箱、哪些证据、什么时候能发、能发多少。
        """
        parts = [
            self.wave_id,
            self.campaign_id,
            self.workspace,
            self.scene_id,
            self.window_start.isoformat(),
            self.window_end.isoformat(),
            self.valid_until.isoformat(),
            str(self.allowed_count),
        ]
        for entry in self.entries:
            parts.extend(
                [
                    entry.draft.draft_id,
                    str(entry.draft.draft_version),
                    entry.draft.body_sha256,
                    entry.recipient,
                    entry.sendbox,
                    entry.stage.value,
                    entry.family_id,
                    entry.writing_policy_id,
                    "|".join(entry.evidence_ids),
                ]
            )
        for key, reason in self.excluded:
            parts.extend(["excluded", key, reason])
        return sha256_text("\n".join(parts))


class AuthorizationProvenance(str, Enum):
    """这份授权**经由哪条路径**进入账本。

    ## 它回答的问题，以及它不回答的问题

    它回答：这条记录是**受管入口**写入的，还是任意一段能 import 本模块的代码
    自己构造的。``UNVERIFIED`` 是默认值 —— 直接调用
    :func:`marketops.application.outreach.record_authorization` 造出来的记录
    就是这一格。``BEARER_PRINCIPAL`` 只由 MCP 的 ``authorize_wave`` 写入，
    那一处的主体取自传输层已认证的 Bearer 令牌，不是调用载荷里的字段。

    它**不**回答「这个主体真的有权批准这笔外联吗」。那需要一个可验证的
    外部批准记录来源；本仓今天没有接入这样的来源，所以
    :class:`marketops.security.authorization.NoAuthoritySource` 的判决是
    ``BLOCKED_TRUST_SOURCE``，而不是一个乐观的 ``VERIFIED``。

    ## 为什么默认值必须是「未验证」

    一个默认 ``BEARER_PRINCIPAL`` 的字段会让「忘了传」与「受管入口写入」在盘上
    同形 —— 而前者正是要挡的那条绕过路径。默认拒绝，显式声明才放行。
    """

    UNVERIFIED = "unverified"
    BEARER_PRINCIPAL = "bearer_principal"


class WaveAuthorization(StrictModel):
    """**一份授权**：谁、在什么范围内、批准了哪一批内容。

    ## 它为什么是一个对象而不是一个布尔

    §6 逐字：「授权必须来自可信、独立授权的责任人，或受管授权记录，绑定到确切
    的清单与批准范围，可过期、可撤回」。四件事各自需要一个字段，而一个布尔
    一个都装不下：

    * 「绑定到确切的清单」→ :attr:`manifest_sha256`
    * 「可过期」→ :attr:`expires_at`
    * 「可撤回」→ :attr:`revoked_at` 与 :attr:`revoked_reason`
    * 「来自可信、独立授权的责任人」→ :attr:`authorized_by` 与
      :attr:`authority_ref`（一句话说不清的来源要有一个可查的引用）

    ## 授权**永远**带一个内容摘要，而且判据是重新算的

    ``manifest_sha256`` 不是「构造时抄进来的那个值」，而是每一次投递前
    **从清单重新算出来再比**。抄进来的值只挡得住「有人手工改了授权记录」，
    挡不住「有人改了清单」—— 而后者才是这个字段存在的全部理由。
    """

    authorization_id: str
    wave_id: str
    workspace: str
    scene_id: str
    #: 被批准的那份清单的内容摘要。
    manifest_sha256: str
    #: 谁批的。**这是一个外部主体标识**：本进程不认识、也无法验证它 ——
    #: 能验证的是「这条记录存在且没被撤回」。所以它必须来自一个受管来源
    #: （一条授权记录），而不是某个模型在正文里写下的一句话。
    authorized_by: str
    #: 这份授权的来源引用（一份批准文件、一条审批记录、一个工单）。
    #: 空串 = 「没有出处」—— 而一条没有出处的授权与一次自己给自己发的批准
    #: 在记录上长得一样。
    authority_ref: str
    authorized_at: datetime
    expires_at: datetime
    #: 批准的范围：允许发多少条。**可以小于清单条数**（批准 10 条而清单有 20 条
    #: 是常态）—— 于是它必须单独存在，不能读成 ``len(entries)``。
    approved_count: int = Field(ge=0)
    #: 这份授权**经由哪条路径**进入账本。默认 ``UNVERIFIED``，而
    #: :func:`marketops.application.outreach.dispatch_wave` 只放行
    #: ``BEARER_PRINCIPAL`` —— 于是「绕过 MCP 的 ``authorize_wave``、直接调应用层
    #: 登记一份授权」这条路径默认发不出去。见 :class:`AuthorizationProvenance`。
    provenance: AuthorizationProvenance = AuthorizationProvenance.UNVERIFIED
    revoked_at: datetime | None = None
    revoked_reason: str = ""

    @field_validator("authorization_id", "wave_id", "workspace", "scene_id")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("manifest_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _as_sha256(value, "manifest_sha256")

    @field_validator("authorized_by", "authority_ref")
    @classmethod
    def _non_empty(cls, value: str, info) -> str:
        if not value or value != value.strip():
            raise ValueError(
                f"{info.field_name} 必填且不得带首尾空白。\n"
                "一条说不出「谁批的、凭哪一份记录批的」的授权，与一次把"
                " ``send_authorized`` 直接置真的动作在记录上长得一样。"
            )
        return value

    @field_validator("authorized_at", "expires_at", "revoked_at")
    @classmethod
    def _aware(cls, value: datetime | None, info) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @model_validator(mode="after")
    def _revocation_is_complete(self) -> "WaveAuthorization":
        if self.expires_at <= self.authorized_at:
            raise ValueError(
                "授权的到期时刻不晚于批准时刻 —— 这份授权一出生就已经过期"
            )
        if self.revoked_at is not None and not self.revoked_reason.strip():
            raise ValueError(
                "撤回必须写明理由。\n"
                "一条没有理由的撤回与「授权记录被人改坏了」在盘上长得一样，"
                "而后者要去查。"
            )
        return self

    def is_live_at(self, at: datetime) -> bool:
        """这一刻这份授权还有效吗。**真值判断只有这一处。**

        三个条件缺一不可：没被撤回、还没过期、**并且**已经生效
        （``authorized_at <= at``）。第三个容易被漏掉：一条「明天生效」的授权
        在今天投递时会被前两条放过去。
        """
        moment = to_utc(at)
        if self.revoked_at is not None:
            return False
        return self.authorized_at <= moment <= self.expires_at

    def covers(self, manifest: OutreachManifest, at: datetime) -> str:
        """这份授权**现在**够不够覆盖这份清单。空串 = 够；否则一条说得出的理由。

        五个条件逐条对应一次真实事故。理由返回**字符串**而不是布尔，因为
        「不够」这一个字读不出该做什么：撤回要去问人，内容变了要重新批，
        过期了要续，清单换了是流程错了。

        摘要这一条是**重新算的**（``manifest.manifest_sha256``），不是比对本对象
        里存的那个值 —— 见类 docstring。
        """
        moment = to_utc(at)
        if self.revoked_at is not None:
            return f"授权已于 {self.revoked_at.isoformat()} 撤回：{self.revoked_reason}"
        if moment < self.authorized_at:
            return (
                f"授权自 {self.authorized_at.isoformat()} 起生效，而现在是 "
                f"{moment.isoformat()}。"
            )
        if moment > self.expires_at:
            return (
                f"授权已于 {self.expires_at.isoformat()} 过期，而现在是 "
                f"{moment.isoformat()}。"
            )
        if self.wave_id != manifest.wave_id:
            return f"授权批的是 {self.wave_id!r}，而这份清单是 {manifest.wave_id!r}。"
        if self.scene_id != manifest.scene_id or self.workspace != manifest.workspace:
            return (
                f"授权批的是 workspace={self.workspace!r} / scene={self.scene_id!r}，"
                f"而这份清单属于 workspace={manifest.workspace!r} / "
                f"scene={manifest.scene_id!r}。"
            )
        if self.manifest_sha256 != manifest.manifest_sha256:
            return (
                "这份清单的内容与授权时批的那一份**不一致**："
                f"授权批的是 {self.manifest_sha256[:16]}…，"
                f"现在是 {manifest.manifest_sha256[:16]}…。\n"
                "受控内容发生任何改动都使上一次授权失效 —— 改一个字的信与"
                "原来那封在清单上看起来是同一批，因为编号没变。"
            )
        if self.approved_count > len(manifest.entries):
            return (
                f"授权批准 {self.approved_count} 条，而清单里只有 "
                f"{len(manifest.entries)} 行 —— 这份清单被换过。"
            )
        return ""

    def approved_slice(self, manifest: OutreachManifest) -> tuple[ManifestEntry, ...]:
        """这份授权**实际覆盖**清单里的哪几行。

        §6 逐字要求清单固定「数量」，而「批准 10 条、清单 20 行」是常态。
        范围取的是清单的**前 ``approved_count`` 行** —— 清单的行序是生成时
        定下的（``application/outreach.py`` 按 ``(send_key,)`` 定序），
        所以「第几条」是一个稳定的事实，不是随机的。

        这个方法**必须在拿得到清单的地方调用**（:meth:`covers` 已经保证了
        ``manifest_sha256`` 与行数一致），所以它不接受一份不相干的清单：
        行数对不上时抛，而不是安静地返回与这份清单无关的前 N 行。
        """
        if self.approved_count > len(manifest.entries):
            raise ValueError(
                f"授权批准 {self.approved_count} 条，而清单里只有 "
                f"{len(manifest.entries)} 行 —— 两者不是同一批。"
            )
        return tuple(manifest.entries[: self.approved_count])


class OutboundAttempt(StrictModel):
    """**一次投递尝试**。发送侧的记录，也是「不得重复发」的执行器。

    ## 身份是 ``send_key``，不是这一行的 id

    ``send_key`` 是**逻辑发送标识**：它由
    ``(workspace, wave_id, draft_id, draft_version, recipient)`` 派生，于是
    「同一封信发给同一个人」在整条链上只有一个键 —— 重启、重复 tick、两个驱动
    并发都落在同一个键上。它**不是** Message-ID：Message-ID 是我们写给对方看的
    东西，而它是我们自己去重的依据，两者由不同的系统保证，合并它们等于把
    「对方的服务器怎么想」当成「我们发过没有」。

    ## ``DELIVERY_UNKNOWN`` 的处置写在类型里

    :attr:`state` 落在 :data:`SENT_OR_UNKNOWN` 里时 :attr:`retry_allowed` 为假。
    这不是一句约定：一个从 ``DELIVERY_UNKNOWN`` 直接重发的实现，会让人收到两封，
    而盘上它看起来是「重试了一次失败」。
    """

    send_key: str
    workspace: str
    wave_id: str
    draft_id: str
    draft_version: int = Field(ge=1)
    draft: DraftRef
    route_ref: str
    account_id: str
    recipient: str
    sendbox: str
    stage: OutreachStage
    #: 生成这次尝试时所依据的授权 id。**留在这里是为了事后能回答**
    #: 「这一封当时凭什么能发」—— 没有它，一次越权投递在记录上只是一条记录。
    authorization_id: str
    #: 这次尝试时那份清单的内容摘要。与授权里的那个值比对是**当时**做的，
    #: 而这个字段让那次比对可复现。
    manifest_sha256: str
    state: MessageState = MessageState.NOT_SENT
    attempt: int = Field(default=1, ge=1)
    started_at: datetime
    finished_at: datetime | None = None
    #: 外部系统给的标识（Message-ID、队列 id）。**允许为空**：一次连不上的
    #: 尝试没有它。它**不是**去重依据 —— 见类 docstring。
    provider_message_id: str = ""
    #: 失败或不确定的原因。``FAILED`` / ``DELIVERY_UNKNOWN`` 时必填。
    failure: str = ""

    @field_validator(
        "send_key", "workspace", "wave_id", "draft_id", "route_ref", "account_id"
    )
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("manifest_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _as_sha256(value, "manifest_sha256")

    @field_validator("started_at", "finished_at")
    @classmethod
    def _aware(cls, value: datetime | None, info) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @model_validator(mode="after")
    def _settled_attempts_say_what_happened(self) -> "OutboundAttempt":
        if self.state is MessageState.NOT_SENT:
            if self.finished_at is not None:
                raise ValueError("还没有发出去的尝试不得有完成时刻")
            return self
        if self.finished_at is None:
            raise ValueError(f"{self.state.value} 的尝试必须有完成时刻")
        if self.finished_at < self.started_at:
            raise ValueError("完成时刻早于开始时刻")
        if self.state in (MessageState.FAILED, MessageState.DELIVERY_UNKNOWN):
            if not self.failure.strip():
                raise ValueError(
                    f"{self.state.value} 必须写明原因。\n"
                    "「可能发出去了」与「确定没出去」的处置相反（前者先对账、"
                    "后者可以重发），而缺了原因它们就只剩一个状态码。"
                )
        return self

    def is_stale_at(self, at: datetime, *, lease: timedelta) -> bool:
        """这一次尝试还占着吗 —— 判据是「开出它的那个驱动还在不在」。

        ``FAILED`` 与两个终态都是**已经定局**的，所以答案与时刻无关：
        前者可以重来，后者不可以。只有 ``NOT_SENT``（开出去了、还没结算）
        与时刻有关：超过 ``lease`` 就当作开出它的进程已经不在了。

        收 ``lease`` 而不是读一个模块常量：它是**部署策略**，与
        ``MaintenanceStore.claim`` 的 ``at`` 同一条处置 —— 判据是纯函数，
        参数由调用方给，于是「一个可控时钟下这一格会不会被判成僵尸」
        是可以被测出来的，而不是要在代码里找一个常量。
        """
        if self.state is MessageState.FAILED:
            return True
        if self.state is not MessageState.NOT_SENT:
            return False
        return to_utc(at) - self.started_at >= lease

    @property
    def sent_or_unknown(self) -> bool:
        """这一封**有可能已经出去了**。是的话不得再发一次。"""
        return self.state in SENT_OR_UNKNOWN

    @property
    def retry_allowed(self) -> bool:
        """可以再发一次吗。

        ``DELIVERY_UNKNOWN`` **不可以** —— 见模块 docstring：重发它就是让人
        收到两封。先对账。
        """
        return self.state in (MessageState.NOT_SENT, MessageState.FAILED)


class AttemptOutcome(StrictModel):
    """一次「开始投递」的结果：**这一封现在归我发吗**。

    ## 为什么需要它

    两个驱动同时对同一封信调用 ``begin_attempt`` 时，两个调用都拿到「当前那一行」——
    而只有一个人的插入成功了。第二个人若照着返回的那一行去发，那封信就发了两遍，
    而两份记录完全一致（因为它们是同一行）。真正知道答案的只有执行了那次插入的
    存储，所以由它回话。

    这与 :class:`~marketops.domain.maintenance.ClaimOutcome` 是**同一个形状**，
    但它**不复用**那个类：那个的去重身份是维护格的 ``dedupe_key``，
    这个的是发送的 ``send_key``，而两者的 ``won`` 判据来自不同的表、
    由不同的实现回答。共用一个类会让某一天其中一个的判断被改到另一个身上 ——
    而两者的后果（多做一次复核 / 多发一封信）不一样贵。

    ## ``won=False`` 不是一个错误

    它有三种来源，处置相同（**这一封不是我的**）：别人正在发、别人已经发出去了
    （含 ``DELIVERY_UNKNOWN``）、或者我刚失败而别人先重试了。要问道理就读
    ``attempt``：它的 ``state`` 就是那三种情况的完整区分。
    """

    attempt: OutboundAttempt
    won: bool

    @property
    def state(self) -> MessageState:
        return self.attempt.state


def may_be_settled_by_reconciliation(
    current: OutboundAttempt, incoming: OutboundAttempt
) -> bool:
    """``DELIVERY_UNKNOWN`` 的**唯一**一次改判：对账把它问清楚了。

    ## 为什么这条规矩要有例外

    「一个逻辑发送的结局一次性写下」是有理由的：允许改写它，「这一封到底发出去
    了没有」就没有答案了。而 :attr:`MessageState.DELIVERY_UNKNOWN` **本来就不是
    一个答案** —— 它是「传输抛了，对面可能收了」的那一格，处置是**先对账**。
    所以拒绝一切改判的实现会让这一格永远出不去：既不能重发（§6），也不能结案。
    两个后果同时成立时，这一行就成了一个永远挂着的、报告上看得见的「可能」——

    而这不是宽容：改判仍然只发生**一次**，且只从 ``DELIVERY_UNKNOWN`` 出发。
    一旦写定（``ACCEPTED_BY_PROVIDER`` / ``FAILED`` / ``DELIVERED`` / …），
    它和其它终态一样不可再动。

    ## 它为什么住在 ``domain/``

    判据是**两个状态之间**的关系，与哪一个存储回答它无关。两个适配器各写一份的
    代价不是重复，是有一天两边不一致 —— 而其中一边会让一次「问清楚了」被静默
    丢弃。``tests/test_outreach_store_contract.py`` 对两个实现一起执行它。
    """
    return (
        current.state is MessageState.DELIVERY_UNKNOWN
        and incoming.state is not MessageState.DELIVERY_UNKNOWN
    )


class RecipientSuppression(StrictModel):
    """一个**不得再联系**的收件人。

    ## 它与「这一批不发」的区别

    抑制是一个**跨 Campaign 长期有效**的事实。落在一条路线或一批信上的
    「不发」是另一件事（那一批改了、或者那个账号不再合格）。
    合并两者会让一个退订的人在下一个 Campaign 里重新被选中 —— 而盘上
    前一批的记录完全正确。

    ## 一旦写下就不删

    ``released_at`` 存在是为了记录「有人手工解除了」这件事，而不是让这一行
    消失。一个删行的实现会让「这个人什么时候被解除了抑制」没有答案，
    而那个答案正是下一次事故里要查的第一件事。
    """

    recipient: str
    reason: SuppressionReason
    #: 这条抑制从哪来（一条事件 id、一条工单）。空串 = 没有出处。
    source_ref: str
    suppressed_at: datetime
    #: 解除时刻。``None`` = 仍然有效。
    released_at: datetime | None = None
    note: str = ""

    @field_validator("recipient")
    @classmethod
    def _recipient_is_normalised(cls, value: str) -> str:
        from marketops.domain.util import normalise_email

        if normalise_email(value) != value:
            raise ValueError(f"抑制名单里的收件人必须是规范化形式：{value!r}")
        return value

    @field_validator("suppressed_at", "released_at")
    @classmethod
    def _aware(cls, value: datetime | None, info) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    def is_active_at(self, at: datetime) -> bool:
        """这一刻这条抑制还算数吗。"""
        if self.released_at is None:
            return True
        return to_utc(at) < self.released_at


class CorrespondenceEvent(StrictModel):
    """一条**进来的**事件：回信、退信、退订、投诉。

    ## 正文是**数据**，永远不是指令

    §7 逐字：「回信正文是数据，永远不得成为可执行指令」。这条落在**本类没有
    任何执行入口**上：``body`` 是一个字符串字段，而消费它的唯一路径是
    ``application/outreach.py`` 里那个把 ``kind`` 映射到
    :class:`CorrespondenceDisposition` 的**表**。表上的取值是七个分类，
    而没有一个分类叫「按正文说的做」。所以这件事不是被拦住，是**没有那个形状**。

    ## 去重靠 ``(sendbox, provider_message_id)``，不靠主题

    §7 逐字：「按发件箱/提供方/消息 id 去重，关联发出去的消息/线程/收件人，
    不只是主题」。主题是**对方**可以随手改的东西，而一条改了主题的回信若被去重
    掉，读者看不到它 —— 而它可能正是一封「请不要再联系我」。

    ## ``raw_sha256`` 与 ``raw_ref`` 同时存在

    原始事件必须留到重试那一刻，即使分类失败（§7 逐字）。所以这里的形状是
    「解析结果 + 指向原件的一份引用 + 原件摘要」：摘要回答「是不是同一份原件」，
    引用回答「去哪读它」。只留引用的实现挡不住原件被改写；只留摘要的实现
    在分类失败之后没有东西可重试。
    """

    event_id: str
    workspace: str
    #: 这条事件从哪来（哪个发件箱、哪个 webhook 端点）。
    sendbox: str
    kind: CorrespondenceKind
    #: 提供方侧的**唯一**标识。它与 :attr:`sendbox` 合起来是去重键的一部分。
    provider_message_id: str
    #: 这条事件关联到我们发出去的哪一次尝试（``OutboundAttempt.send_key``）。
    #: 空串 = **关联不上**。那是一个必须存下来的事实：它意味着我们收到了一封
    #: 不知道对应哪封信的回信，处置要交给人看，而不是丢掉。
    send_key: str = ""
    #: 对方的地址（规范化）。空 = 提供方没给。
    sender: str = ""
    subject: str = ""
    #: 正文。**是数据**，见类 docstring。
    body: str = ""
    #: 原件：引用 + 摘要。见类 docstring。
    raw_ref: str
    raw_sha256: str
    received_at: datetime
    #: 我们**什么时候把它落进库里**。与 ``received_at`` 分开：乱序到达是常态，
    #: 而「什么时候知道的」与「什么时候发生的」在事后是两件事。
    ingested_at: datetime
    #: 处置。**必填** —— 一条分了类但没说怎么处置的事件，与一条被丢掉的事件
    #: 在报告上长得一样。
    disposition: CorrespondenceDisposition
    #: 处置已经执行到哪一步（本仓内的完成度）。空 = 还没有执行。
    #: §7 要求「先持久化、再推进游标」，所以这两件事必须分得开。
    settled_note: str = ""
    #: 需要人看时的理由。``MANUAL_REVIEW`` 时必填。
    hold_reason: str = ""

    @field_validator("event_id", "workspace", "send_key")
    @classmethod
    def _canonical_or_empty(cls, value: str, info) -> str:
        """``send_key`` 允许为空（关联不上），其余必须规范。"""
        if info.field_name == "send_key" and not value:
            return ""
        return check_canonical_id(value, info.field_name)
    @field_validator("sendbox")
    @classmethod
    def _sendbox_is_normalised(cls, value: str) -> str:
        """发送箱是一个**地址**，所以它走 ``normalise_email``，不走 ``check_canonical_id``。

        两件事要分开：本模块里绝大多数 id 会被拼成文件名（见 ``check_canonical_id``
        的说明），而发送箱不会 —— 它是我们要在上面找信的那个信箱。
        用错判据会造出一个荒唐的结果：``outreach@marketops.example`` 被拒绝，
        理由是「它会被规范成 outreach-marketops.example」。

        使用与 :class:`~marketops.domain.outreach.RecipientSuppression` 的收件人
        同一条规范化，而不是更严的形状校验：本层不认识任何一个具体的邮箱，
        而一个「看起来不像邮箱就拒绝」的判据会把企业内网的地址挡在外面。
        """
        from marketops.domain.util import normalise_email

        if not value or normalise_email(value) != value:
            raise ValueError(
                f"发送箱 {value!r} 不是规范化形式（形如 "
                f"{normalise_email(value) if value else 'outreach@example.co.uk'!r}）。\n"
                "清单上的发送箱要与实际用的那个逐字相等 —— 两者不同时调用方拒绝发送，"
                "而「差一个大小写」在那条判据下看起来与「换了一个信箱」一模一样。"
            )
        return value


    @field_validator("raw_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _as_sha256(value, "raw_sha256")

    @field_validator("received_at", "ingested_at")
    @classmethod
    def _aware(cls, value: datetime, info) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("sender")
    @classmethod
    def _sender_is_normalised_or_absent(cls, value: str) -> str:
        from marketops.domain.util import normalise_email

        if not value:
            return ""
        if normalise_email(value) != value:
            raise ValueError(f"发件人必须是规范化形式或空串：{value!r}")
        return value

    @model_validator(mode="after")
    def _manual_review_says_why(self) -> "CorrespondenceEvent":
        if self.disposition is CorrespondenceDisposition.MANUAL_REVIEW and not (
            self.hold_reason.strip()
        ):
            raise ValueError(
                "MANUAL_REVIEW 必须写明理由。\n"
                "§7 要求模糊的回复**先保守地暂停后续外联**、然后才分类；"
                "一条说不出为什么拿去给人看的记录，在队列里与一条忘了处理的"
                "记录长得一样。"
            )
        if self.ingested_at < self.received_at:
            raise ValueError(
                f"落库时刻（{self.ingested_at.isoformat()}）早于收到时刻"
                f"（{self.received_at.isoformat()}）—— 我们不可能在它发生之前就知道。"
            )
        return self

    @property
    def dedupe_key(self) -> str:
        """这条事件的去重键。**由内容算，不落第二个字段。**

        键是 ``(sendbox, provider_message_id)`` —— 见类 docstring：主题不参与。
        同一份原件被重放（webhook 重试、游标回退）落到同一个键上，
        于是「重放」与「两条一样的事件」分得开。
        """
        return sha256_text(f"{self.sendbox}\n{self.provider_message_id}")


class SalesHandoff(StrictModel):
    """一条**销售交接**：一个真人的、内容相关的回信值得人去看。

    §7 逐字：「有效人类回信 → 停止相关自动跟进、产出一条有证据的 SalesHandoff」。
    三件事落在三个字段上：:attr:`event_id`（凭哪条回信）、:attr:`evidence_ids`
    （回信里的哪些证据）、以及 :attr:`state`（走到哪一步了）。

    ``state`` 的三值见 :class:`HandoffState` —— 「本仓产出了」「通知出去了」
    「销售接下了」是三件事。
    """

    handoff_id: str
    workspace: str
    scene_id: str
    campaign_id: str
    account_id: str
    route_ref: str
    event_id: str
    recipient: str
    #: 人写的回信正文。**逐字保留** —— 交接的核心就是让销售读原文。
    reply_body: str
    evidence_ids: tuple[str, ...]
    state: HandoffState = HandoffState.PENDING_LOCAL
    created_at: datetime
    #: 推到外部系统之后拿回来的行 id 与版本（§7 的写回读回）。
    external_row_id: str = ""
    external_version: str = ""
    #: 销售侧明确接下的时刻。
    accepted_at: datetime | None = None

    @field_validator("handoff_id", "workspace", "scene_id", "campaign_id", "account_id")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("created_at", "accepted_at")
    @classmethod
    def _aware(cls, value: datetime | None, info) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_is_required(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError(
                "SalesHandoff 必须有证据。\n"
                "§7 逐字要求「有证据的 SalesHandoff」——一条没有证据的交接，"
                "与一次「有人觉得这封该给销售看」在记录上长得一样。"
            )
        return values

    @model_validator(mode="after")
    def _the_state_and_its_fields_agree(self) -> "SalesHandoff":
        if self.state is HandoffState.PENDING_LOCAL:
            if self.external_row_id or self.accepted_at is not None:
                raise ValueError("还没通知出去，就不该有外部行 id 或被接下的时刻")
            return self
        if not self.external_row_id:
            raise ValueError(
                f"{self.state.value} 必须有外部行 id —— 通知出去这件事的证据"
                "就是那一行的 id 与版本。"
            )
        if self.state is HandoffState.NOTIFIED and self.accepted_at is not None:
            raise ValueError("还没被接下，不该有接下时刻")
        if self.state is HandoffState.ACCEPTED and self.accepted_at is None:
            raise ValueError("ACCEPTED 必须有接下时刻")
        return self


class OutreachCampaign(StrictModel):
    """一个**持续**的触达活动：一批路线、一套策略、一段时间。

    它与 :class:`Wave` 的关系是「活动 : 批次」：活动可以说「这个月做 UK 天然草
    的首次接触」，而每一封实际发出去的信都属于**某一个** wave，而 wave 属于
    campaign。

    ``campaign_id`` 是授权与交配上「跨批次的归属」，而 ``wave_id`` 是「这一批」。
    §6 要求清单固定 workspace/Scene —— 那两样在这里也有一份，且必须一致：
    一个 campaign 可以跨 wave，但**不跨 Scene**（跨 Scene 的活动就是两个活动）。
    """

    campaign_id: str
    workspace: str
    scene_id: str
    name: str
    stage: OutreachStage
    family_id: str
    created_at: datetime
    note: str = ""

    @field_validator("campaign_id", "workspace", "scene_id")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("name")
    @classmethod
    def _name_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("campaign 必须有一个用于人读的名字")
        return value


class Wave(StrictModel):
    """一批待发的信 —— **连同它被发现时的状态**。

    状态转移由 :data:`WAVE_TRANSITIONS` 执行，而出边为空的终态
    （``SETTLED`` / ``CANCELLED``）在类型上就不可能再有下一步。
    """

    wave_id: str
    campaign_id: str
    workspace: str
    scene_id: str
    state: WaveState
    #: 当前这份清单的摘要。**它随内容改** —— 而对它的比较由授权侧做
    #: （``WaveAuthorization.covers`` 拿重新算出来的值比）。
    manifest_sha256: str
    #: 乐观锁序号。写法与 :class:`~marketops.domain.scene.SceneRecord` 一致：
    #: 写的时候必须带上「我读到的是第几版」。
    revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    @field_validator("wave_id", "campaign_id", "workspace", "scene_id")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("manifest_sha256")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _as_sha256(value, "manifest_sha256")

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime, info) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @model_validator(mode="after")
    def _updated_is_not_before_created(self) -> "Wave":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 早于 created_at")
        return self

    def can_move_to(self, target: WaveState) -> bool:
        return target in WAVE_TRANSITIONS[self.state]

    def moved_to(self, target: WaveState, *, at: datetime) -> "Wave":
        """一个**新**的 Wave：状态变了、revision 加一。

        非法转移抛 :class:`WaveTransitionError` 而不是静默返回自身 ——
        一个「转不过去就原地不动」的实现，会让调用方以为它转了。
        """
        if not self.can_move_to(target):
            raise WaveTransitionError(
                f"wave {self.wave_id} 现在是 {self.state.value}，不能转到 "
                f"{target.value}。\n"
                f"从 {self.state.value} 出发的合法转移是 "
                f"{sorted(state.value for state in WAVE_TRANSITIONS[self.state]) or '（无：终态）'}。"
            )
        moment = to_utc(at)
        if moment < self.updated_at:
            raise ValueError(
                f"新状态写于 {moment.isoformat()}，早于当前状态的 "
                f"{self.updated_at.isoformat()} —— 时间倒流意味着有人在拿旧值写。"
            )
        return self.model_copy(update={"state": target, "updated_at": moment, "revision": self.revision + 1})


def _as_sha256(value: str, field: str) -> str:
    """一个 64 位小写十六进制摘要。**形状错的值与没有值是同一件事。**

    与 ``domain/receipt.py`` 的 ``artifact_sha256`` 校验器同一条理由：它在报告里
    会看起来像有一个答案，而实际上答案的形状不对。
    """
    digest = value.strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError(f"{field} 不是 64 位十六进制摘要：{value!r}")
    return digest


__all__ = [
    "ApprovedClaim",
    "AttemptOutcome",
    "CorrespondenceDisposition",
    "CorrespondenceEvent",
    "CorrespondenceKind",
    "DEFAULT_ATTEMPT_LEASE",
    "DraftRef",
    "DraftState",
    "HandoffState",
    "KIND_DISPOSITION",
    "MAX_BODY_CHARS",
    "MAX_SUBJECT_CHARS",
    "ManifestEntry",
    "MessageState",
    "OutreachCampaign",
    "OutreachDraft",
    "OutreachManifest",
    "OutreachPolicyDecl",
    "OutreachStage",
    "OutboundAttempt",
    "RecipientSuppression",
    "SENT_OR_UNKNOWN",
    "SUPPRESSING_KINDS",
    "SalesHandoff",
    "StageTouches",
    "StageWait",
    "SuppressionReason",
    "TemplateFamily",
    "WAVE_TRANSITIONS",
    "Wave",
    "WaveAuthorization",
    "WaveState",
    "WaveTransitionError",
    "WritingPolicy",
    "stops_further_outreach",
    "may_be_settled_by_reconciliation",
]

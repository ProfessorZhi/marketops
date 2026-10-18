"""续跑意图：**一次被受理的业务事件欠下的下一步**，以及执行它的那一代的归属。

## 它回答的问题是「谁欠着下一步」

``docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md`` §10 逐字：**「业务事件受理与
continuation intent 原子落账。控制器基于该事件生成下一批/投影/重评任务，按幂等键
执行并保存 checkpoint。」** 本模块是这句话里那两样东西的形状：一条**意图**，与它被
执行时的那**一代**（epoch）。

今天缺的正是它。``submit_research`` 受理之后是把工作单元**顺手**交出去的 —— 那一次
调用结束、客户端断开，就没有任何东西记着「这批研究跑完之后还有下一批」。于是
「第二批」只能由人再发一次请求来触发，而 R2 的一行定义就是「**关掉聊天仍续下一批**」
（同文件第 22 行）。意图落账之后，下一批的触发者是**盘上的那一行**，不是某个人。

## 为什么它是一条记录，而不是一次扫描推出来的

「欠着什么」本来可以从业务状态推出来（一个 ``QUEUED`` 的作业有没派出去的件）。
做成一条记录是因为推导推不出下面这三样，而它们正是「不重复昂贵工作」的全部依据：

* **幂等键**（``Continuation.business_key``）—— 同一个事件欠下的下一步只有一行，
  于是重启之后重新执行的是**同一行**，不是新的一行；
* **执行 epoch** —— 控制器换代之后，上一代留下的 ``CLAIMED`` 行是**陈旧**的，
  必须能被认出来并收回；没有这一格，一次崩溃会让那一行永远挂在别人名下；
* **checkpoint 与 attempts** —— 恢复从哪里继续、这一件试过几次。

## 幂等键是什么，以及它为什么**不是**一个自增号

``continuation_id`` 是从 ``(scene_id, kind, business_key)`` 算出来的内容寻址 id，
与 ``domain/job.py`` 的三条推导逐字同源。所以「同一个事件第二次落账」不是一条要
靠人去遵守的纪律，而是**写不进第二行**。

``business_key`` 由**产生它那个事件**给出，且必须是那个事件的稳定身份：

* ``admit`` 欠下的派发 → ``job_id``（一次受理一件，受理本身就是那个事件）；
* ``settle`` 欠下的复核 → ``work_item_id``（一次结算一件，工作单元是那件事的稳定
  身份 —— 用 attempt id 会让**同一次结算的重发**算成两个事件，而重发是幂等的）；
* ``record_resolution`` 欠下的重评 → ``resolution_id``（裁决那一侧是**反过来**的：
  同一件案件能离开待办两次 —— 关掉之后重开、再裁决 —— 而那两次重看都要发生，
  所以键取决定本身。理由见 :func:`intents_for_resolution`）。

## 三个「等人」的理由是**闭集**，不是一句话

``BlockedOn`` 的成员各对应一种处置：暂停要人去恢复、缺容量要运维去补、等人要人去做
决定。三者被合成为一句散文时，「这一轮为什么没动」在报告里只剩一句要读的话 ——
而 ``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md`` §B21 逐字要求**分开**这七种状态。

## 三个时间常量**没有**标定过

``BLOCKED_BACKOFF`` / ``RETRY_BACKOFF`` / ``RETRY_CAP`` 是**工程缺省**，不是业务事实
—— 它们没有一个来自获批策略或留出评估。与 ``domain/maintenance.py`` 的
``calibrated`` 同一条纪律：未标定的值可以用（否则系统什么都不做），但**不许**在
报告里被读成「已批准」。它们的取值只需要满足一条：重试的**总次数**有界，于是
「持续运行」绕不开预算（§B21 逐字：不能用「持续运行」绕无限预算）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from pydantic import field_validator, model_validator

from .base import StrictModel
from .review import HumanResolution, ReviewCase, apply_resolution, is_pending
from .subject import SUBJECT_KIND_ACCOUNT, SubjectDossier
from .util import SEP, sha256_text, to_utc

#: 内容寻址 id 的截断长度。与 ``domain/job.py::_ID_CHARS`` 同一个数与同一条理由。
_ID_CHARS = 32


class ContinuationKind(str, Enum):
    """欠着的下一步**是哪一种工作**。闭集，两个成员。

    两个成员各有一个**说得出口的产出者**（``intents_for_admission`` 与
    ``intents_for_settlement``），这一条不是风格：一个没有产出者的成员在报告上
    与「量过了，是零」长得一样 —— 而那是 ``domain/review.py`` 里
    ``no_producer`` 那一格已经定过的纪律。
    """

    #: 一个作业欠着「把它还没交出去的件交给执行器」。产出者：受理。
    DISPATCH_BATCH = "dispatch_batch"
    #: 一个主体欠着「因为刚落下的新证据，重看一眼它到期的格子」。产出者：结算。
    MAINTENANCE_TICK = "maintenance_tick"


class ContinuationState(str, Enum):
    """一条意图的生命周期。

    ``BLOCKED`` 与 ``FAILED`` 是**两件不同的事**，而它们最容易合成一个「没做成」：

    * ``BLOCKED`` —— 门关着（暂停 / 没有执行器 / 等一件待办）。**这不是一次尝试**，
      ``attempts`` 不动，「异常恢复有次数界限」那条判据因此不适用于它。一个暂停了
      一周的场景不会因为每 60 秒 tick 一次而被判成「重试了 10080 次」。
    * ``FAILED`` —— 真的试过、真的挂了。``attempts`` 已经加过，且**有界**；用尽之后
      它是终态，要人看一眼。

    两者都有 ``reason``，而 ``reason`` 在 ``BLOCKED`` 上还带一个 ``blocked_on``
    （闭集）—— 分类读的是那个字段，不是这句话。
    """

    PENDING = "pending"
    CLAIMED = "claimed"
    BLOCKED = "blocked"
    FAILED = "failed"
    DONE = "done"


#: 一条意图**还欠着**的那几个状态。报告里的「未决」读的是它，唯一一处定义。
OPEN_STATES: frozenset[ContinuationState] = frozenset(
    {ContinuationState.PENDING, ContinuationState.CLAIMED, ContinuationState.BLOCKED}
)

#: 走到头的那两个。**没有出边** —— 一条做完了的意图不会被谁重新打开，而
#: ``docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md`` §10 的「恢复从 checkpoint 继续」说的是
#: 未决的那些，不是要做完的再来一遍。
TERMINAL_STATES: frozenset[ContinuationState] = frozenset(
    {ContinuationState.DONE, ContinuationState.FAILED}
)

#: 状态迁移表，与 ``domain/enums.py::JOB_TRANSITIONS`` 同一个形状、同一条理由：
#: 一条非法迁移是一句要读的拒绝，而不是一个悄悄写下的状态。
#:
#: ## 这张表说的是**存在哪些**迁移，不是**谁**可以用它
#:
#: 有两类写者，权力不同，而它们共用一个状态机：
#:
#: * **控制器**（``claim_continuation`` / ``finish_continuation``）—— 举手、执行、
#:   结账。它必须**先认领**才能动任何一行，因为认领就是那一次 CAS（``holder`` +
#:   ``epoch`` 一起写进那一行）。于是 ``CLAIMED`` 的四条出边全是它的。
#: * **事件写者**（``admit`` / ``settle`` / ``set_state`` 在同一个写事务里欠下与豁免）
#:   —— 它不执行任何东西，它只是把「那个业务事件现在欠什么」写成盘上的事实。
#:   ``PENDING → DONE``（受理的作业被取消，那条派发意图**已经没什么可做的**）、
#:   ``PENDING → BLOCKED``（作业被暂停）、``BLOCKED → PENDING``（恢复）、
#:   ``BLOCKED → DONE``（暂停期间被取消）四条是它的。
#:
#: 所以「谁能用哪条边」由**动词**约束（``finish_continuation`` 要求那一行在
#: ``CLAIMED``），不由这张表约束。把两类写者的边合成一张表而让表本身去区分它们，
#: 需要一个「这次是谁在写」的参数 —— 那正是让一次执行能伪装成一次事件的东西。
#:
#: 没有给事件写者开一条不认领的**执行**路径：它写下的每一个状态都是「没有什么要做」
#: （``DONE``）或「现在不能做」（``BLOCKED``），没有任何一条会让它去碰外部。认出
#: 「已经没什么可做」不花钱、不调模型，所以不给它开第二条执行路径不损失任何东西。
CONTINUATION_TRANSITIONS: dict[ContinuationState, frozenset[ContinuationState]] = {
    ContinuationState.PENDING: frozenset(
        {ContinuationState.CLAIMED, ContinuationState.BLOCKED, ContinuationState.DONE}
    ),
    ContinuationState.CLAIMED: frozenset(
        {
            ContinuationState.PENDING,
            ContinuationState.BLOCKED,
            ContinuationState.DONE,
            ContinuationState.FAILED,
        }
    ),
    ContinuationState.BLOCKED: frozenset(
        {ContinuationState.CLAIMED, ContinuationState.PENDING, ContinuationState.DONE}
    ),
    ContinuationState.FAILED: frozenset(),
    ContinuationState.DONE: frozenset(),
}


class BlockedOn(str, Enum):
    """``BLOCKED`` 的**理由类型**，闭集三成员。分类读它，不读那句话。"""

    #: 场景或作业被暂停/归档 —— 一个**已经做出的决定**，恢复由人发起。
    PAUSE = "pause"
    #: 缺执行器 / 缺凭据 / 缺预算 —— 运维要补的那一类。
    CAPACITY = "capacity"
    #: 等一件待办裁决 —— 一个**尚未做出的决定**，人做完它才会解开。
    HUMAN = "human"


class ControllerState(str, Enum):
    """一轮 tick 结束之后，「为什么此刻没有更多工作在跑」的**唯一**答案。

    §B21 逐字要求把这七种**分开**。分开的判据不是「哪一句更好听」，而是它们的处置
    各不相同：``PAUSED`` 等人按一个按钮、``MISSING_CAPACITY`` 要运维补接线、
    ``WAITING_ON_HUMAN`` 要去处理一件待办、``RESEARCHING`` 什么都不用做、
    ``WAITING_DUE`` 什么都**不许**做（还没到期）、``COMPLETE`` 与 ``NO_WORK`` 一个
    是「做完了」一个是「还没有过事」。

    最后两者的区别需要**历史**：``COMPLETE`` 至少有一条 ``DONE``，
    ``NO_WORK`` 一条都没记过。少了这个判据，「这个市场做完了」与「这个市场从来
    没动过」在报告上是同一个空。
    """

    RESEARCHING = "researching"
    PAUSED = "paused"
    WAITING_ON_HUMAN = "waiting_on_human"
    MISSING_CAPACITY = "missing_capacity"
    WAITING_DUE = "waiting_due"
    COMPLETE = "complete"
    NO_WORK = "no_work"


@dataclass(frozen=True)
class ControllerInputs:
    """分类的**全部**输入。六个数 + 一个布尔。

    ``open_total`` 不单列 —— 它是前六个数里非零那几项的和，另存一份会有两个真相
    来源，而它们不一致时没有一处会红。
    """

    #: 这一轮正在做着（``CLAIMED``）的件数。
    in_flight: int = 0
    #: ``PENDING`` 且**已经到期**、这一轮却没能做掉的件数。
    due_now: int = 0
    #: ``PENDING`` 且还没到期的件数。
    waiting_due: int = 0
    #: 被暂停挡住的件数。
    blocked_paused: int = 0
    #: 被缺执行器/凭据/预算挡住的件数。
    blocked_capacity: int = 0
    #: 在等人的件数。
    blocked_human: int = 0
    #: 这个工作区**曾经**做过至少一件（终态的 ``DONE``）。见 ``ControllerState``。
    ever_done: bool = False

    @property
    def open_total(self) -> int:
        return (
            self.in_flight
            + self.due_now
            + self.waiting_due
            + self.blocked_paused
            + self.blocked_capacity
            + self.blocked_human
        )


def classify(inputs: ControllerInputs) -> ControllerState:
    """六个数 + 一个布尔 → 七格之一。**纯函数**，顺序就是全部内容。

    它是**一条级联**，不是一张「恰好一格非零」的对照表：看这些数里**第一个非零的
    那一格**是哪一格，那一格就是答案。优先级与它的理由（每一格都要能答「为什么不是
    下面那一格」）：

    1. ``RESEARCHING`` —— 有件正在做。它答的是「此刻**在**发生什么」，所以压过
       其余六格：一个暂停的场景里若有在途的合法结果，那一轮**确实**在研究。
       暂停这件事不会因此消失 —— 它在回执的 ``blocked_paused`` 里，两个数一起读。
    2. ``PAUSED`` —— 还剩没做完的，而其中**有**被暂停挡住的。暂停是这些理由里最先
       要处理的那一个：场景暂停时其余几件事的处置都做不了（恢复是一个按钮，
       而它按下去之前补执行器、处理待办都没用）。
    3. ``WAITING_ON_HUMAN`` —— 没有暂停挡着的，而有人在等一件待办。它压过
       ``MISSING_CAPACITY``：一件待办被人裁决之后**可能**连带把「缺容量」那件事也
       解掉（比如一条提议批准之后场景才能开），反过来不成立。
    4. ``MISSING_CAPACITY`` —— 卡在接线/凭据/预算上。
    5. ``WAITING_DUE`` —— 有到期的（这一轮没能做掉，只可能来自一次**上限截断**，
       ``limit`` 挡住了尾巴）或者还没到期的。**这一格什么都不做是对的**，所以它必须
       与 ``NO_WORK`` 分开：一个是「等着」，一个是「没有」。
    6. ``COMPLETE`` / ``NO_WORK`` —— 什么都没欠了。两者的差别只有历史。

    ## 为什么是级联，而不是「六个数里恰好一格非零」

    对照表要成立，得假设六个数只有那几种整齐的形状。而**混合是常态**：一次
    ``limit`` 截断之后，尾巴是 ``due_now``、而更后面还有 ``waiting_due`` 的行；
    一个场景暂停的同时旁边还有等裁决的格子。那些形状在对照表上**一格都归不进去**，
    于是判词不是「答错了」而是**抛异常** —— 一轮正常的上限截断会让状态页整个炸掉，
    而这恰恰是最需要看一眼状态页的时候。

    级联对每一个非负组合都有答案，且**每一格的数字都一起报出去**（``ControllerInputs``
    随判词一起返回）—— 所以「暂停挡了 5 件、另有 2 件等人」这件事一个字都没丢，
    只是判词只报最先要处理的那一格。

    ## 代价是原来的那句拒绝没有了，所以穷尽性改由测试守着

    「六个数都非零但一格都归不进去」这种输入消失了，于是那条 ``ConfigError``
    （「分类函数少了一格」）也消失了。它守的失败是真的：将来有人给
    ``ControllerInputs`` 加第七个桶而级联没跟上，那些件会被静默读成
    ``WAITING_DUE``。所以判据搬到 ``tests/test_domain_continuation.py``：**每一个桶
    单独非零都必须得出一个非 ``NO_WORK`` 的判词** —— 加了桶而没接线，那条会红。
    """
    if inputs.in_flight:
        return ControllerState.RESEARCHING
    if inputs.open_total == 0:
        return ControllerState.COMPLETE if inputs.ever_done else ControllerState.NO_WORK
    if inputs.blocked_paused:
        return ControllerState.PAUSED
    if inputs.blocked_human:
        return ControllerState.WAITING_ON_HUMAN
    if inputs.blocked_capacity:
        return ControllerState.MISSING_CAPACITY
    return ControllerState.WAITING_DUE


def continuation_id(scene_id: str, kind: ContinuationKind, business_key: str) -> str:
    """内容寻址：同一个 ``(场景, 种类, 业务键)`` 永远得到同一个 id。

    与 ``domain/job.py::job_id`` 是同一条推导、同一条理由：幂等由**主键**保证，
    而不是靠「写之前先查一下」—— 后者在两个进程之间有一个真实的窗口，而那个窗口
    里发生的事是「同一个事件欠下两行下一步」，于是同一件昂贵的事做两遍。

    ``scene_id`` 参与：同一个业务键在两个场景下是两条意图。业务键今天都带场景
    （``job_id`` 由场景参与推导、``work_item_id`` 由作业派生），所以带上它是**冗余**
    的 —— 而冗余在这里是刻意的：一条只靠业务键的推导在将来某个业务键不再带场景时
    会静默地把两个场景的意图合成一条，而那时报告上只会显示「少了一件」。
    """
    if not scene_id or not business_key:
        raise ValueError("scene_id 与 business_key 都不能为空 —— 它们决定这一行说的是谁。")
    return sha256_text(SEP.join(("continuation", scene_id, kind.value, business_key)))[:_ID_CHARS]


@dataclass(frozen=True)
class ContinuationIntent:
    """**一个事件欠下的下一步**，落账之前的形状。

    它不带 ``state``/``attempts``/``epoch``/``checkpoint`` —— 那四格说的是「执行到
    哪儿了」，而一条刚被欠下的意图还没开始执行。让它们出现在这里，等于让产生意图的
    那一侧（受理、结算）有机会说出一个它并不知道的值。

    ``subject_kind`` / ``subject_ref`` 要么都空、要么都非空，判据在
    :class:`Continuation` 上（一处定义）。``DISPATCH_BATCH`` 两格都空：那件事是
    **整个作业**的，不是某一个主体的 —— 编一个主体出来会让「这一件是关于谁的」有
    一个看起来有答案的空答案。

    §B21 逐字要求的「仅相关对象等人」靠的就是这两格：一条被挡住的意图只带**它自己**
    的主体，所以它旁边那些主体的意图照常跑。把主体合成一个「本批次」的粗引用，会让
    这一条从结构上做不到。
    """

    scene_id: str
    kind: ContinuationKind
    business_key: str
    subject_kind: str = ""
    subject_ref: str = ""
    #: 这条意图**为什么**被欠下 —— 一句话，进回执。不是拒绝理由（那是 ``reason``）。
    why: str = ""


class Continuation(StrictModel):
    """一条续跑意图在盘上的**完整**样子。

    ## 为什么它不是一个可变的当前状态

    它是两样东西拼起来的：一个**只增不改的身份**（前五格，由事件决定）与一段
    **会被覆盖的执行记录**（后面那几格）。与 ``domain/review.py::ReviewCase`` 同一条
    做法：覆盖的是执行记录，而「这一行说的是哪一次事件」永远不会被改写 —— 一个
    原地改写业务键的实现会让「这一行是不是我以为的那一行」有一个说不清的答案。

    ## ``epoch`` 与 ``holder``

    两者一起答「现在（或最后）是谁在做这一件」。``holder`` 是控制器的稳定标识，``epoch`` 是
    它**那一代**的编号。只有 ``holder`` 不够：一次崩溃之后重启的控制器的 ``holder``
    可能与上一次**逐字相同**（同一个部署、同一个 owner id），于是「上一代留下的
    ``CLAIMED``」与「我自己刚领的」长得一模一样，那一行会永远挂在名下。``epoch``
    是换代时递增的那一格，所以陈旧是**可判**的。

    ## ``attempts`` 与 ``reason``

    ``attempts`` 只数**真的试过**的次数（``CLAIMED`` 落回 ``FAILED`` 才算一次），
    被挡住不算 —— 理由写在 ``ContinuationState`` 上。它**有界**，用尽之后
    ``FAILED`` 是终态。

    ``reason`` 是最后一句要读的话：``BLOCKED`` 时它是「门为什么关着」，
    ``FAILED`` 时它是异常原文，``DONE`` 时是这一趟做了什么。
    """

    continuation_id: str
    scene_id: str
    kind: ContinuationKind
    business_key: str
    subject_kind: str = ""
    subject_ref: str = ""
    #: 这一条被欠下时的那句话 —— 留住它是因为「为什么会有这一行」在事后只能从这里读。
    why: str = ""
    state: ContinuationState = ContinuationState.PENDING
    #: 什么时候可以做。**此刻或之前**才算到期。
    due_at: datetime
    epoch: int = 0
    holder: str = ""
    #: 不透明字符串：执行者上一次走到哪儿了。恢复时原样交回给它。
    checkpoint: str = ""
    attempts: int = 0
    reason: str = ""
    #: ``state is BLOCKED`` ⟺ 这一格非空。理由类型是闭集，分类读它。
    blocked_on: BlockedOn | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("continuation_id", "scene_id", "business_key")
    @classmethod
    def _ids_non_empty(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(
                "continuation_id / scene_id / business_key 不能为空或首尾带空白。\n"
                "三格一起决定「这一行说的是哪一次事件」—— 空的一格会让两件不同的事"
                "落到同一个身份上。"
            )
        return value

    @field_validator("subject_kind", "subject_ref")
    @classmethod
    def _maybe_blank(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("主体两格要么空、要么首尾不带空白 —— 不规范化，拒绝。")
        return value

    @field_validator("due_at", "created_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError(
                "时刻必须带时区。naive 时间在两个部署之间会被读成不同的时刻，"
                "而到期判定正是拿它减 now。"
            )
        return to_utc(value)

    @model_validator(mode="after")
    def _halves_agree(self) -> Continuation:
        """三对字段必须在**构造时**就一致，而不是由某个调用点记得去维持。

        * ``subject_kind`` 与 ``subject_ref`` 要么都空、要么都非空 —— 只有一半时，
          这一行说不出「是关于谁的」，而它会被当成有主体来读；
        * ``blocked_on`` 非空 ⟺ ``state is BLOCKED`` —— 分类读的是前者、报告读的是
          后者，两者不一致时「为什么没做」与「有没有被挡住」各说各话；
        * ``epoch`` 与 ``holder`` 一起答「谁在做这一件」，所以 ``CLAIMED`` 必须两格
          都有值 —— 一个没有持有人的在途件谁也收不回来。
        """
        has = bool(self.subject_ref)
        if has != bool(self.subject_kind):
            raise ValueError(
                "subject_kind 与 subject_ref 必须同时给出或同时为空："
                f"kind={self.subject_kind!r} ref={self.subject_ref!r}。"
            )
        blocked = self.state is ContinuationState.BLOCKED
        if blocked != (self.blocked_on is not None):
            raise ValueError(
                f"state={self.state.value} 与 blocked_on={self.blocked_on} 对不上："
                "被挡住时必须说明是**哪一类**理由（暂停 / 缺容量 / 等人），"
                "而没被挡住时不得留下一个理由 —— 分类读的正是这一格。"
            )
        if self.state is ContinuationState.CLAIMED and not (self.holder and self.epoch):
            raise ValueError(
                "CLAIMED 的行必须同时有 holder 与 epoch：一个是「谁」，一个是「他那一代」。"
                "缺了任何一格，控制器换代之后就认不出这一行是不是自己领的。"
            )
        if blocked and not self.reason.strip():
            raise ValueError("被挡住的意图必须写明理由 —— 报告里那一格就是这句话。")
        return self

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def is_due(self, now: datetime) -> bool:
        """到期了没有。``now`` 是参数 —— 本模块没有时钟（见模块 docstring）。"""
        return self.is_open and to_utc(self.due_at) <= to_utc(now)

    def may_move_to(self, target: ContinuationState) -> bool:
        return target in CONTINUATION_TRANSITIONS[self.state]


#: 被挡住之后隔多久再看一眼。**未标定**（见模块 docstring）—— 它只是一个不让
#: tick 空转的下界，不表达任何业务节奏。
BLOCKED_BACKOFF = timedelta(seconds=60)

#: 一次真的失败之后重试的起始间隔，按 ``attempts`` 翻倍。
RETRY_BACKOFF = timedelta(seconds=60)

#: 重试间隔的上界。没有它，翻倍在第六次就变成一小时、第十次变成一天 —— 而一个
#: 「等得越久越久」的重试在这张表上读起来完全正常。
RETRY_BACKOFF_CAP = timedelta(seconds=3600)

#: 一条意图最多真的试几次。**这是「异常恢复有次数界限」那句话的执行器**：到了
#: 上限它是终态 ``FAILED``，要人看一眼，而不是继续烧。
MAX_ATTEMPTS = 3

#: 一个唤醒持有者多久没跳就算**可能**死了。**未标定**（见模块 docstring）。
#:
#: 它的取值只需要满足一条：**明显大于一次 tick 的间隔**（默认 60 秒 → 这里 90 秒）。
#: 取得太近，「卡了一下」会被读成「死了」，于是真的会出现两个控制器同时在跑；
#: 取得太远，一次崩溃之后没人接手，系统停在那里等人 —— 而那正是这一层要消灭的东西。
#: 它只在**另一个持有者想接手**时被读，不是一个会自动收回的定时器（见
#: ``WakeupOwner``）。
OWNER_STALE_AFTER = timedelta(seconds=90)


def retry_delay(attempts: int) -> timedelta:
    """第 ``attempts`` 次失败之后隔多久再试。**纯函数**，两处（存储与控制器）共用。

    ``attempts <= 0`` 取起始间隔：一次「还没试过就重排」的调用不该得到一个零延迟，
    那会让循环在同一秒里转很多圈。
    """
    if attempts <= 0:
        return RETRY_BACKOFF
    return min(RETRY_BACKOFF * (2 ** (attempts - 1)), RETRY_BACKOFF_CAP)


def after_failure(attempts: int) -> ContinuationState:
    """一次**真的**失败之后，这一行落成什么状态。**纯函数**，两个实现共用。

    ``attempts`` 是**加过这一次之后**的数。到 ``MAX_ATTEMPTS`` 就是终态 ``FAILED``
    ——「异常恢复有次数界限」那句话在这一行代码里才有执行器；没到就退回 ``PENDING``
    并按 ``retry_delay`` 排下一次。

    ## 为什么这个判断不住在调用方

    规则只有一条，但执行者有两个（两个存储实现）。写在调用方就是两处各写一遍，
    而漂移的形态是「一个实现重试到第 3 次、另一个到第 30 次」—— 账上只会显示
    「重试次数不一样」，看起来像两件不同的事各做了几次。

    ## 于是执行器报 ``FAILED`` 时，盘上**不一定**是 ``FAILED``

    那是刻意的：执行器报的是「**这一次**失败了」，盘上记的是「**这件事**现在到哪儿了」。
    两者合成一个词会让「试过三次、要人看一眼」与「刚挂了第一次、等 60 秒再试」在报告里
    长得一样 —— 而它们的处置完全不同（一个要人动手，一个什么都不要做）。
    """
    return (
        ContinuationState.FAILED
        if attempts >= MAX_ATTEMPTS
        else ContinuationState.PENDING
    )


def intents_for_admission(job_id: str, scene_id: str) -> tuple[ContinuationIntent, ...]:
    """**受理一个作业欠下什么** —— 一件：把它还没交出去的件交给执行器。

    ## 为什么受理自己就欠着下一步，而这一步今天就在同一次调用里做掉了

    因为「做掉了」与「记着欠」是两件事。``submit_research`` 今天在同一次调用里就
    ``dispatch_job`` 了 —— 那一刻客户端还在。而 §B21 要证的是**客户端断开之后**下一批
    仍然会发生：一批件里总有一部分要等外部执行器跑完再回来（结算），而「跑完之后还有
    什么该做」必须已经在盘上。所以这一行落账的时机是**受理**，不是派发：受理是那个
    业务事件，而它的下一步是「这批工作要交出去、并且要跟着它们走到完」。

    ``due_at`` **不在这里**：一条刚被欠下的意图什么时候可以做，由落账那一刻的
    存储层填（它手里就有那个时刻）。让本函数收一个 ``at``，等于让产生意图的那一侧
    有机会说出一个它其实不知道的值。
    """
    return (
        ContinuationIntent(
            scene_id=scene_id,
            kind=ContinuationKind.DISPATCH_BATCH,
            business_key=job_id,
            why="受理了这个作业，所以它欠着把它还没交出去的件交给执行器。",
        ),
    )


def intents_for_settlement(
    work_item_id: str,
    scene_id: str,
    *,
    subject: SubjectDossier | None = None,
) -> tuple[ContinuationIntent, ...]:
    """**一次结算欠下什么** —— 一件：这个主体到期的格子重看一眼。

    它为什么是「重看」而不是别的：结算刚把**新证据**写进了池子，而
    ``domain/maintenance.py`` 的到期判定读的正是「上次真的看到它是什么时候」。
    新证据落下之后那一格该不该现在就看，由 ``what_is_due`` 说 —— 本函数只说
    「这一件欠着一次复核」。**不许**在这里顺手判一个到期：那会让同一个判定有两个
    生产者，而它们漂移的形态是「账上说该看、调度说不用」或者反过来。

    ``subject`` 为 ``None`` 时两格都空 —— 那不是「没有主体」，是**这个部署说不出**
    它是谁（身份快照机制之前编译的工作单元）。说不出来就不写，理由与
    ``WorkItemSnapshot.subject`` 逐字相同：编一个出来会让「这一件是关于谁的」有
    一个看起来有答案的空答案。

    ``due_at`` 同 ``intents_for_admission``：由落账那一刻的存储层填。
    """
    return (
        ContinuationIntent(
            scene_id=scene_id,
            kind=ContinuationKind.MAINTENANCE_TICK,
            business_key=work_item_id,
            subject_kind=SUBJECT_KIND_ACCOUNT if subject is not None else "",
            subject_ref=subject.account_id if subject is not None else "",
            why="这一件刚结算，落下的新证据让有关的主体欠一次复核。",
        ),
    )


def intents_for_resolution(
    case: ReviewCase, resolution: HumanResolution
) -> tuple[ContinuationIntent, ...]:
    """**一次裁决欠下什么** —— 一件：这一件待办针对的那个对象重看一眼它到期的格子。

    ## 为什么这一条必须有，以及它写着哪一句

    架构 §9 逐字：「提交裁决验证可信 principal、权限、expected_version 与幂等键；
    过时案不写回新事实。**裁决后自动重评受影响对象**，保留历史和恢复进度。」
    在这一条之前，全仓**没有任何生产者**把一次裁决接到「重看那个对象」上：唯一的
    消费者是 ``block_activation``（它是一道闸门，不是一次重评），而
    ``scene_impact`` 算出来的 ``reassess`` 只出现在**预览**里。于是那句话里的
    「自动」在盘上不成立 —— 裁决落账之后什么都不欠，报告上没有任何一处会红。

    ## 「受影响对象」取的是**案件自己带的那一份快照**

    ``ReviewCase.subject_ref`` 是这一件**关于谁**（§12 的字段），而它是案件上唯一
    一个由领域校验器保证是规范 id 的指称（``_ids_are_canonical`` 管着它）。
    ``affected_refs`` 是给人读的快照那一串，成员可以是路线、草稿、成员行 ——
    领域**说不出**它们各自是哪一个种类，而给一个说不出来种类的引用编一个主体，
    正是 ``run_once`` 那边「tick 一个空主体集」的失败形态：它会照常跑完、报
    「0 格到期」，而那与「这一格核过了、没有到期的」逐字相同。

    ## 为什么状态是「还在未决」时**什么都不欠**

    一条 ``PROPOSE_POLICY_CHANGE`` 走出去的案件进的是 ``AWAITING_APPROVAL`` ——
    它还在待办里（``is_pending``），而那条规则变更**还没有生效**。这时候去重评，
    重评的是**旧**规则下的那一格，于是这一趟的唯一产出是一行「看过了、没变」；
    而真正该重评的时刻是它被批准、离开待办的那一刻（``APPROVE_POLICY_CHANGE``）。
    所以判据不是「这一次裁决是什么」，而是**这一件离开待办没有** —— 这一条不需要
    新的业务词汇，``is_pending`` 已经是「还在待办里」的唯一一处定义。

    ``apply_resolution`` 在这里被调用一次是为了**问那个状态**，而它与
    ``record_resolution`` 内部算的是同一个纯函数 —— 同一个产地，所以两处不会漂移。

    ## 幂等键是**这一次决定**，不是这一件案件

    ``business_key`` 取 ``resolution_id``。上面那条「必须是那个事件的稳定身份」在这里
    有两个候选，而它们不等价：

    * ``case_id`` 会**多合一**：同一件案件能离开待办两次 —— 关掉之后
      ``ResolutionOutcome.REOPEN`` 把它打开（回到未决、不欠），再裁决一次又是离开待办。
      那两次是**两次决定**，各自欠一次重看。按 ``case_id`` 做键，第二行撞上第一行的
      ``continuation_id``（而 ``owe`` 是「没有才写」），于是**第二次重看永远不会发生** ——
      表现与「它已经重看过了」逐字相同。这正落在「重启不丢」（§B20）的反面：重开这件
      动作存在的全部意义就是让那件东西再被看一遍。
    * ``resolution_id`` 与 ``intents_for_settlement`` 那条「用 ``work_item_id`` 而不用
      attempt id」是同一条判据的两次应用，只是方向相反：那里要**合掉**同一次结算的重发，
      这里要**分开**两次真的不同的决定。而重发本身在这一层已经被挡在门外 ——
      ``record_resolution`` 对 ``resolution_id`` 撞主键是**报错**，不是静默去重
      （``ports/review.py`` 逐字：「它记的是一件已经发生的事」）。所以同一个
      ``resolution_id`` 走两次，第二次根本写不进去，「同一个事件欠下的下一步只有一行」
      在那之前就已经成立。
    """
    if is_pending(apply_resolution(case, resolution).state):
        return ()
    return (
        ContinuationIntent(
            scene_id=case.scene_id,
            kind=ContinuationKind.MAINTENANCE_TICK,
            business_key=resolution.resolution_id,
            subject_kind=SUBJECT_KIND_ACCOUNT,
            subject_ref=case.subject_ref,
            why=(
                f"待办 {case.case_id} 被裁决（{resolution.outcome.value}）并离开待办，"
                f"所以它针对的 {case.subject_ref} 欠一次按现行规则的重看。"
            ),
        ),
    )


def open_continuation(intent: ContinuationIntent, *, now: datetime) -> Continuation:
    """把一条意图变成盘上那一行。**纯函数**，两个实现共用，所以初始值只有一个产地。

    ## 初始的 ``due_at`` 就是此刻

    不是「此刻 + 一点延迟」：一条刚被欠下的意图**现在就该被看一眼**（看一眼不等于
    能做——做不做由执行者在那一轮里判定，判定不花钱）。给它一个未来的时刻会让
    「刚受理的作业」在报告里显示成「还没到期」，而那是**另一件事**（``WAITING_DUE``
    说的是「周期还没到」，不是「刚发生的事我们决定先等等」）。

    ``PENDING`` / ``holder=""`` / ``epoch=0`` / ``attempts=0`` 都是默认值，这里不写死
    它们 —— 由模型给，于是「一条新意图长什么样」只有一个地方说。

    ``now`` 是参数，本模块没有时钟（见 ``Continuation.is_due``）。
    """
    return Continuation(
        continuation_id=continuation_id(intent.scene_id, intent.kind, intent.business_key),
        scene_id=intent.scene_id,
        kind=intent.kind,
        business_key=intent.business_key,
        subject_kind=intent.subject_kind,
        subject_ref=intent.subject_ref,
        why=intent.why,
        due_at=now,
        created_at=now,
        updated_at=now,
    )


class ContinuationClaim(StrictModel):
    """一次认领的结果：**这一行现在是不是我在持有**。

    与 ``domain/maintenance.py::ClaimOutcome`` 逐字同形、同一条理由：真正知道答案的
    只有存储 —— 它执行了那一次 CAS，或者没有。让调用方比对 ``holder`` 或时刻都不行，
    因为一次换代之后 holder 可以逐字相同（见 ``Continuation.epoch``）。

    ``won=False`` **不是一个错误**，它有三种来源、处置相同（这一行不是我的）：别人
    正在跑（``CLAIMED``）、别人刚跑完（``DONE``/``FAILED``）、或者还不到期。要问是哪
    一种，读 ``continuation`` 的 ``state`` 与 ``due_at`` —— 它们就是那三种情况的完整
    区分，所以这里只有一个布尔，没有原因码。
    """

    continuation: Continuation
    won: bool

    @property
    def state(self) -> ContinuationState:
        return self.continuation.state

    @property
    def epoch(self) -> int:
        return self.continuation.epoch


class WakeupOwner(StrictModel):
    """**谁被授权唤醒这个工作区** —— 以及他是第几代。

    ## 它防的是「两个触发器」

    ``docs/MEMBER_A_AUTOMATION_ARCHITECTURE.md`` §10 逐字：**「唯一唤醒 owner，不能并行
    双触发器」**。这一条不是关于效率的：两个控制器同时在跑，各自都会领到**不同的**
    到期行（CAS 只挡住同一行的重复认领），于是同一批工作被两个驱动各推一遍 ——
    每一个各自看起来都对，账上是两倍的活。

    所以「谁可以动」这件事本身要落账。它不是从进程表推出来的（``ps`` 里两个进程
    看起来完全一样），也不能从「谁先启动」推出来（重启会改变答案）。

    ## ``epoch`` 为什么是这一代，而不是一个进程 id

    控制器换代（重启、换 host、旧的卡住后被顶掉）时 ``epoch`` 加一。它与
    ``Continuation.epoch`` 是同一个数、同一个用途：让**陈旧的**持有者可以被认出来。
    上一代留下的 ``CLAIMED`` 行因此可判 —— 它的 ``epoch`` 小于当前 owner 的。

    进程 id 不行，有两条理由，各自都够：它会重用（同一台机上死掉的那个 pid 会被
    下一个进程拿到），而且它**不是**这个工作区的东西（换一台机器跑同一个工作区时，
    上一代在另一台机上，pid 没有任何意义）。

    ## 心跳是活的证据，不是租约到期

    与 ``JobStore`` 的租约同一条纪律：**没有自动超时收回**。``heartbeat_at`` 是持有者
    定期写下的「我还在」，而 ``OWNER_STALE_AFTER`` 是一条**判据**，由 ``acquire`` 在
    那一刻读一次、并且只在「另一个持有者想接手」时才被读。两者的区别是承重的：
    「按时间自动收回」会让一个只是卡了一下（GC、换页、一次长查询）的控制器被顶掉，
    而它回来之后仍然以为自己持有 —— 那正是 §8B 逐字禁止的 duplicate expensive work。
    """

    workspace: str
    holder: str
    epoch: int
    acquired_at: datetime
    heartbeat_at: datetime

    @field_validator("workspace", "holder")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workspace 与 holder 都必须非空 —— 空持有者无法被追问。")
        return value

    @field_validator("epoch")
    @classmethod
    def _positive_epoch(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                f"epoch 必须是从 1 起的正整数，收到 {value}。"
                "0 与「没有持有者」不可区分，而它必须可以与后者分开。"
            )
        return value

    @field_validator("acquired_at", "heartbeat_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return to_utc(value)

    @model_validator(mode="after")
    def _heartbeat_is_not_before_acquire(self) -> WakeupOwner:
        if self.heartbeat_at < self.acquired_at:
            raise ValueError(
                f"heartbeat_at={self.heartbeat_at} 早于 acquired_at={self.acquired_at}："
                "一个还没拿到就已经在跳的心跳是两次写反了顺序，而它读起来像「活得好好的」。"
            )
        return self

    def is_stale(self, now: datetime, *, after: timedelta = OWNER_STALE_AFTER) -> bool:
        """这个持有者还算活着吗。**判据在调用方手里**，不由存储自动执行。"""
        return to_utc(now) - self.heartbeat_at >= after


__all__ = [
    "BLOCKED_BACKOFF",
    "CONTINUATION_TRANSITIONS",
    "MAX_ATTEMPTS",
    "OPEN_STATES",
    "OWNER_STALE_AFTER",
    "RETRY_BACKOFF",
    "RETRY_BACKOFF_CAP",
    "TERMINAL_STATES",
    "BlockedOn",
    "Continuation",
    "ContinuationClaim",
    "ContinuationIntent",
    "ContinuationKind",
    "ContinuationState",
    "ControllerInputs",
    "ControllerState",
    "WakeupOwner",
    "after_failure",
    "classify",
    "continuation_id",
    "intents_for_admission",
    "intents_for_resolution",
    "intents_for_settlement",
    "open_continuation",
    "retry_delay",
]

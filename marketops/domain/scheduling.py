"""**调度**：一条耐久的声明，与「它真的被点了一次」这件事。

``docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md`` §8 逐字：

    "MCP 定时任务" 应理解为 **MarketOps 服务拥有的 durable Schedule + 一个受管
    trigger driver**，而不是 MCP 协议本身在后台运行计时器。

同一节还有四句逐字的话，这个模块的每一格都是它们的落点：

* ``:320`` —— 「外部 cron、Hosting、ServerOS 以后都只能作为 trigger driver，
  带 ``fire_id/schedule_revision`` 调用同一 application use case；重复 fire 幂等」。
  于是 :class:`ScheduleFire` 的主键是 ``(schedule_id, schedule_revision, fire_id)``
  三格，而 ``schedule_revision`` **在里面**不是装饰：同一份调度改过之后，同一个
  ``fire_id`` 是**另一次** fire —— 那既是「改完声明要能重放」，也是唯一一条
  从占位行里脱身的办法（见 :class:`FireOutcome` 那两段）。
* ``:322`` —— 「调度器醒来先做低成本 due check，再决定是否搜索/浏览器/Jev/Claude。
  空队列不调用 LLM」。到期与否是 :meth:`ScheduleDefinition.due` 那一个纯判定，
  它读的是盘上两格（``enabled`` 与 ``next_due_at``），不碰任何端口。
* ``:322`` —— 「paused/预算耗尽/等人分别记录」。这一格落在
  ``revision_reason`` 上：**每一次写入自己说清为什么写的**，而不是让读的人
  从两格布尔值去猜。
* ``:324`` —— 「改变范围、预算、通知目标等写操作有版本和权限；"每天看一下" 不能
  偷偷变成每天全量重爬」。所以这一族**没有就地改一个字段的入口**：
  :meth:`ScheduleDefinition.advanced` 与 :meth:`ScheduleDefinition.controlled`
  都产出一份**新版**（``schedule_revision + 1``），旧版原样留在盘上。

## 这个模块里没有任何计时器，而这是一条可以被结构性检查的性质

没有 ``threading``、没有 ``asyncio``、没有 ``sched``、没有 ``time.sleep``、
没有 ``while True``。这不是自律：领域层的 import 白名单由
``tests/test_domain_is_pure.py`` 执行，而「哪条调度到期了」是一个**读**，
不是一次醒来 —— :func:`due_schedules` 拿一个时刻当参数，于是同一个库在同一个
时刻上问两次得到同一个答案，而在两个不同的时刻上问得到两个**可解释**的答案。

## 本层不算「下一次到期是什么时候」

``calendar_rule`` 按文档是「RRULE / 受限 cron 等可验证表示」，而把它求值成下一个
时刻是一次真正的时间计算。这一层不做它，理由是**做它的那一半不在这一层**：
真正知道一条规则怎么读的是那个会去执行这件事的 action
（:class:`~marketops.ports.scheduling.ScheduleAction`），而 ``next_due_at`` 的推进
因此是一个**输入**（:class:`ActionReport.next_due_at`），不是一个推断。
推论写在 :meth:`ScheduleDefinition.advanced` 上：``None`` 就是「没有下一次」，
而那意味着这条调度被关掉 —— 一次 fire 之后无声地停在原地是这一族最坏的失败，
因为它在报告上与「一切正常、只是还没到点」逐字同形。

``timezone`` 那一格同理：它按原文声明下来，而 :meth:`ScheduleDefinition.due`
**不用它**。到期判定比的是两个带时区的绝对时刻，而「这条规则在当地时间几点读」
属于求值，不属于比较。让 ``due`` 去读 ``timezone`` 会造出第二处时间算术，
两处的分歧只会表现为「同一条调度在两次醒来之间既到期又不到期」。
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator

from .base import StrictModel
from .util import check_canonical_id, check_reference, sha256_text, to_utc

__all__ = [
    "ActionKind",
    "ActionReport",
    "ActionSpec",
    "FireOutcome",
    "FireReceipt",
    "FireStatus",
    "SETTLED_OUTCOMES",
    "ScheduleControl",
    "ScheduleDefinition",
    "ScheduleFire",
    "TriggerType",
    "due_fire_id",
    "due_schedules",
]


class TriggerType(str, Enum):
    """四类触发（§8「四类触发」逐字）。

    四个成员不多不少。它们回答的是**谁来决定该醒了**，而不是「醒了做什么」——
    后者是 ``action_spec``。把两件事合成一格（例如 ``trigger_type="daily_digest"``）
    会让「每天醒一次」与「醒了发一份摘要」变成同一句话，而它们各自要改的时候
    是两次不同的授权。
    """

    #: 事件立刻评估下一步（研究 settled、证据变化、复核解决）。
    EVENT = "event"
    #: 按每个事实/路线的 ``next_due_at``，只唤醒最早到期的一批。
    DUE_STATE = "due_state"
    #: 每日/每周 digest、固定业务窗口。时区明确。
    CALENDAR = "calendar"
    #: 某日重新检查或一次提醒。
    ONE_SHOT = "one_shot"


class ActionKind(str, Enum):
    """``action_spec`` 说的是**哪一种**动作。闭集。

    闭集在这里是承重的，而不是风格：一条写着本构建不认识的动作的调度，
    它的表现不是报错，而是**永远不点火的备好弦的弓** —— 它在
    ``list_schedule_definitions`` 上与一条健康的调度逐字同形。所以不认识的取值
    在**写入那一刻**被拒（pydantic 的枚举校验），而读取侧因此不需要一个
    「这条我读不懂」的分支。
    """

    #: 受理一轮研究（走 ``application/run_admission.py`` 那条受理面）。
    RESEARCH_RUN = "research_run"
    #: 复核一批到期的事实/路线。
    REFRESH = "refresh"
    #: 汇总一份摘要。
    DIGEST = "digest"
    #: 只发一条通知，不产生任何研究动作。
    NOTIFY = "notify"


class FireOutcome(str, Enum):
    """一条 fire 行上 ``outcome`` 那一格的取值空间：一个已结的结果，或者一个占位。

    ## 为什么 ``PENDING`` 在里面（而它不是动作的一个结果）

    这一族里有一句逐字的要求：「幂等必须来自**主键插不进去**，而不是来自『先查一下
    再决定』」。要让插入成为那件事，就必须在**动作跑之前**先把这一行写下去 ——
    于是这一行在那一刻的 ``outcome`` 只能是一个占位。:attr:`PENDING` 就是它。

    ``PENDING`` **不是** :class:`ActionReport` 能回的取值（那个模型上的校验器拒它）：
    占位是**调用方**写下的，不是动作做出来的。两个来源合成一个枚举，是因为 DDL 上
    只有一列 ``outcome`` —— 而一列 NOT NULL 的文本表示不了「还没有结果」，
    除非它有一个名字。

    ## 占位行会被一次 CAS 结掉，结论不会再变

    :meth:`ScheduleFire.settled` 只在 ``PENDING`` 上生效，因此一条 fire 行的
    ``outcome`` 一生只写两次：占位一次、结论一次。**「重放回同一个结果」** 这句话
    的落点就在这里 —— 重放读到的是结论那一格，而不是再跑一次。

    ## ``NOT_DUE`` 与 ``DISABLED`` 不在这里

    它们是「这次调用没有点火」，不是一种 fire —— 见 :class:`FireStatus`。把两者
    合成一个枚举，会让 ``schedule_fire`` 这张表里躺满每一次醒来的记录，而它的主键
    正是幂等键：一条「今天不到期」的记录会用它自己的 ``fire_id`` 去挡掉今天**真的**
    到期的那一次。
    """

    #: **占位**：这一行已经写下，动作还没跑完（或者跑它的那个进程没了）。
    #: 它只由写入方在动作**之前**写下，只由 :meth:`ScheduleFire.settled` 换掉。
    PENDING = "pending"
    #: 动作跑成了，并且产生了一轮 Run。
    RUN_ADMITTED = "run_admitted"
    #: 动作跑完了，但按它自己的判据什么都不用做（例如摘要没有内容）。
    SKIPPED = "skipped"
    #: 动作规格**指不到东西** —— 目标不在了、或者这个部署没有能执行它的那一半。
    UNRESOLVED = "unresolved"
    #: 动作抛了、或者它自己报了一次失败。**这一次 fire 已经记在盘上**：
    #: 重放它回的是这一格，而不是再跑一次。
    FAILED = "failed"


#: 已结的结果。``PENDING`` **不在**里面 —— 动作那一侧只能回这四个。
SETTLED_OUTCOMES: tuple[FireOutcome, ...] = (
    FireOutcome.RUN_ADMITTED,
    FireOutcome.SKIPPED,
    FireOutcome.UNRESOLVED,
    FireOutcome.FAILED,
)


class FireStatus(str, Enum):
    """一次 ``fire_schedule`` 调用**本身**的结果。"""

    #: 这一次 fire 被受理（动作跑过、或者它是一次重放的 read-back）。
    ADMITTED = "admitted"
    #: 没到期。**一次 provider 调用都没有发生**，也没有写 ``schedule_fire``。
    NOT_DUE = "not_due"
    #: 这条调度是关着的（``enabled=False``）。同上：没有动作、没有 fire 行。
    DISABLED = "disabled"


class ScheduleControl(str, Enum):
    """``control_schedule`` 的三个动词（``catalog.py`` 那条 summary 逐字「开 / 关 / 暂停」）。

    ``DISABLE`` 与 ``PAUSE`` 都落到 ``enabled=False`` 上，而**它们不是同一件事**：
    关掉是「这条规则不再属于这个部署」，暂停是「它还是我的，只是现在不跑」。
    这个区别只活在 ``revision_reason`` 里，因为 DDL 上只有一列 ``enabled`` ——
    而一列布尔值表示不了两种意图，硬要它表示，下一次读的人就只能猜。
    """

    ENABLE = "enable"
    DISABLE = "disable"
    PAUSE = "pause"


class ActionSpec(StrictModel):
    """**动作规格**：到点了要做哪一件事、做在谁身上。

    ## 它指名，不内联

    ``params`` 里可以出现「这一批取多少条」这类参数，但**不许**出现被操作对象的
    字段值。理由与 ``SavedView.query`` 那一条逐字同源：内联一份事实等于给它造了
    第二个住处，而两个住处不一致时读的人无从判断哪个对。

    ## ``target_ref`` 为什么必填

    一条没有目标的动作规格是这一族最安静的一种坏：它在清单上与一条健康的调度
    逐字同形，而在点火那一刻才成为一次 ``UNRESOLVED``。写入时拒它，代价是调用方
    当场改一个字符串；不拒它，代价是某天早上发现「每天都看一眼」已经空转了三个月。
    """

    kind: ActionKind
    target_ref: str
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_ref")
    @classmethod
    def _target_is_named(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(
                "action_spec.target_ref 不能为空：一条指不到东西的动作规格在清单上"
                "与一条健康的调度同形，只有点火那一刻才会露出来"
            )
        return check_reference(value, "action_spec.target_ref")


class ActionReport(StrictModel):
    """动作执行完之后，它自己交回来的那一格。

    ## ``next_due_at`` 是这一格里唯一有后果的一格

    它回答「下一次什么时候再来」。``None`` = **没有下一次**，而那会让这条调度
    在 :meth:`ScheduleDefinition.advanced` 里被关掉 —— 一次 fire 之后停在原地
    是这一族最坏的失败（报告上与「一切正常、只是还没到点」同形），所以「没有
    下一次」必须被**说出来**，而不是靠 `next_due_at` 一直不变来表达。

    ## 动作不在这里说「研究该不该跑」

    ``outcome`` 说的是**这一次动作做成了什么**，不是「业务上该不该发生这件事」。
    把后者塞进来会让一次投递失败有机会变成一次重跑研究的理由，而 §13 逐字
    「一个 sink 投递失败只重试通知，不重跑研究」。
    """

    outcome: FireOutcome
    message: str | None = None
    next_due_at: datetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("outcome")
    @classmethod
    def _settled(cls, value: FireOutcome) -> FireOutcome:
        if value not in SETTLED_OUTCOMES:
            raise ValueError(
                f"ActionReport.outcome 不能是 {value.value!r}：``pending`` 是**调用方**"
                "在动作跑之前写下的占位，不是动作做出来的结果。动作只能回四个已结的"
                "结果之一 —— 允许它回占位，就允许它把「这一次还没跑完」写成一条"
                "看起来像结论的记录。"
            )
        return value

    @field_validator("next_due_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)


class ScheduleDefinition(StrictModel):
    """一条调度声明。字段名逐字取自 §8 的 ``ScheduleDefinition`` 那一段。

    ## 它**只是配置**

    这个类里没有一个方法能发起一次检索、一次抓取或一次模型调用 —— 它连一个端口的
    名字都不出现。执行属于**唯一那个**受管的触发驱动，而驱动唯一有权做的事是
    带着 ``(schedule_id, schedule_revision, fire_id)`` 去调同一个应用用例。
    这一条之所以写在类型上而不是写在文档里，是因为「三套 scheduler 各自跑全量刷新」
    是这一族最容易长出来的东西（``catalog.py`` 里 ``put_schedule_definition`` 的
    summary 逐字），而三套实现长得都像一次正常的接线。

    ## 一版一行

    ``schedule_revision`` 从 1 开始，每一次写入都产出下一版，旧版原样留着。
    「每天看一下」不能偷偷变成每天全量重爬（§8 ``:324``），而这句话的可执行形态
    就是：改范围/预算/通知目标**是**一次新版本写入，它必然会出现在清单上，
    也必然要过一次权限判定。
    """

    schedule_id: str
    schedule_revision: int = Field(ge=1)
    workspace: str
    owner: str
    #: 这条调度在看什么：Watch / Workstream / SavedView digest / maintenance aspect。
    scope_ref: str
    trigger_type: TriggerType
    #: 按原文声明下来。``due()`` **不用它**（见模块 docstring 最后一段）。
    timezone: str
    calendar_rule: str | None = None
    window: str | None = None
    next_due_at: datetime | None = None
    action_spec: ActionSpec
    budget_policy_ref: str | None = None
    notification_policy_ref: str | None = None
    enabled: bool = True
    #: 这一版**为什么**被写下来：``declared`` / ``controlled:<动词>`` /
    #: ``fired:advanced`` / ``fired:no_next_due``。§8 ``:322`` 逐字要求
    #: 「paused/预算耗尽/等人**分别记录**」，而 DDL 上只有一列 ``enabled``。
    revision_reason: str
    created_at: datetime
    updated_at: datetime

    @field_validator("schedule_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return check_canonical_id(value, "schedule_id")

    @field_validator("next_due_at", "created_at", "updated_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)

    # ------------------------------------------------------------------ 判定

    def due(self, at: datetime) -> bool:
        """**低成本的到期检查**：这一刻该不该点火。

        两格，都在盘上，都不需要端口：

        * ``enabled`` —— 关着的调度不到期（§8 ``:322`` 的 paused 那一半）；
        * ``next_due_at is not None and next_due_at <= at`` —— 没有下一次的调度
          不到期（它是一次已经跑完的 one_shot）。

        它是**纯**的，所以「先做 due check 再决定要不要花钱」这条纪律可以在没有
        任何端口的情况下被观测到：一个把 due check 挪到 provider 调用之后的实现，
        在这条函数上不留痕迹，却在调用顺序上留下一次真实的调用 —— 判据因此必须
        数端口被调了几次，而不是读它的返回值。
        """
        if not self.enabled or self.next_due_at is None:
            return False
        return self.next_due_at <= to_utc(at)

    # ------------------------------------------------------------------ 内容

    @property
    def content_digest(self) -> str:
        """这一版**业务内容**的摘要。两个存储实现用它判「这一次要写的是不是已经
        就是盘上那一份」（``ports/scheduling.py::put_definition`` 那一格）。

        它**不含** ``schedule_revision`` / ``revision_reason`` / ``created_at`` /
        ``updated_at``：那四格记的是「这是第几版、为什么写、什么时候落的」，
        而它们不同**正是**新版存在的理由。把任一格放进来，「重发同一份声明」就会
        每一次都产生一个新版本 —— 于是「这条调度上次改是什么时候」读成一个刚刚的
        时刻，而 ``revision_reason`` 那一格的用处正是回答那个问题。

        ``revision_reason`` 被排除是**一次有代价的选择**，写在这里而不是含糊过去：
        一条已经关掉的调度上，「disable」之后再「pause」的**意图**因此不留痕
        （两次都落在 ``enabled=False`` 上，而盘上只有一个布尔列）。代价与收益不对称
        才这么选：留下的代价是一句读不出来也无人可问的措辞漂移，不留下的代价是
        「有人反复点保存」在盘上留下几十版。

        ``action_spec.params`` 与 ``payload`` 整块按 ``sort_keys`` 序列化：
        同一个字典的两种键序是**同一份内容**（``adapters/sqlite/payload.py`` 逐字
        同一条理由），而一份按插入顺序序列化的摘要会把它们算成两版。
        """
        spec = self.action_spec
        parts = [
            f"workspace={self.workspace}",
            f"owner={self.owner}",
            f"scope_ref={self.scope_ref}",
            f"trigger_type={self.trigger_type.value}",
            f"timezone={self.timezone}",
            f"calendar_rule={self.calendar_rule}",
            f"window={self.window}",
            f"next_due_at={None if self.next_due_at is None else to_utc(self.next_due_at).isoformat()}",
            f"action_kind={spec.kind.value}",
            f"action_target_ref={spec.target_ref}",
            f"action_params={json.dumps(spec.params, ensure_ascii=False, sort_keys=True)}",
            f"budget_policy_ref={self.budget_policy_ref}",
            f"notification_policy_ref={self.notification_policy_ref}",
            f"enabled={self.enabled}",
        ]
        return sha256_text("\n".join(parts))[:16]

    # ------------------------------------------------------------------ 下一版

    def _next_revision(self, **updates: Any) -> ScheduleDefinition:
        """产出下一版。**旧版一个字节都不动**，这是「一版一行」的全部内容。"""
        return ScheduleDefinition.model_validate(
            {**self.model_dump(), **updates, "schedule_revision": self.schedule_revision + 1}
        )

    def controlled(
        self, control: ScheduleControl, *, at: datetime, reason: str | None = None
    ) -> ScheduleDefinition:
        """开/关/暂停之后的下一版。

        ``next_due_at`` **不在这里被清掉**：暂停一条调度不该顺手忘掉它本来什么时候
        该醒 —— 恢复之后它该回到原来那个时刻，而一个清掉它的实现在恢复时只能
        重新声明一次，那是一次看不见的重置。

        ## ``revision_reason`` 里没有「谁按的」

        这一格记的是**为什么**（``controlled:disable``，加一句调用方给的理由），
        不是**谁**。理由是可测的：这一层的调用方（``application/scheduling.py``）
        拿不到调用方身份 —— MCP 的服务面把 ``principal`` 用于授权判定，不把它交给
        处理器（``adapters/mcp_server/guard.py``）。在这里收一个 ``actor`` 参数、
        再由调用点填一个占位字符串，会让审计读起来像真的而其实是编的 ——
        要记「谁按的」，先要有人把身份交下来。
        """
        return self._next_revision(
            enabled=control is ScheduleControl.ENABLE,
            revision_reason=f"controlled:{control.value}" + (f":{reason}" if reason else ""),
            updated_at=to_utc(at),
        )

    def advanced(self, report: ActionReport, *, at: datetime) -> ScheduleDefinition:
        """一次 fire 之后的下一版：把 ``next_due_at`` 推过去，别的格子一个不动。

        ``report.next_due_at is None`` ⇒ **这条调度被关掉**，且理由写上
        ``fired:no_next_due``。这是这一族里唯一一处「动作没有下一次」必须有后果的
        地方：不关掉它，它会永远停在 ``next_due_at`` 上，于是每一次醒来都拿同一个
        ``fire_id`` 去撞同一行主键、回同一个「已经点过了」—— 报告上一切正常。

        ## ``next_due_at`` 必须**严格晚于**刚才那一次到期时刻

        这不是一句自律，而是「一次 fire 之后不再立刻 fire」的全部内容：动作回一个
        不晚于刚才那一刻的时刻（比如「马上再来」），下一版仍然是到期的，于是下一次
        醒来它会**再点一次火** —— 而 ``schedule_revision`` 已经加一，所以新的
        ``(schedule_id, revision, fire_id)`` 是一把**没被用过的**幂等键，重复点火
        在那里挡不住（那个主键挡的是同一个 ``fire_id`` 在**同一版**上的第二次）。
        本方法不替调用方纠正它（把 ``now`` 悄悄换成 ``now + 一刻钟`` 是这一层在编
        一个策略），而是让它在盘上**看得见**：``schedule_revision`` 每一轮加一、
        ``next_due_at`` 一直等于现在 —— 而 ``list_schedule_definitions`` 那一格
        读成 ``due_now``。看得见的循环是可以被人叫停的，静默的停摆不能。
        """
        return self._next_revision(
            next_due_at=report.next_due_at,
            enabled=self.enabled and report.next_due_at is not None,
            revision_reason="fired:advanced" if report.next_due_at else "fired:no_next_due",
            updated_at=to_utc(at),
        )


class ScheduleFire(StrictModel):
    """一次**被受理的** fire。主键 ``(schedule_id, schedule_revision, fire_id)``。

    这张表就是幂等键本身，所以这个模型里没有任何一格能表示「这是第几次尝试」——
    重放同一次 fire **不产生第二行**，而是读回这一行。

    ``outcome`` 是动作干成了什么（:class:`FireOutcome`）。它在这个模型里，而不是
    在服务端的内存里，是因为重放要回**同一个结果**：一个只回「已经点过了」的实现
    让调用方无法判断上一次是成了还是败了，而它接下来要做的事完全不同。

    ## 这一行在动作**之前**写下

    它是「重复 fire 幂等」的载体，而载体只有在**插不进主键**的那一刻才起作用。
    先跑动作、再写行的实现在两个调用方并发时各自都查不到行、各自都跑一遍动作 ——
    两轮研究，一次也没被挡住。所以次序是：写占位行（``outcome=PENDING``）→
    插得进去的那个调用方去跑动作 → 用 :meth:`settled` 把结论写上。

    插不进去的那一个**不跑动作**，它读回来的就是这一行 —— 于是「重放不建第二轮
    Run」不是一句纪律，是那一次插入失败的后果。

    ## 一行一生只写两次

    占位一次、结论一次（:meth:`settled` 只在 ``PENDING`` 上生效）。没有第三次：
    一条已经结了的行不会被任何一次重放改写，所以「重放回同一个结果」读到的是
    结论，而不是「最后一次尝试的样子」。
    """

    schedule_id: str
    schedule_revision: int = Field(ge=1)
    fire_id: str
    workspace: str
    fired_at: datetime
    outcome: FireOutcome
    message: str | None = None
    next_due_at: datetime | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("fire_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        # **不是** ``check_slug_safe``：``fire_id`` 的形状归调用方定（§8 ``:320``
        # 只说「调用方给的 fire_id」），而一个真实存在过的写法是
        # ``"2026-09-29T06:00"`` 这种带冒号的时刻串。用文件名的判据去拦它，拦下来
        # 的理由与它做的事无关，而写的人会去改一个不该改的东西。
        return check_reference(value, "fire_id")

    @field_validator("fired_at", "next_due_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)

    # ------------------------------------------------------------------ 两态

    @property
    def pending(self) -> bool:
        """这一行还只是个占位。**它不是「失败」，也不是「成功」。**"""
        return self.outcome is FireOutcome.PENDING

    def settled(self, report: ActionReport, *, at: datetime) -> ScheduleFire:
        """把动作交回来的那一份写成结论。**只在占位行上有效。**

        在一条已经结了的行上调用它是**一次错误**（抛 ``ValueError``）而不是一次
        静默覆盖：能让它覆盖的调用点，就多了一条「把上一次的失败抹掉」的路，
        而重试历史要读的恰恰是那个事实（与 ``delivery_receipt`` 主键带 ``attempt``
        逐字同一条理由）。

        ``report.message`` / ``report.next_due_at`` / ``report.detail`` 原样落到这一行：
        下一版声明的 ``next_due_at`` 由**同一个** :class:`ActionReport` 推出来，
        两处各读一次会分家 —— 分家的形态是「清单说到期在明天、盘上说的是今天」，
        而没有任何一处会红。
        """
        if not self.pending:
            raise ValueError(
                f"fire {self.schedule_id!r}@{self.fire_id!r}（第 {self.schedule_revision} 版）"
                f"已经是 {self.outcome.value!r}，不能再结一次：一条 fire 行的结论只写一次，"
                "覆盖它会把上一次的结果抹掉。"
            )
        return ScheduleFire.model_validate(
            {
                **self.model_dump(),
                "outcome": report.outcome,
                "message": report.message,
                "next_due_at": report.next_due_at,
                "detail": report.detail,
            }
        )


class FireReceipt(StrictModel):
    """``fire_schedule`` 交回来的那一格。

    ``replayed`` 与 ``status`` 是**两件不同的事**，而它们必须分开：

    * ``status=ADMITTED`` + ``replayed=False`` —— 这一次真的点了火；
    * ``status=ADMITTED`` + ``replayed=True`` —— 同一个 ``fire_id`` 的第二次调用，
      回的是 :attr:`fire` 那一行原样存下来的结果；
    * ``status=NOT_DUE`` / ``DISABLED`` —— 没点火，也没写任何行。

    合成一格之后，「重复 fire 幂等」与「这次点火成功了」在回执上会互相冒充，
    而它们的处置一个是「什么都不用做」、一个是「去看那一轮 Run」。

    第三种 ``ADMITTED`` **也是** ``replayed=True``：那个 ``fire_id`` 有一行占位，
    而它的动作还没跑完（跑它的进程没了，或者正跑着）。这一格**不是失败**，也不是
    成功 —— ``fire.outcome`` 说的是 ``pending``，读的人据此知道「这一次点火被人接下了，
    结果不知道」。把它报成失败会让人去重放，而重放从来不重跑动作（那正是占位行的
    用处）；把它报成成功会让人以为研究跑过了。
    """

    schedule_id: str
    schedule_revision: int = Field(ge=1)
    fire_id: str
    workspace: str
    status: FireStatus
    replayed: bool
    at: datetime
    fire: ScheduleFire | None = None
    #: 这一次调用之后，声明推进到了第几版（没点火或没推进时为 ``None``）。
    advanced_to: int | None = None

    @field_validator("at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return to_utc(value)


#: 从到期时刻推出来的 ``fire_id`` 的形状。**一处定义**，见 :func:`due_fire_id`。
_FIRE_ID_STAMP = "%Y%m%dT%H%M%S%fZ"


def due_fire_id(definition: ScheduleDefinition) -> str | None:
    """一次**到期时刻**对应的 ``fire_id``：``<schedule_id>@<到期时刻>``。

    ## 为什么由到期时刻推，而不是由「醒来的那一刻」推

    §8 ``:320`` 逐字：「外部 cron、Hosting、ServerOS 以后都只能作为 trigger driver…
    重复 fire 幂等」。外面那几个驱动**各自看各自的表**，它们不可能商量出一个共同的
    ``fire_id``；它们能商量出来的只有一件东西：**盘上那条调度说它该在什么时候醒**。
    所以幂等键的第三段必须是那个时刻，而不是调用方的墙上时钟 —— 后者在两次醒来
    之间永远不同，于是「重复 fire」这个词在这里会变成一句空话。

    ## 返回 ``None`` 而不是抛

    ``next_due_at is None``（一次已经跑完的 one_shot）不是一次错误，是**没有到期
    时刻**，而「没有到期时刻的调度不该被点火」这条判定住在调用方
    （:meth:`ScheduleDefinition.due` 已经先答过它）。在这里抛会让「还没到点」与
    「这条调度本来就没有下一次」在调用点长成同一个异常。

    时刻先规范到 UTC 再格式化：两个不同偏移量的写法是同一个瞬间，而它们按字符串
    排会落到两个不同的 ``fire_id`` 上 —— 那会造出两次看起来都合法的点火。
    """
    if definition.next_due_at is None:
        return None
    stamp = to_utc(definition.next_due_at).strftime(_FIRE_ID_STAMP)
    return f"{definition.schedule_id}@{stamp}"


def due_schedules(
    definitions: tuple[ScheduleDefinition, ...], *, at: datetime
) -> tuple[ScheduleDefinition, ...]:
    """这一批声明里，这一刻真的该点火的那些。

    按 ``(next_due_at, schedule_id)`` 升序 —— 「只唤醒最早到期的一批」那句
    （§8 ``:318``）需要一个**稳定的次序**才能被实现，而一个无序的集合让「这一批」
    这件事不可复现：同一个库、同一个时刻，两次调用得到两个不同的批次。
    """
    due = [d for d in definitions if d.due(at)]
    due.sort(key=lambda d: (d.next_due_at, d.schedule_id))
    return tuple(due)

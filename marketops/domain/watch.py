"""**Watch** —— 一条监控规则，以及它**产生**的那个到期请求。

§5.3 逐字：

```text
Watch = SavedView/对象集合 + 要监控的aspect + 触发/刷新政策 + 预算 + 通知策略
```

## 这个模块里**没有**什么，那是它最重要的性质

**没有任何定时器。** 这个文件里既没有 ``sleep``、也没有线程、也没有 ``asyncio``、
也没有下一次唤醒的时刻表 —— 它连一个可以「等着」的地方都没有。全仓唯一的 trigger
driver 是 ``schedule_definition`` 那一侧（Agent D 的 ``fire_schedule``，见
``db.py`` 的 ``schedule_fire`` 表注释：「外部 cron、Hosting、ServerOS 以后都只能作为
trigger driver」）。这一片**只是被问到时回答**：

* :meth:`Watch.due_request` 是一个**纯函数** —— 给一个时刻，回答「这一刻有没有要跑的
  事、要跑的是什么」。它不唤醒谁、不排队、不写任何东西。
* :func:`watch_due_key` 推导出这次到期请求的**幂等键**，而 Run 由**既有**的
  ``application/run_admission.admit_run`` 创建（``execution_mode=WATCH``）。于是
  「Watch 自己造第二套调度」在结构上不可能：这一片里没有一个能创建 Run 的对象。

    一个每五分钟醒一次、而 ``next_due_at`` 一直没被推走的驱动，会反复拿到**同一份**
    到期请求；同一份请求配同一个幂等键，在 ``admit_run`` 那一侧落进**同一行** Run。
    重复驱动因而落在「重发」而不是「再跑一次」上 —— 这是「幂等」在这一片的具体形状，
    而它不是靠这一层记得自己刚做过什么。

## 预算：一处**构造**，不是一句提醒

``Watch`` 上有一个 **必填且非空** 的 :attr:`~Watch.ceilings`。一个没有科目上限的
Watch 构造不出来 —— 于是「启用监控要预算」不只是 ``put_watch`` 那一条判据，它是这个
类型本身的性质。理由与 :class:`~marketops.domain.budget.BudgetLedger` 的
``BudgetNotConfiguredError`` 同源：**未配置不等于无限**，而在一个「先开着、有空再配」
的世界里，没有上限的那条线永远是跑得最欢的那条。

对照 :class:`~marketops.domain.saved_view.SavedView` —— 那个类型上**没有**任何预算格。
两处对照着读，「保存不是花钱的那一步、开监控才是」这句话就是两个类型的差集，而不是
一句需要靠人记住的约定。

## 通知策略与「每周 N 次」：一句必须说清的**做不到**

§5.3 的例子逐字写着「每周最多 N 次搜索 / N 次浏览器获取」。这一片把它落成了
:class:`WatchCeiling`（科目 + 上限），并且 ``put_watch`` 会为**这一版**的 Watch 调
``BudgetLedger.set_ceiling``。但**「每周」这一半今天没有执行者**：
``ports/budget.py`` 上没有充值、没有滚动窗口、没有到期——它的模块 docstring 逐字
说「没有哪条分支能顺手把额度还回去」。所以这一片能做到的是「这条 Watch 这一版最多
花 N 次」，做不到的是「每周重新给 N 次」。

写在**这里**而不是只写在报告里，是因为读代码的人最该看见的正是这个缺口：一个把字段
命名成 ``per_week`` 的实现会让每一处读到它的人都以为窗口在被执行，而它一次都没被
执行过。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

from pydantic import Field, field_validator

from marketops.domain.base import StrictModel
from marketops.domain.enums import BudgetLine
from marketops.domain.maintenance import Aspect
from marketops.domain.util import (
    SEP,
    check_canonical_id,
    check_slug_safe,
    sha256_text,
    to_utc,
    utc_now,
)

__all__ = [
    "NotifyOn",
    "Watch",
    "WatchCeiling",
    "WatchDueRequest",
    "WatchTargetState",
    "watch_budget_scope",
    "watch_due_key",
]


class NotifyOn(str, Enum):
    """什么时候该惊动人。§5.3 的最后半句：「变化/连续失败/新增A-B路线时通知」。

    三个成员逐字对着那三件事，不多不少：

    * ``CHANGED`` —— 重查之后那一格**变了**。
    * ``REPEATED_FAILURE`` —— 连续失败。它是**另一档**而不是 ``CHANGED`` 的一种：
      一次失败不是一次变化，而一个只报变化的世界会在「某个来源已经连着三周抓不动」
      这件事上完全安静。
    * ``NEW_ROUTE`` —— 新增 A-B 路线。它同样不是变化的一种：路线是**新出现的**边，
      而不是某条已有边的取值改了。
    """

    CHANGED = "changed"
    REPEATED_FAILURE = "repeated_failure"
    NEW_ROUTE = "new_route"


class WatchTargetState(str, Enum):
    """这份 Watch 的目标**此刻是什么状态**。

    ``VIEW_MISSING`` 是它存在的理由：目标视图被删掉时，Watch 要**如实报成那个样子**，
    而不是静默地变成「不活跃」。两者在报告上完全同形 —— 一行
    ``enabled: true`` 配一个空目标，读起来像「这条 Watch 安静地待着」，而它其实
    指着一条**不存在**的视图，谁都不会再收到它的通知。

    它由调用方**从库里读出来**再传进来（``view_exists``），不由这个模块猜：
    一个「读不到就是不在」的推断会让「库没接上」与「视图被删了」同形，而两者的
    处置相反。
    """

    #: 目标是一条**在的**视图。
    VIEW = "view"
    #: 目标视图**不在**（被删了，或者从来就没有过）。
    VIEW_MISSING = "view_missing"
    #: 直接点名的主体，不依赖任何视图。
    SUBJECTS = "subjects"


class WatchCeiling(StrictModel):
    """一个科目上的上限：**多少**、**哪个科目**。

    ``units`` 的下界是 1（不是 0）。0 是一条**写得出来但立刻拒绝一切**的上限，
    而它与「忘配了」在账本上的区别只有一个数字 —— ``set_ceiling`` 收下 0 之后，
    这条 Watch 的每一次工作都会在预留下被拒，报告上显示的是「预算耗尽」（一个
    看起来正常的业务状态），真实原因是「上限写成了 0」。要停一条 Watch，正确动作
    是 ``enabled=false``，那件事有它自己的字段。
    """

    line: BudgetLine
    units: int = Field(ge=1)


def watch_due_key(
    *, workspace: str, watch_id: str, watch_revision: int, due_at: datetime
) -> str:
    """这一次到期请求的**幂等键**。同一个 Watch 的**同一版**、**同一个**到期时刻
    ⇒ 同一个键 ⇒ ``admit_run`` 那一侧落进同一行 Run。

    分段用 :data:`~marketops.domain.util.SEP`，与 ``domain/budget.py`` 的
    ``reservation_id`` / ``domain/job.py`` 的 ``job_id`` 是同一条推导规则：分段里
    可能含冒号或斜杠（``workspace`` 就可能），于是 ``("a:b", "c")`` 与
    ``("a", "b:c")`` 会拼成同一个字符串 —— 两件不同的事获得同一个幂等键，而幂等键
    决定的是「这一次会不会重新建一个 Run」。

    ``due_at`` 取的是**时刻本身**（ISO-8601，带偏移量），不是它的小时数或日期：
    一条 ``check_every_days=1`` 的 Watch 在连续两天各到期一次，那是**两次**工作，
    而一个只按日期取整的实现会把它们合成一次 —— 第二天那次会被判成重发，于是
    永远不跑。
    """
    return SEP.join(
        (
            "watch",
            workspace,
            watch_id,
            str(watch_revision),
            to_utc(due_at).isoformat(),
        )
    )


def watch_budget_scope(*, workspace: str, watch_id: str) -> str:
    """一条 Watch 的**预算封套**记在账本上的那个 scope 名。**唯一的推导入口。**

    §5.3 逐字的那一行公式里「预算」是五段之一，而 ``WatchCeiling`` 只说了「哪个科目、
    多少」—— 这两格记在**哪个账上**是另一件事，而它必须只有一个答案：两处各拼一遍的
    那天，``put_watch`` 写入的封套与读取它的人看到的会是两个账，而两边各自都正常
    （``watch_due_key`` 那段论证过的同一种失效）。

    形状与 ``application/budget.py::RunBudget._seeded`` 的 ``f"{profile_id}:{label}"``
    同款（冒号分段）：``workspace`` 与 ``watch_id`` 都过 ``check_canonical_id``，
    所以两段里都不可能出现冒号，拼接无歧义。**按 ``watch_id`` 而不是按 ``revision``**：
    一条 Watch 改规则产生新版，而「这一条 Watch 总共花了多少」不跟着版本重开一个账 ——
    每版一个账会让「每周最多 N 次」在每一次改名字之后从头开始。

    ## 如实记一笔：今天没有支出方预留到这个 scope 上

    这个函数存在，是因为 ``put_watch``「要预算」那一半需要一个**真的写下去、能读回来**
    的账；而账本那一侧的**支出**今天仍然按 ``(profile, 账号)`` / ``(profile, 发现范围)``
    分账（``application/discovery_run.py`` 的 ``RunBudget.for_scope``）。也就是说：
    封套是写下来了的、读得回来的（``BudgetLedger.usage(scope)``），而「谁的预留要落进
    这个账」这一条链接**还没有接上**。它记在报告里，不在这里假装成立 ——
    一个把 ``scope`` 取成账号名的实现在这条 Watch 盯着一批账号时会写出 N 条互相覆盖的
    上限（``Watch._ceilings`` 那段「同一科目不能出现两次」要防的正是这件事）。
    """
    return f"{workspace}:watch:{watch_id}"


class WatchDueRequest(StrictModel):
    """**这一刻有活要干** —— 一条 Watch 的到期请求。

    它是一份**请求**，不是一次受理：受理是 ``admit_run`` 的事，而它要的东西这里
    各是各的一格（``subjects`` / ``aspects`` / ``ceilings`` / ``idempotency_key``），
    调用方照着自己的端口填进去即可。

    ``blocked_reason`` 非空时这份请求**执行不了**（今天只有一种：目标视图不在）。
    它仍然被返回，而不是被丢掉 —— 一个被丢掉的到期请求在报告上等于「这条 Watch
    没什么要做的」，而真相是「它指着的东西没有了，没人知道」。执行不了的请求
    ``subjects`` 是空的，于是它**也**过不了 ``admit_run`` 那条「这一批主体上必须
    有一个到期的格子」的判据：一份执行不了的请求建不出 Run，这是构造，不是自觉。
    """

    watch_id: str
    watch_revision: int = Field(ge=1)
    workspace: str
    view_id: str | None = None
    subjects: tuple[tuple[str, str], ...] = ()
    aspects: tuple[Aspect, ...] = ()
    ceilings: tuple[WatchCeiling, ...] = ()
    notify_on: tuple[NotifyOn, ...] = ()
    #: 到期的那个时刻（即这份 Watch 上存的 ``next_due_at``）。
    due_at: datetime
    #: 非空 = 这份请求执行不了，以及为什么。
    blocked_reason: str = ""
    target_state: WatchTargetState

    @property
    def idempotency_key(self) -> str:
        """这一次的幂等键。**推导出来的**，不是一格字段。

        做成属性而不是字段：一个可以从其它格算出来的字段会被两个写者各填一次，
        而它们分家的表现是「同一个到期请求拿了两个键 ⇒ 建出两个 Run」——
        那正是幂等键要挡的那件事。
        """
        return watch_due_key(
            workspace=self.workspace,
            watch_id=self.watch_id,
            watch_revision=self.watch_revision,
            due_at=self.due_at,
        )

    @property
    def servable(self) -> bool:
        return not self.blocked_reason


class Watch(StrictModel):
    """一条监控规则。

    字段逐字对着 §5.3 的那一行公式，每一条都注明了它在公式里的哪一半：

    ``view_id`` / ``subjects`` —— 「SavedView/对象集合」；
    ``aspects`` —— 「要监控的 aspect」；
    ``check_every_days`` —— 「触发/刷新政策」里**属于 Watch 的那一部分**；
    ``ceilings`` —— 「预算」；
    ``notify_on`` —— 「通知策略」。

    ## 刷新政策为什么只带 ``check_every_days``

    「多久算过期」这件事已经有一个权威：``domain/maintenance.py`` 的
    :class:`~marketops.domain.maintenance.RefreshPlan`（每个 aspect 一条
    :class:`~marketops.domain.maintenance.RefreshRule`，带 ``after_days`` 与
    ``calibrated``），而它住在**场景的生效版本**上，由 ``due_work`` 取用。

    这一格里再带一套 ``RefreshRule`` 就会造出第二套节奏词表，而它们分家的表现是
    「这条 Watch 说 30 天、那条规则说 90 天，两边的报告各自都正常」。所以这一片
    只带 :attr:`check_every_days` —— 它是**多久看一次**（§5.3 逐字「每天只做 due
    check」），不是**多久算过期**。前者是 Watch 独有的，后者不是。

    这一条有代价，如实记在这里：**今天没有办法为单条 Watch 覆盖某个 aspect 的
    ``after_days``。** 要覆盖就得在 ``watch`` 表上加列（本片的冻结 DDL 没有），
    或者容忍第二套节奏词表。两者都不该由这一片单方面决定 —— 见报告里那一节。
    """

    watch_id: str
    revision: int = Field(ge=1)
    workspace: str
    owner: str
    name: str
    #: 目标视图。``None`` = 这条 Watch 直接盯着 :attr:`subjects` 里点名的主体。
    view_id: str | None = None
    #: 直接点名的主体（``(subject_kind, subject_id)``）。形状与
    #: ``admit_run(subjects=...)`` / ``what_is_due(subjects=...)`` **逐字相同** ——
    #: 一份到期请求因此可以直接喂进既有 admission，中间不需要一次翻译（一次翻译
    #: 就是一个「这两边说的不是同一件事」的落点）。
    subjects: tuple[tuple[str, str], ...] = ()
    #: 要监控的 aspect。空 = 场景生效版本里的**全部**（``ALL_ASPECTS``），
    #: 与 ``due_work(aspects=None)`` 的语义一致。
    aspects: tuple[Aspect, ...] = ()
    #: 多久看一次（天）。下界 1：0 会让「每天看一眼」变成一条**每一次醒来都到期**的
    #: 规则，而它驱动是一次真的外部工作 —— 这一格写 0，等于把 Watch 变成一台
    #: 转速取决于驱动频率的机器，而报告上它只显示「检查得很勤」。
    check_every_days: int = Field(default=1, ge=1)
    #: **必填且非空**。见模块 docstring 那一节。
    ceilings: tuple[WatchCeiling, ...]
    #: **必填且非空**。一条谁都不通知的 Watch 仍然会花钱、仍然会产生 Run，
    #: 而它的结果没有任何出口 —— 那是一个「看起来在监控」的空转。
    notify_on: tuple[NotifyOn, ...]
    enabled: bool = True
    next_due_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    note: str = ""

    @field_validator("watch_id")
    @classmethod
    def _watch_id(cls, value: str) -> str:
        return check_canonical_id(value, "watch_id")

    @field_validator("workspace")
    @classmethod
    def _workspace(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("view_id")
    @classmethod
    def _view_id(cls, value: str | None) -> str | None:
        return None if value is None else check_canonical_id(value, "view_id")

    @field_validator("owner", "name")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("owner / name 不能为空或首尾带空白")
        return value

    @field_validator("subjects")
    @classmethod
    def _subjects(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        for kind, subject_id in value:
            check_slug_safe(kind, "subjects[].kind")
            check_slug_safe(subject_id, "subjects[].id")
        if not value:
            return value
        return value

    @field_validator("ceilings")
    @classmethod
    def _ceilings(cls, value: tuple[WatchCeiling, ...]) -> tuple[WatchCeiling, ...]:
        if not value:
            raise ValueError(
                "一条 Watch 至少要有一个科目上限（§5.3「+ 预算」）。"
                "没有上限时它的每一次外部工作都无人过问 —— 而「未配置」与"
                "「无限」在账上是同一个样子。"
            )
        lines = [ceiling.line for ceiling in value]
        if len(set(lines)) != len(lines):
            raise ValueError(
                f"同一科目不能出现两次（收到 {[line.value for line in lines]}）："
                "两条上限会在 set_ceiling 上互相覆盖，而先写的那一条读回来是另一个数。"
            )
        return value

    @field_validator("notify_on")
    @classmethod
    def _notify(cls, value: tuple[NotifyOn, ...]) -> tuple[NotifyOn, ...]:
        if not value:
            raise ValueError(
                "一条 Watch 至少要有一个通知时机（§5.3「+ 通知策略」）。"
                "一条谁都不通知的 Watch 会照常花钱、照常产生 Run，而它的结果没有"
                "任何出口 —— 那是一个「看起来在监控」的空转。"
            )
        return value

    @field_validator("created_at", "updated_at", "next_due_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)

    def model_post_init(self, _context: object) -> None:  # noqa: D105 (见下)
        """目标必须有一个。

        写在 ``model_post_init`` 而不是校验器里，是因为它判的是**两格之间**的关系，
        而两个字段各自的校验器都看不到对方。``view_id=None`` 且 ``subjects=()``
        的一条 Watch 到点会产出一份**空的**到期请求 —— 而「这一刻没有到期的东西」
        与「这条 Watch 没有目标」在回执上同形。
        """
        if self.view_id is None and not self.subjects:
            raise ValueError(
                "一条 Watch 要么指一条视图（view_id），要么点名一批主体（subjects）"
                "—— 两者都没有时它会到点产出空的到期请求，而那与「这一刻没有到期的"
                "东西」在报告上同形。"
            )

    # ------------------------------------------------------------------
    # 读法
    # ------------------------------------------------------------------
    @property
    def content_digest(self) -> str:
        """这一版**业务内容**的摘要。两个存储实现用它判「这一次要写的是不是已经就是
        盘上那一份」（``SavedView.content_digest`` 逐字同一条纪律）。

        **含 ``next_due_at``**，而这一条与视野那一份不同款：把下一次检查推走是这一族
        唯一的日常写入（``application/views.py::record_watch_check``），而那一格里
        唯一变的就是这个时刻。不含它，一次「看完之后推下一次」会被判成重发，
        于是 ``next_due_at`` 永远推不走 —— 而它推不走的表现是**这台 Watch 每被问到
        一次就到期一次**，驱动越勤跑得越勤。

        不含 ``revision`` / ``created_at`` / ``updated_at``：理由同上（它们是新版的
        标识与时刻，不是内容）。
        """
        parts = [
            f"workspace={self.workspace}",
            f"owner={self.owner}",
            f"name={self.name}",
            f"view_id={self.view_id or ''}",
            f"subjects={sorted(self.subjects)}",
            f"aspects={sorted(aspect.value for aspect in self.aspects)}",
            f"check_every_days={self.check_every_days}",
            f"ceilings={sorted((c.line.value, c.units) for c in self.ceilings)}",
            f"notify_on={sorted(item.value for item in self.notify_on)}",
            f"enabled={self.enabled}",
            f"next_due_at={self.next_due_at.isoformat() if self.next_due_at else ''}",
            f"note={self.note}",
        ]
        return sha256_text("\n".join(parts))[:16]

    # ------------------------------------------------------------------
    # 纯函数：这一刻要做什么
    # ------------------------------------------------------------------
    def is_due(self, at: datetime) -> bool:
        """这一刻它到期了吗。**纯**：只看 ``at`` 与自己的两格。

        ``enabled=False`` 与 ``next_due_at=None`` 都答「不到期」，但它们**不是**
        同一种不到期 —— 前者是有人关了它，后者是还没排过第一次检查。要在报告里
        分开，读 :meth:`state_note`，不要靠这个布尔值。
        """
        if not self.enabled or self.next_due_at is None:
            return False
        return to_utc(at) >= self.next_due_at

    def target_state(self, *, view_exists: bool | None) -> WatchTargetState:
        """这份 Watch 的目标此刻是什么状态。``view_exists`` 由调用方从库里读来。

        ``None`` 只在一种情况下出现：这条 Watch 直接点名主体（``view_id is None``），
        此时「视图在不在」这个问题没有主语。**不是**「读不到就当不在」—— 那种写法
        会让一次没接上的库读成「目标被删了」。
        """
        if self.view_id is None:
            return WatchTargetState.SUBJECTS
        if view_exists is None:
            raise ValueError(
                f"watch={self.watch_id!r} 指着视图 {self.view_id!r}，"
                "而 view_exists 是 None。判不了的目标状态不能当成「在」："
                "那会让一条指着已删视图的 Watch 看起来一切正常。"
            )
        return WatchTargetState.VIEW if view_exists else WatchTargetState.VIEW_MISSING

    def due_request(
        self, *, view_exists: bool | None, at: datetime
    ) -> WatchDueRequest | None:
        """这一刻的到期请求；不到期就是 ``None``。**纯函数，不写任何东西。**

        ``view_exists`` 是必填关键字（不是缺省 ``None``）：目标视图在不在，只有读库
        的人知道，而一个可以省略它的签名会让「没读」悄悄变成「在」——
        ``domain/saved_view.py`` 的 ``subject_kind`` 那一格是同一条纪律。

        目标视图不在时**照样返回一份请求**，只是带 :attr:`WatchDueRequest.blocked_reason`
        且 ``subjects`` 为空：它执行不了，而「执行不了」必须比「没有要做的」更响。
        """
        state = self.target_state(view_exists=view_exists)
        if not self.is_due(at):
            return None
        blocked = ""
        subjects = self.subjects
        if state is WatchTargetState.VIEW_MISSING:
            blocked = (
                f"目标视图 {self.view_id!r} 已经不在了（被删了，或者从来就没有过）。"
                "这份 Watch 没有可执行的目标 —— 修法是重新存一份视图并把 view_id 指"
                "过去，或者关掉这条 Watch；**不是**把它当成「没什么要做的」。"
            )
            subjects = ()
        assert self.next_due_at is not None  # is_due 已经保证了
        return WatchDueRequest(
            watch_id=self.watch_id,
            watch_revision=self.revision,
            workspace=self.workspace,
            view_id=self.view_id,
            subjects=subjects,
            aspects=self.aspects,
            ceilings=self.ceilings,
            notify_on=self.notify_on,
            due_at=self.next_due_at,
            blocked_reason=blocked,
            target_state=state,
        )

    def state_note(self) -> str:
        """这一条 Watch 现在处于什么状态，**一行话说清**。``list_watches`` 用它。

        三件事各说各的，不合成一个布尔值：关着的、还没排过第一次检查的、目标视图
        不在的。其中最后一件事（``view_missing``）在页面上最容易被读成「不活跃」，
        而它的真相是「这条 Watch 指着一条不存在的视图」—— 那两句话的处置完全不同。
        """
        parts: list[str] = []
        if not self.enabled:
            parts.append("已关闭：不会再产生任何到期请求（有人关掉了它）")
        if self.next_due_at is None:
            parts.append(
                "还没有排过第一次检查（next_due_at 是空的）—— "
                "它与「已关闭」不是同一件事"
            )
        return "；".join(parts)

    def next_revision(self, *, at: datetime | None = None) -> "Watch":
        """这一版的下一版：内容由调用方显式换掉的那几格决定，``revision + 1``。

        ``after_check`` 与它的分工是**这一族里最容易混的一对**：

        * ``after_check`` 说「看完一次之后该是什么样」——它推 ``next_due_at``，
          **不动** ``revision``（同一次检查不是一版新规则）；
        * 这个方法说「下一版叫什么」——它推 ``revision``，**不动** ``next_due_at``。

        所以一条 Watch 改规则时走 ``current.next_revision(at=...).model_copy(...)``，
        而看完一次时走 ``current.after_check(at=...).model_copy(update={"revision": ...})``。
        两条路都不许自己顺手把两格一起推：那样写出来的实现在「只改了规则」与
        「只看了一次」两种情况下盘上都长一样，而它们该不该重置到期时刻是相反的。

        ``created_at`` 原样带过去（``SavedView.next_revision`` 逐字同一条）。
        """
        return self.model_copy(
            update={"revision": self.revision + 1, "updated_at": to_utc(at or utc_now())}
        )

    # ------------------------------------------------------------------
    # 纯函数：这一次看完之后
    # ------------------------------------------------------------------
    def after_check(self, *, at: datetime) -> "Watch":
        """看完一次之后的下一版：``next_due_at`` 推到 ``at + check_every_days``。

        带 ``enabled`` 原样过去 —— 这一次检查不改变「要不要继续看」这件事，
        而一个顺手把它打开的实现在「有人刚关掉、一个在途的检查正好回来」那一瞬
        会把它重新点亮，且不留痕。

        ``revision`` **在这里不动**：下一版号是**写路径**给出来的（``put_watch``
        的 CAS），而一个纯函数顺手把号加一，会让调用方手里的那份与盘上那份对不上，
        于是下一次 CAS 用的是一个谁都没写过的版本。
        """
        return self.model_copy(
            update={
                "next_due_at": to_utc(at) + timedelta(days=self.check_every_days),
                "updated_at": to_utc(at),
            }
        )

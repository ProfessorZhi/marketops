"""真的 :class:`~marketops.ports.scheduling.ScheduleAction`：一次到期 → 一次**真正的 Run 受理**。

## 它为什么在适配层

`domain/scheduling.py:110` 逐字：「受理一轮研究（走 ``application/run_admission.py``
那条受理面）」。那条受理面**不止** ``admit_run`` 一个函数：一次受理要先读盘上的
生效 Scene、再拿它编译一份计划、再把声明、信封、截止、窗口、主体拼成一份规格 ——
那一趟住在这里的两个邻居里（``adapters/mcp_server/runs.py`` 的 ``_preamble`` /
``_Preamble.round_of``，与 ``adapters/mcp_server/workstream.py::_first_round`` 那个
调用点）。``application/`` **不能** import ``adapters/``，所以这一族只能住在适配层。

**这一片没有写第二份编译逻辑**：``perform`` 里的那一段与 ``_first_round`` 逐字同路
（``_preamble`` → ``HOLD`` 分支 → ``round_of`` → ``admit_run``），两处不同的只有
「入参从哪来」—— 那正是这一片要造的东西。

## 入参从 ``definition`` 的哪一格来

``ScheduleAction.perform`` 的签名是冻结的（``ports/scheduling.py:205-265``）：
``(definition, *, fire_id, at)``。所以一切都必须从 ``definition`` 推出来：

* ``action_spec.target_ref`` —— **指名，不内联**（``domain/scheduling.py:196-224``）。
  它指的是**盘上那一版生效 Scene 的 ``scene_id``**，而 ``perform`` **读盘**把它解析
  出来（走 ``_preamble`` → ``_plan_context`` → ``SceneStore``）。解析不出来 ——
  记录不在、没有生效版本、生效版本读不出来、或者它指的不是本部署服务的那一个
  Scene —— 就是 ``FireOutcome.UNRESOLVED`` 那一档，**不静默当成「没有到期」**。
  Run 的 ``workspace`` 因此是**盘上那一版 Scene 的** ``workspace``（``spec_of`` 取
  ``scene.workspace``），不是 ``params`` 里的、也不是 ``definition`` 上的。

* ``action_spec.params`` —— **这一次动作自己的参数**。四格，键名与 ``submit_run``
  的形参尽可能同名（同一个请求在两条入口上要读得像同一件事）：

  ``context``        这一轮研究要什么（一份 ``ResearchContext`` 声明）。必填。
  ``cutoff_hours``   这一轮跑到什么时候为止 —— **从被受理那一刻算的钟点数**，
                     不是一枚写死的时刻。写死时刻的形态在第二次点火上必然过期，
                     而那个失败与「这条规格本身不对」在报告上同形。
  ``execution_mode`` ``bounded_auto`` 或 ``watch``。必填，**且不许是 ``manual``**：
                     §B25 逐字「它只能依据 B17 的 ``bounded_auto/watch`` 授权创建
                     未来 Run，不能把普通 Workstream 自动化」。一条写着 ``manual``
                     的调度不是「跑不起来的调度」，是**一条试图自动化一件没有自动化
                     授权的事的调度** —— 它在这里被拒，而不是被降级成一次手动受理。
  ``budget`` / ``window`` / ``name`` / ``cohort``
                     与 ``submit_run`` 同形同义，逐格透传（``round_of`` 各自校验）。

  多一个键、少一个必填键、类型不对 —— 全部 ``UNRESOLVED``，并点名是哪一个键。

## 另外三个 ``ActionKind`` **失败关闭**

``ActionKind`` 是闭集（``RESEARCH_RUN`` / ``REFRESH`` / ``DIGEST`` / ``NOTIFY``），
今天只有第一类有实现。另外三类回 :attr:`FireOutcome.UNRESOLVED` —— 闭集里
**明确的非成功值**，它的 docstring 逐字覆盖这一格：「这个部署没有能执行它的那一半」。
**不**回 ``SKIPPED``：``adapters/mcp_server/scheduling.py:180-183`` 逐字说过后果 ——
「一个『什么都不做、回 SKIPPED』的动作会让每一次点火都在清单上显示 ``ok`` ——
而它一次研究都没开过」。``UNRESOLVED`` 在 ``_event_for`` 上落成 ``NEEDS_HUMAN``，
所以它在通知清单上看得见。

## ``next_due_at``：一条 ``CALENDAR`` 声明的规则**真的被求值**

求值器住在 ``domain/calendar_rule.py``（一个**显式枚举的闭集** —— RRULE 的受限子集，
选哪一个族、为什么选它的全部理由写在那份 docstring 里）。这一格只做三件事：从
``definition`` 上取那三格（``calendar_rule`` / ``next_due_at`` / ``timezone``）、把
时区解析成一个 ``tzinfo``、把 ``(规则, 锚, 时区)`` 交给它。四类触发各自的处置：

* ``CALENDAR`` —— 求值，得到**严格晚于点火那一刻**的下一期。驱动落后了几期时，
  中间那几期**跳过**（不补跑），所以推进之后的那一版**不会**立刻又到期
  —— 立刻又到期的那条路每一轮都会开出一轮新的 Run（``run_key_of`` 里有
  ``schedule_revision``）。于是「multi-session 关掉之后调度继续」这句话**不再断在**
  「继续多久」上了。
* ``ONE_SHOT`` —— 仍然回 ``None``，而它是**正确**的（「某日重新检查或一次提醒」
  本来就没有下一次）。**这一片没有改它**。
* ``DUE_STATE`` —— 下一次是「每个事实/路线各自的 ``next_due_at`` 里最早的那一个」
  （§8 逐字）：那是一整批对象的读数，不是这条声明上的一格。**不归这一片**，理由是
  盘上今天没有一处产出那份批量读数 —— 见 ``domain/scheduling.py`` 里逐字列出的四格。
* ``EVENT`` —— 下一次由一次业务事件决定，而这一层看不见事件。同样不归这一片。

## 一条**读不懂**的规则不会把调度关掉

``None`` 在这套词汇里是「本来就没有下一次」，而 ``ScheduleDefinition.advanced`` 据此
把它读成**关掉这一条**（``enabled=False`` + ``revision_reason="fired:no_next_due"``）。
一条读不懂的规则（不在闭集里、时区解析不出来、或者 ``CALENDAR`` 却没有规则）
**不许**与它同形，所以 :func:`_calendar_next_due` 抛 :class:`_Unresolvable`，
:meth:`ResearchScheduleAction.perform` 在**受理之前**接住它并回一次 ``UNRESOLVED``
—— 而回执里的 ``next_due_at`` 是**这一版声明的原值**（调度**不推进**）。三件事因此
同时成立：

1. 这条调度**没有被关掉，也没有被动过**：盘上**仍然是原来那一版** ——
   ``enabled=True``、``next_due_at`` 是原值、``revision_reason="declared"``
   （逐字是声明时写下的那个词，不是 ``fired:advanced``），**而且没有第二版**。
   没有第二版是量出来的、不是省出来的：``put_definition`` 按 ``content_digest``
   判「这一次要写的是不是已经是盘上那一份」（该摘要刻意排除 ``schedule_revision``
   / ``revision_reason`` / ``created_at`` / ``updated_at``，见
   ``domain/scheduling.py``），而一次拒绝改不动任何一格业务内容 ——
   于是这条写回是一次**逐字相同**的写回，``revision_written`` 是 ``None``。
2. 这一次点火**没有**开出 Run（拒绝发生在花钱那一步之前），它在通知清单上是一条
   ``NEEDS_HUMAN``（``UNRESOLVED`` 在 ``_event_for`` 上的落点）。
3. 因为第 1 条，它**下一轮醒来仍然到期、还会再拒一次** —— 于是这条调度在清单上一直
   是「到期的、未受理的」。这一条既是代价（每轮一行点火行 + 一条事件）也是它比
   「安静地关掉」值钱的地方 —— 关掉之后它在清单上与「跑完了」逐字同形。

第 1 条那种「零改动」是 ``content_digest`` 这一格**顺带**给出的，不是 ``_advance``
认得「动作没有动」—— 后者才是逐字的落点，而它不在这一片的位置上（见报告里的接线
请求）。差别在计数器上看得见：今天每拒一次仍然落一行 fire、一条事件，只是不落新版本。

**那三格里没有一格写着「声明期拒绝」**，而 TASK-K §4 把声明期列在优先位置。走不通的
原因是两条可查的：写入那条工具（``adapters/mcp_server/scheduling.py``）**正被另一个
slice 写**，而它下游唯一能用上的声明期落点 ``application/scheduling.py::write_schedule``
说给工具听的只有 ``ScheduleConflictError`` / ``StoreError`` 两个词 —— 前者在
``domain/errors.py`` 里逐字只有两个成因（基准版本不对 / 声称首次而它已经在），拿它说
「规则读不懂」会把两件处置不同的事说成同一句。所以这一片选的是**点火期**那条路。

## 不造一个「看起来像下一次」的值

这一格**没有**变成 ``at + 1 day`` 之类的东西。一个编出来的值会让一条
``calendar_rule="monthly"`` 的调度**每天醒一次**，而报告上它是一份正常的清单 ——
它比一句「这一格今天没有可验证的答案」坏得多。今天这一格有答案了，而那个答案来自
规则本身加这一行声明上的三格，不来自墙上时钟。

## 这一族不发通知、不做自己的幂等

两条都是那份契约的正文（``ports/scheduling.py`` 的两个小节）：前者是「一条业务变化
只产生一条事件」，后者是「一个自己去查重试的实现会把幂等键变成两处各判一次的东西，
而两处分歧的那天是两轮研究」。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.mcpserver.exceptions import ToolError

from ...application.plan_preview import PreviewOutcome
from ...application.run_admission import admit_run
from ...config import Settings
from ...domain.calendar_rule import (
    CalendarRuleError,
    parse_calendar_rule,
    parse_fixed_offset,
)
from ...domain.enums import ExecutionMode
from ...domain.scheduling import (
    ActionKind,
    ActionReport,
    FireOutcome,
    ScheduleDefinition,
    TriggerType,
)
from ...ports.budget import BudgetLedger
from ...ports.pool import AccountCohortStore, AccountPoolStore
from ...ports.runs import RunStore
from ...ports.scenes import MaintenanceStore, SceneStore
from .runs import _held_refusal, _preamble

__all__ = ["RESEARCH_SCHEDULE_ACTION_PARAMS", "ResearchScheduleAction"]


#: ``action_spec.params`` 认识的键，一个不多一个不少。
#:
#: 多一个键**不是**「宽容地忽略」：一次被静默吞掉的参数会让写它的人以为它约束了
#: 什么，而 ``submit_run`` 那一条纪律逐字同款（``round_of`` 对 ``name`` 在
#: 非 watch 模式上就是**拒**，不是忽略）。一条今天不认识 ``foo`` 的规格，明天有人
#: 给 ``foo`` 补上实现时，前面那几十条调度会在**没有人再读一遍**的情况下改变行为。
RESEARCH_SCHEDULE_ACTION_PARAMS = frozenset(
    {
        "context",
        "cutoff_hours",
        "execution_mode",
        "budget",
        "window",
        "name",
        "cohort",
    }
)

#: 允许出现在 ``params.execution_mode`` 上的两档。§B25 的那一句是这条闭集的出处。
_AUTOMATION_MODES = frozenset({ExecutionMode.BOUNDED_AUTO, ExecutionMode.WATCH})

#: ``perform`` 认出 ``target_ref`` 之前就失败时的共同措辞头。三个分支共用它，
#: 因为三个分支的**处置是同一条**（去看那一格、去改那条声明），而它们不同的只有
#: 后半句点名的那一格。
_UNRESOLVED_HEAD = "这一条调度点的火今天受理不了："


class _Unresolvable(Exception):
    """这一份动作规格**指不到东西** —— 一次 ``UNRESOLVED`` 的结论，不是一次崩溃。

    它不逃出 :meth:`ResearchScheduleAction.perform`：``UNRESOLVED`` 是
    :class:`FireOutcome` 闭集里那个明确的非成功值，而
    ``application/scheduling.py`` 会把它记成**一行已结的 fire 行**（重放回的是这一格，
    不是再跑一次）。用异常而不是返回值只是为了 ``perform`` 的主体读起来是一条直线 ——
    七处「这一格不对」各自抛，一处接住，结论只有一种。
    """


@dataclass(frozen=True)
class _Declared:
    """``action_spec.params`` 解析完之后的那几格。**没有一格是猜出来的。**"""

    #: 一份 ``ResearchContext`` 声明（**还没**过 pydantic —— 那是 ``_preamble`` 的
    #: ``_declaration`` 干的，它的拒绝比这里能写的任何一句都准）。
    context: Mapping[str, Any]
    #: 这一轮跑多久（钟点，从受理那一刻算）。
    cutoff_hours: int
    #: 谁授权跑起来的。**只能是** ``bounded_auto`` / ``watch``。
    mode: ExecutionMode
    budget: Mapping[str, int] | None
    window: Mapping[str, Any] | None
    name: str
    cohort: str | None


def _declared(definition: ScheduleDefinition) -> _Declared:
    """把 ``action_spec.params`` 收成 :class:`_Declared`，或者一条指得出键的拒绝。"""
    declared = definition.action_spec.params
    known = RESEARCH_SCHEDULE_ACTION_PARAMS

    unknown = sorted(set(declared) - known)
    if unknown:
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``action_spec.params`` 里有这一版不认识的键 "
            f"{unknown}。\n"
            f"  认识的键是 {sorted(known)}。多出来的键**不忽略**：一次被静默吞掉的"
            "参数会让写它的人以为它约束了什么，而这条声明在盘上看起来完全正常。"
        )

    context = declared.get("context")
    if not isinstance(context, Mapping):
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``params.context`` 缺了或者不是一张表。\n"
            "  它是这一轮**要研究什么**（一份 ``ResearchContext`` 声明），而它没有"
            "第二处出处：Scene 那一版上只有地区/场景/预算这些**规则**，没有「这一次"
            "要回答什么问题」。缺了它的调度在清单上与一条健康的调度逐字同形。"
        )

    hours = declared.get("cutoff_hours")
    # ``bool`` 是 ``int`` 的子类，而 ``True`` 会安静地变成「跑一小时」。
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 1:
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``params.cutoff_hours`` 要一个不小于 1 的整数，"
            f"收到的是 {hours!r}。\n"
            "  它是这一轮**从受理那一刻算**跑多久。刻意不是一枚写死的时刻："
            "写死时刻的第二条调度一定已经过期，而那个失败与「这条规格本身不对」"
            "在报告上同形。"
        )

    declared_mode = declared.get("execution_mode")
    try:
        mode = ExecutionMode(declared_mode)
    except ValueError:
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``params.execution_mode`` 是 {declared_mode!r}，"
            f"不是 {[item.value for item in ExecutionMode]} 里的任何一个。\n"
            "  这一格说的是**谁授权这一轮跑起来**，它没有缺省值：一个缺省会让"
            "「这次是有人提交的」成为不写也成立的事。"
        ) from None
    if mode not in _AUTOMATION_MODES:
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``params.execution_mode`` 是 {mode.value!r}，"
            "而一条调度只许在 ``bounded_auto`` / ``watch`` 上创建未来 Run。\n"
            "  §B25 逐字：「它只能依据 B17 的 ``bounded_auto/watch`` 授权创建未来 "
            "Run，**不能把普通 Workstream 自动化**」。所以这一格不是被降级成一次手动"
            "受理，而是被拒 —— 降级的形态是「这条调度每天都在替人做一次他没有授权的"
            "决定」，而它在清单上显示的是一次正常的点火。"
        )

    budget = declared.get("budget")
    if budget is not None and not isinstance(budget, Mapping):
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``params.budget`` 必须是一张表（与 ``submit_run`` "
            f"的同名参数同形），收到的是 {type(budget).__name__}。"
        )
    window = declared.get("window")
    if window is not None and not isinstance(window, Mapping):
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``params.window`` 必须是一张表（与 ``submit_run`` "
            f"的同名参数同形），收到的是 {type(window).__name__}。"
        )
    name = declared.get("name", "")
    if not isinstance(name, str):
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``params.name`` 必须是字符串，收到的是 "
            f"{type(name).__name__}。"
        )
    cohort = declared.get("cohort")
    if cohort is not None and not isinstance(cohort, str):
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``params.cohort`` 必须是字符串或没有，收到的是 "
            f"{type(cohort).__name__}。"
        )

    return _Declared(
        context=context,
        cutoff_hours=hours,
        mode=mode,
        budget=None if budget is None else dict(budget),
        window=None if window is None else dict(window),
        name=name,
        cohort=cohort,
    )


def run_key_of(definition: ScheduleDefinition, fire_id: str) -> str:
    """这一次点火受理出来的 Run 的幂等键。

    **三格都在里面**，而 ``schedule_revision`` 那一格是承重的：

    * ``schedule_id`` / ``fire_id`` —— 「这一次点火是哪一次」；
    * ``schedule_revision`` —— 同一份调度**改过之后**，同一个 ``fire_id`` 是
      **另一次** fire（``domain/scheduling.py`` 模块 docstring 第二段逐字）。
      少了它，第二版上那一次真的该发生的点火会拿回**上一版**那一行 Run
      （``RunStore.admit`` 按 ``(workspace, 键)`` 幂等），报告上写的是「已经受理过
      了」—— 而它一次都没跑。

    这也是 ``claim_fire`` 之外**第二条**独立的防线：那一条盖的是 ``schedule_fire``
    的主键，这一条盖的是 ``research_run`` 的幂等键。两条同时失效的形态是两轮研究，
    而这正是 ``ports/scheduling.py`` 那一段不许实现「自己去查重试」的由来。
    """
    return f"fire:{definition.schedule_id}@{definition.schedule_revision}/{fire_id}"


def _next_due_at(definition: ScheduleDefinition, *, at: datetime) -> datetime | None:
    """下一次什么时候来。``None`` = **这条调度没有下一次**（不是「算不出来」）。

    四类触发里只有 ``CALENDAR`` 这一格今天有答案，而它的答案来自
    ``domain/calendar_rule.py``（闭集、可验证表示、它自己的测试）。另外三类的
    ``None`` 各有理由，见模块 docstring 那一节 —— 其中 ``ONE_SHOT`` 的 ``None``
    是**正确**的，另外两类缺的是别的面上的读数，不是这一格里的东西。

    读不懂的规则**不从这里回 ``None``**：它抛 :class:`_Unresolvable`，由
    :meth:`ResearchScheduleAction.perform` 在受理之前翻成一次显式拒绝，而调度
    **不推进**。理由见模块 docstring「一条读不懂的规则不会把调度关掉」那一节 ——
    ``None`` 与「读不懂」在盘上长得一样，正是这一片要消掉的那种同形。
    """
    if definition.trigger_type is not TriggerType.CALENDAR:
        return None
    return _calendar_next_due(definition, at=at)


def _calendar_next_due(definition: ScheduleDefinition, *, at: datetime) -> datetime:
    """把一条 ``CALENDAR`` 声明的规则求值成下一个时刻。**读不懂就拒，不猜。**

    ``definition`` 上那三格是全部输入：``calendar_rule``（规则）、``next_due_at``
    （锚，``DTSTART`` 那一格）、``timezone``（时区）。三格缺一不可，而**没有一格
    有缺省值** —— 一节里唯一一个缺省会让「这一条声明说的是几点」变成这一层替它决定
    的事，而那个决定在盘上不留痕。

    Raises:
        _Unresolvable: 规则不在闭集里、时区解析不出来、声明的规则缺失、锚缺失，
            或者按它们求不出下一个时刻。四条路各自点名是哪一格不对，而**都不回
            ``None``**。
    """
    declared = definition.calendar_rule
    if declared is None:
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}这是一条 ``CALENDAR`` 声明，而 ``calendar_rule`` 是空的。\n"
            "  这一格**不是**「本来就没有下一次」（那一条按 ``ScheduleDefinition.advanced`` "
            "会把这条调度**关掉**，而关掉之后它与「跑完了、没有下一期」在清单上逐字同形）。"
            "处置是给这条声明补一条规则，而不是让它安静地停下。"
        )
    try:
        rule = parse_calendar_rule(declared)
    except CalendarRuleError as exc:
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``calendar_rule={declared!r}`` 求不了值。\n"
            f"  {exc}\n"
            "  这一条调度**没有被关掉**，也**没有**推进：驱动下一轮还会带着这一句话"
            "再来一次，直到有人把这条规则改成闭集里的取值（或者改成另一条调度）。"
        ) from None

    zone = _zone_of(definition.timezone)
    if zone is None:
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}``timezone={definition.timezone!r}`` 解析不出来。\n"
            "  这一格只认两种拼写：固定偏移（``+08:00`` 这种形状）与一个能解析的"
            " IANA 名字（``Europe/London``；它要问 tzdata，而问不到就是这一句）。\n"
            "  **不把它当成 UTC 顶替**：那会让一条声明看着生效、而实际在错的时间里"
            "点火 —— 与 ``domain/notification.py`` 里免打扰那一格拒绝命名时区是同一条"
            "理由，只是那里的领域层问不了 tzdata。"
        )

    anchor = definition.next_due_at
    if anchor is None:
        # 到期的声明一定有锚（``due()`` 就是这么判的），所以走到这里说明调用方绕过了
        # 那一条。回 None 会把「读不懂」与「没有下一次」混成一件，所以它是一句拒绝。
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}这是一条 ``CALENDAR`` 声明，而它的 ``next_due_at`` 是空的："
            "没有锚，就没有「一天里的几点、星期几、几号」这三格该取谁 —— 这一族的"
            "``BYHOUR``/``BYMINUTE``/``BYDAY``/``BYMONTHDAY`` 缺省时全部取自锚。\n"
            "  锚是这条声明**本来什么时候该来**，所以它不是在点火时才有的东西。"
        )
    try:
        return rule.next_after(tz=zone, anchor=anchor, at=at)
    except CalendarRuleError as exc:
        raise _Unresolvable(
            f"{_UNRESOLVED_HEAD}按 ``calendar_rule={declared!r}`` 求不出 "
            f"{at.isoformat()} 之后的下一个时刻。\n"
            f"  {exc}\n"
            "  同样：这一条调度没有被关掉，也没有推进。"
        ) from None


def _zone_of(declared: str) -> tzinfo | None:
    """``timezone`` 那一格 → 一个 ``tzinfo``；解析不出来回 ``None``。

    两种拼写，**都**落到同一个 ``tzinfo`` 接口上（求值那一层因此对两者一视同仁）：

    * 固定偏移 ``±HH:MM`` —— ``domain/calendar_rule.py::parse_fixed_offset`` 认它，
      而那个函数是**唯一一处**读这条拼写的地方（免打扰窗口那一格用的是同一个读法）；
    * 一个 IANA 名字 —— ``zoneinfo`` 去问 tzdata，问得到就用，问不到回 ``None``
      （这一步**只在这里**：领域层不得触碰文件系统，见那份 docstring）。

    ``ZoneInfo`` 的三种失败都要接住：名字不认识（``ZoneInfoNotFoundError``）、
    一个路径形状的名字（``ValueError``）、以及 tzdata 本身读不出来（``OSError``）。
    三种都回 ``None``，由调用方说一句点名这一刻的拒绝 —— 一个「解析不出来就当 UTC」
    的兜底会让同一条声明在两台机器上给出两个时刻。
    """
    fixed = parse_fixed_offset(declared)
    if fixed is not None:
        return fixed
    try:
        return ZoneInfo(declared)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


class ResearchScheduleAction:
    """一条调度到期时**真的**开出一次研究受理。

    与 ``adapters/memory.py::RecordingScheduleAction`` 的分野只有一条：那个是测试
    替身（只记账、回一个写死的报告），这一个是生产实现。所以它**不收**
    ``next_due_at`` / ``outcome`` 这类"要回什么"的参数 —— 它回答的是一份读数。

    Args:
        settings: 配置。受理只用到它的 ``profile``（本部署服务的那个 Scene，
            ``require_scene`` 逐字只放行它）。
        scenes / runs / maintenance / ledger / accounts / cohorts: 六本**工厂**
            （``Callable[[], Store]``，不是实例）。收工厂不收实例的理由与
            ``scheduling_handlers`` 的那三格逐字相同：``sqlite3`` 的连接不能跨线程，
            而这一族会被触发驱动在它自己的线程上调用。``scenes`` 与 ``runs`` 必填
            （受理读不到场景就指不到东西，写不下 Run 就没有「受理」可言）；
            另外四格按模式用得到，见各自的注解。
    """

    def __init__(
        self,
        *,
        settings: Settings,
        scenes: Callable[[], SceneStore],
        runs: Callable[[], RunStore],
        maintenance: Callable[[], MaintenanceStore],
        ledger: Callable[[], BudgetLedger] | None = None,
        accounts: Callable[[], AccountPoolStore] | None = None,
        cohorts: Callable[[], AccountCohortStore] | None = None,
    ) -> None:
        self._settings = settings
        self._scenes = scenes
        self._runs = runs
        self._maintenance = maintenance
        self._ledger = ledger
        self._accounts = accounts
        self._cohorts = cohorts

    # ------------------------------------------------------------------ 契约
    def perform(
        self, definition: ScheduleDefinition, *, fire_id: str, at: datetime
    ) -> ActionReport:
        """见模块 docstring。四类结论，四条路，各自说得出为什么。"""
        kind = definition.action_spec.kind
        if kind is not ActionKind.RESEARCH_RUN:
            # **不是** ``SKIPPED``：见模块 docstring 那一段（``scheduling.py:180-183``）。
            return ActionReport(
                outcome=FireOutcome.UNRESOLVED,
                message=(
                    f"这一版不执行 {kind.value!r}：动作种类是一个闭集，而今天只有 "
                    f"{ActionKind.RESEARCH_RUN.value!r} 那一半有实现。\n"
                    "  这一条**不是**「今天不用做」（那会是 SKIPPED，而它会让每一次"
                    "点火都在清单上显示 ok）。它是一次要人看一眼的未受理：要么把这条"
                    "调度的 action_spec 改成今天就有的那一类，要么把这一类做完。"
                ),
                next_due_at=None,
            )

        # 规则求值排在这里，也就是**一切要读盘、要花钱的步骤之前**：这一格只用
        # ``definition`` 上那三格（``calendar_rule`` / ``next_due_at`` / ``timezone``），
        # 所以一条读不懂的规则在这里就判得掉，而不是等场景解析、计划预演、受理都走完
        # 之后再拒绝 —— 那时候 Run 已经开出来了，再拒绝就成了「先花钱再说不认」。
        # 读不懂 ⇒ 一次显式拒绝，且**调度不推进**（见模块 docstring「一条读不懂的规则
        # 不会把调度关掉」那一节）。
        try:
            next_due_at = _next_due_at(definition, at=at)
        except _Unresolvable as exc:
            return ActionReport(
                outcome=FireOutcome.UNRESOLVED,
                message=str(exc),
                # ``definition.next_due_at``（**这一版声明的原值**）而不是 ``None``：
                # 在 ``ScheduleDefinition.advanced`` 那儿 ``None`` 是「本来就没有下一次」
                # ⇒ 关掉这条调度，而「读不懂」不许与它同形。
                next_due_at=definition.next_due_at,
            )

        try:
            declared = _declared(definition)
        except _Unresolvable as exc:
            return ActionReport(
                outcome=FireOutcome.UNRESOLVED, message=str(exc), next_due_at=None
            )

        try:
            # 这一段与 ``workstream.py::_first_round`` 是**同一条路**，一处也没另写。
            # ``replace(..., at=at)`` 同款：一次调用只有一个时刻，而它是**点火那一刻**
            # （``_preamble`` 自己取的是 ``utc_now()``，那是服务面的取法）。
            reading = replace(
                _preamble(
                    settings=self._settings,
                    scenes=self._scenes,
                    scene_id=definition.action_spec.target_ref,
                    context=declared.context,
                ),
                at=at,
            )
        except ToolError as exc:
            # 读盘解析不出 ``target_ref`` 指的那一版生效 Scene（记录不在、它的
            # ``effective_version`` 为空、记录说有效而那版读不出来、指的不是本部署
            # 服务的 Scene），或者那份声明本身拼不出来。两种都是**指不到东西**：
            # 一句说得出场景与原因的 UNRESOLVED，而不是一次静默的「今天不到期」。
            return ActionReport(
                outcome=FireOutcome.UNRESOLVED,
                message=f"{_UNRESOLVED_HEAD}{exc}",
                next_due_at=None,
            )

        if reading.plan_report.outcome is PreviewOutcome.HOLD:
            # 一份编不出计划的声明（硬歧义、少了几格）也不是崩溃，是这一次没法受理。
            # 判词与受理面**同一份**（``_held_refusal`` 的 docstring 写着它有这两个读者）。
            return ActionReport(
                outcome=FireOutcome.UNRESOLVED,
                message=f"{_UNRESOLVED_HEAD}{_held_refusal(reading.plan_report)}",
                next_due_at=None,
            )

        this_round = reading.round_of(
            settings=self._settings,
            maintenance=self._maintenance,
            accounts=self._accounts,
            cohorts=self._cohorts,
            budget=declared.budget,
            cutoff=(at + timedelta(hours=declared.cutoff_hours)).isoformat(),
            idempotency_key=run_key_of(definition, fire_id),
            execution_mode=declared.mode.value,
            window=declared.window,
            name=declared.name,
            cohort=declared.cohort,
        )
        # 后面这几步**不接异常**：``admit_run`` 的拒绝（``RunSpecError`` /
        # ``IdempotencyConflictError`` / ``RunStateError`` / ``RunReconciliationError``
        # / pydantic 的 ``ValidationError``）在契约上是**允许抛出**的 ——
        # ``application/scheduling.py`` 把它们记成一行 ``FAILED`` 的 fire 行，而那才是
        # 它们的正确结论（「这一次被拒了」不是「指不到东西」）。在这里再把它们翻译一次
        # 会造出第二处判词，而两处分岔的那天是同一份规格在两条入口上说出两句不同的话。
        run = admit_run(
            self._runs(),
            scene=this_round.scene,
            context=this_round.declared,
            plan=this_round.plan,
            budget=this_round.spec.budget,
            cutoff=this_round.spec.cutoff,
            idempotency_key=this_round.spec.idempotency_key,
            at=this_round.at,
            execution_mode=this_round.mode,
            window=this_round.window,
            ledger=self._ledger() if this_round.window is not None else None,
            scene_store=this_round.store,
            maintenance=(
                self._maintenance() if this_round.mode is ExecutionMode.WATCH else None
            ),
            subjects=this_round.subjects,
            # ``workstream=`` 刻意**不给**：这一片开的是一轮**不挂在任何一次启动
            # 之下**的 Run。挂上去要先把那条工作流读回来、并判它绑的是不是同一版
            # Scene（``application/workstream.py`` 判，不是这里），而那属于
            # 「schedule → workstream」那一条边 —— 它今天**没有生产者**，见 F/REPORT.md
            # 的「没做完的」那一节。给一个没验过的 ``scope_ref`` 会让一个 Run 挂在一行
            # 读不回来的工作流上，而报告上它完全正常。
            workstream=None,
        )
        return ActionReport(
            outcome=FireOutcome.RUN_ADMITTED,
            message=(
                f"受理了第 {definition.schedule_revision} 版声明的这一次点火："
                f"run_id={run.run_id}"
            ),
            detail={
                "run_id": run.run_id,
                "scene_id": this_round.scene.scene_id,
                "scene_version": this_round.scene.version,
                "workspace": this_round.scene.workspace,
                "execution_mode": this_round.mode.value,
            },
            # 上面求过一次的那个值，**不重算**：两处各算一次就是两处各判一次，
            # 而两处分岔的那天是同一份声明回出两个下一次。
            next_due_at=next_due_at,
        )

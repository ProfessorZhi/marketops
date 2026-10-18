"""``calendar_rule`` 的表示与求值：一个**显式枚举的闭集**，读不懂就拒。

## 这一族选的是哪一个，以及为什么是它

§8（``docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md:302``）逐字
``calendar_rule?  # RRULE/受限cron等可验证表示`` —— 两个候选，这里选 **RRULE
（RFC 5545 §3.3.10）的一个受限子集**。三条理由，每一条都是另一族缺的东西：

* **RRULE 有一条 cron 没有的规则：它总是配着一个 ``DTSTART``。** 「每天几点」在
  RRULE 里有一个**标准答案** —— ``BYHOUR``/``BYMINUTE`` 缺省时取 ``DTSTART`` 的
  那个时刻。而这一行声明上**已经有**那一格：
  :attr:`~marketops.domain.scheduling.ScheduleDefinition.next_due_at` 就是那个锚
  （它是「这条调度本来什么时候该来」，而求值把它推过去）。选受限 cron 的话
  ``daily`` 只能是 ``0 0 * * *``（当地午夜），于是「一条声明在 09:00 到期的每日
  调度」会在**没有任何一格写着 09:00** 的情况下被改成午夜醒来 —— 那是一个没有
  出处的取值，而 §8 的下一句正是「不要自己编默认值」。
* **cron 的字段是五个位置串**，其中 ``day-of-month`` 与 ``day-of-week`` 有那条
  著名的「有一个不是 ``*`` 就按 OR 解释」（Vixie cron）。RRULE 的两个过滤器是
  AND 且各自可选，没有这条例外。位置串还让「哪个字段是哪个」取决于**数第几个**，
  而一个数错的实现求出来的是一个**别的、看着正常的**时刻。``KEY=VALUE`` 自点名，
  认不出的键在这里是拒绝，不是忽略。
* 两族的取值面都**必须先收成一个闭集**才谈得上「可验证」，而闭集写在下面。

## 闭集：逐条枚举

一条规则是下面两种拼写之一（整串两端空白不计；除此之外**没有**第三种读法）：

1. **具名频率**：``hourly`` / ``daily`` / ``weekly`` / ``monthly``（恰好这四个，
   小写）。它们是 ``FREQ=<X>`` 的简写，语义逐字相同。这不是装饰：盘上今天就有
   这四个拼写（``tests/`` 里五个文件的声明都写 ``daily``），而**「盘上已经有一个
   取值，求值器读不懂它」正是这一片要消掉的那个状态** —— 一个把 ``daily`` 判成
   「不在子集里」的实现会把它自己的测试夹具判成读不懂。
2. **``FREQ=<X>`` 打头，后接 0..n 个 ``;<KEY>=<VALUE>``**：

   ================  ==================================  ==============================
   ``FREQ``          ``HOURLY`` / ``DAILY`` / ``WEEKLY`` / ``MONTHLY``（必填）
   ``INTERVAL``      ``1``..``1000`` 的整数              可选，缺省 ``1``
   ``BYHOUR``        ``0``..``23`` 的整数                可选，只许配 DAILY/WEEKLY/MONTHLY
   ``BYMINUTE``      ``0``..``59`` 的整数                可选，只许配 DAILY/WEEKLY/MONTHLY
   ``BYDAY``         ``MO``/``TU``/``WE``/``TH``/``FR``/``SA``/``SU``  可选，只许配 WEEKLY
   ``BYMONTHDAY``    ``1``..``31`` 的整数                可选，只许配 MONTHLY
   ================  ==================================  ==============================

   键与取值**区分大小写**（``freq=daily`` 与 ``BYDAY=mo`` 都是拒绝：它们是**键**，
   而键的拼写是这一族的一部分）。重复的键、空的一段（``FREQ=DAILY;;BYHOUR=9``）、
   一段里没有 ``=``、键或值里有空格 —— 全部拒绝。

   **没有**：``COUNT`` / ``UNTIL`` / ``BYMONTH`` / ``BYSETPOS`` / ``BYWEEKNO`` /
   ``BYYEARDAY`` / ``WKST``；**没有**列表（``1,2``）、区间（``1-5``）、步进
   （``*/5``）、``L`` / ``W`` / ``#``（「最后一个周五」这一族）；**没有**秒。
   **没有** ``@daily``：那是 cron 的拼写，而把两个族的取值面混进一个字符串之后，
   「这是什么」就没有答案了 —— 它是**拒绝**，不是被当成 ``daily``。

## 一条规则读出来的是哪一个时刻

求值要三样东西，而三样都在**同一行声明**上：规则（``calendar_rule``）、锚
（``next_due_at``，``DTSTART`` 那一格）、时区（``timezone``）。于是
``(规则, 锚, 时区)`` 确定**唯一**一个下一次：

* 一天里的时刻 = ``BYHOUR``/``BYMINUTE`` 给了就用它，没给就取**锚在当地的那个
  时刻**（RRULE 的缺省语义）。这一格不是编的：它有出处的字段。
* ``WEEKLY`` 的星期几同理（``BYDAY`` 缺省 = 锚的那个星期几）；``MONTHLY`` 的几号
  同理（``BYMONTHDAY`` 缺省 = 锚的那个号）。``BYMONTHDAY=31`` 在只有 30 天的月份
  里**跳过**（RRULE：无效日期被忽略），而不是挪到 30 号。
* 结果**严格晚于** ``at``（点火那一刻），而不是「锚 + 一个周期」。这一条承重：
  驱动停了两天之后，``daily`` 的下一期是「明天那个时刻」，不是「昨天那个时刻」
  —— 后者不晚于 ``at``，于是 ``ScheduleDefinition.advanced`` 写下的那一版**立刻
  又到期**，而 ``run_key_of`` 里有 ``schedule_revision``，每一轮都会开出一轮新的
  Run。错过的那几期**跳过**、不补跑：§8 逐字「错过执行窗口可按 policy
  skip/catch-up 一次，不无限补跑」，而这里取的是 skip 那一半 —— 它是这个函数的
  **性质**而不是一个配置项，因为一个可配置的补跑策略今天没有消费者。
* 秒与微秒**归零**：这一族的分辨率是分钟，而保留秒会让「每一期都比前一期晚 37
  秒」在清单上看不出来。

## 时间算术与 DST：``tzinfo`` 由调用方注入

这一层**不碰 tzdata**（``tests/test_domain_is_pure.py``：领域层不得触碰文件系统），
所以 ``tz`` 是一个参数，由调用方解析好传进来。``+08:00`` 这种固定偏移与
``Europe/London`` 这种命名时区**都**落到同一个 ``tzinfo`` 接口上，于是 DST 的
处置可以写成两句可验证的话，而不是「看情况」：

* **当地时刻不存在**（春季跳表的那一小时，例如 ``Europe/London`` 2026-03-29 的
  ``01:30``）⇒ 这一期**跳过**，取下一期。理由与「不造一个看起来像下一次的值」
  逐字同一条：把它挪到 ``03:30`` 是替这条规则造一个它没写的时刻。
* **当地时刻出现两次**（秋季回表的那一小时）⇒ 取**第一次**（``fold=0``，仍在
  前一个偏移上的那一次）。两次都取会让同一条规则在那一天点两次火，而「是哪一次」
  在盘上分不出来。

两条都不是尽力而为：它们是这一层对 DST 的**全部**话，而
``tests/test_calendar_rule.py`` 各有一条判据钉住。

## 拒绝，而不是猜

:func:`parse_calendar_rule` 对任何不在这两种拼写里的字符串抛
:class:`CalendarRuleError`；求不出时刻时（含步数兜底）也抛它。**没有「尽力解析」
这一档**：一个宽容的解析器会让 ``FREQ=DAILY;BYHOUR=99`` 变成某个时刻，而这件事
在报告上是一次**正常的点火**。读不懂的那条路不是一次崩溃 —— 调用方
（``adapters/mcp_server/schedule_action.py::_next_due_at``）把它翻成一次显式拒绝，
调度**不推进**也不被关掉。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from enum import Enum

from .notification import _offset_minutes

__all__ = [
    "CalendarRule",
    "CalendarRuleError",
    "Frequency",
    "MAX_STEPS",
    "parse_calendar_rule",
    "parse_fixed_offset",
]

#: 求值的兜底步数。**它不是策略，是一条熔断。**
#:
#: 真实路径上这一步数不会有量级：驱动每一轮拿到的是「现在」，而 ``next_due_at``
#: 至多落后几轮 tick。它要挡的是「``next_due_at`` 是 1970 年、规则是 hourly」这种
#: 声明 —— 那一条真的要走几百万步才到得了今天。走到上限是**抛**，不是回一个
#: 近似的时刻：一个「算不动了就回昨天的下一期」的兜底会让这条调度每一轮都再算一次
#: 几百万步，而报告上它只是一条天天点火的调度。
MAX_STEPS = 100_000


class Frequency(str, Enum):
    """``FREQ`` 那四格。**闭集**，多一个都没有。"""

    HOURLY = "HOURLY"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"


class CalendarRuleError(ValueError):
    """一条 ``calendar_rule`` 不在闭集里，或者按它求不出下一个时刻。

    它是 ``ValueError``：这一格的失败**永远是「你给的值不行」**，没有等待与重试
    这一档（与 ``StoreError`` 那类「库打不开」分开，理由同 ``domain/errors.py``
    里那一族的分法：处置不同的失败不共用一个类型）。调用方要么改这条规则、要么
    改这一层 —— 两者都要人来看一眼，所以它到不了「静默跳过」那条路上。
    """


#: 具名频率。**恰好这四个拼写**，见模块 docstring 第 1 条。
_NAMED: dict[str, Frequency] = {
    "hourly": Frequency.HOURLY,
    "daily": Frequency.DAILY,
    "weekly": Frequency.WEEKLY,
    "monthly": Frequency.MONTHLY,
}

#: ``BYDAY`` 的两字母取值 → ``datetime.weekday()`` 的编号（周一是 0）。
_WEEKDAYS: dict[str, int] = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}

#: 每个键允许的取值面。**这张表就是闭集的那一半**：不在这里的键名是拒绝。
_KEYS = frozenset({"FREQ", "INTERVAL", "BYHOUR", "BYMINUTE", "BYDAY", "BYMONTHDAY"})

#: 哪几个键配哪几个频率（``None`` 表示「配得上」，否则是那句拒绝的理由里的一格）。
_BY_HOUR_FREQS = frozenset({Frequency.DAILY, Frequency.WEEKLY, Frequency.MONTHLY})


@dataclass(frozen=True)
class CalendarRule:
    """一条**求过值**的规则。只有 :func:`parse_calendar_rule` 造得出它。

    不在这里做校验是刻意的：一个 dataclass 的 ``__init__`` 不能作为「不在这两个
    拼写里的字符串」的出口 —— 那样 `CalendarRule(freq=...)` 就成了第二条构造路径，
    而两条路径分歧的那天，其中一条造出来的规则没有对应的字符串。全部解析与拒绝
    都在 :func:`parse_calendar_rule` 一处。
    """

    freq: Frequency
    #: ``INTERVAL`` 那一格。**周期是规则的，不是调用方猜的**。
    interval: int = 1
    #: ``BYHOUR``；``None`` = 取锚在当地的那个小时（RRULE 的缺省语义）。
    hour: int | None = None
    #: ``BYMINUTE``；``None`` = 取锚在当地的那一分钟。
    minute: int | None = None
    #: ``BYDAY``；``None`` = 锚的那个星期几。只在 ``WEEKLY`` 上有值。
    weekday: int | None = None
    #: ``BYMONTHDAY``；``None`` = 锚的那个号。只在 ``MONTHLY`` 上有值。
    monthday: int | None = None

    def next_after(self, *, tz: tzinfo, anchor: datetime, at: datetime) -> datetime:
        """**严格晚于** ``at`` 的下一个时刻（UTC，aware）。

        Args:
            tz: 这一行声明的 ``timezone`` 解析出来的那个 ``tzinfo``。**由调用方
                注入**：领域层解析不了命名时区（那要问 tzdata，见模块 docstring）。
            anchor: 这一行声明的 ``next_due_at``（``DTSTART`` 那一格）。
            at: 点火那一刻。结果是「之后的下一个」，不是「之后的一次」。

        Returns:
            一个 aware 的 UTC ``datetime``。

        Raises:
            CalendarRuleError: 锚或 ``at`` 没有时区（一条没有时区的时刻值在这一层
                读不出「几点」），或者 :data:`MAX_STEPS` 步之内求不出下一个时刻。
        """
        if anchor.tzinfo is None or at.tzinfo is None:
            raise CalendarRuleError(
                "求值需要**有时区**的两个时刻（锚与 ``at``），收到的锚是 "
                f"{anchor.isoformat()}、``at`` 是 {at.isoformat()}。\n"
                "  一条没有时区的时刻在这一层读不出「当地几点」，而这正是这一族"
                "求的东西 —— 不许当成本机时区顶替（那会让同一条声明在两台机器上"
                "给出两个时刻，而两台机器上的报告逐字同形）。"
            )
        # 锚在**当地**的样子，而且**去掉 tzinfo**：这一族的每一格（几点、星期几、
        # 几号）都是当地的话，而当地的话在算完之前不该带着一个偏移 —— 带着它的话
        # ``+1 天`` 是「同一个偏移加 24 小时」，而 DST 那一天的那一小时正是这里要
        # 处理的东西。加回偏移只有一处：:func:`_existing_local_time`。
        local = anchor.astimezone(tz).replace(tzinfo=None, second=0, microsecond=0)
        hour = local.hour if self.hour is None else self.hour
        minute = local.minute if self.minute is None else self.minute
        for step in range(1, MAX_STEPS + 1):
            naive = self._candidate(local, hour=hour, minute=minute, step=step)
            if naive is None:
                continue  # 这一格没有这一天（``BYMONTHDAY=31`` 遇上二月）
            instant = _existing_local_time(naive, tz)
            if instant is None:
                continue  # 那个当地时刻不存在（春季跳表那一小时）
            if instant > at:
                return instant
        raise CalendarRuleError(
            f"走了 {MAX_STEPS} 步还没有走出一个晚于 {at.isoformat()} 的时刻：规则是 "
            f"FREQ={self.freq.value}、INTERVAL={self.interval}，锚是 "
            f"{anchor.isoformat()}。\n"
            "  这一条是**熔断**而不是一条判据（见 ``MAX_STEPS``）：真的走到这里"
            "时，要么锚离今天太远，要么规则本身在往一个走不到的方向上迭代。"
            "两条都要人来看一眼，而这里**不回一个近似的时刻** —— 一个兜底的值"
            "会让这条调度每一轮都重算一遍，而报告上它只是一条天天点火的调度。"
        )

    def _candidate(self, local: datetime, *, hour: int, minute: int, step: int) -> datetime | None:
        """第 ``step`` 期在**当地**的样子（naive）。``None`` = 这一期不存在。

        ``local`` 是「锚在当地、秒归零、不带 tzinfo」的样子；``hour`` / ``minute``
        是上面算好的那一格（``BYHOUR``/``BYMINUTE`` 或锚自己的）。

        ``step`` 从 **1** 数起，而 1 是**锚那一格里最早的那一期** —— 它可能就落在
        锚身上（于是不晚于 ``at``，被外面那一层滤掉），也可能落在锚那一格里更晚的
        时刻上（``anchor`` 是 08:00 而 ``BYHOUR=9`` ⇒ 就是**今天** 09:00，不是明天）。
        从 2 起才是一个周期一个周期地推。用「锚 + 一个周期」当起点会让
        「今天 09:00」这一期**被跳过**，而它在报告上只是一条每天晚一天的调度。
        """
        interval = self.interval * (step - 1)
        if self.freq is Frequency.HOURLY:
            # 小时那一族**没有** ``BYHOUR``/``BYMINUTE``（见 ``_BY_HOUR_FREQS``）：
            # 「每 N 小时」的相位就是锚自己那一分钟，多给一格只会让
            # 「**几**点」与「**每小时的第几分钟**」两种意思混在一个键上。
            return local + timedelta(hours=interval)
        if self.freq is Frequency.DAILY:
            return local.replace(hour=hour, minute=minute) + timedelta(days=interval)
        if self.freq is Frequency.WEEKLY:
            target = local.weekday() if self.weekday is None else self.weekday
            # 锚那一格里最早的那个 ``target``（含锚当天），再整周整周地推：``7 *``
            # 而不是让它跟着 ``INTERVAL`` 走天，否则 ``INTERVAL=2`` 会退化成两天一次。
            offset = (target - local.weekday()) % 7
            day = local.date() + timedelta(days=offset + 7 * interval)
            return datetime(day.year, day.month, day.day, hour, minute)
        monthday = local.day if self.monthday is None else self.monthday
        index = local.year * 12 + (local.month - 1) + interval
        year, month = divmod(index, 12)
        month += 1
        if monthday > calendar.monthrange(year, month)[1]:
            # RRULE：无效的那一期被**忽略**（二月没有 31 号 ⇒ 那个月不点）。
            return None
        return datetime(year, month, monthday, hour, minute)


def parse_calendar_rule(value: str) -> CalendarRule:
    """把一条 ``calendar_rule`` 收成 :class:`CalendarRule`，或者拒绝。

    **这里是闭集的唯一入口**：两种拼写之外的一切都抛 :class:`CalendarRuleError`，
    而异常的消息里带着这条规则**为什么**不在里面（点名那个键/那一段）。
    """
    if not isinstance(value, str):
        raise CalendarRuleError(
            f"``calendar_rule`` 必须是字符串，收到的是 {type(value).__name__}。"
        )
    text = value.strip()
    if not text:
        raise CalendarRuleError(
            "``calendar_rule`` 是空的。\n"
            "  空串**不是**「没有规则」那一格 —— 那一格是 ``None``（字段可以没有），"
            "而一个空串是一条**写坏了**的规则：它按这一层读不出任何一次到期。"
        )
    if "=" not in text:
        named = _NAMED.get(text)
        if named is None:
            raise CalendarRuleError(
                f"``calendar_rule={value!r}`` 不在这一版认识的闭集里。\n"
                f"  具名频率恰好四个，小写：{sorted(_NAMED)}；"
                "或者 ``FREQ=<X>`` 打头、后接 ``;KEY=VALUE``（见 "
                "``domain/calendar_rule.py`` 的模块 docstring）。"
            )
        return CalendarRule(freq=named)

    freq: Frequency | None = None
    interval = 1
    hour = minute = weekday = monthday = None
    seen: set[str] = set()
    for chunk in text.split(";"):
        key, sep, raw = chunk.partition("=")
        if not sep:
            raise CalendarRuleError(
                f"``calendar_rule={value!r}`` 里的 {chunk!r} 不是 ``KEY=VALUE``。\n"
                "  这一段读不出键，而一个被忽略的段会让写它的人以为它约束了什么。"
            )
        if key not in _KEYS:
            raise CalendarRuleError(
                f"``calendar_rule={value!r}`` 里有这一版不认识的键 {key!r}。\n"
                f"  认识的键恰好这些：{sorted(_KEYS)}。多出来的键**不忽略**"
                "（同 ``RESEARCH_SCHEDULE_ACTION_PARAMS`` 那一条）：一条今天不认识 "
                "``BYMONTH`` 的规则，明天有人给它补上实现时，盘上那几十条会在"
                "没有人再读一遍的情况下改变行为。"
            )
        if key in seen:
            raise CalendarRuleError(
                f"``calendar_rule={value!r}`` 里 {key!r} 出现了两次。\n"
                "  同键两值里只有一个能生效，而「哪一个」既不在规则里也不在文档里 ——"
                " 拒绝它，而不是按顺序取一个。"
            )
        seen.add(key)
        if key == "FREQ":
            if raw not in Frequency.__members__:
                raise CalendarRuleError(
                    f"``calendar_rule={value!r}`` 的 FREQ={raw!r} 不在闭集里。\n"
                    f"  取值恰好四个（大写）：{sorted(Frequency.__members__)}。"
                )
            freq = Frequency(raw)
        elif key == "INTERVAL":
            interval = _int(raw, low=1, high=1000, key=key, value=value)
        elif key == "BYHOUR":
            hour = _int(raw, low=0, high=23, key=key, value=value)
        elif key == "BYMINUTE":
            minute = _int(raw, low=0, high=59, key=key, value=value)
        elif key == "BYDAY":
            if raw not in _WEEKDAYS:
                raise CalendarRuleError(
                    f"``calendar_rule={value!r}`` 的 BYDAY={raw!r} 不在闭集里。\n"
                    f"  取值恰好这些（大写）：{sorted(_WEEKDAYS)}。这一版**没有**"
                    "``1MO`` 那种序数前缀，也没有 ``1,3`` 这种列表。"
                )
            weekday = _WEEKDAYS[raw]
        else:  # BYMONTHDAY
            monthday = _int(raw, low=1, high=31, key=key, value=value)

    if freq is None:
        raise CalendarRuleError(
            f"``calendar_rule={value!r}`` 没有 ``FREQ``：这一族里它是**必填**的。\n"
            "  一个缺省频率（例如「按日」）会让一条漏写 FREQ 的规则求出一个看着"
            "正常的时刻，而写它的人想的是别的周期。"
        )
    if weekday is not None and freq is not Frequency.WEEKLY:
        raise CalendarRuleError(
            f"``calendar_rule={value!r}`` 把 ``BYDAY`` 配在了 FREQ={freq.value} 上。\n"
            "  这一版里 ``BYDAY`` 只许配 ``WEEKLY``：在 ``DAILY`` 上它是一个**过滤器**"
            "（「每天，但只留周一」）—— 那是另一族语义，而混进来的代价是同一个键在"
            "两条规则上意思不同。"
        )
    if monthday is not None and freq is not Frequency.MONTHLY:
        raise CalendarRuleError(
            f"``calendar_rule={value!r}`` 把 ``BYMONTHDAY`` 配在了 FREQ={freq.value} 上。\n"
            "  这一版里它只许配 ``MONTHLY``，理由同上：在 ``DAILY`` 上它是过滤器。"
        )
    if (hour is not None or minute is not None) and freq not in _BY_HOUR_FREQS:
        raise CalendarRuleError(
            f"``calendar_rule={value!r}`` 把 ``BYHOUR``/``BYMINUTE`` 配在了 "
            f"FREQ={freq.value} 上。\n"
            f"  这一版里它们只许配 {sorted(item.value for item in _BY_HOUR_FREQS)}："
            "``HOURLY`` 的相位就是锚自己那一分钟，多给一格会让「**几**点」与"
            "「每小时的第几分钟」两种意思混在一个键上。"
        )
    return CalendarRule(
        freq=freq,
        interval=interval,
        hour=hour,
        minute=minute,
        weekday=weekday,
        monthday=monthday,
    )


def parse_fixed_offset(declared: str) -> timezone | None:
    """``"+08:00"`` → 一个固定偏移的 ``tzinfo``；命名时区、写坏的形状 → ``None``。

    ``±HH:MM`` 这一条拼写的读法**只有一处**（``domain/notification.py`` 的
    ``_offset_minutes``，免打扰窗口那一格用的是同一个读法），这里 import 它而不是
    抄一遍：两份实现漂移的那天，同一条 ``timezone`` 会在「会不会被免打扰」与
    「下一次什么时候到」两处给出两个答案，而两处各自的报告都正常。
    """
    minutes = _offset_minutes(declared)
    if minutes is None:
        return None
    return timezone(timedelta(minutes=minutes))


def _int(raw: str, *, low: int, high: int, key: str, value: str) -> int:
    """``KEY`` 那一格上的整数，带范围。**不做「尽力解析」**（``+9``/`` 9`` 都拒）。"""
    if not raw.isdigit():
        raise CalendarRuleError(
            f"``calendar_rule={value!r}`` 的 {key}={raw!r} 不是一个十进制整数。\n"
            "  这一层不认 ``+9`` / `` 9`` / ``09:00`` 这些形状：它们是别的族"
            "（或别的字段）的拼写，而一个猜出来的数会在报告上显示成一次正常的到期。"
        )
    number = int(raw)
    if not low <= number <= high:
        raise CalendarRuleError(
            f"``calendar_rule={value!r}`` 的 {key}={raw!r} 越界："
            f"这一格是 {low}..{high}。\n"
            "  越界值**不截断**：把 99 点读成 23 点会让一条写错的规则看起来能用，"
            "而它每一次点火都在一个写规则的人没有说过的时刻上。"
        )
    return number


def _existing_local_time(naive: datetime, tz: tzinfo) -> datetime | None:
    """一个当地时刻对应的那个**真实存在**的 UTC 时刻，或者 ``None``（它不存在）。

    判据是**往返**：把当地时刻按 ``fold=0`` 附上时区、转到 UTC、再转回来，落点
    与原来那个当地时刻逐字相同才算存在。春季跳表那一小时里的时刻过不了这一趟
    （``Europe/London`` 的 01:30 转回来是 02:30），而固定偏移永远过得了。
    ``fold=0`` 同时定下秋季回表时取**第一次**（模块 docstring 那两句）。
    """
    if naive.tzinfo is not None:
        # **抛**而不是把它去掉：一个带着偏移进来的「当地时刻」去掉偏移之后是它
        # 在 UTC 的那个墙上读数，而拿它当当地时刻会算出一个**看着正常**的别的
        # 时刻。这条不变量由 ``next_after`` 的 ``local`` 一处保证，而它一旦被破
        # 坏，坏掉的是「几点」这件事本身。
        raise CalendarRuleError(
            f"内部不变量被破坏：当地时刻 {naive.isoformat()} 带着时区进了这一格。\n"
            "  这一格只收「当地、不带 tzinfo」的形状（见 ``CalendarRule._candidate``）。"
        )
    instant = naive.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
    if instant.astimezone(tz).replace(tzinfo=None) != naive:
        return None
    return instant

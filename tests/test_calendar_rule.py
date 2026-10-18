"""``domain/calendar_rule.py``：闭集表示、求值器、以及它拒绝什么。

## 为什么这一份的判据长这样

这一格是「``calendar_rule`` → 下一个到期时刻」的**唯一**一处实现（生产
``ScheduleAction`` 与它之间只隔一层取字段）。所以判据要的是三件事，缺一不可：

1. **非空**：一条在闭集里的规则必须算出**正确的那个时刻**，而且**只改一格**
   （间隔、字段、规则名）答案就得跟着动 —— 一个回常量的实现、一个把别人的答案抄
   回来的实现，都在这一组上红。期望值是**逐字写下来的 UTC 时刻**，不是
   「大于锚」这种任何实现都能满足的形状。
2. **反面**：闭集之外的一切都拒（``CalendarRuleError``），而拒绝要说得出**是哪一格**。
   闭集的意义在这里：一个「尽力解析」的实现会把 ``0 9 * * *`` 读成一条规则，
   而报告上它是一条正常的调度。
3. **跨时区/DST**：``tz`` 真的被用上（同一个锚、同一份规则，只换时区结果就不同），
   而两次跳变的处置各自有一条判据：**缺口**（当地时刻不存在）跳过那一期、
   **重叠**（当地时刻出现两次）取先出现的那个（``fold=0``）。

这一份**不碰** ``ScheduleDefinition``：那是 ``tests/test_schedule_action.py`` 第 5 节
的地盘（它判那个声明上的三格是真的被读进去的）。这里只有 ``(规则, 锚, 时区, at)``。

## 「不造一个看起来像下一次的值」在这一份上怎么读

第 1 组里每一条的期望值都是算得出来的一个具体时刻。一个 ``at + 1 day`` 的实现
会在 ``hourly`` / ``INTERVAL=6`` / ``BYMONTHDAY=1`` 上分别给出三个错值，而在
``daily`` 那一条上**恰好**正确 —— 所以 ``daily`` 那一行单独留着是有用的。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from marketops.domain.calendar_rule import (
    CalendarRuleError,
    parse_calendar_rule,
    parse_fixed_offset,
)

#: 锚。``2026-09-29T01:00Z`` 在 ``+08:00`` 上是当地 09:00（一条整点的每日调度）。
T0 = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)

SH = parse_fixed_offset("+08:00")
LDN = ZoneInfo("Europe/London")

#: 伦敦 2026 年两次跳变：03-29 起 BST（当地 01:00 → 02:00，01:xx 不存在），
#: 10-25 起 GMT（当地 02:00 → 01:00，01:xx 出现两次）。
GAP_DAY = datetime(2026, 3, 29, tzinfo=timezone.utc)
FOLD_DAY = datetime(2026, 10, 25, tzinfo=timezone.utc)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# 1 · 闭集之内：真的算出那个时刻
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("rule", "anchor", "at", "want"),
    [
        # 具名四个，各自一条。``daily`` 那一行是**一个 ``at + 1 day`` 实现恰好也能过**
        # 的那一行 —— 它在，是因为生产上盘里真的躺着这个值（别的判断都靠它对照）。
        ("hourly", T0, T0, _utc(2026, 9, 29, 2, 0)),
        ("daily", T0, T0, _utc(2026, 9, 30, 1, 0)),
        ("weekly", T0, T0, _utc(2026, 10, 6, 1, 0)),
        ("monthly", T0, T0, _utc(2026, 10, 29, 1, 0)),
        # ``INTERVAL``：只改这一格，答案跟着变（``HOURLY`` 那两行互为对照）。
        ("FREQ=HOURLY;INTERVAL=1", T0, T0, _utc(2026, 9, 29, 2, 0)),
        ("FREQ=HOURLY;INTERVAL=6", T0, T0, _utc(2026, 9, 29, 7, 0)),
        ("FREQ=HOURLY;INTERVAL=1000", T0, T0, _utc(2026, 11, 9, 17, 0)),
        # ``BYHOUR``/``BYMINUTE``：锚那一格里更晚的时刻就是**今天**那一期。
        # （一个「永远从明天算起」的实现会在这两行上给出 09-30。）
        ("FREQ=DAILY;BYHOUR=9;BYMINUTE=30", T0, T0, _utc(2026, 9, 29, 1, 30)),
        ("FREQ=DAILY;BYHOUR=17;BYMINUTE=0", T0, T0, _utc(2026, 9, 29, 9, 0)),
        # ``BYDAY``：锚是周二（2026-09-29），下一个周一才动 —— 不是「七天之后」。
        ("FREQ=WEEKLY;BYDAY=MO", T0, T0, _utc(2026, 10, 5, 1, 0)),
        ("FREQ=WEEKLY;BYDAY=TU", T0, T0, _utc(2026, 10, 6, 1, 0)),
        # ``BYMONTHDAY``：锚是 09-29 ⇒ 下一期是 10-01，不是「一个月之后」。
        ("FREQ=MONTHLY;BYMONTHDAY=1", T0, T0, _utc(2026, 10, 1, 1, 0)),
        ("FREQ=MONTHLY;BYMONTHDAY=15", T0, T0, _utc(2026, 10, 15, 1, 0)),
    ],
)
def test_a_rule_inside_the_subset_computes_that_moment(
    rule: str, anchor: datetime, at: datetime, want: datetime
) -> None:
    """**逐字**期望值：每一条都在一个具体时刻上，不是「大于锚」这种谁都满足的形状。"""
    assert parse_calendar_rule(rule).next_after(tz=SH, anchor=anchor, at=at) == want


def test_the_answers_are_not_one_constant() -> None:
    """十二条规则的答案**互不相同**的多于一条。

    这一行本身不证明什么（上面那一组的期望值已经逐条钉住了），它是一句读数：
    如果有人把上面那一组改成一堆 ``assert got is not None``，这一行仍然会说
    「这些答案其实是同一个值」。
    """
    wanted = {
        parse_calendar_rule(rule).next_after(tz=SH, anchor=T0, at=T0)
        for rule in ("hourly", "daily", "weekly", "monthly")
    }
    assert len(wanted) == 4


def test_a_missed_fire_lands_on_the_rule_grid_not_on_at_plus_one_day() -> None:
    """落后几期时**不补跑**：下一个时刻仍落在规则的格上。

    锚 ``09-29 01:00Z``（当地每日 09:00），点火时刻 ``10-02T05:00Z`` —— 这一刻
    **不在**格上，而 ``at + 1 day`` 是 ``10-03T05:00Z``（也不在）。正确答案是
    ``10-03T01:00Z``：规则的格上的那一个。一个「``at`` 加上一个间隔」的实现在
    这一行上红在两个地方（日期对、时刻错）。
    """
    got = parse_calendar_rule("daily").next_after(
        tz=SH, anchor=T0, at=_utc(2026, 10, 2, 5, 0)
    )
    assert got == _utc(2026, 10, 3, 1, 0)


@pytest.mark.parametrize(
    "rule",
    ["hourly", "daily", "weekly", "monthly", "FREQ=DAILY;BYHOUR=0;BYMINUTE=0"],
)
def test_the_next_moment_is_strictly_later_than_the_fire_moment(rule: str) -> None:
    """**严格**晚于 ``at`` —— 这是「一次 fire 之后不再立刻 fire」的全部内容。

    ``ScheduleDefinition.advanced`` 的 docstring 逐字说了不晚于会怎样：下一版仍然
    到期，而 ``schedule_revision`` 已经加一，于是新的幂等键挡不住第二轮 Run。
    所以这一条是求值器的**契约**，不是一句风格。跨一整个月的每一刻都取一遍。
    """
    parsed = parse_calendar_rule(rule)
    moment = T0
    for _ in range(48):
        moment = moment + timedelta(hours=37)  # 故意错开整点，别只测格上的点
        assert parsed.next_after(tz=SH, anchor=T0, at=moment) > moment


def test_the_seconds_are_zeroed() -> None:
    """答案的秒与微秒是 0：一条 ``daily`` 规则说的不是「09:00:37.412」。

    一个让秒跟着锚走的实现会在盘上写出一个每次都不一样、而报告上看不出差别的时刻。
    """
    anchor = _utc(2026, 9, 29, 1, 0, 37, 412_000)
    got = parse_calendar_rule("daily").next_after(tz=SH, anchor=anchor, at=anchor)
    assert got.second == 0 and got.microsecond == 0


# ----------------------------------------------------------------------
# 2 · 时区：``tz`` 真的被用上；两次跳变各有判据
# ----------------------------------------------------------------------
def test_the_same_rule_under_two_zones_gives_two_moments() -> None:
    """同一个锚、同一份规则，**只换时区** ⇒ 两个不同的时刻。

    这是「``tz`` 真的被用上」的判据。一个把 ``tz`` 丢掉的实现（或一个悄悄按 UTC
    算的实现）在这里给两个相同的答案。

    期望值也都是逐字的，而不是只判「两者不等」：``+08:00`` 那一侧是
    ``09:00+08:00``；``Europe/London`` 那一侧跨过了 03-29 的跳变，当地 09:00 已经是
    BST ⇒ ``08:00Z``（**差一个小时**，而差的那一个小时正是这一条要判的东西）。
    """
    rule = parse_calendar_rule("FREQ=DAILY;BYHOUR=9;BYMINUTE=0")
    anchor = _utc(2026, 3, 27, 9, 0)
    at = _utc(2026, 3, 28, 9, 0)

    fixed = rule.next_after(tz=SH, anchor=anchor, at=at)
    london = rule.next_after(tz=LDN, anchor=anchor, at=at)

    assert fixed == _utc(2026, 3, 29, 1, 0)
    assert london == _utc(2026, 3, 29, 8, 0)
    assert fixed != london


def test_a_local_time_that_does_not_exist_is_skipped() -> None:
    """**缺口**：当地 03-29 的 01:30 不存在（钟从 01:00 直接跳到 02:00）⇒ 跳过那一期。

    期望值是 ``03-30T00:30Z``，也就是 03-30 当地 01:30（BST）。判据要能把它与
    「把不存在的时刻当成 GMT 里的 01:30Z」（即 03-29 那一期照发）分开：
    ``03-29`` 那一天在答案里**根本不出现**。
    """
    got = parse_calendar_rule("FREQ=DAILY;BYHOUR=1;BYMINUTE=30").next_after(
        tz=LDN, anchor=_utc(2026, 3, 28, 1, 30), at=_utc(2026, 3, 28, 1, 30)
    )
    assert got == _utc(2026, 3, 30, 0, 30)
    assert got.astimezone(LDN).date() == GAP_DAY.date() + timedelta(days=1)


def test_a_local_time_that_happens_twice_takes_the_first() -> None:
    """**重叠**：当地 10-25 的 01:30 出现两次 ⇒ 取先出现的那个（``fold=0``，BST）。

    两个读数的差别正好一小时：``fold=0`` 是 ``00:30Z``，``fold=1`` 是 ``01:30Z``。
    逐字钉住前者，是因为「哪一个」是一次**选择**，而选择必须写在判据里 ——
    一个按 ``fold=1`` 算的实现在这里红，而它在盘上看起来完全正常。
    """
    naive = datetime(2026, 10, 25, 1, 30)
    assert naive.replace(tzinfo=LDN, fold=0).astimezone(timezone.utc) == _utc(2026, 10, 25, 0, 30)
    assert naive.replace(tzinfo=LDN, fold=1).astimezone(timezone.utc) == _utc(2026, 10, 25, 1, 30)

    got = parse_calendar_rule("FREQ=DAILY;BYHOUR=1;BYMINUTE=30").next_after(
        tz=LDN, anchor=_utc(2026, 10, 24, 0, 30), at=_utc(2026, 10, 24, 0, 30)
    )
    assert got == _utc(2026, 10, 25, 0, 30)
    assert got.astimezone(LDN).date() == FOLD_DAY.date()


def test_an_aware_input_is_required() -> None:
    """锚与点火时刻都必须是**有时区**的：一个裸的 ``datetime`` 不许被当成 UTC。

    这一类失败不报错时最坏：``2026-03-29 09:00`` 会被读成 ``09:00Z``，而报告上
    它是一条正常的调度 —— 差的那一小时只有把两边都读一遍才看得出来。所以它是一次
    明确的拒绝。
    """
    rule = parse_calendar_rule("daily")
    naive = datetime(2026, 9, 29, 1, 0)

    with pytest.raises(CalendarRuleError):
        rule.next_after(tz=SH, anchor=naive, at=T0)
    with pytest.raises(CalendarRuleError):
        rule.next_after(tz=SH, anchor=T0, at=naive)


def test_a_named_zone_and_a_fixed_offset_are_both_accepted_and_both_honored() -> None:
    """两种 ``tzinfo`` 走的是同一条路：``zoneinfo`` 的与 ``timezone`` 的都行。

    生产那一层把 ``timezone`` 那一格解析成这两种之一，所以求值器**不能**只认一种
    （只认 ``ZoneInfo`` 的实现在 ``+08:00`` 上会红在所有生产判据上；反过来只认固定
    偏移的实现在这里红）。

    期望值仍然逐字给：``FREQ=DAILY;BYHOUR=0`` 说的是**当地**午夜，而同一个 UTC 锚
    在两个时区里的当地午夜差着它们各自偏移之间的那个差（这里是 7 小时）。
    """
    rule = parse_calendar_rule("FREQ=DAILY;BYHOUR=0;BYMINUTE=0")
    anchor = _utc(2026, 9, 28, 9, 0)

    assert rule.next_after(tz=SH, anchor=anchor, at=anchor) == _utc(2026, 9, 28, 16, 0)
    assert rule.next_after(tz=LDN, anchor=anchor, at=anchor) == _utc(2026, 9, 28, 23, 0)


def test_whitespace_around_the_whole_value_is_not_a_difference_and_whitespace_inside_is_a_refusal() -> None:
    """两端空白不计（模块 docstring 逐字写的第一行），**里面**一处空白都不许。

    这一条是那道边界两面的读数：``"  daily  "`` 与 ``"daily"`` 是**同一件事**
    （一个从表格里粘出来、多带了一个空格的值不该被读成一条别的规则），而
    ``FREQ=DAILY; BYHOUR=9`` 里那个空格让 `` BYHOUR`` 变成一个**不认识的键** ⇒ 拒。
    一个把整串按空白切开再拼回去的实现在第二行上绿 —— 而它读 ``0 9 * * *`` 时
    也会绿。
    """
    assert parse_calendar_rule("  daily  ") == parse_calendar_rule("daily")

    for spaced in ("FREQ=DAILY; BYHOUR=9", "FREQ=DAILY;BYHOUR =9", "FREQ=D AILY"):
        with pytest.raises(CalendarRuleError):
            parse_calendar_rule(spaced)


# ----------------------------------------------------------------------
# 3 · 闭集之外：一律拒绝，而且说得出是哪一格
# ----------------------------------------------------------------------
#: 闭集之外的长相，一族一条。``0 9 * * *`` 是**另一个表示族**（受限 cron）——
#: 它在这一版里必须红，因为「RRULE / 受限 cron 里选一种」的意思是另一种**不支持**，
#: 而不是「也顺手认一下」。
OUTSIDE = [
    "",
    "   ",
    "@daily",
    "@hourly",
    "DAILY",
    "Daily",
    "daily;",
    "0 9 * * *",
    "*/5 * * * *",
    "FREQ=daily",
    "FREQ=YEARLY",
    "FREQ=SECONDLY",
    "FREQ=DAILY;COUNT=3",
    "FREQ=DAILY;UNTIL=20261231T000000Z",
    "FREQ=MONTHLY;BYSETPOS=1",
    "FREQ=MONTHLY;BYMONTH=1",
    "FREQ=YEARLY;BYWEEKNO=1",
    "FREQ=MONTHLY;WKST=MO",
    "FREQ=DAILY;BYHOUR=9,10",
    "FREQ=DAILY;BYHOUR=9-17",
    "FREQ=DAILY;INTERVAL=0",
    "FREQ=DAILY;INTERVAL=-1",
    "FREQ=DAILY;INTERVAL=1001",
    "FREQ=DAILY;INTERVAL=1.0",
    "FREQ=DAILY;BYHOUR=+9",
    "FREQ=DAILY;BYHOUR= 9",
    "FREQ=DAILY;BYHOUR=24",
    "FREQ=DAILY;BYMINUTE=60",
    "FREQ=DAILY;BYHOUR=9;BYHOUR=10",
    "FREQ=DAILY;;BYHOUR=9",
    "FREQ=DAILY;",
    "FREQ=DAILY;=9",
    "FREQ=",
]


@pytest.mark.parametrize("rule", OUTSIDE)
def test_a_rule_outside_the_closed_subset_is_refused(rule: str) -> None:
    """闭集之外的一切都抛 ``CalendarRuleError``。**没有**「尽力解析」这一档。

    这一条是这一片的**反面判据**：拿掉 ``parse_calendar_rule`` 里的守卫（让它对不
    认识的东西放行、或者让它忽略不认识的键），这里会**大面积转红**，而不是红一条。
    """
    with pytest.raises(CalendarRuleError):
        parse_calendar_rule(rule)


@pytest.mark.parametrize(
    ("rule", "named"),
    [
        # 多出来的键**不忽略**（同 ``RESEARCH_SCHEDULE_ACTION_PARAMS`` 那条纪律）。
        ("FREQ=DAILY;COUNT=3", "COUNT"),
        ("FREQ=MONTHLY;BYSETPOS=1", "BYSETPOS"),
        # 配错了频率的键。
        ("FREQ=DAILY;BYDAY=MO", "BYDAY"),
        ("FREQ=MONTHLY;BYDAY=MO", "BYDAY"),
        ("FREQ=WEEKLY;BYMONTHDAY=1", "BYMONTHDAY"),
        ("FREQ=HOURLY;BYHOUR=9", "BYHOUR"),
        ("FREQ=HOURLY;BYMINUTE=30", "BYMINUTE"),
        # 越界与坏形状。
        ("FREQ=DAILY;BYHOUR=99", "BYHOUR"),
        ("FREQ=DAILY;INTERVAL=0", "INTERVAL"),
        # 同键两值。
        ("FREQ=DAILY;BYHOUR=9;BYHOUR=10", "BYHOUR"),
        # 取值不在闭集里。
        ("FREQ=YEARLY", "YEARLY"),
    ],
)
def test_the_refusal_names_the_part_that_is_wrong(rule: str, named: str) -> None:
    """拒绝要**说得出是哪一格**：一句「规则不对」在这里是红。

    这一条不是文风判据，它判的是**可操作性**：写规则的人手里只有这一句话，而
    「``BYDAY`` 配在了 ``FREQ=DAILY`` 上」与「``INTERVAL`` 越界」要改的地方不同。
    """
    with pytest.raises(CalendarRuleError) as raised:
        parse_calendar_rule(rule)
    assert named in str(raised.value)


def test_the_parse_refuses_a_value_that_is_not_a_string() -> None:
    """``calendar_rule`` 是 ``str | None``，但**解析器**不收 ``None``。

    「没有规则」与「规则是一张表」都不该在这里变成一条正常规则：``None`` 那一格由
    声明那一侧说清楚（生产 ``_calendar_next_due`` 有它自己的一句话）。
    """
    for bad in (None, 3, ["daily"], {"freq": "DAILY"}):
        with pytest.raises(CalendarRuleError):
            parse_calendar_rule(bad)  # type: ignore[arg-type]

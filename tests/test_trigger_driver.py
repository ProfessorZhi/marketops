"""触发驱动（``marketops/interfaces/trigger_driver.py``）**自己**的判据。

## 这一页不判 §18

「一次点火开出一轮 Run」「Run 的 workspace 来自盘上那一版」「重放不新增 Run」
是 ``tests/test_schedule_action.py`` 的事，它们判的是那个**动作**。
这一页判的是**驱动多出来的那四件** —— 在 ``tick`` 与那个动作之上、只有
「有一个进程真的醒来一次」才成立的东西：

1. **一次醒来是一件事，而且它说得出自己做完没有。** 一轮是有界的
   （``SUGGESTED_LIMIT``），所以回执必须同时给出 ``limit`` 与 ``remaining_due``：
   只报「处理了 N 条」会让一次落后的追赶读起来像「都处理完了」。**这是
   「拒 ≠ 没跑」在驱动这一层的形态。**
2. **``--at`` 真的被当成「现在」。** G2 逐字要求长周期语义用**受控时钟**测，
   而这一格是全仓唯一能把那个时刻喂进去的地方。
3. **重放走驱动也是重放。** 端口把 ``status`` 与 ``replayed`` 分成两格就是为了
   这件事；驱动必须把两格**原样带出来**，不许合成一句「成功了」。
4. **指不到东西的调度报 ``unresolved`` 且不写 Run。** 「这个部署没有能执行它的
   那一半」是 ``FireOutcome.UNRESOLVED`` 已经预留的如实出口，不是一次崩溃、
   也不是一次静默成功。
5. **``fired[]`` 上带得动「哪一轮」与「为什么」。** ``message`` / ``detail`` 两格
   是 2026-09-29 补的：这台驱动是**客户端全断了**也会跑的那一个入口，它的回执常常
   是那一刻唯一还看得见的收据 —— 只有 ``outcome`` 的收据会让一次**拒**读起来像
   一次**莫名的失败**，而处置（去看那一轮 vs 去看那条调度）完全不同。
6. **投递那一半在这条路上真的接上了**（第 6 节，2026-09-29 补）。驱动是全仓
   **唯一**的 sink 接线点（``adapters/mcp_server/scheduling.py:185`` 逐字把这张表
   指派给驱动那一侧），而在此之前那张表恒为空 —— 于是「一个 sink 投递失败只重试
   通知，不重跑研究」（§13 ``:412``）**只在 ``tests/`` 里接得上**，生产路径上没有
   一条 ``accepted`` 落得下来。第 6 节的四条判据各占一格：**接上了**（一条真的
   ``accepted`` 与它那行盘上的收件箱）、**失败只重试投递**（sink 抛一次 ⇒ 一行
   ``unknown``，第二轮重试成 ``accepted``，而 Run 与事件一行都不涨）、**接了与没接
   分得开**（同一个事件两条订阅，只有注册过的那个名字落行；以及一张空表的驱动一行
   都不落）。

## 为什么判据数的是**库里的行**，不是返回值

``_run_rows`` 直接查 ``research_run``。一个「返回值对了但盘上多了一行」的实现会在
这里红 —— 而那一行才是外部副作用。理由与 ``test_schedule_action.py::_rows`` 逐字
同一条：这一族的失败形态是**多出来的那一行**，不是回执上的一句话。

## 这一页不碰网络、不碰凭据

``settings`` 夹具是无凭据的临时运行目录；``_seed_scene`` 写的是本 profile 的第 1 版
声明。全程离线。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from marketops.adapters.notification import InboxSink
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.application.notification import put_subscription
from marketops.config import Settings
from marketops.domain.enums import BudgetLine
from marketops.domain.notification import (
    DeliveryMode,
    NotificationScopeKind,
    NotificationSubscription,
)
from marketops.domain.scene import (
    OutreachPolicyDecl,
    RefreshPlan,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.scheduling import (
    ActionKind,
    ActionSpec,
    ScheduleDefinition,
    TriggerType,
)
from marketops.domain.util import utc_now
from marketops.interfaces import trigger_driver
from marketops.interfaces.composition import (
    default_notification_store,
    default_schedule_store,
)
from marketops.interfaces.trigger_driver import main, run_once

T0 = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
#: 第一次醒来之后的某个时刻。它**不是**第二期到期时刻（那条调度是 daily 的），
#: 所以第二次醒来**不会点火** —— 这一格要问的是「投递那一半自己会不会再试一次」。
T1 = T0 + timedelta(hours=1)

#: 与 ``test_schedule_action.py::CONTEXT`` 逐字同一份声明 —— 这一族的受理与
#: ``submit_run`` 是**同一条路**，两处要能被同一份声明喂饱，否则「同一条路」是空话。
CONTEXT: dict[str, Any] = {
    "geo": "GB",
    "entity_classes": ["school"],
    "asset_classes": ["pitch"],
    "organization_classes": ["trust"],
    "scenario_candidates": [
        {
            "name": "secondary school",
            "reading": "k12",
            "provenance": "declared",
            "source": "req-1",
        }
    ],
    "objective": "find accounts for the approved offer",
    "evidence_requirements": ["official_site"],
}


def _params(**overrides: Any) -> dict[str, Any]:
    """一份写得完整的动作参数。

    ``not_after`` **从当下算**（同 ``test_mcp_runs.py::_window``）：写死一个未来时刻
    是一颗定时炸弹 —— 过了那一天 ``not_after < cutoff`` 恒成立，而那一天之前这条
    判据一直是绿的。
    """
    body: dict[str, Any] = {
        "context": dict(CONTEXT),
        "cutoff_hours": 8,
        "execution_mode": "bounded_auto",
        "window": {
            "max_objects": 5,
            "budget": {"search": 40},
            "not_after": (utc_now() + timedelta(days=3)).isoformat(),
        },
        "budget": {"search": 20},
    }
    body.update(overrides)
    return body


def _definition(settings: Settings, **overrides: Any) -> ScheduleDefinition:
    body: dict[str, Any] = {
        "schedule_id": "sched-0001",
        "schedule_revision": 1,
        "workspace": settings.profile,
        "owner": settings.profile,
        "scope_ref": settings.profile,
        "trigger_type": TriggerType.CALENDAR,
        "timezone": "+08:00",
        "calendar_rule": "daily",
        "next_due_at": T0,
        "action_spec": ActionSpec(
            kind=ActionKind.RESEARCH_RUN,
            target_ref=settings.profile,
            params=_params(),
        ),
        "budget_policy_ref": None,
        "notification_policy_ref": None,
        "enabled": True,
        "revision_reason": "declared",
        "created_at": T0,
        "updated_at": T0,
    }
    body.update(overrides)
    return ScheduleDefinition(**body)


def _put(settings: Settings, definition: ScheduleDefinition) -> None:
    """把一条声明写进**驱动真的会读的那张表**（不是内存替身）。

    驱动自己取 store（``run_once`` 里那一行）；用内存替身喂它的话，「驱动读到的是
    哪一本账」这件事就没被测到 —— 而那一本账正是这一片的全部。
    """
    default_schedule_store(settings)().put_definition(definition, expected_revision=None)


def _seed_scene(settings: Settings) -> None:
    """本 profile 的第 1 版声明。

    ``scene_id`` / ``workspace`` 都取部署的 profile：``require_scene`` 只放行它，
    而这不是省事 —— 一个部署只服务一个 Scene。
    """
    store = SqliteSceneStore(settings.database_path)
    store.put_record(
        SceneRecord(
            scene_id=settings.profile,
            workspace=settings.profile,
            state=SceneState.ACTIVE,
            latest_version=1,
            effective_version=1,
            created_at=T0,
            updated_at=T0,
        ),
        expected_revision=None,
    )
    store.put_version(
        SceneVersion(
            scene_id=settings.profile,
            workspace=settings.profile,
            version=1,
            region=("GB-England-Devon",),
            scenario="sports-surface-maintenance",
            product="acme-natural-grass",
            admissions=("sports-club",),
            exclusions=("golf-course",),
            role_policy="role-policy-v1",
            source_plan=("mother-pool-import",),
            budget=(SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
            refresh=RefreshPlan.uncalibrated_default(),
            outreach=OutreachPolicyDecl.uncalibrated_default(),
            profile_id=settings.profile,
            profile_version="2026-09-01",
            created_at=T0,
            supersedes=None,
        )
    )


@pytest.fixture()
def seeded(settings: Settings) -> Settings:
    _seed_scene(settings)
    return settings


def _run_rows(settings: Settings) -> int:
    """盘上**真的**有几行 Run。数的是库，不是回执。"""
    with sqlite3.connect(settings.database_path) as conn:
        found = conn.execute(
            "SELECT COUNT(*) FROM research_run WHERE workspace = ?",
            (settings.profile,),
        ).fetchone()
    return int(found[0])


# ----------------------------------------------------------------------
# 1 · 一次醒来说得出它做完没有
# ----------------------------------------------------------------------
def test_an_empty_deployment_wakes_and_reports_zero(settings: Settings) -> None:
    """没有任何调度时，醒来一次是**零件事**，而回执要把分母也报出来。

    ``limit`` 与 ``remaining_due`` 一起读才说得清「这一轮做完了」还是「这一轮
    做满了」—— 少了 ``limit``，``remaining_due=0`` 没有量纲。
    """
    receipt = run_once(settings, at=T0)

    assert receipt["fired"] == []
    assert receipt["delivered"] == []
    assert receipt["remaining_due"] == 0
    assert receipt["remaining_events"] == 0
    assert receipt["limit"] > 0
    assert receipt["at"] == T0.isoformat()
    assert receipt["workspace"] == settings.profile


def test_a_bounded_round_reports_what_is_left_over(seeded: Settings) -> None:
    """**一轮有界**：两条都到期、``limit=1`` ⇒ 做一件、**余额报 1**。

    这一格是「拒 ≠ 没跑」的反面：一条只报「处理了 1 条」的实现，会让一次落后的
    追赶与一次「只有一条到期」读成同一件事。两条调度是**两个不同的 ``schedule_id``**，
    所以它们不是同一次 fire 的重放。
    """
    _put(seeded, _definition(seeded, schedule_id="sched-0001"))
    _put(seeded, _definition(seeded, schedule_id="sched-0002"))

    receipt = run_once(seeded, at=T0, limit=1)

    assert len(receipt["fired"]) == 1
    assert receipt["remaining_due"] == 1


# ----------------------------------------------------------------------
# 2 · 到期的真的建 Run；`--at` 真的被当成「现在」
# ----------------------------------------------------------------------
def test_a_due_schedule_admits_exactly_one_run(seeded: Settings) -> None:
    """走**驱动**（不直接调那个动作）跑一次到期 ⇒ 盘上多出**恰好一行** Run。"""
    _put(seeded, _definition(seeded))

    receipt = run_once(seeded, at=T0)

    assert [row["status"] for row in receipt["fired"]] == ["admitted"]
    assert [row["replayed"] for row in receipt["fired"]] == [False]
    assert [row["outcome"] for row in receipt["fired"]] == ["run_admitted"]
    assert _run_rows(seeded) == 1


def test_a_schedule_that_is_not_due_yet_does_nothing(seeded: Settings) -> None:
    """``next_due_at`` 在未来 ⇒ 这一轮**一件都不做**，而库里**零行** Run。

    这一格与上一格是**一对**：上一条证明「到期的会做」，这一条证明「没到期的不做」。
    只有上一条的话，一个「醒来就把所有调度都点一遍」的实现会全绿。
    """
    _put(seeded, _definition(seeded, next_due_at=T0 + timedelta(days=1)))

    receipt = run_once(seeded, at=T0)

    assert receipt["fired"] == []
    assert _run_rows(seeded) == 0


def test_the_same_wake_up_replayed_does_not_add_a_run(seeded: Settings) -> None:
    """同一个 ``--at`` 醒来两次 ⇒ **不新增 Run**，第二次**一件都没到期**。

    ## 第二次为什么是空的（这一格是这条判据的全部）

    不是「驱动认出了重放」，而是**声明被推进了**：第一次点火跑完之后
    ``settle_fire`` → ``advanced`` 把 ``next_due_at`` 推到了下一期，于是第二次醒来
    时 ``due()`` 交回来的清单里**没有它**。所以这条判据同时钉住两件事：

    * 第一格里 ``advanced_to`` **不是 ``None``** —— 声明真的推进了（``next_due_at``
      不推进的话，这条调度每被问一次就到期一次，而外部驱动是每分钟问一次的）；
    * 第二格里 ``fired == []``，而库里的 Run **仍然是一行**。

    ``replayed=True`` 那一格**不在这里判**：它属于「同一个 ``fire_id`` 被再次
    ``claim``」的情形（占位行还在、结论没落定），构造它要一个停在半路的动作，
    而那是 ``test_schedule_action.py`` 的事。**这一页不替它判** —— 两处各判一半
    比一处判一句「成功了」值钱，理由见 :class:`~marketops.domain.scheduling.FireReceipt`
    的 docstring。
    """
    _put(seeded, _definition(seeded))

    first = run_once(seeded, at=T0)
    second = run_once(seeded, at=T0)

    assert [row["outcome"] for row in first["fired"]] == ["run_admitted"]
    assert [row["advanced_to"] for row in first["fired"]] != [None]
    assert second["fired"] == []
    assert second["remaining_due"] == 0
    assert _run_rows(seeded) == 1


# ----------------------------------------------------------------------
# 3 · 指不到东西的调度：报 `unresolved`，不写 Run，也不崩
# ----------------------------------------------------------------------
def test_a_schedule_pointing_at_nothing_reports_unresolved(settings: Settings) -> None:
    """**没有场景**的部署里，一条到期的调度报 ``unresolved`` 且库**零行** Run。

    ``FireOutcome.UNRESOLVED`` 是端口逐字预留的出口：「动作规格指不到东西 —— 目标
    不在了、或者**这个部署没有能执行它的那一半**」。三件事必须分得开，而它们在这一格
    上正是三件不同的报告：

    * ``unresolved`` —— 有人接下了，但指不到东西（**这一格**）；
    * ``failed`` —— 跑了并且失败了；
    * 崩溃 / ``NOT_DUE`` —— 根本没人接。

    把这一格报成失败会让人去重放，而重放从来不重跑动作；报成成功会让人以为研究跑过了。
    """
    _put(settings, _definition(settings))

    receipt = run_once(settings, at=T0)

    assert [row["outcome"] for row in receipt["fired"]] == ["unresolved"]
    assert _run_rows(settings) == 0


def test_unresolved_is_not_a_crash_so_the_next_schedule_still_runs(
    settings: Settings,
) -> None:
    """前一条指不到东西**不挡住**后一条 —— 一轮里的失败是**有限**的。

    G2 逐字要求控制器「**有限成本继续其他合法路径**，而不是把整条线无限重试或统一
    宣称没有客户」。两条调度在这里都没有场景，所以两条都是 ``unresolved``；承重的是
    **``fired`` 里有两行**：一次指不到东西没有把这一轮掐掉。
    """
    _put(settings, _definition(settings, schedule_id="sched-0001"))
    _put(settings, _definition(settings, schedule_id="sched-0002"))

    receipt = run_once(settings, at=T0)

    assert sorted(row["schedule_id"] for row in receipt["fired"]) == [
        "sched-0001",
        "sched-0002",
    ]
    assert {row["outcome"] for row in receipt["fired"]} == {"unresolved"}


# ----------------------------------------------------------------------
# 4 · `fired[]` 上那三格：开了哪一轮 / 为什么没开
# ----------------------------------------------------------------------
# 这一节是 2026-09-29 补的。`_receipt` 的 docstring 原先写「``fire`` 那一格**整块
# 带出来**」，而代码只吐了 `outcome` —— 散文与代码不一致，缺的是 `message` /
# `detail`。这台驱动是「**客户端全断了**也会跑的那一个入口」，它的回执常常是那一刻
# **唯一**还看得见的收据；只报 `outcome` 的话，读的人知道「没开出来」，却读不到
# **为什么** —— 而一个只报「失败了」的收据会让人去**重放**，那恰恰是这一族里最不该
# 做的事。两条判据一正一反，每条都同时读**回执**与**库里的行**。
def test_an_admitted_fire_names_its_run_on_the_receipt(seeded: Settings) -> None:
    """一次真的点火：回执上那个 ``run_id`` **就是盘上那一行**。

    两个量具：``detail.run_id`` / ``message`` 与 ``research_run.run_id``。
    只报 ``outcome=run_admitted`` 的收据让人知道「开出来了」，却读不到**开出来的是
    哪一轮** —— 而那一轮正是读的人下一步要去看的东西。
    """
    _put(seeded, _definition(seeded))

    receipt = run_once(seeded, at=T0)

    (row,) = receipt["fired"]
    assert row["outcome"] == "run_admitted", row
    run_id = row["detail"]["run_id"]
    assert run_id, row
    assert run_id in row["message"], (
        "回执说了开了哪一轮，却没把那一轮的号说出来",
        row,
    )

    with sqlite3.connect(seeded.database_path) as conn:
        on_disk = [found[0] for found in conn.execute("SELECT run_id FROM research_run")]
    assert on_disk == [run_id], (on_disk, row)


def test_a_refused_fire_carries_its_reason_on_the_receipt(settings: Settings) -> None:
    """一次**没受理**的点火：拒词在回执上，而盘上**零行**。

    这是上一条的反面，也是「拒 ≠ 没跑」在**驱动**这一层的形态。断言只咬住两件事：
    那一格是一个**非空**的字符串，且它在**抬头之后还有下文** —— 抬头本身只说
    「受理不了」，读的人要知道的是**为什么受理不了**。
    """
    _put(settings, _definition(settings))

    receipt = run_once(settings, at=T0)

    (row,) = receipt["fired"]
    assert row["outcome"] == "unresolved", row

    message = row["message"]
    assert isinstance(message, str) and message.strip(), row
    head = "这一条调度点的火今天受理不了："
    assert message.startswith(head), (message, row)
    assert len(message) > len(head), (
        "拒词只有抬头，没有下文 —— 读的人知道没开出来，读不到为什么",
        message,
    )
    assert isinstance(row["detail"], dict), row
    assert _run_rows(settings) == 0


# ----------------------------------------------------------------------
# 5 · 两道入口守卫
# ----------------------------------------------------------------------
def test_a_naive_at_is_refused(tmp_path: Any, capsys: Any) -> None:
    """``--at`` **不带时区**要拒，不是补一个。

    一个不带时区的字符串按本机时区解出来，在跨时区的部署上会让「到期」与「记下的
    时刻」差几个小时 —— 而两份报告各自都正常。这与 ``db.py`` 里那几处「两个库
    互相看不见」是同一种失败：**没有任何一处会报错**。
    """
    with pytest.raises(SystemExit) as excinfo:
        main(["--at", "2026-09-29T15:00:00"])

    assert excinfo.value.code == 2
    assert "必须带时区" in capsys.readouterr().err


# ----------------------------------------------------------------------
# 6 · 投递那一半在生产路径上接上了（2026-09-29）
# ----------------------------------------------------------------------
# 这一节之前，驱动传给 ``tick`` 的是一张**空表** —— 于是 ``InboxSink``（端口逐字
# 说的「唯一一个 ``accepted`` 证明得出来的出口」）在生产上**零个调用者**，
# `§13 :412` 那句「一个 sink 投递失败只重试通知，不重跑研究」只有测试里接得上。
# 接线由 ``trigger_driver.deployment_sinks`` 一处给出，而这一节判的是**接线的效果**，
# 不是那个函数长什么样：四条判据全部走 ``run_once``（驱动进程的入口），盘上读数一律
# 读回来（收件箱那行 JSONL、``delivery_receipt`` 的 payload、``research_run`` 的行数），
# 并且每一条都同时问「投递发生了什么」与「业务那一侧涨没涨」。


def _subscribe(
    settings: Settings, *, sink: str = "inbox", subscription_id: str = "sub-0001"
) -> None:
    """一条**工作区范围、即时**的订阅：这一节的四格都只用它。

    ``kinds`` / ``severities`` 留空集 = 不过滤（``domain/notification.py`` 那一节：
    空集与「一条都不匹配」是两件事）。这条判据要问的是「投出去没有、盘上有没有那一
    行」，不该顺带把「MATERIAL_CHANGE 有没有被写对」也考一遍。
    """
    put_subscription(
        default_notification_store(settings)(),
        NotificationSubscription(
            subscription_id=subscription_id,
            revision=1,
            workspace=settings.profile,
            principal="operator",
            scope_kind=NotificationScopeKind.WORKSPACE,
            scope_ref=settings.profile,
            kinds=(),
            severities=(),
            sink=sink,
            delivery_mode=DeliveryMode.IMMEDIATE,
            quiet_hours=None,
            timezone=None,
            updated_at=T0,
        ),
        expected_revision=None,
        at=T0,
    )


def _inbox_path(settings: Settings) -> Any:
    """这个部署的收件箱落在哪 —— 从**配置**取，不在判据里拼字符串。

    与 ``InboxSink.path_for`` 是同一条推导（``<root>/<workspace>/inbox.jsonl``），
    而根取自 :attr:`~marketops.config.Settings.notification_inbox_dir`：两处各写一遍
    路径的那天，「投到别处去了」在判据上读起来像「没有投」。
    """
    return settings.notification_inbox_dir / settings.profile / "inbox.jsonl"


def _inbox_lines(settings: Settings) -> list[dict[str, Any]]:
    """收件箱里**读得回来**的那些行。文件不存在 = 一行都没有（不是异常）。"""
    path = _inbox_path(settings)
    if not path.exists():
        return []
    return [
        json.loads(raw)
        for raw in path.read_text(encoding="utf-8").splitlines()
        if raw.strip()
    ]


def _receipt_payloads(settings: Settings) -> list[dict[str, Any]]:
    """``delivery_receipt`` 那几行，按 ``attempt`` 升序。**读的是盘，不是回执。**"""
    with sqlite3.connect(settings.database_path) as conn:
        rows = [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT payload FROM delivery_receipt ORDER BY attempt"
            )
        ]
    return rows


def _event_rows(settings: Settings) -> int:
    """盘上**真的**有几行业务事件 —— 与 ``_run_rows`` 同一条理由。"""
    with sqlite3.connect(settings.database_path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM notification_event").fetchone()[0])


def _states(receipt: dict[str, Any]) -> list[str]:
    """这一轮投递里每一条收据的状态，按 ``delivered`` 的次序。"""
    return [
        outcome["state"] for item in receipt["delivered"] for outcome in item["outcomes"]
    ]


def _only_outcome(receipt: dict[str, Any]) -> dict[str, Any]:
    """这一轮**恰好一条**订阅读数（四条判据用的都是一条订阅）。"""
    (item,) = receipt["delivered"]
    (outcome,) = item["outcomes"]
    return outcome


def test_a_wake_up_delivers_into_the_inbox_and_the_line_is_readable(seeded: Settings) -> None:
    """**端到端**：走驱动 → 事件被投出去 → 盘上有收据、且 ``accepted`` 可证。

    ``accepted`` 不是一个可以自证的词：``InboxSink`` 的 docstring 逐字说它的判据是
    「那一行**此刻在盘上，并且读得回来**」。所以这一条既读回执（状态与 attempt），
    也把那行 JSONL **读回来**并逐格对上 ``event_id`` / ``subscription_id`` /
    ``workspace``，再读 ``delivery_receipt`` 那一行看它记的时刻**就是这一轮的 ``at``**。

    两格刻意分开写：

    * **第一次醒来** —— 一次真的投递（``accepted``，attempt 1，``note`` 为 ``None``：
      ``note`` 取自 ``receipt.failure``，一次收下的投递没有失败理由可写）；
    * **第二次醒来** —— 投递那一半照跑，而读数变成 ``settled``：一次收下的投递不是
      一次待办，重复投一次**不追加第二行**。同时 ``research_run`` 与
      ``notification_event`` **一行都不涨** —— 这就是「不重跑研究」在生产路径上的读数。
    """
    _put(seeded, _definition(seeded))
    _subscribe(seeded)

    first = run_once(seeded, at=T0)

    assert _states(first) == ["accepted"], first
    outcome = _only_outcome(first)
    assert outcome["sink"] == "inbox", first
    assert outcome["attempt"] == 1, first
    assert outcome["note"] is None, "accepted 那一格不该带一句失败理由"

    lines = _inbox_lines(seeded)
    assert len(lines) == 1, lines
    assert lines[0]["event_id"] == first["delivered"][0]["event_id"], lines
    assert lines[0]["subscription_id"] == outcome["subscription_id"], lines
    assert lines[0]["workspace"] == seeded.profile, lines

    (row,) = _receipt_payloads(seeded)
    assert row["outcome"] == "accepted", row
    assert row["message_ref"] and row["message_ref"].endswith("#1"), row
    # **投递那一刻就是点火那一刻**：两半共用 ``tick`` 里那一个 ``moment``，而这一条
    # 把回执上的 ``at`` 与盘上收据的 ``recorded_at`` 对上 —— 对不上时，收据上的时刻
    # 与事件上的时刻会来自两个瞬间，而两份报告各自都正常。
    #
    # 比的是**时刻**而不是字符串：两个量具的序列化形状不同（回执那一格走
    # ``TickReport.at.isoformat()`` 得 ``…+00:00``，收据那一格在盘上是 ``…Z``）。
    # 比字符串会在这两格里读出一次**不存在的**分歧，而它看起来正好像一次真事故。
    assert first["at"] == T0.isoformat(), first
    assert datetime.fromisoformat(row["recorded_at"]) == T0, row

    runs, events = _run_rows(seeded), _event_rows(seeded)
    second = run_once(seeded, at=T0)

    assert _states(second) == ["settled"], second
    assert _run_rows(seeded) == runs == 1, "投递不许再开一轮研究"
    assert _event_rows(seeded) == events == 1, "一条业务变化只产生一条事件"
    assert len(_receipt_payloads(seeded)) == 1, "收下过的那次投递不是一次待办"
    assert len(_inbox_lines(seeded)) == 1, "同一条投递重复一次不追加第二行"


def test_a_sink_that_raises_is_retried_without_rerunning_the_research(
    seeded: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**失败注入**：sink 抛一次 ⇒ ``unknown`` 一行 ⇒ 下一轮重试成 ``accepted``。

    注入点在**适配器**上（``InboxSink.deliver`` 第一次抛），不在接线上：那个 sink 仍然
    是生产线装配出来的那一个，所以这一条同时证明了「抛出来的异常被记成一次尝试」与
    「生产路径上的第二次醒来会重试它」。

    断言分三格，缺一格都是那种最贵的假绿：

    * ``retry_delivery`` 第 6 步真的走了 ⇒ 收据一行、``outcome=unknown``、``attempt=1``、
      ``failure`` 里是**真实的异常文本**，且 ``message_ref`` 为 ``None``（没有东西收下过
      它，就没有可指的落点）；
    * **业务那一侧一行都不涨** —— 同一轮里 ``research_run`` 与 ``notification_event``
      都还是 1（「sink 失败不触发研究重跑」的读数）；
    * 再醒来一次：状态是 ``accepted`` 而 **attempt 是 2**。这一格排掉的正是五个判词里
      的 ``SETTLED``（「之前已经有过一次 accepted ⇒ 这一轮什么都不做」）—— 若上一次的
      ``unknown`` 被读成了已收下，这一轮的读数会是 ``settled`` 而**盘上一个新行都不会
      有**，看起来像「已经投过了」。
    """
    _put(seeded, _definition(seeded))
    _subscribe(seeded)

    real = InboxSink.deliver
    calls: list[int] = []

    def explode_once(self: InboxSink, event: Any, subscription: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("对端 503（判据注入）")
        return real(self, event, subscription)

    monkeypatch.setattr(InboxSink, "deliver", explode_once)

    first = run_once(seeded, at=T0)

    assert calls == [1], "生产路径上的 sink 一次都没有被调到"
    assert _states(first) == ["retrying"], first
    outcome = _only_outcome(first)
    assert outcome["attempt"] == 1, first
    assert "sink 抛了 RuntimeError" in outcome["note"], outcome

    (row,) = _receipt_payloads(seeded)
    assert row["outcome"] == "unknown", row
    assert row["attempt"] == 1, row
    assert row["message_ref"] is None, row
    assert "对端 503（判据注入）" in row["failure"], row

    runs, events = _run_rows(seeded), _event_rows(seeded)
    assert runs == 1 and events == 1, (runs, events)
    assert _inbox_lines(seeded) == [], "那一次写盘没有成功，收件箱里不该有行"

    second = run_once(seeded, at=T1)

    assert _states(second) == ["accepted"], second
    retried = _only_outcome(second)
    assert retried["attempt"] == 2, second

    assert _run_rows(seeded) == runs == 1, "重试投递不许重跑研究"
    assert _event_rows(seeded) == events == 1
    assert [row["attempt"] for row in _receipt_payloads(seeded)] == [1, 2], (
        "重试产生新的一行，不覆盖旧行"
    )
    assert [row["outcome"] for row in _receipt_payloads(seeded)] == [
        "unknown",
        "accepted",
    ]
    assert len(_inbox_lines(seeded)) == 1, "收下的那一次只落一行"


def test_only_the_registered_name_reaches_the_inbox(seeded: Settings) -> None:
    """**负控（一）**：同一个事件、同一轮、两条订阅 —— 接与没接要分得开。

    两条订阅只有 ``sink`` 那一格不同：``inbox``（本部署注册的那个名字）与
    ``nowhere``（一个谁也没注册的名字）。一轮之后：

    * ``inbox`` 那一格 ``accepted``，并且盘上**恰有一行**、它的 ``subscription_id``
      就是这一条 —— 「接了」；
    * ``nowhere`` 那一格 ``retrying``，而它的 ``note`` 逐字点名「这个部署没有注册
      ``sink='nowhere'``」，盘上**没有**它的行 —— 「没接」，但它**记在案**，不是静默
      跳过（这也是 ``§20`` 那个 ``BLOCKED_EXTERNAL`` 的 Feishu 在这个部署里的读数）。

    少了这一条，「那条接线真的在生效吗」只能靠读源码回答。
    """
    _put(seeded, _definition(seeded))
    _subscribe(seeded, subscription_id="sub-0001", sink="inbox")
    _subscribe(seeded, subscription_id="sub-0002", sink="nowhere")

    receipt = run_once(seeded, at=T0)

    (item,) = receipt["delivered"]
    by_subscription = {o["subscription_id"]: o for o in item["outcomes"]}
    assert set(by_subscription) == {"sub-0001", "sub-0002"}, receipt
    assert by_subscription["sub-0001"]["state"] == "accepted", receipt
    assert by_subscription["sub-0002"]["state"] == "retrying", receipt
    assert "没有注册 sink='nowhere'" in by_subscription["sub-0002"]["note"], receipt

    assert [line["subscription_id"] for line in _inbox_lines(seeded)] == ["sub-0001"], (
        "没注册的那个名字不该落任何行"
    )
    assert _run_rows(seeded) == 1


def test_a_driver_with_no_sink_registered_writes_no_line(
    seeded: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**负控（二）**：把接线拿掉，同一轮就一个字节都落不下来。

    这一条改的**只有一处**：``deployment_sinks`` 交回来的表（其余全部是生产路径：
    同一个入口、同一个 ``tick``、同一张订阅、同一个 ``--at``）。断言与上一条的第一格
    **互为反面**：状态从 ``accepted`` 变成 ``retrying``，收件箱**一行都没有**，盘上的
    收据那一行 ``outcome`` 是 ``unknown`` —— 也就是说这条接线**承重**：没有它，
    「投递成功」在这个部署里没有任何一处落得下来。

    （负控（一）与（二）判的是两件不同的事：（一）说「名字对不上就没有行」，
    （二）说「**表本身**不接线就没有行」。）
    """
    _put(seeded, _definition(seeded))
    _subscribe(seeded)
    monkeypatch.setattr(trigger_driver, "deployment_sinks", lambda settings: {})

    receipt = run_once(seeded, at=T0)

    assert _states(receipt) == ["retrying"], receipt
    outcome = _only_outcome(receipt)
    assert "没有注册 sink='inbox'" in outcome["note"], receipt
    assert _inbox_lines(seeded) == [], "没有接线却在盘上留了收件箱"
    (row,) = _receipt_payloads(seeded)
    assert row["outcome"] == "unknown", row
    assert _run_rows(seeded) == 1

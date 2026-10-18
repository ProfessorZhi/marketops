"""八条工具在**服务面**上的判据，外加「这里没有定时器」那一条。

## 两半，一半今天跑，一半要等接线

``server.py::build_server`` 与 ``tests/mcp_harness.py::drive_with_settings`` 都还没有
``scheduling=`` 这一格（两处都是 MAIN 独占文件，改动走 ``D/REPORT.md`` 里的接线请求
第 1、2 条）。所以这一份分成两半，而**两半都在同一个文件里**：

* **今天跑**：直接构造 ``scheduling_handlers(...)`` 并调用那八条处理器。这一层是
  这一片自己的代码，它要守的东西（八条的名字与目录逐字一致、参数怎么拼成模型、
  domain 的拒绝怎么翻成一条可读回执、回执上**不许出现**没有证据的字段）全部在这里
  就测得到。少了这一半，这一片要等到别人接完线才有第一条判据。
* **接线之后跑**：整条会话穿过真的 ``MCPServer``（令牌、``tools/list``、角色闸门、
  惰性工厂都在那条路上）。它现在 ``skipif``，而 ``skipif`` 的条件是**逐字**那两个
  签名 —— 接线的人不必回来改这个文件，它自己会开始跑。

``test_no_timer_lives_in_the_scheduling_surface`` 不 skip（它是源码扫描）：
``adapters/mcp_server/scheduling.py`` 的模块 docstring 逐字引用了它，而「三套
scheduler 各自跑全量刷新」最可能的形态是某一天顺手在这一层起一个线程 ——
那一次提交看起来完全正常，而且它**不需要任何接线**就会发生。
"""

from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from marketops.adapters.mcp_server.scheduling import scheduling_handlers
from marketops.adapters.mcp_server.server import build_server
from marketops.adapters.memory import RecordingScheduleAction
from marketops.adapters.sqlite.notification import SqliteNotificationStore
from marketops.adapters.sqlite.scheduling import SqliteScheduleStore
from marketops.adapters.sqlite.session import SqliteSessionBindingStore
from marketops.config import Settings
from marketops.domain.scheduling import FireOutcome, ScheduleFire, due_fire_id
from marketops.security.catalog import Role, TOOL_CATALOG, spec_for
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive, drive_with_settings, ring_entry, ring_payload

T0 = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=1)

#: 这一片的那八条，逐字取自 ``catalog.py``。**写一次、引用多处** ——
#: 一条工具被改名时这个文件里所有用例会一起红，而红的话里点名的是它。
SCHEDULING_TOOLS = (
    "put_schedule_definition",
    "list_schedule_definitions",
    "control_schedule",
    "fire_schedule",
    "put_notification_subscription",
    "list_notification_events",
    "list_delivery_receipts",
    "put_session_binding",
)

#: 扫描面：这一层与那个唯一的触发驱动。两处都是「某一天顺手起一个线程」的落点。
NO_TIMER_MODULES = (
    "marketops/adapters/mcp_server/scheduling.py",
    "marketops/application/scheduling.py",
)

#: 起第二个定时器至少要 import 其中一个。``time`` 不在这张单子上 —— 它是
#: ``interfaces/controller.py`` 那个常驻控制器**合法**用的（它自己就是一个循环），
#: 而把 ``time`` 也禁掉会让这一条判据去管别人的一个正确实现。
TIMER_MODULES = frozenset({"threading", "asyncio", "sched", "signal"})


def _accepts(function: object, parameter: str) -> bool:
    return parameter in inspect.signature(function).parameters  # type: ignore[arg-type]


#: 接线现状。两个都真了才跑穿过服务面的那一半 —— 而**两处都要**：只接上
#: ``build_server`` 那个参数、没有 harness 的透传时，这一半会红在一个与判据无关的
#: 地方（``drive_with_settings`` 收到一个它不认识的参数）。
SERVER_WIRED = _accepts(build_server, "scheduling")
HARNESS_WIRED = _accepts(drive_with_settings, "scheduling")
WIRED = SERVER_WIRED and HARNESS_WIRED

requires_wiring = pytest.mark.skipif(
    not WIRED,
    reason=(
        "接线还没做：server.py::build_server 与 mcp_harness.py::drive_with_settings "
        "都还没有 scheduling= 这一格（两处都是 MAIN 独占文件，见 D/REPORT.md 的"
        f"接线请求第 1、2 条）。现状：build_server={SERVER_WIRED}、"
        f"drive_with_settings={HARNESS_WIRED}。接上之后这一条会自动开始跑。"
    ),
)


# ----------------------------------------------------------------------
# 夹具：八条处理器 + 它们 inject 的那三样
# ----------------------------------------------------------------------
@pytest.fixture()
def wired(settings: Settings):
    """与组合根 ``_compose`` 同一接法：三个工厂 + 一个动作。

    工厂是 ``Callable[[], Store]``（``sqlite3`` 的连接不能跨线程），所以这里也接
    工厂，不接实例 —— 接实例的实现会让这一层在真的服务端上第一次并发调用就炸，
    而单线程的判据一次都测不出来。
    """
    schedules = SqliteScheduleStore(settings.database_path)
    notifications = SqliteNotificationStore(settings.database_path)
    bindings = SqliteSessionBindingStore(settings.database_path)
    action = RecordingScheduleAction(next_due_at=LATER, outcome=FireOutcome.RUN_ADMITTED)
    handlers = scheduling_handlers(
        settings=settings,
        schedules=lambda: schedules,
        notifications=lambda: notifications,
        bindings=lambda: bindings,
        action=action,
    )
    return handlers, schedules, notifications, bindings, action


def _handlers(settings: Settings) -> tuple[dict, RecordingScheduleAction]:
    """与组合根 ``_compose`` 同一接法的八条处理器。**只给接线之后那两条用。**

    动作**一并交回**：走服务面那一条要数的还是 ``action.attempts``（一次点火只跑
    一次研究），而穿过 ``MCPServer`` 之后那个数只能从构造它的那一处拿到 ——
    把动作留在函数体里，判据就只剩「调用没报错」。
    """
    schedules = SqliteScheduleStore(settings.database_path)
    notifications = SqliteNotificationStore(settings.database_path)
    bindings = SqliteSessionBindingStore(settings.database_path)
    action = RecordingScheduleAction(next_due_at=LATER)
    return (
        scheduling_handlers(
            settings=settings,
            schedules=lambda: schedules,
            notifications=lambda: notifications,
            bindings=lambda: bindings,
            action=action,
        ),
        action,
    )


def _declare(handlers, settings: Settings, **overrides) -> dict:
    at = T0.isoformat()
    payload = {
        "schedule_id": "sched-0001",
        "schedule_revision": 1,
        "scope_ref": settings.profile,
        "trigger_type": "calendar",
        "timezone": "+08:00",
        "action_spec": {"kind": "research_run", "target_ref": settings.profile},
        "calendar_rule": "daily",
        "next_due_at": T0.isoformat(),
        "at": at,
    }
    payload.update(overrides)
    return handlers["put_schedule_definition"](**payload)


# ----------------------------------------------------------------------
# 一、八条的名字、角色与目录逐字一致
# ----------------------------------------------------------------------
def test_the_factory_returns_exactly_the_eight_catalogued_tools(wired) -> None:
    """返回的键集**恰好**是那八条，一条不多一条不少。

    「多」比「少」更值得一条判据：多出来的一条（比如 ``run_tick``）会被角色闸门
    按目录判一次 —— 而目录里没有它，于是它在任何一个部署上都调不动，看起来像
    「还没实现」，而实际发生的是**这一层多长了一个入口**。少一条则是这一片自己的
    缺口。
    """
    handlers, *_ = wired
    assert set(handlers) == set(SCHEDULING_TOOLS)


def test_every_tool_is_catalogued_as_a_controller_tool_in_phase_k() -> None:
    """八条在目录里都是 ``K`` 相、只给控制器 —— 与 ``spec_for`` 逐字对齐。

    这一条是写死的那张清单与目录之间的夹子：改名或改角色时，两处必须一起动。
    """
    assert set(SCHEDULING_TOOLS) <= set(TOOL_CATALOG)
    for name in SCHEDULING_TOOLS:
        spec = spec_for(name)
        assert spec is not None, f"{name} 不在目录里"
        assert spec.phase == "K", f"{name} 的 phase 是 {spec.phase!r}"
        assert spec.roles == frozenset({Role.CONTROLLER}), f"{name} 的角色是 {spec.roles}"


def test_the_factory_demands_all_four_wiring_slots(settings: Settings) -> None:
    """三个存储工厂与那个动作**都没有缺省值**。

    一个可以为 ``None`` 的接线口会让「没有这个存储」与「这个工作区一条都没有」在
    报告上同形（``scheduling_handlers`` 逐字）。而 ``action`` 的缺省后果最坏：
    一个「什么都不做、回 ``SKIPPED``」的动作会让每一次点火都在清单上显示 ``ok``。
    """
    parameters = inspect.signature(scheduling_handlers).parameters
    assert set(parameters) == {"settings", "schedules", "notifications", "bindings", "action"}
    for name in ("schedules", "notifications", "bindings", "action"):
        assert parameters[name].default is inspect.Parameter.empty, f"{name} 有缺省值"

    # **没有 ``sinks`` 这一格**，而这是一个决定：八条工具里没有一条投递
    # （投递是 ``tick`` 那一半的事，而 ``tick`` 不是工具）。多出这一格的收场有两种，
    # 两种都坏 —— 要么被悄悄忽略，要么诱使某条工具顺手投一次。
    assert "sinks" not in parameters


# ----------------------------------------------------------------------
# 二、这一层的活儿：拼模型、翻拒绝、摊回执
# ----------------------------------------------------------------------
def test_a_declaration_round_trips_and_starts_never_fired(wired, settings: Settings) -> None:
    handlers, *_ = wired
    written = _declare(handlers, settings)
    assert written["schedule_id"] == "sched-0001"
    assert written["workspace"] == settings.profile
    assert written["written"] is True

    listed = handlers["list_schedule_definitions"]()
    assert listed["workspace"] == settings.profile
    assert [row["schedule_id"] for row in listed["schedules"]] == ["sched-0001"]
    # 还没点过火 —— ``resolution`` 那一格如实报 ``never_fired``，而不是留空
    # （留空会让「备好弦的」与「从没点过的」长得一样）。
    assert listed["schedules"][0]["resolution"] == "never_fired"


def test_the_second_first_claim_is_refused_with_a_readable_message(wired, settings: Settings) -> None:
    """再声称一次「这是第 1 版」被拒，而回执里说的是哪一件事。

    ``store_failure`` 把存储层那句话原样带出来 —— 一个把异常吞掉、回一句
    「写失败」的实现让调用方以为是自己参数错了。
    """
    handlers, *_ = wired
    _declare(handlers, settings)
    with pytest.raises(ToolError) as caught:
        _declare(handlers, settings)
    assert "第 1 版" in str(caught.value) or "首次声明" in str(caught.value)


def test_control_accepts_only_the_three_verbs(wired, settings: Settings) -> None:
    handlers, *_ = wired
    _declare(handlers, settings)
    with pytest.raises(ToolError) as caught:
        handlers["control_schedule"](
            schedule_id="sched-0001", schedule_revision=1, control="stop", at=T0.isoformat()
        )
    text = str(caught.value)
    # 拒绝里必须把三个合法动词写出来 —— 「取值只有三个」是调用方要据它改东西的信息。
    assert "enable" in text and "disable" in text and "pause" in text
    assert "stop" in text


def test_control_writes_a_new_revision(wired, settings: Settings) -> None:
    handlers, *_ = wired
    _declare(handlers, settings)
    controlled = handlers["control_schedule"](
        schedule_id="sched-0001",
        schedule_revision=1,
        control="pause",
        reason="先停一停",
        at=T0.isoformat(),
    )
    assert controlled["schedule_revision"] == 2
    assert controlled["enabled"] is False
    # 关掉与暂停都落在 enabled=False 上，区别**只活在 revision_reason 里** —— 而它
    # 由动词起头（``controlled:<verb>[:<reason>]``），于是「谁把它关的」在那一格上
    # 读得出来，而不必去猜。这里连前缀一起钉住：只断 ``"先停一停" in reason`` 的写法
    # 在动词丢了之后照样绿，而那时 pause 与 disable 又分不开了。
    assert controlled["revision_reason"] == "controlled:pause:先停一停"


def test_fire_is_not_due_before_its_moment_and_then_admits(wired, settings: Settings) -> None:
    """没到点 ⇒ ``not_due``，而且**一次动作都没有发生**。

    这一条与竖向那份里的双花判据是同一个否定的服务面形态：调用方拿到的是一个
    明确的「还没到」，而不是一次「成功了但什么都没做」。
    """
    handlers, _, _, _, action = wired
    _declare(handlers, settings, next_due_at=LATER.isoformat())

    early = handlers["fire_schedule"](
        schedule_id="sched-0001", schedule_revision=1, fire_id="manual-1", at=T0.isoformat()
    )
    assert early["status"] == "not_due"
    assert early["fire"] is None
    assert early["replayed"] is False
    assert action.attempts == 0

    # 把到期时刻挪到过去（写第 2 版），同一个 fire_id 现在点得着。
    _declare(handlers, settings, schedule_revision=2, next_due_at=T0.isoformat())
    fired = handlers["fire_schedule"](
        schedule_id="sched-0001", schedule_revision=2, fire_id="manual-1", at=T0.isoformat()
    )
    assert fired["status"] == "admitted"
    assert fired["advanced_to"] == 3
    assert action.attempts == 1
    assert fired["fire"]["outcome"] == "run_admitted"


def test_a_replayed_fire_says_so_and_runs_no_second_action(wired, settings: Settings) -> None:
    """同一个 ``fire_id`` 点第二次：动作还是 1 次，而回执 ``replayed=True``。

    ``replayed`` 与 ``status`` 分开报（``FireReceipt`` 那一节）：合成一格会让
    「重复 fire 幂等」与「这次点火成功了」互相冒充。要走到重放那一支，声明必须
    还停在**原来那一版**且仍然到期 —— 所以这里先声明一条永远不推进的（动作回
    ``next_due_at=None`` 会关掉它，所以用人工重放的路：把声明写回第 1 版不行……
    见下）。
    """
    handlers, schedules, _, _, action = wired
    _declare(handlers, settings, next_due_at=T0.isoformat())
    first = handlers["fire_schedule"](
        schedule_id="sched-0001", schedule_revision=1, fire_id="manual-1", at=T0.isoformat()
    )
    assert first["status"] == "admitted" and first["replayed"] is False
    assert action.attempts == 1

    # 第二次：这一版已经被推进到第 2 版（``advanced_to``），所以同一个 fire_id 在
    # **第 2 版**上是另一次 fire（键的第三段带着版号）—— 那是「改过之后再点」，
    # 不是重放。要读到重放，声明必须停在原版，而那只可能是「收尾没做完」那一支：
    # 直接按端口造一条占位行，再一次 fire 就走到了。
    definition = schedules.get_definition("sched-0001", 2)
    assert definition is not None and definition.next_due_at is not None
    fire_id = due_fire_id(definition)
    assert fire_id is not None
    claim = ScheduleFire(
        schedule_id="sched-0001",
        schedule_revision=2,
        fire_id=fire_id,
        workspace=settings.profile,
        fired_at=definition.next_due_at,
        outcome=FireOutcome.PENDING,
        detail={},
    )
    assert schedules.claim_fire(claim).recorded is True

    replayed = handlers["fire_schedule"](
        schedule_id="sched-0001",
        schedule_revision=2,
        fire_id=fire_id,
        at=definition.next_due_at.isoformat(),
    )
    assert replayed["replayed"] is True
    assert replayed["fire"]["outcome"] == "pending"
    assert action.attempts == 1  # 占位行进来时**不重跑动作**


def test_the_event_list_shows_one_event_per_fire(wired, settings: Settings) -> None:
    handlers, *_ = wired
    _declare(handlers, settings)
    handlers["fire_schedule"](
        schedule_id="sched-0001", schedule_revision=1, fire_id="manual-1", at=T0.isoformat()
    )

    listed = handlers["list_notification_events"]()
    assert listed["workspace"] == settings.profile
    assert listed["count"] == 1
    event = listed["events"][0]
    assert event["kind"] == "schedule_fired"
    assert event["severity"] == "material_change"
    # 事件的范围是**工作区**，不是被看的那个对象（``_event_for`` 逐字）。
    assert event["scope_kind"] == "workspace" and event["scope_ref"] == settings.profile
    # 这里**没有** ``total``：存储那一侧就截断了，编一个会让 ``truncated`` 恒为假。
    assert "total" not in listed


def test_a_bad_severity_is_refused_with_the_five_values(wired, settings: Settings) -> None:
    handlers, *_ = wired
    with pytest.raises(ToolError) as caught:
        handlers["list_notification_events"](severity="urgent")
    assert "urgent" in str(caught.value)


def test_the_receipt_list_reports_only_accepted_or_unknown_and_never_delivered(
    wired, settings: Settings,
) -> None:
    """**诚实性那一条在服务面上的形态：回执里不许出现一个 ``delivered``。**

    ``DeliveryOutcome`` 只有两个成员。一个由收据推出来的「已送达」会让运维据此
    停止重试，而它证明不了 —— 一个没有证据的成功比一句「不知道」贵得多。

    这一条还捎带断言另一半：在这一层上**没有任何东西会写出收据**（八条工具没有一条
    投递，``sinks`` 那一格刻意不存在），所以刚点过火的部署读到的是 0 张收据 ——
    而不是一张写着「投过了」的行。
    """
    handlers, *_ = wired
    _declare(handlers, settings)
    handlers["fire_schedule"](
        schedule_id="sched-0001", schedule_revision=1, fire_id="manual-1", at=T0.isoformat()
    )

    listed = handlers["list_delivery_receipts"]()
    assert listed["count"] == 0 and listed["total"] == 0
    assert listed["truncated"] is False
    assert listed["receipts"] == []
    # 整份回执里没有那个词，也没有任何一格自称投递成功。
    text = json.dumps(listed, ensure_ascii=False)
    assert "delivered" not in text
    assert "accepted" not in text and "unknown" not in text


def test_a_subscription_lands_and_is_read_back_by_the_listing(wired, settings: Settings) -> None:
    """订阅写得进去，而它**不改写任何历史事件**。

    订阅与投递是分开的两张表（``domain/notification.py`` 逐字），所以这一条要同时
    看到两张表各自的读数。
    """
    handlers, *_ = wired
    written = handlers["put_notification_subscription"](
        subscription_id="sub-0001",
        revision=1,
        scope_kind="workspace",
        scope_ref=settings.profile,
        sink="inbox",
        at=T0.isoformat(),
    )
    assert written["subscription_id"] == "sub-0001"
    assert written["workspace"] == settings.profile
    assert written["revision"] == 1

    _declare(handlers, settings)
    handlers["fire_schedule"](
        schedule_id="sched-0001", schedule_revision=1, fire_id="manual-1", at=T0.isoformat()
    )
    assert handlers["list_notification_events"]()["count"] == 1
    assert handlers["put_notification_subscription"](
        subscription_id="sub-0001",
        revision=2,
        scope_kind="workspace",
        scope_ref=settings.profile,
        sink="inbox",
        quiet_hours="22:00-07:00",
        at=T0.isoformat(),
    )["revision"] == 2


def test_a_binding_is_written_and_the_second_call_updates_it_in_place(wired, settings: Settings) -> None:
    """同一个会话写两次 ⇒ **同一行、同一个 ``binding_id``**，而且回执里没有一格权限。

    这条工具在服务面上的第三条否定就在最后那一行：返回值里那几个键是会话四格加
    焦点，**没有** ``roles`` / ``allowed_tools`` 这类东西可以读。
    """
    handlers, _, _, bindings, _ = wired
    first = handlers["put_session_binding"](
        client_kind="claude-code",
        client_session_ref="sess-0001",
        focus_refs=["workstream:ws-1"],
        report_cursor=None,
        expires_at=None,
        at=T0.isoformat(),
    )
    assert first["workspace"] == settings.profile
    assert first["focus_refs"] == ["workstream:ws-1"]

    second = handlers["put_session_binding"](
        client_kind="claude-code",
        client_session_ref="sess-0001",
        focus_refs=["view:sv-2"],
        report_cursor="cursor-9",
        expires_at=None,
        at=(T0 + timedelta(minutes=5)).isoformat(),
    )
    assert second["binding_id"] == first["binding_id"]
    assert second["focus_refs"] == ["view:sv-2"]
    assert len(bindings.bindings(workspace=settings.profile)) == 1

    assert not (
        set(first) & {"roles", "permissions", "allowed_tools", "capabilities", "scope"}
    )


def test_a_binding_without_any_session_identity_is_refused_in_words(wired, settings: Settings) -> None:
    """``client_session_ref`` 与 ``binding_id`` 都没有 ⇒ 拒绝，而且那句话是准的。

    这一层替它编一个 id 的后果，是同一个会话的下一次写入拿到另一个 id —— 于是
    同一行变成两行，而两行都自称是这个会话的焦点。
    """
    handlers, *_ = wired
    with pytest.raises(ToolError) as caught:
        handlers["put_session_binding"](
            client_kind="claude-code",
            focus_refs=["view:sv-1"],
            report_cursor=None,
            expires_at=None,
            at=T0.isoformat(),
        )
    assert "binding_id" in str(caught.value)


def test_another_scene_is_refused(wired, settings: Settings) -> None:
    """``scene_id`` 不是本部署那一个 ⇒ 拒绝。**在拼模型之前。**"""
    handlers, *_ = wired
    for name, call in (
        ("list_schedule_definitions", {}),
        ("list_notification_events", {}),
        ("list_delivery_receipts", {}),
    ):
        with pytest.raises(ToolError) as caught:
            handlers[name](scene_id="some-other-market", **call)
        assert settings.profile in str(caught.value)

    with pytest.raises(ToolError):
        handlers["put_session_binding"](
            client_kind="claude-code",
            client_session_ref="sess-0001",
            focus_refs=[],
            report_cursor=None,
            expires_at=None,
            scene_id="some-other-market",
            at=T0.isoformat(),
        )


# ----------------------------------------------------------------------
# 三、没有定时器
# ----------------------------------------------------------------------
def test_no_timer_lives_in_the_scheduling_surface() -> None:
    """这一层与那个驱动**都不 import 定时器**，也没有一处 ``sleep`` 或 ``while True``。

    这一条的范围刻意只有两个文件：它是这一片的形状，不是对全仓的判决
    （``interfaces/controller.py`` 是一个**合法**的常驻循环，它有 ``time.sleep``）。
    收窄的理由是判据的可辩护性 —— 一条扫全仓的「谁都不许 sleep」会去管别人的
    正确实现，而它变红时读到的人会以为是自己错了。

    ``sched`` / ``signal`` / ``threading`` / ``asyncio`` 四个都是「起第二个定时器」
    至少要用到的一个，而这一层与那个驱动一个都不需要：``tick`` 的时刻是**参数**。
    """
    for relative in NO_TIMER_MODULES:
        source = (Path(relative)).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in TIMER_MODULES, f"{relative} import 了 {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                assert root not in TIMER_MODULES, f"{relative} from {node.module} import ..."
            elif isinstance(node, ast.Name):
                assert node.id != "sleep", f"{relative}:{node.lineno} 用到了 sleep"
            elif isinstance(node, ast.Attribute):
                assert node.attr != "sleep", f"{relative}:{node.lineno} 用到了 .sleep"
            elif isinstance(node, ast.While):
                assert not (
                    isinstance(node.test, ast.Constant) and node.test.value is True
                ), f"{relative}:{node.lineno} 有一个 while True"


def test_the_stdlib_scheduler_is_imported_nowhere_in_the_package() -> None:
    """全仓没有一处 import ``sched``。

    这一条能给到全仓的宽度，理由是它判的东西**没有合法的第二种用法**：
    ``sched`` 是标准库里那个定时器，用它就是要起第二个调度实现。而 ``time``
    不在这一条里（见上一条的说明）。

    扫的是源码（``ast``），不是 grep —— 三个模块的 **docstring** 里逐字写着
    「没有 ``sched``」，一次子串搜索会在那三行上刚好红在我写得最对的地方。
    """
    root = Path(__file__).resolve().parent.parent / "marketops"
    offenders = []
    for path in sorted(root.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path}: {alias.name}"
                    for alias in node.names
                    if alias.name.split(".")[0] == "sched"
                ]
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "sched":
                    offenders.append(f"{path}: from {node.module}")
    assert offenders == [], f"有人起了一个标准库调度器：{offenders}"


def test_the_driver_is_not_a_tool(wired) -> None:
    """``tick`` **不是一条工具**，而且目录里也没有这个名字。

    「先点火，再投递」的那一步是驱动进程调的（``application/scheduling.py::tick``）。
    把它做成一条工具的后果是可预期的：模型会在一次对话里顺手调它 —— 于是投递
    变成一次**工具调用**的副作用，而「一条事件投给 N 个 sink 是 N 张收据」与
    「一次 fire 顺手投了一批」会挤在同一条路径上。
    """
    handlers, *_ = wired
    assert "tick" not in handlers
    assert "run_tick" not in handlers
    assert spec_for("tick") is None
    assert spec_for("run_tick") is None
    assert not [name for name in TOOL_CATALOG if "tick" in name]


# ----------------------------------------------------------------------
# 四、穿过真的服务端（接线之后才跑）
# ----------------------------------------------------------------------
@requires_wiring
def test_the_eight_tools_are_visible_to_a_controller_and_refused_to_a_researcher(
    settings: Settings, tmp_path: Path,
) -> None:
    """``tools/list`` 上有这八条，而一个研究侧 worker 一条都看不见、也调不动。

    「看不见」与「调不动」是两件事、两件都要做（``visible_tools`` 逐字）：只做
    后者时研究侧仍然能列出 control 目录 —— 那既是一次信息泄漏，也是一条把
    「不属于你的工具」与「不存在的工具」区分开的旁路。
    """
    handlers, _ = _handlers(settings)

    token, digest = mint("caller-1", [Role.CONTROLLER])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "caller-1", [Role.CONTROLLER]))),
        encoding="utf-8",
    )
    server = build_server(
        settings=settings, tokens=TokenRing.from_file(ring_path), scheduling=handlers
    )
    outcome = drive(server=server, token=token, want_tool_list=True)
    assert not outcome.handshake_error, outcome.handshake_error
    assert set(SCHEDULING_TOOLS) <= set(outcome.tool_names)

    # 研究侧：八条一条都看不见。
    worker_token, worker_digest = mint("worker-1", [Role.RESEARCHER])
    worker_ring = tmp_path / "worker-tokens.json"
    worker_ring.write_text(
        json.dumps(ring_payload(ring_entry(worker_digest, "worker-1", [Role.RESEARCHER]))),
        encoding="utf-8",
    )
    worker_server = build_server(
        settings=settings, tokens=TokenRing.from_file(worker_ring), scheduling=handlers
    )
    worker_outcome = drive(server=worker_server, token=worker_token, want_tool_list=True)
    assert not (set(SCHEDULING_TOOLS) & set(worker_outcome.tool_names)), (
        "研究身份看得见这一族的某几条 —— 而这一族的每一条都能让这个部署开出一轮研究"
    )


@requires_wiring
def test_the_eight_tools_run_over_a_real_session(settings: Settings, tmp_path: Path) -> None:
    """整条链过一次真的会话：写声明 → 点火 → 事件 → 订阅 → 绑定。

    这一条与上面那些直接调处理器的判据**不是重复**：它多出来的那几样（角色闸门、
    ``tools/list``、参数按 JSON 往返一次、惰性工厂在**线程池**里被调用）只有走
    真的 ``MCPServer`` 才过得到 —— 而「处理器全是 ``def``、每一条开一次工厂」这条
    纪律的失败形态，恰好只在另一个线程上现形。
    """
    handlers, action = _handlers(settings)
    token, digest = mint("caller-1", [Role.CONTROLLER])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "caller-1", [Role.CONTROLLER]))),
        encoding="utf-8",
    )

    calls = (
        (
            "put_schedule_definition",
            {
                "schedule_id": "sched-0001",
                "schedule_revision": 1,
                "scope_ref": settings.profile,
                "trigger_type": "calendar",
                "timezone": "+08:00",
                "action_spec": {"kind": "research_run", "target_ref": settings.profile},
                "calendar_rule": "daily",
                "next_due_at": T0.isoformat(),
                "at": T0.isoformat(),
            },
        ),
        ("list_schedule_definitions", {}),
        (
            "fire_schedule",
            {
                "schedule_id": "sched-0001",
                "schedule_revision": 1,
                "fire_id": "manual-1",
                "at": T0.isoformat(),
            },
        ),
        ("list_notification_events", {}),
        ("list_delivery_receipts", {}),
        (
            "put_session_binding",
            {
                "client_kind": "claude-code",
                "client_session_ref": "sess-0001",
                "focus_refs": ["workstream:ws-1"],
                "report_cursor": None,
                "expires_at": None,
                "at": T0.isoformat(),
            },
        ),
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        scheduling=handlers,
        calls=calls,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    for call in outcome.calls:
        assert not call.is_error, f"{call.name} 失败了：{call.text}"

    assert outcome.calls[1].as_json()["schedules"][0]["resolution"] == "never_fired"
    assert outcome.calls[2].as_json()["status"] == "admitted"
    assert outcome.calls[3].as_json()["count"] == 1
    assert outcome.calls[4].as_json()["receipts"] == []
    assert outcome.calls[5].as_json()["focus_refs"] == ["workstream:ws-1"]
    assert action.attempts == 1

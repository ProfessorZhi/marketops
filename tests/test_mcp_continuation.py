"""§B21 的续跑状态页在**服务面**上的判据。

## 这一条工具为什么要单独一个文件

``tests/test_resident_controller.py`` 已经把「判词怎么算出来的」钉了 16 条 —— 七种
状态、六个计数、每一类的处置。那些判据全部直接调 ``survey`` / ``ContinuationController``，
**一次都没有经过服务面**。而这一条工具的三件事都只在这条路上存在：

1. **它注册不注册**（没有落点的部署里，它必须落回 ``unbacked_message``，而不是回一句
   「没有工作在跑」—— 后者与一个真的什么都不欠的市场在报告上同形）。
2. **`driver` 那一格**：判词答「为什么此刻没有更多工作在跑」，而归属那一行答「现在
   有没有人在推」。**判词逐字相同、处置相反**（去启动一个控制器 / 什么都不用做），
   所以这一格是这一条工具存在的第二个理由，而它在应用层没有落点。
3. **它只读。** 一次查询认领了归属、或者碰了心跳，是一个不会报错、只会在别的控制器
   那边表现为「我怎么被顶掉了」的缺陷。

## 「读一次不建库」为什么是这一条最贵的一条判据

三个存储的构造函数都会 ``ensure_schema``。一条只读工具在一个还没写过任何东西的部署
上顺手建出一个空库，后果不是多一个文件：此后「还没有产物」与「这里本该有东西」在
磁盘上再也分不开，而这一页的全部意义就是让人在什么都没发生的时候问一句为什么。
所以这一条必须在**真的走一遍服务面**的地方断言 —— 直接调函数不会经过 ``_compose``
的惰性工厂，也就测不出「工厂接错了会怎样」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.continuation import continuation_handlers
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.adapters.sqlite.wakeup import SqliteWakeupRegistry
from marketops.config import Settings
from marketops.domain.enums import JobInputKind
from marketops.domain.util import sha256_text, utc_now
from marketops.interfaces.composition import (
    default_scene_store,
    default_wakeup_registry,
)
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

#: 这一条工具的名字。写一次、引用四处 —— 与目录里那一条对不上时，这个文件里所有
#: 用例会一起红，而红的话里点名的是它，不是某一条断言。
TOOL = "get_continuation_status"

#: 一个控制器的稳定标识。它出现在归属行里，所以它必须与 ``settings.profile`` 一样
#: 是个**具体值** —— 「随便谁」在这条工具上不是一种持有者。
HOLDER = "resident-1"


def _jobs(settings: Settings) -> Any:
    """作业记录的工厂。与组合根 ``_compose`` 里那一行同形。"""
    return lambda: SqliteJobStore(settings.database_path)


def _owner_store(settings: Settings) -> SqliteWakeupRegistry:
    """**盘上那一行**的写入口 —— 按路径直接构造，不走夹具的工厂。

    这一处刻意不复用 ``wired.registry``：那个工厂是被测对象的一部分，用它来写
    归属会让「这个工厂指向另一个库」这件事在回执上消失（写进去与读出来都是那一个
    错的库，两边一致，于是判据全绿）。控制器（``interfaces/controller.py``）与
    ``_compose`` 都是在这一个 ``settings.database_path`` 上开存储的，所以这里也
    只按这个路径开 —— 这一条工具读得到控制器写下的那一行，才是这一格的判据。
    """
    return SqliteWakeupRegistry(settings.database_path)


@dataclass(frozen=True)
class _Wired:
    """一条工具加它读的三样东西，与 ``_compose`` 同一接法。

    三个工厂在这里只建一次，与组合根逐字同款：分开建的话，「这一页读的场景」与
    「控制器读的那个」会是两份，而两边各自都正常。
    """

    handlers: dict[str, Any]
    jobs: Any
    registry: Any
    scenes: Any


@pytest.fixture
def wired(settings: Settings) -> _Wired:
    jobs = _jobs(settings)
    registry = default_wakeup_registry(settings)
    scenes = default_scene_store(settings)
    return _Wired(
        handlers=continuation_handlers(
            settings=settings, jobs=jobs, registry=registry, scenes=scenes
        ),
        jobs=jobs,
        registry=registry,
        scenes=scenes,
    )


def _drive(
    settings: Settings,
    tmp_path: Path,
    calls: list[tuple[str, dict[str, Any]]],
    *,
    handlers: dict[str, Any] | None = None,
    roles: list[Role] | None = None,
    want_tool_list: bool = False,
):
    """起一个真的服务端，用给定角色的令牌调这几条工具。

    与 ``tests/test_mcp_product_loop.py::_ring`` 同形（摘要与令牌必须配成一对，
    配错了会话在握手期就失败），但那一份是那个文件的夹具，不跨文件复用 ——
    跨文件复用它的后果是某个文件为了另一处的需要改一个共享的 helper。
    """
    token, digest = mint("caller-1", roles or [Role.CONTROLLER])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "caller-1", roles or [Role.CONTROLLER]))),
        encoding="utf-8",
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        continuation=handlers,
        calls=tuple(calls),
        want_tool_list=want_tool_list,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    return outcome


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    """第 ``index`` 次调用必须成功，并返回它的结构化结果。

    失败时把工具自己那句话带进断言消息 —— 一个只报 ``is_error is True`` 的用例
    在排查时要先手工重跑一遍才知道它到底说了什么。
    """
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = 0) -> str:
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却回了：{call.text}"
    return call.text


def _seed_open_work(settings: Settings) -> None:
    """在本部署那个场景下留下一件**未结**的续跑意图，以及它所属的场景记录。

    两步都是夹具在**盘上写行**，不是走工具：这一条判据要问的是「这一页读的是不是
    本部署那个场景」，而用工具去建场景会让这个文件同时测两条工具（``create_scene``
    与 ``submit_research``）—— 那两条各自有各自的家，红了之后也说不清是哪一条断了。

    第二步不能省：``_read_status`` 先按工作区取出**场景记录**，那个键集就是「这一轮
    要看哪些场景」。没有记录时一行意图都读不到，而读数与「一件都不欠」逐字相同。
    这是本仓刻意的语义（没有立项过的场景不是本部署的世界），这里把它当**前置条件**
    写出来，而不是绕过它。
    """
    from marketops.domain.scene import SceneRecord, SceneState

    now = utc_now()
    store = SqliteSceneStore(settings.database_path)
    store.put_record(
        SceneRecord(
            scene_id=settings.profile,
            workspace=settings.profile,
            state=SceneState.ACTIVE,
            latest_version=1,
            effective_version=1,
            created_at=now,
            updated_at=now,
        ),
        expected_revision=None,
    )
    # 受理一个作业 ⇒ 同一笔写事务里欠下「把这批件交给执行器」这一行
    # （``domain/continuation.py::intents_for_admission``）。此后没有任何执行器跑过，
    # 所以那一行还挂着 —— 这正是「有工作、但此刻没在跑」的形状。
    SqliteJobStore(settings.database_path).admit(
        scene_id=settings.profile,
        input_kind=JobInputKind.DISCOVERY_SCOPE,
        input_ref="devon-2026-09",
        idempotency_key="page-fixture-1",
        request_sha256=sha256_text("page-fixture-1"),
    )


def test_the_page_reads_this_deployments_own_scene(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """判词与计数来自**本部署那个场景**，不是别处，也不是「反正一件都不欠」。

    这一条挡的是 ``workspace=`` 那一格被写错（或者被写成调用方能给的一个参数）。
    写错的后果很安静：一个**真的有欠账**的市场，在这一页上被报成 ``no_work``——
    而那正是运维据此决定「什么都不用做」的那一句。所以这一条必须有一个**非空**的
    库：本文件里其余每一条的判词都是 ``no_work``，而没有这一条时，把工作区那一格
    改成任何一个不存在的名字都不会有判据变红。
    """
    _seed_open_work(settings)

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    assert payload["store_state"] == "present", payload
    assert payload["status"]["workspace"] == settings.profile, payload["status"]
    # 一件到期、没人挡着、也没人做的件 ⇒ ``waiting_due``（``due_now`` 数为 1）。
    # 这不是随手挑的一个判词：它**正是**「等着有人来推」的那一格，而这一页另一半
    # 那句 ``driver`` 说的就是「有没有人来推」—— 两句合起来才读得出「等得到、
    # 但没人来」。一个把工作区写错的实现在这里会答 ``no_work``：盘上那件欠账
    # 在报告里消失，而运维据此决定什么都不做。
    assert payload["status"]["state"] == "waiting_due", (
        f"盘上有一件到期的未结意图，这一页却说别的：{payload['status']}"
    )
    assert payload["status"]["inputs"]["due_now"] == 1, payload["status"]
    assert payload["status"]["inputs"]["open_total"] == 1, payload["status"]
    assert payload["status"]["inputs"]["in_flight"] == 0, payload["status"]
    # 没有人推过这一件 —— 判词相同的那另一半在这里是空的。
    assert payload["driver"] is None, payload


def test_any_one_of_the_three_factories_missing_un_registers_the_tool(
    settings: Settings,
) -> None:
    """三个工厂缺任何一个 ⇒ **这一条不注册**，而不是注册成恒回「没有工作」的替身。

    这一条在服务面上**测不出来**：``build_server`` 把 ``{}`` 与 ``None`` 一视同仁
    （都落回 stub），所以「工厂返回空表」与「调用方没传」在回执上同形。于是判据只能
    落在工厂这一层 —— 而它必须有人守：把 ``return {}`` 改成「返回一个恒回 ``no_work``
    的实现」不会有任何测试变红，只会让一个没接线的部署看起来像一个健康地欠着零件
    工作的市场。

    三个 ``None`` 逐个摆，不是挑一个：三个工厂是三条可以各自缺席的接线，而
    「``jobs`` 缺了会怎样」推不出「``registry`` 缺了会怎样」。
    """
    jobs = _jobs(settings)
    registry = default_wakeup_registry(settings)
    scenes = default_scene_store(settings)

    for missing in ("jobs", "registry", "scenes"):
        given: dict[str, Any] = {"jobs": jobs, "registry": registry, "scenes": scenes}
        given[missing] = None
        assert continuation_handlers(settings=settings, **given) == {}, (
            f"缺 {missing} 时这一条仍然注册了 —— 缺的那一格会变成一句编出来的结论"
        )

    complete = continuation_handlers(
        settings=settings, jobs=jobs, registry=registry, scenes=scenes
    )
    assert set(complete) == {TOOL}, complete


def test_a_deployment_without_the_wiring_says_so_instead_of_reporting_no_work(
    settings: Settings, tmp_path: Path
) -> None:
    """没接线的实例必须报「尚未接线」，**不是**报一句「没有工作在跑」。

    这两句话的处置相反：前者是去把 ``continuation_handlers`` 接上，后者是「什么都不
    用做」。而它们在报告上长得一样 —— 一条恒回 ``no_work`` 的替身会让一个没接线的
    部署看起来像一个健康地欠着零件工作的市场。

    同时钉住另一半：这条工具**仍在该实例的 ``tools/list`` 上**。目录是目录，注册是
    注册 —— 与 ``review`` / ``outreach`` 那几条逐字同一条区分。
    """
    outcome = _drive(
        settings, tmp_path, [(TOOL, {})], handlers=None, want_tool_list=True
    )
    text = _refused(outcome)
    assert "尚未接线" in text, text
    assert "no_work" not in text, "没接线被答成了业务结论"
    assert "没有工作在跑" not in text, "没接线被答成了业务结论"
    assert TOOL in outcome.tool_names, (
        "目录里的工具应当在 tools/list 上 —— 不在的话这一条用例测的就不是"
        "「没接线」，而是「不在这个构建里」"
    )


def test_a_deployment_that_never_wrote_anything_reports_no_store_and_leaves_none_behind(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """还什么都没写过 ⇒ ``no_store``，**且这一次读不建库**。

    后半句是这一条的全部重量。三个存储的构造函数都会 ``ensure_schema``，所以一次
    查询顺手建出一个空库是**默认行为**，不是需要写错才会发生的事 —— 挡住它的只有
    ``continuation_handlers`` 里那道 ``database_path.is_file()``。
    """
    assert not settings.database_path.is_file(), (
        "前置条件：这个部署还什么都没写过（夹具把运行目录建出来了，但没有建库）"
    )

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    assert payload["store_state"] == "no_store", payload
    assert payload["workspace"] == settings.profile, payload
    # 两格都必须是 ``None`` 而不是一个空对象：一个 ``status: {...}`` 的空判词会被
    # 读成「算过了，什么都不欠」。
    assert payload["status"] is None, payload
    assert payload["driver"] is None, payload
    assert "不是" in payload["note"], (
        "这一句必须把「还没有产物」与「没有工作在跑」分开说 —— 读者要据此决定"
        "是去受理一次作业还是什么都不做"
    )

    assert not settings.database_path.is_file(), (
        "一次只读查询把库建出来了：此后「还没有产物」与「这里本该有东西」在磁盘上"
        "再也分不开，而这一页存在的全部意义就是在什么都没发生时问一句为什么"
    )


def test_the_same_verdict_with_and_without_a_driver_is_told_apart(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**判词逐字相同、处置相反** —— 这一条是 ``driver`` 那一格存在的理由。

    库里一行未结意图都没有 ⇒ ``no_work``。这与有没有控制器在推**无关**：没有持有者
    时要去启动一个，有持有者时什么都不用做。判词看不出这一格，所以它必须在回执里
    单列，且两半要能同时在一次回执里读到。
    """
    # 建库（并且只建库）：一行作业都没有。
    SqliteJobStore(settings.database_path)

    before = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))
    assert before["store_state"] == "present", before
    assert before["status"]["state"] == "no_work", before
    assert before["status"]["inputs"]["open_total"] == 0, before
    assert before["driver"] is None, (
        "还没有人认领过归属，这一格必须报「没有人」，不能报一个空的持有者对象"
    )

    now = utc_now()
    owner = _owner_store(settings).acquire(settings.profile, holder=HOLDER, now=now)
    assert owner is not None and owner.epoch == 1, owner

    after = _ok(
        _drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers), 0
    )
    assert after["status"]["state"] == before["status"]["state"], (
        "归属那一行不是工作 —— 有人认领了归属不该改变判词，否则这一格就不是"
        "「判词之外的另一件事」"
    )
    assert after["driver"] is not None, after
    assert after["driver"]["holder"] == HOLDER, after
    assert after["driver"]["epoch"] == 1, after
    assert after["driver"]["stale"] is False, after


def test_a_holder_that_stopped_heartbeating_is_named_and_marked_stale(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """心跳过期 ≠ 没有人持有。

    两件事的处置不同：「这一代卡住了，等它过期」与「没有控制器，去启动一个」。
    合成一格之后，运维会把一个卡死的控制器读成「本来就没接」，然后去启动第二个。
    """
    SqliteJobStore(settings.database_path)
    # 十分钟前认领、此后没再跳过心跳（``OWNER_STALE_AFTER`` 是 90 秒）。
    stale_at = utc_now() - timedelta(minutes=10)
    assert (
        _owner_store(settings).acquire(settings.profile, holder=HOLDER, now=stale_at)
        is not None
    )

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    assert payload["driver"] is not None, (
        "过期的持有者仍然是一个持有者 —— 报成「没有人」会让运维去启动第二个控制器"
    )
    assert payload["driver"]["holder"] == HOLDER, payload
    assert payload["driver"]["stale"] is True, payload
    assert payload["driver"]["heartbeat_at"].startswith(
        stale_at.replace(microsecond=0).isoformat()[:16]
    ), payload


def test_reading_the_page_claims_nothing_and_heartbeats_nothing(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """这一页**只读**：不认领归属、不碰心跳、不写任何行。

    认领是一次 CAS（``acquire`` 成功会把 ``epoch`` 加一），心跳会把 ``heartbeat_at``
    往前推。两件事都不会报错 —— 它们只会在**别的控制器**那边表现为「我怎么被顶掉了」
    或者「一个早就死掉的进程看起来还活着」。所以这里读的是那两格本身，而不是
    「回执看起来正常」。
    """
    SqliteJobStore(settings.database_path)
    claimed_at = utc_now() - timedelta(seconds=5)
    acquired = _owner_store(settings).acquire(
        settings.profile, holder=HOLDER, now=claimed_at
    )
    assert acquired is not None, acquired

    for index in range(2):
        payload = _ok(
            _drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers), 0
        )
        assert payload["driver"]["epoch"] == acquired.epoch, (
            f"第 {index + 1} 次读把归属重新认领了一次（epoch 变了）"
        )
        assert payload["driver"]["heartbeat_at"] == acquired.heartbeat_at.isoformat(), (
            f"第 {index + 1} 次读碰了心跳 —— 一个卡住的进程会因此看起来还活着"
        )

    still = _owner_store(settings).current(settings.profile)
    assert still is not None, "这一页把归属行删掉了"
    assert (still.holder, still.epoch, still.heartbeat_at) == (
        acquired.holder,
        acquired.epoch,
        acquired.heartbeat_at,
    ), still


def test_a_research_identity_cannot_read_the_control_page(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """研究身份读不到这一页：里面住着主体与业务键（控制面的事实）。

    这一条**必须接线之后才作数** —— 没接线时它虽然照旧不可见，但不可见的理由变成了
    「没接线」，而那条路径与「角色不对」是两件事。所以这里传的是**接好的**处理器表。

    顺带钉住同形：不属于你的工具与不存在的工具，答案里只有工具名不一样。
    """
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, {}), ("definitely_not_a_tool", {})],
        handlers=wired.handlers,
        roles=[Role.RESEARCHER],
    )
    denied = _refused(outcome, 0)
    assert "尚未接线" not in denied, (
        "接好了却报「尚未接线」—— 那说明被拒的理由不是角色，而这一条用例就不是"
        "在测权限了"
    )
    unknown = _refused(outcome, 1)
    assert denied.replace(TOOL, "") == unknown.replace("definitely_not_a_tool", ""), (
        f"两条拒绝的措辞不同：\n不属于你的：{denied}\n不存在的：{unknown}"
    )

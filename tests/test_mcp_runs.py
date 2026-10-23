"""§B17 的 Run 面在**服务面**上的判据 —— 受理面（``submit_run``）、读回面（``get_run``）
与结束面（``close_run``）。

## 这两条工具为什么单独一个文件

``tests/test_run_admission.py`` 已经把 ``admit_run`` 的十三件事钉住了，而那些判据
**一次都没有经过服务面**：它们直接构造一份 ``SceneVersion`` 与一份 ``ResearchContext``
再调用例层。服务面上多出来的东西恰好是这一层最容易出错的那几样：

1. **受理那一行真的落盘了没有。** 用例层返回一个 ``Run`` 对象，而「它写进去了」
   只能由另一个连接读回来才作数 —— 所以这里的判据全部在写完之后**另开一个
   ``SqliteRunStore``** 读那一行，而不是拿处理器返回的对象再看一遍。
2. **拒绝是哪一种拒绝。** 一个从出生就过期的 Run、一个今天受理不了的自动模式、
   一份硬歧义说不清的上下文 —— 三件事都由 ``ports/runs.py`` 或 ``preview_plan``
   逐字说过，而服务面要做的只是把那些话**原样**交出去。丢掉它们之后调用方读到的
   是一句光秃秃的 ``Error executing tool submit_run``。
3. **计划是服务端现编的。** 客户端交上来的是一份声明，而 ``admit_run`` 要求计划与
   上下文**对得上**（``plan.fingerprint_digest`` 必须等于 ``fingerprint_of(context)``）。
   这一步在服务面上只有一种诚实做法：现编一份。
4. **它是控制者工具。** Run 圈的是工作区与信封，研究身份读得到它就等于看得见
   **别的账号**这一轮花了多少、要跑多久。

## 一次拒绝里「什么都没写」是这一族最便宜也最要紧的一条

四五个用例都断言了 ``_rows(...) == 0``。少了它，「拒绝」与「先受理再报错」在
回执上完全一样，而后者会在盘上留下一个没有人打算要的 Run —— 它随后会出现在
``get_run`` 的读数里，而那一行说自己是被受理的。

## 读回面是同文件的第 7 节

``get_run`` 与受理面共用同一个处理器表（``run_handlers`` 一次返回三条），而它答的是
另一半：受理面答「这一行**有没有被写下来**」，读回面答「写下来的这一行**现在是什么
样**」。两者共用的那一半是 ``_projection``，所以第 7 节的第一条判据就是钉住那两份
投影不漂 —— 而它同时判「读的是**盘上**那一行」：读回面另起一次 ``_drive``（新服务端、
新连接、同一个库），与第 1 节同一个理由。

## 结束面是同文件的第 9 节

``close_run`` 答的是第三个问题：这一轮**可以**结束吗、而它确实结束了吗。它是 §B17
条件 2 那两半在服务面上的落点（完整 Delta + 业务键双向对账），也是**折入账**这一侧
唯一的**控制面** —— 账由 ``promote_receipt`` 的 ``run_id=`` 记下（e3），由
``get_run.folds`` 读回，由这一条工具拿它核对调用方的主张。

它在服务面上比用例层多出来的一件事，正是这一节里最要紧的判据：**账读不出来时它
不许按零折入读完**。那一读会让「折了却没记」在账上长成「什么都没折」，而池子已经
变大了 —— 用例层判的是那个函数，服务面判的是它**有没有把话原样交出去**。

## preview 面是同文件的第 12 节

``preview_run`` 与 ``submit_run`` 描述的是**同一轮**，所以第 12 节判的不是「preview
这一份读数对不对」（那一份判据在 ``tests/test_run_preview.py``：同一个生产者造的
规格、五条不变量、四步的次序），而是**两条工具说的是不是同一件事** —— 同一个键、
同一个信封、同一份计划、同一本观测账。两份各自自洽、互相说不通的实现照样过得了
库层那一条，因为库层只有一份规格可看。

它同时钉住 preview **一行都不写**，而钉法是次序：那一条判据先 preview、再 submit，
所以「preview 读到的 ``existing`` 是空的」这句话是真的判出来的，不是从别处推的。
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

#: 服务面把「调用方写的那份窗口」拼成链域对象的那一处。别名是因为本文件自己有一个
#: ``_window``（调用方写的那份**声明**）—— 两者名字撞得起是因为它们说的正是这枚硬币
#: 的两面，而混用会让「这个值是交上去的还是解析出来的」读不出来。
from marketops.adapters.mcp_server.runs import _claim as _parse_claim
from marketops.adapters.mcp_server.runs import _delta as _parse_delta
from marketops.adapters.mcp_server.runs import _window as _parse_window
from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.adapters.mcp_server.runs import run_handlers
from marketops.adapters.profile_loader import load_profile
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.adapters.sqlite.runs import SqliteRunStore
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.application.plan_preview import PreviewOutcome, preview_plan
from marketops.application.run_increment import IncrementVerdict
from marketops.config import Settings
from marketops.domain.entities import VenueAsset
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.fold import FoldAttribution
from marketops.domain.maintenance import (
    ALL_ASPECTS,
    Aspect,
    ChangeSignal,
    Observation,
    RefreshPlan,
)
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.research_context import ResearchContext
from marketops.domain.run import run_id
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta
from marketops.domain.scene import (
    PauseReason,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.util import utc_now
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_action_item_store,
    default_asset_store,
    default_maintenance_store,
    default_relation_store,
    default_run_store,
    default_scene_store,
)
from marketops.ports.runs import unknown_run_refusal
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

#: 受理那一条工具的名字。写一次、引用多处 —— 与目录里那一条对不上时，这个文件里所有
#: 用例会一起红，而红的话里点名的是它。读回那一条是 :data:`READ`（第 7 节）。
TOOL = "submit_run"

KEY = "run-2026-09-28-a"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)

#: 截止时刻与「已经过去」的那个时刻都**从当下算**，不写死。
#:
#: ``submit_run`` 的受理时刻由服务端取（``domain.util.utc_now``，照
#: ``product_loop._create_scene`` 的先例：「时刻由服务端取，不接受调用方给一个
#: 时刻」），所以这两个值必须相对真实时钟。写死一个「未来」的时刻是一颗定时炸弹：
#: 它过了那一天之后恒判「截止不在将来」，而那看起来像受理坏了。
CUTOFF = utc_now() + timedelta(days=1)
PAST = utc_now() - timedelta(days=1)

#: 一份说得清楚的声明。逐字取自 ``tests/test_run_admission.py::TASK`` 的形状 ——
#: 两份都走**真实的** ``ResearchContext`` 校验器，所以这里不能随手编一个字典。
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


# --- 夹具 ---------------------------------------------------------------------


def _scene() -> SceneVersion:
    return SceneVersion(
        scene_id="uk-acme-natural-grass",
        workspace="uk-acme-natural-grass",
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
        profile_id="uk-acme-natural-grass",
        profile_version="2026-09-01",
        created_at=T0,
        supersedes=None,
    )


def _seed_scene(
    settings: Settings,
    *,
    state: SceneState = SceneState.ACTIVE,
    pause_reason: PauseReason | None = None,
    version_on_disk: bool = True,
) -> None:
    """在盘上留一份 Scene 记录，以及（可选的）它那一版规则。

    两步都是夹具**直接写行**，不经过工具：这一族要问的是「受理读的是不是盘上那一
    份」，而用 ``create_scene`` 去建它会让这个文件同时测另一条工具，红了之后也说不清
    是哪一条断了。``test_mcp_continuation.py::_seed_open_work`` 是同一个形状。

    ``version_on_disk=False`` 造的是**第二种**缺席：记录说「生效的是第 1 版」，而
    库里那一版读不出来。它不是「还没开张」，是存储不自洽 —— 两句话的处置相反。

    ``pause_reason`` 与 ``state=PAUSED`` 是**一对**，缺了它域层会拒（逐字：「缺理由
    的暂停与「有人点了一下」在报告上无法区分，而 operator_pause 要自己 resume、
    policy_hold 要等策略失效」）。夹具照说那句话，不绕过它。
    """
    store = SqliteSceneStore(settings.database_path)
    store.put_record(
        SceneRecord(
            scene_id=settings.profile,
            workspace=settings.profile,
            state=state,
            latest_version=1,
            effective_version=None if state is SceneState.DRAFT else 1,
            created_at=T0,
            updated_at=T0,
            pause_reason=pause_reason,
        ),
        expected_revision=None,
    )
    if version_on_disk:
        store.put_version(_scene().model_copy(update={"scene_id": settings.profile}))


def _rows(settings: Settings) -> int:
    """盘上**真的**有几行 Run。数的是库，不是处理器返回的对象。

    这是这一族最要紧的那个量具：「拒绝了」与「先受理再报错」在回执上一模一样，
    而后者在盘上留下一行没有人打算要的 Run。
    """
    with sqlite3.connect(settings.database_path) as conn:
        found = conn.execute(
            "SELECT COUNT(*) FROM research_run WHERE workspace = ?", (settings.profile,)
        ).fetchone()
    return int(found[0])


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "context": dict(CONTEXT),
        "budget": {"search": 20},
        "cutoff": CUTOFF.isoformat(),
        "idempotency_key": KEY,
    }
    body.update(overrides)
    return body


@dataclass(frozen=True)
class _Wired:
    """这一族工具加它们读的五样东西，与 ``_compose`` 同一接法。

    五个工厂在这里各建**一次**，与组合根逐字同款：Run 的 ``workspace`` 与
    ``selector`` 是从 Scene 取来的，两处各建一份时那一行会指向一个读不到的场景，
    而两边各自都正常；维护账那一份的形态同款 —— 增量面读的观测必须是产品环那三条
    维护工具写下去的同一本账（见 ``__main__.py`` 里 ``maintenance=`` 那一段）；
    账本那一份说的是另一件事（条件 4 的累计预算要读**扣款的那一本**），
    而它的形状与上面三样同款：**同一个 ``database_path`` 上的一本**。池子那一份是
    e4 多出来的第五样（``close_run`` 按业务键逐条问「这一行在不在」），而同一条
    纪律照旧：池子那一行与折入账是**同一次写**，所以它们必须落在同一个库上。
    """

    handlers: dict[str, Any]
    scenes: Any
    maintenance: Any
    runs: Any
    ledger: Any
    assets: Any


@pytest.fixture
def wired(settings: Settings) -> _Wired:
    scenes = default_scene_store(settings)
    maintenance = default_maintenance_store(settings)
    runs = default_run_store(settings)
    ledger = partial(SqliteBudgetLedger, settings.database_path)
    assets = default_asset_store(settings)
    action_items = default_action_item_store(settings)
    return _Wired(
        handlers=run_handlers(
            settings=settings,
            scenes=scenes,
            maintenance=maintenance,
            runs=runs,
            ledger=ledger,
            assets=assets,
            # 跟进池也照组合根那一份：同一个 ``Settings.database_path`` 上的
            # ``default_action_item_store(settings)``，``close_run`` 用得到。
            followups=action_items,
            # 切片 f 的两条。给**真的**工厂，不给 ``None``：``wired`` 说的是「组合根
            # 那一份接线，逐字同款」，而组合根两样都接得上（``__main__.py`` 里
            # 产品环那三处用的是同一对闭包）。
            accounts=default_account_pool_store(settings),
            cohorts=default_account_cohort_store(settings),
        ),
        scenes=scenes,
        maintenance=maintenance,
        runs=runs,
        ledger=ledger,
        assets=assets,
    )


def _drive(
    settings: Settings,
    tmp_path: Path,
    calls: list[tuple[str, dict[str, Any]]],
    *,
    handlers: Any = None,
    scenes: Any = None,
    roles: list[Role] | None = None,
    want_tool_list: bool = False,
):
    """起一个真的服务端，用给定角色的令牌调这几条工具。

    与 ``tests/test_mcp_continuation.py::_drive`` 同形（摘要与令牌必须配成一对，
    配错了会话在握手期就失败），但那一份是那个文件的夹具，不跨文件复用 —— 跨文件
    复用它的后果是某个文件为了另一处的需要改一个共享的 helper。
    """
    used = roles or [Role.CONTROLLER]
    token, digest = mint("caller-1", used)
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "caller-1", used))),
        encoding="utf-8",
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        scenes=scenes,
        runs=handlers,
        calls=tuple(calls),
        want_tool_list=want_tool_list,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    return outcome


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _denied(outcome, index: int = 0) -> str:
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却成功了：{call.text}"
    return call.text


# --- 1 · 受理真的落了一行 ------------------------------------------------------


def test_submit_run_admits_one_increment_and_the_row_is_on_disk(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """成功路径：回执说的那几格与**盘上那一行**逐格相同。

    逐格比而不是只比 ``run_id``：``workspace`` 与 ``selector`` 取自盘上的 Scene
    （受理面 docstring 里那条「不能由调用方给」），``objective`` 取自上下文，
    三个摘要各由它们自己的生产者给出。任何一格改成「调用方顺手给的」，这里都会红。
    """
    _seed_scene(settings)
    outcome = _drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers)
    receipt = _ok(outcome)

    assert receipt["run_id"] == run_id(settings.profile, KEY)
    assert receipt["workspace"] == settings.profile
    assert receipt["selector"] == settings.profile
    assert receipt["objective"] == CONTEXT["objective"]
    assert receipt["idempotency_key"] == KEY
    assert receipt["execution_mode"] == "manual"
    assert receipt["budget"] == [{"line": "search", "ceiling": 20}]
    assert receipt["cutoff"] == CUTOFF.isoformat()
    # 策略版本答「这一轮读的是哪几份策略」，空的那一份读起来是「没有策略变更」。
    assert receipt["policy_versions"], "policy_versions 不得为空"
    assert all("@" in version for version in receipt["policy_versions"])

    # **另开一个连接**读回来 —— 拿处理器返回的对象再看一遍证明不了写盘。
    stored = SqliteRunStore(settings.database_path).get(receipt["run_id"])
    assert stored is not None, "受理成功了，而盘上读不到那一行"
    assert stored.spec.workspace == settings.profile
    assert stored.spec.selector == settings.profile
    assert stored.spec.objective == CONTEXT["objective"]
    assert stored.spec.idempotency_key == KEY
    assert stored.spec_sha256 == receipt["spec_sha256"]
    # ``Run.created_at`` 是**字符串**（ISO 8601），不是 ``datetime`` —— 存储层原样
    # 吐回它收到的那一份，所以回执与盘上这一格必须逐字相同。
    assert stored.created_at == receipt["created_at"]
    assert stored.updated_at == receipt["updated_at"]
    assert stored.is_stopped is False


def test_the_plan_is_compiled_here_and_it_matches_the_context(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``plan_ref`` 那一份计划**必须是从这份上下文编出来的**。

    ``admit_run`` 比的就是这一条（``plan.fingerprint_digest`` 与
    ``fingerprint_of(context)``）。服务面上若把它换成「客户端顺带交一份计划」，
    一次基于 A 上下文的提交可以被挂上一份 B 的计划，而盘上每一处都会显示
    「这一轮按那份计划跑了」—— 于是这里的判据是：拿这份上下文**自己**编一份，
    摘要必须与受理写下的那一格相同。
    """
    _seed_scene(settings)
    outcome = _drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers)
    receipt = _ok(outcome)

    context = ResearchContext(**CONTEXT)
    preview = preview_plan(load_profile(settings), context)
    assert preview.outcome is PreviewOutcome.READY, preview.held
    assert preview.plan is not None
    assert receipt["plan_ref"] == preview.plan.digest
    assert receipt["context_ref"] == preview.plan.fingerprint_digest


# --- 2 · 条件 1 的后半句：同一个键重发拿回同一行 -------------------------------


def test_the_same_key_twice_is_the_same_row_and_not_a_second_round(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """§B17 条件 1 后半句在服务面上的样子。

    同一个 ``idempotency_key`` 配同一份内容再提交一次，拿回的是**同一行**：
    ``run_id`` / ``created_at`` / ``spec_sha256`` 三格逐字相同，而盘上仍然只有一行。

    ## 两格刻意**不**相同，而它们正是这条判据的量具

    ``admitted_at`` 是**这一次调用**的受理时刻（服务端的时钟），``created_at`` 是
    那一行**写下去**的时刻。第一次提交这两个时刻几乎相同，第二次提交只有一个变了。
    于是「这一行早就有了」这句话在这里是从 ``created_at < admitted_at`` **读出来**
    的，不是任何一个 store 报告的事实 —— 服务面因此刻意**不**做一个 ``replayed``
    布尔：那会把一句由两个时刻读出来的话，变成一处需要有人负责的判断。

    它同时是这条判据的反证：两次的 ``admitted_at`` 若逐字相同，说明第二次提交根本
    没到受理这一步，而上面那三格相同就成了一句空话。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _payload()), (TOOL, _payload())],
        handlers=wired.handlers,
        scenes=wired.scenes,
    )
    first, second = _ok(outcome, 0), _ok(outcome, 1)

    assert first["run_id"] == second["run_id"]
    assert first["created_at"] == second["created_at"]
    assert first["spec_sha256"] == second["spec_sha256"]
    assert first["admitted_at"] != second["admitted_at"], (
        "两次提交的受理时刻逐字相同 —— 第二次多半压根没有到受理这一步"
    )
    assert second["created_at"] == first["created_at"]
    assert _rows(settings) == 1, "同一个键重发留下了第二行"


def test_the_same_key_with_another_spec_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """同一个键配**另一份内容**是一次冲突，不是一次重发。

    这一条与上一条是同一格的两半，而少掉它的话，「幂等」会退化成「这个键随便用」：
    第二份内容会被当成第一份的重发**静默吞掉**，于是调用方以为改过的那一版跑了，
    盘上却是原来那一版。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings,
        tmp_path,
        [
            (TOOL, _payload()),
            (TOOL, _payload(cutoff=(CUTOFF + timedelta(hours=1)).isoformat())),
        ],
        handlers=wired.handlers,
        scenes=wired.scenes,
    )
    _ok(outcome, 0)
    text = _denied(outcome, 1)
    assert "幂等键" in text
    assert _rows(settings) == 1, "冲突之后盘上多了一行"


# --- 3 · 三条拒绝，各自的逐字出处 ---------------------------------------------


def test_an_automatic_mode_is_refused_today(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``bounded_auto`` **不带窗口**时受理不了，拒绝的话来自 ``ports/runs.py``。

    服务面不另写一句「暂不支持这个模式」：那句话与「这个模式今天走不通」在报告上
    同形，而处置完全不同（一个要改治理、一个要等切片）。

    这一句的理由换过两次，而**这一条判据跟着换过两次断言**，所以它值得记下来：

    * e1 之后它点的是**读者**（「窗口有形状了，但还没有一处读过它」）；
    * e2 之后点的是**生产者**（``submit_run`` 恒传 ``None``，没有一处会写下窗口）；
    * e2c 之后点的是**这一份请求** —— 窗口写得出来了，而这一次没写。

    判据在这里钉的是**第三句里的那两件事**：拒词说得出缺的是哪一份授权，也说得出
    处置（把窗口写进 ``window`` 那一格，或者把模式改回 ``manual``）。所以断言里
    「没有带授权窗口」之外还有 ``window`` 这个**字段名** —— 只断前一句的版本在
    「拒了但不说怎么改」时照样绿。

    带窗口的那一份**走得通**，它的判据住在 ``tests/test_run_window_admission.py``
    与 ``test_a_bounded_auto_with_a_window_is_admitted``。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _payload(execution_mode="bounded_auto"))],
        handlers=wired.handlers,
        scenes=wired.scenes,
    )
    text = _denied(outcome)
    assert "bounded_auto" in text
    assert "没有带授权窗口" in text
    assert "window" in text, "拒词说不出处置（窗口该写在哪一格）"
    assert "manual" in text, "拒词说不出另一条出路（改回最保守的那个模式）"
    assert _rows(settings) == 0, "被拒的一次提交在盘上留下了一行"


def test_a_cutoff_that_is_not_in_the_future_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """一个从出生就过期的 Run 会被阈值那套逻辑读成「跑完了」。"""
    _seed_scene(settings)
    outcome = _drive(
        settings, tmp_path, [(TOOL, _payload(cutoff=PAST.isoformat()))], handlers=wired.handlers
    )
    text = _denied(outcome)
    assert "不在将来" in text
    assert _rows(settings) == 0


@pytest.mark.parametrize(
    "key", ["", "   ", " run-2026-09-28-a", "run-2026-09-28-a "], ids=repr
)
def test_a_blank_idempotency_key_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired, key: str
) -> None:
    """空键（与带空白的键）不是一个键 —— 而两种失败都是**看起来正常**的那种。

    ``run_id`` 是 ``(workspace, 键)`` 的内容摘要，所以空键是一个**恒定**的 id：
    第二次提交会被当成第一次的重发拿回同一行，而调用方以为自己又开了一轮。§B17
    条件 1 后半句要的「不重复池子或成本」在这里是靠**键存在**成立的，所以这一格
    必须拒。

    首尾空白那一半更隐晦，所以要一起钉：同一个键多一个空格就是**另一个键** ——
    重发被受理成第二轮，而两行在报告上只差一个看不出来的空格。域那一层因此
    **只判不改写**（``strip()`` 一下收下它会让「报的是哪个键」与「用的是哪个键」
    从此不是同一个字符串），于是两条分支在这里各有一条判据。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings, tmp_path, [(TOOL, _payload(idempotency_key=key))], handlers=wired.handlers
    )
    text = _denied(outcome)
    assert "idempotency_key" in text
    assert _rows(settings) == 0


def test_an_empty_envelope_is_refused(settings: Settings, tmp_path: Path, wired: _Wired) -> None:
    """没有上限不是「随便用」。

    §B17 条件 4 要的是「达累计预算时 stop」，而一条线都没有的 Run 在那个阈值上
    永远不触发 —— 它在报告里长得像「预算充足」，实际是「没人设过」。
    """
    _seed_scene(settings)
    outcome = _drive(settings, tmp_path, [(TOOL, _payload(budget={}))], handlers=wired.handlers)
    text = _denied(outcome)
    assert "budget 不得为空" in text
    assert _rows(settings) == 0


def test_an_unknown_budget_line_names_the_closed_set(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """写错一个科目名的结果必须是**这一次调用被拒**，而不是那条额度被静默丢掉。

    丢掉的额度在报告上读起来像「这个科目没花钱」。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _payload(budget={"queries": 20}))],
        handlers=wired.handlers,
        scenes=wired.scenes,
    )
    text = _denied(outcome)
    assert "queries" in text
    assert "browser_subrequest" in text, "拒绝的话里应当报出本部署的科目闭集"
    assert _rows(settings) == 0


def test_a_context_that_does_not_validate_names_the_field(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """一份构造不出来的上下文：拒绝里要有**字段名**。

    ``str(ValidationError)`` 会把调用方给的那段 JSON 整个回显回去，而调用方要的
    只是「哪一个字段、为什么不行」—— 所以这里断言回执里点得出 ``geo``。
    """
    _seed_scene(settings)
    broken = dict(CONTEXT, geo="gb")
    outcome = _drive(
        settings, tmp_path, [(TOOL, _payload(context=broken))], handlers=wired.handlers
    )
    text = _denied(outcome)
    assert "geo" in text
    assert _rows(settings) == 0


# --- 3′ · 授权窗口：自动模式的那份授权（切片 e2c）-----------------------------


def _window(**overrides: Any) -> dict[str, Any]:
    """一份写得完整的窗口。``not_after`` 与 ``cutoff`` 一样**从当下算**。

    写死一个「未来」的时刻是同一颗定时炸弹（见 :data:`CUTOFF` 那一段）：过了那一天
    之后 ``not_after < cutoff`` 恒成立，而那看起来像窗口那一格坏了。

    ## 要判「同一条链上的两轮」，调用一次、把结果传两次

    窗口的摘要（``AuthorizationWindow.digest``）就是**这条链的身份** —— 也正是
    ``progress_of`` 用来挑出「这条链上有哪几轮」的那一格。所以两次各现算一次
    ``not_after`` 得到的不是「同一个窗口写了两遍」，而是**两条不同的链**：微秒不同、
    摘要不同、上一轮不在链上。

    这不是一条测试的怪癖，是这一格的语义：窗口的字节就是授权的内容，**同一份授权
    重发时才认得出来**。凡是「第二轮要看见第一轮」的用例，都把这个值取一次再传两次
    （见 :func:`_chain_window`）。
    """
    body: dict[str, Any] = {
        "max_objects": 5,
        "budget": {"search": 40},
        "not_after": (utc_now() + timedelta(days=3)).isoformat(),
    }
    body.update(overrides)
    return body


def _chain_window(**overrides: Any) -> dict[str, Any]:
    """:func:`_window` 的**取一次**版 —— 让同一条链上的两轮读到同一份授权。

    这个别名不是糖：它让「这一条链」在用例里有一个名字。用 ``_window()`` 调两次的
    用例看起来在判「同一条链的第二轮」，而实际上判的是「另一条链的第一轮」，且它
    **照样绿** —— 第二轮受理成功，一切都像预期。所以差别只值一个别名，代价却是
    整条判据问错了问题。
    """
    return _window(**overrides)


def _auto(**overrides: Any) -> dict[str, Any]:
    """一次走**得通**的自动受理 —— 这一节里那一份写一遍，不每条例子里重写。"""
    body: dict[str, Any] = {"execution_mode": "bounded_auto", "window": _window()}
    body.update(overrides)
    return _payload(**body)


def test_a_bounded_auto_with_a_window_is_admitted(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**这一条就是那扇门**：带窗口的自动模式受理得下来，而且窗口读得回去。

    e2c 之前 ``submit_run`` 恒传 ``window=None``，所以这一份请求在服务面上**不可能
    成功** —— 不论窗口写得多完整。这一条判据问的是「那扇门真的开了吗」，而它必须
    从**回执**与**盘上那一行**两头都问一遍：

    * 回执里 ``window`` 三格逐字（写下去的与报出来的是同一份）；
    * ``get_run`` 读回来的也是同一份 —— 两处共用 ``_projection``，所以这一半判的是
      「它进了读回面那张字段表」（``_READ_FIELDS`` 里有 ``window``，少了它这两处会
      一起少，而那一节的集合判据是唯一答得了「少了一格」的地方）。

    ``execution_mode`` 那一格也要一起判：一个把窗口收下、却把模式写回 ``manual`` 的
    实现，会让这一行在盘上读起来像「没有人授权过」，而窗口那一格变成一句没人认领
    的注释。

    ## 时刻那一格按**解析后的时刻**比，不按字符串

    回执里的时刻是 pydantic 序列化出来的，本系统写出来的是 ``…Z``；而调用方交上去
    的 ``isoformat()`` 写的是 ``+00:00``。两条字符串说的是同一时刻 —— 比字符串会把
    一次正确的往返读成失败，而它修起来的方式（改回执的格式）动的是**所有**时刻的
    线上形状。所以这里把两边都解析回 ``datetime`` 再比，其余两格逐字比。
    """
    _seed_scene(settings)
    sent = _chain_window()
    receipt = _ok(
        _drive(settings, tmp_path, [(TOOL, _auto(window=sent))], handlers=wired.handlers)
    )

    assert receipt["execution_mode"] == "bounded_auto"
    granted = receipt["window"]
    assert granted["max_objects"] == 5
    assert granted["budget"] == [{"line": "search", "ceiling": 40}]
    assert datetime.fromisoformat(granted["not_after"]) == datetime.fromisoformat(
        sent["not_after"]
    ), "写下去的那一格与报出来的那一格不是同一个时刻"
    assert receipt["budget"] == [{"line": "search", "ceiling": 20}], "本轮信封是另一格"
    assert _rows(settings) == 1

    read = _ok(
        _drive(
            settings,
            tmp_path,
            [(READ, _read(run_id=receipt["run_id"]))],
            handlers=wired.handlers,
        )
    )
    assert read["window"] == receipt["window"]


def test_a_manual_run_reports_no_window(settings: Settings, tmp_path: Path, wired: _Wired) -> None:
    """``manual`` 那一行的 ``window`` 是 ``None`` —— 「没有自动授权」是个**值**。

    与上面那一条成对：只看「窗口读得回来」的判据在「每一行都报一份窗口」时照样绿，
    而那时 ``manual`` 与 ``bounded_auto`` 在回执上分不开 —— 而这两行说的正是这件事
    （「这一轮之后可以自己接下一轮吗」）。
    """
    _seed_scene(settings)
    receipt = _ok(_drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers))
    assert receipt["execution_mode"] == "manual"
    assert receipt["window"] is None


@pytest.mark.parametrize("dropped", ["max_objects", "budget", "not_after"])
def test_a_window_missing_a_field_is_refused(
    dropped: str, settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """缺一格就拒，而且拒词**点名**缺的是哪一格。

    三格都要，依据逐字在 ``domain/run.py::AuthorizationWindow``：§B17 条件 4 的
    「任一」要求三个都能被求值，而一个缺席的阈值会让那句话少判一条 —— 它在报告上
    与「那条判据没触发」同形。所以这里对三格**逐个**判，不是只判一个：一个把
    ``max_objects`` 补了缺省而放着另外两格不判的实现，会在这一族里红两次。

    整格不给（``window=None``）是**另一条路** —— 那是 ``manual``，走得通。两条路的
    区别是这一族要守住的：拒绝与受理只差「有没有写下那份授权」。
    """
    _seed_scene(settings)
    incomplete = _window()
    del incomplete[dropped]
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _auto(window=incomplete))],
        handlers=wired.handlers,
    )
    text = _denied(outcome)
    assert dropped in text, "拒词要点名缺的那一格"
    assert _rows(settings) == 0


def test_a_window_with_an_extra_field_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """多写一格也拒 —— **静默收下**在这里是最坏的处置。

    ``max_object``（少个 s）会被读成「我写上限了」，而那一格从来没进过任何判定：
    报告上它是「这条链还没到上限」，与「没有人写过上限」长得一样。
    ``AuthorizationWindow`` 是 ``StrictModel``，本来就会拒 —— 这一条判的是**服务面
    给它的人话**（点名那个多出来的名字），而不是裸的 pydantic 字段路径。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _auto(window=_window(max_object=5)))],
        handlers=wired.handlers,
    )
    text = _denied(outcome)
    assert "max_object" in text
    assert "max_objects" in text, "拒词要说得出对的名字是哪几个"
    assert _rows(settings) == 0


@pytest.mark.parametrize("shape", ["a string", 5, ["max_objects"]], ids=repr)
def test_a_window_that_is_not_an_object_is_refused(
    shape: Any, settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """窗口要的是一个**对象** —— 三样别的形状各判一遍，而拒绝它的是**框架**。

    ## 这一条判的不是 ``_window`` 里那一支

    实测：这三样形状在进到 ``submit_run`` 的函数体**之前**就被拒了 —— 拒词是
    ``1 validation error for submit_runArguments / window / Input should be a valid
    dictionary``（SDK 按工具入参 schema 自己校了一遍，``window`` 那一格的类型是
    ``dict[str, Any] | None``）。``_window`` 里那句 ``isinstance(declared, Mapping)``
    在服务面上**没有执行器**，它是给库调用方的第二道闸（照 ``_envelope`` 同款的位置，
    不过 ``_envelope`` 连这一道都没有）。这一条的判据因此落在框架那句拒词上：它要点名
    ``window``，也要说出这一格要的是一个**对象**。

    三样形状里那个**列表**最值得留意：它看起来像「三个阈值排一排」，而这一格是按名字
    取的 —— 一个照着位置读的实现会把它读成「max_objects=…」里的第一个成员。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings, tmp_path, [(TOOL, _auto(window=shape))], handlers=wired.handlers
    )
    text = _denied(outcome)
    assert "window" in text, "拒词要点名坏的是哪一格"
    assert "dictionary" in text or "object" in text, "拒词要说出这一格要的是一个对象"
    assert _rows(settings) == 0


@pytest.mark.parametrize("shape", ["a string", 5, ["max_objects"]], ids=repr)
def test_the_windows_own_shape_guard_still_fires(shape: Any) -> None:
    """``_parse_window`` 自己那道 ``isinstance`` 闸 —— 服务面够不到它，所以单独判一次。

    上一条判的是**框架**的拒词（那才是调用方看得见的那一句）。这一条判的是它背后
    那道闸本身：这一函数是模块级的，库调用方（以及将来某个直接拼规格的组合根）
    拿它去解析一份配置值时，框架那道校验**不在这条路上**。

    不判它的代价是它可以被无声地删掉：删掉之后上面那三条形状在服务面上照样被拒
    （框架拒的），而 ``_parse_window("a string")`` 会走到 ``name not in declared``
    那一句 —— 那对字符串是**子串**匹配，于是它会报「窗口缺了三格」，把一次形状错误
    说成一次内容错误。三样形状都要判，因为那道闸是按 ``Mapping`` 判的，而 ``str``
    恰好是 ``Sequence``、``list`` 恰好能当 ``in`` 的右边。
    """
    with pytest.raises(ToolError) as caught:
        _parse_window(shape)
    text = str(caught.value)
    assert "max_objects" in text and "not_after" in text, "拒词要写出这一格该有的三格"


@pytest.mark.parametrize(
    "broken,wanted",
    [
        ({"max_objects": 0}, ("一个对象都不许走",)),
        ({"budget": {}}, ("不得为空",)),
        # 第三条要的**不止**「说的是时区」：``_moment`` 是窗口与 ``cutoff`` 共用的一份
        # 实现，而共用的代价正是「一个字段的报错说成另一个字段」。所以它必须点名
        # ``window.not_after`` —— 调用方拿这个名字去改的那一格，与报错里写的那一格
        # 得是同一格。少了这一条，把 ``field="window.not_after"`` 改回 ``"cutoff"``
        # 在这条用例上照样绿，而那时它会指着一个不属于这次请求的字段。
        ({"not_after": "2026-10-05T00:00:00"}, ("时区", "window.not_after")),
    ],
    ids=["zero-objects", "empty-budget", "naive-moment"],
)
def test_a_window_whose_grids_do_not_hold_is_refused(
    broken: dict[str, Any],
    wanted: tuple[str, ...],
    settings: Settings,
    tmp_path: Path,
    wired: _Wired,
) -> None:
    """三格都在、而有一格**不成立**的三条路，逐条认领自己的那句话。

    三条各自是一条不同的校验收的（``AuthorizationWindow`` 的两个单格校验器 + 时区），
    而它们要一起判：只判其中一条时，「另外两条有没有接线」在服务面上无从观察。

    ## 每一条都断言**它自己那句**

    ``wanted`` 不是修饰：三条路的拒词全都要经过同一层 ``_readable``，所以「有拒绝、
    有窗口字样」这个断言对**任何**一条都成立。这一族在最初的写法里就吃过一次这个亏
    —— 一个「窗口里写了个不认识的科目」的用例实际撞上的是**信封超窗口**那条跨格
    校验器（它的报文里也有「授权窗口」四个字），于是它红了又绿、而它声称在判的那条
    线从来没被走到。所以三条各钉一句只属于它的话：

    * ``max_objects=0`` —— 「一个对象都不许走」的窗口**就是没有授权**。0 在
      ``RunBudgetLine.ceiling`` 上是合法的（「这一轮不走这条线」），所以这一条要判
      的正是「整条链的范围」与「这一轮的一条线」不是同一句话。
    * ``budget={}`` —— 一条线都没有的窗口在「达累计预算」那个阈值上永远不触发。
    * ``not_after`` 没带时区 —— 与 ``cutoff`` 同款：naive 不得进入本系统。这一格
      最值得单独判，因为 ``_moment`` 是**两个时刻共用**的那一份实现，而共用它的
      代价正是「一个字段的报错说成另一个字段」。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _auto(window=_window(**broken)))],
        handlers=wired.handlers,
    )
    text = _denied(outcome)
    assert "窗口" in text or "window" in text, "拒词要说得出坏的是窗口那一格"
    for piece in wanted:
        assert piece in text, f"这一条该由它自己那条校验收，而拒词里没有 {piece!r}"
    assert _rows(settings) == 0


def test_a_window_budget_with_an_unknown_line_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """窗口的 ``budget`` 里写了个不认识的科目 —— 与信封同一个闭集，写错就拒。

    窗口的预算与这一轮的信封**共用** ``_envelope``，所以这条判据同时是「两个地方
    走的是同一个闭集」的判据：只有信封收口而窗口摊开的话，一条拼错名字的线会在窗口
    上静静开出来 —— 而窗口的线正是**累计**上限，那意味着这条链有一条线永远不触发
    「达累计预算就停」。

    这一条**单独**一个用例（而不是上面那一族里的第 4 条），因为它的拒词来自
    ``_envelope`` 而不是 ``AuthorizationWindow``，而且它必须避开另一条判据的射程：
    「这一轮的信封超了窗口」那条跨格校验器在 ``search`` 上先炸的话，这条用例就会
    以「窗口出问题了」的样子绿，而它声称在判的那条线没被走到。所以这里的窗口预算
    在 ``search`` 上给得**比信封宽**（40 > 20），并且只多一个拼错的名字。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _auto(window=_window(budget={"search": 40, "browser_requests": 10})))],
        handlers=wired.handlers,
    )
    text = _denied(outcome)
    assert "browser_requests" in text, "拒词要点名那个不认识的名字"
    assert "browser_subrequest" in text, "拒词要给出正确的名字（闭集里那个）"
    assert _rows(settings) == 0


def test_a_window_on_a_manual_run_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``manual`` 带上窗口是**错的那一对**，由 ``RunSpec`` 的跨格校验器拒。

    这一条守的是「窗口不是一句可以随手加上的注释」：它只在自动模式上有一个**主语**
    （「这条链可以自己接下一轮」）。一份 ``manual`` 规格上挂着窗口，会在记录里写下
    「这一轮之后可以自己接下一轮」，而那句话在那份规格上不成立。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings, tmp_path, [(TOOL, _auto(execution_mode="manual"))], handlers=wired.handlers
    )
    text = _denied(outcome)
    assert "manual" in text
    assert _rows(settings) == 0


def test_a_window_that_ends_before_the_cutoff_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """窗口的截止**早于**本轮的截止：跨格校验器拒，服务面把它交出去。

    这一条同时是条件 4 第三个阈值「今天不可达」的另一半 —— 它存在的意义是让
    ``at < cutoff <= not_after`` 这条链成立（``window_refusal`` 的 docstring 第三节
    写了为什么那条 ``not_after < at`` 的分支永远不触发）。所以这一条判据红了，
    意味着「窗口到期」这件事重新变成了一条**有可能被走到**的路。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _auto(window=_window(not_after=(utc_now() + timedelta(hours=1)).isoformat())))],
        handlers=wired.handlers,
    )
    text = _denied(outcome)
    assert "窗口" in text or "window" in text
    assert _rows(settings) == 0


def test_the_same_key_and_the_same_window_are_the_same_row(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """重发同一个键、配**同一份窗口**，拿回同一行 —— 窗口进了幂等的那份内容。

    受理的幂等比的是**规格的全部内容**（``spec_fingerprint``），而窗口现在是规格的
    一部分。所以这一条与下一条要成对读：同一个键配同一份窗口 ⇒ 同一行；配另一份
    窗口 ⇒ 冲突。只有前者时，「窗口有没有进那份内容」答不出来 —— 一个把窗口漏在
    指纹之外的实现两次都拿回同一行，看着完全正常。
    """
    _seed_scene(settings)
    sent = _chain_window()
    first = _ok(
        _drive(settings, tmp_path, [(TOOL, _auto(window=sent))], handlers=wired.handlers)
    )
    again = _ok(
        _drive(settings, tmp_path, [(TOOL, _auto(window=sent))], handlers=wired.handlers)
    )
    assert again["run_id"] == first["run_id"]
    assert again["spec_sha256"] == first["spec_sha256"]
    assert _rows(settings) == 1


def test_the_same_key_with_another_window_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """同一个键配另一份窗口 ⇒ 冲突。那份承诺是「重发不会受理两次」。

    换个窗口不是「重发」——它是一次**新的授权**。放它过去，等于让调用方用一个已经
    用过的键把这条链的范围悄悄改掉，而盘上那一行还写着旧的那一份。

    这里「另一份」只差一格（``max_objects`` 5 → 9）—— 从**同一份**窗口派生出来，
    不是新算一份：现算一份会连 ``not_after`` 的微秒一起换掉，于是冲突可能来自时刻
    那一格，而这条判据声称在判的是「窗口整个在指纹里」。只差一格时它更强：一个只把
    ``not_after`` 算进指纹、而放着三个阈值里另外两个不管的实现，在这一条上会让同一
    个键受理出第二行。
    """
    _seed_scene(settings)
    sent = _chain_window()
    _ok(_drive(settings, tmp_path, [(TOOL, _auto(window=sent))], handlers=wired.handlers))
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _auto(window=dict(sent, max_objects=9)))],
        handlers=wired.handlers,
    )
    text = _denied(outcome)
    assert "幂等" in text or "idempotency" in text.lower()
    assert _rows(settings) == 1


def test_a_chain_that_reached_its_object_ceiling_opens_no_next_round(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**条件 4 的端到端（对象数）**：链走到了窗口的广度之后，下一次受理被拒。

    §B17 条件 4 逐字：「``bounded_auto`` 在达到对象数/累计预算/截止时间**任一** stop
    后不创建下一 Run」。这一条从**服务面**走通它，四步缺一不可：

    1. 第一轮带窗口受理成功；
    2. 它**结束**（夹具直接调 ``RunStore.stop``：这一条要的是「盘上那一行已经停了」，
       不是「结束这件事经由 ``close_run`` 那两道对账闸发生过」——后者是第 9 节的事），
       而 Delta 里带够 ``max_objects`` 那么多个业务键；
    3. 第二个键、同一份窗口再受理一次 ⇒ **被拒**；
    4. 拒词点名**对象数**那一条，而且盘上仍然只有一行。

    第 2 步是这一条与「库里那几条」的分界：``progress`` 读的是**盘上的行**，所以
    「这一轮结束了没有」必须真的在盘上发生过。第 4 步同时守住「被拒不留行」。

    两轮用的是**同一份**窗口（:func:`_chain_window`），因为链的身份就是那份窗口的
    摘要 —— 各现算一次得到的是两条链，而那时第二轮会像「新链的第一轮」一样被受理，
    这条判据就会以「它本该被拒」的形式红，红的原因却看着像窗口没接线。
    """
    _seed_scene(settings)
    sent = _chain_window(max_objects=1)
    first = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _auto(window=sent, idempotency_key="round-1"))],
            handlers=wired.handlers,
        )
    )
    wired.runs().stop(
        first["run_id"],
        delta=RunDelta(
            effects=(PoolEffect(cell=DeltaCell.DISCOVERED, key="v-1", label="场地 v-1"),)
        ),
        status=RunStatus.COMPLETED,
        at=utc_now(),
    )

    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _auto(window=sent, idempotency_key="round-2"))],
        handlers=wired.handlers,
    )
    text = _denied(outcome)
    assert "达到了它的授权窗口" in text
    assert _rows(settings) == 1, "被拒的那一次不该留下第二行"


def test_a_chain_that_reached_its_budget_ceiling_opens_no_next_round(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**条件 4 的端到端（累计预算）**：这一格第一次在服务面上被走通。

    这是 e2c 里唯一一条**必须**接上账本的判据。链上的累计预算由两半合成：链上有哪
    几笔预留（盘上的行）与那几笔各是多少（账本）。四步：

    1. 账本上真的预留并结算一笔（``set_ceiling`` → ``reserve`` → ``spend``）；
    2. 第一轮带窗口受理，结束时它的 ``delta.usage`` 记下**那一笔的 id**
       （``RunDelta.usage`` 是预留 id 不是金额 —— 「金额只有一个权威」）；
    3. 窗口的预算上限**正好等于**那一笔 ⇒ 第二个键再受理 ⇒ 被拒；
    4. 拒词点名**预算**那一条，且盘上仍然只有一行。

    第 2 步用夹具直接写那一行（``RunDelta.usage`` 今天在任何生产路径上都没有被写过），
    这正是这一格「有读法、没有生产者」的一半 —— 有条件 4 是 PARTIAL 的原因写在那里。

    两轮用**同一份**窗口（:func:`_chain_window`）：链的身份是窗口摘要，各现算一次就是
    两条链，那时第二轮读到的累计是零 —— 而它会**受理成功**，红出来的话说的是别的。
    """
    _seed_scene(settings)
    ledger = wired.ledger()
    # 预留的作用域与受理面读的那一条链无关（``spent_of`` 逐笔按 id 读账本，不按作用域
    # 求和），所以这里的作用域只是「这一笔是谁扣的」。
    scope = f"{settings.profile}:budget-ceiling-case"
    ledger.set_ceiling(scope, BudgetLine.SEARCH, 20, source="declared:profile")
    held = ledger.reserve(scope, BudgetLine.SEARCH, 20, idempotency_key="q-1")
    ledger.spend(held.reservation_id)

    sent = _chain_window(budget={"search": 20})
    first = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _auto(window=sent, idempotency_key="round-1"))],
            handlers=wired.handlers,
        )
    )
    wired.runs().stop(
        first["run_id"],
        delta=RunDelta(
            effects=(PoolEffect(cell=DeltaCell.DISCOVERED, key="v-1", label="场地 v-1"),),
            usage=(held.reservation_id,),
        ),
        status=RunStatus.COMPLETED,
        at=utc_now(),
    )

    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _auto(window=sent, idempotency_key="round-2"))],
        handlers=wired.handlers,
    )
    text = _denied(outcome)
    assert "累计预算走到了窗口上限" in text
    assert "search" in text, "拒词要说得出是哪条线走到了"
    assert _rows(settings) == 1


def test_a_chain_whose_ledger_cannot_answer_is_an_incident_not_a_refusal(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """链上记着一笔账本读不到的预留 ⇒ **事故**，不是一条受理拒绝。

    两种处置相反：受理拒绝是「改这次请求」，而这一条是「去查账」—— 一份 Delta 上
    写着 ``reservation_id`` 而账本说没有这一笔，只有两种可能：读错了账本，或者那一行
    丢了。所以它抛 ``RunReconciliationError``，服务面把它**照样交出去**（不换成一句
    「受理被拒」）。

    这一条与上面那一条成对，而它守的是**读不到时**的行为：一条按 0 继续的实现，
    在上面那一条上照样绿（账本读得到），只在一条链上留下一个「预算还够」的读数 ——
    然后多放行一轮。

    这一条**不需要**账本上有任何东西：要读的那一笔本来就不存在。两轮同一份窗口，
    与上面两条同一个理由。
    """
    _seed_scene(settings)
    sent = _chain_window()
    first = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _auto(window=sent, idempotency_key="round-1"))],
            handlers=wired.handlers,
        )
    )
    wired.runs().stop(
        first["run_id"],
        delta=RunDelta(
            effects=(PoolEffect(cell=DeltaCell.DISCOVERED, key="v-1", label="场地 v-1"),),
            usage=("a-reservation-that-was-never-taken",),
        ),
        status=RunStatus.COMPLETED,
        at=utc_now(),
    )

    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _auto(window=sent, idempotency_key="round-2"))],
        handlers=wired.handlers,
    )
    text = _denied(outcome)
    assert "a-reservation-that-was-never-taken" in text, "要指名是那一笔"
    assert "账本" in text
    assert _rows(settings) == 1


# --- 4 · 两条硬歧义的路 --------------------------------------------------------


def test_a_hard_ambiguity_holds_and_writes_nothing(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """场景那一格没人定 —— ``SCENARIO`` 是硬的那一半，**不编**。

    拒绝里要**逐条**点出是哪一维说不清（``Ambiguity.detail``）：一句「输入有点模糊」
    读的人无法据此改任何东西，而人要答的问题是具体的。
    """
    _seed_scene(settings)
    unclear = dict(CONTEXT, scenario_candidates=[])
    outcome = _drive(
        settings, tmp_path, [(TOOL, _payload(context=unclear))], handlers=wired.handlers
    )
    text = _denied(outcome)
    assert "scenario" in text
    assert _rows(settings) == 0, "一条硬歧义挡下的提交在盘上留下了一行"


def test_a_draft_scene_has_no_effective_version_and_this_is_not_an_error(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """「这个市场还没开张」是一个**合法**的拒绝理由。

    受理要从生效版本取 ``workspace`` 与 ``selector``（授权链的第一段），而一份
    ``DRAFT`` 记录没有生效版本 —— 这不是存储故障，是这个场景还没被批准。两句话
    的处置相反（去开张 / 去查存储），所以它们必须是两句不同的话。

    ## 判它们不同，靠的不是措辞

    本来这两条判据想用「有没有『读不出来』」来分，而那**判不了事**：两条拒绝
    各自都引用了对方那句话（「……是另一句话」正是说理的一部分），所以两边都含
    那四个字，任何一支被折成另一支都照样绿。

    真正分得开它们的是**版本号**：只有「记录说有效、而那一版读不出来」那一支手里
    有版本号可报（``第 1 版`` / ``latest_version=``），而 ``DRAFT`` 那一支连它在说
    哪一版都不知道。所以这一条判的是「拒绝里**没有**任何版本号」，下一条判的是
    「有，而且是盘上记着的那一个」。
    """
    _seed_scene(settings, state=SceneState.DRAFT)
    outcome = _drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers)
    text = _denied(outcome)
    assert "生效版本" in text
    assert "第 1 版" not in text, (
        "DRAFT 那一支报出了版本号 —— 它手里没有版本号，这一支要么读了 latest_version，"
        "要么干脆折成了「记录说有、而那一版读不出来」那一支"
    )
    assert "latest_version=" not in text
    assert _rows(settings) == 0


def test_the_record_says_a_version_is_effective_but_it_is_absent(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """记录说生效的是第 1 版，而那一版读不出来 —— 这是**存储不自洽**。

    把它折成「还没有生效版本」的后果是报告上说「这个市场还没开张」，而它其实开着、
    只是规则不见了：一个要人去查存储的问题被读成一句「还没开张」，于是没有人会去查。
    这一条判的是服务面没有把它折掉 —— ``_plan_context`` 那两句话原样到了调用方手里，
    含**盘上记着的那个版本号**（上一条判的是它在 ``DRAFT`` 那一支里不出现）。
    """
    _seed_scene(settings, version_on_disk=False)
    outcome = _drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers)
    text = _denied(outcome)
    assert "生效版本读不出来" in text
    assert "第 1 版" in text, "拒绝里没报出它读不到的那一版，调用方无处可查"
    assert _rows(settings) == 0


# --- 5 · 归属与角色 ------------------------------------------------------------


def test_another_scene_name_is_refused(settings: Settings, tmp_path: Path, wired: _Wired) -> None:
    """一个部署只服务一个 Scene —— 静默退回默认值会让调用方以为读的是另一个场景。"""
    _seed_scene(settings)
    outcome = _drive(
        settings, tmp_path, [(TOOL, _payload(scene_id="scene-devon"))], handlers=wired.handlers
    )
    text = _denied(outcome)
    assert "scene-devon" in text
    assert settings.profile in text
    assert _rows(settings) == 0


def test_submit_run_is_invisible_to_a_research_principal(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """§8：新的危险操作对普通研究身份**不可见、不可调用**。

    两半都要（``docs/MCP_PRODUCT_LOOP.md`` §3 那条隔离规则）：看不见在
    ``tools/list`` 上，调不动在 ``tools/call`` 上 —— 而拒绝的那句话必须与
    「这个名字根本不存在」**措辞同形**，否则它就成了一条旁路（它知道了这套服务
    能经营什么）。
    """
    _seed_scene(settings)
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _payload()), ("definitely_not_a_tool", {})],
        handlers=wired.handlers,
        scenes=wired.scenes,
        roles=[Role.RESEARCHER],
        want_tool_list=True,
    )
    assert TOOL not in outcome.tool_names

    # 逐字相同的那一句话，含工具名本身 —— 这是「措辞同形」的**全部**含义，
    # 一个只判 ``"Unknown tool" in text`` 的断言在两句话不同的时候照样绿。
    assert _denied(outcome, 0) == f"Unknown tool: {TOOL}"
    assert _denied(outcome, 1) == "Unknown tool: definitely_not_a_tool"
    assert _rows(settings) == 0


@pytest.mark.parametrize(
    "how",
    ["组合根没把 runs 交给服务面", "组合根给了工厂、而那个工厂是 None"],
    ids=["server-defaults", "handler-table-refuses"],
)
def test_a_deployment_without_a_run_store_says_it_is_not_wired(
    how: str, settings: Settings, tmp_path: Path
) -> None:
    """没接 Run 存储的部署回一句**接线事实**，不是「没有这一轮」。

    后者与「这一轮从来没被受理过」在报告上同形，而前者的处置是接线、后者是去受理
    一次 —— ``fetch_attempt_handlers`` 那一族立下的同一条规矩。

    **两条来路都要走**，因为它们是两处不同的代码：组合根压根没把 ``runs=`` 交给
    ``build_server`` 是一种；交给它一个**值为 None 的工厂**是另一种，后者由
    ``run_handlers`` 里那一句 ``if runs is None: return {}`` 处置。只走第一条时，
    第二条被改成「照样注册一条处理器」也照样绿 —— 而那时这一条工具会在被调用时炸
    在 ``runs.admit`` 上，交回给调用方一句内部异常，正是这一段要防的那种答复。

    ``_seed_scene`` 先建场景：第二条来路上，那一条处理器是**真的被调用**的，不先
    建场景它就停在 ``_plan_context`` 的「这个场景读不出来」上，红是红了，红的原因
    却与接线无关。
    """
    _seed_scene(settings)
    handlers = (
        {}
        if how == "组合根没把 runs 交给服务面"
        else run_handlers(
            settings=settings,
            scenes=default_scene_store(settings),
            maintenance=default_maintenance_store(settings),
            runs=None,
            ledger=partial(SqliteBudgetLedger, settings.database_path),
            # 池子照给：这一条的来路是「Run 记录那一份没接」，而 ``assets`` 是另一
            # 件事（e4 加进来的第五样，只有结束面用得到）。两样都缺会让这一条判不出
            # 是哪一样缺了 —— 而 ``runs is None`` 那一句是**唯一**一处「这一族工具
            # 整个不注册」的落点。
            assets=default_asset_store(settings),
            # 跟进池同款照给：``close_run`` 要它，而这一条判的是 ``runs is None``
            # 那一句，不是「哪一样闭包缺了」。
            followups=default_action_item_store(settings),
            # 切片 f 的两条（``submit_run`` 在 watch 上按 ``name`` / ``cohort`` 取主体）
            # 照给：这一条判的是「``runs is None`` 时那一族工具整个不注册」，而
            # 两样都缺会让这一条判不出是哪一样缺了 —— 与 ``assets`` 那一行的理由同款。
            accounts=default_account_pool_store(settings),
            cohorts=default_account_cohort_store(settings),
        )
    )
    outcome = _drive(settings, tmp_path, [(TOOL, _payload())], handlers=handlers)
    text = _denied(outcome)
    assert "尚未接线" in text
    # 一句话之外还有一条：接线缺失**不留下任何一行**。少了它，一个「先受理、
    # 再报接线缺失」的实现会两处都绿。
    assert _rows(settings) == 0


# --- 6 · 参数面：这几样不由载荷提供 --------------------------------------------


#: 任何一条受理工具都**不准**有的入参。
#:
#: 它们逐条对应一位**本服务端已经知道、而调用方不该有机会替它说话**的事实：
#:
#: * ``workspace`` / ``selector`` —— 这一轮圈的是哪个市场、挑的是什么。两样都从
#:   盘上那个场景的生效版本取（这正是本片把 ``scenes`` 交给 ``run_handlers`` 的
#:   唯一理由）。收下它们就等于允许「受理一个场景、写下一行指向另一个市场的 Run」
#:   —— 而那一行此后会被当成本地事实读回去。
#: * ``objective`` / ``plan_ref`` / ``policy_versions`` —— 这三样是**现编的计划**
#:   的产物。看得见它们，调用方就能在受理前先声称一份计划，而
#:   ``preview_plan`` 那次比对就成了走过场。
#: * ``context_ref`` / ``spec_sha256`` / ``spec_fingerprint`` —— 内容寻址的摘要。
#:   收下它们就是让「同一件事」由载荷认定，而幂等重发的整个立论是它由内容算出来。
#: * ``at`` / ``now`` / ``time`` / ``created_at`` / ``updated_at`` / ``admitted_at``
#:   —— 时间。受理时刻取服务端时钟；能倒填的审计记录不是审计记录。
#: * ``principal`` / ``by`` / ``approver`` —— 主体。取自传输层的令牌。
#: * ``run_id`` / ``state`` / ``revision`` —— 一行的身份与状态。前者由
#:   ``run_id(workspace, key)`` 内容寻址算出来，后两者是状态机的产物。
_FORBIDDEN_PARAMETERS = frozenset(
    {
        "admitted_at",
        "approver",
        "at",
        "by",
        "context_ref",
        "created_at",
        "now",
        "objective",
        "plan_ref",
        "policy_versions",
        "principal",
        "revision",
        "run_id",
        "scene",
        "selector",
        "spec_fingerprint",
        "spec_sha256",
        "state",
        "time",
        "updated_at",
        "workspace",
    }
)


def test_submit_run_takes_exactly_these_nine_and_no_others(wired: _Wired) -> None:
    """入参集合**恰好**是这一组 —— 多一个与少一个都要在这里被看见一眼。

    比集合相等而不是「不含某几个已知的坏名字」：后者只在有人加回一个**已经**被
    点过名的参数时才红，而这一条在任何一个新参数出现时都会先红一次，逼着回答
    「那个参数是不是又一条让调用方替事实说话的入口」。同一条纪律写在
    ``tests/test_mcp_product_loop.py::test_no_product_loop_parameter_can_certify_
    or_backdate_anything`` 上 —— 但那一张表走 ``product_loop_handlers``，而受理面
    是**另一个**处理器表，所以它结构上不在那一组里，判据得长在这里。

    九格里有四格要单独说清楚，否则下一次有人会照「``workspace`` 都禁了」把它们
    一起删掉：

    * ``scene_id`` 是**选择器**而不是内容 —— 一个部署只服务一个场景，给另一个名字
      是拒绝（见 ``test_another_scene_name_is_refused``），不是静默改读。所以它收的
      是「你要不要挑」，不是「你要哪个市场」。
    * ``execution_mode`` 是**授权语义**（切片 ``e``）：它收下 ``bounded_auto``、而
      这一份请求**没有带窗口**时，由 ``admission_refusal`` 逐字拒掉（「没有带授权
      窗口」）。e2 之前拒词说的不是这一句 —— 那时它说的是「窗口有形状了，但还没有
      一处读过它」，因为**窗口当时确实没有读者**；e2 把受理面打开了，于是那句拒词
      今天换成了一句关于**这一次请求**的话（那两句话的处置不同：前者的处置是去接
      那个读者，后者的处置是把窗口写上来）。一个收不下的参数会让这件事在接口上
      消失。
    * ``window`` 是**授权本身**，不是「一个服务端已经知道的事实」（e2c）。它进
      这张允许表而不是禁止表，因为服务端手里**没有**这份答案可以替它说话 ——
      ``workspace`` 禁得掉，是因为盘上的 Scene 就是答案；而「这条链最多走多远」
      除了调用方没人签得出来。
      **这不等于这一格没有缺口**：谁有权签它今天不判（身份/授权层，B01/B02）。
      缺口写在 ``adapters/mcp_server/runs.py`` 的模块 docstring 里，而**在这里的
      留白是要命的** —— 一个没有 ``window`` 的版本会让「带窗口的自动受理」这件事
      在接口上无从观察，而那正是这片要打开的那扇门。
    * ``name`` / ``cohort`` 是**这一批主体从哪来**（切片 f）。它们与 ``scene_id``
      同侧而不是与 ``workspace`` 同侧：问的是「你要不要挑一批」，不是「这一批是
      谁」—— 后者的答案在盘上那份清单/名单里，这两格只是把**来源**指出来。服务端
      手里没有这份答案可以替调用方说话（``what_is_due`` 不知道世界上有谁，逐字：
      「一个会自己发现主体的选择器必然也会自己**发明**主体」），所以它们进允许表；
      而它们**只在 watch 上有意义**，在别的模式上给它们会被拒（判据 7）。
    """
    parameters = set(inspect.signature(wired.handlers[TOOL]).parameters)

    forbidden = parameters & _FORBIDDEN_PARAMETERS
    assert not forbidden, (
        f"{TOOL} 收下了 {sorted(forbidden)} —— 这些名字承载的事实（圈的是哪个市场、"
        "挑的是什么、计划是哪一份、哪一个时刻、谁在受理、哪一行）"
        "都由服务端从盘上的场景、现编的计划或传输层推出来，不由载荷提供"
    )

    assert parameters == {
        "context",
        "budget",
        "cutoff",
        "idempotency_key",
        "execution_mode",
        "scene_id",
        "window",
        "name",
        "cohort",
    }


# --- 7 · 读回面：`get_run` ----------------------------------------------------


#: 读回那一条工具的名字。与 ``TOOL`` 并列写一次 —— 两条工具在同一个文件里，
#: 而它们判的不是同一件事（一条写、一条只读）。
READ = "get_run"


def _read(**overrides: Any) -> dict[str, Any]:
    """读回面的载荷。缺省的 id 形状是**对的**（32 位小写十六进制）而库里没有它 ——
    「形状不对」与「这一轮不存在」是两件事，缺省值不替它们混在一起。
    """
    body: dict[str, Any] = {"run_id": "0" * 32}
    body.update(overrides)
    return body


def test_get_run_reads_back_the_row_that_submit_run_wrote(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """读回来的那一行与**盘上那一行**逐格相同，且与受理回执在共有的格上相同。

    两件事都要判，而它们判的不是同一件事：

    * **读的是盘上那一行。** 所以这里另起一次 ``_drive``：新的服务端、新的连接、
      同一个库。与第 1 节那条判据同一个理由 —— 拿处理器手上那个对象再看一遍，
      「真的落盘了」这句话就没有判据。格值另与 ``store.get()`` 读出来的那一行比一次。
    * **两份投影不漂。** 这是 ``_projection`` 存在的唯一理由：两份各写一遍之后，
      先漂的是「哪些格子进这份回执」—— 一份说 ``delta``、另一份不说，而两份都声称
      自己报的是**同一行**。比的是**受理回执减去它独有的那几格**，因为受理面多报的
      正是它答的另一个问题（**这一次调用**的时刻、这一次记下的软歧义）。
    """
    _seed_scene(settings)
    admitted = _ok(_drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers))
    read = _ok(
        _drive(
            settings,
            tmp_path,
            [(READ, _read(run_id=admitted["run_id"]))],
            handlers=wired.handlers,
        )
    )

    # 受理面独有的三格，加上**读回面独有的那一格**。``note`` 两份都有一格、而两份
    # 各说各的（一份说「受理不是执行」，一份说「这是一条读」），所以它也不在共有的
    # 那一半里。
    #
    # ``folds`` 是读回面多出来的（e4）：折入账**不是这一行的一列**，它在
    # ``run_fold`` 那张表上，而那样表是 ``_projection`` 够不着的。所以它由 ``get_run``
    # 自己读、自己报，不在两份共用的那一半里 —— 而这也解释了为什么受理回执里没有它：
    # 那一份答的是「这一行被写下来了吗」，而它报 ``folds`` 只能是「受理当场那份空账」，
    # 一个在受理面上恒定的读数。
    theirs = {"admitted_at", "recorded", "note"}
    assert set(admitted) - theirs == set(read) - {"note", "folds"}
    for name in sorted(set(read) - {"note", "folds"}):
        assert read[name] == admitted[name], f"{name} 在两份投影里不一样：{read[name]!r}"

    # 读回面比受理面**多报**的地方在这里：受理那一刻这一轮还没结束，而这两格
    # 是「结束」这件事仅有的落点。
    assert read["stop_reason"] is None
    assert read["stopped_at"] is None
    assert read["delta"] is None
    assert read["is_stopped"] is False

    # 另起一个 store 读那一行（工厂每次新建一份），逐格比一次 —— 上面的比较是
    # 两份**回执**之间，这一组是回执与**库**之间。
    on_disk = wired.runs().get(admitted["run_id"])
    assert on_disk is not None
    assert read["spec_sha256"] == on_disk.spec_sha256
    assert read["created_at"] == on_disk.created_at
    assert read["updated_at"] == on_disk.updated_at
    assert read["cutoff"] == on_disk.spec.cutoff.isoformat()


#: 读回面逐字报哪些格 —— 一份**写下来**的名单。
#:
#: 为什么不能只比两份回执「相同」：两份都从 ``_projection`` 来，所以**少**一格时
#: 两份一起少，任何「两者一致」的判据都照样绿。而这份投影**就是**这条工具的全部
#: 内容 —— ``budget`` 少一格调用方读不到信封，``plan_ref`` 少一格读不到这一次读的
#: 是哪份计划，``idempotency_key`` 少一格重发之后认不出是不是同一轮。
#: 与受理面那张参数表同一条纪律（``test_submit_run_takes_exactly_these_seven_and_no_
#: others``）：集合逐字比出来，而不是只比「有没有多出来的」。
#:
#: ``folds``（e4）不在 ``_projection`` 里，而它**必须**在这张名单里：那一格是这一轮
#: 「折进池子的是哪几行」在服务面上唯一的读法（账由 ``promote_receipt`` 的
#: ``run_id=`` 记下）。少了它，账就只剩写面没有读面 —— 而报告上那与「这一轮一行
#: 都没折」长得一样。
_READ_FIELDS = frozenset(
    {
        "budget",
        "context_ref",
        "created_at",
        "cutoff",
        "delta",
        "execution_mode",
        "folds",
        "idempotency_key",
        "is_stopped",
        "note",
        "objective",
        "plan_ref",
        "policy_versions",
        "run_id",
        "selector",
        "spec_sha256",
        "stop_reason",
        "stopped_at",
        "updated_at",
        "window",
        "workspace",
    }
)


def test_get_run_reports_exactly_these_fields(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """读回面报的格**恰好**是这一组 —— 而这一条是「少了一格」唯一的判据。

    上面那条判据比的是两份回执**一致**（``_projection`` 不许漂），但它答不了「少一
    格」：两份同源，少了的时候两份一起少，两个集合仍然相等。所以这个集合必须**写出
    来**，否则 ``_projection`` 里的一格可以静默消失 —— 而回执上少一格的表现，与
    「这一格本来就不该有」在读的人那里长得一样。
    """
    _seed_scene(settings)
    admitted = _ok(_drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers))
    read = _ok(
        _drive(
            settings,
            tmp_path,
            [(READ, _read(run_id=admitted["run_id"]))],
            handlers=wired.handlers,
        )
    )
    assert set(read) == _READ_FIELDS


@pytest.mark.parametrize(
    "run_id",
    ["0123456789abcdef0123456789abcdef", "", "run-2026-09-28-a"],
    ids=["content-addressed-but-absent", "empty", "the-other-id-space"],
)
def test_an_unknown_run_id_says_there_is_no_such_run(
    run_id: str, settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """指不到任何一轮时，回的是「**没有**这一轮」，不是「这一轮已经结束了」。

    两句话在报告上同形、处置相反：前者是 id 写错了（去查 id），后者是去读那一轮的
    结局。``unknown_run_refusal`` 逐字写了为什么不能读成后者，而它住在
    ``ports/runs.py``（两个存储实现共用同一份）—— 所以这里判的是**那一段一字不差地
    在回执里**：服务面换一句措辞、或者在它前后补一句自己的解释，都会在这里红。
    两句加强的读法各答一个问题：``endswith`` 答「有没有在后面追加」，``count`` 答
    「有没有在旁边另说一遍」。SDK 那条 ``Error executing tool ...:`` 前缀是传输层的，
    所以不断言整串相等 —— 报文**之外**还写了什么，正是这一条要管的事。

    三个值各是一个来路：

    * 形状**对**（32 位小写十六进制）而库里没有的；
    * 空的；
    * ``run-2026-09-28-a`` —— 本仓**另一个** id 空间（``_derived_run_id()`` 造的记账
      标签，形状是 ``run-<时刻>``）。它摆在这里，正因为那句报文的第二段逐字点了它的名：
      「这个 id 根本不是 Run 的 id」。
    """
    _seed_scene(settings)
    text = _denied(
        _drive(settings, tmp_path, [(READ, _read(run_id=run_id))], handlers=wired.handlers)
    )
    refusal = unknown_run_refusal(run_id)
    assert text.endswith(refusal), "拒绝的话后面还被追加了别的东西"
    assert text.count("没有这一轮") == 1, "这句话出现了不止一次 —— 服务面另说了一遍"


def _row_of_another_workspace(settings: Settings, tmp_path: Path, wired: _Wired) -> str:
    """把盘上那一行的**工作区**改成另一个，返回它改后的 id。

    UPDATE 而不是 INSERT：这一行要满足表上的每一条约束（十一格规格、两个摘要、
    ``stop_reason`` 与 ``delta`` 同时有或同时无），而唯一一位**知道怎么拼出一份合法
    ``RunSpec``** 的生产者是受理面自己。所以先正常受理一轮，再改那一行。

    裸 SQL 而不经 ``admit``：后者的那一句会 ``bind_workspace``，而绑定契约**拒**第二个
    工作区写进同一个库（``tests/test_run_contract.py`` 钉住的就是它）。所以「库里有一行
    属于别的工作区」只能由夹具直接落下 —— 与 ``_seed_scene`` 造「记录说生效的是第 1 版、
    而那一版读不出来」那个不自洽状态同一个形状，理由也一样：要判的是**读出来之后**
    会发生什么。

    这一行是**真会发生**的：部署换了 ``settings.profile``、却仍指着同一个
    ``database_path`` 时，盘上就是这个样子（绑定的那一侧拦的是写，不拦读）。

    ``run_id`` 也一起改：它是 ``run_id(workspace, key)`` 的产物。不改的话这一行的 id
    与它的工作区对不上，于是它被拒的理由会变成另外一句 —— 而那一句不是这里要判的。
    """
    _seed_scene(settings)
    admitted = _ok(_drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers))
    other = "another-market"
    spec = wired.runs().get(admitted["run_id"]).spec.model_copy(update={"workspace": other})
    moved = run_id(other, spec.idempotency_key)
    with sqlite3.connect(settings.database_path) as conn:
        conn.execute(
            "UPDATE research_run SET run_id = ?, workspace = ?, spec = ? WHERE run_id = ?",
            (moved, other, spec.model_dump_json(), admitted["run_id"]),
        )
        conn.commit()
    return moved


def test_a_run_belonging_to_another_workspace_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """一行属于别的工作区 —— 它**读得到**，所以必须在这里被拒。

    「读得到」是这条判据的全部要害：``RunStore.get`` 按 id 取、不按工作区取，所以一个
    跨工作区的 id 会真的读到那一行。绑定契约只保证 ``admit`` 不会往一个库写第二个工作区，
    它**不保证**库里没有那样一行（见夹具的 docstring）。

    两句话都要读到：本部署服务的是哪一个、以及那一行属于哪一个。只说「读不到」会把这件事
    答成一次写错 id —— 而两者的处置相反（一个要换 id，一个要换一本账，或者去查这一行的
    部署为什么指着别人的库）。
    """
    moved = _row_of_another_workspace(settings, tmp_path, wired)
    text = _denied(
        _drive(settings, tmp_path, [(READ, _read(run_id=moved))], handlers=wired.handlers)
    )
    assert "another-market" in text
    assert settings.profile in text


def test_reading_writes_nothing(settings: Settings, tmp_path: Path, wired: _Wired) -> None:
    """一次读不写任何一行、也不改任何一格。

    两条都判，因为它们是两种不同的「写了」：**多出一行**（一个新 Run）与**改掉一行**
    （``updated_at`` 前移）。第二条尤其要紧 —— 一个读了之后顺手记一笔「谁读过」的实现，
    会让 ``updated_at`` 变成「最后一次有人看它」的时刻，而那一格下游要用来判断「这一轮
    从那以后动过没有」。
    """
    _seed_scene(settings)
    admitted = _ok(_drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers))
    before = _rows(settings)
    row = wired.runs().get(admitted["run_id"])
    assert row is not None

    read = _ok(
        _drive(
            settings,
            tmp_path,
            [(READ, _read(run_id=admitted["run_id"]))],
            handlers=wired.handlers,
        )
    )

    assert _rows(settings) == before
    after = wired.runs().get(admitted["run_id"])
    assert after is not None
    assert after.updated_at == row.updated_at
    assert read["updated_at"] == row.updated_at


def test_a_finished_run_is_still_readable(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """结束之后也读得到 —— 而且 ``stop_reason`` 与 ``delta`` 一起出现。

    受理面**报不出**这两半（受理那一刻它俩恒为空），所以这一条只有读回面答得了：一轮
    结束之后，收官的那几格是这一行上仅有的新事实。夹具直接用 ``RunStore.stop`` 造它
    （不经工具：这一条要的是「盘上那一行已经结束」，不是「结束经由那两道对账闸发生过」），
    与 ``_seed_scene`` 同一个形状。

    这里的 ``delta`` 是**空的**（每一格都空的产出）而 ``stop_reason`` 是 ``failed``：
    两者一起出现，而这正是要判的那条不变量 —— 空产出不是「还没有产出」。结局选
    ``failed`` 是因为 ``ending_refusal`` 只对 ``completed`` 要求非空产出（一条在跑起来
    之前就断掉的活，产出确实是零，要求它编点什么出来才是伪造）。
    """
    _seed_scene(settings)
    admitted = _ok(_drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers))
    wired.runs().stop(
        admitted["run_id"], delta=RunDelta(), status=RunStatus.FAILED, at=utc_now()
    )

    read = _ok(
        _drive(
            settings,
            tmp_path,
            [(READ, _read(run_id=admitted["run_id"]))],
            handlers=wired.handlers,
        )
    )
    assert read["stop_reason"] == RunStatus.FAILED.value
    assert read["is_stopped"] is True
    assert read["stopped_at"] is not None
    assert read["delta"] is not None
    assert read["delta"]["effects"] == []
    # 结束改过这一行，所以两个时刻从此分得开 —— 而这一句也是「读回面报的是**当前**
    # 状态、不是受理那一刻的快照」的判据本身。
    assert read["updated_at"] != read["created_at"]


def test_get_run_takes_exactly_the_id_and_nothing_else(wired: _Wired) -> None:
    """读回面的入参**恰好**是 ``run_id`` 一格。

    它落在那份 21 名的禁名单里（``run_id`` 是其中一员），而**这不是一处豁免**：那张表
    管的是「**写下**一格事实的时候不准由载荷替它说话」—— 受理面若收下 ``run_id``，就等于
    让调用方替 ``run_id(workspace, key)`` 这条内容寻址定一个值，于是同一个键可以指向不同
    的行。而读回面在**读**：这一格是查询键本身，它不是被写进任何一行的东西。

    所以这里连**相交**一起断言出来：将来若有人给读回面加 ``workspace=``，这个交集会变成
    两个名字，而那时红的话正好问的是那一句。
    """
    parameters = set(inspect.signature(wired.handlers[READ]).parameters)
    assert parameters == {"run_id"}
    assert parameters & _FORBIDDEN_PARAMETERS == {"run_id"}


# --- 8 · 读回面的下半：`get_run_increment` -------------------------------------
#
# 它与第 7 节那一条读的是**两种东西**：``get_run`` 读的是盘上那一行，
# ``get_run_increment`` 读回的是**按当下的盘现算的一份**。所以这一节的第一条判据
# 不是「读得回哪些格」，而是「同一行 Run、两次读、答案不一样」—— 那一句只有现算的
# 实现答得出来。其余的判据围着它要的那两样输入（维护账与场景的复核计划）转。

INCREMENT = "get_run_increment"


def _increment(**overrides: Any) -> dict[str, Any]:
    """读增量那一条工具的载荷。与 :func:`_read` 同形 —— 两条读回面的入参都是**一个
    内容寻址的 id**，而这一格是查询键本身，不是被写进任何一行的东西（第 7 节末尾
    那条判据写的就是这件事）。"""
    body: dict[str, Any] = {"run_id": "0" * 32}
    body.update(overrides)
    return body


def _admitted(settings: Settings, tmp_path: Path, wired: _Wired) -> str:
    """先受理一轮，返回它的 id。第 8 节每一条判据都要先有那一行 Run。

    受理而不是直接 ``store.admit``：增量面读的是**受理过的那一行**，而它读的是不是
    那一行由 ``_one_run`` 与 ``Run`` 的校验器共同保证 —— 用夹具直接写行会让这一段
    少走一次真实的路径（第 1 节就是为这件事存在的）。
    """
    _seed_scene(settings)
    admitted = _ok(
        _drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers)
    )
    return admitted["run_id"]


def _observe(
    wired: _Wired,
    settings: Settings,
    *,
    aspect: Aspect = Aspect.SOURCE_COVERAGE,
    subject_id: str = "acct-1",
) -> None:
    """往维护账上记一条观测，**时刻取当下**。

    时刻取当下是为了让那一格在钟上落 ``CURRENT``（窗口未到）—— 而 ``CURRENT`` 是
    唯一一档还要问盘盘的（``run_increment`` 第四节）。写一个「很久以前」的时刻会把
    每一格都推成 ``EXPIRED``，于是「复用」那一档与盘点那一支都判不到。

    直接走 ``MaintenanceStore`` 而不经 ``run_maintenance``：那一族要一份主体来源
    （账号池或 operator 的清单），而这一节要问的是「增量面读的是不是这本账」——
    用写工具去造它，红了之后说不清是哪一条断了（与 ``_seed_scene`` 同一个形状）。
    """
    wired.maintenance().put_observation(
        Observation(
            workspace=settings.profile,
            subject_kind="account",
            subject_id=subject_id,
            aspect=aspect,
            observed_at=utc_now(),
            source_ref=KEY,
        )
    )


def _book(settings: Settings) -> tuple[int, int, int]:
    """盘上三本账各几行：Run、观测、变更信号。数的是库，不是处理器手上那些对象。"""
    with sqlite3.connect(settings.database_path) as conn:
        counted = tuple(
            int(
                conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE workspace = ?",  # noqa: S608
                    (settings.profile,),
                ).fetchone()[0]
            )
            for table in ("research_run", "observation", "change_signal")
        )
    return counted


def test_the_increment_is_computed_against_the_book_as_it_stands_now(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """同一行 Run 读两次，答案不一样 —— 而那一行一个字都没改。

    这是它与 ``get_run`` 的结构性差别，也是这一节里唯一一条**判得出来**的差别：
    两份读数取在同一个库、同一行 Run 上，中间只往维护账里加了一条观测。判据因此
    分两半，缺一不可 ——

    * 读数**变了**（零格 → 七格）：它读的确实是当下的盘；
    * 那一行**没变**（``updated_at`` 逐字相同）：变的不是它。

    只判前半句的话，一个「把上一次的读数缓存在 Run 那一行上」的实现照样绿；只判
    后半句的话，``get_run`` 那一条就已经够了。
    """
    recorded = _admitted(settings, tmp_path, wired)
    before_read = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert before_read["cells"] == []
    assert before_read["inputs"] == {"observations": 0, "signals": 0}
    row_before = wired.runs().get(recorded)

    _observe(wired, settings)

    after_read = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert after_read["run_id"] == recorded
    assert after_read["inputs"]["observations"] == 1
    # 一个主体 × 七个面。这一句把「格子空间由观测决定」也一并判了：那一条观测让
    # **七个**面各有一格，而不是一格。
    assert len(after_read["cells"]) == len(ALL_ASPECTS)
    assert {cell["subject_id"] for cell in after_read["cells"]} == {"acct-1"}
    row_after = wired.runs().get(recorded)
    assert row_after is not None and row_before is not None
    assert row_after.updated_at == row_before.updated_at


def test_an_empty_book_reports_zero_cells_along_with_its_inputs(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """一个还没有任何观测的工作区拿到**零格**，而零格必须与「喂进去的是零条」一起读。

    零格这个读数本身是**含糊**的：它既可能是「这一轮没有事要做」，也可能是「这里
    还没有任何沉淀下来的事实」—— 而两者的处置相反（一个是收工，一个是先跑一轮）。
    所以回执里除了 ``cells`` 还有 ``inputs``：零格配零观测是后者。

    ``counts`` 那一条判的是 ``RunIncrement.counts`` 的纪律 —— **零也出现**。少了它，
    一档缺席时调用方从 ``cells`` 里读到的「这一档没有」与「这一档是零」是同一句话。
    """
    recorded = _admitted(settings, tmp_path, wired)
    report = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert report["cells"] == []
    assert report["inputs"] == {"observations": 0, "signals": 0}
    # 四档**都在**，且都是 0 —— 两半都要：只判「值是 0」的话，一个只报出现过的那
    # 几档的实现照样绿。
    assert set(report["counts"]) == {verdict.value for verdict in IncrementVerdict}
    assert set(report["counts"].values()) == {0}


def test_every_cell_carries_a_verdict_a_reason_and_the_plan_version(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """逐格三样：处置、给人照着做的理由、以及这一格是按哪一份策略读的。

    ``policy_version`` 跟着**格**走而不是在这一份读数上写一次，是 ``RefreshPlan`` 的
    docstring 点名要求的（§11：「每一格都带着 ``policy_version``」）。判据比的是
    **那一份生效版本的复核计划**自己算出来的指纹 —— 于是「报的是不是本部署这一份」
    也一并判了。

    ``counts`` 与 ``cells`` 的对应关系在这里判：两半说的是同一件事，一处数错而另一处
    没错的读数比两份都错更难发现。
    """
    recorded = _admitted(settings, tmp_path, wired)
    _observe(wired, settings)
    report = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    known = {verdict.value for verdict in IncrementVerdict}
    assert {cell["verdict"] for cell in report["cells"]} <= known
    assert all(cell["reason"].strip() for cell in report["cells"])
    assert {cell["policy_version"] for cell in report["cells"]} == {
        _scene().refresh.policy_version
    }
    for verdict in IncrementVerdict:
        assert report["counts"][verdict.value] == sum(
            1 for cell in report["cells"] if cell["verdict"] == verdict.value
        )


def test_a_source_coverage_cell_with_nothing_to_bind_is_missing(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """钟说「别动」的那一格，盘上却一条能当依据的来源都没有 —— 判它「缺」。

    这是增量面真正的那一格（``run_increment`` 第二节）：它的钟是 ``CURRENT``，而
    它欠的是一笔从来没被满足的账。天真的划分（``新 ∪ 过期``）会把这样一个主体整个
    报成「复用」，而「复用」是一句「有东西可复用」的话 —— 那里没有东西。

    库里一条证据行都没有，所以 ``census.new_source_gaps`` 非空；而这是本部署盘上
    的真实情形（刚跑起来的部署就是零行），不是夹具编出来的形状。

    **七个面都观测过**才判得动后半句：只观测 ``SOURCE_COVERAGE`` 那一面的话，其余六格
    是 ``NEW``（从没看过这一面）—— 那是另一档处置，而「凡 ``CURRENT`` 一律报缺」的
    实现照样绿。七格齐了，那一句问的才是「同样过了钟的七格里，为什么只有它被判缺」。
    """
    recorded = _admitted(settings, tmp_path, wired)
    for aspect in ALL_ASPECTS:
        _observe(wired, settings, aspect=aspect)
    report = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    coverage = [
        cell for cell in report["cells"] if cell["aspect"] == Aspect.SOURCE_COVERAGE.value
    ]
    assert len(coverage) == 1
    assert coverage[0]["verdict"] == IncrementVerdict.MISSING.value
    assert "能当依据的来源" in coverage[0]["reason"]
    # **只有**那一格是缺；其余六格照旧复用。
    assert report["counts"][IncrementVerdict.MISSING.value] == 1
    assert report["counts"][IncrementVerdict.REUSE.value] == len(ALL_ASPECTS) - 1


def test_a_change_signal_moves_its_own_cell_off_reuse(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """一条变更信号把**它自己那一格**从「复用」推成「重取」，别的格子不动。

    信号这一路（``ChangeSignal``）是这一条工具的第二样输入，而它与观测来自同一个
    存储。判据分三步：先读一次确认那一格是复用，再记一条信号，再读一次。

    只判「``inputs.signals`` 是 1」是不够的 —— 那只说明这一层把条数报出来了，而一条
    被读到却**没有参与判定**的信号在回执上与它长得一样。
    """
    recorded = _admitted(settings, tmp_path, wired)
    _observe(wired, settings, aspect=Aspect.ASSET_IDENTITY)
    before = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    moved = [
        cell for cell in before["cells"] if cell["aspect"] == Aspect.ASSET_IDENTITY.value
    ]
    assert [cell["verdict"] for cell in moved] == [IncrementVerdict.REUSE.value]

    wired.maintenance().put_signal(
        ChangeSignal(
            workspace=settings.profile,
            subject_kind="account",
            subject_id="acct-1",
            aspect=Aspect.ASSET_IDENTITY,
            signalled_at=utc_now(),
            reason="场地换了运营方 —— 这一格该重看",
        )
    )

    after = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert after["inputs"]["signals"] == 1
    moved_after = [
        cell for cell in after["cells"] if cell["aspect"] == Aspect.ASSET_IDENTITY.value
    ]
    assert [cell["verdict"] for cell in moved_after] == [IncrementVerdict.EXPIRED.value]
    assert after["counts"][IncrementVerdict.EXPIRED.value] == 1


def test_a_finished_run_refuses_to_plan_an_increment(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """已经结束的一轮**一格都不产出** —— 而拒绝的话来自 ports，不是这一层写的。

    挡它的位置只有这一处（``run_increment`` 第六节）：``admit`` 是幂等的，同一个键
    重发拿回的是**同一行**，哪怕那一行已经结束 —— 于是「重发一个已经结束的键」在
    受理那一步长得像成功，而它要挡的是第二轮真的又跑一遍。

    ``plan_increment`` 在**存储被读到之前**抛（它先判 ``run.is_stopped``），所以这一
    条同时是「一次拒绝不读盘」的判据 —— 而那正是这一支要挡的：读一遍盘就等于把这一
    轮又重新算了一遍。
    """
    recorded = _admitted(settings, tmp_path, wired)
    wired.runs().stop(
        recorded, delta=RunDelta(), status=RunStatus.FAILED, at=utc_now()
    )
    text = _denied(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert "已经结束" in text
    # 那句里逐字带着「不再开工」，而它是 ``RunStateError`` 的报文 —— 这一层一个字都
    # 没有改写。换一句自己的话在这里，等于让同一个情形在两处有两句话。
    assert "不再开工" in text


def test_a_market_that_is_closed_says_so_in_its_own_words(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """这一轮受理过、而此刻这个市场没有生效版本 —— 两句话**不是**同一句。

    受理面上「没有生效版本」等于「这个市场还没开张」（受理不了任何东西）；读回面上
    它是「这个市场被关上了」，因为**这一轮是受理过的** —— 受理本来就要求有一份生效
    版本。两处措辞不同不是文案问题：把它读成「还没开张」，处置就是去立项；而真实的
    处置是去看这一份 Scene 为什么被关上了。

    所以这一条判两半：读回面的话点名了本部署的场景，且**不是**受理面那一句。
    """
    recorded = _admitted(settings, tmp_path, wired)
    # 走 ``SceneStore`` 自己的写路径（与 ``control_scene`` 走的是同一条）而不是一句
    # 裸 SQL：那条记录的权威是 ``payload`` 列，而 ``get_record`` 是从它反序列化的 ——
    # 改旁边的列不会改到读出来的那一条（实测：改了列、payload 照旧，于是这一条测试
    # 会绿在一个什么都没发生的夹具上）。
    scene_store = SqliteSceneStore(settings.database_path)
    current = scene_store.get_record(settings.profile)
    assert current is not None
    scene_store.put_record(
        current.model_copy(
            update={
                "state": SceneState.ARCHIVED,
                "effective_version": None,
                "revision": current.revision + 1,
                "updated_at": utc_now(),
            }
        ),
        expected_revision=current.revision,
    )

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert settings.profile in text
    assert "受理过" in text
    # 受理面那一句的特征词。它不在这里，是「两处的话分得开」的判据 —— 谁把那段
    # 复制过来，这一条就红。
    assert "受理不了任何东西" not in text


@pytest.mark.parametrize(
    "unknown",
    ["0123456789abcdef0123456789abcdef", "", "run-2026-09-28-a"],
    ids=["content-addressed-but-absent", "empty", "the-other-id-space"],
)
def test_an_unknown_run_id_is_refused_here_too(
    unknown: str, settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """读回面的两条工具**共用同一条读法**，所以三条来路的措辞逐字相同。

    第 7 节已经为 ``get_run`` 判过这一条。这里再判一次的理由不是重复：它判的是
    **这一条工具**也走那一处 —— 将来若有人给增量面另写一条「按 id 取行」，这一条会
    红，而第 7 节那一条照样绿。

    三条来路（内容寻址但不存在 / 空串 / 另一个 id 空间）里，最后一条是那句报文
    第二段点名的那一个 —— 两个 id 空间撞在一起时，调用方手里往往正是一个 ``run-…``
    形状的标签。
    """
    refusal = unknown_run_refusal(unknown)
    text = _denied(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=unknown))],
            handlers=wired.handlers,
        )
    )
    # SDK 把 ``ToolError`` 包成 ``Error executing tool get_run_increment: <文本>``，
    # 所以不断言整串相等（那是传输层的前缀），而判「有没有在后面追加」与「有没有在
    # 旁边另说一遍」（与第 7 节同一条纪律）。
    assert text.endswith(refusal)
    assert text.count("没有这一轮") == 1


def test_reading_the_increment_writes_nothing(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """它是一条**读**：三本账一行都不动。

    三本而不是一本：这一条工具读的是 Run 那一行**与**维护账（观测、信号），所以
    它在哪一本上写下一行都属于违约 —— 而「读一遍顺手记一条观测」在本仓不是假想的
    事故（``run_maintenance`` 那一族**确实**在写），两件事长得像。

    ``assert report["cells"]`` 是这一条的成立条件：一份空读数上的「什么都没写」是
    句空话 —— 一个恒返回空表的实现会两处都绿。
    """
    recorded = _admitted(settings, tmp_path, wired)
    _observe(wired, settings)
    before = _book(settings)
    row_before = wired.runs().get(recorded)

    report = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert report["cells"]
    assert _book(settings) == before
    row_after = wired.runs().get(recorded)
    assert row_before is not None and row_after is not None
    assert row_after.updated_at == row_before.updated_at


def test_get_run_increment_takes_exactly_the_id_and_nothing_else(wired: _Wired) -> None:
    """入参**恰好**是 ``run_id`` 一格 —— 与 ``get_run`` 逐字同一条。

    三格**没有**出现，而它们各自都有一处会诱使人加上：

    * ``scene_id`` —— ``require_scene`` 只放行本部署那一个名字，所以它只有一个合法
      取值。收下它等于让调用方以为可以指另一个场景，而复核计划必须来自这一轮受理时
      那一份。
    * ``subjects`` / ``cohort`` —— 主体来源。今天这一条工具传 ``subjects=()``，格子
      空间只由已沉淀的观测决定；接上主体来源是另一件活（``get_maintenance_plan`` 的
      ``cohort`` / ``name``），而不是在这里开一个参数。
    * ``now`` —— 时刻。它与受理面同一条纪律：能由载荷给的时刻，会让「这一格旧不旧」
      取决于调用方报的是几点。
    """
    parameters = set(inspect.signature(wired.handlers[INCREMENT]).parameters)
    assert parameters == {"run_id"}
    assert parameters & _FORBIDDEN_PARAMETERS == {"run_id"}


# --- 9 · 结束面：`close_run` ---------------------------------------------------
#
# 三条工具在同一个处理器表里，而它们答的是三个不同的问题。这一节答的是第三个：
# 这一轮**可以**结束吗、而它确实结束了吗 —— §B17 条件 2 的两半（完整 Delta +
# 业务键双向对账）在服务面上的落点，也是**折入账**这一侧唯一的控制面。
#
# 这一节与 ``tests/test_run_reconciliation.py`` 不是重复。那一族判的是那个函数
# （它收三个参数、判三个方向），这一节判的是**服务面有没有把那些话原样交出去**、
# 以及**它收的那几格**：账读不出来时不许按零折入读完、主张是**句柄**而不是一个
# 由回执拼出来的对象、``at`` 由服务端取。

#: 结束那一条工具的名字。与 ``TOOL`` / ``READ`` 并列写一次 —— 同一条纪律。
CLOSE = "close_run"

#: 两个池子句柄。形状逐字取自 ``tests/test_run_reconciliation.py`` 的同一对 ——
#: 句柄是**池子那一行的 id**，过 ``check_slug_safe``，而这十六条判据里没有一条读它
#: 的内容：它们读的是**同一个字符串有没有出现在该出现的那一侧**。
V1 = f"src-{'1' * 16}"
V2 = f"src-{'2' * 16}"

#: 折入归属里的**回执**那一格。它进重放判据（``(run_id, receipt_id, ordinal)``），
#: 而这一节没有一条判据在判重放 —— 所以它只需要是一个说得清来源的名字。
RECEIPT_ID = "rcpt-devon-motherpool"


def _close(**overrides: Any) -> dict[str, Any]:
    """结束面的载荷。

    缺省是**一份零产出的 Delta 配 ``failed``**：``ending_refusal`` 只对 ``completed``
    要求非空产出，所以这个缺省值不把两条判据搅在一起（一条在跑起来之前就断掉的活，
    产出确实是零，要求它编点什么出来才是伪造）。判定「缺一格的形状」的那几条用例
    各自覆盖自己要的那一格。
    """
    body: dict[str, Any] = {
        "run_id": "0" * 32,
        "delta": {"effects": []},
        "status": RunStatus.FAILED.value,
    }
    body.update(overrides)
    return body


def _claiming(*cells: tuple[DeltaCell, str]) -> dict[str, Any]:
    """一份 Delta 载荷：给定几格处置。

    ``label`` 与 ``why`` 都给上：九格里六格要前者、三格要后者，而两格都给在类型上
    恒合法（``tests/test_run_reconciliation.py::_effect`` 同一条）。
    """
    return {
        "effects": [
            {
                "cell": cell.value,
                "key": key,
                "label": f"{key} 那块场地",
                "why": f"{key} 的原因",
            }
            for cell, key in cells
        ]
    }


def _fold_into(
    wired: _Wired, settings: Settings, run: str, handle: str, *, ordinal: int = 0
) -> None:
    """真的折一行进池子：池子那一行 + 记在**这一轮**账上的那一笔，同一次写。

    这是这一节每一条判据的前提，而它必须走 ``upsert_asset(..., fold=...)`` 而不是
    分两步写：那两步在自己的事务里各写一处，正是 e3 要关掉的那条假账的形状
    （``ports/pool.py`` 的 ``fold=`` 就是为此存在的）。``ordinal`` 只在**同一份回执
    里的两行**之间才该变 —— 它进重放判据。
    """
    wired.assets().upsert_asset(
        VenueAsset(
            workspace=settings.profile,
            venue_asset_id=handle,
            name=f"{handle} 的场地",
            first_seen_at=utc_now(),
        ),
        fold=FoldAttribution(run_id=run, receipt_id=RECEIPT_ID, ordinal=ordinal),
    )


def _drop_the_ledger(settings: Settings, run: str) -> None:
    """把这一轮的折入账那一行从库里删掉。**不经任何工具** —— 它由写路径构造不出来，
    所以造它只能由夹具直接动手（``_row_of_another_workspace`` 同一个形状，理由也一样：
    要判的是**读不出来之后**会发生什么）。

    ``run_fold`` 上有一条**指向账本的外键**，所以这个状态在开着外键的连接里删不出来
    —— 而适配器那条连接是开的（``db.py`` 把 ``foreign_keys`` 打开，SQLite 的缺省是
    关的）。这里显式关掉它，是因为要造的这个状态**只有靠动库才存在**：写路径把「池子
    那一行」与「这一笔账」放在同一个事务里，所以「折了、而账本没了」在应用层是**构造
    不出来**的。留着隐式缺省会让这条用例在某个把缺省改掉的连接上变成一条静默失败的
    夹具 —— 那时 `_streams` 的断言会红，而红的话读起来像「账本删不掉」。
    """
    with sqlite3.connect(settings.database_path) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM run_fold_ledger WHERE run_id = ?", (run,))
        conn.commit()


def _streams(settings: Settings, run: str) -> tuple[int, int]:
    """盘上两本账各几行：折入账本身、以及折进去的那几行。

    两个数**分开报**是因为 `_drop_the_ledger` 之后的那个状态正是「一个零、一个非零」
    —— 报一个合计数会让「账没了而折入还在」与「折入也没了」同形，而后者是另一个
    状态（真零折入），处置完全不同。
    """
    with sqlite3.connect(settings.database_path) as conn:
        ledger = conn.execute(
            "SELECT COUNT(*) FROM run_fold_ledger WHERE run_id = ?", (run,)
        ).fetchone()
        folds = conn.execute(
            "SELECT COUNT(*) FROM run_fold WHERE run_id = ?", (run,)
        ).fetchone()
    return int(ledger[0]), int(folds[0])


def test_closing_a_round_writes_the_ending_and_it_is_on_disk(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """结束真的落了盘：结局、产出、停表三样同时有 —— 而读回来的是**库**里那一行。

    拿处理器手上那个对象再看一遍答不了这件事（第 1 节同一条理由）；而三样「同时
    有」是 ``domain/run.py`` 与表的 CHECK 共用的那条不变量，服务面上多出来的一件事
    只是「它有没有把这三格一起写下」。

    这里的 ``held`` 是**不带池子主张**的那一格（``requires_an_existing_row`` 为假），
    所以这一条不需要池子里先有那一行 —— 它是这一节的基线：一份最小而完整的结束。
    """
    recorded = _admitted(settings, tmp_path, wired)

    ending = _ok(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V1)),
                        status=RunStatus.COMPLETED.value,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )

    assert ending["is_stopped"] is True
    assert ending["stop_reason"] == RunStatus.COMPLETED.value
    assert ending["stopped_at"] is not None
    assert [one["key"] for one in ending["delta"]["effects"]] == [V1]

    on_disk = wired.runs().get(recorded)
    assert on_disk is not None
    assert on_disk.is_stopped is True
    assert on_disk.stop_reason is RunStatus.COMPLETED
    assert on_disk.delta is not None
    assert [one.key for one in on_disk.delta.effects] == [V1]


def test_the_ending_receipt_does_not_re_report_the_folds(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """两份回执的格子**恰好**差 ``folds`` 一格 —— 不是「多报一点也无妨」。

    ``folds`` 是**账**的读面（写它的是写池子那一次事务），而结束回执答的是「这一行
    现在是什么状态」。两份都报同一串的代价是：先漂的那一格是「哪一串才算折进去了」，
    而两边都声称自己报的是同一件事 —— 于是没有哪一份能被判成错的。所以结束面只写、
    ``get_run`` 只读。

    这一条的判据是**集合逐字比**（照 ``test_get_run_reports_exactly_these_fields``
    那条同款纪律）：结束回执的格子 == 读回面那一份**减去** ``folds``。只判「结束面里
    没有 ``folds``」是不够的 —— 那样一个把 ``stopped_at`` 漏掉的实现照样绿，而那时
    调用方读不出这一轮是什么时候停的。
    """
    recorded = _admitted(settings, tmp_path, wired)

    ending = _ok(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V1)),
                        status=RunStatus.COMPLETED.value,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert set(ending) == _READ_FIELDS - {"folds"}


def test_a_round_that_folded_nothing_closes_and_the_ledger_reads_empty(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**真零折入是一个读数，不是一次拒绝。**

    这一条与下面那条（账上有、而主张说没有）是同一句话的两半，而把两者合成一条的
    实现会在两个方向里至少错一个：要么把零折入也拒掉，要么把假账放过去。所以这一条
    判的是「它停了」，并且**再读一次**把那份空账读回来 —— 后半句是判据的要害：
    一份「恒拒」的实现会让上面那条绿，一份「恒不核账」的实现会让这条绿，而两个
    读数一起看才是「零折入被读成了零折入」。
    """
    recorded = _admitted(settings, tmp_path, wired)

    ending = _ok(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V1)),
                        status=RunStatus.COMPLETED.value,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert ending["is_stopped"] is True

    read = _ok(
        _drive(
            settings,
            tmp_path,
            [(READ, _read(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert read["folds"] == []


def test_get_run_reports_the_rows_this_round_really_folded(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """折入账的**读回面**：这一轮真的折进池子的那几行，按折入次序。

    次序在这里是**读数**而不是排序：账记的是那次写发生的先后（``folds_of`` 的
    docstring），而把它排成升序会让「池子那一行是这一步写下去的」这件事丢掉 ——
    与 ``fold_claim`` 那一侧的升序不是同一件事（那边是**主张**的规范化，因为主张
    没有先后可言）。

    这一格与 ``close_run`` 的 ``folded`` 是**同一个地址空间**：读回来的这一串可以
    原样交回去当主张 —— 那正是这一对读写面成对存在的理由。
    """
    recorded = _admitted(settings, tmp_path, wired)
    _fold_into(wired, settings, recorded, V2, ordinal=0)
    _fold_into(wired, settings, recorded, V1, ordinal=1)

    read = _ok(
        _drive(
            settings,
            tmp_path,
            [(READ, _read(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert read["folds"] == [V2, V1]


def test_get_run_does_not_read_a_missing_ledger_as_zero(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """账本被删掉之后，读回面**也必须读不出来** —— 不是报一个空表。

    这是本片最容易做错的一格，因为「读不出来」在这一条工具上看起来像是把一个**读**
    变成一次失败。而报空表就是那条假账本身：折了却没记，回执上写着「一行都没折」，
    而池子已经变大了。``RunStore.folds_of`` 的三种结局不许塌成两种，服务面要做的
    只是**不把它塌掉** —— 所以这里判的是那句话逐字在回执里、且它**点了名**。
    """
    recorded = _admitted(settings, tmp_path, wired)
    _fold_into(wired, settings, recorded, V1)
    _drop_the_ledger(settings, recorded)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [(READ, _read(run_id=recorded))],
            handlers=wired.handlers,
        )
    )
    assert recorded in text
    assert "在册，但它的折入账不在" in text
    assert "不按零折入读" in text


def test_a_claim_of_nothing_when_the_ledger_names_a_row_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**e3 要关掉的那条假账，在结束面上的样子。**

    这一轮真的折了 V1（池子那一行落盘了、账上记了一笔），而结束时不报 ``folded``。
    修之前这样一份主张会把一条差异都算不出来 —— 对账按主张里的键逐条问池子，主张是
    空的，于是 ``completed`` 停表、账是平的，而池子已经变大了。

    三件事一起判，缺一不可：被拒、报文点出那个键、**这一轮仍然在跑**。第三件是
    「什么都不写」在结束面上的形式（``_rows`` 那一族在受理面判的是同一件事）。
    """
    recorded = _admitted(settings, tmp_path, wired)
    _fold_into(wired, settings, recorded, V1)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V2)),
                        status=RunStatus.COMPLETED.value,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert V1 in text
    assert "这一轮**真的折进去了**、而主张里没有" in text
    # 第二道闸（Delta 与主张）没开：两份记录说的是不同的这一轮，那时连「该拿哪份
    # Delta 对账」都还没有答案。两句都在同一个异常类型上，所以只能从措辞上读。
    assert "的账对不上" not in text

    row = wired.runs().get(recorded)
    assert row is not None
    assert row.is_stopped is False


def test_an_over_claim_on_the_tool_face_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """主张里多报一个句柄 ⇒ 拒。

    与上面那条是同一个闸的**另一个方向**，而它们的处置相反（一条要补一份主张、
    一条要去看为什么没落盘），所以两句报文必须分得开。这一条同时是**句柄**那一侧
    的判据：``folded`` 收的是一串字符串，交上去的也正是池子句柄。
    """
    recorded = _admitted(settings, tmp_path, wired)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V2)),
                        status=RunStatus.FAILED.value,
                        folded=[V1],
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert V1 in text
    assert "主张说折进去了、而账上没有" in text
    assert "的账对不上" not in text

    row = wired.runs().get(recorded)
    assert row is not None
    assert row.is_stopped is False


def test_a_round_whose_ledger_is_gone_cannot_be_closed(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """账本不在时**结束被拒**，而且绝不按零折入读完。

    报一份空账会让这一轮照常停表 —— 而那一份 ``completed`` 在盘上与「真的什么都没
    折」逐字相同。所以这里判三样：被拒、那句话逐字在回执里、以及这一轮仍然在跑。

    ``run_fold`` 那几行**故意留着**（``_fold_into`` 写下去的那一行还在）：这个状态
    正是「折过了、而账读不出来」，把它造得跟「什么都没折过」一样会让这一条判不出事。
    """
    recorded = _admitted(settings, tmp_path, wired)
    _fold_into(wired, settings, recorded, V1)
    _drop_the_ledger(settings, recorded)
    assert _streams(settings, recorded) == (0, 1)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V2)),
                        status=RunStatus.COMPLETED.value,
                        folded=[V1],
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert recorded in text
    assert "在册，但它的折入账不在" in text
    assert "不按零折入读" in text

    row = wired.runs().get(recorded)
    assert row is not None
    assert row.is_stopped is False


def test_an_empty_delta_with_a_completed_ending_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``completed`` 配一份每一格都空的 Delta ⇒ 走 ``ending_refusal``。

    §B17 条件 2 要求的是**零新增**有个归宿，不是零产出可以不写。「这一轮走完了、
    什么都没变」写成那一批 ``unchanged`` / ``held`` / ``blocked`` 的处置行，才与
    「压根没跑」在报告上分得开 —— 而这条拒绝住在 ``ports/runs.py``，服务面只负责
    把它原样交出去（丢掉它，调用方读到的是一句「结束失败」）。
    """
    recorded = _admitted(settings, tmp_path, wired)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta={"effects": []},
                        status=RunStatus.COMPLETED.value,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert "每一格都空" in text

    row = wired.runs().get(recorded)
    assert row is not None
    assert row.is_stopped is False


def test_closing_twice_with_the_same_ending_is_the_same_row(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """同一套结局与产出重放一次，拿回**同一行** —— 而两次之间池子与账都不动。

    ``RunStore.stop`` 那条幂等在这里原样成立，因为这一条工具不是「结束」的第二个
    入口：它只是给 ``stop`` 加了两道前置判据，而账平之后重放一次两道闸照旧平。
    ``stopped_at`` 比的是**第一次**停表的那个时刻（重放不重新取时刻），所以这一格
    相等就是「它没有第二次写下去」的判据。
    """
    recorded = _admitted(settings, tmp_path, wired)
    payload = _close(
        run_id=recorded,
        delta=_claiming((DeltaCell.HELD, V1)),
        status=RunStatus.COMPLETED.value,
    )

    first = _ok(_drive(settings, tmp_path, [(CLOSE, payload)], handlers=wired.handlers))
    second = _ok(_drive(settings, tmp_path, [(CLOSE, payload)], handlers=wired.handlers))

    assert first["stopped_at"] == second["stopped_at"]
    assert first["delta"] == second["delta"]
    assert _rows(settings) == 1


def test_closing_twice_with_another_ending_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """同一轮以**两种结局**结束过 —— 拒绝。

    判重放的依据是**这一套结局与产出**，不是这个方法被调过几次
    （``ports/runs.py::stopping_outcome``）。只比结局词、或者只比产出，都会让「同一轮
    以两种结局结束过」这件事在存储层看不出来，而两次调用都返回成功。
    """
    recorded = _admitted(settings, tmp_path, wired)
    _ok(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V1)),
                        status=RunStatus.FAILED.value,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V1)),
                        status=RunStatus.COMPLETED.value,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert "已经结束了" in text
    assert "产出与先前那一份不同" in text


def test_a_run_belonging_to_another_workspace_cannot_be_closed(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """跨工作区的那一行结束不了 —— 而它**读得到**，所以必须在这里被拒。

    与第 7 节那一条同一条纪律，只是这一条走的是**写**面：找到的是另一本账，所以
    结束它等于把本部署的判据用在一行别人的记录上。读的那一条（第 7 节）已经判过
    措辞，这里判的是``close_run`` **也**走了同一处守卫 —— 将来若有人给这一条工具
    另写一条「按 id 取行」，那一条会红。
    """
    moved = _row_of_another_workspace(settings, tmp_path, wired)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=moved,
                        delta=_claiming((DeltaCell.HELD, V1)),
                        status=RunStatus.FAILED.value,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert "another-market" in text
    assert settings.profile in text


@pytest.mark.parametrize(
    "folded",
    [[""], [V1, ""]],
    ids=["blank", "one-of-two-blank"],
)
def test_a_claim_holding_a_blank_handle_is_refused(
    folded: list[str], settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """一串句柄里有一个**空的** ⇒ 拒，而这一条走得到 ``_claim`` 自己那道闸。

    ## 为什么这一条只留这两个形状

    ``folded`` 那一格的出参 schema 是 ``list[str] | None``，所以 ``[None]`` /
    ``[1]`` / 裸字符串在进到函数体**之前**就被框架拒了（下一条判那个）。而
    ``[""]`` 与 ``[V1, ""]`` 是**合法的 ``list[str]``** —— 它们真的走进了 ``_claim``，
    这就是「一道闸在服务面上有没有执行器」的实测分界：同一个函数里按值的闸够得到、
    按类型的闸够不到。

    不判它的代价：一个空串会进 ``fold_claim`` 的集合，变成一个**永远对不上**的句柄
    —— 于是一次正常的结束被报成「主张多报了一条」。
    """
    recorded = _admitted(settings, tmp_path, wired)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V2)),
                        status=RunStatus.FAILED.value,
                        folded=folded,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert "folded" in text
    # 这一句**必须来自形状闸本身**，不是来自那道对账闸。两句话都可能含 ``folded``
    # （方向一那段话里就有「交给本函数的 ``folded``」），所以只判字段名会让一条放行
    # 空串的实现照样绿 —— 而「池子句柄」这个说法只出现在 ``_claim`` 的拒词里。
    assert "池子句柄" in text

    row = wired.runs().get(recorded)
    assert row is not None
    assert row.is_stopped is False


@pytest.mark.parametrize(
    "folded,wanted",
    [
        ([None], ("folded.0", "valid string")),
        ([1], ("folded.0", "valid string")),
        (f"src-{'1' * 16}", ("folded", "valid list")),
    ],
    ids=["none", "not-a-string", "a-bare-string"],
)
def test_a_claim_of_a_shape_the_framework_can_see_is_refused_by_it(
    folded: Any, wanted: tuple[str, ...], settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """三样别的形状各判一遍，而拒绝它们的是**框架** —— 如实测，不是判断。

    ## 这一条判的不是 ``_claim`` 里那两支

    实测拒词（照抄自运行）::

        folded.0 / Input should be a valid string   # [None] 与 [1]
        folded   / Input should be a valid list     # 裸字符串

    ``_claim`` 里那两支 ``isinstance`` 在服务面上**没有执行器**，与 ``_window`` 那一处
    同一条（``test_the_windows_own_shape_guard_still_fires`` 是它的另一半）。留在
    这里的理由是：**「这一格要的是什么」这件事要有一次服务面判据**，否则把
    ``folded`` 那格的出参类型放宽成 ``Any`` 会无声无息，而那正好是这三样形状进得来
    的唯一理由。

    ## 每一样各自钉一句

    ``[None]`` 与 ``[1]`` 落在**成员**上（``folded.0``），裸字符串落在**整格**上
    （``folded``）—— 所以两样都要判：只判整格的那种，会让一个「只收单值」的实现
    照样绿，而那时 ``[None]`` 会去撞 ``fold_claim`` 的 ``sorted``（对异构的表抛
    ``TypeError``：一次请求的形状错误长成一次崩溃）。

    裸字符串最值得单独判：它是可迭代的，于是 ``sorted(set("src-…"))`` 会把句柄拆成
    一个个字符 —— 不抛、不空，只是把一个句柄读成了十六个。
    """
    recorded = _admitted(settings, tmp_path, wired)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V2)),
                        status=RunStatus.FAILED.value,
                        folded=folded,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    for piece in wanted:
        assert piece in text, f"拒词要点名 {piece}"

    row = wired.runs().get(recorded)
    assert row is not None
    assert row.is_stopped is False


@pytest.mark.parametrize(
    "folded",
    [[None], [1], f"src-{'1' * 16}"],
    ids=["none", "not-a-string", "a-bare-string"],
)
def test_the_claims_own_shape_guard_still_fires(folded: Any) -> None:
    """``_claim`` 自己那两支形状闸 —— 服务面够不到它们，所以单独判一次。

    上一条判的是**框架**的拒词（那才是调用方看得见的那一句）。这一条判的是它背后那
    道闸本身：``_claim`` 是模块级的，库调用方（以及将来某个直接拼请求的组合根）拿它
    去解析一份配置值时，框架那道校验**不在这条路上**。

    不判它的代价是它可以被无声地删掉：删掉之后上一条三样形状在服务面上照样被拒
    （框架拒的），而 ``_claim("src-…")`` 会把那个字符串当可迭代对象拆成十六个字符
    —— 一次「主张里有一个句柄」会被读成「主张里有十六个句柄」，两次读数**都写进盘**，
    只是第二次是错的。
    """
    with pytest.raises(ToolError) as caught:
        _parse_claim(folded)
    assert "池子句柄" in str(caught.value), "拒词要说出这一格要的是一串池子句柄"


def test_a_claim_that_is_not_given_at_all_means_none() -> None:
    """``folded`` 不给 ⇒ 空元组（**不是**「没主张」）。与形状闸同族，所以判在一起。

    「不给」与「给一个空数组」是**同一件事**：这一轮什么都没主张。落成 ``()`` 之后它
    才去撞那道对账闸 —— 折入账里有行时它会红，空账时它过得去。
    """
    assert _parse_claim(None) == ()
    assert _parse_claim([]) == ()


def test_a_delta_that_is_not_an_object_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``delta`` 不是对象 ⇒ 拒，而拒绝它的是**框架**。

    一份形状不对的产出在这里**能**被读成「每一格都空的那一份」—— 于是它会撞上
    ``ending_refusal``（``completed`` 不许配空产出）或者被当作一份合法的 ``failed``。
    两种读法都在替调用方把一次**形状**错误答成一次**内容**问题，而两者要改的东西
    完全不同。

    实测拒词：``delta / Input should be a valid dictionary``。``_delta`` 里那句
    ``isinstance(declared, Mapping)`` 在服务面上**没有执行器**（下一条判它），所以这一
    条的判据落在这里：**要说出这一格要的是一个对象**。只判 ``"delta" in text`` 是不够
    的 —— ``ending_refusal`` 那句里也有 ``Delta``，而它说的是内容。
    """
    recorded = _admitted(settings, tmp_path, wired)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(run_id=recorded, delta="a delta", status=RunStatus.FAILED.value),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert "delta" in text, "拒词要点名坏的是哪一格"
    assert "dictionary" in text or "object" in text, "拒词要说出这一格要的是一个对象"

    row = wired.runs().get(recorded)
    assert row is not None
    assert row.is_stopped is False


@pytest.mark.parametrize(
    "delta", ["a delta", 5, ["effects"]], ids=["a-string", "a-number", "a-list"]
)
def test_the_deltas_own_shape_guard_still_fires(delta: Any) -> None:
    """``_delta`` 自己那道 ``isinstance`` 闸 —— 服务面够不到它，所以单独判一次。

    上一条判的是**框架**的拒词；这一条判的是它背后那道闸本身。三样形状都要判，因为
    这道闸是按 ``Mapping`` 判的，而 ``str`` 恰好是 ``Sequence``、``list`` 恰好能当
    ``in`` 的右边 —— 一个「按可迭代性读」的实现会在这三样上各错一次。

    不判它的代价是它可以被无声地删掉：删掉之后这三样会一路走到
    ``RunDelta.model_validate``，而实测它在三样上都报
    ``Input should be a valid dictionary or instance of RunDelta`` —— 与框架那句
    ``Input should be a valid dictionary`` 只差「or instance of …」半截。于是「闸还在」
    与「闸没了、靠模型层兜着」在服务面上几乎分不开，而两者说的话不同：一个说的是
    「这一格要一个对象」，另一个说的是「这一份拼不成一轮 Run 的 Delta」——后者把一次
    **形状**错误答成了一次**内容**问题，而调用方拿它去改的东西完全不同。
    """
    with pytest.raises(ToolError) as caught:
        _parse_delta(delta)
    assert "九格各答" in str(caught.value), "拒词要说出这九格各答的是一件什么事"


def test_a_delta_with_an_unknown_field_names_it(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """多写一格 ⇒ 点名那一格。

    ``RunDelta`` 是 ``StrictModel``（``extra="forbid"``），所以 pydantic 本来就会拒；
    这一条判的是**那句拒绝有没有被摊成「哪一个字段」**。不摊的话回执是一句
    ``Error executing tool close_run`` 外加整份输入的原文 —— 而字段名是调用方唯一
    能据以动手的东西（``_readable`` 的 docstring）。
    """
    recorded = _admitted(settings, tmp_path, wired)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta={"effects": [], "conclusions": []},
                        status=RunStatus.FAILED.value,
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert "conclusions" in text


def test_an_unknown_end_status_names_the_closed_set(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """结局词是一个闭集，其余一律拒并报出闭集。

    与 ``execution_mode``（``_mode``）逐字同款：这三个词说的是**这一轮算成了什么**，
    而一个不在集合里的词如果被静默接受，它此后会被读成一个新的结局 —— 报告上那句
    与三个真结局长得一样。报出闭集是因为调用方要据此改，而不是猜。
    """
    recorded = _admitted(settings, tmp_path, wired)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=recorded,
                        delta=_claiming((DeltaCell.HELD, V1)),
                        status="finished",
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    assert "finished" in text
    assert RunStatus.COMPLETED.value in text
    assert RunStatus.PARTIAL.value in text


def test_close_run_takes_exactly_these_four_and_no_others(wired: _Wired) -> None:
    """入参集合**恰好**是这一组 —— 与受理面那条判据同一条纪律。

    ``run_id`` 落在那份 21 名的禁名单里，而这一处**不是豁免** —— 与第 7 节末尾
    ``test_get_run_takes_exactly_the_id_and_nothing_else`` 逐字同一个理由：那张表管的是
    「**写下**一格事实的时候不准由载荷替它说话」。这一条的 ``run_id`` 是**地址**，
    它指着一行已经存在的记录，不是要给新的一行定一个身份。

    ``status`` **不在**禁名单里，而这是本片里最容易被读错的一格。它看着像
    ``state`` / ``revision``（一行的状态，禁），但它是**结局判词**：这一轮算成了
    ``completed`` 还是 ``partial``，除了做事的那一方没人签得出来 —— 服务端没有这份
    答案可以替它说话（``workspace`` 禁得掉，是因为盘上的 Scene 就是答案）。放它进来
    是**承认一处缺口**，而缺口的另一半（谁有权宣布一轮做完了）归 B01/B02。
    ``status`` 因此与 ``window`` 同款：它在允许表里，因为它答的是「这一次要做什么」，
    不是「服务端已经知道的某一格」。

    四格之外还该问的是 ``at``：它在禁名单里（时刻由服务端取），所以这一条也判了
    它**没有**出现 —— 与受理面同款（``domain.util.utc_now``）。
    """
    parameters = set(inspect.signature(wired.handlers[CLOSE]).parameters)
    assert parameters == {"run_id", "delta", "status", "folded"}
    assert parameters & _FORBIDDEN_PARAMETERS == {"run_id"}


# --- 10 · 连续两轮：条件 1 的端到端，与条件 3 后半的 idle ------------------------
#
# 前九节各自判一格：受理、拒绝、读回、结束。这一节判**两轮之间**的那件事 ——
# §B17 条件 1 的主语是「同一 Workstream **连续两个** manual Run」，而前九节里
# 「第二个」从来没有出现过：所有「提交两次」的用例都用**同一个键**，于是盘上
# 始终只有一行 Run。
#
# 这一节同时是条件 3 后半（「完成后在 manual 模式下保持 idle，绝不自行扩下一批」）
# 的第一条行为判据。那一句在此之前**不可能**有行为判据：先得能把一轮 Run 结束掉，
# 而那个面到 e4 才存在（收口矩阵条件 3 的缺口 2 逐字写着这一条依赖）。
#
# ## 「复用第一轮已沉淀事实」在今天读作什么
#
# 逐字读是「第二轮读到**第一轮**记下的东西」。而「第一轮」在结构上没有对应物：
# ``Observation`` 不带 ``run_id``、``RunDelta`` 不带轮次来源（冻结的设计记录
# ``docs/B17_DESIGN_SLICES_2026-09-28.md`` 逐字：「『复用第一轮的沉淀』实际是
# 『复用整个工作区账本此刻的沉淀』」）。这一节因此**不假装**它判得动那句话，
# 而是把今天这一层实际在做的事判出来：**读数由账决定，不由轮次决定** ——
# 而这句话是判得动的，方法是拿两个不同的 Run 去读同一本账，比读数是否逐字相同。
#
# 哪一轮记下了哪一格，要等「哪一轮记了哪一格」落盘才谈得上 —— 那是条件 7 的正面
# 问题（``application/run_increment.py`` 第七节逐字：本片不为它猜一个形状）。

#: 第二个键。与 :data:`KEY` 一样是一个说得清来源的名字，而两个键不同是本节的
#: **前提**：同一个键重发拿回的是同一行，那样盘上永远只有一轮。
KEY2 = "run-2026-09-28-b"

#: 一份散文建议。它进 ``RunDelta.recommendations`` —— 条件 3 后半的第二条判据拿它
#: 判「建议不被自动执行」（类型层那一条在 ``tests/test_run_delta.py:296``：那一格
#: 只装字符串，所以「把它喂回受理面」在类型上就写不出来）。
NEXT_ROUND_NOTE = "下一轮去看苏格兰那两家 —— 等英国的先跑完"


def _second_round(settings: Settings, tmp_path: Path, wired: _Wired) -> str:
    """受理**第二**轮（另一个键），返回它的 id。

    与 :func:`_admitted` 分开写而不是加一个 ``key=`` 参数：两个函数的差别是
    「第一轮」与「第二轮」这一件事，而一个带缺省参数的版本会让「这里传的是第几个键」
    在读的时候看不出来 —— 而本节每一条判据的前提正是那个键。
    """
    admitted = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _payload(idempotency_key=KEY2))],
            handlers=wired.handlers,
        )
    )
    return admitted["run_id"]


def _settle_every_face(wired: _Wired, settings: Settings) -> None:
    """七个面各记一条观测 —— 一轮「真的沉淀过」的样子。

    只记 ``SOURCE_COVERAGE`` 那一面是不够的（``_observe`` 的缺省值就是它）：其余
    六面会是 ``NEW``，而 ``NEW`` 是「从没看过这一面」—— 那一档与复用无关，于是
    「第二轮复用了第一轮的沉淀」这句话在那份读数里没有主语。第 8 节那条判据
    （``:1911`` 那一段）逐字写着同一条理由。
    """
    for aspect in ALL_ASPECTS:
        _observe(wired, settings, aspect=aspect)


def _reservations(settings: Settings) -> int:
    """预算账上**预留**过几行。

    表不在时返回 **0** —— 那不是「读不到」，那是**这一层连账本都没碰**：
    ``submit_run`` 只在有窗口时展开 ``ledger()``（它的 docstring 逐字：「没有窗口的
    受理读不到金额，那次 ``ledger()`` 就是一次白建的库」），而建库这件事本身就会
    建出那张表。所以「表不在」与「表在而零行」在这一条判据上**是同一个答案**，
    与 ``ports/runs.py`` 那种「读不出来绝不返回零」的场合不同 —— 那里的零是一笔
    假账，这里的零是一句真话。
    """
    with sqlite3.connect(settings.database_path) as conn:
        present = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type = 'table' AND name = 'budget_reservation'"
        ).fetchone()
        if not int(present[0]):
            return 0
        return int(
            conn.execute("SELECT COUNT(*) FROM budget_reservation").fetchone()[0]
        )


def test_the_second_round_reads_the_book_and_not_the_first_round(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """两轮各自一行；第二轮的读数与「是哪一轮在读」无关 —— 由账决定。

    这是 §B17 条件 1 前半句在服务面上的端到端落点，而它判的**不是**「第二轮复用了
    第一轮记下的那一格」（那一句今天读不了，见本节开头），而是它在这一层实际读作
    的那句话：**读数只由账本此刻的样子决定**。判法是拿两个不同的 Run 去读同一本账，
    比两份读数是否**逐字**相同 —— 两个 ``cells`` 逐格、``counts`` 四档、``inputs``
    两格都要相同。只比 ``len(cells)`` 的话，一个把同一个格子读成另一档处置的实现
    照样绿。

    **第一轮必须真的结束过**（走 :data:`CLOSE` 的两道对账闸，而不是夹具直接
    ``stop``）：判据要说的是「一轮结束之后，这份读数照旧」，而没结束的那一份读数
    答不了这后半句。

    两次读数取在**不同**的 Run 上，所以 ``run_id`` 那一格必须不同 —— 这一条把
    「它读的是被点名的那一轮」也一并判了（一个恒回同一份的实现在这里会露出来：
    两次的 ``run_id`` 会相同）。
    """
    first = _admitted(settings, tmp_path, wired)
    _settle_every_face(wired, settings)
    _fold_into(wired, settings, first, V1)
    while_running = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=first))],
            handlers=wired.handlers,
        )
    )
    assert while_running["run_id"] == first
    # 上一轮真的沉淀过：七格里六格复用、一格缺（库里一条证据都没有 —— ``MISSING``
    # 是 ``SOURCE_COVERAGE`` 那一格唯一说得出口的处置，见增量面第二节）。
    # **四档都写出来**（含两个 0）：``counts`` 的承诺就是四档都在，缺的那一档是 0
    # —— 一条把零档省掉的实现会让「这一档没有」与「这一档是零」同形。
    assert while_running["counts"] == {
        IncrementVerdict.REUSE.value: len(ALL_ASPECTS) - 1,
        IncrementVerdict.NEW.value: 0,
        IncrementVerdict.MISSING.value: 1,
        IncrementVerdict.EXPIRED.value: 0,
    }

    _ok(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=first,
                        delta=_claiming((DeltaCell.UNCHANGED, V1)),
                        status=RunStatus.COMPLETED.value,
                        folded=[V1],
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )

    second = _second_round(settings, tmp_path, wired)
    assert second != first, "两个键受理出了同一个 id —— 那盘上就只有一轮"
    assert _rows(settings) == 2
    after = _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=second))],
            handlers=wired.handlers,
        )
    )
    assert after["run_id"] == second
    assert after["cells"] == while_running["cells"]
    assert after["counts"] == while_running["counts"]
    assert after["inputs"] == while_running["inputs"]


def test_a_finished_manual_round_opens_no_second_one_by_itself(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """一轮结束之后，盘上仍然只有那一行 —— 没有任何东西自行扩下一批。

    §B17 条件 3 后半句（「完成后在 manual 模式下保持 idle，绝不自行扩下一批」）在
    服务面上的第一条行为判据。它在此之前**不可能**有：先得能把一轮结束掉。

    判两半，缺一不可：

    * **结束本身也不开下一轮**（``close_run`` 不顺手提一个）；
    * **写在那份 Delta 里的散文建议不被执行**。``recommendations`` 这一格装的是
      散文（类型层判据在 ``tests/test_run_delta.py:296``），而一条「照着建议再开一轮」
      的实现会让这一格变成一个触发器 —— 条件 3 那句话说的正是它不许变成触发器。
      只判第一半的话，一个「读完 recommendations 就受理」的实现照样绿。

    数的是**库里的行**（:func:`_rows`），不是处理器手上的对象：一次「先受理再报错」
    与一次干净的拒绝在回执上可能一模一样，而前者在盘上留下一行没有人打算要的 Run。
    """
    first = _admitted(settings, tmp_path, wired)
    _fold_into(wired, settings, first, V1)
    ending = _ok(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=first,
                        delta={
                            **_claiming((DeltaCell.UNCHANGED, V1)),
                            "recommendations": [NEXT_ROUND_NOTE],
                        },
                        status=RunStatus.COMPLETED.value,
                        folded=[V1],
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    # 那份建议**确实写下来了** —— 不然这一条判的是「没有建议」而不是「建议没被执行」。
    assert ending["delta"]["recommendations"] == [NEXT_ROUND_NOTE]
    assert _rows(settings) == 1


def test_resubmitting_the_same_key_settles_nothing_into_the_pool(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """§B17 条件 1 后半句的前半：**重复提交同一 idempotency key 不重复池子**。

    收口矩阵里这一格原本逐字是「零判据」：既有的「提交两次」用例都在判**受理那一行**
    （``test_the_same_key_twice_is_the_same_row_and_not_a_second_round`` 断言
    ``_rows(settings) == 1``），而条件 1 说的是**池子**。两者不是同一件事：一个把幂等
    做在 Run 那一行、却让第二轮照跑一遍的实现，受理面那一条判据照样绿，而池子会被
    折第二次 —— 池子那一行是 upsert，所以报告上它看起来「还是那一行」，多出来的是
    **账上的第二笔**。

    所以这一条数**两本账**（:func:`_streams`：折入账本身、折进去的那几行），分开报。
    它们在同一次写里（``ports/pool.py`` 的 ``fold=``），所以一个把两次写拆开的实现
    会在这里露出一个「一个零、一个非零」的形状 —— 而那个形状与「真的折了一行」在
    合计数上同形。

    判据在**第一轮结束之后**取，因为这才是条件 1 说的那个动作：一轮跑完，同一个人
    把同一份请求又发了一遍。没结束的那一轮上重发会撞上另一条判据（增量面拒绝为已
    结束的一轮算增量），两件事就搅在一起了。
    """
    first = _admitted(settings, tmp_path, wired)
    _fold_into(wired, settings, first, V1)
    _ok(
        _drive(
            settings,
            tmp_path,
            [
                (
                    CLOSE,
                    _close(
                        run_id=first,
                        delta=_claiming((DeltaCell.UNCHANGED, V1)),
                        status=RunStatus.COMPLETED.value,
                        folded=[V1],
                    ),
                )
            ],
            handlers=wired.handlers,
        )
    )
    before = (_rows(settings), _streams(settings, first))
    assert before == (1, (1, 1))

    again = _ok(
        _drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers)
    )
    assert again["run_id"] == first, "同键拿回的必须是同一行"
    assert (_rows(settings), _streams(settings, first)) == before


def test_admitting_a_round_reserves_nothing_from_the_budget(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """§B17 条件 1 后半句的后半：**重复提交同一 idempotency key 不重复成本**。

    这一格的旧读数是「只有**账本层**的判据，没有一条经过 ``submit_run`` 这条面」
    （收口矩阵条件 1 缺口 3）。而两件事都要：账本的判据说 ``reserve`` 是两段式的，
    这一条说的是**受理这一步根本不预留** —— 受理是一份信封，不是一次花钱的动作
    （``submit_run`` 的 docstring 逐字：「受理只写下一行 Run，不派发任何东西、不
    **写**预算账本」）。

    三次受理各判一件事，而它们各自能红在**不同**的实现上：

    * 第一轮（有窗口时的那条路走不到，这里是 manual）；
    * **同键重发**：一个「每收到一次请求就预留一次」的实现会在第二次露出来；
    * **另一个键**：一个「受理就按信封预扣整份预算」的实现会在这里露出来 ——
      它不过这一条，而它过得了上面那条同键的。

    读的是**库里的行**（:func:`_reservations`）：账本那一侧没有「一共预留过几次」
    这种读法（``open_reservations`` 读的是**还挂着**的那些，而已结算的预留不在里面），
    所以「不重复成本」这个量的量具只有表本身。
    """
    _admitted(settings, tmp_path, wired)
    assert _reservations(settings) == 0, "受理一轮就预留了额度"
    _ok(_drive(settings, tmp_path, [(TOOL, _payload())], handlers=wired.handlers))
    assert _reservations(settings) == 0, "同键重发又预留了一次"
    _second_round(settings, tmp_path, wired)
    assert _reservations(settings) == 0, "另一轮受理按信封预扣了整份预算"


# --- 11 · watch：它的来源是到期/信号（切片 f）-----------------------------------
#
# §B17 条件 5 逐字：「watch 只为到期/信号对象创建维护 Run；SavedView 单纯查询不创建
# Run。」这一节判的是它的**前半句**，而「**只**为」这两个字就是这一节的形状：
# 有到期 ⇒ 受理（判据 1、3），没有到期 ⇒ 拒**且盘上零行**（判据 2）。
#
# 后半句今天**没有主语**：``SavedView`` 在 ``marketops/`` 与 ``tests/`` 里零命中，
# 收口矩阵把 ``SavedView`` / ``Watch`` / ``ActionItem`` 三个对象逐字划给 B24。所以它
# 换了主语 —— **读不创建 Run**（判据 4）—— 与第 10 节把「第一轮」换成「账」是同一种
# 处置：主语不在时，判这一层**实际在做**的那件事，并把「主语不在」写下来。
#
# ## 「创建」这个动词落在哪儿，以及它**不**落在哪儿
#
# 落在「受理一行 Run」上（条件 4 那句「不创建下一 Run」的同一口径）。这一节**不**声称
# 「这条 Run 跑起来真的去维护那些到期格子」：全仓没有把一行 ``Run`` 变成工作中的
# 执行器 —— Run 内部跑 Job / WorkItem / 执行器 attempt 那一层接的是别的面。条件 5 逐字
# 说的是「**创建**维护 Run」，而这一节判的正是这一个动词。
#
# ## 为什么这一节要写一份账号清单
#
# ``what_is_due`` 不知道世界上有谁（它的 docstring 逐字：「一个会自己发现主体的选择器
# 必然也会自己**发明**主体」），所以「有没有到期的」这句话要先有一个**主体来源**。
# 两个来源各自的判据住在别处（``name`` 那条在 ``tests/test_mcp_maintenance_change.py``，
# ``cohort`` 那条在 ``tests/test_mcp_cohorts.py``）；这一节只借 ``name`` 那条摆出主体
# —— 它判的是受理面**读不读**那个来源，不是那两条路各自对不对。

#: watch 那一条受理专用的键。与 :data:`KEY` 分开：判据 5 要问「同键重发会不会创建
#: 第二条」，而那个判据只有在一个**专用**的键上才读得清 —— 与 ``KEY`` 混用会让第 1 节
#: 那些「盘上正好一行」的断言与这里的行数互相污染。
WATCH_KEY = "run-2026-09-28-w"

#: 那份清单的名字（``<inputs_dir>/<名字>.yaml``）。
WATCH_LIST = "known-accounts"

#: 清单里那一个主体。它与清单必须**逐字**相同，否则 ``what_is_due`` 看到的是「一个
#: 从没看过的格子」而不是「一个已经看过的格子」，于是判据 2 会在「拒了」这个结论上
#: **因为错的原因**而绿 —— 而它要判的恰恰是「看过了、所以不过期」。
WATCH_SUBJECT = "example-one"

#: 一份 operator 手写的清单。形状逐字取自
#: ``tests/test_mcp_maintenance_change.py::ACCOUNTS`` —— 两份都走同一个
#: ``known_account_dossiers`` 装载器，编一个字典是编不过去的。
WATCH_ACCOUNTS = """accounts:
  - account_id: example-one
    organisation: Example One Ltd
    cohort: known_success
    domain: example-one.co.uk
"""


def _account_list(settings: Settings) -> str:
    """摆一份清单，返回它的名字。**直接写文件**，不经过工具。

    与 :func:`_seed_scene` 同一个形状：这一节要问的是「受理面读的是不是调用方指出的
    那一份」，而用导入那条工具去建它会让这个文件同时测另一条工具，红了之后也说不清
    是哪一条断了。
    """
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    (settings.inputs_dir / f"{WATCH_LIST}.yaml").write_text(
        WATCH_ACCOUNTS, encoding="utf-8"
    )
    return WATCH_LIST


def _watch(**overrides: Any) -> dict[str, Any]:
    """watch 那一次受理的载荷。三个缺省值就是它的**形态**：模式、主体来源、键。"""
    body: dict[str, Any] = {
        "execution_mode": "watch",
        "name": WATCH_LIST,
        "idempotency_key": WATCH_KEY,
    }
    body.update(overrides)
    return _payload(**body)


def _settle(
    wired: _Wired, settings: Settings, *, seen_at: datetime | None = None
) -> None:
    """把这个主体**七个面都看过一遍**，于是它没有一格到期。

    与 :func:`_observe` 的差别只有时刻那一格 —— 而那一格正是本节的量具：缺省是当下
    （判据 2 要的「刚刚看过」），判据 3 给它一个**过去**的时刻（那条信号要落在它之后、
    又不能落在将来）。两处都不能用 :func:`_observe`：它的 docstring 逐字写着它取当下
    的理由，而给它加一个参数会与那一段分家。

    「两小时前看过」在 ``RefreshPlan.uncalibrated_default()`` 下仍然是 ``CURRENT``
    （最短的那条节奏是 30 天），所以判据 3 那条信号之前的那一次拒，拒的是「没有到期」
    而不是「没有主体」。
    """
    for aspect in ALL_ASPECTS:
        wired.maintenance().put_observation(
            Observation(
                workspace=settings.profile,
                subject_kind="account",
                subject_id=WATCH_SUBJECT,
                aspect=aspect,
                observed_at=seen_at or utc_now(),
                source_ref=WATCH_KEY,
            )
        )


def _plan_handlers(settings: Settings, scenes: Any) -> dict[str, Any]:
    """产品环那一条**读**面的处理器表 —— 只为了判据 4 里 ``get_maintenance_plan`` 那一半。

    接法与 ``tests/test_mcp_cohorts.py::Session.run`` 逐字同款（那是它今天唯一在用的
    地方）：十一个形参里有三个 ``None``（那几个是**外面** —— 抓取器、解析器、额度账本），
    其余各给一个本部署真的在用的工厂。这一节只用得到 ``get_maintenance_plan``：**它算
    due**，是「最像该顺手开一个 Run」的那一个读者。
    """
    return product_loop_handlers(
        settings=settings,
        scenes=scenes,
        maintenance=default_maintenance_store(settings),
        assets=default_asset_store(settings),
        relations=default_relation_store(settings),
        accounts=default_account_pool_store(settings),
        cohorts=default_account_cohort_store(settings),
        review=None,
        acquisition=None,
        router=None,
        documents=None,
    )


def test_a_watch_run_is_admitted_when_a_subject_has_a_face_due(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """有到期 ⇒ 受理，盘上**一行**，而回执的 ``window`` 是 ``None``。

    这是条件 5 前半句的正面。三格各自判一件事：

    * ``execution_mode`` 逐字是 ``watch`` —— 一条受理成功却把模式写回 ``manual`` 的
      实现，会在这一格露出来（而盘上那一行什么都齐全，只有模式不对）；
    * ``window`` 是 ``None`` —— watch 的三个阈值由 due 判定回答，不由一次受理写下来
      （``ports/runs.py`` 逐字）。这一格与 ``bounded_auto`` 的区别正在这里：那一份
      受理时窗口是必填的；
    * 盘上**真的**有一行（走 :func:`_rows` 数库，不看处理器返回的对象）。

    最后另开一个连接把那一行读回来：拿处理器手上的对象再看一遍证明不了写盘。
    """
    _seed_scene(settings)
    _account_list(settings)

    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _watch())],
        handlers=wired.handlers,
        scenes=wired.scenes,
    )
    receipt = _ok(outcome)

    assert receipt["execution_mode"] == "watch"
    assert receipt["window"] is None, "watch 的受理里不该有一份授权窗口"
    assert receipt["idempotency_key"] == WATCH_KEY
    assert _rows(settings) == 1

    stored = SqliteRunStore(settings.database_path).get(receipt["run_id"])
    assert stored is not None, "受理成功了，而盘上读不到那一行"
    assert stored.spec.execution_mode is ExecutionMode.WATCH
    assert stored.spec.window is None


def test_a_watch_run_with_nothing_due_is_refused_and_writes_nothing(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """没有到期 ⇒ 拒，盘上**零行** —— 条件 5 前半句里那个「**只**为」。

    这一条是本节最要紧的那一条：一个「无条件受理 watch」的实现（把
    ``admission_refusal`` 里那段删掉、又不补核验）在判据 1 上照样绿 —— 它当然受理
    得下来 —— 而只有这一条会红。

    「拒了」与「先受理再报错」在回执上一模一样，而后者在盘上留下一行没有人打算要的
    Run，所以这一条**两半都判**：拒词说得出缺的是什么，且 :func:`_rows` 是零。

    拒词里那一句「N 个主体」是这一条判得住的前提：它让回执说得出**看的是哪一批**
    ——而一个把「没给主体」与「主体都没到期」读成同一句话的实现，会在这里露出来。
    """
    _seed_scene(settings)
    _account_list(settings)
    _settle(wired, settings)

    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, _watch())],
        handlers=wired.handlers,
        scenes=wired.scenes,
    )
    text = _denied(outcome)

    assert "到期或信号" in text, "拒词没有说清它的来源是什么"
    assert "一条都没有" in text, "拒词没有说清这一次缺的是哪一样"
    assert "1 个主体" in text, "拒词没有说清它判的是哪一批主体"
    assert _rows(settings) == 0, "被拒的一次 watch 受理在盘上留下了一行"


def test_a_change_signal_is_what_makes_a_watch_run_admissible(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """条件 5 逐字里的那个「或**信号**」：两半都在这一条里。

    摆法是从「没有到期」出发、只加一条信号 —— 于是这一条与上一条的差别**只有那一条
    信号**，而两半各自判一件事：

    * **没有信号时拒**（与上一条同一个状态，只是观测时刻挪到两小时前）；
    * **有了信号之后受理**，而那条信号落在观测**之后**（``what_is_due`` 逐字：「只要
      还没有人看过，这条信号就一直把这一格留在到期清单上」）。

    信号不取将来时刻：一条落在未来的信号在测试里是颗定时炸弹，而它要判的这句话
    （「观测之后发生的变更让这一格重新到期」）在过去的时刻上完全成立。
    """
    _seed_scene(settings)
    _account_list(settings)
    seen_at = utc_now() - timedelta(hours=2)
    _settle(wired, settings, seen_at=seen_at)

    before = _drive(
        settings,
        tmp_path,
        [(TOOL, _watch())],
        handlers=wired.handlers,
        scenes=wired.scenes,
    )
    assert "到期或信号" in _denied(before)
    assert _rows(settings) == 0

    wired.maintenance().put_signal(
        ChangeSignal(
            workspace=settings.profile,
            subject_kind="account",
            subject_id=WATCH_SUBJECT,
            aspect=Aspect.SOURCE_COVERAGE,
            signalled_at=seen_at + timedelta(hours=1),
            reason="场地的官方目录页换了一版",
        )
    )

    receipt = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _watch())],
            handlers=wired.handlers,
            scenes=wired.scenes,
        )
    )
    assert receipt["execution_mode"] == "watch"
    assert _rows(settings) == 1, "有一条信号落在观测之后，而这一格没有到期"


def test_the_reads_do_not_create_a_run(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """条件 5 后半句**换了主语之后**的落点：**读**不创建 Run。

    逐字那句话的主语是 ``SavedView``，而它在 ``marketops/`` 与 ``tests/`` 里零命中
    （收窄矩阵把三个对象划给 B24）。所以这一条判的是这一层实际在做的那件事，而它取
    的是**两个最像会越界的读者**：

    * ``get_run_increment`` —— 它读**整本维护账**，现算一份「这一轮该补哪些格」；
      一个把「算出来有到期」与「开一轮去补」缝在一起的实现会在这里露出来；
    * ``get_maintenance_plan`` —— 它**算 due**，是产品环里离「该派发了」最近的那一条
      读面（它走的是**另一个**处理器表，所以这里另起一份驱动器）。

    两条都先说得出「这一节真的有到期的东西可看」：前一条先受理一轮 watch（有到期才
    受理得下来），后一条那份回执里 ``due_count`` 必须非零 —— 否则「读不创建 Run」会
    因为**什么都没发生**而绿，而那正是这一条要防的假绿。

    ## 量的是三本账，不只是 Run 的行数

    「读不创建 Run」很容易被读成「数 Run 那一张表就够了」，而只数 Run 行的版本放得
    过另一半：**算 due 的读面顺手把算出来的格子记成「已看过」**。那不是创建 Run，
    但它比创建 Run 更阴 —— 它让下一次判定变成「没有到期」，于是维护 Run 从此不会再
    被创建，而盘上「这件事做过了」的依据是一行没有人派发过的观测。所以两条读面各比
    一次 :func:`_book`（Run / 观测 / 信号三本），而不是各比一次 ``_rows``。
    """
    _seed_scene(settings)
    _account_list(settings)
    admitted = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _watch())],
            handlers=wired.handlers,
            scenes=wired.scenes,
        )
    )
    assert _rows(settings) == 1

    # 一条读账的读面：三次读数（Run / 观测 / 信号）里一行都不许多出来。
    before = _book(settings)
    _ok(
        _drive(
            settings,
            tmp_path,
            [(INCREMENT, _increment(run_id=admitted["run_id"]))],
            handlers=wired.handlers,
            scenes=wired.scenes,
        )
    )
    assert _book(settings) == before, "读增量那一面写了行"

    # 一条**算 due** 的读面。它必须先真的算出了到期的东西，否则这一条是空的。
    plan = _ok(
        _drive(
            settings,
            tmp_path,
            [("get_maintenance_plan", {"name": WATCH_LIST})],
            handlers=_plan_handlers(settings, wired.scenes),
            scenes=wired.scenes,
        )
    )
    assert plan["due_count"] > 0, "这一条判的读者什么都没算出来，于是它判不了任何事"
    assert plan["subjects"]["count"] == 1
    assert _book(settings) == before, "算 due 的那条读面写了行"
    assert _rows(settings) == 1, "算 due 的那条读面创建了一个 Run"


def test_the_same_watch_key_sent_twice_leaves_one_row(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """重发同一条 trigger ⇒ 盘上仍是一行，拿回的是**同一个** id。

    条件 4 那句「重放 trigger 不重复创建」在 watch 上的落点：trigger 是 watch 的来源，
    而来源是**事件**（一条信号、一次到期的判定）。一个把「每一次受理」当成一次新事件
    的实现会在这一条上露出来 —— 而它在判据 1 上照样绿。

    比 id 而不是只比行数：行数相同而 id 不同，说明第一次那一行还在、重发又写了一行，
    而报告上「盘上有一行 watch」这句话两次都成立。
    """
    _seed_scene(settings)
    _account_list(settings)

    first = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _watch())],
            handlers=wired.handlers,
            scenes=wired.scenes,
        )
    )
    second = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _watch())],
            handlers=wired.handlers,
            scenes=wired.scenes,
        )
    )

    assert second["run_id"] == first["run_id"]
    assert _rows(settings) == 1


def test_a_watch_run_carrying_a_window_is_still_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """watch **带**窗口 ⇒ 拒，而拒因是**域层**那条跨格校验器。

    这一条证明判据 1 打开的**不是**一个口子：``ports/runs.py`` 里删掉的那一段说的是
    「它的来源还没有接」（那句话今天不再成立），而「watch 不得带窗口」是另一句、
    住得也更深 —— 它在 ``domain/run.py::RunSpec._window_agrees_with_the_rest`` 里，
    与模式无关地判「这两格说的不是一件事」。

    这里是**有到期行**的状态，所以拒因只可能是那一格：一个把域层那条校验器也放宽的
    实现，会在这一条上被抓住，而在判据 1 上不会。
    """
    _seed_scene(settings)
    _account_list(settings)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _watch(window=_window()))],
            handlers=wired.handlers,
            scenes=wired.scenes,
        )
    )

    assert "带了授权窗口" in text
    assert "由 due 判定回答" in text, "拒词没有说清 watch 的阈值该由谁回答"
    assert _rows(settings) == 0


@pytest.mark.parametrize(
    ("given", "value"), [("name", WATCH_LIST), ("cohort", "known_success")]
)
def test_a_subject_source_on_a_mode_that_never_reads_it_is_refused(
    given: str, value: str, settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``manual`` 上给主体来源 ⇒ 拒，盘上零行。

    这一条堵的是**静默吞参数**：``name`` / ``cohort`` 在别的模式上一个字都不读，
    而一次被无声忽略的参数会让调用方以为它约束了什么 —— 报告上「受理成功」这句话
    两次都成立，回来的那一行却与不带这个参数时**逐格相同**。同一条理由已经在
    ``window`` 上判过一次（那一次由 ``RunSpec`` 的跨格校验器挡，判据在
    ``tests/test_run_window.py``）；这里判的是它旁边那两格。

    两个参数各测一次，因为拒词要点出**是哪一个**：``cohort`` 与 ``name`` 的处置
    不同（一个是换一份冻结名单，一个是换一份手写清单），而一句「参数给错了」会把
    调用方送回原点。

    与判据 1 配着读：同一次受理，只把模式从 ``watch`` 换成 ``manual``，就从「受理
    成功」变成「拒」。两个参数本身没有变坏 —— 变的是**这一次受理有没有那个来源**。
    """
    _seed_scene(settings)
    _account_list(settings)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _payload(**{given: value}))],
            handlers=wired.handlers,
            scenes=wired.scenes,
        )
    )

    assert f"``{given}``" in text, "拒词没有点出是哪一个参数"
    assert "只在 ``execution_mode='watch'`` 上有意义" in text
    assert "'manual'" in text, "拒词没有说清这一次是什么模式"
    assert _rows(settings) == 0


def test_a_watch_run_on_a_paused_scene_is_refused(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """暂停的场景上 watch ⇒ 拒，而理由是**这个场景今天不派发新的工作**。

    这一条钉住的是 ``_require_due`` 里那次翻译：``due_work`` 对非 ``ACTIVE`` 的场景
    抛 ``ConfigError``（它说的是「部署/配置写错了」），而这里要交出去的是一句「这一
    次提交走不通」—— 原文照旧、类型换成本层这一句。**不翻译**的话，调用方读到的是
    一个「配置错误」的形状，处置被读成「去改部署」，而真相是这个场景今天暂停了，
    处置是去把它恢复或者等它恢复。

    暂停与「没有到期」是两件事，而它们在小心里长得一样（两条都拒）：这一条不摆
    任何观测、也不摆任何信号，所以它拒的**不是**「这一批主体没到期」—— 走到那一句
    的前提是先通过了状态那一关。判据 2 才是那个状态为 ``ACTIVE`` 的对照。

    清单必须摆在盘上：``_subjects`` 在 ``admit_run`` **之前**跑，清单不在时报的是
    「这一份名单读不出来」—— 那也是拒，但理由错了，而这一条要判的正是理由。
    """
    _seed_scene(settings, state=SceneState.PAUSED, pause_reason=PauseReason.OPERATOR)
    _account_list(settings)

    text = _denied(
        _drive(
            settings,
            tmp_path,
            [(TOOL, _watch())],
            handlers=wired.handlers,
            scenes=wired.scenes,
        )
    )

    assert "paused" in text, "拒词没有点出这个场景当前是什么状态"
    assert "不接受新的派发" in text, "拒词没有说清这一次为什么走不通"
    assert _rows(settings) == 0


# --- 12 · preview 与受理说的是同一轮 -------------------------------------------
#
# 前十一节各判一条工具（受理 / 读回 / 结束），这一节判的是**两条工具之间**：同一个
# 请求在 ``submit_run`` 与 ``preview_run`` 上错开的那一刻，两边说的是不是同一件事。
#
# 这一段前奏在服务面上**只有一份**（``_preamble`` + ``_Preamble.round_of``），而
# 「只有一份」这件事在库层看不出来 —— ``tests/test_run_preview.py`` 钉的是同一个
# 生产者造的两份规格指纹相等，它看不见服务面。两个处理器各自拼一份前奏、各自都自洽
# 的实现，过得了那一份判据，也报得出同形的报告；漂移只在把两边的读数摆在一起时才
# 露出来，而漂移的形态是「preview 说这一轮是 X，提交下去的是 Y」。

PREVIEW = "preview_run"


def test_the_preview_and_the_submission_describe_the_same_round(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """同一次请求：先 preview 再 submit，两边的四格说的是同一轮。

    次序是有意的 —— **先 preview**。那时盘上还没有那一行，所以这一条同时判到
    「preview 一行都不写」：它读到的 ``verdict`` 是 ``open``、``existing`` 是
    ``None``，而提交之后 ``_rows`` 才从 0 变成 1。反过来先提交再 preview，第二次读
    到的必然是重放，那四格的相等就变成「两份读数都在读盘上那一行」的产物，与前奏拼
    得对不对不是同一句话 —— 同一个键下，两份读数本来就该指同一行。

    四格各钉一种漂移，少一格就有一种病读不出来：

    * ``run_id`` —— 内容寻址的那个键（``run_id(workspace, idempotency_key)``）。
      它不等，意味着两次调用读的**不是同一个工作区或同一个幂等键**。
    * ``budget_and_stop.this_round.budget`` 对 ``budget`` —— 信封。漂移的形态是
      「preview 说这一轮能用 20，提交下去只给了 8」，而两边的判据各自都是绿的。
    * ``cutoff`` —— 截止。与信封同一个道理，而它漂移的后果是「这一轮什么时候失效」
      在两个面上是两个时刻，且两个时刻都合法。
    * ``plan.plan.digest`` 对 ``plan_ref`` —— 计划。它不等，说的是 preview 报的复用
      与缺口是对着**A 计划**算的，而受理写下的那一行记的是 **B 计划**。

    比较的是**格**而不是整块：``this_round`` 与回执说的是同一个信封的两种形状
    （一处是报告里的一格，一处是那一行的投影），逐字节相等不是这一条要判的事。
    """
    _seed_scene(settings)

    outcome = _drive(
        settings,
        tmp_path,
        [(PREVIEW, _payload()), (TOOL, _payload())],
        handlers=wired.handlers,
        scenes=wired.scenes,
    )
    report = _ok(outcome, 0)
    receipt = _ok(outcome, 1)

    stop = report["budget_and_stop"]
    assert stop["verdict"] == "open", "preview 这一侧本该还没受理过任何一次"
    assert stop["existing"] is None, "preview 写的行不该存在 —— 它一行都不写"
    assert _rows(settings) == 1, "两次调用之后盘上应当**只有**受理写下的那一行"

    assert report["run_id"] == receipt["run_id"]
    assert stop["this_round"]["budget"] == receipt["budget"]
    assert stop["this_round"]["cutoff"] == receipt["cutoff"]
    assert report["plan"]["plan"]["digest"] == receipt["plan_ref"]


def test_a_preview_after_the_submission_reads_the_row_it_just_wrote(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """受理之后再 preview 同一个键 ⇒ ``replay``，读到的就是刚写下的那一行。

    这是 ``tests/test_run_preview.py`` 第 5 节那条最承重的判据在**服务面**上的另一半：
    库层证明的是「重放排在链的阈值之前」，这一条证明的是 preview 读的那一本 Run 记录
    与受理写的那一本是**同一本**。

    两本库接错的形态很安静：preview 借到另一个 ``database_path`` 上的 store，于是它
    永远读不到刚受理的那一行 ⇒ 每一次预览都报 ``open``。而 ``open`` 是一个**合法**的
    读数 —— 报告上没有一格显得可疑，两份判据各自也都绿。所以这里必须比对那一行的
    **身份**（``run_id``）与它**此刻在不在**（``existing`` 不是 ``None``）：只判
    ``verdict`` 的话，「重放判定恒不触发」与一个正确的实现在第一次调用上完全同形。

    用带窗口的请求（``_auto()``）而不是默认的 ``manual``：链走到哪那一格只有在
    ``spec.window`` 在场时才是**一个可以被问到的问题**（没有窗口 ⇒ 它恒为 ``None``，
    判不出任何事）。重放那一步在链之前说话，所以这里链的读数必须缺席 —— 而在一份
    没有窗口的请求上它无论如何都缺席。

    注意报告里那三个键的**名字与读数的字段不是一一对应**的，这一条判据因此要点着键
    判：``budget_and_stop['chain']`` 是**窗口本身**（这条授权链的形状：上限、信封、
    到什么时候为止），``['progress']`` 才是**链走到哪了**。重放只让后者缺席 ——
    「没问链的读数」不是「没有链」。
    """
    _seed_scene(settings)
    receipt = _ok(
        _drive(settings, tmp_path, [(TOOL, _auto())], handlers=wired.handlers)
    )

    report = _ok(
        _drive(
            settings,
            tmp_path,
            [(PREVIEW, _auto())],
            handlers=wired.handlers,
            scenes=wired.scenes,
        )
    )

    stop = report["budget_and_stop"]
    assert stop["verdict"] == "replay"
    assert stop["existing"] is not None, "preview 没有读到刚受理的那一行"
    assert report["run_id"] == receipt["run_id"]
    # 窗口在场（这一次的请求带着它），而链的读数**没问**：这两格与 ``chain_refusal``
    # 一起为 ``None`` —— 它们不是「问了，答案是零」。
    assert stop["chain"] is not None, "这一份请求带着窗口，链的形状本该报出来"
    assert stop["progress"] is None
    assert stop["spent"] is None
    assert stop["chain_refusal"] is None


def test_the_preview_counts_what_the_increment_face_counts_on_the_same_book(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """同一行 Run、同一本观测账：两个面对四档的读法是同一份。

    ``tests/test_run_preview.py`` 第 9 节钉的是 ``increment_cells`` 与
    ``plan_increment`` 各占一半（后者调前者，共用一个判定、一处 ``works_of`` /
    ``counts_of``）。而**服务面**上还有一件库层看不见的事：那两个函数各由谁喂、
    喂的是哪一本账。两处都共用同一个函数、却各喂一份不同的输入的实现，过得了库层
    每一条判据。

    所以这里比对的是**同一次调用里的两次读数**（一次 ``_drive``，一个新服务端）：
    盘在那两次调用之间没有变，于是 counts 不等只可能是两处读的账或读的时刻不同。
    输入规模那两格（``observations`` / ``signals``）一并比 —— 它们是「格子空间由已
    沉淀的观测决定」那句话的两个数，而**零格的读数与零条输入必须一起读**（
    ``get_run_increment`` 的 ``note`` 逐字写着这件事）。

    观测在**受理之后**才记：这样 preview 与增量面读的是同一本，而且这一条同时证明了
    一件顺带的事 —— 格子读数**照算**，哪怕这一次提交是一次重放（``verdict='replay'``）
    。那正是库里那一句「哪怕这一次提交会被拒，池子里已经有什么仍然读得出来」在服务面
    上的形状。
    """
    run_id = _admitted(settings, tmp_path, wired)
    _observe(wired, settings)

    outcome = _drive(
        settings,
        tmp_path,
        [(PREVIEW, _payload()), (INCREMENT, _increment(run_id=run_id))],
        handlers=wired.handlers,
        scenes=wired.scenes,
    )
    report = _ok(outcome, 0)
    increment = _ok(outcome, 1)

    assert report["run_id"] == run_id, "preview 说的不是刚受理的那一行"
    assert report["budget_and_stop"]["verdict"] == "replay"

    assert report["counts"] is not None, "重放上这一块不该是 ``None`` —— 格子照算"
    assert report["counts"] == increment["counts"], (
        "同一行 Run、同一本观测账，两个面对四档的读法不一致"
    )
    # 零格的相等是最容易混过去的相等（``{} == {}``），所以必须判到格子数上：格子空间
    # 是**已沉淀的主体 × 七个面**（这一条只记了一条观测，所以是一个主体），于是
    # ``len(ALL_ASPECTS)`` 是一个从域层读出来的数，不是一个抄下来的魔数。
    assert len(increment["cells"]) == len(ALL_ASPECTS)
    assert sum(report["counts"].values()) == len(ALL_ASPECTS)

    assert report["inputs"]["observations"] == increment["inputs"]["observations"]
    assert report["inputs"]["signals"] == increment["inputs"]["signals"]

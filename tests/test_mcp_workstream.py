"""工作流那**四条**工具在**工具面**上的判据（B01 / B02，冻结表 §3.2）。

五条承重点里有两条只有在这一层才量得到，而这一份量的就是它们：

* **幂等**（承重点 1）。「同一个键再交一次回同一次启动」是一句关于**盘**的话，
  所以这里既比回执上的三个 id，也数 ``research_run`` / ``launch`` 那几行：
  一条「重发时又建了一行、只是把它盖掉」的实现，回执上完全看不出来。
* **CAS**（承重点 2）。``control_workstream`` 的 ``expected_revision`` 过期时必须
  拒绝，而拒绝之后**盘上那一行不许动** —— 只比回执不看盘，会把「报了一句拒绝、
  其实已经写进去了」读成绿的。

另外三条各自有主：撤回在 ``test_mcp_claim_withdrawal.py``，逐工作区的有效场景在
``test_mcp_effective_scene.py``，而「只有 ``start_workstream`` 建 Run」在这里也有一条
（它是一句关于盘的话，而这一份已经有了数 Run 那行的量具）。

## 为什么这些用例直接调处理器，不经 MCP 线

与 ``test_mcp_effective_scene.py`` 逐字同一条：``tests/mcp_harness.py`` 的
``drive_with_settings`` / ``build_server`` 收的是一组**闭合**的关键字
（``launch=`` / ``feedback=`` / ``views=`` / ``runs=`` …），而 ``workstream=`` /
``effective_scene=`` / ``claim_withdrawal=`` 不在里面 —— ``interfaces/composition.py``
里那三个工厂（``default_workstream_store`` / ``default_effective_scene_store`` /
``default_claim_withdrawal_store``）**已经写好了**，缺的是把它们接进 ``build_server``
与 ``__main__.py``。所以走线的用例今天写不出来，写出来的会是「工具不在目录里」。
这不是本文件的删减理由，是交回单上的一项接线请求：关键字补齐之后，这一份可以直接
改成走线的版本，判据一条不用改。

## 身份是**装上去的**，不是绕过去的

``start_workstream`` 的 ``launched_by`` 取自传输层（``guard.current_principal`` ←
``get_access_token()``），所以直接调处理器时必须把身份放进**同一个 contextvar**
（``auth_context_var``，传输层那个中间件放的就是它）。这是装上，不是打桩：走的是
``current_principal`` 自己的那几行（``subject`` → 名字、``scopes`` → 角色、
``claims["workspaces"]`` → 范围），一行都没有被替身换掉。

``require_workspace`` 那一格在这一层**量不到** —— 它在守卫中间件里，而直接调处理器
绕过了中间件。那一条的判据在 ``tests/test_mcp_workspace.py``。
"""

from __future__ import annotations

import contextlib
import inspect
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError

from marketops.adapters.mcp_server.effective_scene import (
    PREVIEW_COMPILER,
    effective_scene_handlers,
)
from marketops.adapters.mcp_server.workstream import START_COMPILER, workstream_handlers
from marketops.adapters.memory import InMemoryProductCatalog
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.application.workstream import NOTHING_IN_THIS_WORKSPACE
from marketops.config import Settings
from marketops.domain.enums import BudgetLine
from marketops.domain.errors import StoreError
from marketops.domain.scene import (
    OutreachPolicyDecl,
    PauseReason,
    RefreshPlan,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.util import utc_now
from marketops.domain.workstream import WorkstreamAction, WorkstreamQueue
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_effective_scene_store,
    default_maintenance_store,
    default_run_store,
    default_scene_store,
    default_workstream_store,
)
from marketops.security.catalog import SCOPES_BY_ROLE, Role

T0 = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)

#: 截止时刻**从当下算**，不写死 —— 与 ``test_mcp_runs.py`` 逐字同一条理由：
#: 受理时刻由服务端取，写死一个「未来」的时刻是一颗定时炸弹。
CUTOFF = utc_now() + timedelta(days=1)

#: 按下去的这个人。``start_workstream`` 的回执上有一格 ``launched_by``，而它**只能**
#: 来自令牌 —— 所以这个名字在本文件里出现两次（装令牌、比回执），不是巧合。
OPERATOR = "probe-operator"

#: 一份说得清楚的声明。逐字取自 ``test_mcp_runs.py`` 的 ``CONTEXT``（两份都走真实的
#: ``ResearchContext`` 校验器，所以这里不能随手编一个字典）—— 这一族的受理与 ``submit_run``
#: 是**同一条路**，那两处必须能被同一份声明喂饱，否则「同一条路」是句空话。
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

KEY = "workstream-2026-09-29-a"

#: 入参里**不许**出现的名字。每一格都承载一件不由载荷提供的事实：``at`` 取服务端
#: 时钟，``workspace`` 与 ``launched_by`` 取令牌，后三格是**结果**（谁建出来的、
#: 第几版、多长）。
FORBIDDEN_PARAMETERS = frozenset(
    {
        "at",
        "workspace",
        "launched_by",
        "by",
        "launch_id",
        "run_id",
        "ordinal",
        "revision",
        "state",
        "queue",
        "digest",
    }
)

EXPECTED_START_PARAMETERS = frozenset(
    {
        "context",
        "cutoff",
        "idempotency_key",
        "scene_version",
        "scene_id",
        "workstream_name",
        "name",
        "cohort",
        "execution_mode",
        "window",
        "budget",
        "geo",
        "motion",
        "product_id",
        "product_version",
        "offer_id",
        "offer_version",
    }
)


# --- 夹具 ---------------------------------------------------------------------


def _scene() -> SceneVersion:
    """本 profile 的第 1 版声明。逐字取自 ``test_mcp_runs.py::_scene``。

    ``scene_id`` / ``workspace`` / ``profile_id`` 三格都取本部署的 profile：
    ``require_scene`` 只放行它，而这不是省事 —— 一个部署只服务一个 Scene。
    """
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
    """盘上一份 Scene 记录，以及（可选的）它那一版规则。

    ``pause_reason`` 与 ``state=PAUSED`` 是**一对**（域层逐字判这一条），所以夹具
    照那句话写，不绕过它 —— 一份「暂停了但没写为什么」的场景在本仓根本构造不出来，
    而如果它构造得出来，那条判据就测不到真正要测的那一格。
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

    与 ``test_mcp_runs.py::_rows`` 同形（那里逐字写着为什么：一条「先受理再报错」的
    实现在回执上与真拒绝一模一样，而它在盘上留下一行没人打算要的 Run）。这一族
    多一个用途：承重点 5 说**只有** ``start_workstream`` 建 Run，而那也是一句关于
    这一格的话。
    """
    with sqlite3.connect(settings.database_path) as conn:
        found = conn.execute(
            "SELECT COUNT(*) FROM research_run WHERE workspace = ?", (settings.profile,)
        ).fetchone()
    return int(found[0])


_SCOPE_OF_CONTROLLER = next(
    scope for scope, role in SCOPES_BY_ROLE.items() if role is Role.CONTROLLER
)


@contextlib.contextmanager
def _as_operator(principal: str, *, workspaces: tuple[str, ...]) -> Iterator[str]:
    """把身份放进传输层用的那一个 contextvar。

    走的是 ``AuthenticatedUser`` + ``AccessToken`` 这两个**真**类型，而不是给
    ``current_principal`` 打个桩：桩会把「主体从哪来」这一格从被测对象里删掉，而那
    恰好是 ``launched_by`` 那条判据的全部内容（一个能由载荷填写的「谁按下的」不是
    证据）。装上之后跑的是那个函数自己的每一行。
    """
    token = AccessToken(
        token="probe-token",
        client_id=principal,
        scopes=[_SCOPE_OF_CONTROLLER],
        subject=principal,
        claims={"workspaces": list(workspaces)},
    )
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield principal
    finally:
        auth_context_var.reset(reset)


class Wiring:
    """一次接线：十三个工厂 + 两个实例，与组合根交给这一族的东西同形。

    七格是**组合根自己那三个具名工厂**（``default_workstream_store`` /
    ``default_effective_scene_store`` / ``default_scene_store`` 那一族），不是就地写的
    ``lambda``：那一族 docstring 逐字要求 ``effective`` 由**具名局部**交出去，而
    「两处各写一行 lambda」今天恰好同形、迟早不一样。

    ``effective`` 交给**两个**族（冻结的那一侧与读的那一侧），而这一份因此能看见
    「刚冻结的那一版读不出来」与「它从来没被冻结过」分家。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.scenes = default_scene_store(settings)
        self.maintenance = default_maintenance_store(settings)
        self.runs = default_run_store(settings)
        self.ledger = partial(SqliteBudgetLedger, settings.database_path)
        self.effective = default_effective_scene_store(settings)
        self.workstreams = default_workstream_store(settings)
        self.catalog = InMemoryProductCatalog()
        self.handlers = {
            **workstream_handlers(
                settings=settings,
                scenes=self.scenes,
                catalogs=lambda: self.catalog,
                workstreams=self.workstreams,
                effective=self.effective,
                runs=self.runs,
                maintenance=self.maintenance,
                ledger=self.ledger,
                # e4 那一族：只有 ``watch`` 读得着它们，而组合根两样都接得上。
                accounts=default_account_pool_store(settings),
                cohorts=default_account_cohort_store(settings),
            ),
            **effective_scene_handlers(
                settings=settings,
                scenes=self.scenes,
                catalogs=lambda: self.catalog,
                effective=self.effective,
            ),
        }

    # -- 调用面 ------------------------------------------------------------------

    def start(self, **kwargs: Any) -> dict[str, Any]:
        return self.handlers["start_workstream"](**kwargs)

    def get(self, **kwargs: Any) -> dict[str, Any]:
        return self.handlers["get_workstream"](**kwargs)

    def listing(self, **kwargs: Any) -> dict[str, Any]:
        return self.handlers["list_workstreams"](**kwargs)

    def control(self, **kwargs: Any) -> dict[str, Any]:
        return self.handlers["control_workstream"](**kwargs)

    def preview(self, **kwargs: Any) -> dict[str, Any]:
        return self.handlers["preview_effective_scene"](**kwargs)

    def read(self, **kwargs: Any) -> dict[str, Any]:
        return self.handlers["get_effective_scene"](**kwargs)

    # -- 盘上那三样（读的是存储实例本身，不是处理器的返回值）------------------------

    def run_rows(self) -> int:
        return _rows(self.settings)

    def stream_rows(self) -> tuple[Any, ...]:
        return self.workstreams().workstreams(workspace=self.settings.profile)

    def scene_rows(self) -> tuple[int, ...]:
        return self.effective().versions(self.settings.profile)


def _payload(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "context": dict(CONTEXT),
        "cutoff": CUTOFF.isoformat(),
        "idempotency_key": KEY,
        "scene_version": 1,
        "budget": {"search": 20},
    }
    body.update(overrides)
    return body


@pytest.fixture
def operator(settings: Settings) -> Iterator[str]:
    with _as_operator(OPERATOR, workspaces=(settings.profile,)) as name:
        yield name


@pytest.fixture
def wiring(settings: Settings, operator: str) -> Wiring:
    """盘上一份已生效的 Scene（**不**建任何工作流、**不**冻结任何有效场景）。"""
    _seed_scene(settings)
    return Wiring(settings)


# --- 1 · 参数表 -----------------------------------------------------------------


def test_the_four_tools_take_no_instant_no_workspace_and_no_principal(
    wiring: Wiring,
) -> None:
    """四条工具的入参集合**恰好**是它们自己那一组，受禁的那几格一个都不在。

    ``workstream_name`` 与 ``name`` 两格并存是本条最要紧的一处：``name`` 与
    ``submit_run`` **逐字同名**（它是**主体来源**、一份手写清单的名字），而工作流
    自己的名字叫 ``workstream_name``。两格都叫 ``name`` 会让「给工作流起名」与
    「指定主体清单」在同一个签名里同形，而写错的那一个不会报错 —— 它只是照着
    另一个意思做了一件事。
    """
    signatures = {
        name: set(inspect.signature(handler).parameters)
        for name, handler in wiring.handlers.items()
    }

    assert signatures["start_workstream"] == EXPECTED_START_PARAMETERS, (
        f"多出来的：{sorted(signatures['start_workstream'] - EXPECTED_START_PARAMETERS)}；"
        f"少掉的：{sorted(EXPECTED_START_PARAMETERS - signatures['start_workstream'])}"
    )
    assert signatures["get_workstream"] == {"workstream_id"}
    assert signatures["list_workstreams"] == set()
    assert signatures["control_workstream"] == {
        "workstream_id",
        "action",
        "expected_revision",
        "reason",
        "name",
    }

    for name in ("start_workstream", "control_workstream"):
        assert signatures[name] & FORBIDDEN_PARAMETERS == set(), name
    #: 读的那三条连一个可选参数都没有：``list_workstreams`` 的工作区取自本部署，
    #: 而一个能由载荷挑的工作区会让一次读去翻别人的账。
    assert signatures["list_workstreams"] == set()


def test_the_surface_is_exactly_the_four_workstream_tools(wiring: Wiring) -> None:
    """这一族**只有四条**，且四条都在目录里那七条之内。

    ``withdraw_claim`` 与两条有效场景工具由别的族交出去（这一份的 ``handlers``
    里它们也在，但那是同一个接线对象上的两族，不是这一族多做了事）。
    """
    assert {
        "start_workstream",
        "get_workstream",
        "list_workstreams",
        "control_workstream",
    } <= set(wiring.handlers)


# --- 2 · 启动：三个 id、一次幂等、一行 Run --------------------------------------


def test_a_start_writes_the_three_ids_and_exactly_one_run(wiring: Wiring) -> None:
    """一次启动回执上是 PSM §4.2 第 5 条那几格，而盘上多的是**一行 Run**。

    ``launched_by`` 比的是令牌里的那个名字 —— 这一格是「谁按下的」的证据，而它的
    唯一合法来源是传输层（一个由载荷填的「谁批准的」不是证据）。
    手动那一轮的 ``next_wake_at`` 是 ``None``，而这是一句**说出来的**话（「没有人
    会来叫它」），不是省略：一个周期任务会在这里有值。
    """
    assert wiring.run_rows() == 0

    receipt = wiring.start(**_payload())

    assert receipt["launch_id"]
    assert receipt["workstream_id"] == f"{wiring.settings.profile}#1"
    assert receipt["run_id"]
    assert receipt["scene_id"] == wiring.settings.profile
    assert receipt["scene_version"] == 1
    assert receipt["idempotency_key"] == KEY
    assert receipt["replayed"] is False
    assert receipt["launched_by"] == OPERATOR
    assert receipt["queue"] == WorkstreamQueue.RUN_ADMITTED.value
    assert receipt["why"] == ""
    assert receipt["state"] == "running"
    assert receipt["revision"] == 1
    assert receipt["ordinal"] == 1
    assert receipt["next_wake_at"] is None

    assert wiring.run_rows() == 1, "一次启动的第一轮 Run 没有落盘"
    assert len(wiring.stream_rows()) == 1


def test_the_same_key_twice_returns_the_first_start(wiring: Wiring) -> None:
    """重发：三个 id 逐一相同、``replayed`` 为真，而盘上**没有多出任何一行**。

    「同一个键再交一次回同一次启动」这句话在两个量具下才成立：回执（调用方读的）
    与行数（盘上真有的）。只比回执会放过一条「又建了一行、然后把新行读出来」的
    实现 —— 它的回执与这一条断言逐字相同，而盘上那两行里有一行没人要。
    """
    first = wiring.start(**_payload())
    second = wiring.start(**_payload())

    assert second["launch_id"] == first["launch_id"]
    assert second["workstream_id"] == first["workstream_id"]
    assert second["run_id"] == first["run_id"]
    assert second["replayed"] is True
    assert first["replayed"] is False
    #: 重发时 ``state`` / ``revision`` / ``created_at`` 报的是**第一次**那一组 ——
    #: 它们与 ``replayed`` 一起把「这一次什么都没发生」说清楚。
    assert second["revision"] == first["revision"] == 1
    assert second["created_at"] == first["created_at"]

    assert wiring.run_rows() == 1, "重发又建了一行 Run"
    assert len(wiring.stream_rows()) == 1, "重发又建了一个工作流"


def test_the_same_key_with_another_content_is_a_conflict(wiring: Wiring) -> None:
    """同一个键配另一份内容 ⇒ 拒绝，并点名**盘上那一份摘要与本次这一份**。

    静默拿回第一次那一行会让调用方以为他开的那一轮跑的是他要的东西 —— 而
    ``name``（工作流名）与按下启动的那个人都在请求摘要里。
    """
    wiring.start(**_payload())

    with pytest.raises(ToolError) as caught:
        wiring.start(**_payload(workstream_name="另一个名字"))

    text = str(caught.value)
    assert "已经用这个键记过了" in text
    assert "不是同一份" in text
    assert wiring.run_rows() == 1
    assert len(wiring.stream_rows()) == 1


def test_the_weights_of_a_named_product_that_cannot_be_read_are_readable(
    wiring: Wiring,
) -> None:
    """指名了一个读不回来的产品：拒绝那句话里**必须有那个名字**。

    ## 这一条是一次实测出来的逃逸的回归钉

    冻结那一版有效场景要读产品（``_read_product``），而「指名了却读不回来」在那
    一层是一条 ``ValueError``。这一族原先只接 ``ConfigError`` / ``EvidenceError`` /
    ``WorkstreamStateError`` / ``StoreError``，于是它落进 ``tools/base.py:135-158``
    的**第二支**：调用方读到的是一句光秃秃的 ``Error executing tool
    start_workstream``，而那句话里点名的产品与版本号**一个字都到不了** —— 那是他
    唯一能据以改东西的两格。

    所以这一条判两件事：那句话里点得到名字，**以及**盘上一行都没写（冻结在任何写
    之前，所以一次被拒的启动不留下一份没人启动过的有效场景）。
    """
    with pytest.raises(ToolError) as caught:
        wiring.start(
            **_payload(product_id="no-such-product", product_version=1)
        )

    text = str(caught.value)
    assert "no-such-product" in text, f"拒绝那句话里没有点名产品：{text!r}"
    assert "Error executing tool" not in text, "这一段是本仓自己的话，不是 SDK 那一句"
    assert wiring.run_rows() == 0
    assert wiring.stream_rows() == ()
    assert wiring.scene_rows() == ()


# --- 3 · 那三条形状拒绝（在这一层判，一次都没写）--------------------------------


def test_an_empty_key_is_refused_before_writing_anything(wiring: Wiring) -> None:
    """空幂等键在这一层拒：它会让「重发」那条判据塌掉，而塌掉之后两处都自洽。

    拒的时机也是判据的一部分 —— 这一条**一次都没写**（它是**形状**，不是与盘比对）。
    """
    for key in ("", "   "):
        with pytest.raises(ToolError) as caught:
            wiring.start(**_payload(idempotency_key=key))
        assert "idempotency_key" in str(caught.value)

    assert wiring.run_rows() == 0
    assert wiring.stream_rows() == ()


def test_scene_version_zero_is_not_a_version(wiring: Wiring) -> None:
    """第 0 版不是一版：一个「没说」的版本号会被读成「你给错了版本」，而那是另一件事。"""
    with pytest.raises(ToolError) as caught:
        wiring.start(**_payload(scene_version=0))

    assert "scene_version" in str(caught.value)
    assert wiring.run_rows() == 0


def test_a_scene_this_deployment_does_not_serve_is_refused(wiring: Wiring) -> None:
    """另一个 Scene 名 ⇒ 拒绝，并把本部署配置的那一个**写出来**。

    静默退回默认值会让调用方以为读的是另一个场景的规则 —— 而证据、预算与凭据
    都属于本部署这一个。
    """
    with pytest.raises(ToolError) as caught:
        wiring.start(**_payload(scene_id="some-other-market"))

    text = str(caught.value)
    assert "some-other-market" in text
    assert wiring.settings.profile in text


def test_a_version_that_is_not_effective_names_both(wiring: Wiring) -> None:
    """按第 2 版启动而生效的是第 1 版 ⇒ 拒绝并**点名两版**，且一字未写。

    两类情形有两种处置（``latest_version`` 更大 ⇒ 去批准或改用旧的那一版；
    ``effective_version`` 是别的数 ⇒ 传错了），合成一句「版本不对」会让这两种
    处置分不开。
    """
    with pytest.raises(ToolError) as caught:
        wiring.start(**_payload(scene_version=2))

    text = str(caught.value)
    assert "第 2 版" in text
    assert "第 1 版" in text
    assert wiring.run_rows() == 0
    assert wiring.scene_rows() == (), "一次被拒的启动留下一份没人启动过的有效场景"


def test_a_paused_scene_is_refused_and_nothing_is_written(wiring: Wiring) -> None:
    """暂停中的场景不能派发 —— 放行会让一次启动在一个被暂停的场景上开出一轮 Run。

    这一条与上一条**同时**要判盘：拒绝的语气再准，一行真的写下去了就是另一回事。

    暂停这一步走的是存储自己的 CAS（读回当前记录 → ``SceneRecord.transition`` →
    ``put_record(expected_revision=…)``），**不是**再种一份：``expected_revision=None``
    逐字要求「记录必须不存在」，第二次种会退 ``SceneConflictError``，那条错误会把
    这一条测成「夹具先坏了」。
    """
    scenes = wiring.scenes()
    current = scenes.get_record(wiring.settings.profile)
    assert current is not None, "夹具里那一份 Scene 应该在盘上"
    scenes.put_record(
        current.transition(
            SceneState.PAUSED, at=utc_now(), pause_reason=PauseReason.OPERATOR
        ),
        expected_revision=current.revision,
    )

    with pytest.raises(ToolError) as caught:
        wiring.start(**_payload())

    assert "不能派发" in str(caught.value)
    assert wiring.run_rows() == 0
    assert wiring.stream_rows() == ()


# --- 4 · HOLD：第一轮没被受理，工作流照样建出来 ---------------------------------


def test_a_held_first_round_still_builds_the_workstream(wiring: Wiring) -> None:
    """一份编不出计划的声明不拦下这一次**启动**，它拦下的是**第一轮 Run**。

    ``queue`` / ``why`` / ``next_wake_at`` 三格是 PSM §4.2 第 5 条逐字要的那三格：
    把 ``HOLD`` 读成一次拒绝，会让「这个市场开了、但今天这一轮没什么可跑」与
    「这个市场没开」在报告上同形，而两者的下一步相反。
    """
    receipt = wiring.start(**_payload(context=dict(CONTEXT, scenario_candidates=[])))

    assert receipt["queue"] == WorkstreamQueue.IDLE.value
    assert receipt["why"].strip(), "IDLE 上没有 why —— 读的人分不清是等还是被拒了"
    assert receipt["run_id"] == ""
    assert receipt["next_wake_at"] is None

    #: 工作流建出来了，而 Run **一行都没有**。
    assert len(wiring.stream_rows()) == 1
    assert wiring.run_rows() == 0


# --- 5 · 列出与读回 -------------------------------------------------------------


def test_listing_an_empty_workspace_says_so(wiring: Wiring) -> None:
    """本工作区一个工作流都没有时那句话**在 ``note`` 里**，逐字。

    这一层的读回面上「读错了工作区」「Workstream 这一层没接上」与「这里本来就没有」
    三件事同形，而 ``note`` 是唯一分得开它们的那一格 —— 一个只读 ``workstreams``
    的调用方拿到的是一张空表，那正是这条工具要避免的形状。
    """
    listing = wiring.listing()

    assert listing["workstreams"] == []
    assert listing["note"] == NOTHING_IN_THIS_WORKSPACE


def test_the_listing_and_the_read_agree_with_the_start(wiring: Wiring) -> None:
    """启动回执、清单、读回三处说的是**同一行** —— 名字与 ordinal 都不重算。

    三处各拼一份投影的代价不是那十几行：先漂的是「哪些格子进这份回执」，于是一份
    说 ``queue``、另一份不说，而三份都声称自己报的是同一行。
    """
    started = wiring.start(**_payload())
    listing = wiring.listing()
    read = wiring.get(workstream_id=started["workstream_id"])

    assert len(listing["workstreams"]) == 1
    assert listing["workstreams"][0]["workstream_id"] == started["workstream_id"]
    assert listing["note"] == ""
    for key in ("workstream_id", "scene_id", "scene_version", "ordinal", "launch_id"):
        assert read[key] == started[key], key
    assert read["queue"] == started["queue"]
    assert read["revision"] == started["revision"]
    #: 读回那一条多出来的两格：能不能派发，以及不能时的原因。
    assert read["scene_state"] == SceneState.ACTIVE.value
    assert read["dispatch_allowed"] is True
    assert read["dispatch_refusal"] == ""


def test_an_unknown_workstream_id_names_the_shape_it_wants(wiring: Wiring) -> None:
    """读不到时点名的**是它要的形状**（``<scene_id>#<序号>``），不是一个 id 清单。"""
    with pytest.raises(ToolError) as caught:
        wiring.get(workstream_id="nope")

    assert "<scene_id>#<ordinal>" in str(caught.value)
    #: 空 id 是另一句话（它是形状，不必去查库）。
    with pytest.raises(ToolError) as empty:
        wiring.get(workstream_id="")
    assert "workstream_id" in str(empty.value)


# --- 6 · 控制：CAS 与三档各自的必填参数 -----------------------------------------


def test_pause_needs_a_reason_and_the_receipt_says_which_revision(wiring: Wiring) -> None:
    """``pause`` 要原因；回执上 ``action`` 与 ``expected_revision`` 与**结果**并列。

    并列是为了让读的人看得出「要它从第 1 版走到第 2 版」与「它现在在第几版」是不是
    同一件事。没有原因的暂停三天后没人说得清该不该恢复它，而那是这条工具存在的理由。
    """
    started = wiring.start(**_payload())
    wid = started["workstream_id"]

    with pytest.raises(ToolError) as caught:
        wiring.control(
            workstream_id=wid, action="pause", expected_revision=1, reason="   "
        )
    assert "要一个原因" in str(caught.value)
    assert wiring.stream_rows()[0].revision == 1, "一次被拒的控制动了盘上那一行"

    after = wiring.control(
        workstream_id=wid, action="pause", expected_revision=1, reason="operator_pause"
    )

    assert after["action"] == "pause"
    assert after["expected_revision"] == 1
    assert after["revision"] == 2
    assert after["state"] == "paused"
    assert after["pause_reason"] == "operator_pause"
    assert wiring.stream_rows()[0].revision == 2


def test_a_stale_revision_does_not_overwrite_the_newer_one(wiring: Wiring) -> None:
    """CAS：拿着第 1 版去改一个已经到第 2 版的工作流 ⇒ 拒绝，**一个字都没写**。

    这正是 CAS 那句话的内容：「过期的那一次不覆盖新的一次，因为覆盖之后『有人先改
    了』这件事在盘上不留痕」。所以这一条既比回执（两版都点名），也比盘（还是第 2 版、
    还是那个名字）—— 只比回执的实现会在「报了一句拒绝、其实已经覆盖了」上绿。
    """
    started = wiring.start(**_payload())
    wid = started["workstream_id"]
    wiring.control(
        workstream_id=wid, action="pause", expected_revision=1, reason="first-pause"
    )

    with pytest.raises(ToolError) as caught:
        wiring.control(
            workstream_id=wid, action="rename", expected_revision=1, name="偷跑的改名"
        )

    text = str(caught.value)
    assert "第 2 版" in text, "拒绝里没有点名盘上那一版"
    assert "第 1 版" in text, "拒绝里没有点名本次读到的那一版"
    assert "一个字都没写" in text

    row = wiring.stream_rows()[0]
    assert row.revision == 2
    assert row.name != "偷跑的改名"


def test_a_rename_keeps_the_reason_the_pause_gave(wiring: Wiring) -> None:
    """改名**不动** ``pause_reason`` —— 一个暂停中的工作流改个名字，理由还在。

    ## 这一条是一次实测出来的缺陷的回归钉（2026-09-29）

    这一格原来是一句 ``reason if action.requires_reason else ""``，它把「谁该写
    原因」折成了一个布尔，而三档里有两档的 ``requires_reason`` 都是 ``False`` ——
    于是**改名**会顺手把暂停原因清成空串。两个实现的失败形态还不一样：SQLite 那一
    侧 ``put_workstream`` 会把行读回来重建对象，而域层要求「暂停必须写明原因」，
    于是**写这一步就抛**（抛的还是一个 ``pydantic.ValidationError``）；内存替身
    那边对象原样存着，于是写成功了 —— 同一个动作，一个做不成、一个做错了。

    所以这一条要在**真存储**上判，并在判完之后把那一行读回来看。
    """
    started = wiring.start(**_payload())
    wid = started["workstream_id"]
    wiring.control(
        workstream_id=wid, action="pause", expected_revision=1, reason="operator_pause"
    )

    after = wiring.control(
        workstream_id=wid, action="rename", expected_revision=2, name="改了个名字"
    )

    assert after["name"] == "改了个名字"
    assert after["state"] == "paused"
    assert after["pause_reason"] == "operator_pause"
    assert after["revision"] == 3
    #: 盘上那一行也是同一件事（回执与盘两处都读）。
    row = wiring.stream_rows()[0]
    assert row.name == "改了个名字"
    assert row.pause_reason == "operator_pause"


def test_rename_to_the_empty_string_is_refused(wiring: Wiring) -> None:
    """改成空串与「没打算改名」是两件事：前者是 ``name=""`` 的一次改名，后者是不调它。

    把前者当成后者，会让一次改名在盘上不留痕（而名字可以为空串 ——「没起名字」是
    一个说得出来的状态，所以这一格不能靠名字非空来挡）。
    """
    started = wiring.start(**_payload())
    wid = started["workstream_id"]

    with pytest.raises(ToolError) as caught:
        wiring.control(
            workstream_id=wid, action="rename", expected_revision=1, name="  "
        )

    assert "要一个新名字" in str(caught.value)
    assert wiring.stream_rows()[0].revision == 1


def test_a_typo_in_the_action_lists_the_closed_set(wiring: Wiring) -> None:
    """``action`` 收一个**闭集**，拼错时把闭集写出来。

    一个「落到 else 分支」的实现在拼错时总是能做点什么，而那件事不是任何人要求的。
    """
    started = wiring.start(**_payload())

    with pytest.raises(ToolError) as caught:
        wiring.control(
            workstream_id=started["workstream_id"],
            action="resume!",
            expected_revision=1,
        )

    text = str(caught.value)
    for allowed in (item.value for item in WorkstreamAction):
        assert allowed in text, f"闭集里少写了 {allowed!r}"
    assert "resume!" in text


def test_expected_revision_zero_is_not_a_revision(wiring: Wiring) -> None:
    """``expected_revision`` 从 1 起，且**必填**：给 0 与不给同形。

    它是「我读到的是第几版」那一格，而 CAS 的全部内容就是它 —— 一个缺省会让这一次
    控制跳过一次比对。
    """
    started = wiring.start(**_payload())

    with pytest.raises(ToolError) as caught:
        wiring.control(
            workstream_id=started["workstream_id"], action="resume", expected_revision=0
        )

    assert "expected_revision" in str(caught.value)
    assert wiring.stream_rows()[0].revision == 1


def test_pushing_a_workstream_to_the_state_it_is_already_in_is_refused(
    wiring: Wiring,
) -> None:
    """已经是 ``running`` 再 ``resume`` ⇒ 拒绝。

    放它过去会让 revision 加一、回执说成功了，而盘上什么都没变 —— 一次没发生的事
    写进一次成功的回执，此后没人分得清它到底发生过没有。
    """
    started = wiring.start(**_payload())

    with pytest.raises(ToolError) as caught:
        wiring.control(
            workstream_id=started["workstream_id"],
            action="resume",
            expected_revision=1,
        )

    assert "已经是 running" in str(caught.value)
    assert wiring.stream_rows()[0].revision == 1


def test_the_control_receipt_is_the_row_on_disk(wiring: Wiring) -> None:
    """回执照的是**盘上那一行**，不是刚拼出来的那个对象 —— 两处各读一次，逐格相同。

    少了这一步，回执照的是一个可能没写进去的对象来说话：写失败而回执说成功，读的
    人此后没有任何东西可以据以分辨。
    """
    started = wiring.start(**_payload())
    wid = started["workstream_id"]

    after = wiring.control(
        workstream_id=wid, action="pause", expected_revision=1, reason="operator_pause"
    )

    row = wiring.stream_rows()[0]
    for key in ("state", "revision", "name", "pause_reason", "workstream_id", "ordinal"):
        assert after[key] == getattr(row, key), key


def test_a_workstream_of_another_workspace_cannot_be_put_in_this_library(
    wiring: Wiring,
) -> None:
    """跨工作区的写：库已经绑在本工作区上 ⇒ 一行别人的工作流**写不进去**。

    这一条**比它先前那版更强**。先前那版把一行 ``workspace='another-market'`` 的
    工作流真的插进库里，再判「读它的时候说了什么」；2026-09-29 复测发现写路径上
    ``bind_workspace`` 已落地（``adapters/sqlite/workstreams.py::_bind``），于是
    「把那一行放进去」这一步本身就被拒了。

    后果要写清楚：``workstream.py::_stream_or_refuse`` 的**跨工作区分支**因此从
    **写**路径上不可达 —— 它仍是一层防御（一行别人的工作流若因别的原因落到库里，
    读的时候照样点名两个工作区），但本测试不再声称自己走到了那一支。

    仍要判的是**这一格真的被拒**，不是「没人试过」：所以先断言拒绝文本点名两个
    工作区，再断言被拒之后库里一行都没多。
    """
    started = wiring.start(**_payload())
    elsewhere = wiring.stream_rows()[0].model_copy(
        update={
            "workspace": "another-market",
            "workstream_id": f"{wiring.settings.profile}#9",
            "revision": 1,
        }
    )

    with pytest.raises(StoreError) as caught:
        wiring.workstreams().put_workstream(elsewhere, expected_revision=None)

    text = str(caught.value)
    assert "another-market" in text
    assert wiring.settings.profile in text
    #: 被拒之后库里仍然只有本工作区那一行 —— 「拒了」与「拒了但写了半行」不同。
    assert [row.workstream_id for row in wiring.stream_rows()] == [
        started["workstream_id"]
    ]
    #: 本工作区那一行照旧读得回来。
    assert wiring.get(workstream_id=started["workstream_id"])["queue"] == started["queue"]


# --- 7 · 冻结：写路径是唯一让 Valid Scene 落库的那一条 ---------------------------


def test_the_start_freezes_the_effective_scene_and_the_preview_did_not(
    wiring: Wiring,
) -> None:
    """预览**一格都没写**，而启动冻结了**那一版**，两份摘要因此逐字相同。

    「编译输入与 ``preview_effective_scene`` 同一份拼法」这句话只有在两边都跑一遍
    之后才判得了：两处各拼一份的那天，预览给你看的那份与落库的那份会是两份不同的
    东西，而两份都自洽。这里比的正是那两格（``digest`` 与 ``compiled_from``）。

    ``compiler`` 那一格则**必须不同**：它答的是「哪一条路径编译了它」，而预览与
    冻结是两次动作（``PREVIEW_COMPILER`` / ``START_COMPILER``）。
    """
    previewed = wiring.preview()
    assert wiring.scene_rows() == (), "预览落了一版有效场景"

    wiring.start(**_payload())

    assert wiring.scene_rows() == (1,)
    frozen = wiring.read(version=1)
    assert frozen["compiler"] == START_COMPILER
    assert previewed["compiler"] == PREVIEW_COMPILER
    assert frozen["digest"] == previewed["digest"], "预览与冻结拼出来的不是同一份"
    assert frozen["compiled_from"] == previewed["compiled_from"]


def test_no_path_but_the_start_builds_a_run(wiring: Wiring) -> None:
    """承重点 5 在工具面上：**只有** ``start_workstream`` 会建 Run。

    五条读与控制路径（预览、读冻结的那一份、清单、读一个工作流、控制）各跑一遍，
    数的是 ``research_run`` 那几行 —— 「预览顺手受理了一轮」这件事在回执上与
    「预览了一次」同形，只有盘数得出来。最后由一次真的启动把这一格推上去，
    证明这个量具本身活着（一个恒读 0 的实现会在上面五条上全绿）。
    """
    assert wiring.run_rows() == 0
    wiring.preview()
    assert wiring.run_rows() == 0
    assert wiring.scene_rows() == ()

    started = wiring.start(**_payload())
    assert wiring.run_rows() == 1
    wid = started["workstream_id"]

    wiring.read(version=1)
    wiring.listing()
    wiring.get(workstream_id=wid)
    wiring.control(
        workstream_id=wid, action="pause", expected_revision=1, reason="operator_pause"
    )
    wiring.control(
        workstream_id=wid, action="resume", expected_revision=2
    )

    assert wiring.run_rows() == 1, "有一条读或控制路径建了一行 Run"

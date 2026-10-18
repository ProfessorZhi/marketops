"""§B24 那九条工具走**真实 MCP 会话**时的行为。

## 这一层测的是接线之后才存在的那几件事

判据本身各有住处：``tests/test_views_domain.py`` 与 ``tests/test_watch_domain.py``
（域）、``tests/test_views_store_contract.py`` 与 ``tests/test_sqlite_views.py``
（端口与 sqlite）、``tests/test_views_application.py``（编排 —— 那里也有「一个账号读
了三次」「点名是哪一部分」「目标没了如实报」三条，但判的是**应用层的值对象**）。
本文件不重测那些，它测的是把四段（目录、处理器表、存储、令牌）接起来之后**才会出现**
的东西：

* **人从哪里来**：``actor`` / ``owner`` / ``workspace`` / ``at`` / ``by`` 一律不是入参
  —— 判据是 ``tools/list`` 上那份 ``inputSchema`` 与处理器签名**逐格相等**，所以将来
  没人能悄悄加回一个「让调用方替自己签名」的入口。而 ``owner`` 只允许出现在**读**与
  **处置**上（``list_saved_views`` / ``list_action_items`` / ``resolve_action_item``
  上它是筛选与改派），不允许出现在两条**写**上 —— 视图与 Watch 的所有者是键的一半，
  一个能填的 ``owner`` 就是一条把记录交给别人的入口。
* **承重点 1 的签名半边住在哪里**：这一族分成两张处理器表，而
  :func:`~marketops.adapters.mcp_server.views.views_handlers` 的形参里**没有账本**，
  :func:`~marketops.adapters.mcp_server.followups.followups_handlers` 里有。两份签名
  并排读，才是 §5.3 那半句在代码里的样子（行为半边在
  ``tests/test_views_application.py::test_a_paused_scene_stops_a_watch_but_not_a_saved_view``）。
* **两个「如实报」在一次回执里的读数**：``run_saved_view`` 的合计数
  （``accounts_read`` 与 ``accounts_distinct`` 同时给），以及 ``list_watches`` 的
  ``target_state`` / ``target_missing``。
* **承重点 5 在工具面上的落点**：``resolve_action_item`` 的 ``expected_version``
  没有缺省值，而过期是**一次拒绝**。

## 这一份走**真的** ``build_server``

交回这一份的时候 ``build_server`` **还没有**视图 / 跟进两个槽（它报告里 WIRING-
REQUEST 第 1 项），而 ``RoleGuard.backed`` 在构造那一刻就定死了 —— 一个工具不在
``handlers`` 里，中间件会让它落回 ``unbacked_message``，于是「这一族的行为」在那个
函数的输出上**测不出来**。当时的处置是照 ``build_server`` 的尾巴手搓一个实例，并把
「认证那一段是**抄**的」写在这里 —— 它没有替 MAIN 做接线，那是对的。

接线之后那句抄写就没有理由留着了：抄的正是「受众怎么取、``validate_token_resource``
怎么取舍」那几行，而它们一变，这里的用例**不会**先红 —— 它会照旧用旧规矩跑完，报出
一个与真实部署无关的结论。所以夹具现在调用
``build_server(settings=…, tokens=…, views=…, followups=…)``，会话前面那几段只有
一处写法。两件事因此分得很清楚：

* **这一份自己的**：五份存储与两张处理器表（它要判的是「同一次会话里换一个主体」，
  而组合根真的接上了哪几条有它自己的判据：``tests/test_r1_vertical_acceptance.py``）。
* **生产那一份的**：``TokenRingVerifier`` / ``AuthSettings`` / ``RoleGuard`` /
  ``_register`` —— 一个字节都不在这里。

## 建待办那一条（``open_action_item``）**现在没有替身了**

它曾经是本文件里唯一一条**要替身才测得了**的工具：目录行由 MAIN 独占
（``security/catalog.py``）、表上那一格在 ``followups.py``，而 ``_register`` 只走
``TOOL_CATALOG`` —— 两格缺任何一格，「它真的调得到」这句话都测不出来，而两格分两次
落地时坏得还不一样（只有表没目录：``ValueError: 这些工具名不在目录里``，组合根起不来；
只有目录没表：它在 ``tools/list`` 上出现而调一句「本构建尚未接线」，那个形态与接好的
形态逐字同形）。

两格现在都在真件上了（目录行由 MAIN 落，表上那一格在 ``followups.py``），所以本文件
里的 ``_STAND_IN_SPEC`` / ``_REAL_TOOL_CATALOG`` / 那条替身夹具与那条「还是不是替身」
的判据**已经删掉**。第六节那五条判的因此是真目录与真表 —— 而它们在两格落地前后
**一行都没改**：替身当初就是照真件的样子补的，这也正是「替身」与「判据」在那一节里
分得开的理由。

## 其余八条**都不建** ActionItem，所以下面几处要自己播种

``put_saved_view`` / ``put_watch`` 等八条里没有一条会创建一件待办 —— 要一个**钉住的
时刻**（``at=T0``）时走 ``application/views.py::open_action_item`` 直接写盘；要判「这一条
工具真的调得到」的用例走工具面。两条路各判各的，见 :func:`_seed_item` 与第六节。

会话本身走 ``tests/mcp_harness.py`` 的 ``drive``：真的 ``MCPServer``、真的 ASGI
应用、真的 ``BearerAuthBackend`` 与 ``AuthContextMiddleware``，客户端用 SDK 自己的
``ClientSession``。**没有 socket**，所以它不会去抢 8787。
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.mcpserver import MCPServer

from marketops.adapters.mcp_server.followups import followups_handlers
from marketops.adapters.mcp_server.server import build_server
from marketops.adapters.mcp_server.views import views_handlers
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.adapters.sqlite.views import (
    SqliteActionItemStore,
    SqliteSavedViewStore,
    SqliteWatchStore,
)
from marketops.application.views import open_action_item
from marketops.config import Settings
from marketops.domain.account import MarketAccount
from marketops.domain.action_item import ActionType, ScopeKind
from marketops.domain.enums import AccountStatus, BudgetLine
from marketops.domain.scene import PauseReason, SceneRecord, SceneState
from marketops.domain.watch import NotifyOn
from marketops.interfaces.composition import default_account_pool_store
from marketops.security import catalog as catalog_module
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.conftest import DEFAULT_PROFILE
from tests.mcp_harness import drive, ring_entry, ring_payload

UTC = timezone.utc
T0 = datetime(2026, 9, 27, 1, 0, tzinfo=UTC)

#: 本部署服务的那个工作区 —— 同时是场景 id（``require_scene`` 判的就是这一条）。
WORKSPACE = DEFAULT_PROFILE
SCENE = DEFAULT_PROFILE

#: §B24 的九条。写死在这里而不是从目录里按 phase 筛 —— 与
#: ``tests/test_mcp_feedback.py`` 的 ``FEEDBACK_TOOLS`` 同一条理由：这一份要钉的正是
#: **这九条**（多一条或少一条都要响）。
VIEW_TOOLS: tuple[str, ...] = (
    "put_saved_view",
    "get_saved_view",
    "list_saved_views",
    "run_saved_view",
)
FOLLOWUP_TOOLS: tuple[str, ...] = (
    "put_watch",
    "list_watches",
    "list_action_items",
    "open_action_item",
    "resolve_action_item",
)
NINE: tuple[str, ...] = VIEW_TOOLS + FOLLOWUP_TOOLS

#: 任何一条**都不许**声明的入参。它们是「谁说的 / 在哪儿说的 / 什么时候说的」，
#: 而这三样一律从令牌与服务端时钟来 —— 一个能由载荷填写的「谁说的」，此后会被当成
#: 一个人真的说过的话读回去，并且两处各自都正常。
FORBIDDEN_PARAMETERS: frozenset[str] = frozenset(
    {"actor", "principal", "workspace", "at", "roles", "by"}
)

#: ``owner`` 只许出现在这四条上，而且各有各的理由（见模块 docstring）。
#:
#: ``open_action_item`` 是第四条，而它的理由与前三条**不同一类**：视图与 Watch 的
#: ``owner`` 是 ``(workspace, owner, …)`` 那条键的一半（所以那里它只能来自令牌）；
#: 待办的键是 ``(workspace, action_type, scope_ref)``，**没有 owner**，而「派给谁」
#: 这一格 ``resolve_action_item`` 本来就允许改派。缺省 ``None`` = 没人认领，
#: 而 §B24 那个「有通用邮箱但缺负责人」的例子要建的正是一件**没有负责人**的待办 ——
#: 把这一格从签名里拿掉，「缺负责人」这一类积压就没法被建出来。
OWNER_ALLOWED: frozenset[str] = frozenset(
    {
        "list_saved_views",
        "list_action_items",
        "open_action_item",
        "resolve_action_item",
    }
)

#: 两个人。第一个是大多数用例的调用者，第二个用来判「别人的私有视图」与「别人存的视图」。
CONTROL = "claude-control"
OTHER = "claude-control-two"


# ================================================================== 夹具
def _account(account_id: str, *, country: str | None = "GB") -> MarketAccount:
    """一行账号。``evidence_ids`` 不是可省的 —— ``MarketAccount`` 拒收一条没有证据的行
    （「它会读成『我们查到了』，而事实是『我们猜过』」）。"""
    return MarketAccount(
        workspace=WORKSPACE,
        account_id=account_id,
        organisation=f"Org {account_id}",
        evidence_ids=("ev-1",),
        first_seen_at=T0,
        country=country,
        status=AccountStatus.RESEARCHED,
    )


class Deployment:
    """一个部署：五份存储指向**同一个** ``settings.database_path``（组合根就是这么接的）。

    分家的形态是「这条 Watch 的封套写在这一本账上、而读它的那一本在别处」——
    两边各自都正常，而对不出来。
    """

    def __init__(
        self, *, settings: Settings, tmp_path: Path, scene: SceneState = SceneState.ACTIVE
    ) -> None:
        self.settings = settings
        self.tmp_path = tmp_path
        path = settings.database_path
        self.views = SqliteSavedViewStore(path)
        self.watches = SqliteWatchStore(path)
        self.items = SqliteActionItemStore(path)
        self.scenes = SqliteSceneStore(path)
        self.ledger = SqliteBudgetLedger(path)
        # 池子用组合根自己的那个工厂：合并规则只有一处接得上（``default_account_pool_store``）。
        self.pool = default_account_pool_store(settings)()
        self.scenes.put_record(
            SceneRecord(
                scene_id=SCENE,
                workspace=WORKSPACE,
                state=scene,
                latest_version=1,
                effective_version=None if scene is SceneState.DRAFT else 1,
                created_at=T0,
                updated_at=T0,
                pause_reason=PauseReason.OPERATOR if scene is SceneState.PAUSED else None,
            ),
            expected_revision=None,
        )

    # ------------------------------------------------------------ 接线
    def views_table(self) -> dict[str, Any]:
        """视图那四条。**两张表各一个方法**，因为它们是两个参数。"""
        return views_handlers(
            settings=self.settings,
            views=lambda: self.views,
            pool=lambda: self.pool,
        )

    def followups_table(self) -> dict[str, Any]:
        """跟进那五条。``views=`` 与上一张表收到的是**同一个**工厂 —— 一份视图被保存
        的那一版与监控此后按它到期的那一版是同一行。

        **这里一个字都不替 MAIN 补了**：``open_action_item`` 的那一格现在在
        ``followups_handlers`` 的真返回值里（本片落的），所以第六节那几条判的是真表 ——
        而它们在补那一格前后**一行都没改**。
        """
        return followups_handlers(
            settings=self.settings,
            scenes=lambda: self.scenes,
            views=lambda: self.views,
            watches=lambda: self.watches,
            items=lambda: self.items,
            ledger_factory=lambda: self.ledger,
        )

    def handlers(self) -> dict[str, Any]:
        """两张表的并集 —— 目录那一条判据判的是**交给 ``build_server`` 的那两份**，
        所以它从上面两个方法拼出来，而不是另写一遍。"""
        return {**self.views_table(), **self.followups_table()}

    def server(self, ring: TokenRing | None) -> MCPServer:
        """走**真的** ``build_server``。见模块 docstring 那一节。

        其余各族的槽一律缺省（``None``）—— 这一份只判这九条，而「没接线的那几族
        会落回 ``unbacked_message``」是它们自己的判据在管。
        """
        return build_server(
            settings=self.settings,
            tokens=ring,
            views=self.views_table(),
            followups=self.followups_table(),
        )

    def session(
        self,
        principal: str,
        roles: list[Role],
        *,
        workspaces: tuple[str, ...] = (WORKSPACE,),
    ):
        """一个主体的会话。返回一个 ``run(*calls, want_tool_list=)`` 可调用对象。

        环**一次一个文件**（``TokenRing`` 拒绝同一个主体出现两次），所以每个主体各铸
        一枚、各建一个实例 —— 这与「两个人各自拿一枚令牌连同一个服务端」同形。

        ``workspaces`` 的缺省就是本部署那一个（``ring_entry`` 的缺省也是它，这里写成
        显式的一格只是为了给第六节那把「铸给另一个工作区的令牌」留一个入口 —— 那一格
        是**受众之外的另一格**，而拒绝它的地方在 ``guard``，不在这一份里）。
        """
        token, digest = mint(principal, roles)
        path = self.tmp_path / f"tokens-{principal}.json"
        path.write_text(
            json.dumps(
                ring_payload(
                    ring_entry(digest, principal, roles, workspaces=workspaces)
                )
            ),
            encoding="utf-8",
        )
        server = self.server(TokenRing.from_file(path))

        def run(*calls: tuple[str, dict[str, Any]], want_tool_list: bool = False):
            outcome = drive(
                server=server, token=token, calls=calls, want_tool_list=want_tool_list
            )
            assert not outcome.handshake_error, outcome.handshake_error
            return outcome

        return run

    def control(self):
        return self.session(CONTROL, [Role.CONTROLLER])

    def other_control(self):
        return self.session(OTHER, [Role.CONTROLLER])

    def researcher(self):
        return self.session("claude-research", [Role.RESEARCHER])


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = 0) -> str:
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却成功了：{call.text}"
    return call.text


@pytest.fixture
def deployed(settings: Settings, tmp_path: Path) -> Deployment:
    return Deployment(settings=settings, tmp_path=tmp_path)


# ============================================================ 零、工具名
#: 第六条写工具的名字。**这里是一个常量，而不是把字面量散在第六节里**：那几条判据
#: （研究身份看不见它、越权时与「不存在」同形）要它，而它们**不是**在判「这个名字
#: 拼没拼对」—— 名字拼错的话，它们会以 ``Unknown tool: …`` 报出来，那正是它们要的
#: 形状（一条写工具的名字写错了，那一句答复必须与不存在的工具逐字同形）。
OPEN_ACTION_ITEM = "open_action_item"

# ================================================================== 一、目录与接线
def test_the_two_tables_are_exactly_the_nine_control_tools(deployed: Deployment) -> None:
    """工厂交回的名字与目录里的那九条**逐格相等**，而且每一条都是 ``_CONTROL`` / ``K``。

    多一条：目录那一行没承诺过它，而它会出现在 ``tools/list`` 上 —— 一次对外承诺是改
    目录，不是改这里。少一条：那一条落回 stub，调用方读到的是一句「本构建尚未接线」，
    而它看起来像部署没跟上。
    """
    handlers = deployed.handlers()
    assert set(handlers) == set(NINE)
    for name in NINE:
        # 走**模块属性**而不是 from-import 绑下来的那一个名字：第六节的夹具换的是
        # 属性，而 from-import 的那一份在导入那一刻就绑死了 —— 用它会让这一条判的
        # 是「替身还是真目录」之外的第三个东西（一个永远是旧的映射）。
        spec = catalog_module.TOOL_CATALOG[name]
        assert spec.roles == frozenset({Role.CONTROLLER}), f"{name} 不是 _CONTROL"
        assert spec.phase == "K", f"{name} 的 phase 不是 K"


def test_only_the_watch_half_has_a_ledger_in_its_signature(deployed: Deployment) -> None:
    """**承重点 1 的签名半边。**

    ``views_handlers`` 的形参里没有账本，``followups_handlers`` 里有 —— 而这不是
    「恰好没接」：§5.2 逐字「SavedView 只是查询，不产生外部调用、不产生预算」，§5.3
    那半句是「启用 Watch 是写操作，需要权限和预算；**保存 View 不是**」。两个半边各
    需要一个执行器，而**保存视图那一边的执行器就是这个签名**：一个拿得到账本的
    ``put_saved_view`` 在读取路径上多了一格可以顺手 ``set_ceiling`` 的位置，而那件事
    发生的时候没有任何一条判据会响 —— 它只是一次多出来的写入。

    写成**逐格相等**而不是「不含 ledger」：一个改名成 ``budget_writer`` 的参数会让
    否定式判据静默放行。
    """
    assert set(inspect.signature(views_handlers).parameters) == {
        "settings",
        "views",
        "pool",
    }, "视图那一族的签名里多了一格 —— 而它可以顺手写账"
    assert set(inspect.signature(followups_handlers).parameters) == {
        "settings",
        "scenes",
        "views",
        "watches",
        "items",
        "ledger_factory",
    }, "跟进那一族的签名少了账本 —— 「要预算」这句话就没有执行器了"


def test_every_tool_declares_exactly_the_parameters_its_handler_takes(
    deployed: Deployment,
) -> None:
    """**``inputSchema`` 那条回归判据。**

    ``server.add_tool`` 是**从签名推**工具 schema 的，所以一个包住处理器的装饰器
    （``def wrap(*args, **kwargs)``）会把八条工具在 ``tools/list`` 上变成「不吃任何
    参数」—— 而这一条**不会报错**：调用方以为这些工具没有入参，然后每一次调用都拿到
    「缺参数」。所以这里判的不是「有几个参数」，而是**逐格相等**。

    同一批的第二、第三个断言是承重的那一半：谁都不许声明 ``actor`` / ``at`` /
    ``workspace`` / ``by``（它们从令牌与服务端时钟来），而 ``owner`` 只许出现在三条
    上，两条写工具上一格都没有。
    """
    handlers = deployed.handlers()
    tools = anyio.run(deployed.server(None).list_tools)
    by_name = {tool.name: tool for tool in tools}

    for name, handler in handlers.items():
        schema = by_name[name].input_schema
        declared = set(schema.get("properties", {}))
        signature = set(inspect.signature(handler).parameters)
        assert declared == signature, (
            f"{name} 在 tools/list 上声明的是 {sorted(declared)}，"
            f"而处理器收的是 {sorted(signature)} —— 两者不相等时，"
            "调用方拿到的 schema 与这一次调用真正读的东西是两个答案。"
        )
        assert not (declared & FORBIDDEN_PARAMETERS), (
            f"{name} 声明了 {sorted(declared & FORBIDDEN_PARAMETERS)} —— "
            "「谁说的 / 在哪儿说的 / 什么时候说的」必须从令牌与服务端时钟来。"
        )
        owners = declared & {"owner"}
        if name in OWNER_ALLOWED:
            assert owners == {"owner"}, f"{name} 少了 owner：筛选/改派是它的正当用途"
        else:
            assert owners == set(), (
                f"{name} 声明了 owner —— 它是 (workspace, owner, name) 这条键的一半，"
                "一个能填的 owner 就是一条把记录交给别人的入口，而那条历史看起来正常。"
            )


def test_the_expected_version_and_the_visibility_have_no_default(
    deployed: Deployment,
) -> None:
    """三格**必填**：``put_saved_view`` 的 ``expected_revision`` / ``visibility``，
    ``resolve_action_item`` 的 ``expected_version``。

    它们各自补一个缺省都不报错，而是把「我忘了写」静默地变成其中一句话：``null`` 是
    「建一条新的」、数字是「覆盖那一版」；``private`` 会让一次本该共享的视图没人看得见，
    ``shared`` 会让一条本该私有的视图对全工作区可读；而 ``expected_version`` 的缺省
    恰好会是「盘上现在那一版」—— 于是这条工具唯一能防住「两个人同时收同一个」的那一格
    在调用方不写的时候**自己成立**。
    """
    signature = inspect.signature(deployed.handlers()["put_saved_view"])
    for name in ("expected_revision", "visibility"):
        assert signature.parameters[name].default is inspect.Parameter.empty, (
            f"put_saved_view 的 {name} 有了缺省值 —— 「我忘了写」会静默变成其中一句话。"
        )
    assert (
        inspect.signature(deployed.handlers()["resolve_action_item"])
        .parameters["expected_version"]
        .default
        is inspect.Parameter.empty
    )


# ================================================================== 二、角色
def test_a_controller_sees_the_eight_and_a_researcher_sees_none_of_them(
    deployed: Deployment,
) -> None:
    """目录是目录，可见性是可见性：八条都在 ``_CONTROL`` 上，所以研究者一条都看不见。"""
    outcome = deployed.control()(want_tool_list=True)
    assert set(NINE) <= set(outcome.tool_names)

    outcome = deployed.researcher()(want_tool_list=True)
    assert not (set(NINE) & set(outcome.tool_names)), (
        "研究身份看得见这一族的某几条 —— 而 ``put_watch`` 会写账、``resolve_action_item`` "
        "会改别人手上的那一件。"
    )


def test_a_researcher_calling_one_gets_the_same_answer_as_a_missing_tool(
    deployed: Deployment,
) -> None:
    """**越权与「不存在」在协议上同形**（``guard`` 的刻意选择），这一条判的是这一族
    没有把它改掉 —— 而它比「逐条拒绝」更安全：一个能区分两者的答复把目录本身
    变成了一张可枚举的清单。"""
    outcome = deployed.researcher()(
        ("put_saved_view", {"view_id": "v", "name": "n", "query": {"filter": []},
                            "expected_revision": None, "visibility": "shared"}),
        ("no_such_tool_at_all", {}),
    )
    refused = outcome.calls
    assert all(call.is_error for call in refused), "研究者叫得动这一族"
    assert refused[0].text == "Unknown tool: put_saved_view", (
        "越权那条给的不是「不存在」那句话 —— 一个能区分两者的答复把目录本身变成"
        "一张可枚举的清单。"
    )
    assert refused[1].text == "Unknown tool: no_such_tool_at_all"
    assert refused[0].text.split(":", 1)[0] == refused[1].text.split(":", 1)[0], (
        "两句的**句式**要一样，只有工具名不同。"
    )


# ================================================================== 三、视图那四条
def test_a_view_saves_without_a_ledger_and_says_what_it_wrote(
    deployed: Deployment,
) -> None:
    """存一份视图：**零预算**（回执里没有账本那一格），回执里说得出这一版是刚建的。

    这条判据的行为半边在 ``tests/test_views_application.py``（暂停的场景下存视图照样
    可以）；这里判的是它在这一层的形状：一次创建回 ``revision_written: 1``，而回执里
    **没有** ``envelope`` 那一格 —— 一个「顺手报了封套」的实现会让人以为存视图动过账。
    """
    payload = _ok(
        deployed.control()(
            (
                "put_saved_view",
                {
                    "view_id": "view-gb",
                    "name": "英国的",
                    "query": {"filter": [{"field": "country", "op": "is", "value": "GB"}]},
                    "expected_revision": None,
                    "visibility": "shared",
                    "note": "第一版",
                },
            )
        )
    )
    assert payload["revision_written"] == 1
    assert payload["view"]["view_id"] == "view-gb"
    assert payload["view"]["owner"] == CONTROL, "owner 必须是令牌上那个主体"
    assert payload["view"]["revision"] == 1
    assert payload["view"]["visibility"] == "shared"
    assert "envelope" not in payload, "存视图的回执里出现了账本那一格"
    # 账本里一条上限都不该有：这一族没有账本可拿。
    assert deployed.ledger.usage(f"{WORKSPACE}:watch:view-gb") == ()


def test_resending_an_identical_view_writes_no_second_version(
    deployed: Deployment,
) -> None:
    """**同内容重发不是一版新的**（``revision_written: null``），而回执说得出这件事。

    「你刚才那次成功了」与「你第二次点的时候什么都没发生」是两句不同的话，而只有
    ``revision_written`` 一格时读的人要自己去端口 docstring 里查 ``None`` 是哪一边。
    """
    arguments = {
        "view_id": "view-same",
        "name": "同一份",
        "query": {"filter": []},
        "visibility": "shared",
    }
    control = deployed.control()
    assert _ok(control(("put_saved_view", {**arguments, "expected_revision": None})))[
        "revision_written"
    ] == 1
    payload = _ok(control(("put_saved_view", {**arguments, "expected_revision": 1})))
    assert payload["revision_written"] is None
    assert payload["view"]["revision"] == 1
    assert "一字不差" in payload["note"], (
        "重发那一趟的回执没有说「没有新版本落盘」—— 它读起来像一次成功的写入。"
    )


def test_a_view_that_was_never_saved_is_a_refusal_that_names_the_id(
    deployed: Deployment,
) -> None:
    """读一份不存在的视图是一次**拒绝**（不是 ``found: false``）：没有可读的那一份，
    而它的下一步是「去存一份」，与「它在那儿、只是空的」不同。"""
    text = _refused(deployed.control()(("get_saved_view", {"view_id": "view-nope"})))
    assert "view-nope" in text


def test_the_list_is_not_only_mine_and_filters_by_owner_when_asked(
    deployed: Deployment,
) -> None:
    """``owner`` 缺省 = **不过滤**，不是「只看我的」。

    一个「默认只看自己的」实现会让同一条工作区里别人存的视图从报告里消失，而消失与
    「他没有存过」同形 —— 而两条路线的负责人正是靠看见对方的视图来对齐的。
    """
    save = {
        "name": "视图",
        "query": {"filter": []},
        "expected_revision": None,
        "visibility": "shared",
    }
    deployed.control()(("put_saved_view", {"view_id": "view-mine", **save}))
    deployed.other_control()(("put_saved_view", {"view_id": "view-theirs", **save}))

    everything = _ok(deployed.control()(("list_saved_views", {})))
    assert {row["view_id"] for row in everything["views"]} == {"view-mine", "view-theirs"}
    assert everything["owner_filter"] is None

    mine = _ok(deployed.control()(("list_saved_views", {"owner": CONTROL})))
    assert [row["view_id"] for row in mine["views"]] == ["view-mine"]
    assert mine["owner_filter"] == CONTROL


def test_one_account_in_three_views_is_one_account_read_three_times(
    deployed: Deployment,
) -> None:
    """**「如实报」的第一条，在一份回执里读得出来。**

    ``accounts_read`` 是三份视图各自读一遍池子的**累计**（9），``accounts_distinct``
    是回给你的那些行里有几个不同的 account_id（3）。两格都在，而**那个差**就是这句
    验收口径。只留前者时那句话读不出来；只留后者时「读了几遍」消失，而一个顺手去重的
    实现在报告上看不出区别。
    """
    for account_id in ("acct-1", "acct-2", "acct-3"):
        deployed.pool.upsert_account(_account(account_id))
    control = deployed.control()
    for view_id in ("view-one", "view-two", "view-three"):
        control(
            (
                "put_saved_view",
                {
                    "view_id": view_id,
                    "name": view_id,
                    "query": {"filter": []},
                    "expected_revision": None,
                    "visibility": "shared",
                },
            )
        )

    payload = _ok(control(("run_saved_view", {"view_ids": ["view-one", "view-two", "view-three"]})))

    summary = payload["summary"]
    assert [run["view_id"] for run in payload["runs"]] == [
        "view-one",
        "view-two",
        "view-three",
    ]
    assert [run["accounts_read"] for run in payload["runs"]] == [3, 3, 3]
    assert summary["accounts_read"] == 9, "同一个账号在三份视图里就是读了三次"
    assert summary["accounts_distinct"] == 3
    assert summary["rows"] == 9 and summary["views"] == 3
    assert summary["runnable"] == 3 and summary["refused"] == 0
    assert "去重" in payload["note"]


def test_a_view_that_cannot_run_names_the_part_and_the_others_still_run(
    deployed: Deployment,
) -> None:
    """**「如实报」的第二条。**

    一份一半能跑的查询回**零行**并点名 ``filter[1].field`` —— 把活下来的那几条拼起来
    跑会回一批看起来正常的行，而它比调用方写的那份查询**小**，且少回的行与「本来就没有」
    在报告上同形。另一份照跑：一份查询的形状错与另外几份无关。
    """
    deployed.pool.upsert_account(_account("acct-1"))
    control = deployed.control()
    for view_id, query in (
        (
            "view-half",
            {
                "filter": [
                    {"field": "country", "op": "is", "value": "GB"},
                    {"field": "nope", "op": "is", "value": "x"},
                ]
            },
        ),
        ("view-fine", {"filter": []}),
    ):
        control(
            (
                "put_saved_view",
                {
                    "view_id": view_id,
                    "name": view_id,
                    "query": query,
                    "expected_revision": None,
                    "visibility": "shared",
                },
            )
        )

    payload = _ok(control(("run_saved_view", {"view_ids": ["view-half", "view-fine"]})))

    broken, fine = payload["runs"]
    assert broken["ok"] is False and broken["rows"] == []
    assert broken["accounts_read"] == 0, "跑不动的那一份连池子都不该读"
    assert [part["part"] for part in broken["unrunnable"]] == ["filter[1].field"]
    for part in broken["unrunnable"]:
        assert part["part"] in broken["refusal"]
        assert part["reason"] in broken["refusal"]
    assert fine["ok"] is True and fine["accounts_read"] == 1
    assert payload["summary"]["runnable"] == 1 and payload["summary"]["refused"] == 1
    assert payload["summary"]["accounts_read"] == 1, "跑不动的那一份不该被算进读了几行"


def test_a_private_view_is_refused_to_anything_but_its_owner(
    deployed: Deployment,
) -> None:
    """``private`` 是**存在盘上的一条读路径判据**，而 ``run_saved_view`` 是它唯一的
    执行器 —— 一个只把它存下来而从不读它的实现，会让这个字段在报告上看起来生效过。"""
    deployed.pool.upsert_account(_account("acct-1"))
    deployed.control()(
        (
            "put_saved_view",
            {
                "view_id": "view-private",
                "name": "自己的",
                "query": {"filter": []},
                "expected_revision": None,
                "visibility": "private",
            },
        )
    )

    own = _ok(deployed.control()(("run_saved_view", {"view_ids": ["view-private"]})))
    assert own["runs"][0]["rows"][0]["account_id"] == "acct-1"

    text = _refused(
        deployed.other_control()(("run_saved_view", {"view_ids": ["view-private"]}))
    )
    assert CONTROL in text and OTHER in text, (
        "拒绝没有同时点名 owner 与本次 actor —— 读的人要自己去找是谁的。"
    )
    # 而**读清单**不受这条判据影响：可见性是执行器上的，不是目录上的。
    assert (_ok(deployed.other_control()(("list_saved_views", {}))))["count"] == 1


def test_an_empty_or_blank_view_id_list_is_refused(deployed: Deployment) -> None:
    """一份零项的回执（``runs: []``、``accounts_read: 0``）与「这些视图一条都没匹配上」
    在报告上同形，而它其实什么都没跑。"""
    control = deployed.control()
    empty = _refused(control(("run_saved_view", {"view_ids": []})))
    assert "view_ids" in empty
    blank = _refused(control(("run_saved_view", {"view_ids": [""]})))
    assert "空白" in blank


def test_a_visibility_that_is_not_one_is_refused_by_name(deployed: Deployment) -> None:
    """认不出的可见性**点名认识的取值**，而不是在下游抛一行枚举错误 —— 一个被静默
    当成 ``shared`` 的取值会让一条本该私有的视图对全工作区可读，而那份回执看起来
    完全正常。"""
    text = _refused(
        deployed.control()(
            (
                "put_saved_view",
                {
                    "view_id": "view-x",
                    "name": "n",
                    "query": {"filter": []},
                    "expected_revision": None,
                    "visibility": "public",
                },
            )
        )
    )
    assert "public" in text and "private" in text and "shared" in text


def test_a_taken_name_or_a_stale_basis_is_refused_not_crashed(
    deployed: Deployment,
) -> None:
    """**承重点 6 与 CAS 在工具面上的落点，也是这一族曾经漏掉的那一类拒绝。**

    两件事必须分开读：

    * **改一个视图的名字不撞车** —— ``view-a`` 改成别的名字照样成，因为判据的粒度是
      「**当前版**」而不是「有过这个名字」（一条普通唯一索引会把历史版本一起挡掉，
      于是改名在库上做不成）。
    * **新建 / 改名到一条已被别人占着的当前版名字上要失败**，而失败必须**读得出来**
      —— 存储那句话说清了「这个名字下已经有一条当前版：view_id=… 第 n 版」。

    这一条同时是那两条**兄弟异常**的回归判据：``ConfigError``（说在改第 N 版而盘上
    一版都没有）与 ``SavedViewConflictError`` 都不是 ``StoreError``，只接 ``StoreError``
    时它们逃出去，调用方读到的是光秃秃一句 ``Error executing tool put_saved_view``。
    把 ``views.py::_put`` 里那一条 ``except (ConfigError, SavedViewConflictError)``
    删掉，这一条会红在**异常类型**上（``UnexpectedToolError``），而不是红在某句文案上。
    """
    control = deployed.control()

    def save(view_id: str, name: str, expected_revision: int | None):
        return control(
            (
                "put_saved_view",
                {
                    "view_id": view_id,
                    "name": name,
                    "query": {"filter": []},
                    "expected_revision": expected_revision,
                    "visibility": "shared",
                },
            )
        )

    assert _ok(save("view-a", "甲", None))["revision_written"] == 1
    assert _ok(save("view-b", "乙", None))["revision_written"] == 1

    # 新建一个同名的当前版：拒绝，并点名占着那个名字的 view_id。
    taken = _refused(save("view-c", "乙", None))
    assert "view-b" in taken and "乙" in taken, (
        "重名那条拒绝没有点名**是哪一条**占着这个名字 —— 读的人要自己去清单里找。"
    )

    # 改名到别人占着的名字上：同样拒绝。
    taken = _refused(save("view-a", "乙", 1))
    assert "view-b" in taken
    # 而改到没人占的名字上照样成 —— 拒绝没有把改名本身一起挡掉。
    assert _ok(save("view-a", "丙", 1))["view"]["revision"] == 2

    # 手上那本基准**已经过期**（盘上在第 2 版，调用方说第 1 版）：拒绝。
    #
    # 这里如实记下这一句**读出来是什么样**，而不是记下它「应该」是什么样：
    # 盘上在第 2 版时，应用层用**读到的那一行**算出下一版是 3，于是存储那一侧
    # ``put_view`` 的第一道闸（``view.revision != expected_revision + 1``）先响，
    # 给的是版本算术那句话。它点出了手上的基准与本次算出来的版本，而**没有**说
    # 「盘上现在是第 2 版」—— 那条话说在 ``_require_base`` 里，而它经由应用层
    # 永远轮不到（应用层总是拿刚读到的那一行去加一）。这是措辞上的一个缺口，
    # 如实记在 REPORT 里；判据这里只钉「它是一次点名了基准的拒绝，不是一次崩溃」。
    stale = _refused(save("view-a", "丁", 1))
    assert "view-a" in stale and "基准 1" in stale and "本次是 3" in stale, (
        "过期基准那条拒绝没说出手上的基准与本次算出来的版本 —— 而它必须是一次拒绝。"
    )
    # 说「我在改第 1 版」而那个 id 一版都没有：拒绝（不是崩溃）。
    missing = _refused(save("view-never", "戊", 1))
    assert "view-never" in missing
    # 而盘上什么都没被这些失败动过。
    assert [view.view_id for view in deployed.views.views(workspace=WORKSPACE)] == [
        "view-a",
        "view-b",
    ]


# ================================================================== 四、Watch 那一条
def _watch_call(**overrides: Any) -> tuple[str, dict[str, Any]]:
    arguments: dict[str, Any] = {
        "watch_id": "watch-weekly",
        "name": "每周看一次",
        "ceilings": [{"line": BudgetLine.SEARCH.value, "units": 10}],
        "notify_on": [NotifyOn.CHANGED.value],
        "expected_revision": None,
        "view_id": "view-gb",
    }
    arguments.update(overrides)
    return ("put_watch", arguments)


def test_put_watch_takes_the_budget_and_the_envelope_reads_back(
    deployed: Deployment,
) -> None:
    """**承重点 1 的行为半边，在工具面上。**

    ``put_watch`` 的回执里有一格 ``envelope``，而它写下的东西要能从**账本本身**读回来
    —— 判据不是「回执说了什么」，是 ``SqliteBudgetLedger.usage(scope)`` 给出的上限。
    一个只回一句「已配置」的实现会让这一族与账本之间的关系只存在于回执的措辞里。

    ``source`` 那一格只能从**回执**读：账本端口今天没有读它的入口（``usage`` 只回
    ``line / ceiling / spent / reserved / refunded``）。这一条**如实记在报告里**，
    所以这里断言的是回执里的 source 形如 ``watch:<id>:by:<actor>``，以及库里那一行
    确实存在 —— 而不是假装读得回来。
    """
    deployed.control()(
        (
            "put_saved_view",
            {
                "view_id": "view-gb",
                "name": "英国的",
                "query": {"filter": []},
                "expected_revision": None,
                "visibility": "shared",
            },
        )
    )

    payload = _ok(
        deployed.control()(
            _watch_call(
                ceilings=[
                    {"line": BudgetLine.SEARCH.value, "units": 10},
                    {"line": BudgetLine.DOCUMENT.value, "units": 4},
                ]
            )
        )
    )

    assert payload["revision_written"] == 1
    assert payload["watch"]["target_state"] == "view"
    assert payload["watch"]["target_missing"] is False
    envelope = payload["envelope"]
    scope = f"{WORKSPACE}:watch:watch-weekly"
    assert envelope["budget_scope"] == scope
    assert [(row["line"], row["units"]) for row in envelope["ceilings"]] == [
        ("search", 10),
        ("document", 4),
    ]
    for row in envelope["ceilings"]:
        assert row["scope"] == scope
        assert row["source"] == f"watch:watch-weekly:by:{CONTROL}", (
            "source 里的当事人必须是令牌上那个 —— 一个能传的署名让调用方把一次上限"
            "变更记在别人头上，而账本上那一格看起来完全正常。"
        )

    # 从账本本身读回来。
    usage = {entry.line.value: entry for entry in deployed.ledger.usage(scope)}
    assert usage["search"].ceiling == 10 and usage["document"].ceiling == 4
    assert usage["search"].held == 0, "写封套不是一次支出"
    with sqlite3.connect(deployed.settings.database_path) as conn:
        rows = conn.execute(
            "SELECT line, units, source FROM budget_ceiling WHERE scope = ? ORDER BY line",
            (scope,),
        ).fetchall()
    assert rows == [
        ("document", 4, f"watch:watch-weekly:by:{CONTROL}"),
        ("search", 10, f"watch:watch-weekly:by:{CONTROL}"),
    ], "封套没落到库里 —— 那它只存在于回执的措辞里"
    assert "没有支出方预留到这个 scope 上" in envelope["note"], (
        "回执没有如实说「今天没有支出方预留到这个账上」—— 那是这一片与 §5.3 之间"
        "还差的一段接线。"
    )


def test_put_watch_without_ceilings_or_notify_on_is_refused(
    deployed: Deployment,
) -> None:
    """两格都必填且非空（域那一侧判空）。空的 ``ceilings`` 会让「要预算」这句话在
    这一次写入上不成立，而它不会报错 —— 它只是不写账。"""
    control = deployed.control()
    no_ceiling = _refused(control(_watch_call(ceilings=[])))
    assert "上限" in no_ceiling or "ceiling" in no_ceiling
    no_notify = _refused(control(_watch_call(notify_on=[])))
    assert "通知" in no_notify or "notify" in no_notify
    # 而两次失败都没有在盘上留下任何一版。
    assert deployed.watches.get_watch("watch-weekly") is None


def test_a_watch_on_a_paused_scene_is_refused_not_queued(
    settings: Settings, tmp_path: Path
) -> None:
    """**承重点 1 的行为半边，在工具面上。**

    排队比拒绝更坏：它让「我明明开了监控」与「它一次都不会响」同时为真。而同一次暂停
    之下**存视图照样可以**（那一条在应用层，这里只判工具面把拒绝接住了、翻成了回执）。
    """
    paused = Deployment(settings=settings, tmp_path=tmp_path, scene=SceneState.PAUSED)
    control = paused.control()

    text = _refused(control(_watch_call()))
    assert SCENE in text and "paused" in text
    assert paused.watches.get_watch("watch-weekly") is None

    saved = _ok(
        control(
            (
                "put_saved_view",
                {
                    "view_id": "view-gb",
                    "name": "英国的",
                    "query": {"filter": []},
                    "expected_revision": None,
                    "visibility": "shared",
                },
            )
        )
    )
    assert saved["revision_written"] == 1, "暂停把存视图也挡住了 —— §5.2 逐字「保存 View 不是」"


def test_a_watch_on_another_deployment_s_scene_is_refused(deployed: Deployment) -> None:
    """``require_scene`` 判的是「这个请求说的是不是本部署服务的那个市场」。一台部署
    只服务一个 Scene —— 证据、预算与凭据都属于它。"""
    text = _refused(deployed.control()(_watch_call(scene_id="scene-somewhere-else")))
    assert "scene-somewhere-else" in text and WORKSPACE in text


def test_a_watch_that_points_at_a_deleted_view_is_reported_as_that(
    deployed: Deployment,
) -> None:
    """**「如实报」的第三条。**

    ``enabled`` / ``target_state`` / ``target_missing`` 三格一起读，才能把「关了」与
    「目标没了」分开。只有 ``enabled`` 时，一条指着已删视图的 Watch 读起来与一条安静
    待着的 Watch 一模一样，而它们的处置相反。
    """
    control = deployed.control()
    control(_watch_call(watch_id="watch-gone", view_id="view-gone"))
    control(
        (
            "put_saved_view",
            {
                "view_id": "view-gb",
                "name": "英国的",
                "query": {"filter": []},
                "expected_revision": None,
                "visibility": "shared",
            },
        )
    )
    control(_watch_call(watch_id="watch-live", view_id="view-gb"))
    control(
        _watch_call(
            watch_id="watch-off",
            view_id="view-gb",
            enabled=False,
        )
    )

    payload = _ok(control(("list_watches", {})))
    cards = {card["watch_id"]: card for card in payload["watches"]}

    gone = cards["watch-gone"]
    assert gone["target_state"] == "view_missing" and gone["target_missing"] is True
    assert gone["enabled"] is True, (
        "目标没了**不是**「静默变不活跃」—— 开关那一格照实回它自己的取值。"
    )
    assert cards["watch-live"]["target_state"] == "view"
    assert cards["watch-live"]["target_missing"] is False
    assert cards["watch-off"]["target_missing"] is False, (
        "关掉的 Watch 与目标没了是两件事 —— 一个从 enabled 推 target_missing 的实现"
        "会在这里把它们读成同一件。"
    )
    assert payload["targets_missing"] == ["watch-gone"], (
        "那一条便利清单没有把它拎出来 —— 它存在的唯一理由就是先看这几条。"
    )
    assert payload["enabled_filter"] is None and payload["count"] == 3

    only_off = _ok(control(("list_watches", {"enabled": False})))
    assert [card["watch_id"] for card in only_off["watches"]] == ["watch-off"]


def test_the_written_watch_says_the_same_thing_as_the_list(deployed: Deployment) -> None:
    """写下来的那一版与清单读出来的那一版**逐格同形**（``watch_card`` 一处定义）。

    两处各写一份的那天，「刚开的这条指着什么」与「清单说它指着什么」会先分家，而分家
    的形态是一条指向已删视图的 Watch 在写入回执里看起来正常。
    """
    control = deployed.control()
    written = _ok(control(_watch_call(view_id="view-never-saved")))
    listed = _ok(control(("list_watches", {})))
    card = next(row for row in listed["watches"] if row["watch_id"] == "watch-weekly")
    assert written["watch"] == card
    assert written["watch"]["target_state"] == "view_missing"


# ================================================================== 五、跟进池
def _seed_item(
    deployed: Deployment,
    action_id: str,
    *,
    owner: str = CONTROL,
    scope_id: str = "acct-1",
    action_type: ActionType = ActionType.VERIFY_REFERRAL_CONTACT,
) -> None:
    """把一件待办放进池子。**走应用层**，理由只剩一个：``at=T0``。

    工具面那一条（第六节）用的是**服务端时钟**（``at`` 不是一个入参），而这一份里
    的用例判的是「读回来的那一格对不对」——它们要一个**钉住的时刻**，于是直接调
    ``application/views.py::open_action_item``。两条路并存不是绕路：第六节判的正是
    「工具面真的调得到」，而这一条判的是它之外那七条的行为。

    ``scope_id`` 与 ``action_type`` 是参数而不是写死的：同一主体上的同一类待办**只许
    有一条未结的**（那条 partial unique index），所以两件形状一样的待办根本建不出来
    —— 那个插入失败本身是另一份文件（``tests/test_sqlite_views.py``）的判据，这里
    只是不去撞它。
    """
    open_action_item(
        store=deployed.items,
        action_id=action_id,
        workspace=WORKSPACE,
        scope_kind=ScopeKind.ACCOUNT,
        scope_id=scope_id,
        action_type=action_type,
        reason="这个账号的推荐联系人还没核实过。",
        owner=owner,
        at=T0,
    )


def test_the_pool_shows_every_state_by_default_and_reads_the_subject_back(
    deployed: Deployment,
) -> None:
    """``open_only`` 的缺省是 ``false``：一个「默认只回未结的」实现会让「他有一条做完的」
    与「他从来没做过」在报告上同形。

    ``subject`` 是从 ``scope_ref`` **读回来**的，而 ``scope_ref`` 原样也留着 ——
    一条挂在路线上的待办不许看起来挂在账号上。
    """
    _seed_item(deployed, "act-open")
    _seed_item(deployed, "act-done", action_type=ActionType.FILL_RESPONSIBILITY)
    resolved = _ok(
        deployed.control()(
            (
                "resolve_action_item",
                {
                    "action_id": "act-done",
                    "expected_version": 1,
                    "state": "done",
                    "resolution": "办理完毕。",
                },
            )
        )
    )
    assert resolved["revision_written"] == 2

    payload = _ok(deployed.control()(("list_action_items", {})))
    states = {row["action_id"]: row["state"] for row in payload["action_items"]}
    assert states == {"act-open": "open", "act-done": "done"}
    assert payload["open_only"] is False and payload["count"] == 2

    rows = {row["action_id"]: row for row in payload["action_items"]}
    row = rows["act-open"]
    assert row["subject"] == {"kind": "account", "subject_id": "acct-1"}
    assert row["scope_ref"].startswith("account")
    assert row["expected_version"] == 0, (
        "刚建出来的那一件 expected_version 应当是 0（创建没有可基于的那一版）—— "
        "把它写成 1 会让「刚建出来」与「有人处置过一版」在回执上同形"
        "（``ActionItem.expected_version`` 逐字：创建时是 0）。"
    )
    # ``disposition_digest`` 那一格是**内容**（状态、结果、谁做的、在等什么、期限、
    # 证据），不是「第几次」—— 所以两件处置不同的行摘要不同，而重发同一件时它相同。
    # 它是重发判据的两个半边之一（另一半是版本），必须**从这份回执里**拿得到。
    assert row["disposition_digest"] and (
        row["disposition_digest"] != rows["act-done"]["disposition_digest"]
    ), "两件处置不同的待办给出了同一个内容摘要 —— 那它就不是内容摘要了"

    open_only = _ok(deployed.control()(("list_action_items", {"open_only": True})))
    assert [row["action_id"] for row in open_only["action_items"]] == ["act-open"]

    by_other = _ok(deployed.other_control()(("list_action_items", {"owner": OTHER})))
    assert by_other["count"] == 0 and by_other["action_items"] == []
    by_me = _ok(deployed.control()(("list_action_items", {"owner": CONTROL})))
    assert by_me["count"] == 2
    assert by_me["owner_filter"] == CONTROL


def test_a_state_that_is_not_one_is_refused_by_name(deployed: Deployment) -> None:
    """一个认不出的状态被静默当成「不过滤」，会让一份本该只有几行的清单回出整个池子
    —— 而它读起来完全正常。"""
    _seed_item(deployed, "act-open")
    text = _refused(deployed.control()(("list_action_items", {"state": "finished"})))
    assert "finished" in text
    assert "open" in text and "done" in text and "dismissed" in text, (
        "拒绝没有点名认识的取值 —— 读的人要自己去枚举里找。"
    )
    resolved = _refused(
        deployed.control()(
            (
                "resolve_action_item",
                {
                    "action_id": "act-open",
                    "expected_version": 1,
                    "state": "closed",
                },
            )
        )
    )
    assert "closed" in resolved


def test_a_stale_resolution_is_refused_and_changes_nothing(deployed: Deployment) -> None:
    """**承重点 5 在工具面上的落点。**

    第一次处置写第 2 版；第二次拿着**同一本基准**（1）来 —— 拒绝，而且盘上那一件不动。
    「过期认输」是这条工具唯一能防住「两个人同时收同一个」的地方。

    这里第一步收成 ``claimed`` 而**不是** ``done``，而这不是随手挑的：终态那一格有自己的
    判据，而它排在版本判据**之前**（``ActionItem.resolve`` 逐字：「终态不许再被处置」
    在先）。拿一件已经 ``done`` 的来判过期，测到的是那条终态判据，而两条判据的措辞
    很像、处置却不同（一条是「新建一件」，一条是「重读再决定」）。
    """
    _seed_item(deployed, "act-1")
    control = deployed.control()
    first = _ok(
        control(
            (
                "resolve_action_item",
                {
                    "action_id": "act-1",
                    "expected_version": 1,
                    "state": "claimed",
                    "owner": CONTROL,
                },
            )
        )
    )
    assert first["revision_written"] == 2 and first["item"]["state"] == "claimed"
    digest = first["item"]["disposition_digest"]
    assert digest, "处置过的这一件要留下摘要 —— 它是重发幂等判据的一半"

    text = _refused(
        control(
            (
                "resolve_action_item",
                {
                    "action_id": "act-1",
                    "expected_version": 1,
                    "state": "done",
                    "resolution": "办理完毕。",
                },
            )
        )
    )
    current = deployed.items.get("act-1")
    assert current is not None
    assert (current.revision, current.state.value) == (2, "claimed"), (
        "过期的那一次写出了东西 —— 而它拿的是一本已经被别人改过的基准。"
    )
    assert "1" in text and "2" in text, (
        "拒绝没有同时点出手上的基准与盘上那一版 —— 那是这句话唯一的用处。"
    )


def test_an_unknown_action_id_is_a_different_refusal_from_a_stale_one(
    deployed: Deployment,
) -> None:
    """「这个 action_id 不在盘上」与「你手上的版本过期了」是两句不同的话：前者要去
    看看是谁写的 id，后者要去重读一次。合成一句会把两种处置压成一格。"""
    _seed_item(deployed, "act-1")
    control = deployed.control()
    unknown = _refused(
        control(
            (
                "resolve_action_item",
                {"action_id": "act-nope", "expected_version": 1, "state": "done",
                 "resolution": "x"},
            )
        )
    )
    stale = _refused(
        control(
            (
                "resolve_action_item",
                {"action_id": "act-1", "expected_version": 7, "state": "done",
                 "resolution": "x"},
            )
        )
    )
    assert unknown != stale
    assert "act-nope" in unknown
    assert deployed.items.get("act-nope") is None


def test_a_terminal_item_refuses_the_next_resolution(deployed: Deployment) -> None:
    """收成终态之后再动它是一次**拒绝**（而不是又一次写入）—— 这一条先于版本判据，
    因为「这一件已经收了」与「你拿的版本旧了」是两件事。"""
    _seed_item(deployed, "act-1")
    control = deployed.control()
    control(
        (
            "resolve_action_item",
            {"action_id": "act-1", "expected_version": 1, "state": "done",
             "resolution": "办理完毕。"},
        )
    )
    text = _refused(
        control(
            (
                "resolve_action_item",
                {"action_id": "act-1", "expected_version": 2, "state": "claimed",
                 "owner": OTHER},
            )
        )
    )
    assert "done" in text
    assert deployed.items.get("act-1").state.value == "done"


# ================================================================== 六、建待办
def _open_call(
    action_id: str = "act-new",
    *,
    scope_kind: str = "route",
    scope_id: str = "route-9",
    action_type: str = "verify_referral_contact",
    **overrides: Any,
) -> tuple[str, dict[str, Any]]:
    """一次 ``open_action_item`` 的调用载荷。

    ``scope_kind`` 取 ``route`` 而不是账户：这一个用例要判「主语那一格原样过桥」——
    而在账户上它恰好与「默认值」同形，一个把 ``scope_kind`` 写死成 ``account`` 的实现
    在那条路上照样绿。
    """
    arguments: dict[str, Any] = {
        "action_id": action_id,
        "scope_kind": scope_kind,
        "scope_id": scope_id,
        "action_type": action_type,
        "reason": "这条路线上的转介联系人还没核实过。",
    }
    arguments.update(overrides)
    return (OPEN_ACTION_ITEM, arguments)


def test_the_new_tool_is_callable_over_a_real_session(deployed: Deployment) -> None:
    """**判的不是「函数存在」，是「它调得到」。**

    走的是一次真的 ``tools/call``（真的 ``MCPServer``、真的中间件、真的令牌），而
    这一条路上的三道门是**分开**的三件事：``_register`` 按目录注册（目录里没有它就连
    构造都过不去）、``RoleGuard.backed`` 按处理器表放行、``visible_tools`` 按角色把它
    列出来。中间任何一道没接上，这里拿到的都不是一份回执。

    ``list_action_items`` 是第二步：一条「建了但读不回来」的路在一份回执上看起来
    完全正常，而它是这一族最贵的那种错（池子是给人看的那一面）。

    **这里判的是真件**：目录那一行在 ``security/catalog.py`` 里、这一格在
    ``followups_handlers`` 的返回值里，两处都是盘上那一份，本文件不再有替身。
    """
    outcome = deployed.control()(
        _open_call(),
        ("list_action_items", {}),
        want_tool_list=True,
    )
    assert OPEN_ACTION_ITEM in outcome.tool_names, (
        "控制器在 tools/list 上看不见它 —— 这一条是「对外承诺过」的那一半。"
    )
    created = _ok(outcome, 0)
    item = created["item"]
    assert created["revision_written"] == 1, (
        "新建一件的 ``revision_written`` 是 1（``ports/views.py::ActionItemStore.create`` "
        "逐字返回 ``item.revision``）—— ``None`` 在那里是**重发**，两件事不能同形。"
    )
    assert item["state"] == "open" and item["expected_version"] == 0, (
        "新开的一件是第 1 版、``expected_version`` 为 0（``ActionItem.opened`` 逐字）——"
        "把它写成 1 会让「刚建出来」与「有人处置过一版」在回执上同形。"
    )
    assert item["subject"] == {"kind": "route", "subject_id": "route-9"}, (
        "主语那一格没有原样过桥 —— 一条挂在路线上的待办会看起来挂在账号上。"
    )
    assert item["workspace"] == WORKSPACE, (
        "回执里的 workspace 不是本部署那一个 —— 而 ``workspace`` **不是一个入参**，"
        "所以它只能是服务端填错。"
    )
    assert item["owner"] is None, "这一次调用没有点名负责人，回执不该替他认领"

    pool = _ok(outcome, 1)
    assert [row["action_id"] for row in pool["action_items"]] == ["act-new"]
    assert pool["action_items"][0]["subject"] == {
        "kind": "route",
        "subject_id": "route-9",
    }


def test_a_researcher_neither_sees_nor_calls_it(deployed: Deployment) -> None:
    """目录说它是 ``_CONTROL``，所以研究身份看不见它，而「看不见」与「调不动」是两件
    都要做的事（``catalog.visible_tools`` 逐字）。"""
    outcome = deployed.researcher()(_open_call(), want_tool_list=True)
    assert OPEN_ACTION_ITEM not in outcome.tool_names
    assert _refused(outcome, 0) == f"Unknown tool: {OPEN_ACTION_ITEM}", (
        "越权与「不存在」在协议上必须同形 —— 一个能区分两者的答复把目录变成了一张"
        "可枚举的清单（``guard.unknown_tool_result`` 逐字）。"
    )
    assert deployed.items.get("act-new") is None


def test_a_token_minted_for_another_workspace_cannot_open_one(
    deployed: Deployment,
) -> None:
    """**越权的那个工作区。** 角色过了、受众过了，栽在 ``workspaces`` 那一格上。

    拒绝由 ``RoleGuard`` 给（``require_workspace``），位置在**角色之后、背书之前**；
    而这里要一起判的是第二件事：**盘上什么都没写**。「被拒了」与「被拒了但写了一半」
    在两个回执上长得一样。
    """
    foreign = deployed.session(
        CONTROL, [Role.CONTROLLER], workspaces=("another-market",)
    )
    text = _refused(foreign(_open_call()))
    assert "工作区" in text and "重铸" in text, f"拒绝没有说清是哪一格：{text}"
    assert OPEN_ACTION_ITEM in text, (
        "拒绝没有点名这一次调用是哪一条工具 —— 一个主体同时握着几条令牌时，"
        "看这句话的人分不出要重铸的是哪一条路。"
    )
    assert WORKSPACE not in text, (
        "拒绝里出现了本部署的工作区名 —— 那一句逐字写着「**不报出**本部署的工作区是"
        "哪一个」：一条不属于这里的令牌不该从拒绝里学到这里的名字。"
    )
    assert deployed.items.get("act-new") is None, "被拒的那一次写出了东西"
    assert _ok(deployed.control()(("list_action_items", {})))["count"] == 0


def test_replaying_the_same_open_is_idempotent_and_a_second_open_item_is_refused(
    deployed: Deployment,
) -> None:
    """**重复幂等**的两个半边，而它们是两句不同的话。

    * 同一个 ``action_id`` 配同一份内容 ⇒ **重发**：盘上那一件原样回来，
      ``revision_written=None``（``ports/views.py::ActionItemStore.create`` 逐字）。
    * 同一个主体上的第二条**未结**待办 ⇒ **拒绝**，并点名盘上那一条的 ``action_id``
      —— 「去处理那一条」与「再建一条」是两种处置，而它们的区别只在那句话里
      （``errors.py::OpenActionItemExistsError`` 逐字）。

    两条合起来才是 §B24 那条关闭条件里的「Action 重放幂等」：一条只判前者的用例会让
    「第二条未结的」这件事完全没人管，而它正是那条 partial unique index 存在的理由。
    """
    control = deployed.control()
    first = _ok(control(_open_call()))
    assert first["revision_written"] == 1

    again = _ok(control(_open_call()))
    assert again["revision_written"] is None, (
        "同一次创建重发时产生了第二版 —— 而「一模一样的一次」不该是一次新历史。"
    )
    assert again["item"]["action_id"] == first["item"]["action_id"]
    assert again["item"]["revision"] == first["item"]["revision"]

    text = _refused(control(_open_call("act-second")))
    assert "act-new" in text, (
        "拒绝没有点名盘上那一条的 ``action_id`` —— 只说「已经存在」的回执把调用方"
        "留在原地（``errors.py`` 逐字）。"
    )
    assert deployed.items.get("act-second") is None
    assert _ok(control(("list_action_items", {})))["count"] == 1, (
        "池子里多了一条 —— 第二条未结的待办正是那条 partial unique index 要挡的东西。"
    )


def test_the_version_a_create_hands_back_is_the_half_that_goes_stale(
    deployed: Deployment,
) -> None:
    """**过期 version 拒写**，落在这条工具的**回执**上。

    ``open_action_item`` 自己**没有** ``expected_version`` 这一格，而这不是漏了：
    ``require_creatable`` 逐字要求 ``revision == 1`` 且 ``expected_version == 0`` ——
    创建没有可基于的那一版，所以这里没有「拿哪一版去写」这个问题。那条纪律在同一个
    对象的下一次写入上（``resolve_action_item``），而**要带回去的那一格正是这一份
    回执给出来的**（``item.expected_version``）。

    所以这一条判的是那个接缝：从创建回执里取出的版本，在别人先动过之后**被拒**，
    且拒绝里两个版本都在；而**同一次**处置重发是幂等的。两条并排，才是
    「重放幂等、过期拒写」这两个半边的读数。
    """
    control = deployed.control()
    created = _ok(control(_open_call()))["item"]
    assert created["expected_version"] == 0, (
        "创建时的那一格不是 0 —— 那么「基于哪一版」在下一次处置时就是错的。"
    )

    first = _ok(
        control(
            (
                "resolve_action_item",
                {
                    "action_id": "act-new",
                    "expected_version": 1,
                    "state": "claimed",
                    "owner": CONTROL,
                },
            )
        )
    )
    assert first["revision_written"] == 2 and first["item"]["state"] == "claimed"

    text = _refused(
        control(
            (
                "resolve_action_item",
                {
                    "action_id": "act-new",
                    "expected_version": 1,
                    "state": "done",
                    "resolution": "办理完毕。",
                },
            )
        )
    )
    assert "1" in text and "2" in text, (
        "拒绝没有同时点出手上的基准与盘上那一版 —— 那是这句话唯一的用处。"
    )
    assert deployed.items.get("act-new").revision == 2, (
        "过期的那一次写出了东西 —— 而它拿的是一本已经被别人改过的基准。"
    )
    # 第二半：**再发一次同样的处置不会多出第三版**。这一条刻意不钉死「它是空写还是
    # 一句拒绝」—— 今天到不了幂等那一格（``tests/test_views_application.py::
    # test_resending_the_same_disposition_never_writes_a_third_version`` 逐字记着这个
    # 缺口：``resolve_action_item`` 先把当前那一版读出来交给 ``ActionItem.resolve``，
    # 而它要求 ``expected_version`` 正好等于当前版）。钉死今天的样子会把一个缺口冻成
    # 判据；钉死「今天幂等」会写一条不成立的绿。这里判两造都必须成立的那一件。
    replay = control(
        (
            "resolve_action_item",
            {
                "action_id": "act-new",
                "expected_version": 1,
                "state": "claimed",
                "owner": CONTROL,
            },
        )
    )
    if not replay.calls[0].is_error:
        assert _ok(replay)["revision_written"] is None, (
            "重发的这一次产生了第三版 —— 幂等的那一半必须是**空写**。"
        )
    assert deployed.items.get("act-new").revision == 2
    assert (
        _ok(control(("list_action_items", {})))["count"] == 1
    ), "处置不该在池子里留下第二条"

"""§B23 那八条工具走**真实 MCP 会话**时的行为。

## 这一层测的是接线之后才存在的那几件事

判据本身各有住处：``tests/test_feedback_domain.py``（域：三张表、形状守卫、
``POLICY_PATCHABLE_KINDS``）、``tests/test_feedback_store_contract.py`` 与
``tests/test_sqlite_feedback.py``（端口与 sqlite：幂等、只增、跨工作区）、
``tests/test_application_feedback.py``（编排：来源、四档批准读数）、
``tests/test_feedback_preview.py``（预览那份读数逐格）。本文件不重测那些，
它测的是把四段（目录、处理器表、存储、令牌）接起来之后**才会出现**的东西：

* **人从哪里来**：``principal`` / ``proposed_by`` / ``workspace`` / ``at``
  一律不是入参 —— 判据是 ``tools/list`` 上那份 ``inputSchema`` 与处理器签名
  **逐格相等**，所以将来没人能悄悄加回一个「让调用方替自己签名」的入口。
* **角色与工作区**：控制者看得见这八条，研究身份一条都看不见，而且**越权与不存在
  在协议上同形**（两者都是 ``Unknown tool`` —— 那是 ``guard`` 的刻意选择，
  这一条判的是这一族没有把它改掉）。另一件：一枚铸给别的 Scene 的令牌写不进这一条。
* **承重点 1（scope 的宽度）在工具面上的落点**：不给任何引用是一次拒绝；给了
  引用时取的是**最窄**的那一档，而不是工作区。
* **承重点 4（幂等）在工具面上的落点**：同一把键重发拿回同一行；换一个人用同一把
  键拿到**另一条**（键的第二格是 principal）；同一把键配不同的话是一次拒绝。
* **承重点 3（批准不在这一族里）**：这八条里没有一条是批准；批准走既有的
  ``application/review.py::approve_policy_change``，而这一族只把它读出来 ——
  读数在批准之前是 ``waiting``、之后是 ``approved``，同一个工具同一份回执。
* **读是返回值、写是拒绝**：``get_feedback`` 读不到是 ``found: false``（一个答案），
  ``preview_feedback_impact`` 读不到是一次拒绝（没有可预览的东西）。
* **没接线与空**：``review=None`` 时那一格是 ``unreadable``，不是 ``waiting``。

## 接线走的是组合根那条路

这一族在 ``build_server`` 上有槽（``feedback=``），所以这里**不做第二份组装**：
会话走 ``tests/mcp_harness.py::drive_with_settings``，那一个函数就是 ``build_server``
的薄包装，``handlers`` 换成一个实例自己的 ``feedback_handlers`` 表。

这一条不是风格问题，而是一份自拼的服务实例会把**组合根漏接了这一族**藏起来 ——
症状是那八条在生产部署上落回 ``unbacked_message``，而这个文件全绿。所以这里唯一的
例外是一次**读 schema**（下面那条 ``inputSchema`` 判据）：它要的是目录里那八条的
原始声明，与令牌无关，于是直接 ``build_server(...).list_tools()``，不经会话。

会话本身走真的 ``MCPServer``、真的 ASGI 应用、真的 ``BearerAuthBackend`` 与
``AuthContextMiddleware``，客户端用 SDK 自己的 ``ClientSession``。**没有 socket**，
所以它不会去抢 8787。
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anyio
import pytest

from marketops.adapters.mcp_server.feedback import feedback_handlers
from marketops.adapters.mcp_server.server import build_server
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.feedback import SqliteFeedbackStore
from marketops.adapters.sqlite.review import SqliteReviewCaseStore
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.application.feedback import MAX_FEEDBACK
from marketops.application.review import approve_policy_change
from marketops.config import Settings
from marketops.domain.enums import BudgetLine
from marketops.domain.maintenance import RefreshPlan
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.review import (
    INTAKE,
    HumanResolution,
    ResolutionOutcome,
    ReviewCase,
    ReviewReason,
    ReviewState,
    case_id_for,
)
from marketops.domain.scene import (
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.security.catalog import TOOL_CATALOG, Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

UTC = timezone.utc
T0 = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)

#: §B23 的八条。写死在这里而不是从目录里按 phase 筛 —— 与
#: ``tests/test_mcp_review.py`` 的 ``REVIEW_TOOLS`` 同一条理由：这一份要钉的正是
#: **这八条**（多一条或少一条都要响）。
FEEDBACK_TOOLS: tuple[str, ...] = (
    "submit_feedback",
    "get_feedback",
    "list_feedback",
    "preview_feedback_impact",
    "propose_experience_candidate",
    "list_experience_candidates",
    "propose_policy_patch",
    "list_policy_patches",
)

#: 任何一条**都不许**声明的入参。它们是「谁说的 / 在哪儿说的 / 什么时候说的」，
#: 而这三样一律从令牌与服务端时钟来 —— 一个能由载荷填写的「谁说的」，此后会被当成
#: 一个人真的说过的话读回去，并且两处各自都正常。
FORBIDDEN_PARAMETERS: frozenset[str] = frozenset(
    {"principal", "proposed_by", "workspace", "scene_id", "at", "roles", "by"}
)

#: 批准那两条**不在**这一族里。承重点 3 的落点是「这一族里没有第二条批准路径」，
#: 而这句话在工具面上的形状就是名字集合不相交。
APPROVAL_TOOLS: frozenset[str] = frozenset(
    {"approve_policy_change", "resolve_review_case", "claim_review_case"}
)


# ================================================================== 夹具
class Deployment:
    """一个部署：一份反馈存储、一份证据存储、一份收件箱存储，以及按主体开的会话。

    三份存储指向**同一个** ``settings.database_path``（组合根就是这么接的）：
    分家的形态是「这条候选立在那份材料上、而读它的时候材料不在同一个库里」，
    两边各自都正常。
    """

    def __init__(
        self, *, settings: Settings, tmp_path: Path, review: bool = True
    ) -> None:
        self.settings = settings
        self.tmp_path = tmp_path
        path = settings.database_path
        self.feedback = SqliteFeedbackStore(path)
        self.evidence = SqliteEvidenceStore(path)
        self.review: SqliteReviewCaseStore | None = (
            SqliteReviewCaseStore(path) if review else None
        )
        # 场景存储：**应用**那一档要问的就是它（那一版在不在盘上、是不是当前的
        # 生效版）。缺省时 ``application`` 只报 unreadable —— 这里接上，是为了让
        # 「批准 → 应用 → 生效 → 可撤回」这条链在工具面上真的能走通。
        self.scenes = SqliteSceneStore(path)
        self._sessions: dict[Any, Any] = {}

    # ------------------------------------------------------------ 接线
    def handlers(self) -> dict[str, Any]:
        review = self.review
        return feedback_handlers(
            settings=self.settings,
            feedback=lambda: self.feedback,
            evidence=lambda: self.evidence,
            review=None if review is None else (lambda: review),
            scenes=lambda: self.scenes,
        )

    def server(self) -> Any:
        """这个部署的 ``build_server`` 实例，**不带令牌** —— 只用来读 schema。

        ``tokens=None`` + 本部署没配 ``MARKETOPS_MCP_TOKENS`` ⇒ 这一份没有验证器，
        而那是 ``build_server`` 的合法结局之一（「起得来但谁都不许」）。这里用它只有
        一个用途：``tools/list`` 的 ``inputSchema`` 是注册那一刻从签名算出来的，
        与令牌无关。走**真会话**的那条路需要令牌，见下面 ``session``。
        """
        return build_server(
            settings=self.settings, tokens=None, feedback=self.handlers()
        )

    def session(
        self,
        principal: str,
        roles: list[Role],
        *,
        workspaces: tuple[str, ...] | None = None,
    ):
        """一个主体的会话。返回一个 ``run(*calls, want_tool_list=)`` 可调用对象。

        环**一次一个文件**（``TokenRing`` 拒绝同一个主体出现两次），所以每个主体
        各铸一枚 —— 这与「两个人各自拿一枚令牌连同一个服务端」同形。

        服务实例由 ``drive_with_settings`` 每次调用现造一个（它就是 ``build_server``
        的薄包装）：本部署的三个存储都指着**同一个** ``settings.database_path``，
        所以一趟会话与前一趟看见的是同一份盘上状态，而实例本身不带任何跨调用的状态。
        """
        token, digest = mint(principal, roles)
        entry = (
            ring_entry(digest, principal, roles)
            if workspaces is None
            else ring_entry(digest, principal, roles, workspaces=workspaces)
        )
        path = self.tmp_path / f"tokens-{principal}.json"
        path.write_text(_payload(entry), encoding="utf-8")
        ring = TokenRing.from_file(path)
        handlers = self.handlers()

        def run(*calls: tuple[str, dict[str, Any]], want_tool_list: bool = False):
            outcome = drive_with_settings(
                settings=self.settings,
                tokens=ring,
                token=token,
                feedback=handlers,
                calls=calls,
                want_tool_list=want_tool_list,
            )
            assert not outcome.handshake_error, outcome.handshake_error
            return outcome

        self._sessions[(principal, tuple(sorted(r.value for r in roles)))] = run
        return run

    def control(self):
        return self.session("claude-control", [Role.CONTROLLER])

    def researcher(self):
        return self.session("claude-research", [Role.RESEARCHER])


def _payload(entry: dict[str, Any]) -> str:
    """一条环条目的环文件正文。格式版本号只出现一次（``ring_payload`` 里）。"""
    import json

    return json.dumps(ring_payload(entry))


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


# ================================================================== 一、目录与接线
def test_the_handlers_are_exactly_the_eight_control_tools(deployed: Deployment) -> None:
    """工厂交回的名字与目录里的那八条**逐格相等**。

    多一条：目录那一行没承诺过它，而它会出现在 ``tools/list`` 上 —— 一次对外
    承诺是改目录，不是改这里。少一条：那一条落回 stub，调用方读到的是一句
    「本构建尚未接线」，而它看起来像部署没跟上。
    """
    handlers = deployed.handlers()
    assert set(handlers) == set(FEEDBACK_TOOLS)
    for name in FEEDBACK_TOOLS:
        spec = TOOL_CATALOG[name]
        assert spec.roles == frozenset({Role.CONTROLLER}), f"{name} 不是 _CONTROL"
        assert spec.phase == "K", f"{name} 的 phase 不是 K"


def test_none_of_the_eight_is_an_approval(deployed: Deployment) -> None:
    """**承重点 3 在工具面上的落点。**

    这一族里有两条提案（候选与补丁），却**没有一条批准** —— 批准是
    ``approve_policy_change``（角色 ``_AUTHORIZE``，而且要另一个人），它由
    收件箱那一族提供。一个「顺手」加进来的 ``approve_policy_patch`` 会让这一族
    变成自己批自己的那一侧，而它的表现在报告上是一次成功的调用。
    """
    names = set(deployed.handlers())
    assert not (names & APPROVAL_TOOLS), (
        f"这一族里出现了批准那条路上的名字：{sorted(names & APPROVAL_TOOLS)}"
    )
    assert "approve_policy_change" in TOOL_CATALOG
    assert TOOL_CATALOG["approve_policy_change"].roles == frozenset({Role.AUTHORIZER})


def test_every_tool_declares_exactly_the_parameters_its_handler_takes(
    deployed: Deployment,
) -> None:
    """**``inputSchema`` 那条回归判据。**

    ``server.add_tool`` 是**从签名推**工具 schema 的，所以一个包住处理器的装饰器
    （``def wrap(*args, **kwargs)``）会把八条工具在 ``tools/list`` 上变成「不吃任何
    参数」—— 而这一条**不会报错**：调用方以为这些工具没有入参，然后每一次调用都
    拿到「缺参数」。所以这里判的不是「有几个参数」，而是**逐格相等**：工具声明的
    属性集合 == ``inspect.signature`` 的参数集合。

    同一批的第二个断言是承重的那一半：**谁都不许声明 ``principal`` / ``at`` /
    ``workspace``**。它们从令牌与服务端时钟来（见模块 docstring）。
    """
    handlers = deployed.handlers()
    tools = anyio.run(deployed.server().list_tools)
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


# ================================================================== 二、角色与工作区
def test_a_controller_sees_the_eight_and_a_researcher_sees_none_of_them(
    deployed: Deployment,
) -> None:
    """目录是目录，可见性是可见性：八条都在 ``_CONTROL`` 上，所以研究者一条都看不见。"""
    control = deployed.control()
    outcome = control(want_tool_list=True)
    assert set(FEEDBACK_TOOLS) <= set(outcome.tool_names)

    research = deployed.researcher()
    outcome = research(want_tool_list=True)
    assert not (set(FEEDBACK_TOOLS) & set(outcome.tool_names)), (
        "研究身份看得见这一族的某几条 —— 而这一族的每一条都改得动全局那本账"
    )


def test_a_researcher_calling_one_gets_the_same_answer_as_a_missing_tool(
    deployed: Deployment,
) -> None:
    """**越权与「不存在」在协议上同形。**

    这是 ``guard`` 的刻意选择（``unknown_tool_result`` 与 SDK 逐字相同）：
    一个能被枚举出来的「你无权调用 X」会告诉调用方 X 存在，而目录本身是情报。
    这一条判的是这一族没有把那个选择改掉 —— 两句话都是 ``Unknown tool: <名字>``。
    """
    research = deployed.researcher()
    outcome = research(
        ("list_feedback", {}),
        ("no_such_tool_at_all", {}),
    )
    denied = _refused(outcome, 0)
    missing = _refused(outcome, 1)
    assert denied == "Unknown tool: list_feedback"
    assert missing.startswith("Unknown tool:")


def test_a_token_for_another_scene_cannot_write_here(deployed: Deployment) -> None:
    """工作区那一维在**角色之后、背书之前**判 —— 它判的是范围，不是角色。

    一枚铸给别的 Scene 的控制者令牌，角色上完全够格，却写不进这一条：而那句话
    要说得出来「你被授权的是别的范围」，否则读的人会去查角色配置。
    """
    outsider = deployed.session(
        "claude-elsewhere", [Role.CONTROLLER], workspaces=("other-scene",)
    )
    outcome = outsider(
        ("submit_feedback", {"kind": "run_instruction", "idempotency_key": "k-out", "run_ref": "r-1"})
    )
    text = _refused(outcome)
    assert deployed.settings.profile in text or "工作区" in text, text
    assert "Unknown tool" not in text, "范围不匹配被答成了「不存在」"


# ================================================================== 三、写与读
def _submit(run, **overrides: Any):
    """一条形状正常的提交。缺省给了 ``run_ref`` —— 不给引用是一次拒绝（见下一条）。"""
    arguments: dict[str, Any] = {
        "kind": "workstream_policy",
        "idempotency_key": "k-1",
        "literal_feedback": "这一批的来源计划配得太宽了",
        "run_ref": "run-2026-09-29-b",
        "source_session": "sess-1",
    }
    arguments.update(overrides)
    return run(("submit_feedback", arguments))


def test_submit_then_get_returns_the_words_verbatim(deployed: Deployment) -> None:
    """原话**逐字**回。折成摘要的版本是这一侧的读法，而后来的人要判的是当初说了什么。"""
    control = deployed.control()
    words = "这一批的来源计划配得太宽了，而且用的是上一季那份名单"
    admitted = _ok(_submit(control, literal_feedback=words))
    assert admitted["admitted"] is True and admitted["replayed"] is False
    assert admitted["principal"] == "claude-control", "当事人不是令牌上那个"
    assert admitted["workspace"] == deployed.settings.profile
    assert admitted["ring"] == "fast_loop"

    found = _ok(control(("get_feedback", {"feedback_id": admitted["feedback_id"]})))
    assert found["found"] is True
    assert found["feedback"]["literal_feedback"] == words
    assert found["feedback"]["principal"] == "claude-control"


def test_reading_a_feedback_that_is_not_there_is_an_answer_not_a_refusal(
    deployed: Deployment,
) -> None:
    """**读是返回值。** ``found: false`` 是一个答案，不是一次失败。

    把它报成错误，会让「这个 id 没有反馈」与「这一次调用坏了」在客户端上同形，
    而两者的下一步完全不同（换 id / 查服务）。
    """
    control = deployed.control()
    payload = _ok(control(("get_feedback", {"feedback_id": "fb-0000000000000000"})))
    assert payload["found"] is False
    assert payload["feedback"] is None
    assert "没" in payload["note"]


def test_the_same_key_twice_is_a_replay_not_a_second_row(deployed: Deployment) -> None:
    """**承重点 4 在工具面上的落点。** 同一个人重发同一句话拿回同一行。"""
    control = deployed.control()
    first = _ok(_submit(control))
    second = _ok(_submit(control))
    assert second["feedback_id"] == first["feedback_id"]
    assert second["admitted"] is False and second["replayed"] is True
    assert second["created_at"] == first["created_at"], "重发把时刻改写了"

    page = _ok(control(("list_feedback", {})))
    assert page["matched"] == 1, "同一条反馈在盘上被记成了两条"


def test_the_same_key_with_different_words_is_refused(deployed: Deployment) -> None:
    """同一把键配不同的话是一次**显性失败**，不是静默保留先写的那条。"""
    control = deployed.control()
    _submit(control, literal_feedback="这一批配得太宽了")
    text = _refused(_submit(control, literal_feedback="这一批配得太窄了"))
    assert "太宽" in text and "太窄" in text, "判词没有把两句话都写出来"
    assert "换一把幂等键" in text, "判词没有给出下一步"


def test_the_same_key_under_another_person_is_another_feedback(
    deployed: Deployment,
) -> None:
    """幂等键的**第二格是 principal** —— 两个人各自说了一句同样的话是两件事。

    少了这一格，第二个人会被静默地折进第一个人的那一条里：他的意见在盘上不存在，
    而两次调用各自都成功。
    """
    _ok(_submit(deployed.control()))
    other = deployed.session("claude-control-2", [Role.CONTROLLER])
    second = _ok(_submit(other))
    assert second["admitted"] is True
    assert second["principal"] == "claude-control-2"
    page = _ok(other(("list_feedback", {})))
    assert page["matched"] == 2


# ================================================================== 四、scope 的宽度
def test_submitting_without_any_scope_is_refused(deployed: Deployment) -> None:
    """**承重点 1 在工具面上的第一半：缺省是一次拒绝，不是工作区。**

    一个在缺省时填工作区的实现，会让「有人随口说了一句」在盘上变成一条工作区级的
    建议 —— 而它此后会被当成一条有人主张过的全局规则读回去。两份报告各自都正常。
    """
    outcome = deployed.control()(
        (
            "submit_feedback",
            {
                "kind": "workstream_policy",
                "idempotency_key": "k-narrow",
                "literal_feedback": "这一批配得太宽了",
            },
        )
    )
    text = _refused(outcome)
    assert "作用域" in text or "点名" in text, text
    assert deployed.settings.profile not in text, (
        "拒绝里出现了工作区名 —— 读的人会以为那是一个被采纳的缺省"
    )
    #: 而且盘上一个字都没落下。
    page = _ok(deployed.control()(("list_feedback", {})))
    assert page["matched"] == 0


def test_the_widest_of_the_named_refs_does_not_win(deployed: Deployment) -> None:
    """**承重点 1 在工具面上的第二半：同时点名时取最窄的那一档。**

    对象比 Run 窄、Run 比工作流窄。一个「取最宽的那个」的实现会让一条讲某一轮 Run 的
    话变成一条讲整个工作流的建议 —— 而它在盘上只是一格 ``scope_kind`` 不同，两份报告
    各自都正常。
    """
    control = deployed.control()
    pair = _ok(_submit(control, run_ref="run-1", workstream_ref="ws-1"))
    assert pair["proposed_scope"]["kind"] == "run", (
        "同时点了 Run 与工作流时取了宽的那一个"
    )

    three_way = _ok(
        _submit(
            control,
            idempotency_key="k-2",
            target_ref="obj-1",
            run_ref="run-1",
            workstream_ref="ws-1",
        )
    )
    assert three_way["proposed_scope"]["kind"] == "object", (
        "同时点了对象、Run 与工作流时取了宽的那一个"
    )


# ================================================================== 五、预览
def test_previewing_a_feedback_that_is_not_on_disk_is_refused(
    deployed: Deployment,
) -> None:
    """**写必须有对象。** 预览读的是某一条已经写下的反馈，它不存在时没有可预览的东西。

    一份「什么都不改」的读数与「你写错了 id」同形，而前者看起来像一条结论。
    """
    outcome = deployed.control()(
        ("preview_feedback_impact", {"feedback_id": "fb-0000000000000000"})
    )
    text = _refused(outcome)
    assert "没有 feedback_id" in text
    assert "list_feedback" in text, "判词没有给出下一步"


def test_the_default_applied_scope_is_the_feedback_s_own(deployed: Deployment) -> None:
    """``applied_at`` 的缺省是**反馈自己说的那一格**，不是工作区。

    一个把缺省填成工作区的实现，在这里读起来只是「影响面更大」—— 而它此后会被当成
    一条有人主张过的全局规则读回去。所以两条读数必须**不同**，而且差别正好是那一格。
    """
    control = deployed.control()
    submitted = _ok(_submit(control, run_ref="run-1"))
    quiet = _ok(
        control(("preview_feedback_impact", {"feedback_id": submitted["feedback_id"]}))
    )
    assert quiet["applied_at"] == quiet["proposed_scope"], (
        "不给 applied scope 时，预览用的是别的作用域"
    )
    assert quiet["widened"] is False and quiet["widening"] == []

    asked = _ok(
        control(
            (
                "preview_feedback_impact",
                {
                    "feedback_id": submitted["feedback_id"],
                    "applied_scope_kind": "workstream",
                    "applied_scope_ref": "ws-1",
                },
            )
        )
    )
    assert asked["applied_at"]["kind"] == "workstream"
    assert asked["widening"] == ["workstream"], "扩大跨过了哪几档没有报出来"


def test_previewing_writes_nothing(deployed: Deployment) -> None:
    """**零写入。** 预览前后盘上的行数逐格不变 —— 一份会写行的预览是一种提案，
    而它的形状与一份读数完全一样。"""
    control = deployed.control()
    submitted = _ok(_submit(control))
    before = _ok(control(("list_feedback", {})))["matched"]
    _ok(control(("preview_feedback_impact", {"feedback_id": submitted["feedback_id"]})))
    after = _ok(control(("list_feedback", {})))["matched"]
    assert after == before == 1


# ================================================================== 六、候选
def test_a_candidate_cannot_generalise_to_the_whole_workspace(
    deployed: Deployment,
) -> None:
    """一条想推广到整个工作区的候选，是慢环里一步做不到的事 —— 它要一个**人**。

    这条判据在领域里（``ExperienceCandidate`` 的校验器），而这一层要把它**原样**
    翻成一条可读回执，不能摊成一句「参数不对」。
    """
    outcome = deployed.control()(
        (
            "propose_experience_candidate",
            {
                "observation": "同一族的来源计划连续三次配得过宽",
                "generalises_kind": "workspace",
                "generalises_ref": deployed.settings.profile,
                "risk": "low",
            },
        )
    )
    text = _refused(outcome)
    assert "人" in text, text


def test_a_candidate_keeps_its_evidence_readings_apart(deployed: Deployment) -> None:
    """``evidence_total`` 与 ``evidence_read`` 是两个数 —— 0 与 3 不是同一件事。

    一条立在三份取不到的材料上的候选，与一条真的没证据的候选，在一个数上同形；
    而处置相反（去找材料 / 再攒几条）。
    """
    control = deployed.control()
    submitted = _ok(_submit(control))
    proposed = _ok(
        control(
            (
                "propose_experience_candidate",
                {
                    "observation": "同一族的来源计划连续三次配得过宽",
                    "generalises_kind": "segment",
                    "generalises_ref": "segment-1",
                    "risk": "low",
                    "source_feedback_ids": [submitted["feedback_id"]],
                    "evidence_refs": ["ev-not-on-disk"],
                },
            )
        )
    )
    assert proposed["admitted"] is True
    assert proposed["state"] == "proposed", "候选上出现了批准那一档"

    listed = _ok(control(("list_experience_candidates", {})))
    assert listed["count"] == 1
    row = listed["candidates"][0]
    assert row["evidence_total"] == 1
    assert row["evidence_read"] == 0, "一条取不到的引用被读成了零条"


# ================================================================== 七、补丁与批准
def test_a_run_instruction_cannot_carry_a_patch(deployed: Deployment) -> None:
    """**承重点 2 的逃逸通道。** 一条指令一条版本都不产生，所以它提不起补丁 ——
    而拒绝里必须说出**换哪条路**（否则读的人会去翻 ``POLICY_PATCHABLE_KINDS``）。"""
    control = deployed.control()
    submitted = _ok(
        _submit(
            control,
            kind="run_instruction",
            idempotency_key="k-instr",
            run_ref="run-1",
        )
    )
    outcome = control(
        (
            "propose_policy_patch",
            {
                "source_ref": f"feedback:{submitted['feedback_id']}",
                "changes": ["source_plan"],
                "target_version": 4,
                "monitoring": "下一轮 Run 的来源计划条数",
                "rollback": "回退到第 3 版",
                "risk": "low",
                "rationale": "三次同族观察",
            },
        )
    )
    text = _refused(outcome)
    assert "提不起" in text
    assert "Run" in text, "判词没有说清这一类的下一步在哪"


def test_a_patch_without_a_case_is_not_attached_not_waiting(
    deployed: Deployment,
) -> None:
    """没归案的补丁是 ``not_attached`` —— 它今天连被批准的资格都没有。

    把这一档折进 ``waiting``，会让一份**还没有案**的提案看起来像一件正等着人的待办。
    """
    control = deployed.control()
    submitted = _ok(_submit(control))
    reading = _ok(
        control(
            (
                "propose_policy_patch",
                {
                    "source_ref": f"feedback:{submitted['feedback_id']}",
                    "changes": ["source_plan"],
                    "target_version": 4,
                    "monitoring": "下一轮 Run 的来源计划条数",
                    "rollback": "回退到第 3 版",
                    "risk": "low",
                    "rationale": "三次同族观察",
                },
            )
        )
    )
    assert reading["standing"] == "not_attached"
    assert reading["approved_by"] is None
    assert reading["declared_monitoring"] == "下一轮 Run 的来源计划条数"
    assert reading["monitoring"] is None, "还没生效的那一版报出了生效中的监控路径"


def test_waiting_then_approved_through_the_existing_approval_path(
    deployed: Deployment,
) -> None:
    """**承重点 3 的正面落点。** 批准走既有的 ``approve_policy_change``，这一族只读它。

    同一个工具、同一份回执，批准之前是 ``waiting``、之后是 ``approved`` —— 而
    ``approved_by`` 是**另一个人**。这一条同时是 ``PatchStanding.APPROVED`` 的
    回归判据：它曾经是一个不可达的成员（那时一份批过的补丁读成 ``waiting``）。
    """
    control = deployed.control()
    submitted = _ok(_submit(control))
    assert deployed.review is not None
    case = _case(deployed.settings.profile)
    deployed.review.open_case(case)

    reading = _ok(
        control(
            (
                "propose_policy_patch",
                {
                    "source_ref": f"feedback:{submitted['feedback_id']}",
                    "changes": ["source_plan"],
                    "target_version": 4,
                    "monitoring": "下一轮 Run 的来源计划条数",
                    "rollback": "回退到第 3 版",
                    "risk": "low",
                    "rationale": "三次同族观察",
                    "review_case_id": case.case_id,
                },
            )
        )
    )
    assert reading["standing"] == "waiting"
    assert "approve_policy_change" in reading["standing_reason"]
    assert "另一个人" in reading["standing_reason"]

    _approve(deployed.review, case, by="claude-authorizer", version=4)

    listed = _ok(control(("list_policy_patches", {})))
    assert listed["count"] == 1
    after = listed["patches"][0]
    assert after["standing"] == "approved", (
        "另一个人批过之后仍然读成还没批 —— 四档里有一档到不了"
    )
    assert after["approved_by"] == "claude-authorizer"
    # **批准不产生任何一版。** 第 4 版从没被写下来过，所以应用那一档是
    # ``not_produced``，而监控/撤回路径都还是 null：它们随**已生效**的那一版走。
    # 这一条在只报 standing 的版本里是绿的（那时它报出 4 与那句监控路径），
    # 而盘上连第 4 版都不存在 —— 一条从未生效的提案被读成了生效。
    assert after["application"] == "not_produced", (
        "批准之后那一版并不在盘上 —— 而应用那一档必须说得出这件事"
    )
    assert after["monitoring"] is None, "还没产版的提案报出了生效中的监控路径"
    assert after["rollback_version"] is None
    assert after["declared_monitoring"] == "下一轮 Run 的来源计划条数"

    # 走**既有的**那条路把那一版真的写下来（``add_scene_version`` 的落点），
    # 它写的是声明版；生效还要再一步（``control_scene(activate)``，不在 §B23 八条里）。
    scene_id = deployed.settings.profile
    deployed.scenes.put_record(_scene_record(scene_id, 4), expected_revision=None)
    produced = _ok(control(("list_policy_patches", {})))["patches"][0]
    assert produced["application"] == "not_produced", (
        "记录写了、版本还没写 —— 这一档要说得出「那一版不在盘上」"
    )

    # 第 3 版也要在盘上：「撤得回去」要求回头那一版真的取得到。
    deployed.scenes.put_version(_scene_version(scene_id, 3))
    deployed.scenes.put_version(_scene_version(scene_id, 4))
    produced = _ok(control(("list_policy_patches", {})))["patches"][0]
    assert produced["application"] == "produced", "那一版在盘上而当前生效的不是它"
    assert produced["monitoring"] is None, "还没生效的那一版报出了生效中的监控路径"

    record = deployed.scenes.get_record(scene_id)
    assert record is not None
    deployed.scenes.put_record(
        record.activate(version=4, at=T0, by="claude-authorizer"),
        expected_revision=record.revision,
    )

    live = _ok(control(("list_policy_patches", {})))["patches"][0]
    assert live["application"] == "effective", "第 4 版已生效，而这一档没读出来"
    assert live["monitoring"] == "下一轮 Run 的来源计划条数", (
        "已生效那一版带着的监控路径应当在这一档出现"
    )
    # 「如何撤回」是**机器可读的**：它读那一版自己的 supersedes，不是那句自由文本。
    assert live["rollback_version"] == 3
    assert live["rollback_possible"] is True
    assert live["declared_rollback"] == "回退到第 3 版"


def test_an_approval_by_the_proposer_reads_as_waiting(deployed: Deployment) -> None:
    """**读法必须与写法在不同的地方各拒一次。**

    ``approve_policy_change`` 已经拒了「同一个人既提议又批准」，所以这一条**在真实的
    写入路径上构造不出来** —— 它由存储层直接写下去，而那种行的来源是真实的：一次
    迁移搬过来的旧库、另一个部署写下的行、或者一条绕过了应用层的脚本。读的那一侧
    要防的正是**绕过写入的旧行**：把「那件案上有一条批准」读成「这一份生效了」，
    会让一份提案在盘上提前变成生效的规则，而两份报告各自都正常（见
    ``tests/test_application_feedback.py`` 里那条同名判据，它用的是直接 INSERT）。
    """
    control = deployed.control()
    submitted = _ok(_submit(control))
    assert deployed.review is not None
    case = _case(deployed.settings.profile)
    deployed.review.open_case(case)
    reading = _ok(
        control(
            (
                "propose_policy_patch",
                {
                    "source_ref": f"feedback:{submitted['feedback_id']}",
                    "changes": ["source_plan"],
                    "target_version": 4,
                    "monitoring": "下一轮 Run 的来源计划条数",
                    "rollback": "回退到第 3 版",
                    "risk": "low",
                    "rationale": "三次同族观察",
                    "review_case_id": case.case_id,
                },
            )
        )
    )
    assert reading["standing"] == "waiting"

    #: 提议的人与批准的人是**同一个** —— ``approve_policy_change`` 会拒掉这一步，
    #: 所以这里绕过它，模仿一条已经躺在盘上的旧行。
    proposed = deployed.review.record_resolution(
        HumanResolution(
            resolution_id="res-1",
            case_id=case.case_id,
            workspace=case.workspace,
            outcome=ResolutionOutcome.PROPOSE_POLICY_CHANGE,
            answer="挂起第 4 版",
            by="claude-control",
            roles=("controller",),
            expected_version=case.version,
            at=T0,
            proposed_version=4,
        ),
        expected_version=case.version,
    )
    deployed.review.record_resolution(
        HumanResolution(
            resolution_id="res-2",
            case_id=proposed.case_id,
            workspace=proposed.workspace,
            outcome=ResolutionOutcome.APPROVE_POLICY_CHANGE,
            answer="我自己批了",
            by="claude-control",
            roles=("controller",),
            expected_version=proposed.version,
            at=T0 + timedelta(hours=1),
        ),
        expected_version=proposed.version,
    )

    listed = _ok(control(("list_policy_patches", {})))
    after = listed["patches"][0]
    assert after["standing"] == "waiting", (
        "提议的人自己批被读成了一次批准 —— 那条边界在读数上不存在"
    )
    assert after["approved_by"] is None


def test_a_deployment_without_a_review_store_says_unreadable_not_waiting(
    settings: Settings, tmp_path: Path
) -> None:
    """**``unreadable`` 与 ``waiting`` 分开给。**

    ``review=None`` 是允许的，而那不是降级：把「没查」折进「还没批」，会让一个只是
    没接上存储的部署读起来像「这些补丁都在等着人」，而它此后会被当成一条真实的待办
    队列去催。
    """
    blind = Deployment(settings=settings, tmp_path=tmp_path, review=False)
    assert blind.review is None
    control = blind.control()
    submitted = _ok(_submit(control))
    #: 补丁**归到一件案上**，而那一件不在这一层的视野里 —— 归不到案的补丁是
    #: ``not_attached``（那一档不需要存储就能答），归到了才轮得到 ``unreadable``。
    reading = _ok(
        control(
            (
                "propose_policy_patch",
                {
                    "source_ref": f"feedback:{submitted['feedback_id']}",
                    "changes": ["source_plan"],
                    "target_version": 4,
                    "monitoring": "下一轮 Run 的来源计划条数",
                    "rollback": "回退到第 3 版",
                    "risk": "low",
                    "rationale": "三次同族观察",
                    "review_case_id": "rc-not-in-this-deployment",
                },
            )
        )
    )
    assert reading["standing"] == "unreadable", (
        "没拿到复核存储时答成了别的档 —— 而「没查」与「还没批」的处置完全不同"
    )
    assert "ReviewCaseStore" in reading["standing_reason"]


# ================================================================== 八、清单
def test_the_page_cap_is_held_by_this_side(deployed: Deployment) -> None:
    """``limit`` 的上界由这一侧持有：一个更大的数**被压到上界**，而不是照办。"""
    page = _ok(deployed.control()(("list_feedback", {"limit": 10**9})))
    assert page["limit"] == MAX_FEEDBACK
    assert page["cap"] == MAX_FEEDBACK


# ================================================================== 小工具
def _case(workspace: str) -> ReviewCase:
    """一件形状正确的待办。``case_id`` 由内容算出来 —— 手写一个会被模型拒掉。"""
    return ReviewCase(
        case_id=case_id_for(
            workspace=workspace,
            scene_id=workspace,
            reason=ReviewReason.IDENTITY_CONFLICT,
            subject_ref="subject-1",
        ),
        workspace=workspace,
        scene_id=workspace,
        reason=ReviewReason.IDENTITY_CONFLICT,
        subject_ref="subject-1",
        affected_refs=("subject-1",),
        options=INTAKE[ReviewReason.IDENTITY_CONFLICT].options,
        state=ReviewState.OPEN,
        version=1,
        opened_at=T0,
        updated_at=T0,
    )


def _scene_version(scene_id: str, version: int) -> SceneVersion:
    """某一版 Scene 的**全部**绑定。形状抄 ``tests/test_scene_store_contract.py``。

    ``supersedes`` 必须是 ``version - 1``（校验器钉住了），而「如何撤回」读的就是
    它 —— 这是那一版**自己**说的，不是提案里那句自由文本。
    """
    return SceneVersion(
        scene_id=scene_id,
        workspace=scene_id,
        version=version,
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
        profile_id=scene_id,
        profile_version="2026-09-01",
        created_at=T0,
        supersedes=None if version == 1 else version - 1,
    )


def _scene_record(scene_id: str, latest_version: int) -> SceneRecord:
    return SceneRecord(
        scene_id=scene_id,
        workspace=scene_id,
        state=SceneState.DRAFT,
        latest_version=latest_version,
        created_at=T0,
        updated_at=T0,
    )


def _approve(
    review: SqliteReviewCaseStore, case: ReviewCase, *, by: str, version: int
) -> ReviewCase:
    """走**既有的**那条路：先提议挂起某一版，再由另一个人批准。

    这一份**不写**任何批准逻辑 —— 提议落在 ``record_resolution`` 上（与
    ``approve_policy_change`` 落到的是同一个函数），而批准调的就是它本身。
    """
    proposed = review.record_resolution(
        HumanResolution(
            resolution_id="res-1",
            case_id=case.case_id,
            workspace=case.workspace,
            outcome=ResolutionOutcome.PROPOSE_POLICY_CHANGE,
            answer="挂起第 4 版",
            by="claude-control",
            roles=("controller",),
            expected_version=case.version,
            at=T0,
            proposed_version=version,
        ),
        expected_version=case.version,
    )
    return approve_policy_change(
        store=review,
        resolution=HumanResolution(
            resolution_id="res-2",
            case_id=proposed.case_id,
            workspace=proposed.workspace,
            outcome=ResolutionOutcome.APPROVE_POLICY_CHANGE,
            answer="批准这一版",
            by=by,
            roles=("authorizer",),
            expected_version=proposed.version,
            at=T0 + timedelta(hours=1),
        ),
    )

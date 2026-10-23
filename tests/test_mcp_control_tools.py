"""control 目录里那五个工具的行为（``MCP_SERVICE.md`` §2）。

## 本阶段只落地**有背书**的那些

目录里共 15 个工具，Phase C 只落地其中五个 —— 其余十个的背书（执行器 作业存储、
决策提供方、Crawl4AI 多页遍历、检索分配）当时都还没落地，写出来会是一份按想象编
的接口。哪一个属于哪一阶段写在 ``marketops/security/catalog.py`` 的 ``phase`` 字段
里，是一个可以被读出来、也可以被数出来的事实。

## 两条贯穿本文件的纪律

* **失败要是一个结果，不是一次崩溃。** MCP 工具抛异常时，调用方拿到的是
  Python 异常的字符串 —— 那既不可读也不可编程处理。所以每个工具对坏输入
  返回一条结构化错误，而这里逐条钉住它。
* **凭据永不回显。** ``get_capabilities`` 是要被 Claude Code 读的，而读到的
  东西会进模型上下文。所以「配没配」可以答，「配的是什么」永远不能答。
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from marketops.adapters.build_stamp import LOADED_BUILD
from marketops.adapters.hashing import sha256_file
from marketops.adapters.mcp_server import tools as mcp_tools
from marketops.adapters.mcp_server.tools import control_handlers
from marketops.adapters.page_mirror import page_path
from marketops.adapters.local_board import LocalBoardDispatcher
from marketops.config import REPO_ROOT, Settings
from marketops.domain.enums import Cohort
from marketops.domain.evidence import AccountRecord, Evidence
from marketops.interfaces.composition import default_dispatcher
from marketops.ports.store import EvidenceStore
from marketops.security.catalog import TOOL_CATALOG, Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload
from tests.test_evidence_store_contract import make_evidence

SECRET = "sk-do-not-echo-me-0123456789"


@pytest.fixture
def control(settings: Settings, tmp_path):
    """一个 controller 会话的驱动器：调用给定工具并返回结果。"""
    ring_path = tmp_path / "tokens.json"
    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring_path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, "claude-control", ["controller"]))
        ),
        encoding="utf-8",
    )
    ring = TokenRing.from_file(ring_path)

    def call(tool: str, arguments: dict | None = None):
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            calls=((tool, arguments or {}),),
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome.call(tool)

    return call


# ----------------------------------------------------------------------
# get_capabilities
# ----------------------------------------------------------------------
#: 表上应当有的**七**行：五件集成（四件 Phase D/E + 派发）+ 两个决策后端。
#: 决策那两行的名字来自 ``tools._DECISION_NAMES``（没构造后端时它照样答得出
#: 「本该有哪几个」）；本用例的驱动器不传后端，所以看到的就是那两个名字。
_EXPECTED_ROWS = {"serper", "http", "crawl4ai", "mineru", "dispatch", "jev", "laya"}


def test_capabilities_reports_the_seven_mandatory_integrations(settings, control) -> None:
    """七件套必须**恰好**都在表里 —— 哪怕其中几件当前不可用。

    一个只列「可用项」的能力表会让缺件消失，而缺件恰恰是 §20 要求被看见的
    东西（``NOT_RUN`` 必须显式，不能被省略成不存在）。

    判据是**相等**而不是包含：包含只对「少了一行」报警，而这张表是给调用方读
    的接口 —— 多出来的一行同样是它必须能读懂的东西。加第八件集成时这个用例会
    红，红的位置正好是「这一行要不要写进契约」要被回答的地方。
    """
    payload = control("get_capabilities").as_json()
    names = {row["name"] for row in payload["integrations"]}
    assert names == _EXPECTED_ROWS


def test_capabilities_does_not_claim_a_verification_that_never_happened(settings, control) -> None:
    """不得出现一个恒绿的健康旗。

    ``MCP_SERVICE.md`` §2 逐字要求报告**上一次校验的时间**，而不是一个
    永久的绿勾。本阶段没有任何东西记录过真实校验，所以答案必须是
    「从未校验」，而不是「正常」。
    """
    payload = control("get_capabilities").as_json()
    for row in payload["integrations"]:
        assert row["last_live_verification"] is None, (
            f"{row['name']} 声称有一次从未发生过的校验"
        )
    assert payload["note"], "「从未校验」必须有一句话说明，否则它看起来像数据缺失"


def test_capabilities_reflects_configuration_without_ever_echoing_it(settings, tmp_path) -> None:
    """配置了要如实说，值一个字都不能回显。

    这是本文件里最直接的一条安全断言：``get_capabilities`` 的输出会进
    调用方的模型上下文，任何一个被回显的凭据都等于一次泄漏。
    """
    leaked = Settings(
        data_root=settings.data_root,
        profiles_dir=settings.profiles_dir,
        profile=settings.profile,
        serper_api_key=SECRET,
    )
    ring_path = tmp_path / "tokens.json"
    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring_path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, "claude-control", ["controller"]))
        ),
        encoding="utf-8",
    )
    outcome = drive_with_settings(
        settings=leaked,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        calls=(("get_capabilities", {}),),
        want_tool_list=False,
    )
    rendered = json.dumps(outcome.call("get_capabilities").as_json(), ensure_ascii=False)

    assert SECRET not in rendered, "凭据被回显了"
    serper = next(r for r in outcome.call("get_capabilities").as_json()["integrations"] if r["name"] == "serper")
    assert serper["configured"] is True, "配了 key 却报未配置"


def test_capabilities_states_the_two_hard_constraints(settings, control) -> None:
    """``research_only`` / ``send_authorized`` 必须在能力表里被说出来。

    调用方据此知道自己面对的是哪一种服务。把它们藏起来，等于让「能不能发信」
    变成调用方从行为里猜出来的事。
    """
    payload = control("get_capabilities").as_json()
    assert payload["research_only"] is True
    assert payload["send_authorized"] is False


def test_capabilities_names_the_outbound_wiring_separately(settings, control) -> None:
    """外发路径存不存在是**第三格**，并且缺哪几样要说得出来（§6）。

    ``send_authorized`` 与 ``outbound_capable`` 答的不是同一个问题：前者是研究
    平面的常量（研究身份能不能发信），后者是这个部署有没有接上一条外发路径。
    把两者合成一格，症状是一个「false」—— 它既可能是「研究身份不许」，也可能是
    「中继没配」，而这两种对运维是两件事。

    缺件清单是这条判据的**主句**：只报「不行」而不报「差什么」的能力表，读的人
    无从补起。本用例的 Settings 是直接构造的（外联那几格都是缺省），所以每一样
    都不在 —— 逐字列出全部六个名字，是因为少列一个就少一条「运维据此能补上」的
    线索，而这一格的全部价值就在于那份线索。
    """
    payload = control("get_capabilities").as_json()
    assert payload["outbound_capable"] is False
    assert payload["outbound_missing"] == [
        "smtp_host",
        "smtp_port",
        "smtp_credentials",
        "smtp_security",
        "outbound_from_address",
        "outbound_allowlist",
    ]


def test_wiring_the_outbound_path_leaves_send_authorized_alone(settings) -> None:
    """把清单补齐，``outbound_capable`` 变 true 而 ``send_authorized`` **不动**。

    这是 §0 那句话在能力表这一层的执行器：任何环境变量都不得把研究权限转成发送
    权限。一个把两格接在一起的实现在这里会红 —— 而它红得正是地方：研究身份本来
    就不因部署接线而变化。
    """
    wired = replace(
        settings,
        smtp_host="relay.example.invalid",
        smtp_port=587,
        smtp_username="sender",
        smtp_password=SECRET,
        smtp_security="starttls",
        outbound_from_address="research@example.invalid",
        outbound_allowlist=frozenset({"someone@example.invalid"}),
    )
    ring_path = settings.data_root / "tokens.json"
    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "claude-control", ["controller"]))),
        encoding="utf-8",
    )

    outcome = drive_with_settings(
        settings=wired,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        calls=(("get_capabilities", {}),),
        want_tool_list=False,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    payload = outcome.call("get_capabilities").as_json()

    assert payload["outbound_capable"] is True
    assert payload["outbound_missing"] == []
    assert payload["send_authorized"] is False, "接上外发路径把 send_authorized 抬起来了"
    assert payload["research_only"] is True
    rendered = json.dumps(payload, ensure_ascii=False)
    assert SECRET not in rendered, "中继口令进了能力表"


def test_capabilities_does_no_paid_probe(settings, control) -> None:
    """读一次能力表不得触发任何计费调用。

    这条靠的是「拿到结果」本身：Serper 的探针会真的发一次请求，而本用例
    的 Settings 没有 key（也不该有）。若实现里塞了一个付费探针，它会先
    抛网络异常，工具会返回错误而不是一张表。
    """
    result = control("get_capabilities")
    assert result.is_error is False


def test_capabilities_carries_the_build_this_process_loaded(settings, control) -> None:
    """能力表要答得出「**这个进程**加载的是哪一版」。

    ``version`` 答不了：它是常量，一个 09-22 起的服务与一个刚起的服务报同一个数字，
    而它们加载的代码可以差三个提交。2026-09-23 的收口现场就是撞在这上面 —— 回执
    分辨不出陈旧进程。

    断言比的是**同一个对象**（``LOADED_BUILD``）：两个表面对同一个进程各报一份指纹，
    只会让其中一份是错的，而两份在报告里长得一样。
    """
    payload = control("get_capabilities").as_json()

    assert payload["build"] == LOADED_BUILD.payload()
    assert payload["build"]["source_tree"] == str(REPO_ROOT)
    assert payload["build"]["revision"], f"本机读得出来：{payload['build']['errors']}"
    assert "build" in payload["note"], "这一格的读法（加载时 ≠ 现在）必须写在注里"


# ----------------------------------------------------------------------
# get_capabilities · dispatch 那一行（Phase G5c）
# ----------------------------------------------------------------------
def _capabilities_row(
    settings: Settings,
    *,
    dispatcher: object = None,
    wired: frozenset[str] = frozenset(),
    jobs: object = None,
) -> dict[str, object]:
    """不经服务面直接读表上的 dispatch 行。

    这一行要问的三个问题里有两个（``configured`` / ``runtime_ready``）取决于
    **谁来构造这个服务面**，而 ``control`` 那条路径（``drive_with_settings``）
    永远不传 ``dispatcher`` —— 于是它测不到「配置齐了、这个进程也装了」那一格。

    ``jobs`` 同理，而且更要紧：``wired`` 那一格问的是「**这个实例**里有没有一条
    工具会走到派发」，而 G 那五条进不进表由 ``jobs`` 决定。缺省 ``None``（没有
    作业存储）是**一个合法部署**，也是本文件缺省的被测对象。
    """
    caps = control_handlers(settings=settings, wired=wired, dispatcher=dispatcher, jobs=jobs)[
        "get_capabilities"
    ]()
    return next(row for row in caps["integrations"] if row["name"] == "dispatch")


@pytest.fixture
def dispatch_configured(settings: Settings, tmp_path: Path) -> Settings:
    """本部署**选择派发**，且任务板的根是这一个用例自己的目录。"""
    return replace(
        settings,
        dispatch_executor="local",
        dispatch_root=tmp_path / "board",
    )


def test_an_unconfigured_executor_row_names_what_is_missing(settings: Settings) -> None:
    """本部署选择不派发：两格都是假，且 blocker 点名要改哪一个变量。

    不写一句「外部执行器不可用」：读到它的人手里有环境变量，需要知道的是改哪一个。
    """
    row = _capabilities_row(replace(settings, dispatch_executor="none"))
    assert row["configured"] is False
    assert row["runtime_ready"] is False
    assert "MARKETOPS_DISPATCH_EXECUTOR" in str(row["blocker"])


def test_the_dispatch_row_reads_the_object_not_the_settings(dispatch_configured: Settings) -> None:
    """``configured`` 问配置，``runtime_ready`` 问**这个进程**。

    两者在这条路径上恰好一致（``_serve`` 一定构造，构造不出来就退 2），所以
    如果这一格是从 ``Settings`` 重推的，本用例**不会红** —— 上半段与下半段会
    给出同一个答案。让它们分开的正是「谁来构造这个服务面」：一个不传 ``dispatcher``
    的调用方得到的是「配置齐了但这个进程没有」，而那件事是真的。

    下半段是**正对照**：真的把组合根构造出来的对象递进去，那一格翻真。
    """
    without = _capabilities_row(dispatch_configured, dispatcher=None)
    assert without["configured"] is True, "三样都给了"
    assert without["runtime_ready"] is False, "可这个进程没有把它装起来"
    assert without["blocker"], "两句话不同，不能合成一句空 blocker"

    built = default_dispatcher(dispatch_configured)
    assert isinstance(built, LocalBoardDispatcher)
    with_object = _capabilities_row(dispatch_configured, dispatcher=built)
    assert with_object["runtime_ready"] is True


def test_the_dispatch_row_says_which_kind_of_not_wired_it_is(
    dispatch_configured: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``wired`` 为假时，那句话必须说清是**哪一种**没有调用路径。

    两种「为假」的处置不同：「这个构建里没有工具落点」（``_DISPATCH_TOOL`` 是
    空串 —— 只可能出现在一个人为改回去的构建上）要改的是**这条工具的接线**；
    「有落点、但**这个实例**没把作业存储装起来」要改的是**这个部署**。
    合成一句会让运维去配一个已经配好的东西。判据要求两句话互斥。

    H2b 之前本用例测的是第一种，而今天真的成立的是第二种 —— 第一种只剩
    ``monkeypatch`` 这一条路，下面仍然把它走完（否则那段措辞没有输入能走到，
    与一条恒真的健康旗同类）。
    """
    monkeypatch.setattr(mcp_tools, "_DISPATCH_TOOL", "")
    no_landing = _capabilities_row(dispatch_configured, dispatcher=default_dispatcher(dispatch_configured))
    assert no_landing["wired"] is False
    assert "没有工具落点" in str(no_landing["blocker"])
    assert "尚未接线" not in str(no_landing["blocker"])

    monkeypatch.undo()
    # 有落点、但这个实例没有作业存储 ⇒ 那五条 G 工具没进表。
    no_store = _capabilities_row(dispatch_configured, dispatcher=default_dispatcher(dispatch_configured))
    assert no_store["wired"] is False
    assert f"{mcp_tools._DISPATCH_TOOL!r} 在这个进程里尚未接线" in str(no_store["blocker"])
    assert "没有工具落点" not in str(no_store["blocker"])


def test_the_dispatch_row_flips_to_wired_when_the_job_tools_are_there(
    dispatch_configured: Settings,
) -> None:
    """**正对照**：把作业存储装上，那一格真的翻面，blocker 真的空。

    没有这一条，上面那条判据在「``wired`` 永远是假」的实现下也是绿的 —— 而一个
    恒假的健康旗与一个恒真的健康旗是同一类东西。这里递进去的 ``jobs`` 就是
    ``submit_research`` 受理之后会用它去派发的那个工厂，所以这一格说的不是
    「有名字」，是「**这个实例**受理之后会把工作交出去」。
    """
    opened: list[str] = []

    def jobs():  # pragma: no cover - 本用例不会真的调度它
        opened.append("opened")
        raise AssertionError("能力表不该打开作业存储")

    row = _capabilities_row(
        dispatch_configured,
        dispatcher=default_dispatcher(dispatch_configured),
        jobs=jobs,
    )
    assert row["wired"] is True
    assert row["runtime_ready"] is True
    assert row["blocker"] == ""
    # 表是**声明**，不是探针：报「会派发」不该顺手连一次数据库。
    assert opened == []


def test_the_wired_column_answers_to_the_constant_not_to_a_hardcoded_false(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``wired: false`` 今天是**现状的如实报告**，不是一条恒假的旗。

    这一条是上面那句的全部依据：把 ``_DISPATCH_TOOL`` 改成一个名字，那一格与
    那条 blocker 都要跟着变。没有它，「今天没有工具会派发」与「实现里写死了
    False」在表上**逐字相同**，而后者会在接线的那天照旧报 false。

    顺带钉住 ``bool(_DISPATCH_TOOL) and ...`` 里那个 ``bool()``：空串时
    ``wired`` 必须是假，哪怕有人把空串塞进了 ``wired`` 集合。
    """
    assert _capabilities_row(settings, wired=frozenset({""}))["wired"] is False

    monkeypatch.setattr(mcp_tools, "_DISPATCH_TOOL", "some_new_tool")
    assert _capabilities_row(settings, wired=frozenset({"some_new_tool"}))["wired"] is True
    not_wired = _capabilities_row(settings)
    assert not_wired["wired"] is False
    assert "some_new_tool" in str(not_wired["blocker"])
    assert "没有工具落点" not in str(not_wired["blocker"])


#: 只在 ``application/dispatch.py`` 里定义的名字 —— 引用它们就是接上了派发路径。
#: ``Dispatcher`` 一族不在此列：``ports/dispatch.py`` 是契约，接线与能力表都会
#: 合法地 import 它（把端口名也算进来的话，这条判据从写下的第一天就是恒真的）。
_DISPATCH_ONLY_NAMES = frozenset(
    {"WorkDispatch", "DispatchOutcome", "DispatchReceipt", "link_labels", "INITIAL_EXTERNAL_STATUS"}
)
_PACKAGE = REPO_ROOT / "marketops"


def _referrers_of_dispatch(root: Path = _PACKAGE) -> set[str]:
    """``root`` 下**用到** ``WorkDispatch`` 一族名字的文件（相对 ``root.parent``）。

    扫的是 AST 的名字节点，不是文本：能力表那一行自己就在 docstring 与消息里
    写着 ``WorkDispatch.dispatch``，用 grep 写的判据会在写下它的那天就红。
    ``application/dispatch.py`` 排除在外 —— 定义不是引用。
    """
    found: set[str] = set()
    for path in sorted(root.rglob("*.py")):
        if path == root / "application" / "dispatch.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in _DISPATCH_ONLY_NAMES:
                found.add(str(path.relative_to(root.parent)))
            elif isinstance(node, ast.Attribute) and node.attr in _DISPATCH_ONLY_NAMES:
                found.add(str(path.relative_to(root.parent)))
    return found


def test_the_scanner_sees_a_reference_and_ignores_the_defining_module(tmp_path: Path) -> None:
    """上一条判据的正对照：这个扫描器**真的会**看见引用。

    没有这一条，「没有引用者」与「扫描器什么也没扫」是同一个结果 —— 一条恒真的
    判据。两份断言缺一不可：看得见引用，且不把定义处算成引用。

    所以那个「定义处」要**真的含一个名字节点**（一个只有 ``class WorkDispatch:``
    的文件是过不了这一关的：``ClassDef`` 的类名不是 ``ast.Name``）。否则「排除
    定义处」那半条判据即使被删掉，这里也照样是绿的。

    **两种引入方式各要一个文件。** 名字进来有两条路：``from …dispatch import
    WorkDispatch``（拿到的是 ``ast.Name``）与 ``from …application import
    dispatch`` 之后写 ``dispatch.WorkDispatch(...)``（拿到的是 ``ast.Attribute``）。
    扫描器对两种各有一个分支，而只摆第一种时第二个分支**没有任何输入能走到** ——
    删掉它这一条也不会红（本文件就是这样发现这个洞的：变异把 ``Attribute`` 那支
    去掉，50 条全绿）。
    """
    package = tmp_path / "marketops"
    (package / "application").mkdir(parents=True)
    (package / "application" / "dispatch.py").write_text(
        "class DispatchOutcome:\n    CREATED = 'created'\n\n\n"
        "class WorkDispatch:\n    def dispatch(self):\n        return DispatchOutcome.CREATED\n",
        encoding="utf-8",
    )
    (package / "a_caller.py").write_text(
        "from .application.dispatch import WorkDispatch\n\n\ndef go():\n    return WorkDispatch\n",
        encoding="utf-8",
    )
    (package / "a_module_style_caller.py").write_text(
        "from .application import dispatch\n\n\ndef go():\n    return dispatch.WorkDispatch\n",
        encoding="utf-8",
    )
    assert _referrers_of_dispatch(package) == {
        "marketops/a_caller.py",
        "marketops/a_module_style_caller.py",
    }


def test_the_dispatch_tool_constant_flips_when_somebody_wires_dispatch() -> None:
    """``_DISPATCH_TOOL`` 为空 ⟺ 今天没有任何一处会派发。**两边一起变。**

    这是一条会自己翻面的判据：接线的人（在 ``marketops/`` 里引用 ``WorkDispatch``）
    必须同时把那条工具名填进 ``_DISPATCH_TOOL``，否则 ``get_capabilities`` 的 dispatch
    行会一边报 ``wired: false`` 一边有调用路径 —— 而那一刻这张表就在说谎，且没有
    任何一处看得出来。

    它也是「Phase H 有活要干」这件事的可执行形式：这一格今天为假是因为**没有
    落点**，不是因为它被写死。
    """
    referrers = _referrers_of_dispatch()
    assert bool(referrers) == bool(mcp_tools._DISPATCH_TOOL), (
        f"_DISPATCH_TOOL 是 {mcp_tools._DISPATCH_TOOL!r}，而用了 application/dispatch "
        f"的名字的文件是 {sorted(referrers)}。两者必须一起变：接线的人要把那条工具名"
        "填进 tools._DISPATCH_TOOL（空串 = 今天没有任何工具会派发）。"
    )


# ----------------------------------------------------------------------
# get_scene
# ----------------------------------------------------------------------
def test_scene_returns_the_configured_profile(settings, control) -> None:
    payload = control("get_scene").as_json()
    assert payload["scene_id"] == settings.profile
    assert payload["profile_id"] == settings.profile


def test_scene_summary_matches_the_loader(settings, control, bundle) -> None:
    """工具报的摘要与装载层报的是同一份。

    两个来源各算一遍必然先漂移一处，而漂移的那一处会是「工具说这套规则可用、
    引擎说它求不了值」——一个会让人怀疑引擎的结论。
    """
    payload = control("get_scene").as_json()
    assert payload["summary"] == bundle.summary()


def test_scene_reports_unknown_rules_explicitly(settings, control, bundle) -> None:
    payload = control("get_scene").as_json()
    assert payload["has_unknown_rules"] == bundle.has_unknown_rules
    if bundle.unknowns:
        assert payload["unknowns"], "profile 有 UNKNOWN 规则，工具却说没有"


def test_scene_reports_the_effective_guidance_version(settings, control, bundle) -> None:
    """§2 逐字要 ``get_scene`` 报 "effective policy/guidance version"，这是它。

    判据有**两半**，缺一半都不成立：

    * 工具报的版本**等于装载层算的那个** —— 否则工具报的是一个不描述它返回的
      这套规则的版本；
    * 它是一个摘要，不是空串、不是 ``None``、不是 profile id。

    第二半不是装饰：``get_scene`` 的目录摘要文字从 Phase C 起就写着「生效的策略/
    指引版本」，而在这条判据出现之前，全仓**没有任何地方存在一个版本** ——
    目录里那句话是一句没有承重物的承诺。只判第一半的话，一个恒返回空串的实现
    （两边都空）会让它继续绿。
    """
    payload = control("get_scene").as_json()
    assert payload["scene_version"] == bundle.scene_version
    assert re.fullmatch(r"[0-9a-f]{64}", payload["scene_version"]), payload["scene_version"]


def test_an_unknown_scene_is_refused_not_substituted(settings, control) -> None:
    """要一个不存在的 Scene 时**拒绝**，不能悄悄退回默认的那一个。

    静默替换会让调用方以为自己在读 A 场景的规则，实际读的是 B —— 而
    报告上两边的差异都归因到了内容本身。
    """
    result = control("get_scene", {"scene_id": "no-such-scene"})
    assert result.is_error
    assert "no-such-scene" in result.text


# ----------------------------------------------------------------------
# get_account / get_evidence
# ----------------------------------------------------------------------
def test_account_with_nothing_yet_is_empty_not_an_error(settings, control) -> None:
    """一个还没有任何产出的账号是一个**正常**答案。

    与「读不出来」混在一起时，一次空结果会被读成一次故障，而排查方向会
    错到存储层去。
    """
    result = control("get_account", {"account_id": "test-account-001"})
    assert result.is_error is False
    payload = result.as_json()
    assert payload["routes"] == []
    assert payload["opportunities"] == []
    assert payload["evidence_count"] == 0
    # 空答案不能以**建库**为代价。读一次就把 SQLite 文件造出来，会让「还没有
    # 证据」与「库是刚建的」在盘上不可区分 —— 而后者会让下一次真的写入看起来
    # 像是「库本来就在」。失败关闭在这里的意思是：没有证据就不产生状态。
    assert not settings.database_path.exists(), "一次读取把库建出来了"


def test_account_reports_where_the_organisation_record_would_come_from(settings, control) -> None:
    """还没有账号注册表这件事必须**明说**，而不是留一个空字段。

    空字段会被读成「这个账号没有组织名」，而事实是「这个信息没有来源」。
    两种读法导致两种完全不同的排查方向。
    """
    payload = control("get_account", {"account_id": "test-account-001"}).as_json()
    assert payload["account_record"] is None
    assert payload["account_record_source"], "缺失的来源必须被写出来"


def test_account_returns_a_bounded_dossier(settings, control) -> None:
    from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore

    store = SqliteEvidenceStore(settings.database_path)
    for index in range(3):
        store.put_evidence(make_evidence(evidence_id=f"e{index}", account_id="test-account-001"))

    payload = control("get_account", {"account_id": "test-account-001"}).as_json()
    assert payload["evidence_count"] == 3
    assert len(payload["evidence_refs"]) <= 3


# ----------------------------------------------------------------------
# get_evidence —— 按**引用**读，不按账号
# ----------------------------------------------------------------------
#: 这个工具的输入形状由 ``docs/MCP_SERVICE.md`` §3 那一行定死：「evidence id +
#: bounded locator」，行为要求逐字是「Reuse already acquired material without
#: refetching」。§2 那一行说的是它给什么：「an authorized stored artifact/block,
#: provenance and transformations」—— 两个字对应两个 locator。
#:
#: 在此之前这里收的是 ``(account_id, limit)`` —— 一条**按账号列举**的接口。
#: 那是 ``get_account`` 的事（它返回有界的 ``evidence_refs``），而两件事合成
#: 一件只有一个后果：**只有已经拿到账号的人才验得了一条引用**，而
#: ``submit_findings`` / ``decide`` 的调用方手里只有一个引用。于是 §3 的两行
#: 声明的入口在实现里都不存在。
URL = "https://example.co.uk/contact"
ACCOUNT = "test-account-001"


def _stored(settings: Settings, *, evidence_id: str = "e1", **overrides):
    """落一行证据，返回写入用的 store。"""
    from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore

    store = SqliteEvidenceStore(settings.database_path)
    store.put_evidence(make_evidence(evidence_id=evidence_id, account_id=ACCOUNT, **overrides))
    return store


def _mirror(
    settings: Settings,
    body: str | bytes,
    *,
    account_id: str = ACCOUNT,
    url: str = URL,
    extension: str = ".html",
) -> Path:
    """把字节写到**约定位置上** —— 证据行里的散列与它对得上。"""
    path = page_path(settings, scope=account_id, url=url, extension=extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.encode("utf-8") if isinstance(body, str) else body)
    return path


def test_the_input_shape_is_an_id_and_a_locator(settings: Settings) -> None:
    """工具面的输入**就是**那一行声明的两个名字，多一个都没有。

    比的是**集合相等**，所以多出一个 ``account_id`` 与少一个 ``locator`` 同样
    会红。那个多出来的 ``account_id`` 正是「声明与实现不一致」的载体。

    端口那一侧是正控：``EvidenceStore.get_evidence`` 收的**就是**一个 id ——
    证明「工具面只收一个 id」不是因为底下没有按账号读的能力（有，
    ``list_evidence`` 就在同一个端口上）。
    """
    handler = control_handlers(settings=settings)["get_evidence"]

    assert set(inspect.signature(handler).parameters) == {"evidence_id", "locator"}
    assert set(inspect.signature(EvidenceStore.get_evidence).parameters) == {"self", "evidence_id"}


def test_evidence_is_read_by_id_with_its_provenance(settings, control) -> None:
    """按 id 读回来的那一条带着**出处的全部字段**（provenance + transformations）。

    §2 说这个工具给的是「provenance and transformations」。只回一个
    ``literal_value`` 能让调用方读到片段，却读不出它从哪来、谁抽的 —— 而一条
    没有出处的字面片段支撑不了任何判定。

    期望的字段集合从**模型**推出来（``Evidence.model_fields``），不手抄：
    手抄的清单会在模型加字段时变成一份错的期望，而它红的方式看起来像回归。
    """
    _stored(settings, evidence_id="e1")
    _stored(settings, evidence_id="e2", literal_value="hire@example.co.uk")

    payload = control("get_evidence", {"evidence_id": "e2"}).as_json()

    assert payload["found"] is True
    assert payload["reason"] == ""
    assert payload["item"]["evidence_id"] == "e2"
    assert payload["item"]["literal_value"] == "hire@example.co.uk"
    assert set(payload["item"]) >= set(Evidence.model_fields)
    # 缺省问的是一段，不是整份产物 —— 所以这一格里没有 artifact 块。
    assert payload["locator"] == "block"
    assert payload["artifact"] is None


def test_an_unknown_evidence_id_is_a_result_not_an_empty_reading(settings, control) -> None:
    """引用解不开是**一个结果**，不是一次「读出来是空的」。

    两者在报告上必须不同形：空手而归会被读成「这条证据没有内容」，而真相是
    「这个 id 下没有行」。前者让人去查证据本身，后者才指向那条引用。
    """
    _stored(settings, evidence_id="e1")

    payload = control("get_evidence", {"evidence_id": "no-such-id"}).as_json()

    assert payload["found"] is False
    assert payload["item"] is None
    assert payload["artifact"] is None
    assert "no-such-id" in payload["reason"]


def test_no_store_and_no_such_row_are_different_reasons(settings, control) -> None:
    """「这个部署还没有证据库」与「这个 id 下没有行」是两句不同的话。

    合成一句会让「库还没建」被读成「这条引用是假的」—— 前者的处置是去看部署，
    后者是去看那条引用从哪来。排查方向相反。
    """
    without = control("get_evidence", {"evidence_id": "e1"}).as_json()
    assert not settings.database_path.exists(), "一次读取把库建出来了"

    _stored(settings, evidence_id="e1")
    with_store = control("get_evidence", {"evidence_id": "freshly-missing"}).as_json()

    assert without["found"] is False and with_store["found"] is False
    assert without["reason"] != with_store["reason"], "两种「解不开」被说成了同一句话"


def test_an_unknown_locator_is_refused_and_the_message_names_the_closed_set(
    settings, control
) -> None:
    """``locator`` 是**闭集**，不是自由字符串；拒绝时把取值说出来。

    「有界定位符」的「有界」有两层：回传的量有界，**可选的定位方式**也有界。
    一个自由字符串会被实现静默当成缺省值处理，于是 ``locator="什么都行"`` 与
    ``locator="block"`` 拿到同一份回答，而调用方以为自己问了别的。
    """
    _stored(settings, evidence_id="e1")

    result = control("get_evidence", {"evidence_id": "e1", "locator": "whatever"})

    assert result.is_error
    assert "block" in result.text and "artifact" in result.text


def test_an_empty_evidence_id_is_refused(settings, control) -> None:
    """空 id 是一次调用错误，不是一个结果 —— 它连「一条引用」都不是。"""
    result = control("get_evidence", {"evidence_id": ""})

    assert result.is_error
    assert "Traceback" not in result.text
    assert len(result.text) < 400


def test_the_artifact_locator_returns_the_bytes_that_row_points_at(settings, control) -> None:
    """``locator="artifact"`` 给的是**那一行指向的字节**，而且不重新抓取。

    §3 那行的行为要求是「Reuse already acquired material without refetching」
    —— 所以这一格必须落在盘上**已经有**的产物上。判据把字节写到约定位置、把它
    的散列放进证据行，读回来的是同一串字节。
    """
    body = "<html><body>grounds@example.co.uk</body></html>"
    path = _mirror(settings, body)
    _stored(settings, evidence_id="e1", source_url=URL, content_sha256=sha256_file(path))

    payload = control("get_evidence", {"evidence_id": "e1", "locator": "artifact"}).as_json()

    assert payload["found"] is True
    artifact = payload["artifact"]
    assert artifact["available"] is True
    assert artifact["media"] == "text"
    assert artifact["text"] == body
    assert artifact["truncated"] is False
    assert artifact["byte_length"] == len(body.encode("utf-8"))
    assert artifact["ref"].startswith("mirrors/pages/"), artifact["ref"]


def test_a_row_whose_bytes_are_gone_still_resolves_and_says_so(settings, control) -> None:
    """行在、字节不在 —— ``found`` 仍旧为真，``artifact.available`` 为假。

    两个问题分开：**这条引用解不解得开**（行在不在）与**材料在不在本机**。
    合并成一个布尔会让「库是从别的机器恢复的」看起来像「这条引用是假的」，
    而两者的处置完全不同：一个去修部署，一个去查引用从哪来。

    顺带钉住它的**代价**：这件事不是一次错误。抛出去会让调用方以为读失败了，
    于是重试，拿到同一个答案。
    """
    _stored(settings, evidence_id="e1", source_url=URL, content_sha256="deadbeef")

    payload = control("get_evidence", {"evidence_id": "e1", "locator": "artifact"}).as_json()

    assert payload["found"] is True
    assert payload["artifact"]["available"] is False
    assert payload["artifact"]["text"] == ""
    assert payload["artifact"]["ref"] == ""
    assert payload["artifact"]["reason"]


def test_the_artifact_read_is_bounded_and_reports_the_true_size(settings, control) -> None:
    """回传有界，**真实大小**照报，截断被标出来。

    一个只回前 N 字节、又把 N 当成大小的实现，在两份材料上都「正常」—— 差别只在
    读者以为手里的东西是完整的。§3 要的「有界」指的是回传量，不是把产物说成
    那么长。
    """
    body = "x" * (mcp_tools.MAX_ARTIFACT_BYTES + 5000)
    path = _mirror(settings, body)
    _stored(settings, evidence_id="e1", source_url=URL, content_sha256=sha256_file(path))

    artifact = control(
        "get_evidence", {"evidence_id": "e1", "locator": "artifact"}
    ).as_json()["artifact"]

    assert artifact["truncated"] is True
    assert artifact["byte_length"] == len(body)
    assert len(artifact["text"]) <= mcp_tools.MAX_ARTIFACT_BYTES


def test_a_binary_artifact_is_reported_as_binary_not_as_text(settings, control) -> None:
    """非 UTF-8 的产物（PDF 那些）报 ``binary``，不回一段替换字符。

    猜编码的代价不是乱码，是一次**看起来成功了**的读取：调用方拿到一段
    替换字符（U+FFFD），而没有任何字段说它坏了 —— 于是「这一页里没有那段字面量」
    这句话建立在一份损坏的解码上。
    """
    raw = b"%PDF-1.7\n\xff\xfe\x00\x01binary\x80\x81"
    path = _mirror(settings, raw, extension=".pdf")
    _stored(settings, evidence_id="e1", source_url=URL, content_sha256=sha256_file(path))

    artifact = control(
        "get_evidence", {"evidence_id": "e1", "locator": "artifact"}
    ).as_json()["artifact"]

    assert artifact["available"] is True
    assert artifact["media"] == "binary"
    assert artifact["text"] == ""
    assert artifact["byte_length"] == len(raw)


# ----------------------------------------------------------------------
# get_report
# ----------------------------------------------------------------------
def test_report_lists_what_exists_when_asked_for_nothing(settings, control) -> None:
    payload = control("get_report").as_json()
    assert payload["available"] == []


def test_report_returns_a_written_run(settings, control) -> None:
    settings.benchmarks_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": "run-2026-09-22t10-00-00", "accounts_total": 3}
    (settings.benchmarks_dir / "run-2026-09-22t10-00-00.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    result = control("get_report", {"run_id": "run-2026-09-22t10-00-00"})
    assert result.is_error is False
    assert result.as_json() == payload


#: 拒绝理由的**固定措辞**。测试断言它出现，于是「因为别的原因失败」不会被读成
#: 「判据生效了」—— 这条区别在本文件里刚被证明过是真实存在的（见下一条）。
_NOT_CANONICAL = "不是规范形式"


@pytest.mark.parametrize(
    "run_id",
    ["../../etc/passwd", "..%2f..%2fetc", "a/b", "run id", "..", "Foo"],
    ids=["dotslash", "encoded", "slash", "space", "dotdot", "case"],
)
def test_report_refuses_a_run_id_that_is_not_canonical(settings, control, run_id: str) -> None:
    """``run_id`` 会被拼成文件名，所以它必须**已经是**自己的规范形式。

    这条不是「过滤掉几个坏字符」—— 那样的实现永远落后一个编码变体。
    判据是 ``slugify(run_id) == run_id``：任何需要被规范化的输入都直接拒绝，
    于是不存在「还有哪一种写法能绕过去」这个问题。

    判据现在**只有一处**（``marketops/domain/util.check_canonical_id``），写入侧
    （``Evidence`` / ``FetchRequest`` / ``SearchQuery``）与读取侧共用它 —— 所以
    这一条测的是「读路径把它翻译成了 ``ToolError``」，判据本身住在
    ``tests/test_ids_are_filenames.py``。

    **每条输入都带正控**：先证明它确实不满足判据，再断言它被拒。少了前一步，
    一条因为别的原因失败（比如文件恰好不存在）的用例会与真正生效的判据长得
    一模一样 —— 这张表此前就有两条这样的成员（``run_id`` 与 ``""``），而
    真正的反例 ``Foo``（大小写合并）当时**不在**表里。
    """
    from marketops.domain.util import FILENAME_ID_MAX_LENGTH, slugify

    # 正控用的是**实现的那个界**：写死 80 会让这条控制与实现差 20，
    # 而差 20 的那一段正是历史上「按 40 落盘、按 60 读」的成因。
    assert slugify(run_id, max_length=FILENAME_ID_MAX_LENGTH) != run_id, (
        f"{run_id!r} 其实是规范形式，它不该在这张表里 —— 这条用例会因为别的原因变绿"
    )
    result = control("get_report", {"run_id": run_id})
    assert result.is_error, f"run_id={run_id!r} 被接受了"
    assert _NOT_CANONICAL in result.text, f"拒绝理由不是「不规范」而是 {result.text!r}"


def test_a_canonical_run_id_with_no_report_is_not_found_not_refused(settings, control) -> None:
    """**规范但不存在**与**不规范**是两种拒绝，理由不同。

    ``run_id="run_id"`` 是一个完全合法的 id：下划线在 ``slugify`` 的允许集里，
    它不需要被规范化，因此**不**该被 ``test_report_refuses_a_run_id_that_is_not_canonical``
    拒绝。此前它挂在那张参数表里，拿到的是「找不到那份报告」—— 用例绿了，
    但它测的是另一件事，而且掩盖了真判据的漏洞：真正的反例 ``Foo``
    （``slugify`` 会把大小写合并，两个 run 落进同一个文件）当时不在那张表里。

    两种拒绝必须**说得出是哪一种**：判据漏掉一种写法时要指向判据，报告不在
    时要指向报告。两者混成一句「失败」，红线就失去了指向。
    """
    result = control("get_report", {"run_id": "run_id"})
    assert result.is_error
    assert _NOT_CANONICAL not in result.text, (
        f"一个合法的 id 被说成「不规范」：{result.text!r} —— 两种拒绝的理由必须分得开"
    )
    assert "run_id" in result.text


@pytest.mark.parametrize("run_id", ["", "   "], ids=["empty", "blank"])
def test_a_blank_run_id_means_list_them(settings, control, run_id: str) -> None:
    """空串走的是**列出可用报告**那条路，与「不给 run_id」同一条。

    这也是一条例外：``slugify("") != ""``，所以按字面判据它「不」规范。但它是
    一个**分支**而不是一种输入错误 —— 拼文件名的代码在它之前就返回了。分开测，
    是为了让上面那张表里的每一条都真的是判据的反例。
    """
    payload = control("get_report", {"run_id": run_id}).as_json()
    assert payload["available"] == []
    assert payload["note"]


def test_a_windows_shaped_absolute_path_is_not_a_run_id(settings, control) -> None:
    """盘符形态的绝对路径也「不规范」。

    样本**由字符拼出**，不写成字面量。仓库里有两条闸会把盘符字面量判成「另一台
    机器的路径假设」—— ``tests/test_portability.py`` 的 ``windows-drive-letter``
    与 ``tests/test_no_stale_assumptions.py`` 的同一条规则。它们针对的是**假设**，
    而这里要的是一个**输入**；把这一条加进那两张 allowlist，代价是让那两条告警
    各自少一格可信度，换来的是一个字符类判据已经覆盖的用例。

    ``tests/test_no_stale_assumptions.py`` 自己造样本时用的正是这个办法
    （它的模块文档写明「本文件**没有**豁免」）。
    """
    from marketops.domain.util import FILENAME_ID_MAX_LENGTH, slugify

    windows = "C" + ":" + "\\" + "windows" + "\\" + "win.ini"
    assert slugify(windows, max_length=FILENAME_ID_MAX_LENGTH) != windows, (
        "这条用例会因为别的原因变绿"
    )
    result = control("get_report", {"run_id": windows})
    assert result.is_error
    assert _NOT_CANONICAL in result.text


def test_a_missing_report_is_an_error_not_an_empty_report(settings, control) -> None:
    """「这份报告不存在」与「这份报告是空的」是两件事。"""
    result = control("get_report", {"run_id": "run-2026-01-01t00-00-00"})
    assert result.is_error
    assert "run-2026-01-01t00-00-00" in result.text


# ----------------------------------------------------------------------
# 目录里有、本构建还没接线的那几个（controller 看得见的那一部分）
#
# 这一节不写死数量：``_unbacked_control`` 每次从目录与实现算出来，而目录里
# 没实现的总数随阶段变化（Phase C 是九个，Phase D/E/F 各接掉一部分 —— 那些
# 多半是 researcher 的工具，不进这个集合）。写死一个数字的注释会在下一次落地时
# 变成一句错话，而它是散文，没有执行器会替它转红。
# ----------------------------------------------------------------------
#: 一个一定不存在的工具名。它的错误信息就是「不存在」的标准形态 ——
#: 那一句由 SDK 生成（``Unknown tool: <name>``），这里只做对照。
NO_SUCH_TOOL = "no_such_tool_xyzzy"


def _backed(settings: Settings) -> frozenset[str]:
    """本构建真的有实现的工具名。

    期望值从**实现**里推出来，不手抄：手抄的清单会在第一个新工具落地时
    变成一份错的期望，而它红的方式看起来像权限回归。
    """
    from marketops.adapters.mcp_server.tools import control_handlers

    return frozenset(control_handlers(settings=settings))


def _unbacked_control(settings: Settings) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name, spec in TOOL_CATALOG.items()
            if Role.CONTROLLER in spec.roles and name not in _backed(settings)
        )
    )


def test_the_unbacked_set_is_neither_empty_nor_everything(settings) -> None:
    """两侧都要有成员，否则下面三条会变成永真式。

    全部实现之后这条会红 —— 那时**删掉这一组**，而不是让它空转：一条永远
    成立的判据与一条永远不成立的判据同样没有信息。
    """
    backed = _backed(settings)
    unbacked = _unbacked_control(settings)
    assert unbacked, "目录里已经没有未接线的工具了 —— 删掉这一组测试，别再留一个空转的判据"
    assert backed, "一个工具都没实现？那按目录推出来的期望就失去了对照"
    assert backed.isdisjoint(unbacked)
    assert set(backed) | set(unbacked) == {
        name for name, spec in TOOL_CATALOG.items() if Role.CONTROLLER in spec.roles
    }, "被授权的工具既不在已实现里、也不在未接线里 —— 中间少了一类"


def test_an_unbacked_tool_says_which_phase_it_belongs_to(settings, control) -> None:
    """已授权但未接线 ⇒ 一句带阶段号的话，而不是「工具不存在」。

    「不存在」是一个**错误的事实**：``submit_research`` 在目录里、也被授权，
    只是本构建没实现。它会把排查方向引到「你名字写错了」，而正确的方向是
    「等 Phase G」。
    """
    for name in _unbacked_control(settings):
        result = control(name, {})
        assert result.is_error, f"{name} 未接线却返回了成功"
        assert name in result.text
        assert f"Phase {TOOL_CATALOG[name].phase}" in result.text, (
            f"{name} 的拒绝理由没带阶段号：{result.text!r}"
        )


def test_unbacked_and_nonexistent_are_two_different_answers(settings, control) -> None:
    """对照：这两句话必须**不同**。

    若「未接线」与「不存在」相同，那上面那条测试可以用一句 ``Unknown tool``
    通过 —— 而那句话正是它要避免的。
    """
    name = _unbacked_control(settings)[0]
    unbacked = control(name, {})
    absent = control(NO_SUCH_TOOL, {})

    assert unbacked.text != absent.text, "「未接线」与「不存在」被说成了同一句话"
    assert "Unknown tool" not in unbacked.text


def test_the_phase_answer_does_not_depend_on_the_arguments(settings, control) -> None:
    """未接线的工具带一堆非法参数，答案仍然是那一句阶段号。

    这条证明判定发生在**参数校验之前**：这些 stub 的签名没有参数，任何参数都
    会先被校验拒绝。所以顺序一变，这句话就会变成
    ``Error executing tool …`` —— 而那正好泄漏了「这个工具存在」。
    """
    name = _unbacked_control(settings)[0]
    plain = control(name, {})
    junk = control(name, {"totally_bogus_field": 1, "another": [1, 2, 3]})
    assert plain.text == junk.text, f"参数改变了未接线工具的回答：{junk.text!r}"


# ----------------------------------------------------------------------
# 输出形状：调用方要能编程处理
# ----------------------------------------------------------------------
def test_every_tool_returns_structured_content(settings, control) -> None:
    for tool, arguments in (
        ("get_capabilities", {}),
        ("get_scene", {}),
        ("get_account", {"account_id": "test-account-001"}),
        ("get_evidence", {"evidence_id": "no-such-evidence"}),
        ("get_report", {}),
    ):
        result = control(tool, arguments)
        assert result.is_error is False, f"{tool} 失败了：{result.text}"
        assert isinstance(result.as_json(), dict), f"{tool} 没有返回结构化内容"


def test_bad_input_never_surfaces_a_python_traceback(settings, control) -> None:
    """输入错误必须是**一条**可读的错误，不是一段 Python 栈。

    输出会进调用方的模型上下文；一段栈既浪费上下文，也会把内部的模块路径
    与函数名暴露出去。
    """
    result = control("get_evidence", {"evidence_id": ""})
    assert result.is_error
    assert "Traceback" not in result.text
    assert "File \"" not in result.text
    assert len(result.text) < 400


def test_the_account_record_model_is_not_reimplemented_here(settings, control) -> None:
    """工具返回的证据引用指向领域模型，不是一份手抄的字段表。

    对照：``AccountRecord`` 的字段名若被工具手抄一遍，加一个字段就会让
    工具与模型分家，而分家之后接口层会继续工作 —— 只是少一个字段。
    """
    record = AccountRecord(
        account_id="test-account-001",
        organisation="Example Coatings Ltd",
        cohort=Cohort.KNOWN_SUCCESS,
    )
    assert record.account_id
    assert REPO_ROOT.joinpath("marketops", "domain", "evidence.py").exists()

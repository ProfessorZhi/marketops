"""角色隔离真的挂在每一次调用上了吗（§7）。

``tests/test_security_catalog.py`` 证明策略是对的。本文件证明那条策略有一个
**执行器**，并且执行器的输入是传输层已经确立的身份。

## 承重的三条断言

1. **两个角色看见的工具集不同，且各自等于目录算出来的那一份。**
   只测「研究侧看不见 control 工具」是不够的：一个把所有工具都藏起来的
   实现能通过它。所以两侧都要测，且期望值从目录推出。
2. **拒绝要与「不存在」逐字相同。**
   两者若可区分，研究侧 worker 就能通过错误信息的差别把另一套目录枚举出来。
   §7 说的隔离是**不可区分**，不是「拒绝得清楚一点」。
   （「逐字」指的是**同一条措辞**：两条回答里都嵌着调用方自己发来的工具名，
   那两个名字本来就不同，所以可区分性落在**形状**上 —— 见 ``_denial_shape``。）
3. **伪造的参数不能换来权限。**
   这是本文件存在的核心理由。§7 逐字反对拿模型自己填的字符串当认证 ——
   所以必须有一条测试真的把 ``{"role": "controller"}`` 递进去，并证明它
   什么也没换来。少了这一条，一个「先看参数再看身份」的实现全绿。
"""

from __future__ import annotations

import pytest

from marketops.security.catalog import TOOL_CATALOG, Role, visible_tools
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

#: 一个一定不存在的工具名。它的错误信息就是「不存在」的标准形态。
NO_SUCH_TOOL = "no_such_tool_xyzzy"

_CONTROL_ONLY = sorted(
    n for n, s in TOOL_CATALOG.items() if s.roles == frozenset({Role.CONTROLLER})
)
_RESEARCH_ONLY = sorted(
    n for n, s in TOOL_CATALOG.items() if s.roles == frozenset({Role.RESEARCHER})
)


@pytest.fixture
def ring(tmp_path) -> TokenRing:
    path = tmp_path / "tokens.json"
    _, control_digest = mint("claude-control", [Role.CONTROLLER])
    _, worker_digest = mint("worker-1", [Role.RESEARCHER])
    path.write_text(
        __import__("json").dumps(
            ring_payload(
                ring_entry(control_digest, "claude-control", ["controller"]),
                ring_entry(worker_digest, "worker-1", ["researcher"]),
            )
        ),
        encoding="utf-8",
    )
    return TokenRing.from_file(path)


@pytest.fixture
def controller_token(tmp_path_factory) -> tuple[TokenRing, str]:
    return _ring_and_token(tmp_path_factory, "claude-control", Role.CONTROLLER)


@pytest.fixture
def worker_token(tmp_path_factory) -> tuple[TokenRing, str]:
    return _ring_and_token(tmp_path_factory, "worker-1", Role.RESEARCHER)


def _ring_and_token(tmp_path_factory, principal: str, role: Role) -> tuple[TokenRing, str]:
    path = tmp_path_factory.mktemp("mcp") / "tokens.json"
    token, digest = mint(principal, [role])
    path.write_text(
        __import__("json").dumps(
            ring_payload(ring_entry(digest, principal, [role.value]))
        ),
        encoding="utf-8",
    )
    return TokenRing.from_file(path), token


# ----------------------------------------------------------------------
# 目录的可见性
# ----------------------------------------------------------------------
def test_controller_sees_exactly_the_control_catalog(settings, controller_token) -> None:
    ring, token = controller_token
    outcome = drive_with_settings(settings=settings, tokens=ring, token=token)

    assert not outcome.handshake_error, outcome.handshake_error
    assert set(outcome.tool_names) == {
        n for n, s in TOOL_CATALOG.items() if Role.CONTROLLER in s.roles
    }


def test_researcher_sees_exactly_the_research_catalog(settings, worker_token) -> None:
    ring, token = worker_token
    outcome = drive_with_settings(settings=settings, tokens=ring, token=token)

    assert not outcome.handshake_error, outcome.handshake_error
    assert set(outcome.tool_names) == {
        n for n, s in TOOL_CATALOG.items() if Role.RESEARCHER in s.roles
    }


def test_the_two_catalogs_differ(settings, controller_token, worker_token) -> None:
    """对照：两个角色的可见集合必须**不一样**。

    若中间件根本没装，两次都会列出全部工具，上面两条会各自失败得很难看懂；
    这一条把「隔离失效」直接指出来。
    """
    c_ring, c_token = controller_token
    w_ring, w_token = worker_token
    control = drive_with_settings(settings=settings, tokens=c_ring, token=c_token)
    worker = drive_with_settings(settings=settings, tokens=w_ring, token=w_token)

    assert set(control.tool_names) != set(worker.tool_names)
    assert set(worker.tool_names).isdisjoint(_CONTROL_ONLY)
    assert set(control.tool_names).isdisjoint(_RESEARCH_ONLY)


def test_tool_names_are_unique(settings, controller_token) -> None:
    ring, token = controller_token
    outcome = drive_with_settings(settings=settings, tokens=ring, token=token)
    assert len(outcome.tool_names) == len(set(outcome.tool_names))


# ----------------------------------------------------------------------
# 调用被拒 == 不存在
# ----------------------------------------------------------------------
def _denial_shape(text: str, name: str) -> str:
    """把回答里那个**调用方自己给的名字**抹掉。

    「逐字相同」不能按字面理解：两条回答里都嵌着调用方发来的工具名，而那
    两个名字（``control_job`` 与 ``no_such_tool_xyzzy``）本来就不同。可区分性
    来自**形状**：``Unknown tool: <我叫的那个名字>`` 对任何名字都成立，所以
    一次枚举无法从回答里读出「这个名字存在吗」。
    """
    return text.replace(name, "<名称>")


@pytest.mark.parametrize("tool", _CONTROL_ONLY, ids=_CONTROL_ONLY)
def test_researcher_cannot_call_a_control_tool(settings, worker_token, tool: str) -> None:
    ring, token = worker_token
    outcome = drive_with_settings(
        settings=settings, tokens=ring, token=token, calls=((tool, {}), (NO_SUCH_TOOL, {}))
    )

    denied = outcome.call(tool)
    absent = outcome.call(NO_SUCH_TOOL)
    assert denied.is_error, f"{tool} 对研究侧返回了成功"
    assert absent.is_error
    # 两半都要：形状相同（不可区分），且各自恰好是 SDK 那一句（不是自编的措辞）。
    assert _denial_shape(denied.text, tool) == _denial_shape(absent.text, NO_SUCH_TOOL), (
        "「不属于你的工具」与「不存在的工具」被说成了两种形状 —— 文本是：\n"
        f"  拒绝：{denied.text!r}\n  不存在：{absent.text!r}\n"
        "形状上的差别足以让一次枚举把另一套目录读出来。"
    )
    assert denied.text == f"Unknown tool: {tool}", (
        f"拒绝的文本不是 SDK 对未知工具那一句：{denied.text!r} —— "
        "自编的措辞会与「不存在」那条分家，而分家就是泄漏"
    )
    assert absent.text == f"Unknown tool: {NO_SUCH_TOOL}"


@pytest.mark.parametrize("tool", _RESEARCH_ONLY, ids=_RESEARCH_ONLY)
def test_controller_cannot_call_a_research_tool(settings, controller_token, tool: str) -> None:
    """反方向同样成立 —— 隔离不是单向的。"""
    ring, token = controller_token
    outcome = drive_with_settings(settings=settings, tokens=ring, token=token, calls=((tool, {}),))
    assert outcome.call(tool).is_error


def test_the_denial_is_reported_as_a_tool_error_not_a_crash(settings, worker_token) -> None:
    """拒绝是一个**结果**，不是协议错误。

    协议错误会中断这次会话，而拒绝不该有那个代价：worker 里一次越权尝试
    之后的正常调用应当照常工作。
    """
    ring, token = worker_token
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        calls=((_CONTROL_ONLY[0], {}), ("get_evidence", {"evidence_id": "no-such-evidence"})),
    )
    assert not outcome.handshake_error, "一次越权调用把整条会话打断了"
    assert outcome.call("get_evidence").is_error is False


# ----------------------------------------------------------------------
# §7 的正面战场：参数不能换来权限
# ----------------------------------------------------------------------
_FORGED_ARGUMENTS: tuple[dict[str, object], ...] = (
    {"caller": "claude-control"},
    {"role": "controller"},
    {"roles": ["controller"]},
    {"principal": "claude-control"},
    {"caller": "claude-control", "role": "controller", "scopes": ["marketops:controller"]},
    {"_meta": {"role": "controller"}},
)


@pytest.mark.parametrize("arguments", _FORGED_ARGUMENTS, ids=lambda a: "+".join(sorted(a)))
def test_forged_arguments_grant_nothing(settings, worker_token, arguments: dict) -> None:
    """把身份往参数里塞，必须一点用都没有。

    这几组载荷正是「模型自己填的字符串」。它们在这里全部失败，是因为执行器
    根本不读 ``arguments`` 里的任何字段 —— 它只读传输层认证过的那个主体。
    """
    ring, token = worker_token
    tool = _CONTROL_ONLY[0]
    outcome = drive_with_settings(
        settings=settings, tokens=ring, token=token, calls=((tool, arguments),)
    )
    assert outcome.call(tool).is_error, (
        f"伪造的参数 {arguments!r} 让 {tool} 过关了 —— 权限的输入不是传输层身份"
    )


def test_the_guard_decides_before_the_arguments_are_looked_at(settings, worker_token) -> None:
    """越权请求的参数即使**完全非法**，拒绝理由也必须是「不存在」。

    若参数的校验先发生，一个参数非法的越权调用会得到一条「字段 X 不合法」
    的错误 —— 而那条错误本身就泄漏了「这个工具存在，只是你还不能调」。
    顺序因此是判据的一部分，不是实现细节。
    """
    ring, token = worker_token
    tool = _CONTROL_ONLY[0]
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        calls=((tool, {"totally_bogus_field": 1}), (NO_SUCH_TOOL, {"totally_bogus_field": 1})),
    )
    denied = outcome.call(tool)
    absent = outcome.call(NO_SUCH_TOOL)
    assert denied.text == f"Unknown tool: {tool}", (
        f"参数非法时越权调用答的是 {denied.text!r} —— 它必须与「不存在」同形，"
        "否则「这个工具存在，只是你还不能调」被答出来了"
    )
    assert _denial_shape(denied.text, tool) == _denial_shape(absent.text, NO_SUCH_TOOL)


# ----------------------------------------------------------------------
# 传输层：认证是前提，且失败关闭
# ----------------------------------------------------------------------
def test_no_token_is_refused_at_the_transport(settings, worker_token) -> None:
    """没有 ``Authorization`` 头，请求在**认证中间件**就被挡下。

    断言的是**状态码**而不是错误文本：客户端 SDK 把「401 被拒」与「服务端 500」
    都摊成同一句 ``MCPError: Server returned an error response``，看那句话
    无法区分「传输层确实拒绝了你」与「服务端炸了」—— 而这两者的排查方向完全
    不同。状态码是这条路径上唯一没有被转述过的事实，所以脚手架把它记了下来。
    """
    ring, _ = worker_token
    outcome = drive_with_settings(settings=settings, tokens=ring, token=None)

    assert outcome.handshake_error, "没有 Authorization 头却握手成功了"
    assert 401 in outcome.statuses, (
        f"期望传输层回 401，实际到达的状态码是 {outcome.statuses}；"
        f"客户端看到的错误是 {outcome.handshake_error!r}"
    )


@pytest.mark.parametrize("token", ["", "not-a-real-token", "0" * 64], ids=["empty", "garbage", "hex"])
def test_an_unknown_token_is_refused(settings, worker_token, token: str) -> None:
    ring, _ = worker_token
    outcome = drive_with_settings(settings=settings, tokens=ring, token=token)
    assert outcome.handshake_error, f"未知令牌 {token!r} 通过了"


def test_without_a_configured_ring_nothing_is_reachable(settings) -> None:
    """没配令牌环时，服务面**什么都不许**，而不是「没配就当开放」。

    这是失败关闭那一半。一个「没配认证就放行」的缺省，会让「忘了配」
    与「故意开放」在行为上不可区分 —— 与 ``BudgetNotConfiguredError``
    拒绝把「未配置」读成「无限」是同一条纪律。
    """
    outcome = drive_with_settings(settings=settings, tokens=None, token=None)

    assert not outcome.handshake_error, "未配认证时握手不该失败 —— 它该成功但什么都不许"
    assert outcome.tool_names == (), f"未配认证却列出了一批工具：{outcome.tool_names}"


def test_without_a_configured_ring_every_call_is_denied(settings) -> None:
    outcome = drive_with_settings(
        settings=settings,
        tokens=None,
        token=None,
        calls=((sorted(TOOL_CATALOG)[0], {}),),
        want_tool_list=False,
    )
    assert not outcome.handshake_error
    assert all(call.is_error for call in outcome.calls)


def test_visible_tools_of_the_wire_matches_the_policy(settings, controller_token) -> None:
    """服务面列出来的那一份，与策略函数算出来的那一份是同一份。

    这条把两半钉在一起：策略对了而服务面没接上，或者服务面自己做了一套
    过滤，都会在这里红。
    """
    from marketops.security.catalog import Principal

    ring, token = controller_token
    outcome = drive_with_settings(settings=settings, tokens=ring, token=token)
    expected = visible_tools(Principal("claude-control", frozenset({Role.CONTROLLER})))
    assert set(outcome.tool_names) == set(expected)

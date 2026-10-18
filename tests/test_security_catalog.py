"""工具目录与角色：谁看得见哪些工具（§7）。

## 这一条约束的是哪件事

§7 逐字要求「认证按角色区分」，并点名反对一种做法：**不要拿模型自己填的
字符串当认证**。那句话的工程含义是 —— 权限的输入必须来自传输层已经确立的
身份，而不是本次调用载荷里的某个字段。

所以本文件与 ``tests/test_mcp_isolation.py`` 分工明确：

* **本文件**测**策略**：给定一个 ``Principal`` 与一个工具名，结论是什么。
  它是纯函数，不需要起服务。
* **另一边**测**执行器**：那条策略是不是真的挂在了每一次调用上，以及
  一个伪造的 ``{"role": "controller"}`` 参数能不能把它绕过去。

只有策略没有执行器，就是一句自觉；只有执行器没有策略，就无法单独回答
「这个角色到底该看见什么」。两半都要。

## 权威在文档，不在代码

目录的**内容**来自目录文档里的表 —— 现在有**三份**：``docs/MCP_SERVICE.md`` 的
§2 / §3（研究服务，字面钉在 ``3744262af7…``）、
``docs/MCP_PRODUCT_LOOP.md`` 的 §2（产品环），以及
``docs/MCP_TARGET_CONVERGENCE.md`` 的 §2（目标架构收敛那一轮）。本文件把**每一份**
读出来，断言它们的**并集**与代码里的目录逐字相等，于是「代码少了一个工具」与
「代码多了一个没人要的工具」都会红。少了会静默降级（调用方拿到工具不存在），
多了会静默开出一条没人评审过的权限面。

## 两条判据而不是一条：并集相等，加各来源自己的阶段词表

把判据从「一张表」放宽成「多张表的并集」时，最容易顺手丢掉的是**每张表自己的
约束**。``MCP_SERVICE.md`` 那些工具的归属阶段只准取自 §21 的 A–I；产品环是那之后
新增的一段能力，用 ``J``；目标架构收敛那一轮同样是**另一段**，用 ``K``。如果只留
一条并集判据，「把 J 写到 ``get_scene`` 上」会静默通过 —— 而它会让人读成「这个
工具在产品环那一轮才落地」。所以除了并集，还有一条按**来源**判阶段的判据。

## 第三份来源与另外两份有一处**刻意的不同**

§2 的行通常在实现同一个 commit 里到（``MCP_PRODUCT_LOOP.md`` §4 逐字）。这一轮
不这样：它按**五条并行工作流共享一份目录**来建，所以名字、角色与阶段字母先冻，
实现随后。未实现的那一行在 ``tools/list`` 上照旧列出、调用落回「Phase K，本构建
尚未接线」—— ``_register`` 的三种结局里它属于第三种。理由写在
``docs/MCP_TARGET_CONVERGENCE.md`` §1。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from marketops.adapters.mcp_server.server import build_server
from marketops.config import REPO_ROOT, Settings
from marketops.domain.errors import AuthorizationError
from marketops.security.catalog import (
    FROZEN_TARGET_CONVERGENCE_TOOLS,
    PHASES,
    PHASES_OF_SERVICE_DOCUMENT,
    TOOL_CATALOG,
    Principal,
    Role,
    authorize,
    roles_from_scopes,
    visible_tools,
)

MCP_SERVICE_DOC = REPO_ROOT / "docs" / "MCP_SERVICE.md"
PRODUCT_LOOP_DOC = REPO_ROOT / "docs" / "MCP_PRODUCT_LOOP.md"
TARGET_CONVERGENCE_DOC = REPO_ROOT / "docs" / "MCP_TARGET_CONVERGENCE.md"

#: 表格首列的 `工具名`。各份文档用的是同一个排版。
_ROW = re.compile(r"^\|\s*`([a-z][a-z0-9_]*)`\s*\|", re.MULTILINE)

#: 每一份目录文档：路径、起始小标题、结束小标题。
#:
#: 两个小标题都是**判据的一部分**，不是便利的常量：``_parse_catalog`` 用
#: ``text.index`` 切分，小标题被改名会当场抛 ``ValueError``，而不是静默返回
#: 空集 —— 后者看起来像「文档与代码不一致」，会把排查方向引到错的地方。
#:
#: 按小标题切分而不是全文扫：文档里别的表格（A–E 验收包）首列也是 ``| **A** |``
#: 这种形状，全文扫会把验收包的字母读成工具名。
_CATALOG_SOURCES: tuple[tuple[Path, str, str], ...] = (
    (MCP_SERVICE_DOC, "## 2. Minimal control catalog", "## 4. Jev/Laya modes"),
    (PRODUCT_LOOP_DOC, "## 2. Product-loop catalog", "## 4. Not yet in this catalog"),
    (
        TARGET_CONVERGENCE_DOC,
        "## 2. Target-convergence catalog",
        "## 4. Not yet in this catalog",
    ),
)


def _parse_catalog(source: tuple[Path, str, str]) -> set[str]:
    """从一份目录文档里读出工具名。"""
    path, start_heading, end_heading = source
    text = path.read_text(encoding="utf-8")
    return set(_ROW.findall(text[text.index(start_heading) : text.index(end_heading)]))


def _documented_tools() -> set[str]:
    """所有目录文档里工具名的**并集**。"""
    return set().union(*(_parse_catalog(source) for source in _CATALOG_SOURCES))


def test_every_catalog_source_still_parses() -> None:
    """每一份文档都要解析出**非空**集合。

    这是并集判据的对照。并集有一个坏掉的来源时会静静变小：那份文档里的工具
    全部落进「只在代码里」，报出来的却是「文档与代码不一致」。这条把故障按在
    它真正发生的地方。
    """
    for source in _CATALOG_SOURCES:
        path, start_heading, end_heading = source
        assert path.is_file(), f"{path} 不存在 —— 这份目录文档被删了或搬了"
        parsed = _parse_catalog(source)
        assert parsed, (
            f"{path} 在 {start_heading!r} 与 {end_heading!r} 之间解析出空集合 —— "
            "正则或排版变了，这份来源已经不参与比对"
        )


def test_the_documented_catalog_and_the_code_agree_exactly() -> None:
    """各份文档的并集与代码里的目录**逐字**相等。

    这是本文件最重要的一条。它同时挡住两种漂移：少一个工具（调用方拿到
    「工具不存在」，而没人知道那是遗漏还是设计），多一个工具（一条没有
    评审过的权限面）。
    """
    documented = _documented_tools()
    assert documented, "文档解析出了空集合 —— 正则或小标题变了，这条判据已经失去意义"
    assert documented == set(TOOL_CATALOG), (
        f"只在文档里：{sorted(documented - set(TOOL_CATALOG))}；"
        f"只在代码里：{sorted(set(TOOL_CATALOG) - documented)}"
    )


@pytest.mark.parametrize("name", sorted(TOOL_CATALOG))
def test_every_tool_declares_roles_phase_and_summary(name: str) -> None:
    spec = TOOL_CATALOG[name]
    assert spec.name == name, "目录的键与 ToolSpec.name 必须一致，否则查表会有两个答案"
    assert spec.roles, f"{name} 没有任何角色 —— 一个谁都调不了的工具不该在目录里"
    assert spec.phase in PHASES, f"{name} 的归属阶段 {spec.phase!r} 不在阶段词表里"
    assert spec.summary.strip(), f"{name} 缺一句用途说明"


def test_the_target_convergence_round_committed_exactly_these_tools() -> None:
    """``K`` 那一族**不多不少**就是冻结清单里那 31 条，且它们只在第三份文档里。

    并集判据只问「文档与代码相等」，不问「这一轮承诺了哪几条」。于是五条并行
    工作流里有人顺手多注册一条（比如给自己开一个 ``get_watch``），只要它把自己的
    行写进文档，并集照样相等 —— **没有任何一处**会问那条工具该不该存在。

    三个方向分开报，因为修法完全不同：

    * 代码里多一条 ``K`` → 要么改 ``FROZEN_TARGET_CONVERGENCE_TOOLS``（那是一次
      要说理由的编辑），要么别加；两边同时加就等于没冻。
    * 代码里少一条冻结过的 ``K`` → 少了一条已经写进目录文档的承诺。
    * 第三份文档的行与冻结清单不相等 → 冻结搬了但文档没跟上（或反过来）。
    """
    in_code = {name for name, spec in TOOL_CATALOG.items() if spec.phase == "K"}
    assert in_code == FROZEN_TARGET_CONVERGENCE_TOOLS, (
        f"代码里多出来的 K 工具：{sorted(in_code - FROZEN_TARGET_CONVERGENCE_TOOLS)}；"
        f"冻结了却没在代码里：{sorted(FROZEN_TARGET_CONVERGENCE_TOOLS - in_code)}"
    )
    documented = _parse_catalog(_CATALOG_SOURCES[2])
    assert documented == FROZEN_TARGET_CONVERGENCE_TOOLS, (
        f"第三份文档多写的：{sorted(documented - FROZEN_TARGET_CONVERGENCE_TOOLS)}；"
        f"冻结了却没写进文档的：{sorted(FROZEN_TARGET_CONVERGENCE_TOOLS - documented)}"
    )
    assert not (in_code & _parse_catalog(_CATALOG_SOURCES[1])), (
        "同一条工具同时出现在产品环与目标收敛两份文档里 —— 一份工具只能属于一轮，"
        "两处都写会让「它什么时候有的」有两个答案"
    )


def test_the_service_document_tools_use_only_the_section_21_phases() -> None:
    """``MCP_SERVICE.md`` 来源的工具，阶段只准取自 §21 那九个字母。

    并集判据放开的是「工具住在哪份文档」，不是「每份文档里的工具可以随便标阶段」。
    产品环的 ``J`` 一旦挪到 §2/§3 的任何一条上，这条就红：``J`` 是产品环那一轮的
    字母，标在研究服务工具上会让人读成「它也是那时候才有的」。
    """
    service_tools = _parse_catalog(_CATALOG_SOURCES[0])
    assert service_tools, "第一条来源永远是 MCP_SERVICE.md，空集合说明上面那条判据已经失守"
    for name in sorted(service_tools):
        phase = TOOL_CATALOG[name].phase
        assert phase in PHASES_OF_SERVICE_DOCUMENT, (
            f"{name} 来自 docs/MCP_SERVICE.md，却标着阶段 {phase!r}；"
            f"那一份文档的工具只准用 {sorted(PHASES_OF_SERVICE_DOCUMENT)}"
        )
    assert PHASES_OF_SERVICE_DOCUMENT < PHASES, (
        "阶段词表必须比 §21 严格更宽 —— 否则上一条判据是一条永真式"
    )


def test_the_catalog_is_immutable() -> None:
    """目录在运行期不可改。

    「谁能调什么」若是运行期可写的一个 dict，那么将来任何一处「临时放行」
    都能改掉全局权限，而且不留痕。冻结它，让那件事必须走代码评审。
    """
    with pytest.raises(TypeError):
        TOOL_CATALOG["evil_tool"] = TOOL_CATALOG["get_scene"]  # type: ignore[index]


# ----------------------------------------------------------------------
# 策略：给定主体与工具名，结论是什么。
#
# 期望值是**从目录里推出来的**，不是手抄的 —— 手抄的清单会在目录第一次
# 变动时变成一份错的期望，而错的红灯比没有红灯更费时间。
# ----------------------------------------------------------------------
_CONTROLLER_ONLY = tuple(
    sorted(n for n, s in TOOL_CATALOG.items() if s.roles == frozenset({Role.CONTROLLER}))
)
_RESEARCHER_ONLY = tuple(
    sorted(n for n, s in TOOL_CATALOG.items() if s.roles == frozenset({Role.RESEARCHER}))
)
_AUTHORIZER_ONLY = tuple(
    sorted(n for n, s in TOOL_CATALOG.items() if s.roles == frozenset({Role.AUTHORIZER}))
)
#: 控制面与研究面**共有**的那些。这个集合按**确切的角色对**推导，不按「角色数
#: 大于一」—— 后者会把下面那一族并进来，而那条判据断言的正是「研究侧也够得着」。
#: 「多角色」不是一件事，是两件：控制/研究共有，与控制/授权共有。合成一条
#: 「len(roles) > 1」，会让收件箱那两条被要求对研究侧开放 —— 而那是一条**相反**
#: 的证据。
_SHARED = tuple(
    sorted(
        n
        for n, s in TOOL_CATALOG.items()
        if s.roles == frozenset({Role.CONTROLLER, Role.RESEARCHER})
    )
)
#: 控制端与授权人**共有**的那些（今天是收件箱的两条读）。
_OPERATOR_SHARED = tuple(
    sorted(
        n
        for n, s in TOOL_CATALOG.items()
        if s.roles == frozenset({Role.CONTROLLER, Role.AUTHORIZER})
    )
)


def test_both_catalogs_have_members_that_are_not_the_other() -> None:
    """两侧都必须有**不是**对方的成员。

    否则「按角色隔离」是一条永真式：所有工具两侧都看得见，判据照样全绿。
    这正是本仓反复要求的那种对照 —— 判据必须有可能失败。
    """
    assert _CONTROLLER_ONLY, "没有任何 control 专属工具 —— 隔离判据会是空的"
    assert _RESEARCHER_ONLY, "没有任何 research 专属工具 —— 隔离判据会是空的"
    assert set(_CONTROLLER_ONLY).isdisjoint(_RESEARCHER_ONLY)


def test_every_role_reaches_at_least_one_tool() -> None:
    """每个角色都得有**至少一个**它能调的工具。

    这条挡的是第三种腐烂：一个角色在词表里，却没有任何工具归它。那时
    「按角色隔离」在全仓的判据里全都成立，而``authorizer`` 这个角色是一句
    自觉 —— 它挡不住任何东西，因为没有任何一扇门写着它的名字。按 ``Role``
    推导而不是手抄：新加一个角色时，这条当场红。
    """
    for role in Role:
        reachable = [n for n, s in TOOL_CATALOG.items() if role in s.roles]
        assert reachable, (
            f"角色 {role.value!r} 一个工具都调不到 —— 它现在只是一句自觉。"
            f"要么给它归属的工具，要么把它从 Role 里去掉。"
        )


def test_the_two_halves_of_the_outreach_gate_are_two_roles() -> None:
    """§6 的「独立」落在角色上：放行的那一半与执行的那一半**不是同一组角色**。

    这条判的是两个**点名**的工具，与上面那些按目录推导的判据取向相反，理由
    写在这里：这两个名字是 §6 那句话本身（`authorize_wave` 是「谁批准」，
    `send_wave` 是「谁发」），而「独立」这件事恰恰不能从目录里推导出来 ——
    从目录里能推出来的只是「它们此刻的角色集合不一样」，把一个工具改成
    ``_BOTH`` 之后这句话照样成立。所以要有一条盯住这两个名字的判据。

    另一半（同一个主体不许同时握两半）在 ``tokens.py`` 的装载期判，那里才是
    能拒绝的地方 —— 这一层只是纯函数。
    """
    approving = TOOL_CATALOG["authorize_wave"].roles
    sending = TOOL_CATALOG["send_wave"].roles
    assert approving, "authorize_wave 没有角色 —— 那一格是空的，判据会永真"
    assert sending, "send_wave 没有角色 —— 那一格是空的，判据会永真"
    assert approving.isdisjoint(sending), (
        f"授权的角色 {sorted(r.value for r in approving)} 与投递的角色 "
        f"{sorted(r.value for r in sending)} 有交集。\n"
        "§6 要求授权来自**独立**的责任人：一个同时能批准与投递的主体，把"
        "「谁批准了」与「谁准备并发出去了」合成同一个答案，而那正是这句话"
        "要分开的东西。"
    )


@pytest.mark.parametrize("name", _AUTHORIZER_ONLY)
def test_only_the_authorizer_may_register_an_authorization(name: str) -> None:
    """授权人专属的工具，代理的两个角色都调不动。

    这是「模型写下『我批准』不产生授权」在**权限层**的那一半：代理持有的
    两个角色都到不了那个工具，所以它能做的最多是在正文里写一句话 —— 而那句
    话不进授权记录。
    """
    for role in (Role.CONTROLLER, Role.RESEARCHER):
        with pytest.raises(AuthorizationError):
            authorize(Principal(role.value, frozenset({role})), name)


@pytest.mark.parametrize("name", list(TOOL_CATALOG))
def test_nobody_without_a_principal_may_call_anything(name: str) -> None:
    """没有主体 ⇒ 什么都不许。

    这是**失败关闭**的那一半。传输层没配认证、或令牌没带任何已知 scope 时，
    结论必须是全拒，而不是「没配就当管理员」。
    """
    with pytest.raises(AuthorizationError):
        authorize(None, name)


@pytest.mark.parametrize("name", _RESEARCHER_ONLY)
def test_controller_cannot_reach_researcher_only_tools(name: str) -> None:
    with pytest.raises(AuthorizationError):
        authorize(Principal("claude-control", frozenset({Role.CONTROLLER})), name)


@pytest.mark.parametrize("name", _CONTROLLER_ONLY)
def test_researcher_cannot_reach_controller_only_tools(name: str) -> None:
    with pytest.raises(AuthorizationError):
        authorize(Principal("worker-1", frozenset({Role.RESEARCHER})), name)


@pytest.mark.parametrize("name", _CONTROLLER_ONLY)
def test_controller_reaches_its_own_tools(name: str) -> None:
    spec = authorize(Principal("claude-control", frozenset({Role.CONTROLLER})), name)
    assert spec.name == name


@pytest.mark.parametrize("name", _RESEARCHER_ONLY)
def test_researcher_reaches_its_own_tools(name: str) -> None:
    spec = authorize(Principal("worker-1", frozenset({Role.RESEARCHER})), name)
    assert spec.name == name


@pytest.mark.parametrize("name", _SHARED)
def test_shared_tools_are_reachable_from_both(name: str) -> None:
    authorize(Principal("claude-control", frozenset({Role.CONTROLLER})), name)
    authorize(Principal("worker-1", frozenset({Role.RESEARCHER})), name)


@pytest.mark.parametrize("name", _OPERATOR_SHARED)
def test_the_inbox_is_readable_by_controller_and_authorizer_only(name: str) -> None:
    """待办的**读**两侧都够得着，而研究侧一个都够不着。

    授权者读得到那一件待办是承重的：他要批准一条规则变更，就得读得到那一件是
    拿什么判的、影响预览是什么、谁提的。读不到而能批准，「批准」就退化成盖章 ——
    §9 那句「**一个人**，而不是一个代理」在那条路上只剩一个签名动作。

    研究侧读不到则是 §6 的隔离规则本身：待办里住着证据引用与受影响对象的快照，
    那是控制面的事实，与研究侧的可见面无关。
    """
    for role in (Role.CONTROLLER, Role.AUTHORIZER):
        spec = authorize(Principal(role.value, frozenset({role})), name)
        assert spec.name == name
    with pytest.raises(AuthorizationError):
        authorize(Principal("worker-1", frozenset({Role.RESEARCHER})), name)
    with pytest.raises(AuthorizationError):
        authorize(None, name)


def test_an_unknown_tool_is_refused_not_ignored() -> None:
    """不在目录里的名字必须**报错**，不是放行。

    这一条是「判据真的在判」的对照：若 ``authorize`` 对未知名字返回成功，
    上面所有按目录推出来的期望都会同时成立，而实现可以让任何工具过关。
    """
    controller = Principal("claude-control", frozenset({Role.CONTROLLER}))
    with pytest.raises(AuthorizationError):
        authorize(controller, "definitely_not_a_tool")


def test_a_handler_named_outside_the_catalog_is_refused_at_construction(
    settings: Settings,
) -> None:
    """目录外的工具名是**构造期的拒绝**，不是被安静地忽略。

    这一条是上一条在**另一个方向**上的对照。上一条说的是「调用一个不存在的东西
    要报错」；这一条说的是「**接**一个不存在的东西也要报错」，而在这一轮它才
    第一次真的会伤人：五条并行工作流各交一份 ``*_handlers`` 表，名字是拼出来的。

    为什么安静地忽略会伤人，值得写下来 —— 它不是「少了一条工具」那么轻：

    * ``_register`` 只走 ``TOOL_CATALOG``，所以那个名字既不会有实现、也不会有
      stub，``tools/list`` 上根本不出现；
    * 而 ``build_server`` 那边 ``RoleGuard`` 的 ``backed`` 拿的是 ``handlers`` 的
      **键**，于是同一个名字在判定中间件眼里是「有实现」；
    * 合成起来的形态是：调用方**过了**权限那一道，然后收到协议层的一句
      ``Unknown tool`` —— 不是这一层写的任何一种回答，而且它看起来像协议问题。

    所以构造期就拒，且消息里点出两处可改的地方。``security/catalog.py`` 那一处
    是这条路唯一的**加一行就通过**的口子，所以它更要被点名：补目录是一次对外
    承诺（``docs/MCP_TARGET_CONVERGENCE.md`` §2），不只是让这条错误消失。
    """
    with pytest.raises(ValueError) as caught:
        build_server(
            settings=settings,
            tokens=None,
            research={"definitely_not_a_tool": lambda **kwargs: {}},
        )

    message = str(caught.value)
    assert "definitely_not_a_tool" in message
    assert "security/catalog.py" in message


def test_roles_are_a_frozen_set_so_grants_cannot_grow() -> None:
    """主体持有的角色不可在运行期追加。"""
    principal = Principal("worker-1", frozenset({Role.RESEARCHER}))
    assert isinstance(principal.roles, frozenset)
    with pytest.raises(AttributeError):
        principal.roles = frozenset({Role.CONTROLLER})  # type: ignore[misc]


# ----------------------------------------------------------------------
# 可见性：``tools/list`` 的那一半隔离。
#
# 调用被拒与**看不见**是两件事。只做前者时，研究侧 worker 仍然能列出
# control 目录 —— 那既是一次信息泄漏（它知道了这套服务能做什么），
# 也是一条把「未知工具」与「不属于你的工具」区分开的旁路。
# ----------------------------------------------------------------------
def test_visible_tools_is_exactly_the_authorized_subset() -> None:
    """遍历**整个** ``Role``，不是点名两个。

    点名两个的写法在加第三个角色时静默缩小：新角色在 ``tools/list`` 上看得见
    什么，没有一条判据在问 —— 而「看不见」与「调不动」是两件必须都成立的事
    （见下面 ``visible_tools`` 的 docstring）。授权人因此在这一条里被覆盖到：
    它只看得到那一个工具，而不是整个控制目录。
    """
    for role in Role:
        principal = Principal(role.value, frozenset({role}))
        expected = {n for n, s in TOOL_CATALOG.items() if role in s.roles}
        assert set(visible_tools(principal)) == expected


def test_visible_tools_is_deterministic() -> None:
    """顺序必须稳定：目录是 dict，而 dict 的迭代顺序是插入顺序 ——
    插入顺序会随一次无关的改动而变，于是工具列表在两次调用之间抖动。"""
    controller = Principal("claude-control", frozenset({Role.CONTROLLER}))
    assert list(visible_tools(controller)) == sorted(visible_tools(controller))


def test_no_principal_sees_nothing() -> None:
    assert visible_tools(None) == ()


# ----------------------------------------------------------------------
# scope ↔ 角色：传输层送进来的东西怎么变成角色。
# ----------------------------------------------------------------------
def test_scope_round_trip() -> None:
    for role in Role:
        assert roles_from_scopes([role.scope]) == frozenset({role})


def test_unknown_scopes_grant_nothing() -> None:
    """不认识的 scope 不报错，只是不给任何角色。

    这是一处刻意的失败关闭：令牌里写错一个 scope 的结果是**什么都调不动**
    （当场可见），而不是「照旧拥有原来的权限」（事后才发现）。
    令牌文件里的角色名拼错是另一件事，那一层会直接拒绝装载 —— 见
    ``tests/test_security_tokens.py``。
    """
    assert roles_from_scopes(["marketops:typo", "unrelated:scope"]) == frozenset()


def test_scopes_are_namespaced() -> None:
    """scope 带命名空间，免得与将来别的服务的 scope 撞名后互相授权。"""
    for role in Role:
        assert role.scope.startswith("marketops:")


def test_role_set_is_closed() -> None:
    """角色是闭集，而**加一个成员要改这一行**。

    这一行是刻意手抄的：它逼着「加一个角色」这件事在 diff 里被看见一次。
    按 ``Role`` 推导这条判据会让它永真，而永真的判据挡不住「不小心把
    ``authorizer`` 并进 controller」那类改动。
    """
    assert {r.value for r in Role} == {"controller", "researcher", "authorizer"}


@pytest.mark.parametrize(
    "source", _CATALOG_SOURCES, ids=lambda s: str(s[0].name) + ":" + s[1]
)
def test_the_doc_check_would_notice_a_renamed_heading(
    source: tuple[Path, str, str],
) -> None:
    """对照：小标题被改名时 ``_parse_catalog`` 必须炸，而不是静默返回空集。

    这条保护的是上面那条同义反复的风险 —— 一个静默返回空集的解析器会让
    ``documented == set(TOOL_CATALOG)`` 变成 ``set() == {...}`` 之外的
    另一种失败方式，而它看起来像「文档与代码不一致」，误导排查方向。
    """
    path, start_heading, end_heading = source
    text = path.read_text(encoding="utf-8")
    assert start_heading in text
    assert end_heading in text
    assert Path(path).exists()

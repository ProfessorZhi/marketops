"""工作区那一维真的挂在每一次调用上了吗（§6.1 / §7）。

``tests/test_workspace.py`` 证明**判定**是对的。``tests/test_mcp_isolation.py``
证明**角色**那一维挂在传输层确立的身份上。本文件证明**范围**那一维也挂着 ——
而且挂的是同一根线（``AccessToken.claims`` → ``Principal.workspaces`` →
``RoleGuard``），不是自己另走一条。

## 为什么这一维必须有执行器

``docs/MCP_SERVICE.md`` §6.1 逐字：「Role checks alone do not establish
multi-tenant object isolation」。一条铸给 A 场景、带着 researcher 角色的令牌，
在 B 场景的部署上角色判定与 A 的一模一样 —— 于是它照常被放行，而环文件上看不出
区别、日志里也看不出。判定写对了而没接上，行为与没写完全一样。

## 承重的五条断言

1. **错工作区的令牌在每一个工具上都被拒，且是同**一句**话。** 一次枚举既不能
   通过「哪个名字被拒得不一样」把目录读出来，也不能通过「哪个名字答的是别的
   话」把「哪些工具接线了」读出来。
2. **同一个令牌换成对的工作区就能用。** 少了这一条，一个把所有调用都拒掉的
   实现（比如 ``workspaces`` 压根没从 claims 传下来）会全绿 —— 而那正是本维
   最可能的坏法。
3. **拒绝发生在应用里，不在传输层。** 状态码里没有 401、握手成功。这一维不是
   认证；把它做成认证会让「令牌铸给了别的场景」表现为「令牌无效」，而处置
   （重铸一条名字对的）与「令牌过期了」完全不同。
4. **顺序是角色 → 范围 → 背书。** 两个方向都要钉：既没有角色又没有范围的令牌
   拿到的是「不存在」（它的处置是没有这个角色），而错工作区的令牌问一个**没接线**
   的工具得到的是范围那句话，不是「还没实现」。
5. **对象那一维：一行别的场景的作业读不回来，也改不动，也结算不了。** 后两条更
   要紧 —— ``control_job`` 与 ``submit_findings`` 都是**写**：一个跨越场景的
   pause / cancel 会真的改掉一个不属于本部署的作业的状态，一个跨越场景的
   ``settle`` 会真的把一份产出落进那份作业里，而两张回执上与一次正常的控制 /
   结算完全同形。第三条尤其是**这一维最后一个被堵上的口子**：它读的是 work item，
   而 ``WorkItem`` 上没有 ``scene_id``（Scene 是作业的，不是工作单元的），所以
   它必须顺着 ``job_id`` 走一步才问得出来 —— 少走这一步，前两条判据全绿而这条
   路径照样写得进去。

## 工作区 = Scene，值就是 profile id

``RING_WORKSPACE``（``tests/mcp_harness.py``）取的就是 ``conftest.DEFAULT_PROFILE``
—— 与 ``settings`` fixture 喂给 ``Settings(profile=...)`` 的是同一个字符串。
两处各写一遍的话，一次「测试铸的工作区与服务端服务的 Scene 不一样」会让整个文件
变成「明明配了令牌却被拒」，而两条路径的报错长得很像。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketops.adapters.profile_loader import load_profile
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.config import Settings
from marketops.domain.enums import JobInputKind, JobState
from marketops.domain.util import sha256_text
from marketops.security.catalog import TOOL_CATALOG, Role
from marketops.security.tokens import TokenRing, mint
# ``_denial_shape`` 从隔离测试那边借过来，不再写第二份：两个文件做的是同一件事
# （把调用方自己发来的工具名从回答里抹掉，让「逐字相同」落在形状上），而两份
# 实现漂移的那天，「可区分性」这个判据会在两个文件里给出两个结论。
from tests.test_mcp_isolation import _denial_shape
# 证据构造函数同样借：写第二份的那天，两处对「一条解得开的证据长什么样」会有
# 两个说法，而这条判据要的恰恰是一条**本来会被受理**的证据。
from tests.test_evidence_store_contract import make_evidence
from tests.fakes import synthetic_dossiers
from tests.mcp_harness import RING_WORKSPACE, drive_with_settings, ring_entry, ring_payload

#: 本部署的 Scene（= 工作区）。取 ``mcp_harness`` 的那个常量，不重写字符串。
DEPLOYED = RING_WORKSPACE

#: 另一个工作区。**不是** "uk-second-scene" 那类看起来像真 profile 的名字：
#: 这个文件测的是判定，而真的市场名只应出现在 ``profiles/`` 里。
OTHER = "some-other-scene"

#: 一个一定不存在的工具名 —— 与隔离测试用的是同一个形状（「不存在」的标准形态）。
NO_SUCH_TOOL = "no_such_tool_xyzzy"

#: 本文件里那条范围判据用的主体角色。**不是**「目录需要的全部角色」—— 见下。
_OPERATOR_ROLES = frozenset({Role.CONTROLLER, Role.RESEARCHER})

#: 范围判据问得到的那些工具：**这个主体有权调的**每一个。
#:
#: 从前这里是 ``sorted(TOOL_CATALOG)``（那时 ``both_roles`` 恰好覆盖了整份目录）。
#: 目录里出现第三个角色之后那句话不再成立，而这条判据的**语义**从来没变过：
#: 「一条错工作区的令牌，在被授权的每一个工具上收到同一种回答」。没被授权的那些
#: 工具会先在角色那一关被拒（``guard.py`` 的顺序：先角色、再工作区、最后背书），
#: 那正是角色隔离该有的回答，把它们的形状混进来只会让这条判据变成「两种形状」，
#: 然后被误读成泄漏。
_ALL_TOOLS = tuple(
    sorted(name for name, spec in TOOL_CATALOG.items() if spec.roles & _OPERATOR_ROLES)
)

#: 本文件里唯一一个被要求「接线了没有」的工具：它只属于研究侧，而下面的
#: ``drive_with_settings`` 不传 ``research`` —— 于是它是**没接线**的那一类。
#: 拿它来钉「范围判定发生在背书检查之前」。
_UNBACKED = "search_web"

#: 铸令牌时用的主体名。**提到模块一级**，因为对象那一维的写路径判据要把一条
#: 租约租给这个名字 —— 两处各写一遍的话，一次笔误会让那条判据以「租约不属于你」
#: 的方式红（``_require_holder`` 先拒），看起来像 Scene 判定坏了。
OPERATOR = "op-1"


def _token_for(
    path: Path, *, principal: str, roles: list[Role], workspaces: object
) -> tuple[TokenRing, str]:
    """铸一条令牌并写一个只含它的环，返回（环，明文令牌）。

    每次调用都新铸一条：令牌的明文只在铸造时出现一次，盘上只有摘要 —— 所以
    「同一个主体、两个工作区」必须铸两次，而不是拿同一条令牌改环（改不动）。
    """
    token, digest = mint(principal, roles)
    path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, principal, roles, workspaces=workspaces))
        ),
        encoding="utf-8",
    )
    return TokenRing.from_file(path), token


@pytest.fixture
def both_roles(tmp_path_factory) -> list[Role]:
    return [Role.CONTROLLER, Role.RESEARCHER]


@pytest.fixture
def wrong_workspace_token(tmp_path_factory, both_roles) -> tuple[TokenRing, str]:
    """两个角色都有、工作区**不对**的一条令牌。

    两个角色是刻意的：这样每一条工具都会走到范围那一关，于是「每一条工具"这个
    断言是**十五乘以十五**里的全部十五，而不是「恰好属于某一个角色的那几条」。
    """
    return _token_for(
        tmp_path_factory.mktemp("ws-wrong") / "tokens.json",
        principal=OPERATOR,
        roles=list(both_roles),
        workspaces=(OTHER,),
    )


@pytest.fixture
def right_workspace_token(tmp_path_factory, both_roles) -> tuple[TokenRing, str]:
    """同一个主体、同样的角色，工作区是**对**的那一个。"""
    return _token_for(
        tmp_path_factory.mktemp("ws-right") / "tokens.json",
        principal=OPERATOR,
        roles=list(both_roles),
        workspaces=(DEPLOYED,),
    )


@pytest.fixture
def researcher_only_token(tmp_path_factory) -> tuple[TokenRing, str]:
    """只有研究侧角色、工作区**不对**的一条令牌。用来钉角色在范围之前。"""
    return _token_for(
        tmp_path_factory.mktemp("ws-role") / "tokens.json",
        principal="worker-1",
        roles=[Role.RESEARCHER],
        workspaces=(OTHER,),
    )


# ----------------------------------------------------------------------
# 一、每一个工具名拿到同一句话
# ----------------------------------------------------------------------
def test_a_token_for_another_workspace_is_refused_on_every_tool(
    settings: Settings, wrong_workspace_token
) -> None:
    """这个主体**有权调的每一个**工具，全部被拒，且**形状只有一种**。

    形状只有一种，是因为错工作区的令牌在调用面上什么都不该枚举出来 —— 包括
    「哪些工具接线了」（见第四条断言那一条用例）。名字本身是调用方自己发来的，
    所以可区分性落在**形状**上，与隔离测试同一条判据。

    参数化的是 ``_ALL_TOOLS``（这个主体有权调的目录名字），不是随便一串字符串：
    不在目录里的名字、以及**不属于这个主体**的名字，都在角色那一关就被拒了
    （见下一条用例），而那是角色隔离既有的、写在文档里的行为 —— 把它混进来只会
    让这条判据变成「两种形状」，然后被误读成泄漏。
    """
    ring, token = wrong_workspace_token
    calls = tuple((name, {}) for name in _ALL_TOOLS)
    outcome = drive_with_settings(settings=settings, tokens=ring, token=token, calls=calls)

    assert not outcome.handshake_error, (
        f"错工作区的令牌把整条会话打断了 —— 这一维不该动传输层：{outcome.handshake_error}"
    )
    assert all(call.is_error for call in outcome.calls), (
        "错工作区的令牌在这些工具上过了关："
        f"{[c.name for c in outcome.calls if not c.is_error]}"
    )
    shapes = {_denial_shape(call.text, call.name) for call in outcome.calls}
    assert len(shapes) == 1, (
        "错工作区的令牌对不同工具名给出了不同的回答 —— 差别本身就是一次可枚举的"
        f"事实。收到的形状有 {len(shapes)} 种：\n" + "\n".join(sorted(shapes))
    )


def test_the_authorizers_own_tool_is_refused_the_same_way(
    settings: Settings, tmp_path: Path, wrong_workspace_token
) -> None:
    """授权人的工具也在**同一个形状**里被拒 —— 换一个角色，形状不换。

    上一条的参数化集合是「这个主体有权调的」，而这一句话若没人守着，它会在目录
    长出一个新角色的工具时**静默缩小**：新工具既不在集合里、也没有任何判据问它
    的范围行为，于是「范围这一维对每一条工具生效」在报告上仍然成立，而实际上
    少了一条。

    这一条就是那个缺口的封口，且比对的是一个**算出来的**对照：同一个部署里，
    controller + researcher 的令牌（工作区同样不对）在它自己的工具上收到的那一
    种形状。两个角色的被拒形状必须逐字相同 —— 形状是这一维唯一的答案，而
    「不同角色不同措辞」就是一个可枚举的事实。

    对照之所以能逐字比，是因为这里两条令牌用了**同一个主体名**：被拒的话里
    带着主体名（那句话是给写环的人看的），而这一条判据要判的是**措辞**，不是名字。
    两个环文件各自只装一条令牌，所以「一个主体一个角色集」那条装载规则不受影响。
    """
    token, digest = mint(OPERATOR, [Role.AUTHORIZER])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, OPERATOR, ["authorizer"], workspaces=(OTHER,)))
        ),
        encoding="utf-8",
    )
    ring = TokenRing.from_file(ring_path)
    authorizer_only = tuple(
        sorted(
            name
            for name, spec in TOOL_CATALOG.items()
            if spec.roles == frozenset({Role.AUTHORIZER})
        )
    )
    assert authorizer_only, "授权人一个工具都调不到 —— 这条判据是空转的"

    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        calls=tuple((name, {}) for name in authorizer_only),
    )

    assert not outcome.handshake_error, outcome.handshake_error
    shapes = {_denial_shape(call.text, call.name) for call in outcome.calls}
    assert len(shapes) == 1, f"授权人的工具给出了不同的形状：{sorted(shapes)}"

    other_ring, other_token = wrong_workspace_token
    contrast = drive_with_settings(
        settings=settings,
        tokens=other_ring,
        token=other_token,
        calls=tuple((name, {}) for name in _ALL_TOOLS),
    )
    assert {_denial_shape(call.text, call.name) for call in contrast.calls} == shapes, (
        "两个角色在错工作区下收到的形状不一样。\n"
        "范围这一维的回答只有一个形状（见模块 docstring），而按角色分家的措辞就是"
        "一条能把「这个工具属于谁」枚举出来的旁路。"
    )


def test_a_name_outside_the_catalog_is_still_the_unknown_tool_answer(
    settings: Settings, wrong_workspace_token
) -> None:
    """范围那一维只对**目录里**的名字生效 —— 不在目录里的名字答「不存在」。

    这不是一处遗漏，是一次**没有收益**的扩张：那条回答已经由角色那一关给出了
    （不在任何角色的目录里 = 不存在），而目录本身是公开文档。把范围判定提到
    它前面会改变的是「谁学到什么」以外的另一件事 —— 它会让一次拼写错误
    （调用方想调 ``get_job`` 却写成了 ``get_jobb``）收到一条关于环的报错，
    于是排查方向从「名字拼错了」被引到「令牌铸错了场景」。

    这一条与上面那条一起把范围判定的**作用域**钉住：它管的是目录里的名字。
    """
    ring, token = wrong_workspace_token
    outcome = drive_with_settings(
        settings=settings, tokens=ring, token=token, calls=((NO_SUCH_TOOL, {}),), want_tool_list=False
    )
    assert outcome.call(NO_SUCH_TOOL).text == f"Unknown tool: {NO_SUCH_TOOL}", (
        "不在目录里的名字拿到了范围那句话 —— 一次拼写错误会被读成一次铸造错误"
    )


def test_the_workspace_refusal_is_not_the_unknown_tool_answer(
    settings: Settings, wrong_workspace_token
) -> None:
    """它**必须**与「不存在」那一句不同 —— 这是那条规则唯一的例外，要把理由钉住。

    隔离测试要求「越权」与「不存在」逐字相同，理由是防枚举。范围这一维不满足
    那条规则的前提：会走到这里的调用方**已经是一条有效的令牌**（它过了受众与
    有效期，否则在传输层就 401 了），而目录本身写在 ``docs/MCP_SERVICE.md`` 里。
    合成一句 ``Unknown tool`` 挡不住任何人，只会让一次**铸造错误**表现为
    「这些工具都不存在」—— 排查方向被引到「调用方拼错了名字」，而真正的处置在环上。
    """
    ring, token = wrong_workspace_token
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        calls=(("get_scene", {}),),
        want_tool_list=False,
    )
    text = outcome.call("get_scene").text

    assert text != "Unknown tool: get_scene", (
        "范围拒绝被说成了「不存在」—— 一次铸造错误会被读成一次拼写错误"
    )
    assert "没有名为" not in text, "范围拒绝与场景拒绝被说成了同一件事"
    assert "--check" in text, f"拒绝里没有给出诊断的出路：{text!r}"


def test_the_workspace_refusal_does_not_report_the_deployed_scene(
    settings: Settings, wrong_workspace_token
) -> None:
    """拒绝的那句话里**没有本部署的 Scene**。

    与受众错配同一个取向（``adapters/mcp_server/auth.py``）：一条不属于这里的
    令牌，不该从拒绝里学到这里的名字。诊断走**已认证的**表面 —— ``--check``
    的 ``mcp.workspaces`` 报环里实际有哪些工作区。

    「处理得清楚一点」的诱惑在这里是具体的：把本部署的名字写进报错确实更好排查，
    而那正是它不该在里面的理由。
    """
    ring, token = wrong_workspace_token
    outcome = drive_with_settings(
        settings=settings, tokens=ring, token=token, calls=(("get_scene", {}),), want_tool_list=False
    )
    assert DEPLOYED not in outcome.call("get_scene").text, (
        "拒绝里报出了本部署的 Scene —— 一条为别的场景铸的令牌因此学到了这里的名字"
    )


# ----------------------------------------------------------------------
# 二、换对工作区就能用（否则「全拒」也会让上面三条全绿）
# ----------------------------------------------------------------------
def test_the_same_token_shape_with_the_right_workspace_works(
    settings: Settings, right_workspace_token
) -> None:
    """同一个主体、同样的两个角色，只把工作区换成对的那一个 —— 调用成功。

    这是本文件里唯一一条**正面**判据，而它承重：没有它，一个 ``workspaces``
    根本没从 claims 传到 ``Principal`` 上的实现（``_workspaces_from`` 永远返回
    空集）会让上面每一条都通过。
    """
    ring, token = right_workspace_token
    outcome = drive_with_settings(
        settings=settings, tokens=ring, token=token, calls=(("get_capabilities", {}),)
    )

    assert not outcome.handshake_error, outcome.handshake_error
    result = outcome.call("get_capabilities")
    assert result.is_error is False, (
        "工作区配对了仍然被拒 —— 判定读的不是这条令牌的工作区："
        f"{result.text}"
    )


def test_a_second_workspace_on_the_same_token_also_works(
    settings: Settings, tmp_path_factory
) -> None:
    """一条令牌可以带多个工作区，而**其中之一**对上就够。

    ``--workspace`` 是可重复的，所以「多带一个」是一种正常的铸造形态。若判定
    写成「集合相等」而不是「成员」，这种令牌会全部被拒 —— 而它的报错与
    「铸错了」一模一样。
    """
    ring, token = _token_for(
        tmp_path_factory.mktemp("ws-multi") / "tokens.json",
        principal="op-1",
        roles=[Role.CONTROLLER],
        workspaces=(OTHER, DEPLOYED),
    )
    outcome = drive_with_settings(
        settings=settings, tokens=ring, token=token, calls=(("get_capabilities", {}),)
    )
    assert outcome.call("get_capabilities").is_error is False


# ----------------------------------------------------------------------
# 三、这一维不是认证
# ----------------------------------------------------------------------
def test_the_refusal_happens_in_the_application_not_at_the_transport(
    settings: Settings, wrong_workspace_token
) -> None:
    """握手成功、``tools/list`` 照常、状态码里没有 401。

    把它做成认证（401）会让「令牌铸给了别的场景」表现为「令牌无效」—— 而处置
    （重铸一条名字对的）与「令牌过期了 / 打错了」完全不同。认证与授权是两层，
    这里的报错要能指向环上的那一格。
    """
    ring, token = wrong_workspace_token
    outcome = drive_with_settings(
        settings=settings, tokens=ring, token=token, calls=(("get_scene", {}),)
    )

    assert not outcome.handshake_error, outcome.handshake_error
    assert 401 not in outcome.statuses, (
        f"范围拒绝走了传输层：到达的状态码是 {outcome.statuses}"
    )


def test_the_tool_list_is_not_filtered_by_workspace(
    settings: Settings, wrong_workspace_token, right_workspace_token
) -> None:
    """``tools/list`` 按**角色**筛，不按工作区筛 —— 两边列出来的是同一份。

    这是刻意的，不是漏了。按工作区筛会把这次失败表现成「这些工具不存在」，也就是
    上面那条用例刚刚拒绝过的那种混淆；而它买不到任何东西：目录写在
    ``docs/MCP_SERVICE.md`` 里，且每一次调用的回答都已经是同一句话。多一处过滤
    就多一处会漂移的实现，而它漂移的那天没有任何东西会红。
    """
    w_ring, w_token = wrong_workspace_token
    r_ring, r_token = right_workspace_token
    wrong = drive_with_settings(settings=settings, tokens=w_ring, token=w_token)
    right = drive_with_settings(settings=settings, tokens=r_ring, token=r_token)

    assert wrong.tool_names, "错工作区的令牌一个工具都没列出来"
    assert wrong.tool_names == right.tool_names, (
        "两个工作区列出了不同的工具集 —— 工作区那一维被接到了 tools/list 上，"
        "于是「铸错了场景」会表现为「这些工具不存在」"
    )


# ----------------------------------------------------------------------
# 四、顺序：角色 → 范围 → 背书
# ----------------------------------------------------------------------
def test_a_role_failure_is_decided_before_the_workspace(
    settings: Settings, researcher_only_token
) -> None:
    """只有研究侧角色、工作区**也不对**的令牌，调一个控制面工具得到「不存在」。

    范围判定若放在角色之前，这条会拿到范围那句话 —— 而它的处置（重铸）与它
    真正的问题（没有这个角色）不对应：重铸一条新令牌同样只有 researcher。
    """
    ring, token = researcher_only_token
    outcome = drive_with_settings(
        settings=settings, tokens=ring, token=token, calls=(("control_job", {}),), want_tool_list=False
    )
    text = outcome.call("control_job").text
    assert text == "Unknown tool: control_job", (
        f"角色不够的令牌拿到了范围那句话 —— 处置被指错了方向：{text!r}"
    )


def test_a_backing_failure_is_decided_after_the_workspace(
    settings: Settings, wrong_workspace_token, right_workspace_token
) -> None:
    """错工作区的令牌问一个**没接线**的工具，得到的是范围那句话。

    反过来的话，一个错工作区的令牌就能用「还没实现（Phase D）」把目录里
    **哪些工具接线了**枚举出来 —— 那是背书信息，而它现在是不可枚举的：同样
    这句话对每一个工具名都成立。

    这条判据的两半都要：同一个工具在**对**工作区的令牌下必须给出「还没实现」。
    少了后半句，一个把 ``_UNBACKED`` 也当成未背书而**永远**给出「还没实现」的
    实现对前半句同样全绿 —— 而它证明不了顺序。
    """
    w_ring, w_token = wrong_workspace_token
    r_ring, r_token = right_workspace_token

    wrong = drive_with_settings(
        settings=settings, tokens=w_ring, token=w_token, calls=((
            _UNBACKED,
            {},
        ),), want_tool_list=False
    )
    right = drive_with_settings(
        settings=settings, tokens=r_ring, token=r_token, calls=((
            _UNBACKED,
            {},
        ),), want_tool_list=False
    )

    wrong_text = wrong.call(_UNBACKED).text
    right_text = right.call(_UNBACKED).text

    assert "尚未接线" in right_text, (
        f"{_UNBACKED} 在这个实例里应当是「还没接线」—— 这条用例的前提没了：{right_text!r}"
    )
    assert "尚未接线" not in wrong_text, (
        f"错工作区的令牌读出了「这个工具没接线」—— 背书信息被枚举出来了：{wrong_text!r}"
    )
    assert _denial_shape(wrong_text, _UNBACKED) == _denial_shape(
        drive_with_settings(
            settings=settings, tokens=w_ring, token=w_token, calls=(("get_scene", {}),), want_tool_list=False
        ).call("get_scene").text,
        "get_scene",
    ), "错工作区的令牌对「没接线的工具」与「接线的工具」给出了两种回答"


# ----------------------------------------------------------------------
# 五、对象那一维：作业行自己带着 Scene
# ----------------------------------------------------------------------
@pytest.fixture
def jobs_factory(settings: Settings):
    """同一个库文件上的作业存储工厂 —— 与 ``interfaces/mcp/__main__.py`` 同一个形状。

    这里**必须真的接上** ``jobs``：不接的话这五条 G 工具落回 stub，而那三条
    对象判据会测到「还没接线」而不是场景判定（``get_job`` 是 G 阶段的工具）。
    """
    return lambda: SqliteJobStore(settings.database_path)


@pytest.fixture
def foreign_job(settings: Settings) -> str:
    """库里一行**别的场景**的作业。

    它是怎么来的：同一个人把 ``MARKETOPS_STATE`` 指到同一个库、又改了
    ``MARKETOPS_PROFILE`` 时，旧场景的行会留在库里。所以这不是一个假想的输入，
    是一次部署变更的正常副产品。
    """
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=OTHER,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026",
        idempotency_key="foreign-v1",
        request_sha256="a" * 64,
    )
    return job.job_id


@pytest.fixture
def local_job(settings: Settings) -> str:
    """同一张表里一行**本场景**的作业 —— 对象那一维的正面判据。"""
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=DEPLOYED,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026",
        idempotency_key="local-v1",
        request_sha256="b" * 64,
    )
    return job.job_id


def test_a_job_of_another_scene_cannot_be_read(
    settings: Settings, right_workspace_token, jobs_factory, foreign_job: str
) -> None:
    """令牌**对了**、工作区也对了，但那一行作业属于别的场景 ⇒ 拒绝。

    令牌判定答不了这个问题：它答的是「谁准调 get_job」，而一行别的场景的作业
    读起来与一行本场景的完全同形。
    """
    ring, token = right_workspace_token
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        jobs=jobs_factory,
        calls=(("get_job", {"job_id": foreign_job}),),
        want_tool_list=False,
    )
    text = outcome.call("get_job").text

    assert outcome.call("get_job").is_error, "别的场景的作业被读了回来"
    assert OTHER in text and DEPLOYED in text, (
        f"拒绝没有指出「读到的是哪个场景、本部署是哪个场景」：{text!r}"
    )


def test_a_job_of_this_scene_is_read_normally(
    settings: Settings, right_workspace_token, jobs_factory, local_job: str
) -> None:
    """同一张表里本场景的那一行照常读得回来 —— 否则上面那条只证明了「全拒」。"""
    ring, token = right_workspace_token
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        jobs=jobs_factory,
        calls=(("get_job", {"job_id": local_job}),),
        want_tool_list=False,
    )
    result = outcome.call("get_job")
    assert result.is_error is False, f"本场景的作业被拒了：{result.text}"
    assert result.as_json()["job"]["job_id"] == local_job


def test_a_job_of_another_scene_cannot_be_controlled(
    settings: Settings, right_workspace_token, jobs_factory, foreign_job: str
) -> None:
    """**这一处更要紧：它是写。**

    一个跨越场景的 pause / cancel 会真的去改一个不属于本部署的作业的状态，而
    回执上它与一次正常的控制完全同形 —— 报告里看不出那一行被谁动了。

    判别力在下半段：断言盘上的状态**没有变**。只断言「返回了 is_error」是不够的
    —— 一个先写后判的实现会在这里全绿，而它已经改完了。
    """
    ring, token = right_workspace_token
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        jobs=jobs_factory,
        calls=((
            "control_job",
            {
                "job_id": foreign_job,
                "action": "cancel",
                "expected_state": JobState.QUEUED.value,
                "reason": "cleanup",
            },
        ),),
        want_tool_list=False,
    )
    assert outcome.call("control_job").is_error, "别的场景的作业被控制成功了"

    row = SqliteJobStore(settings.database_path).get(foreign_job)
    assert row is not None, "那一行作业不见了"
    assert row.state is JobState.QUEUED, (
        f"一次被拒的控制改了盘上的状态：{row.state} —— 判定发生在写入之后"
    )


#: 结算那一条引用的证据 id。**真的存在**，由 ``_filed_evidence`` 写进本用例的
#: 证据库 —— 一条编出来的引用会让这条判据以「引用解不开」的方式红，而那个红的
#: 理由与 Scene 判定毫无关系（``tests/test_mcp_assignment_tools.py`` 里记过同一件事）。
_FINDINGS_EVIDENCE = sha256_text("foreign-scene-page-1")[:32]


@pytest.fixture(autouse=True)
def _filed_evidence(settings: Settings) -> None:
    """摆一条解得开的证据。

    它必须是 autouse 的：``submit_findings`` 在**结算之前**逐条查引用（见
    ``application/evidence_refs``），而这条判据要的是一条**本来会成功**的结算
    —— 只有那样，「被拒」才是 Scene 判定干的，而不是别的东西顺手拒的。
    """
    SqliteEvidenceStore(settings.database_path).put_evidence(
        make_evidence(evidence_id=_FINDINGS_EVIDENCE, account_id="foreign-devon-01")
    )


@pytest.fixture
def foreign_assignment(settings: Settings) -> tuple[str, str]:
    """一个属于**别的场景**、已经派发、并且由本文件那条令牌的主体持着的租约。

    ``holder`` 取 ``OPERATOR``（= 那条令牌的主体名）是刻意的：这样
    ``_require_holder`` 那一关是**过**的。不这么做的话，``submit_findings``
    会先在归属那一关被拒，于是一条「Scene 判定根本不存在」的实现照样全绿 ——
    判据就退化成在测归属。

    四个动词各自有自己的判据（``tests/test_job_contract.py``），这里只用它们摆落点。
    """
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=OTHER,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026",
        idempotency_key="foreign-assignment-v1",
        request_sha256="c" * 64,
    )
    (item,) = store.compile_work_items(
        job.job_id,
        synthetic_dossiers("foreign-devon-01"),
        scene=load_profile(settings).scene_files,
    )
    store.map_execution(item.work_item_id, quay_task_id="quay-task-foreign")
    attempt = store.lease(item.work_item_id, holder=OPERATOR)
    return item.work_item_id, attempt.attempt_id


def test_findings_for_another_scene_cannot_be_submitted(
    settings: Settings, right_workspace_token, jobs_factory, foreign_assignment
) -> None:
    """令牌对了、工作区也对了、租约**也确实是自己的** —— 但那份作业属于别的场景 ⇒ 拒绝。

    这一条与上面两条**不同源**：``_job`` / ``_control_job`` 读的是作业行，而这条
    路径读的是 work item。``WorkItem`` 上没有 ``scene_id``，所以要顺着
    ``item.job_id`` 走一步。判据写对了而这一处没接上，正是「一条边界只写在文档里
    就是自觉」的那种形态 —— 而它的表现是别的场景的产出被真的写了进去。

    判别力在下半段：断言那条 work item 在盘上**仍然没有停止理由**。只断言
    「返回了 is_error」不够 —— 一个先 ``settle`` 后判的实现会在这里全绿，而那份
    产出已经落库了。
    """
    work_item_id, attempt_id = foreign_assignment
    ring, token = right_workspace_token
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        jobs=jobs_factory,
        calls=((
            "submit_findings",
            {
                "work_item_id": work_item_id,
                "attempt_id": attempt_id,
                "stop_reason": "goal_met",
                "observations": [
                    {"statement": "不该落进这份作业的一句结论", "evidence_refs": [_FINDINGS_EVIDENCE]}
                ],
                "candidates": [],
                "gaps": [],
            },
        ),),
        want_tool_list=False,
    )
    result = outcome.call("submit_findings")
    assert result.is_error, "别的场景的租约被结算了"

    text = result.text
    assert OTHER in text and DEPLOYED in text, (
        f"拒绝没有指出「读到的是哪个场景、本部署是哪个场景」：{text!r}"
    )
    # 而且在职的**枚举之前**拒：那条回执会把这份 work item 上每一条 attempt id
    # 列出来，而 attempt id 是内容推导的摘要 —— 拒绝得晚一步，它就已经交出去了。
    assert attempt_id not in text, f"拒绝的回执里带了 attempt id：{text!r}"

    item = SqliteJobStore(settings.database_path).get_work_item(work_item_id)
    assert item is not None, "那条 work item 不见了"
    assert item.stop_reason is None, (
        f"一次被拒的结算在盘上留下了停止理由 {item.stop_reason!r} —— "
        "拒绝发生在 settle 之后，产出已经进库了"
    )


# ----------------------------------------------------------------------
# 六、claims 的解码：读不出来时失败关闭
# ----------------------------------------------------------------------
class _FakeToken:
    """一个只有 ``claims`` 的对象 —— ``_workspaces_from`` 唯一读的那一格。

    构造它是为了测**解码**：环的装载器拒绝空表与非列表，所以这几个形状
    **到不了线上**。它们是一层边界防御，防的是将来某条别的令牌来源（或某次
    对 ``auth.py`` 的改动）把形状不对的值放进来。
    """

    def __init__(self, claims: object) -> None:
        self.claims = claims


@pytest.mark.parametrize(
    "claims",
    [
        None,
        {},
        {"workspaces": None},
        {"workspaces": "uk-x"},
        {"workspaces": 7},
        {"workspaces": ["uk-x", 7, "", None]},
    ],
    ids=["no-claims", "no-key", "null", "bare-string", "int", "mixed-list"],
)
def test_only_a_list_of_non_empty_strings_yields_workspaces(claims: object) -> None:
    """除「非空字符串的列表」以外的任何形状 ⇒ 空集（全拒）。

    两个方向都要：``"uk-x"`` 被逐字符读成九个工作区是**失败关闭**的（真工作区
    不在里面），而一个「读不出来就放行」的缺省是敞开的。这一条钉的是后者没有
    发生 —— 与 ``roles_from_scopes`` 对不认识的 scope 的取向相同。

    第四格的混合列表同时钉住「筛掉坏元素」而不是「整条丢掉」：一个 ``7`` 混在
    里面不该把同一个列表里那个合法的工作区一起作废。
    """
    from marketops.adapters.mcp_server.guard import _workspaces_from

    result = _workspaces_from(_FakeToken(claims))
    if claims == {"workspaces": ["uk-x", 7, "", None]}:
        assert result == frozenset({"uk-x"}), f"混合列表里的合法元素被一起作废了：{result}"
    else:
        assert result == frozenset(), f"形状 {claims!r} 被读成了 {result}"

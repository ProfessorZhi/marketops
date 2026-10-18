"""工作区那一格（``security/workspace.py``）的纯函数判据。

``tests/test_mcp_workspace.py`` 证明这一格**挂在每一次调用上**（传输层到对象
那一整条链）。本文件只证明**判定本身**是对的 —— 四个函数、没有服务、没有环、
没有中间件。

## 为什么值得单独一个文件

这一格的作用域是「一条令牌准在哪一个 Scene 里动手」，而它的取值同时是
``Settings.profile_dir`` 的最后一个路径段。名字不合法与判定不通过是两件事，
处置也完全不同（改铸造命令 / 重铸令牌），而它们都会表现为「调用被拒」。所以
两个函数、两种报错，各自要被钉住：合并成一条「工作区不对」的报错，会把一次
拼写错误引向改环境变量。

## 承重的四条断言

1. **规范名通过、非规范名被拒，且拒绝不替调用方规范化。** 一个会顺手把
   ``UK Acmecorp`` 改写成 ``uk-acme`` 的校验，会让两个不同的名字
   静默合并成同一个工作区 —— 而那正是 ``check_canonical_id`` 存在的理由。
2. **空表被拒。** 空表与写错在行为上是同一件事（每一格都拒），而环文件上
   看不出它与一个正常条目的区别。装载期拒绝是唯一能把它区分开的地方。
3. **逐字比较。** 不做前缀、不做大小写折叠：两个工作区名要么逐字相同，
   要么是两个工作区。
4. **两个分支各说各的处置，且都不报出本部署的工作区。** 同一条纪律：
   一条不属于这里的令牌，不该从拒绝里学到这里的名字。
"""

from __future__ import annotations

import pytest

from marketops.domain.errors import AuthorizationError, ConfigError
from marketops.security.workspace import (
    check_workspace_name,
    holds_workspace,
    parse_workspaces,
    require_workspace,
)

#: 测试用的**本部署**工作区。刻意不是一个真的 profile id：这个文件测的是判定，
#: 而真的市场名只应出现在 ``profiles/`` 里（``test_no_hardcoded_market_knowledge``
#: 扫的正是 ``marketops/`` 的字面量 —— 测试文件放宽了，但没有理由顺手违反）。
_DEPLOYED = "deployed-scene"
_OTHER = "another-scene"


# ----------------------------------------------------------------------
# check_workspace_name
# ----------------------------------------------------------------------
@pytest.mark.parametrize("value", ["uk-x", "a", "scene-2026", "x.y", "x_y", "a" * 60])
def test_a_canonical_name_is_returned_unchanged(value: str) -> None:
    """合规的名字原样返回。断言的是**同一个对象**相等的那个值。

    ``a`` * 60 是上界那一侧：``check_canonical_id`` 的长度判据来自
    ``FILENAME_ID_MAX_LENGTH``，这个文件不去重复那个数字，只用它的一个边界
    取值证明**长名字不因为是长的而被拒**。
    """
    assert check_workspace_name(value, where="--workspace") == value


@pytest.mark.parametrize(
    "value",
    ["UK Acmecorp", "uk acme", "UK-X", "uk/x", "-uk-x", "x--", "uk-x-", "ü-uk"],
    ids=["space", "lower-space", "upper", "slash", "leading-dash", "trailing-double", "trailing-dash", "non-ascii"],
)
def test_a_name_that_is_not_canonical_is_refused_without_being_normalised(value: str) -> None:
    """非规范名被拒，**且不替调用方规范化**。

    第二条是这条断言的全部要点。一个「顺手规范化」的实现会在这里通过 ——
    而它会让 ``UK-X`` 与 ``uk-x`` 静默变成同一个工作区，两个名字在环文件上
    仍然不同、在判定上却是同一个。
    """
    with pytest.raises(ConfigError) as excinfo:
        check_workspace_name(value, where="--workspace")

    message = str(excinfo.value)
    assert value in message, f"报错里没有那个被拒的原值：{message!r}"
    assert "--workspace" in message, f"报错没指回它来自哪一格：{message!r}"
    assert "不做规范化" in message, (
        "报错没有说清「为什么不替你规范化」—— 没有那句，读的人第一反应是"
        f"「那就帮我改一下」：{message!r}"
    )


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_an_empty_name_is_refused(value: str) -> None:
    """空值与首尾空白由 ``check_canonical_id`` 交回给 ``check_slug_safe`` 报出。

    这一条钉的是**没有第二种说法**：本模块不自己判空，所以「空」只有一句报错。
    """
    with pytest.raises(ConfigError):
        check_workspace_name(value, where="--workspace")


@pytest.mark.parametrize("value", [None, 7, ["uk-x"], {"uk-x"}, b"uk-x"], ids=["none", "int", "list", "set", "bytes"])
def test_a_non_string_is_refused(value: object) -> None:
    """非字符串在**进** ``check_canonical_id`` 之前就被拒。

    少了这一步，一个 ``None`` 会一路走到 ``slugify`` 里并在那里炸成
    ``AttributeError`` —— 一条与「配置写错了」毫无关系的报错。
    """
    with pytest.raises(ConfigError) as excinfo:
        check_workspace_name(value, where="--workspace")
    assert "必须是一个字符串" in str(excinfo.value)


def test_a_list_is_refused_even_though_iterating_it_would_work() -> None:
    """反例：``["uk-x"]`` 是「看起来像别名」的那种输入。

    一个接受可迭代对象的实现会把它读成「一个工作区」，而调用方以为自己写了
    一个。这一条与上一条一起把「宽容的读取」挡在外面。
    """
    with pytest.raises(ConfigError):
        check_workspace_name(["uk-x"], where="--workspace")


# ----------------------------------------------------------------------
# parse_workspaces
# ----------------------------------------------------------------------
def test_a_list_of_names_becomes_a_set() -> None:
    assert parse_workspaces(["uk-x", "uk-y"], where="--workspace", principal="p") == frozenset(
        {"uk-x", "uk-y"}
    )


def test_a_repeated_name_is_not_a_second_grant() -> None:
    """写两遍等于写一遍 —— ``frozenset`` 让它成立。

    重复本身不是错误（多个重复角色同样无害），所以这里断言的是**集合**相等，
    而不是「报错」。
    """
    parsed = parse_workspaces(["uk-x", "uk-x"], where="--workspace", principal="p")
    assert parsed == frozenset({"uk-x"})
    assert len(parsed) == 1


@pytest.mark.parametrize("value", [None, "uk-x", {"uk-x": 1}, 7], ids=["none", "string", "mapping", "int"])
def test_a_non_list_is_refused(value: object) -> None:
    """**一个字符串不是一张单元素表。** 这条比看上去重要：``"uk-x"`` 被当作
    可迭代对象读下去会变成九个单字符工作区，而判定会全部失败 —— 一次「配了却
    被拒」的形状。"""
    with pytest.raises(ConfigError) as excinfo:
        parse_workspaces(value, where="--workspace", principal="p")
    assert "必须是一个工作区列表" in str(excinfo.value)


def test_an_empty_list_is_refused() -> None:
    """空表在**装载期**抛，而不是让服务起来之后每条调用都拒。

    一个空表与一个拼错的名字，行为上都是「这个主体什么都进不去」—— 那是权限
    bug 的样子。区别是空表可以在装载期被认出来。
    """
    with pytest.raises(ConfigError) as excinfo:
        parse_workspaces([], where="--workspace", principal="op-1")
    message = str(excinfo.value)
    assert "空表" in message
    assert "op-1" in message, f"报错没说是哪个主体：{message!r}"


def test_a_bad_item_says_which_index_it_was() -> None:
    """第几项要出现在报错里 —— 一格里有三个名字时，「哪个写错了」是第一个问题。"""
    with pytest.raises(ConfigError) as excinfo:
        parse_workspaces(["uk-x", "UK Y"], where="--workspace", principal="p")
    message = str(excinfo.value)
    assert "第 1 项" in message, f"报错没有定位到那个坏掉的下标：{message!r}"
    assert "UK Y" in message


# ----------------------------------------------------------------------
# holds_workspace
# ----------------------------------------------------------------------
def test_membership_is_literal() -> None:
    assert holds_workspace({"uk-x"}, "uk-x") is True
    assert holds_workspace({"uk-x"}, "uk-y") is False


@pytest.mark.parametrize(
    "granted,workspace",
    [
        ({"uk-x"}, "UK-X"),
        ({"uk-x"}, "uk-x-south"),
        ({"uk-x-south"}, "uk-x"),
        ({"uk-x"}, " uk-x"),
        ({"uk-x"}, "uk-x "),
        ({"uk-x"}, ""),
    ],
    ids=["case", "prefix", "extends", "leading-space", "trailing-space", "empty"],
)
def test_membership_does_not_fold_prefix_or_trim(granted: set[str], workspace: str) -> None:
    """逐字比较：不折大小写、不做前缀、不裁空白。

    前缀那两格是**真的会出事**的一种误实现（``startswith`` 读起来像「同一族
    场景」），而它会让 ``uk-x`` 的令牌在 ``uk-x-south`` 上生效。
    """
    assert holds_workspace(granted, workspace) is False


def test_membership_accepts_any_iterable() -> None:
    """``Iterable`` 而不是 ``frozenset``：调用方手里可能是刚构造的主体字段。"""
    assert holds_workspace(["uk-x"], "uk-x") is True
    assert holds_workspace(("uk-x",), "uk-x") is True
    assert holds_workspace(iter(["uk-x"]), "uk-x") is True


# ----------------------------------------------------------------------
# require_workspace
# ----------------------------------------------------------------------
def test_a_held_workspace_is_returned() -> None:
    """通过时**返回那个名字**，不是 ``None``。

    返回一个值让调用点写成 ``scene = require_workspace(...)`` —— 与
    ``require_scene`` 同形，「判了没有」在代码里看得见。
    """
    assert (
        require_workspace(
            granted={_DEPLOYED}, workspace=_DEPLOYED, principal="op-1", where="工具 'get_scene' 的调用"
        )
        == _DEPLOYED
    )


def test_a_workspace_the_token_was_not_minted_for_is_refused_with_the_remedy() -> None:
    """有别的、没有这一个 ⇒ 处置是**重铸**，而且要说明为什么改环境变量没用。"""
    with pytest.raises(AuthorizationError) as excinfo:
        require_workspace(
            granted={_OTHER},
            workspace=_DEPLOYED,
            principal="op-1",
            where="工具 'get_scene' 的调用",
        )
    message = str(excinfo.value)
    assert "重铸" in message and "mint-token" in message, f"没说清处置：{message!r}"
    assert "受众" in message, (
        "没有点明工作区与受众是两格 —— 读的人会把重铸与「改受众」混起来："
        f"{message!r}"
    )
    assert "改环境变量" in message, f"没说清为什么改配置没用：{message!r}"


def test_an_empty_grantage_says_it_is_not_from_the_ring() -> None:
    """一个工作区都没有 ⇒ 这一格根本是空的。

    这一条的处置与上一条**不同**（重铸一条什么都没有的令牌没有意义），所以
    报错必须不同。能让环装载起来就说明环里读了非空表
    （``parse_workspaces`` 拒绝空表），所以走到这里多半意味着主体不是从环里来的。
    """
    with pytest.raises(AuthorizationError) as excinfo:
        require_workspace(
            granted=frozenset(), workspace=_DEPLOYED, principal="op-1", where="工具 'get_scene' 的调用"
        )
    message = str(excinfo.value)
    assert "没有任何工作区" in message, f"报错没有说清「这一格是空的」：{message!r}"
    assert "不是从环里读出来的" in message, (
        f"报错没有把读的人引向真正的原因（主体不是从环来的）：{message!r}"
    )
    assert "mint-token" not in message, (
        "空的这一格不该给出「重铸」那条指引 —— 那条指引对应的处置是改环文件里"
        f"某一格的名字，而这里的问题是那一格整个不存在：{message!r}"
    )


def test_the_two_branches_are_distinguishable() -> None:
    """两条报错必须**不一样**。

    合成一句会让第二种情况（铸给了别的场景）的人去做第一种的处置。这是本条
    与上面两条一起要钉的东西：光测「都抛了」是不够的，一个把两个分支合并的
    实现也满足它。
    """
    messages = []
    for granted in (frozenset(), {_OTHER}):
        with pytest.raises(AuthorizationError) as excinfo:
            require_workspace(
                granted=granted,
                workspace=_DEPLOYED,
                principal="op-1",
                where="工具 'get_scene' 的调用",
            )
        messages.append(str(excinfo.value))
    assert messages[0] != messages[1], "两个分支给出了同一句话 —— 处置不同就必须分开"


@pytest.mark.parametrize("granted", [frozenset(), {_OTHER}], ids=["empty", "other"])
def test_neither_branch_reports_the_deployed_workspace(granted: frozenset[str]) -> None:
    """**两条报错都不含本部署的工作区名。**

    与受众错配同一个取向（``adapters/mcp_server/auth.py``）：一条不属于这里的
    令牌，不该从拒绝里学到这里的名字。诊断走**已认证的**表面 —— ``--check``
    的 ``mcp.workspaces``。

    这条是「处理得清楚一点」的诱惑的对称物：把本部署的名字写进报错确实更好
    排查，而那正是它不该在里面的理由。
    """
    with pytest.raises(AuthorizationError) as excinfo:
        require_workspace(
            granted=granted, workspace=_DEPLOYED, principal="op-1", where="工具 'get_scene' 的调用"
        )
    message = str(excinfo.value)
    assert _DEPLOYED not in message, f"报错里出现了本部署的工作区：{message!r}"
    assert "--check" in message, f"报错没有给出诊断的出路：{message!r}"


def test_the_refusal_quotes_the_principal_and_the_call_site() -> None:
    """主体与调用点在报错里 —— 一条不知道「谁、哪次调用」的拒绝没法排查。"""
    with pytest.raises(AuthorizationError) as excinfo:
        require_workspace(
            granted={_OTHER},
            workspace=_DEPLOYED,
            principal="worker-7",
            where="工具 'search_web' 的调用",
        )
    message = str(excinfo.value)
    assert "worker-7" in message
    assert "'search_web'" in message


def test_a_generator_grantage_is_not_read_twice() -> None:
    """``granted`` 是一个生成器时，两个分支不能把它读空。

    ``held = frozenset(granted)`` 这一行就是为此存在的。少了它，第一个分支
    （``not holds_workspace(...)``）会消费掉整个生成器，而第二个分支
    （``if not held``）看到的是一个**已空**的生成器对象 —— 它恒为真，于是
    「有别的、没有这一个」被读成「一个都没有」，两个处置刚好对调。

    生成器在这里是**唯一的**能被读空的形状，所以它是这条判据的输入。
    """
    with pytest.raises(AuthorizationError) as excinfo:
        require_workspace(
            granted=(name for name in [_OTHER]),
            workspace=_DEPLOYED,
            principal="op-1",
            where="工具 'get_scene' 的调用",
        )
    message = str(excinfo.value)
    assert "不包含本部署的工作区" in message, (
        "一个只含别的工作区的生成器被读成了「一个都没有」—— 于是处置从"
        f"「重铸」变成了「去查环」：{message!r}"
    )

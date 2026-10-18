"""工作区：一个主体被限定在哪一个 Scene 之内（§7 / §6.1）。

## 这一格补的是「角色」答不了的那个问题

``catalog.py`` 的判据是 ``(主体, 工具名) -> 结论``。它答的是「**这个人准不准调
这个工具**」，而它答不了「**准他动哪个 Scene 的东西**」—— ``docs/MCP_SERVICE.md``
§6.1 把那件事写在了明处：「Role checks alone do not establish multi-tenant object
isolation」。一条铸给 A 场景、带着 researcher 角色的令牌，在 B 场景的部署上
角色判定与 A 的一模一样，于是它照常被放行；环文件上看不出区别，日志里也看不出。

所以主体到对象之间缺的那一段是：**令牌属于哪个工作区**。

## 工作区就是 Scene，值就是 profile id

不引入第二套命名。理由是这两个东西的重合不是巧合而是定义：本仓的部署边界就是
Scene（``require_scene`` 的措辞：「一个部署只服务一个 Scene —— 证据、预算与凭据
都属于它」），而 Scene 的标识就是 profile id。另起一个名字（``tenant`` / ``org``）
会立刻要求回答「它与 Scene 是不是一一对应」，而那个答案要么是「是」（于是两个名字
指一个东西），要么是「不是」（于是要有一张映射表，而那张表没有生产者）。

## 为什么名字必须是 ``slugify`` 的不动点

``Settings.profile_dir`` 是 ``profiles_dir / profile`` —— 这个值**是一个路径段**。
于是一个含 ``/`` 的值能指向 profile 目录之外，一个含空格的 ``My Scene`` 与
``my-scene`` 会是两个字符串而只可能有一个目录。规则不在本模块发明：它就是
``domain.util.check_canonical_id``（「id 必须是 slugify 的不动点，不满足就抛，
**不截断**」）。装载器与 ``mint-token`` 走同一个函数，所以「命令行收下的名字」
与「环里读得出的名字」不可能在某一天分开。

## 拒绝的那句话**不报出本部署的工作区是哪一个**

与受众错配同一个取向（``auth.py``）：一条不属于这里的工作区令牌，不该从拒绝里
学到这里的名字。诊断走**已认证的**表面 —— ``--check`` 的 ``mcp.workspaces``
报环里实际有哪些工作区，``mint-token`` 在 stderr 报它刚写进去的那一个。

## 说得出它**不**覆盖什么

本模块只回答「这条令牌进不进得来这个工作区」。它不回答「进来之后能不能读这一条
证据 / 这一份账号档案」—— 那些对象上**没有** Scene 血缘（``evidence`` 只有
``account_id``），所以对它们的工作区判定不可实现，本模块也不假装能做。对象那一维
落在对象本身有 Scene 的地方，见 ``require_scene`` 的调用点。
"""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.errors import AuthorizationError, ConfigError
from ..domain.util import check_canonical_id

__all__ = [
    "check_workspace_name",
    "holds_workspace",
    "parse_workspaces",
    "require_workspace",
]


def check_workspace_name(value: object, *, where: str) -> str:
    """工作区名的唯一判据：非空、无首尾空白、是 ``slugify`` 的不动点。

    ``where`` 是**这句话里**那个值的出处（``--workspace`` / 第几个条目），
    与 :func:`require_workspace` 的 ``where`` 不是一回事：那一个是「哪个调用被
    拒绝」，这一个要指回盘上的哪一个字符串。两个都叫 ``where`` 会让一条
    「名字写得不规范」的报错看起来像一次权限拒绝。
    """
    if not isinstance(value, str):
        raise ConfigError(f"{where} 必须是一个字符串：{value!r}")
    try:
        # 空值与首尾空白由它一并报出（check_canonical_id 把这两种交给
        # check_slug_safe），所以「空」在这里没有第二种说法。
        return check_canonical_id(value, where)
    except ValueError as exc:
        raise ConfigError(
            f"{exc}\n"
            "工作区名同时是一个目录名（Settings.profile_dir = profiles_dir / profile），"
            "所以它必须已经是规范形式 —— 校验不做规范化，规范化会把两个不同的名字"
            "静默合并成一个。"
        ) from exc


def parse_workspaces(value: object, *, where: str, principal: str) -> frozenset[str]:
    """环里某一格的 ``workspaces`` → 集合。**空表抛 ``ConfigError``**。

    与 ``roles`` 同一条纪律：一个空表与一个拼错的名字，行为上都是「这个主体什么
    都进不去」—— 那是权限 bug 的样子，所以在装载期报错，而不是让服务起来之后
    每条调用都拒绝。

    去重之后比较：同一格写两遍是笔误，不是两种授权，而一个 ``frozenset`` 让它
    在判定上等于写一遍。这也是**它不与 ``roles`` 共用一套校验**的原因 —— 多个
    重复角色是无害的，多个重复工作区同样无害，但两者的取值规则完全不同。
    """
    if not isinstance(value, list):
        raise ConfigError(
            f"{where} 必须是一个工作区列表：每个元素是一个 profile id，主体 "
            f"{principal!r} 准在哪个 Scene 里动手就写哪个。"
        )
    if not value:
        raise ConfigError(
            f"{where} 是空表（主体 {principal!r}）。\n"
            "空表与写错是同一件事：这个主体的每一条业务调用都会被拒绝，而环文件上"
            "看不出它与一个正常条目的区别。\n"
            "要铸一条什么都不能动的令牌，本仓没有这个需求 —— 不铸就是了。"
        )
    return frozenset(
        check_workspace_name(item, where=f"{where} 第 {index} 项")
        for index, item in enumerate(value)
    )


def holds_workspace(granted: Iterable[str], workspace: str) -> bool:
    """主体被授权的工作区里有没有这一个。**纯判定，不抛。**

    ``granted`` 用 ``Iterable`` 而不是 ``frozenset``：调用方手里可能是环里读出来
    的集合、也可能是一个刚构造出来的主体（``Principal.workspaces``），而两种都是
    同一个问题。判定只做成员比较 —— 不做前缀、不做大小写折叠：两个工作区名要么
    逐字相同，要么是两个工作区。
    """
    return workspace in set(granted)


def require_workspace(
    *, granted: Iterable[str], workspace: str, principal: str, where: str
) -> str:
    """判定一次调用；不通过抛 ``AuthorizationError``，通过则原样返回工作区名。

    返回那个名字而不是 ``None``：调用点写起来与 ``require_scene`` 同形
    （``scene = require_workspace(...)``），于是「判了没有」在代码里看得见 ——
    一个返回 ``None`` 的判定很容易被当成一个可忽略的副作用。

    两个分支的**处置不同**，所以它们必须分开：

    * 授权里一个工作区都没有 —— 令牌这一格是空的。能把环装载起来就说明它不是
      （``parse_workspaces`` 拒绝空表），所以这多半意味着主体不是从环里来的，
      或者中间丢了一格。
    * 授权里有别的、没有这一个 —— 令牌是给另一个工作区铸的。处置是**重铸**，
      不是改环境变量。

    合成一句会让第二种情况的人去做第一种的处置，而两者唯一的共同点是「都被拒了」。
    """
    # 先落成集合：``granted`` 可能是任何一个可迭代对象，而下面两个分支都要读它，
    # 一个生成器会在第一个分支之后空掉 —— 于是「有别的、没有这一个」会被读成
    # 「一个都没有」，两个处置刚好对调。
    held = frozenset(granted)
    if not holds_workspace(held, workspace):
        if not held:
            raise AuthorizationError(
                f"主体 {principal!r} 的授权里没有任何工作区，所以 {where} 被拒绝。\n"
                "铸造时 --workspace 是必填的，环的装载器也拒绝空表 —— 所以这通常"
                "意味着这条主体不是从环里读出来的。\n"
                "诊断：``python -m marketops.interfaces.mcp --check`` 的 "
                "mcp.workspaces 那一格。"
            )
        raise AuthorizationError(
            f"主体 {principal!r} 的授权不包含本部署的工作区，所以 {where} 被拒绝。\n"
            "工作区与受众是两格，答的是两个问题：受众答「这条令牌发给哪个服务」，"
            "工作区答「在那个服务里它准动哪个 Scene 的对象」。这条令牌的两格都过了"
            "受众那一关（否则它在传输层就 401 了），过不了工作区这一关。\n"
            "出路是**重铸**：``marketops mint-token --workspace <本部署的 Scene> ...``。"
            "工作区存在环里，改环境变量改不动它 —— 与受众同一条纪律。\n"
            "这一句**不报出**本部署的工作区是哪一个，与本仓处理受众错配时同一个"
            "取向（``adapters/mcp_server/auth.py``）：一条不属于这里的令牌，不该从"
            "拒绝里学到这里的名字。诊断走已认证的表面 —— "
            "``python -m marketops.interfaces.mcp --check`` 的 mcp.workspaces。"
        )
    return workspace

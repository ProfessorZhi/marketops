"""角色判定的执行器（§7）。

``security/catalog.py`` 是一条纯函数策略：给定主体与工具名，结论是什么。
本模块把那条策略**挂在每一次调用上**，并且把主体取自传输层已经确立的身份
（``get_access_token()``），而不是取自本次调用的载荷。

## 为什么是中间件，而不是在工具函数里各判一次

两个理由，第二个是决定性的。

* 十五个工具各写一遍判定，就有十五处可以漂移，而漂移的那一处会表现为
  「某个工具忘了判」—— 它不报错，只是多了一条没人评审过的权限面。
* **顺序**。中间件运行在参数校验之前（``ServerMiddleware`` 的契约：after
  ``ctx`` is built, before any validation, lookup, or handshake）。若判定写在
  工具函数里，一个越权调用带上非法参数时，调用方拿到的会是「字段 X 不合法」
  —— 那句话本身泄漏了「这个工具存在」。所以判定必须在校验之前，
  ``tests/test_mcp_isolation.py`` 把这一条钉成了行为。

## 三种失败合成一种回答

没有主体、工具不在目录里、角色不允许 —— 对外都是 ``Unknown tool: <name>``，
与 SDK 自己那句**逐字相同**（``mcp/server/mcpserver/tools/tool_manager.py``
的 ``ToolError(f"Unknown tool: {name}")``，经 ``_handle_call_tool`` 原样出线）。

刻意不做「拒绝得清楚一点」：只要两句话可区分，一次枚举就能把不属于自己的
目录读出来。差别本身就是泄漏。

## 「还没实现」是第四种，而且必须可区分

``Unknown tool`` 说的是「没有这个工具」。对于目录里有、本构建没接线的九个
工具，那句话是**错误的事实**，排查方向会被引到「调用方写错了名字」。所以
它们是另一句话，带阶段号。两种回答都不可绕过：不属于你的工具先被拒绝，
然后才轮得到「还没实现」—— 顺序反过来的话，一个研究侧 worker 就能通过
「这个工具还没实现」把 control 目录枚举出来。

## 工作区不匹配是那条「一种回答」规则的唯一例外

上面那条规则的理由是**防枚举**：只要「越权」与「不存在」可区分，一次试探就能把
不属于自己的目录读出来。工作区那一维不满足这条规则的前提 —— 会走到这个判定的
调用方**已经是一条有效的令牌**（它过了受众与有效期，否则在传输层就 401 了），
而目录本身写在 ``docs/MCP_SERVICE.md`` 里。所以在这里合成一句「Unknown tool」
挡不住任何人，只会让一次**铸造错误**（令牌铸给了另一个 Scene）表现为
「这些工具都不存在」—— 排查方向被引到「调用方拼错了名字」，而真正的处置在环上。

它仍然守住了那条规则真正要守的东西：**同一句话回答每一个工具名**。一个错工作区
的令牌问任何名字都得到同一句，所以它在调用面上什么都枚举不出来 —— 包括
「这个工具接没接线」。这句话里也**不报出**本部署的 Scene（那一句指向 ``--check``
的 ``mcp.workspaces``），与受众错配不去报出期望受众是同一个取向。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.types import CallToolResult, TextContent

from ...domain.errors import AuthorizationError
from ...security.catalog import (
    PHASES_OF_SERVICE_DOCUMENT,
    TOOL_CATALOG,
    Principal,
    authorize,
    roles_from_scopes,
    visible_tools,
)
from ...security.workspace import require_workspace

__all__ = [
    "RoleGuard",
    "unavailable_message",
    "unbacked_message",
    "unknown_tool_result",
]


def unknown_tool_result(name: str) -> CallToolResult:
    """与 SDK 对未知工具的回答**逐字**相同。

    这句话的形状是一个契约，不是一个文案：``tests/test_mcp_isolation.py``
    把「越权」与「不存在」两条路径的文本直接比对。改这里的措辞之前先看那条
    测试 —— 它比对的另一半来自 SDK，改不动。
    """
    return CallToolResult(
        content=[TextContent(type="text", text=f"Unknown tool: {name}")], is_error=True
    )


def unbacked_message(name: str) -> str:
    """「这个工具在目录里，但**这个服务实例**没有接线」的那句话。

    带工具名与阶段号：调用方据此能判断该等还是该改。``tools.py`` 的 stub
    也用这一句 —— 两处各写一份，第一次改动就会分家，而分家的形态是「同一个
    工具在两条路径上给出两种说法」。

    主语是**这个实例**，不是「本构建」：研究侧的两个工具（Phase D）已经有了
    实现，而 ``build_server`` 可以在不接研究侧的情况下被组装（只接控制面的
    部署是合法的一种）。那时它们的答案是这一句。至于「这个构建里到底有没有
    它」，``get_capabilities`` 的 ``integrations`` 逐项回答，而且分三个格
    （配了没 / 装了没 / 有没有工具会调它）。

    第二句从前写作「它归属 §21 的 Phase {phase}」，而那个说法对 **J** 是**假的** ——
    §21 的服务文档只有 A–I，J 是本仓的产品环（``docs/MCP_PRODUCT_LOOP.md``），它不
    在那份文档里。一句「它什么时候能好」的话说错了归属，读的人会去那份文档里找
    一个不存在的章节，然后以为是自己没找到。所以这一格按
    :data:`~marketops.security.catalog.PHASES_OF_SERVICE_DOCUMENT` 分岔 ——
    那份闭集是**唯一**的判据，不是这里再抄一遍 "ABCDEFGHI"。将来 §21 增补一段
    （比如把产品环写进去），那句话会自动跟着变。
    """
    spec = TOOL_CATALOG.get(name)
    if spec is None:
        # 走到这里在今天的接线里是不可能的（``_stub`` 与这一格都只在目录上遍历）。
        # 但它**不得**替一个不认识的工具认领一个阶段 —— 那会给出「J 是产品环」
        # 这种对 ``frobnicate`` 完全无意义的句子。所以这里单独说一句实话。
        return (
            f"工具 {name!r} 既不在目录里，也没有实现 —— "
            "它不可能被注册，也不该走到这一句上。"
        )
    phase = spec.phase
    summary = spec.summary
    if phase in PHASES_OF_SERVICE_DOCUMENT:
        where = f"它归属 §21 的服务文档 Phase {phase}。"
    else:
        where = (
            f"它不属于 §21 的 A–I 任何一段 —— {phase} 是本仓的产品环"
            "（docs/MCP_PRODUCT_LOOP.md），§21 里没有它。"
        )
    return (
        f"工具 {name!r} 在目录里，但当前服务实例尚未接线（Phase {phase}）。"
        + where
        + (f"\n该工具做什么：{summary}" if summary else "")
    )


def unavailable_message(name: str, reason: str) -> str:
    """「这个实例**没有这项能力**，所以这条工具根本没注册」的那句话。

    ## 它与 :func:`unbacked_message` 是两句不同的话

    ``unbacked_message`` 回答「这个工具在目录里、这个实例只是还没接它」—— 它的
    下一步是**接线**。这一句回答「这个部署没有这一项能力」—— 它的下一步是**改
    部署**（补凭据 / 补一个来源目录），接线的位置与写法都没错。

    两者混成一句的后果不是文案不准：``send_wave`` 在一台没配 SMTP 的机器上被报成
    「尚未接线」，运维会去看代码的接线；而真正要做的事是配那六样。一块写着
    「还没做」的牌子挂在一台**配置不全**的机器上，是会挂很久的。

    ``reason`` 由**接线那一侧**给（组合根），因为它才知道缺的是哪一样：
    ``settings.outbound_missing`` 那类派生值住在配置层，而这一层只负责把话说出去。
    """
    spec = TOOL_CATALOG.get(name)
    summary = spec.summary if spec is not None else ""
    return (
        f"工具 {name!r} 在本部署里**没有注册**：{reason}\n"
        "  它不在 tools/list 上，也不可调用 —— §8 逐字：「新的危险操作对普通研究身份"
        "不可见、不可调用，仅在部署能力启用时注册」。\n"
        "  这与「尚未接线」不是同一件事：那一条在目录里、调得到、回一句阶段号；"
        "这一条是这个实例**没有这项能力**，要动的是部署，不是接线。"
        + (f"\n该工具做什么：{summary}" if summary else "")
    )


def _unauthorized(name: str) -> CallToolResult:
    return unknown_tool_result(name)


def _wrong_workspace(message: str) -> CallToolResult:
    """工作区不匹配的回答。**这是那个「一种回答」规则的唯一例外**，理由见
    模块 docstring 的最末一节。"""
    return CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)


def _workspaces_from(token: Any) -> frozenset[str]:
    """从 ``AccessToken.claims`` 里读工作区。

    ``auth.py`` 把环里那一格原样放进 ``claims["workspaces"]``。要读得这么小心，
    是因为这个值的**来源**是解码后的令牌声明（协议上它是 ``dict[str, Any]``），
    而一个宽容的读取会把 ``"uk-x"``（一个字符串）逐字符迭代成九个工作区。

    读不出来时返回**空集**：那是「这个主体在范围上什么都没有」，也就是全拒 ——
    与 ``roles_from_scopes`` 对不认识的 scope 的取向相同（失败关闭，而不是
    「读不出来就放行」）。
    """
    claims = getattr(token, "claims", None)
    if not isinstance(claims, Mapping):
        return frozenset()
    value = claims.get("workspaces")
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(item for item in value if isinstance(item, str) and item)


def current_principal() -> Principal | None:
    """从传输层已确立的身份算出主体。

    ``get_access_token()`` 读的是一个 contextvar，由 ``AuthContextMiddleware``
    在请求周期内置入；没有认证、或令牌没带任何已知 scope 时它返回
    ``None``/空角色集 —— 两种都落成「没有主体」，也就是全拒。

    ``subject`` 优先于 ``client_id``：两个字段由 ``auth.py`` 填成同一个值，
    但真到了不一致的时候，``subject`` 是 OAuth 里「谁」的那一位。
    """
    token = get_access_token()
    if token is None:
        return None
    name = token.subject or token.client_id or ""
    return Principal(
        name=name,
        roles=roles_from_scopes(token.scopes),
        workspaces=_workspaces_from(token),
    )


class RoleGuard:
    """一个 ``ServerMiddleware``：过滤 ``tools/list``、判定 ``tools/call``。

    Args:
        backed: 本构建**真的有实现**的工具名。不在这个集合里但属于某个角色的
            工具，会拿到它的阶段号而不是「Unknown tool」。
        workspace: **本部署**的 Scene（``settings.profile``）。每一次 ``tools/call``
            都要求主体被授权进这一个工作区 —— 角色判定答不了这个问题：一条铸给
            另一个 Scene 的令牌，角色判定与这里的一模一样。
        unavailable: **本部署没有这一项能力**因此没有注册的工具，按名字索引到
            「缺的是哪一样」。空表（缺省）= 本部署没有裁掉任何一条。
    """

    def __init__(
        self,
        *,
        backed: frozenset[str],
        workspace: str,
        unavailable: Mapping[str, str] | None = None,
    ) -> None:
        self._backed = backed
        self._workspace = workspace
        self._unavailable = dict(unavailable or {})
        # 一条工具**同时**「有实现」与「没这项能力」是接线矛盾，而且它的表现
        # 是一句随顺序变化的答复（下面那个检查在 ``_backed`` **之前**，所以那条
        # 工具会永远拿到第五种回答，它的实现是一段死代码）。构造期就拒 ——
        # 一个只在运行时才显形的矛盾，要靠一次真的调用才会被发现。
        both = sorted(set(self._unavailable) & backed)
        if both:
            raise ValueError(
                f"这些工具既在 backed 里、又在 unavailable 里：{both}。\n"
                "「有实现」与「本部署没有这项能力」不可能同时成立 —— 前者说代码在、"
                "后者说它没被注册。要么把它从 unavailable 里拿掉，要么让接线真的"
                "不产出它。"
            )

    # ------------------------------------------------------------------
    # 读面：这两个集合是**判定的事实**，所以它们要能被读出来，而不是只被用掉
    #
    # `_register` 与这一个中间件用的是同一对输入，于是「这一条工具接线了没有」
    # 这句话有两个消费者。今天没有别的读面时，第三个消费者（试运行自检）要想
    # 知道这句话，就得把 `_register` 那三段抄一遍 —— 而抄出来的那份会在第一次
    # 有人给 `_register` 加第四种结局时静默变旧，且它变旧的样子是**报一句
    # 错的健康结论**。
    #
    # 做成 property 而不是把字段改成公开的：构造契约不变（三个关键字参数的
    # 名字与含义都没动），而这一个读面还能各写自己的语义。
    # ------------------------------------------------------------------
    @property
    def backed(self) -> frozenset[str]:
        """本构建**真的有实现**、注册了实现的那些工具名。"""
        return self._backed

    @property
    def unavailable(self) -> Mapping[str, str]:
        """本部署**没有这一项能力**、因此根本没注册的工具，按名字索引到缺的是哪一样。

        返回一份拷贝而不是内部那一个 dict：调用方拿它去改自己的副本是正常的，
        而改了内部那一份会让「谁都不许」的答复按调用顺序变。
        """
        return dict(self._unavailable)

    async def __call__(
        self, ctx: ServerRequestContext[Any, Any], call_next: CallNext
    ) -> HandlerResult:
        """契约来自 ``ServerMiddleware``：``(ctx, call_next) -> result``。

        两个方法之外的一切原样透传 —— ``initialize`` 也走 ``call_next``，
        因为握手不是权限判定的一部分（没认证的客户端在传输层就被 401 挡掉了，
        见 ``test_mcp_isolation.py`` 的 ``test_no_token_is_refused_at_the_transport``）。
        """
        method = ctx.method
        if method == "tools/list":
            return self._filter_list(await call_next(ctx))
        if method == "tools/call":
            refusal = self._refuse(ctx.params)
            if refusal is not None:
                return refusal
        return await call_next(ctx)

    # ------------------------------------------------------------------
    def _filter_list(self, result: Any) -> Any:
        """``tools/list`` 的结果是一份 per-version 序列化后的 **dict**。

        这里改的是 dict 而不是 ``ListToolsResult``：中间件的返回值已经在
        ``ServerRunner._inner`` 里过了 ``_serialize``，绕过去返回一个 pydantic
        模型会让这一条路径少掉版本相关的加工（``resultType`` 那一类）。
        只做一处改动：``tools`` 那一项按角色筛。

        ``tools`` 缺席或形状不对时原样放行。那是一次 SDK 契约变化，不是权限
        问题；在这里猜它的新形状，会把一次升级变成一条静默的过滤失效。
        """
        if not isinstance(result, dict):
            return result
        tools = result.get("tools")
        if not isinstance(tools, list):
            return result
        allowed = set(visible_tools(current_principal()))
        kept = [
            tool
            for tool in tools
            if isinstance(tool, Mapping) and tool.get("name") in allowed
        ]
        return {**result, "tools": kept}

    def _refuse(self, params: Any) -> CallToolResult | None:
        """判定一次 ``tools/call``；不拒绝则返回 ``None``。

        参数在这里是**原始**的（尚未校验），所以取名字要防着形状不对。
        形状不对时放行给下游：那不是一次授权问题，下游会给出正确的
        ``invalid params``，而在这里替它编一句话只会让「名字缺失」看起来
        像一次权限拒绝。
        """
        if not isinstance(params, Mapping):
            return None
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return None

        principal = current_principal()
        try:
            # 判定**先于**背书检查。反过来会让「还没实现」成为一条可枚举
            # control 目录的旁路 —— 见模块 docstring。
            authorize(principal, name)
        except AuthorizationError:
            return _unauthorized(name)

        # 范围那一维，在**角色之后、背书之前**。夹在角色与背书之间不是折中：
        # 放在角色之前，一条既没有角色又没有工作区的令牌会拿到工作区那句话，
        # 而它的处置（重铸）与它真正的问题（没有这个角色）不对应；放在背书之后，
        # 一个错工作区的令牌就能靠「还没实现」把目录枚举出来。
        try:
            require_workspace(
                granted=principal.workspaces if principal is not None else frozenset(),
                workspace=self._workspace,
                principal=principal.name if principal is not None else "",
                where=f"工具 {name!r} 的调用",
            )
        except AuthorizationError as exc:
            return _wrong_workspace(str(exc))

        # 「本部署没有这项能力」在**背书检查之前**。反过来的话，一条没注册的工具
        # 会落进「尚未接线」那一格，而那句话的下一步是去看接线 —— 真正要做的是
        # 改部署。位置上它必须在这里：``backed`` 是一份**注册过**的名字，而没注册
        # 的工具天然不在里面，所以这两格的先后就是这两句话的先后。
        reason = self._unavailable.get(name)
        if reason is not None:
            return CallToolResult(
                content=[
                    TextContent(type="text", text=unavailable_message(name, reason))
                ],
                is_error=True,
            )

        if name not in self._backed:
            return CallToolResult(
                content=[TextContent(type="text", text=unbacked_message(name))], is_error=True
            )
        return None

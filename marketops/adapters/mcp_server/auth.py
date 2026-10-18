"""主体从传输层来（§7）。

## 这个模块只做一件事

把 ``Authorization: Bearer <token>`` 翻成一个 ``AccessToken``，或者返回 ``None``。
它不判断「能不能调这个工具」—— 那是 ``guard.py`` 的事 —— 也不判断「这个令牌
能不能进这个会话」—— 那是 SDK 的会话归属逻辑的事。

## 四个条件，全在这里判

一个令牌通过，必须同时满足：

1. **摘要命中环**（``TokenRing.grant_for``，``hmac.compare_digest``）。
2. **受众是本服务**（:func:`marketops.security.tokens.same_resource`）。
3. **没过期**（``grant.expires_at`` 对现在）。
4. 有角色 —— 这一条在装载期就成立了（``TokenRing._parse_entry`` 拒绝空角色）。

## 受众与过期为什么由本层判，而不是交给 SDK

SDK 两种都支持：``AuthSettings.validate_token_resource=True`` 时它自己比对
``AccessToken.resource``，而 ``BearerAuthBackend`` 无条件比对 ``expires_at``。
本层之所以还要自己判受众，是因为**那一格的开关同时控制着另一件事**：只要
``resource_server_url`` 是真值，``Server.streamable_http_server`` 就会挂上
``create_protected_resource_routes``，往外宣告 ``issuer_url`` 是一个授权服务器。

而本构建里**没有**授权服务器：令牌由 ``marketops mint-token`` 在带外铸出，
盘上只有摘要，``iss`` 是一个常量。宣告一个不存在的 AS，会让一个按 RFC 9728
发现文档走的客户端走进死路 —— 那不是「少配了一样东西」，是**说了一句假话**。
所以这一格必须关掉，而关掉它的代价就是受众比对要落到本层。

于是这里有两处刻意的取舍，都写在明面上：

* **受众比对用的是 SDK 自己的那条规则**（:func:`same_resource` 就是
  ``BearerAuthBackend._issued_for_this_resource`` 的同一个 ``AnyHttpUrl`` 归一化）。
  于是「本层认为这两个字符串是同一个资源」与「SDK 认为它们是同一个资源」从第一
  天起就是同一个判决 —— 将来若把这一格开回去，两边不会在某一天分开。这条与
  ``validate_token_resource`` 那条被关掉的判据**不是同一个东西**：那一条之所以
  不能要，是因为签发与校验在同一个进程里由同一份配置算出那个值，它**永远不会
  失败**；而受众来自盘上的环文件，它与本进程认定的资源是各自独立的两个值，
  一次错配是真实会发生的事（铸给另一个端口的令牌、改了 ``MARKETOPS_MCP_RESOURCE``
  之后没重铸、测试环境与生产共用一个人手写的环）。判据的价值在于它能失败，
  这一条能。
* **过期在本层也判一遍**，而 SDK 那一遍照旧开着。这是重复，不是第二个政策：
  两处读的是同一个值，所以不存在「两边判决不同」的可能，只是任何单独一边
  静默改主意时另一边还在。这与上面那条的区别是：那条是**永远为真**的判据，
  这条只是**冗余**。

## 受众错配与令牌写错在传输层上是同一个 401

这是本层唯一一处诊断缺口，而且它是刻意留下的：协议把 ``verify_token`` 的返回
类型定成 ``AccessToken | None``，那里没有放理由的位置，往响应体里加一句
「你的受众不对」也会把「本服务在等哪个受众」告诉一个未认证的调用方。

所以诊断走**已认证的**表面，而不是错误响应：``check-config`` 报本部署认定的
资源（``mcp_resource``），``--check`` 报环里实际有哪些受众（``TokenRing.audiences``），
``mint-token`` 在 stderr 的说明里报它刚写进去的那一个。错配于是是两行的比对。
"""

from __future__ import annotations

import time

from mcp.server.auth.provider import AccessToken

from ...security.tokens import TokenRing, same_resource

__all__ = ["LOCAL_ISSUER", "TokenRingVerifier"]

#: 本地环的签发者标识。常量：本阶段没有第二个签发者，因此它不来自令牌
#: （从令牌里读等于让调用方自己声明自己是谁签发的），也不来自配置（一个可配的
#: ``iss`` 会让同一个环里的令牌声称自己被不同的东西签发过）。
LOCAL_ISSUER = "marketops-local-ring"


class TokenRingVerifier:
    """实现 SDK 的 ``TokenVerifier`` 协议。

    协议是 ``async def verify_token(token) -> AccessToken | None``。返回
    ``None`` 表示「这个令牌不能用」—— 那是**正常事件**（轮换、过期、猜错、
    铸给了别的资源），由传输层翻成 401，不打堆栈。

    Args:
        ring: 装载好的令牌环。摘要比对在 ``TokenRing.grant_for`` 里。
        resource_url: 本服务认定的资源指示符（RFC 8707）。令牌的受众必须与它
            指同一个资源。它由组合根算出来（见 ``server.resource_identifier``），
            **不是**在这里从某个默认值推的 —— 一个自己推出来的资源地址，会在
            部署换了对外地址之后继续用旧的比，而症状是所有令牌 401。
    """

    def __init__(self, ring: TokenRing, *, resource_url: str) -> None:
        self._ring = ring
        self._resource_url = resource_url

    async def verify_token(self, token: str) -> AccessToken | None:
        grant = self._ring.grant_for(token)
        if grant is None:
            return None
        if not same_resource(grant.audience, self._resource_url):
            return None
        if grant.expires_at <= int(time.time()):
            return None
        return AccessToken(
            token=token,
            client_id=grant.principal,
            # 角色翻成带命名空间的 scope。翻不出来的 scope 不会到达这里 ——
            # 令牌环装载期就拒绝了不认识的角色名。
            scopes=sorted(role.scope for role in grant.roles),
            # 如实回报环里那个时刻，而不是再算一遍。SDK 的 ``BearerAuthBackend``
            # 会拿它再判一次（``expires_at < int(time.time())``），那一遍与本层
            # 这一遍读的是同一个值 —— 这是冗余，不是第二个政策。
            expires_at=grant.expires_at,
            # 报告**令牌自己的**受众，而不是本服务认定的那一个。填后者会让
            # ``AccessToken`` 上这一格变成一句永远与 ``resource_server_url``
            # 相同的话，将来谁把 ``validate_token_resource`` 打开，那条判据就
            # 成了一句恒真的真话 —— 正是本模块 docstring 里说的那个形状。
            resource=grant.audience,
            subject=grant.principal,
            # 工作区走 **claims**，不走 scopes。两条理由：
            #
            # * scopes 是「准做什么」，由 ``roles_from_scopes`` 翻成角色；把范围
            #   塞进去会让同一个字段承担两件事，而 ``roles_from_scopes`` 对不认识
            #   的 scope 是**静默忽略**的 —— 于是一个写错的 ``marketops:workspace:x``
            #   会安静地变成「哪都不能动」，症状与一次权限 bug 完全一样。
            # * claims 是协议留给「这条令牌自带的、本服务自己的事实」的位置，
            #   而工作区正是这样一件事：它由 ``mint-token`` 铸进环，不参与 OAuth
            #   的 scope 协商。
            #
            # 这里**原样**传递环里的值，不在这里做判定 —— 判定在 ``guard.py``，
            # 那里有工具名、有本次调用，而这里只有令牌。
            claims={"iss": LOCAL_ISSUER, "workspaces": sorted(grant.workspaces)},
        )

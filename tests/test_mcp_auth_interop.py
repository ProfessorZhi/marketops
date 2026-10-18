"""做「认证」这一格的两半在**传输层**上是否真的接上了（E3）。

``tests/test_security_tokens.py`` 证明令牌环读得对。``tests/test_mcp_isolation.py``
证明角色隔离挂在每一次调用上。本文件证明两半之间的那条线是真的：一个
``Authorization: Bearer`` 头，经过 SDK 的中间件，变成一次有主体、有 scope、
有受众、有有效期的调用 —— 或者变成一个 401。

## 为什么这一层必须被真的走一遍

上面那两个文件都可以全绿，而这一层是断的：

* 令牌环读得对，但 ``build_server`` 忘了把 verifier 交给 SDK ⇒ 所有令牌都进得来
  （一个「谁都不许」的部署变成了「谁都行」）；
* 角色隔离挂在调用上，但 scope 在 ``AccessToken`` 那一格被丢掉了 ⇒ 隔离在执行器
  里，而执行器收不到输入；
* 受众比对写对了，但组合根没把它算出来的资源递给 verifier ⇒ 每条令牌都 401，
  而环、策略、工具看起来全都正常。

三种断法都只在**真的走一遍 ASGI + 真的带一个 Bearer 头**的时候才显形。这就是
``tests/mcp_harness.py`` 里那句「一个被直接调用的工具函数绕过了整条权限链」的
同一件事，只是这次要绕过的更靠前：连 ``verify_token`` 都不许直接调 ——
除了下面那条明说了是单元判据的。

## 这一轮新加的东西，各自都有正控

**停止宣告一个不存在的授权服务器。** 之前 ``resource_server_url`` 是真值，于是
SDK 会挂上 RFC 9728 的发现文档路由，往外说「本资源由 <某个 AS> 签发」。本构建里
没有那个 AS（令牌是 ``mint-token`` 在带外铸的，盘上只有摘要）。所以这条要从
**两个方向**钉：路由表里没有任何一条发现路由，**以及** 401 的
``WWW-Authenticate`` 里没有 ``resource_metadata=`` 指针。只测后者会被一个
「路由在、但中间件没接上那个指针」的实现满足 —— 而客户端照样能 GET 到那份文档。

**受控的预发行 bearer-token 模式：主体、scope、受众、有效期。** 四格各自要能从
一个真头走到 ``AccessToken`` 上。受众与有效期是这一轮新增的，而它们的失败形态是
**静默**的：受众那一格若不判，一条铸给别的端口的令牌照样能读证据；有效期那一格
若不判，一条到期两年的令牌照样能读。所以每条判据都带一个正控 —— **同一个服务
实例**、同一份环，换一个令牌就通过。少了正控，一个「拒绝一切」的实现全绿。

## 401 里没有理由，这是协议定的

``verify_token`` 的返回类型是 ``AccessToken | None``，那里没有放理由的位置。
于是「受众不对」与「令牌写错」在传输层上是同一个 401 —— 这是刻意的，因为往响应
体里写理由会把「本服务在等哪个受众」告诉一个未认证的调用方。所以本文件**不**
断言 401 能区分这两者；它断言的是相反的事：**401 的话术里不含任何关于环的信息**，
而诊断走已认证的表面（``--check`` 的 ``mcp.resource`` / ``mcp.audiences``，
见 ``tests/test_mcp_entry.py``）。
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import httpx2
import pytest

from marketops.config import Settings
from marketops.security.catalog import TOOL_CATALOG, Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import (
    BASE_URL,
    MCP_PATH,
    MCP_URL,
    RING_AUDIENCE,
    drive,
    ring_entry,
    ring_payload,
)

#: 一个**不在环里**但形态与真的完全一样的令牌（64 位十六进制）。用它而不是
#: ``"garbage"``：一个「只认长度」的实现会拒绝后者、收下前者，而那种实现在真实
#: 世界里等于没有认证。
UNKNOWN_TOKEN = "0" * 64

#: 一个已经过去的时刻与一个远在未来的时刻。写死而不是「现在 ∓ N」：有效期那两条
#: 判据的失败模式之一是「根本没读时钟」，而一个随运行时刻漂移的值会让同一个环的
#: 两次运行不可比对。
PAST = "2020-01-01T00:00:00Z"
FUTURE = "2999-01-01T00:00:00Z"

#: 另一个服务的资源标识符。用它当「铸给了别的端点的令牌」—— 端口不同是最便宜的
#: 那种错配，也是真实中最常见的一种（测试环境与生产共用一个人手写的环）。
ELSEWHERE = "http://127.0.0.1:9999/mcp"


# ----------------------------------------------------------------------
# 脚手架
# ----------------------------------------------------------------------
def _ring_for(tmp_path: Path, *entries) -> tuple[TokenRing, dict[str, str]]:
    """写一份环，返回 ``(环, {记号: 令牌})``。

    每个条目是 ``(主体, 角色, 记号名, 覆盖项)``。令牌本身是铸出来的，用例里用一个
    记号名（``"good"`` / ``"expired"`` / ``"elsewhere"``）引用它 —— 于是那条用例
    读起来是「拿那一条过期的令牌去连」，而不是三个难以辨认的十六进制。
    """
    tokens: dict[str, str] = {}
    items = []
    for principal, role, label, overrides in entries:
        token, digest = mint(principal, [Role(role)])
        tokens[label] = token
        items.append(ring_entry(digest, principal, [role], **overrides))
    path = tmp_path / "tokens.json"
    path.write_text(json.dumps(ring_payload(*items), ensure_ascii=False), encoding="utf-8")
    return TokenRing.from_file(path), tokens


def _instance(settings: Settings, ring: TokenRing):
    """一个装好的服务实例。同一个实例可以被驱动任意多次 —— 那正是正控要的。

    ``drive_with_settings`` 每次都会新建一个实例，用它做正控时，正控证明的是
    「这份**配置**能通过」，而不是「这个**实例**没有把所有令牌都拒掉」。两者在
    大多数失败下等价，但在这条判据最关心的那种失败下不等价：一个把环读空了、
    或者只在第一次之后拒绝的实现会被前者放过。
    """
    from marketops.adapters.mcp_server.server import build_server

    return build_server(settings=settings, tokens=ring)


async def _get(app, path: str, **kwargs) -> httpx2.Response:
    """打一个裸请求，拿回完整响应（状态码 + 头 + 体）。

    不走 ``ClientSession``：被问的是**路由与中间件**，而 MCP 的会话建立会把 401
    摊成一句 ``Server returned an error response`` —— 看头与体都得绕回去。
    """
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        return await client.get(path, **kwargs)


def get(app, path: str, **kwargs) -> httpx2.Response:
    async def main() -> httpx2.Response:
        with anyio.fail_after(30.0):
            return await _get(app, path, **kwargs)

    return anyio.run(main)


# ----------------------------------------------------------------------
# 一、不再宣告一个不存在的授权服务器
# ----------------------------------------------------------------------
def test_the_route_table_has_no_authorization_metadata(settings, tmp_path: Path) -> None:
    """路由表里没有任何一条「关于授权的元数据」路由。

    只断言两条具体路径返回 404 是不够的：SDK 换一个 well-known 前缀、或者把路由
    挂到 ``/mcp`` 后面，那条断言就与事实无关了，而它照旧绿。这里问的是一个更宽的
    问题 —— 这个应用暴露的路径里，有没有一条在讲**授权**。

    正控在末尾：同一张路由表里必须有 ``/mcp``。少了它，一个「什么都没挂上」的
    应用也会满足上面那条。
    """
    ring, _ = _ring_for(tmp_path, ("claude-control", "controller", "good", {}))
    app = _instance(settings, ring).streamable_http_app(host="127.0.0.1")

    paths = [getattr(route, "path", "") for route in app.routes]
    offenders = [p for p in paths if "well-known" in p or "oauth" in p.lower()]

    assert paths, "路由表是空的 —— 下面那条「没有发现路由」是空言"
    assert not offenders, (
        f"本服务在对外宣告授权元数据（{offenders}），而它背后**没有**授权服务器："
        "令牌是 mint-token 在带外铸的，盘上只有摘要。"
    )
    assert any(p.startswith(MCP_PATH) for p in paths), "正控：这张表里连 /mcp 都没有"


def test_a_401_carries_no_pointer_to_a_metadata_document(settings, tmp_path: Path) -> None:
    """401 的 ``WWW-Authenticate`` 里不许有 ``resource_metadata=`` 指针。

    这一条与上一条是**同一句话的两个方向**，而两个都要：路由在而指针不在，客户端
    拿不到那份文档（无害，但白留一条路由）；指针在而路由不在，客户端按 RFC 9728
    去找一个 404 —— 那是「说了一句假话」的标准形态。

    同时断言 ``error="invalid_token"``：一个既不宣告元数据、也不说清发生了什么的
    401，会让调用方分不清「认证没过」与「这个端点根本不在这」。
    """
    ring, _ = _ring_for(tmp_path, ("claude-control", "controller", "good", {}))
    app = _instance(settings, ring).streamable_http_app(host="127.0.0.1")

    response = get(app, MCP_PATH, headers={"Authorization": f"Bearer {UNKNOWN_TOKEN}"})

    assert response.status_code == 401
    challenge = response.headers.get("www-authenticate", "")
    assert "invalid_token" in challenge
    assert "resource_metadata" not in challenge, (
        "401 指向了一份本服务并不发布的发现文档 —— 客户端会去 GET 一个 404"
    )


@pytest.mark.parametrize(
    "path",
    [
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/.well-known/oauth-authorization-server",
        "/.well-known/openid-configuration",
    ],
)
def test_the_discovery_paths_are_simply_not_there(settings, tmp_path: Path, path: str) -> None:
    """行为上的那一半：这些路径就是不存在，而不是存在但拒绝。

    「存在但 401」在这里是不够的 —— 一个 401 说明**有一条路由**，而按 RFC 9728
    找到一条路由的客户端会继续按它走。所以期望是 404。

    四条路径里只有前两条是 SDK 会挂的（后两条是另外两个规范的常见落点）。留着
    它们不是防 SDK，是给「有人为了兼容某个客户端手工加了一条发现路由」这件事一个
    会被发现的形状 —— E3 的处置是「不宣告」，不是「换一种宣告方式」。
    """
    ring, _ = _ring_for(tmp_path, ("claude-control", "controller", "good", {}))
    app = _instance(settings, ring).streamable_http_app(host="127.0.0.1")

    assert get(app, path).status_code == 404


# ----------------------------------------------------------------------
# 二、四格：主体、scope、受众、有效期
# ----------------------------------------------------------------------
def test_the_verifier_puts_all_four_fields_on_the_access_token(tmp_path: Path) -> None:
    """``verify_token`` 的四格各自落在 ``AccessToken`` 的哪一格上。

    这一条是本文件里唯一直接调 verifier 的判据，理由：它测的是**翻译**本身，
    而其余三条要在传输层上观察同样的东西会各自多绕一层（受众与有效期在传输层上
    都只是「401」这一个形状，分不出来）。翻译错了的表现是下面的端到端判据
    一起红，而那时说不清是哪一格 —— 这一条负责说清。

    两处刻意的取值：

    * ``scopes`` 是**角色**的 scope 集合，不是角色名。README 与目录里 scope 是
      对外的那一层（``catalog.py`` 的 ``Role.scope``），把角色名直接当 scope
      会让目录改一次名就动到线上调用方的凭据。
    * ``resource`` 报的是**令牌自己的**受众，不是本服务认定的那一个。这一格的意
      义是「这个令牌自称发给谁」，SDK 的 ``_issued_for_this_resource`` 就是拿它
      与 ``resource_server_url`` 比的；报成「本服务是谁」会让那一比恒真。
    """
    ring, tokens = _ring_for(
        tmp_path,
        ("worker-1", "researcher", "good", {"expires_at": "2027-03-01T00:00:00Z"}),
        ("claude-control", "controller", "elsewhere", {"audience": ELSEWHERE}),
    )
    verifier = _verifier(ring, RING_AUDIENCE)

    good = anyio.run(verifier.verify_token, tokens["good"])
    assert good is not None
    assert good.subject == "worker-1"
    assert good.client_id == "worker-1"
    assert good.scopes == [Role.RESEARCHER.scope]
    assert good.resource == RING_AUDIENCE
    assert good.expires_at == 1803859200, "2027-03-01T00:00:00Z"

    other = anyio.run(verifier.verify_token, tokens["elsewhere"])
    assert other is None, "铸给另一个资源的令牌通过了本实例的验证"


def _verifier(ring: TokenRing, resource_url: str):
    from marketops.adapters.mcp_server.auth import TokenRingVerifier

    return TokenRingVerifier(ring, resource_url=resource_url)


@pytest.mark.parametrize(
    ("headers", "label"),
    [
        ({}, "no-header"),
        ({"Authorization": "Basic dXNlcjpwYXNz"}, "wrong-scheme"),
        ({"Authorization": "Bearer"}, "bare-bearer"),
        ({"Authorization": f"Bearer {UNKNOWN_TOKEN}"}, "unknown-token"),
        ({"Authorization": f"bearer {UNKNOWN_TOKEN}"}, "lowercase-scheme"),
    ],
    ids=["no-header", "wrong-scheme", "bare-bearer", "unknown-token", "lowercase-scheme"],
)
def test_nothing_but_a_bearer_token_in_the_ring_gets_in(
    settings, tmp_path: Path, headers: dict[str, str], label: str
) -> None:
    """五种「不是一条环里的 Bearer 令牌」都要拿到 401。

    ``lowercase-scheme`` 是刻意的一条：RFC 6750 说 scheme 大小写不敏感，而
    ``BearerAuthBackend`` 也确实是 ``lower().startswith("bearer ")``。把它列在
    这里是为了让「这个实现在这一格上遵从了协议」成为一条被判据钉住的事 ——
    一个自己手写 ``startswith("Bearer ")`` 的中间件会在这里红，而它拒绝的恰好
    是 RFC 要求它接受的那一种。

    ``bare-bearer``（只有 scheme、没有令牌）与 ``wrong-scheme`` 走的是同一条
    分支（没有 ``bearer `` 前缀），所以它们的期望形状一致：401，而不是 500。
    """
    ring, _ = _ring_for(tmp_path, ("claude-control", "controller", "good", {}))
    app = _instance(settings, ring).streamable_http_app(host="127.0.0.1")

    assert get(app, MCP_PATH, headers=headers).status_code == 401


def test_a_token_in_the_ring_gets_a_session_and_the_tool_list(settings, tmp_path: Path) -> None:
    """正控：同一份环里那条**好**的令牌必须真的连上，并看见工具。

    这一条是上面那条与下面三条的共同正控。它与它们的区别只有令牌那一个变量 ——
    所以任何「这个实例拒绝一切」的失败都会先在这里红，而不是让下面几条各自
    以一种看似有道理的方式红（「过期被拒了」在「所有人都被拒」下同样成立）。

    断言 ``tool_names`` 非空而不是等于某个集合：**哪些**工具属于
    ``test_mcp_isolation.py``，这里要的只是「这条路径通到了工具表」。
    """
    ring, tokens = _ring_for(tmp_path, ("claude-control", "controller", "good", {}))
    server = _instance(settings, ring)

    outcome = drive(server=server, token=tokens["good"])

    assert not outcome.handshake_error, outcome.handshake_error
    assert outcome.tool_names, "连上了却看不见任何工具"
    assert 401 not in outcome.statuses, outcome.statuses


def test_an_expired_token_is_401_while_its_unexpired_sibling_works(
    settings, tmp_path: Path
) -> None:
    """有效期那一格：过期即 401，同一个实例上没过期的**照常**。

    两个主体、两条令牌、一份环、一个实例，只有时刻不同。这一条之所以要正控在
    **同一个实例**上，是因为「有效期被当成了装载条件」也是一种很自然的写法 ——
    那种写法下，含一条过期条目的环会让服务起不来（或者整份环被丢掉），而单独看
    「过期令牌被拒」是绿的。

    过期是**每请求**的判决，不是装载期的：装载期的判决在
    ``tests/test_security_tokens.py`` 有两条，一条证明远未来条目装得下（上界不在
    装载器里），一条证明已经过期的条目装得下。

    **这一条不区分两个执行者，也做不到。** 有效期在两处被判：``auth.py`` 一处，
    SDK 的 ``BearerAuthBackend`` 一处。实测把 ``auth.py`` 那一处换成 ``if False``
    之后本文件全绿 —— 因为 SDK 那一处还在判。这不是判据太弱，是两个执行者的
    判决本来就必须相同（读的是同一个 ``expires_at``）；要测的「已经到期的令牌
    进不来」这件事两处都满足。哪一处是被测的那一个，``auth.py`` 的 docstring
    已经写明（这一处是**冗余**，与那条「永远为真所以被关掉」的
    ``validate_token_resource`` 不是一回事）。
    """
    ring, tokens = _ring_for(
        tmp_path,
        ("worker-1", "researcher", "expired", {"expires_at": PAST}),
        # 同一个主体名不能出现两次（装载期禁止），所以正控换一个主体。
        ("claude-control", "controller", "good", {"expires_at": FUTURE}),
    )
    server = _instance(settings, ring)

    stale = drive(server=server, token=tokens["expired"])
    assert stale.handshake_error, "一条 2020 年就到期的令牌连上了"
    assert 401 in stale.statuses, f"没看到 401，看到的是 {stale.statuses}"

    fresh = drive(server=server, token=tokens["good"])
    assert not fresh.handshake_error, fresh.handshake_error
    assert fresh.tool_names, "正控：同一份环里那条没过期的令牌也进不来"


def test_a_token_minted_for_another_resource_is_401_while_the_right_one_works(
    settings, tmp_path: Path
) -> None:
    """受众那一格：铸给别的端点的令牌一律 401，铸给本端点的照常。

    这是「真实受众校验」这条要求在本文件里的执行器。一个不判受众的实现会让
    ``9999`` 那条令牌读到本服务的证据 —— 而两份环在今天长得完全一样。

    错配的取值选**端口不同**而不是路径不同：端口是最便宜的那种错（测试环境与
    生产共用一个人手写的环、反向代理换了一个对外端口），而它也是最容易被一个
    「比 host」的实现放过的。路径那一条在 ``test_security_tokens.py`` 的
    ``same_resource`` 表里。
    """
    ring, tokens = _ring_for(
        tmp_path,
        ("claude-control", "controller", "elsewhere", {"audience": ELSEWHERE}),
        ("worker-1", "researcher", "good", {"audience": MCP_URL}),
    )
    server = _instance(settings, ring)

    foreign = drive(server=server, token=tokens["elsewhere"])
    assert foreign.handshake_error, "铸给 9999 的令牌连上了本服务"
    assert 401 in foreign.statuses, f"没看到 401，看到的是 {foreign.statuses}"

    local = drive(server=server, token=tokens["good"])
    assert not local.handshake_error, local.handshake_error
    assert local.tool_names, "正控：同一份环里受众正确的那条也进不来"


def test_the_scopes_reach_the_tool_table(settings, tmp_path: Path) -> None:
    """subject → scope → 工具表：三个角色看见的集合必须不同，且各自与目录一致。

    这一条与 ``test_mcp_isolation.py`` 的重叠是刻意的、也是有限的：那边证明的是
    **隔离本身**（含伪造参数、含拒绝措辞不可区分），这一条只走一遍
    「环里的角色名 → scope → 工具表」这条线。它要抓的是一种只在传输层上才显形的
    断法：scope 在 ``AccessToken`` 那一格丢了，于是两个角色拿到同一张表。

    期望值从 ``TOOL_CATALOG`` 推，不写死名字 —— 写死会让这条判据在目录变化时
    以「测试过期了」的样子红，而不是以「隔离坏了」的样子红。
    """
    ring, tokens = _ring_for(
        tmp_path,
        ("claude-control", "controller", "control", {}),
        ("worker-1", "researcher", "worker", {}),
    )
    server = _instance(settings, ring)

    as_controller = drive(server=server, token=tokens["control"])
    as_worker = drive(server=server, token=tokens["worker"])

    assert not as_controller.handshake_error, as_controller.handshake_error
    assert not as_worker.handshake_error, as_worker.handshake_error

    expected_control = {n for n, s in TOOL_CATALOG.items() if Role.CONTROLLER in s.roles}
    expected_worker = {n for n, s in TOOL_CATALOG.items() if Role.RESEARCHER in s.roles}
    assert expected_control != expected_worker, "两个角色在目录里就是同一张表，这条判据没有分辨力"

    assert set(as_controller.tool_names) == expected_control
    assert set(as_worker.tool_names) == expected_worker


# ----------------------------------------------------------------------
# 三、401 里没有理由，也没有凭据
# ----------------------------------------------------------------------
@pytest.mark.parametrize("label", ["unknown", "expired", "elsewhere"])
def test_a_refusal_never_echoes_the_credential(settings, tmp_path: Path, label: str) -> None:
    """被拒的那条令牌不许出现在响应体里 —— 三种被拒的理由都试一遍。

    一条回显了令牌的 401 会把一个**可用的秘密**送给一个未经认证的调用方：它
    只需要发一个自己编的令牌，就能从回显里读回……自己编的那一个。真正危险的是
    这条路径上会不会有别的格式化（把整个 ``Authorization`` 头抄进错误详情、
    或者把令牌当 ``resource`` 打印出来），所以三种理由都试：三种走的是不同的
    分支，而同一个抄写动作可能在其中一个里发生。
    """
    ring, tokens = _ring_for(
        tmp_path,
        ("worker-1", "researcher", "expired", {"expires_at": PAST}),
        ("claude-control", "controller", "elsewhere", {"audience": ELSEWHERE}),
    )
    app = _instance(settings, ring).streamable_http_app(host="127.0.0.1")
    token = UNKNOWN_TOKEN if label == "unknown" else tokens[label]

    response = get(app, MCP_PATH, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    body = response.text
    assert token not in body, "401 回显了令牌本身"
    assert token[:16] not in body, "401 回显了令牌的前缀"
    # 响应里也不该出现「环里有什么」这类信息：拒绝的理由只对本服务有意义。
    assert MCP_URL not in body, "401 里写出了本服务认定的资源 —— 那是一条免费的情报"

"""令牌环：主体从哪来，以及为什么它在**仓外**（§7 / §0）。

## 这是「认证」那一半

``tests/test_security_catalog.py`` 测的是「拿到主体之后怎么判」。本文件测的是
主体**从哪来**。两者之间那条界线是整个 §7 的着力点：主体只能由传输层
（HTTP ``Authorization`` 头）确立，绝不能由本次调用的参数确立。

## 为什么文件里存的是摘要，不是令牌

令牌环读的是 ``MARKETOPS_MCP_TOKENS`` 指向的那个文件。那个路径由部署方给出、
**必须落在仓库之外** —— §0 的硬约束，也是本文件所有用例都往 ``tmp_path`` 写
而不能引用任何一个真实路径的原因。文件里放 ``sha256(token)`` 而不是 token
本身，因此：

* 这个文件可以被备份、被 ``cat``、被误贴进工单，而**不会**泄露一个可用的凭据；
* 反过来，摘要也无法被还原成令牌。这正是「令牌只显示一次」的代价，
  它被写在明处而不是被藏起来。

## 两个失败模式必须区分开

* **文件缺失 / 解析失败 / 角色名拼错** ⇒ 拒绝装载（``ConfigError``）。
  这是部署错误，必须在启动时炸，而不是让服务起来之后什么都不许调。
* **令牌不匹配** ⇒ 返回 ``None``，由上层翻译成 401。
  这是正常事件（过期、轮换、猜错），不是配置错误，不该打堆栈。

## 格式 2 两格的判据分三层，本文件钉的是「装载」那一层

``audience`` 与 ``expires_at`` 各自有三处不同的判决，它们**不能混成一条**：

* 装载期（本文件）：能不能读。读不出来的地方拒绝装载。
* 铸造期（``parse_duration`` / ``mint-token``）：合不合理。上界住在这里，
  理由写在 ``tokens.MAX_TOKEN_LIFETIME_SECONDS``。
* 每次请求（``tests/test_mcp_auth_interop.py``）：这个令牌发给的是不是本服务、
  有没有过期。

本文件里有两条判据专门守着这三层之间的**缝**：一条证明装得下一份「有效期远超
上界」的环（上界不在装载器里），一条证明一份**已经过期**的环照样装载得起来
（过期是请求期的事，装载器不管时间）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketops.domain.errors import ConfigError
from marketops.security.catalog import Role
from marketops.security.tokens import (
    MAX_TOKEN_LIFETIME_SECONDS,
    TokenGrant,
    TokenRing,
    digest_of,
    format_timestamp,
    mint,
    normalize_resource,
    parse_duration,
    same_resource,
)
from tests.mcp_harness import RING_AUDIENCE, RING_WORKSPACE, ring_entry, ring_payload


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def ring_path(tmp_path: Path) -> Path:
    return tmp_path / "tokens.json"


# ----------------------------------------------------------------------
# 正常路径
# ----------------------------------------------------------------------
def test_a_known_token_resolves_to_its_principal_and_roles(ring_path: Path) -> None:
    token, digest = mint("claude-control", [Role.CONTROLLER])
    _write(ring_path, ring_payload(ring_entry(digest, "claude-control", ["controller"])))

    ring = TokenRing.from_file(ring_path)
    grant = ring.grant_for(token)
    assert grant is not None
    assert grant.principal == "claude-control"
    assert grant.roles == frozenset({Role.CONTROLLER})


def test_both_roles_in_one_file(ring_path: Path) -> None:
    control, d1 = mint("claude-control", [Role.CONTROLLER])
    worker, d2 = mint("worker-1", [Role.RESEARCHER])
    _write(
        ring_path,
        ring_payload(
            ring_entry(d1, "claude-control", ["controller"]),
            ring_entry(d2, "worker-1", ["researcher"]),
        ),
    )

    ring = TokenRing.from_file(ring_path)
    assert ring.grant_for(control).roles == frozenset({Role.CONTROLLER})
    assert ring.grant_for(worker).roles == frozenset({Role.RESEARCHER})


def test_mint_returns_a_fresh_unpredictable_token_each_time() -> None:
    first, d1 = mint("worker-1", [Role.RESEARCHER])
    second, d2 = mint("worker-1", [Role.RESEARCHER])
    assert first != second, "两次铸造拿到同一个令牌说明随机源没有被用上"
    assert d1 != d2
    assert digest_of(first) == d1


def test_digest_is_sha256_hex() -> None:
    _, digest = mint("worker-1", [Role.RESEARCHER])
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")


# ----------------------------------------------------------------------
# 格式 2 的两格：读了要能拿到，而且拿到的是文件里写的那个
# ----------------------------------------------------------------------
def test_the_audience_and_the_expiry_reach_the_grant(ring_path: Path) -> None:
    """**这两格要真的进 ``TokenGrant``。** 装载器收下它们而对象里没有，
    是一个不会被任何一条「拒绝坏文件」的判据发现的失败 —— 它只会让
    ``verify_token`` 拿不到可以比对的值，于是那两格又变成了摆设。

    ``expires_at`` 断言的是那个时刻的 epoch 秒，而不是「一个整数」：写进文件
    的是 ISO-8601，读出来要能对上一个具体时刻，否则「到什么时候为止」这件事
    在两次读取之间可以不一样。
    """
    token, digest = mint("worker-1", [Role.RESEARCHER])
    _write(
        ring_path,
        ring_payload(ring_entry(digest, "worker-1", ["researcher"], expires_at="2027-03-01T00:00:00Z")),
    )

    grant = TokenRing.from_file(ring_path).grant_for(token)

    assert grant is not None
    assert grant.audience == RING_AUDIENCE
    assert grant.expires_at == 1803859200, "2027-03-01T00:00:00Z 的 epoch 秒"
    assert format_timestamp(grant.expires_at) == "2027-03-01T00:00:00Z"


def test_the_audience_is_normalized_on_the_way_in(ring_path: Path) -> None:
    """受众按 URL 归一化后**写进对象**（大小写与默认端口归一、尾斜杠保留）。

    归一化在读入时做（而不是每次比对时做）的一个好处是：``--check`` 报出来的
    就是比对时用的那个值 —— 一个「报一种写法、比另一种写法」的实现会让运维
    拿两份看起来一样的东西去解释一次 401。
    """
    token, digest = mint("worker-1", [Role.RESEARCHER])
    _write(
        ring_path,
        ring_payload(ring_entry(digest, "worker-1", ["researcher"], audience="HTTP://LocalHost:80/mcp")),
    )

    grant = TokenRing.from_file(ring_path).grant_for(token)

    assert grant is not None
    assert grant.audience == "http://localhost/mcp"


def test_a_ring_written_by_the_mint_path_reads_back_as_written(ring_path: Path) -> None:
    """``mint-token`` 写出来的东西，装载器读得回来 —— 两半之间没有缝。

    这一条是下面那些「拒绝坏文件」的判据的**对照**：只测拒绝时，一个把所有文件
    都拒掉的装载器同样全绿，而它的症状是服务起不来。
    """
    from marketops.security.tokens import add_token_to_ring

    token, _ = add_token_to_ring(
        ring_path,
        principal="worker-1",
        roles=[Role.RESEARCHER],
        audience=RING_AUDIENCE,
        workspaces=[RING_WORKSPACE],
        expires_in_seconds=parse_duration("12h"),
    )

    ring = TokenRing.from_file(ring_path)
    grant = ring.grant_for(token)

    assert grant is not None
    assert grant.audience == RING_AUDIENCE
    assert ring.audiences == (RING_AUDIENCE,)
    # 盘上只有摘要：令牌本身不在文件里（下面还有一条专测这个）。
    assert token not in ring_path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# 装载期与铸造期的分界：上界不在装载器里
# ----------------------------------------------------------------------
def test_a_ring_whose_expiry_is_far_past_the_minting_ceiling_still_loads(ring_path: Path) -> None:
    """有效期远超一年（铸造上界）的环**照样装载**。

    这一条钉的是那条分界本身。把上界也写进装载器看起来更严 —— 而它的代价是：
    一次策略调整（比如把一年改成九十天）会让**既有的**全部凭据变成装载错误，
    于是服务起不来，错误信息还指向文件格式。那时候排查方向会落在环文件上，
    而真正变了的是代码里的一个常量。

    于是上界落在 ``parse_duration``（铸造期），装载器对任何读得出来的时刻一视同仁。
    """
    _, digest = mint("worker-1", [Role.RESEARCHER])
    _write(
        ring_path,
        ring_payload(ring_entry(digest, "worker-1", ["researcher"], expires_at="2999-01-01T00:00:00Z")),
    )

    ring = TokenRing.from_file(ring_path)

    assert ring.principal_names == ("worker-1",)


def test_a_ring_whose_expiry_has_already_passed_still_loads(ring_path: Path) -> None:
    """**已经过期**的条目照样装载得起来。

    过期是**请求期**的事（``TokenRingVerifier`` 拿它与现在比，见
    ``tests/test_mcp_auth_interop.py`` 那条端到端判据）。在装载期就把它拒掉，
    会让一次到点失效表现为「服务起不来」—— 同一个事实的两种代价完全不同：
    一种是某个令牌 401，另一种是整个服务面下线。

    而且装载器根本不该读时钟：一个会读时间的装载器，它的行为会随运行时刻变化
    而文件一个字节都没变，正是最难复现的那类故障。
    """
    _, digest = mint("worker-1", [Role.RESEARCHER])
    _write(
        ring_path,
        ring_payload(ring_entry(digest, "worker-1", ["researcher"], expires_at="2020-01-01T00:00:00Z")),
    )

    ring = TokenRing.from_file(ring_path)

    assert ring.principal_names == ("worker-1",)


# ----------------------------------------------------------------------
# 不匹配：正常事件，返回 None
# ----------------------------------------------------------------------
_UNKNOWN = (
    "",
    "not-a-token",
    "x" * 200,
)


@pytest.mark.parametrize("token", _UNKNOWN, ids=["empty", "garbage", "long"])
def test_unknown_tokens_resolve_to_nothing(ring_path: Path, token: str) -> None:
    _, digest = mint("worker-1", [Role.RESEARCHER])
    _write(ring_path, ring_payload(ring_entry(digest, "worker-1", ["researcher"])))
    assert TokenRing.from_file(ring_path).grant_for(token) is None


def test_a_near_miss_is_still_a_miss(ring_path: Path) -> None:
    """前缀相同、只差一位的令牌必须落空。

    这一条盯的是「比较退化成前缀比较」这类实现错误 —— 它会通过所有
    「完全不同的令牌被拒绝」的测试。
    """
    token, digest = mint("worker-1", [Role.RESEARCHER])
    _write(ring_path, ring_payload(ring_entry(digest, "worker-1", ["researcher"])))
    ring = TokenRing.from_file(ring_path)
    assert ring.grant_for(token + "_") is None
    assert ring.grant_for(token[:-1]) is None
    assert ring.grant_for(token.upper()) is None


# ----------------------------------------------------------------------
# 装载期失败：配置错误，必须炸
# ----------------------------------------------------------------------
def test_a_missing_file_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        TokenRing.from_file(tmp_path / "definitely-not-here.json")
    message = str(excinfo.value)
    # 报错必须给出**可操作的下一步**，而不是只说文件不存在。
    assert "MARKETOPS_MCP_TOKENS" in message


def test_malformed_json_is_a_configuration_error(ring_path: Path) -> None:
    ring_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        TokenRing.from_file(ring_path)


#: 一份**完整的**格式 3 条目，用来在每条畸形样本里只坏一处。
_GOOD = {
    "digest": "a" * 64,
    "principal": "p",
    "roles": ["controller"],
    "audience": "http://127.0.0.1:8787/mcp",
    "workspaces": [RING_WORKSPACE],
    "expires_at": "2999-01-01T00:00:00Z",
}


def _without(*keys: str) -> dict[str, object]:
    return {key: value for key, value in _GOOD.items() if key not in keys}


def _with(**overrides: object) -> dict[str, object]:
    return {**_GOOD, **overrides}


#: 当前格式版本。表里的版本号写它，**不写一个数字**：上一轮这里写的是 ``2``，
#: 而版本升到 3 之后那些样本仍然会抛 ``ConfigError`` —— 只不过抛的是版本不符，
#: 于是整张表变成了一组「版本号不对」的重复样本，而它看起来全绿。
#: 这是本仓最怕的那种假绿，所以版本号在这一张表里只出现一次。
_THIS_VERSION = 3

_MALFORMED: tuple[tuple[str, object], ...] = (
    ("not-an-object", [1, 2, 3]),
    ("missing-tokens-key", {"version": _THIS_VERSION}),
    ("tokens-not-a-list", {"version": _THIS_VERSION, "tokens": {"a": 1}}),
    ("entry-not-an-object", {"version": _THIS_VERSION, "tokens": ["hello"]}),
    ("entry-missing-digest", {"version": _THIS_VERSION, "tokens": [_without("digest")]}),
    ("entry-missing-principal", {"version": _THIS_VERSION, "tokens": [_without("principal")]}),
    ("entry-missing-roles", {"version": _THIS_VERSION, "tokens": [_without("roles")]}),
    ("digest-not-hex", {"version": _THIS_VERSION, "tokens": [_with(digest="zz")]}),
    ("roles-empty", {"version": _THIS_VERSION, "tokens": [_with(roles=[])]}),
    ("unknown-role-name", {"version": _THIS_VERSION, "tokens": [_with(roles=["admin"])]}),
    # --- 格式 2 新增的两格：缺一不可 ---
    ("entry-missing-audience", {"version": _THIS_VERSION, "tokens": [_without("audience")]}),
    ("entry-missing-expires-at", {"version": _THIS_VERSION, "tokens": [_without("expires_at")]}),
    ("audience-not-a-url", {"version": _THIS_VERSION, "tokens": [_with(audience="/mcp")]}),
    (
        "audience-wrong-scheme",
        {"version": _THIS_VERSION, "tokens": [_with(audience="ftp://example.co.uk/mcp")]},
    ),
    ("audience-empty", {"version": _THIS_VERSION, "tokens": [_with(audience="")]}),
    ("expires-at-not-a-time", {"version": _THIS_VERSION, "tokens": [_with(expires_at="2027-03-01")]}),
    # 不带时区的时刻：要靠「本机在哪个时区」才能定位，而那条推理不在文件里。
    (
        "expires-at-without-a-zone",
        {"version": _THIS_VERSION, "tokens": [_with(expires_at="2027-03-01T00:00:00")]},
    ),
    # --- 格式 3 新增的一格：缺一不可 ---
    ("entry-missing-workspaces", {"version": _THIS_VERSION, "tokens": [_without("workspaces")]}),
    ("workspaces-empty", {"version": _THIS_VERSION, "tokens": [_with(workspaces=[])]}),
    ("workspaces-not-a-list", {"version": _THIS_VERSION, "tokens": [_with(workspaces="uk-x")]}),
    ("workspaces-not-a-string", {"version": _THIS_VERSION, "tokens": [_with(workspaces=[7])]}),
    ("workspaces-empty-string", {"version": _THIS_VERSION, "tokens": [_with(workspaces=[""])]}),
    # 非规范形式：它会是一个目录名的最后一段，而两个只差空白的写法只可能指向
    # 一个目录。校验是**拒绝**而不是规范化（规范化会把两个名字静默合并）。
    ("workspaces-not-canonical", {"version": _THIS_VERSION, "tokens": [_with(workspaces=["UK Scene"])]}),
    ("no-tokens-at-all", {"version": _THIS_VERSION, "tokens": []}),
    # 版本比当前**高**也一样拒 —— 这一格不是「低于才拒」，是「不等于就拒」：
    # 一个来自更新构建的环按本构建的格式读，会把新增的几格读成不存在。
    ("wrong-format-version", {"version": _THIS_VERSION + 1, "tokens": [_GOOD]}),
)


@pytest.mark.parametrize(("_label", "payload"), _MALFORMED, ids=[c for c, _ in _MALFORMED])
def test_malformed_rings_are_refused(ring_path: Path, _label: str, payload: object) -> None:
    """每一种形态都必须**拒绝装载**。

    尤其是 ``unknown-role-name``：一个拼错的角色名若被读成「没有角色」，
    结果是「这个主体什么都调不动」—— 一个看起来像权限 bug 的行为。
    宁可启动失败，也不要一个能起来但语义不明朗的服务。

    格式 2 的两格与格式 3 的那一格同理，而且更安静：一份受众写空、有效期缺失、
    工作区空表的环能读**通**，于是每一条令牌都「哪都能用、永远有效、哪个 Scene
    都能动」，而文件上看不出与正常环的区别。所以缺它们和缺角色名一样，属于装载失败。
    """
    _write(ring_path, payload)
    with pytest.raises(ConfigError):
        TokenRing.from_file(ring_path)


def test_the_good_sample_passes_so_the_malformed_table_is_not_vacuous(ring_path: Path) -> None:
    """``_MALFORMED`` 每一条都是「只坏一处」，所以**只坏零处**的那一份必须能读。

    没有这一条的话，``_without`` / ``_with`` 里任何一个笔误（比如某个键名写错，
    于是每条样本都缺了同一个东西）都会让上面那张表全绿 —— 而它测的就变成了
    「这个表里的每一份都读不了」，一句关于样本而不是关于代码的真话。
    """
    _write(ring_path, ring_payload(_GOOD))

    ring = TokenRing.from_file(ring_path)

    assert ring.principal_names == ("p",)


def test_a_ring_in_the_old_format_says_what_to_do_about_it(ring_path: Path) -> None:
    """格式 1 的环被拒，而报文里**写着出路**。

    格式 1 里没有受众与有效期，所以没有任何「补个默认值」的迁移是诚实的 ——
    补出来的只能是「谁都能用」或「永不过期」。这件事必须由报错说出来：它是
    升级那天唯一会被人读到的地方，而读到它的人此刻手上正拿着一份起不来的部署。
    一条只说「版本不对」的报文会让他去找一个不存在的迁移命令。

    摘要也无法换算这一点同样要说：``--rotate`` 在旧文件上一样会失败（它要先
    读得懂旧环），所以出路是「移开、重铸」，不是「就地换一个开关」。
    """
    _write(ring_path, {"version": 1, "tokens": [{"digest": "a" * 64, "principal": "p", "roles": ["controller"]}]})

    with pytest.raises(ConfigError) as excinfo:
        TokenRing.from_file(ring_path)

    message = str(excinfo.value)
    assert "mint-token" in message, "要说出用哪条命令重铸"
    assert "audience" in message and "expires_at" in message, "要说清旧格式缺了哪两格"
    assert "移" in message, "要说出旧文件该被移开而不是就地改"


def test_two_principals_may_not_share_a_name(ring_path: Path) -> None:
    """一个主体只能有一个角色集。

    这不是洁癖：会话归属（``streamable_http_manager`` 的
    ``_session_owners``）按 ``(client_id, issuer, subject)`` 比对，**不看 scope**。
    同一个主体的两个不同角色集因此会被归属判成同一个人 —— 于是「研究侧
    令牌」与「控制侧令牌」在会话层不可区分，一次串用不会被拦下。
    让它在装载期就不可能存在，比事后再补一条比对规则可靠。
    """
    _, d1 = mint("shared-name", [Role.CONTROLLER])
    _, d2 = mint("shared-name", [Role.RESEARCHER])
    _write(
        ring_path,
        ring_payload(
            ring_entry(d1, "shared-name", ["controller"]),
            ring_entry(d2, "shared-name", ["researcher"]),
        ),
    )
    with pytest.raises(ConfigError):
        TokenRing.from_file(ring_path)


def test_two_principals_may_not_share_a_digest(ring_path: Path) -> None:
    _, digest = mint("worker-1", [Role.RESEARCHER])
    _write(
        ring_path,
        ring_payload(
            ring_entry(digest, "worker-1", ["researcher"]),
            ring_entry(digest, "worker-2", ["researcher"]),
        ),
    )
    with pytest.raises(ConfigError):
        TokenRing.from_file(ring_path)


# ----------------------------------------------------------------------
# 受众：两个字符串指的是不是同一个资源
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("http://127.0.0.1:8787/mcp", "http://127.0.0.1:8787/mcp"),
        ("HTTP://127.0.0.1:8787/mcp", "http://127.0.0.1:8787/mcp"),
        ("http://LOCALHOST:8787/mcp", "http://localhost:8787/mcp"),
        ("http://127.0.0.1:8787/mcp/", "http://127.0.0.1:8787/mcp"),
        ("http://127.0.0.1:80/mcp", "http://127.0.0.1/mcp"),
        ("https://example.co.uk:443/mcp", "https://example.co.uk/mcp"),
    ],
)
def test_two_spellings_of_one_resource_are_one_resource(left: str, right: str) -> None:
    """写法的差异不该被读成两个资源。

    这一侧是有代价的：一个**字节精确**的比对会把这些全判成错配，于是「令牌是
    给这个服务铸的却 401」会在部署换一次大小写写法之后发生，而排查方向会落在
    环上。归一化消除的正是这一类。
    """
    assert same_resource(left, right) is True


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # 不同的路径：/mcp 与 /mcp/v2 是两个资源，不是同一个的两个写法。
        ("http://127.0.0.1:8787/mcp", "http://127.0.0.1:8787/mcp/v2"),
        # 不同的端口：这一个是「铸给了另一个部署」最便宜的样子。
        ("http://127.0.0.1:8787/mcp", "http://127.0.0.1:8788/mcp"),
        ("http://127.0.0.1:8787/mcp", "https://127.0.0.1:8787/mcp"),
        ("http://127.0.0.1:8787/mcp", "http://127.0.0.2:8787/mcp"),
        ("http://example.co.uk/mcp", "http://other.example.co.uk/mcp"),
    ],
)
def test_two_resources_are_not_one_resource(left: str, right: str) -> None:
    """归一化只抹掉写法差异，**不抹掉资源差异**。

    只写上面那一组的实现会通过全部「错配要拒绝」的用例 —— 而它的形状是
    「把 URL 截成 host 比一比」，于是铸给隔壁端口的令牌在这里是有效的。
    """
    assert same_resource(left, right) is False


@pytest.mark.parametrize("bad", ["", "/mcp", "not a url", "ftp://example.co.uk/mcp", None])
def test_an_unparseable_resource_matches_nothing(bad: object) -> None:
    """解析不出来的资源**谁也不匹配**（失败关闭）。

    一个「解析不了就放行」的实现会让一条写坏了的受众变成一张万能通行证 ——
    而它在日志里长得像一次正常的认证，因为返回的是同一个 ``True``。
    """
    assert same_resource(bad, "http://127.0.0.1:8787/mcp") is False  # type: ignore[arg-type]
    assert same_resource("http://127.0.0.1:8787/mcp", bad) is False  # type: ignore[arg-type]


def test_our_rule_and_the_sdks_rule_are_the_same_rule() -> None:
    """本仓的受众比对与 SDK 那条**逐例一致**。

    ``auth.py`` 的 docstring 声明了一件事：这条规则不是本仓发明的，就是
    ``BearerAuthBackend._issued_for_this_resource`` 那一条。声明需要有判据 ——
    SDK 改了自己的归一化（比如开始折叠尾斜杠、或者开始忽略默认端口）之后，
    本仓那句「两边判决从第一天起就是同一个」会变成假的，而两处判决分开的表现是
    「令牌在验证器眼里对、在 SDK 眼里错」，取决于哪一半先跑。

    对 SDK 私有方法下断言是刻意的：被测的正是**这个方法**，不是某个公开行为。
    它一旦改名，这条判据会以 ``AttributeError`` 变红，那也是一次需要人看一眼的
    信号 —— SDK 换了这个方法，本仓那句声明要重新读一遍。
    """
    from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
    from pydantic import AnyHttpUrl

    pairs = [
        ("http://127.0.0.1:8787/mcp", "http://127.0.0.1:8787/mcp"),
        ("http://127.0.0.1:8787/mcp", "HTTP://LocalHost:80/mcp"),
        ("http://127.0.0.1:8787/mcp", "http://127.0.0.1:8787/mcp/"),
        ("http://127.0.0.1:8787/mcp", "http://127.0.0.1:8788/mcp"),
        ("http://127.0.0.1:8787/mcp", "http://127.0.0.1:8787/mcp/v2"),
        ("https://example.co.uk/mcp", "https://example.co.uk:443/mcp"),
    ]
    for left, right in pairs:
        sdk = BearerAuthBackend(object(), resource_server_url=AnyHttpUrl(left))
        assert same_resource(left, right) == sdk._issued_for_this_resource(right), (left, right)


@pytest.mark.parametrize("value", ["/mcp", "ftp://example.co.uk/mcp", "", "not a url"])
def test_an_unparseable_resource_is_refused_with_a_url_shaped_message(value: str) -> None:
    """``normalize_resource`` 对坏值抛 ``ConfigError``，报文里点名了出处。

    它有两个调用点（环里的受众、``MARKETOPS_MCP_RESOURCE``），而两处的处置
    完全不同：前者是「这份环坏了」，后者是「这个部署起不来」。报错里带着
    ``where`` 才分得开。
    """
    with pytest.raises(ConfigError) as excinfo:
        normalize_resource(value, where="MARKETOPS_MCP_RESOURCE")
    assert "MARKETOPS_MCP_RESOURCE" in str(excinfo.value)
    assert "http(s)" in str(excinfo.value)


def test_a_resource_is_normalized_to_the_sdks_canonical_spelling() -> None:
    assert normalize_resource("HTTP://LocalHost:80/mcp") == "http://localhost/mcp"
    assert normalize_resource("http://127.0.0.1:8787/mcp/") == "http://127.0.0.1:8787/mcp/"


# ----------------------------------------------------------------------
# 有效期：写法在铸造期判，上界也在铸造期
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "seconds"),
    [("1s", 1), ("90s", 90), ("45m", 2700), ("12h", 43200), ("30d", 2592000), ("365d", MAX_TOKEN_LIFETIME_SECONDS)],
)
def test_the_duration_writer_reads_what_it_says(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize(
    "text",
    [
        # 「永不过期」不在这里的取值里，而且是有意的：一个收下它的解析器会把
        # 「每条授权都有有效期」这条要求变回一句倡议。
        "never",
        "none",
        "0d",
        "0s",
        "",
        "d",
        "30",
        "-5d",
        "1d12h",
        "30days",
        "1.5h",
        # 一个数字加**两个**单位不是「更精确」，是两段不同长度的话。
        "1h30m",
    ],
)
def test_everything_else_is_refused(text: str) -> None:
    """只有「整数 + 单位」这一种写法。

    注意 ``" 30d "`` **不在**这张表里：``parse_duration`` 会先 strip，所以它
    是收下的 —— 见下面 ``test_surrounding_whitespace_is_not_a_different_duration``。
    把收得下的写法放进「必须拒」的表里，会让这条判据自相矛盾。
    """
    with pytest.raises(ConfigError):
        parse_duration(text)


def test_surrounding_whitespace_is_not_a_different_duration() -> None:
    """``" 30d "`` 与 ``"30d"`` 是同一个有效期。

    命令行与配置文件里带一个尾随空格是常事，而拒绝它给出的是一条「写法不认识」
    的报错 —— 读的人会去改写法，而真正的问题是一个看不见的字符。
    """
    assert parse_duration(" 30d ") == parse_duration("30d")


def test_the_upper_bound_is_the_one_that_is_written_down() -> None:
    """上界两侧各断言一次。

    只测「超了要拒」会被一个把上界写小的实现满足（比如三十天），而那时
    ``--expires-in 60d`` 会在一个看起来正常的日子里被拒。``MAX_TOKEN_LIFETIME_SECONDS``
    是这个取舍的唯一载体，所以两侧都从它派生。
    """
    assert parse_duration(f"{MAX_TOKEN_LIFETIME_SECONDS}s") == MAX_TOKEN_LIFETIME_SECONDS
    with pytest.raises(ConfigError) as excinfo:
        parse_duration(f"{MAX_TOKEN_LIFETIME_SECONDS + 1}s")
    assert "365" in str(excinfo.value), "报文要说清上界是多少，而不是只说「太长」"


def test_the_two_paths_that_make_a_duration_agree_at_the_boundary(ring_path: Path) -> None:
    """``parse_duration`` 与 ``add_token_to_ring`` 对同一个上界给出同一个判决。

    两处各有一份范围判断（一个是解析文本、一个是收整数），它们是**同一个取舍的
    两个入口**。分开写而下一条判据，会让「CLI 放行、库调用被拒」这类分歧在
    某一天出现 —— 而它看起来像「命令行能用、脚本不能用」。
    """
    from marketops.security.tokens import add_token_to_ring

    with pytest.raises(ConfigError):
        add_token_to_ring(
            ring_path,
            principal="worker-1",
            roles=[Role.RESEARCHER],
            audience=RING_AUDIENCE,
            workspaces=[RING_WORKSPACE],
            expires_in_seconds=MAX_TOKEN_LIFETIME_SECONDS + 1,
        )
    with pytest.raises(ConfigError):
        add_token_to_ring(
            ring_path,
            principal="worker-1",
            roles=[Role.RESEARCHER],
            audience=RING_AUDIENCE,
            workspaces=[RING_WORKSPACE],
            expires_in_seconds=0,
        )


def test_a_minted_token_expires_when_it_was_told_to(ring_path: Path) -> None:
    """铸出来的有效期 = 现在 + 要的那一段，而写进文件的是那个时刻。

    不断言「等于 12h」而是断言落在一个**有上下界的区间**里：这段代码跑到了时钟
    的另一边（跨秒），一个等号会在那种情况下偶发地红一次 —— 而偶发的红最后总是
    被忽略。这不是放宽判据：区间只有几秒宽，而任何「算错了量级」的实现
    （小时当成秒、写成常量）都落在区间之外。
    """
    from datetime import datetime, timezone

    from marketops.security.tokens import add_token_to_ring

    before = int(datetime.now(tz=timezone.utc).timestamp())
    add_token_to_ring(
        ring_path,
        principal="worker-1",
        roles=[Role.RESEARCHER],
        audience=RING_AUDIENCE,
        workspaces=[RING_WORKSPACE],
        expires_in_seconds=43200,
    )
    after = int(datetime.now(tz=timezone.utc).timestamp())

    entries = json.loads(ring_path.read_text(encoding="utf-8"))["tokens"]
    written = entries[0]["expires_at"]
    # 盘上写的是 ISO-8601 UTC；读回来才能与区间比。这一条同时钉住了「写出来的
    # 是带 Z 的时刻」—— 一个写本地时间而又带 Z 的实现会在这里偏掉一个时区。
    assert written.endswith("Z")
    moment = int(datetime.fromisoformat(written.replace("Z", "+00:00")).timestamp())
    assert before + 43200 <= moment <= after + 43200


# ----------------------------------------------------------------------
# 泄漏卫生：这个对象会在日志、异常、repr 里出现。
# ----------------------------------------------------------------------
def test_the_ring_never_reveals_a_digest_or_a_token(ring_path: Path) -> None:
    token, digest = mint("worker-1", [Role.RESEARCHER])
    _write(ring_path, ring_payload(ring_entry(digest, "worker-1", ["researcher"])))
    ring = TokenRing.from_file(ring_path)
    grant = TokenGrant(
        principal="worker-1",
        roles=frozenset({Role.RESEARCHER}),
        audience=RING_AUDIENCE,
        workspaces=frozenset({RING_WORKSPACE}),
        expires_at=0,
    )
    for rendered in (repr(ring), str(ring), repr(grant)):
        assert token not in rendered
        assert digest not in rendered


def test_the_file_holds_no_usable_credential(ring_path: Path) -> None:
    """登记到盘上的东西单凭它自己**不能**用来认证。

    这是「凭据永不进仓」那条约束在本模块里的形态：即使这个文件出现在
    某个不该出现的地方，它也不构成一次认证绕过。
    """
    token, digest = mint("worker-1", [Role.RESEARCHER])
    _write(ring_path, ring_payload(ring_entry(digest, "worker-1", ["researcher"])))
    text = ring_path.read_text(encoding="utf-8")
    assert token not in text
    # 摘要本身不是凭据：拿它去认证必须失败。
    assert TokenRing.from_file(ring_path).grant_for(digest) is None


def test_the_audiences_of_a_ring_are_reportable(ring_path: Path) -> None:
    """``audiences`` 是错配时唯一的机器可读答案，所以它要报**全部**取值并去重。

    受众错配与令牌写错在传输层上是同一个 401（理由见
    ``adapters/mcp_server/auth.py``），于是诊断只能靠比对两边：本进程认定的
    资源（``--check`` 的 ``mcp.resource``）与这里列出的值。一个只报第一个条目的
    实现会让「环里混了两种受众」这件事看不出来 —— 而那是它最常见的样子。
    """
    _, d1 = mint("worker-1", [Role.RESEARCHER])
    _, d2 = mint("claude-control", [Role.CONTROLLER])
    _write(
        ring_path,
        ring_payload(
            ring_entry(d1, "worker-1", ["researcher"]),
            ring_entry(d2, "claude-control", ["controller"], audience="http://127.0.0.1:9999/mcp"),
        ),
    )

    assert TokenRing.from_file(ring_path).audiences == (
        "http://127.0.0.1:8787/mcp",
        "http://127.0.0.1:9999/mcp",
    )


def test_a_ring_may_hold_more_than_one_audience(ring_path: Path) -> None:
    """受众不同**不是**装载错误 —— 它是「这份环里有两条不同用途的授权」。

    与「一个主体不能有两套角色」那条刻意不同：那一格的危害在会话归属（同一个
    主体被判成两个人），而受众不同不产生那个问题。把它也做成装载错误，会让
    「两个端点各一条令牌」这种合法部署无法表达 —— 而它需要的是两个文件，
    不是一条更严的规则。
    """
    _, d1 = mint("worker-1", [Role.RESEARCHER])
    _, d2 = mint("claude-control", [Role.CONTROLLER])
    _write(
        ring_path,
        ring_payload(
            ring_entry(d1, "worker-1", ["researcher"]),
            ring_entry(d2, "claude-control", ["controller"], audience="http://127.0.0.1:9999/mcp"),
        ),
    )

    ring = TokenRing.from_file(ring_path)

    assert ring.principal_names == ("claude-control", "worker-1")


# ----------------------------------------------------------------------
# 外联闸门的两半不许落在同一个主体上（§6）
# ----------------------------------------------------------------------
def test_minting_a_token_that_holds_both_halves_of_the_gate_is_refused() -> None:
    """铸一枚同时带 controller 与 authorizer 的令牌 —— 当场拒绝。

    §6 要求批准来自一个**独立**的责任人。目录把两半分给了两个角色
    （``authorize_wave`` 只给 authorizer、``send_wave`` 只给 controller），
    而角色分开了不等于**主体**分开了：一份带两个 scope 的令牌让同一个人握住
    两半，`authorized_by` 那一格照旧会填上，只是它记的是同一个人的两次签名。
    """
    with pytest.raises(ConfigError, match="同时持有 controller 与 authorizer"):
        mint("ops", [Role.CONTROLLER, Role.AUTHORIZER])


def test_a_ring_entry_that_holds_both_halves_is_refused_at_load(ring_path: Path) -> None:
    """手写进环文件的那一份也过不去。

    铸造期的拒绝挡不住「有人直接改环文件」—— 而改环文件正是那条路径上最容易
    发生的一步（把两个 scope 抄进同一行看起来完全正常）。所以同一条规则在装载期
    也要有一次判定，且它判的是**条目**：同一主体、两条条目、各带一半，同样红。
    """
    _, digest = mint("ops", [Role.CONTROLLER])
    _write(ring_path, ring_payload(ring_entry(digest, "ops", ["controller", "authorizer"])))

    with pytest.raises(ConfigError, match="同时持有 controller 与 authorizer"):
        TokenRing.from_file(ring_path)


def test_a_third_role_does_not_launder_the_collapsed_gate(ring_path: Path) -> None:
    """多带一个不相关的角色，**不**让两半落回同一个人身上。

    这条判据判的是那条规则的**形状**（「包含」而不是「恰好等于」）。写成
    ``== {controller, authorizer}`` 读起来更整齐，而它放行的正是最该拒的那一种：
    一个「什么都能干」的主体 —— 恰恰是 §6 「独立授权人」要防的那件事换个名字。
    所以那一格必须按**包含**判，而本判据是那件事的执行器。
    """
    _, digest = mint("ops", [Role.RESEARCHER, Role.CONTROLLER])
    _write(
        ring_path,
        ring_payload(ring_entry(digest, "ops", ["controller", "researcher", "authorizer"])),
    )

    with pytest.raises(ConfigError, match="同时持有 controller 与 authorizer"):
        TokenRing.from_file(ring_path)


def test_the_two_halves_minted_to_two_principals_load(ring_path: Path) -> None:
    """对照：两半铸给两个主体**装载得起来**。

    只测拒绝时，一个把所有环都拒掉的装载器同样全绿 —— 而它的症状是服务起不来。
    这一条同时是那两份拒绝的对照，也是那条规则真正允许的形态。
    """
    _, control = mint("claude-control", [Role.CONTROLLER])
    _, approver = mint("ops-owner", [Role.AUTHORIZER])
    _write(
        ring_path,
        ring_payload(
            ring_entry(control, "claude-control", ["controller"]),
            ring_entry(approver, "ops-owner", ["authorizer"]),
        ),
    )

    ring = TokenRing.from_file(ring_path)

    assert ring.principal_names == ("claude-control", "ops-owner")
    assert set(ring.roles_present) == {"controller", "authorizer"}

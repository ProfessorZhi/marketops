"""服务面**起得来之前**的两道闸（§10 环回、§7 失败关闭）。

``tests/test_mcp_isolation.py`` 测的是服务面起来之后谁看得见什么。本文件测的是
更前面的一步：这个进程**允不允许**把一个监听套接字绑在那个地址上，以及在
没有令牌环的时候它会不会照样起来、然后什么都不许。

## 为什么「绑在哪」是一条安全判据

这台机器上默认绑 ``0.0.0.0`` 的东西不止一个 —— 记忆里
``CloudCLI 默认绑 0.0.0.0 且首启未认证`` 就是同一个形态。MarketOps 的服务面
带着一条能读证据、能提交发现、将来能花钱的接口，把它绑到所有网卡上，等于让
同网段的任何一台机器都拿到这条接口。所以判据不是「记得绑 127.0.0.1」，
而是**非环回地址一律起不来**。

## 两条闸的分工

* ``resolve_bind_host`` —— 地址必须是**字面量**的环回地址。名字（``localhost``）
  被拒绝：绑定发生在解析之后，而 ``/etc/hosts`` 与 DNS 是别人的东西。
  注意这与抓取层的判据不是同一条：那边 ``localhost`` 是**环回目标**（因此要拒
  绝），这边 ``localhost`` 是**不确定的绑定地址**（因此也要拒绝，但理由相反）。
* 令牌环装载 —— 不配令牌**不是**「开放」，是「谁都不许」。所以 ``--check``
  在令牌环缺失时返回非零：一个能起来但谁都不许的服务，部署方必须知道。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from marketops.adapters.build_stamp import LOADED_BUILD
from marketops.config import REPO_ROOT, Settings, load_settings
from marketops.domain.errors import ConfigError
from marketops.interfaces.composition import default_acquisition, default_crawl
from marketops.interfaces.mcp.__main__ import (
    resolve_bind_host,
    wire_decisions,
    wire_research,
)
from marketops.security.tokens import TokenRing
from tests.fixtures.http_fakes import FakeHttp, site_replies
from tests.mcp_harness import MCP_URL, drive_with_settings

#: 多页抓取那一条判据用的站。断言落在抓取器的流水账上，所以要按**它读的那两份
#: 文档**来摆响应 —— 前沿读的是 robots 与站点地图，与调用方给的种子不是同一份。
SITE = "https://example.co.uk"
ROBOTS = f"{SITE}/robots.txt"
SITEMAP = f"{SITE}/sitemap.xml"
SITE_PAGES = (f"{SITE}/", f"{SITE}/contact-us")


# ----------------------------------------------------------------------
# 地址闸
# ----------------------------------------------------------------------
@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "::1"], ids=["v4", "v4-alt", "v6"])
def test_a_literal_loopback_address_is_accepted(host: str) -> None:
    assert resolve_bind_host(host) == host


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "192.168.1.5",
        "10.0.0.1",
        "100.64.0.1",
        "169.254.169.254",
        "0.0.0.0 ",
        "localhost",
        "example.com",
        "127.0.0.1.example.com",
        "",
        "0.0.0.0:8787",
    ],
    ids=[
        "all-v4",
        "all-v6",
        "lan",
        "private",
        "cgnat",
        "metadata",
        "padded",
        "name",
        "public-name",
        "loopback-looking-name",
        "empty",
        "addr-and-port",
    ],
)
def test_anything_but_a_literal_loopback_address_is_refused(host: str) -> None:
    """拒绝必须发生在**绑定之前**，且理由要指得出该改哪个变量。

    ``0.0.0.0:8787`` 与 ``127.0.0.1.example.com`` 是有意的两条：前者看着像地址，
    实际是「地址:端口」；后者看着像环回地址，实际是一个可以把 A 记录指向任何
    地方的公网名字。一个 ``startswith("127.")`` 式的实现会放过第二条，所以判据是
    **先解析成 ``ipaddress`` 对象**，再看它是不是环回。
    """
    with pytest.raises(ConfigError) as excinfo:
        resolve_bind_host(host)
    assert "MARKETOPS_MCP_HOST" in str(excinfo.value), "拒绝理由要说得出该改哪个变量"


# ----------------------------------------------------------------------
# 配置默认值
# ----------------------------------------------------------------------
def test_the_default_bind_is_loopback(settings) -> None:
    assert settings.mcp_host == "127.0.0.1"
    assert resolve_bind_host(settings.mcp_host) == "127.0.0.1"


def test_the_default_port_is_the_one_the_harness_assumes(settings) -> None:
    """脚手架里写死的 ``8787`` 与配置缺省必须是同一个数。

    两处各写一个数，症状是「测试一直过、真跑起来连不上」——因为测试用的是
    自己那个数。这条把两处钉在一起。
    """
    from tests.mcp_harness import BASE_URL

    assert f":{settings.mcp_port}" in BASE_URL


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "::1"], ids=["v4", "v4-alt", "v6"])
def test_dns_rebinding_protection_is_on_for_every_address_we_accept(host: str) -> None:
    """合法地址有几个，保护就有几份 —— 不靠 SDK 的自动开启。

    SDK 只在 ``host in ("127.0.0.1", "localhost", "::1")`` 时自动装上
    ``enable_dns_rebinding_protection``。本服务的地址闸接受 ``127.0.0.5``，
    那条路径上自动开启**不会**发生 —— 于是同一份配置在两个合法地址上得到
    两种安全姿态。这条测试给第二个地址一个正例，让那份差别不可能悄悄回来。
    """
    from marketops.adapters.mcp_server.server import loopback_transport_security

    settings = loopback_transport_security(host)
    assert settings.enable_dns_rebinding_protection is True
    bracketed = f"[{host}]" if ":" in host else host
    assert f"{bracketed}:*" in settings.allowed_hosts, (
        f"绑在 {host} 上却不容许 Host: {bracketed}:* —— 这个地址上的请求会被 421"
    )
    # 对照：保护不能宽到「谁都行」。
    assert "evil.example.com:*" not in settings.allowed_hosts
    assert settings.allowed_origins, "没有 Origin 白名单的保护只挡了一半"


def test_the_port_is_read_from_the_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARKETOPS_MCP_PORT", "8791")
    assert load_settings().mcp_port == 8791


def test_a_non_numeric_port_is_a_configuration_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARKETOPS_MCP_PORT", "eight-thousand")
    with pytest.raises(ConfigError):
        load_settings()


def test_a_host_that_is_not_configured_at_all_is_a_configuration_error(
    tmp_path, monkeypatch
) -> None:
    """空字符串要走「未配置」这条路，不能退化成绑定一切。

    一个把空值当成 ``0.0.0.0`` 的实现在这里就会红 —— 那是本文件里最坏的一种
    默认值，因为它把一次**漏配**变成一次**对外开放**。
    """
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MARKETOPS_MCP_HOST", "   ")
    assert load_settings().mcp_host == "127.0.0.1"


# ----------------------------------------------------------------------
# ``--check``：部署前的那一次自检
# ----------------------------------------------------------------------
def _run_check(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """跑一次 ``python -m marketops.interfaces.mcp --check``。

    用子进程而不是直接调 ``main()``：这一条测的是**运维真的会敲的那一行**，
    包括模块能不能被 ``-m`` 起来。直接调函数会静默地跳过 ``__main__`` 的接线。
    """
    full = {k: v for k, v in os.environ.items() if not k.startswith("MARKETOPS_")}
    full.update(env)
    return subprocess.run(
        [sys.executable, "-m", "marketops.interfaces.mcp", "--check"],
        cwd=REPO_ROOT,
        env=full,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _ring_with_token(
    directory,
    *principals: tuple[str, str],
    name: str = "mcp-tokens.json",
    workspaces: object = None,
) -> tuple[Path, str]:
    """写一份最小令牌环，返回 ``(路径, 第一个主体的令牌)``。

    令牌本身交出来是给**真实会话**用的（``drive_with_settings`` 需要 Bearer 值），
    而 ``--check`` 那一组只要路径。两种需求共用这一份写盘逻辑 —— 分开写会让两份
    环在某次格式变更后只有一份是对的。

    ``workspaces`` 缺省（``None``）走 ``ring_entry`` 自己的缺省，也就是
    ``RING_WORKSPACE`` —— 与这个进程服务的 Scene 是同一个来源。要造**错配**的
    用例显式传别的值，而那条路径同样只有这一份写盘逻辑。
    """
    from marketops.security.catalog import Role
    from marketops.security.tokens import mint
    from tests.mcp_harness import ring_entry, ring_payload

    entries = []
    tokens: list[str] = []
    for principal, role in principals:
        token, digest = mint(principal, [Role(role)])
        tokens.append(token)
        extra = {} if workspaces is None else {"workspaces": workspaces}
        entries.append(ring_entry(digest, principal, [role], **extra))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(ring_payload(*entries)), encoding="utf-8")
    return path, tokens[0]


def _ring(
    directory, *principals: tuple[str, str], name: str = "mcp-tokens.json", workspaces: object = None
) -> Path:
    """``_ring_with_token`` 的路径那一半。"""
    return _ring_with_token(directory, *principals, name=name, workspaces=workspaces)[0]


def test_check_passes_when_the_host_and_the_ring_are_both_good(tmp_path) -> None:
    ring = _ring(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {"MARKETOPS_DATA_ROOT": str(tmp_path / "state"), "MARKETOPS_MCP_TOKENS": str(ring)}
    )
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["ok"] is True
    assert payload["mcp"]["host"] == "127.0.0.1"
    assert payload["mcp"]["port"] == 8787
    assert payload["mcp"]["auth_configured"] is True
    assert payload["mcp"]["principals"] == ["claude-control"]
    assert payload["mcp"]["roles"] == ["controller"]


def test_check_reports_the_resource_and_the_audiences_it_will_accept(tmp_path) -> None:
    """``--check`` 报出**受众比对的两边**：本进程认的资源，与环里实际写着的受众。

    这两格是受众错配唯一的诊断面。传输层把「令牌不认识」与「令牌是给别人的」
    翻成同一个 401，而那响应里没有放理由的位置（``auth.py`` 的 docstring 说了
    为什么不能有）—— 所以运维手里唯一的证据就是这两个值的比对，而它们必须
    来自同一个进程的一次输出。分开两次读会在两次之间引入一个「谁改了什么」的
    缝，而这条判据的全部价值就在于一次读数。

    缺省配置下两边相等，这一条钉的是那个相等**是算出来的**而不是抄出来的：
    ``resource`` 必须等于 ``MCP_URL``（脚手架里服务端也会认定的那个值）。
    """
    ring = _ring(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {"MARKETOPS_DATA_ROOT": str(tmp_path / "state"), "MARKETOPS_MCP_TOKENS": str(ring)}
    )
    assert done.returncode == 0, done.stderr

    mcp = json.loads(done.stdout)["mcp"]
    assert mcp["resource"] == MCP_URL, "缺省配置下资源应当由绑定地址推导出来"
    assert mcp["audiences"] == [MCP_URL], (
        "环里那条授权的受众就是本服务认定的资源，两边必须报出同一个值"
    )


def test_check_shows_the_two_sides_of_an_audience_mismatch(tmp_path) -> None:
    """两格不一致时，输出把**两个**值都摆在眼前 —— 这才是错配被发现的地方。

    复现的是一个真实场景：部署换了对外地址（``MARKETOPS_MCP_RESOURCE``），而环
    里那些令牌是照旧地址铸的。结果是每一条令牌都 401，而它们全都是好令牌。
    这一条**不断言自检失败**：自检在这里是绿的 —— 配置读得出、地址合法、环装得进。
    坏的不是配置，是两侧的取值对不上，而 ``--check`` 能做的就是把它们都报出来。
    一条会在这里退非零的自检，会让运维以为该改的是配置。
    """
    renamed = "http://mcp.internal.example.co.uk/mcp"
    ring = _ring(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {
            "MARKETOPS_DATA_ROOT": str(tmp_path / "state"),
            "MARKETOPS_MCP_TOKENS": str(ring),
            "MARKETOPS_MCP_RESOURCE": renamed,
        }
    )
    assert done.returncode == 0, done.stderr

    mcp = json.loads(done.stdout)["mcp"]
    assert mcp["resource"] == renamed, "配置点名的资源没有被报出来"
    assert mcp["audiences"] == [MCP_URL], "环里的受众没有被报出来"
    assert mcp["resource"] != mcp["audiences"][0], "这一条的前提就是两边不一样"


def test_check_reports_the_workspace_and_the_workspaces_on_the_ring(tmp_path) -> None:
    """``--check`` 报出**工作区比对的两边**：本进程服务的 Scene，与环里写着的。

    与上面两条同一条理由，而且更要紧：工作区错配**不**在传输层报错（它是应用里
    的一条拒绝，那句话说得到 ``--check`` 但说不出名字），所以这两格是它唯一
    的诊断面。两边必须来自同一次输出 —— 分开两次读会在两次之间引入一个
    「谁改了什么」的缝。

    缺省配置下两边相等，这一条钉的是那个相等**是算出来的**：``workspace`` 必须
    是配置里的 Scene，``workspaces`` 必须是环里真的读出来的那一份。
    """
    from tests.conftest import DEFAULT_PROFILE

    ring = _ring(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {"MARKETOPS_DATA_ROOT": str(tmp_path / "state"), "MARKETOPS_MCP_TOKENS": str(ring)}
    )
    assert done.returncode == 0, done.stderr

    mcp = json.loads(done.stdout)["mcp"]
    assert mcp["workspace"] == DEFAULT_PROFILE, "本进程服务的 Scene 没有被报出来"
    assert mcp["workspaces"] == [DEFAULT_PROFILE], (
        "环里那条授权的工作区就是本部署的 Scene，两边必须报出同一个值"
    )


def test_check_shows_the_two_sides_of_a_workspace_mismatch(tmp_path) -> None:
    """错的令牌（铸给了另一个 Scene）在这两格上现形 —— 而调用面上一片沉默。

    这一条**不断言自检失败**：配置读得出、环装得进，自检在这里是绿的。坏的不是
    配置，是两侧的取值对不上；而调用面上所有的工具都会答同一句「工作区不匹配」，
    那句话刻意不报出本部署的 Scene（``security/workspace.py`` 的取向）。所以
    ``--check`` 把两个值都摆出来，是运维唯一能看见它们的地方。
    """
    from tests.conftest import DEFAULT_PROFILE

    other = "some-other-scene"
    ring = _ring(tmp_path, ("claude-control", "controller"), workspaces=(other,))
    done = _run_check(
        {"MARKETOPS_DATA_ROOT": str(tmp_path / "state"), "MARKETOPS_MCP_TOKENS": str(ring)}
    )
    assert done.returncode == 0, done.stderr

    mcp = json.loads(done.stdout)["mcp"]
    assert mcp["workspace"] == DEFAULT_PROFILE, "本进程服务的 Scene 没有被报出来"
    assert mcp["workspaces"] == [other], "环里的工作区没有被报出来"
    assert mcp["workspace"] != mcp["workspaces"][0], "这一条的前提就是两边不一样"


def test_marketops_mcp_resource_is_normalized_the_same_way_as_the_ring(tmp_path) -> None:
    """``MARKETOPS_MCP_RESOURCE`` 与环里的受众走**同一个**归一化。

    两处各写一套规则，症状是「配置里写的是 HTTP://LOCALHOST:80/mcp、环里是
    http://localhost/mcp，而两边都觉得自己是对的」—— 一次 401，两个看起来都
    没错的值。所以这里给一个故意写成大写的配置值，期望它被归成规范写法。

    取的是 ``http://127.0.0.1/mcp``（缺省端口 80 被丢掉）而不是本服务真的绑的
    那个地址：被问的是**归一化**，不是「这个部署绑在哪」。
    """
    ring = _ring(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {
            "MARKETOPS_DATA_ROOT": str(tmp_path / "state"),
            "MARKETOPS_MCP_TOKENS": str(ring),
            "MARKETOPS_MCP_RESOURCE": "HTTP://LocalHost:80/mcp",
        }
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["mcp"]["resource"] == "http://localhost/mcp"


def test_a_marketops_mcp_resource_that_is_not_a_url_is_refused_by_name(tmp_path) -> None:
    """写坏的资源标识符要**点名那个变量**，而且输出仍然是可解析的 JSON。

    这是 ``_payload`` 那条「形状固定，故障路径上也一样」的契约最容易被破坏的
    地方：``resource`` 那一格要算出来，而算它正是这次失败本身。让异常从那里
    逃出去，故障路径就变成一堆堆栈 —— 而它被写出来的理由恰恰是「运维在故障
    时读到的第一句话」。
    """
    ring = _ring(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {
            "MARKETOPS_DATA_ROOT": str(tmp_path / "state"),
            "MARKETOPS_MCP_TOKENS": str(ring),
            "MARKETOPS_MCP_RESOURCE": "not a url",
        }
    )
    assert done.returncode != 0
    payload = json.loads(done.stdout)
    assert payload["ok"] is False
    assert "MARKETOPS_MCP_RESOURCE" in payload["blocker"]
    assert payload["mcp"]["resource"] is None, "算不出来时那一格是 null，不是猜一个值"
    assert "http(s)" in payload["blocker"], "要说清这个变量该长什么样"


def test_check_never_prints_a_token(tmp_path) -> None:
    """自检的输出里没有令牌本身。

    这份输出会被贴进工单、日志与会话记录，而它是在**一个刚铸完令牌**的部署上
    跑的第一条命令 —— 那时令牌还在运维的滚动缓冲里。这一条钉的是自检这条路
    不会把它抄一份出去：它报主体名、角色名、受众、资源，一样都不需要令牌。
    """
    ring, token = _ring_with_token(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {"MARKETOPS_DATA_ROOT": str(tmp_path / "state"), "MARKETOPS_MCP_TOKENS": str(ring)}
    )
    assert done.returncode == 0, done.stderr

    combined = done.stdout + done.stderr
    assert token not in combined
    assert token[:16] not in combined, "自检把令牌的前缀抄了出去"


def test_check_reports_the_build_the_child_process_loaded(tmp_path) -> None:
    """``--check`` 要能答「这个进程加载的是哪一版」。

    子进程读的是**同一棵树**（``_run_check`` 用 ``cwd=REPO_ROOT`` 起它），所以两边
    的 ``source_tree`` / ``revision`` 必须一致 —— 不一致就说明「哪棵树」有了第二个
    来源。

    **不比 ``dirty``**：它读的是两个不同的时刻（本进程在 import 时、子进程在起它时），
    比它测的是「这棵树在这段时间里没被动过」，不是指纹本身。也不比 ``read_at`` ——
    两次读必然不同，那才是对的。
    """
    ring = _ring(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {"MARKETOPS_DATA_ROOT": str(tmp_path / "state"), "MARKETOPS_MCP_TOKENS": str(ring)}
    )
    assert done.returncode == 0, done.stderr

    build = json.loads(done.stdout)["build"]
    assert build["source_tree"] == LOADED_BUILD.source_tree
    assert build["revision"] == LOADED_BUILD.revision
    assert build["errors"] == {}
    assert isinstance(build["dirty"], bool)


def test_check_refuses_a_non_loopback_host(tmp_path) -> None:
    """``--check`` 与真正启动走**同一个**判据。

    两条路径各写一遍检查，是「检查过了」与「实际绑的」迟早分家那条老路。
    """
    ring = _ring(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {
            "MARKETOPS_DATA_ROOT": str(tmp_path / "state"),
            "MARKETOPS_MCP_TOKENS": str(ring),
            "MARKETOPS_MCP_HOST": "0.0.0.0",
        }
    )
    assert done.returncode != 0
    assert "MARKETOPS_MCP_HOST" in done.stdout + done.stderr


def test_a_half_configured_executor_fails_the_self_check_not_only_the_serve_path(
    tmp_path, monkeypatch
) -> None:
    """``--check`` 跑的是**整份装配**，不只是配置与令牌环。

    曾经不是。``--check`` 只做三件事：读配置、解析地址、装令牌环 —— 完全不碰决策
    后端与外部执行器的构造。于是一个 ``MARKETOPS_QUAY_*`` 配了一半的部署会拿到
    「自检不通过：没有配置令牌环」（退 1，读起来是「配好令牌环就能起」），然后在
    真正启动时退 2。同一个部署，两句互相矛盾的话，而先跑到的、也是唯一会被贴进
    工单的那句，是自检那句。

    判据故意做成**两条路径在同一份配置上的举止**，而不是「两处都调了 ``_compose``」
    那种结构断言：后者在有人把装配拆成两半、只让一半走 ``_compose`` 时照样绿。
    """
    ring = _ring(tmp_path, ("claude-control", "controller"))
    half = {"MARKETOPS_QUAY_CLI": "/bin/true"}  # 三样只给一样

    checked = _run_check(
        {
            "MARKETOPS_DATA_ROOT": str(tmp_path / "state"),
            "MARKETOPS_MCP_TOKENS": str(ring),
            **half,
        }
    )
    assert checked.returncode == 2, (
        f"半配的执行器必须让自检**起不来**（退 2），实际退 {checked.returncode}："
        "退 1 读出来是「谁都不许」，而那不是这个部署的问题。"
    )
    assert "MARKETOPS_QUAY_NODE" in checked.stdout + checked.stderr, (
        "消息要点名缺的是哪一个变量 —— 运维手里只有变量名，没有那些字段名。"
    )

    # 真正启动那一条：把 ``run`` 换成空操作，否则它会绑端口并阻塞。这一条的
    # 前提是**装配在 run 之前就失败**，所以这里既能验行为、又不会真的起服务。
    from mcp.server.mcpserver import MCPServer

    from marketops.interfaces.mcp.__main__ import _serve

    monkeypatch.setattr(MCPServer, "run", lambda self, **kwargs: None)
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("MARKETOPS_MCP_TOKENS", str(ring))
    for name, value in half.items():
        monkeypatch.setenv(name, value)
    for name in ("MARKETOPS_QUAY_NODE", "MARKETOPS_QUAY_ROOT"):
        monkeypatch.delenv(name, raising=False)
    assert _serve() == 2, "启动路径与自检必须对同一份配置给出同一个判决"

    # 正对照：把三样补齐，同一条启动路径必须**走到 run**（退 0）。没有这一半，
    # 上面那个 2 可能来自任何一处与执行器无关的失败。
    monkeypatch.setenv("MARKETOPS_QUAY_NODE", sys.executable)
    monkeypatch.setenv("MARKETOPS_QUAY_ROOT", str(tmp_path / "quay-root"))
    (tmp_path / "quay-root").mkdir()
    assert _serve() == 0


def test_a_serve_that_starts_publishes_the_build_it_loaded_before_it_serves(
    tmp_path, monkeypatch
) -> None:
    """服务在**开始服务之前**把自己加载的构建报在数据根里（§15(b) 的服务侧）。

    worker 侧那一半（读回来、比对、不一致就停）在 ``tests/test_research_worker.py``
    里逐条钉着；这一条钉的是另一半。它有一个很安静的失效形态：**服务不写，每一轮
    派发都会停下**，理由是「这个数据根上还没有服务报过自己的构建」—— 那句话把一次
    接线断掉说成一个部署事实（读的人会去查服务起没起过，而它好好地在跑）。

    断言落在 ``run`` **里面**：那一刻文件就得已经在了。一个「起完之后再写」的实现在
    这里会红，而它真的会漏 —— ``run`` 起来到写下文件之间有一个窗口，窗口里进来的
    派发全部撞上「还没有服务报过」，而窗口里发生过的事在事后读不出来。
    """
    from mcp.server.mcpserver import MCPServer

    from marketops.adapters.server_build import read_server_build
    from marketops.interfaces.mcp.__main__ import _serve

    data_root = tmp_path / "state"
    during_run: list = []

    def look_while_serving(self, **kwargs) -> None:  # noqa: ANN001 -- 与 MCPServer.run 同形
        during_run.append(read_server_build(data_root))

    monkeypatch.setattr(MCPServer, "run", look_while_serving)
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MARKETOPS_MCP_TOKENS", str(_ring(tmp_path, ("claude-control", "controller"))))
    monkeypatch.setenv("MARKETOPS_QUAY_CLI", "/bin/true")
    monkeypatch.setenv("MARKETOPS_QUAY_NODE", sys.executable)
    monkeypatch.setenv("MARKETOPS_QUAY_ROOT", str(tmp_path / "quay-root"))
    (tmp_path / "quay-root").mkdir()

    assert _serve() == 0
    assert during_run, "前提没成立：run 一次都没被调到"

    serving = during_run[0]
    assert serving.readable, f"服务开始服务时这份回执还不在：{serving.problem}"
    assert serving.stamp == LOADED_BUILD, "报的必须是**这个进程**加载的那一份"
    assert serving.pid == os.getpid()
    assert read_server_build(data_root).readable, "服务起来之后那一份也要留在盘上"


def test_a_serve_that_cannot_publish_its_build_refuses_to_serve(tmp_path, monkeypatch, capsys) -> None:
    """回执写不出来 ⇒ 退 2，而且 ``run`` 一次都没被调到。

    **不退 1**：这不是「起来但谁都不许」，是起不来。1 读出来是「配好令牌环就能起」，
    而那时它已经起来了。

    也不「打个警告照样服务」：那种部署每一轮派发都会以「这个数据根上还没有服务报过
    自己的构建」停下 —— 那句话把一个磁盘问题说成一个部署事实，而运维会去查一个没坏
    的东西（服务好好地在跑）。

    ``state/`` 里的目录**先按正常路建齐**、再把 ``state/`` 本身置成不可写（``0o500``）：
    ``ensure_data_dirs()`` 对已存在的目录不报错（它只 ``mkdir(exist_ok=True)``），所以
    这一次启动会一直走到发布那一步才失败 —— 那正是要测的那一步。手写目录清单会在
    ``ensure_data_dirs()`` 将来多建一个目录时变成一条**测不到发布**的测试。
    """
    from mcp.server.mcpserver import MCPServer

    from marketops.adapters.server_build import server_build_path
    from marketops.interfaces.mcp.__main__ import _serve

    data_root = tmp_path / "state-root"
    ran: list[int] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, **kwargs: ran.append(1))
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(data_root))
    monkeypatch.setenv("MARKETOPS_MCP_TOKENS", str(_ring(tmp_path, ("claude-control", "controller"))))
    monkeypatch.setenv("MARKETOPS_QUAY_CLI", "/bin/true")
    monkeypatch.setenv("MARKETOPS_QUAY_NODE", sys.executable)
    monkeypatch.setenv("MARKETOPS_QUAY_ROOT", str(tmp_path / "quay-root"))
    (tmp_path / "quay-root").mkdir()
    state = load_settings().state_dir
    load_settings().ensure_data_dirs()
    assert state.is_dir(), "前提：目录树已经按正常路建齐"
    state.chmod(0o500)
    try:
        code = _serve()
        printed = capsys.readouterr().err
    finally:
        state.chmod(0o700)

    assert code == 2, "写不出来是**起不来**；退 1 读出来是「配好令牌环就能起」"
    assert ran == [], "回执写不出来还是把服务绑起来起 —— 那这条闸只在日志里成立"
    assert str(server_build_path(data_root)) in printed, "要点名那个写不出来的路径"


def test_check_fails_loudly_when_there_is_no_token_ring(tmp_path) -> None:
    """没配令牌环时服务面**起得来但什么都不许** —— 部署方必须在起之前知道。

    这一条把「失败关闭」从一个运行时行为变成一条**部署前就会被看见**的事实。
    §7 的隔离若只在运行时生效，第一个发现它的人会是调用方，而不是运维。
    """
    done = _run_check({"MARKETOPS_DATA_ROOT": str(tmp_path / "state")})

    assert done.returncode != 0
    payload = json.loads(done.stdout)
    assert payload["ok"] is False
    assert payload["mcp"]["auth_configured"] is False
    assert payload["mcp"]["principals"] == []
    assert "MARKETOPS_MCP_TOKENS" in payload["blocker"]


def test_check_fails_on_a_ring_it_cannot_parse(tmp_path) -> None:
    """令牌环坏了也是**起之前**就该知道的事。

    坏环与无环的结论相同、理由不同：一个是「谁都不许」，另一个是「谁都不许，
    而且你以为配好了」。后者更贵，因为它会被排到别的地方去查。
    """
    bad = tmp_path / "mcp-tokens.json"
    bad.write_text("{not json", encoding="utf-8")
    done = _run_check(
        {"MARKETOPS_DATA_ROOT": str(tmp_path / "state"), "MARKETOPS_MCP_TOKENS": str(bad)}
    )
    assert done.returncode != 0
    assert json.loads(done.stdout)["ok"] is False


def test_check_never_prints_the_token_file_path(tmp_path) -> None:
    """令牌环的路径可以出现在**启动参数**里，不该出现在**输出**里。

    这个自检的输出会被贴进工单、日志、会话记录。路径本身不是凭据，但把它
    印出来会让「凭据在哪里」变成一条随输出扩散的事实，而这条事实的价值
    只对不该看到它的人成立。
    """
    marker = "vault-marker-do-not-print"
    ring = _ring(tmp_path / marker, ("claude-control", "controller"))
    done = _run_check(
        {"MARKETOPS_DATA_ROOT": str(tmp_path / "state"), "MARKETOPS_MCP_TOKENS": str(ring)}
    )
    assert done.returncode == 0, done.stderr
    assert marker not in done.stdout
    assert marker not in done.stderr


def test_check_does_not_create_the_data_dirs(tmp_path) -> None:
    """自检是只读的。

    一个会把目录树建出来的自检，在「目录还没准备好」这种真正的故障面前
    会先把故障修掉一半，于是运维看不到它 —— 而真正启动时才会以另一种形态炸。
    """
    ring = _ring(tmp_path, ("claude-control", "controller"))
    data_root = tmp_path / "never-created"
    _run_check({"MARKETOPS_DATA_ROOT": str(data_root), "MARKETOPS_MCP_TOKENS": str(ring)})
    assert not data_root.exists(), "--check 建了运行目录"


# ----------------------------------------------------------------------
# 状态库那一道闸（§15(c)）
# ----------------------------------------------------------------------
@pytest.fixture
def bare_settings(tmp_path: Path) -> Settings:
    """一份**还没有建过任何目录**的配置。

    刻意不用 ``settings`` 夹具：它在 ``tests/conftest.py`` 里调过
    ``ensure_data_dirs()``，于是整棵运行目录树早就存在 —— 而本组有一条判据问的
    正是「这一次启动有没有把它建出来」，在一棵已存在的树上问不出任何东西
    （``tests/test_store.py`` 有同样的两处，同样写了理由）。
    """
    from tests.conftest import DEFAULT_PROFILE, PROFILES_DIR

    return Settings(
        data_root=tmp_path / "runtime", profiles_dir=PROFILES_DIR, profile=DEFAULT_PROFILE
    )


def _current_database(settings: Settings) -> Path:
    """按配置该在的那个库建出来。路径**由配置算**，不在这里抄一份 ``state/`` 的拼法。"""
    from marketops.adapters.sqlite import db

    path = settings.database_path
    conn = db.connect(path)
    try:
        db.ensure_schema(conn)
    finally:
        conn.close()
    return path


def _damage_database(settings: Settings, kind: str) -> Path:
    """把一个健康的库弄成某一种打不开的样子。

    五种各自对应 :class:`~marketops.adapters.sqlite.db.SchemaStanding` 里起不来的
    那几格，而它们**来自不同的机制**：版本比代码旧（要人跑一次迁移）、比代码新
    （迁移只有一个方向）、版本那一格不是版本号（被写坏了）、有行却没有绑定
    （手工操作留下的）、以及文件根本不是库（路径指错了）。判据要的是「它们**都**
    让这个部署起不来」，所以五种都要在。
    """
    from marketops.adapters.sqlite import db

    path = _current_database(settings)
    if kind == "not-a-database":
        path.write_bytes(b"this is a text file wearing a .db name\n")
        return path

    conn = db.connect(path)
    try:
        if kind == "behind":
            db.set_meta(conn, db.SCHEMA_VERSION_KEY, "2")
        elif kind == "ahead":
            db.set_meta(conn, db.SCHEMA_VERSION_KEY, "9")
        elif kind == "nonsense":
            db.set_meta(conn, db.SCHEMA_VERSION_KEY, "v1")
        elif kind == "unbound":
            # 有行、却没有工作区绑定。写路径构造不出这一格，所以这里手工插一行
            # （``tests/test_sqlite_pool.py` 造它用的是同一种办法）。
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO market_account (account_id, workspace, payload, stored_at) "
                "VALUES (?, ?, ?, ?)",
                ("an-account", "a-workspace", "{}", "2026-09-24T00:00:00Z"),
            )
            conn.execute("COMMIT")
        else:  # pragma: no cover - 参数表写错时要当场看见，而不是静默跳过
            raise AssertionError(f"没造过这种损坏：{kind}")
    finally:
        conn.close()
    return path


def test_check_reports_a_database_that_is_not_there_as_startable(tmp_path, bare_settings) -> None:
    """还没有库的部署：``absent``、``ok: true``、退 0，而且**自检没把它建出来**。

    这是本组唯一的正控 —— 其余各条都在证明「坏库起不来」，而一个把所有库都判成
    坏库的实现同样能通过它们。它同时钉住「第一次启动走得通」：一个全新的部署在
    任何东西写进去之前，自检必须是绿的。
    """
    ring = _ring(tmp_path, ("claude-control", "controller"))
    done = _run_check(
        {
            "MARKETOPS_DATA_ROOT": str(bare_settings.data_root),
            "MARKETOPS_MCP_TOKENS": str(ring),
        }
    )

    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload["ok"] is True
    assert payload["database"]["standing"] == "absent"
    assert payload["database"]["startable"] is True
    assert payload["database"]["recorded"] is None, "没读到的版本是 null，不是 0"
    assert not bare_settings.database_path.exists(), "--check 把状态库建出来了"


@pytest.mark.parametrize("kind", ["behind", "ahead", "nonsense", "unbound", "not-a-database"])
def test_a_database_this_build_cannot_open_stops_the_deployment(
    tmp_path, bare_settings, monkeypatch, kind: str, capsys
) -> None:
    """五种打不开的库，**自检与启动给同一个判决**：退 2，而且这个进程没有起来。

    这是 §15(c) 那一句的落地。它要的不是「报错更清楚」，而是**它不再长得像
    「工具坏了」**：``ensure_schema`` 的拒绝长在写路径上，而此前一个部署在那一刻
    之前什么都看不见 —— 凡是读写该库的工具全部失败，失败长成的样子是调用方上下文里
    一句没有 message 的 ``Error executing tool``（现场见
    ``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §G2）。「工具坏了」与「库还没迁移」
    的处置不同（去查代码 vs 跑一条命令），而当时唯一分得出它们的东西是一句没有正文
    的错误。

    三条失败各有一半是必须的，缺一条这个判据就会变成一句空话：

    * **``--check`` 退 2**，且输出里那一格说得清是哪一个库、欠着什么；
    * **``_serve()`` 也退 2**，而且 ``server.run`` **一次都没被调到** —— 一个
      「打印了警告但照样绑端口」的部署是这条闸最坏的形态：它看起来起来了；
    * **``ensure_data_dirs()`` 没有跑**。这一条最容易被漏掉，而它正是「状态」与
      「环境」的分界：那个调用会在库的父目录上 ``mkdir(parents=True)``，于是
      「目录还没准备好」这种故障会被这一次启动顺手修掉一半，下一次的读数就不再是
      它当初的样子。断言取的是**目录集合不变**，不是单个 ``state/evidence``：
      一个「建了另外几个目录」的实现照样违反这句话。
    """
    from mcp.server.mcpserver import MCPServer

    from marketops.interfaces.mcp.__main__ import _serve

    ring = _ring(tmp_path, ("claude-control", "controller"))
    damaged = _damage_database(bare_settings, kind)
    # 造库那一步自己建了 `state/`（`db.connect` 会建父目录），所以基线取在这里。
    directories_before = {path for path in bare_settings.data_root.rglob("*") if path.is_dir()}

    checked = _run_check(
        {
            "MARKETOPS_DATA_ROOT": str(bare_settings.data_root),
            "MARKETOPS_MCP_TOKENS": str(ring),
        }
    )
    assert checked.returncode == 2, (
        f"起不来的库必须让自检退 2，实际退 {checked.returncode}："
        "退 1 读出来是「谁都不许」，而那不是这个部署的问题。"
    )
    payload = json.loads(checked.stdout)
    assert payload["ok"] is False
    assert payload["database"]["startable"] is False
    # 造出来的损坏与报出来的处境**逐种对上**：一个把两种损坏归成同一格的实现，
    # 会让「版本那一格被写坏了」在报告里长得像「路径指错了」。
    assert payload["database"]["standing"] == {
        "behind": "behind",
        "ahead": "ahead",
        "nonsense": "unknown",
        "unbound": "unbound",
        "not-a-database": "unreadable",
    }[kind]
    assert payload["database"]["message"], "拒绝必须带一句说得出处置的话"
    assert str(damaged) in payload["database"]["message"], "要点名是哪一个库"

    ran: list[int] = []
    monkeypatch.setattr(MCPServer, "run", lambda self, **kwargs: ran.append(1))
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(bare_settings.data_root))
    monkeypatch.setenv("MARKETOPS_MCP_TOKENS", str(ring))

    assert _serve() == 2, "启动路径与自检必须对同一份配置给出同一个判决"
    assert ran == [], "拒绝之后还是把服务绑起来起 —— 那这条闸只在日志里成立"
    printed = capsys.readouterr().err
    assert str(damaged) in printed, "起不来那一次也要在 stderr 上点名那个库"

    directories_after = {path for path in bare_settings.data_root.rglob("*") if path.is_dir()}
    assert directories_after == directories_before, (
        "拒绝以后又把运行目录建了出来 —— 那会在下一次读数之前把这一条故障修掉一半"
    )


def test_a_database_the_check_refuses_is_the_verdict_the_store_raises(
    tmp_path, bare_settings
) -> None:
    """自检印出来的那句话，就是写路径会抛的那一句 —— **逐字**。

    两处各拼一遍那句拒绝是这条闸最可能长出来的形态，而它的症状很具体：自检说要跑
    A 那条命令、真正启动时说要跑 B，而工单里贴的永远是先跑到的那句。所以断言是
    相等，不是「都提到了迁移」—— 后者对漂开了几个字的副本照样绿。

    ``--check`` 走的是**子进程**（这是运维真的会敲的那一行），所以另一侧在这里用
    同一个库跑一次 ``ensure_schema``。
    """
    from marketops.adapters.sqlite import db
    from marketops.domain.errors import StoreError

    ring = _ring(tmp_path, ("claude-control", "controller"))
    damaged = _damage_database(bare_settings, "behind")

    done = _run_check(
        {
            "MARKETOPS_DATA_ROOT": str(bare_settings.data_root),
            "MARKETOPS_MCP_TOKENS": str(ring),
        }
    )
    checked_message = json.loads(done.stdout)["database"]["message"]

    conn = db.connect(damaged)
    try:
        with pytest.raises(StoreError) as excinfo:
            db.ensure_schema(conn)
    finally:
        conn.close()

    assert checked_message == str(excinfo.value)
    assert "adapters.sqlite.migrate" in checked_message, "这一条的前提是那句里有那条命令"


# ----------------------------------------------------------------------
# 研究侧的接线（Phase D）
# ----------------------------------------------------------------------
def test_a_deployment_without_a_serper_key_still_serves_the_research_tools(
    settings, tmp_path
) -> None:
    """没配 key 的部署**起得来**，而且入口是「没有提供方」而不是「没有这个工具」。

    ``SerperSearchProvider`` 的构造函数在无 key 时抛 ``ConfigError``，所以接线
    那一层必须**不构造它** —— 这条判据走的是真实服务面，断言的是那句特定的回答：
    一个没接线的部署必须说得出自己是没接线，而不是让调用方去猜「是不是我名字写
    错了」。两句话的排查方向完全不同（改部署 vs 改调用方）。

    断言没有落在 ``_browser`` 那种私有字段上，因为这里要问的不是「接线了没」，
    而是**接线的结果对不对**：句子是它唯一诚实的证据。
    """
    ring_path, token = _ring_with_token(tmp_path, ("claude-research", "researcher"))
    ring = TokenRing.from_file(ring_path)
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        research=wire_research(settings),
        calls=(("search_web", {"account_id": "an-account", "queries": ["x"]}),),
        want_tool_list=False,
    )

    assert not outcome.handshake_error, outcome.handshake_error
    result = outcome.call("search_web")
    assert result.is_error
    assert "没有接线检索提供方" in result.text
    assert "Unknown tool" not in result.text, "接线了却答成了「没有这个工具」"


def test_the_serper_predicate_is_shared_with_the_cli(settings) -> None:
    """接线用的谓词就是 ``Settings.serper_enabled``，不是这里另判一次。

    两个入口对**同一个谓词**的处置是相反的：CLI 没 key 就拒绝运行（它没有「假装
    跑过」这个选项），服务面没 key 照常起（它必须能被部署起来）。抄成两份实现
    会让它们某天对同一份配置给出相反的回答 —— 而那时看起来像其中一个是坏的。

    正控是另一半：这一条**在两种取值下都跑**。只测「没 key 时是 None」，一个
    恒为 None 的实现同样通过。

    表里的四把工具两种取值下都在 —— 缺 key 改的是 ``search_web`` 与
    ``run_discovery`` **被调用时**的答复（一句明确的「没接线检索提供方」），不是
    它们在不在这张表上。所以这里的名单是这条接线的**完整**产出，多一把就变红。
    """
    without = settings
    assert without.serper_enabled is False, "夹具不该带凭据"
    assert set(wire_research(without)) == {
        "crawl_site",
        "fetch_document",
        "run_discovery",
        "search_web",
    }

    # 带一个明显是假的 key 的 Settings：谓词为真，而这里**不发起任何请求** ——
    # 被断言的是「接线没有抛」，不是「检索成功了」。
    with_key = Settings(
        data_root=without.data_root,
        profiles_dir=without.profiles_dir,
        profile=without.profile,
        serper_api_key="not-a-real-key",
    )
    assert with_key.serper_enabled is True
    assert set(wire_research(with_key)) == {
        "crawl_site",
        "fetch_document",
        "run_discovery",
        "search_web",
    }


# ----------------------------------------------------------------------
# 决策侧的接线（Phase F）
# ----------------------------------------------------------------------
def _with_backend(settings: Settings, name: str | None) -> Settings:
    """同一份没凭据的 Settings 上，只换 ``MARKETOPS_DECISION_PROVIDER``。

    后端本身仍然构造得出来（``configured`` 用 ``find_spec``、不碰网络），所以这条
    用例测的是**接线**，不是「本机有没有装 SDK」。
    """
    return Settings(
        data_root=settings.data_root,
        profiles_dir=settings.profiles_dir,
        profile=settings.profile,
        decision_provider=name,
    )


def test_wire_decisions_returns_the_table_the_advisory_and_the_tools_together(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """三样来自**同一次构造** —— 而这不是性能要求。

    ``get_capabilities`` 按**对象同一性**回答「decide 会调用谁」，所以配置点名的
    那一个与表里的那一个必须是同一个对象；而 ``LayaDecisionProvider`` 每个实例各持
    一个 421M 参数的 Router，同一个 checkpoint 装两遍不是慢，是 OOM。两条理由都
    指向同一件事：这条接线里 ``default_decisions`` 只许被调一次。

    计数是必须的：``providers[advisory.name] is advisory`` 对「构造两遍、各取出
    同一个名字」也成立。
    """
    from marketops.interfaces import composition

    calls: list[int] = []
    original = composition.default_decisions

    def counted(inner_settings):
        calls.append(1)
        return original(inner_settings)

    monkeypatch.setattr(composition, "default_decisions", counted)

    tools, providers, advisory, records = wire_decisions(_with_backend(settings, "laya"))
    assert len(calls) == 1, "一次接线构造了两批后端 —— 那是两台 Router"
    assert set(tools) == {"decide", "get_decision"}, (
        "判定侧出两条：判一次，读回一次 —— 而读回不判、也不计费"
    )
    assert sorted(providers) == ["jev", "laya"], "两个都要构造，不筛 ready"
    assert advisory is not None
    assert providers[advisory.name] is advisory, "被选中的那个必须来自这一批"
    # 第四个返回值是**判定台账的工厂本身**，``_compose`` 把它一分为二交给
    # ``decide`` 与 ``submit_findings``。它是这个函数的返回值，而不是调用点上的
    # 一个 ``lambda``：两条路要落在**同一张表**上，而「同一个库」只有这里说得出来。
    assert callable(records), "判定台账的工厂要跟着接线一起交出去"


def test_an_unset_decision_provider_is_not_the_same_as_a_missing_one(settings) -> None:
    """没指定后端 ⇒ ``advisory`` 为 ``None``，而工具**照样接线**。

    判据在两种取值下都跑：只测「没指定时是 None」，一个恒为 ``None`` 的实现同样
    通过 —— 而那个实现在指定了后端时会静默不接，症状是「配了却还是说没配」。

    点名一个构造不出来的后端则是 ``ConfigError``：那是一次配置事故，而一个照常
    起来的服务会让运维以为 ``decide`` 在工作。
    """
    assert settings.decision_provider is None, "夹具不该带这个配置"
    tools, providers, advisory, _ = wire_decisions(settings)
    assert set(tools) == {"decide", "get_decision"}, (
        "没指定后端不等于没接线：decide 要答得出为什么；"
        "而记录存储是另一件事，它不受这条影响，读回照样可用"
    )
    assert advisory is None
    assert sorted(providers) == ["jev", "laya"]

    _, _, named, _ = wire_decisions(_with_backend(settings, "jev"))
    assert named is not None and named.name == "jev"

    with pytest.raises(ConfigError) as caught:
        wire_decisions(_with_backend(settings, "no-such-backend"))
    assert "no-such-backend" in str(caught.value)
    assert "jev" in str(caught.value), "要说清本部署能构造的是哪几个"


# ----------------------------------------------------------------------
# 多页抓取：前沿与页面**共用同一个抓取器**（Phase E2b）
# ----------------------------------------------------------------------
def test_the_frontier_and_the_pages_walk_through_one_fetcher(settings, bundle) -> None:
    """前沿读 robots / 站点地图，页面走 ``DocumentAcquisition`` —— 必须是同一个实例。

    ``SitemapFrontier`` 的文件头写了代价：两处各起一个 fetcher，会让超时、UA、
    重试策略变成一条**没人观察的差异**。而它在本仓不是靠自觉维持的：组合根只让
    ``default_crawl`` 收**一个**参数（抓取入口），前沿从它身上取 fetcher，所以
    「两份」在类型上不可表达 —— 这条判据是那句话的执行器。

    它摆的陷阱是组合根最容易走错的那一步：在 ``default_crawl`` 里自己再
    ``default_acquisition(settings)`` 一次。那样写**跑得起来**，两份 fetcher 也
    都正常工作，只是前沿那两次读走了另一个实例（于是超时与 UA 的差异没人看见）。
    所以断言落在**同一个替身**的流水账上：前沿那两次读必须出现在它的 ``asked`` 里。
    少了这一条，上面那句话就只是一句注释。

    这里不经服务面：被问的是组合根接得对不对，而经一遍 MCP 握手不会让这个回答
    更真 —— 它只会把判据的失败原因多包一层。
    """
    http = FakeHttp(settings.data_root / "pages", site_replies(SITE, SITE_PAGES))
    crawl = default_crawl(default_acquisition(settings, http=http))(
        bundle.profile.page_discovery_hints
    )
    report = crawl.crawl([SITE], purpose="site", pages=2, seconds=30.0)

    assert ROBOTS in http.asked, "前沿的 robots 读没有经过同一个抓取器"
    assert SITEMAP in http.asked, "前沿的站点地图读没有经过同一个抓取器"
    assert SITE in http.asked, "页面那一侧没有经过同一个抓取器"
    assert report.receipts, "正控：这条路一次都没真的取到页面，上面三条断言与真空同形"

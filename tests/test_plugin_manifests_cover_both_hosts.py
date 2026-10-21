"""两个宿主各自读得到自己的插件清单，且两份清单说的是同一个 server。

## 这一份守的是哪一句话

``marketops-plugin/`` 里原本只有 Codex 形状的清单：
``.codex-plugin/plugin.json`` + ``.codex-mcp.json``。Claude Code 不读这两份
—— 它在插件根找 ``.claude-plugin/plugin.json`` 与 ``.mcp.json``。于是同一个
插件目录在 Codex 里「Skill 与 MCP 都在」，在 Claude Code 里**只剩 Skill、零报错**：
一份读不到的清单不会失败，它只是不存在。

这不是一条新的产品决定，PR #61 已经把它写成「补两份清单即通」，但那一支从未
合入任何 main。这一份把当时的形状变成可执行的判据，而不是再写一遍解释。

## 为什么判据是这几个，不是「文件存在」

* 只判存在，两份清单可以指向**不同的 server 名** —— 而调用方看到的工具面
  取决于名字，不取决于文件在不在。
* 只判 server 名，清单可以写死一个 endpoint 或一枚 token —— 那是把部署事实
  和凭据复制进源码。端点与凭据必须由宿主在运行时解析。
* 只判占位符，``skills`` 可以指向一个不存在的目录 —— 插件装上了，Skill 却没有。

三条合起来才是「Claude Code 里 Skill 与 MCP 都在，且没有多携带一份权威事实」。

## 不启动任何进程

本文件只读 JSON 与文件系统，不拉起 MCP server、不读环境变量、不发网络请求。
它证明的是**清单的形状**，不是某个部署此刻连通。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "marketops-plugin"

CLAUDE_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
CLAUDE_MCP = PLUGIN_ROOT / ".mcp.json"
CODEX_MANIFEST = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
CODEX_MCP = PLUGIN_ROOT / ".codex-mcp.json"

#: 一枚**字面**凭据的形态下限。占位符 ``${NAME}`` 与 ``<placeholder>`` 都不匹配。
_LITERAL_SECRET = re.compile(r"(?i)\b(?:bearer\s+)?[A-Za-z0-9_\-\.=+/]{24,}\b")


def _json(path: Path) -> dict:
    assert path.is_file(), f"{path.relative_to(REPO_ROOT)} 不存在 —— 这个宿主读不到清单"
    return json.loads(path.read_text(encoding="utf-8"))


def test_both_hosts_have_a_manifest_and_an_mcp_file():
    for path in (CLAUDE_MANIFEST, CLAUDE_MCP, CODEX_MANIFEST, CODEX_MCP):
        assert path.is_file(), f"{path.relative_to(REPO_ROOT)} 缺失"


def test_the_two_manifests_name_the_same_plugin():
    claude = _json(CLAUDE_MANIFEST)
    codex = _json(CODEX_MANIFEST)
    assert claude["name"] == codex["name"] == "marketops"


def test_the_two_mcp_files_expose_the_same_server_name():
    """名字不一致时，调用方在 Claude 里看到的工具面与 Codex 里不是同一个。"""
    claude = _json(CLAUDE_MCP)["mcpServers"]
    codex = _json(CODEX_MCP)["mcpServers"]
    assert set(claude) == set(codex) == {"marketops"}


def test_claude_manifest_skills_point_at_a_real_directory():
    declared = _json(CLAUDE_MANIFEST)["skills"].rstrip("/")
    target = (PLUGIN_ROOT / declared).resolve()
    assert target.is_dir(), f"skills={declared!r} 指向 {target}，它不是目录"
    assert (target / "marketops" / "SKILL.md").is_file(), "插件声明了 skills/，但没有 marketops Skill"


def test_claude_mcp_resolves_endpoint_and_token_at_runtime():
    """端点与凭据都是宿主的事：源码里只许出现 ``${...}``，不许出现值。"""
    server = _json(CLAUDE_MCP)["mcpServers"]["marketops"]
    assert server["url"] == "${MARKETOPS_MCP_URL}"
    assert server["headers"]["Authorization"] == "Bearer ${MARKETOPS_MCP_TOKEN}"
    assert server["type"] == "http"


def test_neither_mcp_file_carries_a_literal_secret():
    """两份清单都不许把一枚真的 token 抄进来。

    ``${...}``、``<...>`` 与短名字（``MARKETOPS_MCP_TOKEN``）都不是凭据值。
    这一条挡的是「先在本地填好、提交时忘了删」那一种。
    """
    offenders: list[str] = []
    for path in (CLAUDE_MCP, CODEX_MCP):
        text = path.read_text(encoding="utf-8")
        for match in _LITERAL_SECRET.finditer(text):
            value = match.group(0)
            if value.startswith("${") or value.startswith("<"):
                continue
            if value.upper() == value and "_" in value:
                # 全大写的环境变量名不是值。
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {value[:8]}…")
    assert not offenders, "清单里出现了看起来像字面凭据的值：\n  " + "\n  ".join(offenders)


def test_the_two_hosts_do_not_share_a_filename():
    """共用一个文件名会让一个宿主的解析器去读另一个宿主的形状。

    这正是「Skill 在、MCP 不在」的成因：Claude Code 只认根上的 ``.mcp.json``，
    Codex 的 ``.codex-mcp.json`` 对它不存在。
    """
    assert CLAUDE_MCP.name != CODEX_MCP.name
    assert CLAUDE_MANIFEST != CODEX_MANIFEST

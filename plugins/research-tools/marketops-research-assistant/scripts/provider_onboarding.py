#!/usr/bin/env python3
"""Offline first-run provider guide; ships inside both native and WorkBuddy packages.

This file never contacts a vendor, reads browser cookies, imports vendor SDKs,
installs software, creates provider accounts or stores API-key values.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "references" / "PROVIDER_CONNECTIONS.json"
SUPPORTED_CLIENTS = ("claude", "codex", "workbuddy")
EXPECTED_IDS = frozenset({"tinyfish", "serpapi", "serper", "crawl4ai", "jev"})


def read_catalog(path: Path = CATALOG) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "marketops.provider-setup.v1":
        raise ValueError("UNSUPPORTED_PROVIDER_SETUP_SCHEMA")
    providers = data.get("providers")
    if not isinstance(providers, list) or {p.get("id") for p in providers
                                            if isinstance(p, dict)} != EXPECTED_IDS:
        raise ValueError("INVALID_PROVIDER_SETUP_CATALOG")
    if len(providers) != len(EXPECTED_IDS):
        raise ValueError("DUPLICATE_PROVIDER_SETUP_ID")
    for provider in providers:
        if not isinstance(provider.get("lite_credential_env"), str):
            raise ValueError("INVALID_PROVIDER_CREDENTIAL_NAME")
        for field in ("account_url", "docs_url"):
            val = provider.get(field)
            if not isinstance(val, str) or not val.startswith("https://"):
                raise ValueError("INVALID_PROVIDER_OFFICIAL_URL")
    return data


def _env_flag(name: str, environ: dict[str, str]) -> str:
    # Never echo, inspect, serialize or print the credential value.
    raw = environ.get(name, "")
    if not raw or not raw.strip() or re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", raw.strip()):
        return "NOT_CONFIGURED"
    return "CONFIGURED_NOT_AUTHENTICATED"


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ValueError, ImportError, ModuleNotFoundError):
        return False


def report(*, provider: str = "all", client: str = "claude",
           environ: dict[str, str] | None = None,
           path: Path = CATALOG) -> dict:
    if client not in SUPPORTED_CLIENTS:
        raise ValueError("UNSUPPORTED_SETUP_CLIENT")
    data = read_catalog(path)
    if provider != "all" and provider not in EXPECTED_IDS:
        raise ValueError("UNKNOWN_PROVIDER")
    env = os.environ if environ is None else environ
    selected = [p for p in data["providers"] if provider in ("all", p["id"])]
    package = {"mcp_python_sdk": _module_available("mcp"),
               "typesafe_python_sdk": _module_available("typesafe_sdk"),
               "crawl4ai_local_sdk": _module_available("crawl4ai"),
               "playwright_python_sdk": _module_available("playwright"),
               "browser_launch_verified": False,
               "runtime_is_venv": sys.prefix != sys.base_prefix}
    from research_official_mcp_auth import has_saved_tokens
    rows = []
    for entry in selected:
        mcp = entry["optional_official_mcp"]
        # No introspection of OAuth cookies, billing balances or client secrets:
        # an installed package cannot prove that a separate host MCP is logged in.
        rows.append({
            "id": entry["id"], "name": entry["label"],
            "role": entry["role"], "account_url": entry["account_url"],
            "docs_url": entry["docs_url"],
            "preferred": entry["preferred"],
            "lite": {"transport": entry["lite_transport"],
                     "credential_variable": entry["lite_credential_env"],
                     "credential_status": _env_flag(entry["lite_credential_env"], env)},
            "official_mcp": {
                "state": mcp["state"], "url": mcp["url"],
                "auth": mcp["auth"],
                "client_steps": mcp["claude_code"] if client == "claude"
                                else mcp["codex"] if client == "codex" else [],
                "embedded_client_bundled": bool(entry.get("embedded_actual_mcp_client", False)),
                "installed_and_authenticated": "AUTH_NOT_VERIFIED",
                "billing_verified": False,
            },
            "capabilities": entry["capabilities"],
            "separate_site_login": entry["separate_site_login"],
            "os_keyring_oauth_tokens_present": (has_saved_tokens() if
                entry["id"] == "tinyfish" else False),
        })
    return {
        "status": "GUIDE_ONLY_NO_NETWORK_OR_AUTH", "schema": data["schema"],
        "client": client, "provider_count": len(rows), "local_runtime": package,
        "providers": rows,
        "paid_calls_enabled": False,
        "host_mcp_registration_performed": False,
        "account_registration_performed": False,
        "browser_profile_login_performed": False,
        "provider_fees_known": False,
    }


def format_text(data: dict) -> str:
    out = [
        "# MarketOps — 首次配置供应商",
        "",
        "本报告只检查本地安装线索；不会登录账户、发起网络调用或读取/显示密钥值。",
        "MarketOps自身已内嵌TinyFish/SerpApi的官方HTTP MCP客户端，不必再额外在宿主装两家MCP；仍需本机账号OAuth/Key和预算授权。",
        "",
        "## 已打包的本地运行能力",
    ]
    installed = data["local_runtime"]
    for key in ("mcp_python_sdk", "typesafe_python_sdk", "crawl4ai_local_sdk",
                "playwright_python_sdk"):
        out.append(f"- {key}: {'已发现' if installed[key] else '未发现（可选择安装）'}")
    out.append("- Chromium 启动状态：未在此工具验证")
    for p in data["providers"]:
        out.extend([
            "",
            f"## {p['name']}",
            f"- 注册/账户入口：{p['account_url']}",
            f"- 官方安装/说明：{p['docs_url']}",
            f"- 推荐接入：{p['preferred']}",
            f"- Lite 可选 Key：{p['lite']['credential_variable']} "
            f"({p['lite']['credential_status']})；不会验证真实身份或扣费。",
            f"- 官方 MCP：{p['official_mcp']['state']}（MarketOps内嵌：{p['official_mcp']['embedded_client_bundled']}）；"
            f"认证方式：{p['official_mcp']['auth'] or '未核实官方独立 MCP'}",
        ])
        for step in p["official_mcp"]["client_steps"]:
            out.append(f"- 客户端待执行步骤（需用户批准）：{step}")
        if p["id"] == "tinyfish":
            out.append("- 可选：使用安装凭据所列的research_official_mcp_auth.py命令，由账号持有人在本机完成OAuth；OS Keyring中仅报告凭据有无（不是实际已认证）。")
            out.append("- TinyFish Agent收费功能默认禁用，不会因为OAuth成功自动开启。")
        if p["id"] == "crawl4ai":
            out.append("- 本地 SDK 不需要云端 API Key；安装浏览器需要另行明确授权，云端和Docker可选。")
        if p["separate_site_login"]:
            out.append("- 平台登录和目标网站 Profile 登录分开；用户自行授权指定站点，勿复制Cookie。")
    out.extend([
        "",
        "## 下一步",
        "1. 仅选择实际需要的服务，到官网注册；不要求一次申请五个账号。",
        "2. Lite REST/SDK 的 Key 在客户 OS Secret Store/环境或WorkBuddy密码输入中配置；不要输入聊天或提交到Git。",
        "3. TinyFish/SerpApi官方MCP客户端已内嵌；用户自行配置API Key或在本机用可选OAuth命令完成TinyFish登录，确认实际 tools/list 与账户额度。",
        "4. 先配置两条本地数据路径。网络与付费上限默认关闭；授权时明确项目、站点、调用次数与钱包。",
        "5. 先做不付费的tools/list与只读状态检查，再考虑小规模真实调研。",
    ])
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("all", "tinyfish", "serpapi", "serper",
                                               "crawl4ai", "jev"), default="all")
    parser.add_argument("--client", choices=SUPPORTED_CLIENTS, default="claude")
    parser.add_argument("--json", action="store_true", help="machine-readable safe local report")
    args = parser.parse_args(argv)
    try:
        value = report(provider=args.provider, client=args.client)
        print(json.dumps(value, ensure_ascii=False, indent=2) if args.json
              else format_text(value))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Never echo paths, raw files, environment values, or foreign error bodies.
        print(json.dumps({"status": "GUIDE_ERROR",
                          "reason_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

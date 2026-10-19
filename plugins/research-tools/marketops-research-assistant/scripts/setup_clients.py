#!/usr/bin/env python3
"""Agent-assisted, opt-in local installer; no credentials, provider calls or remote execution."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
VERSION = "0.4.0"
CLIENTS = ("codex", "workbuddy", "both", "claude", "all")


def _python(venv_path: Path) -> Path:
    return venv_path / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def plan(destination: Path, client: str) -> dict:
    if client not in CLIENTS:
        raise ValueError("UNSUPPORTED_SETUP_CLIENT")
    dest = destination.expanduser().resolve()
    if dest == SOURCE or SOURCE in dest.parents or dest in SOURCE.parents:
        raise ValueError("Destination must not overwrite or nest within plugin checkout")
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError("Refusing to replace an existing install; select a new versioned destination")
    if sys.version_info < (3, 11):
        raise ValueError("Requires Python 3.11 or newer")
    return {
        "version": VERSION, "client": client, "destination": str(dest),
        "plugin_copy": str(dest / "plugins" / "marketops-research-assistant"),
        "customer_data_root": None,
        "experience_file": None,
        "storage_configuration": "USE_EXISTING_CONFIG_OR_ASK_TWO_PATHS_ON_FIRST_USE",
        "no_live_provider_calls": True, "no_secrets_written": True,
        "install_deps": "separate opt-in; only local venv packages",
        "bootstrap_runtime": "explicit opt-in downloads Python packages and Chromium",
        "local_provider_guide": "references/CUSTOMER_QUICKSTART.md",
    }


def install(destination: Path, client: str, *, dependencies: bool,
            register_codex: bool, local_crawl: bool = False,
            browser: bool = False) -> dict:
    # Validate all permissions before touching the install directory.
    if browser and not local_crawl:
        raise ValueError("BROWSER_REQUIRES_LOCAL_CRAWL")
    if local_crawl and not dependencies:
        raise ValueError("LOCAL_CRAWL_REQUIRES_RUNTIME_DEPENDENCIES")
    result = plan(destination, client)
    # Never install source with malformed manifests, Skills or Python syntax.
    from preflight import report as static_report
    outcome = static_report()
    if outcome["errors"]:
        raise ValueError("STATIC_PREFLIGHT_FAILED: review preflight.py results")
    dest = Path(result["destination"])
    target = dest / "plugins" / "marketops-research-assistant"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never package local customer data even if someone accidentally created it
    # under the source directory. The distributable is only code, Skills and docs.
    shutil.copytree(SOURCE, target,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc", ".pytest_cache", ".venv",
                        "dist", "runs", "scratch", "data", "state", "artifacts",
                        "exports", "experience", "search-receipts",
                        "*.zip", "*.sqlite3", "*.sqlite3-*", "*.db",
                        "*.jsonl", ".env", ".env.*", "credentials.json",
                        # `tests/` is the plugin's own suite, not product: a
                        # customer install must not ship our test tree (it also
                        # carries fixtures and this repository's synthetic
                        # credential literals).
                        "tests", "htmlcov", ".coverage"))
    # Installation chooses code/toolchain locations only. Never create a data
    # directory or a new experience file before the user selects the two paths.
    # Existing configuration is read by the runtime, not modified by install.
    python_home = dest / ".venv"
    venv.EnvBuilder(with_pip=True).create(python_home)
    py = _python(python_home)
    # Install from the hash-pinned lock when it is present: every package, at one
    # exact version, with a verified digest. The plain range file stays in the
    # tree as the input the lock was resolved from, and as the fallback if a
    # future tree ships without a lock — but that fallback is recorded, because
    # an unverified install is not the reproducible one.
    dependency_sources = []
    for ranges, lock in (("requirements-lite.txt", "requirements-lock-lite.txt"),
                         ("requirements-crawl-local.txt", "requirements-lock-crawl-local.txt")):
        if not (dependencies if ranges.startswith("requirements-lite") else local_crawl):
            continue
        locked = (target / lock).is_file()
        requirement = target / (lock if locked else ranges)
        command = [str(py), "-m", "pip", "install"]
        if locked:
            command.append("--require-hashes")
        command += ["-r", str(requirement)]
        subprocess.run(command, check=True)
        dependency_sources.append({"file": requirement.name, "hash_pinned": locked})
    browser_root = dest / "browsers"
    if browser:
        # Keep Chromium inside the customer's versioned install directory.
        # This survives isolated HOME and cross-client launch differences.
        # The download is explicit, not a paid vendor API or site interaction.
        browser_root.mkdir(mode=0o700)
        env = dict(os.environ)
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
        subprocess.run([str(py), "-m", "playwright", "install", "chromium"],
                       check=True, env=env)
    # Bind the installed Codex plugin to this customer's venv instead of PATH.
    # No keys are written; env_vars remain inherited from the client process.
    for config_name in ("mcp.json", ".mcp.json"):
        config_path = target / config_name
        config = json.loads(config_path.read_text(encoding="utf-8"))
        servers = config.get("mcpServers") or config.get("mcp_servers") or {}
        for server in servers.values():
            server["command"] = str(py)
            # Every generated client config names the one managed MarketOps
            # entrypoint `scripts/research_mcp.py` — the same module the plugin's
            # manifests and the source-tree configs point at, so no client can
            # load a second (or a differently-built) server. An absolute
            # interpreter path also removes the POSIX-shell entry, which is what
            # makes the generated config usable on Windows.
            server["args"] = [str(target / "scripts" / "research_mcp.py")]
            # Codex may cache a local plugin under a different plugin root and
            # rejects a cwd outside that root. The script uses __file__, so cwd
            # is unnecessary: let Codex select its installed plugin directory.
            server.pop("cwd", None)
            if browser:
                server.setdefault("env", {})["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    # Offline smoke probe over the tree that was just written. Static audits only:
    # they need no third-party package, so this runs even without --install-deps.
    # A failure is recorded, never raised: the install receipt must be able to say
    # "installed but not verified" rather than silently claiming success.
    probe = subprocess.run(
        [str(py), str(target / "scripts" / "client_install_probe.py"),
         "--plugin-root", str(target), "--artifact", "installed", "--no-wire"],
        text=True, capture_output=True)
    try:
        probe_report = json.loads(probe.stdout)
    except (TypeError, ValueError):
        # A probe that did not answer is a failed probe, never a silent pass.
        probe_report = {"ok": False, "findings": [{"kind": "PROBE_DID_NOT_ANSWER"}]}
    results = {"status": "SOURCE_INSTALLED_UNVERIFIED", **result,
               "install_smoke_probe": probe_report,
               "install_smoke_probe_ok": bool(probe_report.get("ok")),
               "venv_python": str(py), "dependencies_installed": dependencies,
               "dependency_sources": dependency_sources,
               "local_crawl_sdk_install_requested": local_crawl,
               "chromium_download_requested": browser,
               "browser_cache": str(browser_root) if browser else None,
               "browser_launch_verified": False,
               "provider_login_performed": False, "paid_provider_calls": 0,
               "external_official_mcp_registered": False,
               "provider_onboarding_commands": {
                   name: [str(py), str(target / "scripts" / "provider_onboarding.py"),
                          "--client", name]
                   for name in ("claude", "codex", "workbuddy")
                   if name == client or client == "all"
                   or (client == "both" and name in {"codex", "workbuddy"})
               },
               "provider_onboarding_command": [
                   str(py), str(target / "scripts" / "provider_onboarding.py"),
                   "--client", ("claude" if client in {"claude", "all"} else
                                "codex" if client in {"codex", "both"} else "workbuddy")],
               "provider_onboarding_guide": str(target / "references" / "CUSTOMER_QUICKSTART.md"),
               "provider_connection_catalog": str(target / "references" / "PROVIDER_CONNECTIONS.json"),
               "tinyfish_own_mcp_oauth_argv": [str(py),
                   str(target / "scripts" / "research_official_mcp_auth.py"),
                   "login", "--provider", "tinyfish"],
               "tinyfish_oauth_ready": False}
    if client in {"claude", "all"}:
        # Official vendor MCPs require the user's separate client OAuth/setup.
        results["claude_next_argv"] = ["claude", "--plugin-dir", str(target)]
        results["claude_host_registration"] = "USER_START_REQUIRED_NOT_AUTOMATIC"
    if client in {"codex", "both", "all"}:
        root = dest / ".agents" / "plugins"
        root.mkdir(parents=True, exist_ok=True)
        marketplace = {
            "name": "marketops-local",
            "interface": {"displayName": "MarketOps Private Local Plugins"},
            "plugins": [{
                "name": "marketops-research-assistant",
                "source": {"source": "local", "path": "./plugins/marketops-research-assistant"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }],
        }
        (root / "marketplace.json").write_text(json.dumps(marketplace, indent=2) + "\n",
                                                encoding="utf-8")
        results["codex_marketplace"] = str(dest)
        results["codex_next"] = "codex plugin marketplace add " + str(dest)
        if register_codex:
            if shutil.which("codex") is None:
                raise ValueError("CODEX_CLI_UNAVAILABLE: marketplace remains generated, not registered")
            subprocess.run(["codex", "plugin", "marketplace", "add", str(dest)], check=True)
            results["codex_marketplace_registered"] = True
        else:
            results["codex_marketplace_registered"] = False
    if client in {"workbuddy", "both", "all"}:
        # Run builder with the installed venv interpreter on *this machine*.
        # Its mcp.json paths are fixed to the customer-controlled stable package directory.
        command = [str(py), str(target / "scripts" / "build_client_bundles.py"),
                   "--output", str(dest / "connectors"), "--with-research-mcp"]
        if browser:
            command += ["--browser-cache", str(browser_root)]
        completed = subprocess.run(command, check=True, text=True,
                                   capture_output=True)
        results["workbuddy_bundle"] = json.loads(completed.stdout)
        results["workbuddy_import_status"] = "NOT_IMPORTED_USER_OR_AGENT_MUST_IMPORT_DIRECTORY"
    starter = dest / "START_HERE.md"
    starter.write_text(
        "# MarketOps 安装后的账号与连接向导\n\n"
        "插件代码已经复制到此目录，但厂商 OAuth/Key、额度和网页登录仍须用户授权。\n\n"
        "1. 打开 [五家Provider第一次使用指南](plugins/marketops-research-assistant/references/CUSTOMER_QUICKSTART.md)。\n"
        "2. 用 INSTALL_RECEIPT.json 的 provider_onboarding_command 检查当前安装，不会联网或输出Key。\n"
        "3. 首次在市场研究 MCP 中填写两条客户自己选择的绝对数据路径。\n"
        "4. TinyFish/SerpApi官方MCP已由MarketOps自身集成：用本地OS Keyring OAuth或API Key认证，不必额外安装它们的宿主插件。\n"
        "5. 其他厂商按需连接，不默认启用付费Agent。\n\n"
        "安装路径和状态见同目录 INSTALL_RECEIPT.json；未验证供应商登录或客户MCP真实启动。\n",
        encoding="utf-8")
    results["start_here"] = str(starter)
    (dest / "INSTALL_RECEIPT.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True,
                        help="new empty customer-owned directory outside repository")
    parser.add_argument("--client", choices=CLIENTS, required=True)
    parser.add_argument("--apply", action="store_true", help="actually create local files/venv")
    parser.add_argument("--install-deps", action="store_true",
                        help="explicit download of mcp and TypeSafe SDK dependencies into private venv")
    parser.add_argument("--install-local-crawl", action="store_true",
                        help="also download Crawl4AI SDK; requires --install-deps")
    parser.add_argument("--install-crawl-browser", action="store_true",
                        help="also download Chromium for Playwright; requires --install-local-crawl")
    parser.add_argument("--bootstrap-runtime", action="store_true",
                        help="one-step opt-in to download core SDKs, local crawler and Chromium")
    parser.add_argument("--register-codex", action="store_true",
                        help="explicitly run the Codex marketplace add command")
    args = parser.parse_args()
    if not args.apply and (args.install_deps or args.register_codex or
                           args.install_local_crawl or args.install_crawl_browser or
                           args.bootstrap_runtime):
        parser.error("dependency/browser downloads and registration require --apply")
    if args.register_codex and args.client not in {"codex", "both", "all"}:
        parser.error("--register-codex requires codex, both or all")
    if args.install_local_crawl and not (args.install_deps or args.bootstrap_runtime):
        parser.error("--install-local-crawl requires --install-deps or --bootstrap-runtime")
    if args.install_crawl_browser and not (args.install_local_crawl or args.bootstrap_runtime):
        parser.error("--install-crawl-browser requires --install-local-crawl or --bootstrap-runtime")
    try:
        result = (install(
                      args.destination, args.client,
                      dependencies=args.install_deps or args.bootstrap_runtime,
                      register_codex=args.register_codex,
                      local_crawl=args.install_local_crawl or args.bootstrap_runtime,
                      browser=args.install_crawl_browser or args.bootstrap_runtime
                  ) if args.apply
                  else {"status": "DRY_RUN", **plan(args.destination, args.client)})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        # Error output deliberately excludes stdout/stderr from command execution.
        print(json.dumps({"status": "INSTALL_BLOCKED",
                          "reason_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

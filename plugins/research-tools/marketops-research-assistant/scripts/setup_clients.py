#!/usr/bin/env python3
"""Agent-assisted, opt-in local installer; no credentials, provider calls or remote execution."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import venv
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
VERSION = "0.4.0"


def _python(venv_path: Path) -> Path:
    return venv_path / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def plan(destination: Path, client: str) -> dict:
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
        "customer_data_root": str(dest / "data"),
        "no_live_provider_calls": True, "no_secrets_written": True,
        "install_deps": "separate opt-in; only local venv packages",
    }


def install(destination: Path, client: str, *, dependencies: bool,
            register_codex: bool) -> dict:
    result = plan(destination, client)
    dest = Path(result["destination"])
    target = dest / "plugins" / "marketops-research-assistant"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, target,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache",
                                                  ".venv", "dist", "runs", "scratch", "*.zip"))
    # Runtime storage intentionally lives OUTSIDE plugin versioned source files.
    (dest / "data").mkdir(mode=0o700)
    python_home = dest / ".venv"
    venv.EnvBuilder(with_pip=True).create(python_home)
    py = _python(python_home)
    if dependencies:
        subprocess.run([str(py), "-m", "pip", "install", "-r",
                        str(target / "requirements-lite.txt")], check=True)
    # Bind the installed Codex plugin to this customer's venv instead of PATH.
    # No keys are written; env_vars remain inherited from the client process.
    for config_name in ("mcp.json", ".mcp.json"):
        config_path = target / config_name
        config = json.loads(config_path.read_text(encoding="utf-8"))
        servers = config.get("mcpServers") or {}
        for server in servers.values():
            server["command"] = str(py)
            server["args"] = [str(target / "scripts" / "research_mcp.py")]
            server["cwd"] = str(target)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    results = {"status": "SOURCE_INSTALLED_UNVERIFIED", **result,
               "venv_python": str(py), "dependencies_installed": dependencies}
    if client in {"codex", "both"}:
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
    if client in {"workbuddy", "both"}:
        # Run builder with the installed venv interpreter on *this machine*.
        # Its mcp.json paths are fixed to the customer-controlled stable package directory.
        command = [str(py), str(target / "scripts" / "build_client_bundles.py"),
                   "--output", str(dest / "connectors"), "--with-research-mcp"]
        completed = subprocess.run(command, check=True, text=True,
                                   capture_output=True)
        results["workbuddy_bundle"] = json.loads(completed.stdout)
        results["workbuddy_import_status"] = "NOT_IMPORTED_USER_OR_AGENT_MUST_IMPORT_DIRECTORY"
    (dest / "INSTALL_RECEIPT.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True,
                        help="new empty customer-owned directory outside repository")
    parser.add_argument("--client", choices=("codex", "workbuddy", "both"), required=True)
    parser.add_argument("--apply", action="store_true", help="actually create local files/venv")
    parser.add_argument("--install-deps", action="store_true",
                        help="allow pip to download pinned-range SDK packages")
    parser.add_argument("--register-codex", action="store_true",
                        help="explicitly run the Codex marketplace add command")
    args = parser.parse_args()
    if not args.apply and (args.install_deps or args.register_codex):
        parser.error("--install-deps and --register-codex require --apply")
    if args.register_codex and args.client == "workbuddy":
        parser.error("--register-codex requires codex or both")
    try:
        result = (install(args.destination, args.client, dependencies=args.install_deps,
                          register_codex=args.register_codex) if args.apply
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

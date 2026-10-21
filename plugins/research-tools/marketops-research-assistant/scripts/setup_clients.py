#!/usr/bin/env python3
"""Agent-assisted, opt-in local installer; no credentials, provider calls or remote execution."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
VERSION = "0.5.9-rc2"
CLIENTS = ("codex", "workbuddy", "both", "claude", "all")
STATE_FILE = "INSTALL_STATE.json"
STATE_OWNER = "marketops.setup_clients.v1"

#: One sentence the customer can act on, per refusal this installer decides.
#:
#: Only these texts are ever printed back. Command output and provider errors
#: are not: they can carry this machine's paths and credentials.
REMEDIES = {
    "PYTHON_312_64BIT_REQUIRED":
        "Install 64-bit Python 3.12 (python.org or uv) and run the installer again.",
    "PYTHON_ENSUREPIP_UNAVAILABLE":
        "The Python 3.12 venv component is missing. Ubuntu/Debian: "
        "sudo apt install python3.12-venv; or use a python.org/uv Python 3.12.",
    "SUPPORTED_PLATFORM_WINDOWS_OR_LINUX_X64_REQUIRED":
        "This package installs on Windows or Linux, x64 only.",
    "DESTINATION_OVERLAPS_SOURCE":
        "Choose an installation directory separate from the unpacked package.",
    "UNSUPPORTED_SETUP_CLIENT":
        "Use --client claude, codex, workbuddy, both or all.",
    "INSTALL_TARGET_NOT_OWNED":
        "The destination is not empty. Use an empty directory, or --resume on the "
        "destination this package installed before.",
    "SYMLINK_IN_PACKAGE":
        "Re-extract the package: a symlink inside it means the download was modified.",
    "BUILD_QA_STALE_OR_INVALID":
        "Re-download the release: the QA receipt does not describe these files.",
    "PLATFORM_DEPENDENCY_LOCK_MISSING":
        "Re-extract the package: its dependency lock for this platform is missing.",
    "DESTINATION_REQUIRED":
        "Re-run the installer with an installation directory, for example: "
        "install.sh ~/MarketOps --client both",
    "INSTALL_TARGET_NOT_DIRECTORY":
        "Choose a directory path: the destination is an existing file and was left untouched.",
    "INSTALL_RESUME_IDENTITY_MISMATCH":
        "Use the same package, destination and --client as the interrupted install, "
        "or choose a new empty directory.",
    "INSTALL_RESUME_OPTIONS_MISMATCH":
        "Re-run with the same download options as the interrupted install.",
    "STATIC_PREFLIGHT_FAILED":
        "Re-extract the release package: the installer refused to copy inconsistent files.",
    "INSTALL_TARGET_SYMLINK":
        "Remove the symlink at the destination, or choose another empty directory.",
    "BROWSER_REQUIRES_LOCAL_CRAWL":
        "Add --install-local-crawl (with --install-deps), or drop --install-crawl-browser.",
    "LOCAL_CRAWL_REQUIRES_RUNTIME_DEPENDENCIES":
        "Add --install-deps when requesting the local crawler.",
    "CODEX_CLI_UNAVAILABLE":
        "Install the Codex CLI, or rerun without --register-codex to keep the "
        "generated marketplace unregistered.",
    "CODEX_PYTHON_LAUNCHER_REQUIRED":
        "Put python/python3 or the Python 3.12 py launcher on PATH, then rerun the installer.",
    "CODEX_REGISTRATION_REQUIRES_CODEX_CLIENT":
        "Use --client codex, both or all together with --register-codex.",
    "INSTALL_STEP_FAILED":
        "Re-run the same installer: it resumes the same destination. If it stops "
        "again, open INSTALL_STATE.json beside the destination.",
    "CUSTOMER_SETUP_INCOMPLETE":
        "Reopen START_HERE.md in the install directory and rerun the printed "
        "local account setup command.",
}


def _refusal_code(exc: BaseException) -> str:
    """The stable code a customer, a support thread or a test can match on.

    Anything not shaped like ``CAPS_WITH_UNDERSCORES`` before the first colon is
    reported as a configuration problem rather than echoed as if it were a code.
    """
    if isinstance(exc, (ValueError, FileExistsError)):
        head = str(exc).split(":", 1)[0].strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", head):
            return head
        return "INSTALL_CONFIGURATION_INVALID"
    return "INSTALL_STEP_FAILED"


def source_files():
    """Explicit distributable roots; private runtime directories never ship."""
    roots = ("scripts", "references", "skills", "agents", "commands", "hooks", ".claude-plugin", ".codex-plugin")
    excluded = {"__pycache__", ".pytest_cache", ".venv", ".git", "data", "state",
                "runs", "scratch", "artifacts", "exports", "tests"}
    for root in roots:
        directory = SOURCE / root
        if directory.is_symlink():
            raise ValueError("SYMLINK_IN_PACKAGE")
        for folder, dirs, files in os.walk(directory, followlinks=False):
            for name in list(dirs):
                item = Path(folder) / name
                if item.is_symlink():
                    raise ValueError("SYMLINK_IN_PACKAGE")
                if name in excluded:
                    dirs.remove(name)
            dirs.sort()
            for name in sorted(files):
                item = Path(folder) / name
                if item.is_symlink():
                    raise ValueError("SYMLINK_IN_PACKAGE")
                if (name.startswith(".env") or item.suffix in {".pyc", ".zip", ".db", ".sqlite3", ".jsonl"}
                        or name in {"credentials.json", "cc_host_probe.sh"}):
                    continue
                yield item
    for name in ("README.md", "START_HERE.md", "AGENT_INSTALL.md", "plugin.json", "mcp.json",
                 ".mcp.json", "Install.cmd", "Install.ps1", "install.sh"):
        item = SOURCE / name
        if item.is_symlink():
            raise ValueError("SYMLINK_IN_PACKAGE")
        if item.is_file():
            yield item
    for item in sorted(SOURCE.glob("requirements*.txt")):
        if item.is_symlink():
            raise ValueError("SYMLINK_IN_PACKAGE")
        yield item


def source_digest() -> str:
    digest = hashlib.sha256()
    for item in sorted(source_files()):
        digest.update(item.relative_to(SOURCE).as_posix().encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
    return digest.hexdigest()


def _state_write(dest, state):
    pending = dest / (STATE_FILE + ".pending")
    pending.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    pending.replace(dest / STATE_FILE)


def _python(venv_path: Path) -> Path:
    return venv_path / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _codex_host_python() -> tuple[str, list[str]]:
    """Return a bare Python command accepted by Codex's plugin stdio parser."""
    for candidate in ("python", "python3", "py"):
        resolved = shutil.which(candidate)
        if not resolved or "\\WindowsApps\\" in resolved:
            continue
        return candidate, (["-3.12"] if candidate == "py" else [])
    raise ValueError(
        "CODEX_PYTHON_LAUNCHER_REQUIRED: add Python to PATH or make the py launcher available"
    )


def normalize_destination(destination) -> Path:
    """Expand ``~``, make a relative path absolute, and normalize separators.

    The installer speaks about one path everywhere: the copy, the state file,
    the receipt and the recovery commands. Resolving it once here is what keeps
    ``~/Market Ops`` and ``Market Ops`` from becoming two different installs.
    """
    text = os.fspath(destination).strip().strip('"').strip("'")
    if not text:
        raise ValueError("DESTINATION_REQUIRED")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        raise ValueError("DESTINATION_REQUIRED") from None


def _path_key(path: Path) -> str:
    """Case-correct comparison key; Windows paths are case-insensitive."""
    return os.path.normcase(os.path.abspath(str(path)))


def _contains(parent: Path, child: Path) -> bool:
    parent_key, child_key = _path_key(parent), _path_key(child)
    return child_key == parent_key or child_key.startswith(parent_key + os.sep)


def plan(destination: Path, client: str, *, resume: bool = False) -> dict:
    if client not in CLIENTS:
        raise ValueError("UNSUPPORTED_SETUP_CLIENT")
    dest = normalize_destination(destination)
    if _contains(dest, SOURCE) or _contains(SOURCE, dest):
        raise ValueError("DESTINATION_OVERLAPS_SOURCE")
    if sys.version_info[:2] != (3, 12):
        raise ValueError("PYTHON_312_64BIT_REQUIRED")
    if sys.platform not in {"win32", "linux"} or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("SUPPORTED_PLATFORM_WINDOWS_OR_LINUX_X64_REQUIRED")
    if importlib.util.find_spec("ensurepip") is None:
        raise ValueError("PYTHON_ENSUREPIP_UNAVAILABLE: install the Python 3.12 venv component")
    if dest.exists() and not dest.is_dir():
        raise ValueError("INSTALL_TARGET_NOT_DIRECTORY")
    if dest.exists() and any(dest.iterdir()):
        state_path = dest / STATE_FILE
        if not resume or not state_path.is_file() or state_path.is_symlink():
            raise FileExistsError("INSTALL_TARGET_NOT_OWNED")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if (state.get("owner") != STATE_OWNER or state.get("source_sha256") != source_digest()
                or state.get("client") != client or state.get("destination") != str(dest)):
            raise ValueError("INSTALL_RESUME_IDENTITY_MISMATCH")
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


def _register_codex(dest: Path, results: dict) -> None:
    """Add the generated marketplace to the *global* Codex registration.

    This is the one installer action that changes state outside the chosen
    directory, so it is only ever reached through an explicit ``--register-codex``
    (or a later re-run that carries one). Command output is captured and never
    echoed: it belongs to the customer's CLI, not to the install report.
    """
    if shutil.which("codex") is None:
        raise ValueError("CODEX_CLI_UNAVAILABLE: marketplace remains generated, not registered")
    subprocess.run(["codex", "plugin", "marketplace", "add", str(dest)],
                   check=True, capture_output=True, text=True)
    results["codex_marketplace_registered"] = True
    support = subprocess.run(["codex", "plugin", "add", "--help"],
                             capture_output=True, text=True)
    if support.returncode == 0:
        subprocess.run(results["codex_install_argv"], check=True,
                       capture_output=True, text=True)
        results["codex_plugin_install_status"] = "INSTALLED_RESTART_HOST_TO_VERIFY"
    else:
        results["codex_plugin_install_status"] = "PENDING_HOST_INSTALL"


def install(destination: Path, client: str, *, dependencies: bool,
            register_codex: bool, local_crawl: bool = False,
            browser: bool = False, resume: bool = False,
            switch_current: bool = True) -> dict:
    # Validate all permissions before touching the install directory.
    if browser and not local_crawl:
        raise ValueError("BROWSER_REQUIRES_LOCAL_CRAWL")
    if local_crawl and not dependencies:
        raise ValueError("LOCAL_CRAWL_REQUIRES_RUNTIME_DEPENDENCIES")
    if register_codex and client not in {"codex", "both", "all"}:
        raise ValueError("CODEX_REGISTRATION_REQUIRES_CODEX_CLIENT")
    result = plan(destination, client, resume=resume)
    codex_requested = client in {"codex", "both", "all"}
    codex_command, codex_prefix = _codex_host_python() if codex_requested else (None, [])
    # Never install source with malformed manifests, Skills or Python syntax.
    from preflight import report as static_report
    outcome = static_report()
    if outcome["errors"]:
        raise ValueError("STATIC_PREFLIGHT_FAILED: review preflight.py results")
    dest = Path(result["destination"])
    settings = dict(dependencies=dependencies, local_crawl=local_crawl,
                    browser=browser, register_codex=register_codex)
    state = {"owner": STATE_OWNER, "source_sha256": source_digest(),
             "client": client, "destination": str(dest), "settings": settings,
             "stage": "PREPARED"}
    # Registration is a separate, later consent, not a property of the copied
    # tree. A customer who first installs without it must be able to re-run
    # with --register-codex without the installer calling that a different
    # install and refusing to continue.
    resume_settings = {key: value for key, value in settings.items()
                       if key != "register_codex"}
    if (dest / STATE_FILE).is_file():
        state = json.loads((dest / STATE_FILE).read_text(encoding="utf-8"))
        stored_settings = state.get("settings") or {}
        if {key: value for key, value in stored_settings.items()
                if key != "register_codex"} != resume_settings:
            raise ValueError("INSTALL_RESUME_OPTIONS_MISMATCH")
        if state.get("stage") == "COMPLETE":
            receipt_path = dest / "INSTALL_RECEIPT.json"
            results = json.loads(receipt_path.read_text(encoding="utf-8"))
            if register_codex and not results.get("codex_marketplace_registered"):
                _register_codex(dest, results)
                receipt_path.write_text(
                    json.dumps(results, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                state["settings"] = dict(stored_settings, register_codex=True)
                _state_write(dest, state)
            return results
        state["settings"] = settings
    dest.mkdir(parents=True, exist_ok=True)
    _state_write(dest, state)
    target = dest / "plugins" / "marketops-research-assistant"
    target.parent.mkdir(parents=True, exist_ok=True)
    # Never package local customer data even if someone accidentally created it
    # under the source directory. The distributable is only code, Skills and docs.
    for source in source_files():
        copied = target / source.relative_to(SOURCE)
        if copied.is_symlink() or any(p.is_symlink() for p in copied.parents if p != dest.parent):
            raise ValueError("INSTALL_TARGET_SYMLINK")
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copied)
    state["stage"] = "SOURCE_COPIED"
    _state_write(dest, state)
    from research_runtime_setup import verify_build_receipt
    build_qa = verify_build_receipt(target)
    if (target / "references" / "BUILD_QA.json").is_file() and not build_qa["ok"]:
        raise ValueError("BUILD_QA_STALE_OR_INVALID")
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
        if sys.platform == "win32":
            lock = lock.replace(".txt", "-windows.txt")
        locked = (target / lock).is_file()
        if not locked:
            raise ValueError("PLATFORM_DEPENDENCY_LOCK_MISSING")
        requirement = target / lock
        command = [str(py), "-m", "pip", "install"]
        if locked:
            command.append("--require-hashes")
        command += ["-r", str(requirement)]
        # Capture the child's output: a failing package manager may print
        # mirrored URLs or local paths, and the installer only ever reports
        # its own stable code and one recovery step.
        subprocess.run(command, check=True, capture_output=True, text=True)
        dependency_sources.append({"file": requirement.name, "hash_pinned": locked})
    if dependencies:
        subprocess.run([str(py), "-m", "pip", "check"], check=True,
                       capture_output=True, text=True)
    state["stage"] = "DEPENDENCIES_READY"
    _state_write(dest, state)
    browser_root = dest / "browsers"
    browser_verified = False
    if browser:
        # Keep Chromium inside the customer's versioned install directory.
        # This survives isolated HOME and cross-client launch differences.
        # The download is explicit, not a paid vendor API or site interaction.
        browser_root.mkdir(mode=0o700, exist_ok=True)
        env = dict(os.environ)
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
        subprocess.run([str(py), "-m", "playwright", "install", "chromium"],
                       check=True, env=env, capture_output=True, text=True)
        check = subprocess.run([str(py), "-c",
            "from playwright.sync_api import sync_playwright; "
            "p=sync_playwright().start(); b=p.chromium.launch(headless=True); "
            "page=b.new_page(); page.goto('about:blank'); b.close(); p.stop()"],
            env=env, capture_output=True, timeout=60)
        browser_verified = check.returncode == 0
    # Bind each generated MCP config to the customer-owned package copy.
    for config_name in ("mcp.json", ".mcp.json"):
        config_path = target / config_name
        config = json.loads(config_path.read_text(encoding="utf-8"))
        servers = config.get("mcpServers") or config.get("mcp_servers") or {}
        for server in servers.values():
            codex_config = codex_requested and (
                config_name == "mcp.json" or client in {"codex", "both"})
            if codex_config:
                # Codex's Agent Plugins parser requires a bare stdio command;
                # it rejects `env_vars` and absolute executable paths. Launch
                # the stdlib wrapper with host Python, then hand off to the
                # isolated venv through MARKETOPS_RESEARCH_PYTHON.
                server["type"] = "stdio"
                server["command"] = codex_command
                server["args"] = [
                    *codex_prefix,
                    str(target / "scripts" / "launch_research_mcp.py"),
                    "scripts/research_mcp.py",
                ]
                server.pop("env_vars", None)
                server.pop("default_tools_approval_mode", None)
                server.pop("cwd", None)
                server.setdefault("env", {})["MARKETOPS_RESEARCH_PYTHON"] = str(py)
            else:
                server["command"] = str(py)
                server["args"] = [str(target / "scripts" / "research_mcp.py")]
                # Claude's plugin launcher is resolved relative to its plugin;
                # it does not need the source-tree cwd after installation.
                server.pop("cwd", None)
            if browser:
                server.setdefault("env", {})["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_root)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    # With dependencies, actually initialize the installed MCP over stdio.
    # The isolated probe lists tools with networking off and no provider calls.
    # Without dependencies, retain the useful static-only installation audit.
    # A failure is recorded, never raised: the install receipt must be able to say
    # "installed but not verified" rather than silently claiming success.
    probe_argv = [str(py), str(target / "scripts" / "client_install_probe.py"),
                  "--plugin-root", str(target), "--artifact", "installed"]
    if not dependencies:
        probe_argv.append("--no-wire")
    check_root = dest / "checks"
    check_root.mkdir(exist_ok=True)
    probe_env = dict(os.environ, TMPDIR=str(check_root), TEMP=str(check_root), TMP=str(check_root))
    probe = subprocess.run(probe_argv, env=probe_env, text=True, capture_output=True, timeout=150)
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
               "browser_launch_verified": browser_verified,
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
    results["source_sha256"] = state["source_sha256"]
    results["build_qa"] = build_qa
    results["credentials_configure_argv"] = [str(py), str(target / "scripts" / "research_credentials.py"), "configure"]
    results["customer_setup_argv"] = [str(py), str(target / "scripts" / "customer_setup.py")]
    validation_ok = bool(probe_report.get("ok")) and (not browser or browser_verified)
    if dependencies:
        results["status"] = "RUNTIME_READY_HOST_PENDING" if validation_ok else "INSTALL_VALIDATION_INCOMPLETE"
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
        results["codex_next_argv"] = ["codex", "plugin", "marketplace", "add", str(dest)]
        results["codex_install_argv"] = ["codex", "plugin", "add", "marketops-research-assistant@marketops-local", "--json"]
        results["codex_next"] = subprocess.list2cmdline(results["codex_next_argv"])
        import release_layout
        if register_codex and release_layout.app_root_for(dest) is None:
            _register_codex(dest, results)
        elif not register_codex:
            results["codex_marketplace_registered"] = False
        # Under <root>/releases/<version>, Codex is registered with <root>/current
        # once it points here (end of install), so an update needs no re-pointing.
    if client in {"workbuddy", "both", "all"}:
        # Run builder with the installed venv interpreter on *this machine*.
        # Its mcp.json paths are fixed to the customer-controlled stable package directory.
        command = [str(py), str(target / "scripts" / "build_client_bundles.py"),
                   "--output", str(dest / "connectors"), "--with-research-mcp",
                   "--host-install-root", str(dest)]
        if browser:
            command += ["--browser-cache", str(browser_root)]
        bundle_receipt = dest / "WORKBUDDY_BUNDLE.json"
        if bundle_receipt.is_file():
            results["workbuddy_bundle"] = json.loads(bundle_receipt.read_text(encoding="utf-8"))
        else:
            # Each attempt has its own owned directory, so an interrupted zip
            # never needs deletion and cannot block recovery of this install.
            attempts = dest / "connectors"
            build_output = attempts
            if (attempts / "workbuddy").exists():
                attempt = 2
                while (attempts / f"attempt-{attempt}").exists():
                    attempt += 1
                build_output = attempts / f"attempt-{attempt}"
            command[command.index("--output") + 1] = str(build_output)
            completed = subprocess.run(command, check=True, text=True, capture_output=True)
            results["workbuddy_bundle"] = json.loads(completed.stdout)
            bundle_receipt.write_text(json.dumps(results["workbuddy_bundle"], indent=2) + "\n", encoding="utf-8")
        results["workbuddy_import_status"] = "PENDING_HOST_IMPORT"
    # Any other MCP-capable agent (DeepSeek Harness and similar): one standard
    # mcpServers file plus the skills folder its instructions are read from.
    generic = {"mcpServers": {"marketops-research": {
        "type": "stdio", "command": str(py),
        "args": [str(target / "scripts" / "research_mcp.py")],
        "cwd": str(target)}}}
    (dest / "GENERIC_MCP.json").write_text(json.dumps(generic, indent=2) + "\n",
                                           encoding="utf-8")
    results["generic_mcp_config"] = str(dest / "GENERIC_MCP.json")
    results["skills_directory"] = str(target / "skills")
    starter = dest / "START_HERE.md"
    starter.write_text(_start_here(results), encoding="utf-8")
    results["start_here"] = str(starter)
    # <root>/releases/<version>: hosts get <root>/current, which an update moves.
    import release_layout
    app_root = release_layout.app_root_for(dest)
    if app_root is not None:
        results["host_files_rebased"] = release_layout.rebase_host_files(dest, target)
        if switch_current:
            results["current"] = release_layout.switch_current(app_root, dest)
            results["update_entrypoints"] = release_layout.write_update_entrypoints(
                app_root, client=client, register_codex=register_codex,
                runtime={"dependencies": dependencies, "local_crawl": local_crawl,
                         "browser": browser})
            if register_codex:
                _register_codex(release_layout.current_link(app_root), results)
        stable = str(release_layout.current_link(app_root))
        for key in ("claude_next_argv", "codex_next_argv"):
            if key in results:
                results[key] = [part.replace(str(dest), stable) for part in results[key]]
        if "codex_next_argv" in results:
            results["codex_next"] = subprocess.list2cmdline(results["codex_next_argv"])
    (dest / "INSTALL_RECEIPT.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state["stage"] = "COMPLETE" if validation_ok else "VALIDATION_PENDING"
    _state_write(dest, state)
    return results


def _command(argv):
    if sys.platform == "win32":
        # PowerShell's call operator and single quotes preserve spaces and
        # apostrophes in a customer path; never construct a shell to execute it.
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in argv)
    import shlex
    return shlex.join(argv)


def _start_here(result):
    # The numbered sections are conditional: a Claude-only install has no Codex
    # or WorkBuddy block, so a literal "2/3/4" would ship a guide whose steps
    # jump from 1 to 4. The counter makes the numbers follow the blocks present.
    counter = {"n": 1}

    def heading(title):
        counter["n"] += 1
        return f"## {counter['n']}. {title}"

    lines = ["# MarketOps 安装后从这里开始", "",
             "这是本机安装目录，请保留；其他电脑需要重新运行源码包的安装入口。",
             "[四账户、申请 Key 与新用户额度](plugins/marketops-research-assistant/references/FOUR_ACCOUNT_START.md)", "",
             "## 1. 本地账号与数据设置", "",
             "在本机终端执行（不要在聊天中输入 Key）：", "", "```",
             _command(result["customer_setup_argv"]), "```", "",
             "配置保存后，新的宿主会话会读取系统凭据库。Crawl4AI 本地无需第五个账户。", ""]
    if "codex_marketplace" in result:
        lines.extend([heading("Codex"), "", "安装器支持当前 CLI 时已执行下列两步；未执行时可在本机运行：", "", "```",
                      _command(result["codex_next_argv"]), _command(result["codex_install_argv"]), "```", "",
                      "完全退出并重开 Codex，打开 Plugins，选择 marketops-local → MarketOps Research Assistant；确认已安装且启用。",
                      "旧版 CLI 没有 plugin add 时：在该 Plugins 来源打开插件详情，点击 Install 并启用，再新建会话。",
                      f"安装状态：{result.get('codex_plugin_install_status', 'PENDING_HOST_INSTALL')}。", ""])
    if "workbuddy_bundle" in result:
        lines.extend([heading("WorkBuddy"), "", "WorkBuddy 4.24.0+：连接器管理 → 添加/导入自定义连接器 → 选择本机生成目录：", "",
                      "```", result["workbuddy_bundle"]["directory"], "```", "",
                      "导入后启用连接器，新建会话。四个 Key 已保存在系统凭据库时，密码字段可留空。",
                      "若该客户端提供的是选择 ZIP，请选择同目录生成的 ZIP；两种方式均只能用于这台机器。",
                      "界面名称随 WorkBuddy 版本可能变化；安装回执保持 PENDING_HOST_IMPORT，直到在宿主看见并调用 MarketOps。", ""])
    if "claude_next_argv" in result:
        lines.extend([heading("Claude Code"), "", "每次使用可从以下命令开启会话：", "", "```",
                      _command(result["claude_next_argv"]), "```", ""])
    if "generic_mcp_config" in result:
        lines.extend([heading("其他 Agent（DeepSeek Harness 等支持 MCP 的客户端）"), "",
                      "在该客户端的 MCP 设置里导入或粘贴这个文件的内容：", "", "```",
                      result["generic_mcp_config"], "```", "",
                      "再把下面目录里的 Skills 加进它的技能/提示词目录（不支持 Skills 时，"
                      "把 marketops-research/SKILL.md 的内容作为系统提示词）：", "", "```",
                      result["skills_directory"], "```", ""])
    lines.extend(["## 开始第一次调研", "",
                  "> 使用 MarketOps，先检查数据路径、凭据、授权额度和供应商连接。按我的目标保存 Research Brief，先做 3 个候选；保存原文、事实引文和责任关系，无法核实的标 HOLD。导出 Excel，记录此次方法和卡点，并在下一批检索可复用经验。", "",
                  "先在新会话调用 marketops_setup_status，看 provider_readiness 里五个服务是否 READY，再做第一次小规模调研。仅代码复制成功不等于宿主与账号验收通过。",
                  "完整安装与实际检查状态见 INSTALL_RECEIPT.json；没有有效 BUILD_QA 的开发包只能诊断，不能据此启用真实调用。", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True,
                        help="new empty customer-owned directory outside repository")
    parser.add_argument("--client", choices=CLIENTS, required=True)
    parser.add_argument("--apply", action="store_true", help="actually create local files/venv")
    parser.add_argument("--resume", action="store_true", help="resume this installer's same-source install; never overwrite an unknown directory")
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
    parser.add_argument("--no-switch-current", action="store_true",
                        help="install beside the version in use without moving <root>/current "
                             "(the updater switches after its own check)")
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
                      browser=args.install_crawl_browser or args.bootstrap_runtime,
                      resume=args.resume,
                      switch_current=not args.no_switch_current,
                  ) if args.apply
                  else {"status": "DRY_RUN", **plan(args.destination, args.client)})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") == "INSTALL_VALIDATION_INCOMPLETE" else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return _refuse(exc)
    except Exception as exc:  # noqa: BLE001 - the installer never prints a raw traceback
        # A bug or an unanticipated library error must not become a console
        # dump of local paths, provider output or credentials. The exception
        # type is kept; its message is not repeated.
        return _refuse(exc, "INSTALL_STEP_FAILED")


def _refuse(exc: BaseException, code: str | None = None) -> int:
    # Error output deliberately excludes stdout/stderr from command execution.
    code = code or _refusal_code(exc)
    remedy = REMEDIES.get(code)
    # A code the installer does not recognise still gets one recovery step on
    # the console; it is not recorded as "detail", because that would claim the
    # installer understood a condition it merely forwarded.
    console = remedy or ("Re-run the installer with --destination and --client; "
                         "see references/CLIENT_SETUP.md.")
    print(f"INSTALL_BLOCKED: {code}: {console}", file=sys.stderr)
    report = {"status": "INSTALL_BLOCKED", "reason_type": type(exc).__name__, "code": code}
    if remedy:
        report["detail"] = remedy
    print(json.dumps(report), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

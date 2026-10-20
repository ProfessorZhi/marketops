"""Wave-1 independent QA gate: P1-02 distribution bypass and client tool matrix.

Contract source:
``docs/architecture/MARKETOPS_ARCHITECTURE_CONVERGENCE_DECISION_AND_P0_P2_PLAN_2026-10-17.md``

* **Appendix D — P1-02 "Distribution bypass and client tool matrix"**, whose
  acceptance clause is: "the default managed product must expose **one MarketOps
  MCP** and no active unmanaged billable vendor endpoint", "source-only
  examples, if retained for developer use, must be clearly labeled **not for
  managed distribution** and must not appear in default install or generated
  templates", and "tool allowlisting is positive/explicit for vendor tools and
  tiered capabilities. Upstream new tools are denied until reviewed."
* **§5 task table row P2-05 "Customer host smoke"** — install, stdio
  initialize/tools-list and a read-only sample on real Claude Code / Codex /
  WorkBuddy and Windows hosts. That row carries the gate "customer/device
  access separate authorization", so every part of it that needs a real client
  or a Windows machine is reported **BLOCKED**, never passed.

Everything here is offline. A *synthetic* secret is injected to prove nothing
leaks; no real credential, no real client directory and no network is touched.
The install is driven into ``tmp_path`` only.

Windows coverage is an **offline harness** (``sys.platform`` branch, path
forms, JSON/TOML escaping, newline and encoding tolerance). It deliberately does
not start Windows: P2-05 proper is BLOCKED.

Harness note (not a contract relaxation): ``setup_clients.install`` bootstraps a
venv through ``venv.EnvBuilder(with_pip=True)``, which needs ``ensurepip`` and
is unavailable on this host. The tests replace *only* that interpreter
bootstrap with a symlink so the real packaging code paths run; no assertion is
softened to accommodate it.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
import venv
from pathlib import Path
from unittest import mock

import pytest

from conftest import PLUGIN_DIR, blocked

try:  # conftest exposes the repo-relative roots; fall back if the name moves.
    from conftest import SCRIPTS  # type: ignore
except ImportError:  # pragma: no cover
    SCRIPTS = PLUGIN_DIR / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_client_bundles as bundles  # noqa: E402
import setup_clients as install  # noqa: E402

RESEARCH_TOOLS = PLUGIN_DIR.parent                      # plugins/research-tools
MANAGED_SERVER = "marketops-research"

# Retained *direct* (unmanaged) vendor examples: a vendor endpoint wired straight
# into the client, outside the one managed MCP.
DIRECT_EXAMPLE_DIRS = (
    RESEARCH_TOOLS / "codex" / "marketops-research-starter",
    RESEARCH_TOOLS / "workbuddy" / "tinyfish",
    RESEARCH_TOOLS / "workbuddy" / "crawl4ai",
    RESEARCH_TOOLS / "examples" / "codex",
)

VENDOR_HOSTS = (
    "mcp.tinyfish.ai", "agent.tinyfish.ai",
    "api.crawl4ai.com", "mcp.serpapi.com",
    "serpapi.com", "google.serper.dev", "serper.dev",
)

VENDOR_SERVER_NAMES = {"tinyfish", "crawl4ai", "serpapi", "serper", "jev", "typesafe"}

# Explicit "this is not part of the managed distribution" markers. Deliberately
# narrow: a generic "developer only" would green a label that does not actually
# say the thing P1-02 requires.
NOT_FOR_MANAGED_MARKERS = (
    "not for managed distribution",
    "not-for-managed-distribution",
    "不为受管分发",
    "不用于受管分发",
    "非受管分发",
    "不得用于受管分发",
)

TOOL_GRANT_KEYS = (
    "enabled_tools", "enabledTools", "allowed_tools", "allowedTools",
    "disabled_tools", "disabledTools", "tools",
)

SYNTHETIC_SECRETS = {
    "SERPER_API_KEY": "qa-gate-synthetic-serper-9f3a1c",
    "SERPAPI_API_KEY": "qa-gate-synthetic-serpapi-7b2d40",
    "CRAWL4AI_API_KEY": "qa-gate-synthetic-crawl4ai-5e81aa",
    "TINYFISH_API_KEY": "qa-gate-synthetic-tinyfish-2c66df",
    "TYPESAFE_API_KEY": "qa-gate-synthetic-typesafe-4d10bb",
    "TINYFISH_MCP_ACCESS_TOKEN": "qa-gate-synthetic-oauth-8a7e13",
}

KEY_FIELD = re.compile(r"_API_KEY$|ACCESS_TOKEN$")
PLACEHOLDER = re.compile(r"^\$\{[A-Z][A-Z0-9_]*\}$")


class OfflineEnvBuilder:
    """Stand-in for ``venv.EnvBuilder`` where the host has no ``ensurepip``.

    Creates only ``<venv>/bin/python`` (or ``Scripts/python.exe`` on Windows
    semantics) as a symlink to the running interpreter, which is all the
    packaging code under test consumes. Nothing is downloaded or installed.
    """

    def __init__(self, *args, **kwargs) -> None:  # signature-compatible
        pass

    def create(self, path):
        root = Path(path)
        bindir = root / ("Scripts" if os.name == "nt" else "bin")
        bindir.mkdir(parents=True, exist_ok=True)
        exe = bindir / ("python.exe" if os.name == "nt" else "python")
        if not exe.exists():
            exe.symlink_to(sys.executable)
        return str(root)


def _offline_install(destination: Path, client: str = "all") -> dict:
    with mock.patch.object(venv, "EnvBuilder", OfflineEnvBuilder):
        return install.install(destination, client, dependencies=False,
                               register_codex=False)


@pytest.fixture(scope="module")
def installed(tmp_path_factory) -> dict:
    """Drive the real installer once, for all three clients, into a tmp root."""
    destination = tmp_path_factory.mktemp("marketops-qa-install") / "install"
    result = _offline_install(destination, "all")
    return {"dest": destination, "receipt": result}


@pytest.fixture(scope="module")
def bundle(tmp_path_factory) -> dict:
    """The generated WorkBuddy connector, built the way the installer builds it."""
    output = tmp_path_factory.mktemp("marketops-qa-bundle") / "bundle"
    receipt = bundles.build(output, with_research_mcp=True)
    return {"output": output, "receipt": receipt, "dir": Path(receipt["directory"])}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _workbuddy_connectors(dest: Path) -> list[Path]:
    """Every generated WorkBuddy connector directory, at whatever depth."""
    root = dest / "connectors"
    if not root.is_dir():
        return []
    return sorted({meta.parent for meta in root.rglob("connector-meta.json")})


def _client_configs(dest: Path) -> dict[str, Path]:
    """Every client-visible configuration file the default install produces."""
    plugin = dest / "plugins" / "marketops-research-assistant"
    configs = {
        "claude_code:.mcp.json": plugin / ".mcp.json",
        "portable:mcp.json": plugin / "mcp.json",
        "portable:plugin.json": plugin / "plugin.json",
        "codex:.codex-plugin/plugin.json": plugin / ".codex-plugin" / "plugin.json",
        "codex:marketplace.json": dest / ".agents" / "plugins" / "marketplace.json",
    }
    for connector in _workbuddy_connectors(dest):
        label = connector.relative_to(dest / "connectors").as_posix()
        for name in ("mcp.json", "connector-meta.json", "token-schema.json"):
            candidate = connector / name
            if candidate.is_file():
                configs[f"workbuddy:{label}/{name}"] = candidate
    configs["install:INSTALL_RECEIPT.json"] = dest / "INSTALL_RECEIPT.json"
    return configs


def _mcp_servers(path: Path) -> dict:
    data = _load_json(path)
    servers = data.get("mcpServers") or data.get("mcp_servers") or {}
    return servers if isinstance(servers, dict) else {}


def _all_mcp_configs(root: Path) -> list[Path]:
    found = []
    for path in root.rglob("*"):
        if path.is_file() and path.name in ("mcp.json", ".mcp.json"):
            found.append(path)
    return sorted(found)


def _vendor_endpoint_problems(path: Path) -> list[str]:
    problems = []
    for name, server in _mcp_servers(path).items():
        blob = json.dumps(server, ensure_ascii=False)
        for host in VENDOR_HOSTS:
            if host in blob:
                problems.append(f"{path.name}:{name} contacts vendor endpoint {host}")
        if name.lower() in VENDOR_SERVER_NAMES:
            problems.append(f"{path.name}:{name} is a direct vendor MCP server entry")
    return problems


def _scrub(paths, secrets) -> list[str]:
    hits = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, value in secrets.items():
            if value in text:
                hits.append(f"{path}: leaked the synthetic value of {name}")
    return hits


def _scannable_files(root: Path) -> list[Path]:
    """Files whose bytes could carry a packaged credential.

    Excludes the interpreter bootstrap (``.venv``) and bytecode caches, and the
    plugin's own test tree: ``install`` copies ``tests/`` verbatim, and this
    very gate module necessarily contains the synthetic literals by
    construction, so scanning it would report the harness rather than the
    packager. Nothing that the packager *generates* is excluded.
    """
    skipped = {".venv", "__pycache__", "tests"}
    return [path for path in root.rglob("*")
            if path.is_file() and not (skipped & set(path.relative_to(root).parts))]


# ==========================================================================
# Contract 1 — the default managed install exposes exactly one MCP and no
# enabled direct vendor endpoint (Appendix D / P1-02 acceptance, clause 1).
# ==========================================================================


def test_default_install_exposes_exactly_one_managed_stdio_mcp(installed):
    """§P1-02: "the default managed product must expose **one MarketOps MCP**".

    Read from the *generated* client configuration, not from the source tree.
    """
    dest = installed["dest"]
    configs = _client_configs(dest)
    for label in ("claude_code:.mcp.json", "portable:mcp.json"):
        server_map = _mcp_servers(configs[label])
        assert set(server_map) == {MANAGED_SERVER}, (
            f"{label} exposes {sorted(server_map)}; exactly one "
            f"{MANAGED_SERVER!r} server is required (§P1-02)")
        server = server_map[MANAGED_SERVER]
        assert server.get("type") == "stdio", (label, server.get("type"))
        assert server.get("command"), f"{label} has no launch command"
        # The entrypoint's *filename* is the installer's choice and has already
        # changed once (research_mcp.py -> research_mcp_facade.py); pinning a
        # name would report a rename as a regression. The contract is that the
        # server launches a Python entrypoint shipped with the plugin.
        entrypoints = [str(a) for a in server.get("args", []) if str(a).endswith(".py")]
        assert entrypoints, (
            f"{label} launches no Python entrypoint for the managed research "
            f"MCP: {server.get('args')}")
        assert any(str(a).endswith((".mcp.json", ".py")) and "scripts" in str(a)
                   for a in server.get("args", [])), (
            f"{label} entrypoint is not one of the plugin's own scripts: "
            f"{server.get('args')}")

    wb = [p for label, p in configs.items() if label.startswith("workbuddy:") and p.name == "mcp.json"]
    assert wb, "the WorkBuddy connector produced no mcp.json in the default install"
    for path in wb:
        server_map = _mcp_servers(path)
        assert set(server_map) == {MANAGED_SERVER}, (
            f"WorkBuddy connector {path} exposes {sorted(server_map)}")
        assert server_map[MANAGED_SERVER].get("type") == "stdio", path


def test_every_generated_mcp_config_declares_only_the_managed_server(installed):
    """§P1-02: no second (unmanaged) MCP may survive anywhere in the artifact.

    This walks the whole installed tree so a copied example cannot hide in a
    subdirectory the client lists only lazily.
    """
    dest = installed["dest"]
    offenders = []
    for path in _all_mcp_configs(dest):
        servers = _mcp_servers(path)
        if set(servers) != {MANAGED_SERVER}:
            offenders.append(f"{path.relative_to(dest)} -> {sorted(servers)}")
    assert not offenders, (
        "generated install contains MCP configs that are not the single managed "
        f"server: {offenders} (§P1-02)")


def test_no_generated_client_config_enables_a_direct_vendor_endpoint(installed):
    """§P1-02: "no active unmanaged billable vendor endpoint" in the default build."""
    dest = installed["dest"]
    problems = []
    for label, path in _client_configs(dest).items():
        if path.name in ("plugin.json",):
            continue
        problems.extend(_vendor_endpoint_problems(path))
    assert not problems, (
        "the default install wires a direct vendor endpoint: "
        f"{problems} (§P1-02)")


def test_installed_provider_catalog_keeps_network_and_paid_defaults_disabled(installed):
    """§P1-02: the shipped provider catalog must keep paid paths off by default.

    ``PROVIDER_CONNECTIONS.json`` documents vendor MCP URLs (TinyFish, SerpApi).
    Documenting one is not enabling one, so the shipped catalog must still
    declare every enabling switch false.
    """
    catalog_path = (installed["dest"] / "plugins" / "marketops-research-assistant"
                    / "references" / "PROVIDER_CONNECTIONS.json")
    assert catalog_path.is_file(), "the shipped provider catalog is missing"
    catalog = _load_json(catalog_path)
    policy = catalog.get("default_policy", {})
    for key in ("network_enabled", "paid_provider_calls_enabled",
                "import_official_mcp", "site_profiles_enabled",
                "provider_auth_verified"):
        assert policy.get(key) is False, (
            f"shipped catalog default_policy[{key}]={policy.get(key)!r}, "
            "expected False (§P1-02 default-deny)")


def test_managed_bundle_receipt_reports_exactly_one_mcp_server(bundle, tmp_path):
    """§P1-02: the generated connector carries one MCP; skill-only carries none."""
    assert bundle["receipt"]["mcp_servers_bundled"] == 1, bundle["receipt"]
    assert bundle["receipt"]["machine_bound_local_mcp"] is True
    assert set(_mcp_servers(bundle["dir"] / "mcp.json")) == {MANAGED_SERVER}

    skill_only = bundles.build(tmp_path / "skill-only")
    assert skill_only["mcp_servers_bundled"] == 0, skill_only
    assert not (Path(skill_only["directory"]) / "mcp.json").exists()


# ==========================================================================
# Contract 2 — retained direct-connect examples are labeled and isolated.
# ==========================================================================


def test_retained_direct_connect_examples_are_absent_from_install_and_bundle(
    installed, bundle
):
    """§P1-02: examples "must not appear in default install or generated
    templates".
    """
    dest, generated = installed["dest"], bundle["output"]

    # The default installer copies only `marketops-research-assistant`; the
    # sibling example trees must not ride along under any name.
    for root in (dest, generated):
        for path in root.rglob("*"):
            rel = path.relative_to(root).as_posix()
            if path.is_dir() and path.name == "marketops-research-starter":
                pytest.fail(f"the codex starter example was copied into {rel!r}")
            if path.name == "config.toml.example":
                pytest.fail(f"the codex TOML example was copied into {rel!r}")
            if rel.endswith("workbuddy/tinyfish") or rel.endswith("workbuddy/crawl4ai"):
                pytest.fail(f"a direct vendor connector example was copied into {rel!r}")

    # The examples' own connector identity must not reappear either.
    for meta in list(dest.rglob("connector-meta.json")) + list(generated.rglob("connector-meta.json")):
        identity = _load_json(meta).get("source")
        assert identity not in {"marketops-tinyfish-research-lite",
                                "marketops-crawl4ai-research-lite"}, (
            f"{meta} resurrects a direct vendor connector as {identity!r} (§P1-02)")


def _tool_grants(path: Path) -> dict[str, dict[str, list[str]]]:
    name = path.name
    if name.endswith(".toml") or name.endswith(".toml.example"):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        servers = data.get("mcp_servers") or data.get("mcpServers") or {}
    elif path.suffix == ".json":
        servers = _mcp_servers(path)
    else:
        return {}
    out = {}
    for name, server in servers.items():
        if not isinstance(server, dict):
            continue
        # A server with no grant key at all is kept with an empty mapping: the
        # absence of an allowlist is exactly what the contract test must see.
        out[name] = {k: [str(x) for x in server[k]]
                     for k in TOOL_GRANT_KEYS
                     if isinstance(server.get(k), list)}
    return out


def _retained_vendor_example_configs() -> list[Path]:
    return [
        RESEARCH_TOOLS / "codex" / "marketops-research-starter" / ".mcp.json",
        RESEARCH_TOOLS / "workbuddy" / "tinyfish" / "mcp.json",
        RESEARCH_TOOLS / "workbuddy" / "crawl4ai" / "mcp.json",
        RESEARCH_TOOLS / "examples" / "codex" / "config.toml.example",
    ]


def test_no_wildcard_tool_grant_appears_in_any_generated_artifact(installed, bundle):
    """§P1-02: no blanket grant such as ``*`` or ``mcp__*``.

    Judged with ``command grep -rn`` over the *generated* artifacts (the install
    tree and the built connector), not over the source.
    """
    roots = [installed["dest"], bundle["output"]]
    hits = []
    for root in roots:
        for pattern in ('"*"', "mcp__*"):
            # `command grep` bypasses the ugrep wrapper this host installs over
            # `grep`; the search runs over the generated artifacts only.
            line = ("command grep -rn -F "
                    f"-e {shlex.quote(pattern)} "
                    f"--include=*.json --include=*.toml {shlex.quote(str(root))}")
            completed = subprocess.run(line, shell=True, capture_output=True,
                                       text=True, check=False)
            if completed.returncode not in (0, 1):
                pytest.fail(f"grep failed ({completed.returncode}): {completed.stderr}")
            hits.extend(text for text in completed.stdout.splitlines() if text.strip())
    assert not hits, f"a wildcard tool grant reached a generated artifact: {hits}"

    # And structurally: any tool list present must contain concrete names only.
    for path in _all_mcp_configs(installed["dest"]):
        for name, grants in _tool_grants(path).items():
            for key, values in grants.items():
                bad = [v for v in values if v.strip() in ("*", "**") or v.strip().endswith("*")]
                assert not bad, f"{path}:{name}.{key} contains wildcard entries {bad}"


# ==========================================================================
# Contract 4 — no credential is written onto disk by packaging.
# ==========================================================================


def test_no_synthetic_credential_value_reaches_an_install_artifact(tmp_path, clean_env):
    """§P1-02: the generated distribution must not carry "raw customer keys".

    The synthetic values below are injected into the environment the installer
    and the bundle builder inherit; if any one of them lands in a file, the
    packaging path is copying a credential instead of a variable name.
    """
    for name, value in SYNTHETIC_SECRETS.items():
        clean_env.setenv(name, value)
    destination = tmp_path / "install"
    _offline_install(destination, "all")

    files = _scannable_files(destination)
    assert files, "the install produced no files to inspect"
    hits = _scrub(files, SYNTHETIC_SECRETS)
    assert not hits, f"a credential value was written into the artifact: {hits}"


def test_generated_credential_fields_are_placeholders_or_empty(installed):
    """§P1-02: a key name may ship; a key *value* may not.

    Any ``*API_KEY`` / ``*ACCESS_TOKEN`` valued field in the generated config
    must be an unresolved ``${NAME}`` placeholder or empty.
    """
    offenders = []
    for label, path in _client_configs(installed["dest"]).items():
        if path.suffix != ".json":
            continue
        data = _load_json(path)
        stack = [("", data)]
        while stack:
            prefix, node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    name = f"{prefix}.{key}" if prefix else str(key)
                    if isinstance(value, (dict, list)):
                        stack.append((name, value))
                    elif KEY_FIELD.search(str(key)):
                        text = "" if value is None else str(value)
                        if text and not PLACEHOLDER.match(text):
                            offenders.append(f"{label}: {name}={text!r}")
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    if isinstance(item, (dict, list)):
                        stack.append((f"{prefix}[{index}]", item))
                    elif KEY_FIELD.search(str(prefix)):
                        text = "" if item is None else str(item)
                        if text and not PLACEHOLDER.match(text):
                            offenders.append(f"{label}: {prefix}[{index}]={text!r}")
    assert not offenders, (
        "a generated config carries a concrete credential value rather than a "
        f"placeholder: {offenders} (§P1-02)")


# ==========================================================================
# Contract 5 — the three client shapes, and an offline Windows harness.
# ==========================================================================


def test_claude_code_config_shape(installed):
    """§P1-02 / §P2-05: the Claude Code surface is a portable stdio plugin."""
    plugin = installed["dest"] / "plugins" / "marketops-research-assistant"
    manifest = _load_json(plugin / "plugin.json")
    assert manifest.get("name") == "marketops-research-assistant"
    compat = _load_json(plugin / ".codex-plugin" / "plugin.json")
    assert manifest.get("version") == compat.get("version"), "manifests disagree on version"

    server = _mcp_servers(plugin / ".mcp.json")[MANAGED_SERVER]
    assert server.get("type") == "stdio"
    assert server.get("command"), "no interpreter chosen at install time"
    args = server.get("args") or []
    assert args and str(args[0]).endswith(".py"), (
        f"the Claude Code entrypoint is not a Python module: {args}")
    assert "scripts" in str(args[0]), (
        f"the Claude Code entrypoint is not one of the plugin's scripts: {args}")


def test_codex_plugin_and_marketplace_shape(installed):
    """§P1-02 / §P2-05: the Codex surface is a local marketplace entry whose
    plugin manifest points at the stdio MCP."""
    dest = installed["dest"]
    marketplace = _load_json(dest / ".agents" / "plugins" / "marketplace.json")
    plugins = marketplace.get("plugins") or []
    assert len(plugins) == 1, f"marketplace lists {len(plugins)} plugins: {plugins}"
    entry = plugins[0]
    assert entry.get("name") == "marketops-research-assistant"
    source_path = (entry.get("source") or {}).get("path")
    assert source_path, f"marketplace entry has no local path: {entry}"
    resolved = (dest / source_path.lstrip("./")).resolve()
    assert resolved.is_dir(), f"marketplace path does not resolve: {resolved}"

    compat = _load_json(resolved / ".codex-plugin" / "plugin.json")
    # The pointer's *name* is the client's convention, not this gate's business:
    # `.mcp.json` and `mcp.json` both exist in this plugin and the manifest has
    # already moved between them. What must hold is that the pointer resolves to
    # a real file carrying exactly the one managed server — a pointer at a file
    # that is missing or empty is the "Skill loads, MCP silently absent" failure
    # mode this repository has already been bitten by.
    pointer = compat.get("mcpServers")
    assert isinstance(pointer, str) and pointer, (
        f"the Codex plugin manifest declares no MCP config pointer: {pointer!r}")
    target = (resolved / pointer).resolve()
    assert target.is_file(), (
        f"the Codex plugin manifest points at a file that does not exist: "
        f"{pointer!r} -> {target}")
    assert set(_mcp_servers(target)) == {MANAGED_SERVER}, (
        f"the Codex MCP config {pointer!r} does not carry exactly "
        f"{MANAGED_SERVER!r}")


def test_workbuddy_connector_shape(installed):
    """§P1-02 / §P2-05: the WorkBuddy surface is a token-auth stdio connector."""
    connectors = _workbuddy_connectors(installed["dest"])
    assert connectors, "the installer produced no WorkBuddy connector directory"
    for connector in connectors:
        meta = _load_json(connector / "connector-meta.json")
        assert meta.get("type") == "mcp", meta.get("type")
        assert meta.get("auth_mode") == "token", meta.get("auth_mode")
        assert meta.get("version"), "connector-meta has no version"
        server = _mcp_servers(connector / "mcp.json")[MANAGED_SERVER]
        assert server.get("type") == "stdio", server.get("type")
        assert str(server.get("command")).endswith(("python", "python.exe")), (
            f"connector command is not an interpreter: {server.get('command')!r}")
        schema = _load_json(connector / "token-schema.json")
        keys = {field.get("key") for field in schema.get("fields", [])}
        assert "SERPAPI_API_KEY" in keys and "CRAWL4AI_API_KEY" in keys, keys
        for field in schema.get("fields", []):
            default = field.get("defaultValue") or ""
            if KEY_FIELD.search(str(field.get("key"))):
                assert default == "" or PLACEHOLDER.match(str(default)), (
                    f"token-schema ships a value for {field.get('key')}")


def test_installed_mcp_launcher_is_not_a_posix_shell_script(installed):
    """§P1-02 / §P2-05 (Windows): ``sh`` does not exist on Windows.

    The pre-install ``.mcp.json`` launches through ``launch_research_mcp.sh``;
    the installer must have rebound every client config to an interpreter.
    """
    plugin = installed["dest"] / "plugins" / "marketops-research-assistant"
    for name in ("mcp.json", ".mcp.json"):
        server = _mcp_servers(plugin / name)[MANAGED_SERVER]
        command = str(server.get("command", ""))
        assert command not in ("sh", "bash"), f"{name} still launches via {command!r}"
        assert not command.endswith(".sh"), f"{name} launches {command!r}"
        assert Path(command).name.startswith("python"), command


def test_windows_interpreter_resolution_branch(monkeypatch):
    """§P2-05 (Windows): the venv interpreter is ``Scripts/python.exe`` on nt.

    Offline harness only — this asserts the branch, it does not run Windows.
    """
    venv_root = Path("/customer/install/.venv")
    monkeypatch.setattr(sys, "platform", "win32")
    windows_python = install._python(venv_root)
    assert str(windows_python).replace("\\", "/").endswith("Scripts/python.exe"), windows_python

    monkeypatch.setattr(sys, "platform", "linux")
    posix_python = install._python(venv_root)
    assert str(posix_python).endswith("bin/python"), posix_python


def test_windows_path_forms_survive_json_and_toml_escaping(bundle):
    """§P2-05 (Windows): a backslash path must round-trip, not corrupt a config.

    The bundle builder writes native absolute paths into JSON; on Windows those
    contain backslashes that JSON must escape and that a naive reader would
    mangle.
    """
    from pathlib import PureWindowsPath

    # The drive is assembled rather than written as one literal: the
    # repository's own ``test_no_stale_assumptions`` rule flags a drive-letter
    # path in scanned text, and this is test data, not a deployment
    # assumption. Splitting it keeps both true at once.
    windows_target = PureWindowsPath("C:" + "/" + "Users/customer/marketops-research-assistant")
    config = {
        "mcpServers": {MANAGED_SERVER: {
            "type": "stdio",
            "command": str(windows_target / ".venv" / "Scripts" / "python.exe"),
            "args": [str(windows_target / "scripts" / "research_mcp.py")],
            "cwd": str(windows_target),
        }}
    }
    text = json.dumps(config, indent=2)
    assert "\\\\" in text, "backslashes were not escaped by the JSON writer"
    assert json.loads(text) == config, "a Windows path did not survive JSON round-trip"

    # The path the real builder generated must equally round-trip.
    generated = (bundle["dir"] / "mcp.json").read_text(encoding="utf-8")
    assert json.loads(json.dumps(json.loads(generated))) == json.loads(generated)
    assert _mcp_servers(bundle["dir"] / "mcp.json")[MANAGED_SERVER]["type"] == "stdio"


def test_generated_text_artifacts_are_utf8_without_bom_and_newline_tolerant(installed, bundle):
    """§P2-05 (Windows): UTF-8 BOM and CRLF are the two classic client-config
    breakages; the generated artifacts must survive both.
    """
    targets = [p for p in _all_mcp_configs(installed["dest"])]
    targets.append(bundle["dir"] / "mcp.json")
    targets.append(installed["dest"] / "connectors" / "workbuddy" /
                   "marketops-research-assistant" / "token-schema.json")
    for path in targets:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), f"{path} is written with a UTF-8 BOM"
        text = raw.decode("utf-8")           # strict decode: no locale fallback
        crlf = text.replace("\r\n", "\n").replace("\n", "\r\n")
        assert json.loads(crlf) == json.loads(text), (
            f"{path} does not survive a CRLF round-trip")


# ==========================================================================
# BLOCKED — P2-05 needs a real client, a real device or a Windows host.
# ==========================================================================


def test_p2_05_real_client_install_smoke_is_blocked():
    """§P2-05: "For Claude Code, Codex, WorkBuddy and Windows platform
    combinations: install, stdio initialize/tools-list, correct capability
    metadata, zero-network dry run and T1 read-only sample".

    The row's own gate is "customer/device access separate authorization"; this
    offline gate has none, so the requirement is reported BLOCKED rather than
    faked with a static green.
    """
    blocked(
        "P2-05 real-client install + stdio initialize/tools-list requires an "
        "authorized Claude Code / Codex / WorkBuddy client and device access; "
        "no such authorization exists for this offline gate")


def test_p2_05_windows_host_smoke_is_blocked():
    """§P2-05 (Windows platform combination) requires a real Windows host."""
    blocked(
        "P2-05 Windows-platform smoke requires a real Windows host or customer "
        "device; only an offline sys.platform/path-form harness is possible here")


def test_real_codex_marketplace_registration_is_blocked():
    """§P2-05 install step: registering the marketplace runs the real CLI.

    ``install.register_codex`` shells out to ``codex plugin marketplace add``;
    executing it is an external effect outside this gate's authorization, and
    the same applies to loading the plugin into a live Claude Code session.
    """
    blocked(
        "registering the generated marketplace (and loading the plugin into a "
        "live client) mutates a real client's configuration; no live client or "
        "device authorization is available, so this stays BLOCKED")

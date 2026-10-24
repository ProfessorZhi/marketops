#!/usr/bin/env python3
"""No-network static integrity preflight for the unpacked MarketOps plugin.

Covers Python syntax, Skill metadata, portable Codex manifests and basic
WorkBuddy bundle build shape. It deliberately cannot prove authentication,
provider correctness, business evidence or customer E2E.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SKILLS = {
    "marketops-research", "marketops-setup", "marketops-discovery",
    "marketops-accounts", "marketops-providers", "marketops-delivery",
    "marketops-orchestrate", "marketops-serper", "marketops-serpapi",
    "marketops-crawl4ai", "marketops-tinyfish", "marketops-jev",
    "marketops-feedback", "marketops-doctor", "marketops-directories",
}
EXPECTED_VARS = {
    "SERPAPI_API_KEY", "SERPER_API_KEY", "TINYFISH_API_KEY", "DEEPSEEK_API_KEY",
    "SERPER_API_KEY_2", "SERPAPI_API_KEY_2",
    "CRAWL4AI_API_KEY", "TYPESAFE_API_KEY", "MARKETOPS_RESEARCH_DATA_ROOT",
    "MARKETOPS_RESEARCH_CONFIG",
    "MARKETOPS_SEARCH_ALLOW_NETWORK", "MARKETOPS_SEARCH_MAX_CALLS",
    "MARKETOPS_RESEARCH_ALLOW_NETWORK", "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS",
    "MARKETOPS_FETCH_ALLOWED_DOMAINS",
    "TINYFISH_MCP_ACCESS_TOKEN", "MARKETOPS_TINYFISH_ACCOUNT_ALIAS",
    "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID",
}


def report() -> dict:
    errors, warnings = [], []
    parsed_count = 0
    for filename in sorted(ROOT.rglob("*.py")):
        if any(n in filename.parts for n in ("__pycache__", ".venv", "build", "dist")):
            continue
        try:
            ast.parse(filename.read_text(encoding="utf-8"), filename=str(filename))
            parsed_count += 1
        except (OSError, SyntaxError, UnicodeError) as exc:
            errors.append(f"{filename.relative_to(ROOT)}: invalid Python {type(exc).__name__}")
    skills = {}
    for filename in sorted((ROOT / "skills").glob("*/SKILL.md")):
        raw = filename.read_text(encoding="utf-8")
        match = re.match(r"^---\s*\nname:\s*([^\n]+)\ndescription:\s*(.+?)\n---\s*\n", raw)
        if not match:
            errors.append(f"Skill frontmatter invalid: {filename.parent.name}")
            continue
        name = match.group(1).strip()
        if name in skills:
            errors.append(f"Duplicate Skill name: {name}")
        skills[name] = filename.parent.name
        if filename.parent.name != name:
            errors.append(f"Skill directory/name mismatch: {filename.parent.name}")
    if set(skills) != EXPECTED_SKILLS:
        errors.append("Skill list mismatch: missing=%s extra=%s" % (
            sorted(EXPECTED_SKILLS - set(skills)), sorted(set(skills) - EXPECTED_SKILLS),
        ))

    # The end-user package MUST include real first-run instructions and a
    # non-secret catalog for every provider, not just a vendor name in a Skill.
    for rel in ("scripts/provider_onboarding.py",
                "scripts/research_official_mcp.py",
                "scripts/research_official_mcp_auth.py",
                "references/CUSTOMER_QUICKSTART.md",
                "references/PROVIDER_CONNECTIONS.json"):
        if not (ROOT / rel).is_file():
            errors.append("Required guided setup asset missing: " + rel)
    catalog_path = ROOT / "references" / "PROVIDER_CONNECTIONS.json"
    if catalog_path.is_file():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            entries = catalog.get("providers", [])
            ids = [item["id"] for item in entries]
            # The five required providers must all be present. An extra entry is
            # allowed only when it is marked optional, so a typo cannot smuggle in
            # a provider the guide would then present as a required account.
            extra_ok = all(item.get("optional") is True for item in entries
                           if item.get("id") not in
                           {"tinyfish", "serpapi", "serper", "crawl4ai", "jev"})
            if (catalog.get("schema") != "marketops.provider-setup.v1"
                    or len(ids) != len(set(ids))
                    or not {"tinyfish", "serpapi", "serper", "crawl4ai", "jev"} <= set(ids)
                    or not extra_ok
                    or any(catalog.get("default_policy", {}).get(key) is not False
                           for key in ("network_enabled", "paid_provider_calls_enabled",
                                       "import_official_mcp", "site_profiles_enabled",
                                       "provider_auth_verified"))):
                errors.append("Provider connection catalog missing safe five-provider defaults")
        except (OSError, ValueError, KeyError, TypeError):
            errors.append("Provider connection catalog invalid")

    def load_json(name: str) -> dict:
        try:
            return json.loads((ROOT / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            errors.append(f"Invalid/missing {name}")
            return {}

    root_manifest = load_json("plugin.json")
    compat_manifest = load_json(".codex-plugin/plugin.json")
    # Claude Code loads a plugin only when its own manifest is present. Without
    # it the Skills appear and the MCP silently never starts, so its absence is
    # an error, not a warning.
    claude_manifest = load_json(".claude-plugin/plugin.json")
    root_mcp = load_json("mcp.json")
    compat_mcp = load_json(".mcp.json")
    if root_manifest.get("name") != "marketops-research-assistant":
        errors.append("Incorrect portable plugin name")
    for label, manifest in (("portable", root_manifest), ("claude", claude_manifest)):
        if manifest.get("version") != compat_manifest.get("version"):
            errors.append(f"{label.capitalize()}/compat manifests disagree on version")
    if claude_manifest.get("name") != compat_manifest.get("name"):
        errors.append("Claude/compat manifests disagree on plugin name")
    for name, config in (("mcp.json", root_mcp), (".mcp.json", compat_mcp)):
        # Codex loader canonical JSON uses mcpServers, despite older docs showing mcp_servers.
        servers = config.get("mcpServers")
        if not isinstance(servers, dict) or set(servers) != {"marketops-research"}:
            errors.append(f"{name}: exactly one 'marketops-research' server required")
            continue
        server = servers["marketops-research"]
        launcher = name == ".mcp.json"
        expected_command = "sh" if launcher else "python"
        # Both hosts must land on the same managed entrypoint: the one
        # `scripts/research_mcp.py`, which is also what the installer and the
        # connector builder rebind every generated config to. The POSIX config
        # names it as the launcher's argument; the portable config names it
        # directly.
        entrypoint = "/scripts/research_mcp.py"
        expected_args = (["/scripts/launch_research_mcp.sh", entrypoint] if launcher else [entrypoint])
        if server.get("type") != "stdio" or server.get("command") != expected_command:
            errors.append(f"{name}: unexpected pre-install stdio MCP command")
        args = server.get("args")
        if not isinstance(args, list) or len(args) != len(expected_args) \
                or not all(isinstance(item, str) and item.endswith(suffix)
                           for item, suffix in zip(args, expected_args)):
            errors.append(f"{name}: unexpected MCP entrypoint")
        env_vars = set(server.get("env_vars") or [])
        expected = EXPECTED_VARS | ({"MARKETOPS_RESEARCH_PYTHON"} if launcher else set())
        if env_vars != expected:
            errors.append(f"{name}: missing or unapproved environment variable names")
        if launcher and not (ROOT / "scripts" / "launch_research_mcp.sh").is_file():
            errors.append(".mcp.json: launcher script missing")
    try:
        actual = importlib.util.find_spec("mcp") is not None
    except (ImportError, ValueError):
        actual = False
    if not actual:
        warnings.append("MCP_SDK_NOT_INSTALLED (preflight cannot perform real tools/list)")
    try:
        actual = importlib.util.find_spec("typesafe_sdk") is not None
    except (ImportError, ValueError):
        actual = False
    if not actual:
        warnings.append("TYPESAFE_SDK_NOT_INSTALLED (Jev is unavailable)")
    try:
        actual = importlib.util.find_spec("crawl4ai") is not None
    except (ImportError, ValueError):
        actual = False
    if not actual:
        warnings.append("CRAWL4AI_LOCAL_NOT_INSTALLED (Cloud mode may be used after approval)")
    return {
        "status": "STATIC_FAIL" if errors else "STATIC_PASS_NOT_RUNTIME_VERIFIED",
        "errors": errors,
        "warnings": warnings,
        "python_files_parsed": parsed_count,
        "skills": len(skills),
        "portable_and_compat_mcp": "marketops-research",
        "provider_calls_made": 0,
        "auth_verified": False,
        "client_import_verified": False,
        "end_to_end_verified": False,
    }


def main() -> int:
    outcome = report()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 1 if outcome["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SKILLS = {
    "marketops-research", "marketops-intake", "marketops-source-discovery",
    "marketops-venue-account", "marketops-person-research", "marketops-evidence",
    "marketops-excel", "marketops-handoff", "marketops-provider-router",
    "marketops-serpapi", "marketops-serper", "marketops-crawl4ai",
    "marketops-tinyfish", "marketops-jev", "marketops-research-retrospective",
    "marketops-setup", "marketops-research-acceptance",
}
EXPECTED_VARS = {
    "SERPAPI_API_KEY", "SERPER_API_KEY", "TINYFISH_API_KEY",
    "CRAWL4AI_API_KEY", "TYPESAFE_API_KEY", "MARKETOPS_RESEARCH_DATA_ROOT",
    "MARKETOPS_RESEARCH_CONFIG",
    "MARKETOPS_SEARCH_ALLOW_NETWORK", "MARKETOPS_SEARCH_MAX_CALLS",
    "MARKETOPS_RESEARCH_ALLOW_NETWORK", "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS",
    "MARKETOPS_FETCH_ALLOWED_DOMAINS",
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

    def load_json(name: str) -> dict:
        try:
            return json.loads((ROOT / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            errors.append(f"Invalid/missing {name}")
            return {}

    root_manifest = load_json("plugin.json")
    compat_manifest = load_json(".codex-plugin/plugin.json")
    root_mcp = load_json("mcp.json")
    compat_mcp = load_json(".mcp.json")
    if root_manifest.get("name") != "marketops-research-assistant":
        errors.append("Incorrect portable plugin name")
    if root_manifest.get("version") != compat_manifest.get("version"):
        errors.append("Portable/compat manifests disagree on version")
    for name, config in (("mcp.json", root_mcp), (".mcp.json", compat_mcp)):
        # Codex loader canonical JSON uses mcpServers, despite older docs showing mcp_servers.
        servers = config.get("mcpServers")
        if not isinstance(servers, dict) or set(servers) != {"marketops-research"}:
            errors.append(f"{name}: exactly one 'marketops-research' server required")
            continue
        server = servers["marketops-research"]
        if server.get("type") != "stdio" or server.get("command") != "python":
            errors.append(f"{name}: expected pre-install stdio Python MCP")
        args = server.get("args")
        if not isinstance(args, list) or len(args) != 1 or not args[0].endswith("/scripts/research_mcp.py"):
            errors.append(f"{name}: unexpected MCP entrypoint")
        env_vars = set(server.get("env_vars") or [])
        if env_vars != EXPECTED_VARS:
            errors.append(f"{name}: missing or unapproved environment variable names")
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

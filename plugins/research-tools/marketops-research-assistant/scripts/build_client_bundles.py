#!/usr/bin/env python3
"""Generate a single WorkBuddy skill-only trial connector from canonical MarketOps Skills."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#193552"/><path d="M12 18h40v8H12zm0 12h32v8H12zm0 12h24v8H12z" fill="#75D3BA"/><circle cx="49" cy="46" r="7" fill="#F6BE65"/></svg>\n"""
META = {
    "name": "MarketOps Research Assistant",
    "name_zh": "MarketOps 半自动市场调研",
    "name_en": "MarketOps Research Assistant",
    "description": "Member-A-grounded business Skills, provider routing and candidate research experience packs.",
    "description_zh": "研究业务、四工具路由和有出处的卡点经验；统一MCP运行时仍待接线。",
    "description_en": "Research methods, four-provider routing and scoped lessons; unified MCP runtime not bundled.",
    "source": "marketops-research-assistant-lite",
    "type": "skill-only",
    "version": "0.4.0",
    "minWorkbuddyVersion": "4.24.0",
    "examples_zh": [
        "研究 Devon 的体育场地，先列来源和候选场地，所有不确定项进人工复核。",
        "根据已有公司名单，寻找相关岗位和有原始出处的公开工作邮箱。",
        "检查调研交付，并把卡点与成功方法归纳成待验证的经验包。"
    ],
    "examples_en": [
        "Find public sports venues in Devon and leave ambiguous claims for manual review.",
        "Research decision-maker roles from official public pages without guessing emails.",
        "Audit the research handoff and produce an evidence-linked candidate retrospective."
    ],
}


def make_zip(folder: Path, output_zip: Path) -> None:
    """Fixed timestamps/order: same source bytes produce identical ZIP."""
    with zipfile.ZipFile(output_zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in sorted(p for p in folder.rglob("*") if p.is_file()):
            arcname = f"{folder.name}/{filename.relative_to(folder).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, filename.read_bytes())


def build(output: Path, *, with_search_mcp: bool = False,
          with_research_mcp: bool = False) -> dict:
    if with_search_mcp and with_research_mcp:
        raise ValueError("Select exactly one MCP mode")
    output = output.expanduser().resolve()
    if output.is_relative_to(HERE):
        raise ValueError("Build output must be outside this plugin source directory")
    workbuddy = output / "workbuddy"
    target = workbuddy / "marketops-research-assistant"
    package = workbuddy / f"marketops-research-assistant-v{META['version']}.zip"
    if target.exists() or package.exists():
        raise FileExistsError(f"Will not overwrite an existing connector/package: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    metadata = dict(META)
    if with_search_mcp or with_research_mcp:
        metadata.update({
            "name": "MarketOps Research Assistant + Research MCP" if with_research_mcp else "MarketOps Research Assistant + Search MCP",
            "name_zh": "MarketOps 半自动调研 MCP" if with_research_mcp else "MarketOps 调研与双搜索 MCP",
            "source": "marketops-research-assistant" if with_research_mcp else "marketops-research-assistant-dual-search",
            "type": "mcp",
            "auth_mode": "token",
            "description": "One local Research MCP with SerpApi, Serper, Crawl4AI, TinyFish Search/Fetch and Jev Choice; all network calls default off." if with_research_mcp else "One local Search MCP for SerpApi or Serper; paid network calls default off.",
            "description_zh": "统一调研 MCP + 17 项业务和工具 Skills；五家 Provider 任选，联网默认关闭。" if with_research_mcp else "本地双搜索 MCP + 17 项 Skills，联网默认关闭。",
        })
    (target / "connector-meta.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "icon.svg").write_text(ICON, encoding="utf-8")
    for part in ("skills", "references"):
        shutil.copytree(HERE / part, target / part)
    (target / "scripts").mkdir()
    for name in ("research_pack.py", "export_excel_safe_csv.py", "experience_pack.py",
                 "research_event_journal.py", "research_config.py",
                 "research_experience_summary.py", "research_workflow.py", "research_evidence.py",
                 "dual_search_mcp.py", "compare_search_receipts.py", "research_lite_store.py",
                 "research_provider_extensions.py", "research_mcp.py",
                 "research_delivery.py", "_xlsx.py", "preflight.py", "setup_clients.py"):
        shutil.copy2(HERE / "scripts" / name, target / "scripts" / name)
    shutil.copy2(HERE / "README.md", target / "README.md")
    for requirements_file in ("requirements-lite.txt", "requirements-crawl-local.txt"):
        shutil.copy2(HERE / requirements_file, target / requirements_file)
    if with_search_mcp or with_research_mcp:
        # This is a *machine-bound* preview: the checked-out source and Python
        # interpreter must remain on the importing client's machine. Never copy
        # the generated ZIP to a different machine without rebuilding locally.
        variables = [
            "SERPAPI_API_KEY", "SERPER_API_KEY", "MARKETOPS_RESEARCH_DATA_ROOT",
            "MARKETOPS_RESEARCH_CONFIG",
            "MARKETOPS_SEARCH_ALLOW_NETWORK", "MARKETOPS_SEARCH_MAX_CALLS",
        ]
        env = {name: "${" + name + "}" for name in variables}
        server_name = "marketops-research" if with_research_mcp else "marketops-dual-search"
        program = "research_mcp.py" if with_research_mcp else "dual_search_mcp.py"
        if with_research_mcp:
            variables.extend(["CRAWL4AI_API_KEY", "TINYFISH_API_KEY", "TYPESAFE_API_KEY",
                              "MARKETOPS_RESEARCH_ALLOW_NETWORK",
                              "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS",
                              "MARKETOPS_FETCH_ALLOWED_DOMAINS"])
            env.update({name: "$" + "{" + name + "}" for name in variables})
        search_config = {"mcpServers": {server_name: {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(target / "scripts" / program)],
            "cwd": str(target),
            "env": env,
            "timeout": 30000,
        }}}
        (target / "mcp.json").write_text(
            json.dumps(search_config, indent=2) + "\n", encoding="utf-8")
        # The unified Research MCP is configured in-product at first use, so the data
        # root is an advanced override here, not a required onboarding field. Never
        # inject a default directory: a blank value must stay unconfigured.
        data_root_field = (
            ("MARKETOPS_RESEARCH_DATA_ROOT",
             "Optional advanced override: absolute research data directory "
             "(leave empty to use the first-use configuration)",
             "text", False, "") if with_research_mcp else
            ("MARKETOPS_RESEARCH_DATA_ROOT", "Private absolute data directory", "text", True, ""))
        field_data = [
            ("SERPAPI_API_KEY", "SerpApi API Key (optional)", "password", False, ""),
            ("SERPER_API_KEY", "Serper API Key (optional)", "password", False, ""),
            data_root_field,
            ("MARKETOPS_RESEARCH_CONFIG", "Optional: path to a different first-use config JSON", "text", False, ""),
            ("MARKETOPS_SEARCH_ALLOW_NETWORK", "Enable billable search: enter 1, otherwise 0", "text", True, "0"),
            ("MARKETOPS_SEARCH_MAX_CALLS", "Cumulative reserved call limit", "text", True, "0"),
        ]
        if with_research_mcp:
            field_data.extend([
                ("CRAWL4AI_API_KEY", "Crawl4AI Cloud API Key (optional; local mode uses no key)", "password", False, ""),
                ("TINYFISH_API_KEY", "TinyFish API Key (optional)", "password", False, ""),
                ("TYPESAFE_API_KEY", "TypeSafe Jev API Key (optional)", "password", False, ""),
                ("MARKETOPS_RESEARCH_ALLOW_NETWORK", "Enable non-search provider calls: 1/0", "text", True, "0"),
                ("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", "Cumulative other provider attempts cap", "text", True, "0"),
                ("MARKETOPS_FETCH_ALLOWED_DOMAINS", "Comma separated approved public website domains", "text", False, ""),
            ])
        schema = {
            "title": "MarketOps Research MCP" if with_research_mcp else "MarketOps Dual Search (local stdio)",
            "description": "One optional account or both; payment authorization and quotas remain your responsibility.",
            "docUrl": "https://github.com/ProfessorZhi/marketops",
            "fields": [
                {"key": key, "label": label, "type": datatype, "required": required, "defaultValue": default}
                for key, label, datatype, required, default in field_data
            ],
        }
        (target / "token-schema.json").write_text(
            json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    # The default skill-only bundle has no MCP; explicitly request search for tool mode.
    make_zip(target, package)
    return {
        "status": "BUILT_STATIC_NOT_CLIENT_TESTED",
        "directory": str(target), "zip": str(package),
        "zip_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "skills": len(list((target / "skills").glob("*/SKILL.md"))),
        "mcp_servers_bundled": 1 if (with_search_mcp or with_research_mcp) else 0,
        "machine_bound_local_mcp": with_search_mcp or with_research_mcp,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="explicit user-owned destination outside source")
    parser.add_argument("--with-search-mcp", action="store_true", help="legacy dual-search local MCP")
    parser.add_argument("--with-research-mcp", action="store_true", help="one local MCP for all 5 provider integrations (beta)")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output, with_search_mcp=args.with_search_mcp,
                               with_research_mcp=args.with_research_mcp), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(status=2, message=f"ERROR: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

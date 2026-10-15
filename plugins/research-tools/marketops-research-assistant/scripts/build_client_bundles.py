#!/usr/bin/env python3
"""Generate a single WorkBuddy skill-only trial connector from canonical MarketOps Skills."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#193552"/><path d="M12 18h40v8H12zm0 12h32v8H12zm0 12h24v8H12z" fill="#75D3BA"/><circle cx="49" cy="46" r="7" fill="#F6BE65"/></svg>\n"""
META = {
    "name": "MarketOps Research Assistant",
    "name_zh": "MarketOps 半自动市场调研",
    "name_en": "MarketOps Research Assistant",
    "description": "Evidence-based research Skills for market discovery, venues, organizations, people, contact routes and Excel review.",
    "description_zh": "从调研需求到来源、场地与组织、候选人、证据和正式联系路线，辅助研究并生成可审核 Excel/CSV。",
    "description_en": "Guide human-reviewed market research with evidence-linked candidate pools and Excel handoffs.",
    "source": "marketops-research-assistant-lite",
    "type": "skill-only",
    "version": "0.2.0",
    "minWorkbuddyVersion": "4.24.0",
    "examples_zh": [
        "研究 Devon 的体育场地，先列来源和候选场地，所有不确定项进人工复核。",
        "根据已有公司名单，寻找相关岗位和有原始出处的公开工作邮箱。",
        "检查这批调研 CSV 的证据、关联和重复项，做成供人工审核的 Excel。"
    ],
    "examples_en": [
        "Find public sports venues in Devon and leave ambiguous claims for manual review.",
        "Research decision-maker roles from official public pages without guessing emails.",
        "Audit the evidence links in this CSV research pack and prepare an Excel review workbook."
    ],
}

def make_zip(folder: Path, output_zip: Path) -> None:
    """Fixed timestamps/order: same source bytes produce identical ZIP."""
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in sorted(p for p in folder.rglob("*") if p.is_file()):
            arcname = f"{folder.name}/{filename.relative_to(folder).as_posix()}"
            info = zipfile.ZipInfo(arcname, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, filename.read_bytes())

def build(output: Path) -> dict:
    workbuddy = output / "workbuddy"
    target = workbuddy / "marketops-research-assistant"
    if target.exists():
        raise FileExistsError(f"Will not overwrite an existing connector: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    (target / "connector-meta.json").write_text(json.dumps(META, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target / "icon.svg").write_text(ICON, encoding="utf-8")
    for part in ("skills", "references"):
        shutil.copytree(HERE / part, target / part)
    (target / "scripts").mkdir()
    shutil.copy2(HERE / "scripts" / "research_pack.py", target / "scripts" / "research_pack.py")
    shutil.copy2(HERE / "README.md", target / "README.md")
    # By design no mcp.json: this connector teaches workflow and never auto-connects providers.
    package = workbuddy / "marketops-research-assistant-v0.2.0.zip"
    make_zip(target, package)
    return {
        "status": "BUILT_STATIC_NOT_CLIENT_TESTED",
        "directory": str(target), "zip": str(package),
        "zip_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "skills": len(list((target / "skills").glob("*/SKILL.md"))),
        "mcp_servers_bundled": 0,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="explicit user-owned destination outside source")
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.output), ensure_ascii=False, indent=2))
    except OSError as exc:
        parser.exit(status=2, message=f"ERROR: {exc}\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

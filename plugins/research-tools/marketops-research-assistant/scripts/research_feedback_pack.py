#!/usr/bin/env python3
"""One ZIP a researcher sends back to whoever maintains the plugin.

Rules that came from Member A's runs:

* The same complaint ("don't make me click continue", "which number is real")
  was repeated in five threads over two months, and each time it was lost when
  the thread ended. A note recorded during the run is the only thing that
  survives, so the pack is built from the notes and wall events in the shared
  data root, across every conversation, never from chat history.
* A person correcting the agent is the most valuable signal (human_correction):
  it is listed first and in full.
* The pack carries no emails, URLs or secrets. Notes are sanitised when they are
  written; host names of sites that blocked are public and kept, because they are
  what the maintainer acts on.
"""
from __future__ import annotations

import csv
import io
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import research_usage_ledger as usage_ledger
import research_walls
from research_event_journal import NOTE_CATEGORIES, _root, read_notes

__all__ = ["build"]

CATEGORY_ZH = {
    "contact_found_not_stored": "发现联系人但未入库",
    "field_template_pollution": "字段模板污染",
    "artifact_without_delivery": "生成了文件但没交付",
    "source_blocked": "来源网站拦截/改版",
    "provider_blocked": "供应商不可用（额度/凭据/授权）",
    "evidence_missing": "拿不到可回读证据",
    "human_correction": "人工纠正了 agent",
    "other": "其他",
}
PROVIDERS = ("serper", "serpapi", "tinyfish", "crawl4ai", "typesafe_jev")


def _provider_usage(root: Path) -> dict[str, int]:
    try:
        ledger = usage_ledger.for_root(root)
        rows = ledger.operations(project=usage_ledger.project_for_root(root))
    except Exception:  # noqa: BLE001 - a missing ledger is reported, not raised
        return {}
    counts = Counter(row.provider for row in rows)
    return {p: counts.get(p, 0) for p in sorted(set(PROVIDERS) | set(counts))}


def build(data_root=None, *, reporter: str = "", out_dir: Path | None = None) -> dict:
    root = _root(data_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = Path(out_dir) if out_dir else root / "feedback"
    target.mkdir(parents=True, exist_ok=True)
    notes = read_notes(root)
    by_category: dict[str, list[dict]] = defaultdict(list)
    for note in notes:
        by_category[note["category"]].append(note)
    runs = sorted({n["run_id"] for n in notes})
    usage = _provider_usage(root)
    walls = research_walls.report(root, out_dir=target / f".lessons-{stamp}")
    unused = [p for p in PROVIDERS if usage and not usage.get(p)]

    lines = [f"# MarketOps 使用反馈包 {stamp}", ""]
    if reporter.strip():
        lines += [f"反馈人：{reporter.strip()[:80]}", ""]
    lines += [f"覆盖任务（Run）{len(runs)} 个，卡点记录 {len(notes)} 条，"
              f"抓取/调用失败 {walls['failed_attempts']} 次。", ""]
    lines += ["## 1. 人工纠正（最重要）", ""]
    corrections = by_category.get("human_correction", [])
    lines += [f"- [{n['run_id']} / {n['stage']}] 问题：{n['problem']}；处理：{n['action_taken']}；"
              f"结果：{n['observed_result']}" for n in corrections] or ["- 无"]
    lines += ["", "## 2. 卡点按类型", ""]
    for category in NOTE_CATEGORIES:
        items = by_category.get(category, [])
        if category == "human_correction" or not items:
            continue
        lines.append(f"### {CATEGORY_ZH.get(category, category)}（{len(items)}）")
        lines += [f"- [{n['run_id']} / {n['stage']}] {n['problem']} → {n['observed_result']}"
                  + (f"（猜测原因：{n['hypothesis']}）" if n["hypothesis"] else "")
                  for n in items[-15:]]
        lines.append("")
    lines += ["## 3. 五家供应商调用次数", ""]
    lines += [f"- {p}: {n}" for p, n in usage.items()] or ["- 读不到调用台账"]
    if unused:
        lines += ["", "未被调用：" + ", ".join(unused) + "（请检查 key、额度或流程）"]
    lines += ["", "## 4. 被拦截最多的网站与类型", ""]
    lines += [f"- {code}: {n}" for code, n in walls["walls"].items()] or ["- 无"]
    lines += [f"- {host}: {n}" for host, n in walls["top_blocked_hosts"].items()]
    lines += ["", "## 5. 自动归纳的改进建议（待维护者确认）", ""]
    lines += [f"- {s}" for s in walls["suggestions"]] or ["- 暂无"]
    report = "\n".join(lines) + "\n"

    notes_csv = io.StringIO()
    writer = csv.writer(notes_csv)
    writer.writerow(["run_id", "stage", "category", "problem", "action_taken",
                     "observed_result", "hypothesis"])
    for n in notes:
        writer.writerow([n["run_id"], n["stage"], n["category"], n["problem"],
                         n["action_taken"], n["observed_result"], n["hypothesis"]])

    archive = target / f"MarketOps_Feedback_{stamp}.zip"
    lessons_dir = Path(walls["directory"])
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("FEEDBACK.md", report)
        bundle.writestr("notes.csv", "﻿" + notes_csv.getvalue())
        for name in ("LESSONS.md", "wall_events_summary.csv"):
            if (lessons_dir / name).is_file():
                bundle.write(lessons_dir / name, name)
    return {"zip": str(archive), "runs": len(runs), "notes": len(notes),
            "human_corrections": len(corrections), "provider_usage": usage,
            "unused_providers": unused,
            "next": "把这个 zip 文件发给插件维护者；里面不含邮箱、网址或密钥。"}

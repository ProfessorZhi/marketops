"""Read-only extraction of anonymous pilot metrics. Never exports raw contacts.

Thread JSON inputs use the Codex read_thread response shape. Raw transcripts,
workbooks, feedback archives and SQLite databases remain outside this repository.
Only aggregate metrics, file hashes and a trace without arguments are written.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import zipfile
from collections import Counter, defaultdict, deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

RUN = "paris-indoor-26oct"
POOL = "idf-indoor-sports-2026"
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def connect(path):
    return closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True))


def file_fact(path, label):
    path = Path(path)
    data = path.read_bytes()
    return {"source": label, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def sheet_counts(path):
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.attrib["Id"]: r.attrib["Target"] for r in relationships}
        result = {}
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            key = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
            target = targets[key].lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            xml = ET.fromstring(archive.read(target))
            count = sum(bool(row.findall("m:c", NS)) for row in xml.findall("m:sheetData/m:row", NS))
            result[sheet.attrib["name"]] = max(0, count - 1)
        return result


def parse_calls(path):
    lines = [re.sub(r"^> ?", "", line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    starts = [i for i, line in enumerate(lines) if line == "MCP tool call"]
    result = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end])
        name = next(line.split(".", 1)[1] for line in lines[start:end]
                    if line.startswith("marketops-research."))
        fences = re.findall(r"```(?:json|text)\n(.*?)\n```", block, flags=re.S)
        arguments = json.loads(fences[0])
        response = None
        if len(fences) > 1:
            try:
                response = json.loads(fences[1])
            except ValueError:
                pass
        result.append({"line": start + 1, "tool": name, "arguments": arguments, "result": response})
    return result


def turn_fact(turn):
    def local_time(value):
        return datetime.fromtimestamp(value, timezone(timedelta(hours=8))).isoformat()
    return {"started_at": local_time(turn["startedAt"]), "completed_at": local_time(turn["completedAt"]),
            "duration_ms": turn["durationMs"], "duration_minutes": round(turn["durationMs"] / 60000, 6)}


def table_row_count(text, header):
    found = False
    count = 0
    for line in text.splitlines():
        if not line.startswith("|"):
            if found:
                break
            continue
        if not found:
            found = header in line
            continue
        if re.fullmatch(r"[| :\-]+", line):
            continue
        count += 1
    return count


def collect(args):
    a, b = read_json(args.case_a), read_json(args.case_b)
    a_turn = a["turns"][0]
    b_turn = next(t for t in b["turns"] if any(i.get("tool") == "marketops_start" for i in t["items"]))
    a_final = [i["text"] for i in a_turn["items"]
               if i["type"] == "agentMessage" and i.get("phase") == "final_answer"][-1]
    searches = [i for i in a_turn["items"] if i["type"] == "webSearch"]
    actions = Counter(i.get("action", {}).get("type") for i in searches)
    query_count = sum(len(i.get("action", {}).get("queries") or [])
                      or int(bool(i.get("action", {}).get("query"))) for i in searches)
    calls = parse_calls(args.transcript_b)
    by_tool = defaultdict(list)
    for call in calls:
        by_tool[call["tool"]].append(call)
    exported = Path(args.export_dir)
    manifest = read_json(exported / "MANIFEST.json")
    csv_counts = {p.stem: len(rows(p)) for p in exported.glob("*.csv")}
    xlsx_counts = sheet_counts(exported / "Research_Result.xlsx")
    assert csv_counts == xlsx_counts == manifest["counts"], "Workbook, CSV and manifest counts disagree"
    verified_files = []
    for name, expected in manifest["files"].items():
        actual = file_fact(exported / name, "B export/" + name)
        assert actual["bytes"] == expected["bytes"] and actual["sha256"] == expected["sha256"], name
        verified_files.append(actual)
    audit_rows = rows(exported / "候选数据审计.csv")
    audit_types = dict(Counter(r["对象类型"] for r in audit_rows))
    goal = manifest["goal_assessment"]
    admission = manifest["admission"]
    routes = rows(exported / "联系人路线.csv")
    state = Path(args.state_dir)
    with connect(state / "usage-ledger.sqlite3") as db:
        db.row_factory = sqlite3.Row
        ledger = [dict(row) for row in db.execute(
            "SELECT provider,capability,charge_state,execution_outcome,COUNT(*) AS operations,"
            "SUM(actual_micros) AS actual_micros FROM operations WHERE run_id=? "
            "GROUP BY provider,capability,charge_state,execution_outcome ORDER BY provider,capability", (RUN,))]
    with connect(state / "research-pool.sqlite3") as db:
        pool = dict(db.execute("SELECT status,COUNT(*) FROM pool_targets WHERE pool_id=? GROUP BY status", (POOL,)))
        sources = db.execute("SELECT SUM(listed_count),SUM(imported),SUM(duplicates) "
                             "FROM pool_sources WHERE pool_id=?", (POOL,)).fetchone()
        pool_orgs = {r[0] for r in db.execute("SELECT organization_key FROM pool_targets WHERE pool_id=?", (POOL,))}
        pool_updated = db.execute("SELECT MAX(updated_at) FROM pool_targets WHERE pool_id=?", (POOL,)).fetchone()[0]
    with connect(state / "research-lite.sqlite3") as db:
        organizations = {r[0] for r in db.execute(
            "SELECT DISTINCT entity_key FROM records WHERE run_id=? AND kind='organization'", (RUN,))}
    handoff = by_tool["marketops_handoff"][0]["result"]
    last_audit = by_tool["marketops_evidence_audit"][-1]["result"]
    next_snapshots = [c["result"] for c in by_tool["marketops_next"] if isinstance(c["result"], dict)]
    last_budget = next_snapshots[-1].get("source_selection", {}).get("budget", {})
    metrics = {
        "schema": "marketops.pilot-comparison.v1", "experiment": "2026-10-21-paris-indoor",
        "design": "retrospective_observational_one_session_per_condition",
        "timezone": "Asia/Shanghai", "plugin_version": "0.5.9-rc3", "source_revision": args.source_revision,
        "source_sessions": {"A": {"id": a["thread"]["id"], "title": a["thread"]["title"]},
                            "B": {"id": b["thread"]["id"], "title": b["thread"]["title"]}},
        "A": {"condition": "MarketOps prohibited", **turn_fact(a_turn),
              "shortlisted_large_venues": table_row_count(a_final, "场馆"),
              "reported_individual_email_candidates": len(set(EMAIL.findall(a_final))),
              "workbook_delivered": False, "complete_inventory_delivered": False,
              "web_action_count": len(searches), "web_action_types": dict(actions), "search_queries": query_count,
              "self_reported_search_count": 9,
              "command_count": sum(i["type"] == "commandExecution" for i in a_turn["items"]),
              "command_failures": sum(i["type"] == "commandExecution" and i["exitCode"] != 0 for i in a_turn["items"]),
              "marketops_mcp_calls": 0, "actual_usd": None, "model_tokens": None,
              "buyer_roi_verified": False, "email_deliverability_verified": False},
        "B": {"condition": "MarketOps required", **turn_fact(b_turn),
              "prior_feasibility_duration_ms": sum(t["durationMs"] for t in b["turns"] if t is not b_turn),
              "research_mcp_calls": sum(i["type"] == "mcpToolCall" for i in b_turn["items"]),
              "all_session_mcp_calls": len(calls),
              "research_mcp_calls_by_tool": dict(Counter(i["tool"] for i in b_turn["items"] if i["type"] == "mcpToolCall")),
              "opaque_tool_errors_in_transcript": sum(c["result"] is None for c in calls),
              "map_results_listed": sources[0], "pool_targets_imported": sources[1], "map_duplicates": sources[2],
              "pool_state_after_run": pool, "pool_last_updated": pool_updated,
              "pool_to_organization_intersection": len(pool_orgs & organizations),
              "pool_keys_without_organization_record": len(pool_orgs - organizations),
              "organization_keys_not_in_pool": len(organizations - pool_orgs),
              "audit_records_by_type": audit_types, "audit_record_count": len(audit_rows),
              "active_record_count": manifest["active_record_count"], "saved_artifacts": manifest["saved_artifact_count"],
              "csv_data_rows": csv_counts, "xlsx_data_rows": xlsx_counts,
              "export_hashes_verified": len(verified_files), "goal_status": goal["status"],
              "goal_satisfied": goal["goal_satisfied"], "coverage_complete": goal["coverage_complete"],
              "organization_targets": goal["target_count"], "dispositions": goal["disposition_counts"],
              "admitted_records": admission["admitted_record_count"], "rejected_records": admission["rejected_record_count"],
              "admitted_by_kind": admission["admitted_by_kind"], "rejected_by_kind": admission["rejected_by_kind"],
              "admission_reason_counts": admission["reason_counts"],
              "evidence_audit_status": last_audit["status"], "evidence_audit_gap_counts": last_audit["gap_counts"],
              "export_status": manifest["status"], "delivery_completeness": handoff["delivery_completeness"],
              "evidence_envelope_export_gate": manifest["evidence_envelope_export_gate"],
              "business_verification": manifest["business_verification"],
              "evidence_ready_for_business_use": manifest["evidence_ready_for_business_use"],
              "exported_contact_functions": dict(Counter(r["职能"] for r in routes)),
              "exported_contact_grades": dict(Counter(r["研究评级"] for r in routes)),
              "send_authorized": manifest["send_authorized"],
              "serper_identity_failures": sum(isinstance(c["result"], dict) and
                  c["result"].get("by_provider", {}).get("serper") == "IDENTITY_VALUE_INVALID" for c in calls),
              "run_ledger_groups": ledger, "run_ledger_operations": sum(g["operations"] for g in ledger),
              "run_charge_unknown_operations": sum(g["operations"] for g in ledger if g["charge_state"] == "UNKNOWN"),
              "run_rate_card_estimated_usd": handoff["run_cost"]["total_estimated_usd"],
              "actual_usd": None, "model_tokens": None,
              "last_recorded_budget": {k: v for k, v in last_budget.items() if isinstance(v, (int, str))},
              "buyer_roi_verified": False, "email_deliverability_verified": False},
        "limitations": ["one session per condition", "different tool access and stopping behavior",
                        "different contact evidence standards", "unknown model/token bill",
                        "no known complete indoor-venue denominator", "product and target buyer unspecified",
                        "no new venue research or provider calls made during this audit"],
    }
    facts = [file_fact(args.transcript_a, "A pasted transcript"), file_fact(args.transcript_b, "B pasted transcript"),
             file_fact(exported / "MANIFEST.json", "B export manifest"), *verified_files,
             file_fact(args.feedback_zip, "B feedback archive; retained locally")]
    signatures = defaultdict(deque)
    for t in b["turns"]:
        for item in t["items"]:
            if item["type"] == "mcpToolCall":
                signatures[(item["tool"], json.dumps(item["arguments"], sort_keys=True))].append(item)
    trace = []
    for index, call in enumerate(calls, 1):
        queue = signatures[(call["tool"], json.dumps(call["arguments"], sort_keys=True))]
        assert queue, f"No matching read_thread call for transcript line {call['line']}"
        item = queue.popleft()
        response = call["result"] or {}
        entry = {"sequence": index, "transcript_line": call["line"], "tool": call["tool"],
                 "transport_status": item["status"], "duration_ms": item["durationMs"],
                 "response_status": response.get("status", "NO_TOP_LEVEL_STATUS" if call["result"] is not None
                                                 else "ERROR_NO_STRUCTURED_RESPONSE")}
        if response.get("by_provider", {}).get("serper") == "IDENTITY_VALUE_INVALID":
            entry["serper_reason"] = "IDENTITY_VALUE_INVALID"
        trace.append(entry)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for name, data in (("metrics.json", metrics), ("source_hashes.json", {"sources": facts}), ("tool_trace.json", trace)):
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        assert not EMAIL.search(text), "Personal email leaked into public aggregate output"
        (output / name).write_text(text, encoding="utf-8")
    print(json.dumps({"A_minutes": metrics["A"]["duration_minutes"], "B_minutes": metrics["B"]["duration_minutes"],
                      "B_csv_rows": csv_counts, "verified_hashes": len(verified_files), "public_trace_rows": len(trace)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("case-a", "case-b", "transcript-a", "transcript-b", "export-dir", "state-dir", "feedback-zip", "output-dir"):
        parser.add_argument("--" + argument, required=True, type=Path)
    parser.add_argument("--source-revision", required=True)
    collect(parser.parse_args())

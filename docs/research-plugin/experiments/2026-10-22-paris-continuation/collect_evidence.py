"""Read-only aggregate of the recorded trial; never export rows or credentials."""

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


WINDOWS = (
    ("latest_turn", "2026-10-22T05:31:03", "2026-10-22T05:41:01"),
    ("two_turns", "2026-10-22T04:52:09", "2026-10-22T05:41:01"),
)


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def collect(root, run_id, manifest):
    result = {
        "schema": "marketops.session-ledger-summary.v1",
        "method": "sqlite_uri_readonly_aggregate_no_network",
        "time_selection": "operation created_at; event recorded_at; UTC",
        "note": "SETTLED is a charge state, not proof of successful page acquisition.",
        "windows": {},
    }
    with closing(readonly(root / "state" / "usage-ledger.sqlite3")) as db:
        db.row_factory = sqlite3.Row
        for label, start, end in WINDOWS:
            rows = [dict(row) for row in db.execute(
                "SELECT provider, capability, outcome, execution_outcome, charge_state, "
                "count(*) AS count FROM operations WHERE run_id=? AND created_at>=? "
                "AND created_at<? GROUP BY 1,2,3,4,5 ORDER BY 1,2,3,4,5",
                (run_id, start, end),
            )]
            result["windows"][label] = {
                "start_utc_inclusive": start + "Z",
                "end_utc_exclusive": end + "Z",
                "operation_rows": rows,
                "raw_operation_count": sum(row["count"] for row in rows),
                "pre_dispatch_refusals": sum(row["count"] for row in rows
                                             if row["outcome"] == "PRE_DISPATCH_REFUSAL"),
            }
    with closing(readonly(root / "state" / "research-events.sqlite3")) as db:
        for label, start, end in WINDOWS:
            statuses, reasons = Counter(), Counter()
            for status, payload in db.execute(
                "SELECT status,payload_json FROM experience_events WHERE run_id=? "
                "AND recorded_at>=? AND recorded_at<? AND kind='tool'",
                (run_id, start, end),
            ):
                data = json.loads(payload)
                if data.get("provider") == "crawl4ai_local" and status != "started":
                    statuses[status] += 1
                    reason = data.get("reason_code")
                    if reason:
                        reasons[reason] += 1
            result["windows"][label]["crawl_terminal_event_statuses"] = dict(sorted(statuses.items()))
            result["windows"][label]["crawl_failure_reason_counts"] = dict(sorted(reasons.items()))
    raw = manifest.read_bytes()
    exported = json.loads(raw.decode("utf-8"))
    result["export_manifest"] = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "status": exported["status"],
        "generated_at": exported.get("generated_at"),
        "input_record_count": exported.get("input_record_count"),
        "active_record_count": exported.get("active_record_count"),
        "note": "Aggregate and file fingerprint only; workbook/contact rows are local-only.",
    }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = collect(args.data_root, args.run_id, args.manifest)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8", newline="\n")
    print(json.dumps({"windows": len(result["windows"]), "raw_rows": {
        key: value["raw_operation_count"] for key, value in result["windows"].items()
    }}))

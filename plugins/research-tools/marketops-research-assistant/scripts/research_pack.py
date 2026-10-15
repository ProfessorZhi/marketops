#!/usr/bin/env python3
"""Offline, single-writer MarketOps research interchange pack. No network or providers."""
from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PACK_VERSION = "0.2.0"
TABLES = {
    "sources": ("source_id", "query", "url", "kind", "discovered_at", "status"),
    "evidence": ("evidence_id", "source_id", "url", "observed_at", "content_sha256", "excerpt", "status"),
    "venue_candidates": ("candidate_id", "name", "country", "region", "source_id", "status"),
    "venues": ("venue_id", "candidate_id", "name", "country", "region", "venue_type", "evidence_id", "status"),
    "organizations": ("organization_id", "name", "official_domain", "evidence_id", "status"),
    "venue_org_links": ("link_id", "venue_id", "organization_id", "relationship", "evidence_id", "status"),
    "account_assessments": ("assessment_id", "organization_id", "scene", "decision", "reason", "evidence_id"),
    "people": ("person_id", "organization_id", "name", "department", "role_title", "evidence_id", "status"),
    "contact_points": ("point_id", "organization_id", "person_id", "type", "literal_value", "evidence_id", "status"),
    "contact_routes": ("route_id", "organization_id", "scene", "person_id", "point_id", "role_function", "grade", "status", "evidence_id", "reviewed_by"),
    "review_queue": ("review_id", "object_type", "object_id", "reason", "status", "reviewer"),
}
STATUSES = {
    "sources": {"discovered", "reviewed", "discarded"},
    "evidence": {"usable", "blocked", "unknown"},
    "venue_candidates": {"discovered", "hold", "rejected"},
    "venues": {"verified", "hold"},
    "organizations": {"discovered", "verified", "hold", "rejected"},
    "venue_org_links": {"verified", "hold"},
    "account_assessments": {"eligible", "hold", "irrelevant", "duplicate"},
    "people": {"discovered", "verified", "hold"},
    "contact_points": {"discovered", "verified", "hold"},
    "contact_routes": {"draft", "ready", "hold", "rejected"},
    "review_queue": {"open", "resolved", "dismissed"},
}
STATUS_FIELD = {t: ("decision" if t == "account_assessments" else "status") for t in TABLES}
REFS = {
    "evidence": {"source_id": "sources"},
    "venue_candidates": {"source_id": "sources"},
    "venues": {"candidate_id": "venue_candidates", "evidence_id": "evidence"},
    "organizations": {"evidence_id": "evidence"},
    "venue_org_links": {"venue_id": "venues", "organization_id": "organizations", "evidence_id": "evidence"},
    "account_assessments": {"organization_id": "organizations", "evidence_id": "evidence"},
    "people": {"organization_id": "organizations", "evidence_id": "evidence"},
    "contact_points": {"organization_id": "organizations", "person_id": "people", "evidence_id": "evidence"},
    "contact_routes": {"organization_id": "organizations", "person_id": "people", "point_id": "contact_points", "evidence_id": "evidence"},
}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_RELATIONS = {"owner", "operator", "maintainer", "procurement", "other"}


def public_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"https", "http"} or not host:
            return False
        if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
            return False
        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return False
        except ValueError:
            pass
        return True
    except ValueError:
        return False


def init_pack(root: Path, request_id: str, country: str, objective: str, scene: str = "") -> None:
    if not ID_RE.fullmatch(request_id):
        raise ValueError("request-id must be a safe stable identifier")
    if not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("country must be 2-letter uppercase ISO-style country code")
    if not objective.strip():
        raise ValueError("objective must not be blank")
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"Refusing to overwrite nonempty pack directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "stages").mkdir(exist_ok=True)
    brief = {
        "format": "marketops-research-pack", "version": PACK_VERSION,
        "request_id": request_id, "country": country, "scene": scene,
        "objective": objective, "execution_mode": "manual",
        "outreach_authorized": False, "approved_paid_calls": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "Interchange pack only; not an authoritative database or provider approval.",
    }
    with (root / "intake.json").open("x", encoding="utf-8") as f:
        json.dump(brief, f, ensure_ascii=False, indent=2)
        f.write("\n")
    for name, columns in TABLES.items():
        with (root / "stages" / f"{name}.csv").open("x", newline="", encoding="utf-8") as f:
            csv.writer(f, lineterminator="\n").writerow(columns)


def read_pack(root: Path) -> tuple[dict[str, dict[str, dict[str, str]]], list[str]]:
    errors: list[str] = []
    all_rows: dict[str, dict[str, dict[str, str]]] = {}
    try:
        brief = json.loads((root / "intake.json").read_text(encoding="utf-8"))
        if brief.get("format") != "marketops-research-pack" or brief.get("version") != PACK_VERSION:
            errors.append("intake.json: unknown pack format/version")
        if brief.get("outreach_authorized") is not False:
            errors.append("intake.json: this research-only pack cannot authorize outreach")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"intake.json: cannot read ({type(exc).__name__})")
    for table, columns in TABLES.items():
        filename = root / "stages" / f"{table}.csv"
        rows: dict[str, dict[str, str]] = {}
        all_rows[table] = rows
        try:
            with filename.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if tuple(reader.fieldnames or ()) != columns:
                    errors.append(f"{table}: header must equal exact schema fields")
                    continue
                for lineno, row in enumerate(reader, start=2):
                    if None in row:
                        errors.append(f"{table}:{lineno}: extra unheaded values")
                        continue
                    item = {k: (v or "").strip() for k, v in row.items()}
                    key = item[columns[0]]
                    if not ID_RE.fullmatch(key):
                        errors.append(f"{table}:{lineno}: missing/invalid ID {key!r}")
                        continue
                    if key in rows:
                        errors.append(f"{table}:{lineno}: duplicate ID {key}")
                        continue
                    rows[key] = item
                    status = item[STATUS_FIELD[table]]
                    if status not in STATUSES[table]:
                        errors.append(f"{table}:{key}: invalid status/decision {status!r}")
        except OSError as exc:
            errors.append(f"{table}: cannot read ({type(exc).__name__})")
    return all_rows, errors


def verify(root: Path) -> dict:
    rows, errors = read_pack(root)
    def err(table: str, key: str, reason: str) -> None:
        errors.append(f"{table}:{key}: {reason}")

    for table, refs in REFS.items():
        for key, row in rows[table].items():
            for field, target in refs.items():
                ident = row[field]
                if ident and ident not in rows[target]:
                    err(table, key, f"{field} -> nonexistent {target}:{ident}")

    evidence = rows["evidence"]
    organizations = rows["organizations"]
    people = rows["people"]
    points = rows["contact_points"]
    def usable(eid: str) -> bool:
        return bool(eid and evidence.get(eid, {}).get("status") == "usable")

    for key, r in rows["sources"].items():
        if not public_url(r["url"]):
            err("sources", key, "source URL must be public http(s)")
    for key, r in evidence.items():
        if not public_url(r["url"]):
            err("evidence", key, "evidence URL must be public http(s)")
        if r["status"] == "usable":
            if not SHA_RE.fullmatch(r["content_sha256"]):
                err("evidence", key, "usable evidence requires actual 64-character lowercase SHA-256")
            if not r["excerpt"]:
                err("evidence", key, "usable evidence needs nonempty source excerpt")
            try:
                observed = datetime.fromisoformat(r["observed_at"].replace("Z", "+00:00"))
                if observed.tzinfo is None:
                    raise ValueError("timezone missing")
            except ValueError:
                err("evidence", key, "usable evidence needs ISO datetime with timezone")

    for table in ("venues", "organizations", "venue_org_links", "people", "contact_points"):
        for key, r in rows[table].items():
            if r["status"] == "verified" and not usable(r["evidence_id"]):
                err(table, key, "verified object requires usable evidence")
    for key, r in rows["venues"].items():
        if r["status"] == "verified" and (not r["candidate_id"] or not r["name"]):
            err("venues", key, "verified venue needs candidate lineage and name")
    for key, r in rows["venue_org_links"].items():
        if r["relationship"] not in VALID_RELATIONS:
            err("venue_org_links", key, "unknown relationship")
        if r["status"] == "verified":
            if rows["venues"].get(r["venue_id"], {}).get("status") != "verified":
                err("venue_org_links", key, "verified link needs verified venue")
            if organizations.get(r["organization_id"], {}).get("status") != "verified":
                err("venue_org_links", key, "verified link needs verified organization")
    assessments = rows["account_assessments"]
    for key, r in assessments.items():
        if r["decision"] == "eligible" and (not r["scene"] or not usable(r["evidence_id"])):
            err("account_assessments", key, "eligible requires named scene and usable evidence")
    for key, r in people.items():
        if r["status"] == "verified" and (
            organizations.get(r["organization_id"], {}).get("status") != "verified"
            or not r["role_title"] or not (r["name"] or r["department"])
        ):
            err("people", key, "verified role needs verified organization, role title, name or department")
    for key, r in points.items():
        if r["person_id"] and people.get(r["person_id"], {}).get("organization_id") != r["organization_id"]:
            err("contact_points", key, "person and contact point organization mismatch")
        if r["type"] not in {"email", "phone", "form", "profile"}:
            err("contact_points", key, "unknown channel type")
        if r["type"] == "email" and r["status"] == "verified":
            if not EMAIL_RE.fullmatch(r["literal_value"]):
                err("contact_points", key, "verified email fails literal address format")
            excerpt = evidence.get(r["evidence_id"], {}).get("excerpt", "")
            if r["literal_value"].lower() not in excerpt.lower():
                err("contact_points", key, "email must literally appear in usable evidence excerpt")
    for key, r in rows["contact_routes"].items():
        p = points.get(r["point_id"], {})
        if r["person_id"] and people.get(r["person_id"], {}).get("organization_id") != r["organization_id"]:
            err("contact_routes", key, "route person and organization mismatch")
        if p and p["organization_id"] != r["organization_id"]:
            err("contact_routes", key, "route point and organization mismatch")
        if r["status"] == "ready":
            if not (p and p.get("status") == "verified" and p.get("type") == "email"):
                err("contact_routes", key, "ready route needs verified literal email point")
            if organizations.get(r["organization_id"], {}).get("status") != "verified":
                err("contact_routes", key, "ready route needs verified organization")
            if not usable(r["evidence_id"]) or not r["reviewed_by"]:
                err("contact_routes", key, "ready route needs usable evidence and human reviewed_by")
            if r["grade"] not in {"A", "B", "C", "D"} or not r["role_function"]:
                err("contact_routes", key, "ready route needs A/B/C/D and role function")
            if not any(a["organization_id"] == r["organization_id"] and
                       a["scene"] == r["scene"] and a["decision"] == "eligible"
                       for a in assessments.values()):
                err("contact_routes", key, "ready route needs eligible assessment for same scene")
    type_to_table = {"source": "sources", "evidence": "evidence", "venue": "venues",
                     "organization": "organizations", "person": "people", "contact_route": "contact_routes"}
    for key, r in rows["review_queue"].items():
        table = type_to_table.get(r["object_type"])
        if table is None or r["object_id"] not in rows[table]:
            err("review_queue", key, "review item must identify an existing known object")

    counts = {table: len(items) for table, items in rows.items()}
    statuses = {name: {status: sum(1 for r in items.values() if r[STATUS_FIELD[name]] == status)
                       for status in sorted(STATUSES[name])}
                for name, items in rows.items()}
    total = sum(counts.values())
    return {
        "result": "INVALID" if errors else ("STRUCTURALLY_VALID_EMPTY" if total == 0 else "STRUCTURALLY_VALID_NOT_BUSINESS_APPROVED"),
        "format_version": PACK_VERSION, "counts": counts, "statuses": statuses,
        "errors": errors, "business_approval": "NOT_TESTED",
        "note": "Structural checks cannot prove market coverage, source semantics or authorization to send.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    ini = sub.add_parser("init", help="create a new empty portable research pack")
    ini.add_argument("path", type=Path)
    ini.add_argument("--request-id", required=True)
    ini.add_argument("--country", required=True)
    ini.add_argument("--objective", required=True)
    ini.add_argument("--scene", default="")
    for command in ("validate", "summary"):
        sub.add_parser(command, help="offline structure/status read only").add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            init_pack(args.path, args.request_id, args.country, args.objective, args.scene)
            print(json.dumps({"result": "EMPTY_PACK_CREATED", "path": str(args.path), "tables": len(TABLES)}))
            return 0
        result = verify(args.path)
        if args.cmd == "summary":
            result = {k: result[k] for k in ("result", "counts", "statuses", "errors", "note")}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["result"] == "INVALID" else 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""Role diversity progress for named contact research; no invented headcount."""
from collections import defaultdict
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

ROLE_GROUPS = {
    "management": r"directeur g[eé]n[eé]ral|general manager|chief|\bceo\b|pr[eé]sident|fondateur|founder|owner",
    "operations": r"op[eé]ration|exploit|facility|centre director|directeur (?:de |du )?(?:centre|site|complexe)|venue director|facility manager",
    "commercial_events": r"commercial|partenariat|partnership|communication|[eé]v[eé]nement|event|sales|business development",
}


def coverage(root, run_id, *, limit=10):
    from research_workflow import _tip
    path = Path(root) / "state/research-lite.sqlite3"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("SELECT record_id,kind,entity_key,payload_json,artifact_ids_json FROM records "
                          "WHERE run_id=? AND kind IN ('observation','organization','person','role','contact_point','review')",
                          (run_id,)).fetchall()
    groups = defaultdict(list)
    for rid, kind, key, payload, artifacts in rows:
        data = json.loads(payload)
        if kind == "observation" and data.get("record_type") != "research_brief":
            continue
        groups[(kind, key)].append({"record_id": rid, "kind": kind, "entity_key": key,
                                   "data": data, "artifact_ids": json.loads(artifacts)})
    active = [tip for versions in groups.values() if (tip := _tip(versions)) is not None]
    brief = next((r["data"] for r in active if r["kind"] == "observation"), {})
    wanted = brief.get("role_groups_wanted", [])
    if not wanted:
        return {"enabled": False, "fixed_people_quota": None, "targets": []}
    if not isinstance(wanted, list) or any(g not in ROLE_GROUPS for g in wanted):
        raise ValueError("CONTACT_ROLE_GROUPS_INVALID")
    people = {r["entity_key"]: r for r in active if r["kind"] == "person" and r["artifact_ids"]}
    contacts = {(r["data"].get("organization_id"), r["data"].get("person_id")) for r in active
                if r["kind"] == "contact_point" and r["artifact_ids"]
                and r["data"].get("person_id") in people}
    covered = defaultdict(set)
    excluded = {r["data"].get("target_entity_key") for r in active if r["kind"] == "review"
                and r["data"].get("review_type") == "target_disposition"
                and r["data"].get("target_kind") == "organization"
                and r["data"].get("disposition") in {"EXCLUDED", "DUPLICATE"}}
    for role in active:
        if role["kind"] != "role" or not role["artifact_ids"]:
            continue
        data = role["data"]
        org, person = data.get("organization_id"), data.get("person_id")
        if (org, person) not in contacts or people[person]["data"].get("organization_id") != org:
            continue
        title = str(data.get("role_title") or data.get("role_function") or "")
        covered[org].update(group for group in wanted if re.search(ROLE_GROUPS[group], title, re.I))
    targets = []
    for org in active:
        if org["kind"] != "organization" or org["entity_key"] in excluded:
            continue
        missing = sorted(set(wanted) - covered[org["entity_key"]])
        if missing:
            targets.append({"organization_key": org["entity_key"], "name": org["data"].get("name"),
                "covered_role_groups": sorted(covered[org["entity_key"]]), "missing_role_groups": missing,
                "next_method": "Use another public source for the missing role; reuse confirmed contacts across verified venue-operator relationships."})
    return {"enabled": True, "fixed_people_quota": None, "role_groups_wanted": wanted,
            "organizations_with_missing_roles": len(targets), "targets": targets[:limit],
            "verification": "candidate coverage only; source audit and delivery gates remain required"}

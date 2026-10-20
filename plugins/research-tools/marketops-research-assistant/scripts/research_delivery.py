#!/usr/bin/env python3
"""Evidence-audited research XLSX + CSV delivery from one Lite SQLite Run.

This is a *candidate-stage business workbook*, not the canonical MarketOps
human/SOP-verified ContactRoute exporter. No email sending or status promotion.
Uses the same validated OOXML serializer as the canonical integration branch.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

import research_lite_store as store
import research_evidence_store as provenance
import research_workflow as workflow
import research_evidence as evidence
from research_event_journal import _root, append_event, append_note
from research_experience_summary import refresh_quietly as refresh_summary
from _xlsx import Sheet, render_workbook

VERSION = "0.5.6-rc1"
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
FORMULA = ("=", "+", "-", "@")
NAMESPACE = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ROW_LIMIT = 20_000
MAX_MATERIAL = 4_000_000

#: Sheets a client reads. Only records that passed the admission gate below may
#: appear here. A held candidate is written to 排除与待确认 and preserved in
#: 候选数据审计; it is never silently dropped and never promoted into these tables.
CLIENT_BUSINESS_SHEETS = ("客户总表", "联系人路线", "场地与资产", "人员岗位")
INTERNAL_AUDIT_SHEETS = ("排除与待确认", "证据索引", "本轮说明", "来源与查询", "候选数据审计")

#: Contract for Lane B's client view (research_client_view.py, owned elsewhere).
#: `evaluate_admission` is the single decision point; a consumer must read
#: `decisions`/`admitted`/`rejected`, never the truncated audit sample window.
ADMISSION_SCHEMA = "marketops.research.delivery-admission.v1"
#: Material kinds that can anchor a candidate: the browser-rendered DOM, the
#: original response, or the vendor's semantic HTML. Provider-extracted markdown,
#: images and model decisions are derived evidence and cannot be the sole anchor.
ADMISSIBLE_MATERIAL_KINDS = frozenset({"rendered_html", "raw_response", "provider_html"})
BUSINESS_KINDS = ("organization", "venue", "contact_point", "person", "role", "relationship")
HOLD_DISPOSITIONS = frozenset({"HOLD", "EXCLUDED", "DUPLICATE"})

TABLES = (
    ("客户总表", ("组织ID", "组织名称", "研究范围", "研究状态", "关联场地数",
                 "公开联系渠道数", "原始资料引用", "未解决事项")),
    ("联系人路线", ("渠道ID", "组织ID", "联系人或部门", "职能", "渠道类型",
                   "公开字面值", "来源字面校验", "研究评级", "业务状态", "资料引用")),
    ("排除与待确认", ("类型", "对象ID", "问题或原因", "后续动作")),
    ("证据索引", ("资料ID", "来源URL", "提供者", "资料类型",
                 "取得时间", "字节数", "SHA-256", "引用状态",
                 # Span-level provenance: which records read this artifact, and which
                 # span of it they read. Without these a row could only say that a
                 # file was attached, never that a candidate was read from it.
                 "引用记录数", "引用跨度SHA256", "证据信封ID", "候选边界")),
    ("本轮说明", ("字段", "值")),
    ("场地与资产", ("场地ID", "名称", "关联组织ID", "研究状态", "资料引用")),
    ("人员岗位", ("候选ID", "记录类型", "姓名或部门", "原始岗位", "组织ID", "状态", "资料引用")),
    ("来源与查询", ("线索ID", "标题", "来源URL", "工具", "状态")),
    ("候选数据审计", ("记录ID", "阶段", "对象类型", "实体键", "存储状态",
                   "引用资料ID", "原始候选JSON")),
)


def _plain(value: object, unknown: str = "UNKNOWN") -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:3000]
    return unknown


def _literal(value: object) -> str:
    """Never interpret untrusted text as a spreadsheet formula."""
    result = "" if value is None else str(value)
    if result.lstrip().startswith(FORMULA):
        return "'" + result
    return result


def _table(name: str, rows: list[tuple]) -> Sheet:
    fields = dict(TABLES)[name]
    safe_rows = tuple(tuple(_literal(v) if isinstance(v, str) else v for v in row) for row in rows)
    if len(safe_rows) > ROW_LIMIT:
        raise ValueError("EXPORT_TOO_MANY_ROWS_FOR_LITE")
    if any(len(row) != len(fields) for row in safe_rows):
        raise ValueError(f"COLUMN_MISMATCH:{name}")
    return Sheet(name, tuple(fields), safe_rows,
                 widths=tuple(min(45.0, max(15.0, len(col) * 2.5)) for col in fields))


def _read_snapshot(root: Path, run_id: str) -> tuple[dict, list[dict], list[dict]]:
    store._id(run_id, "run_id")
    with closing(store._connection(root)) as db:
        # One explicit transaction: the run row, the record set and the artifact set
        # must describe a single snapshot. Without this, a concurrent append could
        # land between the SELECTs and the workbook would describe a run that never
        # existed at any one instant.
        db.execute("BEGIN")
        try:
            run = db.execute(
                "SELECT scope,objective,rule_version,created_at FROM runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError("UNKNOWN_RUN")
            db_records = db.execute(
                "SELECT record_id,stage,kind,status,entity_key,payload_json,artifact_ids_json "
                "FROM records WHERE run_id=? ORDER BY created_at,record_id",
                (run_id,),
            ).fetchall()
            db_artifacts = db.execute(
                "SELECT artifact_id,provider,origin_url,material_kind,content_sha256,relative_path,acquired_at "
                "FROM artifacts WHERE run_id=? ORDER BY artifact_id",
                (run_id,),
            ).fetchall()
        except BaseException:
            db.rollback()
            raise
        db.commit()
    if len(db_records) > ROW_LIMIT:
        raise ValueError("LITE_EXPORT_TOO_MANY_RECORDS")
    records = []
    for r in db_records:
        payload, refs = json.loads(r[5]), json.loads(r[6])
        if not isinstance(payload, dict) or not isinstance(refs, list):
            raise ValueError("STORED_RECORD_SCHEMA_INVALID")
        records.append({
            "record_id": r[0], "stage": r[1], "kind": r[2], "status": r[3],
            "entity_key": r[4], "data": payload, "artifact_ids": refs,
        })
    artifacts = []
    for a in db_artifacts:
        material = (root / a[5]).resolve()
        allowed = (root / "artifacts").resolve()
        if not material.is_relative_to(allowed) or not material.is_file():
            raise ValueError(f"ARTIFACT_MISSING_OR_OUTSIDE_ROOT:{a[0]}")
        b = material.read_bytes()
        if not b or len(b) > MAX_MATERIAL or hashlib.sha256(b).hexdigest() != a[4]:
            raise ValueError(f"ARTIFACT_READBACK_HASH_MISMATCH:{a[0]}")
        artifacts.append({
            "artifact_id": a[0], "provider": a[1], "url": a[2],
            "kind": a[3], "sha256": a[4], "bytes": len(b),
            "text": b.decode("utf-8", errors="replace"), "acquired_at": a[6],
        })
    known = {a["artifact_id"] for a in artifacts}
    for record in records:
        if any(ref not in known for ref in record["artifact_ids"]):
            raise ValueError(f"RECORD_REFERENCES_MISSING_ARTIFACT:{record['record_id']}")
    return dict(zip(("scope", "objective", "rule_version", "created_at"), run)), records, artifacts


def _read_provenance(root: Path, run_id: str) -> tuple[list[dict], list[dict]]:
    """Provenance links and recorded envelopes for one run.

    These are read in their own transaction rather than folded into `_read_snapshot`,
    whose three-tuple shape is relied on elsewhere. Both tables are append-only
    within a run, so a row can be added after the snapshot but never rewritten
    underneath it: the workbook can only ever describe *more* provenance than the
    snapshot carried, never different provenance.
    """
    return provenance.evidence_links(root, run_id), provenance.stored_envelopes(root, run_id)


_PLACEHOLDERS = frozenset({"", "UNKNOWN", "NONE", "N/A", "NULL", "TBD", "NOT_FOUND"})


def _is_meaningful(value: object) -> bool:
    return isinstance(value, str) and value.strip().upper() not in _PLACEHOLDERS


def _field_text(data: dict, *names: str) -> str:
    """First present string field, trimmed. `None` never becomes the string "None"."""
    for name in names:
        value = data.get(name)
        if isinstance(value, str):
            return value.strip()
    return ""


def _literal_in(text: str, value: str) -> bool:
    """A literal occurrence with address boundaries, not an arbitrary substring.

    Mirrors the audit's own boundary rule: a trailing sentence dot is punctuation,
    while a dot followed by another address character is a longer token.
    """
    if not isinstance(value, str) or not value:
        return False
    if "@" in value:
        return re.search(r"(?<![\w.+-])" + re.escape(value) + r"(?![\w+-]|\.[\w+-])",
                         text, re.I) is not None
    return value.casefold() in text.casefold()


def _claim_evidence(record: dict, assets: dict[str, dict], normalized: dict[str, str],
                    link_index: set[tuple]) -> tuple[set[str], set[str], list[str]]:
    """Per-claim fail-closed checks; every reason code is an admission blocker.

    A quote must exist in the saved bytes, come from an admissible material kind,
    and be bound to a recorded (artifact, field, span) link. Nothing here reads the
    audit's result window: the caller passes every record, so >100 mixed records
    cannot hide a bad one behind `samples[:100]`.
    """
    codes: set[str] = set()
    matched_fields: set[str] = set()
    matched_quotes: list[str] = []
    ids = record.get("artifact_ids") or []
    claims = record["data"].get("claims")
    if not ids:
        codes.add("NO_SOURCE")
    if not isinstance(claims, list) or len(claims) > 12:
        codes.add("CLAIMS_INVALID")
        claims = []
    if not claims:
        codes.add("NO_EXCERPT")
    for claim in claims:
        if not isinstance(claim, dict):
            codes.add("CLAIMS_INVALID")
            continue
        field, artifact_id, quote = (claim.get("field"), claim.get("artifact_id"),
                                     claim.get("quote"))
        if (not isinstance(field, str) or not evidence.KEY.fullmatch(field)
                or not isinstance(artifact_id, str) or artifact_id not in ids
                or artifact_id not in assets
                or not isinstance(quote, str) or not 12 <= len(quote) <= 600):
            codes.add("CLAIMS_INVALID")
            continue
        material = assets[artifact_id]
        kind = material.get("kind")
        if kind not in ADMISSIBLE_MATERIAL_KINDS:
            codes.add("SOURCE_TYPE_NOT_ADMISSIBLE:" + str(kind))
            continue
        wanted = evidence._norm(quote)
        if not wanted or wanted not in normalized.get(artifact_id, ""):
            codes.add("EXCERPT_NOT_FOUND")
            continue
        span = hashlib.sha256(wanted.encode("utf-8")).hexdigest()
        if (artifact_id, field, span) not in link_index:
            codes.add("SPAN_NOT_RECORDED")
            continue
        matched_fields.add(field)
        matched_quotes.append(wanted)
    return codes, matched_fields, matched_quotes


def _binding_supported(record: dict, organization: dict,
                       normalized: dict[str, str]) -> bool:
    """Does the evidence tie this record to the organisation it names?

    Either both records cite a common artifact, or the organisation's own recorded
    name occurs in material this record cites. A bare `organization_id` string is
    an assertion, not a binding; a wrong one must fail closed rather than attach a
    contact or venue to a different institution.
    """
    ids = set(record.get("artifact_ids") or ())
    if ids & set(organization.get("artifact_ids") or ()):
        return True
    name = organization["data"].get("name")
    if isinstance(name, str) and len(name.strip()) >= 3:
        wanted = evidence._norm(name)
        if wanted and any(wanted in normalized.get(a, "") for a in ids):
            return True
    return False


def evaluate_admission(records: list[dict], artifacts, links: list[dict],
                       version_conflicts=(), *, conflict_records=()) -> dict:
    """One admission decision per business-kind record, computed for the whole set.

    Pure: reads no disk and writes nothing. `records` is the active append-only
    snapshot; `conflict_records` are the versions removed by
    `workflow.active_records` and are used only to map a forked review disposition
    back to the target it would have held.

    A record is admitted only when every dimension passes — identity, citation
    span, material kind, contact literal, organisation binding, disposition and
    version state. Any reason code means the record goes to 排除与待确认 and
    候选数据审计, never to a client-facing business sheet.
    """
    assets = dict(artifacts) if isinstance(artifacts, dict) else {
        a["artifact_id"]: a for a in artifacts}
    normalized = {aid: evidence._text(a) for aid, a in assets.items()}
    links_by_record: dict[str, list[dict]] = defaultdict(list)
    for link in links:
        links_by_record[link["record_id"]].append(link)
    conflict_ids = {rid for conflict in version_conflicts
                    for rid in conflict.get("record_ids", ())}

    held_targets: dict[tuple[str, str], str] = {}
    for row in records:
        data = row.get("data") or {}
        if row.get("kind") != "review" or data.get("review_type") != "target_disposition":
            continue
        target_kind, target_key = data.get("target_kind"), data.get("target_entity_key")
        if not isinstance(target_kind, str) or not isinstance(target_key, str):
            continue
        disposition = data.get("disposition")
        if disposition not in workflow.DISPOSITIONS:
            held_targets[(target_kind, target_key)] = "TARGET_DISPOSITION_INVALID"
        elif disposition in HOLD_DISPOSITIONS:
            held_targets[(target_kind, target_key)] = "TARGET_DISPOSITION_" + disposition
    disposition_conflicts: set[tuple[str, str]] = set()
    for row in conflict_records:
        data = row.get("data") or {}
        if row.get("kind") == "review" and data.get("review_type") == "target_disposition":
            target_kind, target_key = data.get("target_kind"), data.get("target_entity_key")
            if isinstance(target_kind, str) and isinstance(target_key, str):
                disposition_conflicts.add((target_kind, target_key))

    prepared: dict[str, tuple[set[str], set[str], list[str]]] = {}
    for row in records:
        kind = row.get("kind")
        if kind not in BUSINESS_KINDS:
            continue
        link_index = {(link["artifact_id"], link["field"], link["span_sha256"])
                      for link in links_by_record.get(row["record_id"], ())}
        codes, fields, quotes = _claim_evidence(row, assets, normalized, link_index)
        data = row.get("data") or {}
        if kind in {"organization", "venue"} and "identity" not in fields:
            codes.add("MISSING_REQUIRED_IDENTITY")
        if kind == "relationship" and "relationship" not in fields:
            codes.add("MISSING_REQUIRED_RELATIONSHIP")
        if kind == "organization" and not _is_meaningful(data.get("name")):
            codes.add("ORGANIZATION_NAME_MISSING")
        if kind == "venue" and not _is_meaningful(data.get("name")):
            codes.add("VENUE_NAME_MISSING")
        if kind == "contact_point":
            value = _field_text(data, "value", "email")
            channel = _field_text(data, "type", "channel").casefold()
            if not channel:
                codes.add("CONTACT_CHANNEL_MISSING")
            if not value:
                codes.add("CONTACT_VALUE_MISSING")
            else:
                if channel == "email" and not EMAIL.fullmatch(value):
                    codes.add("EMAIL_INVALID")
                literal = any(
                    _literal_in(normalized.get(a, ""), value)
                    for a in (row.get("artifact_ids") or ())
                    if a in assets and assets[a].get("kind") in ADMISSIBLE_MATERIAL_KINDS)
                if not literal:
                    codes.add("EMAIL_NOT_IN_PRIMARY_MATERIAL" if channel == "email"
                              else "CONTACT_VALUE_NOT_LITERAL")
                # A literal on the page is not a quote. The admitted row must be
                # read from a recorded span whose text contains the value itself.
                if not any(_literal_in(quote, value) for quote in quotes):
                    codes.add("CONTACT_VALUE_NOT_IN_EXCERPT")
        if kind in {"person", "role"}:
            name = data.get("name") if kind == "person" else data.get("department")
            role = data.get("role_title", data.get("role_function"))
            if not _is_meaningful(name) and not _is_meaningful(role):
                codes.add("PERSON_OR_ROLE_IDENTITY_MISSING")
        hold = held_targets.get((kind, row.get("entity_key")))
        if hold:
            codes.add(hold)
        if (kind, row.get("entity_key")) in disposition_conflicts:
            codes.add("DISPOSITION_VERSION_CONFLICT")
        if row["record_id"] in conflict_ids:
            codes.add("VERSION_CONFLICT")
        prepared[row["record_id"]] = (codes, fields, quotes)

    # Organisations are the root of the association graph: a venue or contact may
    # only bind to an organisation that is itself admitted. A rejected organisation
    # therefore takes its dependants with it rather than lending them its key.
    org_by_key: dict[str, dict] = {}
    for row in records:
        if row.get("kind") != "organization":
            continue
        codes = prepared[row["record_id"]][0]
        if not codes:
            if row["entity_key"] in org_by_key:
                codes.add("ORGANIZATION_ENTITY_DUPLICATE")
            else:
                org_by_key[row["entity_key"]] = row

    decisions = []
    for row in records:
        kind = row.get("kind")
        if kind not in BUSINESS_KINDS:
            continue
        codes, fields, _ = prepared[row["record_id"]]
        data = row.get("data") or {}
        organization_id = _field_text(data, "organization_id")
        if organization_id and kind != "organization":
            organization = org_by_key.get(organization_id)
            if organization is None:
                codes.add("ORGANIZATION_NOT_ADMITTED")
            elif not _binding_supported(row, organization, normalized):
                codes.add("ORGANIZATION_PROVENANCE_MISMATCH")
        decisions.append({
            "record_id": row["record_id"], "kind": kind,
            "entity_key": row["entity_key"], "admitted": not codes,
            "reason_codes": sorted(codes), "matched_fields": sorted(fields),
            "organization_id": organization_id or None,
        })

    admitted_ids: dict[str, list[str]] = defaultdict(list)
    rejected_ids: list[str] = []
    reason_counts: Counter = Counter()
    for decision in decisions:
        if decision["admitted"]:
            admitted_ids[decision["kind"]].append(decision["record_id"])
        else:
            rejected_ids.append(decision["record_id"])
            reason_counts.update(decision["reason_codes"])
    return {
        "schema": ADMISSION_SCHEMA,
        "decisions": decisions,
        "admitted": {kind: list(ids) for kind, ids in admitted_ids.items()},
        "rejected": rejected_ids,
        "admitted_record_ids": [d["record_id"] for d in decisions if d["admitted"]],
        "rejected_record_ids": rejected_ids,
        "admitted_by_kind": {kind: len(ids) for kind, ids in sorted(admitted_ids.items())},
        "rejected_by_kind": dict(sorted(Counter(
            d["kind"] for d in decisions if not d["admitted"]).items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "evaluated_record_count": len(decisions),
        "admitted_record_count": sum(1 for d in decisions if d["admitted"]),
        "rejected_record_count": len(rejected_ids),
        "version_conflict_record_ids": sorted(conflict_ids),
        "version_conflict_record_count": len(conflict_ids),
        "client_facing_sheets": list(CLIENT_BUSINESS_SHEETS),
        "internal_audit_sheets": list(INTERNAL_AUDIT_SHEETS),
        "sample_window_independent": True,
    }


_ADMISSION_ISSUE_TYPE = {
    "organization": "组织准入", "venue": "场地准入",
    "contact_point": "联系渠道准入", "person": "人员岗位准入",
    "role": "人员岗位准入", "relationship": "关联身份准入",
}
_ADMISSION_ACTION = {
    "organization": "补充组织身份页面引文与跨度后重新提交；不得进入客户总表",
    "venue": "补充场地身份页面引文并解除 HOLD；不得进入场地与资产",
    "contact_point": "回到原始页面取得含该字面值的具名引文；不得晋升联系人",
    "person": "补充人员或角色的页面引文与岗位证据；不得进入人员岗位",
    "role": "补充人员或角色的页面引文与岗位证据；不得进入人员岗位",
    "relationship": "核实关联双方并补充同一来源跨度；不得进入关联结论",
}


def build_delivery(root: Path, run_id: str) -> tuple[list[Sheet], dict]:
    run, records, artifacts = _read_snapshot(root, run_id)
    links, envelopes = _read_provenance(root, run_id)
    if not records or not artifacts:
        raise ValueError("NO_RESEARCH_ROWS_OR_SOURCE_ARTIFACTS: not a successful E2E")
    # Assess exactly this snapshot: the same records that produced the workbook, so
    # the manifest, the notes sheet and the recorded gaps all describe one instant.
    # This is a structural check only — not semantic, not SOP and not coverage.
    goal = workflow.assess_records(run_id, records)
    # Business sheets show only active append-only tips. Complete history
    # remains visible in 候选数据审计, so a correction is not a second venue.
    effective, version_conflicts = workflow.active_records(records)
    assets = {x["artifact_id"]: x for x in artifacts}
    # Span-level provenance, indexed both ways. A record that cites an artifact
    # without a link is exactly the gap an attachment list cannot show: "a file is
    # attached" and "this candidate was read from that file" are not the same claim.
    links_by_artifact: dict[str, list[dict]] = defaultdict(list)
    links_by_record: dict[str, list[dict]] = defaultdict(list)
    for link in links:
        links_by_artifact[link["artifact_id"]].append(link)
        links_by_record[link["record_id"]].append(link)
    envelopes_by_artifact: dict[str, list[dict]] = defaultdict(list)
    for envelope in envelopes:
        envelopes_by_artifact[envelope["raw_artifact_ref"]].append(envelope)
    audit_all = evidence.audit_records(effective, assets, result_limit=100)
    # A workbook can list many held records. Keep the MCP/manifest receipt tiny
    # while carrying individual record IDs in the human-review worksheet.
    audit = {**audit_all, "samples": audit_all["samples"][:8], "sample_limit": 8}
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for record in effective:
        by_kind[record["kind"]].append(record)

    # One gate decides the whole workbook. It is computed over `effective` — every
    # active record, never the audit's 100-sample window — so a bad record at
    # position 101 cannot slip into a client sheet. The issue list is completed
    # before any business row is built, so a row's status can never be computed
    # from a partial list, and each issue carries its subject's entity key and
    # record id explicitly instead of comparing one against the other.
    conflict_ids = {rid for conflict in version_conflicts
                    for rid in conflict.get("record_ids", ())}
    conflict_records = [r for r in records if r["record_id"] in conflict_ids]
    admission = evaluate_admission(effective, assets, links, version_conflicts,
                                   conflict_records=conflict_records)
    by_id = {r["record_id"]: r for r in effective}
    admitted_by_kind: dict[str, list[dict]] = defaultdict(list)
    for decision in admission["decisions"]:
        if decision["admitted"]:
            admitted_by_kind[decision["kind"]].append(by_id[decision["record_id"]])
    organizations = admitted_by_kind["organization"]
    venues = admitted_by_kind["venue"]
    contacts = admitted_by_kind["contact_point"]
    people = admitted_by_kind["person"] + admitted_by_kind["role"]
    # Admitted relationships are reported in the manifest; the fixed nine-sheet
    # contract has no client relationship sheet, so no row is invented for them.
    relationships = admitted_by_kind["relationship"]
    org_by_key = {org["entity_key"]: org for org in organizations}

    issues: list[dict] = []

    def add_issue(issue_type: str, object_id: str, reason: str, action: str, *,
                  entity_key: str | None = None,
                  organization_id: str | None = None) -> None:
        issues.append({"type": issue_type, "object_id": object_id,
                       "entity_key": entity_key, "organization_id": organization_id,
                       "reason": reason, "action": action})

    for item in version_conflicts:
        add_issue("候选版本冲突", item["entity_key"],
                  "同类实体存在未能形成唯一 supersedes 链的版本：" +
                  ", ".join(item["record_ids"][:8]),
                  "先追加具名更正或人工核对；冲突版本不进入主业务表",
                  entity_key=item["entity_key"])
    seen_orgs: set[str] = set()
    for org in organizations:
        if org["entity_key"] in seen_orgs:
            add_issue("组织合并冲突", org["entity_key"],
                      "同一组织实体键出现多条候选记录，禁止静默覆盖",
                      "人工核实组织及版本", entity_key=org["entity_key"])
        seen_orgs.add(org["entity_key"])
    for decision in admission["decisions"]:
        if decision["admitted"]:
            continue
        kind = decision["kind"]
        add_issue(_ADMISSION_ISSUE_TYPE.get(kind, "准入排除"), decision["record_id"],
                  "准入未通过：" + ", ".join(decision["reason_codes"]),
                  _ADMISSION_ACTION.get(kind, "补充原始来源与引文后重新提交"),
                  entity_key=decision["entity_key"],
                  organization_id=decision.get("organization_id"))
    spanless = [record for record in effective
                if record["kind"] in evidence.CHECK_KINDS
                and record["artifact_ids"] and not links_by_record.get(record["record_id"])]
    for record in spanless:
        add_issue("来源跨度未记录", record["record_id"],
                  "候选引用了资料但未记录读取跨度：" + "; ".join(record["artifact_ids"][:8]),
                  "回到同一资料的具名片段；仅有附件级支持的候选不进入业务结论",
                  entity_key=record["entity_key"])
    for route in by_kind["contact_route"]:
        # The Lite MCP can only persist *proposals*. No text or grade is formal.
        add_issue("联系路线待审核", route["record_id"],
                  "Lite 暂存路线不能执行正式 A/B/C/D 晋升",
                  "通过授权 MarketOps SOP/人工核验系统定级",
                  entity_key=route["entity_key"])
    for other in by_kind["review"]:
        add_issue("人工复核", other["record_id"],
                  _plain(other["data"].get("reason")), "人工决策并记录审批来源",
                  entity_key=other["entity_key"])

    venue_counts = Counter()
    for venue in venues:
        org_key = _field_text(venue["data"], "organization_id")
        if org_key:
            venue_counts[org_key] += 1
    contact_counts = Counter()
    for candidate in contacts:
        org_key = _field_text(candidate["data"], "organization_id")
        if org_key:
            contact_counts[org_key] += 1
    rejected_bound = Counter(
        decision["organization_id"] for decision in admission["decisions"]
        if not decision["admitted"] and decision.get("organization_id"))
    issue_keys = Counter(issue["entity_key"] for issue in issues if issue.get("entity_key"))

    if not organizations and not venues:
        add_issue("业务对象缺失", run_id, "缺少通过准入的组织和场地候选",
                  "继续在限定范围内研究")

    org_rows = []
    for org in organizations:
        key, fact = org["entity_key"], org["data"]
        org_rows.append((
            key, _plain(fact.get("name")), run["scope"], "CANDIDATE_NOT_APPROVED",
            venue_counts[key], contact_counts[key],
            "; ".join(org["artifact_ids"]) or "NONE",
            "REVIEW_REQUIRED" if (issue_keys[key] or rejected_bound[key])
            else "PENDING_SOP_QUALIFICATION",
        ))

    contact_rows: list[tuple] = []
    for candidate in contacts:
        fact = candidate["data"]
        org_key = _field_text(fact, "organization_id")
        channel = str(fact.get("type", fact.get("channel", ""))).strip().lower()
        value = str(fact.get("value", fact.get("email", ""))).strip()
        kinds = {assets[a]["kind"] for a in candidate["artifact_ids"] if a in assets}
        if channel == "email":
            validation = ("RENDERED_EMAIL_LITERAL_NOT_BUSINESS_VERIFIED"
                          if kinds & {"rendered_html", "raw_response"}
                          else "VENDOR_HTML_EMAIL_LITERAL_NOT_BUSINESS_VERIFIED")
        else:
            validation = "LITERAL_IN_SAVED_MATERIAL"
        contact_rows.append((
            candidate["record_id"], org_key or "UNKNOWN",
            _plain(fact.get("person_name", fact.get("department", ""))),
            _plain(fact.get("role_function", fact.get("role_title", ""))),
            channel or "UNKNOWN", value or "UNKNOWN", validation,
            "RULES_UNKNOWN", "CANDIDATE_NOT_APPROVED",
            "; ".join(candidate["artifact_ids"]) or "NONE",
        ))

    venue_rows = [(
        x["entity_key"], _plain(x["data"].get("name")),
        _plain(_field_text(x["data"], "organization_id") or None),
        "CANDIDATE_NOT_APPROVED", "; ".join(x["artifact_ids"]) or "NONE",
    ) for x in venues]

    people_rows = []
    for item in people:
        facts = item["data"]
        org = _field_text(facts, "organization_id")
        people_rows.append((
            item["record_id"], item["kind"],
            _plain(facts.get("name", facts.get("department", ""))),
            _plain(facts.get("role_title", facts.get("role_function", ""))),
            org or "UNKNOWN", "CANDIDATE_NOT_VERIFIED",
            "; ".join(item["artifact_ids"]) or "NONE",
        ))

    source_rows = [(
        x["record_id"], _plain(x["data"].get("title")),
        _plain(x["data"].get("url")), _plain(x["data"].get("provider")),
        x["status"],
    ) for x in by_kind["source"]]

    evidence_rows = [(
        a["artifact_id"], a["url"], a["provider"], a["kind"],
        a["acquired_at"], a["bytes"], a["sha256"], "READBACK_HASH_OK_NOT_FACT_VERIFIED",
        len({link["record_id"] for link in links_by_artifact.get(a["artifact_id"], ())}),
        "; ".join(sorted({link["span_sha256"]
                          for link in links_by_artifact.get(a["artifact_id"], ())})) or "NONE",
        "; ".join(sorted({envelope["evidence_id"]
                          for envelope in envelopes_by_artifact.get(a["artifact_id"], ())})) or "NONE",
        "CANDIDATE_ONLY",
    ) for a in artifacts]

    # §3.5 makes an envelope without a ledger row ineligible for export. The gate
    # reports that as a blocked state; it does not fill the link with a placeholder,
    # and it does not silently present the envelope as exportable.
    envelope_gate = ("NO_ENVELOPES" if not envelopes
                     else "BLOCKED_LEDGER_NOT_WIRED"
                     if any(x["ledger_id"] is None for x in envelopes) else "OPEN")
    envelopeless = sorted({a["artifact_id"] for a in artifacts
                           if links_by_artifact.get(a["artifact_id"])
                           and not envelopes_by_artifact.get(a["artifact_id"])})
    notes = [
        ("Run", run_id),
        ("研究范围", run["scope"]),
        ("原始任务", run["objective"]),
        ("SOP/规则版本", run["rule_version"]),
        ("研究状态", "PARTIAL_CANDIDATE_ONLY"),
        ("业务正式审查", "NOT_PERFORMED"),
        ("业务表准入判定", "FAIL_CLOSED_PER_RECORD"),
        ("准入评估记录数", admission["evaluated_record_count"]),
        ("准入通过记录数", admission["admitted_record_count"]),
        ("准入排除记录数", admission["rejected_record_count"]),
        ("准入排除原因", json.dumps(admission["reason_counts"],
                                   ensure_ascii=False, sort_keys=True)),
        ("准入通过关联身份数", len(relationships)),
        ("客户业务表", "、".join(CLIENT_BUSINESS_SHEETS)),
        ("内部审计表", "、".join(INTERNAL_AUDIT_SHEETS)),
        ("实际组织候选数", len(organizations)),
        ("实际场地候选数", len(venue_rows)),
        ("实际人员/岗位候选数", len(people_rows)),
        ("实际联系渠道候选数", len(contact_rows)),
        ("实际原始/派生资料数", len(evidence_rows)),
        ("待核查事项数", len(issues)),
        ("市场总覆盖率", "UNKNOWN_NO_TRUSTWORTHY_DENOMINATOR"),
        ("收费 Provider 额度与价格", "UNKNOWN_NOT_IN_LITE_STORE"),
        ("联系路线评级", "RULES_UNKNOWN; no A/B/C/D assigned by Agent"),
        ("发送授权", "FALSE: research-only; no outreach"),
        ("通用证据审计状态", audit["status"]),
        ("待补证据计数", json.dumps(audit["gap_counts"], ensure_ascii=False, sort_keys=True)),
        ("证据语义人工复核", "REQUIRED: quote match cannot prove business fact"),
        ("证据跨度引用数", len(links)),
        ("无可读跨度的候选数", len(spanless)),
        ("证据信封数", len(envelopes)),
        ("证据信封导出闸门", envelope_gate),
        ("结构目标状态", goal["status"]),
        ("结构目标满足", str(goal["goal_satisfied"])),
        ("结构检查范围", goal["assessment_scope"]),
        ("缺口统计", json.dumps(goal["gap_counts"], ensure_ascii=False, sort_keys=True)),
        ("推荐下一步 Skill", goal.get("recommended_skill", "UNKNOWN")),
    ]
    audit_rows = [(
        x["record_id"], x["stage"], x["kind"], x["entity_key"], x["status"],
        "; ".join(x["artifact_ids"]) or "NONE",
        json.dumps(x["data"], ensure_ascii=False, sort_keys=True)[:6000],
    ) for x in records]
    issue_rows = [(issue["type"], issue["object_id"],
                   issue["reason"], issue["action"]) for issue in issues]

    collected = {
        "客户总表": org_rows, "联系人路线": contact_rows, "排除与待确认": issue_rows,
        "证据索引": evidence_rows, "本轮说明": notes,
        "场地与资产": venue_rows, "人员岗位": people_rows,
        "来源与查询": source_rows, "候选数据审计": audit_rows,
    }
    sheets = [_table(name, collected[name]) for name, _ in TABLES]
    return sheets, {
        "run_id": run_id, "scope": run["scope"], "rule_version": run["rule_version"],
        "status": "PARTIAL_CANDIDATE_ONLY_NOT_FORMAL_CONTACTS",
        "counts": {name: len(collected[name]) for name, _ in TABLES},
        "input_record_count": len(records), "active_record_count": len(effective),
        "version_conflict_count": len(version_conflicts),
        "saved_artifact_count": len(artifacts),
        "evidence_hash_readback": "ALL_OK", "business_verification": "NOT_PERFORMED",
        "send_authorized": False,
        "evidence_assessment": audit,
        "evidence_ready_for_business_use": False,
        "client_business_tables_admitted_only": True,
        "admission": admission,
        "admission_status": ("ALL_CLIENT_ROWS_ADMITTED"
                             if admission["rejected_record_count"] == 0
                             and admission["version_conflict_record_count"] == 0
                             else "HELD_ROWS_EXCLUDED_FROM_CLIENT_TABLES"),
        "goal_assessment": goal,
        "evidence_link_count": len(links),
        "evidence_spanless_record_count": len(spanless),
        "evidence_envelopes": envelopes,
        "evidence_envelope_export_gate": envelope_gate,
        "evidence_envelope_missing_artifacts": envelopeless,
    }

def _readback_xlsx(path: Path, sheets: list[Sheet]) -> None:
    """Read independent disk ZIP/XML; ensure every sheet has the same row/column cells."""
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError("XLSX_ARCHIVE_CORRUPT")
        for idx, sheet in enumerate(sheets, start=1):
            xml = ET.fromstring(archive.read(f"xl/worksheets/sheet{idx}.xml"))
            rows = xml.findall(".//x:sheetData/x:row", NAMESPACE)
            if len(rows) != len(sheet.rows) + 1:
                raise ValueError(f"XLSX_ROW_COUNT_MISMATCH:{sheet.name}")
            for row in rows:
                if len(row.findall("x:c", NAMESPACE)) != len(sheet.headers):
                    raise ValueError(f"XLSX_COLUMN_COUNT_MISMATCH:{sheet.name}")
            header_cells = rows[0].findall("x:c", NAMESPACE)
            observed = [c.findtext(".//x:t", "", NAMESPACE) for c in header_cells]
            if observed != list(sheet.headers):
                raise ValueError(f"XLSX_HEADER_MISMATCH:{sheet.name}")


def write_delivery(data_root: Path | str | None, run_id: str, delivery_id: str) -> dict:
    root = _root(data_root)
    store._id(run_id, "run_id")
    store._id(delivery_id, "delivery_id")
    sheets, manifest = build_delivery(root, run_id)
    destination = root / "exports" / run_id / delivery_id
    if destination.exists():
        raise FileExistsError("DELIVERY_ID_ALREADY_EXISTS: exports are immutable")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".delivery-", dir=parent))
    try:
        xlsx = staging / "Research_Result.xlsx"
        xlsx.write_bytes(render_workbook(sheets))
        _readback_xlsx(xlsx, sheets)
        files = [xlsx]
        for sheet in sheets:
            filepath = staging / f"{sheet.name}.csv"
            with filepath.open("x", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(sheet.headers)
                writer.writerows(sheet.rows)
            with filepath.open(encoding="utf-8-sig", newline="") as handle:
                if sum(1 for _ in csv.reader(handle)) != len(sheet.rows) + 1:
                    raise ValueError(f"CSV_READBACK_COUNT_MISMATCH:{sheet.name}")
            files.append(filepath)
        receipt = {**manifest, "format": "xlsx+csv+manifest",
                   "generated_at": datetime.now(timezone.utc).isoformat(),
                   "files": {f.name: {
                       "bytes": f.stat().st_size, "sha256": hashlib.sha256(f.read_bytes()).hexdigest()
                   } for f in files}}
        (staging / "MANIFEST.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.rename(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    append_event(root, run_id=run_id, operation_id=delivery_id,
                 stage="export", kind="export", status="succeeded",
                 provider="local", receipt_ref=delivery_id)
    # The workbook is published; now persist the structural goal gaps read from the
    # very same snapshot, and only then refresh the collectable summary.
    try:
        gap_record = workflow.record_goal_gaps(root, run_id, manifest["goal_assessment"])
    except Exception as exc:
        gap_record = {"status": "FAILED", "reason_code": type(exc).__name__, "detail": str(exc)}
    # Store semantic-evidence gaps in the same append-only journal, so the
    # user's single experience file reflects failed research even when XLSX
    # serialization succeeded. Only short reason codes/counts; no PII or URLs.
    evidence_note = {"status": "NO_GAPS"}
    evidence_codes = manifest["evidence_assessment"]["gap_counts"]
    if evidence_codes:
        try:
            short_codes = ",".join(list(sorted(evidence_codes))[:8])
            evidence_note = append_note(
                root, run_id=run_id,
                note_id="evgap-" + manifest["goal_assessment"]["snapshot_id"][:24],
                stage="review", category="evidence_missing",
                problem="Candidate facts lack complete source excerpts or original rendering support",
                action_taken="Retained candidate status and listed evidence holds in workbook review",
                observed_result="gap_types=" + str(len(evidence_codes)) +
                                " codes=" + short_codes,
                hypothesis="", evidence_ref="snapshot-" + manifest["goal_assessment"]["snapshot_id"][:24])
        except Exception as exc:
            evidence_note = {"status": "FAILED", "reason_code": type(exc).__name__}
    refresh = refresh_summary(data_root=root)
    # Two artifacts, two honest reports: a workbook that was really written is never
    # retracted, but neither is a partial delivery dressed up as a complete one.
    partial_reasons = []
    if gap_record.get("status") == "FAILED":
        partial_reasons.append("GOAL_GAP_RECORD_FAILED")
    if evidence_note.get("status") == "FAILED":
        partial_reasons.append("EVIDENCE_NOTE_FAILED")
    if refresh.get("status") != "REFRESHED":
        partial_reasons.append("EXPERIENCE_REFRESH_"
                               + str(refresh.get("reason_code") or refresh.get("status")))
    return {**receipt, "directory": str(destination),
            "xlsx_path": str(destination / "Research_Result.xlsx"),
            "goal_gap_record": gap_record, "evidence_note": evidence_note,
            "experience_file": refresh.get("experience_file"),
            "experience_refresh": refresh,
            "delivery_completeness": "COMPLETE" if not partial_reasons else "PARTIAL",
            "partial_reasons": partial_reasons}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--delivery-id", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(write_delivery(args.data_root, args.run_id, args.delivery_id),
                         ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Evidence workbook: sources -> claims -> evidence -> ledger receipts -> review
# ---------------------------------------------------------------------------
#: The five sheets this workbook must have, in order. Deliberately in English and
#: deliberately not the nine business sheets above: this is the evidence chain a
#: reviewer or an auditor reads, not the candidate spreadsheet a salesperson reads.
EVIDENCE_TABLES = (
    ("sources", ("source_id", "artifact_id", "title", "url", "retrieved_at",
                 "material_kind", "content_sha256", "origin_provider")),
    ("claims", ("claim_id", "candidate_id", "entity_key", "field", "value",
                "source_id", "span_sha256", "support", "final_status")),
    ("evidence", ("evidence_id", "run_id", "operation_id", "ledger_id", "provider",
                  "capability_id", "raw_artifact_ref", "content_sha256",
                  "verified_state", "candidate_only", "exportable", "caveats")),
    ("ledger_receipts", ("ledger_id", "operation_id", "provider", "capability_id",
                         "charge_state", "reserved_usd", "estimated_usd", "actual_usd",
                         "actual_source", "upstream_ref", "counts_against_caps")),
    ("review", ("candidate_id", "claim_id", "decision", "reviewer_minutes",
                "decided_at", "note")),
)

#: Review states a claim can reach. Only `APPROVED` says a human accepted it, and it
#: is reachable only through a recorded decision — never by default, never by export.
CLAIM_STATES = frozenset({"APPROVED", "REJECTED", "BLOCKED_NOT_REVIEWED",
                          "BLOCKED_UNSUPPORTED"})


def _micros_to_usd(value):
    """Integer micro-USD to a decimal string, or `None` when nothing is known."""
    if value is None:
        return None
    from decimal import Decimal, ROUND_HALF_UP
    return str((Decimal(int(value)) / Decimal(1_000_000)).quantize(
        Decimal("0.000001"), ROUND_HALF_UP))


def _evidence_rows(root: Path, run_id: str, snapshot, links, envelopes, reviews):
    """The five sheet bodies, computed from stored rows and never from a claim."""
    records, artifacts = snapshot["records"], snapshot["artifacts"]
    title_by_artifact = {}
    for record in records:
        if record["kind"] == "source":
            title = str(record["data"].get("title") or "").strip()
            for artifact_id in record["artifact_ids"]:
                title_by_artifact.setdefault(artifact_id, title)

    sources = [(
        next((r["record_id"] for r in records
              if r["kind"] == "source" and artifact["artifact_id"] in r["artifact_ids"]),
             artifact["artifact_id"]),
        artifact["artifact_id"],
        title_by_artifact.get(artifact["artifact_id"]) or "UNKNOWN",
        artifact["url"],
        artifact["acquired_at"],
        artifact["kind"],
        artifact["sha256"],
        artifact["provider"],
    ) for artifact in artifacts]

    envelopes_by_artifact = {}
    for envelope in envelopes:
        envelopes_by_artifact.setdefault(envelope["raw_artifact_ref"], []).append(envelope)

    claims, review_rows, blocked = [], [], 0
    decisions = {(r["candidate_id"], r.get("claim_id")): r for r in reviews}
    for record in records:
        for claim in record["data"].get("claims") or []:
            artifact_id = str(claim.get("artifact_id", ""))
            span = str(claim.get("span_sha256", ""))
            matching = [link for link in links
                        if link["record_id"] == record["record_id"]
                        and link["artifact_id"] == artifact_id
                        and link["span_sha256"] == span]
            linked = [e for e in envelopes_by_artifact.get(artifact_id, [])
                      if e.get("ledger_id")]
            supported = bool(matching) and bool(linked)
            decision = decisions.get((record["record_id"], claim.get("claim_id")))
            if not supported:
                status = "BLOCKED_UNSUPPORTED"
            elif decision is None:
                status = "BLOCKED_NOT_REVIEWED"
            elif decision.get("decision") == "ACCEPT":
                status = "APPROVED"
            else:
                status = "REJECTED"
            if status.startswith("BLOCKED"):
                blocked += 1
            claims.append((
                claim.get("claim_id", ""), record["record_id"], record["entity_key"],
                claim.get("field", ""), claim.get("value", ""),
                next((s[0] for s in sources if s[1] == artifact_id), "UNKNOWN"),
                span, "SUPPORTED" if supported else "UNSUPPORTED", status))
    for decision in reviews:
        review_rows.append((decision.get("candidate_id", ""), decision.get("claim_id", ""),
                            decision.get("decision", ""), decision.get("reviewer_minutes", ""),
                            decision.get("decided_at", ""), decision.get("note", "")))

    evidence = [(
        envelope["evidence_id"], envelope["run_id"], envelope["operation_id"],
        envelope["ledger_id"], envelope["provider"], envelope["capability_id"],
        envelope["raw_artifact_ref"], envelope["content_sha256"],
        envelope["verified_state"], bool(envelope["candidate_only"]),
        bool(envelope["ledger_id"]) and bool(envelope["candidate_only"]),
        "; ".join(envelope["caveats"]),
    ) for envelope in envelopes]

    return sources, claims, evidence, review_rows, blocked


def write_evidence_workbook(data_root: Path | str | None, run_id: str, delivery_id: str, *,
                            ledger, project: str, reviews=()) -> dict:
    """Write the evidence chain as one XLSX: sources, claims, evidence, receipts, review.

    Three rules this export will not break:

    * **Envelope ids are read, never rebuilt.** The `evidence` sheet copies
      `evidence_id` and `ledger_id` from the rows `research_evidence_store` recorded.
      Deriving them again at export time would let the workbook describe a link that
      was never written.
    * **A claim is final only after a recorded human decision.** `APPROVED` is
      reachable only through a review row; the default for a supported claim is
      `BLOCKED_NOT_REVIEWED`, and an unsupported one is `BLOCKED_UNSUPPORTED`
      regardless of what any reviewer says.
    * **The ledger's own states are copied verbatim.** An `UNKNOWN` charge stays
      `UNKNOWN` with its `actual_usd` empty; nothing here sums or promotes a state, so
      an unreconciled call can never read as a settled one.

    `ledger` is duck-typed on `operations(project=..., run_id=...)`; it is B's
    `UsageLedger` in practice and a mapping-shaped stand-in in tests.
    """
    root = _root(data_root)
    import research_usage_ledger as usage_ledger
    store._id(run_id, "run_id")
    store._id(delivery_id, "delivery_id")
    snapshot = _read_snapshot(root, run_id)
    links, envelopes = provenance.evidence_links(root, run_id), \
        provenance.stored_envelopes(root, run_id)
    run, records, artifacts = snapshot
    sources, claims, evidence, review_rows, blocked = _evidence_rows(
        root, run_id, {"records": records, "artifacts": artifacts},
        links, envelopes, list(reviews))
    receipts = []
    for operation in ledger.operations(project=project, run_id=run_id):
        display_amount = usage_ledger.display_actual_micros(operation)
        historical_unknown = display_amount is None and operation.actual_micros is not None
        receipts.append((
            operation.operation_id, operation.operation_id, operation.provider,
            operation.capability, operation.charge_state,
            _micros_to_usd(operation.reserved_micros),
            _micros_to_usd(operation.estimated_micros),
            "UNKNOWN" if historical_unknown else _micros_to_usd(display_amount),
            "UNKNOWN" if historical_unknown else operation.actual_source, operation.upstream_ref,
            bool(operation.counts_against_caps)))

    collected = {"sources": sources, "claims": claims, "evidence": evidence,
                 "ledger_receipts": receipts, "review": review_rows}
    sheets = []
    for name, fields in EVIDENCE_TABLES:
        rows = collected[name]
        safe = tuple(tuple(_literal(v) if isinstance(v, str) else ("" if v is None else v)
                           for v in row) for row in rows)
        if any(len(row) != len(fields) for row in safe):
            raise ValueError(f"COLUMN_MISMATCH:{name}")
        sheets.append(Sheet(name, tuple(fields), safe,
                            widths=tuple(min(48.0, max(16.0, len(c) * 2.2))
                                         for c in fields)))

    destination = root / "exports" / run_id / delivery_id
    if destination.exists():
        raise FileExistsError("DELIVERY_ID_ALREADY_EXISTS: exports are immutable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".evidence-", dir=destination.parent))
    try:
        workbook = staging / "Evidence_Chain.xlsx"
        workbook.write_bytes(render_workbook(sheets))
        _readback_xlsx(workbook, sheets)
        files = [workbook]
        for sheet in sheets:
            path = staging / f"{sheet.name}.csv"
            with path.open("x", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(sheet.headers)
                writer.writerows(sheet.rows)
            files.append(path)
        manifest = {
            "schema": "marketops.quality.evidence-workbook.v1",
            "run_id": run_id, "project": project,
            "status": "EVIDENCE_CHAIN_CANDIDATE_ONLY",
            "sheets": {name: len(collected[name]) for name, _ in EVIDENCE_TABLES},
            "claims_blocked": blocked,
            "claims_approved": sum(1 for c in claims if c[-1] == "APPROVED"),
            "evidence_exportable": sum(1 for e in evidence if e[10]),
            "ledger_states": {state: sum(1 for r in receipts if r[4] == state)
                              for state in sorted({r[4] for r in receipts})},
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "files": {},
        }
        manifest["files"] = {f.name: {"bytes": f.stat().st_size,
                                      "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                             for f in files}
        (staging / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.rename(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**manifest, "directory": str(destination), "xlsx_path": str(
        destination / "Evidence_Chain.xlsx")}

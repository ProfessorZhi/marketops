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

VERSION = "0.5.5-plus-xlsx-candidate"
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
FORMULA = ("=", "+", "-", "@")
NAMESPACE = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ROW_LIMIT = 20_000
MAX_MATERIAL = 4_000_000

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
    organizations = by_kind["organization"]
    org_by_key: dict[str, dict] = {}
    issues: list[tuple] = [
        ("候选版本冲突", item["entity_key"],
         "同类实体存在未能形成唯一 supersedes 链的版本：" +
         ", ".join(item["record_ids"][:8]),
         "先追加具名更正或人工核对；冲突版本不进入主业务表")
        for item in version_conflicts
    ]
    # Missing excerpts and mismatched source links are review items, not
    # automatically certified venue types, ownership or hard exclusions.
    for sample in audit_all["samples"]:
        issues.append(("来源声明未核实", sample["record_id"],
                       ", ".join(sample["reason_codes"]),
                       "回到同一资料的具体引文；禁止据附件ID本身晋升事实"))
    spanless = [record for record in effective
                if record["kind"] in evidence.CHECK_KINDS
                and record["artifact_ids"] and not links_by_record.get(record["record_id"])]
    for record in spanless:
        issues.append(("来源跨度未记录", record["record_id"],
                       "候选引用了资料但未记录读取跨度：" + "; ".join(record["artifact_ids"][:8]),
                       "回到同一资料的具名片段；仅有附件级支持的候选不进入业务结论"))

    for org in organizations:
        key = org["entity_key"]
        if key in org_by_key:
            issues.append(("组织合并冲突", key, "同一组织实体键出现多条候选记录，禁止静默覆盖", "人工核实组织及版本"))
        else:
            org_by_key[key] = org

    venue_counts = Counter()
    for venue in by_kind["venue"]:
        org_key = str(venue["data"].get("organization_id", "")).strip()
        if org_key:
            venue_counts[org_key] += 1
            if org_key not in org_by_key:
                issues.append(("关联缺失", venue["record_id"], "场地引用的组织不存在：" + org_key,
                               "补充核实组织，不要推断运营者"))

    contacts = by_kind["contact_point"]
    contact_counts = Counter()
    contact_rows: list[tuple] = []
    for candidate in contacts:
        fact = candidate["data"]
        org_key = str(fact.get("organization_id", "")).strip()
        if org_key:
            contact_counts[org_key] += 1
            if org_key not in org_by_key:
                issues.append(("关联缺失", candidate["record_id"], "联系渠道未对应已记录组织", "检查归属"))
        channel = str(fact.get("type", fact.get("channel", ""))).strip().lower()
        value = str(fact.get("value", fact.get("email", ""))).strip()
        if not candidate["artifact_ids"]:
            validation = "HOLD_NO_SOURCE"
        elif channel == "email" and not EMAIL.fullmatch(value):
            validation = "HOLD_INVALID_EMAIL"
        elif not value:
            validation = "HOLD_EMPTY_CONTACT"
        else:
            supported = any(value.casefold() in assets[i]["text"].casefold()
                            for i in candidate["artifact_ids"])
            validation = "LITERAL_IN_SAVED_MATERIAL" if supported else "HOLD_NOT_LITERAL"
            if supported and channel == "email":
                single = evidence.audit_records([candidate], assets, result_limit=1)
                problems = single["gap_counts"]
                if "EMAIL_NOT_IN_PRIMARY_MATERIAL" in problems:
                    validation = "HOLD_EMAIL_NOT_IN_PRIMARY_MATERIAL"
                elif "NO_EXCERPT" in problems or "CLAIMS_INVALID" in problems:
                    validation = "HOLD_EMAIL_NO_SOURCE_QUOTE"
                elif "EXCERPT_NOT_FOUND" in problems:
                    validation = "HOLD_EMAIL_QUOTE_MISMATCH"
                else:
                    original = any(assets[i]["kind"] in {"rendered_html", "raw_response"}
                                   and value.casefold() in assets[i]["text"].casefold()
                                   for i in candidate["artifact_ids"])
                    validation = ("RENDERED_EMAIL_LITERAL_NOT_BUSINESS_VERIFIED" if original else
                                  "VENDOR_HTML_EMAIL_LITERAL_NOT_BUSINESS_VERIFIED")
        # A literal in a provider-extracted page is useful source support, not formal SOP grading.
        if validation.startswith("HOLD"):
            issues.append(("联系渠道证据", candidate["record_id"], validation,
                           "读取原始来源并补正/排除，不得晋升正式联系人"))
        contact_rows.append((
            candidate["record_id"], org_key or "UNKNOWN",
            _plain(fact.get("person_name", fact.get("department", ""))),
            _plain(fact.get("role_function", fact.get("role_title", ""))),
            channel or "UNKNOWN", value or "UNKNOWN", validation,
            "RULES_UNKNOWN", "CANDIDATE_NOT_APPROVED", "; ".join(candidate["artifact_ids"]) or "NONE",
        ))
    for route in by_kind["contact_route"]:
        # The Lite MCP can only persist *proposals*. No text or grade is formal.
        issues.append(("联系路线待审核", route["record_id"],
                       "Lite 暂存路线不能执行正式 A/B/C/D 晋升",
                       "通过授权 MarketOps SOP/人工核验系统定级"))

    org_rows = []
    for org in organizations:
        key, fact = org["entity_key"], org["data"]
        org_rows.append((
            key, _plain(fact.get("name")), run["scope"], "CANDIDATE_NOT_APPROVED",
            venue_counts[key], contact_counts[key],
            "; ".join(org["artifact_ids"]) or "NONE",
            "REVIEW_REQUIRED" if any(v[1] == key for v in issues) else "PENDING_SOP_QUALIFICATION",
        ))
        if not org["artifact_ids"]:
            issues.append(("组织证据", org["record_id"], "没有可回读的原始资料引用",
                           "补充组织身份来源"))

    venue_rows = [(
        x["entity_key"], _plain(x["data"].get("name")),
        _plain(x["data"].get("organization_id")),
        "CANDIDATE_NOT_APPROVED", "; ".join(x["artifact_ids"]) or "NONE",
    ) for x in by_kind["venue"]]

    people_rows = []
    for item in by_kind["person"] + by_kind["role"]:
        facts = item["data"]
        org = str(facts.get("organization_id", "")).strip()
        if org and org not in org_by_key:
            issues.append(("岗位组织关联", item["record_id"], "任职或部门指向未知组织", "补充组织证据"))
        if not item["artifact_ids"]:
            issues.append(("岗位证据", item["record_id"], "缺少可回读资料引用", "先获取原始岗位证据"))
        people_rows.append((
            item["record_id"], item["kind"],
            _plain(facts.get("name", facts.get("department", ""))),
            _plain(facts.get("role_title", facts.get("role_function", ""))),
            org or "UNKNOWN", "CANDIDATE_NOT_VERIFIED",
            "; ".join(item["artifact_ids"]) or "NONE"
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

    for other in by_kind["review"]:
        issues.append(("人工复核", other["record_id"],
                       _plain(other["data"].get("reason")), "人工决策并记录审批来源"))
    if not org_rows and not venue_rows:
        issues.append(("业务对象缺失", run_id, "缺少组织和场地候选", "继续在限定范围内研究"))
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

    collected = {
        "客户总表": org_rows, "联系人路线": contact_rows, "排除与待确认": issues,
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

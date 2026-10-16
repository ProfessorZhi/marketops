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
from research_event_journal import _root, append_event
from _xlsx import Sheet, render_workbook

VERSION = "0.4.0-plus-xlsx-candidate"
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
                 "取得时间", "字节数", "SHA-256", "引用状态")),
    ("本轮说明", ("字段", "值")),
    ("场地与资产", ("场地ID", "名称", "关联组织ID", "研究状态", "资料引用")),
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


def build_delivery(root: Path, run_id: str) -> tuple[list[Sheet], dict]:
    run, records, artifacts = _read_snapshot(root, run_id)
    if not records or not artifacts:
        raise ValueError("NO_RESEARCH_ROWS_OR_SOURCE_ARTIFACTS: not a successful E2E")
    assets = {x["artifact_id"]: x for x in artifacts}
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_kind[record["kind"]].append(record)
    organizations = by_kind["organization"]
    org_by_key: dict[str, dict] = {}
    issues: list[tuple] = []
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

    source_rows = [(
        x["record_id"], _plain(x["data"].get("title")),
        _plain(x["data"].get("url")), _plain(x["data"].get("provider")),
        x["status"],
    ) for x in by_kind["source"]]

    evidence_rows = [(
        a["artifact_id"], a["url"], a["provider"], a["kind"],
        a["acquired_at"], a["bytes"], a["sha256"], "READBACK_HASH_OK_NOT_FACT_VERIFIED"
    ) for a in artifacts]

    for other in by_kind["review"]:
        issues.append(("人工复核", other["record_id"],
                       _plain(other["data"].get("reason")), "人工决策并记录审批来源"))
    if not org_rows and not venue_rows:
        issues.append(("业务对象缺失", run_id, "缺少组织和场地候选", "继续在限定范围内研究"))
    notes = [
        ("Run", run_id),
        ("研究范围", run["scope"]),
        ("原始任务", run["objective"]),
        ("SOP/规则版本", run["rule_version"]),
        ("研究状态", "PARTIAL_CANDIDATE_ONLY"),
        ("业务正式审查", "NOT_PERFORMED"),
        ("实际组织候选数", len(organizations)),
        ("实际场地候选数", len(venue_rows)),
        ("实际联系渠道候选数", len(contact_rows)),
        ("实际原始/派生资料数", len(evidence_rows)),
        ("待核查事项数", len(issues)),
        ("市场总覆盖率", "UNKNOWN_NO_TRUSTWORTHY_DENOMINATOR"),
        ("收费 Provider 额度与价格", "UNKNOWN_NOT_IN_LITE_STORE"),
        ("联系路线评级", "RULES_UNKNOWN; no A/B/C/D assigned by Agent"),
        ("发送授权", "FALSE: research-only; no outreach"),
    ]
    audit_rows = [(
        x["record_id"], x["stage"], x["kind"], x["entity_key"], x["status"],
        "; ".join(x["artifact_ids"]) or "NONE",
        json.dumps(x["data"], ensure_ascii=False, sort_keys=True)[:6000],
    ) for x in records]

    collected = {
        "客户总表": org_rows, "联系人路线": contact_rows, "排除与待确认": issues,
        "证据索引": evidence_rows, "本轮说明": notes,
        "场地与资产": venue_rows, "来源与查询": source_rows, "候选数据审计": audit_rows,
    }
    sheets = [_table(name, collected[name]) for name, _ in TABLES]
    return sheets, {
        "run_id": run_id, "scope": run["scope"], "rule_version": run["rule_version"],
        "status": "PARTIAL_CANDIDATE_ONLY_NOT_FORMAL_CONTACTS",
        "counts": {name: len(collected[name]) for name, _ in TABLES},
        "input_record_count": len(records), "saved_artifact_count": len(artifacts),
        "evidence_hash_readback": "ALL_OK", "business_verification": "NOT_PERFORMED",
        "send_authorized": False,
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
    return {**receipt, "directory": str(destination),
            "xlsx_path": str(destination / "Research_Result.xlsx")}


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

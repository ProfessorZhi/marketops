#!/usr/bin/env python3
"""Validate/render candidate experience packs offline. Never calls models or changes policy."""
from __future__ import annotations

import argparse
import html
import json
import math
import re
from pathlib import Path

VERSION = "marketops.experience.v1"
CATEGORIES = {
    "scope_policy", "source_discovery", "access_blocked", "auth_budget", "provider_failure",
    "extraction", "entity_binding", "schema_drift", "storage_handoff", "evidence_quality",
    "client_environment", "review_pending", "export_quality", "successful_method", "unknown",
}
STAGES = {"intake", "discovery", "acquisition", "entities", "qualification", "people", "routes", "export", "handoff"}
REF_TYPES = {"runtime_receipt", "artifact", "human_feedback", "historical_analysis", "test_result", "synthetic"}


def validate(data: object) -> dict:
    """Shape/reference gates only: do not attest to factual, causal or privacy correctness."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return {"status": "INVALID", "errors": ["root must be an object"], "business_verified": False}

    def need(obj: dict, keys: tuple[str, ...], where: str) -> None:
        for key in keys:
            if not isinstance(obj.get(key), str) or not obj[key].strip():
                errors.append(f"{where}.{key}: nonempty string required")

    def refs(value: object, known: set[str], where: str, required: bool = True) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            errors.append(f"{where}: list of reference strings required")
            return []
        if required and not value:
            errors.append(f"{where}: empty evidence is not a pass")
        if len(value) != len(set(value)):
            errors.append(f"{where}: duplicate references")
        for ref in value:
            if ref not in known:
                errors.append(f"{where}: unknown reference {ref!r}")
        return value

    def records(key: str, id_key: str) -> dict[str, dict]:
        value = data.get(key)
        if not isinstance(value, list):
            errors.append(f"{key}: array required")
            return {}
        result = {}
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                errors.append(f"{key}[{index}]: object required")
                continue
            ident = item.get(id_key)
            if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}", ident):
                errors.append(f"{key}[{index}]: invalid {id_key}")
                continue
            if ident in result:
                errors.append(f"{key}: duplicate {ident}")
            result[ident] = item
        return result

    if data.get("schema_version") != VERSION:
        errors.append("unsupported schema_version")
    if data.get("promotion") != "candidate_only":
        errors.append("promotion must stay candidate_only; approval is external")
    need(data, ("run_id", "scope", "store_ref", "rule_version", "skill_version"), "pack")
    if data.get("privacy") != "sanitized_candidate":
        errors.append("privacy must declare sanitized_candidate, not a privacy certification")
    evidence = records("evidence_manifest", "ref")
    incidents = records("incidents", "id")
    lessons = records("lessons", "id")
    known = set(evidence)
    for key, record in evidence.items():
        need(record, ("locator",), f"evidence.{key}")
        if record.get("kind") not in REF_TYPES:
            errors.append(f"evidence.{key}: unknown kind")
        digest = record.get("sha256")
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            errors.append(f"evidence.{key}: sha256 must be lowercase 64 hex or null")
    for key, item in incidents.items():
        where = f"incident.{key}"
        need(item, ("observation", "expected", "operation_ref", "impact", "next_action"), where)
        if item.get("stage") not in STAGES or item.get("category") not in CATEGORIES:
            errors.append(f"{where}: unknown stage/category")
        refs(item.get("evidence_refs"), known, where + ".evidence_refs")
        validations = refs(item.get("validation_refs", []), known, where + ".validation_refs", False)
        cause = item.get("root_cause_status")
        if cause not in {"unknown", "hypothesis", "confirmed"}:
            errors.append(f"{where}: invalid root_cause_status")
        if cause in {"hypothesis", "confirmed"}:
            need(item, ("hypothesis",), where)
        if cause == "confirmed" and not validations:
            errors.append(f"{where}: confirmed cause requires validation references")
    for key, item in lessons.items():
        where = f"lesson.{key}"
        need(item, ("when", "do", "avoid", "scope", "limitations", "proposed_change", "rollback"), where)
        refs(item.get("incident_ids"), set(incidents), where + ".incident_ids")
        refs(item.get("evidence_refs"), known, where + ".evidence_refs")
        refs(item.get("counterexample_refs", []), known, where + ".counterexample_refs", False)
        state = item.get("status")
        if state not in {"draft", "tested", "rejected"}:
            errors.append(f"{where}: only draft/tested/rejected allowed; no auto-approved state")
        replay = item.get("replay")
        if not isinstance(replay, dict):
            errors.append(f"{where}: replay object required")
            continue
        need(replay, ("baseline", "change", "acceptance", "status"), where + ".replay")
        if replay.get("status") not in {"not_run", "passed", "failed"}:
            errors.append(f"{where}: invalid replay status")
        train = refs(replay.get("training_cases", []), known, where + ".training_cases", False)
        holdout = refs(replay.get("holdout_cases", []), known, where + ".holdout_cases", False)
        results = refs(replay.get("result_refs", []), known, where + ".result_refs", False)
        if set(train) & set(holdout):
            errors.append(f"{where}: training/holdout case leakage")
        if replay.get("status") in {"passed", "failed"} and (not holdout or not results):
            errors.append(f"{where}: completed replay needs holdout and results")
        if state == "tested" and replay.get("status") != "passed":
            errors.append(f"{where}: tested requires a passed replay (not approval)")
    metrics = data.get("metrics", [])
    if not isinstance(metrics, list):
        errors.append("metrics must be an array")
        metrics = []
    for index, metric in enumerate(metrics):
        where = f"metrics[{index}]"
        if not isinstance(metric, dict):
            errors.append(f"{where}: object required")
            continue
        need(metric, ("name", "unit", "population"), where)
        for key in ("value", "denominator"):
            value = metric.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                errors.append(f"{where}.{key}: nonnegative finite number or null required")
        if metric.get("denominator") == 0:
            errors.append(f"{where}: ratio over zero denominator is not measurable")
        if metric.get("value") is not None:
            refs(metric.get("evidence_refs"), known, where + ".evidence_refs")
    return {
        "status": "INVALID" if errors else ("EMPTY_NOT_EVALUATED" if not incidents else "VALID_CANDIDATE_NOT_VERIFIED"),
        "errors": errors, "incidents": len(incidents), "lessons": len(lessons),
        "business_verified": False, "cause_verified": False, "privacy_verified": False,
        "note": "Only structure and declared references checked; no materials fetched, tests executed, policy approved or uploads made.",
    }


def render(data: dict) -> str:
    """Escape raw HTML/Markdown so untrusted observations do not become active links."""
    text = lambda obj: html.escape(str(obj)).replace("\n", " ").replace("[", "&#91;").replace("]", "&#93;")
    lines = ["# Research experience — CANDIDATE ONLY", "", f"Run: {text(data['run_id'])}",
             f"Scope: {text(data['scope'])}", "", "This is not policy approval or verified causal evidence.", ""]
    for item in data["incidents"]:
        lines += [f"## {text(item['id'])}: {text(item['category'])}",
                  f"Expected: {text(item['expected'])}", f"Observation: {text(item['observation'])}",
                  f"Evidence refs: {text(item['evidence_refs'])}", f"Impact: {text(item['impact'])}",
                  f"Hypothesis: {text(item.get('hypothesis', 'unknown'))}",
                  f"Cause status: {text(item['root_cause_status'])}", f"Next: {text(item['next_action'])}", ""]
    for item in data["lessons"]:
        lines += [f"## Lesson {text(item['id'])} — {text(item['status'])}",
                  f"When: {text(item['when'])}", f"Do: {text(item['do'])}",
                  f"Avoid: {text(item['avoid'])}", f"Evidence refs: {text(item['evidence_refs'])}",
                  f"Limits: {text(item['limitations'])}", f"Replay: {text(item['replay']['status'])}",
                  f"Proposed change: {text(item['proposed_change'])}", f"Rollback: {text(item['rollback'])}", ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "render"))
    parser.add_argument("file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        source = args.file.expanduser()
        if source.stat().st_size > 2_000_000:
            raise ValueError("experience pack exceeds 2 MB limit; use references, not full transcripts")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result
        data = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=unique)
        result = validate(data)
        if result["errors"]:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        if args.command == "render":
            if args.output is None:
                raise ValueError("render requires --output to a new customer-owned file")
            with args.output.expanduser().open("x", encoding="utf-8") as handle:
                handle.write(render(data))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RecursionError):
        print(json.dumps({"status": "INPUT_ERROR", "detail": "Unreadable/malformed input, missing output parent, or output exists. No existing file was overwritten."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

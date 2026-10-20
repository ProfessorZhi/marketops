#!/usr/bin/env python3
"""One bounded research step over the existing managed gateway.

This module owns selection and a replayable chain receipt, not permissions,
provider dispatch, retries or money. The caller injects deployment-owned
admission and managed_call; neither is exposed as an MCP argument.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

import research_event_journal as journal
import research_lite_store as store

ROUTES = {
    "source_discovery": ("serper.search", "serpapi.search", "tinyfish.search"),
    "known_public_url": ("crawl4ai.fetch_page", "tinyfish.fetch_content"),
    "batch_public_pages": ("crawl4ai.fetch_page", "tinyfish.fetch_content"),
    "semantic_choice": ("jev.choice",),
}
SKIPPABLE = frozenset({"CAPABILITY_NOT_AVAILABLE", "CAPABILITY_SCHEMA_DRIFT",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "LOCAL_DEPENDENCY_NOT_INSTALLED",
    "PROVIDER_CREDENTIAL_MISSING", "POLICY_TIER_NOT_GRANTED", "BUDGET_PROVIDER_CEILING"})
SOURCE_STOPS = frozenset({"POLICY_DOMAIN_NOT_ALLOWED", "EGRESS_REFUSED", "HOST_PREVIOUSLY_BLOCKED",
    "HOST_PREVIOUSLY_BLOCKED_SAME_RUN", "SOURCE_CHALLENGE_PAGE", "SOURCE_ACCESS_BLOCKED",
    "CRAWL_REDIRECT_REFUSED", "TARGET_RESOLUTION_FAILED", "TARGET_RESOLVES_NON_GLOBAL",
    "TARGET_RESOLVES_TO_MIXED_ADDRESSES", "OFFICIAL_MCP_TARGET_NOT_ALLOWED"})
SHARED_STOPS = frozenset({"BUDGET_RUN_CEILING", "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING",
    "RESEARCH_NETWORK_DISABLED", "POLICY_GRANT_EXPIRED", "POLICY_GRANT_REVOKED",
    "GRANT_SIGNATURE_INVALID", "PRICE_UNKNOWN_FAIL_CLOSED", "PRICING_SOURCE_MISSING",
    "WALLET_PRECONDITION_FAILED", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "CAPABILITY_PARAM_NOT_ALLOWED", "CAPABILITY_UNKNOWN"})
TECHNICAL = frozenset({"LOCAL_CRAWL_RUNTIME_FAILED", "LOCAL_CRAWL_FAILED", "SOURCE_EMPTY_EXTRACT",
    "UPSTREAM_MCP_EMPTY_RESULT", "UPSTREAM_MCP_TOOL_FAILED", "SERPER_HTTP_ERROR",
    "SERPER_UPSTREAM_ERROR", "SERPAPI_UPSTREAM_FAILED", "SERPAPI_DECLARED_ERROR"})
MAX_BYTES = 4_000_000
#: Capabilities executed on this machine with no provider account behind them.
LOCAL_CAPABILITIES = frozenset({"crawl4ai.fetch_page"})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _stamp():
    return datetime.now(timezone.utc).isoformat()


def _code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", value) else ""


def _db(root):
    path = (root / "state/research-events.sqlite3").resolve()
    if not path.is_relative_to(root):
        raise ValueError("FALLBACK_STATE_PATH_OUTSIDE_ROOT")
    db = journal._connect(root)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS research_fallback_chains (
            run_id TEXT NOT NULL, chain_id TEXT NOT NULL, request_sha256 TEXT NOT NULL,
            state TEXT NOT NULL, receipt_json TEXT, created_at TEXT NOT NULL,
            PRIMARY KEY(run_id,chain_id));
        CREATE TABLE IF NOT EXISTS research_fallback_attempts (
            run_id TEXT NOT NULL, chain_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
            operation_id TEXT NOT NULL, receipt_json TEXT NOT NULL,
            PRIMARY KEY(run_id,chain_id,ordinal));
    """)
    return db


def _attempts(db, run_id, chain_id):
    return [json.loads(r[0]) for r in db.execute(
        "SELECT receipt_json FROM research_fallback_attempts WHERE run_id=? AND chain_id=? ORDER BY ordinal",
        (run_id, chain_id))]


def _attempt_write(root, run_id, chain_id, ordinal, receipt):
    with closing(_db(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO research_fallback_attempts VALUES(?,?,?,?,?) "
                   "ON CONFLICT(run_id,chain_id,ordinal) DO UPDATE SET receipt_json=excluded.receipt_json",
                   (run_id, chain_id, ordinal, receipt["operation_id"], json.dumps(receipt, sort_keys=True)))
        db.commit()


def _material(root, run_id, identifiers):
    if (not isinstance(identifiers, list) or not identifiers or len(identifiers) > 30
            or any(not isinstance(x, str) for x in identifiers)):
        raise ValueError("FALLBACK_MATERIAL_NOT_SAVED_OR_INVALID")
    # Reuse the store's ownership/hash check, with a root-anchored path check
    # first so even a symlink replacing the entire artifacts directory fails.
    with closing(store._connection(root)) as db:
        for identifier in identifiers:
            row = db.execute("SELECT relative_path FROM artifacts WHERE run_id=? AND artifact_id=?",
                             (run_id, identifier)).fetchone()
            if row is None:
                raise ValueError("FALLBACK_MATERIAL_NOT_SAVED_OR_INVALID")
            rel = Path(row[0])
            path = (root / rel).resolve()
            if rel.is_absolute() or ".." in rel.parts or not path.is_relative_to(root / "artifacts"):
                raise ValueError("FALLBACK_MATERIAL_NOT_SAVED_OR_INVALID")
            if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
                raise ValueError("FALLBACK_MATERIAL_NOT_SAVED_OR_INVALID")
            store.read_artifact(root, run_id, identifier)


def _url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError("FALLBACK_PUBLIC_URL_REQUIRED")
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("FALLBACK_PUBLIC_URL_REQUIRED")
    # Runtime egress performs domain/DNS/port checks. No probe or bypass here.
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))


def _request(kind, query, url, artifact_id, question, options, country, language, limit, maximum):
    if kind not in ROUTES or type(maximum) is not int or not 1 <= maximum <= 3:
        raise ValueError("FALLBACK_KIND_OR_ATTEMPT_LIMIT_INVALID")
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("FALLBACK_RESULT_LIMIT_INVALID")
    result = {"task_kind": kind, "max_attempts": maximum, "limit": limit}
    if kind == "source_discovery":
        if not isinstance(query, str) or not query.strip() or len(query) > 600:
            raise ValueError("FALLBACK_QUERY_REQUIRED")
        if not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}", country):
            raise ValueError("FALLBACK_TASK_COUNTRY_REQUIRED")
        if not isinstance(language, str) or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2})?", language):
            raise ValueError("FALLBACK_TASK_LANGUAGE_REQUIRED")
        result.update(query=" ".join(unicodedata.normalize("NFC", query).split()),
                      country=country.upper(), language=language.lower())
    elif kind == "semantic_choice":
        journal._identifier(artifact_id, "artifact_id")
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            raise ValueError("FALLBACK_QUESTION_REQUIRED")
        if (not isinstance(options, list) or not 2 <= len(options) <= 20
                or any(not isinstance(x, str) or not x.strip() or len(x) > 150 for x in options)):
            raise ValueError("FALLBACK_OPTIONS_REQUIRED")
        result.update(artifact_id=artifact_id, question=question.strip(), options=options)
    else:
        result["url"] = _url(url)
    return result


def _params(capability, request):
    if capability == "serper.search":
        return {k: request[k] for k in ("query", "country", "language", "limit")} | {"mode": "web"}
    if capability == "serpapi.search":
        import serpapi_engine_catalog as _catalog
        return {k: request[k] for k in ("query", "country", "language", "limit")} | {
            "engine": _catalog.DEFAULT_ENGINE}
    if capability == "tinyfish.search":
        return {"query": request["query"], "location": request["country"], "language": request["language"]}
    if capability == "crawl4ai.fetch_page":
        return {"url": request["url"]}
    if capability == "tinyfish.fetch_content":
        return {"urls": [request["url"]], "format": "html", "links": True,
                "image_links": False, "iframe_links": False, "page_metadata": False}
    return {k: request[k] for k in ("artifact_id", "question", "options")}


def _nodes(payload):
    """Only reviewed response container shapes, bounded and never executable."""
    if not isinstance(payload, dict):
        return []
    found = [payload]
    for key in ("results", "pages", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            found.extend(x for x in value[:30] if isinstance(x, dict))
        elif isinstance(value, dict):
            found.append(value)
    return found


def _reasons(response):
    codes = {_code(response.get("reason_code"))}
    from research_runtime_results import diagnostic_reason_codes
    codes.update(diagnostic_reason_codes(codes=response.get("provider_reason_codes") or ()))
    for node in _nodes(response.get("payload")):
        codes.update(_code(node.get(key)) for key in ("reason", "reason_code", "error_code", "contract_reason_code"))
    for caveat in response.get("caveats") or ():
        if isinstance(caveat, str):
            codes.add(_code(caveat.removeprefix("adapter_local:")))
    return codes - {""}


def _source_denied(response):
    if _reasons(response) & SOURCE_STOPS:
        return True
    # Reuse the crawler's reviewed challenge signatures, not a new WAF bypass.
    from research_adapter_crawl4ai import _challenge_markers
    for node in _nodes(response.get("payload")):
        if any(node.get(k) in (401, 403, 429) for k in ("status_code", "http_status", "http_status_code")):
            return True
        body = "\n".join(node[k] for k in ("text", "markdown", "html", "content")
                         if isinstance(node.get(k), str))[:MAX_BYTES]
        headers = node.get("headers")
        if _challenge_markers(headers if isinstance(headers, dict) else {}, body):
            return True
    return False


def _usable(response, request):
    payload = response.get("payload")
    if request["task_kind"] == "source_discovery":
        return any(isinstance(hit, dict) and _source_url(hit.get("url") or hit.get("link"))
                   for node in _nodes(payload) for key in ("results", "organic", "organic_results", "matches")
                   for hit in (node.get(key) if isinstance(node.get(key), list) else [])[:30])
    if request["task_kind"] == "semantic_choice":
        judgments = payload.get("judgments") if isinstance(payload, dict) else None
        return (isinstance(judgments, dict) and bool(judgments)
                and any(isinstance(x, dict) and x.get("choice") in request["options"]
                        for x in judgments.values()))
    return any(isinstance(node.get(k), str) and bool(node[k].strip())
               for node in _nodes(payload) for k in ("text", "markdown", "html", "content"))


def _source_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"https", "http"} and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def _decision(response, request):
    codes = _reasons(response)
    if _source_denied(response):
        return "STOP", "FALLBACK_SOURCE_OR_POLICY_REFUSED"
    if (response.get("status") == "UNKNOWN" or "UNKNOWN_CHARGE_OUTSTANDING" in codes
            or any("TIMEOUT" in code for code in codes)):
        return "STOP", "FALLBACK_UNKNOWN_OUTCOME_HOLD"
    if codes & SHARED_STOPS:
        return "STOP", sorted(codes & SHARED_STOPS)[0]
    if response.get("status") == "BLOCKED":
        return ("NEXT", sorted(codes & SKIPPABLE)[0]) if codes & SKIPPABLE else ("STOP", "FALLBACK_UNCLASSIFIED_REFUSAL")
    if response.get("status") == "FAILED":
        technical = codes & TECHNICAL
        status_5xx = any(type(node.get("status_code")) is int and 500 <= node["status_code"] <= 599
                         for node in _nodes(response.get("payload")))
        return ("NEXT", sorted(technical)[0] if technical else "PROVIDER_HTTP_5XX") if technical or status_5xx else (
            "STOP", "FALLBACK_UNCLASSIFIED_FAILURE")
    if response.get("status") != "OK":
        return "STOP", "FALLBACK_UNKNOWN_OUTCOME_HOLD"
    if not _usable(response, request):
        return "NEXT", "EMPTY_USABLE_RESULT"
    return "SUCCESS", "USABLE_CANDIDATE_MATERIAL"


def _amount(value):
    try:
        amount = Decimal(str(value))
        return str(amount) if amount.is_finite() and amount >= 0 else None
    except (InvalidOperation, ValueError):
        return None


def _receipt(run_id, operation_id, request, attempts, *, reason, artifacts=None, selected=None):
    return {"status": "SUCCESS_CANDIDATE" if selected else "HOLD", "reason_code": reason,
            "run_id": run_id, "operation_id": operation_id, "task_kind": request["task_kind"],
            "attempts": attempts, "managed_calls": sum(x["managed_invoked"] for x in attempts),
            "max_attempts": request["max_attempts"], "selected_capability": selected,
            "artifact_ids": artifacts or [],
            "source_artifact_ids": [request["artifact_id"]] if "artifact_id" in request else [],
            "next_action": "SOURCE_OR_MANUAL_REVIEW_REQUIRED" if request["task_kind"] == "semantic_choice" and not selected
                           else "REVIEW_SAVED_CANDIDATES" if selected else "REVIEW_CAUSE_OR_DIFFERENT_AUTHORIZED_SOURCE",
            "unknown_cost_attempts": sum(x["cost_unknown"] for x in attempts),
            "business_verified": False, "budget_reset": False, "replayed": False,
            "measurement_scope": "Managed attempts and persisted materials, not verified business results or savings."}


def research_step(data_root, *, run_id, operation_id, task_kind, managed_call, admission,
                  query="", url="", artifact_id="", question="", options=None,
                  country="", language="", limit=5, max_attempts=3):
    """Execute one explicitly requested fallback chain within existing consent.

    Exceptions after recording attempt intent are ambiguous: stop, retain the
    receipt, never guess that the call was free or invoke another provider.
    """
    journal._identifier(run_id, "run_id")
    journal._identifier(operation_id, "operation_id")
    if not callable(managed_call) or not callable(admission):
        raise ValueError("FALLBACK_LOCAL_CALLBACKS_REQUIRED")
    request = _request(task_kind, query, url, artifact_id, question, options,
                       country, language, limit, max_attempts)
    root = journal._root(data_root)
    store.list_run(root, run_id, limit=1)
    if task_kind == "semantic_choice":
        _material(root, run_id, [artifact_id])
    digest = _digest(request)
    with closing(_db(root)) as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT request_sha256,state,receipt_json FROM research_fallback_chains "
                           "WHERE run_id=? AND chain_id=?", (run_id, operation_id)).fetchone()
        if prior:
            if prior[0] != digest:
                raise ValueError("FALLBACK_CHAIN_ID_COLLISION")
            result = (json.loads(prior[2]) if prior[1] == "COMPLETE" else
                      _receipt(run_id, operation_id, request, _attempts(db, run_id, operation_id),
                               reason="FALLBACK_CHAIN_IN_PROGRESS_OR_INTERRUPTED"))
            db.commit()
            if result["status"] == "SUCCESS_CANDIDATE":
                try:
                    _material(root, run_id, result["artifact_ids"])
                except (ValueError, OSError):
                    result.update(status="HOLD", reason_code="FALLBACK_MATERIAL_NOT_SAVED_OR_INVALID",
                                  next_action="RESTORE_OR_REVIEW_SAVED_MATERIAL")
            return {**result, "replayed": True}
        db.execute("INSERT INTO research_fallback_chains VALUES(?,?,?,'RUNNING',NULL,?)",
                   (run_id, operation_id, digest, _stamp()))
        db.commit()

    attempts, calls, artifacts, selected = [], 0, [], None
    source_refs = {"primary_artifact_ids": [], "provider_html_artifact_ids": []}
    final_reason = "FALLBACK_NO_USABLE_PROVIDER"
    import research_walls
    route = ROUTES[task_kind]
    if task_kind in {"known_public_url", "batch_public_pages"}:
        route = research_walls.preferred_route(root, request["url"], route)
    for ordinal, capability in enumerate(route, 1):
        if calls >= max_attempts:
            final_reason = "FALLBACK_ATTEMPT_LIMIT"
            break
        child = "fb-" + _digest([run_id, operation_id, ordinal, capability])[:40]
        item = {"capability_id": capability, "provider": capability.split(".")[0],
                "operation_id": child, "parent_ref": operation_id, "ordinal": ordinal,
                "status": "SKIPPED", "managed_invoked": False, "gateway_invoked": False,
                "artifact_ids": [],
                "actual_usd": None, "cost_unknown": False, "charge_state": "NOT_DISPATCHED",
                "recorded_at": _stamp()}
        try:
            verdict = admission(capability)
        except Exception:
            verdict = {"allowed": False, "reason_code": "FALLBACK_ADMISSION_UNAVAILABLE"}
        if not isinstance(verdict, dict) or type(verdict.get("allowed")) is not bool:
            verdict = {"allowed": False, "reason_code": "FALLBACK_ADMISSION_UNAVAILABLE"}
        if not verdict["allowed"]:
            code = _code(verdict.get("reason_code")) or "FALLBACK_ADMISSION_UNAVAILABLE"
            item.update(reason_code=code, decision_reason=code)
            attempts.append(item)
            _attempt_write(root, run_id, operation_id, ordinal, item)
            if code not in SKIPPABLE:
                final_reason = "FALLBACK_SOURCE_OR_POLICY_REFUSED" if code in SOURCE_STOPS else code
                break
            continue
        # Persist intent before a callback can cross the network boundary.
        item.update(status="INTENT", managed_invoked=True, gateway_invoked=None,
                    cost_unknown=True, charge_state="UNKNOWN_NOT_RECONCILED")
        _attempt_write(root, run_id, operation_id, ordinal, item)
        calls += 1
        try:
            response = managed_call(capability_id=capability, params=_params(capability, request),
                                    run_id=run_id, operation_id=child)
            if not isinstance(response, dict):
                raise ValueError("FALLBACK_RESPONSE_INVALID")
            action, reason = _decision(response, request)
        except Exception:
            # Never echo errors; they can contain provider keys or request text.
            response = {"status": "UNKNOWN", "reason_code": "FALLBACK_CALLBACK_FAILED",
                        "gateway_invoked": None, "actual_usd": None}
            action, reason = "STOP", "FALLBACK_UNKNOWN_OUTCOME_HOLD"
            if capability in LOCAL_CAPABILITIES:
                # The local crawler runs on this machine and bills nobody: a
                # failure is technical, so the chain continues to the next
                # provider instead of holding a charge that cannot exist.
                response.update(status="FAILED", actual_usd=0)
                action, reason = "NEXT", "LOCAL_CRAWL_RUNTIME_FAILED"
        amount = _amount(response.get("actual_usd"))
        from research_runtime_results import diagnostic_reason_codes
        diagnostics = diagnostic_reason_codes(response.get("payload"),
            codes=[response.get("reason_code"), *(response.get("provider_reason_codes") or ())])
        item.update(status=response.get("status") if response.get("status") in {"OK", "BLOCKED", "FAILED", "UNKNOWN"} else "UNKNOWN",
                    reason_code=_code(response.get("reason_code")) or (diagnostics[0] if diagnostics else ""),
                    primary_reason_code=diagnostics[0] if diagnostics else _code(response.get("reason_code")),
                    provider_reason_codes=diagnostics, decision_reason=reason,
                    gateway_invoked=response.get("gateway_invoked") if type(response.get("gateway_invoked")) is bool else None,
                    actual_usd=amount, cost_unknown=amount is None and response.get("gateway_invoked") is not False,
                    charge_state=_code(response.get("charge_state")) or "NOT_REPORTED_USE_USAGE_LEDGER")
        if response.get("status") == "OK" and action in {"SUCCESS", "NEXT"}:
            try:
                _material(root, run_id, response.get("artifact_ids"))
            except (ValueError, OSError):
                action, reason = "STOP", "FALLBACK_MATERIAL_NOT_SAVED_OR_INVALID"
                item["decision_reason"] = reason
            else:
                item["artifact_ids"] = list(response["artifact_ids"])
                if action == "SUCCESS":
                    artifacts = list(response["artifact_ids"])
                    selected = capability
                    for key in source_refs:
                        values = response.get(key)
                        source_refs[key] = [x for x in values if x in artifacts] if isinstance(values, list) else []
        attempts.append(item)
        _attempt_write(root, run_id, operation_id, ordinal, item)
        final_reason = reason
        if action in {"SUCCESS", "STOP"}:
            break

    receipt = _receipt(run_id, operation_id, request, attempts, reason=final_reason,
                       artifacts=artifacts, selected=selected)
    receipt.update(source_refs)
    if route != ROUTES[task_kind]:
        receipt["route_note"] = "DOMAIN_MEMORY_STARTED_AT_" + route[0]
    research_walls.record_chain(root, run_id=run_id, task_kind=task_kind,
                                url=request.get("url") or "", query=request.get("query") or "",
                                attempts=attempts, selected=selected)
    # This is a short observed mechanism, not a causal conclusion or new SOP.
    stage = "discovery" if task_kind == "source_discovery" else "qualification" if task_kind == "semantic_choice" else "acquisition"
    try:
        event_id = "fallback-" + _digest([run_id, operation_id])[:40]
        event = journal.append_event(root, run_id=run_id, operation_id=event_id,
            stage=stage, kind="decision", status="succeeded" if selected else "hold",
            provider="local", receipt_ref=operation_id, reason_code=final_reason)
        receipt["event_ref"] = "event:" + str(event["sequence"])
        journal.append_note(root, run_id=run_id, note_id=event_id, stage=stage, category="other",
            problem="Bounded research step: " + task_kind,
            action_taken="Then ".join(x["capability_id"] + " " + x["status"] + "; " for x in attempts),
            observed_result=receipt["status"] + ": " + final_reason,
            hypothesis="", evidence_ref=receipt["event_ref"])
        receipt["experience_refresh"] = journal._refresh_summary_quietly(root)
    except Exception as exc:
        receipt["journal_status"] = "PARTIAL"
        receipt["journal_reason_type"] = type(exc).__name__
    with closing(_db(root)) as db:
        db.execute("UPDATE research_fallback_chains SET state='COMPLETE',receipt_json=? WHERE run_id=? AND chain_id=?",
                   (json.dumps(receipt, sort_keys=True), run_id, operation_id))
        db.commit()
    return receipt

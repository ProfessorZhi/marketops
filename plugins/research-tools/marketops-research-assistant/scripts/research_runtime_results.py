"""Persist managed provider results in the existing materials/candidate store."""
import hashlib
import json
import sqlite3
from contextlib import closing

import research_lite_store as store
from research_event_journal import append_event


def normalize_params(capability, params):
    values = dict(params)
    if capability in {"crawl4ai.fetch_page", "crawl4ai.extract"}:
        bounds = {"max_pages": 1, "max_depth": 0, "max_domains": 1,
                  "max_bytes": 3_000_000, "max_seconds": 45,
                  "max_browser_seconds": 45, "max_artifacts": 2}
        supplied = values.get("limits", {})
        if not isinstance(supplied, dict) or set(supplied) - set(bounds):
            raise ValueError("CAPABILITY_PARAM_NOT_ALLOWED: invalid crawl limits")
        for key, value in supplied.items():
            if type(value) is not int or not 0 <= value <= bounds[key]:
                raise ValueError("BUDGET_OPERATION_CEILING: crawl limit exceeds product bound")
        values["limits"] = {**bounds, **supplied}
    if capability in {"serper.search", "serpapi.search"}:
        for old, new in (("q", "query"), ("gl", "country"), ("hl", "language"), ("num", "limit")):
            if old in values:
                incoming = values.pop(old)
                if new in values and values[new] != incoming:
                    raise ValueError("CAPABILITY_PARAM_NOT_ALLOWED: conflicting search aliases")
                values[new] = incoming
        if isinstance(values.get("country"), str):
            values["country"] = values["country"].upper()
        if isinstance(values.get("language"), str):
            values["language"] = values["language"].lower()
        if capability == "serpapi.search" and "mode" in values:
            if values.pop("mode") != "compact":
                raise ValueError("CAPABILITY_PARAM_NOT_ALLOWED: SerpApi mode is compact")
    return values


def identity(capability, params):
    values = dict(params)
    if capability in {"serper.search", "serpapi.search"}:
        import search_canonical
        values = search_canonical.search_operation_args(
            query=values.get("query"), country=values.get("country"),
            language=values.get("language"), limit=values.get("limit"),
            engine=values.get("engine") if capability == "serpapi.search" else None,
            mode=values.get("mode") if capability == "serper.search" else None,
            page=values.get("page"))
    return hashlib.sha256(json.dumps({"capability": capability, "params": values},
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _id(run_id, operation_id, kind):
    return kind + "-" + hashlib.sha256((run_id + ":" + operation_id).encode()).hexdigest()[:32]


def _actual_ledger_id(root, run_id, capability_id, provider, outcome):
    """Read the reservation's real operation, never mint an ID to fill a gap."""
    reservation = getattr(outcome, "reservation", None)
    identifier = getattr(reservation, "operation_id", None)
    if not isinstance(identifier, str):
        return None
    import research_usage_ledger as usage
    import research_capability_registry as registry
    entry = registry.DEFAULT_REGISTRY.get(capability_id)
    path = usage.default_path(root)
    if not path.is_file() or entry is None:
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT project,run_id,provider,capability FROM operations WHERE operation_id=?",
                         (identifier,)).fetchone()
    expected = (usage.project_for_root(root), run_id, provider, entry.ledger_capability)
    return identifier if row is not None and tuple(row) == expected else None


def persist(root, run_id, operation_id, guarded, *, params):
    outcome = guarded.outcome
    result = getattr(outcome, "result", None)
    payload = getattr(result, "payload", None)
    provider = getattr(outcome, "provider", None) or guarded.capability_id.split(".")[0]
    artifacts = []
    for artifact in (getattr(result, "artifacts", ()) or ()):
        identifier = artifact.get("artifact_id") if isinstance(artifact, dict) else artifact
        if not isinstance(identifier, str):
            raise ValueError("ARTIFACT_REFERENCE_NOT_STORE_ID")
        stored = store.read_artifact(root, run_id, identifier)
        if isinstance(artifact, dict) and artifact.get("sha256") not in {None, stored["content_sha256"]}:
            raise ValueError("ARTIFACT_READBACK_HASH_MISMATCH")
        artifacts.append(identifier)
    primary_artifacts = [x for x in artifacts if store.artifact_kind(root, run_id, x)
                         in {"raw_response", "rendered_html"}]
    records = []
    status, reason_code = guarded.status, guarded.reason_code
    source_materials = []
    route = {"serper.search": "serper", "serpapi.search": "serpapi_mcp_search",
             "tinyfish.search": "tinyfish_mcp_search", "tinyfish.fetch_content": "tinyfish_mcp_fetch",
             "crawl4ai.fetch_page": "crawl4ai_local", "crawl4ai.extract": "crawl4ai_local",
             "jev.choice": "jev_choice", "jev.noul": "jev_choice", "jev.score": "jev_choice"
             }.get(guarded.capability_id, provider)
    saved = None
    if guarded.status == "OK" and payload is not None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
        artifact_id = _id(run_id, operation_id, "material")
        url = str(params.get("url") or (params.get("urls") or [""])[0])
        saved = store.save_artifact(root, run_id, artifact_id, provider, url, body,
            "decision_response" if provider == "typesafe_jev" else "provider_extracted")
        if saved["artifact_id"] not in artifacts:
            artifacts.append(saved["artifact_id"])
        if guarded.capability_id == "tinyfish.fetch_content" and params.get("format") == "html":
            from research_provider_html import extract_pages
            try:
                pages = extract_pages(payload, params.get("urls"))
            except ValueError as exc:
                # The vendor call succeeded, but its returned pages cannot be
                # associated with the requested source. Keep the vendor envelope
                # and cost observation, not successful source candidates.
                status, reason_code = "FAILED", str(exc)
            else:
                for page in pages:
                    identifier = _id(run_id, operation_id + ":" + page["url"], "vendor-html")
                    item = store.save_artifact(root, run_id, identifier, provider, page["url"],
                                               page["html"].encode("utf-8"), "provider_html")
                    artifacts.append(identifier)
                    source_materials.append({**item, "url": page["url"], "vendor_mediated": True,
                        "transform": "cleaned_semantic_html", "raw_http_verified": False,
                        "browser_dom_verified": False, "business_verified": False,
                        "method": "tinyfish.fetch_content.format_html",
                        "vendor_envelope_artifact_id": saved["artifact_id"]})
        if isinstance(payload, dict) and status == "OK":
            hits = payload.get("results") or payload.get("organic") or payload.get("matches") or []
            if source_materials:
                hits = [{"url": page["url"], "title": page["url"], "source_material": page}
                        for page in source_materials]
            if not hits and url:
                hits = [{"url": url, "title": payload.get("title") or url}]
            for i, hit in enumerate(hits[:20] if isinstance(hits, list) else []):
                if not isinstance(hit, dict):
                    continue
                record_id = _id(run_id, operation_id + ":" + str(i), "source")
                source = hit.get("source_material")
                refs = [artifact_id] + ([source["artifact_id"]] if source else [])
                source_data = ({"source_material_kind": "provider_html", "vendor_mediated": True,
                                "content_transform": "cleaned_semantic_html"} if source else {})
                record = store.put_candidate(root, run_id, record_id, "discovery", "source",
                    record_id, {"provider": provider, "operation_id": operation_id,
                    "url": hit.get("url") or hit.get("link"), "title": hit.get("title"),
                    "snippet": hit.get("snippet"), "discovery_only": True, **source_data}, refs)
                records.append(record["record_id"])
    # Bind every returned representation to the operation actually retained by
    # the existing ledger. Missing links stay explicit; a syntactically valid
    # operation string alone is not evidence of a real reservation.
    from research_evidence_store import record_envelope
    ledger_id = _actual_ledger_id(root, run_id, guarded.capability_id, provider, outcome)
    envelope_ids = []
    for identifier in artifacts:
        material = store.read_artifact(root, run_id, identifier)
        kind = material["material_kind"]
        method = ("tinyfish.fetch_content.format_html" if kind == "provider_html"
                  else guarded.capability_id + ":" + kind)
        caveats = ("VENDOR_CLEANED_HTML_NOT_RAW_HTTP_OR_BROWSER_DOM",) if kind == "provider_html" else ()
        envelope = record_envelope(root, run_id, identifier, operation_id=operation_id,
            provider=provider, capability_id=guarded.capability_id, extraction_method=method,
            ledger_id=ledger_id, verified_state="UNVERIFIED",
            derived_refs=[x["artifact_id"] for x in source_materials] if saved and identifier == saved["artifact_id"] else [],
            caveats=caveats + ("CANDIDATE_ONLY_NOT_BUSINESS_VERIFIED", "EGRESS_REFERENCE_NOT_RETAINED"))
        envelope_ids.append(envelope["evidence_id"])
    event = append_event(root, run_id=run_id, operation_id=operation_id,
        stage="acquisition" if "search" not in guarded.capability_id else "discovery",
        kind="tool", status="succeeded" if status == "OK" else "blocked"
        if status == "BLOCKED" else "hold", provider=route,
        reason_code=reason_code or "")
    from research_provider_policy import invocation_receipt
    invocation = invocation_receipt(route, reservation="ONE_ATTEMPT_RESERVED_NOT_BILL_VERIFIED")
    return {"status": status, "reason_code": reason_code,
        "provider_status": guarded.status,
        "capability_id": guarded.capability_id, "gateway_invoked": guarded.gateway_invoked,
        "s0_failed": list(guarded.s0_failed), "provider": provider,
        "payload": payload, "artifact_ids": artifacts, "artifact": saved,
        "primary_artifact_ids": primary_artifacts,
        "provider_html_artifact_ids": [x["artifact_id"] for x in source_materials],
        "source_materials": source_materials,
        "ledger_operation_id": ledger_id,
        "evidence_envelope_ids": envelope_ids,
        "stored_record_ids": records, "invocation": invocation,
        "event": event, "caveats": list(guarded.caveats) + list(getattr(outcome, "caveats", ())),
        "actual_usd": (str(result.actual_usd) if getattr(result, "actual_usd", None) is not None else None),
        "outcome": None if outcome is None else {"status": outcome.status,
            "reason_code": outcome.reason_code, "provider": provider,
            "payload": payload, "artifacts": artifacts}}


def resolve_excerpt(root, run_id, excerpt):
    """Legacy Jev input must identify or quote material already saved in this Run."""
    try:
        store.read_artifact(root, run_id, excerpt)
        return excerpt
    except (ValueError, OSError):
        pass
    if not isinstance(excerpt, str) or not 20 <= len(excerpt) <= 20_000:
        raise ValueError("JEV_EVIDENCE_BINDING_REQUIRED: pass a saved artifact ID")
    with closing(store._connection(root)) as db:
        ids = [row[0] for row in db.execute(
            "SELECT artifact_id FROM artifacts WHERE run_id=? ORDER BY artifact_id", (run_id,))]
    matches = []
    for identifier in ids:
        item = store.read_artifact(root, run_id, identifier)
        content = item.get("content") or item.get("bytes") or b""
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        if excerpt in content:
            matches.append(identifier)
    if len(matches) != 1:
        raise ValueError("JEV_EVIDENCE_BINDING_REQUIRED: pass one saved artifact ID")
    return matches[0]

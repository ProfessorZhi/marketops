#!/usr/bin/env python3
"""Bounded adapters for Crawl4AI, TinyFish and TypeSafe Jev, called by the Lite MCP.

Every attempted external call requires user-configured provider credentials and an
operator-set cap. Network/provider operations must never silently fall back.
All results are research material or model *suggestions*, not approved facts.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import research_lite_store as store
from research_event_journal import _root, append_event

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
MAX_OUTPUT_BYTES = 4_000_000
ALLOWED_ACTIONS = frozenset({"crawl4ai_cloud", "crawl4ai_local",
                             "tinyfish_search", "tinyfish_fetch", "jev_choice"})
KEY_ENV = {"crawl4ai_cloud": "CRAWL4AI_API_KEY",
           "tinyfish_search": "TINYFISH_API_KEY",
           "tinyfish_fetch": "TINYFISH_API_KEY",
           "jev_choice": "TYPESAFE_API_KEY"}


def _key(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", value):
        raise ValueError(f"MISSING_PROVIDER_CREDENTIAL:{name}")
    return value


def _limit() -> int:
    val = os.environ.get("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", "0").strip()
    if not val.isdecimal():
        raise ValueError("INVALID_EXTERNAL_CALL_LIMIT")
    return int(val)


def _public_https_url(value: str) -> str:
    """Strictly limit browser/HTTP targets to operator-provided public HTTPS domains."""
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("INVALID_PUBLIC_URL")
    parsed = urllib.parse.urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        raise ValueError("INVALID_PUBLIC_URL")
    if parsed.port not in {None, 443}:
        raise ValueError("INVALID_PUBLIC_URL_PORT")
    if hostname.endswith((".local", ".internal")) or hostname == "localhost":
        raise ValueError("PRIVATE_TARGET")
    try:
        ip = ipaddress.ip_address(hostname)
        raise ValueError("PUBLIC_HOSTNAME_REQUIRED")  # Do not fetch raw IPs.
    except ValueError as exc:
        if str(exc) == "PUBLIC_HOSTNAME_REQUIRED":
            raise
    allow = [d.strip().lower() for d in os.environ.get("MARKETOPS_FETCH_ALLOWED_DOMAINS", "").split(",") if d.strip()]
    if not allow or not any(hostname == d or hostname.endswith("." + d) for d in allow):
        raise ValueError("HOST_NOT_APPROVED: set MARKETOPS_FETCH_ALLOWED_DOMAINS on the client")
    return value


def _checked_run(root: Path, run_id: str, operation_id: str) -> None:
    if not isinstance(operation_id, str) or not ID.fullmatch(operation_id):
        raise ValueError("INVALID_OPERATION_ID")
    if not isinstance(run_id, str) or not ID.fullmatch(run_id):
        raise ValueError("INVALID_RUN_ID")
    store.list_run(root, run_id, limit=1)


def _reserve(root: Path, *, run_id: str, operation_id: str, action: str, signature: str) -> None:
    if action not in ALLOWED_ACTIONS:
        raise ValueError("UNSUPPORTED_PROVIDER_ACTION")
    if os.environ.get("MARKETOPS_RESEARCH_ALLOW_NETWORK") != "1":
        raise ValueError("RESEARCH_NETWORK_DISABLED")
    ceiling = _limit()
    if ceiling < 1:
        raise ValueError("RESEARCH_BUDGET_NOT_APPROVED")
    if action in KEY_ENV:
        _key(KEY_ENV[action])
    if action == "crawl4ai_local" and importlib.util.find_spec("crawl4ai") is None:
        raise ValueError("CRAWL4AI_LOCAL_NOT_INSTALLED")
    folder = root / "state"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(folder / "provider-reservations.sqlite3", timeout=20)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS provider_reservations (
            run_id TEXT NOT NULL, operation_id TEXT NOT NULL, action TEXT NOT NULL,
            signature TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, operation_id)
        )""")
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT signature FROM provider_reservations "
                         "WHERE run_id=? AND operation_id=?", (run_id, operation_id)).fetchone()
        if old:
            if old[0] == signature:
                raise ValueError("OPERATION_ALREADY_RESERVED: inspect the prior receipt")
            raise ValueError("OPERATION_ID_COLLISION")
        reserved = db.execute("SELECT COUNT(*) FROM provider_reservations").fetchone()[0]
        if reserved >= ceiling:
            raise ValueError("EXTERNAL_CALL_CEILING_REACHED")
        db.execute("INSERT INTO provider_reservations VALUES (?,?,?,?,?)",
                   (run_id, operation_id, action, signature, datetime.now(timezone.utc).isoformat()))
        db.commit()


def _http_json(url: str, *, method: str, key: str, data: dict | None = None,
               opener=None) -> dict:
    """Only call known official endpoint URLs, never follow user-supplied API URLs."""
    body = json.dumps(data).encode("utf-8") if data is not None else None
    headers = {"X-API-Key": key} if "tinyfish.ai" in urllib.parse.urlsplit(url).hostname else {
        "Authorization": "Bearer " + key}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    if opener is None:
        # Refuse redirected requests: may carry an auth header to another origin.
        import dual_search_mcp
        opener = urllib.request.build_opener(dual_search_mcp.NoRedirect()).open
    try:
        with opener(req, timeout=24) as response:
            if response.status != 200:
                raise ValueError("UPSTREAM_HTTP_NON_200")
            raw = response.read(MAX_OUTPUT_BYTES + 1)
            if len(raw) > MAX_OUTPUT_BYTES:
                raise ValueError("UPSTREAM_RESULT_TOO_LARGE")
    except Exception:
        # Network errors may contain request URL/headers; redact by code only.
        raise ValueError("UPSTREAM_TRANSPORT_OR_AUTH_FAILED") from None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("UPSTREAM_NOT_JSON") from None


def _crawl4ai_local(url: str) -> dict:
    """Keep post-rendered HTML separate from derivative Markdown, never claim HTTP raw."""
    from crawl4ai import AsyncWebCrawler

    async def crawl() -> dict:
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url)
            status = getattr(result, "status_code", None)
            if status in {401, 403, 429}:
                raise ValueError("SOURCE_ACCESS_BLOCKED")
            if not result.success:
                raise ValueError("LOCAL_CRAWL_FAILED")
            markdown = result.markdown
            content = getattr(markdown, "raw_markdown", None) or str(markdown or "")
            rendered = getattr(result, "html", None) or ""
            probe = (content[:1500] + rendered[:1500]).casefold()
            if any(word in probe for word in ("cf-chl-", "verify you are human",
                                               "captcha challenge", "just a moment...")):
                raise ValueError("SOURCE_CHALLENGE_PAGE")
            if not content.strip():
                raise ValueError("SOURCE_EMPTY_EXTRACT")
            return {"url": getattr(result, "url", url), "markdown": content,
                    "_rendered_html": rendered, "material_kind": "provider_extracted"}

    try:
        return asyncio.run(crawl())
    except ValueError as exc:
        if str(exc) in {"SOURCE_ACCESS_BLOCKED", "SOURCE_CHALLENGE_PAGE",
                        "SOURCE_EMPTY_EXTRACT", "LOCAL_CRAWL_FAILED"}:
            raise
        raise ValueError("LOCAL_CRAWL_RUNTIME_FAILED") from None
    except Exception as exc:
        # Reason codes only: browser installation defects should be actionable
        # without exposing URL, headers or secrets from an exception message.
        msg = str(exc).casefold()
        if "executable doesn't exist" in msg or "browser executable" in msg:
            raise ValueError("LOCAL_BROWSER_NOT_INSTALLED") from None
        raise ValueError("LOCAL_CRAWL_RUNTIME_FAILED") from None


def _jev_choice(*, document: str, question: str, options: list[str]) -> dict:
    if not isinstance(document, str) or not 20 <= len(document) <= 4000:
        raise ValueError("JEV_REQUIRES_BOUNDED_EVIDENCE_TEXT")
    if not isinstance(question, str) or not 10 <= len(question) <= 500:
        raise ValueError("JEV_QUESTION_INVALID")
    if (not isinstance(options, list) or not 2 <= len(options) <= 8
            or any(not isinstance(v, str) or not ID.fullmatch(v) for v in options)
            or len(options) != len(set(options))):
        raise ValueError("JEV_OPTIONS_INVALID")
    from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient
    with TypeSafeClient(api_key=_key("TYPESAFE_API_KEY"),
                        retry=RetryPolicy(max_retries=0)) as client:
        result = client.system_one(
            state={"source_excerpt": document},
            questions={"marketops_choice": Choice(
                instructions=question,
                criteria={label: None for label in options},
            )},
        )
    raw = result.choices["marketops_choice"]
    answer = str(raw.choice)
    if answer not in options:
        raise ValueError("JEV_RETURNED_UNDECLARED_OPTION")
    return {"chosen_option": answer,
            "model": str(getattr(result, "model", "") or ""),
            "judgment_only": True,
            "note": "Typed model choice is NOT evidence or SOP approval."}


def invoke(action: str, *, run_id: str, operation_id: str, url: str = "",
           query: str = "", document: str = "", question: str = "",
           options: list[str] | None = None, opener=None) -> dict:
    """One bounded, metered material/decision call, with audit trail and Lite store."""
    root = _root(None)
    _checked_run(root, run_id, operation_id)
    if action not in ALLOWED_ACTIONS:
        raise ValueError("UNKNOWN_PROVIDER_ACTION")
    if action in {"crawl4ai_cloud", "crawl4ai_local", "tinyfish_fetch"}:
        _public_https_url(url)
    if action == "tinyfish_search" and (not isinstance(query, str) or not 2 <= len(query) <= 240):
        raise ValueError("SEARCH_QUERY_INVALID")
    if action == "jev_choice" and (not isinstance(document, str) or not 20 <= len(document) <= 4000):
        raise ValueError("JEV_REQUIRES_BOUNDED_EVIDENCE_TEXT")
    fingerprint = hashlib.sha256(json.dumps({
        "action": action, "url": url, "query": query,
        "document": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "question": question, "options": options,
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
    try:
        _reserve(root, run_id=run_id, operation_id=operation_id, action=action, signature=fingerprint)
    except ValueError:
        # A rejected budget/key/network preflight is a real research obstacle.
        # Keep a receipt without leaking Key values or the raw problem text.
        try:
            append_event(root, run_id=run_id, operation_id=operation_id,
                         stage="qualification" if action == "jev_choice" else "acquisition",
                         kind="tool", status="blocked", provider=action,
                         reason_code="external_preflight_blocked")
        except Exception:
            pass
        raise
    append_event(root, run_id=run_id, operation_id=operation_id,
                 stage="acquisition" if action != "jev_choice" else "qualification",
                 kind="tool", status="started", provider=action)
    try:
        if action == "crawl4ai_local":
            response = _crawl4ai_local(url)
        elif action == "crawl4ai_cloud":
            response = _http_json("https://api.crawl4ai.com/scrape", method="POST",
                                  key=_key("CRAWL4AI_API_KEY"),
                                  data={"url": url, "format": "md"}, opener=opener)
        elif action == "tinyfish_search":
            target = "https://api.search.tinyfish.ai?" + urllib.parse.urlencode({"query": query})
            response = _http_json(target, method="GET", key=_key("TINYFISH_API_KEY"),
                                  opener=opener)
        elif action == "tinyfish_fetch":
            response = _http_json("https://api.fetch.tinyfish.ai", method="POST",
                                  key=_key("TINYFISH_API_KEY"),
                                  data={"urls": [url]}, opener=opener)
        else:
            response = _jev_choice(document=document, question=question, options=options or [])
        # A browser's rendered DOM is a separate source representation, not
        # on-the-wire HTTP bytes. Never include it in the Agent's tool preview.
        rendered_html = response.pop("_rendered_html", None) if isinstance(response, dict) else None
        serialized = json.dumps(response, ensure_ascii=False, default=str)
        # Upstream services must not be allowed to echo operator credentials into
        # evidence/artifact storage or back into the Agent context.
        for key_name in ("SERPAPI_API_KEY", "SERPER_API_KEY", "TINYFISH_API_KEY",
                         "CRAWL4AI_API_KEY", "TYPESAFE_API_KEY"):
            secret = os.environ.get(key_name, "").strip()
            if len(secret) >= 8:
                serialized = serialized.replace(secret, "[REDACTED_PROVIDER_SECRET]")
        encoded = serialized.encode("utf-8")
        artifact_id = "art-" + hashlib.sha256(
            (run_id + ":" + operation_id + ":" + action).encode("utf-8")).hexdigest()[:32]
        rendered_artifact = None
        if isinstance(rendered_html, str) and rendered_html.strip():
            safe_html = rendered_html
            for key_name in ("SERPAPI_API_KEY", "SERPER_API_KEY", "TINYFISH_API_KEY",
                             "CRAWL4AI_API_KEY", "TYPESAFE_API_KEY"):
                secret = os.environ.get(key_name, "").strip()
                if len(secret) >= 8:
                    safe_html = safe_html.replace(secret, "[REDACTED_PROVIDER_SECRET]")
            raw_id = "raw-" + hashlib.sha256(
                (run_id + ":" + operation_id + ":rendered_html").encode()).hexdigest()[:32]
            rendered_artifact = store.save_artifact(
                root, run_id, raw_id, provider=action,
                origin_url=url if url else "provider-response",
                content=safe_html.encode("utf-8"), material_kind="rendered_html",
            )
        stored = store.save_artifact(
            root, run_id, artifact_id, provider=action,
            origin_url=url if url else "provider-response",
            content=encoded,
            material_kind="decision_response" if action == "jev_choice" else "provider_extracted",
        )
        # Every vendor produces candidate material, not a verified business assertion.
        stage = "qualification" if action == "jev_choice" else "acquisition"
        kind = "observation" if action == "jev_choice" else "source"
        record_id = "rcd-" + hashlib.sha256((artifact_id + ":" + kind).encode()).hexdigest()[:32]
        entry = store.put_candidate(
            root, run_id=run_id, record_id=record_id,
            stage=stage, kind=kind, entity_key=operation_id,
            payload={"provider": action, "material_kind": stored["material_kind"],
                     "artifact_id": artifact_id, "derived_only": True},
            artifact_ids=[artifact_id] + ([rendered_artifact["artifact_id"]] if rendered_artifact else []),
        )
        append_event(root, run_id=run_id, operation_id=operation_id,
                     stage=stage, kind="tool", status="succeeded",
                     provider=action, receipt_ref=artifact_id)
        return {"status": "SAVED_MATERIAL_NOT_VERIFIED",
                "provider": action, "operation_id": operation_id,
                "artifact": stored, "rendered_artifact": rendered_artifact,
                "record": entry,
                "preview": serialized[:480], "preview_truncated": len(serialized) > 480,
                "preview_kind": "brief_derived_snippet_not_evidence"}
    except Exception as exc:
        known_codes = {"SOURCE_ACCESS_BLOCKED", "SOURCE_CHALLENGE_PAGE",
                       "SOURCE_EMPTY_EXTRACT", "LOCAL_CRAWL_FAILED",
                       "LOCAL_BROWSER_NOT_INSTALLED", "LOCAL_CRAWL_RUNTIME_FAILED"}
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) in known_codes else "PROVIDER_OR_STORAGE_FAILED"
        try:
            append_event(root, run_id=run_id, operation_id=operation_id,
                         stage="qualification" if action == "jev_choice" else "acquisition",
                         kind="tool", status="blocked" if reason in known_codes else "unknown",
                         provider=action, reason_code=reason.lower())
        except Exception:
            pass
        raise ValueError(reason + ": inspect prior operation; never auto-retry") from None

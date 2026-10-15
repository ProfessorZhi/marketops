#!/usr/bin/env python3
"""Optional local dual-search MCP (stdio): SerpApi OR Serper, explicit per-call choice.

The same search capability can use either vendor, never silent failover or double billing.
This is a bounded *search discovery* adapter, not the full MarketOps Research MCP.
Run inside a customer-controlled process with explicit operator-set environment.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

try:
    from .research_event_journal import append_event, _root as data_root
except ImportError:
    from research_event_journal import append_event, _root as data_root

PROVIDERS = frozenset(("serper", "serpapi"))
MODES = frozenset(("web", "maps"))
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
COUNTRY = re.compile(r"^[A-Z]{2}$")
LANG = re.compile(r"^[a-z]{2,3}$")
MAX_BODY = 4 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow third-party redirects; a SerpApi query URL contains a secret."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("PROVIDER_REDIRECT_BLOCKED")


def _key_ready(provider: str) -> bool:
    """Unsubstituted WorkBuddy token placeholders never count as configured credentials."""
    name = "SERPAPI_API_KEY" if provider == "serpapi" else "SERPER_API_KEY"
    value = os.environ.get(name, "").strip()
    return bool(value) and not re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", value)

def _configuration() -> dict:
    """Capability discovery only, never disclose tokens or send a provider request."""
    return {
        "serper": {"configured": _key_ready("serper")},
        "serpapi": {"configured": _key_ready("serpapi")},
        "network_enabled": os.environ.get("MARKETOPS_SEARCH_ALLOW_NETWORK") == "1",
        "max_total_reserved_calls": _number("MARKETOPS_SEARCH_MAX_CALLS"),
        "transport": "stdio",
        "guarantees": [
            "one explicit provider per call",
            "no automatic cross-provider fallback",
            "budget reservations persist in local SQLite across restart",
            "all results are discovery candidates, not verified evidence",
        ],
    }


def _number(name: str) -> int:
    raw = os.environ.get(name, "0").strip()
    if not raw.isdecimal():
        raise ValueError(f"{name} must be a nonnegative integer configured by operator")
    return int(raw)


def _check_args(provider: str, query: str, country: str, language: str,
                run_id: str, operation_id: str, mode: str, limit: int) -> None:
    if provider not in PROVIDERS:
        raise ValueError("provider must be serpapi or serper; auto/fallback is not supported")
    if mode not in MODES:
        raise ValueError("mode must be web or maps")
    if not isinstance(query, str) or not (2 <= len(query.strip()) <= 250):
        raise ValueError("query must be 2..250 characters")
    if not COUNTRY.fullmatch(country) or not LANG.fullmatch(language):
        raise ValueError("country is ISO uppercase and language is a lowercase primary tag")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
        raise ValueError("limit must be 1..20")
    for name, value in (("run_id", run_id), ("operation_id", operation_id)):
        if not isinstance(value, str) or not ID.fullmatch(value):
            raise ValueError(f"{name} must be an opaque identifier")
    if os.environ.get("MARKETOPS_SEARCH_ALLOW_NETWORK") != "1":
        raise ValueError("SEARCH_DISABLED: operator must enable network usage before paid calls")
    if not _key_ready(provider):
        raise ValueError("PROVIDER_KEY_MISSING: configure the selected vendor only")
    if _number("MARKETOPS_SEARCH_MAX_CALLS") < 1:
        raise ValueError("BUDGET_NOT_APPROVED: operator must set positive max calls")


def _reserve(root: Path, *, run_id: str, operation_id: str, fingerprint: str,
             provider: str, limit: int) -> None:
    """Atomic, persistent attempt cap; attempted/unknown calls never refunded on retry."""
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(state / "dual-search-ledger.sqlite3", timeout=20)) as db:
        db.execute("PRAGMA busy_timeout=20000")
        db.execute("""CREATE TABLE IF NOT EXISTS search_attempts (
            run_id TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
            provider TEXT NOT NULL, reserved_at TEXT NOT NULL,
            state TEXT NOT NULL,
            PRIMARY KEY (run_id, operation_id)
        )""")
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT fingerprint FROM search_attempts "
                              "WHERE run_id=? AND operation_id=?", (run_id, operation_id)).fetchone()
        if previous:
            if previous[0] != fingerprint:
                raise ValueError("OPERATION_ID_COLLISION: input for reserved id changed")
            raise ValueError("ALREADY_RESERVED: request may already have been billed; inspect receipt")
        used = db.execute("SELECT COUNT(*) FROM search_attempts").fetchone()[0]
        if used >= limit:
            raise ValueError("SEARCH_BUDGET_EXHAUSTED: operator must approve a new ceiling")
        db.execute(
            "INSERT INTO search_attempts VALUES (?,?,?,?,?,?)",
            (run_id, operation_id, fingerprint, provider,
             datetime.now(timezone.utc).isoformat(), "reserved"),
        )
        db.commit()


def _request(provider: str, mode: str, q: str, country: str,
             language: str, limit: int, opener=None) -> dict:
    """Fixed allowlisted HTTPS origins, never follow search URLs or expose API secrets."""
    if provider == "serper":
        endpoint = ("https://google.serper.dev/search" if mode == "web"
                    else "https://google.serper.dev/places")
        body = json.dumps({"q": q, "gl": country.lower(),
                           "hl": language, "num": limit}).encode("utf-8")
        req = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={"X-API-KEY": os.environ["SERPER_API_KEY"],
                     "Content-Type": "application/json"},
        )
    else:
        params = {"engine": "google" if mode == "web" else "google_maps",
                  "q": q, "gl": country.lower(), "hl": language,
                  "api_key": os.environ["SERPAPI_API_KEY"]}
        if mode == "maps":
            params["type"] = "search"
        else:
            params["num"] = str(limit)
        endpoint = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(endpoint, method="GET")
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect()).open
    try:
        with opener(req, timeout=20) as resp:
            if getattr(resp, "status", 200) != 200:
                raise ValueError("PROVIDER_HTTP_ERROR: response was not HTTP 200")
            raw = resp.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise ValueError("PROVIDER_BODY_TOO_LARGE")
    except urllib.error.HTTPError as exc:
        # Do not stringify exc: SerpApi GET request URL includes api_key.
        raise ValueError(f"PROVIDER_HTTP_ERROR_{exc.code}") from None
    except urllib.error.URLError:
        raise ValueError("PROVIDER_CONNECTION_ERROR") from None
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("PROVIDER_INVALID_JSON") from None
    if not isinstance(payload, dict):
        raise ValueError("PROVIDER_INVALID_BODY")
    if payload.get("error"):
        # Never include raw error body: it might echo a request/key.
        raise ValueError("PROVIDER_DECLARED_ERROR")
    if provider == "serpapi" and payload.get("search_metadata", {}).get("status") in ("Processing", "Error"):
        raise ValueError("PROVIDER_NOT_SUCCESSFUL")
    return payload


def _normalize(provider: str, mode: str, data: dict, limit: int) -> list[dict]:
    if provider == "serper":
        items = data.get("organic", []) if mode == "web" else data.get("places", [])
    else:
        items = data.get("organic_results", []) if mode == "web" else data.get("local_results", [])
    if not isinstance(items, list):
        raise ValueError("PROVIDER_INVALID_RESULT_LIST")
    normalized = []
    for row in items:
        if not isinstance(row, dict):
            continue
        place = mode == "maps"
        name = row.get("title") or row.get("name") or ""
        # A map venue may have no direct external website.
        href = row.get("link") or row.get("website") or ""
        if not isinstance(href, str):
            href = ""
        parsed = urllib.parse.urlsplit(href)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            href = ""
        position = row.get("position") or row.get("rank") or len(normalized) + 1
        geo = row.get("gps_coordinates") or {}
        if not isinstance(geo, dict):
            geo = {}
        normalized.append({
            "status": "discovered",
            "provider": provider,
            "kind": "place_candidate" if place else "web_candidate",
            "title": str(name)[:500],
            "url": str(href)[:2000],
            "snippet": str(row.get("snippet") or row.get("description") or "")[:1000],
            "rank": position if isinstance(position, int) else len(normalized) + 1,
            "address": str(row.get("address") or "")[:500] if place else "",
            "place_id": str(row.get("place_id") or row.get("placeId") or "")[:240] if place else "",
            "data_id": str(row.get("data_id") or "")[:240] if place else "",
            "latitude": geo.get("latitude") or row.get("latitude") if place else None,
            "longitude": geo.get("longitude") or row.get("longitude") if place else None,
        })
        if len(normalized) >= limit:
            break
    return normalized


def search(provider: str, query: str, country: str, language: str,
           run_id: str, operation_id: str, mode: str = "web",
           limit: int = 10, opener=None) -> dict:
    """Search one selected provider and store a bounded discovery-only receipt."""
    _check_args(provider, query, country, language, run_id, operation_id, mode, limit)
    root = data_root(None)  # explicit customer data root, outside plugin checkout
    request_meta = {"provider": provider, "query": query.strip(), "mode": mode,
                    "country": country, "language": language, "limit": limit}
    fingerprint = hashlib.sha256(
        json.dumps(request_meta, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    _reserve(root, run_id=run_id, operation_id=operation_id,
             fingerprint=fingerprint, provider=provider,
             limit=_number("MARKETOPS_SEARCH_MAX_CALLS"))
    # An operation reservation counts even if provider fails. No blind retries.
    append_event(root, run_id=run_id, operation_id=operation_id,
                 stage="discovery", kind="tool", status="started", provider=provider)
    try:
        response = _request(provider, mode, query.strip(), country, language, limit, opener=opener)
        hits = _normalize(provider, mode, response, limit)
        normalized = {
            "schema": "marketops.search.discovery.v1",
            "run_id": run_id, "operation_id": operation_id, **request_meta,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "result_count": len(hits), "results": hits,
            "source_status": "discovered_not_verified",
            "business_evidence": False,
        }
        target_dir = root / "search-receipts" / run_id
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = target_dir / (hashlib.sha256(operation_id.encode()).hexdigest() + ".json")
        content = json.dumps(normalized, ensure_ascii=False, indent=2).encode("utf-8")
        if target.exists():
            raise ValueError("RECEIPT_ALREADY_EXISTS")
        fd, filename = tempfile.mkstemp(prefix=".receipt-", dir=target_dir)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, target)
        finally:
            if os.path.exists(filename):
                os.unlink(filename)
        ref = "search-" + hashlib.sha256((run_id + "/" + operation_id).encode()).hexdigest()[:32]
        append_event(root, run_id=run_id, operation_id=operation_id,
                     stage="discovery", kind="tool", status="succeeded",
                     provider=provider, receipt_ref=ref)
        return {"status": "DISCOVERED_NOT_VERIFIED", "provider": provider,
                "operation_id": operation_id, "result_count": len(hits),
                "results": hits, "receipt_ref": ref, "receipt_sha256": hashlib.sha256(content).hexdigest(),
                "calls_reserved": 1}
    except Exception as exc:
        try:
            append_event(root, run_id=run_id, operation_id=operation_id,
                         stage="discovery", kind="tool", status="unknown",
                         provider=provider, reason_code="provider_or_receipt_failed")
        except Exception:
            pass
        if isinstance(exc, ValueError):
            raise
        # Never expose the SerpApi URL, which includes the key.
        raise ValueError("PROVIDER_OR_RECEIPT_ERROR") from None


def main() -> None:
    try:
        from mcp.server import MCPServer
    except ImportError:
        raise SystemExit("Install the reviewed Python MCP SDK (mcp>=2.2,<3) before starting this stdio server.")
    server = MCPServer("MarketOps Lite Dual Search")

    @server.tool()
    def marketops_search_capabilities() -> dict:
        """Show available Serper/SerpApi search connections, with no secrets or paid calls."""
        return _configuration()

    @server.tool()
    def marketops_search(provider: str, query: str, country: str, language: str,
                         run_id: str, operation_id: str, mode: str = "web",
                         limit: int = 10) -> dict:
        """One approved discovery query using *exactly one* SerpApi or Serper account.

        mode=web searches URL leads; mode=maps finds place candidates. Neither
        verifies a business, venue, ownership relationship, email or role.
        The operator must configure network enablement and cumulative call ceiling.
        """
        return search(provider, query, country, language, run_id, operation_id, mode, limit)

    server.run()


if __name__ == "__main__":
    main()

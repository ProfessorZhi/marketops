#!/usr/bin/env python3
"""DeepSeek Search as a MarketOps provider: one more independent web index.

DeepSeek's Search API (``POST https://api.deepseek.com/v1/tools/search``, bearer
key, up to 50 results per request at one price, ``site:``/``lang:`` operators)
runs beside Serper and SerpApi in a multi-route search, not behind them: each
route finds pages the others miss, and a route that fails or runs out of money
only drops out of that one search.

Same discipline as the Serper adapter:

* the key arrives as an argument, never from the environment here;
* one dispatch per request, never retried inside the adapter;
* a refusal before sending is BLOCKED (no charge), a failure after sending is
  FAILED, and a send with no answer is UNKNOWN (the charge stays held);
* results are discovery candidates, never business evidence.

DeepSeek bills only successful, non-empty searches ($0.75 per 1,000 requests at
the time of writing); an empty result list is not charged.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

import research_contract

PROVIDER = "deepseek"
TRANSPORT = "rest"
ENDPOINT = "https://api.deepseek.com/v1/tools/search"
FETCH_ENDPOINT = "https://api.deepseek.com/v1/tools/fetch"
CAPABILITIES: tuple[str, ...] = ("deepseek.search", "deepseek.fetch_page")
ALLOWED_PARAMS: Mapping[str, frozenset[str]] = {
    "deepseek.search": frozenset({"query", "limit", "country", "language", "mode"}),
    "deepseek.fetch_page": frozenset({"url"}),
}
#: Fetch returns Markdown with punctuation escaped ("jean\\.dupont@club\\.fr");
#: the page text is kept as the reader would see it.
_MD_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|<>~;,:@&=/'\"%$?])")
MAX_BODY = 8 * 1024 * 1024
TIMEOUT_SECONDS = 30.0
MAX_QUERY_CHARS = 128   # the API's own limit
MAX_LIMIT = 50

OUTCOME_OK, OUTCOME_BLOCKED, OUTCOME_FAILED, OUTCOME_UNKNOWN = "OK", "BLOCKED", "FAILED", "UNKNOWN"

#: Refusals before anything is sent, and the contract code each one means.
CONTRACT_ANALOGUE: Mapping[str, str] = {
    "DEEPSEEK_TRANSPORT_UNBOUND": "CAPABILITY_NOT_AVAILABLE",
    "DEEPSEEK_CREDENTIAL_ABSENT": "CAPABILITY_NOT_AVAILABLE",
    "DEEPSEEK_PARAMS_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "DEEPSEEK_QUERY_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "DEEPSEEK_MODE_UNSUPPORTED": "CAPABILITY_PARAM_NOT_ALLOWED",
}
#: Failures after the request left: FAILED (or UNKNOWN), never BLOCKED.
POST_DISPATCH_CODES: frozenset[str] = frozenset({
    "DEEPSEEK_UPSTREAM_FAILED", "DEEPSEEK_AMBIGUOUS_TIMEOUT", "DEEPSEEK_BODY_INVALID",
    "DEEPSEEK_BODY_TOO_LARGE", "DEEPSEEK_NO_BALANCE",
    "DEEPSEEK_FETCH_NO_CONTENT", "DEEPSEEK_FETCH_REFUSED", "DEEPSEEK_FETCH_TIMEOUT",
    "DEEPSEEK_RATE_LIMITED",
})
ADAPTER_LOCAL_REASONS: frozenset[str] = frozenset(CONTRACT_ANALOGUE) | POST_DISPATCH_CODES


@dataclass(frozen=True)
class DeepSeekResult:
    outcome: str
    payload: Mapping[str, Any] | None = None
    artifacts: tuple[str, ...] = ()
    upstream_ref: str | None = None
    actual_usd: Decimal | None = None
    actual_source: str = "NONE"
    quota_units_used: Decimal | None = None
    transport: str = TRANSPORT
    schema_verdict: str = "UNVERIFIED"
    caveats: tuple[str, ...] = ()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def capabilities() -> tuple[str, ...]:
    return CAPABILITIES


def _query(value: Any, language: str) -> str:
    text = " ".join(str(value or "").split())
    # Language as DeepSeek's own operator, when the caller gave one and the query
    # does not already say it; the API has no separate locale field.
    if language and "lang:" not in text:
        candidate = f"{text} lang:{language}"
        if len(candidate) <= MAX_QUERY_CHARS:
            text = candidate
    if not 2 <= len(text) <= MAX_QUERY_CHARS:
        raise ValueError("DEEPSEEK_QUERY_INVALID")
    return text


class DeepSeekAdapter:
    """``ProviderAdapter`` for DeepSeek Search. ``provider`` is a class attribute."""

    provider = PROVIDER

    def __init__(self, *, api_key: str | None = None, transport: Any | None = None) -> None:
        self._api_key = api_key if isinstance(api_key, str) and api_key.strip() else None
        self._transport = transport

    def capabilities(self) -> tuple[str, ...]:
        return CAPABILITIES

    def probe_schema(self) -> Mapping[str, object]:
        return {"status": "AUTH_REQUIRED", "provider": PROVIDER, "transport": TRANSPORT,
                "upstream_tools_list": None, "schema_verdict": "UNVERIFIED",
                "detail": "REST API; no tool list to snapshot"}

    def cancel(self, upstream_ref: str) -> DeepSeekResult:
        return DeepSeekResult(outcome=OUTCOME_BLOCKED,
                              payload={"reason": "CAPABILITY_NOT_AVAILABLE",
                                       "upstream_ref": str(upstream_ref or "")},
                              caveats=("CAPABILITY_NOT_AVAILABLE",))

    def execute(self, req: Any) -> DeepSeekResult:
        capability_id = str(getattr(req, "capability_id", "") or "")
        params = getattr(req, "params", None)
        if capability_id not in CAPABILITIES:
            return self._refusal("DEEPSEEK_PARAMS_INVALID", f"unknown capability {capability_id!r}",
                                 capability_id=capability_id, contract="CAPABILITY_UNKNOWN")
        if not isinstance(params, Mapping) or set(map(str, params)) - ALLOWED_PARAMS[capability_id]:
            return self._refusal("DEEPSEEK_PARAMS_INVALID", "unexpected parameters",
                                 capability_id=capability_id)
        if capability_id == "deepseek.fetch_page":
            return self._fetch(params, capability_id, req)
        if str(params.get("mode") or "web") != "web":
            # No maps or news vertical: build venue lists from Serper's maps.
            return self._refusal("DEEPSEEK_MODE_UNSUPPORTED", "DeepSeek Search is web only",
                                 capability_id=capability_id)
        try:
            query = _query(params.get("query"), str(params.get("language") or "").lower()[:3])
            limit = int(params.get("limit") or 10)
        except (TypeError, ValueError):
            return self._refusal("DEEPSEEK_QUERY_INVALID", "query 2-128 characters",
                                 capability_id=capability_id)
        limit = max(1, min(MAX_LIMIT, limit))
        if self._transport is None:
            return self._refusal("DEEPSEEK_TRANSPORT_UNBOUND", "no egress bound",
                                 capability_id=capability_id)
        if self._api_key is None:
            return self._refusal("DEEPSEEK_CREDENTIAL_ABSENT", "no key", capability_id=capability_id)
        body = json.dumps({"query": query, "count": limit, "need_content": False}).encode()
        request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"})
        try:
            response = self._transport(request, TIMEOUT_SECONDS)
            with response as handle:
                raw = handle.read(MAX_BODY + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 402:
                return self._failure("DEEPSEEK_NO_BALANCE", "HTTP 402 insufficient balance",
                                     capability_id=capability_id)
            return self._failure("DEEPSEEK_UPSTREAM_FAILED", f"HTTP {exc.code}",
                                 capability_id=capability_id)
        except TimeoutError:
            return self._failure("DEEPSEEK_AMBIGUOUS_TIMEOUT", "no answer after sending",
                                 capability_id=capability_id, unknown=True)
        except urllib.error.URLError as exc:
            if research_contract.never_sent(exc):
                return self._refusal("DEEPSEEK_TRANSPORT_UNBOUND", "connection never established",
                                     capability_id=capability_id, contract="EGRESS_REFUSED")
            return self._failure("DEEPSEEK_UPSTREAM_FAILED", "connection failed",
                                 capability_id=capability_id, unknown=True)
        if len(raw) > MAX_BODY:
            return self._failure("DEEPSEEK_BODY_TOO_LARGE", f"{len(raw)} bytes",
                                 capability_id=capability_id)
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._failure("DEEPSEEK_BODY_INVALID", "not JSON", capability_id=capability_id)
        rows = data.get("results") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return self._failure("DEEPSEEK_BODY_INVALID", "no results list",
                                 capability_id=capability_id)
        hits = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            url = str(row.get("url") or "")
            if not re.match(r"^https?://[^/\s]+", url):
                continue
            hits.append({
                "status": "discovered", "provider": PROVIDER, "kind": "search_candidate",
                "search_mode": "web", "title": str(row.get("title") or "")[:500],
                "url": url[:2000], "snippet": str(row.get("snippet") or "")[:1000],
                "summary": str(row.get("summary") or "")[:1200], "rank": len(hits) + 1,
                "rerank_score": row.get("rerank_score"), "authority": row.get("authority"),
                "date": row.get("date")})
            if len(hits) >= limit:
                break
        payload = {
            "schema": "marketops.deepseek.discovery.v1", "provider": PROVIDER,
            "transport": TRANSPORT, "capability_id": capability_id,
            "run_id": str(getattr(req, "run_id", "") or ""),
            "operation_id": str(getattr(req, "operation_id", "") or ""),
            "mode": "web", "query": query, "limit": limit, "result_count": len(hits),
            "results": hits, "source_status": "discovered_not_verified",
            "business_evidence": False,
            # Empty searches are not billed by DeepSeek.
            "billable": bool(hits),
            "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        }
        return DeepSeekResult(outcome=OUTCOME_OK, payload=payload,
                              quota_units_used=Decimal(1 if hits else 0),
                              caveats=("DISCOVERY_CANDIDATES_NOT_VERIFIED_BUSINESS_EVIDENCE",))

    def _fetch(self, params, capability_id, req) -> DeepSeekResult:
        """One page as Markdown. Billed only when content comes back: a page DeepSeek
        cannot read (404), refuses (403) or a timeout costs nothing and is FAILED,
        so the fallback chain moves on."""
        url = str(params.get("url") or "")
        if not re.match(r"^https?://[^/\s@]+", url) or len(url) > 8192:
            return self._refusal("DEEPSEEK_PARAMS_INVALID", "an http(s) URL", capability_id=capability_id)
        if self._transport is None:
            return self._refusal("DEEPSEEK_TRANSPORT_UNBOUND", "no egress bound", capability_id=capability_id)
        if self._api_key is None:
            return self._refusal("DEEPSEEK_CREDENTIAL_ABSENT", "no key", capability_id=capability_id)
        request = urllib.request.Request(FETCH_ENDPOINT, data=json.dumps({"url": url}).encode(),
                                         method="POST", headers={
            "Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"})
        try:
            response = self._transport(request, 90.0)
            with response as handle:
                raw = handle.read(MAX_BODY + 1)
        except urllib.error.HTTPError as exc:
            code = {402: "DEEPSEEK_NO_BALANCE", 403: "DEEPSEEK_FETCH_REFUSED",
                    404: "DEEPSEEK_FETCH_NO_CONTENT", 429: "DEEPSEEK_RATE_LIMITED",
                    504: "DEEPSEEK_FETCH_TIMEOUT"}.get(exc.code, "DEEPSEEK_UPSTREAM_FAILED")
            return self._failure(code, f"HTTP {exc.code}", capability_id=capability_id)
        except TimeoutError:
            # DeepSeek bills no timeout: FAILED, not an unknown charge.
            return self._failure("DEEPSEEK_FETCH_TIMEOUT", "no answer", capability_id=capability_id)
        except urllib.error.URLError as exc:
            if research_contract.never_sent(exc):
                return self._refusal("DEEPSEEK_TRANSPORT_UNBOUND", "connection never established",
                                     capability_id=capability_id, contract="EGRESS_REFUSED")
            return self._failure("DEEPSEEK_UPSTREAM_FAILED", "connection failed",
                                 capability_id=capability_id)
        if len(raw) > MAX_BODY:
            return self._failure("DEEPSEEK_BODY_TOO_LARGE", f"{len(raw)} bytes", capability_id=capability_id)
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._failure("DEEPSEEK_BODY_INVALID", "not JSON", capability_id=capability_id)
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, str) or not content.strip():
            return self._failure("DEEPSEEK_FETCH_NO_CONTENT", "empty page", capability_id=capability_id)
        payload = {
            "schema": "marketops.deepseek.fetch.v1", "provider": PROVIDER, "transport": TRANSPORT,
            "capability_id": capability_id, "url": url, "title": str(data.get("title") or "")[:500],
            # The evidence reader takes "markdown" as the page text.
            "markdown": _MD_ESCAPE.sub(r"\1", content),
            "run_id": str(getattr(req, "run_id", "") or ""),
            "operation_id": str(getattr(req, "operation_id", "") or ""),
            "source_status": "vendor_extracted_markdown", "business_evidence": False,
            "billable": True, "raw_response_sha256": hashlib.sha256(raw).hexdigest()}
        return DeepSeekResult(outcome=OUTCOME_OK, payload=payload, quota_units_used=Decimal(1),
                              caveats=("VENDOR_EXTRACTED_MARKDOWN_NOT_RAW_HTML",))

    def _refusal(self, code, detail, *, capability_id="", contract=None) -> DeepSeekResult:
        contract_code = contract or CONTRACT_ANALOGUE.get(code, "CAPABILITY_PARAM_NOT_ALLOWED")
        return DeepSeekResult(outcome=OUTCOME_BLOCKED,
                              payload={"reason": code, "contract_reason_code": contract_code,
                                       "detail": f"{code}: {detail}",
                                       "capability_id": capability_id or None},
                              caveats=(contract_code, "refused locally; nothing was sent"))

    def _failure(self, code, detail, *, capability_id="", unknown=False) -> DeepSeekResult:
        return DeepSeekResult(outcome=OUTCOME_UNKNOWN if unknown else OUTCOME_FAILED,
                              payload={"reason": code, "detail": detail,
                                       "capability_id": capability_id or None,
                                       "charge_state": "UNKNOWN" if unknown else "UNSETTLED_NOT_UNKNOWN"},
                              caveats=(code, "the request was sent"))


__all__ = ["ADAPTER_LOCAL_REASONS", "CAPABILITIES", "DeepSeekAdapter", "DeepSeekResult",
           "ENDPOINT", "FETCH_ENDPOINT", "PROVIDER", "POST_DISPATCH_CODES", "capabilities"]

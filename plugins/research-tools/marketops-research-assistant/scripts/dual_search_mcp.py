#!/usr/bin/env python3
"""Optional local dual-search MCP (stdio): SerpApi OR Serper, explicit per-call choice.

The same search capability can use either vendor, never silent failover or double billing.
This is a bounded *search discovery* adapter, not the full MarketOps Research MCP.
Run inside a customer-controlled process with explicit operator-set environment.

**Which Serper categories exist is a registry, not a dict literal.**
``serper_modes`` owns the declared category set, the endpoint each one is sent
to, and — the part that used to be missing — the evidence behind each claim.
This module no longer decides unilaterally that ``maps`` means ``/places``;
it asks, and the Ask must resolve before a reservation is taken.

**One query is one key.** The reservation fingerprint is
``search_canonical.operation_signature``, which excludes the transport. A REST
call and the same logical call over an embedded MCP therefore land on one
reservation instead of two charges. An operation reservation still counts even
if the provider then fails; there are no blind retries.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import research_usage_ledger as usage_ledger

try:
    from .research_event_journal import append_event, _root as data_root
except ImportError:
    from research_event_journal import append_event, _root as data_root

import search_canonical
import serpapi_engine_catalog
import serper_modes

PROVIDERS = frozenset(("serper", "serpapi"))
# The shared mode *vocabulary*. Which member of it a vendor actually serves is
# a per-vendor question: `serper_modes` answers it for Serper, and the SerpApi
# engine table below answers it for the REST compatibility path.
MODES = frozenset(("web", "maps", "images", "news", "videos",
                   "shopping", "scholar", "patents", "autocomplete"))
SERPER_MODES = serper_modes.ALL_MODES
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
COUNTRY = re.compile(r"^[A-Z]{2}$")
LANG = re.compile(r"^[a-z]{2,3}$")
MAX_BODY = 4 * 1024 * 1024

# The legacy Google REST engine table. It is *not* the SerpApi engine catalog:
# SerpApi's official MCP exposes ~100+ engines as MCP resources, and only a
# handful of common Google types have a rest.json equivalence here. Keeping
# the two apart is what stops this table being read as "SerpApi coverage".
SERPAPI_REST_ENGINES = {
    "web": "google", "maps": "google_maps",
    "images": "google_images", "news": "google_news",
    "videos": "google_videos", "shopping": "google_shopping",
    "scholar": "google_scholar", "patents": "google_patents",
    "autocomplete": "google_autocomplete",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow third-party redirects; a SerpApi query URL contains a secret."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("PROVIDER_REDIRECT_BLOCKED")


def proxy_handler() -> urllib.request.ProxyHandler:
    """The proxy MarketOps itself was told to use, never the machine's ambient one.

    urllib otherwise reads HTTP(S)_PROXY and, on Windows, the Internet Settings
    registry. A stale local proxy there (a VPN or IDE that moved port) turned every
    search into HTTP 502 on a customer machine while direct access worked. Only
    ``customer_setup.py --proxy`` (MARKETOPS_EGRESS_UPSTREAM_PROXY) routes through one.
    """
    upstream = os.environ.get("MARKETOPS_EGRESS_UPSTREAM_PROXY", "").strip()
    return urllib.request.ProxyHandler({"http": upstream, "https": upstream} if upstream else {})


# The operator's escape hatch for the SerpApi REST compatibility path. It is
# not in any shipped manifest's env_vars on purpose: a setting that appears in
# a generated connector template is a setting a customer turns on by filling a
# blank, which is the opposite of "requires a decision".
SERPAPI_REST_ENABLE = "MARKETOPS_SERPAPI_REST_ENABLE"


def _serpapi_rest_enabled() -> bool:
    return os.environ.get(SERPAPI_REST_ENABLE, "").strip() == "1"


def _key_ready(provider: str) -> bool:
    """Unsubstituted WorkBuddy token placeholders never count as configured credentials."""
    name = "SERPAPI_API_KEY" if provider == "serpapi" else "SERPER_API_KEY"
    value = os.environ.get(name, "").strip()
    return bool(value) and not re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", value)

def _configuration() -> dict:
    """Capability discovery only, never disclose tokens or send a provider request."""
    return {
        "serper": {"configured": _key_ready("serper"), "availability": "ENABLED",
                   "endpoint_sample": _serper_endpoint("web"),
                   "declared_endpoint_paths": len(serper_modes.declared_endpoint_paths())},
        "serpapi": {"configured": _key_ready("serpapi"),
                    "availability": ("ENABLED" if _serpapi_rest_enabled() else "DISABLED"),
                    "enable_with": SERPAPI_REST_ENABLE,
                    "note": ("Legacy Google REST compatibility table only; it is NOT "
                             "SerpApi's engine catalog. The managed path is the embedded "
                             "official MCP.")},
        "network_enabled": os.environ.get("MARKETOPS_SEARCH_ALLOW_NETWORK") == "1",
        "max_total_reserved_calls": _number("MARKETOPS_SEARCH_MAX_CALLS"),
        "transport": "stdio",
        # A capability answer that reported nine Serper categories without
        # saying on what evidence was the audited gap. Declared coverage and
        # its verification state travel together now; neither is a claim about
        # this account's entitlement, price or result quality.
        "serper_coverage": serper_modes.coverage_report(),
        "serpapi_mcp": {
            "engine_index_uri": serpapi_engine_catalog.INDEX_URI,
            "engine_schema_uri_template": serpapi_engine_catalog.SCHEMA_URI_TEMPLATE,
            # Literally "this capability probe did not load it" — not "it does
            # not exist". A capability answer must stay read-only and offline,
            # so it reports the contract and leaves the read to a caller who
            # asked for a specific engine.
            "engine_catalog_loaded": False,
            "engine_catalog_state": "NOT_LOADED_BY_CAPABILITY_PROBE",
            "discovery": "LAZY_RESOURCE_READ_ON_DEMAND",
            "note": ("The official MCP publishes per-engine parameter schemas as "
                     "resources. This adapter reads the index, then one engine at a "
                     "time on request; it never loads every engine into context."),
        },
        "provider_calls_made": 0,
        "guarantees": [
            "one explicit provider per call",
            "no automatic cross-provider fallback",
            "budget reservations persist in local SQLite across restart",
            "one query is one reservation key, independent of transport",
            "all results are discovery candidates, not verified evidence",
        ],
    }


def _number(name: str) -> int:
    raw = os.environ.get(name, "0").strip()
    if not raw.isdecimal():
        raise ValueError(f"{name} must be a nonnegative integer configured by operator")
    return int(raw)


def _serper_endpoint(mode: str) -> str:
    """The declared endpoint for a Serper category. The registry is the source."""
    return "https://google.serper.dev" + serper_modes.mode_spec(mode).endpoint_path


def _check_args(provider: str, query: str, country: str, language: str,
                run_id: str, operation_id: str, mode: str, limit: int,
                page: int = 1) -> None:
    if provider not in PROVIDERS:
        raise ValueError("provider must be serpapi or serper; auto/fallback is not supported")
    if mode not in MODES:
        raise ValueError("UNSUPPORTED_SEARCH_MODE")
    # Page validity is a property of the category, so it is checked here —
    # before the reservation, and before either key or budget is inspected.
    # A refused page must not cost a paid attempt.
    if provider == "serper":
        serper_modes.mode_spec(mode)          # category must be a declared one
        serper_modes.assert_page_allowed(mode, page)
    else:
        if page != 1:
            # The legacy Google REST table has no verified paging parameter. A
            # silently ignored `page` would look like a second page of results.
            raise ValueError("SERPAPI_PAGINATION_UNVERIFIED")
        if not _serpapi_rest_enabled():
            # D-2: the SerpApi REST adapter is a *second* route to the same
            # paid vendor, so a default install does not have it. It is kept,
            # not deleted: the product brief asks for it to survive until the
            # official MCP contract is validated. Turning it on is an operator
            # decision, named in the refusal itself.
            raise ValueError("SERPAPI_REST_DISABLED")
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



# Ceilings are persisted in the ledger, so stating them once per process is enough.
# Re-stating them on every reservation is a write on the hot path of an
# eight-process race, and the contention it adds is paid by the test that is
# *about* contention.
_CAPS_STATED: set = set()


def _state_caps(ledger, project: str, run_id: str, ceiling: int,
                project_ceiling: int | None = None, root=None) -> None:
    key = (str(getattr(ledger, "_path", ledger)), project, run_id, ceiling, project_ceiling)
    if key in _CAPS_STATED:
        return
    # A local consent record owns the run/project bounds when it exists: the two
    # ceilings are independent there, and the record may also carry USD caps.
    # Reading them from this adapter's environment variables instead would make
    # one run's promise disagree with the other's, and a bare ``set_cap`` here
    # would erase the dollar column. Both are failures of the same seam.
    if root is not None:
        import research_runtime_setup as runtime
        if runtime.config(root):
            runtime.state_ledger_caps(root, run_id, project=project, ledger=ledger)
            _CAPS_STATED.add(key)
            return
    runtime_setup = None
    try:
        import research_runtime_setup as runtime_setup
    except Exception:  # noqa: BLE001 - legacy path stays usable without the module
        runtime_setup = None
    if runtime_setup is not None:
        runtime_setup.set_call_cap_preserving_money(
            ledger, "run", f"{project}/{run_id}", ceiling_calls=ceiling,
            source="MARKETOPS_SEARCH_MAX_CALLS")
        if project_ceiling is not None:
            runtime_setup.set_call_cap_preserving_money(
                ledger, "project", project, ceiling_calls=project_ceiling,
                source="MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS")
    else:
        ledger.set_cap("run", f"{project}/{run_id}", ceiling_calls=ceiling,
                       source="MARKETOPS_SEARCH_MAX_CALLS")
        if project_ceiling is not None:
            ledger.set_cap("project", project, ceiling_calls=project_ceiling,
                           source="MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS")
    _CAPS_STATED.add(key)


def _project_limit() -> int | None:
    """The optional project-level ceiling, or None when the operator set none.

    An unset variable means "no *additional* project bound", not "a bound of
    zero": reading a missing budget as zero would refuse every call and report it
    as an exhausted budget, which is a lie about why. The per-run ceiling stays
    mandatory — ``_check_args`` refuses a search budget below 1 before any of
    this — so this is not a fail-open path.
    """
    raw = os.environ.get("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", "").strip()
    if not raw.isdecimal():
        return None
    value = int(raw)
    return value if value >= 1 else None


def _reserve(root: Path, *, run_id: str, operation_id: str, fingerprint: str,
             provider: str, limit: int, ledger_args: dict | None = None) -> None:
    """Atomic, persistent attempt cap; attempted/unknown calls never refunded on retry.

    Two keys are kept apart, and the distinction is load-bearing.

    ``fingerprint`` is the **provider execution key**: callers build it from the
    provider, the operation and the canonical query, so that the same query over
    REST and over the embedded official MCP is one spend opportunity rather than
    two. The **logical research intent** is a separate field on the request; it is
    what lets the same objective re-run on a second provider be recognised instead
    of being an invisible duplicate.

    The authoritative reservation is taken in the shared ledger, because it is the
    one writer that sees every transport. The ``search_attempts`` row written here
    is a **receipt**, not the counter: it stays byte-compatible with the historical
    table (contract §9) and carries the financial-state column a dispatched timeout
    needs in order to remain distinguishable from an unused reservation.

    The pre-read below is kept even though the ledger also refuses duplicates,
    because the two refusals are different statements and the operator acts
    differently on each: the ledger says "this run has already paid for this
    operation", the receipt table says "you already hold a receipt for this
    operation id". The ledger owns the money; this table owns the article identity.
    """
    _identity_check(root, run_id, operation_id, fingerprint)
    ledger = usage_ledger.for_root(root)
    project = usage_ledger.project_for_root(root)
    # State the ceilings this adapter is asked to enforce, naming who stated them.
    # A ceiling the ledger was never told about is one it cannot enforce, and an
    # unstated ceiling is not permission.
    _state_caps(ledger, project, run_id, limit, _project_limit(), root=root)
    try:
        ledger.reserve(usage_ledger.OperationRequest(
            # Run-scoped on the adapter side, unique-per-ledger on the ledger's:
            # see research_provider_extensions for why these are reconciled rather
            # than one of them being changed.
            project=project, run_id=run_id, operation_id=f"{run_id}:{operation_id}",
            provider=provider, capability="search", account_ref=provider,
            transport="rest", intent=fingerprint,
            # With no richer identity supplied, the caller's canonical signature
            # *is* the identity: it is what was bought. The receipt's own
            # operation_id is not — it names the row, not the purchase, and using
            # it here made two callers of one query look like two buys.
            args=ledger_args if ledger_args is not None
            else search_canonical.search_operation_args(
                query=fingerprint, capability="search")))
    except usage_ledger.LedgerRefused as exc:
        if exc.code == "DUPLICATE_OPERATION_SIGNATURE":
            raise ValueError("DUPLICATE_SEARCH_SAME_RUN: inspect previous receipt") from None
        # The ledger now raises the contract's ceiling names (§5.3); it used to
        # raise its own. Both are accepted because the adapter's code — which
        # operators and receipts already key on — should not change when the
        # ledger's internal vocabulary does.
        if exc.code in {"CALL_CAP_EXCEEDED", "MONEY_CAP_EXCEEDED", "TIME_CAP_EXCEEDED",
                        "BUDGET_RUN_CEILING", "BUDGET_PROJECT_CEILING",
                        "BUDGET_PROVIDER_CEILING", "BUDGET_OPERATION_CEILING"}:
            raise ValueError(f"SEARCH_BUDGET_EXHAUSTED: {exc}") from None
        raise ValueError(f"{exc.code}: {exc.message}") from None
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    deadline = time.monotonic() + _SCHEMA_RETRY_BUDGET_SECONDS
    with closing(_connect_within(state / "dual-search-ledger.sqlite3", deadline)) as db:
        db.execute("PRAGMA busy_timeout=20000")
        _ensure_search_attempts(db)
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO search_attempts (run_id, operation_id, fingerprint, provider,"
            " reserved_at, state, outcome_code) VALUES (?,?,?,?,?,?,?)",
            (run_id, operation_id, fingerprint, provider,
             datetime.now(timezone.utc).isoformat(), "reserved", "RESERVED"),
        )
        db.commit()


def _connect_within(path, deadline: float):
    """Connect with a timeout that cannot outlive the caller's deadline.

    A retry budget is not a wall bound if the thing being retried blocks: the
    `timeout=20` handed to `sqlite3.connect` is twenty seconds *per attempt*, so
    three attempts could outlive any budget the loop was checking between them.
    The connection is therefore given only what is left of the deadline, and never
    less than a floor that still lets a legitimate acquire succeed.
    """
    remaining = max(0.05, deadline - time.monotonic())
    return sqlite3.connect(path, timeout=min(20.0, remaining))


def _ensure_search_attempts(db) -> None:
    """Create the receipt table and migrate the financial-state column in place.

    Historical rows keep their bytes (contract §9). The added column is what makes
    "may have been billed and cannot yet be proven" a state rather than an absent
    field.

    **Every step here is decided by the table afterwards, not by who won a write.**
    Both operations contend: ``CREATE TABLE`` and ``ALTER TABLE`` need locks that a
    second process holds while it does the same thing, and neither failure is
    distinguishable from a real DDL failure by its exception. The first version
    handled that for the ALTER and left the ``CREATE`` bare, so a fresh database
    under contention still raised ``sqlite3.OperationalError`` at a caller that did
    nothing wrong.

    So the whole initialisation is retried a bounded number of times, and the
    outcome is read back from the schema: present means success regardless of which
    process created it. Exhausting the retries raises a closed-set code rather than
    the driver's message, because a caller cannot branch on
    ``OperationalError: database is locked``.
    """
    last: Exception | None = None
    deadline = time.monotonic() + _SCHEMA_RETRY_BUDGET_SECONDS
    attempt = 0
    while attempt < _SCHEMA_ATTEMPTS and time.monotonic() < deadline:
        attempt += 1
        try:
            _create_receipt_table(db)
            if "outcome_code" in _receipt_columns(db):
                return
            try:
                db.execute("ALTER TABLE search_attempts ADD COLUMN outcome_code TEXT "
                           "NOT NULL DEFAULT 'RESERVED'")
            except sqlite3.Error:
                pass  # decided by the read-back below, not by this exception
            if "outcome_code" in _receipt_columns(db):
                return
            raise ValueError(
                "RECEIPT_SCHEMA_MIGRATION_FAILED: search_attempts.outcome_code is still "
                "absent after the migration was attempted; the receipt layer cannot "
                "record a financial state and will not proceed without one")
        except sqlite3.OperationalError as exc:
            if not _lock_contention(exc):
                raise ValueError(
                    f"RECEIPT_SCHEMA_MIGRATION_FAILED: {type(exc).__name__}: {exc}") from None
            last = exc
            time.sleep(min(_SCHEMA_PAUSE_SECONDS * attempt, 0.1))
    raise ValueError(
        f"RECEIPT_SCHEMA_MIGRATION_BUSY: another process held the receipt database for "
        f"the whole {_SCHEMA_RETRY_BUDGET_SECONDS:.1f}s budget ({attempt} attempts); "
        f"refusing rather than waiting without bound. Last error: {last}")


#: Bounded patience for the receipt schema against other processes.
#:
#: Bounded by **time**, not by a number of attempts. An attempt count is not a bound
#: when the thing being retried can block: a test harness that holds contenders at a
#: barrier for twenty seconds per pass turns "twelve attempts" into four minutes,
#: and the retry loop that was meant to survive contention becomes the reason the
#: caller never returns. Three seconds is enough for real lock contention and far
#: below anything a caller would call a hang.
_SCHEMA_RETRY_BUDGET_SECONDS = 3.0
_SCHEMA_ATTEMPTS = 12
_SCHEMA_PAUSE_SECONDS = 0.0125


def _lock_contention(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).casefold()
    return "locked" in text or "busy" in text


def _create_receipt_table(db) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS search_attempts (
        run_id TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
        provider TEXT NOT NULL, reserved_at TEXT NOT NULL,
        state TEXT NOT NULL,
        PRIMARY KEY (run_id, operation_id)
    )""")


def _receipt_columns(db) -> set:
    return {row[1] for row in db.execute("PRAGMA table_info(search_attempts)")}

def _identity_check(root: Path, run_id: str, operation_id: str,
                    fingerprint: str) -> None:
    """Read-only: has this operation identity already been used?

    Not the gate — the shared ledger is — but it is what lets a refusal keep this
    adapter's exact historical vocabulary instead of a generic duplicate code.
    """
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    # One connection, one read: this runs in every racing process, so the number
    # of open/close cycles here is itself a contention budget.
    deadline = time.monotonic() + _SCHEMA_RETRY_BUDGET_SECONDS
    with closing(_connect_within(state / "dual-search-ledger.sqlite3", deadline)) as db:
        db.execute("PRAGMA busy_timeout=20000")
        _ensure_search_attempts(db)
        previous = db.execute("SELECT fingerprint FROM search_attempts "
                              "WHERE run_id=? AND operation_id=?",
                              (run_id, operation_id)).fetchone()
        # Renaming operation_id must not replay the same provider/search/locale
        # query. A timeout may still have incurred a charge. Reuse the stored
        # receipt or explicitly start a different approved research objective.
        duplicate = db.execute("SELECT 1 FROM search_attempts WHERE run_id=? AND fingerprint=?",
                               (run_id, fingerprint)).fetchone()
    if previous:
        if previous[0] != fingerprint:
            raise ValueError("OPERATION_ID_COLLISION: input for reserved id changed")
        raise ValueError("ALREADY_RESERVED: request may already have been billed; inspect receipt")
    if duplicate:
        raise ValueError("DUPLICATE_SEARCH_SAME_RUN: inspect previous receipt")


def _request(provider: str, mode: str, q: str, country: str,
             language: str, limit: int, page: int = 1, opener=None) -> dict:
    """Fixed allowlisted HTTPS origins, never follow search URLs or expose API secrets."""
    if provider == "serper":
        endpoint = _serper_endpoint(mode)
        payload = {"q": q, "gl": country.lower(), "hl": language, "num": limit}
        # Only a category the registry declares pageable gets a page field, and
        # never for page 1 — a first page sent with `page: 1` is a request shape
        # no receipt has ever covered, and this adapter changes request shapes
        # deliberately or not at all.
        if page > 1 and serper_modes.pagination_param(mode) is not None:
            payload[serper_modes.pagination_param(mode)] = page
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={"X-API-KEY": os.environ["SERPER_API_KEY"],
                     "Content-Type": "application/json"},
        )
    else:
        engine = SERPAPI_REST_ENGINES[mode]
        params = {"engine": engine, "q": q, "gl": country.lower(),
                  "hl": language, "api_key": os.environ["SERPAPI_API_KEY"]}
        if mode == "maps":
            params["type"] = "search"
        else:
            params["num"] = str(limit)
        endpoint = "https://serpapi.com/search.json?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(endpoint, method="GET")
    if opener is None:
        opener = urllib.request.build_opener(NoRedirect(), proxy_handler()).open
    # The refusal code is chosen inside the handler and raised after it. A raise
    # inside `except` re-attaches the caught exception as `__context__`, and a
    # refused HTTPError carries an attacker-controlled reason phrase (and, on the
    # SerpApi GET, a request URL that includes the api_key). `from None` hides it
    # from a printed traceback but not from a consumer that walks the chain, so
    # every handler here records a closed-set code and the raise happens outside.
    #
    # The broad final handler is load-bearing, not defensive padding: `http.client`
    # raises OSErrors and ValueErrors that are not URLError subclasses, and some of
    # them stringify the request target. An unconverted one would reach `search`'s
    # boundary, and there it would be wrapped by an exception whose `__context__`
    # still carries the URL — and therefore the key.
    failure: str | None = None
    raw = b""
    try:
        with opener(req, timeout=20) as resp:
            if getattr(resp, "status", 200) != 200:
                failure = "PROVIDER_HTTP_ERROR: response was not HTTP 200"
            else:
                raw = resp.read(MAX_BODY + 1)
                if len(raw) > MAX_BODY:
                    failure = "PROVIDER_BODY_TOO_LARGE"
    except urllib.error.HTTPError as exc:
        # Do not stringify exc: the SerpApi GET request URL includes api_key.
        failure = f"PROVIDER_HTTP_ERROR_{exc.code}"
    except urllib.error.URLError:
        failure = "PROVIDER_CONNECTION_ERROR"
    except ValueError as exc:
        # `NoRedirect` raises this by design. Anything else that reaches here is
        # not a code this module defined, so it is collapsed rather than echoed.
        failure = ("PROVIDER_REDIRECT_BLOCKED"
                   if str(exc) == "PROVIDER_REDIRECT_BLOCKED"
                   else "PROVIDER_TRANSPORT_ERROR")
    except Exception:
        # Never let a transport exception's message become the raised error or
        # its `__context__`: on the SerpApi GET that message can carry the URL.
        failure = "PROVIDER_TRANSPORT_ERROR"
    if failure is not None:
        raise ValueError(failure)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Raised outside the handler: `JSONDecodeError` embeds a slice of the
        # document, and a provider that echoed the request would put the key in
        # the exception chain.
        payload = None
        failure = "PROVIDER_INVALID_JSON"
    if failure is not None:
        raise ValueError(failure)
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
        # The collection key comes from the registry, so the field we read and
        # the endpoint we called cannot drift apart in two places.
        items = data.get(serper_modes.mode_spec(mode).result_key, [])
    else:
        result_field = {"web": "organic_results", "maps": "local_results",
                        "images": "images_results", "news": "news_results",
                        "videos": "video_results", "shopping": "shopping_results",
                        "scholar": "organic_results", "patents": "organic_results",
                        "autocomplete": "suggestions"}[mode]
        items = data.get(result_field, [])
    if not isinstance(items, list):
        raise ValueError("PROVIDER_INVALID_RESULT_LIST")
    normalized = []
    for row in items:
        if mode == "autocomplete" and isinstance(row, str):
            row = {"title": row}
        if not isinstance(row, dict):
            continue
        place = mode == "maps"
        name = row.get("title") or row.get("name") or row.get("value") or ""
        # A map venue may have no direct external website.
        href = (row.get("link") or row.get("website") or row.get("sourceUrl")
                or row.get("productLink") or "")
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
            "kind": "place_candidate" if place else "search_candidate",
            "search_mode": mode,
            "title": str(name)[:500],
            "url": str(href)[:2000],
            "snippet": str(row.get("snippet") or row.get("description") or row.get("source") or "")[:1000],
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


def _ledger_identity(provider: str, *, query: str, country: str, language: str,
                     mode: str, engine: str | None, limit: int, page: int,
                     account: str) -> dict:
    """The args this adapter hands the ledger.

    SerpApi goes through the one shared builder: its vendor axis is ``engine``,
    and the REST mode is only the local name of an engine (``web`` -> ``google``).
    Keeping ``mode`` out of the identity is what lets the same query over REST and
    over the embedded official MCP land on one ``route_shape``. Serper keeps
    ``mode`` because the category *is* what it buys.
    """
    if provider == "serpapi":
        return search_canonical.serpapi_search_route_args(
            query=query, engine=engine, country=country, language=language,
            num=limit, page=page, account=account)
    return search_canonical.search_operation_args(
        query=query, capability="search", country=country, language=language,
        mode=mode, limit=limit, page=page, extra={"account": account})


def search(provider: str, query: str, country: str, language: str,
           run_id: str, operation_id: str, mode: str = "web",
           limit: int = 10, page: int = 1, account: str = "",
           transport: str = "REST_HTTPS", opener=None) -> dict:
    """Search one selected provider and store a bounded discovery-only receipt."""
    try:
        _check_args(provider, query, country, language, run_id, operation_id,
                    mode, limit, page)
    except ValueError:
        # Save an observable preflight blocker when the supplied operation IDs are
        # safe. No provider charge or attempt reservation has happened yet.
        if isinstance(run_id, str) and isinstance(operation_id, str) and ID.fullmatch(run_id) and ID.fullmatch(operation_id):
            try:
                append_event(data_root(None), run_id=run_id, operation_id=operation_id,
                             stage="discovery", kind="tool", status="blocked",
                             provider=provider if provider in PROVIDERS else "unknown",
                             reason_code="search_preflight_blocked")
            except Exception:
                pass
        raise
    root = data_root(None)  # explicit customer data root, outside plugin checkout
    # The reservation key is transport-independent on purpose: the same query
    # reached over the legacy REST adapter and over the embedded official MCP
    # is one spend opportunity, so it must not reserve twice.
    arguments = search_canonical.canonical_arguments(
        provider=provider, mode=mode, query=query, country=country,
        language=language, limit=limit, page=page,
        engine=SERPAPI_REST_ENGINES.get(mode) if provider == "serpapi" else None,
        account=account, transport=transport,
    )
    fingerprint = search_canonical.operation_signature(arguments)
    request_meta = {"provider": provider, "query": arguments["query"], "mode": mode,
                    "country": country, "language": language, "limit": limit,
                    "page": page, "account": account, "transport": transport,
                    "operation_signature": fingerprint}
    _reserve(root, run_id=run_id, operation_id=operation_id,
             fingerprint=fingerprint, provider=provider,
             ledger_args=_ledger_identity(
                 provider, query=query, country=country, language=language,
                 mode=mode, engine=arguments.get("engine"), limit=limit, page=page,
                 # The account is part of what was bought: two accounts are two
                 # spend opportunities even for one query.
                 account=account),
             limit=_number("MARKETOPS_SEARCH_MAX_CALLS"))
    # An operation reservation counts even if provider fails. No blind retries.
    append_event(root, run_id=run_id, operation_id=operation_id,
                 stage="discovery", kind="tool", status="started", provider=provider)
    try:
        response = _request(provider, mode, arguments["query"], country, language,
                            limit, page, opener=opener)
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
    except ValueError:
        try:
            append_event(root, run_id=run_id, operation_id=operation_id,
                         stage="discovery", kind="tool", status="unknown",
                         provider=provider, reason_code="provider_or_receipt_failed")
        except Exception:
            pass
        # Already a closed-set code from this module (or a pre-reservation
        # refusal). Re-raising the same object adds no context.
        raise
    except Exception:
        try:
            append_event(root, run_id=run_id, operation_id=operation_id,
                         stage="discovery", kind="tool", status="unknown",
                         provider=provider, reason_code="provider_or_receipt_failed")
        except Exception:
            pass
        # Built here, raised *after* the handler. Raising a new exception inside
        # `except` attaches the caught one as `__context__`, and on the SerpApi
        # GET that object can carry a request URL containing the api_key.
        failure = ValueError("PROVIDER_OR_RECEIPT_ERROR")
    raise failure


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
                         limit: int = 10, page: int = 1,
                         account: str = "") -> dict:
        """One approved discovery query using *exactly one* SerpApi or Serper account.

        mode=web/maps/images/news/videos/shopping/scholar/patents/autocomplete. Neither
        verifies a business, venue, ownership relationship, email or role.
        The operator must configure network enablement and cumulative call ceiling.
        `page` is accepted only for the categories whose pagination is declared;
        everything else refuses rather than sending a second page silently.
        """
        return search(provider, query, country, language, run_id, operation_id,
                      mode, limit, page=page, account=account)

    server.run()


if __name__ == "__main__":
    main()

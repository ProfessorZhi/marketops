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
import urllib.error
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import research_lite_store as store
import research_provider_policy as policy
import research_official_mcp as official_mcp
import research_usage_ledger as usage_ledger
import search_canonical
import research_target_guard as target_guard
from research_event_journal import _root, append_event

ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
MAX_OUTPUT_BYTES = 4_000_000
ALLOWED_ACTIONS = frozenset({"crawl4ai_cloud", "crawl4ai_local",
                             "tinyfish_search", "tinyfish_fetch", "jev_choice",
                             "tinyfish_mcp_search", "tinyfish_mcp_fetch",
                             "tinyfish_mcp_agent", "tinyfish_mcp_wait", "serpapi_mcp_search"})
KEY_ENV = {"crawl4ai_cloud": "CRAWL4AI_API_KEY",
           "tinyfish_search": "TINYFISH_API_KEY",
           "tinyfish_fetch": "TINYFISH_API_KEY",
           "jev_choice": "TYPESAFE_API_KEY"}
# Codes this module may re-raise unchanged from inside the transport handler;
# anything else is collapsed to UPSTREAM_TRANSPORT_FAILED so an upstream cannot
# choose our error vocabulary.
_SAFE_TRANSPORT_CODES = frozenset({
    "UPSTREAM_RESULT_TOO_LARGE", "UPSTREAM_INVALID_RESPONSE",
    "UPSTREAM_AUTH_REJECTED", "UPSTREAM_ACCESS_FORBIDDEN",
    "UPSTREAM_INSUFFICIENT_CREDITS", "UPSTREAM_RATE_LIMITED",
    "UPSTREAM_SERVICE_UNAVAILABLE",
})


# Each action's place in the shared ledger's three-part identity: which vendor
# account it draws on, which capability it exercises, and over which transport.
# The transport is recorded rather than folded into the reservation key — folding
# it in is exactly the defect that made one query over REST and over MCP two
# spend opportunities.
#: (provider, capability, transport), in the *shared ledger's* vocabulary — the
#: capability names are the reviewed registry's, not this adapter's action names.
#: Keeping one vocabulary is what lets a REST call and the same call over the
#: embedded official MCP be recognised as one spend opportunity: if each side
#: named its own capability, the ledger would see two different operations and
#: refuse to notice the duplicate.
_ACTION_IDENTITY = {
    "crawl4ai_local": ("crawl4ai", "render", "local_sdk"),
    "crawl4ai_cloud": ("crawl4ai", "render", "rest"),
    "tinyfish_search": ("tinyfish", "search", "rest"),
    "tinyfish_fetch": ("tinyfish", "fetch", "rest"),
    "tinyfish_mcp_search": ("tinyfish", "search", "official_mcp"),
    "tinyfish_mcp_fetch": ("tinyfish", "fetch", "official_mcp"),
    "tinyfish_mcp_agent": ("tinyfish", "agent_run", "official_mcp"),
    "tinyfish_mcp_wait": ("tinyfish", "usage_read", "official_mcp"),
    "serpapi_mcp_search": ("serpapi", "search", "official_mcp"),
    "jev_choice": ("typesafe_jev", "judge", "python_sdk"),
}


def _identity(action: str) -> tuple[str, str, str]:
    if action not in _ACTION_IDENTITY:
        raise ValueError("UNSUPPORTED_PROVIDER_ACTION")
    return _ACTION_IDENTITY[action]


def _provider_of(action: str) -> str:
    return _identity(action)[0]


def _capability_of(action: str) -> str:
    return _identity(action)[1]


def _transport_of(action: str) -> str:
    return _identity(action)[2]


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


def _public_https_url(value: str, *, resolve: bool = True) -> str:
    """Strictly limit browser/HTTP targets to operator-provided public HTTPS domains.

    ``resolve=False`` skips only the name-resolution step. A question that asks
    the network must not be asked before the questions that decide whether this
    operation is authorized at all — otherwise an unresolvable name refuses a
    call that was going to be refused for its real reason and the operator reads
    the wrong cause. `invoke()` runs the shape-only form in its pre-checks and
    the resolving form after the reservation, which is where the credential and
    ceiling questions have already been answered.
    """
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
        ipaddress.ip_address(hostname)
        raise ValueError("PUBLIC_HOSTNAME_REQUIRED")  # Do not fetch raw IPs.
    except ValueError as exc:
        if str(exc) == "PUBLIC_HOSTNAME_REQUIRED":
            raise
    allow = [d.strip().lower() for d in os.environ.get("MARKETOPS_FETCH_ALLOWED_DOMAINS", "").split(",") if d.strip()]
    if not allow or not any(hostname == d or hostname.endswith("." + d) for d in allow):
        raise ValueError("HOST_NOT_APPROVED: set MARKETOPS_FETCH_ALLOWED_DOMAINS on the client")
    # An approved *name* is not an approved *target*. The allowlist is a string
    # rule, and the whole point of an SSRF guard is that the string is not the
    # answer: `www.<approved>` resolves wherever its records say. Resolve here,
    # refuse non-global answers before anything connects, and fail closed when
    # the name cannot be resolved at all.
    if resolve:
        target_guard.resolve_checked(hostname, parsed.port or 443)
    return value


def _checked_run(root: Path, run_id: str, operation_id: str) -> None:
    if not isinstance(operation_id, str) or not ID.fullmatch(operation_id):
        raise ValueError("INVALID_OPERATION_ID")
    if not isinstance(run_id, str) or not ID.fullmatch(run_id):
        raise ValueError("INVALID_RUN_ID")
    store.list_run(root, run_id, limit=1)


def _host_key(url: str) -> str:
    """One-way host label for same-Run stop signals; URLs never enter journal."""
    if not url:
        return ""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    # Plain www and apex represent the same website for access-stop purposes.
    if host.startswith("www."):
        host = host[4:]
    return hashlib.sha256(host.encode("utf-8")).hexdigest() if host else ""


def _reserve(root: Path, *, run_id: str, operation_id: str, action: str,
             signature: str, host_key: str = "",
             ledger_grant: str = "", ledger_args: dict | None = None) -> None:
    """Reserve once; prohibit renamed-operation retries and challenge-site loops.

    Reuse the existing provider-reservations DB. Extra columns migrate in place;
    no customer hostnames, page bodies or API credentials are stored.
    """
    if action not in ALLOWED_ACTIONS:
        raise ValueError("UNSUPPORTED_PROVIDER_ACTION")
    if os.environ.get("MARKETOPS_RESEARCH_ALLOW_NETWORK") != "1":
        raise ValueError("RESEARCH_NETWORK_DISABLED")
    ceiling = _limit()
    if ceiling < 1:
        raise ValueError("RESEARCH_BUDGET_NOT_APPROVED")
    if action in KEY_ENV:
        _key(KEY_ENV[action])
    if action in {"tinyfish_mcp_search", "tinyfish_mcp_fetch",
                  "tinyfish_mcp_agent", "tinyfish_mcp_wait", "serpapi_mcp_search"}:
        official_mcp._credential("serpapi" if action == "serpapi_mcp_search"
                                else "tinyfish")
    if action == "crawl4ai_local" and importlib.util.find_spec("crawl4ai") is None:
        raise ValueError("CRAWL4AI_LOCAL_NOT_INSTALLED")
    # This adapter's own operation identity is checked *before* the ledger, for
    # the same reason `dual_search_mcp` does it: the two refusals say different
    # things and the operator acts differently on each. "You already hold a
    # receipt for this operation id" is an article-identity statement this table
    # owns; "this run has already paid for this operation" is a money statement
    # the ledger owns. Checking here keeps the historical vocabulary — an operator
    # reading `OPERATION_ALREADY_RESERVED` and one reading
    # `DUPLICATE_OPERATION_SIGNATURE` should not have to learn two vocabularies
    # for one event.
    _receipt_identity_check(root, run_id, operation_id, signature, host_key)
    # The authoritative reservation: the shared ledger is the one writer that
    # knows about every transport, so a provider already metered over REST is
    # refused here before this adapter touches its own receipt table.
    ledger = usage_ledger.for_root(root)
    project = usage_ledger.project_for_root(root)
    # Both ceilings the ledger enforces must be stated, because it refuses an
    # unstated one rather than treating it as unlimited. The run bound is the same
    # operator number as the project bound: it says a single run may not consume
    # more than the whole allowance, which is the weaker of the two statements and
    # the only one this adapter can make without inventing a second policy.
    _state_caps(ledger, project, run_id, ceiling, ceiling)
    try:
        ledger.reserve(usage_ledger.OperationRequest(
            # The ledger's operation_id is unique per *ledger*, this adapter's is
            # unique per run. Namespacing reconciles the two without weakening
            # either: the ledger still cannot see one id twice, and a run that
            # legitimately reuses "op-1" is still one run's "op-1".
            project=project, run_id=run_id, operation_id=f"{run_id}:{operation_id}",
            provider=_provider_of(action), capability=_capability_of(action),
            account_ref=_provider_of(action), transport=_transport_of(action),
            intent=signature or action, grant=ledger_grant,
            # The adapter's signature *is* the canonical operation identity — it
            # encodes the url/query/params. Leaving it out of `args` made two
            # different targets of one action share a ledger signature.
            # A direct caller that supplies no richer identity has only its
            # canonical signature, and that *is* what was bought. Falling back to
            # a hand-built two-key dict here made this adapter's shape differ from
            # the other one's, which is exactly the CD-03 gap this boundary exists
            # to close.
            args=ledger_args if ledger_args is not None
            else search_canonical.search_operation_args(
                query=signature, capability=_capability_of(action))))
    except usage_ledger.LedgerRefused as exc:
        if exc.code == "DUPLICATE_OPERATION_SIGNATURE":
            # Re-derive this adapter's precise vocabulary; a race that slipped past
            # the pre-check reaches here, and it is still the same event.
            _receipt_identity_check(root, run_id, operation_id, signature, host_key)
            raise ValueError("DUPLICATE_RESEARCH_ATTEMPT_SAME_RUN: inspect the prior "
                             "receipt") from None
        if exc.code == "GRANT_REQUIRED" and action == "tinyfish_mcp_agent":
            # One paid path, one refusal code: whether the tier is closed by the
            # ledger or by the pre-dispatch switch, the operator is being told the
            # same thing — this dispatch is not authorized.
            raise ValueError(f"TINYFISH_AGENT_PAID_NOT_AUTHORIZED: {exc.message}") from None
        raise ValueError(f"{exc.code}: {exc.message}") from None
    folder = root / "state"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with closing(sqlite3.connect(folder / "provider-reservations.sqlite3", timeout=20)) as db:
        db.execute("PRAGMA busy_timeout=20000")
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS provider_reservations (
            run_id TEXT NOT NULL, operation_id TEXT NOT NULL, action TEXT NOT NULL,
            signature TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (run_id, operation_id)
        )""")
        existing_columns = {row[1] for row in db.execute("PRAGMA table_info(provider_reservations)")}
        if "host_key" not in existing_columns:
            db.execute("ALTER TABLE provider_reservations ADD COLUMN host_key TEXT NOT NULL DEFAULT ''")
        if "outcome_code" not in existing_columns:
            db.execute("ALTER TABLE provider_reservations ADD COLUMN outcome_code TEXT NOT NULL DEFAULT ''")
        old = db.execute("SELECT signature FROM provider_reservations "
                         "WHERE run_id=? AND operation_id=?", (run_id, operation_id)).fetchone()
        if old:
            if old[0] == signature:
                raise ValueError("OPERATION_ALREADY_RESERVED: inspect the prior receipt")
            raise ValueError("OPERATION_ID_COLLISION")
        # An agent can invent a new operation_id; it cannot silently reissue
        # an identical billable/unknown operation inside the same Run.
        if db.execute("SELECT 1 FROM provider_reservations WHERE run_id=? AND signature=?",
                      (run_id, signature)).fetchone():
            raise ValueError("DUPLICATE_RESEARCH_ATTEMPT_SAME_RUN")
        if host_key and db.execute(
            "SELECT 1 FROM provider_reservations WHERE run_id=? AND host_key=? "
            "AND outcome_code IN ('SOURCE_ACCESS_BLOCKED','SOURCE_CHALLENGE_PAGE')",
            (run_id, host_key)
        ).fetchone():
            raise ValueError("HOST_PREVIOUSLY_BLOCKED_SAME_RUN")
        # The lifetime whole-table count that used to live here is gone: the
        # shared ledger above is now the only ceiling authority, and its counters
        # are scoped per run and per project rather than per table lifetime. This
        # table keeps its rows as receipts (contract §9) and still refuses the
        # identity collisions below, which are about article identity and not
        # about budget.
        db.execute("INSERT INTO provider_reservations "
                   "(run_id,operation_id,action,signature,created_at,host_key,outcome_code) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (run_id, operation_id, action, signature,
                    datetime.now(timezone.utc).isoformat(), host_key, "reserved"))
        db.commit()




# Ceilings are persisted in the ledger, so stating them once per process is enough.
# Re-stating them on every reservation is a write on the hot path of an
# eight-process race, and the contention it adds is paid by the test that is
# *about* contention.
_CAPS_STATED: set = set()


def _state_caps(ledger, project: str, run_id: str, ceiling: int,
                project_ceiling: int | None = None) -> None:
    key = (str(getattr(ledger, "_path", ledger)), project, run_id, ceiling, project_ceiling)
    if key in _CAPS_STATED:
        return
    ledger.set_cap("run", f"{project}/{run_id}", ceiling_calls=ceiling,
                   source="MARKETOPS_SEARCH_MAX_CALLS")
    if project_ceiling is not None:
        ledger.set_cap("project", project, ceiling_calls=project_ceiling,
                       source="MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS")
    _CAPS_STATED.add(key)


#: Actions whose ledger identity is a *shared* canonical search identity.
#:
#: Only these carry enough information here to build it. Everything else gets the
#: same *shape* with an adapter-local value: identical key names, which is what
#: CD-03 requires of the boundary, and no claim that two adapters would produce the
#: same value, because they would not.
#:
#: The identity itself is `search_canonical.search_operation_args` — D's module owns
#: it, and a second builder here would be a second answer to "what did we buy".
_SHARED_SEARCH_IDENTITY = frozenset({"serpapi_mcp_search"})


def _param_text(params: dict, name: str) -> str | None:
    """A vendor parameter as text, with "present but empty" read as omitted."""
    value = params.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("IDENTITY_VALUE_INVALID")
    return value.strip() or None


def _param_int(params: dict, name: str) -> int | None:
    value = params.get(name)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("IDENTITY_VALUE_INVALID")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError("IDENTITY_VALUE_INVALID")


def _ledger_identity(action: str, *, query: str, mcp_params, fingerprint: str,
                     account: str = "") -> dict:
    if action in _SHARED_SEARCH_IDENTITY:
        envelope = mcp_params if isinstance(mcp_params, dict) else {}
        params = envelope.get("params")
        if not isinstance(params, dict):
            # Two call shapes reach this function: the MCP argument envelope
            # ``{"params": {...}, "mode": "compact"}`` and the vendor params
            # themselves. The real ``invoke`` path hands the latter. Reading only
            # the envelope made every real call fall through to the adapter-local
            # digest, which is the CD-03 gap this function exists to close.
            params = envelope
        try:
            primary = params.get("q")
            alias = params.get("query")
            if primary is None:
                primary = alias
            elif alias is not None and str(alias) != str(primary):
                raise ValueError("IDENTITY_QUERY_CONFLICT")
            return search_canonical.serpapi_search_route_args(
                query=primary,
                engine=_param_text(params, "engine"),
                country=_param_text(params, "gl"),
                language=_param_text(params, "hl"),
                num=_param_int(params, "num"),
                page=_param_int(params, "page"),
                account=account,
            )
        except (ValueError, TypeError):
            # Not enough to build the canonical identity honestly; fall through
            # rather than invent the missing fields.
            pass
    return search_canonical.search_operation_args(
        query=str(fingerprint), capability=_capability_of(action))


def _receipt_identity_check(root: Path, run_id: str, operation_id: str,
                            signature: str, host_key: str = "") -> None:
    """Read-only: has this run already used this operation id, or this query?

    Not the money gate — the ledger is — but it is what keeps a refusal in this
    adapter's own historical vocabulary instead of a generic duplicate code.
    """
    folder = root / "state"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "provider-reservations.sqlite3"
    if not path.exists():
        return
    with closing(sqlite3.connect(path, timeout=20)) as db:
        db.execute("PRAGMA busy_timeout=20000")
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "provider_reservations" not in tables:
            return
        columns = {row[1] for row in db.execute("PRAGMA table_info(provider_reservations)")}
        old = db.execute("SELECT signature FROM provider_reservations "
                         "WHERE run_id=? AND operation_id=?",
                         (run_id, operation_id)).fetchone()
        if old:
            if old[0] == signature:
                raise ValueError("OPERATION_ALREADY_RESERVED: inspect the prior receipt")
            raise ValueError("OPERATION_ID_COLLISION")
        duplicate = db.execute(
            "SELECT 1 FROM provider_reservations WHERE run_id=? AND signature=?",
            (run_id, signature)).fetchone()
        if duplicate:
            raise ValueError("DUPLICATE_RESEARCH_ATTEMPT_SAME_RUN: inspect the prior receipt")
        if host_key and "host_key" in columns and db.execute(
                "SELECT 1 FROM provider_reservations WHERE run_id=? AND host_key=? "
                "AND outcome_code IN ('SOURCE_ACCESS_BLOCKED','SOURCE_CHALLENGE_PAGE')",
                (run_id, host_key)).fetchone():
            raise ValueError("HOST_PREVIOUSLY_BLOCKED_SAME_RUN")


def _record_outcome(root: Path, run_id: str, operation_id: str, code: str) -> None:
    """Persist bounded outcome codes only; no extra database or raw target."""
    with closing(sqlite3.connect(root / "state" / "provider-reservations.sqlite3", timeout=20)) as db:
        db.execute("PRAGMA busy_timeout=20000")
        db.execute("UPDATE provider_reservations SET outcome_code=? "
                   "WHERE run_id=? AND operation_id=?", (code, run_id, operation_id))
        db.commit()

def _http_status_code(status: int) -> str:
    """Error classification used for routing, never a billing authorization."""
    return {401: "UPSTREAM_AUTH_REJECTED", 402: "UPSTREAM_INSUFFICIENT_CREDITS",
            403: "UPSTREAM_ACCESS_FORBIDDEN", 429: "UPSTREAM_RATE_LIMITED"}.get(
                status, "UPSTREAM_SERVICE_UNAVAILABLE" if isinstance(status, int)
                and status >= 500 else "UPSTREAM_INVALID_RESPONSE")


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
    # The refusal code is chosen inside the handler but *raised* after it.
    # `raise ... from None` only sets `__suppress_context__`: the caught
    # exception stays attached as `__context__`, and the refused HTTPError's
    # reason phrase is attacker-controlled and may echo the query-string key.
    # Anything that walks the chain (this repository's own group-unwrapping
    # helpers do) would still reach it, so the handler is left before raising.
    failure: str | None = None
    raw = b""
    try:
        with opener(req, timeout=24) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                failure = _http_status_code(status)
            else:
                raw = response.read(MAX_OUTPUT_BYTES + 1)
                if len(raw) > MAX_OUTPUT_BYTES:
                    failure = "UPSTREAM_RESULT_TOO_LARGE"
    except urllib.error.HTTPError as exc:
        # Never stringify HTTPError — request URL may contain credentials.
        failure = _http_status_code(exc.code)
        exc.__traceback__ = None
    except urllib.error.URLError:
        failure = "UPSTREAM_NETWORK_FAILED"
    except ValueError as exc:
        failure = (str(exc) if str(exc) in _SAFE_TRANSPORT_CODES
                   else "UPSTREAM_TRANSPORT_FAILED")
    except Exception:
        failure = "UPSTREAM_TRANSPORT_FAILED"
    if failure is not None:
        raise ValueError(failure)
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("UPSTREAM_NOT_JSON")
    if not isinstance(parsed, dict):
        # A top-level array or scalar is a shape no caller here expects. Letting
        # it through would hand an upstream the choice of the pipeline's types
        # and store it as if it were a provider response.
        raise ValueError("UPSTREAM_INVALID_RESPONSE")
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if err:
            code = err.get("code", "") if isinstance(err, dict) else parsed.get("code", "")
            # Structured provider failure only; untrusted messages are never
            # echoed or searched for credentials. Unknown remains unknown.
            normalized = str(code).lower().replace("-", "_")
            if normalized in {"insufficient_credits", "insufficient_balance",
                              "payment_required", "out_of_credits"}:
                raise ValueError("UPSTREAM_INSUFFICIENT_CREDITS")
            if normalized in {"rate_limited", "quota_exceeded", "too_many_requests"}:
                raise ValueError("UPSTREAM_RATE_LIMITED")
            if normalized in {"unauthorized", "invalid_api_key", "authentication_failed"}:
                raise ValueError("UPSTREAM_AUTH_REJECTED")
            raise ValueError("UPSTREAM_DECLARED_ERROR")
    return parsed


def _crawl4ai_local(url: str, *, egress=None) -> dict:
    """Keep post-rendered HTML separate from derivative Markdown, never claim HTTP raw.

    ``egress`` is the controlled egress this browser must dial through (contract
    IA-4, the DECISION-01 binding). There is **no** unmanaged fallback and there
    is deliberately no parameter to ask for one: an unbound call refuses with
    ``LOCAL_CRAWL_NOT_SAFE_TO_RUN`` rather than opening Chromium at a target
    nothing re-checks. The binding is a session from ``research_egress``; its
    ``browser_config_kwargs()`` supplies ``proxy_config`` and the Chromium flags
    that close the implicit-loopback and QUIC bypasses, from the same definition
    the core fetcher reads.
    """
    if egress is None:
        raise ValueError(
            "LOCAL_CRAWL_NOT_SAFE_TO_RUN: no controlled egress is bound to this "
            "browser; an unmanaged local browser is the thing this path exists to "
            "prevent, so this is a refusal and not a degraded mode")
    from crawl4ai import AsyncWebCrawler, BrowserConfig

    async def crawl() -> dict:
        async with AsyncWebCrawler(
                config=BrowserConfig(**egress.browser_config_kwargs(), headless=True)) as crawler:
            result = await crawler.arun(url=url)
            status = getattr(result, "status_code", None)
            if status in {401, 403, 429}:
                raise ValueError("SOURCE_ACCESS_BLOCKED")
            # Cloudflare documents cf-mitigated: challenge on all its
            # interstitial Challenge Page responses. Check it when Crawl4AI
            # exposes headers; fallback to page markers when it does not.
            headers = getattr(result, "response_headers", None)
            if isinstance(headers, dict) and any(
                str(key).casefold() == "cf-mitigated"
                and str(value).casefold() == "challenge"
                for key, value in headers.items()
            ):
                raise ValueError("SOURCE_CHALLENGE_PAGE")
            markdown = getattr(result, "markdown", None)
            content = getattr(markdown, "raw_markdown", None) or str(markdown or "")
            rendered = getattr(result, "html", None) or ""
            # Check for a challenge before treating crawler.success as decisive.
            # A blocked page may be served with HTTP 200 and success=False.
            probe = (rendered[:1800] + content[:1000]).casefold()
            if any(word in probe for word in ("cf-chl-", "verify you are human", "captcha challenge",
                                               "just a moment...", "checking your browser")):
                raise ValueError("SOURCE_CHALLENGE_PAGE")
            if not result.success:
                raise ValueError("LOCAL_CRAWL_FAILED")
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


def _jev_choice(*, document: str, question: str, options: list[str],
                primitive: str = "choice") -> dict:
    """All three official Jev primitives through one narrowly metered adapter.

    Despite the legacy name, one SDK call is either Choice, Noul or Score.
    Semantic output is a *judgment*, never original source or formal approval.
    """
    if not isinstance(document, str) or not 20 <= len(document) <= 4000:
        raise ValueError("JEV_REQUIRES_BOUNDED_EVIDENCE_TEXT")
    if not isinstance(question, str) or not 10 <= len(question) <= 500:
        raise ValueError("JEV_QUESTION_INVALID")
    if primitive not in {"choice", "noul", "score"}:
        raise ValueError("JEV_PRIMITIVE_INVALID")
    if not isinstance(options, list) or len(options) > 10:
        raise ValueError("JEV_OPTIONS_INVALID")
    if primitive == "noul":
        if options:
            raise ValueError("JEV_NOUL_HAS_NO_CRITERIA")
    elif primitive == "choice":
        if (not 2 <= len(options) <= 8
                or any(not isinstance(v, str) or not ID.fullmatch(v) for v in options)
                or len(options) != len(set(options))):
            raise ValueError("JEV_OPTIONS_INVALID")
    else:
        # Score levels are *descriptions*, not ID keys; order matters.
        if (not 2 <= len(options) <= 10
                or any(not isinstance(v, str) or not 3 <= len(v) <= 200 for v in options)
                or len(options) != len(set(options))):
            raise ValueError("JEV_SCORE_LEVELS_INVALID")
    from typesafe_sdk import Choice, Noul, Score, RetryPolicy, TypeSafeClient
    prompt = {"choice": lambda: Choice(instructions=question,
                                      criteria={label: None for label in options}),
              "noul": lambda: Noul(instructions=question),
              "score": lambda: Score(instructions=question, criteria=options)}
    qid = "marketops_judgment"
    with TypeSafeClient(api_key=_key("TYPESAFE_API_KEY"),
                        retry=RetryPolicy(max_retries=0)) as client:
        result = client.system_one(
            state={"source_excerpt": document},
            questions={qid: prompt[primitive]()},
        )
    if primitive == "choice":
        answer = str(result.choices[qid].choice)
        if answer not in options:
            raise ValueError("JEV_RETURNED_UNDECLARED_OPTION")
        output = {"chosen_option": answer}
    elif primitive == "noul":
        answer = float(result.nouls[qid].noul)
        if not 0 <= answer <= 1:
            raise ValueError("JEV_RETURNED_INVALID_NOUL")
        output = {"noul": answer}
    else:
        answer = float(result.scores[qid].score)
        if not 0 <= answer <= len(options) - 1:
            raise ValueError("JEV_RETURNED_INVALID_SCORE")
        output = {"score": answer, "level_count": len(options)}
    # Preserve calibrated output when provided; none of it is a fact proof.
    answer_obj = (result.choices[qid] if primitive == "choice" else
                  result.nouls[qid] if primitive == "noul" else
                  result.scores[qid])
    probabilities = getattr(answer_obj, "probabilities", None)
    if isinstance(probabilities, dict) and len(probabilities) <= 10:
        try:
            output["probabilities"] = {str(k)[:96]: float(v) for k, v in probabilities.items()}
        except (TypeError, ValueError):
            pass
    elif isinstance(probabilities, (list, tuple)) and len(probabilities) <= 10:
        try:
            output["probabilities"] = [float(v) for v in probabilities]
        except (TypeError, ValueError):
            pass
    confidence = getattr(answer_obj, "confidence", None)
    if confidence is not None:
        try:
            value = float(confidence)
            if 0 <= value <= 1:
                output["confidence"] = value
        except (TypeError, ValueError):
            pass
    return {**output, "primitive": primitive,
            "model": str(getattr(result, "model", "") or ""),
            "judgment_only": True,
            "note": "Typed model judgment is NOT source evidence or SOP approval."}

def _owns_submitted_agent_run(root: Path, run_id: str,
                              origin_operation_id: str, provider_run_id: str) -> bool:
    """A status tool cannot inspect someone else's TinyFish run or resubmit it.

    Verify the previous run submission was persisted and hash-checked inside
    this *same* MarketOps run before accepting an asynchronous status check.
    """
    if not ID.fullmatch(origin_operation_id) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", provider_run_id):
        return False
    artifact_id = "art-" + hashlib.sha256(
        (run_id + ":" + origin_operation_id + ":tinyfish_mcp_agent").encode("utf-8")
    ).hexdigest()[:32]
    db_file = root / "state" / "research-lite.sqlite3"
    if not db_file.exists():
        return False
    with closing(sqlite3.connect(db_file)) as db:
        row = db.execute(
            "SELECT relative_path,content_sha256 FROM artifacts "
            "WHERE run_id=? AND artifact_id=? AND provider=?",
            (run_id, artifact_id, "tinyfish_mcp_agent"),
        ).fetchone()
    if not row:
        return False
    filepath = (root / row[0]).resolve()
    if not filepath.is_relative_to(root.resolve()) or not filepath.is_file():
        return False
    data = filepath.read_bytes()
    if hashlib.sha256(data).hexdigest() != row[1]:
        return False
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return False
    return payload.get("agent_run_id") == provider_run_id


def invoke(action: str, *, run_id: str, operation_id: str, url: str = "",
           query: str = "", document: str = "", question: str = "",
           options: list[str] | None = None, primitive: str = "choice",
           mcp_params: dict | None = None, paid_limit_usd: str = "", opener=None,
           egress=None, tier_grant=None, ledger_grant: str = "") -> dict:
    """One bounded, metered material/decision call, with audit trail and Lite store."""
    root = _root(None)
    _checked_run(root, run_id, operation_id)
    if action not in ALLOWED_ACTIONS:
        raise ValueError("UNKNOWN_PROVIDER_ACTION")
    if action in {"crawl4ai_cloud", "crawl4ai_local", "tinyfish_fetch",
                   "tinyfish_mcp_fetch", "tinyfish_mcp_agent"}:
        _public_https_url(url, resolve=False)
    if action in {"tinyfish_search", "tinyfish_mcp_search"} and (
            not isinstance(query, str) or not 2 <= len(query) <= 240):
        raise ValueError("SEARCH_QUERY_INVALID")
    if action == "tinyfish_mcp_wait":
        if type(mcp_params) is not dict or set(mcp_params) != {
                "agent_run_id", "origin_operation_id", "poll_index"}:
            raise ValueError("TINYFISH_AGENT_POLL_INVALID")
        if type(mcp_params["poll_index"]) is not int or not 1 <= mcp_params["poll_index"] <= 10:
            raise ValueError("TINYFISH_AGENT_POLL_LIMIT")
        official_mcp._validate("tinyfish", "get_run",
                               {"id": mcp_params["agent_run_id"]})
        if not _owns_submitted_agent_run(root, run_id,
                                          mcp_params["origin_operation_id"],
                                          mcp_params["agent_run_id"]):
            raise ValueError("TINYFISH_AGENT_NOT_OWNED_BY_THIS_RESEARCH_RUN")
    if action == "serpapi_mcp_search":
        official_mcp._validate("serpapi", "search",
                               {"params": mcp_params, "mode": "compact"})
    if action == "tinyfish_mcp_agent":
        if (type(mcp_params) is not dict or
            type(mcp_params.get("max_steps")) is not int or
            type(mcp_params.get("max_duration_seconds")) is not int):
            raise ValueError("TINYFISH_AGENT_HARD_CAP_REQUIRED")
        official_mcp._validate("tinyfish", "run_web_automation", {
            "url": url, "goal": question,
            "agent_config": {"max_steps": mcp_params["max_steps"],
                             "max_duration_seconds": mcp_params["max_duration_seconds"]}})
        if os.environ.get("MARKETOPS_TINYFISH_AGENT_ALLOW_PAID") != "1":
            raise ValueError("TINYFISH_AGENT_PAID_NOT_AUTHORIZED")
        if not paid_limit_usd:
            raise ValueError("TINYFISH_AGENT_MAX_CHARGE_REQUIRED")
        # Validate BEFORE reserving any provider attempt. This does not grant
        # permission: the MCP client also checks the signed-in account wallet.
        from decimal import Decimal, InvalidOperation
        try:
            maximum = Decimal(paid_limit_usd)
            if not maximum.is_finite() or maximum <= 0 or maximum > Decimal("5"):
                raise InvalidOperation()
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("TINYFISH_AGENT_MAX_CHARGE_INVALID") from None
    if action == "jev_choice":
        if not isinstance(document, str) or not 20 <= len(document) <= 4000:
            raise ValueError("JEV_REQUIRES_BOUNDED_EVIDENCE_TEXT")
        if primitive not in {"choice", "noul", "score"}:
            raise ValueError("JEV_PRIMITIVE_INVALID")
    fingerprint = hashlib.sha256(json.dumps({
        "action": action, "url": url, "query": query,
        "document": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "question": question, "options": options, "primitive": primitive,
        "mcp_params": mcp_params, "paid_limit_usd": paid_limit_usd,
    }, sort_keys=True, ensure_ascii=True).encode("utf-8")).hexdigest()
    try:
        _reserve(root, run_id=run_id, operation_id=operation_id, action=action,
                 signature=fingerprint, ledger_grant=ledger_grant,
                 ledger_args=_ledger_identity(action, query=query,
                                              mcp_params=mcp_params,
                                              fingerprint=fingerprint),
                 host_key=_host_key(url) if action in {"crawl4ai_local", "crawl4ai_cloud",
                                                      "tinyfish_fetch", "tinyfish_mcp_fetch",
                                                      "tinyfish_mcp_agent"} else "")
    except ValueError as exc:
        # Already-reserved/blocked operations are not another provider attempt.
        # Keep an explicit code, never the URL, search text or token values.
        code = str(exc).split(":", 1)[0]
        safe = {"DUPLICATE_RESEARCH_ATTEMPT_SAME_RUN",
                "HOST_PREVIOUSLY_BLOCKED_SAME_RUN", "OPERATION_ALREADY_RESERVED",
                "OPERATION_ID_COLLISION", "EXTERNAL_CALL_CEILING_REACHED"}
        try:
            append_event(root, run_id=run_id, operation_id=operation_id,
                         stage="qualification" if action == "jev_choice" else "acquisition",
                         kind="tool", status="blocked", provider=action,
                         reason_code=(code.lower() if code in safe else "external_preflight_blocked"))
        except Exception:
            pass
        raise
    # The resolving half of target approval, after the reservation: the budget and
    # credential questions have been answered by now, so a name that cannot be
    # resolved can no longer be mistaken for the reason this call was refused.
    if action in {"crawl4ai_cloud", "crawl4ai_local", "tinyfish_fetch",
                  "tinyfish_mcp_fetch", "tinyfish_mcp_agent"}:
        _public_https_url(url)
    append_event(root, run_id=run_id, operation_id=operation_id,
                 stage="acquisition" if action != "jev_choice" else "qualification",
                 kind="tool", status="started", provider=action)
    try:
        if action == "crawl4ai_local":
            response = _crawl4ai_local(url, egress=egress)
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
        elif action == "tinyfish_mcp_search":
            response = official_mcp.call("tinyfish", "search", {"query": query})
        elif action == "tinyfish_mcp_fetch":
            response = official_mcp.call("tinyfish", "fetch_content",
                                         {"urls": [url], "format": "markdown",
                                          "links": False, "image_links": False,
                                          "page_metadata": False})
        elif action == "serpapi_mcp_search":
            response = official_mcp.call("serpapi", "search",
                                         {"params": mcp_params, "mode": "compact"})
        elif action == "tinyfish_mcp_wait":
            response = official_mcp.call(
                "tinyfish", "get_run",
                {"id": mcp_params["agent_run_id"]})
        elif action == "tinyfish_mcp_agent":
            response = official_mcp.call(
                "tinyfish", "run_web_automation",
                {"url": url, "goal": question,
                 "agent_config": {"max_steps": mcp_params["max_steps"],
                                  "max_duration_seconds": mcp_params["max_duration_seconds"]}},
                max_paid_usd=paid_limit_usd, tier_grant=tier_grant)
        else:
            response = _jev_choice(document=document, question=question,
                                   options=options or [], primitive=primitive)
        # A browser's rendered DOM is a separate source representation, not
        # on-the-wire HTTP bytes. Never include it in the Agent's tool preview.
        rendered_html = response.pop("_rendered_html", None) if isinstance(response, dict) else None
        serialized = json.dumps(response, ensure_ascii=False, default=str)
        # Upstream services must not be allowed to echo operator credentials into
        # evidence/artifact storage or back into the Agent context.
        for key_name in ("SERPAPI_API_KEY", "SERPER_API_KEY", "TINYFISH_API_KEY",
                         "CRAWL4AI_API_KEY", "TYPESAFE_API_KEY",
                         "TINYFISH_MCP_ACCESS_TOKEN"):
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
        submitted_agent = action == "tinyfish_mcp_agent"
        agent_wait = action == "tinyfish_mcp_wait"
        lifecycle = response.get("agent_lifecycle", "unknown") if agent_wait else ""
        event_status = ("unknown" if submitted_agent or
                        (agent_wait and lifecycle != "completed") else "succeeded")
        outcome = ("AGENT_SUBMITTED_NOT_TERMINAL" if submitted_agent
                   else "AGENT_" + lifecycle.upper() if agent_wait else "SUCCEEDED")
        append_event(root, run_id=run_id, operation_id=operation_id,
                     stage=stage, kind="tool",
                     status=event_status, provider=action,
                     receipt_ref=artifact_id,
                     reason_code=(outcome.lower() if event_status == "unknown" else ""))
        _record_outcome(root, run_id, operation_id, outcome)
        return {"status": ("MCP_AGENT_SUBMITTED_NOT_TERMINAL" if submitted_agent
                           else "MCP_AGENT_" + lifecycle.upper() +
                                "_NOT_BUSINESS_VERIFIED" if agent_wait
                           else "SAVED_MATERIAL_NOT_VERIFIED"),
                "provider": action, "operation_id": operation_id,
                "invocation": policy.invocation_receipt(
                    action, primitive=primitive,
                    reservation="ONE_ATTEMPT_RESERVED_NOT_BILL_VERIFIED"),
                "artifact": stored, "rendered_artifact": rendered_artifact,
                "record": entry,
                "preview": serialized[:480], "preview_truncated": len(serialized) > 480,
                "preview_kind": "brief_derived_snippet_not_evidence"}
    except Exception as exc:
        known_codes = {"SOURCE_ACCESS_BLOCKED", "SOURCE_CHALLENGE_PAGE",
                       "SOURCE_EMPTY_EXTRACT", "LOCAL_CRAWL_FAILED",
                       "LOCAL_BROWSER_NOT_INSTALLED", "LOCAL_CRAWL_RUNTIME_FAILED",
                       "UPSTREAM_AUTH_REJECTED", "UPSTREAM_INSUFFICIENT_CREDITS",
                       "UPSTREAM_RATE_LIMITED", "UPSTREAM_ACCESS_FORBIDDEN",
                       "UPSTREAM_SERVICE_UNAVAILABLE", "UPSTREAM_NETWORK_FAILED",
                       "UPSTREAM_TRANSPORT_FAILED", "UPSTREAM_NOT_JSON",
                       "UPSTREAM_INVALID_RESPONSE", "UPSTREAM_DECLARED_ERROR",
                       "UPSTREAM_RESULT_TOO_LARGE",
                       "UPSTREAM_MCP_TRANSPORT_OR_SESSION_FAILED",
                       "UPSTREAM_MCP_TOOL_FAILED", "UPSTREAM_MCP_TOOL_NOT_ADVERTISED",
                       "UPSTREAM_MCP_EMPTY_RESULT", "UPSTREAM_MCP_RESULT_TOO_LARGE",
                       "OFFICIAL_MCP_CREDENTIAL_REQUIRED",
                       "TINYFISH_AGENT_SPEND_CAP_EXCEEDED",
                       "TINYFISH_AGENT_WALLET_NOT_VERIFIED",
                       "TINYFISH_AGENT_WALLET_TOOL_REQUIRED",
                       "TINYFISH_AGENT_PROVIDER_HARD_CAP_NOT_VERIFIED",
                       "TINYFISH_AGENT_RUN_ID_MISSING_AFTER_SUBMISSION"}
        reason = str(exc) if isinstance(exc, ValueError) and str(exc) in known_codes else "PROVIDER_OR_STORAGE_FAILED"
        try:
            _record_outcome(root, run_id, operation_id, reason)
        except OSError:
            pass
        try:
            append_event(root, run_id=run_id, operation_id=operation_id,
                         stage="qualification" if action == "jev_choice" else "acquisition",
                         kind="tool", status="blocked" if reason in known_codes else "unknown",
                         provider=action, reason_code=reason.lower())
        except Exception:
            pass
        raise ValueError(reason + ": inspect prior operation; never auto-retry") from None

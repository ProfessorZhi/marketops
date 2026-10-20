#!/usr/bin/env python3
"""Serper as a typed adapter: one capability, one declared route, one dispatch.

## What this module is, and what it is deliberately not

It is the §6 ``ProviderAdapter`` for Serper: a caller hands it an already
authorized ``CapabilityRequest`` and it either produces a discovery payload or
refuses with a closed-set code. It is **not** a second policy source. It does not
read a tier, a budget or a network switch, and it does not decide whether the call
should happen — the Gateway's Policy and Ledger decide that, and an adapter that
re-checked any of it would be a rival authority (contract §6, IA-2).

It also does not call :mod:`dual_search_mcp`. That module owns a *legacy* ledger
(``search_attempts``) which the Wave-1 contract replaces with the unified one; a
call into it would reserve a second time for one logical operation, which is
precisely the "accidental new paid path" ``research_gateway`` exists to prevent.
The wire shape is instead assembled here from :mod:`serper_modes` (the declared
registry) and keyed by :mod:`search_canonical` (the transport-independent
operation key), so there is exactly one place a Serper request can be described.

## The route is not a parameter

Serper's host, path, HTTP method and tool name are **constants**. A request that
tries to supply ``endpoint``, ``url``, ``host``, ``path``, ``tool``, ``method``,
``headers``, ``api_key``, ``transport``, ``tier``, ``provider``, ``key``,
``token``, ``grant`` or ``budget`` is refused by name, and the refusal happens
before any transport is touched. Categories are chosen from the declared
registry, not from a free-form string: ``mode`` resolves through
:mod:`serper_modes`, so an undeclared category, a declared-but-unservable one
(``reviews``) and a pagination the registry cannot describe are three different
refusals rather than one "unsupported".

## What is honestly unverified

The endpoint paths and per-endpoint ``num`` rules behind :mod:`serper_modes` come
from third-party artifacts; Serper's own API reference did not resolve from this
host on 2026-10-17 and **no request has been sent to Serper**. So this adapter's
`readiness` is ``CODED`` and its tests are `OFFLINE_TESTED`: they hold the shapes
we send, not the vendor's acceptance of them. A vendor-side check needs a
separately authorized live probe, and the accounting for it needs the integrated
budget first.
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

import research_contract
import search_canonical
import serper_modes

PROVIDER = "serper"
TRANSPORT = "rest"

#: The capability ids this adapter serves. Cross-checked against the Registry
#: (§6): the Registry stays the allowlist authority, this is the adapter's own
#: claim, and a disagreement is a wiring bug rather than a policy decision.
CAPABILITIES: tuple[str, ...] = ("serper.search",)

#: Tier is data, not a decision. T2 — vendor-metered read behind an operator
#: policy switch and an account credential. All Serper categories are one
#: capability because they share one meter, one credential and one tier; the
#: category is a validated parameter, not a capability id.
TIER_BY_CAPABILITY: Mapping[str, str] = {"serper.search": "T2"}

EFFECT_CLASS_BY_CAPABILITY: Mapping[str, str] = {"serper.search": "BILLABLE_READ"}

#: The exposed parameter subset. Everything here is validated before dispatch;
#: anything outside it is refused by name (IR-2).
ALLOWED_PARAMS: Mapping[str, frozenset[str]] = {
    "serper.search": frozenset({"query", "country", "language", "limit", "page", "mode"}),
}

#: Declared upstream capabilities this adapter does **not** serve, kept visible so
#: the catalog can say "unsupported" rather than leave a hole that reads as an
#: oversight. `scrape` is the case the coordinator's "13 endpoints" count runs
#: into: it is ``scrape.serper.dev``, a different host, and this adapter's
#: allowlisted origin is ``google.serper.dev`` only. Declaring it would be a
#: coverage claim with no route behind it, so it is declared *unsupported*.
DECLARED_NOT_SERVED: Mapping[str, Mapping[str, str]] = {
    "serper.scrape": {
        "availability": "UNSUPPORTED",
        "reason": ("scrape.serper.dev is a different host. This adapter's allowlisted "
                   "origin is google.serper.dev, so the scrape route has no way to be "
                   "reached here and is declared unsupported rather than counted."),
        "evidence": ("third-party SDK/OpenAPI artifacts enumerate scrape.serper.dev; "
                     "serper.dev's own homepage lists no scrape vertical and its API "
                     "reference did not resolve from this host on 2026-10-17"),
    },
}

#: Every route-choosing parameter a host must not be able to supply. Checked
#: before the generic unknown-key rule so the refusal names the injection rather
#: than reporting a spelling problem.
ROUTE_PARAMS_FORBIDDEN: frozenset[str] = frozenset({
    "endpoint", "url", "uri", "host", "hostname", "base_url", "origin", "path",
    "tool", "tool_name", "method", "headers", "body", "api_key", "apikey", "key",
    "token", "access_token", "password", "secret", "credential", "credentials",
    "transport", "protocol", "tier", "effect_class", "provider", "grant",
    "grant_ref", "budget", "budget_usd", "reservation", "ledger_id",
})

#: §6 IA-5: this adapter does not follow redirects. A redirect to another host is
#: a route nobody chose.
MAX_BODY = 4 * 1024 * 1024
TIMEOUT_SECONDS = 20.0
MAX_QUERY_CHARS = 250
MIN_QUERY_CHARS = 2
MAX_LIMIT = 20

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}$")

#: §5.3's ``reason_code`` closed set, transcribed verbatim. A new name means a
#: CHANGE-nn request, not a local edit — `_reason` asserts membership so a typo
#: fails at development time instead of in someone's receipt.
CONTRACT_REASON_CODES: frozenset[str] = frozenset({
    "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
    "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
    "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
    "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
    "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING",
    "RESEARCH_NETWORK_DISABLED", "WALLET_PRECONDITION_FAILED", "GRANT_SIGNATURE_INVALID",
})

#: Refusals **before** dispatch: nothing left this process, so each of these has
#: an honest §5.3 counterpart.
_PRE_DISPATCH_LOCAL_REASONS: frozenset[str] = frozenset({
    "SERPER_TRANSPORT_UNBOUND",
    "SERPER_CREDENTIAL_ABSENT",
    "SERPER_PARAMS_NOT_A_MAPPING",
    "SERPER_ROUTE_PARAM_FORBIDDEN",
    "SERPER_QUERY_INVALID",
    "SERPER_LOCALE_INVALID",
    "SERPER_LIMIT_INVALID",
    "SERPER_MODE_UNSUPPORTED",
    "SERPER_MODE_REQUIRES_UNSENT_PARAM",
    "SERPER_MODE_NOT_PAGINATED",
    "SERPER_MODE_PAGINATION_UNVERIFIED",
    "SERPER_PAGE_INVALID",
})

#: Failures **after** dispatch: the request went out, so §2 says FAILED, not
#: BLOCKED. `caveats[0]` names the adapter-local condition directly; the Gateway
#: only requires a closed-set code for a BLOCKED result, which is why these have
#: no :data:`CONTRACT_ANALOGUE` entry — a policy code here would let a failure
#: that really happened be recorded as one that never left.
POST_DISPATCH_CODES: frozenset[str] = frozenset({
    "SERPER_AMBIGUOUS_TIMEOUT",
    "SERPER_UPSTREAM_FAILED",
    "SERPER_BODY_TOO_LARGE",
    "SERPER_BODY_INVALID",
    "SERPER_DECLARED_ERROR",
    "SERPER_RESULT_LIST_INVALID",
})

ADAPTER_LOCAL_REASONS: frozenset[str] = _PRE_DISPATCH_LOCAL_REASONS | POST_DISPATCH_CODES

#: Local code → the §5.3 code that already means this. Each line's test is "that
#: clause of the contract says exactly this thing", not "the downstream wants a
#: string".
#:
#: * an unbound transport / an absent credential → ``CAPABILITY_NOT_AVAILABLE``:
#:   the capability exists but cannot be exercised on this host right now.
#: * every parameter-shaped refusal → ``CAPABILITY_PARAM_NOT_ALLOWED``: §5.3's own
#:   name for "the request contains something this capability does not accept".
CONTRACT_ANALOGUE: Mapping[str, str] = {
    "SERPER_TRANSPORT_UNBOUND": "CAPABILITY_NOT_AVAILABLE",
    "SERPER_CREDENTIAL_ABSENT": "CAPABILITY_NOT_AVAILABLE",
    "SERPER_PARAMS_NOT_A_MAPPING": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPER_ROUTE_PARAM_FORBIDDEN": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPER_QUERY_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPER_LOCALE_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPER_LIMIT_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPER_MODE_UNSUPPORTED": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPER_MODE_REQUIRES_UNSENT_PARAM": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPER_MODE_NOT_PAGINATED": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPER_MODE_PAGINATION_UNVERIFIED": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPER_PAGE_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
}

#: §3.4 ``AdapterResult``, field for field. The shared type is W-B's; this module
#: neither imports nor copies it — it produces a mapping that an ``AdapterResult``
#: would accept, and a frozen dataclass the Gateway can read attributes off.
ADAPTER_RESULT_FIELDS: tuple[str, ...] = (
    "outcome", "payload", "artifacts", "upstream_ref", "actual_usd",
    "actual_source", "quota_units_used", "transport", "schema_verdict", "caveats",
)

OUTCOME_OK = "OK"
OUTCOME_BLOCKED = "BLOCKED"
OUTCOME_FAILED = "FAILED"
#: §2: "timeout/interrupted, cost and effect both unknown". A request that left
#: with no answer is this, not FAILED — the Gateway holds an UNKNOWN and
#: settles a FAILED, so choosing FAILED here would book an unknown charge as
#: one that was resolved.
OUTCOME_UNKNOWN = "UNKNOWN"

_SECRET_KEY = re.compile(
    r"(secret|token|password|passwd|api[_-]?key|apikey|authorization|cookie|"
    r"credential|private[_-]key|session[_-]id|bearer)",
    re.IGNORECASE,
)
_MAX_STRING = 20_000
_CLEANED = "<redacted>"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow a redirect. The route was chosen; a redirect is a new one."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("PROVIDER_REDIRECT_BLOCKED")


@dataclass(frozen=True)
class SerperResult:
    """The adapter's return value, field-for-field with §3.4 ``AdapterResult``."""

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

    def as_adapter_result_fields(self) -> dict[str, Any]:
        return to_adapter_result_fields(self)


def to_adapter_result_fields(result: SerperResult) -> dict[str, Any]:
    """Flatten into §3.4's field names, so ``AdapterResult(**payload)`` fits.

    The field set is *exactly* the contract's: a refusal code rides inside
    ``caveats``/``payload`` rather than becoming an eleventh key, because an extra
    key is a ``TypeError`` at integration time rather than here.
    """
    return {
        "outcome": result.outcome,
        "payload": dict(result.payload) if result.payload else None,
        "artifacts": tuple(result.artifacts),
        "upstream_ref": result.upstream_ref,
        "actual_usd": result.actual_usd,
        "actual_source": result.actual_source,
        "quota_units_used": result.quota_units_used,
        "transport": result.transport,
        "schema_verdict": result.schema_verdict,
        "caveats": tuple(result.caveats),
    }


def _reason(code: str) -> str:
    """A reason code must be in the closed set; a typo fails here, not in a report."""
    if code not in ADAPTER_LOCAL_REASONS and code not in CONTRACT_REASON_CODES:
        raise AssertionError(f"理由码 {code!r} 不在闭集里 —— 见 CONTRACT_REASON_CODES")
    return code


def _contract_code(code: str) -> str:
    """Map a local code to the §5.3 code the Gateway can read.

    Already-contract codes pass through; post-dispatch codes have no analogue by
    design; anything else must be in :data:`CONTRACT_ANALOGUE`. An unmapped code
    asserts rather than guessing, because a guessed mapping is exactly what makes
    the closed set unauditable.
    """
    if code in CONTRACT_REASON_CODES:
        return code
    if code in POST_DISPATCH_CODES:
        raise AssertionError(
            f"{code!r} 是派发之后的失败，没有 §5.3 对应码 —— 它必须以 FAILED 报出，"
            "不能翻成一个政策码。"
        )
    try:
        return CONTRACT_ANALOGUE[code]
    except KeyError:
        raise AssertionError(
            f"本地理由码 {code!r} 没有契约对应码 —— 加进 CONTRACT_ANALOGUE 并写明契约里"
            "哪一条说的就是这件事，或按 §8 走 CONTRACT-CHANGE-REQUEST。"
        ) from None


def _clean(value: Any, *, depth: int = 0) -> Any:
    """IA-6: an upstream response is untrusted input, not material."""
    if depth > 8:
        return "<truncated>"
    if isinstance(value, Mapping):
        cleaned = {}
        for key, item in list(value.items())[:200]:
            name = str(key)[:90]
            cleaned[name] = (_CLEANED if _SECRET_KEY.search(name)
                             else _clean(item, depth=depth + 1))
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean(item, depth=depth + 1) for item in value[:200]]
    if isinstance(value, str):
        return value[:_MAX_STRING]
    if value is None or type(value) in (int, float, bool):
        return value
    return str(value)[:200]


def _checked_query(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("SERPER_QUERY_INVALID")
    collapsed = " ".join(value.split())
    if not (MIN_QUERY_CHARS <= len(collapsed) <= MAX_QUERY_CHARS):
        raise ValueError("SERPER_QUERY_INVALID")
    return collapsed


def _checked_limit(value: Any) -> int:
    if value is None:
        return 10
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_LIMIT:
        raise ValueError("SERPER_LIMIT_INVALID")
    return value


def _checked_page(value: Any) -> int:
    if value is None:
        return 1
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("SERPER_PAGE_INVALID")
    return value


def _checked_country(value: Any) -> str:
    if not isinstance(value, str) or not _COUNTRY.fullmatch(value):
        raise ValueError("SERPER_LOCALE_INVALID")
    return value


def _checked_language(value: Any) -> str:
    if not isinstance(value, str) or not _LANGUAGE.fullmatch(value):
        raise ValueError("SERPER_LOCALE_INVALID")
    return value


def capabilities() -> tuple[str, ...]:
    """The capability ids this adapter serves, at module level.

    The instance method is what the Gateway looks up; this is what a
    Registry builder and an import-time check can read without
    constructing an adapter that may want a session or a credential.
    """
    return CAPABILITIES


def describe_capabilities() -> tuple[dict[str, Any], ...]:
    """Registry material: one row per served capability, in §4's vocabulary.

    The integrator builds ``CapabilityEntry`` objects from these rather than
    re-deriving tiers and parameter subsets, so there is one place where the
    adapter's claim and the Registry's entry can disagree — and this is it.
    """
    return tuple({
        "capability_id": capability_id,
        "provider": PROVIDER,
        "transport": TRANSPORT,
        "tier": TIER_BY_CAPABILITY[capability_id],
        "effect_class": EFFECT_CLASS_BY_CAPABILITY[capability_id],
        "allowed_params": tuple(sorted(ALLOWED_PARAMS[capability_id])),
        "availability": "ENABLED",
        "readiness": "CODED",
        "notes": ("Category is a validated parameter resolved through serper_modes, "
                  "not a capability id. Endpoint paths are third-party-sourced and "
                  "live-unverified; no request has been sent to Serper."),
    } for capability_id in CAPABILITIES)


class SerperAdapter:
    """§6 ``ProviderAdapter`` for Serper. ``provider`` is a class attribute.

    Every injectable argument exists so a property can be *decided* rather than
    assumed:

    * ``transport`` — the outbound callable. Unbound by default, so an unbound
      adapter cannot be an accidental network path; tests inject a recorder.
    * ``api_key`` — the credential **arrives as an argument** (IA-1). This module
      never reads ``SERPER_API_KEY`` from the environment: an environment
      fallback is a credential source the Gateway cannot see.
    * ``clock`` — injectable, for the same reason as elsewhere in this repo.
    """

    provider = PROVIDER

    def __init__(self, *, api_key: str | None = None, transport: Any | None = None,
                 clock: Any | None = None, artifact_writer: Any | None = None,
                 guard: Any | None = None) -> None:
        self._api_key = api_key if isinstance(api_key, str) and api_key.strip() else None
        self._transport = transport
        self._clock = clock
        self._artifact_writer = artifact_writer
        # Per-instance by default. Refusing a duplicate dispatch is this
        # adapter's job; the durable record of the operation is the ledger's.
        self._guard = guard if guard is not None else search_canonical.DispatchGuard()
        # Built even when a transport is injected: an unredirected opener is the
        # only client this adapter may use, and the test asserts it is installed.
        self._opener = urllib.request.build_opener(NoRedirect())

    # -- §6 Protocol -------------------------------------------------------
    def capabilities(self) -> tuple[str, ...]:
        return CAPABILITIES

    def probe_schema(self) -> Mapping[str, object]:
        """Read-only handshake. This Wave has no authorization for one.

        So it returns *which* prerequisite is missing rather than a snapshot. A
        fabricated snapshot would make a later ``verify_schema`` answer ``MATCH``
        against something nobody fetched.
        """
        if self._transport is None:
            detail = "SERPER_TRANSPORT_UNBOUND"
        elif self._api_key is None:
            detail = "SERPER_CREDENTIAL_ABSENT"
        else:
            detail = "DECISION-04_NOT_AUTHORIZED"
        return {
            "status": "AUTH_REQUIRED",
            "provider": PROVIDER,
            "transport": TRANSPORT,
            "upstream_tools_list": None,
            "schema_verdict": "UNVERIFIED",
            "detail": detail,
            "note": ("未获授权的只读握手不产生快照，此处不伪造一张。Serper 没有已核实的"
                     "厂商 MCP；本能力是 REST。"),
        }

    def execute(self, req: Any) -> SerperResult:
        """Dispatch one already-authorized request. Exactly once, never retried.

        Precondition: Policy and ``Ledger.reserve`` have already run. This method
        makes no tier, budget or routing decision — it validates the request
        against *this capability's* shape and sends it.
        """
        capability_id = str(getattr(req, "capability_id", "") or "")
        run_id = str(getattr(req, "run_id", "") or "")
        operation_id = str(getattr(req, "operation_id", "") or "")
        account_ref = str(getattr(req, "account_ref", "") or "")
        params = getattr(req, "params", None)

        if capability_id not in CAPABILITIES:
            # IR-1: unknown id → local BLOCKED, upstream never reached.
            return self._refusal("CAPABILITY_UNKNOWN",
                                 f"未知的 capability_id {capability_id!r}",
                                 capability_id=capability_id)
        if not isinstance(params, Mapping):
            return self._refusal("SERPER_PARAMS_NOT_A_MAPPING", "params 必须是一个映射",
                                 capability_id=capability_id)

        # Route first: "you tried to choose the endpoint" and "you sent a key I do
        # not know" are different sentences, and the first one is the security
        # finding. Checking it after the unknown-key rule would file it as a typo.
        injected = sorted(str(key) for key in params if str(key) in ROUTE_PARAMS_FORBIDDEN)
        if injected:
            return self._refusal("SERPER_ROUTE_PARAM_FORBIDDEN",
                                 f"请求里出现了路由选择参数：{injected}", capability_id=capability_id)

        allowed = ALLOWED_PARAMS[capability_id]
        unknown = sorted({str(key) for key in params} - allowed)
        if unknown:
            return self._refusal("CAPABILITY_PARAM_NOT_ALLOWED",
                                 f"不在 allowed_params 里的键：{unknown}",
                                 capability_id=capability_id)

        # The category is judged before the request *values*: "this route does not
        # exist" is a finding about where the caller aimed, and it must not be
        # dressed up as "your country code was malformed" because a locale
        # happened to be missing too. Two blocks, two different sentences.
        mode = str(params.get("mode") or "web")
        try:
            page = _checked_page(params.get("page"))
            self._check_mode(mode, page)
        except ValueError as exc:
            code = str(exc).split(":", 1)[0].strip()
            return self._refusal(code, f"{code}: 分类未通过校验", capability_id=capability_id)
        try:
            query = _checked_query(params.get("query"))
            country = _checked_country(params.get("country"))
            language = _checked_language(params.get("language"))
            limit = _checked_limit(params.get("limit"))
        except ValueError as exc:
            code = str(exc).split(":", 1)[0].strip()
            return self._refusal(code, f"{code}: 参数未通过校验", capability_id=capability_id)

        if self._transport is None:
            return self._refusal("SERPER_TRANSPORT_UNBOUND",
                                 "没有绑定出网通道；缺省路径不联网", capability_id=capability_id)
        if self._api_key is None:
            return self._refusal("SERPER_CREDENTIAL_ABSENT",
                                 "没有凭据；本模块不从环境变量取 key（IA-1）",
                                 capability_id=capability_id)

        arguments = search_canonical.canonical_arguments(
            provider=PROVIDER, mode=mode, query=query, country=country,
            language=language, limit=limit, page=page, engine=None,
            account=account_ref, transport="REST_HTTPS",
        )
        operation_key = search_canonical.operation_signature(arguments)

        # One logical operation, one dispatch — including across a caller's retry
        # after a timeout. The key is the ledger's Key B (transport-independent),
        # so a REST attempt and an MCP attempt at the same query are one
        # opportunity, not two.
        try:
            self._guard.begin(operation_key)
        except ValueError:
            return self._refusal(
                "DUPLICATE_OPERATION",
                "同一逻辑操作在本进程内已经派发过；超时之后的重发会变成第二次计费",
                capability_id=capability_id)
        result = self._dispatch(capability_id, arguments, mode, run_id, operation_id,
                                account_ref, country, language, limit, page, query,
                                operation_key)
        self._guard.resolve(operation_key, self._guard_outcome(result))
        return result

    def _dispatch(self, capability_id: str, arguments: Mapping[str, Any], mode: str,
                  run_id: str, operation_id: str, account_ref: str, country: str,
                  language: str, limit: int, page: int, query: str,
                  operation_key: str) -> SerperResult:
        request = self._build_request(mode, arguments)
        try:
            response = self._transport(request, TIMEOUT_SECONDS)
            with response as handle:
                if getattr(handle, "status", 200) != 200:
                    raise ValueError("SERPER_UPSTREAM_FAILED")
                raw = handle.read(MAX_BODY + 1)
        except urllib.error.HTTPError as exc:
            # Never stringify: a SerpApi-style URL could carry a key, and this
            # adapter's own URL never does — but the discipline is the same one
            # the whole repo uses, so it does not depend on that staying true.
            # A status *was* received, so this is a settled failure rather than an
            # unresolved one.
            return self._failure("SERPER_UPSTREAM_FAILED", f"HTTP {exc.code}",
                                 capability_id=capability_id)
        except TimeoutError:
            # Handed to the transport, no answer, no way to prove whether the
            # provider counted it. That is UNKNOWN, and the operation stays held.
            return self._failure("SERPER_AMBIGUOUS_TIMEOUT", "派发之后超时，费用未知",
                                 capability_id=capability_id, unknown=True)
        except urllib.error.URLError as exc:
            if research_contract.never_sent(exc):
                return self._refusal("EGRESS_REFUSED",
                                     "connection never established; request not sent, no charge",
                                     capability_id=capability_id)
            # No response object: the request may or may not have reached the
            # provider, so this holds the exposure too. A connection refused and a
            # reset after send look the same from here, and only one of them is
            # free — so the conservative reading is the one that cannot undercount.
            ambiguous = isinstance(getattr(exc, "reason", None), TimeoutError)
            return self._failure("SERPER_UPSTREAM_FAILED" if not ambiguous
                                 else "SERPER_AMBIGUOUS_TIMEOUT", "连接失败",
                                 capability_id=capability_id, unknown=True)
        except ValueError as exc:
            code = str(exc).split(":", 1)[0].strip()
            if code in POST_DISPATCH_CODES:
                return self._failure(code, f"{code}", capability_id=capability_id)
            raise

        if len(raw) > MAX_BODY:
            return self._failure("SERPER_BODY_TOO_LARGE", f"{len(raw)} bytes",
                                 capability_id=capability_id)
        try:
            body = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._failure("SERPER_BODY_INVALID", "响应不是 JSON",
                                 capability_id=capability_id)
        if not isinstance(body, dict):
            return self._failure("SERPER_BODY_INVALID", "响应不是对象",
                                 capability_id=capability_id)
        if body.get("error"):
            return self._failure("SERPER_DECLARED_ERROR", "上游声明了 error",
                                 capability_id=capability_id)

        spec = serper_modes.mode_spec(mode)
        rows = body.get(spec.result_key, [])
        if not isinstance(rows, list):
            return self._failure("SERPER_RESULT_LIST_INVALID",
                                 f"{spec.result_key} 不是列表", capability_id=capability_id)

        hits = self._normalise(rows, limit=limit, mode=mode)
        pricing = serper_modes.pricing_terms(mode)
        # The meter this concrete request incurs, not the category's abstract
        # terms: `num>10` on images doubles the credit, and a receipt that could
        # not see that could not be used to judge a run's budget.
        credits = serper_modes.credit_estimate(mode, num=limit)
        payload = {
            "schema": "marketops.serper.discovery.v1",
            "provider": PROVIDER,
            "transport": TRANSPORT,
            "capability_id": capability_id,
            "run_id": run_id,
            "operation_id": operation_id,
            "mode": mode,
            "endpoint_path": spec.endpoint_path,
            "query": arguments["query"],
            "country": country,
            "language": language,
            "limit": limit,
            "page": page,
            "result_count": len(hits),
            "results": hits,
            "source_status": "discovered_not_verified",
            "business_evidence": False,
            "pricing_source": pricing["source_id"],
            "pricing_version": pricing["version"],
            "priced_at": pricing["priced_at"],
            "unit_price_usd": pricing["unit_price_usd"],
            "credit_estimate": credits,
            # The same reading in the ledger's Price shape, so the campaign floor
            # can be held in credits even when the dollar side is unknown.
            "price_fields": serper_modes.price_fields(mode, num=limit),
            "logical_operation_key": operation_key,
            # The CD-03 identity this dispatch corresponds to. The ledger key is
            # B's; this is the structure both adapters agree on, recorded so a
            # receipt can be matched to a route_shape without re-deriving it.
            "route_identity": search_canonical.search_route_identity(
                provider=PROVIDER, account_ref=account_ref, query=arguments["query"],
                capability="search", mode=mode, country=country, language=language,
                limit=limit, page=page),
            "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        }
        artifacts: tuple[str, ...] = ()
        if self._artifact_writer is not None:
            artifacts = tuple(self._artifact_writer(payload) or ())
        return SerperResult(outcome=OUTCOME_OK, payload=payload, artifacts=artifacts,
                            transport=TRANSPORT, schema_verdict="UNVERIFIED",
                            actual_source="NONE",
                            caveats=("DISCOVERY_CANDIDATES_NOT_VERIFIED_BUSINESS_EVIDENCE",
                                     "SERPER_CATEGORY_COVERAGE_IS_DECLARED_NOT_LIVE_VERIFIED"))

    def route_identity(self, req: Any) -> dict:
        """This request's ledger identity, in CD-03's shared shape.

        A pure function of what the caller resolved: it reads no catalog, opens no
        connection and touches no cache. The integrator passes the ``args`` member
        to B's ``route_shape`` and the rest to ``OperationRequest``; because the
        MCP adapter builds the same shape from the same query, the two transports
        land on one digest (CD-01/CD-02a) while a different query or a different
        category does not (CD-02b).
        """
        params = getattr(req, "params", None) or {}
        return search_canonical.search_route_identity(
            provider=PROVIDER,
            account_ref=str(getattr(req, "account_ref", "") or ""),
            query=params.get("query"),
            capability="search",
            mode=str(params.get("mode") or "web"),
            country=params.get("country"),
            language=params.get("language"),
            limit=params.get("limit"),
            page=params.get("page"),
        )

    def cancel(self, upstream_ref: str) -> SerperResult:
        """Serper's REST surface declares no idempotent cancellation.

        Returning a success here would tell the caller a remote operation stopped
        when nothing was stopped, so the honest answer is that this route has no
        such action.
        """
        return SerperResult(
            outcome=OUTCOME_BLOCKED,
            payload={"reason": "CAPABILITY_NOT_AVAILABLE",
                     "upstream_ref": str(upstream_ref or ""),
                     "explanation": ("Serper REST 没有可取消的上游操作；一次检索请求"
                                     "没有可撤销的远端句柄。")},
            caveats=("CAPABILITY_NOT_AVAILABLE",
                     "cancel 说的是「这条路上没有这个动作」，不是「已经取消了」。"),
        )

    # -- internals ----------------------------------------------------------
    def _check_mode(self, mode: str, page: int) -> None:
        """Three refusals, three different sentences, all before the network."""
        spec = serper_modes.mode_spec(mode)      # raises UNSUPPORTED_SERPER_MODE
        if spec.dispatch != "OK":
            raise ValueError("SERPER_MODE_REQUIRES_UNSENT_PARAM")
        serper_modes.assert_page_allowed(mode, page)

    def _build_request(self, mode: str, arguments: Mapping[str, Any]) -> urllib.request.Request:
        """The wire shape, assembled only from the declared registry."""
        endpoint = serper_modes.HOST + serper_modes.mode_spec(mode).endpoint_path
        payload: dict[str, Any] = {
            "q": arguments["query"],
            "gl": str(arguments["country"]).lower(),
            "hl": arguments["language"],
            "num": arguments["limit"],
        }
        page = arguments["page"]
        paging = serper_modes.pagination_param(mode)
        if page > 1 and paging is not None:
            payload[paging] = page
        return urllib.request.Request(
            endpoint, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"X-API-KEY": self._api_key or "", "Content-Type": "application/json"},
        )

    @staticmethod
    def _normalise(rows: Sequence[Any], *, limit: int, mode: str) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for row in rows:
            item = row
            if mode == "autocomplete" and isinstance(item, str):
                item = {"title": item}
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("title") or item.get("name") or item.get("value") or "")
            href = str(item.get("link") or item.get("website") or item.get("sourceUrl")
                       or item.get("productLink") or "")
            parsed = urllib.parse.urlsplit(href)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                href = ""
            hits.append(_clean({
                "status": "discovered",
                "provider": PROVIDER,
                "kind": "search_candidate",
                "search_mode": mode,
                "title": name[:500],
                "url": href[:2000],
                "snippet": str(item.get("snippet") or item.get("description") or "")[:1000],
                "rank": len(hits) + 1,
            }))
            if len(hits) >= limit:
                break
        return hits

    def _refusal(self, code: str, detail: str, *, capability_id: str = "") -> SerperResult:
        """A pre-dispatch refusal: ``BLOCKED``, with both codes carried.

        ``caveats[0]`` is the §5.3 member the Gateway reads; ``payload["reason"]``
        is the precise local code, which is the only place the difference between
        "no transport" and "no credential" survives once both map to
        ``CAPABILITY_NOT_AVAILABLE``.
        """
        reason = _reason(code)
        if reason in POST_DISPATCH_CODES:
            raise AssertionError(
                f"{reason!r} 是派发之后的失败，必须走 _failure —— 记成 BLOCKED 会说"
                "「我们没出去」，而那是假的。")
        contract_code = _contract_code(reason)
        return SerperResult(
            outcome=OUTCOME_BLOCKED,
            payload={"reason": reason, "contract_reason_code": contract_code,
                     "detail": f"{reason}: {_clean(detail) if isinstance(detail, str) else detail}",
                     "capability_id": capability_id or None},
            transport=TRANSPORT, schema_verdict="UNVERIFIED",
            caveats=(contract_code,
                     "本地拒绝，未触达上游（§2：BLOCKED 是「本地拒绝，未触达上游」）。"),
        )

    def _failure(self, code: str, detail: str, *, capability_id: str = "",
                 unknown: bool = False) -> SerperResult:
        """A post-dispatch failure: §2's ``FAILED``, not ``BLOCKED``.

        ``unknown`` says the *charge* is unresolved — the request left and no
        answer came back, so nothing can prove whether the provider counted it.
        The outcome stays ``FAILED`` (the call did not succeed) and the financial
        state is a different field on purpose: collapsing "no answer" into "cost
        nothing" is how an unresolved charge gets settled at zero.
        """
        reason = _reason(code)
        if reason not in POST_DISPATCH_CODES:
            raise AssertionError(
                f"{reason!r} 不在 POST_DISPATCH_CODES 里 —— 没派发过的失败记成 FAILED 会"
                "反过来把「没出去」说成「出去了」。")
        return SerperResult(
            outcome=(OUTCOME_UNKNOWN if unknown else OUTCOME_FAILED),
            payload={"reason": reason, "detail": _clean(detail),
                     "capability_id": capability_id or None,
                     "charge_state": "UNKNOWN" if unknown else "UNSETTLED_NOT_UNKNOWN"},
            transport=TRANSPORT, schema_verdict="UNVERIFIED",
            caveats=(reason, "请求已经发出（上游明确失败），所以这是 FAILED 而不是 BLOCKED。"),
        )

    @staticmethod
    def _guard_outcome(result: SerperResult) -> str:
        if result.outcome == OUTCOME_OK:
            return search_canonical.DISPATCH_RESOLVED[0]
        if result.outcome == OUTCOME_BLOCKED:
            return "BLOCKED"
        if result.outcome == OUTCOME_UNKNOWN:
            return search_canonical.DISPATCH_UNKNOWN
        return "FAILED"



__all__ = [
    "ADAPTER_LOCAL_REASONS", "ADAPTER_RESULT_FIELDS", "ALLOWED_PARAMS", "CAPABILITIES",
    "CONTRACT_ANALOGUE", "CONTRACT_REASON_CODES", "DECLARED_NOT_SERVED",
    "EFFECT_CLASS_BY_CAPABILITY", "MAX_BODY", "NoRedirect", "POST_DISPATCH_CODES",
    "OUTCOME_UNKNOWN", "PROVIDER", "ROUTE_PARAMS_FORBIDDEN", "SerperAdapter", "SerperResult",
    "TIER_BY_CAPABILITY", "TRANSPORT", "describe_capabilities",
    "to_adapter_result_fields",
]

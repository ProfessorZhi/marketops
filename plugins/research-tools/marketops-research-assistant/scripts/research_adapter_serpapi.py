#!/usr/bin/env python3
"""SerpApi as a typed adapter: engine-resource discovery, then one search.

## The discovery this module exists for

SerpApi's official MCP does not describe its engines in the ``search`` tool
schema. It publishes them as **MCP resources** — an index at
``serpapi://engines`` and one parameter schema per engine at
``serpapi://engines/<engine>`` — and those two URIs are the whole contract,
read from the vendor's own MCP README rather than inferred.

So discovery here is two capabilities, not a background sync:

* ``serpapi.engine_catalog`` reads the index **once** and records the engine
  names with where they came from and when.
* ``serpapi.engine_schema`` reads **one** engine's schema, on request, and pins
  it under the sha256 it was read at.

Nothing ever reads all of them. :mod:`serpapi_engine_catalog` explains why at
length; the short version is that flattening ~100+ vendor-authored schemas into
a tool result is the "expose every upstream method indiscriminately" failure the
product brief names, and a vendor description is untrusted input rather than an
instruction to the host.

## Why the engine is resolved through the catalog, not validated as a string

``serpapi.search`` takes an ``engine``. It is not checked against a regex and
sent — it is resolved with :func:`serpapi_engine_catalog.resolve_engine`, which
distinguishes four outcomes that a caller needs to tell apart:

``CAPABILITY_PARAM_NOT_ALLOWED`` when the name is not a name this adapter would
put in a URI; ``CAPABILITY_UNKNOWN`` with ``INDEX_NOT_READ`` when the index has
never been read, so *no* engine can be shown to exist; ``CAPABILITY_UNKNOWN``
with ``INDEX_READ`` when the index was read and this engine is absent;
``CAPABILITY_NOT_AVAILABLE`` when the engine is real but its schema has not been
fetched yet. Collapsing the middle two would let "we have never looked" be read
as "the vendor does not have it", and would send the caller to re-read the index
when the right move is to read one resource.

## What is deliberately absent

The adapter does not decide tier, budget or routing (IA-2) — an adapter that
re-checked any of it would be a second policy source. It does not retry (IA-3):
one dispatch is one charge. It does not open its own MCP session; the session is
injected, so an unbound adapter is not an accidental network path and the
ownership of the connection stays with whoever owns the credential. And it never
reads a key from the environment (IA-1); the offline fake sessions in the tests
need none.

## Honest status

No connection has been made to ``mcp.serpapi.com``, no ``resources/list`` or
``resources/read`` has been performed against it, and the engine count is not a
number we measured. Everything here is ``CODED`` / ``OFFLINE_TESTED`` against a
fake session; ``UPSTREAM_SCHEMA_VERIFIED`` needs the separately authorized
read-only handshake, and a live probe needs the integrated budget first.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

import search_canonical
import serpapi_engine_catalog as catalog_module

PROVIDER = "serpapi"
TRANSPORT = "official_mcp"

#: This adapter's served capabilities. Order is the surface order the tests pin.
CAPABILITIES: tuple[str, ...] = (
    "serpapi.engine_catalog",
    "serpapi.engine_schema",
    "serpapi.search",
)

#: Tier is data, not a decision. All three sit at T2 for one reason: each is a
#: remote, credentialed call to the vendor.
#:
#: The two resource reads are *not billed per call to our knowledge* — and that
#: knowledge is the problem. The convergence review says a remote read-only call
#: is not thereby local or free, and no account price was ever read here. So
#: they stay behind the same operator switch as the search instead of being
#: promoted to a cheaper rung on an unverified zero. ``effect_class`` stays
#: ``READ_ONLY`` because that field records what the call *does*, and asserting
#: ``BILLABLE_READ`` would be claiming a charge nobody read either.
TIER_BY_CAPABILITY: Mapping[str, str] = {
    "serpapi.engine_catalog": "T2",
    "serpapi.engine_schema": "T2",
    "serpapi.search": "T2",
}

EFFECT_CLASS_BY_CAPABILITY: Mapping[str, str] = {
    "serpapi.engine_catalog": "READ_ONLY",
    "serpapi.engine_schema": "READ_ONLY",
    "serpapi.search": "BILLABLE_READ",
}

#: The exposed parameter subset. ``mode`` is deliberately *not* here: the
#: upstream tool requires ``mode="compact"`` under our policy, so it is a
#: constant this adapter sets rather than a knob a host could turn off.
ALLOWED_PARAMS: Mapping[str, frozenset[str]] = {
    "serpapi.engine_catalog": frozenset(),
    "serpapi.engine_schema": frozenset({"engine", "refresh"}),
    "serpapi.search": frozenset({"engine", "query", "country", "language", "limit"}),
}

#: Declared upstream paths this adapter does **not** serve, kept visible so the
#: catalog can say "disabled" rather than leave a hole that reads as an
#: oversight. The legacy Google REST table is the case: it is a second route to
#: the same paid vendor and a default install does not have it, but the product
#: brief asks for it to survive until the official MCP contract is validated.
DECLARED_NOT_SERVED: Mapping[str, Mapping[str, str]] = {
    "serpapi.rest_search": {
        "availability": "DISABLED",
        "reason": ("Legacy Google REST compatibility table; default-off, enabled only by "
                   "an explicit operator decision (MARKETOPS_SERPAPI_REST_ENABLE=1). "
                   "Managed by dual_search_mcp, not by this adapter."),
        "evidence": ("W-D Wave-1 default-off change; a second route to the same paid "
                     "vendor is not a default-install capability"),
    },
}

#: Every route-choosing parameter a host must not be able to supply. Checked
#: before the generic unknown-key rule so the refusal names the injection
#: rather than reporting a spelling problem.
ROUTE_PARAMS_FORBIDDEN: frozenset[str] = frozenset({
    "endpoint", "url", "uri", "host", "hostname", "base_url", "origin", "path",
    "tool", "tool_name", "method", "headers", "body", "api_key", "apikey", "key",
    "token", "access_token", "password", "secret", "credential", "credentials",
    "transport", "protocol", "tier", "effect_class", "provider", "grant",
    "grant_ref", "budget", "budget_usd", "reservation", "ledger_id",
    # The MCP tool name is a constant; `session` and `resource_uri` are the
    # connection itself, not request data.
    "session", "resource_uri", "uri_template", "mcp_url",
})

#: §6's transport table names SerpApi's official MCP as the single managed path.
OFFICIAL_MCP_ENDPOINT = "https://mcp.serpapi.com/mcp"
#: The one upstream tool this adapter calls. A constant, never a parameter.
SEARCH_TOOL = "search"
#: Required by our policy upstream; not exposed, so it cannot be turned off.
UPSTREAM_MODE = "compact"

MAX_QUERY_CHARS = 250
MIN_QUERY_CHARS = 2
MAX_LIMIT = 20

_COUNTRY = re.compile(r"^[A-Z]{2}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}$")

#: §5.3's ``reason_code`` closed set, transcribed verbatim.
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

_PRE_DISPATCH_LOCAL_REASONS: frozenset[str] = frozenset({
    "SERPAPI_SESSION_UNBOUND",
    "SERPAPI_PARAMS_NOT_A_MAPPING",
    "SERPAPI_ROUTE_PARAM_FORBIDDEN",
    "SERPAPI_QUERY_INVALID",
    "SERPAPI_LOCALE_INVALID",
    "SERPAPI_LIMIT_INVALID",
    # The name is the catalog module's, not a second spelling of it: two names
    # for one refusal is how a caller ends up branching on the wrong one.
    "SERPAPI_ENGINE_NAME_INVALID",
    "SERPAPI_ENGINE_INDEX_NOT_READ",
    "SERPAPI_ENGINE_UNKNOWN",
    "SERPAPI_ENGINE_SCHEMA_NOT_CACHED",
    "SERPAPI_ENGINE_CACHE_INTEGRITY",
    "SERPAPI_ENGINE_CACHE_UNREADABLE",
    # Refused after listing, before reading: nothing was asked for.
    "SERPAPI_RESOURCE_NOT_ADVERTISED",
    "SERPAPI_REFRESH_INVALID",
    "SERPAPI_SYNC_CALL_INSIDE_RUNNING_LOOP",
})

#: Failures **after** dispatch: the request went out, so §2 says FAILED, not
#: BLOCKED. A policy code here would let a failure that really happened be
#: recorded as one that never left, so these have no :data:`CONTRACT_ANALOGUE`
#: entry and :func:`_contract_code` refuses to invent one.
POST_DISPATCH_CODES: frozenset[str] = frozenset({
    "SERPAPI_AMBIGUOUS_TIMEOUT",
    "SERPAPI_DECLARED_ERROR",
    "SERPAPI_ENGINE_SCHEMA_UNREADABLE",
    "SERPAPI_RESOURCE_EMPTY",
    "SERPAPI_RESOURCE_UNAVAILABLE",
    "SERPAPI_RESULT_INVALID",
    "SERPAPI_RESULT_TOO_LARGE",
    "SERPAPI_SCHEMA_TOO_LARGE",
    "SERPAPI_TOOL_NOT_ADVERTISED",
    "SERPAPI_UPSTREAM_FAILED",
})

ADAPTER_LOCAL_REASONS: frozenset[str] = _PRE_DISPATCH_LOCAL_REASONS | POST_DISPATCH_CODES

#: Local code → the §5.3 code that already means this. Each line's test is "that
#: clause of the contract says exactly this thing".
#:
#: * an unbound session, a schema that was never read, a cache that fails its own
#:   pin, a loop we cannot run the coroutine on → ``CAPABILITY_NOT_AVAILABLE``:
#:   the capability exists but cannot be exercised here.
#: * an index not yet read and an engine absent from a read index both →
#:   ``CAPABILITY_UNKNOWN``: §5.3's own name for "this capability resolves to
#:   nothing we know about". They stay distinguishable in ``payload["index_state"]``,
#:   which is where the difference between "not looked yet" and "not there" lives.
#: * an empty resource body → ``CAPABILITY_NOT_AVAILABLE``: the resource exists
#:   and did not answer.
#: * every parameter-shaped refusal → ``CAPABILITY_PARAM_NOT_ALLOWED``.
CONTRACT_ANALOGUE: Mapping[str, str] = {
    "SERPAPI_SESSION_UNBOUND": "CAPABILITY_NOT_AVAILABLE",
    "SERPAPI_PARAMS_NOT_A_MAPPING": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPAPI_ROUTE_PARAM_FORBIDDEN": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPAPI_QUERY_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPAPI_LOCALE_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPAPI_LIMIT_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPAPI_ENGINE_NAME_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPAPI_ENGINE_INDEX_NOT_READ": "CAPABILITY_UNKNOWN",
    "SERPAPI_ENGINE_UNKNOWN": "CAPABILITY_UNKNOWN",
    "SERPAPI_ENGINE_SCHEMA_NOT_CACHED": "CAPABILITY_NOT_AVAILABLE",
    "SERPAPI_ENGINE_CACHE_INTEGRITY": "CAPABILITY_NOT_AVAILABLE",
    "SERPAPI_ENGINE_CACHE_UNREADABLE": "CAPABILITY_NOT_AVAILABLE",
    "SERPAPI_RESOURCE_NOT_ADVERTISED": "CAPABILITY_NOT_AVAILABLE",
    "SERPAPI_REFRESH_INVALID": "CAPABILITY_PARAM_NOT_ALLOWED",
    "SERPAPI_SYNC_CALL_INSIDE_RUNNING_LOOP": "CAPABILITY_NOT_AVAILABLE",
}

#: §3.4 ``AdapterResult``, field for field. The shared type is W-B's and is still
#: absent from every branch; this module neither imports nor copies it — it
#: produces a mapping an ``AdapterResult`` would accept, and a frozen dataclass
#: the Gateway can read attributes off.
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


@dataclass(frozen=True)
class SerpApiResult:
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


def to_adapter_result_fields(result: SerpApiResult) -> dict[str, Any]:
    """Flatten into §3.4's field names, so ``AdapterResult(**payload)`` fits."""
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
    """Map a local code to the §5.3 code the Gateway can read."""
    if code in CONTRACT_REASON_CODES:
        return code
    if code in POST_DISPATCH_CODES:
        raise AssertionError(
            f"{code!r} 是派发之后的失败，没有 §5.3 对应码 —— 它必须以 FAILED 报出，"
            "不能翻成一个政策码。")
    try:
        return CONTRACT_ANALOGUE[code]
    except KeyError:
        raise AssertionError(
            f"本地理由码 {code!r} 没有契约对应码 —— 加进 CONTRACT_ANALOGUE 并写明契约里"
            "哪一条说的就是这件事，或按 §8 走 CONTRACT-CHANGE-REQUEST。") from None


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
        raise ValueError("SERPAPI_QUERY_INVALID")
    collapsed = " ".join(value.split())
    if not (MIN_QUERY_CHARS <= len(collapsed) <= MAX_QUERY_CHARS):
        raise ValueError("SERPAPI_QUERY_INVALID")
    return collapsed


def _checked_limit(value: Any) -> int:
    if value is None:
        return 10
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_LIMIT:
        raise ValueError("SERPAPI_LIMIT_INVALID")
    return value


def _checked_country(value: Any) -> str:
    if not isinstance(value, str) or not _COUNTRY.fullmatch(value):
        raise ValueError("SERPAPI_LOCALE_INVALID")
    return value


def _checked_language(value: Any) -> str:
    if not isinstance(value, str) or not _LANGUAGE.fullmatch(value):
        raise ValueError("SERPAPI_LOCALE_INVALID")
    return value


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Read a tool result without assuming one SDK's attribute casing.

    ``research_official_mcp._extract`` already had to handle both
    ``structuredContent`` and ``structured_content``; the same courtesy applies
    here rather than pinning this module to one SDK version.
    """
    structured = getattr(value, "structuredContent", None)
    if structured is None:
        structured = getattr(value, "structured_content", None)
    if isinstance(structured, Mapping):
        inner = structured.get("result")
        if isinstance(inner, str):
            try:
                parsed = json.loads(inner)
            except json.JSONDecodeError:
                return {"text": inner}
            return parsed if isinstance(parsed, Mapping) else {"value": parsed}
        return structured
    if isinstance(structured, str):
        try:
            parsed = json.loads(structured)
        except json.JSONDecodeError:
            return {"text": structured}
        return parsed if isinstance(parsed, Mapping) else {"value": parsed}
    blocks = getattr(value, "content", None)
    if isinstance(blocks, (list, tuple)) and blocks:
        text = getattr(blocks[0], "text", None)
        if isinstance(text, str):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
            return parsed if isinstance(parsed, Mapping) else {"value": parsed}
    return {}


def capabilities() -> tuple[str, ...]:
    """The capability ids this adapter serves, at module level.

    The instance method is what the Gateway looks up; this is what a
    Registry builder and an import-time check can read without
    constructing an adapter that may want a session or a credential.
    """
    return CAPABILITIES


def describe_capabilities() -> tuple[dict[str, Any], ...]:
    """Registry material: one row per served capability, in §4's vocabulary."""
    return tuple({
        "capability_id": capability_id,
        "provider": PROVIDER,
        "transport": TRANSPORT,
        "tier": TIER_BY_CAPABILITY[capability_id],
        "effect_class": EFFECT_CLASS_BY_CAPABILITY[capability_id],
        "allowed_params": tuple(sorted(ALLOWED_PARAMS[capability_id])),
        "availability": "ENABLED",
        "readiness": "CODED",
        "resources_reviewed": catalog_module.REVIEWED_ENGINE_RESOURCES,
        "notes": ("Engine schemas are read one resource at a time and cached under the "
                  "sha256 they were read at; no engine is resolved as a free-form "
                  "string. Live resource discovery is unverified: no connection has "
                  "been made to mcp.serpapi.com."),
    } for capability_id in CAPABILITIES)


class SerpApiAdapter:
    """§6 ``ProviderAdapter`` for SerpApi's official MCP. ``provider`` is a class attrs.

    Every injectable argument exists so a property can be *decided*:

    * ``session`` — the already-connected official MCP session. Unbound by
      default, so an unbound adapter cannot be an accidental network path, and
      connection ownership stays with whoever owns the credential.
    * ``catalog`` — the engine cache. Unbound means :meth:`EngineCatalog.default`,
      which lives under the customer data root beside the run ledger.
    * ``runner`` — how a coroutine is driven. Default is :func:`asyncio.run`;
      tests inject nothing and get the same path production uses.
    * ``artifact_writer`` — optional; ``artifacts`` stays empty without it, so
      this adapter never writes to a store it does not own (W-F owns that one).
    """

    provider = PROVIDER

    def __init__(self, *, session: Any | None = None, catalog: Any | None = None,
                 runner: Any | None = None, clock: Any | None = None,
                 artifact_writer: Any | None = None,
                 guard: Any | None = None, session_factory=None,
                 auto_discover: bool = False) -> None:
        self._session = session
        self._session_factory = session_factory
        self._auto_discover = auto_discover
        self._catalog = catalog
        self._runner = runner
        self._clock = clock
        self._artifact_writer = artifact_writer
        # Per-instance by default: refusing a duplicate dispatch is this
        # adapter's job, and the durable record of the operation is the ledger's.
        self._guard = guard if guard is not None else search_canonical.DispatchGuard()

    # -- §6 Protocol -------------------------------------------------------
    def capabilities(self) -> tuple[str, ...]:
        return CAPABILITIES

    def probe_schema(self) -> Mapping[str, object]:
        """Read-only handshake. This Wave has no authorization for one.

        So it says *which* prerequisite is missing instead of returning a
        snapshot. A fabricated snapshot would make a later ``verify_schema``
        answer ``MATCH`` against something nobody fetched, and the engine count
        in it would be a number we never measured.
        """
        if self._session_factory is not None:
            async def probe():
                from research_runtime_transport import tools_payload
                async with self._session_factory() as session:
                    return tools_payload(await session.list_tools())
            return self._drive(probe())
        if self._session is None:
            detail = "SERPAPI_SESSION_UNBOUND"
        else:
            detail = "DECISION-04_NOT_AUTHORIZED"
        return {
            "status": "AUTH_REQUIRED",
            "provider": PROVIDER,
            "transport": TRANSPORT,
            "endpoint": OFFICIAL_MCP_ENDPOINT,
            "upstream_tools_list": None,
            "engine_index_uri": catalog_module.INDEX_URI,
            "engine_count": None,
            "schema_verdict": "UNVERIFIED",
            "resources_reviewed": catalog_module.REVIEWED_ENGINE_RESOURCES,
            "review_note": catalog_module.RESOURCE_REVIEW_NOTE,
            "detail": detail,
            "note": ("未获授权的只读握手不产生快照，此处不伪造一张：一张假快照会让 "
                     "verify_schema 报 MATCH，而那个 MATCH 是假的；引擎数量也不填，"
                     "因为那不是我们数出来的。"),
        }

    def execute(self, req: Any) -> SerpApiResult:
        """Dispatch one already-authorized request. Exactly once, never retried.

        Precondition: Policy and ``Ledger.reserve`` have already run. This method
        makes no tier, budget or routing decision — it validates the request
        against *this capability's* shape and sends it.
        """
        if self._session_factory is not None:
            # MCP's task groups and all session methods live on this one loop.
            # The existing synchronous adapter runs in a worker and submits its
            # coroutines to that loop; it never creates an orphan SDK session.
            async def run():
                async with self._session_factory() as session:
                    loop = asyncio.get_running_loop()
                    bound = SerpApiAdapter(session=session, catalog=self._catalog,
                        runner=lambda work: asyncio.run_coroutine_threadsafe(work, loop).result(90),
                        clock=self._clock, artifact_writer=self._artifact_writer,
                        guard=self._guard, auto_discover=True)
                    return await asyncio.to_thread(bound.execute, req)
            try:
                return asyncio.run(run())
            except ValueError as exc:
                from research_runtime_transport import BeforeDispatchRefused
                if isinstance(exc, BeforeDispatchRefused):
                    return self._refusal(str(exc), "runtime refused before tools/call",
                        capability_id=str(getattr(req, "capability_id", "")))
                return SerpApiResult(outcome=OUTCOME_UNKNOWN,
                    caveats=("SERPAPI_AMBIGUOUS_TIMEOUT",))
            except Exception:
                # Do not echo transport/key-bearing errors; the ledger holds
                # ambiguous exposure and prevents a second dispatch.
                return SerpApiResult(outcome=OUTCOME_UNKNOWN,
                    caveats=("SERPAPI_AMBIGUOUS_TIMEOUT",))
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
            return self._refusal("SERPAPI_PARAMS_NOT_A_MAPPING", "params 必须是一个映射",
                                 capability_id=capability_id)

        # Route first: "you tried to choose the endpoint or the tool" and "you sent
        # a key I do not know" are different sentences, and the first one is the
        # finding. Checking it after the unknown-key rule would file it as a typo.
        injected = sorted(str(key) for key in params if str(key) in ROUTE_PARAMS_FORBIDDEN)
        if injected:
            return self._refusal("SERPAPI_ROUTE_PARAM_FORBIDDEN",
                                 f"请求里出现了路由选择参数：{injected}",
                                 capability_id=capability_id)

        allowed = ALLOWED_PARAMS[capability_id]
        unknown = sorted({str(key) for key in params} - allowed)
        if unknown:
            return self._refusal("CAPABILITY_PARAM_NOT_ALLOWED",
                                 f"不在 allowed_params 里的键：{unknown}",
                                 capability_id=capability_id)

        if self._session is None:
            return self._refusal("SERPAPI_SESSION_UNBOUND",
                                 "没有绑定官方 MCP 会话；缺省路径不联网",
                                 capability_id=capability_id)

        # One logical operation, one dispatch — including across a caller's retry
        # after a timeout. The key is transport-independent and derived from what
        # the operation is, so a resource read does not have to pretend to be a
        # search request to get one.
        #
        # The guard is claimed **only when the operation will actually reach
        # upstream**. A schema served from the pinned cache makes no request and
        # so cannot be a second charge (IL-5), which means it must neither take
        # the dispatch slot nor be refused as a duplicate.
        operation_key = (self._operation_key(capability_id, params)
                         if self._dispatches_upstream(capability_id, params) else None)
        if operation_key is not None:
            try:
                self._guard.begin(operation_key)
            except ValueError:
                return self._refusal(
                    "DUPLICATE_OPERATION",
                    "同一逻辑操作在本进程内已经派发过；超时之后的重发会变成第二次计费",
                    capability_id=capability_id)

        try:
            result = self._dispatch(capability_id, run_id, operation_id,
                                    account_ref, params)
        except TimeoutError:
            # The request left and the answer did not come back. That is a charge
            # nobody can prove either way, so the operation stays held rather
            # than being refunded or re-sent.
            result = self._failure("SERPAPI_AMBIGUOUS_TIMEOUT",
                                   "派发之后超时，费用未知", capability_id=capability_id,
                                   unknown=True)
        except ValueError as exc:
            code = str(exc).split(":", 1)[0].strip()
            if code in POST_DISPATCH_CODES:
                result = self._failure(code, f"{code}", capability_id=capability_id)
            elif code in ADAPTER_LOCAL_REASONS or code in CONTRACT_REASON_CODES:
                result = self._refusal(code, f"{code}: 调用未通过校验",
                                       capability_id=capability_id)
            else:
                raise
        if operation_key is not None:
            self._guard.resolve(operation_key, self._guard_outcome(result))
        return result

    def _dispatches_upstream(self, capability_id: str, params: Mapping[str, Any]) -> bool:
        """Whether this request will put bytes on the wire.

        Only ``engine_schema`` can answer no: it is served from a pinned cache
        unless a refresh is asked for, or unless nothing is cached yet. A refused
        engine resolves to ``False`` here — nothing will be dispatched, so nothing
        needs a dispatch slot, and the normal path still refuses it.
        """
        if capability_id != "serpapi.engine_schema":
            return True
        if params.get("refresh") is True:
            return True
        catalog = self._engine_catalog()
        try:
            catalog_module.engine_uri(params.get("engine"))
        except ValueError:
            return False
        return catalog.cached_schema(params.get("engine")) is None

    def _dispatch(self, capability_id: str, run_id: str, operation_id: str,
                  account_ref: str, params: Mapping[str, Any]) -> SerpApiResult:
        if capability_id == "serpapi.engine_catalog":
            return self._discover_index(capability_id, run_id, operation_id)
        if capability_id == "serpapi.engine_schema":
            return self._discover_schema(capability_id, params)
        return self._search(capability_id, run_id, operation_id, account_ref, params)

    @staticmethod
    def _operation_key(capability_id: str, params: Mapping[str, Any]) -> str:
        """One key per logical operation, whatever the capability's shape is.

        The search capability keys on the same canonical set the ledger will key
        on; the resource reads key on their capability and their (scalar, sorted)
        parameters. Both are transport-independent, which is the property that
        makes a REST attempt and an MCP attempt one spend opportunity.
        """
        return search_canonical.resource_operation_key(
            provider=PROVIDER, capability_id=capability_id,
            params={str(name): params[name] for name in params})

    @staticmethod
    def _guard_outcome(result: SerpApiResult) -> str:
        if result.outcome == OUTCOME_OK:
            return search_canonical.DISPATCH_RESOLVED[0]
        if result.outcome == OUTCOME_BLOCKED:
            return "BLOCKED"
        if result.outcome == OUTCOME_UNKNOWN:
            return search_canonical.DISPATCH_UNKNOWN
        return "FAILED"


    def probe_surface(self, *, resources: tuple[str, ...] = (),
                      expected_tool_count: int | None = None) -> dict:
        """The bounded, read-only discovery of the bound session's surface.

        Deliberately **not** :meth:`probe_schema`. §6's handshake must never
        surprise a caller with network, and this one must never be mistaken for
        a verification: it returns ``live: False`` and ``reviewed: False``
        whatever it finds, because an in-memory or injected session is evidence
        about the contract, not about the vendor.

        It calls no tool. The report's ``methods`` list is the provenance, and
        ``tools/call`` is absent from it by construction.
        """
        if self._session is None:
            raise ValueError("SERPAPI_SESSION_UNBOUND")
        return self._drive(catalog_module.discover_surface(
            self._session, resources=tuple(resources),
            expected_tool_count=expected_tool_count))

    def route_identity(self, req: Any) -> dict:
        """This request's ledger identity, in CD-03's shared shape.

        SerpApi's vendor axis is ``engine``; it has no category parameter, so it
        does not fabricate one. Everything else — query normalisation, materialised
        defaults, the rejected adapter-private fields — comes from the shared
        function, which is what makes this comparable with the REST path's shape.
        """
        params = getattr(req, "params", None) or {}
        return search_canonical.search_route_identity(
            provider=PROVIDER,
            account_ref=str(getattr(req, "account_ref", "") or ""),
            query=params.get("query"),
            capability="search",
            engine=str(params.get("engine") or catalog_module.DEFAULT_ENGINE),
            country=params.get("country"),
            language=params.get("language"),
            limit=params.get("limit"),
        )

    def cancel(self, upstream_ref: str) -> SerpApiResult:
        """SerpApi's MCP surface declares no idempotent cancellation.

        A success here would tell the caller a remote operation stopped when
        nothing was stopped, so the honest answer is that this route has no such
        action.
        """
        return SerpApiResult(
            outcome=OUTCOME_BLOCKED,
            payload={"reason": "CAPABILITY_NOT_AVAILABLE",
                     "upstream_ref": str(upstream_ref or ""),
                     "explanation": ("SerpApi 官方 MCP 没有可取消的幂等操作；一次检索"
                                     "没有可撤销的远端句柄。")},
            caveats=("CAPABILITY_NOT_AVAILABLE",
                     "cancel 说的是「这条路上没有这个动作」，不是「已经取消了」。"),
        )

    # -- the two resource-read capabilities ---------------------------------
    def _discover_index(self, capability_id: str, run_id: str, operation_id: str) -> SerpApiResult:
        """Read ``serpapi://engines`` once. No engine schema is read here."""
        catalog = self._engine_catalog()
        recorded = self._drive(catalog_module.discover_index(self._session, catalog=catalog))
        payload = {
            "schema": "marketops.serpapi.engine-index.v1",
            "provider": PROVIDER,
            "transport": TRANSPORT,
            "capability_id": capability_id,
            "run_id": run_id,
            "operation_id": operation_id,
            "index_uri": catalog_module.INDEX_URI,
            "schema_uri_template": catalog_module.SCHEMA_URI_TEMPLATE,
            "engine_count": recorded["engine_count"],
            "index_sha256": recorded["sha256"],
            "observed_at": recorded["observed_at"],
            "source_uri": recorded["source_uri"],
            "engines": list(catalog.engine_names()),
            "engine_schemas_read": 0,
            "business_evidence": False,
        }
        return SerpApiResult(outcome=OUTCOME_OK, payload=payload, transport=TRANSPORT,
                             schema_verdict="UNVERIFIED", actual_source="NONE",
                             caveats=("ENGINE_NAMES_ARE_NOT_SCHEMAS",
                                      "NO_UPSTREAM_SCHEMA_VERIFICATION_PERFORMED"))

    def _discover_schema(self, capability_id: str, params: Mapping[str, Any]) -> SerpApiResult:
        """Read exactly one ``serpapi://engines/<engine>`` resource."""
        engine = params.get("engine")
        refresh = params.get("refresh", False)
        if not isinstance(refresh, bool):
            return self._refusal("SERPAPI_REFRESH_INVALID", "refresh 必须是布尔值",
                                 capability_id=capability_id)
        catalog = self._engine_catalog()
        # Membership, not cache state, is the gate here: for *this* capability a
        # schema that is not cached yet is the ordinary case, not a refusal. Using
        # resolve_engine's READY here would make the one capability that fills the
        # cache refuse to run until the cache was already full.
        verdict = self._engine_name_verdict(engine, catalog, capability_id)
        if verdict is not None:
            return verdict

        recorded = self._drive(catalog_module.discover_schema(
            self._session, engine, catalog=catalog, refresh=refresh))
        if recorded.get("schema_verdict") == "DRIFT":
            refusal = self._refusal(
                "CAPABILITY_SCHEMA_DRIFT",
                "刷新读到的 schema 与已接受的 pin 不一致；已记录篡改事实，未接受新值",
                capability_id=capability_id)
            refusal.payload["accepted_sha256"] = recorded.get("accepted_sha256")
            refusal.payload["observed_sha256"] = recorded.get("sha256")
            refusal.payload["engine"] = engine
            return refusal
        payload = {
            "schema": "marketops.serpapi.engine-schema.v1",
            "provider": PROVIDER,
            "transport": TRANSPORT,
            "capability_id": capability_id,
            "engine": engine,
            "source_uri": recorded["source_uri"],
            "sha256": recorded["sha256"],
            "source": recorded["source"],
            "schema_verdict": recorded.get("schema_verdict", "UNVERIFIED"),
            "engine_schema": recorded["schema"],
            "business_evidence": False,
        }
        return SerpApiResult(outcome=OUTCOME_OK, payload=payload, transport=TRANSPORT,
                             schema_verdict="UNVERIFIED", actual_source="NONE",
                             upstream_ref=recorded["source_uri"],
                             caveats=("SCHEMA_IS_VENDOR_AUTHORED_UNTRUSTED_INPUT",
                                      "NOT_A_SEARCH_AND_NOT_A_PRICING_STATEMENT"))

    # -- the search capability ----------------------------------------------
    def _search(self, capability_id: str, run_id: str, operation_id: str,
                account_ref: str, params: Mapping[str, Any]) -> SerpApiResult:
        # The engine is judged before the request *values*, for the same reason
        # the Serper adapter judges the category before them: "this engine is not
        # one we can name" is a statement about where the caller aimed, and it
        # must not be dressed up as a malformed country code because a locale
        # happened to be missing too.
        engine = params.get("engine", catalog_module.DEFAULT_ENGINE)
        catalog = self._engine_catalog()
        if self._auto_discover:
            if not catalog.engine_names():
                self._drive(catalog_module.discover_index(self._session, catalog=catalog))
            if engine in catalog.engine_names() and catalog.cached_schema(engine) is None:
                self._drive(catalog_module.discover_schema(self._session, engine, catalog=catalog))
        decision = catalog_module.resolve_engine(
            engine if isinstance(engine, str) else "", catalog=catalog)
        if decision["status"] != "READY":
            return self._from_catalog_decision(decision, capability_id)
        engine = decision["engine"]

        # Fail closed at T2: a schema that changed under an accepted pin is an
        # engine whose required parameters we no longer know. Every capability
        # this adapter serves is T2, so there is no tier where carrying on with a
        # caveat would be allowed.
        drift = catalog.drift(engine)
        if drift is not None:
            refusal = self._refusal(
                "CAPABILITY_SCHEMA_DRIFT",
                "引擎 schema 与已接受的 pin 不一致；在人工复核前不再派发",
                capability_id=capability_id)
            refusal.payload["accepted_sha256"] = drift.get("accepted_sha256")
            refusal.payload["observed_sha256"] = drift.get("observed_sha256")
            return refusal

        try:
            query = _checked_query(params.get("query"))
            country = _checked_country(params.get("country"))
            language = _checked_language(params.get("language"))
            limit = _checked_limit(params.get("limit"))
        except ValueError as exc:
            code = str(exc).split(":", 1)[0].strip()
            return self._refusal(code, f"{code}: 参数未通过校验", capability_id=capability_id)

        arguments = search_canonical.canonical_arguments(
            provider=PROVIDER, mode="web", query=query, country=country,
            language=language, limit=limit, page=1, engine=engine,
            account=account_ref, transport="EMBEDDED_OFFICIAL_MCP_HTTP")
        operation_key = search_canonical.operation_signature(arguments)

        listed = self._drive(self._session.list_tools())
        advertised = [str(getattr(tool, "name", "") or "")
                      for tool in getattr(listed, "tools", [])]
        if SEARCH_TOOL not in advertised:
            # Never call a tool the session does not advertise: the refusal is a
            # statement about the session, and nothing has been dispatched yet.
            return self._refusal("SERPAPI_TOOL_NOT_ADVERTISED",
                                 f"会话未声明 {SEARCH_TOOL}", capability_id=capability_id)

        upstream = {"params": {"engine": engine, "q": query,
                               "gl": country.lower(), "hl": language, "num": limit},
                    "mode": UPSTREAM_MODE}
        result = self._drive(self._session.call_tool(SEARCH_TOOL, upstream))
        if bool(getattr(result, "isError", False)) or bool(getattr(result, "is_error", False)):
            return self._failure("SERPAPI_UPSTREAM_FAILED", "上游声明工具失败",
                                 capability_id=capability_id)
        body = _as_mapping(result)
        if not body:
            return self._failure("SERPAPI_RESULT_INVALID", "响应为空或不可解析",
                                 capability_id=capability_id)
        if body.get("error"):
            return self._failure("SERPAPI_DECLARED_ERROR", "上游声明了 error",
                                 capability_id=capability_id)
        cleaned = _clean(body)
        encoded = json.dumps(cleaned, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 3_500_000:
            return self._failure("SERPAPI_RESULT_TOO_LARGE", f"{len(encoded)} bytes",
                                 capability_id=capability_id)

        hits = self._normalise(cleaned, limit=limit)
        payload = {
            "schema": "marketops.serpapi.discovery.v1",
            "provider": PROVIDER,
            "transport": TRANSPORT,
            "capability_id": capability_id,
            "run_id": run_id,
            "operation_id": operation_id,
            "engine": engine,
            # When the index that authorised this engine was observed. Not a
            # verdict that it is still current: staleness is reported by the
            # catalog, never silently assumed away.
            "engine_index_observed_at": catalog.summary().get("observed_at"),
            "query": arguments["query"],
            "country": country,
            "language": language,
            "limit": limit,
            "upstream_mode": UPSTREAM_MODE,
            "result_count": len(hits),
            "results": hits,
            "source_status": "discovered_not_verified",
            "business_evidence": False,
            "logical_operation_key": operation_key,
            "route_identity": search_canonical.search_route_identity(
                provider=PROVIDER, account_ref=account_ref, query=arguments["query"],
                capability="search", engine=engine, country=country,
                language=language, limit=limit),
            "response_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        artifacts: tuple[str, ...] = ()
        if self._artifact_writer is not None:
            artifacts = tuple(self._artifact_writer(payload) or ())
        return SerpApiResult(outcome=OUTCOME_OK, payload=payload, artifacts=artifacts,
                             transport=TRANSPORT, schema_verdict="UNVERIFIED",
                             actual_source="NONE", upstream_ref=None,
                             caveats=("DISCOVERY_CANDIDATES_NOT_VERIFIED_BUSINESS_EVIDENCE",
                                      "ENGINE_CATALOG_IS_LOCALLY_CACHED_NOT_LIVE_VERIFIED"))

    @staticmethod
    def _normalise(body: Mapping[str, Any], *, limit: int) -> list[dict[str, Any]]:
        rows = body.get("organic_results")
        if not isinstance(rows, list):
            rows = body.get("local_results")
        if not isinstance(rows, list):
            return []
        hits: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            link = str(row.get("link") or row.get("website") or "")
            hits.append({
                "status": "discovered",
                "provider": PROVIDER,
                "kind": "search_candidate",
                "title": str(row.get("title") or row.get("name") or "")[:500],
                "url": link[:2000],
                "snippet": str(row.get("snippet") or row.get("description") or "")[:1000],
                "place_id": str(row.get("place_id") or "")[:240],
                "address": str(row.get("address") or "")[:500],
                "rank": len(hits) + 1,
            })
            if len(hits) >= limit:
                break
        return hits

    # -- internals ----------------------------------------------------------
    def _engine_catalog(self) -> Any:
        return self._catalog if self._catalog is not None else catalog_module.EngineCatalog.default()

    def _drive(self, awaitable: Any) -> Any:
        """Run one coroutine. Injected runner, or ``asyncio.run``.

        Direct use from a running loop is refused rather than attempted: the
        alternative is an unawaited coroutine and a refusal that reports the
        wrong cause.
        """
        if self._runner is not None:
            return self._runner(awaitable)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        raise ValueError("SERPAPI_SYNC_CALL_INSIDE_RUNNING_LOOP: 不能在运行中的事件循环里同步调用")

    def _engine_name_verdict(self, engine: Any, catalog: Any,
                             capability_id: str) -> SerpApiResult | None:
        """Name shape and index membership — without demanding a cached schema.

        Returns a refusal, or ``None`` when the engine is one this adapter may
        name in a resource URI.
        """
        try:
            catalog_module.engine_uri(engine)
        except ValueError:
            return self._refusal("SERPAPI_ENGINE_INVALID",
                                 f"引擎名不合法：{engine!r}", capability_id=capability_id)
        names = catalog.engine_names()
        if not names:
            refusal = self._refusal(
                "SERPAPI_ENGINE_INDEX_NOT_READ",
                "引擎索引从未读过，因此没有任何引擎能被证明存在",
                capability_id=capability_id)
            refusal.payload["index_state"] = "INDEX_NOT_READ"
            return refusal
        if engine not in names:
            refusal = self._refusal(
                "SERPAPI_ENGINE_UNKNOWN",
                f"索引里没有这个引擎（索引里有 {len(names)} 个）",
                capability_id=capability_id)
            refusal.payload["index_state"] = "INDEX_READ"
            return refusal
        return None

    def _from_catalog_decision(self, decision: Mapping[str, Any],
                               capability_id: str) -> SerpApiResult:
        """Turn a catalog verdict into a refusal, keeping its distinguishing field."""
        reason = str(decision.get("reason") or "CAPABILITY_UNKNOWN")
        local = {
            "CAPABILITY_PARAM_NOT_ALLOWED": "SERPAPI_ENGINE_NAME_INVALID",
            "CAPABILITY_UNKNOWN": ("SERPAPI_ENGINE_INDEX_NOT_READ"
                                   if decision.get("index_state") == "INDEX_NOT_READ"
                                   else "SERPAPI_ENGINE_UNKNOWN"),
            "CAPABILITY_NOT_AVAILABLE": (
                "SERPAPI_ENGINE_CACHE_INTEGRITY"
                if decision.get("integrity") == "CACHE_INTEGRITY_FAILURE"
                else "SERPAPI_ENGINE_SCHEMA_NOT_CACHED"),
        }.get(reason, "SERPAPI_ENGINE_UNKNOWN")
        result = self._refusal(local, f"{local}: {decision.get('next_step') or reason}",
                               capability_id=capability_id)
        if "index_state" in decision:
            result.payload["index_state"] = decision["index_state"]
        return result

    def _refusal(self, code: str, detail: str, *, capability_id: str = "") -> SerpApiResult:
        """A pre-dispatch refusal: ``BLOCKED``, with both codes carried.

        ``caveats[0]`` is the §5.3 member the Gateway reads; ``payload["reason"]``
        is the precise local code, which is the only place the difference between
        "no session" and "engine not in the index" survives once both map into the
        closed set.
        """
        if code in CONTRACT_REASON_CODES:
            reason, contract_code = code, code
        else:
            reason = _reason(code)
            if reason in POST_DISPATCH_CODES:
                raise AssertionError(
                    f"{reason!r} 是派发之后的失败，必须走 _failure —— 记成 BLOCKED 会说"
                    "「我们没出去」，而那是假的。")
            contract_code = _contract_code(reason)
        return SerpApiResult(
            outcome=OUTCOME_BLOCKED,
            payload={"reason": reason, "contract_reason_code": contract_code,
                     "detail": f"{reason}: {detail}",
                     "capability_id": capability_id or None},
            transport=TRANSPORT, schema_verdict="UNVERIFIED",
            caveats=(contract_code,
                     "本地拒绝，未触达上游（§2：BLOCKED 是「本地拒绝，未触达上游」）。"),
        )

    def _failure(self, code: str, detail: str, *, capability_id: str = "",
                 unknown: bool = False) -> SerpApiResult:
        """A post-dispatch failure: §2's ``FAILED``, not ``BLOCKED``.

        ``unknown`` says the failure is a *charge* nobody can resolve — a timeout
        after the request left. The outcome is still ``FAILED`` (the call did not
        succeed) and the financial state is ``UNKNOWN``, which is a different
        field on purpose: "we did not get an answer" and "we know what this cost"
        are two facts, and collapsing them is how an unresolved charge gets
        settled at zero.
        """
        reason = _reason(code)
        if reason not in POST_DISPATCH_CODES:
            raise AssertionError(
                f"{reason!r} 不在 POST_DISPATCH_CODES 里 —— 没派发过的失败记成 FAILED 会"
                "反过来把「没出去」说成「出去了」。")
        return SerpApiResult(
            outcome=(OUTCOME_UNKNOWN if unknown else OUTCOME_FAILED),
            payload={"reason": reason, "detail": detail,
                     "capability_id": capability_id or None,
                     "charge_state": "UNKNOWN" if unknown else "UNSETTLED_NOT_UNKNOWN"},
            transport=TRANSPORT, schema_verdict="UNVERIFIED",
            caveats=(reason, "请求已经发出（上游明确失败），所以这是 FAILED 而不是 BLOCKED。"),
        )


__all__ = [
    "ADAPTER_LOCAL_REASONS", "ADAPTER_RESULT_FIELDS", "ALLOWED_PARAMS", "CAPABILITIES",
    "CONTRACT_ANALOGUE", "CONTRACT_REASON_CODES", "DECLARED_NOT_SERVED",
    "EFFECT_CLASS_BY_CAPABILITY", "OFFICIAL_MCP_ENDPOINT", "POST_DISPATCH_CODES",
    "OUTCOME_UNKNOWN", "PROVIDER", "ROUTE_PARAMS_FORBIDDEN", "SEARCH_TOOL", "SerpApiAdapter",
    "SerpApiResult", "TIER_BY_CAPABILITY", "TRANSPORT", "UPSTREAM_MODE",
    "capabilities", "describe_capabilities", "to_adapter_result_fields",
]

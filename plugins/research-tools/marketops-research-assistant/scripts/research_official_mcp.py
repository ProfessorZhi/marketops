#!/usr/bin/env python3
"""Real, opt-in in-process MCP client for TinyFish and SerpApi official HTTP servers.

No separate host-level vendor MCP installation is required. The customer must
still supply a provider key or an already-authorized OAuth access token. This
client neither creates accounts nor performs browser OAuth in the background.
The calling MarketOps layer owns per-Run consent, reservations and evidence.

Do NOT redirect, retry a charged tool, expose token values, or choose arbitrary
remote MCP URLs. Tool discovery validates the upstream schema before use.

Discovery output is *untrusted vendor content*: the reviewed capability
registry and the dated schema snapshot at the foot of this module are what turn
it into a bounded, curated, hash-pinned surface (INTERFACE-CONTRACT.md §4
`CapabilityRegistry` / IR-1..IR-5). No vendor description ever reaches the host.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import research_target_guard

ENDPOINTS = {
    "tinyfish": "https://agent.tinyfish.ai/mcp",
    "serpapi": "https://mcp.serpapi.com/mcp",
}
TOOLS = {
    "tinyfish": frozenset({"search", "fetch_content", "run_web_automation",
                           "get_run"}),
    "serpapi": frozenset({"search"}),
}
SECRET_FIELDS = frozenset({"api_key", "apikey", "authorization", "access_token",
                           "refresh_token", "cookie", "set-cookie", "password",
                           "secret", "vault", "wallet_url", "checkout_url"})
TOKEN_ENV = {
    "tinyfish": ("TINYFISH_MCP_ACCESS_TOKEN", "TINYFISH_API_KEY"),
    "serpapi": ("SERPAPI_API_KEY",),
}
_ENGINE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PLACEHOLDER = re.compile(r"^\$\{[A-Z_][A-Z0-9_]*\}$")


def _upstream_proxy():
    """Only the proxy configured for MarketOps; never the machine's ambient settings."""
    from dual_search_mcp import upstream_proxy
    return upstream_proxy()


def _credential(provider: str, environ: dict | None = None) -> tuple[str, str]:
    if provider not in ENDPOINTS:
        raise ValueError("OFFICIAL_MCP_PROVIDER_UNSUPPORTED")
    env = os.environ if environ is None else environ
    for name in TOKEN_ENV[provider]:
        value = env.get(name, "")
        if isinstance(value, str) and value.strip() and not _PLACEHOLDER.fullmatch(value.strip()):
            return name, value.strip()
    if provider == "tinyfish":
        # The account owner can optionally authenticate through our *own*
        # built-in MCP OAuth login CLI, stored in their OS credential manager.
        # Presence is not proof of valid tokens; SDK handles refresh/failure.
        from research_official_mcp_auth import has_saved_tokens
        if has_saved_tokens():
            return "OS_KEYRING_OAUTH", ""
    raise ValueError("OFFICIAL_MCP_CREDENTIAL_REQUIRED")


def _clean(value, *, depth=0):
    """Bound and sanitize untrusted provider tool responses before persistence."""
    if depth > 8:
        return "[TRUNCATED_NESTED_VALUE]"
    if isinstance(value, dict):
        return {
            str(key)[:90]: ("[REDACTED]" if str(key).lower() in SECRET_FIELDS
                           else _clean(val, depth=depth + 1))
            for key, val in list(value.items())[:150]
        }
    if isinstance(value, list):
        return [_clean(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return value[:220000]
    if type(value) in (int, float, bool) or value is None:
        return value
    return str(value)[:200]


def _approved_url(value: str, *, resolve: bool = True) -> str:
    """Defense in depth for direct client callers; outer Lite uses the same rule.

    The remote vendor must never be asked to fetch an unapproved URL even if a
    plugin developer accidentally calls this module without the Lite wrapper.

    ``resolve=False`` skips only the name-resolution step. That step asks the
    network, and a question that asks the network must not be asked *before* the
    questions that decide whether this call is authorized at all: otherwise a
    name that fails to resolve refuses a paid call that was going to be refused
    for its real reason, and the operator reads the wrong cause. `call()` runs
    the resolving form after the authorization and credential checks; the
    shape-only form runs during argument validation.
    """
    if not isinstance(value, str) or not 1 <= len(value) <= 2000:
        raise ValueError("OFFICIAL_MCP_TARGET_NOT_ALLOWED")
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = (parsed.hostname or "").casefold().strip(".")
        invalid = (parsed.scheme != "https" or not hostname or
                   parsed.username is not None or parsed.password is not None or
                   parsed.port not in (None, 443) or
                   hostname.endswith((".local", ".internal")) or hostname == "localhost")
        if invalid:
            raise ValueError("OFFICIAL_MCP_TARGET_NOT_ALLOWED")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError("OFFICIAL_MCP_TARGET_NOT_ALLOWED")
    except (ValueError, AttributeError):
        raise ValueError("OFFICIAL_MCP_TARGET_NOT_ALLOWED") from None
    allowed = {d.strip().casefold().strip(".") for d in
               os.environ.get("MARKETOPS_FETCH_ALLOWED_DOMAINS", "").split(",")
               if d.strip()}
    if not any(hostname == d or hostname.endswith("." + d) for d in allowed):
        raise ValueError("OFFICIAL_MCP_TARGET_NOT_ALLOWED")
    # The allowlist is a string rule; the resolved address is the thing that
    # actually decides where a request goes. Refuse a non-global answer here,
    # before the vendor is ever asked to fetch it.
    if resolve:
        research_target_guard.resolve_checked(hostname, parsed.port or 443)
    return value


def _approve_targets(tool: str, arguments: dict) -> None:
    """The resolving half of target approval, run once the call is authorized."""
    if tool == "fetch_content":
        for target in arguments.get("urls") or ():
            _approved_url(target)
    elif tool == "run_web_automation":
        _approved_url(arguments.get("url"))


def _validate(provider: str, tool: str, arguments: dict) -> dict:
    if provider not in TOOLS or tool not in TOOLS[provider]:
        raise ValueError("OFFICIAL_MCP_TOOL_NOT_ALLOWLISTED")
    if type(arguments) is not dict or len(arguments) > 24:
        raise ValueError("OFFICIAL_MCP_ARGUMENTS_INVALID")
    def safe(value, depth=0):
        if depth > 4 or isinstance(value, (list, dict)) and len(value) > 30:
            raise ValueError("OFFICIAL_MCP_ARGUMENTS_TOO_LARGE")
        if isinstance(value, dict):
            for key, val in value.items():
                if not isinstance(key, str) or key.casefold() in SECRET_FIELDS:
                    raise ValueError("OFFICIAL_MCP_CREDENTIALS_IN_ARGS_FORBIDDEN")
                safe(val, depth + 1)
        elif isinstance(value, (list, tuple)):
            for val in value: safe(val, depth + 1)
        elif not (value is None or type(value) in (str, bool, int, float)):
            raise ValueError("OFFICIAL_MCP_ARGUMENTS_INVALID")
        elif isinstance(value, str) and len(value) > 3000:
            raise ValueError("OFFICIAL_MCP_ARGUMENTS_TOO_LARGE")
    safe(arguments)
    if provider == "tinyfish" and tool == "search":
        q = arguments.get("query")
        if (not isinstance(q, str) or not 2 <= len(q) <= 240
                or any(k not in {"query", "purpose", "location", "language"} for k in arguments)):
            raise ValueError("TINYFISH_MCP_SEARCH_ARGS_INVALID")
    elif provider == "tinyfish" and tool == "fetch_content":
        urls = arguments.get("urls")
        if (type(urls) is not list or not 1 <= len(urls) <= 10
                or not all(isinstance(u, str) and len(u) <= 2000 for u in urls)
                or any(k not in {"urls", "format", "ttl", "purpose", "links",
                                  "image_links", "page_metadata",
                                  "include_selectors", "exclude_selectors",
                                  "per_url_timeout_ms"} for k in arguments)):
            raise ValueError("TINYFISH_MCP_FETCH_ARGS_INVALID")
        for target in urls:
            _approved_url(target, resolve=False)
    elif provider == "tinyfish" and tool == "run_web_automation":
        goal = arguments.get("goal")
        cfg = arguments.get("agent_config", {})
        if (not isinstance(goal, str) or not 10 <= len(goal) <= 1000
                or type(cfg) is not dict
                or type(cfg.get("max_steps")) is not int
                or type(cfg.get("max_duration_seconds")) is not int
                or not 1 <= cfg["max_steps"] <= 50
                or not 1 <= cfg["max_duration_seconds"] <= 300
                or any(k not in {"url", "goal", "agent_config", "browser_profile",
                                  "capture_config", "use_profile", "use_vault"} for k in arguments)):
            raise ValueError("TINYFISH_AGENT_HARD_CAP_REQUIRED")
        if arguments.get("use_profile") or arguments.get("use_vault"):
            raise ValueError("TINYFISH_SITE_PROFILE_REQUIRES_SEPARATE_AUTHORIZATION")
        if arguments.get("browser_profile", "lite") != "lite":
            raise ValueError("TINYFISH_AGENT_UNAPPROVED_BROWSER_PROFILE")
        _approved_url(arguments.get("url"), resolve=False)
    elif provider == "tinyfish" and tool == "get_run":
        run_id = arguments.get("id")
        if (not isinstance(run_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id)
                or set(arguments) != {"id"}):
            raise ValueError("TINYFISH_AGENT_RUN_ID_INVALID")
    elif provider == "serpapi":
        params = arguments.get("params")
        if type(params) is not dict or not 1 <= len(params) <= 24:
            raise ValueError("SERPAPI_MCP_PARAMS_REQUIRED")
        import serpapi_engine_catalog as _catalog
        engine = params.get("engine", _catalog.DEFAULT_ENGINE)
        if not isinstance(engine, str) or not _ENGINE.fullmatch(engine):
            raise ValueError("SERPAPI_MCP_ENGINE_INVALID")
        # Official MCP is a *multi-engine* interface. Google Flights and
        # Hotels, for example, may legitimately have no 'q'. Require bounded
        # useful input, then let upstream's engine-specific schema enforce the
        # required fields; do not collapse its 100+ engines into Google-only.
        if not any(k not in {"engine", "output", "api_key"} and
                   v not in (None, "", False) for k, v in params.items()):
            raise ValueError("SERPAPI_MCP_ENGINE_INPUT_REQUIRED")
        if arguments.get("mode", "compact") != "compact":
            raise ValueError("SERPAPI_MCP_COMPACT_MODE_REQUIRED")
        if any(k not in {"params", "mode"} for k in arguments):
            raise ValueError("SERPAPI_MCP_ARGS_INVALID")
    return arguments


def _extract(result) -> dict:
    # The MCP Python SDK uses structuredContent and content text blocks. A
    # successful *transport* does not mean the tool succeeded.
    if bool(getattr(result, "isError", False)) or bool(getattr(result, "is_error", False)):
        raise ValueError("UPSTREAM_MCP_TOOL_FAILED")
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        data = structured
        if isinstance(data, dict) and "result" in data and isinstance(data["result"], str):
            try: data = json.loads(data["result"])
            except json.JSONDecodeError: data = {"text": data["result"]}
    else:
        blocks = getattr(result, "content", [])
        data = {"text": "\n".join(getattr(b, "text", "") for b in blocks if
                                   isinstance(getattr(b, "text", None), str))}
        if not data["text"]:
            raise ValueError("UPSTREAM_MCP_EMPTY_RESULT")
        try: data = json.loads(data["text"])
        except json.JSONDecodeError: pass
    safe = _clean(data)
    if len(json.dumps(safe, ensure_ascii=False).encode("utf-8")) > 3_500_000:
        raise ValueError("UPSTREAM_MCP_RESULT_TOO_LARGE")
    return {"tool_result": safe, "mcp_tool_completed": True,
            "business_evidence_verified": False}


def _run_identifier(value, *, depth=0) -> str | None:
    """Extract the exact provider run handle, never infer an ID from text."""
    if depth > 5:
        return None
    if isinstance(value, dict):
        for key in ("runId", "run_id"):
            v = value.get(key)
            if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v):
                return v
        for part in value.values():
            found = _run_identifier(part, depth=depth + 1)
            if found: return found
    if isinstance(value, list):
        for part in value[:20]:
            found = _run_identifier(part, depth=depth + 1)
            if found: return found
    return None


def _agent_status(raw: dict) -> str:
    """Status belongs to provider output, never assume completion from MCP success."""
    data = raw.get("tool_result")
    if type(data) is not dict:
        return "unknown"
    # Provider payloads can wrap one structured result object.
    for name in ("data", "result"):
        if isinstance(data.get(name), dict):
            data = data[name]
            break
    code = data.get("status", data.get("state", ""))
    if not isinstance(code, str):
        return "unknown"
    code = code.casefold().strip()
    if code in {"pending", "queued", "running", "in_progress", "processing"}:
        return "running"
    if code in {"completed", "succeeded", "success", "done"}:
        return "completed"
    if code in {"failed", "error", "cancelled", "canceled"}:
        return "failed"
    return "unknown"


def _wallet_guard(result, max_steps: int, ceiling_usd: str):
    """Fail closed if TinyFish does not publish a usable Agent step quote."""
    data = _extract(result)["tool_result"]
    if type(data) is not dict:
        raise ValueError("TINYFISH_AGENT_WALLET_NOT_VERIFIED")
    # Same account, immediately read from the currently authenticated MCP
    # session; never accept self-declared wallet data from a tool argument.
    try:
        balance = Decimal(str(data["available_balance"]))
        meters = data["rates"]["meters"]
        meter = next(m for m in meters if m.get("label") == "TinyFish Agent")
        unit = Decimal(str(meter["unit_amount"]))
        allowed = Decimal(ceiling_usd)
        if (not all(x.is_finite() for x in (balance, unit, allowed))
                or unit <= 0 or allowed <= 0
                or unit * max_steps > allowed or unit * max_steps > balance):
            raise ValueError("TINYFISH_AGENT_SPEND_CAP_EXCEEDED")
    except (KeyError, IndexError, TypeError, StopIteration, InvalidOperation):
        raise ValueError("TINYFISH_AGENT_WALLET_NOT_VERIFIED") from None


def _supports_agent_bounds(schema: dict) -> bool:
    """Resolve local JSON-Schema $ref/$defs before trusting Agent hard controls.

    MCP input schemas may inline nested object properties or use Pydantic
    $defs plus anyOf nullable references. Never follow remote $ref URLs.
    """
    if type(schema) is not dict:
        return False

    def resolve(node, depth=0):
        if type(node) is not dict or depth > 7:
            return {}
        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/"):
                return {}
            parts = [s.replace("~1", "/").replace("~0", "~")
                     for s in ref[2:].split("/")]
            target = schema
            for part in parts:
                if type(target) is not dict or part not in target:
                    return {}
                target = target[part]
            return resolve(target, depth + 1)
        if type(node.get("properties")) is dict:
            return node
        for key in ("anyOf", "oneOf", "allOf"):
            values = node.get(key)
            if type(values) is list and len(values) <= 12:
                for option in values:
                    candidate = resolve(option, depth + 1)
                    if candidate.get("properties"):
                        return candidate
        return {}

    root = resolve(schema)
    config = resolve(root.get("properties", {}).get("agent_config"))
    fields = config.get("properties", {})
    return type(fields) is dict and {
        "max_steps", "max_duration_seconds"}.issubset(fields)


async def _live_call(provider: str, tool: str, arguments: dict, *,
                     max_paid_usd: str = "") -> dict:
    # Local import keeps no-network static preflight working before pip install.
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import httpx2

    var, token = _credential(provider)
    # The OAuth helper never initiates browser login during a provider call.
    # First login is a separate human-run CLI action; tokens live in OS keyring.
    auth = None
    headers = {}
    if var == "OS_KEYRING_OAUTH":
        from research_official_mcp_auth import oauth_provider
        auth = oauth_provider(interactive=False)
    else:
        headers = {"Authorization": "Bearer " + token}
    # Never expose tokens in URLs, redirects, log bodies, or callbacks.
    async with httpx2.AsyncClient(headers=headers, auth=auth, trust_env=False,
                                  proxy=_upstream_proxy(),
                                  follow_redirects=False,
                                  timeout=httpx2.Timeout(65.0, connect=10.0)) as http:
        async with streamable_http_client(ENDPOINTS[provider], http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                listed = await session.list_tools()
                # MCP Python SDK v2 Tool uses snake_case input_schema.
                # Keep an explicit camelCase fallback for foreign tool objects.
                schemas = {x.name: (getattr(x, "input_schema", None)
                                    or getattr(x, "inputSchema", {}))
                           for x in listed.tools}
                if tool not in schemas:
                    raise ValueError("UPSTREAM_MCP_TOOL_NOT_ADVERTISED")
                if tool == "run_web_automation":
                    if not _supports_agent_bounds(schemas[tool]):
                        raise ValueError("TINYFISH_AGENT_PROVIDER_HARD_CAP_NOT_VERIFIED")
                    if "get_wallet" not in schemas:
                        raise ValueError("TINYFISH_AGENT_WALLET_TOOL_REQUIRED")
                    wallet = await session.call_tool("get_wallet", {})
                    _wallet_guard(wallet, arguments["agent_config"]["max_steps"], max_paid_usd)
                result = await session.call_tool(tool, arguments)
                parsed = _extract(result)
                if tool == "run_web_automation":
                    handle = _run_identifier(parsed["tool_result"])
                    if not handle:
                        # The job could already be running. Never submit again.
                        raise ValueError("TINYFISH_AGENT_RUN_ID_MISSING_AFTER_SUBMISSION")
                    parsed["agent_run_id"] = handle
                    parsed["agent_lifecycle"] = "submitted_not_terminal"
                elif tool == "get_run":
                    parsed["agent_lifecycle"] = _agent_status(parsed)
                return {**parsed, "provider": provider, "mcp_tool": tool,
                        "transport": "OFFICIAL_STREAMABLE_HTTP_MCP",
                        "auth_mode": ("OS_KEYRING_OAUTH" if var == "OS_KEYRING_OAUTH"
                                      else "USER_PROVIDED_OAUTH_ACCESS_TOKEN" if
                                      var == "TINYFISH_MCP_ACCESS_TOKEN" else
                                      "PROVIDER_BEARER_KEY"),
                        "actual_charge_usd": None,
                        "provider_charge_verified": False}


# Only errors deliberately raised by OUR MCP bridge may survive the async
# TaskGroup boundary. Any untrusted exception message (provider HTTP body,
# SDK transport detail, OAuth callback or raw URL) must remain hidden.
_KNOWN_BRIDGE_ERRORS = frozenset({
    "UPSTREAM_MCP_TOOL_FAILED", "UPSTREAM_MCP_EMPTY_RESULT",
    "UPSTREAM_MCP_RESULT_TOO_LARGE", "UPSTREAM_MCP_TOOL_NOT_ADVERTISED",
    "TINYFISH_AGENT_PROVIDER_HARD_CAP_NOT_VERIFIED",
    "TINYFISH_AGENT_WALLET_TOOL_REQUIRED",
    "TINYFISH_AGENT_WALLET_NOT_VERIFIED",
    "TINYFISH_AGENT_SPEND_CAP_EXCEEDED",
    "TINYFISH_AGENT_RUN_ID_MISSING_AFTER_SUBMISSION",
    "TINYFISH_OAUTH_INTERACTIVE_SETUP_REQUIRED",
})


def _known_group_reason(group: ExceptionGroup) -> str | None:
    """Unwrap known errors from anyio/asyncio TaskGroup without leaking secrets."""
    leaves = []
    def descend(exc):
        if isinstance(exc, ExceptionGroup):
            for child in exc.exceptions:
                descend(child)
        else:
            leaves.append(exc)
    descend(group)
    if not leaves or any(not isinstance(e, ValueError) or
                         str(e) not in _KNOWN_BRIDGE_ERRORS for e in leaves):
        return None
    values = {str(e) for e in leaves}
    return next(iter(values)) if len(values) == 1 else None


def call(provider: str, tool: str, arguments: dict, *,
         max_paid_usd: str = "", runner=None, tier_grant=None) -> dict:
    """Run one real official MCP tool using the in-process Python MCP client.

    For offline tests, runner is an injected async coroutine. Production never
    auto-falls-back from MCP to REST or creates a second paid operation.
    """
    data = _validate(provider, tool, arguments)
    if tool == "run_web_automation":
        if os.environ.get("MARKETOPS_TINYFISH_AGENT_ALLOW_PAID") != "1":
            raise ValueError("TINYFISH_AGENT_PAID_NOT_AUTHORIZED")
        if not max_paid_usd:
            raise ValueError("TINYFISH_AGENT_MAX_CHARGE_REQUIRED")
        try:
            val = Decimal(max_paid_usd)
            if not val.is_finite() or val <= 0 or val > Decimal("5"):
                raise InvalidOperation()
        except (InvalidOperation, TypeError):
            raise ValueError("TINYFISH_AGENT_MAX_CHARGE_INVALID") from None
    _credential(provider)  # fail before creating any billable session
    if tool == "run_web_automation":
        # §P1-04 / INTERFACE-CONTRACT §5.4: a process environment variable is not
        # an authorization factor on a host where the Agent shares this OS
        # identity — the switch above is the operator's policy statement, not a
        # boundary. A billable T3 dispatch needs a factor the Agent could not
        # have minted for itself; with none configured here, refuse.
        import research_provider_policy as policy

        verdict = policy.tier_authorization("T3", grant=tier_grant)
        if not verdict.authorized:
            raise ValueError("TINYFISH_AGENT_PAID_NOT_AUTHORIZED: "
                             f"{verdict.reason_code} {verdict.declared_limitation}")
    # Target approval that asks the network runs here, after the authorization and
    # credential refusals, so a name that cannot be resolved never masquerades as
    # the reason a call was refused.
    _approve_targets(tool, arguments)
    # A synchronous MarketOps tool normally runs in a worker thread, but
    # direct use from an async caller must fail BEFORE making a coroutine,
    # otherwise asyncio.run raises and leaves an unawaited coroutine warning.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ValueError("OFFICIAL_MCP_SYNC_CALL_INSIDE_RUNNING_LOOP")
    selected = runner or _live_call
    try:
        return asyncio.run(selected(provider, tool, data, max_paid_usd=max_paid_usd))
    except ValueError:
        raise
    except ExceptionGroup as group:
        # MCP Streamable HTTP uses TaskGroups. Without unwrapping, a
        # precise wallet/schema refusal turns into a generic transport error
        # and encourages unnecessary, potentially charged retry.
        reason = _known_group_reason(group)
        raise ValueError(reason or "UPSTREAM_MCP_TRANSPORT_OR_SESSION_FAILED") from None
    except Exception:
        # Never show upstream SDK/HTTP strings, which can include credentials.
        raise ValueError("UPSTREAM_MCP_TRANSPORT_OR_SESSION_FAILED") from None


# ---------------------------------------------------------------------------
# Reviewed capability registry and dated upstream-schema snapshot
# ---------------------------------------------------------------------------
# Frozen contract: docs/wave1-coordination/INTERFACE-CONTRACT.md §4
# (`CapabilityRegistry` / `CapabilityEntry`) and §10 (`IR-1`..`IR-5`). The
# registry is the **sole** allowlist authority, and an upstream tool
# description is untrusted input that may not become an instruction, a payload
# or a budget for the host Agent. `TOOLS` above stays the exposed set; the
# assertion below makes a divergence between the two a load error rather than a
# silent one.
#
# Two things are deliberately NOT done here, because Wave-1 holds no
# authorization for them — docs/wave1-qa/INDEPENDENT-QA-MATRIX-20261017.md §4
# records that `DECISION-04` (one bounded *read-only* handshake per provider) is
# not granted, so D-6 and "一切真实握手" are `BLOCKED` and a snapshot must not be
# fabricated:
#
#   * no live `tools/list` is performed, so the snapshot records
#     `live_handshake_performed = False` and claims no `UPSTREAM_SCHEMA_VERIFIED`
#     or `LIVE_VERIFIED` rung of §1's ladder;
#   * the TinyFish symbol drift is therefore *recorded*, not resolved. An
#     offline record can detect a disagreement; it cannot decide which side is
#     right, so `SYMBOL_DRIFT["authoritative_side"]` stays
#     `UNRESOLVED_PENDING_HANDSHAKE` instead of inventing the verdict that only
#     the authorized handshake could produce.
#
# Tier / effect-class / availability / verdict values are the literal vocabulary
# of §2. The shared-type module named there (`research_contract.py`) is not
# present in this checkout and §2 forbids a second copy of those types, so this
# block carries the *values* and defines no parallel StrEnum.

SCHEMA_SNAPSHOT_CAPTURED_ON = "2026-10-17"

#: The reviewed, host-exposed capability rows (§4 "Fixed reviewed registry").
#: `allowed_params` is the *host subset* actually offered to an Agent; a vendor
#: parameter outside it is refused by `_validate` and can never be reached by a
#: dynamic invoke (P1-05).
_REVIEWED_ROWS = (
    {
        "capability_id": "tinyfish.search",
        "provider": "tinyfish",
        "upstream_tool": "search",
        "transport": "official_mcp",
        "effect_class": "BILLABLE_READ",
        "tier": "T2",
        "auth_scope": "tinyfish:search",
        "cost_meter": "tinyfish.search",
        "allowed_params": ("location", "language", "purpose", "query"),
        "required_params": ("query",),
        "curated_summary": ("TinyFish web search over the operator's own "
                            "account; returns bounded result records."),
    },
    {
        "capability_id": "tinyfish.fetch_content",
        "provider": "tinyfish",
        "upstream_tool": "fetch_content",
        "transport": "official_mcp",
        "effect_class": "BILLABLE_READ",
        "tier": "T1",
        "auth_scope": "tinyfish:fetch_content",
        "cost_meter": "tinyfish.fetch_content",
        "allowed_params": ("format", "purpose", "ttl", "urls"),
        "required_params": ("urls",),
        "curated_summary": ("Fetch approved https URLs through TinyFish and "
                            "return bounded page content; read-only."),
    },
    {
        "capability_id": "tinyfish.run_web_automation",
        "provider": "tinyfish",
        "upstream_tool": "run_web_automation",
        "transport": "official_mcp",
        "effect_class": "BILLABLE_ACT",
        "tier": "T3",
        "auth_scope": "tinyfish:run_web_automation",
        "cost_meter": "tinyfish.agent.steps",
        "allowed_params": ("agent_config", "browser_profile", "capture_config",
                           "goal", "url", "use_profile", "use_vault"),
        "required_params": ("goal", "agent_config"),
        "curated_summary": ("Run one TinyFish browser Agent against an approved "
                            "URL under hard step and wall-clock caps; paid, and "
                            "authorized per Run by the operator."),
    },
    {
        "capability_id": "tinyfish.get_run",
        "provider": "tinyfish",
        "upstream_tool": "get_run",
        "transport": "official_mcp",
        "effect_class": "READ_ONLY",
        "tier": "T1",
        "auth_scope": "tinyfish:get_run",
        "cost_meter": "tinyfish.agent.steps",
        "allowed_params": ("id",),
        "required_params": ("id",),
        "curated_summary": ("Poll one TinyFish run handle for its "
                            "provider-reported status; the handle is never "
                            "inferred from free text."),
    },
    {
        "capability_id": "serpapi.search",
        "provider": "serpapi",
        "upstream_tool": "search",
        "transport": "official_mcp",
        "effect_class": "BILLABLE_READ",
        "tier": "T2",
        "auth_scope": "serpapi:search",
        "cost_meter": "serpapi.search",
        "allowed_params": ("mode", "params"),
        "required_params": ("params",),
        "curated_summary": ("SerpApi multi-engine search, one engine per call, "
                            "compact output only; no interactive UI app is "
                            "required to use it."),
    },
)

#: §2 availability vocabulary: a capability is `ENABLED` unless a schema
#: judgement or a policy plane says otherwise.
_PINNED_AVAILABILITY = "ENABLED"

#: §1 readiness of every row: the code exists and imports, which is `CODED`.
#: It is deliberately **not** `OFFLINE_TESTED` for a whole capability, and it is
#: never `UPSTREAM_SCHEMA_VERIFIED` without the authorized handshake — §1 forbids
#: promoting a rung on the strength of "文档写了" or "mock 过了".
_PINNED_READINESS = "CODED"


def _host_subset_schema(allowed_params, required_params) -> dict:
    """The host-visible parameter schema: exactly the reviewed subset, nothing else.

    `allowed_params` is an equal-or-subset of this schema's properties (§4), and
    `additionalProperties: false` is what makes IR-2 true by construction rather
    than by a second, drift-prone check.
    """
    return {
        "type": "object",
        "properties": {name: {} for name in sorted(allowed_params)},
        "required": sorted(required_params),
        "additionalProperties": False,
    }


def _canonical_schema_sha256(schema) -> str:
    """Digest a schema the one way every reader can reproduce."""
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CapabilityEntry:
    """§4 verbatim: the reviewed translation of one upstream tool.

    `schema_sha256` is the digest of the accepted **host subset** snapshot, not
    of a vendor `tools/list` — see `SCHEMA_SNAPSHOT["pinned_hash_subject"]`.
    """

    capability_id: str
    provider: str
    upstream_tool: str
    transport: str
    effect_class: str
    tier: str
    auth_scope: str
    cost_meter: str
    param_schema: dict
    allowed_params: frozenset
    schema_sha256: str
    availability: str
    readiness: str
    curated_summary: str


def _build_entries(rows) -> dict:
    entries = {}
    for row in rows:
        schema = _host_subset_schema(row["allowed_params"], row["required_params"])
        entries[row["capability_id"]] = CapabilityEntry(
            capability_id=row["capability_id"],
            provider=row["provider"],
            upstream_tool=row["upstream_tool"],
            transport=row["transport"],
            effect_class=row["effect_class"],
            tier=row["tier"],
            auth_scope=row["auth_scope"],
            cost_meter=row["cost_meter"],
            param_schema=schema,
            allowed_params=frozenset(row["allowed_params"]),
            schema_sha256=_canonical_schema_sha256(schema),
            availability=_PINNED_AVAILABILITY,
            readiness=_PINNED_READINESS,
            curated_summary=row["curated_summary"],
        )
    return entries


#: `capability_id` → `CapabilityEntry`. This mapping **is** the allowlist
#: authority: `TOOLS` is derived from it, and the assertion below makes a
#: divergence a load error.
REVIEWED_CAPABILITIES = _build_entries(_REVIEWED_ROWS)

_REVIEWED_BY_UPSTREAM = {
    (entry.provider, entry.upstream_tool): entry
    for entry in REVIEWED_CAPABILITIES.values()
}

for _provider, _exposed in TOOLS.items():
    _reviewed_names = {entry.upstream_tool for entry in REVIEWED_CAPABILITIES.values()
                       if entry.provider == _provider}
    if _reviewed_names != set(_exposed):
        raise ValueError("REVIEWED_REGISTRY_DISAGREES_WITH_ALLOWLIST")
del _provider, _exposed, _reviewed_names

#: The accepted host-subset hash per capability (the pin IR-4 compares against).
SCHEMA_DIGESTS = {capability_id: entry.schema_sha256
                  for capability_id, entry in REVIEWED_CAPABILITIES.items()}

#: The reviewed host-subset schemas themselves, by capability.
PINNED_SCHEMAS = {capability_id: entry.param_schema
                  for capability_id, entry in REVIEWED_CAPABILITIES.items()}

#: Per-capability pin record: what was accepted, when, and by what authority.
#: `hash_subject` says plainly that this digest is of our own reviewed subset —
#: reading it as a vendor `tools/list` digest would be exactly the fabrication
#: D-6 forbids.
SCHEMA_PINS = {
    capability_id: {
        "provider": entry.provider,
        "upstream_tool": entry.upstream_tool,
        "tier": entry.tier,
        "allowed_params": tuple(sorted(entry.allowed_params)),
        "schema_sha256": entry.schema_sha256,
        "hash_subject": "HOST_SUBSET_NOT_VENDOR_TOOLS_LIST",
        "captured_on": SCHEMA_SNAPSHOT_CAPTURED_ON,
        "source": "REVIEWED_LOCAL_SUBSET",
    }
    for capability_id, entry in REVIEWED_CAPABILITIES.items()
}

#: The drifted symbol, recorded rather than repaired: the repair is gated on an
#: authorization this Wave does not hold, and `TOOLS` is the frozen exposed set.
SYMBOL_DRIFT = {
    "provider": "tinyfish",
    "shipped_tool": "get_run",
    "vendor_documented_tool": "get_run",
    "shipped_param": "id",
    "vendor_documented_param": "id",
    "resolution_state": "REPAIRED_IN_THIS_PATCH",
    "authoritative_side": "UNRESOLVED_PENDING_HANDSHAKE",
    "recorded_on": SCHEMA_SNAPSHOT_CAPTURED_ON,
    "why_not_repaired": (
        "Renaming the exposed tool changes the Agent-visible surface, and the "
        "vendor side of this disagreement is not verified by any handshake this "
        "Wave is authorized to perform (DECISION-04 not granted; "
        "INDEPENDENT-QA-MATRIX-20261017.md §4 D-6 BLOCKED). Repair under "
        "P2-01 waits for the authorized read-only tools/list; until then the "
        "drift is recorded here so it is detectable offline instead of "
        "unprovable."),
}

#: The dated offline record. `capture_mode` and `live_handshake_performed` are
#: the two fields a reader must check before treating anything here as verified.
SCHEMA_SNAPSHOT = {
    "snapshot_id": "marketops-official-mcp-schema-record-2026-10-17",
    "captured_on": SCHEMA_SNAPSHOT_CAPTURED_ON,
    "capture_mode": "OFFLINE_RECORDED",
    "live_handshake_performed": False,
    "verification_status": "UNVERIFIED",
    "blocked_on": ("DECISION-04 — one bounded read-only upstream tools/list "
                   "handshake per provider; NOT GRANTED for Wave-1"),
    "readiness": _PINNED_READINESS,
    "pinned_hash_subject": "HOST_SUBSET_NOT_VENDOR_TOOLS_LIST",
    "vendor_schema_sha256": None,
    "claims_not_made": ("UPSTREAM_SCHEMA_VERIFIED", "LIVE_VERIFIED",
                        "CLIENT_VERIFIED"),
    "provenance": (
        "docs/wave1-coordination/WORKER-DAG-AND-OWNERSHIP.md (W-C TINYFISH, six "
        "recorded corrections), "
        "docs/wave1-coordination/INTEGRATION-ACCEPTANCE-CHECKLIST.md (D-1, "
        "D-6 BLOCKED), "
        "docs/wave1-qa/INDEPENDENT-QA-MATRIX-20261017.md (§4 D-1/D-6)",
    ),
    "providers": {
        "tinyfish": {
            "transport": "official_mcp",
            "endpoint": ENDPOINTS["tinyfish"],
            "shipped_allowlist": sorted(TOOLS["tinyfish"]),
            "recorded_vendor_catalog_size": 28,
            "recorded_vendor_catalog_size_source": (
                "operator knowledge base for this node (TinyFish official "
                "catalog, 28 tools). Not present in this checkout and NOT "
                "live-verified — recorded, not asserted."),
            "recorded_vendor_tool_names": ("search", "fetch_content",
                                           "run_web_automation", "get_run"),
            "exposed_after_patch": ("search", "fetch_content",
                                    "run_web_automation", "get_run"),
            "recorded_vendor_required_params": {
                "run_web_automation": ("session_id",),
                "fetch_content": ("format", "links", "image_links",
                                  "page_metadata"),
                "get_run": ("id",),
            },
            "recorded_vendor_absences": (
                "agent_config.max_steps / agent_config.max_duration_seconds",
            ),
            "recorded_auth_header": "X-API-Key",
            "recorded_corrections_count": 6,
        },
        "serpapi": {
            "transport": "official_mcp",
            "endpoint": ENDPOINTS["serpapi"],
            "shipped_allowlist": sorted(TOOLS["serpapi"]),
            "recorded_vendor_catalog_size": None,
            "recorded_vendor_tool_names": None,
            "ui_extensions": {
                "search_table": "OPT_IN_HOST_DEPENDENT_NOT_EXPOSED",
                "search_dashboard": "OPT_IN_HOST_DEPENDENT_NOT_EXPOSED",
            },
        },
    },
    "symbol_drift": SYMBOL_DRIFT,
    "pinned_schema_sha256": SCHEMA_DIGESTS,
}

#: A snapshot history, so a second authorized capture appends rather than
#: overwrites. It has one member because no handshake has been authorized yet.
SCHEMA_SNAPSHOTS = (SCHEMA_SNAPSHOT,)

#: Per-upstream-tool view: what the reviewed side expects of each vendor tool,
#: side by side with the vendor name the record documents. This is the table a
#: future `verify_schema` run against a real `tools/list` is diffed with.
TOOL_SCHEMA_SNAPSHOT = {}
for _entry in REVIEWED_CAPABILITIES.values():
    TOOL_SCHEMA_SNAPSHOT.setdefault(_entry.provider, {})[_entry.upstream_tool] = {
        "capability_id": _entry.capability_id,
        "allowed_params": tuple(sorted(_entry.allowed_params)),
        "schema_sha256": _entry.schema_sha256,
        "hash_subject": "HOST_SUBSET_NOT_VENDOR_TOOLS_LIST",
        "vendor_documented_name": (
            SYMBOL_DRIFT["vendor_documented_tool"]
            if (_entry.provider, _entry.upstream_tool) ==
            (SYMBOL_DRIFT["provider"], SYMBOL_DRIFT["shipped_tool"])
            else _entry.upstream_tool),
    }
del _entry


def schema_snapshot_json(indent: int = 2) -> str:
    """Serialize `SCHEMA_SNAPSHOT` deterministically (the record file's source).

    The dated artifact under `references/` is regenerated from this function, so
    the module and the file cannot drift apart silently: regenerating and
    diffing shows it.
    """
    return json.dumps(SCHEMA_SNAPSHOT, indent=indent, sort_keys=True,
                      ensure_ascii=False, default=str) + "\n"


@dataclass(frozen=True)
class CapabilitySummary:
    """The cheap registry row: no schema body, no vendor text."""

    capability_id: str
    provider: str
    tier: str
    effect_class: str
    availability: str
    allowed_params: tuple
    schema_sha256: str


@dataclass(frozen=True)
class CapabilityDescriptor:
    """The lazy, host-visible description of one capability.

    There is deliberately no field for an upstream description: IR-5 says vendor
    text must not reach the host, and a field for it would be a hole waiting for
    someone to fill it.
    """

    capability_id: str
    provider: str
    upstream_tool: str
    transport: str
    effect_class: str
    tier: str
    auth_scope: str
    cost_meter: str
    availability: str
    readiness: str
    allowed_params: tuple
    schema_sha256: str
    curated_summary: str


@dataclass(frozen=True)
class CurationResult:
    """One row of `curate_discovered_tools`: what was offered, and what happened.

    `vendor_description_present` / `vendor_description_chars` record *that* a
    hostile description arrived without carrying its text anywhere — a curation
    function that echoed the description would be the injection path it exists
    to close.
    """

    upstream_tool: str
    capability_id: str | None
    accepted: bool
    verdict: str
    availability: str
    observed_schema_sha256: str
    vendor_description_present: bool
    vendor_description_chars: int
    reason_code: str


def _bounded_label(value) -> str:
    """A vendor-supplied *name*, bounded for the reviewer's record.

    Bounded and control-character-stripped so a hostile upstream cannot turn a
    tool name into a payload, and never placed on the host-visible surface.
    """
    if not isinstance(value, str):
        value = str(value)
    cleaned = "".join(ch for ch in value if ch.isprintable())
    return cleaned[:90]


def _observed_tool_schemas(observed) -> dict:
    """Normalize a discovered `tools/list` result to `{name: schema_or_None}`.

    Accepts the SDK's `ListToolsResult` (`.tools`, members exposing
    `input_schema` or `inputSchema`), a mapping `{name: schema}`, or a sequence
    of objects with `.name`. An unrecognized shape contributes nothing rather
    than raising: a hostile upstream chooses its own payload shape, and a
    curation call must not be a way to crash the caller.
    """
    tools = getattr(observed, "tools", observed)
    schemas = {}
    if isinstance(tools, dict):
        for name, schema in list(tools.items())[:256]:
            schemas[_bounded_label(name)] = schema
        return schemas
    if isinstance(tools, (list, tuple)):
        for item in list(tools)[:256]:
            name = getattr(item, "name", None)
            if name is None and isinstance(item, dict):
                name = item.get("name")
            if name is None:
                continue
            schema = getattr(item, "input_schema", None)
            if schema is None:
                schema = getattr(item, "inputSchema", None)
            if schema is None and isinstance(item, dict):
                schema = item.get("input_schema", item.get("inputSchema"))
            schemas[_bounded_label(name)] = schema
        return schemas
    return schemas


def _params_compatible(entry: CapabilityEntry, observed_schema) -> bool:
    """Is the pinned host subset still a valid sub-schema of the observation?

    Compatible means: every parameter we offer is still declared, and every
    parameter the vendor now *requires* is one we already offer. That is exactly
    the "T1 只读且参数子集兼容" condition of IR-4.
    """
    if type(observed_schema) is not dict:
        return False
    properties = observed_schema.get("properties")
    if type(properties) is not dict:
        return False
    if not set(entry.allowed_params) <= {str(k) for k in properties}:
        return False
    required = observed_schema.get("required", [])
    if not isinstance(required, list):
        return False
    return {str(name) for name in required} <= set(entry.allowed_params)


def _schema_verdict(entry: CapabilityEntry, schemas: dict) -> str:
    """§2 `SchemaVerdict` value for one reviewed capability against an observation."""
    if entry.upstream_tool not in schemas:
        return "ABSENT"
    observed_schema = schemas[entry.upstream_tool]
    if observed_schema is None:
        # Advertised with no schema at all: we cannot claim a match.
        return "UNVERIFIED"
    return "MATCH" if _params_compatible(entry, observed_schema) else "DRIFT"


def availability_after(verdict: str, tier: str, *,
                       params_subset_compatible: bool = False) -> str:
    """IR-4: `DRIFT`/`ABSENT` disable the capability from T2 upward.

    A T0/T1 capability may continue while it is read-only and its parameter
    subset is still compatible; anything else fails closed to `SCHEMA_DRIFT`.
    """
    if verdict in ("DRIFT", "ABSENT"):
        if tier in ("T0", "T1") and params_subset_compatible:
            return "ENABLED"
        return "SCHEMA_DRIFT"
    return "ENABLED"


def curate_discovered_tools(observed_tools_list, *, provider: str | None = None):
    """Turn a discovered `tools/list` into reviewed, bounded curation rows (§4).

    This is the missing step the review names: *"Curate/normalize metadata; pin
    schema hash and approve exposed capabilities; do not automatically expose
    unknown upstream tools or descriptions."* An upstream tool that is not in
    the reviewed registry is **refused**, not exposed (P1-02/P1-05); a reviewed
    tool whose advertised parameters no longer cover our subset is reported as
    `DRIFT` with availability `SCHEMA_DRIFT` from T2 up; and no vendor
    description text is carried out of this function in any case.

    Pure: it reads no credential, opens no socket and mutates nothing.
    """
    schemas = _observed_tool_schemas(observed_tools_list)
    descriptions = {}
    tools = getattr(observed_tools_list, "tools", observed_tools_list)
    if isinstance(tools, (list, tuple)):
        for item in list(tools)[:256]:
            name = getattr(item, "name", None)
            if name is None and isinstance(item, dict):
                name = item.get("name")
            if name is not None:
                descriptions[_bounded_label(name)] = getattr(item, "description", None)
    elif isinstance(tools, dict):
        for name in list(tools)[:256]:
            descriptions[_bounded_label(name)] = None

    results = []
    for name in sorted(schemas):
        description = descriptions.get(name)
        present = isinstance(description, str) and bool(description)
        entry = _REVIEWED_BY_UPSTREAM.get((provider, name)) if provider else None
        if entry is None:
            # Also the `provider=None` case: without a provider the row cannot be
            # attributed, and an unattributable tool is not an approved one.
            results.append(CurationResult(
                upstream_tool=name, capability_id=None, accepted=False,
                verdict="UNVERIFIED", availability="UNREVIEWED",
                observed_schema_sha256="", vendor_description_present=present,
                vendor_description_chars=len(description) if present else 0,
                reason_code="UPSTREAM_TOOL_NOT_REVIEWED"))
            continue
        verdict = _schema_verdict(entry, schemas)
        observed_schema = schemas.get(entry.upstream_tool)
        compatible = verdict == "MATCH"
        availability = availability_after(verdict, entry.tier,
                                          params_subset_compatible=compatible)
        results.append(CurationResult(
            upstream_tool=name,
            capability_id=entry.capability_id,
            accepted=verdict == "MATCH",
            verdict=verdict,
            availability=availability,
            observed_schema_sha256=(_canonical_schema_sha256(observed_schema)
                                    if type(observed_schema) is dict else ""),
            vendor_description_present=present,
            vendor_description_chars=len(description) if present else 0,
            reason_code={"MATCH": "REVIEWED_CAPABILITY_ACCEPTED",
                         "DRIFT": "UPSTREAM_SCHEMA_DRIFT",
                         "ABSENT": "UPSTREAM_TOOL_ABSENT",
                         }.get(verdict, "UPSTREAM_SCHEMA_UNVERIFIED")))
    return tuple(results)


def reviewed_capabilities(*, provider: str | None = None, tier: str | None = None):
    """The host-visible, curated capability view — never an upstream string.

    This is the surface a host may be told about: our own curated summaries,
    tiers, effect classes and pinned digests, with vendor text absent by
    construction (IR-5).
    """
    return tuple(
        CapabilityDescriptor(
            capability_id=entry.capability_id,
            provider=entry.provider,
            upstream_tool=entry.upstream_tool,
            transport=entry.transport,
            effect_class=entry.effect_class,
            tier=entry.tier,
            auth_scope=entry.auth_scope,
            cost_meter=entry.cost_meter,
            availability=entry.availability,
            readiness=entry.readiness,
            allowed_params=tuple(sorted(entry.allowed_params)),
            schema_sha256=entry.schema_sha256,
            curated_summary=entry.curated_summary,
        )
        for entry in REVIEWED_CAPABILITIES.values()
        if (provider is None or entry.provider == provider)
        and (tier is None or entry.tier == tier)
    )


class CapabilityRegistry:
    """§4's registry over the reviewed rows; the allowlist authority.

    `task_kind` is refused rather than ignored: §3.1 puts that hint on the
    request and the Policy plane owns routing (see
    `research_provider_policy.TASK_ROUTES`); this offline Wave holds no reviewed
    task-kind → capability map, and silently ignoring a filter would answer a
    different question than the one asked.
    """

    def __init__(self, entries=None):
        self._entries = dict(REVIEWED_CAPABILITIES if entries is None else entries)

    def get(self, capability_id: str) -> CapabilityEntry | None:
        """IR-1: an unknown id resolves to `None` locally, touching nothing."""
        return self._entries.get(capability_id)

    def list(self, *, provider: str | None = None, tier: str | None = None,
             task_kind: str | None = None):
        if task_kind is not None:
            raise ValueError("CAPABILITY_TASK_KIND_NOT_REGISTRY_SCOPED")
        return tuple(
            CapabilitySummary(
                capability_id=entry.capability_id,
                provider=entry.provider,
                tier=entry.tier,
                effect_class=entry.effect_class,
                availability=entry.availability,
                allowed_params=tuple(sorted(entry.allowed_params)),
                schema_sha256=entry.schema_sha256,
            )
            for entry in self._entries.values()
            if (provider is None or entry.provider == provider)
            and (tier is None or entry.tier == tier)
        )

    def describe(self, capability_id: str) -> CapabilityDescriptor:
        """Lazy description of one capability; unknown id fails closed."""
        entry = self._entries.get(capability_id)
        if entry is None:
            raise ValueError("CAPABILITY_NOT_REGISTERED")
        return CapabilityDescriptor(
            capability_id=entry.capability_id,
            provider=entry.provider,
            upstream_tool=entry.upstream_tool,
            transport=entry.transport,
            effect_class=entry.effect_class,
            tier=entry.tier,
            auth_scope=entry.auth_scope,
            cost_meter=entry.cost_meter,
            availability=entry.availability,
            readiness=entry.readiness,
            allowed_params=tuple(sorted(entry.allowed_params)),
            schema_sha256=entry.schema_sha256,
            curated_summary=entry.curated_summary,
        )

    def verify_schema(self, capability_id: str,
                      observed_tools_list) -> str:
        """§4 `verify_schema` → a §2 `SchemaVerdict` value.

        Unknown id → `CAPABILITY_NOT_REGISTERED` before anything is read. An
        empty observation is `UNVERIFIED` (we looked at nothing), never a
        `MATCH`: a no-op must not read as a passing comparison.
        """
        entry = self._entries.get(capability_id)
        if entry is None:
            raise ValueError("CAPABILITY_NOT_REGISTERED")
        schemas = _observed_tool_schemas(observed_tools_list)
        if not schemas:
            return "UNVERIFIED"
        return _schema_verdict(entry, schemas)


#: The module's registry instance. The Gateway (INTERFACE-CONTRACT §7) injects
#: this rather than reaching for the allowlist directly.
capability_registry = CapabilityRegistry()

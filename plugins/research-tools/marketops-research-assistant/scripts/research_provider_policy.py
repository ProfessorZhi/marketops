"""Pure scene-neutral provider routing hints (no network and no execution).

The list is a *proposal* for an operator, never a tool invocation nor
permission/budget grant. Full official MCPs are separate host connections,
not automatically available just because this code documents them.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

# The T0..T4 ladder (owner brief §4) and the per-tier allowances (§5.2) live in
# one place; this plane re-exports them so a capability can be gated by tier "by
# code rather than instructions alone", and so the adapter surface can be given
# them without a second copy existing anywhere.
import research_tier_authorization as tier_plane
from research_tier_authorization import (  # noqa: F401 - re-exported surface, see below
    TIERS, TIER_BY_OPERATION, TierGrant, TierSpec, TierAuthorization,
    ceiling_for, ceiling_scope, tier_of, tier_authorization)

# Current wallet data is supplied explicitly by the host after it calls the
# official read-only wallet tool. This module cannot authenticate the host.
TINYFISH_METERS = {
    "TinyFish Agent": "agent", "TinyFish Browser": "browser",
    "TinyFish Fetch": "fetch", "TinyFish Search": "search",
}


def tinyfish_wallet_observation(wallet: dict | None) -> dict:
    """Bounded sanitized snapshot; never store keys, checkout URLs or profiles.

    Zero-listed Search/Fetch rates remain only an account-reported quote;
    login, connection, quota, provider status and authorization are separate.
    """
    if wallet is None:
        return {"status": "NOT_OBSERVED", "trusted_as_auth": False, "rates": {}}
    if type(wallet) is not dict or len(wallet) > 20:
        raise ValueError("TINYFISH_WALLET_REPORT_INVALID")
    try:
        amount = Decimal(str(wallet["available_balance"]))
        if not amount.is_finite() or abs(amount) > Decimal("100000000"):
            raise InvalidOperation()
        if wallet.get("currency") != "USD":
            raise ValueError("TINYFISH_WALLET_CURRENCY_UNSUPPORTED")
        as_of = datetime.fromisoformat(str(wallet["as_of"]).replace("Z", "+00:00"))
        if as_of.tzinfo is None:
            raise ValueError("TINYFISH_WALLET_TIME_INVALID")
        age = (datetime.now(timezone.utc) - as_of.astimezone(timezone.utc)).total_seconds()
        fresh = -120 <= age <= 900
        meters = wallet.get("rates", {}).get("meters", [])
        if type(meters) is not list or len(meters) > 20:
            raise ValueError("TINYFISH_WALLET_RATES_INVALID")
        rates = {}
        for item in meters:
            if type(item) is not dict:
                raise ValueError("TINYFISH_WALLET_RATES_INVALID")
            key = TINYFISH_METERS.get(item.get("label"))
            if key is None:
                continue
            value = Decimal(str(item["unit_amount"]))
            if not value.is_finite() or value < 0 or value > 100000:
                raise InvalidOperation()
            rates[key] = {
                "zero_listed_price": value == 0,
                "currency": "USD",
                "unit": str(item.get("per", ""))[:32],
            }
    except (ValueError, TypeError, KeyError, InvalidOperation, AttributeError):
        raise ValueError("TINYFISH_WALLET_REPORT_INVALID") from None
    return {"status": ("HOST_REPORTED_FRESH_NOT_AUTHENTICATED"
                       if fresh else "HOST_REPORTED_STALE"),
            "trusted_as_auth": False,
            "available_balance": str(amount) if fresh else None,
            "paid_wallet_exhausted": bool(fresh and amount <= 0),
            "rates": rates if fresh else {},
            "fresh": fresh}


# A Lite MCP call has TWO hops: the Agent invokes MarketOps via stdio MCP,
# then MarketOps calls a provider API/SDK or its local Python library.
# MCP is not an authentication or billing model. The provider account decides
# the charge even when the same vendor is called through its official MCP.
LITE_OPERATION_CHANNELS = {
    "serper": ("serper", "REST_HTTPS", "API_KEY_ENV", "SERPER_API_KEY", "SEARCH_QUERY_OR_ACCOUNT_CREDIT"),
    "serpapi": ("serpapi", "REST_HTTPS", "API_KEY_ENV", "SERPAPI_API_KEY", "SEARCH_QUERY_OR_ACCOUNT_CREDIT"),
    "tinyfish_search": ("tinyfish", "REST_HTTPS", "API_KEY_ENV", "TINYFISH_API_KEY", "SEARCH_QUERY_ACCOUNT_RATE"),
    "tinyfish_fetch": ("tinyfish", "REST_HTTPS", "API_KEY_ENV", "TINYFISH_API_KEY", "FETCH_URL_ACCOUNT_RATE"),
    "crawl4ai_local": ("crawl4ai", "LOCAL_PYTHON_SDK", "LOCAL_POLICY_NO_PROVIDER_KEY", None, "LOCAL_COMPUTE_AND_BROWSER"),
    "crawl4ai_cloud": ("crawl4ai", "REST_HTTPS_CLOUD", "API_KEY_ENV", "CRAWL4AI_API_KEY", "CLOUD_PROVIDER_CREDITS"),
    "jev_choice": ("typesafe_jev", "PYTHON_SDK_REMOTE_API", "API_KEY_ENV", "TYPESAFE_API_KEY", "MODEL_PROVIDER_USAGE_UNKNOWN"),
    "tinyfish_mcp_search": ("tinyfish", "EMBEDDED_OFFICIAL_MCP_HTTP", "OAUTH_BEARER_OR_API_KEY",
                            "TINYFISH_MCP_ACCESS_TOKEN_OR_TINYFISH_API_KEY", "SEARCH_QUERY_ACCOUNT_RATE"),
    "tinyfish_mcp_fetch": ("tinyfish", "EMBEDDED_OFFICIAL_MCP_HTTP", "OAUTH_BEARER_OR_API_KEY",
                           "TINYFISH_MCP_ACCESS_TOKEN_OR_TINYFISH_API_KEY", "FETCH_URL_ACCOUNT_RATE"),
    "tinyfish_mcp_agent": ("tinyfish", "EMBEDDED_OFFICIAL_MCP_HTTP", "OAUTH_BEARER_OR_API_KEY",
                           "TINYFISH_MCP_ACCESS_TOKEN_OR_TINYFISH_API_KEY", "PAID_AGENT_STEPS_WITH_LIMIT"),
    "tinyfish_mcp_wait": ("tinyfish", "EMBEDDED_OFFICIAL_MCP_HTTP", "OAUTH_BEARER_OR_API_KEY",
                          "TINYFISH_MCP_ACCESS_TOKEN_OR_TINYFISH_API_KEY", "STATUS_POLL_NO_NEW_AGENT"),
    "serpapi_mcp_search": ("serpapi", "EMBEDDED_OFFICIAL_MCP_HTTP", "BEARER_API_KEY",
                           "SERPAPI_API_KEY", "SEARCH_QUERY_OR_ACCOUNT_CREDIT"),
}
OFFICIAL_MCP_AUTH_HINTS = {
    # Only vendors with reviewed official MCP sources. Serper's official REST
    # and TypeSafe's official SDK remain distinct (not invented vendor MCPs).
    "tinyfish": "OAUTH_OR_PROVIDER_KEY_CLIENT_DEPENDENT",
    "serpapi": "BEARER_API_KEY_OR_OFFICIAL_MCP_CONFIG",
    "crawl4ai": "SELFHOSTED_TOKEN_OR_CLOUD_KEY",
}


_CREDENTIAL_WIRE = {
    "serper": "X_API_KEY_HTTP_HEADER",
    "serpapi": "API_KEY_HTTPS_QUERY_PARAMETER",
    "tinyfish_search": "X_API_KEY_HTTP_HEADER",
    "tinyfish_fetch": "X_API_KEY_HTTP_HEADER",
    "crawl4ai_local": "NO_PROVIDER_CREDENTIAL",
    "crawl4ai_cloud": "BEARER_HTTP_HEADER",
    "jev_choice": "SDK_API_KEY_ARGUMENT",
    "tinyfish_mcp_search": "MCP_AUTHORIZATION_BEARER_HEADER",
    "tinyfish_mcp_fetch": "MCP_AUTHORIZATION_BEARER_HEADER",
    "tinyfish_mcp_agent": "MCP_AUTHORIZATION_BEARER_HEADER",
    "tinyfish_mcp_wait": "MCP_AUTHORIZATION_BEARER_HEADER",
    "serpapi_mcp_search": "MCP_AUTHORIZATION_BEARER_HEADER",
}


def embedded_official_mcp_capabilities(environ: dict | None = None) -> dict:
    """Code-present integration status, not a real remote account probe."""
    from importlib.util import find_spec
    import os
    env = os.environ if environ is None else environ
    try: sdk = find_spec("mcp") is not None
    except (ImportError, ValueError): sdk = False
    return {
        "tinyfish": {
            "mode": "IN_PROCESS_OFFICIAL_STREAMABLE_HTTP_CLIENT",
            "url": "https://agent.tinyfish.ai/mcp",
            "sdk_present": sdk,
            "credential_configured": bool(
                (env.get("TINYFISH_MCP_ACCESS_TOKEN") or "").strip()
                or (env.get("TINYFISH_API_KEY") or "").strip()),
            "oauth_browser_login_performed": "NOT_VERIFIED",
            "auth_verified": False, "quota_verified": False,
            "free_methods": ["search", "fetch_content"],
            "paid_method": "run_web_automation_EXPLICIT_APPROVAL_AND_WALLET_REQUIRED",
            "cost_reconciled": False,
        },
        "serpapi": {
            "mode": "IN_PROCESS_OFFICIAL_STREAMABLE_HTTP_CLIENT",
            "url": "https://mcp.serpapi.com/mcp",
            "sdk_present": sdk,
            "credential_configured": bool((env.get("SERPAPI_API_KEY") or "").strip()),
            "auth_verified": False, "quota_verified": False,
            "method": "search_MULTI_ENGINE",
            "cost_reconciled": False,
        },
    }


def invocation_receipt(operation: str, *, primitive: str = "choice",
                       reservation: str = "NOT_RECONCILED") -> dict:
    """Small, immutable, safe display for the actual MarketOps Lite route.

    'success' is NEVER proof of a charge. A reserved attempt is a software
    counter, not a vendor invoice or monetary amount. Not an OAuth check.
    """
    info = LITE_OPERATION_CHANNELS.get(operation)
    if info is None:
        return {"provider": "unknown", "operation": "UNKNOWN_LOCAL_OPERATION",
                "tier": tier_of(operation),
                "client_to_marketops": "MARKETOPS_MCP_STDIO_SURFACE",
                "marketops_to_provider": "UNKNOWN", "authentication": {
                    "method": "UNKNOWN"}, "billing": {
                    "unit": "UNKNOWN", "actual_amount": None,
                    "currency": None, "charge_verified": False}}
    provider, transport, method, variable, unit = info
    cap = (operation if operation != "jev_choice"
           else "jev_" + primitive if primitive in {"choice", "score", "noul"} else "jev_unknown")
    return {
        "provider": provider,
        "operation": cap,
        # Which rung of the T0..T4 ladder this operation sits on: a metered
        # operation is recorded with its tier, so "which allowance bounds this"
        # is answerable from the receipt rather than from prose (§3, brief §4).
        "tier": tier_of(operation),
        "effect_class": TIERS[tier_of(operation)].effect_class,
        "cost_meter": TIERS[tier_of(operation)].cost_meter,
        "client_to_marketops": "MARKETOPS_MCP_STDIO_SURFACE",
        "marketops_to_provider": transport,
        "authentication": {
            "method": method,
            "credential_variable": variable,
            "credential_delivery": _CREDENTIAL_WIRE[operation],
            "provider_auth_verified": False,
            "site_login_required": False if method == "LOCAL_POLICY_NO_PROVIDER_KEY"
                                   else "TASK_DEPENDENT_NOT_IN_LITE",
        },
        "billing": {
            "unit": unit,
            "payer": "PROVIDER_ACCOUNT" if provider != "crawl4ai" or
                     operation == "crawl4ai_cloud" else "CUSTOMER_LOCAL_RESOURCES",
            "reservation": reservation,
            "actual_amount": None,
            "currency": None,
            "charge_verified": False,
            "note": "A local reservation is NOT a monetary charge; vendor invoice unknown.",
        },
    }


def official_mcp_route(provider: str) -> dict:
    """Documented external host capability, NOT a connected or audited call."""
    if provider not in OFFICIAL_MCP_AUTH_HINTS:
        raise ValueError("UNKNOWN_OFFICIAL_MCP_PROVIDER")
    return {"provider": provider,
            "client_to_provider": "OFFICIAL_PROVIDER_MCP_HOST_CONNECTION",
            "authentication_method_hint": OFFICIAL_MCP_AUTH_HINTS[provider],
            "connected": "NOT_VERIFIED",
            "billing": {"meter": "VENDOR_ACCOUNT_PRODUCT_DEPENDENT",
                        "actual_amount": None, "charge_verified": False},
            "tracked_in_marketops_lite_attempt_ledger": False}


TASK_ROUTES = {
    "source_discovery": (
        "tinyfish.host_search", "serper.web_search", "serpapi.google_search",
        "crawl4ai.cloud_search"),
    "known_public_url": (
        "tinyfish.host_fetch", "crawl4ai.local", "crawl4ai.cloud",
        "tinyfish.host_browser", "tinyfish.host_agent"),
    "batch_public_pages": (
        "crawl4ai.local", "tinyfish.host_fetch", "crawl4ai.selfhosted",
        "crawl4ai.cloud", "tinyfish.host_agent"),
    "dynamic_navigation": (
        "crawl4ai.local", "crawl4ai.selfhosted", "tinyfish.host_browser",
        "tinyfish.host_agent"),
    "authorized_login_read": (
        "tinyfish.host_browser", "tinyfish.host_agent",
        "crawl4ai.authorized_selfhosted_session"),
    "semantic_choice": ("deterministic_rules", "jev.choice"),
    "semantic_yes_no": ("deterministic_rules", "jev.noul"),
    "semantic_score": ("deterministic_rules", "jev.score"),
}
# Not a static price table; actual user wallet/subscription rates are
# discovered independently at run-time and may differ by user and date.
PROVIDER_COST = {
    "deterministic_rules": "NO_PROVIDER_FEE",
    "crawl4ai.local": "LOCAL_COMPUTE",
    "tinyfish.host_search": "CURRENT_ACCOUNT_RATE_UNVERIFIED",
    "tinyfish.host_fetch": "CURRENT_ACCOUNT_RATE_UNVERIFIED",
    "crawl4ai.selfhosted": "LOCAL_HOSTING",
    "crawl4ai.authorized_selfhosted_session": "LOCAL_HOSTING_AUTH_REQUIRED",
    "tinyfish.host_browser": "METERED_SESSION",
    "tinyfish.host_agent": "METERED_AGENT_STEPS",
}
DEFAULT_CODE_COST = "METERED_OR_ACCOUNT_DEPENDENT"
HOST_SLOTS = {"tinyfish.host_search", "tinyfish.host_fetch",
              "tinyfish.host_browser", "tinyfish.host_agent",
              "crawl4ai.selfhosted", "crawl4ai.authorized_selfhosted_session",
              "crawl4ai.cloud_search"}


def propose_route(task_kind: str, matrix: dict, external_host: dict,
                  *, paid_approved: bool = False, login_approved: bool = False,
                  tinyfish_wallet: dict | None = None) -> dict:
    if task_kind not in TASK_ROUTES:
        raise ValueError("UNKNOWN_RESEARCH_TASK_KIND")
    if type(paid_approved) is not bool or type(login_approved) is not bool:
        raise ValueError("INVALID_RESEARCH_PERMISSION_FLAGS")
    choices = []
    for key in TASK_ROUTES[task_kind]:
        cost = PROVIDER_COST.get(key, DEFAULT_CODE_COST)
        if key == "deterministic_rules":
            availability = "LOCAL_DETERMINISTIC_RULES_ONLY"
        elif key in HOST_SLOTS:
            # The parent MCP cannot see sibling host MCP connections. Even a
            # caller-reported tool list does not verify auth, wallet or rights.
            if key.startswith("tinyfish."):
                name = {"tinyfish.host_search": "tinyfish_search",
                        "tinyfish.host_fetch": "tinyfish_fetch",
                        "tinyfish.host_browser": "tinyfish_browser",
                        "tinyfish.host_agent": "tinyfish_agent"}[key]
                availability = external_host.get(name, "UNKNOWN")
            else:
                availability = "HOST_CONNECTION_AND_TOOL_SCHEMA_REQUIRED"
        else:
            availability = matrix.get(key, {}).get("status", "NOT_IMPLEMENTED_IN_LITE")
        high = cost in {"METERED_SESSION", "METERED_AGENT_STEPS",
                        "METERED_OR_ACCOUNT_DEPENDENT"}
        if tinyfish_wallet and key in {
                "tinyfish.host_search", "tinyfish.host_fetch"}:
            rate_type = "search" if key.endswith("_search") else "fetch"
            published = tinyfish_wallet.get("rates", {}).get(rate_type, {})
            if published.get("zero_listed_price"):
                cost = "HOST_REPORTED_ZERO_LISTED_PRICE_NOT_AUTH_VERIFIED"
        authenticated = task_kind == "authorized_login_read" or             key == "crawl4ai.authorized_selfhosted_session"
        if (high and key.startswith("tinyfish.")
                and tinyfish_wallet and tinyfish_wallet.get("paid_wallet_exhausted")):
            gate = "INSUFFICIENT_WALLET_BALANCE"
        elif high and not paid_approved:
            gate = "PAID_APPROVAL_REQUIRED"
        elif authenticated and not login_approved:
            gate = "SITE_LOGIN_AUTHORIZATION_REQUIRED"
        elif key in HOST_SLOTS:
            gate = "VERIFY_HOST_CONNECTION_ACCOUNT_RATE_SCOPE_AND_LEDGER"
        elif availability != "ELIGIBLE_BUT_AUTH_AND_CREDITS_UNVERIFIED":
            gate = "BLOCKED_OR_UNAVAILABLE"
        else:
            gate = "VERIFY_PROJECT_SCOPE_AND_SOURCE_BEFORE_CALL"
        choices.append({"capability": key, "availability": availability,
                        "cost_class": cost, "gate": gate})
    return {"task_kind": task_kind, "mode": "ADVICE_ONLY_NO_AUTO_SWITCH",
            "required": "approved_domain_and_budget_per_operation",
            "candidate_limit": len(choices), "candidates": choices,
            "warning": "A host MCP may bypass Lite's reservation ledger; no authorization is inferred."}


def _publish_tier_surface() -> None:
    """Give the adapter module the tier vocabulary it has to be able to name.

    ``research_provider_extensions`` is the module that actually dispatches the
    metered calls, and it is single-writer owned, so the vocabulary is
    *published* onto it instead of being copied into it (owner brief §4 / §7:
    "by code rather than instructions alone"). Publication is additive.

    The adapter imports this module, so in the ordinary order the adapter is
    already half-initialised in ``sys.modules`` when this runs. If this module
    was imported first, the adapter is brought up once so that publication is
    not a function of import order; a failure there is swallowed on purpose —
    the policy plane must not depend on the adapter plane loading successfully.
    """
    if tier_plane.publish_tier_surface():
        return
    try:  # pragma: no cover - only exercised when this module is imported first
        import importlib
        importlib.import_module("research_provider_extensions")
    except Exception:
        return
    tier_plane.publish_tier_surface()


_publish_tier_surface()


# --- logical intent and cross-provider escalation --------------------------
# Two keys, kept apart (P1-03 Key A / Key B). `op_signature` is the provider
# execution key: project + provider + capability + canonical params, and it
# deliberately excludes the transport, so the same query over REST and over the
# embedded official MCP is one spend opportunity. This one is the *logical
# research intent*: the objective, not the call. It is what lets the same
# objective re-run on a second provider be recognised at all — without it,
# cross-provider duplication is invisible and the second provider's spend looks
# like new work.
def intent_fingerprint(objective: str) -> str:
    """The logical-intent key for a research objective, in the ledger's own formula.

    The ledger owns this key (``research_usage_ledger.intent_fingerprint``), so this
    delegates to it rather than recomputing a digest here: two formulas for one key
    is two answers to "is this the same objective", and the one that drifts is the
    one nobody reads.
    """
    import research_usage_ledger as ledger

    return ledger.intent_fingerprint(ledger.OperationRequest(
        project="", run_id="", operation_id="", provider="", capability="",
        account_ref="", transport="", intent=str(objective), args={}))


#: A provider switch after a weak or blocked result is an **escalation**, not a
#: retry. It must therefore carry a new reason and draw on a new budget; the
#: symmetric failure — treating it as an automatic retry — is the one that turns
#: one weak result into two billed vendors. Nothing here executes the switch; it
#: decides whether one may be *requested*.
ESCALATION_REASONS = frozenset({
    "SOURCE_QUALITY_INSUFFICIENT", "CAPABILITY_ABSENT_FROM_FIRST_PROVIDER",
    "OPERATOR_EXPLICIT_REQUEST", "FIRST_PROVIDER_BLOCKED_OR_UNAVAILABLE",
})


def escalation_request(*, from_provider: str, to_provider: str,
                       reason: str) -> dict:
    """Whether a provider switch may be requested, and what it still needs.

    Returns a decision, never an execution. A switch is *never* automatic: the
    caller must name a reason from the closed set and the request draws on its
    own budget, so an escalation can be told apart from a retry in the ledger
    rather than in prose.
    """
    if from_provider == to_provider:
        raise ValueError("ESCALATION_REQUIRES_A_DIFFERENT_PROVIDER")
    if reason not in ESCALATION_REASONS:
        raise ValueError("ESCALATION_REASON_NOT_IN_CLOSED_SET")
    return {"kind": "ESCALATION_NOT_RETRY",
            "from_provider": from_provider, "to_provider": to_provider,
            "reason": reason, "automatic": False,
            "requires": "new_reason_and_new_budget",
            "note": "A retry of the same provider after an ambiguous timeout is "
                    "forbidden separately; this is a switch, and it is never "
                    "taken because a previous result was weak."}

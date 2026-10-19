#!/usr/bin/env python3
"""TinyFish official MCP adapter: pinned catalog, typed arguments, tiered gates.

WHY THIS MODULE EXISTS
----------------------
``research_official_mcp.py`` carried a TinyFish tool table that does not match
the vendor's published catalog. It allowed a run-status verb the official
server does not publish, required an ``agent_config`` object the official
``run_web_automation`` does not accept, omitted the required ``session_id``,
and admitted only a hand-written subset of the real ``search`` / ``fetch_content``
filters. Against a real account those paths fail closed with an opaque
transport error instead of a precise refusal, which is exactly the shape that
tempts an agent into a pointless retry of a possibly charged call.

This module is the isolated, reviewed replacement for that TinyFish surface.
It is deliberately *self-contained*: it does not import
``research_provider_extensions.py`` or ``research_mcp.py`` and it changes no
existing behaviour, so the Integration lane can wire it without a simultaneous
edit to a shared file.

WHAT IT IS NOT
--------------
Pinning the upstream documentation is **not** an account probe. Nothing here
proves that a customer's key, OAuth session, wallet or per-tool rate is valid.
Every capability therefore reports ``live: LIVE-UNVERIFIED`` until a separately
authorized real call is made and recorded.

INVARIANTS ENFORCED IN CODE
---------------------------
1. Only reviewed tools are callable. A tool the official server newly
   advertises is denied until it is reviewed into the pinned catalog.
2. A run-status read uses the published ``get_run(id)`` / ``batch_status``
   contract. No unpublished verb is accepted as an alias.
3. Every ``run_web_automation`` submission mints its own random UUID
   ``session_id``. A caller-supplied one is refused, so a replay cannot reuse
   another submission's browser session.
4. The wallet is read, never written. No top-up, no auto-reload change.
5. A dispatched-but-unresolved paid call is reported ``UNKNOWN`` (§2
   ChargeState): it is never retried, never re-routed, and never reported free.
6. Site profiles, vault credentials, browser sessions and recurring monitors
   are catalogued and refused. They require a separate authorization this
   adapter does not have.
"""
from __future__ import annotations

import asyncio
import hashlib
from contextlib import asynccontextmanager
import importlib
import inspect
import json
import os
import re
import uuid
from decimal import Decimal, InvalidOperation

PROVIDER = "tinyfish"
ENDPOINT = "https://agent.tinyfish.ai/mcp"

# The pin's provenance. A static upstream document is evidence about the
# vendor's *published* interface and nothing at all about this account's
# entitlements, so it is labelled as such rather than as verification.
CATALOG_SOURCE = ("https://github.com/tinyfish-io/tinyfish-mcp-server/"
                  "blob/main/docs/tools.md")
CATALOG_OBSERVED_AT = "2026-10-17"
PIN_EVIDENCE = "STATIC_UPSTREAM_DOC_PIN_NOT_ACCOUNT_VERIFIED"

T0, T1, T2, T3, T4 = "T0", "T1", "T2", "T3", "T4"

CONTRACT_REFERENCE = (
    "docs/wave1-coordination/INTERFACE-CONTRACT.md (v1.0 FROZEN, branch "
    "docs/wave1-coordination-20261017) — §2 shared types, §3.3/3.4 runtime "
    "objects, §4 CapabilityRegistry, §5.3 reason codes, §6 ProviderAdapter + "
    "IA-1..IA-6, §10 invariant table")

# The five-provider transport table is fixed by §6 and must not be changed
# per-adapter. TinyFish's primary path is the embedded official MCP; the REST
# adapter is an explicit downgrade only and is never selected automatically.
TRANSPORT = "official_mcp"

# ---------------------------------------------------------------------------
# Contract vocabulary.
#
# These are the *literal values* of the enums frozen in §2. They are literals
# rather than locally defined enums on purpose: §2 says every worker imports the
# shared types from `research_contract.py` and "禁止各自复制一份". That module is
# owned by the Contract/Registry lane (W-B) and does not exist at this base
# commit, so this adapter carries the values and never a competing definition.
# The conformance test asserts these spellings against the contract text.
# ---------------------------------------------------------------------------
EFFECT_READ_ONLY = "READ_ONLY"
EFFECT_BILLABLE_READ = "BILLABLE_READ"
EFFECT_BILLABLE_ACT = "BILLABLE_ACT"
EFFECT_STATE_CHANGING = "STATE_CHANGING"

CHARGE_RESERVED = "RESERVED"
CHARGE_SETTLED = "SETTLED"
CHARGE_NO_CHARGE_PROVEN = "NO_CHARGE_PROVEN"
CHARGE_UNKNOWN = "UNKNOWN"
CHARGE_FREE_TIER = "FREE_TIER"

ACTUAL_PROVIDER_REPORTED = "PROVIDER_REPORTED"
ACTUAL_DERIVED = "DERIVED"
ACTUAL_NONE = "NONE"

OUTCOME_OK = "OK"
OUTCOME_BLOCKED = "BLOCKED"
OUTCOME_FAILED = "FAILED"
OUTCOME_UNKNOWN = "UNKNOWN"

AVAILABILITY_ENABLED = "ENABLED"
AVAILABILITY_DISABLED = "DISABLED"
AVAILABILITY_UNSUPPORTED = "UNSUPPORTED"
AVAILABILITY_AUTH_REQUIRED = "AUTH_REQUIRED"
AVAILABILITY_SCHEMA_DRIFT = "SCHEMA_DRIFT"

SCHEMA_MATCH = "MATCH"
SCHEMA_DRIFT = "DRIFT"
SCHEMA_ABSENT = "ABSENT"
SCHEMA_UNVERIFIED = "UNVERIFIED"

# §5.3 is a closed set. Free text is forbidden, so only these codes are used.
REASON_CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
REASON_CAPABILITY_PARAM_NOT_ALLOWED = "CAPABILITY_PARAM_NOT_ALLOWED"
REASON_CAPABILITY_SCHEMA_DRIFT = "CAPABILITY_SCHEMA_DRIFT"
REASON_CAPABILITY_NOT_AVAILABLE = "CAPABILITY_NOT_AVAILABLE"
REASON_POLICY_TIER_NOT_GRANTED = "POLICY_TIER_NOT_GRANTED"
REASON_WALLET_PRECONDITION_FAILED = "WALLET_PRECONDITION_FAILED"
REASON_BUDGET_OPERATION_CEILING = "BUDGET_OPERATION_CEILING"
REASON_EGRESS_REFUSED = "EGRESS_REFUSED"

# Every refusal code this adapter is allowed to emit. Used to decide whether a
# refusal surfaced by an inner layer can be preserved or must be folded into
# CAPABILITY_PARAM_NOT_ALLOWED; a code outside this set would be untranslatable
# by the gateway. It is not a transcription of §5.3 -- only the members this
# adapter can produce.
_OWN_REFUSAL_CODES = frozenset({
    REASON_CAPABILITY_UNKNOWN, REASON_CAPABILITY_PARAM_NOT_ALLOWED,
    REASON_CAPABILITY_SCHEMA_DRIFT, REASON_CAPABILITY_NOT_AVAILABLE,
    REASON_POLICY_TIER_NOT_GRANTED, REASON_WALLET_PRECONDITION_FAILED,
    REASON_BUDGET_OPERATION_CEILING, REASON_EGRESS_REFUSED,
})

_EFFECT_CLASS = {
    "search": EFFECT_READ_ONLY, "fetch_content": EFFECT_READ_ONLY,
    "get_run": EFFECT_READ_ONLY, "list_runs": EFFECT_READ_ONLY,
    "batch_status": EFFECT_READ_ONLY, "get_wallet": EFFECT_READ_ONLY,
    "get_search_usage": EFFECT_READ_ONLY, "list_fetch_usage": EFFECT_READ_ONLY,
    "list_browser_sessions": EFFECT_READ_ONLY, "list_profiles": EFFECT_READ_ONLY,
    "list_monitors": EFFECT_READ_ONLY, "get_monitor": EFFECT_READ_ONLY,
    "guide_next_step": EFFECT_READ_ONLY,
    "run_web_automation": EFFECT_BILLABLE_ACT,
    "run_web_automation_async": EFFECT_BILLABLE_ACT,
    "create_browser_session": EFFECT_BILLABLE_ACT,
    "start_profile_setup_session": EFFECT_BILLABLE_ACT,
    "save_profile_setup_session": EFFECT_BILLABLE_ACT,
    "create_monitor": EFFECT_BILLABLE_ACT, "run_monitor": EFFECT_BILLABLE_ACT,
    "resume_monitor": EFFECT_BILLABLE_ACT,
    "cancel_run": EFFECT_STATE_CHANGING, "batch_cancel": EFFECT_STATE_CHANGING,
    "close_browser_session": EFFECT_STATE_CHANGING,
    "create_profile": EFFECT_STATE_CHANGING,
    "cancel_profile_setup_session": EFFECT_STATE_CHANGING,
    "pause_monitor": EFFECT_STATE_CHANGING, "cancel_monitor": EFFECT_STATE_CHANGING,
}

_COST_METER = {
    "search": "tinyfish.search_query", "fetch_content": "tinyfish.fetch_url",
    "get_run": "tinyfish.status_read", "list_runs": "tinyfish.status_read",
    "batch_status": "tinyfish.status_read", "get_wallet": "tinyfish.wallet_read",
    "get_search_usage": "tinyfish.usage_read",
    "list_fetch_usage": "tinyfish.usage_read",
    "run_web_automation": "tinyfish.agent_steps",
    "run_web_automation_async": "tinyfish.agent_steps",
    "cancel_run": "tinyfish.status_write", "batch_cancel": "tinyfish.status_write",
    "create_browser_session": "tinyfish.browser_seconds",
    "list_browser_sessions": "tinyfish.browser_seconds",
    "close_browser_session": "tinyfish.browser_seconds",
    "start_profile_setup_session": "tinyfish.browser_seconds",
    "create_profile": "tinyfish.profile_storage",
    "list_profiles": "tinyfish.profile_storage",
    "save_profile_setup_session": "tinyfish.profile_storage",
    "cancel_profile_setup_session": "tinyfish.profile_storage",
    "create_monitor": "tinyfish.monitor_run", "get_monitor": "tinyfish.monitor_run",
    "list_monitors": "tinyfish.monitor_run", "run_monitor": "tinyfish.monitor_run",
    "pause_monitor": "tinyfish.monitor_run", "resume_monitor": "tinyfish.monitor_run",
    "cancel_monitor": "tinyfish.monitor_run",
    "guide_next_step": "tinyfish.no_meter",
}

# T2+ capabilities share a family scope, so one strong grant cannot silently
# cover a weaker family. T1 capabilities scope per tool.
_FAMILY_SCOPE = {
    "run_web_automation": "agent", "run_web_automation_async": "agent",
    "cancel_run": "agent", "batch_cancel": "agent",
    "create_browser_session": "browser_session",
    "list_browser_sessions": "browser_session",
    "close_browser_session": "browser_session",
    "create_profile": "profile", "list_profiles": "profile",
    "start_profile_setup_session": "profile",
    "save_profile_setup_session": "profile",
    "cancel_profile_setup_session": "profile",
    "create_monitor": "monitor", "get_monitor": "monitor",
    "list_monitors": "monitor", "run_monitor": "monitor",
    "pause_monitor": "monitor", "resume_monitor": "monitor",
    "cancel_monitor": "monitor",
}


def _auth_scope(tool: str) -> str:
    return "tinyfish:" + _FAMILY_SCOPE.get(tool, tool)


def _availability(enabled: bool, tool: str) -> str:
    if enabled:
        return AVAILABILITY_ENABLED
    if tool == "guide_next_step":
        return AVAILABILITY_UNSUPPORTED
    return AVAILABILITY_DISABLED

# Credential variables are read, never written, and never echoed.
TOKEN_ENV = ("TINYFISH_MCP_ACCESS_TOKEN", "TINYFISH_API_KEY")
ACCOUNT_ALIAS_ENV = "MARKETOPS_TINYFISH_ACCOUNT_ALIAS"
PAID_GATE_ENV = "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID"
NETWORK_GATE_ENV = "MARKETOPS_RESEARCH_ALLOW_NETWORK"

# The paid Agent ceiling is a per-operation assertion, not a provider cap.
MAX_CHARGE_CEILING_USD = Decimal("5")

SECRET_FIELDS = frozenset({
    "api_key", "apikey", "authorization", "access_token", "refresh_token",
    "cookie", "set-cookie", "password", "secret", "token", "vault",
    "wallet_url", "checkout_url", "top_up_url", "viewer_url",
    "credential_item_ids",
})

# A provider tool description is untrusted vendor content. It is never
# interpreted as an instruction, so it is never allowed to raise a tier.
DESCRIPTIONS_ARE_UNTRUSTED = True


def _billing(may_charge, meter, note=""):
    return {"may_charge": may_charge, "meter": meter, "currency": "USD",
            "note": note}


# --------------------------------------------------------------------------
# Pinned catalog. One row per documented upstream tool.
#
# effect        what the call does to the world
# enabled       may the router ever route to it in this release
# required      argument names that must be present (exact, no extras)
# optional      argument names that may be present
# --------------------------------------------------------------------------
_SPEC = (
    # --- Search & fetch: free-tier, read-only, target-restricted ------------
    ("search", T1, "READ", True,
     "PROJECT_NETWORK_AND_DOMAIN_POLICY",
     _billing(False, "SEARCH_QUERY_ACCOUNT_RATE"),
     "", ("query",),
     ("purpose", "domain_type", "location", "language", "recency_minutes",
      "after_date", "before_date", "pub_year_min", "pub_year_max",
      "include_domains", "exclude_domains", "include_thumbnail", "page")),
    ("fetch_content", T1, "READ", True,
     "PROJECT_NETWORK_AND_DOMAIN_POLICY",
     _billing(False, "FETCH_URL_ACCOUNT_RATE"),
     "", ("urls",),
     ("format", "links", "image_links", "iframe_links", "page_metadata", "purpose",
      "include_selectors", "exclude_selectors", "ttl", "per_url_timeout_ms",
      "if_none_match", "if_modified_since", "include_etag_and_last_modified")),

    # --- Web automation: metered against the account wallet ----------------
    ("run_web_automation", T3, "PAID_AGENT", True,
     "EXPLICIT_HUMAN_AUTHORIZATION_PER_OPERATION",
     _billing(True, "AGENT_STEPS_FROM_WALLET",
              "Upstream publishes no per-call step ceiling; our ceiling is a "
              "client-side assertion and is labelled as such."),
     "", ("url", "goal"),
     ("browser_profile", "capture_config", "output_schema")),
    ("run_web_automation_async", T4, "PAID_AGENT_ASYNC", False,
     "EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(True, "AGENT_STEPS_FROM_WALLET"),
     "Background submission keeps spending after the caller's turn ends and "
     "the vendor warns against using it to retry; it needs a durable "
     "cancellation owner this plugin does not yet have.", (), ()),
    ("get_run", T0, "READ_STATUS", True,
     "PROJECT_RUN_RECEIPT_REQUIRED",
     _billing(False, "STATUS_READ_NO_NEW_AGENT"), "", ("id",), ()),
    ("list_runs", T0, "READ_STATUS", True,
     "PROJECT_RUN_RECEIPT_REQUIRED",
     _billing(False, "STATUS_READ_NO_NEW_AGENT"), "",
     ("limit", "sort_direction"),
     ("status", "goal", "created_after", "created_before", "cursor")),
    ("batch_status", T0, "READ_STATUS", True,
     "PROJECT_RUN_RECEIPT_REQUIRED",
     _billing(False, "STATUS_READ_NO_NEW_AGENT"), "", ("run_ids",), ()),
    # B's registry reviews these at T4 with availability DISABLED: reviewed,
    # deliberately not enabled this Wave. Neither advertised nor routable.
    ("cancel_run", T4, "CANCEL", False,
     "REVIEWED_DISABLED_T4_NOT_ENABLED_THIS_WAVE",
     _billing(False, "STATUS_WRITE_STOPS_SPEND"),
     "State-changing on the vendor side; the registry records it as a gap "
     "rather than omitting it.", ("id",), ()),
    ("batch_cancel", T4, "CANCEL", False,
     "REVIEWED_DISABLED_T4_NOT_ENABLED_THIS_WAVE",
     _billing(False, "STATUS_WRITE_STOPS_SPEND"),
     "Same as cancel_run.", ("run_ids",), ()),

    # --- Browser sessions --------------------------------------------------
    ("create_browser_session", T4, "BROWSER_SESSION", False,
     "EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(True, "METERED_SESSION"),
     "A metered cloud browser is billed by wall-clock time; it needs a "
     "cancellation owner and a per-session ceiling before it can be enabled.",
     (), ("url", "timeout_seconds")),
    ("list_browser_sessions", T1, "BROWSER_SESSION_READ", True,
     "PROJECT_NETWORK_AND_DOMAIN_POLICY",
     _billing(False, "ACCOUNT_READ_NO_CHARGE"),
     # Enabling this is a deliberate coverage decision, not family drift: a
     # *read* of the account's own session inventory charges nothing, reads no
     # credentials and touches no private site, so §2's four-axis rule puts it
     # at T1. Creating and closing sessions stay disabled below.
     "", (), ("limit", "page", "status", "session_id", "start_after", "end_before")),
    ("close_browser_session", T4, "BROWSER_SESSION_CONTROL", False,
     "EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(False, "STATUS_WRITE_STOPS_SPEND"),
     "Controls a session this adapter cannot currently create or own.", (),
     ("session_id",)),

    # --- Browser Context Profiles (saved login state) ----------------------
    ("create_profile", T4, "AUTH_PROFILE", False,
     "NAMED_SITE_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(False, "ACCOUNT_PROFILE_STORAGE"),
     "Creates durable authenticated-login state. Activating it would cross "
     "the candidate/formal boundary the owner brief keeps closed.", (),
     ("name", "proxy_country_code", "set_as_default")),
    ("list_profiles", T4, "AUTH_PROFILE_READ", False,
     "NAMED_SITE_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(False, "ACCOUNT_PROFILE_STORAGE"),
     "Profile names are account-scoped authentication state; the whole "
     "family is described and disabled together.", (), ()),
    ("start_profile_setup_session", T4, "AUTH_PROFILE_LOGIN", False,
     "NAMED_SITE_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(False, "METERED_SESSION_AND_LOGIN"),
     "Produces a manual sign-in URL for a real account. No agent may drive "
     "that login, so this must stay a human-run step outside the router.", (),
     ("profile_id", "url", "timeout_seconds")),
    ("save_profile_setup_session", T4, "AUTH_PROFILE_LOGIN", False,
     "NAMED_SITE_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(False, "ACCOUNT_PROFILE_STORAGE"),
     "Persists cookies and storage from a human sign-in; enabling it would "
     "make this plugin a credential store.", (), ("profile_id", "session_id")),
    ("cancel_profile_setup_session", T4, "AUTH_PROFILE_LOGIN", False,
     "NAMED_SITE_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(False, "ACCOUNT_PROFILE_STORAGE"),
     "Same family as the setup session it discards.", (),
     ("profile_id", "session_id")),

    # --- Monitors: recurring and billed per completed run ------------------
    ("create_monitor", T4, "RECURRING_SCHEDULE", False,
     "EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(True, "RECURRING_PER_RUN",
              "Vendor publishes $0.005 per completed run including the "
              "baseline; failed runs are free."),
     "A recurring wallet charge needs a standing approval this adapter does "
     "not hold, and it must be cancellable by someone who is still awake.",
     (), ()),
    ("get_monitor", T1, "RECURRING_SCHEDULE_READ", True,
     "PROJECT_NETWORK_AND_DOMAIN_POLICY",
     _billing(False, "ACCOUNT_READ_NO_CHARGE"),
     # Reading one monitor's schedule is free and reads no credentials; the
     # billable verbs of the family (create / run / resume) stay disabled.
     "", ("monitor_id",), ()),
    ("list_monitors", T1, "RECURRING_SCHEDULE_READ", True,
     "PROJECT_NETWORK_AND_DOMAIN_POLICY",
     _billing(False, "ACCOUNT_READ_NO_CHARGE"),
     "", (), ()),
    ("run_monitor", T4, "RECURRING_SCHEDULE_CONTROL", False,
     "EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(True, "RECURRING_PER_RUN"),
     "Charges a wallet run on demand; same standing-approval gap.", (),
     ("monitor_id",)),
    ("pause_monitor", T4, "RECURRING_SCHEDULE_CONTROL", False,
     "EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(False, "RECURRING_PER_RUN"),
     "Recurring-monitor family: described, never enabled.", (), ("monitor_id",)),
    ("resume_monitor", T4, "RECURRING_SCHEDULE_CONTROL", False,
     "EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(True, "RECURRING_PER_RUN"),
     "Resuming restarts a recurring charge without a new operator decision.",
     (), ("monitor_id",)),
    ("cancel_monitor", T4, "RECURRING_SCHEDULE_CONTROL", False,
     "EXPLICIT_HUMAN_AUTHORIZATION_REQUIRED_NOT_ENABLED",
     _billing(False, "RECURRING_PER_RUN"),
     "Recurring-monitor family: described, never enabled.", (), ("monitor_id",)),

    # --- Account & usage ---------------------------------------------------
    ("get_wallet", T0, "READ_ACCOUNT", True,
     "PROJECT_NETWORK_AND_DOMAIN_POLICY",
     _billing(False, "ACCOUNT_READ_NO_CHARGE",
              "Read-only. Balance, auto-reload state and per-product rates are "
              "reported as account claims, not as verified entitlements."),
     "", (), ()),
    ("get_search_usage", T0, "READ_USAGE", True,
     "PROJECT_NETWORK_AND_DOMAIN_POLICY",
     _billing(False, "ACCOUNT_READ_NO_CHARGE"),
     "",
     # `limit` is documented with a default, so demanding it turned a legal
     # upstream call into our refusal. Nothing is required here.
     (), ("limit", "page", "status", "start_after", "end_before")),
    ("list_fetch_usage", T0, "READ_USAGE", True,
     "PROJECT_NETWORK_AND_DOMAIN_POLICY",
     _billing(False, "ACCOUNT_READ_NO_CHARGE"), "",
     (), ("limit", "page", "status", "start_after", "end_before")),
    ("guide_next_step", T2, "ONBOARDING_NUDGE", False,
     "NOT_ENABLED_UNTRUSTED_PROVIDER_CONTENT",
     _billing(False, "ACCOUNT_READ_NO_CHARGE"),
     "Returns provider-authored onboarding advice. Provider text is untrusted "
     "input and must never steer spending, so it is catalogued and refused.",
     (), ()),
)

# Tools this release may actually dispatch. Everything else is a descriptor.
READ_TOOLS = tuple(n for n, _t, _e, on, *_ in _SPEC
                   if on and n not in {"run_web_automation", "cancel_run",
                                       "batch_cancel"})
T3_TOOLS = ("run_web_automation",)
# Cancellations name an existing run, so their receipt reports that handle
# rather than inventing one.
CANCEL_TOOLS = ("cancel_run", "batch_cancel")
DESCRIBED_TOOLS = tuple(n for n, _t, _e, on, *_ in _SPEC if not on)

_TOOL_INDEX = {row[0]: row for row in _SPEC}
_TOOL_NAMES = frozenset(_TOOL_INDEX)
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_ADVERTISED_TOOLS = 200


def _descriptor(row) -> dict:
    (name, tier, effect, enabled, authorization, billing,
     deferred_reason, required, optional) = row
    return {
        "tool": name,
        # --- contract §4 CapabilityEntry field parity -----------------------
        "capability_id": "tinyfish." + name,
        "provider": PROVIDER,
        "upstream_tool": name,
        "transport": TRANSPORT,
        "effect_class": _EFFECT_CLASS[name],
        "auth_scope": _auth_scope(name),
        "cost_meter": _COST_METER[name],
        "availability": _availability(enabled, name),
        "readiness": "OFFLINE_TESTED",
        "review_status": _review_status("tinyfish." + name),
        # --- adapter-local fields -------------------------------------------
        "tier": tier,
        "effect": effect,
        "enabled": bool(enabled),
        "callable": bool(enabled),
        "authorization": authorization,
        "requires_explicit_human_authorization": tier in (T3, T4),
        "billing": dict(billing),
        "deferred_reason": deferred_reason,
        "required_arguments": list(required),
        "optional_arguments": list(optional),
        "published_defaults": dict(PUBLISHED_DEFAULTS.get(name, {})),
    }


def _build_tools() -> dict:
    # Fresh objects every call: a caller mutating the returned catalog must not
    # be able to move the pin or silently disable a gate for anyone else.
    return {row[0]: _descriptor(row) for row in _SPEC}


def catalog_sha256() -> str:
    """Pin over the reviewed descriptors themselves, not over the doc text."""
    canonical = json.dumps(_build_tools(), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False,
                           default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def capability_catalog() -> dict:
    tools = _build_tools()
    return {
        "provider": PROVIDER,
        "endpoint": ENDPOINT,
        "catalog_source": CATALOG_SOURCE,
        "catalog_observed_at": CATALOG_OBSERVED_AT,
        "pin_evidence": PIN_EVIDENCE,
        "catalog_sha256": catalog_sha256(),
        "tool_count": len(tools),
        "tools": tools,
    }


def ungated_tools() -> tuple:
    """Tools reachable without a per-operation human authorization."""
    return tuple(READ_TOOLS)


def _row_enablement(name: str, entry: dict) -> str:
    if not entry["enabled"]:
        return "DESCRIBED_NOT_ENABLED"
    return "GATED_T3" if name in T3_TOOLS else "ENABLED_T1"


def capability_matrix() -> list:
    """Per-capability status. Coded, offline-tested and live are separate facts.

    ``live`` is pinned to LIVE-UNVERIFIED on purpose: no real TinyFish account,
    key, OAuth session or wallet was exercised to produce this module, and a
    green offline suite must never be rendered as a working integration.
    """
    rows = []
    for name, entry in _build_tools().items():
        rows.append({
            "tool": name,
            "tier": entry["tier"],
            "effect": entry["effect"],
            "enabled": entry["enabled"],
            "enablement": _row_enablement(name, entry),
            "coded": True,
            "offline_tested": "OFFLINE_TESTED",
            "live": "LIVE-UNVERIFIED",
            "billing_may_charge": entry["billing"]["may_charge"],
        })
    return rows


#: The small hot set a host may expose eagerly. Everything else is described on
#: demand: the whole catalog in context buys nothing a lookup cannot.
CURATED_TOOLS = ("search", "fetch_content", "get_run", "get_wallet")


def curated_tools() -> tuple:
    """The eagerly-exposed capability_ids: free, read-only, small, and reviewed.

    An unadvertised capability cannot be in the hot set -- the hot set is an
    advertisement, and an advertisement is a permission to invoke.
    """
    return tuple("tinyfish." + name for name in CURATED_TOOLS
                 if ("tinyfish." + name) in REVIEWED_CAPABILITIES)


def catalog_digest() -> dict:
    """Cheap shape of the catalog: counts, pin and ids -- no descriptors.

    This is what a host may put in context to learn *what exists*; the argument
    contracts, billing notes and deferred reasons stay out until one capability
    is actually asked for.
    """
    return {
        "provider": PROVIDER,
        "transport": TRANSPORT,
        "tool_count": len(_TOOL_INDEX),
        "capabilities": sorted(capabilities()),
        "unreviewed_capabilities": sorted(unreviewed_capabilities()),
        "disabled_capabilities": sorted(disabled_capabilities()),
        "curated": list(curated_tools()),
        "catalog_sha256": catalog_sha256(),
        "readiness": "OFFLINE_TESTED",
    }


def describe(capability_id: str) -> dict:
    """One capability's full descriptor, on demand.

    Refuses an unreviewed id and a described-only capability with the same
    closed-set codes the gateway uses, so a lazy lookup cannot become a way to
    reach something the Registry would have refused.
    """
    tool = _tool_of(capability_id)
    entry = _TOOL_INDEX.get(tool) if tool else None
    if entry is None:
        raise ValueError(REASON_CAPABILITY_UNKNOWN)
    if not entry[3]:
        raise ValueError(REASON_CAPABILITY_NOT_AVAILABLE)
    return _descriptor(entry)


def capability_provenance(capability_id: str) -> dict:
    """Three separable evidence tiers for one capability.

    They are kept in separate keys, not merged into one "status", because they
    answer different questions and only the first exists today:

    * ``official_document`` -- what the vendor's published page lists. It is
      documentation: not a handshake, and it can be stale.
    * ``local_declaration`` -- what this adapter chose to enable and how it types
      the arguments. Ours; offline and reviewable.
    * ``upstream_tools_list`` -- what the real account advertises. NOT OBTAINED.
    """
    tool = _tool_of(capability_id)
    entry = _TOOL_INDEX.get(tool) if tool else None
    if entry is None:
        raise ValueError(REASON_CAPABILITY_UNKNOWN)
    descriptor = _descriptor(entry)
    return {
        "capability_id": capability_id,
        # A review state, not a local declaration: it says whether B's registry
        # has reviewed this capability, which is not ours to decide.
        "review_status": descriptor["review_status"],
        "official_document": {
            "status": "DOCUMENTED_NOT_VERIFIED",
            "claim": ("listed in the vendor's published MCP tool catalog as "
                      "`%s`" % tool),
            "source": CATALOG_SOURCE,
            "observed_at": CATALOG_OBSERVED_AT,
            "note": ("A published page is evidence about the interface the "
                     "vendor describes. It is not evidence about this account."),
        },
        "local_declaration": {
            "enabled": descriptor["enabled"],
            "tier": descriptor["tier"],
            "effect_class": descriptor["effect_class"],
            "availability": descriptor["availability"],
            "required_arguments": descriptor["required_arguments"],
            "optional_arguments": descriptor["optional_arguments"],
            "published_defaults": descriptor["published_defaults"],
            "deferred_reason": descriptor["deferred_reason"],
            "readiness": descriptor["readiness"],
            "review_status": descriptor["review_status"],
        },
        "upstream_tools_list": {
            "status": "NOT_OBTAINED",
            "sha256": None,
            "blocked_on": ("DECISION-04: no authorized read-only tools/list "
                           "handshake has been performed for this account."),
        },
    }


def capability_audit() -> dict:
    """The whole catalog with its tiers kept apart. No merged verdict."""
    return {
        "provider": PROVIDER,
        "transport": TRANSPORT,
        "tool_count": len(_TOOL_INDEX),
        "catalog_sha256": catalog_sha256(),
        "catalog_source": CATALOG_SOURCE,
        "catalog_observed_at": CATALOG_OBSERVED_AT,
        "readiness": "OFFLINE_TESTED",
        "upstream_tools_list_obtained": False,
        "capabilities": {("tinyfish." + tool): capability_provenance("tinyfish." + tool)
                         for tool in _TOOL_INDEX},
    }


def evidence_report() -> dict:
    """What this provider has and has not been verified against.

    The ladder is the contract's (§1). This adapter stops at OFFLINE_TESTED and
    says so in the fields a reader would otherwise have to infer from prose.
    """
    return {
        "provider": PROVIDER,
        "transport": TRANSPORT,
        "readiness": "OFFLINE_TESTED",
        "upstream_schema_verified": False,
        "schema_verified": False,
        "live_verified": False,
        "client_verified": False,
        "capture_mode": "OFFLINE_RECORDED",
        "live_handshake_performed": False,
        "vendor_schema_sha256": None,
        "pinned_hash_subject": "HOST_SUBSET_NOT_VENDOR_TOOLS_LIST",
        "catalog_sha256": catalog_sha256(),
        "blocked_on": ("DECISION-04: one bounded read-only upstream tools/list "
                       "handshake per provider has not been granted; without it "
                       "no vendor hash can be claimed."),
        "claims_not_made": ("UPSTREAM_SCHEMA_VERIFIED", "LIVE_VERIFIED",
                            "CLIENT_VERIFIED"),
    }


def integration_surface() -> dict:
    """The exact contract the router and Integration lane must wire against."""
    tools = _build_tools()
    surface = []
    for name in list(READ_TOOLS) + list(T3_TOOLS):
        entry = tools[name]
        surface.append({
            "tool": name,
            "tier": entry["tier"],
            "effect": entry["effect"],
            "arguments": {
                "required": entry["required_arguments"],
                "optional": entry["optional_arguments"],
            },
            "gate": entry["authorization"],
            "billing": dict(entry["billing"]),
        })
    return {
        "provider": PROVIDER,
        "endpoint": ENDPOINT,
        "transport": "OFFICIAL_STREAMABLE_HTTP_MCP",
        "catalog_sha256": catalog_sha256(),
        "auth_variables": list(TOKEN_ENV),
        "account_alias_variable": ACCOUNT_ALIAS_ENV,
        "provider_descriptions_are_untrusted": DESCRIPTIONS_ARE_UNTRUSTED,
        "tools": surface,
        "not_wired_here": sorted(DESCRIBED_TOOLS),
    }


# --------------------------------------------------------------------------
# Argument contracts. One closed allowlist per enabled tool: every published
# option is accepted, and an option that is not published is refused rather
# than forwarded, so a widened filter surface never becomes a pass-through.
# --------------------------------------------------------------------------
_MAX_ARGUMENT_KEYS = 24
_MAX_CONTAINER_ITEMS = 30
_MAX_ARGUMENT_DEPTH = 4
_MAX_STRING = 3000

_DOMAIN_TYPE = ("web", "news", "research_paper")
_FETCH_FORMAT = ("markdown", "html", "json")
_RUN_STATUS = ("PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED")
_SORT_DIRECTION = ("asc", "desc")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
                     r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_WORD = re.compile(r"^[A-Za-z]{2,16}$")


def _is_int(value) -> bool:
    # A bool is an int in Python; it must not satisfy an integer bound.
    return type(value) is int


# The URL allowlist is deliberately *not* implemented here. Two disagreeing
# SSRF guards is a shipped defect, so this adapter resolves one from a declared
# order instead: the plugin's own egress module first (W-A owns it), then the
# reviewed function the rest of the plugin already validates against. When
# neither is importable the answer is a refusal, never a second implementation.
#: **Exactly one** source: W-A's `research_egress.approved_url`, which is the
#: same `EgressGate` the browser and auxiliary-HTTP path uses. There is
#: deliberately no second entry. A fallback to another implementation is not
#: defence in depth here -- it is the SSRF policy bypass this seam exists to
#: prevent: the weaker of two rules decides, and nothing reports when they
#: diverge. A missing source is a governed refusal, not a reason to try another.
URL_GUARD_SOURCES = (
    ("research_egress", "approved_url"),
)

# What the guard module owns, and what this adapter owns.
#
# The guard owns *whether* a target is approved. On the integration tip its
# signature is `_approved_url(value, *, resolve=True)`, and its own docstring
# fixes the order: "the shape-only form runs during argument validation", the
# resolving form after the authorization checks. This adapter therefore owns the
# *order*, which is what this constant names. Asking the resolver during
# validation would make argument checking depend on the network and would refuse
# a call for "the name did not resolve" before the questions that decide whether
# the call was authorized at all -- the operator would read the wrong cause.
TARGET_GUARD_ORDER = "SHAPE_DURING_VALIDATION_RESOLVE_BEFORE_DISPATCH"


def _import_guard(module_name: str, attribute: str):
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return None
    guard = getattr(module, attribute, None)
    return guard if callable(guard) else None


def _url_guard():
    """The plugin's URL-allowlist rule, or raise a refusal if there is none."""
    for module_name, attribute in URL_GUARD_SOURCES:
        guard = _import_guard(module_name, attribute)
        if guard is not None:
            return guard
    # A missing guard used to surface as ``ModuleNotFoundError``. That is not a
    # closed-set code, so a gateway cannot classify it: a refusal that provably
    # dispatched nothing was reported as UNKNOWN-charge exposure. A missing
    # security dependency must be a loud refusal, not an ambiguous one.
    raise ValueError(REASON_EGRESS_REFUSED)


def _guard_takes_resolve(guard) -> bool:
    try:
        parameters = inspect.signature(guard).parameters
    except (TypeError, ValueError):
        return False
    parameter = parameters.get("resolve")
    return (parameter is not None
            and parameter.kind is inspect.Parameter.KEYWORD_ONLY)


def _guard_takes_resolver(guard) -> bool:
    try:
        parameters = inspect.signature(guard).parameters
    except (TypeError, ValueError):
        return False
    parameter = parameters.get("resolver")
    return (parameter is not None
            and parameter.kind is inspect.Parameter.KEYWORD_ONLY)


def approved_url(value, *, resolve: bool = False, resolver=None) -> str:
    """Apply the plugin's URL rule. Shape only unless ``resolve`` is asked for.

    The guard module owns *whether* a name is approved and, in its resolving
    form, whether the name's answers are globally routable. This adapter owns the
    *order*: asking the network must not happen before the questions that decide
    whether the call is authorized at all, so argument validation uses the shape
    form and the resolving form runs at the pre-dispatch gate.

    The resolving form is requested only from a guard that accepts it. An older
    guard without the keyword is shape-only, and calling it as if it resolved
    would be a control that is not there -- the caller is told so by
    ``TARGET_GUARD_ORDER`` rather than by a silent no-op.

    ``resolver`` is the guard's own injection point (W-A's
    ``approved_url(value, *, resolve=True, resolver=None)``). It is forwarded only
    to a guard that declares it, and it exists so an offline test can supply a
    deterministic name->address answer instead of patching production DNS. A
    guard without the seam resolves with the real resolver or not at all.
    """
    guard = _url_guard()
    accepts_resolver = _guard_takes_resolver(guard)
    if _guard_takes_resolve(guard):
        if resolve and resolver is not None and accepts_resolver:
            return guard(value, resolve=True, resolver=resolver)
        return guard(value, resolve=resolve)
    return guard(value)


def _guard_shape(arguments: dict, *, depth: int = 0) -> None:
    if depth > _MAX_ARGUMENT_DEPTH or len(arguments) > _MAX_CONTAINER_ITEMS:
        raise ValueError("TINYFISH_ARGUMENTS_TOO_LARGE")
    for value in arguments.values():
        if isinstance(value, dict):
            _guard_shape(value, depth=depth + 1)
        elif isinstance(value, (list, tuple)):
            if len(value) > _MAX_CONTAINER_ITEMS:
                raise ValueError("TINYFISH_ARGUMENTS_TOO_LARGE")
            for item in value:
                if isinstance(item, (dict, list, tuple)):
                    _guard_shape(item if isinstance(item, dict) else {},
                                 depth=depth + 1)
                elif not (item is None or type(item) in (str, bool, int, float)):
                    raise ValueError("TINYFISH_ARGUMENTS_INVALID")
        elif not (value is None or type(value) in (str, bool, int, float)):
            # sets, bytes, objects and other non-JSON values never go upstream
            raise ValueError("TINYFISH_ARGUMENTS_INVALID")
        elif isinstance(value, str) and len(value) > _MAX_STRING:
            raise ValueError("TINYFISH_ARGUMENTS_TOO_LARGE")


def _check_keys(arguments: dict, allowed: set, code: str) -> None:
    if set(arguments) - allowed:
        raise ValueError(code)


def _validate_search(arguments: dict) -> None:
    allowed = set(_TOOL_INDEX["search"][8]) | {"query"}
    _check_keys(arguments, allowed, "TINYFISH_SEARCH_ARGS_INVALID")
    query = arguments["query"]
    if not isinstance(query, str) or not 2 <= len(query) <= 240:
        raise ValueError("TINYFISH_SEARCH_ARGS_INVALID")

    published = {k: v for k, v in arguments.items() if k in allowed and k != "query"}
    domain_type = published.get("domain_type")
    if domain_type is not None and domain_type not in _DOMAIN_TYPE:
        raise ValueError("TINYFISH_SEARCH_DOMAIN_TYPE_INVALID")

    years = [published.get("pub_year_min"), published.get("pub_year_max")]
    if any(y is not None for y in years):
        if domain_type != "research_paper":
            raise ValueError("TINYFISH_SEARCH_PUB_YEAR_REQUIRES_RESEARCH_PAPER")
        for year in years:
            if year is not None and (not _is_int(year) or not 0 <= year <= 9999):
                raise ValueError("TINYFISH_SEARCH_ARGS_INVALID")
        if (published.get("pub_year_min") is not None
                and published.get("pub_year_max") is not None
                and published["pub_year_min"] > published["pub_year_max"]):
            raise ValueError("TINYFISH_SEARCH_PUB_YEAR_RANGE_INVERTED")

    for name in ("after_date", "before_date"):
        value = published.get(name)
        if value is not None and (not isinstance(value, str) or not _DATE.match(value)):
            raise ValueError("TINYFISH_SEARCH_ARGS_INVALID")
    if published.get("recency_minutes") is not None and (
            published.get("after_date") is not None
            or published.get("before_date") is not None):
        raise ValueError("TINYFISH_SEARCH_DATE_WINDOW_CONFLICT")
    recency = published.get("recency_minutes")
    if recency is not None and (not _is_int(recency) or not 1 <= recency <= 5_256_000):
        raise ValueError("TINYFISH_SEARCH_ARGS_INVALID")

    page = published.get("page")
    if page is not None and (not _is_int(page) or not 0 <= page <= 10):
        raise ValueError("TINYFISH_SEARCH_ARGS_INVALID")

    for name in ("include_thumbnail",):
        if published.get(name) is not None and type(published[name]) is not bool:
            raise ValueError("TINYFISH_SEARCH_ARGS_INVALID")
    for name in ("location", "language"):
        if published.get(name) is not None and (
                not isinstance(published[name], str)
                or not _WORD.match(published[name])):
            raise ValueError("TINYFISH_SEARCH_ARGS_INVALID")
    purpose = published.get("purpose")
    if purpose is not None and (not isinstance(purpose, str) or len(purpose) > 300):
        raise ValueError("TINYFISH_SEARCH_ARGS_INVALID")

    included = _domain_set(published.get("include_domains"),
                           "TINYFISH_SEARCH_ARGS_INVALID")
    excluded = _domain_set(published.get("exclude_domains"),
                           "TINYFISH_SEARCH_ARGS_INVALID")
    if included & excluded:
        raise ValueError("TINYFISH_SEARCH_DOMAIN_FILTER_CONFLICT")


def _domain_set(value, code: str) -> set:
    if value is None:
        return set()
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError(code)
    items = [part.strip().casefold().strip(".") for part in value.split(",")]
    items = [item for item in items if item]
    if not 1 <= len(items) <= 20 or any(not _DOMAIN.match(i) for i in items):
        raise ValueError(code)
    return set(items)


def _validate_fetch(arguments: dict) -> None:
    allowed = set(_TOOL_INDEX["fetch_content"][8]) | {"urls"}
    _check_keys(arguments, allowed, "TINYFISH_FETCH_ARGS_INVALID")
    urls = arguments["urls"]
    if (type(urls) is not list or not 1 <= len(urls) <= 10
            or not all(isinstance(u, str) and 1 <= len(u) <= 2000 for u in urls)):
        raise ValueError("TINYFISH_FETCH_ARGS_INVALID")

    published = {k: v for k, v in arguments.items() if k in allowed and k != "urls"}
    if published.get("format") is not None and published["format"] not in _FETCH_FORMAT:
        raise ValueError("TINYFISH_FETCH_ARGS_INVALID")
    for name in ("links", "image_links", "iframe_links", "page_metadata",
                 "include_etag_and_last_modified"):
        if published.get(name) is not None and type(published[name]) is not bool:
            raise ValueError("TINYFISH_FETCH_ARGS_INVALID")
    for name in ("include_selectors", "exclude_selectors"):
        value = published.get(name)
        if value is not None and (type(value) is not list or not 1 <= len(value) <= 20
                                  or not all(isinstance(s, str) and 1 <= len(s) <= 1000
                                             for s in value)):
            raise ValueError("TINYFISH_FETCH_ARGS_INVALID")
    ttl = published.get("ttl")
    if ttl is not None and (not _is_int(ttl) or not 0 <= ttl <= 31_536_000):
        raise ValueError("TINYFISH_FETCH_ARGS_INVALID")
    timeout = published.get("per_url_timeout_ms")
    if timeout is not None and (not _is_int(timeout) or not 1 <= timeout <= 300_000):
        raise ValueError("TINYFISH_FETCH_ARGS_INVALID")
    purpose = published.get("purpose")
    if purpose is not None and (not isinstance(purpose, str) or len(purpose) > 300):
        raise ValueError("TINYFISH_FETCH_ARGS_INVALID")
    for name, limit in (("if_none_match", 200), ("if_modified_since", 100)):
        value = published.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not 1 <= len(value) <= limit:
            raise ValueError("TINYFISH_FETCH_ARGS_INVALID")
        # The published contract allows a conditional header on one URL only.
        if len(urls) != 1:
            raise ValueError("TINYFISH_FETCH_CONDITIONAL_REQUIRES_SINGLE_URL")

    for target in urls:
        approved_url(target)


def _run_ids(value, maximum: int) -> None:
    if (type(value) is not list or not 1 <= len(value) <= maximum
            or not all(isinstance(v, str) and _RUN_ID.match(v) for v in value)):
        raise ValueError("TINYFISH_STATUS_ARGS_INVALID")


def _validate_status(tool: str, arguments: dict) -> None:
    code = "TINYFISH_STATUS_ARGS_INVALID"
    if tool == "get_run":
        _check_keys(arguments, {"id"}, code)
        if not isinstance(arguments["id"], str) or not _RUN_ID.match(arguments["id"]):
            raise ValueError(code)
    elif tool == "batch_status":
        _check_keys(arguments, {"run_ids"}, code)
        _run_ids(arguments["run_ids"], 8)
    elif tool == "cancel_run":
        _check_keys(arguments, {"id"}, code)
        if not isinstance(arguments["id"], str) or not _RUN_ID.match(arguments["id"]):
            raise ValueError(code)
    elif tool == "batch_cancel":
        _check_keys(arguments, {"run_ids"}, code)
        _run_ids(arguments["run_ids"], 8)
    elif tool == "list_runs":
        allowed = set(_TOOL_INDEX["list_runs"][8]) | set(_TOOL_INDEX["list_runs"][7])
        _check_keys(arguments, allowed, code)
        if set(arguments) < {"limit", "sort_direction"}:
            raise ValueError("TINYFISH_ARGUMENTS_INVALID")
        limit, direction = arguments["limit"], arguments["sort_direction"]
        if not _is_int(limit) or not 1 <= limit <= 100:
            raise ValueError(code)
        if direction not in _SORT_DIRECTION:
            raise ValueError(code)
        if arguments.get("status") is not None and arguments["status"] not in _RUN_STATUS:
            raise ValueError(code)
        if arguments.get("goal") is not None and not isinstance(arguments["goal"], str):
            raise ValueError(code)
        if arguments.get("cursor") is not None and not isinstance(arguments["cursor"], str):
            raise ValueError(code)
        for name in ("created_after", "created_before"):
            value = arguments.get(name)
            if value is not None and not isinstance(value, str):
                raise ValueError(code)


def _validate_usage(tool: str, arguments: dict) -> None:
    code = "TINYFISH_USAGE_ARGS_INVALID"
    maximum = 1000 if tool == "get_search_usage" else 100
    allowed = {"limit", "page", "status", "start_after", "end_before"}
    _check_keys(arguments, allowed, code)
    # Nothing is required: upstream documents a default for `limit`, so the bare
    # call is legal and the default is filled at dispatch (PUBLISHED_DEFAULTS).
    if "limit" in arguments and (not _is_int(arguments["limit"])
                                 or not 1 <= arguments["limit"] <= maximum):
        raise ValueError(code)
    if arguments.get("page") is not None and (
            not _is_int(arguments["page"]) or arguments["page"] < 1):
        raise ValueError(code)
    if arguments.get("status") is not None and arguments["status"] not in (
            "completed", "failed"):
        raise ValueError(code)
    for name in ("start_after", "end_before"):
        if arguments.get(name) is not None and not isinstance(arguments[name], str):
            raise ValueError(code)


def _validate_monitor(arguments: dict) -> None:
    code = "TINYFISH_MONITOR_ARGS_INVALID"
    _check_keys(arguments, {"monitor_id"}, code)
    monitor_id = arguments["monitor_id"]
    if not isinstance(monitor_id, str) or not _RUN_ID.match(monitor_id):
        raise ValueError(code)


def _validate_account_inventory(arguments: dict) -> None:
    """`list_browser_sessions`: every published parameter carries a default."""
    code = "TINYFISH_BROWSER_SESSION_ARGS_INVALID"
    # The registry reviews exactly `limit` and `page` for this tool. The
    # vendor documents four more, but a parameter the allowlist authority has
    # not reviewed is not ours to expose: surplus keys are refused with the
    # gateway's own closed-set code, before any SDK, client, ledger or egress
    # work, including when `execute()` is called directly.
    allowed = {"limit", "page"}
    if set(arguments) - allowed:
        raise ValueError("CAPABILITY_PARAM_NOT_ALLOWED")
    limit = arguments.get("limit")
    if limit is not None and (not _is_int(limit) or not 1 <= limit <= 1000):
        raise ValueError(code)
    page = arguments.get("page")
    if page is not None and (not _is_int(page) or page < 1):
        raise ValueError(code)
    if arguments.get("status") is not None and arguments["status"] not in (
            "running", "ended"):
        raise ValueError(code)
    for name in ("session_id", "start_after", "end_before"):
        if arguments.get(name) is not None and not isinstance(arguments[name], str):
            raise ValueError(code)


_ARGS_CODE = {
    "search": "TINYFISH_SEARCH_ARGS_INVALID",
    "fetch_content": "TINYFISH_FETCH_ARGS_INVALID",
    "get_run": "TINYFISH_STATUS_ARGS_INVALID",
    "batch_status": "TINYFISH_STATUS_ARGS_INVALID",
    "list_runs": "TINYFISH_STATUS_ARGS_INVALID",
    "cancel_run": "TINYFISH_STATUS_ARGS_INVALID",
    "batch_cancel": "TINYFISH_STATUS_ARGS_INVALID",
    "get_search_usage": "TINYFISH_USAGE_ARGS_INVALID",
    "list_fetch_usage": "TINYFISH_USAGE_ARGS_INVALID",
    "get_monitor": "TINYFISH_MONITOR_ARGS_INVALID",
    "list_browser_sessions": "TINYFISH_BROWSER_SESSION_ARGS_INVALID",
}


def validate(tool: str, arguments) -> dict:
    """Allowlist and type-check one TinyFish call before any dispatch.

    Unknown, unpublished and described-only tools are refused here, so a caller
    cannot reach a capability this release has not reviewed regardless of what
    the live server happens to advertise.

    Missing-argument codes are split on purpose: a call that supplies *nothing*
    is a caller bug (``TINYFISH_ARGUMENTS_INVALID``), while a call that supplies
    *some* of a tool's published parameters but not all of them is a malformed
    call to that tool and reports the tool's own code.
    """
    entry = _TOOL_INDEX.get(tool)
    if entry is None:
        raise ValueError("TINYFISH_TOOL_NOT_IN_OFFICIAL_CATALOG")
    if not entry[3]:
        raise ValueError("TINYFISH_TOOL_DESCRIBED_NOT_ENABLED")
    if type(arguments) is not dict:
        raise ValueError("TINYFISH_ARGUMENTS_INVALID")
    _guard_shape(arguments)

    required, optional = set(entry[7]), set(entry[8])
    if not required and not optional:
        # Tools that publish no parameters accept no parameters.
        if arguments:
            raise ValueError("TINYFISH_ARGUMENTS_INVALID")
        return arguments
    if not arguments and required:
        # Every published parameter has a documented default, so the bare call
        # is legal; PUBLISHED_DEFAULTS supplies them at dispatch.
        raise ValueError("TINYFISH_ARGUMENTS_INVALID")
    if not required <= set(arguments):
        raise ValueError(_ARGS_CODE[tool])

    if tool == "search":
        _validate_search(arguments)
    elif tool == "fetch_content":
        _validate_fetch(arguments)
    elif tool == "run_web_automation":
        _validate_agent(arguments)
    elif tool in ("get_run", "batch_status", "list_runs", "cancel_run",
                  "batch_cancel"):
        _validate_status(tool, arguments)
    elif tool in ("get_search_usage", "list_fetch_usage"):
        _validate_usage(tool, arguments)
    elif tool == "get_monitor":
        _validate_monitor(arguments)
    elif tool == "list_browser_sessions":
        _validate_account_inventory(arguments)
    return arguments


# Parameters the upstream server does publish on ``run_web_automation`` that
# this adapter refuses outright, each for its own reason. ``session_id`` is
# published and required, but *we* must mint it, so a caller-supplied value is
# a replay attempt rather than a parameter.
_AGENT_SESSION_MANAGED = ("session_id",)
_AGENT_PROFILE_PARAMETERS = ("use_profile", "profile_id", "use_vault",
                             "credential_item_ids")
_AGENT_BROWSER_PROFILE = ("LITE", "STEALTH")


def _validate_agent(arguments: dict) -> None:
    for name in _AGENT_SESSION_MANAGED:
        if name in arguments:
            raise ValueError("TINYFISH_AGENT_SESSION_ID_MUST_BE_GENERATED")
    for name in _AGENT_PROFILE_PARAMETERS:
        if name in arguments:
            raise ValueError(
                "TINYFISH_SITE_PROFILE_REQUIRES_SEPARATE_AUTHORIZATION")
    allowed = set(_TOOL_INDEX["run_web_automation"][7]) | set(
        _TOOL_INDEX["run_web_automation"][8])
    if set(arguments) - allowed:
        # Unknown includes the fork's invented ``agent_config``: the vendor
        # publishes no step/duration bound, so accepting one would let a
        # caller believe a provider-enforced ceiling exists.
        raise ValueError("TINYFISH_AGENT_UNPUBLISHED_PARAMETER")
    goal = arguments["goal"]
    if not isinstance(goal, str) or not 10 <= len(goal) <= 1000:
        raise ValueError("TINYFISH_AGENT_GOAL_INVALID")
    profile = arguments.get("browser_profile")
    if profile is not None and profile not in _AGENT_BROWSER_PROFILE:
        raise ValueError("TINYFISH_AGENT_BROWSER_PROFILE_INVALID")
    approved_url(arguments["url"])


def _safe_label(value, limit: int = 64) -> str:
    """A bounded, printable rendering of attacker-controlled text."""
    text = str(value)[:limit]
    return "".join(ch if ch.isprintable() else "\\x%02x" % ord(ch) for ch in text)


def schema_drift(advertised) -> dict:
    """Compare a live ``tools/list`` against the pin. Drift denies, never widens.

    Hostile values are quarantined rather than coerced: an object without a
    usable ``name``, a non-string, an empty string or an implausible name is
    counted as malformed and never becomes a capability.
    """
    if advertised is None or isinstance(advertised, (str, bytes, dict)):
        raise ValueError("TINYFISH_SCHEMA_ADVERTISEMENT_INVALID")
    try:
        items = list(advertised)
    except TypeError:
        raise ValueError("TINYFISH_SCHEMA_ADVERTISEMENT_INVALID") from None
    if len(items) > MAX_ADVERTISED_TOOLS:
        raise ValueError("TINYFISH_SCHEMA_ADVERTISEMENT_TOO_LARGE")

    names, malformed = [], []
    for item in items:
        raw = item if isinstance(item, str) else getattr(item, "name", None)
        if isinstance(raw, str) and _SAFE_NAME.match(raw):
            names.append(raw)
        else:
            # Kept as a bounded, escape-safe label rather than a bare count: an
            # operator has to be able to see *what* was refused, and a vendor
            # name is attacker-controlled text that must not carry raw control
            # characters into a receipt or a log.
            malformed.append(_safe_label(raw))

    seen, duplicated = set(), []
    for name in names:
        if name in seen and name not in duplicated:
            duplicated.append(name)
        seen.add(name)
    duplicated.sort()

    present = set(names)
    missing = sorted(_TOOL_NAMES - present)
    unexpected = sorted(present - _TOOL_NAMES)
    return {
        "advertised_count": len(items),
        "expected_count": len(_TOOL_NAMES),
        "present": sorted(present),
        "missing": missing,
        "unexpected": unexpected,
        "duplicated": duplicated,
        "malformed_count": len(malformed),
        "malformed": sorted(set(malformed)),
        "denied": sorted(set(missing) | set(unexpected) | set(malformed)),
        "unknown_tools_denied": True,
        # A malformed name is not a capability, but its presence means the
        # advertisement cannot be read at face value: a caller gating on `drift`
        # must not get False from a list that contains nonsense.
        "drift": bool(missing or unexpected or duplicated or malformed),
    }


# --------------------------------------------------------------------------
# Credential classification.
#
# The vendor's published pages do not state the wire contract for a key or a
# bearer token, so the classification below is our reviewed *reading* of the
# account's options and it is reported as unverified. It is never a claim
# that a customer's credential works.
# --------------------------------------------------------------------------
_PLACEHOLDER = re.compile(r"^\$\{[A-Z_][A-Z0-9_]*\}$")

# A provider API key and an OAuth access token are different credential
# classes and do not belong in the same header. The dedicated key header
# matches this repo's reviewed REST convention
# (`research_provider_policy._CREDENTIAL_WIRE`).
#
# UNVERIFIED: the vendor's published MCP pages do not state the wire contract
# for either credential. This is our reviewed reading, reported to every caller
# as `auth_wire_verified: false`, and it must be confirmed by one separately
# authorized real call before anyone calls it correct.
PROVIDER_KEY_HEADER = "X-API-Key"
OAUTH_TOKEN_HEADER = "Authorization"
AUTH_WIRE = ("X_API_KEY_HEADER_FOR_PROVIDER_KEY_AND_AUTHORIZATION_BEARER"
             "_FOR_OAUTH_ACCESS_TOKEN")
AUTH_WIRE_VERIFIED = False


def _keyring_tokens_present() -> bool:
    """Local presence hint only; never proof the OAuth session is valid."""
    try:
        import research_official_mcp_auth
        return bool(research_official_mcp_auth.has_saved_tokens())
    except Exception:
        return False


def credential_mode(environ=None) -> tuple:
    """(auth_mode, variable) or raise. No value is ever returned to a caller."""
    env = os.environ if environ is None else environ
    for name in TOKEN_ENV:
        value = env.get(name, "")
        if (isinstance(value, str) and value.strip()
                and not _PLACEHOLDER.fullmatch(value.strip())):
            return ("USER_PROVIDED_BEARER_KEY" if name == "TINYFISH_API_KEY"
                    else "USER_PROVIDED_MCP_ACCESS_TOKEN"), name
    if _keyring_tokens_present():
        return "OS_KEYRING_OAUTH", "OS_KEYRING_OAUTH"
    raise ValueError("TINYFISH_CREDENTIAL_REQUIRED")


# Charge state uses the §2 ChargeState vocabulary directly, with one name per
# value. Note what the adapter can and cannot know: a dispatched paid call is
# CHARGE_UNKNOWN because only the provider's own settlement can move it, and the
# Ledger — not this adapter — owns RESERVED. A successful free-tier read is
# CHARGE_FREE_TIER, and a refusal that provably never reached the vendor is
# CHARGE_NO_CHARGE_PROVEN.

AUTHORIZATION_SCOPE = "TINYFISH_PAID_AGENT_RUN"
AUTHORIZATION_ISOLATION = "SAME_OS_IDENTITY_NO_CRYPTOGRAPHIC_ISOLATION"
CEILING_ENFORCED_BY = "CLIENT_ASSERTION_NOT_PROVIDER_ENFORCED"
PROVIDER_HARD_CAP = "NOT_PUBLISHED"

_AGENT_STEPS_LABEL = "TinyFish Agent"


def _sanitize(value, *, depth: int = 0):
    """Bound and redact untrusted provider content before it is persisted."""
    if depth > 8:
        return "[TRUNCATED_NESTED_VALUE]"
    if isinstance(value, dict):
        return {str(k)[:90]: ("[REDACTED]" if str(k).casefold() in SECRET_FIELDS
                              else _sanitize(v, depth=depth + 1))
                for k, v in list(value.items())[:150]}
    if isinstance(value, list):
        return [_sanitize(item, depth=depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return value[:220000]
    if value is None or type(value) in (int, float, bool):
        return value
    return str(value)[:200]


def _run_handle(value, *, depth: int = 0):
    """Find the provider's own run handle. Never infer an id from free text."""
    if depth > 5:
        return None
    if isinstance(value, dict):
        for key in ("runId", "run_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and _RUN_ID.match(candidate):
                return candidate
        for part in value.values():
            found = _run_handle(part, depth=depth + 1)
            if found:
                return found
    if isinstance(value, list):
        for part in value[:20]:
            found = _run_handle(part, depth=depth + 1)
            if found:
                return found
    return None


def normalize_run_status(value) -> str:
    data = value
    for name in ("data", "result"):
        if isinstance(data, dict) and isinstance(data.get(name), dict):
            data = data[name]
            break
    code = data.get("status", data.get("state", "")) if isinstance(data, dict) else ""
    if not isinstance(code, str):
        return "unknown"
    code = code.casefold().strip()
    if code in {"pending", "queued", "running", "in_progress", "processing"}:
        return "RUNNING"
    if code in {"completed", "succeeded", "success", "done"}:
        return "COMPLETED"
    if code in {"failed", "error"}:
        return "FAILED"
    if code in {"cancelled", "canceled"}:
        return "CANCELLED"
    return "unknown"


def wallet_facts(data) -> dict:
    """Read the account's own wallet report. Strict: an unknown shape refuses.

    A best-effort parse would be worse than a refusal here: it could skip the
    only price check we have while still returning a green receipt.
    """
    if type(data) is not dict:
        raise ValueError("TINYFISH_AGENT_WALLET_NOT_VERIFIED")
    currency = data.get("currency", "USD")
    if currency != "USD":
        raise ValueError("TINYFISH_AGENT_WALLET_CURRENCY_UNSUPPORTED")
    try:
        balance = Decimal(str(data["available_balance"]))
        if not balance.is_finite() or abs(balance) > Decimal("100000000"):
            raise InvalidOperation()
        meters = data["rates"]["meters"]
        if type(meters) is not list or len(meters) > 20:
            raise ValueError("rates")
        step_rate = None
        for meter in meters:
            if type(meter) is not dict:
                raise ValueError("meter")
            if meter.get("label") != _AGENT_STEPS_LABEL:
                continue
            candidate = Decimal(str(meter["unit_amount"]))
            if not candidate.is_finite() or candidate < 0:
                raise InvalidOperation()
            step_rate = candidate
    except (KeyError, TypeError, IndexError, InvalidOperation, ValueError):
        raise ValueError("TINYFISH_AGENT_WALLET_NOT_VERIFIED") from None
    reloading = data.get("auto_reload", {})
    enabled = (reloading.get("enabled") if isinstance(reloading, dict) else None)
    if type(enabled) is not bool:
        enabled = None
    return {
        "balance": str(balance),
        "currency": "USD",
        "auto_reload_enabled": enabled,
        "auto_reload_is_an_operator_risk": enabled is True,
        "agent_step_rate": None if step_rate is None else str(step_rate),
        "evidence": "ACCOUNT_PROVIDER_REPORTED_UNVERIFIED",
    }


def wallet_observation(data) -> dict:
    """Lenient read of the account's own wallet report. Never raises.

    The *read* must not be all-or-nothing. Refusing the whole method because an
    account spells its payload differently makes the safest capability in the
    catalog unusable, and it hides the account's own answer behind our parse.
    Recognition still goes through the one strict parser, so there is exactly one
    definition of "recognised" -- `shape_recognized` reports whether it matched.

    Spending is guarded by `wallet_facts`, which stays strict and is what the
    paid-Agent preflight calls. Two policies, one parser.
    """
    observation = {
        "balance": None,
        "currency": None,
        "auto_reload_enabled": None,
        "auto_reload_is_an_operator_risk": False,
        "agent_step_rate": None,
        "evidence": "ACCOUNT_PROVIDER_REPORTED_UNVERIFIED",
        "shape_recognized": False,
    }
    if type(data) is not dict:
        return observation
    try:
        observation.update(wallet_facts(data))
    except ValueError:
        return observation
    observation["shape_recognized"] = True
    return observation


def _check_authorization(tool: str, authorization) -> None:
    if type(authorization) is not dict:
        raise ValueError("TINYFISH_AGENT_HUMAN_AUTHORIZATION_REQUIRED")
    approved_by = authorization.get("approved_by")
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise ValueError("TINYFISH_AGENT_HUMAN_AUTHORIZATION_REQUIRED")
    if authorization.get("scope") != AUTHORIZATION_SCOPE:
        raise ValueError("TINYFISH_AGENT_HUMAN_AUTHORIZATION_SCOPE_INVALID")
    if (tool == "run_web_automation"
            and authorization.get("accept_uncapped_provider_spend") is not True):
        # The vendor publishes no per-call step ceiling. Without an explicit
        # acknowledgement there is nothing to stop a run, so we refuse instead
        # of advertising a USD guarantee we cannot enforce.
        raise ValueError("TINYFISH_AGENT_PROVIDER_HARD_CAP_NOT_PUBLISHED")


def _check_ceiling(value) -> Decimal:
    if not value:
        raise ValueError("TINYFISH_AGENT_MAX_CHARGE_REQUIRED")
    try:
        ceiling = Decimal(str(value))
        if not ceiling.is_finite() or ceiling <= 0 or ceiling > MAX_CHARGE_CEILING_USD:
            raise InvalidOperation()
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("TINYFISH_AGENT_MAX_CHARGE_INVALID") from None
    return ceiling


def _receipt(*, tool, entry, payload=None, error=None, charge_state,
             auth_mode="", drift=None, dispatched=False):
    return {
        "ok": error is None,
        "provider": PROVIDER,
        "mcp_tool": tool,
        "transport": "OFFICIAL_STREAMABLE_HTTP_MCP",
        "endpoint": ENDPOINT,
        "auth_mode": auth_mode,
        "auth_wire": AUTH_WIRE,
        "auth_wire_verified": AUTH_WIRE_VERIFIED,
        "auth_header": _auth_header(auth_mode),
        "catalog_sha256": catalog_sha256(),
        "tier": entry[1],
        "effect": entry[2],
        "error": error,
        "tool_result": _sanitize(payload) if payload is not None else None,
        "mcp_tool_completed": error is None,
        "business_evidence_verified": False,
        "actual_charge_usd": None,
        "provider_charge_verified": False,
        "charge_state": charge_state,
        "retry_permitted": False,
        "schema_drift": drift,
        # `dispatched` is the fact the charge taxonomy rests on: a refusal that
        # never dispatched is NO_CHARGE_PROVEN, anything that may have gone out
        # is UNKNOWN.
        "dispatched": bool(dispatched),
        # Non-fatal observations about this dispatch. Kept separate from the
        # AdapterResult caveats because they are assembled at different times.
        "caveats": (),
    }


def call(tool: str, arguments, *, max_charge_usd="", authorization=None,
         runner=None, advertised_tools=None, environ=None,
         transport=None) -> dict:
    """Dispatch one reviewed TinyFish tool, or refuse it before dispatch.

    Two failure shapes, deliberately distinct:

    * A refusal raised as ``ValueError`` means nothing reached the vendor, so
      no money can have moved. Retrying it is free and safe.
    * A returned receipt with ``ok: False`` means the call may already have
      reached the vendor. ``charge_state`` is then ``UNKNOWN (§2 ChargeState)`` and
      ``retry_permitted`` is ``False``; a caller must reconcile, never re-send.

    ``runner`` is an injected async coroutine used by the offline tests; it
    receives ``(tool, arguments)`` and returns the tool's data payload.
    """
    entry = _TOOL_INDEX.get(tool)
    validate(tool, arguments)

    drift = None
    if advertised_tools is not None:
        drift = schema_drift(advertised_tools)
        if drift["drift"]:
            raise ValueError("TINYFISH_SCHEMA_DRIFT_REFUSED")

    env = os.environ if environ is None else environ
    if env.get(NETWORK_GATE_ENV) != "1":
        raise ValueError("TINYFISH_NETWORK_NOT_AUTHORIZED")
    auth_mode, _variable = credential_mode(env)

    if tool in T3_TOOLS:
        if env.get(PAID_GATE_ENV) != "1":
            raise ValueError("TINYFISH_AGENT_PAID_NOT_AUTHORIZED")
        _check_authorization(tool, authorization)

    ceiling = None
    if tool == "run_web_automation":
        ceiling = _check_ceiling(max_charge_usd)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        # A blocking adapter called from inside an event loop would deadlock or
        # leave an unawaited coroutine behind, possibly after dispatch.
        raise ValueError("OFFICIAL_MCP_SYNC_CALL_INSIDE_RUNNING_LOOP")

    payload = {
        "runner": runner,
        "transport": transport,
        "tool": tool,
        "arguments": dict(arguments),
        "ceiling": ceiling,
        "auth_mode": auth_mode,
        "drift": drift,
    }
    try:
        return asyncio.run(_run(payload))
    except ValueError:
        raise
    except Exception:
        # Last resort. Everything that could reach the vendor has already been
        # classified inside _run; this only catches shape surprises.
        if entry[5]["may_charge"]:
            return _receipt(tool=tool, entry=entry, auth_mode=auth_mode,
                            error="TINYFISH_DISPATCH_OUTCOME_UNKNOWN",
                            charge_state=CHARGE_UNKNOWN, drift=drift)
        raise ValueError("TINYFISH_DISPATCH_FAILED") from None


# Codes our own layers raise. They are safe to surface and none of them can mean
# a charge was incurred.
_SAFE_CODES = frozenset({
    "UPSTREAM_MCP_TOOL_FAILED", "UPSTREAM_MCP_EMPTY_RESULT",
    "UPSTREAM_MCP_RESULT_TOO_LARGE", "UPSTREAM_MCP_TOOL_NOT_ADVERTISED",
    "UPSTREAM_MCP_TRANSPORT_OR_SESSION_FAILED", "TINYFISH_SCHEMA_DRIFT_REFUSED",
    "TINYFISH_TOOL_NOT_ADVERTISED",
})

# A refusal that is provably pre-dispatch even though it surfaces from inside
# the session, because the session refuses before calling the paid tool.
_PRE_DISPATCH_CODES = frozenset({
    "UPSTREAM_MCP_TOOL_NOT_ADVERTISED", "TINYFISH_TOOL_NOT_ADVERTISED",
    "TINYFISH_SCHEMA_DRIFT_REFUSED",
})


def _auth_header(auth_mode: str) -> str:
    return {"OS_KEYRING_OAUTH": "SDK_MANAGED_OAUTH",
            "USER_PROVIDED_MCP_ACCESS_TOKEN": OAUTH_TOKEN_HEADER}.get(
                auth_mode, PROVIDER_KEY_HEADER)


def _targets_of(tool: str, arguments: dict) -> tuple:
    """The URLs a capability would ask the provider to fetch."""
    if tool == "fetch_content":
        urls = arguments.get("urls")
        return tuple(urls) if isinstance(urls, list) else ()
    if tool == "run_web_automation":
        url = arguments.get("url")
        return (url,) if isinstance(url, str) else ()
    return ()


# Every refusal code this adapter raises that may be surfaced to a caller. Used
# to decide whether an exception carries a code we may pass on or must replace.
# Refusals that mean "the target was not allowed", from either half of the
# guard. They fold to one closed-set code so an operator reads egress policy
# rather than guessing which layer said no. The guard's convention is
# `CODE: detail`, so the code is the first colon-separated token.
# A capability that moved upstream (or vanished) is a schema-drift refusal, not
# a generic unavailability: the operator needs to tell "the vendor changed this"
# from "we never wired it".
_SCHEMA_DRIFT_CODES = frozenset({
    "TINYFISH_TOOL_NOT_ADVERTISED", "UPSTREAM_MCP_TOOL_NOT_ADVERTISED",
    "TINYFISH_SCHEMA_DRIFT_REFUSED",
})

_EGRESS_REFUSAL_CODES = frozenset({
    "OFFICIAL_MCP_TARGET_NOT_ALLOWED",
    "TARGET_RESOLUTION_FAILED",
    "TARGET_RESOLVES_NON_GLOBAL",
    "TARGET_RESOLVES_TO_MIXED_ADDRESSES",
})

_WALLET_PRECONDITION_CODES = frozenset({
    "TINYFISH_AGENT_WALLET_READ_FAILED", "TINYFISH_AGENT_WALLET_NOT_VERIFIED",
    "TINYFISH_AGENT_WALLET_EMPTY", "TINYFISH_AGENT_WALLET_CURRENCY_UNSUPPORTED",
})


_KNOWN_BRIDGE_CODES = (_SAFE_CODES | _PRE_DISPATCH_CODES | _SCHEMA_DRIFT_CODES
                       | _EGRESS_REFUSAL_CODES | _WALLET_PRECONDITION_CODES
                       | frozenset({"TINYFISH_AGENT_SPEND_CAP_EXCEEDED"}))


def _known_refusal_code(exc) -> str | None:
    """The refusal code carried by ``exc``, unwrapping task-group nesting.

    Returns ``None`` when the exception is not one of ours, so the caller can
    replace it instead of leaking upstream text (which may carry credentials).
    """
    if isinstance(exc, BaseExceptionGroup):
        codes = set()
        def descend(error):
            if isinstance(error, BaseExceptionGroup):
                for child in error.exceptions:
                    descend(child)
            else:
                found = _known_refusal_code(error)
                if found is not None:
                    codes.add(found)
        descend(exc)
        return codes.pop() if len(codes) == 1 else None
    if isinstance(exc, ValueError):
        code = str(exc).split(":", 1)[0].strip()
        return code if code in _KNOWN_BRIDGE_CODES else None
    return None


async def _run(plan: dict) -> dict:
    """Pre-dispatch gates, then exactly one dispatch. Owns the charge boundary.

    Everything above the ``await runner(tool, ...)`` line is provably
    pre-dispatch: a failure there raises a refusal and cannot have spent
    anything. Everything at or below it is ambiguous and becomes a receipt.
    """
    entry = _TOOL_INDEX[plan["tool"]]
    tool = plan["tool"]
    arguments = dict(plan["arguments"])
    auth_mode = plan["auth_mode"]
    drift = plan["drift"]
    transport = plan.get("transport")

    async def runner(tool, arguments):
        return await (plan["runner"] or _live_runner)(
            tool, arguments, **({"transport": transport} if transport
                                and not plan["runner"] else {}))

    async def read_wallet():
        return await runner("get_wallet", {})
    wallet = None

    for name, default in PUBLISHED_DEFAULTS.get(tool, {}).items():
        arguments.setdefault(name, default)

    # The resolving form of the target guard, after the authorization checks
    # that decided this call was allowed to happen at all. Everything above this
    # line is provably pre-dispatch, so a refusal here costs nothing.
    for target in _targets_of(tool, arguments):
        approved_url(target, resolve=True)

    if tool == "fetch_content":
        # The actual 2026-10-20 tools/list requires format and all four flags,
        # including iframe_links, while also documenting
        # defaults for them, so sending nothing leaves the server's behaviour
        # undefined for us. Sending the documented defaults is behaviour
        # preserving under either reading, and an explicit caller value still
        # wins. (This is one of the four W-C corrections.)
        for name, default in (("format", "markdown"), ("links", False),
                              ("image_links", False), ("iframe_links", False),
                              ("page_metadata", False)):
            arguments.setdefault(name, default)

    if tool == "run_web_automation":
        try:
            raw_wallet = await read_wallet()
        except Exception:
            # Only the wallet read failed; the agent was never submitted.
            raise ValueError("TINYFISH_AGENT_WALLET_READ_FAILED") from None
        wallet = wallet_facts(raw_wallet)
        if Decimal(wallet["balance"]) <= 0:
            raise ValueError("TINYFISH_AGENT_WALLET_EMPTY")
        rate = wallet["agent_step_rate"]
        # No ceiling means the caller is the Ledger, which already enforced its
        # own limit; the adapter does not invent a second budget source (IA-2).
        if (rate is not None and plan["ceiling"] is not None
                and Decimal(rate) > plan["ceiling"]):
            raise ValueError("TINYFISH_AGENT_SPEND_CAP_EXCEEDED")
        arguments["session_id"] = str(uuid.uuid4())

    try:
        payload = await runner(tool, arguments)
    except Exception as exc:
        # Both transports run the session inside a task group, so *our own*
        # refusals come back wrapped in an ExceptionGroup. Unwrapping is what
        # keeps a precise refusal from degrading into an unclassifiable
        # transport failure.
        code = _known_refusal_code(exc)
        if code is not None and code in _PRE_DISPATCH_CODES:
            # Provably nothing was described to the vendor.
            raise ValueError(code) from None
        if code in _UPSTREAM_DEFINITE_FAILURES and not entry[5]["may_charge"]:
            # The tool ran and answered "no". For an unmetered capability that
            # is a definite answer, not ambiguity, so it keeps its own code.
            # A *metered* capability is different: it ran, so it may have been
            # billed, and "the vendor said no" is not proof it was free.
            raise ValueError(code) from None
        # Anything else was dispatched and is unresolved. That is UNKNOWN
        # whether or not the capability is metered: the question this field
        # answers is "may something have left this process", not "did it cost
        # money". A free read whose connection died mid-flight is not
        # NO_CHARGE_PROVEN, because nothing proved it did not go out.
        return _receipt(tool=tool, entry=entry, auth_mode=auth_mode,
                        error="TINYFISH_DISPATCH_OUTCOME_UNKNOWN",
                        charge_state=CHARGE_UNKNOWN, drift=drift,
                        dispatched=True)

    charge = CHARGE_UNKNOWN if entry[5]["may_charge"] else CHARGE_FREE_TIER
    receipt = _receipt(tool=tool, entry=entry, payload=payload, auth_mode=auth_mode,
                       charge_state=charge, drift=drift)

    if tool == "run_web_automation":
        handle = _run_handle(payload)
        if handle is None:
            # The run may already be executing. Re-submitting is the one thing
            # that would double the exposure, so this is never a success.
            receipt.update(ok=False, mcp_tool_completed=False,
                           error="TINYFISH_AGENT_RUN_ID_MISSING_AFTER_SUBMISSION",
                           charge_state=CHARGE_UNKNOWN, retry_permitted=False)
        else:
            receipt.update(agent_run_id=handle,
                           agent_lifecycle="SUBMITTED_NOT_TERMINAL",
                           agent_status=normalize_run_status(payload))
        receipt.update(session_id_used=arguments["session_id"],
                       ceiling_enforced_by=CEILING_ENFORCED_BY,
                       provider_hard_cap=PROVIDER_HARD_CAP,
                       authorization_isolation=AUTHORIZATION_ISOLATION)
        if plan["ceiling"] is not None:
            receipt["max_charge_usd_declared"] = str(plan["ceiling"])
    elif tool in ("get_run", "batch_status", "list_runs"):
        receipt["agent_status"] = normalize_run_status(payload)
    elif tool == "get_wallet":
        receipt["wallet"] = wallet_observation(payload)
        if not receipt["wallet"]["shape_recognized"]:
            # Reported, not hidden: the account answered, we did not recognise
            # the shape, and no part of that is a verified balance.
            receipt["caveats"] = tuple(receipt["caveats"]) + (
                "WALLET_SHAPE_UNRECOGNIZED",)
    if wallet is not None:
        receipt["wallet"] = wallet
    return receipt


@asynccontextmanager
async def _official_transport(endpoint: str, http_client):
    """The production transport: the official Streamable HTTP MCP endpoint."""
    from mcp.client.streamable_http import streamable_http_client
    async with streamable_http_client(endpoint, http_client=http_client) as streams:
        yield streams


async def _live_runner(tool: str, arguments: dict, *, transport=None) -> dict:
    """The real MCP session: initialize, list_tools, drift-check, call once.

    ``transport`` is an injectable seam of the same shape as
    ``_official_transport``. It exists so the offline tests can drive *this*
    code against a real in-process MCP server over the SDK's memory streams --
    the handshake under test is then the SDK's own, not a patch over it.
    """
    from mcp import ClientSession
    import httpx2

    auth_mode, variable = credential_mode()
    auth, headers = None, {}
    if auth_mode == "OS_KEYRING_OAUTH":
        from research_official_mcp_auth import oauth_provider
        auth = oauth_provider(interactive=False)
    elif auth_mode == "USER_PROVIDED_MCP_ACCESS_TOKEN":
        headers = {OAUTH_TOKEN_HEADER: "Bearer " + os.environ[variable]}
    else:
        headers = {PROVIDER_KEY_HEADER: os.environ[variable]}

    opener = transport or _official_transport
    async with httpx2.AsyncClient(headers=headers, auth=auth,
                                  follow_redirects=False,
                                  timeout=httpx2.Timeout(65.0, connect=10.0)) as http:
        async with opener(ENDPOINT, http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                listed = await session.list_tools()
                advertised = [getattr(t, "name", None) for t in listed.tools]
                report = schema_drift(advertised)
                # IR-4, applied per capability rather than per session: a
                # capability whose own upstream tool moved is refused, and a
                # T2+ capability is refused on any drift, while a compatible T1
                # read continues. An unreviewed tool the server newly advertises
                # still has no capability_id and can never be invoked.
                verdict = verify_schema(advertised).get("tinyfish." + tool)
                if verdict != SCHEMA_MATCH:
                    raise ValueError("TINYFISH_TOOL_NOT_ADVERTISED")
                if report["drift"] and _TIER_RANK.get(_TOOL_INDEX[tool][1], 1) >= 2:
                    raise ValueError("TINYFISH_SCHEMA_DRIFT_REFUSED")
                return _extract_payload(await session.call_tool(tool, arguments))


def _extract_payload(result) -> dict:
    """A successful *transport* is not a successful tool."""
    if bool(getattr(result, "isError", False)) or bool(getattr(result, "is_error", False)):
        raise ValueError("UPSTREAM_MCP_TOOL_FAILED")
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if structured is not None:
        data = structured
        if isinstance(data, dict) and isinstance(data.get("result"), str):
            try:
                data = json.loads(data["result"])
            except json.JSONDecodeError:
                data = {"text": data["result"]}
    else:
        blocks = getattr(result, "content", []) or []
        text = "\n".join(getattr(b, "text", "") for b in blocks
                         if isinstance(getattr(b, "text", None), str))
        if not text:
            raise ValueError("UPSTREAM_MCP_EMPTY_RESULT")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {"text": text}
    if len(json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")) > 3_500_000:
        raise ValueError("UPSTREAM_MCP_RESULT_TOO_LARGE")
    return data



# ===========================================================================
# ProviderAdapter (INTERFACE-CONTRACT.md §6)
#
# Scope note, stated plainly because it decides what may live below:  §6 / IA-2
# say the adapter **does not** perform tier, policy or budget decisions — it
# receives a request that Registry + Policy + Ledger have already authorized,
# and deciding again would create a second policy source. The functions above
# (`call()`, `_check_authorization`, `_check_ceiling`, the wallet precondition)
# are therefore the *policy edge*, provided so the negative behaviour is
# testable now; at integration the Policy/Registry lane owns them and the
# adapter proper is `execute()` / `cancel()` / `probe_schema()` below.
#
# `execute()` is not a bypass: it refuses without a well-formed request, refuses
# an unreviewed or disabled capability, refuses a T2+ capability with no grant,
# refuses on schema drift, and enforces the parameter allowlist. What it does
# not do is re-litigate whether the project may spend.
# ===========================================================================

# The §3.1 CapabilityRequest fields. B owns the type; until it lands the adapter
# duck-types it, which keeps this module importable at a base commit that
# predates research_contract.py.
_REQUEST_FIELDS = ("capability_id", "params", "run_id", "operation_id",
                   "account_ref", "intent_fingerprint", "grant_ref")

# How the *shipped* MarketOps caller intends to call TinyFish, one entry per
# MarketOps operation, in the integration's own vocabulary. It lives here so the
# handoff to the Integrator is machine-checkable rather than prose: a test
# validates each shape against this adapter's argument contract, and the
# integration patches in `references/patches/` bring the caller to match.
#
# Values are synthetic; nothing here is a credential or an account identifier.
SHIPPING_CALL_SHAPES = {
    "tinyfish_mcp_search": ("search", {"query": "public venue list"}),
    "tinyfish_mcp_fetch": ("fetch_content", {
        "urls": ["https://public.example.org/a"], "format": "markdown",
        "links": False, "image_links": False, "iframe_links": False, "page_metadata": False}),
    "tinyfish_mcp_wait": ("get_run", {"id": "run-abc_1"}),
}

# Parameters the vendor documents a default for. They are sent explicitly so a
# bare call matches the published schema, and an explicit caller value wins.
PUBLISHED_DEFAULTS = {
    "get_search_usage": {"limit": 100, "page": 1},
    "list_fetch_usage": {"limit": 20, "page": 1},
    "list_browser_sessions": {"limit": 100, "page": 1},
}

# A dispatched paid operation the adapter cannot settle on its own.
# Failures where the vendor answered. They are definite, not ambiguous.
_UPSTREAM_DEFINITE_FAILURES = frozenset({
    "UPSTREAM_MCP_TOOL_FAILED", "UPSTREAM_MCP_EMPTY_RESULT",
    "UPSTREAM_MCP_RESULT_TOO_LARGE",
})

_RECEIPT_FAILED_CODES = frozenset({
    "UPSTREAM_MCP_TOOL_FAILED", "UPSTREAM_MCP_EMPTY_RESULT",
    "UPSTREAM_MCP_RESULT_TOO_LARGE",
})


_TIER_RANK = {"T0": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4}




#: The capability ids that have a reviewed counterpart in the registry
#: (`research_wiring.REVIEWED_CAPABILITY` after the handoff's proposed
#: additions). Declared, never inferred: adding an advertisement must be a
#: deliberate edit here rather than a side effect of enabling a tool.
#:
#: Everything else this adapter can serve is NOT_REVIEWED. It stays callable
#: internally and labelled in the catalog, but it is **not advertised**, so it
#: is not user-invokable through the gateway -- which enumerates this tuple.
REVIEWED_CAPABILITIES = (
    # B's registry @ 672447de: reviewed **and** ENABLED.
    "tinyfish.search", "tinyfish.fetch_content",
    "tinyfish.get_run", "tinyfish.list_runs", "tinyfish.batch_status",
    "tinyfish.get_wallet", "tinyfish.get_search_usage", "tinyfish.list_fetch_usage",
    "tinyfish.get_monitor", "tinyfish.list_monitors", "tinyfish.list_browser_sessions",
    "tinyfish.run_web_automation",
)

#: Reviewed but DISABLED (T4) by the same registry. Not "unreviewed": they were
#: reviewed and deliberately not enabled, which is a different owner and a
#: different next action.
DISABLED_REVIEWED_CAPABILITIES = (
    "tinyfish.cancel_run", "tinyfish.batch_cancel",
)


def _review_status(capability_id: str) -> str:
    if capability_id in REVIEWED_CAPABILITIES:
        return "REVIEWED"
    if capability_id in DISABLED_REVIEWED_CAPABILITIES:
        return "REVIEWED_DISABLED"
    return "UNREVIEWED"


def _served_capabilities() -> tuple:
    return tuple("tinyfish." + name
                 for name in list(READ_TOOLS) + list(T3_TOOLS))


def capabilities(*, include_unreviewed: bool = False) -> tuple:
    """The advertised capability_ids, in Registry id shape (§4).

    Default is the reviewed subset: an advertisement is a permission to invoke,
    and B's registry decides review. ``include_unreviewed=True`` returns what the
    adapter can actually serve, for callers that hold the review decision
    themselves.
    """
    served = _served_capabilities()
    if include_unreviewed:
        return served
    return tuple(capability_id for capability_id in served
                 if capability_id in REVIEWED_CAPABILITIES)


def disabled_capabilities() -> tuple:
    """Reviewed but T4-disabled: not advertised, not routable, not unreviewed.

    Scanned over the catalog rather than over "what is served": a disabled
    capability is precisely one the adapter does not serve.
    """
    return tuple("tinyfish." + tool for tool in _TOOL_INDEX
                 if ("tinyfish." + tool) in DISABLED_REVIEWED_CAPABILITIES)


def unreviewed_capabilities() -> tuple:
    return tuple(capability_id for capability_id in _served_capabilities()
                 if capability_id not in REVIEWED_CAPABILITIES
                 and capability_id not in DISABLED_REVIEWED_CAPABILITIES)


def _tool_of(capability_id) -> str | None:
    if not isinstance(capability_id, str) or not capability_id.startswith("tinyfish."):
        return None
    return capability_id[len("tinyfish."):]


def verify_schema(advertised) -> dict:
    """Per-capability §2 SchemaVerdict against an observed ``tools/list``.

    Held per capability rather than per session, because §6 / IR-4 rejects a
    capability whose upstream tool moved while leaving compatible T1 reads
    alone. An advertised name the catalog has not reviewed gets no
    capability_id at all, so it can never be invoked (IR-1 / IR-5).
    """
    report = schema_drift(advertised)
    present = set(report["present"])
    verdicts = {}
    for capability_id in _served_capabilities():
        tool = capability_id[len("tinyfish."):]
        verdicts[capability_id] = SCHEMA_MATCH if tool in present else SCHEMA_ABSENT
    return verdicts


TOOLS_LIST_SCHEMA_VERSION = "marketops.tinyfish-tools-list.v1"

#: The only descriptor fields that enter the digest. A vendor may add fields; a
#: digest that absorbed them would change for reasons we never reviewed.
_DESCRIPTOR_FIELDS = ("name", "description", "input_schema", "annotations")

_AUTHORIZED_CAPTURE = "AUTHORIZED_REAL_CAPTURE"
_SYNTHETIC_CAPTURE = "OFFLINE_SYNTHETIC"


def _canonical_descriptor(record: dict) -> dict:
    return {key: record[key] for key in _DESCRIPTOR_FIELDS
            if key in record and record[key] is not None}


def _hash_json(value) -> str:
    blob = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _descriptor_digest(records) -> str:
    ordered = sorted(records, key=lambda record: record["name"])
    return _hash_json([_canonical_descriptor(record) for record in ordered])


def discover_schema(*, descriptors=None, names=None, baseline=None,
                    capture=_SYNTHETIC_CAPTURE, attestation=None) -> dict:
    """A bounded, composable `tools/list` discovery result.

    The digest is over the **full** canonical descriptor -- name, description,
    inputSchema, annotations when advertised -- because a hash of tool names
    alone cannot see a schema change, and a digest that cannot see the schema is
    not a schema digest.

    `capture` is a *label about provenance*, never an inference:

    * ``OFFLINE_SYNTHETIC`` -- descriptors we constructed (tests, local pins).
      ``vendor_schema_sha256`` stays ``None`` and promotion stays
      ``NOT_CAPTURED``.
    * ``AUTHORIZED_REAL_CAPTURE`` -- only with an ``attestation`` naming the
      authorization and the capture time. Even then this module does not set
      ``schema_verified``: that verdict belongs to the registry and QA, not to
      the adapter that read the bytes.
    """
    if capture == _AUTHORIZED_CAPTURE:
        if (type(attestation) is not dict
                or not str(attestation.get("authorization_ref") or "").strip()
                or not str(attestation.get("captured_at") or "").strip()):
            raise ValueError("TINYFISH_CAPTURE_ATTESTATION_REQUIRED")
    elif capture != _SYNTHETIC_CAPTURE:
        raise ValueError("TINYFISH_CAPTURE_MODE_INVALID")

    if descriptors is not None:
        if type(descriptors) is not list:
            raise ValueError("TINYFISH_TOOLS_LIST_INVALID")
        if len(descriptors) > MAX_ADVERTISED_TOOLS:
            raise ValueError("TINYFISH_SCHEMA_ADVERTISEMENT_TOO_LARGE")
        records = []
        for item in descriptors:
            if type(item) is not dict or not isinstance(item.get("name"), str):
                raise ValueError("TINYFISH_TOOLS_LIST_INVALID")
            records.append(_canonical_descriptor(item))
        reduced = False
        digest_subject = "VENDOR_TOOLS_LIST_DESCRIPTORS"
    elif names is not None:
        # Backward compatible, and labelled: a name list cannot establish the
        # official schema, so it must not be presented as one.
        report = schema_drift(names)
        records = [{"name": name} for name in sorted(set(report["present"]))]
        reduced = True
        digest_subject = "TOOL_NAMES_ONLY"
    else:
        raise ValueError("TINYFISH_TOOLS_LIST_INVALID")

    present = sorted(record["name"] for record in records)
    drift = {"tool_added": sorted(set(present) - _TOOL_NAMES),
             "tool_removed": sorted(_TOOL_NAMES - set(present)),
             "malformed": sorted(set(schema_drift(present)["malformed"])),
             "schema_changed": [], "basis": "NO_BASELINE"}
    if baseline is not None and type(baseline.get("tools")) is list:
        prior = {record["name"]: _hash_json(_canonical_descriptor(record))
                 for record in baseline["tools"]}
        now = {record["name"]: _hash_json(_canonical_descriptor(record))
               for record in records}
        drift["schema_changed"] = sorted(
            name for name in set(prior) & set(now) if prior[name] != now[name])
        drift["basis"] = "BASELINE_TOOL_DIGESTS"

    trusted = capture == _AUTHORIZED_CAPTURE
    digest = _descriptor_digest(records)
    return {
        "schema_version": TOOLS_LIST_SCHEMA_VERSION,
        "provider": PROVIDER,
        "endpoint": ENDPOINT,
        "transport": TRANSPORT,
        "capture": {"mode": capture,
                    "trusted_as_vendor": trusted,
                    "live_handshake_performed": bool(
                        trusted and attestation.get("live_handshake_performed"))},
        "promotion": _AUTHORIZED_CAPTURE if trusted else "NOT_CAPTURED",
        "raw_tool_count": len(records),
        "tools": records,
        "tool_names": present,
        "digest_subject": digest_subject,
        "digest_sha256": digest,
        "reduced": reduced,
        # Only a trusted capture may publish a vendor hash. This adapter never
        # sets `schema_verified`: reading the bytes is not verifying the vendor.
        "vendor_schema_sha256": digest if trusted else None,
        "schema_verified": False,
        "drift": drift,
        "catalog_sha256": catalog_sha256(),
    }


def _descriptors_from_session(transport) -> list:
    """Open one real MCP session and return its canonical tool descriptors."""
    from mcp import ClientSession
    import httpx2
    from research_official_mcp_auth import oauth_provider  # optional

    auth_mode, variable = credential_mode()
    auth, headers = None, {}
    if auth_mode == "OS_KEYRING_OAUTH":
        auth = oauth_provider(interactive=False)
    elif auth_mode == "USER_PROVIDED_MCP_ACCESS_TOKEN":
        headers = {OAUTH_TOKEN_HEADER: "Bearer " + os.environ[variable]}
    else:
        headers = {PROVIDER_KEY_HEADER: os.environ[variable]}

    opener = transport or _official_transport

    async def run():
        async with httpx2.AsyncClient(headers=headers, auth=auth,
                                      follow_redirects=False,
                                      timeout=httpx2.Timeout(65.0, connect=10.0)) as http:
            async with opener(ENDPOINT, http) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    return [_canonical_descriptor({
                        "name": getattr(t, "name", None),
                        "description": getattr(t, "description", None),
                        "input_schema": getattr(t, "input_schema", None),
                        "annotations": getattr(t, "annotations", None),
                    }) for t in listed.tools]

    return asyncio.run(run())


def probe_schema(*, runner=None, advertised=None, transport=None) -> dict:
    """Read-only handshake returning an upstream ``tools/list`` snapshot.

    Never fabricates a snapshot. Without authorization to touch the network it
    says ``AUTH_REQUIRED``; if the handshake fails it says ``UNVERIFIED`` and
    omits the hash entirely, so a failed probe cannot be mistaken for a match.
    """
    if os.environ.get(NETWORK_GATE_ENV) != "1" and advertised is None:
        return {"provider": PROVIDER, "transport": TRANSPORT,
                "status": AVAILABILITY_AUTH_REQUIRED,
                "schema_verdict": SCHEMA_UNVERIFIED,
                "reason": "NETWORK_NOT_AUTHORIZED_NO_SNAPSHOT_FABRICATED",
                "catalog_sha256": catalog_sha256()}
    try:
        if advertised is None:
            if runner is not None:
                advertised = _list_tools(runner)
            else:
                # The real path: one session, full descriptors, no tools/call.
                return _probe_result(discover_schema(
                    descriptors=_descriptors_from_session(transport)))
        if isinstance(advertised, (list, tuple)) and advertised and all(
                isinstance(item, dict) for item in advertised):
            discovery = discover_schema(descriptors=list(advertised))
        else:
            # Legacy callers pass names. That path stays supported and is
            # labelled `TOOL_NAMES_ONLY`; it is not a schema digest.
            discovery = discover_schema(names=list(advertised))
    except ValueError:
        raise
    except Exception:
        return {"provider": PROVIDER, "transport": TRANSPORT,
                "status": "PROBE_FAILED",
                "schema_verdict": SCHEMA_UNVERIFIED,
                "reason": "UPSTREAM_HANDSHAKE_FAILED",
                "catalog_sha256": catalog_sha256()}
    if advertised is None:
        return {"provider": PROVIDER, "transport": TRANSPORT,
                "status": "PROBE_FAILED",
                "schema_verdict": SCHEMA_UNVERIFIED,
                "reason": "UPSTREAM_RETURNED_NO_TOOL_LIST",
                "catalog_sha256": catalog_sha256()}
    return _probe_result(discovery)


def _probe_result(discovery: dict) -> dict:
    """`probe_schema`'s historical field names, over the descriptor discovery."""
    drift = discovery["drift"]
    changed = bool(drift["tool_added"] or drift["tool_removed"]
                   or drift["malformed"] or drift["schema_changed"])
    return {
        "provider": discovery["provider"],
        "transport": discovery["transport"],
        "endpoint": discovery["endpoint"],
        "status": "OK",
        "schema_verdict": SCHEMA_DRIFT if changed else SCHEMA_MATCH,
        # `schema_sha256` is now the descriptor digest. When only names were
        # available it says so through `digest_subject`/`reduced` rather than
        # quietly hashing a name list and calling it a schema.
        "schema_sha256": discovery["digest_sha256"],
        "digest_sha256": discovery["digest_sha256"],
        "digest_subject": discovery["digest_subject"],
        "schema_version": discovery["schema_version"],
        "reduced": discovery["reduced"],
        "advertised_count": discovery["raw_tool_count"],
        "raw_tool_count": discovery["raw_tool_count"],
        "capture": discovery["capture"],
        "promotion": discovery["promotion"],
        "vendor_schema_sha256": discovery["vendor_schema_sha256"],
        "schema_verified": discovery["schema_verified"],
        "tools": discovery["tools"],
        "tool_names": discovery["tool_names"],
        "drift": drift,
        "catalog_sha256": discovery["catalog_sha256"],
    }


async def _list_tools(runner) -> list:
    """Upstream tool names only. Returns None when the runner cannot supply it."""
    names = await runner("__list_tools__", {})
    return names if isinstance(names, list) else None


def _caveats(tool: str, entry, *, extra: tuple = ()) -> tuple:
    out = ["BUSINESS_EVIDENCE_NOT_VERIFIED",
           "AUTH_WIRE_UNVERIFIED", "PROVIDER_DESCRIPTIONS_ARE_UNTRUSTED"]
    if entry[5]["may_charge"]:
        out += ["PROVIDER_HARD_CAP_NOT_PUBLISHED", CEILING_ENFORCED_BY,
                AUTHORIZATION_ISOLATION]
    if tool == "run_web_automation":
        out.append("SESSION_ID_MINTED_LOCALLY_NOT_CALLER_SUPPLIED")
    return tuple(out) + tuple(extra)


class ResultView(dict):
    """One AdapterResult, readable by attribute as well as by key.

    `research_gateway.py` on the integration branch reads the dispatch result by
    attribute (`getattr(result, "caveats", ())`) while the offline tests read it
    by key. `research_contract.AdapterResult` — the type both sides should share
    — belongs to W-B and is not on this branch yet, so this view satisfies both
    shapes without defining a competing AdapterResult. When the real type lands,
    `execute()` returns that and this class goes away.
    """

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _blocked_result(capability_id, reason_code, *, tool=None, entry=None) -> ResultView:
    # The gateway's `_result_refusal_code` will not translate a BLOCKED result
    # that carries no §5.3 member in `caveats`; it raises ContractViolation
    # rather than guessing a plausible code. So the code goes in as the first
    # caveat, and this adapter emits no code outside that closed set.
    result = ResultView({
        "outcome": OUTCOME_BLOCKED,
        "payload": None,
        "artifacts": (),
        "upstream_ref": None,
        "actual_usd": None,
        "actual_source": ACTUAL_NONE,
        "quota_units_used": None,
        "transport": TRANSPORT,
        "schema_verdict": SCHEMA_MATCH if entry is not None else SCHEMA_UNVERIFIED,
        "caveats": (reason_code,)
                   + (_caveats(tool, entry) if entry is not None else ()),
    })
    result.update({
        "capability_id": capability_id,
        "reason_code": reason_code,
        "charge_state": CHARGE_NO_CHARGE_PROVEN,
        # A refusal is only NO_CHARGE_PROVEN because nothing was described to
        # the vendor. The flag makes that the reason, not an assertion.
        "dispatched": False,
        "retry_permitted": True,
        "business_evidence_verified": False,
    })
    return result


def as_adapter_result(receipt: dict, *, capability_id: str, entry,
                      upstream_ref=None) -> ResultView:
    """Project a dispatch receipt onto the §3.4 AdapterResult shape."""
    ok = bool(receipt.get("ok"))
    error = receipt.get("error")
    if ok:
        outcome = OUTCOME_OK
        reason = None
    elif error in _RECEIPT_FAILED_CODES:
        outcome = OUTCOME_FAILED
        reason = REASON_CAPABILITY_NOT_AVAILABLE
    else:
        outcome = OUTCOME_UNKNOWN
        reason = None
    result = ResultView({
        "outcome": outcome,
        "payload": receipt.get("tool_result"),
        "artifacts": (),
        "upstream_ref": (upstream_ref if upstream_ref is not None
                         else receipt.get("agent_run_id")),
        # §3.4 keeps the estimate, the reservation and the verified actual
        # separate. This adapter can produce none of them, so it says so with
        # NONE rather than a zero that would read as "free".
        "actual_usd": None,
        "actual_source": ACTUAL_NONE,
        "quota_units_used": None,
        # §6 fixes one transport per provider. The receipt keeps the verbose
        # wire label ("OFFICIAL_STREAMABLE_HTTP_MCP") for humans; the contract
        # field is the fixed token.
        "transport": TRANSPORT,
        "schema_verdict": SCHEMA_MATCH,
        "caveats": (_caveats(receipt.get("mcp_tool", ""), entry)
                    + tuple(receipt.get("caveats") or ())),
        "dispatched": bool(receipt.get("dispatched")),
    })
    result.update({
        "capability_id": capability_id,
        "reason_code": reason,
        "charge_state": receipt.get("charge_state", CHARGE_UNKNOWN),
        "retry_permitted": bool(receipt.get("retry_permitted")),
        "business_evidence_verified": False,
    })
    return result


def _request_info(req, tool: str) -> dict:
    for field in _REQUEST_FIELDS:
        if not hasattr(req, field):
            raise ValueError("TINYFISH_CAPABILITY_REQUEST_INVALID")
    if not isinstance(req.capability_id, str) or not req.capability_id:
        raise ValueError("TINYFISH_CAPABILITY_REQUEST_INVALID")
    if type(req.params) is not dict:
        raise ValueError("TINYFISH_CAPABILITY_REQUEST_INVALID")
    for field in ("run_id", "operation_id", "account_ref", "intent_fingerprint"):
        value = getattr(req, field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("TINYFISH_CAPABILITY_REQUEST_INVALID")
    if req.grant_ref is not None and not isinstance(req.grant_ref, str):
        raise ValueError("TINYFISH_CAPABILITY_REQUEST_INVALID")
    return {"capability_id": req.capability_id, "params": dict(req.params),
            "grant_ref": req.grant_ref, "tool": tool}


def execute(req, *, runner=None, advertised_tools=None, environ=None,
            transport=None) -> dict:
    """Run one already-authorized capability exactly once (§6, IA-2/IA-3)."""
    if not hasattr(req, "capability_id"):
        raise ValueError("TINYFISH_CAPABILITY_REQUEST_INVALID")
    tool = _tool_of(req.capability_id)
    entry = _TOOL_INDEX.get(tool) if tool else None

    if tool is None:
        raise ValueError("TINYFISH_CAPABILITY_REQUEST_INVALID")
    info = _request_info(req, tool)
    capability_id = req.capability_id

    if tool not in _TOOL_INDEX:
        return _blocked_result(capability_id, REASON_CAPABILITY_UNKNOWN)
    entry = _TOOL_INDEX[tool]
    if not entry[3]:
        return _blocked_result(capability_id, REASON_CAPABILITY_NOT_AVAILABLE,
                               tool=tool, entry=entry)
    if entry[1] in (T2, T3, T4) and not info["grant_ref"]:
        # §3.1: grant_ref is optional only for T0/T1. Requiring it here is not
        # a policy decision — it is the adapter demanding evidence that Policy
        # already ran, which is exactly what "receives an authorized request"
        # means when no shared type exists to prove it.
        return _blocked_result(capability_id, REASON_POLICY_TIER_NOT_GRANTED,
                               tool=tool, entry=entry)
    if advertised_tools is not None:
        verdict = verify_schema(advertised_tools).get(capability_id)
        if verdict != SCHEMA_MATCH:
            return _blocked_result(capability_id, REASON_CAPABILITY_SCHEMA_DRIFT,
                                   tool=tool, entry=entry)

    try:
        validate(tool, info["params"])
    except ValueError as exc:
        code = str(exc)
        # "We will not send this target" is not "you passed a bad parameter":
        # the operator acts differently on each, so the egress refusal keeps its
        # own closed-set code even though it surfaced during validation.
        if code.split(":", 1)[0].strip() in _EGRESS_REFUSAL_CODES:
            return _blocked_result(capability_id, REASON_EGRESS_REFUSED,
                                   tool=tool, entry=entry)
        return _blocked_result(capability_id,
                               code if code in _OWN_REFUSAL_CODES
                               else REASON_CAPABILITY_PARAM_NOT_ALLOWED,
                               tool=tool, entry=entry)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise ValueError("OFFICIAL_MCP_SYNC_CALL_INSIDE_RUNNING_LOOP")

    plan = {"runner": runner, "tool": tool, "arguments": info["params"],
            "ceiling": None, "auth_mode": "", "drift": None,
            "transport": transport}
    try:
        receipt = asyncio.run(_run(plan))
    except ValueError as exc:
        code = str(exc)
        if code.split(":", 1)[0].strip() in _SCHEMA_DRIFT_CODES:
            return _blocked_result(capability_id, REASON_CAPABILITY_SCHEMA_DRIFT,
                                   tool=tool, entry=entry)
        if code.split(":", 1)[0].strip() in _EGRESS_REFUSAL_CODES:
            return _blocked_result(capability_id, REASON_EGRESS_REFUSED,
                                   tool=tool, entry=entry)
        if code in _WALLET_PRECONDITION_CODES:
            return _blocked_result(capability_id,
                                   REASON_WALLET_PRECONDITION_FAILED,
                                   tool=tool, entry=entry)
        if code == "TINYFISH_AGENT_SPEND_CAP_EXCEEDED":
            return _blocked_result(capability_id, REASON_BUDGET_OPERATION_CEILING,
                                   tool=tool, entry=entry)
        return _blocked_result(capability_id, REASON_CAPABILITY_NOT_AVAILABLE,
                               tool=tool, entry=entry)
    requested_ref = info["params"].get("id")
    if requested_ref is None:
        ids = info["params"].get("run_ids")
        requested_ref = ids[0] if isinstance(ids, list) and ids else None
    return as_adapter_result(receipt, capability_id=capability_id, entry=entry,
                             upstream_ref=requested_ref if tool in CANCEL_TOOLS
                             else None)


def cancel(upstream_ref, *, grant_ref=None, runner=None,
           advertised_tools=None) -> dict:
    """Idempotent cancellation by the provider's own run handle (§6)."""
    if not isinstance(upstream_ref, str) or not _RUN_ID.match(upstream_ref):
        return _blocked_result("tinyfish.cancel_run", REASON_CAPABILITY_PARAM_NOT_ALLOWED,
                               tool="cancel_run", entry=_TOOL_INDEX["cancel_run"])

    class _CancelRequest:
        capability_id = "tinyfish.cancel_run"
        params = {"id": upstream_ref}
        run_id = operation_id = account_ref = intent_fingerprint = "cancel"
        grant_ref = None

    request = _CancelRequest()
    request.grant_ref = grant_ref
    return execute(request, runner=runner, advertised_tools=advertised_tools)


class TinyFishAdapter:
    """§6 `ProviderAdapter` as an instance, which is what a gateway looks up.

    The module-level functions carry the semantics; this object is the wiring
    shape. `runner` and `advertised_tools` are injection seams for the offline
    tests and for a session that already holds a tool list — production passes
    neither and the adapter opens its own session.
    """

    provider = PROVIDER

    def __init__(self, *, runner=None, advertised_tools=None,
                 transport=None) -> None:
        self._runner = runner
        self._advertised = advertised_tools
        self._transport = transport

    def capabilities(self) -> tuple:
        return capabilities()

    def probe_schema(self) -> dict:
        return probe_schema(runner=self._runner, advertised=self._advertised,
                            transport=self._transport)

    def execute(self, req) -> ResultView:
        return execute(req, runner=self._runner,
                       advertised_tools=self._advertised,
                       transport=self._transport)

    def cancel(self, upstream_ref: str, *, grant_ref=None) -> ResultView:
        return cancel(upstream_ref, grant_ref=grant_ref, runner=self._runner,
                      advertised_tools=self._advertised)

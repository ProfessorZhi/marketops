#!/usr/bin/env python3
"""S0 -> S1 readiness gate for the first no-/low-cost official provider call.

Owner: independent readiness reviewer (Wave-3). This module is a NEW, previously
unclaimed file; it is not shared policy/ledger/QA production source.

What it does
------------
`evaluate(evidence)` is a PURE function over an injected evidence mapping. It
decides whether the campaign may attempt its FIRST official provider call, and
returns a closed-set verdict plus the exact blocking condition.

It is deliberately offline: no network, no vendor SDK, no credential access.

Vocabulary (do not blur these)
------------------------------
* S1 verdicts reuse the authoritative closed set from
  `docs/wave1-coordination/BUDGET-FLOOR.md`: `AUTHORIZED` / `BLOCKED` /
  `NOT_CAPTURED`. Top-level verdict is the worst across providers, priority
  `BLOCKED > NOT_CAPTURED > AUTHORIZED`.
* `reason_code` values are drawn ONLY from the frozen §5.3 closed set in
  `INTERFACE-CONTRACT.md`. Inventing a new reason code is a contract change and
  is NOT done here.
* `S0_*` gate ids are LOCAL to this reviewer artifact. They are NOT §5.3 reason
  codes and must not be passed off as ones; promoting one would require the §8
  contract-change process.
* Readiness tags are the §1 ladder (`DOCUMENTED` .. `CLIENT_VERIFIED`). This
  gate never awards a tag; it only refuses to let a lower tag masquerade as a
  higher one.

Credential discipline
---------------------
The gate refuses to evaluate evidence that carries credential material. Any
field whose *name* looks secret-bearing makes `evaluate` return `BLOCKED` with
a note, so no credential value can reach a transcript, log or commit through
this path. Only presence booleans and sourced measurements are admissible.

S0_07 is a capture, not a claim
-------------------------------
`upstream_schema_verified` alone is not evidence. Each entry in
`upstream_schema_snapshots` must be a versioned record of a real read-only
`tools/list` capture: provider, allowlisted endpoint, method, trusted source,
capture time inside the freshness window, the restricted egress decision ref,
the audit ref, a positive tool count, and the canonical serialized payload whose
sha256 must equal both the declared `schema_sha256` and the Registry's
`registry_schema_sha256`. Missing, boolean-only, mismatched, stale, untrusted or
non-canonical snapshots are refused when `evaluate` runs — before any gateway or
adapter dispatch.

Pre-S0 discovery exception
--------------------------
`bootstrap_probe_schema()` is the one outbound path allowed before S0 passes.
It is discovery, not use: there is no way to express `tools/call`, and no way
to express a ledger reservation. It must go through the same restricted egress
proxy as the adapters (`egress_decision`), must acknowledge an audit event
before and after the probe (`audit`), and returns the snapshot that S0_07 then
re-validates. Completing discovery never by itself passes S0: S0_02 (independent
QA PASSED at the same SHA) still has to hold.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

# --- closed sets -----------------------------------------------------------

#: S1 verdicts (BUDGET-FLOOR.md §3).
VERDICTS = ("AUTHORIZED", "BLOCKED", "NOT_CAPTURED")
_WORST_FIRST = ("BLOCKED", "NOT_CAPTURED", "AUTHORIZED")

#: §5.3 frozen reason codes; this module may only emit members of this set.
REASON_CODES = frozenset({
    "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
    "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
    "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
    "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
    "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING",
    "RESEARCH_NETWORK_DISABLED", "WALLET_PRECONDITION_FAILED", "GRANT_SIGNATURE_INVALID",
})

#: S0 structural gate ids — LOCAL to this artifact, NOT §5.3 codes.
S0_GATES = (
    "S0_01_INTEGRATION_SHA_PINNED",
    "S0_02_INDEPENDENT_QA_PASSED",
    "S0_03_ONE_EFFECTIVE_MANAGED_MCP",
    "S0_04_NO_DIRECT_VENDOR_BYPASS",
    "S0_05_RUNTIME_SCOPED_CREDENTIAL_INJECTION",
    "S0_06_ENDPOINT_ALLOWLIST",
    "S0_07_UPSTREAM_SCHEMA_VERIFIED",
)

#: Provider units the floor arithmetic understands.
_USD_UNITS = frozenset({"USD", "usd", "US$", "$"})

#: Price sources that positively attest a ZERO unit price. A zero estimate is
#: only "free" when the source says so; otherwise a zero is an unknown cost that
#: happens to be written as 0, which is how "unknown-cost masquerading as free"
#: gets through a gate.
ZERO_PRICE_SOURCES = frozenset({
    "meter_zero_verified",     # provider meter read back and shown to be 0
    "local_no_charge",         # declared local/no-vendor path
})
_SECRET_NAME = re.compile(
    r"(api[_-]?key|apikey|token|secret|password|passwd|credential|bearer|"
    r"authorization|private[_-]?key|client[_-]?secret|wallet[_-]?id|"
    r"cookie|session[_-]?id)", re.I)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

#: Version of the snapshot evidence shape this gate understands. An unknown
#: version is refused, not guessed: a future shape may mean something else.
SNAPSHOT_VERSION = 1
SUPPORTED_SNAPSHOT_VERSIONS = frozenset({SNAPSHOT_VERSION})

#: The only capture path that can produce an S0_07 snapshot. The contract calls
#: this `ProviderAdapter.probe_schema()`: a read-only handshake that never bills.
TRUSTED_SNAPSHOT_SOURCES = frozenset({"probe_schema@official_mcp"})

#: A snapshot older than this is stale even if its hash is right: the upstream
#: tool surface can change without the registry being re-pinned.
MAX_SNAPSHOT_AGE_SECONDS = 7 * 24 * 60 * 60

#: Refuse an absurd payload rather than hashing an unbounded string.
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024

#: The pre-S0 discovery exception is *only* these JSON-RPC methods. `tools/call`
#: is deliberately absent: discovery is not use.
BOOTSTRAP_ALLOWED_METHODS = frozenset({
    "initialize", "notifications/initialized", "tools/list",
})


def canonical_snapshot(payload: Any) -> str:
    """The exact bytes a snapshot digest is computed over.

    Canonical JSON (sorted keys, no whitespace) so the same payload always hashes
    the same way. A non-canonical serialization is refused by the validator
    instead of quietly producing a second digest for the same content.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def snapshot_digest(payload: Any) -> str:
    """sha256 over the canonical serialization, as 64 lowercase hex."""
    return hashlib.sha256(canonical_snapshot(payload).encode("utf-8")).hexdigest()


def _parse_iso(value: Any) -> datetime | None:
    """An ISO timestamp that carries a timezone, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def _host_of(url: str) -> str:
    body = str(url).split("//", 1)[-1]
    return body.split("/", 1)[0].split(":", 1)[0].casefold().strip(".")


def validate_snapshot_entry(name: str, entry: Any, *, hosts: Any,
                            as_of: datetime | None) -> list[str]:
    """Everything wrong with one snapshot entry. Empty list means admissible.

    This is the S0_07 core: a boolean is not evidence. The entry must be a
    *versioned capture of a real payload* whose digest the gate recomputes, taken
    from an allowlisted provider endpoint through the same restricted egress
    path, recently enough to be about the upstream that exists now.
    """
    if not isinstance(entry, Mapping):
        return ["SNAPSHOT_NOT_AN_OBJECT"]

    problems: list[str] = []
    version = entry.get("version")
    if type(version) is not int or version not in SUPPORTED_SNAPSHOT_VERSIONS:
        problems.append("SNAPSHOT_VERSION_UNSUPPORTED")
    if str(entry.get("provider", "")) != str(name):
        problems.append("SNAPSHOT_PROVIDER_MISMATCH")

    endpoint = str(entry.get("endpoint", "")).strip()
    if not endpoint:
        problems.append("SNAPSHOT_ENDPOINT_MISSING")
    elif _host_of(endpoint) not in set(hosts or ()):
        problems.append("SNAPSHOT_ENDPOINT_NOT_ALLOWED")

    if str(entry.get("method", "")) != "tools/list":
        problems.append("SNAPSHOT_METHOD_NOT_TOOLS_LIST")
    if str(entry.get("source", "")) not in TRUSTED_SNAPSHOT_SOURCES:
        problems.append("SNAPSHOT_SOURCE_UNTRUSTED")

    captured = _parse_iso(entry.get("captured_at"))
    if captured is None:
        problems.append("SNAPSHOT_CAPTURED_AT_INVALID")
    elif as_of is None:
        problems.append("SNAPSHOT_AS_OF_INVALID")
    else:
        age = (as_of - captured).total_seconds()
        if age < 0:
            problems.append("SNAPSHOT_CAPTURED_IN_FUTURE")
        elif age > MAX_SNAPSHOT_AGE_SECONDS:
            problems.append("SNAPSHOT_STALE")

    if not str(entry.get("egress_decision_ref", "")).strip():
        problems.append("SNAPSHOT_EGRESS_REF_MISSING")
    if not str(entry.get("audit_ref", "")).strip():
        problems.append("SNAPSHOT_AUDIT_REF_MISSING")

    serialized = entry.get("snapshot_serialized")
    if not isinstance(serialized, str) or not serialized:
        problems.append("SNAPSHOT_SERIALIZED_MISSING")
    elif len(serialized.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        problems.append("SNAPSHOT_SERIALIZED_TOO_LARGE")
    else:
        declared = str(entry.get("schema_sha256", "")).lower()
        pinned = str(entry.get("registry_schema_sha256", "")).lower()
        computed = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if not _SHA256.match(declared) or computed != declared:
            problems.append("SNAPSHOT_HASH_MISMATCH")
        if not _SHA256.match(pinned) or pinned != declared:
            problems.append("SNAPSHOT_REGISTRY_DIGEST_MISMATCH")
        try:
            parsed = json.loads(serialized)
        except (ValueError, TypeError):
            problems.append("SNAPSHOT_SERIALIZED_NOT_JSON")
        else:
            if canonical_snapshot(parsed) != serialized:
                problems.append("SNAPSHOT_NOT_CANONICAL")
            tools = parsed.get("tools") if isinstance(parsed, Mapping) else None
            if not isinstance(tools, list):
                problems.append("SNAPSHOT_SERIALIZED_NOT_TOOLS_LIST")
            elif type(entry.get("tool_count")) is int and len(tools) != entry["tool_count"]:
                problems.append("SNAPSHOT_TOOL_COUNT_MISMATCH")

    tool_count = entry.get("tool_count")
    if type(tool_count) is not int or tool_count <= 0:
        problems.append("SNAPSHOT_TOOL_COUNT_INVALID")
    return problems


def _snapshots_verified(snapshots: Any, *, hosts: Any,
                        as_of: datetime | None) -> bool:
    """Non-empty, every entry admissible. 'Nothing fetched' is not 'verified'."""
    if not isinstance(snapshots, Mapping) or not snapshots:
        return False
    return all(not validate_snapshot_entry(name, entry, hosts=hosts, as_of=as_of)
               for name, entry in snapshots.items())


# --- pre-S0 discovery exception --------------------------------------------

class BootstrapRefused(Exception):
    """The pre-S0 discovery exception refused this action. Never a paid call."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def bootstrap_method_allowed(method: Any) -> bool:
    """The wire-level guard for the discovery exception.

    The integration side must call this in its transport before sending a
    frame. `tools/call` is not in the set, so discovery cannot become use.
    """
    return str(method) in BOOTSTRAP_ALLOWED_METHODS


def bootstrap_probe_schema(*, probe: Any, provider: str, endpoint: str,
                           allowlist: Any, egress_decision: Any, audit: Any,
                           registry_schema_sha256: str,
                           clock: Any = None, captured_at: str | None = None,
                           version: int = SNAPSHOT_VERSION) -> dict:
    """The ONLY outbound path allowed before S0 passes: read-only discovery.

    Narrower than the audit's B-1..B-5 by construction, not by convention:

    * there is no `method` parameter, so `tools/call` cannot be expressed;
    * there is no ledger/reserve parameter, so a paid reservation cannot be
      expressed either — discovery is not a purchase;
    * `egress_decision(endpoint)` must return the restricted proxy's decision
      ref (the same EgressProxy the adapters use), so this cannot become a
      direct-connect bypass;
    * `audit(record)` must acknowledge *before* and *after* the call; the refs
      are carried into the snapshot and re-checked by S0_07;
    * the returned entry is the only shape S0_07 accepts.

    `probe` is the bound read-only handshake (contract: `probe_schema`). It is
    called exactly once, only after the egress decision and the intent audit.
    """
    if not _ID.match(str(provider)):
        raise BootstrapRefused("BOOTSTRAP_PROVIDER_INVALID",
                               "provider must be a short opaque identifier")
    if endpoint and _host_of(endpoint) not in set(allowlist or ()):
        raise BootstrapRefused("BOOTSTRAP_ENDPOINT_NOT_ALLOWED",
                               f"{_host_of(endpoint)!r} is not on the egress allowlist")
    if not endpoint:
        raise BootstrapRefused("BOOTSTRAP_ENDPOINT_MISSING", "endpoint is required")
    if not callable(egress_decision):
        raise BootstrapRefused("BOOTSTRAP_EGRESS_DECISION_REQUIRED",
                               "the restricted EgressProxy decision is required")
    decision_ref = egress_decision(endpoint)
    if not isinstance(decision_ref, str) or not decision_ref.strip():
        raise BootstrapRefused("BOOTSTRAP_EGRESS_DECISION_UNACKNOWLEDGED",
                               "the egress proxy did not return a decision ref")
    if not callable(audit):
        raise BootstrapRefused("BOOTSTRAP_AUDIT_REQUIRED",
                               "an audit sink is required; discovery leaves a trace")
    if not callable(probe):
        raise BootstrapRefused("BOOTSTRAP_PROBE_REQUIRED",
                               "a read-only probe callable is required")

    intent = {"event": "s0.bootstrap.probe_schema", "phase": "intent",
              "provider": provider, "endpoint": endpoint,
              "host": _host_of(endpoint), "method": "tools/list",
              "version": version, "egress_decision_ref": decision_ref}
    intent_ref = audit(intent)
    if not isinstance(intent_ref, str) or not intent_ref.strip():
        raise BootstrapRefused("BOOTSTRAP_AUDIT_UNACKNOWLEDGED",
                               "the audit sink did not return a ref")

    payload = probe()
    if isinstance(payload, Mapping) and "tools" not in payload:
        # The contract's AUTH_REQUIRED shape, and anything else that is not a
        # tools/list payload. Never fabricate a snapshot from it.
        raise BootstrapRefused("BOOTSTRAP_NO_SNAPSHOT",
                               f"probe returned no tools/list payload: {payload.get('status', 'unknown')!r}")
    if not isinstance(payload, Mapping) or not isinstance(payload.get("tools"), list):
        raise BootstrapRefused("BOOTSTRAP_NO_SNAPSHOT",
                               "probe did not return a tools/list payload")
    tool_count = len(payload["tools"])
    if tool_count <= 0:
        raise BootstrapRefused("BOOTSTRAP_EMPTY_TOOLS",
                               "an empty tools/list is not a snapshot")

    serialized = canonical_snapshot(payload)
    if len(serialized.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise BootstrapRefused("BOOTSTRAP_SNAPSHOT_TOO_LARGE",
                               "the tools/list payload exceeds the snapshot limit")
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    pinned = str(registry_schema_sha256).lower()
    if not _SHA256.match(pinned) or pinned != digest:
        raise BootstrapRefused(
            "BOOTSTRAP_SCHEMA_MISMATCH",
            f"upstream digest {digest} does not match the registry snapshot {pinned!r}")

    if captured_at is None:
        moment = clock() if callable(clock) else datetime.now(timezone.utc)
        captured_at = moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    elif _parse_iso(captured_at) is None:
        raise BootstrapRefused("BOOTSTRAP_CAPTURED_AT_INVALID",
                               "captured_at must be an ISO timestamp with a timezone")

    result_ref = audit({"event": "s0.bootstrap.probe_schema", "phase": "result",
                        "provider": provider, "endpoint": endpoint,
                        "host": _host_of(endpoint), "method": "tools/list",
                        "version": version, "tool_count": tool_count,
                        "schema_sha256": digest,
                        "egress_decision_ref": decision_ref})
    if not isinstance(result_ref, str) or not result_ref.strip():
        raise BootstrapRefused("BOOTSTRAP_AUDIT_UNACKNOWLEDGED",
                               "the audit sink did not acknowledge the result")

    return {provider: {
        "version": version,
        "provider": provider,
        "endpoint": endpoint,
        "method": "tools/list",
        "source": "probe_schema@official_mcp",
        "captured_at": captured_at,
        "egress_decision_ref": decision_ref,
        "audit_ref": f"{intent_ref}|{result_ref}",
        "tool_count": tool_count,
        "snapshot_serialized": serialized,
        "schema_sha256": digest,
        "registry_schema_sha256": pinned,
    }}


# --- evidence model --------------------------------------------------------

@dataclass(frozen=True)
class AccountEvidence:
    """One provider account/period. Money is Decimal; never a float."""
    captured: bool = False
    b0: Decimal | None = None
    unit: str = ""
    meter: str = ""
    price_source: str = ""
    last_observed_at: str = ""
    reset_at: str = ""
    drawdown_so_far: Decimal = Decimal("0")
    unknown_exposure: Decimal | None = Decimal("0")
    other_holders_spend: Decimal = Decimal("0")
    next_op_estimate: Decimal | None = None
    auto_topup: bool = False
    key_rotation_confirmed: bool = False
    schema_verified: bool = False
    #: "provider_metered" (default) or "local_no_provider_account" (e.g. the
    #: local Crawl4AI SDK, which spends no vendor account and so has no B0).
    billing_mode: str = "provider_metered"

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "AccountEvidence":
        def dec(key: str) -> Decimal | None:
            if key not in m or m[key] is None:
                return None
            try:
                d = Decimal(str(m[key]))
            except (InvalidOperation, ValueError):
                raise ValueError(f"{key}_NOT_A_DECIMAL") from None
            if not d.is_finite():
                raise ValueError(f"{key}_NOT_FINITE")
            return d

        def dec0(key: str) -> Decimal:
            v = dec(key)
            return Decimal("0") if v is None else v

        return cls(
            captured=bool(m.get("captured", False)),
            b0=dec("b0"), unit=str(m.get("unit", "")),
            meter=str(m.get("meter", "")), price_source=str(m.get("price_source", "")),
            last_observed_at=str(m.get("last_observed_at", "")),
            reset_at=str(m.get("reset_at", "")),
            drawdown_so_far=dec0("drawdown_so_far"),
            unknown_exposure=dec("unknown_exposure"),
            other_holders_spend=dec0("other_holders_spend"),
            next_op_estimate=dec("next_op_estimate"),
            auto_topup=bool(m.get("auto_topup", False)),
            key_rotation_confirmed=bool(m.get("key_rotation_confirmed", False)),
            schema_verified=bool(m.get("schema_verified", False)),
            billing_mode=str(m.get("billing_mode", "provider_metered")),
        )


#: The only account-evidence fields this gate understands. Anything else is a
#: schema problem, not something to ignore: an unrecognised `balance_usd` or
#: `account_id` is exactly how real identity or balance data would arrive.
ACCOUNT_EVIDENCE_FIELDS = frozenset({
    "captured", "b0", "unit", "meter", "price_source", "last_observed_at",
    "reset_at", "drawdown_so_far", "unknown_exposure", "other_holders_spend",
    "next_op_estimate", "auto_topup", "key_rotation_confirmed", "schema_verified",
    "billing_mode",
})

#: Fields a metered account must carry before any arithmetic is trusted.
REQUIRED_METERING_FIELDS = ("b0", "unit", "meter", "price_source",
                            "last_observed_at", "next_op_estimate")

#: Identity-shaped names that must never appear in evidence at all, even as an
#: opaque string: the gate needs a *measurement*, not an account's identity.
_IDENTITY_FIELDS = frozenset({
    "account_id", "account_ref", "wallet_id", "customer_id", "email",
    "owner", "username", "user_id", "project_id",
})


def validate_account_evidence(mapping: Mapping[str, Any]) -> list[str]:
    """Schema problems for one provider account. Empty list means well-formed.

    Reports field *names* only; never reads or echoes a value.
    """
    if not isinstance(mapping, Mapping):
        return ["EVIDENCE_NOT_A_MAPPING"]
    names = {str(k) for k in mapping}
    problems = [f"UNKNOWN_FIELD:{n}" for n in sorted(names - ACCOUNT_EVIDENCE_FIELDS)]
    problems += [f"IDENTITY_FIELD_FORBIDDEN:{n}" for n in sorted(names & _IDENTITY_FIELDS)]
    if str(mapping.get("billing_mode", "provider_metered")) == "provider_metered":
        problems += [f"MISSING_METERING_FIELD:{n}" for n in REQUIRED_METERING_FIELDS
                     if n not in mapping]
    return problems


@dataclass
class ProviderVerdict:
    provider: str
    verdict: str = "NOT_CAPTURED"
    reason_code: str | None = None
    floor: Decimal | None = None
    max_drawdown: Decimal | None = None
    post_operation_available: Decimal | None = None
    notes: list[str] = field(default_factory=list)


# --- arithmetic ------------------------------------------------------------

def floor_for(b0: Decimal, unit: str) -> Decimal:
    """`floor = 0.20 x B0`; USD wallets keep an extra absolute >= US$0.20.

    Credits that are not divisible are rounded UP (BUDGET-FLOOR.md §1), so a
    fractional credit floor can never be met by rounding down.
    """
    proportional = b0 * Decimal("0.20")
    if unit in _USD_UNITS:
        return max(proportional, Decimal("0.20"))
    return proportional.to_integral_value(rounding="ROUND_CEILING")


def max_drawdown_for(b0: Decimal) -> Decimal:
    """`0.80 x B0` is an UPPER BOUND, not a spending target."""
    return b0 * Decimal("0.80")


def money_str(value: Decimal | None, unit: str) -> str | None:
    """Canonical string for a money/quota figure.

    Decimal keeps the scale of its operands, so `2.00 * 0.20` renders as
    "0.4000". Reporting that verbatim makes downstream equality checks flaky,
    so normalise: USD-style units keep exactly two decimals, everything else
    drops trailing fractional zeros.
    """
    if value is None:
        return None
    if unit in _USD_UNITS:
        return format(value.quantize(Decimal("0.01")), "f")
    normalised = value.normalize()
    if normalised == normalised.to_integral_value():
        normalised = normalised.quantize(Decimal("1"))
    return format(normalised, "f")


# --- guards ----------------------------------------------------------------

def _could_hold_a_value(value: Any) -> bool:
    """True when a scalar could literally carry a secret.

    Presence booleans are explicitly admissible (the brief allows `set/not_set`
    only), and a mapping/list is not itself a value — it is recursed into.
    """
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return isinstance(value, (int, float))


def _secret_material_paths(node: Any, path: str = "") -> list[str]:
    """Return dotted paths whose KEY names look secret-bearing AND hold a value.

    Structural descriptors such as ``credential_injection`` (a mapping that
    says *how* a credential arrives) are not themselves secrets, so only
    value-bearing occurrences are refused.
    """
    hits: list[str] = []
    if isinstance(node, Mapping):
        for k, v in node.items():
            here = f"{path}.{k}" if path else str(k)
            if _SECRET_NAME.search(str(k)) and _could_hold_a_value(v):
                hits.append(here)
            hits.extend(_secret_material_paths(v, here))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            hits.extend(_secret_material_paths(v, f"{path}[{i}]"))
    return hits


def _s0(evidence: Mapping[str, Any]) -> tuple[dict[str, bool], list[str]]:
    """Evaluate the seven structural gates. Returns (gate map, failed ids)."""
    integ = evidence.get("integration") or {}
    qa = evidence.get("independent_qa") or {}
    mcp = evidence.get("managed_mcp") or {}
    byp = evidence.get("direct_vendor_bypass") or {}
    cred = evidence.get("credential_injection") or {}
    allow = evidence.get("endpoint_allowlist") or {}

    sha = str(integ.get("sha", "")).lower()
    endpoints = [str(e) for e in (mcp.get("endpoints") or [])]
    hosts = {str(h).casefold().strip(".") for h in (allow.get("hosts") or [])}
    as_of = _parse_iso(evidence.get("as_of"))

    def host_of(url: str) -> str:
        return _host_of(url)

    gates = {
        "S0_01_INTEGRATION_SHA_PINNED":
            bool(_SHA40.match(sha)) and sha == str(integ.get("expected_sha", "")).lower(),
        "S0_02_INDEPENDENT_QA_PASSED":
            str(qa.get("status", "")).upper() == "PASSED"
            and str(qa.get("sha", "")).lower() == sha and bool(sha),
        "S0_03_ONE_EFFECTIVE_MANAGED_MCP":
            int(mcp.get("configured_count", 0)) == 1
            and bool(mcp.get("effective")) and len(endpoints) >= 1,
        "S0_04_NO_DIRECT_VENDOR_BYPASS":
            not bool(byp.get("present", False)) and not bool(mcp.get("vendor_direct_entries", [])),
        "S0_05_RUNTIME_SCOPED_CREDENTIAL_INJECTION":
            bool(cred.get("runtime_scoped", False))
            and str(cred.get("source", "")) in {"process_env", "os_keyring", "host_injection"},
        "S0_06_ENDPOINT_ALLOWLIST":
            bool(hosts) and all(host_of(u) in hosts for u in endpoints) and bool(endpoints),
        "S0_07_UPSTREAM_SCHEMA_VERIFIED":
            bool(evidence.get("upstream_schema_verified", False))
            and _snapshots_verified(evidence.get("upstream_schema_snapshots"),
                                    hosts=hosts, as_of=as_of),
    }
    return gates, [g for g in S0_GATES if not gates[g]]


def _provider_verdict(name: str, acct: AccountEvidence, s0_ok: bool) -> ProviderVerdict:
    """Decide one provider. Order is deliberate: unreconcilable first."""
    v = ProviderVerdict(provider=name)

    if not s0_ok:
        v.verdict, v.reason_code = "BLOCKED", "CAPABILITY_NOT_AVAILABLE"
        v.notes.append("S0 structural gate not passed")
        return v

    if acct.billing_mode == "local_no_provider_account":
        # Local compute spends no vendor account, so S1 has nothing to gate.
        # It still had to pass S0, and it still produces NO business evidence.
        v.verdict = "AUTHORIZED"
        v.notes.append("local compute; no provider account, no B0, no wire cost")
        return v

    if acct.billing_mode != "provider_metered":
        v.verdict, v.reason_code = "BLOCKED", "CAPABILITY_NOT_AVAILABLE"
        v.notes.append(f"unknown billing_mode {acct.billing_mode!r}")
        return v

    if not acct.schema_verified:
        v.verdict, v.reason_code = "BLOCKED", "CAPABILITY_SCHEMA_DRIFT"
        v.notes.append("upstream schema not verified; tool/param names unproven")
        return v

    if not acct.key_rotation_confirmed:
        # A key that once appeared in chat must be rotated/revoked first.
        v.verdict, v.reason_code = "NOT_CAPTURED", "WALLET_PRECONDITION_FAILED"
        v.notes.append("key rotation unconfirmed")
        return v

    if acct.auto_topup:
        v.verdict, v.reason_code = "BLOCKED", "WALLET_PRECONDITION_FAILED"
        v.notes.append("auto top-up/reload must stay disabled")
        return v

    if not acct.captured or acct.b0 is None:
        v.verdict, v.reason_code = "NOT_CAPTURED", "WALLET_PRECONDITION_FAILED"
        v.notes.append("B0 not captured from the vendor account (env/mock/doc do not count)")
        return v

    if acct.b0 <= 0:
        v.verdict, v.reason_code = "BLOCKED", "WALLET_PRECONDITION_FAILED"
        v.notes.append("non-positive B0")
        return v

    if acct.unknown_exposure is None:
        v.verdict, v.reason_code = "BLOCKED", "UNKNOWN_CHARGE_OUTSTANDING"
        v.notes.append("unreconciled exposure not stated; treat as unknown, not zero")
        return v

    if not acct.price_source:
        v.verdict, v.reason_code = "NOT_CAPTURED", "PRICING_SOURCE_MISSING"
        v.notes.append("no price snapshot; wire cost cannot be asserted")
        return v

    if acct.next_op_estimate is None or acct.next_op_estimate < 0:
        v.verdict, v.reason_code = "NOT_CAPTURED", "PRICE_UNKNOWN_FAIL_CLOSED"
        v.notes.append("this operation's cost in the provider unit is unknown")
        return v

    if acct.next_op_estimate == 0 and acct.price_source not in ZERO_PRICE_SOURCES:
        # A zero estimate without a zero-attesting source is an unknown cost
        # written as 0. Refuse rather than let it read as free.
        v.verdict, v.reason_code = "NOT_CAPTURED", "PRICE_UNKNOWN_FAIL_CLOSED"
        v.notes.append("zero estimate without a zero-attesting price source")
        return v

    floor = floor_for(acct.b0, acct.unit)
    drawdown = max_drawdown_for(acct.b0)
    spent = acct.drawdown_so_far + acct.unknown_exposure + acct.other_holders_spend
    post = acct.b0 - spent - acct.next_op_estimate

    v.floor, v.max_drawdown, v.post_operation_available = floor, drawdown, post

    if spent + acct.next_op_estimate > drawdown:
        v.verdict, v.reason_code = "BLOCKED", "BUDGET_PROVIDER_CEILING"
        v.notes.append("would exceed the 0.80 x B0 upper bound")
        return v

    if post < floor:
        v.verdict, v.reason_code = "BLOCKED", "WALLET_PRECONDITION_FAILED"
        v.notes.append("post-operation balance would fall below the floor")
        return v

    v.verdict = "AUTHORIZED"
    v.notes.append("post-operation balance keeps the floor")
    return v


def evaluate(evidence: Mapping[str, Any]) -> dict:
    """Decide whether the FIRST official call may be attempted.

    Pure and offline. Returns verdicts plus the exact blocking conditions.
    """
    if not isinstance(evidence, Mapping):
        raise ValueError("EVIDENCE_MUST_BE_A_MAPPING")

    secret_paths = _secret_material_paths(evidence)
    if secret_paths:
        # Refuse rather than echo: a credential value must never travel through
        # this gate into a transcript, log or commit. Paths, never values.
        return {
            "s0": {"passed": False, "gates": {}, "failed": list(S0_GATES)},
            "s1": {"verdict": "BLOCKED", "reason_code": "GRANT_SIGNATURE_INVALID",
                   "providers": {}},
            "launch": "DENY",
            "notes": ["evidence carries secret-bearing field names; refusing to evaluate",
                      f"offending_field_paths={sorted(secret_paths)}"],
        }

    if str(evidence.get("network_enabled", "")) == "disabled":
        return {
            "s0": {"passed": False, "gates": {}, "failed": list(S0_GATES)},
            "s1": {"verdict": "BLOCKED", "reason_code": "RESEARCH_NETWORK_DISABLED",
                   "providers": {}},
            "launch": "DENY",
            "notes": ["project/process policy has network disabled"],
        }

    gates, failed = _s0(evidence)
    s0_ok = not failed

    providers = evidence.get("providers") or {}
    verdicts: dict[str, dict] = {}
    for name, raw in providers.items():
        try:
            acct = AccountEvidence.from_mapping(raw or {})
        except ValueError as exc:
            verdicts[name] = {"verdict": "BLOCKED", "reason_code": "PRICE_UNKNOWN_FAIL_CLOSED",
                              "notes": [str(exc)]}
            continue
        pv = _provider_verdict(name, acct, s0_ok)
        verdicts[name] = {
            "verdict": pv.verdict, "reason_code": pv.reason_code,
            "floor": money_str(pv.floor, acct.unit),
            "max_drawdown": money_str(pv.max_drawdown, acct.unit),
            "post_operation_available": money_str(pv.post_operation_available, acct.unit),
            "notes": pv.notes,
        }

    if not verdicts:
        top, reason = "NOT_CAPTURED", "WALLET_PRECONDITION_FAILED"
        verdicts["_campaign"] = {"verdict": top, "reason_code": reason,
                                 "notes": ["no provider evidence supplied"]}
    else:
        top = next(v for v in _WORST_FIRST
                   if any(x["verdict"] == v for x in verdicts.values()))
        reason = next((x.get("reason_code") for x in verdicts.values()
                       if x["verdict"] == top), None)

    if not s0_ok:
        top, reason = "BLOCKED", "CAPABILITY_NOT_AVAILABLE"

    launch = "ALLOW" if (s0_ok and top == "AUTHORIZED") else "DENY"
    return {
        "s0": {"passed": s0_ok, "gates": gates, "failed": failed},
        "s1": {"verdict": top, "reason_code": reason, "providers": verdicts},
        "launch": launch,
        "notes": [
            "S0 gate ids are local to this reviewer artifact and are not §5.3 codes",
            "env vars, mocks, doc prices and credential-file existence are not B0 evidence",
        ],
    }


def exit_code(result: Mapping[str, Any]) -> int:
    """0 only when the first call is allowed — mirrors BUDGET-FLOOR.md §3."""
    return 0 if result.get("launch") == "ALLOW" else 2


@dataclass(frozen=True)
class GuardedOutcome:
    """What the product entrypoint gets back.

    `gateway_invoked` is the load-bearing field: when it is False the real
    gateway entry point was never reached, so no reservation was taken and no
    adapter could dispatch. It is an observed fact, not a claim.
    """
    status: str
    reason_code: str
    capability_id: str
    gateway_invoked: bool
    s0_failed: tuple[str, ...] = ()
    outcome: Any | None = None
    caveats: tuple[str, ...] = ()


def guarded_invoke(gateway: Any, evidence: Mapping[str, Any], *, capability_id: str,
                   params: Mapping[str, Any], run_id: str, operation_id: str,
                   account_ref: str, intent_fingerprint: str,
                   grant_ref: str | None = None,
                   agent_claims: Mapping[str, Any] | None = None) -> GuardedOutcome:
    """The truthful seam between this gate and a real gateway.

    Call this INSTEAD of `gateway.invoke`. The gate is evaluated first; unless
    it returns `launch == ALLOW`, the gateway object is not touched at all, so
    there is no reservation, no adapter execution and no vendor dispatch.

    The gateway is duck-typed on purpose: this module must not import the
    integration tree, so it cannot gain a second opinion about what a gateway
    is. Whatever object is passed must expose `invoke(...)` with the contract's
    keyword arguments.
    """
    decision = evaluate(evidence)
    if decision["launch"] != "ALLOW":
        return GuardedOutcome(
            status="BLOCKED",
            reason_code=str(decision["s1"]["reason_code"] or "CAPABILITY_NOT_AVAILABLE"),
            capability_id=capability_id,
            gateway_invoked=False,
            s0_failed=tuple(decision["s0"]["failed"]),
            caveats=("S0_GATE_REFUSED_BEFORE_GATEWAY",))
    outcome = gateway.invoke(
        capability_id=capability_id, params=params, run_id=run_id,
        operation_id=operation_id, account_ref=account_ref,
        intent_fingerprint=intent_fingerprint, grant_ref=grant_ref,
        agent_claims=agent_claims)
    return GuardedOutcome(
        status=str(getattr(outcome, "status", "")),
        reason_code=str(getattr(outcome, "reason_code", "")),
        capability_id=capability_id,
        gateway_invoked=True,
        outcome=outcome)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    from pathlib import Path

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence", type=Path, required=True,
                    help="JSON evidence file (no credential values)")
    args = ap.parse_args(argv)
    result = evaluate(json.loads(args.evidence.read_text(encoding="utf-8")))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())


assert all(isinstance(x, str) and x for x in VERDICTS)  # closed set must stay non-empty
assert math.isfinite(float(floor_for(Decimal("1"), "USD")))
# The discovery exception can never become a tool call or a purchase.
assert "tools/call" not in BOOTSTRAP_ALLOWED_METHODS
assert "notifications/initialized" in BOOTSTRAP_ALLOWED_METHODS
assert "tools/list" in BOOTSTRAP_ALLOWED_METHODS

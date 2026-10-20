#!/usr/bin/env python3
"""The types every Wave-1 collaborator shares (INTERFACE-CONTRACT.md §2-§3, §5.3).

This is the module the other worker modules are supposed to import *instead of
copying the shapes*. The registry, the policy, the adapters and the gateway all
talk in these objects, so the cost of a divergence is paid once, here, rather
than once per pair of modules.

## Two things this module deliberately does not do

**It does not decide anything.** No policy, no budget, no routing. A type that
also made decisions would be a second place to look when a call is refused.

**It does not accept a value it cannot check.** A tier outside T0..T4, a verdict
outside the three the gateway knows, an outcome outside the four the ledger and
the facade distinguish, an amount that is a ``float`` — each raises here, at
construction, so the alternative is not a plausible-looking object that fails
later in somebody else's module.

## Why ``Tier`` is a string enum

The gateway ranks a tier with ``TIER_ORDER.index(str(tier))``. ``str()`` of an
``IntEnum`` is the digit, so an integer tier silently ranks as "not in the
ladder" and every schema-drift gate misfires. The contract's own spelling is
``T0``..``T4``; this is that spelling, and :func:`tier_rank` is the one place
that turns it into an order.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Mapping

__all__ = [
    "Tier", "EffectClass", "ChargeState", "ActualSource", "OutcomeCode",
    "Availability", "SchemaVerdict", "PolicyVerdict", "REASON_CODES",
    "TIER_ORDER", "tier_rank", "ContractError", "ArtifactRef", "CapabilityRequest",
    "PolicyDecision", "AdapterResult", "EvidenceEnvelope",
]


class ContractError(ValueError):
    """A value outside the frozen contract. Raised at construction, never coerced."""


class Tier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
    T4 = "T4"


class EffectClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    BILLABLE_READ = "BILLABLE_READ"
    BILLABLE_ACT = "BILLABLE_ACT"
    STATE_CHANGING = "STATE_CHANGING"


class ChargeState(StrEnum):
    RESERVED = "RESERVED"
    SETTLED = "SETTLED"
    NO_CHARGE_PROVEN = "NO_CHARGE_PROVEN"
    UNKNOWN = "UNKNOWN"
    FREE_TIER = "FREE_TIER"


class ActualSource(StrEnum):
    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    DERIVED = "DERIVED"
    NONE = "NONE"


class OutcomeCode(StrEnum):
    OK = "OK"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class Availability(StrEnum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    UNSUPPORTED = "UNSUPPORTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"


class SchemaVerdict(StrEnum):
    MATCH = "MATCH"
    DRIFT = "DRIFT"
    ABSENT = "ABSENT"
    UNVERIFIED = "UNVERIFIED"


class PolicyVerdict(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN_GRANT = "REQUIRE_HUMAN_GRANT"


TIER_ORDER: tuple[str, ...] = ("T0", "T1", "T2", "T3", "T4")


def tier_rank(tier: Tier | str) -> int:
    """The position of a tier in the ladder. One definition, used by every caller."""
    value = str(getattr(tier, "value", tier))
    try:
        return TIER_ORDER.index(value)
    except ValueError:
        raise ContractError(f"tier outside T0..T4: {tier!r}") from None


#: §5.3, transcribed verbatim. New codes go through the coordinator's CHANGE-nn
#: process; nothing here is invented locally. `research_gateway.REASON_CODES`
#: carries the same set, and a test compares the two rather than trusting that
#: they were transcribed alike.
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


def _check_code(value: str, field_name: str) -> str:
    if not isinstance(value, str) or value not in REASON_CODES:
        raise ContractError(f"{field_name} must be a §5.3 code, got {value!r}")
    return value


def _check_id(value: str, field_name: str, *, limit: int = 96) -> str:
    """Opaque identifiers, checked by shape: no whitespace, no free text."""
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ContractError(f"{field_name} must be a non-empty identifier of <= {limit} chars")
    if any(ch.isspace() for ch in value):
        raise ContractError(f"{field_name} must not contain whitespace")
    return value


def _check_amount(value: Decimal | int | None, field_name: str) -> Decimal | None:
    """Money and quota are ``Decimal`` (or int micros), never ``float``.

    A float here is not a rounding nuisance: it is a number the ledger cannot
    reconcile against a vendor invoice to the micro.
    """
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise ContractError(f"{field_name} must be Decimal or int, never {type(value).__name__}")
    if isinstance(value, int):
        value = Decimal(value)
    if not isinstance(value, Decimal):
        raise ContractError(f"{field_name} must be Decimal or int, got {type(value).__name__}")
    if not value.is_finite() or value < 0:
        raise ContractError(f"{field_name} must be finite and nonnegative")
    return value


def _aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ContractError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ArtifactRef:
    """A pointer to a persisted artifact. The content is never inlined."""

    kind: str
    ref: str
    sha256: str = ""

    def __post_init__(self) -> None:
        _check_id(self.kind, "ArtifactRef.kind", limit=32)
        _check_id(self.ref, "ArtifactRef.ref", limit=2048)
        if self.sha256 and (len(self.sha256) != 64 or
                            any(ch not in "0123456789abcdef" for ch in self.sha256)):
            raise ContractError("ArtifactRef.sha256 must be 64 lowercase hex characters")


@dataclass(frozen=True)
class CapabilityRequest:
    """The input to one managed call. Nothing reaches a provider without one."""

    capability_id: str
    params: Mapping[str, object]
    run_id: str
    operation_id: str
    account_ref: str
    intent_fingerprint: str
    grant_ref: str | None = None

    def __post_init__(self) -> None:
        for name in ("capability_id", "run_id", "operation_id", "account_ref",
                     "intent_fingerprint"):
            _check_id(getattr(self, name), f"CapabilityRequest.{name}")
        if not isinstance(self.params, Mapping):
            raise ContractError("CapabilityRequest.params must be a mapping")
        if self.grant_ref is not None:
            _check_id(self.grant_ref, "CapabilityRequest.grant_ref")


@dataclass(frozen=True)
class PolicyDecision:
    """The gate's answer. ``effective_tier`` is the highest of the four dimensions."""

    verdict: PolicyVerdict | str
    reason_code: str
    effective_tier: Tier | str
    grant_ref: str | None = None
    constraints: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        verdict = str(getattr(self.verdict, "value", self.verdict))
        if verdict not in {v.value for v in PolicyVerdict}:
            raise ContractError(f"policy verdict outside the closed set: {verdict!r}")
        object.__setattr__(self, "verdict", PolicyVerdict(verdict))
        if verdict == PolicyVerdict.ALLOW.value:
            # An allow carries no reason code: the closed set has no "OK", and a
            # plausible-looking one would make "allowed" indistinguishable from a
            # refusal that was mapped to the same word.
            if self.reason_code != "":
                raise ContractError(
                    "an ALLOW decision carries reason_code='', not a §5.3 code")
        else:
            _check_code(self.reason_code, "PolicyDecision.reason_code")
        tier_rank(self.effective_tier)
        if not isinstance(self.constraints, Mapping):
            raise ContractError("PolicyDecision.constraints must be a mapping")


@dataclass(frozen=True)
class AdapterResult:
    """What an adapter returns. Outcome is execution state; money is separate."""

    outcome: OutcomeCode | str
    payload: Mapping[str, object] | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    upstream_ref: str | None = None
    actual_usd: Decimal | int | None = None
    actual_source: ActualSource | str = ActualSource.NONE
    quota_units_used: Decimal | int | None = None
    transport: str = ""
    schema_verdict: SchemaVerdict | str = SchemaVerdict.UNVERIFIED
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        outcome = str(getattr(self.outcome, "value", self.outcome))
        if outcome not in {v.value for v in OutcomeCode}:
            raise ContractError(f"adapter outcome outside the closed set: {outcome!r}")
        object.__setattr__(self, "outcome", OutcomeCode(outcome))
        source = str(getattr(self.actual_source, "value", self.actual_source))
        if source not in {v.value for v in ActualSource}:
            raise ContractError(f"actual_source outside the closed set: {source!r}")
        object.__setattr__(self, "actual_source", ActualSource(source))
        verdict = str(getattr(self.schema_verdict, "value", self.schema_verdict))
        if verdict not in {v.value for v in SchemaVerdict}:
            raise ContractError(f"schema_verdict outside the closed set: {verdict!r}")
        object.__setattr__(self, "schema_verdict", SchemaVerdict(verdict))
        _check_amount(self.actual_usd, "AdapterResult.actual_usd")
        _check_amount(self.quota_units_used, "AdapterResult.quota_units_used")
        if self.payload is not None and not isinstance(self.payload, Mapping):
            raise ContractError("AdapterResult.payload must be a mapping or None")
        if not isinstance(self.artifacts, tuple) or not all(
                isinstance(item, ArtifactRef) for item in self.artifacts):
            raise ContractError("AdapterResult.artifacts must be a tuple of ArtifactRef")
        if not isinstance(self.caveats, tuple) or not all(
                isinstance(item, str) for item in self.caveats):
            raise ContractError("AdapterResult.caveats must be a tuple of strings")
        # A BLOCKED result that names no closed-set reason is not translatable by
        # the gateway (§ its `_result_refusal_code`), so it is refused here where
        # the adapter that produced it is still on the stack.
        if self.outcome is OutcomeCode.BLOCKED and not set(self.caveats) & REASON_CODES:
            raise ContractError(
                "a BLOCKED AdapterResult must carry a §5.3 code in caveats; "
                "otherwise the refusal cannot reach a host")


@dataclass(frozen=True)
class EvidenceEnvelope:
    """The link between a saved artifact and the ledger row that paid for it."""

    evidence_id: str
    run_id: str
    operation_id: str
    ledger_id: str
    provider: str
    capability_id: str
    source_url: str | None
    retrieved_at: datetime
    content_sha256: str
    raw_artifact_ref: str
    derived_refs: tuple[str, ...] = ()
    extraction_method: str = ""
    verified_state: Literal["UNVERIFIED", "CORROBORATED", "CONTRADICTED",
                            "HUMAN_REVIEWED"] = "UNVERIFIED"
    candidate_only: bool = True
    egress_decision_ref: str | None = None
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("evidence_id", "run_id", "operation_id", "ledger_id", "provider",
                     "capability_id", "raw_artifact_ref"):
            _check_id(getattr(self, name), f"EvidenceEnvelope.{name}")
        _aware(self.retrieved_at, "EvidenceEnvelope.retrieved_at")
        if len(self.content_sha256) != 64 or any(
                ch not in "0123456789abcdef" for ch in self.content_sha256):
            raise ContractError("EvidenceEnvelope.content_sha256 must be 64 lowercase hex")
        if self.verified_state not in {"UNVERIFIED", "CORROBORATED", "CONTRADICTED",
                                       "HUMAN_REVIEWED"}:
            raise ContractError(f"verified_state outside the closed set: {self.verified_state!r}")
        if self.candidate_only is not True:
            # Research candidates must not become formal records inside this boundary.
            raise ContractError("EvidenceEnvelope.candidate_only must be True in this boundary")
        if self.source_url is not None and not isinstance(self.source_url, str):
            raise ContractError("EvidenceEnvelope.source_url must be a string or None")
        if not isinstance(self.derived_refs, tuple) or not all(
                isinstance(item, str) for item in self.derived_refs):
            raise ContractError("EvidenceEnvelope.derived_refs must be a tuple of strings")


def never_sent(exc: BaseException) -> bool:
    """True when a failure provably happened before any request byte was sent.

    DNS failure, a refused or unreachable connection, a proxy refusing the
    CONNECT tunnel and a certificate rejected during the handshake all end the
    attempt before the provider can see, let alone bill, a request. Treating them
    as an unknown charge held every later call behind a debt that cannot exist.
    Anything after the connection is up (resets, timeouts, bad bodies) stays
    ambiguous. Exception groups and chained causes are searched.
    """
    import socket
    import ssl
    seen, stack = set(), [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, (socket.gaierror, ConnectionRefusedError,
                                ssl.SSLCertVerificationError)):
            return True
        name = type(current).__name__
        if name in {"ConnectError", "ProxyError"}:  # httpx / httpcore, pre-request
            return True
        if isinstance(current, OSError) and "Tunnel connection failed" in str(current):
            return True
        stack.extend(getattr(current, "exceptions", ()) or ())
        stack.extend((getattr(current, "reason", None) if isinstance(
            getattr(current, "reason", None), BaseException) else None,
            current.__cause__, current.__context__))
    return False

"""TEST FIXTURE — offline stand-in for INTERFACE-CONTRACT §2, §3 and §5.2 shapes.

**This module is NOT the contract and must never be imported by production code.**
The Wave-1 contract (`docs/wave1-coordination/INTERFACE-CONTRACT.md`, v1.0, base
`b2bcdcd`) places the real shared types in `scripts/research_contract.py`, owned by
W-B, and the real `ProviderAdapter` Protocol in `scripts/research_adapter_base.py`.
Both have since landed, so the fixture's remaining job is narrower than it was:
it lets the gateway's unit tests run without the real registry table, policy or
ledger, and it keeps them measuring the gateway rather than a vendor. It is still
not a second source of truth — `tests/test_wave1_integration_seam.py` compares
every field name here against the module that now owns it, so a shape change in
W-B's code turns this fixture red instead of letting the two drift together.

The synthetic collaborators below (registry, policy, ledger, adapter) are
deliberately minimal: they record *who was called, in what order, with what*, so
the orchestrator's ordering guarantees can be asserted rather than assumed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# §2 shared types
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# §3 runtime objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapabilityRequest:
    capability_id: str
    params: Mapping[str, Any]
    run_id: str
    operation_id: str
    account_ref: str
    intent_fingerprint: str
    grant_ref: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    verdict: str
    reason_code: str
    effective_tier: Tier
    grant_ref: str | None = None
    constraints: Mapping[str, Any] = field(default_factory=dict)


# No `Reservation` stand-in lives here, and its absence is deliberate. §3.3 of
# the frozen contract draws one, but no implementation module declares it: the
# ledger's `reserve()` returns its own `Operation` row, and that row is the
# authority. A fixture that supplied a plausible `Reservation` would have let the
# gateway's integration tests agree with a type nothing in the product has — the
# fixture and the code would have drifted together, invisibly. The drift test in
# `test_wave1_integration_seam.py` pins this gap by name.


@dataclass(frozen=True)
class AdapterResult:
    outcome: OutcomeCode
    payload: Mapping[str, Any] | None = None
    artifacts: tuple[str, ...] = ()
    upstream_ref: str | None = None
    actual_usd: Decimal | None = None
    actual_source: ActualSource = ActualSource.NONE
    quota_units_used: Decimal | None = None
    transport: str = ""
    schema_verdict: SchemaVerdict = SchemaVerdict.UNVERIFIED
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityEntry:
    capability_id: str
    provider: str
    upstream_tool: str
    transport: str
    effect_class: EffectClass
    tier: Tier
    auth_scope: str
    cost_meter: str
    param_schema: Mapping[str, Any]
    allowed_params: frozenset[str]
    schema_sha256: str
    availability: Availability
    readiness: str
    #: The ledger's reviewed name for this work. The registry refuses an ENABLED
    #: entry without one, and the wiring bridge reads it — so a fixture entry that
    #: carried only the adapter's vocabulary would have let the gateway tests pass
    #: while the real composition raised `WiringIncomplete` at the reservation.
    ledger_capability: str | None = None
    escalates_to: Mapping[str, Tier] = field(default_factory=dict)
    description: str = ""


# ---------------------------------------------------------------------------
# Synthetic collaborators
# ---------------------------------------------------------------------------

_EPOCH = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)


class Recorder:
    """Ordered call log. Ordering is the integration property under test."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def add(self, kind: str, detail: str = "") -> None:
        self.events.append((kind, detail))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]

    def index_of(self, kind: str) -> int:
        return self.kinds().index(kind)


def entry(capability_id: str = "tinyfish.search", *, provider: str = "tinyfish",
          tier: Tier = Tier.T1, transport: str = "official_mcp",
          allowed_params: frozenset[str] = frozenset({"query", "locale"}),
          availability: Availability = Availability.ENABLED,
          ledger_capability: str | None = "search") -> CapabilityEntry:
    """One synthetic entry. `ledger_capability` defaults to the reviewed name the
    real registry gives `tinyfish.search`; a test that needs an unpriced entry
    passes `None` explicitly rather than relying on a stripped-down default."""
    return CapabilityEntry(
        capability_id=capability_id, provider=provider, upstream_tool="search",
        transport=transport, effect_class=EffectClass.BILLABLE_READ, tier=tier,
        auth_scope=f"{provider}:search", cost_meter=f"{provider}.search",
        param_schema={"type": "object"}, allowed_params=allowed_params,
        schema_sha256="0" * 64, availability=availability, readiness="CODED",
        ledger_capability=ledger_capability)


class FakeRegistry:
    def __init__(self, entries: Sequence[CapabilityEntry], recorder: Recorder | None = None) -> None:
        self._entries = {e.capability_id: e for e in entries}
        self._recorder = recorder

    def get(self, capability_id: str) -> CapabilityEntry | None:
        if self._recorder:
            self._recorder.add("registry.get", capability_id)
        return self._entries.get(capability_id)

    def list(self, *, provider: str | None = None, tier: Tier | None = None,
             task_kind: str | None = None) -> Sequence[CapabilityEntry]:
        return tuple(self._entries.values())

    def describe(self, capability_id: str) -> CapabilityEntry:
        return self._entries[capability_id]

    def verify_schema(self, capability_id: str, observed_tools_list: Mapping[str, Any]) -> SchemaVerdict:
        return SchemaVerdict.UNVERIFIED


class FakePolicy:
    """Records the request it was handed; returns a preset decision."""

    def __init__(self, decision: PolicyDecision | None = None,
                 recorder: Recorder | None = None) -> None:
        self._decision = decision
        self._recorder = recorder
        self.seen: list[Any] = []

    def evaluate(self, req: Any) -> PolicyDecision:
        self.seen.append(req)
        if self._recorder:
            self._recorder.add("policy.evaluate", getattr(req, "capability_id", "?"))
        if self._decision is not None:
            return self._decision
        return PolicyDecision(verdict="ALLOW", reason_code="", effective_tier=Tier.T1)


class FakeLedger:
    """A stand-in with the **real** ledger's call shape.

    It previously mirrored the retired 370-line ledger (`reserve(req, decision)`,
    `settle(id, result)`, `mark_unknown(id)`). The ledger that replaced it takes
    one positional argument and keyword-only required ones, so those calls raised
    TypeError against the real thing — and this stand-in accepting the old shapes
    is why the gateway tests stayed green while the integration was broken. A
    double that is more forgiving than the real dependency hides exactly the
    defects it exists to catch.
    """

    def __init__(self, *, recorder: Recorder | None = None, refuse: BaseException | None = None,
                 operation_id: str = "op-fixture") -> None:
        self._recorder = recorder
        self._refuse = refuse
        self._operation_id = operation_id
        self.reserved: list[Any] = []
        self.settled: list[tuple] = []
        self.unknowns: list[tuple] = []
        self.no_charge: list[tuple] = []
        self.intents: list[str] = []

    class _Reservation:
        """A reservation carrying what a real one carries.

        ``reserved_micros`` is not decoration: the gateway settles with the
        reserve-time estimate under the ledger's ``DERIVED`` label when no
        provider-reported amount exists, and without an estimate there is nothing
        attributable to book — which correctly holds the exposure instead. A
        fixture reservation with no estimate would test the hold path while
        claiming to test the settle path.
        """

        def __init__(self, operation_id: str, reserved_micros: int = 1000) -> None:
            self.operation_id = operation_id
            self.ledger_id = operation_id
            self.reserved_micros = reserved_micros
            self.estimated_micros = reserved_micros

    def reserve(self, request) -> "FakeLedger._Reservation":
        self.reserved.append(request)
        if self._recorder:
            self._recorder.add("ledger.reserve", str(getattr(request, "operation_id", "?")))
        if self._refuse is not None:
            raise self._refuse
        return FakeLedger._Reservation(
            getattr(request, "operation_id", None) or self._operation_id)

    def mark_dispatch_intent(self, operation_id: str):
        self.intents.append(operation_id)
        if self._recorder:
            self._recorder.add("ledger.mark_dispatch_intent", operation_id)
        return None

    def settle(self, operation_id: str, *, actual_micros: int, actual_source: Any,
               upstream_ref: str = "", quota_units: int | None = None):
        self.settled.append((operation_id, actual_micros, actual_source,
                             upstream_ref, quota_units))
        if self._recorder:
            self._recorder.add("ledger.settle", operation_id)
        return None

    def mark_unknown(self, operation_id: str, *, reason: str):
        self.unknowns.append((operation_id, reason))
        if self._recorder:
            self._recorder.add("ledger.mark_unknown", operation_id)
        return None

    def prove_no_charge(self, operation_id: str, *, reason: str):
        self.no_charge.append((operation_id, reason))
        if self._recorder:
            self._recorder.add("ledger.prove_no_charge", operation_id)
        return None

    def reconcile(self, operation_id: str, *, actual_micros: int, actual_source: Any,
                  upstream_ref: str = ""):
        return None

    def headroom(self, scope: Any) -> Decimal:
        return Decimal("100")

    def report(self, scope: Any) -> Any:
        return {}


class ProbeAdapter:
    """Adapter that records being asked anything at all.

    `behaviour` is one of: "ok", "unknown", "failed", "returned_blocked",
    "returned_blocked_with_code", "raise_blocked_code", "raise_unknown".
    """

    def __init__(self, provider: str, capabilities: Sequence[str], *,
                 behaviour: str = "ok", recorder: Recorder | None = None,
                 transport: str = "official_mcp") -> None:
        self.provider = provider
        self._capabilities = tuple(capabilities)
        self._behaviour = behaviour
        self._recorder = recorder
        self.transport = transport
        self.executed: list[Any] = []

    def capabilities(self) -> Sequence[str]:
        if self._recorder:
            self._recorder.add("adapter.capabilities", self.provider)
        return self._capabilities

    def probe_schema(self) -> Mapping[str, Any]:
        return {"status": "AUTH_REQUIRED"}

    def execute(self, req: Any) -> AdapterResult:
        self.executed.append(req)
        if self._recorder:
            self._recorder.add("adapter.execute", self.provider)
        if self._behaviour == "ok":
            return AdapterResult(outcome=OutcomeCode.OK, transport=self.transport,
                                 schema_verdict=SchemaVerdict.MATCH)
        if self._behaviour == "unknown":
            return AdapterResult(outcome=OutcomeCode.UNKNOWN, transport=self.transport)
        if self._behaviour == "failed":
            return AdapterResult(outcome=OutcomeCode.FAILED, transport=self.transport)
        if self._behaviour == "returned_blocked":
            return AdapterResult(outcome=OutcomeCode.BLOCKED, transport=self.transport)
        if self._behaviour == "returned_blocked_with_code":
            return AdapterResult(outcome=OutcomeCode.BLOCKED, transport=self.transport,
                                 caveats=("EGRESS_REFUSED",))
        if self._behaviour == "raise_blocked_code":
            raise ValueError("EGRESS_REFUSED: no proxy available")
        if self._behaviour == "raise_unknown":
            raise RuntimeError("socket closed mid-response, cause unknown")
        raise AssertionError(f"unhandled behaviour {self._behaviour!r}")

    def cancel(self, upstream_ref: str) -> AdapterResult:
        return AdapterResult(outcome=OutcomeCode.BLOCKED, transport=self.transport)


class AskableAdapter(ProbeAdapter):
    """Adapter that fails the test if the orchestrator asks it anything but execute/capabilities."""

    FORBIDDEN = ("evaluate", "decide", "tier", "policy", "budget", "grant", "reserve")

    def __getattr__(self, name: str) -> Any:
        if any(word in name.lower() for word in self.FORBIDDEN):
            raise AssertionError(f"orchestrator asked the adapter for {name!r}")
        raise AttributeError(name)

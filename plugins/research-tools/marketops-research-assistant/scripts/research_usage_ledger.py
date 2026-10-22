#!/usr/bin/env python3
"""One authoritative usage ledger: reserve, execute, settle - with the hold kept.

This replaces two count-based attempt ledgers (``dual_search_mcp``'s
``search_attempts`` and ``research_provider_extensions``'s ``provider_reservations``).
Those answered one question, "how many attempts have ever happened", with a single
global ``COUNT(*)``. They could not answer "how much money is this run holding",
"did the vendor actually charge this call", or "is this the same operation arriving
over a second transport". This module answers those. The written contract is
``references/UNIFIED_USAGE_LEDGER.md``.

## The decisions that are load-bearing

**There is no refund verb.** ``reserve`` is the only way money is held, and
``prove_no_charge`` (before dispatch only) is the only way a hold is released
without evidence. A timeout branch therefore has no call it could make to give the
hold back - it cannot forget a rule that has no entry point. If a vendor did charge
a call that timed out, the ledger still says so.

**Dispatch intent is recorded before the outbound call.** ``mark_dispatch_intent``
is what the caller runs immediately before it reaches the network. A crash between
the two then leaves the intent set, so reconciliation treats the operation as
possibly charged - the ledger's own orderings only ever move the books towards
*over*-counting exposure. What the ledger cannot see is a caller that never records
intent at all: it can still reach the network and then call ``prove_no_charge``, and
no local module can detect that. So the ledger enforces what it can and says so
rather than claiming more:

* ``settle`` and ``settle_free_tier`` require a dispatch intent, so a row that
  booked a cost always shows that a call was made;
* ``prove_no_charge`` refuses once an intent exists;
* recording that intent before the call is a **caller obligation**, and it is the
  one step whose omission a caller can hide from this module.

**Money and quota are different columns.** Integer micro-USD and vendor units
(credits, requests, steps, tokens, pages, seconds) are stored side by side and
never converted into each other. A local page costs 0 USD and 1 page; a Jev call
costs USD and tokens.

**A price is recorded, never assumed.** Every operation carries
``price_source``/``price_version``/``price_observed_at`` from the catalog at
reserve time. An unpriced capability is refused instead of estimated, and
``settle_free_tier`` refuses unless the recorded rate says the operation is free.

**Tier comes from the registry, not the caller.** A caller able to name its own
risk level names the cheapest one. Tier 3 and 4 additionally require a presented
grant that verifies against a public key configured out of band - and with no such
key they are unavailable rather than trusted.

No provider is called from this module. It has no network code, no credential
handling and no vendor SDK: it decides, it records, and it reports.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

try:  # inside the plugin package
    from . import research_grants as grants
except ImportError:  # standalone import from scripts/ (how the tests load it)
    import research_grants as grants

__all__ = [
    "MICRO", "ID", "ChargeState", "ActualSource", "Price", "PricingCatalog",
    "PricingConfigInvalid", "OperationRequest", "Operation", "Usage", "UsageLedger",
    "LedgerRefused", "default_path", "for_root", "project_for_root",
    "documented_pricing", "configured_pricing", "usd_cap_basis",
    "EXECUTION_OUTCOMES", "DOCUMENTED_CREDIT_RATES", "main",
    # the campaign floor: 20% of a frozen B0, and TinyFish's explicit US$0.20
    "USD_MICROS", "CAMPAIGN_FLOOR_RATIO", "CAMPAIGN_MAX_DRAWDOWN_RATIO",
    "TINYFISH_MINIMUM_USD", "TINYFISH_MINIMUM_MICROS", "TINYFISH_PROVIDER",
    "MeterReadingSource", "BudgetVerdict", "CampaignBaseline", "CampaignFloor",
    "CampaignBudget", "BudgetDecision", "AccountTotals",
    # how a refusal travels to a host
    "CONTRACT_REASON_CODES", "REFUSAL_DISPOSITION", "refusal_disposition",
]

MICRO = 1_000_000  # micro-USD per USD: money is integer arithmetic, never a float
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
WINDOWS = ("total", "day", "month")
SCOPE_KINDS = ("operation", "run", "provider_account", "project")
TERMINAL_STATES = ("SETTLED", "FREE_TIER", "NO_CHARGE_PROVEN")

#: Which of the contract's ceiling names a refusal belongs to, by cap scope (§5.3).
_CEILING_CODE = {
    "operation": "BUDGET_OPERATION_CEILING",
    "run": "BUDGET_RUN_CEILING",
    "provider_account": "BUDGET_PROVIDER_CEILING",
    "project": "BUDGET_PROJECT_CEILING",
}
NO_CHARGE_REASONS = frozenset({"PRE_DISPATCH_REFUSAL"})
#: Why the *charge* is uncertain. ``PROVIDER_ERROR_UNVERIFIED`` is an execution
#: failure whose billing cannot be established; ``CHARGE_UNVERIFIED_AFTER_SUCCESS``
#: is the separate case where the call succeeded and only its cost is unknown.
UNKNOWN_REASONS = frozenset({"PROVIDER_TIMEOUT", "PROCESS_DIED", "PROVIDER_ERROR_UNVERIFIED",
                             "CHARGE_UNVERIFIED_AFTER_SUCCESS", "AMBIGUOUS_RESPONSE"})
#: What the adapter observed about execution, kept apart from the charge state. An
#: empty value means nobody recorded it (for example an imported legacy row or a
#: process that died before the outcome was known).
EXECUTION_OUTCOMES = frozenset({"OK", "FAILED", "BLOCKED", "UNKNOWN"})
MAX_ARGS_BYTES = 64 * 1024

#: The units a vendor counts in. Closed list, because a free-form label lets a
#: report file a money-looking column under quota: ``quota_unit="USD"`` next to
#: ``money`` reads as dollars and is not one.
QUOTA_UNITS = frozenset({"requests", "credits", "searches", "steps", "tokens", "pages",
                         "seconds", "bytes", "urls", "sessions", "none"})


def _is_lock_contention(exc: sqlite3.Error) -> bool:
    """Whether a sqlite error is "someone else has it" rather than "this is broken".

    Only contention is worth retrying; anything else is reported as it is, because
    retrying a malformed statement or a corrupt file just delays the same failure.
    """
    text = str(exc).casefold()
    return "locked" in text or "busy" in text


def _usd_price_is_known(price: "Price") -> bool:
    """Whether a rate is a *price* rather than a placeholder.

    ``unit_micros == 0`` with ``free_tier=False`` is this plugin's encoding for "nobody
    has a verified dollar rate for this" - it is what ``documented_pricing`` writes for
    every capability it can only meter in the vendor's own unit. Reading that as US$0 is
    how an unknown charge becomes a free one, and a dollar floor judged against it never
    moves: measured, a US$1.00 wallet with a US$0.20 floor admitted twelve chargeable
    calls while its committed dollars stayed at zero.
    """
    return bool(price.free_tier or price.unit_micros > 0)


def usd_cap_basis(price: "Price | None") -> tuple[bool, str]:
    """Whether a recorded rate is a *provable* upper bound for a USD ceiling.

    A money ceiling is a promise that no call can exceed a dollar amount. That
    promise needs a rate the ledger can multiply by the request's units before
    dispatch; a placeholder zero (``unit_micros=0``, ``free_tier=False``) or an
    unverified rate cannot support it. Returning the basis rather than a bare
    boolean keeps the refusal actionable: "no price" and "price nobody confirmed"
    are different repairs.
    """
    if price is None:
        return False, "PRICE_MISSING"
    if not price.free_tier and price.unit_micros <= 0:
        return False, "PRICE_PLACEHOLDER_ZERO"
    if not price.verified:
        return False, "PRICE_UNVERIFIED"
    if price.free_tier:
        return True, "VERIFIED_FREE_TIER"
    return True, "VERIFIED_RATE"


def display_actual_micros(operation):
    """Read historical placeholder-derived zeros as unknown without rewriting."""
    if (operation.actual_micros == 0 and operation.actual_source == "DERIVED"
            and not operation.price_verified
            and operation.price_source == "provider_documentation_credits_only"):
        return None
    return operation.actual_micros


def _reference(value: Any, field: str) -> str:
    """A bounded opaque reference: a provider run id, a request id, an evidence id.

    Validated rather than accepted verbatim. An unbounded free-text field that lands
    in the database, in the audit trail and in a printed report is a place a caller
    can put a credential, a provider error body or a page of HTML - and every one of
    those has happened in this codebase's neighbourhood. A reference is an
    identifier, so it is checked like one.
    """
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise LedgerRefused(
            "REFERENCE_NOT_OPAQUE",
            f"{field} must be a short opaque identifier, not free text; a provider "
            "reference is an id, and this field is read in reports and in the audit trail")
    return value

#: Schema creation is the one place where several threads constructing a ledger on
#: one database could issue DDL at the same time. Cross-process this is already
#: handled by SQLite's own locking; this lock keeps the in-process case boring.
_SCHEMA_LOCK = threading.Lock()


class ChargeState(str, Enum):
    """Where an operation stands. Five states, and every one of them is reported."""

    RESERVED = "RESERVED"
    SETTLED = "SETTLED"
    FREE_TIER = "FREE_TIER"
    NO_CHARGE_PROVEN = "NO_CHARGE_PROVEN"
    UNKNOWN = "UNKNOWN"


class ActualSource(str, Enum):
    """Where a settled amount came from. ``NONE`` is stamped at reserve time."""

    PROVIDER_REPORTED = "PROVIDER_REPORTED"
    DERIVED = "DERIVED"
    NONE = "NONE"


class LedgerRefused(Exception):
    """A refusal with a stable code; the code is what callers branch on.

    ``reason_code`` is the same value under the name the frozen interface contract
    uses (§5.2), and the name the gateway reads when it turns a refusal into a
    ``BLOCKED`` result. Carrying only ``code`` meant a governed refusal arrived at the
    gateway as ``code=None`` - measured by QA round 5 - so both names are present and
    they cannot disagree.
    """

    def __init__(self, code: str, message: str, *, dimension: str = ""):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        #: Which dimension of a ceiling was hit (``calls`` / ``money`` / ``seconds``).
        #: The contract's vocabulary names the *scope*, not the dimension, so the
        #: dimension rides alongside the code rather than inside it - a caller that
        #: needs it does not have to parse the message, and the code stays one of the
        #: contract's names.
        self.dimension = dimension

    @property
    def reason_code(self) -> str:
        return self.code


class PricingConfigInvalid(ValueError):
    """The cost configuration is wrong. Refused loudly rather than defaulted."""


class _NotCanonical(ValueError):
    """An argument that cannot be canonicalised, so it cannot be fingerprinted."""


# --------------------------------------------------------------------------- #
# Prices
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Price:
    """One provider capability's rate, and where that rate came from.

    ``verified`` is separate from the amount on purpose. A documented zero price is
    a *claim about a plan*, and the plan can change; recording the claim without
    recording that nobody confirmed it would let a report read as settled fact.
    """

    provider: str
    capability: str
    unit_micros: int
    quota_unit: str
    quota_per_call: int
    source: str
    version: str
    observed_at: str
    free_tier: bool = False
    verified: bool = True

    def __post_init__(self) -> None:
        for name in ("provider", "capability"):
            value = getattr(self, name)
            if not isinstance(value, str) or not ID.fullmatch(value):
                raise PricingConfigInvalid(f"price {name} must be a short opaque identifier")
        if type(self.unit_micros) is not int or self.unit_micros < 0:
            raise PricingConfigInvalid(
                f"{self.provider}/{self.capability}: unit_micros must be a nonnegative integer")
        if not isinstance(self.quota_unit, str) or not self.quota_unit.strip():
            raise PricingConfigInvalid(f"{self.provider}/{self.capability}: quota_unit is required")
        if type(self.quota_per_call) is not int or self.quota_per_call < 1:
            raise PricingConfigInvalid(
                f"{self.provider}/{self.capability}: quota_per_call must be a positive integer")
        for name in ("source", "version", "observed_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise PricingConfigInvalid(f"{self.provider}/{self.capability}: {name} is required")
        try:
            observed = datetime.fromisoformat(self.observed_at.replace("Z", "+00:00"))
        except ValueError:
            raise PricingConfigInvalid(
                f"{self.provider}/{self.capability}: observed_at must be an ISO "
                "timestamp") from None
        if observed.tzinfo is None:
            raise PricingConfigInvalid(
                f"{self.provider}/{self.capability}: observed_at must carry a timezone")
        if self.free_tier and self.unit_micros != 0:
            raise PricingConfigInvalid(
                f"{self.provider}/{self.capability}: a free tier rate cannot carry a price")
        for name in ("free_tier", "verified"):
            if type(getattr(self, name)) is not bool:
                raise PricingConfigInvalid(
                    f"{self.provider}/{self.capability}: {name} must be bool")


class PricingCatalog:
    """Reviewed rates, looked up by (provider, capability) - never by vendor tool name."""

    _REQUIRED = ("provider", "capability", "unit_micros", "quota_unit", "quota_per_call",
                 "source", "version", "observed_at")
    _OPTIONAL = ("free_tier", "verified")

    def __init__(self, entries: Iterable[Price]):
        table: dict[tuple[str, str], Price] = {}
        for price in entries:
            key = (price.provider, price.capability)
            if key in table:
                raise PricingConfigInvalid(f"duplicate price entry for {key[0]}/{key[1]}")
            table[key] = price
        self._table = table

    @classmethod
    def from_entries(cls, entries: Iterable[Mapping[str, Any]]) -> PricingCatalog:
        """Build from plain objects (a config file, a test table) - strictly."""
        prices = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                raise PricingConfigInvalid(f"price entry {index} is not an object")
            missing = [name for name in cls._REQUIRED if name not in entry]
            if missing:
                raise PricingConfigInvalid(
                    f"price entry {index} is missing {', '.join(sorted(missing))}")
            unknown = sorted(set(entry) - set(cls._REQUIRED) - set(cls._OPTIONAL))
            if unknown:
                raise PricingConfigInvalid(
                    f"price entry {index} has unknown fields: {', '.join(unknown)}")
            try:
                prices.append(Price(
                    provider=entry["provider"], capability=entry["capability"],
                    unit_micros=entry["unit_micros"], quota_unit=entry["quota_unit"],
                    quota_per_call=entry["quota_per_call"], source=entry["source"],
                    version=entry["version"], observed_at=entry["observed_at"],
                    free_tier=entry.get("free_tier", False),
                    verified=entry.get("verified", True),
                ))
            except PricingConfigInvalid as exc:
                raise PricingConfigInvalid(f"price entry {index}: {exc}") from None
        return cls(prices)

    @classmethod
    def from_json(cls, text: str) -> PricingCatalog:
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            raise PricingConfigInvalid("pricing config is not valid JSON") from None
        entries = payload.get("prices") if isinstance(payload, Mapping) else payload
        if not isinstance(entries, list):
            raise PricingConfigInvalid("pricing config must be a list, or {'prices': [...]}")
        return cls.from_entries(entries)

    @classmethod
    def from_path(cls, path: str | Path) -> PricingCatalog:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise PricingConfigInvalid(f"pricing config is unreadable: {exc.strerror}") from None
        return cls.from_json(text)

    def price_for(self, provider: str, capability: str) -> Price | None:
        return self._table.get((provider, capability))

    def __len__(self) -> int:
        return len(self._table)


# --------------------------------------------------------------------------- #
# Requests and rows
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class OperationRequest:
    """One operation a caller wants the ledger to hold budget for.

    There is no ``tier`` field: the tier comes from the reviewed registry, because
    a caller that can declare its own risk level can declare the lowest one.
    """

    project: str
    run_id: str
    operation_id: str
    provider: str
    capability: str
    account_ref: str
    transport: str
    intent: str
    args: Mapping[str, Any]
    units: int = 1
    quota_unit: str = ""
    estimated_seconds: int = 0
    escalation_reason: str = ""
    grant: str = ""
    site: str = ""


@dataclass(frozen=True)
class Operation:
    """A row of the ledger, whole. Read-only: every change goes through a verb."""

    operation_id: str
    project: str
    run_id: str
    intent_fingerprint: str
    op_signature: str
    provider: str
    account_ref: str
    capability: str
    meter: str
    transport: str
    site: str
    route_shape: str
    tier: int
    estimated_micros: int
    reserved_micros: int
    actual_micros: int | None
    actual_source: str
    charge_state: str
    quota_unit: str
    quota_units: int
    estimated_seconds: int
    outcome: str
    upstream_ref: str
    grant_ref: str
    escalates_from: str
    price_source: str
    price_version: str
    price_observed_at: str
    price_verified: bool
    origin: str
    legacy_ref: str | None
    counts_against_caps: bool
    created_at: str
    dispatch_intent_at: str | None
    settled_at: str | None
    reconciled_at: str | None
    #: Execution state as the adapter observed it (``OK``/``FAILED``/``UNKNOWN``),
    #: or empty when nobody recorded one. Deliberately not the same column as
    #: ``outcome``: ``charge_state``/``outcome`` say what is known about the money,
    #: this says what happened.
    execution_outcome: str = ""

    @property
    def held_micros(self) -> int:
        """Money this row still holds: a live reservation, or an unknown exposure."""
        return self.reserved_micros if self.charge_state in ("RESERVED", "UNKNOWN") else 0


@dataclass(frozen=True)
class Usage:
    """One ceiling's accounting. ``spent``/``reserved``/``unknown`` are split out.

    Collapsing them into one "used" number is how "how much is still unresolved"
    stops being answerable from the ledger - which is the first question asked
    after an incident.
    """

    scope_kind: str
    scope_key: str
    window: str
    period_key: str
    ceiling_micros: int | None
    ceiling_calls: int | None
    ceiling_seconds: int | None
    source: str
    spent_micros: int
    reserved_micros: int
    unknown_micros: int
    calls_used: int
    seconds_used: int
    quota_units: int
    #: Whether this ceiling was explicitly declared a USD ceiling. A call-count
    #: ceiling is a non-dollar mode even when a micro-USD column exists.
    usd_enforced: bool = False

    @property
    def held_micros(self) -> int:
        """Committed but not yet spent. Unknown exposure counts as held."""
        return self.reserved_micros + self.unknown_micros

    @property
    def remaining_micros(self) -> int | None:
        if self.ceiling_micros is None:
            return None
        return max(0, self.ceiling_micros - self.spent_micros - self.held_micros)

    @property
    def remaining_calls(self) -> int | None:
        if self.ceiling_calls is None:
            return None
        return max(0, self.ceiling_calls - self.calls_used)


_OPERATION_COLUMNS = tuple(field.name for field in fields(Operation))


@dataclass(frozen=True)
class AccountTotals:
    """What one ledger has committed on one provider account, across every project.

    Money and vendor units are both reported, because the campaign floor is
    expressed in whichever meter its ``B0`` was read in.
    """

    provider: str
    account_ref: str
    settled_micros: int
    reserved_micros: int
    unknown_micros: int
    quota_units: int
    calls: int

    @property
    def committed_micros(self) -> int:
        """Settled plus reserved: what has been charged or is still held."""
        return self.settled_micros + self.reserved_micros


# --------------------------------------------------------------------------- #
# Canonicalisation
# --------------------------------------------------------------------------- #

def _plain(value: Any) -> Any:
    """Only shapes two callers meaning the same thing will always produce.

    Floats are refused rather than hashed: ``0.1`` written in two places, or a value
    whose repr differs, would fingerprint one intent two ways - and a duplicate
    check that misses is a duplicate charge.

    Strings are normalised to NFC for the same reason. ``"café"`` has two encodings
    that render identically and are different bytes, so without this the same query
    typed by two clients - or by one client on two operating systems - would look
    like two operations and be bought twice.
    """
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    raise _NotCanonical(f"{type(value).__name__} cannot be canonicalised")


def _canonical_json(value: Any) -> str:
    text = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)
    if len(text.encode("utf-8")) > MAX_ARGS_BYTES:
        raise _NotCanonical("payload is too large to fingerprint")
    return text


def _digest(payload: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def route_shape(request: OperationRequest, args: Any) -> str:
    """What is being bought, without either the run or the transport.

    ``op_signature`` answers "is this the same operation in this run"; this answers
    "is this the same operation anywhere in this project". The difference matters for
    the route refusal: reaching one vendor account over a second transport is a route
    change rather than a second budget, and a run-scoped key cannot see it because the
    second caller is a different run by construction.

    It must **not** degenerate into "this project has touched this provider": that
    would refuse an unrelated query, and it would refuse the very migration it exists
    to protect (REST disabled, the embedded MCP the way in) the moment one REST row
    existed. So the operation itself - capability, units, site and canonical
    arguments - is in the key.
    """
    return _digest({"provider": request.provider, "account_ref": request.account_ref,
                    "capability": request.capability, "units": request.units,
                    "site": _plain(request.site), "args": _plain(args)})


def op_signature(request: OperationRequest, args: Any) -> str:
    """Key B: which *provider operation* this is. Transport excluded, everything else included.

    Transport is left out because it is how the call travels, not what it buys: the
    same query over SerpApi REST and SerpApi MCP is one spend opportunity, and a
    signature including the transport would bill it twice while both rows looked
    perfectly consistent on their own.

    ``units`` and ``site`` are **in**: both change what is being bought. Leaving
    ``units`` out made a 1-step and a 25-step purchase one signature, so the second
    was refused as a duplicate; leaving ``site`` out made two sites that a T4 grant
    exists to tell apart into one operation.
    """
    return _digest({"project": request.project, "run_id": request.run_id,
                    "provider": request.provider, "account_ref": request.account_ref,
                    "capability": request.capability, "units": request.units,
                    "site": _plain(request.site), "args": _plain(args)})


def intent_fingerprint(request: OperationRequest) -> str:
    """Key A: the research intent, scoped to the run.

    Scoped to the run because "the same question asked again next month" is that
    run's work, while two vendors bought in parallel for one run is the pattern
    this fingerprint exists to make visible.
    """
    return _digest({"project": request.project, "run_id": request.run_id,
                    "intent": " ".join(request.intent.split()).casefold()})


def _actual_source(value: ActualSource | str | None) -> ActualSource | None:
    if isinstance(value, ActualSource):
        return value
    if isinstance(value, str) and value in ActualSource.__members__:
        return ActualSource[value]
    return None


# --------------------------------------------------------------------------- #
# The ledger
# --------------------------------------------------------------------------- #

class UsageLedger:
    """Reserve, execute, settle - against one SQLite database.

    Args:
        path: the ledger database. Where it may live is the caller's data-root
            policy (never inside a Git checkout), not this class's.
        pricing: reviewed rates. An absent catalog means every operation is
            unpriced, and an unpriced operation is refused - not estimated at zero.
        registry: reviewed capabilities and their tiers.
        clock: injectable, so a period window does not depend on wall time.
        grants_authority: the public key that makes a T3/T4 grant mean anything.
    """

    def __init__(self, path: str | Path, *, pricing: PricingCatalog | None = None,
                 registry: grants.CapabilityRegistry | None = None,
                 clock: Callable[[], datetime] | None = None,
                 grants_authority: grants.GrantAuthority | None = None,
                 campaign_budgets: Mapping[tuple[str, str], CampaignBudget] | None = None,
                 busy_timeout_ms: int = 20_000,
                 init_budget_seconds: float = 3.0):
        if type(busy_timeout_ms) is not int or busy_timeout_ms < 1:
            raise LedgerRefused("INVALID_REQUEST",
                                "busy_timeout_ms must be a positive integer")
        if (isinstance(init_budget_seconds, bool)
                or not isinstance(init_budget_seconds, (int, float))
                or not (0 < init_budget_seconds <= 600)):
            raise LedgerRefused("INVALID_REQUEST",
                                "init_budget_seconds must be a positive number of seconds "
                                "no greater than 600")
        self._busy_timeout_ms = busy_timeout_ms
        self._init_budget_seconds = float(init_budget_seconds)
        self._path = Path(path)
        self._pricing = pricing if pricing is not None else PricingCatalog(())
        self._registry = registry if registry is not None else grants.DEFAULT_REGISTRY
        self._clock = clock
        self._grants = (grants_authority if grants_authority is not None
                        else grants.GrantAuthority.from_environment())
        # Keyed by (provider, account_ref) because a campaign floor belongs to one
        # account. Supplying any budget turns campaign enforcement on for *every*
        # chargeable operation: an account with no budget then fails closed rather
        # than spending outside a floor nobody captured.
        self._campaign = dict(campaign_budgets) if campaign_budgets else {}
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _SCHEMA_LOCK:
            self._initialise_schema()

    #: Total wall-clock budget for opening and upgrading the schema, and the pause
    #: between attempts inside it. A *deadline*, not an attempt count: counting
    #: attempts is not a bound at all when each attempt carries the driver's own
    #: 20 s busy timeout - twelve of those is four minutes (measured by QA round 7 as
    #: "still holding the exclusive lock after 200 s").
    INIT_BUDGET_SECONDS = 3.0
    INIT_PAUSE_SECONDS = 0.025
    #: A guard against a busy-loop of zero-cost attempts; the budget is what bounds it.
    _MAX_INIT_ATTEMPTS = 64

    def _initialise_schema(self) -> None:
        """Create or upgrade the schema, surviving other processes doing the same.

        ``PRAGMA journal_mode=WAL`` is the awkward step: it wants exclusive access and
        does not reliably consult the busy handler, so several processes starting at
        once on a *fresh* file can get ``database is locked`` even with a busy timeout
        set (measured: 2 of 12 concurrent constructors). It is also needed only once
        per file, so the sequence is - read the current mode, which takes no lock, and
        change it only when it is not already WAL.

        Everything here runs against one deadline measured on ``time.monotonic()``: the
        connect, its busy timeout, the pragma, the DDL and every retry pause. When the
        deadline passes the answer is ``LEDGER_INIT_BUSY``, so a caller learns in a few
        seconds that it cannot record - instead of inheriting the driver's timeout
        multiplied by the retry count.
        """
        deadline = time.monotonic() + self._init_budget_seconds
        last: Exception | None = None
        attempts = 0
        while attempts < self._MAX_INIT_ATTEMPTS:
            if time.monotonic() >= deadline:
                break
            attempts += 1
            try:
                # ``governed=False``: this loop *is* the governance for initialisation -
                # it retries contention against the deadline and raises its own code
                # when the budget runs out.
                with self._connect(governed=False, deadline=deadline) as db:
                    self._ensure_wal(db)
                    self._create_schema(db)
                return
            except sqlite3.OperationalError as exc:
                if not _is_lock_contention(exc):
                    raise LedgerRefused(
                        "LEDGER_INIT_FAILED",
                        f"the ledger database could not be initialised: "
                        f"{type(exc).__name__}: {exc}") from None
                last = exc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(self.INIT_PAUSE_SECONDS * attempts, remaining))
        raise LedgerRefused(
            "LEDGER_INIT_BUSY",
            f"the ledger database could not be opened within "
            f"{self._init_budget_seconds:.1f}s of wall clock ({attempts} attempts); refusing "
            f"rather than waiting without bound. Last error: {last}")

    @staticmethod
    def _ensure_wal(db: sqlite3.Connection) -> None:
        """Switch to WAL only when the file is not already in it.

        Asking for the mode on a file that already has it is a read, so the common case
        (every start after the first) takes no exclusive lock at all.
        """
        current = str(db.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if current != "wal":
            db.execute("PRAGMA journal_mode=WAL").fetchone()

    # -- plumbing ---------------------------------------------------------- #

    def _now(self) -> datetime:
        moment = self._clock() if self._clock is not None else datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    @contextmanager
    def _connect(self, *, write: bool = False, governed: bool = True,
                 deadline: float | None = None) -> Iterator[sqlite3.Connection]:
        """One connection per call, one transaction per change.

        A connection per call is what makes this object usable from several threads
        and several processes at once; ``BEGIN IMMEDIATE`` plus a busy timeout is
        what makes the read-check-write sequence in :meth:`reserve` atomic against
        a second process doing the same thing.

        ``governed`` decides what happens when the busy timeout expires under another
        writer's exclusive lock: a caller of this ledger gets a **refusal with a code**
        (``LEDGER_BUSY``), not SQLite's exception. Measured before this change: a
        ``reserve`` against a held ``BEGIN EXCLUSIVE`` sat for the full 20 s and then
        raised a raw ``sqlite3.OperationalError: database is locked`` - the same defect
        class QA round 5 measured in the D-owned adapter. Only contention is translated;
        a malformed statement or a corrupt file still surfaces as itself, because
        renaming it would hide a real fault.

        ``deadline`` (a ``time.monotonic()`` instant) caps *this attempt*: both the
        connect timeout and the busy timeout become the remaining time, so a caller
        with a 3 s budget cannot spend 20 s here. Initialisation passes one; ordinary
        verbs do not, and are bounded by ``busy_timeout_ms`` instead.
        """
        budget_ms = self._busy_timeout_ms
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LedgerRefused(
                    "LEDGER_INIT_BUSY",
                    "the initialisation budget was exhausted before a connection could be "
                    "opened; refusing rather than waiting without bound")
            budget_ms = max(1, min(budget_ms, int(remaining * 1000)))
        db = sqlite3.connect(self._path, timeout=budget_ms / 1000.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute(f"PRAGMA busy_timeout={budget_ms}")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.execute("COMMIT")
        except BaseException as exc:
            if write:
                try:
                    db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            if (governed and isinstance(exc, sqlite3.OperationalError)
                    and _is_lock_contention(exc)):
                raise LedgerRefused(
                    "LEDGER_BUSY",
                    f"another writer held the ledger database for the whole "
                    f"{self._busy_timeout_ms} ms busy timeout; the operation was not "
                    f"recorded. This is contention, not a budget refusal") from None
            raise
        finally:
            db.close()

    @staticmethod
    def _create_schema(db: sqlite3.Connection) -> None:
        db.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                project TEXT NOT NULL, run_id TEXT NOT NULL,
                intent_fingerprint TEXT NOT NULL, op_signature TEXT NOT NULL,
                provider TEXT NOT NULL, account_ref TEXT NOT NULL,
                capability TEXT NOT NULL, meter TEXT NOT NULL, transport TEXT NOT NULL,
                site TEXT NOT NULL DEFAULT '',
                route_shape TEXT NOT NULL DEFAULT '',
                tier INTEGER NOT NULL,
                estimated_micros INTEGER NOT NULL, reserved_micros INTEGER NOT NULL,
                actual_micros INTEGER, actual_source TEXT NOT NULL,
                charge_state TEXT NOT NULL,
                quota_unit TEXT NOT NULL, quota_units INTEGER NOT NULL,
                estimated_seconds INTEGER NOT NULL,
                outcome TEXT NOT NULL DEFAULT '', upstream_ref TEXT NOT NULL DEFAULT '',
                grant_ref TEXT NOT NULL DEFAULT '', escalates_from TEXT NOT NULL DEFAULT '',
                price_source TEXT NOT NULL, price_version TEXT NOT NULL,
                price_observed_at TEXT NOT NULL, price_verified INTEGER NOT NULL,
                origin TEXT NOT NULL DEFAULT 'native', legacy_ref TEXT,
                counts_against_caps INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, dispatch_intent_at TEXT,
                settled_at TEXT, reconciled_at TEXT,
                execution_outcome TEXT NOT NULL DEFAULT ''
            )""")
        # The same logical operation may not be bought twice in a run, whatever
        # operation_id it arrives under and whichever transport carries it.
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS operations_signature "
                   "ON operations (project, run_id, op_signature)")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS operations_legacy "
                   "ON operations (legacy_ref) WHERE legacy_ref IS NOT NULL")
        # A database written before ``route_shape`` existed keeps its old shape under
        # CREATE TABLE IF NOT EXISTS, and the next INSERT would then fail on a column
        # that is missing from the table but present in the statement. Adding it in
        # place is the whole upgrade: a row that predates it has an empty shape, which
        # matches nothing, which is the conservative reading of "we did not record it".
        present = {row["name"] for row in db.execute("PRAGMA table_info(operations)")}
        if "route_shape" not in present:
            try:
                db.execute("ALTER TABLE operations ADD COLUMN route_shape TEXT NOT NULL "
                           "DEFAULT ''")
            except sqlite3.OperationalError as exc:
                # Between the read and the ALTER another process may have added it -
                # the same check-then-ALTER race the D-owned adapter has. Losing that
                # race means the column exists, which is what this wanted, so the
                # duplicate is the success case rather than an initialisation failure.
                if "duplicate column" not in str(exc).casefold():
                    raise
        if "execution_outcome" not in present:
            # Kept beside the money columns rather than folded into ``outcome``:
            # a successful call with an unknown charge and a failed call with an
            # unknown charge are different facts, and one column cannot hold both.
            try:
                db.execute("ALTER TABLE operations ADD COLUMN execution_outcome TEXT "
                           "NOT NULL DEFAULT ''")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).casefold():
                    raise
        db.execute("""
            CREATE TABLE IF NOT EXISTS caps (
                scope_kind TEXT NOT NULL, scope_key TEXT NOT NULL, window TEXT NOT NULL,
                period_key TEXT NOT NULL DEFAULT '',
                ceiling_micros INTEGER, ceiling_calls INTEGER, ceiling_seconds INTEGER,
                usd_enforced INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (scope_kind, scope_key, window)
            )""")
        cap_columns = {row["name"] for row in db.execute("PRAGMA table_info(caps)")}
        if "usd_enforced" not in cap_columns:
            # A database written before this column existed keeps its old caps. They
            # stay non-dollar ceilings rather than being retroactively read as a
            # promise nobody made.
            try:
                db.execute("ALTER TABLE caps ADD COLUMN usd_enforced INTEGER NOT NULL "
                           "DEFAULT 0")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).casefold():
                    raise
        db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT, at TEXT NOT NULL, kind TEXT NOT NULL,
                code TEXT NOT NULL DEFAULT '', detail_ref TEXT NOT NULL DEFAULT ''
            )""")

    def _event(self, db: sqlite3.Connection, operation_id: str, kind: str, *,
               code: str = "", detail_ref: str = "") -> None:
        db.execute("INSERT INTO events (operation_id, at, kind, code, detail_ref) "
                   "VALUES (:operation_id, :at, :kind, :code, :detail_ref)",
                   {"operation_id": operation_id, "at": self._now().isoformat(),
                    "kind": kind, "code": code, "detail_ref": detail_ref})

    # -- ceilings ---------------------------------------------------------- #

    @staticmethod
    def _period_key(window: str, moment: datetime) -> str:
        if window == "day":
            return moment.strftime("%Y-%m-%d")
        if window == "month":
            return moment.strftime("%Y-%m")
        return ""

    @staticmethod
    def _scope_where(scope_kind: str, scope_key: str) -> tuple[str, list[Any]]:
        """Translate a scope key into column predicates, never into a LIKE pattern.

        The operation scope accepts an exact operation signature, or ``<project>/*``
        for a ceiling on every operation of one project. A bare ``*`` is refused: it
        was reachable in an earlier version and meant "every project", so one
        project's ``set_cap`` silently became a global ceiling that another project's
        history could consume.
        """
        if scope_kind == "project":
            where, params = "project = ?", [scope_key]
        elif scope_kind == "operation":
            if scope_key == "*":
                raise LedgerRefused(
                    "INVALID_REQUEST",
                    "an operation ceiling must name its project as <project>/* or name one "
                    "operation signature; a bare '*' would bound every project at once")
            if scope_key.endswith("/*"):
                project = scope_key[:-2]
                if not ID.fullmatch(project):
                    raise LedgerRefused("INVALID_REQUEST",
                                        f"{scope_key!r} does not name a project")
                return "project = ?", [project]
            where, params = "op_signature = ?", [scope_key]
        elif scope_kind == "run":
            parts = scope_key.split("/")
            if len(parts) != 2:
                raise LedgerRefused("INVALID_REQUEST",
                                    "a run scope key is <project>/<run_id>")
            where, params = "project = ? AND run_id = ?", list(parts)
        elif scope_kind == "provider_account":
            parts = scope_key.split("/")
            if len(parts) != 3:
                raise LedgerRefused(
                    "INVALID_REQUEST",
                    "a provider account scope key is <project>/<provider>/<account_ref>")
            if parts[2] == "*":
                where, params = "project = ? AND provider = ?", parts[:2]
            else:
                where, params = "project = ? AND provider = ? AND account_ref = ?", list(parts)
        else:
            raise LedgerRefused("INVALID_REQUEST", f"unknown cap scope kind {scope_kind!r}")
        for part in params:
            if not ID.fullmatch(part):
                raise LedgerRefused("INVALID_REQUEST",
                                    f"{scope_key!r} is not a valid {scope_kind} scope key")
        return where, params

    def _aggregate(self, db: sqlite3.Connection, scope_kind: str, scope_key: str,
                   window: str, period: str) -> sqlite3.Row:
        """The totals for one scope and window, whether or not a ceiling was set there.

        Split out from :meth:`_usage_in` because a grant's own limits are checked
        against the same totals, and those exist even when no ceiling does.
        """
        where, params = self._scope_where(scope_kind, scope_key)
        return self._aggregate_where(db, where, params, window, period)

    def _aggregate_where(self, db: sqlite3.Connection, where: str, params: list[Any],
                         window: str, period: str) -> sqlite3.Row:
        """Totals for an arbitrary predicate - used by the account-wide campaign read."""
        if window == "day":
            where += " AND substr(created_at, 1, 10) = ?"
            params = [*params, period]
        elif window == "month":
            where += " AND substr(created_at, 1, 7) = ?"
            params = [*params, period]
        return db.execute(
            "SELECT COALESCE(SUM(CASE WHEN charge_state IN ('SETTLED','FREE_TIER') "
            "  THEN actual_micros ELSE 0 END), 0) AS spent,"
            " COALESCE(SUM(CASE WHEN charge_state = 'RESERVED' THEN reserved_micros ELSE 0 END)"
            "  , 0) AS reserved,"
            " COALESCE(SUM(CASE WHEN charge_state = 'UNKNOWN' THEN reserved_micros ELSE 0 END)"
            "  , 0) AS unknown,"
            " COALESCE(SUM(CASE WHEN charge_state <> 'NO_CHARGE_PROVEN'"
            "  AND counts_against_caps = 1 THEN 1 ELSE 0 END), 0) AS calls,"
            " COALESCE(SUM(CASE WHEN charge_state <> 'NO_CHARGE_PROVEN'"
            "  AND counts_against_caps = 1 THEN estimated_seconds ELSE 0 END), 0) AS seconds,"
            " COALESCE(SUM(CASE WHEN charge_state <> 'NO_CHARGE_PROVEN'"
            "  AND counts_against_caps = 1 THEN quota_units ELSE 0 END), 0) AS quota"
            f" FROM operations WHERE {where}", params).fetchone()

    def account_totals(self, provider: str, account_ref: str) -> AccountTotals:
        """What this ledger has committed on one provider account, across every project.

        Account-wide on purpose: a campaign floor belongs to the account, so a new
        run must not start with a fresh allowance - which is exactly the bypass
        §1.2 forbids. Other users of the same account are outside this ledger and
        are supplied to :class:`CampaignBudget` as external spend.
        """
        with self._connect() as db:
            return self._account_totals(db, provider, account_ref)

    @staticmethod
    def _account_totals(db: sqlite3.Connection, provider: str, account_ref: str) -> AccountTotals:
        row = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN charge_state IN ('SETTLED','FREE_TIER')"
            "  THEN actual_micros ELSE 0 END), 0) AS spent,"
            " COALESCE(SUM(CASE WHEN charge_state = 'RESERVED' THEN reserved_micros ELSE 0 END)"
            "  , 0) AS reserved,"
            " COALESCE(SUM(CASE WHEN charge_state = 'UNKNOWN' THEN reserved_micros ELSE 0 END)"
            "  , 0) AS unknown,"
            " COALESCE(SUM(CASE WHEN charge_state <> 'NO_CHARGE_PROVEN'"
            "  AND counts_against_caps = 1 THEN quota_units ELSE 0 END), 0) AS quota,"
            " COALESCE(SUM(CASE WHEN charge_state <> 'NO_CHARGE_PROVEN'"
            "  AND counts_against_caps = 1 THEN 1 ELSE 0 END), 0) AS calls"
            " FROM operations WHERE provider = ? AND account_ref = ?",
            (provider, account_ref)).fetchone()
        return AccountTotals(provider=provider, account_ref=account_ref,
                             settled_micros=row["spent"], reserved_micros=row["reserved"],
                             unknown_micros=row["unknown"], quota_units=row["quota"],
                             calls=row["calls"])

    def _usage_in(self, db: sqlite3.Connection, scope_kind: str, scope_key: str,
                  window: str, period: str) -> Usage | None:
        cap = db.execute("SELECT * FROM caps WHERE scope_kind=:kind AND scope_key=:key "
                         "AND window=:window",
                         {"kind": scope_kind, "key": scope_key, "window": window}).fetchone()
        if cap is None:
            return None
        row = self._aggregate(db, scope_kind, scope_key, window, period)
        return Usage(
            scope_kind=scope_kind, scope_key=scope_key, window=window, period_key=period,
            ceiling_micros=cap["ceiling_micros"], ceiling_calls=cap["ceiling_calls"],
            ceiling_seconds=cap["ceiling_seconds"], source=cap["source"],
            spent_micros=row["spent"], reserved_micros=row["reserved"],
            unknown_micros=row["unknown"], calls_used=row["calls"],
            seconds_used=row["seconds"], quota_units=row["quota"],
            usd_enforced=bool(cap["usd_enforced"]))

    def set_cap(self, scope_kind: str, scope_key: str, *, ceiling_micros: int | None = None,
                ceiling_calls: int | None = None, ceiling_seconds: int | None = None,
                window: str = "total", source: str,
                usd_enforced: bool = False, if_absent: bool = False) -> Usage:
        """State a ceiling, and name who stated it.

        A ceiling without a name is not readable later: "who allowed this run to
        spend 200 USD" has to be answerable from the ledger itself.

        ``usd_enforced`` is the operator's explicit statement that ``ceiling_micros``
        is a dollar promise rather than a number nobody relies on. Only then does
        ``reserve`` require a provable USD rate; every other ceiling remains a
        request/credit bound (the default deployment's mode).
        """
        self._scope_where(scope_kind, scope_key)
        if window not in WINDOWS:
            raise LedgerRefused("INVALID_REQUEST", f"unknown cap window {window!r}")
        if type(usd_enforced) is not bool:
            raise LedgerRefused("INVALID_REQUEST", "usd_enforced must be bool")
        if usd_enforced and ceiling_micros is None:
            raise LedgerRefused(
                "USD_ENFORCEMENT_REQUIRES_CEILING",
                "usd_enforced=True without ceiling_micros would claim a dollar bound "
                "that has no amount; state the ceiling or leave the flag off")
        if not isinstance(source, str) or not source.strip():
            raise LedgerRefused("CAP_SOURCE_REQUIRED",
                                "a ceiling must name the operator or policy that set it")
        if ceiling_micros is None and ceiling_calls is None and ceiling_seconds is None:
            raise LedgerRefused("CAP_HAS_NO_LIMIT",
                                "a ceiling row with no limit would read as an allowance")
        for name, value in (("ceiling_micros", ceiling_micros), ("ceiling_calls", ceiling_calls),
                            ("ceiling_seconds", ceiling_seconds)):
            if value is not None and (type(value) is not int or value < 0):
                raise LedgerRefused("INVALID_REQUEST", f"{name} must be a nonnegative integer")
        period = self._period_key(window, self._now())
        with self._connect(write=True) as db:
            if if_absent:
                existing = self._usage_in(db, scope_kind, scope_key, window, period)
                if existing is not None:
                    return existing
            # An operation ceiling bounds one operation, so past spend does not make
            # a lower value inconsistent; every other ceiling is cumulative and must
            # not be lowered below what is already held.
            current = self._usage_in(db, scope_kind, scope_key, window, period) \
                if scope_kind != "operation" else None
            committed = (current.spent_micros + current.held_micros) if current else 0
            if ceiling_micros is not None and ceiling_micros < committed:
                raise LedgerRefused(
                    "CAP_BELOW_HELD",
                    f"{scope_key} already holds {committed} micro-USD; lowering the ceiling below "
                    "that would leave the ledger in an unexplained overdraft")
            if ceiling_calls is not None and current and ceiling_calls < current.calls_used:
                raise LedgerRefused("CAP_BELOW_HELD",
                                    f"{scope_key} has already used {current.calls_used} calls")
            db.execute(
                "INSERT INTO caps (scope_kind, scope_key, window, period_key, ceiling_micros,"
                " ceiling_calls, ceiling_seconds, usd_enforced, source, updated_at)"
                " VALUES (:kind, :key, :window, :period, :money, :calls, :seconds,"
                " :usd_enforced, :source, :now)"
                " ON CONFLICT (scope_kind, scope_key, window) DO UPDATE SET"
                " ceiling_micros=excluded.ceiling_micros, ceiling_calls=excluded.ceiling_calls,"
                " ceiling_seconds=excluded.ceiling_seconds,"
                " usd_enforced=excluded.usd_enforced, source=excluded.source,"
                " updated_at=excluded.updated_at",
                {"kind": scope_kind, "key": scope_key, "window": window, "period": period,
                 "money": ceiling_micros, "calls": ceiling_calls, "seconds": ceiling_seconds,
                 "usd_enforced": int(usd_enforced),
                 "source": source.strip(), "now": self._now().isoformat()})
        return self.remaining(scope_kind, scope_key, window=window)

    def usage(self, scope_kind: str, scope_key: str, *, window: str | None = None
              ) -> tuple[Usage, ...]:
        with self._connect() as db:
            rows = db.execute("SELECT window FROM caps WHERE scope_kind=? AND scope_key=? "
                              "ORDER BY window", (scope_kind, scope_key)).fetchall()
            found = []
            for row in rows:
                if window is not None and row["window"] != window:
                    continue
                usage = self._usage_in(db, scope_kind, scope_key, row["window"],
                                       self._period_key(row["window"], self._now()))
                if usage is not None:
                    found.append(usage)
            return tuple(found)

    def remaining(self, scope_kind: str, scope_key: str, *, window: str = "total") -> Usage:
        """What is left under one ceiling. Raises when that ceiling was never set.

        Returning a zero would make "nobody configured this" and "this is exhausted"
        the same number, and a caller's normal reaction to zero is to stop and
        report a budget that was never approved.
        """
        self._scope_where(scope_kind, scope_key)
        with self._connect() as db:
            found = self._usage_in(db, scope_kind, scope_key, window,
                                   self._period_key(window, self._now()))
        if found is None:
            raise LedgerRefused(
                "CAP_NOT_CONFIGURED",
                f"no {window} ceiling is set for {scope_kind} {scope_key}; an unset ceiling is not "
                "an unlimited one")
        return found

    # -- reading ----------------------------------------------------------- #

    @staticmethod
    def _operation(row: sqlite3.Row) -> Operation:
        values = {name: row[name] for name in _OPERATION_COLUMNS}
        values["price_verified"] = bool(values["price_verified"])
        values["counts_against_caps"] = bool(values["counts_against_caps"])
        return Operation(**values)

    def get(self, operation_id: str) -> Operation | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM operations WHERE operation_id=?",
                             (operation_id,)).fetchone()
        return None if row is None else self._operation(row)

    @staticmethod
    def _require(db: sqlite3.Connection, operation_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM operations WHERE operation_id=?",
                         (operation_id,)).fetchone()
        if row is None:
            raise LedgerRefused("UNKNOWN_OPERATION",
                                f"no operation {operation_id!r} in the ledger")
        return row

    def operations(self, *, project: str | None = None, run_id: str | None = None
                   ) -> tuple[Operation, ...]:
        where, params = [], []
        if project is not None:
            where.append("project = ?")
            params.append(project)
        if run_id is not None:
            where.append("run_id = ?")
            params.append(run_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM operations{clause} ORDER BY created_at, operation_id",
                params).fetchall()
        return tuple(self._operation(row) for row in rows)

    def open_operations(self, *, project: str | None = None, run_id: str | None = None
                        ) -> tuple[Operation, ...]:
        """Everything still holding budget - the reconciliation entry point.

        Holds are never released automatically, so there are always rows that stop
        in ``RESERVED`` or ``UNKNOWN``. Without this reading, "how much is still
        hanging" could only be answered by opening the database by hand.
        """
        return tuple(row for row in self.operations(project=project, run_id=run_id)
                     if row.charge_state in ("RESERVED", "UNKNOWN"))

    def events(self, operation_id: str | None = None) -> tuple[dict, ...]:
        with self._connect() as db:
            if operation_id is None:
                rows = db.execute("SELECT * FROM events ORDER BY seq").fetchall()
            else:
                rows = db.execute("SELECT * FROM events WHERE operation_id=? ORDER BY seq",
                                  (operation_id,)).fetchall()
        return tuple(dict(row) for row in rows)

    def recover(self, *, project: str | None = None) -> dict:
        """What a crash left behind. A reading, never a cleanup."""
        open_rows = self.open_operations(project=project)
        return {
            "schema": "marketops.usage.recovery.v1",
            "open_operations": len(open_rows),
            "dispatched_unsettled": sum(1 for row in open_rows if row.dispatch_intent_at),
            "never_dispatched": sum(1 for row in open_rows if not row.dispatch_intent_at),
            "unknown_exposure_micros": sum(row.held_micros for row in open_rows),
            "operation_ids": [row.operation_id for row in open_rows],
            "action_taken": "REPORT_ONLY",
            "note": ("Holds are not released here. An operation that may have reached the provider "
                     "is closed with evidence, not with a timeout."),
        }

    def report(self, project: str, *, accepted_outputs: int | None = None) -> dict:
        """Attempts, states, three-way money, quota units, per provider, per price basis."""
        rows = self.operations(project=project)
        states = {state.value: 0 for state in ChargeState}
        money = {"estimated_micros": 0, "actual_micros": 0, "reserved_micros": 0,
                 "unknown_exposure_micros": 0}
        quota: dict[str, int] = {}
        providers: dict[str, dict[str, int]] = {}
        evidence = {"verified": 0, "unverified": 0}
        by_unknown_reason: dict[str, int] = {}
        by_execution_outcome: dict[str, int] = {}
        legacy = 0
        legacy_unverified_zeros = 0
        for row in rows:
            historical_zero = row.actual_micros == 0 and display_actual_micros(row) is None
            legacy_unverified_zeros += int(historical_zero)
            states[row.charge_state] = states.get(row.charge_state, 0) + 1
            if row.charge_state == "UNKNOWN":
                by_unknown_reason[row.outcome] = by_unknown_reason.get(row.outcome, 0) + 1
            if row.execution_outcome:
                by_execution_outcome[row.execution_outcome] = (
                    by_execution_outcome.get(row.execution_outcome, 0) + 1)
            money["estimated_micros"] += row.estimated_micros
            if row.charge_state in ("SETTLED", "FREE_TIER"):
                money["actual_micros"] += row.actual_micros or 0
            elif row.charge_state == "RESERVED":
                money["reserved_micros"] += row.reserved_micros
            elif row.charge_state == "UNKNOWN":
                money["unknown_exposure_micros"] += row.reserved_micros
            if row.quota_units and row.charge_state != "NO_CHARGE_PROVEN":
                quota[row.quota_unit] = quota.get(row.quota_unit, 0) + row.quota_units
            bucket = providers.setdefault(row.provider, {
                "operations": 0, "estimated_micros": 0, "actual_micros": 0,
                "unknown_exposure_micros": 0, "legacy_unverified_zero_operations": 0,
                "actual_usd_status": "KNOWN"})
            if (row.charge_state in {"UNKNOWN", "RESERVED"} or historical_zero
                    or (not row.price_verified and row.actual_source == "DERIVED")):
                bucket["actual_usd_status"] = "UNKNOWN"
            bucket["legacy_unverified_zero_operations"] += int(historical_zero)
            bucket["operations"] += 1
            bucket["estimated_micros"] += row.estimated_micros
            if row.charge_state in ("SETTLED", "FREE_TIER"):
                bucket["actual_micros"] += row.actual_micros or 0
            if row.charge_state == "UNKNOWN":
                bucket["unknown_exposure_micros"] += row.reserved_micros
            evidence["verified" if row.price_verified else "unverified"] += 1
            if row.origin == "legacy":
                legacy += 1
        # Whether the money column can be read as money at all. Two things make it
        # indeterminate, and both are ordinary rather than exceptional: a settled row
        # whose rate nobody verified, and exposure nobody has reconciled yet. A derived
        # figure produced anyway - "cost per accepted output: 0" - is the one number a
        # reader would take at face value, so it is withheld instead of printed.
        basis_code = "OK"
        if money["unknown_exposure_micros"] > 0 or states.get("UNKNOWN", 0) > 0:
            basis_code = "UNKNOWN_COST_OUTSTANDING"
        elif evidence["unverified"] > 0:
            basis_code = "PRICE_UNVERIFIED"
        elif states[ChargeState.SETTLED.value] + states[ChargeState.FREE_TIER.value] == 0:
            # Nothing has been closed yet, so there is no cost to average - and an
            # empty ledger divided by a non-empty denominator is exactly how "we know
            # nothing" prints as "$0.00 per accepted output".
            basis_code = "NO_SETTLED_OPERATIONS"
        basis = "VERIFIED_ACTUAL" if basis_code == "OK" else "INDETERMINATE"
        money["actual_usd_status"] = "KNOWN" if basis_code == "OK" else "UNKNOWN"
        money["actual_total_micros"] = money["actual_micros"] if basis_code == "OK" else None
        money["legacy_unverified_zero_operations"] = legacy_unverified_zeros
        if legacy_unverified_zeros:
            money["recorded_actual_micros"] = money["actual_micros"]
            money["actual_micros"] = None
        for bucket in providers.values():
            bucket["actual_total_micros"] = (bucket["actual_micros"]
                                             if bucket["actual_usd_status"] == "KNOWN" else None)
            if bucket["legacy_unverified_zero_operations"]:
                bucket["recorded_actual_micros"] = bucket["actual_micros"]
                bucket["actual_micros"] = None
                bucket["actual_usd_status"] = "UNKNOWN"
        notes = [
            "A local reservation or a settled estimate is not a vendor invoice.",
            "Money is integer micro-USD; quota units are counted separately and are never "
            "converted into money.",
            "UNKNOWN is exposure awaiting reconciliation, not a free call.",
            "Cost per accepted output is reported only when the money behind it can be read "
            "as money: a verified rate on every settled row, and no unreconciled exposure.",
        ]
        if basis_code != "OK":
            reasons = {
                "UNKNOWN_COST_OUTSTANDING": "spend is outstanding that nobody has "
                                            "reconciled, so the total is not knowable yet",
                "PRICE_UNVERIFIED": "no verified rate backs the dollar figures, so they "
                                    "are not dollars",
                "NO_SETTLED_OPERATIONS": "nothing has settled, so there is no cost to "
                                         "average",
            }
            notes.append(f"Cost per accepted output is withheld ({basis_code}): "
                         + reasons[basis_code])
        return {
            "schema": "marketops.usage.ledger-report.v1",
            "project": project,
            "generated_at": self._now().isoformat(),
            "operations": len(rows),
            "legacy_operations": legacy,
            "by_charge_state": states,
            "by_unknown_reason": by_unknown_reason,
            "by_execution_outcome": by_execution_outcome,
            "money": {**money, "basis": basis, "basis_code": basis_code},
            "quota_units": quota,
            "by_provider": providers,
            "price_evidence": evidence,
            "cost_per_accepted_output_micros": (
                money["actual_micros"] // accepted_outputs
                if accepted_outputs and basis_code == "OK" else None),
            "cost_per_accepted_output_basis": basis_code,
            "accepted_outputs": accepted_outputs,
            "notes": notes,
        }

    # -- writing ----------------------------------------------------------- #

    @staticmethod
    def _validate(request: OperationRequest) -> None:
        for name in ("project", "run_id", "operation_id", "provider", "capability",
                     "account_ref", "transport"):
            value = getattr(request, name)
            if not isinstance(value, str) or not ID.fullmatch(value):
                raise LedgerRefused("INVALID_REQUEST",
                                    f"{name} must be a short opaque identifier")
        if not isinstance(request.intent, str) or not request.intent.strip():
            raise LedgerRefused("INVALID_REQUEST", "intent must say what the operation is for")
        if len(request.intent) > 500:
            raise LedgerRefused("INVALID_REQUEST", "intent must be at most 500 characters")
        if type(request.units) is not int or request.units < 1:
            raise LedgerRefused("INVALID_REQUEST", "units must be a positive integer")
        if type(request.estimated_seconds) is not int or request.estimated_seconds < 0:
            raise LedgerRefused("INVALID_REQUEST",
                                "estimated_seconds must be a nonnegative integer")
        if not isinstance(request.args, Mapping):
            raise LedgerRefused("INVALID_REQUEST", "args must be a mapping")
        if request.site and not ID.fullmatch(request.site):
            raise LedgerRefused("INVALID_REQUEST", "site must be a short opaque label")
        if request.quota_unit and request.quota_unit not in QUOTA_UNITS:
            raise LedgerRefused(
                "QUOTA_UNIT_UNREVIEWED",
                f"{request.quota_unit!r} is not a reviewed quota unit "
                f"(expected one of {sorted(QUOTA_UNITS)}); a free-form label here would let a "
                "report file money-looking numbers under quota")

    def _verified_grant(self, request: OperationRequest, tier: grants.Tier) -> grants.Grant:
        if not request.grant:
            raise LedgerRefused(
                "GRANT_REQUIRED",
                f"tier T{int(tier)} is closed until a grant covering this operation is presented")
        try:
            verified = self._grants.verify(
                request.grant, project=request.project, run_id=request.run_id,
                provider=request.provider, capability=request.capability,
                account_ref=request.account_ref, tier=tier, site=request.site,
                now=self._now())
        except grants.GrantRefused as exc:
            raise LedgerRefused(exc.code, exc.message) from None
        return verified

    def reserve(self, request: OperationRequest) -> Operation:
        """Hold budget and quota before dispatch. Returns the reservation.

        Raises:
            LedgerRefused: with a code naming which gate closed.
        """
        self._validate(request)
        try:
            capability = self._registry.require(request.provider, request.capability)
        except grants.GrantRefused as exc:
            raise LedgerRefused(exc.code, exc.message) from None
        if not capability.allows_transport(request.transport):
            raise LedgerRefused(
                "TRANSPORT_NOT_REVIEWED",
                f"{request.transport} is not an allowed transport for "
                f"{request.provider}/{request.capability}; an unreviewed transport on a reviewed "
                "capability is how a second, ungoverned path gets used by accident")
        if capability.tier is grants.Tier.T4 and not request.site:
            raise LedgerRefused(
                "SITE_REQUIRED",
                "a T4 operation must name the site it touches; the tier exists for site-scoped "
                "work and an unnamed site cannot be matched against a grant")
        price = self._pricing.price_for(request.provider, request.capability)
        if price is None:
            raise LedgerRefused(
                "PRICE_UNKNOWN",
                f"no recorded price for {request.provider}/{request.capability}; an unpriced "
                "operation is refused rather than estimated")
        try:
            plain_args = _plain(request.args)
            _canonical_json(plain_args)
        except _NotCanonical as exc:
            raise LedgerRefused("ARGS_NOT_CANONICAL", str(exc)) from None
        signature = op_signature(request, plain_args)
        if (request.provider, request.capability) in RETRYABLE_FREE_FETCHES:
            # A page fetch that costs no vendor money (local Crawl4AI; TinyFish
            # fetch, which does not touch the wallet) may be retried under a new
            # operation id: a host timeout otherwise leaves that page unreachable
            # for the rest of the Run. Reusing an id is still one operation, and
            # every retry still counts against the caps.
            signature = sha256(f"{signature}|{request.operation_id}".encode()).hexdigest()
        shape = route_shape(request, plain_args)
        fingerprint = intent_fingerprint(request)
        estimated = price.unit_micros * request.units
        quota_unit = request.quota_unit or price.quota_unit
        quota_units = price.quota_per_call * request.units
        verified_grant = None
        grant_ref = ""
        if capability.tier.needs_grant or request.grant:
            # A grant presented below its tier is still verified and recorded: an
            # unverifiable token sitting in a request is a caller bug worth surfacing.
            verified_grant = self._verified_grant(request, capability.tier)
            grant_ref = verified_grant.grant_id
        now = self._now().isoformat()
        with self._connect(write=True) as db:
            existing = db.execute("SELECT op_signature FROM operations WHERE operation_id=?",
                                  (request.operation_id,)).fetchone()
            if existing is not None and existing["op_signature"] != signature:
                raise LedgerRefused(
                    "OPERATION_ID_COLLISION",
                    f"operation_id {request.operation_id!r} already names a different operation")
            # Key B, across transports *and* across runs: the same provider
            # operation reached over a second transport is one spend opportunity,
            # not two. The signature already excludes the transport, so this only
            # has to look beyond the run — which is exactly what a second route to
            # one vendor account does (`dual_search_mcp` REST versus the embedded
            # official MCP). Run-scoped dedup alone cannot see it, because the
            # second caller is a different run by construction.
            # Only rows that count against caps are a *live* route claim. An
            # imported legacy row is a receipt of something that already
            # happened; letting it block today's transport choice would make
            # history a policy, and the migration's whole contract is that it
            # preserves history without changing what may run.
            crossing = db.execute(
                "SELECT operation_id, transport FROM operations "
                "WHERE project=? AND route_shape=? AND charge_state<>? AND transport<>? "
                "AND counts_against_caps=1 AND origin<>'legacy' ORDER BY created_at LIMIT 1",
                (request.project, shape, "NO_CHARGE_PROVEN",
                 request.transport)).fetchone()
            if crossing is not None:
                raise LedgerRefused(
                    "DUPLICATE_OPERATION_SIGNATURE",
                    f"provider {request.provider} is already metered as "
                    f"{crossing['operation_id']} over {crossing['transport']}; a second "
                    "transport reaches the same vendor account and is a route change, "
                    "not a second budget")
            # An attempt refused before it was sent (NO_CHARGE_PROVEN) holds no claim:
            # a live Run asked for "places", was refused for the mode name, and could
            # never run that query again after the cause was fixed.
            duplicate = db.execute(
                "SELECT operation_id, transport, charge_state FROM operations "
                "WHERE project=? AND run_id=? AND op_signature=? AND charge_state<>'NO_CHARGE_PROVEN'",
                (request.project, request.run_id, signature)).fetchone()
            if duplicate is not None:
                raise LedgerRefused(
                    "DUPLICATE_OPERATION_SIGNATURE",
                    f"this operation is already recorded as {duplicate['operation_id']} over "
                    f"{duplicate['transport']} in state {duplicate['charge_state']}; a second "
                    "transport is not a second operation")
            prior = db.execute(
                "SELECT operation_id FROM operations WHERE project=? AND run_id=? "
                "AND intent_fingerprint=? AND provider<>? AND charge_state<>'NO_CHARGE_PROVEN' "
                "ORDER BY created_at LIMIT 1",
                (request.project, request.run_id, fingerprint, request.provider)).fetchone()
            escalates_from = ""
            if prior is not None:
                if not request.escalation_reason.strip():
                    raise LedgerRefused(
                        "CROSS_PROVIDER_ESCALATION_UNJUSTIFIED",
                        "another provider already serves this intent in this run; state an "
                        "escalation reason, because this is a second expense, not a retry")
                escalates_from = prior["operation_id"]
            self._check_caps(db, request, signature, estimated, request.estimated_seconds,
                             grant=verified_grant, quota_units=quota_units,
                             quota_unit=quota_unit, price=price)
            db.execute(
                "INSERT INTO operations (operation_id, project, run_id, intent_fingerprint,"
                " op_signature, provider, account_ref, capability, meter, transport, site,"
                " route_shape, tier,"
                " estimated_micros, reserved_micros, actual_micros, actual_source, charge_state,"
                " quota_unit, quota_units, estimated_seconds, outcome, upstream_ref, grant_ref,"
                " escalates_from, price_source, price_version, price_observed_at, price_verified,"
                " origin, legacy_ref, counts_against_caps, created_at, dispatch_intent_at,"
                " settled_at, reconciled_at) VALUES (:operation_id, :project, :run_id,"
                " :intent_fingerprint, :op_signature, :provider, :account_ref, :capability,"
                " :meter, :transport, :site, :route_shape, :tier, :estimated_micros,"
                " :reserved_micros, NULL,"
                " 'NONE', 'RESERVED', :quota_unit, :quota_units, :estimated_seconds, '',"
                " :upstream_ref, :grant_ref, :escalates_from, :price_source, :price_version,"
                " :price_observed_at, :price_verified, 'native', NULL, 1, :created_at, NULL, NULL,"
                " NULL)",
                {"operation_id": request.operation_id, "project": request.project,
                 "run_id": request.run_id, "intent_fingerprint": fingerprint,
                 "op_signature": signature, "provider": request.provider,
                 "account_ref": request.account_ref, "capability": request.capability,
                 "meter": capability.meter, "transport": request.transport,
                 "site": request.site, "route_shape": shape,
                 "tier": int(capability.tier),
                 "estimated_micros": estimated,
                 "reserved_micros": estimated, "quota_unit": quota_unit,
                 "quota_units": quota_units, "estimated_seconds": request.estimated_seconds,
                 "upstream_ref": "", "grant_ref": grant_ref, "escalates_from": escalates_from,
                 "price_source": price.source, "price_version": price.version,
                 "price_observed_at": price.observed_at,
                 "price_verified": int(price.verified), "created_at": now})
            self._event(db, request.operation_id, "reserve", code=capability.effect,
                        detail_ref=grant_ref)
        operation = self.get(request.operation_id)
        if operation is None:  # pragma: no cover - the insert above committed
            raise LedgerRefused("INTERNAL", "reservation did not persist")
        return operation

    def _check_caps(self, db: sqlite3.Connection, request: OperationRequest, signature: str,
                    estimated_micros: int, estimated_seconds: int,
                    grant: grants.Grant | None = None, quota_units: int = 0,
                    quota_unit: str = "", price: Price | None = None) -> None:
        """Every applicable ceiling must have room; the run ceiling must exist.

        Only ceilings that were *set* are enforced, so a project-period ceiling
        stays optional exactly as documented. The run ceiling is required: a run
        with no ceiling at all is the state this ledger refuses to start in, and
        "nobody set one" must not read as "no limit".

        A presented grant's own limits are checked here too, in the same transaction
        as the ceilings. A grant that names an amount or a call count is doing work
        only if something consults it; minting ``max_micros`` and never reading it
        would make the field a decoration.

        The campaign floor is the third check, and it is the only one that is
        **account-wide**: §1.2 requires it to count what other runs and other users
        have spent, so a fresh run cannot start with a fresh allowance. It runs here,
        inside the same ``BEGIN IMMEDIATE`` transaction as the insert, which is what
        makes concurrent reservations stop at the floor instead of racing past it.
        """
        import research_spending
        research_spending.enforce(db, request, price, estimated_micros,
                                  self._path.parent.parent)
        if self._campaign and (estimated_micros > 0 or quota_units > 0):
            # "Potentially charged" is not the same as "costs money". The catalog this
            # plugin ships prices every capability at zero dollars with a real credit
            # unit, because no verified dollar rate exists for this checkout - so a
            # money-only test would leave the campaign floor unreachable in exactly the
            # deployment that ships. A credit is a charge.
            self._check_campaign_floor(db, request, estimated_micros, quota_units, quota_unit,
                                       price)
        if grant is not None and (grant.max_calls or grant.max_micros):
            totals = self._aggregate(db, "run", f"{request.project}/{request.run_id}", "total", "")
            already = totals["spent"] + totals["reserved"] + totals["unknown"]
            if grant.max_calls and totals["calls"] + 1 > grant.max_calls:
                raise LedgerRefused(
                    "GRANT_CALL_LIMIT_EXCEEDED",
                    f"grant {grant.grant_id} allows {grant.max_calls} calls in this run; "
                    f"{totals['calls']} have already been used")
            if grant.max_micros and already + estimated_micros > grant.max_micros:
                raise LedgerRefused(
                    "GRANT_AMOUNT_EXCEEDED",
                    f"grant {grant.grant_id} allows {grant.max_micros} micro-USD in this run; "
                    f"{already} is committed and this operation would reserve "
                    f"{estimated_micros} more")
        scopes = [
            ("operation", signature), ("operation", f"{request.project}/*"),
            ("run", f"{request.project}/{request.run_id}"),
            ("provider_account", f"{request.project}/{request.provider}/{request.account_ref}"),
            ("provider_account", f"{request.project}/{request.provider}/*"),
            ("project", request.project),
        ]
        run_scope = ("run", f"{request.project}/{request.run_id}")
        seen: set[tuple[str, str]] = set()
        caps: list[tuple[str, str, sqlite3.Row]] = []
        for kind, key in scopes:
            if (kind, key) in seen:
                continue
            seen.add((kind, key))
            rows = db.execute("SELECT * FROM caps WHERE scope_kind=? AND scope_key=?",
                              (kind, key)).fetchall()
            if not rows and (kind, key) == run_scope:
                raise LedgerRefused(
                    "CAP_NOT_CONFIGURED",
                    f"no ceiling is set for run {key}; an unset ceiling is not an unlimited one")
            caps.extend((kind, key, row) for row in rows)
        # An explicit USD ceiling is only a bound if this operation has a rate the
        # ledger can multiply by its units *before* dispatch. Checking the rate
        # here, before the money comparison, is what stops a placeholder zero from
        # reading as "reserve US$0.00" and leaving a dollar promise unenforced.
        usd_caps = [(kind, key, cap) for kind, key, cap in caps if cap["usd_enforced"]]
        if usd_caps:
            provable, basis = usd_cap_basis(price)
            if not provable:
                kind, key, cap = usd_caps[0]
                raise LedgerRefused(
                    "PRICE_UNKNOWN",
                    f"{request.provider}/{request.capability} has no provable dollar bound "
                    f"({basis}); the explicit USD ceiling at {kind} {key} "
                    f"({cap['ceiling_micros']} micro-USD) cannot be enforced without one, so "
                    "the operation is refused rather than promised protection it does not have",
                    dimension="money")
        for kind, key, cap in caps:
            found = self._usage_in(db, kind, key, cap["window"],
                                   self._period_key(cap["window"], self._now()))
            if found is None:  # pragma: no cover - the row came from this table
                continue
            window = cap["window"]
            where = f"{kind} {key}"
            # The operation ceiling bounds **this** operation; every other ceiling
            # bounds the accumulated total for its scope. Comparing an operation
            # ceiling against accumulated spend would make it a second, smaller run
            # ceiling, and after enough spend it would refuse every operation
            # regardless of its own size - which is not what "per operation" means.
            per_operation = kind == "operation"
            calls = 1 if per_operation else found.calls_used + 1
            money = estimated_micros if per_operation else \
                found.spent_micros + found.held_micros + estimated_micros
            seconds = estimated_seconds if per_operation else \
                found.seconds_used + estimated_seconds
            # The contract's vocabulary names the *scope* a ceiling belongs to, so the
            # refusal is raised with that name and the dimension alongside it. QA round
            # 5 measured the alternative: the ledger said CALL_CAP_EXCEEDED, the
            # contract's closed set said BUDGET_RUN_CEILING, the intersection of the two
            # vocabularies was one code, and a governed refusal arrived at the host as
            # an unhandled ContractViolation.
            ceiling_code = _CEILING_CODE[kind]
            if found.ceiling_calls is not None and calls > found.ceiling_calls:
                raise LedgerRefused(
                    ceiling_code,
                    f"{where} allows {found.ceiling_calls} calls in its {window} window; "
                    f"{found.calls_used} have already been used", dimension="calls")
            if found.ceiling_micros is not None and money > found.ceiling_micros:
                raise LedgerRefused(
                    ceiling_code,
                    f"{where} allows {found.ceiling_micros} micro-USD per {window} window "
                    f"({'this operation' if per_operation else 'accumulated'}); this operation "
                    f"would reserve {estimated_micros} and the total would be {money}",
                    dimension="money")
            if found.ceiling_seconds is not None and seconds > found.ceiling_seconds:
                raise LedgerRefused(
                    ceiling_code,
                    f"{where} allows {found.ceiling_seconds} seconds per {window} window "
                    f"({'this operation' if per_operation else 'accumulated'}); this operation "
                    f"estimates {estimated_seconds} and the total would be {seconds}",
                    dimension="seconds")

    def campaign_status(self) -> BudgetDecision:
        """Whether this ledger can judge a campaign floor at all.

        A ledger with no budget runs every chargeable operation without one, which is
        a deliberate opt-out rather than a default - and an opt-out nobody can see is
        indistinguishable from an oversight. This is the reading that makes the state
        legible from the entry point the call sites actually use
        (:func:`for_root`), instead of leaving it to be inferred from an empty
        mapping by whoever thinks to look.
        """
        if not self._campaign:
            return BudgetDecision(
                BudgetVerdict.NOT_CAPTURED, "CAMPAIGN_NOT_CONFIGURED", 0, 0, 0,
                "no campaign floor is wired into this ledger: every chargeable operation is "
                "running without one. Capture each account's B0, or record the omission as a "
                "deliberate decision")
        accounts = ", ".join(sorted(f"{provider}/{account}" for provider, account
                                    in self._campaign))
        return BudgetDecision(BudgetVerdict.AUTHORIZED, "OK", 0, 0, 0,
                              f"campaign floors are wired for {len(self._campaign)} "
                              f"account(s): {accounts}")

    def _check_campaign_floor(self, db: sqlite3.Connection, request: OperationRequest,
                              estimated_micros: int, quota_units: int,
                              quota_unit: str = "", price: Price | None = None) -> None:
        """Admit a chargeable operation against the account's frozen campaign floor.

        Fails closed in the four ways §1.6 names. A chargeable operation on an
        account with no captured floor is refused rather than allowed: campaign
        enforcement is on, and "we did not check this account" is not a pass.
        """
        budget = self._campaign.get((request.provider, request.account_ref))
        if budget is None:
            raise LedgerRefused(
                "CAMPAIGN_NOT_CAPTURED",
                f"no campaign floor is captured for {request.provider}/{request.account_ref}, "
                "so this chargeable operation cannot be admitted against one; capture the "
                "account's B0 or leave campaign enforcement off deliberately")
        totals = self._account_totals(db, request.provider, request.account_ref)
        if budget.meter == USD_MICROS:
            # A dollar floor can only judge a call whose dollar price is known. This
            # plugin's own default catalog prices every capability at zero dollars with
            # a real credit unit, so without this check the floor is compared against a
            # number that never moves and a wallet floor admits unlimited work.
            if price is not None and not _usd_price_is_known(price):
                raise LedgerRefused(
                    "CAMPAIGN_PRICE_UNKNOWN_FAIL_CLOSED",
                    f"{request.provider}/{request.capability} has no known dollar rate "
                    f"(unit_micros={price.unit_micros}, free_tier={price.free_tier}, "
                    f"verified={price.verified}), so a US$ floor cannot judge it; an unknown "
                    "charge is not a free one. Price the capability, or capture the floor in "
                    f"the meter it is actually billed in ({quota_unit!r})")
            decision = budget.admit(spend_units=estimated_micros,
                                    spent_units=totals.committed_micros,
                                    unknown_units=totals.unknown_micros, now=self._now())
        else:
            # A credit/quota meter. Two things this branch has to get right:
            #
            # * the units must be the same unit, or the comparison is between
            #   different things. An account whose floor was captured in credits
            #   cannot judge a call billed in tokens, and guessing would be the unit
            #   conflation this ledger keeps separate columns to prevent.
            # * the ledger's quota sum already carries this account's reserved and
            #   unknown operations, so counting them again as unknown would
            #   double-charge the floor.
            if quota_unit != budget.meter:
                raise LedgerRefused(
                    "CAMPAIGN_METER_MISMATCH",
                    f"the campaign floor for {request.provider}/{request.account_ref} is in "
                    f"{budget.meter!r} but this operation is billed in {quota_unit!r}; a floor "
                    "in one unit cannot judge a call in another, so this is refused rather "
                    "than estimated")
            decision = budget.admit(spend_units=quota_units, spent_units=totals.quota_units,
                                    unknown_units=0, now=self._now())
        if not decision.allowed:
            raise LedgerRefused(decision.code, f"{decision.verdict.value}: {decision.detail}")

    def _transition(self, operation_id: str, *, kinds: tuple[str, ...], changes: Mapping[str, Any],
                    event: str, code: str = "", detail_ref: str = "",
                    guard: Callable[[sqlite3.Row], None] | None = None) -> Operation:
        with self._connect(write=True) as db:
            row = self._require(db, operation_id)
            if row["charge_state"] not in kinds:
                raise LedgerRefused(
                    "ALREADY_TERMINAL" if row["charge_state"] in TERMINAL_STATES
                    else "WRONG_STATE",
                    f"{operation_id} is {row['charge_state']}; this verb needs "
                    f"{' or '.join(kinds)}")
            if guard is not None:
                guard(row)
            assignments = ", ".join(f"{name}=:{name}" for name in changes)
            db.execute(f"UPDATE operations SET {assignments} WHERE operation_id=:operation_id",
                       {**changes, "operation_id": operation_id})
            self._event(db, operation_id, event, code=code, detail_ref=detail_ref)
        operation = self.get(operation_id)
        if operation is None:  # pragma: no cover
            raise LedgerRefused("INTERNAL", "transition did not persist")
        return operation

    def mark_dispatch_intent(self, operation_id: str) -> Operation:
        """Record that the outbound call is about to happen. Idempotent.

        Deliberately separate from :meth:`reserve`, and the caller runs it *before*
        it reaches the network. That ordering is what makes a crash conservative:
        dying between here and the socket leaves an intent, so reconciliation treats
        the call as possibly charged instead of writing it off. The other order
        would make every crash look free.
        """
        with self._connect(write=True) as db:
            row = self._require(db, operation_id)
            if row["dispatch_intent_at"]:
                return self._operation(row)
            if row["charge_state"] != "RESERVED":
                raise LedgerRefused("NOT_RESERVED",
                                    f"{operation_id} is {row['charge_state']}, not a live hold")
            db.execute("UPDATE operations SET dispatch_intent_at=? WHERE operation_id=?",
                       (self._now().isoformat(), operation_id))
            self._event(db, operation_id, "dispatch_intent")
        operation = self.get(operation_id)
        if operation is None:  # pragma: no cover
            raise LedgerRefused("INTERNAL", "dispatch intent did not persist")
        return operation

    @staticmethod
    def _guard_evidence(source: ActualSource, reference: str, *, what: str) -> None:
        """A number has to be attributable. Otherwise "settled at 0" is an assertion.

        A provider-reported zero and a provider-reported charge are the same kind of
        claim, so both need the provider record they came from: without it the row is
        indistinguishable from a caller deciding an inconvenient hold was free, and
        the audit trail has nothing to check it against.
        """
        if source is ActualSource.PROVIDER_REPORTED and not reference:
            raise LedgerRefused(
                "EVIDENCE_REQUIRED",
                f"a provider-reported {what} must name the provider record it came from; a settle "
                "without one releases a dispatched hold on the caller's word alone")

    def settle(self, operation_id: str, *, actual_micros: int,
               actual_source: ActualSource | str, upstream_ref: str = "",
               quota_units: int | None = None) -> Operation:
        """Book what it actually cost, and where that number came from.

        Needs a dispatch intent first. A settle without one means the row claims a
        call happened that the ledger was never told about, and that is exactly the
        state ``prove_no_charge`` must not be reachable from - so the two are kept
        apart by construction here rather than by convention.
        """
        source = _actual_source(actual_source)
        if source in (None, ActualSource.NONE):
            raise LedgerRefused("ACTUAL_SOURCE_REQUIRED",
                                "a settled amount must say whether the provider reported it or it "
                                "was derived; NONE is what an unsettled row carries")
        if type(actual_micros) is not int or actual_micros < 0:
            raise LedgerRefused("INVALID_REQUEST", "actual_micros must be a nonnegative integer")
        reference = _reference(upstream_ref, "upstream_ref")
        self._guard_evidence(source, reference, what="amount")
        changes: dict[str, Any] = {
            "charge_state": ChargeState.SETTLED.value, "actual_micros": actual_micros,
            "actual_source": source.value, "settled_at": self._now().isoformat(),
            "outcome": "SETTLED", "upstream_ref": reference,
        }
        if quota_units is not None:
            if type(quota_units) is not int or quota_units < 0:
                raise LedgerRefused("INVALID_REQUEST", "quota_units must be nonnegative")
            changes["quota_units"] = quota_units
        return self._transition(operation_id, kinds=("RESERVED",), changes=changes,
                                event="settle", code=source.value, detail_ref=reference,
                                guard=self._guard_dispatched)

    def settle_free_tier(self, operation_id: str, *, quota_units: int,
                         upstream_ref: str = "") -> Operation:
        """Close a call the recorded rate says is free - and only if it says so."""
        current = self.get(operation_id)
        if current is None:
            raise LedgerRefused("UNKNOWN_OPERATION", f"no operation {operation_id!r} in the ledger")
        price = self._pricing.price_for(current.provider, current.capability)
        if price is None or not price.free_tier:
            raise LedgerRefused(
                "FREE_TIER_CONTRADICTS_RATE",
                f"{current.provider}/{current.capability} has no recorded free rate; a call is not "
                "free because a caller says it was")
        if type(quota_units) is not int or quota_units < 0:
            raise LedgerRefused("INVALID_REQUEST", "quota_units must be nonnegative")
        reference = _reference(upstream_ref, "upstream_ref")
        return self._transition(
            operation_id, kinds=("RESERVED",),
            changes={"charge_state": ChargeState.FREE_TIER.value, "actual_micros": 0,
                     "actual_source": ActualSource.DERIVED.value, "quota_units": quota_units,
                     "settled_at": self._now().isoformat(), "outcome": "FREE_TIER",
                     "upstream_ref": reference},
            event="settle_free_tier", code="FREE_TIER", detail_ref=reference,
            guard=self._guard_dispatched)

    @staticmethod
    def _guard_no_dispatch(row: sqlite3.Row) -> None:
        if row["dispatch_intent_at"]:
            raise LedgerRefused(
                "CANNOT_PROVE_NO_CHARGE_AFTER_DISPATCH",
                f"{row['operation_id']} was dispatched at {row['dispatch_intent_at']}; whether "
                "the provider charged it is not knowable from here")

    @staticmethod
    def _guard_dispatched(row: sqlite3.Row) -> None:
        if not row["dispatch_intent_at"]:
            raise LedgerRefused(
                "NOT_DISPATCHED",
                f"{row['operation_id']} has no dispatch intent, so 'unknown' would be a guess: "
                "it either never left (prove no charge) or is still reserved")

    def prove_no_charge(self, operation_id: str, *, reason: str,
                        evidence_ref: str = "") -> Operation:
        """Release a hold that provably never left the process.

        Valid only before dispatch intent, and only for a reason on the short list.
        A provider *declaring* no charge after a dispatched call is a different act
        with a different authority: see :meth:`reconcile_no_charge`.
        """
        if reason not in NO_CHARGE_REASONS:
            raise LedgerRefused("REASON_NOT_ACCEPTED",
                                f"{reason!r} is not a provable no-charge reason (expected one of "
                                f"{sorted(NO_CHARGE_REASONS)})")
        reference = _reference(evidence_ref, "evidence_ref")
        return self._transition(
            operation_id, kinds=("RESERVED",),
            changes={"charge_state": ChargeState.NO_CHARGE_PROVEN.value, "actual_micros": 0,
                     "actual_source": ActualSource.DERIVED.value,
                     "settled_at": self._now().isoformat(), "outcome": reason},
            event="prove_no_charge", code=reason, detail_ref=reference,
            guard=self._guard_no_dispatch)

    def mark_unknown(self, operation_id: str, *, reason: str,
                     execution_outcome: str = "") -> Operation:
        """A dispatched call whose cost nobody knows. It keeps its hold.

        ``execution_outcome`` is the adapter's own result (``OK``/``FAILED``/
        ``UNKNOWN``) when the caller knows it. Recording it here is what stops a
        successful call whose price is unknown from looking exactly like a provider
        error in the ledger; an empty value honestly means "not observed".
        """
        if reason not in UNKNOWN_REASONS:
            raise LedgerRefused("REASON_NOT_ACCEPTED",
                                f"{reason!r} is not a recognised unknown-charge reason (expected "
                                f"one of {sorted(UNKNOWN_REASONS)})")
        if execution_outcome:
            if (not isinstance(execution_outcome, str)
                    or execution_outcome not in EXECUTION_OUTCOMES):
                raise LedgerRefused(
                    "INVALID_REQUEST",
                    f"execution_outcome {execution_outcome!r} is outside the recorded set "
                    f"{sorted(EXECUTION_OUTCOMES)}; it says what happened, not what it cost")
        changes = {"charge_state": ChargeState.UNKNOWN.value, "outcome": reason}
        if execution_outcome:
            changes["execution_outcome"] = execution_outcome
        return self._transition(operation_id, kinds=("RESERVED",), changes=changes,
                                event="mark_unknown", code=reason,
                                guard=self._guard_dispatched)

    def reconcile(self, operation_id: str, *, actual_micros: int,
                  actual_source: ActualSource | str, upstream_ref: str = "") -> Operation:
        """Close an unknown with a number and its provenance."""
        source = _actual_source(actual_source)
        if source not in (ActualSource.PROVIDER_REPORTED, ActualSource.DERIVED):
            raise LedgerRefused("ACTUAL_SOURCE_REQUIRED",
                                "a reconciliation must say where the number came from")
        if type(actual_micros) is not int or actual_micros < 0:
            raise LedgerRefused("INVALID_REQUEST",
                                "a reconciliation needs a nonnegative actual amount")
        reference = _reference(upstream_ref, "upstream_ref")
        self._guard_evidence(source, reference, what="reconciliation")
        now = self._now().isoformat()
        return self._transition(
            operation_id, kinds=("UNKNOWN",),
            changes={"charge_state": ChargeState.SETTLED.value, "actual_micros": actual_micros,
                     "actual_source": source.value, "settled_at": now, "reconciled_at": now,
                     "outcome": "RECONCILED", "upstream_ref": reference},
            event="reconcile", code=source.value, detail_ref=reference)

    def reconcile_no_charge(self, operation_id: str, *, evidence_ref: str) -> Operation:
        """Close an unknown when the *provider* said it did not bill this call."""
        reference = _reference(evidence_ref, "evidence_ref")
        if not reference:
            raise LedgerRefused("EVIDENCE_REQUIRED",
                                "a post-dispatch no-charge claim must name its evidence")
        now = self._now().isoformat()
        return self._transition(
            operation_id, kinds=("UNKNOWN",),
            changes={"charge_state": ChargeState.NO_CHARGE_PROVEN.value, "actual_micros": 0,
                     "actual_source": ActualSource.PROVIDER_REPORTED.value, "settled_at": now,
                     "reconciled_at": now, "outcome": "NO_CHARGE_PROVEN",
                     "upstream_ref": reference},
            event="reconcile_no_charge", code="NO_CHARGE_PROVEN", detail_ref=reference)

    def import_legacy_record(self, *, legacy_ref: str, project: str, run_id: str, provider: str,
                             capability: str, transport: str, created_at: str, outcome: str = "",
                             counts_against_caps: bool = False) -> tuple[Operation, bool]:
        """Bring one legacy attempt forward as history. Returns (row, whether it was new).

        The row is ``UNKNOWN`` because a legacy reserved attempt may or may not have
        been charged - the old ledger recorded that an attempt happened, not whether
        money moved. It carries **no money at all** rather than an invented estimate,
        and ``tier`` is ``-1``: an imported row has no policy tier, and stamping T0 on
        it would let history read as though it had been reviewed.

        ``outcome`` is a bounded *code*, not the legacy text. A legacy outcome column
        can hold a provider error body - which is where a key or a URL turns up - and
        this ledger's `outcome` is read by reports, so anything that is not
        code-shaped is replaced rather than copied.

        Re-running the import is a no-op, because ``legacy_ref`` is unique.
        """
        if not isinstance(legacy_ref, str) or not legacy_ref.strip():
            raise LedgerRefused("INVALID_REQUEST",
                                "an imported row must carry its legacy reference")
        for name, value in (("project", project), ("run_id", run_id), ("provider", provider),
                            ("capability", capability), ("transport", transport)):
            if not isinstance(value, str) or not ID.fullmatch(value):
                raise LedgerRefused("INVALID_REQUEST", f"{name} must be a short identifier")
        outcome_code = outcome if isinstance(outcome, str) and ID.fullmatch(outcome) \
            else ("LEGACY_UNKNOWN_OUTCOME" if outcome else "")
        expected = "legacy-" + sha256(legacy_ref.encode("utf-8")).hexdigest()[:32]
        digest = _digest({"legacy_ref": legacy_ref})
        with self._connect(write=True) as db:
            existing = db.execute("SELECT * FROM operations WHERE legacy_ref=?",
                                  (legacy_ref,)).fetchone()
            if existing is not None:
                return self._operation(existing), False
            db.execute(
                "INSERT INTO operations (operation_id, project, run_id, intent_fingerprint,"
                " op_signature, provider, account_ref, capability, meter, transport, tier,"
                " estimated_micros, reserved_micros, actual_micros, actual_source, charge_state,"
                " quota_unit, quota_units, estimated_seconds, outcome, upstream_ref, grant_ref,"
                " escalates_from, price_source, price_version, price_observed_at, price_verified,"
                " origin, legacy_ref, counts_against_caps, created_at, dispatch_intent_at,"
                " settled_at, reconciled_at) VALUES (:operation_id, :project, :run_id, :digest,"
                " :digest, :provider, 'legacy', :capability, 'legacy_attempt', :transport, -1,"
                " 0, 0, NULL, 'NONE', 'UNKNOWN', 'none', 0, 0, :outcome, '', '', '',"
                " 'legacy_import', 'n/a', :created_at, 0, 'legacy', :legacy_ref,"
                " :counts_against_caps, :created_at, NULL, NULL, NULL)",
                {"operation_id": expected, "project": project, "run_id": run_id,
                 "digest": digest, "provider": provider, "capability": capability,
                 "transport": transport, "outcome": outcome_code, "created_at": created_at,
                 "legacy_ref": legacy_ref,
                 "counts_against_caps": int(bool(counts_against_caps))})
            self._event(db, expected, "import_legacy", code="LEGACY_ATTEMPT",
                        detail_ref=legacy_ref)
        operation = self.get(expected)
        if operation is None:  # pragma: no cover
            raise LedgerRefused("INTERNAL", "imported row did not persist")
        return operation, True

    def record_local_reuse(self, *, project: str, run_id: str, capability: str,
                           evidence_ref: str, intent: str) -> Operation:
        """Record that a saved artifact answered this, with no upstream call at all.

        The row is terminal the moment it is written, so it can never become a
        dispatch: a cache hit must not be the thing that quietly buys a paid call.
        It holds no budget either, because nothing was spent.

        The id is derived from the arguments and the caller cannot supply one. An
        earlier version accepted an ``operation_id`` and returned whatever row
        already had it - so a caller that passed the id of a live reservation got a
        dispatched, money-holding row back and believed it was a cache hit.
        """
        for name, value in (("project", project), ("run_id", run_id), ("capability", capability)):
            if not isinstance(value, str) or not ID.fullmatch(value):
                raise LedgerRefused("INVALID_REQUEST", f"{name} must be a short identifier")
        reference = _reference(evidence_ref, "evidence_ref")
        if not reference:
            raise LedgerRefused("EVIDENCE_REQUIRED", "a local reuse must name its evidence")
        if not isinstance(intent, str) or not intent.strip():
            raise LedgerRefused("INVALID_REQUEST", "a local reuse must say what it answers")
        identifier = "local-" + sha256(
            f"{project}|{run_id}|{capability}|{reference}".encode()).hexdigest()[:32]
        now = self._now().isoformat()
        with self._connect(write=True) as db:
            existing = db.execute("SELECT * FROM operations WHERE operation_id=?",
                                  (identifier,)).fetchone()
            if existing is not None:
                if existing["origin"] != "local":
                    raise LedgerRefused(
                        "OPERATION_ID_COLLISION",
                        "the derived id of this reuse is held by a row that is not a local reuse; "
                        "the ledger refuses to hand back a live hold as if it were a cache hit")
                return self._operation(existing)
            db.execute(
                "INSERT INTO operations (operation_id, project, run_id, intent_fingerprint,"
                " op_signature, provider, account_ref, capability, meter, transport, tier,"
                " estimated_micros, reserved_micros, actual_micros, actual_source, charge_state,"
                " quota_unit, quota_units, estimated_seconds, outcome, upstream_ref, grant_ref,"
                " escalates_from, price_source, price_version, price_observed_at, price_verified,"
                " origin, legacy_ref, counts_against_caps, created_at, dispatch_intent_at,"
                " settled_at, reconciled_at) VALUES (:operation_id, :project, :run_id,"
                " :intent_fingerprint, :op_signature, 'local', 'local', :capability,"
                " 'local_reuse', 'local_cache', 0, 0, 0, 0, 'DERIVED', 'NO_CHARGE_PROVEN',"
                " 'none', 0, 0, 'LOCAL_CACHE_HIT', :evidence_ref, '', '', 'local_cache', 'n/a',"
                " :now, 1, 'local', NULL, 0, :now, NULL, :now, NULL)",
                {"operation_id": identifier, "project": project, "run_id": run_id,
                 "intent_fingerprint": _digest({"project": project, "run_id": run_id,
                                                "intent": " ".join(intent.split()).casefold()}),
                 "op_signature": _digest({"project": project, "run_id": run_id,
                                          "capability": capability, "evidence": reference}),
                 "capability": capability, "evidence_ref": reference, "now": now})
            self._event(db, identifier, "local_reuse", code="LOCAL_CACHE_HIT",
                        detail_ref=reference)
        operation = self.get(identifier)
        if operation is None:  # pragma: no cover
            raise LedgerRefused("INTERNAL", "local reuse did not persist")
        return operation


# --------------------------------------------------------------------------- #
# The campaign floor: 20% of a frozen B0, plus TinyFish's explicit US$0.20
# --------------------------------------------------------------------------- #

#: The meter name for a USD wallet held in integer micro-USD.
USD_MICROS = "usd_micros"

#: §1.2 of the owner's gate: the absolute campaign floor is this share of B0.
CAMPAIGN_FLOOR_RATIO = Decimal("0.20")

#: §1.3: the drawdown ceiling. An **upper bound**, not a target to spend.
CAMPAIGN_MAX_DRAWDOWN_RATIO = Decimal("0.80")

#: §1.4: TinyFish additionally must retain **at least US$0.20** in its wallet, so its
#: floor is ``max(0.20 * starting_wallet_usd, 0.20)`` - a bare ratio would let a
#: US$0.50 wallet fall to US$0.10, which the owner's document forbids. The minimum is
#: expressed once, here, in the unit the wallet is held in.
TINYFISH_MINIMUM_USD = Decimal("0.20")
TINYFISH_MINIMUM_MICROS = int(TINYFISH_MINIMUM_USD * MICRO)  # 200_000 micro-USD
TINYFISH_PROVIDER = "tinyfish"


class MeterReadingSource(str, Enum):
    """Where a B0 figure came from. Only two of these are evidence.

    §1.1 is explicit: "An inherited environment variable, mocked tool,
    documentation price, or credential-file presence is not evidence of an actual
    current balance." Those values are representable here **so that they can be
    refused** - a vocabulary without them would force a caller to either lie about
    the source or leave the field blank, and a blank source is what gets read as a
    verified balance.
    """

    VENDOR_ACCOUNT_READ = "VENDOR_ACCOUNT_READ"
    OFFICIAL_READONLY_ENDPOINT = "OFFICIAL_READONLY_ENDPOINT"
    ENVIRONMENT_VARIABLE = "ENVIRONMENT_VARIABLE"
    DOCUMENTATION = "DOCUMENTATION"
    MOCK = "MOCK"
    CREDENTIAL_FILE_PRESENCE = "CREDENTIAL_FILE_PRESENCE"
    UNKNOWN = "UNKNOWN"

    @property
    def is_evidence(self) -> bool:
        return self in (MeterReadingSource.VENDOR_ACCOUNT_READ,
                        MeterReadingSource.OFFICIAL_READONLY_ENDPOINT)


class BudgetVerdict(str, Enum):
    """The coordinator's closed set, worst-of at the top level.

    ``NOT_CAPTURED`` means "nobody has verified this", which is emphatically **not**
    an authorization: reading an empty ledger as "approved" is the failure mode this
    vocabulary exists to prevent.
    """

    AUTHORIZED = "AUTHORIZED"
    BLOCKED = "BLOCKED"
    NOT_CAPTURED = "NOT_CAPTURED"

    @classmethod
    def worst(cls, verdicts: Iterable[BudgetVerdict]) -> BudgetVerdict:
        order = {cls.BLOCKED: 2, cls.NOT_CAPTURED: 1, cls.AUTHORIZED: 0}
        return max(verdicts, key=lambda verdict: order[verdict], default=cls.NOT_CAPTURED)


@dataclass(frozen=True)
class CampaignBaseline:
    """The campaign's ``B0``: one provider account's verified starting balance.

    Frozen by construction. There is no method that re-derives it from a running
    balance, because that re-derivation *is* the bypass §1.2 forbids: after spending
    half, a floor recomputed from what is left would drop with it.

    The numbers live in the coordinator's local 0600 ledger, never in this
    repository; this type carries whatever the caller read at campaign start.
    """

    provider: str
    account_ref: str
    meter: str
    units: int
    observed_at: str
    source: MeterReadingSource
    campaign_id: str
    reset_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not ID.fullmatch(self.provider):
            raise LedgerRefused("CAMPAIGN_BASELINE_INVALID",
                                "a baseline must name its provider")
        if not isinstance(self.account_ref, str) or not ID.fullmatch(self.account_ref):
            raise LedgerRefused("CAMPAIGN_BASELINE_INVALID",
                                "a baseline must name the account it was read from")
        if not isinstance(self.meter, str) or not self.meter.strip():
            raise LedgerRefused("CAMPAIGN_BASELINE_INVALID",
                                "a baseline must name the meter; a number without a unit is "
                                "not a balance")
        if type(self.units) is not int or self.units < 0:
            raise LedgerRefused("CAMPAIGN_BASELINE_INVALID",
                                "B0 must be a nonnegative integer in its own meter")
        if not isinstance(self.campaign_id, str) or not ID.fullmatch(self.campaign_id):
            raise LedgerRefused("CAMPAIGN_BASELINE_INVALID",
                                "a baseline belongs to a named campaign; without one, 'the same "
                                "B0 throughout' has nothing to be the same as")
        observed = _aware_timestamp(self.observed_at)
        if observed is None:
            raise LedgerRefused("CAMPAIGN_BASELINE_INVALID",
                                "a baseline must record when it was observed, with a timezone")
        if self.reset_at and _aware_timestamp(self.reset_at) is None:
            raise LedgerRefused("CAMPAIGN_BASELINE_INVALID",
                                "reset_at, when given, must be a timestamp with a timezone")

    @property
    def is_evidence(self) -> bool:
        return self.source.is_evidence


@dataclass(frozen=True)
class BudgetDecision:
    """One admissibility answer, with the numbers it was made from."""

    verdict: BudgetVerdict
    code: str
    floor_units: int
    available_after_units: int
    spendable_units: int
    detail: str

    @property
    def allowed(self) -> bool:
        return self.verdict is BudgetVerdict.AUTHORIZED


@dataclass(frozen=True)
class CampaignFloor:
    """`floor = ceil(ratio * B0)`, raised to US$0.20 for a TinyFish wallet.

    Rounded **up** when the meter is indivisible, because rounding down would put the
    account below the floor it was told to keep.
    """

    baseline: CampaignBaseline
    ratio: Decimal = CAMPAIGN_FLOOR_RATIO
    tolerance_units: int = 0
    max_age_seconds: int | None = None

    def __post_init__(self) -> None:
        if not (Decimal(0) < self.ratio < Decimal(1)):
            raise LedgerRefused("CAMPAIGN_FLOOR_INVALID",
                                "the floor ratio must be strictly between 0 and 1")
        if type(self.tolerance_units) is not int or self.tolerance_units < 0:
            raise LedgerRefused("CAMPAIGN_FLOOR_INVALID",
                                "the safety allowance must be a nonnegative integer")
        if self.max_age_seconds is not None and self.max_age_seconds <= 0:
            raise LedgerRefused("CAMPAIGN_FLOOR_INVALID",
                                "a freshness window must be a positive number of seconds")

    @property
    def floor_units(self) -> int:
        scaled = (Decimal(self.baseline.units) * self.ratio).to_integral_value(ROUND_CEILING)
        floor = int(scaled)
        if self.baseline.provider == TINYFISH_PROVIDER and self.baseline.meter == USD_MICROS:
            # A bare ratio would let a US$0.50 wallet fall to US$0.10.
            floor = max(floor, TINYFISH_MINIMUM_MICROS)
        return floor

    @property
    def max_drawdown_units(self) -> int:
        """The cumulative spend the campaign may not exceed. An upper bound."""
        scaled = (Decimal(self.baseline.units) *
                  CAMPAIGN_MAX_DRAWDOWN_RATIO).to_integral_value(ROUND_FLOOR)
        return int(scaled)

    @property
    def spendable_units(self) -> int:
        """B0 minus the floor - the two bounds' binding half at campaign start."""
        return max(0, self.baseline.units - self.floor_units)

    def is_stale(self, now: datetime) -> bool:
        if self.max_age_seconds is None:
            return False
        observed = _aware_timestamp(self.baseline.observed_at)
        if observed is None:  # pragma: no cover - rejected at construction
            return True
        return (now - observed).total_seconds() > self.max_age_seconds


def _aware_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


class CampaignBudget:
    """The campaign-level check: may this operation spend, given the frozen floor?

    Holds the floor, the account's spend observed elsewhere, and the amount other
    users of the same account have spent - which §1.2 requires to be counted. The
    runtime ledger contributes its own settled, reserved and UNKNOWN figures.
    """

    def __init__(self, floor: CampaignFloor, *, external_spend_units: int = 0):
        if type(external_spend_units) is not int or external_spend_units < 0:
            raise LedgerRefused("CAMPAIGN_BUDGET_INVALID",
                                "external spend must be a nonnegative integer in the meter")
        self._floor = floor
        self._external = external_spend_units

    @property
    def floor(self) -> CampaignFloor:
        return self._floor

    @property
    def meter(self) -> str:
        return self._floor.baseline.meter

    def admit(self, *, spend_units: int, spent_units: int = 0, unknown_units: int = 0,
              now: datetime | None = None) -> BudgetDecision:
        """§1.6: the pre-operation decision. Fails closed for every unknown.

        ``available_after_units`` is what the account would hold if this operation
        were charged: that is why UNKNOWN exposure and other users' spend are
        subtracted *before* comparing against the floor, and why a pricing/usage
        delay is covered by ``tolerance_units`` rather than assumed away.
        """
        for name, value in (("spend_units", spend_units), ("spent_units", spent_units),
                            ("unknown_units", unknown_units)):
            if type(value) is not int or value < 0:
                raise LedgerRefused("CAMPAIGN_BUDGET_INVALID",
                                    f"{name} must be a nonnegative integer")
        floor = self._floor.floor_units
        baseline = self._floor.baseline
        if self._floor.is_stale(now or datetime.now(timezone.utc)):
            return BudgetDecision(BudgetVerdict.NOT_CAPTURED, "CAMPAIGN_BASELINE_STALE", floor,
                                  0, self._floor.spendable_units,
                                  "the B0 reading is older than the allowed window")
        if not baseline.is_evidence:
            return BudgetDecision(
                BudgetVerdict.NOT_CAPTURED, "CAMPAIGN_BASELINE_NOT_EVIDENCE", floor, 0,
                self._floor.spendable_units,
                f"a {baseline.source.value} reading is not evidence of a balance; "
                "an unverified B0 cannot authorize spending")
        if baseline.units <= floor:
            return BudgetDecision(
                BudgetVerdict.BLOCKED, "CAMPAIGN_B0_AT_OR_BELOW_FLOOR", floor,
                baseline.units, 0,
                f"B0 ({baseline.units}) is at or below the floor ({floor}); no chargeable call "
                "is allowed")
        available_after = (baseline.units - spent_units - unknown_units - self._external
                           - spend_units)
        if available_after < floor + self._floor.tolerance_units:
            return BudgetDecision(
                BudgetVerdict.BLOCKED, "CAMPAIGN_FLOOR_WOULD_BREACH", floor, available_after,
                max(0, baseline.units - floor - spent_units - unknown_units - self._external),
                f"post-operation available {available_after} would fall below the floor {floor} "
                f"(floor + safety allowance {floor + self._floor.tolerance_units}); "
                f"already committed {spent_units} settled/reserved and {unknown_units} unknown, "
                f"external spend {self._external}")
        return BudgetDecision(
            BudgetVerdict.AUTHORIZED, "OK", floor, available_after,
            max(0, baseline.units - floor - spent_units - unknown_units - self._external),
            f"post-operation available {available_after} stays at or above the floor {floor}")


# --------------------------------------------------------------------------- #
# How a refusal travels: the contract's closed set, and this ledger's map to it
# --------------------------------------------------------------------------- #

#: The frozen reason-code closed set (§5.3), copied so the mapping beside it is
#: checkable in one place. A code that is already here needs no mapping.
CONTRACT_REASON_CODES = frozenset({
    "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
    "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
    "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
    "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
    "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING", "RESEARCH_NETWORK_DISABLED",
    "WALLET_PRECONDITION_FAILED", "GRANT_SIGNATURE_INVALID",
})

#: Ledger code -> the §5.3 code a host should see, or ``""`` when this is not a
#: governance refusal at all (a programming or state error: a double settle, an
#: operation id nobody issued). An empty string means "raise ContractViolation", not
#: "report BLOCKED with no code" - the distinction matters, because a caller bug must
#: not read as a budget decision.
#:
#: Three entries are the closest existing name rather than a perfect one, and each
#: needs a §8 addition; they are marked in the table below so the gap is visible
#: rather than smoothed over.
REFUSAL_DISPOSITION: dict[str, str] = {
    # -- the contract already has the name ---------------------------------
    "DUPLICATE_OPERATION_SIGNATURE": "DUPLICATE_OPERATION",
    "PRICE_UNKNOWN": "PRICE_UNKNOWN_FAIL_CLOSED",
    "UNREVIEWED_CAPABILITY": "CAPABILITY_UNKNOWN",
    "TRANSPORT_NOT_REVIEWED": "CAPABILITY_NOT_AVAILABLE",
    "ARGS_NOT_CANONICAL": "CAPABILITY_PARAM_NOT_ALLOWED",
    "QUOTA_UNIT_UNREVIEWED": "CAPABILITY_PARAM_NOT_ALLOWED",
    "REFERENCE_NOT_OPAQUE": "CAPABILITY_PARAM_NOT_ALLOWED",
    "INVALID_REQUEST": "CAPABILITY_PARAM_NOT_ALLOWED",
    "GRANT_REQUIRED": "POLICY_TIER_NOT_GRANTED",
    "SITE_REQUIRED": "POLICY_TIER_NOT_GRANTED",
    "CAMPAIGN_PRICE_UNKNOWN_FAIL_CLOSED": "PRICE_UNKNOWN_FAIL_CLOSED",
    # Relayed verbatim from :mod:`research_grants` by ``_verified_grant``; a host
    # sees these on a managed call, so they need dispositions too.
    "GRANT_VERIFICATION_UNAVAILABLE": "POLICY_TIER_NOT_GRANTED",
    "GRANT_MALFORMED": "POLICY_TIER_NOT_GRANTED",
    "GRANT_TIER_TOO_LOW": "POLICY_TIER_NOT_GRANTED",
    "GRANT_SITE_REQUIRED": "POLICY_TIER_NOT_GRANTED",
    "GRANT_SCOPE_MISMATCH_PROJECT": "POLICY_TIER_NOT_GRANTED",
    "GRANT_SCOPE_MISMATCH_RUN_ID": "POLICY_TIER_NOT_GRANTED",
    "GRANT_SCOPE_MISMATCH_PROVIDER": "POLICY_TIER_NOT_GRANTED",
    "GRANT_SCOPE_MISMATCH_CAPABILITY": "POLICY_TIER_NOT_GRANTED",
    "GRANT_SCOPE_MISMATCH_ACCOUNT_REF": "POLICY_TIER_NOT_GRANTED",
    "GRANT_SCOPE_MISMATCH_SITE": "POLICY_TIER_NOT_GRANTED",
    "GRANT_EXPIRED": "POLICY_GRANT_EXPIRED",
    "GRANT_SIGNATURE_INVALID": "GRANT_SIGNATURE_INVALID",
    # -- governance, closest existing name; §8 addition wanted --------------
    # WALLET_PRECONDITION_FAILED: the campaign balance could not be used as a budget.
    "CAMPAIGN_NOT_CAPTURED": "WALLET_PRECONDITION_FAILED",
    "CAMPAIGN_BASELINE_NOT_EVIDENCE": "WALLET_PRECONDITION_FAILED",
    "CAMPAIGN_BASELINE_STALE": "WALLET_PRECONDITION_FAILED",
    "CAMPAIGN_BASELINE_INVALID": "WALLET_PRECONDITION_FAILED",
    "CAMPAIGN_METER_MISMATCH": "WALLET_PRECONDITION_FAILED",
    # BUDGET_PROVIDER_CEILING: the account's own budget stopped it.
    "CAMPAIGN_FLOOR_WOULD_BREACH": "BUDGET_PROVIDER_CEILING",
    "CAMPAIGN_B0_AT_OR_BELOW_FLOOR": "BUDGET_PROVIDER_CEILING",
    "GRANT_CALL_LIMIT_EXCEEDED": "BUDGET_PROVIDER_CEILING",
    "GRANT_AMOUNT_EXCEEDED": "BUDGET_PROVIDER_CEILING",
    # -- no honest target yet: §8 additions requested ----------------------
    # BUDGET_NOT_CONFIGURED: "nobody set a ceiling" is not "a ceiling was reached".
    "CAP_NOT_CONFIGURED": "BUDGET_RUN_CEILING",
    # LEDGER_UNAVAILABLE: the ledger could not record; the call must not proceed.
    "LEDGER_BUSY": "BUDGET_RUN_CEILING",
    "LEDGER_INIT_BUSY": "BUDGET_RUN_CEILING",
    "LEDGER_INIT_FAILED": "BUDGET_RUN_CEILING",
    # POLICY_DOMAIN_NOT_ALLOWED is about domains; this is about re-buying one intent.
    "CROSS_PROVIDER_ESCALATION_UNJUSTIFIED": "POLICY_DOMAIN_NOT_ALLOWED",
    # -- not a governance refusal: a state or programming error ------------
    "ALREADY_TERMINAL": "",
    "NOT_RESERVED": "",
    "NOT_DISPATCHED": "",
    "CANNOT_PROVE_NO_CHARGE_AFTER_DISPATCH": "",
    "UNKNOWN_OPERATION": "",
    "ACTUAL_SOURCE_REQUIRED": "",
    "EVIDENCE_REQUIRED": "",
    "REASON_NOT_ACCEPTED": "",
    "FREE_TIER_CONTRADICTS_RATE": "",
    "INTERNAL": "",
    # Operator-verb misuse (``set_cap``), never reachable from a managed call.
    "CAP_BELOW_HELD": "",
    "CAP_HAS_NO_LIMIT": "",
    "CAP_SOURCE_REQUIRED": "",
    "USD_ENFORCEMENT_REQUIRES_CEILING": "",
    "CAMPAIGN_BUDGET_INVALID": "",
    "CAMPAIGN_FLOOR_INVALID": "",
}


def refusal_disposition(code: str) -> tuple[str, str]:
    """How a host should treat one refusal: ``("BLOCKED", reason_code)`` or
    ``("CONTRACT_VIOLATION", code)``.

    This is the seam the gateway needs: QA round 5 measured a governed refusal
    arriving as ``ContractViolation: ... code outside the closed set: None`` because
    the ledger's vocabulary and the contract's were two different vocabularies that
    overlapped in one code. A caller that asks here gets one answer, and it cannot
    drift from the codes the module actually raises (``test_wave1_ledger_refusals``
    pins the two together).
    """
    if not isinstance(code, str) or not code:
        return "CONTRACT_VIOLATION", ""
    if code in CONTRACT_REASON_CODES:
        return "BLOCKED", code
    target = REFUSAL_DISPOSITION.get(code)
    if target:
        return "BLOCKED", target
    return "CONTRACT_VIOLATION", code


# --------------------------------------------------------------------------- #
# CLI (read-only)
# --------------------------------------------------------------------------- #

def default_path(root: str | Path | None = None) -> Path:
    """The ledger inside the configured customer data root, resolved lazily."""
    try:
        from .research_event_journal import _root
    except ImportError:
        from research_event_journal import _root
    return Path(_root(root)) / "state" / "usage-ledger.sqlite3"


def _pricing_from_environment() -> PricingCatalog:
    """An operator-supplied catalog, if one is configured; otherwise empty (= refuse)."""
    raw = (os.environ.get("MARKETOPS_USAGE_PRICING") or "").strip()
    if not raw or re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", raw):
        return PricingCatalog(())
    return PricingCatalog.from_path(raw)


def configured_pricing() -> PricingCatalog:
    """The operator's price catalog, or an empty one when none is configured.

    Empty is the answer that matters: it means the ledger is running on the
    documented credit baseline, so a dollar ceiling has no provable rate and must
    refuse rather than print a protection it cannot provide.
    """
    return _pricing_from_environment()


PROJECT_ENV = "MARKETOPS_RESEARCH_PROJECT"


def project_for_root(root: str | Path | None = None, environ: Mapping[str, str] | None = None) -> str:
    """The project id a customer data root's operations are booked under.

    A project is an *identifier*, not a path: the ledger's scope keys are validated
    as ids, and putting a filesystem path in that column would write the customer's
    directory layout into every receipt and every report.

    One root is one tenant already — its ledger is a file inside it — so the id only
    has to be stable and unique within that file. An operator who wants a readable
    one names it with ``MARKETOPS_RESEARCH_PROJECT``; with none named, a digest of
    the resolved root is used, which is stable across runs and says nothing about
    where the customer keeps their data.
    """
    env = os.environ if environ is None else environ
    named = (env.get(PROJECT_ENV) or "").strip()
    if named and ID.fullmatch(named):
        return named
    # ``default_path`` already resolves the customer root through the same
    # accessor everything else uses, so the digest is over one answer rather than
    # over a second opinion about where the data root is.
    resolved = str(default_path(root).parent.parent.resolve())
    return "root-" + sha256(resolved.encode("utf-8")).hexdigest()[:16]


#: What this plugin can say about a rate without inventing a dollar figure.
#:
#: These are **credit-metered** capabilities: the vendor bills in its own unit
#: (a search, a credit, a page, a step) and this checkout holds no verified
#: USD rate for any of them. `unit_micros=0` with `free_tier=False` is the
#: honest encoding — money and quota are separate columns precisely so a call
#: can cost 0 USD *and* 1 credit — and `verified=False` plus a named `source`
#: records that nobody confirmed a price against an account.
#:
#: The consequence, stated plainly: with no operator catalog, the ledger bounds
#: these operations by **calls/credits**, not by dollars, because dollars are not
#: known. An operator who knows their account's rates sets
#: ``MARKETOPS_USAGE_PRICING`` and the money column starts meaning something.
DOCUMENTED_CREDIT_RATES = (
    ("serper", "search", "credits"),
    ("serpapi", "search", "searches"),
    ("tinyfish", "search", "searches"),
    ("tinyfish", "fetch", "urls"),
    ("tinyfish", "agent_run", "steps"),
    ("tinyfish", "usage_read", "requests"),
    ("tinyfish", "wallet_read", "requests"),
    ("crawl4ai", "render", "pages"),
    ("crawl4ai", "extract", "pages"),
    ("typesafe_jev", "judge", "requests"),
)
RATE_SOURCE = "provider_documentation_credits_only"

#: Page fetches that spend no vendor money, so a retry is not a double charge.
RETRYABLE_FREE_FETCHES = frozenset({
    ("crawl4ai", "render"), ("crawl4ai", "extract"), ("tinyfish", "fetch"),
})


def documented_pricing() -> PricingCatalog:
    """The credit-only baseline, for when no operator catalog is configured."""
    return PricingCatalog.from_entries([
        {"provider": provider, "capability": capability, "unit_micros": 0,
         "quota_unit": quota_unit, "quota_per_call": 1,
         "source": RATE_SOURCE, "version": "credits-only-1",
         "observed_at": "2026-10-17T00:00:00+00:00", "verified": False}
        for provider, capability, quota_unit in DOCUMENTED_CREDIT_RATES])


def for_root(root: str | Path | None = None, **kwargs) -> "UsageLedger":
    """The ledger for a customer data root, with the operator's price catalog.

    One function, because "which ledger file, and priced by whom" is a question
    every call site would otherwise answer slightly differently — and the two
    ways to answer it wrongly are the two this module exists to prevent: two
    ledgers, or one ledger nobody priced.

    An operator catalog (``MARKETOPS_USAGE_PRICING``) always wins. With none
    configured the *documented credit baseline* is used rather than an empty
    catalog, so a configured deployment can run on call ceilings alone; every row
    it writes says ``verified=False`` with a named source, so the money column is
    legible as "not known" rather than as "free".
    """
    if "pricing" not in kwargs:
        configured = _pricing_from_environment()
        import research_spending
        kwargs["pricing"] = (configured if len(configured) else
            PricingCatalog.from_entries(research_spending.pricing_entries())
            if research_spending.policy(default_path(root).parent.parent) else documented_pricing())
    return UsageLedger(default_path(root), **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="research_usage_ledger",
        description="Read the unified usage ledger. This CLI writes nothing.")
    parser.add_argument("--db", help="ledger database (defaults to the customer data root)")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="usage report for a project")
    report.add_argument("--project", required=True)
    report.add_argument("--accepted-outputs", type=int)
    open_cmd = sub.add_parser("open", help="operations still holding budget")
    open_cmd.add_argument("--project")
    recover = sub.add_parser("recover", help="what a crash left behind (reading only)")
    recover.add_argument("--project")
    args = parser.parse_args(argv)
    path = Path(args.db) if args.db else default_path()
    ledger = UsageLedger(path, pricing=_pricing_from_environment(),
                         grants_authority=grants.GrantAuthority.from_environment())
    if args.command == "report":
        payload = ledger.report(args.project, accepted_outputs=args.accepted_outputs)
    elif args.command == "open":
        payload = {"schema": "marketops.usage.open.v1",
                   "operations": [row.__dict__ for row in
                                  ledger.open_operations(project=args.project)]}
    else:
        payload = ledger.recover(project=args.project)
    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""The reviewed capability registry: the only allowlist (contract §4, IR-1..IR-5).

An upstream vendor's ``tools/list`` is untrusted input. This module turns it into a
fixed table of internal capabilities: each with a stable ``capability_id``, a tier,
an effect class, a meter, the parameter names an Agent may supply, an availability
state and the schema snapshot it was reviewed against.

## What makes it an allowlist rather than a catalogue

* **A capability that is not in this table does not exist** for the gateway. It
  cannot be reached by naming a vendor tool, a URL, or a new upstream name.
* **Parameters are a closed set per capability.** Anything else is refused before
  routing, so a fuzzed key never reaches an adapter.
* **A parameter can raise the tier.** ``run_web_automation`` is T3 — open-ended
  metered steps — but switching on a stored login profile or a webhook makes it
  T4, because the *call*, not the tool, is what becomes sensitive or state
  changing. The highest requirement always wins (§3.2: the four dimensions are
  not averaged).
* **A capability with no ledger name is not dispatchable.** Every managed call is
  reserved first, and the ledger only prices capabilities it has reviewed. An
  entry whose work cannot be priced here is marked DISABLED with the reason next
  to it, rather than being routed and failing later.

## The provider name is the routing key, not the vendor's own spelling

``research_wiring.PROVIDER_ADAPTERS`` and the ledger's review registry both key
Jev as ``typesafe_jev``; the adapter module calls its provider ``jev`` and its
capability ids start with ``jev.``. This table uses ``typesafe_jev`` for
``provider`` because that is the name the gateway looks an adapter up by and the
name the ledger prices under, and keeps ``jev.*`` as the capability ids because
that is what the adapter advertises. The two names are documented here rather
than silently harmonised, because changing either side is its owner's call.

## Two hash subjects, never one substituted for the other

``CapabilityEntry.schema_sha256`` (when populated) is the digest of **our**
reviewed host subset. It is not, and must never be read as, a digest of a
vendor's ``tools/list``. The vendor subject is carried separately, once per
provider, by :class:`VendorSchemaPin`: the canonical sha256 of the **complete**
``tools/list`` payload — every tool's name, ``inputSchema``, descriptor and
annotations — together with the endpoint, capture time, provenance, egress
decision and audit refs that produced it.

The distinction is load-bearing. A host subset can be pinned from this checkout
with no vendor contact; a vendor digest can only come from a capture. Until such
a capture exists the registry reports ``NOT_CAPTURED`` and hands out ``None`` —
it never derives a vendor digest from tool names, a synthetic payload, or a
count of 28, and a synthetic capture can match mechanically without ever being
authoritative. That is what keeps an offline test green from being read as a
LIVE verification.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit

from research_contract import (Availability, EffectClass, SchemaVerdict, Tier,
                               tier_rank)

__all__ = [
    "CapabilityEntry", "CapabilitySummary", "CapabilityDescriptor", "CapabilityRegistry",
    "DEFAULT_REGISTRY", "RegistryError", "param_schema_for", "schema_digest",
    "build_entry", "HashSubject", "VendorSchemaPin", "VendorSchemaCheck",
    "VendorSchemaVerdict", "VendorSchemaStatus", "VendorSchemaRefused",
    "CaptureMode", "TRUSTED_VENDOR_CAPTURE_SOURCES", "MAX_VENDOR_SNAPSHOT_AGE_SECONDS",
    "canonical_tools_list", "tools_list_digest", "tools_list_problems",
]


class RegistryError(ValueError):
    """A registry that would not be a strict allowlist. Raised at construction."""


#: The JSON types this registry will put in a reviewed schema. A closed set, so a
#: typo cannot become a schema that validates everything or nothing.
_JSON_TYPES = frozenset({"string", "integer", "boolean", "object", "array"})


def param_schema_for(params: Mapping[str, str | Mapping[str, object]], *,
                     required: Iterable[str] = ()) -> dict:
    """A host-compatible JSON Schema whose properties are the reviewed parameters.

    A value may be a bare type name (the common shape) or a full property schema,
    for a parameter whose wire contract is more than its type — ``batch_status``
    takes 1..8 run ids, each a bounded opaque string. Expressing the bound here
    keeps one machine-readable contract rather than a comment the adapter alone
    remembers; the adapter still validates at the edge.

    ``required`` is part of the reviewed contract for a capability whose upstream
    tool cannot be called without a handle (a monitor id or a run id list).
    """
    properties = {}
    for name, spec in params.items():
        if isinstance(spec, str):
            if spec not in _JSON_TYPES:
                raise RegistryError(
                    f"parameter {name!r} has an unsupported JSON type {spec!r}")
            properties[name] = {"type": spec}
            continue
        if not isinstance(spec, Mapping):
            raise RegistryError(
                f"parameter {name!r} must be a type name or a property schema")
        kind = spec.get("type")
        if kind not in _JSON_TYPES:
            raise RegistryError(
                f"parameter {name!r} has an unsupported JSON type {kind!r}")
        property_schema = dict(spec)
        if kind == "array":
            items = property_schema.get("items")
            if items is not None:
                if not isinstance(items, Mapping) or items.get("type") not in _JSON_TYPES:
                    raise RegistryError(
                        f"parameter {name!r}: array items need a supported JSON type")
        for bound in ("minItems", "maxItems"):
            if bound in property_schema:
                value = property_schema[bound]
                if type(value) is not int or value < 0:
                    raise RegistryError(f"parameter {name!r}: {bound} must be a "
                                        "nonnegative integer")
        if ("minItems" in property_schema and "maxItems" in property_schema
                and property_schema["minItems"] > property_schema["maxItems"]):
            raise RegistryError(f"parameter {name!r}: minItems exceeds maxItems")
        properties[name] = property_schema
    required = tuple(required)
    surplus = set(required) - set(params)
    if surplus:
        raise RegistryError(f"required parameters outside the reviewed set: {sorted(surplus)}")
    schema: dict = {"type": "object", "properties": properties,
                    "additionalProperties": False}
    if required:
        schema["required"] = list(required)
    return schema


def schema_digest(schema: Mapping[str, object]) -> str:
    """A stable digest of one **host-subset** schema.

    This is the subject of ``CapabilityEntry.schema_sha256``. It is deliberately
    not the function used for vendor payloads: a host subset and a vendor
    ``tools/list`` are different subjects, and a digest that could be computed
    over either would invite exactly the substitution S0_07 refuses.
    """
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Vendor schema pins: the FULL tools/list subject, separate from the host subset
# --------------------------------------------------------------------------- #

class HashSubject(StrEnum):
    """What a digest is a digest *of*. The two values are not interchangeable."""

    HOST_CAPABILITY_SUBSET = "HOST_CAPABILITY_SUBSET"
    FULL_VENDOR_TOOLS_LIST = "FULL_VENDOR_TOOLS_LIST"

    @classmethod
    def coerce(cls, value: "HashSubject | str") -> "HashSubject":
        """Accept the S0 record's legacy spelling, but never guess at a new one."""
        if isinstance(value, cls):
            return value
        text = str(getattr(value, "value", value)).strip()
        # The offline S0 record spells the host-subset subject this way. Reading it
        # is compatibility; emitting it is not: this module always says what it means.
        legacy = {"HOST_SUBSET_NOT_VENDOR_TOOLS_LIST": cls.HOST_CAPABILITY_SUBSET}
        text = legacy.get(text, text)
        try:
            return cls(text)
        except ValueError as exc:
            raise RegistryError(f"unknown hash subject: {value!r}") from exc


class CaptureMode(StrEnum):
    """How a vendor pin came to exist.

    Only ``LIVE_CAPTURE`` can ever be authoritative. ``SYNTHETIC`` is the mode a
    test uses to exercise the digest machinery; it must never be read as a
    capture of a vendor, and the accessor S0 consumes returns ``None`` for it.
    """

    LIVE_CAPTURE = "LIVE_CAPTURE"
    SYNTHETIC = "SYNTHETIC"
    OFFLINE_RECORDED = "OFFLINE_RECORDED"


class VendorSchemaStatus(StrEnum):
    """The authoritative status of one provider's vendor-schema pin."""

    NOT_CAPTURED = "NOT_CAPTURED"
    VERIFIED = "VERIFIED"
    STALE = "STALE"
    UNTRUSTED = "UNTRUSTED"
    INVALIDATED = "INVALIDATED"


class VendorSchemaVerdict(StrEnum):
    """The result of comparing one observed payload with the pin."""

    NOT_CAPTURED = "NOT_CAPTURED"
    MATCH = "MATCH"
    DRIFT = "DRIFT"
    STALE = "STALE"
    UNTRUSTED = "UNTRUSTED"
    INVALIDATED = "INVALIDATED"
    UNVERIFIED = "UNVERIFIED"


class VendorSchemaRefused(RegistryError):
    """A vendor-schema operation refused with a closed-set code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


#: The only capture path S0_07 trusts (``research_s0_preflight_gate`` names the
#: same value). A pin from anywhere else is not authoritative, whatever else it has.
TRUSTED_VENDOR_CAPTURE_SOURCES = frozenset({"probe_schema@official_mcp"})

#: Same freshness window as S0_07. An old digest is stale even when it is correct:
#: the upstream surface can change without this registry being re-pinned.
MAX_VENDOR_SNAPSHOT_AGE_SECONDS = 7 * 24 * 60 * 60

#: A bound, so a hostile payload cannot be hashed without limit.
MAX_TOOLS_IN_SNAPSHOT = 256


def canonical_tools_list(payload: Any) -> str:
    """The exact bytes the vendor digest is computed over.

    This matches ``research_s0_preflight_gate.canonical_snapshot`` byte for byte —
    sorted keys, no whitespace, ``ensure_ascii=False`` — because an integrator
    hands the same payload to both sides and a second canonical form would
    produce a second, different answer about identical content.
    """
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"tools/list payload is not canonical JSON: {exc}") from exc


def tools_list_digest(payload: Any) -> str:
    """sha256 over :func:`canonical_tools_list`, as 64 lowercase hex."""
    return sha256(canonical_tools_list(payload).encode("utf-8")).hexdigest()


def tools_list_problems(payload: Any, *, require_complete: bool = True) -> tuple[str, ...]:
    """Everything wrong with a payload claimed to be a full vendor ``tools/list``.

    ``require_complete`` is for the *pin*: a names-only list, a host subset or a
    count of tool names is not a vendor catalog. An *observation* is checked
    with ``require_complete=False`` because its job is to be compared, not to be
    accepted — the digest mismatch then refuses it.
    """
    if not isinstance(payload, Mapping):
        return ("TOOLS_LIST_NOT_OBJECT",)
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return ("TOOLS_LIST_MISSING_TOOLS",)
    if not tools:
        return ("TOOLS_LIST_EMPTY",)
    if len(tools) > MAX_TOOLS_IN_SNAPSHOT:
        return ("TOOLS_LIST_TOO_LARGE",)
    problems: list[str] = []
    seen: set[str] = set()
    for index, tool in enumerate(tools):
        if not isinstance(tool, Mapping):
            problems.append(f"TOOL_{index}_NOT_OBJECT")
            continue
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"TOOL_{index}_NAME_MISSING")
        elif name in seen:
            problems.append(f"TOOL_{index}_DUPLICATE_NAME")
        else:
            seen.add(name)
        if "annotations" in tool and not isinstance(tool.get("annotations"), Mapping):
            problems.append(f"TOOL_{index}_ANNOTATIONS_NOT_OBJECT")
        if not require_complete:
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, Mapping):
            problems.append(f"TOOL_{index}_INPUT_SCHEMA_MISSING")
        else:
            if schema.get("type") != "object":
                problems.append(f"TOOL_{index}_INPUT_SCHEMA_NOT_OBJECT")
            if not isinstance(schema.get("properties"), Mapping):
                problems.append(f"TOOL_{index}_INPUT_SCHEMA_PROPERTIES_MISSING")
        if "description" not in tool or not isinstance(tool.get("description"), str):
            problems.append(f"TOOL_{index}_DESCRIPTOR_MISSING")
    return tuple(problems)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def _endpoint_problem(endpoint: Any) -> str | None:
    if not isinstance(endpoint, str) or not endpoint.strip():
        return "VENDOR_SCHEMA_ENDPOINT_MISSING"
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        return "VENDOR_SCHEMA_ENDPOINT_NOT_HTTPS"
    return None


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))


@dataclass(frozen=True)
class VendorSchemaPin:
    """One provider's vendor-schema metadata. Immutable, and never invented.

    The default is :meth:`not_captured`: the host-subset subject with no vendor
    digest. A ``FULL_VENDOR_TOOLS_LIST`` pin may only be constructed with the
    complete payload and every provenance field, so a names-only list, an
    offline record or a synthetic count cannot become a vendor assertion by
    omission.
    """

    provider: str
    subject: HashSubject | str = HashSubject.HOST_CAPABILITY_SUBSET
    vendor_schema_sha256: str | None = None
    endpoint: str | None = None
    captured_at: datetime | None = None
    provenance: tuple[str, ...] = ()
    egress_decision_ref: str | None = None
    audit_ref: str | None = None
    tool_count: int | None = None
    source: str = ""
    capture_mode: CaptureMode | str = CaptureMode.OFFLINE_RECORDED
    snapshot_serialized: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise RegistryError("VendorSchemaPin.provider must be a non-empty string")
        object.__setattr__(self, "subject", HashSubject.coerce(self.subject))
        object.__setattr__(self, "capture_mode", CaptureMode(str(getattr(
            self.capture_mode, "value", self.capture_mode))))
        if self.subject is HashSubject.HOST_CAPABILITY_SUBSET:
            if self.vendor_schema_sha256 is not None:
                raise RegistryError(
                    f"{self.provider}: a host-subset pin cannot carry a vendor digest")
            if self.capture_mode is CaptureMode.LIVE_CAPTURE:
                raise RegistryError(
                    f"{self.provider}: a host subset is not a live vendor capture")
            return
        # FULL_VENDOR_TOOLS_LIST: every field is required, and the payload is
        # re-hashed here rather than trusted from a caller's claim.
        if not _is_sha256(self.vendor_schema_sha256):
            raise RegistryError(
                f"{self.provider}: a full vendor pin needs a 64-hex digest")
        problem = _endpoint_problem(self.endpoint)
        if problem:
            raise RegistryError(f"{self.provider}: {problem}")
        if not isinstance(self.captured_at, datetime) or self.captured_at.tzinfo is None:
            raise RegistryError(
                f"{self.provider}: captured_at must be a timezone-aware datetime")
        if (not isinstance(self.provenance, tuple) or not self.provenance
                or not all(isinstance(item, str) and item.strip()
                           for item in self.provenance)):
            raise RegistryError(
                f"{self.provider}: provenance must be a non-empty tuple of refs")
        if not isinstance(self.egress_decision_ref, str) or not self.egress_decision_ref.strip():
            raise RegistryError(
                f"{self.provider}: the restricted egress decision ref is required")
        if not isinstance(self.audit_ref, str) or not self.audit_ref.strip():
            raise RegistryError(f"{self.provider}: an audit ref is required")
        if type(self.tool_count) is not int or self.tool_count <= 0:
            raise RegistryError(f"{self.provider}: tool_count must be a positive int")
        if self.source not in TRUSTED_VENDOR_CAPTURE_SOURCES:
            raise RegistryError(
                f"{self.provider}: source {self.source!r} is not a trusted capture path")
        if not isinstance(self.snapshot_serialized, str) or not self.snapshot_serialized:
            raise RegistryError(
                f"{self.provider}: a full vendor pin must carry the captured payload")
        try:
            captured_payload = json.loads(self.snapshot_serialized)
        except (TypeError, ValueError) as exc:
            raise RegistryError(
                f"{self.provider}: the captured payload is not JSON") from exc
        if canonical_tools_list(captured_payload) != self.snapshot_serialized:
            raise RegistryError(f"{self.provider}: the captured payload is not canonical")
        if tools_list_digest(captured_payload) != self.vendor_schema_sha256:
            raise RegistryError(
                f"{self.provider}: the declared digest is not the captured payload's digest")
        payload_problems = tools_list_problems(captured_payload)
        if payload_problems:
            raise RegistryError(
                f"{self.provider}: not a full vendor tools/list: {list(payload_problems)}")
        if len(captured_payload["tools"]) != self.tool_count:
            raise RegistryError(
                f"{self.provider}: tool_count disagrees with the captured payload")

    # -- constructors ------------------------------------------------------ #
    @classmethod
    def not_captured(cls, provider: str) -> "VendorSchemaPin":
        """The only honest default: a host subset, and no vendor digest."""
        return cls(provider=provider)

    @classmethod
    def from_payload(cls, provider: str, payload: Any, *, endpoint: str,
                     captured_at: datetime, provenance: Sequence[str],
                     egress_decision_ref: str, audit_ref: str,
                     source: str = "probe_schema@official_mcp",
                     capture_mode: CaptureMode | str = CaptureMode.SYNTHETIC
                     ) -> "VendorSchemaPin":
        """Pin a payload that was actually captured, never a description of one."""
        problems = tools_list_problems(payload)
        if problems:
            raise VendorSchemaRefused(
                "VENDOR_SCHEMA_NOT_FULL_TOOLS_LIST",
                f"{provider}: {list(problems)}; names-only and host subsets are not "
                "a vendor tools/list")
        serialized = canonical_tools_list(payload)
        return cls(
            provider=provider, subject=HashSubject.FULL_VENDOR_TOOLS_LIST,
            vendor_schema_sha256=sha256(serialized.encode("utf-8")).hexdigest(),
            endpoint=endpoint, captured_at=captured_at,
            provenance=tuple(provenance), egress_decision_ref=egress_decision_ref,
            audit_ref=audit_ref, tool_count=len(payload["tools"]), source=source,
            capture_mode=capture_mode, snapshot_serialized=serialized)

    # -- status ------------------------------------------------------------ #
    def is_authoritative(self, as_of: datetime | None = None) -> bool:
        """True only for a live, trusted, fresh, fully-provenanced capture."""
        if self.subject is not HashSubject.FULL_VENDOR_TOOLS_LIST:
            return False
        if self.capture_mode is not CaptureMode.LIVE_CAPTURE:
            return False
        if self.source not in TRUSTED_VENDOR_CAPTURE_SOURCES:
            return False
        moment = _as_utc(as_of)
        if self.captured_at is None:
            return False
        age = (moment - self.captured_at).total_seconds()
        return 0 <= age <= MAX_VENDOR_SNAPSHOT_AGE_SECONDS

    def status(self, as_of: datetime | None = None) -> VendorSchemaStatus:
        if self.subject is not HashSubject.FULL_VENDOR_TOOLS_LIST:
            return VendorSchemaStatus.NOT_CAPTURED
        if self.capture_mode is not CaptureMode.LIVE_CAPTURE:
            # A synthetic or offline pin can be useful for exercising the digest
            # machinery; it is never evidence that the vendor was read.
            return VendorSchemaStatus.NOT_CAPTURED
        if self.source not in TRUSTED_VENDOR_CAPTURE_SOURCES:
            return VendorSchemaStatus.UNTRUSTED
        moment = _as_utc(as_of)
        if self.captured_at is None:
            return VendorSchemaStatus.NOT_CAPTURED
        age = (moment - self.captured_at).total_seconds()
        if age < 0 or age > MAX_VENDOR_SNAPSHOT_AGE_SECONDS:
            return VendorSchemaStatus.STALE
        return VendorSchemaStatus.VERIFIED


def _as_utc(as_of: datetime | None) -> datetime:
    if as_of is None:
        return datetime.now(timezone.utc)
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise RegistryError("as_of must be a timezone-aware datetime")
    return as_of.astimezone(timezone.utc)


@dataclass(frozen=True)
class VendorSchemaCheck:
    """Expected-versus-observed, with the tools that changed named."""

    provider: str
    verdict: VendorSchemaVerdict
    authoritative: bool
    expected_sha256: str | None
    observed_sha256: str | None
    expected_tool_count: int | None = None
    observed_tool_count: int | None = None
    changed_tools: tuple[str, ...] = ()
    added_tools: tuple[str, ...] = ()
    removed_tools: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    detail: str = ""


def _tool_map(payload: Any) -> dict[str, str]:
    """tool name -> digest of that tool's canonical object."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("tools"), list):
        return {}
    tools: dict[str, str] = {}
    for tool in payload["tools"]:
        if isinstance(tool, Mapping) and isinstance(tool.get("name"), str):
            tools[tool["name"]] = sha256(
                canonical_tools_list(tool).encode("utf-8")).hexdigest()
    return tools


@dataclass(frozen=True)
class CapabilityEntry:
    """One reviewed capability (§4). Immutable; the registry owns the table."""

    capability_id: str
    provider: str
    upstream_tool: str
    transport: str
    effect_class: EffectClass | str
    tier: Tier | str
    auth_scope: str
    cost_meter: str
    param_schema: Mapping[str, object]
    allowed_params: frozenset[str]
    schema_sha256: str
    availability: Availability | str
    readiness: str
    #: The ledger's reviewed capability name for this work, or ``None`` when the
    #: ledger has no price for it (then the entry is not dispatchable).
    ledger_capability: str | None = None
    #: Parameters that raise the effective tier when present (a stored login
    #: profile, a webhook, a vault handle). The highest wins.
    escalates_to: Mapping[str, Tier] = field(default_factory=dict)
    description: str = ""

    def __post_init__(self) -> None:
        for name in ("capability_id", "provider", "upstream_tool", "transport",
                     "auth_scope", "cost_meter", "readiness"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise RegistryError(f"{name} must be a non-empty string")
        if "." not in self.capability_id:
            raise RegistryError(f"capability_id must look like '<provider>.<verb>': "
                                f"{self.capability_id!r}")
        effect = str(getattr(self.effect_class, "value", self.effect_class))
        if effect not in {item.value for item in EffectClass}:
            raise RegistryError(f"effect_class outside the closed set: {effect!r}")
        object.__setattr__(self, "effect_class", EffectClass(effect))
        tier_rank(self.tier)
        object.__setattr__(self, "tier", Tier(str(getattr(self.tier, "value", self.tier))))
        availability = str(getattr(self.availability, "value", self.availability))
        if availability not in {item.value for item in Availability}:
            raise RegistryError(f"availability outside the closed set: {availability!r}")
        object.__setattr__(self, "availability", Availability(availability))
        if not isinstance(self.param_schema, Mapping):
            raise RegistryError("param_schema must be a mapping")
        properties = self.param_schema.get("properties")
        if not isinstance(properties, Mapping):
            raise RegistryError("param_schema must carry a 'properties' object")
        if not isinstance(self.allowed_params, frozenset):
            raise RegistryError("allowed_params must be a frozenset")
        surplus = self.allowed_params - set(properties)
        if surplus:
            # §4: allowed_params is the exposed subset of param_schema, never a
            # name the schema does not declare.
            raise RegistryError(
                f"{self.capability_id}: allowed_params outside param_schema: {sorted(surplus)}")
        required = self.param_schema.get("required", ())
        if required:
            if (not isinstance(required, Sequence) or isinstance(required, (str, bytes))
                    or not all(isinstance(name, str) and name for name in required)):
                raise RegistryError(
                    f"{self.capability_id}: param_schema.required must be a list of names")
            missing = set(required) - self.allowed_params
            if missing:
                raise RegistryError(
                    f"{self.capability_id}: required parameters not exposed: "
                    f"{sorted(missing)}")
        if self.schema_sha256:
            if len(self.schema_sha256) != 64 or any(
                    ch not in "0123456789abcdef" for ch in self.schema_sha256):
                raise RegistryError(f"{self.capability_id}: schema_sha256 must be 64 hex chars")
        if self.ledger_capability is not None:
            if not isinstance(self.ledger_capability, str) or not self.ledger_capability:
                raise RegistryError(f"{self.capability_id}: ledger_capability must be a name")
        for name, tier in self.escalates_to.items():
            if name not in self.allowed_params:
                raise RegistryError(
                    f"{self.capability_id}: escalating parameter {name!r} is not exposed")
            tier_rank(tier)
        if self.availability is Availability.ENABLED and self.ledger_capability is None:
            raise RegistryError(
                f"{self.capability_id}: ENABLED without a ledger capability would route a call "
                "the ledger cannot price; mark it DISABLED and say why")

    @property
    def effective_tier(self) -> Tier:
        """The tier of the *tool*, before any parameter raises it."""
        return self.tier


@dataclass(frozen=True)
class CapabilitySummary:
    """What a host may see about a capability without loading its schema."""

    capability_id: str
    provider: str
    tier: Tier
    effect_class: EffectClass
    availability: Availability
    readiness: str
    transport: str


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Lazily loaded detail (§4 ``describe``): the schema subset and what may raise the tier."""

    summary: CapabilitySummary
    upstream_tool: str
    auth_scope: str
    cost_meter: str
    param_schema: Mapping[str, object]
    allowed_params: frozenset[str]
    escalates_to: Mapping[str, Tier]
    description: str


class CapabilityRegistry:
    """The reviewed table. Immutable; a derived table is a new instance.

    The earlier shape kept an observation map on whatever instance was handy —
    including the module-level ``DEFAULT_REGISTRY``. A drift observed by one
    caller then downgraded a capability for every other caller in the process,
    so an offline test's result could depend on which test ran first. Observations
    now arrive at construction (``with_schema_verdicts`` / ``with_vendor_check``),
    and the shared default has no mutable state to contaminate.
    """

    def __init__(self, entries: Iterable[CapabilityEntry], *, vendor_pins=None,
                 observations=None, invalidations=None, vendor_checks=None):
        table: dict[str, CapabilityEntry] = {}
        for entry in entries:
            if entry.capability_id in table:
                raise RegistryError(f"duplicate capability_id: {entry.capability_id}")
            table[entry.capability_id] = entry
        self._table = table
        providers = {entry.provider for entry in table.values()}
        pins = {provider: VendorSchemaPin.not_captured(provider) for provider in providers}
        for provider, pin in (vendor_pins or {}).items():
            if provider not in providers:
                raise RegistryError(f"vendor pin for unknown provider: {provider!r}")
            if not isinstance(pin, VendorSchemaPin) or pin.provider != provider:
                raise RegistryError(f"bad vendor pin for {provider!r}")
            pins[provider] = pin
        self._vendor_pins = pins
        observations = dict(observations or {})
        for capability_id, verdict in observations.items():
            if capability_id not in table:
                raise RegistryError(f"observation for unknown capability: {capability_id!r}")
            observations[capability_id] = SchemaVerdict(str(getattr(verdict, "value", verdict)))
        self._observations = observations
        invalidations = dict(invalidations or {})
        for provider, verdict in invalidations.items():
            if provider not in providers:
                raise RegistryError(f"invalidation for unknown provider: {provider!r}")
            invalidations[provider] = VendorSchemaVerdict(
                str(getattr(verdict, "value", verdict)))
        self._invalidations = invalidations
        checks = dict(vendor_checks or {})
        for provider, check in checks.items():
            if provider not in providers or not isinstance(check, VendorSchemaCheck):
                raise RegistryError(f"bad vendor check for {provider!r}")
        self._vendor_checks = checks

    # -- lookup ------------------------------------------------------------ #
    def get(self, capability_id: str) -> CapabilityEntry | None:
        entry = self._table.get(capability_id)
        if entry is None:
            return None
        if entry.availability is Availability.DISABLED:
            # A drift observation must not resurrect a capability this Wave has
            # deliberately left off.
            return entry
        observed = self._observations.get(capability_id)
        if observed in (SchemaVerdict.DRIFT, SchemaVerdict.ABSENT):
            return replace(entry, availability=Availability.SCHEMA_DRIFT)
        if entry.provider in self._invalidations:
            return replace(entry, availability=Availability.SCHEMA_DRIFT)
        return entry

    def list(self, *, provider: str | None = None, tier: Tier | None = None,
             task_kind: str | None = None) -> tuple[CapabilitySummary, ...]:
        """Summaries, filtered. ``task_kind`` is reserved for the router and unused here.

        It is accepted rather than omitted so a caller written against §4 does not
        discover the gap at runtime; an unknown ``task_kind`` filters nothing,
        because this table has no task metadata to filter by yet.
        """
        del task_kind
        entries = []
        for entry in self._table.values():
            current = self.get(entry.capability_id) or entry
            if provider is not None and current.provider != provider:
                continue
            if tier is not None and current.tier is not Tier(str(getattr(tier, "value", tier))):
                continue
            entries.append(_summary(current))
        return tuple(sorted(entries, key=lambda item: item.capability_id))

    def describe(self, capability_id: str) -> CapabilityDescriptor:
        entry = self.get(capability_id)
        if entry is None:
            raise RegistryError(f"unknown capability_id: {capability_id!r}")
        return CapabilityDescriptor(
            summary=_summary(entry), upstream_tool=entry.upstream_tool,
            auth_scope=entry.auth_scope, cost_meter=entry.cost_meter,
            param_schema=entry.param_schema, allowed_params=entry.allowed_params,
            escalates_to=entry.escalates_to, description=entry.description)

    # -- immutable derivation ---------------------------------------------- #
    def with_schema_verdicts(self,
                             verdicts: Mapping[str, SchemaVerdict | str]) -> "CapabilityRegistry":
        """A new registry carrying these per-capability observations.

        ``DRIFT``/``ABSENT`` downgrade the capability's availability to
        ``SCHEMA_DRIFT`` (IR-4); the shared table this was derived from is
        untouched.
        """
        merged = dict(self._observations)
        for capability_id, verdict in verdicts.items():
            if capability_id not in self._table:
                raise RegistryError(f"observation for unknown capability: {capability_id!r}")
            merged[capability_id] = SchemaVerdict(str(getattr(verdict, "value", verdict)))
        return CapabilityRegistry(
            self._table.values(), vendor_pins=self._vendor_pins, observations=merged,
            invalidations=self._invalidations, vendor_checks=self._vendor_checks)

    def with_vendor_pin(self, pin: VendorSchemaPin) -> "CapabilityRegistry":
        """A new registry with one provider's vendor-schema pin replaced."""
        if not isinstance(pin, VendorSchemaPin):
            raise RegistryError("with_vendor_pin requires a VendorSchemaPin")
        if pin.provider not in self._vendor_pins:
            raise RegistryError(f"vendor pin for unknown provider: {pin.provider!r}")
        pins = dict(self._vendor_pins)
        pins[pin.provider] = pin
        return CapabilityRegistry(
            self._table.values(), vendor_pins=pins, observations=self._observations,
            invalidations=self._invalidations, vendor_checks=self._vendor_checks)

    def with_vendor_capture(self, provider: str, payload: Any, *, endpoint: str,
                            captured_at: datetime, provenance: Sequence[str],
                            egress_decision_ref: str, audit_ref: str,
                            source: str = "probe_schema@official_mcp",
                            capture_mode: CaptureMode | str = CaptureMode.SYNTHETIC
                            ) -> "CapabilityRegistry":
        """Pin a captured ``tools/list`` payload for ``provider``.

        ``capture_mode`` defaults to ``SYNTHETIC`` on purpose: a caller must say
        ``LIVE_CAPTURE`` explicitly, and only after a real authorized handshake.
        A synthetic pin can exercise the digest machinery and is never
        authoritative, so an offline test cannot become a LIVE claim by
        forgetting an argument.
        """
        if provider not in self._vendor_pins:
            raise RegistryError(f"vendor capture for unknown provider: {provider!r}")
        pin = VendorSchemaPin.from_payload(
            provider, payload, endpoint=endpoint, captured_at=captured_at,
            provenance=provenance, egress_decision_ref=egress_decision_ref,
            audit_ref=audit_ref, source=source, capture_mode=capture_mode)
        return self.with_vendor_pin(pin)

    def with_vendor_snapshot(self, entry: Mapping[str, Any], *,
                             capture_mode: CaptureMode | str = CaptureMode.SYNTHETIC
                             ) -> "CapabilityRegistry":
        """Pin the exact entry ``bootstrap_probe_schema`` returned.

        This is the integrator's one-liner: the S0 gate has already validated the
        capture, and this method re-checks the fields the registry depends on
        (canonical payload, both digests equal, tool count, endpoint, refs)
        before treating any of it as a pin.
        """
        if not isinstance(entry, Mapping):
            raise VendorSchemaRefused("VENDOR_SCHEMA_ENTRY_NOT_AN_OBJECT",
                                      "a bootstrap snapshot entry must be a mapping")
        provider = str(entry.get("provider", ""))
        if provider not in self._vendor_pins:
            raise VendorSchemaRefused("VENDOR_SCHEMA_PROVIDER_UNKNOWN",
                                      f"no reviewed capabilities for {provider!r}")
        if str(entry.get("method", "")) != "tools/list":
            raise VendorSchemaRefused("VENDOR_SCHEMA_METHOD_NOT_TOOLS_LIST",
                                      "only a tools/list capture may be pinned")
        if str(entry.get("source", "")) not in TRUSTED_VENDOR_CAPTURE_SOURCES:
            raise VendorSchemaRefused("VENDOR_SCHEMA_SOURCE_UNTRUSTED",
                                      f"source {entry.get('source')!r} is not trusted")
        serialized = entry.get("snapshot_serialized")
        if not isinstance(serialized, str) or not serialized:
            raise VendorSchemaRefused("VENDOR_SCHEMA_SERIALIZED_MISSING",
                                      "the captured payload is required")
        try:
            payload = json.loads(serialized)
        except (TypeError, ValueError) as exc:
            raise VendorSchemaRefused("VENDOR_SCHEMA_SERIALIZED_NOT_JSON",
                                      str(exc)) from exc
        if canonical_tools_list(payload) != serialized:
            raise VendorSchemaRefused("VENDOR_SCHEMA_NOT_CANONICAL",
                                      "the captured payload is not canonical JSON")
        digest = tools_list_digest(payload)
        declared = str(entry.get("schema_sha256", "")).lower()
        registry_digest = str(entry.get("registry_schema_sha256", "")).lower()
        if declared != digest:
            raise VendorSchemaRefused("VENDOR_SCHEMA_HASH_MISMATCH",
                                      "schema_sha256 is not the payload's digest")
        if registry_digest != digest:
            raise VendorSchemaRefused("VENDOR_SCHEMA_REGISTRY_DIGEST_MISMATCH",
                                      "registry_schema_sha256 is not the payload's digest")
        tool_count = entry.get("tool_count")
        if type(tool_count) is not int or tool_count != len(payload.get("tools", ())):
            raise VendorSchemaRefused("VENDOR_SCHEMA_TOOL_COUNT_MISMATCH",
                                      "tool_count does not match the captured payload")
        captured_at = _parse_iso(entry.get("captured_at"))
        if captured_at is None:
            raise VendorSchemaRefused("VENDOR_SCHEMA_CAPTURED_AT_INVALID",
                                      "captured_at must be an ISO timestamp with a timezone")
        return self.with_vendor_pin(VendorSchemaPin(
            provider=provider, subject=HashSubject.FULL_VENDOR_TOOLS_LIST,
            vendor_schema_sha256=digest, endpoint=str(entry.get("endpoint", "")),
            captured_at=captured_at,
            provenance=("bootstrap_probe_schema:" + str(entry.get("audit_ref", "")),),
            egress_decision_ref=str(entry.get("egress_decision_ref", "")),
            audit_ref=str(entry.get("audit_ref", "")), tool_count=tool_count,
            source=str(entry.get("source", "")), capture_mode=capture_mode,
            snapshot_serialized=serialized))

    # -- vendor schema metadata -------------------------------------------- #
    @property
    def pinned_hash_subject(self) -> HashSubject:
        """The subject of the per-capability ``schema_sha256`` pins."""
        return HashSubject.HOST_CAPABILITY_SUBSET

    def vendor_schema_pin(self, provider: str) -> VendorSchemaPin:
        pin = self._vendor_pins.get(provider)
        if pin is None:
            raise RegistryError(f"unknown provider: {provider!r}")
        return pin

    def vendor_schema_sha256_for(self, provider: str) -> str | None:
        """The mechanical digest of a full pin, or ``None`` when nothing was pinned.

        This does **not** claim the pin is authoritative; synthetic pins answer
        here so a test can compare digests. Use
        :meth:`authoritative_vendor_schema_sha256` for the S0 handoff.
        """
        pin = self.vendor_schema_pin(provider)
        if pin.subject is not HashSubject.FULL_VENDOR_TOOLS_LIST:
            return None
        return pin.vendor_schema_sha256

    def authoritative_vendor_schema_sha256(self, provider: str, *,
                                           as_of: datetime | None = None) -> str | None:
        """The digest S0 may consume: only a live, trusted, fresh capture."""
        pin = self.vendor_schema_pin(provider)
        return pin.vendor_schema_sha256 if pin.is_authoritative(as_of) else None

    @property
    def vendor_schema_sha256(self) -> str | None:
        """The single authoritative vendor digest, for the S0 bootstrap recipe.

        ``None`` is the honest answer while nothing has been captured — and also
        for a synthetic pin, so an offline fixture cannot be handed to S0 as if
        it were a vendor reading. With more than one authoritative provider the
        question is ambiguous and raises rather than picking one.
        """
        authoritative = [pin.vendor_schema_sha256 for pin in self._vendor_pins.values()
                         if pin.is_authoritative()]
        if not authoritative:
            return None
        if len(authoritative) > 1:
            raise RegistryError(
                "more than one provider has an authoritative vendor digest; "
                "use authoritative_vendor_schema_sha256(provider)")
        return authoritative[0]

    def authoritative_status(self, provider: str, *, as_of: datetime | None = None
                             ) -> VendorSchemaStatus:
        if provider in self._invalidations:
            return VendorSchemaStatus.INVALIDATED
        return self.vendor_schema_pin(provider).status(as_of)

    def vendor_schema_statuses(self, *, as_of: datetime | None = None
                               ) -> Mapping[str, VendorSchemaStatus]:
        return {provider: self.authoritative_status(provider, as_of=as_of)
                for provider in self._vendor_pins}

    def last_vendor_check(self, provider: str) -> VendorSchemaCheck | None:
        return self._vendor_checks.get(provider)

    def require_authoritative_vendor_schema(self, provider: str, *,
                                            as_of: datetime | None = None) -> str:
        """Return the authoritative digest or refuse with a governed code."""
        status = self.authoritative_status(provider, as_of=as_of)
        if status is not VendorSchemaStatus.VERIFIED:
            raise VendorSchemaRefused(
                f"VENDOR_SCHEMA_{status.value}",
                f"{provider}: no authoritative vendor tools/list capture "
                f"(status {status.value}); S0_07 must stay DENY")
        digest = self.authoritative_vendor_schema_sha256(provider, as_of=as_of)
        assert digest is not None
        return digest

    def check_vendor_schema(self, provider: str, observed_tools_list: Any, *,
                            as_of: datetime | None = None, endpoint: str | None = None,
                            source: str | None = None,
                            egress_decision_ref: str | None = None,
                            audit_ref: str | None = None) -> VendorSchemaCheck:
        """Compare an observed ``tools/list`` with the pin, strictly.

        The comparison is over the canonical bytes of the whole payload, so two
        catalogs with the same tool names but different argument schemas,
        descriptions or annotations are ``DRIFT``. No names-only shortcut exists.
        """
        pin = self.vendor_schema_pin(provider)
        moment = _as_utc(as_of)
        if provider in self._invalidations:
            return VendorSchemaCheck(
                provider=provider, verdict=VendorSchemaVerdict.INVALIDATED,
                authoritative=False,
                expected_sha256=pin.vendor_schema_sha256, observed_sha256=None,
                expected_tool_count=pin.tool_count,
                problems=("VENDOR_SCHEMA_INVALIDATED",),
                detail=f"invalidated by {self._invalidations[provider].value}")
        if pin.subject is not HashSubject.FULL_VENDOR_TOOLS_LIST or \
                pin.vendor_schema_sha256 is None:
            return VendorSchemaCheck(
                provider=provider, verdict=VendorSchemaVerdict.NOT_CAPTURED,
                authoritative=False, expected_sha256=None, observed_sha256=None,
                problems=("VENDOR_SCHEMA_NOT_CAPTURED",),
                detail="no full vendor tools/list has been captured and trusted")
        status = pin.status(moment)
        if status is VendorSchemaStatus.STALE:
            return VendorSchemaCheck(
                provider=provider, verdict=VendorSchemaVerdict.STALE,
                authoritative=False, expected_sha256=pin.vendor_schema_sha256,
                observed_sha256=None, expected_tool_count=pin.tool_count,
                problems=("VENDOR_SCHEMA_STALE",),
                detail="the capture is outside the freshness window")
        if status is VendorSchemaStatus.UNTRUSTED:
            return VendorSchemaCheck(
                provider=provider, verdict=VendorSchemaVerdict.UNTRUSTED,
                authoritative=False, expected_sha256=pin.vendor_schema_sha256,
                observed_sha256=None, expected_tool_count=pin.tool_count,
                problems=("VENDOR_SCHEMA_UNTRUSTED",),
                detail="the capture source is not trusted")

        problems: list[str] = []
        if endpoint is not None and endpoint != pin.endpoint:
            problems.append("VENDOR_SCHEMA_ENDPOINT_MISMATCH")
        if source is not None and source != pin.source:
            problems.append("VENDOR_SCHEMA_SOURCE_MISMATCH")
        if egress_decision_ref is not None and egress_decision_ref != pin.egress_decision_ref:
            problems.append("VENDOR_SCHEMA_EGRESS_REF_MISMATCH")
        if audit_ref is not None and audit_ref != pin.audit_ref:
            problems.append("VENDOR_SCHEMA_AUDIT_REF_MISMATCH")
        if problems:
            return VendorSchemaCheck(
                provider=provider, verdict=VendorSchemaVerdict.UNTRUSTED,
                authoritative=False, expected_sha256=pin.vendor_schema_sha256,
                observed_sha256=None, expected_tool_count=pin.tool_count,
                problems=tuple(problems),
                detail="the observation's provenance is not the pin's")

        observed_problems = tools_list_problems(observed_tools_list,
                                                require_complete=False)
        if observed_problems:
            observed_digest = None
            try:
                observed_digest = tools_list_digest(observed_tools_list)
            except RegistryError:
                pass
            return VendorSchemaCheck(
                provider=provider, verdict=VendorSchemaVerdict.UNVERIFIED,
                authoritative=False, expected_sha256=pin.vendor_schema_sha256,
                observed_sha256=observed_digest,
                expected_tool_count=pin.tool_count,
                problems=observed_problems,
                detail="the observation is not a tools/list payload")

        expected_payload = json.loads(pin.snapshot_serialized or "{}")
        expected_tools = _tool_map(expected_payload)
        observed_tools = _tool_map(observed_tools_list)
        changed = tuple(sorted(name for name in expected_tools.keys() & observed_tools.keys()
                               if expected_tools[name] != observed_tools[name]))
        added = tuple(sorted(observed_tools.keys() - expected_tools.keys()))
        removed = tuple(sorted(expected_tools.keys() - observed_tools.keys()))
        observed_digest = tools_list_digest(observed_tools_list)
        verdict = (VendorSchemaVerdict.MATCH
                   if observed_digest == pin.vendor_schema_sha256
                   else VendorSchemaVerdict.DRIFT)
        return VendorSchemaCheck(
            provider=provider, verdict=verdict,
            authoritative=(verdict is VendorSchemaVerdict.MATCH
                           and pin.is_authoritative(moment)),
            expected_sha256=pin.vendor_schema_sha256,
            observed_sha256=observed_digest,
            expected_tool_count=pin.tool_count,
            observed_tool_count=len(observed_tools_list["tools"]),
            changed_tools=changed, added_tools=added, removed_tools=removed,
            detail=("the observed payload is the pinned one" if verdict is
                    VendorSchemaVerdict.MATCH else
                    "the observed payload differs from the pinned one"))

    def with_vendor_check(self, check: VendorSchemaCheck) -> "CapabilityRegistry":
        """A new registry carrying this check; a failure invalidates the provider.

        ``DRIFT``/``STALE``/``UNTRUSTED`` downgrade every capability of that
        provider to ``SCHEMA_DRIFT`` (IR-4). Only an authoritative ``MATCH``
        clears an earlier invalidation.
        """
        if not isinstance(check, VendorSchemaCheck):
            raise RegistryError("with_vendor_check requires a VendorSchemaCheck")
        if check.provider not in self._vendor_pins:
            raise RegistryError(f"vendor check for unknown provider: {check.provider!r}")
        checks = dict(self._vendor_checks)
        checks[check.provider] = check
        invalidations = dict(self._invalidations)
        if check.verdict in (VendorSchemaVerdict.DRIFT, VendorSchemaVerdict.STALE,
                             VendorSchemaVerdict.UNTRUSTED, VendorSchemaVerdict.INVALIDATED):
            invalidations[check.provider] = check.verdict
        elif check.verdict is VendorSchemaVerdict.MATCH and check.authoritative:
            invalidations.pop(check.provider, None)
        return CapabilityRegistry(
            self._table.values(), vendor_pins=self._vendor_pins,
            observations=self._observations, invalidations=invalidations,
            vendor_checks=checks)

    # -- schema drift (IR-4) ----------------------------------------------- #
    def verify_schema(self, capability_id: str,
                      observed_tools_list: Mapping[str, object]) -> SchemaVerdict:
        """Compare one observed tool against the reviewed **host-subset** snapshot.

        Pure: the verdict is returned, not stored. A caller that wants the table
        to act on it derives one with :meth:`with_schema_verdicts`, so the shared
        default cannot be contaminated by whoever probed first.

        Without a recorded host-subset digest the honest answer is ``UNVERIFIED``:
        this checkout has not been authorised to do a live handshake, and a
        fabricated ``MATCH`` would let a drifted upstream tool run as if it had
        been reviewed. A tool that is gone is ``ABSENT``; a tool whose schema
        digest differs is ``DRIFT``.
        """
        entry = self._table.get(capability_id)
        if entry is None:
            raise RegistryError(f"unknown capability_id: {capability_id!r}")
        tool = _find_tool(observed_tools_list, entry.upstream_tool)
        if tool is None:
            return SchemaVerdict.ABSENT
        if not entry.schema_sha256:
            return SchemaVerdict.UNVERIFIED
        observed = tool.get("inputSchema") or tool.get("input_schema") or {}
        if schema_digest(observed) == entry.schema_sha256:
            return SchemaVerdict.MATCH
        return SchemaVerdict.DRIFT

    def observed_verdicts(self) -> Mapping[str, SchemaVerdict]:
        """Observations carried by this derived registry; empty on the default."""
        return dict(self._observations)

    def __len__(self) -> int:
        return len(self._table)

    def __iter__(self):
        return iter(self._table.values())


def _summary(entry: CapabilityEntry) -> CapabilitySummary:
    return CapabilitySummary(
        capability_id=entry.capability_id, provider=entry.provider, tier=entry.tier,
        effect_class=entry.effect_class, availability=entry.availability,
        readiness=entry.readiness, transport=entry.transport)


def _find_tool(observed: Mapping[str, object], upstream_tool: str) -> Mapping[str, object] | None:
    if not isinstance(observed, Mapping):
        raise RegistryError("an observed tools/list must be a mapping")
    tools = observed.get("tools")
    if not isinstance(tools, Sequence) or isinstance(tools, (str, bytes)):
        raise RegistryError("an observed tools/list must carry a 'tools' sequence")
    for tool in tools:
        if isinstance(tool, Mapping) and tool.get("name") == upstream_tool:
            return tool
    return None


# --------------------------------------------------------------------------- #
# The reviewed table
# --------------------------------------------------------------------------- #

def build_entry(capability_id: str, provider: str, upstream_tool: str, transport: str,
           effect: EffectClass, tier: Tier, auth_scope: str, meter: str,
           params: Mapping[str, str | Mapping[str, object]],
           ledger_capability: str | None, *,
           availability: Availability = Availability.ENABLED,
           readiness: str = "OFFLINE_TESTED", escalates_to: Mapping[str, Tier] | None = None,
           description: str = "", required: Iterable[str] = ()) -> CapabilityEntry:
    schema = param_schema_for(params, required=required)
    return CapabilityEntry(
        capability_id=capability_id, provider=provider, upstream_tool=upstream_tool,
        transport=transport, effect_class=effect, tier=tier, auth_scope=auth_scope,
        cost_meter=meter, param_schema=schema, allowed_params=frozenset(schema["properties"]),
        # No live handshake has been authorised, so no reviewed vendor snapshot
        # exists yet. Empty means "unverified", never "matches".
        schema_sha256="",
        availability=availability, readiness=readiness,
        ledger_capability=ledger_capability, escalates_to=dict(escalates_to or {}),
        description=description)


#: The reviewed capabilities, one entry per thing an adapter actually advertises.
#:
#: Every ``capability_id`` here is one the corresponding adapter's ``capabilities()``
#: returns; the test suite compares the two sets rather than trusting this comment.
#: ``readiness`` is deliberately never higher than OFFLINE_TESTED: no live provider
#: call was made to produce this table.
DEFAULT_REGISTRY = CapabilityRegistry((
    # -- TinyFish: embedded official MCP -----------------------------------
    build_entry("tinyfish.search", "tinyfish", "search", "official_mcp", EffectClass.READ_ONLY,
           Tier.T1, "tinyfish:search", "search_query",
           {"query": "string", "domain_type": "string", "location": "string", "language": "string",
            "recency_minutes": "integer", "page": "integer", "purpose": "string"},
           "search", description="Free-tier web/news/research-paper discovery."),
    build_entry("tinyfish.fetch_content", "tinyfish", "fetch_content", "official_mcp",
           EffectClass.READ_ONLY, Tier.T1, "tinyfish:fetch", "fetch_url",
           {"urls": "array", "format": "string", "links": "boolean",
            "image_links": "boolean", "iframe_links": "boolean", "page_metadata": "boolean"},
           "fetch", description="Rendered page retrieval; the four flags are upstream-required."),
    build_entry("tinyfish.get_run", "tinyfish", "get_run", "official_mcp", EffectClass.READ_ONLY,
           Tier.T0, "tinyfish:usage", "request", {"id": "string"}, "usage_read",
           description="Status of one agent run. Read-only; never re-dispatches."),
    build_entry("tinyfish.list_runs", "tinyfish", "list_runs", "official_mcp",
           EffectClass.READ_ONLY, Tier.T0, "tinyfish:usage", "request", {"limit": "integer"},
           "usage_read"),
    build_entry("tinyfish.batch_status", "tinyfish", "batch_status", "official_mcp",
           EffectClass.READ_ONLY, Tier.T0, "tinyfish:usage", "request",
           # The published wire is `batch_status(run_ids)` — a list of 1..8 opaque
           # run ids, each `^[A-Za-z0-9_-]{1,128}$` (references/providers/tinyfish.md,
           # TINYFISH_OFFICIAL_MCP_CONTRACT.md §1/§2, and the pinned adapter spec).
           # `batch_id` was never a vendor parameter; keeping it would leave a
           # reviewed T0 read advertised but unroutable.
           {"run_ids": {"type": "array", "items": {"type": "string",
                                                   "pattern": "^[A-Za-z0-9_-]{1,128}$"},
                        "minItems": 1, "maxItems": 8}},
           "usage_read", required=("run_ids",),
           description="Status of up to eight submitted runs. Published no-charge "
                       "read; never creates or re-dispatches a run."),
    build_entry("tinyfish.get_wallet", "tinyfish", "get_wallet", "official_mcp",
           EffectClass.READ_ONLY, Tier.T0, "tinyfish:wallet", "request", {}, "wallet_read",
           description="Read-only wallet snapshot; the B0 evidence source (§1.1)."),
    build_entry("tinyfish.get_search_usage", "tinyfish", "get_search_usage", "official_mcp",
           EffectClass.READ_ONLY, Tier.T0, "tinyfish:usage", "request", {}, "usage_read"),
    build_entry("tinyfish.list_fetch_usage", "tinyfish", "list_fetch_usage", "official_mcp",
           EffectClass.READ_ONLY, Tier.T0, "tinyfish:usage", "request", {}, "usage_read"),

    # -- TinyFish account inventory reads (documented, not live-captured) ---
    # The reviewed upstream catalog (`references/providers/tinyfish.md`, pinned
    # 2026-10-17) and `references/TINYFISH_OFFICIAL_MCP_CONTRACT.md` §2 record
    # these three as T1 read-only account reads that may not charge. They are
    # added on that **documented** basis, not on a captured `tools/list`:
    # `vendor_schema_sha256` is still None and readiness stays OFFLINE_TESTED.
    # The allowlists are deliberately narrower than the adapter's:
    # `list_browser_sessions` exposes only `limit`/`page`, the two parameters the
    # provider reference documents with defaults; the adapter's `status`,
    # `session_id`, `start_after`, `end_before` stay unexposed until documented.
    # `get_monitor` carries its documented required handle; the billable verbs of
    # the monitor and browser-session families stay DISABLED at T4.
    build_entry("tinyfish.get_monitor", "tinyfish", "get_monitor", "official_mcp",
           EffectClass.READ_ONLY, Tier.T1, "tinyfish:monitor", "request",
           {"monitor_id": "string"}, "usage_read", required=("monitor_id",),
           description="Read one scheduled monitor. Documented no-charge read; "
                       "creating, running or resuming a monitor stays DISABLED/T4."),
    build_entry("tinyfish.list_monitors", "tinyfish", "list_monitors", "official_mcp",
           EffectClass.READ_ONLY, Tier.T1, "tinyfish:monitor", "request", {},
           "usage_read",
           description="List scheduled monitors. Documented no-charge read; the "
                       "provider reference publishes no parameters for it."),
    build_entry("tinyfish.list_browser_sessions", "tinyfish", "list_browser_sessions",
           "official_mcp", EffectClass.READ_ONLY, Tier.T1, "tinyfish:browser_session",
           "request", {"limit": "integer", "page": "integer"}, "usage_read",
           description="List the account's browser sessions. Documented no-charge "
                       "read; limit/page carry documented defaults. Creating or "
                       "closing a session stays DISABLED/T4."),

    build_entry("tinyfish.run_web_automation", "tinyfish", "run_web_automation", "official_mcp",
           EffectClass.BILLABLE_ACT, Tier.T3, "tinyfish:agent", "agent_step",
           {"url": "string", "goal": "string", "session_id": "string",
            "output_schema": "object", "browser_profile": "string", "proxy_config": "object",
            "use_profile": "boolean", "profile_id": "string", "use_vault": "boolean",
            "credential_item_ids": "array", "capture_config": "object",
            "webhook_url": "string"},
           "agent_run", readiness="CODED",
           escalates_to={"use_profile": Tier.T4, "profile_id": Tier.T4,
                         "use_vault": Tier.T4, "credential_item_ids": Tier.T4,
                         "webhook_url": Tier.T4},
           description="Open-ended metered steps. Login state or an outbound webhook raises "
                       "it to T4."),
    build_entry("tinyfish.cancel_run", "tinyfish", "cancel_run", "official_mcp",
           EffectClass.STATE_CHANGING, Tier.T4, "tinyfish:agent", "request",
           {"run_id": "string"}, None, availability=Availability.DISABLED,
           readiness="DEFERRED",
           description="State-changing on the vendor side; this Wave does not enable "
                       "state-changing capabilities. Recorded as a gap, not omitted."),
    build_entry("tinyfish.batch_cancel", "tinyfish", "batch_cancel", "official_mcp",
           EffectClass.STATE_CHANGING, Tier.T4, "tinyfish:agent", "request",
           {"batch_id": "string"}, None, availability=Availability.DISABLED,
           readiness="DEFERRED", description="Same as cancel_run."),

    # -- SerpApi: embedded official MCP ------------------------------------
    build_entry("serpapi.search", "serpapi", "search", "official_mcp", EffectClass.BILLABLE_READ,
           Tier.T2, "serpapi:search", "search_query",
           {"engine": "string", "query": "string", "country": "string", "language": "string",
            "limit": "integer"},
           "search", description="Metered multi-engine search; one credit per call."),
    build_entry("serpapi.engine_catalog", "serpapi", "engine_catalog", "official_mcp",
           EffectClass.READ_ONLY, Tier.T0, "serpapi:resources", "request", {}, None,
           availability=Availability.DISABLED, readiness="DEFERRED",
           description="A resource read the ledger has no reviewed capability to price; "
                       "the review vocabulary must grow before this can be dispatched."),
    build_entry("serpapi.engine_schema", "serpapi", "engine_schema", "official_mcp",
           EffectClass.READ_ONLY, Tier.T0, "serpapi:resources", "request",
           {"engine": "string"}, None, availability=Availability.DISABLED,
           readiness="DEFERRED", description="As engine_catalog."),

    # -- Serper: REST ------------------------------------------------------
    build_entry("serper.search", "serper", "search", "rest", EffectClass.BILLABLE_READ, Tier.T2,
           "serper:search", "search_query",
           {"query": "string", "mode": "string", "limit": "integer", "country": "string",
            "language": "string", "page": "integer"},
           "search", description="Metered Google SERP; num>10 costs a second credit."),

    # -- Crawl4AI: local SDK, through the egress proxy ----------------------
    build_entry("crawl4ai.fetch_page", "crawl4ai", "fetch_page", "local_sdk",
           EffectClass.READ_ONLY, Tier.T1, "crawl4ai:local", "local_page",
           {"url": "string", "limits": "object", "extraction": "object",
            "mode": "string", "max_chars": "integer"},
           "render", description="Local render; zero API price, real CPU and egress."),
    build_entry("crawl4ai.extract", "crawl4ai", "extract", "local_sdk", EffectClass.READ_ONLY,
           Tier.T1, "crawl4ai:local", "local_page",
           {"url": "string", "html": "string", "limits": "object", "extraction": "object",
            "max_chars": "integer"}, "extract"),
    build_entry("crawl4ai.deep_crawl", "crawl4ai", "deep_crawl", "local_sdk",
           EffectClass.READ_ONLY, Tier.T1, "crawl4ai:local", "local_page",
           {"url": "string", "max_pages": "integer", "max_depth": "integer",
            "same_domain_only": "boolean"}, "render"),
    build_entry("crawl4ai.sitemap_seed", "crawl4ai", "sitemap_seed", "local_sdk",
           EffectClass.READ_ONLY, Tier.T1, "crawl4ai:local", "local_page",
           {"url": "string", "max_urls": "integer"}, "render"),
    build_entry("crawl4ai.screenshot", "crawl4ai", "screenshot", "local_sdk",
           EffectClass.READ_ONLY, Tier.T1, "crawl4ai:local", "local_page",
           {"url": "string", "full_page": "boolean", "timeout_ms": "integer"}, "render"),
    build_entry("crawl4ai.pdf", "crawl4ai", "pdf", "local_sdk", EffectClass.READ_ONLY, Tier.T1,
           "crawl4ai:local", "local_page",
           {"url": "string", "timeout_ms": "integer"}, "render"),

    # -- TypeSafe Jev: Python SDK ------------------------------------------
    build_entry("jev.choice", "typesafe_jev", "choice", "python_sdk", EffectClass.BILLABLE_READ,
           Tier.T2, "typesafe_jev:judge", "request",
           {"artifact_id": "string", "question": "string", "options": "array"},
           "judge", description="Typed choice over server-held evidence; input pinned to an "
                                "artifact, never to Agent-supplied text."),
    build_entry("jev.noul", "typesafe_jev", "noul", "python_sdk", EffectClass.BILLABLE_READ,
           Tier.T2, "typesafe_jev:judge", "request",
           {"artifact_id": "string", "question": "string"}, "judge"),
    build_entry("jev.score", "typesafe_jev", "score", "python_sdk", EffectClass.BILLABLE_READ,
           Tier.T2, "typesafe_jev:judge", "request",
           {"artifact_id": "string", "question": "string", "scale": "array"}, "judge"),
    build_entry("jev.box", "typesafe_jev", "box", "python_sdk", EffectClass.BILLABLE_READ,
           Tier.T2, "typesafe_jev:judge", "request",
           {"artifact_id": "string", "target": "string"}, "judge"),
))

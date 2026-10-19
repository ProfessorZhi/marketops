#!/usr/bin/env python3
"""The managed adapter base and its conformance check (§6, IA-1..IA-6).

Five adapters were written by four other lanes before this module existed. They
agree on the *shape* the gateway calls — ``provider``, ``capabilities()``,
``probe_schema()``, ``execute(req)``, ``cancel(ref)`` — but they were written
against that shape independently, so "they all look the same" is a claim worth
measuring rather than assuming. :func:`check_adapter_conformance` measures it, and
:class:`ManagedAdapter` is the base a new adapter should use so the shared
discipline is inherited instead of re-argued per provider.

## What the base actually enforces

* **No retry.** IA-3: a failed or timed-out dispatch is not retried inside an
  adapter. ``execute`` calls ``_dispatch`` exactly once.
* **Refusals are named.** :class:`AdapterRefusal` carries a §5.3 code, and the
  gateway already treats a ``ValueError`` with such a code as a proven local
  refusal. A refusal with a code outside the set is a bug and is refused at
  construction rather than reaching a host as a plausible-looking string.
* **No invented schema snapshot.** ``probe_schema`` answers
  ``{"status": "AUTH_REQUIRED"}`` unless a subclass supplies a real snapshot. A
  fabricated ``MATCH`` is how a drifted upstream tool keeps running as reviewed.
* **Results are validated where the adapter still has a stack.** A mapping is
  coerced through :class:`research_contract.AdapterResult`, which rejects a bad
  outcome, a ``float`` amount, or a BLOCKED result with no closed-set reason.

The base does **not** read credentials, choose a transport, or decide a tier.
Those are the adapter's and the policy plane's jobs respectively (IA-1, IA-2).
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from research_contract import (AdapterResult, CapabilityRequest, REASON_CODES,
                               SchemaVerdict)

__all__ = [
    "ProviderAdapter", "ManagedAdapter", "AdapterRefusal", "RESULT_FIELDS",
    "AdapterConformance", "check_adapter_conformance",
]

#: §3.4, field for field. A dict-like adapter result is coerced through these.
RESULT_FIELDS = ("outcome", "payload", "artifacts", "upstream_ref", "actual_usd",
                 "actual_source", "quota_units_used", "transport", "schema_verdict",
                 "caveats")


@runtime_checkable
class ProviderAdapter(Protocol):
    """What the gateway calls (§6). Structural, because the five adapters predate it."""

    provider: str

    def capabilities(self) -> Sequence[str]: ...

    def probe_schema(self) -> Mapping[str, object]: ...

    def execute(self, req: CapabilityRequest) -> AdapterResult: ...

    def cancel(self, upstream_ref: str) -> AdapterResult: ...


class AdapterRefusal(ValueError):
    """A proven local refusal: a §5.3 code and no dispatch.

    It inherits ``ValueError`` because that is this repository's declared
    refusal channel and the gateway already reads it. The code is checked here so
    a typo cannot become a plausible-looking reason on someone's screen.
    """

    def __init__(self, code: str, detail: str = ""):
        if code not in REASON_CODES:
            raise ValueError(f"adapter refusal code outside the §5.3 set: {code!r}")
        self.reason_code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class ManagedAdapter:
    """Shared adapter discipline. Subclasses implement :meth:`_dispatch`."""

    provider: str = ""
    capability_ids: tuple[str, ...] = ()
    transport: str = ""

    def capabilities(self) -> tuple[str, ...]:
        """The capability ids this adapter serves; the registry must agree."""
        return tuple(self.capability_ids)

    def probe_schema(self) -> Mapping[str, object]:
        """A read-only handshake. Without an authorised one, say so.

        The default is not a placeholder: it is the honest answer for a lane that
        has not been authorised to perform a live ``tools/list``, and the schema
        registry treats it as ``UNVERIFIED`` rather than as a match.
        """
        return {"status": "AUTH_REQUIRED"}

    def execute(self, req: CapabilityRequest) -> AdapterResult:
        """Dispatch exactly once. Never retry; never decide a tier or a budget."""
        if not isinstance(req, CapabilityRequest):
            raise AdapterRefusal(
                "CAPABILITY_PARAM_NOT_ALLOWED",
                "an adapter is given the contract's CapabilityRequest, not a bare mapping")
        if req.capability_id not in self.capabilities():
            raise AdapterRefusal(
                "CAPABILITY_NOT_AVAILABLE",
                f"{self.provider} does not serve {req.capability_id}")
        result = self._dispatch(req)
        return self._coerce(result)

    def _dispatch(self, req: CapabilityRequest) -> AdapterResult | Mapping[str, object]:
        raise NotImplementedError

    def cancel(self, upstream_ref: str) -> AdapterResult:
        """Idempotent cancellation, when the upstream declares it (§6)."""
        raise AdapterRefusal(
            "CAPABILITY_NOT_AVAILABLE",
            f"{self.provider} has no idempotent cancel for {upstream_ref!r}")

    def _coerce(self, result: AdapterResult | Mapping[str, object]) -> AdapterResult:
        if isinstance(result, AdapterResult):
            return result
        if not isinstance(result, Mapping):
            raise AdapterRefusal(
                "CAPABILITY_PARAM_NOT_ALLOWED",
                f"adapter returned {type(result).__name__}, not an AdapterResult")
        payload = {name: result[name] for name in RESULT_FIELDS if name in result}
        payload.setdefault("transport", self.transport)
        payload.setdefault("schema_verdict", SchemaVerdict.UNVERIFIED)
        return AdapterResult(**payload)


@dataclass(frozen=True)
class AdapterConformance:
    """Whether the gateway can call this adapter, and precisely what is wrong if not."""

    provider: str
    conformant: bool
    problems: tuple[str, ...]
    capabilities: tuple[str, ...]
    execute_takes_request: bool
    cancel_takes_ref: bool


def _accepts_single_argument(function: Any, *, name: str) -> bool:
    """Whether ``function`` can be called as ``function(value)``, positionally."""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return False
    parameters = list(signature.parameters.values())
    positional = [item for item in parameters
                  if item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD)]
    required_keyword_only = [item for item in parameters
                             if item.kind is item.KEYWORD_ONLY and item.default is item.empty]
    # One positional parameter (or more, the first is the request) and no required
    # keyword-only parameter the gateway does not know how to pass.
    return bool(positional) and not required_keyword_only and parameters[0].name != ""


def check_adapter_conformance(adapter: Any, *, expected_provider: str | None = None,
                              registry: Any | None = None) -> AdapterConformance:
    """Measure one adapter against §6. Never raises: absence is a problem, not a crash.

    This is what tells the composition root which of the five adapters the gateway
    can *actually* call. A real adapter with a different ``execute`` signature is
    reported as such — the gateway would raise ``TypeError`` on dispatch, and a
    caveat is a much better outcome than a traceback discovered in production.
    """
    problems: list[str] = []
    provider = str(getattr(adapter, "provider", "") or "")
    if not provider:
        problems.append("NO_PROVIDER_ATTRIBUTE")
    elif expected_provider is not None and provider != expected_provider:
        problems.append(f"PROVIDER_MISMATCH:{provider}!={expected_provider}")

    capabilities_function = getattr(adapter, "capabilities", None)
    capabilities: tuple[str, ...] = ()
    if not callable(capabilities_function):
        problems.append("NO_CAPABILITIES_METHOD")
    else:
        try:
            declared = capabilities_function()
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            problems.append(f"CAPABILITIES_RAISED:{type(exc).__name__}")
            declared = ()
        capabilities = tuple(str(item) for item in declared or ())
        if not capabilities:
            problems.append("NO_CAPABILITIES_DECLARED")
        if registry is not None:
            unknown = sorted(cap for cap in capabilities
                             if registry.get(cap) is None)
            if unknown:
                # The registry is the allowlist authority; an adapter advertising
                # something unreviewed is a cross-check failure, not a new capability.
                problems.append("CAPABILITIES_NOT_IN_REGISTRY:" + ",".join(unknown))

    execute = getattr(adapter, "execute", None)
    execute_ok = callable(execute) and _accepts_single_argument(execute, name="execute")
    if not callable(execute):
        problems.append("NO_EXECUTE_METHOD")
    elif not execute_ok:
        problems.append("EXECUTE_SIGNATURE_NOT_CALLABLE_AS_execute(request)")

    cancel = getattr(adapter, "cancel", None)
    cancel_ok = callable(cancel) and _accepts_single_argument(cancel, name="cancel")
    if not callable(cancel):
        problems.append("NO_CANCEL_METHOD")

    probe = getattr(adapter, "probe_schema", None)
    if not callable(probe):
        problems.append("NO_PROBE_SCHEMA_METHOD")

    return AdapterConformance(
        provider=provider, conformant=not problems, problems=tuple(problems),
        capabilities=capabilities, execute_takes_request=execute_ok,
        cancel_takes_ref=cancel_ok)

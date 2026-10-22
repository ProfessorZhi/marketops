#!/usr/bin/env python3
"""The single managed choke point: Registry -> Policy -> Ledger -> Adapter.

Architecture D ("managed capability gateway") says one customer-visible MCP, one
typed capability registry, one policy plane, one authoritative ledger and five
isolated adapters. Each of those is somebody's module. **This module is the joint
between them** and owns exactly one thing the parts cannot own individually: the
*order* and the *gating* of a single managed call.

    resolve capability_id      Registry          unknown -> BLOCKED, no network
    reject disallowed params   Registry subset   fuzz-safe, before any budget
    availability gate          Registry          DISABLED / SCHEMA_DRIFT(T2+)
    route to one adapter       Registry+adapters no silent fallback
    assemble CapabilityRequest contract          transport is NOT a field
    authorize                  Policy            DENY / REQUIRE_HUMAN_GRANT
    reserve atomically         Ledger            ceiling check before the network
    dispatch                   Adapter           exactly once, never retried
    settle / mark_unknown      Ledger            UNKNOWN holds the exposure

Two properties are deliberately not delegated:

* **No adapter is ever dispatched without a Reservation.** An adapter that could
  be reached without one is an accidental new paid path, which is the failure the
  owner brief §2.5 exists to prevent. The reservation precedes the dispatch by
  construction, and the integration tests assert the ordering rather than trust it.
* **Refusals are closed-set codes.** A policy or ledger that answers with prose
  is not translated into a plausible-looking code; it is a `ContractViolation`
  and it is loud. Silently mapping "because I said so" onto a real reason code
  would make the audit trail lie.

`ContractViolation` and `WiringIncomplete` are raised, never returned, so they
cannot become a `reason_code` and dilute the closed set below.

Deliberately absent: retries, cross-provider failover, transport selection,
tier inference from agent input. Each of those is a decision the contract puts
somewhere else, and putting it here would create a second policy source.
"""
from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# INTERFACE-CONTRACT.md v1.0 §5.3, transcribed verbatim. New codes go through the
# coordinator's CHANGE-nn process; nothing here is invented locally.
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

TIER_ORDER = ("T0", "T1", "T2", "T3", "T4")
CONTRACT_MODULE = "research_contract"
_SCHEMA_GATED_FROM = TIER_ORDER.index("T2")


class WiringIncomplete(RuntimeError):
    """A collaborator the contract names is not importable on this branch yet."""

    def __init__(self, missing: Sequence[str]) -> None:
        self.missing = tuple(missing)
        super().__init__(
            "WIRING_INCOMPLETE: missing " + ", ".join(self.missing)
            + " — this is a declared seam, not a runtime failure. Attach the owner's module.")


class ContractViolation(RuntimeError):
    """A collaborator answered outside the frozen contract. Never silently coerced."""


def _value(item: Any) -> Any:
    return getattr(item, "value", item)


def _tier_rank(tier: Any) -> int:
    try:
        return TIER_ORDER.index(str(_value(tier)))
    except ValueError:
        raise ContractViolation(f"tier outside T0..T4: {tier!r}") from None


def _reason_code_from(exc: BaseException) -> str | None:
    """Read a refusal code off the repository's existing convention.

    `research_provider_extensions` already raises `ValueError("<CODE>: detail")`
    and its caller already splits on the colon; reusing that shape keeps one
    refusal channel instead of inventing a second.
    """
    declared = getattr(exc, "reason_code", None)
    if isinstance(declared, str) and declared:
        return declared
    if isinstance(exc, ValueError):
        return str(exc).split(":", 1)[0].strip()
    return None


@dataclass(frozen=True)
class GatewayOutcome:
    """The facade's raw material. `status` follows the existing BLOCKED/UNKNOWN envelope."""

    status: str
    reason_code: str
    capability_id: str
    provider: str | None = None
    transport: str | None = None
    reservation: Any | None = None
    result: Any | None = None
    ignored_agent_claims: tuple[str, ...] = ()
    caveats: tuple[str, ...] = field(default_factory=tuple)


class CapabilityGateway:
    def __init__(self, *, registry: Any, policy: Any, ledger: Any,
                 adapters: Mapping[str, Any], contract: Any | None = None,
                 contract_module_name: str = CONTRACT_MODULE,
                 ledger_request=None, refusal_disposition=None) -> None:
        self.registry = registry
        self.policy = policy
        self.ledger = ledger
        self.adapters = dict(adapters)
        self._contract = contract
        self.contract_module_name = contract_module_name
        self._advertised: dict[str, frozenset[str]] = {}
        # How the contract's `CapabilityRequest` becomes the ledger's own request
        # type. The two are deliberately different (the contract's carries a
        # capability id and a params mapping; the ledger's carries a project, a
        # provider, a reviewed capability name, a transport and a project-scoped
        # intent), and only the composition root knows the project. This seam is
        # required rather than guessed: a gateway that invents the mapping writes
        # reservations under a project nobody chose.
        self.ledger_request = ledger_request
        # The ledger owns the mapping from its own refusal vocabulary onto the
        # contract's closed set, and pins it with a test that walks the codes it
        # raises. Writing a second table here would be a second authority over the
        # same question — the first one to drift wins silently.
        self.refusal_disposition = refusal_disposition

    # -- seams ---------------------------------------------------------------
    def contract_module(self) -> Any:
        if self._contract is None:
            try:
                self._contract = importlib.import_module(self.contract_module_name)
            except ModuleNotFoundError as exc:
                if exc.name == self.contract_module_name:
                    raise WiringIncomplete((self.contract_module_name,)) from None
                raise
        return self._contract

    def _advertises(self, provider: str, adapter: Any, capability_id: str) -> bool:
        known = self._advertised.get(provider)
        if known is None:
            known = frozenset(adapter.capabilities() or ())
            self._advertised[provider] = known
        return capability_id in known

    # -- the one entry point -------------------------------------------------
    def invoke(self, *, capability_id: str, params: Mapping[str, Any], run_id: str,
               operation_id: str, account_ref: str, intent_fingerprint: str,
               grant_ref: str | None = None,
               agent_claims: Mapping[str, Any] | None = None) -> GatewayOutcome:
        contract = self.contract_module()
        ignored = tuple(sorted(str(k) for k in (agent_claims or {})))

        def blocked(code: str, provider: str | None = None,
                    transport: str | None = None,
                    caveats: Sequence[str] = ()) -> GatewayOutcome:
            return GatewayOutcome(status="BLOCKED", reason_code=code,
                                  capability_id=capability_id, provider=provider,
                                  transport=transport, ignored_agent_claims=ignored,
                                  caveats=tuple(caveats))

        entry = self.registry.get(capability_id)
        if entry is None:
            return blocked("CAPABILITY_UNKNOWN")

        if not isinstance(params, Mapping):
            return blocked("CAPABILITY_PARAM_NOT_ALLOWED", entry.provider, entry.transport,
                           ("PARAMS_NOT_A_MAPPING",))
        surplus = sorted({str(k) for k in params} - set(entry.allowed_params))
        if surplus:
            # Key names only: a rejected value may itself be the secret. The
            # names are what makes the refusal actionable: "not allowed" over a
            # set the caller cannot see is a refusal it can only guess at.
            return blocked("CAPABILITY_PARAM_NOT_ALLOWED", entry.provider, entry.transport,
                           ("PARAMS_OUTSIDE_ALLOWED_SUBSET",)
                           + tuple("POLICY_PARAM_NOT_ALLOWED:" + name for name in surplus))

        availability = str(_value(entry.availability))
        if availability == "SCHEMA_DRIFT":
            if _tier_rank(entry.tier) >= _SCHEMA_GATED_FROM:
                return blocked("CAPABILITY_SCHEMA_DRIFT", entry.provider, entry.transport)
        elif availability != "ENABLED":
            return blocked("CAPABILITY_NOT_AVAILABLE", entry.provider, entry.transport,
                           (f"AVAILABILITY_{availability}",))

        adapter = self.adapters.get(entry.provider)
        if adapter is None:
            return blocked("CAPABILITY_NOT_AVAILABLE", entry.provider, entry.transport,
                           ("NO_ADAPTER_FOR_PROVIDER",))
        # Cross-check only: the Registry stays the allowlist authority.
        if not self._advertises(entry.provider, adapter, capability_id):
            return blocked("CAPABILITY_NOT_AVAILABLE", entry.provider, entry.transport,
                           ("ADAPTER_DOES_NOT_ADVERTISE_CAPABILITY",))

        request = contract.CapabilityRequest(
            capability_id=capability_id, params=dict(params), run_id=run_id,
            operation_id=operation_id, account_ref=account_ref,
            intent_fingerprint=intent_fingerprint, grant_ref=grant_ref)

        decision = self.policy.evaluate(request)
        verdict = str(_value(getattr(decision, "verdict", None)))
        if verdict not in {"ALLOW", "DENY", "REQUIRE_HUMAN_GRANT"}:
            raise ContractViolation(f"policy verdict outside closed set: {verdict!r}")
        if verdict != "ALLOW":
            return blocked(self._policy_code(decision), entry.provider, entry.transport,
                           self._policy_detail(decision))

        ledger_caveats: list[str] = []
        try:
            reservation = self.ledger.reserve(self._ledger_request_for(entry, request))
        except WiringIncomplete:
            raise
        except Exception as exc:
            return blocked(self._ledger_code(exc, ledger_caveats),
                           entry.provider, entry.transport,
                           tuple(ledger_caveats))

        ledger_id = _operation_id(reservation, request)
        base = dict(capability_id=capability_id, provider=entry.provider,
                    transport=entry.transport, reservation=reservation,
                    ignored_agent_claims=ignored)

        # The dispatch intent is recorded only for outcomes that are not a
        # proven-local refusal — see `_dispatch_intent` for why the ordering is
        # this way round and what it costs.
        try:
            result = adapter.execute(request)
        except Exception as exc:
            kind, code = self._adapter_failure(exc)
            if kind == "REFUSAL":
                caveats = self._settle_terminal(ledger_id, "BLOCKED", None, None, None, None)
                return GatewayOutcome(status="BLOCKED", reason_code=code,
                                      caveats=tuple(caveats), **base)
            caveats = self._dispatch_intent(ledger_id)
            caveats += self._hold_terminal(ledger_id, self.unknown_reason_for(exc))
            return GatewayOutcome(
                status="UNKNOWN", reason_code="UNKNOWN_CHARGE_OUTSTANDING",
                caveats=tuple(caveats), **base)

        outcome = str(_value(getattr(result, "outcome", None)))
        caveats: list[str] = []
        if outcome != "BLOCKED":
            caveats += self._dispatch_intent(ledger_id)
        if str(_value(getattr(result, "schema_verdict", ""))) in {"DRIFT", "ABSENT"}:
            caveats.append("SCHEMA_DRIFT_OBSERVED_AT_DISPATCH")

        if outcome == "UNKNOWN":
            # The adapter executed and returned no classifiable result. That is an
            # ambiguous response, not evidence of a timeout: the old hard-coded
            # PROVIDER_TIMEOUT invented a cause. The execution state is recorded
            # beside the charge state so the two are not conflated.
            caveats += self._hold_terminal(ledger_id, "AMBIGUOUS_RESPONSE",
                                           execution_outcome="UNKNOWN")
            return GatewayOutcome(status="UNKNOWN", reason_code="UNKNOWN_CHARGE_OUTSTANDING",
                                  result=result, caveats=tuple(caveats), **base)

        if outcome == "BLOCKED":
            try:
                code = self._result_refusal_code(result)
            except ContractViolation:
                # A BLOCKED result never reached the provider: release the
                # reservation before surfacing the adapter bug, so one bad
                # result cannot pin the operation as RESERVED forever.
                self._settle_terminal(ledger_id, "BLOCKED", None, None, None, None)
                raise
            caveats += self._settle_terminal(ledger_id, "BLOCKED", None, None, None, None)
            return GatewayOutcome(status="BLOCKED", reason_code=code, result=result,
                                  caveats=tuple(caveats), **base)

        caveats += self._settle_terminal(
            ledger_id, outcome,
            getattr(result, "actual_usd", None),
            _value(getattr(result, "actual_source", None)),
            getattr(result, "quota_units_used", None),
            getattr(result, "upstream_ref", None),
            estimated_micros=_estimated_micros(reservation), payload=getattr(result, "payload", None))
        status = "OK" if outcome == "OK" else "FAILED"
        return GatewayOutcome(status=status, reason_code="", result=result,
                              caveats=tuple(caveats), **base)

    def _ledger_request_for(self, entry: Any, request: Any) -> Any:
        """The ledger's own request object for this call, or a named refusal.

        Absent the seam this raises rather than handing the ledger the contract's
        request: that produced `AttributeError: 'CapabilityRequest' object has no
        attribute 'project'`, which the caller read as "the ledger refused" when
        the truth was "nothing ever translated between the two shapes".
        """
        if self.ledger_request is None:
            # Passthrough: a ledger that takes the contract's request needs no
            # translation, and refusing here would break every caller that has
            # such a ledger. A ledger that does *not* take it fails with a named
            # violation at the seam (see `_ledger_code`) rather than with an
            # AttributeError read as "the ledger refused".
            return request
        return self.ledger_request(entry, request)

    # -- refusals, and the refusal to invent one -----------------------------
    @staticmethod
    def _policy_detail(decision: Any) -> tuple[str, ...]:
        """Name what the refusal was about, when the policy recorded it.

        The reason code stays inside the closed set; these strings only say
        which parameter or host was refused, so the caller need not guess.
        """
        constraints = getattr(decision, "constraints", None) or {}
        details = ["POLICY_PARAM_NOT_ALLOWED:" + str(name)
                   for name in (constraints.get("disallowed_params") or ())]
        host = constraints.get("refused_host")
        if host:
            details.append("POLICY_DOMAIN_BLOCKED_HOST:" + str(host))
        return tuple(details)

    @staticmethod
    def _policy_code(decision: Any) -> str:
        code = getattr(decision, "reason_code", None)
        if not isinstance(code, str) or code not in REASON_CODES:
            # A prose refusal is not translated into a plausible code: doing so
            # would make the closed set unauditable.
            raise ContractViolation(f"policy reason_code outside the closed set: {code!r}")
        return code

    def _ledger_code(self, exc: BaseException, result_caveats: list) -> str:
        if isinstance(exc, (AttributeError, TypeError)):
            # Not a refusal: the ledger could not read the request it was handed.
            # That is the two shapes failing to meet, and saying so is the whole
            # difference between "this budget was refused" and "nothing ever
            # translated between the contract's request and the ledger's".
            raise ContractViolation(
                "the ledger could not read the request handed to it "
                f"({type(exc).__name__}: {exc}); supply ledger_request so the "
                "contract's request is translated into the ledger's own shape"
            ) from None
        code = _reason_code_from(exc)
        if code in REASON_CODES:
            return code
        if self.refusal_disposition is not None:
            disposition, reason = self.refusal_disposition(code)
            if disposition == "BLOCKED" and reason in REASON_CODES:
                if reason != code:
                    # §5.3 has no exact member for this refusal, so the nearest one
                    # is reported while the ledger's precise code is kept beside it:
                    # the reported vocabulary is coarser than the fact.
                    result_caveats.append("LEDGER_CODE:" + str(code))
                return reason
            raise ContractViolation(
                f"the ledger refused with {code!r}, which its own disposition marks "
                "as a contract violation rather than a governed outcome")
        raise ContractViolation(
            f"ledger refused with a code outside the closed set: {code!r}")

    @staticmethod
    def _adapter_failure(exc: BaseException) -> tuple[str, str | None]:
        """Split a provable local refusal from an unclassifiable transport failure.

        `ValueError` is this repository's declared refusal channel, so a malformed
        one is a bug worth raising. Anything else — a dropped socket, a kill — is
        exactly the case where a charge cannot be proven either way, so budget is
        held rather than refunded.
        """
        import research_contract
        if research_contract.never_sent(exc):
            return "REFUSAL", "EGRESS_REFUSED"
        code = _reason_code_from(exc)
        if isinstance(exc, ValueError):
            if code not in REASON_CODES:
                raise ContractViolation(
                    f"adapter refusal code outside the closed set: {code!r}") from None
            return "REFUSAL", code
        return "UNCLASSIFIED", None

    @staticmethod
    def _result_refusal_code(result: Any) -> str:
        """`AdapterResult` carries no reason_code field — see CONTRACT-CHANGE-REQUEST.

        Until the contract gains one, the only closed-set channel is `caveats`,
        and a BLOCKED result without one is a contract violation rather than a
        guessed code.
        """
        for caveat in getattr(result, "caveats", ()) or ():
            if caveat in REASON_CODES:
                return caveat
        raise ContractViolation(
            "adapter returned BLOCKED without a closed-set reason in caveats")

    # -- ledger writes ------------------------------------------------------
    #
    # These translate a contract `AdapterResult` into the ledger's own verbs.
    # They previously called `settle(ledger_id, result)` and `mark_unknown(id)`,
    # which are the *retired* ledger's signatures: the ledger that replaced it
    # takes one positional and keyword-only required arguments, so every terminal
    # write raised TypeError and was swallowed into a caveat. The rows stayed
    # RESERVED forever and the gateway looked like it was merely holding budget.
    # A swallowed failure that leaves a row in a plausible state is the worst kind
    # of quiet, so the translation is explicit here and its absence is not.

    def _dispatch_intent(self, operation_id: str) -> list[str]:
        """Record that a call may have happened — for outcomes that were not a
        proven-local refusal.

        The ledger needs this *before* the outbound call to make a crash
        conservative, and it refuses ``prove_no_charge`` once an intent exists.
        Those two requirements cannot both be met from here: only the adapter knows
        whether it dispatched, and ``AdapterResult`` has no field that says so. So
        the binding one wins — the contract's IL-4 requires a pre-dispatch local
        refusal to be settleable as ``NO_CHARGE_PROVEN``, and recording an intent
        first makes that unreachable, permanently holding budget for calls that
        never left.

        **The cost, stated rather than hidden:** a crash *during* ``execute`` now
        leaves the row ``RESERVED`` instead of ``UNKNOWN``, so reconciliation sees
        a live reservation rather than a possibly-charged operation. Closing that
        needs ``AdapterResult.dispatched`` (a CONTRACT-CHANGE-REQUEST in the status
        file), because the fact lives in the adapter and the gateway must not infer
        it.
        """
        try:
            self.ledger.mark_dispatch_intent(operation_id)
        except Exception:
            return ["LEDGER_DISPATCH_INTENT_FAILED"]
        return []

    def _hold_terminal(self, operation_id: str, reason: str,
                       execution_outcome: str = "") -> list[str]:
        """A dispatched call whose charge cannot be established. The hold stays.

        ``execution_outcome`` is passed only to a ledger that declares the field.
        A ledger without it still holds the charge; it simply cannot record what
        the adapter observed, and this gateway does not pretend otherwise.
        """
        try:
            try:
                signature = inspect.signature(self.ledger.mark_unknown)
                accepts = ("execution_outcome" in signature.parameters or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in signature.parameters.values()))
            except (TypeError, ValueError):
                accepts = False
            if accepts:
                self.ledger.mark_unknown(operation_id, reason=reason,
                                         execution_outcome=execution_outcome)
            else:
                self.ledger.mark_unknown(operation_id, reason=reason)
        except Exception:
            return ["LEDGER_MARK_UNKNOWN_FAILED"]
        return []

    def _settle_terminal(self, operation_id: str, outcome: str, actual_usd,
                         actual_source, quota_units, upstream_ref,
                         estimated_micros: int = 0, payload=None) -> list[str]:
        """Book the terminal state, or say which ledger verb refused and why.

        `BLOCKED` here means a refusal made *before* dispatch — the adapter
        returned it, so no request left. That is the one case where the ledger can
        be told no charge was incurred, and it is the only case that reaches
        `prove_no_charge`.
        """
        try:
            if outcome == "BLOCKED":
                self.ledger.prove_no_charge(operation_id, reason="PRE_DISPATCH_REFUSAL")
                return []
            if actual_usd is None and hasattr(self.ledger, "get"):
                import research_spending
                row = self.ledger.get(operation_id)
                if row is not None and getattr(row, "price_version", "") == "money-envelope-1" and row.provider == "typesafe_jev":
                    derived = research_spending.derived_cost(row, payload)
                    if derived is None:
                        return self._hold_terminal(operation_id, "CHARGE_UNVERIFIED_AFTER_SUCCESS" if outcome == "OK"
                                                   else "PROVIDER_ERROR_UNVERIFIED", outcome)
                    actual_usd, actual_source = derived, "DERIVED"
            micros, source = _actuals(actual_usd, actual_source, estimated_micros,
                                     priced_at_zero=_priced_at_zero(operation_id, self.ledger))
            if micros is None:
                # The call happened and nobody can say what it cost. Holding is
                # the conservative answer; inventing a zero would be a refund.
                # A successful call and a failed call are different evidence, so
                # they get different charge-uncertainty reasons instead of the one
                # PROVIDER_ERROR_UNVERIFIED that used to mean both.
                reason = ("CHARGE_UNVERIFIED_AFTER_SUCCESS" if outcome == "OK"
                          else "PROVIDER_ERROR_UNVERIFIED")
                caveats = self._hold_terminal(operation_id, reason,
                                              execution_outcome=outcome)
                return ["LEDGER_HELD_ACTUAL_UNKNOWN", *caveats]
            self.ledger.settle(
                operation_id, actual_micros=micros, actual_source=source,
                upstream_ref=upstream_ref or "",
                quota_units=int(quota_units) if quota_units is not None else None)
        except Exception as exc:
            return ["LEDGER_SETTLE_REFUSED:" + (getattr(exc, "code", None)
                                                or type(exc).__name__)]
        return []

    @staticmethod
    def unknown_reason_for(exc: BaseException) -> str:
        """One of the ledger's own UNKNOWN reasons for an unclassifiable failure."""
        return "PROVIDER_TIMEOUT" if _looks_like_timeout(exc) else "AMBIGUOUS_RESPONSE"


def _looks_like_timeout(exc: BaseException) -> bool:
    name = type(exc).__name__.casefold()
    return "timeout" in name or "timed out" in str(exc).casefold()


def _operation_id(reservation: Any, request: Any) -> str:
    """The ledger's own operation id for this reservation.

    A ledger that returns the row is the authority; one that returns only an id
    is believed about the id. A reservation carrying *neither* is a contract
    violation rather than something to paper over with the request's id, because
    writing the terminal state to the wrong row is worse than refusing.
    """
    for attribute in ("operation_id", "ledger_id"):
        value = getattr(reservation, attribute, None)
        if isinstance(value, str) and value:
            return value
    raise ContractViolation("ledger.reserve returned a reservation with no operation id")


def _priced_at_zero(operation_id: str, ledger: Any) -> bool:
    """Only an evidenced zero rate or an explicit free tier can derive USD zero."""
    try:
        row = ledger.get(operation_id)
    except Exception:
        return False
    if row is None or getattr(row, "estimated_micros", None) != 0:
        return False
    if getattr(row, "price_verified", False) and getattr(row, "price_source", ""):
        return True
    pricing = getattr(ledger, "_pricing", None)
    price = pricing.price_for(row.provider, row.capability) if pricing is not None else None
    return bool(price is not None and price.free_tier and price.unit_micros == 0
                and price.source == row.price_source and price.version == row.price_version)


def _estimated_micros(reservation: Any) -> int:
    for attribute in ("reserved_micros", "estimated_micros"):
        value = getattr(reservation, attribute, None)
        if isinstance(value, int):
            return value
    return 0


def _actuals(actual_usd, actual_source, estimated_micros: int,
            priced_at_zero: bool = False):
    """`(micros, source)` for the ledger, or `(None, None)` when nothing is known.

    A provider-reported amount is booked as reported. Otherwise the recorded rate
    is what we know, and the ledger's `DERIVED` label is exactly the statement
    "derived from a known rate" rather than "the vendor told us" — so it is used
    with the reserve-time estimate, and never dressed up as provider-reported.
    """
    if actual_usd is not None:
        try:
            micros = int((actual_usd * 1_000_000).to_integral_value())
        except Exception:
            micros = None
        if micros is not None:
            reported = "REPORTED" in str(actual_source).upper()
            return micros, ("PROVIDER_REPORTED" if reported else "DERIVED")
    if estimated_micros:
        return estimated_micros, "DERIVED"
    if priced_at_zero:
        # This flag is evidence-backed. The default credits-only catalog's
        # placeholder zero never reaches this branch.
        return 0, "DERIVED"
    # Nothing attributable at all: the caller decides between holding and settling
    # by getting None back, and `_settle_terminal` holds.
    return None, None

#!/usr/bin/env python3
"""The policy plane: tier, grant, domain and network — in that order (§3, §5.1, §5.4).

The gateway asks one question of this module: *may this capability run now?* The
answer is a :class:`research_contract.PolicyDecision` whose verdict is one of the
three the gateway knows, and whose refusal always carries a §5.3 code.

## Where each decision actually comes from

* **The tier ladder is not re-implemented here.** ``research_tier_authorization``
  already turns "which tier is this and what does it need" into a verdict, and it
  documents what a grant is worth on a same-identity host. This module calls it
  and translates; writing a second ladder is how two answers to one question
  begin to disagree.
* **Which tier a capability is** comes from the registry entry, not from anything
  the caller says (§4 IR-3). A parameter may *raise* the tier — a stored login
  profile, a webhook — and the highest dimension wins (§3.2).
* **Domain policy** is enforced on the URLs the call would touch, against the
  grant's allowlist or the process allowlist. No configured allowlist means no
  remote page may be fetched: "unset" is not "any".
* **Money is not decided here.** ``max_usd`` travels in ``constraints`` and the
  ledger enforces it at reserve time. Duplicating a budget check in two planes is
  the second policy source this architecture exists to remove.

## §5.4, unchanged and not softened

A process that shares an OS identity, environment or keyring with the Agent can
mint any local grant it likes. ``factor`` records which channel produced a grant,
and the ladder accepts only the two factors it names as non-environmental. In this
deployment neither is configured, so T3 is denied — not because the code is
incomplete, but because the boundary does not exist to rely on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Iterable, Mapping
from urllib.parse import urlsplit

from research_capability_registry import CapabilityEntry, CapabilityRegistry
from research_contract import (Availability, EffectClass, PolicyDecision, PolicyVerdict,
                               Tier, tier_rank)

__all__ = ["PolicyGrant", "Policy", "PolicyError", "effective_tier", "host_allowed",
           "Authorization", "authorize"]

#: The two factors that are not settable by this process (§5.4). Anything else —
#: including a same-user CLI attestation — is a statement of intent, not a boundary.
NON_ENVIRONMENT_FACTORS = frozenset({"OS_ISOLATED_BROKER_GRANT",
                                     "SEPARATELY_AUTHENTICATED_GRANT"})


class PolicyError(ValueError):
    """A grant or policy configuration that cannot mean what it says."""


@dataclass(frozen=True)
class PolicyGrant:
    """One grant (§5.1), as presented to this process. Never read from it."""

    grant_ref: str
    project: str
    max_tier: Tier | str
    providers: tuple[str, ...]
    capability_ids: tuple[str, ...]
    allowed_domains: tuple[str, ...]
    max_usd: Decimal | int
    period: str
    issued_at: datetime
    expires_at: datetime
    issued_by: str
    signature: str
    #: Which channel produced this grant. The ladder refuses factors that are
    #: settable by this process; see §5.4 in the module docstring.
    factor: str = ""
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("grant_ref", "project", "period", "issued_by"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise PolicyError(f"PolicyGrant.{name} must be a non-empty string")
        tier_rank(self.max_tier)
        object.__setattr__(self, "max_tier", Tier(str(getattr(self.max_tier, "value",
                                                             self.max_tier))))
        for name in ("providers", "capability_ids", "allowed_domains"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or not all(
                    isinstance(item, str) and item for item in value):
                raise PolicyError(f"PolicyGrant.{name} must be a tuple of names")
        if isinstance(self.max_usd, bool) or isinstance(self.max_usd, float):
            raise PolicyError("PolicyGrant.max_usd must be Decimal or int, never float")
        if Decimal(self.max_usd) < 0:
            raise PolicyError("PolicyGrant.max_usd must be nonnegative")
        for name in ("issued_at", "expires_at", "revoked_at"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None):
                raise PolicyError(f"PolicyGrant.{name} must be a timezone-aware datetime")


@dataclass(frozen=True)
class Authorization:
    """The ladder's answer, in the shape this module consumes."""

    tier: Tier
    authorized: bool
    enforceable: bool
    factor: str
    reason_code: str


def _delegate():
    """The product's tier ladder, when this branch carries it.

    ``research_tier_authorization`` is the implementation the integrated product
    ships, and it is authoritative there. This module prefers it whenever it is
    importable, so the product has one implementation of the ladder; the local
    rules below run only on a branch that does not carry it yet, and a test that
    runs wherever the ladder *is* present pins the two to the same answers.
    """
    try:
        import research_tier_authorization as module
    except ModuleNotFoundError:
        return None
    return module


def authorize(tier: Tier, grant: PolicyGrant | None, now: datetime) -> Authorization:
    """May this tier dispatch? Delegated when the product's ladder is present."""
    ladder = _delegate()
    if ladder is not None:
        tier_grant = None if grant is None else ladder.TierGrant(
            factor=grant.factor, grant_ref=grant.grant_ref, issued_by=grant.issued_by,
            tier=str(tier), signature=grant.signature, issued_at=grant.issued_at,
            expires_at=grant.expires_at, revoked_at=grant.revoked_at)
        verdict = ladder.tier_authorization(str(tier), grant=tier_grant, now=now)
        return Authorization(tier=tier, authorized=verdict.authorized,
                             enforceable=verdict.enforceable, factor=verdict.factor,
                             reason_code=verdict.reason_code or "")
    return _authorize_locally(tier, grant, now)


def _authorize_locally(tier: Tier, grant: PolicyGrant | None, now: datetime) -> Authorization:
    """The same rules, for a branch without the ladder module.

    Read this next to ``research_tier_authorization.tier_authorization``: T0/T1
    need no external factor; T2 rests on the operator's process policy (and is
    therefore *not* enforceable against a same-identity Agent); T4 is refused
    outright; T3 needs a grant whose factor this process cannot set itself, that
    names an issuer and a signature, is not revoked, has an issued-at that is not
    in the future and an expiry that is still ahead.
    """
    if tier is Tier.T0 or tier is Tier.T1:
        return Authorization(tier, True, True, "LOCAL_NO_EXTERNAL_EFFECT", "")
    if tier is Tier.T2:
        return Authorization(tier, True, False,
                             "OPERATOR_POLICY_SWITCH_AND_ACCOUNT_CREDENTIAL", "")
    if tier is Tier.T4:
        return Authorization(tier, False, False, "REQUIRES_SEPARATE_SCOPED_AUTHORIZATION",
                             "POLICY_TIER_NOT_GRANTED")
    if grant is None:
        return Authorization(tier, False, False, "NONE", "POLICY_TIER_NOT_GRANTED")
    if grant.max_tier is not tier:
        return Authorization(tier, False, False, grant.factor, "POLICY_TIER_NOT_GRANTED")
    if grant.factor not in NON_ENVIRONMENT_FACTORS:
        return Authorization(tier, False, False, grant.factor or "NONE",
                             "POLICY_TIER_NOT_GRANTED")
    if not grant.grant_ref.strip() or not grant.issued_by.strip() or not grant.signature.strip():
        return Authorization(tier, False, False, grant.factor, "POLICY_TIER_NOT_GRANTED")
    if grant.revoked_at is not None:
        return Authorization(tier, False, False, grant.factor, "POLICY_GRANT_REVOKED")
    if grant.issued_at > now:
        return Authorization(tier, False, False, grant.factor, "POLICY_TIER_NOT_GRANTED")
    if grant.expires_at <= now:
        return Authorization(tier, False, False, grant.factor, "POLICY_GRANT_EXPIRED")
    return Authorization(tier, True, True, grant.factor, "")


def effective_tier(entry: CapabilityEntry, params: Mapping[str, object]) -> Tier:
    """The tier of *this call*: the entry's tier, raised by what the parameters do.

    A parameter counts as raised when it is present and not switched off — an
    explicit ``use_profile: false`` does not make a call T4, and a blank string is
    not a site name. Anything ambiguous is left at the higher tier, because the
    cost of over-gating is a refusal and the cost of under-gating is a paid
    action nobody authorised.
    """
    tier = entry.tier
    for name, raised in entry.escalates_to.items():
        if name not in params:
            continue
        value = params[name]
        if value is None or value is False or value == "" or value == () or value == [] or value == {}:
            continue
        if tier_rank(raised) > tier_rank(tier):
            tier = Tier(str(getattr(raised, "value", raised)))
    if entry.effect_class is EffectClass.STATE_CHANGING and tier_rank(tier) < tier_rank(Tier.T4):
        tier = Tier.T4
    return tier


def host_allowed(host: str, domains: Iterable[str]) -> bool:
    """True when ``host`` is one of ``domains`` or a subdomain of one.

    Exact-or-subdomain, never a substring test: ``evil-example.com`` must not pass
    an allowlist that contains ``example.com`` because the string happens to
    contain it.
    """
    candidate = (host or "").strip().casefold().rstrip(".")
    if not candidate:
        return False
    for domain in domains:
        allowed = str(domain).strip().casefold().rstrip(".")
        if not allowed:
            continue
        if candidate == allowed or candidate.endswith("." + allowed):
            return True
    return False


def _urls_in(params: Mapping[str, object]) -> tuple[str, ...]:
    """The URLs a call would fetch, from the parameters the registry exposes."""
    found: list[str] = []
    for name in ("url", "urls"):
        value = params.get(name)
        if isinstance(value, str) and value:
            found.append(value)
        elif isinstance(value, (list, tuple)):
            found.extend(item for item in value if isinstance(item, str) and item)
    return tuple(found)


def _host(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return parsed.hostname


class Policy:
    """Evaluates one :class:`CapabilityRequest` against the registry and the grants."""

    def __init__(self, *, registry: CapabilityRegistry,
                 grants: Mapping[str, PolicyGrant] | Callable[[str], PolicyGrant | None] | None = None,
                 allowed_domains: Iterable[str] = (), network_enabled: bool = False,
                 clock: Callable[[], datetime] | None = None,
                 domain_scope: Callable[[tuple[str, ...]], Iterable[str]] | None = None):
        self._registry = registry
        self._grants = grants
        self._allowed_domains = tuple(allowed_domains)
        # Optional: the runtime's consent-aware scope for the URLs of one call.
        self._domain_scope = domain_scope
        self._network_enabled = bool(network_enabled)
        self._clock = clock

    def _now(self) -> datetime:
        moment = self._clock() if self._clock is not None else datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    def _grant_for(self, grant_ref: str | None) -> PolicyGrant | None:
        if not grant_ref:
            return None
        if self._grants is None:
            return None
        if callable(self._grants):
            return self._grants(grant_ref)
        return self._grants.get(grant_ref)

    def evaluate(self, request) -> PolicyDecision:
        entry = self._registry.get(request.capability_id)
        if entry is None:
            # Not reachable through the gateway (it resolves first), but this
            # module is also called directly; an unknown id is refused here too
            # rather than becoming an AttributeError somewhere downstream.
            return PolicyDecision(PolicyVerdict.DENY, "CAPABILITY_UNKNOWN", Tier.T0)

        tier = effective_tier(entry, request.params)
        surplus = sorted({str(name) for name in request.params} - set(entry.allowed_params))
        if surplus:
            # The gateway checks this too, against this same table; applying the
            # same rule twice cannot disagree, and it keeps this module safe when
            # something other than the gateway calls it.
            # The names travel with the refusal: "a parameter is not allowed"
            # without saying which one leaves the caller guessing at a closed
            # set it cannot see.
            return PolicyDecision(PolicyVerdict.DENY, "CAPABILITY_PARAM_NOT_ALLOWED", tier,
                                  constraints={"disallowed_params": tuple(surplus)})
        availability = entry.availability
        if availability is Availability.SCHEMA_DRIFT:
            if tier_rank(tier) >= tier_rank(Tier.T2):
                return PolicyDecision(PolicyVerdict.DENY, "CAPABILITY_SCHEMA_DRIFT", tier)
        elif availability is not Availability.ENABLED:
            return PolicyDecision(PolicyVerdict.DENY, "CAPABILITY_NOT_AVAILABLE", tier)

        if entry.transport != "local_sdk" and not self._network_enabled:
            return PolicyDecision(PolicyVerdict.DENY, "RESEARCH_NETWORK_DISABLED", tier)

        grant = self._grant_for(request.grant_ref)
        if request.grant_ref and grant is None:
            # A grant reference this process cannot resolve is not a grant; saying
            # "tier not granted" is the honest closed-set answer.
            return PolicyDecision(PolicyVerdict.DENY, "POLICY_TIER_NOT_GRANTED", tier,
                                  request.grant_ref)

        if grant is not None:
            scope_problem = self._scope_problem(grant, entry, tier)
            if scope_problem is not None:
                return PolicyDecision(PolicyVerdict.DENY, scope_problem, tier, grant.grant_ref)

        authorization = authorize(tier, grant, self._now())
        if not authorization.authorized:
            code = authorization.reason_code or "POLICY_TIER_NOT_GRANTED"
            return PolicyDecision(PolicyVerdict.DENY, code, tier, grant.grant_ref
                                  if grant else None)

        domain_problem, refused_host = self._domain_problem(request.params, grant)
        if domain_problem is not None:
            return PolicyDecision(
                PolicyVerdict.DENY, domain_problem, tier,
                grant.grant_ref if grant else None,
                {"refused_host": refused_host} if refused_host else {})

        constraints = {
            "allowed_domains": tuple(grant.allowed_domains) if grant
            else tuple(self._allowed_domains),
            "max_usd": str(Decimal(grant.max_usd)) if grant else None,
            "period": grant.period if grant else None,
            # The ladder's honesty travels with the decision: a caller that records
            # this allowance must not read it as an enforced boundary.
            "tier_enforceable": authorization.enforceable,
            "tier_factor": authorization.factor,
        }
        return PolicyDecision(PolicyVerdict.ALLOW, "", tier,
                              grant.grant_ref if grant else None, constraints)

    @staticmethod
    def _scope_problem(grant: PolicyGrant, entry: CapabilityEntry, tier: Tier) -> str | None:
        if tier_rank(tier) > tier_rank(grant.max_tier):
            return "POLICY_TIER_NOT_GRANTED"
        if grant.providers and entry.provider not in grant.providers:
            return "POLICY_TIER_NOT_GRANTED"
        if grant.capability_ids and entry.capability_id not in grant.capability_ids:
            return "POLICY_TIER_NOT_GRANTED"
        return None

    def _domain_problem(self, params: Mapping[str, object],
                        grant: PolicyGrant | None) -> tuple[str | None, str]:
        """Every URL the call would fetch must be inside the applicable allowlist.

        Returns the reason code and the host that failed it, so a refusal can
        name the site instead of only saying that something was outside.
        """
        urls = _urls_in(params)
        if not urls:
            return None, ""
        allowed = (grant.allowed_domains if grant is not None and grant.allowed_domains
                   else self._allowed_domains)
        if self._domain_scope is not None and not (grant is not None and grant.allowed_domains):
            allowed = tuple(self._domain_scope(tuple(str(url) for url in urls)))
        if not allowed:
            return "POLICY_DOMAIN_NOT_ALLOWED", _host(str(urls[0]))
        for url in urls:
            host = _host(url)
            if not host or not host_allowed(host, allowed):
                return "POLICY_DOMAIN_NOT_ALLOWED", host
        return None, ""

#!/usr/bin/env python3
"""One paid operation, one key — regardless of which transport reached it.

Two vendor paths already fetch the same thing. Serper is reached over REST;
SerpApi is reachable *both* over its legacy REST adapter and over the embedded
official MCP. Each path can reserve, bill and receipt independently, which is
how one research objective turns into two charges for one answer. The
convergence review names the fix as Key B: a provider execution fingerprint
over ``project + run + provider + account + capability + canonical arguments``
that is **independent of transport**.

This module is that key, and only that key. It deliberately holds no ledger,
no policy and no network code — the ledger that consumes it is the Lite
``search_attempts`` table (REST) and the integrator's provider reservation
table (MCP), and giving both the same function is what makes the collapse real
rather than aspirational.

**What is in the key, and why each part is there.**

- ``provider`` — two vendors are two spend opportunities, never one.
- ``account`` — the same query on two accounts is billable twice; an empty
  account means "the operator declared no account", and it is a value, not a
  wildcard.
- ``mode`` / ``query`` / ``country`` / ``language`` / ``limit`` / ``page`` —
  the request as the provider will receive it. ``country`` and ``language``
  are separate axes on purpose: the UK index in English is not the Irish index
  in Irish, and those are two queries that cost two credits.
- ``engine`` — SerpApi's engine choice changes which upstream SERP is bought.

**What is deliberately excluded.** ``transport`` is carried in the arguments
for the receipt and the audit trail, and excluded from the digest. That
exclusion *is* the feature: it is what makes a REST call and the same logical
MCP call one reservation. ``receipt_ref``, timestamps, run totals and any
other bookkeeping are rejected outright rather than ignored — an open key set
would let a later field silently fork the key and restore the double charge
this module exists to remove.

**Query normalisation is whitespace-only.** Runs of whitespace collapse and
the ends are trimmed, so a padded copy of one query is not a second paid call.
Case is *not* folded: two queries that differ only in case are two different
strings upstream, and collapsing them here would refuse a query a researcher
deliberately wrote.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

# The surfaces this package currently reaches. A provider missing from this
# set is not "unsupported forever" — it is "no canonical key has been agreed
# with whoever owns that adapter", which is a different sentence.
PROVIDERS = frozenset(("serper", "serpapi"))

# Called "transport" rather than "vendor" because a vendor can have several.
# Only the first is wired today; the second is why the key ignores it.
TRANSPORTS = frozenset(("REST_HTTPS", "EMBEDDED_OFFICIAL_MCP_HTTP"))

# Closed key set. Order is the insertion order of ``canonical_arguments`` and
# is not part of the digest (the digest sorts).
ARGUMENT_FIELDS = ("provider", "mode", "query", "country", "language",
                   "limit", "page", "engine", "account", "transport")

# Every field except ``transport``. This is the tuple that gets hashed, and
# naming it once means the exclusion cannot drift away from its comment.
_KEYED_FIELDS = tuple(f for f in ARGUMENT_FIELDS if f != "transport")

# The provider-neutral subset: what the researcher asked for, with no vendor
# and no engine attached. Used to *notice* that two vendors are about to be
# asked the same question, not to stop them.
_INTENT_FIELDS = ("mode", "query", "country", "language", "limit", "page")

_WHITESPACE = re.compile(r"\s+")


def _normalise_query(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("CANONICAL_QUERY_INVALID")
    collapsed = _WHITESPACE.sub(" ", value).strip()
    if not collapsed:
        raise ValueError("CANONICAL_QUERY_INVALID")
    return collapsed


def canonical_arguments(*, provider: str, mode: str, query: str, country: str,
                        language: str, limit: int, page: int = 1,
                        engine: str | None = None, account: str = "",
                        transport: str = "REST_HTTPS") -> dict:
    """Build the closed argument set both ledgers key on.

    Raises ``ValueError`` on anything this module cannot describe honestly. It
    does *not* re-implement vendor argument policy: range checks such as
    ``limit <= 20`` belong to the adapter that knows the vendor's rule.
    """
    if provider not in PROVIDERS:
        raise ValueError("CANONICAL_PROVIDER_UNSUPPORTED")
    if transport not in TRANSPORTS:
        raise ValueError("CANONICAL_TRANSPORT_UNSUPPORTED")
    return {
        "provider": provider,
        "mode": mode,
        "query": _normalise_query(query),
        "country": country,
        "language": language,
        "limit": limit,
        "page": page,
        "engine": engine,
        "account": account,
        "transport": transport,
    }


def _checked(arguments: dict) -> dict:
    if type(arguments) is not dict:
        raise ValueError("CANONICAL_ARGUMENTS_INCOMPLETE")
    missing = [name for name in ARGUMENT_FIELDS if name not in arguments]
    if missing:
        raise ValueError("CANONICAL_ARGUMENTS_INCOMPLETE")
    unknown = [name for name in arguments if name not in ARGUMENT_FIELDS]
    if unknown:
        raise ValueError("CANONICAL_ARGUMENTS_UNKNOWN_FIELD")
    if arguments["provider"] not in PROVIDERS:
        raise ValueError("CANONICAL_PROVIDER_UNSUPPORTED")
    if arguments["transport"] not in TRANSPORTS:
        raise ValueError("CANONICAL_TRANSPORT_UNSUPPORTED")
    arguments = {**arguments, "query": _normalise_query(arguments["query"])}
    return arguments


def _digest(fields, arguments: dict) -> str:
    payload = {name: arguments[name] for name in fields}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def operation_signature(arguments: dict) -> str:
    """Key B: the same key for REST and for MCP on the same logical request."""
    return _digest(_KEYED_FIELDS, _checked(arguments))


def intent_signature(arguments: dict) -> str:
    """Key A: the provider-neutral intent, used to *report* overlap.

    A caller that sees two providers share an intent key has found something
    worth a human decision — not something to auto-block, and not something to
    silently downgrade to one vendor.
    """
    return _digest(_INTENT_FIELDS, _checked(arguments))


def same_operation(left: dict, right: dict) -> bool:
    """True when these are one spend opportunity, not two."""
    return operation_signature(left) == operation_signature(right)


def same_intent(left: dict, right: dict) -> bool:
    """True when two vendors would be asked the same question."""
    return intent_signature(left) == intent_signature(right)


_SCALAR = (str, bool, int, float, type(None))


def resource_operation_key(*, provider: str, capability_id: str, params: dict) -> str:
    """Key for an operation that is not a search request.

    A resource read has no query, no locale and no page. Forcing it through
    :func:`canonical_arguments` would mean inventing values for fields the
    operation does not have, and two different reads would then collide on the
    invented values. So it gets its own normalisation with the same two
    properties: derived only from what the operation is, and independent of the
    transport that carries it.

    Bounded on purpose: a key computed over a nested structure would be a way to
    make the identity of an operation depend on something nobody reviewed.
    """
    if provider not in PROVIDERS:
        raise ValueError("CANONICAL_PROVIDER_UNSUPPORTED")
    if not isinstance(capability_id, str) or not capability_id:
        raise ValueError("CANONICAL_ARGUMENTS_INCOMPLETE")
    if type(params) is not dict:
        raise ValueError("CANONICAL_ARGUMENTS_INCOMPLETE")
    normalised: dict[str, str] = {}
    for name in sorted(params):
        value = params[name]
        if not isinstance(value, _SCALAR) or isinstance(value, bytes):
            raise ValueError("CANONICAL_ARGUMENTS_UNKNOWN_FIELD")
        normalised[str(name)] = "" if value is None else str(value)
    return _digest(("provider", "capability_id", "params"), {
        "provider": provider, "capability_id": capability_id, "params": normalised,
    })


# ---------------------------------------------------------------------------
# CD-03: the ledger-facing search identity
# ---------------------------------------------------------------------------
#
# Two keys already exist here and they answer two different questions:
#
#   ``operation_signature``  "is this the same provider operation" — derived from
#                            the arguments we actually send on the wire.
#   ``route_shape`` (B's)    "has this project already bought this" — derived from
#                            provider + account + capability + canonical params,
#                            with no transport and no run.
#
# The coordinator's CD-03 found that the second one was unreachable across
# transports, and the reason was not the ledger: each adapter handed it a private
# shape. The REST path sent ``{"query": "serpapi|search|pool|gb"}``; the MCP path
# sent ``{"action": "serpapi_mcp_search", "signature": "sig-0"}``. No digest over
# those two can ever agree.
#
# So this layer exists: one function, both adapters, one shape.
#
#   {"capability": "search", "query": <normalised>, "params": {<stable axes>}}
#
# The rules it enforces are the decision's, not this module's invention:
# * the transport and the run are not in it — how it travels is not what it buys;
# * adapter-private fields (``action``, ``signature``, ``tool``) are **rejected**,
#   because silently accepting them is how the double charge came back;
# * defaults are materialised, so "omitted" and "explicitly default" are one
#   operation rather than two;
# * normalisation covers whitespace, case and Unicode form, because two spellings
#   of one query are one purchase.
#
# **The wire query is not case-folded.** ``canonical_arguments`` normalises
# whitespace only, because that string is what the vendor receives and the user's
# capitalisation is theirs. The identity is case-insensitive; the request is not.
# That asymmetry is deliberate: it refuses a duplicate, not a spelling.

SEARCH_IDENTITY_SCHEMA = "marketops.search.identity.v1"

#: Fields that describe *how* an operation is carried, or which adapter ran it.
#: They may appear on a receipt; they may never appear in an identity.
IDENTITY_FORBIDDEN_FIELDS = frozenset({
    "action", "signature", "transport", "run_id", "operation_id", "adapter",
    "tool", "tool_name", "endpoint", "url", "uri", "host", "api_key", "key",
    "token", "grant", "budget", "ledger_id", "capability_id",
})

_SCALAR_IDENTITY = (str, bool, int, float, type(None))
_COUNTRY_2 = re.compile(r"^[A-Za-z]{2}$")
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}$")
_IDENTITY_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")


def normalise_search_query(value: str) -> str:
    """One query, one spelling: NFC, whitespace collapsed, case folded.

    ``casefold`` rather than ``lower`` because it is the Unicode operation for
    "these are the same text"; ``lower`` leaves pairs that compare equal upstream
    looking different here.
    """
    if not isinstance(value, str):
        raise ValueError("IDENTITY_QUERY_INVALID")
    collapsed = _WHITESPACE.sub(" ", unicodedata.normalize("NFC", value)).strip()
    if not collapsed:
        raise ValueError("IDENTITY_QUERY_INVALID")
    if len(collapsed) > 250:
        raise ValueError("IDENTITY_QUERY_INVALID")
    return unicodedata.normalize("NFC", collapsed.casefold())


def _identity_int(value, name: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"IDENTITY_VALUE_INVALID: {name}")
    return value


def _identity_token(value, name: str) -> str:
    if not isinstance(value, str) or not _IDENTITY_TOKEN.fullmatch(value):
        raise ValueError(f"IDENTITY_VALUE_INVALID: {name}")
    return value


def search_operation_args(*, query: str, capability: str = "search",
                          country: str | None = None, language: str | None = None,
                          mode: str | None = None, engine: str | None = None,
                          limit: int | None = None, page: int | None = None,
                          extra: dict | None = None) -> dict:
    """The ``args`` mapping both search adapters hand the ledger.

    Callers pass what they resolved: a vendor that has a category passes ``mode``,
    one that has an engine passes ``engine``, and neither fabricates the other's
    axis. ``limit``/``page`` default here rather than at the call site so that
    "omitted" and "explicitly 10" cannot become two purchases.
    """
    if not isinstance(capability, str) or not _IDENTITY_TOKEN.fullmatch(capability):
        raise ValueError("IDENTITY_VALUE_INVALID: capability")

    params: dict[str, object] = {}
    if country is not None:
        if not isinstance(country, str) or not _COUNTRY_2.fullmatch(country):
            raise ValueError("IDENTITY_VALUE_INVALID: country")
        params["country"] = country.upper()
    if language is not None:
        if not isinstance(language, str) or not _LANGUAGE_TAG.fullmatch(language):
            raise ValueError("IDENTITY_VALUE_INVALID: language")
        params["language"] = language.casefold()
    params["limit"] = _identity_int(10 if limit is None else limit, "limit", minimum=1)
    params["page"] = _identity_int(1 if page is None else page, "page", minimum=1)
    if mode is not None:
        params["mode"] = _identity_token(mode, "mode")
    if engine is not None:
        params["engine"] = _identity_token(engine, "engine")

    if extra is not None:
        if type(extra) is not dict:
            raise ValueError("IDENTITY_VALUE_INVALID: extra")
        for name in extra:
            if not isinstance(name, str):
                raise ValueError("IDENTITY_VALUE_INVALID: extra")
            if name in IDENTITY_FORBIDDEN_FIELDS:
                # Loud rather than dropped: a caller that thinks it is recording
                # `action` in the identity has misunderstood the decision, and
                # silently discarding it would hide that.
                raise ValueError(f"IDENTITY_FIELD_FORBIDDEN: {name}")
        for name, value in extra.items():
            if not isinstance(value, _SCALAR_IDENTITY) or isinstance(value, bytes):
                raise ValueError(f"IDENTITY_VALUE_NOT_SCALAR: {name}")
            params[name] = value

    return {
        "capability": capability,
        "query": normalise_search_query(query),
        "params": {name: params[name] for name in sorted(params)},
    }


def search_route_identity(*, provider: str, account_ref: str, query: str,
                          capability: str = "search", units: int = 1,
                          site: str = "", **operation_args) -> dict:
    """The full set of values B's ``route_shape`` digests, in one structure.

    It is returned as a plain mapping rather than a hash on purpose: this module
    does not own the digest. B's ``route_shape(request, args)`` is the authority,
    and a second implementation here would be a second answer to "what did we
    buy". Adapters expose this so the integrator can read it, and so two adapters
    can be compared field by field instead of hash by hash.
    """
    if provider not in PROVIDERS:
        raise ValueError("CANONICAL_PROVIDER_UNSUPPORTED")
    if not isinstance(account_ref, str):
        raise ValueError("IDENTITY_VALUE_INVALID: account_ref")
    if not isinstance(site, str):
        raise ValueError("IDENTITY_VALUE_INVALID: site")
    return {
        "provider": provider,
        "account_ref": account_ref,
        "capability": capability,
        "units": _identity_int(units, "units", minimum=1),
        "site": site,
        "args": search_operation_args(query=query, capability=capability,
                                      **operation_args),
    }


#: What a dispatch guard knows about an operation it has seen. ``UNKNOWN`` is the
#: one that matters: the request went out and the outcome never came back, so the
#: charge is unresolved and the operation must not be re-sent.
DISPATCH_DISPATCHED = "DISPATCHED"
DISPATCH_UNKNOWN = "UNKNOWN"
DISPATCH_RESOLVED = ("OK", "FAILED", "BLOCKED")


class DispatchGuard:
    """Refuse a second dispatch of one logical operation, in this process.

    **This is not the ledger.** The durable record is ``UsageLedger``'s
    ``op_signature``, and nothing here replaces it. What this closes is a gap the
    ledger alone does not: an ambiguous timeout. The request left, the answer did
    not arrive, and the adapter still holds the operation key — so a caller that
    "just retries" is answered ``DUPLICATE_OPERATION`` from the closed set instead
    of being quietly billed twice for one answer.

    It is deliberately per-instance and in-memory, and it says so: a guard that
    lived in a database would be a second ledger, which is the thing this whole
    architecture exists to avoid.
    """

    def __init__(self) -> None:
        self._state: dict[str, str] = {}

    def begin(self, operation_key: str) -> None:
        """Claim the operation, or refuse it by name.

        A key that was ever dispatched is refused, settled or not: an identical
        operation is one spend opportunity whether the first attempt succeeded,
        failed, or was never heard from.
        """
        if operation_key in self._state:
            raise ValueError("DUPLICATE_OPERATION")
        self._state[operation_key] = DISPATCH_DISPATCHED

    def resolve(self, operation_key: str, outcome: str) -> str:
        """Record how the dispatch ended. ``UNKNOWN`` means "hold, do not resend"."""
        if outcome not in DISPATCH_RESOLVED and outcome != DISPATCH_UNKNOWN:
            raise ValueError("GUARD_OUTCOME_NOT_IN_CLOSED_SET")
        if operation_key not in self._state:
            raise ValueError("GUARD_UNKNOWN_OPERATION")
        self._state[operation_key] = outcome
        return outcome

    def state(self, operation_key: str) -> str | None:
        return self._state.get(operation_key)

    def unresolved(self) -> dict[str, str]:
        """Operations still holding an exposure — what a resume decision reads."""
        return {key: state for key, state in self._state.items()
                if state in (DISPATCH_DISPATCHED, DISPATCH_UNKNOWN)}


__all__ = [
    "ARGUMENT_FIELDS", "DISPATCH_DISPATCHED", "DISPATCH_RESOLVED", "DISPATCH_UNKNOWN",
    "DispatchGuard", "IDENTITY_FORBIDDEN_FIELDS", "PROVIDERS",
    "SEARCH_IDENTITY_SCHEMA", "TRANSPORTS", "canonical_arguments",
    "normalise_search_query", "search_operation_args",
    "search_route_identity",
    "intent_signature", "operation_signature", "resource_operation_key",
    "same_intent", "same_operation",
]

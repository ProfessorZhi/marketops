#!/usr/bin/env python3
"""What this adapter is allowed to *claim* about Serper, and on what evidence.

Serper publishes no vendor-maintained MCP, so Serper is reached over its REST
API. The risk this module exists to remove is not "a call fails" — it is a
different one: the product quietly presenting *nine categories we believe
exist* as *nine categories we verified*. Those are two different statements and
only one of them was measured.

**What was actually read for this registry (2026-10-17).**

- ``https://serper.dev/`` — read successfully. It lists ten vertical labels
  (Search, Images, News, Maps, Places, Videos, Shopping, Scholar, Patents,
  Autocomplete) and sample response keys (``organic``, ``images``, ``news``,
  ``places``, ``videos``, ``shopping``), but **no endpoint paths, no credit
  costs and no pagination parameters**. UI labels are not a route table.
- ``https://docs.serper.dev/`` — **did not resolve** (DNS ``ENOTFOUND``). The
  canonical API reference was therefore never read on this host.
- The remaining endpoint paths (``/search``, ``/places``, ``/maps``,
  ``/scholar``, ``/patents``, ``/autocomplete``, ``/lens``, ``/reviews``) and
  the per-endpoint ``num`` rules reached us only through **third-party**
  artifacts — client SDKs, an MCP toolkit, an n8n node and a community OpenAPI
  file. Those are evidence about Serper, not Serper's own statement.

So every category below is ``SECONDARY_SOURCES_ONLY`` or
``OFFICIAL_SITE_LABEL_ONLY``, and none is ``LIVE_VERIFIED``: no request was
made to Serper at all, which is the point — a paid call is not a verification
step we are authorized to buy. ``coverage_report()`` is the honest readout and
the only place a caller should take coverage numbers from.

**No price is stored here.** Serper's per-query credit cost depends on the
account, the plan and the endpoint (third-party sources report that
``num > 10`` on Images *doubles* the credit charge and that batching costs a
multiple of the single-query price). A number baked in here would outlive the
terms it described, so ``price_per_query_usd`` is ``None`` and the basis is
``UNKNOWN_ACCOUNT_DEPENDENT``. The convergence review says the same thing:
never hard-code a perpetual free or fixed price.
"""
from __future__ import annotations

from typing import NamedTuple

# Verification vocabulary. Ordered by strength so a report can be read at a
# glance; only the last member is a measurement of a real request, and nothing
# in this package sets it today.
VERIFICATION_STATES = (
    "OFFICIAL_SITE_LABEL_ONLY",        # a vendor page showed the label; no route
    "SECONDARY_SOURCES_ONLY",          # third-party SDK/OpenAPI/n8n agree, vendor silent
    "OFFICIAL_DOCS_ONLY",              # vendor reference read, request never sent
    "LIVE_VERIFIED",                   # a real request was made and its receipt kept
)

# Pagination styles. ``PAGE_1_BASED`` is the only style this adapter can send
# without inventing an argument; the other three exist so a refusal can name
# its own reason instead of being folded into "unsupported mode".
_PAGE_1_BASED = "PAGE_1_BASED"
_NOT_PAGINATED = "NOT_PAGINATED"
_REQUIRES_UNSENT_PARAM = "REQUIRES_UNSENT_PARAM"
_PAGINATION_UNSOURCED = "PAGINATION_UNSOURCED"

# Dispatchability. A category can be *declared* and still not be *servable* by
# this adapter — `/reviews` is the case in point. Keeping it declared makes it
# visible in the catalog (the convergence review asks for that explicitly)
# while refusing dispatch for a reason that names the missing parameter,
# rather than letting it be silently absent or silently sent.
_DISPATCH_OK = "OK"
_REQUIRES_UNSENT_PARAM_DISPATCH = "REQUIRES_UNSENT_PARAM"

# One host. `scrape.serper.dev` is a different origin and this adapter's
# allowlist is `google.serper.dev` only, so it is deliberately not declared.
PROVIDER = "serper"
HOST = "https://google.serper.dev"

# Paths third-party sources enumerate on this host that no dispatch mode
# reaches. `/maps` is declared here rather than mapped, because mapping a mode
# onto a new path is a live request-shape change and the sources disagree about
# whether `/maps` and `/places` are one endpoint or two.
UNROUTED_DECLARED_PATHS = ("/maps",)


class ModeSpec(NamedTuple):
    """One Serper search category as this adapter will actually send it."""

    mode: str
    endpoint_path: str
    result_key: str
    verification: str
    evidence: str
    endpoint_note: str
    pagination: str
    declared_max_page: int | None
    credit_basis: str
    cost_notes: tuple[str, ...]
    pricing_source: str
    pricing_version: str
    priced_at: str
    credit_rules: tuple[dict, ...] = ()
    dispatch: str = _DISPATCH_OK
    price_per_query_usd: None = None


# The one pricing source every Serper category names. It is a *source id*, not
# a price: the Wave-1 checklist requires the num>10 double-credit rule to enter
# pricing, and it enters as a cited rule with an unverified unit price rather
# than as a number that would outlive the terms it described.
PRICING_SOURCE = "serper.credit_rules"
PRICING_VERSION = "unverified-2026-10-17"
PRICED_AT = "2026-10-17"
#: The reading is date-precision. B's ``Price.observed_at`` requires a timezone
#: carrying timestamp, so the date is encoded at the start of that day and the
#: precision is labelled rather than quietly acquiring an hour nobody observed.
PRICED_AT_TIMESTAMP = "2026-10-17T00:00:00+00:00"
PRICING_PRECISION = "DATE_PRECISION_ENCODED_AT_MIDNIGHT_UTC"

#: The vendor unit this provider meters in. In B's closed ``QUOTA_UNITS`` set:
#: a free-form label would let a report file a money-looking column under quota.
QUOTA_UNIT = "credits"
#: The capability id the price is attached to. Rates are looked up by
#: ``(provider, capability)``, never by a vendor tool name.
PRICE_CAPABILITY = "serper.search"

_NUM_GT_10_RULE = {
    "rule": "images_num_gt_10_doubles_credit",
    "effect": "CREDIT_MULTIPLIER",
    "multiplier": 2,
    "applies_to": ("images",),
    # ``when`` is what makes the rule *evaluable*. Without it the rule was a
    # cited note that reached no accounting value at all, so a run judged against
    # a credit count could not see the doubling it was actually paying.
    "when": {"modes": ("images",), "num_gt": 10},
    "verification": "SECONDARY_SOURCES_ONLY",
    "evidence": "third-party client SDK / toolkit notes; not read from Serper's own "
                "API reference, which did not resolve from this host on 2026-10-17",
    "observed_at": PRICED_AT,
}
_BATCH_RULE = {
    "rule": "batched_request_costs_a_multiple_of_single_query",
    "effect": "CREDIT_MULTIPLIER",
    "multiplier": None,
    "applies_to": ("*",),
    # Empty ``modes`` on purpose: this adapter sends one query per dispatch, so
    # the batching rule is declared and **never applies here**. Giving it a
    # non-empty mode list would multiply single queries by a rule about batches.
    "when": {"modes": (), "num_gt": None},
    "verification": "SECONDARY_SOURCES_ONLY",
    "evidence": "third-party toolkit notes; multiplier not stated numerically",
    "observed_at": PRICED_AT,
}


def _spec(mode, endpoint_path, result_key, evidence, endpoint_note="",
          pagination=_PAGE_1_BASED, declared_max_page=10, cost_notes=(),
          dispatch=_DISPATCH_OK, credit_rules=()) -> ModeSpec:
    return ModeSpec(
        mode=mode, endpoint_path=endpoint_path, result_key=result_key,
        verification="SECONDARY_SOURCES_ONLY", evidence=evidence,
        endpoint_note=endpoint_note, pagination=pagination,
        declared_max_page=declared_max_page,
        credit_basis="UNKNOWN_ACCOUNT_DEPENDENT",
        cost_notes=("Per-query credit cost is account/plan dependent and not stored here.",
                    *cost_notes),
        pricing_source=PRICING_SOURCE, pricing_version=PRICING_VERSION,
        priced_at=PRICED_AT, credit_rules=credit_rules, dispatch=dispatch,
    )


# ``/places`` is what mode ``maps`` is sent to, and that is a deliberate,
# reversible choice rather than a rename: the Maps *vertical* was observed on
# serper.dev, but third-party sources disagree about whether ``/maps`` and
# ``/places`` are one endpoint or two, so the adapter keeps the path it already
# shipped and records the ambiguity instead of changing a live request shape on
# secondary evidence.
_SPECS: tuple[ModeSpec, ...] = (
    _spec("web", "/search", "organic",
          "serper.dev homepage 'Search' vertical + sample `organic` key; "
          "path and `page`/`num` from third-party SDKs and OpenAPI only",
          endpoint_note="General web SERP; the only category every source agrees on.",
          cost_notes=("Third-party reports: a batched request costs a multiple of the single-query price.",),
          credit_rules=(_BATCH_RULE,)),
    _spec("maps", "/places", "places",
          "serper.dev homepage lists 'Maps' and 'Places' as separate labels; "
          "third-party SDKs alias places->/maps while the n8n node keeps both",
          endpoint_note="Sent to /places, not /maps. Third-party sources report /maps "
                        "pages after the first require `ll`, which this adapter never sends.",
          pagination=_REQUIRES_UNSENT_PARAM, declared_max_page=None),
    _spec("images", "/images", "images",
          "serper.dev homepage 'Images' vertical + sample `images` key; "
          "path from third-party SDKs only",
          endpoint_note="Third-party sources report `num` is accepted as 10 or 100 only, "
                        "and that num>10 doubles the credit charge.",
          cost_notes=("Third-party reports: num>10 doubles the credit charge on this endpoint.",),
          credit_rules=(_NUM_GT_10_RULE, _BATCH_RULE)),
    _spec("news", "/news", "news",
          "serper.dev homepage 'News' vertical + sample `news` key; "
          "path from third-party SDKs only",
          endpoint_note="Third-party sources report a time-range filter is available here.",
          credit_rules=(_BATCH_RULE,)),
    _spec("videos", "/videos", "videos",
          "serper.dev homepage 'Videos' vertical + sample `videos` key; "
          "path from third-party SDKs only",
          credit_rules=(_BATCH_RULE,)),
    _spec("shopping", "/shopping", "shopping",
          "serper.dev homepage 'Shopping' vertical + sample `shopping` key; "
          "path from third-party SDKs only",
          endpoint_note="Third-party sources report this endpoint pins num=40 itself.",
          cost_notes=("Third-party reports: the endpoint fixes its own result count, "
                      "so a caller-supplied num may be ignored upstream.",),
          credit_rules=(_BATCH_RULE,)),
    _spec("scholar", "/scholar", "organic",
          "serper.dev homepage 'Scholar' vertical; result key and path from "
          "third-party SDKs only",
          endpoint_note="Third-party sources report `num` is dropped entirely here and "
                        "that author/year filters exist.",
          cost_notes=("Third-party reports: a caller-supplied num may be dropped upstream.",),
          credit_rules=(_BATCH_RULE,)),
    _spec("patents", "/patents", "organic",
          "serper.dev homepage 'Patents' vertical + sample `organic` key; "
          "path from third-party SDKs only",
          credit_rules=(_BATCH_RULE,)),
    _spec("autocomplete", "/autocomplete", "suggestions",
          "serper.dev homepage 'Autocomplete' vertical; path from third-party "
          "SDKs only. The `suggestions` result key is NOT sourced — it is what "
          "this adapter already parsed before this registry existed.",
          endpoint_note="Third-party sources report `num` is dropped here and that the "
                        "endpoint returns a completion list, not a paged result set.",
          pagination=_NOT_PAGINATED, declared_max_page=None,
          credit_rules=(_BATCH_RULE,)),
    _spec("lens", "/lens", "organic",
          "third-party SDK and OpenAPI artifacts enumerate /lens on "
          "google.serper.dev. serper.dev's homepage does NOT list a Lens vertical, "
          "so this category's only provenance is secondary.",
          endpoint_note="Dispatchable: third-party sources describe it as image-driven "
                        "and reachable with the same body shape. Result key `organic` is "
                        "NOT sourced; it is this adapter's declared default.",
          pagination=_PAGINATION_UNSOURCED, declared_max_page=None,
          credit_rules=(_BATCH_RULE,)),
    _spec("reviews", "/reviews", "reviews",
          "third-party SDK and OpenAPI artifacts enumerate /reviews on "
          "google.serper.dev with a `placeId` requirement. serper.dev's homepage "
          "does NOT list a Reviews vertical, so this category's only provenance is "
          "secondary.",
          endpoint_note="DECLARED BUT NOT SERVABLE: /reviews requires a `placeId` and "
                        "pages by cursor (`nextPageToken`). This adapter sends neither, "
                        "so it refuses dispatch instead of sending a request that would "
                        "fail upstream. It stays visible here rather than silently absent.",
          pagination=_REQUIRES_UNSENT_PARAM, declared_max_page=None,
          dispatch=_REQUIRES_UNSENT_PARAM_DISPATCH,
          credit_rules=(_BATCH_RULE,)),
)

_BY_MODE = {spec.mode: spec for spec in _SPECS}
ALL_MODES = tuple(spec.mode for spec in _SPECS)

# The read attempt that produced the states above. Kept in the module, not in a
# doc, so a negative reading travels with the code that depends on it — a
# report that says "unverified" without saying what was read cannot be argued
# with, and the next reader re-does the same work or, worse, assumes it was
# never done.
OFFICIAL_DOCS_ATTEMPT = {
    "observed_at": "2026-10-17",
    "sources_read": ("https://serper.dev/",),
    "sources_unreachable": ("https://docs.serper.dev/",),
    "summary": ("serper.dev homepage read: ten vertical labels and sample response keys, "
                "no endpoint paths, credit costs or pagination parameters. "
                "docs.serper.dev did not resolve (DNS ENOTFOUND), so no canonical Serper "
                "API reference was read on this host. Endpoint paths and per-endpoint num "
                "rules come from third-party SDK/n8n/OpenAPI artifacts only. No request "
                "was sent to Serper."),
}


def mode_spec(mode: str) -> ModeSpec:
    """One declared category, or a refusal that names the category.

    Refusing an undeclared category is the whole point: ``/lens`` and
    ``/reviews`` appear in third-party clients, and a mode string accepted on
    that basis would be coverage claimed from someone else's client library.
    """
    spec = _BY_MODE.get(mode) if isinstance(mode, str) else None
    if spec is None:
        raise ValueError("UNSUPPORTED_SERPER_MODE")
    return spec


def pagination_param(mode: str) -> str | None:
    """The request-field name this adapter would send to page, if any."""
    return "page" if mode_spec(mode).pagination == _PAGE_1_BASED else None


def assert_dispatchable(mode: str) -> str:
    """Can this adapter actually send this declared category?

    A category can be declared and still not servable — ``/reviews`` needs a
    ``placeId`` and a cursor this adapter never sends. Refusing it here keeps
    the word "declared" from quietly meaning "works".
    """
    spec = mode_spec(mode)
    if spec.dispatch == _REQUIRES_UNSENT_PARAM_DISPATCH:
        raise ValueError("SERPER_MODE_REQUIRES_UNSENT_PARAM")
    return mode


def assert_page_allowed(mode: str, page: int) -> int:
    """Validate a requested page *before* any reservation or network call.

    Four refusals, four different statements: the page is not a page; this
    category cannot be sent at all; this category has no pages; or this
    category has pages only when an argument we do not send is present.
    Folding those into one "unsupported" would tell a caller to go look in the
    wrong place.
    """
    spec = mode_spec(mode)
    assert_dispatchable(mode)
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("SERPER_PAGE_INVALID")
    if spec.pagination == _NOT_PAGINATED:
        if page > 1:
            raise ValueError("SERPER_MODE_NOT_PAGINATED")
        return page
    if spec.pagination in (_REQUIRES_UNSENT_PARAM, _PAGINATION_UNSOURCED):
        if page > 1:
            raise ValueError("SERPER_MODE_PAGINATION_UNVERIFIED")
        return page
    if spec.declared_max_page is not None and page > spec.declared_max_page:
        raise ValueError("SERPER_PAGE_INVALID")
    return page


def declared_endpoint_paths() -> tuple[str, ...]:
    """Every path on this host the sources enumerate, routed or not.

    This is the number to quote when someone asks "how many Serper endpoints".
    It is a count of *declared paths*, not of verified or servable ones.
    """
    seen: list[str] = [spec.endpoint_path for spec in _SPECS]
    for path in UNROUTED_DECLARED_PATHS:
        if path not in seen:
            seen.append(path)
    return tuple(seen)


def pricing_terms(mode: str) -> dict:
    """IL-6's three pricing fields, plus the cited rules behind them.

    ``unit_price_usd`` stays ``None`` and ``unit_price_verified`` stays
    ``False``: a source id is not a price, and no account price was ever read.
    """
    spec = mode_spec(mode)
    return {
        "source_id": spec.pricing_source,
        "version": spec.pricing_version,
        "priced_at": spec.priced_at,
        "rules": [dict(rule) for rule in spec.credit_rules],
        "unit_price_usd": None,
        "unit_price_verified": False,
        "credit_basis": spec.credit_basis,
    }


def credit_estimate(mode: str, *, num: int | None = None) -> dict:
    """The credit multiplier this concrete request incurs, with its provenance.

    **``credits`` is ``None``, never ``0``.** The account's per-query credit price
    was never read, and a zero here would be read downstream as "this call is
    free" — the opposite of what "we did not read it" means. What *is* known is
    the multiplier: the doubling rule is evaluable even when the base price is
    not, and that is the part a budget judgement needs.

    Every field IL-6 requires (``source_id`` / ``version`` / ``priced_at``) is
    present, plus ``observed_at``, which happens to be the same date here and is
    kept separate because a *rate* and a *reading of a rate* are different facts.
    """
    spec = mode_spec(mode)
    applied = [rule for rule in spec.credit_rules if _rule_applies(rule, mode, num)]
    multiplier = 1
    for rule in applied:
        value = rule.get("multiplier")
        if isinstance(value, int) and not isinstance(value, bool) and value > 1:
            multiplier *= value
    return {
        "mode": spec.mode,
        # Absolute counts stay unknown: we know the *ratio*, not what a credit
        # costs or how many the account is charged before multipliers.
        "credits": None,
        "multiplier": multiplier,
        # What the campaign floor can actually count. `quota_per_call` is the
        # multiplier because a doubled-credit call consumes two of the vendor's
        # units — that much is declared, and it is enough to move a floor held in
        # credits while the dollar side stays unknown.
        "quota_unit": QUOTA_UNIT,
        "quota_per_call": multiplier,
        "unit_micros": 0,
        "free_tier": False,
        "verified": False,
        "usd_price_known": False,
        # Named rather than implied: a downstream reader must be able to tell
        # "we never read a dollar rate" from "the rate is zero". ``0`` micros
        # with ``free_tier False`` already encodes that in B's Price shape; this
        # is the same statement in words, for receipts and reports.
        # There is no free tier recorded for Serper and no dollar rate either,
        # so this is the only honest value today. It is a named token rather
        # than a comment because a receipt must be machine-readable.
        "usd_basis": "UNKNOWN_NOT_FREE",
        "base": "ONE_QUERY",
        "applied_rules": tuple(rule["rule"] for rule in applied),
        "source_id": spec.pricing_source,
        "version": spec.pricing_version,
        "priced_at": spec.priced_at,
        "observed_at": spec.priced_at,
        "unit_price_usd": None,
        "unit_price_verified": False,
        "credit_basis": spec.credit_basis,
        "precision": PRICING_PRECISION,
    }


def price_fields(mode: str, *, num: int | None = None) -> dict:
    """The reading in B's ``Price`` kwargs, so the floor can see the meter.

    ``unit_micros = 0`` with ``free_tier = False`` is that ledger's encoding for
    "nobody has a verified dollar rate" — deliberately **not** free. The quota
    side is real: ``quota_per_call`` is the multiplier the declared rule produces.

    This module does not construct a ``Price``: the type belongs to the ledger,
    and importing it here would make a vendored copy of B's module the price
    authority. It emits the field names and lets the owner construct it.
    """
    estimate = credit_estimate(mode, num=num)
    return {
        "provider": PROVIDER,
        "capability": PRICE_CAPABILITY,
        "unit_micros": estimate["unit_micros"],
        "quota_unit": estimate["quota_unit"],
        "quota_per_call": estimate["quota_per_call"],
        "source": estimate["source_id"],
        "version": estimate["version"],
        "observed_at": PRICED_AT_TIMESTAMP,
        "free_tier": estimate["free_tier"],
        "verified": estimate["verified"],
    }


def _rule_applies(rule: dict, mode: str, num: int | None) -> bool:
    """Whether a declared rule fires for this request. An absent ``when`` = never.

    Defaulting an un-evaluable rule to "applies" would multiply every request;
    defaulting it to "does not apply" would hide a real cost. Neither is a
    default — so a rule that cannot say when it fires does not fire, and the
    declaration is the thing that has to be fixed.
    """
    when = rule.get("when") or {}
    modes = when.get("modes") or ()
    if mode not in modes:
        return False
    threshold = when.get("num_gt")
    if threshold is None:
        return False
    return isinstance(num, int) and not isinstance(num, bool) and num > threshold


def coverage_report() -> dict:
    """The only honest coverage readout: declared, not verified.

    ``live_verified_modes`` is computed rather than asserted, so the day a real
    receipt exists this number moves on its own instead of needing an edit
    here.
    """
    rows = [{
        "mode": spec.mode,
        "endpoint_path": spec.endpoint_path,
        "result_key": spec.result_key,
        "verification": spec.verification,
        "evidence": spec.evidence,
        "pagination": spec.pagination,
        "declared_max_page": spec.declared_max_page,
        "credit_basis": spec.credit_basis,
        "price_per_query_usd": spec.price_per_query_usd,
        "pricing": pricing_terms(spec.mode),
        "dispatch": spec.dispatch,
        "cost_notes": list(spec.cost_notes),
    } for spec in _SPECS]
    verified = [row for row in rows if row["verification"] == "LIVE_VERIFIED"]
    return {
        "schema": "marketops.serper.coverage.v1",
        "provider": "serper",
        "host": HOST,
        "official_mcp": False,
        "officially_verified_live": bool(verified),
        "official_docs_read_successfully": False,
        "declared_modes": len(_SPECS),
        "declared_endpoint_paths": len(declared_endpoint_paths()),
        "unrouted_declared_paths": list(UNROUTED_DECLARED_PATHS),
        "not_servable_modes": [s.mode for s in _SPECS
                               if s.dispatch != _DISPATCH_OK],
        "live_verified_modes": len(verified),
        "cost_basis": "UNKNOWN_ACCOUNT_DEPENDENT",
        "rows": rows,
        "official_docs_attempt": dict(OFFICIAL_DOCS_ATTEMPT),
        "claims_boundary": (
            "Declared category coverage only. No request was sent to Serper and no "
            "account price was read, so this says nothing about authentication, "
            "entitlement, cost or result quality."
        ),
    }


__all__ = [
    "ALL_MODES", "HOST", "ModeSpec", "OFFICIAL_DOCS_ATTEMPT", "PRICED_AT",
    "PRICED_AT_TIMESTAMP", "PRICING_PRECISION", "PRICING_SOURCE", "PROVIDER",
    "PRICING_VERSION", "PRICE_CAPABILITY", "QUOTA_UNIT",
    "UNROUTED_DECLARED_PATHS",
    "VERIFICATION_STATES", "assert_dispatchable", "assert_page_allowed",
    "coverage_report", "credit_estimate", "declared_endpoint_paths", "mode_spec",
    "price_fields",
    "pagination_param", "pricing_terms",
]

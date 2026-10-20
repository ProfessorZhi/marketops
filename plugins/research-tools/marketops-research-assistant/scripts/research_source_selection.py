#!/usr/bin/env python3
"""Offline, deterministic gap-driven source selection for one existing Run.

This is an *advisory* over local state, not a search engine. It reads the active
candidates, the evidence links that were actually recorded for them, and the
already-known source URLs, then says which missing field to finish and which
official page (or bounded query hint) is the next best attempt. It never calls a
provider, never changes a budget, and never turns a successful fetch into a
verified field: only a linked claim field counts as evidence.

The structured Brief's ``target_kind`` and ``goal`` decide what is relevant. No
generic 0.6 schema is introduced; the input is the existing Lite store row shape.
"""
from __future__ import annotations

from collections import defaultdict
from urllib.parse import urlsplit

__all__ = ["SCHEMA", "POLICY", "select_next_sources", "blocked_domains_from_sources"]

SCHEMA = "marketops.source-selection/1"
POLICY = "OFFLINE_GAP_DRIVEN_V1"

#: The fields this advisory can name. "等" is intentional: a caller may add a
#: field, but every field must map to a bounded action below.
FIELD_PRIORITY = {
    "organization_identity": 100,
    "venue_identity": 100,
    "venue_org_relationship": 92,
    "contact_binding": 90,
    "hourly_rate": 80,
    "official_email": 70,
    "named_person_contact_chain": 60,
    "phone": 40,
}

FIELD_ACTION = {
    "organization_identity": {
        "action": "OFFICIAL_ABOUT_OR_HOME_PAGE",
        "path_hints": ("about", "who-we-are", "home"),
        "query": "official site organisation",
    },
    "venue_identity": {
        "action": "OFFICIAL_VENUE_PAGE",
        "path_hints": ("venue", "hall", "facilities", "hire"),
        "query": "official venue page",
    },
    "venue_org_relationship": {
        "action": "OFFICIAL_VENUE_PAGE_LINKED_FROM_ORG",
        "path_hints": ("venue", "hall", "facilities", "hire"),
        "query": "venue operated by organisation",
    },
    "contact_binding": {
        "action": "NAMED_CONTACT_TO_ORG_RELATIONSHIP",
        "path_hints": ("contact", "team", "staff", "enquiries"),
        "query": "contact for organisation",
    },
    "hourly_rate": {
        "action": "OFFICIAL_RATE_SHEET_OR_BOOKING_PAGE",
        "path_hints": ("rate", "rates", "pricing", "price", "hire", "booking", "bookings", "fees"),
        "query": "hourly rate hire fees booking",
    },
    "official_email": {
        "action": "OFFICIAL_CONTACT_PAGE",
        "path_hints": ("contact", "contact-us", "enquiries"),
        "query": "contact email",
    },
    "named_person_contact_chain": {
        "action": "OFFICIAL_TEAM_OR_CONTACT_PAGE",
        "path_hints": ("team", "staff", "contact", "people"),
        "query": "named person role contact",
    },
    "phone": {
        "action": "OFFICIAL_CONTACT_PAGE",
        "path_hints": ("contact", "contact-us", "enquiries"),
        "query": "phone number",
    },
}

_DIRECTORY_SEGMENTS = ("/directory", "/listing", "/listings", "/venues", "/places",
                       "/search", "/results")
_PLACEHOLDERS = frozenset({"", "UNKNOWN", "NONE", "N/A", "NULL", "TBD", "NOT_FOUND"})
_RATE_KEYS = ("hourly_rate", "price", "rate", "pricing", "hire_rate", "fee")
_EMAIL_TYPES = frozenset({"email", "e-mail", "mail"})
_PHONE_TYPES = frozenset({"phone", "telephone", "tel", "mobile"})


def _text(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip())
            and value.strip().upper() not in _PLACEHOLDERS)


def _domain(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        return ""
    raw = url.strip()
    if "://" not in raw:
        # ``official_domain`` is stored as a bare host, not a URL.
        raw = "//" + raw
    try:
        host = urlsplit(raw).hostname or ""
    except ValueError:
        return ""
    host = host.casefold()
    return host[4:] if host.startswith("www.") else host


def _path(url: object) -> str:
    if not isinstance(url, str):
        return ""
    try:
        return (urlsplit(url.strip()).path or "/").casefold()
    except ValueError:
        return ""


def _claims(row: dict) -> list[dict]:
    claims = (row.get("data") or {}).get("claims")
    return [claim for claim in claims if isinstance(claim, dict)] if isinstance(claims, list) else []


def _quote_supports(quote: object, value: object) -> bool:
    """Mirror the delivery token rule without importing it at module load.

    Kept local to avoid a circular import (delivery imports workflow, and
    workflow imports this module). The tests compare both implementations on the
    same synthetic values.
    """
    if not isinstance(quote, str) or not isinstance(value, str):
        return False
    import re
    token = re.compile(r"[0-9a-z]+")
    from research_evidence import _norm
    value_tokens = tuple(token.findall(_norm(value)))
    if not value_tokens:
        return False
    quote_tokens = tuple(token.findall(_norm(quote)))
    width = len(value_tokens)
    return any(quote_tokens[index:index + width] == value_tokens
               for index in range(max(0, len(quote_tokens) - width + 1)))


def _literal_in(text: object, value: object) -> bool:
    if not isinstance(text, str) or not isinstance(value, str) or not value:
        return False
    import re
    if "@" in value:
        return re.search(r"(?<![\w.+-])" + re.escape(value) + r"(?![\w+-]|\.[\w+-])",
                         text, re.I) is not None
    return value.casefold() in text.casefold()


def _linked(links: dict[str, set[str]], record_id: str, *fields: str) -> bool:
    present = links.get(record_id) or set()
    return any(field in present for field in fields)


def _numbers(value: object) -> tuple[str, ...]:
    import re
    if not isinstance(value, str):
        value = str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else ""
    return tuple(re.findall(r"\d+(?:[.,]\d+)?", value))


def _rate_quote_supports(quote: object, value: object) -> bool:
    """A rate quote must actually contain the stored number, not just 'a price'."""
    if not isinstance(quote, str) or not isinstance(value, str):
        return False
    wanted = _numbers(value)
    if not wanted:
        return _quote_supports(quote, value)
    observed = set(_numbers(quote))
    return all(number in observed for number in wanted)


def _contact_value(contact: dict) -> str:
    data = contact.get("data") or {}
    value = data.get("value") or data.get("email") or data.get("phone")
    return value if isinstance(value, str) else (
        str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else "")


def _contact_value_supported(contact: dict, value: str,
                             links: dict[str, set[str]]) -> bool:
    """A stored contact value counts only when a linked claim quote says it."""
    linked_fields = links.get(contact["record_id"]) or set()
    if not linked_fields:
        return False
    for claim in _claims(contact):
        field = claim.get("field")
        quote = claim.get("quote")
        if field not in linked_fields or not isinstance(quote, str):
            continue
        if "@" in value:
            if _literal_in(quote, value):
                return True
        elif _rate_quote_supports(quote, value):
            return True
    return False


def _contact_binding_ok(contact: dict, org_name: str,
                        related_by_org: dict[str, list[dict]],
                        links: dict[str, set[str]]) -> bool:
    """Org attribution needs a named quote, not just a generic relationship link."""
    if not _text(org_name):
        return False
    value = _contact_value(contact)
    if not _text(value):
        return False
    for claim in _claims(contact):
        field = claim.get("field")
        quote = claim.get("quote")
        if field not in (links.get(contact["record_id"]) or set()):
            continue
        if _quote_supports(quote, org_name) and _literal_in(quote, value):
            return True
    org_key = (contact.get("data") or {}).get("organization_id")
    if not isinstance(org_key, str):
        return False
    for row in related_by_org.get(org_key, ()):
        if row.get("kind") != "relationship":
            continue
        data = row.get("data") or {}
        if data.get("contact_point_id") not in {contact["record_id"], contact["entity_key"]} \
                and data.get("contact_route_id") not in {contact["record_id"], contact["entity_key"]}:
            continue
        linked_fields = links.get(row["record_id"]) or set()
        for claim in _claims(row):
            if claim.get("field") != "relationship" or "relationship" not in linked_fields:
                continue
            quote = claim.get("quote")
            if _quote_supports(quote, org_name) and _literal_in(quote, value):
                return True
    return False


def _rate_covered(candidate: dict, links: dict[str, set[str]]) -> bool:
    data = candidate.get("data") or {}
    linked_fields = links.get(candidate["record_id"]) or set()
    for key in _RATE_KEYS:
        value = data.get(key)
        if not _text(value):
            continue
        for claim in _claims(candidate):
            field = claim.get("field")
            if field not in _RATE_KEYS or field not in linked_fields:
                continue
            if _rate_quote_supports(claim.get("quote"), value):
                return True
    return False


def _phone_covered(contact: dict, org_name: str,
                   related_by_org: dict[str, list[dict]],
                   links: dict[str, set[str]]) -> bool:
    value = _contact_value(contact)
    if not _text(value):
        return False
    if not _contact_value_supported(contact, value, links):
        return False
    return _contact_binding_ok(contact, org_name, related_by_org, links)


def blocked_domains_from_sources(sources: list[dict]) -> set[str]:
    """Domains a Source record itself marks as blocked; no guessing from status prose."""
    blocked = set()
    for source in sources:
        data = source.get("data") or {}
        status = str(data.get("status") or "").upper()
        if data.get("blocked") is True or status in {
                "BLOCKED", "SOURCE_ACCESS_BLOCKED", "SOURCE_CHALLENGE_PAGE"}:
            domain = _domain(data.get("url"))
            if domain:
                blocked.add(domain)
    return blocked


def _source_view(source: dict, *, official_domain: str, org_key: str,
                 org_keys: set[str], blocked_domains: set[str]) -> dict:
    data = source.get("data") or {}
    url = data.get("url")
    domain = _domain(url)
    path = _path(url)
    explicit = data.get("organization_id") in org_keys or data.get("org_id") == org_key
    official = bool(official_domain and domain == official_domain)
    directory = any(segment in path for segment in _DIRECTORY_SEGMENTS)
    if explicit:
        attribution = "EXPLICIT_ORG_LINK"
    elif official and not directory:
        attribution = "OFFICIAL_DOMAIN"
    elif official and directory:
        attribution = "OFFICIAL_DIRECTORY_UNATTRIBUTED"
    else:
        attribution = "UNATTRIBUTED"
    return {"url": url, "domain": domain, "path": path,
            "attribution": attribution, "directory": directory,
            "official": official, "explicit": explicit,
            "blocked": bool(domain and domain in blocked_domains)}


def _action_for(field: str, *, candidate: dict, org: dict | None,
                sources: list[dict], blocked_domains: set[str],
                budget: dict) -> dict:
    spec = FIELD_ACTION[field]
    org_key = (candidate.get("data") or {}).get("organization_id") or candidate["entity_key"]
    org_keys = {org_key, candidate["entity_key"]}
    official_domain = ""
    if org:
        official_domain = _domain((org.get("data") or {}).get("official_domain"))
    if not official_domain:
        official_domain = _domain((candidate.get("data") or {}).get("official_domain"))
    best = None
    for source in sources:
        view = _source_view(source, official_domain=official_domain,
                            org_key=org_key, org_keys=org_keys,
                            blocked_domains=blocked_domains)
        if view["blocked"]:
            continue
        if view["attribution"] in {"UNATTRIBUTED", "OFFICIAL_DIRECTORY_UNATTRIBUTED"}:
            # A shared directory page is not attribution evidence. It may still
            # be a discovery source, but it is never preferred for binding.
            continue
        hit = any(hint in view["path"] for hint in spec["path_hints"])
        if not hit and field in {"organization_identity", "venue_identity"}:
            # A site root is a legitimate identity page for these two actions.
            hit = view["path"] in {"", "/"}
        if not hit:
            # An explicitly linked but unrelated page is not the page this gap
            # needs; a bounded search hint is better than guessing.
            continue
        score = 0
        if hit:
            score += 40
        if view["explicit"]:
            score += 30
        if view["official"]:
            score += 20
        if score and (best is None or score > best[0]):
            best = (score, view)
    if best is not None:
        view = best[1]
        return {"action": spec["action"], "requires_search": False,
                "url": view["url"], "query": None,
                "attribution": view["attribution"],
                "reason": f"{field} is missing; finish it on the existing page",
                "blocked_domains": sorted(blocked_domains),
                "retry_unknown_charge": False}
    if official_domain and official_domain in blocked_domains:
        return {"action": "BLOCKED_DOMAIN_NO_RETRY", "requires_search": False,
                "url": None, "query": None, "attribution": "BLOCKED_DOMAIN",
                "reason": f"{field} remains missing but {official_domain} is blocked; "
                          "do not retry a failed domain",
                "blocked_domains": sorted(blocked_domains),
                "retry_unknown_charge": False}
    search_remaining = budget.get("search_remaining")
    if search_remaining is not None and search_remaining <= 0:
        return {"action": "BUDGET_EXHAUSTED_NO_SEARCH", "requires_search": False,
                "url": None, "query": None, "attribution": "NO_BUDGET",
                "reason": f"{field} remains missing but the search budget is 0; "
                          "do not buy or increase quota",
                "blocked_domains": sorted(blocked_domains),
                "retry_unknown_charge": False}
    if official_domain:
        query = f"site:{official_domain} {spec['query']}"
        attribution = "OFFICIAL_DOMAIN_SEARCH_HINT"
    else:
        name = (candidate.get("data") or {}).get("name") or candidate["entity_key"]
        query = f"{name} {spec['query']}"
        attribution = "NO_OFFICIAL_DOMAIN_SEARCH_HINT"
    return {"action": spec["action"], "requires_search": True,
            "url": None, "query": query, "attribution": attribution,
            "reason": f"{field} is missing; no existing official page carries it",
            "blocked_domains": sorted(blocked_domains),
            "retry_unknown_charge": False}


def _related_contacts(org_key: str, related_by_org: dict[str, list[dict]]) -> list[dict]:
    return [row for row in related_by_org.get(org_key, ())
            if row.get("kind") == "contact_point"]


def _has_person_chain(org_key: str, related_by_org: dict[str, list[dict]],
                      links: dict[str, set[str]]) -> bool:
    related = related_by_org.get(org_key, ())
    people = {row["entity_key"] for row in related
              if row.get("kind") == "person"
              and _linked(links, row["record_id"], "name", "department")}
    roles = {row["data"].get("person_id") for row in related
             if row.get("kind") == "role"
             and _linked(links, row["record_id"], "role_title", "role_function")}
    contacts = {row["data"].get("person_id") for row in related
                if row.get("kind") == "contact_point"
                and _linked(links, row["record_id"], "value", "email")}
    return bool(people and (roles & people) and (contacts & people))


def _missing_fields(candidate: dict, *, org: dict | None, org_name: str,
                    related_by_org: dict[str, list[dict]],
                    links: dict[str, set[str]], brief: dict) -> list[str]:
    missing: list[str] = []
    kind = candidate.get("kind")
    data = candidate.get("data") or {}
    if kind == "organization" and not _linked(links, candidate["record_id"], "identity"):
        missing.append("organization_identity")
    if kind == "venue" and not _linked(links, candidate["record_id"], "identity"):
        missing.append("venue_identity")
    if kind == "venue":
        venue_binding = _linked(links, candidate["record_id"], "relationship")
        if not venue_binding and _text(org_name):
            for claim in _claims(candidate):
                quote = claim.get("quote")
                if _quote_supports(quote, org_name) and _quote_supports(quote, data.get("name")):
                    venue_binding = True
                    break
        if not venue_binding:
            missing.append("venue_org_relationship")
    if not _rate_covered(candidate, links):
        missing.append("hourly_rate")
    org_key = data.get("organization_id") or candidate["entity_key"]
    contacts = _related_contacts(org_key, related_by_org)
    emails = [row for row in contacts
              if str((row.get("data") or {}).get("type")
                     or (row.get("data") or {}).get("channel") or "").casefold() in _EMAIL_TYPES]
    phones = [row for row in contacts
              if str((row.get("data") or {}).get("type")
                     or (row.get("data") or {}).get("channel") or "").casefold() in _PHONE_TYPES]
    email_supported = False
    email_bound = False
    for row in emails:
        value = _contact_value(row)
        if _text(value) and _contact_value_supported(row, value, links):
            email_supported = True
            if _contact_binding_ok(row, org_name, related_by_org, links):
                email_bound = True
    if not emails:
        missing.append("official_email")
    elif not email_supported or not email_bound:
        missing.append("contact_binding")
    if not any(_phone_covered(row, org_name, related_by_org, links) for row in phones):
        missing.append("phone")
    if brief.get("require_named_person") and not _has_person_chain(org_key, related_by_org, links):
        missing.append("named_person_contact_chain")
    return missing


def select_next_sources(*, brief: dict, candidates: list[dict],
                        related_by_org: dict[str, list[dict]] | None = None,
                        org_by_key: dict[str, dict] | None = None,
                        links_by_record: dict[str, set[str]] | None = None,
                        sources: list[dict] | None = None,
                        budget: dict | None = None,
                        blocked_domains: list[str] | set[str] | None = None,
                        limit: int = 8) -> dict:
    """Return a bounded, deterministic next-best-source advisory.

    ``links_by_record`` must contain only *recorded* evidence links. A source
    record, a successful fetch or an attached artifact is not a linked field.
    """
    related_by_org = related_by_org or {}
    org_by_key = org_by_key or {}
    links = links_by_record or {}
    sources = sources or []
    budget = budget or {}
    blocked = {domain.casefold().removeprefix("www.")
               for domain in (blocked_domains or ()) if isinstance(domain, str)}
    kind = brief.get("target_kind")
    rows = [row for row in candidates if row.get("kind") == kind]
    if not rows:
        return {"schema": SCHEMA, "policy": POLICY, "target_kind": kind,
                "goal": brief.get("goal"), "candidates": [],
                "next_action": {"action": "DISCOVER_NEW_TARGET",
                                "requires_search": True,
                                "reason": "no active target candidate exists yet",
                                "blocked_domains": sorted(blocked),
                                "retry_unknown_charge": False},
                "discovery_allowed": True,
                "budget": budget, "blocked_domains": sorted(blocked)}

    scored = []
    for candidate in rows:
        data = candidate.get("data") or {}
        org_key = data.get("organization_id") or candidate["entity_key"]
        org = org_by_key.get(org_key)
        org_name = ((org or candidate).get("data") or {}).get("name") or ""
        missing = _missing_fields(candidate, org=org, org_name=org_name,
                                  related_by_org=related_by_org, links=links,
                                  brief=brief)
        actions = []
        for field in missing:
            action = _action_for(field, candidate=candidate, org=org,
                                 sources=sources, blocked_domains=blocked,
                                 budget=budget)
            action["field"] = field
            action["priority"] = FIELD_PRIORITY.get(field, 10)
            actions.append(action)
        actions.sort(key=lambda item: (-item["priority"],
                                       item["requires_search"], item["action"]))
        total_fields = max(1, len(FIELD_PRIORITY))
        completeness = round(max(0.0, 1.0 - len(missing) / total_fields), 4)
        scored.append({
            "record_id": candidate["record_id"], "entity_key": candidate["entity_key"],
            "kind": candidate["kind"], "organization_id": org_key,
            "missing_fields": missing, "completeness": completeness,
            "next_best_source_action": actions[0] if actions else None,
            "alternatives": actions[:3],
        })
    # Near-complete candidates first; the budget can only narrow this ordering,
    # never invent quota. A 0/1 remaining search slot prefers a direct URL.
    scarce = any(budget.get(key) is not None and budget[key] <= 1
                 for key in ("search_remaining", "provider_remaining"))

    def action_priority(item: dict) -> int:
        action = item.get("next_best_source_action") or {}
        return int(action.get("priority") or 0)

    if scarce:
        # Finish the closest candidate first; a remaining slot is not a reason
        # to open a new one.
        scored.sort(key=lambda item: (item["completeness"], action_priority(item),
                                      item["entity_key"]), reverse=True)
    else:
        scored.sort(key=lambda item: (action_priority(item), item["completeness"],
                                      item["entity_key"]), reverse=True)
    next_action = scored[0]["next_best_source_action"] if scored else None
    result = {
        "schema": SCHEMA, "policy": POLICY,
        "target_kind": kind, "goal": brief.get("goal"),
        "budget": budget,
        "blocked_domains": sorted(blocked),
        "discovery_allowed": False,
        "discovery_policy": "FINISH_EXISTING_CANDIDATES_FIRST",
        "candidates": scored[:max(1, limit)],
        "next_action": next_action,
        "warning": ("A successful fetch or attached artifact is not a verified "
                    "field; only a linked claim field counts."),
    }
    if budget.get("unknown_held"):
        result["budget_note"] = "UNKNOWN_CHARGE_HELD_NO_RETRY"
    return result

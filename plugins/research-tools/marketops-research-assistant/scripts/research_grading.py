#!/usr/bin/env python3
"""A/B/C/D/X for each email route, decided by code from the scene's rules.

Why code: in 30 A/B routes sampled from past manual work, 12 did not hold up.
The failures were mechanical and repeat across markets:
  * name, role and email taken from different pages or blocks, then joined;
  * one inbox (info@, or one secretary's address) bound to several named people;
  * a role outside the wanted set graded A;
  * a role guessed with no person behind it graded A.
Each rule below closes one of those. The grade is a candidate grade for the
client sheet, with its reasons; it does not approve sending.

Units: grades are per email route; the KPI counts distinct people (person id,
else name within the organisation, else the address) so one person listed on
three pages is one A, not three.
"""
from __future__ import annotations

from collections import Counter, defaultdict

import research_scene as scene

__all__ = ["grade_routes", "summary", "named_leads"]

_TITLE_FIELDS = ("role_title", "role_function", "title", "department")


def _text(data: dict, *names: str) -> str:
    for name in names:
        value = data.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _quotes(record: dict) -> list[tuple[str, str]]:
    claims = (record.get("data") or {}).get("claims") or []
    return [(str(c.get("artifact_id", "")), str(c.get("quote", "")))
            for c in claims if isinstance(c, dict)]


def _name_tokens(name: str) -> list[str]:
    return [t for t in scene.fold(name).split() if len(t) >= 3]


#: Addresses at this many other domains on one page make it a directory of others.
DIRECTORY_DOMAINS = 3

_FREE_MAIL = frozenset({"gmail.com", "orange.fr", "free.fr", "wanadoo.fr", "sfr.fr", "yahoo.fr",
                        "yahoo.com", "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr",
                        "laposte.net", "neuf.fr", "live.fr", "icloud.com", "aol.com", "bbox.fr",
                        "numericable.fr", "btinternet.com", "gmx.de", "web.de", "t-online.de"})


def _registrable(host: str) -> str:
    """example.fr for www.sport.example.fr; a free-mail domain stays itself."""
    from urllib.parse import urlsplit
    host = (urlsplit(host).hostname if "://" in host else host).casefold().strip(".") if host else ""
    if host in _FREE_MAIL:
        return host
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "gov", "ac", "net"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _personal_address(email: str, name: str) -> bool:
    """Whether the address is this person's own (prenom.nom@, pnom@, nom@), not a team's.

    "Directeur des sports : Jean Martin - sports@ville.fr" names a person next to the
    department inbox; that route is the department's (C), not Jean Martin's.
    """
    local = scene.fold(email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " "))
    joined = local.replace(" ", "")
    tokens = [t for t in scene.fold(name).split() if len(t) >= 2]
    if not tokens or not joined:
        return False
    if any(len(t) >= 3 and t in joined for t in tokens):
        return True
    surname = tokens[-1]
    return len(tokens) > 1 and joined in {tokens[0][0] + surname, surname + tokens[0][0]}


#: How close (in characters) a role label must stand to an address for the address
#: to count as that role's, when no name is printed ("Président : x@...").
ROLE_LABEL_DISTANCE = 20

_ROLE_WORDS = frozenset("""president presidente presidence directeur directrice direction
director manager gerant gerante responsable secretaire secretary chef head coordinateur
coordinatrice regisseur administrateur administratrice""".split())


def _role_mailbox(email: str, title_words: str) -> bool:
    """president@, directeur.sports@, manager@: an address named after a role, not a service."""
    local = set(scene.fold(email.split("@", 1)[0].replace(".", " ").replace("_", " ")
                           .replace("-", " ")).split())
    return bool(local & _ROLE_WORDS) or bool(local and local & set(title_words.split()) & _ROLE_WORDS)


def _name_in_quote(email: str, quotes: list[tuple[str, str]]) -> str:
    """The person an address belongs to, when the quote that prints it also prints the
    name its local part spells ("Jean Dupont, directeur ... jean.dupont@club.fr").

    Both name parts must appear capitalised in that same quote; nothing is inferred
    from the address alone.
    """
    import re
    parts = [scene.fold(p).replace(" ", "") for p in re.split(r"[._-]+", email.split("@", 1)[0])]
    parts = [p for p in parts if len(p) >= 2 and not p.isdigit()]
    if len(parts) < 2:
        return ""
    for _, quote in quotes:
        if email.casefold() not in quote.casefold():
            continue
        # Capitalised words, hyphenated names split ("Marie-Florence"), and pairs of
        # neighbours joined ("Muller Lardeau" spells "mullerlardeau").
        words = [w for token in re.findall(r"[A-ZÀ-ÖØ-Þ][\w'À-ÿ-]+", quote)
                 for w in token.split("-") if w]
        keys = [scene.fold(w).replace(" ", "") for w in words]
        candidates = {k: w for k, w in zip(keys, words)}
        candidates.update({a + b: f"{wa} {wb}" for (a, wa), (b, wb)
                           in zip(zip(keys, words), zip(keys[1:], words[1:]))})
        found = [candidates.get(part) for part in parts]
        if all(found) and not any(set(scene.fold(w).split()) & _NOT_A_NAME for w in found):
            return " ".join(found)
    return ""


#: Words that make "site.manager@" or "business@" look like a person's name when
#: the quote prints "Site Manager" or "Business": a role or an organisation word
#: is never a name.
_NOT_A_NAME = _ROLE_WORDS | frozenset("""site premises business office school academy college
trust council club team department services service facilities estates grounds bursar finance
manager officer assistant deputy reception admissions lettings bookings head teacher principal
primary secondary infant junior nursery community sports green parish town city""".split())


def grade_routes(records: list[dict], brief: dict | None) -> tuple[dict, dict]:
    """({record_id: {grade, reasons, person_key, organization_id}}, rules) for email routes."""
    rules = scene.load(brief)
    by_kind = defaultdict(list)
    for record in records:
        by_kind[record["kind"]].append(record)
    people = {r["entity_key"]: r for r in by_kind["person"]}
    roles_by_person = defaultdict(list)
    for role in by_kind["role"]:
        person_id = _text(role.get("data") or {}, "person_id")
        if person_id:
            roles_by_person[person_id].append(role)

    contacts = [r for r in by_kind["contact_point"]
                if str((r.get("data") or {}).get("type", (r.get("data") or {}).get("channel", "")))
                .strip().lower() == "email"]

    def person_of(contact: dict) -> tuple[str, str, dict | None]:
        data = contact.get("data") or {}
        person_id = _text(data, "person_id")
        person = people.get(person_id)
        name = _text(data, "person_name", "name") or (
            _text(person.get("data") or {}, "name", "person_name") if person else "")
        return person_id, name, person

    names_by_email = defaultdict(set)
    for contact in contacts:
        value = str((contact.get("data") or {}).get("value", "")).strip().casefold()
        person_id, name, _ = person_of(contact)
        if person_id or name:
            names_by_email[value].add(person_id or scene.fold(name))

    # A page that prints addresses at many other domains (a town's list of clubs, a
    # federation directory) lists other organisations' people: they are real
    # contacts, but not the site owner's.
    org_sites = {r["entity_key"]: _registrable(_text(r.get("data") or {}, "website"))
                 for r in by_kind["organization"]}
    foreign_by_page = defaultdict(set)
    for contact in contacts:
        data = contact.get("data") or {}
        domain = _registrable(str(data.get("value", "")).rsplit("@", 1)[-1])
        own = org_sites.get(_text(data, "organization_id"), "")
        if own and domain != own:
            for artifact in contact.get("artifact_ids") or ():
                foreign_by_page[artifact].add(domain)

    out = {}
    for contact in contacts:
        data = contact.get("data") or {}
        value = str(data.get("value", "")).strip()
        org = _text(data, "organization_id")
        person_id, name, person = person_of(contact)
        named_from_quote = False
        if not name:
            name = _name_in_quote(value, _quotes(contact))
            named_from_quote = bool(name)
        role_records = roles_by_person.get(person_id, []) if person_id else []
        title = _text(data, *_TITLE_FIELDS) or next(
            (_text(r.get("data") or {}, *_TITLE_FIELDS) for r in role_records), "") or (
            _text(person.get("data") or {}, *_TITLE_FIELDS) if person else "")
        key = person_id or (f"{org}:{scene.fold(name)}" if name else value.casefold())
        result = {"grade": "", "reasons": ["NAME_FROM_EMAIL_QUOTE"] if named_from_quote else [],
                  "person_key": key, "organization_id": org, "role_class": "",
                  "person_name": name, "role_title": title}
        own = org_sites.get(org, "")
        domain = _registrable(value.rsplit("@", 1)[-1])
        if own and domain != own and any(len(foreign_by_page[a]) >= DIRECTORY_DOMAINS
                                         for a in contact.get("artifact_ids") or ()):
            result["reasons"].append("LISTED_ON_ANOTHER_ORGANISATIONS_PAGE")
            result["organization_id"] = ""
        out[contact["record_id"]] = result
        if not rules["roles"]:
            result["grade"], result["reasons"] = "RULES_UNKNOWN", ["NO_SCENE_RULES"]
            continue
        quotes = _quotes(contact)
        folded = [(artifact, scene.fold(quote), quote.casefold()) for artifact, quote in quotes]
        with_email = [(a, f) for a, f, raw in folded if value.casefold() in raw]
        distance = -1
        if title:
            cls, title_words = scene.match(title, rules)
            title_words = title_words or scene.fold(title)
        else:
            # No role record: the role printed right next to the address on the saved
            # page ("Head Groundsman: ...") is evidence too, but only that one - not a
            # role further along the sentence, and not a street named after a president.
            cls, title_words, distance = "", "", -1
            for _, quote in quotes:
                found = scene.nearest_title(quote, value, rules)
                if found[0] and (distance < 0 or found[2] < distance):
                    cls, title_words, distance = found
            if cls:
                result["reasons"].append("ROLE_FROM_EMAIL_QUOTE")
            elif _role_mailbox(value, ""):
                # president@club.fr names its role itself.
                local = value.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ")
                cls, title_words = scene.match(local, rules, ("decision", "operational"))
                if cls:
                    result["reasons"].append("ROLE_FROM_MAILBOX_NAME")
        result["role_class"] = cls
        result["role_title"] = result["role_title"] or title_words
        if cls == "excluded":
            result["grade"], result["reasons"] = "X", ["ROLE_EXCLUDED"]
            continue
        if scene.is_generic_inbox(value):
            result["grade"], result["reasons"] = "D", ["GENERIC_INBOX"]
            continue
        if len(names_by_email[value.casefold()]) > 1:
            result["grade"], result["reasons"] = "D", ["SHARED_INBOX_SEVERAL_PEOPLE"]
            continue
        if any(raw.count(value.casefold()) > 1 for _, _, raw in folded):
            # One address printed for several roles on the same page (president and
            # vice-president both "club@...") is the club's inbox.
            result["grade"] = "C"
            result["reasons"] = result["reasons"] + ["SAME_ADDRESS_FOR_SEVERAL_ROLES"]
            continue
        if cls not in {"decision", "operational"}:
            result["grade"] = "C"
            result["reasons"] += ["ENTRY_ROLE"] if cls == "entry" else (
                ["ROLE_NOT_IN_SCENE"] if title else ["NO_ROLE"])
            continue
        if name:
            tokens = _name_tokens(name)
            # A quote of the person's own record that prints the address counts too:
            # it is one block of one saved page either way.
            own = [(a, scene.fold(q)) for r in role_records + ([person] if person else [])
                   for a, q in _quotes(r) if value.casefold() in q.casefold()]
            named_block = any(any(t in f.split() for t in tokens) for _, f in with_email + own)
            role_artifacts = {a for r in role_records + ([person] if person else [])
                              for a, _ in _quotes(r)}
            role_same_page = bool(title_words) and (
                any(scene._matches(f, title_words) for _, f in with_email)
                or bool(role_artifacts & {a for a, _ in with_email}))
            role_box = _role_mailbox(value, title_words)
            if named_block and role_same_page and not _personal_address(value, name) and role_box:
                # president@club.fr next to "Président : Jean Dupont": the role's own
                # address, which reaches that person; a role-bound email is B.
                result["grade"] = "B"
                result["reasons"].append("ROLE_MAILBOX_OF_NAMED_PERSON")
            elif named_block and role_same_page and not _personal_address(value, name):
                result["grade"] = "C"
                result["reasons"].append("DEPARTMENT_INBOX_NEXT_TO_NAME")
            elif named_block and role_same_page:
                result["grade"] = "A" if cls == "decision" else "B"
                result["reasons"].append("NAME_ROLE_EMAIL_ONE_BLOCK")
            else:
                result["grade"] = "C"
                result["reasons"].append("NAME_ROLE_EMAIL_NOT_ONE_BLOCK")
        else:
            bound = title_words and any(scene._matches(f, title_words) for _, f in with_email)
            if bound and (_role_mailbox(value, title_words) or 0 <= distance <= ROLE_LABEL_DISTANCE):
                # "Président : president@club.fr" or a role's own mailbox: role-bound.
                result["grade"] = "B"
                result["reasons"] += ["ROLE_BOUND_NO_NAME"] + (
                    ["NO_NAME_MAX_B"] if cls == "decision" else [])
            elif bound:
                # A role mentioned nearby, but the address is the organisation's
                # own inbox (club-name@gmail.com): an entry point, not the role.
                result["grade"] = "C"
                result["reasons"].append("ROLE_NEAR_ORGANISATION_INBOX")
            else:
                result["grade"] = "C"
                result["reasons"].append("ROLE_NOT_IN_EMAIL_BLOCK")
    return out, rules


def named_leads(records: list[dict], grades: dict, rules: dict) -> list[dict]:
    """Named people in a wanted role (decision or operational) with no A/B email route.

    A named person without an email is still a lead: the team can reach them through
    the organisation's entry route, by name.
    """
    if not rules.get("roles"):
        return []
    with_ab = {g["person_key"] for g in grades.values() if g["grade"] in {"A", "B"}}
    leads, seen = [], set()
    for record in records:
        if record["kind"] not in {"person", "role"}:
            continue
        data = record.get("data") or {}
        name = _text(data, "name", "person_name")
        title = _text(data, *_TITLE_FIELDS)
        cls = scene.role_class(title, rules)
        org = _text(data, "organization_id")
        key = _text(data, "person_id") or record["entity_key"]
        if not name or cls not in {"decision", "operational"} or key in with_ab:
            continue
        if (org, scene.fold(name)) in seen:
            continue
        seen.add((org, scene.fold(name)))
        quote = next((q for _, q in _quotes(record)), "")
        leads.append({"record_id": record["record_id"], "organization_id": org, "name": name,
                      "role": title, "role_class": cls, "quote": quote,
                      "artifact_id": next(iter(record.get("artifact_ids") or []), "")})
    return leads


def summary(grades: dict, *, targets_finished: int | None = None,
            organizations: int | None = None) -> dict:
    """Counts per grade, distinct A+B people, and A+B per 100 targets with its unit."""
    counts = Counter(g["grade"] for g in grades.values() if g["grade"])
    ab = {g["person_key"] for g in grades.values() if g["grade"] in {"A", "B"}}
    ab_orgs = {g["organization_id"] for g in grades.values()
               if g["grade"] in {"A", "B"} and g["organization_id"]}
    reasons = Counter(r for g in grades.values() for r in g["reasons"])
    denominator, unit = (targets_finished, "finished pool targets") if targets_finished else (
        (organizations, "organisations delivered") if organizations else (None, ""))
    return {
        "unit": "email route; A+B counted as distinct people",
        "grades": {k: counts.get(k, 0) for k in ("A", "B", "C", "D", "X", "RULES_UNKNOWN")},
        "ab_people": len(ab),
        "organisations_with_ab": len(ab_orgs),
        "ab_per_100": round(100 * len(ab) / denominator, 1) if denominator else None,
        "per_100_of": unit or "UNKNOWN_NO_DENOMINATOR",
        "denominator": denominator,
        "reasons": dict(sorted(reasons.items())),
    }

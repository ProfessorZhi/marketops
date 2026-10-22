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
        words = re.findall(r"[A-ZÀ-ÖØ-Þ][\w'À-ÿ-]+", quote)
        found = []
        for part in parts:
            match = next((w for w in words if scene.fold(w).replace(" ", "") == part), None)
            if match is None:
                break
            found.append(match)
        else:
            return " ".join(found)
    return ""


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
                  "person_name": name}
        out[contact["record_id"]] = result
        if not rules["roles"]:
            result["grade"], result["reasons"] = "RULES_UNKNOWN", ["NO_SCENE_RULES"]
            continue
        quotes = _quotes(contact)
        folded = [(artifact, scene.fold(quote), quote.casefold()) for artifact, quote in quotes]
        with_email = [(a, f) for a, f, raw in folded if value.casefold() in raw]
        if title:
            cls, title_words = scene.match(title, rules)
            title_words = title_words or scene.fold(title)
        else:
            # No role record: the role words printed next to the address on the saved
            # page ("Head Groundsman: ...") are evidence too. A department word that
            # only appears elsewhere in the sentence does not exclude the route.
            cls, title_words = next(((c, w) for c, w in (
                scene.match(f, rules, ("decision", "operational", "entry")) for _, f in with_email)
                if c), ("", ""))
            if cls:
                result["reasons"].append("ROLE_FROM_EMAIL_QUOTE")
        result["role_class"] = cls
        if cls == "excluded":
            result["grade"], result["reasons"] = "X", ["ROLE_EXCLUDED"]
            continue
        if scene.is_generic_inbox(value):
            result["grade"], result["reasons"] = "D", ["GENERIC_INBOX"]
            continue
        if len(names_by_email[value.casefold()]) > 1:
            result["grade"], result["reasons"] = "D", ["SHARED_INBOX_SEVERAL_PEOPLE"]
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
            if title_words and any(scene._matches(f, title_words) for _, f in with_email):
                result["grade"] = "B"
                result["reasons"] += ["ROLE_BOUND_NO_NAME"] + (
                    ["NO_NAME_MAX_B"] if cls == "decision" else [])
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

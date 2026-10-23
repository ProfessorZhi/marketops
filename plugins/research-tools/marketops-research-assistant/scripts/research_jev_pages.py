#!/usr/bin/env python3
"""Jev on harvested pages: the small repeated questions code cannot settle alone.

Code reads the page first and does everything it can: routes, names spelled by
the address, roles next to the address, the grade. Jev is asked only where code
finds several literal candidates and cannot tell which one is meant, and only to
pick among them:

* **directory split** - a town's or federation's list of clubs prints many
  organisations; which of the names printed before an address owns it
  (`research_directory`);
* **name fill** - "Président : Jean DUPONT ... contact.asm@orange.fr": the address
  does not spell the name, so code cannot bind them; which printed name, if any,
  the address belongs to;
* **screen** - one question on the target's own first page: is it in scope, a
  competitor, or another kind of body, so an out-of-scope site is not crawled
  further.

Every pick is checked against the page: the chosen name must occur literally in
the quote that is written, and that quote is the page's own text from the name
to the address. Jev never writes a fact, a role or a grade; the grading code reads
the quote as it always does.

``ask(artifact_id, locator, question, options) -> (choice, confidence)`` is the
only way out to Jev; it raises ``ValueError`` when Jev is unavailable, and every
caller here then keeps what code alone found.
"""
from __future__ import annotations

import re

import research_directory as directory

__all__ = ["PICK_CONFIDENCE", "SCREEN_CONFIDENCE", "name_candidates", "fill_names",
           "split_directory", "screen", "screen_question"]

#: A pick below this confidence is not used; code's own reading stands.
PICK_CONFIDENCE = 0.8
#: Screening stops a crawl, so it needs a surer answer than a pick.
SCREEN_CONFIDENCE = 0.9
MAX_CANDIDATES = 6
_BEFORE, _AFTER = 160, 60

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UPPER = "A-ZÀ-ÖØ-Þ"
#: "Jean Dupont", "Jean DUPONT", "Marie-Claire Le Goff", "J. Dupont".
_PERSON = re.compile(
    rf"\b(?:[{_UPPER}][a-zà-ÿ'’]+(?:-[{_UPPER}][a-zà-ÿ'’]+)?|[{_UPPER}]\.)"
    rf"(?:\s+(?:de|du|le|la|van|von|der|di|da)?\s*[{_UPPER}][{_UPPER}a-zà-ÿ'’-]+){{1,2}}\b")
#: Words that make a capitalised pair a place, a body or a heading, not a person.
_NOT_PERSON = re.compile(
    r"\b(rue|avenue|boulevard|bd|place|chemin|allée|allee|impasse|quai|route|street|road|"
    r"lane|stade|gymnase|parc|salle|centre|center|complexe|mairie|ville|club|association|"
    r"comité|comite|ligue|fédération|federation|section|union|bureau|président|presidente|"
    r"présidente|president|secrétaire|secretaire|trésorier|tresorier|contact|email|e-mail|"
    r"téléphone|telephone|tél|tel|site|web|paris|france|saint|sainte|lundi|mardi|mercredi|"
    r"jeudi|vendredi|samedi|dimanche|janvier|février|mars|avril|mai|juin|juillet|août|"
    r"septembre|octobre|novembre|décembre)\b", re.IGNORECASE)


def _ask(ask, artifact_id, locator, question, options):
    try:
        choice, confidence = ask(artifact_id, locator[:60], question, options)
    except ValueError:
        return None, None
    return choice, confidence


def name_candidates(text: str, email: str) -> list[dict]:
    """Person-shaped names printed near ``email``, since the previous address."""
    at = text.find(email)
    if at < 0:
        return []
    left_start = max(0, at - _BEFORE)
    previous = list(_EMAIL.finditer(text, left_start, at))
    if previous:
        left_start = previous[-1].end()
    right_end = min(len(text), at + len(email) + _AFTER)
    following = _EMAIL.search(text, at + len(email), right_end)
    if following:
        right_end = following.start()
    found, seen = [], set()
    for match in _PERSON.finditer(text, left_start, right_end):
        if match.start() <= at < match.end():
            continue
        name = " ".join(match.group(0).split())
        if _NOT_PERSON.search(name) or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        found.append({"name": name, "start": match.start(), "end": match.end()})
    return found[-MAX_CANDIDATES:]


def _span_quote(text: str, start: int, end: int) -> str:
    quote = " ".join(text[start:end].split())
    return quote if 12 <= len(quote) <= directory.MAX_QUOTE else ""


def fill_names(text: str, routes: list[dict], artifact_id: str, ask, *, has_name) -> int:
    """Bind a printed name to each email route code left nameless; returns how many.

    ``has_name(email, quote)`` is code's own reading; a route it already names is
    never sent to Jev. A filled route carries ``person_name`` and a quote that
    spans the name and the address on this page.
    """
    filled = 0
    for route in routes:
        if route.get("type") != "email" or route.get("person_name"):
            continue
        email = route["value"]
        if has_name(email, route["quote"]):
            continue
        names = name_candidates(text, email)
        if not names:
            continue
        options = [f"name_{index + 1}" for index in range(len(names))] + ["none_of_these"]
        question = (f"Whose email address is {email}? Pick the person it belongs to, or "
                    "none_of_these if it is an organisation or office inbox. "
                    + "; ".join(f"name_{i + 1} = {c['name']}" for i, c in enumerate(names)))
        choice, confidence = _ask(ask, artifact_id, email, question[:480], options)
        route["jev_name_check"] = choice or "unavailable"
        if not choice or not choice.startswith("name_") or (confidence or 0) < PICK_CONFIDENCE:
            continue
        pick = names[int(choice.split("_")[1]) - 1]
        at = text.find(email)
        start, end = min(pick["start"], at), max(pick["end"], at + len(email))
        held = text.find(route["quote"])
        if held >= 0:
            # Keep what the quote already proves (a club's name from its list entry).
            start, end = min(start, held), max(end, held + len(route["quote"]))
        quote = _span_quote(text, start, end)
        if not quote or pick["name"] not in quote or email not in quote:
            continue
        route.update(person_name=pick["name"], quote=quote, name_by="JEV_PICK_LITERAL",
                     jev_name_confidence=confidence)
        filled += 1
    return filled


def split_directory(text: str, site_url: str, routes: list[dict], artifact_id: str,
                    ask) -> list[dict]:
    """Assign each address on a directory page to the organisation printed with it."""
    emails = [r["value"] for r in routes if r.get("type") == "email"]
    if not directory.is_directory(site_url, emails):
        return []

    def choose(email, names):
        options = [f"org_{index + 1}" for index in range(len(names))] + ["none_of_these"]
        question = (f"Which organisation does the address {email} belong to on this list? "
                    + "; ".join(f"org_{i + 1} = {n}" for i, n in enumerate(names)))
        choice, confidence = _ask(ask, artifact_id, email, question[:480], options)
        if choice and choice.startswith("org_") and (confidence or 0) >= PICK_CONFIDENCE:
            return int(choice.split("_")[1]) - 1
        return None
    return directory.split(text, routes, choose=choose)


def screen_listed(text: str, splits: list[dict], artifact_id: str, question: str, ask) -> None:
    """A town's guide lists every association; drop the clubs Jev is sure are out of
    scope (a history society in a sports-venue Run) before they are staged."""
    if not question:
        return
    for split in splits:
        email = split["route"]["value"]
        q = (question.replace("Is the organisation whose own website this is a target?", "")
             + f" Is {split['organization']['name']} (address {email}) a target?")[:480]
        choice, confidence = _ask(ask, artifact_id, email, q, SCREEN_OPTIONS)
        split["screen"] = choice or "unavailable"
        split["out"] = (choice in {"competitor", "out_of_scope"}
                        and (confidence or 0) >= SCREEN_CONFIDENCE)


def screen_question(brief: dict, objective: str) -> str:
    """The one screening question, from the brief, or "" when it says too little."""
    scope = str(brief.get("screen_in_scope") or objective or "").strip()
    if len(scope) < 10:
        return ""
    parts = [f"Research target: {scope[:220]}."]
    if brief.get("screen_competitors"):
        parts.append(f"Competitors (not customers): {str(brief['screen_competitors'])[:120]}.")
    if brief.get("screen_out_of_scope"):
        parts.append(f"Out of scope: {str(brief['screen_out_of_scope'])[:120]}.")
    parts.append("Is the organisation whose own website this is a target?")
    return " ".join(parts)[:480]


SCREEN_OPTIONS = ["target", "competitor", "out_of_scope", "unknown"]


def screen(artifact_id: str, question: str, ask) -> dict:
    """{'choice', 'confidence', 'stop'}: stop only on a sure competitor/out-of-scope."""
    if not question:
        return {"choice": None, "confidence": None, "stop": False}
    choice, confidence = _ask(ask, artifact_id, "", question, SCREEN_OPTIONS)
    stop = choice in {"competitor", "out_of_scope"} and (confidence or 0) >= SCREEN_CONFIDENCE
    return {"choice": choice or "unavailable", "confidence": confidence, "stop": stop}

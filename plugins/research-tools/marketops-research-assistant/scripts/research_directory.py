#!/usr/bin/env python3
"""Split a directory page into the organisations it lists.

A town's list of clubs, a federation's club finder or a league page prints many
organisations, each with its officer and address. Harvested as the town's own
site, those addresses were credited to the town (wrong) or left with no
organisation (lost). Here each address on such a page gets its own organisation:

1. code collects the organisation names printed just before the address (since the
   previous address on the page): short segments that read like a club or body;
2. one candidate that names a kind of body is taken as is; several candidates go to
   Jev, which only picks among them (a choice, never free text);
3. the pick is checked literally against the page, and the quote written for the
   organisation and its contact is the page's own text from the name to the
   address, so the evidence gate and the grading see name, person, role and
   address together.

Nothing is inferred: an address with no organisation name printed before it stays
with the site it was found on.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

__all__ = ["is_directory", "candidates", "split"]

#: Addresses at this many other domains on one page make it a list of others.
MIN_OTHER_DOMAINS = 3
MAX_QUOTE = 600
#: Up to what one quote can carry (the gate takes 600 characters), so the club
#: name and the address it owns fit in the same quote.
WINDOW = 560

#: Words that say a segment names a club or body, in the languages met so far.
_BODY = re.compile(
    r"\b(club|association|asso|team|union|entente|cercle|amicale|olympique|racing|"
    r"academie|académie|ecole|école|section|ligue|comite|comité|federation|fédération|"
    r"societe|société|sporting|athletic|ac|as|us|cs|es|sc|fc|rc|asc|usm|jsc|fsgt|ufolep|"
    r"verein|tsv|sv|vfb|vereniging|sportclub|council|trust|academy|school|college|"
    r"university|université|universite|lycée|lycee|collège|college)\b", re.IGNORECASE)
_FREE_MAIL = {"gmail.com", "orange.fr", "free.fr", "wanadoo.fr", "sfr.fr", "yahoo.fr", "yahoo.com",
              "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "laposte.net", "neuf.fr",
              "live.fr", "icloud.com", "aol.com", "bbox.fr", "numericable.fr", "btinternet.com"}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
#: A run of text between list separators: line breaks, bullets, bars, colons,
#: commas, brackets, and a dash with spaces around it ("Club X - Président").
_SEGMENT = re.compile(r"(?:(?!\s[-–—]\s)[^\n•|·;:,()\[\]])+")
#: Capitalised words, joined by the small words names keep ("Union Sportive du
#: Gros-Caillou", "Club of the Year"); digits count ("Paris 7", "1er").
_TOKEN = r"[A-ZÀ-ÖØ-Þ0-9][\w'’&-]*(?:\.[\w'’&-]+)*"
_RUN = re.compile(_TOKEN + r"(?:\s+(?:(?:du|de|des|la|le|les|d'|of|the|and|et|&|sur|en|aux?)\s+)*"
                  + _TOKEN + r")*")
#: A bare kind of body ("Comité", "L'association", "Société") names nobody.
_GENERIC = re.compile(r"^(?:l['’]\s*)?(?:the\s+)?(?:club|association|asso|société|societe|comité|comite|"
                      r"section|union|ligue|amicale|cercle|team|federation|fédération)s?$", re.IGNORECASE)


#: The label that follows a name on a list page is a capitalised word too, so the
#: run glued it on: "Association Tsunagari Discipline(s) pratiquée(s) :" became an
#: organisation named "Association Tsunagari Discipline". Only the *tail* is
#: trimmed, and only when another word precedes it.
_LABEL_TAIL = frozenset({
    "discipline", "disciplines", "équipement", "equipement", "équipements", "equipements",
    "nom", "adresse", "adresses", "téléphone", "telephone", "mail", "site", "contact",
    "correspondant", "lieu", "tél", "tel", "activité", "activites", "activités"})


def _clean(name: str) -> str:
    """Trim what a rendered list glues onto a name: page numbers, web addresses,
    and the first word of the description after an all-capitals name."""
    words = name.split()
    while words and (re.fullmatch(r"(?i)page|p\.?|\d+", words[0]) or "." in words[0].strip(".")):
        words = words[1:]
    caps = 0
    while caps < len(words) and len(re.sub(r"\W", "", words[caps])) >= 2 and words[caps].isupper():
        caps += 1
    if 2 <= caps < len(words) and _BODY.search(" ".join(words[:caps])):
        words = words[:caps]  # "ASSOCIATION DE DANSE IRITCHA Cours de danse" -> the capitals
    while len(words) > 1 and re.sub(r"\W", "", words[-1]).casefold() in _LABEL_TAIL:
        words = words[:-1]  # "Association Tsunagari Discipline(s)" -> the name
    return " ".join(words)


def _registrable(host: str) -> str:
    host = (host or "").casefold().strip(".").removeprefix("www.")
    if host in _FREE_MAIL:
        return host
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "gov", "ac", "net"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_directory(site_url: str, emails: list[str]) -> bool:
    """Whether a page lists other organisations' addresses (three or more other domains)."""
    own = _registrable(urlsplit(site_url).hostname or "")
    others = {_registrable(e.rsplit("@", 1)[-1]) for e in emails} - {own}
    return len(others) >= MIN_OTHER_DOMAINS


def candidates(text: str, email: str) -> list[dict]:
    """Organisation names printed before ``email`` since the previous address."""
    at = text.find(email)
    if at < 0:
        return []
    left = text[max(0, at - WINDOW):at]
    previous = list(_EMAIL.finditer(left))
    if previous:
        left = left[previous[-1].end():]
    start = at - len(left)
    found, seen = [], set()
    for segment in _SEGMENT.finditer(left):
        # Pages often lose their line breaks when rendered ("... du 7e arrondissement
        # Badminton Club Paris 7 - Président"), so a candidate is a run of
        # capitalised words inside a segment, and it must name a kind of body:
        # a person's name printed next to the address is never an organisation.
        for run in _RUN.finditer(segment.group(0)):
            name = _clean(run.group(0).strip(" -–—,."))
            words = name.split()
            if (not 1 <= len(words) <= 9 or len(name) < 3 or not _BODY.search(name)
                    or _GENERIC.match(name)):
                continue
            if name.casefold() in seen:
                continue
            seen.add(name.casefold())
            offset = start + segment.start() + run.start() + run.group(0).find(name)
            found.append({"name": name, "start": offset, "body_word": True})
    return found


def _slug(name: str) -> str:
    return "org-dir-" + hashlib.sha256(name.casefold().encode()).hexdigest()[:16]


def split(text: str, routes: list[dict], *, choose=None) -> list[dict]:
    """For each email route on a directory page: {route, organization, quote} or nothing.

    ``choose(email, names)`` returns the index of the organisation the address
    belongs to, or None; it is asked only when more than one candidate names a body.
    """
    out = []
    for route in routes:
        if route.get("type") != "email":
            continue
        email = route["value"]
        found = candidates(text, email)
        bodies = found
        pick = None
        if len(bodies) == 1:
            pick = bodies[0]
        elif len(bodies) > 1 and choose is not None:
            index = choose(email, [c["name"] for c in bodies[-6:]])
            if isinstance(index, int) and 0 <= index < len(bodies[-6:]):
                pick = bodies[-6:][index]
        if pick is None:
            continue
        end = text.find(email, pick["start"]) + len(email)
        quote = text[pick["start"]:end].strip()
        if not (12 <= len(quote) <= MAX_QUOTE) or pick["name"] not in quote or email not in quote:
            continue
        out.append({"route": route, "organization": {"name": pick["name"], "entity_key": _slug(pick["name"])},
                    "quote": quote})
    return out

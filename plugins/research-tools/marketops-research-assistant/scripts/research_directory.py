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
WINDOW = 320

#: Words that say a segment names a club or body, in the languages met so far.
_BODY = re.compile(
    r"\b(club|association|asso|team|union|entente|stade|cercle|amicale|olympique|racing|"
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
    for match in _SEGMENT.finditer(left):
        segment = match.group(0).strip(" -–—,.")
        words = segment.split()
        if not 1 <= len(words) <= 9 or len(segment) < 3:
            continue
        if not (_BODY.search(segment) or (len(words) >= 2 and sum(w[:1].isupper() for w in words) >= 2)):
            continue
        if re.search(r"\b(tél|tel|e-?mail|courriel|mail|contact|phone|adresse|site)\b", segment, re.I) \
                and not _BODY.search(segment):
            continue
        key = segment.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append({"name": segment, "start": start + match.start() + (len(match.group(0))
                                                                     - len(match.group(0).lstrip(" -–—,."))),
                      "body_word": bool(_BODY.search(segment))})
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
        bodies = [c for c in found if c["body_word"]] or found
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

#!/usr/bin/env python3
"""Scene rules: which role titles are A, B, C or excluded in this market.

A scene is small on purpose: a few lists of title words per class, in the
market's language. A market the plugin has never seen (Paris indoor venues,
say) needs no shipped pack: the Agent writes `scene_rules` into the brief from
the user's roles, in the local language, and the code grades with them. A
shipped pack (references/scenes/<id>.json) only saves that drafting step, and
the brief's own rules always win over it.

Classes:
  decision     owns the decision for the product (A with a named, bound email)
  operational  runs the site or team day to day (B)
  entry        a business entry point: bookings, facilities team, venue hire (C)
  excluded     never a contact for this product (X)
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

__all__ = ["CLASSES", "GENERIC_LOCAL_PARTS", "fold", "validate", "load", "role_class", "match",
           "is_generic_inbox", "shipped"]

CLASSES = ("decision", "operational", "entry", "excluded")
MAX_TITLES = 60
SCENES_DIR = Path(__file__).resolve().parents[1] / "references" / "scenes"

#: The local part of an organisation-wide inbox, in the languages the plugin has
#: met. Such an inbox is a D route whatever name is printed next to it: three
#: different people "at" one info@ address are one switchboard, not three routes.
GENERIC_LOCAL_PARTS = frozenset("""
info infos information informations contact contacts contactus enquiries enquiry inquiries
inquiry office hello hi admin administration mail email post reception accueil secretariat
secretariaat secretaria segreteria sekretariat secretary general generale mairie gemeinde
rathaus ayuntamiento comune kontakt kantoor bureau team welcome service services
customerservice support help webmaster noreply no-reply
""".split())


def fold(text: object) -> str:
    """Lower case, no accents, single spaces: 'Directrice des Équipements' -> 'directrice des equipements'."""
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[0-9a-z]+", value.casefold()))


def validate(rules: object) -> list[str]:
    """Problems with a scene_rules mapping; empty when it is usable."""
    if not isinstance(rules, dict):
        return ["scene_rules must be an object {decision:[...], operational:[...], entry:[...], excluded:[...]}"]
    problems = [f"scene_rules.{key} is not a class; use {', '.join(CLASSES)}"
                for key in rules if key not in CLASSES]
    for key in CLASSES:
        titles = rules.get(key, [])
        if not isinstance(titles, list) or not all(isinstance(t, str) and fold(t) for t in titles):
            problems.append(f"scene_rules.{key} must be a list of role title words")
        elif len(titles) > MAX_TITLES:
            problems.append(f"scene_rules.{key} has more than {MAX_TITLES} titles")
    if not problems and not rules.get("decision") and not rules.get("operational"):
        problems.append("scene_rules needs decision or operational titles")
    return problems


def shipped(scene_id: str) -> dict | None:
    """A shipped pack by id, or None."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", str(scene_id or "")):
        return None
    path = SCENES_DIR / f"{scene_id}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load(brief: dict | None) -> dict:
    """The rules this Run grades with, and where they came from.

    Order: the brief's scene_rules, else the shipped pack the brief names, else
    the brief's roles_wanted as decision titles. With none of these there are no
    rules, and routes stay RULES_UNKNOWN.
    """
    brief = brief or {}
    pack = shipped(brief.get("scene", "")) if brief.get("scene") else None
    rules = brief.get("scene_rules")
    if isinstance(rules, dict) and not validate(rules):
        base = (pack or {}).get("roles", {})
        merged = {key: list(rules.get(key) or base.get(key) or []) for key in CLASSES}
        return {"source": "brief.scene_rules" + (f"+{brief['scene']}" if pack else ""),
                "roles": merged, "scene": brief.get("scene", "")}
    if pack:
        return {"source": f"shipped:{brief['scene']}", "roles": pack["roles"],
                "scene": brief["scene"]}
    wanted = brief.get("roles_wanted")
    if isinstance(wanted, str):
        wanted = [part for part in re.split(r"[,;/]| or ", wanted) if part.strip()]
    if isinstance(wanted, list) and any(fold(w) for w in wanted):
        return {"source": "brief.roles_wanted", "scene": "",
                "roles": {"decision": [w for w in wanted if fold(w)], "operational": [],
                          "entry": [], "excluded": []}}
    return {"source": "", "scene": "", "roles": {}}


def _matches(title: str, words: str) -> bool:
    return bool(words) and re.search(rf"(?<![0-9a-z]){re.escape(words)}(?![0-9a-z])", title) is not None


def match(text: object, rules: dict, order=("excluded", "decision", "operational", "entry")) -> tuple[str, str]:
    """(class, the title words that matched) for some text; ('', '') when none do."""
    folded = fold(text)
    if not folded:
        return "", ""
    roles = rules.get("roles", {})
    for key in order:
        for words in roles.get(key, ()):
            if _matches(folded, fold(words)):
                return key, fold(words)
    return "", ""


def role_class(title: object, rules: dict) -> str:
    """'decision' | 'operational' | 'entry' | 'excluded' | '' for a role title.

    Excluded wins over everything, then the most senior class that matches.
    """
    return match(title, rules)[0]


def is_generic_inbox(email: object) -> bool:
    local = str(email or "").split("@", 1)[0].casefold()
    return local in GENERIC_LOCAL_PARTS or re.sub(r"[0-9._-]+$", "", local) in GENERIC_LOCAL_PARTS

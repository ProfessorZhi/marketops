#!/usr/bin/env python3
"""Turn one saved list page into organisation records, in a single local read.

An annuaire, a club finder or a committee page prints hundreds of small records:
a name, then "Contact :", "Adresse mail :", "Téléphone :". Reading it through
`marketops_material_excerpt` costs one call per 1,200 characters, so a 40 KB page
became thirty calls and an Agent that gave up and wrote its own scraper. This
module is that scraper, shipped inside the plugin:

1. it reads the page with the same decoder and tag-stripper the evidence gate
   uses, so every quote it emits is a substring of what the gate will see;
2. it finds the field labels that repeat once per record ("Adresse mail :"),
   takes the most reliable one as the record anchor and reads each block
   between two anchors;
3. the organisation name is the run of words printed immediately before the
   block's first label, with a street, a schedule or a label the page glued
   on refused rather than kept as part of the name;
4. it emits, per record, the fields plus exact quotes for name, person and
   email, ready for `marketops_propose_batch`.

Nothing is inferred: a record with no organisation name printed on the page is
kept with `organization: null` and reported as such, never named after the site.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

__all__ = ["FIELDS", "extract", "items_for_batch", "render", "main"]

#: The fields a directory block can carry. Order is the order they are emitted.
FIELDS = ("organization", "person", "role", "email", "phone", "website",
          "address", "discipline", "venue")

#: A field label printed before its value. Group order is alternation order:
#: "Adresse mail" must be tried as email before "Adresse" is tried as address.
#: Every label must be followed by a colon, which keeps the words out of prose.
_LABEL_GROUPS = (
    ("person", r"nom de la personne (?:à|a) contacter|personne (?:à|a) contacter|"
               r"personne de contact|contact person|name of (?:the )?contact|"
               r"ansprechpartner(?:in)?|correspondant(?:e)?|contact|responsable"),
    ("email", r"adresse (?:mail|e-?mail|électronique)|e-?mail|courriel|mél|mail"),
    ("phone", r"num[ée]ros? de t[ée]l[ée]phone|t[ée]l[ée]phone|t[ée]l\.?|phone|"
              r"telefon(?:nummer)?"),
    # "Adresse du site" is a website, not a postal address: the website group
    # precedes address in the alternation so it wins at the same position.
    ("website", r"adresse (?:du |de )?site|site (?:internet|web)|website|webseite|site"),
    ("address", r"adresse postale|adresse|address|anschrift"),
    ("role", r"fonction|qualité|poste|rôle|position|title"),
    ("discipline", r"disciplines? pratiquées?|discipline\(s\) pratiquée\(s\)|"
                   r"disciplines?|activités?"),
    ("venue", r"[ée]quipements?(?: sportifs?)? utilis[ée]s?|"
              r"[ée]quipement\(s\) (?:sportif\(s\) )?utilis[ée]\(s\)|"
              r"[ée]quipements? sportifs?|[ée]quipement|installations?|"
              r"lieux? (?:des? )?cours|lieu"),
)
_LABEL_RE = re.compile(
    r"(?<![\w])(?:" + "|".join("(?P<%s>%s)" % (field, pattern)
                               for field, pattern in _LABEL_GROUPS) + r")\s*[:：]",
    re.IGNORECASE)

#: Words that say a run names a body rather than a person, a venue or a value.
_ORG_WORD = re.compile(
    r"\b(club|association|asso|union|entente|cercle|amicale|olympique|racing|"
    r"académie|academie|école|ecole|section|ligue|comité|comite|"
    r"fédération|federation|société|societe|sporting|athletic|"
    r"team|verein|sportclub|academy|school|college|university|université|"
    r"municipal|mairie|office)\b", re.IGNORECASE)

#: Which label is trusted to appear exactly once per record, best first.
_ANCHOR_ORDER = ("email", "person", "phone")
_MAX_VALUE = 200
_MAX_QUOTE = 600
_NAME_ZONE = 220


#: Tags that end a printed block. A name never continues across one: the page
#: puts a club's name in its own heading or cell, not mid-sentence.
_BLOCK_TAG = re.compile(
    r"(?i)</?(?:p|div|li|ul|ol|tr|td|th|table|dl|dt|dd|br|h[1-6]|section|article|"
    r"header|footer|form|fieldset|blockquote)\b[^>]*>")


def page_text(material_kind: str, content: bytes) -> str:
    """The saved page as lines, one per printed block, decoded like the gate.

    Lines matter: they are where a name, a phone number and the next name sit in
    separate elements. Collapsing the page to one line let a heading run into the
    first club's name. Quotes still match the gate, which collapses whitespace
    before comparing.
    """
    from research_evidence import decode_material, readable_text
    raw = decode_material(content)
    if material_kind not in {"rendered_html", "provider_html", "raw_response"}:
        return " ".join(raw.split())
    lines = [readable_text(material_kind, piece)
             for piece in _BLOCK_TAG.split(raw)]
    return "\n".join(line for line in lines if line)


def _hits(text: str) -> list[dict]:
    return [{"field": match.lastgroup, "start": match.start(), "end": match.end()}
            for match in _LABEL_RE.finditer(text)]


def _anchor(hits: list[dict]) -> str | None:
    counts: dict[str, int] = {}
    for hit in hits:
        counts[hit["field"]] = counts.get(hit["field"], 0) + 1
    for field in _ANCHOR_ORDER:
        if counts.get(field, 0) >= 2:
            return field
    if not counts:
        return None
    field, count = max(counts.items(), key=lambda item: item[1])
    return field if count >= 2 else None


def _strip_suffix(gap: str) -> str:
    """Drop a short trailing parenthetical ("... (A.S.C. 13)") so the name before
    it is still read as the name; the abbreviation is not part of the title."""
    trimmed = gap.rstrip(" \u00a0-\u2013\u2014|,;:.")
    while trimmed.endswith(")") and len(trimmed) <= len(gap):
        depth, cut = 0, None
        for position in range(len(trimmed) - 1, -1, -1):
            character = trimmed[position]
            if character == ")":
                depth += 1
            elif character == "(":
                depth -= 1
                if depth == 0:
                    cut = position
                    break
        if cut is None or len(trimmed) - cut > 40:
            break
        trimmed = trimmed[:cut].rstrip(" \u00a0-\u2013\u2014|,;:.")
    return trimmed


#: Characters that end a value rather than continue a name: punctuation of an
#: address, a URL, an email, a parenthetical, a list separator, a quote mark.
_VALUE_CHARS = set("@./\\:;|()[]«»\"“”,–—")


def _name_like(token: str) -> bool:
    """Whether a whitespace token can belong to an organisation's printed name."""
    if not token or any(character in _VALUE_CHARS for character in token):
        return False
    letters = sum(1 for character in token if character.isalpha())
    # A lone letter is the "E" of "Maior E Deus" or the "à" of a name.
    return letters >= 1


def _name_tail(gap: str) -> tuple[str, int] | None:
    """The organisation name printed at the end of a gap, with its offset.

    A gap is the text between one field's value and the next label. When a new
    record begins, that gap ends with the record's organisation name: walk back
    from the end over name-like words and stop at the value before them (a phone
    number, an address, a URL). A value that merely contains a capitalised run
    does not qualify, because the run has to reach the end of the gap.
    """
    stripped = gap.rstrip()
    line = stripped.rsplit("\n", 1)[-1]
    # Offsets index the gap as it was given, so they come from the trimmed
    # text: trailing newlines must not shift the name to the right.
    offset = len(stripped) - len(line)
    trimmed = _strip_suffix(line)
    tokens = [(match.start(), match.group(0)) for match in re.finditer(r"\S+", trimmed)]
    collected, begin = [], len(trimmed)
    for index in range(len(tokens) - 1, -1, -1):
        position, token = tokens[index]
        if len(collected) >= 14:
            break
        if _name_like(token):
            collected.append(token)
            begin = position
            continue
        # A number belongs to a name only near its end ("Basket 13",
        # "Paris 13 Atlético"); inside "06 07 49 05 30" it is the value before it.
        if (token.isdigit() and index > 0 and len(collected) <= 2
                and _name_like(tokens[index - 1][1])):
            collected.append(token)
            begin = position
            continue
        break
    if not collected:
        return None
    name = trimmed[begin:].strip(" -\u2013\u2014,.:;")
    letters = re.sub(r"\W", "", name)
    words = name.split()
    if not 3 <= len(letters) <= 80 or not 1 <= len(words) <= 14:
        return None
    if not any(word[0].isupper() for word in words if word[:1].isalpha()):
        return None
    if len(words) < 2 and not (letters.isupper() or any(c.isdigit() for c in letters)):
        return None
    # An address or a schedule also ends in capitals and would otherwise be read
    # as the next record's name ("5, rue du Moulin Vert" -> "rue du Moulin Vert").
    if _ADDRESS_WORD.match(words[0]) or any(_CLOCK_WORD.match(word) for word in words):
        return None
    return name, offset + begin


#: A run of words can end in capitals and still be a street, not a club: pages
#: that print "Lieu des cours : ..." put the address where the name would sit.
_ADDRESS_WORD = re.compile(
    r"^(?:rue|avenue|av\.?|boulevard|bd|place|chemin|impasse|quai|all[ée]e|square|"
    r"route|cours|villa|passage|esplanade|mail|sentier|ruelle|voie|porte)$",
    re.IGNORECASE)
_CLOCK_WORD = re.compile(r"^\d{1,2}\s?h\s?\d{0,2}$", re.IGNORECASE)


def _line_of(text: str, position: int) -> int:
    return text.count("\n", 0, position)


def _record_starts(text: str, hits: list[dict], block_lines: bool = False) -> list[tuple]:
    """Every label that begins a record.

    ``(label_start, field, name, name_start, name_end, previous_field)``. A label
    begins a record when the text just before it ends with an organisation name.
    Labels whose preceding gap is far away (page chrome) are not considered.

    On a page whose printed blocks are separate lines, a name that sits on the
    same line as the previous label is that label's own value ("Équipement(s)
    utilisé(s) : Gymnase Caillaux", not a club called Gymnase Caillaux).
    """
    starts = []
    for index, hit in enumerate(hits):
        previous = hits[index - 1] if index else None
        previous_end = previous["end"] if previous else 0
        if hit["start"] - previous_end > 600:
            continue
        gap = text[previous_end:hit["start"]]
        named = _name_tail(gap)
        if named is None:
            continue
        if block_lines and previous is not None \
                and _line_of(text, previous["start"]) == _line_of(text, previous_end + named[1]):
            continue
        if previous is not None and previous["field"] in {"person", "role"} \
                and not gap[:named[1]].strip("  \u00a0-\u2013\u2014|,;:."):
            # The gap is that label's own value (a person's name), not the
            # organisation name printed before a new record.
            continue
        name, offset = named
        starts.append((hit["start"], hit["field"], name,
                       previous_end + offset, previous_end + offset + len(name),
                       previous["field"] if previous else ""))
    return starts


def _choose_start_field(starts: list[tuple]) -> str | None:
    """The field records start with.

    Counting alone is not enough: a discipline value ("Tennis de table") can look
    like a name before the next label, so the venue label would open a false
    record. A record's first label follows a field that ends a record (phone,
    website, email); a false start follows a field that itself opens records.
    """
    counts: dict[str, int] = {}
    for start in starts:
        counts[start[1]] = counts.get(start[1], 0) + 1
    candidates = {field for field, count in counts.items() if count >= 3}
    if not candidates:
        return None
    predecessors: dict[str, dict[str, int]] = {}
    for start in starts:
        if start[1] in candidates and start[5]:
            bucket = predecessors.setdefault(start[1], {})
            bucket[start[5]] = bucket.get(start[5], 0) + 1

    def rank(field: str) -> tuple:
        top = predecessors.get(field) or {}
        top_field = max(top, key=top.get) if top else ""
        clean = top_field not in candidates or top_field in {
            "email", "phone", "website", "person"}
        return clean, counts[field]

    return max(candidates, key=lambda field: (*rank(field), field))


def _records_for_starts(text: str, hits: list[dict], starts: list[tuple]) -> list[dict]:
    records = []
    for position, start in enumerate(starts):
        block_start, _field, name, name_start, name_end = start[:5]
        # The next record owns everything from its organisation name onwards,
        # including that name: stopping at its label would leave the name glued
        # to this block's last value ("adresse mail : x@y" + "Club suivant").
        block_end = (starts[position + 1][3] if position + 1 < len(starts)
                     else len(text))
        block_hits = [h for h in hits if block_start <= h["start"] < block_end]
        if not block_hits:
            continue
        record = {field: "" for field in FIELDS}
        record["organization"] = name
        spans = {"organization": (name_start, name_end)}
        for order, item in enumerate(block_hits):
            stop = block_hits[order + 1]["start"] if order + 1 < len(block_hits) else block_end
            raw = text[item["end"]:stop]
            if stop == block_end and position + 1 < len(starts):
                # The next record's name starts the tail; keep it out.
                tail = _name_tail(raw)
                if tail is not None:
                    raw = raw[:tail[1]]
            elif stop == block_end:
                # Last record of the page: navigation, footer and contact blocks
                # follow the final value. This is the last label on the page, so
                # anything after the first line with no label behind it is page
                # chrome, not this field's value.
                for match in re.finditer(r"\n", raw):
                    if _LABEL_RE.search(raw, match.end()) is None:
                        raw = raw[:match.start()]
                        break
            lead = len(raw) - len(raw.lstrip())
            value = raw.strip(" \t\r\n\u00a0-\u2013\u2014|,;")
            if len(value) > _MAX_VALUE:
                value = value[:_MAX_VALUE].rstrip()
            if not value or record.get(item["field"]):
                continue
            if item["field"] in ("email", "phone", "website"):
                # A printed contact is one line. Block-split text otherwise
                # glues the next printed line (a discipline tag, a heading) onto
                # the address, and the address is the value the gate grades.
                value = _single_line(item["field"], value)
                if not value:
                    continue
            record[item["field"]] = value
            # The quote spans the label and its value, so it holds the email or
            # the person's name the way the page prints them, side by side.
            spans[item["field"]] = (item["start"], item["end"] + lead + len(value))
        if not any(record[field] for field in ("person", "email", "phone", "website")):
            continue
        record["_spans"] = {field: [start, end] for field, (start, end) in spans.items()}
        record["_block"] = [block_start, block_end]
        records.append(record)
    return records


_EMAIL_TOKEN = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def _single_line(field: str, value: str) -> str:
    line = value.split("\n", 1)[0].strip()
    if field == "email" and " " in line:
        for token in line.split():
            if _EMAIL_TOKEN.fullmatch(token):
                return token
        return line.split()[0]
    if field == "phone":
        # A page that glues a number to the next field prints
        # "06 01 86 94 64aymeric@example.org www.club.fr" in one run: the
        # number ends where an address starts.
        kept = []
        for token in line.split():
            if "@" in token:
                digits = re.match(r"\d+", token.split("@", 1)[0])
                if digits:
                    kept.append(digits.group(0))
                break
            kept.append(token)
        return " ".join(kept)
    return line


def _dedupe(records: list[dict]) -> tuple[list[dict], int]:
    kept, seen, dropped = [], set(), 0
    for record in records:
        key = (record["organization"].casefold(), record["email"].casefold())
        if key in seen:
            dropped += 1
            continue
        seen.add(key)
        kept.append(record)
    return kept, dropped


def extract(text: str, *, labels: list[str] | None = None, max_records: int = 5000) -> dict:
    """All records printed on one page, with the quotes that support them.

    ``labels`` narrows the field set (e.g. ``["organization", "email"]``) without
    changing the parsing; the anchor still comes from the page itself.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("DIRECTORY_TEXT_EMPTY: the artifact has no readable text")
    hits = _hits(text)
    anchor = _anchor(hits)
    if anchor is None:
        return {"records": [], "anchor": None, "dropped_duplicates": 0,
                "warnings": ["DIRECTORY_NO_REPEATING_LABEL: no field label appears "
                             "twice with a colon, so this page is not a list of records"]}
    starts = _record_starts(text, hits, block_lines=text.count("\n") >= 3)
    start_field = _choose_start_field(starts)
    warnings = []
    if start_field is None:
        return {"records": [], "anchor": anchor, "dropped_duplicates": 0,
                "warnings": ["DIRECTORY_LAYOUT_NOT_RECOGNISED: labels repeat but no "
                             "organisation name is printed before them; this page is "
                             "not a record list (try the site's own export or a saved "
                             "sitemap page)"]}
    records = _records_for_starts(
        text, hits, [start for start in starts if start[1] == start_field])
    records, dropped = _dedupe(records)
    if labels:
        unknown = set(labels) - set(FIELDS)
        if unknown:
            raise ValueError("DIRECTORY_FIELD_UNKNOWN: " + ", ".join(sorted(unknown)))
        keep = set(labels) | {"_spans", "_block"}
        records = [{key: value for key, value in record.items() if key in keep}
                   for record in records]
    if len(records) > max_records:
        warnings.append("DIRECTORY_TRUNCATED: kept the first %d of %d records"
                        % (max_records, len(records)))
        records = records[:max_records]
    return {"records": records, "anchor": anchor, "start_field": start_field,
            "dropped_duplicates": dropped, "warnings": warnings}


def _span_quote(text: str, span: list[int] | None, *, pad: int = 40) -> str | None:
    """A gate-accepted quote around one field: 12–600 characters, exact substring."""
    if not span:
        return None
    start, end = span
    if end - start >= 12:
        left, right = start, min(end, start + _MAX_QUOTE)
    else:
        left, right = max(0, start - pad), min(len(text), end + pad)
    quote = text[left:right].strip()
    return quote if 12 <= len(quote) <= _MAX_QUOTE else None


def _key(prefix: str, *parts: str) -> str:
    return prefix + "-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _identity_quote(text: str, span: list[int] | None, name: str) -> str:
    """A quote that both contains the name and *supports* it as an identity.

    `field=name` alone is not enough: the delivery gate requires
    `field=identity` for an organisation, and 114 annuaire organisations staged
    with only a name quote were refused after the page had been read. The span
    quote is tried first; if the word that follows the name on the page turns
    the match into a longer legal entity ("... Club"), narrower windows on the
    same name are tried before giving up.
    """
    if not span:
        return ""
    import research_delivery as delivery

    start, end = span
    candidates = [_span_quote(text, span),
                  _span_quote(text, span, pad=12),
                  text[start:min(len(text), end + 12)].strip()]
    for quote in candidates:
        if quote and 12 <= len(quote) <= _MAX_QUOTE and \
                delivery._quote_supports(quote, name, distinctive=True):
            return quote
    return ""


def _block_quote(text: str, org_span: list[int] | None, span: list[int] | None,
                 name: str, value: str) -> str | None:
    """One contiguous quote carrying both the club's name and this record's value.

    A shared directory page lists a hundred clubs, so the delivery refuses a
    person or address that cites that page without proof it belongs to *this*
    club (ORGANIZATION_PROVENANCE_MISMATCH). The page prints the club, then the
    person, then the address as one block; that block is the proof.
    """
    if not org_span or not span:
        return None
    start, end = org_span[0], span[1]
    if not 0 <= start < end:
        return None
    quote = text[start:end].strip()
    if not 12 <= len(quote) <= _MAX_QUOTE:
        return None
    if name and name not in quote:
        return None
    if value and value not in quote:
        return None
    return quote


def records_to_bundles(text: str, records: list[dict], artifact_id: str,
                       stats: dict | None = None) -> list[list[dict]]:
    """Ready-to-stage candidates, grouped per record: organisation, person, address.

    Grading reads an address from a ``contact_point`` whose ``organization_id``
    names its organisation, so one flat organisation row would lose the email.
    Each bundle stays together and inside the store's 20-item batch bound.

    ``entity_key`` is the identity the gates and the grading join on, so it is
    derived from the printed name alone. ``record_id`` names *this reading of
    the page* and carries the artifact: re-reading a page from a fresh fetch
    after the extractor changes appends a version of the same entity instead of
    colliding with the row already stored (the store refuses to rewrite a row
    whose artifact differs, which is what RECORD_ID_COLLISION_OR_CROSS_RUN
    reported when a fixed extractor met its own earlier output).

    Within one page an identity is staged once. "Paris Université Club" prints
    twenty-two sections; twenty-two organisation rows with one ``entity_key``
    are twenty-two live roots of the same identity, and the delivery view drops
    such a group rather than choose between its readings — the whole
    organisation disappears from the customer sheet. The sections differ in
    their contacts, and those records carry the difference; the organisation row
    itself is the same organisation.
    """
    bundles = []
    staged: set[str] = set()

    def first_reading(entity_key: str) -> bool:
        if entity_key in staged:
            if stats is not None:
                stats["duplicate_entity_readings"] = stats.get("duplicate_entity_readings", 0) + 1
            return False
        staged.add(entity_key)
        return True

    for ordinal, record in enumerate(records):
        name = (record.get("organization") or "").strip()
        if not name:
            continue
        org_key = _key("org", name.casefold())
        # Two records can print the same name ("Paris Université Club" lists
        # thirteen sections, each with its own venue and contact). The identity
        # is one organisation; the readings are distinct, so the ordinal is
        # part of the version id and the entity key is not.
        reading = str(ordinal)
        org_record = _key("org", artifact_id, reading, name.casefold())
        spans = record.get("_spans") if isinstance(record.get("_spans"), dict) else {}
        items = []
        org_facts = {"name": name}
        name_quote = _span_quote(text, spans.get("organization"))
        identity_quote = _identity_quote(text, spans.get("organization"), name)
        claims = []
        if identity_quote:
            claims.append({"artifact_id": artifact_id, "field": "identity",
                           "quote": identity_quote})
        elif name_quote:
            # No window on this page supports the name as an identity; stage the
            # name quote anyway so the record is still auditable, and let the
            # gate say MISSING_REQUIRED_IDENTITY rather than inventing a quote.
            claims.append({"artifact_id": artifact_id, "field": "name",
                           "quote": name_quote})
        if record.get("website"):
            org_facts["website"] = record["website"]
            website_quote = _span_quote(text, spans.get("website"))
            if website_quote:
                claims.append({"artifact_id": artifact_id, "field": "website",
                               "quote": website_quote})
        if record.get("address"):
            org_facts["address"] = record["address"]
        if claims:
            org_facts["claims"] = claims
        if first_reading(org_key):
            items.append({"record_id": org_record, "stage": "entities", "kind": "organization",
                          "entity_key": org_key, "facts": org_facts,
                          "artifact_ids": [artifact_id]})
        person = (record.get("person") or "").strip()
        person_key = ""
        if person:
            person_key = _key("person", name.casefold(), person.casefold())
            person_quote = (_block_quote(text, spans.get("organization"), spans.get("person"),
                                         name, person)
                            or _span_quote(text, spans.get("person")))
            person_facts = {"name": person, "organization_id": org_key}
            if person_quote:
                person_facts["claims"] = [{"artifact_id": artifact_id,
                                           "field": "name", "quote": person_quote}]
            if first_reading(person_key):
                items.append({"record_id": _key("person", artifact_id, reading, name.casefold(),
                                                person.casefold()),
                              "stage": "entities", "kind": "person",
                              "entity_key": person_key, "facts": person_facts,
                              "artifact_ids": [artifact_id]})
        email = (record.get("email") or "").strip()
        contact_quote = (_block_quote(text, spans.get("organization"), spans.get("email"),
                                      name, email)
                         or _span_quote(text, spans.get("email")))
        if email and contact_quote:
            contact_key = _key("contact", org_key, email.casefold())
            contact_facts = {"organization_id": org_key, "type": "email", "value": email,
                             "claims": [{"artifact_id": artifact_id, "field": "value",
                                         "quote": contact_quote}]}
            if person_key:
                contact_facts["person_id"] = person_key
                contact_facts["person_name"] = person
            if first_reading(contact_key):
                items.append({"record_id": _key("contact", artifact_id, reading, org_key,
                                                email.casefold()),
                              "stage": "routes", "kind": "contact_point",
                              "entity_key": contact_key,
                              "facts": contact_facts, "artifact_ids": [artifact_id]})
        if items:
            bundles.append(items)
    return bundles


def items_for_batch(text: str, records: list[dict], artifact_id: str) -> list[dict]:
    """The same candidates, flattened (one list, for callers that batch themselves)."""
    return [item for bundle in records_to_bundles(text, records, artifact_id)
            for item in bundle]


def render(summary: dict) -> str:
    return json.dumps(summary, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    """Offline use: python3 research_extract.py page.html [--limit N] [--items ID]."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: research_extract.py <saved-page> [--limit N] [--items SOURCE_ARTIFACT_ID]",
              file=sys.stderr)
        return 2
    source, limit, artifact_id = Path(args[0]), 20, "source"
    rest = args[1:]
    for position, flag in enumerate(rest):
        if flag == "--limit" and position + 1 < len(rest):
            limit = int(rest[position + 1])
        elif flag == "--items" and position + 1 < len(rest):
            artifact_id = rest[position + 1]
    from research_evidence import decode_material, readable_text
    text = readable_text("provider_extracted", decode_material(source.read_bytes()))
    summary = extract(text)
    summary["items"] = items_for_batch(text, summary["records"][:limit], artifact_id)
    print(render({key: value for key, value in summary.items() if key != "records"} |
                 {"record_count": len(summary["records"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

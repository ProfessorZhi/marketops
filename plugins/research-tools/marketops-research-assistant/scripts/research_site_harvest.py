#!/usr/bin/env python3
"""Harvest one organisation's own site into quoted candidates in a single call.

An Agent used to spend six or seven tool calls per page: fetch, read, find the
contact block, copy a quote, propose, audit, then learn at export time that the
binding failed. This module does the mechanical part in one bounded pass:

1. fetch the organisation's page through the existing managed fallback chain
   (Crawl4AI first, TinyFish fetch when the local crawler is unavailable);
2. follow at most ``max_pages - 1`` same-host links whose path or anchor text
   names a contact, people or hire page;
3. cut literal quotes for the organisation name, every email address and every
   phone number out of the *same normalised text* the delivery gate reads;
4. optionally stage the organisation and contact candidates with those claims
   and return the delivery gate's decision for each one.

It never decides who a contact is: every route comes back with its surrounding
text so the Agent (or a host subagent) assigns role and grade. Fetch failures,
blocked hosts and unknown charges are reported, never retried.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import research_delivery as delivery
import research_evidence as evidence
import research_lite_store as store

__all__ = ["harvest", "candidate_links", "extract_routes", "MAX_PAGES"]

MAX_PAGES = 10
QUOTE_RADIUS = 110
CONTEXT_RADIUS = 220
#: Path or anchor words that mark a page worth one of the bounded fetches.
LINK_WORDS = ("contact", "about", "team", "staff", "people", "committee", "trustee",
              "governance", "officer", "hire", "booking", "venue", "facilit", "ground",
              "who-we-are", "find-us", "enquir",
              # Dutch, German, French, Spanish, Italian: the same pages by their own names.
              "over-ons", "wie-zijn", "medewerker", "bestuur", "organisatie", "personeel",
              "kontakt", "impressum", "ueber-uns", "uber-uns", "mitarbeiter", "vorstand",
              "ansprechpartner", "qui-sommes", "a-propos", "equipe", "nous-contacter",
              "contacto", "equipo", "quienes", "contatti", "chi-siamo", "organigram")
#: Words in the task's wanted roles shorter than this are too common to steer by.
FOCUS_WORD_MIN = 4
#: The local part may carry letters beyond ASCII ("geschäftsstelle@"): the
#: literal on the page is the address. A match never starts after a
#: replacement character, which would cut a word in half.
# The local part starts and ends with a letter or digit: "...@antony.fr" is a
# placeholder in a form ("réservez une adresse ...@antony.fr"), not an address.
_EMAIL = re.compile(r"(?<![\w.+\ufffd-])[^\W_](?:[\w.%+-]{0,62}[^\W_])?@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}(?![\w-])")
#: A French number is printed in ten digits, and very often as five pairs
#: ("01 45 81 13 13"). The generic arm below wants a 3-4 digit group, so the
#: pair form — the standard on association and mairie pages — was read as no
#: phone at all. It is matched first, as its own arm.
_PHONE = re.compile(
    r"(?<![\w+])(?:0\d(?:[\s.-]?\d{2}){4}|"
    r"(?:\+\d{1,3}[\s.-]?|0)(?:\(?\d{2,5}\)?[\s.-]?)\d{3,4}[\s.-]?\d{3,4})(?!\d)")
_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js", ".ico")


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href") or None
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href = None


def _readable(kind: str, raw: str) -> str:
    """The delivery gate's text without case folding, so cut quotes stay readable."""
    return evidence.readable_text(kind, raw)


_CFEMAIL = re.compile(r"""data-cfemail\s*=\s*["'][0-9a-fA-F]+["']""")


def obfuscated_email_count(raw: str) -> int:
    """Protected addresses on the page. Member A's rule: never decode them."""
    return len(_CFEMAIL.findall(raw))


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def focus_words(text: str) -> tuple[str, ...]:
    """Distinctive words of the wanted roles ("head greenkeeper, sportdienst")."""
    return tuple(sorted({w for w in re.findall(r"[a-zÀ-ɏ]+", (text or "").casefold())
                         if len(w) >= FOCUS_WORD_MIN} - {"head", "manager", "with", "from",
                                                         "that", "this", "find", "email"}))


def candidate_links(page_url: str, html: str, limit: int,
                    focus: tuple[str, ...] = ()) -> list[str]:
    """Same-host contact/people/hire links, best first, without the page itself.

    A link naming one of the task's own words (``focus``, e.g. "sport" for a
    municipal sports department) counts double: that is the page the task is for.
    """
    parser = _Links()
    try:
        parser.feed(html)
    except Exception:
        return []
    home, seen, ranked = _host(page_url), {page_url.rstrip("/")}, []
    for href, text in parser.links:
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = urljoin(page_url, href).split("#")[0]
        # A page served over https has its plain-http links lifted to https; a page
        # served over http (a site with no certificate) keeps them on http, or every
        # link out of it would be refused by the same missing certificate.
        if page_url.startswith("https://") and url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or _host(url) != home:
            continue
        if parts.path.casefold().endswith(_ASSET_SUFFIXES) or url.rstrip("/") in seen:
            continue
        haystack = (parts.path + " " + text).casefold()
        score = (sum(word in haystack for word in LINK_WORDS)
                 + 2 * sum(word in haystack for word in focus))
        if score:
            seen.add(url.rstrip("/"))
            ranked.append((-score, len(ranked), url))
    return [url for _score, _order, url in sorted(ranked)[:max(0, limit)]]


def _window(text: str, start: int, end: int, radius: int) -> str:
    left, right = max(0, start - radius), min(len(text), end + radius)
    # Trim to word boundaries so a quote never starts mid-token.
    while left > 0 and left < start and not text[left - 1].isspace():
        left += 1
    while right < len(text) and right > end and not text[right].isspace():
        right -= 1
    return text[left:right].strip()


def _quote(text: str, start: int, end: int) -> str:
    quote = _window(text, start, end, QUOTE_RADIUS)
    return quote if 12 <= len(quote) <= 600 else ""


#: Labels that precede a registration, tax or bank number, which share the
#: digit shape of a phone ("Ondernemingsnummer 0207.512.345", "BTW BE0207...").
_NUMBER_LABELS = re.compile(
    r"(btw|vat|kbo|ondernemingsn|enterprise|company\s*(no|number|reg)|registration|"
    r"iban|bic|rpr|siret|siren|ust-?id|steuer|handelsregister|kvk|cif|nif|p\.?iva|"
    r"lat|lon|lng|coordinat|gps)[^\n]{0,25}$", re.IGNORECASE)


def _not_a_phone(text: str, start: int, value: str) -> bool:
    """A company/VAT/bank number or a coordinate pair, not a telephone."""
    if _NUMBER_LABELS.search(text[max(0, start - 40):start]):
        return True
    # "51.1234567, 4.4567" style coordinates: a dot followed by five or more digits.
    return bool(re.search(r"\.\d{5,}", value))


#: Domains forms use to show the shape of an address ("Exemple : nom.prenom@exemple.com").
#: Site builders ship their own sample address ("info@monsite.fr" on Wix, 2026-10-24);
#: it is printed as if it were the organisation's, and no person owns it.
_PLACEHOLDER_DOMAINS = re.compile(
    r"@(?:[\w-]+\.)*(?:example|exemple|beispiel|ejemplo|esempio|voorbeeld|domain|domaine|"
    r"votre-?domaine|yourdomain|mydomain|email|e-mail|mail|test|sample|monsite|mon-?site|"
    r"votresite|votre-?site|monweb|siteweb|wixsite|squarespace)\.[a-z]{2,6}$", re.IGNORECASE)


def extract_routes(text: str) -> list[dict]:
    """Every literal email/phone in normalised text with a quote and its context."""
    found, routes = set(), []
    for channel, pattern in (("email", _EMAIL), ("phone", _PHONE)):
        for match in pattern.finditer(text):
            value = match.group(0).strip().rstrip(".")
            if channel == "email" and _PLACEHOLDER_DOMAINS.search(value):
                continue
            if channel == "phone" and (sum(ch.isdigit() for ch in value) < 10
                                       or _not_a_phone(text, match.start(), value)):
                continue
            key = (channel, value.casefold())
            if key in found:
                continue
            quote = _quote(text, match.start(), match.end())
            if not quote:
                continue
            found.add(key)
            routes.append({"type": channel, "value": value, "quote": quote,
                           "context": _window(text, match.start(), match.end(), CONTEXT_RADIUS)})
    return routes


def _identity_quote(text: str, name: str) -> str:
    index = text.casefold().find(name.casefold())
    if index < 0:
        return ""
    quote = _quote(text, index, index + len(name))
    return quote if delivery._quote_supports(quote, name, distinctive=True) else ""


#: Language-neutral-ish naming habits: how any organisation shortens its own
#: name on its site. No market or sport is named here.
_ARTICLES = ("the ", "die ", "der ", "le ", "la ", "les ", "el ", "de ", "het ")
_LEGAL = {"ltd", "limited", "plc", "llc", "llp", "inc", "incorporated", "corp", "co",
          "gmbh", "ag", "sa", "sas", "sarl", "srl", "spa", "bv", "nv", "pty", "cic", "cio"}
_TYPE_WORDS = {"club", "association", "society", "trust", "council", "centre", "center",
               "foundation", "federation", "league", "academy", "school", "college",
               "company", "group", "union", "institute", "committee"}
_QUALIFIERS = {"county", "district", "borough", "municipal", "regional", "city", "town"}


def name_variants(name: str) -> list[str]:
    """Spellings an organisation uses for itself: drop the article, the legal
    form and regional qualifiers, shorten the type phrase to its initials
    ("X Football Club" -> "X FC"), or drop the type word ("X Football")."""
    base = re.sub(r"\s+", " ", name).strip(" ,.")
    out = [base]
    # Map listings append a place or a description: "Brand Nanterre - Salle d'escalade",
    # "Brand | Padel". The part before the separator is how the site names itself.
    head = re.split(r"\s+[-–—|:]\s+|\s*\(", base, maxsplit=1)[0].strip(" ,.")
    if head and head != base:
        out.append(head)
    for article in _ARTICLES:
        if base.casefold().startswith(article):
            out.append(base[len(article):])
    for item in list(out):
        words = item.split()
        while len(words) > 2 and words[-1].casefold().strip(".,") in _LEGAL:
            words = words[:-1]
        out.append(" ".join(words))
    for item in list(out):
        words = item.split()
        if len(words) >= 3 and words[-1].casefold() in _TYPE_WORDS:
            for width in (2, 3):
                head = words[:-width]
                if any((w.casefold() + " ") not in _ARTICLES for w in head):
                    initials = "".join(w[0].upper() for w in words[-width:] if w[0].isalpha())
                    out.append(" ".join(head + [initials]))
            out.append(" ".join(words[:-1]))
    for item in list(out):
        kept = [w for w in item.split() if w.casefold() not in _QUALIFIERS]
        if 2 <= len(kept) < len(item.split()):
            out.append(" ".join(kept))
    seen, ordered = set(), []
    for item in out:
        item = re.sub(r"\s+", " ", item).strip()
        if len(item.split()) >= 2 and item.casefold() not in seen:
            seen.add(item.casefold())
            ordered.append(item)
    return ordered or [base]


#: Words that only say what kind of public body or organisation a name is, in
#: the languages the plugin meets most ("Gemeente Rumst", "City of Austin").
_BODY_WORDS = {"gemeente", "stad", "city", "town", "municipality", "gemeinde", "stadt",
               "markt", "commune", "ville", "comune", "ayuntamiento", "municipio",
               "kommune", "gmina", "mairie", "village", "parish", "of", "de", "di", "du"}


def host_bound_name(name: str, page_url: str) -> str:
    """The one distinctive word of a name like "Gemeente Rumst", when the site's
    own host carries it (rumst.be). A single word binds only with that support."""
    words = [w for w in re.findall(r"[^\W_]+(?:-[^\W_]+)*", name)
             if w.casefold() not in _BODY_WORDS]
    host = (urlsplit(page_url).hostname or "").casefold().replace("-", "")
    if len(words) == 1:
        return words[0] if len(words[0]) >= 4 and words[0].casefold().replace("-", "") in host else ""
    # A brand followed by a place, as map listings name a chain's site ("Arkose
    # Nanterre", "Climb Up Epinay"): the longest leading words the host spells out.
    # "Harlow Cricket Club" is not "Harlow": a rest naming a kind of body means the
    # first word is a place, which the site would also print for the council.
    for width in range(min(3, len(words) - 1), 0, -1):
        brand = "".join(words[:width]).casefold().replace("-", "")
        rest = {w.casefold() for w in words[width:]}
        if len(brand) >= 4 and brand in host and not rest & _TYPE_WORDS:
            return " ".join(words[:width])
    return ""


def _acronym_quote(text: str, name: str) -> tuple[str, str]:
    """"Harlow CC" listed, "Harlow Cricket Club" published: expand a trailing
    acronym against the page itself instead of a dictionary."""
    words = name.split()
    if len(words) < 2 or not re.fullmatch(r"[A-Z]{2,4}", words[-1]):
        return "", ""
    head = re.escape(" ".join(words[:-1]))
    letters = words[-1]
    pattern = head + "".join(rf"\s+{c}\w+" for c in letters)
    match = re.search(pattern, text, re.I)
    if not match:
        return "", ""
    published = " ".join(match.group(0).split())
    return _identity_quote(text, published), published


def _record_id(*parts: str) -> str:
    return "harvest-" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]


def _envelope_links(root, run_id: str, receipt: dict) -> list[str]:
    """Links a vendor returned beside its cleaned HTML (TinyFish strips anchors)."""
    links = []
    for artifact_id in receipt.get("artifact_ids") or []:
        try:
            material = store.read_artifact(root, run_id, artifact_id)
        except ValueError:
            continue
        if material["material_kind"] != "provider_extracted":
            continue
        try:
            payload = json.loads(evidence.decode_material(material["content"]))
        except ValueError:
            continue
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if isinstance(node.get("links"), list):
                    links += [x for x in node["links"] if isinstance(x, str)]
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return links


def _exists(root, run_id: str, column: str, value: str, *, kind: str = "") -> bool:
    """Key lookup instead of listing the Run: a Run can hold tens of thousands of rows."""
    from contextlib import closing
    with closing(store._connection(root)) as db:
        return db.execute(
            f"SELECT 1 FROM records WHERE run_id=? AND {column}=? AND (?='' OR kind=?) LIMIT 1",
            (run_id, value, kind, kind)).fetchone() is not None


def _same_organization(root, run_id: str, name: str, url: str, own_key: str) -> str:
    """An organisation already in this Run with the same name on the same site.

    Two keys for one organisation (a pool key and a hand-typed one) make its
    site look owned by two organisations, and the delivery gate then refuses
    every contact quoted from it. Same name plus same host is the same entity.
    """
    import json as _json
    from contextlib import closing
    host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
    wanted = " ".join(name.casefold().split())
    with closing(store._connection(root)) as db:
        rows = db.execute("SELECT entity_key, payload_json FROM records WHERE run_id=? AND "
                          "kind='organization' AND entity_key<>?", (run_id, own_key)).fetchall()
    for key, payload in rows:
        try:
            facts = _json.loads(payload)
        except ValueError:
            continue
        names = {" ".join(str(facts.get(f) or "").casefold().split())
                 for f in ("name", "listed_name")}
        site = (urlsplit(str(facts.get("website") or "")).hostname or "").casefold()
        if wanted in names and site.removeprefix("www.") == host:
            return key
    return ""


def admission_for(root, run_id: str, record_ids) -> list[dict]:
    """The delivery gate's decision for these records (one snapshot read per call)."""
    wanted = set(record_ids)
    if not wanted:
        return []
    _sheets, manifest = delivery.build_delivery(root, run_id)
    return [{"record_id": d["record_id"], "kind": d["kind"], "admitted": d["admitted"],
             "reason_codes": d["reason_codes"]}
            for d in manifest["admission"]["decisions"] if d["record_id"] in wanted]


def _redirect_refused(receipt: dict) -> bool:
    return any(a.get("primary_reason_code") == "CRAWL_REDIRECT_REFUSED"
               for a in receipt.get("attempts") or [])


def _redirect_target(receipt: dict, url: str) -> str:
    """The page the site redirected to (an http(s) URL other than ``url``), or ""."""
    for attempt in receipt.get("attempts") or []:
        target = attempt.get("redirect_to") or ""
        if urlsplit(target).scheme not in {"http", "https"} or target.rstrip("/") == url.rstrip("/"):
            continue
        # An https page keeps the lift the listing path applies: its http canonical
        # is usually stale. A page served over http keeps the scheme the site
        # itself named, or the retry would ask a host without a certificate for
        # port 443 — which is the unreachable half of the same site.
        if url.startswith("https://") and target.startswith("http://"):
            target = "https://" + target[len("http://"):]
        return target
    return ""


def _www_twin(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    twin = host[4:] if host.startswith("www.") else "www." + host
    return parts._replace(netloc=twin).geturl()


def _evidence_artifact(receipt: dict) -> str:
    for key in ("primary_artifact_ids", "provider_html_artifact_ids", "artifact_ids"):
        values = receipt.get(key) or []
        if values:
            return values[0]
    return ""


def _jev_page_work(texts: dict, routes: dict, ask, screen_question: str = "") -> None:
    """Directory split, then name fill, on each saved page's own routes (in place)."""
    import research_grading
    import research_jev_pages
    by_page = {}
    for route in routes.values():
        by_page.setdefault(route["artifact_id"], []).append(route)
    for artifact_id, page_routes in by_page.items():
        text = texts.get(artifact_id)
        if not text:
            continue
        splits = research_jev_pages.split_directory(text, page_routes[0]["url"], page_routes,
                                                    artifact_id, ask)
        research_jev_pages.screen_listed(text, splits, artifact_id, screen_question, ask)
        for split in splits:
            split["route"].update(directory_org=split["organization"], quote=split["quote"],
                                  listed_out_of_scope=split.get("out", False))
        research_jev_pages.fill_names(
            text, page_routes, artifact_id, ask,
            has_name=lambda email, quote: bool(research_grading._name_in_quote(email, [("", quote)])))


def harvest(data_root, *, run_id: str, operation_id: str, organization_name: str,
            organization_key: str, url: str, fetch, max_pages: int = 6,
            write: bool = True, check_admission: bool = True, grade=None,
            time_budget_seconds: float | None = None, focus: str = "",
            jev_ask=None, screen_question: str = "") -> dict:
    """Fetch, extract and (optionally) stage one organisation's own-site routes.

    ``fetch(url, operation_id)`` is the managed known-public-URL step; it must
    return the fallback receipt. Nothing here touches the network directly.

    Page fetch IDs carry a short digest of ``organization_key``: parallel
    subagents that each name their call ``harvest-001`` research different
    organisations and must not collide, while a retry for the same
    organisation replays its finished pages instead of fetching them again.
    ``time_budget_seconds`` stops opening new pages once spent (hosts cut a
    tool call off at about 60 s); the result says to call again, which resumes.

    ``jev_ask`` (see `research_jev_pages`) lets Jev settle what code cannot: which
    club on a directory page owns an address, which printed name an address
    belongs to, and, with ``screen_question``, whether the site is a target at
    all (a sure competitor or out-of-scope site is not crawled or staged).
    """
    started = time.monotonic()
    if not isinstance(organization_name, str) or not 3 <= len(organization_name.strip()) <= 200:
        raise ValueError("HARVEST_ORGANIZATION_NAME_INVALID: 3-200 characters, as published")
    store._id(organization_key, "organization_key")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"HARVEST_MAX_PAGES_INVALID: integer 1-{MAX_PAGES}")
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("HARVEST_URL_INVALID: an http(s) page on the organisation's own site")
    name = " ".join(organization_name.split())
    # Map listings often carry http:// sites; HTTPS is tried first where the site is
    # likely to have it. The listing also states how the site is actually served, so
    # that scheme stays available as the one fallback — see ``published_http`` below.
    published_http = url.startswith("http://")
    url = "https://" + url[len("http://"):] if published_http else url
    root = Path(store._root(data_root))

    pages, queue, routes, identity, obfuscated = [], [url], {}, None, 0
    texts, screened = {}, None
    scope = hashlib.sha256(organization_key.encode()).hexdigest()[:8]
    out_of_time = False
    while queue and len(pages) < max_pages:
        if (time_budget_seconds is not None and pages
                and time.monotonic() - started > time_budget_seconds):
            out_of_time = True
            break
        page_url = queue.pop(0)
        child = f"{operation_id}-{scope}-p{len(pages) + 1}"
        receipt = fetch(page_url, child)
        if not pages and _redirect_refused(receipt):
            # The browser refuses to follow any redirect. The organisation's own first
            # page is asked for once more where the site said it lives (ville-x.fr ->
            # www.x.fr/accueil), or else at the other spelling of the same host; that
            # is a new request with its own checks, and identity is still required.
            page_url = _redirect_target(receipt, page_url) or _www_twin(page_url)
            receipt = fetch(page_url, child + "w")
        artifact_id = _evidence_artifact(receipt) if receipt.get("status") == "SUCCESS_CANDIDATE" else ""
        if not artifact_id and not pages and published_http and page_url.startswith("https://"):
            # Many association sites never got a certificate: they answer on :80 and
            # refuse :443 (oms1paris.free.fr answers 200 over http and drops the
            # https connection). The listing published the http URL, so it is asked
            # for exactly as published, once, and the page says which scheme answered.
            page_url = "http://" + page_url[len("https://"):]
            receipt = fetch(page_url, child + "h")
            artifact_id = (_evidence_artifact(receipt)
                           if receipt.get("status") == "SUCCESS_CANDIDATE" else "")
        page = {"url": page_url, "status": receipt.get("status"),
                "reason_code": receipt.get("reason_code", ""), "artifact_id": artifact_id or None}
        pages.append(page)
        if not artifact_id:
            if len(pages) == 1:
                break  # the organisation's own page is unreachable: report, do not wander
            continue
        material = store.read_artifact(root, run_id, artifact_id)
        raw = evidence.decode_material(material["content"])
        text = _readable(material["material_kind"], raw)
        texts[artifact_id] = text
        if len(pages) == 1 and jev_ask is not None and screen_question:
            import research_jev_pages
            screened = research_jev_pages.screen(artifact_id, screen_question, jev_ask)
            if screened["stop"]:
                break  # a sure competitor or out-of-scope site: read no further
        if len(pages) == 1:
            anchors = raw + "".join(f'<a href="{link}"></a>'
                                    for link in _envelope_links(root, run_id, receipt))
            queue.extend(candidate_links(material["origin_url"] or page_url, anchors,
                                         max_pages - 1, focus_words(focus)))
        if identity is None:
            for published in name_variants(name):
                quote = _identity_quote(text, published)
                if quote:
                    break
            else:
                quote, published = _acronym_quote(text, name)
                single = "" if quote else host_bound_name(name, page_url)
                if single:
                    index = text.casefold().find(single.casefold())
                    quote = _quote(text, index, index + len(single)) if index >= 0 else ""
                    published = single
            if quote:
                identity = {"artifact_id": artifact_id, "quote": quote, "url": page_url,
                            "published_name": published}
        obfuscated += obfuscated_email_count(raw)
        for route in extract_routes(text):
            key = (route["type"], route["value"].casefold())
            if key not in routes:
                routes[key] = {**route, "artifact_id": artifact_id, "url": page_url}

    if jev_ask is not None and not (screened or {}).get("stop"):
        _jev_page_work(texts, routes, jev_ask, screen_question)
    if grade is not None:
        # Jev tags who each email reaches; a ranking aid, never evidence. The
        # calls are independent, so they run side by side within the host timeout.
        from concurrent.futures import ThreadPoolExecutor

        def tag(route):
            try:
                route.update(grade(route))
            except ValueError as exc:
                route["role"] = "ungraded:" + str(exc).split(":", 1)[0]
        emails = [route for route in routes.values() if route["type"] == "email"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(tag, emails))
    result = {"run_id": run_id, "operation_id": operation_id, "organization_key": organization_key,
              "pages": pages, "identity": identity, "routes": list(routes.values()),
              "obfuscated_emails_not_decoded": obfuscated,
              "business_verified": False, "written": [], "admission": []}
    if screened is not None:
        result["screen"] = screened
    if (screened or {}).get("stop"):
        result["routes"] = []
        result["next_action"] = ("SCREENED_OUT: Jev judged this site " + str(screened["choice"])
                                 + f" at {screened['confidence']}; nothing staged. Reopen the "
                                 "target if the brief says otherwise")
        return result
    if not pages[0]["artifact_id"]:
        result["next_action"] = ("SITE_UNREACHABLE: " + (pages[0]["reason_code"] or "NO_MATERIAL")
                                 + "; record HOLD with this cause or use another authorised source")
        return result
    if not identity:
        result["next_action"] = ("NAME_NOT_FOUND_ON_SITE: check the published name or the URL; "
                                 "without an identity quote no route can bind to this organisation")
    # Clubs split from a list page carry their own quotes: they are staged even when
    # the site's own name is not on the page; the site's own routes are not.
    listed_only = not identity and any(r.get("directory_org") for r in result["routes"])
    if not write or not (identity or listed_only):
        return result

    items = []
    if listed_only:
        result["routes"] = [r for r in result["routes"] if r.get("directory_org")]
    if identity and not _exists(root, run_id, "entity_key", organization_key, kind="organization"):
        existing = _same_organization(root, run_id, name, url, organization_key)
        if existing:
            result["merged_into_organization_key"] = existing
            organization_key = result["organization_key"] = existing
    if identity and not _exists(root, run_id, "entity_key", organization_key, kind="organization"):
        items.append({"record_id": _record_id(run_id, organization_key, "org"), "stage": "entities",
                      "kind": "organization", "entity_key": organization_key,
                      "facts": {"name": identity["published_name"], "website": url,
                                "listed_name": name, "claims": [
                          {"field": "identity", "artifact_id": identity["artifact_id"],
                           "quote": identity["quote"]}]},
                      "artifact_ids": [identity["artifact_id"]]})
    # A club the screen put out of scope is neither the site's nor staged.
    result["routes"] = [r for r in result["routes"] if not r.get("listed_out_of_scope")]
    listed = {}
    for route in result["routes"]:
        club = route.get("directory_org")
        if club and club["entity_key"] not in listed:
            # A club printed on this page's list: its own organisation, quoted from
            # the list entry that names it and prints its address.
            listed[club["entity_key"]] = True
            if not _exists(root, run_id, "entity_key", club["entity_key"], kind="organization"):
                items.append({"record_id": _record_id(run_id, club["entity_key"], "org"),
                              "stage": "entities", "kind": "organization",
                              "entity_key": club["entity_key"],
                              "facts": {"name": club["name"], "listed_on": route["url"],
                                        "listed_by_organization_id": organization_key,
                                        "found_by": "DIRECTORY_SPLIT", "claims": [
                                  {"field": "identity", "artifact_id": route["artifact_id"],
                                   "quote": route["quote"]}]},
                              "artifact_ids": [route["artifact_id"]]})
    for route in result["routes"]:
        owner = (route.get("directory_org") or {}).get("entity_key") or organization_key
        entity = f"{owner}-{route['type']}-" + hashlib.sha256(
            route["value"].casefold().encode()).hexdigest()[:10]
        items.append({"record_id": _record_id(run_id, entity), "stage": "routes",
                      "kind": "contact_point", "entity_key": entity,
                      "facts": {"organization_id": owner, "type": route["type"],
                                "value": route["value"], "source_url": route["url"],
                                **({"person_name": route["person_name"],
                                    "person_name_by": route.get("name_by", "")}
                                   if route.get("person_name") else {}),
                                # Jev's tag ranks the route for people; it is not evidence.
                                **({"jev_role": route["role"],
                                    "jev_confidence": route.get("confidence")}
                                   if route.get("role") else {}),
                                "claims": [{"field": "value", "artifact_id": route["artifact_id"],
                                            "quote": route["quote"]}]},
                      "artifact_ids": [route["artifact_id"]]})
    if items:
        fresh = [item for item in items
                 if not _exists(root, run_id, "record_id", item["record_id"])]
        for start in range(0, len(fresh), 20):  # the store's atomic batch bound
            store.put_candidates_batch(root, run_id, fresh[start:start + 20])
        result["written"] = [item["record_id"] for item in fresh]
        result["record_ids"] = [item["record_id"] for item in items]
        if check_admission:
            result["admission"] = admission_for(root, run_id, result["record_ids"])
    result["next_action"] = ("ASSIGN_ROLE_AND_GRADE_FROM_CONTEXT" if result["routes"] else
                             "NO_PUBLIC_ROUTE_ON_SITE: record HOLD with the pages tried, or try a "
                             "role search on this domain")
    if out_of_time:
        result["pages_not_read_for_time"] = len(queue)
        result["next_action"] = ("TIME_BUDGET_REACHED: call marketops_site_harvest again with the "
                                 "same arguments to read the remaining pages (finished pages are "
                                 "replayed, not fetched again); " + result["next_action"])
    return result


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
import html as html_lib
import json
import re
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
              "who-we-are", "find-us", "enquir")
_EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}(?![\w-])")
_PHONE = re.compile(r"(?<![\w+])(?:\+\d{1,3}[\s.-]?|0)(?:\(?\d{2,5}\)?[\s.-]?)\d{3,4}[\s.-]?\d{3,4}(?!\d)")
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
    """The delivery gate's normalised text without case folding.

    Quotes are compared after ``research_evidence._norm``; cutting them from the
    same text with its original case keeps the client-facing evidence readable.
    """
    text = raw
    if kind == "provider_extracted":
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict) and isinstance(parsed.get("markdown"), str):
                text = parsed["markdown"]
        except ValueError:
            pass
    if kind in {"rendered_html", "raw_response", "provider_html"}:
        text = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", text)
        text = evidence.mailto_visible(text)
        text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html_lib.unescape(text).split())


_CFEMAIL = re.compile(r"""data-cfemail\s*=\s*["'][0-9a-fA-F]+["']""")


def obfuscated_email_count(raw: str) -> int:
    """Protected addresses on the page. Member A's rule: never decode them."""
    return len(_CFEMAIL.findall(raw))


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def candidate_links(page_url: str, html: str, limit: int) -> list[str]:
    """Same-host contact/people/hire links, best first, without the page itself."""
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
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or _host(url) != home:
            continue
        if parts.path.casefold().endswith(_ASSET_SUFFIXES) or url.rstrip("/") in seen:
            continue
        haystack = (parts.path + " " + text).casefold()
        score = sum(word in haystack for word in LINK_WORDS)
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


def extract_routes(text: str) -> list[dict]:
    """Every literal email/phone in normalised text with a quote and its context."""
    found, routes = set(), []
    for channel, pattern in (("email", _EMAIL), ("phone", _PHONE)):
        for match in pattern.finditer(text):
            value = match.group(0).strip().rstrip(".")
            if channel == "phone" and sum(ch.isdigit() for ch in value) < 10:
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


_CLUB_WORDS = (("Cricket Club", "CC"), ("Football Club", "FC"), ("Rugby Football Club", "RFC"))


def name_variants(name: str) -> list[str]:
    """How a club usually writes its own name: list "Essex County Cricket Club",
    site "Essex Cricket"; list "Harlow Cricket Club", site "Harlow CC"."""
    base = re.sub(r"\s+", " ", name).strip()
    out = [base]
    if base.casefold().startswith("the "):
        out.append(base[4:])
    for long, short in _CLUB_WORDS:
        for item in list(out):
            if re.search(rf"\b{long}\b", item, re.I):
                out.append(re.sub(rf"\b{long}\b", short, item, flags=re.I))
                out.append(re.sub(r"\s+Club\b", "", item, flags=re.I))
            elif re.search(rf"\b{short}\b", item):
                out.append(re.sub(rf"\b{short}\b", long, item))
    for item in list(out):
        if re.search(r"\bCounty\b", item, re.I):
            out.append(re.sub(r"\s+County\b", "", item, flags=re.I))
    seen, ordered = set(), []
    for item in out:
        item = re.sub(r"\s+", " ", item).strip()
        if item and item.casefold() not in seen:
            seen.add(item.casefold())
            ordered.append(item)
    return ordered


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
            payload = json.loads(material["content"].decode("utf-8", errors="replace"))
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


def admission_for(root, run_id: str, record_ids) -> list[dict]:
    """The delivery gate's decision for these records (one snapshot read per call)."""
    wanted = set(record_ids)
    if not wanted:
        return []
    _sheets, manifest = delivery.build_delivery(root, run_id)
    return [{"record_id": d["record_id"], "kind": d["kind"], "admitted": d["admitted"],
             "reason_codes": d["reason_codes"]}
            for d in manifest["admission"]["decisions"] if d["record_id"] in wanted]


def _evidence_artifact(receipt: dict) -> str:
    for key in ("primary_artifact_ids", "provider_html_artifact_ids", "artifact_ids"):
        values = receipt.get(key) or []
        if values:
            return values[0]
    return ""


def harvest(data_root, *, run_id: str, operation_id: str, organization_name: str,
            organization_key: str, url: str, fetch, max_pages: int = 6,
            write: bool = True, check_admission: bool = True) -> dict:
    """Fetch, extract and (optionally) stage one organisation's own-site routes.

    ``fetch(url, operation_id)`` is the managed known-public-URL step; it must
    return the fallback receipt. Nothing here touches the network directly.
    """
    if not isinstance(organization_name, str) or not 3 <= len(organization_name.strip()) <= 200:
        raise ValueError("HARVEST_ORGANIZATION_NAME_INVALID: 3-200 characters, as published")
    store._id(organization_key, "organization_key")
    if type(max_pages) is not int or not 1 <= max_pages <= MAX_PAGES:
        raise ValueError(f"HARVEST_MAX_PAGES_INVALID: integer 1-{MAX_PAGES}")
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("HARVEST_URL_INVALID: an http(s) page on the organisation's own site")
    name = " ".join(organization_name.split())
    # Map listings often carry http:// sites; the fetch path accepts HTTPS only.
    url = "https://" + url[len("http://"):] if url.startswith("http://") else url
    root = Path(store._root(data_root))

    pages, queue, routes, identity, obfuscated = [], [url], {}, None, 0
    while queue and len(pages) < max_pages:
        page_url = queue.pop(0)
        child = f"{operation_id}-p{len(pages) + 1}"
        receipt = fetch(page_url, child)
        artifact_id = _evidence_artifact(receipt) if receipt.get("status") == "SUCCESS_CANDIDATE" else ""
        page = {"url": page_url, "status": receipt.get("status"),
                "reason_code": receipt.get("reason_code", ""), "artifact_id": artifact_id or None}
        pages.append(page)
        if not artifact_id:
            if len(pages) == 1:
                break  # the organisation's own page is unreachable: report, do not wander
            continue
        material = store.read_artifact(root, run_id, artifact_id)
        raw = material["content"].decode("utf-8", errors="replace")
        text = _readable(material["material_kind"], raw)
        if len(pages) == 1:
            anchors = raw + "".join(f'<a href="{link}"></a>'
                                    for link in _envelope_links(root, run_id, receipt))
            queue.extend(candidate_links(material["origin_url"] or page_url, anchors, max_pages - 1))
        if identity is None:
            for published in name_variants(name):
                quote = _identity_quote(text, published)
                if quote:
                    identity = {"artifact_id": artifact_id, "quote": quote, "url": page_url,
                                "published_name": published}
                    break
        obfuscated += obfuscated_email_count(raw)
        for route in extract_routes(text):
            key = (route["type"], route["value"].casefold())
            if key not in routes:
                routes[key] = {**route, "artifact_id": artifact_id, "url": page_url}

    result = {"run_id": run_id, "operation_id": operation_id, "organization_key": organization_key,
              "pages": pages, "identity": identity, "routes": list(routes.values()),
              "obfuscated_emails_not_decoded": obfuscated,
              "business_verified": False, "written": [], "admission": []}
    if not pages[0]["artifact_id"]:
        result["next_action"] = ("SITE_UNREACHABLE: " + (pages[0]["reason_code"] or "NO_MATERIAL")
                                 + "; record HOLD with this cause or use another authorised source")
        return result
    if not identity:
        result["next_action"] = ("NAME_NOT_FOUND_ON_SITE: check the published name or the URL; "
                                 "without an identity quote no route can bind to this organisation")
    if not write or not identity:
        return result

    items = []
    if not _exists(root, run_id, "entity_key", organization_key, kind="organization"):
        items.append({"record_id": _record_id(run_id, organization_key, "org"), "stage": "entities",
                      "kind": "organization", "entity_key": organization_key,
                      "facts": {"name": identity["published_name"], "website": url,
                                "listed_name": name, "claims": [
                          {"field": "identity", "artifact_id": identity["artifact_id"],
                           "quote": identity["quote"]}]},
                      "artifact_ids": [identity["artifact_id"]]})
    for route in result["routes"]:
        entity = f"{organization_key}-{route['type']}-" + hashlib.sha256(
            route["value"].casefold().encode()).hexdigest()[:10]
        items.append({"record_id": _record_id(run_id, entity), "stage": "routes",
                      "kind": "contact_point", "entity_key": entity,
                      "facts": {"organization_id": organization_key, "type": route["type"],
                                "value": route["value"], "source_url": route["url"],
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
    return result


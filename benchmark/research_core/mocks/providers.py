"""Provider-shaped mocks: seven APIs, ten failure modes, no network.

Every provider is served from the same deterministic corpus index, but each
answers in its *own* envelope. That is the point: the adapter normalises Serper's
``organic`` list, TinyFish's ``results``, DeepSeek's ``data``, YouTube's
``items`` and Places' ``places`` into one internal result shape. A provider that
changes its envelope breaks one normaliser, not the benchmark.

Failure modes are injected, not simulated with random numbers, so a contract
test can assert the exact status a real failure must produce:

    normal, empty, timeout, rate_limited, server_error, duplicate,
    bad_format, missing_field, invalid_json, multilingual

``bad_format`` means well-formed JSON with a wrong type where a list belongs.
``missing_field`` means a required key is absent. ``invalid_json`` means the
body is not JSON at all. The three must not collapse into one another: they map
to ``invalid_output``, ``invalid_output`` and ``failed`` respectively, and
never to ``empty``.
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata

from ..adapters.base import AdapterTimeout, HTTPError, ParseError

REPO = pathlib.Path(__file__).resolve().parents[3]
INDEX_PATH = REPO / "benchmark" / "datasets" / "research-core" / "corpus" / "search_index.json"

MODES = ("normal", "empty", "timeout", "rate_limited", "server_error", "duplicate",
         "bad_format", "missing_field", "invalid_json", "multilingual")

PROVIDERS = {
    "mock_serper": {"stage": "search", "envelope": "organic",
                    "requires": ["query", "num"],
                    "fields": ["title", "link", "snippet", "position"]},
    "mock_tinyfish_search": {"stage": "search", "envelope": "results",
                             "requires": ["query", "count"],
                             "fields": ["title", "url", "description"]},
    "mock_deepseek_search": {"stage": "search", "envelope": "data",
                             "requires": ["q", "limit"],
                             "fields": ["title", "url", "content"]},
    "mock_youtube_data": {"stage": "search", "envelope": "items",
                          "requires": ["part", "q", "maxResults"],
                          "fields": ["snippet.title", "id.channelId"]},
    "mock_google_maps_places": {"stage": "search", "envelope": "places",
                                "requires": ["textQuery", "maxResultCount"],
                                "fields": ["displayName.text", "websiteUri"]},
    "mock_tinyfish_fetch": {"stage": "fetch", "envelope": "content",
                            "requires": ["url"],
                            "fields": ["url", "text", "links"]},
    "mock_deepseek_fetch": {"stage": "fetch", "envelope": "page",
                            "requires": ["url"],
                            "fields": ["final_url", "content", "mailto"]},
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "their", "our",
    "der", "die", "das", "und", "für", "mit", "von", "den", "dem", "des",
    "een", "het", "van", "voor", "met", "die", "dat",
    "official", "website", "company", "companies", "contact", "details", "profile",
}

#: Intents where the caller's country is a filter, not a hint.
COUNTRY_STRICT = {
    "official_website", "contact_page", "team_page", "executive", "country_limited",
    "multilingual", "identity_link", "official_source_priority",
}

_INDEX: dict[str, dict] | None = None


def index() -> dict[str, dict]:
    global _INDEX
    if _INDEX is None:
        _INDEX = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return _INDEX


def normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in normalise(text).split() if len(t) >= 3 and t not in STOPWORDS}


def _handle(slug: str) -> str:
    return "".join(part.capitalize() for part in slug.split("-"))


def search(
    provider: str = "mock_serper",
    *,
    query: str,
    country: str = "",
    language: str = "",
    top_k: int = 10,
    intent: str = "",
    mode: str = "normal",
) -> dict:
    """Return this provider's own envelope; ``adapter`` normalises it."""
    if mode == "timeout":
        raise AdapterTimeout(f"{provider}: no response within 5s")
    if mode == "rate_limited":
        raise HTTPError(429, f"{provider}: rate limit exceeded")
    if mode == "server_error":
        raise HTTPError(500, f"{provider}: upstream error")
    if mode == "invalid_json":
        raise ParseError(f"{provider}: body is not JSON")

    docs = _rank(query, country=country, language=language, top_k=top_k, intent=intent)
    if mode == "empty":
        docs = []
    if mode in ("bad_format", "missing_field"):
        docs = docs[:1] or [{"slug": "northwind-marine"}]

    rows = []
    youtube = intent in ("youtube_channel", "youtube_creator")
    for position, doc in enumerate(docs, start=1):
        source = index()[doc["slug"]]
        if youtube:
            rows.append({"title": f"{source['name']} — YouTube",
                         "url": f"https://www.youtube.com/@{_handle(doc['slug'])}",
                         "snippet": f"Official channel of {source['name']}."})
        else:
            rows.append({"title": source["name"],
                         "url": f"https://{source['domain']}/",
                         "snippet": source["description"][:160]})
    if mode == "duplicate" and rows:
        rows = [rows[0]] + rows

    return _envelope(provider, query, rows, top_k, mode)


def _envelope(provider: str, query: str, rows: list[dict], top_k: int, mode: str) -> dict:
    if provider == "mock_serper":
        organic = [{"title": r["title"], "link": r["url"], "snippet": r["snippet"],
                    "position": i} for i, r in enumerate(rows, start=1)]
        payload = {"searchParameters": {"q": query, "num": top_k}, "organic": organic}
        if mode == "bad_format":
            payload["organic"] = {"unexpected": "object"}
        elif mode == "missing_field":
            payload["organic"][0].pop("link", None)
    elif provider == "mock_tinyfish_search":
        payload = {"query": query, "count": top_k,
                   "results": [{"title": r["title"], "url": r["url"],
                                "description": r["snippet"]} for r in rows]}
        if mode == "bad_format":
            payload["results"] = {"unexpected": "object"}
        elif mode == "missing_field":
            payload["results"][0].pop("url", None)
    elif provider == "mock_deepseek_search":
        payload = {"q": query, "limit": top_k,
                   "data": [{"title": r["title"], "url": r["url"],
                             "content": r["snippet"]} for r in rows]}
        if mode == "bad_format":
            payload["data"] = {"unexpected": "object"}
        elif mode == "missing_field":
            payload["data"][0].pop("url", None)
    elif provider == "mock_youtube_data":
        payload = {"kind": "youtube#searchListResponse",
                   "items": [{"snippet": {"title": r["title"], "channelTitle": r["title"]},
                              "id": {"channelId": r["url"].rsplit("@", 1)[-1]}}
                             for r in rows]}
        for item, r in zip(payload["items"], rows):
            item["snippet"]["canonicalUrl"] = r["url"]
        if mode == "bad_format":
            payload["items"] = {"unexpected": "object"}
        elif mode == "missing_field":
            payload["items"][0]["snippet"].pop("title", None)
    elif provider == "mock_google_maps_places":
        payload = {"places": [{"displayName": {"text": r["title"]},
                               "websiteUri": r["url"]} for r in rows]}
        if mode == "bad_format":
            payload["places"] = {"unexpected": "object"}
        elif mode == "missing_field":
            payload["places"][0].pop("websiteUri", None)
    else:
        raise KeyError(f"{provider} is not a search provider")
    return payload


def _rank(query: str, *, country: str, language: str, top_k: int, intent: str) -> list[dict]:
    needle = normalise(query)
    query_tokens = _tokens(query)
    strict_country = bool(country) and intent in COUNTRY_STRICT
    scored = []
    for slug, doc in index().items():
        if strict_country and doc["country"].lower() != country.lower():
            continue
        haystack = normalise(" ".join([
            doc["name"], " ".join(doc.get("aliases", [])), doc["industry"],
            doc["description"],
        ]))
        doc_tokens = set(doc["tokens"])
        relevance = sum(1 for token in query_tokens if token in doc_tokens)
        phrase = normalise(doc["name"]) in needle or any(
            normalise(alias) and normalise(alias) in needle for alias in doc.get("aliases", [])
        )
        if phrase:
            relevance += 10
        # Country and language only break ties; they must never make an
        # unrelated company relevant.
        # A single shared token is enough to be a candidate; two are required
        # only when the country is not already narrowing the pool.
        minimum = 1 if strict_country else 2
        if relevance < minimum and not phrase:
            continue
        score = relevance
        if country and doc["country"].lower() == country.lower():
            score += 2
        if language and doc["language"] == language:
            score += 1
        scored.append((-score, slug, doc))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [doc for _, slug, doc in scored[:top_k]]


def fetch(provider: str = "mock_tinyfish_fetch", *, url: str, page: dict,
          mode: str = "normal") -> dict:
    """Normalise a fetched page into this provider's envelope.

    ``page`` is the already-retrieved ``{final_url, text, links, emails}``; the
    HTTP work belongs to the fetch adapter, not here.
    """
    if mode == "timeout":
        raise AdapterTimeout(f"{provider}: connection timed out for {url}")
    if mode == "rate_limited":
        raise HTTPError(429, f"{provider}: rate limit exceeded")
    if mode == "server_error":
        raise HTTPError(500, f"{provider}: upstream error")
    if mode == "invalid_json":
        raise ParseError(f"{provider}: body is not JSON")
    if mode == "empty":
        page = {"final_url": page["final_url"], "text": "", "links": [], "emails": []}

    if provider == "mock_tinyfish_fetch":
        payload = {"url": page["final_url"], "content_type": "text/html",
                   "text": page["text"],
                   "links": page["links"]}
        if mode == "bad_format":
            payload["links"] = {"unexpected": "object"}
        elif mode == "missing_field":
            payload.pop("links", None)
    elif provider == "mock_deepseek_fetch":
        payload = {"final_url": page["final_url"], "content": page["text"],
                   "mailto": page["emails"]}
        if mode == "bad_format":
            payload["content"] = ["unexpected", "list"]
        elif mode == "missing_field":
            payload.pop("content", None)
    else:
        raise KeyError(f"{provider} is not a fetch provider")
    if mode == "duplicate" and payload.get("links"):
        payload["links"] = [payload["links"][0]] + list(payload["links"])
    return payload

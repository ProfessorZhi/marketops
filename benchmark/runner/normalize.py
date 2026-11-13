"""Turn recorded provider bodies into benchmark results.

The benchmark stores provider responses verbatim. That is what makes replay honest --
but it also means the scorer has to understand each vendor's envelope. This module is
the only place that knowledge lives, and it is intentionally narrow: it maps a response
to ``(title, url, snippet, rank)`` and nothing else.

Adding a provider means adding one branch here and one fixture in the offline tests. A
provider whose envelope is not recognised fails loudly rather than silently scoring
zero, because a zero from an unparsed body looks exactly like a provider that found
nothing.
"""

from __future__ import annotations

from typing import Any, Iterable

from .metrics import FetchResult, SearchResult


class NormalizeError(ValueError):
    """Raised when a recorded body does not match any known envelope."""


def _organic(rows: Iterable[dict], *, url_key: str = "link", snippet_key: str = "snippet",
             title_key: str = "title") -> list[dict]:
    out = []
    rank = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = (row.get(url_key) or row.get("url") or row.get("website") or "").strip()
        if not url:
            continue
        rank += 1
        out.append(
            {
                "rank": rank,
                "title": (row.get(title_key) or "").strip(),
                "url": url,
                "snippet": (row.get(snippet_key) or "").strip(),
            }
        )
    return out


def search_rows(provider: str, body: dict) -> list[dict]:
    """Extract ranked rows from a recorded search response.

    Accepts the benchmark's own normalised form (``{"results": [...]}``) first so a
    hand-written fixture does not need to imitate a vendor envelope.
    """
    if not isinstance(body, dict):
        raise NormalizeError(f"{provider}: recorded body is not an object")
    if isinstance(body.get("results"), list):
        rows = []
        for i, row in enumerate(body["results"]):
            if not isinstance(row, dict) or not row.get("url"):
                continue
            rows.append(
                {
                    "rank": int(row.get("rank") or i + 1),
                    "title": row.get("title") or "",
                    "url": row["url"],
                    "snippet": row.get("snippet") or "",
                }
            )
        return rows
    if provider == "serper":
        rows = body.get("organic")
        if rows is None:
            rows = body.get("places")
        return _organic(rows or [])
    if provider == "serpapi":
        rows = body.get("organic_results")
        if rows is None:
            rows = body.get("local_results")
        return _organic(rows or [])
    if provider in ("deepseek", "tinyfish"):
        return _organic(body.get("results") or [])
    raise NormalizeError(
        f"{provider}: unrecognised search envelope (keys: {sorted(body)[:8]})"
    )


def search_results(provider: str, body: dict) -> list[SearchResult]:
    return [
        SearchResult(url=r["url"], rank=r["rank"], title=r["title"], snippet=r["snippet"])
        for r in search_rows(provider, body)
    ]


def fetch_result(reader: str, body: dict, url: str) -> FetchResult:
    """Build a :class:`FetchResult` from a recorded fetch response.

    Recorded shape (benchmark form)::

        {"ok": true, "text": "...", "html": "...", "links": [...],
         "emails": [...], "phones": [...], "persons": [["name", "role"]],
         "status": 200, "error_code": ""}
    """
    if not isinstance(body, dict):
        raise NormalizeError(f"{reader}: recorded body is not an object")
    if "ok" not in body:
        raise NormalizeError(f"{reader}: fetch body has no 'ok' field")
    return FetchResult(
        url=body.get("url") or url,
        ok=bool(body.get("ok")),
        text=body.get("text") or "",
        html=body.get("html") or "",
        links=tuple(body.get("links") or ()),
        emails=tuple(body.get("emails") or ()),
        phones=tuple(body.get("phones") or ()),
        persons=tuple(tuple(p) for p in (body.get("persons") or ())),
        status=body.get("status"),
        error_code=body.get("error_code") or "",
    )


def e2e_delivered(provider: str, body: dict) -> dict:
    """Recorded end-to-end delivery: ``{"entities": [{entity_id, name, official_url,
    contacts: [{kind, value, evidence_url, person, role}]}]}``."""
    if not isinstance(body, dict) or not isinstance(body.get("entities"), list):
        raise NormalizeError(f"{provider}: e2e body must hold an 'entities' list")
    return body


def require(condition: bool, message: str) -> None:
    if not condition:
        raise NormalizeError(message)


def as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}

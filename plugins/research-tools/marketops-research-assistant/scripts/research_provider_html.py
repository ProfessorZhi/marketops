"""Validate vendor-cleaned HTML without calling it an original HTTP response."""
from __future__ import annotations

from html import unescape
import re
from urllib.parse import urlsplit, urlunsplit


def canonical_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError("PROVIDER_HTML_URL_INVALID")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError()
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
    except ValueError:
        raise ValueError("PROVIDER_HTML_URL_INVALID") from None


def _same_site(left, right):
    def host(value):
        return (urlsplit(value).hostname or "").casefold().removeprefix("www.")
    return bool(host(left)) and host(left) == host(right)


def validate_content(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 4_000_000:
        raise ValueError("PROVIDER_HTML_INVALID_CONTENT")
    if not re.search(r"<(?:html|body|head|main|section|article|div|p|h[1-6]|ul|ol|table|a|span|address)\b", value, re.I):
        raise ValueError("PROVIDER_HTML_INVALID_CONTENT")
    from research_adapter_crawl4ai import _challenge_markers
    if _challenge_markers({}, value):
        raise ValueError("SOURCE_CHALLENGE_PAGE")
    visible = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>|<!--.*?-->", " ", value)
    if not unescape(re.sub(r"<[^>]*>", " ", visible)).strip():
        raise ValueError("SOURCE_EMPTY_EXTRACT")
    return value


def extract_pages(payload, requested_urls):
    """Validate the whole batch before any page receives a source-material ID.

    Known response shapes are per-URL results/pages/data, a single URL object,
    or a dictionary keyed by source URL. Missing URL binding is never guessed
    from body text, order in a batch or the fact that one URL was requested.
    """
    if not isinstance(requested_urls, list) or not 1 <= len(requested_urls) <= 10:
        raise ValueError("PROVIDER_HTML_REQUEST_URLS_INVALID")
    requested = {canonical_url(x) for x in requested_urls}
    if len(requested) != len(requested_urls):
        raise ValueError("PROVIDER_HTML_REQUEST_URLS_DUPLICATE")
    if not isinstance(payload, dict) or payload.get("errors") or payload.get("error"):
        raise ValueError("PROVIDER_HTML_BATCH_INCOMPLETE")
    rows = None
    for key in ("results", "pages", "data"):
        if isinstance(payload.get(key), list):
            rows = payload[key]
            break
    if rows is None and "url" in payload:
        rows = [payload]
    if rows is None and payload and all(isinstance(k, str) and k.startswith(("http://", "https://")) for k in payload):
        rows = [{**value, "url": key} if isinstance(value, dict) else {"url": key, "html": value}
                for key, value in payload.items()]
    if not isinstance(rows, list) or len(rows) != len(requested):
        raise ValueError("PROVIDER_HTML_BATCH_INCOMPLETE")
    seen, results = set(), []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("PROVIDER_HTML_RESPONSE_INVALID")
        url = canonical_url(row.get("url"))
        if url not in requested or url in seen:
            raise ValueError("PROVIDER_HTML_URL_MISMATCH")
        for key in ("final_url", "redirected_url", "resolved_url"):
            # A redirect inside the same site (http->https, www<->apex, a landing
            # path) is the same source; a redirect to another host is not.
            if row.get(key) and not _same_site(canonical_url(row[key]), url):
                raise ValueError("PROVIDER_HTML_URL_MISMATCH")
        status = row.get("status_code", row.get("http_status", row.get("http_status_code")))
        if status is not None and (type(status) is not int or not 200 <= status < 300):
            raise ValueError("SOURCE_ACCESS_BLOCKED")
        if row.get("error") or row.get("success") is False:
            raise ValueError("PROVIDER_HTML_BATCH_INCOMPLETE")
        headers = row.get("headers")
        from research_adapter_crawl4ai import _challenge_markers
        if _challenge_markers(headers if isinstance(headers, dict) else {}, ""):
            raise ValueError("SOURCE_CHALLENGE_PAGE")
        body = row.get("html", row.get("content"))
        if row.get("html") is not None and row.get("content") is not None and row["html"] != row["content"]:
            raise ValueError("PROVIDER_HTML_AMBIGUOUS_CONTENT")
        if body is None and row.get("format") in (None, "html"):
            # TinyFish returns the requested HTML in `text`; validate_content still
            # requires real markup, so Markdown cannot be relabelled as HTML.
            body = row.get("text")
        results.append({"url": url, "html": validate_content(body)})
        seen.add(url)
    if seen != requested:
        raise ValueError("PROVIDER_HTML_BATCH_INCOMPLETE")
    return results

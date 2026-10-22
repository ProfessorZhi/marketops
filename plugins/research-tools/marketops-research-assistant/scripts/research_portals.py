#!/usr/bin/env python3
"""Turn a paged open-data record URL into the portal's bulk export of the same query.

Record APIs page slowly and stop at a fixed depth: Opendatasoft gives 100 rows per
call and refuses offsets past 10,000. In the Paris run that cost 17 minutes and the
last 2,772 rows. Every common portal has a bulk route for the same filter; this
module rewrites the first-page URL to it so the agent does not have to know each
portal. Only a first page is rewritten (no offset, or offset 0): an explicit later
page is left alone. A query this module cannot translate safely is left alone too.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

__all__ = ["bulk_url", "MAX_EXPORT_ROWS"]

#: Matches research_bulk.MAX_ROWS: a slice above it must be split with a filter.
MAX_EXPORT_ROWS = 50_000

_ODS_V2 = re.compile(r"^(?P<base>/api/explore/v2\.[01]/catalog/datasets/[^/]+)/records/?$")
_ODS_V2_KEEP = {"where", "select", "refine", "exclude", "order_by", "lang", "timezone"}
_CKAN = re.compile(r"/api/(?:3/)?action/datastore_search/?$")
_SOCRATA = re.compile(r"^/resource/[a-z0-9]{4}-[a-z0-9]{4}\.(?:json|csv)$")


def _first_page(params: dict) -> bool:
    for key in ("offset", "$offset", "start"):
        value = params.get(key)
        if value not in (None, "", "0"):
            return False
    return True


def bulk_url(url: str) -> tuple[str, str]:
    """(export URL, portal) for a paged record URL; (url, "") when nothing applies."""
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    if not _first_page(params):
        return url, ""
    path = parts.path
    ods = _ODS_V2.match(path)
    if ods:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k in _ODS_V2_KEEP]
        return urlunsplit((parts.scheme, parts.netloc, ods["base"] + "/exports/json",
                           urlencode(kept), "")), "opendatasoft"
    if path.rstrip("/").endswith("/api/records/1.0/search") and params.get("dataset"):
        if set(params) - {"dataset", "rows", "start", "lang", "timezone"} - {
                k for k in params if k.startswith(("refine.", "exclude."))}:
            return url, ""  # free-text q or facets: no safe translation
        kept = [("refine", f'{k[7:]}:"{v}"') for k, v in params.items() if k.startswith("refine.")]
        kept += [("exclude", f'{k[8:]}:"{v}"') for k, v in params.items() if k.startswith("exclude.")]
        base = f"/api/explore/v2.1/catalog/datasets/{params['dataset']}/exports/json"
        return urlunsplit((parts.scheme, parts.netloc, base, urlencode(kept), "")), "opendatasoft"
    if _CKAN.search(path) and params.get("resource_id"):
        params.pop("offset", None)
        params["limit"] = "32000"  # CKAN's default ceiling (ckan.datastore.search.rows_max)
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(params), "")), "ckan"
    if _SOCRATA.match(path):
        try:
            current = int(params.get("$limit") or 0)
        except ValueError:
            return url, ""
        if current >= MAX_EXPORT_ROWS:
            return url, ""
        params.pop("$offset", None)
        params["$limit"] = str(MAX_EXPORT_ROWS)
        return urlunsplit((parts.scheme, parts.netloc, path, urlencode(params), "")), "socrata"
    return url, ""

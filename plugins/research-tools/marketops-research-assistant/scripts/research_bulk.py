"""Parse saved, SHA-checked JSON once and import bounded local pages of targets."""
import hashlib
from collections import Counter
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qs, urlsplit

import research_lite_store as store
import research_pool as pool


class _Pre(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inside = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "pre":
            self.inside = True

    def handle_endtag(self, tag):
        if tag == "pre":
            self.inside = False

    def handle_data(self, text):
        if self.inside:
            self.parts.append(text)


def _json(material):
    text = material["content"].decode("utf-8-sig")
    if material["material_kind"] == "rendered_html":
        parser = _Pre()
        parser.feed(text)
        text = "".join(parser.parts)
    try:
        data = json.loads(text)
    except ValueError:
        raise ValueError("BULK_JSON_REQUIRED: use a saved JSON response or browser JSON pre element") from None
    if isinstance(data, list):
        rows, listed = data, len(data)
    elif isinstance(data, dict):
        rows = data.get("results", data.get("records"))
        listed = data.get("total_count", len(rows) if isinstance(rows, list) else None)
    else:
        rows, listed = None, None
    if not isinstance(rows, list) or len(rows) > 5000 or any(not isinstance(r, dict) for r in rows):
        raise ValueError("BULK_ROWS_INVALID: at most 5000 JSON objects per saved response")
    # Opendatasoft v1 wraps fields; v2.1 does not.
    rows = [r["fields"] if isinstance(r.get("fields"), dict) else r for r in rows]
    return text, rows, listed


def _quote(text, field, value):
    for encoded in (json.dumps(value, ensure_ascii=False), json.dumps(value, ensure_ascii=True)):
        found = re.search(re.escape(json.dumps(field)) + r"\s*:\s*" + re.escape(encoded), text)
        if found and 12 <= len(found[0]) <= 600:
            return found[0]
    return ""


def import_saved(root, *, run_id, artifact_id, pool_id, source_id, mapping=None,
                 filters=None, offset=0, limit=500, preview=False, objective="", scene=""):
    pool._id(pool_id, "pool_id")
    pool._id(source_id, "source_id")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("BULK_WINDOW_INVALID")
    material = store.read_artifact(root, run_id, artifact_id)
    text, rows, listed = _json(material)
    mapping, filters = mapping or {}, filters or {}
    try:
        upstream_offset = int(parse_qs(urlsplit(material["origin_url"]).query).get("offset", ["0"])[0])
        if upstream_offset < 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("BULK_UPSTREAM_OFFSET_INVALID") from None
    allowed = {"name", "website", "address", "city", "postcode", "source_key", "indoor"}
    if set(mapping) - allowed or any(not isinstance(v, str) for v in mapping.values()):
        raise ValueError("BULK_MAPPING_INVALID")
    if not isinstance(filters, dict) or any(not isinstance(k, str) or not isinstance(v, list)
            or not v or len(v) > 100 or any(not isinstance(x, (str, int, float, bool)) for x in v)
            for k, v in filters.items()):
        raise ValueError("BULK_FILTER_INVALID: field to allowed literal values; no expressions")
    more_upstream = isinstance(listed, int) and listed > upstream_offset + len(rows)
    metadata = {"saved_rows": len(rows), "source_total_count": listed,
                "upstream_offset": upstream_offset,
                "upstream_next_offset": upstream_offset + len(rows) if more_upstream and rows else None,
                "upstream_pagination_remaining": more_upstream,
                "available_fields": sorted({k for row in rows for k in row}),
                "network_requests": 0, "artifact_id": artifact_id,
                "source_sha256": material["content_sha256"]}
    if preview:
        sample = [{key: str(value)[:100] for key, value in list(row.items())[:8]}
                  for row in rows[offset:offset + min(limit, 2)]]
        return {**metadata, "status": "LOCAL_JSON_PREVIEW", "sample_rows": sample,
                "untrusted_source_content": True}
    if not mapping.get("name"):
        raise ValueError("BULK_NAME_MAPPING_REQUIRED")
    page = rows[offset:offset + limit]
    if not page:
        return {**metadata, "status": "LOCAL_JSON_COMPLETE", "next_offset": None}
    identity = json.dumps({"sha": material["content_sha256"], "pool": pool_id,
        "source_label": source_id, "origin": material["origin_url"], "mapping": mapping,
        "filters": filters, "offset": offset, "limit": limit}, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
    source = "bulk-" + digest
    receipt_id = "bulk-import-" + digest
    from contextlib import closing
    with closing(store._connection(root)) as db:
        prior = db.execute("SELECT payload_json FROM records WHERE run_id=? AND record_id=?",
                           (run_id, receipt_id)).fetchone()
    if prior:
        return {**json.loads(prior[0]), "replayed": True}
    targets, excluded, candidates = [], [], []
    with closing(store._connection(root)) as db:
        known_venues = {r[0] for r in db.execute("SELECT entity_key FROM records WHERE run_id=? AND kind='venue'", (run_id,))}
    for index, row in enumerate(page, offset):
        if any(field not in row for field in filters):
            raise ValueError("BULK_FILTER_FIELD_MISSING: inspect the saved JSON schema first")
        if any(row[field] not in values for field, values in filters.items()):
            excluded.append({"index": index, "reason": "OUTSIDE_EXPLICIT_FILTER"})
            continue
        name = row.get(mapping["name"])
        quote = _quote(text, mapping["name"], name)
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or not quote:
            excluded.append({"index": index, "reason": "NAME_OR_PRIMARY_QUOTE_MISSING"})
            continue
        values = {k: row.get(v) for k, v in mapping.items()}
        address = " ".join(str(values.get(k) or "") for k in ("address", "postcode", "city")).strip()
        key = "venue-" + hashlib.sha256((material["origin_url"].split("?")[0] + ":" +
            str(values.get("source_key") or pool.target_key(name, address))).encode()).hexdigest()[:24]
        claims = [{"field": "identity", "artifact_id": artifact_id, "quote": quote}]
        if mapping.get("indoor"):
            indoor_quote = _quote(text, mapping["indoor"], values.get("indoor"))
            if not indoor_quote:
                excluded.append({"index": index, "reason": "INDOOR_PRIMARY_QUOTE_MISSING"})
                continue
            claims.append({"field": "indoor", "artifact_id": artifact_id, "quote": indoor_quote})
        facts = {k: v for k, v in values.items() if v is not None}
        facts.update(claims=claims, source_artifact_id=artifact_id)
        if key not in known_venues:
            candidates.append({"record_id": key + "-" + digest[:8], "entity_key": key,
                "kind": "venue", "stage": "entities", "facts": facts, "artifact_ids": [artifact_id]})
        targets.append({"name": name, "website": values.get("website") or "", "address": address,
            "kind": "venue", "venue_entity_key": key, "source_artifact_id": artifact_id,
            "source_row": index, "source_key": values.get("source_key"), "source_label": source_id})
    distinct = {}
    for candidate in candidates:
        rid = candidate["record_id"]
        if rid in distinct and distinct[rid] != candidate:
            raise ValueError("BULK_SOURCE_ID_CONFLICT: choose the correct facility identity field")
        distinct[rid] = candidate
    candidates = list(distinct.values())
    for at in range(0, len(candidates), 20):
        store.put_candidates_batch(root, run_id, candidates[at:at + 20])
    try:
        imported = pool.import_targets(root, pool_id=pool_id, source_id=source,
            source_url=material["origin_url"], provider=material["provider"],
            listed_count=len(page), targets=targets, objective=objective, scene=scene,
            excluded_count=len(excluded))
    except ValueError as exc:
        if not str(exc).startswith("POOL_SOURCE_ALREADY_IMPORTED"):
            raise
        # Recover a crash between the pool commit and receipt write.
        with closing(pool._db(pool._root(root))) as db:
            row = db.execute("SELECT imported,duplicates,rejected FROM pool_sources WHERE pool_id=? AND source_id=?",
                             (pool_id, source)).fetchone()
        imported = dict(imported=row[0], duplicates=row[1], rejected=row[2], reconciled=sum(row) == len(page))
    result = {**metadata, **imported, "status": "LOCAL_JSON_IMPORTED", "source_id": source,
        "processed_rows": len(page), "excluded": excluded[:20], "excluded_count": len(excluded),
        "exclusion_reasons": dict(Counter(r["reason"] for r in excluded)),
        "mapping": mapping, "filters": filters, "offset": offset,
        "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
        "replayed": False}
    store.put_candidate(root, run_id, receipt_id, "entities", "observation", receipt_id,
                        result, [artifact_id])
    return result

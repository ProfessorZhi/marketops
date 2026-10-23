"""The engine catalog is read lazily, pinned, and never trusted on read-back.

SerpApi's official MCP publishes ~100+ engine parameter schemas as MCP
resources. Loading them into a tool result would be the "expose every upstream
tool indiscriminately" failure the product brief names, so the contract is the
opposite: read the index, then one schema at a time, and cache each with the
hash it was read at. These tests use a fake MCP session — no vendor is
contacted, and no key is present.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import serpapi_engine_catalog as catalog_module


class FakeContent:
    def __init__(self, uri, text):
        self.uri = uri
        self.mimeType = "application/json"
        self.text = text


class FakeReadResult:
    def __init__(self, uri, text):
        self.contents = [FakeContent(uri, text)]


class FakeResource:
    def __init__(self, uri, uri_template=None, name=""):
        self.uri = uri
        self.uriTemplate = uri_template
        self.name = name


class FakeListResult:
    def __init__(self, resources):
        self.resources = resources


class FakeSession:
    """Duck-typed stand-in for ``mcp.ClientSession``. Counts every read."""

    def __init__(self, engines, schemas):
        self.engines = engines
        self.schemas = schemas
        self.resource_reads = []
        self.listed = 0

    async def list_resources(self):
        self.listed += 1
        return FakeListResult([
            FakeResource(catalog_module.INDEX_URI, name="Engine index"),
            FakeResource(None, catalog_module.SCHEMA_URI_TEMPLATE, name="Engine schema"),
            FakeResource("serpapi://something/else", name="Unrelated"),
        ])

    async def read_resource(self, uri):
        self.resource_reads.append(uri)
        if uri == catalog_module.INDEX_URI:
            return FakeReadResult(uri, json.dumps({"engines": list(self.engines)}))
        name = uri.rsplit("/", 1)[-1]
        return FakeReadResult(uri, json.dumps(self.schemas[name]))


class EngineIndexParsingTests(unittest.TestCase):
    def test_index_parses_the_shapes_the_vendor_could_send(self):
        for payload, expected in (
            ({"engines": ["google", "google_maps"]}, ("google", "google_maps")),
            (["google", "google_maps"], ("google", "google_maps")),
            ({"engines": [{"name": "google"}, {"engine": "google_maps"}]},
             ("google", "google_maps")),
            ({"engines": "google\ngoogle_maps\n"}, ("google", "google_maps")),
            ("google\ngoogle_maps\n", ("google", "google_maps")),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(expected, catalog_module.parse_engine_index(
                    payload if isinstance(payload, str) else json.dumps(payload)))

    def test_an_engine_name_that_could_not_be_a_resource_uri_is_dropped(self):
        payload = json.dumps({"engines": ["google", "google maps", "../etc", "", 7]})
        self.assertEqual(("google",), catalog_module.parse_engine_index(payload))

    def test_engine_uri_is_built_not_guessed(self):
        self.assertEqual("serpapi://engines/google_light",
                         catalog_module.engine_uri("google_light"))
        for bad in ("Google", "google/light", "google light", "", None):
            with self.subTest(engine=bad):
                with self.assertRaisesRegex(ValueError, "SERPAPI_ENGINE_NAME_INVALID"):
                    catalog_module.engine_uri(bad)


class CatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state" / "serpapi-engine-cache.json"

    def test_reading_the_index_does_not_read_any_schema(self):
        session = FakeSession([f"engine_{i}" for i in range(118)],
                              {f"engine_{i}": {"type": "object"} for i in range(118)})
        catalog = catalog_module.EngineCatalog(self.path)
        asyncio.run(catalog_module.discover_index(session, catalog=catalog))
        self.assertEqual([catalog_module.INDEX_URI], session.resource_reads)
        self.assertEqual(118, len(catalog.engine_names()))
        self.assertIsNone(catalog.cached_schema("engine_7"))

    def test_one_schema_is_read_once_and_then_served_from_cache(self):
        session = FakeSession(["google_light"], {"google_light": {"type": "object"}})
        catalog = catalog_module.EngineCatalog(self.path)
        first = asyncio.run(catalog_module.discover_schema(
            session, "google_light", catalog=catalog))
        second = asyncio.run(catalog_module.discover_schema(
            session, "google_light", catalog=catalog))
        self.assertEqual(["serpapi://engines/google_light"], session.resource_reads)
        self.assertEqual({"type": "object"}, first["schema"])
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual("CACHE_HIT", second["source"])

    def test_an_uncached_schema_is_absent_not_empty(self):
        catalog = catalog_module.EngineCatalog(self.path)
        with self.assertRaisesRegex(ValueError, "SERPAPI_ENGINE_SCHEMA_NOT_CACHED"):
            catalog.schema("google_light")

    def test_a_cache_edited_on_disk_is_refused_not_served(self):
        session = FakeSession(["google_light"], {"google_light": {"type": "object"}})
        catalog = catalog_module.EngineCatalog(self.path)
        asyncio.run(catalog_module.discover_schema(session, "google_light", catalog=catalog))
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        stored["engines"]["google_light"]["schema"]["type"] = "array"
        self.path.write_text(json.dumps(stored), encoding="utf-8")
        reloaded = catalog_module.EngineCatalog(self.path)
        with self.assertRaisesRegex(ValueError, "SERPAPI_ENGINE_CACHE_INTEGRITY"):
            reloaded.schema("google_light")

    def test_the_index_records_where_it_came_from_and_when(self):
        session = FakeSession(["google"], {"google": {}})
        catalog = catalog_module.EngineCatalog(self.path)
        recorded = asyncio.run(catalog_module.discover_index(session, catalog=catalog))
        self.assertEqual(catalog_module.INDEX_URI, recorded["source_uri"])
        self.assertEqual(1, recorded["engine_count"])
        self.assertRegex(recorded["sha256"], r"^[0-9a-f]{64}$")
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual("marketops.serpapi.engine-cache.v1", on_disk["schema"])
        self.assertNotIn("key", json.dumps(on_disk).lower())

    def test_the_summary_never_carries_a_schema_body(self):
        session = FakeSession(["google"], {"google": {"type": "object",
                                                     "properties": {"q": {"type": "string"}}}})
        catalog = catalog_module.EngineCatalog(self.path)
        asyncio.run(catalog_module.discover_index(session, catalog=catalog))
        asyncio.run(catalog_module.discover_schema(session, "google", catalog=catalog))
        summary = catalog.summary()
        self.assertEqual(1, summary["engine_count"])
        self.assertEqual(1, summary["cached_schema_count"])
        self.assertEqual(["google"], summary["engines"])
        self.assertNotIn("properties", json.dumps(summary))

    def test_staleness_is_reported_not_hidden(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)

        def clock():
            return now

        catalog = catalog_module.EngineCatalog(self.path, clock=clock, ttl_seconds=3600)
        catalog.record_index(("google",), source_uri=catalog_module.INDEX_URI)
        self.assertFalse(catalog.is_stale())
        later = catalog_module.EngineCatalog(
            self.path, clock=lambda: now + timedelta(seconds=3601), ttl_seconds=3600)
        self.assertTrue(later.is_stale())
        self.assertIn("FALLBACK", later.summary()["freshness_note"])

    def test_a_corrupt_cache_file_does_not_read_as_an_empty_catalog(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")
        catalog = catalog_module.EngineCatalog(self.path)
        with self.assertRaisesRegex(ValueError, "SERPAPI_ENGINE_CACHE_UNREADABLE"):
            catalog.engine_names()


class EngineResolutionTests(unittest.TestCase):
    """An unknown engine is refused locally, and the two "unknown"s differ.

    "this engine is not in the index" and "the index has not been read" look
    the same to a caller that only checks for absence, and they need different
    actions. Neither one touches the vendor.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.catalog = catalog_module.EngineCatalog(
            Path(self.temp.name) / "state" / "serpapi-engine-cache.json")

    def test_an_engine_outside_the_index_is_blocked_without_a_call(self):
        self.catalog.record_index(("google_light", "google_maps"))
        decision = catalog_module.resolve_engine("google_flights", catalog=self.catalog)
        self.assertEqual("BLOCKED", decision["status"])
        self.assertEqual("CAPABILITY_UNKNOWN", decision["reason"])
        self.assertEqual(0, decision["upstream_calls"])
        self.assertEqual("INDEX_READ", decision["index_state"])

    def test_an_unread_index_is_not_reported_as_an_absent_engine(self):
        decision = catalog_module.resolve_engine("google_light", catalog=self.catalog)
        self.assertEqual("BLOCKED", decision["status"])
        self.assertEqual("CAPABILITY_UNKNOWN", decision["reason"])
        self.assertEqual("INDEX_NOT_READ", decision["index_state"])

    def test_a_known_engine_without_its_schema_is_not_yet_available(self):
        self.catalog.record_index(("google_light",))
        decision = catalog_module.resolve_engine("google_light", catalog=self.catalog)
        self.assertEqual("BLOCKED", decision["status"])
        self.assertEqual("CAPABILITY_NOT_AVAILABLE", decision["reason"])
        self.assertEqual("INSERT_ENGINE_SCHEMA_RESOURCE_READ_PENDING", decision["next_step"])

    def test_a_known_engine_with_its_schema_resolves(self):
        session = FakeSession(["google_light"], {"google_light": {"type": "object"}})
        asyncio.run(catalog_module.discover_index(session, catalog=self.catalog))
        asyncio.run(catalog_module.discover_schema(session, "google_light",
                                                   catalog=self.catalog))
        decision = catalog_module.resolve_engine("google_light", catalog=self.catalog)
        self.assertEqual("READY", decision["status"])
        self.assertEqual("serpapi://engines/google_light", decision["source_uri"])

    def test_an_invalid_engine_name_is_blocked_rather_than_hashed_into_a_uri(self):
        decision = catalog_module.resolve_engine("Google Flight", catalog=self.catalog)
        self.assertEqual("BLOCKED", decision["status"])
        self.assertEqual("CAPABILITY_PARAM_NOT_ALLOWED", decision["reason"])


class DefaultLocationTests(unittest.TestCase):
    def test_the_cache_lives_under_the_customer_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MARKETOPS_RESEARCH_DATA_ROOT": tmp}):
                catalog = catalog_module.EngineCatalog.default()
        self.assertTrue(str(catalog.path).startswith(tmp))
        self.assertEqual("serpapi-engine-cache.json", catalog.path.name)


if __name__ == "__main__":
    unittest.main()

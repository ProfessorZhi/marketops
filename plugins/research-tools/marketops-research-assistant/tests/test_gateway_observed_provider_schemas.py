"""Validate shipped calls against actual 2026-10-20 non-billable tools/list."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_adapter_tinyfish as tinyfish
import research_capability_registry as registry
import research_runtime_setup as runtime

CAPTURE = json.loads((Path(__file__).parent / "fixtures" / "provider-schemas-20261020.json").read_text())


class ObservedProviderSchemaTests(unittest.TestCase):
    def test_bootstrap_accepts_both_tinyfish_core_calls_and_compact_serpapi(self):
        self.assertEqual(["tinyfish.fetch_content", "tinyfish.search"],
            runtime.review_schema("tinyfish", CAPTURE["providers"]["tinyfish"]))
        self.assertEqual(["serpapi.search"],
            runtime.review_schema("serpapi", CAPTURE["providers"]["serpapi"]))

    def test_fetch_defaults_satisfy_actual_required_iframe_flag(self):
        import jsonschema
        schema = next(x["inputSchema"] for x in CAPTURE["providers"]["tinyfish"]["tools"]
                      if x["name"] == "fetch_content")
        sent = []
        async def runner(tool, args):
            jsonschema.validate(args, schema)
            sent.append(args)
            return {"results": []}
        with patch.object(tinyfish, "approved_url", side_effect=lambda value, **kw: value):
            receipt = asyncio.run(tinyfish._run({"tool": "fetch_content",
                "arguments": {"urls": ["https://example.org/"], "format": "html"},
                "auth_mode": "", "drift": None, "runner": runner, "ceiling": None}))
        self.assertTrue(receipt["ok"], receipt)
        self.assertIs(sent[0]["iframe_links"], False)
        self.assertEqual("html", sent[0]["format"])

    def test_validator_and_registry_accept_iframe_boolean_and_reject_other_type(self):
        self.assertIn("iframe_links", registry.DEFAULT_REGISTRY.get("tinyfish.fetch_content").allowed_params)
        with patch.object(tinyfish, "approved_url", side_effect=lambda value, **kw: value):
            tinyfish.validate("fetch_content", {"urls": ["https://example.org/"], "iframe_links": False})
            with self.assertRaises(ValueError):
                tinyfish.validate("fetch_content", {"urls": ["https://example.org/"], "iframe_links": "false"})

    def test_search_registry_uses_observed_field_names_and_refuses_legacy_conflicts(self):
        allowed = registry.DEFAULT_REGISTRY.get("tinyfish.search").allowed_params
        self.assertTrue({"location", "language", "recency_minutes"} <= allowed)
        self.assertTrue({"gl", "hl", "recency"}.isdisjoint(allowed))
        tinyfish.validate("search", {"query": "public venues", "location": "US", "language": "en", "recency_minutes": 60})
        with self.assertRaises(ValueError):
            tinyfish.validate("search", {"query": "public venues", "gl": "GB", "location": "US"})


if __name__ == "__main__":
    unittest.main()

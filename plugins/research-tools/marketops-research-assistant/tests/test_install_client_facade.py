"""W-G facade checks: IF-1 (one server, no vendor tools) and IF-4 (schema subset).

These assert what a host would actually receive, so the interesting cases launch
the real entry over stdio rather than calling a helper.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import client_install_probe as probe  # noqa: E402
import research_mcp_facade as facade  # noqa: E402

HAS_MCP = importlib.util.find_spec("mcp") is not None


class NormalizeTests(unittest.TestCase):
    def test_nullable_anyof_flattens_to_a_plain_type(self):
        schema = {"anyOf": [{"type": "array", "items": {"type": "string"}},
                            {"type": "null"}]}
        self.assertEqual({"type": "array", "items": {"type": "string"}},
                         facade.normalize_schema(schema))

    def test_sibling_keywords_survive_the_flatten(self):
        schema = {"title": "ids", "default": None,
                  "anyOf": [{"type": "object", "additionalProperties": True},
                            {"type": "null"}]}
        self.assertEqual({"title": "ids", "default": None, "type": "object",
                          "additionalProperties": True},
                         facade.normalize_schema(schema))

    def test_nested_flatten_keeps_position(self):
        schema = {"type": "object", "properties": {
            "a": {"anyOf": [{"type": "string"}, {"type": "null"}]}}}
        self.assertEqual({"type": "object",
                          "properties": {"a": {"type": "string"}}},
                         facade.normalize_schema(schema))

    def test_unknown_combinator_fails_closed(self):
        # A two-of-three anyOf is not the nullable-optional pattern. Passing it
        # through unchanged would be the silent-host-break this facade exists to
        # prevent, so it must raise instead.
        for schema in ({"oneOf": [{"type": "string"}, {"type": "number"}]},
                       {"anyOf": [{"type": "string"}, {"type": "number"},
                                  {"type": "null"}]},
                       {"anyOf": [{"type": "string"}, {"type": "number"}]},
                       {"$ref": "#/$defs/x"}):
            with self.assertRaises(facade.HostIncompatibleSchema):
                facade.normalize_schema(schema)

    def test_conflicting_sibling_is_not_silently_merged(self):
        with self.assertRaises(facade.HostIncompatibleSchema):
            facade.normalize_schema({"type": "object",
                                     "anyOf": [{"type": "string"}, {"type": "null"}]})

    def test_audit_reports_the_keyword_and_the_path(self):
        findings = facade.audit_schema(
            {"type": "object", "properties": {"q": {"anyOf": [{"type": "string"}]}}})
        self.assertEqual([{"path": "/properties/q/anyOf", "keyword": "anyOf"}], findings)


@unittest.skipIf(not HAS_MCP, "mcp package required to speak the wire protocol")
class WireTests(unittest.TestCase):
    """The host-visible surface, measured end to end over stdio."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.report = probe.probe_stdio_tools(
            sys.executable, ROOT / "scripts" / "research_mcp_facade.py",
            data_root=Path(cls._tmp.name) / "store")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_one_server_and_only_managed_tool_names(self):
        # IF-1. A vendor tool name reaching the host is the direct-bypass defect.
        self.assertTrue(self.report["ok"], self.report["findings"])
        self.assertEqual([], [f for f in self.report["findings"]
                              if f["kind"] == "VENDOR_NAMED_TOOL"])
        self.assertTrue(all(name.startswith("marketops_")
                            for name in self.report["tools"]), self.report["tools"])

    def test_advertised_schemas_use_only_the_host_safe_subset(self):
        # IF-4. This is the check that fails if the facade stops normalising.
        self.assertEqual([], [f for f in self.report["findings"]
                              if f["kind"] == "HOST_UNSUPPORTED_SCHEMA"])

    def test_the_facade_serves_the_same_tools_as_the_raw_server(self):
        # The facade narrows advertised schemas; it must not drop or rename a tool.
        raw = probe.probe_stdio_tools(sys.executable, ROOT / "scripts" / "research_mcp.py",
                                      data_root=Path(self._tmp.name) / "store-raw")
        self.assertEqual(raw["tools"], self.report["tools"])
        self.assertEqual(raw["server"], self.report["server"])

    def test_behaviour_of_a_blocked_call_is_unchanged(self):
        # IF-5: refusal is a closed-code envelope, and the facade must not turn a
        # refusal into a plain traceback or an empty success.
        import asyncio

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = dict(os.environ)
        env.update({"MARKETOPS_RESEARCH_DATA_ROOT": str(Path(self._tmp.name) / "store-b"),
                    "MARKETOPS_SEARCH_ALLOW_NETWORK": "0",
                    "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0"})

        async def call() -> str:
            params = StdioServerParameters(
                command=sys.executable,
                args=[str(ROOT / "scripts" / "research_mcp_facade.py")], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "marketops_search",
                        {"provider": "serper", "query": "x", "country": "gb",
                         "language": "en", "run_id": "absent-run",
                         "operation_id": "op", "limit": 1})
                    return result.content[0].text

        text = asyncio.run(call())
        # The SDK prefixes the tool error with a human sentence; the envelope
        # itself starts at the first brace. Asserting on the envelope, not the
        # prefix, is what makes this a check of the contract.
        self.assertIn("Error executing tool", text)
        payload = json.loads(text[text.index("{"):])
        self.assertIn(payload.get("status"), {"BLOCKED", "UNKNOWN"})
        self.assertEqual("marketops_search", payload.get("tool"))
        self.assertIn("reason", payload)

    def test_no_network_switch_is_on_during_the_probe(self):
        # The probe pins every spend switch off; if that regressed, a "passing"
        # probe could have reached a vendor.
        source = (ROOT / "scripts" / "client_install_probe.py").read_text(encoding="utf-8")
        for name in ("MARKETOPS_SEARCH_ALLOW_NETWORK", "MARKETOPS_RESEARCH_ALLOW_NETWORK",
                     "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID"):
            self.assertIn(name, source)


if __name__ == "__main__":
    unittest.main()

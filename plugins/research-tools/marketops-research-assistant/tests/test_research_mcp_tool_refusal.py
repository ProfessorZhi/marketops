"""MCP tool-surface contract: an anticipated refusal must stay readable.

No provider call, no network, no credentials. The 2026-10-16 acceptance run
found that a designed refusal (empty Run, tampered source, immutable output)
escaped `marketops_export_xlsx` as an unhandled exception, so a client saw only
`Error executing tool marketops_export_xlsx` and lost the reason code that
`research_delivery.main` renders as {"status": "BLOCKED", "reason": ...}.
"""
from __future__ import annotations
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_mcp as mcp

EXPECTED_TOOLS = {
    "marketops_runs", "marketops_research_step", "marketops_experience_recall",
    "marketops_experience_feedback", "marketops_experience_summary",
    "marketops_setup_status", "marketops_setup_configure", "marketops_note",
    "marketops_capabilities", "marketops_start", "marketops_search", "marketops_crawl",
    "marketops_tinyfish", "marketops_serpapi_mcp_search", "marketops_tinyfish_agent",
    "marketops_tinyfish_agent_wait", "marketops_jev_choice", "marketops_propose", "marketops_status",
    "marketops_events", "marketops_export", "marketops_export_xlsx", "marketops_handoff",
    "marketops_experience_draft", "marketops_next", "marketops_propose_batch",
    "marketops_material_excerpt", "marketops_evidence_audit",
    "marketops_managed_call", "marketops_evidence_workbook",
}


class ToolRefusalTests(unittest.TestCase):
    def test_anticipated_refusal_becomes_a_readable_tool_error(self):
        from mcp.server.mcpserver.exceptions import ToolError

        @mcp._guarded
        def export_xlsx(run_id: str) -> dict:
            raise ValueError("NO_RESEARCH_ROWS_OR_SOURCE_ARTIFACTS: not a successful E2E")

        with self.assertRaises(ToolError) as ctx:
            export_xlsx("live-lincoln-20261016")
        payload = json.loads(str(ctx.exception))
        self.assertEqual("BLOCKED", payload["status"])
        self.assertEqual("export_xlsx", payload["tool"])
        self.assertIn("NO_RESEARCH_ROWS_OR_SOURCE_ARTIFACTS", payload["reason"])

    def test_file_conflict_is_a_refusal_not_a_crash(self):
        from mcp.server.mcpserver.exceptions import ToolError

        @mcp._guarded
        def write_delivery(delivery_id: str) -> dict:
            raise FileExistsError("DELIVERY_ID_ALREADY_EXISTS: exports are immutable")

        with self.assertRaises(ToolError) as ctx:
            write_delivery("accept-run-1")
        self.assertIn("DELIVERY_ID_ALREADY_EXISTS", json.loads(str(ctx.exception))["reason"])

    def test_unexpected_exception_is_not_swallowed(self):
        @mcp._guarded
        def broken() -> dict:
            raise RuntimeError("programming error must stay loud")

        with self.assertRaises(RuntimeError):
            broken()

    def test_build_server_registers_every_documented_tool(self):
        server = mcp.build_server()
        names = set(server._tool_manager._tools)  # noqa: SLF001 - registry is the contract
        self.assertEqual(EXPECTED_TOOLS, names)

    def test_server_reports_the_plugin_version(self):
        server = mcp.build_server()
        self.assertEqual("0.5.2", server.version)


if __name__ == "__main__":
    unittest.main()

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


class RefusalTextTests(unittest.TestCase):
    """A blocked call has to say *why*, using only what the answer carries."""

    def test_a_gate_refusal_names_the_gate(self):
        text = mcp._refusal_text({"reason_code": "RESEARCH_NETWORK_DISABLED",
                                  "s0_failed": ("run local runtime configure first",)})
        self.assertEqual("RESEARCH_NETWORK_DISABLED: run local runtime configure first", text)

    def test_a_ledger_refusal_keeps_the_caveats_the_gateway_recorded(self):
        # `managed_invoke` used to replace the gateway's caveats with one
        # constant, so a ledger refusal arrived as a bare `DUPLICATE_OPERATION`
        # with nothing behind the colon.
        text = mcp._refusal_text({"reason_code": "DUPLICATE_OPERATION",
                                  "s0_failed": (),
                                  "caveats": ("LEDGER_CODE:DUPLICATE_OPERATION_SIGNATURE",)})
        self.assertIn("LEDGER_CODE:DUPLICATE_OPERATION_SIGNATURE", text)
        self.assertIn("DUPLICATE_SEARCH_SAME_RUN", text)

    def test_an_empty_refusal_does_not_invent_a_reason(self):
        text = mcp._refusal_text({"reason_code": "CAPABILITY_NOT_AVAILABLE",
                                  "s0_failed": (), "caveats": ()})
        self.assertEqual("CAPABILITY_NOT_AVAILABLE: ", text)


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
        self.assertEqual("0.5.4", server.version)


if __name__ == "__main__":
    unittest.main()

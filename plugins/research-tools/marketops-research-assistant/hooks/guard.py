#!/usr/bin/env python3
"""Claude Code PreToolUse guard: research goes through the MarketOps tools only.

A zero-cost acceptance run spent 43 of 51 turns reading the plugin's Python to
reverse-engineer why a quote was refused, and an earlier one hand-wrote the
workbook. Both bypass the evidence gate. This hook refuses those moves and
says what to do instead. It never blocks anything outside the plugin's own
code, its research databases and workbook writing.
"""
from __future__ import annotations

import json
import os
import re
import sys

PLUGIN_ROOT = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
DATABASES = re.compile(r"research-(lite|pool|walls|events)\.sqlite3|usage-ledger\.sqlite3")
WORKBOOK_WRITERS = re.compile(r"\b(openpyxl|xlsxwriter)\b|\.to_excel\(")
PLUGIN_CODE = re.compile(r"(research_[a-z_]+|marketops_safe_egress)[/\\]?[a-z_]*\.py\b")

ADVICE = ("MarketOps rule: research only through the marketops_* tools. Do not read the plugin's "
          "code or databases to debug a refusal - the tool's caveat says what to fix (for a "
          "quote: copy the 'closest text on the page' it shows). Deliver only with "
          "marketops_handoff; never write the workbook yourself. If a target cannot be fixed, "
          "record HOLD and move on.")


def refused(reason: str) -> None:
    sys.stderr.write(f"BLOCKED ({reason}). {ADVICE}\n")
    sys.exit(2)


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    tool = event.get("tool_name", "")
    data = event.get("tool_input") or {}
    text = " ".join(str(v) for v in data.values() if isinstance(v, (str, int)))
    if PLUGIN_ROOT and PLUGIN_ROOT in text and tool in {"Bash", "Read", "Grep", "Glob"}:
        if re.search(r"[/\\]scripts([/\\]|\b)", text) or PLUGIN_CODE.search(text):
            refused("reading plugin source")
    if tool == "Bash" and (re.search(r"\b(import|from)\s+(research_|marketops_safe_egress)", text)
                           or (PLUGIN_CODE.search(text) and "python" in text)):
        refused("running plugin internals")
    if DATABASES.search(text) and tool in {"Bash", "Read", "Write", "Edit"}:
        refused("touching the research database directly")
    if tool == "Bash" and WORKBOOK_WRITERS.search(text):
        refused("writing a workbook by hand")
    if tool in {"Write", "Edit"} and str(data.get("file_path", "")).casefold().endswith(".xlsx"):
        refused("writing a workbook by hand")


if __name__ == "__main__":
    main()

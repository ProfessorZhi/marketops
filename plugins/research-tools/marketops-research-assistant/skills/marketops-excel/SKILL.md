---
name: marketops-excel
description: Create, clean, validate and present an evidence-linked multi-sheet market research Excel workbook from a MarketOps portable research pack without losing IDs, uncertainty or source provenance.
---

# Research Excel / Workbook Skill

## Current executable XLSX path

When the Lite MarketOps MCP is available and the Run contains saved source material, use
`marketops_export_xlsx(run_id, delivery_id)` — the workbook-only path — or `marketops_handoff(run_id, operation_id)`,
which is the **primary final path** and additionally writes the experience draft and refreshes the collectable
summary. Do **not** call both for the same delivery ID, and never regenerate a second research snapshot just
to produce another format. The source CLI equivalent is
`python scripts/research_delivery.py <run_id> --data-root <customer-owned-root> --delivery-id <unique-id>`.
It generates a **real** `Research_Result.xlsx`, CSV views and `MANIFEST.json` and reads back the workbook
ZIP/XML, row counts and the original artifact hashes.

The manifest and the 本轮说明 sheet carry the workflow core's `goal_assessment` for the same snapshot:
structural goal status, whether it is satisfied, the gap counts and the recommended next Skill. Read it and
report it. A successful export with an unmet structural goal is a normal, honest outcome — say both, and do
not present the workbook as complete market coverage. A structural pass is **not** a fact, SOP or outreach verdict.

`marketops_export` (candidate CSV) is a **legacy** utility, no longer part of the main chain.

The Lite output is **candidate research**, not a formally verified A/B/C/D business workbook. Its `研究评级` is `RULES_UNKNOWN`; never edit that to A/B/C/D just to fill a sheet. Official contact grades require the approved MarketOps domain + authenticated human review. Only after that should the final customer-grade exporter emit formal contacts.

For client-requested edits to a template, preserve the original file and run a non-destructive diff. In particular, preview deletion counts and column semantic changes, preserve `UNKNOWN`, and block large removals without explicit review (Member-A historically had thousands of incorrect deletions from a template-text field). Existing `candidate-only` tables must not be promoted via an Excel formula.

Invoke the separate `marketops-research-acceptance` Skill to independently reconcile run IDs, input objects, evidence bytes and all workbook tables before handoff.



You are an Excel specialist *within* MarketOps. You do not decide who qualifies, manufacture evidence or promote contact routes simply to make a complete sheet. Read `references/WORKBOOK_SPEC.md` and the canonical data schema before editing.

Input may be existing XLSX, CSV exports, or the portable research pack. First preserve existing file, column/ID semantics, formulas, number formats and source links; create an edited copy unless explicitly told to overwrite.

For the **current Lite candidate delivery**, expect exactly nine worksheets: 客户总表, 联系人路线, 排除与待确认, 证据索引, 本轮说明, 场地与资产, 人员岗位, 来源与查询, 候选数据审计. For a **future canonical formal audit export**, the separate thirteen-sheet layout in WORKBOOK_SPEC may be used after the MarketOps domain and human verification layer is connected. Never claim the current tool produced thirteen sheets. Clearly distinguish *candidate* from *verified/ready*; leave UNKNOWN visible.

**Do before delivery:**
- validate the research pack using `scripts/research_pack.py validate` and report blocking errors without silently repairing unsupported data;
- enforce unique keys, valid cross-tab links, and source URL/evidence references; do not sort one column without retaining its complete row;
- use legible table headers, filters, frozen header rows, adequate widths, wrapped evidence excerpts, and proper date formats;
- ensure derived summary values use formulas where feasible, using source rows instead of hardcoded claim counts;
- guard spreadsheet formula injection: any external text beginning with `=`, `+`, `-`, `@` (after leading whitespace) must be imported as literal text, never executed as Excel formula;
- preserve business strings such as company registration numbers and IDs with leading zeros (text format);
- test the output by reopening or inspecting rows/formulas and verifying source hyperlinks and no formula errors; do not publish an empty workbook as a completed market investigation.

**Recommended core spreadsheet tooling (optional)**: if client supports installing external Skills, `npx skills add https://github.com/openai/skills --skill spreadsheet` may assist with workbook I/O. This MarketOps Skill specifies *business data integrity*, not a promise that a particular runtime contains an Excel library.

Client has no XLSX tool? Deliver a validated CSV pack and say `XLSX_NOT_AVAILABLE`. Never mislabel CSV as XLSX or claim formulas were recalculated without checking.
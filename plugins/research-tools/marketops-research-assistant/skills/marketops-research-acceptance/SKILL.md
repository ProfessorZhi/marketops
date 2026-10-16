---
name: marketops-research-acceptance
description: Independent acceptance of a bounded research Run and its XLSX/CSV/experience handoff; reconcile inputs, saved evidence, candidate accounts and public contact routes without mistaking a file, status or unreviewed person for business success.
---

# MarketOps research acceptance / 独立研究验收

This Skill is used at **every complete or partial research handoff**, not only when a spreadsheet happens to exist. Read references/BUSINESS_PLAYBOOK.md, references/QUALITY_GATES.md, references/WORKBOOK_SPEC.md, and references/E2E_VERIFICATION.md.

1. Identify *one* named Run and one frozen scope/SOP revision. Read back its real SQLite store and operation events. Missing data or a different Run is a blocker, not an implicit empty result.
2. Reconcile actual inputs and outcomes by **unique entity IDs**. Compare source hit, acquisition, venue, organization, person/department, role, contact point, route proposal and review counts, including held/rejected/unchanged. A page may yield several venues; do not subtract incomparable units.
3. Verify source material on disk by reading its bytes and recomputing SHA-256. Material marked `provider_extracted` must not be described as raw source bytes. Confirm that the person's organizational association and **literal public email** appear in attributed material; an email pattern or search snippet is not enough.
4. Validate **two separate gates**: customer eligibility per Scene and route A/B/C/D grading. An ungraded route, no business SOP, absent human approval or only a local Lite candidate MUST remain `RULES_UNKNOWN`, `HOLD` or `CANDIDATE_NOT_APPROVED`. Do not infer buyer authority from an operational inbox.
5. If running the current Lite MCP, use `marketops_export_xlsx(run_id,delivery_id)` to create an actual audited 8-sheet XLSX plus per-sheet CSV and manifest. Re-open workbook, verify headers, row counts, source links, ID identity, all held/unknown rows and file hashes. The Lite workbook is **partial candidate research**, not formal contact certification.
6. Generate an Experience Draft with the retrospective Skill from real operation receipts. Record any failed handoff, duplicate attempt, missing evidence, conflict, bad parse or provider escalation as a scoped incident. Do not silently edit historical measurements or approve a new SOP.
7. Report outcome in three **separate** axes: `TECHNICAL_ARTIFACT_PASS/FAIL`, `BUSINESS_RESEARCH_VERIFIED/PARTIAL/NOT_TESTED`, `PROVIDER_AND_CLIENT_E2E_TESTED/NOT_TESTED`. A green test fixture cannot stand in for live provider calls, real client MCP handshake, or approved business routes.

Hard stop: any missing or tampered artifact, inconsistent Run, bad cross-object ownership, an inexplicable input-to-output gap, or inability to open the XLSX. Human-specific procurement thresholds and outbound approvals remain outside this Skill.

**Never claim E2E completion from a successful MCP tool return alone.** Genuine E2E requires a real bounded request, source acquisition, durable storage, source-based factual assessment, business-specific review, generated workbook, workbook readback and actual client/provider receipts. If only the source-to-candidate-XLSX portion passed, name that exact subset.

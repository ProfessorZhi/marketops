# MarketOps Research Assistant — full E2E verification ladder

**This document is a test plan with executable commands, not a claim that a full real-client E2E has passed.** The Lite plugin and the canonical MarketOps autonomous workflow are two different deliverables. This plan tests only the former.

## Levels of proof

| Level | Must have | Can claim |
|---|---|---|
| L0 source | Skill Markdown, Python, manifests present | code authored |
| L1 offline | `unittest`, compilation and static preflight pass on actual interpreter | offline candidate integration is healthy |
| L2 real public source | Official page fetched over actual HTTPS; source bytes stored with SHA; source, venue, organization and public department contact candidate persisted; XLSX/CSV read back | **real-public-source → candidate data → real XLSX** only |
| L3 official paid providers | Each required Serper/SerpApi, Crawl4AI, TinyFish, Jev capability produces authentic receipts with customer-owned approved credentials and budgets | named Provider path verified (not all four unless individually tested) |
| L4 formal business | Current SOP version, account qualification, human review and formal A/B/C/D ContactRoute via authorized MarketOps business domain, with evidence and idempotence | verified business research output |
| L5 real agent clients | Real Codex and WorkBuddy MCP tools/list + tools/call, full Run/handoff, true XLSX workbook, no silent rows, experience draft | complete semi-automatic product E2E |

An `L2` passing job must never be rebranded as `L5`. The live test is explicitly **read-only**, limited to a City of Lincoln Council official public venue/contact page, and **does not call a billable third-party Provider**. It checks real public content changes fail clearly, instead of manufacturing a success receipt.

## Offline test

```bash
cd plugins/research-tools/marketops-research-assistant
python -m compileall -q scripts tests
python -m unittest discover -s tests -v
python scripts/preflight.py
```

The new regression tests validate 9-sheet XLSX/CSV creation, saved-artifact SHA reread, document/email literal evidence, wrong Run and idempotent immutable output, and a route staying ungraded without an authorized business verdict.

## Real public source test (manual opt-in)

```bash
MARKETOPS_LIVE_PUBLIC_E2E=1 python -m unittest discover -s tests -p test_research_live_e2e.py -v
```

The test reads `https://www.lincoln.gov.uk/people-community/community-sport-leisure-facilities/4`, requires observable West Common cricket pitch and the department's published work email; creates the Run, records raw HTTP response, writes candidate organization/venue/department route and exports real XLSX, then reopens it and tests the exact evidence/grade status. No post/email/form submit. If site blocks fetching or no longer publishes the data, the test **fails** and records the external dependency; changing the assertion to match a guess is not an acceptable fix.

## Formal business E2E still missing

The present `marketops_propose` intentionally does not accept `verified`, `ready` or `reviewed_by`. Adding `marketops_export_xlsx` does not solve that business gate. Completing L4 requires connecting the existing MarketOps qualified Account / Venue / Evidence / ContactRoute application use cases with the authorized human reviewer and current SOP. A reviewer-name string inside a Lite JSON is not identity proof.

The canonical exporter under `marketops/adapters/xlsx.py` and `marketops/application/export.py` already exists on integration branches, including the five formal business views. The self-contained copy in `scripts/_xlsx.py` is **only the serializer**: no qualification or ranking rules have been duplicated.

## Actual files and status

`marketops_export_xlsx(run_id, delivery_id)` writes to a customer-owned data root only:

```text
exports/<run-id>/<delivery-id>/
  Research_Result.xlsx
  客户总表.csv
  联系人路线.csv
  排除与待确认.csv
  证据索引.csv
  本轮说明.csv
  场地与资产.csv
  人员岗位.csv
  来源与查询.csv
  候选数据审计.csv
  MANIFEST.json
```

`MANIFEST.json` includes actual counts and SHA-256 for each file. No source bytes or customer data should be added to Git. All routes are explicitly `RULES_UNKNOWN` / `CANDIDATE_NOT_APPROVED` until formal review. `Research_Result.xlsx` is a candidate research workbook, not evidence that all target contacts have been found or that messages can be sent.

After each pilot, update `RELEASE_GATE.md` with actual CI run ID, SDK/python versions, actual provider receipt IDs and reviewed deliverable hash. Never fill checkboxes without observing execution.

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

## Measured on 2026-10-16 (authorized isolated runner)

Device `tokyo-shared-dev-01`, branch base `7491730`, Python 3.12.14, `mcp==2.3.0`.

| Level | Command | Observed result |
|---|---|---|
| L1 | `python -m compileall -q scripts tests` | rc 0 |
| L1 | `python -m unittest discover -s tests -v` | 68 tests, `OK (skipped=1)` |
| L1 | `python scripts/preflight.py` | rc 0, `STATIC_PASS_NOT_RUNTIME_VERIFIED` |
| L2 | `MARKETOPS_LIVE_PUBLIC_E2E=1 python -m unittest discover -s tests -p test_research_live_e2e.py -v` | 1 test, `OK` |
| L2 | separate 4-source acceptance run over real `www.lincoln.gov.uk` HTTPS pages | 28/28 checks PASS; real 9-sheet XLSX, per-file SHA-256 matched `MANIFEST.json`; DB ↔ workbook counts agreed; a byte-flipped source copy was refused with `ARTIFACT_READBACK_HASH_MISMATCH` |
| L3 | any Serper/SerpApi/Crawl4AI/TinyFish/Jev call | NOT RUN — not authorized, no key read |
| L4 | formal A/B/C/D route | NOT CONNECTED — see below |
| L5 | real Codex / WorkBuddy client handshake | NOT RUN |

The GitHub Actions red X on this branch is **not** a Python result. Run `38833245882` carries the check-run annotation
"The job was not started because recent account payments have failed or your spending limit needs to be increased";
every job has `runner_name: ""`, `steps: []` and a 3-second span, and `main` fails identically. This ladder was therefore
measured on the authorized runner instead.

L4 boundary, proven by execution rather than by reading this document: the Lite MCP refuses every promotion-shaped
field (`verified`, `ready`, `approved`, `reviewed_by`, `send_authorized`) at the store layer, exposes no grade/approve/
reviewer verb in its 13-tool surface, imports nothing from the formal `marketops` package, and renders a requested
`contact_route` grade as an explicit `Lite 暂存路线不能执行正式 A/B/C/D 晋升` issue. The formal `Grade` machinery
(`marketops/domain/enums.py`, `marketops/application/grading.py`) exists and requires a non-empty `approved_by` with
`approved_at`; it is a separate surface this plugin does not reach. Do not promote a Lite row to a formal route.

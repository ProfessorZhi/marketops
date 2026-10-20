# MarketOps 0.5.6-rc4 — write-time version guard and gap-driven sources (DRAFT)

> Historical RC4 body. The current candidate is 0.5.6-rc5; see
> [`PR-BODY-RC5.md`](PR-BODY-RC5.md) and [`RC5-INTEGRATION.md`](RC5-INTEGRATION.md).

**Status: DRAFT — not a release. Do not merge to `main`. Do not overwrite the
existing `0.5.5`, `0.5.6-rc1`, `0.5.6-rc2` or `0.5.6-rc3` ZIPs.**

Same PR #22 branch `rc/marketops-0.5.6-rc2-20261020`; product version
`0.5.6-rc4`. No new PR/branch, no force-push, no `main`.

## Source revisions

| Role | SHA |
| --- | --- |
| Lane H base | `38dcef853f526f64f669c475c96ad9c3bad3ad47` |
| RC3 verified head | `1bd84ac25295f1e1db28287359887072c36b05fa` |
| F money gate | `a3e45df134bc88305a7647d3f8f9f646be317450` |
| G browser egress | `f977c6a149235b68b1388d2bd793f71d86675615` |
| PR9 baseline | `4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44` |

## Core A — supersedes is checked before the row lands

`research_lite_store._insert_candidate` now validates every `facts.supersedes`
inside the write transaction: same run, kind, stage and entity_key; parent
exists; parent is the unique current tip; no cycle; review target identity is
unchanged; one live disposition per target. Refusals are stable codes with a
repair, e.g.:

```text
SUPERSEDES_ENTITY_KEY_MISMATCH: reuse entity_key from <old ID>
SUPERSEDES_NOT_CURRENT_TIP: previous record <old> is not the current tip; append against <tip>
TARGET_DISPOSITION_ALREADY_EXISTS: ... append a revision with facts.supersedes=<old> and reuse its entity_key
```

The old rows are never updated or deleted, caller facts are never rewritten, and
there is no cascade. The seven Essex reviews that used a new entity_key are
refused at write time instead of surfacing as seven export conflicts.

Controls: 17 tests (`test_rc4_supersedes_write_guard.py`), including the exact
Essex shape, legal same-entity corrections, cross-run/unknown/kind/stage/target
mismatches, non-tip parent, duplicate disposition, idempotency, batch rollback,
concurrent corrections and an existing historical fork left untouched.

## Core B — gap-driven source selection (offline)

New `research_source_selection.py`, returned by `marketops_next` as
`source_selection` / `next_best_source_action`. It reads only local rows and
recorded `evidence_links`; it does not search, spend, increase quota or retry an
UNKNOWN charge.

It reports `missing_fields` and a prioritized action per active candidate:
`organization_identity`, `venue_identity`, `venue_org_relationship`,
`contact_binding`, `hourly_rate`, `official_email`, `phone`,
`named_person_contact_chain`. It prefers official rate/booking/contact/venue
pages, excludes blocked domains, ignores shared directory pages as attribution,
and under a scarce budget finishes the most complete candidate before opening a
new one. A successful fetch or attached artifact is **not** a verified field.
The budget snapshot reads the runtime-config managed ceiling first and only then
the legacy env; an unreadable value stays `None`, never a false 0.

Controls: 15 tests (`test_rc4_source_selection.py`) plus the `marketops_next`
exposure assertion.

## Verification (offline; real provider calls = 0)

| Gate | Result |
| --- | --- |
| Plugin full offline suite | `Ran 2255 tests`, 0 failures, 0 errors, 30 skipped |
| supersedes write guard | 17 tests OK |
| source selection | 15 tests OK |
| targeted workflow/MCP | OK |

Version is `0.5.6-rc4` across manifests, builder, installer, MCP server, docs and
test expectations. No new customer ZIP was generated; the existing rc3 ZIP and
the running acceptance directories are untouched.

## Blockers / honest reds

- **Real Essex trial was not run by this development session.** The 8 UNKNOWN
  charges were not changed to 0, not released, not expanded and no provider was
  called.
- **No official QA signature**: `qa_verified=false`; release gate remains
  BLOCKED / NOT_RELEASE_READY. No self-signing and no reuse of the old rc3
  BUILD_QA receipt with the new SHA.
- Root `ci` keeps the same 23 pre-existing failures as `main@63a5fe48` and PR9.
- QA #15 frozen fixture remains 6/9 (missing org-name+value span); the QA
  artifact was not edited.
- OS-level UDP isolation UNPROVEN; `gitleaks` CLI absent.

## Rollback

RC3 head `1bd84ac2`; RC4 is an appended commit on the same branch. Revert it to
return to RC3; no history rewrite, no Lane branch change, no ZIP overwrite.

# MarketOps 0.5.6-rc5 — review-type guard and evidence-backed gaps (DRAFT)

**Status: DRAFT — not a release. Do not merge to `main`. Do not overwrite the
existing `0.5.5`, `0.5.6-rc1`…`rc4` ZIPs.**

Same PR #22 branch `rc/marketops-0.5.6-rc2-20261020`; product version
`0.5.6-rc5`. No new PR/branch, no force-push, no `main`.

## Source revisions

| Role | SHA |
| --- | --- |
| Lane H base | `38dcef853f526f64f669c475c96ad9c3bad3ad47` |
| RC4 head | `6a6f4f4e48cbde47ff6d46a302c41238ee5d631f` |
| F money gate | `a3e45df134bc88305a7647d3f8f9f646be317450` |
| G browser egress | `f977c6a149235b68b1388d2bd793f71d86675615` |
| PR9 baseline | `4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44` |

## P0 — a note cannot replace a disposition

`review_type=note` with `facts.supersedes=<live HOLD disposition>` used to become
the live tip because run/kind/stage/entity_key matched. The HOLD disappeared and
a held venue moved from 0 to 1. `_validate_supersedes` now forces, for every
`kind=review` supersedes:

```text
parent.review_type == child.review_type
```

Otherwise it returns `SUPERSEDES_REVIEW_TYPE_MISMATCH` with the parent type and a
repair. Target-disposition revisions still require the same target; a review
without a review_type cannot replace a disposition, and a disposition cannot
replace a note.

Reproducer before the fix: note insert succeeded, venue 0→1. After: refused with
`SUPERSEDES_REVIEW_TYPE_MISMATCH`, venue stays 0. Batch atomicity keeps the
valid item from committing. Same-type HOLD→RESEARCHED_CANDIDATE→second revision
passes and keeps all three rows in the audit. Controls: 6 tests.

## P1 — rate / phone / contact binding need linked quotes

`research_source_selection._missing_fields` no longer counts:

- a raw `hourly_rate` field with no claim/link;
- a claim without a matching `evidence_links` field;
- a quote that does not contain the stored number;
- a phone record with no linked quote or no org attribution;
- a generic `links.relationship` with no quote naming the org and contact.

`hourly_rate` needs value + linked rate field + quote support for the number.
`phone` needs a phone contact + linked quote containing the number + valid org
attribution (same local span, or a named relationship record). Contact binding
needs a named quote, not a generic link. Missing values remain in
`missing_fields` and keep the official rate/contact page recommendation.
Blocked domains, zero budget, UNKNOWN-held and pure-advisory behaviour are
unchanged. Controls: 10 tests.

## Verification (offline; real provider calls = 0)

| Gate | Result |
| --- | --- |
| Plugin full offline suite | `Ran 2271 tests`, 0 failures, 0 errors, 30 skipped |
| review_type guard | 6 tests OK |
| source evidence | 10 tests OK |
| Targeted release gate | G19/G20 PASS; G15 BLOCKED (no independent QA) |

Version is `0.5.6-rc5` across manifests, builder, installer, MCP server, docs and
tests. No new customer ZIP; old ZIPs and the running acceptance directories are
untouched.

## Blockers / honest reds

- **14 UNKNOWN charges** (RC3 8 + 0.5.5 6) untouched; no settlement, no provider
  call, no quota change, no `ALLOW` fabrication.
- No official independent QA signature; release gate remains BLOCKED /
  NOT_RELEASE_READY. No self-signing, no reuse of the old BUILD_QA receipt.
- Root `ci` keeps the same 23 pre-existing failures as `main@63a5fe48` and PR9.
- QA #15 frozen fixture remains 6/9; OS UDP unproven; gitleaks CLI absent.

## Rollback

RC4 head `6a6f4f4e`; RC5 is an appended commit on the same branch. Revert it to
return to RC4; no history rewrite, no ZIP overwrite.

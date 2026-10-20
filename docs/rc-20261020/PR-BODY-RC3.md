# MarketOps 0.5.6-rc3 — P0 evidence fixes and automatic client view (DRAFT)

**Status: DRAFT — not a release. Do not merge to `main`. Do not publish or overwrite
the existing `0.5.5`, `0.5.6-rc1` or `0.5.6-rc2` ZIPs.**

This PR keeps the single product branch `rc/marketops-0.5.6-rc2-20261020` and
advances the product version to `0.5.6-rc3`. The branch name is unchanged on
purpose: a version bump does not create a second development branch.

## Source revisions

| Role | SHA |
| --- | --- |
| Lane H base | `38dcef853f526f64f669c475c96ad9c3bad3ad47` |
| RC2 verified head | `be77fe0de9527afe3ecc6a136b579a06d0c2fb14` |
| F money gate | `a3e45df134bc88305a7647d3f8f9f646be317450` (integrated `57fd5036`) |
| G browser egress | `f977c6a149235b68b1388d2bd793f71d86675615` (integrated `b6e66e96`) |
| PR9 baseline | `4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44` |

## P0 fixes

### P0-1 — a quote must say the value

`research_delivery.evaluate_admission` no longer admits a record because *some*
linked quote exists. Organisation and venue names must appear in the record's own
linked, SHA-verified `identity` quote. Every displayed person/role field
(`name`, `department`, `role_title`/`role_function`) needs a linked quote that
actually contains it. Matching is a complete contiguous token sequence, so
`Acme Natural Grass` is not supported by `Acme Natural Grass Ltd`, and a short
single-token alias cannot carry an identity. Unrelated quotes are refused.

### P0-2 — a shared page is not ownership

`_binding_supported` no longer returns true on `record.artifact_ids &
organization.artifact_ids`. Binding now requires either:

1. one local quote containing the organisation name **and** the record's own
   value (contact literal, venue/person name, role title); or
2. an admitted `relationship` record that names this record by id/entity key and
   whose `relationship` quote contains the organisation name and the value.

A public directory page listing several organisations therefore cannot attach
A's email to B. Same-page two-organisation controls, short alias/partial-name
controls, cross-organisation person/role controls and a long-page four-positive
control are in `tests/test_rc3_evidence_p0.py`.

### Write-time warning

`research_lite_store._span_caveat` now warns when an organisation/venue has a
linked claim but no `field=identity`, and when a relationship has a linked claim
but no `field=relationship`. The message names the missing field and the repair;
the writer never rewrites the caller's claims.

### Automatic client view

`research_delivery.write_delivery` — the function behind the real MCP handoff and
`marketops_export_xlsx` — now calls `research_client_view.write_client_view` from
the filtered business sheets, inside the delivery transaction. The top-level
`MANIFEST.json` carries every `client-view/*` byte count and SHA256, and
`client_view.manifest_sha256`. A failed view removes the whole staging tree, so
no partial delivery is left behind. Status remains
`PARTIAL_CANDIDATE_ONLY_NOT_FORMAL_CONTACTS` with `send_authorized=false` and
`formal_sop_claimed=false`. The 9 internal workbook sheets and audit tables are
unchanged.

### Independent QA gate

`SOURCE_MANIFEST.qa_verified=false` now makes **both** report layers BLOCKED /
NOT_RELEASE_READY. `check_release_hygiene.py` keeps `H12-independent-qa`.
`check_release_gate.py` now separates `technical_checks_pass` from
`release_ready`; its `ok` is true only for overall PASS. Even when every ZIP/hash
check passes, missing `qa_verified` or a missing external signature makes
`G15-release-readiness` BLOCKED, `ok=false`, overall `BLOCKED`, exit 2.
`build_source` only sets `qa_verified=true` when an external `independent_qa`
block verifies as an Ed25519 signature against `MARKETOPS_QA_PUBLIC_KEY`. A
receipt the tree can generate for itself stays `SOURCE_PACKAGE_PREVIEW_BUILT`.

### live public E2E binding

The live fixture no longer assumes that citing the same council page binds a
venue/contact. It searches the **saved real page** for a 12–600 character
contiguous span containing the organisation name and the venue name/email. When
one exists it writes a named `relationship` record with that exact quote; when
none exists it asserts the honest fail-closed outcome: organisation admitted,
venue/contact HOLD with `ORGANIZATION_PROVENANCE_MISMATCH`, and both absent from
the client view. The shared-artifact rule is not restored and the test is not
skipped/xfailed.

## Verification (offline; real provider calls = 0)

| Gate | Result |
| --- | --- |
| Plugin full offline suite | `Ran 2212 tests`, 0 failures, 0 errors, 30 skipped |
| P0 evidence controls | 19 tests OK (4 legitimate long-page positives) |
| Release QA controls | 6 tests OK |
| Gate readiness controls | 8 tests OK |
| Targeted release gate | technical checks PASS; `G15-release-readiness` **BLOCKED**, `ok=false`, overall **BLOCKED / NOT_RELEASE_READY**, exit 2 |
| Release hygiene | technical checks 11/11 PASS; `H12-independent-qa` BLOCKED; overall **BLOCKED / NOT_RELEASE_READY** |
| Automatic client view | 4 admitted / 4 held; `client-view/` 8 files; hashes match |

Final artifact hashes are recorded in the evidence directory and in the PR
evidence comment. They are deliberately not hard-coded in the tree.

## Blockers / honest reds

- **Independent QA is not established.** No externally signed receipt/public key,
  so `qa_verified=false`; this PR does not claim formal QA.
- **QA #15 frozen fixture:** 3 of 9 fail because its “legit” venue/contact/person
  quotes do not contain the organisation name and value in one span. The QA
  artifact was not edited and the product gate was not weakened. QA must upgrade
  the fixture or add a named relationship.
- **live-readonly CI fixture:** the real-page fixture has the same missing span;
  its venue/contact are expected to enter HOLD under RC3, so the old count
  assertions are expected to fail. Real research must be run by the independent
  acceptance session against a fixture that carries the required span.
- **OS-level UDP isolation UNPROVEN** (no packet capture); gitleaks CLI absent, so
  the regex gate is not a gitleaks pass.
- Root `ci` retains the same 23 pre-existing failures as `main@63a5fe48` and
  PR9 `4f87bc33`; no test was changed or skipped to hide them.

## Rollback

- RC2 head: `be77fe0de9527afe3ecc6a136b579a06d0c2fb14`.
- RC3 changes are appended commits on the same branch; no history rewrite, no
  force-push, no Lane branch change. Revert the appended commits to return to RC2.

## Review focus

1. `_quote_supports` token/suffix rules and the two binding shapes.
2. `write_delivery` atomicity when the client view fails.
3. `verify_independent_qa` canonical payload and key handling.
4. Whether any existing fixture was changed in a way that weakens evidence
   (the changed fixtures add organisation names to the quoted spans; they do not
   remove checks).

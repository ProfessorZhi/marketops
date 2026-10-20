# Draft: MarketOps Research Plugin RC — 2026-10-20

**Status: DRAFT — not a release. Do not merge to `main`. Do not publish the 0.5.5 zip.**

- Base: `feat/bruce-plugin-delivery-20261020`
- Baseline commit: `4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44`
- Head: `rc/marketops-release-candidate-20261020`
- Scope: integrate five independent Lanes and one independent QA Lane; no production,
  no real Provider, no paid call, no real customer email.

## What this RC integrates

| Lane | Source branch | Source SHA | RC SHA | Targeted evidence |
| --- | --- | --- | --- | --- |
| A delivery admission gate | `fix/marketops-rc-delivery-gate-20261020` | PENDING | PENDING | PENDING |
| B client view | `fix/marketops-rc-client-view-20261020` | `76991cb76ec3b56e70732ea898e1b6458b271202` | `b07499368e75c0f9c859f35ba898fd8f1e9ed932` | 11 tests OK |
| C installer opt-in | `fix/marketops-rc-installer-20261020` | `ed4836ba32f17378e61563f2493c5c0fd08beb4a` | `533d706eddb99c96e38e34312bdef89c6c3f4f16` | 21 tests OK |
| D search transport dedup | `fix/marketops-rc-search-cost-20261020` | PENDING | PENDING | PENDING |
| E Jev evidence locator | `fix/marketops-rc-jev-target-20261020` | `da3005018ac625fec02c22f18435f075e60aeddc` | `38c717df2a211e90953a46fdce479cab1ee810c1` | 19 tests OK |

The machine-readable ledger is `docs/rc-20261020/LANE-LEDGER.md`; the manifest is
`docs/rc-20261020/lanes.json`. Cherry-picks use the exact SHA above, never a branch tip.

## What is verified (see `docs/rc-20261020/RC-TEST-MATRIX.md`)

- **Client view is generated from the filtered delivery output** — A's four business
  sheets feed B's `write_client_view`, then `verify_client_view` re-reads and hashes
  every file. The nine-sheet delivery is never passed wholesale.
- **P0 fabricated data stays out of client business sheets** — an organisation with a
  name but no matching excerpt, a venue with no source, an email that is not a literal,
  and an email without a quote are all held; they remain visible in `排除与待确认` and
  `候选数据审计`.
- **A qualified sample still enters** — the positive fixture yields 4/4 admitted, so the
  gate is not a blanket refusal.
- **Release manifest/hash** — `build_source()` and the connector bundle are built outside
  the repository; per-file SHA-256, `.sha256` sidecar and ZIP hash agree, and the bundle
  actually contains `research_client_view.py`.
- **Installer is explicit opt-in** — no `--apply` means no writes; non-interactive run
  without a destination fails closed; global Codex registration defaults to no.
- **SerpApi does not reserve twice** — REST and embedded official MCP resolve to one
  canonical operation signature; one reservation per logical query.
- **Jev locator** — `evidence_query` is a bounded locator, must match saved bytes, never
  degrades to the head, and is refused for box capabilities.

## Deliberately not done

- No merge to `main`, no release, no deployment, no production restart.
- No overwrite of the existing `0.5.5` ZIP; RC artefacts live outside the repo.
- No real Provider, paid call, paid search, or outbound email.
- No private data: fixtures use synthetic `example.org` / `example.net` only.

## Open QA blockers

See `docs/rc-20261020/QA-BLOCKERS.md`. At the time of writing: A and D had not yet
delivered commits; the full offline plugin suite had not been run (deliberately, to
avoid running it concurrently with Lane agents); the synthetic private-key fixture in
Lane B's test is classified REVIEW, not FAIL.

## Review notes

- Conflicts stop the integration: the in-flight cherry-pick is aborted and the RC branch
  is left unchanged. No reset, clean, force-push, or worktree deletion is used.
- If a Lane delivers more commits after this PR opens, they are added as additional
  exact-SHA ledger rows rather than rewriting history.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

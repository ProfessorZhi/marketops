# MarketOps 0.5.6-rc2 — final delivery candidate (DRAFT)

**Status: DRAFT — not a release. Do not merge to `main`. Do not publish or overwrite
the existing `0.5.5` / `0.5.6-rc1` ZIPs.**

This PR is the single integration candidate for the MarketOps research plugin. It
cherry-picks the two lane commits that had not yet reached the RC branch (F money
gate, G browser egress), then applies the integrator-owned seams so both changes
take effect on the real REST and MCP entry points.

## Source revisions

| Role | Branch | Exact SHA |
| --- | --- | --- |
| RC2 base (H) | `fix/marketops-rc-release-hygiene-20261020` | `38dcef853f526f64f669c475c96ad9c3bad3ad47` |
| F money gate | `fix/marketops-rc-money-gate-20261020` | `a3e45df134bc88305a7647d3f8f9f646be317450` |
| G browser egress | `fix/marketops-rc-browser-egress-20261020` | `f977c6a149235b68b1388d2bd793f71d86675615` |
| QA #15 | `qa/marketops-rc-customer-gate-20261020` | `cdb1d9a3f7c4b9d7c8d12923364bcd091fcd2a95` |
| PR9 baseline | `feat/bruce-plugin-delivery-20261020` | `4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44` |

The three remote branch heads above were re-read with `git ls-remote` immediately
before integration and matched the requested SHAs byte-for-byte.

Cherry-picks (no conflict, no ours/theirs, no reset):

- `57fd5036` — F, `-x a3e45df1`
- `b6e66e96` — G, `-x f977c6a1`
- RC2 seam commit — integrator-owned connections and tests

## What changed beyond the cherry-picks

1. **F is now effective on both real entries.** `research_wiring` (MCP managed
   call), `dual_search_mcp` (REST) and `research_provider_extensions` all call
   `runtime.state_ledger_caps` when local consent exists. Legacy call-only paths
   use `set_call_cap_preserving_money`, so a consented USD cap and its
   `usd_enforced` flag are no longer erased by a later `set_cap`.
   Run and project call ceilings stay independent; unknown pricing remains
   fail-closed; “success with unknown cost” remains
   `CHARGE_UNVERIFIED_AFTER_SUCCESS`, not a Provider failure; unknown holds are
   never zeroed.
2. **G is now effective on the remaining reachable Crawl4AI entry.**
   `research_provider_extensions._crawl4ai_local` validates the URL scheme and the
   egress binding, rejects downgraded WebRTC/DNS/proxy flags before BrowserConfig
   construction, and merges the mandatory guards. A rejected binding returns
   `LOCAL_CRAWL_NOT_SAFE_TO_RUN` with **no direct fallback**.
3. **Core fetcher non-coverage is documented and fenced.** The release bundle
   copies only `plugins/.../scripts`; it has no import edge to
   `marketops/adapters/crawl4ai_fetcher.py`, which still lacks the WebRTC/DNS
   guards. A test forbids adding that edge. This PR does **not** claim the core
   path is hardened.
4. **Version is `0.5.6-rc2`** across manifests, builder, installer, MCP server,
   documentation and test expectations. No old ZIP is touched.

## Verification (all offline; real provider calls = 0)

| Gate | Result |
| --- | --- |
| F targeted (`test_runtime_dollar_cap_contract.py`) | 16 tests OK |
| G targeted (`test_crawl_browser_webrtc_egress.py`) | 19 tests OK |
| RC2 seams (`test_rc2_integration_seams.py`) | 12 tests OK |
| Cross-transport dedup (`test_serpapi_cross_transport_no_double_charge.py`) | included in the 256-test targeted batch OK |
| Installer opt-in (`test_installer_optin_security.py`) | 21 tests OK |
| Jev header locating (`test_jev_targeted_excerpt.py`) | 19 tests OK |
| P0 fabricated email/institution + QA #15 | 9/9 OK against the RC plugin root |
| Plugin full offline suite | `Ran 2187 tests`, 0 failures, 0 errors, 30 skipped |
| Release hygiene (build → unpack → stdio → mixed client view) | PASS; server `0.5.6-rc2`, 30 tools, provider calls 0 |
| Release gate (A/B/C/D/E/F/G/RC/RC2 + ancestry) | all PASS |

Artifact SHA256 (final source ZIP and connector ZIP) is recorded in the evidence
directory and in this PR’s evidence comment; it is deliberately not hard-coded in
the tree, because doing so would change the bytes it describes.

### Existing root CI reds

The root `ci` workflow reports 23 failed tests at H (`38dcef85`, run
`39376391351`). The same 23 appear on `main@63a5fe48` (run `39194528248`) and on
PR9 `4f87bc33` (run `39364517578`), one-for-one:

- `tests/test_evidence_store_contract.py` — 9 (3 tests × jsonl/sqlite/memory)
- `tests/test_sources_snapshot.py` — 12
- `tests/test_portability.py` — 1
- `tests/test_security_egress.py` — 1

No test was changed, skipped or downgraded to make these green.

## Blockers / not proven

- **OS-level UDP isolation is UNPROVEN.** No packet capture was taken. G’s flags
  are proven to be written into the launch configuration, not that the kernel
  saw no non-proxied UDP. `BROWSER_SECURITY_BOUNDARY` says so explicitly.
- **Unknown pricing BLOCKS a USD-budgeted call.** Without an operator-reviewed
  catalog, an explicit USD cap fails closed; real paid-provider cost accuracy is
  NOT_VERIFIED in this run.
- **gitleaks CLI is absent on this node.** The in-repo regex gate passes (0
  secret-level, 1 allowlisted synthetic fixture), but this PR must not be read as
  “gitleaks passed”.
- **Root CI baseline reds remain** (see above); they are not introduced here.
- Target worktree had no pre-existing directory, so nothing was overwritten.

## Rollback

- Base: `38dcef853f526f64f669c475c96ad9c3bad3ad47`.
- F integration: `57fd5036`; G integration: `b6e66e96`; seam commit follows.
- No history rewrite, no force-push, no change to `main`, no Lane branch changed.
- To roll back: close this Draft PR and delete only
  `rc/marketops-0.5.6-rc2-20261020`. All Lane commits and old ZIPs remain intact.

## Review

A separate security reviewer is requested for:

1. the no-direct-fallback path in `_crawl4ai_local` (including exception mapping);
2. every remaining `set_cap` call that could drop `usd_enforced`;
3. `state_ledger_caps` vs. the legacy env-driven ceilings;
4. the absence of a core `marketops/` import edge in the shipped bundle.

# R17-D1 / R17-D2 — closed on the C side, with one new blocker for B

**From:** W-C · **Branch:** `feat/marketops-capgateway-tinyfish-20261017`
**Authority used:** `research_capability_registry.py` @ `672447de` (transcribed into
`tests/test_reading_tinyfish_registry_alignment.py`, not inferred).
**Q:** Round-17 file copied read-only from `4a5ef3c1`; Q's tests were not modified.

## R17-D1 — review labels now follow the registry

Three states, not two. The registry reviews 14 TinyFish capabilities; it **enables
12** and **disables 2**:

| state | ids | advertised? | routable? |
|---|---|---|---|
| REVIEWED | search, fetch_content, get_run, list_runs, batch_status, get_wallet, get_search_usage, list_fetch_usage, get_monitor, list_monitors, list_browser_sessions, run_web_automation | **yes** | yes |
| REVIEWED_DISABLED | cancel_run, batch_cancel (T4, `DISABLED`) | no | no |
| UNREVIEWED | none today | no | no |

`capabilities()` returns the 12; `disabled_capabilities()` returns the 2;
`unreviewed_capabilities()` returns the empty set. A T4-disabled capability is
reported as `REVIEWED_DISABLED`, never as "unreviewed" — that label would send a
reader to the wrong owner. Tiers now match the registry too (the account/usage
reads are T0, the provider-facing reads T1, cancels T4).

`readiness` is still `OFFLINE_TESTED` everywhere and the vendor schema stays
`NOT_CAPTURED`: **reviewed ≠ enabled ≠ live.**

## R17-D2 — `list_browser_sessions` exposes only what the registry reviewed

The registry allowlist is `{limit, page}`. The vendor documents four more
(`status`, `session_id`, `start_after`, `end_before`); the adapter no longer
accepts them:

* `validate("list_browser_sessions", {...surplus...})` →
  `CAPABILITY_PARAM_NOT_ALLOWED`
* a **direct** `execute()` with a surplus key → `BLOCKED /
  CAPABILITY_PARAM_NOT_ALLOWED / NO_CHARGE_PROVEN / dispatched: false`, with
  **zero** adapter/SDK/client/ledger/egress work
* `{"limit": 10, "page": 2}` still accepted and forwarded; out-of-bound values
  still `TINYFISH_BROWSER_SESSION_ARGS_INVALID`
* `get_monitor` requires `monitor_id`; `list_monitors` takes no arguments

## Evidence — Q Round 17, read-only copy

```
BEFORE (product adapter 95a6d2b)   2 failed, 7 passed
AFTER  (C adapter)                 9 passed
```

Wider TinyFish-relevant Q subset (19 files, read-only copy), A/B on the same
scratch:

```
BEFORE 32 failures   AFTER 30 failures
NEW failures caused by this change: 0
FIXED: exactly the two R17 reds
  test_qa_round17_tinyfish_monitors.py::test_no_id_the_registry_reviews_is_labelled_unreviewed
  test_qa_round17_tinyfish_monitors.py::test_list_browser_sessions_is_reviewed_to_limit_and_page_only
```

The 30 remaining failures are **pre-existing in that scratch** (assembly /
installed-tree / deployment-fixture gaps), identical before and after; none is
attributed to this change. `test_qa_browser_ssrf.py` needed the Core `marketops`
package on the path — a scratch setup issue, now supplied.

## New blocker for B (found while aligning; not silently "fixed")

Two parameter names disagree between the registry and the vendor catalog:

| capability | registry `allowed_params` (672447de) | adapter wire params (vendor catalog) |
|---|---|---|
| `tinyfish.batch_status` | `{batch_id}` | `{run_ids}` |
| `tinyfish.cancel_run` | `{run_id}` | `{id}` |

`cancel_run` is DISABLED so it does not bite today, but **`batch_status` is
advertised and T0-enabled**: through the gateway its `run_ids` argument is a
surplus key, so the call is refused before dispatch. The capability is
advertised but not genuinely routable.

I did not change the adapter's wire params: the pinned vendor catalog is the only
evidence available for the wire, and it says `run_ids`. Fixing this means B
reconciles its two entries with the vendor catalog — or an authorized
`tools/list` capture settles it. Until then this is an open B-side item, recorded
rather than guessed.

## Not changed

URL guard stays single-source (`research_egress.approved_url` only, no
fallback); missing guard → governed `EGRESS_REFUSED` with zero contact. T0–T4
tiers, UNKNOWN-charge holding, the 20% B0 floor and the USD 0.20 TinyFish reserve
are untouched. No provider call, credential, wallet, OAuth or paid request.

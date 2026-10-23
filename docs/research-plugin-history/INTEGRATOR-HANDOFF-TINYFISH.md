# TinyFish → W-INTEGRATOR handoff (two shared-file changes + test impact)

**From:** W-C · **Branch:** `feat/marketops-capgateway-tinyfish-20261017`
**Measured against:** integration tip `6f0219d` (the "gateway could not complete a
single managed call" fix) and `cf8ff6e` for the bridge blob.
**Status:** proposal only. No shared file was modified on this branch.

Both changes below were **run**, not read: the patch was applied to the real blob
(`git apply --check` → rc=0) and the mapping was exercised through the real
`research_wiring.reviewed_capability()`.

---

## 0. The **shipping** path (measured end to end, not read)

```
.mcp.json / mcp.json  →  research_mcp.py  →  research_provider_extensions.invoke()
                      →  research_official_mcp.{_validate,call}
```

`research_mcp.py`'s `marketops_tinyfish*` tools map MarketOps operations onto
`extra.invoke(...)`, and `research_provider_extensions.py` is what actually calls
the bridge. That file hardcodes the vendor verb at **two** places per operation:

| shipping op | `_validate` | `call` | problem |
|---|---|---|---|
| `tinyfish_mcp_search` | `("tinyfish","search",{query})` | same | none |
| `tinyfish_mcp_fetch` | `_public_https_url` | `{urls, format:"markdown"}` | omits the published `links`/`image_links`/`page_metadata` |
| `tinyfish_mcp_wait` | `("tinyfish","wait_for_run",{run_id})` @643 | `call(… "wait_for_run" …)` @740 | **verb + param name are not the vendor's** |
| `tinyfish_mcp_agent` | `("tinyfish","run_web_automation",{url,goal,agent_config})` @652 | same @743 | `agent_config` is not a vendor parameter; required `session_id` is never sent |

### The lockstep problem this exposed

**Patch 1 (the get_run rename) applied alone is a regression on the shipping
path.** Measured on the integration tree:

```
as shipped                      _validate("tinyfish","wait_for_run",{"run_id":…})  → ACCEPTED
patch 1 only                    …                                              → REFUSED: OFFICIAL_MCP_TOOL_NOT_ALLOWLISTED
patch 1 + caller patch (3)      _validate("tinyfish","get_run",{"id":…})        → ACCEPTED
```

The shipped `marketops_tinyfish_agent_wait` tool would fail on **every** call.
So patches 1 and 3 must land together — my previous handoff said "applies cleanly"
and did not check the caller; that was incomplete.

**Patch 3's fetch change also needs patch 2.** The caller sending the documented
defaults is refused by the bridge's own argument allowlist
(`TINYFISH_MCP_FETCH_ARGS_INVALID`), so widening the allowlist has to land with it.

All three are in `references/patches/` with a `README.md` manifest, applied in
order 1 → 2 → 3. Each was `git apply --check`ed against the blob at `cf8ff6e`.

### Credential wire divergence (unresolved, needs the handshake)

On the shipping path the bridge sends the provider key as
`Authorization: Bearer` (its `_live_call`). This adapter sends a provider key as
`X-API-Key` and an OAuth access token as `Authorization: Bearer`. Both are
readings of a contract the vendor does not publish, so the two paths currently
disagree and **neither is verified**. Do not "align" them by guessing: one
authorized read-only handshake settles it, and until then both sides report
`auth_wire_verified: false`.

### Agreement is machine-checked, not prose

`research_adapter_tinyfish.SHIPPING_CALL_SHAPES` holds the intended shape of each
shipped call in the integration's own operation vocabulary, and
`tests/test_reading_tinyfish_lazy_surface.py::ShippedCallShapes` validates every one
against this adapter's argument contract. If our contract moves, that test names
the shipped call that stopped matching.

---

## 0b. Exact method/API diff (signatures read at integration tip `6f0219d`)

Line numbers move; the method names do not. Each row names the function, what it
does today, what it must do, and which patch carries it.

| # | method | today | required | carrier |
|---|---|---|---|---|
| 1 | `TOOLS["tinyfish"]` | `{search, fetch_content, run_web_automation, wait_for_run}` | `{…, get_run}` | patch 1 |
| 2 | `_validate(provider, tool, arguments)` — run-status branch | keyed on `wait_for_run`, reads `run_id` | keyed on `get_run`, reads `id` | patch 1 |
| 3 | `_live_call(provider, tool, arguments, *, max_paid_usd)` — run-status branch | calls `wait_for_run` | calls `get_run` | patch 1 |
| 4 | `_validate` — `fetch_content` allowlist | `{urls, format, ttl, purpose}` | `+ links, image_links, page_metadata, include_selectors, exclude_selectors, per_url_timeout_ms` | patch 2 |
| 5 | `_validate` — `run_web_automation` | requires `agent_config.{max_steps,max_duration_seconds}` | accept required `session_id`; stop requiring `agent_config` (the vendor has no such parameter) | **none — paid-gate restructure** |
| 6 | `_live_call` — agent submission | sends `agent_config`; gates on `_supports_agent_bounds(schema)` | send a fresh UUID `session_id`; stop asserting a provider step ceiling | **none** |
| 7 | `_wallet_guard(result, max_steps, ceiling_usd)` | rate × `max_steps` | with no upstream step bound, compare a declared ceiling and label it `CLIENT_ASSERTION_NOT_PROVIDER_ENFORCED` | **none** |
| 8 | `_credential(provider, environ=None)` → `_live_call` header | `Authorization: Bearer` for a provider key | provider key `X-API-Key`, OAuth token Bearer — **unverified; do not change without the handshake** | **deliberately none** |
| 9 | `research_provider_extensions._validate` @643 | `("tinyfish","wait_for_run",{"run_id": …})` | `("tinyfish","get_run",{"id": …})` | patch 3 |
| 10 | `research_provider_extensions.call` @739 | `call("tinyfish","wait_for_run",{"run_id": …})` | `call("tinyfish","get_run",{"id": …})` | patch 3 |
| 11 | `research_provider_extensions.call` @733 | `{urls, format:"markdown"}` | send the documented defaults | patch 3 |
| 12 | `research_wiring.REVIEWED_CAPABILITY` | see §2 | 6 additions; 8 capabilities need registry entries | handoff §2 |

Rows 5–7 are the remaining paid-path gap and are **not** in any patch: they change
what the spend gate is allowed to claim, which is a decision above this lane.
Row 8 is deliberately untouched: two readings of an unpublished contract must not
be "aligned" by guessing.

All three patches were re-checked against the **current** integration blobs after
the tip moved, `git apply --check` → rc=0 each.

---

## 1. `scripts/research_official_mcp.py` — the run-status rename

**Patch:** `references/patches/tinyfish-official-bridge-get-run.patch`
(6 hunks). Base blob = the file at `cf8ff6e`.

Why: the file exposes `TOOLS["tinyfish"] = {… "wait_for_run"}`, requires
`agent_config.{max_steps,max_duration_seconds}` (a parameter the vendor does not
publish), and its own `SYMBOL_DRIFT` record says
`shipped_tool: wait_for_run / vendor_documented_tool: get_run`,
`resolution_state: RECORDED_NOT_REPAIRED`.

What it changes — `wait_for_run(run_id)` → `get_run(id)` in all five places, and
closes the drift record:

| Place | Before | After |
|---|---|---|
| `TOOLS["tinyfish"]` | `…, "wait_for_run"` | `…, "get_run"` |
| `_validate` branch | `tool == "wait_for_run"`, `arguments.get("run_id")` | `tool == "get_run"`, `arguments.get("id")` |
| accepted key set | `{"run_id"}` | `{"id"}` |
| dispatch branch | `tool == "wait_for_run"` | `tool == "get_run"` |
| capability descriptor | `tinyfish.wait_for_run`, `allowed_params=("run_id",)` | `tinyfish.get_run`, `("id",)` |
| `SYMBOL_DRIFT` | `RECORDED_NOT_REPAIRED` | `REPAIRED_IN_THIS_PATCH` |

**Behaviour evidence** (patched module imported inside the integration's own
`scripts/` tree):

```
TOOLS['tinyfish']  : ['fetch_content','get_run','run_web_automation','search']
_validate id       : {'id': 'run-abc_1'}                    -> accepted
{'run_id': …} / {} / {'id': ''} / {'id':'a','extra':1}       -> refused
REVIEWED_CAPABILITIES: tinyfish.get_run present, wait_for_run gone
SYMBOL_DRIFT.resolution_state: REPAIRED_IN_THIS_PATCH
```

**Tests that must change with it** (they pin the old contract; this is the exact
list, not an estimate):

- `tests/test_reading_official_mcp_session_protocol.py`
  - `FakeSession.names` tuple contains `"wait_for_run"` (line ~57) → `"get_run"`
  - the branch `elif name == "wait_for_run"` (line ~78) → `"get_run"`
  - the `agent_config` schema fixture (line ~67) is no longer part of the
    contract this test pins
- `tests/test_reading_official_mcp_embedded.py`
  - `agent_config` appears in the paid-agent arguments (~94, ~104, ~142, ~146,
    ~159)
- `tests/test_reading_embedded_official_mcp_bridge.py`
  - `agent_config` schema/key fixtures (~100, ~106, ~112, ~144)

I did **not** touch those files; changing them is the Integrator's call, and they
are also why I did not edit the bridge on this branch.

**Not in this patch, deliberately:** `session_id` minting, removing
`agent_config`, `fetch_content`'s mandatory flags, and the `X-API-Key` /
`Authorization: Bearer` split. Those change the structure of the *paid* path and
touch the wallet precondition, which is a decision for you (and is the half the
`DECISION-04` note was gating). Say the word and I will prepare them as a second
patch with the same evidence standard.

---

## 2. `scripts/research_wiring.py` — the capability-name bridge

`reviewed_capability()` refuses an unmapped name ("A fallback that stripped the
prefix would silently turn an unreviewed capability into a reviewed one"), so the
map is the actual gate on whether a capability can be booked.

**Measured gap (as it stands today):**

| Surface | ids | unmapped |
|---|---:|---:|
| my adapter's `capabilities()` | 14 | **13** |
| the integration bridge's own `REVIEWED_CAPABILITIES` | 4 | **3** |

So the integration's *own* TinyFish adapter cannot be booked through its own
gateway either, except `search`. Its map keys (`tinyfish.fetch`,
`tinyfish.agent`, `tinyfish.run_status`) do not match its registry ids
(`tinyfish.fetch_content`, `tinyfish.run_web_automation`, `tinyfish.get_run`).

**Proposed additions** (verified: each resolves through the real function):

```python
    ("tinyfish", "tinyfish.search"): "search",
    ("tinyfish", "tinyfish.fetch_content"): "fetch",
    ("tinyfish", "tinyfish.run_web_automation"): "agent_run",
    ("tinyfish", "tinyfish.get_run"): "usage_read",
    ("tinyfish", "tinyfish.list_runs"): "usage_read",
    ("tinyfish", "tinyfish.batch_status"): "usage_read",
```

After both this and the bridge patch: **6 of my 14** resolve, and **all four** of
the bridge's ids resolve (its fourth only because the patch renames it to
`tinyfish.get_run`).

**Still unmapped (8) — these need a registry entry, not a name guess:**

```
tinyfish.get_wallet        tinyfish.get_search_usage   tinyfish.list_fetch_usage
tinyfish.list_monitors     tinyfish.get_monitor        tinyfish.list_browser_sessions
tinyfish.cancel_run        tinyfish.batch_cancel
```

They are free and read-only except the two cancels, and the reviewed vocabulary
has no name for them. Inventing one here would be the exact move the map's own
comment forbids, so they stay refused until the registry gains them. That makes
the coverage I added reachable through the adapter but **not** through the
gateway — a registry decision, not a code defect.

---

## 3. Remaining compatibility gaps (not both-fixable above)

| # | Gap | Owner |
|---|---|---|
| 1 | `research_contract.py` / `research_adapter_base.py` absent → my `execute()` duck-types `CapabilityRequest` and returns `ResultView` | W-B |
| 2 | `AdapterResult` has no `reason_code`; the closed-set channel is `caveats[0]` (your own CONTRACT-CHANGE-REQUEST) | coordinator |
| 3 | `test_gateway_integration_gateway.py` drives a synthetic `ProbeAdapter`; the real `TinyFishAdapter` is not covered by it | I |
| 4 | Credential wire (`X-API-Key` vs Bearer) unverified on this account | needs an authorized read-only handshake |
| 5 | Upstream `_approved_url(resolve=True)` performs DNS, so URL capabilities refuse in a sandbox (fail-closed) | recorded; tests inject the guard |
| 6 | Three read-only inventory capabilities moved T4→T1 | CONTRACT-CHANGE-REQUEST in `status/W-C.md` |

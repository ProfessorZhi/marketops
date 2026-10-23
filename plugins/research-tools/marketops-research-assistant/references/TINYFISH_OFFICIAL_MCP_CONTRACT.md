# TinyFish official MCP — reviewed contract, capability status and wiring surface

**Date:** 2026-10-17 · **Owner:** Wave-1 Worker C (`feat/marketops-capgateway-tinyfish-20261017`)
**Base:** PR #73 `b2bcdcd24862a923f7b32eff75bfcc2756ae767b`
**Module:** `scripts/research_adapter_tinyfish.py` (new, self-contained)
**Tests:** `tests/test_wave1_tinyfish_{catalog,arguments,agent_gate,protocol,adapter_contract}.py`
**Pinned catalog SHA-256:** `4ac83f6d30ce1ca163168afc8eb8782f54861cb46379880e55b84bd2a9c751e4`

中文摘要：本文件是 TinyFish 官方 MCP 的**已复核契约**——钉住上游 28 个工具目录、按 T1/T3/T4 分档给出逐能力状态、并给出 Integration 接线所需的精确调用面。任何 `live` 一栏都是 `LIVE-UNVERIFIED`：本仓库没有任何真实账号、Key、OAuth 会话或钱包被触碰过。

---

## 1. Why a separate module

This work is P2-01 of the architecture convergence plan. The reviewed upstream catalog
disagrees with the TinyFish tool table carried in `research_official_mcp.py` on four
load-bearing points:

| Field | Reviewed official contract | `research_official_mcp.py` at base |
|---|---|---|
| run status | `get_run(id)`, `batch_status(run_ids)` | allows `wait_for_run` — a verb the official catalog does not publish |
| Agent submit | `run_web_automation(url, goal, session_id, ...)`, `session_id` a fresh random UUID per call | `session_id` not accepted at all |
| Agent hard bound | no `agent_config` parameter; no published per-call step ceiling | requires `agent_config.{max_steps,max_duration_seconds}` and gates on "provider hard cap" |
| fetch flags | `links`, `image_links`, `page_metadata`, selectors, `ttl`, timeouts, conditional headers | admits only `format`, `ttl`, `purpose` |

The old paths **fail closed** — a real server never advertises `wait_for_run`, so the call
is refused as `UPSTREAM_MCP_TOOL_NOT_ADVERTISED` — but they are **unusable**, and the
opaque transport error is exactly the shape that invites a pointless retry of a possibly
charged operation.

`research_official_mcp.py` was **left byte-identical**. Its own test modules
(`test_reading_official_mcp_embedded.py`, `test_reading_official_mcp_session_protocol.py`,
`test_reading_embedded_official_mcp_bridge.py`) assert the old TinyFish contract, and rewriting a
shared file that a concurrent lane may be touching is the failure mode this branch was told
to avoid. The replacement is isolated, additive, and the Integrator wires it (§6).

This also matches the Wave-1 coordination contract (`WORKER-DAG-AND-OWNERSHIP.md` §3.1,
single-writer rule): that file is recorded as **C's**, but the primary deliverable is the
new file and old-file changes are secondary and transitional. No old file was modified on
this branch.

---

## 2. Per-capability status

`coded`, `offline_tested` and `live` are independent facts. Nothing in this release is
`live`.

| Tool | Tier | Enablement | May charge | live |
|---|---|---|---|---|
| `search` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `fetch_content` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `get_run` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `list_runs` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `batch_status` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `get_wallet` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `get_search_usage` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `list_fetch_usage` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `run_web_automation` | T3 | GATED_T3 | **yes** | LIVE-UNVERIFIED |
| `cancel_run` | T3 | GATED_T3 | no | LIVE-UNVERIFIED |
| `batch_cancel` | T3 | GATED_T3 | no | LIVE-UNVERIFIED |
| `run_web_automation_async` | T4 | DESCRIBED_NOT_ENABLED | yes | not applicable |
| `create_browser_session` | T4 | DESCRIBED_NOT_ENABLED | yes | not applicable |
| `list_browser_sessions` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `close_browser_session` | T4 | DESCRIBED_NOT_ENABLED | no | not applicable |
| `create_profile` | T4 | DESCRIBED_NOT_ENABLED | no | not applicable |
| `list_profiles` | T4 | DESCRIBED_NOT_ENABLED | no | not applicable |
| `start_profile_setup_session` | T4 | DESCRIBED_NOT_ENABLED | no | not applicable |
| `save_profile_setup_session` | T4 | DESCRIBED_NOT_ENABLED | no | not applicable |
| `cancel_profile_setup_session` | T4 | DESCRIBED_NOT_ENABLED | no | not applicable |
| `create_monitor` | T4 | DESCRIBED_NOT_ENABLED | yes | not applicable |
| `get_monitor` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `list_monitors` | T1 | ENABLED_T1 | no | LIVE-UNVERIFIED |
| `run_monitor` | T4 | DESCRIBED_NOT_ENABLED | yes | not applicable |
| `pause_monitor` | T4 | DESCRIBED_NOT_ENABLED | no | not applicable |
| `resume_monitor` | T4 | DESCRIBED_NOT_ENABLED | yes | not applicable |
| `cancel_monitor` | T4 | DESCRIBED_NOT_ENABLED | no | not applicable |
| `guide_next_step` | T2 | DESCRIBED_NOT_ENABLED | no | not applicable |

The 14 `DESCRIBED_NOT_ENABLED` tools stay **visible in the catalog with a stated reason**
rather than silently absent, so an operator can tell "upstream cannot do this" from
"nobody thought of it". `guide_next_step` is catalogued and refused because provider-authored
text is untrusted input and must never steer spending.

### Invariants enforced in code

1. **Only reviewed tools are callable.** `tools/list` is compared to the pin on every
   session; any addition, removal, duplicate or malformed name is `TINYFISH_SCHEMA_DRIFT_REFUSED`
   before a tool call. An unknown tool can never become callable by being advertised.
2. **No unpublished run-status verb.** `get_run(id)` / `batch_status(run_ids)` only.
3. **Fresh UUID `session_id` per submission.** A caller-supplied `session_id` is refused
   (`TINYFISH_AGENT_SESSION_ID_MUST_BE_GENERATED`) so a replay cannot inherit another
   submission's browser session.
4. **The wallet is read, never written.** No top-up, no auto-reload change; no such tool
   exists in the catalog. `auto_reload_enabled` is surfaced as an operator risk fact.
5. **No retry of a charged call.** A dispatched-but-unresolved paid call returns
   `charge_state: UNKNOWN`, `retry_permitted: false`; it is never re-sent and never
   cross-routed.
6. **Pre-dispatch and ambiguous failure are different objects.** A refusal raises
   `ValueError` and provably spent nothing. An ambiguous outcome returns a receipt with
   `ok: false`. The charge accounting depends on this split, which is why the wallet
   preflight runs in its **own** protocol session rather than batched with the submission.
7. **Profiles, Vault, browser sessions and monitors are refused**, not merely discouraged.
8. **No claimed provider hard cap.** Upstream publishes none, so the operator must
   acknowledge `accept_uncapped_provider_spend` in writing; `max_charge_usd` is recorded as
   `CLIENT_ASSERTION_NOT_PROVIDER_ENFORCED`.

---

## 3. Integration API surface

```python
import research_adapter_tinyfish as tinyfish

tinyfish.ENDPOINT                 # "https://agent.tinyfish.ai/mcp"
tinyfish.PROVIDER                 # "tinyfish"
tinyfish.READ_TOOLS               # 8 T1 tools, callable
tinyfish.T3_TOOLS                 # ("run_web_automation","cancel_run","batch_cancel")
tinyfish.DESCRIBED_TOOLS          # the 17 descriptors
tinyfish.ungated_tools()          # == READ_TOOLS

tinyfish.capability_catalog()     # descriptors + pinned catalog_sha256
tinyfish.capability_matrix()      # per-capability coded / offline / live
tinyfish.integration_surface()    # exactly what the router may call
tinyfish.schema_drift(advertised) # compare a live tools/list to the pin
tinyfish.validate(tool, arguments)
tinyfish.call(tool, arguments, *, max_charge_usd="", authorization=None,
              runner=None, advertised_tools=None, environ=None) -> receipt
tinyfish.credential_mode()        # ("USER_PROVIDED_BEARER_KEY"|"USER_PROVIDED_MCP_ACCESS_TOKEN"|"OS_KEYRING_OAUTH", var)
tinyfish.wallet_facts(data)
tinyfish.normalize_run_status(payload)

# ProviderAdapter (INTERFACE-CONTRACT.md §6)
tinyfish.TRANSPORT                # "official_mcp" — fixed by §6, not per-adapter
tinyfish.capabilities()           # ("tinyfish.search", ...) — served capability_ids
tinyfish.probe_schema(runner=None, advertised=None) -> snapshot | AUTH_REQUIRED
tinyfish.verify_schema(advertised) -> {capability_id: MATCH|ABSENT}
tinyfish.PUBLISHED_DEFAULTS        # documented defaults, sent explicitly
tinyfish.wallet_observation(data)  # lenient read; the strict parse guards spending
tinyfish.URL_GUARD_SOURCES         # the one module that implements the URL rule
tinyfish.TARGET_GUARD_ORDER        # shape during validation, resolve before dispatch
tinyfish.execute(req, *, runner=None, advertised_tools=None) -> AdapterResult
tinyfish.cancel(upstream_ref, *, grant_ref=None, runner=None) -> AdapterResult
```

## 3a. Conformance to the frozen Wave-1 contract

The adapter implements `ProviderAdapter` (§6) and the `IA-*` invariants (§10):

| Invariant | How this module satisfies it |
|---|---|
| `IA-1` no key in any output | every payload passes `_sanitize`; `SECRET_FIELDS` redacted; tests assert no credential reaches a receipt or a log line |
| `IA-2` adapter does not decide policy | `execute()` receives an authorized request and refuses only on structure: unknown capability, disabled capability, missing grant for T2+, schema drift, disallowed params. It does **not** read `MARKETOPS_TINYFISH_AGENT_ALLOW_PAID` or the network gate, and does not re-check the USD ceiling — the Ledger owns budget |
| `IA-3` no retry after dispatch | one dispatch site; a dispatched-but-unresolved call becomes `OUTCOME_UNKNOWN` / `CHARGE_UNKNOWN` with `retry_permitted: false` and is never re-sent |
| `IA-5` no redirect following | `follow_redirects=False` on the HTTP client |
| `IA-6` upstream response is untrusted | `_clean`-equivalent `_sanitize` runs before the payload enters an `AdapterResult` |

`call()` and the `_check_*` helpers above `execute()` are the **policy edge**, kept so the
negative behaviour is testable today. At integration the Policy/Registry lane owns them;
`execute()` is the adapter.

`AdapterResult` (§3.4) carries the ten contracted fields plus five declared extras
(`capability_id`, `reason_code`, `charge_state`, `retry_permitted`,
`business_evidence_verified`); the conformance test pins that set so an eleventh is a
deliberate act. `actual_usd` is always `None` and `actual_source` always `NONE`: this
adapter cannot produce a verified charge and will not report a zero that would read as
free.

### Gates, in order

| # | Gate | Failure code |
|---|---|---|
| 1 | `validate()` — allowlist, closed argument sets, types, bounds | `TINYFISH_TOOL_NOT_IN_OFFICIAL_CATALOG`, `TINYFISH_TOOL_DESCRIBED_NOT_ENABLED`, `TINYFISH_ARGUMENTS_*`, `TINYFISH_{SEARCH,FETCH,STATUS,USAGE,AGENT}_ARGS_INVALID` |
| 2 | schema drift (`advertised_tools` / live `tools/list`) | `TINYFISH_SCHEMA_DRIFT_REFUSED` |
| 3 | `MARKETOPS_RESEARCH_ALLOW_NETWORK == "1"` | `TINYFISH_NETWORK_NOT_AUTHORIZED` |
| 4 | credential present | `TINYFISH_CREDENTIAL_REQUIRED` |
| 5 | T3 only: `MARKETOPS_TINYFISH_AGENT_ALLOW_PAID == "1"` | `TINYFISH_AGENT_PAID_NOT_AUTHORIZED` |
| 6 | T3 only: per-operation human authorization | `TINYFISH_AGENT_HUMAN_AUTHORIZATION_REQUIRED` / `_SCOPE_INVALID` |
| 7 | Agent only: acknowledge the absent provider ceiling | `TINYFISH_AGENT_PROVIDER_HARD_CAP_NOT_PUBLISHED` |
| 8 | Agent only: `max_charge_usd` in `(0, 5]` | `TINYFISH_AGENT_MAX_CHARGE_REQUIRED` / `_INVALID` |
| 9 | not inside a running event loop | `OFFICIAL_MCP_SYNC_CALL_INSIDE_RUNNING_LOOP` |
| 10 | Agent only: wallet preflight (read-only, own session) | `TINYFISH_AGENT_WALLET_READ_FAILED` / `_NOT_VERIFIED` / `_EMPTY` / `_CURRENCY_UNSUPPORTED` / `_SPEND_CAP_EXCEEDED` |

### Receipt keys

`ok` · `provider` · `mcp_tool` · `transport` · `endpoint` · `tier` · `effect` ·
`auth_mode` · `auth_wire` · `auth_wire_verified` · `catalog_sha256` · `error` ·
`tool_result` (sanitized) · `mcp_tool_completed` · `business_evidence_verified` ·
`actual_charge_usd` (always `null`) · `provider_charge_verified` · `charge_state` ·
`retry_permitted` · `schema_drift` · and, per tool: `agent_run_id` / `agent_lifecycle` /
`agent_status` / `session_id_used` / `max_charge_usd_declared` / `ceiling_enforced_by` /
`provider_hard_cap` / `authorization_isolation` / `wallet`.

`charge_state ∈ {FREE_TIER, NO_CHARGE_PROVEN, UNKNOWN}` — the §2 `ChargeState`
vocabulary; `RESERVED`/`SETTLED` belong to the Ledger, not to the adapter.
`business_evidence_verified` is **always false**: a successful MCP call is not verified
business evidence.

### Human authorization object

```python
{"approved_by": "<operator identity>",          # non-empty; required
 "scope": "TINYFISH_PAID_AGENT_RUN",            # exact; required
 "accept_uncapped_provider_spend": True}        # Agent submissions only
```

`authorization_isolation` in the receipt records
`SAME_OS_IDENTITY_NO_CRYPTOGRAPHIC_ISOLATION`. This is a **trusted-operator** mode, not an
adversarial boundary: a shell-capable agent running as the same OS user could call the
vendor directly. No cryptographic separation is claimed. Per the convergence plan (P1-04),
T3/T4 needs an isolated credential/approval boundary before it can be called hardened.

---

## 4. What is deliberately **not** here

- No top-up, auto-reload or wallet mutation path of any kind.
- No Profile creation, login capture, Vault use, or cookie read/export.
- No browser-session creation/close and no recurring monitor — descriptors only.
- No `agent_config` / step-time ceiling (upstream publishes none).
- No retry, no cross-provider fallback, no REST fallback.
- No change to `research_official_mcp.py`, `research_mcp.py` or
  `research_provider_extensions.py`.

---

## 5. Verification performed (offline only, no network)

```
python -m compileall -q scripts tests                  # OK
python -m unittest discover -s tests                   # 421 tests, OK, 1 skipped
                                                        # (309 at base → 112 new)
python scripts/preflight.py                            # STATIC_PASS, 0 errors
```

Per-module: catalog 23, arguments 31, agent_gate 40, protocol 18.

Mutation checks (guard is load-bearing only if removing it turns a test red):
loosening the Agent `goal` bound, and removing the Agent target-URL allowlist call, each
turned exactly its own test red and green again on restore.

Suite wall time is unchanged by this work: the same tree ran 309 tests in 33.3 s and 421
tests in 29.9 s. (An earlier 102 s reading was machine load — load average 57 from
concurrent agents.)

No socket, credential, wallet, browser, OAuth flow, provider account or paid call was used.

---

## 6. Wiring plan for the Integration lane

Not claimed as integrated. The Integrator owns these steps:

1. Point the TinyFish branch of `research_mcp.py`'s router at
   `research_adapter_tinyfish.call()`, replacing the `research_official_mcp` TinyFish path.
2. Retire (or reduce to a shim) the TinyFish section of `research_official_mcp.py`; update
   `test_reading_official_mcp_embedded.py`, `test_reading_official_mcp_session_protocol.py`
   and `test_reading_embedded_official_mcp_bridge.py`, which currently assert the superseded
   contract.
3. Update the shared policy vocabulary in `research_provider_policy.py`:
   `tinyfish_mcp_wait` (`STATUS_POLL_NO_NEW_AGENT`) describes a `wait_for_run` polling
   operation; the reviewed operation is `get_run`, plus new keys for
   `tinyfish_mcp_wallet_read` and `tinyfish_mcp_batch_status`.
4. Keep the payment-gate names (`MARKETOPS_TINYFISH_AGENT_ALLOW_PAID`) — no new environment
   variable is introduced, so `mcp.json` / `.mcp.json` `env_vars` and the preflight
   allowlist are unchanged.
5. Emit `marketops_events` started/succeeded/blocked rows from the receipt
   (`charge_state`, `retry_permitted`, `agent_run_id`) so the ledger and the adapter agree
   on what "unknown charge" means.

### Contract the Integrator must not break

- A caller-supplied `session_id` stays refused.
- `retry_permitted` stays `false` on every paid receipt.
- `validate()` stays the only entry to argument acceptance; no passthrough.
- The catalog SHA is bumped deliberately, with a reviewed diff, never automatically.

---

## 7. Unresolved gates (BLOCKED, not PASS)

| Gate | Status |
|---|---|
| Real-account `tools/list` comparison against the pin | **BLOCKED** — needs separately authorized remote handshake |
| Auth wire contract (`Authorization: Bearer` for key vs OAuth) | **UNVERIFIED** — official docs do not state it; `auth_wire_verified: false` |
| Wallet payload shape on a real account | **UNVERIFIED** — the parser is strict and will refuse an unknown shape rather than guess a price |
| Per-call step/duration ceiling | **NOT PUBLISHED BY VENDOR** — no USD hard stop is claimed |
| T3 live submission | **BLOCKED** — no paid call attempted, by instruction |
| T4 Profile/Monitor/Session | **BLOCKED** — descriptors only; requires named-site authorization and an isolated credential boundary |
| CI on this branch | **BLOCKED** — P0-02: account billing; the runner never starts, so a red check is not a test result |

**Confidence:** high on the catalog, argument contracts, gating and failure classification
(offline-proven, mutation-checked). **Zero** on live behaviour of this account: nothing was
authenticated, spent or logged in.

---

## 8. Sources

- Upstream tool catalog: https://github.com/tinyfish-io/tinyfish-mcp-server/blob/main/docs/tools.md (pinned 2026-10-17)
- Upstream MCP docs: https://docs.tinyfish.ai/mcp-integration
- Owner brief and convergence plan: `docs/architecture/MARKETOPS_{RESEARCH_PLUGIN_PRODUCT_BRIEF,ARCHITECTURE_CONVERGENCE_DECISION_AND_P0_P2_PLAN}_2026-10-17.md`

# TinyFish → I (and A/B): offline egress fixture + review-gated advertisement

**From:** W-C · **Branch:** `feat/marketops-capgateway-tinyfish-20261017`
**C-owned files only:** `scripts/research_adapter_tinyfish.py`,
`tests/test_wave1_tinyfish_*.py`, `tests/_tinyfish_egress_fixture.py`.
Nothing in `research_official_mcp.py`, `research_wiring.py`,
`research_capability_registry.py` or `research_egress.py` was touched.

## 1. The reported red, reproduced and fixed

W-I10 reported `test_reading_tinyfish_agent_gate` and `..._protocol` red when run
alone. Reproduced against **integration `fba7b27` + A's seam (`fceaf070`)**:

```
BEFORE   agent_gate: 40 tests, 12 failures + 12 errors
         protocol  : 24 tests,  1 failure  +  5 errors
AFTER    agent_gate: 40 tests, OK
         protocol  : 24 tests, OK
         egress_gate: OK        (no skip in this tree)
```

Root cause was **not** only the resolver. Two independent things:

1. the tests delivered `public.example.org` to a guard whose resolving form asks
   the system resolver, and the name does not resolve;
2. **A's guard needs a matching slice.** `research_egress.approved_url`
   (fceaf070) calls `security.network.check_port`; the slice vendored at
   `fba7b27` has no such function and the guard refuses with
   `OFFICIAL_MCP_TARGET_NOT_ALLOWED: egress slice lacks the port policy
   (security.network.check_port) — refusing rather than guessing`.
   That is fail-closed and correct, but it means **`research_egress.py` and
   `marketops_safe_egress/` must be integrated as a pair**. The demonstration
   above only goes green with the matched slice.

### How the tests stay offline without weakening anything

`tests/_tinyfish_egress_fixture.py`:

* when the active guard declares W-A's seam
  `approved_url(value, *, resolve=True, resolver=None)`, the **real guard** runs
  with a `FakeResolver` whose answer is `192.0.0.9` (IETF protocol-assignment
  anycast: `ipaddress.is_global` is true, and it is not a host anyone contacts);
* otherwise the **real guard's own** `resolve=False` mode runs — the guard's
  documented "do not ask the network yet" path. No rule is reimplemented.
* `no_real_dns()` replaces `socket.getaddrinfo` with a tripwire that raises if
  anything really resolves, so "offline" is asserted, not assumed.
* the negative gate is tested two ways: a guard double that refuses on
  resolution (deterministic everywhere), and — where the seam exists — the real
  guard answering `127.0.0.1`, which must refuse.

Production SSRF behaviour is untouched: no production resolver is patched, and
no weakened guard is installed.

## 2. Advertisement is now review-gated (B decides)

`capabilities()` returns the **reviewed subset** by default:

```python
capabilities()                       # 6 reviewed ids — what the gateway may route
capabilities(include_unreviewed=True)  # all 14 the adapter can serve
unreviewed_capabilities()            # the 8 with no reviewed counterpart
```

Reviewed (advertised): `search`, `fetch_content`, `run_web_automation`,
`get_run`, `list_runs`, `batch_status`.

NOT_REVIEWED (served, labelled, **not advertised**): `get_wallet`,
`get_search_usage`, `list_fetch_usage`, `list_monitors`, `get_monitor`,
`list_browser_sessions`, `cancel_run`, `batch_cancel`.

Why this shape: an advertisement is a permission to invoke, and the gateway
enumerates `capabilities()`. So the three inventory reads named in the dispatch —
and the five other unmapped capabilities — are not user-invokable through the
product until B reviews them, while remaining visible in
`capability_catalog()`/`capability_provenance()` as `NOT_REVIEWED` with their
reason. `curated_tools()` is intersected with the reviewed set for the same
reason.

**For B:** review status is declared, never inferred. When the registry reviews a
capability, move its id into `REVIEWED_CAPABILITIES` in the adapter — that is the
only edit needed; no test encodes the count. If B prefers a different reviewed
set, the declaration is the single place to change, and
`tests/test_reading_tinyfish_review_status.py` asserts the advertisement equals it.

## 3. One behaviour change worth knowing

An egress refusal raised during **argument validation** now keeps
`EGRESS_REFUSED` instead of collapsing into `CAPABILITY_PARAM_NOT_ALLOWED`.
"We will not send this target" and "you passed a bad parameter" are different
operator actions; both stay inside the §5.3 closed set, and both are
`NO_CHARGE_PROVEN` with `dispatched: false`.

## 4. What is still not claimed

`UPSTREAM_SCHEMA_VERIFIED`, `LIVE_VERIFIED`, `CLIENT_VERIFIED` remain
unattainable: no real vendor `tools/list`, no credential authorization, TinyFish
key rotation unconfirmed. The 20% B0 floor / USD 0.20 TinyFish floor and the
UNKNOWN-charge rule are unchanged. No provider call, key, wallet or account was
touched by this work.

# Full `tools/list` descriptor digest — handoff to B (registry) and I (wiring)

**From:** W-C · **Branch:** `feat/marketops-capgateway-tinyfish-20261017`
**Module:** `scripts/research_adapter_tinyfish.py` (C-owned; nothing else touched)
**Schema version:** `marketops.tinyfish-tools-list.v1`

## What changed and why

`probe_schema` used to hash **tool names only** (`json.dumps(sorted(names))`). A
server could therefore change every `inputSchema`, keep the names, and produce an
identical hash — so the "schema" digest could not see a schema. The S0 gate's
`pinned_hash_subject = HOST_SUBSET_NOT_VENDOR_TOOLS_LIST` and
`vendor_schema_sha256 = None` were honest; the digest behind them was not
sufficient to become a vendor hash even after a real capture.

`discover_schema(...)` now returns the **full** canonical descriptor per tool and
hashes that.

## The result shape (composable, bounded)

```python
tinyfish.discover_schema(descriptors=[...])          # offline / synthetic
tinyfish.discover_schema(names=[...])                # legacy: labelled reduced
tinyfish.discover_schema(descriptors=..., baseline=previous)   # drift vs baseline
```

| field | meaning |
|---|---|
| `schema_version` | `marketops.tinyfish-tools-list.v1` |
| `provider` / `endpoint` / `transport` | `tinyfish` / `https://agent.tinyfish.ai/mcp` / `official_mcp` |
| `raw_tool_count` | how many tools the list contained, before any filtering |
| `tools` | canonical list, sorted by name: `name`, `description`, `input_schema`, `annotations` (only when advertised) |
| `tool_names` | sorted names — the backward-compatible view |
| `digest_subject` | `VENDOR_TOOLS_LIST_DESCRIPTORS` or `TOOL_NAMES_ONLY` |
| `digest_sha256` | SHA-256 over `json.dumps([canonical descriptors], sort_keys=True, separators=(",",":"))` |
| `reduced` | `true` when only names were available |
| `capture.mode` | `OFFLINE_SYNTHETIC` \| `AUTHORIZED_REAL_CAPTURE` |
| `capture.trusted_as_vendor` | `true` only for a real, attested capture |
| `promotion` | `NOT_CAPTURED` \| `AUTHORIZED_REAL_CAPTURE` |
| `vendor_schema_sha256` | the digest **only** when trusted; otherwise `None` |
| `schema_verified` | **always `False` here** — reading bytes is not verifying a vendor |
| `drift` | `{tool_added, tool_removed, malformed, schema_changed, basis}` |

Canonicalisation is deliberate: only the four allowed fields enter the digest,
so a vendor adding an unreviewed field cannot change a reviewed hash.

An `AUTHORIZED_REAL_CAPTURE` requires `attestation={"authorization_ref": …,
"captured_at": …}`; without it the call raises
`TINYFISH_CAPTURE_ATTESTATION_REQUIRED`. There is **no** real capture, so today
every result is `OFFLINE_SYNTHETIC` / `NOT_CAPTURED` / `vendor_schema_sha256:
None`.

## B — `research_capability_registry.py` (B-owned; I am not editing it)

Expected field/path, stated against the frozen `CapabilityEntry` (§4):

| frozen field | what to store |
|---|---|
| `schema_sha256` | `discovery["digest_sha256"]` — the **descriptor** digest |
| `param_schema` | derived from `discovery["tools"]` for the reviewed subset only; never the raw vendor object |
| `readiness` | stays `OFFLINE_TESTED` until a trusted capture exists |

Two additions B's registry needs that the frozen entry does not have yet:

1. `vendor_schema_sha256: str | None` — separate from `schema_sha256`, because
   the pinned local descriptor digest and a trusted vendor hash answer different
   questions. Conflating them is how a local pin gets read as a vendor fact.
2. `schema_subject: str` — carry `digest_subject` verbatim
   (`VENDOR_TOOLS_LIST_DESCRIPTORS` / `TOOL_NAMES_ONLY`), so a reduced digest can
   never be presented as a schema digest.

`verify_schema(capability_id, observed_tools_list)` must compare the **descriptor
digest**, and must return `UNVERIFIED` (not `MATCH`) when the observation is
`reduced`. Names agreeing is not evidence that the schema agrees.

## I — `research_wiring.py` / S0 gate (I-owned; I am not editing it)

Wiring path:

```
research_wiring → TinyFishAdapter(transport=…) → .probe_schema()
                → discovery fields into the registry row and the S0 decision
```

Expected use:

- call `probe_schema()` **once at wiring time**, not per request;
- persist `digest_sha256`, `digest_subject`, `capture.mode`, `promotion`,
  `vendor_schema_sha256` alongside the registry row;
- keep `schema_verified = False`.

### Focused S0 assertions (optional, exact)

```python
probe = adapter.probe_schema()
assert probe["promotion"] == "NOT_CAPTURED"            # no real capture exists
assert probe["vendor_schema_sha256"] is None
assert probe["schema_verified"] is False
assert probe["capture"]["live_handshake_performed"] is False
assert probe["digest_subject"] == "VENDOR_TOOLS_LIST_DESCRIPTORS"
# a reduced observation can never satisfy the schema gate
assert not (probe["reduced"] and probe["schema_verdict"] == "MATCH")
```

S0 remains **DENY** while any of the first four does not hold. Passing the
synthetic 28-tool catalog through a real `ClientSession` is a protocol test, not
a vendor capture, and must not flip S0.

## What is deliberately still missing

- No real `tools/list` capture: `UPSTREAM_SCHEMA_VERIFIED` and `LIVE_VERIFIED`
  remain unattainable, and `auth_wire_verified` stays false.
- The descriptor digest tells you the list changed. It does not tell you the list
  is *correct*, and it must not be used as an authorization signal.

# Egress control — dual-platform smoke record (Linux + Windows)

**Contract:** P1-01 matrix item 8 — *"Linux + Windows smoke results must be recorded."*
**Revision:** `de00c6c` + the egress-binding change recorded here (see `git log`).
**Date:** 2026-10-17 (北京时间).
**Status:** Linux **MEASURED**; Windows **OFFLINE-ASSERTED, REAL-HOST RUN BLOCKED**.

> A security boundary whose Windows behaviour is asserted nowhere is not a
> boundary — and a Windows behaviour *reported as measured* when no Windows host
> ran is worse than one reported as absent. This record keeps those two apart,
> and the BLOCKED line is part of the claim, not a caveat under it.

---

## 1. Linux — measured

Interpreter and runner are recorded because the `pytest` on `PATH` on this host
is not a working install (acceptance checklist §G-5).

```bash
cd <worktree>
/home/operator/artifacts/marketops-capgateway-independent-qa-20261017/venv/bin/python \
  -m pytest tests/test_wave1_egress_entry.py tests/test_wave1_egress_bundle.py \
            tests/test_wave1_egress_guarded_http.py -q -p no:cacheprovider
# 92 passed
```

Adversarial matrix (P1-01 items 1–6), driven by the independent gate
`tests/qa_gate/test_qa_browser_ssrf.py`, offline, resolver faked, connections
refused by `conftest.SocketSentinel`:

| item | what it asserts | result on this revision |
|---|---|---|
| 1 | an approved name resolving to loopback / private / link-local / metadata is refused **before a connection** | PASS — 9 address classes (IPv4 + IPv6) |
| 1a | the guard actually asks the resolver, so the refusal can be by address and not by string | PASS |
| 2 | the guarded browser path is bindable to a controlled egress | PASS — `_crawl4ai_local(url, *, egress=…)` |
| 2b | an unbound browser refuses with `LOCAL_CRAWL_NOT_SAFE_TO_RUN` | PASS — and there is no parameter to ask for an unmanaged fallback |
| 4 | the checked address is pinned against rebinding | PASS (core `EgressGate`, faked resolver) |
| 7 | a legitimately public HTTPS host still works — "refuse everything" is not the pass condition | PASS |

Direct evidence that the refusal is by resolved address, not by name:

```text
ValueError: TARGET_RESOLVES_NON_GLOBAL: an approved name resolved to a
non-globally-routable address (link-local IPv4); refused before any connection
```

## 2. Windows — asserted offline, not executed

**A real Windows host run is `BLOCKED`.** No Windows machine or customer device
is reachable from this node, and P2-05/F-1 require separate device
authorization. **Nothing below is a measurement on Windows.**

What *is* determinable offline, and is asserted by reading the code rather than
by running it:

| property | how it was established | result |
|---|---|---|
| no POSIX-only module in the egress path | `grep -rE 'posix\|pwd\|grp\|pty\|fcntl\|termios'` over `research_target_guard.py`, `research_egress.py`, `marketops_safe_egress/security/network.py` | 0 hits |
| no shell-out anywhere in the path | same files, `grep -rE 'subprocess\|os\.system\|\bsh\b'` | 0 hits |
| no platform-conditional branch | `grep -rE 'sys\.platform\|os\.sep'` over the same files | 0 hits |
| the address predicate is the stdlib one | `ipaddress.ip_address(...).is_global`, used unchanged on both platforms | — |
| the vendored slice is pure stdlib Python | `scripts/marketops_safe_egress/` imports reviewed; no compiled or platform-bound dependency | — |

These make "the Windows refusal decision is the same decision" a reasonable
expectation. They do **not** make it a measurement, and this record does not
claim one. The Windows arm of P1-01 item 8 stays open until a real Windows host
is authorized to run the same matrix.

## 3. What is explicitly not claimed

- Not `UPSTREAM_SCHEMA_VERIFIED`, not `LIVE_VERIFIED`, not `CLIENT_VERIFIED`
  (no `DECISION-04` authorization; no real provider handshake was performed).
- Not a claim that a CONNECT proxy sees the URL inside TLS. It does not. A
  successful proxy decision is **not** evidence that fetched content is
  trustworthy, and nothing in the egress path is written to suggest otherwise.
- Not a claim that resolving a name closes the rebinding window. Resolving
  narrows it; closing it requires connecting to the address that was checked,
  which is what the pinned-address path does and which the caller must use.
- Not a Windows execution. See §2.

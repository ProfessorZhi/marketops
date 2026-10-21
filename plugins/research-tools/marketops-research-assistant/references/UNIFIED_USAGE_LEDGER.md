# Unified usage ledger and grant policy — written contract (P1-03 / P1-04)

**Status:** design + offline-tested module contract. **Not** a live-verified, billing-verified or
production-approved statement. No provider was called to produce anything on this page.

**Owner code:** `scripts/research_usage_ledger.py`, `scripts/research_grants.py`,
`scripts/research_usage_ledger_migration.py`.

This page is written before the code on purpose: the ledger's whole value is a state machine that
nobody may extend by accident. If a behaviour here and a behaviour in the code disagree, this page
is what the reviewer reads first and the code is what gets fixed.

---

## 1. Why there is one ledger and what it replaces

Two independent, count-based attempt ledgers existed:

| Legacy store | Table | Ceiling | Units |
| --- | --- | --- | --- |
| `state/dual-search-ledger.sqlite3` | `search_attempts` | `COUNT(*)` over **all** rows | attempts |
| `state/provider-reservations.sqlite3` | `provider_reservations` | `COUNT(*)` over **all** rows | attempts |

Both answered "how many attempts have ever happened" with one global number. Neither could answer
"how much money is this run holding", "did the provider charge this call", or "is the same logical
operation being bought twice over two transports". They are replaced **logically**: exactly one
component decides whether an operation may proceed, and exactly one place records what it cost.

They are **not** deleted and **not** rewritten. Their rows are read, copied forward and kept
byte-identical — see §7.

## 2. Schema (one SQLite database, WAL)

`operations` — one row per logical provider operation:

```text
operation_id      TEXT PK   -- opaque, caller-supplied, attributed to one dispatch
project           TEXT NOT NULL
run_id            TEXT NOT NULL
intent_fingerprint TEXT NOT NULL  -- Key A (§4)
op_signature      TEXT NOT NULL   -- Key B (§4), transport-independent
provider          TEXT NOT NULL
account_ref       TEXT NOT NULL   -- provider account/meter identity, never a credential
capability        TEXT NOT NULL
meter             TEXT NOT NULL   -- what the vendor counts: search_query/agent_step/...
transport         TEXT NOT NULL   -- recorded for audit only; never part of the signature
site              TEXT NOT NULL DEFAULT ''  -- required at T4; the site the grant names
tier              INTEGER NOT NULL
estimated_micros  INTEGER NOT NULL   -- integer micro-USD; never a float
reserved_micros   INTEGER NOT NULL
actual_micros     INTEGER               -- NULL until settled
actual_source     TEXT NOT NULL         -- PROVIDER_REPORTED | DERIVED | NONE
charge_state      TEXT NOT NULL         -- §3
quota_unit        TEXT NOT NULL         -- requests|credits|steps|tokens|pages|seconds|bytes
quota_units       INTEGER NOT NULL
estimated_seconds INTEGER NOT NULL
outcome           TEXT NOT NULL         -- bounded code, never a provider body
upstream_ref      TEXT                  -- provider run/request id when one exists
grant_ref         TEXT                  -- required for tier >= 3 (§6)
escalates_from    TEXT                  -- prior operation_id for a justified cross-provider run
price_source      TEXT NOT NULL         -- where the rate came from
price_version     TEXT NOT NULL         -- version/date of that rate
price_observed_at TEXT NOT NULL         -- when it was observed
origin            TEXT NOT NULL         -- native | legacy | local
legacy_ref        TEXT                  -- stable reference into a legacy store (§7)
counts_against_caps INTEGER NOT NULL    -- 1 | 0, see §7
created_at, dispatch_intent_at, settled_at, reconciled_at TEXT
```

Constraints, all enforced by the database rather than by a caller remembering:

- `UNIQUE (project, run_id, op_signature)` — the same logical operation cannot be bought twice in a
  run, whatever `operation_id` it is given and whichever transport it arrives on.
- `UNIQUE (operation_id)` — an id names one operation for its whole life.
- `UNIQUE (legacy_ref) WHERE legacy_ref IS NOT NULL` — migration is idempotent.
- Terminal states are written once: a settle/reconcile either matches the stored row or is refused.

`caps` — explicit ceilings, each with the human who set it:

```text
scope_kind TEXT   -- operation | run | provider_account | project
scope_key  TEXT   -- operation_id | <project>/<run> | <project>/<provider>/<account> | project
window     TEXT   -- total | day | month
period_key TEXT   -- '' for total, else YYYY-MM-DD / YYYY-MM (UTC)
ceiling_micros, ceiling_calls, ceiling_seconds  INTEGER
source     TEXT NOT NULL   -- who set it; a ceiling without a name is not readable
```

`events` — append-only audit trail: `(seq, operation_id, at, kind, code, detail_ref)`. Nothing in
this ledger deletes audit rows.

**Rates are reviewed configuration, and the row carries the snapshot.** There is no `pricing`
table: the catalog (`PricingCatalog`, a reviewed table of `provider, capability, unit_micros,
free_tier, source, version, observed_at, quota_unit, quota_per_call`) supplies the rate at reserve
time, and the operation row stores `price_source`/`price_version`/`price_observed_at` plus what was
actually used. A shared rate table that a later edit could change would silently re-price history —
an invoice from last month would stop matching the ledger row that recorded it. The snapshot cannot
drift from the row, because it *is* the row.

**The request payload is not stored.** A row keeps its fingerprint, not the query, the URL or the
page. A ledger that copied request bodies would become a second, unmanaged copy of customer
content, and the two fingerprints already answer the questions the payload would be read for (is
this the same operation, and is this the same intent).

## 3. Charge state machine

```text
                    reserve()
        (none) ─────────────────▶ RESERVED
                                   │  │  │  │
   mark_dispatch_intent() ─────────┘  │  │  │   (recorded BEFORE the outbound call, §5)
                                      │  │  │
   settle(actual, source) ────────────┘  │  │   ▶ SETTLED
   settle_free_tier(quota) ──────────────┘  │   ▶ FREE_TIER
   prove_no_charge(reason) ─────────────────┘   ▶ NO_CHARGE_PROVEN   (only before dispatch)
   mark_unknown(reason) ──────────────────────▶ UNKNOWN             (only after dispatch)

   UNKNOWN ── reconcile(...) ──▶ SETTLED | NO_CHARGE_PROVEN
```

Rules that make this fail closed:

1. **There is no refund verb.** `reserve` is the only way money is held and `prove_no_charge` is the
   only way a hold is released before dispatch. A timeout therefore cannot be silently refunded —
   the failure branch has no call to make, instead of a call it must remember not to make.
2. **Dispatch intent is written before the call, not after.** A crash between the two leaves
   `dispatch_intent_at` set, so reconciliation treats the call as possibly charged: the ledger's own
   orderings only ever move the books towards *over*-counting exposure. What the ledger cannot see is
   a caller that never records intent at all — it can still reach the network and then call
   `prove_no_charge`, and no local module can detect that. So the code enforces what it can and this
   page says the rest: `settle` and `settle_free_tier` **require** a dispatch intent (so a row that
   booked a cost always shows a call was made), `prove_no_charge` refuses once an intent exists, and
   recording the intent before the call is a **caller obligation** — the one step whose omission a
   caller can hide from this module.
3. **A settled amount must be attributable.** `settle` and `reconcile` require an
   `actual_source` of `PROVIDER_REPORTED` or `DERIVED`, and a `PROVIDER_REPORTED` amount — **zero
   included** — must name the provider record it came from. Without that, "settled at 0" would be
   indistinguishable from a caller deciding an inconvenient hold was free, and the audit trail would
   have nothing to check it against.
4. **`prove_no_charge` refuses on a dispatched operation.** Providers do declare "not charged" for
   some failures; that path exists, but it is `reconcile_no_charge` and it must name the evidence.
   Claiming a dispatched call was free is an act with a reference, not a cleanup.
5. **`UNKNOWN` keeps its hold** until reconciled. It is exposure, it is reported, and it is not a
   retry permission: the ledger has no automatic retry, and §5's rule against re-routing a
   timed-out paid call to another paid provider is enforced by `escalates_from` (§4).
6. **Terminal states are terminal.** `FREE_TIER`, `SETTLED` and `NO_CHARGE_PROVEN` accept no further
   transition. There is no annotation path that reopens one.
7. **References are identifiers, and that is the whole claim.** `upstream_ref` and `evidence_ref`
   are bounded opaque identifiers: free text, a URL with a query string or a provider error body is
   refused, so a report cannot be made to print one. An *identifier-shaped* secret is still stored —
   a reference field exists to hold the provider's own id and the ledger cannot tell one string from
   another — and the report and the recovery reading do not print references at all.

`FREE_TIER` is separate from `NO_CHARGE_PROVEN` on purpose: "this vendor charges nothing for this
operation under the recorded rate" and "this attempt demonstrably incurred no charge" have
different evidence, different reporting and different failure modes. Merging them would let a
pricing assumption masquerade as a billing fact.

**Reusing a saved artifact is its own verb, not a reservation.** `record_local_reuse` writes a
`NO_CHARGE_PROVEN` row (origin `local`, no money, not counted against any ceiling) for work
answered out of the evidence store. It is terminal the moment it is written, so it can never
become a dispatch: a cache hit must not be the thing that quietly buys a paid call. There is no
"settle it later" path from it, which is what makes the claim checkable rather than conventional.

## 4. Two fingerprints

- **Key B — `op_signature`**: `sha256(canonical_json(project, run_id, provider, account_ref,
  capability, units, site, canonical_arguments))`. **Transport is excluded**, so the same query over
  SerpApi REST and SerpApi MCP is one signature and the second attempt is refused as a duplicate
  rather than billed again. **Everything that changes what is bought is included**: `units` (a
  25-step purchase is not a 1-step purchase) and `site` (two sites are two purchases, which is the
  distinction a T4 grant exists to make).
- **Key A — `intent_fingerprint`**: `sha256(canonical_json(project, run_id, normalized_objective))`.
  Scoped to the **run**: the same question asked again in a later run is that run's work, while two
  vendors bought in parallel for one run is the pattern this key exists to surface. Deliberately
  *not* part of the uniqueness constraint. The same objective served by a second provider is a
  *decision*, not a bug: it is allowed only when the caller states an `escalation_reason`, and the
  new row records `escalates_from`. Without a reason the reserve is refused. Cross-provider
  escalation is therefore neither auto-blocked nor free.

Canonicalisation: keys sorted, UTF-8, `ensure_ascii`, JSON scalars only; anything else (nested
objects with unordered keys are fine, floats are not) is refused rather than hashed into something
that differs between two callers who mean the same thing. Strings are normalised to **NFC**, because
`"café"` has two encodings that render identically and would otherwise fingerprint as two
operations - and a duplicate check that misses is a duplicate charge.

## 5. Caps: four levels, two unit systems

| Level | Scope key | Required? | Typical use |
| --- | --- | --- | --- |
| `run` | `<project>/<run>` | **yes** | the per-run USD, call, and wall-clock budget |
| `operation` | `<project>/*`, or one operation signature | no | one operation may not exceed this; it bounds **this** operation, not accumulated spend |
| `provider_account` | `<project>/<provider>/<account>` | no | rolling day/month spend per vendor account |
| `project` | project | no | rolling day/month spend for the whole project |

A bare `*` is **refused** at the operation level: it means "every project", so one project's
`set_cap` would become a global ceiling that another project's history could consume. `<project>/*`
is the supported way to say "any single operation of this project".

Money is stored as **integer micro-USD**; quota units (requests, credits, steps, tokens, pages,
seconds, bytes) are stored **separately** and are never converted into money or vice versa. A
local Crawl4AI page costs 0 USD and 1 page; a Jev call costs USD and tokens. Reporting keeps the
two columns apart because their ceilings are not comparable.

**Windows are UTC.** A `day` window rolls at 00:00 UTC, which is 08:00 in Beijing — so an operator
thinking in Beijing days will see a daily ceiling reset mid-morning. The period key is derived from
the injected clock (`UsageLedger(clock=...)`) in one place, so moving the window to a local day is
a deliberate change at that one point, not a rewrite; it has not been made, because "which day" is
a policy decision and silently picking the operator's timezone would make two deployments disagree
about when a ceiling resets.

"Headroom" means `ceiling − (reserved + settled actual + UNKNOWN exposure)`. `UNKNOWN` counts
against the ceiling: a call whose price nobody knows is not a free call.

By default a cap must **exist** for the `run` scope before a paid operation may be reserved.
An unconfigured ceiling is not an unlimited ceiling; "there is no ceiling" must be stated
explicitly by a named operator, because a missing config that reads as unlimited is the failure
this whole section exists to prevent.

## 6. Tiers, grants and what the boundary is really worth

Tier rules live in `research_grants.py`, implementing §4 of the product brief
(`docs/architecture/MARKETOPS_RESEARCH_PLUGIN_PRODUCT_BRIEF_2026-10-17.md`, on branch
`docs/independent-technical-audit-20261016` — that file is **not** on this branch, so the tier table
above is the copy to read here and the brief remains the authority the copy follows). The code
implements that table and adds nothing:

- **T0/T1** proceed within approved scope and caps, and the capability's transport must be one of
  its reviewed transports.
- **T2** additionally requires a recorded price. "Project policy opt-in" is expressed by the
  operator configuring the ceilings and the registry entry — there is no separate opt-in flag, and
  claiming one would describe code that does not exist.
- **T3** is **off**. A reservation at tier ≥ 3 is refused unless it names a verified grant, and the
  ledger refuses it even if the caller forgot to ask the policy module.
- **T4** is **off**, requires a **site**: the operation must name one and the grant must name the
  same one. A grant that names no site cannot be minted at T4, because an unnamed site scope would
  cover every site — the opposite of what the tier is for.

**Unknown capability ⇒ deny.** A capability absent from the reviewed registry is not
"unclassified, proceed carefully"; it is refused, so an upstream vendor adding a tool cannot
create a callable paid path by publishing a new name.

**A grant's own limits are enforced, not decorative.** A verified grant that names `max_micros` or
`max_calls` is checked against the run's committed totals inside the same transaction as the
ceilings, and the refusal names the grant. A grant whose amount the operation alone would exceed is
refused outright.

**A grant is scoped, not single-use.** The `nonce` exists so two otherwise identical grants have
different signatures; it is *not* a one-shot token. A grant authorizes a scope for a period, and
using it for several operations inside that scope is exactly what `max_calls` expresses. Presenting
it outside its scope (another project, run, provider, capability, account or site), after it
expires, or against a verification key it was not signed by, fails.

**Grant authority, stated honestly.** A grant is signed with an Ed25519 key. Verification uses a
public key configured out of band; the private key is never read by the plugin process. This is a
real boundary *only* while the verification key is configured and the signing key is genuinely
elsewhere:

- No verification key configured ⇒ **T3/T4 are unavailable**, not "trusted because local". There is
  no self-issued-grant fallback.
- The plugin exposes **no tool** that mints a grant. Minting is a separate CLI that requires a
  signing key file, a TTY and a typed confirmation.
- **A shell-capable process running as the same OS user can read anything that user can read,
  including a signing key stored on the same account, and can allocate a TTY.** Where that is the
  deployment, a grant is an *attestation of operator intent*, not a cryptographic isolation
  boundary, and `research_grants.authority_boundary()` returns exactly that sentence to the caller
  instead of a marketing claim. The enforceable ceilings in that deployment are the vendor's own
  (prepaid wallet, plan quota) and the host's approval prompt — not this signature.
- Server deployments that need a hard boundary must put the signing key behind a separate OS
  identity or an authorizer service. The plugin can consume such a grant and cannot produce one.

## 7. Legacy migration: additive, byte-preserving, reconciled

`research_usage_ledger_migration.py` never mutates a legacy database.

1. **Plan** (read-only, and it reads a copy): count rows per legacy table, compute a content hash
   over the ordered columns, and attribute each row to its project/run.
2. **Copy**: legacy files are copied to a scratch directory and the copy's hash is compared with
   the original's. Migration reads the *copy*. Source bytes are re-hashed afterwards and the report
   states whether they are unchanged.
3. **Map**: every legacy row becomes one ledger row with `origin='legacy'`, a stable
   `legacy_ref = "<store>:<table>:<run_id>:<operation_id>"` (colon-separated; the store path
   itself contains slashes), `charge_state=UNKNOWN` — because a
   legacy reserved attempt may or may not have been charged, and the ledger has no evidence either
   way — `tier = -1` (an imported row has no policy tier, and stamping T0 on it would let history
   read as though it had been reviewed), and zero money. Nothing is invented to fill
   `actual_micros`.
4. **Reconcile**: the report lists source rows, migrated rows, and any row that could not be
   mapped (with its reason). A row that cannot be mapped is **reported, never dropped**. Re-running
   the migration inserts nothing, because `legacy_ref` is unique.
5. **Caps**: migrated rows default to `counts_against_caps = 0`, with the report stating the count
   so the operator can see it. This is a units decision, not a convenience: legacy ceilings counted
   *attempts globally*, and the new ceilings are per-scope USD/quota. Folding historical attempts
   into a fresh USD ceiling would invent a ceiling nobody set. The exposure is preserved and
   visible either way, and `--legacy-rows-count-against-caps` opts into the other reading.

## 8. What this module does *not* claim

- It does not make a vendor charge an amount, and it does not stop a vendor from charging one.
- It cannot guarantee a per-run USD ceiling for an operation whose upstream has no step or amount
  bound (TinyFish `run_web_automation`). Where the vendor offers no hard cap, the ledger records
  the precondition and the exposure, and says so.
- It does not verify authentication, quotas or wallet state; those are host-reported observations
  and are labelled as such.
- Passing tests here are **offline** tests. No provider call, no credential and no billable
  operation is involved in any of them.

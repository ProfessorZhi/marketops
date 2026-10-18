# MarketOps MCP — Product-loop catalog

Updated 2026-09-24. This is the second catalog document. It exists because
[MCP service](MCP_SERVICE.md) §2–3 is a **historical record**: its header pins the
review baseline `3744262af703de5e1961878e8b3f97d860216d6b` and says the 15 names
there are what was registered at that revision. Adding rows to that table would
turn a sentence that was true about a revision into a false sentence about today,
and the person editing it would not be editing the sentence they thought they were.

So new capabilities get their own table. **The machine check did not get weaker.**
`tests/test_security_catalog.py` now reads every catalog document, and asserts that
the **union** of the names it parses equals `marketops/security/catalog.py`'s
`TOOL_CATALOG` exactly. One name missing is red; one name extra is red; renaming a
heading in either document is red. What was loosened is *which document a tool
lives in*, not *whether the catalog and the code agree*.

`MCP_SERVICE.md` remains the contract for the research service. Nothing here
supersedes it. Where the two disagree about a shared fact (permissions, the
research-only mode, the send gate), `MCP_SERVICE.md` §6 and the code win, and a
disagreement is a bug in this file.

## 1. Boundary

The research service answers **one research job**: a controller admits work, a
scoped worker executes it, evidence and results come back. The product loop asks a
different question about a different subject: **how is one market run over time.**
Which region and product, on what source plan, at what budget, under which version
of the rules, what happens to what was already collected when the rules change, and
what may be said to whom.

That is why these tools are not more rows on §2. It is also why they are a separate
module in the adapter (`marketops/adapters/mcp_server/product_loop.py`): the storage
ports do not overlap, the failure modes are not the same class, and the roles differ.

**The mode constants are not touched by anything here.** `research_only = true`,
`send_authorized = false` and `feishu_write_authorized = false` remain read-only
properties of `marketops/config.py`, with no setter and no environment input. A
product-loop tool cannot raise them, and no product-loop parameter reaches them.
The outreach half of the loop is specified here and stays behind its own gate; see
§3 below and `MCP_SERVICE.md` §6.

## 2. Product-loop catalog

These names are the current v1 product-loop contract. As in §2 of the service
document, presence in this table is not a claim that a given deployment has wired
it: a handler that exists and a handler this instance was given are different
facts, and `get_capabilities` is where the deployment's own answer lives.

| Tool | Purpose and result |
|---|---|
| `create_scene` | Start a market: write region, scenario, product, role policy, source plan and budget as version 1, optionally activating it; an existing Scene returns its state and version numbers instead of being overwritten |
| `add_scene_version` | Write the next version of an existing Scene: give only the fields that change and the rest are inherited field by field; returns the impact of this version against the previous one (reusable / needs re-assessment / must be re-fetched) |
| `control_scene` | Typed activate / pause / resume / archive intent and the observed result; activating must name which version takes effect, and pausing must give a reason |
| `list_scenes` | Every Scene this deployment has, with its lifecycle state: which version is in force (and that version's byte digest), which version is pending, whether new work may be dispatched now, and the reason when it may not |
| `import_motherpool` | Read an upstream mother-pool artifact into a source receipt and land it: every raw row gets a stated disposition (researchable / merged / excluded / pending / unreadable), exclusions come from the profile's own declaration, and an exclusion the profile never declared refuses the whole artifact rather than dropping that row |
| `run_discovery` | Run one bounded open-web discovery round: issue the profile's queries, fetch the pages, and materialise the result as a pool artifact that can be fed straight back into admission, with the per-line readings (hits, hosts, evidence, spend, stop reasons) alongside. The retrieval ceiling comes from the frozen profile rather than the caller, the region is a required frozen value with no default, and a round that produced no row writes **no** artifact — an empty one would read as "ran, found nothing" |
| `promote_receipt` | Fold one source receipt's resolved rows into the market: one venue per resolved row (the handle derived from the source number, or from name and place when there is none) **and** one canonical account per row (`acct-<hex16>`, derived from that venue handle — the same value `market_account` is keyed by), with the discovery evidence re-anchored onto that account. Every row reports its own disposition (promoted / venue only / refused) with its reason; replaying the same receipt adds no row and moves no timestamp backwards |
| `list_assets` | Read the market's venue asset pool: per venue its four domain cells kept apart (claimed by the upstream / seen by us / verified by us, with the verdict beside them), when it was first seen and last re-checked, its evidence count, and the pool-wide counts — a cell nobody measured is `null` with its reason, never 0 |
| `remember_responsibility` | Record a batch of "who is responsible for **this** venue" judgements into the responsibility table: a per-row disposition (written / folded into an earlier row of the same batch / its evidence does not resolve / the row does not stand up), each row judged on its own; one venue may carry several responsible parties at once, and a relation that was found but not settled is written `unresolved` rather than left out; the rows it writes carry an **empty** `decision_record_ids`, which is how a hand-written row is told from a judgement-backed one |
| `list_responsibilities` | Read the market's responsibility table: per row which account is responsible for which venue (**with its name**), under which of the six relations, the evidence ids it hangs on, the `decision_record_ids` of the judgements that produced it (empty = not reached by a judgement), and the two timestamps; by venue (who is responsible for this) or by account (which venues this one manages), and giving both is refused; a cell that could not be computed is `null` with its reason |
| `list_accounts` | Read the **operator-maintained roster file** for this Scene: every row of the list in file order, with its admission verdict and the rules left unchecked, its stored route / opportunity / evidence counts, and whether that row can be dispatched at all — this reads the declared list, not the researched account pool, which is `list_account_pool`, **and the accounts this system discovered and promoted for itself live there and not in this file**; the product path (maintenance, drafting, research) no longer has to go through this file, because `freeze_cohort` freezes a member roster out of the pool, and that is its main road now |
| `remember_accounts` | Record a batch of accounts into the market's researched account pool: a per-row disposition (written / folded into an earlier row of the same batch / its evidence does not resolve / the row does not stand up), each row judged on its own so one failure does not carry the batch; a row carries the organisation, its other names, its domains, addresses and country, and **the verdict recorded for it** — while the Scene it lands in, the timestamp, and "has it been checked" are not the caller's to give |
| `list_account_pool` | Read the market's researched account pool: per row the organisation and its other names, its domains / addresses / country (**three states**: not looked up / looked up and there is none / a value), the verdict recorded for it, the evidence ids it hangs on, the two timestamps, and the venues it manages on the responsibility table (**with each venue's name and the relation**); giving an `account_id` reads that one row (absent is `lookup.found = false`, not an error, and `null` where this deployment has no pool at all — see §3); a cell that could not be computed is `null` with its reason, never 0 |
| `freeze_cohort` | Read this Scene's account pool once and freeze it into a member roster under one rule: every member (ascending by `account_id`, and that order is what decides `work_item_id`) and **every excluded account with its reason code**; the rule goes into the preimage of `cohort_id`, so the same pool under the same rule computes the same roster — it dispatches no work, spends nothing, and touches no network |
| `list_cohorts` | List every member roster this deployment has frozen, earliest first (the last one is the Scene's current one): per roster its id, which Scene it belongs to, the rule it was selected under, when it was frozen, and how many member / exclusion rows it has; the **content** of members and exclusions is read back by id with `get_cohort` |
| `get_cohort` | Read one member roster in full by id (empty id = the most recently frozen one for this Scene): its members, the selection it was frozen under, every exclusion with its reason, and **what the pool looks like now** (which members are still there and which cells changed since the freeze); this is a read — it creates no store, freezes no roster and writes no pool row |
| `get_maintenance_plan` | Read the Scene's maintenance plan: each aspect's refresh cadence and whether it is calibrated, the due rule, and which aspects this deployment actually re-checks — the declared set and the dispatch table must be subsets of each other; the subjects for this batch are named by `cohort` (a frozen member roster) or by `name` (a hand-written list), exactly one of the two, and the receipt reports which one was read |
| `run_maintenance` | Run one incremental-maintenance tick: claim only the cells this deployment can re-check, and report per cell what it re-checked and what gap that revealed; cells outside the scope are counted by aspect and never claimed; the subjects for this batch are named by `cohort` (a frozen member roster) or by `name` (a hand-written list), exactly one of the two, and the receipt reports which one was read |
| `get_ops_report` | Read the operating report: due work, re-check receipts, per-aspect counts, and a per-cell freshness line — every aspect of every subject, **including the ones that are not due**, each with when it was last verified, when it is next due (empty when it has never been checked), whether it is current, and why it is stale; a metric nobody measured is returned as unavailable with its reason, never as 0; the subjects for this batch are named by `cohort` (a frozen member roster) or by `name` (a hand-written list), exactly one of the two, and the receipt reports which one was read |
| `get_continuation_status` | Read **why no more work is running right now**, and whether anything is driving it: one verdict (researching / paused / waiting on a human / missing capacity / waiting due / complete / no work) with the six counts behind it, plus **the driver** — who holds the wake-up row, which generation they are, when they last said they were alive, and whether that heartbeat has gone stale. The two halves are read together: a deployment waiting on something due **with no holder** and one being driven carry the **same** verdict and opposite dispositions. A deployment that has never written anything answers `no_store` rather than "no work", because the first wants a job admitted and the second wants nothing. This is a read — it claims no ownership, drives no round, and writes no row |
| `list_fetch_attempts` | Read the **access-attempt ledger** — one row per fetch this deployment attempted, which is what answers "what happened to this URL that day" for exactly the attempts that produced no evidence at all (a 403, a challenge page, an address-policy refusal: none of them leave anything in the evidence store). Filter by `scope` (which subject the hop was recorded under), `url` (the address that was **requested**, not where the redirect landed) and `since` (an ISO-8601 instant that **must carry a time zone** — one without it would be read in the server machine's local zone). The three filters read the same way throughout: `null` is *no filter*, while `scope: ""` is *the hops belonging to no subject*. Each row carries the ledger's own fields plus four cells computed here — elapsed, whether a body was kept, whether the status was **never observed** (which is not 200 and not 0), and whether the attempt counts as succeeded. `counts` covers **this page only** and truncation is reported, so a short page never reads as a short ledger. One of its tables is `by_material`, and it answers the question this ledger is otherwise unable to answer: **how many of these rows ever reached the open network at all.** A row whose body came from a controlled fixture or a target we stood up ourselves is `synthetic`; one that fetched a real third-party page is `live` — §8.C.2 requires both kinds to be labeled, because a controlled fixture proves integration behaviour only and **cannot count as market yield or real-site improvement**. So a reading of "200 fetches, 180 succeeded" does not mean 180 real sites until `by_material` says which rows were which. The table lists **every** member including the zeros, so `synthetic: 0` is a claim ("every row on this page went out") rather than a number nobody computed This is a read: it claims nothing, drives no round, writes no row, and does not scan the whole table to report a number. Whether a source circuit is open **right now** it cannot answer — that state lives in the process, not on disk |
| `list_open_reservations` | Read the **budget reservations that have not settled** — the entry point for reconciling them (§8/§9: an unknown charge is reconciled before anything else). One row per reservation, earliest first, each carrying the `reservation_id` that the two reconciling actions need, its `scope`, which budget line it drew on, how many units, the idempotency key and when it was created. `counts` covers this batch: how many rows, the units summed, and the split **by budget line** with all four lines listed including the zeros — this counts rows that were read, so a zero is a reading rather than a metric nobody measured, and listing all four is what tells "no charge is hanging on this line" from "this line was not counted". The summed units are **not** a ceiling comparison: ceilings are set per `(scope, line)`, so the per-scope picture is `usage`, which `get_job` reports. Scope reads the same way as `list_fetch_attempts`: `null` is *no filter*, `scope: ""` is *the reservations belonging to no subject*. A reservation sits at `reserved` because the design requires it — an unknown or timed-out charge is **not** released by default, so nothing anywhere refunds it on its own and the default behaviour, doing nothing, is the correct one. This page hands back the list and the ids; it does **not** settle or refund anything, and it does not scan the whole table to report a number. A deployment that has never written anything answers `no_store` rather than "nothing is hanging", because the first wants a paid call run and the second wants nothing |
| `put_family` | Publish **one revision** of a template family: the same `(id, revision)` with different content is refused (a revision already written is immutable), and a new revision must sit directly after the latest one; the receipt returns the stored family and the next revision number, so "change a line of copy" has one answer — publish the next revision |
| `put_writing_policy` | Publish **one revision** of a writing policy: fields you leave out take the domain's own default (this layer does not keep a second copy of them), and the receipt returns the stored policy in full so that is readable; an empty link-prefix list means *no links at all*, not "anything goes" |
| `put_claim` | Register one revision of an approved claim: the approver comes from the **authenticated principal** and the approval time from the server clock, neither is a parameter; the source is a parameter, because a claim with no source is not an approved claim but somebody's recollection; expiry is not filtered here — whether a claim still counts is decided where it is used |
| `draft_outreach` | Write a batch of drafts for one wave: a generation receipt and a stored state per recipient (generated / review-only / held with its reason / refused), each judged on its own so one failure does not carry the batch; the recipients for this batch are named by `cohort` (a frozen member roster) or by `pool` (a hand-written list), exactly one of the two, and the receipt reports which one was read — **giving neither is a refusal, not a default**; a batch finishing is not permission to send any of it. **Drafting is its own use case, not the first half of freezing a wave**: it judges only "should this letter be written" (the entry kind can carry a normal draft at all, the route's grade is not `X`, the route hangs on some evidence, the recipient is not suppressed and has not said stop, one route per account and per person in this batch) and **not** the three pre-send gates — whether the route has left intake, the per-recipient quota, and the wave cap are `prepare_wave`'s, so a route already sent stage 1 still gets its stage-2 draft; each target freezes the entry kind and grade it had **at drafting time** (a later re-grade does not rewrite a draft that has already gone out) and carries which family matched; a family is chosen by role class, and a route whose role class has no family of its own may only fall back to the stage's family at grade A or B — a C/D route never borrows the named-decision-maker wording. A route nobody ever judged the binding of, or whose freshness nobody judged, is written **review-only**: stored, readable, and never in a manifest. The receipt reconciles the **frozen target set** across `accepted / review_only / held / excluded / failed / reused` plus the rows actually written this pass — which is not the number of drafts returned, since a re-run hands back the stored drafts having written none; a receipt whose rows do not reconcile with the plan is refused rather than padded. `facts` may only **cite** evidence: each fact names an evidence id that must be in the store **and belong to the account the fact is about**, and a fact citing another account's evidence refuses the **whole call** — the shape and the digest are fine in that case, so nothing cheaper than the ownership check catches it |
| `prepare_wave` | Freeze a wave's drafts into a manifest and move the wave to `prepared_pending_authorization`; a candidate with no draft is recorded under `excluded` rather than failing the batch; the manifest digest is what an authorization will bind to; the subjects for this batch are named by `cohort` (a frozen member roster) or by `pool` (a hand-written list), exactly one of the two — **giving neither is a refusal, not a default** |
| `get_wave` | Read one wave: the manifest line by line, each line's attempt and outcome, the authorization live at this instant and how many rows it approved, and **why** the wave is stopped where it is (pending authorization / missing drafts / suppressed / window closed) |
| `ingest_replies` | Read one batch of incoming events from a wired source: per event its classification, disposition and stored result; a classification failure still stores the original and sends the disposition to manual review; an unsubscribe or complaint writes a suppression at once, and a hard bounce is suppressed under the SOP with its evidence kept |
| `list_handoffs` | Read the sales handoffs: per handoff its source event, the reason it was judged a handoff, the owner and the state; an ambiguous reply and a literal alternate contact stay in awaiting-review / awaiting-confirmation and are not advanced |
| `authorize_wave` | Register an authorization record: the approver is taken from the **authenticated principal** and not from a parameter, and the manifest and approved count it binds are checked on the spot; a model writing "I approve" in a message body does not reach this |
| `send_wave` | Deliver one wave under an authorization live at that instant: re-check authorization / state / freshness / history / duplicates / suppressions / replies / bounces / stage / window per row and send only the rows the authorization covers; **registered only when the deployment has outbound capability** |
| `reconcile_wave` | Take the deliveries in one wave whose state is `delivery_unknown` back to the transport and settle each on its answer (delivered / a specific failure / still unknown); the ones still unknown are **not** re-sent and not written down as failures; available wherever a transport is wired, because it reads outcomes rather than making new ones |
| `list_review_cases` | Read the Inbox: **pending first, earliest first**, and per case its state, reason, subject, the snapshot of the objects it affects, the outcomes a person may choose, and the system's recommendation; cases that are closed are in this list too, because a question that was answered and a question that never existed have to be told apart |
| `get_review_case` | Read one case in full: its current state and who holds it, the evidence it is to be judged on, **the policy version in force when it was opened**, and every human decision on it so far (who, when, what, and why); a pending case and a closed one are read by the same tool, because otherwise "closed" would be a claim made by the reader rather than by the record |
| `get_review_flow` | Read the flow over a window and report on the spot whether the identity holds: pending at the start plus opened during the window equals closed during the window plus pending at the end; when it does not hold, the difference is reported per cell rather than as a reassuring total |
| `claim_review_case` | Take an `OPEN` case (the claimant comes from the **authenticated principal**, not from a parameter); a compare-and-set on `expected_version`, so two people reaching for one case leaves the second refused instead of silently overwriting the first one's claim |
| `resolve_review_case` | Record one human decision and fold it into its case: correct the object, dismiss it, propose a policy change, or reopen a closed one; a proposal must name **which version** it suspends and that version must actually exist — a proposal hanging on a version nobody wrote waits for an approval that can never come, and nothing anywhere goes red |
| `approve_policy_change` | Have an **authorizing principal** approve a policy change a case proposed: the approver comes from the authenticated principal and may not be the person who proposed it; only after this may that version be activated |
| `get_scene_impact` | Preview the impact between any two versions of the rules (who is affected and along which dimension); it is the **same computation** `add_scene_version` returns, not a second implementation of it |
| `submit_run` | Admit one manual increment: the workspace and the object scope are read from the **Scene on disk** rather than declared by the caller, the plan is **compiled on the spot** from this context and checked against it, and the envelope, cutoff and idempotency key come from the caller; re-sending the same key returns the **same row** while the same key with a different spec is refused. Admitting is **not** executing — it dispatches nothing, touches no budget ledger and opens no new work item |
| `preview_run` | Read once **before** admitting: it shares the **same preamble** with `submit_run`, so the same arguments compile the same spec (byte-identical `spec_fingerprint`) at the **same instant**, and it reports the five things §253 asks for — how much existing fact is reused, which gaps would be filled, which providers the plan might call, budget/stop, and which ambiguities would go to Review — carrying the entries the plan layer cannot answer **verbatim** (`not_reported`) rather than restating them. The budget/stop cell is **not re-judged**: the same three functions are asked in the same order as on the admitting face, and the reading collapses into five verdicts (`not_due` → `request_refused` → `replay` → `chain_refused` → `open`), with `replay` **before** `chain_refused` — a re-send never asks the chain's thresholds, so "this chain is already at stop" and "this submission would be refused" are not the same sentence. That chain step is **absent** when a previous step has spoken, not an empty string. Zero provider calls is a matter of **construction**: this layer cannot reach a search or fetch port and does not read a clock (`at` comes from outside). This is a **read** — it writes no row, dispatches nothing and costs no search; when the plan cannot be compiled (`HOLD`) it does **not** error but returns a reading with all five entries in `not_reported` |
| `get_run` | Read back one Run's **current state**: the envelope, cutoff, idempotency key, the content-addressed digests, when the row was written and last updated, how it ended, and what it produced (the Delta, once it has ended). This is a **read** — it writes no row, dispatches nothing and touches no budget ledger. An id that names no Run answers "there is **no** such Run" rather than "that Run has ended", because the second reading turns a mistyped id into a replay. A row belonging to another workspace is refused for the same reason: a cross-workspace read leaves no trace |
| `close_run` | End one Run: hand in a **complete Delta** (eight cells) and one ending (`completed` / `partial` / `failed`), and the run stops **only if the books balance**. Two checks run: this run's own **fold ledger** — which rows really landed in the venue pool, written by the same transaction that wrote them — against the `folded` claim handed in, and that claim against the Delta. So a success is **not** "this call did not error" but "the pool grew while the report said it did not" is false for this run. When the books do not balance the run stays running and not one byte on disk changes, so it can be corrected and handed in again; when the fold ledger cannot be read it is **not** read as zero folds, because that would let "folded but did not record" grow into "nothing was folded". The ending comes from the party that did the work — the server holds no answer to speak for it, and "who may declare a run finished" is an identity/authorization question this tool does not judge. A `completed` ending with an all-eight-cells-empty Delta is refused: zero change has to be written as a batch of `unchanged` / `held` / `blocked` dispositions to stay distinguishable from "never ran". Re-sending the same ending and output returns the **same row**; ending the same run a second time with a different ending is refused |
| `get_run_increment` | Read what this run **would do against the disk as it stands now**: per `(subject, aspect)` cell, reuse it, look at it for the first time, go find a source that can serve as evidence, or re-fetch it — with the four verdict counts, **zero included**. It reads a different thing from `get_run`: none of its inputs (the refresh plan, the observations, the change signals) live on the Run row, so reading the same Run later may answer differently, and that is not a stale cache. The cell space is fixed by the **observations already accumulated** — this tool takes no subject source — so an empty cell list must be read together with the observation count in `inputs`: it means "no facts have settled yet", **not** "there is nothing to do". It judges no cost, writes no row |
| `put_product_version` | Register one version of product material: which version it is (**named**, never inferred), what it can do, what it does not do, its unit, its region variants, the source artifacts it was read from, what is still unknown, who owns it, its status (`draft` / `ready` / `retired`), and who approved it and when — the last two are **not** parameters: the verifier comes from the transport's authenticated principal and the moment from this machine's clock, because a payload that names its own approver is an approval record that signs and stamps itself and then looks like any other. Writing the same `(product_id, version)` again with the same content is idempotent — replaying a batch of registrations after a restart lands here — while the same pair with different content is **refused**, because every reference writes only `(id, 2)`, and a version number that means two different things across two deployments is invisible at every one of those call sites. Registering makes **no** outbound call: this face cannot reach a search or fetch port at all, and that is a property of the wiring rather than a rule it is trusted to follow |
| `put_offer_version` | Register one version of an offer in **one workspace**: which product versions it proposes, to which markets and locales, which already-approved claims it leans on, and its price when there is one. The price is the cell that makes this shape worth having — it may be **absent** (a version written down before anyone has quoted it), and it may not be **invented**: a quoted price carries its amount, its basis and who quoted it when, so a number without a source cannot be expressed at all. Same idempotence boundary as `put_product_version`, including the refusal of an over-written version number, and the same rule for the mark a `ready` version carries: it is put there by the session, not by the caller |
| `put_product_line` | Register a product line: the customer problem and the capability family that a group of product versions serves, plus the approved material they share. PSM §4.1 makes this a **zero-behaviour group**, and it therefore has **no version number** — `line_id` is itself the only version, so writing different content under an id that already exists is refused rather than overwritten. Overwriting would erase *when* the membership of this group changed, and that moment is exactly what someone comes here to look up. A product version does **not** need a line first: the grouping is optional, and requiring one would make the line the place where an ungrouped product gets invented |
| `get_product_version_readiness` | Read what one version of product material is still **missing**, before anything starts: three lists — cells to fill or correct, references that do not point at what they claim to point at, and "only a person's sign-off is missing" — each row naming **which record's which cell** and why, plus a fourth list of what this layer **cannot answer** (whether the bytes of the source artifacts are still on disk: the store can only write down where they came from, not read back whether they are still there). The three lists are **not mutually exclusive**: a draft can be both incomplete and unsigned, and collapsing them would make "fill this in and it can run" the natural misreading. `usable` is four verdicts rather than one boolean, decided by the status and the first two lists only — awaiting approval does not take part, because `draft` is already refused by the status cell and a term that can never change the answer would hide the causality. A version that is **not on disk** is **refused** rather than answered with three empty lists: an empty reading and "nothing is missing" have the same shape, and the second one reads as ready to run. The moment comes from the server clock, never from the payload; a product version has **no workspace** (B01 :104), so this one is not filtered by workspace. It is a read: it writes no row and makes no outbound call |
| `get_offer_version_readiness` | Read what one version of an offer is still **missing**, before anything starts: cells to fill or correct, references that do not point at what they claim (a product version not on disk, a claim not on disk, a claim cited at a revision **newer** than the latest on disk, a broken `supersedes` link), the single sign-off that is missing, and what this layer cannot answer. A cited claim that is **not the latest revision** is reported as unanswerable and is **not** replaced by the latest one: `approved_claims()` hands back only the newest revision per `claim_id`, and judging the cited revision with it would report a valid reference as expired on the day the newest one is renewed. Whether a cited claim still counts is decided along expiry, market and locale at **one instant**, taken from the server clock rather than from the payload. This version belongs to **one workspace**: reading a version that belongs to another is **refused** (a cross-workspace read leaves no trace — it looks like a successful query on this side), the same rule `get_run` carries. A version that is not on disk is refused for the same reason as above. It is a read: it writes no row and makes no outbound call |
| `preview_launch` | Read what one version of a Scene would still be **missing if it were started now**: the declaration it fixed (research objective, GeoScope, scenario, role policy, source plan, budget, refresh cadence and outreach cadence, echoed through the *same* function the scene receipts use), the optional product version and offer it cites, three lists — cells to supply or correct, references that do not point at what they claim, and "only a person's sign-off is missing" — a fourth list of what this layer cannot answer, and **one** next action taken from those lists plus the record's own state (`fix_citations` / `supply_missing` / `await_approval` / `request_activation` / `preview_run`). The cited product version and offer are carried **whole** in `cited`, not flattened: each is a readiness reading with its own subject, and flattening would make "this version is missing something" and "the thing it cites is missing something" the same row. A cited offer that belongs to **another workspace** lands in `invalid` and is **not** carried in `cited` — reading it would make another deployment's material into a local fact. The four version cells (`version`, `latest_version`, `effective_version`, `is_effective`) are reported together, so *which* version was read is a reading rather than an assumption; a version that is not on disk is **refused** rather than answered with three empty lists, and the refusal names what the disk does have, because an empty reading and "nothing is missing" have the same shape. The moment comes from the server clock, never from the payload. It is a read: it writes no row, dispatches nothing, spends nothing — **zero outbound calls by construction**, and the face cannot even reach a search or fetch port (B01 :104, fifth closure item) |

Every row above is controller-only except for the Inbox rows, and those are not one
exception but two. `authorize_wave` and `approve_policy_change` are the two an
authorizing principal reaches and no controller does: approving is a **different**
office from doing, and a controller that could approve could send its own proposal
back to itself. `list_review_cases` and `get_review_case` are the opposite case —
they are for **both** offices, because an authorizer who cannot read the case cannot
judge it, and approving blind is not approving. The remaining four Inbox rows
(`get_review_flow`, `claim_review_case`, `resolve_review_case`, `get_scene_impact`)
are controller-only like the rest of the table. No product-loop tool is visible to a
research principal, including on `tools/list` — the isolation rule is that being
unable to call a tool and being unable to see it are both required
(`MCP_SERVICE.md` §6).

This paragraph used to carry a count of the rows ("fifteen of the sixteen"). The
count was already one behind the table, and nothing checks it — the machine check
parses names, not arithmetic. A sentence about "every row except `authorize_wave`"
says the same thing, cannot fall behind the table, and is the fact the paragraph is
about.

Presence in this table is not a claim that a given deployment registered it.
`send_wave` is the sharp case: a deployment without outbound capability does not
register it at all, so it is absent from `tools/list` rather than present and
failing. That is the difference between a tool this repository has and a tool this
instance was given, and it is a deployment fact, not a role fact — which is why
hiding it is done at registration and not by putting it behind a role.

## 3. Boundaries these tools carry

These are properties of the interface, not conventions, and each one has a test
that goes red when it is violated.

**`by` is not a parameter.** Activation records who activated. The actor comes
from the authenticated principal, the same way `marketops/security/catalog.py`
resolves roles, and never from the call payload. A tool that accepted `by` would
let a caller write someone else's name into an approval record — the exact
"the model wrote a string and called it authentication" failure §7 names.

**`calibrated` is not a parameter.** Refresh rules start uncalibrated
(`RefreshRule.calibrated` defaults to `False` in `marketops/domain/maintenance.py`).
No argument, in any product-loop tool, can mark a rule calibrated. Calibration is
an empirical claim and must be earned from observed data, so it cannot be something
a caller asserts in a request.

**Time is not a parameter.** Every timestamp comes from `utc_now()` inside the
application layer. A caller cannot backdate a version, a pause, or an activation.

**Exclusions only grow.** `additional_exclusions` is a union with the profile's own
list, sorted for a deterministic `frozen_sha256`. There is no argument that removes
an exclusion — the direction of that operation is the one that gets people emailed.

**An import declares neither how the batch was obtained nor which exclusions apply
to it.** `import_motherpool` has no `source_method_id` parameter: the tool reads a
registry export and that is what `import.register.v1` says it does, so an argument
would only let a caller label those rows `search.engine.v1` — same family in the
diversity metric, opposite truth in "how many queries ran". It has no
`declared_exclusion_ids` parameter either; that list is read from the profile, and a
row marked with an exclusion the profile never declared refuses the whole artifact
rather than dropping that row. The same rule as `by` and `calibrated`: a field that
lets a request create a rule is a field that lets a request invent one.

**Activation names a version.** `activate` without a version is refused rather than
defaulting to "the latest". The latest version may be one that was written and never
reviewed, and silently making an unread version effective is what that check exists
to prevent.

**Which aspects get re-checked is a deployment declaration, not a request field.**
`run_maintenance` has no `aspects` parameter. The set comes from
`REFRESHABLE_ASPECTS` in `marketops/application/refresh.py`, and the plan endpoint
reports it alongside the plan itself, because "this aspect is due" and "this
deployment re-checks this aspect" are different facts and a caller that could pass
the second one in would be choosing its own scope. The filtering happens **before**
the claim: `MaintenanceStore.claim` writes a new row with an incremented attempt
when it finds a `FAILED` one, so a tick that claims work it cannot do grows the
ledger every time it runs.

**Re-reading a record is not an observation; counting the records is.**
`run_maintenance` reports what it found, and whether that report also becomes an
`Observation` is a per-cell declaration by the refresh use case, not a property of the
tick. Only `source_coverage` declares one, and the distinction is the aspect's
*subject*: that aspect asks which sources we hold, those rows live in our own store,
and enumerating all of them is a **complete** measurement of it — not an approximation
of one. The blanket rule this replaces ("reading local records again is not an
observation") described a different action: re-reading a record *about* an object and
claiming the object was re-checked. That action is `promote_receipt`, which still
writes no verification timestamp. The test is not local versus remote; it is whether
the aspect's subject *is* the local record.

What the old rule protected still holds, and is why the callback does not write the
row itself: `run_once` writes the `Observation`, after the claim has actually run, so
no refresh use case can push a real re-check out by one cadence. Re-scheduling an
aspect that has no such declaration still requires a `ChangeSignal` — "this cell needs
another look" is something only a party that knows that can say. And the convergence
argument survives the change: a signal stops selecting the cell once an `Observation`
at or after it exists, so a tick that has answered it does not leave it due forever.

**Reading a receipt is not re-checking the venues in it.** `promote_receipt` folds
one receipt's rows into the venue asset pool and leaves `last_verified_at` empty on
every row it writes, along with `evidence_ids` and `domain_evidence_ids`. A receipt
is the product of admitting a list; the venues on it were not re-looked at. Writing a
verification timestamp there would give every row "just re-checked" on the day of the
first import — and the next maintenance tick would skip the lot, for the aspect's
whole cadence. The same trap, and the same discipline as
`tests/test_domain_temporal.py::test_observation_alone_never_counts_as_reverification`:
a value that is *nearly* the right one, landing exactly where the real measurement
will later be read from.

**A fold cannot be truncated, backdated, or pointed at another market.**
`promote_receipt` takes two parameters, `receipt_id` and `run_id`. There is no `limit`:
truncating a read shows fewer rows, truncating a write drops rows, and no column of
the pool would show that the receipt had more. There is no `at` / `seen_at` (the
timestamp is `utc_now()`), no `workspace` (lineage belongs to the row and is taken
from this deployment's profile; a parameter would let one call write into another
deployment's pool, which lives in the same database file), and no `source_method_id`
(the receipt already states how that batch was obtained). The same rule as `by` and
`calibrated`: a field that lets a request create a fact is a field that lets a
request invent one.

`run_id` is not an exception to that rule, and the difference is the whole point of
adding it. It names **which Run this fold belongs to**, and it buys nothing to the
caller: the fold ledger is keyed by that Run, the store refuses a Run it has never
admitted or one that has already stopped, and `close_run` compares the ledger against
whatever `folded` the caller reports. Omitting it is not a loophole either — `None`
is a declared fact ("this row belongs to no round"), and the pool writes no ledger
entry for it. It cannot point at another market: a Run store binds to one workspace
the way the pool does, so a Run id minted for another workspace has no ledger here,
and a fold carrying it is refused with the pool untouched — the row is not written
and then corrected, it is not written at all.

**A fold is idempotent by construction, not by a prior check.** Fold the same receipt
twice and the pool gains no row and `first_seen_at` does not move. That holds because
the handle is derived from the source number (or from name and place), so the second
fold lands on the same row and the merge rule refuses to erase a filled cell — not
because the tool asks first whether that receipt was already folded. A prior check is
a read-modify-write, and under two overlapping workers the answer it reads may already
be out of date.

**Unmeasured is not zero.** The census reports `new_source_gaps` as a count of what
is pending (it reaches zero, and reaching zero is a measurement), and
`new_venues` as `null` under `not_measured`, because a genuinely new venue can only
be observed by a *new search* and this layer reads local records without network
access. Zero would be a claim that a search ran and found nothing.
`list_responsibilities` carries the same distinction one level down: its
`summary.assets_without_a_relation` counts pool venues that appear in no row of the
table, so it is a whole-pool fact — a read filtered by venue or by account returns
`null` and a reason in `summary_gap` rather than a 0, because 0 would be read as
"every venue has someone responsible for it", which is the one direction that number
can be misused in.

**Recording a judgement is not re-verifying the relation.**
`remember_responsibility` writes `first_seen_at` from `utc_now()` and leaves
`last_verified_at` empty on every row it writes, for exactly the reason
`promote_receipt` does: "how long has this relation gone unchecked" only becomes a
meaningful question once the table exists, and filling the column would answer it
"just re-checked" on the first day and make the next maintenance tick skip the lot.
The freshness cadence for this aspect is §11's to define, and this layer deliberately
has no opinion about it.

**A judgement declares neither its market nor its timestamp nor its venue.**
`remember_responsibility` has `venue_asset_id` and `judgements`, and nothing else.
No `workspace` (lineage belongs to the row and comes from this deployment's profile;
a parameter would let one call write into another Scene, and §5's "the same venue and
the same account can be judged differently under two Scenes" means two Scenes each
write their own row, not one call writing on another's behalf), no `at` (the
timestamp is `utc_now()`), and no `last_verified_at` (see above). `venue_asset_id` is
a **parameter rather than a field of each row** on purpose: the unit of work is "I
just looked at *this* venue and found these parties responsible for it", and a row
that carried its own venue would let one call write into any number of venues with
nothing in the report able to show it. The relation itself is a closed set
(`VenueRelation`, six members); a value outside it is refused **for that row**, not
for the call.

**One bad row does not carry the batch, and a fold is not a second merge rule.** Each
judgement gets its own disposition, the same way `draft_outreach` judges each
recipient on its own (`_as_draft` in `marketops/application/outreach.py` is the first
place this rule landed). Two rows of one batch that state the same relation fold into
one row through the store's own `upsert_relation` — the very same `merge_relations`
that folds against the database — so there is exactly one definition of how two
observations of one relation combine. The `folded` disposition is a *report* of what
happened, not a second code path; without it, a caller would read a row count as a
relation count.

**A relation is never hung on a venue that is not in the pool.**
`remember_responsibility` looks the venue up first and refuses the whole call when it
is absent, rather than writing the row anyway. The table is keyed by
`venue_asset_id`, and the read side joins the venue's *name* to make a row legible —
so a handle the pool has never heard of produces a row that can never surface in any
report while sitting in the table looking researched. The refusal names both
remedies: fold the receipt in first, or check the handle (it is derived —
`src-<hex16>` / `named-<hex16>` — not a venue name).

**A row says which judgement produced it, and those ids are written down, not
inferred.** Both the write receipt and `list_responsibilities` carry
`decision_record_ids` — the `decision_record.record_id` of each judgement that
produced or re-confirmed the row (the id is `record_id` in `decide`'s receipt and
`decision_id` in a research run's findings; one string space, named here after the
table it is the key of). The previous live acceptance could only reverse-look-up the
producing call by "evidence set exactly equal *and* answer value exactly equal", and
that is not a causal proof: one account can be asked two different questions over the
same pages, and set equality cannot tell those apart. The union is what makes the
look-up total — a relation judged twice names both calls.

The two paths onto this table differ in exactly this cell, so it is readable rather
than a matter of discipline. `remember_responsibility` has no decision ledger in hand
and writes an **empty** list, which says "this row was not reached by a judgement";
`submit_findings` (the research path) requires a `decision_id` per finding and checks
it against the ledger before anything is written — the work item, the subject, the
spec, the outcome and the chosen answer. The cell is **not** an argument of
`remember_responsibility`: a caller who could declare "a judgement backed this row"
would be making a claim this layer cannot check, and the row would then be
indistinguishable from a researched one.

**Reading the table by venue and by account are two questions, and asking both is
refused.** `list_responsibilities` takes `venue_asset_id` or `account_id`, never
both: "what is this account's relation to this venue" is one row, and the
venue-scoped read already returns it — a second read path for the same sentence would
be a second place for it to be defined. The venue-scoped read is §5's question and
the account-scoped read is §6's; both exist because the pool gets asked in both
directions, and the account-scoped one is what gives
`AccountVenueRelationStore.relations_for_account` its first production caller.

**An account does not hang off one venue.** `remember_accounts` has no
`venue_asset_id` — `remember_responsibility` does. The unit of work there is "I just
looked at *this* venue and found these parties responsible for it"; an account is not
a property of one venue, since one account may manage ten of them, and "which ones"
is the responsibility table's fact (the account-scoped read of `list_responsibilities`
already answers it). A `venue_asset_id` here would have to be picked from a set, and
the report would show a pick rather than the set. Same reason as the others: no
`workspace` (a parameter would let one call write into another Scene, whose rows live
in the same database file, and §6's "objective facts may be reused across Scenes"
means the second Scene writes **its own** row), no `at` (the timestamp is `utc_now()`),
and no `last_verified_at` — recording an account is not re-checking it, and filling
that column would answer "how long has this gone unchecked" with "just checked" on
the day the pool is created, which is exactly where the next maintenance tick reads it.

**A verdict that was not given is not a verdict that clears.** `AccountClaim.status`
defaults to `discovered`, which is §6·1's "no judgement yet", and the merge rule
refuses to let `discovered` overwrite a verdict already in the pool — so a
re-research that says nothing about an account's standing leaves the standing where it
was, rather than resetting it. The two producers default in opposite directions and
that is one rule applied to two things: `RelationClaim.relation` defaults to `""` and
an empty string is *refused*, because `unresolved` is itself a judgement that needs
evidence and giving nothing there is giving it wrongly; `discovered` is the absence of
a judgement, so giving nothing has an honest value to take. A status outside the seven
is refused **for that row**, and the refusal names all seven.

**Whether a row can be written is checked before whether it is a repeat.** `folded`
means an earlier row of the same batch already stated that account. It is a
consequence, not a criterion: checking duplicates first would report a row carrying an
impossible `status` as "repeat, skipped", and that bad value would never be mentioned
again from this side while the pool keeps its old verdict — which reads exactly like
"this batch's verdict is that one". A row that is both a repeat and unwritable is
reported `rejected`.

**An account pool row carries no second copy of §5.** `list_account_pool` joins the
responsibility table to say which venues an account manages, and the venue's *name*
comes from the asset pool. A deployment missing either returns `linked_assets: null`
with the reason in `linked_assets_gap` rather than `[]`: an empty list reads as "this
account manages no venue", which is a claim about the market rather than about the
deployment, and it is the one direction that field can be misused in.
`summary.accounts_without_a_relation` carries the same distinction at pool scale — it
is the pending-work queue ("which accounts have no venue attached"), so a read filtered
by `account_id` returns `null` and a reason instead of a 0 that would read as "every
account has one". Giving an `account_id` is §14's `get_account`: the same row builder
and the same read path, only filtered — it goes through `AccountPoolStore.get_account`
rather than filtering a full read, which is what gives that port method its first
production caller. "Not in the pool" is `lookup.found = false` and an empty list, not
an error, because it is a normal answer with a different remedy from "could not read".

**The pool read does not create the pool.** `list_account_pool` reports `no_store` and
an empty list when this deployment has no database file, and `remember_accounts`
refuses outright, saying so. A read that created its own subject would make "the pool
is empty" and "nothing has ever been recorded here" the same answer, and it is the
second one a person needs; a write that proceeded would report every row as
`evidence_unknown` (each row's evidence must resolve in the store, and there is no
store), which looks like a failure of the batch rather than a missing step that comes
first. A deployment that wired no pool at all gets `not_wired` — the same shape
`list_assets` and `list_responsibilities` return, because a read tool that raised here
would be the one member of its family that answers a missing wire with an error. On
that branch `lookup.found` is `null` and **not** `false`: the question was "is this
account in the pool", and the pool is not in *this* deployment — `false` would say "this
market does not have it", which is a different sentence.

## 4. Not yet in this catalog

This section is the honest list of what the product loop needs and does not have
yet. Names here are **not** catalog rows, deliberately: the union check in
`tests/test_security_catalog.py` asserts the documents and `TOOL_CATALOG` are equal
exactly, so a name placed in §2 before the code backs it is red, and a name placed
here is not registered by anything.

Every name this section carried in the previous revision — the five outreach-chain
tools, the `authorize_wave`/`send_wave` pair, and `get_ops_report` — is now a row in
§2, added in the same commit as its implementation and tests. The heading stays even
though the list is empty: it is the end anchor the machine check parses to, and it
is where the next such name goes.

Two parts of the loop are absent from this catalog **on purpose**, and the reason is
worth keeping next to the list:

- **The mail-capture ingest endpoint** (`marketops/adapters/mail_capture.py`) is an
  HTTP surface rather than a tool. A webhook delivered by a mail provider carries no
  principal, and every tool in this catalog is authorized by one taken from the
  transport layer. As a tool it would either invent an identity for whoever posted
  the body, or be the one entry in the catalog reachable by nobody — and both are
  worse than a route that is not an MCP tool and says so.
- **Writing to Feishu** stays behind `feishu_write_authorized = false` with no tool
  at all. There is no partial version of that gate worth building.

A name appearing in §2 is not the same claim as a name appearing in a deployment's
`tools/list`. `send_wave` is absent from the wire in a deployment whose outbound
capability is off, and that absence is a deployment fact stated in §2 rather than an
unfinished tool listed here.

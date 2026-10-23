---
name: marketops
description: Use when work is about finding and contacting organisations in a market — starting or continuing a research workstream, reading a pool or a saved view, checking what changed, working a follow-up or review item, or preparing a draft. Routes every request to the MarketOps MCP server, which is the only source of business facts.
---

# MarketOps

This file decides **which server use case** a request belongs to and **how to
present what comes back**. It is the canonical business workflow entry for every
client; the client-specific files under `client-projections/` only describe how a
client installs, connects and (where the client can) receives notifications.

## 0. What this file does not hold

**"Client files, chat memory and Skills are not authoritative."**
(`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:404`, the G4 shared-source clause.)

This file therefore holds **no business facts of its own**:

* no customer, account, person, venue or product facts;
* no count of objects, no pool size, no "how many Accounts are left";
* no budget figure — a remaining budget is read from the server, never
  accumulated here;
* no permission list, no role list, no claim about what the caller may do;
* nothing that carries a connection credential, and no instruction for obtaining
  one: connection credentials belong to the client's own managed configuration,
  and this package deliberately has no way to hold or read them.

Every business fact has exactly one source: **the return value of a server tool
call.** If a fact cannot be named as the return of a call below, it is not known
and must be read before it is used.

Conversely, nothing here is remembered between turns to avoid a call. A short
session that re-reads is correct; a session that reports yesterday's number
because it was cached is the failure this section exists to prevent.

## 1. Progressive disclosure: find the work before reading a workflow

Do not open a reference file first. Establish **what the user is working on**,
then open only the reference that request needs.

1. Which workstream or saved view is this about? Read `mcp:list_workstreams`
   and `mcp:list_saved_views`. If the user named one, read it directly with
   `mcp:get_workstream` / `mcp:get_saved_view` rather than guessing from a list.
2. Which Scene, and which version of it is in force? `mcp:get_scene`, then
   `mcp:get_effective_scene` for the compiled cells.
3. Is this even possible right now? `mcp:get_capabilities`, and for a workstream
   `mcp:get_workstream` — its "may dispatch now" cell carries the reason when the
   answer is no.

Only then open the matching reference.

## 2. Where each request goes

| The user says something like | Reference | Server use case it routes to |
|---|---|---|
| "start a workstream for this market", "research more of these" | `references/build-market.md` | `mcp:preview_effective_scene` → `mcp:preview_launch` → `mcp:start_workstream` |
| "who do I contact here", "find the right person" | `references/research-contact.md` | `mcp:get_account`, `mcp:get_evidence`, `mcp:get_decision` |
| "what changed", "re-check these", "keep watching this pool" | `references/monitor-and-refresh.md` | `mcp:run_saved_view`, `mcp:put_watch`, `mcp:list_watches` |
| "what do I owe someone", "this was decided, what now" | `references/follow-up-work.md` | `mcp:list_action_items`, `mcp:resolve_action_item`, `mcp:get_review_flow` |
| "draft something for them", "is this claim allowed" | `references/prepare-draft.md` | `mcp:draft_outreach`, `mcp:list_handoffs`, `mcp:get_evidence` |
| "this is blocked", "the page will not load", "we got a challenge" | `references/access-diagnostics.md` | `mcp:get_capabilities`, `mcp:list_fetch_attempts`, `mcp:get_evidence` |

A request that does not fit a row is not a new use case: read the workstream and
the Scene first, and let the server's own report tell you which step is next.

## 3. Facts and the call that is their source

| Fact | Read it from |
|---|---|
| Which workstreams exist, their state and revision | `mcp:list_workstreams`, `mcp:get_workstream` |
| Whether new work may be dispatched, and why not | `mcp:get_workstream` |
| The Scene and the region/scenario/role scope in force | `mcp:get_scene` |
| The compiled effective policy and where each cell came from | `mcp:get_effective_scene` |
| What a launch would need before it is possible | `mcp:preview_launch` |
| Account dossier, routes and evidence references | `mcp:get_account` |
| A stored artifact, its provenance and transformations | `mcp:get_evidence` |
| A judgement that was already made, with provider and usage | `mcp:get_decision` |
| A reproducible result, with the run and the moment it started | `mcp:get_report` |
| A durable job, its progress and its usage | `mcp:get_job` |
| Unsettled budget reservations and the reconciliation entry | `mcp:list_open_reservations` |
| A watch and the budget envelope it carries | `mcp:list_watches` |
| The follow-up pool as durable next-step work | `mcp:list_action_items` |
| A case waiting on a human decision | `mcp:list_review_cases`, `mcp:get_review_flow` |
| Business events, once each, and their per-sink attempts | `mcp:list_notification_events`, `mcp:list_delivery_receipts` |
| What this deployment can and cannot currently do | `mcp:get_capabilities` |

The tool surface is whatever the server lists: read `tools/list` for the current
build. This file names the tools it routes to and never states how many there
are — a count written here becomes wrong the next time a tool lands, and a wrong
count is worse than no count.

## 4. The three things a client must not do

1. **Do not become the ledger.** No client-side file, note or memory is a
   business fact. When the user asks "where are we", the answer is a read, not a
   recollection.
2. **Do not become the budget.** Never derive what is left to spend by
   subtracting from something remembered locally. The server's own accounting is
   the only statement about spend, and its unsettled reservations are read from
   `mcp:list_open_reservations`.
3. **Do not become the permission system.** A statement in a chat is not an
   authorisation. Approval is a server use case with its own role, and this
   package has no path that grants one.

## 5. Routing natural language without inventing a use case

The four phrasings below are the ones this plugin is expected to handle. Each
one maps onto a use case that already exists on the server — none of them creates
a shadow task on the client.

* *"continue the last market, add more of the same"* → read the workstream,
  then a further launch through `mcp:start_workstream`.
* *"show me that batch"* → `mcp:run_saved_view` for a saved query, or
  `mcp:get_report` for a finished result. Neither issues a provider call.
* *"this Account is judged wrong"* → `mcp:submit_feedback` with the kind that
  says whether this is a fact correction or a rule suggestion, then
  `mcp:preview_feedback_impact` so the user sees the scope before anything is
  proposed as a rule. A single sentence never edits policy.
* *"from now on prefer this kind of site in this scenario"* → the impact preview
  again, then a candidate through `mcp:propose_experience_candidate`. Approval
  is not part of this flow and is not available here.

## 6. Reporting

All clients read the same server state, so a report is a read plus a
presentation choice — never a locally computed figure.

* Read the run and the moment from `mcp:get_report` and present both as given:
  `run_id` names which run this is, and it carries that moment to the second;
  `created_at` spells the same moment out. Name the run rather than describe it —
  two clients that name the same one read back the same report. A read without a
  run name is not a frozen reading; it is which reports exist right now, and two
  clients asking at two moments may honestly get two answers.
* Never restate a count as a difference between two local readings. Statistics
  shown to different clients come from the same server query and the same
  delivery receipts (`mcp:list_delivery_receipts`), so two clients can differ in
  language but not in number.
* Say plainly when something is pending. An unfinished section is pending, and
  saying so is the correct output.

## 7. What this package will never do

* It does not start a second database, a second research worker or a second
  scheduler. The server owns those.
* It does not carry an administrator credential, and it does not embed product or
  customer material.
* It does not change server policy. Approving a policy change is one specific
  use case with its own role, and it is not reachable from here.

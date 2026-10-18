# MCP target-convergence catalog

> This document is the third catalog source read by `tests/test_security_catalog.py`.
> The other two are `MCP_SERVICE.md` (§2–§3) and `MCP_PRODUCT_LOOP.md` (§2).

## 1. Boundary

This is the control surface of the target-architecture convergence round
(`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md` §B01–§B25, plus the extensions in
`docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md` and
`docs/PRODUCT_LINE_AND_SCENE_MODEL.md`).

**These rows land before their handlers, deliberately.** The other two catalog
documents follow the rule that a §2 row arrives in the same commit as its
implementation. This round does not, and the reason is that it is built as
**five parallel workstreams sharing one catalog**. If each workstream named its
own tools, the same capability would appear under three names and the union
check in `tests/test_security_catalog.py` would be green while the surface was
inconsistent. So the names, roles and phase letter are frozen first, and an
unimplemented row answers `Phase K, 本构建尚未接线` — the same sentence §2 of the
service document describes for a tool this *instance* was not given, and the one
`MCP_PRODUCT_LOOP.md` §4 keeps as its end anchor.

`Phase K` rather than `J`: `J` is the product-loop round, and a build-progress
letter that names somebody else's round makes a later reader conclude the tool
existed then (`security/catalog.py`, the `PHASES` docstring).

Everything here is `_CONTROL` and **none of it is `_AUTHORIZE`**. The one place
a second human being has to act stays `approve_policy_change`, which is already a
row of §2 in the service document.

## 2. Target-convergence catalog

As in the other two documents, presence in this table is not a claim that a given
deployment wired it.

| Tool | Purpose and result |
|---|---|
| `start_workstream` | Accept one launch: bind an approved Scene version, persist a `LaunchRecord`, create the `Workstream`, and admit its first Run through the existing admission path — returning all three ids plus the queue state and when it will wake again. Resubmitting the same `(scene, version, idempotency_key)` returns **the same launch**, builds nothing and resets no budget; a composition that no longer matches the preview's subject is refused by name |
| `get_workstream` | Read one Workstream: its state and revision, the Scene version it is bound to, the launch that created it, and whether new work may be dispatched right now with the reason when it may not |
| `list_workstreams` | Every Workstream in this workspace with its Scene, state, revision and last change; a workspace with none says so rather than returning an empty list without a reason |
| `control_workstream` | Typed pause / resume / rename with the observed result. Pausing must give a reason and takes `expected_revision`; pausing one Workstream changes nothing about another, and a shared source quota stays shared |
| `preview_effective_scene` | Compile the declared Scene version into an `EffectiveScene`: the resolved GeoScope, ScenarioProfile, Offer, SalesMotion and overridable policy, each with its source, provenance, units, locale, owner and hash, **precedence spelled out and every conflict named one by one rather than resolved to a winner**. Reads only what is already on disk — no provider, no write |
| `get_effective_scene` | Read a frozen `EffectiveScene` by `(scene_id, version)`: its digest, its compiled cells with the same provenance, and the conflicts recorded at compile time. A version that is not on disk is refused and the versions that are get named |
| `withdraw_claim` | Withdraw one approved claim: every **unsent** draft that cites it goes to `hold`, unrelated work is unchanged, and the affected set is computed from evidence rather than from a keyword match. All of it in one transaction, or none of it |
| `submit_feedback` | Record one `FeedbackEvent` — literal feedback or an artifact reference, the principal and source session, the object/run/workstream it is about, its kind, the proposed scope and the moment. Natural language never edits global policy; this row only writes the event |
| `get_feedback` | Read one `FeedbackEvent` by id with its literal text, kind, proposed scope, and what has since been proposed on top of it |
| `list_feedback` | Feedback filtered by scope (object / run / workstream / segment / scenario / geo / workspace), newest first, with the count matched beside the count returned |
| `preview_feedback_impact` | What one feedback event would change if it were promoted: which runs, drafts, objects and policies are in scope, what each would be re-decided against, and which kinds of change are **not** available at this scope. Zero provider calls; it writes nothing |
| `propose_experience_candidate` | Turn repeatable experience into an `ExperienceCandidate`: the observation, the evidence it rests on, the scope it would generalise to, and its risk class. It is a proposal, not an approval, and nothing reads it as authority |
| `list_experience_candidates` | Candidates with their state, scope and evidence count; a candidates-evidence reference that could not be resolved is reported as such rather than counted as zero |
| `propose_policy_patch` | Propose a `PolicyPatch` from a candidate or from scoped feedback: the exact cells it would change, the version it would produce, and the `ReviewCase` it was filed as. Approval is **not** here — it is `approve_policy_change`, which requires the authorizer role and a different human being |
| `list_policy_patches` | Patches with their state, the review case they are waiting on, and — for a promoted one — the version it produced and the monitor/rollback path that version carries |
| `put_saved_view` | Save or revise a query over shared market facts and workstream assessments: the filter, the columns, sort, owner and visibility. Saving is not the budgeted or permissioned act; enabling a Watch over it is. An existing `(owner, name)` returns its current version rather than quietly overwriting it |
| `get_saved_view` | Read one SavedView: its query, version, owner, visibility and when it last changed. Every query-shaped field is echoed as stored, so a caller can tell their filter from the compiler's reading of it |
| `list_saved_views` | The views this workspace has, with owner, visibility, version and last change |
| `run_saved_view` | Run a saved query and return the rows: **zero provider calls, zero budget, zero Run**. An Account appearing in three views is one Account read three times, not three rows; a view whose query cannot be evaluated says which part could not be, rather than returning fewer rows |
| `put_watch` | Enable or change a monitoring rule over a view or object set: the aspects to watch, the freshness and trigger policy, the budget envelope (search and fetch ceilings), and the notification policy. This is the write that needs permission and budget; enabling a Watch over a paused Workstream is refused rather than queued |
| `list_watches` | Watches with their target, enabled state, revision, next due moment and budget envelope; a watch whose target view was deleted is reported as such rather than silently inactive |
| `open_action_item` | Put one new item into the follow-up pool: the object it hangs on (`account` / `route` / `review` / `campaign`), which of the four kinds of work it is (verify a contact, fill a fact, make a ruling, release a draft), why it is worth someone's time, who owns it, when it comes due, and the evidence it rests on. **It needs permission but spends no budget and starts no Run** — in this family `put_watch` is the budgeted write and this one is not. A duplicate is an insert failure, not a read-then-write: the rule lives on the `(workspace, action_type, scope_ref)` partial unique index and the refusal names the `action_id` on disk, because a "check first, then insert" implementation looks identical and lets two through under concurrency. The same `action_id` with byte-identical content is a replay (idempotent, no second revision); with different content it is a real collision and stops rather than passing as a duplicate name |
| `list_action_items` | The follow-up pool as a projection over durable next-step work: per item its object, type, owner, due moment, state, blocking item and the evidence it rests on; filterable by owner and by state. It is not a per-account status field and reading it changes nothing |
| `resolve_action_item` | Move one ActionItem to done or dismissed, or reassign it, taking `expected_version`. A replay of the same resolution is idempotent; a stale version is refused rather than overwriting newer facts |
| `put_schedule_definition` | Write a durable ScheduleDefinition: trigger type (one_shot / calendar / due_state / event), timezone, calendar rule, execution window, action spec, budget policy and notification policy. **MCP configures, reads and controls; it does not run a timer** — execution belongs to the one managed trigger driver |
| `list_schedule_definitions` | Schedules with their revision, enabled state, next due moment, timezone and action spec; the trigger type is named, and a schedule whose action spec no longer resolves is reported rather than left looking armed |
| `control_schedule` | Enable / disable / pause one schedule taking `schedule_revision`, with the observed result. Changing scope, budget or notification target is a new revision with permission, not a field edit — "look at it daily" cannot become a daily full re-crawl |
| `fire_schedule` | The single application use case a trigger must go through, carrying `schedule_id`, `schedule_revision` and a caller-supplied `fire_id`. Replaying the same fire returns the same outcome and creates no second Run; the due check happens **before** any provider call, and an empty queue spends nothing. An external cron, Hosting or ServerOS is only ever another caller of this same row |
| `put_notification_subscription` | Write a subscription: principal or team, scope, filters and severities, sink, delivery mode, quiet hours and timezone. The event itself never records who was notified — subscriptions and deliveries are separate facts |
| `list_notification_events` | Immutable business-change events emitted **once** each, with kind, severity, scope, summary and the source event they came from. One event delivered to N sinks is one row here and N receipts below, never N events |
| `list_delivery_receipts` | Per-sink delivery attempts: which event, which subscription, which sink, which attempt, accepted or unknown, message reference, failure and retry-after. A failed sink retries the notification only — it never re-runs the research, and it never mutates the event |
| `put_session_binding` | Record a client session's convenience focus (which Workstream / SavedView / review queue, and the report cursor). It grants no permission, is not a foreign key for any Account or Job, and takes no part in business idempotency — every write still states its own object and version |

## 3. Boundaries these tools carry

* **The authorizer role is untouched.** Nothing in §2 can approve a policy change,
  authorise a wave, or mint a role. `approve_policy_change` and `authorize_wave`
  keep the roles they had, and the reason they are separate is written in
  `security/catalog.py` at `_AUTHORIZE`.
* **A preview never spends.** `preview_effective_scene`, `preview_feedback_impact`
  and `run_saved_view` are readings: they issue no provider call and open no Run,
  and each is pinned by a test that checks the construction rather than today's
  behaviour.
* **One trigger driver.** `ScheduleDefinition` and `Watch` do not each carry a
  timer. They both end at the Run admission path that already exists, and
  `fire_schedule`'s primary key is what makes a second driver unable to double-fire.
* **`watch` and `bounded_auto` are not permission shortcuts.** Pause, exhausted
  budget, a Review hold and a changed scope or version each stop the next Run
  regardless of which mode asked for it.

## 4. Not yet in this catalog

Empty, and the heading stays: `tests/test_security_catalog.py` parses to this
end anchor, and a source whose end heading is renamed raises `ValueError` rather
than silently returning an empty set.

Names that belong to this round and are **not** tool rows, with where they live
instead:

* `GeoScope` and `SalesMotion` — compile inputs of `preview_effective_scene`, not
  objects with their own verbs.
* `ExperienceCandidate` and `PolicyPatch` approval — the approval verb is
  `approve_policy_change` in the service document's §2, not a row here.
* `NotificationEvent` production — business state emits it internally. There is
  deliberately no `emit_notification` tool: a caller who could emit one directly
  could produce a second event for one business change.
* The plugin and client projections — `marketops-plugin/`, which is not an MCP
  surface.

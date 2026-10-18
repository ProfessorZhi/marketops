# MarketOps Architecture Decisions — Discovery Round 2

```yaml
status: proposed defaults awaiting user confirmation
phase: architecture-discovery
last_updated: 2026-09-21
principle: freeze business boundaries before implementation details
```

This document converts hidden product questions into explicit architecture decisions. Each item has a recommended default; the recommendation is not considered frozen until confirmed by product discovery or experiment.

---

## AD-01 — Canonical entities are global; relevance is Scene-scoped

### Problem

The same organisation, person or venue may matter to more than one Scene.

If each Scene stores independent copies, the system will repeatedly research the same Council/domain/person and may disagree with itself.

### Proposed default

Use a global factual layer:

```text
VenueAsset
Account
AccountVenueRelation
Person
Role
ContactPoint
Evidence / Observation
```

Then attach Scene-specific interpretation:

```text
SceneAssetAssessment
SceneAccountAssessment
SceneRouteAssessment
SceneFreshnessPolicy
SceneResearchPriority
```

Example:

```text
Example City Council
    global Account
       │
       ├── Scene A: AcmeCorp → eligible, grounds route = high value
       └── Scene B: another product → maybe irrelevant / different roles
```

Facts such as the organisation domain or the literal public email should not be rediscovered independently for every Scene. Their **business relevance, grade and freshness requirement** can differ by Scene.

### Consequence

The current implementation can keep existing route models during the research milestone, but the target architecture should avoid making Scene-specific A/B/C/D grades the identity of a global contact fact.

---

## AD-02 — One MUC/Hermes MarketOps Steward manages multiple Scenes

### Problem

Creating one permanent Hosting Agent per Scene gives each Scene separate memory/skills, but creates profile sprawl and fragments the user's control surface.

### Proposed default

One MarketOps Steward per user/tenant manages many Scenes through MarketOps MCP.

```text
Member-A
  ↓
MarketOps Steward (Hermes)
  ├── Scene: UK natural grass
  ├── Scene: US schools
  └── Scene: future product/market
```

Hermes memory stores user-level preferences and operating conventions.

Scene-specific operational knowledge remains in MarketOps ScenePack and is loaded through MCP when the user refers to a Scene.

Create a dedicated Hosting Agent per Scene only if future access-control, customer-facing isolation or extremely different identities require it.

---

## AD-03 — Scene has explicit lifecycle, priority and budget envelope

### Proposed state

```text
draft → active ↔ paused → archived
```

A Scene owns:

- priority;
- schedule/refresh policy;
- search/token/spend budget;
- optional concurrency cap;
- run mode;
- owner/tenant;
- current ScenePack version.

Suggested operating modes:

```text
bootstrap       build/expand mother pool and Account coverage
maintenance     refresh only due/change items
campaign_prep   aggressively refresh routes required for an upcoming authorized campaign
```

These modes adjust due-work planning; they do not change safety/authorization rules.

---

## AD-04 — MarketOps globally schedules due work; Quay executes it

MUC/Hermes cron triggers a Scene check.

MarketOps compares active Scenes, priorities, budgets and due items and produces SceneRuns / WorkItems.

Quay should not independently decide which business Scene deserves budget.

```text
Hermes Cron/manual request
       ↓
MarketOps Scheduler/Planner
       ↓
priority + budget + due-state
       ↓
SceneRun / WorkItems
       ↓
Quay execution
```

If multiple Scenes are active, MarketOps should support fairness and per-Scene caps so one huge mother pool cannot starve all other Scenes.

---

## AD-05 — Fact freshness is type-specific, evidence-aware and temporal

Do not assign one global TTL to an Account.

Different fact classes change at different rates.

Example policy dimensions:

| Fact | Typical refresh semantics |
|---|---|
| venue coordinates / stable asset id | source-change driven, low frequency |
| Account official domain | low frequency + failure/change trigger |
| ownership/operator relation | periodic + source-change corroboration |
| person currently in role | higher frequency / before campaign |
| literal work email | refresh with person/role and before sending |
| historical project responsibility | historical fact remains true for that period; do not pretend current |
| suppression/unsubscribe | durable until explicitly resolved by policy |

Freshness is a Scene policy because a high-value campaign may require more recent verification than background market intelligence.

---

## AD-06 — Evidence has temporal and source semantics, not only confidence

When sources conflict, MarketOps should reason over:

- source authority/relevance;
- publication/effective date;
- observation timestamp;
- whether the source claims current or historical responsibility;
- whether newer evidence supersedes older evidence.

Example ordering is contextual, not a universal numeric score:

```text
current official staff/department page
    often stronger for current role
than
2021 official meeting minutes
    strong evidence for 2021 role but weak for currentness
```

A direct human reply saying “I no longer handle this; contact Jane” is a new high-value routing observation and should trigger a research refresh, but still should not silently create an email not literally provided/verified.

---

## AD-07 — Account research priority is a first-class object

Not every eligible Account deserves equal research spend.

A Scene should compute/maintain a `SceneAccountPriority` from signals such as:

- strategic fit;
- number/type of relevant assets;
- evidence quality/gaps;
- current route quality;
- route freshness;
- prior research cost;
- campaign urgency;
- manual priority override.

DecisionProvider may help with fuzzy ranking, but the final hard budget/queue policy belongs to MarketOps.

---

## AD-08 — Research sufficiency is Account-class specific

Research should stop when it has enough useful coverage, not when the agent feels tired or the web is exhausted.

Example:

```text
small club:
  one strong operational route may be sufficient

large council / university:
  require redundancy across grounds/facilities/operations
  or multiple route opportunities before stop
```

The ScenePack can define sufficiency profiles by Account class.

Hard query/fetch/token/time limits remain absolute.

---

## AD-09 — ScenePack uses base + scene overlay rather than duplicating generic skills

Avoid making every Scene relearn generic public-web research and evidence rules.

Target composition:

```text
MarketOps BasePack
  - universal evidence rules
  - no-evasion rules
  - generic research lifecycle
  - tool semantics
  - failure taxonomy

ScenePack Overlay
  - source methods
  - role/function vocabulary
  - query playbook
  - Account classes
  - sufficiency goals
  - freshness policy
  - decision specs
  - writing policy
```

The effective pack is:

```text
BasePack version + ScenePack version
```

Every SceneRun records both hashes.

---

## AD-10 — Hermes uses one generic Steward Skill, not one skill per Scene by default

The MUC/Hermes Agent should install a stable `marketops-steward` skill explaining:

- MarketOps concepts;
- MCP tools;
- how to create/query/pause Scenes;
- authorization boundaries;
- how to present reports and exceptions.

When acting on a Scene, Hermes retrieves Scene configuration/state through MCP.

Scene-specific data is not copied into permanent Hermes memory unless it is a tiny user-facing convention.

This reduces Skill/Memory synchronization problems across many Scenes.

---

## AD-11 — Claude workers receive frozen Scene context per WorkItem

A Quay-launched Claude Code worker receives:

```text
BasePack version
ScenePack version
WorkItem
Account dossier
relevant prior observations/evidence
budgets
Research MCP config
```

It does not rely on persistent user-level Claude memory.

For repeatability, worker-level `CLAUDE.md`/instructions should be generated from the frozen pack rather than accumulated ad hoc on a host.

---

## AD-12 — Scene evolution creates candidate patches, never direct production mutation

Experience can propose changes to:

- source priority;
- query playbook;
- role synonyms;
- research strategy;
- sufficiency heuristics;
- DecisionProvider specs;
- writing examples/policy.

But evolution is:

```text
ExperienceCandidate
→ ScenePatch proposal
→ held-out replay
→ metric comparison
→ risk gate
→ versioned promotion
```

not:

```text
agent had one experience
→ update permanent Skill immediately
```

Generic Hermes skill curation may still help the Steward UX, but canonical ScenePack mutation flows through MarketOps + Quay evaluation.

---

## AD-13 — Human attention is an exception queue, not a mandatory pipeline stage

The product should attempt to make the normal path unattended.

Human review queues are for:

- responsibility/evidence conflict;
- unresolved identity collision;
- medium/high-risk ScenePack promotion;
- campaign/wave authorization;
- ambiguous human reply;
- policy/compliance exception;
- manual strategic prioritization.

No routine “approve every Account” step should exist unless a specific Scene requires it.

---

## AD-14 — Reply feedback enters the research graph

Replies are not only sales outcomes.

Examples:

```text
“I no longer manage this”
→ stale/superseded role signal + stop follow-up

“Jane handles our pitches”
→ new RouteOpportunity / research target

“Use grounds@example.org”
→ observed literal alternate contact + new verification work

“Not interested”
→ outreach outcome, not evidence that Account is irrelevant forever
```

Reply classification must separate market/account facts from campaign preference/outcome.

---

## AD-15 — Personalization is evidence-grounded and learns by role/Account class

One recipient gets one draft, but learning happens at reusable levels.

The Scene can learn that a particular **role/account class/value proposition angle** performs better, rather than memorizing prose from individual emails.

```text
reply outcomes
→ anonymized/structured writing experience
→ WritingPolicy/Example candidate
→ replay/review
→ ScenePack update
```

Do not automatically copy successful customer-specific claims into future emails.

---

## AD-16 — Tool/provider details stay behind MarketOps MCP

Hermes and Claude Code should consume business-semantic tools:

```text
search_public_web
fetch_public_url
classify_responsibility
rank_candidate_pages
submit_observation
```

They should not need to know:

```text
Serper API key
Crawl4AI endpoint
Jev vs Laya provider selection
provider retry/billing details
```

This preserves provider replacement and makes the Scene skill about the business process rather than infrastructure.

---

## Discovery questions still open

The next discovery round should resolve:

1. Whether Accounts/People/Evidence should truly be reusable across multiple products/Scenes, or whether business/legal isolation requires separate copies.
2. How Scene priority and spend caps should behave when many active Scenes compete for workers.
3. Which Account classes need distinct sufficiency policies.
4. What freshness windows make sense for people/roles/emails before campaign preparation.
5. Whether public phone/contact-form/profile signals should become future non-email outreach channels or remain research-only intelligence.
6. Which ScenePack fields may eventually auto-promote after replay vs always require human confirmation.
7. Whether one MarketOps Steward per user/tenant is sufficient for enterprise use or whether some teams need multiple role-specific Stewards.

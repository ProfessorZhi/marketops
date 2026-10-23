# Scene Evolution Architecture

> 2026-09-27 clarification: [Product/Scene model §9](PRODUCT_LINE_AND_SCENE_MODEL.md) defines outcome attribution, role facts vs priority, sparse feedback/selection-bias limits, and approval boundaries. Implementation stays in B20/B23 of [the unified plan](MEMBER_A_AUTOMATION_EXECUTION_PLAN.md). Research feedback is in R1/R2; actual reply-rate learning requires authorized R3 events. Historical session text and a high raw response rate never auto-activate policy, product claims, or sending. Multi-product/region reuse is permitted within scope; a new SKU or small region does not always require a new Scene.


```yaml
status: architecture-discovery
goal: each business Scene becomes progressively better at its own recurring work
principle: learn at the Scene configuration/skill layer, not by uncontrolled self-modifying production code
last_updated: 2026-09-21
```

---

## 1. Why Scene-level evolution exists

A MarketOps Scene is a recurring business process, not a one-shot prompt.

Repeated runs naturally produce experience:

- which sources yield real Accounts;
- which queries discover high-value official pages;
- which role terms are useful or misleading;
- which Account classes need more/less research;
- where research repeatedly stops;
- which pages/relations are commonly misclassified;
- which outreach angles get meaningful replies later.

The system should convert this experience into reusable scene-specific operating knowledge.

The goal is:

```text
same Scene + more verified experience
→ less repeated exploration
→ better source/query choices
→ better evidence quality
→ lower token/search cost
→ better contact coverage
```

without allowing unverified experience to silently rewrite business truth or safety policy.

---

## 2. Two intelligent roles

The initial architecture intentionally has two primary intelligent roles.

### A. MUC/Hermes Scene Steward

Long-lived, human-facing.

Responsibilities:

- understand the user's business intent;
- create/configure/pause/resume Scenes through MarketOps MCP;
- use Hermes memory for compact user preferences and operating conventions;
- use Scene skills/knowledge to explain and operate the Scene;
- schedule refresh checks through Hermes cron;
- summarize SceneRun results and exceptions;
- request human decisions/authorization when needed.

It should **not** perform every deep Account research job itself.

### B. Dispatched, restricted research worker

Short-lived, task-facing.

Responsibilities:

- execute one frozen WorkItem;
- search/fetch/read public evidence;
- resolve complex organisation/person/role relationships;
- return observations, evidence candidates, RouteOpportunities and ContactRoute candidates;
- return structured research diagnostics/experience signals.

It should be isolated, budgeted, and have no outreach authority.

Claude Code is the initial strong worker choice because the research worker path already targets isolated Claude execution. Hermes/DSH remain replaceable executor candidates and can be benchmarked later.

---

## 3. ScenePack — the evolvable unit

The canonical thing that evolves is a versioned `ScenePack`, not the MarketOps core engine.

Conceptual contents:

```text
ScenePack/
├── scene.yaml                 scope / country / product / scenario
├── source-policy.yaml         source methods, priorities, cadence
├── research-policy.yaml       sufficiency goals and hard budgets
├── role-matrix.yaml           positive/excluded role/function vocabulary
├── query-playbook.md          reusable search strategies/patterns
├── decision-specs/            typed decision definitions / thresholds
├── writing-policy.yaml        future outreach voice/personalization rules
├── examples/                  reviewed positive/negative exemplars
├── skill/
│   └── SKILL.md               procedural guidance for the Scene
└── CHANGELOG.md
```

Not every file must exist in V1. The important rule is that Scene-specific learning is versioned and reviewable.

### Canonical vs projections

- MarketOps owns the canonical ScenePack/version.
- MUC/Hermes may receive a Scene Skill / knowledge projection for the user-facing Scene Steward.
- Dispatched Claude workers receive the exact frozen ScenePack version attached to their WorkItem.

This prevents Hosting and worker guidance from drifting independently.

---

## 4. Skill vs memory vs ledger

These are different things.

### Business ledger

Authoritative facts:

- Accounts/assets/relations;
- people/roles/contact routes;
- evidence;
- freshness;
- job/run state;
- authorization/suppression/reply state.

Never stored only in agent memory.

### Scene Experience

Structured, non-authoritative learning signals derived from runs:

- query pattern yielded useful official page;
- source produced many duplicates;
- a role synonym repeatedly caused false positives;
- an Account class routinely needs a different search path;
- a research stop was later judged premature.

These live with MarketOps Scene history and can feed evolution.

### Skill

Reusable procedure:

- what to search first;
- how to distinguish operator vs user;
- preferred source order;
- when to inspect PDFs;
- when to stop/escalate.

Skills should be compact abstractions distilled from multiple experiences, not transcripts.

### Hermes memory

Use for compact user/agent conventions and pointers such as:

- preferred report style;
- default interaction preference;
- “this MarketOps Agent manages Scene X through MarketOps MCP”.

Do not fill Hermes MEMORY.md with thousands of account-level facts or raw research history.

---

## 5. Controlled evolution loop

```text
SceneRun N
   ↓
Outcomes + diagnostics + corrections
   ↓
Experience candidates
   ↓
Scene Improvement WorkItem
   ↓
Claude Code proposes ScenePatch
   ↓
executor evaluation / replay / gates
   ↓
promote | hold | reject
   ↓
ScenePack vN+1
   ↓
next SceneRun
```

### Step 1 — Collect experience

Every normal run already produces useful labels:

- source yield;
- query/fetch result;
- route quality;
- false attribution;
- hold/reject reasons;
- budget stop;
- human correction;
- later reply/referral/bounce outcome.

### Step 2 — Propose improvements

After enough new signal (for example N completed Accounts, N failed cases, a recurring error cluster, or a periodic cadence), the executor creates a meta WorkItem.

The same Claude Code runtime can act under a different role: **Scene Curator**.

It receives:

- current ScenePack;
- aggregated metrics;
- selected success/failure examples;
- human corrections;
- frozen safety/authority constraints.

It can propose changes to evolvable ScenePack files.

### Step 3 — Replay/evaluate

Candidate changes do not immediately become production.

Run current vs candidate on frozen historical samples.

Possible metrics:

- Account qualification precision/recall;
- route attribution error;
- A/B yield;
- useful-page precision;
- search/fetch/token cost;
- premature-stop rate;
- Source ROI;
- writing factuality/style scores later.

### Step 4 — Promotion gate

Classify changes by risk.

#### Low-risk

Examples:

- query ordering;
- source priority/cadence;
- additional reviewed search synonym;
- examples/reference notes.

May auto-promote only after deterministic benchmark gates pass.

#### Medium-risk

Examples:

- role/function mappings;
- research sufficiency targets;
- DecisionProvider thresholds;
- Account-class research strategies;
- WritingPolicy changes.

Require stronger evaluation and initially human review.

#### High-risk / authority

Examples:

- hard scene exclusions/inclusions that materially change target population;
- legal/compliance/send policy;
- suppression logic;
- authorization semantics;
- access-control boundaries;
- product claims allowed in outreach.

Never autonomously promoted by the Scene evolution loop.

### Step 5 — Version and rollback

Every promotion creates:

```text
ScenePack v12
→ ScenePatch
→ benchmark receipt
→ approval/gate
→ ScenePack v13
```

Old versions remain replayable and rollbackable.

---

## 6. Reuse MUC/Hermes self-improvement instead of duplicating it

MUC/Hermes already has useful primitives:

- persistent compact memory;
- per-agent knowledge workspace;
- skills;
- agent-created skill management;
- background skill review/curation;
- cron.

MarketOps should reuse these capabilities for the **Scene Steward** experience.

However, generic Hermes skill curation is not automatically the authority to mutate a canonical business ScenePack.

Recommended integration:

```text
Hermes learns a useful operating trick
        ↓
Skill/experience candidate
        ↓
MarketOps Scene improvement queue
        ↓
executor replay/gate
        ↓
approved ScenePack update
        ↓
refresh MUC Scene Skill projection
```

This gives us Hermes's learning UX without allowing an unsupervised foreground/background agent to silently change business policy.

---

## 7. Why not raw “self-learning memory”

Repeated experience can improve agents, but it can also propagate old errors.

Therefore MarketOps uses:

```text
experience
→ candidate abstraction
→ evaluation
→ promotion
```

not:

```text
one successful/failed run
→ write instruction into permanent memory
→ trust forever
```

Scene evolution needs quality labels and rollback.

---

## 8. The executor's role in evolution

The dispatch executor is valuable twice:

### Normal operation

```text
SceneRun
→ source/account research work graph
→ validation/fan-in
```

### Meta-improvement

```text
SceneImprove
├── collect candidate experiences
├── propose ScenePatch
├── run baseline replay
├── run candidate replay
├── compare metrics
├── policy/risk gate
└── promote/hold/reject
```

This keeps the learning lifecycle observable in the same execution system rather than creating a separate “self-improvement daemon”.

---

## 9. Evolution must optimize business metrics

Do not reward the Scene for producing more activity.

Bad objective:

- more searches;
- more pages;
- more contacts;
- more tokens spent.

Better objective:

- higher verified A/B coverage;
- lower false attribution;
- lower cost per verified useful route;
- lower human supervision;
- faster change detection;
- fewer unnecessary refreshes;
- later: higher meaningful reply/demo/referral yield without raising complaints/opt-outs.

A Scene may learn to **do less work** when that is the better policy.

---

## 10. Initial implementation sequence

1. Introduce `ScenePackVersion` and attach it to every SceneRun/WorkItem.
2. Add structured `ExperienceCandidate` records from normal research diagnostics.
3. Create a manually-triggered `SceneImprove` compound task through the executor.
4. Claude Code proposes a patch; it cannot apply it directly.
5. Replay current vs candidate on a frozen small benchmark.
6. Store benchmark receipt and human promotion decision.
7. Only after repeated successful cycles consider auto-promotion for explicitly low-risk fields.

Do not start by enabling autonomous skill mutation in production.

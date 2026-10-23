# MarketOps Requirements & Scenario Discovery

```yaml
status: architecture-discovery
source_of_truth: SOP + approved Feishu business discussions + validated experiments
last_updated: 2026-09-21
principle: scenarios first; components second
```

This document is deliberately written before further implementation. It captures **what users need the system to do**, what must remain under human authority, and what evidence should prove that the architecture works.

---

## 1. Product problem

The core business problem is not “web scraping” and not “build an agent”.

The problem is:

> Continuously turn broad public market sources into evidence-backed, actionable account/contact intelligence, then—only under explicit campaign authorization—execute outreach and route meaningful replies to sales with minimal human supervision.

The first validated scenario is UK outdoor natural-grass sports venues for AcmeCorp Pro. The product must remain parameterizable by country + product + scenario.

The initial user pain is concentrated in research:

- too much time;
- too much model/token cost;
- too much manual supervision;
- repeated rediscovery of the same organisations/pages;
- weak ability to distinguish “search miss” from “fetch/parse miss”;
- difficult account/person attribution at scale.

Therefore the system optimizes for **validated useful routes and low supervision**, not raw crawl volume.

---

## 2. Primary actors

### Business Owner / Campaign Owner

Example: Member-A.

Needs to:

- define a market scene;
- inspect coverage and quality;
- start/pause/resume research;
- approve or reject outreach waves;
- receive exceptions and meaningful replies;
- avoid supervising every research step.

Does not need to understand dispatch task internals or manage crawler processes.

### Research Operator / Builder

Example: Member-B / engineering.

Needs to:

- add/modify source methods;
- benchmark Search/Decision/Agent providers;
- inspect failed evidence chains;
- replay and audit runs;
- evolve rules without silently rewriting historical conclusions.

### Sales Owner

Needs only:

- effective human replies;
- qualified referrals;
- evidence-backed account context.

Should not receive crawl noise, OOO messages, parser failures or generic D routes packaged as strong leads.

### Automated Research Worker

Hermes / Claude Code / DSH or another approved runtime.

Receives a bounded work item, tools and budgets; returns observations and candidates.

It is not the authority for sending, suppression or immutable evidence mutation.

---

## 3. Core user journeys

### Scenario A — Create a market scene

User says:

> Maintain UK outdoor natural-grass sports venues relevant to AcmeCorp Pro.

The system must materialize a versioned Scene/Profile containing at least:

- country/region/language;
- product and use case;
- asset inclusion evidence;
- hard exclusions;
- responsible-entity relation types;
- role/business-function taxonomy;
- research budgets;
- freshness policy;
- outreach policy reference.

**Acceptance:** the scene can be inspected and versioned without reading an agent conversation.

---

### Scenario B — Maintain the mother pool

A scheduled or manual refresh runs source methods.

```text
SourceMethod
→ SourceRun
→ AssetCandidate
→ normalize/dedupe
→ VenueAsset
```

For the initial outdoor scenario, OSM/Overpass may be one source. It is not the entire product and not necessarily sufficient for every future scene.

The system must distinguish:

- candidate discovery;
- asset qualification;
- responsible-account resolution.

**Acceptance:** the same source refreshed twice does not duplicate unchanged assets, while new/changed candidates are traceable to their source receipt.

---

### Scenario C — Asset → responsible Account

Given one or more venue assets, determine which organisations own, operate, maintain, control bookings, or procure for them.

Possible outcomes:

- one Account controls many assets;
- one asset has multiple responsibility relations;
- responsibility is ambiguous → hold;
- source candidate is irrelevant.

**Acceptance:** venue name alone is never the account key; every promoted relation has evidence or is explicitly marked provisional/hold.

---

### Scenario D — Research one eligible Account

Once an Account is worth researching:

```text
known Account / domain / related assets
→ search official/public sources
→ fetch/read pages/PDFs
→ locate role/person/business function
→ locate literal public email
→ bind evidence
→ ContactRoute / RouteOpportunity
```

The worker may use Serper and normal HTTP; render/document fallbacks are optional capabilities.

**Acceptance:** a non-X ContactRoute can be traced to fetched public evidence; SERP snippets alone never create a route.

---

### Scenario E — No route found

“No email found” is not one state.

The system must distinguish at least:

- searched but no relevant page found;
- useful page found but no literal email;
- named role found but no email (RouteOpportunity);
- source/fetch blocked;
- parse failure;
- responsibility still unclear;
- research budget exhausted.

**Acceptance:** the next operator/worker can tell *why* no route exists and can choose a rational next action.

---

### Scenario F — Maintain freshness

Accounts and people change over time.

The system should refresh based on due work, not full re-search every day.

Triggers include:

- source changed;
- new asset candidate;
- evidence stale;
- contact page changed;
- bounce/reply/referral;
- campaign preparing to contact a stale route.

A missing name on the latest page is a change signal, not proof of departure.

**Acceptance:** previous evidence is preserved and later observations can supersede/withdraw a conclusion without silently rewriting history.

---

### Scenario G — Daily unattended service

The user configures a scene once.

A scheduler triggers a lightweight “check due work” request. MarketOps computes what is actually due and the dispatch executor runs it.

The daily result should be a business digest such as:

- new eligible Accounts;
- new/changed A/B routes;
- new RouteOpportunities;
- stale routes refreshed;
- blocked/failed items requiring attention;
- spend/token/search budget;
- no-change summary.

**Acceptance:** closing the MUC/Hosting chat does not stop the durable job, and reopening tomorrow can query the same job/account state.

---

### Scenario H — Prepare outreach

Research may automatically produce an **outreach candidate manifest**, but not send.

Before an address is allowed into a wave:

- current route evidence is valid;
- duplicates/history are checked;
- suppression/opt-out/bounce filters pass;
- campaign/template/sender/stage are frozen;
- manifest is immutable and auditable.

**Acceptance:** research completion alone can never move a route to sent state.

---

### Scenario I — Explicitly authorized outreach

A Campaign Owner explicitly authorizes a particular manifest or policy-bounded wave.

Future automation may execute within that approved window, but the authorization must specify scope and be revocable.

**Acceptance:** no authorization object → fail closed.

---

### Scenario J — Reply handling

Inbound email creates events:

- interested/demo/qualified referral → sales handoff + stop follow-up;
- unsubscribe/complaint → permanent suppression;
- hard bounce → invalid/bounced;
- OOO/system → log/defer;
- ambiguous human reply → review;
- new literal alternate contact → routing event, not automatic resend.

**Acceptance:** reply events update the business ledger and future send eligibility.

---

## 4. Confirmed product decisions — 2026-09-21

The following decisions were explicitly confirmed during architecture discovery and should now be treated as requirements rather than open questions.

### D1 — One Scene is one long-lived business task

A Scene is not a single dispatch primitive task. It is a durable market-research workspace such as:

```text
UK + AcmeCorp Pro + outdoor natural-grass sports venues
```

A Scene owns its:

- profile and scope;
- source methods;
- mother pool / Asset Pool;
- Account Pool;
- research/freshness policies;
- source and account priorities;
- outreach policy reference;
- historical runs and metrics.

A Scene can run continuously in the background.

Each execution cycle is a `SceneRun`.

```text
Scene
  ├── SceneRun 001
  ├── SceneRun 002
  └── SceneRun 003
```

**Acceptance:** pausing/resuming a Scene does not lose its mother pool, Account history, evidence or prior run state.

### D2 — The dispatch executor runs a SceneRun

MarketOps decides what work is due; the dispatch executor owns execution of that run.

A typical dispatch graph may include:

```text
SceneRun
  ├── refresh source(s)
  ├── normalize / dedupe candidates
  ├── resolve Asset → Account relations
  ├── qualify Accounts
  ├── fan-out Account research
  ├── validate evidence
  ├── grade routes
  └── produce run digest
```

The dispatch executor is therefore useful before, during and after contact research; it is not only a final validation step.

### D3 — Account-centric research, asset-aware evidence

Default research unit is the responsible Account, not one task per venue.

If one Council operates 80 sports grounds, the normal path is one Account research work item with those assets attached as context/evidence.

Very large/complex Accounts may be sharded by asset group or department only when the work-item size/budget justifies it.

### D4 — Research has a configurable sufficiency goal

The system must not search indefinitely after it already has enough useful contact coverage.

A Scene/Profile can define a research goal such as:

```text
preferred roles / functions
minimum strong routes
desired redundancy
max queries
max fetches
max wall time / token cost
```

The stop decision is deterministic at hard limits. A DecisionProvider may recommend continuing/stopping inside the remaining budget but cannot exceed the hard cap.

Different Account classes may have different sufficiency targets.

### D5 — No-email intelligence is still valuable

A named relevant person/role without a valid literal email remains a `RouteOpportunity`.

The system may also preserve supplementary **observed public contact signals**, for example:

- official phone;
- official contact page;
- public professional profile URL;
- department/role page.

These are research clues / alternate channels, not automatically valid email routes and not sending authorization.

### D6 — Facts are temporal; historical truth is not current truth

“Person X was responsible in 2021” must never silently become “Person X is responsible now”.

For important assertions, MarketOps must distinguish at least:

- source publication/effective date when available;
- first observed;
- last observed;
- last verified;
- current / historical / stale / contradicted / unknown-currentness;
- superseding evidence.

Official current staff pages may support currentness at fetch time. Old minutes, archived PDFs or dated news may support historical responsibility but need current corroboration before a current ContactRoute is treated as fresh.

### D7 — Refresh is incremental and executor-driven

The scheduler asks MarketOps to check a Scene.

MarketOps creates work only for:

- new/changed source rows;
- unresolved Accounts;
- stale/high-value routes;
- changed pages;
- bounce/reply/referral events;
- manual priority overrides.

The dispatch executor runs that due work and records its run/gates.

### D8 — Research budget and stop reasons are first-class data

A run/work item records why it stopped:

- sufficiency goal met;
- query/fetch/token/time budget exhausted;
- no useful next action;
- blocked/parse/provider failure;
- ambiguity/hold;
- human pause.

This prevents “no email” from collapsing into an uninformative result.

### D9 — Outreach is per-recipient personalized, evidence-grounded and human-sounding

Future outreach should not be a static template with only name/company substitutions.

Each ContactRoute can produce a distinct draft using:

- recipient role/business function;
- Account context;
- relevant assets/use case;
- only evidence-backed facts;
- approved value proposition and CTA.

A campaign still owns a stable `WritingPolicy`:

- brand voice;
- language/locale;
- maximum length;
- approved CTA;
- prohibited claims;
- formatting;
- opt-out/legal text;
- “anti-AI” style constraints (avoid generic praise, inflated adjectives, repetitive formulaic openings, fake familiarity and unsupported personalization).

The system should optimize for a natural concise human business email, not for evading an AI detector.

Every generated draft must pass factual-grounding and style validation before it can enter an authorized manifest.

### D10 — Source scheduling adapts to measured value

A SourceMethod should accumulate metrics such as:

- unique candidate yield;
- eligible Account yield;
- duplicate rate;
- A/B route contribution;
- cost/search/fetch burden;
- blocked/failure rate;
- freshness/change yield.

MarketOps may lower or raise its refresh priority/cadence from deterministic policy based on those metrics.

Low-yield sources are down-ranked, not silently deleted; coverage implications remain visible.


### D11 — Each Scene can improve from verified experience

Each long-lived Scene should accumulate reusable operating knowledge from repeated runs.

The evolvable unit is a versioned `ScenePack`, not the MarketOps core code.

Scene learning may improve:

- source ordering/cadence;
- search/query playbooks;
- role/function vocabulary;
- Account-class research strategies;
- sufficiency/stop heuristics within hard limits;
- DecisionProvider specs/thresholds;
- later, WritingPolicy examples and personalization strategies.

Experience does not directly mutate production. Candidate improvements must pass replay/evaluation and risk-appropriate gates before promotion.

### D12 — Two primary intelligent roles

Initial product architecture uses two intentionally different intelligent roles:

1. **MUC/Hermes Scene Steward** — persistent, user-facing, uses memory/skills/knowledge/cron and controls the Scene through MarketOps MCP.
2. **Dispatched, restricted research worker** — ephemeral, isolated, executes research/meta-improvement WorkItems with a frozen ScenePack and budgets.

Avoid a third permanent “self-improvement agent”. Scene improvement is another dispatch work graph executed by the existing research runtime under a curator role.


---

## 5. Product requirements inferred from the scenarios

### R1 — Durable business ledger

Required even if MUC/Hermes already has conversation memory.

The ledger must survive:

- a new chat;
- a different agent/model;
- worker restart;
- executor restart;
- memory reset.

It owns accounts, evidence, routes, freshness, campaign/suppression/reply facts.

### R2 — Minimal Agent memory dependency

Hermes memory may retain user preferences and compact operating conventions.

MarketOps must not require an external memory service for correctness.

OpenViking or other context systems remain optional experiments only if measured retrieval/experience problems justify them.

### R3 — Single scheduling authority at the user level

Reuse MUC/Hermes cron as the initial user-facing schedule trigger.

It submits “check/refresh scene” to MarketOps.

MarketOps computes due work.

The dispatch executor runs due work.

Do not run independent duplicate full-refresh schedulers in MUC, MarketOps and the executor.

### R4 — Durable asynchronous jobs

MCP/REST calls that start research return a durable job id.

A client disconnect must not cancel the job.

### R5 — Replaceable executors

Account research can be executed by Hermes, Claude Code or DSH through an `AgentRuntimeProvider`.

One work item selects one executor. Avoid nested-agent orchestration unless an experiment proves value.

### R6 — Bounded decision abstraction

Jev/Laya are not core business dependencies.

They implement `DecisionProvider` for narrow typed questions.

Rules/human authority remain above them.

### R7 — Provider-independent search/acquisition

Search/fetch/document tools remain ports:

- SearchProvider;
- HttpFetcher / BrowserFetcher;
- DocumentParser.

Business code must not care whether the provider is Serper, another SERP, Crawl4AI, or another compatible implementation.

### R8 — Explicit failure taxonomy

Failure reasons must be data, not merely logs.

The architecture must separate:

- no discovery;
- no route;
- blocked;
- parse failure;
- ambiguity;
- budget stop;
- provider error.

### R9 — Append-only evidence semantics

Corrections append withdrawal/supersession records; they do not silently mutate history.

### R10 — Authorization is not memory

Neither Hermes memory, Jev confidence, an agent statement nor a dispatch task status can constitute sending authorization.

---

## 6. What the product should NOT build initially

To avoid over-design:

- no independent generic agent platform — reuse MUC;
- no second chat UI — reuse MUC/Feishu;
- no mandatory OpenViking/vector-memory service;
- no separate always-on self-improvement daemon/agent;
- no Kubernetes until measured need;
- no custom web search engine;
- no custom browser automation framework;
- no custom generic task engine — reuse the dispatch executor;
- no requirement that every task use Claude/DSH;
- no requirement that every decision use Jev/Laya;
- no full UK re-crawl every day;
- no automatic outreach without explicit authorization.

---

## 7. Minimal target architecture

```text
                         Human surfaces
                     MUC/Hermes + Feishu
                              │
                         MarketOps MCP
                              │
                    ┌─────────▼─────────┐
                    │ MarketOps Service │
                    │ business ledger   │
                    │ scene/due planner │
                    │ policy/validation │
                    └───────┬───────────┘
                            │ work graph
                            ▼
                    dispatch executor
                 ┌──────────┼──────────┐
                 ▼          ▼          ▼
              Python     Hermes      Claude/DSH
              workers    worker      worker
                 │          │          │
                 └──── Search/Fetch/Parse ────┐
                                              │
                         optional typed decisions
                             Jev / Laya
                                              │
                                              ▼
                                    observations/evidence
                                              │
                                     MarketOps validation
                                              │
                                      business ledger
```

### Initial persistent components

Only three conceptual persistent systems are mandatory:

1. **MUC/Hermes** — user/agent interaction + small assistant memory + schedule trigger;
2. **MarketOps ledger/service** — business truth and durable jobs;
3. **Dispatch executor** — durable work/execution state.

Everything else is a provider/capability or optional optimization.

---

## 8. Architecture decisions still open

These are questions to answer by experiment, not assumption:

### A. Research executor

- Hermes worker?
- Claude Code?
- DSH?
- mix by work type?

Benchmark with identical frozen work items and tools.

### B. Decision model

- rules only;
- Jev;
- Laya;
- LLM structured output.

Benchmark per decision type. Do not choose one global winner.

### C. Source strategy

OSM is proven useful for outdoor venue discovery, but the second independent batch source is still required before scale acceptance.

### D. Storage adapter

JSONL is sufficient for current research/audit experimentation.

Move to a database when cross-account concurrency/query/update requirements materially exceed append-only files. Do not introduce Postgres merely because the target architecture might need it later.

### E. Browser/document fallback

Add only against measured failures. Maintain the no-evasion boundary.

---

## 9. Experiments required before architecture freeze

### Experiment 1 — Continuous-service smoke

Goal: prove the product behaves like a service, not a chat session.

1. MUC/Hermes submits a research job through MarketOps MCP.
2. Close/disconnect the chat client.
3. The dispatch executor continues execution.
4. Open a new session.
5. Query the same job and account results.
6. Re-trigger the same scheduled refresh.
7. Verify unchanged work is not duplicated.

### Experiment 2 — Executor ablation

Same 20–50 accounts, same source snapshot, same tools/budgets:

- Hermes worker;
- Claude Code worker;
- DSH if applicable.

Measure route quality, evidence correctness, token/cost, wall time, failure/retry rate and supervision required.

### Experiment 3 — DecisionProvider shadow

Freeze labeled samples for:

- page triage;
- responsibility relation;
- role/business-function classification.

Compare rules / Jev / Laya / LLM structured output.

### Experiment 4 — Second-refresh maintenance

Take a completed account cohort and introduce controlled changes:

- unchanged page;
- new contact;
- removed contact;
- page moved;
- 403;
- conflicting staff records.

Measure whether the system preserves old evidence, identifies due work and avoids false “person left” claims.

### Experiment 5 — Scene evolution replay

After enough corrected/verified research cases exist:

1. freeze a ScenePack baseline;
2. let Claude Code propose one bounded low/medium-risk ScenePatch;
3. replay baseline vs candidate on held-out prior cases;
4. compare quality/cost metrics;
5. reject regressions and preserve rollback.

The first experiment must be manual-promotion only.

### Experiment 6 — Source ablation

Compare OSM-only vs OSM + one independent source.

Measure unique eligible Accounts, overlap, precision, duplicate rate and cost.

---

## 10. Product success metrics

### Research value

- eligible Account yield per source;
- A/B route coverage;
- C/D coverage separately;
- RouteOpportunity count;
- evidence attribution error;
- no-route reason distribution;
- cost per newly verified A/B route;
- human-supervision minutes per 100 Accounts.

### Maintenance value

- duplicate work avoided;
- stale route detection;
- meaningful change detection precision;
- time from source/contact change to ledger refresh.

### Outreach value (future)

- authorized send count;
- valid reply rate;
- demo/referral rate;
- unsubscribe/complaint/bounce;
- time from effective reply to sales handoff.

Raw page count, raw email count and worker concurrency are operational metrics, not final business success.

---

## 11. Architecture freeze criterion

Do not call the architecture “frozen” until the system has demonstrated:

- at least two batch source methods;
- one end-to-end mother-pool → Account → evidence-backed route flow;
- Executor-owned execution without manual step-by-step dispatch;
- durable jobs queryable from a new MUC/Hermes session;
- second-refresh incremental behavior;
- at least one decision benchmark if Jev/Laya is to enter the hot path;
- research-only authority separation;
- fail-closed manifest/authorization logic in a no-send test.

Until then, the architecture stays intentionally modular and small.

# MarketOps MCP — One Business Interface, Jev-first Research

Updated 2026-09-27. The current release scope is R1/R2 in [the architecture](MEMBER_A_AUTOMATION_ARCHITECTURE.md) and [execution plan](MEMBER_A_AUTOMATION_EXECUTION_PLAN.md). This is the research interface and acceptance contract, **not a claim that every deployment or acceptance gate has passed**. At review baseline `3744262af703de5e1961878e8b3f97d860216d6b`, the 15 unique names in §2–3 are registered in `marketops/security/catalog.py`, with implementations assembled by `adapters/mcp_server/server.py`. A deployment may still leave handlers unwired and return explicit unavailable/stub behavior. Tool presence, runtime wiring, and live acceptance are different facts.

Business policy remains the frozen SOP. Current mode is research-only with no sending or external form submission. See [Architecture](ARCHITECTURE.md) for the single-MCP product boundary and [2026-09-23 review](ARCHITECTURE_REVIEW_2026-09-23.md) for Member-C's requirements, source references and code-level gaps. No new runtime acceptance was performed by this documentation revision.

**Current required research integrations: Serper, Crawl4AI, Jev and a deployment-provided research worker runtime. Laya is DEFERRED.** This explicitly supersedes the 2026-09-23 six-integration gate; it does not change old acceptance results. Docker, ServerOS integration and production outreach are deferred. Optional per-job invocation differs from implementation and release verification.

## Plugin, multi-session, scheduling and notification extensions (design, not registered tools)

The target client surface is one canonical MarketOps plugin/main Skill projected into approved clients, all connecting to the same authenticated MCP service. A chat session is a convenience view, not job ownership or authority. Session bindings, SavedViews, Watches, Actions, durable schedules and notifications are specified in [MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md](MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md); implementation belongs to the unified execution plan. These concepts are **not** registered tool rows until application use cases, authorization, schemas and tests land together.

MCP configures/reads schedule state; the protocol is not the timer. One managed trigger driver fires idempotent schedule events. Client cron may poll Notification Inbox but must not create a second full-refresh loop. Server-to-arbitrary-chat push is client-specific and not assumed. Feishu chat notification is a future sink separate from the existing Bitable adapter and stays blocked until authorization/credentials/live verification exist.

## Product-line and acquisition extensions (design, not registered tools)

[Product/Scene model](PRODUCT_LINE_AND_SCENE_MODEL.md) defines product version publishing, launch preview/approval, and shared facts with per-Scene judgments. [Acquisition/provider design](ACQUISITION_AND_PROVIDER_DESIGN.md) defines observable access failures, legal fallback, and actual Serper/Crawl4AI/Jev consumption. The [unified execution plan](MEMBER_A_AUTOMATION_EXECUTION_PLAN.md) owns implementation; these descriptions do not add tool names to the registered catalogs below.

Creating a ProductLine is metadata-only; paid work requires an approved bounded Scene/Launch. Read-only status does not call providers. Browser availability is not permission to bypass a target's access controls. Ray ID/403 alone does not establish an IP block. Privileged product/policy publication is separate from research-worker capability.

## Current shared development dependency

Use the existing Serper/Jev adapters through MarketOps business use cases/DecisionProvider. Do not add one business MCP per vendor. A future shared or dedicated Laya deployment is a separately verified option, not a current dependency. Do not start model replicas, alter ServerOS or containerize this release; see [Development infrastructure](DEVELOPMENT_INFRASTRUCTURE.md).

## 1. Service boundary

```text
Claude Code control client (Hosting/Hermes later)
       -> MarketOps MCP / persistent job admission
       -> actual work item dispatch
       -> scoped Claude Code research workers
       -> MarketOps research tools
          Serper | HTTP/PDF + Crawl4AI | Jev (Laya deferred)
       -> evidence/result ingestion
       -> persistent dossier, report and integration receipts
```

The controller cannot substitute an outer-shell worker launch for dispatch through the executor. The worker cannot recursively submit top-level jobs. One logical MCP service exposes two permission-filtered catalogs, not two products or two independent fact stores. Internal adapters use functions, SDKs or private APIs rather than one MCP server per module. A Skill teaches use or research methods; it is not the backend or the keeper of durable state.

The baseline research catalogs below contain nine controller-visible and eight researcher-visible names, with `get_evidence` and `get_decision` shared: 15 unique tools. The section headings and first-column tool names in §2–3 are consumed by `tests/test_security_catalog.py`; future capabilities must not be inserted into those tables as if already approved and registered.

Capabilities added after this baseline get their own catalog rather than a new row here. The product loop (Scene lifecycle, incremental maintenance, outreach) is specified in [MCP product loop](MCP_PRODUCT_LOOP.md); its tools are registered in `marketops/security/catalog.py` under phase `J` and, like §2–3, are checked against this code by the same test — which now reads every catalog document and compares the union.

## 2. Minimal control catalog

The following names are the current v1 catalog contract. Detailed behavior remains subject to implementation and live acceptance; discover actual deployment readiness through capabilities and explicit receipts, not this table alone.

| Tool | Purpose and result |
|---|---|
| `get_capabilities` | Report configured/runtime-ready/last-live-verification state for each integration, with versions, observed timestamps and sanitized blockers; no implicit paid probe |
| `get_scene` | Read an authorized Scene's product/scope, effective policy/guidance version and summary |
| `submit_research` | Admit a finite research/refresh request; return durable job id and truthful queued/blocked state |
| `get_job` | Read actual dispatch task/attempt links, progress, partial results, usage and capability/evaluation status |
| `get_account` | Read a bounded temporal dossier, routes, opportunities and evidence references |
| `get_evidence` | Read an authorized stored artifact/block, provenance and transformations |
| `get_decision` | Read back one persisted DecisionRecord by id: provider/model, spec version, answers, usage, latency and evidence references; never re-decides or re-bills |
| `get_report` | Return reproducible research results and separate integration/comparison receipts; unfinished sections stay pending |
| `control_job` | Typed pause/resume/cancel intent and observed execution result |

`submit_research` references a Scene plus one input kind: existing source receipt, known Accounts, or an approved discovery scope. It includes an idempotency key and optional stricter limits. The server resolves tenant/principal and hard budgets; the caller cannot silently expand them. Reusing a key with different inputs is a conflict. Research requests do not receive a caller-controlled `send_authorized` switch.

**Current input limitation:** `application/inputs.py` implements the known-Account producer. Its `_NO_PRODUCER` entries explicitly block `source_receipt` and `discovery_scope`: the former lacks a reusable acquisition receipt producer; the latter lacks the runner connecting existing discovery components. These are implementation gaps, not permission to fabricate empty successful jobs or require the user to invent official domains. See review E1/E2. The existing known-Account identity checks remain in force while raw-candidate enrichment is added through its own path.

A preconfigured Scene suffices for the first MVP. Do not require a new scene-builder UI or a manually fabricated list of domains. Job scope/cutoff makes completion meaningful even when its parent Scene is continuously active.

Capability health is not proof that useful work happened. `get_capabilities` reports the last verification's age; it must not return a permanent green flag or call billed providers every time a dashboard is read.

"Versions" means the deployment, not only the product: the report carries a `build` block for the code *this process loaded* — the source tree, its revision, whether that tree had uncommitted changes, and when the stamp was read (`marketops/adapters/build_stamp.py`; `--check` reports the same object). A constant version string cannot separate a stale process from a fresh one, and on 2026-09-23 it didn't: a service started the previous evening answered identically to a process started after three commits. `dirty: null` there means "not read", never "clean".

## 3. Task-scoped research catalog

| Tool | Contract input shape | Required behavior |
|---|---|---|
| `get_assignment` | work item id | Frozen Scene/guidance, input refs, accepted prerequisites, lease/attempt, tool rights, remaining budget, and the compiled `decision_specs` — the ids `decide` accepts. Handing them over is reading, not authoring: `decide` matches ids only and has no path that accepts a caller-supplied spec. A Scene declaring no decision tasks returns an empty list; one whose declared options do not match its own closed set refuses the whole assignment rather than returning an empty list, because an empty list and a broken profile would otherwise read identically (§F) |
| `search_web` | bounded queries + assignment | Serper results with original response refs and discovery-only snippets; actual request accounting |
| `fetch_document` | URL, purpose, `auto/http/browser` request | Policy validates route; persist acquired bytes/DOM and parsed blocks with final URL, timestamps and artifact types |
| `crawl_site` | approved seeds/scope, purpose and requested ceilings | Crawl4AI bounded multi-page work, per-page receipts and links; durable operation id when it cannot finish within tool-call limits |
| `decide` | work item id, server-owned DecisionSpec id, evidence/state references | Real configured DecisionProvider result (currently Jev) or explicitly unavailable/abstained result, plus a durable DecisionRecord id; the record is keyed by (work item, provider, case) so replaying one invocation never pays twice, while asking a second provider the same question is a second judgment |
| `get_decision` | record id | Read back one persisted DecisionRecord verbatim without re-deciding |
| `get_evidence` | evidence id + bounded locator | Reuse already acquired material without refetching |
| `submit_findings` | attempt/result id, observations, candidates, gaps, stop reason, responsibility relations | Idempotent validation and ingestion receipt; no direct arbitrary DB edits. A relation row is a claim of fact, not a stored row on request: each one must name the DecisionRecord that concluded it, the venue is re-derived server-side from the work item's own source receipt rather than taken from the caller, and every referenced evidence id must resolve inside this work item's Scene and account — a settled judgement whose audit trail does not hold up refuses that row, and writes nothing. Contact routes and route opportunities are likewise **bound server-side** from the evidence this settlement cited: the subject comes from the work item's frozen dossier (never from the caller), a cited row that is a field-level reference or a non-eligible source is named as such instead of being bound, and on a replayed settlement nothing is re-bound — `routes` reports `null` there, because `settle` is content-keyed while the route tables are inserts |

The five selectees of §B16 close condition 7 — Serper, the HTTP fetcher, Crawl4AI, Jev and the dispatch executor — each take one optional `source_method` cell and echo it into the receipt: which plan step this invocation served. Blank is a claim of its own ("this call serves no plan step"), never a default; a name that is not a registered source method, or one this Scene's declarations never opened this round, is refused **before** anything is spent, sent or handed out — and the refusal is at the door, so a rejected call leaves no job, no work item and no state write behind. The step is always compared against the **effective** Scene, which is what the tools hold; the frozen per-work-item version bounds what runs, not what a call may claim. The server hands the worker the values it may use (`get_assignment`'s `guidance.plan_steps`); the plan itself is composed server-side, and no tool accepts a caller-authored plan.

`crawl_site` is an acquisition operation subordinate to a dispatch assignment, not another top-level research planner. It uses existing acquisition execution or the task runtime, not a new generic scheduler. Its status is discoverable through the parent job/assignment. It cannot create unbounded Account tasks.

Browser selection can be requested, not forced past policy. An already adequate HTTP result remains a valid fast path. Explicit access restrictions are not rendering failures. A browser test may deliberately exercise a permitted JS page without establishing that every research page should be rendered.

Do not expose Crawl4AI administration, arbitrary JavaScript/Python execution or a raw unrestricted SDK config object. Use the allowlisted subset supported by the pinned version. If default browser behavior exceeds the assigned capability boundary, reject rather than silently widening it.

## 4. Jev/Laya modes and comparison

The current required provider is Jev and it must execute actual inference when used. The default `decide` resolves its provider from a versioned configuration. Laya and dual-provider comparison are deferred; future authorized evaluation may compare providers under a fixed policy. No worker-defined scoring rules, ad hoc thresholds or provider credentials in prompts.

A DecisionRecord includes: decision/spec version, structured input/evidence hash, provider/model/checkpoint and preprocessing, answer, reported probabilities if available, latency, known usage/cost and failure/abstention reason. Never invent unsupported probability values.

The `decide` receipt carries one field that is not part of the record itself: `source_method`, the plan step this invocation served — blank when it claims none, never defaulted. It is validated against the Scene's declared source openings before any inference happens, and it is persisted with the record, so a Jev call can be traced back to the plan step that selected it (§B16 close condition 7). A worker takes that value from `get_assignment`'s `guidance.plan_steps`; the server never accepts a caller-authored plan.

- **Shadow:** Jev and Laya run over identical frozen cases; hide their answers from the baseline researcher and report disagreement independently.
- **Assist:** one configured provider supplies an advisory answer to the researcher. Mark the trajectory assisted; it is not the shadow baseline.
- **Automated acceptance:** not enabled merely because inference works. Domain labels, held-out quality and suitable confidence evaluation must justify any future threshold.

Current R1/R2 acceptance requires a callable Jev decision tool and evidence that an applicable advisory result is consumed through the product path. Live dual-provider shadow receipts are not a current gate. It does not require proving a performance win or allowing a model to authorize business actions.

Check evidence completeness, language and input length before inference. No silent truncation. Unknown or conflicting responsibility can remain unresolved. For nonexclusive relations use multiple predicates or an appropriate schema instead of forcing one label. Missing credentials/checkpoint/capacity is a blocker, not a fake successful inference.

## 5. Acquisition provenance and budget

Persist original response artifacts when available, rendered DOM when used, parsed text/Markdown, parser version and transformation links as distinct artifact types. An extracted summary does not replace the evidence used for literal occurrence and person/role/Account binding. Preserve raw supporting blocks even when filtering reduces text sent to the model.

Every page records attempted/final URL, access mode, status, times, content type, hashes, links and failure reason. A browser's rendered DOM is not labeled raw HTTP HTML. Do not flatten association context merely to save tokens.

Reserve top-level document/search/inference budget atomically before attempts; track browser network subrequests separately and enforce their own bounded resource/egress policy. Descendant navigation, retries and extra model calls cannot escape shared ceilings. Comparison mode accounts for each provider separately. Unknown external usage remains uncertain until reconciled.

All URLs, redirects and browser subrequests are subject to network policy: reject unsafe schemes, internal/private targets and unapproved access. Do not connect a browser holding business credentials to untrusted pages. Test malicious page instructions as data, not authority to call control tools or change Skills.

## 6. Durable execution and permissions

Use the deployment's existing worker launch/profile/driver mechanisms with a documented research-completion adapter. Its software worktree/fan-in defaults do not prove correct research completion. Validate actual dependency blocking, effective prompts/config hashes, acceptance execution and real process/task ownership. A terminal task cannot certify itself. Do not fake commits or use test-only injection as production.

SQLite plus evidence files is the recommended initial single-host state. Store admission and dispatch intent transactionally, reconcile the dispatch task mapping, use attempt/lease protection, and commit findings through one controlled writer. Workers do not share or mutate master CSV/JSON counters.

Pause normally prevents new dispatch and lets accepted in-flight results settle. Cancel reports what actually stopped. Worker/provider failure preserves verified partial output. Same request/result retries are idempotent; stale attempts cannot overwrite later versions.

Run MarketOps under a service owner separate from the Claude control session and use authenticated Streamable HTTP for persistence tests. Stdio alone is not proof of an independently durable job. Pin compatible SDK and CLI versions; discover the real tool set. Controller, executor host and worker have different permissions; neither permissive prompt approval nor a caller-supplied tenant string establishes isolation.

### 6.1 Current authentication and workspace limitations

`adapters/mcp_server/server.py` runs a local TokenRing/`mint-token` deployment. **No discovery metadata is published**: `resource_server_url` is explicitly `None`, so the SDK registers no RFC 9728 protected-resource routes, and the 401 `WWW-Authenticate` header carries no `resource_metadata=` pointer. The earlier "advertises a nonexistent AS" gap is closed by not claiming one — both directions are gated in `tests/test_mcp_auth_interop.py` (route table, and the 401 response header).

The controlled-token mode is what "authenticated" means here: a token is minted out of band by `marketops mint-token`, shown once, and only its `sha256` digest lands on disk. Each grant carries a principal, roles, an audience, one or more workspaces and an expiry; **all five are enforced**, in two layers: audience and expiry at the transport layer in `adapters/mcp_server/auth.py`, roles and workspaces in the `RoleGuard` middleware in `adapters/mcp_server/guard.py`. **It does not impersonate full OAuth discovery**, and no tokens are issued by this service. Audience check is done in that layer rather than by the SDK because one SDK switch (`resource_server_url`) also controls publishing the discovery routes; it uses the SDK's own `AnyHttpUrl` normalization, and `tests/test_security_tokens.py` cross-checks that both layers reach the same verdict. An audience mismatch and a wrong token are the **same 401** — the protocol's return type has no place for a reason — so diagnosis is on the authenticated surface: `--check` reports `mcp.resource` (what this process accepts) alongside `mcp.audiences` (what is actually on disk), and `mint-token` reports on stderr what it just wrote. `MARKETOPS_MCP_RESOURCE` must be set explicitly behind a reverse proxy; after changing it, the ring's tokens must be re-minted, because the audience is stored on the ring. The same holds for the workspace, and here it is not only a matter of a changed value: a ring written before the workspace dimension existed carries format 2 and **fails to load**, with a message naming which fields are missing and the re-minting command. **Both older formats are refused, and they are refused by name**: format 1 lacks audiences and expiry, format 2 lacks workspaces (`marketops/security/tokens.py::_OLDER_FORMATS`). This is not hypothetical — the ring in the vault on this host was format 1 on 2026-09-23, so the current build exits 2 with "令牌环格式版本是 1，本构建只认 3" until it is re-minted. That is an operational step for any live deployment, not a code path, and it is deliberate — a ring that loaded without the field would grant a token valid in every Scene.

Supporting a client that performs a full OAuth discovery flow is **not** implemented and is not claimed; that requires a real approved authorization server/gateway and a tested discovery/authorization/audience round trip. Do not publish fictitious discovery metadata or pass provider tokens through to clients.

`security/catalog.py` defines `Principal(name, roles, workspaces)`. Role checks alone do not establish multi-tenant object isolation, so there is a second dimension. **What is now enforced:** a workspace is a Scene and its value is a profile id; `RoleGuard` requires the caller's grant to include the workspace this deployment serves before any `tools/call` proceeds (`security/workspace.py`). Order is role → workspace → backing, and each order is load-bearing: a role failure must not be answered as a workspace failure, and workspace must precede backing or a mis-minted token could enumerate which tools are wired. A workspace mismatch is the **one deliberate exception** to the "every refusal is `Unknown tool`" anti-enumeration rule — the caller already holds a valid token and the catalogue is published, so collapsing it would make a minting error look like a typo. That refusal reports the calling tool name and nothing about this deployment's Scene (same stance as audience mismatch), and diagnosis is on the authenticated surface: `--check` reports `mcp.workspace` (the Scene this process serves) alongside `mcp.workspaces` (what is actually on disk). Workspaces travel in `AccessToken.claims`, not scopes, because `roles_from_scopes` silently ignores unknown scopes and a typo would quietly mean "allowed nowhere". Workspace mismatch is **not** a 401: it is an application-level refusal, so re-minting a correctly named token is the remedy rather than treating the token as invalid.

**What is still not claimed.** Workspace authorization is enforced over job objects that carry Scene lineage: `research_job.scene_id` is checked by `get_job` and `control_job` (the latter is a write, and a cross-Scene pause/cancel would otherwise succeed while looking identical to a normal one). Account-scoped knowledge objects now carry it too, and are enforced at the store rather than at the tool: `Evidence`, `ContactRoute` and `RouteOpportunity` each require a `workspace` (no default), a store instance belongs to one workspace, a write of a foreign object is refused, and a row whose `workspace` disagrees with the store's is refused **by naming the row** — not skipped. Two things remain outside it and must not be read as covered: (1) `decision_record.work_item_id` is not validated against the job store, so a research-side caller holding a valid token can file a decision record against a foreign work-item id; (2) researcher access still does not respect assignment/lease scope. `tools/list` is **not** filtered by workspace (a deliberate choice — see `tests/test_mcp_workspace.py`) and a name outside the catalogue still answers `Unknown tool` regardless of workspace. Check access separately for Accounts, evidence, decisions, reports and cache reuse. Deployment is still a configured workspace with a defined trusted caller set, not a shared multi-tenant service. This documentation review was not a complete vulnerability audit.

### 6.2 Version and disconnection semantics

Record the exact installed Python MCP SDK, negotiated/supported protocol version, transport/auth mode, client version and loaded build. The declared `mcp>=2.2,<3` dependency range does not prove compatibility with every protocol revision. Use the lifecycle semantics of the tested combination and treat a protocol upgrade as a separate compatibility change; see [primary-source review](research/MCP_RELIABILITY_REVIEW_2026-09-23.md).

A business job id is not a JSON-RPC request id, protocol session, dispatch task id or attempt. Disconnecting the client, canceling an individual MCP call or reopening a transport must not silently delete or duplicate admitted work. A durable cancellation requires `control_job` and an observed execution receipt. Protocol task extensions, Resources or Prompts may be added only as separately specified conveniences over the same business state; none is claimed as implemented by this revision.

## 7. SOP acceptance remains independent

All A/B/C/D routes require a literal email. A requires relevant decision/demo responsibility, not just a name; D is a real generic official email, not a phone or form. ContactSignal/RouteOpportunity and unresolved hold remain separate.

Validate exact evidence/block attribution, Account binding and currentness. A date-old document acquired today is historical unless current corroboration supports otherwise. Semantic accuracy requires independent review; correct schema/hash does not imply true responsibility. Prior pilot labels are not gold data.

Research tokens never send mail, authorize campaigns, clear suppression or write production Feishu results without applicable authorization. No evidence or self-written Skill can expand those rights.

## 8. Mandatory MVP acceptance bundle

**Build receipts against this bundle** are kept per build, not merged into one status page: [MVP_ACCEPTANCE_REPORT_2026-09-23_IMPLEMENTATION.md](MVP_ACCEPTANCE_REPORT_2026-09-23_IMPLEMENTATION.md) is the `168d69b` receipt (A–E, each item PASS / FAIL / BLOCKED / NOT_RUN with its evidence). `MVP_ACCEPTANCE_REPORT.md` stays frozen; a newer receipt never rewrites an older FAIL.

### A. No-network protocol and safety tests

Use labeled synthetic fixtures/fake adapters to test schemas, isolation, unsafe URL denial, budget races, idempotency, missing prerequisites, stale attempts, pause and recovery. These tests do not satisfy real integration coverage.

### B. Real end-to-end research

Use a small source-backed cohort, initially up to five candidates within a frozen approved scope and spend envelope. Claude Code submits through MCP; actual dispatched workers research and submit results. Disconnect/reconnect the controller and query the same job. Demonstrate at least two independent workers overlapping and no duplicate expensive work.

No fixed positive A/B count is required: zero is an honest business outcome. Source discovery and the shortlist are the system's work, not a demand that the user invent Account records.

### C. Current research integration coverage

One release bundle verifies the currently required research paths below. Laya is explicitly outside this gate:

1. Serper live query/response linked to researched candidate URLs.
2. Crawl4AI browser rendering of an approved normal public JS page or a clearly labeled supplemental controlled fixture, with DOM/text evidence and no policy bypass.
3. Crawl4AI bounded multi-page traversal that respects scope/limits and records every page; no requirement for every advanced feature in the upstream project.
4. Jev live inference on the frozen DecisionSpec/evidence cases.
5. DEFERRED: Laya checkpoint inference and cross-provider comparison. Its absence is not a current failure or blocker.
6. Executor-owned dispatch and the worker's actual scoped task/tool execution.

Real source-backed browser evidence is preferred. A controlled fixture proves integration behavior only and cannot count as market yield or real-site improvement. Both kinds must be labeled.

### D. Quality and continuity

Test historical/changed/unchanged staff pages, another organisation's email on the same page, a name collision, a relevant PDF, an explicit block, one unavailable provider and a crashed worker. Check exact counters, partial results, nonduplicated ingestion and a second incremental maintenance run.

Serial-versus-parallel performance is a separate controlled comparison. Different models/runtime versions/cache conditions cannot be pooled as a causal speedup. Shadow results are not production savings.

Add Member-C-driven cases as the mother-pool paths are implemented: source IDs without names, entity/asset disambiguation, one Account with multiple assets, multiple responsibility relations, only generic emails, unsupported currentness and deterministic multilingual output. These extend business coverage; they do not replace the existing integration gates.

### E. Truthful outcome and gate

Record each integration as `PASS`, `FAIL` or `NOT_RUN`, with evidence refs. Missing provider access may block that check while other work proceeds; the **full MVP gate remains incomplete**. Imports, healthchecks, fake predictions, installed containers and marketing benchmark numbers cannot turn NOT_RUN into PASS.

Separate three questions in the report: did research execute; was the evidence/business output valid; did every required integration pass? There must be no single green flag that conceals a missing model/browser path. The existing `MVP_ACCEPTANCE_REPORT.md` is a frozen record with failed checks, not an up-to-date deployment certificate; a fix requires a new build-specific receipt, not editing old FAIL labels.

## 9. Report and deliverables

Deliver documented launch/configuration, a tested MCP entrypoint, real adapters, compact client/worker Skills, local persistence/migrations and a pinned deployment manifest. The report contains input/Scene versions, task/attempt links, integration coverage, SOP grades, opportunities, exact evidence, stop reasons, actual usage, latency breakdown, concurrency, failures/retries and missing prerequisites.

Cost per new A/B is unavailable when yield is zero; unknown provider prices remain unknown. Human review/label effort is included in evaluation cost when used. Keep test-only and genuine market records separate. Keep Account qualification, route grade, evidence/currentness and research priority separate; a popular job title or a high model confidence cannot substitute for a verified route.

Hosting/Hermes connects to the same endpoint afterwards by approved profile/Skill/MCP configuration. This client integration is distinct from writing a Feishu Bitable projection: `adapters/feishu/client.py` remains `interface_only` at the review baseline. A later authorized sink must validate structured output and read back row ids, versions and counts before reporting delivery success. Local research output must remain available if that sink is unconfigured or fails.

(2026-09-24 update: `ADAPTER_STATE` is now `partially_wired`. The handoff write path exists and does read back row id, version and count — a mismatch is a failure, not a success — but it runs only in isolated tests with a transport double; the real Feishu write is `BLOCKED` (`feishu_write_authorized = False`). No production caller wires it into the pipeline yet, so local research output remains the only delivered output.)

No Hosting source changes, new RuntimeBackend, broad national rollout, real external mail, heavy OCR or autonomous skill promotion is needed to accept this research MVP.

## References

The project's frozen SOP defines business policy. Technical references: [MCP architecture](https://modelcontextprotocol.io/docs/learn/architecture), [Claude Code MCP](https://code.claude.com/docs/en/mcp), [Crawl4AI self-hosting](https://docs.crawl4ai.com/core/self-hosting/), [Laya](https://github.com/NandhaKishorM/laya). Match examples to pinned versions before implementation; these are not deployment claims. Version-specific protocol and research evidence for this revision are recorded in [MCP reliability review](research/MCP_RELIABILITY_REVIEW_2026-09-23.md).

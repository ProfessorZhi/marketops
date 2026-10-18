# MarketOps MVP Deployment — All Integrations, Small Footprint

Updated 2026-09-22. Proposed packaging; no services or Compose files are created by this document. See `ARCHITECTURE.md` and `MCP_SERVICE.md` for the product and acceptance contracts.

## 1. Standard MVP bundle

The current user decision is host/service-first development on Tokyo. ServerOS and
MarketOps share ONE governed Laya inference instance through configured endpoints;
workers do not load their own models. MarketOps remains an independent application
and authenticated single business MCP. See [Development infrastructure](DEVELOPMENT_INFRASTRUCTURE.md).

Docker/Compose packaging and production Hosting integration are deferred. The
container references below are future deployment constraints, not current tasks.

The standard MVP includes Serper, Crawl4AI, Jev, Laya, Quay and Claude Code integrations. Runtime routing may skip unnecessary providers, but missing implementation/live acceptance is not a complete MVP.

| Unit | What runs here | Persistence / exposure |
|---|---|---|
| `marketops` application | MCP, Scene policy, admission, SQLite, result ingestion, search/decision/acquisition adapters | Durable local ledger/evidence; the only client-facing business endpoint |
| Quay/Claude research runtime | Verified installed Quay manager/driver lifecycle and isolated Claude task execution | Task state and scoped attempt artifacts; operator UI private |
| Crawl4AI browser service | Pinned browser/runtime, pooling and controlled multi-page acquisition | Isolated from the business vault/DB; private authenticated API |
| Laya inference service | One resident checkpoint under the agreed CPU/RAM limits, shared with ServerOS through an authenticated endpoint | Reusable bounded model cache; no public endpoint |
| External APIs | Serper and Jev | Credentials owned by the application adapter, not sent to models |

Run the MarketOps application independently in its prepared host environment. Its MCP surface, ledger ingestion, due-item selection and provider adapters may share one application process. Logical modules are not mandatory microservices; image packaging is a later phase.

Laya is not launched by this deployment. It is the one governed host service already shared with ServerOS, reached through its authenticated endpoint; this bundle starts no second checkpoint and no private sidecar copy. The MarketOps side holds only a light client behind the versioned `DecisionProvider` boundary, and that client must not block the MCP event loop. If the shared service is down, its inference is an explicit unavailable outcome — never a locally loaded replacement.

Quay may reuse the existing governed runtime during the first test instead of immediately containerizing its installed plugin/session lifecycle. Packaging must preserve its actual supported startup, profiles and acceptance path; declaring a container does not prove research dispatch.

A small Compose deployment or the existing operations tooling is sufficient. Do not add a database cluster, message broker, external memory service, Kubernetes or a second scheduler. Hosting remains outside this bundle and unchanged until configuration-only integration is accepted.

## 2. Startup, readiness and versioning

Prepare the development runtime, dependencies, browser binaries, allowed tools and configuration once, not per page or Account; **do not build an image in this phase** (see the deferral in §1). Pin app commit, MCP SDK/transport, Quay/provider, Claude CLI/launcher/model, Crawl4AI/browser and Laya/checkpoint/preprocessing. Do not infer installed behavior from a remembered version.

Use fresh bounded research contexts; process availability does not require persistent cross-task LLM conversations. Reuse connections/caches within their permitted scope; do not share tenant secrets or personal memories. Use Quay's actual driver ownership, worker records and outcomes to demonstrate execution.

The research worker's launcher (the single-token `launcher` of the `task-worker` role in `.quay/profiles.yml`) owns five declarations, and the worker entry refuses the dispatch when any is missing — none has a default, because a default would make a deployment error look like a successful run: the delegate program (`MARKETOPS_RESEARCH_DELEGATE`), the module path (`PYTHONPATH`, which must be **the tree the service itself loads** — the build check below is what makes that requirement fail closed instead of silently wrong), the run-state root (`MARKETOPS_DATA_ROOT` — where the service reports its own build and where the identity lock is taken), the **isolated run root** (`MARKETOPS_WORKER_ROOT`, an absolute path — the worker creates one fresh directory per dispatch under it and hands that path to the delegate as its cwd, so the child's relative paths cannot reach `.quay/`, a sibling task, or the deployment's own config), and the **token ring** (`MARKETOPS_MCP_TOKENS`).

The five values are the only lines a deployment edits; the rest of the file is this contract. A conforming launcher is:

```sh
#!/bin/sh
set -eu
DELEGATE=<the agent runtime this workspace dispatches to>
SERVICE_TREE=<the checkout the MCP service itself runs from>
DATA_ROOT=<this deployment's run-state root>
TOKEN_RING=<the format-3 token ring this deployment's service reads>
VENV_PY=<the interpreter that has marketops importable>

export MARKETOPS_RESEARCH_DELEGATE="$DELEGATE"
export PYTHONPATH="$SERVICE_TREE"
export MARKETOPS_DATA_ROOT="$DATA_ROOT"
export MARKETOPS_WORKER_ROOT="$DATA_ROOT/workers"
export MARKETOPS_MCP_TOKENS="$TOKEN_RING"
export CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0

exec "$VENV_PY" -m marketops.interfaces.research_worker "$@"
```

`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0` is a delegate knob rather than a MarketOps fact (without it a finished round waits up to 600s on background tasks; the driver-side timeout is the real ceiling), so it is not one of the five.

**The identity is no longer one of the launcher's declarations.** It used to be: this file walked the vault's `marketops-mcp-token-worker-<k>.txt` family itself, took a kernel-held `flock` on `<workspace>/.quay/identities/worker-<k>.lock`, and `cat`-ed the chosen token into the environment. That is now done inside the worker entry (`marketops/security/identity.py`), for three reasons that are properties of the round rather than of the shell that starts it. The ring is the only place where a token's validity is written, so a vault-file walk cannot answer "is this token still accepted" — the 2026-09-23 format-3 ring replaced the two tokens minted on 09-22, the files did not change, and every dispatch that read them handed the delegate a dead token: the delegate then had **no `marketops` tool surface at all** (the service answers 401 to every call) while its own report said the server was not mounted. Second, the lock must live exactly as long as the round does, and the launcher `exec`s away; a lock taken before `exec` has to be carried across by an inherited fd, which is a harder thing to keep true than "the process that runs the round holds it". Third, the lock belongs under the run-state root rather than the workspace, because what it protects is that root's run-level database (`state/marketops.db`), which is exactly why two workers sharing a principal is dangerous.

So the entry reads the ring, resolves each candidate file's token to a principal, requires the role to be **exactly** `researcher`, refuses a grant that is already expired (the service's `check_token` does, so anything else just relocates the 401), and holds a `flock` on `<data root>/state/identities/<principal>.lock` until the round ends. When every identity is already held it exits 2 with the naming refusal and no delegate runs. The four refusals — "not in the ring", "role is not exactly researcher", "expired", "all identities held" — are four different sentences with four different remedies (rotate / fix the role / rotate / add an identity or lower the cap), and they are deliberately not collapsed into one.

Why identity is a deployment fact and not a product fact: the product's only identity input is the transport token, and its own test `tests/test_mcp_assignment_tools.py::test_another_worker_cannot_read_the_assignment` pins the property that one principal cannot read another's assignment. A deployment that hands every worker the same token **satisfies that test and falsifies the property** — the second worker's `get_assignment(<the first worker's work item>)` hits `_claim`'s same-principal branch and receives that assignment, and nothing in the receipt says so, because the `holder` the caller sees is its own name. So the number of identities is the host's real concurrency ceiling: `kinds.worker.cap` in `plugin/scripts/drivers.yml` must not exceed the number of researcher principals the ring resolves a candidate file for.

The launcher's own copy of this contract is the mirror; change both together.

**Build consistency is a pre-dispatch gate (§15(b)).** Before the delegate is started, the worker compares the build it loaded against the build the service reported at its own startup in `<data root>/state/server-build.json` (the service publishes it before it begins serving, so "the file is there" means "a service reported once", never "a service is serving right now"). Both the empty-read and the mismatch case exit 2 with the delegate never started, and the two say different things: the first is a deployment that is not wired up (no service has run against this data root, or it ran against a different one), the second is two genuinely different code revisions, named side by side with the remedy. This exists because a worker whose `PYTHONPATH` pointed at a different checkout than the service — 47 commits apart — produced rounds where every finding came from one code revision while `run.json` claimed the other, and no log line said so. A `dirty` worktree is reported and not judged: a development round on an uncommitted tree is legitimate, a round on a *different* tree is not.

The worker's built-in file tools are bounded by two mechanisms, and the second one exists because the first one cannot cover absolute paths. The first is the isolated run root above: `Read`, `Grep` and `Glob` resolve a *relative* path against the delegate's cwd, so a worker that stands in its own fresh directory cannot reach a sibling task by accident. That fact says nothing about an absolute path, which a file tool resolves from the filesystem root and the permission layer — not the cwd — decides. So the role's `--settings` file (`.claude/launch.settings.json`, written by the deployment) also carries three `Read` deny rules, and they are the deployment's own copy of this list; change both together:

```json
"Read(//home/operator/.secrets/**)"
"Read(//home/operator/artifacts/marketops-mvp-quay/.quay/**)"
"Read(//home/operator/artifacts/marketops-mvp-quay/tasks/**)"
```

`Read` deny rules are also the documented way to bound `Grep` and `Glob` (a rule written against `Glob` itself is accepted and never consulted), and a `Read` deny also blocks `Edit`/`Write` on the same path, so one rule per path covers all four tools. Deny rules are enforced in every permission mode, which is the property that matters: before they were added the vault and `.quay/` were refused only because print mode cannot answer a prompt, and a change of `defaultMode` would have silently opened them. The path prefix is `//` — a single `/` anchors at the settings file's project root, not the filesystem root.

The application exposes scoped capability status: configured, runtime-ready, last real verification time and sanitized blocker. Healthchecks may test local readiness but must not silently spend Serper/Jev credits. Live probes belong to an explicitly budgeted acceptance task.

A missing Jev credential or Laya model keeps its acceptance NOT_RUN/FAIL. It may permit an explicitly degraded research job under policy; it cannot make the full integration release green. A request requiring browser acquisition fails clearly if that service is unavailable; no empty evidence is fabricated.

Startup order is dependency-driven: ledger/service first; required model/browser readiness as needed; Quay execution owner ready before reporting a job running. Queued jobs survive provider startup delays and reconnects.

**Schema migration is five explicit steps: `preflight → backup → migrate → verify → start`, and the migration never runs by itself.** Preflight is the service's own startup check (`read_schema_state`); backup, migrate and verify are one commit of `python -m marketops.adapters.sqlite.migrate --db <the db>`; start is the service's own path. Nothing is auto-migrated, because the direction, the values that have to be proven, and the fate of the old database are a person's decisions.

The startup check must name the situation and the command rather than presenting as a broken tool. A data root copied from an older deployment reads, verbatim, on stderr and with exit code 2:

```
[marketops-mcp] 起不来：状态库 <path>：schema v2，代码期望 3 —— 要迁移
状态库 schema 版本是 2，代码期望 3：<path>
<why the two vocabularies are not interchangeable>
先跑一次迁移（它先备份，再只改**证得出来 / 认得出来**的那些行，认不出来的会点名拒绝）：
    python -m marketops.adapters.sqlite.migrate --db <path>
迁移**不自动跑**是刻意的：补什么值需要证据，旧库的去留是人的决定。
```

The same sentence is what `--check` prints, and it is why a stale database does not look like a service that failed to start. `migrate` backs up first, proves each stamped row belongs to this deployment's Scene (refusing the whole run, naming the rows, when it cannot), and after writing, **reads the result back on a fresh connection**: version, workspace binding, every planned stamp, no surviving old vocabulary, and each maintenance receipt key still matching its own columns. A read-back that fails says the database *has already changed*, names the backup, gives the rollback (`-wal`/`-shm` are part of it), and states that the archive side was not touched.

**"Can this process start" and "can this deployment run a trial" are two questions, and only the first one is `--check`.** `python -m marketops.interfaces.cli trial preflight` answers the second: seven rows — configuration, endpoint, auth, database, provider, budget, code — and one verdict over all of them, `READY` / `BLOCKED_CONFIG` / `BLOCKED_EXTERNAL` / `BLOCKED_CODE`, exiting 0 only for `READY`. It issues no provider call and no network request, creates no directory, writes no file, and prints no credential. The two commands disagree deliberately, and that disagreement is why the second one exists: a ring whose audience is written against a different resource starts the service (so `--check` is green) while every token is rejected at the transport layer (so the preflight's `auth` row is red, and it names audience mismatch and a wrong token as the two things that look identical at the transport layer). Two things about that row are worth saying out loud, because each of them costs a reader time. It is a **row**, not the verdict: the verdict is the heaviest of the seven, so while the `code` row is unfinished an audience mismatch appears in `auth` and nowhere else. And it judges the **ring**, not each token — a ring holding one matching and one mismatched audience is `READY` there while the mismatched principal's every call is still refused at the transport layer. A green `auth` row therefore means at least one principal the ring resolves can act here; it is not evidence about the rest of the ring. Which verdict means what, and who moves next for each, is in `docs/MEMBER_A_REAL_TASK_TRIAL.md` §2.

### Production rollout: `BLOCKED`

The research worker's production launcher (`artifacts/marketops-mvp-quay/bin/marketops-research-worker`) has been moved onto the format-3 ring; it no longer reads the superseded per-worker token files. The **production rollout itself is blocked on two independent deployment facts**, both of which now fail closed with a legible message instead of a silent 401:

| # | What is true today | Reading | Unblocking command |
|---|---|---|---|
| 1 | The vault's `marketops-mcp-token-worker-{1,2}.txt` are the tokens minted 09-22; the ring the service reads (`marketops-mcp-tokens-v3.json`) was minted 09-23 and resolves neither of them | `环里解不出一个可用的研究员身份 … 2 份候选不在环里：marketops-mcp-token-worker-1.txt、marketops-mcp-token-worker-2.txt` | re-mint the two consumer tokens into the ring and the vault files (`python -m marketops.interfaces.cli mint-token --rotate …`), then move the old files aside — **rotating existing production consumer tokens requires human authorization** |
| 2 | The service running on 8787 was started before this build and has published no `state/server-build.json`, so the worker cannot check the two builds | `核不了构建（MARKETOPS_DATA_ROOT=…）：…/state/server-build.json 不在` | restart the service onto the current tree so it publishes its build receipt |

Neither has been done here. `docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md` §G1 is the same first blocker seen from the outside (a report saying the marketops server was not mounted); §G3 is the second. The test deployment that *does* run is described in that file's §I, and the launcher contract above was exercised end to end on an isolated deployment with its own data root and its own ring.

### Feishu: `BLOCKED_EXTERNAL`, and it cannot gate the trial

Feishu is the one integration whose absence the preflight **reports and deliberately excludes from its verdict**. Two different things are missing behind that one word, and collapsing them would send the wrong reader after the wrong next move.

**Where the line is.** The Bitable projection — a handoff written into a table — has code (`adapters/feishu/client.py`) and declares its state inside that code: `LIVE_WRITE_STATE = "BLOCKED"` with three named blockers, because a one-line "BLOCKED" in a document disappears at the third rewrite while a disappearing *value* gets noticed. Its tests drive the whole path over an in-process transport double, and the ones that pass carry `sandbox_pass` in their names (`tests/test_feishu_push.py`) — an isolated pass, never a vendor pass. The **message sink / bot channel** is not blocked: it does not exist. Nothing under `marketops/` mentions the message surface at all — zero occurrences of `im/v1`, `chat_id` or `/messages`. So one half is a credential problem and the other is an authoring problem, and a single `BLOCKED_EXTERNAL` over both would have someone asking for an app secret to unblock missing code.

**What was measured.**

| Checked | Reading |
|---|---|
| a `FEISHU_*` variable in this deployment's environment | none |
| `feishu_enabled` / `feishu_write_authorized` under this deployment's settings | `False` / `False` |
| how the second one could be changed | it cannot be, from outside: a module constant with no setter, not read from the environment (`config.py:58-66`, `:323-331`) — deliberately, because an `FEISHU_WRITE_AUTHORIZED=1` that worked would eventually be copy-pasted into a deployment, and its symptom would be research output quietly appearing in a table other people can see |
| the message surface under `marketops/` | zero mentions |
| the other Feishu credentials **on this host** | present under `.secrets/vault/feishu/` (`vault-feishu.env`, `lark-cli/`, `muc-e2e/`), and **no** reference to any of them exists in this repository — `_raw` is `os.environ` and nothing else (`config.py:73`) |
| the preflight's own handling | the `provider` row carries `feishu` as a *fact* and keeps it out of `blocking` (`{"serper": false, "quay": false, "jev": false, "laya": false, "feishu": false, "blocking": ["serper", "quay"]}` on a deployment with neither credential), with the reason stated in the code: making it drive the verdict would make `READY` unreachable |

The fifth row is the one not to round off. "This host has no Feishu credentials" is false; "this deployment has none, and the ones the host does hold belong to other consumers whose grants are not this deployment's" is the true sentence. The two failures that keeps apart are opposite: someone concluding the machine cannot reach Feishu at all and stopping there, and someone wiring this adapter to another consumer's app secret to make a test go green.

**What lifts it.** For the projection: the business write authorization, app credentials in this deployment's own environment (`FEISHU_APP_ID`, `FEISHU_APP_SECRET` and the two Bitable locators), **and one exercise against the vendor** — that third is not a credential, and until it happens a real write's pass or fail is unknown, which is exactly what `BLOCKED` says. For the message sink: writing it, then the app's group scope, its rate limits, and a vendor live acceptance. Neither half blocks the manual trial, and neither is something a code change in this repository can clear on its own.

**What must not be done instead.**

* Writing any of this as verified because Feishu is expected to speak the documented API. Expected compatibility is not a measurement — the sentence the client projections' `blocked_external` states carry, applied here unchanged.
* Moving `feishu` into `_row_provider`'s `blocking` list, or deleting it from `flags`. The first makes `READY` unreachable; the second turns a legible gap into an invisible one. Both errors are pinned by tests (`tests/test_trial_preflight.py`, the two `feishu` cases at the end).
* Leaving a notification path that works only locally presented as the Feishu path. A client that cannot receive notifications is never described as not needing them (`marketops-plugin/client-projections/README.md`).

## 3. Network and secrets

Clients use authenticated Streamable HTTP. In a host-local test, bind the business/operator endpoints to an approved loopback address and use an authorized tunnel. Container-internal listeners and host port publication are distinct: do not publish internal service ports across the host by default. Follow the actual host directory/network policy before creating roots, mounts or networks.

Browser egress policy must cover redirects, subresources and additional navigations, not only the initial URL. Prevent access to private/internal services and metadata endpoints. Keep browser containers away from control credentials, business database and any Docker socket. Use separate narrow work tokens for research; the controller and Quay host have different scopes.

Secrets are injected through the approved deployment mechanism. Do not print them, place them in Git, model-visible prompts, arbitrary command lines or benchmark artifacts. Do not mount full host HOME, unrelated repositories or the entire credential vault into any worker.

Crawl4AI self-hosting documentation includes migration-dependent behavior and examples for different releases. Validate the chosen image's auth, transport and hooks against its own version; do not copy obsolete inline-code/unauthenticated examples into this deployment.

## 4. Concurrency and shared state

One owner per Quay driver kind manages multiple workers. Do not start a new competing Quay runtime for every chat request. Use supported role/profile/cap settings and measure actual dispatch rather than manually writing telemetry.

Workers, page operations, browser network traffic, Serper attempts and Jev/Laya inference have explicit aggregate budgets. Reserve request budget transactionally before dispatch and reconcile actual/unknown usage afterwards. Nested activity and retries count too.

All business writes go through the application. Use SQLite on durable local storage with controlled short writes, not a shared network DB file or unlocked master CSV/JSON. Store source/raw/DOM/parsed artifacts separately with hashes and transformation metadata. Back up the ledger and referenced evidence together; test restore and schema migration. Retention is explicit and tenant-scoped.

Cache/warm-start policies may reduce cost, but must preserve currentness and version information. A cache hit is recorded; it is not a fresh upstream verification or a new provider invocation.

## 5. Quay research completion

Reuse existing profile/driver hooks, with a documented narrow research adapter if necessary. The previously reviewed v0.11.0 default lifecycle includes software worktrees and landing. Its research POC had custom completion logic. Validate dependencies, effective prompts, actual result acceptance and restart behavior before adopting it.

No fake source commits, forged done states, test-only command injection or silent gate removal. If this integration cannot pass, report the missing contract; do not substitute outer-client shell execution while claiming Quay ownership.

## 6. Initial and later clients

Initial client: a scoped Claude Code session connected to MarketOps MCP. Closing it must not kill the separately owned MarketOps/Quay services. Stdio can test individual tools but is not the durable deployment test.

Later client: an authorized MUC/Hermes profile with a generic Steward Skill and the same remote MCP. Verify the deployed SDK/transport, identity mapping and network without changing Hosting source. Reuse Hermes schedule triggers for Scene checks; MarketOps computes due records; Quay runs tasks. Do not introduce competing full-refresh schedules.

## 7. Release evidence

The MVP manifest must reference real Serper query receipts, Crawl4AI rendering/bounded traversal receipts, Jev and Laya inference receipts on comparison cases, actual Quay dispatch and Claude execution, plus persistent results and reconnect/recovery tests. Unit-test fakes and controlled fixtures are labeled and do not count as live market yield.

For Laya, call the same authenticated shared host service used by ServerOS; use one resident checkpoint under the agreed CPU/RAM limits. Do not load a model per Account/worker or start a second service on failure. For Jev, use its remote adapter only under data/budget policy and include transport/serialization in measurement. Serper reuses the existing adapter and protected key injection; current credential presence is not a live-account test.

Research completion, provider readiness and full-MVP acceptance are separate outcomes. Missing provider evidence stays visible even when useful partial research can be returned. Include exact usage/counters, latency, failures and resource observations; do not claim speedup from concurrency alone.

No production Hosting edits, real customer sending, new nationwide run or model download/deployment has been performed by this documentation change.

References: the project SOP; [Crawl4AI self-hosting](https://docs.crawl4ai.com/core/self-hosting/), [Quay v0.11.0](https://github.com/yaleh/quay/tree/v0.11.0), [Laya](https://github.com/NandhaKishorM/laya), [Claude Code MCP](https://code.claude.com/docs/en/mcp). Implementation must verify the pinned artifacts rather than assume every upstream feature is available.

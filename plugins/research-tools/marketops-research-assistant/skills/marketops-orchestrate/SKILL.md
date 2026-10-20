---
name: marketops-orchestrate
description: Run large MarketOps research across several windows sharing one data root - build and reconcile the shared target pool, test a method on 30 targets, let worker windows claim batches, watch progress and stop or switch methods. Load for any task over ~50 targets or when the user opens several windows.
---

# Orchestrate a pool: many windows, one database

Every window (Claude Code, Codex, WorkBuddy) runs the plugin against the same data directory. They
hand work over through the pool's state, never through files or chat.

## Roles

| Window | Does | Tools |
|---|---|---|
| Orchestrator (one) | build the pool from whole-list sources, test methods, watch progress | search tools, `marketops_pool_import`, `marketops_pool_status`, `marketops_lessons_report` |
| Worker (several) | claim a batch, harvest it, judge routes, claim the next | `marketops_harvest_batch` (or `marketops_pool_claim` + `marketops_site_harvest` + `marketops_pool_complete`) |
| Checker (one, optional) | sample and verify, export | `marketops_pool_status`, `research-verifier`, `marketops_handoff` per Run |

## Orchestrator steps

1. Pool id per project (e.g. `uk-sports-2026`). Import every source with its `listed_count`; the
   reply's `reconciled=false` means rows were lost - find them before going on.
2. Prefer whole-list sources (association search pages, `/wp-json`, open data, county directories),
   then map listings per town, then single sites.
3. **Test before scaling** (Member A, GP-06): have one worker harvest 30 targets, read
   `marketops_pool_status.hit_rate`: ≥10% scale up; 5–10% improve the method once; <5% stop that
   method and switch source. Tell the user only when a method is stopped.
4. Tell the user how many worker windows to open (start with 3–4; providers rate-limit more).

## Worker loop

Each worker uses its own `worker_id` and its own `run_id` (e.g. `uk-sports-w1`) with the project
brief. Repeat without asking: `marketops_harvest_batch(pool_id, worker_id, run_id, count=20,
roles_wanted=<the brief's roles>)` → read the summary → grade each route from its Jev tag →
next batch. Stop when it returns `POOL_EMPTY_OR_ALL_LEASED` or the budget nears the cap. A crashed
window's targets return to the pool when its lease expires.

## Finish

`marketops_pool_status` must show no pending/claimed targets and every source reconciled. Each worker
hands off its Run once; `marketops_lessons_report(pool_id)` shows walls, rescue rates and cost per
route for the user.

# Member-A's real-task trial (G5) — template and steps

Status of G5 in this repository: **`NOT_RUN`.**

This file is not the trial. It is the **template and the steps** for the one trial that
only you can start, plus the single command that answers "can this deployment even run
it". Nothing here chooses a country, a customer, a budget or a task for you — §29 of the
execution contract forbids the implementation from doing that on your behalf, and that
refusal is deliberate: a trial whose subject was picked by the tooling measures the
tooling's taste, not your market.

Nothing in this file has been executed against real customer data, and no real paid
provider call has been made to produce it.

## 1. What the trial is

One **manual run** over a market you name, driven by you from a client, with the budget
you authorise — and your own judgement of the result. It is the first time the whole
chain (market → pool → contact routes → draft material) is exercised on a real question
rather than on a fixture.

It is deliberately **not**:

* a send. Outreach stays out of scope for this trial; drafts are prepared and read, never
  delivered.
* a nationwide run. The scope is whatever you bound it to.
* an autonomous loop. A run happens because you submitted it; nothing schedules further
  runs on its own unless you enable a watch or a schedule, and nothing in this trial
  enables one for you.

## 2. What must already be true of the deployment

Four things live outside the repository and none of them has a default — a missing one
must be a legible refusal, never a plausible guess. The authority for all four is
[`DEPLOYMENT.md`](DEPLOYMENT.md) §2; do not re-derive them from this file.

| # | What | Where it comes from |
|---|---|---|
| 1 | A **run-state root** — the deployment's own directory, not a path copied off another machine | `MARKETOPS_DATA_ROOT` |
| 2 | A **format-3 token ring** giving this deployment a principal that may act in this Scene | `MARKETOPS_MCP_TOKENS`, minted with `marketops mint-token` |
| 3 | **Provider credentials** for whatever the run will actually reach | the deployment's own injection mechanism; never the repository |
| 4 | A **declared budget ceiling** — the frozen profile's own numbers, not a per-invocation flag | the profile the deployment loads |

### The one command that answers the question

```sh
python -m marketops.interfaces.cli trial preflight
```

It issues **no provider call, no network request, creates no directory and writes no
file**, and it prints no credential. It answers seven rows — configuration, endpoint,
auth, database, provider, budget, code — and one verdict over all of them. Add `--json`
for the machine-readable form (same seven rows, same keys on every row).

Exit code is `0` if and only if the verdict is `READY`; any other verdict exits `1`. The
human-readable output is in Chinese; `--json` is language-neutral.

| Verdict | What it means | What moves next |
|---|---|---|
| `READY` | Nothing in this deployment blocks a trial. | You. Go to §4. |
| `BLOCKED_CONFIG` | Something in the deployment's own files is missing or contradictory — the ring, the profile, a declared ceiling. | Whoever holds this deployment. It is locally fixable. |
| `BLOCKED_EXTERNAL` | The code and the configuration are complete; a credential or a permission that belongs to somebody else is not in place. | Ask for that access. Do not expect a code change to clear it. |
| `BLOCKED_CODE` | Tools this build declares are not wired up yet. | Nobody outside the build. It is the implementation's own work. |

The verdict is the **heaviest** row, not the first: when code is unfinished and a
credential is also missing, it says `BLOCKED_CODE`, because no amount of access fixes an
unwired tool. Nothing reports `BLOCKED_EXTERNAL` while the code side is incomplete.

`python -m marketops.interfaces.mcp --check` answers a **different** question — "can this
process start" — and the two disagree on purpose. A ring whose audience is written
wrongly starts the service (so `--check` is green) while every token is rejected at the
transport layer (so the preflight's `auth` row is red). When they disagree, the preflight
is the one about the trial.

Two things about that row cost a reader time if they are not said. It is a **row**, not
the verdict: the verdict is the heaviest of the seven, so while the `code` row is
`BLOCKED_CODE` — which it is until the last of the declared tools is wired up — an
audience mismatch appears in `auth` and nowhere else. And the row judges the **ring**,
not each token: a ring holding one matching and one mismatched audience is `READY` there
while the mismatched principal's calls are still refused at the transport layer. A green
`auth` row means at least one principal the ring resolves can act here; it is not
evidence about the rest of the ring.

## 3. Four blanks only you can fill

Write them down before the run; they are what makes the result comparable later.

1. **Geographic scope.** Which market, bounded how.
2. **Which organisations count.** The customer class you would and would not call on.
3. **The budget.** A number you authorise for this run. The preflight reports the declared
   ceiling; it does not decide how much of it to spend.
4. **The task.** One sentence, in your own words, of what a good answer looks like.

## 4. The trial

The client-side workflow is the canonical Skill packaged in
[`marketops-plugin/`](../marketops-plugin/SKILL.md); the client-specific install and
connection notes are under `marketops-plugin/client-projections/`. The Skill holds no
business fact of its own — every account, count and budget it reports is read from the
server at the moment it is used, so what you see is the deployment's state and not the
package's memory.

1. Run the preflight (§2) and record its verdict with the time.
2. Start the service against this deployment's own data root and ring.
3. Connect the client, and confirm the tool surface the server actually lists — not the
   surface this document implies.
4. Submit **one** run with the four blanks from §3.
5. Read the result: the business keys, the usage, and the outstanding items the server
   itself reports. Read it **by the run's own name** — `run_id` names the run and
   `created_at` carries the moment it started, which is why reading the same `run_id`
   twice returns the same figures. (The listing you get when you name no run is not a
   frozen reading: it answers "which reports exist right now", and that set can move
   between two calls.) An unfinished section is reported as pending, and reading
   "pending" is a correct outcome, not a failure.
6. Record your own judgement of it, separately from the server's report.

## 5. What will not happen on its own

* No run is submitted for you; a run exists because you submitted it.
* No watch, schedule or notification subscription is enabled by the trial.
* No policy changes. A single sentence of feedback never becomes a rule: it becomes a
  correction or a candidate, with its scope and its impact shown first, and approving it
  is a separate act with its own role.
* No email. No client-side copy of the pool, the budget or the permission list is created
  as a side effect — if one appears, that is the failure the Skill's §4 exists to prevent.

## 6. How to read the outcome

Keep these apart; collapsing them is how a trial gets read as more than it showed.

| | Meaning |
|---|---|
| **Ran** | The chain executed end to end on a real question within the authorised budget. |
| **Agreed** | Your judgement of the result matched what you would have done. |
| **Better than you** | Not demonstrated by one run, or by several. |

A trial that ran and disagreed with you is a result. A trial that could not run is a
`BLOCKED_*` verdict plus the reason — it is not a soft pass, and it is not evidence about
the market either way.

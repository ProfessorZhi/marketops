# Roles

One file per stage of the chain. A worker is an agent (a Claude Code subagent, a Codex agent,
a WorkBuddy agent or a separate session) given a project name and one of these roles. It works
its queue with the marketops tools until the queue is empty, then reports in three lines.

| Role | File | Takes work from | Hands work to |
|---|---|---|---|
| research | [research.md](research.md) | `tasks(kind="sweep")` packages | contacts, verdicts, the playbook |
| sources | [sources.md](sources.md) | the brief, coverage gaps | `pool: ...` tasks, or pools simple exports itself |
| pool | [pool.md](pool.md) | `pool: ...` tasks | venues.csv, orgs.csv |
| websites | [websites.md](websites.md) | `orgs(stage="no_website")` | orgs.csv websites |
| dig | [dig.md](dig.md) | `tasks(kind="dig")` - a second look | contacts, verdicts, the playbook |
| finish | [finish.md](finish.md) | `tasks(kind="review")`, the 抽检 sheet | grades, checks, the user's list |

Rules for every role:
- Only the marketops tools add data. Every address comes from a page read with
  `fetch(project, url, org_id=...)`; never write or guess one. Your host's own web search and
  fetch are for looking around.
- One organisation, one writer: work only on what you claimed; hand it back with
  `tasks(action="done", ...)` (or `release=True`) so the pipeline and others can take it.
- Keep notes short and factual; put what only the user can decide in your final report as a
  numbered item, never as a question mid-work.
- Research only; nothing is sent to anyone.
- Several workers share one machine: keep any helper files of your own in a folder named after
  you (e.g. `<scratch>/<worker-name>/`), never in a shared spot another worker may overwrite.

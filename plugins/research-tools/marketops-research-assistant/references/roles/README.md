# Roles

The research is a super-pipeline of pools - sources -> pages -> venues -> organisations ->
contacts. Each role works one stage, every stage runs at the same time, and several workers can
share one role: each claims its own package from its lane, so no two writers touch the same
source or organisation. A worker is an agent (a Claude Code subagent, a Codex agent, a
WorkBuddy agent or a separate session) given a project name and one of these roles. It works
its lane with the marketops tools until the lane is empty, then reports in three lines.

| Role | File | Lane (`tasks(action="claim", kind=...)`) | Takes from pool | Fills pool |
|---|---|---|---|---|
| sources (finder) | [sources.md](sources.md) | `find` (lanes the lead adds) | coverage gaps | sources (`sources(add=...)`) |
| pool (miner) | [pool.md](pool.md) | `source` - one source per claim | sources | pages, venues, organisations (and contacts on the pages read) |
| websites | [websites.md](websites.md) | `site` | organisations without a site | organisations' websites |
| research | [research.md](research.md) | `sweep` - about ten organisations | organisations | contacts, verdicts, the playbook |
| dig | [dig.md](dig.md) | `dig` - a second look | organisations already worked | contacts, verdicts, the playbook |
| finish | [finish.md](finish.md) | `review`, the 抽检 sheet | contacts | grades, checks, the user's list |

`source`, `site` and `sweep` refill themselves: claiming an empty lane cuts a package from what
has arrived in the pool since. A worker whose lane stays empty moves to the lane where
`project(name)["flow"]["waiting_between_pools"]` is highest - it does not wait.

Rules for every role:
- Only the marketops tools add data. Every address comes from a page read with
  `fetch(project, url, org_id=...)`, `harvest` or `run`; never write or guess one. Your host's
  own web search and fetch are for looking around.
- One claim, one writer: work only on what you claimed; hand it back with
  `tasks(action="done", ...)` (or `release=True`) so others can take it.
- Feed the pools behind and ahead of you as you go: a new list you notice ->
  `sources(add=[...])`; an organisation that really runs a venue -> `orgs(add=[...])`. Do not
  mine or research it yourself unless it is in your lane - the pipeline picks it up.
- Keep notes short and factual; put what only the user can decide in your final report as a
  numbered item, never as a question mid-work.
- Research only; nothing is sent to anyone.
- Several workers share one machine: keep any helper files of your own in a folder named after
  you (e.g. `<scratch>/<worker-name>/`), never in a shared spot another worker may overwrite.

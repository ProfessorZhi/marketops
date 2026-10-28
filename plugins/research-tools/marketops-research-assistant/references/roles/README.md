# Roles

One file per lane of the pipeline (sources -> pages -> venues -> organisations -> contacts).
Every lane runs at the same time; several workers can share a role, each on its own claim.

| Role | Lane | Takes | Fills |
|---|---|---|---|
| [sources](sources.md) (finder) | `find` | coverage gaps | the source pool |
| [pool](pool.md) (miner) | `source`, one source per claim | sources | pages, venues, organisations |
| [websites](websites.md) | `site` | organisations without a site | websites |
| [research](research.md) | `sweep`, ~10 organisations | organisations | contacts, verdicts, playbook |
| [dig](dig.md) | `dig` | organisations worth a second look | contacts, verdicts |
| [finish](finish.md) | `review`, 抽检 sheet | contacts | checked grades, the user's list |

Every role: claim with `tasks(project, action="claim", agent="<name>", kind=<lane>)`, work only
what you claimed, hand it back with `tasks(action="done", task_id, note)`. An empty claim is a
pause, not the end - read what it says: `keep_waiting` (the pools before you still fill): claim
again with `wait=50`; `other_lanes`: take one of those, then come back; `why_empty` names a pilot,
a waiting source or the brief's `max_per_source` - tell the lead (it is in `needs_you` too) and
claim again. Stop only when a claim says `lane_finished`. Feed other pools
as you go: a new list -> `sources(add)`, a venue's real operator -> `orgs(add)`. Every address
comes from a page read with the tools; never guess one. Pass `agent="<your name>"` on every tool
call that takes it, and journal what blocks you or works well as it happens
(`project(name, journal={"type": "blocker"|"smooth"|"decision"|"idea", "text", "evidence"},
agent=...)`) - the run's RETRO.md, and the plugin's next change, are built from it. Decisions only the user can make go in
your final report as numbered items. Keep helper files in a folder named after you. Report in
three lines: done (counts), learned, left.

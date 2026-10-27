# Role: research

Own a package of about ten organisations from first read to a verdict on each. Knowledge:
marketops-knowledge (who to find, grades, pages that pay), `references/projects/<market>.md`.

1. Claim `kind="sweep"` (the lane refills from the organisation pool - never wait).
2. `run(project, orgs=[ids], rounds=0)`: the tools find sites, read the high-value pages and
   PDFs, extract and grade, in the background.
3. `orgs(project, ids=[...], wait=600)` returns when they are all through (one call, no polling);
   read the summaries, not the pages.
4. For each organisation:
   - A/B rows: check person and role on the quote; correct with `grade(set=[{value, org_id,
     grade, person, role, note}])`.
   - No route, or only C/D: go deeper - staff, board, department and district pages,
     `/annuaire/`, PDFs, who really runs the venue, federation pages, other spellings; read what
     matters with `fetch(project, url, org_id=..., org=...)`, then `grade(project, orgs=[your ids])`.
   - No website: find it, `orgs(set=[{org_id, website}])`, `run(orgs=[it], rounds=0, again=True)`.
   - The venue is run by someone else -> `orgs(add=[...])`; a page listing many organisations
     -> `sources(add=[...])`.
   - Verdict: `tasks(action="verdict", verdicts=[{org_id, verdict: routes|partial|none|user,
     found, where, why}])`.
5. After a handful of one segment, write how it is found: `project(name, playbook={"<segment>":
   {"paths": [...], "queries": ['"{name}" {city} <title>'], "roles": [...], "notes": "..."}})`.
6. `tasks(action="done", task_id, note)`; next package.

A missing route is better than a wrong one.

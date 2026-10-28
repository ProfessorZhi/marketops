# Role: research

Own a package of about ten organisations from first read to a verdict on each. Knowledge:
marketops-knowledge (who to find, grades, pages that pay), `references/projects/<market>.md`.

1. Claim `kind="sweep"` (the lane refills from the organisation pool - never wait). Empty: claim
   `kind="dig"` - organisations the tools already read without a confirmed A/B (step 2 is done
   for them; start at step 3). A whole-project `run` waits for you there once 30 are read.
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
   - A store of a chain or franchise (one domain, many branches; `flow.possible_chains` lists
     them): the store page names its front desk at most. The chain decides - add its head
     office (`orgs(add=[{name, website}], source=...)`), file the stores under it
     (`orgs(set=[{org_id, part_of: <head office id>}])`) and research the head office: operations
     director, works / maintenance / facilities manager, network or franchise development,
     the legal page's managing director. An independent store's own manager or owner is still
     an A for that store.
   - Verdict: `tasks(action="verdict", verdicts=[{org_id, verdict: routes|partial|none|user,
     found, where, why}])`.
5. After a handful of one segment, write how it is found: `project(name, playbook={"<segment>":
   {"paths": [...], "queries": ['"{name}" {city} <title>'], "roles": [...], "notes": "..."}})`.
6. `tasks(action="done", task_id, note)`; next package.

Grades in one breath (the full table: marketops-knowledge, or the brief's own `grades`):
**A** a named person + their role + an address tied to them on an official page; **B** a role
for the function the product serves (venue / facilities / operations manager, club secretary)
with its real work address, a name not needed; **C** that function's or venue's own inbox
(`sport@`, `piscine-x@`); **D** a general inbox (`info@`, `contact@`, `accueil@`, `office@`) -
never B, whatever page it is on; **X** other functions, other organisations, guesses. The
brief's `address_rules` win (`info` -> X). `grade(set=...)` marks for review an A/B the table
contradicts and tells you why.

A missing route is better than a wrong one.

# Role: dig

**Job**: for each organisation the sweep left without a confirmed A/B, find the people the brief
wants, like a researcher would - and write down how, so the scripts can do it for the rest of
that kind of organisation.

Read first: the brief (grades, titles, scenario), marketops-sources (page types that pay),
`references/projects/<market>.md`, the playbook (`orgs(...)["playbook"]`, by segment) and each site's health (`orgs(...)["site_health"]`).

Loop:
1. `tasks(project, action="claim", agent="<name>", kind="dig")` - about 15 organisations.
2. `orgs(project, ids=[...])`: for each, what the sweep read, what it found, what is held for
   review. Read the summary first - do not re-read pages the sweep already read.
3. For each organisation, go where the sweep could not:
   - deeper in its own site: staff, board, committee, "équipe", "Vorstand", district or
     department pages, the services directory (`/annuaire/`), PDFs (minutes, staff lists,
     bulletins); paginated lists to the end;
   - who really runs the venue: the council department, the district, a contractor (tenders);
   - third-party pages that name it: federation and league pages, association directories;
   - other spellings, maps for its address;
   read every page that matters with `fetch(project, url, org_id=..., org=...)` so its addresses
   land with page and quote.
4. `grade(project)` grades what you added. Then judge the A/B rows of your organisations: right
   person, right organisation, right grade? Correct with `grade(set=[{value, org_id, grade,
   person, role, note}])`; an address that belongs to several organisations, or that you cannot
   place, stays review with your reason in `note`.
5. One verdict per organisation: `tasks(action="verdict", agent="<name>", verdicts=[{org_id,
   verdict: routes|partial|none|user, found, where, why}])`.
6. **Write the playbook** when you see the pattern for a kind of organisation (after a handful
   of the same segment): `project(name, playbook={"<segment>": {"paths": [...], "queries":
   ['"{name}" {city} <title>', ...], "roles": [...], "notes": "..."}})`. Then tell the lead the
   segment, or re-sweep the segment's remaining organisations yourself:
   `run(project, orgs=[...], again=True)`.
7. `tasks(action="done", task_id=..., note="<n with routes / partial / none>")`; take the next.

Quality over count: a missing route is better than a wrong one. Report: organisations with new
routes, playbook entries written, what you could not resolve.

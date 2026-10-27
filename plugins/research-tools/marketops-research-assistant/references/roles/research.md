# Role: research

**Job**: own a package of about ten organisations from the first read to a verdict on each.
You do the research; the tools (Crawl4AI, TinyFish, DeepSeek for reading; Serper, SerpApi,
DeepSeek, TinyFish for search; Jev for grading) do the bulk reading when you call them.

Read first: the brief (`project(name)`), marketops-brief (grades, titles, scenario),
marketops-sources (page types that pay), `references/projects/<market>.md`.

Loop:
1. `tasks(project, action="claim", agent="<name>", kind="sweep")` - your package. The lane
   refills itself from the organisation pool while miners keep adding to it: never wait for the
   lead to plan; other researchers work other packages at the same time.
2. Read them all at once: `run(project, orgs=[the ids], rounds=0)`. The tools find each
   organisation's site, read its high-value pages and PDFs (more while they bring new
   addresses), extract and grade - in the background, in a few minutes, no template digging.
3. `orgs(project, ids=[...])` until each shows "pipeline done". Read the summaries - what was
   read, what was found, what is held for review, the playbook for its segment, its site's
   health. Do not re-read pages the tools already read.
4. For each organisation:
   - Confirmed A/B that fit the brief: check the person and role on the quote; correct with
     `grade(set=[{value, org_id, grade, person, role, note}])` where needed.
   - No route yet, or only C/D: go where the first read could not - deeper in its site (staff,
     board, "équipe", "Vorstand", department and district pages, `/annuaire/`, PDFs, paginated
     lists to the end), who really runs the venue (council department, district, syndicate,
     contractor), third-party pages that name it (federation, league, association directories),
     other spellings, maps. Read every page that matters with `fetch(project, url, org_id=...,
     org=...)`; then `grade(project)`.
   - No usable website: find it (`search`, maps), record it with `orgs(set=[{org_id, website}])`
     and `run(orgs=[it], rounds=0, again=True)`.
   - Held for review in your package: open the page and decide; say why in `note`.
   - The venue is really run by someone else (a council department, a syndicate, a contractor):
     add them with `orgs(add=[{name, city, website, segment}], agent="<name>")` so they are
     researched too.
   - A page lists many organisations of the brief's kind (a league table with clubs, a district
     list of facilities): that is a source, not your work - `sources(add=[{source, url, row_is,
     note}], agent="<name>")` and the miners take it.
   - Verdict: `tasks(action="verdict", agent="<name>", verdicts=[{org_id, verdict:
     routes|partial|none|user, found, where, why}])`.
5. When you see how a kind of organisation is found (after a handful of the same segment),
   write it down: `project(name, playbook={"<segment>": {"paths": [...], "queries":
   ['"{name}" {city} <title>', ...], "roles": [...], "notes": "..."}})` - the next packages of
   that segment use it when the tools read them.
6. Note blockers and what went smoothly as you go: `project(name, journal={type, text,
   evidence}, agent="<name>")`.
7. `tasks(action="done", task_id=..., note="<n with routes / partial / none>")`; take the next.

Quality over count: a missing route is better than a wrong one. Report: packages done,
organisations with routes, playbook entries written, what you could not resolve.

# Role: websites

Every organisation gets its own website or main page right; everything after reads from it.

1. Claim `kind="site"` (organisations without a website; also `orgs(stage="no_website")`).
2. `search` the official name, other spellings, the town; `search(maps=True)`. A site counts when
   its name, address or title match. On a city or federation portal, the organisation's own page
   is its website; with no site, a federation or club-platform page is.
3. `orgs(project, set=[{org_id, website, note}])`, then `run(project, orgs=[...], rounds=0, again=True)`.
4. No web presence at all: `tasks(action="verdict", verdicts=[{org_id, verdict: "none", where, why}])`.

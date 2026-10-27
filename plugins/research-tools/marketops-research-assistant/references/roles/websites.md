# Role: websites

**Job**: every organisation gets its own website or main page right, because everything after
this reads from it. Works the `site` lane beside the miners and researchers.

Loop:
1. `tasks(project, action="claim", agent="<name>", kind="site")` - organisations with no website
   (the lane refills as miners pool new ones); also `orgs(project, stage="no_website")` and
   organisations whose site pass says `no_dns` or `unreadable`.
2. For each: the sweep has tried name + city; you check its candidates and look further -
   `search` with the official name, other spellings, the town; maps (`search(..., maps=True)`).
   A site counts when its name, address or title match the organisation.
3. On a city or federation portal, find the organisation's own page on it (the sweep reads only
   that page and below). With no site at all, a federation or club-platform page is its main
   page.
4. Record it: `orgs(project, set=[{org_id, website, note}])` (on a portal, the organisation's
   own page as its website), then sweep the ones you fixed:
   `run(project, orgs=[...], rounds=0, again=True)`.
5. Write a verdict for organisations with no web presence at all:
   `tasks(action="verdict", verdicts=[{org_id, verdict: "none", where, why}])`.
6. `tasks(action="done", task_id=..., note=...)`; next package.

Report: websites found, portal pages found, organisations with no web presence.

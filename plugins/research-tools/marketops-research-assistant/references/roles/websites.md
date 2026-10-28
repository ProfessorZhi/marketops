# Role: websites

Every organisation gets its own website or main page right; everything after reads from it.

1. Claim `kind="site"` (organisations without a website, source by source; also
   `orgs(stage="no_website")`).
2. Let the tools try the whole package first: `run(project, orgs=[ids], rounds=0)` searches each
   name and town and keeps a domain that carries the name; `orgs(project, ids=[...], wait=600)`
   shows which got one. Your judgement goes to the rest and to the doubtful ones: other
   spellings, registry look-ups by number (a school's UAI, a company's SIRET), `search(maps=True)`.
   A site counts when its name, address or title match. On a city or federation portal, the
   organisation's own page is its website; with no site, a federation or club-platform page is.
3. `orgs(project, set=[{org_id, website, note}])`, then `run(project, orgs=[...], rounds=0, again=True)`.
4. No web presence at all: `tasks(action="verdict", verdicts=[{org_id, verdict: "none", where, why}])`.

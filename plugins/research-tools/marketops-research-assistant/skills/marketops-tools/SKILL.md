---
name: marketops-tools
description: How to get the most out of each MarketOps provider - Serper, SerpApi, DeepSeek and TinyFish search; Crawl4AI, TinyFish and DeepSeek readers; Jev for judging and grading; the paid TinyFish Agent; and Claude's own web search and fetch - with the query recipes and failure handling from Bruce's research sessions. Use when choosing a tool, writing searches, a page will not read, or providers are slow, blocked or out of credit.
---

# Using the tools well

The plugin's tools already run providers in parallel, cache everything in the library and log
every call against the red lines. This page is about choosing and phrasing; each provider's
official parameters, limits and pitfalls are in `references/` - read the one you need:
[deepseek](references/deepseek.md), [tinyfish](references/tinyfish.md) (search, fetch, agent),
[jev](references/jev.md), [serper / serpapi](references/serper-serpapi.md),
[crawl4ai](references/crawl4ai.md).

**Set `country` and `language` in the brief** (`"country": "FR", "language": "fr"`). Every
search engine answers as US / English without them; the plugin passes them to Serper and
SerpApi (`gl` / `hl`), TinyFish (`location` / `language`) and DeepSeek (`lang:`), and sends the
brief's goal as the search intent (TinyFish `purpose`, DeepSeek `ori_question`).

## Search: `search(query, maps?)`

All configured engines answer at once (shares in `brief.providers.search`); the first answer
counts and slower engines get a few seconds; late answers land in the library for next time.

| Engine | Good at | Use |
|---|---|---|
| Serper (Google) | fast (1-2 s), exact phrases, `site:`, `filetype:pdf`, maps places with website and phone | **main engine** - keep it funded; several keys in slots |
| DeepSeek | broad web search; operators `site:`, `"..."`, `lang:`, `after:`; long summaries | **main engine** (paid per request, cheap) |
| TinyFish search | broad web search, fast; `include_domains` | always on, 30 a minute per account (paced for the whole machine) |
| SerpApi (Google / Bing) | the same as Serper | **main engine**, equal to Serper; each stands in for the other when its quota runs out |

Recipes Bruce used most (`derived/websearch-queries.csv`, 1,000 queries):
- **Reverse look-up of an address you already found**: `"<address>"` (his most used query) and
  `"<address>" owner` - who uses it, on which other pages. The pipeline's evidence stage does
  the first; do the second for a business contractor with no name on its site.
- **Inside a site**: `site:<domain> <title word>` (his most frequent operator), `site:<domain>
  "<person>" email`, `site:<domain> filetype:pdf` for committee lists and minutes.
- **Person to organisation**: `"<person>" "<organisation>" email`.
- **Whole lists**: `<federation> <club finder>`, `annuaire <type> <région>`, `site:gouv.fr
  <dataset>`, `<country> <type> elenco / directorio / Verzeichnis`.
- **Places**: `maps=True` with `<what> <town>` for venues with website and phone.
- Never guess an address or a domain and search for it: guessed domains were ~90% wrong.

## Reading pages: `fetch(project, url)`

| Reader | Strength | Note |
|---|---|---|
| Crawl4AI (local browser) | renders JavaScript, keeps `mailto:` and decoded addresses | always on, up to 10 tabs; slowest |
| TinyFish fetch | real browser render, HTML out, reads PDFs | freely (free) |
| DeepSeek fetch | Markdown out, reads PDFs and awkward pages | freely |

A page is as fast as its fastest reader; slower readers finish in the background and their
contacts are merged. Data files (CSV, JSON, XLSX) are downloaded, then `pool`ed.

Mining a list (miners): `harvest` is `fetch` in bulk for one source - list pages first, then
the entry pages several at once, every reader racing as above, each page saved once in the
library so later reads of it (by `pool`, by researchers) cost nothing. Find the entries before
paging through anything: a content sitemap (`sitemap=True`, Crawl4AI's URL-seeding idea) or a
CMS / JSON endpoint lists every entry in one request; paging is the fallback. Regexes are on the
full URL: `follow="/club/\d+"`, `next="[?&]page=\d+"`.

When a page will not read:
1. **Is it the extraction or the site?** Zero links on a big site means a parsing problem
   (gzip, encoding), not a block - check the saved page in the library.
2. **403 / 429 / reset**: retried once automatically. A Cloudflare page with a Ray ID is an IP
   block - another reader may pass, more retries will not.
3. **Content behind a click** (email buttons, "show contact", tabs): `browse(project, url)` -
   the TinyFish Agent, paid; the pipeline notes pages with several email buttons and no
   address. Use it on pages that carry several people, not on single inboxes. A custom `goal`
   should name the fields, the steps, what not to do and the output shape (see
   `references/tinyfish.md`).
4. **Dead domain** (no DNS): the organisation may have moved to a platform or a new domain -
   search its name and town.
5. Record the blocker and move on; a missing page is a finding, not a reason to guess.

## Jev: the cheap judge at every pool

Jev answers typed questions (a Choice among options, a Noul - probability a statement is true)
over rows, in any language, in batches, for about $0.04 per million input tokens. Its place in
the pipeline is TypeSafe's own pattern (`references/jev.md`): **code finds the candidates, Jev
makes the one judgment a person would make at a glance, code decides; what Jev cannot settle
goes to an agent.** That is how it speeds the job up and saves money - a batch of eight pages
judged by Jev costs a fraction of a cent and seconds, the same eight read by an agent costs
thousands of tokens and minutes - and agents spend their time on the rows that need thinking.

| Pool | Jev judges | Code decides | Without Jev (no key, no credit) |
|---|---|---|---|
| sources | what one row of a candidate list is - `judge` on 20 sample rows (venue / club / operator / other) | the finder keeps or drops the list | the finder reads the 20 rows |
| pages (`harvest`) | which link on an entry page is the entry's own website (select, not generate) + a Noul "is any of them its own site?"; what the page describes | website set at Noul >= 0.7; 0.3-0.7 keeps the candidates for the miner | website set only when one candidate carries the name; the miner picks from `website_candidates` |
| venues | in scope? who runs it (`judge` with options) | `filters`, `group_by` | the brief's filters and the miner |
| organisations | outside the brief (`scope=out`, kept), the same organisation?, the official site? (`judge`) | merges, skips | name + place merging; the agent on unclear pairs |
| contacts (`grade`) | the kind of route an address is (decision maker, operator, team / general inbox, not a route) + a Noul "does the quote name its person?" | grade A-D/X, review flag, A -> B downgrade | rules grade the obvious rows (role words, generic inboxes); agents grade the rest with `grade(set=...)` - the review and finish workers share it |
| digging | orders the leads (order only, nothing dropped) | the dig reads in that order | the template order |

`judge(project, file, question, options, column)` puts one decision on every row of any pool
file: give 2-4 options that are mutually exclusive and describe content (`club`, `operator`,
not `A`/`B`), ask about what the row shows, and add an "unsure" option or a separate Noul for
"none of these". Probability of the chosen option, not confidence, is the review signal.

## Tools for a team of agents

| Tool | Use it for |
|---|---|
| `project(name)` -> `flow` | every pool's size, what waits between pools, each lane's open and claimed packages and agents, whether the organisation pool adds up; staff where work waits |
| `sources(project, add=[...], set=[...])` | the source pool: register lists as you find them (finders), move or drop them; each source's state and what it brought, saturation |
| `harvest(project, source, start, follow, next, sitemap, urls)` | mine one source in bulk: page through its listing (or take the entries from its sitemap), read the entry pages several at once, one candidate row per entry in `pages/harvest-<source>.csv`; call again until nothing is queued |
| `pool(project, file, ..., source=...)` | rows into the venue and organisation pools, logged under the source; addresses on saved entry pages (`page_url`) join the new organisations at once |
| `orgs(project, stage="no_ab" \| "review" \| "no_website" \| "unread" \| "held", ids=[...])` | one summary per organisation - what the sweep read and found, rows to review, verdicts - plus the playbook by segment and each website's health (reach, last success, readers it blocks), so you decide without re-reading pages |
| `orgs(project, set=[{org_id, website, segment, scope, note}])` | fix a website, set the segment the playbook keys on, mark scope |
| `orgs(project, add=[{name, city, website, segment}], agent=...)` | add an organisation you found (the department or contractor that really runs a venue); merged on name + place |
| `run(project, orgs=[...], again=True)` | sweep a batch again in the background (after fixing websites or writing the playbook); results land as `orgs(ids=...)` shows |
| `tasks(action="plan" \| "claim" \| "done" \| "add")` | the lanes: `find`, `source`, `site`, `sweep`, `dig`, `review`, `evidence`, `browse`; `source`, `site` and `sweep` refill themselves on claim; while you hold a package the pipeline leaves its organisations alone; a claim untouched for 40 minutes returns to the queue |
| `tasks(action="verdict", verdicts=[{org_id, verdict, found, where, why}])` | one line per organisation you finished (workbook sheet "agent 结论") |
| `project(name, playbook={segment: {paths, queries, roles, notes}})` | what works for a kind of organisation: `paths` read first on its site, `queries` (`{name}`, `{city}`, `{site}`) run first in its dig, `roles` added to its search words |
| `grade(set=[{value, org_id, checked: "ok" \| "wrong"}])` | the delivery spot check (sheet 抽检, precision on 说明) |

Several agents call providers at once: the plugin paces each provider for the whole machine
(TinyFish search 30 a minute, and what a 429 reports), so more workers never means more 429s.

## The host's own web search / fetch (Claude Code, Codex, WorkBuddy)

Fine for a quick check or when every plugin provider is out of credit. Results do not enter
the project or the library: anything worth keeping goes through `fetch(project, url)` so it is
saved, parsed and counted.

## Speed and spend

- More organisations in flight, not more pages per site: the default is 24 organisations per
  process and `run(shards=4)`; each site is read 4 pages at a time.
- Bruce's slow runs were architecture, not network: a heartbeat that reread state every few
  minutes added 8 pages in 14 minutes; a frozen queue with 16 parallel reads, 3 per domain and
  short timeouts read 11 pages in 20 seconds. Never poll; let the pipeline run and check
  `project()`.
- Each paid account stops at its red line (10% of the first balance seen); the rest carry on.
  Serper and SerpApi out of credit: maps discovery falls back to web results.

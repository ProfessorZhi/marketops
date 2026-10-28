---
name: marketops-tools
description: How MarketOps agents use their tools - the marketops MCP tools (project, sources, harvest, search, fetch, pool, run, orgs, tasks, grade, judge, browse, export) and the six providers behind them (Serper, SerpApi, DeepSeek, TinyFish, Crawl4AI, Jev), what each is good at, and how the plugin steps down when one is missing. Use when choosing a tool, writing searches, mining a list, a page will not read, or a provider is slow, blocked or out of credit.
---

# The tools

The MCP tools run the providers in parallel, cache every page and search in a shared library
(a page read once is free afterwards, for every project), log every call against each paid
account's red line (10% of its first balance) and pace each provider for the whole machine.
Every provider is optional. Official details per provider, read when needed:
`references/` [serper-serpapi](references/serper-serpapi.md), [deepseek](references/deepseek.md),
[tinyfish](references/tinyfish.md), [crawl4ai](references/crawl4ai.md), [jev](references/jev.md).

## The six providers

| Provider | Does | Good at | Without it |
|---|---|---|---|
| Serper (Google) | search, maps | fast, exact phrases, `site:`, `filetype:pdf`; maps places with website and phone | SerpApi takes over; with neither, no maps and digging leans on DeepSeek / TinyFish |
| SerpApi (Google / Bing) | search, maps, Facebook pages | the same; maps by page and by map centre; the public fields of a Facebook page | as above; Facebook-only organisations need the host's browsing |
| DeepSeek | search, read | broad search with `site:`, `"..."`, `lang:`; reads PDFs and awkward pages as Markdown | other engines and readers |
| TinyFish | search, read, Agent | free search (30/min) and real-browser reading (PDFs, ten pages per request) - free, use them freely; the paid Agent clicks through hidden contacts (`browse`), exiting from the site's country - **off until the user allows it** (`brief.paid_agent`): a `browse` call meanwhile queues the page and `needs_you` asks once for all | other engines and readers; no clicking |
| Crawl4AI | read (local browser) | JavaScript pages, keeps `mailto:` and encoded addresses; slowest | TinyFish, DeepSeek, plain HTTP; script-built pages read thin |
| Jev | judge, grade | cheap typed judgments on many rows: what a row is, which link is the own site, what kind of route an address is | rules take the obvious rows, agents judge the rest (`grade(set=...)`) |

The working set is DeepSeek (search and read), Serper (Google search and maps), TinyFish's free
search and reading, Crawl4AI and Jev; SerpApi is a backup when its account has credit. With
DeepSeek, TinyFish, Crawl4AI and Jev alone the whole chain runs - without Serper there are no
maps (places with their website), which matters most for private venues and for finding
websites.

Set `country` and `language` in the brief: every engine answers as US / English without them.
Several keys per provider (`NAME`, `NAME_2`..`_9`): a used-up key hands over by itself.

## The MCP tools by pool

| Pool | Tools |
|---|---|
| sources | `sources(add / set)` register and move lists; `search` to find them; `fetch` one page or an export (data files download to `pages/`) |
| pages | `harvest(source, start, follow, next, sitemap, urls)` - page through a listing (or take its sitemap), read entry pages in bulk, one row per entry, Jev picks the own website; call again until nothing is queued |
| venues, organisations | `pool(file, into, filters, map, group_by, source)` - every source into the same pools, merged on name + place; `judge(file, question, options, column)` - one Jev decision per row; `orgs(set / add)` |
| contacts | `run(orgs=[...])` reads a package's sites in the background (rounds 0-2, default 0: digging is yours); `orgs(ids=...)` summaries; `fetch(url, org_id)` for digging; `grade()` (Jev) and `grade(set=...)` (you); `browse` for click-hidden contacts (paid) |
| all | `project(name)` - brief, `flow` (pools, waiting work, lanes), `needs_you`, costs; `tasks(plan / claim / done / add / verdict)` - the lanes; `export` - the workbook |

## Using them well

- **Searches that paid for Bruce**: `"<address>"` (who else uses it), `site:<domain> <title
  word>`, `site:<domain> filetype:pdf` (committee lists, minutes), `"<person>" "<organisation>"
  email`, `<federation> club finder`, `annuaire <type> <région>`; `maps=True` for venues. Never
  guess a domain or an address and search for it.
- **Lightest tool first, heavier only when it failed** (TinyFish's own ladder): search -> fetch
  -> `browse` -> `browse(stealth=True)`. The race already tries every reader; `browse` is for
  contacts behind clicks on a page with several people, never a single inbox.
- **Cover an area, not its top 20**: maps return 20 places a page - `search(q, maps=True,
  pages=3, near=[map centres])` walks a city; a venue pool built from one maps search is a sample.
- **Lists before pages**: an export or API that returns the roster > a content sitemap
  (`harvest(sitemap=True)`) > paging a listing (`harvest(start, follow, next)`) > searching.
- **Jev pattern**: code finds candidates, Jev makes one at-a-glance judgment, code decides, what
  stays unsure goes to an agent. Options describe content (`club`, `operator`), never letters;
  the probability of the chosen option is the review signal.
- **A page will not read**: zero links on a big site is a parsing problem, not a block; a
  Cloudflare page with a Ray ID is an IP block (another reader may pass, retries will not);
  contacts behind clicks -> `browse`; blocked from abroad -> `browse` exits from the site's country (stealth for bot walls); a Facebook-only organisation -> `fetch` its facebook.com page; no DNS -> search the name, it may have moved; a missing
  page is a finding - record it and move on.
- **Speed**: more organisations in flight, not more pages per site. The machine shares one pace
  per provider and at most 4 requests at once per web host (a 429 pauses that host and lowers its
  share), so more agents never means a banned portal. Never poll: `orgs(ids, wait=50)` returns
  when a package is through, `harvest(background=True)` keeps reading while you check rows; look
  at `project()` every 10-15 minutes.
- The host's own web search / fetch are for looking; anything worth keeping goes through
  `fetch(project, url)`.

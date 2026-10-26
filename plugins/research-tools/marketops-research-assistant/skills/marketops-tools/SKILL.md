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
| DeepSeek | broad web search; operators `site:`, `"..."`, `lang:`, `after:`; long summaries | freely (paid per request, cheap) |
| TinyFish search | broad web search, fast; `include_domains` | freely (free, 30 a minute - paced) |
| Serper (Google) | exact phrases, `site:`, `filetype:pdf`, maps places with website and phone | moderately (credits) |
| SerpApi (Google / Bing) | the same, smaller quota | sparingly |

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

## Jev: `judge`, `grade`, ranking, scope

Jev answers multiple-choice questions over rows, in any language, cheaply and in batches.
- `grade(project)`: A-D/X from each address's own quote against the brief's grades; each
  address with the same quote is graded once; names and titles are picked from the quote.
- `judge(project, file, question, options, column)`: one decision per row - in scope?, same
  organisation?, is this the official site?, maintenance in-house or outsourced? Give 2-4
  options that are mutually exclusive and an "unsure" option; ask about what the row shows,
  not what it might mean.
- The pipeline also uses Jev to order dig leads (order only, nothing dropped) and to mark
  organisations clearly outside the brief (`scope=out`, kept, skipped).

## Claude's own WebSearch / WebFetch

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

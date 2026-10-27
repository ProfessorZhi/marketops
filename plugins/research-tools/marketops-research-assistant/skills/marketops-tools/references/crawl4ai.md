# Crawl4AI (local browser)

Official: https://docs.crawl4ai.com; agent skill: `brettdavies/crawl4ai-skill` (verified
against crawl4ai 0.8.9; checked 2026-10-27). Installed by `install.sh` (Playwright Chromium);
`crawl4ai-doctor` checks the install. Optional: without it the plugin reads with TinyFish,
DeepSeek and plain HTTP, and script-built pages read thinner.

## How the plugin uses it
- One shared headless browser, at most 10 tabs; each page `page_timeout` 30 s, cache bypassed,
  1 s settle before the HTML is taken. It always takes part in a read (other readers race it;
  the page is as fast as the fastest reader).
- It returns the full HTML: cards, `mailto:` links and Cloudflare-encoded addresses survive for
  the contact parser - the reason it stays on even when it is the slowest.
- A browser timeout is not retried (it already took 30 s); a dropped connection or 5xx is.

## When a browser is the right reader (the official skill's rule, and Bruce's numbers)
- **Yes**: JavaScript-rendered pages and single-page apps, contacts that load after the first
  paint, infinite scroll, many URLs of one site at once.
- **No**: static pages - a browser pays ~2 s start-up; plain fetches are near instant. Bruce's
  runs: a browser read 2-2.5 domains a minute against 100+ for plain fetches, and 96% of its
  failures were connection resets. The race lets the faster readers answer those.
- Content behind clicks or logins is not Crawl4AI's job here: `browse` (TinyFish Agent).

## What the official skill teaches that the pipeline uses
- **Find URLs before crawling them.** Crawl4AI's `AsyncUrlSeeder` reads sitemaps (and the
  Common Crawl index) and scores URLs against a query without fetching a page; `DomainMapper`
  maps a whole domain. The plugin applies the same idea without a browser: `harvest(...,
  sitemap=True, follow=...)` takes a directory's entries from its sitemap, and site reads rank
  links before reading. A content sitemap (`club-sitemap.xml`) gave +189% pages on single-page
  sites in one of Bruce's runs.
- **Schema extraction for uniform pages.** For a directory whose entries are identical cards,
  Crawl4AI's `JsonCssExtractionStrategy` pulls fields with CSS selectors, no model involved. In
  the plugin the miner's equivalent is `harvest` (one row per entry page, fields by rule, the
  website chosen by Jev) followed by `pool(map=...)`.
- **`wait_until="networkidle"`** waits for the page to go quiet (~500 ms after load) - the right
  setting when contacts fill in late; `wait_for="css:<selector>"` when you know the element.
  Never `css:body` (it exists before anything renders).
- **Do not guess its API.** Names of config fields and strategies change between versions;
  check the installed version (`python -c "import importlib.metadata as m;
  print(m.version('crawl4ai'))"`) and the docs before changing the reader.

## Tuning (only after measuring)
- `wait_until="networkidle"` helps pages that fill in after load, at the cost of time.
- Lower `pages_at_once` if the machine runs out of memory; raise it on a large machine.

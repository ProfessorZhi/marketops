# Crawl4AI (local browser)

Official: https://docs.crawl4ai.com; community skill: `brettdavies/crawl4ai-skill`.
Installed by `install.sh` (Playwright Chromium); `crawl4ai-doctor` checks the install.

## How the plugin uses it
- One shared headless browser, at most 10 tabs; each page `page_timeout` 30 s, cache bypassed,
  1 s settle before the HTML is taken. It always takes part in a read (other readers race it).
- It returns the full HTML: cards, `mailto:` links and Cloudflare-encoded addresses survive for
  the contact parser - the reason it stays on even when it is the slowest.
- A browser timeout is not retried (it already took 30 s); a dropped connection or 5xx is.

## When it is the right reader
- JavaScript-rendered pages and single-page apps; pages whose contacts load after the first
  paint.
- Not needed for static pages: a browser pays ~2 s start-up and renders slowly
  (Bruce's runs: 2-2.5 domains a minute for a browser vs 100+ for plain fetches) - the race
  lets the faster readers answer those.

## Tuning (only after measuring)
- `wait_until="networkidle"` helps pages that fill in after load, at the cost of time.
- Lower `pages_at_once` if the machine runs out of memory; raise it on a large machine.

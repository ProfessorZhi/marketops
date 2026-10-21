---
name: marketops-crawl4ai
description: Crawl4AI local browser crawler in MarketOps - the free default for fetching pages and organisation sites, rendering JavaScript, and reading directory listings and PDFs. Load when fetching or harvesting pages.
---

# Crawl4AI: the free default fetcher

Runs on this machine with its own Chromium; no key, no per-page cost. It is rung 1 inside
`marketops_research_step`, `marketops_site_harvest` and `marketops_harvest_batch`; you rarely call it
directly.

**What it is good at**: JavaScript-rendered pages, following real links (link-driven discovery found
emails at 62–88% of sites; guessed paths 6.5%), keeping `mailto:` addresses, PDFs (minutes, annual
reports naming officers).

**When it fails**:
- Browser cannot start (missing Chromium, blocked by antivirus, broken install): this is a
  problem with this computer, not with the site, and it is never handled silently.
  - Three such failures in a row trigger a browser check. If the browser really does not start,
    Crawl4AI is marked down for this computer, and the answer carries `crawl4ai_down`.
  - When that happens, stop and tell the user. Offer the repair: `marketops_crawl4ai_check`
    with `repair=true`, or Repair.cmd (Windows) or repair.sh in the MarketOps folder. It
    reinstalls Crawl4AI and Chromium into this install, takes a few minutes and downloads
    about 200 MB.
  - Continue with TinyFish fetch only, which is free but has no JavaScript rendering and is
    slower, only if the user says yes. Write that in the report.
  - After a repair, run `marketops_crawl4ai_check(run_id=...)` to confirm with a real page
    read.
- The site blocks it (403/429/challenge): that host is recorded; TinyFish fetch is tried only for
  technical failures, never to get round a challenge or login.
- HTTP 200 can still be a cookie wall or login page: check the saved text before quoting.

**Directories**: fetch the listing page, read it with `marketops_material_excerpt`, then import the
entities into the pool with the count the page shows.

Obfuscated emails (Cloudflare email protection, `name [at] domain`) are counted, never decoded.

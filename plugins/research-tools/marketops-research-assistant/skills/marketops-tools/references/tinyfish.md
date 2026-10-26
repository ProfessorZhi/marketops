# TinyFish: Search, Fetch, Agent

Official: https://docs.tinyfish.ai (index: `/llms.txt`; append `.md` to any page). Checked
2026-10-26. Search and Fetch are free; credits are used only by the Agent and the browser.

## Search - `GET https://api.search.tinyfish.ai` (header `X-API-Key`)
- `query`; `location` (country code) and `language` - **without them it searches as US /
  English** (the plugin sends `brief.country` / `brief.language`); `purpose` (why you search,
  <=2,000 chars - the plugin sends `brief.goal`); `include_domains` / `exclude_domains`
  (comma-separated, preferred over `site:`); `page` 0-10; `recency_minutes`, `after_date`,
  `before_date`; `domain_type` web / news.
- Result: `position`, `site_name`, `title`, `snippet`, `url`.
- Default limit **30 requests a minute**; a 429 carries `error.details.limit` and
  `window_seconds` - the plugin paces Search to 30/min and adopts whatever limit a 429 reports.

## Fetch - `POST https://api.fetch.tinyfish.ai`
- `urls` (up to 10 per request), `format` html / markdown / json (the plugin asks for `html`
  so cards and `mailto:` links survive for the contact parser), `links`, `iframe_links` (for
  booking widgets and embedded directories), `per_url_timeout_ms` (<=110,000; the plugin sends
  35,000), `ttl` (0 = live), `include_selectors` / `exclude_selectors`, `purpose`.
- Rendered in a real browser. Its rate limit counts URLs, not requests.

## Agent - `https://agent.tinyfish.ai/v1/automation/run-async` (paid)
- For pages where contact details appear only after clicks. The plugin submits through MCP with
  a fresh session id and polls the run.
- Goal quality decides success: TinyFish measured specific goals 4.9x faster with 16x less
  unwanted data. A good goal has objective, target, steps, fields, output shape, guardrails and
  edge cases - the plugin's default goal follows that and asks for one line per entry:
  `ROLE | NAME | EMAIL | PHONE`.
- Use it on pages that hold several people (a board, a staff list) behind buttons, not on a
  single inbox.

## Errors to recognise
- `RATE_LIMIT_EXCEEDED` (429): with `details` it is your account's limit; without it the
  upstream provider throttled - retry with backoff.
- 402 / spending limit: the account is out - the plugin stops calling it and carries on with
  the others.

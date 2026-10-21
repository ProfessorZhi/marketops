---
name: marketops-tinyfish
description: TinyFish official MCP (embedded in MarketOps) - free search and fetch as the fallback fetcher, and the paid browser Agent for hard pages (paginated portals, click-to-reveal, the user's own logged-in directories). Load when a page needs the fallback or the hard-case rung.
---

# TinyFish: fallback fetch, and the hard-case browser

The official TinyFish MCP client is built into the plugin.

**Free rung (2)**: `fetch_content` and `search`. Used automatically when the local crawler cannot
render a page; hosts where that happened start at TinyFish next time. It returns vendor-cleaned
HTML (fine for literal quotes) with links listed separately; harvest tools follow those links.

**Paid rung (3): browser Agent**. Off by default; billed per step (about $0.016) from the TinyFish
wallet; `marketops_costs(action='balances')` shows the wallet. Whether a user wants it is that
user's choice: ask at the start of the task (see `marketops-providers`) and record the answer in the
brief. In this release it stays switched off in the plugin; list targets that would need it in the
report. When it is enabled, use it only when all of these hold:
- the target is high value (likely A/B contact or a whole-list source);
- rungs 1–2 failed for a technical reason, or the data only appears after interaction:
  a paginated member search, a "show email" click, a filter form;
- the user authorised a hard-case budget, and for logged-in pages, the account is the user's own and
  its terms allow it.

Never use it for CAPTCHAs, challenge pages, someone else's account or anything the site forbids.
Record what it returned like any other material; it is not evidence until quoted.

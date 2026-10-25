---
name: marketops-tinyfish
description: TinyFish official MCP (embedded in MarketOps) - free search and fetch as the fallback fetcher, and the paid browser Agent for hard pages (paginated portals, click-to-reveal, the user's own logged-in directories). Load when a page needs the fallback or the hard-case rung.
---

# TinyFish: fallback fetch, and the hard-case browser

The official TinyFish MCP client is built into the plugin.

**Free rung (2)**: `fetch_content` and `search`. Used automatically when the local crawler cannot
render a page; hosts where that happened start at TinyFish next time. It returns vendor-cleaned
HTML (fine for literal quotes) with links listed separately; harvest tools follow those links.

**Paid rung (3): browser Agent** (`marketops_hard_page`, runs in the background). The owner turns
it on once in setup; billed per step (about $0.016) from the TinyFish wallet and bounded by that
account's red line; `marketops_costs(action='balances')` shows the wallet. Harvests queue
technically unreadable sites by themselves; call it yourself only when all of these hold:
- the target is high value (likely A/B contact or a whole-list source);
- rungs 1–2 failed for a technical reason, or the data only appears after interaction:
  a paginated member search, a "show email" click, a filter form;
- the page is public (login profiles and vault credentials stay off).

Never use it for CAPTCHAs, challenge pages, someone else's account or anything the site forbids.
Record what it returned like any other material; it is not evidence until quoted.

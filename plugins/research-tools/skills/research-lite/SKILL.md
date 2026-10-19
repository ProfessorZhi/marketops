---
name: marketops-research-lite
description: Conduct small, evidence-backed semi-automated public market research with Serper, Crawl4AI, TinyFish and optional TypeSafe Jev; never treat search snippets as verified facts.
---

# MarketOps Research Lite (semi-automatic)

Use this Skill for public business/location research with a human reviewer. Before doing anything, determine target geography, category, number of candidates, authorized sources and allowed budget. Start with at most five candidates unless user explicitly approves more.

## Tool selection

1. Discover public URLs/places with an already connected search tool (Serper Search/Maps/Places or TinyFish Search). The search result is a **lead**, not a verified fact.
2. For each candidate, open the official website or authoritative source. Prefer low-cost HTTP/Crawl4AI or TinyFish Fetch. Record the canonical URL, page title, date, and exact source content supporting the business field.
3. Escalate to TinyFish `run_web_automation` only for a legitimate multi-step interactive website after the user explicitly approves the likely cost and site scope. Never try to circumvent blocked pages, login walls or CAPTCHA.
4. Use Jev only for bounded Choice/Score/Noul questions that require semantic judgment; preserve probabilities and expose uncertain decisions for human review. Do not use a model answer to fabricate evidence.

## Output contract

Return a compact table with `candidate_name`, `place_or_organization`, `country_region`, `official_url`, `source_url`, `verified_claim`, `status` (`discovered|verified|unknown|rejected`), `confidence_note`, `checked_at`. Distinguish company, venue and person; multiple venues can share one host. Do not infer a contact email from a naming pattern.

## Never do automatically

No direct email or form submission; no login, purchase, subscription, top-up, spend-cap increase, unrestricted browser automation, mass harvesting of personal data or production database write without explicit authorization. Do not reveal provider API keys. If any needed connector is unavailable, report the blocker; do not silently substitute an untrusted third-party MCP.

## Review

Separate observations, inferred classification, and action suggestions. Ask the human to approve final qualification before importing into MarketOps business pools. Never claim a full workflow passed without an actual evidence-backed trial.


---
name: marketops-research
description: Main entry for stage-based, human-reviewed public market research from natural-language intake to sources, venue/organization pools, people, evidence, contact routes and Excel.
---

# MarketOps Research Assistant

Use this as the **one conversational entry** for a user who wants to find market opportunities or research venues/organizations/contacts. This is not a background scheduler.

1. Start with `marketops-intake`. Prefer the approved scope and any existing research pack over inventing a new country, industry, customer class or budget.
2. Read `references/FLOW_AND_SCHEMA.md`; choose the appropriate next-stage Skill. Use `marketops-source-discovery` for search, `marketops-venue-account` for entities, `marketops-person-research` for people, and `marketops-evidence` at *every* step.
3. Invoke *only* currently connected, authorized tools. Search: Serper API/MCP or TinyFish Search. Fetch: Crawl4AI/HTTP or TinyFish Fetch. Dynamic site interaction: TinyFish Agent only with an explicit human approval of site, task and cost. Jev/TypeSafe only for bounded classification after source evidence exists.
4. Preserve individual states: source hit -> candidate -> verified venue/account -> person/role candidate -> verified literal contact channel -> assessed contact route. Do not skip a gate simply because a tool returned JSON.
5. Materialize data in the portable CSV research pack using its stable IDs; run the local read-only validator before handoff. Use `marketops-excel` for the final workbook/CSV review.
6. Stop when scope/budget is exhausted, useful additional sources are unavailable, evidence conflicts, a website blocks access, or human judgment is required. Report `HOLD/UNKNOWN` honestly.

**Hard boundaries**: never send messages, submit forms, top up accounts, change provider billing, bypass login/CAPTCHA, invent emails, silently widen geography, automatically schedule the next run or present spreadsheet row counts as full-market coverage. Business approval cannot be inferred from an Agent's tool permission.

If the user wants a real MarketOps server database change, this Skill alone is insufficient: the authorized MarketOps MCP/Run import must be available. See `references/QUALITY_GATES.md`.
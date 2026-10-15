---
name: marketops-research-lite
description: Use connected TinyFish MCP for small-scale source-backed public business research, with explicit human approval for browser automation and all paid actions.
---

Start with the user's target industry, geography and maximum number of candidates. Use only approved public sources. Search first; fetch actual pages for verification; show every claim's source URL and timestamp. Search snippets only create discovery leads, not verified facts.

Default to at most five candidates and read-only search/fetch. Do not invoke `run_web_automation`, recurring monitors, credential/profile changes, purchases, top-ups, forms or outreach without separate explicit authorization and a budget check. Prompt instructions do not technically disable paid tools: the operator must enforce tool allowlists and provider limits outside this Skill.

If a required tool or OAuth session is missing, report the blocker. Never invent a verified contact, infer email addresses or read private files outside the authorized task. Output a human-review table with discovered/verified/unknown statuses.


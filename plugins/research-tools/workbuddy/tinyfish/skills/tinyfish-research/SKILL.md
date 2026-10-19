---
name: tinyfish-public-research
description: Search and fetch public webpages via TinyFish, capture source URLs and timestamps, and require approval before any paid interactive automation.
---

Use the connected TinyFish MCP for small public research. Prefer `search` for discovery and `fetch_content` for reading a known URL. Before using any other tool, inspect whether it is enabled and whether it can incur charges or change website state.

Do not call browser agent, scheduled monitor, profile setup, external forms, login or billing tools during this pilot. A search snippet is not evidence of a company's actual operations; read official sources and list uncertainty.

Return a table with candidate, official URL, source URL, extracted fact, status and timestamp. If a tool is disabled or OAuth is missing, say so rather than substituting another provider without approval.


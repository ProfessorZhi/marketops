---
name: marketops-source-discovery
description: Find traceable public sources for target markets using connected Serper/TinyFish Search and record search hits without falsely promoting snippets to verified venue/company facts.
---

# Source discovery

Read ResearchBrief and existing pool before spending resources. Reuse already-fetched, in-scope, nonstale evidence; then prepare country/language-correct queries and a small bounded batch.

- Use **Serper Search / Maps / Places** for broad discovery when configured, or **TinyFish Search**; neither is guaranteed to be free. Provider MCP names are not stable assumptions—discover available tools at runtime.
- Store each hit in `sources.csv`: a stable `source_id`, query, URL, `kind`, discovered timestamp, and `status`.
- Determine whether a result points to an independent venue, an organization website, a directory listing or an index with *multiple* venues. **Never deduplicate the entire site down to one venue**; use both canonical URL and provisional entity identity.
- Source summaries/maps are leads, **not** evidence of legal identity, surface type, purchase authority, person employment, or actual email.
- If a search method yields zero results, record how much was searched and why an alternate query/source might help; do not claim there are no businesses in the market.
- Fetch selected official pages with `marketops-evidence` support. Ordinary readable pages need not trigger browser automation; blocked pages remain blocked.
- For authorized batch work, preserve query, locale, result count, credits if the provider reports them and a clear stop reason.

Then hand off relevant rows to `marketops-venue-account`.
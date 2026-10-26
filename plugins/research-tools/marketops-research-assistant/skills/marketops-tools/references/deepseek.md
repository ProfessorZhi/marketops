# DeepSeek Search & Fetch

Official: https://api-docs.deepseek.com/guides/search (checked 2026-10-26; launched 2026-10).

## Search - `POST https://api.deepseek.com/v1/tools/search`
- Body: `query` (1-128 characters, operators kept), `count` 1-50 (default 10),
  `ori_question` (the user's goal, <=128 - the plugin sends `brief.goal`), `need_content`
  (page text in each result), `localization` (an IP to localise results).
- Operators inside `query`: `site:a.fr OR site:b.fr`, `-site:`, `"exact phrase"`, `-term`,
  `lang:fr,en`, `after:2025-01-01`, `before:`. The plugin appends `lang:<brief.language>` when
  the query has room.
- Each result: `title`, `url`, `snippet` (100-200 chars), `summary` (600-800 chars, written for a
  model - the plugin keeps it for name matching), `content`, `rerank_score` 0-3 (>= 1.5 good),
  `authority` 0-4, `date`.
- 128 characters is tight: put the organisation name in quotes and one title word; drop filler.

## Fetch - `POST https://api.deepseek.com/v1/tools/fetch`
- Body: `url`. Returns `title` and `content` as Markdown. Reads PDFs.

## Limits and cost
- Concurrency is per account (the model table shows 2,500 / 500 for the chat models; the tools
  are not listed separately). Over the limit: HTTP 429 - the plugin retries once after a pause.
- Paid per successful request (third-party reports: about $0.75 per 1,000 for each endpoint);
  failed fetches are reported as not billed. The plugin logs every call in `costs.jsonl`.

## Use it for
- Broad web search in any language (the plugin's default share 1.0).
- Reading pages and PDFs other readers fail on.

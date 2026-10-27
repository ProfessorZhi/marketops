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

## Limits and cost (official page, re-checked 2026-10-27)
- Rate limit per **account**, not per key: Search 100 requests/s, Fetch 25 requests/s, apart
  from the chat models' limit. Several keys of one account in slots do not add speed or money -
  only keys of different accounts do. Fetch also has a per-target-site limit (HTTP 429 too).
- Price: 5 yuan per 1,000 successful requests for each endpoint (about $0.0007 a call).
  Only HTTP 200 with results is billed: empty searches, failed fetches and timeouts are free.
  Calls DeepSeek makes for its own models inside Claude Code / Codex are free until 2026-11-01
  00:00 Beijing time, billed after; the plugin's calls are direct and billed already.
- Errors: 402 no balance (the next slot takes over); 401 bad key (next slot); 403 fetch refused
  by DeepSeek's safety policy; 404 fetch got no usable Markdown - the page may well be fine
  (the plugin records it as "no text" for this reader, never as a dead page); 408 / 429 / 5xx
  are retried once after a pause.
- Speed: at 100 searches and 25 fetches a second the account limit is
  never what slows a run. A slow run is waiting somewhere else (`project()["stages"]`).

## Use it for
- Broad web search in any language (the plugin's default share 1.0).
- Reading pages and PDFs other readers fail on.

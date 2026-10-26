# Serper and SerpApi (Google results)

Official: https://serper.dev and https://serpapi.com/search-api. Both are paid per search;
the plugin keeps them at a lower share (`brief.providers.search`: serper 0.5, serpapi 0.3)
and brings them in when the free engines return fewer than 3 results.

## Serper - `POST https://google.serper.dev/search` and `/places` (header `X-API-KEY`)
- `q`, `gl` (country), `hl` (language), `num`. The plugin sends `brief.country` /
  `brief.language` as `gl` / `hl` - without them Google answers as for the US.
- `/places` returns maps places with `website` and `phoneNumber`: the best single call to turn
  "stade <town>" into organisations with sites.
- Google operators work as typed: `site:`, `filetype:pdf`, `"exact"`, `-term`, `intitle:`.

## SerpApi - `GET https://serpapi.com/search.json`
- `engine=google` (`q`, `gl`, `hl`, `num`) or `engine=google_maps` (local results with website
  and phone). Out of searches: the response says "run out" - the plugin marks the account
  exhausted and stops calling it.

## When to spend them
- Maps discovery of venues (`search(..., maps=True)`), exact-phrase reverse look-ups of an
  address, and `site:` searches where Google's index is deepest.
- Not for broad questions the free engines answer as well.

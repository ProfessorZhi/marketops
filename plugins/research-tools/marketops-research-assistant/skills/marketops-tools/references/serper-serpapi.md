# Serper and SerpApi (Google results)

Official: https://serper.dev and https://serpapi.com/search-api; SerpApi's own MCP server
(`serpapi/serpapi-mcp`, 118 engines, per-engine parameter schemas) - read 2026-10-27. Both are
main engines and stand in for each other when one runs out; several keys per provider in slots.

## Serper - `POST https://google.serper.dev/search` and `/places` (header `X-API-KEY`)
- `q`, `gl` (country), `hl` (language), `num`, `page`. The plugin sends `brief.country` /
  `brief.language` as `gl` / `hl` - without them Google answers as for the US.
- `/places`: maps places with `website` and `phoneNumber`; `page` for the next 20.
- Google operators work as typed: `site:`, `filetype:pdf`, `"exact"`, `-term`, `intitle:`.

## SerpApi - `GET https://serpapi.com/search.json`
- `engine=google` (`q`, `gl`, `hl`, `num`, `start`).
- `engine=google_maps`, `type=search`: 20 places a page; `start` = 0, 20, 40 ... up to about 100
  (Google Maps repeats itself beyond); `ll=@lat,lon,14z` centres the search. One search shows a
  town's top 20; a grid of centres with a few pages each is how a venue pool covers a city -
  `search(q, maps=True, pages=3, near=["@48.86,2.34,14z", ...])`.
- `engine=facebook_profile` (`profile_id` from the page URL): the public fields of a Facebook page
  - email, phone, website, address. Many small clubs exist only on Facebook; `fetch` on a
  facebook.com URL uses it when a SerpApi key is set.
- Other engines worth knowing: `bing_maps`, `apple_maps` (a second places source), `google_local`.
- Out of searches: the response says "run out" - the account is marked exhausted, the next slot
  or Serper takes over.

## When to spend them
- Venue discovery on maps, exact-phrase reverse look-ups of an address, `site:` and
  `filetype:pdf` digging where Google's index is deepest, Facebook-only organisations.
- Not for broad questions the other engines answer as well.

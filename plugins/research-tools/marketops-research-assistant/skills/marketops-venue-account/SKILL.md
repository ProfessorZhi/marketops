---
name: marketops-venue-account
description: Build distinct venue, organization and responsibility-relation pools with source evidence and per-scene qualification; do not conflate a website host with one venue.
---

# Venue / Account pools

This Skill accepts `sources.csv` and source-backed `evidence.csv`. Make **two parallel entry paths**:

1. Asset-driven: venue listing -> `venue_candidates.csv` -> after verification -> `venues.csv`.
2. Account-driven: business/distributor/contractor website -> `organizations.csv`, without creating a fictitious venue.

Canonical facts and joins:
- `venues.csv`: one physical venue/asset per ID; names, geo, venue category, original candidate and source evidence.
- `organizations.csv`: one organization per ID, with official domain only if verified.
- `venue_org_links.csv`: link venue and organization using typed owner / operator / maintainer / procurement / other. Distinct roles may refer to different organizations.
- `account_assessments.csv`: *per Scene* decision eligible / hold / irrelevant / duplicate, not an intrinsic permanent property of an organization.

Never infer ownership from a website's host/title. A directory may list thousands of sites, and one company may manage several venues. A phone line or general mailbox does not prove who purchases equipment.

Use provider tools: inexpensive fetch or Crawl4AI for content extraction, TinyFish for authorized interactive indexes, TypeSafe/Jev for uncertain bounded classification *after* evidence exists. A Jev answer is not a source document.

Every verified venue, organization relation and qualification must point to usable evidence. Lack of fact -> `hold`, never a guessed value. Pass eligible/held organizations to `marketops-person-research`.
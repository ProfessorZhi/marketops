---
name: marketops-accounts
description: Research one organisation or venue to its best public contact routes with quoted evidence - own-site harvest, relationships, roles, grading and HOLD. Use for each target in a MarketOps Run, or inside the account-researcher subagent.
---

# Account procedure

Repeat for each organisation until it has a result. Use only `marketops_*` tools; when a write
returns `QUOTE_NOT_IN_ARTIFACT`, copy from the closest page text it shows - do not open the plugin's
code. Two failed tries on one target means HOLD and the next target.

1. **Official site.** Use the website from a saved map result or one Serper query. A directory,
   listing or aggregator page is never the official site.
2. **Harvest.** `marketops_site_harvest(run_id, operation_id, organization_name=<name exactly as the
   site publishes it>, organization_key, url=<official site>, max_pages=6)`. It fetches the page and
   its contact/people/hire pages, cuts literal quotes, writes the organisation and every email/phone
   as candidates, and returns each record's gate decision. It also tries the club's usual site
   spellings ("Essex County Cricket Club" -> "Essex Cricket", "Harlow CC"). `NAME_NOT_FOUND_ON_SITE`:
   read the routes' context for the published name and retry once. `SITE_UNREACHABLE`: HOLD.
   In bulk, `marketops_harvest_batch(..., roles_wanted="<the brief's roles>")` does this for 20
   targets and Jev tags every email with a role guess and confidence.
3. **Judge each route** from its Jev tag and `context`: who it reaches (named person, department,
   generic inbox, switchboard) and what role. Apply the brief's or acceptance profile's grades.
   Read the page yourself only when the tag's confidence is below 0.85.
4. **Missing decision-maker?** One Serper role search:
   `site:<domain> "Grounds Manager" OR "Head Groundsman" OR "Facilities" OR "Bookings"`, then
   harvest the best staff page. Also `site:<domain> filetype:pdf groundsman OR grounds`: recruitment
   packs, minutes and annual reports name officers and often their email. PDFs fetch with
   `marketops_research_step` like pages; note their date.
   **A named person in the wanted role without an email is still a result.** Fetch the page the
   search result points to, then save a `role` record (person, title, quote) so the person appears
   in the workbook as a lead; never keep a name only from a search snippet.
5. **Venue link.** If the brief is venue-led, save the venue and an `operates`/`owns` relationship
   whose quote names both sides (see example).
6. **Close the target.** Admitted routes, or a HOLD review with the reason and pages tried.

## Rules the gate enforces (write it right the first time)

- Every claim: `claims:[{field, artifact_id, quote}]`, quote 12–600 characters copied from that
  saved page, the artifact also listed in `artifact_ids`. Organisation and venue need
  `field:'identity'`; relationships need `field:'relationship'`; exclusions `field:'exclusion'`.
- A contact binds to its organisation when the quote names the organisation, or it comes from the
  organisation's own site (where it has an identity quote and no other organisation does), or a
  relationship record names both. Directory pages never bind a contact.
- Literal values only: no email patterns, no search-snippet emails, no Markdown-only emails.
- A named person's role and contact share the person's `entity_key` as `person_id`; a department
  mailbox has no `person_id` and does not satisfy a named-person brief.
- Generic inboxes (info@, office@) and switchboards are recorded but never presented as the key
  contact. Owner, operator, maintainer and buyer may be different organisations.
- Corrections: new `record_id`, same `entity_key`, `facts.supersedes=<old record_id>`.

Venue, organisation and relationship from one saved page (replace every example value):

<!-- tested-example:venue-organization-relation -->
```json
{"run_id":"run-example","items":[
  {"record_id":"org-example-v1","stage":"entities","kind":"organization","entity_key":"org-example","facts":{"name":"Example Community Trust","claims":[{"field":"identity","artifact_id":"art-example","quote":"Example Community Trust operates Example Centre."}]},"artifact_ids":["art-example"]},
  {"record_id":"venue-example-v1","stage":"entities","kind":"venue","entity_key":"venue-example","facts":{"name":"Example Centre","organization_id":"org-example","claims":[{"field":"identity","artifact_id":"art-example","quote":"Example Community Trust operates Example Centre."}]},"artifact_ids":["art-example"]},
  {"record_id":"relation-example-v1","stage":"entities","kind":"relationship","entity_key":"relation-example","facts":{"venue_id":"venue-example","organization_id":"org-example","relationship_type":"operates","claims":[{"field":"relationship","artifact_id":"art-example","quote":"Example Community Trust operates Example Centre."}]},"artifact_ids":["art-example"]}
]}
```

A department role and its route, without a named person:

<!-- tested-example:department-role-contact -->
```json
{"run_id":"run-example","items":[
  {"record_id":"role-example-v1","stage":"people","kind":"role","entity_key":"role-example","facts":{"organization_id":"org-example","role_title":"Facilities team","role_function":"Manages the centre","claims":[{"field":"role","artifact_id":"art-example","quote":"The Facilities team at Example Community Trust manages the centre."}]},"artifact_ids":["art-example"]},
  {"record_id":"contact-example-v1","stage":"routes","kind":"contact_point","entity_key":"contact-example","facts":{"organization_id":"org-example","department":"Facilities team","type":"email","value":"facilities@example.org","claims":[{"field":"email","artifact_id":"art-example","quote":"Contact the Facilities team at facilities@example.org."}]},"artifact_ids":["art-example"]}
]}
```

Read saved pages with `marketops_material_excerpt` (keyword queries, ≤1200 characters) instead of
pulling whole pages into context. Check gaps with `marketops_evidence_audit` before moving on.

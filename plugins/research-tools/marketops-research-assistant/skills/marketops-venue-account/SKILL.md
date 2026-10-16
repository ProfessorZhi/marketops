---
name: marketops-venue-account
description: Extract distinct venue, organization and responsibility candidates from saved Run material; preserve evidence and entity identity through the unified SQLite contract.
---

# Venue / Account candidates

**Trigger:** saved sources/material exist but requested target entities are missing, or a target has an identity/relation gap. **Input:** the same Run's Brief, source/material IDs and current venue/organization records. Contract: [workflow](../../references/WORKFLOW_CONTRACT.md).

Keep two entry paths: asset-driven venue discovery and account-driven organization discovery. A distributor/company task need not invent a venue. A directory or council website may list many distinct venues. Owner, operator, maintainer and procurer may be different organizations; a domain or public telephone does not prove the relationship.

Read existing targets with marketops_status filters. Use marketops_propose for separate `venue`, `organization` and `relationship` records at stage `entities`, with stable entity keys, explicit joins and actual in-Run artifact_ids. Known organization joins use `organization_id`; relationship records additionally use venue_id and relationship_type. Never hand off by editing venue_candidates.csv, venues.csv or organizations.csv.

Every assertion remains a candidate. Per-Scene eligibility is a source-backed assessment proposal, not an intrinsic organization property or a contact grade. Unknown facts stay missing/HOLD. Jev may help interpret already-saved content but its answer is not independent evidence or approved SOP.

Detect conflicting observations/duplicate keys before adding more. Append explicit `supersedes` corrections rather than overwrite history. No automatic cross-Run canonical merge exists; similar names/domains are not proof of identity. Do not reuse foreign Run artifact IDs.

**Output:** read-back candidate IDs/joins/material refs and unresolved conflicts. **Exit:** call marketops_next. Venue-pool tasks may proceed to review without people research; contact tasks proceed only for their actual organization targets. Bounded exhausted research gets a target disposition with reason, not invented completion.

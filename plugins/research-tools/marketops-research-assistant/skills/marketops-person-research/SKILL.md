---
name: marketops-person-research
description: Identify public professional people/departments, current roles and literal contact points, keeping candidate status distinct from a formally graded evidence-backed ContactRoute.
---

# Person / role / contact research

Input: an in-scope organization or verified venue responsibility, a declared buyer/sales motion and priority role matrix. Search official staff/team/contact pages and attributable published documents.

- Store candidate **people/departments** separately from addresses, and evidence-backed employment/role claims separately from guessed authority. A 2021 staff PDF does not prove a person still works there.
- In `people.csv`, keep person, department, role title, organization and role evidence. For department-only routes, leave person_id blank and retain the organizational role function.
- `contact_points.csv` includes source-literal work email or separate non-email signals. No extrapolated `first.last@`, no emails from search snippets, no guessed owner when several names are on one page.
- Verify an email's *literal occurrence* in a usable retrieved page/document and the claimed organizational/person association. When an email is valid but ownership is unclear, mark `hold`.
- `contact_routes.csv` is a separate assessment over organization + role + contact point + evidence. A/B/C/D grading belongs to a *route*; client qualification belongs in `account_assessments.csv` and does not determine grade.
- A role without a verified work email is a **RouteOpportunity**, not Grade B by default. Non-email signals (phone, form, professional profile) do not automatically become an email route.
- A formally `ready` route needs human `reviewed_by` in the portable Lite pack, a usable evidence reference and an eligible organization assessment where applicable.

No email sending or form submission under this Skill. Handoff to evidence review and Excel.
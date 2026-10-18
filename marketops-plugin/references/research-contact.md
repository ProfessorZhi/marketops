# People, roles and public contact routes

Use when the request is about **who to contact at an organisation**, which
responsibility a person holds, or which public route reaches them.

## The three things that must stay separate

Requests like "find the facilities manager at this site" mix three different
facts. Keep them apart, because each has its own source and its own freshness:

| Fact | Read it from | Never infer it from |
|---|---|---|
| Who holds which responsibility, over time | `mcp:get_account` — the temporal dossier and its responsibility relations | a job title string in one page |
| Which public route reaches them | `mcp:get_account` — the contact routes it returns, with their evidence | the address pattern of the organisation's website |
| Whether a route is still current | the freshness the same call reports | when the dossier was last read |

A role that was observed on a page and a role that has been settled as fact are
different things. `mcp:get_decision` reads back the judgement that settled one,
with the provider and the usage it cost — it never re-decides and never re-bills.

## Order of operations

1. Read the account with `mcp:get_account`. That call is the whole dossier: the
   identity, the routes, the opportunities and the evidence references.
2. Follow an evidence reference with `mcp:get_evidence` to show the user the
   material a claim rests on. Do not paraphrase an artefact from memory.
3. Where a responsibility is being recorded or withdrawn, `mcp:remember_responsibility`
   and `mcp:list_responsibilities` are the surface. Withdrawing a claim that
   drafts cite is `mcp:withdraw_claim`: affected **unsent** drafts go on hold and
   unrelated work is unchanged. Present that outcome; do not pre-compute which
   drafts are affected.

## Front page vs named contact

A generic organisational route and a named person's route are different answers,
and the difference is not a quality problem — it is a fact about the
organisation. Report which one the server returned. Do not upgrade a generic
route into a named contact by combining it with a person found elsewhere.

## Correcting a fact

When the user says a person has left, or that a route bounced, that is a fact
correction, not a rule change:

* record it with `mcp:submit_feedback` using the fact-correction kind;
* show the effect with `mcp:preview_feedback_impact` **before** anything is
  presented as a rule;
* if the user is stating a rule about a scenario rather than a fact about one
  organisation, that is a different reference and a different use case — see
  `SKILL.md` §5.

## Boundaries

* Do not guess an address from a name, a domain or another employee's pattern.
* Do not present a candidate as a settled fact because it is plausible.
* Do not describe what a provider would return; read the stored artefact.

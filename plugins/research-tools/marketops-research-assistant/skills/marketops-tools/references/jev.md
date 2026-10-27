# Jev (TypeSafe System One)

Official: https://docs.typesafe.ai (index `/llms.txt`) and TypeSafe's own agent skill,
`typesafe-ai/skills` (`claude plugin marketplace add typesafe-ai/skills`). Checked 2026-10-27 (skill read in full).

## What it is
A model that answers typed questions about the `state` you send: **Choice** (one option of a
set, with a probability per option), **Noul** (probability a statement is true), **Score**
(levels), Box (images). `jev-latest` -> `jev-1.14.0`. Priced per input token ($0.042 per
million); output is free. Context 64k per request / 32k per question. Rate limits in tokens and
requests per second; the SDK retries 429 / 529 with backoff by default.

## How the plugin asks (and why)
TypeSafe's guidance, applied in `grade` and `judge`:
- **Judgments from Jev, decisions in code.** Jev says what kind of route an address is and
  whether a person is named; code turns that into the grade, the review flag and the A -> B
  downgrade.
- **Option names describe the content** - `decision_maker`, `operator_or_forwarder`,
  `team_inbox`, `general_inbox`, `not_a_route` - letters and positional names bias the model.
- **Each candidate sits in its own question** (organisation, address, page, quote), the shared
  brief and grading guide go in `state` once; many questions per request are cheap and run in
  parallel.
- **A Noul for the null case** ("does this text name the person who uses X?") instead of a
  "none" option.
- **Probability of the chosen option, not confidence, is the review signal.** Confidence only
  says how spread the answer is. A Noul near 0.5 means the text does not settle it; 0.7 / 0.3
  are good starting thresholds.

## Where Jev sits in the pipeline (TypeSafe's cookbooks, applied)
- **Select instead of generate** - `harvest`: code collects the links an entry page shows, Jev
  picks which one is the entry's own website, with a Noul for "none of them"; code keeps the
  candidates when the Noul lands between 0.3 and 0.7.
- **Find and judge evidence** - `grade`: code finds each address and the lines around it, Jev
  says what kind of route it is and whether the quote names its person.
- **Verify and escalate** - what Jev leaves unsure (low probability, a Noul near 0.5, an address
  on several organisations) is marked review and goes to an agent, never dropped and never
  decided by a confidence value alone.
- **Ask speculatively, bundle by state** - questions that share a page or a row go in one
  request (kind + own site + which site for one entry page); the state is paid once.
- **A failed call is an error, not an answer**: a Jev failure leaves the row for the rules and
  the agent; it is never read as "no".
- **No Jev at all** (no key, no credit, red line): every pool still works - see the table in the
  marketops-tools skill; agents take the judgments, rules take the obvious rows.

## Writing your own `judge` questions
- Start from the decision: write the truth table (what code does for each combination of
  answers, including "unsure"), then ask only the judgments it needs.
- Design the state first: give what a person would look at (names, the row's own fields), every
  field relevant, nothing truncated that the question needs. One item per question, not an id
  into a shared list.
- Pass the human test: could a person answer from this row and this question at a glance?
  If not, fix the fields, not the wording.
- One judgment per question; split "or". Name the confusing case in the criteria
  ("a federation's own office is not the club").
- Avoid prompt gymnastics: criteria that name test rows, thresholds fitted to a few cases,
  caveats that explain away a quirk, code that quietly overrides answers. Fix the state or split
  the question instead.
- Test new questions on a handful of real rows (representative, hard, and "nothing fits") before
  running them over a pool; keep the payload so a result can be rerun. Live tests spend the
  user's `TYPESAFE_API_KEY` - say so.
- Bundle questions that share a row into one request.

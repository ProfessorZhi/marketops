# Jev (TypeSafe System One)

Official: https://docs.typesafe.ai (index `/llms.txt`) and TypeSafe's own agent skill,
`typesafe-ai/skills` (`claude plugin marketplace add typesafe-ai/skills`). Checked 2026-10-26.

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

## Writing your own `judge` questions
- Pass the human test: could a person answer from this row and this question at a glance?
  If not, fix the fields, not the wording.
- One judgment per question; split "or". Name the confusing case in the criteria
  ("a federation's own office is not the club").
- Avoid prompt gymnastics: criteria that name test rows, thresholds fitted to a few cases.
- Bundle questions that share a row into one request.

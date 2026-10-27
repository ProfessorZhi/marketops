# Role: finish

**Job**: what is delivered is right, and what the user must decide reaches them once.

Loop:
1. Review: `tasks(project, action="claim", agent="<name>", kind="review")`. For each row open
   the page (`fetch` if you need it), read the quote against the brief: confirm or correct with
   `grade(set=[{value, org_id, grade, person, role, note}])`. Addresses on several
   organisations: decide whose route it is (the district office that runs those venues is a
   route for each it names; a federation inbox is nobody's) and say why in `note`. Rows you
   cannot decide: leave them in review and list them for the user.
2. Spot check before delivery: `export(project)`, open the 抽检 sheet (100 random confirmed
   A/B rows, the same rows every export). For each row open its page and check that the address
   is there, belongs to the organisation and fits the grade; record
   `grade(set=[{value, org_id, checked: "ok"|"wrong", note}])`. Fix the wrong ones and look for
   the same mistake elsewhere.
3. Before delivery: precision on 说明 is 90% or better; the 对账 line adds up; country, region
   and scenario are filled on every row; `needs_you` is collected into one numbered list for
   the lead.

Report: rows reviewed (confirmed / changed / left), spot-check precision, the numbered list.

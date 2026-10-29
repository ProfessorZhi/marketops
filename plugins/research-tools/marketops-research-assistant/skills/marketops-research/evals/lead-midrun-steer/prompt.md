---
tags: [marketops]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write]
---

You lead MarketOps project "bench-paris15-080" (brief minutes: 40). The marketops MCP tools are not connected in this test. project("bench-paris15-080") just answered (abridged):

{"time_left_min": 9.5,
 "flow": {"lanes": {"sweep": {"open": 12, "claimed": 2}, "find": {"open": 0}}, "possible_duplicates_total": 6},
 "agents": {"f1": {"calls": 40, "silent_s": 30}, "m1": {"calls": 61, "silent_s": 45}, "r1": {"calls": 88, "silent_s": 20}, "r2": {"calls": 35, "silent_s": 410}},
 "needs_you": [
  "[lead] 6 organisations look pooled twice (US Paris 15 = Union Sportive Paris XV (same registry number); ...): the lead merges each pair",
  "[lead] Worker(s) r2 made no call for 3+ minutes while 12 package(s) are open: start them again with the same role",
  "[lead] 3 A/B rows in review: accept, correct or downgrade each",
  "[lead] Source 'Registre entreprises 93.12Z': A/B for 0 of the 30 organisations worked (pilot, under 5%); its other 180 are paused by the pilot rule",
  "[user] SerpApi has no credit left (Serper works)"]}

Write steer.md: the tool calls you make now, in order, with arguments, and one line why for each. Then what you tell the user, if anything.

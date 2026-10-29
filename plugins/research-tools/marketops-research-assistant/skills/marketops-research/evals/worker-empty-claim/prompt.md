---
tags: [marketops]
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write]
---

You are the research worker "r3" on MarketOps project "bench-lyon3-080". The marketops MCP tools are not connected in this test: plan the calls you would make and assume each one succeeds as the tools describe; here is what they answered you, in order:

1. tasks(action="done", task_id="t17", note="10 orgs, 4 with A/B") -> {"queue": {"sweep": {"open": 0, "claimed": 2, "done": 5}}, "next": "claim your next package now: tasks(action='claim', kind='sweep', wait=50) - end only when a claim says lane_finished"}
2. tasks(action="claim", kind="sweep", agent="r3", wait=50) -> {"task": null, "keep_waiting": true, "why_empty": "source 'Mairie 3e - creneaux' is being mined by m1: organisations are coming"}
3. tasks(action="claim", kind="sweep", agent="r3", wait=50) -> {"task": null, "keep_waiting": false, "other_lanes": {"source": 1}, "why_empty": "no organisation waits; 1 source registered and nobody mines it"}

Write actions.md: after each answer, the next tool call you make (exact arguments) or that you end, and one line why. Then say when you would end your turn.

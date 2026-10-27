---
description: Run a MarketOps research task end to end with a team of agents
argument-hint: <task, e.g. "Swiss outdoor natural-grass football pitches, people who look after the grounds">
---

Use the `marketops-research` skill to lead this research task from start to finish:

$ARGUMENTS

Ask at most once, up front, for what is missing (what the user sells, region, kind of
organisation, roles). Then start the whole team at once with your host's multi-agent mode -
finders, miners, researchers, websites, finish, several per stage - steer them by
`project()["flow"]` (what waits between the pools),
put anything only the user can decide in one numbered list, and deliver the Excel when the
spot check passes.

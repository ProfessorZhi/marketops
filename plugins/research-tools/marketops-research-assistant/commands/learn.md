---
description: Turn a finished MarketOps project into lasting knowledge for the next one
argument-hint: <project name>
---

Distil what the MarketOps project `$ARGUMENTS` taught into the plugin's project knowledge, so
the next project in the same market or line starts from it.

1. `project("$ARGUMENTS")` and `export("$ARGUMENTS")`; read the workbook sheets "说明",
   "数据源", "贡献（消融）" and "过程（耗时）", the brief, and the run notes.
2. Pick the matching file in the plugin's `references/projects/` (same market and kind of
   organisation), or start a new one from the template in `references/projects/README.md`.
3. Add, in the template's sections:
   - pool sources with organisations brought and the share with A/B;
   - the page types and paths where first A/Bs came from;
   - titles that ended A or B (local language) with counts;
   - every correction the user made to the brief (old rule -> new rule, why);
   - pitfalls: what slowed the run or hid results, how it showed, what fixed it;
   - one benchmark row: organisations, share with A/B, named A/B, minutes, cost, plugin version.
   Keep numbers that the workbook shows; mark anything else as an observation.
4. If a lesson changes how every project should run, propose the edit to the
   `marketops-knowledge`, `marketops-research` or `marketops-tools` skill in the same change.
5. Never write the researched organisations' names, people's names, addresses, phone numbers
   or account ids: counts, titles, source names, page types and rules only. Scan the change
   for `@` and long digit runs before saving.

Show the user the diff and where it was saved; committing it is their call.

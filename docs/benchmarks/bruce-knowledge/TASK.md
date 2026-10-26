# Task: extract Bruce's business knowledge for the plugin skills

Runs on the machine that holds Bruce's corpus. The output feeds the per-line files in the
plugin's `references/projects/` (same template as `references/projects/README.md`) and, for
rules every project needs, the `marketops-brief` / `marketops-sources` / `marketops-tools`
skills. The first pass from the existing `marketops-session` analysis is already in those
files; this task fills what that analysis does not have (title counts per market, source
yields for UK, US and Germany).

## What to extract (per line, the files in references/projects/)

1. **Job titles** that Bruce graded A or B, in the market's language, with the grade and how
   often (counts only). Also titles he graded C, D or X when he had to correct himself.
2. **Sources** he pooled or read: the register / club finder / directory / page type, the
   country, how many organisations it gave, and how many of them ended with A/B
   (per source family: own site, federation / association, school, council, other).
3. **Methods** that changed his yield: the step, before / after counts.
4. **Corrections**: every time the user redefined a grade, scope or rule - the old rule, the
   new rule, the reason (paraphrased).
5. **Dead ends**: sources or methods he dropped, and why.

## How (single pass, no re-reading)

- One streaming pass over the corpus builds an index: for every message, project family,
  session, time, role, and the line numbers of tool outputs that are tables.
- Count from the index; read only the lines the index points to.
- A "record" is one account row of a result table, never a physical line of a transcript:
  CSVs arrive inside code patches with escaped newlines - split them into real rows first,
  and de-duplicate tool outputs repeated across messages (SELECTION.md 7.5).
- Hard time limit 60 minutes; report partial results with what is missing.

## Output

Edits to the files in `plugins/research-tools/marketops-research-assistant/references/projects/`,
in their template's sections, with the source file for every number. Allowed: counts, batch numbers, job titles, source and page-type names, rule texts.
**Never**: names of the researched organisations, person names, email addresses, phone numbers, account ids.
Check the file with a scan for `@`, digits runs of 6+, and capitalised name pairs before
committing; open it as a PR against `main`.

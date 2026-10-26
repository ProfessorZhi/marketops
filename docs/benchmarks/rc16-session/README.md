# rc16 session extract

The second half of the round-2 benchmark session, from the point where plugin
`0.5.11-rc16` was installed to the end of the run, published for plugin
analysis.

This is **not** the raw transcript. It is a redacted extract of it, produced
because the source file is 65 MB of JSONL in which nearly every tool result
carries third-party material — organisation names, staff names, email
addresses, phone numbers and verbatim page text — and this repository is
public.

## What the extract keeps

Enough to reconstruct the plugin's behaviour line by line:

- every tool call, in order, with its tool name;
- every tool result's shape: `status` values, reason codes
  (`NAME_NOT_FOUND_ON_SITE`, `ROUTES_NOT_ADMITTED`, `SITE_UNREACHABLE`,
  `DUPLICATE_OPERATION`, `UNSAFE_CELL_CHARACTER`, …), counts, error strings,
  and `plugin_advice` / `next_method` hints verbatim;
- the agent's own reasoning and its narration of operator decisions;
- the operator's own messages, including the round-2 task specification.

## What it drops, and why

| Dropped | Rule |
| --- | --- |
| Organisation, person, contact and account names | Blanked by JSON key, then by a 1 457-term dictionary built from the benchmark inputs, then by an explicit list of fragments only visible in page chrome |
| Email addresses, URLs, phone numbers | Regex substitution to `<email>` / `<url>` / `<phone>` (a run of digits is only treated as a phone number if it holds ≥ 9 digits, so dates and hashes survive) |
| Credential-shaped strings | `sk-…`, `ghp_…`, `github_pat_…`, `AKIA…`, `Bearer …`, `api_key=` / `password=` → `<redacted-credential>` |
| Harvested page text (`snippets`) | Replaced by `<snippets:N chars of page text, dropped>`; the snippet count and total length survive, the words do not |
| Per-account identifiers (`CH-…-NNN`) | `<account-id>`. Per-account detail stays on the authoring machine; only the benchmark result documents are in Git |
| `thinking` blocks | Not exported at all |

Tool parameters that carry identity are blanked **by key rather than
dropped**, so the argument structure of each call survives — that structure is
part of what is being reviewed.

## Regenerating it

```bash
python3 -I extract.py   <session>.jsonl '0.5.11-rc16' out.md
python3 -I namepass.py  out.md out.pass2.md     # needs the benchmark inputs locally
# third pass: an explicit fragment list, kept off-Git because it enumerates
# exactly the strings this directory exists to withhold
```

`namepass.py` reads `task_input.csv` and `baseline_rows.csv` from a local
benchmark directory; those files are not in this repository, so the second pass
only reproduces where the benchmark inputs are present.

## Limits

- Redaction is mechanical. It removes names that appear in the benchmark inputs
  or in the explicit fragment list; a person named only once, in prose, could
  survive. Nothing in this file should be treated as de-identified data about
  any real organisation or person.
- Role vocabulary (`Leiter Infrastruktur`, `Platzwart`, `Site Manager`, …) is
  deliberately kept: it is the material the grading analysis turns on, and it
  identifies a job title rather than a person.

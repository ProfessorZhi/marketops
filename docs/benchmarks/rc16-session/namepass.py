#!/usr/bin/env python3
"""Second pass: blank the benchmark's own account and person names.

The first pass redacts by JSON key and by pattern.  Names that arrive inside
free text or CSV-shaped tool output survive that, so this pass builds the term
list from the two benchmark inputs (which enumerate exactly the organisations
and people the run touched) and replaces longest-first.
"""
import csv, re, sys
from pathlib import Path

BENCH = Path("/data/home/vince/artifacts/marketops/benchmarks")
terms = set()

def add(s):
    s = (s or "").strip()
    if len(s) >= 4:
        terms.add(s)

for task, name_col, url_col, city_col in (
        ("uk-pitchfinder", "organization", "official_url", None),
        ("swiss-football", "organization", "official_url", "city")):
    p = BENCH / task / "task_input.csv"
    for row in csv.DictReader(p.open(encoding="utf-8-sig")):
        add(row.get(name_col)); add(row.get("account_id"))
        host = re.sub(r"^https?://(www\.)?", "", row.get(url_col, "")).split("/")[0]
        add(host); add(host.split(".")[0] if host else "")
        if city_col: add(row.get(city_col))
    b = BENCH / task / "baseline_rows.csv"
    for row in csv.DictReader(b.open(encoding="utf-8-sig")):
        for col in ("Contact Name", "contact_name", "organization", "Account Name"):
            add(row.get(col))

# longest first so "FC Lungern" is not eaten by "Lungern" before it is matched
ordered = sorted({t for t in terms if t and not t.isdigit()}, key=len, reverse=True)
pat = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(t) for t in ordered) + r")(?![A-Za-z0-9])",
                 re.IGNORECASE)

src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
out, n = pat.subn("<account-or-person>", text)
open(dst, "w", encoding="utf-8").write(out)
print("terms", len(ordered), "replacements", n)

#!/usr/bin/env python3
"""Hard checks on a redacted extract, plus a list of what still looks like a name.

Exit code is non-zero when a hard pattern survives, so this can gate a push.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import Counter

OUTDIR = pathlib.Path("/data/home/vince/artifacts/marketops/session-extracts-060")

HARD = {
    "email": re.compile(r"\b[A-Za-z0-9._%+\-]{2,}@(?!(?:org\d+|«host»)[. ])"
                        r"[A-Za-z0-9\-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?<![\w/])(?:\+44|0)(?:\d[\s\-()]{0,2}){8,11}\d(?![\w/])"),
    "postcode": re.compile(r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}\b"),
    "secret": re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{10,}|ghp_[A-Za-z0-9]{20,}|"
                         r"github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
                         r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{5,})"),
    "honorific": re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+(?!P\d+)[A-Z][\w'\-]+"),
    "real_url": re.compile(r"https?://(?!«host»|org\d+\.example)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
}

BIGRAM = re.compile(r"\b([A-Z][a-z]{2,})\s+([A-Z][a-z]{2,})\b")
STOP = set("""
The This That There Then These Those When Where Which While With What Your You Our His Her Their
If In On At By For And But Not Now Only Also Both Each Every Some Most More Less Very Just
Server OS Claude Code GitHub Japan Tokyo Beijing Swiss Swiss Bruce MarketOps DeepSeek TinyFish
Serper SerpApi Jev Playwright Docker Linux Windows macOS Python Bash JSON CSV XLSX PDF HTML
Google Chrome Safari Firefox WordPress Wix Squarespace Monday Tuesday Wednesday Thursday Friday
Saturday Sunday January February March April May June July August September October November
December Foundation Stage Year Site Team School Club Bursar Facilities Groundsman Ground
Information Contact Service Services Search Crawl Grade Export Project Browse Fetch Judge
Account Accounts Email Emails Phone Person People Name Names Role Roles Grade Grades Quote Quotes
Page Pages Project Projects Plugin Plugins Tool Tools Agent Agents Run Runs Item Items Candidate
Swiss UK England Scotland Ireland Wales Canton
""".split())


def main() -> int:
    body = (OUTDIR / "body.md").read_text(encoding="utf-8")
    rmap = json.loads((OUTDIR / "redaction-map.json").read_text(encoding="utf-8"))

    bad = 0
    for label, rx in HARD.items():
        hits = rx.findall(body)
        print("%-9s %d %s" % (label, len(hits), sorted(set(hits))[:6]))
        bad += len(hits)

    for kind in ("person", "org"):
        allow = {"Green"} if kind == "person" else set()   # the colour, not the surname
        leaks = []
        for k in rmap[kind]:
            if k in allow:
                continue
            if len(k) > 3 and re.search(r"\b" + re.escape(k) + r"\b", body, re.I):
                leaks.append(k)
        leaked = leaks
        print("%-9s leaked %d %s" % (kind, len(leaked), leaked[:6]))
        bad += len(leaked)

    bigrams = Counter()
    for m in BIGRAM.finditer(body):
        a, b = m.group(1), m.group(2)
        if a in STOP or b in STOP:
            continue
        bigrams["%s %s" % (a, b)] += 1
    print("\ncapitalised bigrams that survive (review this list): %d" % len(bigrams))
    for bg, n in bigrams.most_common(60):
        print("  %3d  %s" % (n, bg))
    (OUTDIR / "residual-bigrams.txt").write_text(
        "\n".join("%d\t%s" % (n, bg) for bg, n in bigrams.most_common()),
        encoding="utf-8")
    print("\nHARD findings:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

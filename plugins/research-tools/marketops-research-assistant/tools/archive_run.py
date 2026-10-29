"""Archive one test run into a private run repository, redacted - the step every benchmark run
ends with (references/benchmarks.md, "Archive").

    python tools/archive_run.py <project folder> <run repository> <run name> [--doc notes.md]

Copies the run's standard files (workbook, routes, contacts, organisations, verdicts, venues,
sources, tasks, calls, costs, journal, trace, crawl, playbook, brief, RETRO) into
<run repository>/runs/<run name>/, never the raw pages or per-organisation stores. Every email
address (with its "name @ host . fr" and "name(at)host" forms) becomes <email> and every
telephone number <phone>, in text files and inside the workbook; names stay. It then scans what
it wrote: one address, phone or secret left and it removes the folder and exits 1 - nothing
half-redacted is ever left to commit. The agent then commits on a branch, pushes, opens the pull
request and merges it (the run repository is private; the redacted copy is what it holds).
"""
from __future__ import annotations

import argparse
import io
import re
import shutil
import sys
import zipfile
from pathlib import Path

KEEP = ("*.xlsx", "*-routes.csv", "contacts.csv", "orgs.csv", "verdicts.csv", "venues.csv", "sources.csv",
        "source_pool.csv", "tasks.csv", "calls.jsonl", "costs.jsonl", "journal.jsonl", "trace.jsonl", "crawl.csv",
        "playbook.json", "brief.json", "RETRO.md", "phones.csv", "unattributed.csv", "made.csv", "bad_urls.jsonl",
        "pages.csv")
TEXT = {".csv", ".jsonl", ".json", ".md", ".txt", ".xml"}

EMAIL = re.compile(r"[\w.+-]+\s?(?:@|\(at\)|\[at\]|\s(?:at|arobase)\s)\s?[\w-]+(?:\s?\.\s?[\w-]+)*\s?\.\s?"
                   r"(?-i:[a-z]{2,10}(?![a-z])|[A-Z]{2,10}(?![A-Z]))", re.I)   # an ending glued to the next word ("comCORRESPONDANT")
PHONE = re.compile(r"(?<![\w/])(?:\+|00)\d{1,3}[\s.-]?\(?0?\)?(?:[\s.-]?\d){8,11}(?!\d)"
                   r"|(?<![\w/.])0\d(?:[\s.-]?\d{2}){4}(?![\d])"
                   # service numbers grouped 0 8XX XX XX XX or 0800 XXX XXX (079: 64 of them passed)
                   r"|(?<![\w/.])0[\s.-]?[1-9]\d{2}(?:[\s.-]?\d{2}){3}(?![\d])"
                   r"|(?<![\w/.])0[\s.-]?[1-9]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}(?![\d])")
SECRET = re.compile(r"(?i)\b(?:sk-[a-z0-9]{16,}|ghp_[a-z0-9]{20,}|api[_-]?key\s*[:=]\s*\S{12,}|bearer\s+[a-z0-9._-]{20,}"
                    r"|password\s*[:=]\s*\S+)")


def redact(text: str) -> tuple[str, int, int]:
    # a reader's Markdown escapes ("name\@club\.fr") hide an address from any pattern
    text = re.sub(r"\\([@.\-_+])", r"\1", text)
    text, emails = EMAIL.subn("<email>", text)
    text, phones = PHONE.subn("<phone>", text)
    return text, emails, phones


# the check is written apart from the redaction: a check built from the same patterns cannot see
# what the patterns miss (079: the tool reported 0 left while 65 numbers were still there)
CHECK_EMAIL = re.compile(r"[^\s<>\"',;:()]+\s?@\s?[^\s<>\"',;:()]+\.[a-z]{2,}", re.I)
CHECK_PHONE = re.compile(r"(?<!\d)(?:\+33|0033|0)(?:[ .\-]?\d){9}(?!\d)")


def left_over(text: str) -> list[str]:
    text = re.sub(r"\\([@.\-_+])", r"\1", text.replace("<email>", " ").replace("<phone>", " "))
    return [m.group(0) for rx in (CHECK_EMAIL, CHECK_PHONE, SECRET) for m in rx.finditer(text)][:5]


def archive(project: Path, repo: Path, name: str, doc: Path | None = None) -> dict:
    if not project.is_dir():
        raise SystemExit(f"no project folder at {project}")
    out = repo / "runs" / name
    if out.exists():
        raise SystemExit(f"{out} exists already: pick another run name")
    out.mkdir(parents=True)
    counts = {"files": 0, "emails": 0, "phones": 0}
    files = sorted({f for pat in KEEP for f in project.glob(pat) if f.is_file()})
    try:
        for f in files:
            target = out / f.name
            if f.suffix == ".xlsx":
                buf = io.BytesIO()
                with zipfile.ZipFile(f) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
                    for item in zin.infolist():
                        data = zin.read(item.filename)
                        if item.filename.endswith(".xml"):
                            text, e, p = redact(data.decode("utf-8", "replace"))
                            counts["emails"] += e
                            counts["phones"] += p
                            data = text.encode("utf-8")
                        zout.writestr(item, data)
                target.write_bytes(buf.getvalue())
            elif f.suffix in TEXT:
                text, e, p = redact(f.read_text(encoding="utf-8", errors="replace"))
                counts["emails"] += e
                counts["phones"] += p
                target.write_text(text, encoding="utf-8")
            else:
                continue
            counts["files"] += 1
        if doc:
            text, e, p = redact(doc.read_text(encoding="utf-8"))
            (repo / "docs" / "runs").mkdir(parents=True, exist_ok=True)
            (repo / "docs" / "runs" / f"{name}.md").write_text(text, encoding="utf-8")
        # the check: nothing written may still carry an address, a number or a secret
        problems = {}
        for f in out.iterdir():
            texts = []
            if f.suffix == ".xlsx":
                with zipfile.ZipFile(f) as z:
                    texts = [z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".xml")]
            else:
                texts = [f.read_text(encoding="utf-8", errors="replace")]
            hits = [h for t in texts for h in left_over(t)]
            if hits:
                problems[f.name] = hits[:3]
        if problems:
            raise RuntimeError(f"left after redaction: {problems}")
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise
    (out / "README.md").write_text(
        f"# {name}\n\nRedacted copy of a MarketOps test run: {counts['emails']} addresses -> <email>, "
        f"{counts['phones']} telephone numbers -> <phone>; names kept. Raw pages and per-organisation stores are "
        "not archived. The run's own account is RETRO.md; the process notes are docs/runs/"
        f"{name}.md when the lead wrote them.\n", encoding="utf-8")
    return {"folder": str(out), **counts}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", type=Path)
    ap.add_argument("repo", type=Path)
    ap.add_argument("name")
    ap.add_argument("--doc", type=Path, help="the lead's process notes, archived as docs/runs/<name>.md")
    a = ap.parse_args()
    try:
        got = archive(a.project.expanduser(), a.repo.expanduser(), a.name, a.doc)
    except RuntimeError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(got)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

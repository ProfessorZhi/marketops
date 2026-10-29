"""Archive one test run into a private run repository, redacted - the step every benchmark run
ends with (references/benchmarks.md, "Archive").

    python tools/archive_run.py <project folder> <run repository> <run name> [--doc notes.md]

Copies the run's standard files (workbook, routes, contacts, organisations, verdicts, venues,
sources, tasks, calls, costs, journal, trace, crawl, playbook, brief, RETRO) into
<run repository>/runs/<run name>/, never the raw pages or per-organisation stores. Every email
address (with its "name @ host . fr" and "name(at)host" forms) becomes <email> and every
telephone number <phone>, social links <social> and @handles <handle>, in text files and inside
the workbook; names stay. A secret anywhere stops the archive before anything is written. It then scans what
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

# What paris-all (0.8.4) found the patterns missing, round after round, all fixed here:
#   endings in any case ("ldb13.Com", "x.Fr"), a doubled dot ("noos..fr"), an ending glued to the
#   next word ("orange.frVICE", "msn.comCORRESPONDANT"), endings up to 24 letters; numbers glued to
#   the word before ("Renseignements01 43 ...", "tél.01 ...") or after ("... 10Adresse",
#   "... 10www."), eleven digits written together, +country numbers of 8 to 15 digits; an address
#   cut before its ending ("jean.dupont@clu"); social profile links and @handles.
EMAIL = re.compile(r"[\w.+-]+\s?(?:@|\(at\)|\[at\]|\s(?:at|arobase)\s)\s?[\w-]+(?:\s?\.+\s?[\w-]+)*\s?\.+\s?"
                   r"(?-i:[a-z]{2,24}(?![a-z])|[A-Z]{2,24}(?![A-Z])|[A-Z][a-z]{1,23}(?![a-z]))", re.I)
FRAGMENT = re.compile(r"[\w.+-]+@[\w.-]+")   # whatever is left with an @ in it: a cut address
_END = r"(?!\d|[a-z]+\d)"   # not inside a longer number or an identifier ("10www." ends a number)
PHONE = re.compile(r"(?<![\d/])(?:\+|00)\d{1,3}[\s.-]?\(?0?\)?(?:[\s.-]?\d){8,15}" + _END +
                   r"|(?<!\d)0[1-9](?:[\s.-]?\d{2}){4}" + _END +
                   # service numbers grouped 0 8XX XX XX XX or 0800 XXX XXX (079: 64 of them passed)
                   r"|(?<!\d)0[\s.-]?[1-9]\d{2}(?:[\s.-]?\d{2}){3}" + _END +
                   r"|(?<!\d)0[\s.-]?[1-9]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}" + _END +
                   r"|(?<!\d)33[1-9]\d{8}(?!\d)")   # +33 written as eleven digits together
SOCIAL = re.compile(r"(?i)\b(?:www\.)?(?:facebook|fb|instagram|twitter|x|linkedin|tiktok|youtube)\.com/[^\s\"'<>|,;]+")
HANDLE = re.compile(r"(?<![\w@./])@[A-Za-z0-9_](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9_])?\b")
SECRET = re.compile(r"(?i)\b(?:sk-[a-z0-9]{16,}|ghp_[a-z0-9]{20,}|api[_-]?key\s*[:=]\s*\S{12,}|bearer\s+[a-z0-9._-]{20,}"
                    r"|password\s*[:=]\s*\S+)")


def redact(text: str) -> tuple[str, int, int]:
    # a reader's Markdown escapes ("name\@club\.fr") hide an address from any pattern
    text = re.sub(r"\\([@.\-_+])", r"\1", text)
    text, emails = EMAIL.subn("<email>", text)
    text, cut = FRAGMENT.subn("<email>", text)
    text = SOCIAL.sub("<social>", text)
    text = HANDLE.sub("<handle>", text)
    text, phones = PHONE.subn("<phone>", text)
    return text, emails + cut, phones


# the check is written apart from the redaction: a check built from the same patterns cannot see
# what the patterns miss (079: the tool reported 0 left while 65 numbers were still there)
CHECK_EMAIL = re.compile(r"[^\s<>\"',;:()]+\s?@\s?[^\s<>\"',;:()]+\.[a-z]{2,}", re.I)
CHECK_AT = re.compile(r"[\w.+-]@[\w-]")
CHECK_PHONE = re.compile(r"(?<!\d)(?:\+33|0033|0)(?:[ .\-]?\d){9}(?!\d)")


def left_over(text: str) -> list[str]:
    text = re.sub(r"\\([@.\-_+])", r"\1", text)
    for mark in ("<email>", "<phone>", "<social>", "<handle>"):
        text = text.replace(mark, " ")
    return [m.group(0) for rx in (CHECK_EMAIL, CHECK_AT, CHECK_PHONE, SECRET) for m in rx.finditer(text)][:5]


def secrets_in(text: str) -> list[str]:
    """Secrets are looked for before anything is redacted: a key whose digits look like a phone
    number would otherwise be hidden from the check, and a run holding a key is never archived."""
    return [m.group(0)[:12] + "..." for m in SECRET.finditer(text)][:3]


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
                            if secrets_in(data.decode("utf-8", "replace")):
                                raise RuntimeError(f"a secret in {f.name}/{item.filename} - remove it from the project first")
                            text, e, p = redact(data.decode("utf-8", "replace"))
                            counts["emails"] += e
                            counts["phones"] += p
                            data = text.encode("utf-8")
                        zout.writestr(item, data)
                target.write_bytes(buf.getvalue())
            elif f.suffix in TEXT:
                raw = f.read_text(encoding="utf-8", errors="replace")
                if secrets_in(raw):
                    raise RuntimeError(f"a secret in {f.name}: {secrets_in(raw)} - remove it from the project first")
                text, e, p = redact(raw)
                counts["emails"] += e
                counts["phones"] += p
                target.write_text(text, encoding="utf-8")
            else:
                continue
            counts["files"] += 1
        if doc:
            if secrets_in(doc.read_text(encoding="utf-8")):
                raise RuntimeError(f"a secret in {doc.name} - remove it first")
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

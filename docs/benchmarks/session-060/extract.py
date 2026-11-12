#!/usr/bin/env python3
"""Turn the second half of a Claude Code session transcript into a redacted Markdown
document that can be published for outside analysis.

Everything that identifies a third party is replaced before anything is written:
person names, organisation names, domains, mailboxes, phone numbers, postcodes and
credential-shaped strings. Replacement is *shape preserving* so the plugin behaviour
stays readable: people become P01…, organisations ORG01… and a host becomes
org07.example, so a reviewer can still see "this row's person is not the person its
quote names" without seeing who anybody is.

Tokyo-local only. The reverse map is written next to this script and is never
committed.
"""
from __future__ import annotations

import csv
import json
import os
import pathlib
import re
import sys
from collections import Counter, OrderedDict

SRC = pathlib.Path(
    "/data/home/vince/.claude/projects/-data-home-vince/cc60b938-2354-4f3b-b5c9-44e1078a910a.jsonl"
)
OUTDIR = pathlib.Path("/data/home/vince/artifacts/marketops/session-extracts-060")
START_LINE = 24603          # first record of the 0.6.0 round
END_LINE = 26172            # last user turn of the round; everything after is this extract's own making
RESULT_CHARS = 1500
ARGS_CHARS = 900
TEXT_CHARS = 4000
THINK_CHARS = 700

# Operator work dirs for the runs in the slice; override with MARKETOPS_PROJECT_DIRS.
PROJECTS = [
    pathlib.Path(p) for p in os.environ.get(
        "MARKETOPS_PROJECT_DIRS",
        "/data/home/vince/MarketOps/swiss-pitchfinder-060-20261026:"
        "/data/home/vince/MarketOps/uk-pitchfinder-060-20261026",
    ).split(":") if p
]
BENCH = [
    pathlib.Path("/data/home/vince/artifacts/marketops/benchmarks/uk-pitchfinder"),
    pathlib.Path("/data/home/vince/artifacts/marketops/benchmarks/swiss-football"),
]

ROLE_WORDS = set("""
info information admin adminoffice administration office enquiries enquiry contact contacts
hello reception receptionist bursar sbm caretaker groundsman grounds ground groundsmen
facilities facility site manager premises premis bookings booking book sport sports hires
hire hireandletting lettings venue venues mail email school team marketing sales operations
finance head secretary club welfare chair treasurer comms media events development business
registrar admissions accounts payroll hr it webmaster postmaster general enquiries
secretariat sekretariat renseignements informations kontakt contact mail
""".split())


def load_world():
    """Collect every name / organisation / host that the run could have touched."""
    names, orgs, hosts = set(), {}, {}
    for proj in PROJECTS:
        orgs_csv = proj / "orgs.csv"
        if orgs_csv.exists():
            with orgs_csv.open(encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    name = (r.get("name") or "").strip()
                    site = (r.get("website") or "").strip()
                    if name:
                        orgs[name] = site
                    h = host_of(site)
                    if h:
                        hosts[h] = name
        con = proj / "contacts.csv"
        if con.exists():
            with con.open(encoding="utf-8") as fh:
                for r in csv.DictReader(fh):
                    add_name(names, r.get("person") or "")
    for bench in BENCH:
        base = bench / "baseline_rows.csv"
        if base.exists():
            with base.open(encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    add_name(names, r.get("contact_name") or "")
                    n = (r.get("organization") or "").strip()
                    if n:
                        orgs.setdefault(n, "")
                    hosts.setdefault(host_of(r.get("official_url") or ""), n)
        tin = bench / "task_input.csv"
        if tin.exists():
            with tin.open(encoding="utf-8-sig") as fh:
                for r in csv.DictReader(fh):
                    n = (r.get("organization") or "").strip()
                    if n:
                        orgs.setdefault(n, r.get("official_url") or "")
                    hosts.setdefault(host_of(r.get("official_url") or ""), n)
    hosts.pop("", None)
    return names, orgs, hosts


def host_of(url: str) -> str:
    m = re.match(r"^[a-z]+://([^/:?#]+)", (url or "").strip(), re.I)
    h = (m.group(1) if m else (url or "").strip()).lower()
    return h[4:] if h.startswith("www.") else h


def add_name(bucket: set, person: str) -> None:
    person = re.sub(r"\((?:[^)]*)\)", " ", person)
    person = re.sub(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s*", "", person, flags=re.I)
    person = person.replace("&", " ").replace("/", " ")
    for chunk in re.split(r"[;,]", person):
        chunk = " ".join(chunk.split())
        chunk = re.sub(r"[^\w'\-\. ]", " ", chunk)
        chunk = " ".join(chunk.split())
        if not chunk:
            continue
        toks = [t for t in chunk.split() if t.strip(".-")]
        if len(toks) >= 2 and all(len(t) > 1 or t.endswith(".") for t in toks):
            bucket.add(chunk)
        elif len(toks) == 1 and len(toks[0]) > 2 and toks[0][:1].isupper():
            bucket.add(toks[0])


class Redactor:
    def __init__(self):
        names, orgs, hosts = load_world()
        self.map = {"person": OrderedDict(), "org": OrderedDict(), "host": OrderedDict()}
        # organise hosts as orgNN.example, keyed by the organisation they belong to
        self.host_pseudo = {}
        for h, org in sorted(hosts.items()):
            if not h:
                continue
            key = org or h
            if key not in self.map["org"]:
                self.map["org"][key] = "ORG%02d" % (len(self.map["org"]) + 1)
            self.host_pseudo[h] = self.map["org"][key].lower() + ".example"
        # names: longest first so "Ann Smith-Jones" wins over "Ann Smith"
        extra = pathlib.Path(__file__).with_name("blocklist.txt")
        blocked_orgs = []
        if extra.exists():
            for line in extra.read_text(encoding="utf-8").splitlines():
                line = line.split("#")[0].strip()
                if not line:
                    continue
                if line.startswith("org:"):
                    blocked_orgs.append(line[4:].strip())
                else:
                    names.add(line)
        for o in blocked_orgs:
            orgs.setdefault(o, "")
        variants = sorted(names, key=len, reverse=True)
        self.name_multi = [n for n in variants if " " in n]
        self.name_single = [n for n in variants if " " not in n]
        # surname -> person id, for mailbox local parts
        # every token of a multi-token name is itself a redaction target, so a
        # bare surname in prose ("Mrs Surname", a staff list) does not survive
        titles = {"mr", "mrs", "ms", "miss", "dr", "prof"}
        for n in self.name_multi:
            for tok in n.split():
                tok = tok.strip(".-")
                if len(tok) > 2 and tok.lower() not in titles:
                    names.add(tok)
        self.name_multi = sorted(names, key=len, reverse=True)
        self.name_multi = [n for n in self.name_multi if " " in n]
        self.name_single = [n for n in sorted(names, key=len, reverse=True) if " " not in n]
        self.multi_re = (
            re.compile(r"\b(?:%s)\b" % "|".join(
                re.escape(n).replace(r"\ ", r"\s+") for n in self.name_multi), re.I)
            if self.name_multi else None
        )
        self.single_re = (
            re.compile(r"\b(?:%s)\b" % "|".join(re.escape(n) for n in self.name_single))
            if self.name_single else None
        )
        self.surname = {}
        for n in variants:
            toks = [t.strip(".-") for t in n.split() if t.strip(".-")]
            if len(toks) >= 2 and len(toks[-1]) > 2:
                self.surname.setdefault(toks[-1].lower(), n)
        self.multi_re = (
            re.compile(r"\b(?:%s)\b" % "|".join(
                re.escape(n).replace(r"\ ", r"\s+") for n in self.name_multi), re.I)
            if self.name_multi else None
        )
        self.single_re = (
            re.compile(r"\b(?:%s)\b" % "|".join(re.escape(n) for n in self.name_single))
            if self.name_single else None
        )
        self.org_re = (
            re.compile(r"\b(?:%s)\b" % "|".join(
                re.escape(o).replace(r"\ ", r"\s+")
                for o in sorted(orgs, key=len, reverse=True)), re.I)
            if orgs else None
        )
        self.host_re = (
            re.compile(r"\b(?:www\.)?(?:%s)\b" % "|".join(
                re.escape(h) for h in sorted(self.host_pseudo, key=len, reverse=True)), re.I)
            if self.host_pseudo else None
        )
        self.person_ids = {}
        self.email_re = re.compile(r"\b([A-Za-z0-9._%+\-]+)@((?:[A-Za-z0-9\-]+\.)+[A-Za-z]{2,})\b")
        self.url_re = re.compile(r"\b(https?://)([^\s/)\"'<>\],]+)([^\s)\"'<>\],]*)")
        self.phone_re = re.compile(
            r"(?<![\w/])(?:\+44\s?\(?0?\)?\s?|\b0)(?:\d[\s\-()]{0,2}){8,11}\d(?![\w/])")
        self.secret_res = [
            re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"),
            re.compile(r"\b(?:ghp|gho|ghs|ghu)_[A-Za-z0-9]{20,}"),
            re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
            re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
            re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{5,}"),
            re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{10,}"),
            re.compile(r"\bxox[bp]-[A-Za-z0-9\-]{10,}"),
            re.compile(r"\b[0-9a-f]{32,}\b"),
        ]
        # names also leak in lowercase slug form (artifact ids, file names)
        self.slug_names = {}
        for n in sorted(names, key=len, reverse=True):
            for tok in n.split():
                tok = tok.strip(".-").lower()
                if len(tok) > 3:
                    self.slug_names.setdefault(tok, n)
        self.slug_re = re.compile(r"\b[a-z0-9]{2,}(?:[._\-][a-z0-9]{2,}){1,4}\b")
        self.lone_local_re = re.compile(r"\b([a-z][a-z0-9._\-]{2,})@(?![A-Za-z0-9])")
        self.local_email_re = re.compile(
            r"\b([a-z][a-z0-9._%+\-]{2,})@([a-z][a-z0-9\-]{1,24})(?![.\w])")
        self.honorific_re = re.compile(r"\b(Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+([A-Z][\w'\-]+)")
        self.postcode_re = re.compile(r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}\b")
        self.plz_re = re.compile(r"\b(?:CH-)?(\d{4})\s+([A-ZÄÖÜ][a-zäöü]{2,})\b")

    def person(self, name: str) -> str:
        key = re.sub(r"\s+", " ", name.strip()).lower()
        if key not in self.person_ids:
            self.person_ids[key] = "P%02d" % (len(self.person_ids) + 1)
            self.map["person"][name.strip()] = self.person_ids[key]
        return self.person_ids[key]

    def _addr(self, m: re.Match) -> str:
        local, dom = m.group(1), m.group(2).lower()
        dom = dom[4:] if dom.startswith("www.") else dom
        host = self.host_pseudo.get(dom, "org??.example")
        low = re.sub(r"[^a-z]", "", local.lower())
        who = None
        for surname, full in self.surname.items():
            if len(surname) > 3 and surname in low:
                who = full
                break
        if who:
            return "%s@%s" % (self.person(who).lower(), host)
        words = [w for w in re.split(r"[._\-+]", local.lower()) if w]
        for w in words:
            if w in ROLE_WORDS:
                return "%s@%s" % (w, host)
        return "x@%s" % host

    def __call__(self, text: str) -> str:
        if not text:
            return ""
        for rx in self.secret_res:
            text = rx.sub("«secret»", text)
        text = self.email_re.sub(self._addr, text)
        text = self.url_re.sub(self._url, text)
        if self.host_re:
            text = self.host_re.sub(lambda m: self.host_pseudo.get(
                m.group(0).lower().removeprefix("www."), "org??.example"), text)
        if self.org_re:
            text = self.org_re.sub(lambda m: self._org(m.group(0)), text)
        if self.multi_re:
            text = self.multi_re.sub(lambda m: self.person(m.group(0)), text)
        if self.single_re:
            text = self.single_re.sub(lambda m: self.person(m.group(0)), text)
        text = self.slug_re.sub(self._slug, text)
        text = self.local_email_re.sub(self._local_email, text)
        text = self.lone_local_re.sub(self._local_email, text)
        text = self.honorific_re.sub(self._honorific, text)
        text = self.phone_re.sub("«phone»", text)
        text = self.postcode_re.sub("«postcode»", text)
        text = self.plz_re.sub("«city»", text)
        return text

    def _local_email(self, m: re.Match) -> str:
        local = m.group(1)
        words = [w for w in re.split(r"[._\-]", local) if w]
        who = next((self.surname[w] for w in words if w in self.surname), None)
        if who:
            return "%s@«host»" % self.person(who).lower()
        for w in words:
            if w in ROLE_WORDS:
                return "%s@«host»" % w
        return "x@«host»"

    def _honorific(self, m: re.Match) -> str:
        who = m.group(2)
        if re.fullmatch(r"P\d+", who):
            return m.group(0)
        return "%s %s" % (m.group(1), self.person(who))

    def _slug(self, m: re.Match) -> str:
        parts = re.split(r"[._\-]", m.group(0))
        hit = next((p for p in parts if p in self.slug_names), None)
        if hit is None:
            return m.group(0)
        return self.person(self.slug_names[hit]).lower()

    def _org(self, name: str) -> str:
        key = name if name in self.map["org"] else name
        for k in self.map["org"]:
            if k.lower() == name.lower():
                return self.map["org"][k]
        self.map["org"][key] = "ORG%02d" % (len(self.map["org"]) + 1)
        return self.map["org"][key]

    def _url(self, m: re.Match) -> str:
        scheme, host, path = m.group(1), m.group(2).lower(), m.group(3) or ""
        host = host[4:] if host.startswith("www.") else host
        pseudo = self.host_pseudo.get(host)
        if pseudo is None and "." in host:
            # unknown host: keep the registrable-domain suffix only
            pseudo = "«host»"
        path = path if len(path) <= 40 else path[:40] + "…"
        return "%s%s%s" % (scheme, pseudo or host, path)


def blocks(path: pathlib.Path, red: Redactor):
    """Yield (kind, timestamp, label, body) tuples in transcript order."""
    with path.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i < START_LINE or (END_LINE is not None and i > END_LINE):
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ty = d.get("type")
            if ty not in ("user", "assistant"):
                continue
            ts = (d.get("timestamp") or "")[11:19]
            msg = d.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    txt = (b.get("text") or "").strip()
                    if not txt:
                        continue
                    if txt.startswith("<system-reminder>") or "<openviking-context>" in txt:
                        txt = re.sub(r"<openviking-context>.*?</openviking-context>", "", txt,
                                     flags=re.S).strip()
                        txt = re.sub(r"<system-reminder>.*?</system-reminder>", "", txt,
                                     flags=re.S).strip()
                        if not txt:
                            continue
                    yield (ty, ts, "text", txt, TEXT_CHARS)
                elif t == "thinking":
                    txt = (b.get("thinking") or "").strip()
                    if txt:
                        yield (ty, ts, "thinking", txt, THINK_CHARS)
                elif t == "tool_use":
                    yield (ty, ts, "call:" + (b.get("name") or "?"),
                           json.dumps(b.get("input") or {}, ensure_ascii=False), ARGS_CHARS)
                elif t == "tool_result":
                    raw = b.get("content")
                    if isinstance(raw, list):
                        raw = "\n".join(x.get("text") or "" for x in raw if isinstance(x, dict))
                    if not isinstance(raw, str):
                        continue
                    if not raw.strip():
                        continue
                    yield (ty, ts, "result", raw, RESULT_CHARS)


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[%d chars omitted]" % (len(text) - limit)


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    red = Redactor()
    out = []
    stats = Counter()
    for ty, ts, label, body, limit in blocks(SRC, red):
        body = red(body)
        clipped = clip(body, limit)
        stats[label if not label.startswith("call:") else "call"] += 1
        stats["chars"] += len(clipped)
        fence = "```"
        if label == "text":
            head = "### %s · %s" % (ts, "user" if ty == "user" else "assistant")
            out.append("%s\n\n%s\n" % (head, clipped))
        elif label == "thinking":
            out.append("### %s · thinking\n\n%s\n" % (ts, "> " + clipped.replace("\n", "\n> ")))
        elif label.startswith("call:"):
            out.append("### %s · → %s\n\n%sjson\n%s\n%s\n" % (ts, label[5:], fence, clipped, fence))
        else:
            out.append("### %s · ← result\n\n%s\n%s\n%s\n" % (ts, fence, clipped, fence))
    (OUTDIR / "body.md").write_text("\n".join(out), encoding="utf-8")
    (OUTDIR / "redaction-map.json").write_text(
        json.dumps(red.map, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"stats": stats, "person": len(red.map["person"]),
                      "org": len(red.map["org"]), "host": len(red.host_pseudo)},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

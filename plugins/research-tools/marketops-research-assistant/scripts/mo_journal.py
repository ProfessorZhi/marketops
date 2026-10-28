"""The project's work journal and its retrospective.

Agents write what they ran into (blockers, what went smoothly, decisions, ideas); the plugin
writes what it notices by itself (a shard restarted, a provider failing far more than usual, a
source yielding under 5%, a spot check under 90%). At export the journal and the run's numbers
become RETRO.md - masked (no addresses, no phone numbers), so it can go to the plugin's
repository as the record the next big change starts from.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

TYPES = ("blocker", "smooth", "decision", "idea", "retro", "auto")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d .()-]{7,}\d")


def mask(text: str) -> str:
    text = _EMAIL.sub(lambda m: m.group(0)[0] + "***@" + m.group(0).split("@")[1], str(text or ""))
    return _PHONE.sub("<phone>", text)


def _path(pdir: Path) -> Path:
    return pdir / "journal.jsonl"


def read(pdir: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in _path(pdir).read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def add(pdir: Path, entries, agent: str = "") -> int:
    """entries: {type, text, evidence?} or a list of them. type: blocker | smooth | decision |
    idea | retro."""
    items = entries if isinstance(entries, list) else [entries]
    n = 0
    with open(_path(pdir), "a", encoding="utf-8") as fh:
        for e in items:
            if not isinstance(e, dict) or not str(e.get("text") or "").strip():
                continue
            kind = str(e.get("type") or "idea").lower()
            fh.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "agent": agent or e.get("agent", ""),
                                 "type": kind if kind in TYPES else "idea", "text": str(e["text"])[:1000],
                                 "evidence": str(e.get("evidence") or "")[:500]}, ensure_ascii=False) + "\n")
            n += 1
    return n


def auto(pdir: Path, key: str, text: str, evidence: str = "", every_s: int = 3600) -> bool:
    """The plugin notes something itself - once per `key` per hour."""
    now = time.time()
    for e in reversed(read(pdir)):
        if e.get("type") == "auto" and e.get("key") == key:
            try:
                if now - time.mktime(time.strptime(e["time"], "%Y-%m-%dT%H:%M:%S")) < every_s:
                    return False
            except (KeyError, ValueError):
                pass
            break
    with open(_path(pdir), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "agent": "plugin", "type": "auto",
                             "key": key, "text": text[:1000], "evidence": evidence[:500]}, ensure_ascii=False) + "\n")
    return True


def failures(pdir: Path, since_s: float | None = None) -> dict:
    """Read failures per provider by kind (mo_health's kinds), from costs.jsonl."""
    import mo_health
    out: dict[str, Counter] = {}
    calls: Counter = Counter()
    cutoff = time.time() - since_s if since_s else None
    try:
        lines = (pdir / "costs.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if cutoff:
            try:
                if time.mktime(time.strptime(r["time"][:19], "%Y-%m-%dT%H:%M:%S")) < cutoff:
                    continue
            except (KeyError, ValueError):
                continue
        p = r.get("provider", "?")
        calls[p] += 1
        if not r.get("ok"):
            out.setdefault(p, Counter())[mo_health.kind_of(r.get("note", ""))] += 1
    return {p: {"calls": calls[p], "failed": sum(c.values()), "by_kind": dict(c.most_common())}
            for p, c in out.items()} | {p: {"calls": n, "failed": 0, "by_kind": {}} for p, n in calls.items() if p not in out}


def watch(pdir: Path) -> None:
    """Called by the run every few minutes: note a provider failing far more than usual."""
    for p, f in failures(pdir, since_s=600).items():
        if f["calls"] >= 30 and f["failed"] / f["calls"] > 0.6:
            top = ", ".join(f"{k} {n}" for k, n in list(f["by_kind"].items())[:3])
            auto(pdir, f"fail:{p}", f"{p}: {f['failed']} of {f['calls']} calls failed in the last 10 minutes ({top})",
                 "costs.jsonl")


def calls_summary(pdir: Path) -> list[str]:
    """The tool-call log (calls.jsonl) in brief: who worked when, which tools, the slowest
    calls and the errors - the chain of the run without its transcripts."""
    rows = []
    try:
        for line in (pdir / "calls.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    if not rows:
        return []
    out = ["", "## The tool calls (calls.jsonl)", "", "| agent | first | last | calls | seconds | tools |", "|---|---|---|---|---|---|"]
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(r.get("agent") or "(unnamed)", []).append(r)
    for a, rs in sorted(by.items(), key=lambda kv: kv[1][0].get("time", "")):
        tools: dict[str, int] = {}
        for r in rs:
            tools[r.get("tool", "?")] = tools.get(r.get("tool", "?"), 0) + 1
        out.append(f"| {a} | {rs[0].get('time', '')[11:16]} | {rs[-1].get('time', '')[11:16]} | {len(rs)} | "
                   f"{round(sum(r.get('seconds') or 0 for r in rs))} | "
                   + ", ".join(f"{t} {n}" for t, n in sorted(tools.items(), key=lambda kv: -kv[1])) + " |")
    slow = sorted(rows, key=lambda r: -(r.get("seconds") or 0))[:5]
    out += ["", "Slowest: " + "; ".join(f"{r.get('tool')} {r.get('seconds')} s ({r.get('agent') or '-'})" for r in slow)]
    errs: dict[str, int] = {}
    for r in rows:
        if not r.get("ok"):
            k = f"{r.get('tool')}: {mask(str(r.get('error') or ''))[:90]}"
            errs[k] = errs.get(k, 0) + 1
    if errs:
        out += ["", "Errors: " + "; ".join(f"{k} (x{n})" for k, n in sorted(errs.items(), key=lambda kv: -kv[1])[:8])]
    return out


def retro(pdir: Path, metrics: dict, stages: dict, extra: dict | None = None) -> Path:
    """RETRO.md: the numbers, where reads failed, and what agents and the plugin noted."""
    entries = read(pdir)
    by_type: dict[str, list] = {}
    for e in entries:
        by_type.setdefault(e.get("type", "idea"), []).append(e)
    fails = failures(pdir)
    lines = [f"# Retrospective: {pdir.name}", "", f"Written {time.strftime('%Y-%m-%d %H:%M')} at export. "
             "Masked: no addresses or phone numbers.", "", "## Numbers", ""]
    for k, v in metrics.items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Work waiting per stage at export", "", "```", json.dumps(stages, ensure_ascii=False, indent=1), "```",
              "", "## Reads and searches", "", "| provider | calls | failed | by kind |", "|---|---|---|---|"]
    for p, f in sorted(fails.items()):
        lines.append(f"| {p} | {f['calls']} | {f['failed']} | "
                     + ", ".join(f"{k} {n}" for k, n in f["by_kind"].items()) + " |")
    lines += calls_summary(pdir)
    for k, v in (extra or {}).items():
        lines += ["", f"## {k}", ""] + [f"- {x}" for x in v]
    titles = {"blocker": "Blockers", "smooth": "What went smoothly", "decision": "Decisions",
              "idea": "Ideas for the plugin", "retro": "The lead's account", "auto": "Noticed by the plugin"}
    for t in ("retro", "blocker", "smooth", "decision", "idea", "auto"):
        if by_type.get(t):
            lines += ["", f"## {titles[t]}", ""]
            for e in by_type[t]:
                who = f" ({e['agent']})" if e.get("agent") else ""
                ev = f" - {mask(e['evidence'])}" if e.get("evidence") else ""
                lines.append(f"- {e['time'][:16]}{who}: {mask(e['text'])}{ev}")
    path = pdir / "RETRO.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

"""What the plugin learns across projects, so it gets better at research the more it runs.

Two things, both keyed by market (the brief's country) and kind of organisation (segment):
- playbook entries agents wrote (pages to read first, searches, roles), with how often they
  were used and how often they led to an A/B. A new project loads the ones that work (or are
  still young); an entry that stops working (a redesigned site, a changed portal) falls out
  after enough misses. Nothing is deleted.
- which kind of dig search finds A/B: role words, inside the site and its PDFs, directories,
  email searches. The dig tries each kind until it has enough tries, then leads with the one
  that works best for that market and kind of organisation.

It only changes what is tried first and what agents are shown. Grades, evidence rules, client
rules and the delivery checks are never touched by it.

Stored next to the library (<library>/knowledge.jsonl, append-only), so machines or teams that
share a library share what it learned.
"""
from __future__ import annotations

import json
import os
import threading
import time

DIG_KINDS = ("terms", "site_pdf", "directories", "email")
EXPLORE = 10        # uses a playbook entry gets before its rate counts
DIG_EXPLORE = 30    # tries every dig kind needs before the order may change
DIG_MARGIN = 0.10   # and the leader must beat the default first kind by this much
DROP_BELOW = 0.05   # an entry with this hit rate after EXPLORE uses is no longer loaded


class _Knowledge:
    def __init__(self):
        self.lock = threading.Lock()
        self.path = None
        self.offset = 0
        self.loaded_at = 0.0
        self.origin = f"{os.getpid()}:{id(self)}:{time.time():.3f}"
        self.dig: dict[tuple, dict] = {}        # (country, segment, kind) -> {tries, hits}
        self.entries: dict[tuple, dict] = {}    # (country, segment) -> merged playbook entry
        self.uses: dict[tuple, dict] = {}       # (country, segment) -> {uses, hits}

    def _file(self):
        import mo_library
        p = mo_library.root() / "knowledge.jsonl"
        if self.path != p:
            self.path, self.offset, self.dig, self.entries, self.uses = p, 0, {}, {}, {}
        return p

    def _catch_up(self, force: bool = False) -> None:
        p = self._file()
        if not force and time.time() - self.loaded_at < 60:
            return
        self.loaded_at = time.time()
        try:
            with open(p, encoding="utf-8") as fh:
                fh.seek(self.offset)
                for line in fh:
                    if not line.endswith("\n"):
                        break
                    self.offset += len(line.encode("utf-8"))
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("o") != self.origin:
                        self._fold(rec)
        except OSError:
            pass

    def _append(self, rec: dict) -> None:
        p = self._file()
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {**rec, "o": self.origin, "t": time.time()}
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fold(rec)

    def _fold(self, r: dict) -> None:
        key = (str(r.get("c", "")).upper(), str(r.get("s", "")).lower())
        if r.get("k") == "dig":
            st = self.dig.setdefault(key + (r.get("q", ""),), {"tries": 0, "hits": 0})
            st["tries"] += 1
            st["hits"] += 1 if r.get("hit") else 0
        elif r.get("k") == "playbook":
            cur = self.entries.setdefault(key, {})
            for f, v in (r.get("e") or {}).items():
                if isinstance(v, list):
                    cur[f] = list(dict.fromkeys([*(cur.get(f) or []), *[str(x) for x in v]]))
                elif f != "updated":
                    cur[f] = str(v)
        elif r.get("k") == "use":
            st = self.uses.setdefault(key, {"uses": 0, "hits": 0})
            st["uses"] += 1
            st["hits"] += 1 if r.get("hit") else 0

    # ------------------------------------------------------------- API
    def record_dig(self, country: str, segment: str, kind: str, hit: bool) -> None:
        if kind in DIG_KINDS:
            with self.lock:
                self._append({"k": "dig", "c": country, "s": segment, "q": kind, "hit": bool(hit)})

    def dig_order(self, country: str, segment: str) -> list[str]:
        """The dig kinds in the order to try them: untested ones first (in the default order),
        then by how often they found an A/B here."""
        with self.lock:
            self._catch_up()
            key = (country.upper(), segment.lower())
            stats = {k: self.dig.get(key + (k,), {"tries": 0, "hits": 0}) for k in DIG_KINDS}
        # change the order only on clear evidence: every kind tried enough, and the best one
        # clearly ahead of the default first - a noisy reorder costs searches (seen in simulation)
        if any(stats[k]["tries"] < DIG_EXPLORE for k in DIG_KINDS):
            return list(DIG_KINDS)
        rate = {k: stats[k]["hits"] / stats[k]["tries"] for k in DIG_KINDS}
        if max(rate.values()) - rate[DIG_KINDS[0]] < DIG_MARGIN:
            return list(DIG_KINDS)
        return sorted(DIG_KINDS, key=lambda k: -rate[k])

    def learn_playbook(self, country: str, segment: str, entry: dict) -> None:
        if segment and any(isinstance(v, list) and v for v in entry.values()):
            with self.lock:
                self._append({"k": "playbook", "c": country, "s": segment,
                              "e": {f: v for f, v in entry.items() if f in ("paths", "queries", "roles", "notes")}})

    def record_use(self, country: str, segment: str, hit: bool) -> None:
        with self.lock:
            self._append({"k": "use", "c": country, "s": segment, "hit": bool(hit)})

    def playbook(self, country: str, segment: str) -> dict:
        """The learned entry for this market and kind of organisation, if it is still worth
        loading: young (under EXPLORE uses) or hitting at least DROP_BELOW of the time."""
        with self.lock:
            self._catch_up()
            key = (country.upper(), segment.lower())
            e, u = self.entries.get(key), self.uses.get(key, {"uses": 0, "hits": 0})
        if not e:
            return {}
        if u["uses"] >= EXPLORE and u["hits"] / u["uses"] < DROP_BELOW:
            return {}
        return {**e, "learned": f"from earlier projects: used {u['uses']} times, led to an A/B {u['hits']} times"}

    def summary(self) -> dict:
        with self.lock:
            self._catch_up(force=True)
            return {"segments_with_playbook": len(self.entries),
                    "dig_stats": {"|".join(k): v for k, v in sorted(self.dig.items())}}


KNOWLEDGE = _Knowledge()


def reset_for_tests() -> None:
    global KNOWLEDGE
    KNOWLEDGE = _Knowledge()


def enabled() -> bool:
    return os.environ.get("MARKETOPS_KNOWLEDGE", "1") != "0"

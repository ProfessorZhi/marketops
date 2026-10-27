"""Site health: how readable each site and page has been, shared by every project on this
machine (it lives in the library), so a dead page is not read three times over and over, and a
site that blocks one reader is read with another.

A failure is a hint, not a verdict. The rules:
- one failure blocks nothing; a site or page is set aside only when the same kind of failure
  comes back hours apart, or two different readers fail on it;
- failures on our side are not the site's: proxy errors, rate limits, budget skips, a reader's
  own crash, and any minute where many different sites fail at once (our network is down);
- a 404 marks that page, not the site; an anti-bot block marks the site for that reader only;
- set aside means "later", never "never": 3 hours, then 1, 3, 7 and 30 days; the first try after
  that is a probe, and one success restores the site at once.

Records are appended to <library>/health.jsonl (one line per outcome, cheap with many workers);
each process folds them into memory and catches up with other processes' lines every minute.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
from collections import deque

import mo_config

BACKOFF_H = (3, 24, 72, 168, 720)      # hours a confirmed failure sets a site or page aside
CONFIRM_GAP_S = 3 * 3600               # the same failure again this much later confirms it
BURST_WINDOW_S, BURST_FAILS, BURST_HOSTS = 120, 20, 8   # many sites failing at once: our network

KINDS = (
    ("ours", re.compile(r"proxy|skipped:|HTTP 429|rate.?limit|Unexpected error in _crawl_web|"
                        r"refused the key|out of credit|402", re.I)),
    ("gone", re.compile(r"HTTP 404|HTTP 410|page_not_found|not found", re.I)),
    ("down", re.compile(r"target_unreachable|invalid_url|Name or service|getaddrinfo|no_dns|"
                        r"nodename|DNS|ConnectError|connection refused", re.I)),
    ("blocked", re.compile(r"bot_blocked|cloudflare|akamai|challenge|captcha|HTTP 403|login_required|"
                           r"access denied", re.I)),
    ("transient", re.compile(r"time.?out|HTTP 5\d\d|ConnectionReset|RemoteProtocolError|target_http_error", re.I)),
    ("empty", re.compile(r"no text|empty_content|minimal_text|no result", re.I)),
)


def kind_of(error: str) -> str:
    for name, rx in KINDS:
        if rx.search(error or ""):
            return name
    return "other"


def host_of(url: str) -> str:
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def page_of(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return host_of(url) + (p.path.rstrip("/") or "/") + (("?" + p.query) if p.query else "")


class _Health:
    def __init__(self):
        self.lock = threading.Lock()
        self.path = None
        self.offset = 0
        self.loaded_at = 0.0
        self.recent: deque = deque()            # (time, host, ok) of this process, for the burst check
        self.hosts: dict[str, dict] = {}        # host -> state
        self.pages: dict[str, dict] = {}        # page -> state
        self.origin = f"{os.getpid()}:{id(self)}:{time.time():.3f}"   # this process's lines

    # ------------------------------------------------------------- storage
    def _file(self):
        import mo_library
        p = mo_library.root() / "health.jsonl"
        if self.path != p:
            self.path, self.offset, self.hosts, self.pages = p, 0, {}, {}
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
                    if rec.get("o") != self.origin:   # our own lines are folded when written
                        self._fold(rec)
        except OSError:
            pass

    def _append(self, rec: dict) -> None:
        p = self._file()
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {**rec, "o": self.origin}
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fold(rec)   # folded now; skipped when the file is read back

    # ------------------------------------------------------------- state
    @staticmethod
    def _blank() -> dict:
        return {"ok_at": 0.0, "fails": [], "level": 0, "until": 0.0, "blocked": {}, "pages": 0, "with_contacts": 0}

    def _fold(self, r: dict) -> None:
        t, host, page, reader, k = r.get("t", 0.0), r.get("h", ""), r.get("p", ""), r.get("r", ""), r.get("k", "")
        if k == "yield":
            st = self.hosts.setdefault(host, self._blank())
            st["pages"] += 1
            st["with_contacts"] += 1 if r.get("n") else 0
            return
        for key, table in ((host, self.hosts), (page, self.pages)):
            st = table.setdefault(key, self._blank())
            if k == "ok":
                st.update(ok_at=t, fails=[], level=0, until=0.0)
                st["blocked"].pop(reader, None)
                continue
            if table is self.hosts and k not in ("down", "transient", "blocked"):
                continue          # a missing or empty page says nothing about the site
            if table is self.pages and k not in ("gone", "empty", "down"):
                continue
            if k == "blocked":
                st["blocked"][reader] = st["blocked"].get(reader, 0) + 1
                continue
            st["fails"] = (st["fails"] + [(t, reader, k)])[-12:]
            if self._confirmed(st["fails"], t, k) and t >= st["until"]:
                st["level"] = min(st["level"] + 1, len(BACKOFF_H))
                st["until"] = t + BACKOFF_H[st["level"] - 1] * 3600
                st["fails"] = []

    @staticmethod
    def _confirmed(fails: list, now: float, k: str) -> bool:
        same = [(t, r) for t, r, kk in fails if kk == k]
        if len(same) < 2:
            return False
        times = [t for t, _ in same]
        readers = {r for _, r in same}
        if k == "transient":   # slow or erroring sites: only when it keeps happening, hours apart
            return len(same) >= 3 and max(times) - min(times) >= CONFIRM_GAP_S
        return max(times) - min(times) >= CONFIRM_GAP_S or len(readers) >= 2

    def _our_network_down(self, now: float) -> bool:
        while self.recent and now - self.recent[0][0] > BURST_WINDOW_S:
            self.recent.popleft()
        fails = [(t, h) for t, h, ok in self.recent if not ok]
        oks = sum(1 for _, _, ok in self.recent if ok)
        return len(fails) >= BURST_FAILS and len({h for _, h in fails}) >= BURST_HOSTS and oks < len(fails) * 0.2

    # ------------------------------------------------------------- API
    def record(self, url: str, reader: str, ok: bool, error: str = "") -> str:
        """Note one read. Returns the kind counted ("ok", "gone", ..., or "ignored")."""
        now = time.time()
        host, page = host_of(url), page_of(url)
        if not host:
            return "ignored"
        with self.lock:
            self._catch_up()
            k = "ok" if ok else kind_of(error)
            if k not in ("ours",):
                self.recent.append((now, host, ok))
            if k in ("ours", "other") or (not ok and self._our_network_down(now)):
                return "ignored"
            self._append({"t": now, "h": host, "p": page, "r": reader, "k": k})
            return k

    def record_yield(self, url: str, contacts: int) -> None:
        host = host_of(url)
        if host:
            with self.lock:
                self._append({"t": time.time(), "h": host, "k": "yield", "n": int(contacts or 0)})

    def skip(self, url: str, reader: str) -> str:
        """Why this reader should not read this URL now ("" = read it)."""
        now = time.time()
        host, page = host_of(url), page_of(url)
        with self.lock:
            self._catch_up()
            for what, st in (("page", self.pages.get(page)), ("site", self.hosts.get(host))):
                if st and st["until"] > now:
                    days = (st["until"] - now) / 86400
                    return f"{what} set aside after repeated failures, retried in {days:.1f} days"
            st = self.hosts.get(host)
            if st and st["blocked"].get(reader, 0) >= 2 and st["ok_at"] < now - CONFIRM_GAP_S:
                return f"site blocks {reader} (blocked {st['blocked'][reader]} times)"
        return ""

    def status(self, url: str) -> dict:
        """A site's health for people and agents: reach 0-100, yield share, why."""
        now = time.time()
        host = host_of(url)
        with self.lock:
            self._catch_up()
            st = self.hosts.get(host)
        if not st:
            return {"site": host, "reach": None, "note": "not read yet"}
        reach = 100
        if st["level"]:
            reach = max(5, int(100 / (2 ** st["level"])))
        elif st["fails"]:
            reach = max(50, 100 - 15 * len(st["fails"]))
        out = {"site": host, "reach": reach,
               "yield": round(st["with_contacts"] / st["pages"], 2) if st["pages"] else None,
               "last_ok": time.strftime("%Y-%m-%d", time.localtime(st["ok_at"])) if st["ok_at"] else "",
               "blocks": sorted(r for r, n in st["blocked"].items() if n >= 2)}
        if st["until"] > now:
            out["set_aside_until"] = time.strftime("%Y-%m-%d %H:%M", time.localtime(st["until"]))
        return out


HEALTH = _Health()


def reset_for_tests() -> None:
    global HEALTH
    HEALTH = _Health()


def enabled() -> bool:
    return os.environ.get("MARKETOPS_HEALTH", "1") != "0" and bool(mo_config.home())

"""The upstream services, each a plain function.

Search (independent indexes, run together): Serper, SerpApi, DeepSeek, TinyFish.
Read a page (run together, each sees the web differently): Crawl4AI (local browser,
keeps the rendered HTML), TinyFish fetch, DeepSeek fetch; plus a direct download for
data files (CSV/JSON/XLSX/PDF exports of open-data portals).
TinyFish Agent drives a real browser for pages that need clicks; it runs async.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import httpx

import mo_budget
import mo_config

SEARCH_ENGINES = ("serper", "serpapi", "deepseek", "tinyfish")
READERS = ("crawl4ai", "tinyfish", "deepseek")
# Share of calls each provider takes part in (brief.providers overrides). The rest is held
# back and called only when the chosen ones come back weak - never at the cost of results.
# DeepSeek, TinyFish search/fetch, Crawl4AI and Jev are cheap and plentiful: always on.
# Serper and SerpApi cost per search: a share. The paid TinyFish Agent is opt-in only.
DEFAULT_WEIGHTS = {"search": {"deepseek": 1.0, "tinyfish": 1.0, "serper": 0.5, "serpapi": 0.3},
                   "fetch": {"crawl4ai": 1.0, "tinyfish": 1.0, "deepseek": 1.0}}


def pick(key: str, available: list[str], weights: dict | None, always: tuple = ()) -> tuple[list, list]:
    """Split providers into (call now, hold back) by weight, the same way for the same key."""
    import hashlib
    w = weights or {}
    now, later = [], []
    for p in available:
        share = float(w.get(p, 1.0))
        roll = int(hashlib.sha1(f"{p}:{key}".encode()).hexdigest(), 16) % 1000
        (now if p in always or roll < share * 1000 else later).append(p)
    if not now and later:
        later.sort(key=lambda p: -float(w.get(p, 1.0)))
        now.append(later.pop(0))
    return now, later
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


class ProviderError(Exception):
    pass


TRANSIENT = re.compile(r"HTTP (429|5\d\d)|timed? ?out|Timeout|ConnectError|ConnectionReset|RemoteProtocolError", re.I)
TRANSIENT_FAST = re.compile(r"HTTP (429|5\d\d)|ConnectError|ConnectionReset|RemoteProtocolError", re.I)


def _again(call, tries: int = 2, wait: float = 3.0, transient=TRANSIENT):
    """A rate limit, a server error or a dropped connection is tried once more after a pause -
    a site that failed once is not a dead site."""
    import time
    for n in range(tries):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            if n == tries - 1 or not transient.search(f"{type(exc).__name__} {exc}"):
                raise
            time.sleep(wait * (n + 1))


class _Rate:
    """Per-endpoint pace. Starts from the provider's documented limit where there is one and
    adopts the limit a 429 reports (TinyFish puts it in error.details)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.per_min: dict[str, float] = {"tinyfish:search": 30}   # docs.tinyfish.ai error codes
        self.next_at: dict[str, float] = {}

    def wait(self, key: str) -> None:
        import time
        rate = self.per_min.get(key)
        if not rate:
            return
        with self.lock:
            now = time.time()
            at = max(now, self.next_at.get(key, 0.0))
            self.next_at[key] = at + 60.0 / rate
        if at > now:
            time.sleep(at - now)

    def learn(self, key: str, response) -> None:
        try:
            d = ((response.json() or {}).get("error") or {}).get("details") or {}
            if d.get("limit") and d.get("window_seconds"):
                self.per_min[key] = float(d["limit"]) * 60.0 / float(d["window_seconds"])
        except Exception:  # noqa: BLE001
            pass


RATE = _Rate()


def _check_money(provider: str, response: httpx.Response, kind: str = "") -> None:
    if response.status_code == 429:
        RATE.learn(f"{provider}:{kind}", response)
    if response.status_code in (402,) or (response.status_code == 429 and provider != "crawl4ai"):
        if response.status_code == 402:
            mo_budget.mark_exhausted(provider, "provider refused for money (HTTP 402)")
        raise ProviderError(f"HTTP {response.status_code}")
    if response.status_code >= 400:
        raise ProviderError(f"HTTP {response.status_code}")


def public_url(url: str) -> str:
    """http(s) to a public host only (no local network, no credentials in the URL)."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
        raise ProviderError("not a public http(s) URL")
    host = parts.hostname.strip().rstrip(".")
    if " " in host:
        raise ProviderError("host has a space in it")
    if host in ("localhost",) or host.endswith((".local", ".internal", ".localhost")):
        raise ProviderError("host is on a private network")
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        infos = []  # behind a proxy local DNS may not answer; the readers will say if it is dead
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ProviderError("host is on a private network")
    return urllib.parse.urlunsplit((parts.scheme, host.lower() + (f":{parts.port}" if parts.port else ""),
                                    parts.path or "/", parts.query, ""))


# ---------------------------------------------------------------- search
def _serper(q, country, lang, n, maps, purpose=""):
    body = {"q": q, "num": n}
    if country:
        body["gl"] = country.lower()
    if lang:
        body["hl"] = lang.lower()
    r = httpx.post("https://google.serper.dev/" + ("places" if maps else "search"), json=body,
                   headers={"X-API-KEY": mo_config.key("serper")}, timeout=30)
    _check_money("serper", r)
    d = r.json()
    if maps:
        return [{"title": p.get("title"), "url": p.get("website") or "", "address": p.get("address"),
                 "phone": p.get("phoneNumber"), "snippet": p.get("category") or ""} for p in d.get("places", [])]
    return [{"title": o.get("title"), "url": o.get("link"), "snippet": o.get("snippet")} for o in d.get("organic", [])]


def _serpapi(q, country, lang, n, maps, purpose=""):
    params = {"api_key": mo_config.key("serpapi"), "q": q,
              "engine": "google_maps" if maps else "google"}
    if not maps:
        params["num"] = n
    if country:
        params["gl"] = country.lower()
    if lang:
        params["hl"] = lang.lower()
    r = httpx.get("https://serpapi.com/search.json", params=params, timeout=60)
    _check_money("serpapi", r)
    d = r.json()
    if d.get("error") and "run out" in str(d["error"]).lower():
        mo_budget.mark_exhausted("serpapi", "searches used up")
        raise ProviderError("searches used up")
    if maps:
        return [{"title": p.get("title"), "url": p.get("website") or "", "address": p.get("address"),
                 "phone": p.get("phone"), "snippet": p.get("type") or ""} for p in d.get("local_results", [])]
    return [{"title": o.get("title"), "url": o.get("link"), "snippet": o.get("snippet")}
            for o in d.get("organic_results", [])]


def _deepseek(q, country, lang, n, maps, purpose=""):
    """api-docs.deepseek.com/guides/search: query <= 128 chars with operators (site:, "...",
    lang:), count 1-50, ori_question = the intent; results carry a long summary, a rerank score
    (>= 1.5 good) and an authority tier."""
    if maps:
        return []
    if lang and "lang:" not in q and len(q) + len(lang) + 6 <= 128:
        q = f"{q} lang:{lang.lower()}"
    body = {"query": q[:128], "count": max(1, min(n, 50)), "need_content": False}
    if purpose:
        body["ori_question"] = purpose[:128]
    r = httpx.post("https://api.deepseek.com/v1/tools/search", json=body,
                   headers={"Authorization": "Bearer " + mo_config.key("deepseek")}, timeout=40)
    _check_money("deepseek", r, "search")
    return [{"title": o.get("title"), "url": o.get("url"),
             "snippet": " ".join(x for x in (o.get("snippet"), o.get("summary")) if x)[:900],
             "score": o.get("rerank_score")}
            for o in r.json().get("results", [])]


def _tinyfish(q, country, lang, n, maps, purpose=""):
    """docs.tinyfish.ai/search-api: without location / language it searches as US / English;
    `purpose` (the goal) improves ranking; 30 requests a minute by default."""
    if maps:
        return []
    params = {"query": q}
    if country:
        params["location"] = country.upper()
    if lang:
        params["language"] = lang.lower()
    if purpose:
        params["purpose"] = purpose[:2000]
    RATE.wait("tinyfish:search")
    r = httpx.get("https://api.search.tinyfish.ai", params=params,
                  headers={"X-API-Key": mo_config.key("tinyfish")}, timeout=40)
    _check_money("tinyfish", r, "search")
    return [{"title": o.get("title"), "url": o.get("url"), "snippet": o.get("snippet")}
            for o in r.json().get("results", [])]


_SEARCH = {"serper": _serper, "serpapi": _serpapi, "deepseek": _deepseek, "tinyfish": _tinyfish}


def _norm(url: str) -> str:
    p = urllib.parse.urlsplit(url or "")
    host = (p.hostname or "").removeprefix("www.")
    return host + p.path.rstrip("/") + ("?" + p.query if p.query else "")


SEARCH_GRACE = 5.0   # seconds the slower engines get after the first answer


def search(query: str, engines=None, country: str = "", lang: str = "", n: int = 10,
           maps: bool = False, project_dir=None, weights: dict | None = None) -> dict:
    purpose = ""
    if project_dir is not None:
        import mo_workspace
        loc = mo_workspace.locale(project_dir)
        country, lang, purpose = country or loc["country"], lang or loc["language"], loc["purpose"]
    available = [e for e in (engines or SEARCH_ENGINES) if e in _SEARCH and mo_config.key(e)]
    if engines or weights is None:
        wanted, held = available, []
    else:
        wanted, held = pick(query, available, weights)
    routes, merged = {}, {}

    def one(engine):
        import mo_library
        key = [engine, query, country, lang, n, maps]
        cached = mo_library.get_search(key)
        if cached is not None:   # asked before by any project: free
            return engine, cached, ""
        ok, why = mo_budget.allowed(engine, "search")
        if not ok:
            return engine, None, why
        try:
            rows = _again(lambda: _SEARCH[engine](query, country, lang, n, maps, purpose))
            mo_budget.record(project_dir, engine, "search", True, query[:80])
            mo_library.put_search(key, rows)
            return engine, rows, ""
        except Exception as exc:  # noqa: BLE001 - one engine failing never fails the search
            mo_budget.record(project_dir, engine, "search", False, f"{type(exc).__name__}: {exc}")
            return engine, None, mo_config.scrub(f"{type(exc).__name__}: {exc}")[:160]

    def gather(batch):
        # Every engine at once; once one has answered, the slower ones get `grace` seconds.
        # An engine still running keeps going in the background and its answer is saved in
        # the library, so the next time the same question is asked it is there for free.
        import time
        from concurrent.futures import FIRST_COMPLETED, wait
        futs = [_pool("search-" + e).submit(one, e) for e in batch]
        pending, first = set(futs), None
        while pending:
            left = None if first is None else first + SEARCH_GRACE - time.time()
            if left is not None and left <= 0:
                break
            done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
            for f in done:
                engine, rows, why = f.result()
                routes[engine] = {"results": len(rows)} if rows is not None else {"skipped": why}
                if rows and first is None:
                    first = time.time()
                for rank, row in enumerate(rows or []):
                    key = _norm(row.get("url") or "") or ("place:" + str(row.get("title")))
                    hit = merged.setdefault(key, {**row, "found_by": []})
                    hit["found_by"].append(engine)
                    for f_ in ("snippet", "address", "phone", "url"):
                        if not hit.get(f_) and row.get(f_):
                            hit[f_] = row[f_]
        for f in pending:
            routes.setdefault(batch[futs.index(f)], {"late": "answer saved to the library"})
    gather(wanted)
    if held and len(merged) < 3:   # weak answer: bring in the engines held back
        gather(held)
    for e in held:
        routes.setdefault(e, {"held_back": "weight"})
    results = sorted(merged.values(), key=lambda h: -len(h["found_by"]))
    return {"query": query, "routes": routes, "results": results}


# ---------------------------------------------------------------- read
class _Browser:
    """One Crawl4AI browser on its own event loop, shared by every read."""

    def __init__(self, pages_at_once: int = 10):
        self.loop = None
        self.crawler = None
        self.lock = threading.Lock()
        self.slots = threading.Semaphore(pages_at_once)  # one browser, at most this many tabs

    def _start(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="crawl4ai").start()

        async def boot():
            from crawl4ai import AsyncWebCrawler, BrowserConfig
            crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False, user_agent=UA))
            await crawler.start()
            return crawler
        self.crawler = asyncio.run_coroutine_threadsafe(boot(), self.loop).result(120)

    def read(self, url: str, timeout: int = 30) -> dict:
        with self.lock:
            if self.crawler is None:
                self._start()
        from crawl4ai import CacheMode, CrawlerRunConfig
        cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=timeout * 1000,
                               verbose=False, delay_before_return_html=1.0)
        with self.slots:
            result = asyncio.run_coroutine_threadsafe(self.crawler.arun(url, config=cfg),
                                                      self.loop).result(timeout + 30)
        if not result.success:
            raise ProviderError((result.error_message or "page did not load")[:160])
        return {"final_url": result.redirected_url or url, "html": result.html or "",
                "text": str(result.markdown or "")}


BROWSER = _Browser()


def _read_crawl4ai(url):
    return BROWSER.read(url)


def _read_tinyfish(url):
    """docs.tinyfish.ai/fetch-api: a real browser render; format=html keeps the page structure
    (cards, mailto links) for the contact parser; per-URL timeout below our own."""
    RATE.wait("tinyfish:fetch")
    r = httpx.post("https://api.fetch.tinyfish.ai",
                   json={"urls": [url], "format": "html", "per_url_timeout_ms": 35000},
                   headers={"X-API-Key": mo_config.key("tinyfish")}, timeout=40)
    _check_money("tinyfish", r, "fetch")
    rows = r.json().get("results") or []
    if not rows or not (rows[0].get("text") or "").strip():
        err = (r.json().get("errors") or [{}])[0] if r.json().get("errors") else {}
        raise ProviderError(str(err.get("error") or err.get("message") or "no text")[:160])
    body = rows[0]["text"]
    is_html = isinstance(body, str) and "<" in body[:2000]
    return {"final_url": rows[0].get("final_url") or url, "html": body if is_html else "",
            "text": "" if is_html else str(body), "title": rows[0].get("title")}


def _read_deepseek(url):
    r = httpx.post("https://api.deepseek.com/v1/tools/fetch", json={"url": url},
                   headers={"Authorization": "Bearer " + mo_config.key("deepseek")}, timeout=40)
    _check_money("deepseek", r, "fetch")
    content = (r.json() or {}).get("content") or ""
    if not content.strip():
        raise ProviderError("no text")
    return {"final_url": url, "html": "", "text": content, "title": r.json().get("title")}


def download(url: str, max_bytes: int = 60_000_000) -> dict:
    """A plain GET: data exports, PDFs, CSV, JSON. Returns bytes and content type."""
    with httpx.stream("GET", url, headers={"User-Agent": UA}, timeout=120, follow_redirects=True) as r:
        if r.status_code >= 400:
            raise ProviderError(f"HTTP {r.status_code}")
        chunks, size = [], 0
        for chunk in r.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ProviderError("file larger than 60 MB; read it in slices")
            chunks.append(chunk)
        return {"final_url": str(r.url), "content_type": r.headers.get("content-type", ""),
                "bytes": b"".join(chunks)}


_READ = {"crawl4ai": _read_crawl4ai, "tinyfish": _read_tinyfish, "deepseek": _read_deepseek}


def available_readers(readers=None) -> list[str]:
    return [r for r in (readers or READERS) if r in _READ and (r == "crawl4ai" or mo_config.key(r))]


def read(url: str, readers=None, project_dir=None) -> dict:
    """Read one page with several readers at once. {reader: result or {"error": ...}}."""
    wanted = available_readers(readers)
    out = {}

    def one(reader):
        ok, why = mo_budget.allowed(reader, "fetch")
        if not ok:
            return reader, {"error": "skipped: " + why}
        try:
            # a page that timed out in the browser is not read again (it already took 30 s)
            res = _again(lambda: _READ[reader](url), transient=TRANSIENT_FAST if reader == "crawl4ai" else TRANSIENT)
            mo_budget.record(project_dir, reader, "fetch", True, url[:120])
            return reader, res
        except Exception as exc:  # noqa: BLE001 - each reader fails on its own
            # keep the reason ("no text", "HTTP 403", a timeout) so failures can be told apart later
            mo_budget.record(project_dir, reader, "fetch", False,
                             mo_config.scrub(f"{type(exc).__name__}: {exc}")[:80] + " | " + url[:80])
            return reader, {"error": mo_config.scrub(f"{type(exc).__name__}: {exc}")[:160]}

    with ThreadPoolExecutor(max_workers=3) as pool:
        for reader, res in pool.map(one, wanted):
            out[reader] = res
    return out


_POOLS: dict[str, ThreadPoolExecutor] = {}
_POOLS_LOCK = threading.Lock()


def _pool(name: str) -> ThreadPoolExecutor:
    """One worker pool per reader / engine: a slow one piling up never holds back the others."""
    with _POOLS_LOCK:
        if name not in _POOLS:
            _POOLS[name] = ThreadPoolExecutor(max_workers=32, thread_name_prefix=name)
        return _POOLS[name]


def read_race(url: str, readers=None, project_dir=None, grace: float = 3.0, on_late=None) -> dict:
    """Every reader at once; return when the first one has the page (plus `grace` seconds for
    the rest), so a page is as fast as its fastest reader. Readers still running finish in
    the background and hand their result to on_late(reader, result): nothing is lost."""
    import time
    from concurrent.futures import FIRST_COMPLETED, wait
    futs = {_pool(r).submit(read, url, [r], project_dir): r for r in available_readers(readers)}

    def result(f, reader):
        try:
            return f.result().get(reader) or {"error": "no result"}
        except Exception as exc:  # noqa: BLE001
            return {"error": mo_config.scrub(f"{type(exc).__name__}: {exc}")[:160]}
    out, first_ok, pending = {}, None, set(futs)
    while pending:
        left = None if first_ok is None else first_ok + grace - time.time()
        if left is not None and left <= 0:
            break
        done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
        for f in done:
            res = out[futs[f]] = result(f, futs[f])
            if first_ok is None and "error" not in res and len(res.get("html") or res.get("text") or "") > 200:
                first_ok = time.time()
    for f in pending:
        if on_late:
            with _POOLS_LOCK:
                _LATE.add(f)
            f.add_done_callback(lambda f, r=futs[f]: (on_late(r, result(f, r)), _LATE.discard(f)))
    return out


_LATE: set = set()


def drain(timeout: float = 90) -> int:
    """Wait for readers still finishing in the background (their contacts are merged when they
    land) so a run's final export has them. Returns how many were still out at the end."""
    import time
    end = time.time() + timeout
    while _LATE and time.time() < end:
        time.sleep(0.5)
    return len(_LATE)


# ---------------------------------------------------------------- TinyFish MCP (Agent, wallet)
def tinyfish_mcp(tool: str, args: dict, timeout: float = 60):
    import json

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def go():
        async with httpx.AsyncClient(headers={"Authorization": "Bearer " + mo_config.key("tinyfish")},
                                     timeout=timeout) as http:
            async with streamable_http_client("https://agent.tinyfish.ai/mcp", http_client=http) as s:
                async with ClientSession(s[0], s[1]) as session:
                    await session.initialize()
                    res = await session.call_tool(tool, args)
                    parts = [getattr(c, "text", "") or "" for c in (res.content or [])]
                    for part in parts:  # the first part is the JSON answer; later parts are notes
                        try:
                            return json.JSONDecoder().raw_decode(part.strip())[0]
                        except ValueError:
                            continue
                    return {"text": "\n".join(parts)}

    box = {}
    t = threading.Thread(target=lambda: box.setdefault("v", asyncio.run(go())), daemon=True)
    t.start()
    t.join(timeout + 15)
    if "v" not in box:
        raise ProviderError("TinyFish did not answer")
    return box["v"]


def agent_submit(url: str, goal: str) -> str:
    ok, why = mo_budget.allowed("tinyfish", "agent")
    if not ok:
        raise ProviderError("TinyFish skipped: " + why)
    import uuid
    # TinyFish requires a fresh session id per run (a reused one could replay a run).
    res = tinyfish_mcp("run_web_automation_async", {"url": url, "goal": goal, "session_id": str(uuid.uuid4())})
    run_id = res.get("run_id") or res.get("id") or (res.get("run") or {}).get("id")
    if not run_id:
        raise ProviderError("TinyFish gave no run id: " + str(res)[:160])
    return str(run_id)


def agent_status(run_id: str) -> dict:
    return tinyfish_mcp("get_run", {"id": run_id})

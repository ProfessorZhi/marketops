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
import socket
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import httpx

import mo_budget
import mo_config

SEARCH_ENGINES = ("serper", "serpapi", "deepseek", "tinyfish")
READERS = ("crawl4ai", "tinyfish", "deepseek")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


class ProviderError(Exception):
    pass


def _check_money(provider: str, response: httpx.Response) -> None:
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
def _serper(q, country, lang, n, maps):
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


def _serpapi(q, country, lang, n, maps):
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


def _deepseek(q, country, lang, n, maps):
    if maps:
        return []
    r = httpx.post("https://api.deepseek.com/v1/tools/search", json={"query": q[:128], "count": min(n, 50),
                   "need_content": False}, headers={"Authorization": "Bearer " + mo_config.key("deepseek")},
                   timeout=40)
    _check_money("deepseek", r)
    return [{"title": o.get("title"), "url": o.get("url"), "snippet": o.get("snippet") or o.get("summary")}
            for o in r.json().get("results", [])]


def _tinyfish(q, country, lang, n, maps):
    if maps:
        return []
    r = httpx.get("https://api.search.tinyfish.ai", params={"query": q},
                  headers={"X-API-Key": mo_config.key("tinyfish")}, timeout=40)
    _check_money("tinyfish", r)
    return [{"title": o.get("title"), "url": o.get("url"), "snippet": o.get("snippet")}
            for o in r.json().get("results", [])]


_SEARCH = {"serper": _serper, "serpapi": _serpapi, "deepseek": _deepseek, "tinyfish": _tinyfish}


def _norm(url: str) -> str:
    p = urllib.parse.urlsplit(url or "")
    host = (p.hostname or "").removeprefix("www.")
    return host + p.path.rstrip("/") + ("?" + p.query if p.query else "")


def search(query: str, engines=None, country: str = "", lang: str = "", n: int = 10,
           maps: bool = False, project_dir=None) -> dict:
    wanted = [e for e in (engines or SEARCH_ENGINES) if e in _SEARCH and mo_config.key(e)]
    routes, merged = {}, {}

    def one(engine):
        ok, why = mo_budget.allowed(engine)
        if not ok:
            return engine, None, why
        try:
            rows = _SEARCH[engine](query, country, lang, n, maps)
            mo_budget.record(project_dir, engine, "search", True, query[:80])
            return engine, rows, ""
        except Exception as exc:  # noqa: BLE001 - one engine failing never fails the search
            mo_budget.record(project_dir, engine, "search", False, f"{type(exc).__name__}: {exc}")
            return engine, None, mo_config.scrub(f"{type(exc).__name__}: {exc}")[:160]

    with ThreadPoolExecutor(max_workers=4) as pool:
        for engine, rows, why in pool.map(one, wanted):
            routes[engine] = {"results": len(rows)} if rows is not None else {"skipped": why}
            for rank, row in enumerate(rows or []):
                key = _norm(row.get("url") or "") or ("place:" + str(row.get("title")))
                hit = merged.setdefault(key, {**row, "found_by": []})
                hit["found_by"].append(engine)
                for f in ("snippet", "address", "phone", "url"):
                    if not hit.get(f) and row.get(f):
                        hit[f] = row[f]
    results = sorted(merged.values(), key=lambda h: -len(h["found_by"]))
    return {"query": query, "routes": routes, "results": results}


# ---------------------------------------------------------------- read
class _Browser:
    """One Crawl4AI browser on its own event loop, shared by every read."""

    def __init__(self):
        self.loop = None
        self.crawler = None
        self.lock = threading.Lock()

    def _start(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="crawl4ai").start()

        async def boot():
            from crawl4ai import AsyncWebCrawler, BrowserConfig
            crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False, user_agent=UA))
            await crawler.start()
            return crawler
        self.crawler = asyncio.run_coroutine_threadsafe(boot(), self.loop).result(120)

    def read(self, url: str, timeout: int = 45) -> dict:
        with self.lock:
            if self.crawler is None:
                self._start()
        from crawl4ai import CacheMode, CrawlerRunConfig
        cfg = CrawlerRunConfig(cache_mode=CacheMode.BYPASS, page_timeout=timeout * 1000,
                               verbose=False, delay_before_return_html=1.0)
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
    r = httpx.post("https://api.fetch.tinyfish.ai", json={"urls": [url]},
                   headers={"X-API-Key": mo_config.key("tinyfish")}, timeout=90)
    _check_money("tinyfish", r)
    rows = r.json().get("results") or []
    if not rows or not (rows[0].get("text") or "").strip():
        err = (r.json().get("errors") or [{}])[0] if r.json().get("errors") else {}
        raise ProviderError(str(err.get("error") or "no text")[:160])
    return {"final_url": rows[0].get("final_url") or url, "html": "", "text": rows[0]["text"],
            "title": rows[0].get("title")}


def _read_deepseek(url):
    r = httpx.post("https://api.deepseek.com/v1/tools/fetch", json={"url": url},
                   headers={"Authorization": "Bearer " + mo_config.key("deepseek")}, timeout=90)
    _check_money("deepseek", r)
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


def read(url: str, readers=None, project_dir=None) -> dict:
    """Read one page with several readers at once. {reader: result or {"error": ...}}."""
    wanted = [r for r in (readers or READERS) if r in _READ and (r == "crawl4ai" or mo_config.key(r))]
    out = {}

    def one(reader):
        ok, why = mo_budget.allowed(reader)
        if not ok:
            return reader, {"error": "skipped: " + why}
        try:
            res = _READ[reader](url)
            mo_budget.record(project_dir, reader, "fetch", True, url[:120])
            return reader, res
        except Exception as exc:  # noqa: BLE001 - each reader fails on its own
            mo_budget.record(project_dir, reader, "fetch", False, f"{type(exc).__name__}")
            return reader, {"error": mo_config.scrub(f"{type(exc).__name__}: {exc}")[:160]}

    with ThreadPoolExecutor(max_workers=3) as pool:
        for reader, res in pool.map(one, wanted):
            out[reader] = res
    return out


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
                    text = "".join(getattr(c, "text", "") for c in (res.content or []))
                    try:
                        return json.loads(text)
                    except ValueError:
                        return {"text": text}

    box = {}
    t = threading.Thread(target=lambda: box.setdefault("v", asyncio.run(go())), daemon=True)
    t.start()
    t.join(timeout + 15)
    if "v" not in box:
        raise ProviderError("TinyFish did not answer")
    return box["v"]


def agent_submit(url: str, goal: str) -> str:
    ok, why = mo_budget.allowed("tinyfish")
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

"""Offline adapters: the reference pipeline and the provider-shaped mocks.

Everything here is labelled ``kind="mock"`` in the run manifest, so a mock score
can never be mistaken for a live provider's score.
"""

from __future__ import annotations

import io
import json
import pathlib
import re
import socket
import urllib.parse

import httpx

from ..adapters.base import (
    AdapterInfo, AdapterTimeout, ContractViolation, HTTPError, ParseError,
    TransportError,
)
from ..mocks import pipeline, providers, public_web, slices, web

FIXTURE_HOST = "127.0.0.1"
FIXTURE_PORT = 18090
FIXTURE_BASE = f"http://{FIXTURE_HOST}:{FIXTURE_PORT}"
PUBLIC_PORT = 18091
PUBLIC_BASE = f"http://{FIXTURE_HOST}:{PUBLIC_PORT}"

_server: web._Server | None = None
_public_server: public_web._Server | None = None


def _ensure(host: str, port: int, starter) -> None:
    """Serve ``port`` if nothing is listening there yet.

    Port 18080 (the pre-existing fixture server) is never touched.
    """
    global _server, _public_server
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        if probe.connect_ex((host, port)) == 0:
            return
    server, _thread = starter(host, port)
    if port == FIXTURE_PORT:
        _server = server
    else:
        _public_server = server


def ensure_fixture_server() -> None:
    _ensure(FIXTURE_HOST, FIXTURE_PORT, web.start)


def ensure_public_server() -> None:
    """Serve the frozen public snapshots, so a public case scores offline."""
    _ensure(FIXTURE_HOST, PUBLIC_PORT, public_web.start)


class _Base:
    info: AdapterInfo

    def __init__(self, info: AdapterInfo, mode: str = "normal") -> None:
        if mode not in providers.MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {providers.MODES}")
        self.info = info
        self.mode = mode

    def availability(self) -> tuple[bool, str]:
        return True, ""

    def run(self, case: dict) -> dict:  # pragma: no cover - subclass responsibility
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _field(obj: dict, key: str, kind: type):
    if key not in obj:
        raise ContractViolation(f"missing field {key!r}")
    value = obj[key]
    if not isinstance(value, kind):
        raise ContractViolation(f"field {key!r} is {type(value).__name__}, expected {kind.__name__}")
    return value


def _domain(url: str) -> str:
    return (urllib.parse.urlsplit(url).netloc or "").lower().removeprefix("www.")


SEARCH_NORMALISERS = {
    "mock_serper": lambda raw: [
        {"title": _field(item, "title", str), "url": _field(item, "link", str),
         "snippet": item.get("snippet", "")}
        for item in _field(raw, "organic", list)
    ],
    "mock_tinyfish_search": lambda raw: [
        {"title": _field(item, "title", str), "url": _field(item, "url", str),
         "snippet": item.get("description", "")}
        for item in _field(raw, "results", list)
    ],
    "mock_deepseek_search": lambda raw: [
        {"title": _field(item, "title", str), "url": _field(item, "url", str),
         "snippet": item.get("content", "")}
        for item in _field(raw, "data", list)
    ],
    "mock_youtube_data": lambda raw: [
        {"title": _field(_field(item, "snippet", dict), "title", str),
         "url": _field(_field(item, "snippet", dict), "canonicalUrl", str),
         "snippet": ""}
        for item in _field(raw, "items", list)
    ],
    "mock_google_maps_places": lambda raw: [
        {"title": _field(_field(item, "displayName", dict), "text", str),
         "url": _field(item, "websiteUri", str),
         "snippet": ""}
        for item in _field(raw, "places", list)
    ],
}


class MockSearchAdapter(_Base):
    def __init__(self, provider: str = "mock_serper", mode: str = "normal") -> None:
        if provider not in SEARCH_NORMALISERS:
            raise KeyError(provider)
        super().__init__(AdapterInfo(
            name=provider, stage="search", kind="mock",
            requires=(), notes="Deterministic corpus index in the provider's own envelope.",
        ), mode)

    def run(self, case: dict) -> dict:
        params = case["input"]
        raw = providers.search(
            self.info.name, query=params["query"],
            country=params.get("country_code", ""),
            language=params.get("language_code", ""),
            top_k=params.get("top_k", 10), intent=params.get("intent", ""),
            mode=self.mode,
        )
        rows = SEARCH_NORMALISERS[self.info.name](raw)
        results = [{"title": row["title"], "url": row["url"], "snippet": row["snippet"],
                    "domain": _domain(row["url"]), "rank": rank}
                   for rank, row in enumerate(rows, start=1)]
        return {"results": results}


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

_META_REFRESH = re.compile(
    r'<meta[^>]+http-equiv="refresh"[^>]+content="[^"]*url=([^"]+)"', re.I | re.S
)


class MockFetchAdapter(_Base):
    def __init__(self, provider: str = "mock_local_http", mode: str = "normal") -> None:
        if provider not in ("mock_local_http", "mock_tinyfish_fetch", "mock_deepseek_fetch"):
            raise KeyError(provider)
        super().__init__(AdapterInfo(
            name=provider, stage="fetch", kind="mock",
            requires=(FIXTURE_BASE, PUBLIC_BASE),
            notes="Local corpus over HTTP; PDF via pypdf; one JS pattern emulated.",
        ), mode)
        ensure_fixture_server()
        ensure_public_server()

    def _page(self, url: str) -> dict:
        timeout = 10.0
        with httpx.Client(follow_redirects=True, timeout=timeout,
                          headers={"User-Agent": "ResearchCoreMock/1.0"}) as client:
            try:
                response = client.get(url)
            except httpx.TimeoutException as exc:
                raise AdapterTimeout(f"timeout fetching {url}") from exc
            except httpx.HTTPError as exc:
                raise TransportError(f"{type(exc).__name__}: {exc}") from exc

            if response.status_code == 429:
                raise HTTPError(429, f"rate limited fetching {url}", http_status=429)
            if response.status_code >= 500:
                raise HTTPError(response.status_code, f"upstream error fetching {url}",
                                http_status=response.status_code)
            if response.status_code >= 400:
                return {"final_url": str(response.url), "text": "", "links": [], "emails": []}

            content_type = response.headers.get("content-type", "")
            if "application/pdf" in content_type or str(response.url).endswith(".pdf"):
                return self._pdf(response)
            # A fetcher returns page text, not arbitrary bytes. Decoding an
            # image as HTML would report a success that no reader can use.
            if not any(kind in content_type for kind in ("text/", "html", "json", "xml")):
                return {"final_url": str(response.url), "text": "", "links": [], "emails": []}

            markup = response.text
            refresh = _META_REFRESH.search(markup)
            if refresh:
                target = urllib.parse.urljoin(str(response.url), refresh.group(1).strip())
                return self._page(target)

            return self._html(markup, str(response.url))

    def _html(self, markup: str, final_url: str) -> dict:
        markup = pipeline.render_js(markup)
        text = pipeline.visible_text(markup)
        # An app shell or binary garbage yields no words. Reporting it as text
        # would be a false success.
        if len(text) < 20 or not any(ch.isalpha() for ch in text):
            text = ""
        emails, _people, links = pipeline.page_facts(markup)
        return {"final_url": final_url, "text": text, "links": links, "emails": emails}

    def _pdf(self, response: httpx.Response) -> dict:
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise ParseError("pypdf is required to read PDFs") from exc
        reader = PdfReader(io.BytesIO(response.content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        return {"final_url": str(response.url), "text": text.strip(), "links": [], "emails": []}

    def run(self, case: dict) -> dict:
        url = case["input"]["url"]
        page = self._page(url)
        if self.info.name == "mock_local_http":
            return page
        raw = providers.fetch(self.info.name, url=url, page=page, mode=self.mode)
        if self.info.name == "mock_tinyfish_fetch":
            links = _field(raw, "links", list)
            text = _field(raw, "text", str)
            emails = [link[len("mailto:"):] for link in links if link.startswith("mailto:")]
            return {"final_url": _field(raw, "url", str), "text": text,
                    "links": [link for link in links], "emails": emails}
        text = _field(raw, "content", str)
        return {"final_url": _field(raw, "final_url", str), "text": text,
                "links": [], "emails": _field(raw, "mailto", list)}


# ---------------------------------------------------------------------------
# Single-stage reference adapters
# ---------------------------------------------------------------------------

class MockBriefAdapter(_Base):
    def __init__(self, mode: str = "normal") -> None:
        super().__init__(AdapterInfo(name="mock_brief_rules", stage="brief", kind="mock",
                                     notes="Keyword baseline; not a model."), mode)

    def run(self, case: dict) -> dict:
        return pipeline.parse_brief(case["input"]["raw_brief"])


class MockExtractionAdapter(_Base):
    def __init__(self, mode: str = "normal") -> None:
        super().__init__(AdapterInfo(name="mock_extractor_regex", stage="extraction", kind="mock",
                                     notes="Reports only strings visible in the snapshot."), mode)

    def run(self, case: dict) -> dict:
        snapshot = (pipeline.DATASET_ROOT / case["input"]["snapshot_path"]).resolve()
        if not snapshot.is_file():
            raise TransportError(f"snapshot missing: {case['input']['snapshot_path']}")
        markup = snapshot.read_text(encoding="utf-8")
        return pipeline.extract(markup, case["input"]["company"])


class MockQualificationAdapter(_Base):
    """A batch qualifier that remembers what it has already seen.

    Duplicate detection is order-aware by design: the same domain seen earlier in
    the dataset is a duplicate. Earlier *inputs* only -- gold is never read.
    """

    def __init__(self, mode: str = "normal") -> None:
        super().__init__(AdapterInfo(name="mock_qualification_rules", stage="qualification",
                                     kind="mock", notes="Policy rules over the stated ICP."), mode)
        self._seen: dict[str, set[str]] = self._earlier_domains()

    @staticmethod
    def _earlier_domains() -> dict[str, set[str]]:
        path = pipeline.DATASET_ROOT / "synthetic" / "qualification" / "cases.jsonl"
        seen: set[str] = set()
        out: dict[str, set[str]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            case = json.loads(line)
            out[case["case_id"]] = set(seen)
            seen.add(case["input"]["prospect"]["domain"])
        return out

    def run(self, case: dict) -> dict:
        prospect = case["input"]["prospect"]
        verdict = pipeline.qualify(prospect, case["input"]["icp"])
        if prospect["domain"] in self._seen.get(case["case_id"], set()):
            verdict.update({
                "qualification_status": "duplicate", "icp_match": True,
                "reason": "Same domain as an earlier prospect in this batch.",
            })
        return verdict


class MockIdentityAdapter(_Base):
    def __init__(self, mode: str = "normal") -> None:
        super().__init__(AdapterInfo(name="mock_identity_rules", stage="identity", kind="mock",
                                     notes="Name/domain keys; relations kept out of clusters."), mode)

    def run(self, case: dict) -> dict:
        return pipeline.cluster(case["input"]["records"])


class MockEvidenceAdapter(_Base):
    def __init__(self, mode: str = "normal") -> None:
        super().__init__(AdapterInfo(name="mock_evidence_checker", stage="evidence", kind="mock",
                                     notes="Recomputes SHA256 and searches the snapshot text."), mode)

    def run(self, case: dict) -> dict:
        return {"checks": pipeline.check_evidence(case["input"]["evidence"])}


class MockChannelAdapter(_Base):
    def __init__(self, mode: str = "normal") -> None:
        super().__init__(AdapterInfo(name="mock_channel_rules", stage="channel", kind="mock",
                                     notes="Sorts by stated prior; drops policy red flags."), mode)

    def run(self, case: dict) -> dict:
        return pipeline.rank_channels(case["input"]["channels"])


class MockE2EAdapter(_Base):
    def __init__(self, mode: str = "normal") -> None:
        super().__init__(AdapterInfo(name="mock_research_e2e", stage="e2e", kind="mock",
                                     requires=(FIXTURE_BASE,),
                                     notes="Reference pipeline over a fixed candidate slice."), mode)
        ensure_fixture_server()

    def run(self, case: dict) -> dict:
        return pipeline.run_e2e(case, slices.SLICES)


def registry() -> "AdapterRegistry":  # noqa: F821 - imported lazily to avoid a cycle
    from ..adapters.base import AdapterRegistry

    reg = AdapterRegistry()
    for name in SEARCH_NORMALISERS:
        reg.register(MockSearchAdapter(name))
    for name in ("mock_local_http", "mock_tinyfish_fetch", "mock_deepseek_fetch"):
        reg.register(MockFetchAdapter(name))
    reg.register(MockBriefAdapter())
    reg.register(MockExtractionAdapter())
    reg.register(MockQualificationAdapter())
    reg.register(MockIdentityAdapter())
    reg.register(MockEvidenceAdapter())
    reg.register(MockChannelAdapter())
    reg.register(MockE2EAdapter())
    return reg

#!/usr/bin/env python3
"""Capture the Public Evidence Benchmark: real, public, read-only pages.

This is the *only* part of the lab that touches the network, and it does so once,
politely, to freeze a snapshot. Everything after capture runs offline against
those bytes, so a score can be reproduced without asking the site again.

Rules this script enforces, not just documents:

  * ``robots.txt`` is fetched and honoured with :mod:`urllib.robotparser`; a
    disallowed path is recorded as a gap and never requested;
  * at most one request per host every ``POLITENESS_SECONDS``, and a clear
    ``User-Agent`` with a contact URL;
  * HTTP 401/403/429 (login, bot wall, rate limit) is a gap, never a workaround;
  * no LinkedIn, no Instagram, no login, no paywall, no captcha;
  * the committed snapshot is **desensitised** -- addresses and phone numbers are
    replaced -- while the original byte hash is kept in the manifest so the
    retrieval is still traceable.

Usage:
    python benchmark/datasets/research-core/public-evidence/build_public_evidence.py
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

HERE = pathlib.Path(__file__).resolve().parent
DATASET_VERSION = "research-core-public-1.0.0"
SCHEMA_VERSION = "marketops.research.case.v1"
USER_AGENT = ("ResearchCoreLab/1.0 (public evidence benchmark; "
              "+https://github.com/ProfessorZhi/marketops)")
POLITENESS_SECONDS = 2.0
TIMEOUT = 30
MAX_BYTES = 2_000_000
#: Below this many visible characters a page is an app shell, not readable content.
MIN_TEXT_CHARS = 60

#: Twenty real companies across nine countries and seven languages. Each entry
#: is (slug, display name, country, language, [(page, path)]).
COMPANIES: list[tuple[str, str, str, str, list[tuple[str, str]]]] = [
    ("trumpf", "TRUMPF", "DE", "de", [("home", "/"), ("about", "/en/company/about-trumpf/")]),
    ("krones", "Krones", "DE", "de", [("home", "/"), ("about", "/en/company/")]),
    ("siemens", "Siemens", "DE", "de", [("home", "/"), ("about", "/company/about")]),
    ("vanderlande", "Vanderlande", "NL", "nl", [("home", "/"), ("about", "/en/about-vanderlande")]),
    ("damen", "Damen Shipyards", "NL", "nl", [("home", "/"), ("about", "/en/about-us")]),
    ("renishaw", "Renishaw", "GB", "en", [("home", "/"), ("about", "/en/about-renishaw")]),
    ("rolls-royce", "Rolls-Royce", "GB", "en", [("home", "/"), ("about", "/company")]),
    ("atlas-copco", "Atlas Copco", "SE", "en", [("home", "/"), ("about", "/en/about-us")]),
    ("sandvik", "Sandvik", "SE", "en", [("home", "/"), ("about", "/en/about/")]),
    ("fanuc", "FANUC", "JP", "en", [("home", "/"), ("about", "/en/company/")]),
    ("keyence", "KEYENCE", "JP", "en", [("home", "/"), ("about", "/en/company/")]),
    ("abb", "ABB", "CH", "en", [("home", "/"), ("about", "/about-us")]),
    ("schneider-electric", "Schneider Electric", "FR", "fr", [("home", "/"), ("about", "/fr/about-us/")]),
    ("legrand", "Legrand", "FR", "fr", [("home", "/"), ("about", "/fr/")]),
    ("inditex", "Inditex", "ES", "es", [("home", "/"), ("about", "/en/about-us")]),
    ("mango", "Mango", "ES", "es", [("home", "/"), ("about", "/en/company/")]),
    ("cargill", "Cargill", "US", "en", [("home", "/"), ("about", "/about-cargill")]),
    ("emerson", "Emerson", "US", "en", [("home", "/"), ("about", "/en-us/company/about-us")]),
    ("rockwell-automation", "Rockwell Automation", "US", "en",
     [("home", "/"), ("about", "/en-us/company/about-us")]),
    ("eaton", "Eaton", "IE", "en", [("home", "/"), ("about", "/us/en-us/company/about-us.html")]),
    # Home pages below this line are single-snapshot targets; a site that
    # blocks the crawler is recorded as a gap, never retried around.
    ("kuka", "KUKA", "DE", "de", [("home", "/")]),
    ("boschrexroth", "Bosch Rexroth", "DE", "de", [("home", "/")]),
    ("sick", "SICK", "DE", "de", [("home", "/")]),
    ("wago", "WAGO", "DE", "de", [("home", "/")]),
    ("beckhoff", "Beckhoff", "DE", "de", [("home", "/")]),
    ("alfalaval", "Alfa Laval", "SE", "en", [("home", "/")]),
    ("gea", "GEA", "DE", "de", [("home", "/")]),
    ("metso", "Metso", "FI", "en", [("home", "/")]),
    ("andritz", "ANDRITZ", "AT", "de", [("home", "/")]),
    ("voith", "Voith", "DE", "de", [("home", "/")]),
    ("liebherr", "Liebherr", "CH", "de", [("home", "/")]),
    ("zeiss", "ZEISS", "DE", "de", [("home", "/")]),
    ("amada", "AMADA", "JP", "en", [("home", "/")]),
    ("igus", "igus", "DE", "de", [("home", "/")]),
    ("skf", "SKF", "SE", "en", [("home", "/")]),
    ("timken", "Timken", "US", "en", [("home", "/")]),
    ("hubbell", "Hubbell", "US", "en", [("home", "/")]),
    ("festo", "Festo", "DE", "de", [("home", "/")]),
    ("dmgmori", "DMG MORI", "DE", "de", [("home", "/")]),
    ("haascnc", "Haas Automation", "US", "en", [("home", "/")]),
    ("pilz", "Pilz", "DE", "de", [("home", "/")]),
    ("balluff", "Balluff", "DE", "de", [("home", "/")]),
    ("phoenixcontact", "Phoenix Contact", "DE", "de", [("home", "/")]),
    ("omron", "OMRON", "JP", "en", [("home", "/")]),
    ("yaskawa", "Yaskawa", "JP", "en", [("home", "/")]),
    ("danfoss", "Danfoss", "DK", "en", [("home", "/")]),
    ("grundfos", "Grundfos", "DK", "en", [("home", "/")]),
    ("schaeffler", "Schaeffler", "DE", "de", [("home", "/")]),
    ("parker", "Parker Hannifin", "US", "en", [("home", "/")]),
    ("heidenhain", "HEIDENHAIN", "DE", "de", [("home", "/")]),
    ("okuma", "OKUMA", "JP", "en", [("home", "/")]),
    ("cognex", "Cognex", "US", "en", [("home", "/")]),
    ("kollmorgen", "Kollmorgen", "US", "en", [("home", "/")]),
    ("smcworld", "SMC", "JP", "en", [("home", "/")]),
    ("molex", "Molex", "US", "en", [("home", "/")]),
    ("te", "TE Connectivity", "CH", "en", [("home", "/")]),
    ("amphenol", "Amphenol", "US", "en", [("home", "/")]),
    ("nord", "NORD Drivesystems", "DE", "de", [("home", "/")]),
    ("weidmueller", "Weidmüller", "DE", "de", [("home", "/")]),
    ("br-automation", "B&R Automation", "AT", "de", [("home", "/")]),
    ("mitsubishielectric", "Mitsubishi Electric", "JP", "en", [("home", "/")]),
]

#: Nineteen real YouTube channels. The channel page is a public HTML document;
#: the platform is a real one, the use is read-only and rate limited.
CREATORS: list[tuple[str, str, str, str]] = [
    ("siemens-youtube", "Siemens", "DE", "de"),
    ("abb-youtube", "ABB", "CH", "en"),
    ("fanuc-youtube", "FANUC", "JP", "en"),
    ("renishaw-youtube", "Renishaw", "GB", "en"),
    ("krones-youtube", "Krones", "DE", "de"),
    ("atlas-copco-youtube", "Atlas Copco", "SE", "en"),
    ("mango-youtube", "Mango", "ES", "es"),
    ("trumpf-youtube", "TRUMPF", "DE", "de"),
    ("sandvik-youtube", "Sandvik Coromant", "SE", "en"),
    ("dmgmori-youtube", "DMG MORI", "DE", "de"),
    ("haas-youtube", "Haas Automation", "US", "en"),
    ("kuka-youtube", "KUKA", "DE", "de"),
    ("festo-youtube", "Festo", "DE", "de"),
    ("boschrexroth-youtube", "Bosch Rexroth", "DE", "de"),
    ("igus-youtube", "igus", "DE", "de"),
    ("legrand-youtube", "Legrand", "FR", "fr"),
    ("schneider-youtube", "Schneider Electric", "FR", "fr"),
    ("inditex-youtube", "Inditex", "ES", "es"),
    ("cargill-youtube", "Cargill", "US", "en"),
]

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
#: Public pages embed third-party browser keys (YouTube ships Google's). They are
#: not our credentials, but a dataset has no business carrying a key-shaped string.
_PUBLIC_KEY = re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b|\bAKIA[0-9A-Z]{12,}\b")
_SCRIPT = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


class Politeness:
    """One request per host per ``POLITENESS_SECONDS``, and robots.txt honoured."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _wait(self, host: str) -> None:
        previous = self._last.get(host)
        if previous is not None:
            remaining = POLITENESS_SECONDS - (time.monotonic() - previous)
            if remaining > 0:
                time.sleep(remaining)
        self._last[host] = time.monotonic()

    def _parser(self, scheme: str, host: str) -> urllib.robotparser.RobotFileParser:
        if host in self._robots:
            return self._robots[host]
        parser = urllib.robotparser.RobotFileParser()
        robots_url = f"{scheme}://{host}/robots.txt"
        try:
            self._wait(host)
            request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                parser.parse(response.read(MAX_BYTES).decode("utf-8", "replace").splitlines())
        except urllib.error.HTTPError as exc:
            # RFC 9309: a 4xx robots.txt means "no restrictions".
            if not (400 <= exc.code < 500):
                parser.disallow_all = True
        except Exception:  # noqa: BLE001 - unreachable robots is a conservative block
            parser.disallow_all = True
        self._robots[host] = parser
        return parser

    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        return self._parser(parts.scheme, parts.netloc).can_fetch(USER_AGENT, url)

    def get(self, url: str) -> tuple[int, bytes, str, str]:
        parts = urllib.parse.urlsplit(url)
        self._wait(parts.netloc)
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en;q=0.9,*;q=0.5",
        })
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_BYTES), \
                response.headers.get("content-type", ""), response.geturl()


def _text(html: str) -> str:
    import html as html_mod

    stripped = _SCRIPT.sub(" ", html)
    return re.sub(r"\s+", " ", html_mod.unescape(_TAG.sub(" ", stripped))).strip()


def _redact_phone(match: re.Match[str]) -> str:
    """Only redact when the span really looks like a phone number.

    A bare ``\\d[\\d\\s().-]{7,}\\d`` also matches CSS lengths and ids; requiring
    nine digits keeps the substitution from rewriting markup.
    """
    return "+00 000 000 000" if sum(ch.isdigit() for ch in match.group(0)) >= 9 else match.group(0)


def desensitise(html: str, domain: str) -> str:
    """Replace addresses, phone numbers and key-shaped strings; structure stays intact.

    Applying this twice changes nothing, which lets an already-frozen snapshot be
    re-scrubbed when a rule is added without re-fetching the site.
    """
    html = _EMAIL.sub(f"contact@{domain}", html)
    html = _PHONE.sub(_redact_phone, html)
    return _PUBLIC_KEY.sub("REDACTED_PUBLIC_KEY", html)


def _slug_domain(slug: str, name: str) -> str:
    return f"{slug}.example"


def capture(existing: dict[str, dict] | None = None,
            known_gaps: dict[str, dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Capture every target not already frozen.

    ``existing`` maps a source URL to its snapshot record and ``known_gaps`` maps
    a source URL to the gap it produced. A re-run therefore adds new targets
    without asking the same site again -- re-fetching a page that has not changed
    is not politeness, it is load. To retry a blocked site, delete its entry from
    ``capture-manifest.json`` (or the whole file) and run again.
    """
    existing = existing or {}
    known_gaps = known_gaps or {}
    politeness = Politeness()
    snapshots: list[dict] = []
    gaps: list[dict] = []

    def take(slug: str, name: str, country: str, language: str,
             page: str, url: str, platform: str) -> None:
        if url in existing:
            snapshots.append(existing[url])
            print(f"have {slug:<20} {page:<6} {'':>7} {url}")
            return
        if url in known_gaps:
            gaps.append(known_gaps[url])
            print(f"gap  {slug:<20} {page:<6} {known_gaps[url]['reason']:<12} {url}")
            return
        if not politeness.allowed(url):
            gaps.append({"slug": slug, "url": url, "reason": "disallowed_by_robots"})
            return
        try:
            status, body, content_type, final_url = politeness.get(url)
        except urllib.error.HTTPError as exc:
            reason = {401: "login_required", 403: "forbidden", 429: "rate_limited"}.get(
                exc.code, f"http_{exc.code}")
            gaps.append({"slug": slug, "url": url, "reason": reason})
            return
        except Exception as exc:  # noqa: BLE001 - recorded, never retried around
            gaps.append({"slug": slug, "url": url, "reason": f"{type(exc).__name__}: {exc}"})
            return
        if "text/html" not in content_type:
            gaps.append({"slug": slug, "url": url, "reason": f"content_type={content_type}"})
            return

        original = body
        markup = desensitise(original.decode("utf-8", "replace"), _slug_domain(slug, name))
        folder = HERE / "snapshots" / slug
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{page}.html"
        path.write_text(markup, encoding="utf-8")

        title = _TITLE.search(markup)
        record = {
            "slug": slug,
            "name": name,
            "country": country,
            "language": language,
            "platform": platform,
            "page": page,
            "source_url": url,
            "final_url": final_url,
            "http_status": status,
            "content_type": content_type,
            "retrieved_at": _now(),
            "original_sha256": hashlib.sha256(original).hexdigest(),
            "snapshot_path": path.relative_to(HERE.parent).as_posix(),
            "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
            "title": (title.group(1).strip() if title else ""),
        }
        existing[url] = record
        snapshots.append(record)
        print(f"ok   {slug:<20} {page:<6} {len(markup):>7}B  {url}")

    for slug, name, country, language, pages in COMPANIES:
        for page, path in pages:
            take(slug, name, country, language, page, f"https://www.{slug}.com{path}", "website")

    for slug, name, country, language in CREATORS:
        handle = "".join(part.capitalize() for part in slug.split("-")[:-1])
        take(slug, name, country, language, "channel",
             f"https://www.youtube.com/@{handle}", "youtube")

    return snapshots, gaps


def _case(case_id: str, stage: str, country: str, language: str,
          source: str, snapshot: dict, input_obj: dict, gold: dict, notes: str) -> dict:
    return {
        "case_id": case_id,
        "schema_version": SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "stage": stage,
        "split": "holdout",
        "origin": "public_real_world",
        "market": country,
        "language": language,
        "input": input_obj,
        "gold": gold,
        "label_status": "draft",
        "provenance": {
            "source_ref": source,
            "captured_at": snapshot["retrieved_at"],
            "content_sha256": snapshot["snapshot_sha256"],
            "notes": notes,
        },
        "notes": notes,
    }


def _visible(snap: dict) -> str:
    """The words a reader could see, read from the frozen bytes on disk."""
    markup = (HERE.parent / snap["snapshot_path"]).read_text(encoding="utf-8")
    return _text(markup)


def build_cases(snapshots: list[dict]) -> tuple[list[dict], list[dict]]:
    fetch_cases: list[dict] = []
    evidence_cases: list[dict] = []
    for index, snap in enumerate(snapshots, start=1):
        text = _visible(snap)
        # An app shell that renders only its own logo is not a readable page. The
        # gold says so instead of pretending the fetch succeeded.
        usable = len(text) >= MIN_TEXT_CHARS
        markers = [snap["name"]] if usable and snap["name"].lower() in text.lower() else []
        # Gold is read off the frozen page, not off the checker: a field is
        # traceable only if its own value is visible in the desensitised bytes.
        fields = {"name": snap["name"]} if snap["name"].lower() in text.lower() else {}
        fetch_cases.append(_case(
            f"RC-FECH-PUB-{index:03d}", "fetch", snap["country"], snap["language"],
            snap["snapshot_path"],
            snap,
            {"url": f"http://127.0.0.1:18091/{snap['slug']}/{snap['page']}.html",
             "timeout_seconds": 30,
             "expected_outcome": "content" if usable else "no_content",
             "final_url_expected": None},
            {"page_kind": f"public_{snap['platform']}", "requires_js": not usable,
             "expected_text_markers": markers, "expected_emails": [],
             "expected_persons": [], "expected_links": [],
             "min_text_chars": 60, "forbidden_strings": []},
            f"Desensitised snapshot of {snap['source_url']}.",
        ))
        evidence_cases.append(_case(
            f"RC-EVID-PUB-{index:03d}", "evidence", snap["country"], snap["language"],
            snap["snapshot_path"],
            snap,
            {"evidence": {
                "evidence_id": f"RC-EVID-PUB-{index:03d}",
                "source_url": snap["source_url"],
                "source_type": "official_youtube" if snap["platform"] == "youtube" else "official_site",
                "retrieved_at": snap["retrieved_at"],
                "snapshot_path": snap["snapshot_path"],
                "sha256": snap["snapshot_sha256"],
                "extracted_fields": fields,
                "entity_refs": [snap["slug"]],
                "upstream_refs": [],
            }},
            {"snapshot_exists": True, "sha256_matches": True,
             "fields_traceable": list(fields.values()),
             "entity_ref": snap["slug"], "stale_after_days": None, "contradicts": []},
            f"Public snapshot; original hash {snap['original_sha256']}.",
        ))
    return fetch_cases, evidence_cases


def _write(path: pathlib.Path, cases: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases),
                    encoding="utf-8")


def _load_existing() -> tuple[dict[str, dict], dict[str, dict]]:
    """Records already frozen (and gaps already seen), keyed by source URL.

    The snapshot path and hash are recomputed from disk rather than trusted from
    the old manifest, so a record can never disagree with the bytes beside it.
    """
    manifest_path = HERE / "capture-manifest.json"
    if not manifest_path.is_file():
        return {}, {}
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}, {}
    known_gaps = {gap["url"]: gap for gap in previous.get("gaps", [])}
    existing: dict[str, dict] = {}
    for record in previous.get("snapshots", []):
        path = HERE / "snapshots" / record["slug"] / f"{record['page']}.html"
        if not path.is_file():
            continue
        existing[record["source_url"]] = {
            **record,
            "snapshot_path": path.relative_to(HERE.parent).as_posix(),
            "snapshot_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
    return existing, known_gaps


def _rescrub_existing(existing: dict[str, dict]) -> int:
    """Re-apply desensitisation to already-frozen snapshots; return how many changed.

    This exists for the case where a redaction rule is added after capture: the
    committed bytes can be scrubbed without asking any site again.
    """
    changed = 0
    for record in existing.values():
        path = HERE / "snapshots" / record["slug"] / f"{record['page']}.html"
        text = path.read_text(encoding="utf-8")
        cleaned = desensitise(text, _slug_domain(record["slug"], record["name"]))
        if cleaned != text:
            path.write_text(cleaned, encoding="utf-8")
            record["snapshot_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            record["bytes"] = path.stat().st_size
            changed += 1
    return changed


def _gaps_markdown(manifest: dict) -> str:
    counts = manifest["counts"]
    gaps = manifest["gaps"]
    lines = [
        "# Public Evidence Capture Gaps",
        "",
        f"Dataset `{manifest['dataset_version']}`, captured {manifest['captured_at']}.",
        "",
        "A gap is a target the capture could not freeze: a bot wall (403), a login",
        "wall (401), a rate limit (429), a `robots.txt` disallow, an empty page, or a",
        "404. None of them is worked around, retried around, or replaced with a",
        "synthetic stand-in, and the count is reported rather than filled.",
        "",
        f"- snapshots frozen: {counts['snapshots']}",
        f"- gaps recorded: {counts['gaps']}",
        f"- companies represented: {counts['companies']}",
        f"- creators represented: {counts['creators']}",
        f"- countries: {counts['countries']}; languages: {counts['languages']}",
        "",
    ]
    if gaps:
        lines += ["| slug | reason | url |", "|---|---|---|"]
        lines += [f"| {g['slug']} | {g['reason']} | {g['url']} |" for g in gaps]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    existing, known_gaps = _load_existing()
    rescrubbed = _rescrub_existing(existing)
    if rescrubbed:
        print(f"re-scrubbed {rescrubbed} frozen snapshot(s) with the current rules")
    snapshots, gaps = capture(existing, known_gaps)
    manifest = {
        "dataset_version": DATASET_VERSION,
        "captured_at": _now(),
        "user_agent": USER_AGENT,
        "politeness_seconds": POLITENESS_SECONDS,
        "snapshots": snapshots,
        "gaps": gaps,
        "counts": {
            "snapshots": len(snapshots),
            "gaps": len(gaps),
            "companies": len({s["slug"] for s in snapshots if s["platform"] == "website"}),
            "creators": len({s["slug"] for s in snapshots if s["platform"] == "youtube"}),
            "countries": len({s["country"] for s in snapshots}),
            "languages": len({s["language"] for s in snapshots}),
        },
    }
    (HERE / "capture-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (HERE / "GAPS.md").write_text(_gaps_markdown(manifest), encoding="utf-8")

    fetch_cases, evidence_cases = build_cases(snapshots)
    _write(HERE / "fetch" / "cases.jsonl", fetch_cases)
    _write(HERE / "evidence" / "cases.jsonl", evidence_cases)
    print(f"\n{len(snapshots)} snapshots, {len(gaps)} gaps, "
          f"{len(fetch_cases)} fetch cases, {len(evidence_cases)} evidence cases")
    for gap in gaps:
        print(f"  GAP {gap['slug']:<20} {gap['reason']:<20} {gap['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

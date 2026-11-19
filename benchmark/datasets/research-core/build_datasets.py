#!/usr/bin/env python3
"""Emit every Research Core dataset from one source of truth.

Why generated rather than hand-written JSONL:

  * a case and the corpus page it refers to cannot drift apart — the builder
    reads the page hash from ``corpus/MANIFEST.json``;
  * the same company record feeds search, fetch, extraction, qualification and
    E2E, so a prospect in one dataset is the same organisation in another;
  * ``tests/test_research_datasets.py`` re-runs the builder and fails if the
    committed JSONL is not byte-identical, which makes hand-edits impossible to
    slip in unnoticed.

Nothing here is random. Every id, order and pick is a pure function of the
input lists, so two runs produce the same bytes.

Usage:
    python benchmark/datasets/research-core/build_datasets.py
"""

from __future__ import annotations

import html as _html
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE / "corpus"))

import source  # noqa: E402

DATASET_VERSION = "research-core-1.0.0"
SCHEMA_VERSION = "marketops.research.case.v1"
CAPTURED = "2026-11-19T09:00:00+08:00"

MANIFEST = json.loads((HERE / "corpus" / "MANIFEST.json").read_text(encoding="utf-8"))
COMPANIES = {c["slug"]: c for c in source.COMPANIES}
ORDER = [c["slug"] for c in source.COMPANIES]


def page(slug: str, name: str = "about.html") -> dict:
    # Keys in corpus/MANIFEST.json are relative to corpus/; the case's
    # provenance path is relative to this dataset directory.
    rel = f"sites/{slug}/{name}"
    entry = MANIFEST["files"].get(rel)
    if entry is None:
        raise SystemExit(f"corpus manifest has no {rel}")
    return {"path": f"corpus/{rel}", "sha256": entry["sha256"], "bytes": entry["bytes"]}


def base(case_id: str, stage: str, split: str, origin: str, market: str, language: str,
         inp: dict, gold: dict, *, ref: str, sha: str | None, notes: str,
         label_status: str = "draft") -> dict:
    return {
        "case_id": case_id,
        "schema_version": SCHEMA_VERSION,
        "dataset_version": DATASET_VERSION,
        "stage": stage,
        "split": split,
        "origin": origin,
        # ISO-style upper case, so `de` and `DE` cannot become two markets.
        "market": market if market == "multi" else market.upper(),
        "language": language,
        "input": inp,
        "gold": gold,
        "label_status": label_status,
        "provenance": {
            "source_ref": ref,
            "captured_at": CAPTURED,
            "content_sha256": sha,
            "notes": notes,
        },
        "notes": notes,
    }


def write(stage: str, cases: list[dict]) -> None:
    directory = HERE / "synthetic" / stage
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "cases.jsonl"
    text = "".join(json.dumps(c, ensure_ascii=False, sort_keys=False) + "\n" for c in cases)
    path.write_text(text, encoding="utf-8")
    print(f"{stage:14} {len(cases):3} cases -> {path.relative_to(REPO)}")


# ---------------------------------------------------------------------------
# Briefs
# ---------------------------------------------------------------------------

BRIEFS = [
    {
        "case_id": "RC-BRIEF-001",
        "raw": {
            "product": "Deck hardware supplier programme",
            "description": "A supplier programme for deck fittings, cleats and hatch hardware used by "
                           "commercial shipyards and marine equipment distributors.",
            "offer": "Onboarding with a two-week trial order and technical documentation pack.",
            "markets": ["GB", "NL", "IE"],
            "languages": ["en", "nl"],
            "goal": "Find marine equipment companies and shipyards with 20-500 staff that buy deck hardware, "
                    "and identify the person who owns purchasing or partnerships.",
        },
        "gold": {
            "icp_attributes": ["marine equipment", "shipyard", "deck hardware", "20-500 employees", "GB", "NL", "IE"],
            "personas": ["Managing Director", "Head of Procurement", "Head of Partnerships"],
            "pain_points": ["long lead times on deck fittings", "single-supplier dependency", "spare-parts availability"],
            "channels": ["direct email to a named director", "trade association member directory", "industry trade fair"],
            "queries": ["marine deck fittings supplier Netherlands", "shipyard equipment distributor UK",
                        "scheepsbeslag leverancier Zeeland"],
            "exclusions": ["consumer boat shops", "furniture retailers", "companies under 20 employees"],
            "forbidden_claims": ["ISO 9001 certified", "exclusive distributor", "market leader"],
        },
    },
    {
        "case_id": "RC-BRIEF-002",
        "raw": {
            "product": "CNC tooling and factory automation retrofit",
            "description": "A retrofit package for the German Mittelstand that connects older CNC machine "
                           "tools to modern automation cells.",
            "offer": "Free production-line assessment followed by a fixed-price retrofit.",
            "markets": ["DE", "AT"],
            "languages": ["de"],
            "goal": "Find German and Austrian machine-tool builders and automation integrators with "
                    "100-1000 staff, and reach the technical or purchasing decision maker.",
        },
        "gold": {
            "icp_attributes": ["machine tools", "factory automation", "100-1000 employees", "DE", "AT", "Mittelstand"],
            "personas": ["Geschäftsführer", "Leiter Vertrieb", "Leiter Einkauf"],
            "pain_points": ["obsolete control systems", "skilled-labour shortage", "integration cost"],
            "channels": ["direct email in German to the Geschäftsführung", "industry association event",
                         "specialist trade press"],
            "queries": ["CNC Fräsmaschinen Hersteller Mittelstand", "Automatisierung Retrofit Anbieter Deutschland",
                        "Werkzeugmaschinenbauer Österreich"],
            "exclusions": ["hotel groups", "management consultancies", "commercial kitchen suppliers",
                           "companies under 100 employees"],
            "forbidden_claims": ["TÜV certified", "guaranteed 30% cost saving", "industry standard"],
        },
    },
    {
        "case_id": "RC-BRIEF-003",
        "raw": {
            "product": "Editorial video production retainer",
            "description": "A monthly retainer for brand films, documentary shorts and podcast production "
                           "for cultural and charitable organisations.",
            "offer": "One paid pilot episode, then a rolling monthly retainer.",
            "markets": ["GB", "IE"],
            "languages": ["en"],
            "goal": "Find independent production studios with 10-200 staff and identify the founder or "
                    "executive producer who commissions external work.",
        },
        "gold": {
            "icp_attributes": ["video production", "podcast production", "10-200 employees", "GB", "IE", "independent studio"],
            "personas": ["Founder", "Executive Producer", "Head of Partnerships"],
            "pain_points": ["uneven project pipeline", "in-house capacity limits", "client demand for audio"],
            "channels": ["direct email to the founder", "production industry directory", "film festival network"],
            "queries": ["independent video production studio London", "podcast production company Ireland",
                        "documentary production studio Bristol"],
            "exclusions": ["software vendors", "data analytics firms", "companies over 200 employees",
                           "in-house corporate teams"],
            "forbidden_claims": ["award-winning", "BAFTA nominated", "industry leading"],
        },
    },
]


def build_briefs() -> list[dict]:
    cases = []
    for index, brief in enumerate(BRIEFS, start=1):
        cases.append(base(
            brief["case_id"], "brief", "development", "synthetic", "multi", "en",
            {"raw_brief": brief["raw"]}, brief["gold"],
            ref=f"build_datasets.py#BRIEFS[{index - 1}]", sha=None,
            notes="Synthetic product brief for harness testing; not a real product description.",
        ))
    return cases


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

# (case_id, query, intent, country, lang, slugs, extras)
# ``slugs`` are the companies a correct engine must surface. The query always
# contains words that appear in the corpus page, so the mock index can answer it
# for the reason a real engine would: the words are on the page.
SEARCH_SPECS: list[tuple] = [
    # official_website -- 5
    ("RC-SRCH-WEB-001", "Northwind Marine official website", "official_website", "gb", "en",
     ["northwind-marine"], {}),
    ("RC-SRCH-WEB-002", "Braunfeld Maschinenbau GmbH offizielle Website", "official_website", "de", "de",
     ["braunfeld-maschinenbau"], {}),
    ("RC-SRCH-WEB-003", "Van Dijk Watersport officiële website", "official_website", "nl", "nl",
     ["van-dijk-watersport"], {}),
    ("RC-SRCH-WEB-004", "Kestrel Robotics warehouse automation company", "official_website", "us", "en",
     ["kestrel-robotics"], {}),
    ("RC-SRCH-WEB-005", "Alpenblick Hotels official site", "official_website", "at", "de",
     ["alpenblick-hotels"], {}),
    # contact_page -- 4
    ("RC-SRCH-CON-006", "Northwind Marine contact details", "contact_page", "gb", "en",
     ["northwind-marine"], {}),
    ("RC-SRCH-CON-007", "Kranich Software Kontakt", "contact_page", "de", "de",
     ["kranich-software"], {}),
    ("RC-SRCH-CON-008", "Zuiderzee Techniek contactgegevens", "contact_page", "nl", "nl",
     ["zuiderzee-techniek"], {}),
    ("RC-SRCH-CON-009", "Amberlight Media contact email", "contact_page", "ie", "en",
     ["amberlight-media"], {}),
    # team_page -- 2
    ("RC-SRCH-TEAM-010", "Braunfeld Maschinenbau Team Geschäftsführung", "team_page", "de", "de",
     ["braunfeld-maschinenbau"], {}),
    ("RC-SRCH-TEAM-011", "Quarry Lane Studios team executive producer", "team_page", "gb", "en",
     ["quarry-lane-studios"], {}),
    # executive -- 3
    ("RC-SRCH-EXEC-012", "Marta Visser Northwind Marine Managing Director", "executive", "gb", "en",
     ["northwind-marine"], {}),
    ("RC-SRCH-EXEC-013", "Julia Brandt Kranich Software Partnerschaften", "executive", "de", "de",
     ["kranich-software"], {}),
    ("RC-SRCH-EXEC-014", "Willem van Dijk directeur watersport", "executive", "nl", "nl",
     ["van-dijk-watersport"], {}),
    # youtube_channel -- 3
    ("RC-SRCH-YTCH-015", "Northwind Marine YouTube channel", "youtube_channel", "gb", "en",
     ["northwind-marine"], {"kind": "youtube_channel"}),
    ("RC-SRCH-YTCH-016", "Elbwerk Automation YouTube Kanal", "youtube_channel", "de", "de",
     ["elbwerk-automation"], {"kind": "youtube_channel"}),
    ("RC-SRCH-YTCH-017", "Brabant Food Group YouTube kanaal", "youtube_channel", "nl", "nl",
     ["brabant-food-group"], {"kind": "youtube_channel"}),
    # youtube_creator -- 2
    ("RC-SRCH-YTCR-018", "Quarry Lane Studios video essays YouTube", "youtube_creator", "gb", "en",
     ["quarry-lane-studios"], {"kind": "youtube_channel"}),
    ("RC-SRCH-YTCR-019", "Amberlight Media podcast YouTube", "youtube_creator", "ie", "en",
     ["amberlight-media"], {"kind": "youtube_channel"}),
    # exact_company -- 3
    ("RC-SRCH-EXACT-020", '"Schwarzwald Präzision GmbH"', "exact_company", "de", "de",
     ["schwarzwald-praezision"], {}),
    ("RC-SRCH-EXACT-021", '"Harborline Foods"', "exact_company", "ie", "en",
     ["harborline-foods"], {}),
    ("RC-SRCH-EXACT-022", '"Van Dijk Watersport"', "exact_company", "nl", "nl",
     ["van-dijk-watersport"], {}),
    # long_tail -- 3
    ("RC-SRCH-LONG-023", "supplier of deck fittings and hatch hardware for commercial shipyards",
     "long_tail", "gb", "en", ["northwind-marine"], {}),
    ("RC-SRCH-LONG-024", "precision turned stainless steel parts for medical technology",
     "long_tail", "de", "de", ["schwarzwald-praezision"], {}),
    ("RC-SRCH-LONG-025", "rooftop solar installation for farms and industrial sites",
     "long_tail", "ie", "en", ["solstice-renewables"], {}),
    # multilingual -- 3
    ("RC-SRCH-ML-026", "scheepsbeslag en dekbeslag leverancier Zeeland", "multilingual", "nl", "nl",
     ["van-dijk-watersport"], {}),
    ("RC-SRCH-ML-027", "Kontraktlogistik Standorte Nordrhein-Westfalen", "multilingual", "de", "de",
     ["lindenhof-logistik"], {}),
    ("RC-SRCH-ML-028", "chilled and frozen produce distribution Leinster", "multilingual", "ie", "en",
     ["harborline-foods"], {}),
    # country_limited -- 2
    ("RC-SRCH-CTRY-029", "marine equipment companies in the Netherlands", "country_limited", "nl", "nl",
     ["van-dijk-watersport", "zuiderzee-techniek"], {}),
    ("RC-SRCH-CTRY-030", "hotel group in Austria", "country_limited", "at", "de",
     ["alpenblick-hotels"], {}),
    # parent_subsidiary -- 2
    ("RC-SRCH-PARENT-031", "Delta Ridge Holdings subsidiaries mining equipment", "parent_subsidiary", "us", "en",
     ["delta-ridge-holdings", "delta-ridge-mining"], {}),
    ("RC-SRCH-PARENT-032", "Delta Ridge Mining Equipment parent company", "parent_subsidiary", "us", "en",
     ["delta-ridge-mining", "delta-ridge-holdings"], {}),
    # disambiguation -- 2
    ("RC-SRCH-DISAMB-033", "Lindenhof Logistik GmbH logistics company", "disambiguation", "de", "de",
     ["lindenhof-logistik"], {}),
    ("RC-SRCH-DISAMB-034", "Lindeboom Meubelen furniture manufacturer", "disambiguation", "be", "nl",
     ["lindeboom-meubelen"], {}),
    # competitor -- 2
    ("RC-SRCH-COMP-035", "alternatives to Kestrel Robotics factory automation and robotics",
     "competitor", "us", "en", ["kestrel-robotics", "elbwerk-automation"], {}),
    ("RC-SRCH-COMP-036", "competitors of Braunfeld Maschinenbau machine tools", "competitor", "de", "de",
     ["braunfeld-maschinenbau"], {}),
    # identity_link -- 2
    ("RC-SRCH-IDLINK-037", "Marta Visser Northwind Marine profile", "identity_link", "gb", "en",
     ["northwind-marine"], {}),
    ("RC-SRCH-IDLINK-038", "Anna Moser Wienerwald Küche Einkauf", "identity_link", "at", "de",
     ["wienerwald-kueche"], {}),
    # official_source_priority -- 2
    ("RC-SRCH-SRC-039", "Northwind Marine company profile registry", "official_source_priority", "gb", "en",
     ["northwind-marine"], {"excluded": ["business-registry.example", "company-data.example"]}),
    ("RC-SRCH-SRC-040", "Vlaamse Zorg Groep ondernemingsprofiel", "official_source_priority", "be", "nl",
     ["vlaamse-zorg"], {"excluded": ["company-data.example", "belgian-companies.example"]}),
    # product_catalogue -- 1
    ("RC-SRCH-CAT-042", "Braunfeld Maschinenbau Produktkatalog CNC Werkzeugmaschinen",
     "product_catalogue", "de", "de", ["braunfeld-maschinenbau"], {}),
    # pdf_document -- 1
    ("RC-SRCH-PDF-043", "Northwind Marine PDF catalogue deck fittings", "pdf_document",
     "gb", "en", ["northwind-marine"], {}),
    # news_article -- 1
    ("RC-SRCH-NEWS-044", "Amberlight Media award video production", "news_article",
     "ie", "en", ["amberlight-media"], {}),
    # job_posting -- 1
    ("RC-SRCH-JOB-045", "Kestrel Robotics careers warehouse automation engineer", "job_posting",
     "us", "en", ["kestrel-robotics"], {}),
    # social_profile -- 1
    ("RC-SRCH-SOCIAL-046", "Quarry Lane Studios Instagram video essays", "social_profile",
     "gb", "en", ["quarry-lane-studios"], {}),
    # local_business -- 1
    ("RC-SRCH-LOCAL-047", "Brabant Food Group locatie Brabant", "local_business",
     "nl", "nl", ["brabant-food-group"], {}),
]

#: Domains that a correct answer must not offer as the company's official home.
DIRECTORIES = ["business-registry.example", "company-data.example", "belgian-companies.example",
               "crunchbase.example", "dnb.example"]


def build_search() -> list[dict]:
    cases = []
    for index, spec in enumerate(SEARCH_SPECS, start=1):
        case_id, query, intent, country, lang, slugs, extra = spec
        expected_domains = [COMPANIES[s]["domain"] for s in slugs]
        expected_urls: list[str] = []
        if extra.get("kind") == "youtube_channel":
            # Synthetic channels. The handle is derived from the slug so it is
            # stable; the site is the real youtube.com, the channel is not.
            expected_domains = ["youtube.com"]
            expected_urls = [f"https://www.youtube.com/@{''.join(p.capitalize() for p in s.split('-'))}"
                             for s in slugs]
        personas = []
        for slug in slugs:
            for person in COMPANIES[slug].get("team", []):
                if person["decision_maker"]:
                    personas.append({"name": person["name"], "entity_type": "person"})
        entities = [{"name": COMPANIES[s]["name"], "entity_type": "organisation"} for s in slugs]
        if intent in {"executive", "identity_link", "team_page"}:
            entities.extend(personas[:3])

        excluded = list(extra.get("excluded", []))
        if intent == "official_source_priority":
            excluded += [d for d in DIRECTORIES if d not in excluded]

        page_ref = page(slugs[0], "index.html")
        cases.append(base(
            case_id, "search", "development" if index <= 28 else "holdout",
            "synthetic", country, lang,
            {"query": query, "intent": intent, "country_code": country,
             "language_code": lang, "top_k": 10, "expected_result_kind": "results"},
            {
                "expected_domains": expected_domains,
                "expected_urls": expected_urls,
                "expected_entities": entities,
                "excluded_domains": excluded,
                "source_refs": [page_ref["path"]],
            },
            ref=page_ref["path"], sha=page_ref["sha256"],
            notes="Synthetic company and query; gold is the corpus record, not a provider result.",
        ))
    # Two deliberately hard shapes: one query with no answer, one with only
    # irrelevant answers. Both are allowed to return nothing.
    cases.append(base(
        "RC-SRCH-EMPTY-041", "search", "validation", "synthetic", "gb", "en",
        {"query": "quantum flux capacitor supplier in Greenland", "intent": "long_tail",
         "country_code": "gl", "language_code": "kl", "top_k": 10,
         "expected_result_kind": "no_results"},
        {"expected_domains": [], "expected_urls": [], "expected_entities": [],
         "excluded_domains": [], "source_refs": ["synthetic/no-results"]},
        ref="synthetic/no-results", sha=None,
        notes="A correct engine returns nothing here. Returning anything is a miss.",
    ))
    return cases


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def _page_bytes(slug: str, page_name: str) -> str:
    return (HERE / "corpus" / "sites" / slug / page_name).read_text(encoding="utf-8")


def _page_visible_text(slug: str, page_name: str) -> str:
    raw = re.sub(r"<script.*?</script>", " ", _page_bytes(slug, page_name), flags=re.S)
    return _html.unescape(re.sub(r"<[^>]+>", " ", raw))


def _page_facts(slug: str, page_name: str) -> tuple[list[str], list[dict], list[str]]:
    """What a human reading this rendered page can actually see.

    Gold is read off the frozen page rather than recomputed from ``source.py``,
    because the page is the artifact the provider will be given. A page that
    withholds an address (form-only contact pages) must not have one in gold.
    """
    raw = _page_bytes(slug, page_name)
    emails = sorted(set(re.findall(r'mailto:([^"\'<>\s]+)', raw)))
    company = COMPANIES[slug]
    persons: dict[str, dict] = {}
    for match in re.finditer(r"<li><strong>(.+?)</strong> — (.*?)(?:, <a|</li>)", raw):
        name = _html.unescape(match.group(1))
        persons.setdefault(name, {"name": name, "role": _html.unescape(match.group(2)).strip()})
    # A contact block pairs a name with an address, not with a role; a role
    # that is not on the page is not something a fetcher can be scored on.
    links = [f"mailto:{address}" for address in emails]
    return emails, list(persons.values()), links


def _fetch(slug: str, page_name: str, case_id: str, page_kind: str, *, outcome: str = "content",
           split: str = "development", requires_js: bool = False,
           markers: list[str] | None = None, emails: list[str] | None = None,
           persons: list[dict] | None = None, links: list[str] | None = None,
           min_chars: int = 60, forbidden: list[str] | None = None,
           final_url: str | None = None, url_suffix: str = "", notes: str = "") -> dict:
    company = COMPANIES[slug]
    ref = page(slug, page_name)
    url = f"http://127.0.0.1:18090/{slug}/{page_name}{url_suffix}"
    seen_emails, seen_persons, seen_links = _page_facts(slug, page_name)
    if markers is None:
        markers = [company["name"]]
        if page_name in ("index.html", "about.html"):
            markers.append(company["industry"])
    if emails is None:
        emails = seen_emails
    if persons is None:
        persons = seen_persons
    if links is None:
        links = seen_links
    if forbidden is None:
        # A reader that does not wait for the script leaves the placeholder in.
        forbidden = [{"en": "Loading", "de": "Wird geladen", "nl": "Laden"}[company["lang"]]] if requires_js else []
    return base(
        case_id, "fetch", split, "synthetic", company["country"].lower() if len(company["country"]) == 2 else "xx",
        company["lang"],
        {"url": url, "timeout_seconds": 30, "expected_outcome": outcome,
         "final_url_expected": final_url},
        {"page_kind": page_kind, "requires_js": requires_js,
         "expected_text_markers": markers, "expected_emails": emails,
         "expected_persons": persons, "expected_links": links,
         "min_text_chars": min_chars, "forbidden_strings": forbidden or []},
        ref=ref["path"], sha=ref["sha256"], notes=notes or f"{page_kind} page of {company['name']}.",
    )


def build_fetch() -> list[dict]:
    cases: list[dict] = []
    # static HTML / about / index
    cases.append(_fetch("northwind-marine", "index.html", "RC-FECH-STATIC-001", "static_html"))
    cases.append(_fetch("braunfeld-maschinenbau", "index.html", "RC-FECH-STATIC-002", "static_html"))
    cases.append(_fetch("van-dijk-watersport", "index.html", "RC-FECH-STATIC-003", "static_html"))
    # about pages
    cases.append(_fetch("kranich-software", "about.html", "RC-FECH-ABOUT-004", "about_page"))
    cases.append(_fetch("kempen-bouw", "about.html", "RC-FECH-ABOUT-005", "about_page"))
    # founder biography / org structure
    cases.append(_fetch("quarry-lane-studios", "team.html", "RC-FECH-FOUNDER-006", "founder_biography",
                        markers=["Imogen Price", "Creative Director"]))
    cases.append(_fetch("delta-ridge-holdings", "about.html", "RC-FECH-ORG-007", "org_structure",
                        markers=["Delta Ridge Holdings", "industrial holding company"]))
    # team pages, static and JS
    cases.append(_fetch("harborline-foods", "team.html", "RC-FECH-TEAM-008", "team_page"))
    cases.append(_fetch("falkenried-kliniken", "team.html", "RC-FECH-TEAM-009", "team_page"))
    cases.append(_fetch("northwind-marine", "team.html", "RC-FECH-JS-010", "team_page",
                        requires_js=True))
    cases.append(_fetch("kranich-software", "team.html", "RC-FECH-JS-011", "team_page",
                        requires_js=True))
    cases.append(_fetch("zuiderzee-techniek", "team.html", "RC-FECH-JS-012", "team_page",
                        requires_js=True, markers=["Pieter Bakker", "Directeur"]))
    # contact pages: email, mailto, role, generic
    cases.append(_fetch("brabant-food-group", "contact.html", "RC-FECH-CONTACT-013", "contact_page"))
    cases.append(_fetch("wienerwald-kueche", "contact.html", "RC-FECH-MAILTO-014", "contact_page",
                        links=["mailto:office@wienerwald-kueche.example",
                               "mailto:a.moser@wienerwald-kueche.example"]))
    cases.append(_fetch("elbwerk-automation", "contact.html", "RC-FECH-ROLE-015", "role_based_contact"))
    cases.append(_fetch("cedar-finch", "contact.html", "RC-FECH-GENERIC-016", "generic_contact",
                        emails=[], persons=[], links=[],
                        markers=["Contact form", "Send"], min_chars=60))
    # missing contact: a company page that really has no contact block
    cases.append(_fetch("de-vries-luchtbehandeling", "contact.html", "RC-FECH-MISSING-017",
                        "missing_contact", emails=[], persons=[], links=[],
                        markers=["Contactformulier"], min_chars=60))
    # dynamic behaviours
    cases.append(base(
        "RC-FECH-REDIRECT-018", "fetch", "development", "synthetic", "gb", "en",
        {"url": "http://127.0.0.1:18090/dynamic/redirect/1", "timeout_seconds": 30,
         "expected_outcome": "content",
         "final_url_expected": "http://127.0.0.1:18090/northwind-marine/about.html"},
        {"page_kind": "redirect_chain", "requires_js": False,
         "expected_text_markers": ["Northwind Marine", "Founded"],
         "expected_emails": [], "expected_persons": [], "expected_links": [],
         "min_text_chars": 80, "forbidden_strings": []},
        ref="serve.py#/dynamic/redirect/1", sha=None,
        notes="Two-hop 302; the final URL is part of the gold.",
    ))
    cases.append(base(
        "RC-FECH-COOKIE-019", "fetch", "development", "synthetic", "gb", "en",
        {"url": "http://127.0.0.1:18090/dynamic/cookie/set", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "cookie_consent", "requires_js": False,
         "expected_text_markers": ["Cookie accepted", "Northwind Marine"],
         "expected_emails": ["info@northwindmarine.example"],
         "expected_persons": [], "expected_links": ["mailto:info@northwindmarine.example"],
         "min_text_chars": 60, "forbidden_strings": []},
        ref="serve.py#/dynamic/cookie", sha=None,
        notes="The payload only exists after the cookie is stored.",
    ))
    cases.append(base(
        "RC-FECH-META-020", "fetch", "development", "synthetic", "nl", "nl",
        {"url": "http://127.0.0.1:18090/dynamic/meta-refresh", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "meta_refresh", "requires_js": False,
         "expected_text_markers": ["Van Dijk Watersport"],
         "expected_emails": ["info@vandijkwatersport.example"],
         "expected_persons": [], "expected_links": ["mailto:info@vandijkwatersport.example"],
         "min_text_chars": 60, "forbidden_strings": []},
        ref="serve.py#/dynamic/meta-refresh", sha=None,
        notes="A meta refresh is not an HTTP redirect; a reader that ignores it gets the hop page.",
    ))
    cases.append(base(
        "RC-FECH-LAZY-021", "fetch", "development", "synthetic", "de", "de",
        {"url": "http://127.0.0.1:18090/dynamic/lazy", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "lazy_loading", "requires_js": True,
         "expected_text_markers": ["Braunfeld", "Esslingen"],
         "expected_emails": [], "expected_persons": [], "expected_links": [],
         "min_text_chars": 40, "forbidden_strings": ["Wird geladen"]},
        ref="serve.py#/dynamic/lazy", sha=None,
        notes="Rows appear after a timer; the reader must wait for them.",
    ))
    cases.append(base(
        "RC-FECH-PAGE-022", "fetch", "development", "synthetic", "nl", "nl",
        {"url": "http://127.0.0.1:18090/dynamic/page/2", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "pagination", "requires_js": False,
         "expected_text_markers": ["Pagina 2", "Zuiderzee Techniek"],
         "expected_emails": [], "expected_persons": [], "expected_links": [],
         "min_text_chars": 50, "forbidden_strings": []},
        ref="serve.py#/dynamic/page/2", sha=None, notes="Second page of a paginated list.",
    ))
    # PDF: link page + download
    cases.append(base(
        "RC-FECH-PDFLINK-023", "fetch", "development", "synthetic", "de", "de",
        {"url": "http://127.0.0.1:18090/dynamic/pdf-link", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "pdf_link", "requires_js": False,
         "expected_text_markers": ["Produktkatalog", "PDF"],
         "expected_emails": [], "expected_persons": [],
         "expected_links": ["/dynamic/catalogue.pdf"], "min_text_chars": 40,
         "forbidden_strings": []},
        ref="serve.py#/dynamic/pdf-link", sha=None,
        notes="The page is HTML; the reader must keep the link to the catalogue.",
    ))
    cases.append(base(
        "RC-FECH-PDF-024", "fetch", "development", "synthetic", "de", "de",
        {"url": "http://127.0.0.1:18090/dynamic/catalogue.pdf", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "pdf_text", "requires_js": False,
         "expected_text_markers": ["Braunfeld", "CNC"],
         "expected_emails": [], "expected_persons": [], "expected_links": [],
         "min_text_chars": 40, "forbidden_strings": []},
        ref="serve.py#/dynamic/catalogue.pdf", sha=None,
        notes="A real PDF with a text layer, served as application/pdf.",
    ))
    # failure shapes
    cases.append(base(
        "RC-FECH-SHELL-025", "fetch", "development", "synthetic", "gb", "en",
        {"url": "http://127.0.0.1:18090/dynamic/spa", "timeout_seconds": 30,
         "expected_outcome": "no_content", "final_url_expected": None},
        {"page_kind": "empty_spa", "requires_js": True, "expected_text_markers": [],
         "expected_emails": [], "expected_persons": [], "expected_links": [],
         "min_text_chars": 1, "forbidden_strings": ["Loading"]},
        ref="serve.py#/dynamic/spa", sha=None,
        notes="An empty SPA shell. A reader that reports success here is wrong.",
    ))
    cases.append(base(
        "RC-FECH-404-026", "fetch", "development", "synthetic", "gb", "en",
        {"url": "http://127.0.0.1:18090/dynamic/missing-page", "timeout_seconds": 30,
         "expected_outcome": "no_content", "final_url_expected": None},
        {"page_kind": "http_error", "requires_js": False, "expected_text_markers": [],
         "expected_emails": [], "expected_persons": [], "expected_links": [],
         "min_text_chars": 1, "forbidden_strings": []},
        ref="serve.py#/dynamic/missing-page", sha=None,
        notes="HTTP 404. The only correct outcome is 'no usable content'.",
    ))
    cases.append(base(
        "RC-FECH-INVALID-027", "fetch", "development", "synthetic", "us", "en",
        {"url": "http://127.0.0.1:18090/dynamic/garbage", "timeout_seconds": 30,
         "expected_outcome": "no_content", "final_url_expected": None},
        {"page_kind": "invalid_content", "requires_js": False, "expected_text_markers": [],
         "expected_emails": [], "expected_persons": [], "expected_links": [],
         "min_text_chars": 1, "forbidden_strings": []},
        ref="serve.py#/dynamic/garbage", sha=None,
        notes="Binary garbage with a text/html content type.",
    ))
    # wrong language / duplicate / stale
    cases.append(base(
        "RC-FECH-WRONGLANG-028", "fetch", "development", "synthetic", "de", "de",
        {"url": "http://127.0.0.1:18090/dynamic/wrong-language", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "wrong_language", "requires_js": False,
         "expected_text_markers": ["Kranich Software", "ERP"],
         "expected_emails": ["info@kranich-sw.example"],
         "expected_persons": [], "expected_links": [], "min_text_chars": 60,
         "forbidden_strings": []},
        ref="serve.py#/dynamic/wrong-language", sha=None,
        notes="Declares lang=en but the body is German; the text is still the right company.",
    ))
    cases.append(base(
        "RC-FECH-DUP-029", "fetch", "development", "synthetic", "gb", "en",
        {"url": "http://127.0.0.1:18090/dynamic/duplicate", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "duplicate_content", "requires_js": False,
         "expected_text_markers": ["Northwind Marine"],
         "expected_emails": ["info@northwindmarine.example"],
         "expected_persons": [], "expected_links": [], "min_text_chars": 80,
         "forbidden_strings": []},
        ref="serve.py#/dynamic/duplicate", sha=None,
        notes="Same body as northwind-marine/about.html; deduplication is a later stage's job.",
    ))
    cases.append(base(
        "RC-FECH-STALE-030", "fetch", "development", "synthetic", "ie", "en",
        {"url": "http://127.0.0.1:18090/dynamic/stale", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "stale_content", "requires_js": False,
         "expected_text_markers": ["Harborline Foods", "2019"],
         "expected_emails": ["sales@harborlinefoods.example"],
         "expected_persons": [], "expected_links": [], "min_text_chars": 60,
         "forbidden_strings": []},
        ref="serve.py#/dynamic/stale", sha=None,
        notes="Last updated in 2019. The reader gets the text; freshness is judged downstream.",
    ))
    # JS-rendered contact block, an HTTP 410, and non-text bytes: three shapes
    # where "did the reader get content?" is the whole question.
    cases.append(base(
        "RC-FECH-JSRENDER-031", "fetch", "validation", "synthetic", "de", "de",
        {"url": "http://127.0.0.1:18090/dynamic/js-rendered", "timeout_seconds": 30,
         "expected_outcome": "content", "final_url_expected": None},
        {"page_kind": "javascript_rendered", "requires_js": True,
         "expected_text_markers": ["Braunfeld Maschinenbau GmbH"],
         "expected_emails": ["info@braunfeld-mb.example"],
         "expected_persons": [], "expected_links": [],
         "min_text_chars": 30, "forbidden_strings": ["Wird geladen"]},
        ref="serve.py#/dynamic/js-rendered", sha=None,
        notes="The contact block exists only after the script runs.",
    ))
    cases.append(base(
        "RC-FECH-410-032", "fetch", "validation", "synthetic", "gb", "en",
        {"url": "http://127.0.0.1:18090/dynamic/not-found", "timeout_seconds": 30,
         "expected_outcome": "no_content", "final_url_expected": None},
        {"page_kind": "not_found", "requires_js": False, "expected_text_markers": [],
         "expected_emails": [], "expected_persons": [], "expected_links": [],
         "min_text_chars": 1, "forbidden_strings": []},
        ref="serve.py#/dynamic/not-found", sha=None,
        notes="HTTP 410. A removed resource is not content.",
    ))
    cases.append(base(
        "RC-FECH-BINARY-033", "fetch", "validation", "synthetic", "us", "en",
        {"url": "http://127.0.0.1:18090/dynamic/binary", "timeout_seconds": 30,
         "expected_outcome": "no_content", "final_url_expected": None},
        {"page_kind": "binary_garbage", "requires_js": False, "expected_text_markers": [],
         "expected_emails": [], "expected_persons": [], "expected_links": [],
         "min_text_chars": 1, "forbidden_strings": []},
        ref="serve.py#/dynamic/binary", sha=None,
        notes="image/png bytes. Decoding them as text would be a false success.",
    ))
    return cases


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _extraction_case(index: int, slug: str, page_name: str, *, split: str = "development") -> dict:
    company = COMPANIES[slug]
    ref = page(slug, page_name)
    visible_emails, visible_people, _ = _page_facts(slug, page_name)
    people = []
    # A contact block lists a name next to an address, not a role. Claiming a
    # role there would be gold asserting something the page never says.
    for person in ([] if page_name == "contact.html" else visible_people):
        entry = next((p for p in company.get("team", []) if p["name"] == person["name"]), None)
        people.append({
            "name": person["name"],
            "role": person["role"],
            "company": company["name"],
            "decision_maker": bool(entry and entry["decision_maker"]),
            "evidence_text": f"{person['name']} — {person['role']}",
        })
    emails = []
    for address in visible_emails:
        person = next((p["name"] for p in company.get("team", [])
                       if source.personal_email(company, p["name"]) == address), None)
        emails.append({"value": address, "kind": "personal" if person else "generic",
                       "person": person, "evidence_text": address})
    no_personal = not any(e["kind"] == "personal" for e in emails)
    missing = []
    if no_personal:
        missing.append("personal email not published")
    if not emails:
        missing.append("no address at all; the page offers a contact form")
    return base(
        f"RC-EXTR-{index:03d}", "extraction",
        split, "synthetic", company["country"], company["lang"],
        {"snapshot_path": ref["path"], "source_url": f"http://127.0.0.1:18090/{slug}/{page_name}",
         "company": {"name": company["name"], "domain": company["domain"]}},
        {
            "companies": [{"name": company["name"], "domain": company["domain"]}],
            "people": people,
            "emails": emails,
            "expect_no_personal_email": no_personal,
            "forbidden_emails": [],
            "missing_evidence": missing,
        },
        ref=ref["path"], sha=ref["sha256"],
        notes=f"Snapshot is the {page_name} page of a fictional company.",
    )


def build_extraction() -> list[dict]:
    # Group A: team pages that publish a personal address (10).
    group_a = [s for s in ORDER if s not in source.NO_PERSONAL_EMAIL_SLUGS and COMPANIES[s].get("team")]
    # Group B: team pages that publish no personal address (10) -- the negatives.
    group_b = [s for s in ORDER if s in source.NO_PERSONAL_EMAIL_SLUGS and COMPANIES[s].get("team")]
    picked_a, picked_b = group_a[:10], group_b[:10]
    # Group C: contact pages, from companies used by neither team group.
    group_c = [s for s in ORDER if s not in picked_a and s not in picked_b]

    if len(picked_a) < 10 or len(picked_b) < 10 or len(group_c) < 10:
        raise SystemExit(f"groups too small: A={len(picked_a)} B={len(picked_b)} C={len(group_c)}")

    picks = ([(s, "team.html") for s in picked_a]
             + [(s, "team.html") for s in picked_b]
             + [(s, "contact.html") for s in group_c[:10]])
    cases = []
    for index, (slug, page_name) in enumerate(picks, start=1):
        split = "development" if index <= 20 else "holdout"
        cases.append(_extraction_case(index, slug, page_name, split=split))
    return cases


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------

ICP = {
    "countries": ["GB", "NL", "IE"],
    "industries": ["marine equipment", "marine engineering", "shipyard"],
    "employees_min": 20,
    "employees_max": 500,
    "target_roles": ["Managing Director", "Head of Procurement", "Head of Partnerships"],
    "exclusions": ["consumer boat shops", "furniture retailers"],
}

QUALIFICATION_SPECS = [
    # (company slug, expected status, icp_match, relevance, reason, missing)
    ("northwind-marine", "strong", True, 3, "Marine equipment in GB, 180 staff, director with purchasing authority.", []),
    ("van-dijk-watersport", "strong", True, 3, "Marine equipment in NL, 48 staff, owner and purchasing lead.", []),
    ("zuiderzee-techniek", "strong", True, 3, "Marine engineering in NL, 175 staff.", []),
    ("harborline-foods", "weak", False, 1, "Food distribution, not marine equipment; adjacent only by cold-chain.", ["industry mismatch"]),
    ("cedar-finch", "excluded", False, 0, "Furniture retailer is an explicit exclusion.", ["excluded industry"]),
    ("kestrel-robotics", "not_qualified", False, 0, "Warehouse automation in the US, outside the ICP countries.", ["wrong country", "wrong industry"]),
    ("braunfeld-maschinenbau", "not_qualified", False, 0, "German machine tools; outside the marine ICP.", ["wrong country", "wrong industry"]),
    ("vlaamse-zorg", "not_qualified", False, 0, "Elderly care in Belgium; outside the ICP.", ["wrong industry", "wrong country"]),
    ("atlas-field-services", "weak", False, 1, "Industrial maintenance in the US; adjacent capability, wrong country.", ["wrong country"]),
    ("solstice-renewables", "partial", False, 2, "Solar installation in IE; relevant market, wrong industry.", ["industry mismatch"]),
    ("rowan-pike", "weak", False, 1, "Architecture practice in GB; right country, unrelated industry.", ["industry mismatch"]),
    ("amstel-data", "weak", False, 1, "Data centres in NL; right country, unrelated industry.", ["industry mismatch"]),
    ("delta-ridge-mining", "not_qualified", False, 0, "Mining equipment in the US; outside the ICP.", ["wrong country", "wrong industry"]),
    ("maasvallei-transport", "weak", False, 1, "Road transport in BE; only a logistics adjacency, and outside the ICP countries.", ["wrong country"]),
    ("fluweelmode", "excluded", False, 0, "Fashion wholesale; explicit exclusion.", ["excluded industry"]),
    ("lindeboom-meubelen", "excluded", False, 0, "Furniture manufacturer; explicit exclusion.", ["excluded industry"]),
    ("nordlicht-energie", "not_qualified", False, 0, "Wind energy in DE; outside the marine ICP in both industry and country.", ["wrong industry", "wrong country"]),
    ("kranich-software", "insufficient_evidence", False, 0, "No page states the industry or headcount.", ["industry unverified", "headcount unverified"]),
    ("hollands-groen", "weak", False, 1, "Dutch horticulture supplier; right country, unrelated industry.", ["industry mismatch"]),
    ("van-dijk-watersport", "duplicate", True, 3, "Same domain as RC-QUAL-002; duplicate of an existing prospect.", []),
    # Three outcomes the other twenty cannot express without inventing them:
    # the record is not a company, the domain publishes nothing, and the
    # company's own pages disagree with each other.
    ("maritime-suppliers-directory", "out_of_scope", False, 0,
     "A trade directory: it sells listings about marine suppliers, it does not supply equipment.",
     ["not a company record"]),
    ("harbourline-marine", "unverified_identity", False, 0,
     "The domain serves one placeholder page, so neither the company nor any fact about it "
     "can be confirmed.",
     ["identity unverified", "site not live"]),
    ("nordsee-maritim", "needs_review", False, 0,
     "about.html states 180 employees while team.html describes a founding team of 25; the "
     "headcount conflict has to be resolved by a human before the prospect is judged.",
     ["conflicting facts"]),
]

#: Prospect fields that only the three edge-type records carry. Kept out of the
#: other twenty cases so their bytes (and their committed gold) do not move.
QUALIFICATION_PROSPECT_EXTRAS = {
    "maritime-suppliers-directory": {
        "record_type": "directory",
        "evidence_notes": {
            "ev-msd-about": "The directory itself does not design, stock or supply equipment; "
                            "it sells listings.",
        },
    },
    "harbourline-marine": {
        "domain_status": "placeholder",
        "evidence_notes": {
            "ev-harbourline-index": "This domain is registered, but no company website has "
                                    "been published yet.",
        },
    },
    "nordsee-maritim": {
        "conflicts": ["about.html says 180 employees; team.html says a founding team of 25"],
        "evidence_notes": {
            "ev-nordsee-about": "Employees: 180.",
            "ev-nordsee-team": "Our founding team of 25 people still answers every enquiry.",
        },
    },
}

QUALIFICATION_EVIDENCE = {
    "northwind-marine": ["ev-northwind-about", "ev-northwind-team"],
    "van-dijk-watersport": ["ev-vandijk-about"],
    "zuiderzee-techniek": ["ev-zuiderzee-about"],
    "harborline-foods": ["ev-harborline-about"],
    "cedar-finch": ["ev-cedar-about"],
    "kestrel-robotics": ["ev-kestrel-about"],
    "braunfeld-maschinenbau": ["ev-braunfeld-about"],
    "vlaamse-zorg": ["ev-vlaamse-about"],
    "atlas-field-services": ["ev-atlas-about"],
    "solstice-renewables": ["ev-solstice-about"],
    "rowan-pike": ["ev-rowan-about"],
    "amstel-data": ["ev-amstel-about"],
    "delta-ridge-mining": ["ev-deltamining-about"],
    "maasvallei-transport": ["ev-maasvallei-about"],
    "fluweelmode": ["ev-fluweel-about"],
    "lindeboom-meubelen": ["ev-lindeboom-about"],
    "nordlicht-energie": ["ev-nordlicht-index"],
    "kranich-software": [],
    "hollands-groen": ["ev-hollands-about"],
    "maritime-suppliers-directory": ["ev-msd-about"],
    "harbourline-marine": ["ev-harbourline-index"],
    "nordsee-maritim": ["ev-nordsee-about", "ev-nordsee-team"],
}


def build_qualification() -> list[dict]:
    cases = []
    for index, spec in enumerate(QUALIFICATION_SPECS, start=1):
        slug, status, match, relevance, reason, missing = spec
        company = COMPANIES[slug]
        evidence = QUALIFICATION_EVIDENCE.get(slug, [])
        prospect = {
            "name": company["name"], "domain": company["domain"],
            "country": company["country"], "industry": company["industry"],
            "employees": company["employees"], "evidence": evidence,
        }
        prospect.update(QUALIFICATION_PROSPECT_EXTRAS.get(slug, {}))
        cases.append(base(
            f"RC-QUAL-{index:03d}", "qualification",
            "development" if index <= 14 else "validation", "synthetic",
            company["country"], company["lang"],
            {
                "prospect": prospect,
                "icp": ICP,
            },
            {
                "qualification_status": status, "icp_match": match,
                "relevance": relevance, "reason": reason,
                "supporting_evidence": evidence, "missing_evidence": missing,
            },
            ref=f"corpus/sites/{slug}/meta.json",
            sha=page(slug, "meta.json")["sha256"],
            notes="Synthetic prospect judged against the marine ICP.",
        ))
    return cases


# ---------------------------------------------------------------------------
# Identity / dedup
# ---------------------------------------------------------------------------

IDENTITY_SPECS = [
    # (id, records, clusters, relations, note)
    ("RC-IDNT-NAME-001",
     [{"record_id": "r1", "name": "Northwind Marine Ltd", "domain": "northwindmarine.example", "country": "GB"},
      {"record_id": "r2", "name": "Northwind Marine Limited", "domain": "northwindmarine.example", "country": "GB"}],
     [["r1", "r2"]], [], "Legal suffix variant, same domain."),
    ("RC-IDNT-ABBR-002",
     [{"record_id": "r1", "name": "Delta Ridge Mining Equipment", "domain": "deltaridgemining.example", "country": "US"},
      {"record_id": "r2", "name": "DRM Equipment", "domain": "deltaridgemining.example", "country": "US"}],
     [["r1", "r2"]], [], "Abbreviation plus same domain."),
    ("RC-IDNT-PARENT-003",
     [{"record_id": "r1", "name": "Delta Ridge Holdings", "domain": "deltaridge.example", "country": "US"},
      {"record_id": "r2", "name": "Delta Ridge Mining Equipment", "domain": "deltaridgemining.example", "country": "US"}],
     [["r1"], ["r2"]], [{"parent": "r1", "child": "r2", "kind": "parent_of"}],
     "Different legal entities, different domains, explicit ownership."),
    ("RC-IDNT-BRANCH-004",
     [{"record_id": "r1", "name": "Northwind Marine Ltd", "domain": "northwindmarine.example", "country": "GB"},
      {"record_id": "r2", "name": "Northwind Marine B.V.", "domain": "northwindmarine.nl.example", "country": "NL"}],
     [["r1"], ["r2"]], [{"parent": "r1", "child": "r2", "kind": "branch_of"}],
     "Same group, different country entity; a branch is not a duplicate."),
    ("RC-IDNT-HOMONYM-005",
     [{"record_id": "r1", "name": "Meridian Health Partners", "domain": "meridianhealth.example", "country": "US"},
      {"record_id": "r2", "name": "Meridian Health Partners", "domain": "meridianhealth.co.uk", "country": "GB"}],
     [["r1"], ["r2"]], [], "Same name, different countries and domains: not the same organisation."),
    ("RC-IDNT-ROLE-006",
     [{"record_id": "r1", "name": "Julia Brandt", "domain": "kranich-sw.example", "country": "DE"},
      {"record_id": "r2", "name": "Julia Brandt", "domain": "kranich-sw.example", "country": "DE"}],
     [["r1", "r2"]], [], "Same person recorded twice under different roles at one company."),
    ("RC-IDNT-SRC-007",
     [{"record_id": "r1", "name": "Braunfeld Maschinenbau GmbH", "domain": "braunfeld-mb.example", "country": "DE"},
      {"record_id": "r2", "name": "Braunfeld Maschinenbau", "domain": "braunfeld-mb.example", "country": "DE"},
      {"record_id": "r3", "name": "BRAUNFELD MASCHINENBAU GMBH", "domain": "braunfeld-mb.example", "country": "DE"}],
     [["r1", "r2", "r3"]], [], "Three sources, one company, case differences only."),
    ("RC-IDNT-DOMAIN-008",
     [{"record_id": "r1", "name": "Kempen Bouw", "domain": "kempenbouw.example", "country": "BE"},
      {"record_id": "r2", "name": "Kempen Bouw B.V.", "domain": "www.kempenbouw.example", "country": "BE"}],
     [["r1", "r2"]], [], "www. prefix is not a different company."),
    ("RC-IDNT-COUNTRY-009",
     [{"record_id": "r1", "name": "Lindenhof Logistik GmbH", "domain": "lindenhof-logistik.example", "country": "DE"},
      {"record_id": "r2", "name": "Lindenhof Logistik GmbH", "domain": "lindenhof-logistik.at", "country": "AT"}],
     [["r1"], ["r2"]], [], "Same name in two countries: an AT branch must be confirmed, not assumed."),
    ("RC-IDNT-ALIAS-010",
     [{"record_id": "r1", "name": "Cedar and Finch", "domain": "cedarandfinch.example", "country": "GB", "aliases": ["Cedar & Finch"]},
      {"record_id": "r2", "name": "Cedar & Finch", "domain": "cedarandfinch.example", "country": "GB"}],
     [["r1", "r2"]], [], "Punctuation variant of the trading name."),
    ("RC-IDNT-GROUP-011",
     [{"record_id": "r1", "name": "Delta Ridge Holdings", "domain": "deltaridge.example", "country": "US"},
      {"record_id": "r2", "name": "Delta Ridge Mining Equipment", "domain": "deltaridgemining.example", "country": "US"},
      {"record_id": "r3", "name": "Delta Ridge Rail", "domain": "deltaridgerail.example", "country": "US"}],
     [["r1"], ["r2"], ["r3"]],
     [{"parent": "r1", "child": "r2", "kind": "parent_of"},
      {"parent": "r1", "child": "r3", "kind": "parent_of"}],
     "One parent, two subsidiaries: grouping must not collapse them into one prospect."),
    ("RC-IDNT-LANG-012",
     [{"record_id": "r1", "name": "Schwarzwald Präzision GmbH", "domain": "sw-praezision.example", "country": "DE"},
      {"record_id": "r2", "name": "Schwarzwald Praezision GmbH", "domain": "sw-praezision.example", "country": "DE"}],
     [["r1", "r2"]], [], "Transliterated umlaut at the same domain."),
    ("RC-IDNT-NODOMAIN-013",
     [{"record_id": "r1", "name": "Amberlight Media", "domain": None, "country": "IE"},
      {"record_id": "r2", "name": "Amberlight Media", "domain": None, "country": "IE"}],
     [["r1", "r2"]], [], "No domain on either record; name and country are the only evidence."),
    ("RC-IDNT-WRONG-014",
     [{"record_id": "r1", "name": "Van Dijk Watersport", "domain": "vandijkwatersport.example", "country": "NL"},
      {"record_id": "r2", "name": "De Vries Luchtbehandeling", "domain": "devrieslucht.example", "country": "NL"}],
     [["r1"], ["r2"]], [], "Two unrelated Dutch companies; a shared country is not identity."),
    ("RC-IDNT-MIXED-015",
     [{"record_id": "r1", "name": "Brabant Food Group B.V.", "domain": "brabantfood.example", "country": "NL"},
      {"record_id": "r2", "name": "Brabant Food Group", "domain": "brabantfood.example", "country": "NL"},
      {"record_id": "r3", "name": "Brabant Food Group", "domain": "brabantfood.nl", "country": "NL"},
      {"record_id": "r4", "name": "Hollands Groen B.V.", "domain": "hollandsgroen.example", "country": "NL"}],
     [["r1", "r2", "r3"], ["r4"]], [], "A cluster with three records plus a separate company."),
]


def build_identity() -> list[dict]:
    cases = []
    for index, (case_id, records, clusters, relations, note) in enumerate(IDENTITY_SPECS, start=1):
        cases.append(base(
            case_id, "identity",
            "development" if index <= 10 else "validation", "synthetic",
            "multi", "en",
            {"records": records},
            {"clusters": clusters, "relations": relations},
            ref=f"build_datasets.py#IDENTITY_SPECS[{index - 1}]", sha=None,
            notes=note,
        ))
    return cases


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

EVIDENCE_SPECS = [
    # (id, slug, page, source_type, fields, entity_ref, stale_after, gold overrides, note)
    ("RC-EVID-OK-001", "northwind-marine", "about.html", "official_site",
     {"name": "Northwind Marine Ltd", "employees": "180"}, "northwind-marine", None, {}, "Valid snapshot."),
    ("RC-EVID-OK-002", "braunfeld-maschinenbau", "about.html", "official_site",
     {"name": "Braunfeld Maschinenbau GmbH", "employees": "320"}, "braunfeld-maschinenbau", None, {}, "Valid snapshot."),
    ("RC-EVID-OK-003", "van-dijk-watersport", "contact.html", "official_site",
     {"email": "info@vandijkwatersport.example"}, "van-dijk-watersport", None, {}, "Valid snapshot."),
    ("RC-EVID-OK-004", "kranich-software", "team.html", "official_site",
     {"person": "Julia Brandt", "role": "Leiterin Partnerschaften"}, "kranich-software", None, {}, "Valid snapshot."),
    ("RC-EVID-OK-005", "delta-ridge-holdings", "about.html", "official_site",
     {"name": "Delta Ridge Holdings"}, "delta-ridge-holdings", None, {}, "Valid snapshot."),
    ("RC-EVID-OK-006", "kempen-bouw", "contact.html", "official_site",
     {"email": "info@kempenbouw.example"}, "kempen-bouw", None, {}, "Valid snapshot."),
    ("RC-EVID-OK-007", "alpenblick-hotels", "about.html", "official_site",
     {"name": "Alpenblick Hotels AG", "employees": "600"}, "alpenblick-hotels", None, {}, "Valid snapshot."),
    ("RC-EVID-OK-008", "amstel-data", "team.html", "official_site",
     {"person": "Femke Bos", "role": "Hoofd Sales"}, "amstel-data", None, {}, "Valid snapshot."),
    ("RC-EVID-OK-009", "solstice-renewables", "index.html", "official_site",
     {"name": "Solstice Renewables"}, "solstice-renewables", None, {}, "Valid snapshot."),
    ("RC-EVID-OK-010", "lindenhof-logistik", "about.html", "official_site",
     {"industry": "logistics", "employees": "480"}, "lindenhof-logistik", None, {}, "Valid snapshot."),
    ("RC-EVID-STALE-011", "harborline-foods", "about.html", "official_site",
     {"name": "Harborline Foods"}, "harborline-foods", 365, {}, "Retrieved 2019; stale for the brief."),
    ("RC-EVID-STALE-012", "wienerwald-kueche", "about.html", "official_site",
     {"name": "Wienerwald Küche GmbH"}, "wienerwald-kueche", 180, {}, "Retrieved two years ago."),
    ("RC-EVID-HASH-013", "schwarzwald-praezision", "about.html", "official_site",
     {"name": "Schwarzwald Präzision GmbH"}, "schwarzwald-praezision", None,
     {"sha256_matches": False}, "Hash does not match the snapshot: evidence must be rejected."),
    ("RC-EVID-HASH-014", "vlaamse-zorg", "contact.html", "official_site",
     {"email": "info@vlaamsezorg.example"}, "vlaamse-zorg", None,
     {"sha256_matches": False}, "Tampered record."),
    ("RC-EVID-MISSING-015", "brabant-food-group", "about.html", "official_site",
     {"name": "Brabant Food Group B.V."}, "brabant-food-group", None,
     {"snapshot_exists": False}, "Snapshot file is missing from the repository."),
]


def build_evidence() -> list[dict]:
    cases = []
    for index, spec in enumerate(EVIDENCE_SPECS, start=1):
        (case_id, slug, page_name, source_type, fields, entity_ref,
         stale_after, overrides, note) = spec
        ref = page(slug, page_name)
        snapshot_path = ref["path"] if overrides.get("snapshot_exists", True) else "corpus/sites/does-not-exist.html"
        recorded_sha = ref["sha256"]
        if overrides.get("sha256_matches") is False:
            recorded_sha = "0" * 64
        retrieved_at = "2019-04-02T10:00:00+08:00" if index in (11, 12) else "2026-11-18T12:00:00+08:00"
        cases.append(base(
            case_id, "evidence",
            "development" if index <= 10 else "validation", "synthetic",
            COMPANIES[slug]["country"], COMPANIES[slug]["lang"],
            {"evidence": {
                "evidence_id": case_id, "source_url": f"http://127.0.0.1:18090/{slug}/{page_name}",
                "source_type": source_type, "retrieved_at": retrieved_at,
                "snapshot_path": snapshot_path, "sha256": recorded_sha,
                "extracted_fields": fields, "entity_refs": [entity_ref],
                "upstream_refs": [f"prospect:{entity_ref}"],
            }},
            {"snapshot_exists": overrides.get("snapshot_exists", True),
             # A hash cannot match bytes that are not there, and fields cannot
             # be traced through a missing file.
             "sha256_matches": overrides.get("sha256_matches",
                                             overrides.get("snapshot_exists", True)),
             "fields_traceable": (list(fields.values())
                                  if overrides.get("snapshot_exists", True) else []),
             "entity_ref": entity_ref, "stale_after_days": stale_after,
             "contradicts": []},
            ref=ref["path"], sha=ref["sha256"], notes=note,
        ))
    return cases


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------

CHANNEL_SPECS = [
    ("RC-CHAN-MARINE-001", "RC-BRIEF-001",
     [("direct_email", "Direct email to a named director", "Named directors of 20-500 staff marine firms read their own inbox.", 2, 1, 0.45),
      ("trade_directory", "Trade association member directory", "Association members self-identify by industry.", 2, 1, 0.25),
      ("trade_fair", "Industry trade fair", "High trust, slow, expensive.", 3, 3, 0.15),
      ("cold_linkedin", "LinkedIn outreach", "Constrained by platform terms; not an approved channel.", 2, 1, 0.10),
      ("mass_mail", "Purchased email list", "No consent; out of policy.", 3, 1, 0.05)],
     ["direct_email", "trade_directory"], ["direct_email", "trade_directory"], ["cold_linkedin", "mass_mail"]),
    ("RC-CHAN-MARINE-002", "RC-BRIEF-001",
     [("trade_fair", "Industry trade fair", "High trust, slow, expensive.", 3, 3, 0.2),
      ("direct_email", "Direct email to a named director", "Named directors read their own inbox.", 2, 1, 0.5),
      ("trade_directory", "Trade association member directory", "Self-identified members.", 2, 1, 0.3)],
     ["direct_email", "trade_directory"], ["direct_email", "trade_directory", "trade_fair"], ["mass_mail"]),
    ("RC-CHAN-MACHINE-003", "RC-BRIEF-002",
     [("german_email", "Direct email in German to the Geschäftsführung", "Mittelstand decision makers read German email.", 2, 1, 0.5),
      ("association_event", "Industry association event", "VDW-style events reach technical buyers.", 2, 3, 0.3),
      ("trade_press", "Specialist trade press", "Reaches engineers, not buyers.", 2, 2, 0.2)],
     ["german_email", "association_event"], ["german_email", "association_event", "trade_press"], []),
    ("RC-CHAN-MACHINE-004", "RC-BRIEF-002",
     [("cold_call", "Cold call the switchboard", "Gatekeepers block technical buyers.", 1, 1, 0.3),
      ("german_email", "Direct email in German to the Geschäftsführung", "Direct to the decision maker.", 2, 1, 0.5),
      ("association_event", "Industry association event", "Warm introduction.", 2, 3, 0.2)],
     ["german_email", "association_event"], ["german_email", "association_event", "cold_call"], []),
    ("RC-CHAN-CREATIVE-005", "RC-BRIEF-003",
     [("founder_email", "Direct email to the founder", "Small studios are founder-led.", 2, 1, 0.5),
      ("production_directory", "Production industry directory", "Self-listed studios with credits.", 2, 1, 0.3),
      ("festival", "Film festival network", "Warm but seasonal.", 2, 3, 0.2)],
     ["founder_email", "production_directory"], ["founder_email", "production_directory", "festival"], []),
    ("RC-CHAN-CREATIVE-006", "RC-BRIEF-003",
     [("festival", "Film festival network", "Warm but seasonal.", 2, 3, 0.3),
      ("founder_email", "Direct email to the founder", "Founder-led studios.", 2, 1, 0.5),
      ("production_directory", "Production industry directory", "Self-listed studios.", 2, 1, 0.2)],
     ["founder_email", "production_directory"], ["founder_email", "production_directory", "festival"], []),
]


def build_channel() -> list[dict]:
    cases = []
    for index, (case_id, brief_ref, channels, ranked, acceptable, rejected) in enumerate(CHANNEL_SPECS, start=1):
        cases.append(base(
            case_id, "channel", "development" if index <= 4 else "validation",
            "synthetic", "multi", "en",
            {"brief_ref": brief_ref,
             "channels": [
                 {"channel_id": cid, "name": name, "hypothesis": hyp, "reach": reach,
                  "cost": cost, "prior": prior}
                 for cid, name, hyp, reach, cost, prior in channels
             ]},
            {"ranked_channels": ranked, "acceptable_set": acceptable, "rejected": rejected},
            ref="build_datasets.py#CHANNEL_SPECS", sha=None,
            notes="Channel hypotheses for a synthetic brief; ranking gold is an editorial judgement.",
        ))
    return cases


# ---------------------------------------------------------------------------
# E2E
# ---------------------------------------------------------------------------

E2E_SPECS = [
    # (case_id, brief_ref, corpus slice, {slug: expected status})
    ("RC-E2E-MARINE-001", "RC-BRIEF-001", "marine", {
        "northwind-marine": "strong",
        "van-dijk-watersport": "strong",
        "zuiderzee-techniek": "strong",
        "harborline-foods": "weak",
        "atlas-field-services": "weak",
        "cedar-finch": "excluded",
        "kestrel-robotics": "not_qualified",
        "rowan-pike": "weak",
        "solstice-renewables": "partial",
        "hollands-groen": "weak",
        "maasvallei-transport": "weak",
        "fluweelmode": "excluded",
    }),
    ("RC-E2E-MACHINE-002", "RC-BRIEF-002", "machine", {
        "braunfeld-maschinenbau": "strong",
        "schwarzwald-praezision": "partial",
        "elbwerk-automation": "strong",
        "adlerwerk-werkzeuge": "partial",
        "wienerwald-kueche": "excluded",
        "donau-consulting": "excluded",
        "alpenblick-hotels": "excluded",
        # DE/AT is the machine brief's market, so a German company in an
        # unrelated industry is "weak" by the same convention the qualification
        # dataset uses (right country, unrelated industry), not "not_qualified".
        "lindenhof-logistik": "weak",
        "kranich-software": "weak",
        "nordlicht-energie": "weak",
        "falkenried-kliniken": "weak",
        "rosenheim-papier": "weak",
    }),
    ("RC-E2E-CREATIVE-003", "RC-BRIEF-003", "creative", {
        "quarry-lane-studios": "strong",
        "amberlight-media": "strong",
        "tideswell-publishing": "partial",
        "ironbark-analytics": "excluded",
        "rowan-pike": "weak",
        "cedar-finch": "weak",
        "kestrel-robotics": "not_qualified",
        "solstice-renewables": "weak",
        "northwind-marine": "weak",
        "harborline-foods": "weak",
        "vlaamse-zorg": "not_qualified",
        "donau-consulting": "not_qualified",
    }),
]

E2E_GAPS = ["personal email not published for some prospects",
            "contact form only, no address, for some companies"]


def build_e2e() -> list[dict]:
    cases = []
    for case_id, brief_ref, corpus, statuses in E2E_SPECS:
        expected = []
        for slug, status in statuses.items():
            company = COMPANIES[slug]
            expected.append({
                "company": company["name"],
                "expected_status": status,
                # Whether a person and a personal address are actually published
                # is a fact about the corpus, not an editorial judgement.
                "has_person": bool(company.get("team")),
                "has_email": any(source.personal_email(company, p["name"])
                                 for p in company.get("team", [])),
            })
        cases.append(base(
            case_id, "e2e", "development", "synthetic", "multi", "en",
            {"brief_ref": brief_ref, "max_prospects": 12, "corpus": corpus},
            {"expected_prospects": expected, "expected_gaps": E2E_GAPS,
             "forbidden_claims": ["ISO 9001 certified", "exclusive distributor", "market leader",
                                  "award-winning", "TÜV certified"]},
            ref="build_datasets.py#E2E_SPECS", sha=None,
            notes="Mock E2E over the synthetic corpus; never a real market finding.",
        ))
    return cases


def main() -> int:
    write("brief", build_briefs())
    write("search", build_search())
    write("fetch", build_fetch())
    write("extraction", build_extraction())
    write("qualification", build_qualification())
    write("identity", build_identity())
    write("evidence", build_evidence())
    write("channel", build_channel())
    write("e2e", build_e2e())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

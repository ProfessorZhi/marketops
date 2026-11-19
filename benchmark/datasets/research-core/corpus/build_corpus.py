#!/usr/bin/env python3
"""Render the synthetic corpus to static HTML plus a search index.

Deterministic by construction: no timestamps, no random ids, keys sorted. Running
it twice must produce byte-identical files — the dataset checksum and the
evidence hashes depend on that, and ``tests/test_research_corpus.py`` fails if
they do not.

Output (all under ``corpus/``):

    sites/<slug>/index.html     homepage
    sites/<slug>/about.html     company history and facts
    sites/<slug>/team.html      people (JavaScript-rendered for three slugs)
    sites/<slug>/contact.html   addresses, or a contact form when that is all
                                the company publishes
    sites/<slug>/meta.json      the facts, for mock providers and the E2E corpus
    search_index.json           what the mock search engine knows
    MANIFEST.json               sha256 + byte length of every generated file

Usage:
    python benchmark/datasets/research-core/corpus/build_corpus.py
"""

from __future__ import annotations

import hashlib
import html
import json
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import source  # noqa: E402

SITES = HERE / "sites"

NAV = {"en": [("Home", "index.html"), ("About", "about.html"), ("Team", "team.html"), ("Contact", "contact.html")],
       "de": [("Startseite", "index.html"), ("Über uns", "about.html"), ("Team", "team.html"), ("Kontakt", "contact.html")],
       "nl": [("Home", "index.html"), ("Over ons", "about.html"), ("Team", "team.html"), ("Contact", "contact.html")]}

LABELS = {
    "en": {"about": "About", "team": "Our team", "contact": "Contact", "founded": "Founded",
           "employees": "Employees", "industry": "Industry", "parent": "Parent company",
           "address": "Registered office", "phone": "Phone", "form": "Contact form",
           "form_note": "Use the form below and we will reply within two working days.",
           "send": "Send", "loading": "Loading…"},
    "de": {"about": "Über uns", "team": "Unser Team", "contact": "Kontakt", "founded": "Gegründet",
           "employees": "Mitarbeitende", "industry": "Branche", "parent": "Muttergesellschaft",
           "address": "Sitz", "phone": "Telefon", "form": "Kontaktformular",
           "form_note": "Nutzen Sie das Formular; wir antworten innerhalb von zwei Werktagen.",
           "send": "Senden", "loading": "Wird geladen…"},
    "nl": {"about": "Over ons", "team": "Ons team", "contact": "Contact", "founded": "Opgericht",
           "employees": "Medewerkers", "industry": "Sector", "parent": "Moederbedrijf",
           "address": "Vestigingsadres", "phone": "Telefoon", "form": "Contactformulier",
           "form_note": "Gebruik het formulier; wij reageren binnen twee werkdagen.",
           "send": "Versturen", "loading": "Laden…"},
}

STREETS = ["Harbour Road 14", "Industriestraße 7", "Havenweg 22", "Station Lane 3",
           "Werkstraße 19", "Kanaalstraat 8", "Dock Street 41", "Gewerbepark 5"]


def _page(company: dict, title: str, body: str) -> str:
    lang = company["lang"]
    nav = " ".join(
        f'<a href="{href}">{html.escape(label)}</a>' for label, href in NAV[lang]
    )
    return (
        "<!doctype html>\n"
        f'<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n"
        f'<meta name="description" content="{html.escape(company["description"][:120])}">\n'
        "</head>\n<body>\n"
        f"<header><nav>{nav}</nav></header>\n"
        f"<main>\n{body}\n</main>\n"
        f"<footer><p>&copy; 2026 {html.escape(company['name'])}.</p></footer>\n"
        "</body>\n</html>\n"
    )


def _address(company: dict) -> str:
    index = sum(ord(ch) for ch in company["slug"]) % len(STREETS)
    return f"{STREETS[index]}, {company['country']}"


def _index(company: dict) -> str:
    l10n = LABELS[company["lang"]]
    body = (
        f"<h1>{html.escape(company['name'])}</h1>\n"
        f"<p>{html.escape(company['description'])}</p>\n"
        f"<h2>{html.escape(l10n['contact'])}</h2>\n"
        f"<p>{html.escape(l10n['industry'])}: {html.escape(company['industry'])}. "
        f"{html.escape(l10n['employees'])}: {company['employees']}.</p>\n"
        f'<p><a href="contact.html">{html.escape(l10n["contact"])}</a></p>'
    )
    return _page(company, company["name"], body)


def _about(company: dict) -> str:
    l10n = LABELS[company["lang"]]
    parent = ""
    if company.get("parent"):
        parent = (f"<p>{html.escape(l10n['parent'])}: "
                  f"{html.escape(company['parent'])}.</p>\n")
    body = (
        f"<h1>{html.escape(l10n['about'])} — {html.escape(company['name'])}</h1>\n"
        f"<p>{html.escape(company['description'])}</p>\n"
        f"<p>{html.escape(l10n['founded'])}: {company['founded']}. "
        f"{html.escape(l10n['employees'])}: {company['employees']}. "
        f"{html.escape(l10n['industry'])}: {html.escape(company['industry'])}.</p>\n"
        f"<p>{html.escape(l10n['address'])}: {html.escape(_address(company))}.</p>\n"
        f"{parent}"
    )
    return _page(company, f"{l10n['about']} — {company['name']}", body)


def _team(company: dict) -> str:
    l10n = LABELS[company["lang"]]
    people = company.get("team", [])
    if company["slug"] in source.JS_TEAM_SLUGS:
        rows = "".join(
            f"<li><strong>{html.escape(p['name'])}</strong> — {html.escape(p['role'])}</li>"
            for p in people
        )
        body = (
            f"<h1>{html.escape(l10n['team'])}</h1>\n"
            f'<div id="people">{html.escape(l10n["loading"])}</div>\n'
            "<script>\n"
            "setTimeout(function () {\n"
            f"  document.getElementById('people').innerHTML = '<ul>{rows}</ul>';\n"
            "}, 300);\n"
            "</script>"
        )
    else:
        items = []
        for person in people:
            email = source.personal_email(company, person["name"])
            suffix = ""
            if email:
                suffix = f', <a href="mailto:{email}">{email}</a>'
            items.append(
                f"<li><strong>{html.escape(person['name'])}</strong> — "
                f"{html.escape(person['role'])}{suffix}</li>"
            )
        intro = ""
        if company.get("team_intro"):
            intro = f"<p>{html.escape(company['team_intro'])}</p>\n"
        body = (f"<h1>{html.escape(l10n['team'])}</h1>\n{intro}<ul>\n"
                + "\n".join(items) + "\n</ul>")
    return _page(company, f"{l10n['team']} — {company['name']}", body)


def _placeholder(company: dict) -> str:
    """A registered domain that serves nothing: no fact on it can be confirmed."""
    body = (
        f"<h1>{html.escape(company['name'])}</h1>\n"
        "<p>This domain is registered, but no company website has been published yet.</p>\n"
        "<p>No industry, headcount, team or contact details can be confirmed from this page.</p>"
    )
    return _page(company, company["name"], body)


def _contact(company: dict) -> str:
    l10n = LABELS[company["lang"]]
    address = html.escape(_address(company))
    phones = {"GB": "+44 20 7946 0101", "IE": "+353 1 555 0102", "US": "+1 202 555 0143",
              "DE": "+49 30 555 0199", "AT": "+43 1 555 0117", "NL": "+31 20 555 0121",
              "BE": "+32 2 555 0134"}
    phone = phones.get(company["country"], "+00 000 0000")

    parts = [f"<h1>{html.escape(l10n['contact'])} — {html.escape(company['name'])}</h1>"]
    parts.append(f"<h2>{html.escape(l10n['address'])}</h2>\n<p>{address}</p>")
    parts.append(f"<h2>{html.escape(l10n['phone'])}</h2>\n<p>{phone}</p>")

    emails = company["emails"]["generic"]
    if company["contact_method"] == "form":
        parts.append(
            f"<h2>{html.escape(l10n['form'])}</h2>\n"
            f"<p>{html.escape(l10n['form_note'])}</p>\n"
            '<form action="contact.html" method="post">\n'
            '  <label>Name <input name="name" type="text"></label>\n'
            '  <label>Message <textarea name="message"></textarea></label>\n'
            f'  <button type="submit">{html.escape(l10n["send"])}</button>\n'
            "</form>"
        )
    else:
        links = " ".join(
            f'<a href="mailto:{email}">{email}</a>,' for email in emails
        ).rstrip(",")
        parts.append(f"<h2>Email</h2>\n<p>{links}</p>")

    people_with_mail = [
        (p["name"], source.personal_email(company, p["name"]))
        for p in company.get("team", [])
        if source.personal_email(company, p["name"])
    ]
    if people_with_mail:
        rows = "".join(
            f'<p>{html.escape(name)}: <a href="mailto:{email}">{email}</a></p>'
            for name, email in people_with_mail
        )
        parts.append(f"<h2>{html.escape(l10n['team'])}</h2>\n{rows}")

    return _page(company, f"{l10n['contact']} — {company['name']}", "\n".join(parts))


def _tokens(text: str) -> list[str]:
    import re

    words = re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", text.lower())
    return sorted(set(words))


def build() -> dict:
    if SITES.exists():
        shutil.rmtree(SITES)
    SITES.mkdir(parents=True)

    index: dict[str, dict] = {}
    files: dict[str, dict] = {}

    for company in source.COMPANIES:
        slug = company["slug"]
        directory = SITES / slug
        directory.mkdir()
        if company.get("placeholder"):
            pages = {name: _placeholder(company) for name in
                     ("index.html", "about.html", "team.html", "contact.html")}
        else:
            pages = {
                "index.html": _index(company),
                "about.html": _about(company),
                "team.html": _team(company),
                "contact.html": _contact(company),
            }
        for name, text in pages.items():
            (directory / name).write_text(text, encoding="utf-8")

        meta = {
            "slug": slug,
            "name": company["name"],
            "domain": company["domain"],
            "country": company["country"],
            "language": company["lang"],
            "industry": company["industry"],
            "employees": company["employees"],
            "founded": company["founded"],
            "parent": company.get("parent"),
            "aliases": company.get("aliases", []),
            "description": company["description"],
            "contact_method": company["contact_method"],
            "generic_emails": list(company["emails"]["generic"]),
            "people": [
                {
                    "name": p["name"],
                    "role": p["role"],
                    "decision_maker": p["decision_maker"],
                    "email": source.personal_email(company, p["name"]),
                }
                for p in company.get("team", [])
            ],
            "address": _address(company),
        }
        # Present only where the registry record actually says so; adding these
        # keys to every company would rewrite existing meta.json bytes.
        if "record_type" in company:
            meta["record_type"] = company["record_type"]
        if "domain_status" in company:
            meta["domain_status"] = company["domain_status"]
        (directory / "meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        if not company.get("indexed", True):
            continue

        index[slug] = {
            "slug": slug,
            "name": company["name"],
            "aliases": company.get("aliases", []),
            "domain": company["domain"],
            "country": company["country"],
            "language": company["lang"],
            "industry": company["industry"],
            "description": company["description"],
            "employees": company["employees"],
            "tokens": _tokens(
                " ".join([
                    company["name"], " ".join(company.get("aliases", [])),
                    company["industry"], company["country"], company["description"],
                ])
            ),
        }

    for path in sorted(SITES.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(HERE))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files[rel] = {"sha256": digest, "bytes": path.stat().st_size}

    (HERE / "search_index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    manifest = {
        "corpus_version": source.CORPUS_VERSION,
        "companies": len(source.COMPANIES),
        "files": files,
    }
    (HERE / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    m = build()
    print(f"wrote {len(m['files'])} files for {m['companies']} companies")

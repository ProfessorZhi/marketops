"""The discovery line of the pipeline: while contacts are being found for the organisations
already pooled, keep finding new sources and new organisations.

For every combination of what to look for (brief.focus.find, e.g. "gymnase", "climbing gym",
"badminton club") and where (brief.focus.areas, e.g. the city's districts; else the region):
  maps       places with name, website, address, phone           -> organisations
  lists      directory / association / federation list pages     -> organisations they link to
Each new organisation is merged into orgs.csv (the same one from two sources is one row) and
the pipeline picks it up at once. Jev marks organisations clearly outside the brief
scope=out: they stay in the pool, the pipeline skips them. Every source is logged in
sources.csv. New find/areas words added to the brief while it runs are picked up too.
"""
from __future__ import annotations

import re
import urllib.parse

import mo_config
import mo_grade
import mo_pages
import mo_pool
import mo_providers
import mo_workspace as ws

LIST_WORDS = ("annuaire", "liste", "directory", "list", "clubs", "verzeichnis", "vereine", "elenco")
NAV_WORDS = {"accueil", "home", "contact", "contacts", "mentions legales", "mentions légales", "plan du site",
             "connexion", "login", "shop", "boutique", "calendrier", "actualites", "actualités", "news", "blog",
             "agenda", "faq", "presse", "recrutement", "jobs", "cgv", "cookies", "newsletter", "website", "site web",
             "en savoir plus", "voir", "plus", "read more", "more", "facebook", "instagram"}
MIN_LIST_LINKS = 5
PLATFORMS = ("facebook.", "instagram.", "twitter.", "x.com", "linkedin.", "youtube.", "google.", "wikipedia.",
             "tripadvisor.", "yelp.", "apple.com", "pagesjaunes.", "mappy.", "waze.", "booking.com")


def combos(pdir) -> list[tuple[str, str]]:
    focus = ws.brief(pdir).get("focus") or {}
    find = focus.get("find") or []
    areas = focus.get("areas") or [ws.brief(pdir).get("region", "")]
    return [(f, a) for f in find for a in areas if f]


def _from_maps(pdir, what: str, where: str) -> tuple[list[dict], str]:
    """Places from maps; when no maps provider can answer, the web results themselves (title
    as name, its site as website) so discovery never comes back empty."""
    res = mo_providers.search(f"{what} {where}".strip(), None, n=20, maps=True, project_dir=pdir,
                              weights=ws.weights(pdir, "search"))
    rows = [{"name": h["title"], "website": h.get("url") or "", "address": h.get("address") or "",
             "phone": h.get("phone") or "", "category": h.get("snippet") or "", "city": where}
            for h in res["results"] if h.get("title")]
    if rows:
        return rows, "maps"
    res = mo_providers.search(f"{what} {where}".strip(), None, n=20, project_dir=pdir,
                              weights=ws.weights(pdir, "search"))
    for h in res["results"]:
        url = h.get("url") or ""
        if url.startswith("http") and not any(p in url for p in PLATFORMS) and h.get("title"):
            name = re.split(r"\s[|\-–—]\s", h["title"])[0].strip()
            home = urllib.parse.urlsplit(url)._replace(path="/", query="", fragment="").geturl()
            rows.append({"name": name, "website": home, "category": what, "city": where,
                         "snippet": str(h.get("snippet") or "")[:200]})
    return rows, "web results"


def _from_lists(pdir, what: str, where: str) -> list[tuple[str, list[dict]]]:
    """Directory / association / federation list pages and the organisations they link to."""
    out = []
    res = mo_providers.search(f"{what} {where} {LIST_WORDS[0]} {LIST_WORDS[2]}".strip(), None, n=8,
                              project_dir=pdir, weights=ws.weights(pdir, "search"))
    for h in res["results"][:4]:
        url = h.get("url") or ""
        if not url.startswith("http"):
            continue
        try:
            got = ws.fetch(pdir, url, None, "", "", via="discover")
        except Exception:  # noqa: BLE001
            continue
        page_site, rows, sites = mo_pages.site_of(url), [], set()
        for href, text in got.get("content_links") or []:   # page body only: no header/footer/menus
            site = mo_pages.site_of(href)
            name = " ".join(str(text).split())
            if (site and site != page_site and site not in sites and not any(p in href for p in PLATFORMS)
                    and 3 <= len(name) <= 80 and name.lower() not in NAV_WORDS
                    and not re.search(r"https?://|www\.|@", name)):
                sites.add(site)
                home = urllib.parse.urlsplit(href)._replace(path="/", query="", fragment="").geturl()
                rows.append({"name": name, "website": home, "city": where, "category": what})
        if len(rows) >= MIN_LIST_LINKS:   # a list names many organisations; a few links is not a list
            out.append((url, rows))
    return out


def scope(pdir) -> None:
    """Jev: which pooled organisations are clearly outside the brief (kept, marked scope=out)."""
    if not mo_config.key("jev"):
        return
    b = ws.brief(pdir)
    kinds = ", ".join((b.get("focus") or {}).get("find") or []) or b.get("goal", "")
    mo_grade.judge(pdir, "orgs.csv",
                   f"Is this row itself one of these - {kinds} - or the organisation (club, operator, owner, "
                   f"school, municipality) that runs one? Research goal: {b.get('goal', '')}",
                   {"in": "yes: such a venue, club or the body that runs one",
                    "out": "no: a brand, certification, shop, media, federation page, service or anything else",
                    "unsure": "cannot tell from the row"},
                   "scope", fields=["name", "category", "website", "city", "snippet"])


def discover(pdir, stop) -> dict:
    """Work through every find x area combination once (new ones as the brief grows)."""
    done_file = pdir / "discovered.txt"
    done = set(done_file.read_text(encoding="utf-8").splitlines()) if done_file.exists() else set()
    found = 0
    while not stop():
        todo = [c for c in combos(pdir) if "|".join(c) not in done]
        if not todo:
            break
        for what, where in todo:
            if stop():
                break
            key = f"{what}|{where}"
            try:
                places, kind = _from_maps(pdir, what, where)
                if places:
                    found += mo_pool.add_orgs(pdir, places, f"{kind}: {what} {where}", kind, key)["orgs_added"]
                for url, rows in _from_lists(pdir, what, where):
                    found += mo_pool.add_orgs(pdir, rows, url, "list page", key)["orgs_added"]
            except Exception:  # noqa: BLE001 - one search failing never stops discovery
                pass
            done.add(key)
            done_file.write_text("\n".join(sorted(done)), encoding="utf-8")
            scope(pdir)
    return {"new_orgs": found, "combinations_done": len(done)}

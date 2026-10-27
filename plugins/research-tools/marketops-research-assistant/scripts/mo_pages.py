"""Turn a saved page into readable lines, links and contact candidates.

Nothing here decides who a contact is. It finds every email and phone printed on the
page and keeps the lines around it as the quote; the grade comes later (Jev or the agent).
"""
from __future__ import annotations

import html as htmlmod
import re
import urllib.parse
from html.parser import HTMLParser

BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "section", "article",
         "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "dl", "address",
         "blockquote", "figure", "figcaption", "nav", "aside", "main", "form", "hr", "pre"}
SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head"}

# Words that mark a page worth reading for contacts, in the languages we meet most.
# A hint for picking links, never a filter on what is kept.
CONTACT_HINTS = (
    "contact", "kontakt", "impressum", "annuaire", "services-techniques", "service-des-sports", "imprint", "legal", "mentions", "about", "ueber", "über",
    "uber-uns", "wir", "team", "staff", "people", "personen", "vorstand", "board", "committee",
    "comite", "comité", "officers", "verein", "organisation", "organization", "organigramm",
    "ansprechpartner", "management", "leadership", "governors", "who-we-are", "equipe", "équipe",
    "bureau", "contatti", "chi-siamo", "trainer", "platz", "anlage", "facilit", "venue", "hire",
    "booking", "verwaltung", "gemeinde", "sport", "directory", "funktion", "kader", "club",
    "adress", "kommission", "ressort", "mitarbeit", "personal", "gremi", "leitung", "geschaeftsstelle",
    "geschäftsstelle", "sekretariat", "meet", "our-team", "governance", "grounds", "estates", "site-team",
    "premises", "caretaker", "bursar", "groundsman", "officials", "honorary", "secretary", "membres",
    "responsables", "organigramme", "dirigeants", "conseil", "direction", "persone", "organi", "soci",
)
PEOPLE_HINTS = ("team", "staff", "people", "personen", "vorstand", "board", "committee", "comite", "comité",
                "officers", "ansprechpartner", "leadership", "governors", "our-team", "who-we-are", "bureau",
                "organigramm", "dirigeants", "responsables", "membres", "officials", "honorary", "gremi",
                "mitarbeit", "personal", "direction", "conseil", "persone", "organi")
LEGAL_HINTS = ("contact", "kontakt", "impressum", "imprint", "mentions", "legal", "contatti")
# Club and association sites often live on a hosting platform under another domain; a contact
# or committee link there is followed like one on the club's own site.
HOSTED = ("wildapricot.org", "clubdesk.com", "clubeo.com", "footeo.com", "assoconnect.com", "sportsregions.fr",
          "pitchero.com", "teamsnap.com", "leagueapps.com", "sportngin.com", "sportsengine.com", "stacksports.com",
          "teamapp.com", "clubbuzz.co.uk", "jimdofree.com", "jimdosite.com", "wixsite.com", "webnode.fr",
          "webnode.com", "e-monsite.com", "over-blog.com", "wordpress.com", "blogspot.com", "sites.google.com",
          "vereinsticket.de", "vereinonline.org", "clubee.com")


def hosted(url: str) -> bool:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in HOSTED)


# Pages that rarely carry the people wanted: lower their score so portals do not spend the
# page budget on news. A hint, never a filter.
NOISE_HINTS = ("news", "aktuell", "actualite", "actualité", "blog", "medien", "presse", "press-release",
               "agenda", "event", "veranstaltung", "galerie", "gallery", "archiv", "shop", "cookie",
               "datenschutz", "privacy", "login", "jobs", "stellen", "/tag/", "/category/", "2019", "2020",
               "2021", "2022", "2023")
EMAIL_WORDS = re.compile(r"^\s*(e-?mail|mail|email me|email us|send (an )?e-?mail|nachricht|courriel|kontakt)\b", re.I)

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?![\w-])")
NOT_TLD = {"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "pdf", "ico", "mp4", "woff", "woff2"}
NOT_DOMAIN = ("example.", "sentry.", "wixpress.com", "domain.", "email.com", "yourdomain", "sentry-next")
# Plainly general inboxes: graded D without asking Jev. Role-ish words (secretary, service,
# administration, mairie, team...) are not here - Jev grades them, they can be B or C.
GENERIC = {"info", "office", "kontakt", "contact", "contacto", "contatto", "hello", "hallo", "mail",
           "email", "enquiries", "enquiry", "inquiries", "reception", "post", "general", "welcome",
           "accueil", "noreply", "no-reply", "webmaster", "news", "newsletter"}
PHONE = re.compile(r"(?<![\w])(?:\+|00)?\d[\d\s./()\-]{7,20}\d(?![\w])")
DATEISH = re.compile(r"^\d{1,2}[./-]\d{1,2}([./-]\d{2,4})?(\s|$|-)|^\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{2,4}")


def _cf_decode(hexstr: str) -> str:
    try:
        key = int(hexstr[:2], 16)
        return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))
    except ValueError:
        return ""


class _Text(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.out: list[str] = []
        self.skip = 0
        self.links: list[tuple[str, str]] = []
        self._href = None
        self._anchor: list[str] = []
        self.title = ""
        self._in_title = False
        self.open: list[tuple[str, int]] = []   # open block elements and where their text starts
        self.ranges: list[tuple[int, int]] = []  # closed block elements: (first chunk, end chunk)
        self.hidden_email_buttons = 0
        self.mailtos: set[str] = set()
        self.chrome: list[str] = []          # open header/footer/nav/menu elements
        self.content_links: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        marker = f"{a.get('class') or ''} {a.get('id') or ''} {a.get('role') or ''}".lower()
        if tag in ("nav", "header", "footer") or (tag in ("div", "ul", "section", "aside") and any(
                w in marker for w in ("menu", "navbar", "navigation", "footer", "header", "breadcrumb", "cookie"))):
            self.chrome.append(tag)
        elif self.chrome and tag == self.chrome[-1]:
            self.chrome.append(tag)
        if tag in SKIP:
            self.skip += 1
            return
        if tag in BLOCK:
            self.out.append("\n")
            if tag not in ("br", "hr"):
                self.open.append((tag, len(self.out)))
        if a.get("data-cfemail"):
            self.out.append(" " + _cf_decode(a["data-cfemail"]) + " ")
        if tag == "a":
            self._href = a.get("href") or ""
            self._anchor = []
        if tag == "img" and a.get("alt"):
            self.out.append(" " + a["alt"] + " ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self.chrome and tag == self.chrome[-1]:
            self.chrome.pop()
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "a" and self._href is not None:
            href, text = self._href, " ".join("".join(self._anchor).split())
            if href.lower().startswith("mailto:"):
                addr = urllib.parse.unquote(href[7:].split("?")[0]).strip()
                if addr:
                    self.mailtos.add(addr.lower())
                if addr and addr.lower() not in text.lower():
                    self.out.append(" " + addr + " ")
            elif href.lower().startswith("tel:"):
                num = urllib.parse.unquote(urllib.parse.unquote(href[4:])).strip()
                if num and re.sub(r"\D", "", num) not in re.sub(r"\D", "", text):
                    self.out.append(" " + num + " ")
            elif href.strip() in ("", "#") or href.startswith(("#", "javascript:")):
                if EMAIL_WORDS.match(text):
                    self.hidden_email_buttons += 1
            else:
                link = (urllib.parse.urljoin(self.base, href), text)
                self.links.append(link)
                if not self.chrome:
                    self.content_links.append(link)
            self._href = None
        if tag in BLOCK:
            for k in range(len(self.open) - 1, -1, -1):
                if self.open[k][0] == tag:
                    self.ranges.append((self.open[k][1], len(self.out)))
                    del self.open[k:]
                    break
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.skip:
            return
        self.out.append(data)
        if self._href is not None:
            self._anchor.append(data)


_MD_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)[^)]*\)")


def _flat(chunks: list[str]) -> str:
    return " / ".join(x for x in (" ".join(line.split()) for line in "".join(chunks).split("\n")) if x)


def _blocks(p: "_Text", limit: int = 700) -> dict[str, str]:
    """For each email, the text of the largest page block that holds this email and no other.

    On card and table layouts this is exactly the card or the row, so the name and role next
    to an address are the ones that belong to it, whatever order the page prints them in.
    """
    out: dict[str, str] = {}
    hits = []
    for i, chunk in enumerate(p.out):
        for e in EMAIL.findall(_deobfuscate(chunk)):
            hits.append((i, e.lower()))
    if not hits:
        return out
    ranges = sorted(p.ranges, key=lambda r: r[1] - r[0])
    for i, e in hits:
        if e in out:
            continue
        best = ""
        for start, end in ranges:
            if not start <= i < end:
                continue
            text = _flat(p.out[start:end])
            found = {x.lower() for x in EMAIL.findall(_deobfuscate(text))}
            if found != {e} or len(text) > limit:
                break
            best = text
        if best and len(best) > len(e) + 3:
            out[e] = best
    return out


def from_html(raw: str, base: str) -> dict:
    p = _Text(base)
    try:
        p.feed(raw)
        p.close()
    except Exception:  # noqa: BLE001 - broken HTML still gives what was parsed
        pass
    for _tag, start in p.open:
        p.ranges.append((start, len(p.out)))
    lines = [" ".join(line.split()) for line in "".join(p.out).split("\n")]
    return {"title": " ".join(p.title.split()), "lines": [x for x in lines if x], "links": p.links,
            "blocks": _blocks(p), "hidden_email_buttons": p.hidden_email_buttons, "mailtos": p.mailtos,
            "content_links": p.content_links}


def from_markdown(text: str, base: str) -> dict:
    links = []

    def repl(m):
        label, href = m.group(1), m.group(2)
        if href.lower().startswith("mailto:"):
            addr = urllib.parse.unquote(href[7:].split("?")[0])
            return label if addr.lower() in label.lower() else f"{label} {addr}"
        if href.lower().startswith("tel:"):
            num = urllib.parse.unquote(urllib.parse.unquote(href[4:]))
            return label if re.sub(r"\D", "", num) in re.sub(r"\D", "", label) else f"{label} {num}"
        links.append((urllib.parse.urljoin(base, href), label))
        return label
    body = _MD_LINK.sub(repl, text)
    body = re.sub(r"[*_`#>|]+", " ", body)
    lines = [" ".join(line.split()) for line in body.split("\n")]
    return {"title": "", "lines": [x for x in lines if x], "links": links}


def _deobfuscate(line: str) -> str:
    line = re.sub(r"\s*[\[\(\{<]\s*(?:at|ät|arobase|@)\s*[\]\)\}>]\s*", "@", line, flags=re.I)
    line = re.sub(r"\s*[\[\(\{<]\s*(?:dot|punkt|point|\.)\s*[\]\)\}>]\s*", ".", line, flags=re.I)
    return line


def site_of(url: str) -> str:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    # keep the registrable part: example.co.uk -> example.co.uk, a.b.example.ch -> example.ch
    if len(parts) >= 3 and parts[-2] in {"co", "org", "ac", "gov", "sch", "net", "com", "ltd", "plc", "nhs", "police"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _phones(line: str) -> list[str]:
    found = []
    for m in PHONE.finditer(line):
        s = m.group(0).strip()
        digits = re.sub(r"\D", "", s)
        if not 9 <= len(digits) <= 15 or DATEISH.search(s) or s.count(".") > 3:
            continue
        found.append(s)
    return found


def _unglue(e: str, before: str, known: set[str]) -> str:
    """A phone number printed right before an address can glue its last digits onto it
    ('83 10praesident@x.ch'). The page's own mailto value wins; otherwise digits are cut
    when the text before them ends in a digit group."""
    m = re.match(r"^(\d{1,6})([A-Za-z].*@.+)$", e)
    if not m:
        return e
    if m.group(2).lower() in known or (e.lower() not in known and re.search(r"\d[\d\s./-]*$", before)):
        return m.group(2)
    return e


def contacts(lines: list[str], page_url: str, org_site: str = "", blocks: dict | None = None,
             mailtos: set | None = None) -> list[dict]:
    """Every email (and phone) on the page with the lines around it as the quote.

    With `blocks` (from HTML) the quote is the page block that holds only this address.
    """
    page_site = site_of(page_url)
    out, last = [], -1
    clean = [_deobfuscate(x) for x in lines]
    for i, line in enumerate(clean):
        emails = [e for e in EMAIL.findall(line)
                  if e.rsplit(".", 1)[-1].lower() not in NOT_TLD
                  and not any(d in e.lower().split("@")[1] for d in NOT_DOMAIN)]
        pos = 0
        fixed = []
        for e in emails:
            at = line.find(e)
            fixed.append(_unglue(e, line[:at] if at >= 0 else "", mailtos or set()))
        emails = fixed
        for n, e in enumerate(dict.fromkeys(emails)):
            at = line.find(e, pos)
            end = at + len(e) if at >= 0 else len(line)
            # Several entries can share one line: this address owns the text since the previous one.
            seg = line[pos:end].strip(" -/|,;")
            before = clean[max(last + 1, i - 4):i] if n == 0 else []
            quote = " / ".join(before + [seg])[-500:]
            after = line[end:].strip()[:100] or (clean[i + 1][:100] if i + 1 < len(clean) else "")
            if after:
                quote += " [next: " + after + "]"
            pos = end
            block = (blocks or {}).get(e.lower())
            if block:
                quote = block[-600:]
            dom = site_of("http://" + e.split("@")[1])
            local = e.split("@")[0].lower()
            out.append({
                "kind": "email", "value": e, "quote": quote, "page_url": page_url,
                "quote_from": "block" if block else "lines",
                "email_site": dom,
                "same_site": "yes" if dom in {page_site, org_site} - {""} else "no",
                "mailbox": ("generic" if re.split(r"[._-]", local)[0] in GENERIC else "named_or_role"),
            })
        if emails:
            last = i
        for ph in _phones(line):
            out.append({"kind": "phone", "value": ph, "quote": " / ".join(clean[max(0, i - 2):i + 1])[:400],
                        "page_url": page_url, "email_site": "", "same_site": "", "mailbox": "",
                        "quote_from": "lines"})
    return out


def contact_links(links: list[tuple[str, str]], base: str, limit: int = 12, scores: bool = False) -> list:
    """Same-site links whose address or text looks like a contact/people page, best first.

    PDFs count too (club address lists, staff lists). scores=True returns (url, score).
    """
    site = site_of(base)
    scored = {}
    for href, text in links:
        parts = urllib.parse.urlsplit(href)
        if parts.scheme not in ("http", "https") or (site_of(href) != site and not hosted(href)):
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|zip|docx?|xlsx?|mp4|ics|mp3|webp|svg)$", parts.path, re.I):
            continue
        hay = (urllib.parse.unquote(parts.path) + " " + parts.query + " " + text).lower()
        score = sum(h in hay for h in CONTACT_HINTS) - 2 * sum(h in hay for h in NOISE_HINTS)
        if score > 0:
            url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
            # Bruce's runs: people pages (staff, team, board) carried ~10x the A/B of contact pages
            # per page read, home pages almost none; the legal page names the operator.
            score += 3 * any(h in hay for h in PEOPLE_HINTS) + 2 * any(h in hay for h in LEGAL_HINTS)
            scored[url] = max(scored.get(url, 0), score)
    ranked = sorted(scored.items(), key=lambda kv: -kv[1])[:limit]
    return ranked if scores else [u for u, _ in ranked]


def unescape(text: str) -> str:
    return htmlmod.unescape(text)

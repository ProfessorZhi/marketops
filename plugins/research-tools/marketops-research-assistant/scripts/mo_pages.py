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
    "contact", "kontakt", "impressum", "imprint", "legal", "mentions", "about", "ueber", "über",
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
EMAIL_WORDS = re.compile(r"^\s*(e-?mail|mail|email me|email us|send (an )?e-?mail|nachricht|courriel|kontakt)\b", re.I)

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?![\w-])")
NOT_TLD = {"png", "jpg", "jpeg", "gif", "webp", "svg", "css", "js", "pdf", "ico", "mp4", "woff", "woff2"}
NOT_DOMAIN = ("example.", "sentry.", "wixpress.com", "domain.", "email.com", "yourdomain", "sentry-next")
GENERIC = {"info", "office", "kontakt", "contact", "contacto", "contatto", "admin", "hello", "hallo",
           "mail", "email", "enquiries", "enquiry", "inquiries", "reception", "sekretariat",
           "secretariat", "secretary", "post", "general", "welcome", "service", "support", "team",
           "verwaltung", "administration", "bureau", "accueil", "mairie", "gemeinde", "school",
           "noreply", "no-reply", "webmaster", "news", "newsletter", "media", "presse", "press"}
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

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
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
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if tag == "a" and self._href is not None:
            href, text = self._href, " ".join("".join(self._anchor).split())
            if href.lower().startswith("mailto:"):
                addr = urllib.parse.unquote(href[7:].split("?")[0]).strip()
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
                self.links.append((urllib.parse.urljoin(self.base, href), text))
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
            "blocks": _blocks(p), "hidden_email_buttons": p.hidden_email_buttons}


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


def contacts(lines: list[str], page_url: str, org_site: str = "", blocks: dict | None = None) -> list[dict]:
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
        if parts.scheme not in ("http", "https") or site_of(href) != site:
            continue
        if re.search(r"\.(jpg|jpeg|png|gif|zip|docx?|xlsx?|mp4|ics|mp3|webp|svg)$", parts.path, re.I):
            continue
        hay = (urllib.parse.unquote(parts.path) + " " + parts.query + " " + text).lower()
        score = sum(h in hay for h in CONTACT_HINTS)
        if score:
            url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
            scored[url] = max(scored.get(url, 0), score + (2 if "kontakt" in hay or "contact" in hay or "impressum" in hay else 0))
    ranked = sorted(scored.items(), key=lambda kv: -kv[1])[:limit]
    return ranked if scores else [u for u, _ in ranked]


def unescape(text: str) -> str:
    return htmlmod.unescape(text)

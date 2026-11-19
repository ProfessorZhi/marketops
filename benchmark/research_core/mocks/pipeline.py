"""Deterministic stage logic shared by the mock adapters and the E2E pipeline.

This is a *reference implementation*, not a model. It exists so the harness can
be exercised end to end with no keys, no network and no LLM, and so every
comparison has a fixed baseline. Its scores say how well this baseline does, not
how good MarketOps is.

Rules that matter:

  * nothing here reads a case's ``gold``;
  * extraction only reports what is visible in the page bytes;
  * a missing snapshot never becomes "verified";
  * a provider with no credential is handled by the runner, not here.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html as _html
import json
import pathlib
import re
import unicodedata

REPO = pathlib.Path(__file__).resolve().parents[3]
DATASET_ROOT = REPO / "benchmark" / "datasets" / "research-core"
CORPUS = DATASET_ROOT / "corpus"
SITES = CORPUS / "sites"

#: Fixed clock for freshness checks: the dataset's capture date, not "today".
NOW = _dt.datetime(2026, 11, 19, tzinfo=_dt.timezone.utc)

DECISION_ROLES = (
    "director", "geschäftsführer", "geschaeftsfuehrer", "founder", "ceo",
    "vorstand", "directeur", "head", "leiter", "managing", "partner",
    "vorsitzender", "bestuurder", "eigenaar", "chief", "vp ", "president",
    "owner", "inhaber", "producer", "publisher",
)

EXCLUDED_INDUSTRY_WORDS = ("furniture", "fashion", "retail", "meubel", "mode")

MARINE_INDUSTRY_WORDS = ("marine", "shipyard", "scheeps", "werft", "yacht")

ADJACENT_INDUSTRY_WORDS = (
    "industrial maintenance", "solar", "renewable", "logistics", "transport",
    "food", "horticulture", "publishing",
)

#: What "adjacent" and "excluded" mean for each brief family. The ICP travels
#: with the brief; the words that make an industry adjacent do not fit in the
#: five ICP fields, so they live here and are named in the report.
FAMILY_RULES = {
    "marine": {
        "adjacent": ("industrial maintenance", "solar", "renewable", "logistics",
                     "transport"),
        "excluded": ("furniture", "fashion", "retail", "meubel", "mode"),
    },
    "machine": {
        "adjacent": ("hand tools", "precision", "tooling", "steel"),
        "excluded": ("hotel", "consulting", "kitchen"),
    },
    "creative": {
        "adjacent": ("publishing", "music", "media"),
        "excluded": ("software", "analytics", "data"),
    },
}


def norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", stripped).strip()


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_JS_ASSIGN = re.compile(
    r"document\.getElementById\('([^']+)'\)\.innerHTML\s*=\s*'((?:\\.|[^'\\])*)';"
)


def render_js(markup: str) -> str:
    """Apply the fixture's one DOM pattern, so a JS team list is readable.

    This is not a browser; it is the smallest emulation that lets a rendered
    page be compared with a static one.
    """
    for target, payload in _JS_ASSIGN.findall(markup):
        escaped = payload.replace("\\'", "'")
        markup = re.sub(
            rf'(<div id="{re.escape(target)}">).*?(</div>)',
            lambda m: m.group(1) + escaped + m.group(2),
            markup, count=1, flags=re.S,
        )
    return markup


def visible_text(markup: str) -> str:
    without_scripts = re.sub(r"<(script|style)\b.*?</\1>", " ", markup, flags=re.S | re.I)
    text = _html.unescape(re.sub(r"<[^>]+>", " ", without_scripts))
    text = "".join(ch for ch in text if ch.isprintable() or ch == " ")
    return re.sub(r"\s+", " ", text).strip()


def page_facts(markup: str) -> tuple[list[str], list[dict], list[str]]:
    """Addresses, people and links a reader can see in the markup."""
    emails = sorted({m.group(1) for m in re.finditer(r'mailto:([^"\'<>\s?]+)', markup)})
    links = sorted({m.group(1) for m in re.finditer(r'href="([^"#]+)"', markup)
                    if not m.group(1).lower().startswith("javascript:")})

    people: dict[str, dict] = {}
    # Team list: <li><strong>Name</strong> — Role, <a href="mailto:...">
    for match in re.finditer(r"<li>\s*<strong>(.+?)</strong>\s*[—-]\s*(.*?)(?:,|<|$)",
                             markup, flags=re.S):
        name = _html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()
        role = _html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip(" ,")
        if name:
            people.setdefault(name, {"name": name, "role": role})
    # Contact block: <p>Name: <a href="mailto:...">
    for match in re.finditer(r"<p>\s*([^<>:]{2,80}?):\s*<a href=\"mailto:", markup):
        name = _html.unescape(match.group(1)).strip()
        if name and name not in people:
            people[name] = {"name": name, "role": "contact"}

    return emails, list(people.values()), links


def email_owner(markup: str) -> dict[str, str]:
    """Map an address to the person named next to it, when the page says so."""
    owners: dict[str, str] = {}
    for item in re.finditer(r"<li>.*?</li>", markup, flags=re.S):
        block = item.group(0)
        name = re.search(r"<strong>(.+?)</strong>", block)
        address = re.search(r"mailto:([^\"'<>\s?]+)", block)
        if name and address:
            owners[address.group(1)] = _html.unescape(name.group(1)).strip()
    for match in re.finditer(r"<p>\s*([^<>:]{2,80}?):\s*<a href=\"mailto:([^\"'<>\s?]+)\"",
                             markup):
        owners[match.group(2)] = _html.unescape(match.group(1)).strip()
    return owners


def is_decision_maker(role: str) -> bool:
    lowered = role.lower()
    if "marketing" in lowered:  # a marketing title is not a buying role
        return False
    return any(word in lowered for word in DECISION_ROLES)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract(markup: str, company: dict) -> dict:
    emails, people, _links = page_facts(markup)
    owners = email_owner(markup)

    # The page is presented with its company already known (that is the input),
    # so the heading may not repeat the legal name. Prefer the given company.
    name = company["name"]

    people_out = [{
        "name": person["name"],
        "role": person["role"],
        "company": name,
        "decision_maker": is_decision_maker(person["role"]),
        "evidence_text": f"{person['name']} — {person['role']}",
    } for person in people if person["role"] != "contact"]
    emails_out = [{
        "value": address,
        "kind": "personal" if address in owners else "generic",
        "person": owners.get(address),
        "evidence_text": address,
    } for address in emails]

    gaps = []
    if not any(e["kind"] == "personal" for e in emails_out):
        gaps.append("personal email not published")
    if not emails_out:
        gaps.append("no address at all; the page offers a contact form")
    return {
        "companies": [{"name": name, "domain": company["domain"]}],
        "people": people_out,
        "emails": emails_out,
        "gaps": gaps,
    }


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------

def qualify(prospect: dict, icp: dict, family: str = "marine") -> dict:
    rules = FAMILY_RULES.get(family, FAMILY_RULES["marine"])
    industry = prospect["industry"].lower()
    employees = prospect.get("employees")
    country_ok = prospect["country"] in icp["countries"]
    industry_ok = any(
        norm(word) in norm(industry) or norm(industry) in norm(word)
        for word in icp["industries"]
    )
    adjacent = any(word in industry for word in rules["adjacent"])
    size_ok = employees is not None and icp["employees_min"] <= employees <= icp["employees_max"]
    has_evidence = bool(prospect.get("evidence"))
    record_type = (prospect.get("record_type") or "company").lower()
    domain_status = (prospect.get("domain_status") or "live").lower()
    conflicts = prospect.get("conflicts") or []

    if not has_evidence:
        status, reason = "insufficient_evidence", "No cited page states the industry or size."
        missing = ["industry unverified", "headcount unverified"]
    elif record_type != "company":
        # A directory, marketplace or aggregator lists companies; it is not one
        # that could buy anything, whatever its country and industry say.
        status, reason = "out_of_scope", f"The record is a {record_type}, not a company."
        missing = ["not a company record"]
    elif domain_status != "live":
        # A parked or placeholder domain is an identity question, not a poor fit:
        # nothing on it can confirm that this is the company being judged.
        status, reason = ("unverified_identity",
                          f"The domain is {domain_status}; no published page confirms the company.")
        missing = ["identity unverified", "site not live"]
    elif conflicts:
        # Facts that contradict each other are not a fit decision a rule may make.
        status, reason = ("needs_review",
                          "Cited pages disagree; a human has to resolve the conflict first.")
        missing = ["conflicting facts"]
    elif any(word in industry for word in rules["excluded"]):
        status, reason = "excluded", "Industry is an explicit exclusion in the ICP."
        missing = ["excluded industry"]
    elif industry_ok and country_ok:
        status = "strong" if size_ok else "partial"
        reason = ("ICP industry in an ICP country" if size_ok
                  else "ICP industry in an ICP country, but headcount is outside the range.")
        missing = [] if size_ok else ["headcount outside range"]
    elif industry_ok:
        status, reason = "partial", "ICP industry, but outside the ICP countries."
        missing = ["wrong country"]
    elif country_ok and adjacent:
        status, reason = "partial", "In an ICP country; industry is adjacent, not a target."
        missing = ["industry mismatch"]
    elif country_ok:
        status, reason = "weak", "In an ICP country, but the industry is only weakly related."
        missing = ["industry mismatch"]
    elif adjacent:
        status, reason = "weak", "Adjacent industry, but outside the ICP countries."
        missing = ["wrong country"]
    else:
        status, reason = "not_qualified", "Neither the industry nor the country matches the ICP."
        missing = ["wrong industry", "wrong country"]

    relevance = {"strong": 3, "partial": 2, "weak": 1}.get(status, 0)
    # A match needs the ICP industry *in* an ICP country; an adjacent industry in
    # the right country is a partial fit, not a match. When the record itself is
    # the problem -- not a company, an unverifiable domain, contradictory pages --
    # the stated attributes do not add up to a match: unknown is not a yes.
    judged = status not in ("out_of_scope", "unverified_identity", "needs_review")
    return {
        "qualification_status": status,
        "icp_match": judged and industry_ok and country_ok,
        "relevance": relevance,
        "reason": reason,
        "supporting_evidence": list(prospect.get("evidence") or []),
        "missing_evidence": missing,
    }


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

LEGAL_TOKENS = {"ltd", "limited", "gmbh", "ag", "bv", "nv", "inc", "llc", "plc",
                "co", "sarl", "sa", "oy", "ab"}


def name_key(name: str) -> str:
    tokens = [t for t in norm(name).split() if t not in LEGAL_TOKENS]
    return " ".join(tokens)


def domain_key(domain: str | None) -> str | None:
    if not domain:
        return None
    return re.sub(r"^www\.", "", domain.lower())


def cluster(records: list[dict]) -> dict:
    """Union-find over two identity signals: shared domain, or same name+country.

    A shared domain merges spelling variants; the same normalised name in the
    same country merges records whose domains differ (a company with two
    domains). Different countries stay apart: a branch is not a duplicate.
    """
    parent = {record["record_id"]: record["record_id"] for record in records}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    buckets: dict[tuple, list[str]] = {}
    for record in records:
        domain = domain_key(record["domain"])
        if domain:
            buckets.setdefault(("domain", domain), []).append(record["record_id"])
        buckets.setdefault(("name", name_key(record["name"]), record["country"]),
                           []).append(record["record_id"])
    for ids in buckets.values():
        for other in ids[1:]:
            union(ids[0], other)

    groups: dict[str, list[str]] = {}
    for record in records:
        groups.setdefault(find(record["record_id"]), []).append(record["record_id"])

    relations = []
    holdings = [r for r in records if re.search(r"\bholdings?\b", r["name"], re.I)]
    for holding in holdings:
        stem = name_key(holding["name"]).split()[:2]
        for record in records:
            if record is holding:
                continue
            if name_key(record["name"]).split()[:2] == stem:
                relations.append({"parent": holding["record_id"],
                                  "child": record["record_id"], "kind": "parent_of"})
    return {"clusters": list(groups.values()), "relations": relations}


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def check_evidence(evidence: dict, *, root: pathlib.Path = DATASET_ROOT) -> dict:
    snapshot = (root / evidence["snapshot_path"]).resolve()
    exists = snapshot.is_file()
    actual = hashlib.sha256(snapshot.read_bytes()).hexdigest() if exists else None
    rendered = render_js(snapshot.read_text(encoding="utf-8", errors="replace")) if exists else ""
    text = norm(visible_text(rendered))

    traceable = [value for value in evidence["extracted_fields"].values()
                 if norm(str(value)) and norm(str(value)) in text]

    stale = False
    if evidence.get("retrieved_at"):
        try:
            retrieved = _dt.datetime.fromisoformat(evidence["retrieved_at"])
        except ValueError:
            retrieved = None
        if retrieved is not None:
            if retrieved.tzinfo is None:
                retrieved = retrieved.replace(tzinfo=_dt.timezone.utc)
            days = (NOW - retrieved).days
            stale = days > 365  # older than a year by default; cases may override
    return {
        "snapshot_exists": exists,
        "sha256_matches": bool(actual) and actual == evidence["sha256"],
        "fields_traceable": traceable,
        "entity_ref": (evidence.get("entity_refs") or [None])[0],
        "stale": stale,
        "contradicts": [],
    }


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------

CHANNEL_RED_FLAGS = ("linkedin", "purchased", "mass mail", "cold call", "switchboard")


def rank_channels(channels: list[dict]) -> dict:
    rejected = [c["channel_id"] for c in channels
                if any(flag in (c["name"] + " " + c["hypothesis"]).lower()
                       for flag in CHANNEL_RED_FLAGS)]
    usable = [c for c in channels if c["channel_id"] not in rejected and c["prior"] >= 0.25]
    usable.sort(key=lambda c: (-c["prior"], c["channel_id"]))
    ranked = [c["channel_id"] for c in usable]
    return {"ranked_channels": ranked, "acceptable_set": ranked, "rejected": rejected}


# ---------------------------------------------------------------------------
# Brief
# ---------------------------------------------------------------------------

BRIEF_KNOWLEDGE = {
    "marine": {
        "icp_attributes": ["marine equipment", "shipyard", "deck hardware", "20-500 employees"],
        "personas": ["Managing Director", "Head of Procurement", "Head of Partnerships"],
        "pain_points": ["long lead times on deck fittings", "single-supplier dependency",
                        "spare-parts availability"],
        "channels": ["direct email to a named director", "trade association member directory",
                     "industry trade fair"],
        "queries": ["marine deck fittings supplier Netherlands",
                    "shipyard equipment distributor UK",
                    "scheepsbeslag leverancier Zeeland"],
        "exclusions": ["consumer boat shops", "furniture retailers",
                       "companies under 20 employees"],
    },
    "machine": {
        "icp_attributes": ["machine tools", "factory automation", "100-1000 employees", "DE", "AT"],
        "personas": ["Geschäftsführer", "Leiter Vertrieb", "Leiter Einkauf"],
        "pain_points": ["obsolete control systems", "skilled-labour shortage",
                        "integration cost"],
        "channels": ["direct email in German to the Geschäftsführung",
                     "industry association event", "specialist trade press"],
        "queries": ["CNC Fräsmaschinen Hersteller Mittelstand",
                    "Automatisierung Retrofit Anbieter Deutschland",
                    "Werkzeugmaschinenbauer Österreich"],
        "exclusions": ["hotel groups", "management consultancies",
                       "commercial kitchen suppliers", "companies under 100 employees"],
    },
    "creative": {
        "icp_attributes": ["video production", "podcast production", "10-200 employees", "GB", "IE"],
        "personas": ["Founder", "Executive Producer", "Head of Partnerships"],
        "pain_points": ["uneven project pipeline", "in-house capacity limits",
                        "client demand for audio"],
        "channels": ["direct email to the founder", "production industry directory",
                     "film festival network"],
        "queries": ["independent video production studio London",
                    "podcast production company Ireland",
                    "documentary production studio Bristol"],
        "exclusions": ["software vendors", "data analytics firms",
                       "companies over 200 employees", "in-house corporate teams"],
    },
}


def _brief_family(raw: dict) -> str:
    blob = " ".join([raw.get("product", ""), raw.get("description", ""),
                     raw.get("goal", "")]).lower()
    if any(word in blob for word in ("cnc", "machine tool", "automation", "werkzeug")):
        return "machine"
    if any(word in blob for word in ("video", "podcast", "documentary", "studio")):
        return "creative"
    return "marine"


#: Segment descriptors a brief states in prose rather than in a structured
#: field. A keyword baseline may only report what the brief actually says, so
#: each canonical gold phrase lists the surface forms that evidence it. Absent
#: from the text, absent from the output -- nothing is inferred.
SEGMENT_CUES = {
    "Mittelstand": ("mittelstand",),
    "independent studio": ("independent studio", "independent production studio"),
}


def parse_brief(raw: dict) -> dict:
    family = BRIEF_KNOWLEDGE[_brief_family(raw)]
    out = {field: list(values) for field, values in family.items()}
    blob = " ".join(str(raw.get(field, "")) for field in
                    ("product", "description", "offer", "goal")).lower()
    segments = [phrase for phrase, cues in SEGMENT_CUES.items()
                if any(cue in blob for cue in cues)]
    # Languages are not ICP attributes; only markets and stated segments join
    # the family's industry/size profile.
    out["icp_attributes"] = list(dict.fromkeys(
        out["icp_attributes"] + list(raw.get("markets", [])) + segments
    ))
    out["forbidden_claims"] = []
    return out


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def _meta(slug: str) -> dict:
    return json.loads((SITES / slug / "meta.json").read_text(encoding="utf-8"))


def _pages(slug: str) -> list[pathlib.Path]:
    return [SITES / slug / name for name in ("index.html", "about.html", "team.html", "contact.html")]


def load_brief(brief_ref: str) -> dict:
    path = DATASET_ROOT / "synthetic" / "brief" / "cases.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and json.loads(line)["case_id"] == brief_ref:
            return json.loads(line)
    raise KeyError(f"no brief {brief_ref!r}")


ICP_INDUSTRIES = {
    "marine": ["marine equipment", "marine engineering", "shipyard"],
    "machine": ["machine tools", "factory automation"],
    "creative": ["video production", "podcast production", "documentary production"],
}
ICP_HEADCOUNT = {"marine": (20, 500), "machine": (100, 1000), "creative": (10, 200)}


def icp_for(raw: dict, family: str) -> dict:
    low, high = ICP_HEADCOUNT[family]
    return {
        "countries": list(raw["markets"]),
        "industries": list(ICP_INDUSTRIES[family]),
        "employees_min": low,
        "employees_max": high,
        "target_roles": ["Managing Director", "Head of Procurement", "Head of Partnerships"],
        "exclusions": list(BRIEF_KNOWLEDGE[family]["exclusions"]),
    }


def run_e2e(case: dict, slices: dict[str, list[str]]) -> dict:
    """Search -> fetch -> extract -> qualify -> dedupe -> dossier.

    The discovery result set is ``input.corpus``; everything after it is earned
    from the page bytes.
    """
    slugs = slices[case["input"]["corpus"]]
    brief = load_brief(case["input"]["brief_ref"])
    family = _brief_family(brief["input"]["raw_brief"])
    icp = icp_for(brief["input"]["raw_brief"], family)

    prospects = []
    gaps: list[str] = []
    seen_domains: set[str] = set()
    missing_personal = False
    form_only = False

    for slug in slugs:
        meta = _meta(slug)
        evidence_ids = []
        people: list[dict] = []
        emails: list[dict] = []
        for page in _pages(slug):
            markup = page.read_text(encoding="utf-8")
            result = extract(markup, {"name": meta["name"], "domain": meta["domain"]})
            evidence_ids.append(f"snapshot:{page.relative_to(DATASET_ROOT).as_posix()}")
            for person in result["people"]:
                if person["name"] not in {p["name"] for p in people}:
                    people.append(person)
            for email in result["emails"]:
                if email["value"] not in {e["value"] for e in emails}:
                    emails.append(email)
        if meta["contact_method"] == "form":
            form_only = True

        personal = [e for e in emails if e["kind"] == "personal"]
        generic = [e for e in emails if e["kind"] != "personal"]
        # "email" means a personal address published on the site. A generic
        # inbox is recorded separately: it is a way to reach the company, not
        # evidence that a named person was found.
        if personal:
            email_value = personal[0]["value"]
            email_owner_name = personal[0]["person"]
        else:
            email_value = None
            email_owner_name = None
            missing_personal = True
        contact_email = generic[0]["value"] if generic else None

        decision_people = [p for p in people if p["decision_maker"]]
        person = (decision_people or people or [None])[0]

        prospect = {
            "name": meta["name"], "domain": meta["domain"],
            "country": meta["country"], "industry": meta["industry"],
            "employees": meta["employees"], "evidence": evidence_ids,
        }
        verdict = qualify(prospect, icp, family)

        status = verdict["qualification_status"]
        if meta["domain"] in seen_domains:
            status = "duplicate"
        seen_domains.add(meta["domain"])

        prospects.append({
            "company": meta["name"],
            "domain": meta["domain"],
            "status": status,
            "person": person["name"] if person else None,
            "person_role": person["role"] if person else None,
            "email": email_value,
            "email_owner": email_owner_name,
            "contact_email": contact_email,
            "evidence": evidence_ids,
            "relevance": verdict["relevance"],
        })

    if missing_personal:
        gaps.append("personal email not published for some prospects")
    if form_only:
        gaps.append("contact form only, no address, for some companies")

    return {
        "prospects": prospects,
        "gaps": gaps,
        "dossier": {
            "brief_ref": case["input"]["brief_ref"],
            "sections": ["brief", "method", "prospects", "evidence", "gaps"],
            "prospect_count": len(prospects),
            "qualified_count": sum(1 for p in prospects if p["status"] in ("strong", "partial")),
        },
    }

"""Grade contact candidates A/B/C/D/X against the brief, in any language.

Jev reads each candidate's quote and picks the grade the brief describes. The agent can
also set grades, people and roles itself (it reads quotes as well as Jev does); a row the
agent set is never overwritten by Jev.
"""
from __future__ import annotations

import json
import os
import re

import mo_budget
import mo_config
import mo_workspace as ws
from mo_pool import common_words, mentions

# Bruce's grading table (written by him on 2026-09-05; A_key_role ... D_official_general_research_entry),
# used when the brief has none. Worded for the grounds / facilities lines it came from; a brief
# for another product puts that product's function in its place.
DEFAULT_GRADES = {
    "A": "A named key person who decides, evaluates technically, buys, or runs the operation the "
         "product serves (head of grounds / facilities / estates, sports department head, club "
         "president, the board member for the ground). Name, role, organisation and this address "
         "are tied together on an official page.",
    "B": "An operator role for that function - grounds, facilities, estates, maintenance, sport "
         "facilities, site, operations, projects (groundsman, site / venue manager, caretaker, "
         "Platzwart, club secretary) - whose real work address stands in the same official record "
         "as the role. A name is not required.",
    "C": "That function's department inbox, official for this organisation: grounds@, facilities@, "
         "estates@, sport@, booking@, maintenance@ - a usable entry, not a named contact.",
    "D": "A general official inbox: office@, info@, enquiries@, reception@, admin@, contact@. The "
         "page need not say it handles the venue; it is labelled D, never presented as a person.",
    "X": "Excluded: functions unrelated to the product's use or purchase - admissions, attendance, "
         "courses, safeguarding, student or parent services, HR, finance, marketing, PR, press, "
         "no-reply; another company's address (sponsor, web designer, partner); a private person "
         "with no role here; anything inferred, guessed or malformed.",
}


def mark_missing_role(rows: list[dict], just: set) -> list[dict]:
    """A/B rows with no role after the name look-up go to review.

    The brief's A requires a name, a role and the address in one official record; B requires
    a role address or a transferable operations person. When no role could be read, the row
    may still be right, but it must not pass as confirmed: flag it instead of trusting it.
    """
    out = []
    for r in rows:
        key = (r.get("org_id"), (r.get("value") or "").lower())
        if r.get("grade") not in ("A", "B") or key not in just:
            continue
        if str(r.get("role") or "").strip() or r.get("review") == "yes":
            continue
        why = (r.get("review_reason") or "").strip()
        out.append({**r, "review": "yes",
                    "review_reason": (why + "; " if why else "") + "no role recorded"})
    return out


def rubric(pdir) -> dict:
    grades = ws.brief(pdir).get("grades") or {}
    return {g: grades.get(g) or DEFAULT_GRADES[g] for g in "ABCDX"}


def _jev(state: dict, questions: dict):
    """One Jev request; a key refused for money or as invalid is set aside and the same request
    goes out again at once with the next key slot."""
    tried = set()
    while True:
        key = mo_config.key("jev")
        tried.add(key)
        try:
            return _jev_once(state, questions, key)
        except Exception as exc:  # noqa: BLE001
            text = f"{type(exc).__name__} {exc}".lower()
            if "402" in text or "quota" in text or "credit" in text or "payment" in text:
                mo_budget.mark_exhausted("jev", "Jev refused for money")
            elif "401" in text or "unauthorized" in text or "invalid api key" in text:
                mo_budget.mark_exhausted("jev", "Jev refused the key")
            else:
                raise
            if not mo_config.key("jev") or mo_config.key("jev") in tried:
                raise


def _jev_once(state: dict, questions: dict, key: str):
    import typesafe_sdk as sdk
    with sdk.TypeSafeClient(
            api_key=key, timeout=120,
            model=os.environ.get("MARKETOPS_JEV_MODEL") or None,
            ) as client:   # the SDK's default retry backs off on 429 / 529 (docs.typesafe.ai)
        return client.system_one(state, questions)


GUIDE = ("Read any language. Judge job titles and abbreviations by this kind of organisation (in a "
         "school 'FS' is Foundation Stage, not facilities). An address whose own words name a role "
         "(facilities@, grounds@, groundsman@, caretaker@, bursar@, sbm@ = school business manager, "
         "platzwart@, estates@) counts for that role even when no person is named. If the address "
         "is also listed for other organisations, or the page belongs to another organisation (a "
         "directory, a council or canton staff list), give A or B only when the text ties the "
         "person to THIS organisation or its venue; otherwise C or X. Grade only this address, not "
         "the neighbouring entries. The person who actually runs the grounds, the equipment or the "
         "maintenance contract counts for more than the highest title. A supporters' or sponsors' "
         "club, a festival or bar team, teachers, admissions, student services, alumni or newsletter "
         "addresses are not the venue's decision or operations side; a coach only when the research "
         "brief names coaches.")
REVIEW_BELOW = 0.6
SHARED_REVIEW = 5   # named next to each venue, one district office running up to five is normal; more is a federation
REVIEW_BELOW_OWN_SITE = 0.35  # on its own site, bound in its card: only very unsure grades wait


def _address_words(email: str) -> str:
    local = email.split("@")[0]
    return " ".join(w for w in re.split(r"[._\-+0-9]+", local) if len(w) > 1)


def auto(pdir, limit: int = 0, regrade: bool = False, batch: int = 16, workers: int = 8,
         orgs: set | list | None = None) -> dict:
    """Grade the email candidates that have no grade yet (or all, with regrade). limit 0 = all.
    orgs: only these organisations' rows - an agent grades its own package and never waits on, or
    pays twice for, rows another agent's package is grading.

    Rules first, at no cost: the brief's address rules, an organisation's own plain inbox (D) and
    an address that has nothing to do with the organisation (X: neither its page, its domain nor
    its name). Jev grades the rest - only the rows that need judgement. Without Jev the rules still
    run and the rest waits for agents (grade(set=[...]))."""
    rows = ws.read_contacts(pdir)
    jev = mo_budget.jev_ready()
    import mo_pages
    org_site = {str(o.get("id") or o.get("org_id") or ""): mo_pages.site_of(str(o.get("website") or ""))
                for o in ws.read_csv(pdir / "orgs.csv")}
    portals = ws.shared_sites(pdir)
    holders: dict[str, set] = {}   # address -> the distinct organisations listing it
    for r in rows:
        if r.get("kind") == "email":
            # one organisation pooled twice shares its own website; venues on a city portal do not
            site = org_site.get(r.get("org_id"))
            holders.setdefault(r["value"].lower(), set()).add(site if site and site not in portals else r.get("org_id"))
    common = common_words([str(o.get("name") or "") for o in ws.read_csv(pdir / "orgs.csv")])
    only = {str(o) for o in orgs} if orgs else None
    todo = [r for r in rows if r.get("kind") == "email" and not r.get("graded_by", "").startswith("agent")
            and (regrade or not r.get("grade")) and (only is None or str(r.get("org_id")) in only)]
    if limit:
        todo = todo[:limit]
    fields = ["grade", "graded_by", "grade_reason", "review", "review_reason"]
    # Addresses the brief grades by their name alone (brief.address_rules, e.g. {"info": "X"}):
    # no Jev call. The same address with the same text around it for the same organisation is
    # graded once; earlier grades of it are reused.
    known = {} if regrade else {_same(r): r for r in rows if r.get("graded_by") == "jev" and r.get("grade")}
    by_rule, reused, first, copies = [], [], {}, []
    for r in todo:
        g = ws.address_rule(pdir, r.get("value", ""))
        why = f"address rule: {g}"
        if not g:
            g, why = _rule_grade(r, common)
        if g:
            r.update(grade=g, graded_by="rule", grade_reason=why, review="", review_reason="")
            by_rule.append(r)
        elif _same(r) in known:
            r.update({f: known[_same(r)].get(f, "") for f in fields})
            reused.append(r)
        elif _same(r) in first:
            copies.append(r)
        else:
            first[_same(r)] = r
    todo = list(first.values())
    if not jev:   # no Jev: the rules' grades stand, the rest waits for an agent
        ws.update_contacts(pdir, by_rule + reused, fields, guard=lambda cur: not cur.get("graded_by", "").startswith("agent"))
        return {"graded": 0, "by_rule": len(by_rule), "reused": len(reused), "waiting_for_an_agent": len(todo) + len(copies),
                "note": "no usable Jev key: the rules graded what they can; agents grade the rest with grade(set=[...])"}
    import typesafe_sdk as sdk
    from concurrent.futures import ThreadPoolExecutor
    rub = rubric(pdir)
    b = ws.brief(pdir)
    focus = b.get("focus") or ""
    context = (f"Research goal: {b.get('goal', '')}\nRegion: {b.get('region', '')}\n"
               f"Exclusions: {b.get('exclude', '')}\nFocus: {json.dumps(focus, ensure_ascii=False) if focus else ''}").strip()
    counts = {"graded": 0, "failed": 0}

    # TypeSafe's own guidance (docs.typesafe.ai, typesafe-ai/skills): option names that describe
    # the content (letters bias the model), each candidate inside its own question rather than a
    # number in a shared list, a yes/no question (Noul) for "is a person named", the chosen
    # option's probability - not confidence - as the review signal, decisions in code.
    options = {OPTION[g]: text for g, text in rub.items()}
    state = {"research_brief": context, "how_to_judge": GUIDE}

    def card(r: dict) -> str:
        return (f"organisation: {r.get('org')}\nemail: {r.get('value')} (address words: "
                f"{_address_words(r.get('value', '')) or '-'}; email domain {r.get('email_site')}; "
                f"same site as the organisation: {r.get('same_site')})\n"
                f"page: {r.get('page_url')} (on the organisation's own site: {r.get('page_on_org_site') or '?'})\n"
                f"also listed for {len(holders.get(r['value'].lower(), ())) - 1} other organisations in this project\n"
                f"text around it: {r.get('quote')}")

    def one_batch(chunk):
        questions = {}
        for n, r in enumerate(chunk):
            questions[f"grade_{n + 1}"] = sdk.Choice(
                instructions=f"For `research_brief`, what kind of route to {r.get('org')} is this address?\n\n"
                             + card(r), criteria=options)
            questions[f"named_{n + 1}"] = sdk.Noul(
                instructions=f"Does this text name the person who uses {r.get('value')}?\n\n"
                             f"text: {r.get('quote')}")
        ok, _why = mo_budget.allowed("jev")
        if not ok:
            counts["failed"] += len(chunk)
            return
        try:
            resp = _jev(state, questions)
        except Exception as exc:  # noqa: BLE001
            text = f"{type(exc).__name__} {exc}".lower()
            if "402" in text or "quota" in text or "credit" in text or "payment" in text:
                mo_budget.mark_exhausted("jev", "Jev refused for money")   # the next key slot takes over
            elif "401" in text or "unauthorized" in text or "invalid api key" in text:
                mo_budget.mark_exhausted("jev", "Jev refused the key")
            mo_budget.record(pdir, "jev", "grade", False, f"{type(exc).__name__}: {exc}")
            counts["failed"] += len(chunk)
            return
        mo_budget.record(pdir, "jev", "grade", True, f"{len(chunk)} candidates")
        got = answers(resp)
        for n, r in enumerate(chunk):
            g, named = got.get(f"grade_{n + 1}"), got.get(f"named_{n + 1}")
            if g is None or getattr(g, "choice", None) not in GRADE_OF:
                counts["failed"] += 1
                continue
            grade, prob = GRADE_OF[g.choice], probability(g)
            p_named = yes(named)
            note = ""
            if grade == "A" and p_named is not None and p_named < 0.5:
                grade, note = "B", "A without a named person -> B"
            shared = len(holders.get(r["value"].lower(), ())) - 1
            why = []
            if grade in ("A", "B"):
                own = r.get("page_on_org_site") in ("yes", "likely") or r.get("email_on_org") in ("yes", "likely")
                if isinstance(prob, float) and prob < (REVIEW_BELOW_OWN_SITE if own else REVIEW_BELOW):
                    why.append(f"unsure grade (p={prob:.2f})")
                named_here = mentions(r.get("org", ""), r.get("quote", ""), common)
                if shared >= SHARED_REVIEW:
                    why.append(f"address also listed for {shared} other organisations")
                elif shared and not named_here:
                    # the same address under two organisations and neither named next to it:
                    # whose route it is was not read from the page, so a person decides
                    why.append("address also listed for another organisation, this one not named next to it")
                if r.get("page_on_org_site") == "no" and not named_here:
                    why.append("page on another site, organisation not named next to it")
                elif (r.get("page_on_org_site") == "unknown" and r.get("email_on_org") not in ("yes", "likely")
                      and not named_here):
                    why.append("neither the page nor the address is on the organisation's own site")
            r["grade"], r["graded_by"], r["review"] = grade, "jev", "yes" if why else ""
            r["review_reason"] = "; ".join(why)
            r["grade_reason"] = (f"jev {grade} p {prob:.2f}" if isinstance(prob, float) else f"jev {grade}") + \
                (f"; named p {p_named:.2f}" if p_named is not None else "") + (f"; {note}" if note else "") + \
                (f"; also listed for {shared} other orgs" if shared > 0 else "") + \
                ("; page on another site" if r.get("page_on_org_site") == "no" else "")
            counts["graded"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one_batch, [todo[i:i + batch] for i in range(0, len(todo), batch)]))
    for r in copies:
        src = first[_same(r)]
        if src.get("graded_by") == "jev":
            r.update({f: src.get(f, "") for f in fields})
    counts.update(by_rule=len(by_rule), reused=len(reused) + sum(r.get("graded_by") == "jev" for r in copies))
    ws.update_contacts(pdir, by_rule + reused + [r for r in todo + copies if r.get("graded_by") == "jev"], fields,
                       guard=lambda cur: not cur.get("graded_by", "").startswith("agent"))
    just = {(r.get("org_id"), r.get("value", "").lower()) for r in by_rule + reused + todo + copies
            if r.get("grade") in ("A", "B")}
    named = fill_people(pdir, only=just)
    # A means a named person (Bruce's contract): an A whose page gives no name after the name
    # look-up is a role address, so B
    no_name = [{**r, "grade": "B", "grade_reason": (r.get("grade_reason", "") + "; A but no name on the page -> B").lstrip("; ")}
               for r in ws.read_contacts(pdir) if r.get("grade") == "A" and r.get("graded_by") == "jev"
               and not str(r.get("person") or "").strip() and (r.get("org_id"), r.get("value", "").lower()) in just]
    if no_name:
        ws.update_contacts(pdir, no_name, ["grade", "grade_reason"])
    no_role = mark_missing_role(ws.read_contacts(pdir), just)
    if no_role:
        ws.update_contacts(pdir, no_role, ["review", "review_reason"])
    import mo_evidence
    evidence = mo_evidence.rescore(pdir)
    after = ws.read_contacts(pdir)
    left = sum(1 for r in after if r.get("kind") == "email" and not r.get("grade"))
    review = sum(1 for r in after if r.get("review") == "yes")
    return {**counts, "people_filled": named, "evidence": evidence, "still_ungraded": left, "to_review": review}


GRADES = ("A", "B", "C", "D", "X")
# Descriptive option names for Jev (the names are read by the model); grades stay letters in code.
OPTION = {"A": "decision_maker", "B": "operator_or_forwarder", "C": "team_inbox", "D": "general_inbox",
          "X": "not_a_route"}
GRADE_OF = {v: k for k, v in OPTION.items()}


def answers(resp) -> dict:
    """The answers by question id (`answers` in the current SDK, `choices` in older ones)."""
    return getattr(resp, "answers", None) or getattr(resp, "choices", None) or {}


def probability(a):
    """Probability of the chosen option; the confidence value only when there is none."""
    probs = getattr(a, "probabilities", None)
    if isinstance(probs, dict) and getattr(a, "choice", None) in probs:
        return float(probs[a.choice])
    conf = getattr(a, "confidence", None)
    return float(conf) if isinstance(conf, (int, float)) else None


def yes(a):
    """A Noul's probability of yes (None when there is no answer)."""
    if a is None:
        return None
    v = getattr(a, "noul", None)
    if isinstance(v, (int, float)):
        return float(v)
    c = getattr(a, "choice", None)   # older yes/no Choice answers
    return None if c is None else (1.0 if c == "yes" else 0.0)


FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.fr", "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr",
             "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net", "gmx.de", "gmx.net", "web.de",
             "t-online.de", "bluewin.ch", "icloud.com", "live.com", "aol.com", "btinternet.com"}


def _rule_grade(r: dict, common: dict) -> tuple[str, str]:
    """Grades that need no judgement (and no Jev call): the organisation's own plain inbox is D;
    an address on another site's page, on another domain, with the organisation not named next to
    it, is X. Kept and labelled - an agent can change either."""
    own_mail = r.get("email_on_org") in ("yes", "likely") or r.get("same_site") == "yes"
    if r.get("mailbox") == "generic" and own_mail:
        return "D", "rule: the organisation's own general inbox"
    site = str(r.get("email_site") or "").lower()
    if (r.get("page_on_org_site") == "no" and not own_mail and site not in FREE_MAIL
            and not mentions(r.get("org", ""), r.get("quote", ""), common)):
        return "X", "rule: not this organisation's (another site's page, another domain, organisation not named)"
    return "", ""


def _same(r: dict) -> tuple:
    """One address seen in the same text for the same organisation gets the same grade. The
    organisation is part of the key: the question names it and asks whether the page is its own,
    so a grade given for one organisation is not a grade for another."""
    return (str(r.get("org_id") or ""), r.get("value", "").lower(),
            " ".join(str(r.get("quote") or "").split()).lower())


def _segments(quote: str) -> list[str]:
    body = quote.split(" [next")[0]
    segs = []
    for part in re.split(r"\s+/\s+|\s+---\s+|\s+\|\s+", body):
        part = re.sub(r"\S+@\S+|\b(e-?mail|tel\.?|phone|natel|mobile?)\b:?", " ", part, flags=re.I)
        part = " ".join(part.split()).strip(" -,;:")
        if 2 < len(part) <= 90 and not re.fullmatch(r"[\d\s+()./-]+", part):
            segs.append(part)
    return list(dict.fromkeys(segs))[-8:]


def fill_people(pdir, only: set | None = None, batch: int = 6, workers: int = 8) -> int:
    """For A/B rows without a person: Jev picks, from the quote's own segments, the one that
    is the person's name and the one that is the job title (any language, any layout).
    only: the (org_id, address) rows to look at - the ones just graded; each address+text once."""
    if not mo_budget.jev_ready():
        return 0
    import typesafe_sdk as sdk
    from concurrent.futures import ThreadPoolExecutor
    rows = [r for r in ws.read_contacts(pdir) if r.get("kind") == "email" and r.get("grade") in ("A", "B")
            and not r.get("person") and not r.get("graded_by", "").startswith("agent")
            and (only is None or (r.get("org_id"), r.get("value", "").lower()) in only)]
    same: dict[tuple, list] = {}
    for r in rows:
        same.setdefault(_same(r), []).append(r)
    rows = [g[0] for g in same.values()]
    filled = {}

    def one(group):
        chunk = [(r, _segments(r.get("quote", ""))) for r in group]
        chunk = [(r, segs) for r, segs in chunk if segs]
        if not chunk:
            return
        questions, listing = {}, []
        for n, (r, segs) in enumerate(chunk):
            opts = {f"s{k + 1}": seg for k, seg in enumerate(segs)}
            listing.append(f"[{n + 1}] {r['value']}: " + " | ".join(f"s{k + 1}={seg}" for k, seg in enumerate(segs)))
            questions[f"person_{n + 1}"] = sdk.Choice(
                instructions=f"Which segment of [{n + 1}] is the name of the person who uses {r['value']}?",
                criteria={**opts, "none": "no person's name among them"})
            questions[f"role_{n + 1}"] = sdk.Choice(
                instructions=f"Which segment of [{n + 1}] is the job title or function tied to {r['value']}?",
                criteria={**opts, "none": "no job title among them"})
        try:
            resp = _jev({"candidates": "\n".join(listing)}, questions)
        except Exception as exc:  # noqa: BLE001
            mo_budget.record(pdir, "jev", "grade", False, f"{type(exc).__name__}: {exc}")
            return
        mo_budget.record(pdir, "jev", "grade", True, f"people x{len(chunk)}")
        choices = answers(resp)
        for n, (r, segs) in enumerate(chunk):
            got = {}
            for field in ("person", "role"):
                a = choices.get(f"{field}_{n + 1}")
                if a is not None and a.choice != "none" and a.choice.startswith("s"):
                    k = int(a.choice[1:]) - 1
                    if 0 <= k < len(segs):
                        got[field] = segs[k]
            p = got.get("person", "")
            if ":" in p:  # "Präsident: Hans Muster 079 ..." -> role and name
                left, right = p.split(":", 1)
                got["person"] = right
                got.setdefault("role", left.strip())
            if got.get("person"):
                got["person"] = " ".join(re.sub(r"[\d+()/.-]{3,}.*$", "", got["person"]).split())
            if got.get("person") or got.get("role"):
                for twin in same[_same(r)]:
                    filled[(twin["org_id"], twin["value"].lower())] = got

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, [rows[i:i + batch] for i in range(0, len(rows), batch)]))
    if filled:
        updates = [{"org_id": k[0], "value": v_, **got} for (k, got) in filled.items() for v_ in [k[1]]]
        for u in updates:
            u.setdefault("person", "")
            u.setdefault("role", "")
        ws.update_contacts(pdir, updates, ["person", "role"], guard=lambda cur: not cur.get("person"))
    return len(filled)


def conflicts(pdir, row: dict) -> list[str]:
    """Where an agent's A or B disagrees with the grade table or the brief: kept, but marked for
    review. (073 smoke: a worker on a small model set accueil@, contact@ and info@ to B - D in the
    table, and info@ X by the brief's address_rules - and nothing showed it.)"""
    import re as _re
    import mo_pages
    g, value = row.get("grade"), str(row.get("value") or "")
    if g not in ("A", "B") or row.get("kind", "email") != "email":
        return []
    out = []
    head = _re.split(r"[._-]", value.split("@")[0].lower())[0]
    rule = ws.address_rule(pdir, value)
    if rule and rule not in ("A", "B"):
        out.append(f"the brief's address_rules grade this address {rule}")
    elif head in mo_pages.GENERIC:
        out.append(f"{head}@ is a general inbox: D in the grade table (C if it is the venue/function's own)")
    if g == "A" and not str(row.get("person") or "").strip():
        out.append("A needs the named person (person)")
    if not str(row.get("role") or "").strip():
        out.append(f"{g} needs the role the page gives (role)")
    return out


def manual(pdir, updates: list[dict]) -> dict:
    """The agent sets grade/person/role/note on rows, matched by value (+ org_id if given).
    An A or B that disagrees with the grade table or the brief is kept and marked review with
    the reason (confirm=True on the update: the agent has looked, no mark)."""
    rows = ws.read_contacts(pdir)
    picked, checks = [], []
    for u in updates:
        value = str(u.get("value") or "").lower()
        for r in rows:
            if r.get("value", "").lower() == value and (not u.get("org_id") or r.get("org_id") == str(u["org_id"])):
                for f in ("grade", "person", "role", "note", "grade_reason", "checked"):
                    if f in u:
                        r[f] = str(u[f])
                if any(f in u for f in ("grade", "person", "role")):   # a spot check alone changes nothing else
                    r["graded_by"], r["review"], r["review_reason"] = "agent", "", ""
                    why = [] if u.get("confirm") else conflicts(pdir, r)
                    if why:
                        r["review"], r["review_reason"] = "yes", "agent grade: " + "; ".join(why)
                        checks.append({"value": r["value"], "org_id": r.get("org_id"), "grade": r["grade"], "why": why})
                picked.append(r)
    changed = ws.update_contacts(pdir, picked, ["grade", "person", "role", "note", "grade_reason", "graded_by",
                                                "review", "review_reason", "checked"])
    import mo_evidence
    mo_evidence.rescore(pdir)
    out: dict = {"updated": changed}
    if checks:
        table = rubric(pdir)
        out["check"] = checks
        out["grade_table"] = {g: table[g] for g in sorted({c["grade"] for c in checks} | {"C", "D"})}
        out["note"] = ("these A/B rows disagree with the grade table or the brief and are marked review: correct "
                       "them, or set them again with confirm=true if the page really says so")
    return out


def judge(pdir, file: str, question: str, options: dict, column: str, fields: list | None = None,
          limit: int = 300, redo: bool = False, batch: int = 10) -> dict:
    """Jev answers one multiple-choice question for each row of a project CSV.

    The answer goes into `column`, its confidence into `column`_conf. Rows that already have
    an answer are skipped unless redo. Use it wherever a decision repeats over many rows:
    is this venue in scope, is this the organisation's own site, is this a duplicate.
    """
    if not mo_budget.jev_ready():
        return {"judged": 0, "note": "no Jev key: decide yourself and edit the CSV"}
    if not isinstance(options, dict) or len(options) < 2:
        return {"error": "options: at least two {answer: what it means}"}
    import typesafe_sdk as sdk
    path = pdir / file
    if path.resolve().parent != pdir.resolve() or path.suffix != ".csv":
        return {"error": "file: a .csv in the project folder"}
    contacts = file == "contacts.csv"
    rows = ws.read_contacts(pdir) if contacts else ws.read_csv(path)
    todo = [r for r in rows if redo or not r.get(column)][:limit]
    judged = failed = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        listing = "\n".join(f"[{n + 1}] " + " | ".join(f"{k}: {str(r.get(k, ''))[:300]}"
                                                     for k in (fields or [k for k in r if not k.endswith('_conf')]))
                            for n, r in enumerate(chunk))
        qs = {f"q{n + 1}": sdk.Choice(instructions=f"{question} - answer for row [{n + 1}].",
                                      criteria={str(k): str(v) for k, v in options.items()})
              for n in range(len(chunk))}
        try:
            resp = _jev({"rows": listing, "context": json.dumps(ws.brief(pdir), ensure_ascii=False)[:2000]}, qs)
        except Exception as exc:  # noqa: BLE001
            mo_budget.record(pdir, "jev", "grade", False, f"{type(exc).__name__}: {exc}")
            failed += len(chunk)
            continue
        mo_budget.record(pdir, "jev", "grade", True, f"judge {column} x{len(chunk)}")
        choices = answers(resp)
        for n, r in enumerate(chunk):
            a = choices.get(f"q{n + 1}")
            if a is None:
                failed += 1
                continue
            r[column] = a.choice
            conf = getattr(a, "confidence", None)
            r[column + "_conf"] = f"{conf:.2f}" if isinstance(conf, float) else ""
            judged += 1
    if contacts:
        ws.update_contacts(pdir, todo, [column, column + "_conf"])
        rows = ws.read_contacts(pdir)
    else:
        with ws._lock(pdir / "pools" if file in ("orgs.csv", "venues.csv") else path):
            fresh = ws.read_csv(path)
            if len(fresh) == len(rows):  # nobody rewrote the file meanwhile: keep their edits, add ours
                for old, new in zip(rows, fresh):
                    if column in old:
                        new[column], new[column + "_conf"] = old[column], old.get(column + "_conf", "")
                rows = fresh
            cols = list(rows[0].keys()) if rows else []
            ws.write_csv(path, cols, rows)
    counts: dict = {}
    for r in rows:
        counts[r.get(column, "")] = counts.get(r.get(column, ""), 0) + 1
    return {"judged": judged, "failed": failed, "answers": counts}

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

# Bruce's grading contract (fjd-email-role-qualification), used when the brief has none.
DEFAULT_GRADES = {
    "A": "A named person who decides on or owns the venue, grounds or pitch maintenance, or the "
         "purchase: head of grounds/facilities/estates, sports department head, club president or "
         "the board member responsible for the ground. Their own email.",
    "B": "Anyone in the organisation's leadership or operations who can act on or forward a "
         "grounds/pitch enquiry: groundsman, pitch/site/venue/operations manager, caretaker "
         "(Platzwart, Hauswart), infrastructure lead, other board or committee members, club "
         "secretary or manager; or a role mailbox for that function (grounds@, facilities@, "
         "platz@, vorstand@, infrastruktur@).",
    "C": "An official entry of the organisation that reaches a team rather than a person: "
         "bookings/hire, sports office, department inbox, administration of the facility.",
    "D": "A generic official inbox (info@, office@, contact@, secretariat) - still a usable "
         "first contact.",
    "X": "Clearly not a route for this organisation: another company's address (sponsor, web "
         "designer, partner), no-reply, press only, or a private person with no role here.",
}


def rubric(pdir) -> dict:
    grades = ws.brief(pdir).get("grades") or {}
    return {g: grades.get(g) or DEFAULT_GRADES[g] for g in "ABCDX"}


def _jev(state: dict, questions: dict):
    import typesafe_sdk as sdk
    with sdk.TypeSafeClient(
            api_key=mo_config.key("jev"), timeout=120,
            model=os.environ.get("MARKETOPS_JEV_MODEL") or None,
            retry=sdk.RetryPolicy(max_retries=1)) as client:
        return client.system_one(state, questions)


GUIDE = ("Read any language. Judge job titles and abbreviations by this kind of organisation (in a "
         "school 'FS' is Foundation Stage, not facilities). An address whose own words name a role "
         "(facilities@, grounds@, groundsman@, caretaker@, bursar@, sbm@ = school business manager, "
         "platzwart@, estates@) counts for that role even when no person is named. If the address "
         "is also listed for other organisations, or the page belongs to another organisation (a "
         "directory, a council or canton staff list), give A or B only when the text ties the "
         "person to THIS organisation or its venue; otherwise C or X. Grade only this address, not "
         "the neighbouring entries. A supporters' or sponsors' club, a festival or bar team, or a "
         "coach of one team only is not the venue's decision or operations side.")
REVIEW_BELOW = 0.6


def _address_words(email: str) -> str:
    local = email.split("@")[0]
    return " ".join(w for w in re.split(r"[._\-+0-9]+", local) if len(w) > 1)


def auto(pdir, limit: int = 0, regrade: bool = False, batch: int = 8, workers: int = 8) -> dict:
    """Jev grades the email candidates that have no grade yet (or all, with regrade). limit 0 = all."""
    if not mo_config.key("jev"):
        return {"graded": 0, "note": "no Jev key: read the quotes and set grades with grade(set=[...])"}
    import typesafe_sdk as sdk
    from concurrent.futures import ThreadPoolExecutor
    path = pdir / "contacts.csv"
    rows = ws.read_csv(path)
    import mo_pages
    org_site = {str(o.get("id") or o.get("org_id") or ""): mo_pages.site_of(str(o.get("website") or ""))
                for o in ws.read_csv(pdir / "orgs.csv")}
    holders: dict[str, set] = {}   # address -> the distinct organisations (by website) listing it
    for r in rows:
        if r.get("kind") == "email":
            holders.setdefault(r["value"].lower(), set()).add(org_site.get(r.get("org_id")) or r.get("org_id"))
    todo = [r for r in rows if r.get("kind") == "email" and not r.get("graded_by", "").startswith("agent")
            and (regrade or not r.get("grade"))]
    if limit:
        todo = todo[:limit]
    rub = rubric(pdir)
    b = ws.brief(pdir)
    focus = b.get("focus") or ""
    context = (f"Research goal: {b.get('goal', '')}\nRegion: {b.get('region', '')}\n"
               f"Exclusions: {b.get('exclude', '')}\nFocus: {json.dumps(focus, ensure_ascii=False) if focus else ''}").strip()
    counts = {"graded": 0, "failed": 0}

    def one_batch(chunk):
        listing = "\n\n".join(
            f"[{n + 1}] organisation: {r.get('org')}\nemail: {r.get('value')} (address words: "
            f"{_address_words(r.get('value', '')) or '-'}; email domain {r.get('email_site')}; "
            f"same site as the organisation: {r.get('same_site')})\n"
            f"page: {r.get('page_url')} (on the organisation's own site: {r.get('page_on_org_site') or '?'})\n"
            f"also listed for {len(holders.get(r['value'].lower(), ())) - 1} other organisations in this project\n"
            f"text around it: {r.get('quote')}"
            for n, r in enumerate(chunk))
        questions = {}
        for n, r in enumerate(chunk):
            questions[f"grade_{n + 1}"] = sdk.Choice(
                instructions=f"Grade candidate [{n + 1}] ({r.get('value')}) for the research goal. " + GUIDE,
                criteria=rub)
            questions[f"named_{n + 1}"] = sdk.Choice(
                instructions=f"Does the text around candidate [{n + 1}] name a real person who uses "
                             f"{r.get('value')}?", criteria={"yes": "a person's name is tied to it",
                                                             "no": "no person's name tied to it"})
        ok, _why = mo_budget.allowed("jev")
        if not ok:
            counts["failed"] += len(chunk)
            return
        try:
            resp = _jev({"brief": context, "candidates": listing}, questions)
        except Exception as exc:  # noqa: BLE001
            text = f"{type(exc).__name__} {exc}".lower()
            if "402" in text or "quota" in text or "credit" in text or "payment" in text:
                mo_budget.mark_exhausted("jev", "Jev refused for money")
            mo_budget.record(pdir, "jev", "grade", False, f"{type(exc).__name__}: {exc}")
            counts["failed"] += len(chunk)
            return
        mo_budget.record(pdir, "jev", "grade", True, f"{len(chunk)} candidates")
        choices = getattr(resp, "choices", None) or {}
        for n, r in enumerate(chunk):
            g, named = choices.get(f"grade_{n + 1}"), choices.get(f"named_{n + 1}")
            if g is None:
                counts["failed"] += 1
                continue
            grade, conf = g.choice, getattr(g, "confidence", None)
            note = ""
            if grade == "A" and named is not None and named.choice == "no":
                grade, note = "B", "A without a named person -> B"
            shared = len(holders.get(r["value"].lower(), ())) - 1
            why = []
            if grade in ("A", "B"):
                if isinstance(conf, float) and conf < REVIEW_BELOW:
                    why.append(f"low confidence {conf:.2f}")
                if shared > 0:
                    why.append(f"address also listed for {shared} other organisations")
                if r.get("page_on_org_site") == "no":
                    why.append("page on another site")
            r["grade"], r["graded_by"], r["review"] = grade, "jev", "yes" if why else ""
            r["review_reason"] = "; ".join(why)
            r["grade_reason"] = (f"jev {grade} conf {conf:.2f}" if isinstance(conf, float) else f"jev {grade}") + \
                (f"; named={named.choice}" if named is not None else "") + (f"; {note}" if note else "") + \
                (f"; also listed for {shared} other orgs" if shared > 0 else "") + \
                ("; page on another site" if r.get("page_on_org_site") == "no" else "")
            counts["graded"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one_batch, [todo[i:i + batch] for i in range(0, len(todo), batch)]))
    with ws._lock(path):
        current = {(r["org_id"], r["value"].lower()): r for r in ws.read_csv(path)}
        for r in todo:
            k = (r["org_id"], r["value"].lower())
            if k in current and r.get("graded_by") == "jev" and not current[k].get("graded_by", "").startswith("agent"):
                current[k].update({f: r.get(f, "") for f in ("grade", "graded_by", "grade_reason", "review", "review_reason")})
        ws.write_csv(path, ws.CONTACT_COLS, list(current.values()))
    named = fill_people(pdir)
    import mo_evidence
    evidence = mo_evidence.rescore(pdir)
    left = sum(1 for r in ws.read_csv(path) if r.get("kind") == "email" and not r.get("grade"))
    review = sum(1 for r in ws.read_csv(path) if r.get("review") == "yes")
    return {**counts, "people_filled": named, "evidence": evidence, "still_ungraded": left, "to_review": review}


def _segments(quote: str) -> list[str]:
    body = quote.split(" [next")[0]
    segs = []
    for part in re.split(r"\s+/\s+|\s+---\s+|\s+\|\s+", body):
        part = re.sub(r"\S+@\S+|\b(e-?mail|tel\.?|phone|natel|mobile?)\b:?", " ", part, flags=re.I)
        part = " ".join(part.split()).strip(" -,;:")
        if 2 < len(part) <= 90 and not re.fullmatch(r"[\d\s+()./-]+", part):
            segs.append(part)
    return list(dict.fromkeys(segs))[-8:]


def fill_people(pdir, batch: int = 6) -> int:
    """For A/B rows without a person: Jev picks, from the quote's own segments, the one that
    is the person's name and the one that is the job title (any language, any layout)."""
    if not mo_config.key("jev"):
        return 0
    import typesafe_sdk as sdk
    path = pdir / "contacts.csv"
    rows = [r for r in ws.read_csv(path) if r.get("kind") == "email" and r.get("grade") in ("A", "B")
            and not r.get("person") and not r.get("graded_by", "").startswith("agent")]
    filled = {}
    for i in range(0, len(rows), batch):
        chunk = [(r, _segments(r.get("quote", ""))) for r in rows[i:i + batch]]
        chunk = [(r, segs) for r, segs in chunk if segs]
        if not chunk:
            continue
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
            continue
        mo_budget.record(pdir, "jev", "grade", True, f"people x{len(chunk)}")
        choices = getattr(resp, "choices", None) or {}
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
                filled[(r["org_id"], r["value"].lower())] = got
    if filled:
        with ws._lock(path):
            current = ws.read_csv(path)
            for r in current:
                got = filled.get((r.get("org_id"), r.get("value", "").lower()))
                if got and not r.get("person"):
                    r.update(got)
            ws.write_csv(path, ws.CONTACT_COLS, current)
    return len(filled)


def manual(pdir, updates: list[dict]) -> dict:
    """The agent sets grade/person/role/note on rows, matched by value (+ org_id if given)."""
    path = pdir / "contacts.csv"
    changed = 0
    with ws._lock(path):
        rows = ws.read_csv(path)
        for u in updates:
            value = str(u.get("value") or "").lower()
            for r in rows:
                if r.get("value", "").lower() == value and (not u.get("org_id") or r.get("org_id") == str(u["org_id"])):
                    for f in ("grade", "person", "role", "note", "grade_reason"):
                        if f in u:
                            r[f] = str(u[f])
                    r["graded_by"] = "agent"
                    r["review"] = ""
                    changed += 1
        ws.write_csv(path, ws.CONTACT_COLS, rows)
    import mo_evidence
    mo_evidence.rescore(pdir)
    return {"updated": changed}


def judge(pdir, file: str, question: str, options: dict, column: str, fields: list | None = None,
          limit: int = 300, redo: bool = False, batch: int = 10) -> dict:
    """Jev answers one multiple-choice question for each row of a project CSV.

    The answer goes into `column`, its confidence into `column`_conf. Rows that already have
    an answer are skipped unless redo. Use it wherever a decision repeats over many rows:
    is this venue in scope, is this the organisation's own site, is this a duplicate.
    """
    if not mo_config.key("jev"):
        return {"judged": 0, "note": "no Jev key: decide yourself and edit the CSV"}
    if not isinstance(options, dict) or len(options) < 2:
        return {"error": "options: at least two {answer: what it means}"}
    import typesafe_sdk as sdk
    path = pdir / file
    if path.resolve().parent != pdir.resolve() or path.suffix != ".csv":
        return {"error": "file: a .csv in the project folder"}
    rows = ws.read_csv(path)
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
        choices = getattr(resp, "choices", None) or {}
        for n, r in enumerate(chunk):
            a = choices.get(f"q{n + 1}")
            if a is None:
                failed += 1
                continue
            r[column] = a.choice
            conf = getattr(a, "confidence", None)
            r[column + "_conf"] = f"{conf:.2f}" if isinstance(conf, float) else ""
            judged += 1
    with ws._lock(path):
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

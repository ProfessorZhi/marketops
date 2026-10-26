"""Grade contact candidates A/B/C/D/X against the brief, in any language.

Jev reads each candidate's quote and picks the grade the brief describes. The agent can
also set grades, people and roles itself (it reads quotes as well as Jev does); a row the
agent set is never overwritten by Jev.
"""
from __future__ import annotations

import json
import os

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


def auto(pdir, limit: int = 200, regrade: bool = False, batch: int = 8) -> dict:
    """Jev grades the email candidates that have no grade yet (or all, with regrade)."""
    if not mo_config.key("jev"):
        return {"graded": 0, "note": "no Jev key: read the quotes and set grades with grade(set=[...])"}
    import typesafe_sdk as sdk
    path = pdir / "contacts.csv"
    rows = ws.read_csv(path)
    todo = [r for r in rows if r.get("kind") == "email" and not r.get("graded_by", "").startswith("agent")
            and (regrade or not r.get("grade") or r.get("graded_by", "").startswith("rule"))][:limit]
    rub = rubric(pdir)
    b = ws.brief(pdir)
    context = (f"Research goal: {b.get('goal', '')}\nRegion: {b.get('region', '')}\n"
               f"Exclusions: {b.get('exclude', '')}\n").strip()
    graded, failed = 0, 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        listing = "\n\n".join(
            f"[{n + 1}] organisation: {r.get('org')} (site {r.get('email_site')}, same site as page: {r.get('same_site')})\n"
            f"email: {r.get('value')}\npage: {r.get('page_url')}\ntext around it: {r.get('quote')}"
            for n, r in enumerate(chunk))
        state = {"brief": context, "candidates": listing}
        questions = {}
        for n, r in enumerate(chunk):
            questions[f"grade_{n + 1}"] = sdk.Choice(
                instructions=f"Grade candidate [{n + 1}] ({r.get('value')}) for the research goal, "
                             "using only its text around it. Read any language.",
                criteria=rub)
            questions[f"named_{n + 1}"] = sdk.Choice(
                instructions=f"Does the text around candidate [{n + 1}] name a real person who uses "
                             f"{r.get('value')}?", criteria={"yes": "a person's name is tied to it",
                                                             "no": "no person's name tied to it"})
        ok, why = mo_budget.allowed("jev")
        if not ok:
            break
        try:
            resp = _jev(state, questions)
        except Exception as exc:  # noqa: BLE001
            text = f"{type(exc).__name__} {exc}".lower()
            if "402" in text or "quota" in text or "credit" in text or "payment" in text:
                mo_budget.mark_exhausted("jev", "Jev refused for money")
            mo_budget.record(pdir, "jev", "grade", False, f"{type(exc).__name__}: {exc}")
            failed += len(chunk)
            continue
        mo_budget.record(pdir, "jev", "grade", True, f"{len(chunk)} candidates")
        choices = getattr(resp, "choices", None) or {}
        for n, r in enumerate(chunk):
            g, named = choices.get(f"grade_{n + 1}"), choices.get(f"named_{n + 1}")
            if g is None:
                failed += 1
                continue
            grade = g.choice
            conf = getattr(g, "confidence", None)
            if grade == "A" and named is not None and named.choice == "no":
                grade, note = "B", "A without a named person -> B"
            else:
                note = ""
            r["grade"] = grade
            r["graded_by"] = "jev"
            r["grade_reason"] = (f"jev {grade} conf {conf:.2f}" if isinstance(conf, float) else f"jev {grade}") + \
                (f"; named={named.choice}" if named is not None else "") + (f"; {note}" if note else "")
            graded += 1
    with ws._lock(path):
        current = {(r["org_id"], r["value"].lower()): r for r in ws.read_csv(path)}
        for r in todo:
            k = (r["org_id"], r["value"].lower())
            if k in current and r.get("graded_by") == "jev":
                current[k].update({f: r[f] for f in ("grade", "graded_by", "grade_reason")})
        ws.write_csv(path, ws.CONTACT_COLS, list(current.values()))
    left = sum(1 for r in ws.read_csv(path) if r.get("kind") == "email" and not r.get("grade"))
    return {"graded": graded, "failed": failed, "still_ungraded": left}


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
                    changed += 1
        ws.write_csv(path, ws.CONTACT_COLS, rows)
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

#!/usr/bin/env python3
"""Score a research run against the published rubric, offline and deterministically.

The owner's acceptance document (§3) asks for a two-pass audit over a blind,
independently checkable subset, names ten measurements, and lists the zero-tolerance
failures. W-QA's fixtures fix the rubric (grades A..F), the twelve anti-fabrication
checks and the cost-ledger shape. This module is the scorer that applies them to a
run's own artefacts; it does not define a second rubric.

## Three rules it will not bend

* **Derived over declared.** A row's `source_grade` is what the producer said. The
  scorer works out the *domain class* of the page the row actually cites, from the
  stored lineage, and compares. Claiming an evidence grade for a directory page is an
  `OVERCLAIM` and the row cannot be accepted on it. Claiming a *weaker* grade than the
  cited page supports is `UNDERCLAIM`: recorded, not fatal, because it is not the
  direction that misleads.
* **Unevaluable is BLOCKED, never a pass.** Each check names the artefact it reads. If
  that artefact was not supplied the check is BLOCKED with the excuse naming it, and —
  per the rubric's own evaluation rule — a BLOCKED check prevents a PASS verdict.
  `PASS` here means "everything this run made checkable was checked and held".
* **Unknown money is UNKNOWN.** A dispatched charge whose actual is unreconciled makes
  cost-per-accepted-row `PARTIAL_UNKNOWN` and the confirmed part is reported as the
  confirmed part. It is never rounded to zero, and the all-in figure is a separate
  field so the two cannot be mistaken for each other.

## What it emits

A `marketops.quality.scorecard.v1` document plus a flat CSV of the measurements, both
deterministic: same inputs, byte-identical output, so a re-check can compare them and
a reader can pin the run by the input hashes in `inputs.sha256`. The emitted document
carries identifiers, closed-set codes and numbers — never a page's text, a contact
value or a person's name, which is what makes it safe to publish.

No network call, no credential, no provider.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

SCHEMA = "marketops.quality.scorecard.v1"
#: Six places. Money and ratios are emitted as decimal strings, never floats.
PLACES = Decimal("0.000001")

#: States a measurement can be in. `MEASURED` is the only one that is an answer.
MEASURED, PARTIAL_UNKNOWN, BLOCKED, UNKNOWN = (
    "MEASURED", "PARTIAL_UNKNOWN", "BLOCKED", "UNKNOWN")

#: Verdicts. PASS requires every check to have been evaluated and held.
PASS, FAIL, BLOCKED_VERDICT = "PASS", "FAIL", "BLOCKED"

#: Domain classes, derived from the page a row cites — never from the row's claim.
SELF, PARENT_BODY, THIRD_PARTY_REFERENCE, DIRECTORY, NO_RETRIEVED_PAGE, CHALLENGE, ABSENT = (
    "SELF", "PARENT_BODY", "THIRD_PARTY_REFERENCE", "DIRECTORY",
    "NO_RETRIEVED_PAGE", "CHALLENGE", "ABSENT")
#: The two classes the rubric counts as evidence. The A/B split inside them is about
#: whether the page states the fact directly, which is a reading, not a derivation —
#: so the scorer checks the class and leaves the directness to the declared grade.
EVIDENCE_CLASSES = frozenset({SELF, PARENT_BODY})

#: Markers a challenge/login/consent wall leaves in the bytes. Matched against stored
#: material rather than trusting a flag, because a flag can be missing.
CHALLENGE_MARKERS = (
    "verify you are human", "captcha", "are you a robot", "enable javascript and cookies",
    "checking your browser", "attention required", "sign in to continue",
)

#: Credential shapes worth failing AF-10 on. Deliberately anchored to known prefixes:
#: a bare long hex string is a digest, and digests are everywhere in this data.
CREDENTIAL_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}=*"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9._~+/-]{16,}"),
)

#: States that mean a call was charged (or is held as an unknown charge).
CHARGED_STATES = frozenset({"SETTLED", "FREE_TIER", "UNKNOWN_CHARGE_OUTSTANDING"})

#: The claim must cover these; the other required fields are metadata, not quotations.
QUOTED_FIELDS = ("organisation_name", "venue_address_public", "official_contact_route")

#: Two-label registrable-domain approximation. A full public-suffix list is not
#: available offline, so the common multi-part suffixes are enumerated and the
#: assumption is reported in the scorecard rather than left implicit.
MULTI_PART_SUFFIXES = frozenset({
    "co.uk", "gov.uk", "org.uk", "ac.uk", "sch.uk", "me.uk", "net.uk",
    "com.au", "net.au", "org.au", "co.jp", "co.nz", "com.br", "co.za",
})
DOMAIN_SUFFIX_ASSUMPTION = (
    "registrable domain approximated as the last two labels, extended by a "
    "hard-coded list of multi-part suffixes; no public-suffix list is available offline")


class ScorecardError(ValueError):
    """The inputs cannot support a scorecard. Raised rather than guessed around."""


# ------------------------------------------------------------------ io helpers
def _load_json(path: Path | str | None, *, required: bool = True):
    if path is None or not Path(path).is_file():
        if required:
            raise ScorecardError(f"SCORECARD_INPUT_MISSING: {path}")
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_jsonl(path: Path | str | None) -> list[dict] | None:
    if path is None or not Path(path).is_file():
        return None
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            items.append(json.loads(line))
    return items


def _sha256_file(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inputs_from_paths(paths: dict) -> dict:
    """Every input the scorer takes, loaded from the paths a run produced.

    Optional inputs stay `None` rather than becoming empty collections: "no reviewer
    decisions were supplied" and "the reviewer decided nothing" are different facts
    and the scorecard reports them differently.
    """
    paths = dict(paths)
    return {
        "paths": paths,
        "brief": _load_json(paths.get("brief")),
        "rubric": _load_json(paths.get("rubric")),
        "checks": _load_json(paths.get("checks")),
        "rows": _load_jsonl(paths.get("rows")) or [],
        "lineage": _load_json(paths.get("lineage")) or [],
        "targets": _load_jsonl(paths.get("targets")) or [],
        "reviews": _load_jsonl(paths.get("reviews")),
        "ledger": _load_json(paths.get("ledger"), required=False),
        "receipts": _load_jsonl(paths.get("receipts")),
    }


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - a malformed amount is not zero
        raise ScorecardError(f"SCORECARD_AMOUNT_NOT_A_DECIMAL: {value!r}") from None


def _ratio(numerator: int, denominator: int) -> str:
    if not denominator:
        return "0.000000"
    return str((Decimal(numerator) / Decimal(denominator)).quantize(PLACES, ROUND_HALF_UP))


def _money(value: Decimal) -> str:
    return str(value.quantize(PLACES, ROUND_HALF_UP))


# --------------------------------------------------------------- domain helpers
def registrable_domain(host: str) -> str:
    host = (host or "").strip().lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _host_of(url: str) -> str:
    text = (url or "").strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    return text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]


def _searchable(record: dict, raw: bytes) -> str:
    """The text a claim's quote is looked for in. Whitespace is collapsed so a
    re-wrapped page does not read as a missing excerpt."""
    text = raw.decode("utf-8", errors="replace")
    if record.get("material_kind") in {"rendered_html", "raw_response"}:
        text = re.sub(r"(?is)<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


def looks_like_challenge(raw: bytes) -> bool:
    text = raw.decode("utf-8", errors="replace").casefold()
    return any(marker in text for marker in CHALLENGE_MARKERS)


def domain_class(cited_ids: list[str], lineage: dict[str, dict], row: dict,
                 brief: dict, *, challenge: bool = False) -> str:
    if not cited_ids:
        return NO_RETRIEVED_PAGE
    record = lineage.get(cited_ids[0])
    if record is None:
        return ABSENT
    return _class_of(record, row, brief, challenge=challenge)


def _same_site(host: str, domain: str) -> bool:
    """A host belongs to a domain when it is that domain or a subdomain of it.

    Compared on the full host rather than the registrable domain: `a.example.org` and
    `b.example.org` share a registrable domain but are not the same publisher, and
    collapsing them would grade one organisation's page as another's own site.
    """
    host, domain = _normalise_host(host), _normalise_host(domain)
    return bool(host) and bool(domain) and (host == domain or host.endswith("." + domain))


def _normalise_host(host: str) -> str:
    host = (host or "").strip().lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def _class_of(record: dict, row: dict, brief: dict, *, challenge: bool = False) -> str:
    # A challenge wall is grade F wherever it was served from: a CAPTCHA on the
    # organisation's own domain is still not a page that states anything.
    if challenge:
        return CHALLENGE
    host = _host_of(record.get("origin_url", ""))
    fields = row.get("fields") or {}
    if _same_site(host, fields.get("official_domain", "")):
        return SELF
    if any(_same_site(host, d)
           for d in (row.get("same_operator_official_domains") or [])):
        return PARENT_BODY
    if any(_same_site(host, d) for d in
           (brief.get("input", {}).get("third_party_authoritative_domains") or [])):
        return THIRD_PARTY_REFERENCE
    return DIRECTORY


def grade_agreement(declared: str | None, derived_class: str) -> str:
    """Whether the declared grade could have come from the page the row cites."""
    claims_evidence = declared in {"A", "B"}
    is_evidence = derived_class in EVIDENCE_CLASSES
    if claims_evidence and not is_evidence:
        return "OVERCLAIM"
    if not claims_evidence and is_evidence:
        return "UNDERCLAIM"
    return "AGREE"


# ------------------------------------------------------------------- the scoring
class _Run:
    """Everything the scorer reads, resolved once so each row sees one snapshot."""

    def __init__(self, *, brief, rubric, checks, rows, lineage, targets, reviews,
                 ledger, receipts, paths):
        self.paths = paths
        self.brief = brief or {}
        self.rubric = rubric or {}
        self.checks = checks or {}
        self.rows = rows or []
        self.targets = targets or []
        self.reviews = reviews
        self.ledger = ledger
        self.receipts = receipts
        self.lineage = {r["artifact_id"]: r for r in lineage}
        self.artifacts_dir = Path(paths["artifacts_dir"]) if paths.get("artifacts_dir") else None
        self.bytes: dict[str, bytes | None] = {}
        self.required_fields = tuple(
            self.brief.get("input", {}).get("required_fields_per_target")
            or ("organisation_name", "venue_address_public", "official_domain",
                "official_contact_route", "source_url", "source_excerpt_sha256"))

    def raw(self, artifact_id: str) -> bytes | None:
        if artifact_id not in self.bytes:
            self.bytes[artifact_id] = None
            if artifact_id in self.lineage and self.artifacts_dir is not None:
                candidate = self.artifacts_dir / f"{artifact_id}.bin"
                if candidate.is_file():
                    self.bytes[artifact_id] = candidate.read_bytes()
        return self.bytes[artifact_id]

    def hash_ok(self, artifact_id: str) -> bool:
        raw = self.raw(artifact_id)
        record = self.lineage.get(artifact_id)
        if raw is None or record is None:
            return False
        return hashlib.sha256(raw).hexdigest() == record.get("content_sha256")

    def challenge_artifacts(self) -> list[str]:
        """Artifacts whose *stored bytes* carry challenge markers, flag or no flag."""
        found = []
        for artifact_id, record in sorted(self.lineage.items()):
            raw = self.raw(artifact_id)
            if raw is not None and looks_like_challenge(raw):
                found.append(artifact_id)
        return found


def _derive_row(row: dict, run: _Run) -> dict:
    cited = list(row.get("cited_artifact_ids") or [])
    primary = cited[0] if cited else ""
    derived = domain_class(cited, run.lineage, row, run.brief,
                           challenge=primary in run.challenge_artifacts())
    declared = row.get("source_grade")
    agreement = grade_agreement(declared, derived)
    fields = row.get("fields") or {}
    reasons: list[str] = []
    covered: list[str] = []

    for artifact_id in cited:
        if artifact_id not in run.lineage:
            reasons.append("CITED_ARTIFACT_NOT_IN_LINEAGE")
        elif not run.hash_ok(artifact_id):
            reasons.append("CITED_ARTIFACT_BYTES_TAMPERED")

    for claim in row.get("claims") or []:
        artifact_id = str(claim.get("artifact_id", ""))
        field = str(claim.get("field", ""))
        record = run.lineage.get(artifact_id)
        if record is None:
            reasons.append("CLAIM_ARTIFACT_NOT_IN_LINEAGE")
            continue
        raw = run.raw(artifact_id)
        if raw is None or not run.hash_ok(artifact_id):
            reasons.append("CLAIM_ARTIFACT_BYTES_TAMPERED")
            continue
        quote = " ".join(str(claim.get("quote", "")).split())
        if not quote or quote not in _searchable(record, raw):
            reasons.append("EXCERPT_NOT_IN_STORED_MATERIAL")
            continue
        value = fields.get(field)
        if isinstance(value, str) and value and value not in quote:
            reasons.append(f"EXCERPT_LACKS_FIELD_VALUE:{field}")
            continue
        if claim.get("artifact_id") in run.challenge_artifacts():
            reasons.append("EXCERPT_FROM_CHALLENGE_PAGE")
            continue
        covered.append(field)

    missing = [name for name in run.required_fields if name not in fields]
    for name in missing:
        reasons.append(f"MISSING_REQUIRED_FIELD:{name}")

    if agreement == "OVERCLAIM":
        reasons.append("GRADE_OVERCLAIM")
    elif derived not in EVIDENCE_CLASSES:
        reasons.append(f"DOMAIN_CLASS_NOT_EVIDENCE:{derived}")

    uncovered = [f for f in QUOTED_FIELDS if f in fields and f not in covered]
    for name in uncovered:
        reasons.append(f"FIELD_NOT_QUOTE_SUPPORTED:{name}")

    accepted = (agreement != "OVERCLAIM" and derived in EVIDENCE_CLASSES
                and not missing and not uncovered and not reasons)
    # Traceability is about the *page and excerpt* being re-findable, not about the
    # field value matching them: the rubric measures "facts traceable to an accessible
    # source page and excerpt hash", which a row can fail at the value level and still
    # have. Keeping the two apart is what lets a reader tell the failures apart.
    cited_ok = any(run.hash_ok(a) for a in cited if a in run.lineage)
    excerpt_ok = any("EXCERPT_" not in r and "ARTIFACT_" not in r
                     for r in reasons) or bool(covered)
    traceable = cited_ok and excerpt_ok
    return {
        "row_ref": row.get("row_id"),
        "target_ref": row.get("target_id"),
        "declared_grade": declared,
        "domain_class": derived,
        "grade_agreement": agreement,
        "accepted": accepted,
        "traceable": traceable,
        "reason_codes": sorted(set(reasons)),
    }


# -------------------------------------------------------------- anti-fabrication
def _check(check_id: str, state: str, *, subject: str = "", reason_codes=(),
           excuse: str = "", instrument: str = "") -> dict:
    entry = {"id": check_id, "state": state, "subject_ref": subject,
             "reason_codes": sorted(set(reason_codes))}
    if excuse:
        entry["excuse"] = excuse
    if instrument:
        entry["instrument"] = instrument
    return entry


def _af1(row: dict, run: _Run) -> list[dict]:
    """A person or contact value the cited evidence does not contain."""
    contact = row.get("contact") or {}
    claims = row.get("claims") or []
    cited = [str(c.get("artifact_id", "")) for c in claims]
    haystack = []
    for artifact_id in cited:
        raw = run.raw(artifact_id)
        record = run.lineage.get(artifact_id)
        if (raw is not None and record is not None
                and _class_of(record, row, run.brief,
                              challenge=artifact_id in run.challenge_artifacts())
                in EVIDENCE_CLASSES):
            haystack.append(_searchable(record, raw))
    joined = " ".join(haystack)
    out = []
    for field in ("person_name", "email"):
        value = contact.get(field)
        if isinstance(value, str) and value and value not in joined:
            out.append(_check("AF-1", "FAIL", subject=str(row.get("row_id")),
                              reason_codes=[f"CONTACT_VALUE_NOT_IN_CITED_EVIDENCE:{field}"]))
    return out


def _af2(row: dict, derived: dict) -> list[dict]:
    codes = []
    if row.get("source_grade") == "E":
        codes.append("SNIPPET_GRADE_E")
    if derived["domain_class"] in {NO_RETRIEVED_PAGE, ABSENT}:
        codes.append("NO_RETRIEVED_PAGE")
    if not codes:
        return []
    return [_check("AF-2", "FAIL", subject=str(row.get("row_id")), reason_codes=codes)]


def _af3(run: _Run) -> list[dict]:
    cited = {str(c.get("artifact_id", ""))
             for row in run.rows for c in (row.get("claims") or [])}
    return [_check("AF-3", "FAIL", subject=artifact_id,
                   reason_codes=["CHALLENGE_MARKERS_IN_STORED_BYTES"])
            for artifact_id in run.challenge_artifacts() if artifact_id in cited]


def _af5(run: _Run) -> dict:
    if run.ledger is None or run.receipts is None:
        return _check("AF-5", "BLOCKED",
                      excuse="the cost ledger or the provider receipt stream was not supplied")
    dispatched = {r["operation_id"] for r in run.receipts if r.get("dispatched")}
    charged_states = {"SETTLED", "UNKNOWN_CHARGE_OUTSTANDING", "FREE_TIER"}
    rows_by_id = {op.get("operation_id"): op for op in run.ledger.get("operations", [])}
    codes = []
    for op_id in sorted(dispatched):
        if op_id not in rows_by_id:
            codes.append("RECEIPT_WITHOUT_LEDGER_ROW")
    for op_id, op in sorted(rows_by_id.items()):
        if op.get("state") in charged_states and op_id not in dispatched:
            codes.append("LEDGER_ROW_WITHOUT_RECEIPT")
    if codes:
        return _check("AF-5", "FAIL", subject="cost_ledger",
                      reason_codes=sorted(set(codes)))
    return _check("AF-5", "PASS", subject="cost_ledger")


def _af6(run: _Run) -> dict:
    if run.ledger is None:
        return _check("AF-6", "BLOCKED", excuse="the cost ledger was not supplied")
    settled = {"SETTLED", "UNKNOWN_CHARGE_OUTSTANDING", "NO_CHARGE_PROVEN"}
    dispatched = ({r["operation_id"] for r in run.receipts if r.get("dispatched")}
                  if run.receipts is not None else
                  {op.get("operation_id") for op in run.ledger.get("operations", [])
                   if op.get("state") != "BLOCKED"})
    # Only a dispatched operation can be an untracked charge; a row the gateway
    # refused to dispatch is not one, and counting it would inflate the finding.
    untracked = sorted(op.get("operation_id")
                       for op in run.ledger.get("operations", [])
                       if op.get("operation_id") in dispatched
                       and op.get("state") not in settled)
    if untracked:
        return _check("AF-6", "FAIL", subject="cost_ledger",
                      reason_codes=["DISPATCHED_OPERATION_NEITHER_SETTLED_NOR_HELD"])
    return _check("AF-6", "PASS", subject="cost_ledger")


def _af7(run: _Run) -> dict:
    """One operation signature, more than one charged dispatch.

    QA's ledger schema has no `op_signature` column, so with the schema as published
    this check cannot be evaluated and says BLOCKED rather than passing on a proxy.
    When the rows do carry signatures, they are what is compared.
    """
    if run.ledger is None:
        return _check("AF-7", "BLOCKED", excuse="the cost ledger was not supplied")
    return af7_from_ledger(run.ledger)


def _af9(run: _Run) -> list[dict]:
    out = []
    for artifact_id, record in sorted(run.lineage.items()):
        raw = run.raw(artifact_id)
        if raw is None:
            out.append(_check("AF-9", "BLOCKED", subject=artifact_id,
                              excuse="the stored bytes for this artefact were not supplied"))
            continue
        if hashlib.sha256(raw).hexdigest() != record.get("content_sha256"):
            out.append(_check("AF-9", "FAIL", subject=artifact_id,
                              reason_codes=["CONTENT_SHA256_MISMATCH"]))
        for parent in record.get("parent_ids") or []:
            if parent not in run.lineage:
                out.append(_check("AF-9", "FAIL", subject=artifact_id,
                                  reason_codes=["LINEAGE_PARENT_MISSING"]))
    return out


def _af10(run: _Run) -> tuple[dict, list[str]]:
    """Scan the supplied artefacts for credential-shaped values.

    Only the *shape* is reported: the value is never copied into the scorecard, because
    writing it down to prove it was found would be the disclosure AF-10 forbids.
    """
    hits: list[str] = []
    for label, payload in (("rows", run.rows), ("lineage", run.lineage),
                           ("brief", run.brief), ("ledger", run.ledger)):
        if payload is None:
            continue
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if any(p.search(blob) for p in CREDENTIAL_PATTERNS):
            hits.append(label)
    if hits:
        return (_check("AF-10", "FAIL", subject=";".join(sorted(hits)),
                       reason_codes=["CREDENTIAL_SHAPED_VALUE_IN_INPUT"]), hits)
    return _check("AF-10", "PASS", subject="supplied_artifacts"), []


def _af12(row: dict, run: _Run) -> list[dict]:
    """Personal data of a named individual beyond a role title on an evidence page."""
    person = (row.get("contact") or {}).get("person_name")
    if not isinstance(person, str) or not person:
        return []
    for claim in row.get("claims") or []:
        artifact_id = str(claim.get("artifact_id", ""))
        record = run.lineage.get(artifact_id)
        raw = run.raw(artifact_id)
        if record is None or raw is None:
            continue
        if _class_of(record, row, run.brief,
                     challenge=artifact_id in run.challenge_artifacts()) in EVIDENCE_CLASSES:
            if person in _searchable(record, raw):
                return []
    return [_check("AF-12", "FAIL", subject=str(row.get("row_id")),
                   reason_codes=["NAMED_INDIVIDUAL_NOT_ON_AN_EVIDENCE_PAGE"])]


def evaluate_checks(run: _Run, derived: list[dict]) -> dict:
    deferred = {"AF-4": "the egress decision log was not supplied",
                "AF-8": "the request log was not supplied",
                "AF-11": "the brief's starting commit hash was not supplied"}
    declared = [c["id"] for c in (run.checks.get("checks") or [])] or sorted(deferred)
    by_row = {d["row_ref"]: d for d in derived}
    entries: list[dict] = []
    for check_id in sorted(set(declared) | set(deferred)):
        if check_id in deferred:
            entries.append(_check(check_id, "BLOCKED", excuse=deferred[check_id]))
            continue
        if check_id in {"AF-1", "AF-12"}:
            produced = [e for row in run.rows for e in
                        (_af1(row, run) if check_id == "AF-1" else _af12(row, run))]
        elif check_id == "AF-2":
            produced = [e for row in run.rows
                        for e in _af2(row, by_row[row.get("row_id")])]
        elif check_id == "AF-3":
            produced = _af3(run)
        elif check_id == "AF-5":
            produced = [_af5(run)]
        elif check_id == "AF-6":
            produced = [_af6(run)]
        elif check_id == "AF-7":
            produced = [_af7(run)]
        elif check_id == "AF-9":
            produced = _af9(run)
        elif check_id == "AF-10":
            produced = [_af10(run)[0]]
        else:
            entries.append(_check(check_id, "BLOCKED",
                                  excuse="this offline scorer has no evaluator for "
                                         "this check and will not pass it by default"))
            continue
        if any(e["state"] == "FAIL" for e in produced):
            entries.extend(e for e in produced if e["state"] == "FAIL")
        else:
            entries.append(next((e for e in produced if e["state"] == "BLOCKED"),
                                _check(check_id, "PASS", subject="evaluated")))
    return {
        "checks": sorted(entries, key=lambda e: (e["id"], e["subject_ref"])),
        "failed": sorted({e["id"] for e in entries if e["state"] == "FAIL"}),
        "blocked": sorted({e["id"] for e in entries if e["state"] == "BLOCKED"}),
        "passed": sorted({e["id"] for e in entries if e["state"] == "PASS"}),
    }


# ------------------------------------------------------------------ measurements
def _ledger_view(run: _Run) -> dict:
    return _ledger_view_from_dict(run.ledger)


def _ledger_view_from_dict(ledger: dict | None) -> dict:
    """Confirmed, unreconciled and quota figures, kept in separate meters."""
    if ledger is None:
        return {"available": False}
    confirmed = Decimal("0")
    unknown = Decimal("0")
    credits = Decimal("0")
    credits_unknown = Decimal("0")
    # Units are never summed across meters. Only operations whose provider declares
    # the "credits" meter contribute to the credits figure at all.
    meters = {p.get("provider"): p.get("meter")
              for p in ledger.get("providers", [])}
    by_state: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for op in ledger.get("operations", []):
        state = str(op.get("state", "UNKNOWN"))
        by_state[state] = by_state.get(state, 0) + 1
        if op.get("reason_code"):
            by_reason[str(op["reason_code"])] = by_reason.get(str(op["reason_code"]), 0) + 1
        actual = op.get("actual")
        if actual is not None:
            confirmed += _decimal(actual)
        elif state in {"RESERVED", "UNKNOWN_CHARGE_OUTSTANDING"}:
            # Dispatched with nothing confirmed: exposure, never folded into confirmed.
            unknown += _decimal(op.get("estimated") or "0")
        if (state in {"SETTLED", "UNKNOWN_CHARGE_OUTSTANDING", "FREE_TIER"}
                and meters.get(op.get("provider")) == "credits"):
            credits += _decimal(op.get("reserved") or "0")
            if op.get("actual") is None and state != "NO_CHARGE_PROVEN":
                credits_unknown += _decimal(op.get("reserved") or "0")
    # A provider whose rate nobody verified cannot produce a confirmed dollar figure.
    # `meter_trust` is W-QA's own enum and the only place the schema can carry this,
    # and §1.6 of the owner's acceptance document makes it disqualifying: an
    # unverifiable meter forbids treating the money as settled.
    trust = {p.get("provider"): p.get("meter_trust", "verified")
             for p in ledger.get("providers", [])}
    return {"available": True, "confirmed_usd": confirmed, "unknown_usd": unknown,
            "credits": credits, "credits_unknown": credits_unknown,
            "meter_untrusted": any(value != "verified" for value in trust.values()),
            "meter_trust": trust,
            "by_state": by_state, "by_reason_code": by_reason}


def _review_view(run: _Run) -> dict:
    if run.reviews is None:
        return {"available": False}
    accepted = [r for r in run.reviews if r.get("decision") == "ACCEPT"]
    minutes = Decimal("0")
    for review in run.reviews:
        minutes += _decimal(review.get("reviewer_minutes") or "0")
    return {"available": True, "accepted": [r["row_id"] for r in accepted],
            "rejected": [r["row_id"] for r in run.reviews if r.get("decision") == "REJECT"],
            "minutes": minutes}


def _cost_measurements(ledger: dict, accepted_rows: int) -> tuple[dict, dict]:
    """Cost per accepted result, in USD and in quota units, kept apart.

    Three things can stop the dollar figure being an answer, and each gets its own
    state rather than a number with a footnote:

    * no ledger at all -> BLOCKED;
    * no accepted row -> a null with its reason (there is no denominator, so zero
      would be a fabricated figure rather than a missing one);
    * an unverified rate -> UNKNOWN, with the computed-but-untrusted figure kept in a
      separately named field so the information survives without being read as fact;
    * an unreconciled charge -> PARTIAL_UNKNOWN, with `all_in_usd` beside `value`.
    """
    if not ledger.get("available"):
        return ({"value": None, "state": BLOCKED, "meter": "usd",
                 "excuse": "no cost ledger was supplied, so nothing about spend is known"},
                {"value": None, "state": BLOCKED, "meter": "credits",
                 "excuse": "no cost ledger was supplied"})
    if not accepted_rows:
        return ({"value": None, "state": MEASURED, "meter": "usd",
                 "excuse": "no row was accepted by a reviewer, so a per-row cost has no "
                           "denominator; it is null, not zero"},
                {"value": None, "state": MEASURED, "meter": "credits",
                 "excuse": "no row was accepted by a reviewer"})
    denominator = Decimal(accepted_rows)
    all_in = ledger["confirmed_usd"] + ledger["unknown_usd"]
    confirmed = _money(ledger["confirmed_usd"] / denominator)
    if ledger.get("meter_untrusted"):
        cost = {"value": None, "state": UNKNOWN, "meter": "usd",
                "unverified_usd_per_accepted_row": confirmed,
                "unknown_exposure_usd": _money(ledger["unknown_usd"]),
                "all_in_usd": _money(all_in / denominator),
                "excuse": "the rate for at least one provider was never verified, so a "
                          "confirmed dollar figure cannot be stated; the unverified "
                          "computation is reported separately and is not an answer"}
    else:
        cost = {"value": confirmed,
                "state": PARTIAL_UNKNOWN if ledger["unknown_usd"] else MEASURED,
                "meter": "usd",
                "unknown_exposure_usd": _money(ledger["unknown_usd"]),
                "all_in_usd": _money(all_in / denominator)}
    # Each meter carries its own unknown exposure: an unreconciled USD charge says
    # nothing about whether the credit count is settled, and vice versa. And an
    # unverified *rate* says nothing about a unit count, which is a count of calls.
    credits = {"value": _money(ledger["credits"] / denominator),
               "state": (PARTIAL_UNKNOWN if ledger["credits_unknown"] else MEASURED),
               "meter": "credits"}
    return cost, credits


def measurement_from_ledger(ledger: dict, *, accepted_rows: int) -> dict:
    """The two cost measurements for one schema-shaped ledger and a row count.

    Public because the integration path reaches them without a whole scored run: the
    ledger lives on one side of the seam and the review count on the other.
    """
    view = _ledger_view_from_dict(ledger)
    cost, credits = _cost_measurements(view, accepted_rows)
    return {"usd_per_accepted_row": cost, "credits_per_accepted_row": credits}


def af7_from_ledger(ledger: dict) -> dict:
    """The duplicate-charge check over a schema-shaped ledger.

    Requires `op_signature`. Without it — which QA's own schema does not carry — the
    check is BLOCKED rather than passed on a proxy that could never fail.
    """
    operations = ledger.get("operations") or []
    signatures = [op.get("op_signature") for op in operations]
    if not operations or not any(signatures):
        return _check("AF-7", "BLOCKED",
                      excuse="no operation signature on any ledger row; the "
                             "duplicate-charge check needs the transport-agnostic "
                             "signature, which QA's cost-ledger schema does not carry")
    seen: dict[str, int] = {}
    for op in operations:
        if op.get("state") in CHARGED_STATES:
            seen[str(op.get("op_signature"))] = seen.get(str(op.get("op_signature")), 0) + 1
    repeated = sorted(sig for sig, n in seen.items() if n > 1)
    if repeated:
        return _check("AF-7", "FAIL", subject="cost_ledger",
                      reason_codes=["ONE_SIGNATURE_CHARGED_TWICE"])
    return _check("AF-7", "PASS", subject="cost_ledger")


def measure(run: _Run, derived: list[dict]) -> dict:
    rows_submitted = len(derived)
    mech_accepted = [d for d in derived if d["accepted"]]
    targets_accepted = {d["target_ref"] for d in mech_accepted}
    review = _review_view(run)
    ledger = _ledger_view(run)
    missing_by_field: dict[str, int] = {}
    for entry in derived:
        for code in entry["reason_codes"]:
            if code.startswith("MISSING_REQUIRED_FIELD:"):
                name = code.split(":", 1)[1]
                missing_by_field[name] = missing_by_field.get(name, 0) + 1

    def plain(value, state=MEASURED, **extra):
        return {"value": value, "state": state, **extra}

    reviews_accepted = review["accepted"] if review["available"] else []
    n_review_accepted = len(reviews_accepted)
    duplicates_extra = len(mech_accepted) - len(targets_accepted)

    false_positive_rows = sorted(set(review.get("rejected", []))
                                 & {d["row_ref"] for d in mech_accepted}) \
        if review["available"] else []

    cost, credits = _cost_measurements(ledger, n_review_accepted)

    return {
        "official_domain_accuracy": plain(
            _ratio(sum(1 for d in mech_accepted if d["domain_class"] == SELF),
                   len(mech_accepted)),
            numerator=sum(1 for d in mech_accepted if d["domain_class"] == SELF),
            denominator=len(mech_accepted)),
        "traceable_fact_ratio": plain(
            _ratio(sum(1 for d in derived if d["traceable"]), rows_submitted),
            numerator=sum(1 for d in derived if d["traceable"]),
            denominator=rows_submitted),
        "accepted_valid_rows": (plain(_ratio(n_review_accepted, rows_submitted),
                                      numerator=n_review_accepted,
                                      denominator=rows_submitted)
                                if review["available"] else
                                {"value": None, "state": BLOCKED,
                                 "excuse": "no human review decisions were supplied; "
                                           "acceptance is a reviewer's call, not the "
                                           "scorer's"}),
        "duplicates_per_accepted_entity": plain(
            _ratio(duplicates_extra, len(targets_accepted)) if targets_accepted else "0.000000",
            numerator=duplicates_extra, denominator=len(targets_accepted)),
        "missed_required_fields": plain(str(sum(missing_by_field.values())),
                                        total=sum(missing_by_field.values()),
                                        by_field=missing_by_field),
        "false_positives": (plain(str(len(false_positive_rows)),
                                  count=len(false_positive_rows), rows=false_positive_rows)
                            if review["available"] else
                            {"value": None, "state": BLOCKED,
                             "excuse": "no human review decisions were supplied"}),
        "manual_minutes_per_accepted_row": (plain(
            str((review["minutes"] / Decimal(n_review_accepted)).quantize(PLACES, ROUND_HALF_UP))
            if n_review_accepted else None,
            total_minutes=str(review["minutes"]),
            excuse="" if n_review_accepted else "no row was accepted by a reviewer")
            if review["available"] else
            {"value": None, "state": BLOCKED,
             "excuse": "no human review decisions were supplied"}),
        "usd_per_accepted_row": cost,
        "credits_per_accepted_row": credits,
        "failure_causes": plain("", by_state=ledger.get("by_state", {}),
                                by_reason_code=ledger.get("by_reason_code", {})),
    }


# ------------------------------------------------------------------- the document
def _issue_ids(checks: list[dict]) -> list[dict]:
    """One stable, unique id per finding.

    Keyed on check + subject + occurrence, not on the reason codes: two findings can
    share a subject and a check (an invented name and an invented address are both
    AF-1 on one row), and an id that collides makes a defect list unquotable.
    """
    seen: dict[str, int] = {}
    issues = []
    for entry in checks:
        if entry["state"] != "FAIL":
            continue
        stem = f"{entry['id']}-{entry['subject_ref'] or 'run'}"
        seen[stem] = seen.get(stem, 0) + 1
        issues.append({"issue_id": stem if seen[stem] == 1 else f"{stem}#{seen[stem]}",
                       "check": entry["id"], "severity": "P0",
                       "subject_ref": entry["subject_ref"],
                       "reason_codes": entry["reason_codes"]})
    return sorted(issues, key=lambda i: i["issue_id"])


def sanitize(document: dict) -> dict:
    """The last gate before publication: nothing contact-shaped may leave here.

    The scorer only ever emits identifiers, closed-set codes and numbers, so this pass
    should find nothing. It exists because "should" is not a control, and a future field
    added carelessly is exactly how a contact value reaches a published scorecard.
    """
    blob = json.dumps(document, ensure_ascii=False, sort_keys=True)
    problems = []
    # Each family is tested with its own compiled patterns: joining them with `|`
    # would put an inline `(?i)` in the middle of the alternation and fail to compile.
    for name, patterns in (
            ("email", (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),)),
            ("url", (re.compile(r"(?i)\bhttps?://"),)),
            ("query", (re.compile(r"\?[A-Za-z0-9_]+="),)),
            ("credential", CREDENTIAL_PATTERNS)):
        if any(pattern.search(blob) for pattern in patterns):
            problems.append(name)
    return {"scan": "PASS" if not problems else "FAIL", "findings": sorted(problems),
            "policy": "identifiers, closed-set codes and numbers only; no page text, "
                      "no contact value, no person name"}


def score(*, brief, rubric, checks, rows, lineage, targets, reviews, ledger, receipts,
          paths) -> dict:
    run = _Run(brief=brief, rubric=rubric, checks=checks, rows=rows, lineage=lineage,
               targets=targets, reviews=reviews, ledger=ledger, receipts=receipts,
               paths=paths or {})
    derived = [_derive_row(row, run) for row in run.rows]
    tolerance = evaluate_checks(run, derived)
    measurements = measure(run, derived)
    if tolerance["failed"]:
        verdict, reason = FAIL, "P0_CHECK_FAILED:" + ",".join(tolerance["failed"])
    elif tolerance["blocked"]:
        verdict, reason = BLOCKED_VERDICT, "CHECK_NOT_EVALUABLE:" + ",".join(tolerance["blocked"])
    else:
        verdict, reason = PASS, "ALL_CHECKS_EVALUATED_AND_HELD"

    acquired = sorted(r["acquired_at"] for r in run.lineage.values() if r.get("acquired_at"))
    document = {
        "schema": SCHEMA,
        "run_ref": {
            "brief_id": (run.brief.get("brief_id") if run.brief else None),
            "generated_at": acquired[-1] if acquired else None,
            "targets_declared": len(run.targets),
            "rows_submitted": len(derived),
        },
        "verdict": verdict,
        "verdict_reason": reason,
        "zero_tolerance": tolerance,
        "measurements": measurements,
        "rows": sorted(derived, key=lambda d: str(d["row_ref"])),
        "issues": _issue_ids(tolerance["checks"]),
        "assumptions": {"domain_suffix": DOMAIN_SUFFIX_ASSUMPTION},
        "inputs": {"sha256": {
            name: _sha256_file(path) for name, path in sorted((paths or {}).items())
            if name in {"brief", "rubric", "checks", "rows", "lineage", "targets",
                        "reviews", "ledger", "receipts"}
            and path is not None and Path(path).is_file()}},
    }
    document["sanitization"] = sanitize(document)
    if document["sanitization"]["scan"] != "PASS":
        # A scorecard that leaks is not publishable, and degrade-to-publish is worse
        # than stopping: the leak would ship attached to a passing verdict.
        raise ScorecardError("SCORECARD_SANITISATION_FAILED:"
                             + ",".join(document["sanitization"]["findings"]))
    return document


# ------------------------------------------------------------------------ output
CSV_COLUMNS = ("measurement", "state", "value", "meter", "excuse")


def write_scorecard(document: dict, base_path: Path | str) -> dict:
    """Write the JSON and a flat CSV of the measurements, both deterministic."""
    base = Path(base_path)
    base.parent.mkdir(parents=True, exist_ok=True)
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")
    json_path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)
                         + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for name in sorted(document["measurements"]):
            measured = document["measurements"][name]
            writer.writerow({
                "measurement": name,
                "state": measured.get("state", ""),
                "value": "" if measured.get("value") is None else measured["value"],
                "meter": measured.get("meter", ""),
                "excuse": measured.get("excuse", ""),
            })
    return {"json": str(json_path), "csv": str(csv_path),
            "json_sha256": _sha256_file(json_path), "csv_sha256": _sha256_file(csv_path),
            "verdict": document["verdict"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--brief", required=True)
    parser.add_argument("--rubric", required=True)
    parser.add_argument("--checks", required=True)
    parser.add_argument("--rows", required=True)
    parser.add_argument("--lineage", required=True)
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--targets")
    parser.add_argument("--reviews")
    parser.add_argument("--ledger")
    parser.add_argument("--receipts")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    paths = {"brief": args.brief, "rubric": args.rubric, "checks": args.checks,
             "rows": args.rows, "lineage": args.lineage,
             "artifacts_dir": args.artifacts_dir, "targets": args.targets,
             "reviews": args.reviews, "ledger": args.ledger,
             "receipts": args.receipts}
    try:
        document = score(**inputs_from_paths(paths))
        written = write_scorecard(document, args.out)
    except ScorecardError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    print(json.dumps(written, ensure_ascii=False, indent=2))
    # A FAIL is a result, not a tool error: the exit code tells a pipeline which it got.
    return 0 if document["verdict"] == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())

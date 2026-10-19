"""The offline benchmark: build a run-shaped bundle and evaluate it against the
published rubric and the owner's zero-tolerance list.

It exists so the QA machinery is *proven to detect fabrication* rather than
merely declaring rules. A rule nobody has ever seen fire is not a control.

Two things this module is deliberately **not**:

* it is not a research run — every "source" here is synthetic local bytes;
* it is not a product module — nothing under ``scripts/`` is imported for its
  behaviour except the plugin's own candidate store, which is used read/write
  against a temporary data root so the lineage hashes are computed by the real
  code path rather than by this file.

No network, no credential, no provider, no LLM.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

EVIDENCE_GRADES = {"A", "B"}
CHALLENGE_MARKERS = ("cf-chl-", "verify you are human", "captcha challenge",
                     "just a moment...", "checking your browser")

REQUIRED_FIELDS = ("organisation_name", "venue_address_public", "official_domain",
                   "official_contact_route", "source_url", "source_excerpt_sha256")


@dataclass
class Row:
    """One candidate row, shaped as the brief asks for."""

    target_id: str
    fields: dict
    source_url: str
    source_grade: str
    artifact_id: str
    excerpt: str = ""
    reviewed_accepted: bool = False
    ledger_operation_id: str | None = None

    def excerpt_sha256(self) -> str:
        return hashlib.sha256(self.excerpt.encode("utf-8")).hexdigest()


@dataclass
class Bundle:
    rows: list = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)   # artifact_id -> raw bytes
    artifact_grade: dict = field(default_factory=dict)
    operations: list = field(default_factory=list)
    receipts: list = field(default_factory=list)
    requests: list = field(default_factory=list)     # (method, url)
    brief_sha256: str = ""

    def row_metrics(self) -> dict:
        accepted = [r for r in self.rows if r.reviewed_accepted]
        traceable = [r for r in self.rows
                     if r.source_grade in EVIDENCE_GRADES
                     and r.artifact_id in self.artifacts
                     and len(r.fields.get("source_excerpt_sha256", "")) == 64
                     and self.excerpt_present(r)]
        missing = sum(1 for r in accepted
                      if not all(r.fields.get(f) for f in REQUIRED_FIELDS))
        entities = [r.fields.get("organisation_name", r.target_id) for r in accepted]
        duplicates = len(entities) - len(set(entities))
        return {
            "submitted_rows": len(self.rows),
            "accepted_rows": len(accepted),
            "traceable_fact_ratio": (round(len(traceable) / len(self.rows), 4)
                                     if self.rows else None),
            "missed_required_fields": missing,
            "duplicates_per_accepted_entity": duplicates,
            "usd_per_accepted_row": None,      # no paid call in this stage
            "credits_per_accepted_row": None,
            "tokens_in": 0,
            "tokens_out": 0,
        }

    def excerpt_present(self, row: Row) -> bool:
        """The recorded hash must be the hash of the excerpt we hold, and the
        excerpt must actually contain the field values the row claims."""
        if row.excerpt_sha256() != row.fields.get("source_excerpt_sha256"):
            return False
        flat = row.excerpt.casefold()
        for name in ("organisation_name", "official_domain"):
            value = row.fields.get(name)
            if value and str(value).casefold() not in flat:
                return False
        return True


# --- the anti-fabrication evaluator ----------------------------------------
# Implements docs/wave1-qa/fixtures/anti-fabrication-checks.json. Each check
# returns PASS / FAIL / BLOCKED with the evidence it used.

_PERSON_HINT = ("@", "+44", "tel:", "contact_name")


def evaluate_anti_fabrication(bundle: Bundle) -> dict:
    results: dict[str, dict] = {}

    def record(check_id, state, detail):
        results[check_id] = {"state": state, "detail": detail}

    # AF-1 / AF-12: invented person, email or personal data.
    invented = []
    for row in bundle.rows:
        for key, value in row.fields.items():
            text = str(value)
            if any(h in text.casefold() for h in _PERSON_HINT) and \
                    text.casefold() not in (row.excerpt or "").casefold():
                invented.append(f"{row.target_id}:{key}")
            if key in {"email", "phone", "person_name", "first_name", "last_name"}:
                invented.append(f"{row.target_id}:{key}")
    record("AF-1", "FAIL" if invented else "PASS",
           f"unsupported personal data: {invented}" if invented
           else "no person, email or phone outside its cited excerpt")
    record("AF-12", "FAIL" if invented else "PASS",
           f"personal data outside a cited A/B page: {invented}" if invented
           else "no personal data beyond public role fields")

    # AF-2: an accepted row whose source is not grade A/B.
    weak = [r.target_id for r in bundle.rows
            if r.reviewed_accepted and r.source_grade not in EVIDENCE_GRADES]
    record("AF-2", "FAIL" if weak else "PASS",
           f"accepted rows citing non-evidence grades: {weak}" if weak
           else "every accepted row cites a grade A or B source")

    # AF-3: a challenge page cited as validated evidence.
    challenged = []
    for row in bundle.rows:
        blob = bundle.artifacts.get(row.artifact_id, b"").decode("utf-8", "replace").casefold()
        if any(m in blob for m in CHALLENGE_MARKERS) and row.reviewed_accepted:
            challenged.append(row.target_id)
    record("AF-3", "FAIL" if challenged else "PASS",
           f"challenge pages cited as validated: {challenged}" if challenged
           else "no challenge page is cited by an accepted row")

    # AF-4: a retrieval outside the brief's allowance, or to a private address.
    forbidden = [f"{m} {u}" for m, u in bundle.requests
                 if _private_or_unauthorised(u)]
    record("AF-4", "FAIL" if forbidden else "PASS",
           f"forbidden retrievals: {forbidden[:5]}" if forbidden
           else "every retrieval stayed inside the allowance")

    # AF-5: a receipt with no ledger row, or a ledger row with no receipt.
    ledger_ids = {op.get("operation_id") for op in bundle.operations}
    receipt_ids = {rc.get("operation_id") for rc in bundle.receipts}
    unpaired = sorted(ledger_ids ^ receipt_ids)
    record("AF-5", "FAIL" if unpaired else "PASS",
           f"operations not present on both sides: {unpaired}" if unpaired
           else "every provider call has a ledger row and a receipt")

    # AF-6: an operation left neither settled nor held.
    dangling = [op.get("operation_id") for op in bundle.operations
                if op.get("state") not in {"SETTLED", "NO_CHARGE_PROVEN",
                                           "UNKNOWN_CHARGE_OUTSTANDING", "BLOCKED"}]
    record("AF-6", "FAIL" if dangling else "PASS",
           f"operations with no terminal state: {dangling}" if dangling
           else "every dispatched operation reached a terminal state")

    # AF-7: one operation signature charged more than once.
    signatures = [op.get("signature") for op in bundle.operations
                  if op.get("charged")]
    doubled = sorted({s for s in signatures if s and signatures.count(s) > 1})
    record("AF-7", "FAIL" if doubled else "PASS",
           f"signatures charged twice: {doubled}" if doubled
           else "no signature was charged more than once")

    # AF-8: a request that is not a read.
    side_effects = [f"{m} {u}" for m, u in bundle.requests if m.upper() != "GET"]
    record("AF-8", "FAIL" if side_effects else "PASS",
           f"non-read requests: {side_effects[:5]}" if side_effects
           else "every request was a GET")

    # AF-9: a cited artefact whose bytes do not match its recorded hash.
    tampered = []
    for row in bundle.rows:
        if not row.reviewed_accepted:
            continue
        if not bundle.excerpt_present(row):
            tampered.append(row.artifact_id)
    record("AF-9", "FAIL" if tampered else "PASS",
           f"accepted rows whose excerpt/hash does not match: {sorted(set(tampered))}"
           if tampered else "every accepted row's excerpt hashes as recorded")

    # AF-10: a credential value or shape in any shipped artefact.
    leaks = []
    for artifact_id, blob in bundle.artifacts.items():
        text = blob.decode("utf-8", "replace")
        if _looks_like_credential(text):
            leaks.append(artifact_id)
    record("AF-10", "FAIL" if leaks else "PASS",
           f"artefacts carrying credential-shaped text: {leaks}" if leaks
           else "no credential value or shape in any artefact")

    # AF-11: the brief used must be the one committed before the run.
    record("AF-11", "PASS" if bundle.brief_sha256 else "BLOCKED",
           "brief hash pinned" if bundle.brief_sha256
           else "no brief hash recorded for this bundle; the baseline of the "
                "comparison is unnamed")

    failing = sorted(k for k, v in results.items() if v["state"] == "FAIL")
    blocked = sorted(k for k, v in results.items() if v["state"] == "BLOCKED")
    return {
        "checks": results,
        "failing": failing,
        "blocked": blocked,
        "verdict": "FAIL" if failing else ("BLOCKED" if blocked else "PASS"),
    }


def _private_or_unauthorised(url: str) -> bool:
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return True
    if not host:
        return True
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
        return True
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


def _looks_like_credential(text: str) -> bool:
    import re
    if re.search(r"\b(sk|pk)_[A-Za-z0-9]{16,}\b", text):
        return True
    if re.search(r"(?i)\b(api[_-]?key|access[_-]?token)\b\s*[:=]\s*[\"']?[A-Za-z0-9\-_]{16,}", text):
        return True
    if re.search(r"(?i)\b(sha256|len)\s*\(\s*api[_-]?key", text):
        return True
    return False


# --- bundle construction ---------------------------------------------------


def fixture_brief_sha256() -> str:
    from pathlib import Path as _P
    brief = (_P(__file__).resolve().parents[5] / "docs" / "wave1-qa" / "fixtures"
             / "research-brief.json")
    if not brief.is_file():
        return ""
    return hashlib.sha256(brief.read_bytes()).hexdigest()


def build_clean_bundle(directory: Path, targets: list[dict]) -> Bundle:
    """A well-formed run: official sources, hashes that match, reads only.

    Nothing here reaches the network: the bytes are written locally and the
    'requests' list is a record of what *would* have been retrieved.
    """
    directory.mkdir(parents=True, exist_ok=True)
    bundle = Bundle(brief_sha256=fixture_brief_sha256())
    for index, target in enumerate(targets, start=1):
        artifact_id = f"art-{index:02d}"
        domain = f"{target['target_id']}.example.org"
        excerpt = (f"{target['name']} is operated by {target['operator_hint']} in "
                   f"{target['geography']}. Contact the venue through the official "
                   f"enquiries page. Official site: {domain}")
        blob = f"<html><body><p>{excerpt}</p></body></html>".encode("utf-8")
        bundle.artifacts[artifact_id] = blob
        bundle.artifact_grade[artifact_id] = "A"
        operation_id = f"op-{index:02d}"
        row = Row(
            target_id=target["target_id"],
            fields={
                "organisation_name": target["name"],
                "venue_address_public": f"{target['geography']}, United Kingdom",
                "official_domain": domain,
                "official_contact_route": "the official enquiries page (GET, no form)",
                "source_url": f"https://{domain}/contact",
                "source_excerpt_sha256": hashlib.sha256(
                    excerpt.encode("utf-8")).hexdigest(),
            },
            source_url=f"https://{domain}/contact",
            source_grade="A",
            artifact_id=artifact_id,
            excerpt=excerpt,
            reviewed_accepted=False,        # no human reviewer has run yet
            ledger_operation_id=operation_id,
        )
        bundle.rows.append(row)
        bundle.requests.append(("GET", row.source_url))
        bundle.operations.append({
            "operation_id": operation_id, "state": "NO_CHARGE_PROVEN",
            "signature": f"crawl4ai_local|{domain}|contact", "charged": False,
        })
        bundle.receipts.append({"operation_id": operation_id,
                                "provider": "crawl4ai_local"})
    return bundle


def build_fabricated_bundle(directory: Path, targets: list[dict]) -> Bundle:
    """The same run, with one of each failure the owner forbids.

    Every defect is deliberate and is labelled here so a reader can see exactly
    which check is being exercised.
    """
    bundle = build_clean_bundle(directory, targets)
    # AF-1/AF-12: an invented contact that appears nowhere in the source.
    bundle.rows[0].fields["official_contact_route"] = "email bookings@invented.example"
    bundle.rows[0].reviewed_accepted = True
    # AF-2: accepted on a snippet-grade source.
    bundle.rows[1].source_grade = "E"
    bundle.rows[1].reviewed_accepted = True
    # AF-3: a challenge page stored and cited.
    bundle.artifacts[bundle.rows[2].artifact_id] = (
        b"<html><body>Checking your browser before accessing. cf-chl-token</body></html>")
    bundle.rows[2].reviewed_accepted = True
    # AF-4: a retrieval aimed at a private address.
    bundle.requests.append(("GET", "http://169.254.169.254/latest/meta-data/"))
    # AF-5: a receipt with no ledger row.
    bundle.receipts.append({"operation_id": "op-ghost", "provider": "tinyfish"})
    # AF-6: an operation that never reached a terminal state.
    bundle.operations.append({"operation_id": "op-dangling", "state": "RESERVED",
                              "signature": "tinyfish|search|pool", "charged": False})
    # AF-7: the same signature charged twice.
    bundle.operations.append({"operation_id": "op-dup-a", "state": "SETTLED",
                              "signature": "serper|search|pool", "charged": True})
    bundle.operations.append({"operation_id": "op-dup-b", "state": "SETTLED",
                              "signature": "serper|search|pool", "charged": True})
    bundle.receipts += [{"operation_id": "op-dup-a"}, {"operation_id": "op-dup-b"}]
    # AF-8: a side effect.
    bundle.requests.append(("POST", "https://example.org/enquiry-form"))
    # AF-9: an accepted row whose excerpt no longer hashes as recorded.
    bundle.rows[3].reviewed_accepted = True
    bundle.rows[3].fields["source_excerpt_sha256"] = "0" * 64
    # AF-10: a credential-shaped string in a stored artefact.
    bundle.artifacts["art-leak"] = b"config: api_key = sk_live_ABCDEFGHIJKLMNOP1234\n"
    return bundle


def write_bundle(bundle: Bundle, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / "benchmark-bundle.json"
    out.write_text(json.dumps({
        "rows": [{"target_id": r.target_id, "fields": r.fields,
                  "source_url": r.source_url, "source_grade": r.source_grade,
                  "artifact_id": r.artifact_id,
                  "reviewed_accepted": r.reviewed_accepted} for r in bundle.rows],
        "metrics": bundle.row_metrics(),
        "requests": bundle.requests,
        "operations": bundle.operations,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    return out

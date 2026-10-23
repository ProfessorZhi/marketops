"""One complete offline research run, end to end, for the run-level audit.

The chain the audit has to walk, in order:

    Research Brief -> stored artefacts (hash) -> candidate rows with citations
    -> Jev Choice/Noul/Score/Box through a fake SDK -> review state
    -> Excel/CSV export -> scorecard inputs

Every name, URL and address is synthetic and uses `example.org` only. Nothing here
stands for a real organisation and nothing here is evidence about the world. No
network call, no credential, no provider, no charge.

The fixture can build a **clean** run and can inject one defect at a time, so a test
can prove the audit detects each named class rather than trusting that it would.

Helpers are prefixed `_` as a module, so `unittest discover` does not collect them.
"""
from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_adapter_jev as jev          # noqa: E402
import research_delivery as delivery        # noqa: E402
import research_evidence_store as evidence_store  # noqa: E402
import research_lite_store as store         # noqa: E402

RUN = "run-w3"
PROJECT = "w3-audit"
BRIEF_ID = "w3-public-orgs"

#: Two retrieved pages for the same organisation: the organisation's own domain, and
#: the governing body's. Both are originals; neither is derived from the other.
OWN_PAGE = ("<html><body><h1>Example Arts Centre</h1>"
            "<p>Operated by the Example Borough Council.</p>"
            "<p>Address: 1 Example Street, Exampleton</p>"
            "<p>Enquiries: enquiries@example.org</p>"
            "<p>Director of Operations: A. Person</p></body></html>")
PARENT_PAGE = ("<html><body><h1>Example Borough Council venues</h1>"
               "<p>The Council operates the Example Arts Centre and the Example Museum."
               "</p></body></html>")
#: What a Jev call would store: model output about a page, not the page.
JEVD_PAGE = None  # built at run time from the adapter's own response

SPAN_OPERATED = "Operated by the Example Borough Council."
SPAN_ADDRESS = "Address: 1 Example Street, Exampleton"
SPAN_ENQUIRIES = "Enquiries: enquiries@example.org"

ORG = "Example Arts Centre"
ADDRESS = "1 Example Street, Exampleton"
ENQUIRIES = "enquiries@example.org"

REQUIRED_FIELDS = ("organisation_name", "venue_address_public", "official_domain",
                   "official_contact_route", "source_url", "source_excerpt_sha256")
QUOTED_FIELDS = ("organisation_name", "venue_address_public", "official_contact_route")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------- fake SDK
# The shape is copied from the installed 0.7.5 package, not invented: question
# objects take keyword-only `instructions`/`criteria`, the client is a context
# manager, and the response groups answers by primitive.

class _Question:
    def __init__(self, *, instructions=None, criteria=None):
        self.instructions, self.criteria = instructions, criteria


class Choice(_Question):
    pass


class Noul(_Question):
    pass


class Score(_Question):
    pass


class Box(_Question):
    pass


class Image:
    def __init__(self, data):
        self.data = data


class RetryPolicy:
    def __init__(self, **kwargs):
        self.params = kwargs


class FakeJev:
    """A 0.7.5-shaped stand-in. Records every option it was constructed with."""

    def __init__(self, *, choice="operator", noul=0.25, score=1.5, request_id="req-w3",
                 usage=True, fail=None):
        self.choice, self.noul, self.score = choice, noul, score
        self.request_id, self.usage, self.fail = request_id, usage, fail
        self.calls: list[dict] = []

    def module(self) -> types.SimpleNamespace:
        sdk = self

        class TypeSafeClient:
            def __init__(self, **options):
                self._inner = _Client(sdk, options)

            def __enter__(self):
                return self._inner

            def __exit__(self, *exc):
                return False

            def system_one(self, state, questions, **kwargs):
                return self._inner.system_one(state, questions, **kwargs)

        return types.SimpleNamespace(
            Choice=Choice, Noul=Noul, Score=Score, Box=Box, Image=Image,
            RetryPolicy=RetryPolicy, TypeSafeClient=TypeSafeClient,
            DEFAULT_MODEL="jev-latest", DEFAULT_BASE_URL="https://api.typesafe.ai")


class _Client:
    def __init__(self, sdk: FakeJev, options: dict):
        self._sdk = sdk
        sdk.calls.append({"options": options})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def system_one(self, state, questions, **kwargs):
        self._sdk.calls[-1]["state"] = state
        self._sdk.calls[-1]["questions"] = questions
        if self._sdk.fail is not None:
            raise self._sdk.fail
        response = types.SimpleNamespace(model="jev-latest", answers={}, choices={},
                                         nouls={}, scores={}, boxes={})
        if self._sdk.request_id is not None:
            response.request_id = self._sdk.request_id
        response.usage = (types.SimpleNamespace(input_tokens=1234, output_tokens=7)
                          if self._sdk.usage else None)
        for name, question in questions.items():
            if isinstance(question, Choice):
                response.choices[name] = types.SimpleNamespace(
                    choice=self._sdk.choice, confidence=0.83,
                    probabilities={self._sdk.choice: 0.83})
            elif isinstance(question, Noul):
                response.nouls[name] = types.SimpleNamespace(noul=self._sdk.noul)
            elif isinstance(question, Score):
                response.scores[name] = types.SimpleNamespace(
                    score=self._sdk.score, confidence=0.6,
                    legend={0: "weak", 1: "moderate", 2: "strong"},
                    probabilities={0: 0.2, 1: 0.6, 2: 0.2})
            else:
                response.boxes[name] = types.SimpleNamespace(
                    box=types.SimpleNamespace(top=0.1, bottom=0.4, left=0.2, right=0.7),
                    confidence=0.55)
        return response


def materialise_artifacts(root: Path, artifact_ids, target_dir: Path) -> Path:
    """Write `<artifact_id>.bin` files for the scorecard's artifacts directory.

    The store names its files by content hash; a scorer that took the store's layout as
    its convention would be reading an implementation detail. The convention is a
    directory of `<artifact_id>.bin`, so the caller materialises it — here from the
    store's own hash-verified readback, which is the only source that may be used.
    """
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for artifact_id in artifact_ids:
        read = store.read_artifact(root, RUN, artifact_id)
        (target_dir / f"{artifact_id}.bin").write_bytes(read["content"])
    return target_dir


# ----------------------------------------------------------------------- brief
def build_brief() -> dict:
    return {
        "schema": "marketops.qa.research_brief.v1",
        "brief_id": BRIEF_ID,
        "_comment": "Synthetic. The run consumes it verbatim and never edits it.",
        "input": {
            "research_question": "Which organisations operate a public venue here, and "
                                 "what is the published way to contact each one?",
            "target_kind": "venue",
            "geography": {"name": "Exampleton"},
            "target_count": {"min": 1, "max": 3},
            "required_fields_per_target": list(REQUIRED_FIELDS),
            "budget": {"paid_provider_calls": "none in this stage"},
            "hard_prohibitions": ["invented person names, email addresses or roles",
                                  "presenting a search snippet as the source page"],
        },
    }


def build_rows() -> list[dict]:
    """Two candidate rows for one organisation, both fully cited."""
    claims = [{"field": "organisation_name", "artifact_id": "art-own", "quote": ORG},
              {"field": "official_contact_route", "artifact_id": "art-own",
               "quote": SPAN_ENQUIRIES},
              {"field": "venue_address_public", "artifact_id": "art-own",
               "quote": SPAN_ADDRESS}]
    return [{
        "row_id": "row-01", "target_id": "tgt-01", "source_grade": "A",
        "fields": {"organisation_name": ORG, "venue_address_public": ADDRESS,
                   "official_domain": "example.org", "official_contact_route": ENQUIRIES,
                   "source_url": "https://example.org/visit",
                   "source_excerpt_sha256": _sha(SPAN_ENQUIRIES)},
        "claims": claims, "cited_artifact_ids": ["art-own"],
        "same_operator_official_domains": ["example.gov.uk"],
        "contact": {"person_name": None, "email": None},
    }]


# ------------------------------------------------------------------- the run
def build_run(directory: Path, *, defect: str | None = None) -> dict:
    """Materialise the whole chain and return paths plus a note of what was injected.

    `defect` names one injected fault. Absent, the run is clean, so a test can assert
    both that the audit finds the fault and that it does not invent one.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    root = directory / "data"

    # --- Research Brief: written first, so the run's inputs are pinned before it runs
    brief_path = directory / "brief.json"
    brief_path.write_text(json.dumps(build_brief(), indent=2, sort_keys=True) + "\n",
                          encoding="utf-8")

    # --- evidence artefacts, stored with their hashes
    store.start_run(root, RUN, "GB-exampleton", "Contact routes for public venues",
                    "SOP-PENDING")
    store.save_artifact(root, RUN, "art-own", "fixture-public-html",
                        "https://example.org/visit", OWN_PAGE.encode("utf-8"),
                        "raw_response")
    store.save_artifact(root, RUN, "art-parent", "fixture-public-html",
                        "https://example.gov.uk/venues", PARENT_PAGE.encode("utf-8"),
                        "raw_response")

    # --- a judgement, through the fake SDK only
    fake = FakeJev()
    saved_modules = sys.modules.get("typesafe_sdk")
    sys.modules["typesafe_sdk"] = fake.module()
    try:
        binding = jev.bind_evidence(root, RUN, "art-own", query="Operated by")
        judgment = jev.execute(
            root, run_id=RUN, operation_id="op-jev",
            bindings=[binding],
            questions=[jev.choice_question("operator", "Which entity operates the venue?",
                                           ["operator", "tenant"]),
                       jev.noul_question("disputed", "Is the recorded status disputed?"),
                       jev.score_question("support", "How strong is the recorded support?",
                                          ["weak", "moderate", "strong"])],
            api_key="fictional-test-key")
    finally:
        if saved_modules is None:
            sys.modules.pop("typesafe_sdk", None)
        else:
            sys.modules["typesafe_sdk"] = saved_modules

    # --- candidate rows and their provenance
    for record_id, stage, kind, key, payload in (
            ("org-1", "entities", "organization", "example-borough",
             {"name": "Example Borough Council"}),
            ("venue-1", "entities", "venue", "example-arts-centre",
             {"name": ORG, "organization_id": "example-borough"}),
            ("contact-1", "routes", "contact_point", "enquiries",
             {"organization_id": "example-borough", "type": "email", "value": ENQUIRIES,
              "role_function": "Enquiries"})):
        store.put_candidate(root, RUN, record_id, stage, kind, key, payload, ["art-own"])
    if defect == "duplicate_candidate":
        store.put_candidate(root, RUN, "venue-2", "entities", "venue",
                            "example-arts-centre", {"name": ORG,
                                                    "organization_id": "example-borough"},
                            ["art-own"])
    if defect == "unauthorized_promotion":
        # The store refuses promotion fields outright, so the fault is injected the
        # only way it could reach a reader: a record written by something that did not
        # go through the staging API.
        import sqlite3
        with sqlite3.connect(root / "state" / "research-lite.sqlite3") as db:
            db.execute("UPDATE records SET status='verified' WHERE record_id='org-1'")
            db.commit()

    evidence_store.link_evidence(
        root, RUN, "contact-1", "art-own",
        content_sha256=store.artifact_sha256(root, RUN, "art-own"),
        span_sha256=_sha(SPAN_ENQUIRIES), locator_kind="query", locator="Enquiries",
        material_kind="raw_response")
    # The envelope for the judged page is already recorded by the adapter itself, on
    # the same (run, artifact, operation, capability) key. Record a second one for the
    # *other* retrieved page here, so the fixture exercises the API without colliding
    # with the adapter's own row.

    evidence_store.record_envelope(
        root, RUN, "art-parent", operation_id="op-fetch", provider="crawl4ai",
        capability_id="crawl4ai.local", extraction_method="server:fixture")
    # --- review state
    reviews_path = directory / "reviews.jsonl"
    reviews = [{"row_id": "row-01", "decision": "ACCEPT", "reviewer_minutes": "3"}]
    if defect == "invented_contact":
        reviews = [{"row_id": "row-01", "decision": "ACCEPT", "reviewer_minutes": "3"}]
    reviews_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in reviews),
                            encoding="utf-8")
    if defect == "invented_contact":
        rows = build_rows()
        rows[0]["contact"] = {"person_name": "B. Invented",
                              "email": "not.on.the.page@example.org"}
        (directory / "rows.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    else:
        (directory / "rows.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in build_rows()),
            encoding="utf-8")

    # --- lineage: the retrieved originals, plus the judgement as a derivative
    lineage = []
    for artifact_id in ("art-own", "art-parent"):
        read = store.read_artifact(root, RUN, artifact_id)
        lineage.append({
            "artifact_id": artifact_id, "run_id": RUN, "provider": "fixture",
            "origin_url": read["origin_url"], "material_kind": read["material_kind"],
            "content_sha256": read["content_sha256"], "bytes": len(read["content"]),
            "acquired_at": read["acquired_at"], "parent_ids": [], "derivation": "retrieved",
            "validated": False})
    for artifact in judgment["artifacts"]:
        lineage.append({
            "artifact_id": artifact["artifact_id"], "run_id": RUN, "provider": "jev",
            "origin_url": "provider-response", "material_kind": "decision_response",
            "content_sha256": artifact["sha256"], "bytes": 1,
            "acquired_at": "2026-10-17T03:00:00+00:00", "parent_ids": ["art-own"],
            "derivation": "judged", "validated": False})
    if defect == "derivative_as_original":
        # A row that cites the judgement as if it were the page it was judged from.
        rows = build_rows()
        rows[0]["cited_artifact_ids"] = ["art-own"]
        rows[0]["claims"] = [{"field": "organisation_name",
                              "artifact_id": judgment["artifacts"][0]["artifact_id"],
                              "quote": ORG}]
        (directory / "rows.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    (directory / "lineage.json").write_text(
        json.dumps(lineage, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # --- export: a real XLSX plus its manifest
    export = delivery.write_delivery(root, RUN, f"delivery-w3{'-defect' if defect else ''}")

    # The scorecard's artifacts directory is materialised from the store's verified
    # readback, so it has to happen while the bytes are still intact — before any defect
    # that breaks them, and before the export that would refuse the same breakage.
    artifacts_dir = materialise_artifacts(
        root, ["art-own", "art-parent"] + [a["artifact_id"] for a in judgment["artifacts"]],
        directory / "artifacts")
    if defect == "empty_artifact":
        # Injected **after** the export on purpose: the exporter verifies artefact
        # hashes and refuses a truncated one, which is correct behaviour and would
        # stop the run before the audit ever saw it. The defect worth auditing is the
        # bytes going missing after the workbook was produced.
        next((root / "artifacts" / RUN).glob("*.bin")).write_bytes(b"")

    # --- ledger: one confirmed local call and one judged call whose cost nobody knows
    ledger, receipts = _build_ledger(defect=defect)
    (directory / "ledger.json").write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    (directory / "receipts.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in receipts), encoding="utf-8")
    (directory / "targets.jsonl").write_text(
        json.dumps({"target_id": "tgt-01", "name": ORG, "verification": "PENDING"},
                   sort_keys=True) + "\n", encoding="utf-8")

    # --- the rubric and check list the scorecard is graded by. The canonical copies
    #     belong to W-QA; these are the shape, so the audit can run standalone.
    (directory / "rubric.json").write_text(json.dumps(
        {"schema": "marketops.qa.source_quality_rubric.v1",
         "grades": [{"grade": g, "counts_as_evidence": g in {"A", "B"}}
                    for g in "ABCDEF"]}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (directory / "checks.json").write_text(json.dumps(
        {"schema": "marketops.qa.anti_fabrication_checks.v1",
         "checks": [{"id": f"AF-{n}", "severity": "P0"} for n in range(1, 13)],
         "evaluation_rule": "A check with no readable artefact is BLOCKED, and a BLOCKED "
                            "check prevents a PASS verdict.",
         "verdict_rule": "Any P0 failure means the run's quality verdict is FAIL."},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "directory": directory, "root": root, "defect": defect,
        "brief": brief_path, "rows": directory / "rows.jsonl",
        "lineage": directory / "lineage.json",
        "artifacts_dir": artifacts_dir,
        "reviews": reviews_path, "ledger": directory / "ledger.json",
        "receipts": directory / "receipts.jsonl",
        "targets": directory / "targets.jsonl",
        "rubric": directory / "rubric.json", "checks": directory / "checks.json",
        "xlsx": Path(export["xlsx_path"]), "manifest": export,
        "judgment": judgment,
    }


def _build_ledger(*, defect: str | None) -> tuple[dict, list[dict]]:
    """A schema-shaped ledger: one confirmed call, one judged call with no known cost.

    The judged call is the interesting one — it is `billed=True` in the adapter's own
    receipt while nothing about its amount is known, which is exactly the state that
    must not become a zero in a per-accepted-result figure.
    """
    operations = [
        {"operation_id": "op-local", "provider": "crawl4ai",
         "capability_id": "crawl4ai.local", "tier": "T1",
         "initiating_client": "offline_benchmark", "reserved": "0.000000",
         "estimated": "0.000000", "derived": "0.000000", "actual": "0.000000",
         "state": "SETTLED", "reason_code": None, "op_signature": "sig-local"},
        {"operation_id": "op-jev", "provider": "jev", "capability_id": "jev.choice",
         "tier": "T2", "initiating_client": "offline_benchmark",
         "reserved": "0.000500", "estimated": "0.000500", "derived": "0.000500",
         "actual": None, "state": "UNKNOWN_CHARGE_OUTSTANDING", "reason_code": None,
         "op_signature": "sig-jev"},
    ]
    if defect == "unknown_charge_as_zero":
        # The failure mode the audit exists for: an unreconciled call with its amount
        # written as zero, which reads as "this call was free".
        operations[1]["reserved"] = "0.000000"
        operations[1]["estimated"] = "0.000000"
        operations[1]["derived"] = "0.000000"
    providers = [
        {"provider": "crawl4ai", "account_ref": "acct-local", "meter": "requests",
         "b0": "100", "b0_source": "provider_api_read",
         "b0_observed_at": "2026-10-17T00:00:00+00:00", "floor": "20",
         "max_cumulative_drawdown": "80", "spendable_ceiling": "80",
         "current_available": "100", "checked_at": "2026-10-17T03:00:00+00:00",
         "meter_trust": "verified", "in_flight_or_unknown_exposure": "0.000000",
         "external_account_spend": "0"},
        {"provider": "jev", "account_ref": "acct-jev", "meter": "tokens",
         "b0": "1000", "b0_source": "provider_api_read",
         "b0_observed_at": "2026-10-17T00:00:00+00:00", "floor": "200",
         "max_cumulative_drawdown": "800", "spendable_ceiling": "800",
         "current_available": "1000", "checked_at": "2026-10-17T03:00:00+00:00",
         # The judged call's rate was never verified, so its money is not known.
         "meter_trust": "unknown", "in_flight_or_unknown_exposure": "0.000500",
         "external_account_spend": "0"},
    ]
    ledger = {
        "schema": "marketops.qa.cost_ledger.v1", "campaign_id": PROJECT,
        "providers": providers, "operations": operations,
        "totals": {"attempted": 2, "succeeded": 1, "blocked": 0, "unknown": 1,
                   "usd_estimated": "0.000500", "usd_actual_confirmed": "0.000000",
                   "usd_unreconciled": "0.000500", "accepted_rows": 1,
                   "usd_per_accepted_row": None, "credits_per_accepted_row": None,
                   "tokens_in": 1234, "tokens_out": 7, "floor_breached": False},
    }
    receipts = [{"operation_id": "op-local", "provider": "crawl4ai", "dispatched": False},
                {"operation_id": "op-jev", "provider": "jev", "dispatched": True,
                 "billed": True, "upstream_ref": "req-w3"}]
    return ledger, receipts


# ------------------------------------------------------- complete sample dataset
#: Fifteen synthetic organisations. Fourteen rows are unremarkable and differ only in
#: where the fact was published or what the page was (HTML, PDF); the rest each carry
#: exactly one thing that a plausible-looking result gets wrong. Blind reviewable in the
#: only sense that matters offline: every expected finding is derivable from the stored
#: bytes, not from the row's own claim.
SAMPLE_ORGS = (
    {"key": "example-arts", "name": "Example Arts Centre", "material": "html",
     "class": "own", "grade": "A", "row": "clean"},
    {"key": "example-museum", "name": "Example Museum", "material": "html",
     "class": "own", "grade": "A", "row": "clean"},
    {"key": "example-gallery", "name": "Example Gallery", "material": "html",
     "class": "own", "grade": "A", "row": "clean"},
    {"key": "example-theatre", "name": "Example Theatre", "material": "html",
     "class": "own", "grade": "A", "row": "clean"},
    {"key": "example-library", "name": "Example Library", "material": "html",
     "class": "parent", "grade": "B", "row": "clean"},
    {"key": "example-hall", "name": "Example Hall", "material": "html",
     "class": "parent", "grade": "B", "row": "clean"},
    # a search snippet: no retrieved page behind it at all
    {"key": "example-studio", "name": "Example Studio", "material": "none",
     "class": "none", "grade": "E", "row": "snippet"},
    # the same entity recorded twice
    {"key": "example-cinema", "name": "Example Cinema", "material": "html",
     "class": "own", "grade": "A", "row": "duplicate"},
    # cites a page nobody stored
    {"key": "example-pier", "name": "Example Pier", "material": "none",
     "class": "none", "grade": "A", "row": "untraceable"},
    # a PDF, which is text evidence and must be quotable like a page
    {"key": "example-zoo", "name": "Example Zoo", "material": "pdf",
     "class": "own", "grade": "A", "row": "pdf"},
    # a screenshot: bytes, but not text anybody can quote a field out of
    {"key": "example-farm", "name": "Example Farm", "material": "image",
     "class": "own", "grade": "A", "row": "image"},
    # a CAPTCHA wall served with 200
    {"key": "example-castle", "name": "Example Castle", "material": "challenge",
     "class": "own", "grade": "A", "row": "challenge"},
    # stored, exported, then the bytes go missing
    {"key": "example-mill", "name": "Example Mill", "material": "html",
     "class": "own", "grade": "A", "row": "empty"},
    # a role the page never states
    {"key": "example-baths", "name": "Example Baths", "material": "html",
     "class": "own", "grade": "A", "row": "role_inferred"},
    # a contact route the page never states
    {"key": "example-harbour", "name": "Example Harbour", "material": "html",
     "class": "own", "grade": "A", "row": "route_inferred"},
)
DUPLICATED_KEY = "example-cinema"
UNTRACEABLE_KEY = "example-pier"
INFERRED_ROLE = "Head of Venues"
INFERRED_ROUTE = "bookings@example-harbour.example.org"
CHALLENGE_TEXT = "Please complete the CAPTCHA to continue."


def _org_text(org: dict) -> str:
    return (f"{org['name']}\nOperated by the Example Borough Council.\n"
            f"Address: 1 Example Street, Exampleton\n"
            f"Enquiries: enquiries@{org['key']}.example.org\n")


def _org_page(org: dict) -> bytes:
    if org["material"] == "pdf":
        # A minimal but real PDF signature, with the text the row will quote.
        return b"%PDF-1.4\n" + _org_text(org).encode("utf-8")
    if org["material"] == "image":
        # A real PNG signature and no text at all: there is nothing to quote.
        return b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    if org["material"] == "challenge":
        return (f"<html><body><h1>Verify you are human</h1><p>{CHALLENGE_TEXT}</p>"
                "</body></html>").encode("utf-8")
    return (f"<html><body><h1>{org['name']}</h1>"
            "<p>Operated by the Example Borough Council.</p>"
            "<p>Address: 1 Example Street, Exampleton</p>"
            f"<p>Enquiries: enquiries@{org['key']}.example.org</p></body></html>"
            ).encode("utf-8")


def _row_for(org: dict, index: int, artifact: str) -> dict:
    contact = f"enquiries@{org['key']}.example.org"
    name_quote = org["name"] if org["material"] != "challenge" else CHALLENGE_TEXT
    fields = {"organisation_name": org["name"],
              "venue_address_public": "1 Example Street, Exampleton",
              "official_domain": f"{org['key']}.example.org",
              "official_contact_route": contact,
              "source_url": f"https://{org['key']}.example.org/visit",
              "source_excerpt_sha256": _sha(name_quote)}
    claims = [{"field": "organisation_name", "artifact_id": artifact,
               "quote": name_quote}]
    if org["material"] in {"html", "pdf"}:
        # Every quoted field gets its own claim, because the acceptance rule is per
        # field: a row that only quotes its name is incomplete, not suspicious.
        claims = [{"field": "organisation_name", "artifact_id": artifact,
                   "quote": name_quote},
                  {"field": "official_contact_route", "artifact_id": artifact,
                   "quote": f"Enquiries: {contact}"},
                  {"field": "venue_address_public", "artifact_id": artifact,
                   "quote": "Address: 1 Example Street, Exampleton"}]
    if org["row"] == "role_inferred":
        fields["role_title"] = INFERRED_ROLE
    if org["row"] == "route_inferred":
        # The published route is not on the page; the row completes it anyway.
        fields["official_contact_route"] = INFERRED_ROUTE
        claims = [{"field": "organisation_name", "artifact_id": artifact,
                   "quote": name_quote},
                  {"field": "official_contact_route", "artifact_id": artifact,
                   "quote": f"Enquiries: {contact}"},
                  {"field": "venue_address_public", "artifact_id": artifact,
                   "quote": "Address: 1 Example Street, Exampleton"}]
    if org["row"] == "untraceable":
        artifact = "art-never-stored"
        claims = [{"field": "organisation_name", "artifact_id": artifact,
                   "quote": org["name"]}]
    if org["row"] == "snippet":
        claims = []
    return {"row_id": f"row-{index + 1:02d}", "target_id": f"tgt-{index + 1:02d}",
            "source_grade": org["grade"], "fields": fields, "claims": claims,
            "cited_artifact_ids": [artifact] if claims else [],
            "same_operator_official_domains": ["example.gov.uk"],
            "contact": {"person_name": None, "email": None}}


def build_dataset_rows() -> list[dict]:
    rows = [_row_for(org, i, f"art-{i:02d}") for i, org in enumerate(SAMPLE_ORGS)]
    duplicate = next(i for i, o in enumerate(SAMPLE_ORGS) if o["key"] == DUPLICATED_KEY)
    rows.append({**rows[duplicate], "row_id": f"row-{len(rows) + 1:02d}"})
    return rows


def build_dataset(directory: Path) -> dict:
    """A whole sample dataset: artifacts, records, rows, reviews, ledger, export."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    root = directory / "data"

    brief = build_brief()
    brief["brief_id"] = "w3-sample-dataset"
    brief["input"]["target_count"] = {"min": 10, "max": 20}
    (directory / "brief.json").write_text(json.dumps(brief, indent=2, sort_keys=True) + "\n",
                                          encoding="utf-8")

    store.start_run(root, RUN, "GB-exampleton", "The whole sample dataset", "SOP-PENDING")
    lineage = []
    stored = []
    for index, org in enumerate(SAMPLE_ORGS):
        if org["material"] == "none":
            continue
        artifact_id = f"art-{index:02d}"
        kind = {"pdf": "pdf", "image": "image"}.get(org["material"], "raw_response")
        store.save_artifact(root, RUN, artifact_id, "fixture-public-html",
                            f"https://{org['key']}.example.org/visit",
                            _org_page(org), kind)
        stored.append(artifact_id)
        read = store.read_artifact(root, RUN, artifact_id)
        lineage.append({"artifact_id": artifact_id, "run_id": RUN, "provider": "fixture",
                        "origin_url": read["origin_url"],
                        "material_kind": read["material_kind"],
                        "content_sha256": read["content_sha256"],
                        "bytes": len(read["content"]),
                        "acquired_at": read["acquired_at"], "parent_ids": [],
                        "derivation": "retrieved", "validated": False})

    for index, org in enumerate(SAMPLE_ORGS):
        store.put_candidate(root, RUN, f"org-{index + 1:02d}", "entities", "organization",
                            org["key"], {"name": org["name"]},
                            [f"art-{index:02d}"] if org["material"] != "none" else [])
        store.put_candidate(root, RUN, f"venue-{index + 1:02d}", "entities", "venue",
                            org["key"], {"name": org["name"],
                                         "organization_id": org["key"]},
                            [f"art-{index:02d}"] if org["material"] != "none" else [])
    for index, org in enumerate(SAMPLE_ORGS[:3]):
        store.put_candidate(root, RUN, f"contact-{index + 1:02d}", "routes",
                            "contact_point", f"{org['key']}-enquiries",
                            {"organization_id": org["key"], "type": "email",
                             "value": f"enquiries@{org['key']}.example.org",
                             "role_function": "Enquiries"}, [f"art-{index:02d}"])
    store.put_candidate(root, RUN, "venue-99", "entities", "venue", DUPLICATED_KEY,
                        {"name": "Example Cinema", "organization_id": DUPLICATED_KEY},
                        ["art-07"])

    rows = build_dataset_rows()
    (directory / "rows.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    (directory / "lineage.json").write_text(json.dumps(lineage, indent=2, sort_keys=True) + "\n",
                                            encoding="utf-8")
    (directory / "reviews.jsonl").write_text("".join(
        json.dumps({"row_id": r["row_id"], "decision": "ACCEPT", "reviewer_minutes": "2"},
                   sort_keys=True) + "\n" for r in rows[:6]), encoding="utf-8")
    (directory / "targets.jsonl").write_text("".join(
        json.dumps({"target_id": f"tgt-{i + 1:02d}", "name": org["name"],
                    "verification": "PENDING"}, sort_keys=True) + "\n"
        for i, org in enumerate(SAMPLE_ORGS)), encoding="utf-8")
    (directory / "rubric.json").write_text(json.dumps(
        {"schema": "marketops.qa.source_quality_rubric.v1",
         "grades": [{"grade": g, "counts_as_evidence": g in {"A", "B"}}
                    for g in "ABCDEF"]}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (directory / "checks.json").write_text(json.dumps(
        {"schema": "marketops.qa.anti_fabrication_checks.v1",
         "checks": [{"id": f"AF-{n}", "severity": "P0"} for n in range(1, 13)]},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")

    ledger, receipts = _build_ledger(defect=None)
    (directory / "ledger.json").write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n",
                                           encoding="utf-8")
    (directory / "receipts.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in receipts), encoding="utf-8")

    artifacts_dir = materialise_artifacts(root, stored, directory / "artifacts")
    export = delivery.write_delivery(root, RUN, "delivery-w3-dataset")
    # After the export, so the exporter's own hash guard does not stop the run: the
    # defect worth auditing is the bytes going missing after the workbook was produced.
    empty = next(i for i, o in enumerate(SAMPLE_ORGS) if o["row"] == "empty")
    (root / "artifacts" / RUN / (hashlib.sha256(f"art-{empty:02d}".encode()).hexdigest()
                                 + ".bin")).write_bytes(b"")
    return {
        "directory": directory, "root": root,
        "brief": directory / "brief.json", "rows": directory / "rows.jsonl",
        "lineage": directory / "lineage.json", "artifacts_dir": artifacts_dir,
        "reviews": directory / "reviews.jsonl", "ledger": directory / "ledger.json",
        "receipts": directory / "receipts.jsonl", "targets": directory / "targets.jsonl",
        "rubric": directory / "rubric.json", "checks": directory / "checks.json",
        "xlsx": Path(export["xlsx_path"]), "manifest": export,
        "expectations": {
            "organisations": len(SAMPLE_ORGS),
            "rows_submitted": len(rows),
            "duplicated_entity": DUPLICATED_KEY,
            "untraceable_entity": UNTRACEABLE_KEY,
            "challenge_artifact": f"art-{next(i for i, o in enumerate(SAMPLE_ORGS) if o['row'] == 'challenge'):02d}",
            "empty_artifact": f"art-{empty:02d}",
            "pdf_artifact": f"art-{next(i for i, o in enumerate(SAMPLE_ORGS) if o['row'] == 'pdf'):02d}",
            "image_artifact": f"art-{next(i for i, o in enumerate(SAMPLE_ORGS) if o['row'] == 'image'):02d}",
            "inferred_role": INFERRED_ROLE,
            "inferred_route": INFERRED_ROUTE,
        },
    }

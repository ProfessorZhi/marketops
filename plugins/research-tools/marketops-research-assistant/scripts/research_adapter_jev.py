#!/usr/bin/env python3
"""Artifact-bound TypeSafe Jev judgments: Choice, Noul, Score and, optionally, Box.

## What this module answers

`research_provider_extensions._jev_choice` takes `document=<free text from the
Agent>` and sends it straight to the model, so a judgment can never be traced back
to stored material (independent audit 2026-10-16, finding F-6, severity P2). Here
the only way to reach :func:`execute` is with :class:`EvidenceBinding` objects minted
by :func:`bind_evidence`, and every binding is re-derived from the saved bytes
immediately before the call. The text that is sent is therefore provably the text
the *server* extracted from a hash-verified artifact; an Agent-supplied string has
no path into the request.

## Three things this adapter deliberately does not do

* **It does not decide price.** It reports token counts and the provider's request
  id and leaves `actual_usd` to the ledger (contract §6, IA-2). An adapter that
  priced its own calls would be a second pricing authority.
* **It does not retry.** `RetryPolicy(max_retries=0)` on every call: one dispatch is
  one charge, and a retry inside the SDK is invisible to the budget (IA-3).
* **It does not read a credential from the environment.** The key arrives as an
  argument (IA-1). The core adapter's docstring explains at length why the SDK's own
  `TYPESAFE_API_KEY` fallback is refused; this module simply never constructs that
  situation.

## Capability checks, not capability assumptions

`Box` and `Image` exist in `typesafe-sdk` 0.7.5 and do **not** exist in 0.7.1, and
`requirements-lite.txt` admits `typesafe-sdk>=0.7,<1`. So Box is asked for at runtime
via :func:`sdk_capabilities` and refused with an explicit code when the installed SDK
cannot do it — never faked, never inferred from a version string.

## Refusals live in the contract's code space

`research_gateway` will not translate a refusal it cannot classify: a raised
`ValueError` whose first colon-delimited token is outside §5.3 raises
`ContractViolation`, and so does a BLOCKED result whose `caveats` hold no §5.3
member. So every caller-facing refusal here is raised as a :class:`JevRefusal`,
which opens its message with the §5.3 code and carries the adapter's own diagnosis
in `local_code`. `JEV_OUTCOME_CODES` are what a call *returned*, not what it
refused, and are deliberately absent from the mapping; `JEV_INTERNAL_BUG_CODES`
are unmapped so a bug surfaces loudly instead of being filed under a plausible
refusal code.

## A judgment is a suggestion

"Jev conclusions serve only as suggestions, not as evidence." The stored artifact is
`decision_response`, which :mod:`research_evidence` already classifies as not
evidence, and no path here writes a promotion or authorization field.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import research_evidence
import research_evidence_store as evidence_store
import research_lite_store as store
from research_event_journal import _root, append_event

#: The routing key. `research_capability_registry` reviews Jev under
#: ``typesafe_jev`` and both `research_wiring.PROVIDER_ADAPTERS` and the ledger key it
#: that way, so this adapter must answer to the same name or the conformance probe
#: reports ``PROVIDER_MISMATCH`` and the gateway never finds it.
PROVIDER = "typesafe_jev"
#: The vendor's own short spelling, which the capability ids and W-QA's
#: `cost-ledger.schema.json` provider enum both use. Kept as a name rather than a
#: comment so the translation in `research_ledger_bridge` cites something.
LEGACY_PROVIDER = "jev"
TRANSPORT = "python_sdk"

#: Primitives this adapter can ask for, and the registry capability each maps to.
PRIMITIVE_CAPABILITY = {"choice": "jev.choice", "noul": "jev.noul",
                        "score": "jev.score", "box": "jev.box"}
PRIMITIVES = ("choice", "noul", "score", "box")
#: The SDK class that has to exist on the installed version for a primitive to be askable.
PRIMITIVE_ATTR = {"choice": "Choice", "noul": "Noul", "score": "Score", "box": "Box"}
#: The reverse map, for the §3.1 request path: a capability id names one primitive.
PRIMITIVE_FOR_CAPABILITY = {capability: primitive
                            for primitive, capability in PRIMITIVE_CAPABILITY.items()}
#: The registry's reviewed parameter allowlist, mirrored here as defence in depth.
#: The registry is the authority; a request that reaches this adapter with a key
#: outside the set is refused rather than forwarded, because the gateway's own check
#: is one component and a second one costs nothing.
#:
#: `evidence_query` is a locator for saved text, not a second way to supply the
#: material. It is accepted on the three text primitives only. The registry entry
#: still has to list it for the gateway route to admit the key; this mirror refuses
#: every other name and every use on a box capability regardless.
ALLOWED_PARAMS = {
    # `evidence_query` is a locator into saved material, never the material itself.
    # It is a reviewed parameter for text judgments only; a box reads a whole image
    # and has no text span to locate inside.
    "jev.choice": frozenset({"artifact_id", "question", "options", "evidence_query"}),
    "jev.noul": frozenset({"artifact_id", "question", "evidence_query"}),
    "jev.score": frozenset({"artifact_id", "question", "scale", "evidence_query"}),
    "jev.box": frozenset({"artifact_id", "target"}),
}

#: Bounded by policy, not by the API: one call shares one input, so a larger batch
#: widens the blast radius of a single dispatch without lowering its unit price.
MAX_QUESTIONS = 8
#: Matches the excerpt extractor's own limit; a second artifact doubles the input.
MAX_ARTIFACTS = 3
MIN_SPAN_CHARS = 20
#: The upstream text bound for a single judgment, matching the previous adapter.
MAX_SPAN_CHARS = 4000
#: Bounds of an evidence locator. They mirror `research_evidence.excerpts`, which
#: receives the locator as a query; keeping the two equal means a locator this
#: adapter accepts is one the extractor can serve.
MIN_LOCATOR_CHARS = 2
MAX_LOCATOR_CHARS = 60
NAME = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

#: The contract's §5.3 closed set. `research_contract.py` (W-B) does not exist yet,
#: so this is a transcription; `test_wave2_jev_refusal_channel` asserts it equals the
#: integrator's `research_gateway.REASON_CODES` whenever that module is importable,
#: so the two copies cannot drift silently.
CONTRACT_REASON_CODES = frozenset({
    "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
    "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
    "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
    "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
    "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING",
    "RESEARCH_NETWORK_DISABLED", "WALLET_PRECONDITION_FAILED", "GRANT_SIGNATURE_INVALID",
})

#: Local diagnostics that stop a *dispatch* before it happens. Each one must map to a
#: §5.3 code, because the gateway refuses to translate a refusal it cannot classify:
#: `_adapter_failure` raises `ContractViolation` on an unclassifiable `ValueError` and
#: `_result_refusal_code` raises on a BLOCKED result whose caveats carry no §5.3 member.
JEV_REFUSAL_CODES = (
    # Capability absent in this installation. The capability is real; this
    # interpreter cannot serve it.
    "JEV_SDK_NOT_INSTALLED",
    "JEV_PRIMITIVE_UNSUPPORTED_BY_SDK",
    "JEV_BOX_UNSUPPORTED_BY_SDK",
    "JEV_BATCH_UNSUPPORTED_BY_SDK",
    "JEV_CREDENTIAL_ABSENT",
    # The request as given cannot be served: malformed questions, or an input that is
    # not a binding to a hash-verifiable saved artifact. `JEV_EVIDENCE_READBACK_MISMATCH`
    # sits here rather than in an integrity code because what the adapter refuses is
    # *this request* — the stored bytes moved, so these inputs are no longer valid.
    "JEV_OPTIONS_INVALID",
    "JEV_NOUL_CRITERIA_INVALID",
    "JEV_SCORE_LEVELS_INVALID",
    "JEV_QUESTION_INVALID",
    "JEV_QUESTION_NAME_INVALID",
    "JEV_QUESTION_NAME_DUPLICATE",
    "JEV_QUESTIONS_REQUIRED",
    "JEV_BATCH_TOO_LARGE",
    "JEV_PRIMITIVE_INVALID",
    "JEV_BOX_IMAGE_ARTIFACT_INVALID",
    "JEV_BOX_CANNOT_SHARE_A_TEXT_BATCH",
    "JEV_EVIDENCE_BINDING_REQUIRED",
    "JEV_EVIDENCE_LOCATOR_INVALID",
    "JEV_EVIDENCE_LOCATOR_NO_MATCH",
    "JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN",
    "JEV_EVIDENCE_READBACK_MISMATCH",
    "JEV_EVIDENCE_SPAN_MISMATCH",
    "JEV_EVIDENCE_SPAN_TOO_SHORT",
    "JEV_TEXT_JUDGMENT_REQUIRES_TEXT_EVIDENCE",
    "JEV_BOX_REQUIRES_AN_IMAGE_ARTIFACT",
    "JEV_BOX_ARTIFACT_MISMATCH",
    # The §3.1 request path: a capability id this adapter does not serve, a parameter
    # outside the reviewed allowlist, or no data root to read saved evidence from.
    # All three are pre-dispatch, so they must be refusals rather than exceptions —
    # the gateway has already reserved by the time it calls `execute`, and anything it
    # cannot classify is booked as an UNKNOWN charge for a call that never happened.
    "JEV_CAPABILITY_UNKNOWN",
    "JEV_PARAM_NOT_ALLOWED",
    "JEV_DATA_ROOT_ABSENT",
    "JEV_CANCEL_NOT_SUPPORTED",
)

#: What a call returned, not what it refused. These ride on FAILED/UNKNOWN results and
#: are deliberately absent from the mapping: an outcome is not a refusal, and dressing
#: one up as a §5.3 code would put a code on a result the gateway never reads a refusal
#: from.
JEV_OUTCOME_CODES = (
    "JEV_RETURNED_UNDECLARED_OPTION",
    "JEV_RETURNED_INVALID_NOUL",
    "JEV_RETURNED_INVALID_SCORE",
    "JEV_RETURNED_INVALID_BOX",
    "JEV_UPSTREAM_FAILED",
)

#: Breaches of this module's own invariants. Deliberately unmapped: a bug should
#: surface as a `ContractViolation` at the gateway rather than be filed under a
#: plausible-looking refusal code that hides it.
JEV_INTERNAL_BUG_CODES = (
    "JEV_REASON_CODE_NOT_DECLARED",
    "JEV_NOTE_CODE_NOT_DECLARED",
    "JEV_BLOCKED_WITHOUT_REFUSAL_CODE",
    "ADAPTER_RESULT_FIELD_SET_DRIFT",
)

#: The whole local code space, for `_result`'s validation.
JEV_REASON_CODES = JEV_REFUSAL_CODES + JEV_OUTCOME_CODES

#: Local refusal code -> §5.3 code. Total over `JEV_REFUSAL_CODES`; a test asserts that,
#: because a gap here becomes a `ContractViolation` at exactly the wrong moment.
CONTRACT_CODE_FOR_JE: dict[str, str] = {
    **{code: "CAPABILITY_NOT_AVAILABLE" for code in (
        "JEV_SDK_NOT_INSTALLED", "JEV_PRIMITIVE_UNSUPPORTED_BY_SDK",
        "JEV_BOX_UNSUPPORTED_BY_SDK", "JEV_BATCH_UNSUPPORTED_BY_SDK",
        "JEV_CREDENTIAL_ABSENT", "JEV_DATA_ROOT_ABSENT",
        "JEV_CANCEL_NOT_SUPPORTED")},
    **{code: "CAPABILITY_PARAM_NOT_ALLOWED" for code in (
        "JEV_OPTIONS_INVALID", "JEV_NOUL_CRITERIA_INVALID", "JEV_SCORE_LEVELS_INVALID",
        "JEV_QUESTION_INVALID", "JEV_QUESTION_NAME_INVALID",
        "JEV_QUESTION_NAME_DUPLICATE", "JEV_QUESTIONS_REQUIRED", "JEV_BATCH_TOO_LARGE",
        "JEV_PRIMITIVE_INVALID", "JEV_BOX_IMAGE_ARTIFACT_INVALID",
        "JEV_BOX_CANNOT_SHARE_A_TEXT_BATCH", "JEV_EVIDENCE_BINDING_REQUIRED",
        "JEV_EVIDENCE_LOCATOR_INVALID", "JEV_EVIDENCE_LOCATOR_NO_MATCH",
        "JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN",
        "JEV_EVIDENCE_READBACK_MISMATCH", "JEV_EVIDENCE_SPAN_MISMATCH",
        "JEV_EVIDENCE_SPAN_TOO_SHORT", "JEV_TEXT_JUDGMENT_REQUIRES_TEXT_EVIDENCE",
        "JEV_BOX_REQUIRES_AN_IMAGE_ARTIFACT", "JEV_BOX_ARTIFACT_MISMATCH",
        "JEV_PARAM_NOT_ALLOWED")},
    "JEV_CAPABILITY_UNKNOWN": "CAPABILITY_UNKNOWN",
}


class JevRefusal(ValueError):
    """A local refusal, in the contract's code space and in its refusal channel.

    The repository's refusal channel is `ValueError("<CODE>: detail")` with the code
    taken from before the first colon — so the message opens with the §5.3 code. The
    adapter's own diagnosis rides along, both in `local_code` and in the message, so a
    caller can still tell *why* the capability was unavailable without a second
    channel. `reason_code` is also set because the gateway reads that attribute first.
    """

    def __init__(self, local_code: str, detail: str = "") -> None:
        contract = CONTRACT_CODE_FOR_JE.get(local_code)
        if contract is None:
            # Losing the local code entirely would be worse than a loud failure here.
            raise ValueError(f"JEV_REASON_CODE_NOT_DECLARED: {local_code}")
        self.local_code = local_code
        self.reason_code = contract
        super().__init__(f"{contract}: adapter_local={local_code}"
                         + (f" {detail}" if detail else ""))


def refusal_code(exc: BaseException) -> str | None:
    """The §5.3 code a raised refusal carries, or None if it carries none."""
    declared = getattr(exc, "reason_code", None)
    if isinstance(declared, str) and declared in CONTRACT_REASON_CODES:
        return declared
    if isinstance(exc, BaseException):
        head = str(exc).split(":", 1)[0].strip()
        return head if head in CONTRACT_REASON_CODES else None
    return None

#: What a result is missing, as opposed to what it refused. `caveats` carries these
#: **codes**, never their prose: a receipt read by a program must not depend on
#: wording, and the sentence below is for the human reading the receipt afterwards.
NOTES = {
    "REQUEST_ID_UNAVAILABLE": "The provider response carried no request id; none was invented.",
    "USAGE_UNAVAILABLE": "The provider reported no usage; token counts are unknown, not zero.",
    "MODEL_IS_AN_ALIAS_NOT_A_PINNED_VERSION": "No model was pinned; an alias may drift between calls.",
    "JUDGMENT_IS_NOT_SOURCE_EVIDENCE": "Typed model judgment is not source evidence or SOP approval.",
    "UPSTREAM_ERROR_DETAIL_IN_EVENT_JOURNAL": "The exception type and text are in the run's event journal, not here.",
    "UPSTREAM_QUOTA_OR_PAYMENT": "The provider refused for credits or payment (402 / insufficient credits).",
    "UPSTREAM_RATE_LIMITED": "The provider asked to slow down (429 / rate limit).",
}

#: Words in an upstream error that mean "out of credits" or "slow down".
_QUOTA_MARKERS = ("402", "payment required", "insufficient", "credit", "quota", "balance")
_RATE_MARKERS = ("429", "too many requests", "rate limit", "ratelimit")

#: AdapterResult, field-for-field per contract §3.4. `caveats[0]` carries the
#: adapter-local reason code when there is one, so the field set stays exactly the
#: contract's while the refusal stays machine-readable.
ADAPTER_RESULT_FIELDS = ("outcome", "payload", "artifacts", "upstream_ref", "actual_usd",
                         "actual_source", "quota_units_used", "transport",
                         "schema_verdict", "caveats")
#: §3.4 plus one sibling the facade consumes (`envelopes`, contract §3.5). It is a
#: named extension rather than a shuffled field: a caller constructing an
#: `AdapterResult` from this dict subsets on `ADAPTER_RESULT_FIELDS`, and the
#: envelope list stays available for the export path that requires it. It is
#: always present — empty on a refusal — so the shape never depends on the outcome.
RESULT_FIELDS = ADAPTER_RESULT_FIELDS + ("envelopes",)

OUTCOME_OK = "OK"
OUTCOME_BLOCKED = "BLOCKED"
OUTCOME_FAILED = "FAILED"
OUTCOME_UNKNOWN = "UNKNOWN"

#: The name recorded as the extractor. It is the server's own excerpt reader, so a
#: binding produced anywhere else stays distinguishable in the provenance link.
EXTRACTOR = "server:research_evidence.excerpts"
#: An image has no extracted span: the whole asset is the input. Named rather than
#: left as an empty string so an envelope never reads as "read, found nothing".
EXTRACTOR_IMAGE = "none:image_asset"
REDACTED = "[REDACTED]"


# --------------------------------------------------------------------- SDK probe
def _sdk_module():
    """The installed `typesafe_sdk`, or `None`. Never imports a vendored copy."""
    try:
        import typesafe_sdk  # noqa: PLC0415 - probed, not a hard dependency
    except Exception:  # noqa: BLE001 - a missing SDK is an answer, not a crash
        return None
    return typesafe_sdk


def _introspectable_batch(sdk) -> bool:
    """Whether the client exposes a multi-question entry point.

    The SDK's `questions` parameter is a Mapping keyed by answer name, which is what
    makes batching one request instead of a loop. The check is that the parameter
    exists; a signature that cannot be read is not evidence of support.
    """
    method = getattr(getattr(sdk, "TypeSafeClient", None), "system_one", None)
    if method is None:
        return False
    try:
        return "questions" in inspect.signature(method).parameters
    except (TypeError, ValueError):
        return False


def sdk_capabilities() -> dict:
    """What the *installed* SDK can actually do. Measured, never assumed.

    Reported per primitive so a caller can see which one is missing on the version
    it happens to have, rather than a single opaque boolean.
    """
    sdk = _sdk_module()
    if sdk is None:
        return {"installed": False, "version": None,
                "primitives": {p: False for p in PRIMITIVES},
                "box": False, "images": False, "batch": False,
                "usage_fields": False, "request_id_field": False,
                "source": "typesafe_sdk is not importable in this interpreter"}
    primitives = {p: hasattr(sdk, attr) for p, attr in PRIMITIVE_ATTR.items()}
    return {
        "installed": True,
        "version": getattr(sdk, "__version__", None),
        "primitives": primitives,
        "box": primitives["box"],
        "images": hasattr(sdk, "Image"),
        "batch": _introspectable_batch(sdk),
        "usage_fields": hasattr(sdk, "Usage"),
        "request_id_field": hasattr(getattr(sdk, "SystemOneResponse", None), "request_id"),
        "source": "introspection of the installed typesafe_sdk",
    }


# -------------------------------------------------------------------- questions
@dataclass(frozen=True)
class JevQuestion:
    """One question, already validated. Built only by the four constructors below."""

    name: str
    primitive: str
    instructions: str
    criteria: Any = None
    image_artifact_id: str | None = None


def _checked_name(value: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise JevRefusal("JEV_QUESTION_NAME_INVALID")
    return value


def _checked_instructions(value: str) -> str:
    if not isinstance(value, str) or not 10 <= len(value) <= 500:
        raise JevRefusal("JEV_QUESTION_INVALID")
    return value


def choice_question(name: str, instructions: str, options: Sequence[str], *,
                    descriptions: dict[str, str] | None = None) -> JevQuestion:
    """A selection between named alternatives.

    Each option carries its own description when one is given, so the model chooses
    between described outcomes rather than between bare labels.
    """
    _checked_name(name); _checked_instructions(instructions)
    if (isinstance(options, (str, bytes)) or not isinstance(options, (list, tuple))
            or not 2 <= len(options) <= 255
            or any(not isinstance(v, str) or not NAME.fullmatch(v) for v in options)
            or len(options) != len(set(options))):
        raise JevRefusal("JEV_OPTIONS_INVALID")
    described = descriptions or {}
    if (set(described) - set(options)
            or any(not isinstance(v, str) or not v for v in described.values())):
        raise JevRefusal("JEV_OPTIONS_INVALID")
    return JevQuestion(name, "choice", instructions,
                       {label: described.get(label) for label in options})


def noul_question(name: str, instructions: str, *, true_description: str | None = None,
                  false_description: str | None = None) -> JevQuestion:
    """A yes/no question, with either outcome describable in words."""
    _checked_name(name); _checked_instructions(instructions)
    criteria = None
    if true_description is not None or false_description is not None:
        criteria = {}
        if true_description is not None:
            criteria["true"] = true_description
        if false_description is not None:
            criteria["false"] = false_description
        if any(not isinstance(v, str) or not v for v in criteria.values()):
            raise JevRefusal("JEV_NOUL_CRITERIA_INVALID")
    return JevQuestion(name, "noul", instructions, criteria)


def score_question(name: str, instructions: str, levels: Sequence[str]) -> JevQuestion:
    """An ordered rubric. Position determines the score, starting at zero."""
    _checked_name(name); _checked_instructions(instructions)
    if (isinstance(levels, (str, bytes)) or not isinstance(levels, (list, tuple))
            or not 2 <= len(levels) <= 10
            or any(not isinstance(v, str) or not 3 <= len(v) <= 200 for v in levels)
            or len(levels) != len(set(levels))):
        raise JevRefusal("JEV_SCORE_LEVELS_INVALID")
    return JevQuestion(name, "score", instructions, tuple(levels))


def box_question(name: str, instructions: str, *, image_artifact_id: str) -> JevQuestion:
    """What to locate inside a saved image artifact."""
    _checked_name(name); _checked_instructions(instructions)
    if not isinstance(image_artifact_id, str) or not store.ID.fullmatch(image_artifact_id):
        raise JevRefusal("JEV_BOX_IMAGE_ARTIFACT_INVALID")
    return JevQuestion(name, "box", instructions, None, image_artifact_id)


def _normalise_questions(questions: Sequence[JevQuestion]) -> list[JevQuestion]:
    if isinstance(questions, (str, bytes)) or not isinstance(questions, (list, tuple)):
        raise JevRefusal("JEV_QUESTIONS_REQUIRED")
    if not questions:
        raise JevRefusal("JEV_QUESTIONS_REQUIRED")
    if len(questions) > MAX_QUESTIONS:
        raise JevRefusal("JEV_BATCH_TOO_LARGE")
    seen = set()
    for question in questions:
        if not isinstance(question, JevQuestion):
            raise JevRefusal("JEV_QUESTIONS_REQUIRED")
        if question.primitive not in PRIMITIVES:
            raise JevRefusal("JEV_PRIMITIVE_INVALID")
        if question.name in seen:
            raise JevRefusal("JEV_QUESTION_NAME_DUPLICATE")
        seen.add(question.name)
    # A box reads an image; every other primitive reads text. Both in one call would
    # need one `state` to be two things at once, so it is refused rather than split
    # silently into two charged calls.
    if "box" in {q.primitive for q in questions} and len({q.primitive for q in questions}) > 1:
        raise JevRefusal("JEV_BOX_CANNOT_SHARE_A_TEXT_BATCH")
    return list(questions)


# ---------------------------------------------------------------------- binding
@dataclass(frozen=True)
class EvidenceBinding:
    """A judgment input, pinned to saved bytes and to the span actually extracted.

    :func:`bind_evidence` is the only minter, and :func:`execute` re-derives the
    span from disk before dispatch, so a hand-edited instance fails the
    re-derivation instead of being trusted.
    """

    artifact_id: str
    run_id: str
    content_sha256: str
    span_text: str
    span_sha256: str
    locator_kind: str
    locator: str
    material_kind: str
    extracted_by: str

    @property
    def is_image(self) -> bool:
        return self.locator_kind == "asset"


def _checked_locator(query: object) -> str:
    """Validate a locator without ever treating it as content.

    A locator is a short, printable phrase that must occur in the saved material.
    Anything else — a document, a multi-line block, a control character, a value
    that is not a string — is refused before extraction, so a caller cannot smuggle
    free text in under the name of a locator.
    """
    if not isinstance(query, str) or not MIN_LOCATOR_CHARS <= len(query) <= MAX_LOCATOR_CHARS:
        raise JevRefusal("JEV_EVIDENCE_LOCATOR_INVALID")
    if not query.strip() or any(not char.isprintable() for char in query):
        raise JevRefusal("JEV_EVIDENCE_LOCATOR_INVALID")
    if not any(char.isalnum() for char in query):
        raise JevRefusal("JEV_EVIDENCE_LOCATOR_INVALID")
    return query


def _extract_span(store_root: Path, run_id: str, artifact_id: str, query: str) -> str:
    """The server-side extraction. The caller never supplies the text itself."""
    result = research_evidence.excerpts(
        store_root, run_id, [artifact_id], [query] if query else [])
    row = result["results"][0]
    # A locator that matches nothing must not degrade into "the head" or an empty
    # span: either would send a judgment about material the caller did not select.
    if query and not row["query_hits"]:
        raise ValueError("EXCERPT_QUERY_NO_MATCH")
    return "\n".join(row["snippets"]).strip()


def _extracted_span(store_root: Path, run_id: str, artifact_id: str, query: str) -> str:
    """`_extract_span`, with the extractor's refusals in this adapter's code space."""
    try:
        return _extract_span(store_root, run_id, artifact_id, query)
    except ValueError as exc:
        if str(exc) == "EXCERPT_SOURCE_HASH_MISMATCH":
            raise JevRefusal("JEV_EVIDENCE_READBACK_MISMATCH") from None
        if str(exc) == "EXCERPT_ARTIFACT_NOT_IN_RUN":
            raise JevRefusal("JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN") from None
        if str(exc) == "EXCERPT_QUERY_NO_MATCH":
            raise JevRefusal("JEV_EVIDENCE_LOCATOR_NO_MATCH") from None
        raise


def _readback(store_root: Path, run_id: str, artifact_id: str) -> dict:
    try:
        return store.read_artifact(store_root, run_id, artifact_id)
    except ValueError as exc:
        if str(exc) == "ARTIFACT_NOT_IN_RUN":
            raise JevRefusal("JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN") from None
        raise JevRefusal("JEV_EVIDENCE_READBACK_MISMATCH") from None


def bind_evidence(root: Path | str | None, run_id: str, artifact_id: str,
                  *, query: str = "") -> EvidenceBinding:
    """Bind a judgment input to a saved artifact, extracting the span server-side.

    `query` is a *locator*, not content: it selects which part of the saved material
    the judgment should read. An empty query takes the head of the material, which
    is the backward-compatible default; a non-empty query must match the saved bytes.
    """
    store_root = _root(root)
    store._id(run_id, "run_id")
    try:
        store._id(artifact_id, "artifact_id")
    except ValueError:
        raise JevRefusal("JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN") from None
    if query is None:
        query = ""
    if not isinstance(query, str):
        raise JevRefusal("JEV_EVIDENCE_LOCATOR_INVALID")
    query = _checked_locator(query) if query else ""
    material = _readback(store_root, run_id, artifact_id)

    if material["material_kind"] == "image":
        # The whole asset is the input; there is no text span to locate inside, so a
        # locator here would be a locator that does nothing.
        if query:
            raise JevRefusal("JEV_EVIDENCE_LOCATOR_INVALID")
        return EvidenceBinding(artifact_id, run_id, material["content_sha256"], "",
                               material["content_sha256"], "asset", "", "image",
                               EXTRACTOR_IMAGE)

    span = _extracted_span(store_root, run_id, artifact_id, query)
    if len(span) < MIN_SPAN_CHARS:
        raise JevRefusal("JEV_EVIDENCE_SPAN_TOO_SHORT")
    span = span[:MAX_SPAN_CHARS]
    return EvidenceBinding(
        artifact_id, run_id, material["content_sha256"], span,
        hashlib.sha256(span.encode("utf-8")).hexdigest(),
        "query" if query else "head", query, material["material_kind"], EXTRACTOR)


def _reverify(store_root: Path, binding: EvidenceBinding) -> str:
    """Re-derive the span from disk. Returns the text to send; raises on any drift.

    This is the check that makes the binding load-bearing: after it, the string
    handed to the model is the server's extraction of hash-verified saved bytes.
    """
    material = _readback(store_root, binding.run_id, binding.artifact_id)
    if material["content_sha256"] != binding.content_sha256:
        raise JevRefusal("JEV_EVIDENCE_READBACK_MISMATCH")
    if binding.is_image:
        return ""
    span = _extracted_span(store_root, binding.run_id, binding.artifact_id,
                           binding.locator)[:MAX_SPAN_CHARS]
    if hashlib.sha256(span.encode("utf-8")).hexdigest() != binding.span_sha256:
        raise JevRefusal("JEV_EVIDENCE_SPAN_MISMATCH")
    return span


def sdk_questions(questions: Sequence[JevQuestion]) -> dict:
    """The validated questions as the *installed* SDK's own question objects.

    Public so a test can hand the result to the real package's own validation: the
    shape this adapter emits is then checked against the upstream model rather than
    against a fake we wrote ourselves.
    """
    sdk = _sdk_module()
    built = {}
    for question in questions:
        if question.primitive == "choice":
            built[question.name] = sdk.Choice(instructions=question.instructions,
                                              criteria=dict(question.criteria))
        elif question.primitive == "noul":
            built[question.name] = sdk.Noul(instructions=question.instructions,
                                            criteria=question.criteria)
        elif question.primitive == "score":
            built[question.name] = sdk.Score(instructions=question.instructions,
                                             criteria=list(question.criteria))
        else:
            built[question.name] = sdk.Box(instructions=question.instructions)
    return built


# ----------------------------------------------------------------------- result
class AdapterResult(dict):
    """The §3.4 field set, readable as a mapping *and* as an object.

    A plain `dict` is not enough for the gateway: `_result_refusal_code` reads
    `getattr(result, "caveats", ())` and `invoke` reads `getattr(result, "outcome", ...)`.
    On a dict those attributes do not exist, so a BLOCKED result would have been read
    as a silent FAILED — a wrong answer that looks like an answer. Subclassing `dict`
    keeps `result["outcome"]` and `json.dumps(result)` working for every existing
    caller while attribute access satisfies the contract.
    """

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _result(outcome: str, *, payload=None, artifacts=(), upstream_ref=None,
            caveats=(), reason: str | None = None, envelopes=()) -> AdapterResult:
    """One AdapterResult (§3.4) plus the `envelopes` sibling (§3.5).

    On BLOCKED the first caveat is the §5.3 code and the second names the adapter's
    own diagnosis, so both the gateway's closed-set reader and a human reading the
    receipt get what they need from one field.
    """
    if reason is not None and reason not in JEV_REASON_CODES:
        # A typo in a reason code would otherwise reach a caller as a code that no
        # reader knows, which is how a closed set stops being closed.
        raise ValueError("JEV_REASON_CODE_NOT_DECLARED")
    for note in caveats:
        if note not in NOTES:
            raise ValueError("JEV_NOTE_CODE_NOT_DECLARED")
    if outcome == OUTCOME_BLOCKED:
        if reason not in JEV_REFUSAL_CODES:
            raise ValueError("JEV_BLOCKED_WITHOUT_REFUSAL_CODE")
        ordered = (CONTRACT_CODE_FOR_JE[reason], f"adapter_local:{reason}") + tuple(caveats)
    else:
        ordered = ((reason,) if reason else ()) + tuple(caveats)
    result = AdapterResult({
        "outcome": outcome,
        "payload": payload,
        "artifacts": tuple(artifacts),
        "upstream_ref": upstream_ref,
        "actual_usd": None,             # IA-2: pricing belongs to the ledger
        "actual_source": "NONE",
        "quota_units_used": None,
        "transport": TRANSPORT,
        "schema_verdict": "UNVERIFIED",  # §6: no authorized handshake has run
        "caveats": ordered,
        "envelopes": tuple(envelopes),
    })
    if set(result) != set(RESULT_FIELDS):
        raise ValueError("ADAPTER_RESULT_FIELD_SET_DRIFT")
    return result


def _request_id(response) -> str | None:
    """The provider's request id, or None. The real SDK *raises* when it is absent."""
    try:
        value = response.request_id
    except Exception:  # noqa: BLE001 - absence is a normal SDK outcome, not an error
        return None
    return value if isinstance(value, str) and value.strip() else None


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int_keyed(value) -> dict | None:
    """Score answers key their legend and probabilities by integer level."""
    if not isinstance(value, dict) or not 1 <= len(value) <= 10:
        return None
    out = {}
    for key, item in value.items():
        try:
            out[int(key)] = item
        except (TypeError, ValueError):
            return None
    return out


class JevAdapter:
    """The Jev provider adapter (contract §6).

    `api_key` is required and there is no environment fallback (IA-1). The adapter
    makes no tier, policy or budget decision: it receives an already-authorized
    request and performs it (IA-2).
    """

    provider = PROVIDER
    transport = TRANSPORT

    def __init__(self, api_key: str | None = None, *, root: Path | str | None = None,
                 model: str | None = None, base_url: str | None = None,
                 timeout: float | None = None) -> None:
        self._api_key = (api_key or "").strip()
        # The data root is where the saved evidence lives. It is **not** taken from the
        # request: a caller that could name the store could name a different one, and
        # the whole point of §3.1 is that the request carries ids, not locations.
        self._root = root
        self._model = model
        self._base_url = base_url
        self._timeout = timeout

    # ------------------------------------------------------------------ capability
    def capabilities(self) -> tuple[str, ...]:
        """Capability ids this adapter can serve on this interpreter, right now."""
        caps = sdk_capabilities()
        return tuple(PRIMITIVE_CAPABILITY[p] for p in PRIMITIVES if caps["primitives"][p])

    def probe_schema(self) -> dict:
        """Read-only handshake. Never invents a snapshot it did not receive (§6)."""
        if not self._api_key:
            return {"status": "AUTH_REQUIRED"}
        return {"status": "NOT_PERFORMED",
                "reason": "read_only_handshake_not_authorized_this_wave"}

    # --------------------------------------------------------------------- execute
    def execute(self, request: Any) -> dict:
        """§6 entry point: one already-authorized call.

        ``request`` is duck-typed (§3.1 ``CapabilityRequest``): this reads
        ``capability_id``, ``params``, ``run_id`` and ``operation_id`` and nothing
        else. It deliberately does not import the shared type — that module is W-B's,
        and four read-only attributes is the whole surface needed.

        **Every precondition below raises or returns a refusal, never an unclassified
        exception.** The gateway calls this after `ledger.reserve`, and it books
        anything it cannot classify as an UNKNOWN charge — so a `TypeError` from a
        signature it cannot call, or a stray `KeyError` from a malformed request,
        would leave money held for a call that never left the process.
        """
        capability_id = str(getattr(request, "capability_id", "") or "")
        run_id = str(getattr(request, "run_id", "") or "")
        operation_id = str(getattr(request, "operation_id", "") or "")
        params = getattr(request, "params", None)
        if not isinstance(params, Mapping):
            raise JevRefusal("JEV_PARAM_NOT_ALLOWED", "params must be a mapping")

        primitive = PRIMITIVE_FOR_CAPABILITY.get(capability_id)
        if primitive is None:
            raise JevRefusal("JEV_CAPABILITY_UNKNOWN",
                             f"{capability_id!r} is not a capability this adapter serves")
        surplus = sorted(set(params) - ALLOWED_PARAMS[capability_id])
        if surplus:
            # Key names only: a rejected value may itself be the secret.
            raise JevRefusal("JEV_PARAM_NOT_ALLOWED", "keys=" + ",".join(surplus))

        if self._root is None:
            raise JevRefusal("JEV_DATA_ROOT_ABSENT",
                             "the adapter was constructed without a data root, so no "
                             "saved artifact can be read")
        artifact_id = params.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise JevRefusal("JEV_EVIDENCE_BINDING_REQUIRED", "artifact_id is required")

        # The input is the *saved* artifact; the question is the only free text.
        # `evidence_query` is a locator: it selects a span inside those saved bytes
        # and is re-derived from disk before dispatch. It is never accepted as the
        # text itself, and it is not carried for a box capability (the allowlist
        # above refuses it there).
        question = self._question_for(capability_id, primitive, params, artifact_id)
        evidence_query = params.get("evidence_query", "")
        binding = bind_evidence(self._root, run_id, artifact_id, query=evidence_query)
        return self._run(self._root, run_id=run_id, operation_id=operation_id,
                         bindings=[binding], questions=[question])

    @staticmethod
    def _question_for(capability_id: str, primitive: str, params: Mapping,
                      artifact_id: str) -> JevQuestion:
        """Build the one question the registry allows, or refuse by name."""
        instructions = params.get("question") or params.get("target")
        try:
            if primitive == "choice":
                return choice_question("judgment", instructions, params.get("options"))
            if primitive == "noul":
                return noul_question("judgment", instructions)
            if primitive == "score":
                return score_question("judgment", instructions, params.get("scale"))
            return box_question("judgment", params.get("target"),
                                image_artifact_id=artifact_id)
        except JevRefusal:
            raise
        except (TypeError, ValueError) as exc:
            # The builders raise plain ValueErrors for a shape the provider cannot
            # serve; on this path that is still a refusal, and it must not surface as
            # an unclassified exception after the ledger has reserved.
            raise JevRefusal("JEV_PARAM_NOT_ALLOWED", f"{capability_id}: {exc}") from None

    def cancel(self, upstream_ref: str) -> dict:
        """§6 cancel. Jev exposes no cancellable upstream object.

        The contract's cancel is "idempotent cancellation, only where the upstream
        declares the operation idempotent". A judgement is one request/response with
        no remote handle to withdraw, so this reports that honestly instead of
        pretending a cancel succeeded — a fake success would make a caller believe a
        charge had been stopped.
        """
        return _result(OUTCOME_BLOCKED, reason="JEV_CANCEL_NOT_SUPPORTED",
                       caveats=["JUDGMENT_IS_NOT_SOURCE_EVIDENCE"])

    # ------------------------------------------------------------------ internals
    def _run(self, root: Path | str | None, *, run_id: str, operation_id: str,
             bindings: Sequence[EvidenceBinding],
             questions: Sequence[JevQuestion]) -> dict:
        store_root = _root(root)
        store._id(run_id, "run_id")
        store._id(operation_id, "operation_id")
        asked = _normalise_questions(questions)

        caps = sdk_capabilities()
        if not caps["installed"]:
            return self._blocked(store_root, run_id, operation_id, "JEV_SDK_NOT_INSTALLED")
        missing = sorted({q.primitive for q in asked if not caps["primitives"][q.primitive]})
        if missing == ["box"]:
            return self._blocked(store_root, run_id, operation_id, "JEV_BOX_UNSUPPORTED_BY_SDK")
        if missing:
            return self._blocked(store_root, run_id, operation_id,
                                 "JEV_PRIMITIVE_UNSUPPORTED_BY_SDK")
        if len(asked) > 1 and not caps["batch"]:
            return self._blocked(store_root, run_id, operation_id, "JEV_BATCH_UNSUPPORTED_BY_SDK")
        if not self._api_key:
            return self._blocked(store_root, run_id, operation_id, "JEV_CREDENTIAL_ABSENT")

        verified, blocked = self._verify_bindings(store_root, run_id, operation_id,
                                                  bindings, asked)
        if blocked is not None:
            return blocked
        return self._dispatch(store_root, run_id, operation_id, asked, verified)

    def _blocked(self, store_root: Path, run_id: str, operation_id: str, reason: str) -> dict:
        self._journal(store_root, run_id, operation_id, status="blocked", reason=reason)
        return _result(OUTCOME_BLOCKED, reason=reason,
                       caveats=["JUDGMENT_IS_NOT_SOURCE_EVIDENCE"])

    def _verify_bindings(self, store_root: Path, run_id: str, operation_id: str,
                         bindings, asked: list[JevQuestion]):
        """Verify every binding against disk.

        Returns `(bindings, None)` when the call may proceed, or `(None, result)`
        carrying the refusal. The refusal is a value rather than an exception so
        every local rejection reaches the caller the same way.
        """
        if isinstance(bindings, (str, bytes)) or not isinstance(bindings, (list, tuple)):
            raise JevRefusal("JEV_EVIDENCE_BINDING_REQUIRED")
        if not 1 <= len(bindings) <= MAX_ARTIFACTS:
            raise JevRefusal("JEV_EVIDENCE_BINDING_REQUIRED")
        for binding in bindings:
            if not isinstance(binding, EvidenceBinding):
                raise JevRefusal("JEV_EVIDENCE_BINDING_REQUIRED")
            if binding.run_id != run_id:
                raise JevRefusal("JEV_EVIDENCE_ARTIFACT_NOT_IN_RUN")
        box_artifact = next((q.image_artifact_id for q in asked if q.primitive == "box"), None)
        if box_artifact is not None:
            # Box reads a picture. The artifact named by the question has to be the
            # binding, so the image sent is the image the evidence envelope names.
            if len(bindings) != 1 or bindings[0].artifact_id != box_artifact:
                return None, self._blocked(store_root, run_id, operation_id,
                                           "JEV_BOX_ARTIFACT_MISMATCH")
            if not bindings[0].is_image:
                return None, self._blocked(store_root, run_id, operation_id,
                                           "JEV_BOX_REQUIRES_AN_IMAGE_ARTIFACT")
        elif any(binding.is_image for binding in bindings):
            return None, self._blocked(store_root, run_id, operation_id,
                                       "JEV_TEXT_JUDGMENT_REQUIRES_TEXT_EVIDENCE")
        try:
            for binding in bindings:
                _reverify(store_root, binding)
        except JevRefusal as exc:
            # The message now opens with the §5.3 code, so the local diagnosis has to
            # be read off the exception rather than parsed back out of its text.
            return None, self._blocked(store_root, run_id, operation_id, exc.local_code)
        return list(bindings), None

    def _state(self, store_root: Path, bindings: list[EvidenceBinding]):
        """The request `state`, built from bindings only — never from caller text."""
        if bindings[0].is_image:
            sdk = _sdk_module()
            material = _readback(store_root, bindings[0].run_id, bindings[0].artifact_id)
            return sdk.Image(material["content"])
        spans = [_reverify(store_root, b) for b in bindings]
        # The same key the previous adapter used, so any downstream prompt or
        # fixture that speaks this shape keeps working.
        return {"source_excerpt": "\n\n".join(spans)}

    def _call(self, state, questions):
        sdk = _sdk_module()
        with sdk.TypeSafeClient(
            api_key=self._api_key,
            # None lets the SDK apply its own defaults (typesafe_sdk.constants in
            # 0.7.5); the module has no top-level DEFAULT_* names to read.
            base_url=self._base_url,
            model=self._model,
            timeout=self._timeout,
            # One dispatch is one charge; a retry inside the SDK is invisible to the
            # budget, so retrying is the caller's explicit decision, not this layer's.
            retry=sdk.RetryPolicy(max_retries=0),
        ) as client:
            return client.system_one(state, questions)

    def _dispatch(self, store_root: Path, run_id: str, operation_id: str,
                  asked: list[JevQuestion], bindings: list[EvidenceBinding]) -> dict:
        state = self._state(store_root, bindings)
        built = sdk_questions(asked)
        self._journal(store_root, run_id, operation_id, status="started")
        try:
            response = self._call(state, built)
        except Exception as exc:  # noqa: BLE001 - see the comment below
            # The exception hierarchy is deliberately not caught by type:
            # `TypeSafeError` members come and go between SDK versions, and a dispatch
            # that never returned is an auditable outcome rather than a crash. A
            # timeout is UNKNOWN because the charge really is unknown after dispatch,
            # and nothing is retried.
            timed_out = isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.casefold()
            # The exception text can echo request headers, so it is scrubbed before it
            # goes anywhere, and it goes to the journal rather than into `caveats`,
            # which stays a closed set of codes.
            self._journal(store_root, run_id, operation_id, status="unknown",
                          reason=f"JEV_UPSTREAM_FAILED:"
                                 f"{type(exc).__name__}:{self._scrub(str(exc))}"[:200])
            text = f"{type(exc).__name__} {exc}".casefold()
            extra = (["UPSTREAM_QUOTA_OR_PAYMENT"] if any(m in text for m in _QUOTA_MARKERS)
                     else ["UPSTREAM_RATE_LIMITED"] if any(m in text for m in _RATE_MARKERS)
                     else [])
            return _result(OUTCOME_UNKNOWN if timed_out else OUTCOME_FAILED,
                           reason="JEV_UPSTREAM_FAILED",
                           caveats=["UPSTREAM_ERROR_DETAIL_IN_EVENT_JOURNAL", *extra])
        return self._collect(store_root, run_id, operation_id, asked, response, bindings)

    # ---------------------------------------------------------------------- output
    def _judgement(self, question: JevQuestion, response):
        """One answer, validated against what was asked for. Returns (dict, code)."""
        views = {"choice": "choices", "noul": "nouls", "score": "scores", "box": "boxes"}
        view = getattr(response, views[question.primitive], None) or {}
        answer = view.get(question.name)
        if answer is None:
            return None, "JEV_UPSTREAM_FAILED"
        if question.primitive == "choice":
            choice = getattr(answer, "choice", None)
            if choice not in question.criteria:
                return None, "JEV_RETURNED_UNDECLARED_OPTION"
            out = {"primitive": "choice", "choice": choice,
                   "confidence": _as_float(getattr(answer, "confidence", None)),
                   "probabilities": _as_probabilities(getattr(answer, "probabilities", None))}
        elif question.primitive == "noul":
            noul = _as_float(getattr(answer, "noul", None))
            if noul is None or not 0.0 <= noul <= 1.0:
                return None, "JEV_RETURNED_INVALID_NOUL"
            out = {"primitive": "noul", "noul": noul}
        elif question.primitive == "score":
            score = _as_float(getattr(answer, "score", None))
            if score is None or not 0.0 <= score <= len(question.criteria) - 1:
                return None, "JEV_RETURNED_INVALID_SCORE"
            out = {"primitive": "score", "score": score,
                   "level_count": len(question.criteria),
                   "legend": _as_int_keyed(getattr(answer, "legend", None)),
                   "probabilities": _as_int_keyed(getattr(answer, "probabilities", None)),
                   "confidence": _as_float(getattr(answer, "confidence", None))}
        else:
            box = getattr(answer, "box", None)
            coords = {edge: _as_float(getattr(box, edge, None))
                      for edge in ("top", "bottom", "left", "right")}
            if any(v is None or not 0.0 <= v <= 1.0 for v in coords.values()):
                return None, "JEV_RETURNED_INVALID_BOX"
            out = {"primitive": "box", "box": coords,
                   "confidence": _as_float(getattr(answer, "confidence", None))}
        out["judgment_only"] = True
        return out, None

    def _collect(self, store_root: Path, run_id: str, operation_id: str,
                 asked: list[JevQuestion], response,
                 bindings: list[EvidenceBinding]) -> dict:
        judgements = {}
        for question in asked:
            value, code = self._judgement(question, response)
            if code is not None:
                self._journal(store_root, run_id, operation_id, status="failed", reason=code)
                return _result(OUTCOME_FAILED, reason=code,
                               caveats=["JUDGMENT_IS_NOT_SOURCE_EVIDENCE"])
            judgements[question.name] = value

        upstream_ref = _request_id(response)
        usage_obj = getattr(response, "usage", None)
        input_tokens = getattr(usage_obj, "input_tokens", None) if usage_obj else None
        output_tokens = getattr(usage_obj, "output_tokens", None) if usage_obj else None
        model = str(getattr(response, "model", "") or "")

        notes = ["JUDGMENT_IS_NOT_SOURCE_EVIDENCE"]
        if upstream_ref is None:
            notes.append("REQUEST_ID_UNAVAILABLE")
        if usage_obj is None or input_tokens is None:
            notes.append("USAGE_UNAVAILABLE")
        if self._model is None:
            notes.append("MODEL_IS_AN_ALIAS_NOT_A_PINNED_VERSION")

        payload = {
            "provider": PROVIDER,
            "capability_ids": sorted({PRIMITIVE_CAPABILITY[q.primitive] for q in asked}),
            "model": model,
            "model_pinned": self._model is not None,
            "upstream_ref": upstream_ref,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                # One request carries every question in the batch, so the input is
                # counted once: attributing it to each question would multiply the
                # cost of the same bytes by the batch size.
                "questions_charged_once": len(asked),
                "input_tokens_shared_across_questions": True,
            },
            "judgments": judgements,
            "evidence": [{"artifact_id": b.artifact_id, "content_sha256": b.content_sha256,
                          "span_sha256": b.span_sha256, "locator_kind": b.locator_kind,
                          "locator": b.locator, "extracted_by": b.extracted_by}
                         for b in bindings],
            "judgment_only": True,
        }
        artifact_id = "art-" + hashlib.sha256(
            (run_id + ":" + operation_id + ":jev").encode("utf-8")).hexdigest()[:32]
        raw = {**payload, "provider_response": _safe_dump(response)}
        saved = store.save_artifact(
            store_root, run_id, artifact_id, provider=PROVIDER,
            origin_url=_origin(bindings, store_root), content=json.dumps(
                _jsonable(raw), ensure_ascii=False, sort_keys=True).encode("utf-8"),
            material_kind="decision_response")
        record_id = "rcd-" + hashlib.sha256(
            (artifact_id + ":observation").encode("utf-8")).hexdigest()[:32]
        store.put_candidate(
            store_root, run_id=run_id, record_id=record_id, stage="qualification",
            kind="observation", entity_key=operation_id,
            payload={**payload,
                     "material_kind": saved["material_kind"],
                     "artifact_id": artifact_id,
                     "derived_only": True,
                     "judgment_only": True},
            artifact_ids=[artifact_id] + [b.artifact_id for b in bindings])
        for binding in bindings:
            evidence_store.link_evidence(
                store_root, run_id, record_id, binding.artifact_id,
                content_sha256=binding.content_sha256, span_sha256=binding.span_sha256,
                locator_kind=binding.locator_kind, locator=binding.locator,
                material_kind=binding.material_kind)
        envelopes = tuple(
            evidence_store.record_envelope(
                store_root, run_id, binding.artifact_id, operation_id=operation_id,
                provider=PROVIDER,
                capability_id=PRIMITIVE_CAPABILITY[asked[0].primitive],
                extraction_method=binding.extracted_by,
                derived_refs=(artifact_id,) if not binding.is_image else (),
                caveats=("JUDGMENT_IS_NOT_SOURCE_EVIDENCE",))["envelope"]
            for binding in bindings)
        self._journal(store_root, run_id, operation_id, status="succeeded",
                      receipt_ref=artifact_id)
        return _result(OUTCOME_OK, payload=payload, artifacts=(saved,),
                       upstream_ref=upstream_ref, caveats=notes, envelopes=envelopes)

    def _scrub(self, text: str) -> str:
        """Remove our own key from any outgoing text. Cheap, and not a bet on the SDK."""
        return text.replace(self._api_key, REDACTED) if self._api_key else text

    def _journal(self, store_root: Path, run_id: str, operation_id: str, *,
                 status: str, reason: str = "", receipt_ref: str = "") -> None:
        try:
            append_event(store_root, run_id=run_id, operation_id=operation_id,
                         stage="qualification", kind="tool", status=status,
                         provider=PROVIDER, reason_code=reason.lower(),
                         receipt_ref=receipt_ref)
        except Exception:  # noqa: BLE001 - the result already reports the outcome
            pass


def _as_probabilities(value) -> dict | None:
    if not isinstance(value, dict) or not 1 <= len(value) <= 255:
        return None
    try:
        return {str(k)[:96]: float(v) for k, v in value.items()}
    except (TypeError, ValueError):
        return None


def _origin(bindings: list[EvidenceBinding], store_root: Path) -> str:
    """The URL the judgment was made from — the first evidence artifact's origin."""
    try:
        return _readback(store_root, bindings[0].run_id, bindings[0].artifact_id)["origin_url"]
    except ValueError:
        return "provider-response"


def _jsonable(value):
    """Tuples become lists so the stored JSON matches what a reader will parse back."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _safe_dump(response) -> dict:
    """The provider's own answer, as data. Never the credential, never live objects."""
    dump = getattr(response, "model_dump", None)
    if callable(dump):
        try:
            return _jsonable(dump())
        except Exception:  # noqa: BLE001 - a pydantic failure is not worth the call
            pass
    views = {}
    for name in ("model", "answers", "choices", "nouls", "scores", "boxes"):
        value = getattr(response, name, None)
        if value is not None:
            views[name] = value
    return _jsonable(views)


# ------------------------------------------------------------------- entry point
def execute(root: Path | str | None, *, run_id: str, operation_id: str,
            bindings: Sequence[EvidenceBinding], questions: Sequence[JevQuestion],
            api_key: str | None = None, model: str | None = None,
            base_url: str | None = None, timeout: float | None = None) -> dict:
    """Run one bounded judgment batch. See :class:`JevAdapter` for the contract."""
    return JevAdapter(api_key=api_key, root=root, model=model, base_url=base_url,
                      timeout=timeout)._run(
        root, run_id=run_id, operation_id=operation_id, bindings=bindings,
        questions=questions)


__all__ = [
    "ADAPTER_RESULT_FIELDS", "ALLOWED_PARAMS", "CONTRACT_CODE_FOR_JE",
    "CONTRACT_REASON_CODES",
    "EXTRACTOR", "EXTRACTOR_IMAGE", "AdapterResult", "EvidenceBinding",
    "JEV_INTERNAL_BUG_CODES", "JEV_OUTCOME_CODES", "JEV_REASON_CODES", "LEGACY_PROVIDER",
    "JEV_REFUSAL_CODES", "JevAdapter", "JevQuestion", "JevRefusal", "NOTES",
    "PRIMITIVE_CAPABILITY", "PROVIDER", "RESULT_FIELDS", "TRANSPORT",
    "bind_evidence", "box_question", "choice_question", "execute", "noul_question",
    "refusal_code", "score_question", "sdk_capabilities", "sdk_questions",
]

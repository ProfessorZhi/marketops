"""The shape every adapter must return, per stage.

An adapter is allowed to fail; it is not allowed to return something the metrics
cannot read. These checks are deliberately structural and cheap: they catch the
three ways provider output actually breaks a pipeline — a missing list, an item
that is not an object, and a field with the wrong type — without pretending to
judge quality. Quality is the gold label's job.
"""

from __future__ import annotations

from typing import Any

#: Required top-level keys and the type each must have.
CONTRACTS: dict[str, dict[str, type]] = {
    "brief": {
        "icp_attributes": list, "personas": list, "pain_points": list,
        "channels": list, "queries": list, "exclusions": list, "forbidden_claims": list,
    },
    "search": {"results": list},
    "fetch": {"final_url": str, "text": str, "links": list, "emails": list},
    "extraction": {"companies": list, "people": list, "emails": list, "gaps": list},
    "qualification": {
        "qualification_status": str, "icp_match": bool, "relevance": int,
        "reason": str, "supporting_evidence": list, "missing_evidence": list,
    },
    "identity": {"clusters": list, "relations": list},
    "evidence": {"checks": dict},
    "channel": {"ranked_channels": list, "acceptable_set": list, "rejected": list},
    "e2e": {"prospects": list, "gaps": list, "dossier": dict},
}

_SEARCH_ITEM = {"url": str, "title": str, "snippet": str, "rank": int}
_PERSON = {"name": str, "role": str, "company": str, "decision_maker": bool}
_EMAIL = {"value": str, "kind": str}
_PROSPECT = {"company": str, "status": str}


def _check_items(items: list, spec: dict[str, type], where: str, violations: list[str]) -> None:
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            violations.append(f"{where}[{index}] is not an object")
            continue
        for key, want in spec.items():
            if key not in item:
                violations.append(f"{where}[{index}] missing {key!r}")
            elif not isinstance(item[key], want):
                violations.append(f"{where}[{index}].{key} should be {want.__name__}")


def violations(stage: str, output: Any) -> list[str]:
    """Return every structural violation; empty list means the contract holds."""
    if not isinstance(output, dict):
        return [f"output is {type(output).__name__}, not an object"]
    contract = CONTRACTS.get(stage)
    if contract is None:
        return [f"unknown stage {stage!r}"]
    out: list[str] = []
    for key, want in contract.items():
        if key not in output:
            out.append(f"missing {key!r}")
        elif not isinstance(output[key], want):
            out.append(f"{key!r} should be {want.__name__}")
    if out:
        return out
    if stage == "search":
        _check_items(output["results"], _SEARCH_ITEM, "results", out)
    elif stage == "extraction":
        _check_items(output["people"], _PERSON, "people", out)
        _check_items(output["emails"], _EMAIL, "emails", out)
    elif stage == "e2e":
        _check_items(output["prospects"], _PROSPECT, "prospects", out)
    elif stage == "identity":
        for index, cluster in enumerate(output["clusters"]):
            if not isinstance(cluster, list) or not all(isinstance(x, str) for x in cluster):
                out.append(f"clusters[{index}] should be a list of record ids")
    return out


def is_valid(stage: str, output: Any) -> bool:
    return not violations(stage, output)

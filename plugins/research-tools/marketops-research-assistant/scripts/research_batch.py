#!/usr/bin/env python3
"""Claim a batch from the shared pool and harvest it in one call.

A worker conversation calls this repeatedly; each call leases targets, runs
the own-site harvest for each with bounded concurrency, records every
target's outcome back into the pool and returns a summary only. The Agent's
context sees counts, routes and reasons, not pages.
"""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor

import research_pool as pool
import research_site_harvest as site_harvest

__all__ = ["harvest_batch", "MAX_CONCURRENCY"]

MAX_CONCURRENCY = 8


def _organization_key(target: dict) -> str:
    return "org-" + target["target_key"].removeprefix("t-")


def harvest_batch(data_root, *, pool_id: str, worker_id: str, run_id: str, fetch,
                  count: int = 20, concurrency: int = 4, max_pages: int = 5,
                  lease_minutes: int = 30) -> dict:
    """Lease `count` targets, harvest each own site, complete them in the pool."""
    if type(concurrency) is not int or not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"BATCH_CONCURRENCY_INVALID: 1-{MAX_CONCURRENCY}")
    leased = pool.claim(data_root, pool_id=pool_id, worker_id=worker_id, count=count,
                        lease_minutes=lease_minutes)

    def work(target: dict) -> dict:
        org_key = _organization_key(target)
        if not target.get("website"):
            return {"target": target, "outcome": "hold", "reason": "NO_OFFICIAL_SITE_IN_POOL",
                    "org_key": org_key, "record_ids": [], "routes": []}
        op = "b-" + hashlib.sha256(f"{run_id}:{target['target_key']}:{target['attempts']}"
                                   .encode()).hexdigest()[:24]
        try:
            result = site_harvest.harvest(
                data_root, run_id=run_id, operation_id=op, organization_name=target["name"],
                organization_key=org_key, url=target["website"], fetch=fetch,
                max_pages=max_pages, write=True, check_admission=False)
        except ValueError as exc:
            return {"target": target, "outcome": "hold", "org_key": org_key, "record_ids": [],
                    "routes": [], "reason": str(exc).split(":", 1)[0]}
        reason = "" if result["routes"] and result.get("identity") else (
            result.get("next_action", "NO_RESULT").split(":", 1)[0])
        return {"target": target, "outcome": "done" if not reason else "hold",
                "reason": reason, "org_key": org_key,
                "record_ids": result.get("record_ids", []), "routes": result["routes"],
                "pages": len(result["pages"]),
                "obfuscated": result.get("obfuscated_emails_not_decoded", 0)}

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        outcomes = list(executor.map(work, leased["targets"]))

    # One gate read for the whole batch instead of one per organisation.
    decisions = {d["record_id"]: d for d in site_harvest.admission_for(
        data_root, run_id, [rid for item in outcomes for rid in item["record_ids"]])}
    summary, results = [], []
    for item in outcomes:
        routes = sum(1 for rid in item["record_ids"]
                     if decisions.get(rid, {}).get("admitted")
                     and decisions[rid]["kind"] == "contact_point")
        outcome, reason = item["outcome"], item["reason"]
        if outcome == "done" and routes == 0:
            outcome, reason = "hold", "ROUTES_NOT_ADMITTED"
        results.append({"target_key": item["target"]["target_key"], "outcome": outcome,
                        "reason": reason, "run_id": run_id, "organization_key": item["org_key"],
                        "routes": routes})
        summary.append({"name": item["target"]["name"], "outcome": outcome,
                        "routes": [f"{r['type']}:{r['value']}" for r in item["routes"]][:6],
                        "reason": reason or None})
    recorded = pool.complete(data_root, pool_id=pool_id, worker_id=worker_id,
                             results=results) if results else {"accepted": 0, "refused": []}
    done = sum(1 for r in results if r["outcome"] == "done")
    reasons: dict[str, int] = {}
    for r in results:
        if r["reason"]:
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    return {"pool_id": pool_id, "worker_id": worker_id, "run_id": run_id,
            "claimed": leased["claimed"], "done": done, "hold": len(results) - done,
            "routes": sum(r["routes"] for r in results), "hold_reasons": reasons,
            "recorded": recorded["accepted"], "refused": recorded["refused"],
            "targets": summary,
            "next_action": ("CLAIM_NEXT_BATCH" if leased["claimed"] else
                            "POOL_EMPTY_OR_ALL_LEASED: check marketops_pool_status")}

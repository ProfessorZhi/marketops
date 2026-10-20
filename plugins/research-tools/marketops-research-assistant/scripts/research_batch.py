#!/usr/bin/env python3
"""Claim a batch from the shared pool and harvest it in one call.

A worker conversation calls this repeatedly; each call leases targets, runs
the own-site harvest for each with bounded concurrency, records every
target's outcome back into the pool and returns a summary only. The Agent's
context sees counts, routes and reasons, not pages.
"""
from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, wait
from urllib.parse import urlsplit

import research_pool as pool
import research_site_harvest as site_harvest

__all__ = ["harvest_batch", "MAX_CONCURRENCY"]

MAX_CONCURRENCY = 8
#: Hosts end a tool call at 60 s (Claude Code's MCP default). Start no new site
#: after this, return what finished and give unstarted targets back.
DEADLINE_SECONDS = 40


def _route_line(route: dict) -> str:
    line = f"{route['type']}:{route['value']}"
    if route.get("role"):
        confidence = route.get("confidence")
        line += f" [{route['role']}" + (f" {confidence:.2f}]" if isinstance(confidence, (int, float)) else "]")
    return line


def _record_dispositions(data_root, run_id: str, results: list[dict]) -> None:
    """Every organisation saved in the Run gets its disposition with the reason.

    The Agent may supersede one (e.g. after a role search); without these, a
    handoff reports dozens of organisations as undecided.
    """
    items = []
    for result in results:
        org_key = result["organization_key"]
        if not site_harvest._exists(data_root, run_id, "entity_key", org_key, kind="organization"):
            continue
        record_id = "review-" + org_key + "-batch"
        if site_harvest._exists(data_root, run_id, "record_id", record_id):
            continue
        done = result["outcome"] == "done"
        items.append({"record_id": record_id, "stage": "review", "kind": "review",
                      "entity_key": "review-" + org_key, "artifact_ids": [],
                      "facts": {"review_type": "target_disposition", "target_kind": "organization",
                                "target_entity_key": org_key,
                                "disposition": "RESEARCHED_CANDIDATE" if done else "HOLD",
                                "reason": ("Own-site harvest recorded public routes; grade them."
                                           if done else "Own-site harvest: " + result["reason"])}})
    for start in range(0, len(items), 20):
        site_harvest.store.put_candidates_batch(data_root, run_id, items[start:start + 20])


def _organization_key(target: dict) -> str:
    return "org-" + target["target_key"].removeprefix("t-")


def harvest_batch(data_root, *, pool_id: str, worker_id: str, run_id: str, fetch,
                  count: int = 20, concurrency: int = 4, max_pages: int = 5,
                  lease_minutes: int = 10, grade=None,
                  deadline_seconds: float = DEADLINE_SECONDS) -> dict:
    """Lease `count` targets, harvest each own site, complete them in the pool.

    `grade(route)` (Jev, when the caller names the wanted roles) returns
    {"role", "confidence"} for each email route; it ranks, it is not evidence.
    """
    if type(concurrency) is not int or not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"BATCH_CONCURRENCY_INVALID: 1-{MAX_CONCURRENCY}")
    leased = pool.claim(data_root, pool_id=pool_id, worker_id=worker_id, count=count,
                        lease_minutes=lease_minutes)
    deadline = time.monotonic() + deadline_seconds

    def work(target: dict) -> dict | None:
        if time.monotonic() > deadline:
            return None  # not started: handed back below
        org_key = _organization_key(target)
        if not target.get("website"):
            return {"target": target, "outcome": "hold", "reason": "NO_OFFICIAL_SITE_IN_POOL",
                    "org_key": org_key, "record_ids": [], "routes": []}
        if site_harvest.delivery.shared_host(urlsplit(target["website"]).hostname or ""):
            # A Facebook page or a directory entry is not the organisation's own site;
            # find its real site (Serper) or record the HOLD.
            return {"target": target, "outcome": "hold", "org_key": org_key, "record_ids": [],
                    "routes": [], "reason": "WEBSITE_IS_SOCIAL_OR_DIRECTORY"}
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
        if grade is not None:
            for route in result["routes"]:
                if route["type"] == "email":
                    try:
                        route.update(grade(route))
                    except ValueError as exc:
                        route["role"] = "ungraded:" + str(exc).split(":", 1)[0]
        reason = "" if result["routes"] and result.get("identity") else (
            result.get("next_action", "NO_RESULT").split(":", 1)[0])
        return {"target": target, "outcome": "done" if not reason else "hold",
                "reason": reason, "org_key": org_key,
                "record_ids": result.get("record_ids", []), "routes": result["routes"],
                "pages": len(result["pages"]),
                "obfuscated": result.get("obfuscated_emails_not_decoded", 0)}

    executor = ThreadPoolExecutor(max_workers=concurrency)
    futures = {executor.submit(work, target): target for target in leased["targets"]}
    wait(futures, timeout=max(1.0, deadline - time.monotonic() + 5))
    executor.shutdown(wait=False, cancel_futures=True)
    outcomes, unstarted, running = [], [], 0
    for future, target in futures.items():
        if future.cancelled() or (future.done() and future.result() is None):
            unstarted.append(target["target_key"])
        elif future.done():
            outcomes.append(future.result())
        else:
            running += 1
    # A site still loading keeps its lease; it finishes in the background and
    # returns to the pool when the lease ends, without a duplicate record.
    released = pool.release(data_root, pool_id=pool_id, worker_id=worker_id,
                            target_keys=unstarted)

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
                        "routes": [_route_line(r) for r in item["routes"]][:6],
                        "reason": reason or None})
    _record_dispositions(data_root, run_id, results)
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
            "targets": summary, "returned_unstarted": released,
            "still_loading": running,
            "next_action": ("CLAIM_NEXT_BATCH" if leased["claimed"] else
                            "POOL_EMPTY_OR_ALL_LEASED: check marketops_pool_status")}

#!/usr/bin/env python3
"""Bounded 5-provider Lite Research MCP. Candidate-only, local, human-initiated."""
from __future__ import annotations

import functools
import hashlib
import importlib.util
import inspect
import json
import os
import sqlite3
import sys
from pathlib import Path

import dual_search_mcp
import research_config
import research_usage_ledger as usage_ledger
import research_lite_store as store
import research_provider_extensions as extra
import research_delivery as delivery
import research_workflow as workflow
import research_evidence as evidence
import research_provider_policy as provider_policy
from research_event_journal import (_root, append_event, append_note, draft_experience,
                                    summarize_events)
from research_experience_summary import refresh_quietly as refresh_summary


def next_step(run_id, limit=10, previous_snapshot_id="", after_attempt=False):
    """Bounded tasks; an unchanged snapshot after work triggers a stop/switch hint."""
    return workflow.next_step(root(), run_id, limit, previous_snapshot_id, after_attempt)


def status(run_id, limit=20, cursor="", kind="", entity_key=""):
    """Persisted candidates through the workflow page contract.

    A page fixes a row high-water mark; later appends land in a new query, and a
    cursor belongs to its Run and filters.
    """
    return workflow.page(root(), run_id, limit, cursor, kind, entity_key)


def provider_events(run_id: str) -> dict:
    """What this Lite MCP actually invoked; never external official MCP traffic."""
    summary = summarize_events(root(), run_id, limit=20)
    latest = None
    for event in summary["events"]:
        name = event.get("provider")
        if event.get("kind") != "tool" or name not in provider_policy.LITE_OPERATION_CHANNELS:
            continue
        info = provider_policy.invocation_receipt(
            name,
            # Historical journal schema records the generic Jev tool but not
            # its Choice/Noul/Score primitive. Never reconstruct "Choice" as
            # a fact when a different primitive may have run.
            primitive="unknown" if name == "jev_choice" else "choice",
            reservation="NOT_RECONCILED_FROM_EVENT")
        event["invocation_route"] = {
            "upstream": info["marketops_to_provider"],
            "auth_method": info["authentication"]["method"],
            "billing_unit": info["billing"]["unit"],
        }
        latest = {"operation_id": event.get("operation_id"),
                  "status": event.get("status"),
                  "time": event.get("recorded_at"),
                  "invocation": info}
    return {**summary, "latest_lite_provider_call": latest,
            "external_official_mcp_activity": "NOT_MONITORED_BY_LITE"}


def setup_status():
    """Only two mandatory paths. Vendor account onboarding remains optional."""
    state = research_config.status(redact=True)
    return {**state, "provider_onboarding": {
        "state": "OPTIONAL_AFTER_LOCAL_SETUP",
        "guide": "references/CUSTOMER_QUICKSTART.md",
        "connection_catalog": "references/PROVIDER_CONNECTIONS.json",
        "local_offline_command": "python scripts/provider_onboarding.py --client claude",
        "provider_accounts_authenticated": "NOT_VERIFIED",
        "official_mcp_sessions_verified": False,
        "network_or_provider_fee_incurred": False,
    }}


def setup_configure(data_root, experience_file, confirm=False):
    """Persist the two paths the user answered once at first use."""
    return research_config.configure(data_root, experience_file, confirm=confirm)


def note(run_id, note_id, stage, category, problem, action_taken, observed_result,
         hypothesis="", evidence_ref=""):
    """Record a real bottleneck and refresh the user's one summary file."""
    r = root()
    recorded = append_note(r, run_id=run_id, note_id=note_id, stage=stage,
                           category=category, problem=problem, action_taken=action_taken,
                           observed_result=observed_result, hypothesis=hypothesis,
                           evidence_ref=evidence_ref)
    return {"note": recorded, "summary": refresh_summary(data_root=r)}


def root():
    return _root(os.environ.get("MARKETOPS_RESEARCH_DATA_ROOT"))


def configured(name):
    value = os.environ.get(name, "").strip()
    return bool(value) and not value.startswith("$" + "{")


def _local_browser_cache_hint() -> dict:
    """Cheap preflight HINT only; neither SDK presence nor cache proves launch."""
    explicit = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if explicit == "0":
        return {"status": "SYSTEM_BROWSER_MODE_NOT_PROBED", "verified": False}
    if explicit.startswith("$" + "{"):
        return {"status": "BROWSER_PATH_PLACEHOLDER_UNRESOLVED", "verified": False}
    if explicit:
        folder = Path(explicit).expanduser()
        source = "operator_environment"
    elif sys.platform == "win32":
        folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"
        source = "platform_default"
    elif sys.platform == "darwin":
        folder = Path.home() / "Library" / "Caches" / "ms-playwright"
        source = "platform_default"
    else:
        folder = Path.home() / ".cache" / "ms-playwright"
        source = "platform_default"
    try:
        present = folder.is_dir() and any(
            child.is_dir() and child.name.startswith(("chromium-", "chromium_headless_shell-"))
            for child in folder.iterdir()
        )
    except OSError:
        present = False
    return {"status": ("CHROMIUM_CACHE_PRESENT_NOT_LAUNCH_VERIFIED" if present
                       else "CHROMIUM_CACHE_MISSING_OR_INCOMPLETE"),
            "verified": False, "source": source}


def _ledger_usage(filename: str, table: str) -> int | None:
    """Read-only budget count; missing ledger=0, corrupt/unconfigured=unknown."""
    try:
        folder = research_config.data_root() / "state"
    except (ValueError, OSError):
        return None
    path = folder / filename
    if not path.is_file():
        return 0
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            return int(db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0])
    except (sqlite3.Error, OSError):
        return None


def _capacity(limit: int, used: int | None) -> dict:
    return {"limit": limit, "reserved": used,
            "remaining": max(0, limit - used) if used is not None else None,
            "scope": "per_run_plus_per_project",
            "metric": "attempts_not_currency"}


# --- the converged read model ---------------------------------------------
# One number for a run's metered operations, across every transport and every
# provider. Before this, a run's spending was only visible per ledger file, so
# "what did this run cost" had as many answers as there were transports.


def run_usage(run_id: str, data_root=None) -> dict:
    """One run's metered operations, read from the single authoritative ledger.

    This is the *read* side of the one-ledger rule: a run that performed a search
    over REST and a fetch over the embedded official MCP has spent two metered
    operations, and the answer to "how many" must not depend on which adapter is
    asked. It reads the operations table rather than a counter, so an operation
    that is still held or still unknown is visible as such rather than folded into
    a total.
    """
    resolved = Path(data_root) if data_root is not None else root()
    project = usage_ledger.project_for_root(resolved)
    ledger = usage_ledger.for_root(resolved)
    rows = ledger.operations(project=project, run_id=run_id)
    by_provider: dict[str, int] = {}
    by_transport: dict[str, int] = {}
    by_state: dict[str, int] = {}
    held_micros = 0
    for operation in rows:
        by_provider[operation.provider] = by_provider.get(operation.provider, 0) + 1
        by_transport[operation.transport] = by_transport.get(operation.transport, 0) + 1
        by_state[operation.charge_state] = by_state.get(operation.charge_state, 0) + 1
        held_micros += operation.held_micros
    return {"run_id": run_id, "project": project,
            "metered_operations": len(rows), "held_micros": held_micros,
            "by_provider": by_provider, "by_transport": by_transport,
            "by_state": by_state, "root_read": str(resolved)}


def legacy_receipts(data_root=None) -> list:
    """Every historical reservation row, from both legacy tables, verbatim.

    Contract §9: a migration preserves audit history and silently deletes nothing.
    The two legacy stores are *read* here, never rewritten: ``migration.plan`` is
    the module that reads them without touching them, and it is the same code the
    real migration uses, so a report cannot disagree with what a migration would
    find.
    """
    resolved = Path(data_root) if data_root is not None else root()
    rows: list[dict] = []
    stores = (("dual-search-ledger.sqlite3", "search_attempts"),
              ("provider-reservations.sqlite3", "provider_reservations"))
    for filename, table in stores:
        path = resolved / "state" / filename
        if not path.is_file():
            continue
        try:
            # Read-only URI, so surveying the history cannot create the file it
            # is surveying or leave a -wal beside a customer database.
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5) as db:
                present = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if table not in present:
                    continue
                columns = [row[1] for row in db.execute("PRAGMA table_info(" + table + ")")]
                for row in db.execute("SELECT * FROM " + table):
                    rows.append({**dict(zip(columns, row)), "legacy_table": table,
                                 "legacy_store": filename})
        except (sqlite3.Error, OSError):
            # A store we cannot read is reported by its absence, not by a crash
            # in a read model whose whole job is to be safe to call.
            continue
    return rows


def reset_or_roll_period(data_root=None) -> dict:
    """Report the current accounting period for each window.

    There is no counter to reset. A ceiling is stored per ``(scope, window)`` and
    its period key is derived from the clock, so a new period begins by time
    rather than by a verb — and a verb that "reset" the previous period's numbers
    would be a way to erase spending. What a caller asking "has the period rolled"
    needs is the key, so that is what this returns.
    """
    resolved = Path(data_root) if data_root is not None else root()
    project = usage_ledger.project_for_root(resolved)
    ledger = usage_ledger.for_root(resolved)
    windows = {}
    for scope_kind, scope_key in (("project", project),):
        for usage in ledger.usage(scope_kind, scope_key):
            windows[usage.window] = {"period_key": usage.period_key,
                                     "calls_used": usage.calls_used,
                                     "spent_micros": usage.spent_micros,
                                     "held_micros": usage.held_micros}
    return {"project": project, "windows": windows,
            "note": "ceilings roll by the clock; there is no counter to reset"}



def _access_status(*, network: bool, credential: bool = True, installed: bool = True,
                   remaining: int | None = None) -> str:
    if not network:
        return "DISABLED_BY_PROJECT_OR_PROCESS_POLICY"
    if not credential:
        return "CREDENTIAL_NOT_CONFIGURED"
    if not installed:
        return "LOCAL_DEPENDENCY_NOT_INSTALLED"
    if remaining is None:
        return "BUDGET_STATE_UNKNOWN"
    if remaining == 0:
        return "ATTEMPT_LIMIT_EXHAUSTED"
    return "ELIGIBLE_BUT_AUTH_AND_CREDITS_UNVERIFIED"


def _external_inventory(observed_tools: list[str] | None) -> dict:
    """A caller-supplied host tool list is a hint, never account authorization."""
    if observed_tools is None:
        return {"source": "HOST_TOOL_INVENTORY_NOT_SUPPLIED",
                "tinyfish_agent": "UNKNOWN", "tinyfish_profiles": "UNKNOWN",
                "tinyfish_wallet": "UNKNOWN", "tinyfish_search": "UNKNOWN",
                "tinyfish_fetch": "UNKNOWN", "tinyfish_browser": "UNKNOWN"}
    if (not isinstance(observed_tools, list) or len(observed_tools) > 40
            or any(not isinstance(t, str) or len(t) > 160 for t in observed_tools)):
        raise ValueError("INVALID_HOST_OBSERVED_TOOLS")
    unique = set(observed_tools)
    def found(tool: str) -> str:
        return ("HOST_TOOL_REPORTED_NOT_AUTH_OR_CREDIT_VERIFIED" if
                any(t.endswith("__" + tool) and "TinyFish" in t for t in unique)
                else "NOT_REPORTED_BY_HOST_NOT_PROOF_OF_ABSENCE")
    return {"source": "CALLER_REPORTED_TOOL_NAMES_NOT_SERVER_VERIFIED",
            "tinyfish_agent": found("run_web_automation"),
            "tinyfish_profiles": found("list_profiles"),
            "tinyfish_wallet": found("get_wallet"),
            "tinyfish_search": found("search"),
            "tinyfish_fetch": found("fetch_content"),
            "tinyfish_browser": found("create_browser_session")}


def capabilities(host_observed_tools: list[str] | None = None,
                 task_kind: str | None = None,
                 tinyfish_wallet_snapshot: dict | None = None) -> dict:
    """One cheap local view; accounts and external apps require a separate handshake.

    The same five adapters can be installed in different client environments
    with different keys, policies, budgets, SDKs, wallet balances and sessions.
    """
    search = dual_search_mcp._configuration()
    budget_search = _capacity(search["max_total_reserved_calls"],
                              _ledger_usage("dual-search-ledger.sqlite3", "search_attempts"))
    budget_other = _capacity(extra._limit(),
                             _ledger_usage("provider-reservations.sqlite3", "provider_reservations"))
    search_on = bool(search["network_enabled"])
    other_on = os.environ.get("MARKETOPS_RESEARCH_ALLOW_NETWORK") == "1"
    browser_hint = _local_browser_cache_hint()
    sdk_crawl = importlib.util.find_spec("crawl4ai") is not None
    sdk_jev = importlib.util.find_spec("typesafe_sdk") is not None

    def spec(code: str, *, search_budget: bool, key: bool = True,
             installed: bool = True) -> dict:
        capacity = budget_search if search_budget else budget_other
        return {"implemented_in_lite": True, "capability": code,
                "status": _access_status(network=search_on if search_budget else other_on,
                                         credential=key, installed=installed,
                                         remaining=capacity["remaining"]),
                "authorization_verified": False, "credits_verified": False,
                "cost_class": "PROVIDER_METERED_OR_UNVERIFIED"}

    external = _external_inventory(host_observed_tools)
    capability_matrix = {
        "serper.web_search": spec("serper.web_search", search_budget=True,
                                   key=bool(search["serper"]["configured"])),
        "serper.places": spec("serper.places", search_budget=True,
                               key=bool(search["serper"]["configured"])),
        "serpapi.google_search": spec("serpapi.google_search", search_budget=True,
                                      key=bool(search["serpapi"]["configured"])),
        "serpapi.google_maps": spec("serpapi.google_maps", search_budget=True,
                                    key=bool(search["serpapi"]["configured"])),
        "crawl4ai.local": {**spec("crawl4ai.local", search_budget=False,
                                  installed=sdk_crawl),
                           "browser_cache_hint": browser_hint,
                           "cost_class": "NO_PROVIDER_FEE_LOCAL_COMPUTE"},
        "crawl4ai.cloud": spec("crawl4ai.cloud", search_budget=False,
                                key=configured("CRAWL4AI_API_KEY")),
        "tinyfish.search": spec("tinyfish.search", search_budget=False,
                                 key=configured("TINYFISH_API_KEY")),
        "tinyfish.fetch": spec("tinyfish.fetch", search_budget=False,
                                key=configured("TINYFISH_API_KEY")),
        "jev.choice": spec("jev.choice", search_budget=False,
                            key=configured("TYPESAFE_API_KEY"), installed=sdk_jev),
        "tinyfish.browser_agent": {"implemented_in_lite": True,
                                   "status": "EMBEDDED_MCP_PAID_AGENT_STRICT_GATED",
                                   "authorization_verified": False, "credits_verified": False,
                                   "requires": "embedded_official_MCP_credential_wallet_hard_limits_and_explicit_paid_permission"},
        "tinyfish.browser_context_profile": {
            "implemented_in_lite": False, "status": external["tinyfish_profiles"],
            "authentication_verified": False,
            "requires": "user_owned_external_profile_id_and_site_approval"},
        "jev.score": spec("jev.score", search_budget=False,
                          key=configured("TYPESAFE_API_KEY"), installed=sdk_jev),
        "jev.noul": spec("jev.noul", search_budget=False,
                         key=configured("TYPESAFE_API_KEY"), installed=sdk_jev),
    }
    for category in ("images", "news", "videos", "shopping", "scholar",
                     "patents", "autocomplete"):
        for vendor in ("serper", "serpapi"):
            item = vendor + "." + category
            capability_matrix[item] = spec(
                item, search_budget=True,
                key=bool(search[vendor]["configured"]))
    observed_wallet = provider_policy.tinyfish_wallet_observation(tinyfish_wallet_snapshot)
    routing = (provider_policy.propose_route(
        task_kind, capability_matrix, external, tinyfish_wallet=observed_wallet)
        if task_kind else None)
    return {
        "product": "MarketOps Lite Research MCP",
        "version": "0.4.0-candidate",
        "providers": {
            "serpapi": {"transport": "REST through local MCP", **search["serpapi"]},
            "serper": {"transport": "REST through local MCP", **search["serper"]},
            "crawl4ai": {"local_installed": sdk_crawl, "browser_cache_hint": browser_hint,
                         "cloud_key": configured("CRAWL4AI_API_KEY"),
                         "modes": ["local", "cloud"]},
            "tinyfish": {"key": configured("TINYFISH_API_KEY"),
                         "implemented": ["search", "fetch"],
                         "agent": "official TinyFish host connector, not in local Lite MCP"},
            "jev": {"key": configured("TYPESAFE_API_KEY"), "sdk": sdk_jev,
                    "implemented": ["choice", "noul", "score"]},
        },
        "capability_matrix": capability_matrix,
        "external_host": external,
        "tinyfish_wallet_observation": observed_wallet,
        # The compact capability view is read once per Run. Detailed
        # per-operation receipts appear only when that operation is called.
        "lite_invocation_channels": {
            operation: {
                "upstream": item["marketops_to_provider"],
                "auth": item["authentication"]["method"],
                "credential_delivery": item["authentication"]["credential_delivery"],
                "billing_unit": item["billing"]["unit"],
                "charge_verified": False,
            }
            for operation in provider_policy.LITE_OPERATION_CHANNELS
            for item in (provider_policy.invocation_receipt(operation),)
        },
        # Host-direct vendor MCPs are distinct from the embedded clients.
        # Crawl4AI remains optional/self-hosted; TinyFish/SerpApi use the
        # in-process official MCP client in this same Lite server.
        "official_mcp_channels": {
            vendor: provider_policy.official_mcp_route(vendor)
            for vendor in ("crawl4ai",)
        },
        "embedded_official_mcp": provider_policy.embedded_official_mcp_capabilities(),
        "connection_state": "EMBEDDED_TINYFISH_SERPAPI_MCP_CLIENT_SHIPPED_AUTH_NOT_VERIFIED",
        "routing_advice": routing,
        "budgets": {"search": budget_search, "provider": budget_other},
        "limits": {"search_on": search_on, "search_calls": search["max_total_reserved_calls"],
                   "other_network_on": other_on, "other_calls": extra._limit()},
        "selection_boundary": "NO_AUTOMATIC_PAID_FALLBACK_OR_LOGIN",
        "business_store": "local research-lite.sqlite3, candidate-only",
        "no_auto_outreach": True,
    }


def _valid_brief(run_id: str, brief: dict) -> None:
    """Validate the existing generic contract without depending on a scene."""
    if type(brief) is not dict or brief.get("record_type") != "research_brief":
        raise ValueError("RESEARCH_BRIEF_INVALID")
    if "supersedes" in brief:
        raise ValueError("BRIEF_REVISION_REQUIRES_MARKETOPS_PROPOSE")
    synthetic = {"record_id": "brief-validation", "stage": "qualification",
                 "kind": "observation", "status": "candidate_unverified",
                 "entity_key": "research-brief", "artifact_ids": [], "data": brief}
    result = workflow.assess_records(run_id, [synthetic])
    if result["status"] in {"BRIEF_REQUIRED", "BRIEF_CONFLICT", "BRIEF_INVALID",
                            "BRIEF_UNIT_REVIEW_REQUIRED"}:
        raise ValueError("RESEARCH_BRIEF_INVALID: " + result["status"])
    if evidence.audit_records([synthetic], {}, result_limit=0)["invalid_brief_evidence_requirements"]:
        raise ValueError("RESEARCH_BRIEF_EVIDENCE_REQUIREMENTS_INVALID")


def _require_brief_for_network(root_path, run_id: str) -> None:
    """Do not spend an approved attempt when the project objective is undefined."""
    assessment = workflow.next_step(root_path, run_id, limit=1)
    if assessment["status"] in {"BRIEF_REQUIRED", "BRIEF_CONFLICT",
                                 "BRIEF_INVALID", "BRIEF_UNIT_REVIEW_REQUIRED"}:
        raise ValueError("RESEARCH_BRIEF_REQUIRED_BEFORE_NETWORK: " + assessment["status"])


def start(run_id, scope, objective, rule_version, brief=None):
    """Start/resume one Run and atomically stage its validated structured goal."""
    r = root()
    if brief is not None:
        _valid_brief(run_id, brief)
    result = store.start_run(r, run_id, scope, objective, rule_version)
    brief_receipt = None
    if brief is not None:
        brief_receipt = store.put_candidate(
            r, run_id, "brief-" + hashlib.sha256(run_id.encode()).hexdigest()[:24],
            "qualification", "observation", "research-brief", brief, [])
    event = append_event(r, run_id=run_id, operation_id="run-" + run_id,
                         stage="intake", kind="run", status="succeeded",
                         provider="local")
    next_state = workflow.next_step(r, run_id, limit=1)
    return {**result, "event": event,
            "brief": brief_receipt or {"status": "BRIEF_REQUIRED"},
            "next_status": next_state["status"],
            "next_skill": next_state["recommended_skill"]}


def _require_s0_for_provider(what):
    """The single S0 gate in front of every host-visible provider entry.

    This module holds the product's only two provider-plane calls —
    `extra.invoke` in `invoke` and `dual_search_mcp.search` in `search` — so
    checking here covers every MCP tool and every alias that funnels into them.
    The check is deliberately above the planes rather than inside them: a guard
    inside `research_provider_extensions.invoke` is one that any caller of the
    plane inherits by accident and that a test or a lane can replace together
    with the plane.

    Evidence is read from the data root by `research_wiring.s0_provider_entry`;
    it is not a parameter here, so no tool can be handed its own S0 result.
    """
    import research_wiring

    allowed, reason_code, failed = research_wiring.s0_provider_entry(root())
    if allowed:
        return
    detail = ",".join(failed) if failed else "S0_NOT_EVALUATED"
    # The repository's declared refusal channel: `_guarded` renders this as a
    # BLOCKED tool response with the code intact, not as a server error.
    raise ValueError(f"{reason_code}: S0 gate refused {what} ({detail})")


def search(provider, query, country, language, run_id, operation_id, mode="web", limit=10):
    r = root()
    store.list_run(r, run_id, limit=1)
    # Same order as `invoke`: local preconditions, then the deployment gate.
    _require_brief_for_network(r, run_id)
    _require_s0_for_provider(f"search provider {provider!r}")
    result = dual_search_mcp.search(
        provider, query, country, language, run_id, operation_id, mode, limit)
    record_ids = []
    try:
        for i, item in enumerate(result["results"]):
            record_id = "src-" + hashlib.sha256(
                (run_id + operation_id + str(i) +
                 json.dumps(item, sort_keys=True)).encode("utf-8")).hexdigest()[:32]
            rec = store.put_candidate(
                r, run_id, record_id, "discovery", "source", record_id,
                {"provider": provider, "operation_id": operation_id,
                 "url": item.get("url"), "title": item.get("title"),
                 "place_id": item.get("place_id"), "address": item.get("address"),
                 "snippet": item.get("snippet"), "discovery_only": True})
            record_ids.append(rec["record_id"])
    except Exception:
        append_event(r, run_id=run_id, operation_id=operation_id, stage="discovery",
                     kind="handoff", status="hold", provider=provider,
                     reason_code="search_receipt_not_fully_staged")
        raise ValueError("SEARCH_RECEIPT_SAVED_BUT_RECORD_STAGING_FAILED") from None
    return {**result, "stored_record_ids": record_ids,
            "invocation": provider_policy.invocation_receipt(
                provider, reservation="ONE_ATTEMPT_RESERVED_NOT_BILL_VERIFIED")}


def invoke(action, run_id, operation_id, url="", query="", document="",
           question="", options=None, primitive="choice", mcp_params=None,
           paid_limit_usd="", ledger_grant=""):
    # Local preconditions first, then the deployment gate. `_require_brief_for_network`
    # reads the store and opens nothing, so it is not an egress path; its refusal is
    # also the more specific one ("this run has no brief") and running S0 ahead of it
    # would replace that answer with "this deployment may not spend", which is true
    # but useless to the caller. The S0 check still precedes every provider plane.
    _require_brief_for_network(root(), run_id)
    # Only a real provider action is gated. An unknown action never reaches a
    # provider, and refusing it here would turn "you called something that does
    # not exist" into "this deployment may not spend", which is a different and
    # less useful answer.
    if action in extra.ALLOWED_ACTIONS:
        _require_s0_for_provider(f"provider action {action!r}")
    return extra.invoke(action, run_id=run_id, operation_id=operation_id,
                        url=url, query=query, document=document,
                        question=question, options=options, primitive=primitive,
                        mcp_params=mcp_params, paid_limit_usd=paid_limit_usd,
                        ledger_grant=ledger_grant)


def propose(run_id, record_id, stage, kind, entity_key, facts, artifact_ids=None):
    r = root()
    result = store.put_candidate(r, run_id, record_id, stage, kind,
                                 entity_key, facts, artifact_ids or [])
    append_event(r, run_id=run_id, operation_id=record_id, stage=stage,
                 kind="entity", status="succeeded", provider="local")
    return result


def propose_batch(run_id, items):
    """Atomic append-only candidates, at most 20 per transaction/tool call."""
    r = root()
    result = store.put_candidates_batch(r, run_id, items)
    operation_id = "batch-" + hashlib.sha256(
        json.dumps([item["record_id"] for item in items], sort_keys=True).encode()).hexdigest()[:24]
    append_event(r, run_id=run_id, operation_id=operation_id,
                 stage="entities", kind="entity", status="succeeded", provider="local")
    return result


def material_excerpt(run_id, artifact_ids, queries=None):
    return evidence.excerpts(root(), run_id, artifact_ids, queries)


def evidence_audit(run_id, limit=12):
    r = root()
    _, records, artifacts = delivery._read_snapshot(r, run_id)
    return evidence.audit_records(records, {a["artifact_id"]: a for a in artifacts},
                                  result_limit=limit)


def export(run_id, output_path):
    r = root()
    output = Path(output_path).expanduser().resolve()
    if not output.is_relative_to(r / "exports"):
        raise ValueError("EXPORT_MUST_BE_WITHIN_CUSTOMER_DATA_ROOT_EXPORTS")
    result = store.export_csv(r, run_id, output)
    operation_id = "exp-" + hashlib.sha256(str(output.resolve()).encode()).hexdigest()[:24]
    append_event(r, run_id=run_id, operation_id=operation_id, stage="export",
                 kind="export", status="succeeded", provider="local")
    return result


def evidence_workbook(run_id, delivery_id):
    """The evidence chain as one five-sheet XLSX, bound to this tree's real ledger.

    This is the controlled local research evidence path for W-F's workbook: the
    data root is this deployment's own, and the ledger is the shipped
    `UsageLedger` for it — not a stand-in. The receipts sheet copies the ledger's
    charge states verbatim, so an UNKNOWN charge stays UNKNOWN with its actual
    amount empty; nothing here sums or promotes a state.

    No reviews are supplied. That is not an omission: a claim is `APPROVED` only
    after a recorded human decision, and with none recorded every claim reports
    as blocked rather than defaulting to approval.
    """
    r = root()
    ledger = usage_ledger.for_root(r)
    project = usage_ledger.project_for_root(r)
    return delivery.write_evidence_workbook(
        r, run_id, delivery_id, ledger=ledger, project=project)


def handoff(run_id, operation_id):
    """One audited workbook snapshot plus experience artifacts and an honest receipt.

    Counts come from the workbook manifest, not a preceding status query. A child
    delivery's partial failure may never disappear at this orchestration boundary.
    Final experience refresh happens after the handoff event, including failures.
    """
    r = root()
    store._id(run_id, "run_id")
    store._id(operation_id, "operation_id")
    # Run metadata is immutable; counts are not. Use this read only to validate
    # existence and obtain scope/policy for the experience draft.
    head = workflow.page(r, run_id, 1)
    experiences = r / "experience" / run_id
    experiences.mkdir(parents=True, exist_ok=True)
    experience_path = experiences / (operation_id + "-experience.json")
    try:
        workbook = delivery.write_delivery(r, run_id, operation_id)
    except Exception as exc:
        try:
            append_event(r, run_id=run_id, operation_id=operation_id,
                         stage="handoff", kind="handoff", status="hold",
                         provider="local", reason_code="handoff_delivery_failed")
        except Exception:
            pass  # Do not replace the real export refusal with a journal error.
        raise ValueError(f"HANDOFF_PARTIAL_OR_FAILED: {type(exc).__name__}: {exc}") from None

    result = {
        "status": "CANDIDATE_HANDOFF_NOT_BUSINESS_VERIFIED",
        "run_id": run_id,
        "records": workbook["input_record_count"],
        "artifacts": workbook["saved_artifact_count"],
        "workbook_xlsx": workbook["xlsx_path"],
        "workbook_directory": workbook["directory"],
        "candidate_csv": str(Path(workbook["directory"]) / "候选数据审计.csv"),
        "candidate_csv_schema": "audit_csv_from_this_workbook_not_a_second_snapshot",
        "goal_assessment": workbook.get("goal_assessment"),
        "goal_gap_record": workbook.get("goal_gap_record"),
        "evidence_note": workbook.get("evidence_note"),
        "workbook_delivery_completeness": workbook.get("delivery_completeness"),
        "workbook_experience_refresh": workbook.get("experience_refresh"),
        "unresolved_business_review": True,
        "warning": "Candidate workbook plus its audit CSV; NOT ready contacts or a formal A/B/C/D route.",
    }
    reasons = list(workbook.get("partial_reasons") or [])
    if workbook.get("delivery_completeness") == "PARTIAL" and not reasons:
        reasons.append("WORKBOOK_DELIVERY_PARTIAL")
    if (workbook.get("goal_gap_record") or {}).get("status") == "FAILED":
        if "GOAL_GAP_RECORD_FAILED" not in reasons:
            reasons.append("GOAL_GAP_RECORD_FAILED")
    initial_refresh = workbook.get("experience_refresh") or {}
    if initial_refresh.get("status") != "REFRESHED":
        reason = "EXPERIENCE_REFRESH_" + str(initial_refresh.get("reason_code")
                                             or initial_refresh.get("status") or "FAILED")
        if reason not in reasons:
            reasons.append(reason)
    try:
        experience_result = draft_experience(
            r, run_id=run_id, scope=head["scope"],
            rule_version=head["rule_version"], skill_version="0.4.0",
            output=experience_path)
        result["experience_candidate"] = str(experience_path)
        result["experience_incidents"] = experience_result["incidents"]
    except Exception as exc:
        result["experience_candidate"] = None
        result["experience_incidents"] = None
        reasons.append("EXPERIENCE_DRAFT_FAILED_" + type(exc).__name__)

    try:
        append_event(r, run_id=run_id, operation_id=operation_id,
                     stage="handoff", kind="handoff", status="hold" if reasons else "succeeded",
                     provider="local", reason_code="partial_handoff" if reasons else "")
    except Exception as exc:
        reasons.append("HANDOFF_EVENT_FAILED_" + type(exc).__name__)
    # Include the final handoff event in the user's collectable file, not just
    # the earlier export. A successful earlier refresh cannot mask a later one.
    try:
        refresh = refresh_summary(data_root=r)
    except Exception as exc:
        refresh = {"status": "FAILED", "reason_code": type(exc).__name__}
    result["experience_file"] = refresh.get("experience_file") or workbook.get("experience_file")
    result["experience_refresh"] = refresh
    if refresh.get("status") != "REFRESHED":
        reasons.append("FINAL_EXPERIENCE_REFRESH_" + str(refresh.get("reason_code")
                                                        or refresh.get("status") or "FAILED"))
        try:
            append_event(r, run_id=run_id, operation_id=operation_id,
                         stage="handoff", kind="handoff", status="hold",
                         provider="local", reason_code="partial_handoff")
        except Exception as exc:
            reasons.append("FINAL_HOLD_EVENT_FAILED_" + type(exc).__name__)
    reasons = list(dict.fromkeys(reasons))
    result["delivery_completeness"] = "PARTIAL" if reasons else "COMPLETE"
    result["partial_reasons"] = reasons
    if reasons:
        result["status"] = "PARTIAL_HANDOFF_" + reasons[0]
    return result


def _blocked_call_route(fn, args: tuple, kwargs: dict) -> dict | None:
    """Report selected route even for failures; NEVER infer an actual charge."""
    if fn.__name__ not in {
            "marketops_search", "marketops_crawl", "marketops_tinyfish",
            "marketops_jev_choice", "marketops_serpapi_mcp_search",
            "marketops_tinyfish_agent", "marketops_tinyfish_agent_wait"}:
        return None
    try:
        fields = inspect.signature(fn).bind_partial(*args, **kwargs).arguments
    except (TypeError, ValueError):
        return None
    if fn.__name__ == "marketops_search":
        op, primitive = fields.get("provider", ""), "choice"
    elif fn.__name__ == "marketops_crawl":
        op, primitive = {"local": "crawl4ai_local", "cloud": "crawl4ai_cloud"}.get(
            fields.get("implementation", "local"), ""), "choice"
    elif fn.__name__ == "marketops_tinyfish":
        transport = fields.get("transport", "mcp")
        options = ({"search": "tinyfish_mcp_search", "fetch": "tinyfish_mcp_fetch"}
                   if transport == "mcp" else
                   {"search": "tinyfish_search", "fetch": "tinyfish_fetch"})
        op, primitive = options.get(fields.get("task", "search"), ""), "choice"
    elif fn.__name__ == "marketops_serpapi_mcp_search":
        op, primitive = "serpapi_mcp_search", "choice"
    elif fn.__name__ == "marketops_tinyfish_agent":
        op, primitive = "tinyfish_mcp_agent", "choice"
    elif fn.__name__ == "marketops_tinyfish_agent_wait":
        op, primitive = "tinyfish_mcp_wait", "choice"
    else:
        op, primitive = "jev_choice", fields.get("primitive", "choice")
    return provider_policy.invocation_receipt(
        op, primitive=primitive,
        reservation="UNKNOWN_BLOCKED_BEFORE_OR_AFTER_PROVIDER_ATTEMPT")


def _guarded(fn):
    """Return an anticipated domain refusal as a readable MCP tool failure.

    ``research_delivery.main`` and the store already render a designed refusal as
    ``{"status": "BLOCKED", "reason": ...}``. Escaping the tool as a bare
    exception loses that reason: the client sees only ``Error executing tool
    <name>``. ``ToolError`` is the SDK's mechanism for a failure the server saw
    coming, and is delivered as ``is_error=True`` with the reason in ``content``.
    Unexpected exception types are deliberately left to crash loudly.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, OSError) as exc:
            from mcp.server.mcpserver.exceptions import ToolError
            response = {"status": "BLOCKED", "tool": fn.__name__,
                        "reason": str(exc)}
            route = _blocked_call_route(fn, args, kwargs)
            if route is not None:
                response["invocation"] = route
            raise ToolError(json.dumps(response, ensure_ascii=False)) from exc
    return wrapper


def build_server():
    from mcp.server import MCPServer
    mcp = MCPServer("MarketOps Lite Research", version="0.4.0")

    def tool(fn):
        return mcp.tool()(_guarded(fn))

    @tool
    def marketops_setup_status() -> dict:
        """Read-only: are the two first-use paths chosen? Never creates or changes anything."""
        return setup_status()

    @tool
    def marketops_setup_configure(data_root: str, experience_file: str,
                                  confirm: bool = False) -> dict:
        """Persist the two paths the user answered once: research data dir, experience file."""
        return setup_configure(data_root, experience_file, confirm=confirm)

    @tool
    def marketops_note(run_id: str, note_id: str, stage: str, category: str,
                       problem: str, action_taken: str, observed_result: str,
                       hypothesis: str = "", evidence_ref: str = "") -> dict:
        """Record one real bottleneck the agent met, with what it tried and what happened."""
        return note(run_id, note_id, stage, category, problem, action_taken,
                    observed_result, hypothesis, evidence_ref)

    @tool
    def marketops_capabilities(host_observed_tools: list[str] | None = None,
                               task_kind: str | None = None,
                               tinyfish_wallet_snapshot: dict | None = None) -> dict:
        """Read local and optionally host-reported cost hints. Never buys or logs in."""
        return capabilities(host_observed_tools, task_kind, tinyfish_wallet_snapshot)

    @tool
    def marketops_start(run_id: str, scope: str, objective: str, rule_version: str,
                        brief: dict | None = None) -> dict:
        """Start/resume and save an explicit research_brief before any network spend."""
        return start(run_id, scope, objective, rule_version, brief)

    @tool
    def marketops_search(provider: str, query: str, country: str, language: str,
                         run_id: str, operation_id: str, mode: str = "web",
                         limit: int = 10) -> dict:
        """SerpApi OR Serper, explicit one-provider discovery, without fact promotion."""
        return search(provider, query, country, language, run_id, operation_id, mode, limit)

    @tool
    def marketops_crawl(run_id: str, operation_id: str, url: str,
                        implementation: str = "local") -> dict:
        """Crawl4AI local open-source crawler or approved Cloud API."""
        actions = {"local": "crawl4ai_local", "cloud": "crawl4ai_cloud"}
        return invoke(actions.get(implementation, "unsupported"), run_id, operation_id, url=url)

    @tool
    def marketops_tinyfish(run_id: str, operation_id: str,
                           task: str = "search", query: str = "", url: str = "",
                           transport: str = "mcp") -> dict:
        """Embedded official TinyFish MCP Search/Fetch by default; REST explicit fallback."""
        options = {"mcp": {"search": "tinyfish_mcp_search", "fetch": "tinyfish_mcp_fetch"},
                   "rest": {"search": "tinyfish_search", "fetch": "tinyfish_fetch"}}
        action = options.get(transport, {}).get(task, "unsupported")
        return invoke(action, run_id, operation_id, url=url, query=query)

    @tool
    def marketops_serpapi_mcp_search(run_id: str, operation_id: str,
                                    params: dict) -> dict:
        """Embedded official SerpApi MCP search; any valid engine, compact mode.

        The normal marketops_search(provider='serpapi') remains a separate
        backwards-compatible REST adapter; no automatic double search/fallback.
        """
        return invoke("serpapi_mcp_search", run_id, operation_id, mcp_params=params)

    @tool
    def marketops_tinyfish_agent(run_id: str, operation_id: str, url: str,
                                 goal: str, max_steps: int, max_duration_seconds: int,
                                 max_charge_usd: str) -> dict:
        """Submit one read-only, bounded, explicitly paid TinyFish official MCP Agent.

        Requires separate process paid opt-in, domain allowlist, wallet-rate
        readback and an upstream-supported hard max_steps/max_duration_seconds.
        Returns SUBMITTED/UNKNOWN, not evidence or a completed run. Never retry.
        """
        return invoke("tinyfish_mcp_agent", run_id, operation_id, url=url,
                      question=goal, mcp_params={"max_steps": max_steps,
                                                "max_duration_seconds": max_duration_seconds},
                      paid_limit_usd=max_charge_usd)

    @tool
    def marketops_tinyfish_agent_wait(run_id: str, operation_id: str,
                                     original_operation_id: str,
                                     agent_run_id: str, poll_index: int) -> dict:
        """Check an already-submitted TinyFish Agent run through official MCP.

        Polling never creates a new Agent; the exact run handle must match a
        prior hash-verified agent submission in this MarketOps Run. Use a
        monotonically increasing poll_index 1..10; no automatic polling.
        """
        return invoke("tinyfish_mcp_wait", run_id, operation_id,
                      mcp_params={"agent_run_id": agent_run_id,
                                  "origin_operation_id": original_operation_id,
                                  "poll_index": poll_index})

    @tool
    def marketops_jev_choice(run_id: str, operation_id: str, source_excerpt: str,
                             question: str, options: list[str],
                             primitive: str = "choice") -> dict:
        """Jev Choice/Noul/Score via one metered API; never a fact or SOP verdict."""
        return invoke("jev_choice", run_id, operation_id, document=source_excerpt,
                      question=question, options=options, primitive=primitive)

    @tool
    def marketops_propose(run_id: str, record_id: str, stage: str,
                          kind: str, entity_key: str, facts: dict,
                          artifact_ids: list[str] | None = None) -> dict:
        """Write staged venue/organization/role/contact/review proposals only."""
        return propose(run_id, record_id, stage, kind, entity_key, facts, artifact_ids)

    @tool
    def marketops_propose_batch(run_id: str, items: list[dict]) -> dict:
        """One atomic batch of 1–20 candidates; same checks as marketops_propose."""
        return propose_batch(run_id, items)

    @tool
    def marketops_material_excerpt(run_id: str, artifact_ids: list[str],
                                   queries: list[str] | None = None) -> dict:
        """Get SHA-checked source snippets capped at 1200 chars; no full-page dump."""
        return material_excerpt(run_id, artifact_ids, queries)

    @tool
    def marketops_evidence_audit(run_id: str, limit: int = 12) -> dict:
        """Compact, generic claim-to-source mismatch report, not a fact certification."""
        return evidence_audit(run_id, limit)

    @tool
    def marketops_status(run_id: str, limit: int = 20, cursor: str = "",
                         kind: str = "", entity_key: str = "") -> dict:
        """Read persistent candidates; iterate next_cursor for a full audit, or filter."""
        return status(run_id, limit, cursor, kind, entity_key)

    @tool
    def marketops_next(run_id: str, limit: int = 10,
                       previous_snapshot_id: str = "", after_attempt: bool = False) -> dict:
        """Bounded task batch; after_attempt+prior snapshot detects lack of durable progress."""
        return next_step(run_id, limit, previous_snapshot_id, after_attempt)

    @tool
    def marketops_events(run_id: str) -> dict:
        """Recent actual Lite call paths, billing meter & failures, not invoices."""
        return provider_events(run_id)

    @tool
    def marketops_export(run_id: str, output_path: str) -> dict:
        """Export local candidate-only CSV; no contact/outreach authorization."""
        return export(run_id, output_path)

    @tool
    def marketops_export_xlsx(run_id: str, delivery_id: str) -> dict:
        """Write real 9-sheet evidence-audited XLSX + CSV from this Run's Lite store.

        All contacts remain CANDIDATE_NOT_APPROVED; no A/B/C/D or send authority.
        """
        return delivery.write_delivery(root(), run_id, delivery_id)

    @tool
    def marketops_evidence_workbook(run_id: str, delivery_id: str) -> dict:
        """Five-sheet evidence chain (sources/claims/evidence/receipts/review) from the real ledger."""
        return evidence_workbook(run_id, delivery_id)

    @tool
    def marketops_handoff(run_id: str, operation_id: str) -> dict:
        """Deliver one audited candidate XLSX/CSV snapshot and experience artifacts with partial failures visible."""
        return handoff(run_id, operation_id)

    @tool
    def marketops_experience_draft(run_id: str, scope: str, rule_version: str,
                                   output_path: str) -> dict:
        """Create unapproved incident draft from actual event receipts."""
        r = root()
        output = Path(output_path).expanduser().resolve()
        if not output.is_relative_to(r / "experience"):
            raise ValueError("EXPERIENCE_MUST_BE_WITHIN_CUSTOMER_DATA_ROOT")
        output.parent.mkdir(parents=True, exist_ok=True)
        return draft_experience(r, run_id=run_id, scope=scope,
                                rule_version=rule_version, skill_version="0.4.0",
                                output=output)

    @tool
    def marketops_managed_call(capability_id: str, params: dict, run_id: str,
                               operation_id: str, account_ref: str,
                               intent_fingerprint: str,
                               grant_ref: str = "") -> dict:
        """The one managed path: S0 gate, then Registry→Policy→Ledger→Adapter.

        There is no `evidence` parameter on purpose. S0 evidence is read from this
        deployment's data root, so an Agent cannot declare that S0 passed and
        unlock a paid call. A refused gate returns `gateway_invoked: false`,
        which means no reservation was taken and no adapter ran.
        """
        import research_wiring

        outcome = research_wiring.managed_invoke(
            capability_id=capability_id, params=params, run_id=run_id,
            operation_id=operation_id, account_ref=account_ref,
            intent_fingerprint=intent_fingerprint, grant_ref=grant_ref or None,
            root=root())
        inner = outcome.outcome
        return {
            "status": outcome.status,
            "reason_code": outcome.reason_code,
            "capability_id": outcome.capability_id,
            "gateway_invoked": outcome.gateway_invoked,
            "s0_failed": list(outcome.s0_failed),
            "caveats": list(outcome.caveats),
            "outcome": None if inner is None else {
                "status": getattr(inner, "status", None),
                "reason_code": getattr(inner, "reason_code", None),
                "provider": getattr(inner, "provider", None),
                "caveats": list(getattr(inner, "caveats", ()) or ()),
            },
        }

    return mcp


def host_compatible_server():
    """The stdio entry point this file is installed as.

    This module is the one managed entry the installers and the client configs
    name, so *this* is where host compatibility has to hold, not in a sibling
    file the launch path never reaches. The advertised `tools/list` schema is
    narrowed to the subset every supported host accepts, by the facade that
    already owns that rewrite; every tool's *behaviour* is untouched, and the
    server's own validator still accepts the values the narrowed schema omits.
    """
    import research_mcp_facade

    return research_mcp_facade.build_host_compatible_server()


if __name__ == "__main__":
    host_compatible_server().run(transport="stdio")

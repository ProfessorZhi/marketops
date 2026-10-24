#!/usr/bin/env python3
"""Bounded 5-provider Lite Research MCP. Candidate-only, local, human-initiated."""
from __future__ import annotations

import functools
from datetime import datetime, timezone
import hashlib
import importlib.util
import inspect
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
import logging

import dual_search_mcp


def _quiet_http_logs() -> None:
    """httpx logs each request URL at INFO, query included. SerpApi takes its key in the
    query, so a host that shows the server's stderr showed the key (seen in a live run)."""
    for name in ("httpx", "httpcore", "httpx2", "mcp.client.streamable_http"):
        logging.getLogger(name).setLevel(logging.WARNING)


_quiet_http_logs()
import research_config
import research_usage_ledger as usage_ledger
import research_lite_store as store
import research_provider_extensions as extra
import research_delivery as delivery
import research_workflow as workflow
import research_evidence as evidence
import research_experience_recall as experience_memory
import research_provider_policy as provider_policy
from research_event_journal import (_root, append_event, append_note, draft_experience,
                                    summarize_events)
from research_experience_summary import refresh_quietly as refresh_summary


def next_step(run_id, limit=10, previous_snapshot_id="", after_attempt=False):
    """Bounded tasks; an unchanged snapshot after work triggers a stop/switch hint."""
    result = workflow.next_step(root(), run_id, limit, previous_snapshot_id, after_attempt,
                                budget=_source_selection_budget(run_id))
    result["experience"] = _experience_view(run_id, stage=result.get("stage", "intake"))
    import research_contacts
    contacts = research_contacts.coverage(root(), run_id, limit=limit)
    result["contact_coverage"] = contacts
    if contacts.get("organizations_with_missing_roles", 0):
        result.update(goal_satisfied=False, status="MORE_RESEARCH_REQUIRED")
    import research_execution
    result["execution"] = research_execution.assess(root(), run_id, goal=result,
                                                    budget=_source_selection_budget(run_id))
    return result


#: Constant disclaimers the skills already state once; repeating them on every
#: next call only spent the Agent's context.
_NEXT_CONSTANT_FIELDS = ("business_verified", "coverage_complete", "send_authorized",
                         "assessment_scope", "allowed_capabilities", "network_authorization",
                         "warning", "next_best_source_action", "candidate_write_contract")


def compact_next(result: dict) -> dict:
    """What an Agent needs from next: status, gaps, targets, the next action, budget."""
    view = {key: value for key, value in result.items() if key not in _NEXT_CONSTANT_FIELDS}
    selection = result.get("source_selection")
    if isinstance(selection, dict):
        view["source_selection"] = {
            "next_action": selection.get("next_action"),
            "budget": selection.get("budget"),
            "blocked_domains": selection.get("blocked_domains"),
            "budget_note": selection.get("budget_note"),
            "candidates": [{key: item.get(key) for key in
                            ("entity_key", "completeness", "missing_fields")}
                           for item in (selection.get("candidates") or [])[:5]],
        }
    experience = result.get("experience")
    if isinstance(experience, dict):
        view["experience"] = {
            "status": experience.get("status"),
            "tips": [{"id": item.get("experience_id"), "do": item.get("do")}
                     for item in experience.get("experiences", [])],
            "more": "marketops_experience_recall" if experience.get("more_available") else None,
        }
    if "candidate_write_contract" in result:
        view["write_help"] = "marketops-accounts skill: claims, joins and examples"
    execution = result.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("methods"), dict):
        view["execution"] = {**execution, "methods": {
            ladder: {"next_method": info.get("next_method"),
                     "tried": {t["method"]: t["outcome"] for t in info.get("tried", [])}}
            for ladder, info in execution["methods"].items()}}
    return view


def _experience_view(run_id, *, stage="intake"):
    """A failed advisory lookup must be visible without losing durable research."""
    try:
        found = experience_memory.recall(root(), run_id=run_id, stage=stage, limit=2)
        fields = ("experience_id", "source_type", "source_run_id", "when", "do",
                  "limitations", "strategy_hint")
        suggestions = []
        for item in found["experiences"]:
            compact = {name: item[name] for name in fields if name in item}
            compact["feedback"] = {name: item.get("feedback", {}).get(name)
                                   for name in ("independent_runs", "outcomes")}
            compact["execution_authority"] = "none"
            suggestions.append(compact)
        return {"status": found["status"], "as_of": found["as_of"],
                "experiences": suggestions, "matched_count": found["matched_count"],
                "more_available": found["matched_count"] > len(suggestions),
                "detail_tool": "marketops_experience_recall",
                "business_verified": False, "policy_changed": False}
    except Exception as exc:
        code = str(exc).split(":", 1)[0]
        return {"status": "UNAVAILABLE", "experiences": [],
                "reason_code": code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code)
                else "EXPERIENCE_READ_FAILED", "reason_type": type(exc).__name__,
                "business_verified": False, "policy_changed": False}


def status(run_id, limit=20, cursor="", kind="", entity_key=""):
    """Persisted candidates through the workflow page contract.

    A page fixes a row high-water mark; later appends land in a new query, and a
    cursor belongs to its Run and filters.
    """
    if kind == "artifact":
        if entity_key:
            raise ValueError("MATERIAL_VIEW_HAS_NO_ENTITY_FILTER")
        return store.list_materials(None, run_id, limit=limit, cursor=cursor)
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


PROVIDER_KEYS = {"serper": "SERPER_API_KEY", "serpapi": "SERPAPI_API_KEY",
                 "tinyfish": "TINYFISH_API_KEY", "typesafe_jev": "TYPESAFE_API_KEY",
                 "deepseek": "DEEPSEEK_API_KEY"}

#: Every provider this report has a line for. ``crawl4ai`` is the only one with no
#: key (it is a local install); every other entry must also appear in
#: ``PROVIDER_KEYS``. The two used to be maintained separately, and DeepSeek —
#: added later as a search route — ended up in ``PROVIDER_KEYS`` but not here, so
#: its key could be missing and the panel said nothing at all. A provider that is
#: in one table and not the other is invisible, and invisible is not "fine".
READINESS_PROVIDERS = ("serper", "deepseek", "serpapi", "tinyfish", "crawl4ai",
                       "typesafe_jev")


def provider_readiness() -> dict:
    """Per provider, what this machine can tell without spending a call.

    READY means: key present, enabled for this project with network on, and
    (SerpApi/TinyFish) the connection check passed at setup; Crawl4AI needs no
    key, only its local install. It is not a check of the account's credit.
    """
    import research_runtime_setup
    try:
        # An installing agent may have stored keys after this server started;
        # re-read them so the answer is about the keyring as it is now.
        from research_credentials import load_into_environment
        load_into_environment()
    except Exception:  # noqa: BLE001 - readiness must answer without a usable keyring
        pass
    try:
        data_root = root()
        settings = research_runtime_setup.config(data_root) or {}
    except (ValueError, OSError):
        data_root, settings = None, {}
    enabled = set(settings.get("providers", ())) if settings.get("network_enabled") else set()
    out = {}
    import research_accounts
    for provider in READINESS_PROVIDERS:
        if provider == "crawl4ai":
            import research_crawl_doctor
            checked = research_crawl_doctor.cached(data_root) if data_root is not None else None
            # Importing the SDK here initializes its global database before the
            # managed transport can select the customer's cache directory.
            sdk_present = "crawl4ai" in sys.modules or importlib.util.find_spec("crawl4ai") is not None
            cache_issue = ""
            if sdk_present and data_root is not None:
                from research_runtime_transport import validate_crawl_cache
                try:
                    validate_crawl_cache(data_root)
                except ValueError as exc:
                    cache_issue = str(exc).split(":", 1)[0]
            if not sdk_present:
                state = "NOT_INSTALLED"
            elif cache_issue:
                state = "RESTART_REQUIRED" if cache_issue == "CRAWL_CACHE_RESTART_REQUIRED" else "CACHE_CONFIGURATION_INVALID"
            elif checked is None:
                state = "INSTALLED_NOT_CHECKED"
            else:
                state = ("READY" if checked.get("page_read_verified") is True else
                         "BROWSER_READY_PAGE_UNCHECKED") if checked["status"] == "WORKING" else "NOT_WORKING"
        elif (not configured(PROVIDER_KEYS[provider]) and not
              (data_root is not None and research_accounts.has_local_key(data_root, provider))):
            state = "KEY_MISSING"
        else:
            state = "READY"
        if state == "READY" and provider not in enabled:
            state = "NOT_ENABLED_FOR_PROJECT"
        if (state == "READY" and provider in {"serpapi", "tinyfish"} and data_root is not None
                and not (Path(data_root) / "state" / f"{provider}-schema.json").is_file()):
            state = "CONNECTION_NOT_CHECKED"
        out[provider] = state
    fixes = {"KEY_MISSING": "rerun the local setup command (START_HERE.md) and enter the key",
             "NOT_ENABLED_FOR_PROJECT": "rerun the local setup and answer y to enable research",
             "CONNECTION_NOT_CHECKED": "rerun the local setup; check the key and the account",
             "NOT_INSTALLED": "marketops_crawl4ai_check(repair=True), or run Repair.cmd "
                              "(Windows) / repair.sh in the MarketOps folder",
             "INSTALLED_NOT_CHECKED": "marketops_crawl4ai_check() (starts the browser once)",
             "BROWSER_READY_PAGE_UNCHECKED": "marketops_crawl4ai_check(run_id=<current run>) to verify managed page reading",
             "RESTART_REQUIRED": "restart the agent/MCP server to select the managed Crawl4AI cache before SDK import",
             "CACHE_CONFIGURATION_INVALID": "correct the local Crawl4AI cache setting and restart the agent",
             "NOT_WORKING": "marketops_crawl4ai_check(repair=True), or run Repair.cmd / "
                            "repair.sh in the MarketOps folder"}
    import research_balances
    import research_provider_health as health
    for provider, entry in health.status(data_root).items() if data_root is not None else ():
        if provider in out and out[provider] == "READY":
            out[provider] = f"{entry['state']}_UNTIL_{entry['until'][:10]}"
    fixes.update({"QUOTA_EXHAUSTED": "top up the account (or wait for the monthly reset), then "
                                     "marketops_costs(action='balances') to clear it",
                  "RATE_LIMITED": "wait a minute; other providers carry on meanwhile",
                  "LOCAL_DOWN": "marketops_crawl4ai_check(repair=True), or run Repair.cmd / "
                                "repair.sh in the MarketOps folder"})
    low = research_balances.warnings(data_root) if data_root is not None else []
    account_status = research_accounts.status(data_root) if data_root is not None else {"accounts": []}
    if (str(out.get("typesafe_jev", "")).startswith("QUOTA_EXHAUSTED")
            and any(a["provider"] == "typesafe_jev" and a["enabled"] and a["failover"]
                    and not health.blocked(data_root, a["account_ref"])
                    for a in account_status["accounts"])):
        out["typesafe_jev"] = "READY"
    budget = None
    if data_root is not None and settings.get("max_calls") is not None:
        from research_runtime_status import usage
        import research_runtime_setup as budget_runtime
        budget = usage(data_root, budget_runtime.cap_plan(settings)["project_max_calls"])
        if budget_runtime.cap_plan(settings)["budget_mode"] == "MONEY":
            import research_spending
            budget.update(metric="requests_observed_not_a_spending_limit", request_limit_active=False,
                          monetary=research_spending.status(data_root))
    return {"providers": out,
            "accounts": account_status["accounts"],
            "low_balance_warnings": low,
            "shared_request_budget": budget,
            "request_budget_warning": "LOCAL_REQUEST_LIMIT_EXHAUSTED" if budget and budget.get("remaining") == 0
                                      else "LOCAL_REQUEST_LIMIT_NEAR" if budget and type(budget.get("remaining")) is int
                                      and budget["remaining"] <= 10 else "",
            "how_to_fix": {p: fixes[s.split("_UNTIL_")[0]] for p, s in out.items()
                           if s.split("_UNTIL_")[0] in fixes},
            "note": "READY is local readiness, not vendor credit or permission to exceed the shared request budget."}


def setup_status():
    """Only two mandatory paths. Vendor account onboarding remains optional."""
    state = research_config.status(redact=True)
    try:
        readiness = provider_readiness()
    except Exception as exc:  # noqa: BLE001 - status must answer even when this fails
        readiness = {"providers": "UNKNOWN", "reason": type(exc).__name__}
    return {**state, "provider_readiness": readiness, "provider_onboarding": {
        "state": "OPTIONAL_AFTER_LOCAL_SETUP",
        "guide": "references/CUSTOMER_QUICKSTART.md",
        "connection_catalog": "references/PROVIDER_CONNECTIONS.json",
        "local_offline_command": "python scripts/provider_onboarding.py --client claude",
        "provider_accounts_authenticated": "NOT_VERIFIED",
        "official_mcp_sessions_verified": False,
        "network_or_provider_fee_incurred": False,
    }, "build": _build_note()}


def _build_note():
    """A missing or stale test receipt is reported, never a reason to refuse calls."""
    from research_runtime_setup import verify_build_receipt
    receipt = verify_build_receipt()
    if receipt["ok"]:
        return {"tested_release": True, "source_revision": receipt["source_revision"]}
    return {"tested_release": False,
            "note": "Test build without a release receipt; research works normally."}


def update_check(include_prerelease=False):
    """Compare this install with the newest GitHub release, through the managed egress."""
    import release_layout
    import update_marketops as updater
    version_dir = Path(__file__).resolve().parents[3]
    app_root = release_layout.app_root_for(version_dir)
    current = json.loads((Path(__file__).resolve().parents[1] / "plugin.json")
                         .read_text(encoding="utf-8"))["version"]
    if os.environ.get("MARKETOPS_RESEARCH_ALLOW_NETWORK") == "0":
        return {"status": "NETWORK_DISABLED", "current": current}

    def get(url):
        import httpx2
        from research_egress import open_session
        with open_session(allowlist=frozenset({"api.github.com"})) as connection:
            with httpx2.Client(proxy=connection.proxy.url, trust_env=False,
                               follow_redirects=False,
                               timeout=httpx2.Timeout(20, connect=10)) as http:
                answer = http.get(url, headers={"Accept": "application/vnd.github+json",
                                                "User-Agent": "marketops-update-check"})
                answer.raise_for_status()
                return answer.content
    try:
        release = updater.latest_release(release_layout.REPO, include_prerelease, get=get)
    except Exception as exc:  # noqa: BLE001 - a failed check is reported, never raised
        return {"status": "CHECK_FAILED", "current": current, "reason": type(exc).__name__}
    latest = release["tag_name"].removeprefix("marketops-v") if release else None
    newer = bool(latest and updater.version_key(latest) > updater.version_key(current))
    how = (("Run Update.cmd (Windows) or update.sh in " + str(app_root) +
            ", then restart this agent.") if app_root is not None else
           "This install predates the update layout: install the new release once with "
           "Install.cmd; later updates are one command.")
    return {"status": "UPDATE_AVAILABLE" if newer else "UP_TO_DATE", "current": current,
            "latest": latest, "notes": release.get("html_url") if release else None,
            **({"how_to_update": how} if newer else {})}


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


def installed_browser_root() -> Path | None:
    """The Chromium the installer put beside this copy (<install>/browsers), if any."""
    folder = Path(__file__).resolve().parents[3] / "browsers"
    try:
        if folder.is_dir() and any(c.name.startswith(("chromium", "chromium_headless_shell"))
                                   for c in folder.iterdir()):
            return folder
    except OSError:
        return None
    return None


def _ensure_repair_entrypoints() -> None:
    """An install from before Repair.cmd existed gets it from the first server start."""
    try:
        import release_layout
        install = Path(__file__).resolve().parents[3]
        app_root = release_layout.app_root_for(install) or (
            install.parent if install.name == "current" else None)
        if app_root is not None and not (Path(app_root) / "Repair.cmd").exists():
            release_layout.write_repair_entrypoints(app_root)
    except Exception:  # noqa: BLE001 - a read-only install still serves research
        pass


def _use_installed_browser() -> None:
    """Find the plugin's own browser even when a host did not pass its path.

    The installer downloads Chromium into <install>/browsers and writes the path
    into each host's MCP config; a host that drops `env` left Crawl4AI looking in
    the system default cache, finding nothing, and failing on every page.
    """
    current = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    folder = installed_browser_root()
    if current and not current.startswith("$" + "{"):
        # A machine-wide PLAYWRIGHT_BROWSERS_PATH (set for some other tool) may hold
        # other Chromium builds than the one this Playwright needs; then Crawl4AI found
        # "no browser" on every page although the installer had put one beside us.
        if folder is None or Path(current).resolve() == folder.resolve():
            return
        wanted = {c.name for c in folder.iterdir() if c.name.startswith("chromium")}
        try:
            have = {c.name for c in Path(current).iterdir()}
        except OSError:
            have = set()
        if wanted <= have:
            return
    if folder is not None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(folder)


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


def _unknown_held(run_id: str) -> int | None:
    """Read-only count of a Run's UNKNOWN charge rows; never creates the ledger."""
    try:
        path = root() / "state" / "usage-ledger.sqlite3"
    except (ValueError, OSError):
        return None
    if not path.is_file():
        return 0
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
            return int(db.execute(
                "SELECT COUNT(*) FROM operations WHERE run_id=? AND charge_state='UNKNOWN'",
                (run_id,)).fetchone()[0])
    except (sqlite3.Error, OSError):
        return None


def _money_remaining(money: dict):
    """Headroom under the active money policy, in whichever shape it reports.

    An envelope policy reports ``remaining_usd`` directly. A red-line policy
    states an amount to keep instead, so its headroom is the distance between
    that line and what has been spent since the policy began. A policy that
    reports neither is unknown, not zero — and reading a key the red-line shape
    does not carry raises, which used to drop the whole managed budget to the
    environment view and report a run with plenty of money as exhausted.
    """
    if "remaining_usd" in money:
        return money["remaining_usd"]
    line, spent = money.get("total_red_line_usd"), money.get("spent_since_policy_usd")
    if line is None or spent is None:
        return None
    from decimal import Decimal, InvalidOperation
    try:
        return str(Decimal(str(line)) - Decimal(str(spent)))
    except (InvalidOperation, ValueError):
        return None


def _source_selection_budget(run_id: str) -> dict:
    """Local budget facts for the advisory; a missing value stays None, never 0.

    A runtime-configured deployment keeps its ceiling in ``runtime-config.json``,
    not in ``MARKETOPS_SEARCH_MAX_CALLS``. Reading only the env would report a
    false zero and make every advisory say BUDGET_EXHAUSTED. The managed path is
    read first; the legacy env path is the fallback.
    """
    try:
        data_root = research_config.data_root()
        import research_runtime_setup as runtime_setup
        settings = runtime_setup.config(data_root)
    except Exception:  # noqa: BLE001 - no readable runtime config
        data_root, settings = None, None
    if settings:
        try:
            from research_runtime_status import usage as runtime_usage
            snapshot = runtime_usage(
                data_root, runtime_setup.cap_plan(settings)["project_max_calls"])
            monetary = {}
            if runtime_setup.cap_plan(settings)["budget_mode"] == "MONEY":
                import research_spending
                money = research_spending.status(data_root)
                monetary = {"budget_mode": "MONEY", "request_limit_active": False,
                            "remaining_usd": _money_remaining(money),
                            "free_page_reads_available": True}
            return {**monetary, "search_remaining": snapshot["remaining"],
                    "provider_remaining": snapshot["remaining"],
                    "unknown_held": snapshot.get("ambiguous_outcome_operations",
                                                 snapshot["unknown_charge_operations"]),
                    "unbilled_successes": (snapshot["unknown_charge_operations"] or 0)
                    - (snapshot.get("ambiguous_outcome_operations") or 0),
                    "source": "RUNTIME_CONFIG"}
        except Exception:  # noqa: BLE001 - fall through to the env view
            pass
    try:
        ceiling = dual_search_mcp._configuration()["max_total_reserved_calls"]
        # The adapters read an unset ceiling variable as 0. A ceiling of zero can
        # never be approved — every caller refuses one below 1 — so 0 means "the
        # environment stated no ceiling", which is unknown. Reporting that as 0
        # made every advisory on a managed deployment say BUDGET_EXHAUSTED.
        search_remaining = (
            _capacity(ceiling, _ledger_usage("dual-search-ledger.sqlite3",
                                             "search_attempts"))["remaining"]
            if ceiling and ceiling > 0 else None)
    except Exception:  # noqa: BLE001 - an unavailable budget is unknown, not zero
        search_remaining = None
    try:
        ceiling = extra._limit()
        provider_remaining = (
            _capacity(ceiling, _ledger_usage("provider-reservations.sqlite3",
                                             "provider_reservations"))["remaining"]
            if ceiling and ceiling > 0 else None)
    except Exception:  # noqa: BLE001
        provider_remaining = None
    return {"search_remaining": search_remaining,
            "provider_remaining": provider_remaining,
            "unknown_held": _unknown_held(run_id),
            "source": "ENV"}


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
    import research_runtime_setup as runtime_setup
    try:
        configured_root = research_config.data_root()
    except research_config.ConfigError:
        configured_root = None
    if configured_root is not None and runtime_setup.config(configured_root):
        from research_runtime_status import view
        return {"product": "MarketOps Research MCP", "version": "0.5.11-rc13",
                **view(configured_root, task_kind=task_kind),
                "external_host": _external_inventory(host_observed_tools),
                "embedded_official_mcp": provider_policy.embedded_official_mcp_capabilities(),
                "business_store": "customer data root; evidence-linked candidates",
                "no_auto_outreach": True}
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
        "version": "0.5.11-rc13",
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
    import research_scene
    if brief.get("scene") and research_scene.shipped(brief["scene"]) is None:
        raise ValueError("RESEARCH_BRIEF_INVALID: scene '" + str(brief["scene"])[:60]
                         + "' is not shipped; write scene_rules for this market instead")
    if "scene_rules" in brief:
        problems = research_scene.validate(brief["scene_rules"])
        if problems:
            raise ValueError("RESEARCH_BRIEF_INVALID: " + "; ".join(problems))
    gate = brief.get("source_gate", True)
    if not (gate is True or gate is False or isinstance(gate, dict)):
        raise ValueError("RESEARCH_BRIEF_INVALID: source_gate must be false or {pilot, promote, stop}")
    synthetic = {"record_id": "brief-validation", "stage": "qualification",
                 "kind": "observation", "status": "candidate_unverified",
                 "entity_key": "research-brief", "artifact_ids": [], "data": brief}
    result = workflow.assess_records(run_id, [synthetic])
    if result["status"] in {"BRIEF_REQUIRED", "BRIEF_CONFLICT", "BRIEF_INVALID",
                            "BRIEF_UNIT_REVIEW_REQUIRED"}:
        problems = result.get("problems") or ()
        raise ValueError("RESEARCH_BRIEF_INVALID: " + result["status"]
                         + (" (fix: " + "; ".join(problems) + ")" if problems else ""))
    if evidence.audit_records([synthetic], {}, result_limit=0)["invalid_brief_evidence_requirements"]:
        raise ValueError("RESEARCH_BRIEF_EVIDENCE_REQUIREMENTS_INVALID: expected kind -> field-name array, "
                         "for example {organization: [identity], venue: [identity, surface_type]}; "
                         "put narrative requirements in objective, not in this mapping")


def _require_brief_for_network(root_path, run_id: str) -> None:
    """Do not spend an approved attempt when the project objective is undefined."""
    assessment = workflow.next_step(root_path, run_id, limit=1)
    if assessment["status"] in {"BRIEF_REQUIRED", "BRIEF_CONFLICT",
                                 "BRIEF_INVALID", "BRIEF_UNIT_REVIEW_REQUIRED"}:
        raise ValueError("RESEARCH_BRIEF_REQUIRED_BEFORE_NETWORK: " + assessment["status"])


def start(run_id, scope, objective, rule_version, brief=None):
    """Start/resume one Run and atomically stage its validated structured goal."""
    r = root()
    if (type(brief) is dict and brief.get("minimum_targets") is not None
            and "minimum_targets_unit" not in brief):
        # A new Brief states its quantity in its own target unit. The unit review
        # exists for legacy Runs; asking a fresh Brief for it only stalled intake.
        brief = {**brief, "minimum_targets_unit": brief.get("target_kind")}
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
    if brief_receipt is None and next_state.get("brief_record_id"):
        brief_receipt = {"status": "EXISTING_BRIEF", "record_id": next_state["brief_record_id"]}
    return {**result, "event": event,
            "usage_checkpoint": {"since": datetime.now(timezone.utc).isoformat(),
                                 "scope": "this conversation slice; retain across checkpoint continuation"},
            "brief": brief_receipt or {"status": "BRIEF_REQUIRED"},
            "next_status": next_state["status"],
            "next_skill": next_state["recommended_skill"],
            "experience": _experience_view(run_id, stage="intake")}


#: The ledger's own code for "this logical operation is already recorded in this
#: run". It reaches a caller folded into ``DUPLICATE_OPERATION``, so the reason
#: the two attempts are one operation is added back here rather than left as a
#: bare code an agent can only answer by retrying.
_DUPLICATE_SIGNATURE_CAVEAT = "LEDGER_CODE:DUPLICATE_OPERATION_SIGNATURE"

_DUPLICATE_SIGNATURE_HINT = (
    "DUPLICATE_SEARCH_SAME_RUN: this exact operation is already recorded in this run"
    " (a refused attempt keeps its claim); use a new run_id or a different query")


def _refusal_text(answer):
    """One line a host can act on, built only from what the answer carries.

    ``s0_failed`` names the gate that closed; ``caveats`` names what happened
    after the gate, which is where the ledger's own code travels. Neither is
    invented here: an empty refusal stays empty rather than acquiring a guess.
    """
    failed = list(answer.get("s0_failed") or ())
    caveats = [str(item) for item in (answer.get("caveats") or ())]
    detail = [item for item in failed + caveats if item]
    if _DUPLICATE_SIGNATURE_CAVEAT in detail:
        detail.append(_DUPLICATE_SIGNATURE_HINT)
    return str(answer.get("reason_code") or "BLOCKED") + ": " + ",".join(detail)


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


def managed_call(capability_id, params, run_id, operation_id, account_ref="",
                 intent_fingerprint="", grant_ref=""):
    """One effective provider route, with the real durable evidence contract."""
    import research_wiring
    import research_capability_registry as registry
    import research_runtime_results as results
    r = root()
    store.list_run(r, run_id, limit=1)
    _require_brief_for_network(r, run_id)
    entry = registry.DEFAULT_REGISTRY.get(capability_id)
    if entry is None:
        raise ValueError("CAPABILITY_UNKNOWN")
    # Account/intent authority belongs to local setup and canonical arguments,
    # never caller-supplied strings which could bypass the account/dedup scopes.
    if account_ref and account_ref != entry.provider:
        raise ValueError("CAPABILITY_PARAM_NOT_ALLOWED: account alias is deployment-owned")
    if not isinstance(params, dict):
        raise ValueError("CAPABILITY_PARAM_NOT_ALLOWED")
    if capability_id in {"serper.search", "serpapi.search", "deepseek.search"}:
        import research_locale
        params = {**params}
        for key, convert in (("country", research_locale.country_code),
                             ("language", research_locale.language_code)):
            if key in params:
                params[key] = convert(params[key])
    arguments = results.normalize_params(capability_id, params)
    if capability_id in {"serper.search", "serpapi.search"}:
        for key, value in {"country": "US", "language": "en", "limit": 10}.items():
            arguments.setdefault(key, value)
        import serpapi_engine_catalog as _catalog
        arguments.setdefault("mode" if capability_id == "serper.search" else "engine",
                             "web" if capability_id == "serper.search"
                             else _catalog.DEFAULT_ENGINE)
    fingerprint = results.identity(capability_id, arguments)
    # A provider that said it is out of credit (or to slow down) is answered here,
    # in a form the fallback chain skips, instead of being asked again.
    import research_provider_health as health
    import research_accounts as accounts
    if entry.provider in accounts.PROVIDERS:
        account, known, pooled = accounts.select(r, entry.provider)
        selected_ref = account["account_ref"]
    else:
        selected_ref, pooled = entry.provider, False
        known = health.blocked(r, selected_ref)
    if known:
        return health.refusal(entry.provider, capability_id, known)
    claimed = False
    if pooled:
        import research_runtime_setup as runtime
        if not runtime.config(r):
            return {"status": "BLOCKED", "reason_code": "CAPABILITY_NOT_AVAILABLE",
                    "caveats": ["ACCOUNT_POOL_REQUIRES_RUNTIME_CONFIG"], "gateway_invoked": False,
                    "artifact_ids": []}
        if not runtime.admission(r, capability_id)["allowed"]:
            pooled = False
    if pooled:
        claimed, previous = accounts.claim_intent(r, run_id, entry.provider, fingerprint)
        if not claimed:
            if previous is not None:
                return {**previous, "reused": True}
            return {"status": "BLOCKED", "reason_code": "DUPLICATE_OPERATION",
                    "provider": entry.provider, "account_ref": selected_ref,
                    "gateway_invoked": False, "artifact_ids": []}
        historical = accounts.historical_intent(r, run_id, entry.provider, fingerprint)
        if historical is not None:
            answer = {"status": "BLOCKED", "reason_code": "DUPLICATE_OPERATION",
                      "provider": entry.provider, "account_ref": historical.account_ref,
                      "ledger_operation_id": historical.operation_id,
                      "caveats": ["HISTORICAL_INTENT_REQUIRES_RECONCILIATION"],
                      "gateway_invoked": False, "artifact_ids": []}
            accounts.finish_intent(r, run_id, entry.provider, fingerprint, answer)
            return answer
    # IDs are run-scoped at the MCP boundary, ledger-scoped internally.
    ledger_operation = "op-" + hashlib.sha256((run_id + ":" + operation_id).encode()).hexdigest()[:40]
    guarded = research_wiring.managed_invoke(
        capability_id=capability_id, params=arguments, run_id=run_id,
        operation_id=ledger_operation, account_ref=selected_ref,
        intent_fingerprint=fingerprint, root=r, grant_ref=grant_ref or None)
    answer = results.persist(r, run_id, operation_id, guarded, params=arguments)
    answer["account_ref"] = selected_ref
    if capability_id == "crawl4ai.fetch_page":
        import research_crawl_doctor as doctor
        if doctor.note_local_result(r, ok=answer.get("status") == "OK",
                                    reason_code=str(answer.get("reason_code") or "")):
            answer["crawl4ai_down"] = crawl4ai_down_notice(r)
    state = health.classify(answer)
    if state:
        health.record(r, entry.provider, state, account_ref=selected_ref,
                      detail=str((answer.get("payload") or {}).get("detail") or "")[:120])
        if state == "RATE_LIMITED" and selected_ref != entry.provider:
            health.record(r, entry.provider, state, detail="supplier cooldown; account rotation disabled")
        answer["provider_health"] = {"provider": entry.provider, "state": state,
                                     "next": "the next provider in the chain takes over; "
                                             "tell the user in the report"}
    if claimed:
        accounts.finish_intent(r, run_id, entry.provider, fingerprint, answer)
    return answer


def crawl4ai_down_notice(data_root) -> dict:
    """What the Agent tells the user when the local reader stops working."""
    import research_crawl_doctor as doctor
    last = (doctor.cached(data_root) or {})
    if str(last.get("failed_step", "")).startswith("CACHE_"):
        return {"status": "CRAWL4AI_NOT_WORKING_ON_THIS_COMPUTER", "failed_step": last["failed_step"],
                "fix": last.get("fix", ""), "tell_the_user": last.get("fix", ""),
                "repair": last.get("repair", "restart the agent/MCP server")}
    return {"status": "CRAWL4AI_NOT_WORKING_ON_THIS_COMPUTER",
            "failed_step": last.get("failed_step", ""), "fix": last.get("fix", ""),
            "tell_the_user": ("Crawl4AI, the free local page reader, is not working on this "
                              "computer. Offer the repair (marketops_crawl4ai_check with "
                              "repair=true). Until it works, pages are read by TinyFish fetch "
                              "only if the user agrees; say so in the report."),
            "repair": last.get("repair", "marketops_crawl4ai_check(repair=True)")}


def crawl4ai_check(run_id="", repair=False) -> dict:
    """Run (or repair and rerun) the Crawl4AI self-check; a run_id adds a real page read."""
    import research_crawl_doctor as doctor
    # A repair/download can finish after this long-lived MCP process started.
    _use_installed_browser()
    data_root = root()
    if repair:
        outcome = doctor.repair(data_root)
        if outcome["status"] != "REPAIRED" or not run_id:
            return outcome
    page_read = None
    if run_id:
        def page_read():
            answer = managed_call("crawl4ai.fetch_page", {"url": "https://example.com/"},
                                  run_id, "crawl4ai-self-check-" + os.urandom(4).hex())
            if answer.get("status") == "OK":
                return True, ""
            reason = answer.get("primary_reason_code") or answer.get("reason_code")
            if reason in doctor.RUNTIME_FAILURES or answer.get("reason_code") == "CAPABILITY_NOT_AVAILABLE":
                detail = (answer.get("payload") or {}).get("detail", "")
                safe_detail = (detail if isinstance(detail, str) and re.fullmatch(
                    r"[A-Za-z][A-Za-z0-9_]{0,79}:[A-Z0-9_]{1,80}", detail) else "")
                return False, reason + (":" + safe_detail if safe_detail else "")
            return None  # the run's rules refused it: says nothing about the browser
    result = doctor.check(data_root, page_read=page_read)
    return {**result, "tell_the_user": "" if result["status"] == "WORKING"
            else crawl4ai_down_notice(data_root)["tell_the_user"]}


def search(provider, query, country, language, run_id, operation_id, mode="web", limit=10):
    if provider == "all":
        return _search_all(query, country, language, run_id, operation_id, limit)
    if provider == "tinyfish":
        # A third, independent index: it surfaces sites the Google-based two miss.
        answer = managed_call("tinyfish.search", {"query": query, "location": country,
                                                  "language": language}, run_id, operation_id)
        if answer["status"] != "OK":
            raise ValueError(_refusal_text(answer))
        return {**answer, **(answer.get("payload") or {}),
                "invocation": provider_policy.invocation_receipt(
                    "tinyfish_mcp_search", reservation="ONE_ATTEMPT_RESERVED_NOT_BILL_VERIFIED")}
    if provider == "deepseek":
        answer = managed_call("deepseek.search", {"query": query, "country": country,
                                                  "language": language, "limit": limit,
                                                  "mode": mode}, run_id, operation_id)
        if answer["status"] != "OK":
            raise ValueError(_refusal_text(answer))
        return {**answer, **(answer.get("payload") or {}),
                "invocation": provider_policy.invocation_receipt(
                    "deepseek", reservation="ONE_ATTEMPT_RESERVED_NOT_BILL_VERIFIED")}
    if provider not in {"serper", "serpapi"}:
        raise ValueError("CAPABILITY_UNKNOWN")
    if provider == "serpapi" and mode != "web":
        raise ValueError("CAPABILITY_PARAM_NOT_ALLOWED: use an explicitly reviewed engine")
    params = {"query": query, "country": country, "language": language, "limit": limit}
    import serpapi_engine_catalog as _catalog
    params["mode" if provider == "serper" else "engine"] = (
        mode if provider == "serper" else _catalog.DEFAULT_ENGINE)
    answer = managed_call(provider + ".search", params, run_id, operation_id)
    if answer["status"] != "OK":
        raise ValueError(_refusal_text(answer))
    return {**answer, **(answer.get("payload") or {}),
            "invocation": provider_policy.invocation_receipt(
                "serpapi_mcp_search" if provider == "serpapi" else provider,
                reservation="ONE_ATTEMPT_RESERVED_NOT_BILL_VERIFIED")}


def find_official_site(run_id, operation_id, target, country="us", language="en") -> str:
    """The organisation's own site for a target listed by name only, or "".

    One merged Serper + TinyFish search on the published name and place. A hit
    whose host contains a distinctive word of the name wins; otherwise the
    first hit that is not a social network, directory or encyclopaedia.
    """
    name = " ".join(str(target.get("name") or "").split())
    if not name:
        return ""
    place = " ".join(str(target.get("address") or "").split())[:80]
    answer = _search_all(f"{name} {place}".strip(), country or "us", language or "en",
                         run_id, operation_id, 8)
    words = [w for w in re.findall(r"[0-9a-z]+", name.casefold()) if len(w) >= 4]
    candidates = []
    for hit in answer.get("results", []):
        host = (urlsplit(hit["url"]).hostname or "").casefold()
        if not host or delivery.shared_host(host) or "wikipedia." in host:
            continue
        candidates.append((not any(w in host for w in words), hit["url"]))
    if not candidates:
        return ""
    best = sorted(candidates, key=lambda c: c[0])[0][1]
    parts = urlsplit(best)
    return f"https://{parts.hostname}/"


def _simple_site_query(query: str) -> str:
    """site:X plus the first few plain words: what engines honour reliably."""
    sites = re.findall(r"\bsite:\S+", query)
    rest = re.sub(r"\bsite:\S+|\(|\)|\bOR\b|\bAND\b|\"", " ", query)
    words = [w for w in rest.split() if not w.startswith("-") and ":" not in w][:4]
    return " ".join(sites[:1] + words)


#: Independent web indexes asked side by side by provider='all'. Each finds pages the
#: others miss; one that fails, is out of money or at its red line drops out of that
#: search only. Configurable per project: runtime setting search_routes.
SEARCH_ROUTES = ("serper", "deepseek", "serpapi", "tinyfish")


def _search_routes() -> tuple[str, ...]:
    import research_runtime_setup as runtime
    settings = runtime.config(root()) or {}
    chosen = settings.get("search_routes")
    enabled = set(settings.get("providers") or SEARCH_ROUTES)
    routes = tuple(r for r in (chosen if isinstance(chosen, list) else SEARCH_ROUTES)
                   if r in SEARCH_ROUTES and r in enabled)
    return routes or ("serper", "tinyfish")


def _search_all(query, country, language, run_id, operation_id, limit, _retried=False):
    """Every search route on the same query at once; results merged by URL."""
    from concurrent.futures import ThreadPoolExecutor
    def one(provider):
        try:
            return provider, search(provider, query, country, language, run_id,
                                    f"{operation_id}-{provider}", limit=limit)
        except ValueError as exc:
            import research_provider_health as health
            if provider == "serper" and health.blocked(root(), "serper") and "serpapi" not in routes:
                # Serper is out of credit: SerpApi answers the same Google query.
                try:
                    return provider, {**search("serpapi", query, country, language, run_id,
                                               f"{operation_id}-serpapi", limit=limit),
                                      "substituted_by": "serpapi"}
                except ValueError as again:
                    return provider, {"error": str(again)[:160],
                                      "substitute_failed": "serpapi"}
            # The whole message: "IDENTITY_VALUE_INVALID" alone hid which field was wrong.
            return provider, {"error": str(exc)[:160]}
    routes = _search_routes()
    with ThreadPoolExecutor(max_workers=len(routes)) as pool:
        answers = dict(pool.map(one, routes))
    try:
        import research_search_routes
        research_search_routes.record(root(), run_id, query, answers)
    except Exception:  # noqa: BLE001 - the recall log never stops a search
        pass
    merged, seen = [], set()
    for provider, answer in answers.items():
        for hit in _hits(answer):
            url = hit.get("url") or hit.get("link")
            if isinstance(url, str) and url not in seen:
                seen.add(url)
                merged.append({"title": hit.get("title"), "url": url,
                               "snippet": hit.get("snippet") or hit.get("description"),
                               "found_by": answer.get("substituted_by", provider)})
    # A site: query that comes back with other sites (the Paris run got Gmail,
    # Outlook and US Senate pages for "site:urbansoccer.fr (... OR ...) email") is
    # the engine ignoring the operator: those hits are noise, not leads.
    sites = [m.casefold().removeprefix("www.") for m in re.findall(r"\bsite:([^\s)\"']+)", query)]
    dropped = 0
    if sites:
        def on_site(url):
            host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
            return any(host == s.split("/")[0] or host.endswith("." + s.split("/")[0]) for s in sites)
        kept = [hit for hit in merged if on_site(hit["url"])]
        dropped, merged = len(merged) - len(kept), kept
    import research_provider_health as health
    out = {"status": "OK", "query": query, "results": merged,
           "by_provider": {p: (len(_hits(a)) if "error" not in a else a["error"])
                           for p, a in answers.items()},
           "artifact_ids": [x for a in answers.values() for x in a.get("artifact_ids") or []]}
    if dropped or not merged:
        # Search walls belong in the experience file as much as page walls do.
        code = "SEARCH_SITE_OPERATOR_IGNORED" if dropped and not merged else (
            "SEARCH_OFF_SITE_RESULTS" if dropped else "SEARCH_NO_RESULTS")
        try:
            append_event(root(), run_id=run_id, operation_id=f"{operation_id}-wall"[:96],
                         stage="discovery", kind="source", status="hold",
                         provider="search", reason_code=code)
        except Exception:  # noqa: BLE001
            pass
    if dropped:
        out["off_site_results_dropped"] = dropped
        simple = _simple_site_query(query)
        if not merged and not _retried and simple and simple != query:
            # The engine ignored site: on a complex query: ask once more, simply.
            again = _search_all(simple, country, language, run_id, f"{operation_id}-simple"[:90],
                                limit, _retried=True)
            again["original_query"] = query
            again["simplified_query"] = simple
            again["off_site_results_dropped"] = dropped + again.get("off_site_results_dropped", 0)
            return again
        if not merged:
            out["query_hint"] = ("Every result was off the site, also for the simplified query. "
                                 "Harvest the site directly with marketops_site_harvest.")
    substituted = {p: a["substituted_by"] for p, a in answers.items() if a.get("substituted_by")}
    if substituted:
        out["substituted"] = substituted
    exhausted = health.status(root())
    if exhausted:
        out["providers_out"] = {p: e["state"] for p, e in exhausted.items()}
    return out


def _hits(answer):
    for key in ("organic", "results", "organic_results", "items"):
        value = answer.get(key)
        if isinstance(value, list):
            return [h for h in value if isinstance(h, dict)]
    return []


def _jev_ask(run_id: str):
    """``ask(artifact_id, locator, question, options) -> (choice, confidence)`` for
    the harvest's page work; one Jev choice, read around ``locator`` when given."""
    def ask(artifact_id, locator, question, options):
        op = "jp-" + hashlib.sha256(json.dumps(
            [run_id, artifact_id, locator, question, options]).encode()).hexdigest()[:24]
        answer = invoke("jev_choice", run_id, op, document=artifact_id, question=question,
                        options=options, primitive="choice", evidence_query=locator)
        judgment = ((answer.get("payload") or {}).get("judgments") or {}).get("judgment") or {}
        return judgment.get("choice"), judgment.get("confidence")
    return ask


def _screen_question(run_id: str, organization_key: str = "") -> str:
    """The brief's screening question, or "" when screening does not apply.

    The question asks whether the site's owner is a *target*; a pool target is one
    by construction, and the source that listed it said so. Asking anyway drops
    whole families: an OPERATORS target (the office or association that runs the
    venues) reads as "not an indoor sports venue" and is screened out at 0.9+.
    """
    import research_jev_pages
    if organization_key:
        import research_pool
        try:
            if research_pool.is_pool_target(root(), organization_key):
                return ""
        except Exception:  # noqa: BLE001 - a missing pool is not a reason to stop the harvest
            pass
    try:
        snapshot = store.list_run(root(), run_id, limit=50)
    except (ValueError, KeyError):
        return ""
    brief = {}
    for record in snapshot["records"]:
        data = record.get("data") or {}
        if data.get("record_type") == "research_brief":
            brief = data
    if brief.get("screen") is False:
        return ""
    return research_jev_pages.screen_question(brief, str(snapshot.get("objective") or ""))


def _brief_roles(run_id: str) -> str:
    """The brief's roles_wanted, so Jev tags routes without the Agent repeating them."""
    try:
        snapshot = store.list_run(root(), run_id, limit=50)
    except (ValueError, KeyError):
        return ""
    for record in reversed(snapshot["records"]):
        data = record.get("data") or {}
        if data.get("record_type") == "research_brief" and data.get("roles_wanted"):
            roles = data["roles_wanted"]
            return ", ".join(roles) if isinstance(roles, list) else str(roles)
    # Agents often name the roles only in the objective; without this fallback
    # Jev never tags a route in such a Run.
    return str(snapshot.get("objective") or "")


#: Hosts end a tool call at about 60 s; stop opening pages well before that so
#: the call returns its work and the next call resumes.
HARVEST_TIME_BUDGET_SECONDS = 30

ROLE_OPTIONS = ["wanted_role_person", "wanted_role_team_inbox", "other_named_role",
                "generic_inbox", "unclear"]


def jev_triage(run_id, operation_id, artifact_ids, question, options, threshold=0.85,
               concurrency=4):
    """One Jev choice per saved page, run concurrently; a compact table back.

    This is the speed layer: Claude reads the table, not the pages. Rows below
    `threshold` are flagged for Claude to judge from the source. Jev is never
    evidence; only quotes are.
    """
    from concurrent.futures import ThreadPoolExecutor
    if not isinstance(artifact_ids, list) or not 1 <= len(artifact_ids) <= 50:
        raise ValueError("JEV_TRIAGE_ITEMS_INVALID: 1-50 saved artifact ids")
    if not 0.5 <= float(threshold) <= 0.99:
        raise ValueError("JEV_TRIAGE_THRESHOLD_INVALID: 0.5-0.99")

    def ask(index_and_id):
        index, artifact_id = index_and_id
        try:
            answer = invoke("jev_choice", run_id, f"{operation_id}-{index}", document=artifact_id,
                            question=question, options=options, primitive="choice")
        except ValueError as exc:
            return {"artifact_id": artifact_id, "choice": None,
                    "error": str(exc).split(":", 1)[0]}
        judgment = ((answer.get("payload") or {}).get("judgments") or {}).get("judgment") or {}
        confidence = judgment.get("confidence")
        return {"artifact_id": artifact_id, "choice": judgment.get("choice"),
                "confidence": confidence,
                "needs_review": confidence is None or confidence < float(threshold)}

    with ThreadPoolExecutor(max_workers=max(1, min(int(concurrency), 8))) as pool:
        rows = list(pool.map(ask, enumerate(artifact_ids)))
    counts = {}
    for row in rows:
        counts[row.get("choice") or "error"] = counts.get(row.get("choice") or "error", 0) + 1
    return {"run_id": run_id, "question": question, "threshold": threshold, "counts": counts,
            "needs_review": sum(1 for r in rows if r.get("needs_review") or r.get("error")),
            "rows": rows, "judgment_only": True}


def invoke(action, run_id, operation_id, url="", query="", document="",
           question="", options=None, primitive="choice", mcp_params=None,
           paid_limit_usd="", ledger_grant="", evidence_query=""):
    if action == "crawl4ai_local":
        capability, params = "crawl4ai.fetch_page", {"url": url}
    elif action == "tinyfish_mcp_search":
        capability, params = "tinyfish.search", {"query": query}
    elif action == "tinyfish_mcp_fetch":
        capability, params = "tinyfish.fetch_content", {"urls": [url], "format": "html",
            "links": False, "image_links": False, "iframe_links": False, "page_metadata": False}
    elif action == "serpapi_mcp_search":
        from research_runtime_results import normalize_params
        data = dict(mcp_params or {})
        allowed = {"q", "query", "engine", "gl", "hl", "num"}
        if "limit" in data and "num" not in data:
            data["num"] = data.pop("limit")  # the common alias agents reach for
        if set(data) - allowed:
            raise ValueError("CAPABILITY_PARAM_NOT_ALLOWED: unsupported "
                             + ", ".join(sorted(map(str, set(data) - allowed)))
                             + "; allowed " + ", ".join(sorted(allowed)))
        capability, params = "serpapi.search", normalize_params("serpapi.search", data)
    elif action == "jev_choice":
        from research_runtime_results import resolve_excerpt
        if primitive not in {"choice", "noul", "score"}:
            raise ValueError("CAPABILITY_PARAM_NOT_ALLOWED")
        capability, params = "jev." + primitive, {"artifact_id": resolve_excerpt(root(), run_id, document),
                                                 "question": question}
        if primitive == "choice":
            params["options"] = options
            if evidence_query:
                # Read the part of the page around this phrase, not the page head.
                params["evidence_query"] = evidence_query[:60]
        elif primitive == "score":
            params["scale"] = options
    else:
        # Old explicit REST/Cloud paths never silently substitute for a reviewed
        # provider. Paid browser-agent and account mutation remain unavailable.
        raise ValueError("CAPABILITY_NOT_AVAILABLE: action is not in the initial product profile")
    answer = managed_call(capability, params, run_id, operation_id, grant_ref=ledger_grant)
    if answer["status"] != "OK":
        raise ValueError(_refusal_text(answer))
    return {**answer, "status": "SAVED_MATERIAL_NOT_VERIFIED",
            "managed_status": answer["status"], "result": answer.get("payload")}


def _check_scene_fields(facts) -> None:
    """A brief revised mid-Run gets the same scene checks as one given at start:
    rules that do not validate would silently leave every route RULES_UNKNOWN."""
    if not isinstance(facts, dict) or facts.get("record_type") != "research_brief":
        return
    import research_scene
    if facts.get("scene") and research_scene.shipped(facts["scene"]) is None:
        raise ValueError("RESEARCH_BRIEF_INVALID: scene '" + str(facts["scene"])[:60]
                         + "' is not shipped; write scene_rules for this market instead")
    if "scene_rules" in facts:
        problems = research_scene.validate(facts["scene_rules"])
        if problems:
            raise ValueError("RESEARCH_BRIEF_INVALID: " + "; ".join(problems))


def propose(run_id, record_id, stage, kind, entity_key, facts, artifact_ids=None):
    r = root()
    _check_scene_fields(facts)
    result = store.put_candidate(r, run_id, record_id, stage, kind,
                                 entity_key, facts, artifact_ids or [])
    append_event(r, run_id=run_id, operation_id=record_id, stage=stage,
                 kind="entity", status="succeeded", provider="local")
    return result


def propose_batch(run_id, items):
    """Atomic append-only candidates, at most 20 per transaction/tool call."""
    r = root()
    for item in items if isinstance(items, list) else ():
        _check_scene_fields(item.get("facts") if isinstance(item, dict) else None)
    result = store.put_candidates_batch(r, run_id, items)
    operation_id = "batch-" + hashlib.sha256(
        json.dumps([item["record_id"] for item in items], sort_keys=True).encode()).hexdigest()[:24]
    append_event(r, run_id=run_id, operation_id=operation_id,
                 stage="entities", kind="entity", status="succeeded", provider="local")
    return result


def material_excerpt(run_id, artifact_ids, queries=None):
    return evidence.excerpts(root(), run_id, artifact_ids, queries)


def directory_extract(run_id, artifact_id, offset=0, limit=20, stage=False, fields=None):
    """One local read turns a saved list page into organisation records.

    Excerpts are for evidence snippets: a 40 KB annuaire cost thirty calls and
    still left the Agent writing its own scraper. This reads the saved page once
    — the same bytes and decoder the evidence gate uses — splits it by its own
    repeating labels and returns candidates that quote the page they came from.
    """
    import research_extract

    r = root()
    store._id(run_id, "run_id")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("DIRECTORY_OFFSET_INVALID: offset must be 0 or more")
    if not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("DIRECTORY_LIMIT_INVALID: pass 1-20 items per call and "
                         "raise offset to read the next page")
    material = store.read_artifact(r, run_id, artifact_id)
    if material["material_kind"] == "image":
        raise ValueError("DIRECTORY_ARTIFACT_IS_IMAGE: save the page as HTML or text first")
    text = research_extract.page_text(material["material_kind"], material["content"])
    extracted = research_extract.extract(text, labels=fields)
    records = extracted["records"]
    bundles = research_extract.records_to_bundles(text, records, artifact_id)

    page, used, next_offset = [], 0, None
    for index, bundle in enumerate(bundles[offset:], start=offset):
        if page and used + len(bundle) > limit:
            next_offset = index
            break
        page.extend(bundle)
        used += len(bundle)

    public = [{key: value for key, value in record.items() if not key.startswith("_")}
              for record in records]
    kept = []
    for record in public:
        if len(json.dumps(kept + [record], ensure_ascii=False).encode("utf-8")) > 3_500_000:
            break
        kept.append(record)
    content = json.dumps({"source_artifact_id": artifact_id, "records": kept},
                         ensure_ascii=False).encode("utf-8")
    extracted_id = "dir-" + hashlib.sha256(content).hexdigest()[:24]
    store.save_artifact(r, run_id, extracted_id, "marketops-extract",
                        material["origin_url"], content, "provider_extracted")

    response = {
        "status": "DIRECTORY_EXTRACTED_NOT_VERIFIED",
        "run_id": run_id,
        "source_artifact_id": artifact_id,
        "artifact_id": extracted_id,
        "records": len(records),
        "with_email": sum(1 for record in records if record.get("email")),
        "with_person": sum(1 for record in records if record.get("person")),
        "without_organization": sum(1 for record in records if not record.get("organization")),
        "anchor": extracted["anchor"],
        "start_field": extracted.get("start_field"),
        "dropped_duplicates": extracted["dropped_duplicates"],
        "warnings": extracted["warnings"],
        "items_total": sum(len(bundle) for bundle in bundles),
        "offset": offset,
        "returned": len(page),
        "next_offset": next_offset,
        "items": page,
        "facts_verified": False,
        "note": ("items are grouped per record (organisation, person, address) and every "
                 "quote is exact saved-page text; stage them with marketops_propose_batch "
                 "or write them to a pool with marketops_pool_import"),
    }
    if stage and page:
        response["staged"] = propose_batch(run_id, page)
    return response


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


def handoff(run_id, operation_id, mode="auto"):
    """One audited workbook snapshot plus experience artifacts and an honest receipt.

    Counts come from the workbook manifest, not a preceding status query. A child
    delivery's partial failure may never disappear at this orchestration boundary.
    Final experience refresh happens after the handoff event, including failures.
    """
    r = root()
    store._id(run_id, "run_id")
    store._id(operation_id, "operation_id")
    if mode not in {"auto", "checkpoint", "final"}:
        raise ValueError("HANDOFF_MODE_INVALID: auto, checkpoint or final")
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
        # The client view is produced by write_delivery from the filtered business
        # sheets, not by the caller. Expose its path and content hash so the
        # handoff receipt describes the same bytes a customer would receive.
        "client_view_directory": workbook.get("client_view_directory"),
        "client_view_manifest": workbook.get("client_view_manifest"),
        "client_view_manifest_sha256": (workbook.get("client_view") or {}).get(
            "manifest_sha256"),
        "client_view_status": (workbook.get("client_view") or {}).get("status"),
        "client_view_send_authorized": (workbook.get("client_view") or {}).get(
            "send_authorized"),
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
            rule_version=head["rule_version"], skill_version="0.5.11-rc13",
            output=experience_path)
        result["experience_candidate"] = str(experience_path)
        result["experience_incidents"] = experience_result["incidents"]
    except Exception as exc:
        result["experience_candidate"] = None
        result["experience_incidents"] = None
        reasons.append("EXPERIENCE_DRAFT_FAILED_" + type(exc).__name__)

    try:
        result["experience_feedback"] = experience_memory.summary(r, run_id=run_id)
    except Exception as exc:
        result["experience_feedback"] = {"status": "UNAVAILABLE",
                                         "reason_type": type(exc).__name__}
        reasons.append("EXPERIENCE_FEEDBACK_SUMMARY_FAILED")

    import research_execution
    try:
        execution = research_execution.assess(r, run_id, goal=workbook.get("goal_assessment") or {})
    except Exception as exc:
        reasons.append("EXECUTION_ASSESSMENT_FAILED_" + type(exc).__name__)
        execution = {"state": "CHECKPOINT_EXECUTION_STATE_UNAVAILABLE", "is_final": False,
                     "continuation_required": True, "next_action": "RESTORE_EXECUTION_STATE_BEFORE_FINALIZING",
                     "reason_type": type(exc).__name__}
    try:
        append_event(r, run_id=run_id, operation_id=operation_id,
                     stage="handoff", kind="handoff", status="hold" if reasons else "succeeded",
                     provider="local", reason_code="partial_handoff" if reasons else
                     "research_checkpoint" if not execution["is_final"] or mode == "checkpoint" else "")
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
    result["execution"] = execution
    result["is_final"] = execution["is_final"] and mode != "checkpoint" and not reasons
    result["continuation_required"] = not result["is_final"]
    result["delivery_status"] = result["status"]
    if not result["is_final"] and not reasons:
        result["status"] = "CHECKPOINT_RESEARCH_NOT_FINISHED"
    if mode == "final" and not result["is_final"]:
        result["finalization_refused"] = "RESEARCH_OR_LOCAL_FINALIZATION_REMAINS"
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


#: Which research stage a refused tool belongs to, for the experience journal.
_TOOL_STAGES = {"marketops_search": "discovery", "marketops_serpapi_mcp_search": "discovery",
                "marketops_research_step": "acquisition", "marketops_pool_import": "discovery",
                "marketops_venue_groups": "discovery", "marketops_site_harvest": "acquisition",
                "marketops_harvest_batch": "acquisition",
                "marketops_directory_extract": "entities",
                "marketops_jev_choice": "qualification",
                "marketops_jev_triage": "qualification", "marketops_propose_batch": "entities",
                "marketops_evidence_audit": "review", "marketops_handoff": "handoff",
                "marketops_material_excerpt": "acquisition"}


def _journal_refusal(fn, args, kwargs, reason: str) -> None:
    """A refused tool call is a wall too: record its code against the Run.

    BULK_ROWS_INVALID, FALLBACK_TASK_COUNTRY_REQUIRED and the like reached the
    agent and nowhere else, so the experience file could not show them.
    """
    try:
        fields = inspect.signature(fn).bind_partial(*args, **kwargs).arguments
        run_id = fields.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return
        code = (re.match(r"[A-Z][A-Z0-9_]{2,80}", reason) or [None])[0] or "TOOL_REFUSED"
        op = "refusal-" + hashlib.sha256(f"{fn.__name__}:{run_id}:{reason}:{datetime_now()}"
                                         .encode()).hexdigest()[:24]
        append_event(root(), run_id=run_id, operation_id=op,
                     stage=_TOOL_STAGES.get(fn.__name__, "review"), kind="tool",
                     status="blocked", provider="local", reason_code=code)
    except Exception:  # noqa: BLE001 - recording a refusal never hides it
        pass


def datetime_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


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
            response = fn(*args, **kwargs)
            if fn.__name__ in {"marketops_managed_call", "marketops_search", "marketops_crawl",
                               "marketops_tinyfish", "marketops_serpapi_mcp_search",
                               "marketops_jev_choice"} and isinstance(response, dict):
                from research_response_view import compact_provider_response
                fields = inspect.signature(fn).bind_partial(*args, **kwargs).arguments
                if fields.get("run_id"):
                    response = compact_provider_response(root(), fields["run_id"], response)
            return response
        except (ValueError, OSError) as exc:
            from mcp.server.mcpserver.exceptions import ToolError
            response = {"status": "BLOCKED", "tool": fn.__name__,
                        "reason": str(exc)}
            _journal_refusal(fn, args, kwargs, str(exc))
            route = _blocked_call_route(fn, args, kwargs)
            if route is not None:
                response["invocation"] = route
            raise ToolError(json.dumps(response, ensure_ascii=False)) from exc
    return wrapper


#: What an Agent sees. Every other function stays importable for tests, the
#: installer and power users, but a smaller surface is what makes a host pick
#: the right tool: duplicates (propose vs propose_batch, crawl/tinyfish vs the
#: fallback step), unavailable paid entries and maintenance views are hidden.
PUBLIC_TOOLS = frozenset({
    "marketops_setup_status", "marketops_setup_configure", "marketops_runs",
    "marketops_start", "marketops_next", "marketops_status",
    "marketops_search", "marketops_serpapi_mcp_search", "marketops_research_step",
    "marketops_site_harvest", "marketops_jev_choice", "marketops_material_excerpt",
    "marketops_propose_batch", "marketops_evidence_audit", "marketops_note",
    "marketops_handoff", "marketops_experience_recall", "marketops_experience_feedback",
    "marketops_pool_import", "marketops_pool_claim", "marketops_pool_complete",
    "marketops_pool_status", "marketops_harvest_batch", "marketops_lessons_report",
    "marketops_jev_triage", "marketops_suppression", "marketops_feedback_pack",
    "marketops_pool_reopen", "marketops_costs", "marketops_update_check", "marketops_accounts",
    "marketops_crawl4ai_check", "marketops_method", "marketops_venue_groups",
    "marketops_search_routes", "marketops_directory_extract",
})


#: Tools that can spend: their result carries the budget left afterwards, so
#: an Agent sees cost as it works instead of discovering exhaustion later.
BUDGETED_TOOLS = frozenset({
    "marketops_search", "marketops_serpapi_mcp_search", "marketops_research_step",
    "marketops_site_harvest", "marketops_jev_choice", "marketops_harvest_batch",
    "marketops_jev_triage",
})


def _with_budget(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        result = fn(*args, **kwargs)
        run_id = kwargs.get("run_id")
        if isinstance(result, dict) and isinstance(run_id, str):
            try:
                result["budget_after"] = _source_selection_budget(run_id)
            except Exception:  # noqa: BLE001 - an unreadable budget is unknown
                result["budget_after"] = {"source": "UNAVAILABLE"}
        return result
    return wrapper


def build_server():
    import research_code_drift  # noqa: F401 - the fingerprint is taken at server start
    from research_credentials import load_into_environment
    load_into_environment()
    try:
        import research_key_slots
        research_key_slots.apply(root())
    except (ValueError, OSError):
        pass  # no data root yet: slot 1, as configured
    # Direct by default; only an explicitly configured proxy changes that.
    research_config.load_upstream_proxy_into_environment()
    _use_installed_browser()
    try:
        # A launch mode chosen on this computer (installer, check or repair) applies
        # to every crawl, Crawl4AI included.
        import research_browser_launch
        research_browser_launch.install_launch_default()
    except Exception:  # noqa: BLE001 - the default launch still runs
        pass
    _ensure_repair_entrypoints()
    from mcp.server import MCPServer
    mcp = MCPServer("MarketOps Lite Research", version="0.5.11-rc13")
    _quiet_http_logs()  # after the server set up its own logging

    def tool(fn):
        if fn.__name__ not in PUBLIC_TOOLS:
            return fn
        if fn.__name__ in BUDGETED_TOOLS:
            return mcp.tool()(_guarded(_with_budget(fn)))
        return mcp.tool()(_guarded(fn))

    @tool
    def marketops_setup_status() -> dict:
        """Read-only: are the two first-use paths chosen? Never creates or changes anything."""
        changed = research_code_drift.drift()
        status = setup_status()
        return {**status, "restart_required": changed} if changed else status

    @tool
    def marketops_setup_configure(data_root: str, experience_file: str,
                                  confirm: bool = False) -> dict:
        """Persist the two paths the user answered once: research data dir, experience file."""
        return setup_configure(data_root, experience_file, confirm=confirm)

    @tool
    def marketops_note(run_id: str, note_id: str, stage: str, category: str,
                       problem: str, action_taken: str, observed_result: str,
                       hypothesis: str = "", evidence_ref: str = "") -> dict:
        """Record a real observation, action and result, without URLs, emails or secrets.

        category: contact_found_not_stored, field_template_pollution,
        artifact_without_delivery, source_blocked, provider_blocked,
        evidence_missing, human_correction or other. evidence_ref identifies an
        actual saved artifact/event; use the receipt ID, never an invented ref.
        """
        return note(run_id, note_id, stage, category, problem, action_taken,
                    observed_result, hypothesis, evidence_ref)

    @tool
    def marketops_capabilities(host_observed_tools: list[str] | None = None,
                               task_kind: Literal["source_discovery", "known_public_url", "batch_public_pages",
                                                  "dynamic_navigation", "authorized_login_read",
                                                  "semantic_choice", "semantic_yes_no", "semantic_score"] | None = None,
                               tinyfish_wallet_snapshot: dict | None = None) -> dict:
        """Read actual runtime readiness and budget. task_kind is a tool activity, not the Brief goal.

        Use source_discovery for search, known_public_url for fetching a page,
        semantic_choice for judgment; omit task_kind to inspect all capabilities.
        """
        return capabilities(host_observed_tools, task_kind, tinyfish_wallet_snapshot)

    @tool
    def marketops_start(run_id: str, scope: str, objective: str, rule_version: str,
                        brief: dict | None = None) -> dict:
        """Start/resume and save a valid Brief before network spend. Reuse existing scope/rule.

        Minimal brief: {record_type: research_brief, target_kind: organization,
        goal: contacts, require_named_person: false, minimum_targets: null,
        evidence_requirements: {organization: [identity]}}.
        target_kind is venue or organization; goal is pool or contacts.
        evidence_requirements maps candidate kinds to arrays of field names,
        never narrative strings. The narrative belongs in objective (1–300 chars).
        """
        return start(run_id, scope, objective, rule_version, brief)

    @tool
    def marketops_search(provider: str, query: str, country: str, language: str,
                         run_id: str, operation_id: str, mode: str = "web",
                         limit: int = 10) -> dict:
        """provider='all' (default choice for discovery) runs every search route the
        project enables (Serper, DeepSeek, SerpApi, TinyFish) on the same query at
        once and merges results with found_by; a route with no money or a failure
        only drops out. Or one of serper, deepseek, serpapi, tinyfish. Snippets are
        leads, not facts. marketops_search_routes shows what each route adds."""
        return search(provider, query, country, language, run_id, operation_id, mode, limit)

    @tool
    def marketops_search_routes(run_id: str) -> dict:
        """Recall per search route in this Run: pages found, pages only it found, and
        which route found the sites the A/B routes came from; and per page reader
        (Crawl4AI, TinyFish fetch, DeepSeek Fetch): pages read, failures by reason,
        addresses found, pages only it read addresses from. Read-only; use it to
        decide which routes to keep when money is short."""
        import research_search_routes
        import research_sources
        grades, _, _ = research_sources.graded(root(), run_id)
        _, records, artifacts = delivery._read_snapshot(root(), run_id)
        urls = {a["artifact_id"]: a["url"] for a in artifacts}
        by_id = {r["record_id"]: r for r in records}
        hosts = set()
        for record_id, grade in grades.items():
            if grade["grade"] in {"A", "B"}:
                for artifact in by_id.get(record_id, {}).get("artifact_ids") or ():
                    host = (urlsplit(urls.get(artifact, "")).hostname or "").casefold()
                    if host:
                        hosts.add(host.removeprefix("www."))
        import research_fetch_routes
        return {**research_search_routes.report(root(), run_id, hosts),
                "page_readers": research_fetch_routes.report(root(), run_id)}

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
        """Embedded official TinyFish MCP Search/Fetch. Legacy REST selection is refused."""
        options = {"mcp": {"search": "tinyfish_mcp_search", "fetch": "tinyfish_mcp_fetch"},
                   "rest": {"search": "tinyfish_search", "fetch": "tinyfish_fetch"}}
        action = options.get(transport, {}).get(task, "unsupported")
        return invoke(action, run_id, operation_id, url=url, query=query)

    @tool
    def marketops_serpapi_mcp_search(run_id: str, operation_id: str,
                                    params: dict) -> dict:
        """Embedded official SerpApi MCP search; any valid engine, compact mode.

        params keys: q (or query), engine (e.g. google, google_maps), gl, hl, num.

        Both search tool names use the same embedded MCP adapter and ledger identity.
        """
        return invoke("serpapi_mcp_search", run_id, operation_id, mcp_params=params)

    @tool
    def marketops_tinyfish_agent(run_id: str, operation_id: str, url: str,
                                 goal: str, max_steps: int, max_duration_seconds: int,
                                 max_charge_usd: str) -> dict:
        """Compatibility entry; paid TinyFish Agent is unavailable in this research profile.

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
        """Compatibility entry; TinyFish Agent polling is unavailable in this research profile.

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
        """Jev choice / noul / score on one saved page; ranks and filters, never a fact.

        source_excerpt: a saved artifact_id of this Run (or a quote that occurs
        in exactly one saved artifact). options: snake_case labels such as
        ["hourly_hire", "events_only", "unknown"]; noul takes [], score takes
        ordered level labels.
        """
        return invoke("jev_choice", run_id, operation_id, document=source_excerpt,
                      question=question, options=options, primitive=primitive)

    @tool
    def marketops_propose(run_id: str, record_id: str, stage: str,
                          kind: str, entity_key: str, facts: dict,
                          artifact_ids: list[str] | None = None) -> dict:
        """Persist one candidate now, including input targets; missing evidence remains unverified.

        stage/kind: entities/organization (facts.name); people/role
        (facts.organization_id, role_title OR role_function); routes/contact_point
        (facts.organization_id, type, value); review/review (facts.review_type=
        target_disposition, target_kind, target_entity_key, disposition, reason).
        Joins use entity_key, never record_id. Named roles/contacts need person_id.
        facts.claims=[{field,artifact_id,quote}], at most12; quote12..600 chars
        from a listed same-Run artifact. Organization/venue require identity;
        use the Brief's additional evidence fields. Unknowns must remain unknown.
        Corrections: new record_id, same kind/entity_key, facts.supersedes=old ID.
        All writes remain candidates; no approval or outreach authority.
        """
        return propose(run_id, record_id, stage, kind, entity_key, facts, artifact_ids)

    @tool
    def marketops_propose_batch(run_id: str, items: list[dict]) -> dict:
        """Persist 1–20 candidates atomically; do this after each 1–2 targets, before more searches.

        Every item has record_id, stage, kind, entity_key, facts, artifact_ids.
        Full example shape (replace every example value with actual input/evidence):
        {record_id:'org-record-1',stage:'entities',kind:'organization',
         entity_key:'org-1',facts:{name:'actual organization name',
         claims:[{field:'identity',artifact_id:'actual-artifact-id',
         quote:'actual published fragment, 12..600 chars'}]},
         artifact_ids:['actual-artifact-id']}.
        For input-only unknown targets, facts:{name:<input name>},artifact_ids:[]
        is valid and remains unverified; do not wait for every website first.
        Register all supplied target names first (batches <=20), then research
        1..2 targets per batch. Other kinds/joins use marketops_next.candidate_write_contract. Use
        stage='routes',kind='contact_point' for a literal contact channel.
        A review uses facts:{review_type:'target_disposition',target_kind:
        'organization',target_entity_key:<org entity_key>,disposition:'HOLD',
        reason:<observed gap>}; no invented quote is needed for HOLD.
        A failed item rolls back the whole batch; fix its error before retrying.
        """
        return propose_batch(run_id, items)

    @tool
    def marketops_material_excerpt(run_id: str, artifact_ids: list[str] | None = None,
                                   queries: list[str] | None = None,
                                   artifact_id: str = "") -> dict:
        """Get SHA-checked snippets: 1–3 artifact IDs and at most 3 query strings (2–60 chars each).

        artifact_id (one ID) is accepted in place of artifact_ids. The total excerpt
        is capped at 1200 characters; make a separate focused read if more evidence
        is needed, rather than requesting four queries.
        """
        if artifact_id and not artifact_ids:
            artifact_ids = [artifact_id]
        if not artifact_ids:
            raise ValueError("EXCERPT_ARTIFACT_LIMIT: pass artifact_ids=[...] (1-3 IDs)")
        return material_excerpt(run_id, artifact_ids, queries)

    @tool
    def marketops_directory_extract(run_id: str, artifact_id: str, offset: int = 0,
                                    limit: int = 20, stage: bool = False,
                                    fields: list[str] | None = None) -> dict:
        """Read a saved list page in full and return one record per organisation.

        Use it on any saved annuaire, club finder or directory page (an artifact
        from marketops_research_step or marketops_site_harvest): it splits the page
        by its own repeating labels ("Nom de la personne à contacter :", "Adresse
        mail :") and returns ready-to-stage candidates — organisation, person and
        email — each quoting the page. No excerpt ceiling, no per-page cost.

        Returns 1-20 items per call (one record = 2-3 items); raise offset to read
        the next page. stage=true writes them as candidates (batches of 20) so the
        Run can grade them. fields narrows the output; omit it for everything.
        """
        return directory_extract(run_id, artifact_id, offset, limit, stage, fields)

    @tool
    def marketops_evidence_audit(run_id: str, limit: int = 12) -> dict:
        """Compact, generic claim-to-source mismatch report, not a fact certification."""
        return evidence_audit(run_id, limit)

    @tool
    def marketops_research_step(run_id: str, operation_id: str,
                                task_kind: Literal["source_discovery", "known_public_url",
                                                   "batch_public_pages", "semantic_choice"],
                                query: str = "", url: str = "", artifact_id: str = "",
                                question: str = "", options: list[str] | None = None,
                                country: str = "", language: str = "",
                                limit: int = 5, max_attempts: int = 3,
                                as_is: bool = False) -> dict:
        """Run one bounded research step with recorded, admitted fallback channels.

        task_kind: source_discovery, known_public_url, batch_public_pages (one
        URL per step), or semantic_choice. Unknown charges, timeouts, access
        restrictions and exhausted shared budgets stop the chain. Results are
        candidate materials; read the returned artifact IDs and verify claims.
        A first-page open-data record URL (Opendatasoft, CKAN, Socrata) is read
        as the portal's bulk export of the same filter; the reply says so in
        bulk_export. as_is=true reads the URL exactly as given.
        """
        import research_fallback
        import research_runtime_setup
        data_root = root()
        _require_brief_for_network(data_root, run_id)
        rewrite = None
        if url and not as_is and task_kind in {"known_public_url", "batch_public_pages"}:
            import research_portals
            exported, portal = research_portals.bulk_url(url)
            if portal:
                rewrite = {"portal": portal, "requested_url": url, "read_url": exported,
                           "note": "Read as one export instead of 100-row pages; import it with "
                                   "marketops_pool_import in artifact mode. Over "
                                   f"{research_portals.MAX_EXPORT_ROWS} rows: narrow the filter "
                                   "(one region per call)."}
                url = exported
        result = research_fallback.research_step(
            data_root, run_id=run_id, operation_id=operation_id, task_kind=task_kind,
            managed_call=managed_call,
            admission=lambda capability: research_runtime_setup.admission(data_root, capability),
            query=query, url=url, artifact_id=artifact_id, question=question, options=options,
            country=country, language=language, limit=limit, max_attempts=max_attempts)
        if rewrite and isinstance(result, dict):
            result["bulk_export"] = rewrite
        return result

    @tool
    def marketops_site_harvest(run_id: str, operation_id: str, organization_name: str,
                               organization_key: str, url: str, max_pages: int = 6,
                               write: bool = True, roles_wanted: str = "") -> dict:
        """Harvest one organisation's OWN site into quoted, gate-checked candidates.

        Fetches url, then up to max_pages-1 same-host contact/people/hire pages
        (Crawl4AI, TinyFish fetch fallback), cuts literal quotes for the name and
        every email/phone, stages organization + contact_point candidates and
        returns each record's delivery-gate decision. Each route carries its
        surrounding context: you assign role and grade. Never use on a directory.
        Jev tags each email with who it reaches (roles from roles_wanted or the
        brief's roles_wanted); read the page only for low-confidence tags.
        """
        import research_site_harvest
        data_root = root()
        _require_brief_for_network(data_root, run_id)

        fetch = _fetcher(data_root, run_id)
        return research_site_harvest.harvest(
            data_root, run_id=run_id, operation_id=operation_id,
            organization_name=organization_name, organization_key=organization_key,
            url=url, fetch=fetch, max_pages=max_pages, write=write,
            grade=_role_grader(run_id, roles_wanted),
            time_budget_seconds=HARVEST_TIME_BUDGET_SECONDS,
            focus=roles_wanted.strip() or _brief_roles(run_id),
            jev_ask=_jev_ask(run_id), screen_question=_screen_question(run_id, organization_key))

    def _role_grader(run_id, roles_wanted):
        roles_wanted = roles_wanted.strip() or _brief_roles(run_id)
        if not roles_wanted:
            return None
        import hashlib

        def grade(route):
            op = "role-" + hashlib.sha256(
                f"{run_id}:{route['artifact_id']}:{route['value']}".encode()).hexdigest()[:20]
            answer = invoke("jev_choice", run_id, op, document=route["artifact_id"],
                            question=(f"Who does the contact {route['value']} on this page belong "
                                      f"to? Wanted roles: {roles_wanted.strip()[:200]}."),
                            options=ROLE_OPTIONS, primitive="choice",
                            # Around the address: the page head may not even print it.
                            evidence_query=route["value"] if len(route["value"]) <= 60 else "")
            judgment = ((answer.get("payload") or {}).get("judgments") or {}).get("judgment") or {}
            return {"role": judgment.get("choice") or "unclear",
                    "confidence": judgment.get("confidence")}
        return grade

    def _fetcher(data_root, run_id):
        import research_fallback
        import research_fetch_routes
        import research_runtime_setup

        def step(page_url, child, routes=None):
            return research_fallback.research_step(
                data_root, run_id=run_id, operation_id=child, task_kind="known_public_url",
                managed_call=managed_call,
                admission=lambda capability: research_runtime_setup.admission(data_root, capability),
                url=page_url, max_attempts=3, routes=routes)

        def fetch(page_url, child):
            return research_fetch_routes.fetch(data_root, run_id, page_url, child, step)
        return fetch

    @tool
    def marketops_pool_import(pool_id: str, source_id: str, source_url: str = "", provider: str = "",
                              listed_count: int | None = None, targets: list[dict] | None = None,
                              objective: str = "", scene: str = "", run_id: str = "",
                              artifact_id: str = "", mapping: dict | None = None,
                              filters: dict | None = None, offset: int = 0,
                              limit: int = 500, preview: bool = False,
                              family: str = "",
                              operator_covered_types: list[str] | None = None) -> dict:
        """Add one source's targets to the shared pool every window works from.

        operator_covered_types (saved-JSON imports): the source's own values for
        venues run by a public body or chain whose staff sit at the operator (Data
        ES equip_gest_type e.g. ["Commune", "Etat", "Département", "Région"]). Such a
        venue without its own website is kept as a venue but not queued: research
        its operator with marketops_venue_groups instead.

        family: which kind of organisation this source lists (OPERATORS,
        CLUBS_ASSOCIATIONS, SCHOOLS_UNIVERSITIES, FEDERATION_LISTS). A contacts goal
        pilots every family before it may finish (marketops_next execution).

        targets: [{name, website?, address?, kind?, priority?}]. listed_count is how
        many entities the source itself listed; the reply says whether imported +
        duplicates + rejected reconcile with it. Dedupe: name + postcode/region.
        For saved JSON pass run_id/artifact_id and preview=true to inspect fields,
        then mapping={name:source_field,indoor:source_field,...} and literal-value
        filters={source_field:[allowed_values]}. Local pagination consumes no
        provider requests; follow next_offset, then upstream_pagination_remaining.
        """
        import research_pool
        import research_sources
        if family:
            research_sources.set_family(root(), pool_id, source_id, family)
        if artifact_id:
            if targets is not None:
                raise ValueError("BULK_OR_EXPLICIT_TARGETS_NOT_BOTH")
            import research_bulk
            return research_bulk.import_saved(root(), run_id=run_id, artifact_id=artifact_id,
                pool_id=pool_id, source_id=source_id, mapping=mapping, filters=filters,
                offset=offset, limit=limit, preview=preview, objective=objective, scene=scene,
                operator_covered_types=operator_covered_types)
        return research_pool.import_targets(
            root(), pool_id=pool_id, source_id=source_id, source_url=source_url,
            provider=provider, listed_count=listed_count, targets=targets,
            objective=objective, scene=scene)

    @tool
    def marketops_pool_claim(pool_id: str, worker_id: str, count: int = 20,
                             lease_minutes: int = 30) -> dict:
        """Lease up to `count` targets to this window; expired leases are re-claimable."""
        import research_pool
        return research_pool.claim(root(), pool_id=pool_id, worker_id=worker_id,
                                   count=count, lease_minutes=lease_minutes)

    @tool
    def marketops_pool_complete(pool_id: str, worker_id: str, results: list[dict]) -> dict:
        """Report outcomes for targets this window holds.

        results: [{target_key, outcome: done|hold|excluded|duplicate, reason, run_id,
        organization_key, routes}]. A reason is required for anything but done.
        """
        import research_pool
        return research_pool.complete(root(), pool_id=pool_id, worker_id=worker_id,
                                      results=results)

    @tool
    def marketops_pool_status(pool_id: str) -> dict:
        """Shared progress: counts by status, sources reconciled, active windows, hit rate."""
        import research_pool
        return research_pool.status(root(), pool_id=pool_id)

    @tool
    def marketops_costs(action: str = "report", run_id: str = "", provider: str = "",
                        capability: str = "", usd_per_unit: str = "", plan: str = "",
                        left: float | None = None, unit: str = "", account_ref: str = "",
                        since: str = "", amount_usd: str = "", keep: str = "",
                        limit: str = "", user_words: str = "") -> dict:
        """Money, not just call counts: spend per project/Run/provider and account balances.

        action="report": estimated spend (all Runs, or run_id), free allowances left.
        action="balances": read SerpApi, Serper and TinyFish accounts now (free reads);
          Jev has no balance API, so it is computed from token usage since the last
          balance a person entered.
        action="set_rate": the user's own price, e.g. provider="serpapi",
          capability="search", usd_per_unit="0.015", plan="Developer".
        action="set_balance": what a provider console shows, e.g. provider="serper",
          left=2380, unit="credit".
        Estimates say so; a provider account reading is the authority.
        since: the usage_checkpoint.since returned by marketops_start; omitting
        it reports cumulative Run totals. action=budget reads money envelopes.
        action=set_budget with amount_usd and optional provider narrows an
        already approved envelope; it cannot grant additional spending.
        action=set_red_line with provider and keep (the amount to leave in the
        account, in its unit): raising it is always allowed; lowering it spends more
        and needs user_words, the user's own words asking for it, kept on record.
        action=set_limit with limit and provider (in that account's unit) or no
        provider (the total, in USD): how much research may spend. Lowering is always
        allowed; raising needs user_words. Either action turns an older fixed-share
        policy into red lines. When the user asks to spend more mid-Run, use these;
        never a new Run or a reset.
        """
        import research_balances
        import research_costs
        data_root = root()
        if action == "report":
            return research_costs.report(data_root, run_id=run_id or None, since=since)
        if action == "budget":
            import research_spending
            return research_spending.status(data_root)
        if action == "set_budget":
            import research_spending
            return research_spending.tighten(data_root, amount_usd=amount_usd, provider=provider)
        if action == "set_limit":
            import research_spending
            if limit == "":
                raise ValueError("LIMIT_REQUIRED: limit (and provider, or none for the total)")
            return research_spending.set_limit(data_root, provider=provider, limit=limit,
                                               user_words=user_words)
        if action == "set_red_line":
            import research_spending
            if not provider or keep == "":
                raise ValueError("RED_LINE_ARGUMENTS_REQUIRED: provider and keep")
            return research_spending.move_red_line(data_root, provider=provider, keep=keep,
                                                   user_words=user_words)
        if action == "balances":
            return research_costs.balances(data_root)
        if action == "set_rate":
            return research_costs.set_rate(data_root, provider=provider, capability=capability,
                                           usd_per_unit=usd_per_unit or None, plan=plan,
                                           account_ref=account_ref)
        if action == "set_balance":
            if left is None:
                raise ValueError("BALANCE_REQUIRED: left=<number shown in the console>")
            return research_balances.record_manual(data_root, provider=provider, left=left,
                                                   unit=unit or "credit", account_ref=account_ref)
        raise ValueError("COSTS_ACTION_INVALID: report, balances, budget, set_rate, set_balance, "
                         "set_budget, set_limit or set_red_line")

    @tool
    def marketops_accounts(action: str = "status", account_ref: str = "") -> dict:
        """List billing accounts/key aliases, or claim a once-only balance question.

        No key values or owner emails are returned. action='ask_balance' atomically
        returns ask_now=true once per account; record the answer with marketops_costs
        set_balance, or continue with UNKNOWN if the user does not know. Account
        registration and key changes use the local research_accounts.py CLI (stdin).
        action='slots' shows which Serper/SerpApi key slot is in use; action='use_slot',
        account_ref='serper:2' switches to the second key. Switch only when the user
        tells you to (a slot is one of their accounts); never to dodge a limit yourself.
        """
        import research_accounts
        if action == "status":
            return research_accounts.status(root())
        if action in {"use_slot", "slots"}:
            # Serper/SerpApi second key: switched only on the user's word.
            import research_key_slots
            if action == "slots":
                return research_key_slots.status(root())
            provider, _, slot = account_ref.partition(":")
            if not slot.isdigit():
                raise ValueError("KEY_SLOT_INVALID: account_ref='serper:2' or 'serpapi:1'")
            return research_key_slots.use(root(), provider, int(slot))
        if action == "ask_balance":
            accounts = research_accounts.status(root())["accounts"]
            if account_ref not in {a["account_ref"] for a in accounts}:
                raise ValueError("ACCOUNT_NOT_REGISTERED")
            return research_accounts.claim_balance_question(root(), account_ref)
        raise ValueError("ACCOUNT_ACTION_INVALID")

    @tool
    def marketops_update_check(include_prerelease: bool = False) -> dict:
        """Is a newer MarketOps release on GitHub? Read-only; installs nothing.

        Call at the start of a task. If one is available, tell the user in one line
        and how to update: run Update.cmd (Windows) or update.sh in the MarketOps
        folder, then restart this agent. Never run the update yourself mid-task.
        """
        return update_check(include_prerelease)

    @tool
    def marketops_method(run_id: str, method: str = "", outcome: str = "", note: str = "") -> dict:
        """Which research methods this Run has tried, and the next one to use.

        Without method: the status of both ladders (contacts, inventory). With
        method and outcome (started, productive, exhausted, not_applicable): record
        it. When a method finds little, record it exhausted and go straight on with
        next_method on the remaining targets; stopping a method never ends the
        research. note: one line on why (counts, not URLs or emails).
        """
        import research_methods
        store.list_run(root(), run_id, limit=1)
        if not method:
            return research_methods.status(root(), run_id)
        if method == "OPERATOR" and outcome == "not_applicable":
            import research_bulk
            groups = research_bulk.venue_groups(root(), run_id=run_id, limit=1)
            if groups["venues_total"]:
                raise ValueError("METHOD_OPERATOR_REQUIRED_FOR_VENUES: this Run holds "
                                 f"{groups['venues_total']} venues; group them by operator or "
                                 "city with marketops_venue_groups and research the operators")
        return research_methods.record(root(), run_id, method, outcome, note)

    @tool
    def marketops_venue_groups(run_id: str, by: str = "operator", operator_types: list[str] | None = None,
                               limit: int = 50, import_to_pool: str = "",
                               country_hint: str = "", name_template: str = "",
                               park_covered_in_pool: str = "") -> dict:
        """The Run's venues grouped by who runs them, largest groups first; no network.

        A venue's decision-makers usually sit at its operator: the municipality's
        sports department for public venues, the company or association otherwise.
        Map operator/operator_type when importing the inventory (marketops_pool_import
        mapping), then group here. by='operator' uses that field; by='city' groups
        municipal venues by town (pass operator_types with the source's own value for
        public/municipal management). name_template names each town's target in the
        local language, e.g. "City of {group}". import_to_pool=<pool_id> adds the
        largest uncovered groups as organisation targets; research them with
        marketops_harvest_batch. park_covered_in_pool=<pool_id> (with operator_types)
        first holds that pool's pending venue rows that have no website and such an
        operator, so the run stops waiting on venue-by-venue searches.
        """
        import research_bulk
        store.list_run(root(), run_id, limit=1)
        parked = None
        if park_covered_in_pool:
            parked = research_bulk.park_operator_covered(
                root(), run_id=run_id, pool_id=park_covered_in_pool, operator_types=operator_types)
        result = research_bulk.venue_groups(root(), run_id=run_id, by=by,
                                            operator_types=operator_types, limit=limit,
                                            import_to_pool=import_to_pool,
                                            country_hint=country_hint,
                                            name_template=name_template)
        return {**result, "parked_venue_targets": parked} if parked else result

    @tool
    def marketops_crawl4ai_check(run_id: str = "", repair: bool = False) -> dict:
        """Is Crawl4AI, the free local page reader, really working on this computer?

        Starts the browser once (and, with the current run_id, reads one test page).
        Call at the start of a task when setup status shows crawl4ai as anything but
        READY. If it is not working, tell the user and offer repair=True, which
        reinstalls Crawl4AI and its browser into this install (a few minutes, about
        200 MB). Never switch to TinyFish-only page reading without the user's yes.
        """
        return crawl4ai_check(run_id, repair)

    @tool
    def marketops_pool_reopen(pool_id: str, reason: str, worker_id: str = "") -> dict:
        """Put HOLD targets back to pending once their cause is fixed.

        reason: the HOLD reason prefix to reopen (e.g. NO_OFFICIAL_SITE_IN_POOL), or
        ALL. Targets already tried 3 times stay HOLD.
        reason="RELEASE_CLAIMS" with worker_id: hand that worker's unfinished leases
        back to pending now, instead of waiting for them to expire.
        """
        import research_pool
        if reason == "RELEASE_CLAIMS":
            if not worker_id:
                raise ValueError("WORKER_ID_REQUIRED: the worker whose leases to release")
            return research_pool.release_worker(root(), pool_id=pool_id, worker_id=worker_id)
        return research_pool.reopen(root(), pool_id=pool_id, reason=reason)

    @tool
    def marketops_suppression(action: str = "status", entries: list[str] | None = None,
                              reason: str = "", source: str = "", note: str = "") -> dict:
        """The shared No-Go / unsubscribe / complaint list every Run's delivery obeys.

        action="status": entry counts; NO_LIST means none was loaded, not clean.
        action="add": entries are emails or domains (example.org / @example.org);
        reason is unsubscribe|complaint|no_go|competitor|bounce|other; source says
        where the list came from. Matching routes never reach a client sheet.
        Removing an entry is a person's decision and is not offered here.
        """
        import research_suppression
        if action == "status":
            return research_suppression.status(root())
        if action == "add":
            return research_suppression.add_entries(
                root(), entries=list(entries or []), reason=reason, source=source, note=note)
        raise ValueError("SUPPRESSION_ACTION_INVALID: status or add")

    @tool
    def marketops_harvest_batch(pool_id: str, worker_id: str, run_id: str, count: int = 20,
                                concurrency: int = 4, max_pages: int = 5,
                                roles_wanted: str = "", country: str = "",
                                language: str = "") -> dict:
        """Claim `count` targets and harvest each own site in one call; summary only.

        Targets listed without a website (or with only a social/directory page) get
        their official site looked up first by name and place; give country and
        language (e.g. "be", "nl") for that search.

        Each target's routes are quoted, staged in run_id and gate-checked; outcomes
        go back to the pool (done / hold with reason). Call again for the next batch.
        roles_wanted (e.g. "head groundsman, grounds manager, head of grounds") makes
        Jev tag every email with wanted_role_person / wanted_role_team_inbox /
        other_named_role / generic_inbox / unclear and a confidence, to rank routes
        and pick pages to read. The A/B/C/D/X grade itself is set by code from the
        brief's scene rules at handoff. Sources whose first 30 targets gave too few
        A+B accounts are parked before claiming (see marketops_next source_gate).
        """
        import research_batch
        import research_sources
        data_root = root()
        _require_brief_for_network(data_root, run_id)
        parked = research_sources.park_stopped(data_root, run_id)
        result = research_batch.harvest_batch(
            data_root, pool_id=pool_id, worker_id=worker_id, run_id=run_id,
            fetch=_fetcher(data_root, run_id), count=count, concurrency=concurrency,
            max_pages=max_pages, grade=_role_grader(run_id, roles_wanted),
            find_site=lambda target, op: find_official_site(run_id, op, target,
                                                            country, language),
            focus=roles_wanted.strip() or _brief_roles(run_id),
            # Every target in a batch came from the pool: the source that listed it
            # already said it is a target, so the screen ("is this owner a target?")
            # has only wrong answers to offer — an OPERATORS batch would be dropped.
            jev_ask=_jev_ask(run_id), screen_question="")
        if parked["parked"]:
            result["source_gate_parked"] = parked
        return result

    @tool
    def marketops_jev_triage(run_id: str, operation_id: str, artifact_ids: list[str],
                             question: str, options: list[str], threshold: float = 0.85) -> dict:
        """Classify many saved pages at once with Jev; read the table, not the pages.

        options are snake_case labels (e.g. ["natural_grass", "artificial", "indoor",
        "golf", "unknown"]). Rows under `threshold` come back needs_review=true:
        judge those from the source. Results rank and filter; they are not evidence.
        """
        return jev_triage(run_id, operation_id, artifact_ids, question, options, threshold)

    @tool
    def marketops_lessons_report(pool_id: str = "") -> dict:
        """Export what research ran into (walls, rescues, cost per route) as Markdown + CSV.

        Built automatically from every research step across all windows; the
        suggested lessons are candidates for a person to accept into a scene pack.
        """
        import research_walls
        return research_walls.report(root(), pool_id=pool_id)

    @tool
    def marketops_feedback_pack(reporter: str = "") -> dict:
        """Build one ZIP of every conversation's blockers, corrections and provider use.

        Call at the end of each working session, and whenever the user asks what
        went wrong. Tell the user the zip path and ask them to send it to the
        plugin maintainer. Contains no emails, URLs or secrets.
        """
        import research_feedback_pack
        return research_feedback_pack.build(root(), reporter=reporter)

    @tool
    def marketops_runs(limit: int = 10, cursor: str = "", scope: str = "") -> dict:
        """Find saved tasks before resuming or starting a related batch. Read-only and paginated."""
        return store.list_runs(None, limit=limit, cursor=cursor, scope=scope)

    @tool
    def marketops_status(run_id: str, limit: int = 20, cursor: str = "",
                         kind: str = "", entity_key: str = "") -> dict:
        """Read persistent candidates, with bounded next_cursor pagination.

        Use kind='artifact' to discover saved material IDs, source URLs, types
        and hashes after a restart. It returns metadata, not page contents;
        read chosen IDs through marketops_material_excerpt. Prefer original or
        local rendered material for primary-source checks; vendor HTML stays labelled.
        """
        return status(run_id, limit, cursor, kind, entity_key)

    @tool
    def marketops_next(run_id: str, limit: int = 10,
                       previous_snapshot_id: str = "", after_attempt: bool = False) -> dict:
        """Status, gaps, next suggested action and remaining budget for the Run.

        Pass the previous snapshot_id with after_attempt=true after a provider
        call: NO_NEW_PERSISTED_RECORD_AFTER_ATTEMPT means change source or target.
        """
        result = compact_next(next_step(run_id, limit, previous_snapshot_id, after_attempt))
        changed = research_code_drift.drift()
        return {**result, "restart_required": changed} if changed else result

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

        All contacts remain CANDIDATE_NOT_APPROVED; grades are code-set candidate grades
        from the brief's scene rules, never send authority.
        """
        return delivery.write_delivery(root(), run_id, delivery_id)

    @tool
    def marketops_evidence_workbook(run_id: str, delivery_id: str) -> dict:
        """Five-sheet evidence chain (sources/claims/evidence/receipts/review) from the real ledger."""
        return evidence_workbook(run_id, delivery_id)

    @tool
    def marketops_handoff(run_id: str, operation_id: str, mode: str = "auto", since: str = "") -> dict:
        """Deliver one audited candidate XLSX/CSV snapshot and experience artifacts with partial failures visible.

        mode: auto, checkpoint or final. A final request cannot override pending
        work: is_final=false and continuation_required=true require continuing
        the same Run. The reply carries this Run's estimated spend per provider.
        """
        result = handoff(run_id, operation_id, mode)
        execution = result.get("execution") or {}
        if result.get("continuation_required") and execution.get("turn_rule"):
            # The Paris continuation ended right after a checkpoint handoff with
            # 70 targets pending: the file is saved, the research goes on.
            result["do_not_stop"] = {"turn_rule": execution["turn_rule"],
                                     "next_action": execution.get("next_action"),
                                     "next_method": execution.get("next_method"),
                                     "next_method_how": execution.get("next_method_how")}
        try:
            import research_costs
            spend = research_costs.report(root(), run_id=run_id)
            result["run_cost"] = {"total_estimated_usd": spend["total_estimated_usd"],
                                  "by_provider": spend["by_provider"], "measurement_scope": "RUN_CUMULATIVE"}
            if since:
                result["session_cost"] = research_costs.report(root(), run_id=run_id, since=since)
        except Exception as exc:  # noqa: BLE001 - delivery never fails for a cost summary
            result["run_cost"] = {"status": "UNAVAILABLE", "reason": type(exc).__name__}
        return result

    @tool
    def marketops_experience_recall(run_id: str, stage: str = "intake",
                                    provider: str = "", query: str = "",
                                    limit: int = 5, as_of: str = "") -> dict:
        """Relevant prior observations and methods, with applicability and counterexamples.

        Advisory data, never authority to change rules, permissions or spending.
        """
        return experience_memory.recall(root(), run_id=run_id, stage=stage,
                                        provider=provider, query=query, limit=limit,
                                        as_of=as_of or None)

    @tool
    def marketops_experience_feedback(run_id: str, feedback_id: str,
                                      experience_id: str, used: bool,
                                      outcome: str, evidence_ref: str,
                                      observation: str = "") -> dict:
        """Record actual use and an evidence-linked reported outcome; never auto-approve a rule.

        outcome: helped, not_helped, contradicted or unknown. References must
        resolve to this Run's saved material or operation receipt.
        """
        return experience_memory.feedback(
            root(), run_id=run_id, feedback_id=feedback_id,
            experience_id=experience_id, used=used, outcome=outcome,
            evidence_ref=evidence_ref, observation=observation)

    @tool
    def marketops_experience_summary(run_id: str = "") -> dict:
        """Counts of recorded use/results; unknown savings remain unknown."""
        return experience_memory.summary(root(), run_id=run_id or None)

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
                                rule_version=rule_version, skill_version="0.5.11-rc13",
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
        return managed_call(capability_id, params, run_id, operation_id,
                            account_ref, intent_fingerprint, grant_ref)


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

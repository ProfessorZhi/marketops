#!/usr/bin/env python3
"""Bounded 5-provider Lite Research MCP. Candidate-only, local, human-initiated."""
from __future__ import annotations

import functools
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import dual_search_mcp
import research_config
import research_lite_store as store
import research_provider_extensions as extra
import research_delivery as delivery
import research_workflow as workflow
from research_event_journal import (_root, append_event, append_note, draft_experience,
                                    read_events)
from research_experience_summary import refresh_quietly as refresh_summary


def next_step(run_id, limit=25):
    """Bounded structural next-step batch for one Run; never a spending authority."""
    return workflow.next_step(root(), run_id, limit)


def status(run_id, limit=300, cursor="", kind="", entity_key=""):
    """Persisted candidates through the workflow page contract.

    A page fixes a row high-water mark; later appends land in a new query, and a
    cursor belongs to its Run and filters.
    """
    return workflow.page(root(), run_id, limit, cursor, kind, entity_key)


def setup_status():
    """Are the two first-use paths chosen? Read-only, redacted, never creates anything."""
    return research_config.status(redact=True)


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


def capabilities():
    search = dual_search_mcp._configuration()
    return {
        "product": "MarketOps Lite Research MCP",
        "version": "0.4.0-candidate",
        "providers": {
            "serpapi": {"transport": "REST through local MCP", **search["serpapi"]},
            "serper": {"transport": "REST through local MCP", **search["serper"]},
            "crawl4ai": {"local_installed": importlib.util.find_spec("crawl4ai") is not None,
                         "cloud_key": configured("CRAWL4AI_API_KEY"),
                         "modes": ["local", "cloud"]},
            "tinyfish": {"key": configured("TINYFISH_API_KEY"),
                         "implemented": ["search", "fetch"],
                         "agent": "use official TinyFish MCP separately"},
            "jev": {"key": configured("TYPESAFE_API_KEY"),
                    "sdk": importlib.util.find_spec("typesafe_sdk") is not None,
                    "implemented": ["choice"]},
        },
        "limits": {"search_on": search["network_enabled"],
                   "search_calls": search["max_total_reserved_calls"],
                   "other_network_on": os.environ.get("MARKETOPS_RESEARCH_ALLOW_NETWORK") == "1",
                   "other_calls": extra._limit()},
        "business_store": "local research-lite.sqlite3, candidate-only",
        "no_auto_outreach": True,
    }


def start(run_id, scope, objective, rule_version):
    r = root()
    result = store.start_run(r, run_id, scope, objective, rule_version)
    event = append_event(r, run_id=run_id, operation_id="run-" + run_id,
                         stage="intake", kind="run", status="succeeded",
                         provider="local")
    return {**result, "event": event}


def search(provider, query, country, language, run_id, operation_id, mode="web", limit=10):
    r = root()
    store.list_run(r, run_id, limit=1)
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
    return {**result, "stored_record_ids": record_ids}


def invoke(action, run_id, operation_id, url="", query="", document="",
           question="", options=None):
    return extra.invoke(action, run_id=run_id, operation_id=operation_id,
                        url=url, query=query, document=document,
                        question=question, options=options)


def propose(run_id, record_id, stage, kind, entity_key, facts, artifact_ids=None):
    r = root()
    result = store.put_candidate(r, run_id, record_id, stage, kind,
                                 entity_key, facts, artifact_ids or [])
    append_event(r, run_id=run_id, operation_id=record_id, stage=stage,
                 kind="entity", status="succeeded", provider="local")
    return result


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
            raise ToolError(json.dumps({"status": "BLOCKED", "tool": fn.__name__,
                                        "reason": str(exc)}, ensure_ascii=False)) from exc
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
    def marketops_capabilities() -> dict:
        """Show local provider configuration and operation limits without charging."""
        return capabilities()

    @tool
    def marketops_start(run_id: str, scope: str, objective: str, rule_version: str) -> dict:
        """Start/resume a human-initiated research Run; not an automatic schedule."""
        return start(run_id, scope, objective, rule_version)

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
                           task: str = "search", query: str = "", url: str = "") -> dict:
        """TinyFish official search/fetch APIs; browser Agent via official connector."""
        actions = {"search": "tinyfish_search", "fetch": "tinyfish_fetch"}
        return invoke(actions.get(task, "unsupported"), run_id, operation_id,
                      url=url, query=query)

    @tool
    def marketops_jev_choice(run_id: str, operation_id: str, source_excerpt: str,
                             question: str, options: list[str]) -> dict:
        """Jev Choice over bounded source excerpt; not a formal SOP decision."""
        return invoke("jev_choice", run_id, operation_id, document=source_excerpt,
                      question=question, options=options)

    @tool
    def marketops_propose(run_id: str, record_id: str, stage: str,
                          kind: str, entity_key: str, facts: dict,
                          artifact_ids: list[str] | None = None) -> dict:
        """Write staged venue/organization/role/contact/review proposals only."""
        return propose(run_id, record_id, stage, kind, entity_key, facts, artifact_ids)

    @tool
    def marketops_status(run_id: str, limit: int = 300, cursor: str = "",
                         kind: str = "", entity_key: str = "") -> dict:
        """Read persistent candidates; iterate next_cursor for a full audit, or filter."""
        return status(run_id, limit, cursor, kind, entity_key)

    @tool
    def marketops_next(run_id: str, limit: int = 25) -> dict:
        """Bounded structural next-step batch for this Run; recommends, never authorizes."""
        return next_step(run_id, limit)

    @tool
    def marketops_events(run_id: str) -> dict:
        """Inspect saved operation events and failures."""
        rows = read_events(root(), run_id)
        return {"count": len(rows), "events": rows[-100:]}

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

    return mcp


if __name__ == "__main__":
    build_server().run(transport="stdio")

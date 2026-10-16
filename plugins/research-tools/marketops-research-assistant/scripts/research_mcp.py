#!/usr/bin/env python3
"""Bounded 5-provider Lite Research MCP. Candidate-only, local, human-initiated."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import dual_search_mcp
import research_lite_store as store
import research_provider_extensions as extra
import research_delivery as delivery
from research_event_journal import _root, append_event, draft_experience, read_events


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
    """One human-initiated, candidate-only research+experience handoff.

    Both outputs are immutable and live under this customer's data root.
    An incomplete second write remains a visible HOLD, never a green result.
    """
    r = root()
    store._id(run_id, "run_id")
    store._id(operation_id, "operation_id")
    # Validate Run exists before creating user-visible artifacts.
    snapshot = store.list_run(r, run_id, limit=500)
    if snapshot["record_count"] > snapshot["returned"]:
        raise ValueError("HANDOFF_REQUIRES_COMPLETE_SNAPSHOT")
    exports = r / "exports" / run_id
    experiences = r / "experience" / run_id
    exports.mkdir(parents=True, exist_ok=True)
    experiences.mkdir(parents=True, exist_ok=True)
    csv_path = exports / (operation_id + "-candidates.csv")
    experience_path = experiences / (operation_id + "-experience.json")
    # Partial success is not a completed handoff.
    try:
        export_result = export(run_id, str(csv_path))
        experience_result = draft_experience(
            r, run_id=run_id, scope=snapshot["scope"],
            rule_version=snapshot["rule_version"], skill_version="0.4.0",
            output=experience_path)
        append_event(r, run_id=run_id, operation_id=operation_id,
                     stage="handoff", kind="handoff", status="succeeded",
                     provider="local")
        return {
            "status": "CANDIDATE_HANDOFF_NOT_BUSINESS_VERIFIED",
            "run_id": run_id,
            "records": snapshot["record_count"],
            "artifacts": snapshot["artifact_count"],
            "candidate_csv": str(csv_path),
            "experience_candidate": str(experience_path),
            "experience_incidents": experience_result["incidents"],
            "unresolved_business_review": True,
            "warning": "CSV is NOT XLSX and staged candidates are NOT ready contacts.",
        }
    except Exception:
        append_event(r, run_id=run_id, operation_id=operation_id,
                     stage="handoff", kind="handoff", status="hold",
                     provider="local", reason_code="partial_handoff_or_file_conflict")
        raise ValueError("HANDOFF_PARTIAL_OR_FAILED: inspect immutable output files and journal") from None


def build_server():
    from mcp.server import MCPServer
    mcp = MCPServer("MarketOps Lite Research")

    @mcp.tool()
    def marketops_capabilities() -> dict:
        """Show local provider configuration and operation limits without charging."""
        return capabilities()

    @mcp.tool()
    def marketops_start(run_id: str, scope: str, objective: str, rule_version: str) -> dict:
        """Start/resume a human-initiated research Run; not an automatic schedule."""
        return start(run_id, scope, objective, rule_version)

    @mcp.tool()
    def marketops_search(provider: str, query: str, country: str, language: str,
                         run_id: str, operation_id: str, mode: str = "web",
                         limit: int = 10) -> dict:
        """SerpApi OR Serper, explicit one-provider discovery, without fact promotion."""
        return search(provider, query, country, language, run_id, operation_id, mode, limit)

    @mcp.tool()
    def marketops_crawl(run_id: str, operation_id: str, url: str,
                        implementation: str = "local") -> dict:
        """Crawl4AI local open-source crawler or approved Cloud API."""
        actions = {"local": "crawl4ai_local", "cloud": "crawl4ai_cloud"}
        return invoke(actions.get(implementation, "unsupported"), run_id, operation_id, url=url)

    @mcp.tool()
    def marketops_tinyfish(run_id: str, operation_id: str,
                           task: str = "search", query: str = "", url: str = "") -> dict:
        """TinyFish official search/fetch APIs; browser Agent via official connector."""
        actions = {"search": "tinyfish_search", "fetch": "tinyfish_fetch"}
        return invoke(actions.get(task, "unsupported"), run_id, operation_id,
                      url=url, query=query)

    @mcp.tool()
    def marketops_jev_choice(run_id: str, operation_id: str, source_excerpt: str,
                             question: str, options: list[str]) -> dict:
        """Jev Choice over bounded source excerpt; not a formal SOP decision."""
        return invoke("jev_choice", run_id, operation_id, document=source_excerpt,
                      question=question, options=options)

    @mcp.tool()
    def marketops_propose(run_id: str, record_id: str, stage: str,
                          kind: str, entity_key: str, facts: dict,
                          artifact_ids: list[str] | None = None) -> dict:
        """Write staged venue/organization/role/contact/review proposals only."""
        return propose(run_id, record_id, stage, kind, entity_key, facts, artifact_ids)

    @mcp.tool()
    def marketops_status(run_id: str, limit: int = 300) -> dict:
        """Read persistent research candidates and material refs."""
        return store.list_run(root(), run_id, limit)

    @mcp.tool()
    def marketops_events(run_id: str) -> dict:
        """Inspect saved operation events and failures."""
        rows = read_events(root(), run_id)
        return {"count": len(rows), "events": rows[-100:]}

    @mcp.tool()
    def marketops_export(run_id: str, output_path: str) -> dict:
        """Export local candidate-only CSV; no contact/outreach authorization."""
        return export(run_id, output_path)

    @mcp.tool()
    def marketops_export_xlsx(run_id: str, delivery_id: str) -> dict:
        """Write real 8-sheet evidence-audited XLSX + CSV from this Run's Lite store.

        All contacts remain CANDIDATE_NOT_APPROVED; no A/B/C/D or send authority.
        """
        return delivery.write_delivery(root(), run_id, delivery_id)

    @mcp.tool()
    def marketops_handoff(run_id: str, operation_id: str) -> dict:
        """Write both candidate research CSV and experience incident draft in one review handoff."""
        return handoff(run_id, operation_id)

    @mcp.tool()
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

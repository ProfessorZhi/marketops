"""Bound the model-facing view after a successful provider payload is saved.

Call at the final MCP response boundary, after compatibility wrappers have
finished. This never changes the store, ledger, source evidence or error result.
"""
from __future__ import annotations
import copy
import json

import research_lite_store as store

DIAGNOSTIC_FIELDS = frozenset({
    "error", "errors", "warning", "warnings", "caveats", "reason_code",
    "usage", "cost", "cost_usd", "actual_usd", "billing", "charges", "quota",
    "rate_limit", "charge_state", "cost_basis", "usage_receipt",
    "status", "status_code", "http_status", "failure_reason", "error_code", "isError",
})


def _bytes(value):
    return len(json.dumps(value, ensure_ascii=False, indent=2, default=str).encode("utf-8"))


def compact_provider_response(root, run_id, response, *, max_rows=10,
                              max_text_chars=1200, max_preview_bytes=8000):
    """Return one bounded payload, keeping every top-level status/cost field.

    A store readback must match the complete payload before it can be omitted
    from the model context. Missing/mismatched storage is an integration error,
    never permission to truncate the sole surviving copy. Per-page errors and
    usage diagnostics are also retained separately without shortening.
    """
    if not isinstance(response, dict) or response.get("payload") is None:
        return response
    status = response.get("managed_status", response.get("status"))
    if status not in {"OK", "PROVIDER_CALL_COMPLETED"}:
        return response
    if (type(max_rows) is not int or not 1 <= max_rows <= 30
            or type(max_text_chars) is not int or not 100 <= max_text_chars <= 4000
            or type(max_preview_bytes) is not int or not 1000 <= max_preview_bytes <= 32000):
        raise ValueError("RESPONSE_VIEW_INVALID_LIMIT")
    payload = response["payload"]
    artifact = response.get("artifact")
    identifier = artifact.get("artifact_id") if isinstance(artifact, dict) else None
    if not isinstance(identifier, str) or identifier not in response.get("artifact_ids", ()):
        raise ValueError("RESPONSE_VIEW_REQUIRES_SAVED_PAYLOAD")
    try:
        retained = store.read_artifact(root, run_id, identifier)
        full = json.loads(retained["content"])
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("RESPONSE_VIEW_REQUIRES_SAVED_PAYLOAD") from None
    if full != payload:
        raise ValueError("RESPONSE_VIEW_SAVED_PAYLOAD_MISMATCH")

    result = copy.deepcopy(response)
    removed = []
    if result.get("result") == payload:
        result.pop("result", None)
        removed.append("result")
    if isinstance(result.get("outcome"), dict) and result["outcome"].get("payload") == payload:
        result["outcome"].pop("payload", None)
        removed.append("outcome.payload")

    rows, diagnostics, truncated_paths = {}, {}, []
    remaining = max_preview_bytes

    def collect(value, path="$", depth=0):
        # Provider JSON is already bounded at ingress. Keep these fields even
        # on rows outside the displayed result slice: partial failure and cost
        # cannot disappear merely because a model preview is shorter.
        if isinstance(value, dict):
            if DIAGNOSTIC_FIELDS.intersection(value):
                for source_key in ("url", "source_url", "link"):
                    if isinstance(value.get(source_key), str):
                        diagnostics[path + "." + source_key] = value[source_key]
            for key, child in value.items():
                child_path = path + "." + key
                if key in DIAGNOSTIC_FIELDS:
                    diagnostics[child_path] = copy.deepcopy(child)
                else:
                    collect(child, child_path, depth + 1)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                collect(child, f"{path}[{i}]", depth + 1)

    def preview(value, path="$", depth=0):
        nonlocal remaining
        if depth > 7 or remaining <= 0:
            truncated_paths.append(path)
            return None
        if isinstance(value, str):
            shown = value[:max_text_chars]
            shown = shown.encode("utf-8")[:max(0, remaining - 2)].decode("utf-8", errors="ignore")
            if shown != value:
                truncated_paths.append(path)
            remaining -= _bytes(shown)
            return shown
        if isinstance(value, list):
            answer = []
            remaining -= 2
            for i, child in enumerate(value[:max_rows]):
                if remaining <= 0:
                    break
                answer.append(preview(child, f"{path}[{i}]", depth + 1))
                remaining -= 1
            rows[path] = {"total": len(value), "returned": len(answer)}
            if len(answer) != len(value):
                truncated_paths.append(path)
            return answer
        if isinstance(value, dict):
            answer = {}
            remaining -= 2
            for key, child in value.items():
                if remaining <= _bytes(key) + 3:
                    truncated_paths.append(path)
                    break
                remaining -= _bytes(key) + 2
                answer[key] = preview(child, path + "." + key, depth + 1)
            return answer
        remaining -= _bytes(value)
        return value

    collect(payload)
    result["payload"] = preview(payload)
    # Exact UTF-8 JSON size wins over the inexpensive traversal accounting.
    # This fallback is explicit and still points to the verified full copy.
    if _bytes(result["payload"]) > max_preview_bytes:
        result["payload"] = {"preview_omitted": "PREVIEW_BYTE_LIMIT", "full_payload_artifact_id": identifier}
        truncated_paths.append("$")
        for record in rows.values():
            record["returned"] = 0
    if diagnostics:
        result["payload_diagnostics"] = diagnostics
    result["response_view"] = {
        "schema": "marketops.provider-response-view.v1",
        "full_payload_persisted": True,
        "full_payload_artifact_id": identifier,
        "full_payload_sha256": retained["content_sha256"],
        "full_payload_bytes": len(retained["content"]),
        "preview_bytes": _bytes(result["payload"]),
        "truncated": bool(truncated_paths),
        "truncated_paths": list(dict.fromkeys(truncated_paths))[:30],
        "row_counts": rows,
        "removed_duplicate_paths": removed,
        "diagnostics_truncated": False,
        "read_more_tool": "marketops_material_excerpt",
    }
    return result

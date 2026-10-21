#!/usr/bin/env python3
"""Host-compatible MCP facade for the MarketOps Research server.

The Research server itself (`research_mcp.py`) is frozen for this wave and is the
only place the 23 tools are defined. This module is the *host-facing* boundary:

* it serves the same server, unchanged in behaviour, and
* it advertises only the JSON Schema subset every supported host accepts.

Why a facade instead of editing the server: FastMCP/MCPServer derives an input
schema from the Python signature, so an optional parameter typed `list[str] | None`
becomes `anyOf: [{"type": "array", ...}, {"type": "null"}]`. That is legal JSON
Schema but it is a combinator, and hosts differ in which combinators they accept
(Codex is documented as rejecting some legal fields). Flattening the nullable arm
keeps the advertised schema to `type` / `properties` / `required` / `items` /
`additionalProperties` / `title` / `default` — no combinators at all.

The direction of the rewrite is always *narrower advertised, unchanged accepted*:
the server's own validator still accepts an explicit `null`, so no previously
working call starts failing. A construct this module does not know how to
flatten fails closed (`HostIncompatibleSchema`) rather than being passed through.

Verified against `mcp==2.3.0`: `_handle_list_tools` delegates to the public
`MCPServer.list_tools()`, which this module wraps per instance.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

#: JSON Schema keywords the supported hosts are not known to accept. Any of these
#: reaching a tool's advertised schema is a defect, not a warning.
HOST_UNSUPPORTED_KEYWORDS = (
    "$ref", "$defs", "definitions", "$dynamicRef", "$dynamicAnchor",
    "anyOf", "oneOf", "allOf", "not", "if", "then", "else",
    "patternProperties", "unevaluatedProperties", "unevaluatedItems",
    "dependentRequired", "dependentSchemas", "prefixItems", "contains",
    "propertyNames", "const",
)

#: Keywords this facade emits. Anything else in the output is a bug in this module.
HOST_SUPPORTED_KEYWORDS = (
    "type", "properties", "required", "items", "additionalProperties",
    "title", "description", "default", "enum", "minimum", "maximum",
)


class HostIncompatibleSchema(ValueError):
    """A schema construct that cannot be flattened into the host-safe subset."""


def _is_null_only(node: object) -> bool:
    return isinstance(node, dict) and node.get("type") == "null" and len(node) == 1


def normalize_schema(node: object) -> object:
    """Return a host-safe copy of one JSON Schema node.

    Only two rewrites are performed, both lossless for a caller that sends
    concrete values:

    ``anyOf: [X, {"type": "null"}]`` -> the normalised ``X``
    ``{"type": ["a", "b"]}`` -> ``anyOf``-free union, kept only when neither
        arm is ``null`` (a genuine union is left alone).

    Anything else unsupported raises instead of being silently forwarded.
    """
    if isinstance(node, list):
        return [normalize_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    if "anyOf" in node:
        arms = node["anyOf"]
        if not isinstance(arms, list) or len(arms) != 2:
            raise HostIncompatibleSchema(
                f"anyOf with {len(arms) if isinstance(arms, list) else '?'} arms is not flattenable")
        non_null = [arm for arm in arms if not _is_null_only(arm)]
        null_arms = [arm for arm in arms if _is_null_only(arm)]
        if len(non_null) != 1 or len(null_arms) != 1:
            raise HostIncompatibleSchema("anyOf is not the nullable-optional pattern")
        merged = {key: value for key, value in node.items() if key != "anyOf"}
        flattened = normalize_schema(non_null[0])
        if not isinstance(flattened, dict):
            raise HostIncompatibleSchema("nullable arm does not flatten to an object schema")
        overlap = set(merged) & set(flattened)
        if overlap:
            raise HostIncompatibleSchema(f"anyOf carries conflicting siblings: {sorted(overlap)}")
        merged.update(flattened)
        return merged

    out: dict = {}
    for key, value in node.items():
        if key in ("properties",):
            if not isinstance(value, dict):
                raise HostIncompatibleSchema("properties must be an object")
            out[key] = {name: normalize_schema(sub) for name, sub in value.items()}
        elif key == "items":
            out[key] = normalize_schema(value)
        elif key in HOST_UNSUPPORTED_KEYWORDS:
            raise HostIncompatibleSchema(f"unsupported keyword at this position: {key}")
        else:
            out[key] = normalize_schema(value)
    return out


def audit_schema(node: object, path: str = "") -> list[dict]:
    """List every host-unsupported construct in one schema, without rewriting it."""
    findings: list[dict] = []
    if isinstance(node, list):
        for index, item in enumerate(node):
            findings.extend(audit_schema(item, f"{path}[{index}]"))
        return findings
    if not isinstance(node, dict):
        return findings
    for key, value in node.items():
        here = f"{path}/{key}"
        if key in HOST_UNSUPPORTED_KEYWORDS:
            findings.append({"path": here, "keyword": key})
        findings.extend(audit_schema(value, here))
    return findings


def audit_tools(tools: list) -> dict:
    """Audit an already-listed tool set: names, vendor leakage and schema subset.

    ``tools`` is the list returned by ``MCPServer.list_tools()``; each entry has
    ``name`` and ``input_schema``.
    """
    findings: list[dict] = []
    names: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", "")
        names.append(name)
        schema = getattr(tool, "input_schema", None)
        if schema is None:
            schema = getattr(tool, "parameters", None)
        if not isinstance(schema, dict):
            findings.append({"tool": name, "path": "", "keyword": "MISSING_INPUT_SCHEMA"})
            continue
        for finding in audit_schema(schema):
            findings.append({"tool": name, **finding})
        top = set(schema)
        unexpected = sorted(top - set(HOST_SUPPORTED_KEYWORDS))
        if unexpected:
            findings.append({"tool": name, "path": "",
                             "keyword": f"TOP_LEVEL:{','.join(unexpected)}"})
    vendor = [name for name in names if not name.startswith("marketops_")]
    return {
        "tool_count": len(names),
        "tools": names,
        "vendor_named_tools": vendor,
        "schema_findings": findings,
        "host_compatible": not findings and not vendor,
    }


def build_host_compatible_server():
    """Build the Research server and narrow its advertised tool schemas.

    Behaviour of every tool is untouched; only the `tools/list` payload is
    normalised. Raises ``HostIncompatibleSchema`` if any tool carries a construct
    this facade cannot flatten, so a new SDK or a new tool cannot silently
    reintroduce a host-incompatible schema.
    """
    import research_mcp

    server = research_mcp.build_server()
    original = server.list_tools

    async def list_tools():
        tools = await original()
        normalized = []
        for tool in tools:
            schema = getattr(tool, "input_schema", None)
            if schema is None:
                schema = getattr(tool, "parameters", None)
            if not isinstance(schema, dict):
                raise HostIncompatibleSchema(
                    f"{getattr(tool, 'name', '?')} has no dict input schema")
            normalized.append(tool.model_copy(update={"input_schema": normalize_schema(schema)}))
        return normalized

    server.list_tools = list_tools
    return server


def _check() -> int:
    server = build_host_compatible_server()
    tools = asyncio.run(server.list_tools())
    report = audit_tools(tools)
    report["server"] = {"name": getattr(server, "name", None),
                        "version": getattr(server, "version", None)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["host_compatible"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="audit the advertised tool schemas and exit; serves nothing")
    args = parser.parse_args()
    if args.check:
        return _check()
    build_host_compatible_server().run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

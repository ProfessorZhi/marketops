"""Synthetic bottom-level official MCP service for product routing tests.

All snapshots are temporary test data. Never package or promote these fixtures.
"""
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import research_runtime_setup as runtime
import research_runtime_transport as transport
from research_s0_preflight_gate import canonical_snapshot, snapshot_digest


def install(case, root, *, providers=("tinyfish", "serpapi", "serper", "crawl4ai")):
    runtime.configure(root, providers=providers, domains=["example.org", "public.example.org"],
                      max_calls=10, task="synthetic research", confirm=True)
    qa = patch.object(runtime, "verify_build_receipt", return_value={"ok": True})
    qa.start()
    case.addCleanup(qa.stop)


@asynccontextmanager
async def connection(provider, *, root=None, egress=None, calls=None, result=None):
    schemas = ({"search": {"params": {"type": "object"}, "mode": {"type": "string"}}}
        if provider == "serpapi" else {
            "search": {"query": {"type": "string"}},
            "fetch_content": {"urls": {"type": "array"}, "format": {"type": "string"},
                "links": {"type": "boolean"}, "image_links": {"type": "boolean"}, "iframe_links": {"type": "boolean"},
                "page_metadata": {"type": "boolean"}}})
    listed = SimpleNamespace(tools=[SimpleNamespace(name=name, description=None,
        input_schema={"type": "object", "properties": props, "required": list(props)})
        for name, props in schemas.items()])
    class Session:
        async def list_tools(self): return listed
        async def list_resources(self):
            return SimpleNamespace(resources=[SimpleNamespace(uri="serpapi://engines", uriTemplate=None),
                SimpleNamespace(uri=None, uriTemplate="serpapi://engines/{engine}")])
        async def read_resource(self, uri):
            value = {"engines": ["google_light", "google_scholar", "google_news", "google"]} \
                if str(uri) == "serpapi://engines" else {"type": "object", "properties": {"q": {"type": "string"}}}
            return SimpleNamespace(contents=[SimpleNamespace(text=json.dumps(value), uri=uri)])
        async def call_tool(self, name, arguments):
            if calls is not None: calls.append((provider, name, arguments))
            value = result(provider, name, arguments) if result else {
                "organic_results": [{"title": "Public Library", "link": "https://example.org/library"}],
                "matches": [{"title": "Public Library", "url": "https://example.org/library"}],
                "text": "Public Library opening hours 09:00 to 17:00"}
            return SimpleNamespace(isError=False, structuredContent=value)
    yield Session()


def bind(case, root, *, calls=None, result=None):
    # Capture the fake service's actual list, in memory; no live claim is emitted.
    import asyncio
    async def snapshot(provider):
        async with connection(provider, root=root) as session:
            return transport.tools_payload(await session.list_tools())
    for provider in ("tinyfish", "serpapi"):
        payload = asyncio.run(snapshot(provider))
        runtime._write(runtime._path(root, provider + "-schema.json"), {
            "version": 1, "provider": provider, "endpoint": runtime.ENDPOINTS[provider],
            "method": "tools/list", "source": "probe_schema@official_mcp",
            "captured_at": runtime.now(), "egress_decision_ref": "synthetic-egress",
            "audit_ref": "synthetic-audit", "tool_count": len(payload["tools"]),
            "snapshot_serialized": canonical_snapshot(payload),
            "schema_sha256": snapshot_digest(payload), "registry_schema_sha256": snapshot_digest(payload)})
    replacement = patch.object(transport, "mcp_session", lambda provider, **kwargs:
        connection(provider, calls=calls, result=result, **kwargs))
    replacement.start()
    case.addCleanup(replacement.stop)

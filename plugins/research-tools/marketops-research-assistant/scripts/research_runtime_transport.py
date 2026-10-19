"""Production dependencies for the existing provider adapters. No retries."""
from contextlib import asynccontextmanager, contextmanager
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
import urllib.request


class BeforeDispatchRefused(ValueError):
    """A local check before tools/call, safe to settle as no dispatch."""


def tools_payload(listed):
    if hasattr(listed, "model_dump"):
        return {"tools": listed.model_dump(mode="json", by_alias=True)["tools"]}
    return {"tools": [{"name": t.name, "description": getattr(t, "description", None),
                       "inputSchema": getattr(t, "input_schema", None)
                       or getattr(t, "inputSchema", {})} for t in listed.tools]}


@asynccontextmanager
async def mcp_session(provider, *, root=None, egress=None):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import httpx2
    from research_egress import open_session
    from research_runtime_setup import ENDPOINTS, config, verify_build_receipt
    from contextlib import nullcontext
    endpoint = ENDPOINTS[provider]
    settings = config(root)
    if (provider not in settings.get("providers", ()) or not settings.get("network_enabled")
            or os.environ.get("MARKETOPS_RESEARCH_ALLOW_NETWORK") == "0"):
        raise BeforeDispatchRefused("RESEARCH_NETWORK_DISABLED")
    if not verify_build_receipt()["ok"]:
        raise BeforeDispatchRefused("CAPABILITY_NOT_AVAILABLE")
    auth, headers = None, {}
    if provider == "tinyfish":
        from research_adapter_tinyfish import credential_mode
        mode, variable = credential_mode()
        if mode == "OS_KEYRING_OAUTH":
            from research_official_mcp_auth import oauth_provider
            auth = oauth_provider(interactive=False)
        elif variable == "TINYFISH_API_KEY":
            headers = {"X-API-Key": os.environ[variable]}
        else:
            headers = {"Authorization": "Bearer " + os.environ[variable]}
    else:
        key = os.environ.get("SERPAPI_API_KEY", "").strip()
        if not key:
            raise BeforeDispatchRefused("CAPABILITY_NOT_AVAILABLE")
        headers = {"Authorization": "Bearer " + key}
    scope = nullcontext(egress) if egress is not None else open_session(
        allowlist=frozenset({urlsplit(endpoint).hostname}))
    with scope as connection:
        async with httpx2.AsyncClient(headers=headers, auth=auth,
                proxy=connection.proxy.url, trust_env=False, follow_redirects=False,
                timeout=httpx2.Timeout(65, connect=10)) as http:
            async with streamable_http_client(endpoint, http_client=http) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    yield session


def validate_live_tools(root, provider, listed, tool=None, arguments=None):
    """Compare with the admitted capture, then validate the actual outbound args."""
    import jsonschema
    import research_runtime_setup as runtime
    from research_s0_preflight_gate import snapshot_digest
    payload = tools_payload(listed)
    accepted = runtime._read(runtime._path(root, provider + "-schema.json"))
    if snapshot_digest(payload) != accepted.get("schema_sha256"):
        raise BeforeDispatchRefused("CAPABILITY_SCHEMA_DRIFT")
    if tool is not None:
        schemas = {x["name"]: x.get("inputSchema") or x.get("input_schema")
                   for x in payload["tools"]}
        schema = schemas.get(tool)
        if not isinstance(schema, dict):
            raise BeforeDispatchRefused("CAPABILITY_SCHEMA_DRIFT")
        try:
            jsonschema.validate(arguments, schema)
        except (jsonschema.ValidationError, jsonschema.SchemaError):
            raise BeforeDispatchRefused("CAPABILITY_PARAM_NOT_ALLOWED") from None
    return payload


def tinyfish_runner(root):
    async def run(tool, arguments):
        from research_adapter_tinyfish import _extract_payload
        async with mcp_session("tinyfish", root=root) as session:
            listed = await session.list_tools()
            validate_live_tools(root, "tinyfish", listed, tool, arguments)
            return _extract_payload(await session.call_tool(tool, arguments))
    return run


def tinyfish_target_guard(root):
    def guard(value, *, resolve=True, resolver=None):
        from research_runtime_setup import config
        from research_egress import approved_url
        return approved_url(value, resolve=resolve, resolver=resolver,
                            allowlist=frozenset(config(root).get("allowed_domains", ())))
    return guard


class CheckedSerpApiSession:
    """SDK session facade: exact admitted schema and audited resource reads."""
    def __init__(self, session, root):
        self._session, self._root = session, root
        self._listed = None

    async def list_tools(self):
        listed = await self._session.list_tools()
        validate_live_tools(self._root, "serpapi", listed)
        self._listed = listed
        return listed

    async def call_tool(self, name, arguments):
        listed = self._listed or await self.list_tools()
        validate_live_tools(self._root, "serpapi", listed, name, arguments)
        return await self._session.call_tool(name, arguments)

    async def list_resources(self, *args, **kwargs):
        from research_runtime_setup import audit
        audit(self._root, {"event": "engine-discovery", "provider": "serpapi",
                           "method": "resources/list", "phase": "intent"})
        return await self._session.list_resources(*args, **kwargs)

    async def list_resource_templates(self, *args, **kwargs):
        from research_runtime_setup import audit
        audit(self._root, {"event": "engine-discovery", "provider": "serpapi",
                           "method": "resources/templates/list", "phase": "intent"})
        result = await self._session.list_resource_templates(*args, **kwargs)
        audit(self._root, {"event": "engine-discovery", "provider": "serpapi",
                           "method": "resources/templates/list", "phase": "result"})
        return result

    async def read_resource(self, uri):
        from research_runtime_setup import audit
        audit(self._root, {"event": "engine-discovery", "provider": "serpapi",
                           "method": "resources/read", "resource_uri": str(uri), "phase": "intent"})
        result = await self._session.read_resource(uri)
        audit(self._root, {"event": "engine-discovery", "provider": "serpapi",
                           "method": "resources/read", "resource_uri": str(uri), "phase": "result"})
        return result


@asynccontextmanager
async def serpapi_session(root):
    async with mcp_session("serpapi", root=root) as session:
        yield CheckedSerpApiSession(session, root)


@contextmanager
def serper_transport(request, timeout):
    from research_egress import open_session
    from research_adapter_serper import NoRedirect
    host = urlsplit(request.full_url).hostname
    if host != "google.serper.dev":
        raise ValueError("EGRESS_REFUSED")
    with open_session(allowlist=frozenset({host})) as session:
        opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler(
            {"https": session.proxy.url, "http": session.proxy.url}))
        with opener.open(request, timeout=timeout) as response:
            yield response


class RuntimeCrawlAdapter:
    """Own one restricted egress session for one existing adapter execution."""
    provider = "crawl4ai"

    def __init__(self, root):
        self.root = root

    def capabilities(self):
        from research_adapter_crawl4ai import CAPABILITIES
        return CAPABILITIES

    def probe_schema(self):
        from research_adapter_crawl4ai import Crawl4AIAdapter
        return Crawl4AIAdapter().probe_schema()

    def cancel(self, upstream_ref):
        from research_adapter_crawl4ai import Crawl4AIAdapter
        return Crawl4AIAdapter().cancel(upstream_ref)

    def execute(self, request):
        from research_adapter_crawl4ai import Crawl4AIAdapter
        import research_egress as egress
        from research_crawl4ai_binding import binding_for
        from research_runtime_setup import config, _root
        domains = frozenset(config(self.root).get("allowed_domains", ()))
        def save_primary(run_id, operation_id, url, body):
            import hashlib
            import research_lite_store as store
            artifact_id = "primary-" + hashlib.sha256(
                (run_id + ":" + operation_id).encode()).hexdigest()[:32]
            store.save_artifact(self.root, run_id, artifact_id, "crawl4ai", url,
                                body, "rendered_html")
            return (artifact_id,)
        if request.capability_id == "crawl4ai.extract" and "html" in request.params:
            return Crawl4AIAdapter().execute(request)
        # safe_browser_egress's readiness probe imports Crawl4AI; bind its
        # import-time database cache before that first possible import.
        try:
            cache_base = prepare_crawl_cache(self.root)
        except ValueError as exc:
            from research_adapter_crawl4ai import CrawlOutcome
            return CrawlOutcome(outcome="BLOCKED", payload={"reason": "CAPABILITY_NOT_AVAILABLE",
                "detail": str(exc).split(":", 1)[0]},
                caveats=("CAPABILITY_NOT_AVAILABLE", "CRAWL_CACHE_CONFIGURATION_REFUSED"))
        with egress.safe_browser_egress(allowlist=domains) as session:
            return Crawl4AIAdapter(egress=binding_for(session, egress),
                artifact_root=str(_root(self.root) / "artifacts"),
                material_writer=save_primary, cache_base_directory=cache_base).execute(request)


_managed_crawl_cache = None


def prepare_crawl_cache(root):
    """Bind import-time and per-crawler caches to an absolute owned location.

An explicit absolute operator override is respected. A process whose SDK was
already imported against another cache must restart: changing an environment
variable cannot relocate that SDK's existing global database manager.
"""
    global _managed_crawl_cache
    from research_runtime_setup import _root
    base = _root(root)
    override = os.environ.get("CRAWL4_AI_BASE_DIRECTORY", "").strip()
    explicit = bool(override and override != _managed_crawl_cache)
    if explicit:
        configured = Path(override).expanduser()
        if not configured.is_absolute():
            raise ValueError("CRAWL_CACHE_OVERRIDE_MUST_BE_ABSOLUTE")
        chosen = configured.resolve()
    else:
        chosen = (base / "cache" / "crawl4ai").resolve()
        if not chosen.is_relative_to(base):
            raise ValueError("CRAWL_CACHE_PATH_ESCAPE")
    if any((part / ".git").exists() for part in (chosen, *chosen.parents)):
        raise ValueError("CRAWL_CACHE_MUST_BE_OUTSIDE_SOURCE")
    initialized = sys.modules.get("crawl4ai.async_database")
    db_path = getattr(initialized, "DB_PATH", None)
    if db_path is not None and Path(db_path).resolve() != chosen / ".crawl4ai" / "crawl4ai.db":
        raise ValueError("CRAWL_CACHE_RESTART_REQUIRED")
    chosen.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["CRAWL4_AI_BASE_DIRECTORY"] = str(chosen)
    if not explicit:
        _managed_crawl_cache = str(chosen)
    return str(chosen)

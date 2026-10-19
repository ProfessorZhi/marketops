"""SYNTHETIC official-MCP server + fake guarded egress proxy.

**This is not an official MCP server and not a live verification.** The catalog
is invented: four names are the ones the registry records for TinyFish
(`search`, `fetch_content`, `run_web_automation`, `get_run`) and the rest are
synthetic padding to the recorded catalog size of 28. Nothing here contacts a
vendor, reads a credential or proves an upstream schema.

Why it exists: S0_07 must be provable against a *wire* that enforces the
pre-S0 method whitelist, not against a dictionary that happens to contain the
word "tools". The fake proxy is the same seam the production EgressProxy
occupies, so a `tools/call` refusal here is a statement about the policy, not
about a mock's convenience.

The method whitelist is read from the gate module itself, so the fixture and the
gate cannot drift into two different answers about what is allowed.
"""
from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_s0_preflight_gate as gate  # noqa: E402

#: The four names the registry records; the other 24 are synthetic padding.
RECORDED_VENDOR_TOOLS = ("search", "fetch_content", "run_web_automation", "get_run")
RECORDED_CATALOG_SIZE = 28

#: Methods that are chargeable or otherwise outside the discovery exception.
CHARGEABLE_METHODS = (
    "tools/call",
    "resources/read",
    "resources/list",
    "prompts/get",
    "sampling/createMessage",
    "completion/complete",
)


class WireRefused(Exception):
    """The synthetic wire refused a frame. Never a vendor refusal."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass
class SyntheticOfficialMCPServer:
    """A 28-tool catalog with a handshake state machine. `live` is always False."""

    tool_count: int = RECORDED_CATALOG_SIZE
    protocol_version: str = "synthetic-2026-10-18"
    live: bool = False
    handshaken: bool = False
    calls: list[str] = field(default_factory=list)
    catalog: list[dict[str, Any]] = field(init=False)

    def __post_init__(self) -> None:
        names = list(RECORDED_VENDOR_TOOLS)
        names += [f"synthetic_tool_{i}" for i in range(self.tool_count - len(names))]
        self.catalog = [
            {"name": name, "description": f"synthetic catalog entry for {name}",
             "inputSchema": {"type": "object",
                             "properties": {"query": {"type": "string"}},
                             "required": ["query"]}}
            for name in names
        ]

    def initialize(self) -> dict[str, Any]:
        self.calls.append("initialize")
        self.handshaken = True
        return {"protocolVersion": self.protocol_version,
                "serverInfo": {"name": "synthetic-official-mcp", "version": "0"},
                "capabilities": {"tools": {}}}

    def initialized(self) -> dict[str, Any]:
        self.calls.append("notifications/initialized")
        return {}

    def list_tools(self) -> dict[str, Any]:
        if not self.handshaken:
            raise WireRefused("INITIALIZE_REQUIRED", "tools/list before initialize")
        self.calls.append("tools/list")
        return {"tools": copy.deepcopy(self.catalog)}

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> None:
        # Recorded before the refusal so a test can prove nothing was dispatched.
        self.calls.append(f"tools/call:{name}")
        raise WireRefused("TOOLS_CALL_NOT_ALLOWED",
                          "the discovery exception is not a use exception")

    def drift_catalog(self, tool_count: int) -> None:
        """Replace the catalog with a different one, for drift negatives."""
        self.__post_init__()
        if tool_count != len(self.catalog):
            self.catalog = self.catalog[:tool_count]
        self.handshaken = False


class FakeGuardedEgressProxy:
    """The egress seam: allowlist, method whitelist, decision refs, audit frames.

    It is deliberately strict in the same two dimensions the real proxy is:
    the *host* must be on the allowlist, and the *method* must be on the
    discovery whitelist. A frame that fails either never reaches the server.
    """

    def __init__(self, allowlist: Any, audit: Any) -> None:
        self.allowlist = {str(h).casefold().strip(".") for h in allowlist}
        self.audit = audit
        self.frames: list[tuple[str, str]] = []
        self.denials: list[tuple[str, str]] = []

    @staticmethod
    def host_of(endpoint: str) -> str:
        body = str(endpoint).split("//", 1)[-1]
        return body.split("/", 1)[0].split(":", 1)[0].casefold().strip(".")

    def decide(self, endpoint: str) -> str:
        host = self.host_of(endpoint)
        if host not in self.allowlist:
            self.denials.append(("host", host))
            raise WireRefused("EGRESS_HOST_NOT_ALLOWED", host)
        ref = f"egress:fake:{host}:{len(self.frames)}"
        self.audit({"event": "egress.decision", "phase": "intent",
                    "host": host, "endpoint": endpoint, "ref": ref})
        return ref

    def send(self, endpoint: str, method: str, *,
             server: SyntheticOfficialMCPServer,
             arguments: Mapping[str, Any] | None = None) -> Any:
        host = self.host_of(endpoint)
        if not gate.bootstrap_method_allowed(method):
            self.denials.append(("method", method))
            raise WireRefused("METHOD_NOT_ALLOWED", method)
        if host not in self.allowlist:
            self.denials.append(("host", host))
            raise WireRefused("EGRESS_HOST_NOT_ALLOWED", host)
        self.frames.append((host, method))
        if method == "initialize":
            return server.initialize()
        if method == "notifications/initialized":
            return server.initialized()
        if method == "tools/list":
            return server.list_tools()
        raise WireRefused("METHOD_NOT_ALLOWED", method)

    def probe_schema(self, endpoint: str, *,
                     server: SyntheticOfficialMCPServer) -> dict[str, Any]:
        """The only sequence the discovery exception permits."""
        self.send(endpoint, "initialize", server=server)
        self.send(endpoint, "notifications/initialized", server=server)
        return self.send(endpoint, "tools/list", server=server)


class AuditLog:
    """An audit sink that acknowledges every record with a ref."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def __call__(self, record: Mapping[str, Any]) -> str:
        self.events.append(dict(record))
        return f"audit:fake:{len(self.events)}"

    def bootstrap_events(self) -> list[dict[str, Any]]:
        return [e for e in self.events if str(e.get("event", "")).startswith("s0.bootstrap")]

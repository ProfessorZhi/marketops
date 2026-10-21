"""TEST FIXTURE — a deployment for which S0 has already passed.

Product entry points now refuse every provider call until the S0 preflight gate
returns `launch == ALLOW`. That is the point: a deployment with no captured B0,
no verified schema and no confirmed key rotation may not spend. Existing tests
that exercise an *inner* guard (the paid-agent opt-in, the charge ceiling, a
receipt shape) were written when no such gate existed, so they have to establish
the deployment precondition first — exactly as they already have to set
`MARKETOPS_TINYFISH_AGENT_ALLOW_PAID` to reach the code below that flag.

**This is not evidence.** Every value here is synthetic and the price source says
so. It stands for "a deployment whose gates have passed", so that a test can then
assert what happens *after* the gate. A test that needs to prove the gate itself
refuses must NOT install this.

Kept in one place so the shape lives next to the gate's own validator: if
`validate_snapshot_entry` or the account schema grows a field, this fixture
breaks loudly instead of silently passing a weaker deployment.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: The filename `research_wiring.load_s0_evidence` reads from a data root.
EVIDENCE_FILENAME = "s0-evidence.json"

#: A price source that cannot be mistaken for a vendor rate.
SYNTHETIC_SOURCE = "TEST_FIXTURE_NOT_A_VENDOR_RATE"


def _snapshot(provider: str, hosts: list[str]) -> dict[str, Any]:
    """One admissible capture, built with the gate's own canonical form."""
    import research_s0_preflight_gate as gate

    payload = {"tools": [{"name": "search"}, {"name": "fetch"}, {"name": "wallet"}]}
    serialized = gate.canonical_snapshot(payload)
    digest = gate.snapshot_digest(payload)
    return {
        provider: {
            "version": gate.SNAPSHOT_VERSION,
            "provider": provider,
            "endpoint": f"https://{hosts[0]}/mcp",
            "method": "tools/list",
            "source": "probe_schema@official_mcp",
            "captured_at": "2026-10-18T00:00:00Z",
            "egress_decision_ref": "egress:test-fixture-1",
            "audit_ref": "audit:test-fixture-1",
            "snapshot_serialized": serialized,
            "schema_sha256": digest,
            "registry_schema_sha256": digest,
            "tool_count": len(payload["tools"]),
        }
    }


def _metered() -> dict[str, Any]:
    """A metered account that clears the floor with room to spare."""
    return {
        "captured": True,
        "b0": "1.00",
        "unit": "USD",
        "meter": "requests",
        "price_source": SYNTHETIC_SOURCE,
        "last_observed_at": "2026-10-18T00:00:00Z",
        "unknown_exposure": "0",
        "next_op_estimate": "0.01",
        "schema_verified": True,
        "key_rotation_confirmed": True,
        "auto_topup": False,
    }


def evidence() -> dict[str, Any]:
    """S0 in its all-passed shape. Synthetic in every provider-specific value."""
    hosts = ["managed.example"]
    return {
        "integration": {"sha": "c" * 40, "expected_sha": "c" * 40},
        "independent_qa": {"status": "PASSED", "sha": "c" * 40},
        "managed_mcp": {"configured_count": 1, "effective": True,
                        "endpoints": [f"https://{hosts[0]}/mcp"],
                        "vendor_direct_entries": []},
        "direct_vendor_bypass": {"present": False},
        "credential_injection": {"runtime_scoped": True, "source": "process_env"},
        "endpoint_allowlist": {"hosts": hosts},
        "as_of": "2026-10-18T00:00:00Z",
        "upstream_schema_verified": True,
        "upstream_schema_snapshots": _snapshot("serpapi", hosts),
        "network_enabled": "enabled",
        "providers": {
            # The local SDK spends no vendor account, which the gate states
            # explicitly rather than inferring from a zero balance.
            "crawl4ai": {"billing_mode": "local_no_provider_account"},
            "tinyfish": _metered(),
            "serpapi": _metered(),
            "serper": _metered(),
            "typesafe_jev": _metered(),
        },
    }


def install(root: str | Path) -> Path:
    """Write the fixture evidence where the product reads it, and return the path."""
    path = Path(root) / "state" / EVIDENCE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence(), ensure_ascii=False), encoding="utf-8")
    return path

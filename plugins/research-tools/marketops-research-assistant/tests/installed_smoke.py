"""Install into a clean directory, then exercise the actual stdio process.

Explicit integration runner, not an offline unittest: installation downloads
the hash-pinned core dependencies. Provider networking stays disabled. No host
account is changed and no real credential is read. Use on Windows and Linux.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

PLUGIN = Path(__file__).resolve().parents[1]


def isolated_environment(output: Path) -> dict[str, str]:
    prefixes = ("MARKETOPS_", "SERPER_", "SERPAPI_", "TINYFISH_", "TYPESAFE_",
                "CRAWL4AI_", "JEV_")
    env = {k: v for k, v in os.environ.items() if not k.startswith(prefixes)}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        "MARKETOPS_RESEARCH_CONFIG": str(output / "config.json"),
        "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0",
        "MARKETOPS_SEARCH_ALLOW_NETWORK": "0",
        "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "0",
        "MARKETOPS_SEARCH_MAX_CALLS": "0",
        "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0",
        "TMPDIR": str(output / "tmp"),
        "TMP": str(output / "tmp"),
        "TEMP": str(output / "tmp"),
    })
    return env


def result_object(result) -> dict:
    if getattr(result, "is_error", False):
        raise AssertionError("Installed MCP tool returned an error")
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for block in result.content:
        if getattr(block, "type", None) == "text":
            value = json.loads(block.text)
            if isinstance(value, dict):
                return value
    raise AssertionError("Installed MCP tool did not return a JSON object")


async def journey(python: Path, plugin: Path, output: Path, env: dict) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=str(python),
        args=[str(plugin / "scripts" / "research_mcp.py")], env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            required = {"marketops_start", "marketops_next", "marketops_handoff",
                        "marketops_experience_recall", "marketops_experience_feedback",
                        "marketops_managed_call", "marketops_runs", "marketops_research_step"}
            assert required <= tools, sorted(required - tools)
            assert all(t.startswith("marketops_") for t in tools)

            async def call(name, **arguments):
                return result_object(await session.call_tool(name, arguments))

            first = await call("marketops_setup_status")
            assert first["status"] == "CONFIG_REQUIRED", first
            data = output / "research-data"
            experience = output / "Research_Experience.md"
            await call("marketops_setup_configure", data_root=str(data),
                       experience_file=str(experience))
            configured = await call("marketops_setup_status")
            assert configured["status"] == "CONFIGURED", configured
            brief = {"record_type": "research_brief", "target_kind": "venue",
                     "goal": "pool", "minimum_targets": 1,
                     "minimum_targets_unit": "venue", "require_named_person": False}
            fields = dict(run_id="installed-smoke", scope="synthetic-install-check",
                          objective="Check an installed local research workflow",
                          rule_version="synthetic-install-v1", brief=brief)
            created = await call("marketops_start", **fields)
            resumed = await call("marketops_start", **fields)
            assert created["status"] == "RUN_CREATED", created
            assert resumed["status"] == "EXISTS_NO_CHANGE", resumed
            next_step = await call("marketops_next", run_id=fields["run_id"])
            assert next_step["status"] != "BRIEF_REQUIRED", next_step
            recalled = await call("marketops_experience_recall", run_id=fields["run_id"],
                                  stage="intake", limit=5)
            refused = await call("marketops_managed_call", capability_id="serper.search",
                                 params={"query": "synthetic"}, run_id=fields["run_id"],
                                 operation_id="not-authorized", account_ref="serper",
                                 intent_fingerprint="synthetic-install")
            assert refused["status"] == "BLOCKED", refused
            assert refused["gateway_invoked"] is False, refused
            empty_handoff = await session.call_tool("marketops_handoff", {
                "run_id": fields["run_id"], "operation_id": "installed-handoff"})
            handoff_text = " ".join(getattr(b, "text", "") for b in empty_handoff.content)
            assert "HANDOFF_PARTIAL_OR_FAILED" in handoff_text, handoff_text
            assert not list((data / "exports").rglob("Research_Result.xlsx"))
            return {"server": init.server_info.name, "tool_count": len(tools),
                    "configured": True, "resumed": True,
                    "experience_recall": recalled,
                    "unapproved_provider_call": refused,
                    "empty_handoff_refused": True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--journey-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--destination", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    if output == PLUGIN or PLUGIN in output.parents:
        raise ValueError("Output must be outside the source package")
    env = isolated_environment(output)
    if args.journey_only:
        destination = args.destination.resolve()
        python = destination / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        installed = destination / "plugins" / "marketops-research-assistant"
        result = asyncio.run(asyncio.wait_for(journey(python, installed, output, env), timeout=120))
        result.update(status="INSTALLED_STDIO_SMOKE_PASS", provider_calls=0,
                      business_result="EMPTY_SYNTHETIC_INSTALL_CHECK_NOT_RESEARCH_ACCEPTANCE",
                      native_host_verified=False)
        (output / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": result["status"], "receipt": str(output / "receipt.json")}))
        return 0
    output.mkdir(parents=True, exist_ok=False)
    (output / "tmp").mkdir()
    destination = output / "installed with spaces"
    command = [sys.executable, str(PLUGIN / "scripts" / "setup_clients.py"),
               "--destination", str(destination), "--client", "all", "--apply",
               "--install-deps"]
    process = subprocess.run(command, env=env, text=True, capture_output=True, timeout=600)
    (output / "install.log").write_text(process.stdout + process.stderr, encoding="utf-8")
    if process.returncode:
        print(json.dumps({"status": "INSTALL_FAILED", "log": str(output / "install.log")}))
        return process.returncode
    python = destination / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    # Drive the wire check with the interpreter we actually installed. A bare
    # Python on a clean customer/CI machine need not have the MCP SDK itself.
    return subprocess.run([str(python), str(Path(__file__).resolve()), "--output",
                           str(output), "--journey-only", "--destination", str(destination)],
                          env=env, timeout=180).returncode


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Local product admission: tested build, finite consent and observed MCP schemas.

REQUEST_COUNT bounds operations; it does not invent dollar prices, account
balances or a wallet floor. This file is deliberately not an MCP consent tool.
Legacy campaign evidence remains supported by research_s0_preflight_gate.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

PLUGIN = Path(__file__).resolve().parents[1]
CONFIG_NAME = "runtime-config.json"
KEYS = {"serper": "SERPER_API_KEY", "serpapi": "SERPAPI_API_KEY",
        "tinyfish": "TINYFISH_API_KEY", "typesafe_jev": "TYPESAFE_API_KEY"}
PROVIDERS = (*KEYS, "crawl4ai")
ENDPOINTS = {"tinyfish": "https://agent.tinyfish.ai/mcp",
             "serpapi": "https://mcp.serpapi.com/mcp"}
CORE = {"serper.search", "serpapi.search", "tinyfish.search", "tinyfish.fetch_content",
        "crawl4ai.fetch_page", "crawl4ai.extract", "jev.choice", "jev.noul", "jev.score"}


def now():
    return datetime.now(timezone.utc).isoformat()


def _root(root):
    from research_event_journal import _root as configured_root
    return Path(configured_root(root, create=False)).resolve()


def _path(root, name):
    base = _root(root)
    target = base / "state" / name
    if not target.resolve().is_relative_to(base) or target.is_symlink():
        raise ValueError("RUNTIME_PATH_ESCAPE")
    return target


def _read(path):
    try:
        if path.stat().st_size > 4_000_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".runtime-", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def config(root=None):
    return _read(_path(root, CONFIG_NAME))


def configure(root, *, providers, domains, max_calls, task, confirm=False, amend=False,
              provider_limits=None):
    if confirm is not True or type(max_calls) is not int or not 1 <= max_calls <= 500:
        raise ValueError("FINITE_LOCAL_CONSENT_REQUIRED")
    selected = sorted(set("typesafe_jev" if x == "jev" else x for x in providers))
    if not selected or not set(selected) <= set(PROVIDERS):
        raise ValueError("PROVIDER_NOT_ALLOWED")
    from research_egress import allowed_domains_from_env
    allowed = sorted(allowed_domains_from_env(",".join(domains))) if domains else []
    if not isinstance(task, str) or not task.strip() or len(task) > 500:
        raise ValueError("TASK_SCOPE_REQUIRED")
    path = _path(root, CONFIG_NAME)
    old = _read(path)
    # Omission on resume preserves previously granted provider bounds. It must
    # not silently erase a smaller supplier allowance back to the global cap.
    raw_limits = ({p: n for p, n in old.get("provider_limits", {}).items() if p in selected}
                  if provider_limits is None else provider_limits)
    if not isinstance(raw_limits, dict):
        raise ValueError("PROVIDER_LIMITS_INVALID")
    limits = {}
    for provider, limit in raw_limits.items():
        provider = "typesafe_jev" if provider == "jev" else provider
        if provider not in selected or type(limit) is not int or not 1 <= limit <= max_calls:
            raise ValueError("PROVIDER_LIMITS_INVALID")
        if provider in limits:
            raise ValueError("PROVIDER_LIMITS_DUPLICATE_ALIAS")
        limits[provider] = limit
    proposed = {"schema": "marketops.runtime.v1", "providers": selected,
                "allowed_domains": allowed, "max_calls": max_calls,
                "provider_limits": dict(sorted(limits.items())),
                "task": task.strip(), "budget_mode": "REQUEST_COUNT",
                "network_enabled": True, "paid_agent_enabled": False}
    if old:
        previous = {k: old.get(k, {} if k == "provider_limits" else None) for k in proposed}
        if previous != proposed:
            if not amend:
                raise ValueError("RUNTIME_CONSENT_ALREADY_EXISTS: use --amend --confirm; max-calls is the total cumulative ceiling")
            # A fresh local authorization may change scope or raise the total
            # cap. It never truncates any Run, observation, or ledger usage.
            import research_usage_ledger as usage
            database = usage.default_path(root)
            if database.exists():
                ledger = usage.for_root(root)
                project = usage.project_for_root(root)
                # Validate every cumulative lower bound before changing any cap.
                # UNKNOWN and RESERVED operations remain counted by the ledger.
                if any(row.calls_used > max_calls for row in ledger.usage("project", project)):
                    raise ValueError("CAP_BELOW_HELD")
                for provider in selected:
                    if ledger.account_totals(provider, provider).calls > limits.get(provider, max_calls):
                        raise ValueError("CAP_BELOW_HELD")
                ledger.set_cap("project", project,
                               ceiling_calls=max_calls, source="LOCAL_CONSENT_AMENDMENT")
                for provider in selected:
                    ledger.set_cap("provider_account", f"{project}/{provider}/{provider}",
                        ceiling_calls=limits.get(provider, max_calls), source="LOCAL_CONSENT_AMENDMENT")
            history = _path(root, "runtime-consent-history.jsonl")
            history.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(history, "a", encoding="utf-8") as out:
                out.write(json.dumps({"replaced_at": now(), "previous": old}, sort_keys=True) + "\n")
            os.chmod(history, 0o600)
            proposed["configured_at"] = now()
            proposed["revision"] = int(old.get("revision", 1)) + 1
            _write(path, proposed)
            return proposed
        return old
    proposed["configured_at"] = now()
    proposed["revision"] = 1
    _write(path, proposed)
    return proposed


def source_manifest(plugin=PLUGIN):
    """All shipped executable/configuration inputs, excluding mutable QA receipts."""
    plugin = Path(plugin)
    roots = ("scripts", "skills", "references")
    files = set()
    for directory in roots:
        if (plugin / directory).exists():
            files.update(p for p in (plugin / directory).rglob("*") if p.is_file()
                         and "__pycache__" not in p.parts and p.suffix != ".pyc"
                         and p.name not in {"BUILD_QA.json", "cc_host_probe.sh"})
    files.update(p for p in plugin.glob("*") if p.is_file()
                 and p.name.startswith("requirements"))
    return {p.relative_to(plugin).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def verify_build_receipt(plugin=PLUGIN):
    receipt = _read(Path(plugin) / "references" / "BUILD_QA.json")
    test = receipt.get("test", {})
    okay = (receipt.get("schema") == "marketops.build-qa.v1"
            and bool(re.fullmatch(r"[0-9a-f]{40}", receipt.get("source_revision", "")))
            and receipt.get("files") == source_manifest(plugin)
            and test.get("returncode") == 0 and type(test.get("tests_run")) is int
            and test["tests_run"] > 0 and test.get("suite") == "plugin-unittest-discovery"
            and bool(re.fullmatch(r"[0-9a-f]{64}", test.get("log_sha256", ""))))
    return {"ok": bool(okay), "reason": "" if okay else "BUILD_QA_MISSING_OR_STALE",
            "source_revision": receipt.get("source_revision") if okay else None}


def verify_build(output, *, plugin=PLUGIN):
    """Run the actual complete offline suite and emit a receipt only on success."""
    plugin = Path(plugin).resolve()
    output = Path(output).resolve()
    if output.is_relative_to(plugin):
        raise ValueError("QA_OUTPUT_MUST_BE_OUTSIDE_PLUGIN")
    output.mkdir(parents=True, exist_ok=True)
    before = source_manifest(plugin)
    started = now()
    removed_prefixes = ("SERPER", "SERPAPI", "TINYFISH", "TYPESAFE", "JEV_", "CRAWL4AI",
                        "CRAWL4_AI", "MARKETOPS_", "OPENAI_", "ANTHROPIC_")
    test_env = {k: v for k, v in os.environ.items()
                if not k.startswith(removed_prefixes)
                and not k.endswith(("_API_KEY", "_TOKEN", "_SECRET"))}
    for folder in ("tmp", "cache", "config", "data"):
        (output / folder).mkdir(parents=True, exist_ok=True)
    test_env.update({"PYTHONDONTWRITEBYTECODE": "1",
        "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0", "MARKETOPS_SEARCH_ALLOW_NETWORK": "0",
        "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring", "KEYRING_BACKEND": "null",
        "TMPDIR": str(output / "tmp"), "TEMP": str(output / "tmp"), "TMP": str(output / "tmp"),
        "XDG_CACHE_HOME": str(output / "cache"), "XDG_DATA_HOME": str(output / "data"),
        "XDG_CONFIG_HOME": str(output / "config"),
        "MARKETOPS_RESEARCH_CONFIG": str(output / "config" / "research.json")})
    run = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                         cwd=plugin, capture_output=True, text=True,
                         env=test_env)
    log = (run.stdout + run.stderr).encode()
    (output / "unittest.log").write_bytes(log)
    count = re.search(rb"Ran (\d+) tests?", log)
    if run.returncode or not count or int(count[1]) < 1 or before != source_manifest(plugin):
        return {"ok": False, "reason": "QA_FAILED_OR_SOURCE_CHANGED", "returncode": run.returncode}
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=plugin,
                              capture_output=True, text=True, check=True).stdout.strip()
    receipt = {"schema": "marketops.build-qa.v1", "source_revision": revision,
               "files": before, "test": {"suite": "plugin-unittest-discovery",
               "returncode": 0, "tests_run": int(count[1]), "started_at": started,
               "finished_at": now(), "log_sha256": hashlib.sha256(log).hexdigest()}}
    _write(output / "BUILD_QA.json", receipt)
    return {"ok": True, "receipt": str(output / "BUILD_QA.json"), "tests_run": int(count[1])}


verify_release = verify_build_receipt


def review_schema(provider, payload):
    """Compare actual wire schemas with shipped calls; a digest alone is no review."""
    import jsonschema
    if not isinstance(payload, dict) or not isinstance(payload.get("tools"), list):
        return []
    import serpapi_engine_catalog as _catalog
    samples = ({"search": {"params": {"engine": _catalog.DEFAULT_ENGINE, "q": "public venue",
                "gl": "us", "hl": "en", "num": 3}, "mode": "compact"}}
               if provider == "serpapi" else {
                   "search": {"query": "public venue"},
                   "fetch_content": {"urls": ["https://example.org/"], "format": "html",
                                     "links": False, "image_links": False,
                                     "iframe_links": False, "page_metadata": False}})
    accepted = []
    for tool in payload["tools"]:
        if not isinstance(tool, dict) or tool.get("name") not in samples:
            continue
        sample = samples[tool["name"]]
        schema = tool.get("inputSchema") or tool.get("input_schema")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            continue
        # An empty permissive schema cannot attest the reviewed wire vocabulary.
        if not set(sample) <= set(schema.get("properties", {})):
            continue
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.validate(sample, schema)
        except (jsonschema.ValidationError, jsonschema.SchemaError):
            continue
        accepted.append(provider + "." + tool["name"])
    return sorted(accepted)


def _credential_present(provider):
    if provider == "crawl4ai":
        return True
    if provider == "tinyfish":
        from research_adapter_tinyfish import credential_mode
        try:
            credential_mode()
            return True
        except ValueError:
            return False
    value = os.environ.get(KEYS[provider], "").strip()
    if provider == "typesafe_jev" and not value:
        value = os.environ.get("JEV_API_KEY", "").strip()
    return bool(value and not re.fullmatch(r"\$\{[A-Z_0-9]+\}", value))


def audit(root, record):
    """Append only bounded nonsecret protocol metadata, never vendor bodies."""
    allowed = {"event", "phase", "provider", "endpoint", "host", "method", "version",
               "egress_decision_ref", "tool_count", "schema_sha256", "resource_uri"}
    clean = {k: v for k, v in record.items() if k in allowed}
    clean["at"] = now()
    line = json.dumps(clean, sort_keys=True, separators=(",", ":"))
    path = _path(root, "runtime-audit.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as out:
        out.write(line + "\n")
    return "audit-" + hashlib.sha256(line.encode()).hexdigest()


def bootstrap(root, provider):
    """The user-configured, tools/list-only connection check; no tools/call."""
    if provider not in ENDPOINTS:
        raise ValueError("BOOTSTRAP_PROVIDER_NOT_MCP")
    settings = config(root)
    if provider not in settings.get("providers", ()) or not settings.get("network_enabled"):
        raise ValueError("RESEARCH_NETWORK_DISABLED")
    if not _credential_present(provider):
        raise ValueError("CAPABILITY_NOT_AVAILABLE: credential missing")
    if not verify_build_receipt()["ok"]:
        raise ValueError("CAPABILITY_NOT_AVAILABLE: BUILD_QA_MISSING_OR_STALE")
    from research_runtime_transport import mcp_session, tools_payload
    from research_s0_preflight_gate import bootstrap_probe_schema, snapshot_digest
    from research_egress import open_session
    from urllib.parse import urlsplit
    endpoint = ENDPOINTS[provider]
    host = urlsplit(endpoint).hostname
    # The same actual proxy supplies both connection and the decision reference.
    with open_session(allowlist=frozenset({host})) as egress:
        async def capture():
            async with mcp_session(provider, root=root, egress=egress) as session:
                return tools_payload(await session.list_tools())
        audit(root, {"event": "bootstrap", "phase": "intent", "provider": provider,
                     "method": "tools/list", "endpoint": endpoint})
        payload = asyncio.run(capture())
        accepted = review_schema(provider, payload)
        if not accepted:
            raise ValueError("CAPABILITY_SCHEMA_DRIFT: no reviewed calls match")
        # Acceptance above compares with the shipped contract before a pin exists.
        captured = bootstrap_probe_schema(
            probe=lambda: payload, provider=provider, endpoint=endpoint, allowlist=[host],
            egress_decision=lambda _: egress.browser_egress_ref(),
            audit=lambda record: audit(root, record),
            registry_schema_sha256=snapshot_digest(payload))
    captured[provider]["accepted_capabilities"] = accepted
    _write(_path(root, provider + "-schema.json"), captured[provider])
    return {"provider": provider, "status": "SCHEMA_OBSERVED", "accepted_capabilities": accepted,
            "schema_sha256": captured[provider]["schema_sha256"], "paid_calls": 0,
            "balance": None, "account_balance_verified": False}


def admission(root, capability_id):
    import research_capability_registry as registry
    entry = registry.DEFAULT_REGISTRY.get(capability_id)
    def denied(code, detail):
        return {"allowed": False, "reason_code": code, "detail": detail}
    if entry is None:
        return denied("CAPABILITY_UNKNOWN", "unknown capability")
    if capability_id not in CORE:
        return denied("POLICY_TIER_NOT_GRANTED", "initial product supports bounded research only")
    settings = config(root)
    if not settings.get("network_enabled") or os.environ.get("MARKETOPS_RESEARCH_ALLOW_NETWORK") == "0":
        return denied("RESEARCH_NETWORK_DISABLED", "run local runtime configure first")
    if entry.provider not in settings.get("providers", ()):
        return denied("POLICY_TIER_NOT_GRANTED", "provider not in local consent")
    if type(settings.get("max_calls")) is not int or not 1 <= settings["max_calls"] <= 500:
        return denied("BUDGET_PROJECT_CEILING", "finite request limit required")
    if not verify_build_receipt()["ok"]:
        return denied("CAPABILITY_NOT_AVAILABLE", "BUILD_QA_MISSING_OR_STALE")
    if not _credential_present(entry.provider):
        return denied("CAPABILITY_NOT_AVAILABLE", "PROVIDER_CREDENTIAL_MISSING")
    account = _read(_path(root, entry.provider + "-account.json"))
    if account and not account_observation_fresh(account):
        return denied("WALLET_PRECONDITION_FAILED", "ACCOUNT_OBSERVATION_STALE: confirm a new observation with observe-account --refresh")
    if entry.provider in ENDPOINTS:
        from research_s0_preflight_gate import validate_snapshot_entry, _parse_iso
        from urllib.parse import urlsplit
        snapshot = _read(_path(root, entry.provider + "-schema.json"))
        problems = validate_snapshot_entry(entry.provider, snapshot,
            hosts=[urlsplit(ENDPOINTS[entry.provider]).hostname], as_of=_parse_iso(now()))
        try:
            payload = json.loads(snapshot.get("snapshot_serialized", "{}"))
        except ValueError:
            payload = {}
        if problems or capability_id not in review_schema(entry.provider, payload):
            return denied("CAPABILITY_SCHEMA_DRIFT", "run local bootstrap; schema absent, stale or incompatible")
    if entry.provider == "crawl4ai" and importlib.util.find_spec("crawl4ai") is None:
        return denied("CRAWL4AI_LOCAL_NOT_INSTALLED", "install local crawl runtime")
    return {"allowed": True, "reason_code": "", "detail": "REQUEST_COUNT_ONLY",
            "provider": entry.provider, "balance": None, "dollar_limit_enforced": False}


def status(root=None):
    settings = config(root)
    observations = {p: _read(_path(root, p + "-account.json")) for p in PROVIDERS}
    return {"schema": "marketops.runtime-status.v1", "configured": bool(settings),
            "budget_mode": settings.get("budget_mode"), "max_calls": settings.get("max_calls"),
            "provider_limits": settings.get("provider_limits", {}),
            "dollar_limit_enforced": False, "wallet_floor_enforced": bool(any(observations.values())),
            "qa": verify_build_receipt(), "providers": {
                p: {"selected": p in settings.get("providers", ()),
                    "max_calls": settings.get("provider_limits", {}).get(p, settings.get("max_calls"))
                        if p in settings.get("providers", ()) else None,
                    "credential_present": _credential_present(p), "balance": None,
                    "balance_status": "BASELINE_OBSERVED_CURRENT_UNKNOWN" if observations[p] else "UNKNOWN",
                    "baseline": observations[p] or None,
                    "wallet_floor_configured": bool(observations[p])} for p in PROVIDERS}}


def account_observation_fresh(account):
    observed = (account.get("latest_observation") or account).get("observed_at")
    try:
        moment = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return False
        return 0 <= (datetime.now(timezone.utc) - moment).total_seconds() <= 86400
    except (AttributeError, TypeError, ValueError):
        return False


def usage_snapshot(root, provider, meter):
    """Observed local usage, without opening/migrating a missing ledger."""
    import sqlite3
    from contextlib import closing
    path = _path(root, "usage-ledger.sqlite3")
    if not path.exists():
        return {"settled_units": 0, "unknown_operations": 0, "reserved_operations": 0}
    field = "actual_micros" if meter == "usd_micros" else "quota_units"
    where = "provider=? AND account_ref=?"
    parameters = [provider, provider]
    if meter != "usd_micros":
        where += " AND quota_unit=?"
        parameters.append(meter)
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
            settled, unknown, reserved = db.execute(
                f"SELECT COALESCE(SUM(CASE WHEN charge_state IN ('SETTLED','FREE_TIER') "
                f"THEN {field} ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN charge_state='UNKNOWN' THEN 1 ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN charge_state='RESERVED' THEN 1 ELSE 0 END),0) "
                f"FROM operations WHERE {where}", parameters).fetchone()
    except (sqlite3.Error, OSError):
        raise ValueError("ACCOUNT_USAGE_UNREADABLE") from None
    return {"settled_units": settled, "unknown_operations": unknown, "reserved_operations": reserved}


def observe_account(root, provider, *, units, meter, source_url, confirm=False, refresh=False):
    """Freeze a locally confirmed dashboard observation; never infer it from a key.

This is explicitly an operator observation, not an authenticated account-API
read. The existing ledger enforces the floor in the observed unit and refuses
unknown USD conversion or mismatched meters.
"""
    from urllib.parse import urlsplit
    official = {"serper": "serper.dev", "serpapi": "serpapi.com",
                "tinyfish": "tinyfish.ai", "typesafe_jev": "typesafe.ai"}
    if (confirm is not True or provider not in official or type(units) is not int
            or units < 0 or (not refresh and units == 0)):
        raise ValueError("ACCOUNT_OBSERVATION_REQUIRES_LOCAL_CONFIRMATION")
    url = urlsplit(source_url)
    host = (url.hostname or "").lower()
    if (url.scheme != "https" or url.username or url.password or url.query or url.fragment
            or not (host == official[provider] or host.endswith("." + official[provider]))):
        raise ValueError("OFFICIAL_ACCOUNT_SOURCE_REQUIRED")
    if meter not in {"credits", "searches", "urls", "requests", "usd_micros"}:
        raise ValueError("ACCOUNT_METER_UNSUPPORTED")
    observation = {"provider": provider, "units": units, "meter": meter,
        "source_url": source_url, "trust": "OPERATOR_DASHBOARD_OBSERVATION",
        "observed_at": now(), "source": "VENDOR_ACCOUNT_READ"}
    path = _path(root, provider + "-account.json")
    if path.exists():
        if not refresh:
            raise ValueError("ACCOUNT_BASELINE_ALREADY_FROZEN")
        original = _read(path)
        if original.get("meter") != meter or original.get("provider") != provider:
            raise ValueError("ACCOUNT_REFRESH_METER_MISMATCH")
        snapshot = usage_snapshot(root, provider, meter)
        observation["ledger_snapshot"] = snapshot
        # UNKNOWN and RESERVED are deliberately not subtracted here. The
        # ledger keeps holding them; a dashboard observation is not settlement.
        external = max(0, original["units"] - units - snapshot["settled_units"])
        history = _path(root, provider + "-account-history.jsonl")
        fd = os.open(history, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as out:
            out.write(json.dumps({"reobserved_at": observation["observed_at"],
                                 "previous": original}, sort_keys=True) + "\n")
        # The B0 and its original timestamp never move. A top-up is visible as
        # an observation but cannot increase the original campaign allowance.
        _write(path, {**original, "latest_observation": observation,
                      "external_spend_units": external,
                      "unknown_exposure_policy": "RETAINED_CONSERVATIVELY_NOT_RECONCILED"})
        return {"provider": provider, "status": "ACCOUNT_OBSERVATION_REFRESHED",
                "trust": observation["trust"], "original_baseline_preserved": True,
                "ledger_unchanged": True, "balance_verified_by_api": False}
    if refresh:
        raise ValueError("ACCOUNT_BASELINE_REQUIRED_BEFORE_REFRESH")
    _write(path, observation)
    return {"provider": provider, "status": "ACCOUNT_BASELINE_RECORDED",
            "trust": observation["trust"], "balance_verified_by_api": False}


def ledger_for(root, provider):
    import research_usage_ledger as usage
    observation = _read(_path(root, provider + "-account.json"))
    if not observation:
        return usage.for_root(root)
    if not account_observation_fresh(observation):
        raise usage.LedgerRefused("CAMPAIGN_BASELINE_STALE", "confirm a new account observation with --refresh")
    baseline = usage.CampaignBaseline(provider=provider, account_ref=provider,
        units=observation["units"], meter=observation["meter"],
        observed_at=observation["observed_at"], source=usage.MeterReadingSource(observation["source"]),
        campaign_id="local-" + hashlib.sha256(str(_root(root)).encode()).hexdigest()[:16])
    # Freshness applies to the latest real observation (checked above), while
    # the immutable original B0 continues to determine the floor.
    budget = usage.CampaignBudget(usage.CampaignFloor(baseline),
        external_spend_units=observation.get("external_spend_units", 0))
    return usage.for_root(root, campaign_budgets={(provider, provider): budget})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("configure", "status", "bootstrap", "verify-build", "observe-account"))
    parser.add_argument("--data-root")
    parser.add_argument("--provider", action="append", choices=(*PROVIDERS, "jev"))
    parser.add_argument("--domains", default="")
    parser.add_argument("--max-calls", type=int, default=0)
    parser.add_argument("--provider-limit", action="append", metavar="PROVIDER=N",
                        help="cumulative supplier limit (repeatable); omitted providers inherit max-calls")
    parser.add_argument("--task", default="")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--amend", action="store_true",
                        help="explicitly revise consent in the same data root; max-calls stays cumulative")
    parser.add_argument("--output")
    parser.add_argument("--units", type=int)
    parser.add_argument("--meter")
    parser.add_argument("--source-url")
    parser.add_argument("--refresh", action="store_true",
                        help="record a new current account observation without rebasing B0 or clearing usage")
    args = parser.parse_args(argv)
    if args.command != "verify-build":
        try:
            from research_credentials import load_into_environment
            load_into_environment()
        except ImportError:
            pass
    try:
        if args.command == "configure":
            limits = None
            if args.provider_limit is not None:
                limits = {}
                for spec in args.provider_limit:
                    provider, separator, count = spec.partition("=")
                    if not separator or not count.isdecimal() or provider in limits:
                        raise ValueError("PROVIDER_LIMITS_INVALID")
                    limits[provider] = int(count)
            result = configure(args.data_root, providers=args.provider or [],
                domains=[x.strip() for x in args.domains.split(",") if x.strip()],
                max_calls=args.max_calls, task=args.task, confirm=args.confirm, amend=args.amend,
                provider_limits=limits)
        elif args.command == "observe-account":
            if len(args.provider or []) != 1:
                raise ValueError("ONE_PROVIDER_REQUIRED")
            result = observe_account(args.data_root, args.provider[0], units=args.units,
                                     meter=args.meter, source_url=args.source_url or "", confirm=args.confirm,
                                     refresh=args.refresh)
        elif args.command == "bootstrap":
            result = [bootstrap(args.data_root, p) for p in (args.provider or [])]
        elif args.command == "verify-build":
            if not args.output:
                raise ValueError("OUTPUT_REQUIRED")
            result = verify_build(args.output)
        else:
            result = status(args.data_root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not isinstance(result, dict) or result.get("ok", True) else 2
    except Exception as exc:
        # Upstream and credential exceptions may contain secrets. No raw echo.
        code = str(exc).split(":", 1)[0] if type(exc) is ValueError else "RUNTIME_SETUP_FAILED"
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", code):
            code = "RUNTIME_SETUP_FAILED"
        print(json.dumps({"status": "BLOCKED", "reason": code, "reason_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

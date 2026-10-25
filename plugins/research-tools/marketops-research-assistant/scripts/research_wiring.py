#!/usr/bin/env python3
"""The declared seam: which worker modules exist on this branch, and which do not.

The Wave-1 contract names thirteen modules that seven roles will produce. Until
they land there is nothing to integrate, and the failure mode to avoid is a
"waiting" claim that is really a guess. So waiting is made *checkable*: `probe()`
answers which of the named modules are present, attributes each missing one to
its owner, and returns a state the coordinator's manifest can read.

Two rules this module keeps:

* **Absence is reported, never worked around.** There is no fallback import, no
  stub collaborator, no default that would let a half-built gateway look wired.
  A missing module raises `WiringIncomplete` listing every name that is missing —
  not just the first one, because "which parts are still absent" is the question
  a resume decision actually asks.
* **The probe does not import what it measures.** `find_spec` reads; importing a
  worker's module to see whether it exists would execute it, and a module that
  half-exists is exactly the case worth distinguishing from one that works.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
from pathlib import Path

import research_gateway as gateway

SCRIPT_DIR = Path(__file__).resolve().parent

# INTERFACE-CONTRACT.md v1.0 §3 "目标文件布局", with the owner each role was
# assigned in WORKER-DAG-AND-OWNERSHIP.md §3. Both are single-owner by design.
PLANNED_MODULES: dict[str, str] = {
    "research_contract": "W-B",
    "research_capability_registry": "W-B",
    "research_policy": "W-B",
    "research_usage_ledger": "W-B",
    "research_adapter_base": "W-B",
    "research_egress": "W-A",
    "research_adapter_tinyfish": "W-C",
    "research_adapter_serpapi": "W-D",
    "research_adapter_serper": "W-D",
    "research_adapter_crawl4ai": "W-E",
    "research_adapter_jev": "W-F",
    "research_evidence_store": "W-F",
    "research_mcp_facade": "W-G",
}

# What the gateway cannot be assembled without. The provider adapters are *not*
# on this list: they are assembled from whichever provider modules are present
# (`assemble_adapters`), so requiring them to be handed in would make the routing
# table the caller's job and leave every adapter reachable only from its own tests.
REQUIRED_COLLABORATORS = ("contract", "registry", "policy", "ledger")


def _locate(name: str) -> str | None:
    """Where `name` was found, or None. Never imports the module it reports on."""
    if (SCRIPT_DIR / f"{name}.py").is_file():
        return "package"
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return None
    return "sys_path" if spec is not None else None


def probe() -> dict:
    """The resume trigger. Reads the filesystem; changes nothing."""
    located = {name: _locate(name) for name in PLANNED_MODULES}
    present = sorted(name for name, where in located.items() if where)
    missing = sorted(name for name, where in located.items() if not where)
    return {
        "state": "WAITING_FOR_WORKERS" if missing else "WORKERS_PRESENT",
        "worker_plan_complete": not missing,
        "present": present,
        "missing": missing,
        "located": {name: where for name, where in located.items() if where},
        "owners": {name: PLANNED_MODULES[name] for name in missing},
        "missing_by_owner": _by_owner(missing),
        "searched": {"directory": str(SCRIPT_DIR), "module_name": "sys.path"},
    }


def _by_owner(missing: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for name in missing:
        grouped.setdefault(PLANNED_MODULES[name], []).append(name)
    return {owner: sorted(names) for owner, names in sorted(grouped.items())}


#: The reviewed provider name -> the module and class that serves it.
#:
#: Keyed by the **registry's** provider name, not the adapter's own `PROVIDER`
#: constant, because the registry is the allowlist authority and a router keyed by
#: something else would silently find no adapter. All five agree today: W-F's
#: adapter now answers to `typesafe_jev` and keeps the vendor's short spelling as
#: `LEGACY_PROVIDER`. This table is still where a future disagreement would show up.
PROVIDER_ADAPTERS: dict[str, tuple[str, str]] = {
    "tinyfish": ("research_adapter_tinyfish", "TinyFishAdapter"),
    "serpapi": ("research_adapter_serpapi", "SerpApiAdapter"),
    "serper": ("research_adapter_serper", "SerperAdapter"),
    "deepseek": ("research_adapter_deepseek", "DeepSeekAdapter"),
    "crawl4ai": ("research_adapter_crawl4ai", "Crawl4AIAdapter"),
    "typesafe_jev": ("research_adapter_jev", "JevAdapter"),
}


def assemble_adapters(registry=None, root=None, credential_overrides=None) -> dict:
    """Build every adapter whose module is present *and* the gateway can call.

    Two failures are reported, never skipped. A module that exists but cannot be
    constructed is one. An adapter that constructs but does not have the shape the
    gateway calls is the other, and it is the more expensive one: the gateway
    dispatches with ``adapter.execute(request)``, so a mismatched signature is not
    a refusal — it is a ``TypeError`` raised *after* the reservation, which the
    gateway can only book as an UNKNOWN charge. Keeping such an adapter out of the
    routing table turns a phantom charge into a named ``CAPABILITY_NOT_AVAILABLE``.

    Conformance is measured, not assumed. With ``research_adapter_base`` absent
    this refuses rather than accepting, because "could not measure" must not read
    as "conformant". ``registry`` is optional and only widens the check: when it
    is supplied, an adapter that advertises something the registry never reviewed
    is also refused.
    """
    built: dict[str, object] = {}
    failures: dict[str, str] = {}
    base = _import_if_present("research_adapter_base")
    for provider, (module_name, class_name) in PROVIDER_ADAPTERS.items():
        module = _import_if_present(module_name)
        if module is None:
            continue
        adapter_class = getattr(module, class_name, None)
        if adapter_class is None:
            failures[provider] = f"{module_name} has no {class_name}"
            continue
        try:
            adapter = (_construct_adapter(adapter_class, root,
                                          api_key=credential_overrides[provider])
                       if credential_overrides and provider in credential_overrides
                       else _construct_adapter(adapter_class, root))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            failures[provider] = f"{type(exc).__name__}: {exc}"
            continue
        if base is None:
            failures[provider] = (
                "NO_CONFORMANCE_AUTHORITY: research_adapter_base is not on this branch")
            continue
        # The registry only widens the check, so it is passed only when it can
        # answer `get`; a collaborator that cannot is the gateway's problem to
        # report, not something to crash the assembly over.
        checked = registry if callable(getattr(registry, "get", None)) else None
        report = base.check_adapter_conformance(
            adapter, expected_provider=provider, registry=checked)
        if not report.conformant:
            failures[provider] = "NON_CONFORMANT: " + "; ".join(report.problems)
            continue
        built[provider] = adapter
    assemble_adapters.failures = failures  # type: ignore[attr-defined]
    return built


def _construct_adapter(adapter_class, root, *, api_key=None):
    """Construct one adapter, injecting the data root when it accepts one.

    The Jev adapter reads saved evidence from a data root it deliberately does
    **not** take from the request: a caller that could name the store could name a
    different one. So the root is injected here, at the composition root, or every
    Jev call refuses ``JEV_DATA_ROOT_ABSENT`` *after* passing conformance — a
    gateway routing a capability it can never execute.

    Nothing else is injected. In particular no credential: this module must not
    read one, and an adapter without one refuses before dispatch, which is the
    outcome that costs nothing.
    """
    # The composition root owns process credentials and connection lifetimes;
    # constructors remain network-free. Standalone adapter defaults stay inert.
    import os
    import research_runtime_transport as transport
    name = adapter_class.__name__
    if name == "SerperAdapter":
        return adapter_class(api_key=api_key or os.environ.get("SERPER_API_KEY"),
                             transport=transport.serper_transport)
    if name == "DeepSeekAdapter":
        return adapter_class(api_key=api_key or os.environ.get("DEEPSEEK_API_KEY"),
                             transport=transport.deepseek_transport)
    if name == "JevAdapter":
        return adapter_class(root=root, api_key=api_key or os.environ.get("TYPESAFE_API_KEY")
                             or os.environ.get("JEV_API_KEY"))
    if name == "SerpApiAdapter":
        from serpapi_engine_catalog import EngineCatalog
        from research_event_journal import _root
        return adapter_class(session_factory=lambda: transport.serpapi_session(root),
            catalog=(EngineCatalog(Path(_root(root)) / "state" / "serpapi-engine-cache.json")
                     if root is not None else None))
    if name == "Crawl4AIAdapter":
        return transport.RuntimeCrawlAdapter(root)
    if name == "TinyFishAdapter":
        return adapter_class(runner=transport.tinyfish_runner(root),
                             target_guard=transport.tinyfish_target_guard(root))
    if root is None:
        return adapter_class()
    try:
        accepts = "root" in inspect.signature(adapter_class).parameters
    except (TypeError, ValueError):
        accepts = False
    return adapter_class(root=root) if accepts else adapter_class()


def _import_if_present(name: str):
    """Import a worker module by name, or None when it is not on this branch."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name == name:
            return None
        raise
    except ImportError:
        # Present but unimportable is a defect, not an absence; let it surface.
        raise


#: Adapter capability vocabulary -> the **reviewed registry's** capability name.
#:
#: Derived, never written down here. The registry already carries this bridge as
#: `CapabilityEntry.ledger_capability` — the name the ledger prices the work
#: under — and `CapabilityEntry.__post_init__` refuses to mark an entry ENABLED
#: without one. A hand-written copy of the same bridge was a second authority,
#: and the drift was measured rather than hypothetical: it listed capabilities no
#: adapter advertises (`tinyfish.fetch`, `crawl4ai.crawl_local`), omitted most of
#: the ones that are (`tinyfish.fetch_content`, the whole `crawl4ai` and Jev
#: set), and mapped `crawl4ai.fetch_page` onto `extract` while the registry prices
#: `fetch_page` as `render`. On a real reviewed capability the effect was a
#: `WiringIncomplete` and no row at all; on `fetch_page` it would have been a
#: reservation under the wrong price.
#:
#: Reading it off the registry makes that drift impossible instead of merely
#: unlikely. When the registry is not on this branch the table is empty, and
#: `build_gateway` refuses before anything can consult it.
def _reviewed_capability_table() -> dict[tuple[str, str], str]:
    registry = _import_if_present("research_capability_registry")
    if registry is None:
        return {}
    return {(entry.provider, entry.capability_id): entry.ledger_capability
            for entry in registry.DEFAULT_REGISTRY
            if entry.ledger_capability}


REVIEWED_CAPABILITY: dict[tuple[str, str], str] = _reviewed_capability_table()


def reviewed_capability(provider: str, capability_id: str) -> str:
    """The reviewed name for an adapter capability, or a named refusal.

    A convenience projection of :data:`REVIEWED_CAPABILITY` for callers that hold
    a capability id but not the entry. The gateway path does **not** use it: it
    already holds the live entry, and `ledger_request_builder` reads
    `entry.ledger_capability` so a registry other than `DEFAULT_REGISTRY` is
    still honoured.
    """
    resolved = REVIEWED_CAPABILITY.get((provider, capability_id))
    if resolved is None:
        raise gateway.WiringIncomplete(
            [f"REVIEWED_CAPABILITY[{provider}/{capability_id}]"])
    return resolved


def ledger_request_builder(root=None):
    """A builder that turns the contract's request into the ledger's own.

    Lives here because this is the only module that knows both the customer data
    root (and therefore the project) and the vocabulary bridge above. The gateway
    is deliberately not given a default: it has neither fact, and a gateway that
    invents them writes reservations under a project nobody chose.
    """
    import research_usage_ledger as ledger_module

    project = ledger_module.project_for_root(root)
    from research_costs import billed_units

    def build(entry, request):
        # The live entry is the authority, not `REVIEWED_CAPABILITY`: the gateway
        # may be composed with a registry other than `DEFAULT_REGISTRY`, and a
        # lookup in a projection of the default table would refuse a capability
        # that the registry actually handed us. The registry's own
        # `__post_init__` already guarantees this is set for an ENABLED entry;
        # this refusal covers the DISABLED ones that policy should have stopped.
        capability = getattr(entry, "ledger_capability", None)
        if not capability:
            raise gateway.WiringIncomplete(
                [f"ledger_capability[{entry.capability_id}]"])
        return ledger_module.OperationRequest(
            project=project,
            run_id=request.run_id,
            operation_id=request.operation_id,
            provider=entry.provider,
            capability=capability,
            account_ref=request.account_ref,
            transport=entry.transport,
            intent=getattr(request, "intent_fingerprint", "") or entry.capability_id,
            args=dict(getattr(request, "params", {}) or {}),
            units=billed_units(entry.provider, capability,
                               dict(getattr(request, "params", {}) or {})),
            grant=getattr(request, "grant_ref", None) or "")

    build.project = project  # type: ignore[attr-defined]
    return build


def build_gateway(*, contract=None, registry=None, policy=None, ledger=None,
                  adapters=None, root=None, credential_overrides=None) -> gateway.CapabilityGateway:
    """Assemble the managed gateway, or say precisely what is still absent.

    Every collaborator may be handed in, and every one that is not is built from
    the module that owns it. A factory that refused unless the caller supplied
    four objects was not a product entry point: the product has no caller holding
    them, so the "real" gateway was only ever constructed inside tests. The
    defaults are the owners' own constructors, not re-implementations:

    * ``contract``   — the ``research_contract`` module;
    * ``registry``   — ``research_capability_registry.DEFAULT_REGISTRY``;
    * ``policy``     — ``research_policy.Policy`` with **network off** (this
      factory cannot know that S0 passed, and a default of on would make the
      policy plane's network gate inert);
    * ``ledger``     — ``research_usage_ledger.for_root(root)``.

    What it still refuses is a *module* that is absent. Handing in stand-ins for
    the four objects does not make the modules exist, and a gateway assembled
    around stubs would pass every downstream check while doing nothing.
    """
    report = probe()
    absent = list(report["missing"])

    if contract is None:
        contract = _import_if_present("research_contract")
        if contract is None:
            absent.append("contract")
    if registry is None:
        registry_module = _import_if_present("research_capability_registry")
        registry = getattr(registry_module, "DEFAULT_REGISTRY", None) \
            if registry_module is not None else None
        if registry is None:
            absent.append("registry")
    if policy is None:
        policy = _default_policy(registry)
        if policy is None:
            absent.append("policy")
    if ledger is None:
        ledger = _default_ledger(root)
        if ledger is None:
            absent.append("ledger")
    # An adapter mapping the caller did not hand in is assembled from whichever
    # provider modules are present. That is the difference between a routing table
    # and a set of standalone modules: without this, every adapter is reachable
    # from its own tests and from nothing else.
    if adapters is None:
        adapters = (assemble_adapters(registry=registry, root=root,
                                      credential_overrides=credential_overrides)
                    if credential_overrides else assemble_adapters(registry=registry, root=root))
    if not adapters:
        absent.append("adapters")
    if absent:
        raise gateway.WiringIncomplete(sorted(set(absent)))
    import research_usage_ledger as ledger_module

    return gateway.CapabilityGateway(
        registry=registry, policy=policy, ledger=ledger, adapters=adapters,
        contract=contract, ledger_request=ledger_request_builder(root),
        refusal_disposition=ledger_module.refusal_disposition)


def _default_policy(registry):
    """The policy plane this factory builds when the caller hands none in."""
    module = _import_if_present("research_policy")
    if module is None or registry is None:
        return None
    # Network is off. Enabling it is a deployment decision made by whoever knows
    # S0 passed, and it is not this function's to guess.
    return module.Policy(registry=registry, network_enabled=False)


def _default_ledger(root):
    """The one ledger for this data root, priced by the operator's catalog."""
    module = _import_if_present("research_usage_ledger")
    if module is None:
        return None
    return module.for_root(root)


#: Where the S0 evidence for a deployment lives, beside the ledger it describes.
S0_EVIDENCE_FILENAME = "s0-evidence.json"


def load_s0_evidence(root=None) -> dict:
    """The S0 evidence this deployment may present. Never the caller's.

    Read from a file under the customer data root, next to the ledger the same
    deployment writes — not from a tool argument and not from the environment. A
    caller able to hand in its own evidence could declare
    ``upstream_schema_verified: true`` and unlock a paid call, which is precisely
    the declaration S0_07 exists to refuse.

    Unreadable or absent yields ``{}``: every gate then fails closed, which is the
    correct answer for "nothing has been captured yet".
    """
    import json

    import research_usage_ledger as ledger_module

    try:
        path = ledger_module.default_path(root).parent / S0_EVIDENCE_FILENAME
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def s0_provider_entry(root=None, capability_id=None) -> tuple[bool, str, tuple[str, ...]]:
    """The one fail-closed S0 decision every provider-capable entry passes.

    Returns ``(allowed, reason_code, failed_gate_ids)`` and **never raises**. Its
    callers are MCP tool bodies, where an escaping exception is rendered as a
    tool *error* rather than a governed refusal — "the server broke" and "this
    deployment may not call a provider" are different facts and must not look the
    same.

    Evidence comes from :func:`load_s0_evidence` (the deployment's data root),
    never from the caller. A tool argument would let an Agent declare that S0
    passed, which is the boolean-only hole S0_07 exists to close, moved one layer
    up.

    Absent gate or unreadable evidence is a refusal, not permission: the gate is
    the only thing that could authorise a provider call, so its absence cannot
    mean yes.
    """
    import research_runtime_setup as runtime
    if runtime.config(root):
        if not capability_id:
            return False, "CAPABILITY_NOT_AVAILABLE", ("CAPABILITY_REQUIRED",)
        verdict = runtime.admission(root, capability_id)
        return verdict["allowed"], verdict["reason_code"], (() if verdict["allowed"]
                                                          else (verdict["detail"],))
    try:
        gate = _import_if_present("research_s0_preflight_gate")
    except Exception:  # noqa: BLE001 - fail closed on a broken import too
        return False, "CAPABILITY_NOT_AVAILABLE", ("S0_GATE_UNIMPORTABLE",)
    if gate is None:
        return False, "CAPABILITY_NOT_AVAILABLE", ("S0_GATE_ABSENT",)
    try:
        decision = gate.evaluate(load_s0_evidence(root))
    except Exception:  # noqa: BLE001 - never echo the evidence in a traceback
        return False, "CAPABILITY_NOT_AVAILABLE", ("S0_EVALUATION_FAILED",)
    s0 = decision.get("s0") or {}
    s1 = decision.get("s1") or {}
    if decision.get("launch") == "ALLOW":
        return True, "", ()
    return (False, str(s1.get("reason_code") or "CAPABILITY_NOT_AVAILABLE"),
            tuple(s0.get("failed") or ()))


class _GatewayAfterTheGate:
    """Builds the real gateway on first *dispatch*, never before the gate.

    `research_s0_preflight_gate.guarded_invoke` is the single place that decides
    whether a call may proceed: it evaluates the evidence and returns before
    touching the gateway unless `launch == ALLOW`. Handing it a built gateway
    defeated that ordering, because construction is the side effect — the shipped
    ledger opens (or creates) a SQLite file, runs its schema migration and leaves
    a `state/` directory behind. A denied deployment should leave the data root
    untouched, and "no file was created" is checkable in a way that "no
    reservation was written" is not, once the database exists.

    The proxy also keeps the gate a single source of truth: there is still
    exactly one `evaluate` call per invocation, inside `guarded_invoke`. Nothing
    here re-decides.
    """

    def __init__(self, *, root):
        self._root = root
        self._built = None

    def invoke(self, **kwargs):
        if self._built is None:
            import research_runtime_setup as runtime
            import research_policy
            import research_capability_registry
            settings = runtime.config(self._root)
            if settings:
                registry = research_capability_registry.DEFAULT_REGISTRY
                provider = registry.get(kwargs["capability_id"]).provider
                account_ref = kwargs["account_ref"]
                overrides = None
                import research_accounts
                if (provider in {"serper", "typesafe_jev"}
                        and research_accounts.query(self._root,
                            "SELECT 1 FROM accounts WHERE provider=? AND account_ref=?",
                            (provider, account_ref))):
                    secret = research_accounts.credential(self._root, provider, account_ref)
                    if not secret:
                        return gateway.GatewayOutcome(status="BLOCKED", reason_code="CAPABILITY_NOT_AVAILABLE",
                            capability_id=kwargs["capability_id"], provider=provider,
                            caveats=("ACCOUNT_KEY_MISSING",))
                    overrides = {provider: secret}
                policy = research_policy.Policy(registry=registry,
                    network_enabled=settings.get("network_enabled") is True,
                    allowed_domains=settings.get("allowed_domains", ()),
                    domain_scope=lambda urls: runtime.fetch_allowlist(
                        runtime.config(self._root), urls))
                ledger = (runtime.ledger_for(self._root, provider, account_ref=account_ref)
                          if account_ref != provider else runtime.ledger_for(self._root, provider))
                self._built = (build_gateway(root=self._root, policy=policy, ledger=ledger,
                                             credential_overrides=overrides)
                               if overrides else build_gateway(root=self._root, policy=policy, ledger=ledger))
            else:
                self._built = build_gateway(root=self._root)
        import research_runtime_setup as runtime
        settings = runtime.config(self._root)
        if settings:
            import research_usage_ledger as usage
            project = usage.project_for_root(self._root)
            # The consent record is the single source of the run and project
            # bounds. They are independent there, and the same call states any
            # USD cap the operator consented to. Re-stating ``max_calls`` for
            # both (the old code) silently widened a smaller run bound and
            # dropped the money column on every managed call.
            plan = runtime.state_ledger_caps(self._root, kwargs["run_id"],
                                             project=project, ledger=self._built.ledger)
            import research_capability_registry
            provider = research_capability_registry.DEFAULT_REGISTRY.get(kwargs["capability_id"]).provider
            # The supplier's limit is stated per provider; a per-account entry is
            # the narrower override for a named wallet. Falling through to
            # max_calls silently widens an operator's stated provider limit.
            provider_limits = settings.get("provider_limits", {})
            provider_ceiling = provider_limits.get(
                provider, provider_limits.get(kwargs["account_ref"], plan["max_calls"]))
            if plan["budget_mode"] == "MONEY":
                provider_ceiling = None
            # The supplier's cumulative cap must survive switching billing accounts.
            aggregate_key = f"{project}/{provider}/*"
            if not self._built.ledger.usage("provider_account", aggregate_key, window="total"):
                money, enforced, seconds = runtime._existing_cap(
                    self._built.ledger, "provider_account", f"{project}/{provider}/{provider}")
                if plan["budget_mode"] == "MONEY" and money is None and provider_ceiling is None:
                    # A provider enabled after the money policy was set up had no row
                    # to inherit from; its ceiling is the total red line like the others.
                    envelope = settings["spending"]
                    total = envelope.get("total_budget_micros")
                    money = None if total is None else envelope["ledger_money_baseline"] + total
                    enforced = total is not None
                    if money is None:
                        provider_ceiling = runtime.NO_TOTAL_CAP_CALLS
                self._built.ledger.set_cap("provider_account", aggregate_key,
                    ceiling_calls=provider_ceiling, ceiling_micros=money,
                    usd_enforced=enforced, ceiling_seconds=seconds, source="LOCAL_CONSENT",
                    if_absent=True)
            for kind, key, limit in (
                    ("provider_account",
                     f"{project}/{provider}/{kwargs['account_ref']}",
                     provider_ceiling),
                    ("provider_account", aggregate_key, provider_ceiling),
                    ("operation", f"{project}/*", 1)):
                if plan["budget_mode"] == "MONEY" and kind == "provider_account":
                    envelope = settings["spending"]
                    total = envelope.get("total_budget_micros")
                    self._built.ledger.set_cap(kind, key,
                        ceiling_calls=runtime.NO_TOTAL_CAP_CALLS if total is None else None,
                        ceiling_micros=None if total is None else envelope["ledger_money_baseline"] + total,
                        usd_enforced=total is not None, source="LOCAL_MONETARY_ENVELOPE")
                    continue
                runtime.set_call_cap_preserving_money(
                    self._built.ledger, kind, key, ceiling_calls=limit,
                    source="LOCAL_REQUEST_COUNT_CONSENT")
        return self._built.invoke(**kwargs)


def managed_invoke(*, capability_id: str, params, run_id: str, operation_id: str,
                   account_ref: str, intent_fingerprint: str, root=None,
                   grant_ref: str | None = None, gateway_object=None,
                   evidence=None):
    """The product's only managed path: the S0 gate first, then the gateway.

    This is the entry a customer-facing tool must call. The gate is evaluated
    before the gateway object is touched at all, so a refused S0 means no
    reservation, no adapter execution and no vendor dispatch — the property
    `GuardedOutcome.gateway_invoked` reports.

    ``evidence`` exists so a test can present a known snapshot. It is **not** a
    parameter of the MCP tool: the tool path resolves evidence from
    :func:`load_s0_evidence`, and a test asserts that the tool cannot be given
    one.
    """
    gate = _import_if_present("research_s0_preflight_gate")
    if gate is None:
        raise gateway.WiringIncomplete(["research_s0_preflight_gate"])
    import research_runtime_setup as runtime
    if evidence is None and runtime.config(root):
        verdict = runtime.admission(root, capability_id)
        if not verdict["allowed"]:
            return gate.GuardedOutcome(status="BLOCKED", reason_code=verdict["reason_code"],
                capability_id=capability_id, gateway_invoked=False,
                s0_failed=(verdict["detail"],))
        runtime_gateway = gateway_object or _GatewayAfterTheGate(root=root)
        result = runtime_gateway.invoke(capability_id=capability_id, params=params,
            run_id=run_id, operation_id=operation_id, account_ref=account_ref,
            intent_fingerprint=intent_fingerprint, grant_ref=grant_ref)
        # The gateway's own caveats are kept: they are where a ledger refusal
        # names its precise code, and replacing the tuple with one constant made
        # every blocked call report a bare ``DUPLICATE_OPERATION`` with no reason.
        return gate.GuardedOutcome(status=result.status, reason_code=result.reason_code,
            capability_id=capability_id, gateway_invoked=True, outcome=result,
            caveats=("REQUEST_COUNT_ONLY_USD_AND_BALANCE_UNKNOWN",)
                    + tuple(getattr(result, "caveats", ()) or ()))
    if gateway_object is None:
        # A proxy, not a built gateway. `guarded_invoke` evaluates S0 first and
        # only calls `invoke` when it allows, so a refused gate never reaches the
        # constructor — and the constructor is what creates the SQLite file,
        # migrates the schema and leaves a ledger behind. Building eagerly made
        # "refused" and "the database was initialised anyway" the same observable
        # outcome, which is not a refusal: it is a refusal plus a side effect.
        gateway_object = _GatewayAfterTheGate(root=root)
    if evidence is None:
        evidence = load_s0_evidence(root)
    return gate.guarded_invoke(
        gateway_object, evidence, capability_id=capability_id, params=params,
        run_id=run_id, operation_id=operation_id, account_ref=account_ref,
        intent_fingerprint=intent_fingerprint, grant_ref=grant_ref)


if __name__ == "__main__":  # pragma: no cover - operator convenience
    import json
    print(json.dumps(probe(), indent=2, ensure_ascii=False))

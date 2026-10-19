"""P1-04 contract: what a *shell-capable host Agent* can and cannot authorize.

Acceptance source: ``MARKETOPS_ARCHITECTURE_CONVERGENCE_DECISION_AND_P0_P2_PLAN_2026-10-17.md``
§2 row "Local human authorization" and Appendix D "P1-04 Approval isolation", plus
the owner brief §4 tier table (T3 = paid interactive automation, T4 = authenticated
/ recurring / state-changing).

The threat model under test is specific and must not be softened into "an
operator clicked yes": the host Agent runs as the *same OS user* as the plugin,
so it can read the plugin's environment, choose its own environment variables and
invoke the plugin's own entry points. Anything the plugin decides purely from its
process environment is therefore decided by the Agent.

Tests that the current candidate satisfies are green. Tests that encode the
*contract* — which the candidate does not yet meet — are deliberately red and
name the contract in their docstring.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from conftest import PLUGIN_DIR, SCRIPTS

SYNTHETIC_KEY = "qa-gate-SYNTHETICKEY-DEADBEEF"


def _wallet_result(balance: str, unit: str, label: str = "TinyFish Agent"):
    """A provider wallet quote shaped like the MCP structuredContent payload."""
    return SimpleNamespace(structuredContent={
        "available_balance": balance,
        "rates": {"meters": [{"label": label, "unit_amount": unit}]},
    })


def _agent_arguments(**overrides):
    arguments = {
        "url": "https://allowed.example.org/start",
        "goal": "collect the opening hours shown on this page",
        "agent_config": {"max_steps": 5, "max_duration_seconds": 60},
    }
    arguments.update(overrides)
    return arguments


def _paid_agent_env(monkeypatch):
    """Everything a compromised *host Agent* could set for itself."""
    monkeypatch.setenv("MARKETOPS_RESEARCH_ALLOW_NETWORK", "1")
    monkeypatch.setenv("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", "10")
    monkeypatch.setenv("MARKETOPS_TINYFISH_AGENT_ALLOW_PAID", "1")
    monkeypatch.setenv("TINYFISH_API_KEY", SYNTHETIC_KEY)
    monkeypatch.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", "allowed.example.org")


# --- T4: capabilities that must be refused outright ----------------------


def test_site_profile_and_vault_are_refused_outright(clean_env, monkeypatch):
    """T4 (owner brief §4): "Off; separately scoped explicit authorization".

    The embedded bridge must not accept a browser profile or vault handle from
    any caller at all — there is no environment variable that turns it on.
    """
    import research_official_mcp as mcp

    _paid_agent_env(monkeypatch)
    for argument in ({"use_profile": True}, {"use_vault": True}):
        with pytest.raises(ValueError, match="TINYFISH_SITE_PROFILE_REQUIRES_SEPARATE_AUTHORIZATION"):
            mcp._validate("tinyfish", "run_web_automation", _agent_arguments(**argument))


def test_an_non_lite_browser_profile_is_refused(clean_env, monkeypatch):
    """T4: an authenticated browser profile is never a silent default."""
    import research_official_mcp as mcp

    _paid_agent_env(monkeypatch)
    with pytest.raises(ValueError, match="UNAPPROVED_BROWSER_PROFILE"):
        mcp._validate("tinyfish", "run_web_automation",
                      _agent_arguments(browser_profile="customer-logged-in"))


def test_no_environment_variable_can_enable_a_site_profile(clean_env, monkeypatch):
    """T4 has no environment escape hatch anywhere in the plugin scripts.

    This is the strong form of "off by default": not a default that a variable
    flips, but the absence of the switch. It is asserted over the source so a
    future 'convenience' variable cannot appear unnoticed.
    """
    import re

    offenders = []
    for path in sorted(SCRIPTS.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"(?i)environ(?:\.get\(|\[)\s*[\"']([A-Z0-9_]*(PROFILE|VAULT|LOGIN|SESSION)[A-Z0-9_]*)[\"']", text):
            offenders.append(f"{path.name}:{match.group(1)}")
    assert offenders == [], f"environment switches exist for T4 capabilities: {offenders}"


def test_recurring_monitors_are_not_an_enabled_action(clean_env):
    """T4 "long-running monitors" must not be reachable through the action table."""
    import research_provider_extensions as ext

    assert not [a for a in ext.ALLOWED_ACTIONS if "monitor" in a or "recur" in a], (
        f"recurring capability is enabled: {sorted(ext.ALLOWED_ACTIONS)}")


# --- T3: caps that must hold before a billable session exists -------------


def test_a_paid_agent_run_without_provider_bounded_caps_is_refused(clean_env, monkeypatch):
    """T3 (owner brief §4): "time/steps/estimated USD caps" before dispatch."""
    import research_official_mcp as mcp

    _paid_agent_env(monkeypatch)
    for config in ({}, {"max_steps": 5}, {"max_duration_seconds": 60},
                   {"max_steps": 0, "max_duration_seconds": 60},
                   {"max_steps": 5, "max_duration_seconds": 9999}):
        with pytest.raises(ValueError, match="TINYFISH_AGENT_HARD_CAP_REQUIRED"):
            mcp._validate("tinyfish", "run_web_automation",
                          _agent_arguments(agent_config=config))


def test_the_paid_agent_is_denied_by_default(clean_env, monkeypatch):
    """T3 default is OFF; an unset variable is a refusal, not a lenient default."""
    import research_official_mcp as mcp

    monkeypatch.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", "allowed.example.org")
    with pytest.raises(ValueError, match="TINYFISH_AGENT_PAID_NOT_AUTHORIZED"):
        mcp.call("tinyfish", "run_web_automation", _agent_arguments(),
                 max_paid_usd="1", runner=_never_called)


def test_the_usd_ceiling_is_bounded_in_code(clean_env, monkeypatch):
    """T3: "cost-unknown fail-closed" and a code-level USD insurance bound."""
    import research_official_mcp as mcp

    _paid_agent_env(monkeypatch)
    for ceiling in ("", "0", "-1", "5.0001", "Infinity", "NaN", "lots"):
        with pytest.raises(ValueError):
            mcp.call("tinyfish", "run_web_automation", _agent_arguments(),
                     max_paid_usd=ceiling, runner=_never_called)


def test_no_billable_session_is_created_without_a_credential(clean_env, monkeypatch):
    """T3: the credential is checked *before* any provider session is opened."""
    import research_official_mcp as mcp

    monkeypatch.setenv("MARKETOPS_TINYFISH_AGENT_ALLOW_PAID", "1")
    monkeypatch.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", "allowed.example.org")
    called = []
    with pytest.raises(ValueError, match="OFFICIAL_MCP_CREDENTIAL_REQUIRED"):
        mcp.call("tinyfish", "run_web_automation", _agent_arguments(),
                 max_paid_usd="1", runner=_recorder(called))
    assert called == [], "a runner was reached without a configured credential"


def test_the_wallet_guard_fails_closed_without_a_provider_quote(clean_env, monkeypatch):
    """T3: "no hard USD guarantee unless backed by enforceable controls" — so an
    unreadable wallet quote must refuse rather than assume a safe balance."""
    import research_official_mcp as mcp

    for broken in (SimpleNamespace(structuredContent={"available_balance": "12"}),
                   SimpleNamespace(structuredContent={"available_balance": "12",
                                                      "rates": {"meters": []}}),
                   SimpleNamespace(structuredContent={"available_balance": "12",
                                                      "rates": {"meters": [
                                                          {"label": "Other", "unit_amount": "1"}]}}),
                   SimpleNamespace(structuredContent={"available_balance": "lots",
                                                      "rates": {"meters": [
                                                          {"label": "TinyFish Agent", "unit_amount": "1"}]}})):
        with pytest.raises(ValueError, match="TINYFISH_AGENT_WALLET_NOT_VERIFIED"):
            mcp._wallet_guard(broken, 5, "1")


def test_the_wallet_guard_enforces_the_ceiling_against_balance_and_steps(clean_env):
    """T3: the reservation must fit both the operator ceiling and the balance."""
    import research_official_mcp as mcp

    mcp._wallet_guard(_wallet_result("100", "0.10"), 5, "1")          # 0.50 <= 1
    with pytest.raises(ValueError, match="SPEND_CAP_EXCEEDED"):
        mcp._wallet_guard(_wallet_result("100", "0.30"), 5, "1")      # 1.50 > 1
    with pytest.raises(ValueError, match="SPEND_CAP_EXCEEDED"):
        mcp._wallet_guard(_wallet_result("0.50", "0.30"), 5, "100")   # over balance
    for bad in ("0", "-1", "Infinity", "NaN"):
        with pytest.raises(ValueError, match="SPEND_CAP_EXCEEDED"):
            mcp._wallet_guard(_wallet_result("100", bad), 5, "1")


# --- P1-04: the boundary the candidate does not yet hold -----------------


def test_a_process_environment_variable_is_the_entire_paid_authorization(clean_env, monkeypatch):
    """§P1-04: the paid-T3 gate must not be satisfiable by the Agent alone.

    Everything a host Agent running as the same OS user needs to dispatch a
    billable TinyFish Agent — the network switch, the ceiling, the paid opt-in
    and the vendor credential — is read from its own process environment. The
    contract requires a factor the Agent cannot set for itself (an OS-isolated
    broker, a separately authenticated grant, or an explicit declared
    trusted-operator mode that makes no isolation claim).
    """
    import research_official_mcp as mcp

    _paid_agent_env(monkeypatch)
    reached = []
    with pytest.raises(ValueError):
        # A conforming candidate refuses here without a non-environment factor.
        mcp.call("tinyfish", "run_web_automation", _agent_arguments(),
                 max_paid_usd="1", runner=_recorder(reached))
    assert reached == [], (
        "the same process that runs the host Agent authorized a billable T3 "
        "dispatch by setting its own environment (§P1-04)")


def test_the_trust_boundary_of_this_deployment_is_disclosed(clean_env):
    """§2 "Local human authorization": state the limitation explicitly.

    A same-user CLI confirmation is not cryptographic isolation. The shipped
    customer-facing documents must say so, so nobody reads "allow_paid" as a
    security boundary.
    """
    corpus = []
    for path in list(PLUGIN_DIR.rglob("*.md")) + list(PLUGIN_DIR.rglob("*.json")):
        if ".git" in path.parts:
            continue
        try:
            corpus.append(path.read_text(encoding="utf-8", errors="replace").casefold())
        except OSError:  # pragma: no cover - unreadable file is not a claim
            continue
    joined = "\n".join(corpus)
    markers = ("same os user", "same os identity", "shell access", "shell-capable",
               "not a security boundary", "no hard isolation", "trusted-operator mode",
               "不可作为安全边界")
    assert any(marker in joined for marker in markers), (
        "no shipped document discloses that a shell-capable Agent sharing the "
        "operator's OS identity can satisfy the paid/high-tier gates itself "
        "(§P1-04, §2 'Local human authorization')")


def test_tier_is_recorded_on_every_metered_operation(clean_env):
    """§3: registry entries carry an "effect class, tier, auth scope and cost meter"."""
    import research_provider_extensions as ext
    import research_provider_policy as policy

    assert "tier" in " ".join(dir(policy)).casefold() or hasattr(policy, "TIERS"), (
        "no tier vocabulary exists anywhere in the policy plane, so a capability "
        "cannot be gated by tier in code (owner brief §4 / §7 'by code rather "
        "than instructions alone')")
    assert any("tier" in name.casefold() for name in dir(ext)), (
        "the adapter surface exposes no tier field for a metered operation")


def test_paid_t3_and_metered_t2_do_not_share_one_flat_ceiling(clean_env, monkeypatch):
    """§4: "Separate per-operation, per-run, provider/account/period and project
    ceilings" — a T3 paid Agent must not consume, or be bounded by, the same
    single counter as a cheap T2 search."""
    import research_provider_extensions as ext

    monkeypatch.setenv("MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS", "3")
    assert hasattr(ext, "_tier_ceiling") or hasattr(ext, "_ceiling_for"), (
        "one flat MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS bounds every tier, so a "
        "cheap search and a paid Agent draw on the same allowance (§4 ceilings)")


def _never_called(provider, tool, data, *, max_paid_usd=""):  # pragma: no cover
    raise AssertionError("runner must not be reached")


def _recorder(sink):
    def runner(provider, tool, data, *, max_paid_usd=""):
        sink.append((provider, tool))

        async def _noop():
            return SimpleNamespace(structuredContent={"status": "queued"})

        return _noop()
    return runner

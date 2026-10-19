"""P1-01 contract: browser / HTTP network isolation, adversarial local matrix.

Acceptance source: ``MARKETOPS_ARCHITECTURE_CONVERGENCE_DECISION_AND_P0_P2_PLAN_2026-10-17.md``
Appendix D, "P1-01 Browser / HTTP network isolation" — the eight-item adversarial
acceptance matrix. The document lives on branch
``origin/docs/independent-technical-audit-20261016`` at
``docs/architecture/MARKETOPS_ARCHITECTURE_CONVERGENCE_DECISION_AND_P0_P2_PLAN_2026-10-17.md``;
it is not on this branch, so read it with::

    git show origin/docs/independent-technical-audit-20261016:\
docs/architecture/MARKETOPS_ARCHITECTURE_CONVERGENCE_DECISION_AND_P0_P2_PLAN_2026-10-17.md

Every test here is **offline and synthetic**. No real network request is issued:
name resolution is replaced by a fake ``socket.getaddrinfo``, connections are
refused by ``conftest.SocketSentinel``, and every credential mentioned is the
literal ``qa-gate-synthetic-not-a-credential``. The browser stack (crawl4ai /
Playwright) is deliberately absent from this QA venv, so any case that can only
be executed by really driving Chromium reports **BLOCKED** rather than passing.

Two facts about the candidate under test drive almost every red below. They were
read from the source, not assumed:

* the plugin-side entry is ``scripts/research_provider_extensions.py::_crawl4ai_local(url)``;
  it builds ``AsyncWebCrawler()`` with **no browser configuration and no proxy**,
  so nothing constrains where that browser dials;
* the only input guard is ``research_provider_extensions.py::_public_https_url``
  (and its twin ``research_official_mcp.py::_approved_url``), a pure
  string/hostname rule — scheme, userinfo, port, ``.local``/``.internal``/
  ``localhost``, bare IPs and an operator allowlist. **Neither guard performs a
  DNS resolution**, so an approved-looking hostname that resolves to
  ``127.0.0.1`` or ``169.254.169.254`` is accepted.

A test that encodes a contract the candidate does not meet is left **red** on
purpose, and its docstring names the contract (``§P1-01`` + matrix item number).
Loosening one of those assertions to make it green is the failure this file
exists to prevent.
"""
from __future__ import annotations

import inspect
import os
import socket
import sys
from pathlib import Path

import pytest

from conftest import PLUGIN_DIR, REPO_ROOT, SCRIPTS, SocketSentinel, blocked, module_available

# The plugin modules themselves import cleanly in this venv; a failure here is a
# real defect, not something to paper over with a skip.
import research_official_mcp as official_mcp  # noqa: E402
import research_provider_extensions as provider_ext  # noqa: E402

SYNTHETIC_SECRET = "qa-gate-synthetic-not-a-credential"
APPROVED_DOMAIN = "allowed.example"
APPROVED_URL = "https://www.allowed.example/page"

#: Guard implementations that stand between a caller and an outbound fetch. Both
#: are string-only today; the matrix requires each to also reject by resolved
#: address.
GUARDS = (
    pytest.param(provider_ext._public_https_url, "research_provider_extensions._public_https_url",
                 id="plugin-browser-guard"),
    pytest.param(official_mcp._approved_url, "research_official_mcp._approved_url",
                 id="official-mcp-guard"),
)

# --- core marketops (the reference implementation) -------------------------

_CORE_IMPORT_ERROR = ""
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))
try:
    from marketops.adapters.egress_proxy import EgressGate, EgressProxy, EgressVerdict
    from marketops.ports.dns import HostResolver
    _CORE_OK = True
except Exception as exc:  # noqa: BLE001
    _CORE_OK = False
    _CORE_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def _require_core():
    if not _CORE_OK:
        blocked(f"core marketops package is not importable from this worktree "
                f"({_CORE_IMPORT_ERROR}); the reference EgressGate cannot be exercised")


class _FakeResolver(HostResolver):
    """Answers a fixed sequence of address lists and counts every lookup."""

    def __init__(self, answers):
        self._answers = [tuple(a) for a in answers]
        self.calls = 0

    def resolve(self, host: str, port: int):
        self.calls += 1
        if not self._answers:
            raise AssertionError("resolver asked more times than the test provided answers")
        return self._answers.pop(0)


def _getaddrinfo_returning(address: str):
    """A drop-in ``socket.getaddrinfo`` that answers ``address`` for any name."""

    def fake(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 (address, port or 443))]

    return fake


def _script_sources() -> dict[str, str]:
    """Every plugin ``scripts/*.py`` source, by file name (read-only)."""
    return {p.name: p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(SCRIPTS.glob("*.py"))}


def _plugin_crawl_path_binds_egress() -> bool:
    """Does the guarded browser path accept and use a controlled-egress binding?

    The contract is not "a keyword exists" but "the call path can be pointed at
    a controlled egress", so both a parameter and its use in the body are
    required. Today ``_crawl4ai_local(url)`` has neither.
    """
    signature = inspect.signature(provider_ext._crawl4ai_local)
    if not (set(signature.parameters) & {"proxy", "egress", "browser_config", "egress_proxy"}):
        return False
    body = inspect.getsource(provider_ext._crawl4ai_local)
    return any(token in body for token in ("BrowserConfig", "proxy_config", "EgressProxy"))


def _invoke_plumbing(monkeypatch, tmp_path):
    """Neutralise only the run-plumbing so the guard itself stays the real one.

    ``invoke()`` opens with a data-root lookup and a Run-existence check. Neither
    is part of the network-isolation contract, so they are stubbed; every line
    that decides whether a target may be fetched is left untouched.
    """
    monkeypatch.setattr(provider_ext, "_root", lambda value: Path(tmp_path))
    monkeypatch.setattr(provider_ext, "_checked_run", lambda *a, **k: None)


# ======================================================================
# A. Must fail BEFORE a connection (matrix item 1)
# ======================================================================


def test_approved_hostname_resolving_to_loopback_is_refused_before_any_connection(
        monkeypatch, clean_env):
    """§P1-01 matrix item 1 — an approved public-looking host that resolves to
    loopback must fail *before a connection*, with an audit refusal.

    ``_public_https_url`` never resolves a name, so ``www.allowed.example``
    passes the allowlist and the URL is handed to the browser; the resolution
    that turns it into ``127.0.0.1`` happens later, inside Chromium, where no
    refusal is recorded. The red here is the missing refusal, not a missing
    import.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_returning("127.0.0.1"))

    with SocketSentinel() as sentinel:
        with pytest.raises(ValueError) as refusal:
            provider_ext._public_https_url(APPROVED_URL)

    assert "PRIVATE" in str(refusal.value) or "RESOLV" in str(refusal.value).upper(), (
        "§P1-01 item 1: an approved hostname resolving to 127.0.0.1 must be refused "
        f"with an audit refusal code; the guard answered {refusal.value!r}")
    assert sentinel.attempts == [], (
        f"§P1-01 item 1: refusal must precede any connection; attempts={sentinel.attempts!r}")


@pytest.mark.parametrize("address,kind", [
    ("127.0.0.1", "loopback IPv4"),
    ("10.0.0.5", "private IPv4"),
    ("172.16.9.9", "private IPv4 (172.16/12)"),
    ("192.168.1.10", "private IPv4"),
    ("169.254.169.254", "cloud metadata / link-local IPv4"),
    ("::1", "loopback IPv6"),
    ("fd00::1", "private IPv6 (ULA)"),
    ("fe80::1", "link-local IPv6"),
])
def test_approved_hostname_resolving_to_non_global_address_is_refused(
        monkeypatch, clean_env, address, kind):
    """§P1-01 matrix item 1 — every non-globally-routable resolution class.

    The matrix names loopback, private, link-local and metadata addresses
    explicitly, in both address families. All eight are answered by the fake
    resolver for a *single approved hostname*, so a rule that only inspects the
    string form of the target cannot pass this.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_returning(address))

    with pytest.raises(ValueError):
        provider_ext._public_https_url(APPROVED_URL)


def test_the_guard_that_may_connect_resolves_the_name_and_refuses_metadata(
        monkeypatch, clean_env):
    """§P1-01 matrix item 1 — the contract is about the path that *connects*.

    BEFORE (this test's earlier form) asserted the guard issued **no** lookup and
    failed while `lookups == 0`. That was the *hole* written down as a test: an
    approved name was accepted on string rules alone. Once the guard began
    resolving, the assertion inverted and reported the fix as a regression.

    The contract it now holds is the one the matrix always meant: on the path
    that can connect, an approved name whose records point at a non-global
    address is refused **before a connection**, and the refusal is attributable
    to the resolution rather than to the name.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)

    metadata = "169.254.169.254"

    def resolver_returning(address):
        def fake(host, port, *a, **k):
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                     (address, port or 443))]
        return fake

    # (a) the closed hole: an approved *name* resolving to cloud metadata.
    monkeypatch.setattr(socket, "getaddrinfo", resolver_returning(metadata))
    with pytest.raises(ValueError) as refused:
        provider_ext._public_https_url(APPROVED_URL)
    assert "OFFICIAL" not in str(refused.value)  # a refusal, not a transport error

    # (b) negative counter-proof: the guard is not simply refusing everything.
    # A public address for the same approved name must still be accepted, so (a)
    # is attributable to the resolved address and not to the name.
    monkeypatch.setattr(socket, "getaddrinfo", resolver_returning("93.184.216.34"))
    assert provider_ext._public_https_url(APPROVED_URL) == APPROVED_URL


def test_the_admission_path_does_not_claim_it_checked_the_address(monkeypatch, clean_env):
    """The two questions stay apart: "is this name allowed" and "where does it
    point". Admission is the string question and must not be read as the second.

    ``resolve=False`` is the admission form. Asserting that it issues **no**
    lookup is what keeps the two distinguishable: if admission silently began
    resolving, a later caller could not tell a name check from an address check.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    lookups: list[tuple] = []

    def recording(host, port, *a, **k):
        lookups.append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 ("169.254.169.254", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", recording)
    assert provider_ext._public_https_url(APPROVED_URL, resolve=False) == APPROVED_URL
    assert lookups == [], (
        "the admission path resolved the name, so a caller cannot tell a name "
        "check from an address check")


def test_the_connecting_path_refuses_when_no_controlled_egress_is_bound(clean_env):
    """§P1-01 item 8 — no governed egress means a named refusal, not a fallback."""
    import research_provider_extensions as ext

    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    with pytest.raises(ValueError, match="LOCAL_CRAWL_NOT_SAFE_TO_RUN"):
        ext._crawl4ai_local(APPROVED_URL, egress=None)


# ======================================================================
# B. Redirects to a private / metadata target (matrix item 2)
# ======================================================================


def test_redirect_hops_are_confined_by_the_guarded_crawl_path():
    """§P1-01 matrix item 2 — a redirect from an allowlisted page to a
    local/private/metadata target must not connect, in either scheme variant.

    A redirect target is chosen by the *remote* page, so no input guard applied
    to the submitted URL can cover it; only a controlled egress every hop must
    traverse can. The guarded path today constructs ``AsyncWebCrawler()`` with no
    configuration whatsoever, so nothing re-checks hop two.
    """
    assert _plugin_crawl_path_binds_egress(), (
        "§P1-01 item 2: the guarded browser path cannot be bound to a controlled "
        "egress, so a redirect to http(s)://169.254.169.254/ is followed with no "
        "decision recorded. Contract: every hop passes the same gate.")


def test_the_guarded_browser_fetch_exposes_a_controlled_egress_injection_point():
    """§P1-01 matrix item 2 (fallback form) — the call path must offer a
    parameter or an injection point that points the browser at the controlled
    egress (proxy / browser_config).

    Chromium cannot be driven in this QA venv (crawl4ai/Playwright absent), so
    the contract is asserted at its narrowest executable form: the entry point
    must be *bindable*. ``_crawl4ai_local`` accepts only ``url`` today.
    """
    params = set(inspect.signature(provider_ext._crawl4ai_local).parameters)
    assert params & {"proxy", "egress", "browser_config", "egress_proxy"}, (
        "§P1-01 item 2: `_crawl4ai_local` accepts "
        f"{sorted(params)!r} — no controlled-egress injection point exists, so a "
        "redirect cannot be re-checked and the egress cannot be pinned.")


def test_redirect_following_is_provable_only_with_a_real_browser():
    """§P1-01 matrix item 2 — the end-to-end form needs a live browser.

    The real case (a loopback decoy serving ``302 Location:`` at a private /
    metadata target, then asserting nothing connected to it) requires
    crawl4ai + Playwright. They are absent here, so this reports BLOCKED; the
    bindability half is asserted by the test above and stays red.
    """
    if not module_available("crawl4ai"):
        blocked("crawl4ai (Playwright browser stack) is not installed in this QA "
                "venv, so a redirect cannot be followed end to end; the bindability "
                "contract is asserted by "
                "test_the_guarded_browser_fetch_exposes_a_controlled_egress_injection_point")
    assert _plugin_crawl_path_binds_egress(), (
        "§P1-01 item 2: with crawl4ai present the guarded path must still bind a "
        "controlled egress before a redirect can be honoured")


# ======================================================================
# C. Subresources (matrix item 3)
# ======================================================================


def test_subresources_cannot_bypass_the_guarded_egress_path():
    """§P1-01 matrix item 3 — iframe, images, script, fetch/XHR, worker and
    websocket subresources must not bypass the guarded egress path.

    Subresources are requested by the page, not by the caller, so again only the
    browser's own egress setting can confine them. With no browser configuration
    in the guarded path there is no confinement to assert, and a page may load
    ``<script src="http://127.0.0.1:PORT/steal.js">`` directly.
    """
    assert _plugin_crawl_path_binds_egress(), (
        "§P1-01 item 3: the guarded browser path binds no egress, so subresource "
        "traffic (iframe/img/script/fetch/worker/ws) is unconstrained and can "
        "reach loopback or metadata directly.")


def test_subresource_confinement_is_provable_only_with_a_real_browser():
    """§P1-01 matrix item 3 — the end-to-end subresource case needs a browser."""
    if not module_available("crawl4ai"):
        blocked("crawl4ai (Playwright browser stack) is not installed in this QA "
                "venv; a page with an iframe/script subresource cannot be rendered "
                "to observe whether the subresource traversed the guarded egress")
    assert _plugin_crawl_path_binds_egress(), (
        "§P1-01 item 3: with crawl4ai present, subresources must still be confined "
        "to the guarded egress")


# ======================================================================
# D. DNS rebinding / TOCTOU (matrix item 4)
# ======================================================================


def test_core_egress_gate_pins_the_checked_address_and_resolves_once():
    """§P1-01 matrix item 4 — the reference implementation connects to the
    *checked* address and never re-resolves it.

    The gate is handed a resolver that would answer ``127.0.0.1`` on a second
    ask; the verdict must carry the first (public) address, must have consulted
    the resolver exactly once, and — read from the connection path — the proxy
    must dial ``decision.address`` rather than a name. This is the shape the
    plugin is required to reuse (item 9 of this file).
    """
    _require_core()
    resolver = _FakeResolver([["93.184.216.34"], ["127.0.0.1"]])
    gate = EgressGate(resolver=resolver)

    verdict = gate.check("www.example.com", 443)

    assert verdict.allowed and verdict.address == "93.184.216.34", (
        f"§P1-01 item 4: the verdict must be pinned to the checked address; got {verdict!r}")
    assert resolver.calls == 1, (
        f"§P1-01 item 4: the name must be resolved exactly once per request; "
        f"resolver was asked {resolver.calls} times")

    connect_source = inspect.getsource(EgressProxy._connect_upstream)
    assert "decision.address" in connect_source, (
        "§P1-01 item 4: the proxy must connect to the pinned `decision.address`, "
        "not to a hostname that would be resolved again (rebinding window)")


def test_core_egress_gate_refuses_a_name_that_resolves_to_a_private_address():
    """§P1-01 matrix item 4 — the reference gate refuses by resolved address."""
    _require_core()
    for address in ("127.0.0.1", "169.254.169.254"):
        verdict = EgressGate(resolver=_FakeResolver([[address]])).check("www.example.com", 443)
        assert not verdict.allowed and verdict.refusal, (
            f"§P1-01 item 4: a name resolving to {address} must be refused with a "
            f"reason code; got {verdict!r}")


def test_a_verdict_may_not_be_allowed_without_an_address():
    """§P1-01 matrix item 4 — "allowed" without an address is not a decision."""
    _require_core()
    with pytest.raises(ValueError):
        EgressVerdict(allowed=True)


def test_plugin_guard_pins_the_checked_address_against_dns_rebinding(monkeypatch, clean_env):
    """§P1-01 matrix item 4 — the plugin half: a name validated as public, then
    re-resolved to a private address before the connection, must not succeed.

    The plugin guard returns the raw URL string and pins nothing, so the
    "checked address" does not exist and there is no rebinding window to close.
    The assertion is that a decision bound to a resolved address comes back (or
    a refusal does); today neither does, and the URL is returned verbatim.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_returning("127.0.0.1"))

    try:
        decided = provider_ext._public_https_url(APPROVED_URL)
    except ValueError:
        decided = None

    assert decided is None, (
        "§P1-01 item 4: the guard returned the unmodified URL "
        f"{decided!r} after the approved name resolved to 127.0.0.1, pinning no "
        "address — a rebinding between validation and connection is unconstrained.")


# ======================================================================
# E. Loopback implicit bypass, proxy environment, alternate contexts (item 5)
# ======================================================================


@pytest.mark.parametrize("proxy_env", [
    {"HTTPS_PROXY": "http://127.0.0.1:9999", "ALL_PROXY": "http://127.0.0.1:9999"},
    {"HTTPS_PROXY": f"http://{SYNTHETIC_SECRET}@127.0.0.1:9999"},
    {"NO_PROXY": "*", "ALL_PROXY": "http://169.254.169.254:80"},
])
def test_proxy_environment_variables_cannot_whitelist_a_target(monkeypatch, clean_env, proxy_env):
    """§P1-01 matrix item 5 (environment half) — proxy environment variables must
    not create a direct route past the target guard.

    A hostile caller that sets ``HTTPS_PROXY`` / ``ALL_PROXY`` / ``NO_PROXY``
    must not thereby have a private or metadata literal accepted. This half is
    satisfied today: the string guard never consults the environment, so its
    refusals are proxy-independent. The browser-side half (how those variables
    affect Chromium) is a different test, below.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    for name, value in proxy_env.items():
        monkeypatch.setenv(name, value)

    for target in ("https://127.0.0.1/", "https://169.254.169.254/latest/meta-data/",
                   "https://metadata.google.internal/"):
        with pytest.raises(ValueError):
            provider_ext._public_https_url(target)


def test_plugin_neutralises_chromium_loopback_implicit_bypass_and_quic():
    """§P1-01 matrix item 5 — Chromium's implicit loopback proxy bypass, QUIC and
    alternate browser contexts must not create a direct route.

    Note the asymmetry with the environment half above: those are properties of
    the *browser launch*, so they can only be neutralised where the browser is
    launched. The guarded path launches ``AsyncWebCrawler()`` with no arguments,
    so none of them is addressed — there is no ``--proxy-bypass-list`` /
    ``--disable-quic`` / ``proxy_config`` anywhere in the plugin.
    """
    sources = _script_sources()
    joined = "\n".join(sources.values())
    missing = [token for token in ("proxy-bypass-list", "--disable-quic", "proxy_config")
               if token not in joined]
    assert not missing, (
        f"§P1-01 item 5: the plugin never sets {missing}, so Chromium's loopback "
        f"implicit bypass, the HTTP/3 (UDP) path and alternate browser contexts "
        f"are unaddressed — scanned {len(sources)} files under {SCRIPTS}.")


# ======================================================================
# F. Auxiliary Crawl4AI operations (matrix item 6)
# ======================================================================


@pytest.mark.parametrize("action", [
    "crawl4ai_local", "crawl4ai_cloud", "tinyfish_fetch",
    "tinyfish_mcp_fetch", "tinyfish_mcp_agent",
])
def test_every_acquisition_action_routes_the_submitted_url_through_the_guard(
        monkeypatch, tmp_path, clean_env, action):
    """§P1-01 matrix item 6 (allowlist half) — the same allowlist applies to every
    operation that takes a caller-supplied URL.

    ``invoke`` applies ``_public_https_url`` to the whole set before any
    reservation, so an unapproved host is refused with ``HOST_NOT_APPROVED``
    rather than reaching a provider. This half holds today.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    _invoke_plumbing(monkeypatch, tmp_path)

    with pytest.raises(ValueError) as refusal:
        provider_ext.invoke(action, run_id="qa-run", operation_id="qa-op",
                            url="https://not-approved.example/x")

    assert "HOST_NOT_APPROVED" in str(refusal.value), (
        f"§P1-01 item 6: {action} must refuse an unapproved host through the shared "
        f"guard; it answered {refusal.value!r}")


@pytest.mark.parametrize("guard,name", GUARDS)
def test_auxiliary_guard_enforces_dns_checks_not_only_string_rules(
        monkeypatch, clean_env, guard, name):
    """§P1-01 matrix item 6 (DNS half) — auxiliary operations must enforce the
    same allowlist **and DNS checks**.

    The allowlist is enforced by both plugin modules; the DNS check is enforced
    by neither, so the same metadata hole exists on the official-MCP side and on
    the browser side. Parametrised over both guards so a fix must land in both.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_returning("169.254.169.254"))

    with pytest.raises(ValueError):
        guard(APPROVED_URL)


def test_robots_sitemap_deep_crawl_and_css_extract_enforce_the_same_guard():
    """§P1-01 matrix item 6 — robots retrieval, sitemap URL seeding, deep crawling
    and CSS-extract retrieval must enforce the same allowlist, DNS checks and
    resource budgets.

    The plugin exposes no such auxiliary surface at all (no robots/sitemap/deep
    crawl/CSS-extract call into Crawl4AI), and the browser stack is absent, so
    this cannot be executed. It reports BLOCKED — a silent pass here would read
    as "the auxiliary paths are governed", which is not measured.
    """
    blocked("the plugin exposes no robots / sitemap-seeding / deep-crawl / "
            "CSS-extract Crawl4AI surface to exercise, and crawl4ai is not installed "
            "in this QA venv; there is no auxiliary path to prove governed or ungoverned")


# ======================================================================
# G / H. Positive case and evidence integrity (matrix item 7)
# ======================================================================


def test_allowed_public_https_host_remains_usable(monkeypatch, clean_env):
    """§P1-01 matrix item 7 (positive half) — a public allowed HTTPS host must
    remain functional; the guard must not become an over-blocker.

    An approved hostname whose resolution is a globally routable address is
    returned unchanged by both the browser guard and the official-MCP guard.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo_returning("93.184.216.34"))

    assert provider_ext._public_https_url(APPROVED_URL) == APPROVED_URL
    assert official_mcp._approved_url(APPROVED_URL) == APPROVED_URL


def test_a_refused_url_is_not_stored_as_validated_business_evidence(monkeypatch, tmp_path, clean_env):
    """§P1-01 matrix item 7 (integrity half) — the refusal result must not be
    stored as validated business evidence.

    A refusal must leave the artifact/candidate store untouched: no
    ``save_artifact`` and no ``put_candidate`` call may be reached. Driven through
    the real ``invoke`` with only the run-plumbing stubbed, so the guard on the
    path is the production one. Holds today, because the guard raises before the
    store is opened.
    """
    clean_env.setenv("MARKETOPS_FETCH_ALLOWED_DOMAINS", APPROVED_DOMAIN)
    _invoke_plumbing(monkeypatch, tmp_path)

    writes: list[tuple] = []
    monkeypatch.setattr(provider_ext.store, "save_artifact",
                        lambda *a, **k: writes.append(("save_artifact", a, tuple(k))) or {})
    monkeypatch.setattr(provider_ext.store, "put_candidate",
                        lambda *a, **k: writes.append(("put_candidate", a, tuple(k))) or {})

    with pytest.raises(ValueError):
        provider_ext.invoke("crawl4ai_local", run_id="qa-run", operation_id="qa-op",
                            url="https://not-approved.example/x")

    assert writes == [], (
        f"§P1-01 item 7: a refused target must not become stored evidence; the store "
        f"was written {writes!r}")


def test_a_private_resolving_approved_url_is_not_stored_as_evidence():
    """§P1-01 matrix item 7 (integrity half, adversarial form) — an approved name
    that resolves to metadata must not be stored as validated evidence either.

    Reaching the storage step requires a successful render, which requires
    crawl4ai; it is absent, so this reports BLOCKED. It must not be turned into a
    pass: the fact that today's guard accepts the URL (asserted red in section A)
    means the store *would* be reached on a machine with the browser installed.
    """
    blocked("reaching the storage step requires a rendered page (crawl4ai absent), "
            "so 'is the metadata-resolved page stored' cannot be observed here; the "
            "guard's acceptance of that URL is asserted red in section A instead")


# ======================================================================
# I. Static: the plugin does not revisit core egress control (matrix §P1-01)
# ======================================================================


def test_the_plugin_reuses_the_core_egress_control():
    """§P1-01 (Appendix D preamble + item 1..8) — "Reuse core egress boundary for
    Chromium and auxiliary HTTP".

    Core already ships the maintained control (``marketops/adapters/egress_proxy.py``:
    DNS-pinned local proxy, ``EgressGate``, decisions/refusals) and
    ``marketops/adapters/crawl4ai_fetcher.py`` already routes the browser to it.
    The plugin is required to reuse that single implementation rather than keep a
    string-only rule of its own. It imports none of it, so this stays red.
    """
    sources = _script_sources()
    joined = "\n".join(sources.values())
    evidence = {
        "egress_proxy": joined.count("egress_proxy"),
        "EgressProxy": joined.count("EgressProxy"),
        "marketops imports": sum(1 for line in joined.splitlines()
                                 if line.lstrip().startswith(("import marketops", "from marketops"))),
    }
    assert any(evidence.values()), (
        "§P1-01: the plugin does not reuse the core egress control — no reference to "
        f"`egress_proxy` / `EgressProxy` / `marketops` in any of the {len(sources)} files "
        f"under {SCRIPTS} ({evidence!r}), so its Chromium and auxiliary HTTP traffic "
        "bypasses the DNS-pinned boundary core already implements.")


# ======================================================================
# J. Dual-platform record and the named unsafe-mode refusal (matrix item 8)
# ======================================================================


def test_unsafe_local_crawl_reports_a_named_refusal_not_a_silent_fallback():
    """§P1-01 matrix item 8 — "Unavailable secure egress mode means
    ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``, **not** an ungoverned fallback".

    The guarded browser path must have a named refusal for the case where no
    secure egress mode is available. It has none: the plugin's crawl error codes
    cover access-blocked, challenge, empty extract, browser-not-installed and a
    generic runtime failure, but never "there is no safe way to run this".
    """
    sources = _script_sources()
    present = any("LOCAL_CRAWL_NOT_SAFE_TO_RUN" in text for text in sources.values())
    assert present, (
        "§P1-01 item 8: no `LOCAL_CRAWL_NOT_SAFE_TO_RUN` exists in any of the "
        f"{len(sources)} files under {SCRIPTS}; without a controlled egress the browser "
        "path has no named refusal and degrades to an ungoverned direct crawl.")


def test_dual_platform_smoke_results_are_recorded(monkeypatch, tmp_path):
    """§P1-01 matrix item 8 — "Linux + Windows smoke results must be recorded".

    A security boundary whose Windows behaviour is asserted nowhere is not a
    boundary. The search below is bounded and reported: files whose name suggests
    a smoke record, under the plugin tree and the repository's ``tests/`` and
    ``docs/`` trees, mentioning both platforms. Nothing is found, so this is red.
    """
    roots = [PLUGIN_DIR]
    for candidate in (REPO_ROOT / "tests", REPO_ROOT / "docs"):
        if candidate.is_dir():
            roots.append(candidate)

    named_smoke: list[Path] = []
    dual_platform: list[Path] = []
    for root in roots:
        for path in root.rglob("*"):
            if not path.is_file() or "smoke" not in path.name.lower():
                continue
            named_smoke.append(path)
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            if "windows" in text and "linux" in text:
                dual_platform.append(path)

    assert dual_platform, (
        "§P1-01 item 8: no smoke record covering BOTH Linux and Windows exists for "
        f"the egress control. Searched {len(roots)} trees ({[str(r) for r in roots]}) "
        f"and found {len(named_smoke)} smoke-named files: "
        f"{[str(p) for p in named_smoke] or 'none'}. A security boundary measured on "
        "one platform records half its behaviour.")


# ======================================================================
# Environment guard: this file must never reach for a real credential or network
# ======================================================================


def test_the_suite_uses_no_real_credential(clean_env):
    """Every credential used under ``qa_gate/`` is synthetic.

    A convenience guard for the whole file: the synthetics are literals, and no
    provider key may be inherited from the operator environment by any test that
    ends up touching provider code.
    """
    assert SYNTHETIC_SECRET.startswith("qa-gate-synthetic")
    for name in ("CRAWL4AI_API_KEY", "TINYFISH_API_KEY", "TYPESAFE_API_KEY",
                 "SERPER_API_KEY", "SERPAPI_API_KEY"):
        assert os.environ.get(name, "") == "", (
            f"{name} leaked into the test environment; synthetic-only is the rule")

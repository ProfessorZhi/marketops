"""RC2 integration seams: F's caps and G's browser guards at every reachable entry.

Lane F and Lane G each hardened one module.  The RC2 integrator owns the
*connections*, and these tests exist because a hardened module with an
unhardened caller is a green suite over a live bypass:

* ``research_provider_extensions._crawl4ai_local`` handed the egress kwargs
  straight to ``BrowserConfig``.  It is reachable through the provider invoke
  path, so without this seam the WebRTC/DNS flags applied only to the adapter
  path that the tests happened to drive.
* ``dual_search_mcp._state_caps`` and ``research_wiring`` re-stated only a
  request count, which replaced the whole cap row and dropped a consented USD
  ceiling (``set_cap`` is a row replace, not a merge).

Everything here is synthetic and offline: a fake ``crawl4ai`` module, a fake
egress binding and temporary SQLite files.  No browser is launched, no provider
is called and no price is invented.
"""
from __future__ import annotations

import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
sys.path.insert(0, str(SCRIPTS))

import dual_search_mcp  # noqa: E402
import research_provider_extensions as extensions  # noqa: E402
import research_runtime_setup as runtime  # noqa: E402
import research_runtime_status as runtime_status  # noqa: E402
import research_usage_ledger as usage  # noqa: E402
import research_wiring as wiring  # noqa: E402
from research_adapter_crawl4ai import (  # noqa: E402
    BROWSER_SECURITY_ARGS,
    DNS_REBINDING_GUARD,
    WEBRTC_NON_PROXIED_UDP_GUARD,
)


# ---------------------------------------------------------------------------
# A fake crawl4ai SDK.  It records what the production code would have passed
# to the library, which is exactly the decision this seam owns.
# ---------------------------------------------------------------------------
class _FakeMarkdown:
    raw_markdown = "<p>Example Sports Ground</p>"


class _FakeResult:
    success = True
    status_code = 200
    response_headers = {}
    markdown = _FakeMarkdown()
    html = "<html><body><p>Example Sports Ground</p></body></html>"

    def __init__(self, url):
        self.url = url


class _FakeBrowserConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeAsyncWebCrawler:
    constructed = 0

    def __init__(self, config=None, **kwargs):
        type(self).constructed += 1
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def arun(self, url, **kwargs):
        return _FakeResult(url)


def _fake_crawl4ai():
    module = types.ModuleType("crawl4ai")
    module.AsyncWebCrawler = _FakeAsyncWebCrawler
    module.BrowserConfig = _FakeBrowserConfig
    return module


class _Egress:
    """The two attributes the local crawl path reads."""

    def __init__(self, *, server="http://127.0.0.1:8899", extra_args=()):
        self._server = server
        self._extra_args = list(extra_args)

    def browser_config_kwargs(self):
        return {"proxy_config": {"server": self._server},
                "extra_args": list(self._extra_args)}


class LocalCrawlEntryTests(unittest.TestCase):
    def setUp(self):
        _FakeAsyncWebCrawler.constructed = 0
        self.module = _fake_crawl4ai()
        self.addCleanup(lambda: sys.modules.pop("crawl4ai", None))

    def _run(self, url, egress):
        with mock.patch.dict(sys.modules, {"crawl4ai": self.module}):
            return extensions._crawl4ai_local(url, egress=egress)

    def test_local_entry_applies_the_same_webrtc_and_dns_guards_as_the_adapter(self):
        result = self._run("https://example.org/venue", _Egress())

        self.assertEqual("<p>Example Sports Ground</p>", result["markdown"])
        self.assertEqual(1, _FakeAsyncWebCrawler.constructed)
        # Assert the exact kwargs the seam handed to ``BrowserConfig``.
        self.assertIn(WEBRTC_NON_PROXIED_UDP_GUARD, _LAST_BROWSER_KWARGS["extra_args"])
        self.assertIn(DNS_REBINDING_GUARD, _LAST_BROWSER_KWARGS["extra_args"])
        self.assertEqual("http://127.0.0.1:8899",
                         _LAST_BROWSER_KWARGS["proxy_config"]["server"])
        self.assertTrue(_LAST_BROWSER_KWARGS["headless"])
        for mandatory in BROWSER_SECURITY_ARGS:
            self.assertIn(mandatory, _LAST_BROWSER_KWARGS["extra_args"])

    def test_a_downgraded_egress_policy_is_refused_before_the_browser_exists(self):
        egress = _Egress(extra_args=["--webrtc-ip-handling-policy=default"])

        with self.assertRaises(ValueError) as ctx:
            self._run("https://example.org/venue", egress)

        self.assertTrue(str(ctx.exception).startswith("LOCAL_CRAWL_NOT_SAFE_TO_RUN"))
        self.assertEqual(0, _FakeAsyncWebCrawler.constructed,
                         "no browser may be constructed when the launch policy is downgraded")

    def test_a_missing_proxy_is_refused_before_the_browser_exists(self):
        with self.assertRaises(ValueError) as ctx:
            self._run("https://example.org/venue", _Egress(server=""))

        self.assertTrue(str(ctx.exception).startswith("LOCAL_CRAWL_NOT_SAFE_TO_RUN"))
        self.assertEqual(0, _FakeAsyncWebCrawler.constructed)

    def test_a_non_http_url_is_refused_before_the_browser_exists(self):
        with self.assertRaises(ValueError) as ctx:
            self._run("file:///etc/passwd", _Egress())

        self.assertEqual("EGRESS_REFUSED", str(ctx.exception))
        self.assertEqual(0, _FakeAsyncWebCrawler.constructed)

    def test_no_egress_still_refuses_rather_than_falling_back_to_direct(self):
        with self.assertRaises(ValueError) as ctx:
            self._run("https://example.org/venue", None)
        self.assertTrue(str(ctx.exception).startswith("LOCAL_CRAWL_NOT_SAFE_TO_RUN"))


# ``_FakeBrowserConfig`` has no class-level record; capture the last kwargs in a
# module-level dict so the assertions above can read what the seam decided.
_LAST_BROWSER_KWARGS: dict = {}


def _record_browser_config(self, **kwargs):
    _LAST_BROWSER_KWARGS.clear()
    _LAST_BROWSER_KWARGS.update(kwargs)


_FakeBrowserConfig.__init__ = _record_browser_config


# ---------------------------------------------------------------------------
# F seam: the REST entry and the MCP managed entry share one consent.
# ---------------------------------------------------------------------------
class CapWiringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rc2-caps-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.ledger_path = self.root / "state" / "usage-ledger.sqlite3"
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        dual_search_mcp._CAPS_STATED.clear()
        extensions._CAPS_STATED.clear()

    def _configure(self, **overrides):
        values = dict(providers=["serper"], domains=["example.org"], max_calls=10,
                      task="RC2 synthetic seam consent", confirm=True,
                      run_max_calls=2, project_max_calls=5,
                      run_max_usd_micros=100_000,
                      project_max_usd_micros=1_000_000)
        values.update(overrides)
        return runtime.configure(self.root, **values)

    def _ledger(self):
        return usage.UsageLedger(self.ledger_path,
                                 pricing=usage.documented_pricing())

    def test_rest_entry_states_the_independent_caps_and_keeps_the_usd_promise(self):
        self._configure()
        ledger = self._ledger()

        # The adapter's own environment numbers (99/99) must not override the
        # local consent record; the consent owns both bounds.
        dual_search_mcp._state_caps(ledger, "proj", "run-a", 99, 99,
                                    root=self.root)

        run = ledger.remaining("run", "proj/run-a")
        project = ledger.remaining("project", "proj")
        self.assertEqual(2, run.ceiling_calls)
        self.assertEqual(5, project.ceiling_calls)
        self.assertTrue(run.usd_enforced)
        self.assertTrue(project.usd_enforced)
        self.assertEqual(100_000, run.ceiling_micros)
        self.assertEqual(1_000_000, project.ceiling_micros)

    def test_unknown_price_under_a_consented_usd_cap_fails_closed_at_the_rest_entry(self):
        self._configure()
        ledger = self._ledger()
        dual_search_mcp._state_caps(ledger, "proj", "run-a", 99, 99,
                                    root=self.root)

        with self.assertRaises(usage.LedgerRefused) as ctx:
            ledger.reserve(usage.OperationRequest(
                project="proj", run_id="run-a", operation_id="run-a:op-1",
                provider="serper", capability="search", account_ref="serper",
                transport="rest", intent="synthetic", args={"query": "seam"}))

        self.assertEqual("PRICE_UNKNOWN", ctx.exception.code)

    def test_managed_mcp_entry_reuses_the_same_consent_and_preserves_money(self):
        self._configure()
        ledger = self._ledger()
        project = usage.project_for_root(self.root)

        proxy = wiring._GatewayAfterTheGate(root=self.root)
        proxy._built = SimpleNamespace(
            ledger=ledger,
            invoke=lambda **kwargs: "managed-result")

        result = proxy.invoke(capability_id="serper.search", params={},
                              run_id="run-m", operation_id="op-1",
                              account_ref="serper",
                              intent_fingerprint="synthetic")

        self.assertEqual("managed-result", result)
        run = ledger.remaining("run", f"{project}/run-m")
        project_cap = ledger.remaining("project", project)
        self.assertEqual(2, run.ceiling_calls)
        self.assertEqual(5, project_cap.ceiling_calls)
        self.assertTrue(run.usd_enforced)
        self.assertTrue(project_cap.usd_enforced)
        self.assertEqual(100_000, run.ceiling_micros)
        self.assertEqual(1_000_000, project_cap.ceiling_micros)
        # The provider and single-operation bounds are still stated, and stating
        # them did not zero the money columns.
        self.assertEqual(10, ledger.remaining(
            "provider_account", f"{project}/serper/serper").ceiling_calls)
        self.assertEqual(1, ledger.remaining(
            "operation", f"{project}/*").ceiling_calls)
        # Stating the provider/operation call bounds did not invent a money cap
        # on those rows: they were never given one.
        self.assertIsNone(ledger.remaining(
            "provider_account", f"{project}/serper/serper").ceiling_micros)

    def test_legacy_call_only_entry_preserves_an_existing_usd_cap(self):
        ledger = self._ledger()
        ledger.set_cap("run", "proj/run-legacy", ceiling_calls=9,
                       ceiling_micros=250_000, usd_enforced=True,
                       source="synthetic-consent")
        ledger.set_cap("project", "proj", ceiling_calls=9,
                       ceiling_micros=2_500_000, usd_enforced=True,
                       source="synthetic-consent")

        # No runtime consent record: the legacy environment path runs, and it
        # must merge rather than replace.
        dual_search_mcp._state_caps(ledger, "proj", "run-legacy", 4, 6,
                                    root=self.root)

        run = ledger.remaining("run", "proj/run-legacy")
        project = ledger.remaining("project", "proj")
        self.assertEqual(4, run.ceiling_calls)
        self.assertEqual(6, project.ceiling_calls)
        self.assertTrue(run.usd_enforced)
        self.assertTrue(project.usd_enforced)
        self.assertEqual(250_000, run.ceiling_micros)
        self.assertEqual(2_500_000, project.ceiling_micros)

    def test_runtime_status_matrix_reports_the_enforced_mode(self):
        self._configure()
        allowed = {"allowed": True, "reason_code": "", "detail": "REQUEST_COUNT_ONLY"}
        with mock.patch.object(runtime, "admission", return_value=allowed), \
                mock.patch("research_usage_ledger._pricing_from_environment",
                           return_value=usage.PricingCatalog(())):
            view = runtime_status.view(self.root)

        self.assertFalse(view["runtime"]["dollar_limit_enforced"])
        self.assertEqual("FAIL_CLOSED_PRICING_SOURCE_MISSING",
                         view["runtime"]["dollar_limit_enforcement"])
        for row in view["capability_matrix"].values():
            self.assertFalse(row["dollar_limit_enforced"])
            self.assertEqual("REQUEST_COUNT_AND_USD_CAP", row["budget_mode"])
            self.assertEqual(2, row["run_max_calls"])
            self.assertEqual(5, row["project_max_calls"])


class CoreFetcherReachabilityTests(unittest.TestCase):
    """The core fetcher is *not* covered by G; the shipped plugin must not reach it.

    ``marketops/adapters/crawl4ai_fetcher.py::_browser_config`` carries only
    ``--proxy-bypass-list=<-loopback>`` and ``--disable-quic``.  It has no
    ``--webrtc-ip-handling-policy`` and no ``--host-resolver-rules``, so a plugin
    entry that imported it would launch a browser without G's guards while every
    test in this file stayed green.  The release bundle copies ``scripts/`` only
    (``build_client_bundles.build``), so the core module is outside the artefact;
    this test keeps it there by refusing an import edge.
    """

    IMPORT_RE = re.compile(
        r"(?m)^\s*(?:from|import)\s+marketops\.adapters(?:\.|\s|$)")

    def test_plugin_scripts_never_import_the_unhardened_core_fetcher(self):
        offenders = []
        for path in sorted((PLUGIN / "scripts").rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if self.IMPORT_RE.search(text):
                offenders.append(path.relative_to(PLUGIN).as_posix())
        self.assertEqual([], offenders,
                         "a release-plugin import of the core adapter would bypass G's "
                         "WebRTC/DNS guards; the plugin is not allowed that edge")

    def test_the_vendored_slice_is_the_plugins_only_egress_implementation(self):
        # ``marketops_safe_egress`` is a byte-pinned copy with its own
        # PROVENANCE.json; importing a *different* copy from the core tree is the
        # drift this assertion forbids.
        text = "\n".join(path.read_text(encoding="utf-8", errors="replace")
                         for path in sorted((PLUGIN / "scripts").rglob("*.py")))
        self.assertIn("marketops_safe_egress", text)
        self.assertNotIn("from marketops.adapters", text)


if __name__ == "__main__":
    unittest.main()

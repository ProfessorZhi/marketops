"""Field fixes from a Windows WorkBuddy install and the Paris continuation.

* a stale machine proxy must not break searches, the update or the install;
* a headless shell that hangs on this machine is replaced by a launch mode that works,
  and every crawl in the process uses it;
* a poor method moves the Run to the next method instead of ending the research.
"""
import os
import sys
import tempfile
import types
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_browser_launch as launch  # noqa: E402
import research_methods as methods  # noqa: E402


class AmbientProxy(unittest.TestCase):
    def proxies(self, handler):
        return getattr(handler, "proxies", None)

    def test_marketops_requests_ignore_the_machine_proxy(self):
        import dual_search_mcp
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY":
                                     "http://127.0.0.1:9"}):
            os.environ.pop("MARKETOPS_EGRESS_UPSTREAM_PROXY", None)
            self.assertEqual({}, self.proxies(dual_search_mcp.proxy_handler()))

    def test_only_the_configured_proxy_is_used(self):
        import dual_search_mcp
        with patch.dict(os.environ, {"MARKETOPS_EGRESS_UPSTREAM_PROXY": "http://proxy.corp:8080"}):
            self.assertEqual("http://proxy.corp:8080",
                             self.proxies(dual_search_mcp.proxy_handler())["https"])

    def test_serper_adapter_has_no_ambient_proxy(self):
        import research_adapter_serper
        # An empty ProxyHandler registers nothing and keeps urllib's default
        # (environment/registry) ProxyHandler out of the opener.
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9"}):
            opener = research_adapter_serper.SerperAdapter()._opener
        self.assertFalse([h for h in opener.handlers
                          if isinstance(h, urllib.request.ProxyHandler) and h.proxies])

    def test_jev_uses_its_own_client_without_the_machine_proxy(self):
        import research_adapter_jev as jev
        seen = {}

        class Client:
            def __init__(self, **options):
                seen.update(options)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def system_one(self, state, questions):
                return "answer"

        fake = types.SimpleNamespace(TypeSafeClient=Client,
                                     RetryPolicy=lambda **kw: kw)
        me = types.SimpleNamespace(_api_key="k", _base_url=None, _model=None, _timeout=30)
        with patch.object(jev, "_sdk_module", return_value=fake), \
                patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9"}):
            self.assertEqual("answer", jev.JevAdapter._call(me, {}, []))
        self.assertFalse(seen["http_client"]._trust_env)

    def test_the_installer_downloads_direct_only_when_the_proxy_is_broken(self):
        import setup_clients
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9"}):
            with patch.object(setup_clients, "_reachable",
                              side_effect=lambda url, direct: direct):
                env, note = setup_clients._download_env()
            self.assertNotIn("HTTPS_PROXY", env)
            self.assertEqual("*", env["NO_PROXY"])
            self.assertTrue(note)
            with patch.object(setup_clients, "_reachable", return_value=True):
                env, note = setup_clients._download_env()
            self.assertEqual("http://127.0.0.1:9", env["HTTPS_PROXY"])
            self.assertEqual("", note)

    def test_the_updater_retries_direct(self):
        import update_marketops
        calls = []

        class Direct:
            def open(self, request, timeout):
                calls.append("direct")
                return _Response(b"[]")

        with patch.object(urllib.request, "urlopen", side_effect=OSError("502")), \
                patch.object(urllib.request, "build_opener", return_value=Direct()):
            self.assertEqual(b"[]", update_marketops._get("https://api.github.com/x"))
        self.assertEqual(["direct"], calls)


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class LaunchModes(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        os.environ.pop(launch.OVERRIDE_ENV, None)

    def fake_probe(self, working):
        def probe(mode, **kwargs):
            ok = mode in working
            return {"mode": mode, "ok": ok, "external_ok": ok, "detail": "" if ok else "hung",
                    "seconds": 1.0}
        return probe

    def test_a_hanging_headless_shell_is_replaced_and_remembered(self):
        with patch.object(launch, "probe", side_effect=self.fake_probe({"chromium", "msedge"})):
            chosen = launch.choose(root=self.root)
        self.assertEqual("chromium", chosen["mode"])
        self.assertEqual(["default", "chromium"], [t["mode"] for t in chosen["tried"]])
        self.assertEqual("chromium", launch.configured_mode(self.root))

    def test_the_remembered_mode_is_tried_first(self):
        with patch.object(launch, "probe", side_effect=self.fake_probe({"msedge"})):
            launch.choose(root=self.root)
        with patch.object(launch, "probe", side_effect=self.fake_probe({"msedge", "chromium"})) as p:
            launch.choose(root=self.root)
        self.assertEqual("msedge", p.call_args_list[0].args[0])

    def test_nothing_works_is_said_with_each_cause(self):
        with patch.object(launch, "probe", side_effect=self.fake_probe(set())):
            chosen = launch.choose(root=self.root)
        self.assertEqual("", chosen["mode"])
        self.assertEqual(list(launch.MODES), [t["mode"] for t in chosen["tried"]])
        self.assertEqual("default", launch.configured_mode(self.root))

    def test_every_launch_without_a_channel_gets_the_chosen_one(self):
        seen = []

        class BrowserType:
            def launch(self, **kwargs):
                seen.append(kwargs.get("channel"))

            def launch_persistent_context(self, *args, **kwargs):
                seen.append(kwargs.get("channel"))

        sync_mod = types.ModuleType("playwright.sync_api")
        async_mod = types.ModuleType("playwright.async_api")
        sync_mod.BrowserType = BrowserType

        class AsyncBrowserType(BrowserType):
            pass

        async_mod.BrowserType = AsyncBrowserType
        modules = {"playwright": types.ModuleType("playwright"),
                   "playwright.sync_api": sync_mod, "playwright.async_api": async_mod}
        with patch.dict(sys.modules, modules), \
                patch.dict(os.environ, {launch.OVERRIDE_ENV: "chromium"}):
            self.assertEqual("chromium", launch.install_launch_default())
            BrowserType().launch(headless=True)
            BrowserType().launch(headless=True, channel="chrome")
            AsyncBrowserType().launch_persistent_context("dir", headless=True)
        self.assertEqual(["chromium", "chrome", "chromium"], seen)

    def test_the_doctor_switches_mode_instead_of_failing(self):
        import research_crawl_doctor as doctor
        with patch.object(launch, "probe", side_effect=self.fake_probe({"chromium"})), \
                patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(self.root)}), \
                patch.object(launch, "install_launch_default", return_value="chromium"):
            ok, detail = doctor._launch_probe()
        self.assertTrue(ok)
        self.assertIn("chromium", detail)


class MethodLadder(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_poor_method_hands_over_to_the_next(self):
        self.assertEqual("OWN_SITE", methods.status(self.root, "r1")["contacts"]["next_method"])
        after = methods.record(self.root, "r1", "OWN_SITE", "exhausted", "30 targets, 0 named")
        self.assertEqual("OPERATOR", after["contacts"]["next_method"])
        self.assertIn("operator", after["contacts"]["next_method_how"].lower())
        self.assertEqual("OWN_SITE", methods.status(self.root, "r2")["contacts"]["next_method"])

    def test_the_ladder_ends_only_when_every_method_is_done(self):
        for name, _ in methods.LADDERS["contacts"]:
            status = methods.record(self.root, "r1", name, "exhausted")
        self.assertTrue(status["contacts"]["all_exhausted"])
        self.assertFalse(status["inventory"]["all_exhausted"])

    def test_unknown_values_are_refused(self):
        with self.assertRaisesRegex(ValueError, "METHOD_UNKNOWN"):
            methods.record(self.root, "r1", "GUESS_EMAILS", "exhausted")
        with self.assertRaisesRegex(ValueError, "METHOD_OUTCOME_INVALID"):
            methods.record(self.root, "r1", "OWN_SITE", "done")

    def test_unfinished_research_carries_the_turn_rule_and_next_method(self):
        import research_execution
        methods.record(self.root, "r1", "OWN_SITE", "exhausted")
        state = research_execution.assess(self.root, "r1", goal={"goal_satisfied": False})
        self.assertTrue(state["continuation_required"])
        self.assertEqual("OPERATOR", state["next_method"])
        self.assertIn("Do not end your turn", state["turn_rule"])

    def test_all_methods_exhausted_is_a_real_stop(self):
        import research_execution
        for name, _ in methods.LADDERS["contacts"]:
            methods.record(self.root, "r1", name, "exhausted")
        state = research_execution.assess(self.root, "r1", goal={"goal_satisfied": False})
        self.assertEqual("FINAL_METHODS_EXHAUSTED", state["state"])
        self.assertFalse(state["continuation_required"])


if __name__ == "__main__":
    unittest.main()

"""The target gate under test: deterministic, offline, and actually refusing.

Two claims are asserted here rather than assumed:

* **no real DNS happens.** A tripwire replaces `socket.getaddrinfo` and fails
  loudly if anything asks it. "Offline" is checked, not hoped for.
* **an inward resolution is refused at the same gate.** A refusal on resolution
  must reach the adapter as `EGRESS_REFUSED` with `NO_CHARGE_PROVEN` and zero
  dispatches -- it must not degrade into a transport failure or an exposure.

The production rule is W-A's; this file does not reimplement it. Where the guard
exposes W-A's resolver seam the **real** guard runs with a fake answer.
"""
from __future__ import annotations

import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish
import _tinyfish_egress_fixture as egress_fixture


class EgressGateOffline(unittest.TestCase):
    def setUp(self):
        self.calls = []
        env = patch.dict(os.environ, {
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": egress_fixture.ALLOWED_HOST,
            "MARKETOPS_RESEARCH_ALLOW_NETWORK": "1",
            "TINYFISH_API_KEY": "synthetic-tinyfish-key-not-for-network",
            "TINYFISH_MCP_ACCESS_TOKEN": "",
            "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0"})
        env.start()
        self.addCleanup(env.stop)

    def runner(self):
        async def run(tool, arguments):
            self.calls.append((tool, arguments))
            return {"results": []}
        return run

    def request(self, capability_id, params):
        class _Req:
            pass
        req = _Req()
        req.capability_id = capability_id
        req.params = params
        req.run_id = "run-1"
        req.operation_id = "op-1"
        req.account_ref = "a"
        req.intent_fingerprint = "i"
        req.grant_ref = None
        return req

    def test_the_tripwire_fires_if_the_system_resolver_is_asked(self):
        egress_fixture.no_real_dns(self)
        with self.assertRaises(AssertionError):
            socket.getaddrinfo("public.example.org", 443)

    def test_a_synthetic_public_answer_passes_the_production_shape_rule(self):
        probe = egress_fixture.EgressFixture()
        if probe.mode == "unavailable":
            self.skipTest("no production guard on this checkout; the shape "
                          "rule under test is W-A's and is not present here")
        fixture = egress_fixture.install(self)
        self.assertEqual("https://public.example.org/a",
                         tinyfish.approved_url("https://public.example.org/a",
                                               resolve=True))
        self.assertIn(fixture.mode, {"resolver", "shape_only", "opaque"})
        self.assertFalse(fixture.double_used)

    def test_an_inward_resolution_is_refused_with_no_charge_proven(self):
        # Deterministic on every tree: a guard that refuses on resolution. What
        # is under test is the adapter's classification, which is C's job -- the
        # resolution rule itself is W-A's.
        def refusing(value, *, resolve=False, resolver=None):
            raise ValueError("TARGET_RESOLVES_NON_GLOBAL: loopback IPv4")

        with patch.object(tinyfish, "_url_guard", lambda: refusing):
            with self.assertRaisesRegex(ValueError, "TARGET_RESOLVES_NON_GLOBAL"):
                tinyfish.call("fetch_content",
                              {"urls": ["https://public.example.org/a"]},
                              runner=self.runner())
            result = tinyfish.execute(
                self.request("tinyfish.fetch_content",
                             {"urls": ["https://public.example.org/a"]}),
                runner=self.runner())
        self.assertEqual("BLOCKED", result["outcome"])
        self.assertEqual("EGRESS_REFUSED", result["reason_code"])
        self.assertEqual("NO_CHARGE_PROVEN", result["charge_state"])
        self.assertFalse(result["dispatched"])
        self.assertEqual([], self.calls)

    def test_the_guard_under_test_is_the_one_the_egress_proxy_uses(self):
        probe = egress_fixture.EgressFixture()
        if probe.mode != "resolver":
            self.skipTest("no production guard on this checkout (%s); the "
                          "integrated tree runs this and asserts the module"
                          % probe.mode)
        self.assertEqual("research_egress", probe.real_guard.__module__)
        self.assertTrue(probe.resolution_exercised)

    def test_the_real_guard_refuses_a_loopback_answer_when_it_can_resolve(self):
        # Runs where W-A's guard exposes its resolver seam (the integrated
        # tree). On a checkout without that seam there is nothing to drive, and
        # the case says so rather than passing silently.
        probe = egress_fixture.EgressFixture()
        if probe.mode != "resolver":
            self.skipTest("active guard exposes no resolver seam (%s); the "
                          "refusal path is covered by the guard double above "
                          "and by W-A's own suite" % probe.mode)
        fixture = egress_fixture.install(
            self, resolver=egress_fixture.FakeResolver(
                egress_fixture.SYNTHETIC_LOOPBACK))
        with self.assertRaises(ValueError) as err:
            tinyfish.call("fetch_content",
                          {"urls": ["https://public.example.org/a"]},
                          runner=self.runner())
        self.assertIn("TARGET_RESOLVES_NON_GLOBAL", str(err.exception))
        self.assertEqual([], self.calls)
        self.assertTrue(fixture.resolution_exercised)


if __name__ == "__main__":
    unittest.main()

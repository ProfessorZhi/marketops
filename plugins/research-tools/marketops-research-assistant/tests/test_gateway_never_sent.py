"""RC6: a request that never left this machine cannot leave an unknown charge.

One proxy-refused CONNECT used to become UNKNOWN_CHARGE_OUTSTANDING and hold
every later paid call (the 14 UNKNOWN stop). Pre-send failures are refusals;
timeouts and anything after the connection is up stay unknown.
"""
from __future__ import annotations

import socket
import ssl
import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import research_adapter_serper as serper  # noqa: E402
import research_contract as contract  # noqa: E402
from test_search_serp_adapter_contract import Request  # noqa: E402


def raising(exc):
    def transport(_request, _timeout):
        raise exc
    return transport


class NeverSentTests(unittest.TestCase):
    def test_pre_send_failures_are_recognised(self):
        for exc in (socket.gaierror(-2, "Name or service not known"),
                    ConnectionRefusedError(111, "refused"),
                    OSError("Tunnel connection failed: 403 Forbidden"),
                    urllib.error.URLError(socket.gaierror(-3, "temporary failure")),
                    ssl.SSLCertVerificationError("bad cert"),
                    ExceptionGroup("task group", [type("ProxyError", (Exception,), {})("403")])):
            with self.subTest(exc=type(exc).__name__):
                self.assertTrue(contract.never_sent(exc))

    def test_post_send_failures_stay_ambiguous(self):
        for exc in (TimeoutError("timed out"), ConnectionResetError(104, "reset"),
                    urllib.error.URLError(TimeoutError("timed out")), ValueError("bad body")):
            with self.subTest(exc=type(exc).__name__):
                self.assertFalse(contract.never_sent(exc))

    def test_serper_dns_failure_is_blocked_not_unknown(self):
        adapter = serper.SerperAdapter(api_key="k", transport=raising(
            urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))))
        result = adapter.execute(Request("serper.search", {"query": "halls", "country": "GB",
                                                           "language": "en"}))
        self.assertEqual("BLOCKED", result.outcome)
        self.assertIn("EGRESS_REFUSED", result.caveats)

    def test_serper_reset_after_connect_stays_unknown(self):
        adapter = serper.SerperAdapter(api_key="k", transport=raising(
            urllib.error.URLError(ConnectionResetError(104, "reset"))))
        result = adapter.execute(Request("serper.search", {"query": "halls", "country": "GB",
                                                           "language": "en"}))
        self.assertEqual("UNKNOWN", result.outcome)


if __name__ == "__main__":
    unittest.main()

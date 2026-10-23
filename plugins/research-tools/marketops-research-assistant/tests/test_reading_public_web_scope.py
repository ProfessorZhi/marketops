"""RC6: the user's public-web consent lets discovered sites be fetched, nothing more.

Without the grant the configured allowlist is the whole scope. With it, each
requested URL's own host joins the scope; IP literals and malformed hosts never
do, and the egress layer still refuses private addresses, other ports and
unchecked redirects.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_runtime_setup as runtime  # noqa: E402


class PublicWebScopeTests(unittest.TestCase):
    def test_without_consent_only_configured_domains(self):
        settings = {"allowed_domains": ["essexhalls.example"]}
        self.assertEqual(frozenset({"essexhalls.example"}),
                         runtime.fetch_allowlist(settings, ["https://alphahall.example/"]))

    def test_consent_adds_each_requested_host(self):
        settings = {"allowed_domains": [], "public_web_fetch": True}
        self.assertEqual(frozenset({"alphahall.example", "betacommunity.example"}),
                         runtime.fetch_allowlist(settings, [
                             "https://www.alphahall.example/contact",
                             "https://betacommunity.example/"]))

    def test_ip_literals_and_garbage_never_join(self):
        settings = {"public_web_fetch": True}
        self.assertEqual(frozenset(), runtime.fetch_allowlist(settings, [
            "https://10.0.0.5/admin", "https://[::1]/", "not a url", None,
            "https://localhost/"]))

    def test_configure_records_the_grant(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            runtime.configure(tmp, providers=["crawl4ai"], domains=[], max_calls=5,
                              task="open research", confirm=True, public_web_fetch=True)
            self.assertTrue(runtime.config(tmp)["public_web_fetch"])


if __name__ == "__main__":
    unittest.main()

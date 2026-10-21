"""An operator-chosen upstream proxy for networks that cannot reach the web directly.

Direct stays the default; with an upstream every literal, scope and port check
still runs, and a name that resolves locally is still checked for private
addresses before the tunnel is opened.
"""
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import research_config  # noqa: E402
import research_egress  # noqa: E402
from marketops_safe_egress import DnsResolutionError, EgressGate  # noqa: E402
from marketops_safe_egress.adapters.egress_proxy import _connect_via  # noqa: E402
from marketops_safe_egress.ports.dns import DnsFailureKind  # noqa: E402


class _NoDns:
    def resolve(self, host, port):
        raise DnsResolutionError(host, DnsFailureKind.FAILURE, "no local DNS")


class _Private:
    def resolve(self, host, port):
        return ("10.1.2.3",)


class GateTests(unittest.TestCase):
    allow = frozenset({"example.org"})
    ports = frozenset({443})

    def test_direct_is_the_default_and_unresolvable_names_are_refused(self):
        gate = EgressGate(_NoDns(), allowlist=self.allow, allowed_ports=self.ports)
        self.assertIsNone(gate.upstream)
        verdict = gate.check("example.org", 443)
        self.assertFalse(verdict.allowed)
        self.assertTrue(verdict.refusal.startswith("dns:"))

    def test_upstream_takes_over_resolution_only_when_local_dns_fails(self):
        gate = EgressGate(_NoDns(), allowlist=self.allow, allowed_ports=self.ports,
                          upstream=("127.0.0.1", 3128))
        verdict = gate.check("example.org", 443)
        self.assertTrue(verdict.allowed)
        self.assertEqual("resolved_by_upstream_proxy", verdict.detail)

    def test_upstream_keeps_every_other_check(self):
        gate = EgressGate(_Private(), allowlist=self.allow, allowed_ports=self.ports,
                          upstream=("127.0.0.1", 3128))
        self.assertEqual("resolves_to_private", gate.check("example.org", 443).refusal)
        self.assertFalse(gate.check("other.org", 443).allowed)
        self.assertFalse(gate.check("example.org", 22).allowed)
        self.assertFalse(gate.check("localhost", 443).allowed)


class TunnelTests(unittest.TestCase):
    def _fake_proxy(self, answer: bytes):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        seen = []

        def serve():
            conn, _ = server.accept()
            seen.append(conn.recv(4096))
            conn.sendall(answer)
            conn.close()
            server.close()
        threading.Thread(target=serve, daemon=True).start()
        return server.getsockname(), seen

    def test_connect_tunnel_is_requested_for_the_checked_target(self):
        address, seen = self._fake_proxy(b"HTTP/1.1 200 Connection established\r\n\r\n")
        sock = _connect_via(address, "example.org", 443, 5)
        sock.close()
        self.assertTrue(seen[0].startswith(b"CONNECT example.org:443 HTTP/1.1"))

    def test_a_refused_tunnel_is_an_error_not_a_site_answer(self):
        address, _ = self._fake_proxy(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        with self.assertRaises(OSError):
            _connect_via(address, "example.org", 443, 5)


class SettingTests(unittest.TestCase):
    def test_env_parsing(self):
        self.assertIsNone(research_egress.upstream_proxy_from_env(""))
        self.assertEqual(("proxy.corp", 3128),
                         research_egress.upstream_proxy_from_env("http://proxy.corp:3128"))
        for bad in ("http://u:p@proxy:1", "socks5://proxy:1", "proxy:1"):
            with self.assertRaises(ValueError):
                research_egress.upstream_proxy_from_env(bad)

    def test_config_stores_and_loads_but_environment_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {research_config.CONFIG_ENV: os.path.join(tmp, "config.json")}
            research_config.configure(os.path.join(tmp, "data"),
                                      os.path.join(tmp, "exp", "Research_Experience.md"),
                                      env=env)
            self.assertEqual("DIRECT", research_config.load_upstream_proxy_into_environment(env))
            research_config.set_upstream_proxy("http://proxy.corp:3128", env=env)
            self.assertEqual("CONFIG", research_config.load_upstream_proxy_into_environment(env))
            self.assertEqual("http://proxy.corp:3128", env[research_config.UPSTREAM_PROXY_ENV])
            env[research_config.UPSTREAM_PROXY_ENV] = "http://other:1"
            self.assertEqual("ENVIRONMENT", research_config.load_upstream_proxy_into_environment(env))


if __name__ == "__main__":
    unittest.main()

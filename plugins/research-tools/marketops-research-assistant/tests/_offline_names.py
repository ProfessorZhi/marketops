"""Offline tests that name documentation domains must not need a DNS server.

The target guard resolves every host before a connection is allowed. Offline
tests use RFC 2606 names (example.org, example.com); on a machine without DNS
(a sandbox, a CI runner behind a proxy) the lookup failed and the test failed
for a reason that has nothing to do with what it checks. Only those reserved
names get a fixed public answer here; every other name still goes to the real
resolver, so the guard is not weakened.
"""
from __future__ import annotations

import socket
from unittest.mock import patch

#: The IANA documentation address the example domains answer with.
EXAMPLE_ADDRESS = "93.184.215.14"
RESERVED = ("example.org", "example.com", "example.net")


def stub_reserved_names(testcase) -> None:
    import research_target_guard
    real = socket.getaddrinfo

    def getaddrinfo(host, port, *args, **kwargs):
        name = str(host or "").lower().rstrip(".")
        if any(name == root or name.endswith("." + root) for root in RESERVED):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (EXAMPLE_ADDRESS, port or 443))]
        return real(host, port, *args, **kwargs)

    patcher = patch.object(research_target_guard.socket, "getaddrinfo", getaddrinfo)
    patcher.start()
    testcase.addCleanup(patcher.stop)

"""Deterministic target-guard fixture for the TinyFish adapter tests.

Why this exists: the production guard asks the resolver once, and the name these
tests use (`public.example.org`) does not resolve. That is correct behaviour and
must not be weakened -- but it means an offline test that reaches the guard dies
with `TARGET_RESOLUTION_FAILED` before it ever exercises the thing it is about
(wallet preflight, tier gate, charge state).

So the tests inject a **deterministic resolver answer**, not a weaker guard:

* when the active guard declares W-A's seam
  ``approved_url(value, *, resolve=True, resolver=None)``, the **real** guard runs
  with a fake resolver -- the real shape rule and the real resolution rule both
  execute, only the DNS answer is synthetic;
* otherwise the **real** shape rule runs with ``resolve=False`` -- the
  production guard's own documented "do not ask the network yet" mode. Nothing is
  reimplemented here.

A tripwire (`no_real_dns`) makes any attempt at real resolution fail loudly, so
"no network" is asserted rather than assumed.

Nothing in this file is imported by production code.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_adapter_tinyfish as tinyfish  # noqa: E402

#: The module's own guard resolver, captured before any fixture patches it.
#: Tests that are *about* the seam restore this rather than fighting the fixture.
REAL_URL_GUARD = tinyfish._url_guard

#: An IETF protocol-assignment anycast address (RFC 6890, 192.0.0.0/24). It is
#: globally routable per `ipaddress`, and it is not a host we would ever connect
#: to -- the fake resolver only ever *returns* it.
SYNTHETIC_PUBLIC = "192.0.0.9"
SYNTHETIC_LOOPBACK = "127.0.0.1"
ALLOWED_HOST = "public.example.org"


class FakeResolver:
    """`HostResolver`-shaped: `resolve(host, port) -> tuple[str, ...]`."""

    def __init__(self, answer=SYNTHETIC_PUBLIC, *, record=None):
        self.answer = answer
        self.record = record if record is not None else []

    def resolve(self, host: str, port: int) -> tuple:
        self.record.append((host, port))
        return (self.answer,)


class NoSuchHostResolver:
    """A resolver that fails the way an unresolvable name does."""

    def resolve(self, host: str, port: int) -> tuple:
        try:
            from marketops_safe_egress.ports.dns import DnsResolutionError
        except Exception:  # pragma: no cover - fixture module absent
            raise OSError("synthetic resolution failure") from None
        raise DnsResolutionError("synthetic: name does not resolve")


def _guard_mode(guard) -> str:
    """How the fixture can drive this guard without inventing a rule."""
    import inspect
    try:
        parameters = inspect.signature(guard).parameters
    except (TypeError, ValueError):
        return "opaque"
    if "resolver" in parameters:
        return "resolver"
    if "resolve" in parameters:
        return "shape_only"
    return "opaque"


class EgressFixture:
    """Installs the deterministic guard for one test case."""

    def __init__(self, resolver=None):
        self.resolver = resolver or FakeResolver()
        self.calls = []
        self.double_used = False
        try:
            self.real_guard = tinyfish._url_guard()
        except ValueError:
            # This checkout has no production guard at all (`research_egress`
            # absent). Tests that are not about the guard still need the adapter
            # to run, so a clearly-labelled stand-in applies the operator
            # allowlist and nothing else. Tests that ARE about the guard assert
            # `mode == "resolver"` and say so when it is not.
            self.real_guard = None
            self.mode = "unavailable"
        else:
            self.mode = _guard_mode(self.real_guard)

    def guard(self, value, *, resolve=False, resolver=None):
        self.calls.append(bool(resolve))
        if self.mode == "unavailable":
            self.double_used = True
            return self._allowlist_double(value)
        if self.mode == "resolver":
            # The real guard, the real rules, a synthetic answer.
            return self.real_guard(value, resolve=resolve,
                                   resolver=resolver or self.resolver)
        if self.mode == "shape_only":
            # The real guard's own "shape only" mode. Resolution is not asked
            # for here; the resolution rule is covered by the refusing double
            # in the negative tests and by W-A's own suite.
            return self.real_guard(value, resolve=False)
        return self.real_guard(value)

    @staticmethod
    def _allowlist_double(value):
        """Test-only stand-in for a checkout with no production guard.

        It is deliberately **stricter** than the production rule, never looser:
        https only, no userinfo, port 443 or none, no literal IP, and the host
        must be in the operator allowlist. A stand-in that accepted anything the
        real guard would refuse would turn a production refusal into a green
        test; one that refuses something the real guard would allow can only
        produce a false red, which is visible and harmless.

        It is not evidence about the production rule, and tests whose subject IS
        that rule assert `mode == "resolver"` and say so when it is not.
        """
        import ipaddress
        import os
        from urllib.parse import urlsplit
        try:
            parts = urlsplit(str(value))
            host = (parts.hostname or "").casefold().strip(".")
        except ValueError:
            raise ValueError("OFFICIAL_MCP_TARGET_NOT_ALLOWED") from None
        if (parts.scheme != "https" or not host
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443)
                or host.endswith((".local", ".internal")) or host == "localhost"):
            raise ValueError("OFFICIAL_MCP_TARGET_NOT_ALLOWED")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("OFFICIAL_MCP_TARGET_NOT_ALLOWED")
        allowed = {d.strip().casefold().strip(".")
                   for d in os.environ.get("MARKETOPS_FETCH_ALLOWED_DOMAINS", "").split(",")
                   if d.strip()}
        if not any(host == d or host.endswith("." + d) for d in allowed):
            raise ValueError("OFFICIAL_MCP_TARGET_NOT_ALLOWED")
        return value

    @property
    def resolution_exercised(self) -> bool:
        return self.mode == "resolver"


def install(case, resolver=None) -> EgressFixture:
    """Install for ``case`` and register the undo with ``addCleanup``."""
    fixture = EgressFixture(resolver=resolver)
    started = patch.object(tinyfish, "_url_guard", lambda: fixture.guard)
    started.start()
    case.addCleanup(started.stop)
    return fixture


class _DnsTripwire:
    def __init__(self, original):
        self.original = original
        self.attempts = []

    def __call__(self, host, port, *args, **kwargs):
        self.attempts.append((host, port))
        raise AssertionError(
            "real DNS resolution attempted for %r; the test must supply a "
            "deterministic resolver instead" % (host,))


def no_real_dns(case):
    """Fail loudly if any code path asks the system resolver."""
    tripwire = _DnsTripwire(socket.getaddrinfo)
    started = patch.object(socket, "getaddrinfo", tripwire)
    started.start()
    case.addCleanup(started.stop)
    return tripwire


def install_allowed_domains(case, domains=ALLOWED_HOST):
    import os
    started = patch.dict(os.environ, {"MARKETOPS_FETCH_ALLOWED_DOMAINS": domains})
    started.start()
    case.addCleanup(started.stop)

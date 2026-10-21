"""Shared fixtures for the independent Wave-1 quality gate.

These tests are *hostile offline regression tests*: every external effect is
replaced by a synthetic local decoy, a fake resolver or a fake opener, so a run
makes no network call, touches no credential and no real provider account.

Design rules for everything under ``qa_gate/``:

* A test asserts a **contract**, not the current implementation. Where the
  contract is not yet met the test must FAIL, not be softened to pass.
* A test that cannot execute because an optional upstream dependency or an
  external authorization is absent must report **BLOCKED** (an explicit
  ``pytest.skip`` carrying ``BLOCKED:`` in the reason), never a silent pass.
* No test may read, print or persist a credential; the leak assertions check
  that a *synthetic* secret injected for the test never surfaces in a message.
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]        # .../marketops-research-assistant/tests
PLUGIN_DIR = Path(__file__).resolve().parents[2]       # .../marketops-research-assistant
REPO_ROOT = Path(__file__).resolve().parents[5]        # repository root
SCRIPTS = PLUGIN_DIR / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

BLOCKED = "BLOCKED:"


def blocked(reason: str):
    """Skip with an explicit BLOCKED marker so it is never read as a pass."""
    pytest.skip(BLOCKED + " " + reason, allow_module_level=False)


@pytest.fixture()
def data_root():
    """A disposable MarketOps research data root with a private ``state/``."""
    with tempfile.TemporaryDirectory(prefix="qa-gate-") as tmp:
        root = Path(tmp) / "data"
        (root / "state").mkdir(parents=True, exist_ok=True)
        yield root


@pytest.fixture()
def clean_env(monkeypatch):
    """Remove every MarketOps/vendor variable this suite could inherit.

    A leaking operator environment is exactly how a "default off" control turns
    into a false green, so every test starts from a known-empty baseline and
    opts in explicitly.
    """
    prefixes = ("MARKETOPS_", "SERPER_", "SERPAPI_", "TINYFISH_", "TYPESAFE_",
                "CRAWL4AI_", "OPENAI_", "ANTHROPIC_")
    for name in list(os.environ):
        if name.startswith(prefixes):
            monkeypatch.delenv(name, raising=False)
    return monkeypatch


def set_env(monkeypatch, **values):
    for key, value in values.items():
        monkeypatch.setenv(key, str(value))


class SocketSentinel:
    """Record every attempt to open a socket, and refuse to actually connect.

    Used to prove that a refused target is refused *before a connection*, the
    distinction Appendix D's P1-01 matrix turns on.
    """

    def __init__(self):
        self.attempts: list[tuple] = []
        self._real_connect = socket.socket.connect
        self._real_getaddrinfo = socket.getaddrinfo

    def __enter__(self):
        sentinel = self

        def connect(sock, address, *args, **kwargs):
            sentinel.attempts.append(address)
            raise AssertionError(
                f"BLOCKED-BY-SENTINEL: a connection to {address!r} was attempted")

        socket.socket.connect = connect
        return self

    def __exit__(self, *exc):
        socket.socket.connect = self._real_connect
        return False


def module_available(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None

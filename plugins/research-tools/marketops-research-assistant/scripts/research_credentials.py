#!/usr/bin/env python3
"""Local account-owner API-key setup. Secrets live only in native OS keyring.

No network, provider calls, dotenv files, command-line key values or MCP write
tool. Importing this module does not read credentials. The server explicitly
loads them once before constructing adapters; an injected process value wins.
"""
from __future__ import annotations
import argparse
import getpass
import json
import os
import re
import sys

KEYRING_SERVICE = "MarketOpsResearch_Provider_API_Keys_v1"
PROVIDER_KEYS = {
    "serper": "SERPER_API_KEY",
    "serpapi": "SERPAPI_API_KEY",
    "tinyfish": "TINYFISH_API_KEY",
    "typesafe": "TYPESAFE_API_KEY",
}


def _backend():
    # Reuse the existing native-backend allowlist, not a second interpretation
    # of a positive keyring priority (which plaintext alternatives also have).
    from research_official_mcp_auth import _backend as secure_backend
    return secure_backend()


def _present(value: str | None) -> bool:
    return bool(value and value.strip() and not re.fullmatch(
        r"\$\{[A-Z][A-Z0-9_]*\}", value.strip()))


def configure_keys(providers=None) -> dict:
    selected = list(PROVIDER_KEYS) if providers is None else list(providers)
    if not selected or any(p not in PROVIDER_KEYS for p in selected):
        raise ValueError("UNKNOWN_PROVIDER")
    backend = _backend()  # Refuse before collecting anything if storage is unsafe.
    if not sys.stdin.isatty():
        raise ValueError("INTERACTIVE_TERMINAL_REQUIRED")
    states = {}
    for provider in selected:
        name = PROVIDER_KEYS[provider]
        value = getpass.getpass(f"{provider} API Key（隐藏输入，回车保留已有值）: ").strip()
        if not value:
            states[provider] = "UNCHANGED"
            continue
        if not _present(value) or any(ch in value for ch in "\r\n\x00"):
            raise ValueError("INVALID_API_KEY_INPUT")
        try:
            backend.set_password(KEYRING_SERVICE, name, value)
        except Exception:
            raise ValueError("OS_KEYRING_WRITE_FAILED") from None
        states[provider] = "SAVED_NOT_AUTHENTICATED"
    return {"status": "LOCAL_KEYS_CONFIGURED", "providers": states,
            "network_calls": 0, "secret_values_returned": False}


def load_into_environment(environ=None) -> dict:
    env = os.environ if environ is None else environ
    loaded = {}
    existing = [name for name in PROVIDER_KEYS.values() if _present(env.get(name))]
    missing = [name for name in PROVIDER_KEYS.values() if name not in existing]
    if not missing:
        return {"loaded": loaded, "environment": existing, "keyring": "NOT_NEEDED"}
    # Literal host placeholders are never sent to a provider as credentials.
    for name in missing:
        env.pop(name, None)
    try:
        backend = _backend()
    except Exception:
        return {"loaded": loaded, "environment": existing, "keyring": "UNAVAILABLE"}
    for name in missing:
        try:
            value = backend.get_password(KEYRING_SERVICE, name)
        except Exception:
            continue
        if _present(value):
            env[name] = value.strip()
            loaded[name] = "OS_KEYRING"
    return {"loaded": loaded, "environment": existing, "keyring": "AVAILABLE"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("configure", "status"))
    parser.add_argument("--provider", action="append", choices=tuple(PROVIDER_KEYS))
    args = parser.parse_args(argv)
    try:
        result = configure_keys(args.provider) if args.action == "configure" else load_into_environment(dict(os.environ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, EOFError, KeyboardInterrupt) as exc:
        # Do not serialize backend exceptions: a backend can echo the input.
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z_]+", str(exc)) else "CREDENTIAL_SETUP_INTERRUPTED"
        print(json.dumps({"status": "CREDENTIAL_SETUP_FAILED", "code": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

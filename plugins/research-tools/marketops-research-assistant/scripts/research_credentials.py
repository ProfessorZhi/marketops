#!/usr/bin/env python3
"""Local account-owner API-key setup. Secrets live only in native OS keyring.

No network, provider calls, dotenv files, command-line key values or MCP write
tool. Importing this module does not read credentials. The server explicitly
loads them once before constructing adapters; an injected process value wins.

Two entry points write the keyring, and neither returns a stored value.
`configure_keys` prompts on a real terminal with hidden input.
`configure_keys_from_values` takes keys that were already collected — that is
how an installing agent stores what the user handed it, so the values travel
over stdin (`configure --stdin`) rather than argv, a file or an environment
variable.
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
    "deepseek": "DEEPSEEK_API_KEY",
}


def _backend():
    # Reuse the existing native-backend allowlist, not a second interpretation
    # of a positive keyring priority (which plaintext alternatives also have).
    from research_official_mcp_auth import _backend as secure_backend
    return secure_backend()


def _present(value: str | None) -> bool:
    return bool(value and value.strip() and not re.fullmatch(
        r"\$\{[A-Z][A-Z0-9_]*\}", value.strip()))


def _selected(providers) -> list[str]:
    chosen = list(PROVIDER_KEYS) if providers is None else list(providers)
    if not chosen or any(p not in PROVIDER_KEYS for p in chosen):
        raise ValueError("UNKNOWN_PROVIDER")
    return chosen


def _save(backend, provider: str, value: str) -> str:
    if not _present(value) or any(ch in value for ch in "\r\n\x00"):
        raise ValueError("INVALID_API_KEY_INPUT")
    try:
        backend.set_password(KEYRING_SERVICE, PROVIDER_KEYS[provider], value)
    except Exception:
        raise ValueError("OS_KEYRING_WRITE_FAILED") from None
    return "SAVED_NOT_AUTHENTICATED"


def _receipt(states: dict) -> dict:
    return {"status": "LOCAL_KEYS_CONFIGURED", "providers": states,
            "network_calls": 0, "secret_values_returned": False}


def configure_keys(providers=None) -> dict:
    selected = _selected(providers)
    backend = _backend()  # Refuse before collecting anything if storage is unsafe.
    if not sys.stdin.isatty():
        raise ValueError("INTERACTIVE_TERMINAL_REQUIRED")
    states = {}
    for provider in selected:
        value = getpass.getpass(f"{provider} API Key（隐藏输入，回车保留已有值）: ").strip()
        if not value:
            states[provider] = "UNCHANGED"
            continue
        states[provider] = _save(backend, provider, value)
    return _receipt(states)


def configure_keys_from_values(values, providers=None) -> dict:
    """Store keys already in hand; the installing agent's path.

    An absent or empty entry leaves that provider untouched, so a user who has
    only three of the four services can still finish setup.
    """
    selected = _selected(providers)
    if not isinstance(values, dict):
        raise ValueError("INVALID_API_KEY_INPUT")
    backend = _backend()
    states = {}
    for provider in selected:
        raw = values.get(provider)
        if raw is None or raw == "":
            states[provider] = "UNCHANGED"
            continue
        if not isinstance(raw, str):
            raise ValueError("INVALID_API_KEY_INPUT")
        states[provider] = _save(backend, provider, raw.strip())
    return _receipt(states)


def _read_stdin_values() -> dict:
    try:
        values = json.loads(sys.stdin.read())
    except ValueError:
        raise ValueError("INVALID_API_KEY_INPUT") from None
    if not isinstance(values, dict):
        raise ValueError("INVALID_API_KEY_INPUT")
    return values


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
    parser.add_argument("--stdin", action="store_true",
                        help="configure: read the keys as a JSON object on stdin instead of "
                             "prompting on a terminal, so no key reaches the command line")
    args = parser.parse_args(argv)
    try:
        if args.action == "configure":
            result = (configure_keys_from_values(_read_stdin_values(), args.provider)
                      if args.stdin else configure_keys(args.provider))
        else:
            result = load_into_environment(dict(os.environ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, EOFError, KeyboardInterrupt) as exc:
        # Do not serialize backend exceptions: a backend can echo the input.
        code = str(exc) if isinstance(exc, ValueError) and re.fullmatch(r"[A-Z_]+", str(exc)) else "CREDENTIAL_SETUP_INTERRUPTED"
        print(json.dumps({"status": "CREDENTIAL_SETUP_FAILED", "code": code}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

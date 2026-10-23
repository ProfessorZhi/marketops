#!/usr/bin/env python3
"""TinyFish official MCP OAuth account login for MarketOps's own MCP client.

This is OPTIONAL and must be started interactively by the account owner.
OAuth credentials are stored only in the current OS keyring backend, never
in Git, the Lite SQLite database, an exported workbook or the setup receipt.
No credentials or browser sessions are created by importing this module.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import re
import sys
from urllib.parse import parse_qs, urlsplit

KEYRING_SERVICE = "MarketOpsResearch_TinyFish_Official_MCP"
OAUTH_BASE = "https://agent.tinyfish.ai/mcp"


def _account_slot() -> str:
    slot = os.environ.get("MARKETOPS_TINYFISH_ACCOUNT_ALIAS", "default").strip()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,50}", slot):
        raise ValueError("INVALID_TINYFISH_ACCOUNT_ALIAS")
    return slot


def _backend():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError:
        raise ValueError("OS_KEYRING_NOT_INSTALLED") from None
    try:
        impl = keyring.get_keyring()
        # Some alternative plaintext-file backends advertise positive
        # priorities. A positive score alone does NOT prove OS protection.
        # Fail closed on third-party or composite keyring backends rather than
        # accidentally saving a refresh token in a readable plaintext file.
        backend_module = type(impl).__module__.casefold()
        native = (
            "keyring.backends.secretservice",
            "keyring.backends.windows",
            "keyring.backends.macos",
            "keyring.backends.kwallet",
            "keyring.backends.libsecret",
        )
        if (getattr(impl, "priority", -1) <= 0 or
                not backend_module.startswith(native)):
            raise ValueError("OS_KEYRING_SECURE_BACKEND_UNAVAILABLE")
        return keyring
    except (KeyringError, RuntimeError):
        raise ValueError("OS_KEYRING_SECURE_BACKEND_UNAVAILABLE") from None


def _slot(name: str) -> str:
    return _account_slot() + ":" + name


class OSKeyringTokenStorage:
    """TokenStorage's async interface; secrets only in OS credential manager."""

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        keyring = _backend()
        raw = keyring.get_password(KEYRING_SERVICE, _slot("tokens"))
        return OAuthToken.model_validate_json(raw) if raw else None

    async def set_tokens(self, tokens):
        _backend().set_password(KEYRING_SERVICE, _slot("tokens"),
                                tokens.model_dump_json())

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        raw = _backend().get_password(KEYRING_SERVICE, _slot("client_info"))
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def set_client_info(self, client_info):
        _backend().set_password(KEYRING_SERVICE, _slot("client_info"),
                                client_info.model_dump_json())


def has_saved_tokens() -> bool:
    """Only a local presence hint, NOT proof the OAuth session is valid."""
    try:
        return bool(_backend().get_password(KEYRING_SERVICE, _slot("tokens")))
    except (ValueError, Exception):
        return False


async def _needs_human(_auth_url: str):
    raise ValueError("TINYFISH_OAUTH_INTERACTIVE_SETUP_REQUIRED")


async def _needs_human_callback():
    raise ValueError("TINYFISH_OAUTH_INTERACTIVE_SETUP_REQUIRED")


async def _open_auth_link(url: str):
    # This URL is a short-lived OAuth authorization link, NOT an API Key.
    # It is shown only inside this user-invoked CLI, not an MCP tool result.
    if urlsplit(url).scheme != "https":
        raise ValueError("TINYFISH_OAUTH_INVALID_AUTH_URL")
    print("在自己的浏览器打开下面的官方登录地址；不要把登录回调发到聊天/Git：")
    print(url, flush=True)


async def _paste_callback():
    from mcp.client.auth import AuthorizationCodeResult
    raw = getpass.getpass("浏览器重定向后，粘贴完整回调URL（输入不回显）：").strip()
    parsed = urlsplit(raw)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("TINYFISH_OAUTH_CALLBACK_INVALID")
    params = parse_qs(parsed.query)
    code = params.get("code", [None])[0]
    state = params.get("state", [None])[0]
    if not code or not state:
        raise ValueError("TINYFISH_OAUTH_CALLBACK_INVALID")
    return AuthorizationCodeResult(code=code, state=state,
                                   iss=params.get("iss", [None])[0])


def oauth_provider(*, interactive: bool):
    """mcp Python SDK OAuth auth object, automatically refreshes cached tokens.

    In regular MCP tool calls this NEVER opens a browser or prompts: expired
    sessions return OAUTH_SETUP_REQUIRED until the user explicitly logs in.
    """
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import OAuthClientMetadata
    from pydantic import AnyUrl
    _backend()  # fail before any remote request if no safe OS keyring
    return OAuthClientProvider(
        server_url=OAUTH_BASE,
        client_metadata=OAuthClientMetadata(
            client_name="MarketOps Research Assistant",
            redirect_uris=[AnyUrl("http://localhost:8765/callback")],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            scope="user",
        ),
        storage=OSKeyringTokenStorage(),
        redirect_handler=_open_auth_link if interactive else _needs_human,
        callback_handler=_paste_callback if interactive else _needs_human_callback,
    )


async def _login():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    import httpx2
    auth=oauth_provider(interactive=True)
    # Never the machine's ambient proxy (a stale IDE/VPN proxy answers 502): only
    # the one configured for MarketOps, as for every other provider call.
    from dual_search_mcp import upstream_proxy
    async with httpx2.AsyncClient(auth=auth,follow_redirects=False,trust_env=False,
                                  proxy=upstream_proxy() or None,
                                  timeout=httpx2.Timeout(45.0,connect=10.0)) as http:
        async with streamable_http_client(OAUTH_BASE,http_client=http) as streams:
            async with ClientSession(streams[0],streams[1]) as session:
                await session.initialize()
                found=await session.list_tools()
                return len(found.tools)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("status","login"))
    parser.add_argument("--provider",choices=("tinyfish",),default="tinyfish")
    args=parser.parse_args(argv)
    if args.action=="status":
        # Silent, offline, no token or account name output.
        print(json.dumps({"provider":"tinyfish",
                          "os_keyring_oauth_tokens_present":has_saved_tokens(),
                          "authenticated_live":False,
                          "remote_probe_performed":False,
                          "account_login_required_to_verify":True}))
        return 0
    try:
        count=asyncio.run(_login())
        print(json.dumps({"provider":"tinyfish",
                          "status":"OAUTH_MCP_TOOLS_DISCOVERED",
                          "tool_count":count,
                          "oauth_tokens_saved_in_os_keyring":has_saved_tokens(),
                          "provider_wallet_checked":False,
                          "paid_agent_activated":False}))
        return 0
    except (ValueError, OSError):
        print(json.dumps({"status":"TINYFISH_OAUTH_SETUP_BLOCKED",
                          "reason":"CHECK_OS_KEYRING_OR_COMPLETE_BROWSER_AUTH_WITHOUT_SHARING_SECRETS"}),
              file=sys.stderr)
        return 2
    except Exception:
        # Never render an SDK transport exception with OAuth codes/URLs.
        print(json.dumps({"status":"TINYFISH_OAUTH_SETUP_FAILED",
                          "reason":"SAFE_EXTERNAL_OAUTH_FAILURE_RETRY_ONLY_IF_NOT_ALREADY_LOGGED_IN"}),
              file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""A urllib proxy handler that always uses the proxy it was given.

urllib's ``ProxyHandler`` skips the proxy for any host the environment's
``NO_PROXY`` lists. For MarketOps' own egress proxy that is a way around the
guard: the allowlist and the audit live in that proxy, and a machine whose
``NO_PROXY`` names a host (``127.0.0.1``, an internal domain) would send that
request straight out. Requests bound to the egress proxy go through it, always.
"""
from __future__ import annotations

import base64
import urllib.request
from urllib.parse import unquote

__all__ = ["AlwaysProxyHandler"]


class AlwaysProxyHandler(urllib.request.ProxyHandler):
    """``ProxyHandler`` without the ``NO_PROXY`` bypass; otherwise the same."""

    def proxy_open(self, req, proxy, type):  # noqa: A002 - urllib's own signature
        orig_type = req.type
        proxy_type, user, password, hostport = urllib.request._parse_proxy(proxy)
        if proxy_type is None:
            proxy_type = orig_type
        if user and password:
            credentials = f"{unquote(user)}:{unquote(password)}"
            req.add_header("Proxy-authorization",
                           "Basic " + base64.b64encode(credentials.encode()).decode("ascii"))
        req.set_proxy(unquote(hostport), proxy_type)
        if orig_type == proxy_type or orig_type == "https":
            return None  # let the following handlers send it, through the proxy
        return self.parent.open(req, timeout=req.timeout)

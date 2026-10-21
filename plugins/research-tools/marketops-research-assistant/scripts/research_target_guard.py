#!/usr/bin/env python3
"""Resolve a target name and refuse it if any answer is not globally routable.

## Why this module exists, and why it is separate

The two input guards — ``research_provider_extensions._public_https_url`` and
``research_official_mcp._approved_url`` — were **string rules**. They checked the
scheme, the userinfo, the port, the literal text of the hostname and an operator
allowlist, and then handed the URL on. None of that can tell a public site from
cloud metadata, because the answer is not in the name: ``www.allowed.example``
resolves to whatever whoever controls that record says it resolves to, and the
only place the decision used to happen was inside Chromium, after the connection
had been made to a name that had already been approved.

So this module adds the missing half: ask the resolver, and refuse when *any*
answer is loopback, private, link-local, unique-local or metadata.

**Fail closed on a resolution failure.** "I could not find out where this name
points" is not a reason to connect to it, and it is not the same statement as
"it points somewhere public". Both refuse; the reason codes differ so an operator
can tell a typo from an attack.

**Every address, not the first.** A name with one public and one loopback answer
is exactly the DNS-rebinding shape; refusing the whole set is the only answer
that does not depend on which record the client happened to pick.

A guard that resolves is still not an egress proxy: between this check and the
connection the record can change. This narrows the window; it does not close it,
and the caller that needs it closed must connect to the address checked here —
which is what the managed egress boundary exists for. Saying otherwise would be
the "successful proxy decision is not trustworthy content" mistake in a new
costume.
"""
from __future__ import annotations

import ipaddress
import socket

#: This module's refusal vocabulary. Kept closed and small on purpose: these
#: become the `reason` on a refused fetch, and a caller must be able to tell
#: "the name is not approved" from "the name is approved and points inward".
REASONS = (
    "TARGET_RESOLUTION_FAILED",
    "TARGET_RESOLVES_NON_GLOBAL",
    "TARGET_RESOLVES_TO_MIXED_ADDRESSES",
)


def _is_globally_routable(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if parsed.version == 6 and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    return parsed.is_global


def resolve_checked(hostname: str, port: int = 443) -> tuple[str, ...]:
    """Resolve ``hostname`` and return its addresses, or raise a ``ValueError``.

    The refusal code is the first colon-separated token, matching this
    repository's existing refusal convention, and the detail after it names the
    offending address class — never the full attacker-chosen record set, which
    would be a new way to get text into our logs.
    """
    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError):
        raise ValueError(
            f"TARGET_RESOLUTION_FAILED: {hostname!r} did not resolve; a name whose "
            "target cannot be established is not a name that may be connected to"
        ) from None
    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4] if len(info) > 4 else ()
        if sockaddr and isinstance(sockaddr[0], str):
            addresses.append(sockaddr[0])
    if not addresses:
        raise ValueError(f"TARGET_RESOLUTION_FAILED: {hostname!r} resolved to no address")
    offenders = sorted({a for a in addresses if not _is_globally_routable(a)})
    if offenders:
        # Deliberately reports the *class*, not the address: the refusal travels
        # into receipts, and a metadata address in a receipt is a hint about the
        # environment this ran in.
        raise ValueError(
            "TARGET_RESOLVES_NON_GLOBAL: an approved name resolved to a "
            f"non-globally-routable address ({_address_class(offenders[0])}); "
            "refused before any connection"
        )
    # Multiple answers are *not* a refusal: round-robin and anycast are ordinary,
    # and refusing them would make the product unusable while adding no safety.
    # Rebinding is the address changing between the check and the connection —
    # which is why the caller that needs that window closed must connect to an
    # address from this set rather than re-resolving, and why this function
    # returns the set instead of a verdict.
    return tuple(addresses)


def _address_class(address: str) -> str:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return "unparseable"
    if parsed.version == 6 and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    for attribute, label in (("is_loopback", "loopback"),
                             ("is_link_local", "link-local"),
                             ("is_private", "private"),
                             ("is_reserved", "reserved"),
                             ("is_multicast", "multicast"),
                             ("is_unspecified", "unspecified")):
        if getattr(parsed, attribute, False):
            return f"{label} IPv{parsed.version}"
    return "non-global"

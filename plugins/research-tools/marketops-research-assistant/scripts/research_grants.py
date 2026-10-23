#!/usr/bin/env python3
"""Reviewed capability registry, T0-T4 tiers, and the grant that opens T3/T4.

Pure policy: no network, no database, no vendor SDK. The unified usage ledger
(``research_usage_ledger.py``) imports this module so that *one* table decides a
capability's tier; a caller cannot declare its own risk level, because a caller
that can name its own tier can name the lowest one.

## The three things this module refuses to pretend

1. **An unreviewed capability is denied, not "unclassified".** A vendor publishing
   a new tool must not thereby create a callable paid path. :func:`policy_decision`
   returns ``UNREVIEWED_CAPABILITY`` for any (provider, capability) pair absent
   from the registry, and the ledger refuses to reserve for it too.

2. **T3/T4 are closed until a verification key exists out of band.** A grant is an
   Ed25519 signature over a canonical payload. The plugin can *verify* one; it
   deliberately has no key with which to *mint* one. With no public key configured
   the answer is ``GRANT_VERIFICATION_UNAVAILABLE`` — never "local, therefore
   trusted", which would turn a missing configuration into an authorization.

3. **A shell-capable process on the same OS account is not excluded by any of
   this.** It can read whatever that user can read, including a signing key left on
   that account, and it can allocate a pseudo-terminal. Where that is the
   deployment, a grant records that an operator *said yes*; it is not an isolation
   boundary. :func:`authority_boundary` states this in the same breath as what the
   signature does buy, so a caller cannot quote half of it.

Minting lives in the CLI at the bottom of this file and is reachable from no MCP
tool. The enforceable ceilings in a same-user deployment are the vendor's own
(prepaid wallet, plan quota) and the host's approval prompt — see
``references/UNIFIED_USAGE_LEDGER.md`` §6.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from hashlib import sha256
from pathlib import Path

__all__ = [
    "Tier", "Capability", "CapabilityRegistry", "DEFAULT_REGISTRY",
    "Grant", "GrantAuthority", "GrantRefused",
    "WalletObservation", "paid_agent_preconditions", "Preconditions",
    "policy_decision", "Decision", "authority_boundary",
    "canonical_payload", "mint_grant", "main",
]

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
TOKEN_SCHEME = "marketops-grant-v1"


class Tier(IntEnum):
    """Required controls, not a vendor's current price.

    The tier is the **highest** requirement across four questions: how it is
    billed, how determinate that cost is, whether it has side effects, and how
    sensitive the data is. A read-only call against a private logged-in account is
    T4 on sensitivity even though it costs nothing on billing.
    """

    T0 = 0  # local deterministic / read-only account state; no external billing
    T1 = 1  # zero marginal price but rate- or quota-limited, or local compute
    T2 = 2  # metered at a price knowable in advance
    T3 = 3  # open-ended billing: the vendor exposes no per-run step/amount bound
    T4 = 4  # authenticated, recurring or state-changing

    @property
    def default_open(self) -> bool:
        """T0-T2 may run within scope and caps; T3/T4 start closed."""
        return self < Tier.T3

    @property
    def needs_grant(self) -> bool:
        return self >= Tier.T3


@dataclass(frozen=True)
class Capability:
    """One reviewed provider capability. ``capability_id`` is ours, not the vendor's.

    ``meter`` is the unit the *vendor* counts (search_query, agent_step, ...). It
    is recorded so a quota ceiling can be expressed in the same unit the invoice
    uses, and so a vendor changing its metering shows up as a changed registry
    entry rather than as a quietly different number in a report.
    """

    capability_id: str
    provider: str
    tier: Tier
    meter: str
    effect: str
    allowed_transports: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("capability_id", self.capability_id), ("provider", self.provider),
                            ("meter", self.meter), ("effect", self.effect)):
            if not isinstance(value, str) or not ID.fullmatch(value):
                raise ValueError(f"Capability.{name} must be a short opaque identifier")
        if not isinstance(self.tier, Tier):
            raise ValueError("Capability.tier must be a Tier")

    def allows_transport(self, transport: str) -> bool:
        """An empty allowlist means "any reviewed transport", not "anything goes"."""
        return not self.allowed_transports or transport in self.allowed_transports


class CapabilityRegistry:
    """The reviewed set. Look-up is by (provider, capability_id), never by vendor tool name."""

    def __init__(self, capabilities):
        entries: dict[tuple[str, str], Capability] = {}
        for capability in capabilities:
            key = (capability.provider, capability.capability_id)
            if key in entries:
                raise ValueError(f"duplicate capability in registry: {key}")
            entries[key] = capability
        self._entries = entries

    def get(self, provider: str, capability_id: str) -> Capability | None:
        return self._entries.get((provider, capability_id))

    def require(self, provider: str, capability_id: str) -> Capability:
        capability = self.get(provider, capability_id)
        if capability is None:
            raise GrantRefused(
                "UNREVIEWED_CAPABILITY",
                f"{provider}/{capability_id} is not in the reviewed capability registry; "
                "an upstream tool name is not an authorization",
            )
        return capability

    def __iter__(self):
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)


#: The five providers' researched capabilities, with the tier each one needs.
#:
#: This is a policy table in code, not a data file, so that changing what may spend
#: money is reviewable in the same diff as the code enforcing it. Every entry is
#: documented as *available in the architecture* — it says nothing about whether a
#: credential exists, whether the vendor's schema matches, or whether a call has
#: ever succeeded. See references/PROVIDER_CONNECTIONS.json for those states.
DEFAULT_REGISTRY = CapabilityRegistry((
    Capability("search", "serper", Tier.T2, "search_query", "read_only"),
    Capability("search", "serpapi", Tier.T2, "search_query", "read_only"),
    Capability("search", "deepseek", Tier.T2, "search_query", "read_only"),
    Capability("fetch", "deepseek", Tier.T2, "fetch_url", "read_only"),
    Capability("search", "tinyfish", Tier.T1, "search_query", "read_only"),
    Capability("fetch", "tinyfish", Tier.T1, "fetch_url", "read_only"),
    Capability("render", "crawl4ai", Tier.T1, "local_page", "local_compute"),
    Capability("extract", "crawl4ai", Tier.T1, "local_page", "local_compute"),
    Capability("judge", "typesafe_jev", Tier.T2, "request", "read_only"),
    Capability("wallet_read", "tinyfish", Tier.T0, "request", "read_only"),
    Capability("usage_read", "tinyfish", Tier.T0, "request", "read_only"),
    Capability("agent_run", "tinyfish", Tier.T3, "agent_step", "paid_automation"),
    Capability("browser_session", "tinyfish", Tier.T3, "session_minute", "paid_automation"),
    Capability("profile_read", "tinyfish", Tier.T4, "session", "authenticated_read"),
    Capability("monitor", "tinyfish", Tier.T4, "monitor_run", "recurring"),
))


@dataclass(frozen=True)
class Decision:
    """A policy answer. ``allowed`` is the whole answer; the rest is why."""

    allowed: bool
    code: str
    capability: Capability | None
    tier: Tier | None
    required_grant_tier: Tier | None
    notes: tuple[str, ...] = ()


def policy_decision(registry: CapabilityRegistry, provider: str, capability_id: str,
                    *, transport: str = "") -> Decision:
    """May this capability run at all, before any budget or grant is considered.

    Money is not consulted here: the ledger owns caps, and duplicating that check
    in two places is how the two drift apart. This answers only "is this a
    reviewed capability, and is its default posture open".
    """
    capability = registry.get(provider, capability_id)
    if capability is None:
        return Decision(False, "UNREVIEWED_CAPABILITY", None, None, None,
                        ("an upstream tool name is not a reviewed capability",))
    if transport and not capability.allows_transport(transport):
        return Decision(False, "TRANSPORT_NOT_REVIEWED", capability, capability.tier, None,
                        (f"{transport} is not an allowed transport for this capability",))
    if capability.tier.needs_grant:
        return Decision(False, "GRANT_REQUIRED", capability, capability.tier, capability.tier,
                        ("this tier is off until a grant for it is presented and verified",))
    return Decision(True, "OK", capability, capability.tier, None,
                    ("subject to the run and project ceilings in the usage ledger",))


class GrantRefused(ValueError):
    """A refusal with a stable code. The code is what callers branch on."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
# Wallet preconditions for T3
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class WalletObservation:
    """What a host reported about a prepaid wallet. Never trusted as authentication.

    ``auto_reload_enabled`` is the load-bearing field: with auto-reload on, the
    wallet balance is not a ceiling, because the wallet refills itself. A missing
    observation is not "no auto-reload" — it is an unknown, and an unknown blocks.
    """

    auto_reload_enabled: bool | None
    balance_micros: int | None
    observed_at: str
    source: str = "host_reported"


@dataclass(frozen=True)
class Preconditions:
    satisfied: bool
    blockers: tuple[str, ...]
    observations: tuple[str, ...]
    disclosure: str


def paid_agent_preconditions(wallet: WalletObservation | None, *, grant: Grant | None,
                             run_has_unknown: bool) -> Preconditions:
    """The T3 conditions from the architecture review, evaluated honestly.

    The one thing this can force is the *precondition*: a dedicated wallet with
    auto-reload off and a balance at or below the granted amount. It cannot force
    a per-run amount, because the upstream tool has no step or amount parameter —
    so the worst case is stated as the wallet balance rather than dressed up as a
    run cap.
    """
    blockers: list[str] = []
    observations: list[str] = []
    if grant is None:
        blockers.append("T3_GRANT_REQUIRED")
    elif grant.tier < Tier.T3:
        blockers.append("GRANT_TIER_TOO_LOW")
    if wallet is None:
        blockers.append("WALLET_NOT_OBSERVED")
    else:
        if wallet.auto_reload_enabled is None:
            blockers.append("AUTO_RELOAD_STATE_UNKNOWN")
        elif wallet.auto_reload_enabled:
            blockers.append("AUTO_RELOAD_ENABLED_NOT_A_CEILING")
        else:
            observations.append("auto_reload_off")
        if wallet.balance_micros is None:
            blockers.append("BALANCE_UNKNOWN")
        elif grant is not None and wallet.balance_micros > grant.max_micros:
            blockers.append("BALANCE_ABOVE_GRANTED_AMOUNT")
        else:
            observations.append("balance_within_granted_amount")
    if run_has_unknown:
        blockers.append("RUN_HAS_UNRECONCILED_UNKNOWN_CHARGE")
    return Preconditions(
        satisfied=not blockers,
        blockers=tuple(blockers),
        observations=tuple(observations),
        disclosure=(
            "Satisfied means the wallet preconditions were reported as met. It does not mean a "
            "per-run dollar ceiling exists: this upstream exposes no step or amount bound, so the "
            "worst case equals the wallet balance."
        ),
    )


# --------------------------------------------------------------------------- #
# Grants
# --------------------------------------------------------------------------- #

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def canonical_payload(*, grant_id: str, tier: Tier, project: str, run_id: str,
                      provider: str, capability: str, site: str, account_ref: str,
                      max_micros: int, max_calls: int, issued_at: str, expires_at: str,
                      nonce: str, issued_by: str) -> bytes:
    """The exact bytes a signature covers.

    Every field is in it. A grant that did not cover, say, ``run_id`` could be
    replayed into another run — the signature would still verify, which is exactly
    the failure a partial payload produces.
    """
    return json.dumps({
        "scheme": TOKEN_SCHEME, "grant_id": grant_id, "tier": int(tier),
        "project": project, "run_id": run_id, "provider": provider,
        "capability": capability, "site": site, "account_ref": account_ref,
        "max_micros": max_micros, "max_calls": max_calls, "issued_at": issued_at,
        "expires_at": expires_at, "nonce": nonce, "issued_by": issued_by,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


@dataclass(frozen=True)
class Grant:
    """A signed statement that a named person allowed this scope for this long."""

    grant_id: str
    tier: Tier
    project: str
    run_id: str
    provider: str
    capability: str
    site: str
    account_ref: str
    max_micros: int
    max_calls: int
    issued_at: str
    expires_at: str
    nonce: str
    issued_by: str
    signature: str

    def payload(self) -> bytes:
        return canonical_payload(**{name: getattr(self, name) for name in (
            "grant_id", "tier", "project", "run_id", "provider", "capability", "site",
            "account_ref", "max_micros", "max_calls", "issued_at", "expires_at",
            "nonce", "issued_by")})

    def token(self) -> str:
        return f"{TOKEN_SCHEME}.{_b64(self.payload())}.{self.signature}"

    def is_expired(self, now: datetime) -> bool:
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except ValueError:
            return True  # an unreadable expiry is an expired grant, not a lasting one
        if expiry.tzinfo is None:
            return True
        return now >= expiry

    def covers(self, *, tier: Tier, project: str, run_id: str, provider: str,
               capability: str, account_ref: str, site: str = "") -> tuple[bool, str]:
        """Empty scope fields mean "not narrowed" - except ``site`` at T4.

        For a T4 operation the comparison is two-way: the grant must name the site
        and the operation must name the same one. Letting an unnamed grant cover
        every site would make the tier's whole point optional.
        """
        if tier > self.tier:
            return False, "GRANT_TIER_TOO_LOW"
        if tier >= Tier.T4:
            if not site:
                return False, "GRANT_SITE_REQUIRED"
            if not self.site:
                return False, "GRANT_SITE_REQUIRED"
            if self.site != site:
                return False, "GRANT_SCOPE_MISMATCH_SITE"
        for name, wanted, actual in (("project", self.project, project),
                                     ("run_id", self.run_id, run_id),
                                     ("provider", self.provider, provider),
                                     ("capability", self.capability, capability),
                                     ("account_ref", self.account_ref, account_ref)):
            if wanted and wanted != actual:
                return False, f"GRANT_SCOPE_MISMATCH_{name.upper()}"
        return True, ""


def parse_token(token: str) -> Grant:
    """Read a token without verifying it. The result is untrusted until verified."""
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != TOKEN_SCHEME:
        raise GrantRefused("GRANT_MALFORMED", "not a MarketOps grant token")
    try:
        payload = json.loads(_unb64(parts[1]).decode("utf-8"))
        return Grant(
            grant_id=str(payload["grant_id"]), tier=Tier(int(payload["tier"])),
            project=str(payload["project"]), run_id=str(payload["run_id"]),
            provider=str(payload["provider"]), capability=str(payload["capability"]),
            site=str(payload["site"]), account_ref=str(payload["account_ref"]),
            max_micros=int(payload["max_micros"]), max_calls=int(payload["max_calls"]),
            issued_at=str(payload["issued_at"]), expires_at=str(payload["expires_at"]),
            nonce=str(payload["nonce"]), issued_by=str(payload["issued_by"]),
            signature=parts[2],
        )
    except GrantRefused:
        raise
    except (KeyError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise GrantRefused("GRANT_MALFORMED", "grant token did not decode") from None


class GrantAuthority:
    """Verifies grants against a public key it was given out of band.

    The plugin has no signing capability here on purpose: a component that can both
    verify and mint an authority has no authority boundary at all, only a policy.
    """

    def __init__(self, public_key=None, *, source: str = "not_configured"):
        self._public_key = public_key
        self.source = source

    @property
    def available(self) -> bool:
        return self._public_key is not None

    @classmethod
    def from_environment(cls, environ: dict | None = None) -> GrantAuthority:
        """Read the verification key from ``MARKETOPS_GRANT_PUBLIC_KEY`` (a path).

        Absent, blank or a template placeholder are all "not configured" — the same
        rule the data root uses, and for the same reason: a literal path named
        ``${VAR}`` must never be read as a file.
        """
        env = os.environ if environ is None else environ
        raw = (env.get("MARKETOPS_GRANT_PUBLIC_KEY") or "").strip()
        if not raw or re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*\}", raw):
            return cls(None, source="not_configured")
        path = Path(raw).expanduser()
        try:
            key = load_public_key(path.read_bytes())
        except OSError:
            return cls(None, source=f"unreadable:{path.name}")
        except GrantRefused:
            return cls(None, source=f"invalid:{path.name}")
        return cls(key, source=f"file:{path.name}")

    def verify(self, token: str, *, project: str, run_id: str, provider: str,
               capability: str, account_ref: str, tier: Tier, site: str = "",
               now: datetime | None = None) -> Grant:
        """Verify and scope-check, or raise :class:`GrantRefused`."""
        if not self.available:
            raise GrantRefused(
                "GRANT_VERIFICATION_UNAVAILABLE",
                "no grant verification key is configured, so no grant can be accepted; "
                "T3/T4 stay closed rather than trusting a locally produced token",
            )
        grant = parse_token(token)
        if not _verify_signature(self._public_key, grant.payload(), grant.signature):
            raise GrantRefused("GRANT_SIGNATURE_INVALID", "grant signature did not verify")
        moment = now or datetime.now(timezone.utc)
        if grant.is_expired(moment):
            raise GrantRefused("GRANT_EXPIRED", "grant is expired or has no usable expiry")
        covered, code = grant.covers(tier=tier, project=project, run_id=run_id,
                                     provider=provider, capability=capability,
                                     account_ref=account_ref, site=site)
        if not covered:
            raise GrantRefused(code, "grant does not cover this operation")
        return grant


def authority_boundary(*, verification_key_configured: bool = False) -> dict:
    """What a grant is worth here, in one answer that cannot be half-quoted."""
    return {
        "grant_forgeable_by_same_os_user": True,
        "verification_key_configured": verification_key_configured,
        "t3_t4_available": verification_key_configured,
        "disclosure": (
            "A process running as the same OS user can read what that user can read and can "
            "allocate a pseudo-terminal, so on a same-user deployment this signature records "
            "operator intent; it is NOT an isolation boundary. Hard enforcement requires the "
            "signing key behind a separate OS identity or an authorizer service."
        ),
        "enforceable_without_isolation": [
            "vendor-side prepared wallet with auto-reload disabled",
            "vendor plan quotas",
            "host tool-approval prompt",
        ],
        "not_claimed": [
            "per-run dollar guarantee where the upstream has no step or amount bound",
            "cryptographic separation from a shell-capable same-user agent",
            "authentication or quota correctness of any provider account",
        ],
    }


# --------------------------------------------------------------------------- #
# Signing (CLI only) and verification primitives
# --------------------------------------------------------------------------- #

def _load_private_key(raw: bytes):
    """A PEM private key, or base64 of 32 raw Ed25519 seed bytes."""
    ed25519 = _load_crypto()
    text = raw.decode("utf-8", "replace").strip()
    if "BEGIN PRIVATE KEY" in text:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        key = load_pem_private_key(text.encode("utf-8"), password=None)
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise GrantRefused("GRANT_KEY_UNSUPPORTED", "private key is not Ed25519")
        return key
    try:
        return ed25519.Ed25519PrivateKey.from_private_bytes(_unb64(text))
    except Exception:
        raise GrantRefused("GRANT_KEY_UNSUPPORTED",
                           "private key is neither PEM nor base64 Ed25519") from None


def public_pem_from_private(raw: bytes) -> bytes:
    """The verification half of a signing key, so it can be handed over separately."""
    from cryptography.hazmat.primitives import serialization
    return _load_private_key(raw).public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _load_crypto():
    """Import the optional signature library, or say plainly that it is absent."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError:
        raise GrantRefused(
            "CRYPTO_UNAVAILABLE",
            "Ed25519 support requires the optional 'cryptography' package; without it no grant "
            "can be verified, so T3/T4 remain closed",
        ) from None
    return ed25519


def load_public_key(raw: bytes):
    """A PEM public key, or base64 of 32 raw Ed25519 bytes."""
    ed25519 = _load_crypto()
    text = raw.decode("utf-8", "replace").strip()
    if "BEGIN PUBLIC KEY" in text:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        key = load_pem_public_key(text.encode("utf-8"))
        if not isinstance(key, ed25519.Ed25519PublicKey):
            raise GrantRefused("GRANT_KEY_UNSUPPORTED", "public key is not Ed25519")
        return key
    try:
        return ed25519.Ed25519PublicKey.from_public_bytes(_unb64(text))
    except Exception:
        raise GrantRefused("GRANT_KEY_UNSUPPORTED",
                           "public key is neither PEM nor base64 Ed25519") from None


def _verify_signature(public_key, payload: bytes, signature: str) -> bool:
    from cryptography.exceptions import InvalidSignature
    try:
        public_key.verify(_unb64(signature), payload)
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def mint_grant(*, private_key_bytes: bytes, tier: Tier, project: str, run_id: str = "",
               provider: str = "", capability: str = "", site: str = "", account_ref: str = "",
               max_micros: int = 0, max_calls: int = 0, ttl_seconds: int = 3600,
               issued_by: str, now: datetime | None = None, nonce: str = "") -> Grant:
    """Sign a grant. Called by the CLI below, never from an MCP tool.

    Scope fields left empty mean "not narrowed by this grant". A grant that narrows
    nothing is a deliberate, visible choice; it is not the default this function
    invents.
    """
    if not issued_by or not ID.fullmatch(issued_by):
        raise GrantRefused("GRANT_ISSUER_REQUIRED", "a grant must name who issued it")
    if not ID.fullmatch(project):
        raise GrantRefused("GRANT_SCOPE_REQUIRED", "a grant must name its project")
    if tier >= Tier.T4 and not site:
        # T4 is the tier for authenticated and state-changing work. A T4 grant that
        # names no site authorizes every site, which is the opposite of what the
        # tier exists for - so the site is required here rather than left to the
        # caller to remember.
        raise GrantRefused(
            "GRANT_SITE_REQUIRED",
            "a T4 grant must name the site it authorizes; an unnamed site scope would cover all "
            "of them")
    if ttl_seconds <= 0 or ttl_seconds > 30 * 24 * 3600:
        raise GrantRefused("GRANT_TTL_OUT_OF_RANGE", "grant lifetime must be 1s..30d")
    if max_micros < 0 or max_calls < 0:
        raise GrantRefused("GRANT_LIMIT_INVALID", "grant limits cannot be negative")
    moment = now or datetime.now(timezone.utc)
    key = _load_private_key(private_key_bytes)
    grant_id = "gnt-" + sha256(
        f"{project}|{provider}|{capability}|{site}|{moment.isoformat()}|{nonce}".encode()
    ).hexdigest()[:24]
    draft = Grant(
        grant_id=grant_id, tier=tier, project=project, run_id=run_id, provider=provider,
        capability=capability, site=site, account_ref=account_ref, max_micros=max_micros,
        max_calls=max_calls, issued_at=moment.isoformat(),
        expires_at=(moment + timedelta(seconds=ttl_seconds)).isoformat(), nonce=nonce,
        issued_by=issued_by, signature="",
    )
    signature = _b64(key.sign(draft.payload()))
    return Grant(**{**draft.__dict__, "signature": signature})


# --------------------------------------------------------------------------- #
# CLI: the only minting path, and it is not an MCP tool
# --------------------------------------------------------------------------- #

def _refuse_inside_managed_process() -> None:
    """The managed MCP process must not be able to mint, even if it has a key.

    This is a code-level separation, not a security boundary: an agent with a shell
    can start a fresh process without this marker. It is here so that the *product*
    never presents minting as an available capability, and so that the honest
    limitation in :func:`authority_boundary` is the only claim made.
    """
    if os.environ.get("MARKETOPS_MCP_PROCESS") == "1":
        raise GrantRefused("GRANT_MINT_REFUSED_IN_MCP",
                           "grants are minted by an operator CLI, never by the MCP surface")


def _assert_key_outside_checkout(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if any((part / ".git").exists() for part in (resolved.parent, *resolved.parents)):
        raise GrantRefused("GRANT_KEY_INSIDE_CHECKOUT",
                           "refusing to sign with a key stored inside a Git checkout")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="research_grants",
        description="Mint or inspect a MarketOps grant. Operator CLI; no MCP tool mints grants.")
    sub = parser.add_subparsers(dest="command", required=True)

    mint = sub.add_parser("mint", help="sign a grant with an operator-held Ed25519 key")
    mint.add_argument("--key", required=True, help="path to the Ed25519 private key")
    mint.add_argument("--public-key-out", help="where to write the matching public key (PEM)")
    mint.add_argument("--tier", required=True, choices=[f"T{index}" for index in range(5)])
    mint.add_argument("--project", required=True)
    mint.add_argument("--run-id", default="")
    mint.add_argument("--provider", default="")
    mint.add_argument("--capability", default="")
    mint.add_argument("--site", default="")
    mint.add_argument("--account-ref", default="")
    mint.add_argument("--max-usd", default="0", help="granted ceiling in USD")
    mint.add_argument("--max-calls", type=int, default=0)
    mint.add_argument("--ttl-seconds", type=int, default=3600)
    mint.add_argument("--issued-by", required=True)

    sub.add_parser("boundary", help="print what a grant is worth in this deployment")

    args = parser.parse_args(argv)
    try:
        if args.command == "boundary":
            print(json.dumps(authority_boundary(
                verification_key_configured=GrantAuthority.from_environment().available),
                indent=2))
            return 0
        return _mint_cli(args)
    except GrantRefused as exc:
        print(json.dumps({"status": "REFUSED", "code": exc.code, "message": exc.message}))
        return 2


def _mint_cli(args) -> int:
    """Mint, always with a human in the loop.

    There is deliberately no ``--yes``. A flag that skips the terminal check and the
    typed confirmation would make "minting requires a TTY" a sentence the code does
    not keep, and an unattended path to a signed grant is the one thing this CLI
    exists to not have. A caller that needs one should hold the signing key behind a
    separate identity instead - which is a real boundary, unlike a flag.
    """
    _refuse_inside_managed_process()
    if not sys.stdin.isatty():
        raise GrantRefused(
            "INTERACTIVE_CONFIRMATION_REQUIRED",
            "minting needs a terminal so a human sees the scope before it is signed",
        )
    key_path = Path(args.key)
    _assert_key_outside_checkout(key_path)
    try:
        raw = key_path.read_bytes()
    except OSError:
        raise GrantRefused("GRANT_KEY_UNREADABLE", "the signing key could not be read") from None
    from decimal import Decimal, InvalidOperation
    try:
        max_micros = int((Decimal(args.max_usd) * 1_000_000).to_integral_value())
        if max_micros < 0:
            raise InvalidOperation()
    except (InvalidOperation, ValueError):
        raise GrantRefused("GRANT_LIMIT_INVALID", "--max-usd must be a nonnegative amount")
    grant = mint_grant(private_key_bytes=raw, tier=Tier[args.tier], project=args.project,
                       run_id=args.run_id, provider=args.provider, capability=args.capability,
                       site=args.site, account_ref=args.account_ref, max_micros=max_micros,
                       max_calls=args.max_calls, ttl_seconds=args.ttl_seconds,
                       issued_by=args.issued_by)
    print(json.dumps(json.loads(grant.payload()), indent=2))
    if input("Sign this grant? type the grant id to confirm: ").strip() != grant.grant_id:
        raise GrantRefused("NOT_CONFIRMED", "confirmation did not match the grant id")
    if args.public_key_out:
        Path(args.public_key_out).write_bytes(public_pem_from_private(raw))
    result = {"status": "MINTED", "grant_id": grant.grant_id, "tier": f"T{int(grant.tier)}",
              "expires_at": grant.expires_at, "token": grant.token(),
              "note": "a grant records operator intent; see 'boundary' for what it enforces"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

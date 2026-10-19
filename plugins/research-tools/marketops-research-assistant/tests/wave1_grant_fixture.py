"""An offline, self-minting T3 grant for tests that exercise the paid path.

The shared ledger closes tier T3 and T4 unless the caller presents a grant that
verifies against a public key configured out of band
(``research_grants.GrantAuthority``). That is the point of the tier: the switch
that used to open this path was a process environment variable, and an Agent on
the same OS identity can set one of those.

A test that wants to reach the *code behind* that gate therefore has to present a
real grant. This module mints one with a throwaway Ed25519 key generated in the
test process, installs only the **public** half where the authority reads it, and
returns the signed token.

This does not weaken the boundary, and it is worth being precise about why: the
private key is created and discarded inside the test process, so it authorizes
nothing outside it. It is a fixture that says "an operator has authorized this",
which is exactly what the gate is asking about. A production caller still needs a
key held by a separate identity — ``research_grants``' CLI refuses to mint
non-interactively for that reason, and this module is not on that path.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

ISSUED_BY = "wave1-test-fixture"


def _keypair() -> tuple[bytes, bytes]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key = ed25519.Ed25519PrivateKey.generate()
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    return private, public


def install_authority(directory: str | Path | None = None) -> Path:
    """Publish a fresh public key where ``GrantAuthority.from_environment`` reads it."""
    _private, public = _keypair()
    folder = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="wave1-grant-"))
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "grant-public.pem"
    path.write_bytes(public)
    os.environ["MARKETOPS_GRANT_PUBLIC_KEY"] = str(path)
    return path


def _token(*, root: str | Path, run_id: str, provider: str, capability: str,
           account_ref: str, private: bytes, max_micros: int, max_calls: int) -> str:
    import research_grants as grants
    import research_usage_ledger as ledger

    grant = grants.mint_grant(
        private_key_bytes=private, tier=grants.Tier.T3,
        project=ledger.project_for_root(root), run_id=run_id, provider=provider,
        capability=capability, account_ref=account_ref,
        max_micros=max_micros, max_calls=max_calls, issued_by=ISSUED_BY)
    return grant.token()


def mint_for(root: str | Path, *, run_id: str, provider: str, capability: str,
             account_ref: str, max_micros: int = 1_000_000,
             max_calls: int = 10, directory: str | Path | None = None) -> str:
    """Install a fresh authority and return one T3 token scoped to this operation."""
    _private, public = _keypair()
    folder = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="wave1-grant-"))
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "grant-public.pem"
    path.write_bytes(public)
    os.environ["MARKETOPS_GRANT_PUBLIC_KEY"] = str(path)
    return _token(root=root, run_id=run_id, provider=provider, capability=capability,
                  account_ref=account_ref, private=_private,
                  max_micros=max_micros, max_calls=max_calls)

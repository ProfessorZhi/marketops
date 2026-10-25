"""Offline tests for the capability registry, the T0-T4 tiers and grant authority.

No network, no credential, no provider call. The one test that must pass in *both*
worlds - with and without the optional signature library - is
``test_without_a_verification_key_t3_is_unavailable_not_trusted``: whichever way
the environment is missing something, T3/T4 close.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_grants as grants

CRYPTO = importlib.util.find_spec("cryptography") is not None
NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
needs_crypto = unittest.skipUnless(
    CRYPTO, "Ed25519 signing and verification need the optional cryptography package")


def keypair():
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


def minted_with(private: bytes, **overrides):
    fields = dict(tier=grants.Tier.T3, project="devon", run_id="run-01",
                  provider="tinyfish", capability="agent_run", account_ref="main",
                  max_micros=5_000_000, max_calls=1, ttl_seconds=3600,
                  issued_by="operator-member-b", now=NOW)
    fields.update(overrides)
    return grants.mint_grant(private_key_bytes=private, **fields)


def minted(**overrides):
    """A grant signed by a key nobody holds - good enough for shape, not for verifying."""
    return minted_with(keypair()[0], **overrides)


class FakeStdin:
    """A stdin the test controls, so a TTY prompt can never block the suite."""

    def __init__(self, *, tty: bool = False, answer: str = ""):
        self._tty = tty
        self._answer = answer

    def isatty(self) -> bool:
        return self._tty

    def readline(self) -> str:  # pragma: no cover - only reached if isatty lies
        return ""


class TestRegistryAndTiers(unittest.TestCase):
    def test_an_unreviewed_capability_is_denied_rather_than_classified(self):
        decision = grants.policy_decision(grants.DEFAULT_REGISTRY, "tinyfish", "brand_new_tool")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "UNREVIEWED_CAPABILITY")
        with self.assertRaises(grants.GrantRefused) as ctx:
            grants.DEFAULT_REGISTRY.require("tinyfish", "brand_new_tool")
        self.assertEqual(ctx.exception.code, "UNREVIEWED_CAPABILITY")

    def test_a_reviewed_read_only_capability_is_open_and_a_paid_one_is_not(self):
        cheap = grants.policy_decision(grants.DEFAULT_REGISTRY, "serper", "search")
        self.assertTrue(cheap.allowed)
        self.assertEqual(cheap.tier, grants.Tier.T2)
        # The public-page Agent is a T2 paid read under the owner's local consent
        # (2026-10-25); a browser session that outlives one call is still T3.
        agent = grants.policy_decision(grants.DEFAULT_REGISTRY, "tinyfish", "agent_run")
        self.assertEqual(agent.tier, grants.Tier.T2)
        paid = grants.policy_decision(grants.DEFAULT_REGISTRY, "tinyfish", "browser_session")
        self.assertFalse(paid.allowed)
        self.assertEqual(paid.code, "GRANT_REQUIRED")
        self.assertEqual(paid.required_grant_tier, grants.Tier.T3)
        site = grants.policy_decision(grants.DEFAULT_REGISTRY, "tinyfish", "profile_read")
        self.assertEqual(site.required_grant_tier, grants.Tier.T4)

    def test_only_the_zero_and_one_and_two_tiers_are_open_by_default(self):
        self.assertTrue(grants.Tier.T0.default_open)
        self.assertTrue(grants.Tier.T1.default_open)
        self.assertTrue(grants.Tier.T2.default_open)
        self.assertFalse(grants.Tier.T3.default_open)
        self.assertFalse(grants.Tier.T4.default_open)
        # The tier answers four questions; the effective one is the highest.
        self.assertGreater(grants.Tier.T4, grants.Tier.T3)

    def test_a_transport_outside_the_allowlist_is_refused(self):
        registry = grants.CapabilityRegistry((
            grants.Capability("search", "serpapi", grants.Tier.T2, "search_query",
                              "read_only", allowed_transports=("embedded_official_mcp",)),
        ))
        self.assertTrue(registry.require("serpapi", "search").allows_transport(
            "embedded_official_mcp"))
        decision = grants.policy_decision(registry, "serpapi", "search", transport="rest_https")
        self.assertEqual(decision.code, "TRANSPORT_NOT_REVIEWED")
        self.assertFalse(decision.allowed)

    def test_a_capability_cannot_be_registered_twice_for_one_provider(self):
        entry = grants.Capability("search", "serper", grants.Tier.T2, "search_query", "read_only")
        with self.assertRaises(ValueError):
            grants.CapabilityRegistry((entry, entry))

    def test_a_malformed_capability_is_refused(self):
        for bad in ({"capability_id": "has space"}, {"provider": ""}, {"tier": 2}):
            with self.subTest(bad=bad):
                fields = {"capability_id": "search", "provider": "serper",
                          "tier": grants.Tier.T2, "meter": "search_query",
                          "effect": "read_only"}
                fields.update(bad)
                with self.assertRaises(ValueError):
                    grants.Capability(**fields)


class TestGrantPayload(unittest.TestCase):
    BASE = dict(grant_id="gnt-1", tier=grants.Tier.T3, project="devon", run_id="run-01",
                provider="tinyfish", capability="agent_run", site="", account_ref="main",
                max_micros=5_000_000, max_calls=1, issued_at="2026-10-17T09:00:00+00:00",
                expires_at="2026-10-17T10:00:00+00:00", nonce="n-1", issued_by="operator-member-b")

    @staticmethod
    def _changed(name, value):
        """A different-but-valid value for every field, without changing its type."""
        if isinstance(value, grants.Tier):
            return grants.Tier.T4 if value is not grants.Tier.T4 else grants.Tier.T3
        if isinstance(value, bool):
            return not value
        if isinstance(value, int):
            return value + 1
        return "different-" + name

    def test_every_field_of_a_grant_is_inside_the_signature(self):
        base = grants.canonical_payload(**self.BASE)
        for name, value in self.BASE.items():
            with self.subTest(field=name):
                changed = dict(self.BASE, **{name: self._changed(name, value)})
                self.assertNotEqual(base, grants.canonical_payload(**changed))
        # ...including a run id, whose omission would let a grant be replayed elsewhere.
        other = dict(self.BASE, run_id="run-02")
        self.assertNotEqual(base, grants.canonical_payload(**other))

    def test_the_payload_is_stable_and_ascii(self):
        first = grants.canonical_payload(**self.BASE)
        self.assertEqual(first, grants.canonical_payload(**self.BASE))
        self.assertEqual(first, first.decode("ascii").encode("ascii"))
        self.assertEqual(json.loads(first)["scheme"], grants.TOKEN_SCHEME)

    def test_a_grant_token_that_cannot_be_parsed_is_refused(self):
        for token in ("", "not-a-token", "marketops-grant-v1.only-two-parts",
                      "other-scheme.abc.def"):
            with self.subTest(token=token):
                with self.assertRaises(grants.GrantRefused) as ctx:
                    grants.parse_token(token)
                self.assertEqual(ctx.exception.code, "GRANT_MALFORMED")


class TestAuthorityBoundary(unittest.TestCase):
    def test_t3_is_unavailable_without_a_verification_key_and_says_so(self):
        authority = grants.GrantAuthority(None, source="not_configured")
        self.assertFalse(authority.available)
        with self.assertRaises(grants.GrantRefused) as ctx:
            authority.verify("marketops-grant-v1.abc.def", project="devon", run_id="run-01",
                             provider="tinyfish", capability="agent_run",
                             account_ref="main", tier=grants.Tier.T3, now=NOW)
        self.assertEqual(ctx.exception.code, "GRANT_VERIFICATION_UNAVAILABLE")

    def test_an_unconfigured_environment_never_invents_a_key(self):
        for environ in ({}, {"MARKETOPS_GRANT_PUBLIC_KEY": ""},
                        {"MARKETOPS_GRANT_PUBLIC_KEY": "   "},
                        {"MARKETOPS_GRANT_PUBLIC_KEY": "${MARKETOPS_GRANT_PUBLIC_KEY}"}):
            with self.subTest(environ=environ):
                authority = grants.GrantAuthority.from_environment(environ)
                self.assertFalse(authority.available)
                self.assertEqual(authority.source, "not_configured")

    def test_an_unreadable_or_wrong_key_file_is_not_configured_rather_than_fatal(self):
        authority = grants.GrantAuthority.from_environment(
            {"MARKETOPS_GRANT_PUBLIC_KEY": "/nonexistent/verification-key.pem"})
        self.assertFalse(authority.available)
        self.assertTrue(authority.source.startswith("unreadable:"))

    def test_the_boundary_statement_cannot_be_read_as_an_isolation_claim(self):
        boundary = grants.authority_boundary(verification_key_configured=False)
        self.assertTrue(boundary["grant_forgeable_by_same_os_user"])
        self.assertFalse(boundary["t3_t4_available"])
        self.assertIn("NOT an isolation boundary", boundary["disclosure"])
        self.assertIn("pseudo-terminal", boundary["disclosure"])
        self.assertTrue(any("wallet" in item for item in boundary["enforceable_without_isolation"]))
        self.assertTrue(any("per-run dollar" in item for item in boundary["not_claimed"]))


@needs_crypto
class TestWalletPreconditions(unittest.TestCase):
    def test_a_wallet_with_auto_reload_off_inside_the_grant_is_acceptable(self):
        result = grants.paid_agent_preconditions(
            grants.WalletObservation(auto_reload_enabled=False, balance_micros=1_000_000,
                                     observed_at=NOW.isoformat()),
            grant=minted(), run_has_unknown=False)
        self.assertTrue(result.satisfied, result.blockers)
        self.assertIn("auto_reload_off", result.observations)
        self.assertIn("wallet balance", result.disclosure)

    def test_each_unsafe_wallet_state_blocks_with_its_own_reason(self):
        grant = minted()
        cases = (
            (None, False, "WALLET_NOT_OBSERVED"),
            (grants.WalletObservation(True, 1_000_000, NOW.isoformat()), False,
             "AUTO_RELOAD_ENABLED_NOT_A_CEILING"),
            (grants.WalletObservation(None, 1_000_000, NOW.isoformat()), False,
             "AUTO_RELOAD_STATE_UNKNOWN"),
            (grants.WalletObservation(False, None, NOW.isoformat()), False, "BALANCE_UNKNOWN"),
            (grants.WalletObservation(False, 9_000_000, NOW.isoformat()), False,
             "BALANCE_ABOVE_GRANTED_AMOUNT"),
            (grants.WalletObservation(False, 1_000_000, NOW.isoformat()), True,
             "RUN_HAS_UNRECONCILED_UNKNOWN_CHARGE"),
        )
        for wallet, has_unknown, expected in cases:
            with self.subTest(expected=expected):
                result = grants.paid_agent_preconditions(
                    wallet, grant=grant, run_has_unknown=has_unknown)
                self.assertFalse(result.satisfied)
                self.assertIn(expected, result.blockers)


@needs_crypto
class TestGrantRoundTrip(unittest.TestCase):
    def setUp(self):
        self.private, self.public = keypair()
        self.authority = grants.GrantAuthority(grants.load_public_key(self.public))

    def mint(self, **overrides):
        return minted_with(self.private, **overrides)

    def test_a_grant_minted_for_this_scope_verifies(self):
        grant = self.mint()
        verified = self.authority.verify(grant.token(), project="devon", run_id="run-01",
                                         provider="tinyfish", capability="agent_run",
                                         account_ref="main", tier=grants.Tier.T3, now=NOW)
        self.assertEqual(verified.grant_id, grant.grant_id)
        self.assertEqual(verified.max_micros, 5_000_000)

    def test_the_same_mint_helper_signs_with_its_own_key(self):
        # A token signed by another key must not verify against this authority.
        with self.assertRaises(grants.GrantRefused) as ctx:
            self.authority.verify(minted().token(), project="devon", run_id="run-01",
                                  provider="tinyfish", capability="agent_run",
                                  account_ref="main", tier=grants.Tier.T3, now=NOW)
        self.assertEqual(ctx.exception.code, "GRANT_SIGNATURE_INVALID")

    def test_a_scope_the_grant_does_not_name_is_refused(self):
        grant = self.mint()
        cases = (
            ({"project": "other"}, "GRANT_SCOPE_MISMATCH_PROJECT"),
            ({"run_id": "run-02"}, "GRANT_SCOPE_MISMATCH_RUN_ID"),
            ({"provider": "serpapi"}, "GRANT_SCOPE_MISMATCH_PROVIDER"),
            ({"capability": "search"}, "GRANT_SCOPE_MISMATCH_CAPABILITY"),
            ({"account_ref": "other"}, "GRANT_SCOPE_MISMATCH_ACCOUNT_REF"),
        )
        for override, expected in cases:
            with self.subTest(expected=expected):
                scope = {"project": "devon", "run_id": "run-01", "provider": "tinyfish",
                         "capability": "agent_run", "account_ref": "main", **override}
                with self.assertRaises(grants.GrantRefused) as ctx:
                    self.authority.verify(grant.token(), tier=grants.Tier.T3, now=NOW, **scope)
                self.assertEqual(ctx.exception.code, expected)

    def test_an_operation_above_the_granted_tier_is_refused(self):
        grant = self.mint(tier=grants.Tier.T3)
        with self.assertRaises(grants.GrantRefused) as ctx:
            self.authority.verify(grant.token(), project="devon", run_id="run-01",
                                  provider="tinyfish", capability="profile_read",
                                  account_ref="main", tier=grants.Tier.T4, now=NOW)
        self.assertEqual(ctx.exception.code, "GRANT_TIER_TOO_LOW")

    def test_an_expired_grant_is_refused(self):
        grant = self.mint(ttl_seconds=60)
        later = NOW.replace(hour=10)
        with self.assertRaises(grants.GrantRefused) as ctx:
            self.authority.verify(grant.token(), project="devon", run_id="run-01",
                                  provider="tinyfish", capability="agent_run",
                                  account_ref="main", tier=grants.Tier.T3, now=later)
        self.assertEqual(ctx.exception.code, "GRANT_EXPIRED")

    def test_tampering_with_any_field_breaks_the_signature(self):
        grant = self.mint()
        scheme, encoded, signature = grant.token().split(".")
        payload = json.loads(grants._unb64(encoded).decode("utf-8"))
        payload["max_micros"] = 999_000_000
        forged = ".".join((scheme, grants._b64(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()), signature))
        with self.assertRaises(grants.GrantRefused) as ctx:
            self.authority.verify(forged, project="devon", run_id="run-01", provider="tinyfish",
                                  capability="agent_run", account_ref="main",
                                  tier=grants.Tier.T3, now=NOW)
        self.assertEqual(ctx.exception.code, "GRANT_SIGNATURE_INVALID")

    def test_a_site_scoped_grant_only_covers_that_site(self):
        grant = self.mint(tier=grants.Tier.T4, capability="profile_read", site="example-portal")
        for site in ("example-portal",):
            with self.subTest(site=site):
                verified = self.authority.verify(
                    grant.token(), project="devon", run_id="run-01", provider="tinyfish",
                    capability="profile_read", account_ref="main", tier=grants.Tier.T4,
                    site=site, now=NOW)
                self.assertEqual(verified.site, "example-portal")
        for site in ("another-portal", ""):
            with self.subTest(site=site):
                with self.assertRaises(grants.GrantRefused) as ctx:
                    self.authority.verify(
                        grant.token(), project="devon", run_id="run-01", provider="tinyfish",
                        capability="profile_read", account_ref="main", tier=grants.Tier.T4,
                        site=site, now=NOW)
                self.assertEqual(ctx.exception.code,
                                 "GRANT_SCOPE_MISMATCH_SITE" if site else "GRANT_SITE_REQUIRED")

    def test_a_t4_grant_must_name_its_site(self):
        # The tier exists for site-scoped work; a grant that names no site would
        # authorize every site, so minting one is refused rather than narrowed later.
        with self.assertRaises(grants.GrantRefused) as ctx:
            self.mint(tier=grants.Tier.T4, capability="profile_read", site="")
        self.assertEqual(ctx.exception.code, "GRANT_SITE_REQUIRED")
        # And a grant minted for a different tier keeps the narrow-nothing shape,
        # because nothing about T0-T3 is site-scoped.
        self.assertEqual(self.mint(tier=grants.Tier.T3).site, "")

    def test_a_public_key_that_is_not_a_public_key_is_refused(self):
        with self.assertRaises(grants.GrantRefused) as ctx:
            grants.load_public_key(self.private)  # an Ed25519 *private* key
        self.assertEqual(ctx.exception.code, "GRANT_KEY_UNSUPPORTED")


class TestMintingIsNotAnAgentCapability(unittest.TestCase):
    """The mint path is a CLI, refuses inside the managed process, and needs a human.

    The refusals that happen *before* key material is touched run in every
    environment; only the two tests that actually sign need the optional library.
    Keeping them apart is why this class does not build a keypair in ``setUp``: a
    suite that errors wholesale without ``cryptography`` would hide the fail-closed
    behaviour that has to work in exactly that environment.
    """

    def setUp(self):
        self.paths: list[str] = []
        self.private = self.public = b""
        if CRYPTO:
            self.private, self.public = keypair()

    def tearDown(self):
        for path in self.paths:
            Path(path).unlink(missing_ok=True)

    def key_file(self, material: bytes | None = None) -> str:
        import tempfile
        handle = tempfile.NamedTemporaryFile("wb", suffix=".pem", delete=False)
        handle.write(material if material is not None else self.private or b"not-a-key\n")
        handle.close()
        self.paths.append(handle.name)
        return handle.name

    def _run(self, argv, *, tty: bool, environ: dict | None = None, answer=None):
        import io
        import os
        from contextlib import redirect_stdout
        from unittest.mock import patch
        buffer = io.StringIO()
        with patch.dict(os.environ, environ or {"MARKETOPS_MCP_PROCESS": "0"}, clear=False):
            with patch.object(sys, "stdin", FakeStdin(tty=tty)):
                with redirect_stdout(buffer):
                    if answer is None:
                        code = grants.main(argv)
                    else:
                        with patch("builtins.input", side_effect=answer):
                            code = grants.main(argv)
        return code, buffer.getvalue()

    def test_minting_inside_the_managed_process_is_refused(self):
        code, output = self._run(
            ["mint", "--key", self.key_file(), "--tier", "T3", "--project", "devon",
             "--issued-by", "operator-member-b"],
            tty=True, environ={"MARKETOPS_MCP_PROCESS": "1"})
        self.assertEqual(code, 2)
        self.assertIn("GRANT_MINT_REFUSED_IN_MCP", output)

    def test_minting_without_a_terminal_is_refused(self):
        code, output = self._run(
            ["mint", "--key", self.key_file(), "--tier", "T3", "--project", "devon",
             "--issued-by", "operator-member-b"],
            tty=False)
        self.assertEqual(code, 2)
        self.assertIn("INTERACTIVE_CONFIRMATION_REQUIRED", output)

    def test_there_is_no_unattended_flag_that_skips_the_terminal(self):
        # A --yes would make "minting requires a TTY" a sentence the code does not
        # keep, so the flag does not exist and argparse rejects it.
        with self.assertRaises(SystemExit) as ctx:
            self._run(["mint", "--key", self.key_file(), "--tier", "T3", "--project", "devon",
                       "--issued-by", "operator-member-b", "--yes"], tty=False)
        self.assertEqual(ctx.exception.code, 2)

    def test_minting_nothing_of_a_key_never_echoes_the_key(self):
        code, output = self._run(
            ["mint", "--key", "/nonexistent/signing-key.pem", "--tier", "T3",
             "--project", "devon", "--issued-by", "operator-member-b"],
            tty=True)
        self.assertEqual(code, 2)
        self.assertIn("GRANT_KEY_UNREADABLE", output)
        self.assertNotIn("BEGIN PRIVATE KEY", output)

    @needs_crypto
    def test_a_signing_key_inside_a_git_checkout_is_refused(self):
        code, output = self._run(
            ["mint", "--key", str(ROOT / "scripts" / "research_grants.py"), "--tier", "T3",
             "--project", "devon", "--issued-by", "operator-member-b"],
            tty=True)
        self.assertEqual(code, 2)
        self.assertIn("GRANT_KEY_INSIDE_CHECKOUT", output)

    @needs_crypto
    def test_a_confirmation_that_does_not_match_the_grant_id_signs_nothing(self):
        code, output = self._run(
            ["mint", "--key", self.key_file(), "--tier", "T3", "--project", "devon",
             "--issued-by", "operator-member-b"],
            tty=True, answer=lambda prompt: "not-the-grant-id")
        self.assertEqual(code, 2)
        self.assertIn("NOT_CONFIRMED", output)

    @needs_crypto
    def test_a_confirmed_mint_produces_a_token_that_verifies(self):
        # The operator's actual first run: a key outside any checkout, a terminal, and
        # the grant id typed back after reading the scope that was printed.
        import re

        import io
        import os
        from contextlib import redirect_stdout
        from unittest.mock import patch
        buffer = io.StringIO()
        with patch.dict(os.environ, {"MARKETOPS_MCP_PROCESS": "0"}, clear=False):
            with patch.object(sys, "stdin", FakeStdin(tty=True)):
                def read_the_scope_and_type_its_id(prompt: str) -> str:
                    # The scope was printed before the prompt; the human reads it and
                    # types back the id of the grant they just looked at.
                    match = re.search(r'"grant_id":\s*"([^"]+)"', buffer.getvalue())
                    self.assertIsNotNone(match, buffer.getvalue())
                    return match.group(1)

                with patch("builtins.input", side_effect=read_the_scope_and_type_its_id):
                    with redirect_stdout(buffer):
                        code = grants.main(["mint", "--key", self.key_file(), "--tier", "T3",
                                            "--project", "devon", "--run-id", "run-01",
                                            "--issued-by", "operator-member-b"])
        self.assertEqual(code, 0, buffer.getvalue())
        payload = json.loads(buffer.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload["status"], "MINTED")
        verified = grants.GrantAuthority(grants.load_public_key(self.public)).verify(
            payload["token"], project="devon", run_id="run-01", provider="", capability="",
            account_ref="", tier=grants.Tier.T3)
        self.assertEqual(verified.grant_id, payload["grant_id"])

    def test_the_boundary_subcommand_reports_without_a_key(self):
        import io
        import os
        from contextlib import redirect_stdout
        from unittest.mock import patch
        buffer = io.StringIO()
        with patch.dict(os.environ, {"MARKETOPS_GRANT_PUBLIC_KEY": ""}, clear=False):
            with redirect_stdout(buffer):
                code = grants.main(["boundary"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertFalse(payload["t3_t4_available"])
        self.assertTrue(payload["grant_forgeable_by_same_os_user"])


if __name__ == "__main__":
    unittest.main()

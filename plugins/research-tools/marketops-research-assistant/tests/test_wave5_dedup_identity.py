"""CD-01..CD-03 against the real ledger: one operation, two transports.

The coordinator's decision (`CONTRACT-DECISION-DEDUP-IDENTITY.md`) settles two
things that have to be held at the same time, and this file asserts **both**:

* **CD-02a** — the *same* canonical operation issued over a second transport is one
  spend opportunity, and the second must be refused;
* **CD-02b** — a *different* query on the same provider must be **allowed**, because
  refusing it would be the "this project has touched this provider" degeneration the
  formula's own docstring warns about.

Only the pair is evidence. Asserting (a) alone would pass on a rule that refuses
everything of one provider; asserting (b) alone would pass on a ledger that never
deduplicates across transports at all.

**CD-03** is why this needed a change in the adapters rather than in the ledger: the
ledger keys the route refusal on a digest of `args`, so two adapters that each sent
their own private field names could never match however correct the ledger was. The
identity now comes from `search_canonical`, which is transport-free by construction.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_provider_extensions as ext  # noqa: E402
import research_usage_ledger as ledger_module  # noqa: E402
import search_canonical  # noqa: E402


def _args(query: str) -> dict:
    """The args both adapters hand the ledger, from D's shared builder."""
    return search_canonical.search_operation_args(
        query=query, capability="search", country="GB", language="en",
        mode="web", limit=10, page=1)


class SharedShapeTests(unittest.TestCase):
    def test_both_adapters_hand_the_ledger_the_same_key_names(self):
        """CD-03: the boundary's shape is shared; the values are what may differ."""
        from_dsm = _args("paint pool london")
        from_ext = ext._ledger_identity(
            "serpapi_mcp_search", query="",
            mcp_params={"params": {"engine": "google", "q": "paint pool london",
                                   "gl": "gb", "hl": "en", "num": 10}},
            fingerprint="an-adapter-local-digest")
        self.assertEqual(set(from_dsm), set(from_ext))
        self.assertEqual({"capability", "query", "params"}, set(from_dsm))

    def test_the_serpapi_mcp_identity_is_the_canonical_signature_not_a_private_digest(self):
        """The MCP side must key on what was asked, not on its own action name."""
        identity = ext._ledger_identity(
            "serpapi_mcp_search", query="",
            mcp_params={"params": {"engine": "google", "q": "  Paint   POOL london ",
                                   "gl": "GB", "hl": "en", "num": 10}},
            fingerprint="an-adapter-local-digest")
        self.assertEqual("search", identity["capability"])
        self.assertNotEqual("an-adapter-local-digest", identity["query"],
                            "the adapter's private digest leaked into the identity")

    def test_normalisation_is_stable_across_cosmetic_differences(self):
        """Whitespace, case of the locale and key order must not change the key."""
        left = ext._ledger_identity(
            "serpapi_mcp_search", query="",
            mcp_params={"params": {"engine": "google", "q": "paint pool london",
                                   "gl": "GB", "hl": "en", "num": 10}},
            fingerprint="a")
        right = ext._ledger_identity(
            "serpapi_mcp_search", query="",
            mcp_params={"params": {"num": 10, "hl": "en", "gl": "gb",
                                   "q": "  paint   pool   london ", "engine": "google"}},
            fingerprint="b")
        self.assertEqual(left["query"], right["query"])

    def test_an_action_without_canonical_arguments_does_not_pretend_to_share_one(self):
        identity = ext._ledger_identity("tinyfish_mcp_search", query="paint",
                                        mcp_params=None, fingerprint="local-digest")
        self.assertEqual({"capability", "query", "params"}, set(identity))
        self.assertEqual("local-digest", identity["query"])


class RealLedgerDedupTests(unittest.TestCase):
    def _ledger(self, root: Path):
        ledger = ledger_module.for_root(root)
        project = ledger_module.project_for_root(root)
        ledger.set_cap("run", f"{project}/runA", ceiling_calls=20, source="test-fixture")
        return ledger, project

    def _reserve(self, ledger, project, *, operation_id, transport, args):
        return ledger.reserve(ledger_module.OperationRequest(
            project=project, run_id="runA", operation_id=operation_id,
            provider="serpapi", capability="search", account_ref="serpapi",
            transport=transport, intent="i", args=args))

    def test_cd_02a_the_same_operation_over_a_second_transport_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger, project = self._ledger(Path(tmp))
            identity = _args("paint pool london")
            self._reserve(ledger, project, operation_id="rest-1",
                          transport="rest", args=identity)
            with self.assertRaises(ledger_module.LedgerRefused) as caught:
                self._reserve(ledger, project, operation_id="mcp-1",
                              transport="official_mcp", args=dict(identity))
            self.assertEqual("DUPLICATE_OPERATION_SIGNATURE", caught.exception.code)

    def test_cd_02b_a_different_query_on_the_same_provider_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger, project = self._ledger(Path(tmp))
            self._reserve(ledger, project, operation_id="rest-1", transport="rest",
                          args=_args("paint pool london"))
            # The anti-degradation half: same provider, same capability, one
            # different query, over a second transport. This MUST pass — a rule
            # that refuses it is "this project has touched this provider".
            self._reserve(ledger, project, operation_id="mcp-2",
                          transport="official_mcp",
                          args=_args("tiles birmingham"))
            self.assertEqual(2, len(ledger.operations()))

    def test_cd_04_an_unknown_charge_still_claims_the_route(self):
        """A dispatched call whose charge is unresolved still holds the exposure."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger, project = self._ledger(Path(tmp))
            identity = _args("paint pool london")
            self._reserve(ledger, project, operation_id="rest-1",
                          transport="rest", args=identity)
            ledger.mark_dispatch_intent("rest-1")
            ledger.mark_unknown("rest-1", reason="PROVIDER_TIMEOUT")
            with self.assertRaises(ledger_module.LedgerRefused) as caught:
                self._reserve(ledger, project, operation_id="mcp-1",
                              transport="official_mcp", args=dict(identity))
            self.assertEqual("DUPLICATE_OPERATION_SIGNATURE", caught.exception.code)


if __name__ == "__main__":
    unittest.main()

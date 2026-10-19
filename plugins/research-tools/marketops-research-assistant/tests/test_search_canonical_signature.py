"""One spend opportunity must have one key, whichever transport reaches it.

The convergence review's Key B is a provider execution fingerprint that is
independent of transport, so a Serper query sent over REST and the same query
sent through an embedded MCP collapse to a single reservation instead of
billing twice. These tests pin that collapse, and pin its two boundaries: a
different provider or different arguments must *not* collapse, and a malformed
argument set must be refused rather than hashed into something plausible.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import search_canonical as canonical


def args(**overrides):
    base = dict(provider="serper", mode="web", query="public sports venues Devon",
                country="GB", language="en", limit=10, page=1, engine=None,
                account="acct-1", transport="REST_HTTPS")
    base.update(overrides)
    return canonical.canonical_arguments(**base)


class OperationSignatureTests(unittest.TestCase):
    def test_same_arguments_over_two_transports_are_one_operation(self):
        rest = args(transport="REST_HTTPS")
        mcp = args(transport="EMBEDDED_OFFICIAL_MCP_HTTP")
        self.assertNotEqual(rest["transport"], mcp["transport"])
        self.assertEqual(canonical.operation_signature(rest),
                         canonical.operation_signature(mcp))

    def test_distinct_providers_or_arguments_stay_distinct_operations(self):
        baseline = canonical.operation_signature(args())
        for overrides in ({"provider": "serpapi"},
                          {"mode": "news"},
                          {"query": "public sports venues Cornwall"},
                          {"country": "IE"},
                          {"language": "ga"},
                          {"limit": 5},
                          {"page": 2},
                          {"engine": "google_news"},
                          {"account": "acct-2"}):
            with self.subTest(**overrides):
                self.assertNotEqual(
                    baseline,
                    canonical.operation_signature(args(**overrides)),
                )

    def test_query_whitespace_is_not_a_second_paid_call(self):
        padded = args(query="  public   sports venues Devon  ")
        self.assertEqual(canonical.operation_signature(args()),
                         canonical.operation_signature(padded))

    def test_signature_is_a_stable_hex_digest_of_the_closed_key_set(self):
        first = canonical.operation_signature(args())
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(first, canonical.operation_signature(args()))

    def test_the_key_set_is_closed(self):
        with self.assertRaisesRegex(ValueError, "CANONICAL_ARGUMENTS_UNKNOWN_FIELD"):
            canonical.operation_signature({**args(), "receipt_ref": "search-deadbeef"})
        incomplete = args()
        del incomplete["account"]
        with self.assertRaisesRegex(ValueError, "CANONICAL_ARGUMENTS_INCOMPLETE"):
            canonical.operation_signature(incomplete)

    def test_undeclared_provider_or_transport_is_refused(self):
        with self.assertRaisesRegex(ValueError, "CANONICAL_PROVIDER_UNSUPPORTED"):
            canonical.canonical_arguments(provider="google", mode="web", query="x y",
                                          country="GB", language="en", limit=5)
        with self.assertRaisesRegex(ValueError, "CANONICAL_TRANSPORT_UNSUPPORTED"):
            canonical.canonical_arguments(provider="serper", mode="web", query="x y",
                                          country="GB", language="en", limit=5,
                                          transport="SOME_PROXY")


class IntentSignatureTests(unittest.TestCase):
    def test_the_same_intent_at_two_providers_shares_one_intent_key(self):
        serper = args(provider="serper")
        serpapi = args(provider="serpapi", engine="google")
        self.assertEqual(canonical.intent_signature(serper),
                         canonical.intent_signature(serpapi))
        self.assertNotEqual(canonical.intent_signature(args()),
                            canonical.intent_signature(args(query="other ground enquiry")))

    def test_same_operation_is_a_predicate_not_an_auto_block(self):
        # Cross-provider repetition is *reported* here, never refused: the
        # convergence review keeps that call with the router and its budget.
        self.assertTrue(canonical.same_operation(args(), args(transport="EMBEDDED_OFFICIAL_MCP_HTTP")))
        self.assertFalse(canonical.same_operation(args(), args(provider="serpapi")))
        self.assertTrue(canonical.same_intent(args(), args(provider="serpapi")))
        self.assertFalse(canonical.same_intent(args(), args(mode="news")))


if __name__ == "__main__":
    unittest.main()

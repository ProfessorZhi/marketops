"""Serper's claimed REST coverage is a *declared* registry, never an assumption.

Serper publishes no official MCP and this host could not read Serper's own API
reference, so every category carries an explicit verification state and its
provenance. These tests hold the registry to that: a category we cannot source
must not present itself as verified, and a category we do not declare must not
be dispatchable at all.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import serper_modes


class DeclaredModeRegistryTests(unittest.TestCase):
    def test_every_declared_mode_carries_endpoint_key_and_provenance(self):
        self.assertEqual(
            ("web", "maps", "images", "news", "videos", "shopping",
             "scholar", "patents", "autocomplete", "lens", "reviews"),
            serper_modes.ALL_MODES,
        )
        for mode in serper_modes.ALL_MODES:
            with self.subTest(mode=mode):
                spec = serper_modes.mode_spec(mode)
                self.assertTrue(spec.endpoint_path.startswith("/"))
                self.assertTrue(spec.result_key)
                self.assertIn(spec.verification, serper_modes.VERIFICATION_STATES)
                self.assertTrue(spec.evidence.strip())
                self.assertNotEqual("LIVE_VERIFIED", spec.verification)

    def test_categories_we_do_not_declare_are_not_dispatchable(self):
        # `scrape` lives on a different host (scrape.serper.dev) and is not a
        # route this adapter may call; `/search` as a *mode name* is not a
        # category. Declaring a mode is a decision, and these were not decided.
        #
        # The coordinator's D-3 asks for "13 endpoints". Twelve are declared
        # here; the thirteenth in that count is the scrape host, which this
        # adapter will not claim because its allowlisted origin is
        # google.serper.dev only.
        for undeclared in ("scrape", "search", "lens_deprecated"):
            with self.subTest(mode=undeclared):
                with self.assertRaisesRegex(ValueError, "SERPER_MODE_UNSUPPORTED"):
                    serper_modes.mode_spec(undeclared)

    def test_maps_is_the_places_endpoint_and_says_so(self):
        spec = serper_modes.mode_spec("maps")
        self.assertEqual("/places", spec.endpoint_path)
        self.assertEqual("places", spec.result_key)
        self.assertIn("/maps", spec.endpoint_note)
        self.assertEqual(("/maps",), serper_modes.UNROUTED_DECLARED_PATHS)

    def test_reviews_is_declared_but_this_adapter_cannot_serve_it(self):
        # /reviews needs placeId and a cursor. This adapter sends neither, so
        # the category stays visible in the catalog and refuses dispatch with
        # its own reason instead of being silently absent or silently sent.
        with self.assertRaisesRegex(ValueError, "SERPER_MODE_REQUIRES_UNSENT_PARAM"):
            serper_modes.assert_dispatchable("reviews")
        serper_modes.assert_dispatchable("web")
        serper_modes.assert_dispatchable("lens")
        with self.assertRaisesRegex(ValueError, "SERPER_MODE_REQUIRES_UNSENT_PARAM"):
            serper_modes.assert_page_allowed("reviews", 2)

    def test_the_declared_endpoint_set_is_twelve_paths_on_one_host(self):
        declared = serper_modes.declared_endpoint_paths()
        self.assertEqual(12, len(declared))
        self.assertEqual(
            {"/search", "/places", "/maps", "/images", "/videos", "/news",
             "/shopping", "/scholar", "/patents", "/autocomplete", "/lens",
             "/reviews"},
            set(declared),
        )
        self.assertTrue(all(p.startswith("/") for p in declared))


class PaginationContractTests(unittest.TestCase):
    def test_page_is_one_based_and_bounded_where_declared(self):
        self.assertEqual("page", serper_modes.pagination_param("web"))
        serper_modes.assert_page_allowed("web", 1)
        serper_modes.assert_page_allowed("web", 10)
        for bad in (0, -1, 11, True, "2", None):
            with self.subTest(page=bad):
                with self.assertRaisesRegex(ValueError, "SERPER_PAGE_INVALID"):
                    serper_modes.assert_page_allowed("web", bad)

    def test_autocomplete_is_declared_unpaginated_and_refuses_page_two(self):
        self.assertIsNone(serper_modes.pagination_param("autocomplete"))
        with self.assertRaisesRegex(ValueError, "SERPER_MODE_NOT_PAGINATED"):
            serper_modes.assert_page_allowed("autocomplete", 2)
        serper_modes.assert_page_allowed("autocomplete", 1)

    def test_maps_pagination_needs_a_parameter_we_do_not_send(self):
        # Third-party sources report /maps pages after the first require `ll`.
        # This adapter never sends `ll`, so a second page would be a request we
        # cannot describe — refuse it instead of sending it and hoping.
        with self.assertRaisesRegex(ValueError, "SERPER_MODE_PAGINATION_UNVERIFIED"):
            serper_modes.assert_page_allowed("maps", 2)


class CostAndVerificationHonestyTests(unittest.TestCase):
    def test_no_price_is_hardcoded_anywhere_in_the_registry(self):
        for mode in serper_modes.ALL_MODES:
            with self.subTest(mode=mode):
                spec = serper_modes.mode_spec(mode)
                self.assertIsNone(spec.price_per_query_usd)
                self.assertEqual("UNKNOWN_ACCOUNT_DEPENDENT", spec.credit_basis)
                self.assertNotIn("$", " ".join(spec.cost_notes))

    def test_the_num_gt_10_double_credit_rule_is_recorded_as_pricing_not_as_a_price(self):
        # The Wave-1 acceptance checklist requires this rule to enter pricing.
        # It enters as a *rule with a source*, not as a number: no account price
        # was ever read, so there is nothing to put in a USD field.
        images = serper_modes.mode_spec("images")
        self.assertEqual("serper.credit_rules", images.pricing_source)
        self.assertIn("2026-10-17", images.priced_at)
        rule = next(r for r in images.credit_rules if r["rule"] == "images_num_gt_10_doubles_credit")
        self.assertEqual("SECONDARY_SOURCES_ONLY", rule["verification"])
        self.assertTrue(rule["evidence"].strip())
        self.assertIsNone(images.price_per_query_usd)
        self.assertEqual("serper.credit_rules", serper_modes.pricing_terms("images")["source_id"])
        self.assertIs(serper_modes.pricing_terms("images")["unit_price_verified"], False)

    def test_every_mode_names_a_pricing_source_and_version(self):
        # IL-6: pricing_source / version / priced_at are all-or-nothing.
        for mode in serper_modes.ALL_MODES:
            with self.subTest(mode=mode):
                terms = serper_modes.pricing_terms(mode)
                self.assertTrue(terms["source_id"])
                self.assertTrue(terms["version"])
                self.assertTrue(terms["priced_at"])

    def test_coverage_report_never_claims_official_verification(self):
        report = serper_modes.coverage_report()
        self.assertEqual("marketops.serper.coverage.v1", report["schema"])
        self.assertFalse(report["officially_verified_live"])
        self.assertFalse(report["official_docs_read_successfully"])
        self.assertEqual(0, report["live_verified_modes"])
        self.assertEqual(len(serper_modes.ALL_MODES), report["declared_modes"])
        self.assertEqual(
            sorted(serper_modes.ALL_MODES),
            sorted(row["mode"] for row in report["rows"]),
        )
        for row in report["rows"]:
            self.assertNotEqual("LIVE_VERIFIED", row["verification"])

    def test_the_negative_reading_records_what_was_actually_read(self):
        # A negative result must carry what was read, or a typo'd URL reads as
        # "the world is like this".
        attempt = serper_modes.coverage_report()["official_docs_attempt"]
        self.assertIn("https://serper.dev/", attempt["sources_read"])
        self.assertTrue(attempt["summary"].strip())


if __name__ == "__main__":
    unittest.main()

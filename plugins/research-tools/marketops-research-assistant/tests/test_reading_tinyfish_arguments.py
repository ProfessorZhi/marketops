"""Typed TinyFish argument contracts, derived from the pinned official schema.

The point of these tests is that a broadened filter surface must not become an
open pass-through: every extra option is still checked for name, type and
bound, and anything unknown or credential-shaped is refused before dispatch.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_adapter_tinyfish as tinyfish
import _tinyfish_egress_fixture as egress_fixture


class TinyFishArgumentTest(unittest.TestCase):
    def setUp(self):
        egress_fixture.install(self)
        egress_fixture.no_real_dns(self)
        env = patch.dict(os.environ, {
            "MARKETOPS_FETCH_ALLOWED_DOMAINS": "public.example.org,news.example.org",
        })
        env.start()
        self.addCleanup(env.stop)

    def assertRefused(self, code, tool, arguments):
        with self.assertRaises(ValueError) as err:
            tinyfish.validate(tool, arguments)
        self.assertEqual(code, str(err.exception))


class SearchFilters(TinyFishArgumentTest):
    def test_minimal_search_is_accepted_and_config_is_client_side_only(self):
        out = tinyfish.validate("search", {"query": "public venue list"})
        self.assertEqual({"query": "public venue list"}, out)

    def test_published_search_filters_are_accepted(self):
        out = tinyfish.validate("search", {
            "query": "registry filings",
            "purpose": "find an official register",
            "domain_type": "research_paper",
            "location": "SG",
            "language": "en",
            "pub_year_min": 2015,
            "pub_year_max": 2026,
            "include_domains": "public.example.org",
            "exclude_domains": "news.example.org",
            "include_thumbnail": True,
            "page": 2,
        })
        self.assertEqual(2, out["page"])

    def test_recency_and_absolute_dates_are_mutually_exclusive(self):
        self.assertRefused("TINYFISH_SEARCH_DATE_WINDOW_CONFLICT", "search", {
            "query": "recent notices", "recency_minutes": 60,
            "after_date": "2026-01-01"})
        self.assertRefused("TINYFISH_SEARCH_DATE_WINDOW_CONFLICT", "search", {
            "query": "recent notices", "recency_minutes": 60,
            "before_date": "2026-01-01"})

    def test_publication_year_bounds_require_a_research_paper_search(self):
        self.assertRefused("TINYFISH_SEARCH_PUB_YEAR_REQUIRES_RESEARCH_PAPER",
                           "search", {"query": "some paper", "pub_year_min": 2020})
        self.assertRefused("TINYFISH_SEARCH_PUB_YEAR_REQUIRES_RESEARCH_PAPER",
                           "search", {"query": "some paper", "pub_year_max": 2020,
                                      "domain_type": "web"})

    def test_publication_year_range_must_not_be_inverted(self):
        self.assertRefused("TINYFISH_SEARCH_PUB_YEAR_RANGE_INVERTED", "search", {
            "query": "some paper", "domain_type": "research_paper",
            "pub_year_min": 2026, "pub_year_max": 2015})

    def test_domain_type_is_a_closed_set(self):
        self.assertRefused("TINYFISH_SEARCH_DOMAIN_TYPE_INVALID", "search", {
            "query": "some query", "domain_type": "patents"})

    def test_a_domain_may_not_be_both_included_and_excluded(self):
        self.assertRefused("TINYFISH_SEARCH_DOMAIN_FILTER_CONFLICT", "search", {
            "query": "some query",
            "include_domains": "public.example.org,news.example.org",
            "exclude_domains": "news.example.org"})

    def test_search_bounds_are_enforced(self):
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"query": "x"})
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"query": "y" * 241})
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"query": "ok query", "page": 11})
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"query": "ok query", "page": -1})
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"query": "ok query", "recency_minutes": 0})
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"query": "ok query", "recency_minutes": 5256001})
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"query": "ok query", "after_date": "01/01/2026"})
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"query": "ok query", "include_thumbnail": "yes"})

    def test_boolean_is_not_accepted_where_an_integer_is_required(self):
        # In Python True == 1; a boolean must not slip through an int bound.
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"query": "ok query", "page": True})

    def test_an_unpublished_search_option_is_refused_rather_than_forwarded(self):
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {
            "query": "ok query", "engine": "google"})
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {
            "query": "ok query", "api_key": "x"})

    def test_search_requires_a_query_and_not_its_absence(self):
        self.assertRefused("TINYFISH_SEARCH_ARGS_INVALID", "search", {"purpose": "no query"})


class FetchFilters(TinyFishArgumentTest):
    def test_minimal_fetch_is_accepted(self):
        out = tinyfish.validate("fetch_content", {"urls": ["https://public.example.org/a"]})
        self.assertEqual(["https://public.example.org/a"], out["urls"])

    def test_published_fetch_flags_are_accepted(self):
        out = tinyfish.validate("fetch_content", {
            "urls": ["https://public.example.org/a"],
            "format": "html",
            "links": True,
            "image_links": True,
            "page_metadata": True,
            "purpose": "extract the contact block",
            "include_selectors": [".contact"],
            "exclude_selectors": ["#cookie-banner"],
            "ttl": 0,
            "per_url_timeout_ms": 20000,
            "include_etag_and_last_modified": True,
        })
        self.assertEqual("html", out["format"])

    def test_conditional_headers_are_single_url_only(self):
        for key, value in (("if_none_match", '"abc"'),
                           ("if_modified_since", "Wed, 21 Oct 2026 07:28:00 GMT")):
            self.assertRefused("TINYFISH_FETCH_CONDITIONAL_REQUIRES_SINGLE_URL",
                               "fetch_content",
                               {"urls": ["https://public.example.org/a",
                                         "https://public.example.org/b"],
                                key: value})
        out = tinyfish.validate("fetch_content", {
            "urls": ["https://public.example.org/a"], "if_none_match": '"abc"'})
        self.assertEqual('"abc"', out["if_none_match"])

    def test_fetch_bounds_are_enforced(self):
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content", {"urls": []})
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content",
                           {"urls": ["https://public.example.org/%d" % i for i in range(11)]})
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content",
                           {"urls": "https://public.example.org/a"})
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content",
                           {"urls": ["https://public.example.org/a"], "format": "pdf"})
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content",
                           {"urls": ["https://public.example.org/a"], "ttl": -1})
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content",
                           {"urls": ["https://public.example.org/a"], "links": "true"})
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content",
                           {"urls": ["https://public.example.org/a"], "include_selectors": []})
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content",
                           {"urls": ["https://public.example.org/a"],
                            "include_selectors": ["x" * 1001]})

    def test_fetch_unknown_option_and_embedded_credential_are_refused(self):
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content",
                           {"urls": ["https://public.example.org/a"], "cookies": "session=1"})
        self.assertRefused("TINYFISH_FETCH_ARGS_INVALID", "fetch_content",
                           {"urls": ["https://public.example.org/a"], "authorization": "Bearer x"})

    def test_broadened_filters_do_not_widen_the_target_allowlist(self):
        self.assertRefused("OFFICIAL_MCP_TARGET_NOT_ALLOWED", "fetch_content",
                           {"urls": ["https://not-approved.example.com/a"], "links": True})
        self.assertRefused("OFFICIAL_MCP_TARGET_NOT_ALLOWED", "fetch_content",
                           {"urls": ["http://public.example.org/a"]})
        self.assertRefused("OFFICIAL_MCP_TARGET_NOT_ALLOWED", "fetch_content",
                           {"urls": ["https://public.example.org:8443/a"]})
        self.assertRefused("OFFICIAL_MCP_TARGET_NOT_ALLOWED", "fetch_content",
                           {"urls": ["https://127.0.0.1/a"]})
        self.assertRefused("OFFICIAL_MCP_TARGET_NOT_ALLOWED", "fetch_content",
                           {"urls": ["https://public.example.org@evil.example.com/a"]})

    def test_a_subdomain_of_an_approved_domain_is_approved(self):
        out = tinyfish.validate("fetch_content", {
            "urls": ["https://data.public.example.org/a"]})
        self.assertEqual(["https://data.public.example.org/a"], out["urls"])

    def test_suffix_confusion_is_not_treated_as_a_subdomain(self):
        self.assertRefused("OFFICIAL_MCP_TARGET_NOT_ALLOWED", "fetch_content",
                           {"urls": ["https://evilpublic.example.org.attacker.test/a"]})


class StructuredArgumentSafety(TinyFishArgumentTest):
    """Argument shapes that are not JSON scalars must never reach the vendor."""

    def test_non_dict_arguments_are_refused(self):
        for bad in (None, [], "query=1", 7, True):
            self.assertRefused("TINYFISH_ARGUMENTS_INVALID", "search", bad)

    def test_argument_bearing_tool_rejects_an_unknown_or_missing_argument(self):
        # get_wallet takes no arguments at all; a stray key is a refusal.
        self.assertRefused("TINYFISH_ARGUMENTS_INVALID", "get_wallet", {"limit": 1})
        self.assertRefused("TINYFISH_ARGUMENTS_INVALID", "get_run", {})
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "get_run", {"id": "x", "extra": 1})

    def test_over_wide_and_over_deep_arguments_are_refused(self):
        self.assertRefused("TINYFISH_ARGUMENTS_TOO_LARGE", "search",
                           {"query": "ok query", "purpose": {str(i): i for i in range(40)}})
        nested = current = {}
        for _ in range(6):
            current["next"] = {}
            current = current["next"]
        self.assertRefused("TINYFISH_ARGUMENTS_TOO_LARGE", "search",
                           {"query": "ok query", "purpose": nested})

    def test_non_serialisable_values_are_refused(self):
        self.assertRefused("TINYFISH_ARGUMENTS_INVALID", "search",
                           {"query": "ok query", "location": {"SG"}})
        self.assertRefused("TINYFISH_ARGUMENTS_INVALID", "search",
                           {"query": "ok query", "language": b"en"})


class RunStatusAndUsage(TinyFishArgumentTest):
    def test_get_run_uses_the_published_id_parameter(self):
        self.assertEqual({"id": "run-abc_123"},
                         tinyfish.validate("get_run", {"id": "run-abc_123"}))

    def test_unpublished_run_status_verbs_are_not_aliases(self):
        for name in ("wait_for_run", "wait_for_run_by_id", "get_run_status"):
            self.assertRefused("TINYFISH_TOOL_NOT_IN_OFFICIAL_CATALOG", name,
                               {"id": "run-abc"})
            self.assertRefused("TINYFISH_TOOL_NOT_IN_OFFICIAL_CATALOG", name,
                               {"run_id": "run-abc"})

    def test_run_identifier_shape_is_enforced(self):
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "get_run", {"id": ""})
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "get_run", {"id": "a" * 129})
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "get_run", {"id": "run/../etc"})
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "get_run", {"id": 7})

    def test_batch_status_bounds_the_run_id_list(self):
        self.assertEqual({"run_ids": ["a"]}, tinyfish.validate("batch_status", {"run_ids": ["a"]}))
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "batch_status", {"run_ids": []})
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "batch_status",
                           {"run_ids": ["r%d" % i for i in range(9)]})

    def test_list_runs_requires_the_published_required_pair(self):
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "list_runs", {"limit": 10})
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "list_runs",
                           {"limit": 10, "sort_direction": "up"})
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "list_runs",
                           {"limit": 101, "sort_direction": "desc"})
        self.assertRefused("TINYFISH_STATUS_ARGS_INVALID", "list_runs",
                           {"limit": 10, "sort_direction": "desc", "status": "QUEUED"})
        out = tinyfish.validate("list_runs", {"limit": 10, "sort_direction": "desc",
                                              "status": "RUNNING"})
        self.assertEqual("RUNNING", out["status"])

    def test_wallet_read_takes_no_arguments(self):
        self.assertEqual({}, tinyfish.validate("get_wallet", {}))

    def test_usage_reads_use_their_published_limits(self):
        # `limit` carries a documented default, so the bare call is legal and
        # the default is filled at dispatch rather than demanded here.
        self.assertEqual({}, tinyfish.validate("get_search_usage", {}))
        self.assertRefused("TINYFISH_USAGE_ARGS_INVALID", "get_search_usage", {"limit": 1001})
        self.assertRefused("TINYFISH_USAGE_ARGS_INVALID", "list_fetch_usage", {"limit": 101})
        out = tinyfish.validate("list_fetch_usage", {"limit": 20, "status": "failed"})
        self.assertEqual("failed", out["status"])

    def test_disabled_tools_are_refused_even_with_valid_arguments(self):
        for tool, args in (("create_monitor", {}), ("list_profiles", {}),
                           ("create_browser_session", {}),
                           ("run_web_automation_async", {"url": "https://public.example.org",
                                                         "goal": "read the page"}),
                           ("guide_next_step", {})):
            self.assertRefused("TINYFISH_TOOL_DESCRIBED_NOT_ENABLED", tool, args)


if __name__ == "__main__":
    unittest.main()

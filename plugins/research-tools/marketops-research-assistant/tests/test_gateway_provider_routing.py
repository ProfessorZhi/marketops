"""Policy routing is advice only: no provider billing, login or network."""
from __future__ import annotations
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import research_mcp as mcp
import research_provider_policy as p


class RoutingContractTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.env=patch.dict(os.environ,{
          "HOME":t.name, "XDG_CONFIG_HOME":str(Path(t.name)/"config"),
          "MARKETOPS_RESEARCH_DATA_ROOT":str(Path(t.name)/"data"),
          "MARKETOPS_SEARCH_ALLOW_NETWORK":"0",
          "MARKETOPS_RESEARCH_ALLOW_NETWORK":"0",
          "MARKETOPS_SEARCH_MAX_CALLS":"0",
          "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS":"0",
          "SERPAPI_API_KEY":"","SERPER_API_KEY":"","CRAWL4AI_API_KEY":"",
          "TINYFISH_API_KEY":"","TYPESAFE_API_KEY":""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_known_public_url_never_default_selects_paid_agent(self):
        result=mcp.capabilities(task_kind="known_public_url")
        self.assertEqual("ADVICE_ONLY_NO_AUTO_SWITCH",result["routing_advice"]["mode"])
        choices=result["routing_advice"]["candidates"]
        self.assertEqual("tinyfish.host_fetch",choices[0]["capability"])
        self.assertEqual("PAID_APPROVAL_REQUIRED",choices[-1]["gate"])
        self.assertEqual("tinyfish.host_agent",choices[-1]["capability"])
        self.assertFalse((Path(os.environ["MARKETOPS_RESEARCH_DATA_ROOT"])/"state").exists())

    def test_authenticated_mode_stops_until_user_authorizes_login(self):
        r=mcp.capabilities(task_kind="authorized_login_read")["routing_advice"]
        self.assertEqual("PAID_APPROVAL_REQUIRED",r["candidates"][0]["gate"])
        x=p.propose_route("authorized_login_read",{},{"tinyfish_browser":"reported"},
                          paid_approved=True)
        self.assertEqual("SITE_LOGIN_AUTHORIZATION_REQUIRED",x["candidates"][0]["gate"])
        y=p.propose_route("authorized_login_read",{},{"tinyfish_browser":"reported"},
                          paid_approved=True, login_approved=True)
        self.assertEqual("VERIFY_HOST_CONNECTION_ACCOUNT_RATE_SCOPE_AND_LEDGER",
                         y["candidates"][0]["gate"])

    def test_unknown_task_and_bad_permissions_rejected(self):
        with self.assertRaisesRegex(ValueError,"UNKNOWN_RESEARCH_TASK_KIND"):
            mcp.capabilities(task_kind="pay_for_everything")
        with self.assertRaisesRegex(ValueError,"INVALID_RESEARCH_PERMISSION_FLAGS"):
            p.propose_route("source_discovery",{}, {}, paid_approved=1)

    def test_jev_promotes_only_if_data_budget_and_policy_are_verified(self):
        result=mcp.capabilities(task_kind="semantic_yes_no")
        self.assertEqual("deterministic_rules",
                         result["routing_advice"]["candidates"][0]["capability"])
        self.assertEqual("PAID_APPROVAL_REQUIRED",
                         result["routing_advice"]["candidates"][1]["gate"])

    def test_negative_wallet_blocks_agent_only_and_keeps_zero_listed_fetch_candidate(self):
        wallet = {
            "available_balance": "-0.904", "currency": "USD",
            "as_of": datetime.now(timezone.utc).isoformat(),
            "wallet_url": "https://hidden.example.org/secret-token-not-for-logs",
            "rates": {"meters": [
                {"label": "TinyFish Agent", "unit_amount": "0.016", "per": "step"},
                {"label": "TinyFish Browser", "unit_amount": "0.002", "per": "minute"},
                {"label": "TinyFish Fetch", "unit_amount": "0", "per": "url"},
                {"label": "TinyFish Search", "unit_amount": "0", "per": "query"},
            ]}
        }
        report = mcp.capabilities(
            ["mcp__TinyFish__fetch_content", "mcp__TinyFish__run_web_automation"],
            "known_public_url", wallet)
        observed = report["tinyfish_wallet_observation"]
        self.assertTrue(observed["paid_wallet_exhausted"])
        self.assertFalse(observed["trusted_as_auth"])
        self.assertNotIn("hidden.example.org", str(report))
        choices = {r["capability"]: r for r in report["routing_advice"]["candidates"]}
        self.assertEqual("HOST_REPORTED_ZERO_LISTED_PRICE_NOT_AUTH_VERIFIED",
                         choices["tinyfish.host_fetch"]["cost_class"])
        self.assertEqual("VERIFY_HOST_CONNECTION_ACCOUNT_RATE_SCOPE_AND_LEDGER",
                         choices["tinyfish.host_fetch"]["gate"])
        self.assertEqual("INSUFFICIENT_WALLET_BALANCE",
                         choices["tinyfish.host_agent"]["gate"])

    def test_stale_or_malformed_wallet_never_grants_paid_or_free_claim(self):
        stale = {"available_balance": "100", "currency": "USD",
                 "as_of": "2020-01-01T00:00:00+00:00",
                 "rates": {"meters": [{"label":"TinyFish Fetch","unit_amount":"0","per":"url"}]}}
        result = mcp.capabilities(task_kind="known_public_url",
                                  tinyfish_wallet_snapshot=stale)
        self.assertEqual("HOST_REPORTED_STALE", result["tinyfish_wallet_observation"]["status"])
        self.assertEqual("CURRENT_ACCOUNT_RATE_UNVERIFIED",
                         result["routing_advice"]["candidates"][0]["cost_class"])
        with self.assertRaisesRegex(ValueError,"TINYFISH_WALLET_REPORT_INVALID"):
            mcp.capabilities(tinyfish_wallet_snapshot={"available_balance":"NaN"})

    def test_unknown_rate_does_not_pretend_free_and_wallet_is_not_implicitly_checked(self):
        status=mcp.capabilities(["mcp__TinyFish__fetch_content"],
                                "known_public_url")
        self.assertEqual("CURRENT_ACCOUNT_RATE_UNVERIFIED",
                         status["routing_advice"]["candidates"][0]["cost_class"])
        self.assertEqual("HOST_TOOL_REPORTED_NOT_AUTH_OR_CREDIT_VERIFIED",
                         status["external_host"]["tinyfish_fetch"])
        self.assertFalse(status["capability_matrix"]["tinyfish.browser_agent"]["credits_verified"])


if __name__=="__main__":
    unittest.main()

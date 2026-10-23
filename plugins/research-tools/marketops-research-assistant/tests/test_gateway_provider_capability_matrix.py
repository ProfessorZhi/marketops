"""No-cost capability matrix, provider refusal and native MCP interpreter checks."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import research_mcp as mcp
import research_provider_extensions as ext
import dual_search_mcp as search


# The shared ledger refuses an unpriced capability rather than estimating one
# (research_usage_ledger: "A price is recorded, never assumed"). These tests are
# about other behaviour, so they install an offline catalog that says so.
import sys as _sys
import pathlib as _pathlib

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import wave1_pricing_fixture

wave1_pricing_fixture.install()


class ProviderCapabilityTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root=Path(t.name)
        env=patch.dict(os.environ,{"HOME":str(self.root),
          "XDG_CONFIG_HOME":str(self.root/"xdg"),
          "MARKETOPS_RESEARCH_DATA_ROOT":str(self.root/"store"),
          "MARKETOPS_SEARCH_ALLOW_NETWORK":"0",
          "MARKETOPS_SEARCH_MAX_CALLS":"3",
          "MARKETOPS_RESEARCH_ALLOW_NETWORK":"0",
          "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS":"2",
          "SERPER_API_KEY":"", "SERPAPI_API_KEY":"",
          "TINYFISH_API_KEY":"", "TYPESAFE_API_KEY":"", "CRAWL4AI_API_KEY":""})
        env.start()
        self.addCleanup(env.stop)

    def test_five_partially_wired_providers_are_not_assumed_available(self):
        result=mcp.capabilities()
        self.assertEqual("NO_AUTOMATIC_PAID_FALLBACK_OR_LOGIN",result["selection_boundary"])
        self.assertEqual("UNKNOWN",result["external_host"]["tinyfish_agent"])
        for item in ("serper.web_search","serpapi.google_maps","tinyfish.search",
                     "tinyfish.fetch","crawl4ai.cloud","jev.choice"):
            self.assertEqual("DISABLED_BY_PROJECT_OR_PROCESS_POLICY",
                             result["capability_matrix"][item]["status"])
            self.assertFalse(result["capability_matrix"][item]["credits_verified"])
        self.assertTrue(result["capability_matrix"]["tinyfish.browser_agent"]["implemented_in_lite"])
        self.assertEqual("EMBEDDED_MCP_PAID_AGENT_STRICT_GATED",
                         result["capability_matrix"]["tinyfish.browser_agent"]["status"])
        self.assertFalse(result["capability_matrix"]["tinyfish.browser_agent"]["credits_verified"])
        self.assertEqual({"tinyfish", "serpapi"}, set(result["embedded_official_mcp"]))
        self.assertTrue(result["capability_matrix"]["jev.score"]["implemented_in_lite"])
        self.assertTrue(result["capability_matrix"]["jev.noul"]["implemented_in_lite"])
        self.assertEqual("DISABLED_BY_PROJECT_OR_PROCESS_POLICY",
                         result["capability_matrix"]["jev.score"]["status"])
        self.assertEqual(0,result["budgets"]["provider"]["reserved"])
        self.assertFalse((self.root/"store"/"state").exists(),"Capability check must be read-only")

    def test_host_inventory_is_not_account_or_wallet_verification(self):
        result=mcp.capabilities([
            "mcp__TinyFish__run_web_automation",
            "mcp__TinyFish__list_profiles",
            "mcp__TinyFish__get_wallet"])
        host=result["external_host"]
        self.assertIn("REPORTED",host["tinyfish_agent"])
        self.assertIn("REPORTED",host["tinyfish_profiles"])
        self.assertFalse(result["capability_matrix"]["tinyfish.browser_agent"]["credits_verified"])
        self.assertFalse(result["capability_matrix"]["tinyfish.browser_context_profile"]["authentication_verified"])
        with self.assertRaisesRegex(ValueError,"INVALID_HOST_OBSERVED_TOOLS"):
            mcp.capabilities(["x"*999])

    def test_search_capacity_counts_reserved_attempts_across_runs_without_spending(self):
        with patch.dict(os.environ,{"MARKETOPS_SEARCH_ALLOW_NETWORK":"1","SERPER_API_KEY":"fake"}):
            search._reserve(self.root/"store",run_id="run-a",operation_id="op-a",
                            fingerprint="fingerprint-a",provider="serper",limit=3)
            caps=mcp.capabilities()
        self.assertEqual(1,caps["budgets"]["search"]["reserved"])
        self.assertEqual(2,caps["budgets"]["search"]["remaining"])
        self.assertEqual("ELIGIBLE_BUT_AUTH_AND_CREDITS_UNVERIFIED",
                         caps["capability_matrix"]["serper.web_search"]["status"])


class ProviderErrorTests(unittest.TestCase):
    def test_credit_auth_rate_http_classes_are_safe(self):
        for code,expected in ((401,"UPSTREAM_AUTH_REJECTED"),
                              (402,"UPSTREAM_INSUFFICIENT_CREDITS"),
                              (403,"UPSTREAM_ACCESS_FORBIDDEN"),
                              (429,"UPSTREAM_RATE_LIMITED"),
                              (503,"UPSTREAM_SERVICE_UNAVAILABLE")):
            with self.subTest(code=code):
                def reject(req,timeout):
                    raise urllib.error.HTTPError(
                        req.full_url,code,"raw-secret-must-not-leak",None,None)
                with self.assertRaisesRegex(ValueError,expected) as failure:
                    ext._http_json("https://api.fetch.tinyfish.ai",method="POST",
                                   key="private-test-key",data={"url":"https://example.org"},
                                   opener=reject)
                self.assertNotIn("private-test-key",str(failure.exception))
                self.assertNotIn("example.org",str(failure.exception))

    def test_structured_provider_credit_error_is_classified(self):
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): return False
            def read(self,n): return b'{"error":{"code":"insufficient_credits"}}'
        with self.assertRaisesRegex(ValueError,"UPSTREAM_INSUFFICIENT_CREDITS"):
            ext._http_json("https://api.fetch.tinyfish.ai",method="POST",
                           key="a-test-key",data={"url":"https://example.org"},
                           opener=lambda req,timeout:Response())


@unittest.skipIf(sys.platform=="win32","Shell launcher is POSIX-only; Windows uses installed venv config")
class LauncherTests(unittest.TestCase):
    def test_bundled_launcher_uses_explicit_private_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=Path(tmp)/"fake-python"
            fake.write_text("#!/bin/sh\nif [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
                            "printf 'LAUNCHED:%s\\n' \"$1\"\n",encoding="utf-8")
            fake.chmod(0o700)
            env={**os.environ,"MARKETOPS_RESEARCH_PYTHON":str(fake)}
            run=subprocess.run(["sh",str(ROOT/"scripts"/"launch_research_mcp.sh")],
                               env=env,text=True,capture_output=True)
            self.assertEqual(0,run.returncode,run.stderr)
            self.assertIn("LAUNCHED:",run.stdout)
            # The launcher's default entry is the host-facing facade, which
            # serves the same server with host-compatible tool schemas.
            self.assertIn("research_mcp_facade.py",run.stdout)

    def test_relative_override_is_rejected_without_running_anything(self):
        with patch.dict(os.environ,{"MARKETOPS_RESEARCH_PYTHON":"relative-python"}):
            run=subprocess.run(["sh",str(ROOT/"scripts"/"launch_research_mcp.sh")],
                               text=True,capture_output=True)
        self.assertEqual(78,run.returncode)
        self.assertIn("MARKETOPS_PYTHON_OVERRIDE_NOT_ABSOLUTE",run.stderr)

    def test_claude_native_manifest_runs_launcher_not_path_python(self):
        manifest=json.loads((ROOT/".mcp.json").read_text(encoding="utf-8"))
        server=manifest["mcpServers"]["marketops-research"]
        self.assertEqual("sh",server["command"])
        self.assertIn("launch_research_mcp.sh",server["args"][0])
        self.assertIn("MARKETOPS_RESEARCH_PYTHON",server["env_vars"])


if __name__=="__main__":
    unittest.main()

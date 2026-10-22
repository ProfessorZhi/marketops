"""Fixes found by driving the installed plugin on a live Paris run.

* an unknown Serper mode is a clean local refusal, not an "unknown charge" that
  holds budget and blocks every later search;
* the Serper, TinyFish and official-MCP clients use only the proxy configured for
  MarketOps, never the machine's ambient one;
* a machine-wide PLAYWRIGHT_BROWSERS_PATH without this Playwright's Chromium does
  not hide the browser the installer downloaded;
* request URLs (SerpApi carries its key in the query) are not logged at INFO.
"""
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


class SerperModes(unittest.TestCase):
    def test_places_means_maps_and_unknown_modes_have_a_closed_code(self):
        import research_adapter_serper as adapter
        import serper_modes
        self.assertEqual("maps", serper_modes.mode_spec("places").mode)
        with self.assertRaisesRegex(ValueError, "SERPER_MODE_UNSUPPORTED"):
            serper_modes.mode_spec("nearby")
        # The refusal path must accept the code instead of asserting.
        self.assertEqual("SERPER_MODE_UNSUPPORTED", adapter._reason("SERPER_MODE_UNSUPPORTED"))


class ConfiguredProxyOnly(unittest.TestCase):
    def test_upstream_proxy_reads_only_the_marketops_setting(self):
        import dual_search_mcp
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9"}):
            os.environ.pop("MARKETOPS_EGRESS_UPSTREAM_PROXY", None)
            self.assertIsNone(dual_search_mcp.upstream_proxy())
        with patch.dict(os.environ, {"MARKETOPS_EGRESS_UPSTREAM_PROXY": "http://proxy.corp:8080"}):
            self.assertEqual("http://proxy.corp:8080", dual_search_mcp.upstream_proxy())
            import research_adapter_serper
            import urllib.request
            opener = research_adapter_serper.SerperAdapter()._opener
            proxies = [h.proxies for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
            self.assertIn({"http": "http://proxy.corp:8080", "https": "http://proxy.corp:8080"}, proxies)
            import research_adapter_tinyfish
            import research_official_mcp
            self.assertEqual("http://proxy.corp:8080", research_adapter_tinyfish._upstream_proxy())
            self.assertEqual("http://proxy.corp:8080", research_official_mcp._upstream_proxy())


class InstalledBrowser(unittest.TestCase):
    def test_a_machine_path_without_our_chromium_is_replaced(self):
        import research_mcp
        with tempfile.TemporaryDirectory() as tmp:
            mine, other = Path(tmp) / "browsers", Path(tmp) / "other"
            (mine / "chromium-1248").mkdir(parents=True)
            (other / "chromium-1194").mkdir(parents=True)
            with patch.object(research_mcp, "installed_browser_root", return_value=mine), \
                    patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(other)}):
                research_mcp._use_installed_browser()
                self.assertEqual(str(mine), os.environ["PLAYWRIGHT_BROWSERS_PATH"])
            (other / "chromium-1248").mkdir()
            with patch.object(research_mcp, "installed_browser_root", return_value=mine), \
                    patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": str(other)}):
                research_mcp._use_installed_browser()
                self.assertEqual(str(other), os.environ["PLAYWRIGHT_BROWSERS_PATH"])


class QuietHttpLogs(unittest.TestCase):
    def test_request_urls_are_not_logged_at_info(self):
        import research_mcp
        logging.getLogger("httpx").setLevel(logging.INFO)
        research_mcp._quiet_http_logs()
        self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)




class MapListingNames(unittest.TestCase):
    def test_a_listing_title_finds_the_name_the_site_uses(self):
        import research_site_harvest as harvest
        self.assertIn("Arkose Nanterre Arboretum",
                      harvest.name_variants("Arkose Nanterre Arboretum - Salle d'escalade"))
        self.assertEqual("Arkose", harvest.host_bound_name("Arkose Issy-les-Moulineaux",
                                                           "https://arkose.com/issy"))
        self.assertEqual("Climb Up", harvest.host_bound_name("Climb Up Epinay",
                                                             "https://epinay.climb-up.fr/"))
        self.assertEqual("", harvest.host_bound_name("Gymnase Japy", "https://www.paris.fr/x"))
        self.assertEqual("Rumst", harvest.host_bound_name("Gemeente Rumst", "https://www.rumst.be"))


class EmailExtraction(unittest.TestCase):
    def test_a_placeholder_is_not_an_address(self):
        import research_site_harvest as harvest
        values = [r["value"] for r in harvest.extract_routes(
            "Réservez une adresse ...@antony.fr pour votre blog. Contact : jean.dupont@ville.fr.")]
        self.assertEqual(["jean.dupont@ville.fr"], values)


class TrackingParameters(unittest.TestCase):
    def test_map_links_lose_their_tracking_tags(self):
        import research_pool
        self.assertEqual("https://www.lefive.fr/nos-centres/25/creteil",
                         research_pool._without_tracking(
                             "https://www.lefive.fr/nos-centres/25/creteil?utm_source=google&utm_medium=x"))
        self.assertEqual("https://x.fr/p?id=3", research_pool._without_tracking("https://x.fr/p?id=3&gclid=9"))


if __name__ == "__main__":
    unittest.main()

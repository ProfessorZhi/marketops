"""RC6: response shapes and SDK names observed in the first live run.

Fakes had encoded assumptions the real vendors do not share: TinyFish returns
requested HTML in `text` with anchors stripped and links listed separately, and
typesafe-sdk 0.7.5 keeps its defaults in `typesafe_sdk.constants`.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_adapter_jev as jev  # noqa: E402
import research_lite_store as store  # noqa: E402
import research_provider_html as provider_html  # noqa: E402
import research_site_harvest as harvest  # noqa: E402

TINYFISH_ROW = {"url": "https://writtlevillagehall.co.uk/",
                "final_url": "https://writtlevillagehall.co.uk/", "format": "html",
                "text": "<body><h2>Writtle Village Hall</h2><h4>Email</h4>"
                        "<h3>writtlevillagehall2019@gmail.com</h3></body>",
                "links": ["https://writtlevillagehall.co.uk/contact.php",
                          "https://writtlevillagehall.co.uk/hire-rates.php"]}


class LiveVendorShapeTests(unittest.TestCase):
    def test_tinyfish_html_in_text_is_accepted(self):
        pages = provider_html.extract_pages({"results": [TINYFISH_ROW]},
                                            ["https://writtlevillagehall.co.uk/"])
        self.assertIn("writtlevillagehall2019@gmail.com", pages[0]["html"])

    def test_same_site_redirect_is_accepted_and_cross_site_refused(self):
        same = {**TINYFISH_ROW, "final_url": "https://www.writtlevillagehall.co.uk/index.php"}
        self.assertTrue(provider_html.extract_pages({"results": [same]},
                                                    ["https://writtlevillagehall.co.uk/"]))
        other = {**TINYFISH_ROW, "final_url": "https://parking-page.example/"}
        with self.assertRaisesRegex(ValueError, "PROVIDER_HTML_URL_MISMATCH"):
            provider_html.extract_pages({"results": [other]}, ["https://writtlevillagehall.co.uk/"])

    def test_social_and_directory_hosts_are_not_own_sites(self):
        import research_delivery as delivery
        self.assertTrue(delivery.shared_host("www.facebook.com"))
        self.assertTrue(delivery.shared_host("essexrcc.org.uk"))
        self.assertFalse(delivery.shared_host("writtlevillagehall.co.uk"))

    def test_markdown_in_text_is_still_refused(self):
        row = {**TINYFISH_ROW, "text": "# Writtle Village Hall\nEmail: x@y.example"}
        with self.assertRaises(ValueError):
            provider_html.extract_pages({"results": [row]}, ["https://writtlevillagehall.co.uk/"])

    def test_harvest_reads_links_from_the_vendor_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "r", "s", "objective", "v1")
            store.save_artifact(root, "r", "env", "tinyfish", TINYFISH_ROW["url"],
                                json.dumps({"results": [TINYFISH_ROW]}).encode(),
                                "provider_extracted")
            links = harvest._envelope_links(root, "r", {"artifact_ids": ["env"]})
        self.assertEqual(TINYFISH_ROW["links"], links)

    @unittest.skipUnless(importlib.util.find_spec("typesafe_sdk"), "typesafe-sdk not installed")
    def test_jev_call_uses_only_names_the_real_sdk_has(self):
        import typesafe_sdk
        source = inspect.getsource(jev.JevAdapter._call)
        for name in ("DEFAULT_BASE_URL", "DEFAULT_MODEL"):
            if not hasattr(typesafe_sdk, name):
                self.assertNotIn("sdk." + name, source)
        self.assertTrue(hasattr(typesafe_sdk, "TypeSafeClient"))
        self.assertTrue(hasattr(typesafe_sdk, "RetryPolicy"))


if __name__ == "__main__":
    unittest.main()

"""RC7: a public PDF (recruitment pack, pitch strategy) is quotable evidence.

The live Essex cricket run lost the Head Groundsman's email: it sat in a club
PDF, TinyFish returned the PDF's text with final_url on the server's IP, and
the URL check refused it. The browser rung cannot read PDFs at all.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import research_provider_html as html  # noqa: E402
import research_walls as walls  # noqa: E402

PDF = "https://club.example.org/uploads/Grounds-Pack.pdf"


class PdfFetchTests(unittest.TestCase):
    def test_pdf_text_on_the_resolved_ip_is_the_requested_source(self):
        pages = html.extract_pages({"results": [{
            "url": PDF, "final_url": "https://104.26.8.43/uploads/Grounds-Pack.pdf",
            "text": "Head Groundsman Stuart Kerrison stuart.kerrison@club.example.org"}]}, [PDF])
        self.assertIn("stuart.kerrison@club.example.org", pages[0]["html"])
        self.assertTrue(pages[0]["html"].startswith("<html><body><pre>"))

    def test_another_path_on_an_ip_or_another_host_is_still_refused(self):
        for final in ("https://104.26.8.43/other.pdf", "https://elsewhere.example/uploads/Grounds-Pack.pdf"):
            with self.assertRaisesRegex(ValueError, "PROVIDER_HTML_URL_MISMATCH"):
                html.extract_pages({"results": [{"url": PDF, "final_url": final, "text": "x" * 40}]},
                                   [PDF])

    def test_plain_text_for_a_web_page_is_not_relabelled_as_html(self):
        page = "https://club.example.org/contact"
        with self.assertRaisesRegex(ValueError, "PROVIDER_HTML_INVALID_CONTENT"):
            html.extract_pages({"results": [{"url": page, "text": "Contact us at info@x.org"}]}, [page])

    def test_pdfs_go_straight_to_the_cloud_fetch(self):
        route = ("crawl4ai.fetch_page", "tinyfish.fetch_content")
        self.assertEqual(("tinyfish.fetch_content",), walls.preferred_route("/nonexistent", PDF, route))


class VerifierSeesWhatTheGateSawTests(unittest.TestCase):
    """Live run: the verifier read Billericay's "Email" links as missing addresses."""

    PAGE = (b'<p>Secretary Rob Rayner <a href="mailto:sec@billericaycc.example">Email</a></p>'
            b'<p>Fixtures <a href="mailto:fix@billericaycc.example">fix@billericaycc.example</a></p>')

    def test_excerpt_shows_link_addresses_once(self):
        import tempfile
        import research_evidence as evidence
        import research_lite_store as store
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "r", "clubs", "objective", "v1")
            store.save_artifact(root, "r", "a1", "crawl4ai", "https://billericaycc.example/contact",
                                self.PAGE, "rendered_html")
            snippet = evidence.excerpts(root, "r", ["a1"], ["Rob Rayner"])["results"][0]["snippets"][0]
        self.assertIn("Secretary Rob Rayner sec@billericaycc.example Email", snippet)
        self.assertEqual(1, snippet.count("fix@billericaycc.example"))


class PublishedNameTests(unittest.TestCase):
    """The list says "Essex County Cricket Club"; the club's own site says "Essex Cricket"."""

    def test_the_site_name_variant_binds_identity_and_routes(self):
        import tempfile
        import research_lite_store as store
        import research_site_harvest as harvest
        page = (b"<html><body><h1>Welcome to Essex Cricket</h1><p>Head Groundsman: Stuart Kerrison,"
                b" stuart.kerrison@essexcricket.example</p></body></html>")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "r", "clubs", "Essex cricket clubs", "v1")

            def fetch(url, op):
                if url != "https://essexcricket.example/":
                    return {"status": "HOLD", "reason_code": "FETCH_FAILED"}
                store.save_artifact(root, "r", "a-" + op, "crawl4ai", url, page, "rendered_html")
                return {"status": "SUCCESS_CANDIDATE", "artifact_ids": ["a-" + op],
                        "primary_artifact_ids": ["a-" + op]}

            result = harvest.harvest(root, run_id="r", operation_id="h1",
                                     organization_name="Essex County Cricket Club",
                                     organization_key="org-eccc", url="https://essexcricket.example/",
                                     fetch=fetch, max_pages=1)
        self.assertEqual("Essex Cricket", result["identity"]["published_name"])
        self.assertEqual("ASSIGN_ROLE_AND_GRADE_FROM_CONTEXT", result["next_action"])
        self.assertIn("stuart.kerrison@essexcricket.example", [r["value"] for r in result["routes"]])

    def test_a_refused_apex_to_www_redirect_retries_the_www_twin(self):
        import tempfile
        import research_lite_store as store
        import research_site_harvest as harvest
        asked = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store.start_run(root, "r", "clubs", "objective", "v1")

            def fetch(url, op):
                asked.append(url)
                if url == "https://harlowcc.example/":
                    return {"status": "HOLD", "attempts": [
                        {"primary_reason_code": "CRAWL_REDIRECT_REFUSED"}]}
                store.save_artifact(root, "r", "a-" + op, "crawl4ai", url,
                                    b"<p>Harlow Cricket Club: info@harlowcc.example</p>", "rendered_html")
                return {"status": "SUCCESS_CANDIDATE", "artifact_ids": ["a-" + op]}
            result = harvest.harvest(root, run_id="r", operation_id="h", organization_name="Harlow CC",
                                     organization_key="org-h", url="https://harlowcc.example/",
                                     fetch=fetch, max_pages=1)
        self.assertEqual(["https://harlowcc.example/", "https://www.harlowcc.example/"], asked)
        self.assertEqual("Harlow Cricket Club", result["identity"]["published_name"])

    def test_a_village_hall_name_is_not_shortened(self):
        import research_site_harvest as harvest
        self.assertEqual(["Writtle Village Hall"], harvest.name_variants("Writtle Village Hall"))


if __name__ == "__main__":
    unittest.main()

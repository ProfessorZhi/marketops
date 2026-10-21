"""Real SDK regression: a dead desktop console must not break rendered material.

Raw HTML is supplied to the browser; no public URL or provider account is used.
The optional local-crawler install runs these tests; the core-only suite skips.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@unittest.skipUnless(importlib.util.find_spec("crawl4ai"), "optional local Crawl4AI SDK not installed")
class BackgroundConsole(unittest.TestCase):
    def test_dead_console_preserves_repeated_page_reads_and_pdf(self):
        probe = r'''
import io, sys
from unittest.mock import patch
from research_adapter_crawl4ai import Crawl4AIAdapter, LOOPBACK_BYPASS_GUARD
import crawl4ai

class DeadConsole(io.TextIOBase):
    def write(self, text):
        raise OSError(22, "host console unavailable")
    def flush(self):
        pass

adapter = Crawl4AIAdapter()
kwargs = {"proxy_config": {"server": "http://127.0.0.1:9"},
          "extra_args": [LOOPBACK_BYPASS_GUARD, "--disable-quic"]}
url = "raw:<html><body><h1>Background read proof</h1><p>Known local content.</p></body></html>"
with patch("sys.stderr", DeadConsole()), patch("sys.stdout", DeadConsole()):
    for _ in range(2):
        page = adapter._run_sdk_once(crawl4ai, url=url, egress_kwargs=kwargs, page_timeout_ms=10000)
        assert page["success"] and "Background read proof" in page["html"], page
    pdf = adapter._run_sdk_artifact(crawl4ai, url=url, kind="pdf", egress_kwargs=kwargs, max_bytes=1000000)
    assert pdf["bytes"].startswith(b"%PDF"), "missing actual PDF"
print("BACKGROUND_READS_AND_PDF_OK")
'''
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, "PYTHONPATH": str(SCRIPTS), "CRAWL4_AI_BASE_DIRECTORY": folder}
            done = subprocess.run([sys.executable, "-c", probe], env=env,
                                  capture_output=True, text=True, timeout=90)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertIn("BACKGROUND_READS_AND_PDF_OK", done.stdout)


if __name__ == "__main__":
    unittest.main()

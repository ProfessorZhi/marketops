"""RC6: the one-time setup warns about synced folders and installs the crawler on request."""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import customer_setup as setup  # noqa: E402


class CustomerSetupTests(unittest.TestCase):
    def test_synced_folder_is_warned(self):
        out = io.StringIO()
        with redirect_stdout(out):
            setup.warn_sync_folder("C:/Users/someone/OneDrive/MarketOps")
            setup.warn_sync_folder("D:/MarketOps/data")
        self.assertEqual(1, out.getvalue().count("警告"))

    def test_crawler_install_runs_pip_then_browser(self):
        calls = []
        with patch("importlib.util.find_spec", return_value=None), \
                patch("builtins.input", return_value=""), \
                patch.object(setup.subprocess, "run",
                             side_effect=lambda argv: calls.append(argv) or type("R", (), {"returncode": 0})()), \
                redirect_stdout(io.StringIO()):
            setup.offer_crawl4ai()
        self.assertEqual(["pip", "install"], calls[0][2:4])
        self.assertEqual(["playwright", "install", "chromium"], calls[1][2:5])

    def test_crawler_install_can_be_declined(self):
        with patch("importlib.util.find_spec", return_value=None), \
                patch("builtins.input", return_value="n"), \
                patch.object(setup.subprocess, "run") as run, redirect_stdout(io.StringIO()):
            setup.offer_crawl4ai()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

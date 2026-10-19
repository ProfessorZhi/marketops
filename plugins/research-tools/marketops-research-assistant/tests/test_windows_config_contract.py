"""Windows paths must preserve the same source/data/summary boundaries."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_config as config


class WindowsConfigurationBoundaryTests(unittest.TestCase):
    def test_summary_inside_windows_data_is_refused_even_with_case_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"MARKETOPS_RESEARCH_CONFIG": str(Path(tmp) / "config.json")}
            for summary in (r"C:\Research\data\Experience.md", r"c:\research\DATA\Experience.md"):
                with self.assertRaisesRegex(ValueError, "EXPERIENCE_FILE_INSIDE_DATA_ROOT"):
                    config.configure(r"C:\Research\data", summary, env=env, platform="win32")
            self.assertFalse(Path(env["MARKETOPS_RESEARCH_CONFIG"]).exists())

    @unittest.skipUnless(sys.platform == "win32", "Requires real Windows Path semantics")
    def test_native_windows_git_checkout_is_refused_before_any_config_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            checkout = base / "checkout"
            (checkout / ".git").mkdir(parents=True)
            env = {"MARKETOPS_RESEARCH_CONFIG": str(base / "config.json")}
            with self.assertRaisesRegex(ValueError, "PATH_INSIDE_GIT_CHECKOUT"):
                config.configure(str(checkout / "data"), str(base / "Experience.md"), env=env)
            self.assertFalse(Path(env["MARKETOPS_RESEARCH_CONFIG"]).exists())


if __name__ == "__main__":
    unittest.main()

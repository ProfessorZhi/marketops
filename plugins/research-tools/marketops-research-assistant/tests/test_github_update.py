"""Update from GitHub: newest release, verified download, versioned layout, rollback."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import release_layout as layout  # noqa: E402
import update_marketops as updater  # noqa: E402


class Versions(unittest.TestCase):
    def test_release_beats_its_candidates(self):
        key = updater.version_key
        self.assertLess(key("0.5.7-rc7"), key("0.5.7"))
        self.assertLess(key("0.5.7"), key("0.5.8-rc1"))
        self.assertLess(key("0.5.9"), key("0.5.10"))
        self.assertIsNone(key("latest"))

    def test_stable_channel_skips_prereleases_and_drafts(self):
        feed = [{"tag_name": "marketops-v0.6.0-rc1", "prerelease": True, "draft": False},
                {"tag_name": "marketops-v0.5.9", "prerelease": False, "draft": True},
                {"tag_name": "marketops-v0.5.8", "prerelease": False, "draft": False},
                {"tag_name": "other-v9.9.9", "prerelease": False, "draft": False}]
        get = lambda _url: json.dumps(feed).encode()  # noqa: E731
        self.assertEqual("marketops-v0.5.8",
                         updater.latest_release("o/r", False, get=get)["tag_name"])
        self.assertEqual("marketops-v0.6.0-rc1",
                         updater.latest_release("o/r", True, get=get)["tag_name"])


class Download(unittest.TestCase):
    def test_a_package_that_does_not_match_its_checksum_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            package = tmp / "marketops-research-assistant-v0.5.8-source.zip"
            package.write_bytes(b"package")
            release = {"assets": [
                {"name": package.name, "browser_download_url": package.as_uri()},
                {"name": package.name + ".sha256",
                 "browser_download_url": (tmp / "sum").as_uri()}]}
            (tmp / "sum").write_text("0" * 64 + "  x\n")
            with self.assertRaises(ValueError):
                updater.download(release, tmp / "dl")
            (tmp / "sum").write_text(hashlib.sha256(b"package").hexdigest() + "  x\n")
            self.assertTrue(updater.download(release, tmp / "dl2").is_file())


class Layout(unittest.TestCase):
    def test_hosts_get_current_and_current_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "MarketOps"
            old, new = root / "releases" / "0.5.7", root / "releases" / "0.5.8"
            for version_dir in (old, new):
                plugin = version_dir / "plugins" / "p"
                plugin.mkdir(parents=True)
                (plugin / "mcp.json").write_text(json.dumps(
                    {"command": str(version_dir / ".venv" / "bin" / "python")}))
            self.assertEqual(root, layout.app_root_for(old))
            self.assertIsNone(layout.app_root_for(Path(tmp) / "elsewhere"))
            rebased = layout.rebase_host_files(old, old / "plugins" / "p")
            self.assertEqual(1, len(rebased))
            text = (old / "plugins" / "p" / "mcp.json").read_text()
            self.assertIn(str(layout.current_link(root)), json.loads(text)["command"])
            first = layout.switch_current(root, old)
            self.assertIsNone(first["previous"])
            second = layout.switch_current(root, new)
            self.assertEqual(str(old.resolve()), second["previous"])
            self.assertEqual(new.resolve(), layout.current_link(root).resolve())
            self.assertEqual(["0.5.7", "0.5.8"], layout.installed_versions(root))

    def test_update_entrypoints_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = layout.write_update_entrypoints(Path(tmp), client="all", register_codex=True)
            self.assertEqual(3, len(files))
            settings = json.loads((Path(tmp) / "update-settings.json").read_text())
            self.assertEqual("ProfessorZhi/marketops", settings["repository"])


if __name__ == "__main__":
    unittest.main()

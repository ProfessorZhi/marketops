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

    def test_candidate_tag_is_not_stable_even_if_github_flag_is_false(self):
        feed = [{"tag_name": "marketops-v0.6.0-rc1", "prerelease": False}]
        self.assertIsNone(updater.latest_release("o/r", False,
                          get=lambda _: json.dumps(feed).encode()))


class Download(unittest.TestCase):
    def test_a_package_that_does_not_match_its_checksum_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            package = tmp / "marketops-research-assistant-v0.5.8-source.zip"
            package.write_bytes(b"package")
            release = {"tag_name": "marketops-v0.5.8", "assets": [
                {"name": package.name, "browser_download_url": package.as_uri()},
                {"name": package.name + ".sha256",
                 "browser_download_url": (tmp / "sum").as_uri()}]}
            (tmp / "sum").write_text("0" * 64 + "  x\n")
            with self.assertRaises(ValueError):
                updater.download(release, tmp / "dl")
            (tmp / "sum").write_text(hashlib.sha256(b"package").hexdigest() + "  x\n")
            self.assertTrue(updater.download(release, tmp / "dl2").is_file())

    def test_assets_must_belong_to_the_selected_version(self):
        release = {"tag_name": "marketops-v0.5.8", "assets": [
            {"name": "marketops-research-assistant-v0.5.7-source.zip"}]}
        with self.assertRaisesRegex(ValueError, "RELEASE_ASSET_MISSING"):
            updater._asset(release, "-source.zip")


class Install(unittest.TestCase):
    def test_update_and_rollback_use_the_previously_active_version(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for version in ("0.5.7", "0.5.8", "0.5.9"):
                plugin = root / "releases" / version / "plugins" / updater.PLUGIN
                plugin.mkdir(parents=True)
                (plugin / "plugin.json").write_text(json.dumps({"version": version}))
            layout.switch_current(root, root / "releases/0.5.7")
            layout.write_update_entrypoints(root, client="codex", register_codex=True)
            with patch.object(updater, "latest_release", return_value={"tag_name": "marketops-v0.5.9"}), \
                    patch.object(updater, "install", return_value=root / "releases/0.5.9"), \
                    patch.object(updater, "self_check", return_value={"ok": True, "tools": 30}), \
                    patch.object(updater, "reregister", return_value="CODEX_REFRESHED"), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, updater.main(["--app-root", str(root)]))
                self.assertEqual("0.5.9", updater.installed_version(root))
                self.assertEqual(0, updater.main(["--app-root", str(root), "--rollback"]))
            self.assertEqual("0.5.7", updater.installed_version(root))
            self.assertTrue(json.loads((root / "update-settings.json").read_text())["register_codex"])

    def test_failed_self_check_keeps_the_active_version_and_settings(self):
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "releases/0.5.7/plugins" / updater.PLUGIN
            plugin.mkdir(parents=True)
            (plugin / "plugin.json").write_text(json.dumps({"version": "0.5.7"}))
            layout.switch_current(root, root / "releases/0.5.7")
            layout.write_update_entrypoints(root, client="codex", register_codex=True)
            original = (root / "update-settings.json").read_bytes()
            with patch.object(updater, "latest_release", return_value={"tag_name": "marketops-v0.5.8"}), \
                    patch.object(updater, "install", return_value=root / "releases/0.5.8"), \
                    patch.object(updater, "self_check", return_value={"ok": False, "tools": 0}), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(2, updater.main(["--app-root", str(root)]))
            self.assertEqual("0.5.7", updater.installed_version(root))
            self.assertEqual(original, (root / "update-settings.json").read_bytes())

    def test_staging_an_install_does_not_replace_shared_update_settings(self):
        import setup_clients

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout.write_update_entrypoints(root, client="all", register_codex=True,
                                            channel="prerelease")
            original = {name: (root / name).read_bytes()
                        for name in ("Update.cmd", "update.sh", "update-settings.json")}
            target = root / "releases/0.5.8"
            setup_clients.install(target, "codex", dependencies=False, register_codex=False,
                                  switch_current=False)
            for name, content in original.items():
                self.assertEqual(content, (root / name).read_bytes(), name)
            self.assertFalse(layout.current_link(root).exists())

    def test_workbuddy_archive_uses_current_before_its_digest_is_computed(self):
        import zipfile
        import build_client_bundles

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version = root / "releases/0.5.8"
            output = build_client_bundles.build(version / "connectors", with_research_mcp=True,
                                                host_install_root=version)
            archive_path = Path(output["zip"])
            with zipfile.ZipFile(archive_path) as archive:
                config = json.loads(archive.read(updater.PLUGIN + "/mcp.json"))
            server = config["mcpServers"]["marketops-research"]
            self.assertIn(str(root.resolve() / "current"), server["args"][0])
            self.assertNotIn(str(version), server["args"][0])
            self.assertEqual(hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                             output["zip_sha256"])

    def test_failed_or_incomplete_install_keeps_current_and_settings(self):
        import zipfile
        from types import SimpleNamespace

        for code in (1, 2):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                old = root / "releases" / "0.5.7"
                old.mkdir(parents=True)
                layout.switch_current(root, old)
                layout.write_update_entrypoints(root, client="codex", register_codex=True)
                settings_path = root / "update-settings.json"
                settings_bytes = settings_path.read_bytes()
                package = root / "test.zip"
                with zipfile.ZipFile(package, "w") as archive:
                    archive.writestr(updater.PLUGIN + "/scripts/setup_clients.py", "")
                with patch.object(updater, "download", return_value=package), \
                        patch.object(updater.subprocess, "run",
                                     return_value=SimpleNamespace(returncode=code, stdout="failed")):
                    with self.assertRaisesRegex(ValueError, "NEW_VERSION_INSTALL_FAILED"):
                        updater.install(root, {"tag_name": "marketops-v0.5.8"},
                                        json.loads(settings_bytes))
                self.assertEqual(old.resolve(), layout.current_link(root).resolve())
                self.assertEqual(settings_bytes, settings_path.read_bytes())
                self.assertEqual("failed", (root / "downloads/0.5.8/install.log").read_text())


class Layout(unittest.TestCase):
    def test_a_real_current_directory_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = root / "current"
            current.mkdir()
            (current / "keep.txt").write_text("user file")
            version = root / "releases/0.5.8"
            version.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "CURRENT_IS_NOT_A_LINK"):
                layout.switch_current(root, version)
            self.assertEqual("user file", (current / "keep.txt").read_text())

    def test_hosts_get_current_and_current_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve() / "MarketOps"
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
            self.assertEqual(5, len(files))
            settings = json.loads((Path(tmp) / "update-settings.json").read_text())
            self.assertEqual("ProfessorZhi/marketops", settings["repository"])

    def test_failed_switch_restores_the_old_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root / "releases/0.5.7", root / "releases/0.5.8"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            layout.switch_current(root, old)
            if sys.platform == "win32":
                rename = Path.rename

                def fail_switch(path, target):
                    if path.name == "current.next":
                        raise OSError("simulated switch failure")
                    return rename(path, target)

                mock_switch = patch.object(Path, "rename", fail_switch)
            else:
                mock_switch = patch.object(layout.os, "replace", side_effect=OSError("switch failed"))
            with mock_switch, self.assertRaises(OSError):
                layout.switch_current(root, new)
            self.assertEqual(old.resolve(), layout.current_link(root).resolve())


if __name__ == "__main__":
    unittest.main()

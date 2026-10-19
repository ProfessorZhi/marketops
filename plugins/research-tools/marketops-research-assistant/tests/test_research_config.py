"""First-use configuration contract: two paths, persisted, re-readable after restart.

No network, no provider, no credentials. Every case passes its own `env` mapping
and `platform` so the tests never read or write the machine's real settings.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import research_config as cfg

POSIX = "linux"


def env_for(tmp: str, **extra) -> dict:
    """An isolated environment: own HOME and XDG dir, nothing inherited."""
    base = {"HOME": str(Path(tmp) / "home"), "XDG_CONFIG_HOME": str(Path(tmp) / "xdg"),
            "PATH": os.environ.get("PATH", "")}
    base.update(extra)
    Path(base["HOME"]).mkdir(parents=True, exist_ok=True)
    return base


class UnconfiguredTests(unittest.TestCase):
    def test_unconfigured_status_asks_exactly_two_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = cfg.status(env=env_for(tmp), platform=POSIX)
            self.assertEqual("CONFIG_REQUIRED", status["status"])
            self.assertFalse(status["configured"])
            self.assertEqual(2, len(status["questions"]))
            self.assertIn("研究数据", status["questions"][0])
            self.assertIn("经验", status["questions"][1])
            self.assertNotIn("data_root", status)

    def test_unconfigured_data_root_raises_structured_config_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(cfg.ConfigError) as ctx:
                cfg.data_root(env=env_for(tmp), platform=POSIX)
            self.assertEqual("CONFIG_REQUIRED", ctx.exception.code)

    def test_unconfigured_never_falls_back_into_the_plugin_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(cfg.ConfigError):
                cfg.data_root(env=env_for(tmp), platform=POSIX)
            # Nothing was silently created next to the plugin code.
            self.assertFalse((ROOT / "data").exists())
            self.assertFalse((ROOT / "state").exists())


class ConfigureTests(unittest.TestCase):
    def test_configure_persists_both_paths_and_status_reads_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            data = Path(tmp) / "research-data"
            exp = Path(tmp) / "Research_Experience.md"
            result = cfg.configure(str(data), str(exp), env=env, platform=POSIX)
            self.assertEqual("CONFIGURED", result["status"])
            self.assertTrue(Path(result["config_file"]).is_file())
            saved = json.loads(Path(result["config_file"]).read_text(encoding="utf-8"))
            self.assertEqual(cfg.CONFIG_VERSION, saved["version"])
            self.assertEqual(str(data.resolve()), saved["data_root"])
            self.assertEqual(str(exp.resolve()), saved["experience_file"])
            status = cfg.status(env=env, platform=POSIX)
            self.assertEqual("CONFIGURED", status["status"])
            self.assertEqual(str(data.resolve()), status["data_root"])
            self.assertEqual(str(exp.resolve()), status["experience_file"])

    def test_paths_are_reusable_after_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            data, exp = Path(tmp) / "d", Path(tmp) / "Research_Experience.md"
            cfg.configure(str(data), str(exp), env=env, platform=POSIX)
            # A fresh interpreter has no in-process state; only the JSON matters.
            self.assertEqual(data.resolve(), cfg.data_root(env=env, platform=POSIX))
            self.assertEqual(exp.resolve(), cfg.experience_file(env=env, platform=POSIX))

    def test_reconfiguring_to_different_paths_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            cfg.configure(str(Path(tmp) / "d1"), str(Path(tmp) / "a.md"),
                          env=env, platform=POSIX)
            with self.assertRaises(cfg.ConfigError) as ctx:
                cfg.configure(str(Path(tmp) / "d2"), str(Path(tmp) / "b.md"),
                              env=env, platform=POSIX)
            self.assertEqual("RECONFIGURE_REQUIRES_CONFIRMATION", ctx.exception.code)
            # The old paths are intact.
            self.assertEqual((Path(tmp) / "d1").resolve(),
                             cfg.data_root(env=env, platform=POSIX))
            cfg.configure(str(Path(tmp) / "d2"), str(Path(tmp) / "b.md"),
                          confirm=True, env=env, platform=POSIX)
            self.assertEqual((Path(tmp) / "d2").resolve(),
                             cfg.data_root(env=env, platform=POSIX))

    def test_same_paths_reconfigure_is_idempotent_without_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            data, exp = str(Path(tmp) / "d"), str(Path(tmp) / "a.md")
            cfg.configure(data, exp, env=env, platform=POSIX)
            again = cfg.configure(data, exp, env=env, platform=POSIX)
            self.assertEqual("CONFIGURED", again["status"])
            self.assertTrue(again["unchanged"])


class PathValidationTests(unittest.TestCase):
    def test_data_root_and_experience_file_must_be_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            data = Path(tmp) / "research"
            with self.assertRaises(cfg.ConfigError) as ctx:
                cfg.configure(str(data), str(data / "Research_Experience.md"),
                              env=env, platform=POSIX)
            self.assertEqual("EXPERIENCE_FILE_INSIDE_DATA_ROOT", ctx.exception.code)

    def test_refuses_a_data_root_inside_a_git_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            checkout = Path(tmp) / "clone"
            (checkout / ".git").mkdir(parents=True)
            with self.assertRaises(cfg.ConfigError) as ctx:
                cfg.configure(str(checkout / "data"), str(Path(tmp) / "a.md"),
                              env=env, platform=POSIX)
            self.assertEqual("PATH_INSIDE_GIT_CHECKOUT", ctx.exception.code)

    def test_refuses_relative_or_empty_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            for bad in ("", "   ", "relative/dir"):
                with self.assertRaises(cfg.ConfigError) as ctx:
                    cfg.configure(bad, str(Path(tmp) / "a.md"), env=env, platform=POSIX)
                self.assertEqual("PATH_NOT_ABSOLUTE", ctx.exception.code)

    def test_accepts_a_windows_absolute_path(self):
        # The path is recognised as absolute on Windows without touching the OS.
        self.assertTrue(cfg.is_absolute_path(r"C:\Users\member-a\research", "win32"))
        self.assertTrue(cfg.is_absolute_path(r"\\server\share\research", "win32"))
        self.assertFalse(cfg.is_absolute_path(r"research\data", "win32"))
        self.assertTrue(cfg.is_absolute_path("/home/member-a/research", "linux"))

    def test_windows_config_file_lives_under_appdata(self):
        path = cfg.config_path(env={"APPDATA": r"C:\Users\member-a\AppData\Roaming"}, platform="win32")
        self.assertEqual(r"C:\Users\member-a\AppData\Roaming\marketops-research\config.json",
                         str(path).replace("/", "\\"))

    def test_unix_config_file_lives_under_xdg(self):
        path = cfg.config_path(env={"HOME": "/home/member-a", "XDG_CONFIG_HOME": "/home/member-a/.config"},
                               platform="linux")
        self.assertEqual("/home/member-a/.config/marketops-research/config.json", str(path))


class ExistingFileTests(unittest.TestCase):
    def test_refuses_to_overwrite_a_foreign_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            foreign = Path(tmp) / "Research_Experience.md"
            foreign.write_text("# My own notes\n\nnot a marketops file\n", encoding="utf-8")
            with self.assertRaises(cfg.ConfigError) as ctx:
                cfg.configure(str(Path(tmp) / "d"), str(foreign), env=env, platform=POSIX)
            self.assertEqual("EXPERIENCE_FILE_NOT_MARKETOPS", ctx.exception.code)
            self.assertIn("My own notes", foreign.read_text(encoding="utf-8"))

    def test_accepts_an_existing_marketops_experience_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            mine = Path(tmp) / "Research_Experience.md"
            mine.write_text(cfg.EXPERIENCE_MARKER + "\n\n# Research Experience\n",
                            encoding="utf-8")
            result = cfg.configure(str(Path(tmp) / "d"), str(mine), env=env, platform=POSIX)
            self.assertEqual("CONFIGURED", result["status"])


class CompatibilityTests(unittest.TestCase):
    def test_explicit_data_root_env_still_wins_for_existing_deployments(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp, MARKETOPS_RESEARCH_DATA_ROOT=str(Path(tmp) / "from-env"))
            cfg.configure(str(Path(tmp) / "from-config"), str(Path(tmp) / "a.md"),
                          env=env, platform=POSIX)
            self.assertEqual((Path(tmp) / "from-env").resolve(),
                             cfg.data_root(env=env, platform=POSIX))
            self.assertEqual("env", cfg.status(env=env, platform=POSIX)["data_root_source"])

    def test_explicit_config_env_selects_a_different_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            custom = Path(tmp) / "custom-config.json"
            env["MARKETOPS_RESEARCH_CONFIG"] = str(custom)
            cfg.configure(str(Path(tmp) / "d"), str(Path(tmp) / "a.md"),
                          env=env, platform=POSIX)
            self.assertTrue(custom.is_file())
            self.assertFalse((Path(env["XDG_CONFIG_HOME"]) / "marketops-research" / "config.json").exists())
            self.assertEqual(custom.resolve(), cfg.config_path(env=env, platform=POSIX).resolve())


class RedactionTests(unittest.TestCase):
    def test_status_redacts_the_home_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = env_for(tmp)
            home = Path(env["HOME"])
            cfg.configure(str(home / "research"), str(home / "Research_Experience.md"),
                          env=env, platform=POSIX)
            status = cfg.status(env=env, platform=POSIX, redact=True)
            self.assertNotIn(str(home), json.dumps(status))
            self.assertIn("~", status["data_root"])
            # Without redaction the real paths come back (local CLI/debug use).
            plain = cfg.status(env=env, platform=POSIX)
            self.assertEqual(str((home / "research").resolve()), plain["data_root"])


if __name__ == "__main__":
    unittest.main()

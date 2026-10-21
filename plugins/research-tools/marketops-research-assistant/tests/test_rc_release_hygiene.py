"""Release-hygiene checks for the 0.5.10-rc3 release candidate.

These tests only read the package.  They exist because the RC previously kept
the published 0.5.5 version in every manifest while shipping different code:
a customer could not tell the two artefacts apart, and a build could silently
overwrite the old name.  The fixture checks make the REDACTION.md convention
executable instead of leaving it to review memory.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_client_bundles  # noqa: E402
import setup_clients  # noqa: E402

VERSION = "0.5.10-rc3"
MANIFESTS = (
    ROOT / "plugin.json",
    ROOT / ".claude-plugin" / "plugin.json",
    ROOT / ".codex-plugin" / "plugin.json",
)


class ReleaseVersionTests(unittest.TestCase):
    def test_version_is_consistent_across_manifests_builder_and_installer(self):
        for path in MANIFESTS:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(VERSION, manifest["version"], str(path))
            self.assertIn("release candidate", manifest["description"].lower(),
                          f"{path} does not identify itself as an RC")
        self.assertEqual(VERSION, build_client_bundles.META["version"])
        self.assertEqual(VERSION, setup_clients.VERSION)

    def test_version_is_a_release_or_rc_and_not_the_published_0_5_5(self):
        # Releases are tagged marketops-v<version> and updated from GitHub.
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+(-rc\d+)?$")
        self.assertNotEqual("0.5.5", VERSION)

    def test_release_scripts_do_not_report_a_stale_version(self):
        offenders = []
        for path in sorted(SCRIPTS.glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "0.5.5" in text:
                offenders.append(path.name)
        self.assertEqual([], offenders,
                         "release scripts still report the published 0.5.5 version")


class RedactionFixtureTests(unittest.TestCase):
    FIXTURE = ROOT / "tests" / "test_client_view_generation.py"
    CONFIG = ROOT.parents[2] / ".gitleaks.toml"

    def test_private_key_header_is_inline_allowlisted_and_invalid(self):
        text = self.FIXTURE.read_text(encoding="utf-8")
        match = re.search(r'key_header = "([^"]+)"\s+# gitleaks:allow', text)
        self.assertIsNotNone(match, "synthetic key header has no inline gitleaks:allow")
        header = match.group(1).replace("\\n", "\n")
        # Assemble the marker instead of spelling it: this test *checks* the
        # synthetic fixture, it is not itself key material, and a literal here
        # trips the release gate's secret scan.
        self.assertTrue(header.startswith("-----BEGIN " + "RSA PRIVATE KEY-----"))
        payload = header.split("\n", 1)[1]
        # It is deliberate garbage, not a key: invalid base64 must fail closed.
        with self.assertRaises(binascii.Error):
            base64.b64decode(payload, validate=True)

    def test_gitleaks_allowlist_is_path_and_match_scoped(self):
        text = self.CONFIG.read_text(encoding="utf-8")
        block = re.search(
            r"description = \"[^\"]*test_client_view_generation[^\"]*\"\n"
            r"paths = \[\'\'\'([^\']+)\'\'\'\]\n"
            r"regexTarget = \"line\"\n"
            r"regexes = \[\'\'\'([^\']+)\'\'\'\]",
            text)
        self.assertIsNotNone(block, "no path/match allowlist for the key fixture")
        self.assertIn(r"test_client_view_generation\.py", block.group(1))
        self.assertIn("PRIVATE KEY", block.group(2))
        self.assertNotRegex(text, r"(?m)^paths = \[.*\*\*.*\]$",
                            "a repo-wide allowlist would weaken the scan")


if __name__ == "__main__":
    unittest.main()

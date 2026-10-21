#!/usr/bin/env python3
"""Update MarketOps from its GitHub Releases, check the new version, then switch.

    Update.cmd                 update to the newest stable release
    Update.cmd --check         only say whether a newer release exists
    Update.cmd --prerelease    also consider pre-releases (release candidates)
    Update.cmd --rollback      go back to the previous installed version

The new version is installed beside the one in use (<root>/releases/<version>),
with its own Python environment. Only when its own check passes does
<root>/current move to it; otherwise nothing changes. Research data, keys and the
experience file live outside <root> and are not touched. Previous versions are
kept for --rollback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import release_layout  # noqa: E402

API = "https://api.github.com/repos/{repo}/releases?per_page=30"
TAG = re.compile(r"^marketops-v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")
PLUGIN = "marketops-research-assistant"


def version_key(text: str):
    """0.5.8 > 0.5.8-rc7 > 0.5.7; None for anything else."""
    match = TAG.match("marketops-v" + text.removeprefix("marketops-v").removeprefix("v"))
    if not match:
        return None
    major, minor, patch, rc = match.groups()
    return (int(major), int(minor), int(patch), int(rc) if rc else 10**6)


def installed_version(root: Path) -> str:
    manifest = release_layout.current_link(root) / "plugins" / PLUGIN / "plugin.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def _get(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "marketops-updater"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - https only
        return response.read()


def latest_release(repo: str, prerelease: bool, get=None) -> dict | None:
    releases = json.loads((get or _get)(API.format(repo=repo)))
    best = None
    for release in releases:
        tag = TAG.fullmatch(release.get("tag_name", ""))
        if tag is None or (tag.group(4) and not prerelease):
            continue
        if release.get("draft") or (release.get("prerelease") and not prerelease):
            continue
        key = version_key(release.get("tag_name", ""))
        if key and (best is None or key > best[0]):
            best = (key, release)
    return best[1] if best else None


def _asset(release: dict, suffix: str) -> dict:
    version = release["tag_name"].removeprefix("marketops-v")
    expected = f"{PLUGIN}-v{version}{suffix}"
    for asset in release.get("assets", []):
        if asset["name"] == expected:
            return asset
    raise ValueError(f"RELEASE_ASSET_MISSING: {suffix}")


def download(release: dict, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    package, digest = _asset(release, "-source.zip"), _asset(release, "-source.zip.sha256")
    zip_path = folder / package["name"]
    zip_path.write_bytes(_get(package["browser_download_url"], timeout=600))
    expected = _get(digest["browser_download_url"]).decode("ascii").split()[0].strip().lower()
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if actual != expected:
        zip_path.unlink()
        raise ValueError("DOWNLOAD_SHA256_MISMATCH: the package was not what the release published")
    return zip_path


def self_check(version_dir: Path) -> dict:
    """The new version's own interpreter must start the MCP and list its tools."""
    python = version_dir / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    scripts = version_dir / "plugins" / PLUGIN / "scripts"
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import research_mcp; "
             "tools = research_mcp.build_server()._tool_manager._tools; print(len(tools))")
    done = subprocess.run([str(python), "-c", probe, str(scripts)], capture_output=True,
                          text=True, timeout=180)
    tools = int(done.stdout.strip() or 0) if done.returncode == 0 else 0
    return {"ok": done.returncode == 0 and tools >= 20, "tools": tools}


def install(root: Path, release: dict, settings: dict) -> Path:
    if not TAG.fullmatch(release.get("tag_name", "")):
        raise ValueError("RELEASE_TAG_INVALID")
    version = release["tag_name"].removeprefix("marketops-v")
    work = root / "downloads" / version
    zip_path = download(release, work)
    source = work / "src"
    shutil.rmtree(source, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(source)
    installer = source / PLUGIN / "scripts" / "setup_clients.py"
    target = root / "releases" / version
    command = [sys.executable, str(installer), "--destination", str(target),
               "--client", settings.get("client", "both"), "--apply", "--no-switch-current"]
    runtime = settings.get("runtime", {"dependencies": True})
    for key, flag in (("dependencies", "--install-deps"),
                      ("local_crawl", "--install-local-crawl"),
                      ("browser", "--install-crawl-browser")):
        if runtime.get(key):
            command.append(flag)
    if target.exists():
        command.append("--resume")
    done = subprocess.run(command, capture_output=True, text=True)
    (work / "install.log").write_text(done.stdout[-20000:], encoding="utf-8")
    if done.returncode != 0:
        raise ValueError("NEW_VERSION_INSTALL_FAILED: see " + str(work / "install.log"))
    manifest = target / "plugins" / PLUGIN / "plugin.json"
    if json.loads(manifest.read_text(encoding="utf-8")).get("version") != version:
        raise ValueError("NEW_VERSION_MANIFEST_MISMATCH")
    return target


def reregister(root: Path, settings: dict) -> str:
    """Codex caches plugins: tell it the plugin at <root>/current has changed."""
    if not settings.get("register_codex") or shutil.which("codex") is None:
        return "NOT_REGISTERED_WITH_CODEX"
    stable = str(release_layout.current_link(root))
    subprocess.run(["codex", "plugin", "marketplace", "add", stable], capture_output=True)
    added = subprocess.run(["codex", "plugin", "add", f"{PLUGIN}@marketops-local", "--json"],
                           capture_output=True)
    return "CODEX_REFRESHED" if added.returncode == 0 else "CODEX_REFRESH_NEEDS_HOST"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--prerelease", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)
    root = args.app_root.resolve()
    try:
        settings = json.loads((root / "update-settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {"repository": release_layout.REPO, "client": "both"}
    current = installed_version(root)

    if args.rollback:
        # A directory can be a failed staged install. Roll back only to the
        # version that was active before a successful update, never the highest
        # directory name left behind by a failed attempt.
        back = settings.get("previous_version", "")
        if not version_key(back) or not (root / "releases" / back).is_dir():
            print(json.dumps({"status": "NO_EARLIER_VERSION", "current": current}))
            return 1
        if not self_check(root / "releases" / back)["ok"]:
            print(json.dumps({"status": "ROLLBACK_VERSION_FAILED_CHECK", "current": current}))
            return 2
        release_layout.switch_current(root, root / "releases" / back)
        settings.pop("previous_version", None)
        _save_settings(root, settings)
        print(json.dumps({"status": "ROLLED_BACK", "from": current, "to": back,
                          "codex": reregister(root, settings),
                          "next": "restart your agent (Codex / WorkBuddy / Claude Code)"}))
        return 0

    prerelease = args.prerelease or settings.get("channel") == "prerelease"
    release = latest_release(settings.get("repository", release_layout.REPO), prerelease)
    newest = release["tag_name"].removeprefix("marketops-v") if release else None
    newer = bool(newest and version_key(newest) > version_key(current))
    if args.check or not newer:
        print(json.dumps({"status": "UPDATE_AVAILABLE" if newer else "UP_TO_DATE",
                          "current": current, "latest": newest,
                          "notes": release.get("html_url") if release else None}))
        return 0

    target = install(root, release, settings)
    check = self_check(target)
    if not check["ok"]:
        print(json.dumps({"status": "NEW_VERSION_FAILED_CHECK_NOTHING_CHANGED",
                          "current": current, "tried": newest, "check": check}))
        return 2
    switched = release_layout.switch_current(root, target)
    settings["previous_version"] = current
    _save_settings(root, settings)
    print(json.dumps({"status": "UPDATED", "from": current, "to": newest,
                      "tools": check["tools"], "codex": reregister(root, settings),
                      "previous_kept_for_rollback": switched["previous"],
                      "next": "restart your agent (Codex / WorkBuddy / Claude Code); "
                              "WorkBuddy: if it still shows the old version, re-import the "
                              "connector from <root>/current/connectors once"}, ensure_ascii=False))
    return 0


def _save_settings(root: Path, settings: dict) -> None:
    path = root / "update-settings.json"
    staged = path.with_suffix(".tmp")
    staged.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    staged.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())

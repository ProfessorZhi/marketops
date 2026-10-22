#!/usr/bin/env python3
"""One stable path for every host, many versions behind it.

    <app root>/releases/<version>/   a complete install (its own venv)
    <app root>/current               -> the version in use (a directory junction on
                                        Windows, a symlink elsewhere)
    <app root>/Update.cmd, update.sh  the GitHub updater

Hosts (Codex, WorkBuddy, Claude Code, DeepSeek Harness) are given paths under
``current``, so an update installs the next version beside the old one, checks
it, then moves ``current``: no host has to be re-pointed, and the previous
version stays for a rollback. Research data, keys and the experience file live
outside the app root and are never touched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

__all__ = ["app_root_for", "current_link", "rebase_host_files", "switch_current",
           "write_update_entrypoints", "write_repair_entrypoints", "installed_versions"]

HOST_FILES = ("mcp.json", ".mcp.json")
REPO = "ProfessorZhi/marketops"


def app_root_for(destination: Path) -> Path | None:
    """The app root when the install sits in <root>/releases/<version>, else None."""
    destination = Path(destination)
    return destination.parent.parent if destination.parent.name == "releases" else None


def current_link(app_root: Path) -> Path:
    return Path(app_root) / "current"


def _rewrite(path: Path, old: str, new: str) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    # The same path appears as written, JSON-escaped (doubled backslashes), and with
    # forward slashes; each spelling is replaced by the same spelling of `new`.
    pairs = [(old.replace("\\", "\\\\"), new.replace("\\", "\\\\")),
             (old, new),
             (old.replace("\\", "/"), new.replace("\\", "/"))]
    changed = text
    for before, after in pairs:
        if before:
            changed = changed.replace(before, after)
    if changed != text:
        path.write_text(changed, encoding="utf-8")
        return True
    return False


def rebase_host_files(destination: Path, plugin_dir: Path) -> list[str]:
    """Point every host-facing file of this version at ``current`` instead.

    The receipt and the install state keep the real path: they describe this
    version. What a host reads names the stable path, so it survives an update.
    """
    # The builder resolves its output. Normalize Windows short-name aliases
    # here too so the same directory is not mistaken for a different prefix.
    original = str(destination)
    destination = Path(destination).resolve()
    root = app_root_for(destination)
    if root is None:
        return []
    real, stable = str(Path(destination)), str(current_link(root))
    files = [plugin_dir / name for name in HOST_FILES]
    files += [Path(destination) / "GENERIC_MCP.json", Path(destination) / "START_HERE.md"]
    connectors = Path(destination) / "connectors"
    if connectors.is_dir():
        files += [p for p in connectors.rglob("*") if p.is_file() and p.suffix in {".json", ".md"}]
    changed = []
    for path in files:
        if not path.is_file():
            continue
        rewritten = _rewrite(path, real, stable)
        if original != real:
            rewritten = _rewrite(path, original, stable) or rewritten
        if rewritten:
            changed.append(str(path))
    return changed


def switch_current(app_root: Path, version_dir: Path) -> dict:
    """Point ``current`` at ``version_dir``; the old target is reported for rollback."""
    link = current_link(app_root)
    version_dir = Path(version_dir)
    if (not version_dir.is_dir()
            or not version_dir.resolve().is_relative_to((Path(app_root) / "releases").resolve())):
        raise ValueError("RELEASE_TARGET_INVALID")
    if link.exists() and not (link.is_symlink() or link.is_junction()):
        raise ValueError("CURRENT_IS_NOT_A_LINK")
    previous = None
    if link.exists() or link.is_symlink():
        try:
            previous = str(Path(os.path.realpath(link)))
        except OSError:
            previous = None
    staged = link.with_name("current.next")
    _remove_link(staged)
    _make_link(staged, Path(version_dir))
    if sys.platform != "win32":
        # Replacing a symlink is atomic on POSIX; never unlink the working one first.
        os.replace(staged, link)
    else:
        # Windows cannot replace an existing directory junction. Keep the old
        # junction under a backup name until the replacement succeeds.
        backup = link.with_name("current.previous")
        _remove_link(backup)
        had_link = link.exists() or link.is_symlink()
        if had_link:
            link.rename(backup)
        try:
            staged.rename(link)
        except OSError:
            if had_link:
                backup.rename(link)
            _remove_link(staged)
            raise
        _remove_link(backup)
    return {"current": str(link), "now": str(version_dir), "previous": previous}


def _make_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        # A directory junction needs no administrator rights or developer mode.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        os.symlink(str(target), str(link), target_is_directory=True)


def _remove_link(link: Path) -> None:
    if not (link.exists() or link.is_symlink() or link.is_junction()):
        return
    if not (link.is_symlink() or link.is_junction()):
        raise ValueError("CURRENT_IS_NOT_A_LINK")
    if sys.platform == "win32":
        os.rmdir(link)  # removes the junction itself, never its target's files
    else:
        link.unlink()


def installed_versions(app_root: Path) -> list[str]:
    releases = Path(app_root) / "releases"
    return sorted(p.name for p in releases.iterdir() if p.is_dir()) if releases.is_dir() else []


def write_update_entrypoints(app_root: Path, *, client: str, register_codex: bool,
                             channel: str = "stable", runtime: dict | None = None) -> list[str]:
    """Update.cmd / update.sh at the app root, plus the settings they read."""
    root = Path(app_root)
    settings = {"repository": REPO, "channel": channel, "client": client,
                "register_codex": bool(register_codex)}
    if runtime is not None:
        settings["runtime"] = runtime
    (root / "update-settings.json").write_text(json.dumps(settings, indent=2) + "\n",
                                               encoding="utf-8")
    script = r"current\plugins\marketops-research-assistant\scripts\update_marketops.py"
    (root / "Update.cmd").write_text(
        "@echo off\r\nsetlocal\r\n"
        f'"%~dp0current\\.venv\\Scripts\\python.exe" "%~dp0{script}" --app-root "%~dp0." %*\r\n'
        "set \"MARKETOPS_UPDATE_EXIT=%ERRORLEVEL%\"\r\n"
        "if not \"%MARKETOPS_NO_PAUSE%\"==\"1\" pause\r\nexit /b %MARKETOPS_UPDATE_EXIT%\r\n",
        encoding="utf-8")
    update_sh = root / "update.sh"
    update_sh.write_text(
        "#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\n"
        'exec "$root/current/.venv/bin/python" '
        '"$root/current/plugins/marketops-research-assistant/scripts/update_marketops.py" '
        '--app-root "$root" "$@"\n', encoding="utf-8")
    update_sh.chmod(0o755)
    return [str(root / "Update.cmd"), str(update_sh), str(root / "update-settings.json"),
            *write_repair_entrypoints(root)]


def write_repair_entrypoints(app_root: Path) -> list[str]:
    """Repair.cmd / repair.sh: reinstall Crawl4AI and its browser into the current release.

    Also written by the updater and by a starting server when missing, so an install
    from before they existed gets them without reinstalling.
    """
    root = Path(app_root)
    doctor = r"current\plugins\marketops-research-assistant\scripts\research_crawl_doctor.py"
    (root / "Repair.cmd").write_text(
        "@echo off\r\nsetlocal\r\n"
        f'"%~dp0current\\.venv\\Scripts\\python.exe" "%~dp0{doctor}" repair\r\n'
        "set \"MARKETOPS_REPAIR_EXIT=%ERRORLEVEL%\"\r\n"
        "if not \"%MARKETOPS_NO_PAUSE%\"==\"1\" pause\r\nexit /b %MARKETOPS_REPAIR_EXIT%\r\n",
        encoding="utf-8")
    repair_sh = root / "repair.sh"
    repair_sh.write_text(
        "#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- \"$(dirname -- \"$0\")\" && pwd)\n"
        'exec "$root/current/.venv/bin/python" '
        '"$root/current/plugins/marketops-research-assistant/scripts/research_crawl_doctor.py" '
        'repair\n', encoding="utf-8")
    repair_sh.chmod(0o755)
    return [str(root / "Repair.cmd"), str(repair_sh)]

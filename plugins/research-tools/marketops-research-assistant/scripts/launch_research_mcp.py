#!/usr/bin/env python3
"""OS-neutral launcher for the managed MCP server.

`launch_research_mcp.sh` does the same job and is the POSIX entry `--plugin-dir`
users get. It cannot be the only one: Windows has no `sh`, and a host config can
name exactly one `command`, so there is no single token that starts this server
on both families (`sh` does not exist on Windows; `python` is not guaranteed on
Linux). The OS-neutral mechanism is therefore not a cleverer command string — it
is letting the *installer* resolve an absolute interpreter path at install time,
which it already does, and giving everyone else this file to point at.

Usage from a Codex plugin config:

    command: python (or another bare host Python command)
    args:    ["<plugin>/scripts/launch_research_mcp.py", "scripts/research_mcp.py"]
    env:     {"MARKETOPS_RESEARCH_PYTHON": "<absolute path to the installed venv>"}

Other clients may call this launcher from an absolute Python path directly.

Behaviour:

* If the interpreter already running this file can `import mcp` and there is no
  explicit `MARKETOPS_RESEARCH_PYTHON`, the server runs in this process.
* Otherwise it probes, in order: `MARKETOPS_RESEARCH_PYTHON` (must be absolute),
  the plugin-local venvs the installer creates, then `python3` / `python` /
  Windows' `py -3.12` on PATH. The first one that can `import mcp` wins.
* Nothing is installed, downloaded, or read from a credential store.

Exit codes: 0 when the server ran and returned, 78 for the launcher's own
refusals (`MARKETOPS_*` codes on stderr), otherwise the server's own status.
"""
from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SERVER = HERE / "research_mcp.py"
DEFAULT_ENTRY = "research_mcp_facade.py"

#: Names tried on PATH after the explicit override and the plugin-local venvs.
#: `py` is the Windows Python launcher and is deliberately probed with `-3.12`.
PATH_CANDIDATES = (("python3.12",), ("python3",), ("python",), ("py", "-3.12"))


def _usable(command: list[str]) -> bool:
    """True when `command` runs and can import the MCP SDK."""
    try:
        return subprocess.run([*command, "-c", "import mcp"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def candidates() -> list[list[str]]:
    """Every interpreter command this launcher would try, in order.

    Returned as argv lists because one candidate is a two-token command
    (`py -3.12`), which a plain "which" cannot express.
    """
    probes: list[list[str]] = []
    override = os.environ.get("MARKETOPS_RESEARCH_PYTHON", "").strip()
    if override:
        probes.append([override])
    # The installer's venv, on either layout: bin/ on POSIX, Scripts/ on Windows.
    for relative in ("../.venv/bin/python", "../../.venv/bin/python", "../../../.venv/bin/python",
                     "../.venv/Scripts/python.exe", "../../.venv/Scripts/python.exe", "../../../.venv/Scripts/python.exe"):
        candidate = (HERE / relative).resolve()
        if candidate.is_file():
            probes.append([str(candidate)])
    for names in PATH_CANDIDATES:
        found = shutil.which(names[0])
        if found:
            probes.append([found, *names[1:]])
    return probes


def choose() -> list[str] | None:
    """The first candidate that can import `mcp`, or None."""
    for probe_ in candidates():
        if _usable(probe_):
            return probe_
    return None


def main(argv: list[str]) -> int:
    flags = {token for token in argv[1:] if token.startswith("--")}
    positional = [token for token in argv[1:] if not token.startswith("--")]

    # An override that is not absolute must not silently fall through to some
    # other interpreter: the operator asked for *this* one.
    override = os.environ.get("MARKETOPS_RESEARCH_PYTHON", "").strip()
    if override and not os.path.isabs(override):
        print("MARKETOPS_PYTHON_OVERRIDE_NOT_ABSOLUTE", file=sys.stderr)
        return 78

    if "--list" in flags:
        for probe_ in candidates():
            print(("ok   " if _usable(probe_) else "no   ") + " ".join(probe_))
        return 0

    if "--check" in flags:
        chosen = choose()
        if chosen is None:
            print("MARKETOPS_MCP_INTERPRETER_UNAVAILABLE: no candidate can import mcp",
                  file=sys.stderr)
            return 78
        print(" ".join(chosen))
        return 0

    # The entry is resolved against the *package*, never against the caller's
    # working directory: a copied connector whose relative path happened to
    # resolve somewhere else must not silently start a different tree. And an
    # entry outside this package is refused outright — it is the one shape of
    # "run this other file" that a shipped launcher has no reason to allow.
    package_root = HERE.parent.resolve()
    entry = Path(positional[0]) if positional else DEFAULT_SERVER
    server = (entry if entry.is_absolute() else package_root / entry).resolve()
    try:
        server.relative_to(package_root)
    except ValueError:
        print(f"MARKETOPS_MCP_ENTRY_OUTSIDE_PACKAGE: {server}", file=sys.stderr)
        return 78
    if not server.is_file():
        print("MARKETOPS_MCP_ENTRY_MISSING", file=sys.stderr)
        return 78

    if override:
        if not _usable([override]):
            print("MARKETOPS_MCP_OVERRIDE_CANNOT_IMPORT_MCP", file=sys.stderr)
            return 78
        return subprocess.run([override, str(server)]).returncode

    # The interpreter running this file is already a Python; if it has the SDK
    # there is no reason to hand the work to a second one.
    if _usable([sys.executable]):
        runpy.run_path(str(server), run_name="__main__")
        return 0

    chosen = choose()
    if chosen is None:
        print("MARKETOPS_MCP_INTERPRETER_UNAVAILABLE: configure a private Python "
              "venv with MCP and MARKETOPS_RESEARCH_PYTHON", file=sys.stderr)
        return 78
    return subprocess.run([*chosen, str(server)]).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

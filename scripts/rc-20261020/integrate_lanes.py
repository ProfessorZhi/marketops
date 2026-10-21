#!/usr/bin/env python3
"""Cherry-pick exactly the Lane commits named in ``lanes.json`` onto the RC branch.

The script is deliberately *not* a merge tool.  It refuses to guess a commit, it
refuses to run on a dirty tree, and a conflict aborts the single in-flight
cherry-pick and stops.  It never resets, cleans, stashes, force-pushes or touches
another worktree.

Usage::

    python scripts/rc-20261020/integrate_lanes.py --dry-run
    python scripts/rc-20261020/integrate_lanes.py
    python scripts/rc-20261020/integrate_lanes.py --only A --only B

Exit codes: 0 integrated (or dry-run valid), 2 precondition failed,
3 conflict aborted and stopped, 4 no commits to integrate.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "docs" / "rc-20261020" / "lanes.json"
LEDGER = REPO / "docs" / "rc-20261020" / "LANE-LEDGER.md"


def git(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(REPO), *args], text=True,
                          capture_output=True, check=check)


def fail(message: str, code: int = 2) -> None:
    print(f"INTEGRATION_BLOCKED: {message}", file=sys.stderr)
    raise SystemExit(code)


def require(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def clean_worktree() -> None:
    status = git("status", "--porcelain").stdout.strip()
    require(not status, "RC worktree is not clean; commit or remove the changes first:\n" + status)


def validate_commit(commit: str, baseline: str) -> None:
    probe = git("cat-file", "-e", f"{commit}^{{commit}}")
    require(probe.returncode == 0, f"commit not found: {commit}")
    ancestor = git("merge-base", "--is-ancestor", baseline, commit)
    require(ancestor.returncode == 0,
            f"{commit} does not descend from baseline {baseline}; refusing to pick")


def append_ledger(lane: dict, source_sha: str, rc_sha: str, subject: str) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    if not LEDGER.exists():
        LEDGER.write_text(
            "# Lane commit ledger (2026-10-20)\n\n"
            "| lane | source branch | source SHA | RC SHA | subject |\n"
            "| --- | --- | --- | --- | --- |\n", encoding="utf-8")
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(f"| {lane['id']} | `{lane['branch']}` | `{source_sha}` | "
                     f"`{rc_sha}` | {subject.replace('|', '/')} |\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--only", action="append", choices=["A", "B", "C", "D", "E"],
                        help="integrate only this lane (repeatable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate commits and order without picking")
    args = parser.parse_args(argv)

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    baseline = manifest["baseline"]
    rc_branch = manifest["rc_branch"]

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    require(branch == rc_branch, f"HEAD is {branch!r}, expected {rc_branch!r}")
    require(git("rev-parse", "--verify", baseline).returncode == 0,
            f"baseline {baseline} is not present in this repository")
    clean_worktree()

    selected = [lane for lane in manifest["lanes"]
                if not args.only or lane["id"] in args.only]
    pending = [(lane, commit) for lane in selected for commit in lane["commits"]]
    if not pending:
        print("INTEGRATION_PENDING: no Lane commit has been delivered yet; "
              "nothing was picked and the RC branch is unchanged.", file=sys.stderr)
        return 4

    for lane, commit in pending:
        validate_commit(commit, baseline)
        print(f"validated {lane['id']} {commit} ({lane['branch']})")

    if args.dry_run:
        print(f"DRY_RUN_OK: {len(pending)} commit(s) would be picked in lane order.")
        return 0

    picked = 0
    for lane, commit in pending:
        pick = git("cherry-pick", commit)
        if pick.returncode != 0:
            conflict = git("diff", "--name-only", "--diff-filter=U").stdout.strip()
            git("cherry-pick", "--abort")
            print("INTEGRATION_CONFLICT: stopped; the in-flight cherry-pick was aborted.\n"
                  f"lane={lane['id']} source_sha={commit}\n"
                  f"conflicted files:\n{conflict or '(git reported none)'}\n"
                  f"git stderr:\n{pick.stderr.strip()}", file=sys.stderr)
            return 3
        rc_sha = git("rev-parse", "HEAD").stdout.strip()
        subject = git("log", "-1", "--format=%s", rc_sha).stdout.strip()
        append_ledger(lane, commit, rc_sha, subject)
        picked += 1
        print(f"picked {lane['id']} {commit} -> {rc_sha}")

    print(f"INTEGRATED: {picked} commit(s). Ledger: {LEDGER.relative_to(REPO)}")
    print("Next: review the diff, then run the release gate. "
          "The ledger is uncommitted on purpose; commit it separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

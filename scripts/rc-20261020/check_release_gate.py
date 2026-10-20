#!/usr/bin/env python3
"""Offline release gate for the MarketOps RC branch.

Every check runs locally with billable network switched off.  Nothing is written
inside the repository: build artefacts go to a temporary directory, and the JSON
report goes to ``--evidence-dir`` (which must be outside the repository).

The gate reports one of PASS / FAIL / BLOCKED / BASELINE_RED_NOT_NEW per check.
A missing Lane test is BLOCKED, never a skip dressed up as success.

Typical use (run the full plugin suite exactly once):

    $TOOLCHAINS_ROOT/marketops-research-plugin-e2e/bin/python \
        scripts/rc-20261020/check_release_gate.py \
        --full-suite --baseline-failures 23 \
        --evidence-dir $ARTIFACTS_ROOT/skills/marketops-rc/20261020
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "research-tools" / "marketops-research-assistant"
SCRIPTS = PLUGIN / "scripts"
BASELINE = "4f87bc3306b90d55bfd0332ef1efc8b97f1dcc44"
#: The exact Lane-H commit the RC3 candidate branches from.  Baseline ancestry
#: alone would still pass if the branch had quietly been rebuilt from an older
#: point; this check pins the *base* the integrator was told to use.
RC3_BASE = "38dcef853f526f64f669c475c96ad9c3bad3ad47"
#: The single product development branch is the one PR #22 already has. It keeps
#: its rc2 *name* while the product version advances to rc3; creating a second
#: branch for a version bump is exactly the split this check exists to prevent.
RC3_BRANCH = "rc/marketops-0.5.6-rc2-20261020"

#: Lane test modules, in the order they must be green.  ``None`` means the Lane
#: has not named its test yet; that is BLOCKED rather than silently omitted.
LANE_TESTS = (
    ("G1-A", "delivery-admission-gate", ["test_research_delivery.py",
                                         "test_delivery_admission_gate.py"]),
    ("G2-B", "client-view", ["test_client_view_generation.py"]),
    ("G3-C", "installer-optin", ["test_installer_optin_security.py"]),
    ("G4-D", "search-transport-dedup", None),
    ("G5-E", "jev-evidence-locator", ["test_jev_targeted_excerpt.py"]),
    ("G6-RC", "cross-lane-integration", ["test_rc_integration_gate.py"]),
    ("G10-F", "money-gate", ["test_runtime_dollar_cap_contract.py"]),
    ("G11-G", "browser-egress", ["test_crawl_browser_webrtc_egress.py"]),
    ("G12-RC2", "rc2-integration-seams", ["test_rc2_integration_seams.py"]),
    ("G13-RC3", "rc3-evidence-p0", ["test_rc3_evidence_p0.py"]),
    ("G14-RC3", "rc3-release-qa", ["test_rc3_release_qa.py"]),
)

_SECRET_PATTERNS = (
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("API_TOKEN", re.compile(r"\bsk-[A-Za-z0-9._-]{20,}\b")),
    ("BEARER_TOKEN", re.compile(r"\bBearer\s+[A-Za-z0-9._-]{20,}")),
)
_PRIVATE_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+-]+@(gmail|qq|163|126|outlook|hotmail|yahoo)\.[A-Za-z.]+\b",
    re.IGNORECASE)
_MAINLAND_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_HAN_PHONE_CONTEXT = re.compile(r"(手机|电话|联系方式)")


def run(command: list[str], *, cwd: Path, timeout: int, env: dict[str, str]) -> dict:
    started = time.time()
    try:
        completed = subprocess.run(command, cwd=str(cwd), env=env, text=True,
                                   capture_output=True, timeout=timeout)
        return {
            "command": " ".join(command),
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "seconds": round(time.time() - started, 1),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": " ".join(command),
            "returncode": None,
            "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": f"TIMEOUT after {timeout}s",
            "seconds": round(time.time() - started, 1),
        }


def offline_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "MARKETOPS_SEARCH_ALLOW_NETWORK": "0",
        "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0",
        "MARKETOPS_SEARCH_MAX_CALLS": "0",
        "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "0",
        "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    return env


def result(check_id: str, name: str, status: str, detail: str, **extra) -> dict:
    return {"id": check_id, "name": name, "status": status, "detail": detail, **extra}


def check_repo_state() -> list[dict]:
    checks = []
    branch = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
                            text=True, capture_output=True)
    checks.append(result("G0-branch", "RC branch", "PASS" if branch.stdout.strip()
                         == RC3_BRANCH else "FAIL",
                         branch.stdout.strip() or branch.stderr.strip()))
    ancestor = subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor",
                               BASELINE, "HEAD"], capture_output=True)
    checks.append(result("G0-base", "baseline ancestry", "PASS" if ancestor.returncode == 0
                         else "FAIL", f"baseline {BASELINE} ancestor of HEAD"))
    rc3_base = subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor",
                               RC3_BASE, "HEAD"], capture_output=True)
    checks.append(result("G0-rc3-base", "Lane H base ancestry",
                         "PASS" if rc3_base.returncode == 0 else "FAIL",
                         f"Lane H {RC3_BASE} ancestor of HEAD"))
    return checks


def check_private_data() -> dict:
    """Scan only lines this branch adds, so pre-existing allowlisted fixtures stay out."""
    diff = subprocess.run(["git", "-C", str(REPO), "diff", "--unified=0", BASELINE, "--", "."],
                          text=True, capture_output=True)
    findings: list[dict] = []
    current_path = ""
    for line in diff.stdout.splitlines():
        if line.startswith("+++ b/"):
            current_path = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        text = line[1:]
        # A synthetic credential under tests/ is a fixture, not a leak.  This
        # mirrors .gitleaks.toml's path+match allowlists; the body must still
        # carry an explicit synthetic marker, so a real key in tests/ still fails.
        synthetic_fixture = (
            ("/tests/" in f"/{current_path}" or current_path.startswith("tests/"))
            and re.search(r"synthetic|fixture|fake|dummy|example", text, re.IGNORECASE)
            is not None)
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                findings.append({
                    "severity": "REVIEW" if synthetic_fixture else "FAIL",
                    "kind": label + ("_SYNTHETIC_TEST_FIXTURE" if synthetic_fixture else ""),
                    "path": current_path, "line": text[:200]})
        email = _PRIVATE_EMAIL.search(text)
        if email:
            findings.append({"severity": "REVIEW", "kind": "PRIVATE_EMAIL_DOMAIN",
                             "path": current_path, "line": text[:200]})
        if _MAINLAND_PHONE.search(text) and _HAN_PHONE_CONTEXT.search(text):
            findings.append({"severity": "REVIEW", "kind": "MAINLAND_PHONE",
                             "path": current_path, "line": text[:200]})
    failed = [item for item in findings if item["severity"] == "FAIL"]
    status = "FAIL" if failed else "PASS"
    detail = (f"{len(failed)} secret-level finding(s), "
              f"{len(findings) - len(failed)} review-level finding(s)")
    return result("G9-private-data", "no private data in added lines", status, detail,
                  findings=findings[:50])


def check_targeted(env: dict[str, str]) -> list[dict]:
    checks = []
    for check_id, name, files in LANE_TESTS:
        if files is None:
            # Lane D names its test when it commits; find it by intent rather than
            # hard-coding a filename the Lane has not agreed to yet.  It must be a
            # file this branch ADDS: a baseline test that happens to contain the
            # word "transport" is not Lane D's evidence and must not read as green.
            added = subprocess.run(
                ["git", "-C", str(REPO), "diff", "--name-only", "--diff-filter=A",
                 BASELINE, "--", str((PLUGIN / "tests").relative_to(REPO))],
                text=True, capture_output=True)
            added_names = {Path(line).name for line in added.stdout.splitlines()
                           if line.strip()}
            files = [path.name for path in sorted((PLUGIN / "tests").glob("test_*.py"))
                     if path.name in added_names
                     and any(token in path.name for token in
                             ("dedup", "transport", "reserve", "double_charge",
                              "no_double_charge"))]
            if not files:
                checks.append(result(check_id, name, "BLOCKED",
                                     "Lane has not delivered a committed search-dedup test"))
                continue
        missing = [name_ for name_ in files if not (PLUGIN / "tests" / name_).is_file()]
        if missing:
            checks.append(result(check_id, name, "BLOCKED",
                                 f"missing Lane test file(s): {', '.join(missing)}"))
            continue
        # unittest discovery takes one pattern per run, so each file is run on its own.
        runs = [run([sys.executable, "-m", "unittest", "discover", "-s", "tests",
                     "-p", pattern, "-v"], cwd=PLUGIN, timeout=900, env=env)
                for pattern in files]
        output = "\n".join(item["stdout_tail"] + item["stderr_tail"] for item in runs)
        returned = all(item["returncode"] == 0 for item in runs)
        ran_match = re.search(r"Ran (\d+) tests?", output)
        ran = int(ran_match.group(1)) if ran_match else None
        skipped = re.search(r"skipped", output, re.IGNORECASE) is not None
        if ran == 0:
            status, detail = "BLOCKED", "no tests executed (0 run); a skip is not a pass"
        elif skipped:
            status, detail = "BLOCKED", "skipped tests reported; treat as not executed"
        elif returned:
            status, detail = "PASS", f"Ran {ran} tests; no failures"
        else:
            status, detail = "FAIL", "non-zero test exit"
        checks.append(result(check_id, name, status, detail, runs=runs,
                             ran=ran, skipped_reported=skipped))
    return checks


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_release_artifacts(env: dict[str, str]) -> dict:
    sys.path.insert(0, str(SCRIPTS))
    import build_client_bundles  # noqa: E402

    root = Path(tempfile.mkdtemp(prefix="marketops-rc-gate-"))
    if root.resolve().is_relative_to(REPO):
        return result("G7-release", "release manifest/hash", "FAIL",
                      "temporary build root landed inside the repository")
    problems: list[str] = []
    evidence: dict = {"temporary_root": str(root)}

    source_dir = root / "source"
    source = build_client_bundles.build_source(source_dir)
    evidence["source"] = {k: source.get(k) for k in
                          ("status", "zip", "zip_sha256", "source_sha256",
                           "qa_verified", "portable")}
    archive = Path(source["zip"])
    if not archive.is_file():
        problems.append("source archive missing")
    else:
        if _sha256(archive) != source["zip_sha256"]:
            problems.append("source zip sha256 does not match returned hash")
        sidecar = archive.with_name(archive.name + ".sha256")
        if not sidecar.is_file():
            problems.append("source .sha256 sidecar missing")
        else:
            recorded = sidecar.read_text(encoding="ascii").split()[0]
            if recorded != source["zip_sha256"]:
                problems.append(".sha256 sidecar disagrees with zip")
    manifest_path = source_dir / "marketops-research-assistant" / "SOURCE_MANIFEST.json"
    if not manifest_path.is_file():
        problems.append("SOURCE_MANIFEST.json missing")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        evidence["source_manifest"] = {
            "schema": manifest.get("schema"), "version": manifest.get("version"),
            "file_count": len(manifest.get("files") or {}),
            "qa_verified": manifest.get("qa_verified")}
        for relative, digest in (manifest.get("files") or {}).items():
            target = source_dir / "marketops-research-assistant" / relative
            if not target.is_file():
                problems.append(f"manifest lists missing file: {relative}")
            elif _sha256(target) != digest:
                problems.append(f"manifest digest mismatch: {relative}")

    bundle_dir = root / "bundle"
    bundle = build_client_bundles.build(bundle_dir, with_research_mcp=True)
    evidence["bundle"] = {k: bundle.get(k) for k in
                          ("status", "zip", "zip_sha256", "machine_bound",
                           "mcp_servers_bundled")}
    bundle_zip = Path(bundle["zip"])
    if not bundle_zip.is_file() or _sha256(bundle_zip) != bundle["zip_sha256"]:
        problems.append("bundle zip missing or hash mismatch")
    helper = bundle_dir / "workbuddy" / "marketops-research-assistant" / "scripts" / "research_client_view.py"
    evidence["client_view_helper_in_bundle"] = helper.is_file()
    if not helper.is_file():
        problems.append("research_client_view.py is not shipped in the connector bundle")

    status = "FAIL" if problems else "PASS"
    return result("G7-release", "release manifest/hash", status,
                  "; ".join(problems) if problems else
                  "source + connector manifests, per-file hashes and zip hashes verified",
                  evidence=evidence)


def _summary_counts(output: str) -> tuple[int | None, int | None, int | None]:
    ran = re.search(r"Ran (\d+) tests?", output)
    failed = re.search(r"FAILED \(([^)]*)\)", output)
    failures = errors = None
    if failed:
        numbers = dict(re.findall(r"(failures|errors|skipped)=(\d+)", failed.group(0)))
        failures = int(numbers.get("failures", 0))
        errors = int(numbers.get("errors", 0))
    return (int(ran.group(1)) if ran else None, failures, errors)


def check_full_suite(env: dict[str, str], baseline_failures: int | None) -> dict:
    run_result = run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                     cwd=PLUGIN, timeout=1800, env=env)
    output = run_result["stdout_tail"] + "\n" + run_result["stderr_tail"]
    ran, failures, errors = _summary_counts(output)
    total_red = None if failures is None else failures + (errors or 0)
    if run_result["returncode"] == 0:
        status, detail = "PASS", f"Ran {ran} tests; no failures"
    elif total_red is not None and baseline_failures is not None and total_red == baseline_failures:
        status = "BASELINE_RED_NOT_NEW"
        detail = (f"Ran {ran} tests; {failures} failures + {errors} errors equals the "
                  f"declared baseline {baseline_failures}; not evidence of a new regression")
    else:
        status = "FAIL"
        detail = (f"Ran {ran} tests; failures={failures} errors={errors}"
                  + ("; no --baseline-failures supplied" if baseline_failures is None else
                     f"; declared baseline {baseline_failures}"))
    return result("G8-full-suite", "full offline plugin suite (run once)", status, detail,
                  run=run_result, ran=ran, failures=failures, errors=errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path,
                        help="directory outside the repository for the JSON report")
    parser.add_argument("--full-suite", action="store_true",
                        help="run the complete offline plugin suite exactly once")
    parser.add_argument("--baseline-failures", type=int,
                        help="declared baseline failures+errors on 4f87bc33")
    parser.add_argument("--skip-release-build", action="store_true",
                        help="skip the bundle/source manifest build")
    args = parser.parse_args(argv)

    if args.evidence_dir is not None:
        target = args.evidence_dir.expanduser().resolve()
        if target.is_relative_to(REPO):
            print("GATE_BLOCKED: --evidence-dir must be outside the repository", file=sys.stderr)
            return 2
        target.mkdir(parents=True, exist_ok=True)
    else:
        target = None

    env = offline_env()
    checks: list[dict] = []
    checks.extend(check_repo_state())
    checks.append(check_private_data())
    checks.extend(check_targeted(env))
    if not args.skip_release_build:
        checks.append(check_release_artifacts(env))
    if args.full_suite:
        checks.append(check_full_suite(env, args.baseline_failures))

    okay = all(item["status"] in {"PASS", "BASELINE_RED_NOT_NEW"} for item in checks)
    report = {
        "schema": "marketops.rc-release-gate/1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline": BASELINE,
        "head": subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                               text=True, capture_output=True).stdout.strip(),
        "offline": True,
        "ok": okay,
        "checks": checks,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if target is not None:
        path = target / f"rc-gate-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
        path.write_text(text + "\n", encoding="utf-8")
        print(f"GATE_REPORT: {path}", file=sys.stderr)
    return 0 if okay else 1


if __name__ == "__main__":
    raise SystemExit(main())

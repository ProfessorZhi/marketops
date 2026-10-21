#!/usr/bin/env python3
"""Offline release-hygiene acceptance for the MarketOps 0.5.7-rc6 candidate.

What this proves, and what it deliberately does not:

* It builds the portable source ZIP and the WorkBuddy connector from the
  current checkout into a directory **outside** the repository, then re-reads
  every manifest digest, the ZIP hash and the member list.
* It unpacks that ZIP and drives the real stdio MCP entry with network and
  budgets forced to zero.  A missing MCP SDK is BLOCKED, never a pass.
* It seeds only synthetic records (qualified + held), runs the real delivery
  admission gate, feeds the resulting business sheets to the real client-view
  generator, and asserts that held/evidence-only values do not appear in the
  client view.  Nothing here hand-writes a client view.
* It writes a JSON report and a release MANIFEST carrying the client-view
  content hash to ``--evidence-dir`` (also outside the repository).

No provider, paid call, SMTP, browser, credential store, global install or
network write is touched.  See docs/rc-20261020/RELEASE-HYGIENE.md.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "research-tools" / "marketops-research-assistant"
STAMP = "2026-10-20T00:00:00+00:00"
RUN_ID = "rc-hygiene-run"
ART = "rc-hygiene-material"

PAGE = (
    b"<html><body>"
    b"<h1>Example Borough Council</h1>"
    b"<p>Example Sports Ground is operated by Example Borough Council.</p>"
    b"<p>Alex Sample is the Site manager at Example Borough Council.</p>"
    b"<p>Grounds Management at Example Borough Council is the responsible department.</p>"
    b"<p>Contact Example Borough Council at grounds@example.org for grounds enquiries.</p>"
    b"<p>Evidence Only Holdings can be reached at evidence-only@example.net.</p>"
    b"</body></html>"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha256(directory: Path) -> tuple[str, dict[str, str]]:
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = sha256(path)
    digest = hashlib.sha256()
    for name, value in files.items():
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), files


def check(item_id: str, name: str, status: str, detail: str, **extra) -> dict:
    return {"id": item_id, "name": name, "status": status, "detail": detail, **extra}


def outside_repository(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise SystemExit(f"{label} must be outside the repository: {resolved}")
    return resolved


def offline_env(root: Path) -> dict[str, str]:
    prefixes = ("MARKETOPS_", "SERPER_", "SERPAPI_", "TINYFISH_", "TYPESAFE_",
                "CRAWL4AI_", "JEV_")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(prefixes)}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        "MARKETOPS_RESEARCH_CONFIG": str(root / "config.json"),
        "MARKETOPS_RESEARCH_ALLOW_NETWORK": "0",
        "MARKETOPS_SEARCH_ALLOW_NETWORK": "0",
        "MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS": "0",
        "MARKETOPS_SEARCH_MAX_CALLS": "0",
        "MARKETOPS_TINYFISH_AGENT_ALLOW_PAID": "0",
        "TMPDIR": str(root / "tmp"),
        "TMP": str(root / "tmp"),
        "TEMP": str(root / "tmp"),
    })
    (root / "tmp").mkdir(parents=True, exist_ok=True)
    return env


def safe_extract(archive: Path, destination: Path) -> list[str]:
    members = []
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            name = info.filename
            if name.startswith("/") or ".." in Path(name).parts or "\\" in name:
                raise ValueError(f"UNSAFE_ZIP_MEMBER: {name}")
            target = (destination / name).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise ValueError(f"ZIP_MEMBER_ESCAPES_DESTINATION: {name}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"SYMLINK_IN_ZIP: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                target.chmod(mode & ~0o022)
            members.append(name)
    return members


def build_artifacts(work: Path, qa_receipt: Path | None = None) -> tuple[dict, list[dict], Path]:
    sys.path.insert(0, str(PLUGIN / "scripts"))
    import build_client_bundles  # noqa: E402

    version = build_client_bundles.META["version"]
    source = build_client_bundles.build_source(work / "source", qa_receipt=qa_receipt)
    bundle = build_client_bundles.build(work / "bundle", with_research_mcp=True)
    checks = []

    archive = Path(source["zip"])
    sidecar = archive.with_name(archive.name + ".sha256")
    check_version = "PASS" if version.startswith("0.5.6-rc") else "FAIL"
    checks.append(check("H1-version", "manifest/builder/installer version",
                        check_version, f"builder version {version}"))

    if archive.is_file() and sha256(archive) == source["zip_sha256"]:
        checks.append(check("H2-zip-hash", "source ZIP hash", "PASS",
                            source["zip_sha256"]))
    else:
        checks.append(check("H2-zip-hash", "source ZIP hash", "FAIL",
                            "returned hash does not match the archive"))
    if sidecar.is_file() and sidecar.read_text(encoding="ascii").split()[0] == source["zip_sha256"]:
        checks.append(check("H3-sidecar", ".sha256 sidecar", "PASS", str(sidecar)))
    else:
        checks.append(check("H3-sidecar", ".sha256 sidecar", "FAIL", "sidecar missing or disagrees"))

    manifest_path = work / "source" / "marketops-research-assistant" / "SOURCE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems = []
    if manifest.get("schema") != "marketops.source-package.v1":
        problems.append("wrong schema")
    if manifest.get("version") != version:
        problems.append(f"manifest version {manifest.get('version')} != {version}")
    for relative, digest in (manifest.get("files") or {}).items():
        target = work / "source" / "marketops-research-assistant" / relative
        if not target.is_file():
            problems.append(f"missing: {relative}")
        elif sha256(target) != digest:
            problems.append(f"digest mismatch: {relative}")
    status = "PASS" if not problems else "FAIL"
    checks.append(check("H4-source-manifest", "source manifest per-file digests", status,
                        "; ".join(problems) if problems else
                        f"{len(manifest.get('files') or {})} files verified",
                        version=manifest.get("version"),
                        source_sha256=manifest.get("source_sha256")))

    with zipfile.ZipFile(archive) as bundle_zip:
        members = [item.filename for item in bundle_zip.infolist()]
    forbidden = [name for name in members
                 if name.endswith((".pyc", ".db", ".sqlite3", ".zip"))
                 or any(part in name.split("/") for part in
                        ("__pycache__", ".git", ".venv", "tests", "data", "state"))]
    needed = "marketops-research-assistant/scripts/research_client_view.py"
    if not forbidden and needed in members:
        checks.append(check("H5-members", "source ZIP members", "PASS",
                            f"{len(members)} members; client-view helper present"))
    else:
        checks.append(check("H5-members", "source ZIP members", "FAIL",
                            f"forbidden={forbidden[:10]} helper={needed in members}",
                            members=len(members)))

    helper = work / "bundle" / "workbuddy" / "marketops-research-assistant" / "scripts" / "research_client_view.py"
    bundle_zip = Path(bundle["zip"])
    if (helper.is_file() and bundle_zip.is_file()
            and sha256(bundle_zip) == bundle["zip_sha256"]):
        checks.append(check("H6-bundle", "WorkBuddy connector bundle", "PASS",
                            f"{bundle['zip_sha256']}"))
    else:
        checks.append(check("H6-bundle", "WorkBuddy connector bundle", "FAIL",
                            "client-view helper absent or bundle hash mismatch"))

    # A ZIP that hashes correctly is a *preview*, not a QA pass. Promotion to
    # qa_verified requires an external, signed attestation; with none, this check
    # is BLOCKED and the whole report is NOT_RELEASE_READY.
    attestation = source.get("qa_attestation") or {}
    qa_verified = bool(manifest.get("qa_verified"))
    if qa_verified and attestation.get("verified"):
        checks.append(check("H12-independent-qa", "independent QA attestation", "PASS",
                            "signed_by=" + str(attestation.get("signed_by")),
                            qa_attestation=attestation))
    else:
        checks.append(check(
            "H12-independent-qa", "independent QA attestation", "BLOCKED",
            "SOURCE_MANIFEST.qa_verified=false; ZIP/hash checks are NOT formal QA. "
            "Supply an externally signed BUILD_QA receipt and MARKETOPS_QA_PUBLIC_KEY. "
            "reason=" + str(attestation.get("reason") or "QA_NOT_VERIFIED"),
            qa_attestation=attestation))

    evidence = {
        "version": version,
        "source": {key: source.get(key) for key in
                   ("status", "zip", "zip_sha256", "source_sha256",
                    "qa_verified", "qa_attestation", "portable")},
        "bundle": {key: bundle.get(key) for key in
                   ("status", "zip", "zip_sha256", "machine_bound",
                    "mcp_servers_bundled")},
        "source_manifest": {"version": manifest.get("version"),
                            "source_sha256": manifest.get("source_sha256"),
                            "file_count": len(manifest.get("files") or {}),
                            "qa_verified": manifest.get("qa_verified")},
        "member_count": len(members),
        "formal_qa": "PASS" if qa_verified else "NOT_PERFORMED",
        "release_readiness": "RELEASE_READY" if qa_verified else "NOT_RELEASE_READY",
    }
    return evidence, checks, archive


async def stdio_journey(python: str, unpacked: Path, root: Path) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=python,
        args=[str(unpacked / "scripts" / "research_mcp.py")],
        env=offline_env(root),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            result = await session.call_tool("marketops_setup_status", {})
            structured = getattr(result, "structured_content", None)
            if not isinstance(structured, dict):
                for block in result.content:
                    if getattr(block, "type", None) == "text":
                        value = json.loads(block.text)
                        if isinstance(value, dict):
                            structured = value
                            break
            return {
                "server_name": getattr(init.server_info, "name", None),
                "server_version": getattr(init.server_info, "version", None),
                "tool_count": len(tools),
                "all_tools_namespaced": all(name.startswith("marketops_") for name in tools),
                "setup_status": (structured or {}).get("status"),
                "provider_calls": 0,
            }


def _seed(plugin_root: Path, data: Path) -> None:
    sys.path.insert(0, str(plugin_root / "scripts"))
    import research_lite_store as store

    store.start_run(data, RUN_ID, "GB-sports-grounds",
                    "RC release-hygiene synthetic fixture", "RC-RULES-SYNTHETIC")
    store.save_artifact(data, RUN_ID, ART, "fixture-public-html",
                        "https://example.org/rc-hygiene", PAGE, "rendered_html")

    def propose(record_id, stage, kind, entity_key, payload, artifacts):
        return store.put_candidate(data, RUN_ID, record_id, stage, kind,
                                   entity_key, payload, artifacts)

    propose("org-legit", "entities", "organization", "example-borough",
            {"name": "Example Borough Council",
             "claims": [{"field": "identity", "artifact_id": ART,
                         "quote": "Example Borough Council"}]}, [ART])
    propose("venue-legit", "entities", "venue", "example-sports-ground",
            {"name": "Example Sports Ground", "organization_id": "example-borough",
             "claims": [{"field": "identity", "artifact_id": ART,
                         "quote": "Example Sports Ground is operated by Example Borough Council."}]},
            [ART])
    propose("person-legit", "people", "person", "grounds-management",
            {"department": "Grounds Management", "role_title": "Grounds Management",
             "organization_id": "example-borough",
             "claims": [{"field": "role_title", "artifact_id": ART,
                         "quote": "Grounds Management at Example Borough Council is the responsible department."}]},
            [ART])
    propose("contact-legit", "routes", "contact_point", "grounds-contact",
            {"organization_id": "example-borough", "type": "email",
             "value": "grounds@example.org", "role_function": "Grounds Management",
             "claims": [{"field": "email", "artifact_id": ART,
                         "quote": "Contact Example Borough Council at grounds@example.org for grounds enquiries."}]},
            [ART])
    propose("org-fabricated", "entities", "organization", "totally-fabricated-org",
            {"name": "Totally Fabricated Org"}, [ART])
    propose("venue-fabricated", "entities", "venue", "totally-fabricated-arena",
            {"name": "Totally Fabricated Arena"}, [])
    propose("contact-fabricated", "routes", "contact_point", "fabricated-address",
            {"organization_id": "example-borough", "type": "email",
             "value": "fabricated@example.net"}, [ART])
    propose("contact-no-quote", "routes", "contact_point", "secondary-address",
            {"organization_id": "example-borough", "type": "email",
             "value": "secondary@example.org"}, [ART])


def client_view_journey(unpacked: Path, work: Path) -> tuple[dict, list[dict]]:
    checks = []
    data = work / "research-data"
    _seed(unpacked, data)
    sys.path.insert(0, str(unpacked / "scripts"))
    import research_client_view as client_view
    import research_delivery as delivery

    # The real handoff path, not a hand-written view: write_delivery must build the
    # workbook, filter the business sheets through admission, and create the
    # client view itself. Calling write_client_view separately here would let a
    # caller-side step stand in for the product behaviour being claimed.
    result = delivery.write_delivery(data, RUN_ID, "rc-hygiene-delivery")
    view = Path(result["client_view_directory"])
    client = result.get("client_view") or {}
    counts = {"business_sheets": len(client.get("counts") or {}),
              "admitted_records": result["admission"]["admitted_record_count"],
              "rejected_records": result["admission"]["rejected_record_count"]}
    modules = {
        "research_delivery": str(Path(delivery.__file__).resolve()),
        "research_client_view": str(Path(client_view.__file__).resolve()),
    }
    modules_from_unpacked = all(
        Path(path).is_relative_to(unpacked.resolve()) for path in modules.values())
    expected_view = Path(result["directory"]) / "client-view"
    inside_delivery = (view.is_dir()
                       and view.resolve() == expected_view.resolve())
    if counts["business_sheets"] != 4 or not modules_from_unpacked or not inside_delivery:
        checks.append(check("H8-delivery", "delivery -> automatic client-view", "FAIL",
                            f"sheets={counts['business_sheets']} inside={inside_delivery}; "
                            f"modules={modules}"))
        return {**counts, "modules": modules}, checks
    verified = client_view.verify_client_view(view)
    checks.append(check("H8-delivery", "delivery -> automatic client-view", "PASS",
                        f"{counts['admitted_records']} admitted / "
                        f"{counts['rejected_records']} held; "
                        f"{len(client.get('files') or {})} files inside {expected_view.name}"))

    blobs = b"\n".join(path.read_bytes() for path in sorted(view.iterdir())
                       if path.is_file())
    forbidden = (b"Totally Fabricated", b"fabricated@example.net",
                 b"secondary@example.org", b"Evidence Only Holdings",
                 b"evidence-only@example.net", b"HOLD_")
    leaked = [token.decode("ascii") for token in forbidden if token in blobs]
    required = (b"Example Borough Council", b"grounds@example.org")
    missing = [token.decode("ascii") for token in required if token not in blobs]
    if leaked or missing:
        checks.append(check("H9-no-evidence-leak", "held/evidence values excluded",
                            "FAIL", f"leaked={leaked} missing={missing}"))
    else:
        checks.append(check("H9-no-evidence-leak", "held/evidence values excluded",
                            "PASS", "qualified values present; held and evidence-only values absent"))

    manifest = json.loads((view / "MANIFEST.json").read_text(encoding="utf-8"))
    manifest_hashes_ok = all(
        (view / name).read_bytes().__len__() == meta["bytes"]
        and sha256(view / name) == meta["sha256"]
        for name, meta in manifest["files"].items())
    tree_digest, files = tree_sha256(view)
    # The delivery's final MANIFEST must carry the client-view files too, with
    # the same hashes, or the customer receipt describes a different tree.
    final_manifest = json.loads(
        (Path(result["directory"]) / "MANIFEST.json").read_text(encoding="utf-8"))
    final_ok = all(
        relative in final_manifest.get("files", {})
        and final_manifest["files"][relative]["sha256"] == sha256(Path(result["directory"]) / relative)
        for relative in ("client-view/MANIFEST.json", "client-view/Client_View.xlsx",
                         "client-view/客户总表.csv"))
    checks.append(check("H10-manifest", "client-view + delivery manifest hashes",
                        "PASS" if manifest_hashes_ok and verified["files_verified"]
                        and final_ok else "FAIL",
                        f"manifest {client.get('manifest_sha256')}"))
    return {
        "status": client.get("status"),
        "send_authorized": client.get("send_authorized"),
        "formal_sop_claimed": client.get("formal_sop_claimed"),
        "counts": client.get("counts"),
        "modules": modules,
        "directory": str(view),
        "manifest_sha256": client.get("manifest_sha256"),
        "content_tree_sha256": tree_digest,
        "xlsx_sha256": sha256(view / "Client_View.xlsx"),
        "files": files,
        "forbidden_tokens_checked": [token.decode("ascii") for token in forbidden],
    }, checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True,
                        help="outside-repository directory for report + MANIFEST")
    parser.add_argument("--work-dir", type=Path,
                        help="outside-repository scratch root (default: evidence/work)")
    parser.add_argument("--python", default=sys.executable,
                        help="interpreter with the MCP SDK, used only for the stdio smoke")
    parser.add_argument("--qa-receipt", type=Path,
                        help="external BUILD_QA receipt; must carry a signed independent_qa block "
                             "and MARKETOPS_QA_PUBLIC_KEY to set qa_verified")
    args = parser.parse_args(argv)

    evidence_dir = outside_repository(args.evidence_dir, "--evidence-dir")
    work_root = outside_repository(args.work_dir or evidence_dir / "work", "--work-dir")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="marketops-rc-hygiene-", dir=str(work_root)))

    checks: list[dict] = []
    qa_receipt = args.qa_receipt.expanduser().resolve() if args.qa_receipt else None
    if qa_receipt is not None and not qa_receipt.is_file():
        raise SystemExit(f"--qa-receipt is missing: {qa_receipt}")
    artifacts, artifact_checks, archive = build_artifacts(work, qa_receipt)
    checks.extend(artifact_checks)

    unpacked = work / "unpacked"
    members = safe_extract(archive, unpacked)
    plugin_root = unpacked / "marketops-research-assistant"
    checks.append(check("H7-unpack", "source ZIP unpack", "PASS",
                        f"{len(members)} members; no traversal or symlink"))

    stdio = {"status": "BLOCKED", "reason": "not attempted"}
    try:
        stdio = asyncio.run(asyncio.wait_for(stdio_journey(args.python, plugin_root, work),
                                             timeout=120))
        ok = (stdio["tool_count"] > 0 and stdio["all_tools_namespaced"]
              and stdio["setup_status"] == "CONFIG_REQUIRED")
        stdio["status"] = "PASS" if ok else "FAIL"
    except ImportError as exc:
        stdio = {"status": "BLOCKED", "reason": f"MCP SDK unavailable: {exc}"}
    except Exception as exc:  # noqa: BLE001 - report the wire failure, do not hide it
        stdio = {"status": "FAIL", "reason": f"{type(exc).__name__}: {exc}"}
    checks.append(check("H11-stdio", "unpacked offline stdio MCP smoke",
                        stdio["status"], stdio.get("reason", ""),
                        server_name=stdio.get("server_name"),
                        server_version=stdio.get("server_version"),
                        tool_count=stdio.get("tool_count"),
                        setup_status=stdio.get("setup_status")))

    client_view, client_checks = client_view_journey(plugin_root, work)
    checks.extend(client_checks)

    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                          text=True, capture_output=True, check=True).stdout.strip()
    statuses = {item["status"] for item in checks}
    overall = "FAIL" if "FAIL" in statuses else ("BLOCKED" if "BLOCKED" in statuses else "PASS")
    report = {
        "schema": "marketops.rc-release-hygiene/1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "head": head,
        "version": artifacts["version"],
        "offline": True,
        "status": overall,
        "formal_qa": artifacts.get("formal_qa", "NOT_PERFORMED"),
        "release_readiness": artifacts.get("release_readiness", "NOT_RELEASE_READY"),
        "checks": checks,
        "artifacts": artifacts,
        "stdio": stdio,
        "client_view": client_view,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    (evidence_dir / f"release-hygiene-{stamp}.json").write_text(text + "\n", encoding="utf-8")
    manifest = {
        "schema": "marketops.rc-release-manifest/1",
        "version": artifacts["version"],
        "head": head,
        "status": overall,
        "source_zip_sha256": artifacts["source"]["zip_sha256"],
        "source_sha256": artifacts["source_manifest"]["source_sha256"],
        "source_file_count": artifacts["source_manifest"]["file_count"],
        "portable_artifact": "source_zip",
        "bundle_zip_sha256": artifacts["bundle"]["zip_sha256"],
        "bundle_hash_scope": "RUN_LOCAL_MACHINE_BOUND",
        "client_view_manifest_sha256": client_view.get("manifest_sha256"),
        "client_view_content_tree_sha256": client_view.get("content_tree_sha256"),
        "client_view_xlsx_sha256": client_view.get("xlsx_sha256"),
        "client_view_files": client_view.get("files", {}),
        "qa_verified": bool(artifacts.get("source", {}).get("qa_verified")),
        "formal_qa": artifacts.get("formal_qa", "NOT_PERFORMED"),
        "release_readiness": artifacts.get("release_readiness", "NOT_RELEASE_READY"),
        "formal_release_approved": False,
        "blocked_reasons": [item["detail"] for item in checks
                            if item["status"] == "BLOCKED"],
    }
    (evidence_dir / "RELEASE_HYGIENE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"EVIDENCE: {evidence_dir}", file=sys.stderr)
    return 0 if overall == "PASS" else (2 if overall == "BLOCKED" else 1)


if __name__ == "__main__":
    raise SystemExit(main())

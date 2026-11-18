#!/usr/bin/env python3
"""MinerU Local smoke test.

Checks, in order:

  1. the TCP API answers and requires a Bearer token
  2. a real text PDF parses to completion through the API
  3. the extracted markdown contains the text that is actually in the PDF
  4. the model weights are resident on the GPU while the parse runs

Check 4 does not compare "peak vs baseline". The managed parse-server is
started with --preload-models, so the weights are already resident before the
first request: a correct GPU run shows almost no growth over the baseline and
would be misjudged by a growth threshold. What is measured instead is that a
process running from the MinerU environment holds a substantial block of GPU
memory (named in nvidia-smi's compute-apps table) and that the GPU is busy at
some point during the parse. A CPU-only run has neither.

The token is read from the MinerU config file and never printed.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("MINERU_BASE_URL", "http://127.0.0.1:18000")
HOME = pathlib.Path(os.environ.get("MINERU_HOME", "/data/home/fjd/marketops-lab/mineru-home"))
PDF = os.environ.get(
    "MINERU_SMOKE_PDF",
    "/data/home/fjd/marketops-lab/datasets-raw/smoke/sample.pdf",
)
TIER = os.environ.get("MINERU_SMOKE_TIER", "standard")
TIMEOUT_S = int(os.environ.get("MINERU_SMOKE_TIMEOUT", "600"))
EXPECTED = os.environ.get("MINERU_SMOKE_EXPECT", "Northwind Ltd quality manual")


def token() -> str:
    text = (HOME / "config.yaml").read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("token:"):
            return stripped.split(":", 1)[1].strip()
    raise SystemExit("doclib auth token not found in config.yaml")


def call(path: str, method: str = "GET", payload: dict | None = None, auth: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode(errors="replace")[:300]}


def nvidia_query(field: str) -> str:
    out = subprocess.run(
        ["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout.strip()
    return out.splitlines()[0]


def mineru_gpu_mib() -> tuple[int, list[str]]:
    """GPU memory held by processes running out of the MinerU environment."""
    out = subprocess.run(
        ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"],
        capture_output=True, text=True, timeout=5, check=True,
    ).stdout.strip()
    total = 0
    names = []
    for line in out.splitlines():
        if not line.strip():
            continue
        pid, name, mem = [p.strip() for p in line.split(",")]
        if "marketops-lab/envs/mineru" not in name:
            continue
        try:
            total += int(mem.split()[0])
        except ValueError:
            continue
        names.append(f"{pid}:{name.split('/')[-1]}:{mem}")
    return total, names


def main() -> int:
    tok = token()
    checks = []

    status, _ = call("/api/v1/server/status")
    checks.append({"name": "auth_required", "status": status, "passed": status == 401})

    status, body = call("/api/v1/server/status", auth=tok)
    checks.append({"name": "status", "status": status, "passed": status == 200,
                   "detail": body.get("version") or body.get("mineru_version")})

    resident_before, procs = mineru_gpu_mib()
    util_samples: list[int] = []
    started = time.monotonic()
    # force=True: doclib caches by (sha256, tier). A cache hit would prove the
    # API answers, not that inference ran, and this test has to tell the two
    # apart before anyone trusts a benchmark number from this service.
    status, resp = call("/api/v1/parses", method="POST",
                        payload={"path": PDF, "tier": TIER, "force": True}, auth=tok)
    parse_ids = resp.get("wait_parse_ids") or resp.get("created_parse_ids") or []
    checks.append({"name": "submit", "status": status, "passed": status == 200 and bool(parse_ids),
                   "parse_ids": parse_ids, "sha256": resp.get("sha256"),
                   "cache_hit": resp.get("cache_hit")})

    peak_gpu_total = int(nvidia_query("memory.used"))
    final = None
    if status == 200 and parse_ids:
        deadline = started + TIMEOUT_S
        parse_id = parse_ids[0]
        while time.monotonic() < deadline:
            _, final = call(f"/api/v1/parses/{parse_id}", auth=tok)
            try:
                util_samples.append(int(nvidia_query("utilization.gpu")))
                peak_gpu_total = max(peak_gpu_total, int(nvidia_query("memory.used")))
            except Exception:
                pass
            if final.get("status") in ("done", "failed"):
                break
            time.sleep(0.5)

    elapsed = round(time.monotonic() - started, 1)
    checks.append({
        "name": "parse",
        "passed": bool(final and final.get("status") == "done"),
        "status": final.get("status") if final else None,
        "error_code": (final or {}).get("error_code"),
        "error_msg": (final or {}).get("error_msg"),
        "elapsed_s": elapsed,
    })

    text = ""
    if final and final.get("status") == "done":
        short = final.get("short_id")
        query = urllib.parse.urlencode({"tier": TIER})
        status, doc = call(f"/api/v1/docs/{short}/content?{query}", auth=tok)
        text = doc.get("content", "") if status == 200 else ""
    checks.append({
        "name": "content",
        "passed": EXPECTED in text,
        "text_chars": len(text),
        "excerpt": text[:200],
    })

    resident_after, _ = mineru_gpu_mib()
    checks.append({
        "name": "gpu_resident",
        "passed": resident_after >= 500,
        "mineru_gpu_mib": resident_after,
        "processes": procs,
    })
    checks.append({
        "name": "gpu_busy",
        "passed": any(u > 0 for u in util_samples),
        "util_samples": util_samples[:40],
        "gpu_total_peak_mib": peak_gpu_total,
    })

    out = {"base_url": BASE, "tier": TIER, "pdf": PDF, "checks": checks,
           "passed": all(c["passed"] for c in checks)}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

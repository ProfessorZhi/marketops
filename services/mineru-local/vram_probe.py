#!/usr/bin/env python3
"""Sample GPU memory and utilisation while MinerU parses one document.

The resident VRAM of the parse-server (roughly 2.5 GB) is not the interesting
number: models are preloaded with --preload-models, so a before/after snapshot
would show almost no change even while the GPU is working. This samples
continuously during the parse and reports the peak, which is what a capacity
plan needs.

Usage:
    python3 services/mineru-local/vram_probe.py [--case DOC-PDF-SCAN-001]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import subprocess
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CASES = ROOT / "benchmark/datasets/local-runtime/document/cases.jsonl"
RUNS = ROOT / "benchmark/local-runtime/runs"
MINERU_MARK = "marketops-lab/envs/mineru"


def _nvidia(fields: str, target: str) -> list[tuple[str, ...]]:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-{target}={fields}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    return [tuple(x.strip() for x in line.split(",")) for line in out.splitlines() if line.strip()]


class Sampler(threading.Thread):
    def __init__(self, interval: float = 0.2):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_event = threading.Event()
        self.samples: list[dict] = []

    def run(self) -> None:
        while not self.stop_event.is_set():
            gpu = _nvidia("memory.used,utilization.gpu", "gpu")
            procs = _nvidia("pid,process_name,used_memory", "compute-apps")
            mineru_mib = sum(int(m) for _, name, m in procs if MINERU_MARK in name)
            used, util = (int(gpu[0][0]), int(gpu[0][1])) if gpu else (None, None)
            self.samples.append({
                "t": round(time.monotonic(), 3),
                "gpu_used_mib": used,
                "gpu_util_pct": util,
                "mineru_proc_mib": mineru_mib,
            })
            self.stop_event.wait(self.interval)

    def stop(self) -> list[dict]:
        self.stop_event.set()
        self.join(timeout=5)
        return self.samples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="DOC-PDF-SCAN-001")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from benchmark.local_runtime.document_bench import parse_mineru

    cases = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]
    case = next(c for c in cases if c["case_id"] == args.case)

    baseline = _nvidia("memory.used,utilization.gpu", "gpu")
    procs = _nvidia("pid,process_name,used_memory", "compute-apps")
    mineru_before = sum(int(m) for _, name, m in procs if MINERU_MARK in name)

    sampler = Sampler()
    sampler.start()
    started = time.monotonic()
    try:
        result = parse_mineru(case)
        error = None
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        result, error = None, f"{type(exc).__name__}: {exc}"
    elapsed = round(time.monotonic() - started, 3)
    samples = sampler.stop()

    used = [s["gpu_used_mib"] for s in samples if s["gpu_used_mib"] is not None]
    util = [s["gpu_util_pct"] for s in samples if s["gpu_util_pct"] is not None]
    mineru = [s["mineru_proc_mib"] for s in samples]
    report = {
        "case_id": case["case_id"],
        "case_path": case["path"],
        "parse_ok": result is not None,
        "error": error,
        "parse_latency_s": elapsed,
        "samples": len(samples),
        "baseline": {
            "gpu_used_mib": int(baseline[0][0]) if baseline else None,
            "gpu_util_pct": int(baseline[0][1]) if baseline else None,
            "mineru_proc_mib": mineru_before,
        },
        "during": {
            "gpu_used_peak_mib": max(used) if used else None,
            "gpu_used_mean_mib": round(statistics.mean(used), 1) if used else None,
            "gpu_util_peak_pct": max(util) if util else None,
            "gpu_util_mean_pct": round(statistics.mean(util), 1) if util else None,
            "gpu_util_samples_above_0": sum(1 for u in util if u > 0),
            "mineru_proc_peak_mib": max(mineru) if mineru else None,
            "mineru_proc_mean_mib": round(statistics.mean(mineru), 1) if mineru else None,
        },
        "result": {
            "text_chars": len(result["text"]) if result else 0,
            "pages": result["pages"] if result else None,
        } if result else None,
        "gpu_is_shared": True,
        "note": "host GPU also carries other users' processes; 'gpu_used' is the whole device, "
                "'mineru_proc' is the part attributable to this deployment",
    }
    out = pathlib.Path(args.out) if args.out else (
        RUNS / f"mineru-vram-{time.strftime('%Y%m%dT%H%M%S')}.json")
    out.write_text(json.dumps({"summary": report, "series": samples},
                              indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nwritten -> {out}")
    return 0 if result else 1


if __name__ == "__main__":
    sys.exit(main())

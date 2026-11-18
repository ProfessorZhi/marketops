#!/usr/bin/env python3
"""Pull an image's layers in parallel and assemble a local OCI layout.

Why this exists: `skopeo copy docker://... docker-archive:...` fetches every
layer on one connection, and on this host that connection settles around
0.23 MB/s while four parallel connections measure ~6.7 MB/s aggregate. The
image is 2.3 GB compressed, so serial is ~2.5 hours and parallel is minutes.

Nothing about the artifact is hand-rolled: this script only *downloads* blobs
and writes the OCI layout files (oci-layout, index.json, blobs/sha256/*).
`skopeo copy oci:... docker-archive:...` still does the assembly under its own
name, so the tar that `docker load` sees is produced by skopeo, not by us.

Every blob is verified against its digest before it is written, so a truncated
download fails here instead of at `docker load`.

Usage:
    python3 pull_image_oci.py --repo unclecode/crawl4ai --tag 0.9.5 \
        --registry docker.m.daocloud.io \
        --auth-realm https://m.daocloud.io/auth/token \
        --out /data/home/fjd/marketops-lab/cache/crawl4ai-oci
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

CHUNK = 1 << 20
# The CDN in front of the mirror answers `User-Agent: Python-urllib/3.x` with
# Cloudflare 1010 before it even looks at the bearer token. Naming the client
# is both allowed and necessary; without it every blob is a 403 that reads
# like an auth failure.
USER_AGENT = "marketops-lab-pull/1.0"


def _get(url: str, token: str | None, accept: str | None = None) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if accept:
        req.add_header("Accept", accept)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def get_token(realm: str, service: str, scope: str) -> str:
    q = urllib.parse.urlencode({"service": service, "scope": scope})
    body = json.loads(_get(f"{realm}?{q}", None))
    token = body.get("token") or body.get("access_token")
    if not token:
        raise RuntimeError(f"no token in auth response keys={list(body)}")
    return token


def download_blob(base: str, repo: str, digest: str, dest: pathlib.Path,
                  token: str, size: int | None) -> tuple[str, int, float]:
    """Stream one blob to ``dest``, hashing as it goes; verify before rename."""
    part = dest.with_suffix(".part")
    started = time.monotonic()
    h = hashlib.sha256()
    written = 0
    req = urllib.request.Request(f"{base}/v2/{repo}/blobs/{digest}")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=120) as resp, part.open("wb") as fh:
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
            fh.write(chunk)
            written += len(chunk)
    got = "sha256:" + h.hexdigest()
    if got != digest:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"digest mismatch {digest}: got {got} ({written} bytes)")
    if size is not None and written != size:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"size mismatch {digest}: {written} != {size}")
    part.rename(dest)
    return digest, written, time.monotonic() - started


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--auth-realm", required=True)
    ap.add_argument("--arch", default="amd64")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    base = f"https://{args.registry}"
    scope = f"repository:{args.repo}:pull"
    token = get_token(args.auth_realm, args.registry, scope)

    out = pathlib.Path(args.out)
    blobs = out / "blobs/sha256"
    blobs.mkdir(parents=True, exist_ok=True)
    (out / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')

    # Resolve tag -> index -> the manifest for one platform.
    accept = ", ".join([
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ])
    raw = _get(f"{base}/v2/{args.repo}/manifests/{args.tag}", token, accept)
    top = json.loads(raw)
    if "manifests" in top:
        chosen = [m for m in top["manifests"]
                  if (m.get("platform") or {}).get("architecture") == args.arch]
        if not chosen:
            raise RuntimeError(f"no {args.arch} manifest in index")
        mdigest = chosen[0]["digest"]
        raw = _get(f"{base}/v2/{args.repo}/manifests/{mdigest}", token, accept)
    if "sha256:" + hashlib.sha256(raw).hexdigest() != mdigest:
        raise RuntimeError("manifest bytes do not hash to their digest")
    manifest = json.loads(raw)
    (blobs / mdigest.split(":", 1)[1]).write_bytes(raw)

    config_digest = manifest["config"]["digest"]
    layers = manifest["layers"]
    total = sum(l["size"] for l in layers)
    print(f"manifest {mdigest[:19]}…  layers={len(layers)}  "
          f"compressed={total/1e6:.1f} MB", flush=True)

    jobs = [(config_digest, manifest["config"]["size"])]
    jobs += [(l["digest"], l["size"]) for l in layers]

    done_bytes = 0
    started = time.monotonic()
    errors = []
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_blob, base, args.repo, digest,
                        blobs / digest.split(":", 1)[1], token, size): digest
            for digest, size in jobs
        }
        for fut in concurrent.futures.as_completed(futures):
            try:
                digest, n, dt = fut.result()
                done_bytes += n
                results.append((digest, n))
                print(f"  ok {digest[7:19]}… {n/1e6:7.1f} MB in {dt:5.1f}s "
                      f"({n/1e6/dt:4.2f} MB/s)  total {done_bytes/1e6:7.1f}/{total/1e6:.1f} MB",
                      flush=True)
            except Exception as exc:  # noqa: BLE001 - reported, not swallowed
                errors.append(f"{futures[fut]}: {exc}")
                print(f"  FAIL {futures[fut]}: {exc}", flush=True)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    # index.json must reference the manifest by the digest of its exact bytes.
    index = {
        "schemaVersion": 2,
        "manifests": [{
            "mediaType": manifest.get("mediaType",
                                      "application/vnd.oci.image.manifest.v1+json"),
            "digest": mdigest,
            "size": len(raw),
            "annotations": {"org.opencontainers.image.ref.name": args.tag},
        }],
    }
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"\nOCI layout -> {out}  ({done_bytes/1e6:.1f} MB in "
          f"{time.monotonic()-started:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

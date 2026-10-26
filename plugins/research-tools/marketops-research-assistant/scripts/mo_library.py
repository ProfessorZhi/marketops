"""One shared library for every project, so no research starts from zero.

    <library>/pages/        every page ever read, one file per reader (the evidence originals)
    <library>/pages.csv     index: url, final_url, reader, file, chars, time
    <library>/search/       search answers per engine and query

<library> is MARKETOPS_LIBRARY, or ~/.marketops/library; point it at a shared folder and a
team shares it. A page read in the last PAGE_DAYS days is reused by any project instead of
being fetched again; a search answer for PAGE/SEARCH_DAYS days. Projects keep their own
grades, briefs and pools - only the raw material is shared.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mo_config

PAGE_DAYS = float(os.environ.get("MARKETOPS_PAGE_DAYS", "30"))
SEARCH_DAYS = float(os.environ.get("MARKETOPS_SEARCH_DAYS", "14"))
COLS = ["url", "final_url", "reader", "file", "chars", "time"]

_lock = threading.Lock()
_index: dict = {"mtime": None, "rows": {}}


def root() -> Path:
    path = Path(os.environ.get("MARKETOPS_LIBRARY") or mo_config.home() / "library")
    (path / "pages").mkdir(parents=True, exist_ok=True)
    (path / "search").mkdir(parents=True, exist_ok=True)
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _flock(path: Path):
    try:
        import fcntl
        fh = open(str(path) + ".lock", "a")
        fcntl.flock(fh, fcntl.LOCK_EX)
        return fh
    except (ImportError, OSError):
        return None


def _load() -> dict[str, list[dict]]:
    idx = root() / "pages.csv"
    try:
        mtime = idx.stat().st_mtime
    except OSError:
        return {}
    if _index["mtime"] != mtime:
        rows: dict[str, list[dict]] = {}
        with open(idx, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.setdefault(r["url"], []).append(r)
        _index.update(mtime=mtime, rows=rows)
    return _index["rows"]


def put_page(url: str, reader: str, body: bytes, ext: str, final_url: str) -> str:
    lib = root()
    name = hashlib.sha256(url.encode()).hexdigest()[:16] + f".{reader}.{ext}"
    path = lib / "pages" / name
    path.write_bytes(body)
    row = {"url": url, "final_url": final_url, "reader": reader, "file": str(path), "chars": len(body),
           "time": _now()}
    with _lock:
        fh = _flock(lib / "pages.csv")
        try:
            new = not (lib / "pages.csv").exists()
            with open(lib / "pages.csv", "a", newline="", encoding="utf-8") as out:
                w = csv.DictWriter(out, fieldnames=COLS)
                if new:
                    w.writeheader()
                w.writerow(row)
        finally:
            if fh is not None:
                fh.close()
        _index["rows"].setdefault(url, []).append(row)
    return str(path)


def get_pages(url: str, days: float = PAGE_DAYS) -> list[dict]:
    """The freshest copy per reader of this URL, if read within `days`."""
    with _lock:
        rows = _load().get(url, [])
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    best: dict[str, dict] = {}
    for r in rows:
        if r["time"] >= since and Path(r["file"]).exists() and r["reader"] != "download":
            if r["reader"] not in best or r["time"] > best[r["reader"]]["time"]:
                best[r["reader"]] = r
    return list(best.values())


def _search_path(key: list) -> Path:
    return root() / "search" / (hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24] + ".json")


def get_search(key: list, days: float = SEARCH_DAYS):
    path = _search_path(key)
    try:
        if time.time() - path.stat().st_mtime < days * 86400:
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return None


def put_search(key: list, rows: list) -> None:
    try:
        _search_path(key).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def stats() -> dict:
    with _lock:
        rows = _load()
    return {"library": str(root()), "pages": sum(len(v) for v in rows.values()), "urls": len(rows),
            "searches": len(list((root() / "search").glob("*.json")))}

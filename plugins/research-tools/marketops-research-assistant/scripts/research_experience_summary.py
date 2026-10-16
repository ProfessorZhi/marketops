#!/usr/bin/env python3
"""One Markdown file the user collects, summarising every Run's bottlenecks.

Member-A should not have to hunt each Run's `experience.json`. This module folds the
append-only journal (events) plus the agent's deliberate notes into a single
cumulative Markdown view at the path chosen during first-use configuration.

Safety properties, each one a deliberate choice:

* **Candidate only.** The file says so in its first paragraph. It never rewrites
  the SOP and never promotes a contact.
* **No inferred root cause.** An event's status is *not* a diagnosis. The auto
  table says ``待人工确认（未推断根因）`` and points at the receipt; only the
  agent's own note may carry a hypothesis, and it is labelled as one.
* **Atomic.** Rendered to a temp file in the same directory and ``os.replace``d,
  so a reader never sees half a file.
* **Never clobbers a newer snapshot.** The first line carries a machine-readable
  count header; if the file on disk already knows about more events, this run
  refuses rather than overwrite it. The whole read-render-write runs inside one
  SQLite ``BEGIN IMMEDIATE`` transaction, so two processes cannot interleave.
* **De-identified.** Only opaque IDs and short text reach the file.

No network, no provider calls, no credentials.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import research_config
import research_event_journal as journal

SNAPSHOT_VERSION = 1
HEADER_FIELDS = ("events", "notes", "runs", "bump_events")
BUMP_STATUSES = journal.BUMP_STATUSES   # one definition, in the journal's own vocabulary
_HEADER_RE = re.compile(r"^<!--\s*marketops:research-experience\s+v(\d+)\s+([^>]*?)-->\s*$")
_NOT_RECORDED = "—"
_NO_ROOT_CAUSE = "待人工确认（未推断根因）"


def _cell(value: object) -> str:
    """Markdown-table safe, and never a formula."""
    text = "" if value is None else str(value)
    text = text.replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()
    if text[:1] in ("=", "+", "-", "@"):
        text = "'" + text
    return text or _NOT_RECORDED


def read_snapshot_header(path: Path) -> dict:
    """Parse the count header, or refuse — an unreadable header must not be clobbered."""
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError) as exc:
        raise ValueError(f"EXPERIENCE_SNAPSHOT_HEADER_INVALID: {type(exc).__name__}") from None
    match = _HEADER_RE.match(first.strip())
    if not match:
        raise ValueError("EXPERIENCE_SNAPSHOT_HEADER_INVALID: first line is not a MarketOps header")
    fields = dict(re.findall(r"(\w+)=(\d+)", match.group(2)))
    if set(fields) != set(HEADER_FIELDS):
        raise ValueError("EXPERIENCE_SNAPSHOT_HEADER_INVALID: unexpected field set")
    return {name: int(value) for name, value in fields.items()}


def _grouped(events: list[dict]) -> list[dict]:
    buckets: dict[tuple[str, str], dict] = {}
    for event in events:
        if event["status"] not in BUMP_STATUSES:
            continue
        key = (event["stage"], event.get("reason_code") or event["status"])
        bucket = buckets.setdefault(key, {"stage": key[0], "category": key[1], "count": 0,
                                          "last": "", "runs": set()})
        bucket["count"] += 1
        bucket["last"] = max(bucket["last"], event["recorded_at"])
        bucket["runs"].add(event["run_id"])
    return [buckets[key] for key in sorted(buckets)]


def render(events: list[dict], notes: list[dict], header: dict) -> str:
    """Pure render: the entire file content, given the journal contents."""
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    bumps = [e for e in events if e["status"] in BUMP_STATUSES]
    runs = sorted({e["run_id"] for e in events} | {n["run_id"] for n in notes})
    lines: list[str] = [
        f"<!-- marketops:research-experience v{SNAPSHOT_VERSION} "
        + " ".join(f"{name}={header[name]}" for name in HEADER_FIELDS) + " -->",
        "# MarketOps 调研经验与卡点汇总（candidate）",
        "",
        "> 本文件由 MarketOps Research Plugin 自动累计刷新，是 **candidate** 视图；**不修改正式SOP**，"
        "不能凭此晋升业务联系人，也不能代替人工复核。",
        "> 你要定期收取的就是这一个文件；单次 Run 的审计原件（experience.json、研究表格）仍留在数据根里。",
        f"> 刷新时间（UTC）：{stamp}",
        "> **无异常不代表零卡点**：这里没有记录到阻断，只说明没有记录到，不等于没有卡点。",
        "> 只保留脱敏 ID 和简短文本；不含邮件、URL、Key/Token/cookie，也不含隐藏思维链。",
        "",
        "## 一、按「阶段 / 问题类型」统计",
        "",
        "| 阶段 | 问题类型 | 次数 | 最近发生(UTC) | 涉及 Run |",
        "| --- | --- | --- | --- | --- |",
    ]
    grouped = _grouped(events)
    if grouped:
        lines += [f"| {_cell(g['stage'])} | {_cell(g['category'])} | {g['count']} | "
                  f"{_cell(g['last'])} | {_cell(', '.join(sorted(g['runs'])))} |" for g in grouped]
    else:
        lines.append(f"| {_NOT_RECORDED} | {_NOT_RECORDED} | 0 | {_NOT_RECORDED} | {_NOT_RECORDED} |")
    lines += [
        "",
        "## 二、卡点明细（聚合事件，自动；不推断根因）",
        "",
        "| 发生时间(UTC) | Run | 工具/原因 | 处理尝试 | 证据 ref | 经验候选 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if bumps:
        lines += [
            f"| {_cell(e['recorded_at'])} | {_cell(e['run_id'])} | "
            f"{_cell((e.get('provider') or 'local') + '/' + (e.get('reason_code') or e['status']))} | "
            f"{_NOT_RECORDED} | research-events.sqlite3#event-{e['sequence']} | {_NO_ROOT_CAUSE} |"
            for e in bumps]
    else:
        lines.append(f"| {_NOT_RECORDED} | {_NOT_RECORDED} | {_NOT_RECORDED} | {_NOT_RECORDED} | "
                     f"{_NOT_RECORDED} | {_NOT_RECORDED} |")
    lines += [
        "",
        "## 三、Agent 记录的方法经验 note（明确写下，不是由状态码猜的）",
        "",
        "| 记录时间(UTC) | Run | 阶段 | 类别 | 问题 | 处理尝试 | 实际结果 | 经验假设 | 证据 ref |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    if notes:
        lines += [f"| {_cell(n['recorded_at'])} | {_cell(n['run_id'])} | {_cell(n['stage'])} | "
                  f"{_cell(n['category'])} | {_cell(n['problem'])} | {_cell(n['action_taken'])} | "
                  f"{_cell(n['observed_result'])} | {_cell(n['hypothesis'])} | "
                  f"{_cell(n['evidence_ref'])} |" for n in notes]
    else:
        lines.append("| " + " | ".join([_NOT_RECORDED] * 9) + " |")
    lines += [
        "",
        "## 四、各 Run 事件计数",
        "",
        "| Run | 事件数 | 卡点数 | note 数 |",
        "| --- | --- | --- | --- |",
    ]
    for run_id in runs:
        run_events = [e for e in events if e["run_id"] == run_id]
        lines.append(f"| {_cell(run_id)} | {len(run_events)} | "
                     f"{len([e for e in run_events if e['status'] in BUMP_STATUSES])} | "
                     f"{len([n for n in notes if n['run_id'] == run_id])} |")
    if not runs:
        lines.append(f"| {_NOT_RECORDED} | 0 | 0 | 0 |")
    lines += [
        "",
        "## 五、本文件如何更新",
        "",
        "每次真实阻断/失败/HOLD/人工纠正，以及每次 Run 的 handoff 或导出结束时，插件会原子地重写本文件。",
        "文件首行是机器可读的计数头：若磁盘上的版本已经知道更多事件，本次刷新会拒绝覆盖（`EXPERIENCE_FILE_IS_NEWER`），",
        "而不是把一个更旧的视图写回去。刷新失败会带 reason code 出现在交付回执里，不会被当成成功。",
        "",
    ]
    return "\n".join(lines)


def _reason(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if code:
        return str(code)
    found = re.search(r"\b([A-Z][A-Z0-9_]{3,})\b", str(exc))
    return found.group(1) if found else (str(exc) or type(exc).__name__)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".experience-", delete=False)
    try:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        if os.path.exists(handle.name):
            os.unlink(handle.name)
        raise


def refresh(*, data_root: str | Path | None = None,
            experience_file: str | Path | None = None,
            env: dict | None = None, platform: str = sys.platform) -> dict:
    """Re-render the cumulative summary from the journal. Safe to call often."""
    env = os.environ if env is None else env
    try:
        root = journal._root(data_root)
        if experience_file is None:
            target = research_config.experience_file(env, platform)
        else:
            target = Path(experience_file).expanduser().resolve()
    except (research_config.ConfigError, ValueError) as exc:
        return {"status": "FAILED", "reason_code": _reason(exc),
                "experience_file": None if experience_file is None else str(experience_file)}
    try:
        with closing(journal._connect(root)) as db:
            # One writer at a time across processes, for the whole read-render-write.
            db.execute("BEGIN IMMEDIATE")
            try:
                events = journal.read_all_events(db)
                # Read the notes on the *same* handle: the whole read-render-write
                # stays inside one transaction, so no writer can slip between them.
                note_rows = db.execute(
                    "SELECT " + ", ".join(journal.NOTE_FIELDS) +
                    " FROM research_notes ORDER BY sequence").fetchall()
                notes = [dict(zip(journal.NOTE_FIELDS, row)) for row in note_rows]
                header = {"events": len(events), "notes": len(notes),
                          "runs": len({e["run_id"] for e in events} | {n["run_id"] for n in notes}),
                          "bump_events": len([e for e in events if e["status"] in BUMP_STATUSES])}
                previous = read_snapshot_header(target) if target.is_file() else None
                if previous and any(previous[name] > header[name] for name in HEADER_FIELDS):
                    db.commit()
                    return {"status": "SKIPPED_NOT_NEWER",
                            "reason_code": "EXPERIENCE_FILE_IS_NEWER",
                            "experience_file": str(target), **header}
                text = render(events=events, notes=notes, header=header)
                _atomic_write(target, text)
                db.commit()
            except BaseException:
                db.rollback()
                raise
        return {"status": "REFRESHED", "experience_file": str(target), **header,
                "bytes": len(text.encode("utf-8")),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "refreshed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    except BaseException as exc:  # noqa: BLE001 - every failure must be observable
        return {"status": "FAILED", "reason_code": _reason(exc),
                "experience_file": str(target)}


def refresh_quietly(**kwargs) -> dict:
    """Refresh for a delivery receipt: never raise, always report a reason code."""
    try:
        return refresh(**kwargs)
    except BaseException as exc:  # pragma: no cover - refresh already catches
        return {"status": "FAILED", "reason_code": _reason(exc), "experience_file": None}


if __name__ == "__main__":  # local convenience
    print(__import__("json").dumps(refresh(), ensure_ascii=False, indent=2))

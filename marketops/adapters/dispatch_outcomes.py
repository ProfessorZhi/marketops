"""派发这一侧**自己写、自己读**的那本退出账：``<dispatch_root>/outcomes.jsonl``。

## 它为什么是一本追加式 JSONL，而不是 SQLite 的一张表

读者只有一个 —— 报告（``application/research_terminal.py``）。它要回答的是「这一件
被派出去过几次、每一次外面怎么判的」，而这件事的权威时刻是**那一轮结束的那一刻**，
不是「现在去问执行器」。一张表会诱使读的人把它当成状态；一本账留下了每一次退出的
原文，包括同一件工作被派出去三次的那三行。

放在 ``dispatch_root`` 下面而不是 worker 的运行目录里：一轮的运行目录按轮次分家，
找「这一件历来的退出」要扫一遍树；而这里一行一件事，读的人按任务 id 收窄就够了。

## 写的人是谁

``interfaces/dispatch_runner.py`` 每跑完一轮写一行，**在收敛之后**写 —— 收敛失败
（看板写不进去）也是这一轮的一个结局，而它比「delegate 退了 0」更重要。一轮跑完
却没有这一行，读起来就是「这件事没有结局」，那是真的。

## 读的宽容度与写相反

写只有一种形状（本模块);读**必须**宽容到「文件不在 = 一次都没跑过（一个事实）」，
而**严格**到「文件在、某一行坏了 = 抛」—— 把坏行读成「没有记录」会让「外面判了
没落地」与「外面什么都没说」在报告里长得一样，而这两件事的处置相反。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..domain.errors import ConfigError
from ..ports.dispatch import WorkerExitRecord

__all__ = [
    "OUTCOMES_FILENAME",
    "append_outcome",
    "outcomes_path",
    "read_worker_outcomes",
]

#: 账文件名。放在 ``dispatch_root`` 下，与 ``board.sqlite3`` 并列。
OUTCOMES_FILENAME = "outcomes.jsonl"

#: 每一行必须有的三格。它们回答「什么时候、哪件事、判成什么」—— 缺一个就读不出
#: 这一行是谁的，而读不出来的行不能当成「没有这一行」。
_OUTCOME_FIELDS = ("ts", "task", "final_state")


def outcomes_path(root: Path) -> Path:
    """账本的位置。**唯一的算法** —— 写与读都从这里取，免得两处各拼一次路径。"""
    return root / OUTCOMES_FILENAME


def append_outcome(
    root: Path,
    *,
    task_id: str,
    final_state: str,
    reason: str | None = None,
    exit_code: int | None = None,
    wall_clock_ms: int | None = None,
    now: datetime | None = None,
) -> None:
    """追加一行。**一个写事务就是一次 ``O_APPEND`` 写** —— 一行 JSON 不超过一次
    管道写的大小，而多进程同时追加的原子性由内核保证；这里不加锁。

    Raises:
        ConfigError: 写不进去。调用方是 runner，它已经跑完了一轮 —— 这一行读不出来
            等于那一轮没有结局，所以要抛，不是静默。
    """
    record = {
        "ts": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "task": task_id,
        "final_state": final_state,
    }
    if reason is not None:
        record["failure_reason"] = reason
    if exit_code is not None:
        record["exit_code"] = exit_code
    if wall_clock_ms is not None:
        record["wall_clock_ms"] = wall_clock_ms
    path = outcomes_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        raise ConfigError(f"这一轮的结局写不进 {path}：{exc}") from exc


def _optional_text(value: object) -> str | None:
    """一个**可以缺失**的字符串。``None`` 是「没写」，不是空串。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(
            f"退出账里这一格应当是一个字符串，读到 {type(value).__name__}"
        )
    return value


def _optional_int(value: object) -> int | None:
    """一个**可以缺失**的整数。``bool`` 不是整数（它是 ``int`` 的子类，会静默通过）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"退出账里这一格应当是一个整数，读到 {type(value).__name__}")
    return value


def read_worker_outcomes(root: Path, task_id: str) -> tuple[WorkerExitRecord, ...]:
    """这本账里关于**一个任务**的行，按写下的顺序。

    Raises:
        ConfigError: 文件在，但某一行不是合法 JSON、不是一个对象、或缺了
            ``ts`` / ``task`` / ``final_state`` 里任何一个。
    """
    path = outcomes_path(root)
    if not path.is_file():
        return ()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"读不出退出账 {path}：{exc}") from exc

    records: list[WorkerExitRecord] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"{path} 第 {number} 行不是合法 JSON：{exc}。\n"
                "读不出来的行不能当成「没有这一行」—— 那会让「外面判了没落地」"
                "与「外面什么都没说」在报告里长得一样。"
            ) from exc
        if not isinstance(row, dict):
            raise ConfigError(f"{path} 第 {number} 行不是一个对象：{type(row).__name__}")
        missing = sorted(key for key in _OUTCOME_FIELDS if key not in row)
        if missing:
            raise ConfigError(
                f"{path} 第 {number} 行缺字段 {missing} —— 那三个是这一行的主语"
                "（什么时候、哪件事、判成什么），缺一个就读不出这行是谁的。"
            )
        if row.get("task") != task_id:
            continue
        records.append(
            WorkerExitRecord(
                task_id=task_id,
                recorded_at=str(row["ts"]),
                final_state=str(row["final_state"]),
                reason=_optional_text(row.get("failure_reason")),
                exit_code=_optional_int(row.get("exit_code")),
                wall_clock_ms=_optional_int(row.get("wall_clock_ms")),
            )
        )
    return tuple(records)


"""一个**假的外部执行器**：照着 ``2026-09-22`` 实测到的行为写，用来测 ``quay_cli``。

它不是 ``unittest.mock`` 那种替身 —— 它是一个**真的子进程**，收真的 argv、写真的
stderr、退真的码。理由只有一条：本适配器最容易错的那两处（列表查询的旗标拼法、
迁移时带不带 ``--title``）**错法都是退 0**，而一个退 0 的错法在集成测试里长得像
成功。要在一个单元测试里把它们抓住，替身就必须**照着真行为失败**，而不是照着
「我们以为它会怎么失败」失败。

所以下面每一条怪癖都有一条实测钉着，出处见 ``marketops/adapters/quay_cli.py``
的 M1–M10：

* 每次调用都往 stderr 打一行横幅（**成功时也有**）—— M1；
* 列表查询的过滤旗标是 ``--label``（单数、可重复、AND），``--labels``
  （复数，建/改任务那个拼法）**静默忽略并返回全表** —— M7。这一条是**刻意**
  实现的：它让「适配器把两种拼法搞反」这个错法在单元测试里就以
  「查到的是全表」的样子暴露出来；
* 对一个已存在的 id ``create``：退 0，``title`` 被改掉（upsert）—— M5；
* 不带 ``--labels`` 的 ``edit``：已有 label 保留 —— M6；
* 带 ``--expect-status`` 且任务不存在：退 1。**两种措辞都实现** —— 不带 ``--title``
  时是 ``… does not exist yet; creating a new task requires --title``（M4），带了
  则是 ``CAS conflict … actual current status is "null"``（M11）。**两种情况任务都
  没有被建出来**，所以 ``--title`` 不是复活钥匙，它降级的是诊断 —— 这正是适配器
  不能在 transition 上带那个旗标的原因，而这个替身必须两种都照着做，
  否则那条判据就是空的（第一版只有一种措辞，变异测试当场证明了它是空的）；
* CAS 冲突：退 1，stdout **空的**，报文加一个假的 Node 栈在 stderr 上 —— M2；
* ``--root`` 下（含向上）没有 ``.quay/config.yml``：退 1 —— M10。

## 注入的失败方式

``FAKE_QUAY_MODE`` 换掉其中几处，用来测适配器**自己**的判据：

| 值 | 行为 | 测的是适配器的哪一条 |
|---|---|---|
| ``ok`` | 如实（缺省） | 正常路径 |
| ``drop_labels`` | ``create`` **静默不写** ``--labels`` | 「回读是判据」——写下去了却查不回来 |
| ``silent`` | 什么都不打印，退 0 | 「退 0 但没有 payload」不能被读成成功 |
| ``nonzero_ok`` | 如实输出，但**退 7** | **正对照**：适配器必须不读退出码。任何人给它加一句 ``if rc: raise``，这里就红 |
| ``garbage`` | stdout 上打一段不是 JSON 的东西 | 输出读不懂 ⇒ 基础设施故障，不是「对面说不行」 |
| ``slow`` | 睡过适配器的超时 | 超时是「没听到答案」，不是「对面拒绝」 |

## 它**不**假装的东西

它不模拟门（``task check``）、不模拟 ``extra``、不模拟生命周期四动词 —— 适配器
今天一个都不用。替身照着**用得到的那一面**写，多出来的部分只会让人以为那条路
被测过了。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

BANNER = "quay-native mcp: serving tasks from {}"


def _die(message: str, *, code: int = 1) -> None:
    print(message, file=sys.stderr)
    sys.exit(code)


def _tasks_file(flags: dict) -> Path:
    """``--root`` 必须指到一个含 ``.quay/config.yml`` 的目录（含向上找）—— M10。"""
    raw = flags.get("--root")
    if not isinstance(raw, str):
        _die("Error: --root is required")
    here = Path(raw).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".quay" / "config.yml").is_file():
            return candidate / ".quay" / "tasks.json"
    _die(
        f'no .quay/config.yml found under --root "{raw}" (searched from there upward). '
        "Point --root at a quay workspace root (a directory containing .quay/config.yml)."
    )
    raise AssertionError("unreachable")


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, tasks: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")


def _parse(argv: list[str]) -> tuple[list[str], dict]:
    """把 argv 收成 ``(位置参数, 旗标)``。重复的旗标收成一个列表（``--label``）。"""
    positional: list[str] = []
    flags: dict[str, object] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("--"):
            name = token
            # 只支持 ``--flag value`` 这一种写法：真的 CLI 也只在文档里承诺这一种，
            # 而 ``--flag=value`` 在这里被当成一个没有值的旗标。
            if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                value = argv[index + 1]
                index += 2
            else:
                value = True
                index += 1
            if name in flags:
                previous = flags[name]
                flags[name] = [*previous, value] if isinstance(previous, list) else [previous, value]
            else:
                flags[name] = value
        else:
            positional.append(token)
            index += 1
    return positional, flags


def _dump(task: dict) -> None:
    print(json.dumps(task, ensure_ascii=False, indent=2))  # noqa: T201 -- 这是 CLI 的 stdout


def _mode() -> str:
    return os.environ.get("FAKE_QUAY_MODE", "ok")


def _do_list(flags: dict, path: Path) -> None:
    tasks = _load(path)
    # **M7 的那个陷阱，刻意保留**：``--label``（单数）过滤，``--labels``（复数）
    # 不认 —— 于是它退 0 并返回**全表**。适配器若把两种拼法搞反，这里就会以
    # 「查到的东西比该查到的多」的样子暴露，而不是以一句报错的样子。
    wanted = flags.get("--label")
    if isinstance(wanted, str):
        wanted = [wanted]
    rows = list(tasks.values())
    if isinstance(wanted, list):
        rows = [row for row in rows if all(label in row["labels"] for label in wanted)]
    print(json.dumps(rows, ensure_ascii=False, indent=2))  # noqa: T201


def _write(task_id: str, flags: dict, tasks: dict, *, creating: bool) -> dict:
    existing = tasks.get(task_id)
    title = flags.get("--title")
    if creating and not existing and not (isinstance(title, str) and title.strip()):
        _die("quay task create: --title <title> is required (and must be non-empty)")
    if "/" in task_id or "\0" in task_id:
        _die(f'Error: invalid task id "{task_id}": must not contain a path separator or null byte')
    if _mode() == "drop_labels":
        labels: list[str] = []
    else:
        raw = flags.get("--labels")
        labels = [part for part in str(raw).split(",") if part] if isinstance(raw, str) else None
    task = dict(existing) if existing else {"id": task_id, "labels": [], "status": "todo"}
    if isinstance(title, str):
        task["title"] = title  # **upsert：建一个已存在的 id 会把标题改掉** —— M5
    if isinstance(flags.get("--status"), str):
        task["status"] = flags["--status"]
    if labels is not None:
        task["labels"] = labels  # ``--labels`` 是**整体替换**（真 CLI 的说明逐字如此）
    tasks[task_id] = task
    return task


def _do_create(positional: list[str], flags: dict, path: Path) -> None:
    if not positional:
        _die("usage: quay task create <task-id> --title <title>")
    task_id = positional[0]
    tasks = _load(path)
    _dump(_write(task_id, flags, tasks, creating=True))
    _save(path, tasks)


def _do_edit(positional: list[str], flags: dict, path: Path) -> None:
    if not positional:
        _die("usage: quay task edit <task-id>")
    task_id = positional[0]
    tasks = _load(path)
    expected = flags.get("--expect-status")
    existing = tasks.get(task_id)
    if isinstance(expected, str):
        if existing is None:
            # **M4 / M11 逐字，两种措辞都保留** —— 而这一对正是适配器不带
            # ``--title`` 的理由：
            #
            # * 没有 ``--title`` ⇒ 一句**说得清**的话：「任务还不存在」；
            # * 有 ``--title`` ⇒ 同一件事被报成一次 **CAS 冲突**，而「读到的状态」
            #   是一个 ``null``。
            #
            # 两边的任务都**没有**被建出来（实测）。所以 ``--title`` 不是复活钥匙，
            # 它降级的是诊断 —— 把一个「映射悬空了，去查谁删的」变成一个「有别人
            # 先写了，按新状态重新决定」。而这两句话的处置相反。
            if "--title" in flags:
                print(
                    f'Error: CAS conflict: CAS conflict on {task_id}: expected status "{expected}" '
                    'but actual current status is "null" — another writer changed it first; '
                    "refusing to overwrite (write() aborted, nothing written to disk)",
                    file=sys.stderr,
                )
                print("    at Object.taskWrite (file:///fake/quay/src/provider-client.ts:98:26)", file=sys.stderr)
                sys.exit(1)
            _die(
                f"quay task edit: task {task_id} does not exist yet; "
                "creating a new task requires --title (or use 'quay task create')"
            )
        if existing.get("status") != expected:
            # **M2 逐字**：退 1，stdout 是**空的**，报文 + 一个栈在 stderr 上。
            print(
                f'Error: CAS conflict: CAS conflict on {task_id}: expected status "{expected}" '
                f'but actual current status is "{existing.get("status")}" — another writer '
                "changed it first; refusing to overwrite (write() aborted, nothing written to disk)",
                file=sys.stderr,
            )
            print("    at Object.taskWrite (file:///fake/quay/src/provider-client.ts:98:26)", file=sys.stderr)
            print("    at async main (file:///fake/quay/bin/quay.ts:1044:5)", file=sys.stderr)
            sys.exit(1)
    _dump(_write(task_id, flags, tasks, creating=False))
    _save(path, tasks)


def _do_view(positional: list[str], flags: dict, path: Path) -> None:
    if not positional:
        _die("usage: quay task view <task-id>")
    tasks = _load(path)
    task = tasks.get(positional[0])
    if task is None:
        _die(f"no such task: {positional[0]}")  # **M4 的另一种措辞**
    _dump(task)


def main() -> None:
    argv = sys.argv[1:]
    mode = _mode()

    # 横幅先打：**成功时也有** —— M1。所以「stderr 非空」不能当失败信号。
    print(BANNER.format("(fake)"), file=sys.stderr)

    if mode == "slow":
        time.sleep(120)
    if mode == "silent":
        sys.exit(0)
    if mode == "garbage":
        print("this is not json at all, and it is not an error message either")
        sys.exit(0)

    positional, flags = _parse(argv)
    path = _tasks_file(flags)

    if positional[:2] == ["task", "list"]:
        _do_list(flags, path)
    elif positional[:2] == ["task", "create"]:
        _do_create(positional[2:], flags, path)
    elif positional[:2] == ["task", "edit"]:
        _do_edit(positional[2:], flags, path)
    elif positional[:2] == ["task", "view"]:
        _do_view(positional[2:], flags, path)
    else:
        _die(f"usage: quay <init|task list|view|create|edit|check|…> ...  (got {argv!r})")

    # **正对照**：输出一切如实，只是退出码不是 0。适配器必须照样接受它 ——
    # 真 CLI 的门失败在 0.11.0 上退 0、在本构建上退 1（M3），那个数字不承载判断。
    if mode == "nonzero_ok":
        sys.exit(7)


if __name__ == "__main__":
    main()

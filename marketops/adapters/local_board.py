"""``Dispatcher`` 的**正式**实现：一张本机 SQLite 任务板。

## 它为什么存在

它是 MarketOps 派发路径的**正式**执行器：端口（``ports/dispatch.py``）与它上面那一层
（``application/dispatch.py``）都不认识任何具体实现 —— 缺的只是一个落点。本模块就是
那个落点：任务是一条本机
SQLite 行，状态是 ``todo`` / ``ready`` / ``running`` / ``done`` / ``needs-human``
这些字符串，查询按 label 的 **AND** 语义。

## 它**不是**一个内存替身

``tests/fakes.py`` 的 ``FakeDispatcher`` 已经是一个实现，而它不能当正式执行器用，
差别不在「有没有持久化」，在三条本模块逐字兑现的性质：

* **跨进程可见。** 派发发生在服务进程（``submit_research`` / ``control_job``），
  领取发生在 runner 进程。字典做不到这件事，而「同一台机器上的两个进程共用一张板」
  正是本地执行器要回答的那个问题。
* **并发靠事务，不靠单线程。** 两个派发者同时到达时，``create`` 的幂等由主键 +
  ``BEGIN IMMEDIATE`` 兑现（理由与 ``adapters/sqlite/db.py`` 那条一模一样）。
* **CAS 是真 CAS。** ``transition`` 是一条 ``UPDATE … WHERE status = ?``，影响行数为
  零就是「有人先写了」—— 一次真 CAS 就是这样，而不是「先读再写」。

## 状态词表是本模块的事，不是端口的事

端口刻意不把外部状态机的成员抄成自己的闭集（``ports/dispatch.py`` 逐字）。所以
``ready`` / ``running`` 这些词住在这里，而 ``application/dispatch.py`` 的
``STOP_REASON_EXTERNAL_STATUS`` 只认识 ``done`` / ``needs-human`` 两个**它自己
写下去**的取值 —— 那是收敛目标，不是一个要校验的闭集。

## 它是**唯一**的执行器

组合根只认两个取值：``local``（本模块）与 ``none``（本部署不派发）。没有第三个可选
的执行器实现，也没有「本地坏了就换一个」的兜底 —— 回退会让两个状态互不相通的板在
同一个服务里各记一半，而两边都正常。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..application.dispatch import external_task_id
from ..domain.errors import ConfigError, DispatchError, ExternalStateError
from ..ports.dispatch import Dispatcher, ExecutionUnit, ExternalTask
from .sqlite.db import Connection

__all__ = [
    "BOARD_FILENAME",
    "READY",
    "RUNNING",
    "LocalBoardDispatcher",
    "ReadyTask",
]

#: 板文件名。放在 ``dispatch_root`` 目录下而不是直接当 ``dispatch_root``：那个根还会
#: 装别的东西（运行记录、锁），而一个「根就是一个 sqlite 文件」的约定会让「这个目录
#: 能不能整个搬走」这个问题在第一次加第二个文件时变成一次迁移。
BOARD_FILENAME = "board.sqlite3"

#: 本机板上的状态词。``READY`` 是**runner 扫的那一个** —— 与
#: ``application/dispatch.py`` 的 ``INITIAL_EXTERNAL_STATUS`` 是同一个词，这不是
#: 巧合：派发与领取两边说的必须是同一件事，否则「哪件活会被领取」会变成两套要分别
#: 记住的规则。
READY = "ready"
#: 已被一个 runner 领走、正在跑。它挡的是「两个 runner 同时领同一件」——CAS 在
#: :meth:`LocalBoardDispatcher.claim` 里，不在调用方。
RUNNING = "running"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_task (
    external_id TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    labels      TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dispatch_task_status ON dispatch_task (status);
"""


def _now() -> str:
    """带时区的 UTC 时刻。与 ``research_worker`` 的 ``run.json`` 同一条处置：
    这一格会被人拿去和别的日志对时间，而本机同时存在两种时区的日志。"""
    return datetime.now(UTC).isoformat()


def _checked_labels(labels: Sequence[str]) -> tuple[str, ...]:
    """一组 label：逐个校验非空，并拒绝**空的那一组**。

    空的那一组不是「全都要」，是「不加过滤」—— 在本地板上它同样是错读：板子里
    混着别的作业的任务，而 ``find`` 的调用方会拿结果决定要不要 ``create``。
    """
    if not labels:
        raise ValueError(
            "至少要一个 label。一次不按 label 收窄的查询会把别的作业的任务也读进来 "
            "—— 那不是「查全部」，那是一次越界读。"
        )
    checked = []
    for label in labels:
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"label 必须是非空字符串，收到 {label!r}")
        if label != label.strip():
            raise ValueError(f"label 带首尾空白：{label!r}")
        checked.append(label)
    return tuple(checked)


def _row_to_task(row: tuple[str, str, str]) -> ExternalTask:
    external_id, status, labels = row
    try:
        parsed = json.loads(labels)
    except json.JSONDecodeError as exc:  # pragma: no cover - 写入口只写合法 JSON
        raise DispatchError(f"本地板上的 {external_id} 有一组读不懂的 label：{exc}") from exc
    return ExternalTask(external_id=external_id, status=status, labels=tuple(parsed))


@dataclass(frozen=True)
class ReadyTask:
    """runner 领到的一件活：外部 id + 它带着的那组 label。

    ``labels`` 逐字带上，因为 runner 下一步要用 ``work_item_id_from_labels`` 从里面
    读回工作单元 —— 让 runner 自己再查一次板子等于把「领的是哪一行」与「读的是哪一行」
    分成两次，而两次之间别人可以改写它。
    """

    external_id: str
    labels: tuple[str, ...]


class LocalBoardDispatcher(Dispatcher):
    """本机 SQLite 任务板。三个入参之外没有缺省值。

    Args:
        root: 任务板**目录**（不是文件）。要求绝对路径：一个相对值会解析到调用方
            站的那棵树里，而服务与 runner 是**两个进程**，它们对「相对在哪」的答案
            可以不同 —— 那时两边各看一张板，且两边都正常。
        timeout_seconds: 一次写事务等锁的上限。并发写是预期行为（多 runner），
            立刻抛 ``database is locked`` 会把「排队」误报成「失败」。

    Raises:
        ConfigError: ``root`` 不是绝对路径、建不出来、或盘上那个文件不是一张能打开的
            SQLite 库。**在构造时抛，不在第一次调用时抛** —— 与
            ``执行器CliDispatcher`` 同一条纪律：一个「建得起来、派发时才炸」的执行器
            在部署自检里完全隐形。
    """

    def __init__(self, *, root: Path, timeout_seconds: float = 30.0) -> None:
        if not root.is_absolute():
            raise ConfigError(
                f"本地执行器的根必须是绝对路径，收到 {root!s}。服务与 runner 是两个"
                "进程，相对路径在它们那里可以解析到两棵不同的树上 —— 而那时两边"
                "各看一张板，各自都正常。"
            )
        self._root = root
        self._path = root / BOARD_FILENAME
        self._timeout = timeout_seconds
        # **构造期不建任何东西。** 这一条是被一处具体的误伤逼出来的：``--check``
        # 会走整份装配，而「自检不落盘」是它的一条明文约定 —— 一个在构造时
        # ``mkdir`` 的执行器会让同一条命令在读数之前把一半故障修掉。
        #
        # 于是这里只回答两件事：根**存在但不是目录**（一个已经坏了的地方）、
        # 任务板文件**存在但打不开**（一个坏库，不是「还没有派发过」）。两者都是
        # 建得起来就一定会遇到的事实，而它们都还留在构造期被发现。
        if root.exists() and not root.is_dir():
            raise ConfigError(f"本地执行器的根不是目录：{root!s}")
        if self._path.exists():
            try:
                with self._connect() as conn:
                    self._require_schema(conn)
            except (sqlite3.Error, OSError) as exc:
                raise ConfigError(
                    f"{self._path} 打不开或建不出表（{exc}）。这个文件不是一张能用的任务板 "
                    "—— 它是一个坏库，不是一次「还没有派发过」。"
                ) from exc

    @property
    def path(self) -> Path:
        """板文件的位置。只给运维读 —— 派发路径不依赖它。"""
        return self._path

    # ------------------------------------------------------------------
    @staticmethod
    def _require_schema(conn: Connection) -> None:
        """这张库认不认得任务板那三列之外的形状。**幂等**，每次连接都跑一次很便宜。"""
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'dispatch_task'"
        ).fetchone()
        if row is None:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        """一次连接 = 一次调用。**写事务用 ``BEGIN IMMEDIATE``**，理由同
        ``adapters/sqlite/db.py``：读到的值与据此写下的值必须在同一个写事务里。

        目录与板文件在这里**才**建出来 —— 构造期不落盘（见 ``__init__`` 那段）。
        """
        try:
            self._root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DispatchError(f"本地执行器的根建不出来：{self._root!s}（{exc}）") from exc
        conn = sqlite3.connect(self._path, timeout=self._timeout, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = %d" % int(self._timeout * 1000))
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("PRAGMA foreign_keys = ON")
            self._require_schema(conn)
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _write_transaction(self) -> Iterator[Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------
    def find(self, *, labels: Sequence[str]) -> tuple[ExternalTask, ...]:
        wanted = _checked_labels(labels)
        placeholders = ",".join("?" for _ in wanted)
        sql = (
            "SELECT external_id, status, labels FROM dispatch_task "
            f"WHERE (SELECT COUNT(DISTINCT value) FROM json_each(dispatch_task.labels) "
            f"WHERE value IN ({placeholders})) = ?"
        )
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, (*wanted, len(set(wanted)))).fetchall()
        except sqlite3.Error as exc:
            # **查询失败必须抛，不能读成零条**：调用方拿 ``find`` 的结果决定要不要
            # ``create``，而「失败读成零条」会稳定地生产重复派发（端口逐字）。
            raise DispatchError(f"本地板查询失败（{exc}）—— 这不是「查到零条」") from exc
        return tuple(_row_to_task(row) for row in rows)

    def create(
        self, unit: ExecutionUnit, *, labels: Sequence[str], status: str
    ) -> ExternalTask:
        wanted = _checked_labels(labels)
        if not status:
            raise ValueError("status 不能为空")
        if not unit.title:
            raise ValueError("unit.title 不能为空 —— 板上那行会变成一行无法辨认的东西")
        if not unit.body:
            raise ValueError("unit.body 不能为空")
        task_id = external_task_id(unit.job_id, unit.unit_id)
        payload = json.dumps(list(wanted), ensure_ascii=False)
        now = _now()
        try:
            with self._write_transaction() as conn:
                # **upsert，与对面那条边界同形**：同一个 id 再建一次是原地对齐，
                # 不是新建。名称与正文会被改写（那正是「对齐」），而 ``status``
                # **不**被改写 —— 重派一件已经在跑的事，把它的状态打回 ``ready``
                # 就是第二次派发。
                conn.execute(
                    "INSERT INTO dispatch_task "
                    "(external_id, status, labels, title, body, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(external_id) DO UPDATE SET "
                    "labels = excluded.labels, title = excluded.title, "
                    "body = excluded.body, updated_at = excluded.updated_at",
                    (task_id, status, payload, unit.title, unit.body, now, now),
                )
        except sqlite3.Error as exc:
            raise DispatchError(f"本地板写不进去（{exc}）") from exc

        # 回读：**判据，不是保险**。写下去却按同一组 label 查不回来，等于这件事
        # 从查询这边看不存在，而本地那条映射下一步就要指向它。
        found = self.find(labels=wanted)
        if len(found) != 1:
            raise DispatchError(
                f"任务 {task_id} 写下去了，但按 {list(wanted)!r} 查回来的是 {len(found)} 条："
                f"{[task.external_id for task in found]!r}。零条 ⇒ label 没落盘；"
                "多条 ⇒ 这一组 label 下的任务不止一个（同一件事被派发了两遍）。"
            )
        (read_back,) = found
        if read_back.external_id != task_id:
            raise DispatchError(
                f"按 {list(wanted)!r} 查回来的是 {read_back.external_id!r}，不是刚写的 "
                f"{task_id!r} —— 两个不同的单元派生出了同一个外部 id。"
            )
        missing = [label for label in wanted if label not in read_back.labels]
        if missing:
            raise DispatchError(
                f"任务 {task_id} 少了这些 label：{missing!r}（现在带的是 "
                f"{list(read_back.labels)!r}）。label 是这条边界上唯一的关联。"
            )
        return read_back

    def transition(self, external_id: str, *, expected: str, status: str) -> ExternalTask:
        if not expected or not status:
            raise ValueError("expected 与 status 都不能为空 —— 一次不写明两端的迁移不是一次 CAS")
        try:
            with self._write_transaction() as conn:
                cur = conn.execute(
                    "UPDATE dispatch_task SET status = ?, updated_at = ? "
                    "WHERE external_id = ? AND status = ?",
                    (status, _now(), external_id, expected),
                )
                changed = cur.rowcount
                row = conn.execute(
                    "SELECT external_id, status, labels FROM dispatch_task WHERE external_id = ?",
                    (external_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise DispatchError(f"本地板写不进去（{exc}）") from exc
        if row is None:
            raise ExternalStateError(
                f"本地板上没有任务 {external_id!r} —— 本地那条映射指向一行不存在的记录。"
            )
        if changed == 0:
            raise ExternalStateError(
                f"CAS 冲突：{external_id} 的当前状态是 {row[1]!r}，不是预期的 {expected!r}。"
            )
        return _row_to_task(row)

    # ------------------------------------------------------------------
    # runner 那一侧（**不在端口上**：端口不给「扫全部」的入口，这里是板自己的队列）
    # ------------------------------------------------------------------
    def claim_next(self, *, limit: int = 1) -> tuple[ReadyTask, ...]:
        """领走最多 ``limit`` 件 ``ready`` 的活，**同一事务里**置为 ``running``。

        读与写必须是同一次操作：先 ``find`` 再 ``transition`` 会让两个 runner 各自
        读到同一行、各自以为领到了它，而两边都会正常跑完 —— 同一件事被做两遍，
        报告上只显示「工作多了一倍」。

        Returns:
            领到的任务，按 ``created_at``（先进先出）排序。空元组 = 板上一件 ready
            都没有，这是**答案**，不是失败。
        """
        if limit < 1:
            raise ValueError(f"limit 必须 ≥ 1，收到 {limit!r}")
        try:
            with self._write_transaction() as conn:
                rows = conn.execute(
                    "SELECT external_id, labels FROM dispatch_task "
                    "WHERE status = ? ORDER BY created_at, external_id LIMIT ?",
                    (READY, limit),
                ).fetchall()
                if rows:
                    now = _now()
                    conn.executemany(
                        "UPDATE dispatch_task SET status = ?, updated_at = ? "
                        "WHERE external_id = ? AND status = ?",
                        [(RUNNING, now, row[0], READY) for row in rows],
                    )
        except sqlite3.Error as exc:
            raise DispatchError(f"本地板领不了活（{exc}）") from exc
        claimed: list[ReadyTask] = []
        for external_id, labels in rows:
            claimed.append(ReadyTask(external_id=external_id, labels=tuple(json.loads(labels))))
        return tuple(claimed)

    def release_to_ready(self, external_id: str) -> None:
        """把一件派出去但没结算的活放回队列，**只从 ``running`` 放**。

        它是 runner 那一侧的 ``release``：一次失败的一轮不是一个结局，而「失败就停在
        ``running``」会让板子上留下一行永远没有人再领的东西 —— 那与「这件事被交出去
        零次」逐字同形（端口 docstring 的第 2 条失败）。
        """
        try:
            with self._write_transaction() as conn:
                conn.execute(
                    "UPDATE dispatch_task SET status = ?, updated_at = ? "
                    "WHERE external_id = ? AND status = ?",
                    (READY, _now(), external_id, RUNNING),
                )
        except sqlite3.Error as exc:
            raise DispatchError(f"本地板放不回队列（{exc}）") from exc

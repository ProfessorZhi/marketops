"""SQLite 判定记录：**主存**。

## 它补的是哪一个洞

``adapters/mcp_server/decisions.py`` 当时有一条 ``_NOT_STORED_NOTE``，逐字承认本构建
不持久化 DecisionRecord（那条常量今天已被 ``_NO_RECORD_STORE`` 取代 —— 后者说的是
「**这个实例**没接记录存储」，不是「本构建存不下来」）。于是「一条判定发生过」只活
在**那一次工具调用的返回值**里：服务重启之后它没有了，换一个客户端它也没有了，而它
是一条**付过费**的记录（Jev 是外部 API，Laya 是本地 checkpoint —— 两者都不是免费的）。
§10 要验收的那条链「decide 回执 → DB 行 → 服务重启 / 新客户端 → 同一个 decision_id
读回」的前半截，在 ``get_decision`` 之前根本不存在。

## 三个键是**行的**，不是回执的

``work_item_id`` / ``subject_ref`` / ``recorded_at`` 由这张表自己产出：前两个描述
「这一行属于谁」，第三个描述「什么时候落的盘」—— 回执里没有它们，而回执也不该有
（``decided_at`` 是**后端什么时候判的**，与**我们什么时候写下的**是两件事，在挂了
重试的路径上它们真的会不同）。

所以 ``put_decision`` 拒绝一个自带这三个键的回执，``_row`` 在读出时把它们合回去。
不拒的话，``{**payload, ...}`` 会让回执里的那个值**静默消失**，而读的人拿到的是一
个看起来完全正常、只是那一格不知来自哪里的对象。

## 为什么 ``check_scope`` 在**两个**地方被调

付费调用**之前**一次（``adapters/mcp_server/decisions.py::_decide``），写事务里
又一次（本模块的 ``put_decision``）。同一个函数、同一条判据，两次调用的理由不同：

* 前一次是为了**别为一次注定会被拒的判定付钱**。Jev 是外部计费 API，而一条指向
  别人的账号、或者解不开的引用，在调用之前就已经能看出来。
* 后一次是**结构性的**。前一次与这次写之间隔着一次真实的网络往返（几百毫秒到几十
  秒），期间任何东西都可以变。只有写事务里的那一次是承重的 —— 没有它，这条不变量
  就只是「调用方记得检查」。

两处都调不是「同一个判据的两个实现」：实现只有 :func:`_check_scope` 一个。

## 归属由外键拒，不由自觉拒

``work_item_id`` 是真外键。一条无法归属的判定记录正是「通用记忆库」的第一块砖，
而库自己拒它，比任何一层「写之前先查一下」都强：后者在并发下两个写者都会通过，
而且它对任何写者都只是自觉。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...domain.errors import StoreError
from ...domain.util import utc_now
from ...ports.decisions import DecisionRecordStore, PinnedScene
from .db import ensure_schema, session, write_transaction
from .jobs import scene_files_from_rows

#: 由**行**产出、不由回执产出的键。见模块 docstring。
#:
#: ``record_id`` **不在**这里：它是回执自带的（内容寻址的那一格，也是这一行的
#: 主键），:func:`_row` 读回时用的就是回执里那一个。把它列进来会同时坏掉两件事
#: —— 每一份合法回执都会被 :func:`_receipt_payload` 拒掉，而理由说的是「回执自
#: 带了行的字段」，读的人会去找一个并不存在的覆盖问题。
_ROW_KEYS = frozenset({"work_item_id", "subject_ref", "recorded_at"})

#: 回执里必须自带的**列来源**：这一行的身份，以及幂等键的三段里出自回执的那两段。
#:
#: ``provider`` 在这里而不是在 :data:`_ROW_KEYS` 里：它是**回执**的事实（§4：谁判
#: 的），这一行只是把它摊成一列以便去重与查询 —— 与 ``case_id`` 同一条，不是
#: 「行产出的字段」。
_REQUIRED = ("record_id", "case_id", "provider")


class SqliteDecisionRecordStore(DecisionRecordStore):
    """基于 SQLite 的判定记录存储。

    构造参数是 ``db_path`` 而不是 ``Settings``：与 ``SqliteEvidenceStore`` /
    ``SqliteBudgetLedger`` 同一个理由 —— 存储必须能在没有配置、没有 ``os.environ``
    的情况下对着一个临时文件被测出来，也必须能和别的存储指同一个库（它们确实是
    同一个库的两张表）。

    建表在 ``__init__`` 里做（``ensure_schema`` 幂等）：构造即验证，一个不可写或
    版本不匹配的库在第一个调用点就报错，而不是在付费调用之后。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    def check_scope(self, work_item_id: str, refs: Sequence[str] = ()) -> str:
        with session(self._path) as conn:
            return _check_scope(conn, work_item_id, refs)

    def decision_for_case(
        self, work_item_id: str, provider: str, case_id: str
    ) -> Mapping[str, Any] | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT * FROM decision_record WHERE work_item_id = ? AND provider = ? "
                "AND case_id = ?",
                (work_item_id, provider, case_id),
            ).fetchone()
        return None if row is None else _row(row)

    def get_decision(self, record_id: str) -> Mapping[str, Any] | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT * FROM decision_record WHERE record_id = ?", (record_id,)
            ).fetchone()
        return None if row is None else _row(row)

    def pinned_scene(self, work_item_id: str) -> PinnedScene | None:
        """见端口。**两个表、同一个连接、同一次读。**

        ``work_item_snapshot`` 与 ``scene_snapshot`` 由 ``compile_work_items`` 的
        同一个写事务写下（``adapters/sqlite/jobs.py``），所以这里读到的两格要么
        都是那一次编译的事实，要么第一格缺席 —— 不存在「版本是新的、字节是旧的」
        那种中间态。分成两次连接读会把这个保证从**存储层**降级成「恰好没有别人
        在中间写」，而那正是 ``SceneFiles`` 那个值要消灭的东西。
        """
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT scene_version FROM work_item_snapshot WHERE work_item_id = ?",
                (work_item_id,),
            ).fetchone()
            if row is None:
                return None
            version = str(row["scene_version"])
            content = conn.execute(
                "SELECT path, content FROM scene_snapshot WHERE scene_version = ? ORDER BY path",
                (version,),
            ).fetchall()
        return PinnedScene(
            scene_version=version,
            files=scene_files_from_rows(content),
        )

    def put_decision(
        self, work_item_id: str, receipt: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        payload = _receipt_payload(receipt)
        with session(self._path) as conn, write_transaction(conn):
            # 同一件工作的主语、以及这些引用在不在它的范围内 —— 见模块 docstring：
            # 这一次是承重的那一次。
            subject_ref = _check_scope(
                conn, work_item_id, tuple(payload.get("evidence_refs") or ())
            )
            try:
                conn.execute(
                    "INSERT INTO decision_record (record_id, work_item_id, subject_ref, "
                    "provider, case_id, payload, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (record_id) DO NOTHING",
                    (
                        payload["record_id"],
                        work_item_id,
                        subject_ref,
                        payload["provider"],
                        payload["case_id"],
                        _dump(payload),
                        utc_now().isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise _collision(conn, work_item_id, payload, exc) from exc
            row = conn.execute(
                "SELECT * FROM decision_record WHERE record_id = ?",
                (payload["record_id"],),
            ).fetchone()
            if row is None:
                # 不可能：上面那一句要么写进去、要么因为冲突抛出来。留一条说得清的
                # 分支，比让下一行的 `row["payload"]` 抛一个 KeyError 好。
                raise StoreError(
                    f"写下的判定记录读不回来（record_id={payload['record_id']!r}）——"
                    "这一条不该发生，库可能已被外部改动。"
                )
            if _load(row["payload"], row["record_id"]) != payload:
                # 主键撞了、内容却不同。「同一个 id 配不同内容」只可能意味着 id 被
                # 手工指定过，而静默保留先写的那条会让「写进去的」与「我以为写进去
                # 的」变成两件事（与 ``SqliteEvidenceStore.put_evidence`` 同一条）。
                raise StoreError(
                    f"已有 record_id={payload['record_id']!r} 的判定记录，但内容与本次"
                    "不同。这个 id 是内容寻址的，所以这只可能意味着它被手工指定过。"
                )
            return _row(row)


# ----------------------------------------------------------------------
def _check_scope(
    conn: sqlite3.Connection, work_item_id: str, refs: Sequence[str]
) -> str:
    """这件工作的主体，以及这些引用是否都在它的可引用范围内。

    三件事一起答，因为它们出自同一次读：没有这件工作、它针对谁、这些引用属于谁。

    引用的**存在性**与**归属**分别报，不合成一句：前者的处置是「那条证据根本没
    抓下来过」，后者是「你引的是别人的东西」—— 后者在 §8 的并发隔离里是最要紧的
    一格，而合成一句之后它会被读成一次笔误。
    """
    row = conn.execute(
        "SELECT subject_ref FROM work_item WHERE work_item_id = ?", (work_item_id,)
    ).fetchone()
    if row is None:
        raise StoreError(
            f"没有 work_item_id={work_item_id!r} 的工作单元，所以这条判定没有主语。\n"
            "判定记录只能挂在**一件真的工作**上 —— 一条无主的记录是「通用模型记忆库」"
            "的第一块砖：它没有主语，于是谁都可以往里写、谁都可以从里读。"
        )
    subject = str(row["subject_ref"])
    for ref in refs:
        found = conn.execute(
            "SELECT account_id FROM evidence WHERE evidence_id = ?", (ref,)
        ).fetchone()
        if found is None:
            raise StoreError(
                f"证据引用 {ref!r} 解不开：状态库里没有这条证据。\n"
                "引用进输入指纹、也逐字进记录，所以收下一条解不开的引用，会让"
                "『任何结论都应当能追到一条已抓取的公开文档』变成一句无法验证的话。"
            )
        owner = str(found["account_id"])
        if owner != subject:
            raise StoreError(
                f"证据引用 {ref!r} 属于账号 {owner!r}，而工作单元 "
                f"{work_item_id!r} 的主语是 {subject!r}。\n"
                "可引用的范围是**这一件工作**的可引用集，不是「库里所有证据」。"
                "跨账号引用在记录上只能看出『它引了两条证据』，读的人无从分辨。"
            )
    return subject


def _collision(
    conn: sqlite3.Connection, work_item_id: str, payload: dict[str, Any], exc: Exception
) -> StoreError:
    """把主键/唯一约束的冲突翻成一句说得清的话。

    会走到这里的只有一种：同一件工作下**同一个后端**已经判过**同一个 case**，
    而那一条的 ``record_id`` 与这一次算出来的**不同**。而这正是「同一个问题被问
    了两次、付了两次费」—— 两次调用的 ``decided_at`` 不同，所以内容寻址的主键
    拦不住第二次。

    这一格**不合并、不覆盖**：两条记录里哪一条算数没有诚实的答案。报出来，让
    重复付费可见。常见成因是两个 worker 同时到达（调用前那次检查两边都通过了），
    而那是真的发生了两次调用 —— 记成一条会让账对不上。
    """
    other = conn.execute(
        "SELECT record_id FROM decision_record WHERE work_item_id = ? AND provider = ? "
        "AND case_id = ?",
        (work_item_id, payload["provider"], payload["case_id"]),
    ).fetchone()
    if other is None:
        return StoreError(
            f"写判定记录失败（record_id={payload['record_id']!r}，"
            f"work_item_id={work_item_id!r}）：{exc}"
        )
    return StoreError(
        f"工作单元 {work_item_id!r} 下 {payload['provider']!r} 已经判过 case "
        f"{payload['case_id']!r}，记录是 {other['record_id']!r}，而这一次算出来的是 "
        f"{payload['record_id']!r}。\n"
        "同一个问题被同一个后端判了两次 —— 也就是**付了两次费**。两条记录不合并："
        "哪一条算数没有诚实的答案。常见成因是两个 worker 同时到达（调用前那次幂等"
        "检查两边都通过了）。要重判，换一件工作单元，或换一个后端（换后端是**另一"
        "次**判定，不是重放，所以它不会被这条拦住）。"
    )


def _receipt_payload(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """校验并取出要落盘的那一份。

    Raises:
        StoreError: 缺 ``record_id`` / ``case_id``，或回执自带了
            :data:`_ROW_KEYS` 里的一个键（见模块 docstring：那会让行自己的事实被
            静默盖掉）。
    """
    if not isinstance(receipt, Mapping):
        raise StoreError(f"回执必须是一个映射，实际拿到 {type(receipt).__name__}")
    payload = dict(receipt)
    for key in _REQUIRED:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise StoreError(
                f"回执缺一个非空的 {key!r}（实际 {value!r}）。"
                "这三个都是这一行的**列来源**：record_id 是行的身份，"
                "provider 与 case_id 是幂等键的两段（少了任何一段，"
                "「同一个问题问第二次」就认不出来，而第二次是另一次付费）。"
            )
    clash = sorted(_ROW_KEYS.intersection(payload))
    if clash:
        raise StoreError(
            f"回执自带了 {clash} —— 这几个是**行**的字段，由存储产出，不由回执产出。\n"
            "收下它们会让读出时那份『行的事实』把回执里的值静默盖掉，而读的人拿到的"
            "是一个看起来完全正常、只是那一格不知来自哪里的对象。"
        )
    return payload


def _row(row: sqlite3.Row) -> dict[str, Any]:
    """一行 → 调用方看到的那个对象：回执 + 这一行自己的三个事实。"""
    return {
        **_load(row["payload"], row["record_id"]),
        "work_item_id": row["work_item_id"],
        "subject_ref": row["subject_ref"],
        "recorded_at": row["recorded_at"],
    }


def _dump(payload: dict[str, Any]) -> str:
    """``sort_keys`` 只为让同一份内容在库里是同一串字节 —— 便于人直接读库、做 diff。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _load(raw: str, where: str) -> dict[str, Any]:
    """读回一行 payload。

    不合法时报错而不是跳过：这个表只有本模块写，一行读不出来意味着库被外部改过或
    损坏了。静默跳过会让「少了一条记录」表现为「这条流程没找到东西」。
    """
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StoreError(
            f"状态库里的判定回执不是合法 JSON（{where}）：{exc}。"
            "人工修复后再继续 —— 静默跳过会污染后续所有统计。"
        ) from exc
    if not isinstance(loaded, dict):
        raise StoreError(f"状态库里的判定回执不是对象（{where}）：{type(loaded).__name__}")
    return loaded


__all__ = ["SqliteDecisionRecordStore"]

"""``ClaimWithdrawalStore`` 的 SQLite 实现 —— 一次撤回的**一个**事务。

## 它为什么读的是 ``outreach_*`` 那三张表，而不是自己的表

撤回不产生新的事实，它**改变已有事实的适用性**：一条说法退场（追加一版，
``expires_at`` 落在撤回那一刻），引用它的草稿进 ``hold``（各出一版新的）。
两半落的都是 ``adapters/sqlite/outreach.py`` 已经在写的那两张表，所以这里
复用的是**表**而不是那个适配器的代码 —— 那一份的行映射是私有的，跨模块 import
一个下划线名字会让「谁改它算破坏契约」说不清。

三张表都**不需要新列**：``outreach_draft`` 已有 ``workspace``（按工作区枚举的
依据），``outreach_attempt`` 已有 ``workspace`` 与 ``draft_id`` / ``draft_version``
（「这份草稿出去过没有」的依据），``outreach_claim`` 的主键 ``(claim_id, revision)``
就是追加一版的依据。

## ``withdraw`` 里那个事务是这条工具的全部承重点

读（``drafts_citing`` / ``attempts``）与写（``withdraw``）分成两次调用，是因为
**判断**要用到读的结果，而判断住在用例层。但判断完之后的那一批写必须在**一个**
``BEGIN IMMEDIATE`` 里 —— 它们的入参是整份要写下去的对象，端口不做领域判断。
"""

from __future__ import annotations

from pathlib import Path

from ...domain.errors import StoreError
from ...domain.outreach import ApprovedClaim, OutboundAttempt, OutreachDraft
from ...ports.claim_withdrawal import ClaimWithdrawalStore
from .db import ensure_schema, session, write_transaction
from .payload import dump_payload as _dump
from .payload import load_payload as _load

#: 每份草稿最新一版的 SQL 片段。与 ``outreach.py::latest_drafts`` 逐字同一条
#: 推理：``MAX`` 在 SQL 里算而不是把整批历史读回内存再 ``max()``。
_LATEST_DRAFTS = (
    "SELECT d.payload AS payload FROM outreach_draft AS d "
    "JOIN (SELECT draft_id, MAX(draft_version) AS v FROM outreach_draft "
    "      WHERE workspace = ? GROUP BY draft_id) AS m "
    "  ON d.draft_id = m.draft_id AND d.draft_version = m.v "
    "ORDER BY d.draft_id"
)


class SqliteClaimWithdrawalStore(ClaimWithdrawalStore):
    def __init__(self, path: Path) -> None:
        self._path = path
        # 与 ``SqliteOutreachStore`` 等每一个 SQLite 存储逐字同一条：建表在构造时
        # 做、幂等。**这一句在这条路径上尤其不能少**：这个端口读的
        # （``outreach_draft`` / ``outbound_attempt``）与写的那张 ``outreach_claim``
        # 分属两张表，而少了建表，一次撤回会在空库上炸在 ``no such table`` ——
        # 那看起来像「这一层的判据写错了」，而它是接线缺了一句。
        with session(self._path) as conn:
            ensure_schema(conn)

    def drafts_citing(self, *, workspace: str, claim_id: str) -> tuple[OutreachDraft, ...]:
        with session(self._path) as conn:
            rows = conn.execute(_LATEST_DRAFTS, (workspace,)).fetchall()
        citing = []
        for row in rows:
            draft = OutreachDraft.model_validate(
                _load(row["payload"], f"outreach_draft/{workspace}")
            )
            # **按引用筛，不按正文筛。** ``claim_ids`` 是草稿自己记下的结构化引用，
            # 而关键词匹配会把两件事同时做错：一条改写过而没引用的草稿被误伤，
            # 一条引用了却没提到字面的草稿被漏掉 —— 两者在报告上都表现为
            # 「撤干净了」。过滤放在 Python 里而不是 SQL 的 ``json_each`` 里，
            # 是因为这一格是**结构化字段**，而不是一段要解析的 JSON。
            if claim_id in draft.claim_ids:
                citing.append(draft)
        return tuple(citing)

    def attempts(self, *, workspace: str) -> tuple[OutboundAttempt, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT payload FROM outreach_attempt WHERE workspace = ? "
                "ORDER BY started_at, send_key",
                (workspace,),
            ).fetchall()
        return tuple(
            OutboundAttempt.model_validate(
                _load(row["payload"], f"outreach_attempt/{workspace}")
            )
            for row in rows
        )

    def withdraw(
        self, *, retirement: ApprovedClaim, drafts: tuple[OutreachDraft, ...]
    ) -> None:
        with session(self._path) as conn, write_transaction(conn):
            self._append_claim(conn, retirement)
            for draft in drafts:
                self._append_draft(conn, draft)

    @staticmethod
    def _append_claim(conn, claim: ApprovedClaim) -> None:
        payload = _dump(claim.model_dump(mode="json"))
        inserted = conn.execute(
            "INSERT INTO outreach_claim (claim_id, revision, payload) VALUES (?, ?, ?) "
            "ON CONFLICT (claim_id, revision) DO NOTHING",
            (claim.claim_id, claim.revision, payload),
        ).rowcount
        if inserted:
            return
        row = conn.execute(
            "SELECT payload FROM outreach_claim WHERE claim_id = ? AND revision = ?",
            (claim.claim_id, claim.revision),
        ).fetchone()
        assert row is not None
        if row["payload"] == payload:
            return
        raise StoreError(
            f"说法 {claim.claim_id!r} 第 {claim.revision} 版已经存在，而这一次的内容"
            "不是同一份。\n"
            "  说法是**追加**的（``ApprovedClaim`` 的 docstring 逐字：「说法改一个字"
            "就是新的一版」）—— 就地改写会让「当时批准的那句话」在盘上变成另一句。"
        )

    @staticmethod
    def _append_draft(conn, draft: OutreachDraft) -> None:
        payload = _dump(draft.model_dump(mode="json"))
        inserted = conn.execute(
            "INSERT INTO outreach_draft (draft_id, draft_version, wave_id, workspace, "
            "body_sha256, generation_key, created_at, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (draft_id, draft_version) DO NOTHING",
            (
                draft.draft_id,
                draft.draft_version,
                draft.wave_id,
                draft.workspace,
                draft.body_sha256,
                draft.generation_key,
                draft.created_at.isoformat(),
                payload,
            ),
        ).rowcount
        if inserted:
            return
        row = conn.execute(
            "SELECT payload FROM outreach_draft WHERE draft_id = ? AND draft_version = ?",
            (draft.draft_id, draft.draft_version),
        ).fetchone()
        assert row is not None
        if row["payload"] == payload:
            return
        raise StoreError(
            f"草稿 {draft.draft_id!r} 第 {draft.draft_version} 版已经存在，而这一次的"
            "内容不是同一份。\n"
            "  这一行会让**整个撤回**回滚（一个事务），所以它不是一次部分成功 —— "
            "见 ``withdraw`` 的 docstring：全成或全不成。"
        )

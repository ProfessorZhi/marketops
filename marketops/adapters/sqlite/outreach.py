"""SQLite 的 ``OutreachStore``。

## 这个文件里只有两处难的东西

其余每一个方法都是「一行 INSERT / 一行 SELECT 再 ``model_validate``」。难的两处是
:meth:`SqliteOutreachStore.begin_attempt` 与 :meth:`SqliteOutreachStore.put_wave`。

### ``begin_attempt``：判据是「这一封现在归我发吗」

它在一个读写之间做判断，而那个判断决定了**一封信会不会被发两遍**：

    rows = conn.execute("INSERT INTO outreach_attempt ... ON CONFLICT DO NOTHING").rowcount
    if rows == 0:  # 已经有别人开的了
        return AttemptOutcome(attempt=<库里那条>, won=False)

``rowcount`` 由**执行插入的那次调用**回答，所以「我拿到了吗」这个问题的答案不需要
调用方去猜。曾经打算让调用方比对 ``attempt`` 数字来推 —— 那个推法是错的：
``begin_attempt`` 在库里那条是 ``FAILED`` 时会把 ``attempt`` 加一然后交出去，于是
两个并发驱动可以各自看到不同的 ``attempt``，而只有一个真的写了行。这与
``MaintenanceStore.claim`` 的 ``won`` 是同一条（``ClaimOutcome``）。

判断与写入落在**同一个** ``BEGIN IMMEDIATE`` 里（``ports/scenes.py`` 与
``adapters/sqlite/db.py`` 的模块 docstring 说明了为什么必须是写事务而不是
``BEGIN DEFERRED``）：两个进程读同一行、各自判「可以发」、再各写一次，这个顺序
在单线程测试里永远正确，只在真的并发时表现为「同一个人收到两封」。

### ``put_wave``：一个 wave 的 manifest 在它声明之前不得被换掉

``Wave.manifest_sha256`` 是一个**重言式**：它总是等于那个 wave 当前那份清单的摘要。
所以它本身没法被写错——除非清单被换过。于是这条约束只能在**清单真的存在**的那些
``wave_id`` 上检查：这次的 revision 在 2 及以上（也就是这个 wave 已经声明过一份清单）
时，库里那份清单必须在，而且摘要必须与本次带上的一致。不一致就报错。

为什么是 revision >= 2 而不是「状态落在那几个之后」：状态的词表会随业务增长，而
revision 的语义（第一次创建是 0，此后每次变更加一）不会。把这条判据挂在状态的
取值上，意味着**下一个** agent 加一个 ``WaveState`` 成员时，这个检查会静默少覆盖
一种情况 —— 而它少覆盖的那一种正是「清单被换了」。这条取舍与 §4 里那条
「不要为了加一个枚举成员就制造双写状态」是同一件事的两面。

## 哪些列摊出来

与 ``scenes.py`` 同一条取舍：出现在 WHERE / ORDER BY / 去重键里的字段摊成列，
其余整块进 ``payload``。这里摊出来的每一列**都被某一处读过**：

* ``outreach_attempt`` 的 ``attempt`` / ``state`` 被 ``begin_attempt`` 的
  冲突分支读（决定「能不能再发一次」）；
* ``outreach_wave`` 的 ``revision`` 被 CAS 的 WHERE 子句读；
* ``outreach_manifest`` 的 ``manifest_sha256`` 被 ``put_wave`` 的一致性检查读。

**没有一处**摊出来的列是「为了好看」。上一轮 ``scene`` 表吃过一次亏：摊出来的列
与 payload 不一致时 CAS 会拿一个错的 revision 去比 —— 而它会**成功**。所以这里
每一个从列重建的对象都在读回来时逐字段校验一次（``_attempt_from_row`` / ``_wave_from_row``）。

## 为什么 ``put_handoff`` 与 ``put_suppression`` 的处置相反

``outreach_handoff`` 是**覆盖写**：交接的状态推进是「这件事正在走」，读的人要的是
当前值。``outreach_suppression`` 是**写不进去**：一条解除之后再收到同一理由的投诉是
两条事实，主键 ``(recipient, reason)`` 让第二次写入失败，而失败正是该有的行为 ——
一个覆盖 ``released_at`` 的实现会让「这个人被解除过抑制吗」永远答不上来。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...domain.errors import (
    IdempotencyConflictError,
    SceneConflictError,
    StoreError,
    WaveConflictError,
)
from ...domain.outreach import (
    DEFAULT_ATTEMPT_LEASE,
    ApprovedClaim,
    AttemptOutcome,
    CorrespondenceEvent,
    MessageState,
    OutreachCampaign,
    OutreachDraft,
    OutreachManifest,
    OutboundAttempt,
    RecipientSuppression,
    SalesHandoff,
    TemplateFamily,
    Wave,
    WaveAuthorization,
    WritingPolicy,
    may_be_settled_by_reconciliation,
)
from ...domain.util import normalise_email, to_utc
from ...ports.outreach import OutreachStore
from .db import ensure_schema, session, write_transaction
from .payload import dump_payload as _dump
from .payload import load_payload as _load

#: ``outreach_attempt`` 里那几个**不只是索引**的列。读回来时与 payload 逐字段比。
_ATTEMPT_COLUMNS = ("workspace", "wave_id", "recipient", "draft_id", "attempt", "state")

#: ``outreach_wave`` 同上。
_WAVE_COLUMNS = (
    "campaign_id",
    "workspace",
    "scene_id",
    "state",
    "manifest_sha256",
    "revision",
)


def _attempt_from_row(row) -> OutboundAttempt:  # noqa: ANN001 - sqlite3.Row
    attempt = OutboundAttempt.model_validate(
        _load(row["payload"], f"outreach_attempt/{row['send_key']}")
    )
    expected = {
        "workspace": attempt.workspace,
        "wave_id": attempt.wave_id,
        "recipient": attempt.recipient,
        "draft_id": attempt.draft_id,
        "attempt": attempt.attempt,
        "state": attempt.state.value,
    }
    for column in _ATTEMPT_COLUMNS:
        if row[column] != expected[column]:
            raise StoreError(
                f"outreach_attempt/{row['send_key']} 的列 {column!r} 是 {row[column]!r}，"
                f"而 payload 里是 {expected[column]!r}。\n"
                "摊出来的列是**判定依据**（begin_attempt 拿 state 决定能不能再发一次），"
                "所以这里不能拿一个自己都不自洽的记录继续跑 —— 停机对账。"
            )
    return attempt


def _wave_from_row(row) -> Wave:  # noqa: ANN001 - sqlite3.Row
    wave = Wave.model_validate(_load(row["payload"], f"outreach_wave/{row['wave_id']}"))
    expected = {
        "campaign_id": wave.campaign_id,
        "workspace": wave.workspace,
        "scene_id": wave.scene_id,
        "state": wave.state.value,
        "manifest_sha256": wave.manifest_sha256,
        "revision": wave.revision,
    }
    for column in _WAVE_COLUMNS:
        if row[column] != expected[column]:
            raise StoreError(
                f"outreach_wave/{row['wave_id']} 的列 {column!r} 是 {row[column]!r}，"
                f"而 payload 里是 {expected[column]!r}。\n"
                "revision 与 manifest_sha256 都会进 CAS 的 WHERE 子句与一致性检查，"
                "列错一个就会让一次「内容变了」被读成「没变」。"
            )
    return wave


def _revalidate(model, replacement: dict):  # noqa: ANN001, ANN202
    """改过的对象**重新过一遍校验器**。

    ``model_copy(update=...)`` **不校验** —— 这是本仓反复踩到的一处：改完直接写回，
    一个已经不满足跨字段约束的对象就落库了，而读回来时它是合法的 JSON，于是
    「这条记录是什么时候坏的」永远查不出来。改动只发生在存储层，所以重验放在
    这里，而不是指望调用方改之前先自己验一遍。
    """
    merged = model.model_dump()
    merged.update(replacement)
    return type(model).model_validate(merged)


class SqliteOutreachStore(OutreachStore):
    """外向链的全部持久化。构造参数是 ``db_path``，不是 ``Settings``。

    与其余 SQLite 适配器同一个理由：存储必须能在没有配置、没有 ``os.environ`` 的
    情况下对着一个临时文件被测出来。建表在 ``__init__`` 里做（``ensure_schema``
    幂等）—— 构造即验证，一个不可写或版本不匹配的库在第一个调用点就报错。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------ 配置

    def put_family(self, family: TemplateFamily) -> None:
        self._put_config(
            table="outreach_family",
            key_column="family_id",
            key=family.family_id,
            revision=family.revision,
            model=family,
            what="模板族",
            extra_columns=(("stage", family.stage.value),),
        )

    def families(self) -> tuple[TemplateFamily, ...]:
        return tuple(
            TemplateFamily.model_validate(_load(row["payload"], f"outreach_family/{row['key']}"))
            for row in self._latest("outreach_family", "family_id")
        )

    def put_writing_policy(self, policy: WritingPolicy) -> None:
        self._put_config(
            table="outreach_writing_policy",
            key_column="policy_id",
            key=policy.policy_id,
            revision=policy.revision,
            model=policy,
            what="写作策略",
        )

    def writing_policies(self) -> tuple[WritingPolicy, ...]:
        return tuple(
            WritingPolicy.model_validate(
                _load(row["payload"], f"outreach_writing_policy/{row['key']}")
            )
            for row in self._latest("outreach_writing_policy", "policy_id")
        )

    def put_claim(self, claim: ApprovedClaim) -> None:
        self._put_config(
            table="outreach_claim",
            key_column="claim_id",
            key=claim.claim_id,
            revision=claim.revision,
            model=claim,
            what="已批准说法",
        )

    def approved_claims(self) -> tuple[ApprovedClaim, ...]:
        return tuple(
            ApprovedClaim.model_validate(_load(row["payload"], f"outreach_claim/{row['key']}"))
            for row in self._latest("outreach_claim", "claim_id")
        )

    def _latest(self, table: str, key_column: str) -> list[sqlite3.Row]:
        """每个 id 的**最新**一版，按 id 排序。

        读路径返回最新版、写路径保留全部版本，这两件事不矛盾：写信要用的是现在的
        话术，而**历史版本必须还在** —— 一条草稿记的是 ``(id, 版)``，把旧版丢掉会让
        「这一稿当时按哪一版写的」变成一句查不到的话。所以这里只挑最新，不删旧的。

        用 ``MAX(revision)`` 而不是 ``ORDER BY revision DESC LIMIT 1``：后者在 id
        上要写一次循环，而循环里每多一次查询就多一次「读到的两行来自两个时刻」的
        机会。
        """
        with session(self._path) as conn:
            rows = conn.execute(
                f"SELECT f.{key_column} AS key, f.payload AS payload FROM {table} f"
                f" JOIN (SELECT {key_column} AS k, MAX(revision) AS r FROM {table}"
                f"       GROUP BY {key_column}) newest"
                f"   ON newest.k = f.{key_column} AND newest.r = f.revision"
                f" ORDER BY f.{key_column}"
            ).fetchall()
        return list(rows)

    def _put_config(
        self,
        *,
        table: str,
        key_column: str,
        key: str,
        revision: int,
        model: Any,
        what: str,
        extra_columns: tuple[tuple[str, Any], ...] = (),
    ) -> None:
        """三张「配置表」共用的一段：**同 ``(id, 版)`` 不同内容是报错，不是覆盖。**

        抽出来是因为三个调用点逐字相同，而不是因为它们将来会一样 —— 它们的**语义**
        已经一样了（模板族、写作策略、已批准说法都是「引用它的东西记的是
        ``(id, 版)``，所以那一格底下的内容不得变」）。

        ## 三条判据，顺序有内容

        1. **那一版在不在**。在，且内容一样 → 幂等返回（同一次发布重放不报错）；
           在，而内容不一样 → 拒绝。这一条就是「历史修订不可变」，而它不是洁癖：
           一条已经生成过的草稿记的是 ``(id, 版)``，改掉那一版的内容会让那句记录
           **指向一份它当时没见过的配置**，而盘上看起来一切正常。
        2. **不在** → 新版本必须**接在最新一版之后**（``revision == latest + 1``）。
           跳版会让中间那些版号的去向没有答案，而报告上只显示「有一个新版本」。
           —— 与 ``OutreachDraft`` 那条 ``supersedes == draft_version - 1`` 同一条
           理由，连拒绝的形状都一样。
        3. 两条都过 → 落一行新的。**旧版一个字节都不动。**

        ## 为什么不靠主键的 ``ON CONFLICT``

        判据一需要把「那一版在不在」与「内容一不一样」分开答，而 ``ON CONFLICT
        DO NOTHING`` 只答前者。更要紧的是：``ON CONFLICT (id, revision)`` 这个名字
        在**旧形状**的库上会抛一句 ``does not match any PRIMARY KEY`` —— 那是一句
        看起来像代码坏了的失败。判据写在 Python 里，这条路径就与表上有没有那个主键
        无关（旧形状的库由 ``ensure_schema`` 的版本判据挡住，见 ``db.py`` 的 v3 → v4）。
        """
        payload = _dump(model.model_dump(mode="json"))
        columns = [key_column, "revision", *(name for name, _ in extra_columns), "payload"]
        values: tuple[Any, ...] = (
            key,
            revision,
            *(value for _, value in extra_columns),
            payload,
        )
        placeholders = ", ".join("?" for _ in columns)
        with session(self._path) as conn, write_transaction(conn):
            row = conn.execute(
                f"SELECT payload FROM {table} WHERE {key_column} = ? AND revision = ?",
                (key, revision),
            ).fetchone()
            if row is not None:
                if row["payload"] == payload:
                    return
                raise StoreError(
                    f"{what} {key!r} 第 {revision} 版已经在库里，而本次的内容与那一版不同。\n"
                    "**不覆盖** —— 引用它的是 ``(id, 版)``，改掉那一版会让已经按它写过"
                    "的草稿指向一份它当时没见过的配置，而盘上看起来一切正常。\n"
                    f"要改内容，请发布下一版（第 {revision + 1} 版）。"
                )
            latest = conn.execute(
                f"SELECT MAX(revision) AS newest FROM {table} WHERE {key_column} = ?",
                (key,),
            ).fetchone()
            newest = None if latest is None else latest["newest"]
            if newest is not None and revision != newest + 1:
                raise StoreError(
                    f"{what} {key!r} 现在最新的版本是第 {newest} 版，而本次要落的是"
                    f"第 {revision} 版。\n"
                    f"**新版本必须接在最新一版之后**（本次应当是第 {newest + 1} 版）——"
                    "跳版会让中间那些版号的去向没有答案，而报告上只显示「多了一个版本」。"
                )
            conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", values
            )

    # ------------------------------------------------- 活动、批次、清单、授权

    def put_campaign(self, campaign: OutreachCampaign) -> None:
        payload = _dump(campaign.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            inserted = conn.execute(
                "INSERT INTO outreach_campaign (campaign_id, workspace, scene_id, payload) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (campaign_id) DO NOTHING",
                (campaign.campaign_id, campaign.workspace, campaign.scene_id, payload),
            ).rowcount
            if inserted:
                return
            row = conn.execute(
                "SELECT workspace, scene_id, payload FROM outreach_campaign "
                "WHERE campaign_id = ?",
                (campaign.campaign_id,),
            ).fetchone()
            assert row is not None
            if (row["workspace"], row["scene_id"]) != (
                campaign.workspace,
                campaign.scene_id,
            ):
                raise SceneConflictError(
                    f"活动 {campaign.campaign_id!r} 属于 workspace={row['workspace']!r} / "
                    f"scene={row['scene_id']!r}，本次声称的是 "
                    f"workspace={campaign.workspace!r} / scene={campaign.scene_id!r}。\n"
                    "一个活动**不跨 Scene** —— 跨 Scene 的活动就是两个活动。"
                )
            if row["payload"] != payload:
                raise StoreError(
                    f"活动 {campaign.campaign_id!r} 已经存在，而本次的内容不同。不覆盖。"
                )

    def campaign(self, campaign_id: str) -> OutreachCampaign | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT payload FROM outreach_campaign WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
        if row is None:
            return None
        return OutreachCampaign.model_validate(
            _load(row["payload"], f"outreach_campaign/{campaign_id}")
        )

    def put_draft(self, draft: OutreachDraft) -> None:
        payload = _dump(draft.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
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
                "SELECT body_sha256, payload FROM outreach_draft "
                "WHERE draft_id = ? AND draft_version = ?",
                (draft.draft_id, draft.draft_version),
            ).fetchone()
            assert row is not None
            if row["payload"] == payload:
                return
            raise StoreError(
                f"草稿 {draft.draft_id!r} 第 {draft.draft_version} 版已经存在，"
                f"而本次的内容不同（库里那版正文摘要 {row['body_sha256'][:16]}…，"
                f"本次是 {draft.body_sha256[:16]}…）。\n"
                "**不覆盖** —— 覆盖会让一份绑在旧正文摘要上的授权静默继续有效，"
                "而盘上看起来只是「再生成了一次」。要改内容就出一版新的。"
            )

    def draft(self, draft_id: str, version: int) -> OutreachDraft | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT payload FROM outreach_draft WHERE draft_id = ? AND draft_version = ?",
                (draft_id, version),
            ).fetchone()
        if row is None:
            return None
        return OutreachDraft.model_validate(
            _load(row["payload"], f"outreach_draft/{draft_id}@{version}")
        )

    def latest_drafts(self, *, wave_id: str) -> tuple[OutreachDraft, ...]:
        """每一份草稿的**最新一版**。

        ``MAX(draft_version)`` 在 SQL 里算而不是在 Python 里 ``max()`` —— 后者要求
        先把这一批的**全部**版本读进内存，而草稿正文每一版几千字符、一批几十封、
        改上几版就是几十万字符；一个「先全读回来再挑最大的」的实现会让一次
        「看一眼这批到哪了」的调用把整批历史都划一遍。
        """
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT d.payload FROM outreach_draft AS d "
                "JOIN (SELECT draft_id, MAX(draft_version) AS v FROM outreach_draft "
                "      WHERE wave_id = ? GROUP BY draft_id) AS m "
                "  ON d.draft_id = m.draft_id AND d.draft_version = m.v "
                "ORDER BY d.draft_id",
                (wave_id,),
            ).fetchall()
        return tuple(
            OutreachDraft.model_validate(_load(row["payload"], f"outreach_draft/{wave_id}"))
            for row in rows
        )

    def put_manifest(self, manifest: OutreachManifest) -> None:
        payload = _dump(manifest.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            inserted = conn.execute(
                "INSERT INTO outreach_manifest (wave_id, campaign_id, workspace, scene_id, "
                "manifest_sha256, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (wave_id) DO NOTHING",
                (
                    manifest.wave_id,
                    manifest.campaign_id,
                    manifest.workspace,
                    manifest.scene_id,
                    manifest.manifest_sha256,
                    manifest.created_at.isoformat(),
                    payload,
                ),
            ).rowcount
            if inserted:
                return
            row = conn.execute(
                "SELECT manifest_sha256, payload FROM outreach_manifest WHERE wave_id = ?",
                (manifest.wave_id,),
            ).fetchone()
            assert row is not None
            if row["payload"] == payload:
                return
            raise StoreError(
                f"批次 {manifest.wave_id!r} 已经有一份清单，而本次这份的内容不同"
                f"（库里 {row['manifest_sha256'][:16]}…，本次 "
                f"{manifest.manifest_sha256[:16]}…）。\n"
                "**不覆盖** —— 换掉一份清单会让绑在旧摘要上的授权全部失去意义，"
                "而盘上只显示「清单更新了」。要换内容就换一个 wave。"
            )

    def manifest(self, wave_id: str) -> OutreachManifest | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT payload FROM outreach_manifest WHERE wave_id = ?", (wave_id,)
            ).fetchone()
        if row is None:
            return None
        return OutreachManifest.model_validate(
            _load(row["payload"], f"outreach_manifest/{wave_id}")
        )

    def put_wave(self, wave: Wave, *, expected_revision: int | None) -> None:
        if expected_revision is None:
            if wave.revision != 0:
                raise StoreError(
                    f"首次创建批次 {wave.wave_id!r} 时 revision 必须是 0，"
                    f"本次是 {wave.revision}。\n"
                    "带着非零 revision 创建，会让一次「这个批次已经存在」变成"
                    "「我写的是第 n 版」—— 而库里那一条的 revision 与它毫无关系。"
                )
            self._insert_wave(wave)
            return
        if wave.revision != expected_revision + 1:
            raise StoreError(
                f"批次 {wave.wave_id!r} 的写入 revision 必须是 {expected_revision + 1}"
                f"（读到的 {expected_revision} 加一），本次是 {wave.revision}。"
            )
        self._update_wave(wave, expected_revision=expected_revision)

    def _check_wave_manifest_agrees(self, conn, wave: Wave) -> None:  # noqa: ANN001
        """见模块 docstring 的 ``put_wave`` 一节。

        只在 revision 落在 2 及以上时检查：revision 0（刚创建）与 1（第一次状态变更）
        都可能发生在清单被写下来之前，而那时它必须等于入库时带的那个值 ——
        也就是它本来就是重言式。从 2 起，这个批次已经**声明过**一份清单了。
        """
        if wave.revision < 2:
            return
        row = conn.execute(
            "SELECT manifest_sha256 FROM outreach_manifest WHERE wave_id = ?",
            (wave.wave_id,),
        ).fetchone()
        if row is None:
            raise StoreError(
                f"批次 {wave.wave_id!r} 已经走到 revision={wave.revision}，"
                "而它**没有**一份冻结清单。\n"
                "一份没有清单的批次不得进入授权与投递 —— 授权绑的就是清单摘要，"
                "没有清单时那句「授权覆盖这一批」无从判定。"
            )
        if row["manifest_sha256"] != wave.manifest_sha256:
            raise StoreError(
                f"批次 {wave.wave_id!r} 现在的清单摘要与库里那一份不一致"
                f"（库里 {row['manifest_sha256'][:16]}…，本次 {wave.manifest_sha256[:16]}…）。\n"
                "一个批次的清单在写下之后**不得被换掉**：授权绑的是摘要，"
                "换掉之后那些授权在记录上依然存在、依然「有效」，而它们批的是另一批信。"
            )

    def _insert_wave(self, wave: Wave) -> None:
        with session(self._path) as conn, write_transaction(conn):
            self._check_wave_manifest_agrees(conn, wave)
            inserted = conn.execute(
                "INSERT INTO outreach_wave (wave_id, campaign_id, workspace, scene_id, state, "
                "manifest_sha256, revision, created_at, updated_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (wave_id) DO NOTHING",
                (
                    wave.wave_id,
                    wave.campaign_id,
                    wave.workspace,
                    wave.scene_id,
                    wave.state.value,
                    wave.manifest_sha256,
                    wave.revision,
                    wave.created_at.isoformat(),
                    wave.updated_at.isoformat(),
                    _dump(wave.model_dump(mode="json")),
                ),
            ).rowcount
            if inserted:
                return
            row = conn.execute(
                "SELECT workspace, revision FROM outreach_wave WHERE wave_id = ?",
                (wave.wave_id,),
            ).fetchone()
            assert row is not None
            raise IdempotencyConflictError(
                f"批次 {wave.wave_id!r} 已经存在（工作区 {row['workspace']!r}，"
                f"revision={row['revision']}），本次调用声称它是首次创建。\n"
                "这条判据不看内容是否相同：`expected_revision=None` 的承诺是"
                "「这是我建的第一个」，而一个已经存在的行让它不成立。"
            )

    def _update_wave(self, wave: Wave, *, expected_revision: int) -> None:
        with session(self._path) as conn, write_transaction(conn):
            self._check_wave_manifest_agrees(conn, wave)
            changed = conn.execute(
                "UPDATE outreach_wave SET campaign_id = ?, workspace = ?, scene_id = ?, "
                "state = ?, manifest_sha256 = ?, revision = ?, created_at = ?, updated_at = ?, "
                "payload = ? WHERE wave_id = ? AND revision = ?",
                (
                    wave.campaign_id,
                    wave.workspace,
                    wave.scene_id,
                    wave.state.value,
                    wave.manifest_sha256,
                    wave.revision,
                    wave.created_at.isoformat(),
                    wave.updated_at.isoformat(),
                    _dump(wave.model_dump(mode="json")),
                    wave.wave_id,
                    expected_revision,
                ),
            ).rowcount
            if changed:
                return
            # 三种原因分别报：不存在 / 版本不是我以为的那个 / 属于别的工作区。
            # 合成一句「冲突了」，读的人无法判断该去创建、该重读、还是该停下查根目录。
            current = conn.execute(
                "SELECT workspace, revision FROM outreach_wave WHERE wave_id = ?",
                (wave.wave_id,),
            ).fetchone()
            if current is None:
                raise WaveConflictError(
                    f"批次 {wave.wave_id!r} 不存在，本次调用以为它在第 "
                    f"{expected_revision} 版。一个字都没写。"
                )
            raise WaveConflictError(
                f"批次 {wave.wave_id!r} 现在是第 {current['revision']} 版，"
                f"本次调用读到的是第 {expected_revision} 版。一个字都没写。\n"
                f"库里那条属于工作区 {current['workspace']!r}，本次写的是 "
                f"{wave.workspace!r} —— 若两者不同，这更可能是一次配错根目录的写入。"
            )

    def wave(self, wave_id: str) -> Wave | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT * FROM outreach_wave WHERE wave_id = ?", (wave_id,)
            ).fetchone()
        if row is None:
            return None
        return _wave_from_row(row)

    def put_authorization(self, authorization: WaveAuthorization) -> None:
        payload = _dump(authorization.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            inserted = conn.execute(
                "INSERT INTO outreach_authorization (authorization_id, wave_id, workspace, "
                "scene_id, authorized_at, expires_at, revoked_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (authorization_id) DO NOTHING",
                (
                    authorization.authorization_id,
                    authorization.wave_id,
                    authorization.workspace,
                    authorization.scene_id,
                    authorization.authorized_at.isoformat(),
                    authorization.expires_at.isoformat(),
                    None
                    if authorization.revoked_at is None
                    else authorization.revoked_at.isoformat(),
                    payload,
                ),
            ).rowcount
            if inserted:
                return
            row = conn.execute(
                "SELECT payload FROM outreach_authorization WHERE authorization_id = ?",
                (authorization.authorization_id,),
            ).fetchone()
            assert row is not None
            if row["payload"] == payload:
                return
            raise StoreError(
                f"授权 {authorization.authorization_id!r} 已经存在，而本次的内容不同。\n"
                "**不覆盖** —— 一次撤回应当是一条**新**记录（旧的那条留着），"
                "因为「谁在什么时候撤回了什么」正是事后要查的第一件事。"
                "就地改写那条记录会让那次撤回在盘上从来没有发生过。"
            )

    def authorizations(self, *, wave_id: str) -> tuple[WaveAuthorization, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT payload FROM outreach_authorization WHERE wave_id = ? "
                "ORDER BY authorized_at, authorization_id",
                (wave_id,),
            ).fetchall()
        return tuple(
            WaveAuthorization.model_validate(
                _load(row["payload"], f"outreach_authorization/{wave_id}")
            )
            for row in rows
        )

    # ------------------------------------------------------------ 投递

    def attempt(self, send_key: str) -> OutboundAttempt | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT * FROM outreach_attempt WHERE send_key = ?", (send_key,)
            ).fetchone()
        if row is None:
            return None
        return _attempt_from_row(row)

    def begin_attempt(
        self, attempt: OutboundAttempt, *, at: datetime, lease: timedelta | None = None
    ) -> AttemptOutcome:
        with session(self._path) as conn, write_transaction(conn):
            current = conn.execute(
                "SELECT * FROM outreach_attempt WHERE send_key = ?", (attempt.send_key,)
            ).fetchone()

            if current is not None:
                existing = _attempt_from_row(current)
                if not existing.is_stale_at(at, lease=lease or DEFAULT_ATTEMPT_LEASE):
                    # **这一封不是我的** —— 已定局，或者开出去还没到 lease。
                    return AttemptOutcome(attempt=existing, won=False)
                # 库里那条还没定局（NOT_SENT）或上一次失败了 —— 这是**同一个
                # send_key 的第 n 次尝试**，尝试次数由行上的值加一，不由调用方给。
                opened = _revalidate(
                    attempt,
                    {
                        "attempt": existing.attempt + 1,
                        "state": MessageState.NOT_SENT,
                        "started_at": attempt.started_at,
                        "finished_at": None,
                        "provider_message_id": "",
                        "failure": "",
                    },
                )
                changed = conn.execute(
                    "UPDATE outreach_attempt SET attempt = ?, state = ?, started_at = ?, "
                    "finished_at = NULL, payload = ? WHERE send_key = ? AND attempt = ?",
                    (
                        opened.attempt,
                        opened.state.value,
                        opened.started_at.isoformat(),
                        _dump(opened.model_dump(mode="json")),
                        attempt.send_key,
                        existing.attempt,
                    ),
                ).rowcount
                if changed != 1:
                    raise StoreError(
                        f"outreach_attempt/{attempt.send_key} 的重试写入了 "
                        f"{changed} 行 —— 期望恰好 1 行。\n"
                        "同一事务里刚读到第 "
                        f"{existing.attempt} 次尝试还没定局，写却说它已经不是了："
                        "那意味着有写者绕过了本库的写事务序。"
                    )
                return AttemptOutcome(attempt=opened, won=True)

            inserted = conn.execute(
                "INSERT INTO outreach_attempt (send_key, workspace, wave_id, recipient, "
                "draft_id, attempt, state, started_at, finished_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (send_key) DO NOTHING",
                (
                    attempt.send_key,
                    attempt.workspace,
                    attempt.wave_id,
                    attempt.recipient,
                    attempt.draft_id,
                    attempt.attempt,
                    attempt.state.value,
                    attempt.started_at.isoformat(),
                    None,
                    _dump(attempt.model_dump(mode="json")),
                ),
            ).rowcount
            if inserted != 1:
                # 同一事务里刚读到「不存在」，插入却说这一行已存在 ——
                # 那意味着有写者绕过了本库的写事务序。失败关闭。
                raise StoreError(
                    f"outreach_attempt/{attempt.send_key} 的插入写入了 {inserted} 行 —— "
                    "期望恰好 1 行。有写者绕过了一次性写入序。"
                )
            return AttemptOutcome(attempt=attempt, won=True)

    def finish_attempt(self, attempt: OutboundAttempt) -> None:
        if attempt.finished_at is None:
            raise StoreError(
                f"结算 outreach_attempt/{attempt.send_key} 时没有完成时刻。\n"
                "一条已定局的尝试必须答得出「什么时候结束的」—— 缺了它，"
                "「这一封挂了多久」在事后无法回答。"
            )
        with session(self._path) as conn, write_transaction(conn):
            row = conn.execute(
                "SELECT * FROM outreach_attempt WHERE send_key = ?", (attempt.send_key,)
            ).fetchone()
            if row is None:
                raise StoreError(
                    f"没有 send_key={attempt.send_key!r} 这条尝试，不能结算。\n"
                    "结算一条**没开始过**的投递，意味着有人凭空写下了一次发送。"
                )
            current = _attempt_from_row(row)
            if current.state is not MessageState.NOT_SENT:
                if current.state is attempt.state:
                    return
                if not may_be_settled_by_reconciliation(current, attempt):
                    raise StoreError(
                        f"send_key={attempt.send_key!r} 已经以 {current.state.value} 定局，"
                        f"不能改成 {attempt.state.value}。\n"
                        "同一个逻辑发送的结局是一次性写下的：允许改写它，"
                        "「这一封到底发出去了没有」就没有答案了。"
                    )
            if attempt.attempt != current.attempt:
                raise StoreError(
                    f"send_key={attempt.send_key!r} 现在在第 {current.attempt} 次尝试，"
                    f"而本次结算声称是第 {attempt.attempt} 次。\n"
                    "写错行会让「第 1 次失败、第 2 次成功」被记成「第 1 次成功」——"
                    "而那一行会告诉下一个驱动这一封已经发过了。"
                )
            # CAS 比的是**刚读到的那一个状态**，不是写死的 ``NOT_SENT``：
            # 对账那一次改判（``DELIVERY_UNKNOWN`` → 一个确定答案）走的是同一
            # 条写入，而它读到的当前状态不是 ``NOT_SENT``。写死会让那次更新
            # 一行都改不到，然后被下面那条「有写者绕过了一次性写入序」挡下 ——
            # 判据本身没错，错的是这里有第二个合法的起点。
            changed = conn.execute(
                "UPDATE outreach_attempt SET state = ?, finished_at = ?, payload = ? "
                "WHERE send_key = ? AND attempt = ? AND state = ?",
                (
                    attempt.state.value,
                    attempt.finished_at.isoformat(),
                    _dump(attempt.model_dump(mode="json")),
                    attempt.send_key,
                    current.attempt,
                    current.state.value,
                ),
            ).rowcount
            if changed != 1:
                raise StoreError(
                    f"结算 outreach_attempt/{attempt.send_key} 写入了 {changed} 行 —— "
                    "期望恰好 1 行。有写者绕过了一次性写入序。"
                )

    def attempts(self, *, wave_id: str) -> tuple[OutboundAttempt, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT * FROM outreach_attempt WHERE wave_id = ? "
                "ORDER BY recipient, draft_id",
                (wave_id,),
            ).fetchall()
        return tuple(_attempt_from_row(row) for row in rows)

    def attempts_by_recipient(self, recipient: str) -> tuple[OutboundAttempt, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT * FROM outreach_attempt WHERE recipient = ? "
                "ORDER BY started_at, wave_id, draft_id",
                (normalise_email(recipient),),
            ).fetchall()
        return tuple(_attempt_from_row(row) for row in rows)

    # ------------------------------------------------------------ 抑制

    def put_suppression(self, suppression: RecipientSuppression) -> None:
        payload = _dump(suppression.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            inserted = conn.execute(
                "INSERT INTO outreach_suppression (recipient, reason, source_ref, "
                "suppressed_at, released_at, payload) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (recipient, reason) DO NOTHING",
                (
                    suppression.recipient,
                    suppression.reason.value,
                    suppression.source_ref,
                    suppression.suppressed_at.isoformat(),
                    None
                    if suppression.released_at is None
                    else suppression.released_at.isoformat(),
                    payload,
                ),
            ).rowcount
            if inserted:
                return
            row = conn.execute(
                "SELECT payload FROM outreach_suppression WHERE recipient = ? AND reason = ?",
                (suppression.recipient, suppression.reason.value),
            ).fetchone()
            assert row is not None
            if row["payload"] == payload:
                return
            raise StoreError(
                f"{suppression.recipient!r} 因为 {suppression.reason.value} 已经在抑制名单里，"
                "而本次这一条与库里那条不同。\n"
                "**不覆盖** —— 一条解除之后再收到同一理由的投诉是**两条**事实。"
                "就地改写会让「这个人被解除过抑制吗」永远答不上来，"
                "而那正是下一次事故里要查的第一件事。"
            )

    def suppressions(self, recipient: str) -> tuple[RecipientSuppression, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT payload FROM outreach_suppression WHERE recipient = ? "
                "ORDER BY suppressed_at, reason",
                (normalise_email(recipient),),
            ).fetchall()
        return tuple(
            RecipientSuppression.model_validate(
                _load(row["payload"], f"outreach_suppression/{recipient}")
            )
            for row in rows
        )

    def is_suppressed(self, recipient: str, *, at: datetime) -> bool:
        """**跨批次**查，且**不读缓存**。

        抑制名单会被另一个进程改（投诉进来那一刻），而一个读缓存的实现会让
        「他刚投诉完、我们又发了一封」这件事在盘上看起来完全正常。判据是
        ``released_at is None or at < released_at`` —— 一条已经解除的抑制
        不该继续挡住发送。

        **不在 SQL 里判 ``at``**：``released_at`` 是 ISO-8601 字符串，而它的字符串
        顺序只在偏移量相同时才等于瞬间的先后，所以「用 SQL 比一下更快」这个
        优化会静默地在非 UTC 偏移上给出相反的答案。判定用 ``is_active_at``，
        而它拿的是已经规范化的时刻 —— 那条规范化由 ``to_utc`` 保证。
        """
        moment = to_utc(at)
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT payload FROM outreach_suppression WHERE recipient = ?",
                (normalise_email(recipient),),
            ).fetchall()
        return any(
            RecipientSuppression.model_validate(
                _load(row["payload"], f"outreach_suppression/{recipient}")
            ).is_active_at(moment)
            for row in rows
        )

    # ------------------------------------------------------- 事件与交接

    def put_event(self, event: CorrespondenceEvent) -> bool:
        payload = _dump(event.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            inserted = conn.execute(
                "INSERT INTO outreach_event (event_id, dedupe_key, workspace, sendbox, kind, "
                "received_at, ingested_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (dedupe_key) DO NOTHING",
                (
                    event.event_id,
                    event.dedupe_key,
                    event.workspace,
                    event.sendbox,
                    event.kind.value,
                    event.received_at.isoformat(),
                    event.ingested_at.isoformat(),
                    payload,
                ),
            ).rowcount
            if inserted:
                return True
            # 重复：**不覆盖**。重放的原件与「两条一样的事件」是两件事，
            # 而前者不该让后者的记录变样 —— 尤其不该把它的 disposition 改掉。
            row = conn.execute(
                "SELECT event_id, payload FROM outreach_event WHERE dedupe_key = ?",
                (event.dedupe_key,),
            ).fetchone()
            assert row is not None
            if row["payload"] == payload:
                return False
            raise StoreError(
                f"事件去重键 {event.dedupe_key[:16]}… 已经存在"
                f"（event_id={row['event_id']!r}），而本次这条的内容不同。\n"
                "同一个 (sendbox, provider_message_id) 下的两条**不同**事件的处置"
                "只能由人决定：既可能是提供方复用了消息 id，也可能是我们解析错了。"
                "静默丢掉新的那条会让第二种情况永远查不出来。"
            )

    def event(self, event_id: str) -> CorrespondenceEvent | None:
        with session(self._path) as conn:
            row = conn.execute(
                "SELECT payload FROM outreach_event WHERE event_id = ?", (event_id,)
            ).fetchone()
        if row is None:
            return None
        return CorrespondenceEvent.model_validate(
            _load(row["payload"], f"outreach_event/{event_id}")
        )

    def events(self, *, workspace: str) -> tuple[CorrespondenceEvent, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT payload FROM outreach_event WHERE workspace = ? "
                "ORDER BY received_at, event_id",
                (workspace,),
            ).fetchall()
        return tuple(
            CorrespondenceEvent.model_validate(_load(row["payload"], f"outreach_event/{workspace}"))
            for row in rows
        )

    def put_handoff(self, handoff: SalesHandoff) -> None:
        payload = _dump(handoff.model_dump(mode="json"))
        with session(self._path) as conn, write_transaction(conn):
            conn.execute(
                "INSERT INTO outreach_handoff (handoff_id, workspace, scene_id, state, "
                "created_at, payload) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (handoff_id) DO UPDATE SET state = excluded.state, "
                "payload = excluded.payload",
                (
                    handoff.handoff_id,
                    handoff.workspace,
                    handoff.scene_id,
                    handoff.state.value,
                    handoff.created_at.isoformat(),
                    payload,
                ),
            )

    def handoffs(self, *, workspace: str) -> tuple[SalesHandoff, ...]:
        with session(self._path) as conn:
            rows = conn.execute(
                "SELECT payload FROM outreach_handoff WHERE workspace = ? "
                "ORDER BY created_at, handoff_id",
                (workspace,),
            ).fetchall()
        return tuple(
            SalesHandoff.model_validate(
                _load(row["payload"], f"outreach_handoff/{workspace}")
            )
            for row in rows
        )


__all__ = ["SqliteOutreachStore"]

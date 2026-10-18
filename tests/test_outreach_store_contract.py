"""``OutreachStore`` 契约：**同一组判据，对着两个实现各跑一遍**。

## 为什么这个端口必须有两个实现

``ports/outreach.py`` 抽出端口时点名的理由是「记错了」与「发出去了」是两种相反的
失败。但端口本身不构成契约 —— 构成契约的是**同一组判据同时看着两个实现**。
本仓已有的先例是 ``tests/test_evidence_store_contract.py``（三个实现）与
``tests/test_scene_store_contract.py``（两个实现）：一个实现改了语义另一个不会红，
才叫「这是一个契约」。

两个实现：

* ``adapters/sqlite/outreach.py`` —— 主存。``begin_attempt`` 的判据由
  ``BEGIN IMMEDIATE`` 背书。
* ``adapters/memory.py`` —— 测试替身。**没有锁**。

## 有一条判据**刻意不在这里**

「写完重开一个 store 还能读到」只对持久化实现成立，内存实现按定义做不到。把它写
进来就只能给它开一个「内存除外」的例外 —— 而契约里出现第一个例外之后，第二个就
不会有人再争论了。与 ``test_scene_store_contract.py`` 同一条处置。

## 这里有三处判据是**「不得重复发」的执行器**

§6 把「不得重复发」写成了一句要求，而它在本仓的全部执行器就是下面这三条：

* ``test_an_attempt_that_may_have_gone_out_is_never_reopened`` ——
  ``DELIVERY_UNKNOWN`` 不得被重开。去掉它，「可能发出去了就先对账、不要重发」
  就退回成一段自觉，而重发的后果是**有人收到两封**。
* ``test_a_failed_attempt_is_a_second_attempt_not_a_second_row`` ——
  重试是同一个 ``send_key`` 的第 2 次，不是第二行。做成两行的实现会让
  「这一封发过几次」永远答成 1。
* ``test_suppression_is_a_query_not_a_cache`` —— 抑制是**查**出来的。
  一个读缓存的实现会让「他刚投诉完、我们又发了一封」在盘上看起来完全正常。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryOutreachStore
from marketops.adapters.sqlite.outreach import SqliteOutreachStore
from marketops.domain.errors import (
    IdempotencyConflictError,
    SceneConflictError,
    StoreError,
    WaveConflictError,
)
from marketops.domain.outreach import (
    ApprovedClaim,
    CorrespondenceDisposition,
    CorrespondenceEvent,
    CorrespondenceKind,
    DEFAULT_ATTEMPT_LEASE,
    DraftRef,
    DraftState,
    HandoffState,
    ManifestEntry,
    MessageState,
    OutreachCampaign,
    OutreachDraft,
    OutreachManifest,
    OutreachStage,
    OutboundAttempt,
    RecipientSuppression,
    SalesHandoff,
    SuppressionReason,
    TemplateFamily,
    Wave,
    WaveAuthorization,
    WaveState,
    WritingPolicy,
)
from marketops.domain.util import sha256_text

UTC = timezone.utc
#: 一个**非** UTC 的偏移量。用它构造的时刻在字符串排序与瞬间排序下顺序相反 ——
#: 「按发生时刻排序」那条判据要抓的就是这个。
PLUS_EIGHT = timezone(timedelta(hours=8))

T0 = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
WORKSPACE = "ws-natural-grass"
SCENE = "scene-uk-natural-grass"


def _digest(text: str) -> str:
    """一个合法的 64 位摘要。**从内容算**，不是一串固定的 a。

    写死一串 ``"a" * 64`` 也能过校验，但那样两处不同的内容会带同一个摘要，
    而下面好几条判据（幂等、CAS、清单一致性）判的就是摘要——用同一个常数
    会让它们全部「通过」，且通过的原因是判据没在判东西。
    """
    return sha256_text(text)


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path: Path):
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteOutreachStore(tmp_path / "state" / "marketops.db")
    return InMemoryOutreachStore()


# --- 构造器 -------------------------------------------------------------------


def make_family(family_id: str = "family-uk-grass-stage-1", **overrides) -> TemplateFamily:
    payload = {
        "family_id": family_id,
        "revision": 1,
        "stage": OutreachStage.STAGE_1,
        "role_class": "grounds_manager",
        "locale": "en-GB",
        "product": "natural-grass",
        "writing_policy_id": "policy-uk-grass-stage-1",
    }
    payload.update(overrides)
    return TemplateFamily(**payload)


def make_policy(policy_id: str = "policy-uk-grass-stage-1", **overrides) -> WritingPolicy:
    payload = {"policy_id": policy_id, "revision": 1}
    payload.update(overrides)
    return WritingPolicy(**payload)


def make_claim(claim_id: str = "claim-01-no-herbicide", **overrides) -> ApprovedClaim:
    payload = {
        "claim_id": claim_id,
        "revision": 1,
        "text": "本产品在批准用量下不需要在成坪期追加除草剂。",
        "source_ref": "label-rev-2026-04",
        "approved_by": "operator:member-a",
        "approved_at": T0,
    }
    payload.update(overrides)
    return ApprovedClaim(**payload)


def make_campaign(campaign_id: str = "campaign-uk-grass", **overrides) -> OutreachCampaign:
    payload = {
        "campaign_id": campaign_id,
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "name": "UK 天然草 首次接触",
        "stage": OutreachStage.STAGE_1,
        "family_id": "family-uk-grass-stage-1",
        "created_at": T0,
    }
    payload.update(overrides)
    return OutreachCampaign(**payload)


def make_draft(
    draft_id: str = "draft-0001",
    *,
    draft_version: int = 1,
    body: str = "您好，看到贵俱乐部的主场地是天然草。",
    wave_id: str = "wave-0001",
    **overrides,
) -> OutreachDraft:
    payload = {
        "draft_id": draft_id,
        "draft_version": draft_version,
        "supersedes": None if draft_version == 1 else draft_version - 1,
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "scene_version": 1,
        "campaign_id": "campaign-uk-grass",
        "wave_id": wave_id,
        "route_ref": "route-0001",
        "account_id": "account-0001",
        "family_id": "family-uk-grass-stage-1",
        "family_revision": 1,
        "writing_policy_id": "policy-uk-grass-stage-1",
        "writing_policy_revision": 1,
        "writer_id": "writer-fixture",
        "stage": OutreachStage.STAGE_1,
        "locale": "en-GB",
        "subject": "关于贵俱乐部天然草养护的一次简短交流",
        "body": body,
        "body_sha256": _digest(body),
        "subject_sha256": _digest("subject-0001"),
        "generation_key": _digest(f"gen/{draft_id}/{draft_version}"),
        "generation_receipt_ref": "receipt-0001",
        "created_at": T0,
    }
    payload.update(overrides)
    return OutreachDraft(**payload)


def make_entry(
    *,
    draft_id: str = "draft-0001",
    draft_version: int = 1,
    recipient: str = "grounds@example.co.uk",
    **overrides,
) -> ManifestEntry:
    payload = {
        "draft": DraftRef(
            draft_id=draft_id,
            draft_version=draft_version,
            body_sha256=_digest("body-0001"),
        ),
        "route_ref": "route-0001",
        "account_id": "account-0001",
        "recipient": recipient,
        "sendbox": "outreach@marketops.example",
        "stage": OutreachStage.STAGE_1,
        "family_id": "family-uk-grass-stage-1",
        "writing_policy_id": "policy-uk-grass-stage-1",
        "evidence_ids": ("evidence-0001",),
    }
    payload.update(overrides)
    return ManifestEntry(**payload)


def make_manifest(
    *,
    wave_id: str = "wave-0001",
    entries: tuple[ManifestEntry, ...] | None = None,
    **overrides,
) -> OutreachManifest:
    rows = entries if entries is not None else (make_entry(),)
    payload = {
        "wave_id": wave_id,
        "campaign_id": "campaign-uk-grass",
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "entries": rows,
        "window_start": T0,
        "window_end": T0 + timedelta(days=2),
        "allowed_count": len(rows),
        "valid_until": T0 + timedelta(days=7),
        "created_at": T0,
    }
    payload.update(overrides)
    return OutreachManifest(**payload)


def make_wave(
    *,
    wave_id: str = "wave-0001",
    revision: int = 0,
    state: WaveState = WaveState.PREPARED,
    manifest_sha256: str | None = None,
    **overrides,
) -> Wave:
    payload = {
        "wave_id": wave_id,
        "campaign_id": "campaign-uk-grass",
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "state": state,
        "manifest_sha256": manifest_sha256 or _digest("manifest-0001"),
        "revision": revision,
        "created_at": T0,
        "updated_at": T0,
    }
    payload.update(overrides)
    return Wave(**payload)


def make_authorization(
    *,
    authorization_id: str = "auth-0001",
    manifest: OutreachManifest | None = None,
    approved_count: int = 1,
    **overrides,
) -> WaveAuthorization:
    frozen = manifest if manifest is not None else make_manifest()
    payload = {
        "authorization_id": authorization_id,
        "wave_id": frozen.wave_id,
        "workspace": frozen.workspace,
        "scene_id": frozen.scene_id,
        "manifest_sha256": frozen.manifest_sha256,
        "authorized_by": "operator:member-a",
        "authority_ref": "approval-ticket-0001",
        "authorized_at": T0,
        "expires_at": T0 + timedelta(days=7),
        "approved_count": approved_count,
    }
    payload.update(overrides)
    return WaveAuthorization(**payload)


def make_attempt(
    *,
    send_key: str = "send-0001",
    state: MessageState = MessageState.NOT_SENT,
    started_at: datetime = T0,
    finished_at: datetime | None = None,
    **overrides,
):
    payload = {
        "send_key": send_key,
        "workspace": WORKSPACE,
        "wave_id": "wave-0001",
        "draft_id": "draft-0001",
        "draft_version": 1,
        "draft": DraftRef(
            draft_id="draft-0001", draft_version=1, body_sha256=_digest("body-0001")
        ),
        "route_ref": "route-0001",
        "account_id": "account-0001",
        "recipient": "grounds@example.co.uk",
        "sendbox": "outreach@marketops.example",
        "stage": OutreachStage.STAGE_1,
        "authorization_id": "auth-0001",
        "manifest_sha256": _digest("manifest-0001"),
        "state": state,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    payload.update(overrides)
    return OutboundAttempt(**payload)


def make_event(
    *,
    event_id: str = "event-0001",
    provider_message_id: str = "provider-msg-0001",
    kind: CorrespondenceKind = CorrespondenceKind.HUMAN_REPLY,
    disposition: CorrespondenceDisposition = CorrespondenceDisposition.HANDOFF,
    received_at: datetime = T0,
    **overrides,
):
    payload = {
        "event_id": event_id,
        "workspace": WORKSPACE,
        "sendbox": "outreach@marketops.example",
        "kind": kind,
        "provider_message_id": provider_message_id,
        "send_key": "send-0001",
        "sender": "grounds@example.co.uk",
        "subject": "Re: 关于贵俱乐部天然草养护的一次简短交流",
        "body": "有兴趣，方便下周通个电话吗？",
        "raw_ref": "raw/event-0001.eml",
        "raw_sha256": _digest("raw-0001"),
        "received_at": received_at,
        "ingested_at": received_at + timedelta(minutes=1),
        "disposition": disposition,
    }
    payload.update(overrides)
    return CorrespondenceEvent(**payload)


def make_handoff(*, handoff_id: str = "handoff-0001", **overrides) -> SalesHandoff:
    payload = {
        "handoff_id": handoff_id,
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "campaign_id": "campaign-uk-grass",
        "account_id": "account-0001",
        "route_ref": "route-0001",
        "event_id": "event-0001",
        "recipient": "grounds@example.co.uk",
        "reply_body": "有兴趣，方便下周通个电话吗？",
        "evidence_ids": ("evidence-0001",),
        "created_at": T0,
    }
    payload.update(overrides)
    return SalesHandoff(**payload)


# --- 配置三张表：同 id 不同内容是报错，不是覆盖 ---------------------------------


def test_config_is_idempotent_when_the_content_is_the_same(store) -> None:
    store.put_family(make_family())
    store.put_family(make_family())
    store.put_writing_policy(make_policy())
    store.put_writing_policy(make_policy())
    store.put_claim(make_claim())
    store.put_claim(make_claim())

    assert [f.family_id for f in store.families()] == ["family-uk-grass-stage-1"]
    assert [p.policy_id for p in store.writing_policies()] == ["policy-uk-grass-stage-1"]
    assert [c.claim_id for c in store.approved_claims()] == ["claim-01-no-herbicide"]


def test_config_is_refused_when_the_content_changes(store) -> None:
    """**不覆盖**。引用它的是 id —— 覆盖之后「这一稿按哪一版策略写的」仍然指着
    同一个 id，而那个 id 底下的东西已经换了。"""
    store.put_family(make_family())

    with pytest.raises(StoreError):
        store.put_family(make_family(role_class="head_greenkeeper"))


def test_config_is_listed_in_id_order(store) -> None:
    """顺序**必须**是确定的：报告按它读，而一个按插入顺序返回的实现会让
    「同一份库、两次跑出两份不同的报告」。"""
    store.put_family(make_family("family-b"))
    store.put_family(make_family("family-a"))

    assert [f.family_id for f in store.families()] == ["family-a", "family-b"]


def test_claims_are_not_filtered_by_expiry_in_the_store(store) -> None:
    """过期是**相对于某个时刻**的判定，所以它不属于存储层。

    一个在库里过滤的实现会让「这条说法现在还算不算数」这个问题的答案取决于
    查询发生在什么时候 —— 而调用方是带着它自己的时刻来问的。
    """
    store.put_claim(make_claim("claim-expired", expires_at=T0 - timedelta(days=1)))

    assert [c.claim_id for c in store.approved_claims()] == ["claim-expired"]
    expired = store.approved_claims()[0]
    assert expired.is_live_at(T0) is False
    assert expired.is_live_at(T0 - timedelta(days=2)) is True


# --- 活动、草稿、清单 ---------------------------------------------------------


def test_a_campaign_records_its_scene(store) -> None:
    store.put_campaign(make_campaign())

    got = store.campaign("campaign-uk-grass")
    assert got is not None
    assert got.scene_id == SCENE
    assert store.campaign("campaign-does-not-exist") is None


def test_a_campaign_may_not_move_to_another_scene(store) -> None:
    """一个活动**不跨 Scene** —— 跨 Scene 的活动就是两个活动。

    用的是 ``SceneConflictError`` 而不是 ``StoreError``：处置是「读出来重新决定」，
    不是「停机」—— 一个把这条判据抛成 ``StoreError`` 的实现会让一次普通的撞车在
    日志里长得像库坏了。
    """
    store.put_campaign(make_campaign())

    with pytest.raises(SceneConflictError):
        store.put_campaign(make_campaign(scene_id="scene-elsewhere"))


def test_a_draft_version_is_immutable(store) -> None:
    """同一版写第二次、内容不同 → 报错。

    覆盖会让一份绑在旧正文摘要上的授权**静默继续有效**，而盘上看起来只是
    「再生成了一次」。
    """
    store.put_draft(make_draft())
    store.put_draft(make_draft())

    with pytest.raises(StoreError):
        store.put_draft(make_draft(body="把上面那段换掉"))

    assert store.draft("draft-0001", 1) is not None
    assert store.draft("draft-0001", 2) is None
    assert store.draft("draft-0001", 99) is None


def test_the_next_version_supersedes_the_previous_one(store) -> None:
    store.put_draft(make_draft())
    store.put_draft(make_draft(draft_version=2, body="第二版正文"))

    second = store.draft("draft-0001", 2)
    assert second is not None
    assert second.supersedes == 1
    # 两版都在。**不是**就地改写 —— 「上一稿是什么」必须读得回来。
    assert store.draft("draft-0001", 1).body != second.body


def test_latest_drafts_returns_one_row_per_draft(store) -> None:
    """每一份草稿的**最新一版**，按 ``draft_id`` 升序。

    返回全部版本会让调用方以为这一批要发 2 封，而它只有 1 封。
    """
    store.put_draft(make_draft("draft-a"))
    store.put_draft(make_draft("draft-a", draft_version=2, body="第二版"))
    store.put_draft(make_draft("draft-b"))

    latest = store.latest_drafts(wave_id="wave-0001")
    assert [(d.draft_id, d.draft_version) for d in latest] == [("draft-a", 2), ("draft-b", 1)]


def test_latest_drafts_is_scoped_to_the_wave(store) -> None:
    store.put_draft(make_draft("draft-a", wave_id="wave-0001"))
    store.put_draft(make_draft("draft-b", wave_id="wave-0002"))

    assert [d.draft_id for d in store.latest_drafts(wave_id="wave-0002")] == ["draft-b"]


def test_a_manifest_is_frozen_per_wave(store) -> None:
    """同 wave_id 且内容相同 → 幂等；内容不同 → **报错**。

    换掉一份清单会让绑在旧摘要上的授权全部失去意义，而盘上只显示「清单更新了」。
    """
    manifest = make_manifest()
    store.put_manifest(manifest)
    store.put_manifest(manifest)

    other = make_manifest(entries=(make_entry(recipient="other@example.co.uk"),))
    with pytest.raises(StoreError):
        store.put_manifest(other)

    assert store.manifest("wave-0001").manifest_sha256 == manifest.manifest_sha256
    assert store.manifest("wave-absent") is None


def test_the_manifest_summary_moves_when_the_recipient_moves(store) -> None:
    """摘要算的是**内容**，编号算的是**位置**。

    这条判据是「授权绑在 wave_id 上」那个设计错在哪里的最小复现：改一个收件人，
    wave 没变、清单对象还是那一份，而摘要必须变。
    """
    first = make_manifest()
    second = make_manifest(entries=(make_entry(recipient="other@example.co.uk"),))

    assert first.manifest_sha256 != second.manifest_sha256


# --- 批次状态：乐观锁 ---------------------------------------------------------


def test_a_wave_round_trips_and_its_revision_advances(store) -> None:
    store.put_wave(make_wave(), expected_revision=None)
    store.put_manifest(make_manifest())

    first = store.wave("wave-0001")
    assert first is not None and first.revision == 0

    store.put_wave(
        first.moved_to(WaveState.PREPARED_PENDING_AUTHORIZATION, at=T1),
        expected_revision=0,
    )
    second = store.wave("wave-0001")
    assert second.state is WaveState.PREPARED_PENDING_AUTHORIZATION
    assert second.revision == 1


def test_creating_a_wave_that_already_exists_is_refused(store) -> None:
    """``expected_revision=None`` 的承诺是「这是我建的第一个」。

    一条「内容一样就算幂等」的判据会把「我以为这是第一次创建」与「这确实是
    第一次创建」变成同一件事 —— 而前者正是一次并发重复建批。
    """
    store.put_wave(make_wave(), expected_revision=None)

    with pytest.raises(IdempotencyConflictError):
        store.put_wave(make_wave(), expected_revision=None)


def test_a_stale_revision_writes_nothing(store) -> None:
    store.put_wave(make_wave(), expected_revision=None)
    store.put_manifest(make_manifest())
    current = store.wave("wave-0001")
    store.put_wave(
        current.moved_to(WaveState.PREPARED_PENDING_AUTHORIZATION, at=T1),
        expected_revision=0,
    )

    # 有人拿着第 0 版来写。它必须是**一个字都没落**的失败。
    with pytest.raises(WaveConflictError):
        store.put_wave(
            current.moved_to(WaveState.CANCELLED, at=T1 + timedelta(hours=1)),
            expected_revision=0,
        )
    assert store.wave("wave-0001").state is WaveState.PREPARED_PENDING_AUTHORIZATION


def test_a_wave_may_not_swap_its_manifest_underneath(store) -> None:
    """revision >= 2 之后，这个批次已经声明过一份清单了。

    带一份**别的**摘要去写，意味着有人在授权之后把清单换了 —— 而那些授权在
    记录上依然存在、依然「有效」，它们批的是另一批信。
    """
    store.put_wave(make_wave(), expected_revision=None)
    store.put_manifest(make_manifest())
    current = store.wave("wave-0001")
    store.put_wave(
        current.moved_to(WaveState.PREPARED_PENDING_AUTHORIZATION, at=T1),
        expected_revision=0,
    )

    with pytest.raises(StoreError):
        store.put_wave(
            make_wave(
                revision=2,
                state=WaveState.AUTHORIZED,
                manifest_sha256=_digest("a-completely-different-manifest"),
                updated_at=T1 + timedelta(minutes=1),
            ),
            expected_revision=1,
        )
    assert store.wave("wave-0001").revision == 1


def test_a_wave_beyond_the_first_state_must_have_a_manifest(store) -> None:
    """一份没有清单的批次不得进入授权与投递 —— 授权绑的就是清单摘要。"""
    store.put_wave(make_wave(), expected_revision=None)
    current = store.wave("wave-0001")
    store.put_wave(
        current.moved_to(WaveState.PREPARED_PENDING_AUTHORIZATION, at=T1),
        expected_revision=0,
    )

    with pytest.raises(StoreError):
        store.put_wave(
            make_wave(
                revision=2,
                state=WaveState.AUTHORIZED,
                updated_at=T1 + timedelta(minutes=1),
            ),
            expected_revision=1,
        )


def test_a_wave_that_does_not_exist_is_not_updated(store) -> None:
    with pytest.raises(WaveConflictError):
        store.put_wave(make_wave(revision=1), expected_revision=0)


# --- 授权 ---------------------------------------------------------------------


def test_an_authorization_is_appended_never_overwritten(store) -> None:
    """一次撤回是一条**新**记录，旧的那条留着。

    就地改写那条记录会让那次撤回在盘上从来没有发生过 —— 而「谁在什么时候撤回了
    什么」正是事后要查的第一件事。
    """
    manifest = make_manifest()
    first = make_authorization(manifest=manifest)
    store.put_authorization(first)
    store.put_authorization(first)

    revoked = make_authorization(
        authorization_id="auth-0002",
        manifest=manifest,
        revoked_at=T1,
        revoked_reason="业务方要求先核一遍话术",
    )
    store.put_authorization(revoked)

    rows = store.authorizations(wave_id="wave-0001")
    assert [a.authorization_id for a in rows] == ["auth-0001", "auth-0002"]
    assert rows[0].revoked_at is None
    assert rows[1].is_live_at(T1) is False


def test_an_authorization_record_is_immutable(store) -> None:
    store.put_authorization(make_authorization())

    with pytest.raises(StoreError):
        store.put_authorization(make_authorization(authorized_by="operator:someone-else"))


def test_authorizations_are_listed_per_wave(store) -> None:
    store.put_authorization(make_authorization())

    assert store.authorizations(wave_id="wave-absent") == ()


# --- 投递：不得重复发 ---------------------------------------------------------


def test_the_first_attempt_is_won_by_whoever_writes_it(store) -> None:
    outcome = store.begin_attempt(make_attempt(), at=T0)

    assert outcome.won is True
    assert outcome.state is MessageState.NOT_SENT
    assert outcome.attempt.attempt == 1


def test_an_attempt_that_may_have_gone_out_is_never_reopened(store) -> None:
    """``DELIVERY_UNKNOWN`` **不得**被重开。

    一次「连不上」与一次「超时了但对面可能收了」在这里长得一样，而它们的处置
    相反。重发一个可能已经出去的投递的后果是**有人收到两封** ——
    而它在这一层唯一会被挡住的地方就是这条判据。
    """
    store.begin_attempt(make_attempt(), at=T0)
    unknown = make_attempt(
        state=MessageState.DELIVERY_UNKNOWN,
        finished_at=T0 + timedelta(minutes=1),
        failure="连接在 DATA 之后断了，对端可能已收下",
    )
    store.finish_attempt(unknown)

    again = store.begin_attempt(make_attempt(started_at=T1), at=T1)
    assert again.won is False, "已经有别人开的了 —— 而这一封可能已经出去了"
    assert again.state is MessageState.DELIVERY_UNKNOWN
    assert again.attempt.attempt == 1, "不是第二次尝试，是同一个 send_key 的那一次"


@pytest.mark.parametrize(
    "terminal",
    [MessageState.ACCEPTED_BY_PROVIDER, MessageState.DELIVERED, MessageState.BOUNCED],
)
def test_any_settled_attempt_is_never_reopened(store, terminal) -> None:
    store.begin_attempt(make_attempt(), at=T0)
    store.finish_attempt(
        make_attempt(state=terminal, finished_at=T0 + timedelta(minutes=1))
    )

    again = store.begin_attempt(make_attempt(started_at=T1), at=T1)
    assert again.won is False
    assert again.state is terminal


def test_a_failed_attempt_is_a_second_attempt_not_a_second_row(store) -> None:
    """重试是**同一个 send_key 的第 2 次**。

    做成两行的实现会让「这一封发过几次」永远答成 1 —— 而那个数是
    「重试有没有失控」这个问题的唯一读数。
    """
    store.begin_attempt(make_attempt(), at=T0)
    store.finish_attempt(
        make_attempt(
            state=MessageState.FAILED,
            finished_at=T0 + timedelta(minutes=1),
            failure="对端明确拒绝：550 5.1.1",
        )
    )

    outcome = store.begin_attempt(make_attempt(started_at=T1), at=T1)
    assert outcome.won is True
    assert outcome.attempt.attempt == 2
    assert outcome.attempt.state is MessageState.NOT_SENT
    assert outcome.attempt.finished_at is None, "新一次尝试还没结束"
    assert outcome.attempt.failure == "", "上一次的失败理由不得留在这一次上"

    assert len(store.attempts(wave_id="wave-0001")) == 1, "一个 send_key 一行"


def test_an_open_attempt_is_handed_over_rather_than_reopened(store) -> None:
    """``NOT_SENT`` 还没定局 —— 但**这一封不是我的**。

    ``won=False`` 不是错误：要问道理就读 ``attempt.state``，它说的是
    「有人正在发」。
    """
    store.begin_attempt(make_attempt(), at=T0)

    second = store.begin_attempt(
        make_attempt(started_at=T1), at=T0 + DEFAULT_ATTEMPT_LEASE / 2
    )
    assert second.won is False
    assert second.state is MessageState.NOT_SENT


def test_an_attempt_nobody_settled_is_reopened_once_the_lease_runs_out(store) -> None:
    """一次**没有结局**的尝试在租约到期后必须能被接手。

    这条与上一条是一对，缺任何一条都有一半是错的：上一条挡「把还在发的那一封
    再发一遍」，这一条挡「一个被杀掉的驱动把这一封永久锁死」。两者在盘上是
    **同一行**（``NOT_SENT`` 加一个 ``started_at``），所以判别只能是时间。

    没有这一条，`DEFAULT_ATTEMPT_LEASE` 就是一段只写了没人读的散文 ——
    而它的失效方式恰好是「一切看起来都正常，只是这一封永远发不出去」。
    """
    store.begin_attempt(make_attempt(), at=T0)

    later = store.begin_attempt(make_attempt(started_at=T1), at=T0 + DEFAULT_ATTEMPT_LEASE)
    assert later.won is True
    assert later.attempt.attempt == 2, "接手是同一个 send_key 的第 2 次，不是新的一行"
    assert later.attempt.state is MessageState.NOT_SENT
    assert len(store.attempts(wave_id="wave-0001")) == 1


def test_a_settled_attempt_cannot_change_its_mind(store) -> None:
    """同一个逻辑发送的结局是一次性写下的。

    允许改写它，「这一封到底发出去了没有」就没有答案了。
    """
    store.begin_attempt(make_attempt(), at=T0)
    store.finish_attempt(
        make_attempt(state=MessageState.DELIVERED, finished_at=T0 + timedelta(minutes=1))
    )

    # 同一个状态再来一次是幂等的（重放）。
    store.finish_attempt(
        make_attempt(state=MessageState.DELIVERED, finished_at=T0 + timedelta(minutes=1))
    )

    with pytest.raises(StoreError):
        store.finish_attempt(
            make_attempt(
                state=MessageState.FAILED,
                finished_at=T0 + timedelta(minutes=2),
                failure="改口说没发出去",
            )
        )


def test_reconciliation_may_settle_an_unknown_answer_once(store) -> None:
    """``DELIVERY_UNKNOWN`` **可以**被改判一次 —— 否则对账这一整条路写不进任何东西。

    这一条与上面那条「一次性写下」不是互相矛盾的：``DELIVERY_UNKNOWN`` 本来
    就不是一个答案（传输抛了、对面可能收了）。拒绝一切改判的实现会让这一格永远
    出不去：不许重发（§6），也不许结案 —— 报告上就是一个永远挂着的「可能」。

    判据在 ``domain.outreach.may_be_settled_by_reconciliation``，两个适配器都
    必须用它。
    """
    store.begin_attempt(make_attempt(), at=T0)
    store.finish_attempt(
        make_attempt(
            state=MessageState.DELIVERY_UNKNOWN,
            finished_at=T0 + timedelta(minutes=1),
            provider_message_id="<mid-1@x>",
            failure="连接在 DATA 之后断了，对端可能已收下",
        )
    )

    store.finish_attempt(
        make_attempt(
            state=MessageState.ACCEPTED_BY_PROVIDER,
            finished_at=T0 + timedelta(minutes=2),
            provider_message_id="<mid-1@x>",
        )
    )
    settled = store.attempts(wave_id="wave-0001")[0]
    assert settled.state is MessageState.ACCEPTED_BY_PROVIDER
    assert settled.provider_message_id == "<mid-1@x>"

    # 改判只有一次：写定之后又是不可改的。
    with pytest.raises(StoreError):
        store.finish_attempt(
            make_attempt(
                state=MessageState.FAILED,
                finished_at=T0 + timedelta(minutes=3),
                failure="第二次对账改口说没发出去",
            )
        )
    # 改判不能从别的终态出发 —— 这一半与那一次例外是一对。
    other = make_attempt(send_key="send-0002")
    store.begin_attempt(other, at=T0)
    store.finish_attempt(
        other.model_copy(update={"state": MessageState.DELIVERED, "finished_at": T1})
    )
    with pytest.raises(StoreError):
        store.finish_attempt(
            other.model_copy(update={"state": MessageState.FAILED, "failure": "从送达改口成拒收"})
        )


def test_settling_an_attempt_that_never_started_is_refused(store) -> None:
    """结算一条**没开始过**的投递，意味着有人凭空写下了一次发送。"""
    with pytest.raises(StoreError):
        store.finish_attempt(
            make_attempt(
                state=MessageState.DELIVERED, finished_at=T0 + timedelta(minutes=1)
            )
        )


def test_settling_the_wrong_attempt_number_is_refused(store) -> None:
    """写错行会让「第 1 次失败、第 2 次成功」被记成「第 1 次成功」。

    而那一行会告诉下一个驱动这一封已经发过了。
    """
    store.begin_attempt(make_attempt(), at=T0)
    store.finish_attempt(
        make_attempt(
            state=MessageState.FAILED,
            finished_at=T0 + timedelta(minutes=1),
            failure="超时",
        )
    )
    store.begin_attempt(make_attempt(started_at=T1), at=T1)

    with pytest.raises(StoreError):
        store.finish_attempt(
            make_attempt(
                attempt=1,
                state=MessageState.DELIVERED,
                finished_at=T1 + timedelta(minutes=1),
            )
        )


def test_attempts_are_scoped_to_the_wave_and_sorted(store) -> None:
    store.begin_attempt(
        make_attempt(send_key="send-b", recipient="b@example.co.uk"), at=T0
    )
    store.begin_attempt(
        make_attempt(send_key="send-a", recipient="a@example.co.uk"), at=T0
    )

    assert [a.recipient for a in store.attempts(wave_id="wave-0001")] == [
        "a@example.co.uk",
        "b@example.co.uk",
    ]
    assert store.attempts(wave_id="wave-absent") == ()
    assert store.attempt("send-absent") is None


def test_history_is_read_across_waves(store) -> None:
    """§6 逐字要求每一次投递前重查**跨批次**的发送历史。

    一个只查本批次历史的实现，会让同一个人在两个活动里各收到一封 ——
    而两批的记录各自完全正确。
    """
    store.begin_attempt(make_attempt(send_key="send-w1", wave_id="wave-0001"), at=T0)
    store.begin_attempt(make_attempt(send_key="send-w2", wave_id="wave-0002"), at=T0)

    history = store.attempts_by_recipient("grounds@example.co.uk")
    assert {a.wave_id for a in history} == {"wave-0001", "wave-0002"}
    assert store.attempts_by_recipient("nobody@example.co.uk") == ()


def test_a_recipient_may_be_read_in_either_spelling(store) -> None:
    """收件人在清单里已经是规范化形式，而查询侧不能要求调用方先规范化一遍 ——
    一个区分大小写的查询会让「同一个人」在两个活动里查不到彼此的记录。"""
    store.begin_attempt(make_attempt(recipient="grounds@example.co.uk"), at=T0)

    assert len(store.attempts_by_recipient("grounds@Example.CO.UK")) == 1


# --- 抑制 ---------------------------------------------------------------------


def test_suppression_is_a_query_not_a_cache(store) -> None:
    """抑制会被**另一个进程**改（投诉进来那一刻），而查询必须看得见它。

    一个读缓存的实现会让「他刚投诉完、我们又发了一封」在盘上看起来完全正常。
    """
    assert store.is_suppressed("grounds@example.co.uk", at=T0) is False

    store.put_suppression(
        RecipientSuppression(
            recipient="grounds@example.co.uk",
            reason=SuppressionReason.COMPLAINT,
            source_ref="event-0002",
            suppressed_at=T0,
        )
    )
    assert store.is_suppressed("grounds@example.co.uk", at=T0) is True
    # 域名那一段不区分大小写（``normalise_email`` 只做这一件不改变语义的事）。
    assert store.is_suppressed("grounds@Example.CO.UK", at=T0) is True
    # 而**本地部分故意不折叠**：它在 RFC 5321 下是大小写敏感的，随意 lower()
    # 会把两个不同的人合成一个 —— ``normalise_email`` 的 docstring 说明了这条。
    assert store.is_suppressed("Grounds@example.co.uk", at=T0) is False
    assert store.is_suppressed("other@example.co.uk", at=T0) is False


def test_a_released_suppression_stops_blocking(store) -> None:
    """按 ``at`` 判而不是按「有没有记录」判。

    一条已经被解除的抑制不该继续挡住发送 —— 否则「解除」这个动作没有任何效果，
    而它看起来完全生效了。
    """
    store.put_suppression(
        RecipientSuppression(
            recipient="grounds@example.co.uk",
            reason=SuppressionReason.UNSUBSCRIBE,
            source_ref="event-0002",
            suppressed_at=T0,
            released_at=T0 + timedelta(days=1),
        )
    )

    assert store.is_suppressed("grounds@example.co.uk", at=T0) is True
    assert store.is_suppressed("grounds@example.co.uk", at=T0 + timedelta(days=2)) is False


def test_the_same_reason_twice_is_two_facts(store) -> None:
    """一条解除之后再收到同一理由的投诉是**两条**事实。

    就地改写会让「这个人被解除过抑制吗」永远答不上来 —— 而那正是下一次事故里
    要查的第一件事。
    """
    store.put_suppression(
        RecipientSuppression(
            recipient="grounds@example.co.uk",
            reason=SuppressionReason.COMPLAINT,
            source_ref="event-0002",
            suppressed_at=T0,
            released_at=T0 + timedelta(days=1),
        )
    )

    with pytest.raises(StoreError):
        store.put_suppression(
            RecipientSuppression(
                recipient="grounds@example.co.uk",
                reason=SuppressionReason.COMPLAINT,
                source_ref="event-0009",
                suppressed_at=T0 + timedelta(days=2),
            )
        )

    assert len(store.suppressions("grounds@example.co.uk")) == 1


def test_a_suppression_records_that_it_was_released(store) -> None:
    store.put_suppression(
        RecipientSuppression(
            recipient="grounds@example.co.uk",
            reason=SuppressionReason.OPERATOR,
            source_ref="ticket-0001",
            suppressed_at=T0,
            released_at=T1,
            note="业务方确认这是一次误加",
        )
    )

    row = store.suppressions("grounds@example.co.uk")[0]
    assert row.released_at == T1
    assert row.note == "业务方确认这是一次误加"
    # **不删行** —— 这一行还在，只是不再挡。
    assert store.suppressions("other@example.co.uk") == ()


# --- 事件：去重靠 (sendbox, provider_message_id) -------------------------------


def test_a_replayed_event_is_not_a_second_event(store) -> None:
    """重放（webhook 重试、游标回退）落到同一个键上。

    于是「重放」与「两条一样的事件」分得开 —— 而后者是必须被人看见的。
    """
    assert store.put_event(make_event()) is True
    assert store.put_event(make_event()) is False

    assert len(store.events(workspace=WORKSPACE)) == 1


def test_the_dedupe_key_does_not_include_the_subject(store) -> None:
    """主题是**对方**可以随手改的东西。

    一条改了主题的回信若被去重掉，读者看不到它 —— 而它可能正是一封
    「请不要再联系我」。
    """
    store.put_event(make_event())

    with pytest.raises(StoreError):
        store.put_event(make_event(subject="Re: 换个话题"))


def test_events_are_ordered_by_when_they_happened(store) -> None:
    """乱序到达是常态。

    一份按**落库顺序**排的事件表会让「谁先说的」在报告上变成「谁先被我们读到的」。
    """
    store.put_event(
        make_event(
            event_id="event-late",
            provider_message_id="provider-late",
            received_at=T0 + timedelta(hours=5),
        )
    )
    store.put_event(
        make_event(
            event_id="event-early",
            provider_message_id="provider-early",
            received_at=T0,
        )
    )

    assert [e.event_id for e in store.events(workspace=WORKSPACE)] == [
        "event-early",
        "event-late",
    ]


def test_events_are_scoped_to_the_workspace(store) -> None:
    store.put_event(make_event())

    assert store.events(workspace="ws-elsewhere") == ()
    assert store.event("event-0001") is not None
    assert store.event("event-absent") is None


# --- 交接：覆盖写 -------------------------------------------------------------


def test_a_handoff_advances_in_place(store) -> None:
    """与抑制**相反**：交接的状态推进是「这件事正在走」，读的人要的是当前值。

    做成只增不改的实现会让「这条交接现在到哪一步了」需要调用方自己去挑
    最近一条 —— 而挑错了不会报错。
    """
    store.put_handoff(make_handoff())
    assert store.handoffs(workspace=WORKSPACE)[0].state is HandoffState.PENDING_LOCAL

    store.put_handoff(
        make_handoff(
            state=HandoffState.NOTIFIED,
            external_row_id="sheet-row-42",
            external_version="7",
        )
    )

    rows = store.handoffs(workspace=WORKSPACE)
    assert len(rows) == 1, "同一条交接是一条记录，不是一个历史"
    assert rows[0].state is HandoffState.NOTIFIED
    assert rows[0].external_row_id == "sheet-row-42"


def test_handoffs_are_scoped_to_the_workspace(store) -> None:
    store.put_handoff(make_handoff())

    assert store.handoffs(workspace="ws-elsewhere") == ()


# --- 时刻：规范化必须在存储层之前发生 ------------------------------------------


def test_an_event_keeps_its_received_instant_across_offsets(store) -> None:
    """同一个瞬间的两种写法落到**同一个** ``received_at``。

    ``to_utc`` 把每个时刻字段规范到 UTC（``domain/util.py``）。没有它，落库的
    字符串会带着调用方给的偏移量，而 ISO-8601 字符串的大小顺序只在偏移量相同时
    才等于瞬间的先后 —— ``2026-09-23T09:00:00+08:00`` 与
    ``2026-09-23T02:00:00+00:00`` 是同一个瞬间，前者按字符串排却在**后面**。
    于是「取最近一条回信」会静默取到一封更早的，而没有任何一处会红。
    """
    same_instant = T0.astimezone(PLUS_EIGHT)
    assert same_instant.isoformat() != T0.isoformat(), "两者是不同的字符串"
    assert same_instant == T0, "而是同一个瞬间"

    store.put_event(make_event(received_at=same_instant))

    row = store.events(workspace=WORKSPACE)[0]
    assert row.received_at == T0
    assert row.received_at.isoformat() == T0.isoformat(), "落库的是规范形式，不是调用方给的那串"
    assert row.ingested_at.isoformat().endswith("+00:00")


def test_an_attempt_keeps_its_started_instant_across_offsets(store) -> None:
    opened = store.begin_attempt(
        make_attempt(started_at=T0.astimezone(PLUS_EIGHT)),
        at=T0.astimezone(PLUS_EIGHT),
    )
    assert opened.attempt.started_at == T0
    assert store.attempt("send-0001").started_at == T0


# --- 草稿的状态是列还是 payload：两条都要对得上 --------------------------------


def test_a_draft_that_is_held_keeps_its_state(store) -> None:
    """``HELD`` 是一条**正常**的结果，不是失败。

    §5 要求个性化缺失时用已批准的通稿措辞或停下 —— 停下的那一份必须留痕，
    否则「有多少封因为缺个性化没发出去」永远答不上来。
    """
    store.put_draft(make_draft(state=DraftState.HELD))

    assert store.draft("draft-0001", 1).state is DraftState.HELD

"""``ClaimWithdrawalStore`` 契约：**同一组判据，两个实现各跑一遍**。

两个实现：

* ``adapters/sqlite/claim_withdrawal.py`` —— 主存。它与 ``SqliteOutreachStore``
  指着**同一个库文件**（同一批 ``outreach_*`` 表），而「一个事务」由
  ``BEGIN IMMEDIATE`` 背书。
* ``adapters/memory.py::InMemoryClaimWithdrawalStore`` —— 测试替身。它不自己存
  东西，读与写都转手给同一个 ``InMemoryOutreachStore``；「一个事务」在那里的形状
  是「写进副本、成功才换上去、``StoreError`` 就还原」，而**没有锁**。

## 这里装着承重点 3 的三半

冻结表把 ``withdraw_claim`` 写成一句话，三个从句各有各的执行器：

1. 「受影响**未发**的草稿 → hold」 —— ``test_the_citing_drafts_are_the_ones_with_a_reference``
   与 ``test_attempts_are_the_raw_rows_the_rule_needs``。前者钉的是「按结构化引用
   算，不按关键词」，后者钉的是「这一层交出去的是原始尝试行」——
   「发出去过没有」的那条规则（``SENT_OR_UNKNOWN`` 四元集合）住在
   ``domain/outreach.py``，在这里再写一遍就是同一句话的第二份实现。
2. 「无关任务不变」 —— ``test_a_draft_that_does_not_cite_it_is_not_written``。
3. 「**一个事务**、全成或全不成」 —— ``test_a_failed_half_leaves_no_half_changed_state``。
   它刻意构造一次**在事务中途**炸掉的写（第二份草稿那一格已经有一份内容不同的
   版本），然后断言**两半都没落盘**：那条说法还是原来那一版。少了它，「原子」
   只是一个形容词 —— 而半改的状态正是这条工具最贵的那一格。

## 有一条判据刻意是「没有发生」

``drafts_citing`` 回的是**最新一版**，所以「一份草稿的第 2 版不再引用这条说法」
时它不该出现在受影响的集合里。写这一条是因为它有两种错法：按历史版本取（于是
一份已经改过、不再引用它的草稿被误伤），或者按第一版取（于是那一份「改掉了引用」
的操作被静默忽略）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryClaimWithdrawalStore, InMemoryOutreachStore
from marketops.adapters.sqlite.claim_withdrawal import SqliteClaimWithdrawalStore
from marketops.adapters.sqlite.outreach import SqliteOutreachStore
from marketops.domain.errors import StoreError
from marketops.domain.outreach import (
    SENT_OR_UNKNOWN,
    ApprovedClaim,
    DraftRef,
    DraftState,
    MessageState,
    OutreachDraft,
    OutreachStage,
    OutboundAttempt,
)
from marketops.domain.util import sha256_text

UTC = timezone.utc
T0 = datetime(2026, 9, 29, 1, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
WORKSPACE = "ws-natural-grass"
OTHER_WORKSPACE = "ws-rye-blend"
SCENE = "scene-uk-natural-grass"
CLAIM = "claim-01-no-herbicide"
OTHER_CLAIM = "claim-02-seed-rate"


def _digest(text: str) -> str:
    """合法摘要，**从内容算**（一个常数会让「两处内容不同」的判据恒真）。"""
    return sha256_text(text)


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path: Path):
    """``(外联存储, 撤回存储)`` —— 两个实现之一，两个句柄指着**同一批表**。

    名字进测试 id：红了的时候一眼看得见是哪一个实现。两份各存一份的实现会让
    ``drafts_citing`` 在一个库里筛、撤回写进另一个库，而两边都跑得过 ——
    这个夹具的形状就是为挡它而写的。
    """
    if request.param == "sqlite":
        db = tmp_path / "state" / "marketops.db"
        return SqliteOutreachStore(db), SqliteClaimWithdrawalStore(db)
    outreach = InMemoryOutreachStore()
    return outreach, InMemoryClaimWithdrawalStore(outreach)


# --- 构造器 -------------------------------------------------------------------


def make_claim(claim_id: str = CLAIM, **overrides) -> ApprovedClaim:
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


def make_draft(
    draft_id: str = "draft-0001",
    *,
    draft_version: int = 1,
    body: str = "您好，看到贵俱乐部的主场地是天然草。",
    claim_ids: tuple[str, ...] = (CLAIM,),
    state: DraftState = DraftState.GENERATED,
    workspace: str = WORKSPACE,
    **overrides,
) -> OutreachDraft:
    payload = {
        "draft_id": draft_id,
        "draft_version": draft_version,
        "supersedes": None if draft_version == 1 else draft_version - 1,
        "workspace": workspace,
        "scene_id": SCENE,
        "scene_version": 1,
        "campaign_id": "campaign-uk-grass",
        "wave_id": "wave-0001",
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
        "claim_ids": claim_ids,
        "state": state,
        "created_at": T0,
    }
    payload.update(overrides)
    return OutreachDraft(**payload)


def make_attempt(
    *,
    send_key: str = "send-0001",
    draft_id: str = "draft-0001",
    draft_version: int = 1,
    state: MessageState = MessageState.DELIVERED,
    started_at: datetime = T0,
    finished_at: datetime | None = None,
    failure: str = "",
    workspace: str = WORKSPACE,
    **overrides,
) -> OutboundAttempt:
    payload = {
        "send_key": send_key,
        "workspace": workspace,
        "wave_id": "wave-0001",
        "draft_id": draft_id,
        "draft_version": draft_version,
        "draft": DraftRef(
            draft_id=draft_id,
            draft_version=draft_version,
            body_sha256=_digest(f"body/{draft_id}/{draft_version}"),
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
        # 终态必须有完成时刻（``OutboundAttempt`` 的校验器），而 ``NOT_SENT``
        # 是「开出去了、还没结算」，它没有完成时刻 —— 两格说的是同一件事的两面。
        "finished_at": finished_at
        if finished_at is not None
        else (None if state is MessageState.NOT_SENT else started_at + timedelta(minutes=1)),
        # ``FAILED`` / ``BOUNCED`` / ``DELIVERY_UNKNOWN`` 各自要写明原因 ——
        # 「可能发出去了」与「确定没出去」的处置相反（先对账 / 可以重发），
        # 而缺了原因它们就只剩一个状态码。
        "failure": failure,
    }
    payload.update(overrides)
    return OutboundAttempt(**payload)


def retire(claim: ApprovedClaim, *, at: datetime = T1) -> ApprovedClaim:
    """撤回那一刻写下去的那一版：同一条说法，``expires_at`` 落在撤回时刻。"""
    return claim.model_copy(update={"revision": claim.revision + 1, "expires_at": at})


def hold(draft: OutreachDraft) -> OutreachDraft:
    return draft.model_copy(
        update={
            "draft_version": draft.draft_version + 1,
            "supersedes": draft.draft_version,
            "state": DraftState.HELD,
        }
    )


# --- drafts_citing：按引用算，不按关键词 ---------------------------------------


def test_the_citing_drafts_are_the_ones_with_a_reference(store):
    """**受影响集合由证据算出来** —— 判据是 ``OutreachDraft.claim_ids``。

    第二份草稿的**正文里逐字写着**这句话，而它没有引用它；第三份引用的是另一条。
    关键词匹配会让第二份被误伤、让一份引用了而没提到字面的被漏掉 ——
    而两者在报告上都表现为「撤干净了」。
    """
    outreach, withdrawals = store
    outreach.put_claim(make_claim())
    outreach.put_claim(make_claim(OTHER_CLAIM, text="另一个说法"))
    cited = make_draft("draft-0001", claim_ids=(CLAIM,))
    quoted_only = make_draft(
        "draft-0002",
        body="本产品在批准用量下不需要在成坪期追加除草剂。",
        claim_ids=(),
    )
    other = make_draft("draft-0003", claim_ids=(OTHER_CLAIM,))
    for draft in (cited, quoted_only, other):
        outreach.put_draft(draft)

    found = withdrawals.drafts_citing(workspace=WORKSPACE, claim_id=CLAIM)

    assert [draft.draft_id for draft in found] == ["draft-0001"]
    assert found[0] == cited


def test_the_citing_drafts_are_the_latest_version_of_each(store):
    """每份取**最新一版**：第 2 版把引用去掉了，它就不再受影响。"""
    outreach, withdrawals = store
    outreach.put_claim(make_claim())
    outreach.put_draft(make_draft("draft-0001", draft_version=1, claim_ids=(CLAIM,)))
    outreach.put_draft(make_draft("draft-0001", draft_version=2, claim_ids=()))
    outreach.put_draft(make_draft("draft-0002", draft_version=1, claim_ids=(CLAIM,)))
    outreach.put_draft(make_draft("draft-0002", draft_version=2, claim_ids=(CLAIM,)))

    found = withdrawals.drafts_citing(workspace=WORKSPACE, claim_id=CLAIM)

    assert [(draft.draft_id, draft.draft_version) for draft in found] == [("draft-0002", 2)]


def test_the_citing_drafts_are_scoped_to_the_workspace(store):
    """按工作区枚举 —— 一个省略它的实现在另一个工作区的草稿上也会动手。"""
    outreach, withdrawals = store
    outreach.put_claim(make_claim())
    outreach.put_draft(make_draft("draft-0001"))
    outreach.put_draft(make_draft("draft-0002", workspace=OTHER_WORKSPACE))

    mine = withdrawals.drafts_citing(workspace=WORKSPACE, claim_id=CLAIM)
    assert [draft.draft_id for draft in mine] == ["draft-0001"]
    theirs = withdrawals.drafts_citing(workspace=OTHER_WORKSPACE, claim_id=CLAIM)
    assert [draft.draft_id for draft in theirs] == ["draft-0002"]
    assert withdrawals.drafts_citing(workspace="ws-nobody", claim_id=CLAIM) == ()


def test_a_claim_nobody_cites_yields_nothing(store):
    """空集合是一个**正常结果**（这条说法没有任何草稿引用它），不是一个错误。"""
    outreach, withdrawals = store
    outreach.put_claim(make_claim())
    outreach.put_draft(make_draft("draft-0001", claim_ids=(OTHER_CLAIM,)))
    assert withdrawals.drafts_citing(workspace=WORKSPACE, claim_id=CLAIM) == ()


# --- attempts：交出去的是原始行，不是「发过没有」那个布尔 -----------------------


def test_attempts_are_the_raw_rows_the_rule_needs(store):
    """**这一条钉的是分层**：端口交原始尝试行，判「发出去过没有」的规则在域层。

    所以这里三种状态都要读得回来 —— 包括 ``DELIVERY_UNKNOWN``（它在
    ``SENT_OR_UNKNOWN`` 里）与 ``FAILED``（**不在**里）。一个在存储这一层就把
    「发过没有」判完的实现，会把那一格压成一个布尔，而布尔里读不出来
    「可能已经出去了」与「明确没出去」的区别 —— 两者的处置相反。
    """
    outreach, withdrawals = store
    for index, state in enumerate(
        (MessageState.DELIVERED, MessageState.DELIVERY_UNKNOWN, MessageState.FAILED), start=1
    ):
        outreach.put_claim(make_claim())
        outreach.put_draft(make_draft(f"draft-000{index}"))
        outreach.begin_attempt(
            make_attempt(
                send_key=f"send-000{index}",
                draft_id=f"draft-000{index}",
                state=state,
                started_at=T0 + timedelta(minutes=index),
                failure="" if state is MessageState.DELIVERED else "投递链给的那句话",
            ),
            at=T0,
        )

    rows = withdrawals.attempts(workspace=WORKSPACE)

    assert [row.send_key for row in rows] == ["send-0001", "send-0002", "send-0003"], (
        "按 (started_at, send_key) 排序 —— 两个实现给出两种顺序的那天，"
        "同一份数据会说两句不同的话"
    )
    assert {row.state for row in rows} == {
        MessageState.DELIVERED,
        MessageState.DELIVERY_UNKNOWN,
        MessageState.FAILED,
    }
    assert MessageState.DELIVERY_UNKNOWN in SENT_OR_UNKNOWN
    assert MessageState.FAILED not in SENT_OR_UNKNOWN


def test_attempts_are_scoped_to_the_workspace(store):
    outreach, withdrawals = store
    outreach.put_claim(make_claim())
    outreach.put_draft(make_draft("draft-0001"))
    outreach.put_draft(make_draft("draft-0002", workspace=OTHER_WORKSPACE))
    outreach.begin_attempt(make_attempt(send_key="send-mine"), at=T0)
    outreach.begin_attempt(
        make_attempt(
            send_key="send-theirs",
            draft_id="draft-0002",
            workspace=OTHER_WORKSPACE,
        ),
        at=T0,
    )

    assert [row.send_key for row in withdrawals.attempts(workspace=WORKSPACE)] == ["send-mine"]
    assert [
        row.send_key for row in withdrawals.attempts(workspace=OTHER_WORKSPACE)
    ] == ["send-theirs"]


# --- withdraw：两半在一个事务里 -------------------------------------------------


def test_withdraw_writes_both_halves(store):
    """撤回的形态是**追加一版**：说法退场、草稿进 hold。"""
    outreach, withdrawals = store
    claim = make_claim()
    outreach.put_claim(claim)
    draft = make_draft("draft-0001")
    outreach.put_draft(draft)

    retirement = retire(claim)
    retired = hold(draft)
    withdrawals.withdraw(retirement=retirement, drafts=(retired,))

    # 说法：新一版可读，而 `approved_claims()` 只回最新的那一版 —— 撤回之后它不再
    # 算数，是因为消费者按 `is_live_at(moment)` 筛，而它的 `expires_at` 是撤回时刻。
    latest = outreach.approved_claims()
    assert [(row.claim_id, row.revision) for row in latest] == [(CLAIM, 2)]
    assert latest[0].expires_at == T1
    assert latest[0].is_live_at(T1 - timedelta(seconds=1)) is True
    # **到期当天算活着**（``is_live_at`` 的 docstring 逐字：「``expires_at`` 是
    # 『到这一刻为止』，不是『这一刻起失效』」）—— 所以边界是**之后**才翻面。
    # 这条判据是那个选择的执行器：一个写成 ``at < expires_at`` 的实现会在这里红。
    assert latest[0].is_live_at(T1) is True
    assert latest[0].is_live_at(T1 + timedelta(seconds=1)) is False
    # 旧那一版仍然在盘上（追加，不是改写）—— 「当时批准的那句话」读得回来。
    assert outreach.approved_claims()[0].text == claim.text

    # 草稿：新一版 state=held，旧那一版仍在。
    newest = outreach.draft("draft-0001", 2)
    assert newest is not None
    assert newest.state is DraftState.HELD
    assert newest.supersedes == 1
    assert outreach.draft("draft-0001", 1) == draft


def test_the_retirement_revision_carries_the_mark_that_says_it_retired(store):
    """撤回写下去的那一版带着**它自己的**判别位：读回来还在，而且有人在读它。

    这一条是变异量出来的缺口：把 ``is_live_at`` 里判 ``retired`` 那两行删掉，
    今天**没有任何判据会红** —— 因为 ``withdraw_claim`` 必然同时把 ``expires_at``
    落在撤回那一刻，于是一个只判期限的实现全都绿。

    三行一起判，因为一个「写进去但没人读」的实现会让前两行照样绿 —— 而它在
    **行为上**与今天（根本没有这个位）一模一样。末一行才是那个位的读者。
    """
    outreach, withdrawals = store
    claim = make_claim()
    outreach.put_claim(claim)

    retirement = claim.model_copy(
        update={"revision": claim.revision + 1, "expires_at": T1, "retired": True}
    )
    withdrawals.withdraw(retirement=retirement, drafts=())

    latest = outreach.approved_claims()
    assert [(row.claim_id, row.revision) for row in latest] == [(CLAIM, 2)]
    assert latest[0].retired is True, "盘上那一版没有那个位"
    # 期限那一格仍写着撤回时刻 —— 两个位各说各的，不是二选一。
    assert latest[0].expires_at == T1
    # 按期限算，它在撤回那一刻**之前**是活的（``T1 - 1s <= T1``）；它答不活，
    # 就是那个位在作数，而不是期限。
    assert latest[0].is_live_at(T1 - timedelta(seconds=1)) is False, (
        "那个位在盘上，却没有读者 —— 判活的仍然只看期限"
    )


def test_a_claim_row_written_before_the_mark_existed_reads_as_not_retired(tmp_path: Path):
    """盘上那条**写在那个位之前**的行读出来是「未退场」，不是「退场」。

    ``ApprovedClaim`` 的「空 = 旧读数」：这一族新加的字段，缺省值就是那条老行的
    意思。缺省若翻成 ``True``，盘上**所有**老说法会在一夜之间静默退场 —— 而报告
    上只会显示「没有可用的说法」，看不出是有人改了缺省。

    造的是**真的一条旧行**（payload 里没有那个键），不是 ``model_validate({...})``
    一次：读路径上还有 ``_load`` 那一层，而「缺省对」与「读得回来」是两件事。
    """
    db = tmp_path / "state" / "marketops.db"
    SqliteOutreachStore(db)  # 只为建表，句柄不留
    payload = make_claim().model_dump(mode="json")
    del payload["retired"]  # 那个键存在之前写下的那些行就是这个形状
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO outreach_claim (claim_id, revision, payload) VALUES (?, ?, ?)",
            (CLAIM, 1, json.dumps(payload)),
        )

    rows = SqliteOutreachStore(db).approved_claims()
    assert [(row.claim_id, row.revision) for row in rows] == [(CLAIM, 1)]
    assert rows[0].retired is False, "一条旧行被读成了已退场"
    # 而它照旧算活着：期限那一格（这一条没写）没被那个位连累。
    assert rows[0].is_live_at(T0) is True


def test_withdraw_of_nothing_affected_still_writes_the_retirement(store):
    """没有受影响的草稿是一个结果，不是一次空调用：这条说法**照样**退场。"""
    outreach, withdrawals = store
    claim = make_claim()
    outreach.put_claim(claim)

    withdrawals.withdraw(retirement=retire(claim), drafts=())

    assert [row.revision for row in outreach.approved_claims()] == [2]


def test_withdraw_twice_is_not_two_withdrawals(store):
    """重发同一次撤回是幂等的（两半都是 ``(id, version)`` 上的追加）。"""
    outreach, withdrawals = store
    claim = make_claim()
    outreach.put_claim(claim)
    draft = make_draft("draft-0001")
    outreach.put_draft(draft)

    retirement, retired = retire(claim), hold(draft)
    withdrawals.withdraw(retirement=retirement, drafts=(retired,))
    withdrawals.withdraw(retirement=retirement, drafts=(retired,))

    assert [row.revision for row in outreach.approved_claims()] == [2]
    assert [draft.draft_version for draft in (outreach.draft("draft-0001", 2),)] == [2]


def test_a_failed_half_leaves_no_half_changed_state(store):
    """**承重点 3 的执行器：全成或全不成。**

    构造一次会在**事务中途**炸掉的写：第二份草稿那一格
    （``(draft_id, draft_version)``）已经有一份**内容不同**的版本，而
    ``put_draft`` 在那种情况下报 ``StoreError``。写下去的次序是「先说法、后草稿」，
    所以炸点在**第一半已经写完**之后 —— 一次没有事务的实现会留下
    「说法已经撤了、草稿还压着」的状态，而盘上看起来完全正常。
    """
    outreach, withdrawals = store
    claim = make_claim()
    outreach.put_claim(claim)
    first = make_draft("draft-0001")
    second = make_draft("draft-0002")
    outreach.put_draft(first)
    outreach.put_draft(second)
    # 抢占第二份草稿要写的那一格，而内容**不是**同一份。
    outreach.put_draft(make_draft("draft-0002", draft_version=2, body="别人写的第 2 版"))

    with pytest.raises(StoreError):
        withdrawals.withdraw(
            retirement=retire(claim), drafts=(hold(first), hold(second))
        )

    # 两半都没落盘：说法还是原来那一版，第一份草稿也没有进 hold。
    assert [row.revision for row in outreach.approved_claims()] == [1]
    assert outreach.approved_claims()[0].expires_at is None
    assert outreach.draft("draft-0001", 1) == first
    assert outreach.draft("draft-0001", 2) is None, "第一半被第二半的失败带回来了"
    assert outreach.draft("draft-0002", 2) == make_draft(
        "draft-0002", draft_version=2, body="别人写的第 2 版"
    ), "别人那一版没有被这一次撤回改掉"


def test_a_retirement_revision_that_is_already_taken_is_refused(store):
    """同一版说法写第二份**不同**内容是冲突 —— 「说法改一个字就是新的一版」。"""
    outreach, withdrawals = store
    claim = make_claim()
    outreach.put_claim(claim)

    withdrawals.withdraw(retirement=retire(claim), drafts=())

    with pytest.raises(StoreError):
        withdrawals.withdraw(
            retirement=retire(claim).model_copy(update={"text": "另一句话"}), drafts=()
        )

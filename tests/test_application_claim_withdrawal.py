"""``application/claim_withdrawal.py`` 自己的判据：**算哪些进 hold、哪些放过**。

存储那一份在 ``tests/test_claim_withdrawal_store_contract.py``（原子性、"全成或
全不成" 的落点在那里）。这里问的是这一层**判**了什么，用一个小记录替身把
``withdraw`` 的调用面记下来 —— 那两个契约断言（"恰好一次"、"两半同时交出"）
只能在这一层判，因为存储那一层看不见调用次数。

## 三条判据各挡一种错法

1. **一条不在的说法不撤**（``ConfigError`` 点名 ``expires_at``，且**一次都没写**）。
   放它过去的错法是："撤了一条本来就不生效的说法" —— 盘上多一版更正，而那一版
   什么都不改；报告上它是"撤干净了"。
2. **已经出去过的那一版不压**（``_sent_keys`` 按 ``(draft_id, draft_version)``）。
   两种错法都要挡：按 ``draft_id`` 判（一份改写过、新版还没发出去的草稿逃过撤回），
   与把 ``FAILED`` 也当成"出去过"（确定没出去的草稿被留在待发队列外，
   处置相反：那一份**可以**重发）。
3. **恰好一次写**。它是"全成或全不成"在上游的那一半 —— 适配器保证了事务，
   而这一层保证两半是**同一次**调用的入参。写两次的实现在这里就红了
   （那条承重点的变异要去改适配器，判据在存储那一份）。

``left_alone`` 里每一份都带一句为什么，判据逐条断言那句话**点到了名**
（``send_key`` 或 ``状态码``）：一个只回"放过 3 份"的实现在报告上分不清
"已经出去了"与"本来就压着"，而两者的下一步相反。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from marketops.application.claim_withdrawal import WithdrawalResult, withdraw_claim
from marketops.domain.errors import ConfigError
from marketops.domain.outreach import (
    ApprovedClaim,
    DraftState,
    MessageState,
    OutboundAttempt,
    OutreachDraft,
)
from marketops.ports.claim_withdrawal import ClaimWithdrawalStore
from tests.test_claim_withdrawal_store_contract import (
    CLAIM,
    OTHER_WORKSPACE,
    T0,
    T1,
    WORKSPACE,
    make_attempt,
    make_claim,
    make_draft,
)

#: 撤回那一刻。``T1`` 与存储契约那一份用的是同一个（同一批夹具同一套时刻）。
AT = T1


class RecordingWithdrawalStore(ClaimWithdrawalStore):
    """一个**只记录**的替身：这一层的判据全在"交了什么下去"上。

    它答两个读（引用这条说法的草稿、本工作区的尝试），并把唯一那次 ``withdraw``
    的入参原样留下。两个读都记账（``asked``），因为"按哪个工作区问的"这件事
    只能在这一层看：``workspace`` 是这一层的必填关键字，而它不进任何对象
    （``ApprovedClaim`` 没有那一格）。
    """

    def __init__(
        self,
        *,
        drafts: tuple[OutreachDraft, ...] = (),
        attempts: tuple[OutboundAttempt, ...] = (),
    ) -> None:
        self._drafts = drafts
        self._attempts = attempts
        self.asked: list[tuple[str, str]] = []
        self.calls: list[tuple[ApprovedClaim, tuple[OutreachDraft, ...]]] = []

    def drafts_citing(self, *, workspace: str, claim_id: str) -> tuple[OutreachDraft, ...]:
        self.asked.append(("drafts_citing", f"{workspace}/{claim_id}"))
        return self._drafts

    def attempts(self, *, workspace: str) -> tuple[OutboundAttempt, ...]:
        self.asked.append(("attempts", workspace))
        return self._attempts

    def withdraw(
        self, *, retirement: ApprovedClaim, drafts: tuple[OutreachDraft, ...]
    ) -> None:
        self.calls.append((retirement, drafts))

    @property
    def written_drafts(self) -> tuple[OutreachDraft, ...]:
        """那唯一一次写交下去的草稿。写上两次的话这里只看得见最后一次 ——
        所以次数由 :attr:`calls` 判，不由这一格判。"""
        assert len(self.calls) == 1, f"withdraw 被调了 {len(self.calls)} 次"
        return self.calls[0][1]

    @property
    def written_retirement(self) -> ApprovedClaim:
        assert len(self.calls) == 1
        return self.calls[0][0]


def _live_claim(**overrides) -> ApprovedClaim:
    """一条算数的说法。``expires_at=None``（永不过期）是这一格的一个合法读数。"""
    payload = {"expires_at": None}
    payload.update(overrides)
    return make_claim(**payload)


def _withdraw(store: ClaimWithdrawalStore, claim: ApprovedClaim, *, at=AT, **kwargs):
    return withdraw_claim(
        workspace=kwargs.pop("workspace", WORKSPACE), claim=claim, at=at, store=store
    )


# --- 1. 不在的说法：拒绝，且一次都没写 ------------------------------------------


def test_an_expired_claim_is_refused_and_nothing_is_written():
    """**到期当天算活着**（``is_live_at`` 的边界选择），所以这里用的是"过了期"。

    两条一起判：拒绝那句话里要有 ``expires_at``（处置是"重新读最新那一版"，
    而不是"改这次撤回的参数"），以及**一次写都没有发生**——一个先写再判的实现
    会留下一版没人要的更正。
    """
    expired = _live_claim(expires_at=T0)
    store = RecordingWithdrawalStore(drafts=(make_draft(),), attempts=())

    with pytest.raises(ConfigError) as caught:
        _withdraw(store, expired)

    message = str(caught.value)
    assert "expires_at" in message
    assert f"第 {expired.revision} 版" in message
    assert store.calls == [], "被拒的撤回不许留下任何一行"


def test_the_expiry_day_itself_still_counts():
    """``at == expires_at`` 那一刻**不算**被拒 —— 钉住承重点用的那条边界。

    少了这一条，一个把边界写成 ``<`` 的实现与一个写成 ``<=`` 的实现在这里同形，
    而它们在"上周过的期"与"今天过的期"上给出的答案相反。
    """
    claim = _live_claim(expires_at=AT)
    store = RecordingWithdrawalStore()
    result = _withdraw(store, claim)
    assert result.retirement.expires_at == AT


def test_the_refusal_reads_the_clock_it_was_given_not_the_wall_clock():
    """同一个 ``at`` 上两种说法给出相反的答案：判据是传进来的那一刻，不是"现在"。"""
    store = RecordingWithdrawalStore()
    claim = _live_claim(expires_at=T0)
    # 到期当天算活着 —— 所以换成 T0 那一刻就该放行。
    result = _withdraw(store, claim, at=T0)
    assert result.retirement.revision == 2
    with pytest.raises(ConfigError):
        _withdraw(store, claim, at=T0 + timedelta(seconds=1))


# --- 2. 撤回的形态是追加一版，不是就地改写 --------------------------------------


def test_the_retirement_is_the_next_revision_expiring_now():
    claim = _live_claim(expires_at=None)
    store = RecordingWithdrawalStore()

    result = _withdraw(store, claim)

    assert result.retirement.revision == claim.revision + 1
    assert result.retirement.expires_at == AT, "退场写的是撤回这一刻"
    assert result.retirement.claim_id == claim.claim_id
    assert result.retirement.text == claim.text, "那句话本身一个字都没改"


def test_the_claim_in_hand_is_not_mutated():
    """交进去的那一版**在盘上还得是原样**：就地改写会让"当时批准的那句话"变成另一句。"""
    claim = _live_claim(expires_at=None)
    before = claim.model_dump(mode="python")
    store = RecordingWithdrawalStore()

    _withdraw(store, claim)

    assert claim.model_dump(mode="python") == before
    assert claim.revision == 1
    assert claim.expires_at is None


# --- 3. 哪些进 hold、哪些放过 ---------------------------------------------------


def test_a_citing_draft_that_never_went_out_goes_to_hold_on_a_new_version():
    """进 hold 的形态是**出一版新的**（``draft_version + 1`` 且写明 ``supersedes``），
    不是把那一行改成 hold。``supersedes`` 那一格是那条模型校验器要求它写明的。"""
    draft = make_draft(state=DraftState.REVIEW_ONLY)
    store = RecordingWithdrawalStore(drafts=(draft,))

    result = _withdraw(store, _live_claim())

    assert result.held_ids == (draft.draft_id,)
    held = result.held[0]
    assert held.state is DraftState.HELD
    assert held.draft_version == draft.draft_version + 1
    assert held.supersedes == draft.draft_version
    assert result.left_alone == ()
    assert store.written_drafts == (held,), "交下去的正是回执里那一份"


def test_a_draft_already_held_is_left_alone_with_its_reason():
    """再压一版是同一句话的第二个版本，而"压了几次"这个数会因此虚高。"""
    draft = make_draft(state=DraftState.HELD)
    store = RecordingWithdrawalStore(drafts=(draft,))

    result = _withdraw(store, _live_claim())

    assert result.held == ()
    assert [item.draft_id for item, _ in result.left_alone] == [draft.draft_id]
    reason = result.left_alone[0][1]
    assert "held" in reason
    assert store.written_drafts == (), "没进 hold 的不许出现在写上"


def test_a_version_that_went_out_is_left_alone_and_the_reason_names_the_attempt():
    """把已经出去的东西标成 hold 是一种谎 —— 它没被拦下来。

    回执那一句必须**点到名**（``send_key`` 与状态），否则读的人拿不到出处，
    只能自己去把这张表再查一遍。
    """
    draft = make_draft()
    attempt = make_attempt(
        draft_id=draft.draft_id,
        draft_version=draft.draft_version,
        state=MessageState.DELIVERED,
        send_key="send-0007",
    )
    store = RecordingWithdrawalStore(drafts=(draft,), attempts=(attempt,))

    result = _withdraw(store, _live_claim())

    assert result.held == ()
    assert [item.draft_id for item, _ in result.left_alone] == [draft.draft_id]
    reason = result.left_alone[0][1]
    assert "send-0007" in reason
    assert MessageState.DELIVERED.value in reason
    assert store.written_drafts == ()


def test_a_second_version_of_a_draft_is_not_blocked_by_the_first_one_having_gone_out():
    """``(draft_id, draft_version)`` 两格都要看：一份**改写过**的新版没有出去过。

    按 ``draft_id`` 判的实现会让这一份逃过撤回 —— 而它正是最该被压住的那一份
    （它引着一条刚刚被撤的说法，而且还没出去）。
    """
    draft = make_draft(draft_version=2)
    attempt = make_attempt(
        draft_id=draft.draft_id, draft_version=1, state=MessageState.DELIVERED
    )
    store = RecordingWithdrawalStore(drafts=(draft,), attempts=(attempt,))

    result = _withdraw(store, _live_claim())

    assert result.held_ids == (draft.draft_id,)
    assert result.held[0].draft_version == 3


@pytest.mark.parametrize(
    "state",
    [
        MessageState.ACCEPTED_BY_PROVIDER,
        MessageState.DELIVERED,
        MessageState.DELIVERY_UNKNOWN,
        MessageState.BOUNCED,
    ],
)
def test_every_state_in_sent_or_unknown_blocks_the_hold(state: MessageState):
    """**四元集合逐条跑一遍**，而 ``DELIVERY_UNKNOWN`` 是这里最要紧的那一个：
    "可能发出去了"与"确定没出去"的处置相反（先对账 / 可以重发）。"""
    draft = make_draft()
    store = RecordingWithdrawalStore(
        drafts=(draft,), attempts=(make_attempt(state=state, failure="fixture"),)
    )

    result = _withdraw(store, _live_claim())

    assert result.held == (), (
        f"{state.value} 属于「已发出或不确定」，不该被压进 hold —— "
        "而它进了 left_alone，处置是去对账那条链"
    )


def test_a_failed_attempt_does_not_block_the_hold():
    """``FAILED`` 不在那个四元集合里 —— 确定没出去的草稿**可以**重发，
    而把它压进 hold 会让它留在待发队列外，处置相反。"""
    draft = make_draft()
    store = RecordingWithdrawalStore(
        drafts=(draft,),
        attempts=(make_attempt(state=MessageState.FAILED, failure="550 mailbox unavailable"),),
    )

    result = _withdraw(store, _live_claim())

    assert result.held_ids == (draft.draft_id,)


def test_a_not_sent_attempt_does_not_block_the_hold():
    """``NOT_SENT`` = "开出去了、还没结算"。它既不是"已发出"也不是"确定没出去"，
    而 ``sent_or_unknown`` 的判据把它挡在外面 —— 这一条钉住那条属性被这一层用上了。"""
    draft = make_draft()
    store = RecordingWithdrawalStore(
        drafts=(draft,), attempts=(make_attempt(state=MessageState.NOT_SENT),)
    )

    result = _withdraw(store, _live_claim())
    assert result.held_ids == (draft.draft_id,)


def test_the_held_and_the_left_alone_are_the_same_draft_set():
    """三份草稿里两种处置各占一部分：**一份都不许漏**（漏掉的那一份既不在
    ``held`` 也不在 ``left_alone``，而回执上它与"本来就不引用这条说法"同形）。"""
    going_out = make_draft("draft-0001")
    already_held = make_draft("draft-0002", state=DraftState.HELD)
    fresh = make_draft("draft-0003")
    store = RecordingWithdrawalStore(
        drafts=(going_out, already_held, fresh),
        attempts=(make_attempt(draft_id="draft-0001"),),
    )

    result = _withdraw(store, _live_claim())

    assert result.held_ids == ("draft-0003",)
    assert sorted(item.draft_id for item, _ in result.left_alone) == [
        "draft-0001",
        "draft-0002",
    ]
    seen = set(result.held_ids) | {item.draft_id for item, _ in result.left_alone}
    assert seen == {"draft-0001", "draft-0002", "draft-0003"}


def test_nothing_to_hold_still_retires_the_claim():
    """"没有任何草稿引用它"是一次正常的撤回，不是一次空操作 —— 那条说法**仍然**
    要退场，否则它会继续被以后生成的草稿引用。"""
    store = RecordingWithdrawalStore(drafts=(), attempts=())

    result = _withdraw(store, _live_claim())

    assert result.retirement.expires_at == AT
    assert len(store.calls) == 1
    assert store.written_drafts == ()


# --- 4. 恰好一次写、两个读按本工作区 --------------------------------------------


def test_both_halves_are_written_in_exactly_one_call():
    """**这条是承重点 3 在上游那一半的判据。** 适配器保证了事务；这一层保证两半
    是同一次调用的入参。写两次的实现在这里立刻红，而那正是"说法已经撤了、
    草稿还压着"那个窗口的来源。"""
    draft = make_draft()
    store = RecordingWithdrawalStore(drafts=(draft,))

    result = _withdraw(store, _live_claim())

    assert len(store.calls) == 1
    retirement, drafts = store.calls[0]
    assert retirement is result.retirement
    assert drafts == result.held


def test_both_reads_are_scoped_to_the_workspace_it_was_given():
    """``workspace`` 是这一层的**必填关键字**而不是从 ``claim`` 上读的
    （``ApprovedClaim`` 没有那一格）。两个读都按它问，而它一路上没被换掉。"""
    store = RecordingWithdrawalStore()

    _withdraw(store, _live_claim(), workspace=OTHER_WORKSPACE)

    assert store.asked == [
        ("attempts", OTHER_WORKSPACE),
        ("drafts_citing", f"{OTHER_WORKSPACE}/{CLAIM}"),
    ]


def test_the_claim_id_is_what_the_drafts_are_asked_by_not_the_revision():
    """``OutreachDraft.claim_ids`` 记的是 ``claim_id``，版本不在草稿那一格上 ——
    按 ``(claim_id, revision)`` 问会让一份引用了第 1 版而盘上已经到第 2 版的草稿
    落在集合外，而回执上它是"没有草稿引用它"。"""
    store = RecordingWithdrawalStore()
    _withdraw(store, _live_claim(), at=T0)
    assert ("drafts_citing", f"{WORKSPACE}/{CLAIM}") in store.asked


def test_the_result_is_frozen_and_carries_both_halves():
    """``WithdrawalResult`` 的三格是这次撤回**实测**的东西：写下去的那一版、
    真进 hold 的那几份、放过的那几份（各带一句为什么）。"""
    store = RecordingWithdrawalStore()
    result = _withdraw(store, _live_claim())
    assert isinstance(result, WithdrawalResult)
    assert result.left_alone == ()
    with pytest.raises(FrozenInstanceError):
        result.retirement = None  # type: ignore[misc]

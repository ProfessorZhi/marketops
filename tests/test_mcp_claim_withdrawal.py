"""``withdraw_claim`` 在**工具面**上的判据（冻结表 §3.2 那条「Agent A 额外的一条判据」）。

## 为什么这一层要单独判一遍

应用层的三条判据（哪些进 ``hold``、哪些放过、恰好一次写）已经在
``tests/test_application_claim_withdrawal.py`` 里，落库那一份在
``tests/test_claim_withdrawal_store_contract.py`` 里。这一份问的是**只有在这个面上
才看得见**的那几格：

* **入参表**：这一条工具收一格 ``claim_id``，**没有** ``revision``。冻结表那句
  「``withdraw_claim`` 撤回一条 ``ApprovedClaim``」没说版本，而这一格的缺失是
  **承重的**：调用方给一个过期的版本号与给一个正确的 id 是两件要分开的事
  （一个去重读最新那版、一个去核对 id），多一格 ``revision`` 会让它们同形。
* **拒绝发生在读库之前**：空 id 与不规范的 id 都是**形状**问题，形状不对时不必
  去查库 —— 这一条在这一层判，因为「查了没有」只有在这一层看得见。
* **回执的形状**：``held`` / ``left_alone`` 各自是什么、``left_alone`` 每一份
  带不带一句为什么。应用层的 ``WithdrawalResult`` 是**对象**，投影成回执是这一层
  做的事，而投影会丢东西。
* **盘上真写成了什么**：这一条用的是**真的 SQLite 存储**（不是替身），所以
  「受影响未发的草稿进了 hold、无关的任务一格没变」这一句是在库上判的。

## 身份

这一条工具不收 ``workspace``，工作区取自 ``settings.profile``（本部署服务的那个），
与 ``application/claim_withdrawal.withdraw_claim`` 的 docstring 逐字同一条 ——
``ApprovedClaim`` 上没有工作区那一格，而受影响的草稿**按工作区**枚举。所以这里
不需要装令牌：被测对象一个字节的身份都不读。**这不表示这一条不需要授权**：
角色/scope 那一条在 ``security/catalog.py`` 的 ``_spec`` 上（``_CONTROL`` +
``frozenset({Role.CONTROLLER})``），由服务端的调度层执行，判据在
``tests/test_security_catalog.py``。
"""

from __future__ import annotations

import inspect
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from marketops.adapters.mcp_server.claim_withdrawal import claim_withdrawal_handlers
from marketops.adapters.sqlite.claim_withdrawal import SqliteClaimWithdrawalStore
from marketops.adapters.sqlite.outreach import SqliteOutreachStore
from marketops.config import Settings
from marketops.domain.outreach import (
    ApprovedClaim,
    DraftState,
    MessageState,
    OutboundAttempt,
    OutreachDraft,
)
from marketops.interfaces.composition import (
    default_claim_withdrawal_store,
    default_outreach_store,
)
from marketops.ports.claim_withdrawal import ClaimWithdrawalStore
from marketops.ports.outreach import OutreachStore

from .test_claim_withdrawal_store_contract import CLAIM, T0, make_attempt, make_claim, make_draft

UTC = timezone.utc

#: 这一刻**从当下算**，不写死 —— 与 ``test_mcp_workstream.py`` 逐字同一条理由：
#: 一个写死的「未来」时刻是一颗定时炸弹，而「过期」这一族判据全靠它。
PAST = datetime.now(UTC) - timedelta(days=1)


class Wiring:
    """一次接线：组合根那两个具名工厂 + 这一族的处理器。

    ``claims`` 与 ``withdrawals`` 都取**具名工厂**（``default_outreach_store`` /
    ``default_claim_withdrawal_store``），不是就地写的 ``lambda``：那一族 docstring
    逐字要求「服务面拿的是哪一个」只在具名工厂那一处看得出，其余各处各写一行
    lambda 今天恰好同形、迟早不一样。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.claims = default_outreach_store(settings)
        self.withdrawals = default_claim_withdrawal_store(settings)
        self.handlers = claim_withdrawal_handlers(
            settings=settings, claims=self.claims, withdrawals=self.withdrawals
        )

    # -- 调用面 ------------------------------------------------------------------

    def withdraw(self, **kwargs: Any) -> dict[str, Any]:
        return self.handlers["withdraw_claim"](**kwargs)

    # -- 种盘（用的是真存储，不是替身）--------------------------------------------

    def seed(
        self,
        *,
        claim: ApprovedClaim | None = None,
        drafts: tuple[OutreachDraft, ...] = (),
        attempts: tuple[tuple[OutboundAttempt, datetime], ...] = (),
    ) -> None:
        book = self.claims()
        if claim is not None:
            book.put_claim(claim)
        for draft in drafts:
            book.put_draft(draft)
        for attempt, at in attempts:
            book.begin_attempt(attempt, at=at)

    # -- 盘上那几样（读的是存储本身，不是处理器的返回值）---------------------------

    def latest_claim(self) -> ApprovedClaim:
        rows = [row for row in self.claims().approved_claims() if row.claim_id == CLAIM]
        assert len(rows) == 1, rows
        return rows[0]

    def claim_rows(self) -> list[tuple[str, int]]:
        """盘上**真的**有几版说法。数的是库，不是 ``approved_claims()`` 的投影。

        后者每个 ``claim_id`` 只回**最新**那一版，所以「一次撤回往盘上写了几版」
        在它上面看不出来 —— 一次把第 2 版写了两遍的实现与一次只写一遍的实现在那里
        同形，而那是「一行都没多写」这条判据的全部内容。
        """
        with sqlite3.connect(self.settings.database_path) as conn:
            rows = conn.execute(
                "SELECT claim_id, revision FROM outreach_claim ORDER BY claim_id, revision"
            ).fetchall()
        return [(str(claim_id), int(revision)) for claim_id, revision in rows]

    def draft(self, draft_id: str, version: int) -> OutreachDraft | None:
        return self.claims().draft(draft_id, version)


@pytest.fixture
def wiring(settings: Settings) -> Wiring:
    """一只**空的**库。这一族的每一条测试自己种它要的那几行。"""
    return Wiring(settings)


def _draft(draft_id: str, **overrides: Any) -> OutreachDraft:
    """本工作区的一份草稿。工作区取自 ``settings`` 的方式见各条测试的 ``wiring``。"""
    return make_draft(draft_id, **overrides)


def _refusing_factory(what: str) -> Callable[[], Any]:
    """一个**一旦被调用就炸**的工厂。

    「形状不对时不必去查库」这句话的判据只能是「那句查询有没有发生」，
    而一个真的存储读没读过盘在回执上看不出来 —— 所以这一格用的是一个会在被读到
    的那一刻就响的替身。它不是被测对象（被测对象是那个顺序），只是一只温度计。
    """

    def factory() -> Any:
        raise AssertionError(f"形状检查没过，却读到了 {what}")

    return factory


# --- 1 · 入参表 -----------------------------------------------------------------


def test_the_tool_takes_exactly_one_parameter_and_no_revision() -> None:
    """``claim_id`` 一格，**没有** ``revision``、没有 ``workspace``、没有 ``at``。

    ``revision`` 那一格是这一条最要紧的缺失：撤回是给这个 ``claim_id`` **追加**一版
    （``expires_at`` 落在撤回那一刻），所以「撤哪一版」不是这条工具的问题 —— 而多出
    这一格会让「调用方读到的是一个过期的版本」与「调用方打错了 id」在回执上同形。
    """
    handlers = claim_withdrawal_handlers(
        settings=None,  # type: ignore[arg-type]  # 只看签名，不调用
        claims=lambda: None,  # type: ignore[arg-type,return-value]
        withdrawals=lambda: None,  # type: ignore[arg-type,return-value]
    )
    signature = inspect.signature(handlers["withdraw_claim"])
    assert set(signature.parameters) == {"claim_id"}
    assert signature.parameters["claim_id"].default == ""
    for forbidden in ("revision", "workspace", "at", "by", "claim", "expires_at"):
        assert forbidden not in signature.parameters, forbidden


# --- 2 · 形状不对时一次库都不查 --------------------------------------------------


def test_an_empty_claim_id_is_refused_without_reading_the_library() -> None:
    """空 id 是一条**参数**问题：没有 id 时没有可撤的东西。

    一个「撤最近那一条」的兜底会让一次漏传参数变成一次真的撤回，而报告上看不出来
    撤的是哪一条 —— 所以这里是拒绝，而不是取一条缺省。
    """
    handlers = claim_withdrawal_handlers(
        settings=None,  # type: ignore[arg-type]
        claims=_refusing_factory("说法存储"),
        withdrawals=_refusing_factory("撤回存储"),
    )

    with pytest.raises(ToolError) as caught:
        handlers["withdraw_claim"]()

    text = str(caught.value)
    assert "claim_id" in text
    assert "Error executing tool" not in text, "这句话逃出了 ToolError，客户端只会看到工具名"


def test_a_non_canonical_claim_id_is_refused_without_reading_the_library() -> None:
    """不规范的 id 是**形状**问题，不是「这条说法不在盘上」—— 两者处置不同。

    ``ApprovedClaim.claim_id`` 自己过同一个检查，所以形状不对时不必去查库。
    """
    handlers = claim_withdrawal_handlers(
        settings=None,  # type: ignore[arg-type]
        claims=_refusing_factory("说法存储"),
        withdrawals=_refusing_factory("撤回存储"),
    )

    with pytest.raises(ToolError) as caught:
        handlers["withdraw_claim"](claim_id="CLAIM-01-NO-HERBICIDE")

    text = str(caught.value)
    assert "CLAIM-01-NO-HERBICIDE" in text
    assert "claim-01-no-herbicide" in text, "拒绝那句话要给出会被规范成的那个 id"
    assert "Error executing tool" not in text


# --- 3 · 「读不到这条说法」与「这条说法已经不算数了」是两件事 -----------------------


def test_an_unknown_claim_id_is_refused_and_the_message_names_it(wiring: Wiring) -> None:
    """读不到 ⇒ 点名那个 id，且**一行都没写**。

    这一格读的是每个 ``claim_id`` 的**最新**一版，所以「读不到」只可能是这个 id
    从来没被批准过 —— 与「已经过期」（下一条）的处置相反。
    """
    wiring.seed(claim=make_claim())

    with pytest.raises(ToolError) as caught:
        wiring.withdraw(claim_id="claim-99-never-approved")

    text = str(caught.value)
    assert "claim-99-never-approved" in text
    assert "Error executing tool" not in text
    assert wiring.claim_rows() == [(CLAIM, 1)], "被拒的撤回不许留下任何一行"
    assert wiring.draft("draft-0001", 2) is None


def test_an_expired_claim_is_refused_and_the_message_names_expires_at(wiring: Wiring) -> None:
    """已经不算数的说法**不撤** —— 撤它是一次没有效果的写。

    ``expires_at`` 必须出现在拒绝那句话里：处置是「重读最新那一版」，而只有点名了
    那个时刻，读的人才分得清「这条说法过期了」与「这个 id 我读错了」。
    """
    wiring.seed(claim=make_claim(expires_at=PAST))

    with pytest.raises(ToolError) as caught:
        wiring.withdraw(claim_id=CLAIM)

    text = str(caught.value)
    assert "expires_at" in text
    #: 那一刻**点到名**即可 —— 它在拒绝那句话里是 ``datetime`` 的默认形状
    #: （空格分隔），不是 ``isoformat`` 的 ``T``。判日期那一段，不判分隔符。
    assert PAST.strftime("%Y-%m-%d") in text
    assert wiring.claim_rows() == [(CLAIM, 1)]
    assert wiring.latest_claim().expires_at == PAST, "撤回没有把它就地改写"


# --- 4 · 回执的形状 -------------------------------------------------------------


def test_the_receipt_holds_the_unsent_drafts_and_explains_every_one_it_left_alone(
    wiring: Wiring,
) -> None:
    """**这一条是承重点 3 在工具面上的判据。**

    四份草稿，而受影响的集合**由证据算出来**：

    * ``draft-0001`` 引用这条说法、没出去过 → 进 ``hold``（出一版新的，``state=held``）；
    * ``draft-0002`` 引用它、**已经出去过**（一条 ``DELIVERED`` 的尝试）→ 放过，
      理由里点着 ``send_key`` 与状态码；
    * ``draft-0003`` 引用它、**本来就压着**（``state=HELD``）→ 放过，理由是「再压
      一版会让『压了几次』虚高」；
    * ``draft-0004`` **不引用**它（它引的是另一条说法）→ 一格不变。

    「无关任务不变」在这里判的是**盘**：``left_alone`` 里那两份与 ``draft-0004``
    各自的第 1 版都还在，而且**没有**多出第 2 版。
    """
    workspace = wiring.settings.profile
    going_out = _draft("draft-0002", workspace=workspace)
    already_held = _draft("draft-0003", workspace=workspace, state=DraftState.HELD)
    unrelated = _draft("draft-0004", workspace=workspace, claim_ids=("claim-02-seed-rate",))
    wiring.seed(
        claim=make_claim(),
        drafts=(
            _draft("draft-0001", workspace=workspace),
            going_out,
            already_held,
            unrelated,
        ),
        attempts=(
            (
                make_attempt(
                    send_key="send-0007",
                    draft_id="draft-0002",
                    state=MessageState.DELIVERED,
                    workspace=workspace,
                ),
                T0,
            ),
        ),
    )

    receipt = wiring.withdraw(claim_id=CLAIM)

    # 回执的形状：六格，一格不多。
    assert set(receipt) == {
        "claim_id",
        "retired_revision",
        "expires_at",
        "withdrawn_at",
        "held",
        "left_alone",
    }
    assert receipt["claim_id"] == CLAIM
    assert receipt["retired_revision"] == 2
    assert receipt["expires_at"] == receipt["withdrawn_at"], "退场写的是撤回这一刻"

    # 压下去的那一份：**新版本**，不是把旧那行改成 hold。
    assert [item["draft_id"] for item in receipt["held"]] == ["draft-0001"]
    held = receipt["held"][0]
    assert held["state"] == DraftState.HELD.value
    assert held["draft_version"] == 2
    assert held["supersedes"] == 1
    assert held["body"] == _draft("draft-0001", workspace=workspace).body, "正文一个字没改"

    # 放过的两份：**每一份带一句为什么**，而且那句话点到名。
    left = {item["draft"]["draft_id"]: item["why"] for item in receipt["left_alone"]}
    assert set(left) == {"draft-0002", "draft-0003"}
    assert "send-0007" in left["draft-0002"]
    assert MessageState.DELIVERED.value in left["draft-0002"]
    assert DraftState.HELD.value in left["draft-0003"]
    assert all(item["why"] for item in receipt["left_alone"]), "没有一句空话"

    # 盘上：那一次写落成了什么。
    assert wiring.draft("draft-0001", 1) is not None, "旧那一版还在（追加，不是就地改）"
    assert wiring.draft("draft-0001", 2).state is DraftState.HELD  # type: ignore[union-attr]


def test_the_unrelated_rows_are_untouched_on_disk(wiring: Wiring) -> None:
    """「无关任务不变」是一条**关于盘的话**，不是在回执里少列几行。

    两份「无关的」：一份压根不引用这条说法，一份在**别的工作区**里引用了它。
    后者是这一条最要紧的一半 —— 受影响的草稿是**按工作区**枚举的，而一个按
    ``claim_id`` 全表扫的实现会把别人那一份也压进 hold，而两份回执各自都正常。
    """
    workspace = wiring.settings.profile
    unrelated = _draft("draft-0004", workspace=workspace, claim_ids=("claim-02-seed-rate",))
    elsewhere = _draft("draft-0005", workspace="another-market")
    wiring.seed(
        claim=make_claim(),
        drafts=(_draft("draft-0001", workspace=workspace), unrelated, elsewhere),
    )

    receipt = wiring.withdraw(claim_id=CLAIM)

    assert [item["draft_id"] for item in receipt["held"]] == ["draft-0001"]
    assert wiring.draft("draft-0004", 1) == unrelated, "不引用它的那一份一格没变"
    assert wiring.draft("draft-0004", 2) is None
    assert wiring.draft("draft-0005", 1) == elsewhere, "别的工作区那份一格没变"
    assert wiring.draft("draft-0005", 2) is None


def test_nothing_to_hold_is_still_a_normal_result(wiring: Wiring) -> None:
    """没有任何草稿引用它是一次**正常**的撤回，不是一次空操作。

    ``held == []`` 在这里不歧义：这条工具跑过了、``retired_revision`` 在，所以空
    列表说的是「没有受影响的」，不是「读错了工作区」。
    """
    wiring.seed(claim=make_claim())

    receipt = wiring.withdraw(claim_id=CLAIM)

    assert receipt["held"] == []
    assert receipt["left_alone"] == []
    assert receipt["retired_revision"] == 2
    assert wiring.claim_rows() == [(CLAIM, 1), (CLAIM, 2)]


# --- 5 · 撤回之后它不再算数 -----------------------------------------------------


def test_the_retired_claim_cannot_be_withdrawn_twice(wiring: Wiring) -> None:
    """第二次撤回读到的是**已经退场的那一版** ⇒ 拒绝，且盘上不多一行。

    这一条钉的是「退场是真的退场」：``approved_claims()`` 只回每个 id 的**最新**一版，
    而那一版的 ``expires_at`` 落在撤回那一刻 —— 于是同一句话不会被撤两遍
    （一次没有效果的写）。
    """
    workspace = wiring.settings.profile
    wiring.seed(claim=make_claim(), drafts=(_draft("draft-0001", workspace=workspace),))

    first = wiring.withdraw(claim_id=CLAIM)
    assert first["retired_revision"] == 2

    with pytest.raises(ToolError) as caught:
        wiring.withdraw(claim_id=CLAIM)

    text = str(caught.value)
    assert "expires_at" in text
    assert "Error executing tool" not in text
    assert wiring.claim_rows() == [(CLAIM, 1), (CLAIM, 2)], "被拒的那一次没有多写一版"
    assert wiring.draft("draft-0001", 3) is None, "被拒的那一次没有把草稿再压一版"


def test_both_halves_are_on_disk_after_one_call(wiring: Wiring) -> None:
    """一次调用把**两半**都写下去：说法的新版本，以及受影响草稿的新版本。

    这一条与存储契约那一份的原子性判据是同一个承重点的两端：那边判「半途失败
    两半都不在」，这里判「成功时两半都在」。少了这一条，一个只写一半的实现在
    回执上完全正常。
    """
    workspace = wiring.settings.profile
    wiring.seed(claim=make_claim(), drafts=(_draft("draft-0001", workspace=workspace),))

    wiring.withdraw(claim_id=CLAIM)

    latest = wiring.latest_claim()
    assert latest.revision == 2
    assert latest.expires_at is not None, "退场写的是 expires_at，不是就地改写"
    assert latest.text == make_claim().text, "那句话本身一个字都没改"
    assert wiring.draft("draft-0001", 2).state is DraftState.HELD  # type: ignore[union-attr]


def test_the_two_factories_are_two_different_objects(wiring: Wiring) -> None:
    """``claims`` 与 ``withdrawals`` 是**两个**存储，不是一个。

    它们答两个不同的问题（「这个 id 指的是哪一行」与「本工作区里哪些草稿引用了
    它、以及那次原子的写」），而合成一格会让「这个部署有没有领活」与「它记不记得
    撤回」变成同一件事。这里判的是它们**不是同一个对象**，且各自就是组合根那
    两个具名工厂交出来的那一个类型。
    """
    assert isinstance(wiring.claims(), OutreachStore)
    assert isinstance(wiring.withdrawals(), ClaimWithdrawalStore)
    assert isinstance(wiring.withdrawals(), SqliteClaimWithdrawalStore)
    assert isinstance(wiring.claims(), SqliteOutreachStore)
    assert wiring.claims() is not wiring.withdrawals()


def test_the_tool_name_is_the_one_the_catalog_declares() -> None:
    """这一族交出去的键**恰好**是 ``withdraw_claim`` —— 名字说了算。

    名字是工具面上的地址：一个叫 ``withdraw`` 的键在组合根那一层接不上，而它
    在**这一层**看着一切正常。
    """
    handlers = claim_withdrawal_handlers(
        settings=None,  # type: ignore[arg-type]
        claims=lambda: None,  # type: ignore[arg-type,return-value]
        withdrawals=lambda: None,  # type: ignore[arg-type,return-value]
    )
    assert set(handlers) == {"withdraw_claim"}

"""§8/§9 的对账面在**服务面**上的判据：``list_open_reservations``。

## 为什么要单独一个文件

``ports/budget.py::open_reservations`` 在被接上这条工具之前**没有生产调用者**
（三个实现、五处测试调用）。契约测试判过它的形状（只回没结算的、按创建时间排序），
但「它被谁用」那个问题是空的 —— 而 §9 与 §5 要求「未知计费**先对账**」，对账的第一
步就是从账上挑出悬着的那几笔。这一步在服务面上无从开始的时候，一本跪着的额度与一本
没人问过的额度在报告上同形。

这一份判四件事，四件都只在这条路上存在：

1. **``no_store`` 与「一条都没有」分得开。** 与 ``list_fetch_attempts`` 逐字同一条。
2. **那个 id 真的能拿去结算。** 这一条是本文件最承重的一条：回执上的
   ``reservation_id`` 必须就是 ``BudgetLedger.spend`` / ``refund`` 收的那个值。
   一个自己拼一个好看 id 的实现能让上面所有形状判据全绿，而拿到它的人对不了账。
3. **一次读不建库。** ``SqliteBudgetLedger.__init__`` 会 ``ensure_schema``，所以在
   一个还没写过东西的部署上顺手建出一个空库是**默认行为**。
4. **研究身份读不到它。** 这本账跨主体。

## 这一份**不**判什么

结算与退款本身的语义在 ``tests/test_budget_contract.py``（对两个实现各跑一遍）。
这一份只用它们当**量具** —— 拿回执给的那个 id 去结算，看它是否真的落定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.budget import budget_handlers
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.config import Settings
from marketops.domain.enums import BudgetLine
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

#: 这一条工具的名字。写一次、引用多处 —— 与目录里那一条对不上时，这个文件里所有
#: 用例会一起红，而红的话里点名的是它，不是某一条断言。
TOOL = "list_open_reservations"

#: 两个主体。``scope`` 的形状与 ``RunBudget`` 拼的一致（``<profile>:<run>``）——
#: 用具名的常量而不是每次现拼，是为了让「按主体筛」那一条判据与它是同一个字符串。
SCOPE = "uk-acme-natural-grass:run-1"
OTHER_SCOPE = "uk-acme-natural-grass:run-2"


@dataclass(frozen=True)
class _Wired:
    """一条工具与它读的那本账，与 ``_compose`` 同一接法。"""

    handlers: dict[str, Any]
    ledger: SqliteBudgetLedger


def _factory(settings: Settings):
    """组合根交出来的那种**工厂**（惰性）。

    传一本建好的账会让「组合期就把库建出来了」这件事在判据上消失
    —— ``SqliteBudgetLedger`` 一构造就 ``ensure_schema``。
    """
    return lambda: SqliteBudgetLedger(settings.database_path)


@pytest.fixture
def wired(settings: Settings) -> _Wired:
    return _Wired(
        handlers=budget_handlers(settings=settings, ledger_factory=_factory(settings)),
        # 写行的那个入口按路径**直接**构造，不走夹具的工厂：那个工厂是被测对象的
        # 一部分，用它来写行会让「工厂指向了另一个库」在回执上消失（写进去与读出来
        # 都是那一个错的库，两边一致，于是判据全绿）。与
        # ``test_mcp_fetch_attempts.py::_ledger`` 同一条。
        ledger=SqliteBudgetLedger(settings.database_path),
    )


def _drive(
    settings: Settings,
    tmp_path: Path,
    calls: list[tuple[str, dict[str, Any]]],
    *,
    handlers: dict[str, Any] | None = None,
    roles: list[Role] | None = None,
):
    """起一个真的服务端，用给定角色的令牌调这几条工具。

    与 ``test_mcp_fetch_attempts.py::_drive`` 同形（摘要与令牌必须配成一对，配错了
    会话在握手期就失败），那两个文件的这一份都是各自的夹具，不跨文件复用。
    """
    granted = roles or [Role.CONTROLLER]
    token, digest = mint("caller-1", granted)
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "caller-1", granted))),
        encoding="utf-8",
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        reservations=handlers,
        calls=tuple(calls),
    )
    assert not outcome.handshake_error, outcome.handshake_error
    return outcome


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = 0) -> str:
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却回了：{call.text}"
    return call.text


def _reserve(ledger: SqliteBudgetLedger, scope: str, line: BudgetLine, key: str, units: int = 1):
    """先设上限，再押一笔 —— ``reserve`` 在没有上限的科目上抛（未配置 ≠ 无限）。"""
    ledger.set_ceiling(scope, line, 10, source="tests/test_mcp_budget.py")
    return ledger.reserve(scope, line, units, idempotency_key=key)


# --- 读出来的就是那些悬着的 --------------------------------------------------


def test_the_page_lists_what_has_not_settled_and_leaves_out_what_has(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**悬着的那些读得出来，结算过的那些不再出现。**

    两半都要：只有前半时，一个「回全部预留」的实现照样绿 —— 而它会让**已经花掉的
    钱**看起来还等着人去对账，于是读到的人会去退一笔已经落定的账。
    """
    hanging = _reserve(wired.ledger, SCOPE, BudgetLine.SEARCH, "q-1", units=3)
    settled = _reserve(wired.ledger, SCOPE, BudgetLine.DOCUMENT, "q-2")
    wired.ledger.spend(settled.reservation_id)

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    assert payload["store_state"] == "present", payload
    assert [row["reservation_id"] for row in payload["reservations"]] == [
        hanging.reservation_id
    ], payload["reservations"]
    assert payload["counts"]["returned"] == 1, payload["counts"]


def test_the_row_carries_what_the_two_reconciling_actions_need(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """逐字段：id、主体、科目、单位数、幂等键、创建时刻。

    这一条是「名单」这个说法的最小内容 —— 少了 ``scope`` 与 ``line``，读到的人
    不知道该去处理哪个主体的哪个科目；少了 ``units``，不知道这笔有多重。
    """
    booked = _reserve(wired.ledger, SCOPE, BudgetLine.SEARCH, "q-1", units=3)

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))
    (row,) = payload["reservations"]

    assert row["reservation_id"] == booked.reservation_id, row
    assert row["scope"] == SCOPE, row
    assert row["line"] == "search", row
    assert row["units"] == 3, row
    assert row["idempotency_key"] == "q-1", row
    assert row["state"] == "reserved", row
    assert row["created_at"], row
    assert row["settled_at"] is None, (
        "还没结算的那一格必须是 None，而不是一个看起来像结算时刻的值"
    )


def test_the_page_has_exactly_the_keys_it_documents(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """回执的格**恰好**是那六格 —— 不多一格「顺手能算」的读数，不少一格。

    这一条钉的是 ``budget.py`` 模块 docstring 里那句「这一页**没有**当场算的读数」。
    一句关于「不存在的东西」的话，只能由一条**穷举形状**的判据来执行：否则下一个
    人加一格 ``usage``（读者会以为它是账上的列，其实是当场算的）或加一格
    ``remaining``（那要按 ``(scope, line)`` 各算一次），那句 docstring 就变成假话，
    而没有任何一条用例会红。

    ``store_state`` 与 ``workspace`` 也在这一格里，理由同上：它们说的不是账，
    是这个部署 —— 少了它们，一本没接线的账与一本空账在回执上会合流。
    """
    _reserve(wired.ledger, SCOPE, BudgetLine.SEARCH, "q-1")

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    assert set(payload) == {
        "workspace",
        "store_state",
        "filters",
        "reservations",
        "counts",
        "note",
    }, payload
    assert set(payload["counts"]) == {"returned", "held_units", "by_line"}, payload["counts"]
    #: 每一行也只有账上那几列 —— 没有一格是算出来的。
    assert set(payload["reservations"][0]) == {
        "reservation_id",
        "scope",
        "line",
        "units",
        "state",
        "idempotency_key",
        "created_at",
        "settled_at",
        "note",
    }, payload["reservations"][0]


def test_the_counts_name_every_line_including_the_zeros(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``by_line`` 把四个科目都列出来，哪怕是 0。

    这一格与「一个没人测过的指标」正相反：这里数的是一批我们真的读过、真的看得见的
    行，所以 0 是一个**读数**（这个科目上没有悬账）。只列出出现过的那些值，会让
    ``by_line`` 的形状随数据变，读的人分不出「这个科目没有悬账」与「这个科目没被数」
    —— 而 §8 要求浏览器子请求**单独**计量，正是为了让它挂住时能被单独看见。
    """
    _reserve(wired.ledger, SCOPE, BudgetLine.SEARCH, "q-1", units=3)
    _reserve(wired.ledger, SCOPE, BudgetLine.BROWSER_SUBREQUEST, "q-2", units=2)

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    counts = payload["counts"]
    assert set(counts["by_line"]) == {line.value for line in BudgetLine}, counts
    assert counts["by_line"]["search"] == 1, counts
    assert counts["by_line"]["browser_subrequest"] == 1, counts
    assert counts["by_line"]["document"] == 0, "没有的那一类要报 0，不是缺席"
    assert counts["by_line"]["inference"] == 0, "没有的那一类要报 0，不是缺席"
    assert counts["held_units"] == 5, counts


# --- 那个 id 真的能拿去结算（本文件最承重的一条）-----------------------------


def test_the_id_this_page_hands_back_is_the_id_the_settlement_takes(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**开环 → 闭环。** 拿回执给的那个 id 去结算，那一笔必须真的落定。

    这一条判的是**同一个 id 空间**。``fetch_attempt`` 那一页有同形的一条
    （``test_the_row_names_the_browser_as_the_method...`` 判 ``attempt_id`` 的种子），
    理由逐字相同：一个自己拼一个好看 id 的实现能让形状判据全绿，而拿到它的人对不了账
    —— 而这正是这一页存在的理由（``open_reservations`` 的 docstring：「否则『有多少
    额度悬着』只能靠翻库」）。

    第二半同样承重：结算**之后**这一页必须空了。少了它，一个把 ``spend`` 接到别处去的
    实现照样绿，而它对账对的是两份不同的记录。
    """
    booked = _reserve(wired.ledger, SCOPE, BudgetLine.SEARCH, "q-1")

    first = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))
    (row,) = first["reservations"]

    # 拿这一页给的 id 去做那两个动作之一。
    settled = wired.ledger.spend(row["reservation_id"])
    assert settled.reservation_id == booked.reservation_id, (
        "回执给的 id 不是账本认的那一个 —— 对账的第一步就走不通"
    )

    second = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))
    assert second["reservations"] == [], second
    assert second["counts"]["returned"] == 0, second["counts"]


def test_a_refund_clears_the_same_row_and_needs_a_reason(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """退款那一条路同样从这一页给的 id 走，而它**要写明理由**。

    §8 把「不要默认释放预算」做成了一个不存在的状态转移（没有任何路径把
    ``reserved`` 自动推向 ``refunded``），所以退款只能是**一次写明理由的显式调用**。
    这一条同时钉住两半：这一页不提供退款入口，而账本要求理由 —— 两句合起来才是
    「对账」与「顺手退掉」不是同一件事。
    """
    _reserve(wired.ledger, SCOPE, BudgetLine.SEARCH, "q-1")

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))
    (row,) = payload["reservations"]

    with pytest.raises(ValueError):
        wired.ledger.refund(row["reservation_id"], reason="")
    wired.ledger.refund(row["reservation_id"], reason="那一次检索超时，没有结果可用")

    after = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))
    assert after["reservations"] == [], after


# --- 与 list_fetch_attempts 同形的两条 ---------------------------------------


def test_an_empty_scope_is_a_filter_and_not_a_wildcard(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``scope=""`` 与**没给** ``scope`` 是两件事，而这一条钉在**工具**这一层。

    端口那一侧有一个 ``scope=None`` 的缺省，所以工具这一层多一次机会把它丢掉：
    一个 ``scope or None`` 的写法就能把「不属于任何主体的那一批」变成「全部主体」，
    而**端口那边照样全绿**。与 ``test_mcp_fetch_attempts.py`` 那一条逐字同形。
    """
    _reserve(wired.ledger, SCOPE, BudgetLine.SEARCH, "q-1")
    _reserve(wired.ledger, "", BudgetLine.SEARCH, "q-2")

    everything = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))
    assert everything["counts"]["returned"] == 2, everything["counts"]

    none_of_the_subjects = _ok(
        _drive(settings, tmp_path, [(TOOL, {"scope": ""})], handlers=wired.handlers)
    )
    assert none_of_the_subjects["counts"]["returned"] == 1, none_of_the_subjects["counts"]
    assert [row["scope"] for row in none_of_the_subjects["reservations"]] == [""], (
        "``scope=\"\"`` 问的是「不属于任何主体的那一批」，不是「不筛」"
    )

    one_subject = _ok(
        _drive(settings, tmp_path, [(TOOL, {"scope": SCOPE})], handlers=wired.handlers)
    )
    assert one_subject["counts"]["returned"] == 1, one_subject["counts"]
    assert [row["scope"] for row in one_subject["reservations"]] == [SCOPE], one_subject


def test_the_factory_missing_un_registers_the_tool(settings: Settings) -> None:
    """工厂缺了 ⇒ **这一条不注册**（空表），而不是注册成恒回空列表的替身。

    这一条在服务面上**测不出来**：``build_server`` 把 ``{}`` 与 ``None`` 一视同仁
    （都落回 stub）。所以判据只能落在工厂这一层 —— 而它必须有人守。
    """
    assert budget_handlers(settings=settings, ledger_factory=None) == {}
    complete = budget_handlers(settings=settings, ledger_factory=_factory(settings))
    assert set(complete) == {TOOL}, complete


def test_a_research_identity_cannot_read_the_ledger(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """研究身份读不到这本账：它**跨主体**。

    ``scope`` 就是业务主体的名字，所以这本账给研究侧，等于让一次研究看见**别的账号**
    还剩多少额度、花了多少。研究侧本来就在自己那一跳的 ``search_web`` 回执上拿到了
    ``budget`` 那一格 —— 它不需要账。

    这一条**必须接线之后才作数** —— 没接线时它虽然照旧不可见，但不可见的理由变成了
    「没接线」，而那条路径与「角色不对」是两件事。
    """
    _reserve(wired.ledger, SCOPE, BudgetLine.SEARCH, "q-1")
    outcome = _drive(
        settings,
        tmp_path,
        [(TOOL, {}), ("definitely_not_a_tool", {})],
        handlers=wired.handlers,
        roles=[Role.RESEARCHER],
    )
    denied = _refused(outcome, 0)
    assert "尚未接线" not in denied, (
        "接好了却报「尚未接线」—— 那说明被拒的理由不是角色，而这一条用例就不是"
        "在测权限了"
    )
    unknown = _refused(outcome, 1)
    assert denied.replace(TOOL, "") == unknown.replace("definitely_not_a_tool", ""), (
        f"两条拒绝的措辞不同：\n不属于你的：{denied}\n不存在的：{unknown}"
    )


def test_reading_the_ledger_writes_nothing_and_creates_no_database(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """这一页只读：行一行不动，且在一个空部署上**不建库**。

    后半是这一条的重心：``SqliteBudgetLedger`` 的构造会 ``ensure_schema``，所以一次
    查询在一个还没写过任何东西的部署上顺手建出一个空库是**默认行为**。后果不是多一个
    文件 —— 此后「还没有产物」与「这里本该有东西」在磁盘上再也分不开，而一本账的存在
    本身就是一个读数（与 ``test_mcp_fetch_attempts.py`` 那一条逐字同一条）。
    """
    _reserve(wired.ledger, SCOPE, BudgetLine.SEARCH, "q-1")
    snapshot = [
        (row.reservation_id, row.state, row.settled_at)
        for row in wired.ledger.open_reservations()
    ]

    for _ in range(2):
        _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    after = [
        (row.reservation_id, row.state, row.settled_at)
        for row in wired.ledger.open_reservations()
    ]
    assert after == snapshot, "这一页写账了（或者顺手结算了它自己读到的那些）"

    # 另一半：一个还没建库的部署。
    fresh = Settings(
        data_root=tmp_path / "untouched",
        profiles_dir=settings.profiles_dir,
        profile=settings.profile,
    )
    fresh.ensure_data_dirs()
    assert not fresh.database_path.is_file(), "前置条件：这个部署还什么都没写过"
    payload = _ok(
        _drive(
            fresh,
            tmp_path,
            [(TOOL, {}), (TOOL, {"scope": SCOPE})],
            handlers=budget_handlers(settings=fresh, ledger_factory=_factory(fresh)),
        )
    )
    assert payload["store_state"] == "no_store", payload
    # 两格都必须是 ``None`` 而不是一个空列表 / 一个全零的计数：``[]`` 会被读成
    # 「账读过了，一条都没悬着」。
    assert payload["reservations"] is None, payload
    assert payload["counts"] is None, payload
    assert "不是" in payload["note"], (
        "这一句必须把「还没有产物」与「没有悬着的额度」分开说"
    )
    assert not fresh.database_path.is_file(), (
        "一次只读查询把库建出来了：此后「还没有产物」与「这里本该有东西」在磁盘上"
        "再也分不开"
    )

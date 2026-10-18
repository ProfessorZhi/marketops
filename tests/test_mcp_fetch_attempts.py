"""§B15 的访问尝试账在**服务面**上的判据：``list_fetch_attempts``。

## 为什么要单独一个文件

``tests/test_http_fetch_attempts.py`` 已经把「每一次尝试留不留一行账、那一行写了什么」
钉了 32 条 —— 那些判据全部在抓取层，**一次都没有经过服务面**。而这一条工具的四件事
只在这条路上存在：

1. **它注册不注册**（没有落点的部署里，它必须落回 ``unbacked_message``，而不是回一句
   「没有尝试过」—— 后者的处置是去跑一次抓取，而前者是去接线）。
2. **``scope=""`` 到了工具这一层还是不是一个值。** 端口那一侧有判据（契约里那条
   ``empty_scope``），但工具这一层多一次机会把它丢掉：一个 ``scope or None`` 的写法
   就能把「不属于任何主体的那一批」变成「全部主体」，而**端口那边照样全绿**。
3. **``since`` 的时区**：工具收的是字符串，而字符串没有时区。``fromisoformat`` 会
   把 ``2026-09-27T01:00:00`` 解析出来，然后按**服务所在机器**的本地时区解释 ——
   于是「从这里往后的尝试」取决于部署在哪台机器上。
4. **它只读**：一次查询顺手建出一个库，或者改了账上的行，在这条路径上不会报错。

## 「读一次不建库」为什么在这里判

``SqliteFetchAttemptStore.__init__`` 会 ``ensure_schema``，所以一次查询在一个还没写过
任何东西的部署上顺手建出一个空库是**默认行为**。后果不是多一个文件：此后「还没有
产物」与「这里本该有东西」在磁盘上再也分不开，而一本尝试账的存在本身就是一个读数。
直接调 ``fetch_attempt_handlers`` 不会经过 ``_compose`` 的惰性工厂，那一半测不出来 ——
所以这一条必须走真的服务面。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.fetch_attempt import (
    MAX_ATTEMPTS,
    fetch_attempt_handlers,
)
from marketops.adapters.sqlite.fetch_attempt import SqliteFetchAttemptStore
from marketops.config import Settings
from marketops.domain.access import ActionTaken, SuspectedCause
from marketops.domain.fetch_attempt import BodyKind, MaterialProvenance, SettlementState
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload
from tests.test_fetch_attempt_store_contract import T0, make_attempt

#: 这一条工具的名字。写一次、引用四处 —— 与目录里那一条对不上时，这个文件里所有
#: 用例会一起红，而红的话里点名的是它，不是某一条断言。
TOOL = "list_fetch_attempts"

PAGE = "https://example.co.uk/contact"
OTHER_PAGE = "https://example.co.uk/about"

#: 一个 ``scope`` 的取值：业务主体的名字。空串那一个（「不属于任何主体」）在下面
#: 那条判据里单独摆。
SCOPE = "account-001"


def _ledger(settings: Settings) -> SqliteFetchAttemptStore:
    """盘上那本账的写入口。**按路径直接构造**，不走夹具的工厂。

    与 ``test_mcp_continuation.py::_owner_store`` 同一条理由：那个工厂是被测对象的
    一部分，用它来写行会让「工厂指向了另一个库」在回执上消失（写进去与读出来都是
    那一个错的库，两边一致，于是判据全绿）。
    """
    return SqliteFetchAttemptStore(settings.database_path)


@dataclass(frozen=True)
class _Wired:
    """一条工具与它读的那本账，与 ``_compose`` 同一接法。"""

    handlers: dict[str, Any]
    ledger: SqliteFetchAttemptStore


@pytest.fixture
def wired(settings: Settings) -> _Wired:
    #: 组合根交出来的是**工厂**（惰性），这里照同一形状传 —— 传一本建好的账会让
    #: 「组合期就把库建出来了」这件事在判据上消失。
    factory = lambda: SqliteFetchAttemptStore(settings.database_path)  # noqa: E731
    return _Wired(
        handlers=fetch_attempt_handlers(settings=settings, attempts=factory),
        ledger=SqliteFetchAttemptStore(settings.database_path),
    )


def _drive(
    settings: Settings,
    tmp_path: Path,
    calls: list[tuple[str, dict[str, Any]]],
    *,
    handlers: dict[str, Any] | None = None,
    roles: list[Role] | None = None,
    want_tool_list: bool = False,
):
    """起一个真的服务端，用给定角色的令牌调这几条工具。

    与 ``test_mcp_continuation.py::_drive`` 同形（摘要与令牌必须配成一对，配错了
    会话在握手期就失败），但那一份是那个文件的夹具，不跨文件复用。
    """
    token, digest = mint("caller-1", roles or [Role.CONTROLLER])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "caller-1", roles or [Role.CONTROLLER]))),
        encoding="utf-8",
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        attempts=handlers,
        calls=tuple(calls),
        want_tool_list=want_tool_list,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    return outcome


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    """第 ``index`` 次调用必须成功，并返回它的结构化结果。"""
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = 0) -> str:
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却回了：{call.text}"
    return call.text


def _ids(payload: dict[str, Any]) -> list[str]:
    return [row["attempt_id"] for row in payload["attempts"]]


# --- 读出来的东西是盘上那些行 ------------------------------------------------
def test_the_page_reads_this_deployments_ledger_newest_first(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """回执里的行**就是**盘上那些行，最新在前。

    这一条挡的是 ``workspace=`` 那一格被写错，以及读的是**另一个库**。两者都安静：
    一本真的没有尝试的账与一本读错了的账在回执上逐字相同（``returned: 0``），
    而它们的处置相反（去跑一次抓取 / 去查接线）。所以这一条必须有一个**非空**的库。
    """
    older = make_attempt(url=PAGE, started_at=T0)
    newer = make_attempt(url=OTHER_PAGE, started_at=T0 + timedelta(minutes=5))
    wired.ledger.put_attempt(older)
    wired.ledger.put_attempt(newer)

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    assert payload["store_state"] == "present", payload
    assert payload["workspace"] == settings.profile, payload
    assert _ids(payload) == [newer.attempt_id, older.attempt_id], (
        "顺序是**最新在前**（按开始时刻降序）—— 一条 N 的读数要答的是「最近 N 次」，"
        "把最新的一头截掉会把这句话答反"
    )
    assert payload["counts"]["returned"] == 2, payload["counts"]
    assert payload["truncation"] is None, payload
    first = payload["attempts"][0]
    assert first["url"] == OTHER_PAGE, first
    assert first["response_headers"] == {
        "retry-after": "120",
        "content-type": "text/html",
    }, "整行逐字段回，含非标量字段 —— 一个 str() 之后存进去的实现读回来是别的东西"


def test_the_derived_cells_are_computed_and_a_missing_status_stays_missing(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """四格**当场算的**读数，以及「没有观测到状态」不是 200。

    ``observed_status`` 留空 + ``body_bytes`` 非零是这本账上最容易被折错的一格：
    折成 200 让一次超时看起来像一次成功；把字节数折成 0 让「拿着了但没留下」看起来
    像「什么都没拿到」。两句话的处置相反，而这条判据同时钉住它们。
    """
    wired.ledger.put_attempt(
        make_attempt(
            observed_status=None,
            error_code="ConnectTimeout",
            challenge_detected=False,
            body_kind=BodyKind.NONE,
            body_bytes=512,
            body_sha256="",
        )
    )

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    row = payload["attempts"][0]
    assert row["observed_status"] is None, row
    assert row["status_unknown"] is True, row
    assert row["ok"] is False, "没有观测到状态的那一行的 ``ok`` 不能是 True"
    assert row["body_kept"] is False, "没有摘要就是没有留存物"
    assert row["body_bytes"] == 512, (
        "字节数**是真的**：那 512 个字节确实回来了。写成 0 会把「没有正文」与"
        "「正文被我们丢掉了」折成同一个读数"
    )
    assert row["elapsed_ms"] == 250, row
    assert "elapsed_ms" in payload["note"], (
        "回执必须说出哪几格不是账上的列 —— 否则读的人会以为账上有一列叫 elapsed_ms"
    )


def test_the_counts_name_every_cause_including_the_zeros(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """三张分表把枚举里**每一个**成员都列出来，哪怕是 0。

    这一格与「一个没人测过的指标」正相反：这里数的是一批我们真的读过、真的看得见的
    行，所以 0 是一个**读数**（这一页上没有一次挑战），而不是把「没测」说成了「零」。
    只列出出现过的那些值，会让 `by_cause` 的形状随数据变，而读的人分不出
    「没有这一类」与「这一类没被数」。
    """
    wired.ledger.put_attempt(
        make_attempt(
            url=PAGE,
            suspected_cause=SuspectedCause.WAF_CHALLENGE,
            action_taken=ActionTaken.STOPPED_METHOD,
            challenge_detected=True,
        )
    )
    wired.ledger.put_attempt(
        make_attempt(
            url=OTHER_PAGE,
            started_at=T0 + timedelta(minutes=1),
            suspected_cause=SuspectedCause.RATE_LIMITED,
            action_taken=ActionTaken.DEFERRED_BACKOFF,
            challenge_detected=False,
        )
    )

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    counts = payload["counts"]
    assert counts["returned"] == 2, counts
    assert set(counts["by_cause"]) == {cause.value for cause in SuspectedCause}, counts
    assert set(counts["by_action"]) == {action.value for action in ActionTaken}, counts
    assert set(counts["by_settlement"]) == {
        state.value for state in SettlementState
    }, counts
    assert counts["by_cause"]["waf_challenge"] == 1, counts
    assert counts["by_cause"]["rate_limited"] == 1, counts
    assert counts["by_cause"]["source_gone"] == 0, "没有的那一类要报 0，不是缺席"
    assert counts["by_settlement"]["not_applicable"] == 2, (
        "这两行都是直连抓取落下的，两行的结算都应说「这一次不走预算」"
    )
    assert counts["challenged"] == 1, counts


def test_a_suspended_charge_is_countable_and_not_inferred_from_the_rest(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**负向判据。** 悬着的那一笔必须是一个**数出来的数**，不是「比别的行少的那些」。

    §9 与 §5 要求「未知计费**先对账**」，而对账的第一步是从账上挑出悬着的那几笔。
    一个把 ``unknown_pending_reconciliation`` 读成「不与任何预留对应的行」的实现，
    在这一页上恰好答错：这一页有 ``limit``，那几行可以整批落在被截掉的一头，于是读法
    变成「没有悬着的账」—— 而那是这一格最不该撒的谎。

    这一条同时是 ``SettlementState.UNKNOWN_PENDING_RECONCILIATION`` 的**第一个消费者**：
    在此之前那个成员只有定义与契约描述，没有任何一条路径读得出来。它经过真的写入、
    真的读回、真的计数 —— 一条形状上说得通的行（模型要求它指得出是哪一份预留）
    在服务面上数得到。
    """
    wired.ledger.put_attempt(
        make_attempt(
            url=PAGE,
            budget_settlement=SettlementState.UNKNOWN_PENDING_RECONCILIATION,
            budget_reservation_id="9f2c1e0b8a7d6c5f4e3b2a1908f7e6d5",
        )
    )
    wired.ledger.put_attempt(make_attempt(url=OTHER_PAGE, started_at=T0 + timedelta(minutes=1)))

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    counts = payload["counts"]
    assert counts["by_settlement"]["unknown_pending_reconciliation"] == 1, counts
    assert counts["by_settlement"]["not_applicable"] == 1, (
        "另一行是普通的直连抓取：两类都数出来，才叫分得开"
    )
    assert counts["by_settlement"]["reserved"] == 0, "没有的那一类要报 0，不是缺席"

    # 悬着的那一笔在行上答得出「拿哪一笔去核」—— 对账从这里开始。
    suspended = [row for row in payload["attempts"] if row["budget_settlement"] == "unknown_pending_reconciliation"]
    assert len(suspended) == 1, payload["attempts"]
    assert suspended[0]["budget_reservation_id"], "说不出去核哪一笔的行不该落得进账本"


def test_both_kinds_of_material_are_counted_on_the_page(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """**这一页要分得开现场与自造。** §8.C.2 逐字要求两者都被标注。

    这一本账把「抓一个真实的第三方公开页面」与「在一个受控夹具或本机靶子上跑一遍」
    记在一起，而两种行在回执上同形。``counts.by_material`` 是它们在服务面上唯一
    分得开的地方 —— 没有它，「抓了 200 次、成功 180 次」里有多少次**根本没出过网**
    没有一个地方答得出来，而 §8.C.2 的下一句正是「一份受控夹具不能算作市场产出」。

    这一条同时是 ``MaterialProvenance.SYNTHETIC`` 的**第一个消费者**：它在此之前只
    有定义、缺省与一份契约描述，没有任何一条路径**读**得出来。与 ``by_settlement``
    那两条同形：枚举里每一个成员都列出来，没有的那一类报 0 而不是缺席 ——
    ``synthetic: 0`` 是一句「这一页上的每一行都出过网」，可以被质疑、可以被对账；
    只列出现过的值则会让那句话变成一个没人算过的数。
    """
    wired.ledger.put_attempt(make_attempt(url=PAGE, material=MaterialProvenance.LIVE))
    wired.ledger.put_attempt(
        make_attempt(
            url=OTHER_PAGE,
            started_at=T0 + timedelta(minutes=1),
            material=MaterialProvenance.SYNTHETIC,
        )
    )

    payload = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    counts = payload["counts"]
    assert set(counts["by_material"]) == {
        provenance.value for provenance in MaterialProvenance
    }, counts
    assert counts["by_material"]["live"] == 1, counts
    assert counts["by_material"]["synthetic"] == 1, (
        "受控夹具那一行没有被数成自造 —— 它会被读成一次现场覆盖"
    )

    # 逐行的这一格也要回：计数说得出一页上有几行，说不出**是哪几行**。
    by_url = {row["url"]: row["material"] for row in payload["attempts"]}
    assert by_url == {PAGE: "live", OTHER_PAGE: "synthetic"}, by_url


# --- 三个筛子 ----------------------------------------------------------------
def test_an_empty_scope_is_a_filter_and_not_a_wildcard(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``scope=""`` 与**没给** ``scope`` 是两件事，而这一条钉在**工具**这一层。

    端口那一侧有判据（契约里那条 ``empty_scope``），而工具这一层多一次把它丢掉的
    机会：``scope or None`` 这个写法在端口那边**照样全绿**，它只在这一层把
    「不属于任何主体的一批」变成「全部主体」。两种读法的处置不同 —— 前者是
    「这次运行没抓到什么」，后者会把它读成「很多抓取不属于任何主体」。

    两半都要判：空串是一个筛子，而**缺省是真的不筛**。
    """
    owned = make_attempt(scope=SCOPE, url=PAGE)
    unowned = make_attempt(scope="", url=OTHER_PAGE)
    wired.ledger.put_attempt(owned)
    wired.ledger.put_attempt(unowned)

    filtered = _ok(
        _drive(settings, tmp_path, [(TOOL, {"scope": ""})], handlers=wired.handlers)
    )
    assert _ids(filtered) == [unowned.attempt_id], (
        "``scope=\"\"`` 必须是「筛那个不属于任何主体的那一批」，不是「不筛」"
    )
    assert filtered["filters"]["scope"] == "", filtered["filters"]

    unfiltered = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))
    assert sorted(_ids(unfiltered)) == sorted([owned.attempt_id, unowned.attempt_id]), (
        "没给 scope 就是**不筛** —— 与上面那一格不是同一件事"
    )
    assert unfiltered["filters"]["scope"] is None, unfiltered["filters"]


def test_the_url_filter_asks_about_the_address_we_requested(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``url`` 筛的是**问过的那个地址**，不是重定向之后的落点。

    ``make_attempt`` 的两格刻意不同（``url`` 是 ``PAGE``、``final_url`` 是
    ``PAGE + "/"``），所以这条判据能分开三种实现：筛错那一格、两格一起筛、
    对两格都不筛（那会回两行）。排查问的是「我们问过这个 URL 吗」—— 而落点那个
    问题的答案要按别的身份去找。

    ## 筛空的结果是 ``present`` + 空列表

    不是 ``no_store``：库在、读过了、这个地址没有被问过。这两句话的处置相反
    （去接线 / 去问一次），所以下面两格都判。
    """
    wired.ledger.put_attempt(make_attempt(url=PAGE))

    asked = _ok(_drive(settings, tmp_path, [(TOOL, {"url": PAGE})], handlers=wired.handlers))
    assert len(asked["attempts"]) == 1, asked
    assert asked["store_state"] == "present", asked

    never = _ok(
        _drive(settings, tmp_path, [(TOOL, {"url": f"{PAGE}/"})], handlers=wired.handlers)
    )
    assert never["attempts"] == [], (
        "落点那一格被当成筛子的刻度了 —— 它答的是另一个问题"
    )
    assert never["store_state"] == "present", "库在、读过了，只是这个地址没被问过"
    assert never["counts"]["returned"] == 0, never["counts"]


def test_since_is_compared_in_utc_and_refuses_a_timeless_value(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``since`` 按**绝对时刻**比，且不带时区一律拒。

    ## 为什么 +08:00 那个例子是承重的

    行上的时刻是 ``2026-09-27T01:00:00Z``，而下面给的 ``since`` 是
    ``2026-09-27T02:00:00+08:00``（＝ 前一天的 18:00Z）。按字符串比（或者不归一化
    就丢给 SQL）的实现会把它读成「比 01:00 更晚」，于是明明在此之后的这一行被筛掉
    —— 而报告上只是「这里没有尝试」，看起来完全正常。
    """
    row = make_attempt(started_at=T0)  # 2026-09-27T01:00:00Z
    wired.ledger.put_attempt(row)

    earlier = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, {"since": "2026-09-27T02:00:00+08:00"})],
            handlers=wired.handlers,
        )
    )
    assert _ids(earlier) == [row.attempt_id], (
        "这一行在 18:00Z 之后 —— 按字符串比的实现会说它不在"
    )
    assert earlier["filters"]["since"] == "2026-09-26T18:00:00+00:00", (
        "回执报出的应用值必须是 UTC —— 报回原串的话，读的人没法核「筛的是哪一段」"
    )

    later = _ok(
        _drive(
            settings,
            tmp_path,
            [(TOOL, {"since": "2026-09-27T12:00:00+08:00"})],
            handlers=wired.handlers,
        )
    )
    assert later["attempts"] == [], "04:00Z 之后不该有这一行"

    text = _refused(
        _drive(
            settings,
            tmp_path,
            [(TOOL, {"since": "2026-09-27T01:00:00"})],
            handlers=wired.handlers,
        )
    )
    assert "时区" in text, text
    # 拒绝要**点名是哪个入参**。``"错误" in text`` 这一类的断言在这里不够用：
    # 服务面把每一条失败都包成 ``Error executing tool <名字>: …``，所以工具名
    # 一定在文本里，而那句话对「入参不对」与「工具不存在」是同一句。
    assert "since" in text, f"拒绝没有点名 since，读的人不知道该改哪一格：{text}"


def test_a_limit_drops_the_oldest_and_says_so(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """``limit`` 截的是**最旧的那一头**，而截断必须被报出来。

    一份静默变短的账读起来像「就这几次」—— 而「最近两次都失败」与「最近二十次里
    有两次失败」是两句结论相反的话。所以截断与否要能读出来，缺省也要看得到。
    """
    rows = [
        make_attempt(url=PAGE, started_at=T0),
        make_attempt(url=OTHER_PAGE, started_at=T0 + timedelta(minutes=1)),
        make_attempt(url=f"{PAGE}?page=2", started_at=T0 + timedelta(minutes=2)),
    ]
    for row in rows:
        wired.ledger.put_attempt(row)

    cut = _ok(
        _drive(settings, tmp_path, [(TOOL, {"limit": 2})], handlers=wired.handlers)
    )
    assert _ids(cut) == [rows[2].attempt_id, rows[1].attempt_id], (
        "被丢下的是**最早**的那一次"
    )
    assert cut["counts"]["returned"] == 2, (
        "``counts`` 数的是**回出去的这一页**。数成「多要的那一行」或整本账的话，"
        "截断之后的每一个计数都会比 ``len(attempts)`` 大 —— 而读的人拿这两格对不上时，"
        "会以为是自己数错了"
    )
    assert cut["truncation"] is not None, cut
    assert cut["truncation"]["limit"] == 2, cut["truncation"]

    everything = _ok(
        _drive(settings, tmp_path, [(TOOL, {"limit": None})], handlers=wired.handlers)
    )
    assert len(everything["attempts"]) == 3, everything
    assert everything["truncation"] is None, everything

    default = _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))
    assert len(default["attempts"]) == 3, "缺省上限比这多，三行一条都不该被截"
    assert default["filters"]["limit"] == MAX_ATTEMPTS, default["filters"]

    text = _refused(
        _drive(settings, tmp_path, [(TOOL, {"limit": 0})], handlers=wired.handlers)
    )
    assert "limit" in text, text


# --- 注册、角色、只读 ---------------------------------------------------------
def test_a_deployment_without_the_wiring_says_so_instead_of_nothing_was_tried(
    settings: Settings, tmp_path: Path
) -> None:
    """没接线的实例必须报「尚未接线」，**不是**报一句「没有尝试过」。

    这两句话的处置相反：前者是去把 ``fetch_attempt_handlers`` 接上，后者是去跑一次
    抓取。而它们在报告上长得一样 —— 一条恒回空列表的替身会让一个没接线的部署看起来
    像一个安静地什么都没试过的市场。

    同时钉住另一半：这条工具**仍在该实例的 ``tools/list`` 上**。目录是目录，注册是
    注册 —— 与 ``review`` / ``continuation`` 那几条逐字同一条区分。
    """
    outcome = _drive(settings, tmp_path, [(TOOL, {})], handlers=None, want_tool_list=True)
    text = _refused(outcome)
    assert "尚未接线" in text, text
    assert "没有尝试过" not in text, "没接线被答成了业务结论"
    assert TOOL in outcome.tool_names, (
        "目录里的工具应当在 tools/list 上 —— 不在的话这一条用例测的就不是"
        "「没接线」，而是「不在这个构建里」"
    )


def test_the_factory_missing_un_registers_the_tool(settings: Settings) -> None:
    """工厂缺了 ⇒ **这一条不注册**（空表），而不是注册成恒回空列表的替身。

    这一条在服务面上**测不出来**：``build_server`` 把 ``{}`` 与 ``None`` 一视同仁
    （都落回 stub）。所以判据只能落在工厂这一层 —— 而它必须有人守。
    """
    assert fetch_attempt_handlers(settings=settings, attempts=None) == {}
    complete = fetch_attempt_handlers(
        settings=settings, attempts=lambda: SqliteFetchAttemptStore(settings.database_path)
    )
    assert set(complete) == {TOOL}, complete


def test_a_research_identity_cannot_read_the_ledger(
    settings: Settings, tmp_path: Path, wired: _Wired
) -> None:
    """研究身份读不到这本账：它**跨主体**。

    同一个 URL 上的尝试会被记在不同 ``scope`` 名下，而 ``scope`` 就是业务主体的
    名字。研究侧本来就在自己那一跳的回执上拿到了诊断，它不需要账 —— 而账给它，
    等于让一次研究看见别的账号在问什么。

    这一条**必须接线之后才作数** —— 没接线时它虽然照旧不可见，但不可见的理由变成了
    「没接线」，而那条路径与「角色不对」是两件事。
    """
    wired.ledger.put_attempt(make_attempt())
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

    前半：两次读之间账上那些行必须逐格相同（这本账没有 ``update``/``delete``，
    所以「改了」只可能是一次意外的 ``put``）。
    后半：库文件不存在时一次查询顺手建出一个空库是**默认行为**（构造函数会
    ``ensure_schema``），而后果不是多一个文件 —— 此后「还没有产物」与「这里本该有
    东西」在磁盘上再也分不开，而一本尝试账的存在本身就是一个读数。
    """
    before = [make_attempt(url=PAGE)]
    wired.ledger.put_attempt(before[0])
    snapshot = [
        (row.attempt_id, row.observed_status) for row in wired.ledger.list_attempts()
    ]

    for _ in range(2):
        _ok(_drive(settings, tmp_path, [(TOOL, {})], handlers=wired.handlers))

    after = [
        (row.attempt_id, row.observed_status) for row in wired.ledger.list_attempts()
    ]
    assert after == snapshot, "这一页写账了（或者顺手记了一次它自己的访问）"

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
            [(TOOL, {}), (TOOL, {"scope": "account-001"}), (TOOL, {"limit": 5})],
            handlers=fetch_attempt_handlers(
                settings=fresh,
                attempts=lambda: SqliteFetchAttemptStore(fresh.database_path),
            ),
        )
    )
    assert payload["store_state"] == "no_store", payload
    # 两格都必须是 ``None`` 而不是一个空列表 / 一个全零的计数：``[]`` 会被读成
    # 「账读过了，一次都没有」。
    assert payload["attempts"] is None, payload
    assert payload["counts"] is None, payload
    assert "不是" in payload["note"], (
        "这一句必须把「还没有产物」与「没有尝试过」分开说"
    )
    assert not fresh.database_path.is_file(), (
        "一次只读查询把库建出来了：此后「还没有产物」与「这里本该有东西」在磁盘上"
        "再也分不开"
    )

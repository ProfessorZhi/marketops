"""§6.3 的**读**那一半：拒收分布与逐条回执。

## 这一份文件要挡住的那件事

落盘的收据要有人按账号读回来才算数。这条链上有两个各自看起来都对的失败方式：

* **只写不读** —— 一个没有读者的表，与「算出来就丢掉」在报告上完全同形，而它更糟：
  它看起来像已经处理过了。所以每一条判据都从 ``list_route_rejections`` 读回来，
  没有一条是拿内存里的列表数一遍。
* **按类别漏一列 0** —— ``by_code`` 是**闭集全列**的。少列一个 0 与「这一类真的没
  发生过」在报告上长得一样，而消失之后它看起来像从没发生过。所以这一条用
  ``set(by_code) == 全集`` 断言，而不是断言某几格的数字。

分布与具体项**两件都要**（§6.3 逐字）。它们不是「摘要与详情」的关系：「这一页没有
联系人」（正常）与「域名判错了」（要修）在只有分布时是同一个数字，而它们的下一步
相反；只有具体项时，一份几千行的回执里看不出哪一类在涨。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from marketops.adapters.jsonl.store import LocalEvidenceStore
from marketops.adapters.memory import InMemoryEvidenceStore
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.application.route_rejections import RejectionTally, history, tally
from marketops.domain.errors import EvidenceError
from marketops.domain.route_records import RouteRejectionCode
from tests.test_evidence_store_contract import FETCHED_AT, WORKSPACE, make_rejection

#: 三个实现各一个构造式。JSONL 与 SQLite 要一份 ``Settings``（它们落盘），内存替身
#: **刻意不要** —— 一个测试替身不该要求一份部署配置（见它自己的 docstring）。
STORES = {
    "jsonl": lambda settings: LocalEvidenceStore(settings),
    "sqlite": lambda settings: SqliteEvidenceStore(settings.database_path),
    "memory": lambda settings: InMemoryEvidenceStore(),
}


@pytest.fixture(params=sorted(STORES))
def store(request, settings):
    """三个实现之一 —— 与契约那一份同一个理由：红了的时候一眼看得见是哪一个。"""
    return STORES[request.param](settings)


def _receipt(**overrides):
    return make_rejection("example-coatings", **overrides)


# --- 空集合：是一张分布，不是一个 None ----------------------------------------


def test_a_closed_set_is_listed_in_full_even_at_zero() -> None:
    """每一类都记 0 —— 包括一条都没发生过的那些。

    这是 ``by_code`` 与「一本字典」的区别：字典的键由**发生过什么**决定，于是
    「这一类还没发生过」与「这一类没有在生产端接线」在报告里长得一样。而后者是本仓
    要能一眼看出来的事（一个枚举成员没有生产者，是一处缺口，不是一个 0）。
    """
    empty = tally([])

    assert empty.total == 0
    assert set(empty.by_code) == {code.value for code in RouteRejectionCode}
    assert set(empty.by_code.values()) == {0}
    assert empty.items == ()
    assert empty.by_run == ()
    assert empty.truncated is False


def test_an_account_nobody_ever_rejected_says_zero_not_none(store) -> None:
    """「这个账号从来没被拒过」与「这一格没有算过」是两句不同的话。

    后者只在调用方压根没读的地方出现（``None``），而一次**读过的**、没有结果的查询
    必须说 0 —— 否则报告里一个空账号与一个没跑过的账号同形。
    """
    result = history(store, "example-coatings")

    assert result.total == 0
    assert result.by_code["foreign_domain"] == 0


# --- 分布与具体项 -------------------------------------------------------------


def test_the_distribution_and_the_receipts_both_come_back(store) -> None:
    """§6.3 逐字要的两件：**分布**（每一类各多少）与**具体项**（是哪几条）。

    所以这里两个方向各断言一次：每一类的数字对得上逐条的条数，而逐条里留着的是
    候选本身（不是一句摘要）—— 报告要能回答「是哪一条候选被谁拒了、为什么」。

    ``foreign_domain`` 那两条也一并钉住计数与明细的同源性：分布是从**同一批**逐条
    数出来的（一次 :func:`tally`），不是两处各数一遍。
    """
    store.put_route_rejection(_receipt(code=RouteRejectionCode.FOREIGN_DOMAIN))
    store.put_route_rejection(
        _receipt(
            code=RouteRejectionCode.FOREIGN_DOMAIN,
            candidate="other@elsewhere.co.uk",
        )
    )
    store.put_route_rejection(
        _receipt(code=RouteRejectionCode.UNBOUND_EMAIL, candidate="grounds@example.co.uk")
    )

    result = history(store, "example-coatings")

    assert result.total == 3
    assert result.by_code["foreign_domain"] == 2
    assert result.by_code["unbound_email"] == 1
    assert result.by_code["entry_elsewhere"] == 0
    assert sorted(item.candidate for item in result.items) == [
        "grounds@example.co.uk",
        "other@elsewhere.co.uk",
        "someone.else@example.co.uk",
    ]
    assert {item.code for item in result.items} == {
        RouteRejectionCode.FOREIGN_DOMAIN,
        RouteRejectionCode.UNBOUND_EMAIL,
    }


def test_the_two_distributions_answer_two_different_questions(store) -> None:
    """``by_code`` 回答「是哪一类」，``by_run`` 回答「是哪一趟」。

    第二格存在的理由是一件运维上分得开、而报告上分不开的事：**一次性的与每一趟都在
    发生的**。同一类拒收三十条来自同一趟（那一页特别脏）与来自三十趟（域名归属判据
    坏了）处置完全不同 —— 而 ``by_code`` 那一格两者一模一样。
    """
    for index in range(3):
        store.put_route_rejection(_receipt(run_id="run-1", candidate=f"a{index}@elsewhere.co.uk"))
    store.put_route_rejection(_receipt(run_id="run-2", candidate="b@elsewhere.co.uk"))

    result = history(store, "example-coatings")

    assert result.by_code["entry_elsewhere"] == 4
    assert result.by_run == (("run-1", 3), ("run-2", 1))


def test_the_run_distribution_has_one_total_order(store) -> None:
    """同条数时按 ``run_id`` 升序 —— 两份内容相同的分布必须排成一个样子。

    没有这条次序的话，两份「内容一样」的导出会因为字典插入顺序不同而排成两个样子，
    而那种差异最后总会被读成「有人改过东西」。
    """
    pairs = [("run-b", 2), ("run-a", 2), ("run-c", 5)]
    for run_id, count in pairs:
        for index in range(count):
            store.put_route_rejection(
                _receipt(run_id=run_id, candidate=f"{run_id}-{index}@elsewhere.co.uk")
            )

    result = history(store, "example-coatings")

    assert result.by_run == (("run-c", 5), ("run-a", 2), ("run-b", 2))


def test_the_receipts_are_ordered_by_time_then_id(store) -> None:
    """逐条按 ``(occurred_at, rejection_id)`` —— 与存储读回来**逐字同一个**次序。

    这条不是洁癖：一个按到达顺序（也就是字典顺序）排列的报告，会让同一份库在两次
    导出里是两段不同的 JSON，而那种差异最后总会被当成「有人改过东西」。
    """
    for index in range(3):
        store.put_route_rejection(
            _receipt(occurred_at=FETCHED_AT + timedelta(hours=index), candidate=f"c{index}@x.co.uk")
        )

    result = history(store, "example-coatings")

    assert [item.occurred_at for item in result.items] == sorted(
        item.occurred_at for item in result.items
    )
    assert [item.occurred_at for item in result.items] == [
        item.occurred_at for item in store.list_route_rejections("example-coatings")
    ]


# --- 截断必须说出来 -----------------------------------------------------------


def test_a_truncated_tally_says_so(store) -> None:
    """「只返回了前 20 条」与「一共就 20 条」是一句话的两种读法，报告里不能含糊。

    截断是**调用方**钉的上限（MCP 面的响应大小），不是这一层的事实：这一层说得出
    「一共有多少条」，钉多少条出去是接口的事。所以 ``total`` 永远是全集的大小，
    与 ``limit`` 无关。
    """
    for index in range(5):
        store.put_route_rejection(_receipt(candidate=f"d{index}@x.co.uk"))

    truncated = history(store, "example-coatings", limit=2)
    whole = history(store, "example-coatings")

    assert truncated.total == 5, "总数是全集的，不是返回的那几条"
    assert len(truncated.items) == 2
    assert truncated.truncated is True
    assert whole.truncated is False
    assert len(whole.items) == 5


def test_a_limit_that_does_not_bite_is_not_a_truncation(store) -> None:
    """上限恰好等于条数时**不是**截断 —— 一个越界的判断会让报告说「还有更多」，
    而其实一条都没剩下。"""
    for index in range(3):
        store.put_route_rejection(_receipt(candidate=f"e{index}@x.co.uk"))

    assert history(store, "example-coatings", limit=3).truncated is False


# --- 归属：这一层不重新判一次账号 ---------------------------------------------


def test_the_tally_counts_only_this_account(store) -> None:
    """按账号读回来 —— 另一个账号的收据不进这张分布。

    归属判据是**存储**那一侧的（三个实现各自的 ``owned``），这里不重新筛一遍：一个
    在本层再筛一次账号的实现，会让「存储按绑定拒掉了别的工作区的行」与「这里筛掉了」
    在报告上同形。
    """
    store.put_route_rejection(_receipt())
    store.put_route_rejection(make_rejection("another-coatings"))

    assert history(store, "example-coatings").total == 1
    assert history(store, "another-coatings").total == 1
    assert history(store, "nobody").total == 0


def test_a_tally_is_a_value_not_a_view(store) -> None:
    """分类结果是一份**不可变**的值：调用方改不动它，写下去之后也不会跟着变。

    ``by_code`` 与 ``items`` 都是元组/只读映射 —— 一个可以被调用方改掉的分布，
    会在两个读者之间被就地改写，而报告里那条差异看起来像「有人改过库」。
    """
    store.put_route_rejection(_receipt())
    result = history(store, "example-coatings")

    assert isinstance(result, RejectionTally)
    with pytest.raises(TypeError):
        result.by_code["foreign_domain"] = 99  # type: ignore[index]
    assert isinstance(result.items, tuple)
    assert isinstance(result.by_run, tuple)


def test_the_tally_of_a_batch_is_the_tally_of_its_store_read(store) -> None:
    """``tally``（拿着内存里那一批）与 ``history``（对着库读）算出**同一个**分布。

    这一条是「两处各数一遍」的执行器：``submit_findings`` 手上那一批拒收**还没有
    落盘读过一遍**（它们就是这一趟刚生成的），却要报同一张分布。两份实现只要有一处
    忘了补新枚举成员，那一类就会从其中一份报告里静默消失。
    """
    for index in range(4):
        store.put_route_rejection(
            _receipt(
                code=RouteRejectionCode.FOREIGN_DOMAIN
                if index % 2
                else RouteRejectionCode.ENTRY_ELSEWHERE,
                candidate=f"f{index}@x.co.uk",
                run_id=f"run-{index % 3}",
            )
        )

    stored = store.list_route_rejections("example-coatings")

    assert tally(stored) == history(store, "example-coatings")


def test_a_foreign_workspace_is_refused_and_never_enters_the_distribution(store) -> None:
    """跨工作区写进来 → 存储拒绝，而**它不计进**本账号的分布。

    越权写在报告里最坏的样子不是报错，是**成功**：一条别人的收据混进本账号的分布，
    而它看起来只是「这个账号被拒得更多」。

    先写一条本工作区的：三个实现都靠**第一次写入**建立绑定（JSONL 那一份比的是部署
    配置，SQLite 与内存替身是「第一行写进来的那个工作区」），所以没有这一步的话，
    那条别人的收据反而会成为绑定 —— 空库上的第一次写入永远是被允许的。
    """
    store.put_route_rejection(_receipt())

    with pytest.raises(EvidenceError):
        store.put_route_rejection(_receipt(workspace="another-scene", candidate="x@y.co.uk"))

    result = history(store, "example-coatings")
    assert result.total == 1
    assert _receipt().workspace == WORKSPACE, "上面那句「另一个工作区」要真的不是它"

"""``FetchAttemptStore`` 契约：**同一组判据，对着两个实现各跑一遍**。

## 它守的失败是「一次没抓到的抓取什么都不留下」

采集设计 §5 要求每一次尝试都留一行账，而这一条最容易的失守方式不是某一处代码写错，
是**这一行根本没写**：一次 403、一次挑战页、一次地址判据拦下都**产不出一条证据**
（证据表按内容寻址，而它们没有内容），于是那份「那天到底问过什么」的记录恰好缺了
最需要它的那一批。本文件判的是**存储这一端**忠不忠实 —— 生产者那一端在
``tests/test_http_fetch_attempts.py``。

## 三条判据是这一份独有的一段：**读侧不许把「别人的」折成「没有的」**

* ``get_attempt`` 的 ``None`` 只有一个意思：没有这个 id 的行。一行属于别的工作区时
  **报错**，不返回 ``None``（与 ``ports/pool.py`` 的三份实现逐字同一条）；
* ``list_attempts`` 同理 —— 少挡一个入口就是一条绕行路；
* 结构面：``FetchAttemptStore`` 的抽象方法**只有那三个**。这一条是「这条记录只增
  不改」在接口层面的执行器 —— 加一个 ``prune`` 或 ``update`` 不会让任何一条已有
  用例转红，它只会让下一个人顺手用上它，而一条能被改写的访问记录在最需要它的场合
  （有人问「为什么那天什么都没抓到」）恰好是最不可信的。

## 空串是一个值，``None`` 是「不筛」

``scope=""`` 说的是「不属于任何主体的那些尝试」，``scope=None`` 说的是「不筛」。
把它们合成一个值之后，「这次运行什么都没抓到」与「不属于任何主体的抓取非常多」在
报告上同形。三个筛子（``scope`` / ``url`` / ``since``）都是这个写法，而下面有一条
**带对照组的**判据专门钉住它 —— 一条「无论传什么筛出来都是全部」的实现会让相反的
判据照样通过。

## 为什么两个实现

``SqliteFetchAttemptStore``（主存、跨进程）与 ``InMemoryFetchAttemptStore``（测试替身）。
它在这里通过而在 SQLite 那里失败，才是「这是一个契约」有意义的样子（B3 立的判据：
端口在第二个实现出现时才提取，这里正好两个）。

## 两条判据**刻意不在这里**（与 ``test_budget_contract.py`` 同一条处置）

1. **「写完重开一个 store 还能读到」**只对持久化实现成立，那条在
   ``tests/test_sqlite_fetch_attempt.py``。
2. **并发**：写进共享契约就得给内存那份加锁 —— 那不是补齐契约，是把一个测试替身
   伪装成并发存储。真正守得住靠的是 ``BEGIN IMMEDIATE``，那是 SQLite 的性质。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryFetchAttemptStore
from marketops.adapters.sqlite.fetch_attempt import SqliteFetchAttemptStore
from marketops.adapters.sqlite.payload import dump_payload
from marketops.domain.fetch_attempt import (
    BodyKind,
    FetchAttempt,
    MaterialProvenance,
    MethodKind,
    SettlementState,
    attempt_id,
)
from marketops.domain.errors import StoreError
from marketops.ports.fetch_attempt import FetchAttemptStore

UTC = timezone.utc
T0 = datetime(2026, 9, 27, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
OTHER_WORKSPACE = "uk-something-else"

PAGE = "https://example.co.uk/contact"

#: 契约判据要跑过哪些实现。
IMPLEMENTATION_PARAMS = ("sqlite", "memory")


def test_the_contract_runs_against_every_implementation() -> None:
    """实现清单缩水时「两个实现都通过」会变成「一个实现通过」。

    ``params`` 是一个可以被删掉的名字：删掉 ``memory`` 之后整份契约照样全绿，
    而「这是一个契约」这句话就没有依据了 —— 与
    ``tests/test_budget_contract.py`` 里那条同一个道理。
    """
    assert set(IMPLEMENTATION_PARAMS) == {"sqlite", "memory"}


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> FetchAttemptStore:
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteFetchAttemptStore(tmp_path / "marketops.db")
    return InMemoryFetchAttemptStore()


def make_attempt(**overrides) -> FetchAttempt:  # noqa: ANN003 - 测试构造器
    """一条形状正常的尝试记录。**每一格都填上**，好让「写进去又读回来」能逐格比。

    五格刻意填上非空值：``final_url``（与 ``url`` 不同）、``redirect_trace``、
    ``observed_status``、``retry_after_s``、``challenge_detected``。它们的缺省值恰好是
    「什么都没有」，而一个把某个字段**漏掉不存**的实现会让缺省值那几条判据全绿 ——
    ``observed_status`` 尤其如此（``None`` 是合法缺省，谁也不会发现它没写进去）。
    第六格 ``material`` 取的是**非缺省**的那一个成员（缺省是 ``SYNTHETIC``），
    理由与它们同一条。

    ## ``attempt_id`` 是**按生效后的字段**算的，而不是按缺省那几格

    第一版把它写成 ``overrides`` 之前的一个常量，于是「换个 ``url`` 再写一条」会得到
    **同一个 id** —— 六条判据当场红在一件与存储无关的事上（替身自己的 bug），而报错
    读起来像「这个存储不幂等」。派生之后 ``make_attempt(url=...)`` 是一条**真的**另一次
    尝试。显式传 ``attempt_id=`` 仍然优先（有一条判据要摆出「同一个 id、两套内容」）。
    """
    payload = {
        "scope": "account-001",
        "workspace": WORKSPACE,
        "method": MethodKind.HTTP,
        "url": PAGE,
        "final_url": f"{PAGE}/",
        "redirect_trace": (PAGE, f"{PAGE}/"),
        "started_at": T0,
        "ended_at": T0 + timedelta(milliseconds=250),
        "observed_status": 403,
        "response_headers": {"retry-after": "120", "content-type": "text/html"},
        "error_code": "Forbidden",
        "body_kind": BodyKind.NONE,
        "body_bytes": 512,
        # 这一格刻意取**非缺省**的那一个成员：缺省是 ``SYNTHETIC``，所以一条只带
        # 缺省的行在「存进去又读回来」里证明不了这一格真的被存住了 —— 它与一个
        # 把这一格整列丢掉的实现逐字同形（``observed_status`` 那一格受的同一条罪，
        # 见上面那段）。
        "material": MaterialProvenance.LIVE,
        "retry_after_s": 120,
        "challenge_detected": True,
        "cause_evidence_ref": "ev-1",
    }
    payload.update(overrides)
    payload.setdefault(
        "attempt_id",
        attempt_id(
            workspace=payload["workspace"],
            scope=payload["scope"],
            method=payload["method"],
            url=payload["url"],
            started_at=payload["started_at"],
        ),
    )
    return FetchAttempt.model_validate(payload)


def _smuggle_foreign_row(store: FetchAttemptStore, row: FetchAttempt) -> None:
    """把**别人的**一行放进这个存储的盘上，绕过它自己的写路径。

    两个实现只有**往哪放**的差别：SQLite 是直接写那一行（写它的那个进程没有我们的
    代码），内存实现是把手里的 model 塞进它的字典。用 ``INSERT OR REPLACE`` 而不是
    ``INSERT``：要摆出的状态是「**同一个主键**的那一行写着别人的工作区」，而那个键
    已经被本库自己写过一行了。
    """
    payload = dump_payload(row.model_dump(mode="json"))
    if isinstance(store, SqliteFetchAttemptStore):
        conn = sqlite3.connect(store._path)  # noqa: SLF001 - 见上面那段说明
        try:
            conn.execute(
                "INSERT OR REPLACE INTO fetch_attempt "
                "(attempt_id, workspace, scope, url, started_at, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row.attempt_id,
                    row.workspace,
                    row.scope,
                    row.url,
                    row.started_at.isoformat(),
                    payload,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return
    store._attempts[row.attempt_id] = row  # type: ignore[attr-defined]  # noqa: SLF001


# ----------------------------------------------------------------------
# 写：一行只写一次，而写不进去要响
# ----------------------------------------------------------------------
def test_the_port_has_no_way_to_change_or_remove_a_row() -> None:
    """★ 这一条是「只增不改」在**结构层面**的执行器。

    数据面那几条挡不住「多一个入口」：加一个 ``prune`` 不会让任何一条已写下的判据
    转红，它只会让下一个人顺手用上它。而这条记录是事后回答「这个 URL 那天到底怎么样
    了」的唯一凭据 —— 要清账就换一个库（``db.py::bind_workspace`` 对「换工作区」的
    处置就是同一句话）。

    判据直接从类上取（``__abstractmethods__``），所以它**不需要**人抄一份方法名清单
    —— 抄下来的那份清单不会因为接口长了新方法而变红，而这一条会。
    """
    assert FetchAttemptStore.__abstractmethods__ == {
        "put_attempt",
        "get_attempt",
        "list_attempts",
    }, (
        "访问尝试账的端口上多了一个动词。它是一个**只增不改**的记录：一个新的写动词"
        "请先回答「有人事后问『那天为什么什么都没抓到』时，这条记录还可不可信」。"
    )


def test_a_written_attempt_comes_back_whole(store: FetchAttemptStore) -> None:
    """**逐格**比，而不是比两个对象的 ``==``。

    比 ``==`` 会通过一个「只存了 id、读回来时现编一份缺省」的实现吗？不会 —— 但那条
    失败的现场只有一句 ``assert ... == ...``，而这里逐格报得出**是哪一格**没存住。
    这一张表上值得知道「是哪一格」，因为每一格救的是不同的排查问题（``observed_status``
    答「服务端说了什么」、``retry_after_s`` 答「它要我们等多久」、``response_headers``
    答「它有没有说别的」）。
    """
    row = make_attempt()
    store.put_attempt(row)

    back = store.get_attempt(row.attempt_id)
    assert back is not None
    assert back.attempt_id == row.attempt_id
    assert back.scope == "account-001", "空串会合法，所以这一格要非空才算测到"
    assert back.workspace == WORKSPACE
    assert back.url == PAGE
    assert back.final_url == f"{PAGE}/"
    assert back.redirect_trace == (PAGE, f"{PAGE}/")
    assert back.method is MethodKind.HTTP
    assert back.started_at == T0
    assert back.ended_at == T0 + timedelta(milliseconds=250)
    assert back.observed_status == 403, "状态码是这一行最常被读的一格"
    assert back.response_headers == {"retry-after": "120", "content-type": "text/html"}
    assert back.error_code == "Forbidden"
    assert back.body_bytes == 512
    assert back.retry_after_s == 120
    assert back.challenge_detected is True
    assert back.cause_evidence_ref == "ev-1"
    assert back.material is MaterialProvenance.LIVE, (
        "这一格是 §11 唯一一行**直接要求标注**的那一条（MCP_SERVICE §8.C.2）—— "
        "它读不回来时，报告上的「现场覆盖」会由缺省值代答"
    )
    assert back == row, "读回来的是同一个值，不是一个「看起来差不多」的新对象"


def test_writing_the_same_attempt_twice_is_idempotent(store: FetchAttemptStore) -> None:
    """同一次尝试被提交两遍是正常的：一次重放、一次重试的收尾、一条消息被消费两次。"""
    row = make_attempt()
    store.put_attempt(row)
    store.put_attempt(row)

    assert [found.attempt_id for found in store.list_attempts()] == [row.attempt_id], (
        "同一行写了两次却多出一行 —— 报告上「这个 URL 被问了两次」会变成一句真话，"
        "而它其实只被问过一次"
    )


def test_the_same_id_with_different_content_is_a_conflict(store: FetchAttemptStore) -> None:
    """两套内容只可能意味着两个生产者对**同一次尝试**说了两套话。

    静默保留先写的那条会让事后那句「这个 URL 那天到底怎么样了」有一个**看不出来**
    的错误答案。所以这里要报错，而且**先写的那一行必须还在** —— 一个「报错之前先把
    坏的写进去」的实现在第一次调用上看起来完全正确。
    """
    first = make_attempt(observed_status=200, error_code="")
    store.put_attempt(first)

    with pytest.raises(StoreError, match="attempt_id"):
        store.put_attempt(
            make_attempt(observed_status=None, error_code="ConnectTimeout")
        )

    survivor = store.get_attempt(first.attempt_id)
    assert survivor is not None and survivor.observed_status == 200, (
        "冲突之后留下的必须是先写的那一行 —— 否则「报错」只是把一条错答案换成了另一条"
    )


# ----------------------------------------------------------------------
# 读：三个问法，以及两个入口都不许把「别人的」折成「没有的」
# ----------------------------------------------------------------------
def test_an_unknown_id_is_none_and_only_none(store: FetchAttemptStore) -> None:
    assert store.get_attempt("att-0000000000000000") is None


def test_a_second_workspace_cannot_join_this_store(store: FetchAttemptStore) -> None:
    """一个库只装一个工作区的产出：绑定由**第一行写入**建立。"""
    store.put_attempt(make_attempt())

    with pytest.raises(StoreError, match=WORKSPACE):
        store.put_attempt(make_attempt(workspace=OTHER_WORKSPACE))

    assert len(store.list_attempts()) == 1, "被拒的那一行不留痕"


def test_a_row_from_another_workspace_is_an_error_not_none(
    store: FetchAttemptStore,
) -> None:
    """**不属于本库的行报错，不是被跳过，也不是 ``None``/空列表。**

    跳过它会让「这一行不属于你」与「没有这一行」在调用点长成同一个空结果 —— 越权读
    因此变成一次静默成功。两个读入口都要挡（少挡一个就是一条绕行路）。
    """
    store.put_attempt(make_attempt())
    foreign = make_attempt(workspace=OTHER_WORKSPACE)
    _smuggle_foreign_row(store, foreign)

    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        store.get_attempt(foreign.attempt_id)
    with pytest.raises(StoreError, match=OTHER_WORKSPACE):
        store.list_attempts()


# ----------------------------------------------------------------------
# 读：三条筛子、顺序、截断
# ----------------------------------------------------------------------
def test_listing_is_newest_first(store: FetchAttemptStore) -> None:
    """``N`` 的读数通常是「最近 N 次怎么样了」，而这一条是它的前提。"""
    older = make_attempt(started_at=T0, ended_at=T0 + timedelta(seconds=1))
    newer = make_attempt(
        started_at=T0 + timedelta(minutes=5), ended_at=T0 + timedelta(minutes=5, seconds=1)
    )
    store.put_attempt(older)
    store.put_attempt(newer)

    assert [row.started_at for row in store.list_attempts()] == [
        newer.started_at,
        older.started_at,
    ]


def test_the_ordering_is_stable_when_two_attempts_share_a_moment(
    store: FetchAttemptStore,
) -> None:
    """同刻的一批行必须有**稳定**的顺序。

    秒级精度下同一秒里有两条是常事。少了尾部那个 ``attempt_id``，两次读在上限截断
    之下会给出不同的那一批，而报告上只显示「这次列的是别的几行」。
    """
    first = make_attempt(url=f"{PAGE}?page=1")
    second = make_attempt(url=f"{PAGE}?page=2")
    store.put_attempt(first)
    store.put_attempt(second)

    once = [row.attempt_id for row in store.list_attempts()]
    twice = [row.attempt_id for row in store.list_attempts()]
    assert once == twice
    assert once == sorted(once, reverse=True), (
        "同一个 ``started_at`` 之下按 ``attempt_id`` 降序 —— 降序本身不重要，"
        "重要的是它有一个写下来的方向"
    )


def test_the_limit_drops_the_oldest(store: FetchAttemptStore) -> None:
    """截掉**最旧的**：三行取两行，被丢的是最早那一条。"""
    rows = [
        make_attempt(
            url=f"{PAGE}?page={index}",
            started_at=T0 + timedelta(minutes=index),
            ended_at=T0 + timedelta(minutes=index, seconds=1),
        )
        for index in range(3)
    ]
    for row in rows:
        store.put_attempt(row)

    kept = store.list_attempts(limit=2)
    assert [row.url for row in kept] == [f"{PAGE}?page=2", f"{PAGE}?page=1"]


def test_an_empty_scope_is_a_filter_and_not_a_wildcard(store: FetchAttemptStore) -> None:
    """**空串是一个值**：筛的是「不属于任何主体的那些尝试」。

    写成 ``if scope:`` 会把 ``""`` 读成 ``None``，而它的表现是「这次运行什么都没抓到」
    与「不属于任何主体的抓取非常多」在报告上同形。两个方向都判 —— 一条「无论传什么
    都筛出全部」的实现会让上一条照样通过。
    """
    named = make_attempt(scope="account-001", url=f"{PAGE}?a")
    anonymous = make_attempt(scope="", url=f"{PAGE}?b")
    store.put_attempt(named)
    store.put_attempt(anonymous)

    assert [row.url for row in store.list_attempts(scope="")] == [f"{PAGE}?b"], (
        "``scope=''`` 筛的是不属于任何主体的那一批"
    )
    assert [row.url for row in store.list_attempts(scope="account-001")] == [f"{PAGE}?a"]
    assert len(store.list_attempts(scope=None)) == 2, "``None`` 是「不筛」"


def test_a_scope_with_no_rows_is_an_empty_list(store: FetchAttemptStore) -> None:
    """有存储、但没有这个主体的行 —— 那是一份空的清单，不是一次错误。"""
    store.put_attempt(make_attempt())
    assert store.list_attempts(scope="account-999") == []


def test_the_url_filter_is_the_requested_url_not_the_redirect_target(
    store: FetchAttemptStore,
) -> None:
    """排查问的是「**我们问过**这个 URL 吗」，而重定向之后的地址是另一个问题的答案。

    所以筛的是 ``url``（请求的那个），不是 ``final_url``。一行一条：这一条能红，
    只可能因为实现筛错了列。
    """
    store.put_attempt(make_attempt())  # url=PAGE，final_url={PAGE}/

    assert len(store.list_attempts(url=PAGE)) == 1
    assert store.list_attempts(url=f"{PAGE}/") == [], (
        "``final_url`` 不是这一格筛的东西 —— 筛它会把「我们没问过这个地址」"
        "答成「问过」"
    )


def test_since_filters_on_the_start_moment_not_the_end(store: FetchAttemptStore) -> None:
    """用**开始**时刻，与 ``attempt_id`` 同一条理由（结束时刻会随重试次数变）。

    两行的分界刻意落在这一格上：一条在 ``since`` 之后**开始**、在它之前**结束**
    是不可能的，所以判据取的是「在 ``since`` 之前开始、之后结束」的那一条 ——
    按结束筛的实现会把它留下，按开始筛的实现会把它筛掉。
    """
    straddling = make_attempt(
        started_at=T0, ended_at=T0 + timedelta(hours=2)
    )
    later = make_attempt(
        url=f"{PAGE}?later",
        started_at=T0 + timedelta(hours=1),
        ended_at=T0 + timedelta(hours=1, seconds=1),
    )
    store.put_attempt(straddling)
    store.put_attempt(later)

    floor = T0 + timedelta(minutes=30)
    assert [row.url for row in store.list_attempts(since=floor)] == [f"{PAGE}?later"], (
        "跨越 ``since`` 的那一条**开始于**它之前，所以它落在线外 —— 按 ``ended_at`` "
        "筛的实现会把它留下"
    )


def test_since_accepts_an_aware_moment_in_any_offset(store: FetchAttemptStore) -> None:
    """传入的时刻先归一到 UTC 再比。

    不归一的话，``+08:00`` 的串与 ``Z`` 的串按字典序比会给出一个**看起来像答案**的
    结果（``2026-09-27T09:00:00+08:00`` 与 ``2026-09-27T01:00:00Z`` 是同一瞬间，而
    前者的字符串更大）。这一条与「写进去的那一列就是 ``to_utc(...).isoformat()``」
    是一对：两处不同刻度，比较就没有意义。
    """
    store.put_attempt(make_attempt())  # started_at = 2026-09-27T01:00:00Z

    # 同一瞬间，另一个时区的写法。它必须**包含**那一行（>= 而不是 >）。
    same_moment = T0.astimezone(timezone(timedelta(hours=8)))
    assert [row.url for row in store.list_attempts(since=same_moment)] == [PAGE]

    # 一秒之后，同一瞬间的另一种写法必须**排除**它。
    later = (T0 + timedelta(seconds=1)).astimezone(timezone(timedelta(hours=8)))
    assert store.list_attempts(since=later) == []


def test_the_filters_compose(store: FetchAttemptStore) -> None:
    """三条筛子都要能同时用 —— 排查的现场总是「这个主体的、这个地址的、那以后」。"""
    wanted = make_attempt(scope="account-001", url=f"{PAGE}?a")
    store.put_attempt(wanted)
    store.put_attempt(make_attempt(scope="account-002", url=f"{PAGE}?b"))

    found = store.list_attempts(scope="account-001", url=f"{PAGE}?a", since=T0)
    assert [row.attempt_id for row in found] == [wanted.attempt_id]


def test_settlement_state_round_trips(store: FetchAttemptStore) -> None:
    """预算那两格是枚举而不是字符串。

    写进去的是 ``SettlementState``，读回来的必须仍是那个成员 —— 一个把它存成 ``str``
    再读回来的实现会让 ``is SettlementState.RESERVED`` 这类判据静默为假，而报告上
    那一格只显示一个字符串，看不出它已经不是枚举了。

    ## 预留号也一起判，因为它是那两格里的另一格

    标题说的是「两格」，而这一条原先只判了其中一格。跟着一起判的好处不只是覆盖：
    ``RESERVED`` 那一格**必须**配一个预留号（``FetchAttempt``
    ``_the_settlement_names_its_reservation``），所以造一条 ``RESERVED`` 却不给号的
    记录本身就是自相矛盾的 —— 那样一条行写进去之后，账上就多了一笔找不到预留的
    「押着额度」。这一格因此同时是那条一致性规则的一个正常用例。
    """
    reservation = "9f2c1e0b8a7d6c5f4e3b2a1908f7e6d5"
    store.put_attempt(
        make_attempt(
            budget_settlement=SettlementState.RESERVED,
            budget_reservation_id=reservation,
        )
    )

    (back,) = store.list_attempts()
    assert back.budget_settlement is SettlementState.RESERVED
    assert back.budget_reservation_id == reservation

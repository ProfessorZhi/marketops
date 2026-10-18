"""``AccountCohortStore`` 契约：**同一组判据，对着两个实现各跑一遍**。

## 为什么两个实现

与 ``tests/test_account_store_contract.py`` / ``test_asset_store_contract.py`` /
``test_relation_store_contract.py`` 逐字同一条理由：``ports/pool.py`` 抽出端口的
理由是幂等边界不同，但端口本身不构成契约 —— 构成契约的是**同一组判据同时看着两个
实现**。

* ``adapters/sqlite/pool.py::SqliteAccountCohortStore`` —— 主存。
* ``adapters/memory.py::InMemoryAccountCohortStore`` —— 测试替身。

## 这一族与前三族不同的那一格：**没有合并规则**

另外三个端口的构造参数里都有一条 ``merge`` 规则，而这一张表**没有**。理由是它们
的写是「读—改—写」，所以「怎么折」必须由调用方定；这一张表的写**不读旧行** ——
一份名单的内容完全由 ``cohort_id`` 决定（那个 id 是内容寻址的，见
``domain/cohort.py``），所以「怎么折」这个问题在这里不存在。

所以下面没有「接进来的规则真的被执行了」那一条探针（前三份都有）。取而代之的是
**它的反面**：``created_at`` 是这张表上唯一一处读—改—写，而它只有一种改法
（取更早的那一个），所以这里钉的是「重放不把它推后」—— 一个把 ``created_at``
交给来行覆盖的实现会让一份早就冻好的名单声称自己是刚冻的，而 §B22 的复用判定
看的正是这个时刻。

## 两份判据**刻意不在这里**

1. 「写完重开一个 store 还能读到」只对持久化实现成立，那条在
   ``tests/test_sqlite_pool.py``。
2. **跨工作区**：``bind_workspace`` 的拒绝形态两个实现都有，而它属于「一个库只能
   绑一个工作区」这条**部署**判据，不在这一份里（见下面最后一条的理由）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import InMemoryAccountCohortStore
from marketops.adapters.sqlite.pool import SqliteAccountCohortStore
from marketops.domain.cohort import (
    AccountCohort,
    CohortExclusion,
    CohortExclusionCode,
    CohortMember,
    CohortSelection,
    cohort_id_for,
    exclusion_reason,
)
from marketops.domain.enums import AccountStatus
from marketops.ports.pool import AccountCohortStore

UTC = timezone.utc
T0 = datetime(2026, 9, 26, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
SCENE = "scene-0123456789abcdef"


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path) -> AccountCohortStore:
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteAccountCohortStore(tmp_path / "state" / "marketops.db")
    return InMemoryAccountCohortStore()


def make_cohort(
    *,
    members: tuple[str, ...] = ("acct-0000000000000001",),
    exclusions: tuple[str, ...] = (),
    scene_id: str = SCENE,
    selection: CohortSelection | None = None,
    at: datetime = T0,
) -> AccountCohort:
    """一份形状正确的名单。``cohort_id`` 由内容算出来 —— 手写一个会被模型拒掉。"""
    chosen = selection or CohortSelection()
    member_rows = tuple(
        CohortMember(account_id=aid, organisation=f"Example {aid}", status=AccountStatus.ELIGIBLE)
        for aid in members
    )
    exclusion_rows = tuple(
        CohortExclusion(
            account_id=aid,
            organisation=f"Example {aid}",
            status=AccountStatus.HOLD,
            code=CohortExclusionCode.ON_HOLD,
            reason=exclusion_reason(AccountStatus.HOLD, CohortExclusionCode.ON_HOLD),
        )
        for aid in exclusions
    )
    return AccountCohort(
        cohort_id=cohort_id_for(
            workspace=WORKSPACE,
            scene_id=scene_id,
            selection=chosen,
            members=member_rows,
            exclusions=exclusion_rows,
        ),
        workspace=WORKSPACE,
        scene_id=scene_id,
        selection=chosen,
        members=member_rows,
        exclusions=exclusion_rows,
        created_at=at,
    )


# --- 写：按 id 覆盖，重放是空写 ------------------------------------------------


def test_storing_the_same_roster_twice_leaves_one(store: AccountCohortStore) -> None:
    """按 ``cohort_id`` 覆盖 —— 行数就是「这个部署冻过几份名单」。

    重放写出第二行的话，报告上会把同一份名单数成两份。
    """
    first = make_cohort()
    store.put_cohort(first)
    store.put_cohort(first)

    assert [row.cohort_id for row in store.list_cohorts()] == [first.cohort_id]


def test_put_returns_the_row_that_landed(store: AccountCohortStore) -> None:
    """返回的是**落盘后的那一份**，调用方不需要（也不该）再读一遍。

    再读一遍等于把「这一次冻下来的是什么」这句答复建立在另一次快照上。
    """
    first = make_cohort()
    assert store.put_cohort(first) == first
    assert store.put_cohort(first) == store.get_cohort(first.cohort_id)


def test_replaying_an_older_roster_does_not_push_its_timestamp_later(
    store: AccountCohortStore,
) -> None:
    """``created_at`` 只往早走 —— 它是「我们第一次把这份读数冻下来」。

    推后之后，一份早就冻好的名单会声称自己是刚冻的，而 §B22 的复用判定看的正是
    这个时刻：一份「刚冻的」名单会让所有可复用的读数看起来都过期了。

    ★ 这一条钉的是一个**不会被别的判据发现**的改法：把 ``created_at`` 交给来行
    覆盖。单看「折同一个 id 只有一行」还是绿的，因为内容逐位相同 —— 只有这一格
    不同，而它不同的时候报告上完全看不出来。
    """
    early = make_cohort(at=T0)
    late = make_cohort(at=T0 + timedelta(days=3))

    store.put_cohort(late)
    stored = store.put_cohort(early)

    assert stored.created_at == T0
    assert store.get_cohort(early.cohort_id).created_at == T0  # type: ignore[union-attr]


def test_the_first_write_wins_the_timestamp_even_when_the_later_one_lands_first(
    store: AccountCohortStore,
) -> None:
    """上一条的对照：先写早的、再写晚的，落盘的还是早的那一个。"""
    early = make_cohort(at=T0)
    late = make_cohort(at=T0 + timedelta(days=3))

    store.put_cohort(early)
    assert store.put_cohort(late).created_at == T0


# --- 读：两个问法 ---------------------------------------------------------------


def test_get_returns_none_only_for_an_absent_id(store: AccountCohortStore) -> None:
    """``None`` 只有一个意思：**没有这个 id 的名单**。

    与「这个 id 存在但读不出来」不是同一句话 —— 后者是坏数据，前者是一次正常的
    查空。
    """
    store.put_cohort(make_cohort())
    assert store.get_cohort("coh-0000000000000000") is None
    assert store.get_cohort(make_cohort().cohort_id) is not None


def test_list_is_ordered_earliest_first(store: AccountCohortStore) -> None:
    """**早的在前**，而「当前那一份」就是这份序列的最后一个。

    顺序是这个端口的承重面：排序键在这里有第二个定义处的话，MCP 那一侧的「当前
    名单」与库里那一份会在某次改动后分家 —— 而分家的表现是「报告说这一批跑的是 A」
    与「真的跑了 B」同时成立，两份都自洽。
    """
    second = make_cohort(members=("acct-0000000000000002",), at=T0 + timedelta(hours=1))
    first = make_cohort(members=("acct-0000000000000001",), at=T0)
    store.put_cohort(second)
    store.put_cohort(first)

    assert [row.cohort_id for row in store.list_cohorts()] == [
        first.cohort_id,
        second.cohort_id,
    ]


def test_two_rosters_frozen_at_the_same_instant_still_have_one_order(
    store: AccountCohortStore,
) -> None:
    """**同一时刻**冻的两份也要有一个定的次序。

    一次 tick 里对两个场景各读一次就会这样。顺序留给实现的返回序的话，两次调用会
    给出两个「当前」，而报告上它们都只是「最近的一份名单」。
    """
    left = make_cohort(scene_id="scene-aaaaaaaaaaaaaaaa")
    right = make_cohort(scene_id="scene-bbbbbbbbbbbbbbbb")
    store.put_cohort(right)
    store.put_cohort(left)

    once = [row.cohort_id for row in store.list_cohorts()]
    twice = [row.cohort_id for row in store.list_cohorts()]
    assert once == twice
    assert once == sorted([left.cohort_id, right.cohort_id])


def test_a_roster_comes_back_the_same_as_it_went_in(store: AccountCohortStore) -> None:
    """读回来的是**同一份**：成员、排除、规则、时刻，逐格相同。

    这一条看着像废话，而它挡的是一种很常见的收口方式：把排除清单存在另一个字段里
    （或者干脆不存），读回来时按规则现推一遍。那会让「当时为什么排除它」变成
    「照现在的规则它为什么会被排除」，而两者在池子变了之后不是同一句话。
    """
    cohort = make_cohort(
        members=("acct-0000000000000001", "acct-0000000000000002"),
        exclusions=("acct-0000000000000003",),
        selection=CohortSelection(statuses=(AccountStatus.ELIGIBLE,), limit=7),
    )
    store.put_cohort(cohort)
    assert store.get_cohort(cohort.cohort_id) == cohort


# --- 端口上只有三个问法 ---------------------------------------------------------


def test_the_port_has_no_second_way_to_ask_for_a_roster() -> None:
    """接口上**只有**三个抽象方法：存一份、按 id 取一份、取全部。

    ★ 这一条是 ``ports/pool.py`` 那段「一个『按期或按场景查最近一份』的读不在这里」
    在**结构层面**的执行器。加一个 ``latest_cohort(scene_id)`` 不会让任何一条已有
    用例转红 —— 它只会让「哪一份是当前的」在存储里有第二个定义处。

    判据直接从类上取（``__abstractmethods__``），所以它**不需要**人抄一份方法名清单。

    这里**没有** ``__init__``，而另外三个端口都有（它们各收一条合并规则，所以那一格
    由端口定形状）。这一张表的写不读旧行，没有一条规则要接 —— 所以「构造参数里多
    一个 ``merge``」这件事在这里也要红：它意味着有人把一条读—改—写的规则接了进来，
    而一份名单的内容完全由 ``cohort_id`` 决定。
    """
    assert AccountCohortStore.__abstractmethods__ == {
        "get_cohort",
        "list_cohorts",
        "put_cohort",
    }, (
        "冻结成员名单的端口上多了一个问法，或者多了一条要接进来的规则。请先回答"
        "「哪一份是当前的由谁定义」—— 这个顺序只能有一处，而它今天是 ``list_cohorts`` "
        "的最后一个；以及「一份名单的内容由谁决定」—— 它由内容寻址的 ``cohort_id`` "
        "决定，不由一条合并规则。"
    )

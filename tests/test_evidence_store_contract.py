"""``EvidenceStore`` 契约：**同一组判据，对着三个实现各跑一遍**。

## 这个文件存在的理由

B5 之前只有两个实现，而且它们写在同一个文件里 —— 行为靠「照着上一个写一遍」对齐。
那种对齐没有任何执行器：一个实现改了语义，另一个不会红，因为没有任何东西同时
看着它们。判据写在这里、实现各自在文件里，才是「这是一个契约」而不是「两段恰好
相似的代码」。

三个实现：

* ``adapters/jsonl/store.py`` —— 导出与审计通道（人类可直接 grep）
* ``adapters/sqlite/evidence_store.py`` —— 主存（事务性）
* ``adapters/memory.py`` —— 测试替身

## 有一条判据**刻意不在这里**

「写完之后重开一个 store 还能读到」是持久化实现之间共有的性质，内存实现按定义
做不到。把它写进来，就只能给它加一个「内存除外」的例外 —— 而契约里出现第一个
例外之后，第二个就不会有人再争论了。所以那条判据属于 SQLite 与 JSONL 各自的
文件，不在这里。

## 这里的数据构造器是**共享**的

``make_evidence`` / ``make_route`` / ``make_opportunity`` 也给
``tests/test_store.py`` 用。分开的是**判据**，不是被判断的对象 —— 两个文件说的
是同一批模型，各自抄一份构造器只会让它们慢慢漂开。
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marketops.adapters.jsonl.store import LocalEvidenceStore
from marketops.adapters.memory import InMemoryEvidenceStore
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.payload import dump_payload
from marketops.config import REPO_ROOT, Settings
from marketops.domain.entities import ContactRoute, RouteOpportunity
from marketops.domain.enums import Cohort, ContactField, Grade, SourceKind
from marketops.domain.errors import EvidenceError
from marketops.domain.evidence import Evidence
from marketops.domain.route_records import (
    RouteObservation,
    RouteRejection,
    RouteRejectionCode,
    canonical_route_key,
    fold_observation,
    observation_id_for,
    rejection_id_for,
)

FETCHED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

#: 构造器默认用的工作区：``_settings`` 里那个部署的 ``profile``。
#:
#: **默认值在这里是合法的**（而模型层一个都没有）：这些是**测试的**构造器，默认值
#: 说的是「这一条属于这个测试所模拟的那个部署」—— 一个测试文件里只可能有一个部署。
#: 判据要用到「另一个工作区的对象」时，显式传 ``workspace=``（见
#: ``test_a_row_of_another_workspace_is_rejected_not_skipped``）。
WORKSPACE = "uk-acme-natural-grass"


def make_evidence(
    evidence_id: str = "e1", account_id: str = "a1", **overrides
) -> Evidence:
    payload = {
        "workspace": WORKSPACE,
        "evidence_id": evidence_id,
        "account_id": account_id,
        "source_url": "https://example.co.uk/contact",
        "source_kind": SourceKind.FETCHED_HTML,
        "literal_value": "grounds@example.co.uk",
        "content_sha256": "abc",
        "fetched_at": FETCHED_AT,
        "field": ContactField.EMAIL,
    }
    payload.update(overrides)
    return Evidence(**payload)


def make_route(account_id: str = "a1", **overrides) -> ContactRoute:
    payload = {
        "workspace": WORKSPACE,
        "account_id": account_id,
        "email": "grounds@example.co.uk",
        "evidence_ids": ["e1"],
    }
    payload.update(overrides)
    return ContactRoute(**payload)


def make_observation(
    account_id: str = "a1",
    *,
    observed_at: datetime = FETCHED_AT,
    route: ContactRoute | None = None,
    **overrides,
) -> RouteObservation:
    """一次观察：``make_route`` 那个对象，加上「这是哪一刻看的」。

    ``observed_at`` 取自 ``FETCHED_AT`` —— 与证据用的是同一个时刻，因为真实路径上
    它就是材料的 ``fetched_at``（``RouteObservation.observed_at`` 的 docstring
    逐字如此）。默认值在这里合法，理由与 ``WORKSPACE`` 那一格相同：这是**测试的**
    构造器。

    ``route=`` 是给**已经造好的**那一条用的（传了它就忽略 ``overrides``）。需要它的
    理由只有一个：``make_route`` 是一个不判时效的薄构造器，而真实路径上落盘的每一行
    都**先过** :func:`~marketops.application.freshness.judge`
    （``application/contact_routes.py`` 的 ``_judge_and_grade``，判完再定级、再
    ``rebuild``）。要造一条「与生产产出的那一行同形」的观察，就得把判过的那一行
    交进来 —— 在这里重跑一遍 ``judge`` 是另一条路，但那样一来本函数就得知道时限口径，
    而口径是**每个调用方**的事（部署的 profile 声明它，测试自己的夹具声明它）。
    """
    if route is None:
        route = make_route(account_id=account_id, **overrides)
    key = canonical_route_key(
        workspace=route.workspace, account_id=route.account_id, email=route.email
    )
    return RouteObservation(
        observation_id=observation_id_for(
            workspace=route.workspace,
            account_id=route.account_id,
            canonical_key=key,
            observed_at=observed_at,
            route=route,
        ),
        canonical_key=key,
        workspace=route.workspace,
        account_id=route.account_id,
        observed_at=observed_at,
        route=route,
    )


def make_rejection(account_id: str = "a1", **overrides) -> RouteRejection:
    """一条拒收回执。``candidate`` + ``field`` 而不是一格 ``email`` —— 见
    :class:`~marketops.domain.route_records.RouteRejection` 的 docstring。

    ``rejection_id`` 由 ``candidate``（不是 ``field``）算出来，所以只改 ``field``
    的用例会落到**同一个** id 上 —— 那是刻意的：这两个字面值来自同一列上的同一次
    拒收，收据还是那一张。
    """
    payload: dict = {
        "workspace": WORKSPACE,
        "account_id": account_id,
        "candidate": "someone.else@example.co.uk",
        "field": ContactField.EMAIL,
        "code": RouteRejectionCode.ENTRY_ELSEWHERE,
        "detail": "页面上是另一个人",
        "rule": "SOP L126",
        "source_kind": SourceKind.FETCHED_HTML,
        "source_url": "https://example.co.uk/contact",
        "evidence_ids": ("e1",),
        "run_id": "run-1",
        "work_item_id": "wi-1",
        "occurred_at": FETCHED_AT,
    }
    payload.update(overrides)
    payload["rejection_id"] = rejection_id_for(
        workspace=payload["workspace"],
        account_id=payload["account_id"],
        code=payload["code"],
        candidate=payload["candidate"],
        source_url=payload["source_url"],
        occurred_at=payload["occurred_at"],
    )
    return RouteRejection(**payload)


def make_opportunity(account_id: str = "a1", **overrides) -> RouteOpportunity:
    payload = {
        "workspace": WORKSPACE,
        "account_id": account_id,
        "role_title": "Grounds Manager",
    }
    payload.update(overrides)
    return RouteOpportunity(**payload)


@pytest.fixture(params=["jsonl", "sqlite", "memory"])
def store(request, tmp_path: Path):
    """三个实现之一。名字进测试 id，红了的时候一眼看得见是哪一个。

    JSONL 用裸 ``Settings`` 而不是 ``settings`` 夹具的目录：三个参数共用同一份
    临时目录会让「哪一个实现写的」变成需要推理的事，而这里每个参数本来就该有
    自己的位置。
    """
    if request.param == "jsonl":
        return LocalEvidenceStore(_settings(tmp_path))
    if request.param == "sqlite":
        return SqliteEvidenceStore(tmp_path / "state" / "marketops.db")
    return InMemoryEvidenceStore()


def _settings(tmp_path: Path) -> Settings:
    resolved = Settings(
        data_root=tmp_path / "runtime",
        profiles_dir=REPO_ROOT / "profiles",
        profile="uk-acme-natural-grass",
    )
    resolved.ensure_data_dirs()
    return resolved


def _fresh_store(store):
    """同类型的**空**存储，放在一个新目录里。

    顺序那一条判据要跑两遍同一批观察 —— 而重放同一条观察是不生效的（那正是幂等
    那条要求），所以第二遍必须在另一个存储上。用一个新的 ``tmp_path`` 而不是清空
    原来那个：清空会把「第一次那个存储读出来是什么」也一并毁掉。
    """
    tmp = Path(tempfile.mkdtemp())
    if isinstance(store, LocalEvidenceStore):
        return LocalEvidenceStore(_settings(tmp))
    if isinstance(store, SqliteEvidenceStore):
        return SqliteEvidenceStore(tmp / "state" / "marketops.db")
    return InMemoryEvidenceStore()


# --- 往返保真 -----------------------------------------------------------------


def test_evidence_round_trip(store) -> None:
    store.put_evidence(make_evidence())

    loaded = store.list_evidence("a1")
    assert len(loaded) == 1
    assert loaded[0].literal_value == "grounds@example.co.uk"
    assert loaded[0].source_kind is SourceKind.FETCHED_HTML
    assert loaded[0].fetched_at == FETCHED_AT


def test_route_round_trip(store) -> None:
    store.record_route(make_observation(grade=Grade.B))

    loaded = store.list_routes("a1")
    assert len(loaded) == 1
    assert loaded[0].route.grade is Grade.B
    assert loaded[0].route.send_authorized is False
    # 投影不只是那条路线换个壳 —— 它还得说得出这条读法是几次观察折出来的。
    assert loaded[0].revision == 1
    assert loaded[0].observation_ids == (make_observation(grade=Grade.B).observation_id,)


def test_observation_round_trip(store) -> None:
    """观察是**历史**：写进去的那一条按它自己的 id 读得回来。"""
    written = make_observation(grade=Grade.B)
    store.record_route(written)

    assert store.list_route_observations("a1") == [written]


def test_rejection_round_trip(store) -> None:
    """一条被拒的候选写得进、读得出来，而且**那句话是它自己的**。"""
    written = make_rejection()
    store.put_route_rejection(written)

    loaded = store.list_route_rejections("a1")
    assert loaded == [written]
    assert loaded[0].code is RouteRejectionCode.ENTRY_ELSEWHERE
    assert loaded[0].candidate == "someone.else@example.co.uk"
    assert loaded[0].field is ContactField.EMAIL


def test_opportunity_round_trip(store) -> None:
    store.put_opportunity(make_opportunity())

    loaded = store.list_opportunities("a1")
    assert len(loaded) == 1
    assert loaded[0].role_title == "Grounds Manager"


def test_non_ascii_content_round_trips(store) -> None:
    """邮箱里可以有非 ASCII 本地部分与 IDN 域名。编码错了会静默变成问号。"""
    store.put_evidence(make_evidence(literal_value="контакт@例え.jp"))

    assert store.list_evidence("a1")[0].literal_value == "контакт@例え.jp"


def test_insertion_order_is_preserved(store) -> None:
    """顺序是判据的一部分：报告按它读，对账也按它读。

    「先写 A 再写 B，读出来是 [A, B]」这条对三个实现都成立，但它们成立的**理由**
    各不相同（文件追加顺序 / 主键自增 / dict 插入序）。写下来是因为换实现时这条
    最容易被无声地破坏，而破坏之后没有任何东西会红。
    """
    store.record_route(make_observation(email="first@example.co.uk"))
    store.record_route(make_observation(email="second@example.co.uk"))

    assert [r.route.email for r in store.list_routes("a1")] == [
        "first@example.co.uk",
        "second@example.co.uk",
    ]


def test_projection_order_is_by_key_not_by_arrival(store) -> None:
    """投影按 ``canonical_key`` 排，**不按写入先后**。

    写 B 再写 A，读出来是 [A, B] —— 与反过来写得到的是同一个列表。这条判据的
    意义在于报告：同一份存档在两台机器上导出，行序必须相同，否则「对一下这两个
    文件」没法做。按到达序排的实现会在这一条上红，而在上面那条（写入顺序也是键
    顺序）上**是绿的** —— 两条要一起看才有意义。
    """
    store.record_route(make_observation(email="zebra@example.co.uk"))
    store.record_route(make_observation(email="aardvark@example.co.uk"))

    assert [r.route.email for r in store.list_routes("a1")] == [
        "aardvark@example.co.uk",
        "zebra@example.co.uk",
    ]


# --- 按 id 取 -----------------------------------------------------------------


def test_evidence_can_be_read_back_by_its_own_id(store) -> None:
    """写进去的那一条，用**它自己的 id** 取回来是同一行。

    这是 ``MCP_SERVICE.md`` §3 那行 ``get_evidence``（"evidence id + bounded
    locator"）在存储层的前提：一条引用要能被解回材料，端口上就得先有这条路。
    在它落地之前，``submit_findings`` / ``decide`` 收下的 ``evidence_refs`` 只能
    校验「形状像一个 id」—— 引一条不存在的证据在记录上与引一条真的完全一样。
    现在收口的那两条路是 ``application/evidence_refs``（规则本身）与
    ``get_evidence`` 那条工具（按引用读），判据在 ``tests/test_evidence_refs.py``
    与 ``tests/test_mcp_control_tools.py``。
    """
    written = make_evidence()
    store.put_evidence(written)

    assert store.get_evidence(written.evidence_id) == written


def test_an_unknown_evidence_id_reads_as_none_rather_than_raising(store) -> None:
    """取不到返回 ``None``，**不抛**。

    调用方要回答的是「这条引用解不解得开」，而那是一个**结果**，不是一个异常
    情形。抛出去的话，唯一能写出正确处置的地方就被夹在 ``except`` 里 —— 而
    更常见的写法是把它并进那个已经把「库损坏」「连接失败」「schema 不符」一起
    接住的兜底，于是「这条引用是假的」与「存储坏了」在报告上同形。
    """
    assert store.get_evidence("no-such-evidence-id") is None


def test_the_two_read_paths_agree(store) -> None:
    """``get_evidence(id)`` 与 ``list_evidence(account)`` 里的那一条**相等**。

    两条读路径各自演化时，同一行证据会按读法不同给出两份内容，而报告上看不出来
    —— 两边都「读到了」。这条判据是它们之间唯一的连接件。
    """
    store.put_evidence(make_evidence(evidence_id="e1"))
    store.put_evidence(make_evidence(evidence_id="e2", literal_value="hire@example.co.uk"))

    listed = {item.evidence_id: item for item in store.list_evidence("a1")}
    assert len(listed) == 2
    for evidence_id, expected in listed.items():
        assert store.get_evidence(evidence_id) == expected


def test_the_id_space_is_shared_across_accounts(store) -> None:
    """按 id 取**不经过账号** —— ``evidence_id`` 是内容寻址的，把 ``account_id``
    也算进了原像（``domain.evidence.evidence_id``），所以三个实现看到的本来就是
    同一个 id 空间。

    这条判据钉的是「取的时候不需要调用方知道账号」。要求调用方先交出账号，就等于
    要求它先解开一次它正在试图解开的引用。
    """
    store.put_evidence(make_evidence(evidence_id="e1", account_id="a2"))

    assert store.get_evidence("e1") is not None
    assert store.get_evidence("e1").account_id == "a2"


# --- 幂等边界 -----------------------------------------------------------------


def test_put_evidence_is_idempotent(store) -> None:
    """同一证据重复写入不追加第二行 —— 否则指标会被重复计数。"""
    for _ in range(3):
        store.put_evidence(make_evidence())

    assert len(store.list_evidence("a1")) == 1


def test_distinct_emails_are_distinct_routes(store) -> None:
    """同一个人的工作邮箱与部门邮箱是**两条路线**，各有各的投影。"""
    store.record_route(make_observation())
    store.record_route(make_observation(email="info@example.co.uk"))

    assert len(store.list_routes("a1")) == 2
    assert {r.route.email for r in store.list_routes("a1")} == {
        "grounds@example.co.uk",
        "info@example.co.uk",
    }


def test_the_same_route_observed_twice_is_one_projection(store) -> None:
    """**这一条是这次改动的全部内容。** 同一个 (账号, 邮箱) 看两遍：

    * 投影是**一条**（重复抓取不再让报告说「路线更多了」）；
    * 历史是**两条**（那一次抓取确实发生过，抹掉它就是抹掉证据）；
    * 投影的 ``observation_ids`` 两条都在 —— 两者同源，不是各说各话。
    """
    first = make_observation(observed_at=FETCHED_AT)
    second = make_observation(
        observed_at=FETCHED_AT.replace(hour=18), grade=Grade.B
    )
    store.record_route(first)
    store.record_route(second)

    projections = store.list_routes("a1")
    assert len(projections) == 1
    assert projections[0].revision == 2
    assert projections[0].previous_grade is Grade.X
    assert projections[0].route.grade is Grade.B
    assert set(projections[0].observation_ids) == {
        first.observation_id,
        second.observation_id,
    }
    assert len(store.list_route_observations("a1")) == 2


def test_replaying_an_observation_changes_nothing(store) -> None:
    """重放同一次观察：投影与历史**逐位不变**。

    这是 §6.2 的幂等那一条。它必须比「写两次只有一行」更强 —— 后者对一个
    「第二次又把 revision 加一」的实现也成立。
    """
    observation = make_observation()
    store.record_route(observation)
    before = store.list_routes("a1")[0]

    store.record_route(observation)
    store.record_route(observation)

    assert store.list_routes("a1") == [before]
    assert len(store.list_route_observations("a1")) == 1


def test_a_late_writing_does_not_overwrite_the_current_reading(store) -> None:
    """**晚到的那次写不覆盖当前读法，但它仍然进历史。**

    先看到 D（下午），后收到一条早上就该到的观察（X，也就是「这一页上没有
    可用的联系入口」）。当前读法必须仍然是 D —— 按到达序覆盖会让一条**更早**的
    事实把后来确认的那条挤掉。而历史里两条都在：那条早上的观察确实被观察到了，
    抹掉它就是抹掉「那天早上那页长这样」。
    """
    late = make_observation(observed_at=FETCHED_AT.replace(hour=18), grade=Grade.D)
    early = make_observation(observed_at=FETCHED_AT, grade=Grade.X)
    store.record_route(late)
    store.record_route(early)

    projection = store.list_routes("a1")[0]
    assert projection.route.grade is Grade.D, "迟到的写把当前读法改回去了"
    assert projection.revision == 1
    assert len(store.list_route_observations("a1")) == 2


def test_the_fold_does_not_take_the_maximum(store) -> None:
    """``D + X + X`` 折出来**不是** D。

    取最大等级的实现会在这里红，而且它在「先 D 后 X」这一条上也是红的 —— 两条
    一起看才能说明这不是某一条判据写坏了。被挡住的是「一条旧的、看着更好的读法
    在证据早就变了之后继续活着」。

    ## 为什么这里只断读数，不断 ``revision``

    三对观察（9/12/18 点各一对 D 与 X）折出来，**读数**是 18 点那一条读法里的
    X，这一条与到达顺序无关。``revision`` 却是「这个存储改写了几次」，它与顺序
    有关（见 ``CanonicalContactRoute`` 的 docstring）—— 在同一个瞬间到达的 D 与
    X 由内容哈希定序，所以这里**不能**断言一个具体的次数。断它的实现会在
    「同一批观察换个顺序」时红，而那正是设计说会发生的事。
    """
    times = [FETCHED_AT.replace(hour=h) for h in (9, 12, 18)]
    for at in times:
        store.record_route(make_observation(observed_at=at, grade=Grade.D))
        store.record_route(make_observation(observed_at=at, grade=Grade.X))

    projection = store.list_routes("a1")[0]
    assert projection.route.grade is Grade.X, "取最大值了 —— 一条旧的 D 活着"
    assert projection.last_observed_at == times[-1], "读数不是时间上最后那一条"
    # 全部六条都在历史里，一条没少 —— 投影只有一行不代表观察被丢了。
    assert len(store.list_route_observations("a1")) == 6


def test_the_current_reading_does_not_depend_on_the_order_observations_arrive(
    store,
) -> None:
    """**§6.2 的并发安全，判据就是这一条。**

    同一批观察按时间序提交与倒着提交，落下来的**当前读法逐位相同**。两个 worker
    谁先谁后不该被看见 —— 而被看见的表现不是报错，是一份取决于时序的报告。

    ``revision`` / ``previous_grade`` **故意**不在比较里，它们记的是改写过程
    （见 ``CanonicalContactRoute`` 的 docstring）。这一条判据把那个区别钉在明处：
    想当然地把它们也一起比的人会在这里看到自己在要求一件设计说不会发生的事。
    """
    forward = [
        make_observation(observed_at=FETCHED_AT.replace(hour=h), grade=Grade.D)
        for h in (9, 12)
    ]
    for observation in forward:
        store.record_route(observation)
    by_time = store.list_routes("a1")[0]

    # 换一个存储再来一遍（同一个 store 上重放走的是幂等那条路，测不到顺序）。
    other = _fresh_store(store)
    for observation in reversed(forward):
        other.record_route(observation)
    by_arrival = other.list_routes("a1")[0]

    assert by_time.route == by_arrival.route
    assert by_time.last_observed_at == by_arrival.last_observed_at
    assert by_time.last_observation_id == by_arrival.last_observation_id
    assert by_time.canonical_key == by_arrival.canonical_key
    # 而改写账**会**不同：倒着到达时那条更早的观察一版都没改，因为它一进来就已经
    # 比当前读法旧。这一句是刻意断的 —— 它把「这两格是过程账」写在明处。
    assert by_arrival.revision == 1


def test_all_observations_survive_regardless_of_arrival_order(store) -> None:
    """历史与到达顺序无关：六条观察，两种顺序，历史都是六条且集合相同。"""
    observations = [
        make_observation(observed_at=FETCHED_AT.replace(hour=h), grade=Grade.X)
        for h in (9, 12, 18)
    ]
    store.record_route(observations[1])
    store.record_route(observations[2])
    store.record_route(observations[0])

    assert len(store.list_route_observations("a1")) == 3
    assert store.list_routes("a1")[0].route.grade is Grade.X


def test_a_put_route_rejection_is_idempotent(store) -> None:
    """同一条拒绝写三次只有一行 —— 它是内容寻址的，重说不产生新信息。"""
    for _ in range(3):
        store.put_route_rejection(make_rejection())

    assert len(store.list_route_rejections("a1")) == 1


def test_the_same_rejection_on_another_page_is_another_row(store) -> None:
    """同一个邮箱在**另一个来源页**上被拒是另一条记录。

    这正是 ``rejection_id`` 把 ``source_url`` 与 ``occurred_at`` 也算进原像的理由：
    一次拒绝说的是「这一次运行在那个页面上没有接受它」，换一个页面就是另一份证据。
    按 (账号, 邮箱) 去重的实现会把第二条吞掉，而它看起来只是「少了一条没解释的
    候选」—— 那正是 §6.3 说不能只剩一个数字的那件事。
    """
    store.put_route_rejection(make_rejection())
    store.put_route_rejection(make_rejection(source_url="https://example.co.uk/team"))

    assert len(store.list_route_rejections("a1")) == 2


def test_a_rewritten_receipt_keeps_its_identity_and_moves_its_bookkeeping(store) -> None:
    """同一份材料被**第二趟**结算：还是那一行，而上面留的是最近那趟。

    第二趟与第一趟的差别只在记账那三格（``run_id`` 换了），而 ``rejection_id`` 的
    原像里**没有**它们 —— 那正是为了让重试、两个 worker 同时跑同一批、维护轮次重看
    同一页写的是**同一行**。三个实现此前都要求整行逐字节相同，于是这三种情况各撞
    一个 ``EvidenceError``；而幂等的承诺是「再算一遍不会多出一行」。

    这条判据同时钉住另一半：**不能反过来保留先写的那条**。收据上那句「这是哪一趟
    拒的」必须说最近那趟，否则一条今天还在发生的拒收会在报告里指向几周前的那次重试。
    """
    store.put_route_rejection(make_rejection())
    store.put_route_rejection(
        make_rejection(run_id="run-2", work_item_id="wi-2", rule="SOP L126（2026-09 版）")
    )

    rows = store.list_route_rejections("a1")
    assert len(rows) == 1
    assert rows[0].run_id == "run-2"
    assert rows[0].work_item_id == "wi-2"
    assert rows[0].rule == "SOP L126（2026-09 版）"


def test_a_rejection_whose_id_was_hand_set_is_refused(store) -> None:
    """``rejection_id`` 与内容对不上 → 抛，而不是静静地覆盖掉。

    这是「改写账」那条判据的**反面**，两半必须一起在：只有「同 id 就覆盖」而没有
    这一条的话，一个手工指定（或从别处抄来）的 id 会把一条**不同的**拒收盖掉 ——
    而报告上那条候选从此不存在，且看起来像是从没被拒过。

    身份在 ``rejection_id`` 的原像里（候选、原因码、来源页、时间都在），所以这里用
    改候选来制造不一致 —— 而 id 由 ``make_rejection`` 按新候选**重新算**，于是要
    手工把旧 id 按回去。
    """
    first = make_rejection()
    store.put_route_rejection(first)

    stolen = make_rejection(candidate="another.person@example.co.uk")
    assert stolen.rejection_id != first.rejection_id, "两个候选本该算出两个 id"
    forged = stolen.model_copy(update={"rejection_id": first.rejection_id})

    with pytest.raises(EvidenceError, match="身份与本次不同"):
        store.put_route_rejection(forged)

    assert [row.candidate for row in store.list_route_rejections("a1")] == [first.candidate]


def test_rejections_are_not_routes(store) -> None:
    """被拒的候选**不是**一条路线，也不是一条观察 —— 三个集合各数各的。"""
    store.put_route_rejection(make_rejection())

    assert store.list_routes("a1") == []
    assert store.list_route_observations("a1") == []
    assert len(store.list_route_rejections("a1")) == 1


def test_opportunities_accumulate_rather_than_deduplicate(store) -> None:
    store.put_opportunity(make_opportunity())
    store.put_opportunity(make_opportunity(role_title="Estates Manager"))

    assert len(store.list_opportunities("a1")) == 2


def test_the_same_id_with_different_content_is_refused(store) -> None:
    """``evidence_id`` 是内容寻址的 —— 同 id 不同内容只可能是 id 被手工指定过。

    静默保留先写的那条会让「证据写进去了」与「我以为写进去的这条证据」变成两件
    事，而报告只显示「证据更多了」。
    """
    store.put_evidence(make_evidence(literal_value="grounds@example.co.uk"))

    with pytest.raises(EvidenceError, match="内容与本次不同"):
        store.put_evidence(make_evidence(literal_value="someone.else@example.co.uk"))


def test_a_refused_conflict_does_not_replace_the_stored_row(store) -> None:
    """拒绝必须是**整个**拒绝：冲突的那次不能顺手把旧的改了。"""
    store.put_evidence(make_evidence(literal_value="grounds@example.co.uk"))
    with pytest.raises(EvidenceError):
        store.put_evidence(make_evidence(literal_value="someone.else@example.co.uk"))

    assert store.list_evidence("a1")[0].literal_value == "grounds@example.co.uk"


# --- 边界 ---------------------------------------------------------------------


def test_routes_and_opportunities_do_not_mix(store) -> None:
    """两个集合分开存、分开读。

    合成一个 ``list_contacts`` 会让「邮箱是否为空」变成每个调用方各自记得检查的
    约定 —— 而约定会在第一次「先跑通再说」的改动里消失。
    """
    store.record_route(make_observation())
    store.put_opportunity(make_opportunity())

    assert len(store.list_routes("a1")) == 1
    assert len(store.list_opportunities("a1")) == 1
    assert store.list_routes("a1")[0].route.email == "grounds@example.co.uk"


def test_accounts_are_isolated(store) -> None:
    store.put_evidence(make_evidence("e1", "a1"))
    store.put_evidence(make_evidence("e2", "a2", literal_value="other@example.co.uk"))

    assert len(store.list_evidence("a1")) == 1
    assert len(store.list_evidence("a2")) == 1


def test_missing_account_returns_empty(store) -> None:
    assert store.list_evidence("nobody") == []
    assert store.list_routes("nobody") == []
    assert store.list_route_observations("nobody") == []
    assert store.list_route_rejections("nobody") == []
    assert store.list_opportunities("nobody") == []


def test_cohort_is_irrelevant_to_store(store) -> None:
    """store 不关心队列归属 —— 那是 metrics 的事。"""
    store.record_route(make_observation(account_id="a1"))

    assert Cohort.KNOWN_SUCCESS.value == "known_success"
    assert len(store.list_routes("a1")) == 1


# --- 一个存储只装一个工作区：别人的行是**拒绝**，不是空 -------------------------

#: 另一个工作区。**它在不在 ``profiles/`` 下与这条判据无关** —— 问的是「这一行
#: 属不属于这个存储」，而那个答案由写入时写下的值决定，不由盘上有哪些 profile 决定。
OTHER_WORKSPACE = "uk-acme-somewhere-else"

#: 三个集合各自的（写入方法、读方法）。构造器见下面的 ``_make``。
_ACCESS: dict[str, tuple[str, str]] = {
    "evidence": ("put_evidence", "list_evidence"),
    # 路线那一族有两张表，于是有两个「一条」的读法：投影（现在是什么）与观察
    # （历来看到过什么）。**两个都要单独守**，因为两张表都可能被别的部署的行污染，
    # 而它们各自的读路径不会互相检查。
    "route": ("record_route", "list_routes"),
    "observation": ("record_route", "list_route_observations"),
    "opportunity": ("put_opportunity", "list_opportunities"),
}

#: 集合 → SQLite 的表名。只有 ``_smuggle`` 用。
_TABLES: dict[str, str] = {
    "evidence": "evidence",
    "route": "contact_route",
    "observation": "route_observation",
    "opportunity": "route_opportunity",
}

#: 外来行的 ``stored_at``。一个不是本进程写的行带着它自己那个时刻，而这三个实现
#: 都不按时钟读 —— 一个常量把「这条判据与时间无关」写在明处。
_STORED_AT = "2026-01-01T00:00:00+00:00"


def _make(
    kind: str,
    *,
    workspace: str = WORKSPACE,
    account_id: str = "a1",
    tag: str = "mine",
    **overrides,
) -> Evidence | ContactRoute | RouteOpportunity:
    """按集合构造一条。``tag`` 让两次构造**不是同一条** —— 证据的 id 是主键。"""
    overrides.setdefault("account_id", account_id)
    overrides.setdefault("workspace", workspace)
    if kind == "evidence":
        overrides.setdefault("evidence_id", f"ev-{tag}")
        overrides.setdefault("literal_value", f"{tag}@example.co.uk")
        return make_evidence(**overrides)
    if kind in ("route", "observation"):
        overrides.setdefault("email", f"{tag}@example.co.uk")
        # 两张表的对象**是同一个**（一次观察），差别只在往哪张表放 —— 而那正是
        # 这两条判据要说的事：同一个对象落在两个地方，两处都得守。
        return make_observation(**overrides)
    overrides.setdefault("role_title", f"{tag.capitalize()} Manager")
    return make_opportunity(**overrides)


def _put(store, kind: str, item) -> None:
    getattr(store, _ACCESS[kind][0])(item)


def _list(store, kind: str, account_id: str):
    return getattr(store, _ACCESS[kind][1])(account_id)


def _smuggle(store, kind: str, item, *, without: tuple[str, ...] = ()) -> None:
    """把**别人的**一行放进这个存储的盘上，绕过它自己的写路径。

    绕过去是**刻意的**，也是这一组判据唯一可能的构造方式：三个实现的写路径都拒绝
    别人的行（那是同一个契约的另一半，见下面的 ``test_writing_...``）。而真实世界
    里这种行不经过任何写入调用 —— 一次 ``cp -r state/``、一个拿错了
    ``MARKETOPS_DATA_ROOT`` 的进程、一个从别的部署搬过来的库，都会造出它。

    实现之间的差别只有**往哪放**：JSONL 是追加一行到 ``<account>.jsonl``，SQLite 是
    直接 ``INSERT``（用不着我们的写事务 —— 写它的那个进程没有我们的代码），内存实现
    是把手里的 model 塞进它的字典（它没有盘上的形态，所以这一条只能在内存里摆）。
    前两者用的是一个**局外写入者**的写法，不是本仓的 ``put_*``。

    ``without`` 去掉的那几格用来摆出另一半：**缺血统的行**（v1 写下的那些，或手工
    被删过那一格的）。内存实现摆不出这一半 —— 它的单位是模型实例，而模型上这一格
    必填（见 ``test_models_invariants.py``），那一侧的判据在 ``_put`` 上。
    """
    payload = item.model_dump(mode="json")
    for key in without:
        payload.pop(key, None)

    if isinstance(store, LocalEvidenceStore):
        directory = {
            "evidence": store._settings.evidence_dir,
            "route": store._settings.routes_dir,
            "observation": store._settings.route_observations_dir,
            "opportunity": store._settings.opportunities_dir,
        }[kind]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{item.account_id}.jsonl"
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return

    if isinstance(store, SqliteEvidenceStore):
        conn = sqlite3.connect(store._path)
        try:
            if kind == "evidence":
                conn.execute(
                    "INSERT INTO evidence (evidence_id, account_id, payload, stored_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        payload["evidence_id"],
                        item.account_id,
                        dump_payload(payload),
                        _STORED_AT,
                    ),
                )
            elif kind == "route":
                # 投影那一张的列比证据多：主键是规范键，还带一句血统与账号。
                conn.execute(
                    "INSERT INTO contact_route "
                    "(canonical_key, account_id, workspace, payload, stored_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        payload["canonical_key"],
                        item.account_id,
                        payload.get("workspace", ""),
                        dump_payload(payload),
                        _STORED_AT,
                    ),
                )
            elif kind == "observation":
                conn.execute(
                    "INSERT INTO route_observation "
                    "(observation_id, canonical_key, account_id, workspace, observed_at, "
                    " payload, stored_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        payload["observation_id"],
                        payload["canonical_key"],
                        item.account_id,
                        payload.get("workspace", ""),
                        payload["observed_at"],
                        dump_payload(payload),
                        _STORED_AT,
                    ),
                )
            else:
                conn.execute(
                    f"INSERT INTO {_TABLES[kind]} (account_id, payload, stored_at) "
                    "VALUES (?, ?, ?)",
                    (item.account_id, dump_payload(payload), _STORED_AT),
                )
            conn.commit()
        finally:
            conn.close()
        return

    assert not without, "内存实现摆不出「缺这一格」的行"
    if kind == "evidence":
        store._evidence.setdefault(item.account_id, {})[item.evidence_id] = item
    elif kind == "route":
        store._routes[item.canonical_key] = fold_observation(
            None, item, at=item.observed_at
        ).projection
    elif kind == "observation":
        store._observations.setdefault(item.account_id, {})[item.observation_id] = item
    else:
        store._opportunities.setdefault(item.account_id, []).append(item)


@pytest.mark.parametrize("kind", ["evidence", "route", "observation", "opportunity"])
def test_a_row_of_another_workspace_is_rejected_not_skipped(store, kind: str) -> None:
    """同一个账号下混着别人的行：**整次读报错**，不是把别人的那行跳过去。

    跳过去的话，调用点拿到的是「这个账号的、本部署的那些行」—— 一个**少了东西却
    看不出少了**的答案。而真正的处置在这里只有两种：把这一行移走，或者换一个存储。
    """
    _put(store, kind, _make(kind, account_id="a1", tag="mine"))
    _smuggle(
        store, kind, _make(kind, account_id="a1", tag="theirs", workspace=OTHER_WORKSPACE)
    )

    with pytest.raises(EvidenceError, match=OTHER_WORKSPACE):
        _list(store, kind, "a1")


@pytest.mark.parametrize("kind", ["evidence", "route", "observation", "opportunity"])
def test_a_foreign_only_account_raises_rather_than_returning_empty(store, kind: str) -> None:
    """这个账号下**只有**别人的行。跳过它们的话读出来是一个空列表。

    而那个空列表与「这个账号确实没有证据」在调用点长得**一模一样** —— 越权读于是
    变成一次静默成功，而报告里那句「这个账号没有证据」是假话。所以这一条判据的产出
    是拒绝，不是空。
    """
    # 先写一条本部署的（另一个账号）—— SQLite 与内存实现的绑定由第一行写入建立，
    # 少了这一步测的就成了「这个存储还没绑」，而不是「这一行不属于它」。
    _put(store, kind, _make(kind, account_id="a0", tag="mine"))
    _smuggle(
        store, kind, _make(kind, account_id="a1", tag="theirs", workspace=OTHER_WORKSPACE)
    )

    with pytest.raises(EvidenceError, match=OTHER_WORKSPACE):
        _list(store, kind, "a1")
    # 正对照：别人的行被拒**不等于**这个存储读不出东西 —— 本部署那一行照常读得出来，
    # 否则上面那句「拒绝」可能只是因为整个读路径坏掉了。
    assert len(_list(store, kind, "a0")) == 1


def test_another_workspaces_evidence_is_not_reachable_by_id(store) -> None:
    """按 id 取也一样：别人的行是**报错**，不是 ``None``。

    ``None`` 只剩一个意思 —— **没有这个 id 的行**。它不再是「有这一行但不给你看」：
    后者在这里变成「这条引用是假的」，而那是一句关于别人的数据的假话。

    ## 正对照为什么摆在别人的行落地**之前**

    ``adapters/jsonl`` 的 ``get_evidence`` 要扫**整个目录**（文件名是账号，不是 id ——
    见 ``ports/store.py`` 那条「三个实现的代价不一样」），所以那个目录里只要有一个
    别人的文件，**这一次进程里按 id 取的每一条都会拒绝**，不只那一条。这是刻意的
    方向（关得比需要的更紧，而不是更松），但它意味着「真的没有这一行」那个正对照
    必须在别人的行落地之前做，否则它测的是另一件事。SQLite 与内存实现按行判，没有
    这个性质 —— 判据留在这里对三个实现都成立的那一半。
    """
    assert store.get_evidence("ev-nobody-wrote-this") is None
    store.put_evidence(make_evidence(account_id="a0", evidence_id="ev-mine"))
    _smuggle(
        store,
        "evidence",
        make_evidence(account_id="a1", evidence_id="ev-theirs", workspace=OTHER_WORKSPACE),
    )

    with pytest.raises(EvidenceError, match=OTHER_WORKSPACE):
        store.get_evidence("ev-theirs")


@pytest.mark.parametrize("kind", ["evidence", "route", "observation", "opportunity"])
def test_writing_an_object_of_another_workspace_is_refused(store, kind: str) -> None:
    """写这一侧：别人的对象**写不进去**，而且一个字都没落下。

    这是上面那几条读判据的另一半 —— 如果一个存储能被写进别人的行，那「读的时候
    拒绝」就成了一道只有历史数据才会撞上的防线。
    """
    _put(store, kind, _make(kind, account_id="a0", tag="mine"))

    with pytest.raises(EvidenceError, match=OTHER_WORKSPACE):
        _put(
            store, kind, _make(kind, account_id="a1", tag="theirs", workspace=OTHER_WORKSPACE)
        )
    assert _list(store, kind, "a1") == []


@pytest.mark.parametrize("kind", ["evidence", "route", "observation", "opportunity"])
def test_a_row_that_lost_its_lineage_is_refused(store, kind: str) -> None:
    """**缺**这一格与**写着别人的名字**是两件事，而两件都不被读出来。

    缺这一格的行是 v1 写下的那些（或被人手工删过那一格）。它不属于任何人，所以
    按「当前部署」把它读出来正是把没有出处的行读成自己的 —— 模型层已经让新的行
    构造不出这个状态（见 ``test_models_invariants.py``），这里挡的是盘上的历史行。
    两个实现各自说得清这件事：SQLite 说这一格是 ``None``，JSONL 除了说这一格不在、
    还给出**怎么修**（跑一次迁移）。
    """
    if isinstance(store, InMemoryEvidenceStore):
        pytest.skip(
            "内存实现的一行就是一个模型实例，而模型上这一格必填 —— 缺这一格的行在"
            "这个实现上构造不出来（那一侧由 test_models_invariants.py 守）"
        )
    _put(store, kind, _make(kind, account_id="a0", tag="mine"))
    _smuggle(store, kind, _make(kind, account_id="a1", tag="theirs"), without=("workspace",))

    with pytest.raises(EvidenceError, match="没有 workspace|workspace=None"):
        _list(store, kind, "a1")


# --- 正对照 -------------------------------------------------------------------


class _BlindAppender(InMemoryEvidenceStore):
    """刻意做错的实现：不做幂等判断，照单全收。只用于下面的正对照。"""

    def __init__(self) -> None:
        super().__init__()
        self._flat: list[Evidence] = []

    def put_evidence(self, evidence: Evidence) -> None:
        self._flat.append(evidence)

    def list_evidence(self, account_id: str) -> list[Evidence]:
        return [item for item in self._flat if item.account_id == account_id]


def test_the_idempotency_criterion_actually_discriminates() -> None:
    """正对照：证明 ``== 1`` 这条判据不是空言。

    没有这一段，``test_put_evidence_is_idempotent`` 只是「某个实现碰巧去重了」；
    有了一个真的会重复计数的实现，才知道那条判据真的会红。
    """
    good = InMemoryEvidenceStore()
    broken = _BlindAppender()
    for target in (good, broken):
        for _ in range(3):
            target.put_evidence(make_evidence())

    assert len(good.list_evidence("a1")) == 1
    assert len(broken.list_evidence("a1")) == 3

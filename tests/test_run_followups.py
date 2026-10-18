"""§B24 那一格：``RunDelta.actions`` 的生产者、它的唯一性，以及那两条反例。

## 这一族要钉的三件事

1. **产出 → 待办**：一轮产出路线时，跟进池里真的多出一条
   ``VERIFY_REFERRAL_CONTACT``（挂在 ``ScopeKind.ROUTE`` 上，``scope_id`` 就是那条
   路线），而那一轮的 Delta 记下了它的 ``action_id``。
2. **没有产出就是没有**：
   ``test_a_run_with_no_referral_route_opens_no_item`` 是这一格的**反例**。一个
   「反正每轮都开一条」的实现会让 ``adapters/sqlite/db.py`` 那句「没有它，每个 Run
   都会新增一条「核实转介联系人」」从一句**约束的理由**变成一句对这个实现的描述 ——
   而后者在报告上只显示「待办多了一些」。
3. **重放不长出第二条**：判据是 ``(workspace, action_type, scope_ref)`` 那条 partial
   unique index，**不是**「写之前先查一下」—— 见
   ``test_the_second_attempt_is_refused_by_the_index_not_by_a_lookup``。

## 为什么「插入真的被拒过一次」值得一条判据

一个先查后插的实现与 ``_open_one`` 在**单进程**下的读数逐字相同（两条都只开一条、
都返回同一个 id），差别要等到两个写者之间那个窗口打开才看得见 —— 而测试里没有那个
窗口。所以这一条不判「结果一样」，它判**过程不一样**：数那次 ``create`` 有没有真的
发生、有没有真的被拒。这正是 ``ports/views.py::ActionItemStore.create`` 逐字要求的那
句话（「判据落在那条 partial unique index 上，不是『写之前先查一下』」）。

## ``action_id`` 必须是随机的，这件事在这里也有执行器

``domain/action_item.py::new_action_id`` 的 docstring 给了两条理由。第二条（判据必须
落在索引上、不能先撞主键）在这一族里读得出来：把 ``new_action_id`` 改成内容寻址的，
第二次开盘会先撞 ``action_id`` 主键，而那一条路返回的是「重发」——
``revision_written=None``、索引一次都不响，于是上面那条数「索引响了几次」的判据转红。

## 读回那一步为什么也要一条判据

``_open_one`` 在插入失败之后**读回**盘上那一条。它不参与判定，而它**参与重放**：
``ports/runs.py::stopping_outcome`` 比的是整份 Delta 的指纹，所以一次重放必须算出
**同一批** id。``test_replaying_the_whole_close_is_idempotent`` 判的就是这件事 ——
少了读回，一次正常的重放会被读成「同一轮以两种结局结束过」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path

import pytest

#: 读回面那一份投影函数（``run_handlers`` 里 ``get_run`` 返回的就是它，见
#: ``adapters/mcp_server/runs.py`` 的 ``"get_run": get_run`` 与那份 ``return {**_projection(run), ...}``）。
#: 与 ``tests/test_mcp_runs.py`` 直接 import ``_claim`` / ``_delta`` / ``_window`` 同一种用法。
from marketops.adapters.mcp_server.runs import _projection, run_handlers
from marketops.adapters.memory import InMemoryActionItemStore
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.adapters.sqlite.views import SqliteActionItemStore
from marketops.application.run_followups import (
    Followup,
    followups_for,
    open_followups,
)
from marketops.application.run_reconciliation import close_run
from marketops.config import Settings
from marketops.domain.action_item import (
    ActionItem,
    ActionItemState,
    ActionType,
    ScopeKind,
    scope_ref,
)
from marketops.domain.entities import VenueAsset
from marketops.domain.enums import BudgetLine, ExecutionMode, RunStatus
from marketops.domain.errors import (
    OpenActionItemExistsError,
    RunReconciliationError,
    RunStateError,
)
from marketops.domain.run import RunBudgetLine, RunSpec, run_id, spec_fingerprint
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta
from marketops.interfaces.composition import (
    default_action_item_store,
    default_asset_store,
    default_maintenance_store,
    default_run_store,
    default_scene_store,
)
from marketops.ports.views import ActionItemStore

WORKSPACE = "uk-acme-natural-grass"
KEY = "run-2026-09-28-c"
SCENE = "scene-devon"
T0 = datetime(2026, 9, 28, 9, 0, tzinfo=timezone.utc)
CUTOFF = T0 + timedelta(days=1)
STOPPED_AT = T0 + timedelta(hours=2)
RID = run_id(WORKSPACE, KEY)

#: 两条路线的主语。形状与 ``domain/route_records.py::canonical_route_key`` 的产物
#: 一致（``route-<16 位十六进制>``）—— 这一格收的是那一族的 id，而本模块**不重算**
#: 它（它由 ``application/contact_routes.py`` 那一层算出来）。
ROUTE_A = f"route-{'a' * 16}"
ROUTE_B = f"route-{'b' * 16}"
V1 = f"src-{'1' * 16}"


def _spec(**overrides: object) -> RunSpec:
    """与 ``tests/test_run_reconciliation.py::_spec`` 同形。

    工作区是 ``settings.profile``（两个文件都取那个场景）：收盘那一趟从**盘上那一行**
    取工作区，而读回面要求它与本部署的场景一致 —— 不一致的读数在那一层是一条拒绝。
    """
    payload: dict[str, object] = {
        "workspace": WORKSPACE,
        "objective": "补一轮 Devon 的场地",
        "selector": SCENE,
        "context_ref": "a" * 64,
        "plan_ref": "b" * 64,
        "execution_mode": ExecutionMode.MANUAL,
        "window": None,
        "budget": (RunBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "cutoff": CUTOFF,
        "policy_versions": ("grading@1f2e3d",),
        "idempotency_key": KEY,
    }
    payload.update(overrides)
    return RunSpec(**payload)  # type: ignore[arg-type]


def _effect(cell: DeltaCell, key: str) -> PoolEffect:
    """一条池子处置。``label`` 与 ``why`` 都给上（逐条去想「这一格要哪一个」是噪音）。"""
    return PoolEffect(cell=cell, key=key, label=f"{key} 那块场地", why=f"{key} 的原因")


def _asset(venue_asset_id: str) -> VenueAsset:
    """池子里的一行。第四节的最后一条要它：``UPDATED`` 那一格主张的那一行**得真的在**。"""
    return VenueAsset(
        workspace=WORKSPACE,
        venue_asset_id=venue_asset_id,
        name=f"{venue_asset_id} 的场地",
        first_seen_at=T0,
    )


def _open(
    items: ActionItemStore,
    routes: tuple[str, ...] = (),
    *,
    declared: tuple[str, ...] = (),
    workspace: str = WORKSPACE,
    run_id_: str = RID,
    at: datetime = STOPPED_AT,
) -> tuple[str, ...]:
    """走一次「收盘那一趟开待办」，把 ``delta.routes`` / ``delta.actions`` 拼出来。

    Delta 由本函数拼、不由调用方传：这一族要判的是**两个格子之间的关系**（路线进、
    actions 出），而让每个用例自己拼一份 Delta 会把那条关系写进每一处，于是它漂了
    也没有一处读得出来。
    """
    return open_followups(
        items=items,
        workspace=workspace,
        run_id=run_id_,
        delta=RunDelta(routes=routes, actions=declared),
        at=at,
    )


@pytest.fixture(params=["sqlite", "memory"])
def items(request: pytest.FixtureRequest, tmp_path: Path) -> ActionItemStore:
    """跟进池，两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。

    SQLite 那一份是**真的**部署里那一只（那条 partial unique index 由 DDL 执行），
    内存那一份把同一条判据换成一次显式查找（``adapters/memory.py`` 自己逐字写了这个
    代价）。两者都要跑，因为「重复创建是插入失败」这句话在部署上由索引执行，而在
    替身上只是**复现了它的答案**。
    """
    if request.param == "sqlite":
        return SqliteActionItemStore(tmp_path / "state" / "marketops.db")
    return InMemoryActionItemStore()


class _Recorder(ActionItemStore):
    """一条**数得清**的跟进池：包住真的一份，只数两件事。

    ``created`` 数的是「插入被真的试过几次」，``refused`` 数的是「被那条唯一性拒了几
    次」。这两格合起来才分得开「先查后插」与「插入失败」—— 两者在单进程下的**读数**
    逐字相同，差别只在这里。
    """

    def __init__(self, inner: ActionItemStore) -> None:
        self._inner = inner
        self.created = 0
        self.refused = 0

    def create(self, item: ActionItem):
        self.created += 1
        try:
            return self._inner.create(item)
        except OpenActionItemExistsError:
            self.refused += 1
            raise

    def resolve(self, item: ActionItem, *, expected_version: int):
        return self._inner.resolve(item, expected_version=expected_version)

    def get(self, action_id: str) -> ActionItem | None:
        return self._inner.get(action_id)

    def items(self, **kwargs: object) -> tuple[ActionItem, ...]:
        return self._inner.items(**kwargs)  # type: ignore[arg-type]


# --- 一、映射：哪一条变化 → 哪一类工作（纯函数） -------------------------------


def test_a_route_is_the_only_thing_that_opens_work() -> None:
    """一条路线 → 一条「核实转介联系人」，挂在**那一条路线**上。

    三条理由的正文在 ``application/run_followups.py`` 的模块 docstring 第一节；这条
    判据只把它们钉成一句话：主语是路线（不是账号、不是整个 Run），类型是那一个成员，
    而且**一条路线一件**（不是一批一件 —— 按批开一件会让「这条路线没人核实过」在盘上
    问不出来）。
    """
    assert followups_for((ROUTE_A,)) == (
        Followup(
            scope_kind=ScopeKind.ROUTE,
            scope_id=ROUTE_A,
            action_type=ActionType.VERIFY_REFERRAL_CONTACT,
        ),
    )


def test_no_referral_route_opens_nothing() -> None:
    """**反例（纯的那一半）。** 没有路线，就一条映射都成立不了。

    这一条判的是映射本身是一个**业务映射**，不是一个兜底分支：它的定义域是产出的
    路线，空定义域上它是空的。
    """
    assert followups_for(()) == ()


def test_the_routes_are_read_in_order_and_not_deduplicated() -> None:
    """保序、**不去重** —— 两句话各自有理由，而它们不是同一条。

    * 顺序留着：``close_run`` 按这个次序把 id 写回 Delta，而重放要算出同一串。
    * 去重不做：``routes`` 那一格自己的校验器已经拒了重复；在这里悄悄去一次，会让
      「这一轮到底产出了几条路线」在**这一层**读不出来（与
      ``RunDelta._ids_are_distinct_and_not_blank`` 逐字同一条纪律）。
    """
    three = followups_for((ROUTE_B, ROUTE_A, ROUTE_B))

    assert [one.scope_id for one in three] == [ROUTE_B, ROUTE_A, ROUTE_B]


# --- 二、落盘：一条路线真的开出一条待办 ---------------------------------------


def test_one_route_opens_one_item_on_that_route(items: ActionItemStore) -> None:
    """那一件真的在盘上，而它点着的 id 是**这一趟返回的**那一个。"""
    opened = _open(items, (ROUTE_A,))

    assert len(opened) == 1
    row = items.get(opened[0])
    assert row is not None
    assert row.action_type is ActionType.VERIFY_REFERRAL_CONTACT
    assert row.scope_ref == scope_ref(ScopeKind.ROUTE, ROUTE_A)
    assert row.workspace == WORKSPACE
    assert row.state is ActionItemState.OPEN
    assert row.owner is None
    # ``reason`` 说的是**这一轮做下了什么**（产出了这条路线），不是「这个联系人没有
    # 核实过」—— 后者本层判不了（一件核实做完之后，同一条路线可以再被产出一次）。
    assert ROUTE_A in row.reason and RID in row.reason


def test_a_run_with_no_referral_route_opens_no_item(items: ActionItemStore) -> None:
    """**反例（落盘的那一半，也是本片最要紧的一条）。**

    一份**有池子处置、但没有路线**的 Delta 收完盘之后，跟进池里一条待办都没有 ——
    包括「为这一轮本身开一条」这种兜底。一个「反正每轮都开一条」的实现会让
    ``adapters/sqlite/db.py`` 那句约束的理由反过来描述自己：每个 Run 都会新增一条
    「核实转介联系人」，而报告上看起来只是「待办多了一些」。
    """
    delta = RunDelta(effects=(_effect(DeltaCell.UPDATED, V1),))

    opened = open_followups(
        items=items,
        workspace=WORKSPACE,
        run_id=RID,
        delta=delta,
        at=STOPPED_AT,
    )

    assert opened == ()
    assert items.items(workspace=WORKSPACE, open_only=True) == ()
    # 一份**什么都没有**的池子与一份「读不出来」的池子不是同一件事：上面那一句问的
    # 是这一只池子（它刚刚被写过一次路）、而这一只池子今天在这条路上一次都没被写过。
    assert items.items(workspace=WORKSPACE) == ()


def test_replaying_the_same_route_does_not_open_a_second_item(
    items: ActionItemStore,
) -> None:
    """同一轮重放一次，池子里**还是一条**，而且拿到的是**同一个** id。

    第二句才是承重的：返回一个新的 id 也能让池子里只有一条（那一条会被索引挡掉），
    而盘上的 Delta 会在两趟之间指向两个不同的 action —— 那正是``stopping_outcome``
    会读成「同一轮以两种结局结束过」的那种 Delta。
    """
    first = _open(items, (ROUTE_A,))
    second = _open(items, (ROUTE_A,), at=STOPPED_AT + timedelta(hours=1))

    assert second == first
    assert len(items.items(workspace=WORKSPACE, open_only=True)) == 1


def test_the_second_attempt_is_refused_by_the_index_not_by_a_lookup(
    items: ActionItemStore,
) -> None:
    """**判据在哪。** 第二趟的 ``create`` 真的发生过、也真的被拒过。

    先查后插的实现会在这里留下 ``created == 1``：它查到有一条就返回，从不去写。两者
    在单进程下的结果逐字相同（都是一个 id、一条待办），所以这一条判的不是结果 ——
    它判那条 partial unique index **响过**。约束挡得住先查后插挡不住的那三条路
    （另一条路径、另一个进程、一次手写的 SQL），所以「响过」这件事才是这条唯一性的
    执行者。
    """
    recorder = _Recorder(items)

    first = _open(recorder, (ROUTE_A,))
    assert (recorder.created, recorder.refused) == (1, 0)

    second = _open(recorder, (ROUTE_A,))

    assert second == first
    assert (recorder.created, recorder.refused) == (2, 1), (
        "第二趟必须先真的插一次、被那条唯一性拒一次 —— 一次「查到就跳过」的实现"
        "读数是 (1, 0)，而它在两个写者之间会放行两条"
    )
    assert len(items.items(workspace=WORKSPACE, open_only=True)) == 1


# --- 三、``actions`` 那一格的并集与它的第三道闸 --------------------------------


def test_a_declared_id_that_reads_back_is_kept_and_comes_first(
    items: ActionItemStore,
) -> None:
    """调用方声明的那一串**不被丢掉**，而且排在前面。

    排序不是风格：``get_run`` → ``close_run`` 那条往返里，调用方交回来的 Delta 带着
    上一趟写下的 ``actions``，而重放要拿回**同一串**（``stopping_outcome`` 比整份
    Delta）。丢掉它 = 指纹变了 = 一次正常重放读成一次冲突；次序变了 = 同一件事。
    """
    existing = _open(items, (ROUTE_A,))[0]

    opened = _open(items, (ROUTE_B,), declared=(existing,))

    assert opened == (existing,) + tuple(
        one.action_id
        for one in items.items(workspace=WORKSPACE, open_only=True)
        if one.action_id != existing
    )
    assert len(opened) == 2
    assert items.get(opened[1]) is not None


def test_a_declared_id_that_does_not_read_back_refuses_the_close(
    items: ActionItemStore,
) -> None:
    """声明了一个读不回来的 id ⇒ 拒，而且**一件都没开**。

    这是 ``actions`` 比 ``routes`` / ``evidence`` / ``artifacts`` / ``reviews`` /
    ``usage`` 那五格**严**的地方，也是它唯一比它们多的一条判据：这一趟手里**就是**
    那只跟进池（``reconcile_run_delta`` 判不了那五格，因为它拿不到五个存储）。一条
    点着一个不存在的 action 的 Delta，在 ``get_run`` 上读起来与一条真的产出过待办的
    Delta 逐字相同，而它下面什么都没有。

    第二句判的是**次序**：这道闸在三道里排最前，所以一次被它拒的收盘在盘上一个字节都
    没落 —— 与「被拒的结束不留痕迹」那句话同款（``run_reconciliation`` 的三道闸）。
    """
    known = _open(items, (ROUTE_A,))[0]

    with pytest.raises(RunReconciliationError) as caught:
        _open(items, (ROUTE_B,), declared=(f"act-{'f' * 16}",))

    assert f"act-{'f' * 16}" in str(caught.value)
    # 被拒的这一趟**没有**把 ROUTE_B 开出来（否则「拒了」与「做了一半」就分不开）。
    assert [one.action_id for one in items.items(workspace=WORKSPACE, open_only=True)] == [
        known
    ]


# --- 四、收盘那一趟：第三格真的被写回来，而它读得回来 --------------------------


def _close(
    settings: Settings,
    *,
    status: RunStatus = RunStatus.COMPLETED,
    delta: RunDelta | None = None,
    at: datetime = STOPPED_AT,
    admit: bool = True,
):
    """用**组合根那一份接线**收一次盘：真库、真的三只存储、同一个 ``database_path``。

    三个存储必须落在同一个库上（``wired`` 那一段逐字写过的理由）：一次性折入是**两件
    事**（池子那一行与记在那一轮账上的同一笔），分在两个库上时测的是一台配错了的机器。
    """
    runs = default_run_store(settings)()
    assets = default_asset_store(settings)()
    items = default_action_item_store(settings)()
    if admit:
        spec = _spec()
        runs.admit(spec, spec_sha256=spec_fingerprint(spec), at=T0)
    stopped = close_run(
        runs=runs,
        assets=assets,
        items=items,
        run_id=RID,
        delta=RunDelta(routes=(ROUTE_A,)) if delta is None else delta,
        status=status,
        folded=(),
        at=at,
    )
    return runs, items, stopped


def test_the_close_pass_writes_the_cell_back_and_the_row_keeps_it(
    settings: Settings,
) -> None:
    """结束之后，盘上那一行的 Delta **带着** ``actions``，而它点着的 id 读得回来。

    三句各判一件事：这一格被写回来了（不是恒空）、写回来的就是盘上那一份（不是回执
    里那一份）、而它点着的 action **真的在**（否则这一格记的是一个不存在的 id ——
    那正是它要防的假回执）。
    """
    runs, items, stopped = _close(settings)

    assert stopped.delta is not None
    assert len(stopped.delta.actions) == 1

    row = runs.get(RID)
    assert row is not None and row.delta is not None
    assert row.delta.actions == stopped.delta.actions

    found = items.get(row.delta.actions[0])
    assert found is not None
    assert found.scope_ref == scope_ref(ScopeKind.ROUTE, ROUTE_A)


def test_the_read_path_reports_the_actions_cell(settings: Settings) -> None:
    """**有人读得到它。** 读回面报出来的那一格里就有 ``actions``。

    两条判据走的是两处**不同**的代码：``payload`` 那一句走 ``run_handlers`` 里那条
    真的工具（``"get_run": get_run``），而 ``_projection`` 那一句直接问那份**共用**的
    投影函数 —— 两者都要有，因为它们漂的方向不同：工具那一条判「读回面接上的是这一
    份投影」，投影那一条判「这一份投影里有这一格」（它同时是受理回执那一半）。
    """
    runs, items, stopped = _close(settings)
    handlers = run_handlers(
        settings=settings,
        scenes=default_scene_store(settings),
        maintenance=default_maintenance_store(settings),
        runs=default_run_store(settings),
        ledger=partial(SqliteBudgetLedger, settings.database_path),
        assets=default_asset_store(settings),
        followups=default_action_item_store(settings),
        accounts=None,
        cohorts=None,
    )

    payload = handlers["get_run"](RID)

    assert payload["delta"]["actions"] == list(stopped.delta.actions)
    assert payload["delta"]["actions"], "这一格必须在生产读路径上非空 —— 它刚刚被写回来"
    assert payload["workspace"] == WORKSPACE

    row = runs.get(RID)
    assert row is not None
    assert _projection(row)["delta"]["actions"] == list(stopped.delta.actions)
    assert items.get(payload["delta"]["actions"][0]) is not None


def test_replaying_the_whole_close_is_idempotent(settings: Settings) -> None:
    """**重放一整趟收盘**：拿到同一行、同一批 id，而池子里还是一条。

    这是「读回」那一步存在的理由：重放时 ``open_followups`` 再插一次、被那条索引拒掉、
    读回**同一个** id，于是写回去的 Delta 与盘上那一份逐字相同 —— ``stopping_outcome``
    的指纹也就相同，``stop`` 认出它是一次重放。少了读回那一步，这一趟会撞
    ``RunStateError``（「同一轮以两种结局结束过」），而它其实是一次正常重放。
    """
    runs, items, first = _close(settings)

    _, _, second = _close(
        settings, at=STOPPED_AT + timedelta(hours=1), admit=False
    )

    assert second.delta is not None and first.delta is not None
    assert second.delta.actions == first.delta.actions
    assert len(items.items(workspace=WORKSPACE, open_only=True)) == 1


def test_a_replay_that_is_not_a_replay_leaves_one_item_behind(
    settings: Settings,
) -> None:
    """**那个窗口如实记着。** 待办开完之后 ``stop`` 才拒时，那一条已经落盘了。

    唯一还够得着这一条的路径是「这一轮已经以另一套产出结束过」（换一个结局词就是
    另一套产出 —— ``stopping_outcome`` 的指纹含它）。它不是半个 Run：一件待办是一条
    独立的、有人要去做的活儿，而它的重复创建被那条索引挡住 —— 拿正确的 Delta 重做
    一次，同一主体上不会再长出第二条。下面两句判的正是这两半。
    """
    _, items, first = _close(settings)

    with pytest.raises(RunStateError):
        _close(settings, status=RunStatus.FAILED, at=STOPPED_AT + timedelta(hours=1))

    open_now = items.items(workspace=WORKSPACE, open_only=True)
    assert len(open_now) == 1

    # 拿**正确**的那一份重做一次：一条都不多长，而它拿回的是同一个 id。
    _, _, again = _close(settings, at=STOPPED_AT + timedelta(hours=2), admit=False)
    assert again.delta is not None and first.delta is not None
    assert again.delta.actions == first.delta.actions
    assert len(items.items(workspace=WORKSPACE, open_only=True)) == 1


def test_a_close_with_effects_but_no_route_lands_an_empty_cell(
    settings: Settings,
) -> None:
    """反例的**另一半**：§2 那条反例接到真的收盘那一趟上。

    纯函数那一半判的是映射（没有路线就没有映射），这一半判的是**收盘这一趟真的没有
    别的地方在开待办** —— 一份有池子处置、没有路线的 Delta 走完整趟之后，盘上那一
    行的 ``actions`` 是**空的**（不是「还没来得及写」：那一行已经结束了）。

    池子那一格同时**真的**参与了这一趟：``UPDATED`` 主张池子里有那一行，所以池子里
    得先真的有它（``upsert_asset``），否则整趟会被第一道闸拒 —— 而「被拒」与「开了
    零条」在这里会读成同一件事，这条判据也就是空的了。
    """
    runs = default_run_store(settings)()
    assets = default_asset_store(settings)()
    items = default_action_item_store(settings)()
    spec = _spec()
    runs.admit(spec, spec_sha256=spec_fingerprint(spec), at=T0)
    assets.upsert_asset(_asset(V1))

    stopped = close_run(
        runs=runs,
        assets=assets,
        items=items,
        run_id=RID,
        delta=RunDelta(effects=(_effect(DeltaCell.UPDATED, V1),)),
        status=RunStatus.COMPLETED,
        folded=(),
        at=STOPPED_AT,
    )

    assert stopped.delta is not None
    assert stopped.delta.actions == ()
    row = runs.get(RID)
    assert row is not None and row.delta is not None
    assert row.delta.actions == ()
    assert items.items(workspace=WORKSPACE, open_only=True) == ()

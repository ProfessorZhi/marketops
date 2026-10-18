"""``application/views.py`` 的判据：**两件必须如实报出来的事**，以及它们周围的构造。

## 这一份里承重的是那两句验收口径

``TASK-C.md`` 逐字：

* ``run_saved_view``——「同一个 Account 出现在三个视图里是**一个 Account 读了三次**，
  不是三行；一份跑不动的查询要点名**是哪一部分**跑不动，**不是少回几行**」；
* ``list_watches``——「目标视图已被删掉的 Watch 要**如实报成那个样子**，不是静默变
  不活跃」。

三句各有各的判据，而且每一句都有一个**看起来一样**的错法要挡住：

* 「读了三次」的对立面是一个已经读过就不再读的优化 —— 它把 ``accounts_read`` 从 3
  变成 1，于是「同一个账号被读了三遍」这件事在回执上再也读不出来。见
  ``test_each_view_reads_the_pool_itself``。
* 「点名是哪一部分」的对立面是「把活下来的那几条拼起来跑」—— 它会回一批看起来正常
  的行，而它比调用方写的查询小。见 ``test_a_query_that_cannot_run_names_the_part_and_returns_no_rows``。
* 「如实报成那个样子」的对立面是把 ``view_missing`` 折成 ``enabled=False`` ——
  那两句的处置分别是「重新存一份视图指过去」与「去问为什么关的」。见
  ``test_a_watch_that_points_at_a_deleted_view_is_reported_as_that``。

## 三件尺寸上做小了的构造，各有判据看着

* **零预算**：``put_saved_view`` 的签名里没有账本；``put_watch`` 要两格（域那一侧判空）
  且必须在**在跑**的场景上。这一条是承重点 1 的**行为**半边（**签名**半边在
  ``tests/test_mcp_views.py``：工具面那一层才是「能不能传进来」的落点）。
* **private 只在 ``run_saved_view`` 上执行**：存下来的那一格只有在产生行的那一刻才
  说话 —— 见 ``test_a_private_view_is_refused_to_anyone_but_its_owner``。它**只**执行
  在那一处（``get_saved_view`` / ``list_saved_views`` 不看它），这是今天的设计，如实
  记在报告里，不在这里补判据。
* **``scope_ref`` 算出来**：它不是 ``open_action_item`` 的参数 —— 一个手拼的实现在
  ``#`` 的哪一边放什么都可能，而那条唯一索引盖的正是它。

## 这一份不重测的东西

* 三个 store 的写语义（幂等边界、CAS、名字判据）—— ``tests/test_views_store_contract.py``
  与 ``tests/test_sqlite_views.py``；
* 工具面（回执形状、两个 ``_CONTROL`` 工具没有缺省值）—— ``tests/test_mcp_views.py``；
* 这一族的 import 闭包到不到得了检索/抓取端口 —— ``tests/test_mcp_views_are_offline.py``；
* 域那一层的编译与投影（``compile_view_query`` / ``project_accounts`` 自己的判据）——
  它们在那里各有一份。
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import (
    InMemoryAccountPoolStore,
    InMemoryActionItemStore,
    InMemorySavedViewStore,
    InMemorySceneStore,
    InMemoryWatchStore,
)
from marketops.application.accounts import merge_accounts
from marketops.application.views import (
    get_saved_view,
    list_action_items,
    list_saved_views,
    list_watches,
    open_action_item,
    put_saved_view,
    put_watch,
    record_watch_check,
    resolve_action_item,
    run_saved_view,
    run_saved_views,
    watch_card,
    watch_due_requests,
)
from marketops.domain.account import MarketAccount
from marketops.domain.action_item import (
    ActionItemState,
    ActionType,
    ScopeKind,
    scope_ref,
    split_scope_ref,
)
from marketops.domain.enums import AccountStatus, BudgetLine
from marketops.domain.errors import (
    ActionItemConflictError,
    ConfigError,
    OpenActionItemExistsError,
    SavedViewConflictError,
    StoreError,
)
from marketops.domain.saved_view import ViewVisibility
from marketops.domain.scene import PauseReason, SceneRecord, SceneState
from marketops.domain.watch import NotifyOn, WatchCeiling, WatchTargetState

UTC = timezone.utc
T0 = datetime(2026, 9, 27, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
OWNER = "member-b"
OTHER_OWNER = "member-d"
SCENE = "scene-grass"
CEILING = WatchCeiling(line=BudgetLine.SEARCH, units=10)


# ---------------------------------------------------------------------- 夹具
def make_account(
    account_id: str,
    *,
    country: str | None = "GB",
    status: AccountStatus = AccountStatus.RESEARCHED,
    **overrides,
) -> MarketAccount:
    """一行账号。``evidence_ids`` 不是可省的 —— ``MarketAccount`` 拒收一条没有证据的行
    （「它会读成『我们查到了』，而事实是『我们猜过』」）。"""
    fields = {
        "workspace": WORKSPACE,
        "account_id": account_id,
        "organisation": f"Org {account_id}",
        "evidence_ids": ("ev-1",),
        "first_seen_at": T0,
        "country": country,
        "status": status,
    }
    fields.update(overrides)
    return MarketAccount(**fields)


@pytest.fixture
def pool() -> InMemoryAccountPoolStore:
    """三个账号，``country`` 各是**有值 / 没查过 / 查过是没有** ——
    渲染那一条判据要的就是这三档（``None`` 与 ``""`` 不是同一句话）。"""
    store = InMemoryAccountPoolStore(merge_accounts)
    store.upsert_account(make_account("acct-1", country="GB"))
    store.upsert_account(make_account("acct-2", country="IE"))
    store.upsert_account(make_account("acct-3", country=None))
    return store


@pytest.fixture
def stores() -> tuple[InMemorySavedViewStore, InMemoryWatchStore, InMemoryActionItemStore]:
    return InMemorySavedViewStore(), InMemoryWatchStore(), InMemoryActionItemStore()


@pytest.fixture
def views(stores) -> InMemorySavedViewStore:
    return stores[0]


@pytest.fixture
def watches(stores) -> InMemoryWatchStore:
    return stores[1]


@pytest.fixture
def items(stores) -> InMemoryActionItemStore:
    return stores[2]


def make_scene(
    state: SceneState = SceneState.ACTIVE, *, scene_id: str = SCENE, workspace: str = WORKSPACE
) -> InMemorySceneStore:
    """盘上那一版场景。``_require_scene_active`` 读的就是它。"""
    store = InMemorySceneStore()
    store.put_record(
        SceneRecord(
            scene_id=scene_id,
            workspace=workspace,
            state=state,
            latest_version=1,
            effective_version=None if state is SceneState.DRAFT else 1,
            created_at=T0,
            updated_at=T0,
            pause_reason=PauseReason.OPERATOR if state is SceneState.PAUSED else None,
        ),
        expected_revision=None,
    )
    return store


@pytest.fixture
def scene() -> InMemorySceneStore:
    return make_scene()


def save(
    views,
    view_id: str,
    *,
    query: dict | None = None,
    owner: str = OWNER,
    name: str | None = None,
    visibility: ViewVisibility = ViewVisibility.SHARED,
):
    return put_saved_view(
        store=views,
        view_id=view_id,
        workspace=WORKSPACE,
        owner=owner,
        name=name or f"视图 {view_id}",
        query={"filter": []} if query is None else query,
        expected_revision=None,
        visibility=visibility,
        at=T0,
    )


def open_watch(
    watches,
    scene_store,
    watch_id: str,
    *,
    view_id: str | None = None,
    subjects: tuple[tuple[str, str], ...] = (),
    next_due_at: datetime | None = T0,
    enabled: bool = True,
    check_every_days: int = 7,
    units: int = 10,
):
    return put_watch(
        store=watches,
        scene_store=scene_store,
        scene_id=SCENE,
        watch_id=watch_id,
        workspace=WORKSPACE,
        owner=OWNER,
        name=f"监控 {watch_id}",
        ceilings=(WatchCeiling(line=BudgetLine.SEARCH, units=units),),
        notify_on=(NotifyOn.CHANGED,),
        view_id=view_id,
        subjects=subjects,
        check_every_days=check_every_days,
        next_due_at=next_due_at,
        enabled=enabled,
        at=T0,
    )


# ------------------------------------- 承重点 1：谁要预算、谁要权限（行为半边）
def test_a_paused_scene_stops_a_watch_but_not_a_saved_view(
    views, watches, scene, pool
) -> None:
    """同一次暂停之下，**存视图照样可以，开 Watch 被拒**。

    这一条是承重点 1 的行为半边（签名半边在 ``tests/test_mcp_views.py``）。两件事
    各自都有一个看起来一样的错法：

    * 让存视图也要一个场景 ⇒ 存取一份**查询**变成了要有在跑的工作流才行，而 §5.2
      逐字「保存 View 不是」；
    * 让开 Watch 不问场景 ⇒ 盘上会躺着一条「用户以为开着、一次都不会响」的记录，
      而排队比拒绝更坏，因为它让「我明明开了监控」与「它一次都不会响」同时为真。
    """
    paused = make_scene(SceneState.PAUSED)

    saved = save(views, "view-anyway", query={"filter": []})
    assert saved.revision_written == 1

    with pytest.raises(ConfigError) as caught:
        open_watch(watches, paused, "watch-never")
    message = str(caught.value)
    assert SceneState.PAUSED.value in message
    assert SCENE in message
    # 被拒之后盘上**什么都没有**：一条开不出来的 Watch 不该留下半行。
    assert watches.get_watch("watch-never") is None


def test_a_watch_on_a_scene_that_does_not_exist_names_the_scene(watches, views) -> None:
    """场景不在盘上，与场景被暂停是**两句**拒绝（处置不同：接线 vs 等它恢复）。"""
    empty = InMemorySceneStore()
    with pytest.raises(ConfigError) as caught:
        open_watch(watches, empty, "watch-nowhere")
    assert "不在盘上" in str(caught.value)
    assert SCENE in str(caught.value)


def test_a_scene_from_another_workspace_cannot_be_the_target(watches) -> None:
    """场景属于另一个工作区 ⇒ 拒绝，而不是把这条 Watch 登记到一个不属于它的场景上。"""
    foreign = make_scene(SceneState.ACTIVE, workspace="uk-something-else")
    with pytest.raises(StoreError) as caught:
        open_watch(watches, foreign, "watch-foreign", view_id="view-a")
    assert "uk-something-else" in str(caught.value)
    assert WORKSPACE in str(caught.value)


def test_enabling_a_watch_asks_for_both_ceilings_and_notify_on() -> None:
    """签名上的那两格：``put_watch`` 收 ``ceilings`` 与 ``notify_on``，``put_saved_view``
    一个都不收 —— 而这不是风格，是承重点 1 在代码上的形状。

    空值由**域**那一侧拒（``Watch`` 的两个校验器），所以这里判的是「这两格在签名里」，
    行为那半边在上一条里。
    """
    saved_params = inspect.signature(put_saved_view).parameters
    watch_params = inspect.signature(put_watch).parameters
    assert {"ceilings", "notify_on"} <= set(watch_params)
    assert not ({"ceilings", "notify_on", "ledger", "budget"} & set(saved_params)), (
        "存一份视图的签名里出现了预算那一格 —— §5.2 逐字「SavedView 只是查询，"
        "不产生外部调用、不产生预算」"
    )
    # 而**签名栏里的**那两格只判空值是不够的：一个把它们收下就丢掉的实现照样过。
    # 「收下并写进盘」的判据在下一条。
    assert watch_params["scene_store"].default is inspect.Parameter.empty
    assert watch_params["scene_id"].default is inspect.Parameter.empty


def test_what_the_watch_was_open_with_lands_on_disk(watches, scene) -> None:
    """上限与通知时机**写进盘**（不是收下就丢）。没有这一条，上一条只证了一个签名。"""
    written = open_watch(watches, scene, "watch-1", view_id="view-a", units=42)
    assert written.watch.ceilings == (WatchCeiling(line=BudgetLine.SEARCH, units=42),)
    assert written.watch.notify_on == (NotifyOn.CHANGED,)
    assert watches.get_watch("watch-1").ceilings == written.watch.ceilings


# --------------------------------------------------- 承重点 6 之外的视图读写
def test_the_owner_of_a_view_is_not_a_field_a_write_can_change(views) -> None:
    """``owner`` 是 ``(workspace, owner, name)`` 的一半 —— 换它不是一次普通修改。

    在这里判它是为了判**那一句拒绝说了什么**：消息里两个 owner 都要在，否则它读起来
    像一次「名字被占了」。
    """
    save(views, "view-1", owner=OWNER)
    with pytest.raises(SavedViewConflictError) as caught:
        put_saved_view(
            store=views,
            view_id="view-1",
            workspace=WORKSPACE,
            owner=OTHER_OWNER,
            name="换个人",
            query={"filter": []},
            expected_revision=1,
            at=T0,
        )
    message = str(caught.value)
    assert OWNER in message and OTHER_OWNER in message
    assert views.current("view-1").owner == OWNER


def test_reading_a_view_that_was_never_saved_names_the_id(views, pool) -> None:
    """不存在与「读失败」是两件事 —— 而且它要说的是「去看是不是抄错了」。"""
    for call in (
        lambda: get_saved_view(store=views, view_id="view-typo"),
        lambda: run_saved_view(views=views, pool=pool, view_id="view-typo", actor=OWNER),
    ):
        with pytest.raises(ConfigError) as caught:
            call()
        assert "view-typo" in str(caught.value)


def test_listing_views_without_an_owner_filter_is_not_only_mine(views) -> None:
    """``owner=None`` = 不过滤。一个「缺省只看我的」的实现在报告上少一整批视图。"""
    save(views, "view-mine", owner=OWNER)
    save(views, "view-theirs", owner=OTHER_OWNER)
    assert {v.view_id for v in list_saved_views(store=views, workspace=WORKSPACE)} == {
        "view-mine",
        "view-theirs",
    }
    assert {v.view_id for v in list_saved_views(store=views, workspace=WORKSPACE, owner=OWNER)} == {
        "view-mine"
    }


# ------------------------------- 如实报之一：一个 Account 读了三次，不是一个账号三份
def test_one_account_in_three_views_is_one_account_read_three_times(views, pool) -> None:
    """三份视图盯着同一个池子 —— 池子里是**三个**账号，读出来是**九行引用**。

    §5.2 逐字：「一个 Account 可同时出现在多个 View；移动/读某个 View 不复制 Account
    行」。所以这一条有两个半边，缺一个都能让一个错实现绿：

    * ``accounts_read`` 三份各是三 ⇒ 那一个账号被读了三遍（不是一遍、不是零遍）；
    * 三份回执里的 ``account_id`` 是**同一批三个** ⇒ 出现了九行，而账号仍然只有三个，
      池子里也仍然只有三行（读不复制）。
    """
    for view_id in ("view-a", "view-b", "view-c"):
        save(views, view_id, query={"filter": []})

    runs = run_saved_views(
        views=views, pool=pool, view_ids=("view-a", "view-b", "view-c"), actor=OWNER
    )

    assert [run.accounts_read for run in runs] == [3, 3, 3]
    assert sum(run.accounts_read for run in runs) == 9
    references = [row.account_id for run in runs for row in run.rows]
    assert len(references) == 9
    assert set(references) == {"acct-1", "acct-2", "acct-3"}, (
        "九行引用必须是同一个账号被引用九次，而不是九个账号 —— 回执里留着 ``account_id`` "
        "正是为了让这一条验得了"
    )
    assert len(pool.list_accounts()) == 3, "读一份视图不复制账号行"


def test_each_view_reads_the_pool_itself(views, pool) -> None:
    """``accounts_read`` 是**这一趟从池子里读回来的行数**，不是 ``len(rows)``。

    两条判据，各挡一个看起来一样的错法：

    * 一份视图只筛出一个账号时，``accounts_read`` **仍然是 3** —— 被筛掉的那些也读过
      了。一个把它写成 ``len(rows)`` 的实现会让「读了多少」在报告上跟着结果走。
    * 三份视图各读一遍，而不是第一份读过就缓存 —— 那个优化会把 ``accounts_read``
      从 ``[3, 3, 3]`` 变成 ``[3, 0, 0]``，于是「同一个账号被读了三遍」这件事在回执上
      再也读不出来（``application/views.py::run_saved_views`` 逐字）。
    """
    save(views, "view-one", query={"filter": [{"field": "country", "op": "is", "value": "GB"}]})
    save(views, "view-two", query={"filter": []})
    save(views, "view-three", query={"filter": []})

    runs = run_saved_views(
        views=views, pool=pool, view_ids=("view-one", "view-two", "view-three"), actor=OWNER
    )

    assert len(runs[0].rows) == 1 and runs[0].accounts_read == 3
    assert [run.accounts_read for run in runs] == [3, 3, 3]


# ------------------------------- 如实报之一（另一半）：跑不动就点名，不是少回几行
def test_a_query_that_cannot_run_names_the_part_and_returns_no_rows(views, pool) -> None:
    """一份**一半能跑**的查询回**零行**，并且点名是**哪一部分**跑不动。

    这份查询的第一条谓词是好的（它真的筛得中 ``acct-1``），第二条不认识。一个「把活
    下来的那几条拼起来跑」的实现会回一行看起来完全正常的 ``acct-1`` —— 而它比调用方
    写的那份查询**小**，且少回的那一行与「本来就没有」在报告上同形
    （``CompileOutcome`` 的 docstring 逐字）。所以这里两条都要判：``rows == ()``
    与每一处 ``part`` 都在拒绝措辞里。
    """
    save(
        views,
        "view-half",
        query={
            "filter": [
                {"field": "country", "op": "is", "value": "GB"},
                {"field": "nope", "op": "is", "value": "x"},
            ]
        },
    )

    run = run_saved_view(views=views, pool=pool, view_id="view-half", actor=OWNER)

    assert run.ok is False and run.refusal
    assert run.rows == (), "跑不动就一行都不回 —— 把好的那几条拼起来跑会回一批看起来正常的行"
    assert run.compiled is None
    assert [part.part for part in run.unrunnable] == ["filter[1].field"]
    for part in run.unrunnable:
        assert part.part in run.refusal
        assert part.reason in run.refusal
    assert run.view_id == "view-half" and run.view_revision == 1


def test_every_broken_part_is_named_not_just_the_first(views, pool) -> None:
    """三处跑不动 ⇒ 三处都点名。只报第一处的实现在「改完一个再跑一次」上要来回三趟。"""
    save(
        views,
        "view-three-broken",
        query={
            "subject_kind": "venue",
            "filter": [{"field": "country", "op": "eq"}],
            "limit": 0,
        },
    )
    run = run_saved_view(views=views, pool=pool, view_id="view-three-broken", actor=OWNER)
    assert [part.part for part in run.unrunnable] == [
        "subject_kind",
        "filter[0].op",
        "limit",
    ]
    assert all(part.reason in run.refusal for part in run.unrunnable)


def test_one_broken_view_does_not_stop_the_others(views, pool) -> None:
    """一份跑不动**不整批抛** —— 整批抛会让调用方看不到哪些是好的，而次序也要保留
    （回执与 ``view_ids`` 一一对应是调用方唯一的对位方式）。"""
    save(views, "view-broken", query={"filter": [{"field": "nope", "op": "is", "value": "x"}]})
    save(views, "view-fine", query={"filter": []})

    runs = run_saved_views(
        views=views, pool=pool, view_ids=("view-broken", "view-fine"), actor=OWNER
    )

    assert [run.view_id for run in runs] == ["view-broken", "view-fine"]
    assert runs[0].ok is False
    assert runs[1].ok is True and len(runs[1].rows) == 3


# ------------------------------------------------------- 三个桶与一行的渲染
def test_a_row_with_no_answer_is_a_third_bucket(views) -> None:
    """``country is_not "GB"`` 遇上一行 ``country=None``：它既不是命中也不是被丢掉。

    算成命中 ⇒ 没查过的行混进结果；算成不命中 ⇒ 它静默消失。两个方向都会让报告说
    「按国家筛过了」而其实没有（``domain/saved_view.py::_verdict`` 逐字）。
    """
    pool = InMemoryAccountPoolStore(merge_accounts)
    pool.upsert_account(make_account("acct-gb", country="GB"))
    pool.upsert_account(make_account("acct-ie", country="IE"))
    pool.upsert_account(make_account("acct-unknown", country=None))
    save(views, "view-not-gb", query={"filter": [{"field": "country", "op": "is_not", "value": "GB"}]})

    run = run_saved_view(views=views, pool=pool, view_id="view-not-gb", actor=OWNER)

    assert [row.account_id for row in run.rows] == ["acct-ie"]
    assert run.undecided == 1
    assert run.dropped == 0
    assert "acct-unknown" not in {row.account_id for row in run.rows}
    assert run.accounts_read == 3, "判不了的那一行**读过**了"


def test_a_limit_reports_what_it_dropped(views, pool) -> None:
    """截断必须被记录：一份「回了 1 行」与「本来就只有 1 行」在报告上同形。"""
    save(views, "view-limited", query={"filter": [], "limit": 1})
    run = run_saved_view(views=views, pool=pool, view_id="view-limited", actor=OWNER)
    assert len(run.rows) == 1
    assert run.dropped == 2
    assert run.undecided == 0
    assert run.accounts_read == 3


def test_a_row_is_a_reference_so_it_always_carries_the_account_id(views, pool) -> None:
    """``columns=[]`` 时回执里**仍然**有主语。

    一个只按点名的那几列渲染的实现在这里回一行没有主语的文本 —— 而那种行没法与池子里
    那一行对上，「读了三次」这句话就再也验不了（``ViewRow`` 的 docstring 逐字）。
    """
    save(views, "view-nocols", query={"columns": []})
    run = run_saved_view(views=views, pool=pool, view_id="view-nocols", actor=OWNER)
    assert [row.account_id for row in run.rows] == ["acct-1", "acct-2", "acct-3"]
    assert all(row.cells == () for row in run.rows)


def test_unconfirmed_and_checked_empty_render_differently(views) -> None:
    """``None``（还没查）与 ``""``（查了，是没有）在回执里不是同一串字。

    把两者都渲染成空会让一份「按国家筛」的结果把「没查过」读成「没有」——
    ``domain/account.py::MarketAccount.country`` 那一格逐字。
    """
    pool = InMemoryAccountPoolStore(merge_accounts)
    pool.upsert_account(make_account("acct-gb", country="GB"))
    pool.upsert_account(make_account("acct-unknown", country=None))
    pool.upsert_account(make_account("acct-empty", country=""))
    save(views, "view-country", query={"columns": ["country"]})

    run = run_saved_view(views=views, pool=pool, view_id="view-country", actor=OWNER)
    rendered = {
        row.account_id: dict(row.cells)["country"] for row in run.rows
    }

    assert rendered["acct-gb"] == "GB"
    assert rendered["acct-unknown"] == "未确认"
    assert rendered["acct-empty"] == "(查过：没有)"
    assert rendered["acct-unknown"] != rendered["acct-empty"]


# ------------------------------------------------------------- private 的执行点
def test_a_private_view_is_refused_to_anyone_but_its_owner(views, pool) -> None:
    """private 存下来之后**有一个执行者**：产生行的那一处（``run_saved_view``）。

    一个只把 private 存下来而从不读它的实现，会让这个字段在报告上看起来生效过 ——
    判据是同一个 id 在两个人手里给出两个答案，而**行**只在允许的那一个那里出现。
    """
    save(
        views,
        "view-private",
        query={"filter": []},
        owner=OWNER,
        visibility=ViewVisibility.PRIVATE,
    )
    assert len(run_saved_view(views=views, pool=pool, view_id="view-private", actor=OWNER).rows) == 3

    with pytest.raises(ConfigError) as caught:
        run_saved_view(views=views, pool=pool, view_id="view-private", actor=OTHER_OWNER)
    message = str(caught.value)
    assert OWNER in message and OTHER_OWNER in message
    assert "private" in message


# ------------------------------------- 如实报之二：目标视图没了的 Watch 要那样报
def test_a_watch_that_points_at_a_deleted_view_is_reported_as_that(views, watches, scene) -> None:
    """**两句话必须分得开**：有人关掉了它 / 它指的东西没有了。

    所以这里摆的是三张卡，而它们的处置各不相同：

    * ``watch-gone``：开着（``enabled=True``）而目标视图不在 ⇒ ``VIEW_MISSING``。
      把它渲染成「不活跃」的实现在这里红 —— 因为它的 ``enabled`` 是真的 ``True``。
    * ``watch-off``：被关掉了，而目标视图在 ⇒ **不是** ``VIEW_MISSING``。
    * ``watch-subjects``：不指视图，直接点名主体 ⇒ ``SUBJECTS``，而它**不是**
      「视图在」的同义词（``None`` 不折成 ``False``）。
    """
    save(views, "view-live", query={"filter": []})
    open_watch(watches, scene, "watch-live", view_id="view-live")
    open_watch(watches, scene, "watch-gone", view_id="view-never-existed")
    open_watch(watches, scene, "watch-off", view_id="view-live", enabled=False)
    open_watch(watches, scene, "watch-subjects", subjects=(("account", "acct-1"),))

    cards = {card.watch.watch_id: card for card in list_watches(
        store=watches, views=views, workspace=WORKSPACE
    )}

    gone = cards["watch-gone"]
    assert gone.target_state is WatchTargetState.VIEW_MISSING
    assert gone.target_missing is True
    assert gone.enabled is True, "它**开着** —— 说它「不活跃」就是漏掉了真正的那件事"

    assert cards["watch-live"].target_state is WatchTargetState.VIEW
    assert cards["watch-live"].target_missing is False
    assert cards["watch-off"].enabled is False
    assert cards["watch-off"].target_missing is False, (
        "关掉的那一条指着一条**存在**的视图 —— 它与目标没了的处置不同"
    )
    assert cards["watch-subjects"].target_state is WatchTargetState.SUBJECTS
    assert cards["watch-subjects"].target_missing is False


def test_the_card_definition_is_shared_with_the_write_receipt(watches, views, scene) -> None:
    """``watch_card`` 是**一处定义**：清单与写入回执问的是同一句话。

    两处各写一遍的那天，「刚开的这一条指着一条不存在的视图」与「清单里这一条指着一条
    不存在的视图」会给出两个答案 —— 而其中只有一个会随着目标视图被删掉而变。
    """
    written = open_watch(watches, scene, "watch-gone", view_id="view-never-existed")
    fresh = watch_card(watch=written.watch, view_exists=views.current("view-never-existed") is not None)
    assert fresh.target_state is WatchTargetState.VIEW_MISSING
    # 清单里那一条是同一个答案（同一份定义）。
    listed = next(
        card
        for card in list_watches(store=watches, views=views, workspace=WORKSPACE)
        if card.watch.watch_id == "watch-gone"
    )
    assert listed.target_state is fresh.target_state
    assert listed.state_note == fresh.state_note


def test_the_due_sweep_separates_what_can_run_from_what_cannot(views, watches, scene) -> None:
    """到期的里面：**执行得了的**与**执行不了的**分开报，而不是只回前者。

    一份被丢掉的到期请求在报告上等于「这条 Watch 没什么要做的」，而真相是「它指着的
    东西没有了，没人知道」（``WatchDueRequest`` 的 docstring 逐字）。所以执行不了的那一
    份照样回来，只是 ``subjects`` 空、``blocked_reason`` 非空。

    这一层**不创建任何 Run**：它只产生请求。
    """
    save(views, "view-live", query={"filter": []})
    open_watch(watches, scene, "watch-live", view_id="view-live")
    open_watch(watches, scene, "watch-gone", view_id="view-never-existed")
    open_watch(watches, scene, "watch-subjects", subjects=(("account", "acct-1"),))
    open_watch(watches, scene, "watch-off", view_id="view-live", enabled=False)
    open_watch(
        watches,
        scene,
        "watch-later",
        view_id="view-live",
        next_due_at=T0 + timedelta(days=1),
    )

    sweep = watch_due_requests(
        store=watches, views=views, workspace=WORKSPACE, at=T0
    )

    assert {request.watch_id for request in sweep.servable} == {"watch-live", "watch-subjects"}
    assert {request.watch_id for request in sweep.blocked} == {"watch-gone"}
    blocked = sweep.blocked[0]
    assert blocked.subjects == ()
    assert blocked.view_id == "view-never-existed"
    assert "view-never-existed" in blocked.blocked_reason
    # 关掉的与还没到期的**两份都不在** —— 不是被塞进 blocked。
    assert "watch-off" not in {r.watch_id for r in sweep.servable + sweep.blocked}
    assert "watch-later" not in {r.watch_id for r in sweep.servable + sweep.blocked}
    assert sweep.empty is False

    empty = watch_due_requests(
        store=watches, views=views, workspace=WORKSPACE, at=T0 - timedelta(days=1)
    )
    assert empty.servable == () and empty.blocked == () and empty.empty is True


def test_recording_a_check_advances_the_due_moment_without_touching_the_rule(
    watches, scene
) -> None:
    """看完一次推的是 ``next_due_at``，**不动**规则那几格。

    两条路（``after_check`` 与 ``next_revision``）都不许自己顺手把两格一起推：那样写出来
    的实现在「只改了规则」与「只看了一次」两种情况下盘上都长一样，而它们该不该重置到期
    时刻是相反的（``domain/watch.py::next_revision`` 逐字）。
    """
    open_watch(watches, scene, "watch-live", view_id="view-a", check_every_days=7, units=42)

    written = record_watch_check(
        store=watches, watch_id="watch-live", expected_revision=1, at=T0 + timedelta(days=1)
    )

    assert written.revision_written == 2
    assert written.watch.revision == 2
    assert written.watch.next_due_at == T0 + timedelta(days=8)
    assert written.watch.ceilings == (WatchCeiling(line=BudgetLine.SEARCH, units=42),)
    assert written.watch.notify_on == (NotifyOn.CHANGED,)
    assert written.watch.check_every_days == 7
    assert written.watch.enabled is True


def test_recording_a_check_on_a_stale_revision_is_refused_and_writes_nothing(
    watches, scene
) -> None:
    """一次「今天的检查看完了」撞上一次「有人刚改了这条规则」⇒ **不覆盖**。

    覆盖会把那次人写的改动静默吃掉（刚被关掉的它又亮起来、刚改的 ``check_every_days``
    回到旧值）。判据是两半：一句点名两个版本的拒绝，以及**盘上那一格没有动**。
    """
    open_watch(watches, scene, "watch-live", view_id="view-a", check_every_days=7)
    first = record_watch_check(
        store=watches, watch_id="watch-live", expected_revision=1, at=T0 + timedelta(days=1)
    )
    # 人改了一次规则（第 3 版）。
    put_watch(
        store=watches,
        scene_store=scene,
        scene_id=SCENE,
        watch_id="watch-live",
        workspace=WORKSPACE,
        owner=OWNER,
        name="改过的监控",
        ceilings=(WatchCeiling(line=BudgetLine.SEARCH, units=10),),
        notify_on=(NotifyOn.CHANGED,),
        view_id="view-a",
        check_every_days=1,
        expected_revision=2,
        enabled=False,
        at=T0 + timedelta(days=2),
    )

    with pytest.raises(StoreError) as caught:
        record_watch_check(
            store=watches,
            watch_id="watch-live",
            expected_revision=2,
            at=T0 + timedelta(days=3),
        )
    message = str(caught.value)
    assert "2" in message and "3" in message

    row = watches.get_watch("watch-live")
    assert (row.revision, row.enabled, row.check_every_days) == (3, False, 1)
    assert row.next_due_at == first.watch.next_due_at, "被拒时一个字节都不该写"

    with pytest.raises(ConfigError) as missing:
        record_watch_check(
            store=watches, watch_id="watch-nope", expected_revision=1, at=T0
        )
    assert "watch-nope" in str(missing.value)


# ------------------------------------------------------------------ 跟进池
def test_the_scope_ref_is_computed_here_not_passed_in(items) -> None:
    """``open_action_item`` **不收** ``scope_ref``，它只有一个编码入口。

    一个手拼的实现会在 ``#`` 的哪一边放什么都可能 —— 于是「同一主体上的第二条」在库上
    成了另一个主体，那条唯一索引一次都不响（``open_action_item`` 的 docstring 逐字）。
    """
    assert "scope_ref" not in inspect.signature(open_action_item).parameters
    assert "scope_ref_value" not in inspect.signature(open_action_item).parameters

    written = open_action_item(
        store=items,
        action_id="act-route",
        workspace=WORKSPACE,
        scope_kind=ScopeKind.ROUTE,
        scope_id="route-7",
        action_type=ActionType.REVIEW_CONFLICT,
        reason="这条路线与另一条撞了。",
        owner=OWNER,
        at=T0,
    )

    assert written.item.scope_ref == scope_ref(ScopeKind.ROUTE, "route-7")
    assert split_scope_ref(written.item.scope_ref) == (ScopeKind.ROUTE, "route-7")


def test_a_second_open_item_on_the_same_subject_is_an_insert_failure(items) -> None:
    """重复创建**不是在这一层判的**：拒绝来自 ``store.create`` 的一次插入失败。

    判据落在那个 partial unique index 上，所以这里的两条都要判：一句点名盘上那一条
    ``action_id`` 的拒绝，以及盘上仍然只有**一件**（一个「先查一下」的实现也过得了
    前半条，而它挡不住另一条路径、另一个进程、一次手写的 SQL）。
    """
    open_action_item(
        store=items,
        action_id="act-first",
        workspace=WORKSPACE,
        scope_kind=ScopeKind.ACCOUNT,
        scope_id="acct-1",
        action_type=ActionType.VERIFY_REFERRAL_CONTACT,
        reason="这个账号的推荐联系人还没核实过。",
        owner=OWNER,
        at=T0,
    )

    with pytest.raises(OpenActionItemExistsError) as caught:
        open_action_item(
            store=items,
            action_id="act-second",
            workspace=WORKSPACE,
            scope_kind=ScopeKind.ACCOUNT,
            scope_id="acct-1",
            action_type=ActionType.VERIFY_REFERRAL_CONTACT,
            reason="同一条工作的第二次尝试。",
            owner=OWNER,
            at=T0,
        )
    message = str(caught.value)
    assert "act-first" in message, "要点名**盘上那一条**的 id，不是这一次的那个"
    assert scope_ref(ScopeKind.ACCOUNT, "acct-1") in message
    assert {item.action_id for item in list_action_items(store=items, workspace=WORKSPACE)} == {
        "act-first"
    }


def test_a_different_subject_or_type_is_a_different_slot(items) -> None:
    """那条槽是 ``(workspace, action_type, scope_ref)`` —— 三个里换一个就是另一件工作，
    而**做完的**那一件把槽空出来（历史可以有任意多条）。"""
    def open_one(action_id: str, *, scope_id: str = "acct-1", action_type=ActionType.VERIFY_REFERRAL_CONTACT):
        return open_action_item(
            store=items,
            action_id=action_id,
            workspace=WORKSPACE,
            scope_kind=ScopeKind.ACCOUNT,
            scope_id=scope_id,
            action_type=action_type,
            reason="一件工作。",
            owner=OWNER,
            at=T0,
        )

    open_one("act-1")
    open_one("act-other-subject", scope_id="acct-2")
    open_one("act-other-type", action_type=ActionType.FILL_RESPONSIBILITY)

    resolve_action_item(
        store=items,
        action_id="act-1",
        expected_version=1,
        state=ActionItemState.DONE,
        by=OWNER,
        resolution="办完了。",
        at=T0,
    )
    open_one("act-1-again")  # 槽空了，同一主体上的下一件建得出来

    assert {item.action_id for item in list_action_items(store=items, workspace=WORKSPACE)} == {
        "act-1",
        "act-other-subject",
        "act-other-type",
        "act-1-again",
    }


def test_resolving_with_a_stale_version_is_refused_and_writes_nothing(items) -> None:
    """手上的版本过期 ⇒ 拒绝，消息里**两个版本都在**（「自己太慢」与「对面在打架」
    的处置不同），而盘上那一件不动。"""
    open_action_item(
        store=items,
        action_id="act-1",
        workspace=WORKSPACE,
        scope_kind=ScopeKind.ACCOUNT,
        scope_id="acct-1",
        action_type=ActionType.VERIFY_REFERRAL_CONTACT,
        reason="一件工作。",
        owner=OWNER,
        at=T0,
    )
    resolve_action_item(
        store=items,
        action_id="act-1",
        expected_version=1,
        state=ActionItemState.CLAIMED,
        by=OWNER,
        at=T0,
    )

    with pytest.raises(ActionItemConflictError) as caught:
        resolve_action_item(
            store=items,
            action_id="act-1",
            expected_version=1,
            state=ActionItemState.WAITING,
            by=OTHER_OWNER,
            blocked_by="等对方的回信。",
            at=T0,
        )
    message = str(caught.value)
    assert "1" in message and "2" in message

    row = items.get("act-1")
    assert (row.revision, row.state, row.owner) == (2, ActionItemState.CLAIMED, OWNER)


def test_resending_the_same_disposition_never_writes_a_third_version(items) -> None:
    """重发同一次处置：**要么是空写、要么是一句拒绝，绝不是第三版**。

    这一条刻意不钉死是两种里的哪一种，理由要写下来：``ports/views.py::ActionItemStore.resolve``
    逐字承诺了「重发同一次处置是幂等的」，而那一格今天**到不了**这里 ——
    ``resolve_action_item`` 先把**当前那一版**读出来交给 ``ActionItem.resolve``，而它
    要求 ``expected_version`` 正好等于当前版，所以第二次调用在域那一层就被拦下了。

    钉死「今天会拒绝」会把一个缺口冻成判据；钉死「今天幂等」会写一条不成立的绿。
    所以这里判的是那件两造都必须成立的事：**盘上不会多出第三版，那一行也不会变**。
    缺口的处置记在 ``REPORT.md`` 里（要么这一层学会把重发交给存储判，要么把端口那一句
    改成它真实的样子）。
    """
    open_action_item(
        store=items,
        action_id="act-1",
        workspace=WORKSPACE,
        scope_kind=ScopeKind.ACCOUNT,
        scope_id="acct-1",
        action_type=ActionType.VERIFY_REFERRAL_CONTACT,
        reason="一件工作。",
        owner=OWNER,
        at=T0,
    )
    first = resolve_action_item(
        store=items,
        action_id="act-1",
        expected_version=1,
        state=ActionItemState.CLAIMED,
        by=OWNER,
        at=T0,
    )
    assert first.revision_written == 2

    try:
        again = resolve_action_item(
            store=items,
            action_id="act-1",
            expected_version=1,
            state=ActionItemState.CLAIMED,
            by=OWNER,
            at=T0 + timedelta(minutes=1),
        )
    except ActionItemConflictError as refusal:
        assert "1" in str(refusal) and "2" in str(refusal), (
            "拒绝里要同时有手上的版本与盘上的版本，否则它读起来像一次「状态不相容」"
        )
    else:
        assert again.revision_written is None, "幂等的那一半必须是**空写**，不是第三版"

    row = items.get("act-1")
    assert (row.revision, row.state) == (2, ActionItemState.CLAIMED)
    assert len(list_action_items(store=items, workspace=WORKSPACE)) == 1


def test_an_unknown_action_id_is_a_different_refusal_from_a_stale_one(items) -> None:
    """抄错 id 与「你手上的版本过期了」是**两件事**（一个是换 id，一个是重读）。"""
    with pytest.raises(ConfigError) as caught:
        resolve_action_item(
            store=items,
            action_id="act-typo",
            expected_version=1,
            state=ActionItemState.DONE,
            by=OWNER,
            at=T0,
        )
    assert "act-typo" in str(caught.value)


def test_the_item_list_shows_every_state_by_default(items) -> None:
    """``open_only`` 的缺省是 ``False``：一个「默认只回未结的」的实现会让「他有一条
    做完的」与「他从来没做过」在报告上同形。"""
    for action_id, scope_id in (("act-open", "acct-1"), ("act-done", "acct-2")):
        open_action_item(
            store=items,
            action_id=action_id,
            workspace=WORKSPACE,
            scope_kind=ScopeKind.ACCOUNT,
            scope_id=scope_id,
            action_type=ActionType.VERIFY_REFERRAL_CONTACT,
            reason="一件工作。",
            owner=OWNER,
            at=T0,
        )
    resolve_action_item(
        store=items,
        action_id="act-done",
        expected_version=1,
        state=ActionItemState.DONE,
        by=OWNER,
        resolution="办完了。",
        at=T0,
    )

    assert {item.action_id for item in list_action_items(store=items, workspace=WORKSPACE)} == {
        "act-open",
        "act-done",
    }
    assert {item.action_id for item in list_action_items(
        store=items, workspace=WORKSPACE, open_only=True
    )} == {"act-open"}
    assert {item.action_id for item in list_action_items(
        store=items, workspace=WORKSPACE, state=ActionItemState.DONE
    )} == {"act-done"}
    # ``owner`` 那一格**真的进过滤**（一个收了参数不筛的实现在报告上与筛过的一模一样）。
    assert list_action_items(store=items, workspace=WORKSPACE, owner="nobody") == ()

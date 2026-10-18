"""``ports/views.py`` 那三个端口的契约：**同一组判据，对着两个实现各跑一遍**。

与 ``tests/test_review_store_contract.py`` 逐字同一条理由：端口本身不构成契约 ——
构成契约的是同一组判据同时看着两个实现。

* ``adapters/sqlite/views.py`` —— 主存（三张表，见 ``db.py`` 的 DDL）。
* ``adapters/memory.py`` —— 测试替身。

## 这一族里两个实现**判据相同而执行者不同**的地方，正是这份要钉的东西

1. **名字判据**：SQLite 那一份是 ``_require_name_free`` 里一条带
   ``max(revision)`` 子查询的 ``SELECT``，内存那一份是三个 ``if`` 与一次遍历。
   两边的语义必须逐字相同，而**这两处都不是 DDL 上的唯一索引** ——
   一条普通的唯一索引会把历史版本也挡掉，于是「改名」在库上做不成。
   ``test_renaming_a_view_does_not_collide_with_its_own_history`` 与
   ``test_a_second_current_version_under_the_same_name_is_refused`` 各钉一半。
2. **同一主体上的第二件未结待办**：SQLite 那一份靠 ``action_item_open_by_subject``
   这条 partial unique index，内存那一份靠一次显式查找（它自己的类文档写了这件事，
   以及那条替换的代价）。契约测试只能证明**答案**相同；「判据住在索引上」那一条
   在 ``tests/test_sqlite_views.py`` 里对着库本身验。

## 一份判据**刻意不在这里**

「写完重开一个 store 还能读到」只对持久化实现成立，那一条在
``tests/test_sqlite_views.py`` 里。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import (
    InMemoryActionItemStore,
    InMemorySavedViewStore,
    InMemoryWatchStore,
)
from marketops.adapters.sqlite.views import (
    SqliteActionItemStore,
    SqliteSavedViewStore,
    SqliteWatchStore,
)
from marketops.domain.action_item import (
    ActionItem,
    ActionItemState,
    ActionType,
    ScopeKind,
    scope_ref,
)
from marketops.domain.errors import (
    ActionItemConflictError,
    OpenActionItemExistsError,
    SavedViewConflictError,
    StoreError,
)
from marketops.domain.saved_view import SavedView, ViewVisibility
from marketops.domain.watch import NotifyOn, Watch, WatchCeiling
from marketops.domain.enums import BudgetLine
from marketops.ports.views import ActionItemStore, SavedViewStore, WatchStore

UTC = timezone.utc
T0 = datetime(2026, 9, 27, 1, 0, tzinfo=UTC)
WORKSPACE = "uk-acme-natural-grass"
OTHER_WORKSPACE = "uk-something-else"
OWNER = "member-b"
OTHER_OWNER = "member-d"

#: 一份**形状正确**的查询文档。它不需要真的跑得出结果 —— 这一份测的是**存储**，
#: 不是编译器；内容只用来让 ``content_digest`` 在两次写之间真的不同。
QUERY = {
    "subject_kind": "account",
    "filter": [{"field": "status", "op": "is", "value": "known_success"}],
}


# ---------------------------------------------------------------------- 夹具
@pytest.fixture(params=["sqlite", "memory"])
def wired(request, tmp_path) -> tuple[SavedViewStore, WatchStore, ActionItemStore]:
    """三个存储，两个实现各一套。参数名进测试 id —— 红了时一眼看得见是哪一个。

    三个 store 必须**共用一块盘**（同一个 ``db_path`` / 三个内存对象之间没有共享，
    但它们的 ``workspace`` 绑定各自成立）—— 一份「写视图、开 Watch、建待办」的
    用例要能在一套夹具里跑完，而不是每个端口各写一遍前置。
    """
    if request.param == "sqlite":
        db_path = tmp_path / "state" / "marketops.db"
        return (
            SqliteSavedViewStore(db_path),
            SqliteWatchStore(db_path),
            SqliteActionItemStore(db_path),
        )
    return (
        InMemorySavedViewStore(),
        InMemoryWatchStore(),
        InMemoryActionItemStore(),
    )


@pytest.fixture
def views(wired: tuple[SavedViewStore, WatchStore, ActionItemStore]) -> SavedViewStore:
    return wired[0]


@pytest.fixture
def watches(wired: tuple[SavedViewStore, WatchStore, ActionItemStore]) -> WatchStore:
    return wired[1]


@pytest.fixture
def items(wired: tuple[SavedViewStore, WatchStore, ActionItemStore]) -> ActionItemStore:
    return wired[2]


# ---------------------------------------------------------------------- 构造
def make_view(
    *,
    view_id: str = "view-accounts",
    revision: int = 1,
    owner: str = OWNER,
    name: str = "全部已知账号",
    workspace: str = WORKSPACE,
    query: dict | None = None,
    visibility: ViewVisibility = ViewVisibility.SHARED,
    at: datetime = T0,
    **overrides,
) -> SavedView:
    fields = {
        "view_id": view_id,
        "revision": revision,
        "workspace": workspace,
        "owner": owner,
        "name": name,
        "visibility": visibility,
        "query": dict(QUERY if query is None else query),
        "created_at": at,
        "updated_at": at,
        "note": "",
    }
    fields.update(overrides)
    return SavedView(**fields)


def make_watch(
    *,
    watch_id: str = "watch-weekly",
    revision: int = 1,
    owner: str = OWNER,
    name: str = "每周看一眼",
    workspace: str = WORKSPACE,
    view_id: str | None = "view-accounts",
    subjects: tuple[tuple[str, str], ...] = (),
    aspects: tuple = (),
    check_every_days: int = 7,
    ceilings: tuple[WatchCeiling, ...] = (
        WatchCeiling(line=BudgetLine.SEARCH, units=10),
    ),
    notify_on: tuple[NotifyOn, ...] = (NotifyOn.CHANGED,),
    enabled: bool = True,
    next_due_at: datetime | None = None,
    at: datetime = T0,
    **overrides,
) -> Watch:
    fields = {
        "watch_id": watch_id,
        "revision": revision,
        "workspace": workspace,
        "owner": owner,
        "name": name,
        "view_id": view_id,
        "subjects": subjects,
        "aspects": aspects,
        "check_every_days": check_every_days,
        "ceilings": ceilings,
        "notify_on": notify_on,
        "enabled": enabled,
        "next_due_at": next_due_at,
        "created_at": at,
        "updated_at": at,
        "note": "",
    }
    fields.update(overrides)
    return Watch(**fields)


def make_item(
    *,
    action_id: str = "act-1",
    workspace: str = WORKSPACE,
    scope_kind: ScopeKind = ScopeKind.ACCOUNT,
    scope_id: str = "test-account-001",
    action_type: ActionType = ActionType.VERIFY_REFERRAL_CONTACT,
    owner: str | None = OWNER,
    reason: str = "这个账号的推荐联系人还没核实过。",
    due_at: datetime | None = None,
    at: datetime = T0,
    **overrides,
) -> ActionItem:
    fields = {
        "action_id": action_id,
        "workspace": workspace,
        "scope_ref_value": scope_ref(scope_kind, scope_id),
        "action_type": action_type,
        "reason": reason,
        "owner": owner,
        "due_at": due_at,
        "at": at,
    }
    fields.update(overrides)
    return ActionItem.opened(**fields)


# ---------------------------------------------------------------------- 视图
def test_a_new_view_lands_as_revision_one(views: SavedViewStore) -> None:
    write = views.put_view(make_view(), expected_revision=None)
    assert write.revision_written == 1
    assert views.current("view-accounts") == write.view


def test_resending_the_same_content_writes_no_new_version(
    views: SavedViewStore,
) -> None:
    """内容一字不差时**不产生新版本**，而回执说得出这一点。

    「你刚才那次成功了」与「你第二次点的时候什么都没发生」是两句不同的话 ——
    一个每次都产生新版本的实现会让这两句在报告上同形。
    """
    first = views.put_view(make_view(), expected_revision=None)
    again = views.put_view(
        make_view(revision=2, at=T0 + timedelta(minutes=1)), expected_revision=1
    )
    assert again.revision_written is None
    assert again.view.revision == 1 == first.view.revision
    assert views.current("view-accounts") is not None
    assert views.current("view-accounts").revision == 1  # type: ignore[union-attr]


def test_expected_revision_none_on_an_existing_id_is_refused(
    views: SavedViewStore,
) -> None:
    """``expected_revision=None`` 是**必须不存在**，不是「随便写」。"""
    views.put_view(make_view(), expected_revision=None)
    with pytest.raises(SavedViewConflictError):
        views.put_view(make_view(), expected_revision=None)


def test_a_stale_base_is_refused_and_nothing_is_written(
    views: SavedViewStore,
) -> None:
    """拿着一版过期的基础写回必须被拒 —— 而且**盘上不动**。"""
    views.put_view(make_view(), expected_revision=None)
    views.put_view(
        make_view(name="改过名字的", revision=2, at=T0 + timedelta(minutes=1)),
        expected_revision=1,
    )
    with pytest.raises(SavedViewConflictError) as exc:
        views.put_view(
            make_view(name="拿旧版本写回的", revision=2, at=T0 + timedelta(minutes=2)),
            expected_revision=1,
        )
    assert "第 1 版" in str(exc.value) and "第 2 版" in str(exc.value)
    current = views.current("view-accounts")
    assert current is not None and current.revision == 2
    assert current.name == "改过名字的"  # type: ignore[union-attr]


def test_renaming_a_view_does_not_collide_with_its_own_history(
    views: SavedViewStore,
) -> None:
    """**改名不撞车** —— 历史版本还挂在老名字底下，而它不是「另一条」。

    这条判据最容易实现成 DDL 上的一条普通唯一索引，而那会让改名在库上做不成
    （``ports/views.py::SavedViewStore.put_view`` 逐字）。红了要去看的是
    ``_require_name_free`` 的 ``view_id <> ?``，不是去加索引。
    """
    views.put_view(make_view(name="老名字"), expected_revision=None)
    write = views.put_view(
        make_view(name="新名字", revision=2, at=T0 + timedelta(minutes=1)),
        expected_revision=1,
    )
    assert write.revision_written == 2
    assert write.view.name == "新名字"


def test_a_second_current_version_under_the_same_name_is_refused(
    views: SavedViewStore,
) -> None:
    """**另一条**视图不许叫同一个名字 —— 而拒绝要点名占着它的那一条。"""
    views.put_view(make_view(name="占着的"), expected_revision=None)
    with pytest.raises(SavedViewConflictError) as exc:
        views.put_view(
            make_view(view_id="view-other", name="占着的"), expected_revision=None
        )
    message = str(exc.value)
    assert "view-accounts" in message
    assert "第 1 版" in message
    # **盘上不能多出那一条**：一次被拒的写只在回执里出现。
    assert views.current("view-other") is None


def test_a_name_freed_by_renaming_becomes_available_again(
    views: SavedViewStore,
) -> None:
    """把当前版改到别的名字之后，老名字**空了出来** —— 这正是「只判当前版」。"""
    views.put_view(make_view(name="老名字"), expected_revision=None)
    views.put_view(
        make_view(name="新名字", revision=2, at=T0 + timedelta(minutes=1)),
        expected_revision=1,
    )
    write = views.put_view(
        make_view(view_id="view-other", name="老名字"), expected_revision=None
    )
    assert write.revision_written == 1


def test_views_are_current_only_sorted_and_owner_filtered(
    views: SavedViewStore,
) -> None:
    views.put_view(make_view(name="b", view_id="v-b", owner=OWNER), expected_revision=None)
    views.put_view(
        make_view(name="a", view_id="v-a", owner=OTHER_OWNER), expected_revision=None
    )
    # 给 v-b 写第二版：清单里只许出现一次，而且是第 2 版。
    views.put_view(
        make_view(name="b2", view_id="v-b", owner=OWNER, revision=2, at=T0 + timedelta(1)),
        expected_revision=1,
    )
    everything = views.views(workspace=WORKSPACE)
    assert [(v.owner, v.name, v.revision) for v in everything] == [
        (OWNER, "b2", 2),
        (OTHER_OWNER, "a", 1),
    ]
    mine = views.views(workspace=WORKSPACE, owner=OWNER)
    assert [v.view_id for v in mine] == ["v-b"]
    assert views.views(workspace=OTHER_WORKSPACE) == ()


def test_a_version_jump_is_refused_before_the_store_is_touched(
    views: SavedViewStore,
) -> None:
    """写下去的那一版必须是**基准加一**。

    这一条挡的是「读完之后跳了一版直接写回」：它会留下一个**空洞**，而在那个空洞里，
    一个拿着旧版本的写者会成功（``ports/views.py::SavedViewStore.put_view`` 逐字）。
    主键判据拦不住它 —— 那一版号没有被任何人用过，``INSERT`` 落得进去。
    """
    views.put_view(make_view(name="v1"), expected_revision=None)
    with pytest.raises(StoreError) as exc:
        views.put_view(make_view(name="v3", revision=3), expected_revision=1)
    assert "必须是 2" in str(exc.value)
    assert views.current("view-accounts").revision == 1  # type: ignore[union-attr]


# ---------------------------------------------------------------------- 监控
def test_watch_versions_accumulate_and_current_is_the_max(
    watches: WatchStore,
) -> None:
    watches.put_watch(make_watch(), expected_revision=None)
    second = watches.put_watch(
        make_watch(check_every_days=14, revision=2, at=T0 + timedelta(1)),
        expected_revision=1,
    )
    assert second.revision_written == 2
    current = watches.get_watch("watch-weekly")
    assert current is not None and current.check_every_days == 14


def test_two_watches_may_share_a_name(watches: WatchStore) -> None:
    """Watch 的重名是**允许**的 —— 它的身份是 ``watch_id``。

    与视图相反，这个差别不是为了好写：视图的名字是人用来认一份**查询**的，
    而 Watch 的名字是一句给人读的说明（``ports/views.py::WatchStore.put_watch`` 逐字）。
    """
    watches.put_watch(make_watch(watch_id="w-1", name="同一个名字"), expected_revision=None)
    write = watches.put_watch(
        make_watch(watch_id="w-2", name="同一个名字"), expected_revision=None
    )
    assert write.revision_written == 1


def test_watches_filters_by_workspace_and_enabled(watches: WatchStore) -> None:
    watches.put_watch(make_watch(watch_id="w-on"), expected_revision=None)
    watches.put_watch(
        make_watch(watch_id="w-off", enabled=False), expected_revision=None
    )
    assert [w.watch_id for w in watches.watches(workspace=WORKSPACE)] == [
        "w-off",
        "w-on",
    ]
    assert [w.watch_id for w in watches.watches(workspace=WORKSPACE, enabled=False)] == [
        "w-off"
    ]
    assert watches.watches(workspace=OTHER_WORKSPACE) == ()


def test_due_watches_only_returns_enabled_and_due(watches: WatchStore) -> None:
    watches.put_watch(
        make_watch(watch_id="w-due", next_due_at=T0), expected_revision=None
    )
    watches.put_watch(
        make_watch(watch_id="w-later", next_due_at=T0 + timedelta(days=30)),
        expected_revision=None,
    )
    watches.put_watch(
        make_watch(watch_id="w-off-due", next_due_at=T0, enabled=False),
        expected_revision=None,
    )
    assert [w.watch_id for w in watches.due_watches(workspace=WORKSPACE, at=T0)] == [
        "w-due"
    ]


def test_due_watches_never_returns_a_historical_version(watches: WatchStore) -> None:
    """**只回当前版**：一条被推到将来的 Watch，它的旧行还躺在 ``watch_by_due`` 索引里。

    这条判据在 SQLite 那一份里靠 ``revision = (SELECT MAX(revision) ...)`` 那句子查询，
    在内存那一份里靠 ``_current``。少写那句子查询的实现会让到期的旧版**混在结果里**，
    而它与当前版长得一模一样（同样的 ``watch_id``）—— 于是调度会按一条已经改过的
    节奏去跑。
    """
    watches.put_watch(
        make_watch(next_due_at=T0), expected_revision=None
    )
    current = watches.get_watch("watch-weekly")
    assert current is not None
    moved = current.after_check(at=T0).model_copy(update={"revision": 2})
    watches.put_watch(moved, expected_revision=1)
    assert watches.due_watches(workspace=WORKSPACE, at=T0) == ()
    assert [w.watch_id for w in watches.due_watches(workspace=WORKSPACE, at=T0 + timedelta(days=7))] == [
        "watch-weekly"
    ]


def test_due_watches_orders_by_due_then_id(watches: WatchStore) -> None:
    watches.put_watch(
        make_watch(watch_id="w-b", next_due_at=T0 + timedelta(hours=2)),
        expected_revision=None,
    )
    watches.put_watch(
        make_watch(watch_id="w-a", next_due_at=T0 + timedelta(hours=1)),
        expected_revision=None,
    )
    assert [w.watch_id for w in watches.due_watches(workspace=WORKSPACE, at=T0 + timedelta(days=1))] == [
        "w-a",
        "w-b",
    ]


# -------------------------------------------------------------------- 跟进池
def test_a_new_item_lands_open_at_version_one(items: ActionItemStore) -> None:
    write = items.create(make_item())
    assert write.revision_written == 1
    assert write.item.state is ActionItemState.OPEN
    assert write.item.expected_version == 0
    assert write.item.is_open


def test_a_duplicate_open_item_is_an_insert_failure(items: ActionItemStore) -> None:
    """**重复创建必须是插入失败，不是一次查询的结论。**

    回执里要点名**盘上那一条的 ``action_id``**：一句「已经有一条了」而不说是哪一条，
    让调用方只能把它当成一次故障。红了要去看的是存储那一侧的插入路径
    （SQLite 那一份靠 ``action_item_open_by_subject`` 这条索引），**不是**在这一层
    加一次「先查一下」—— 先查后插在两个写者之间有一个窗口。
    """
    items.create(make_item(action_id="act-first"))
    with pytest.raises(OpenActionItemExistsError) as exc:
        items.create(make_item(action_id="act-second"))
    assert "act-first" in str(exc.value)
    assert items.get("act-second") is None


def test_the_open_constraint_covers_all_three_open_states(
    items: ActionItemStore,
) -> None:
    """``open`` / ``claimed`` / ``waiting`` 三档**都算未结** —— 索引的 ``WHERE`` 与
    ``OPEN_STATES`` 是同一句话的两个写法（文本对账在
    ``tests/test_action_item_open_states.py`` 里）。
    """
    items.create(make_item(action_id="act-open"))
    claimed = items.resolve(
        items.get("act-open").resolve(  # type: ignore[union-attr]
            at=T0 + timedelta(minutes=1),
            state=ActionItemState.CLAIMED,
            by=OWNER,
            expected_version=1,
        ),
        expected_version=1,
    )
    # ``revision_written`` 是**写下去的那一版**（2），不是「写了几行」（1）——
    # 就地更新那一行，而版本号加一。
    assert claimed.revision_written == 2
    assert claimed.item.state is ActionItemState.CLAIMED
    with pytest.raises(OpenActionItemExistsError):
        items.create(make_item(action_id="act-again"))


def test_a_different_subject_or_type_is_a_different_slot(
    items: ActionItemStore,
) -> None:
    """索引盖的是 ``(workspace, action_type, scope_ref)`` 三元组 —— 换任何一格都是
    另一个格子。"""
    items.create(make_item(action_id="act-1"))
    items.create(make_item(action_id="act-2", scope_id="test-account-002"))
    items.create(
        make_item(action_id="act-3", action_type=ActionType.FILL_RESPONSIBILITY)
    )
    assert len(items.items(workspace=WORKSPACE)) == 3


def test_a_finished_item_frees_the_slot_while_the_row_stays(
    items: ActionItemStore,
) -> None:
    """**已结的可以有任意多条，而那条约束只管未结的。**

    这条判据就是那条 partial unique index 里 ``WHERE state IN (...)`` 那半句。
    去掉 ``WHERE`` 的变异（``tests/test_sqlite_views.py`` 里那条 raw SQL 探针）
    会让这里的第一条 ``pytest.raises`` 变成一次误报的成功：结掉的那一行还占着
    ``(workspace, action_type, scope_ref)``，于是同一个主体上的下一件**再也建不出来** ——
    而报告上只显示「这个账号的待办一直是那一条」。
    """
    items.create(make_item(action_id="act-first"))
    current = items.get("act-first")
    assert current is not None
    items.resolve(
        current.resolve(
            at=T0 + timedelta(minutes=1),
            state=ActionItemState.DONE,
            by=OWNER,
            expected_version=1,
            resolution="打过电话了，联系人还在。",
        ),
        expected_version=1,
    )
    second = items.create(make_item(action_id="act-second"))
    assert second.revision_written == 1

    # 再结一条，然后第三件仍然建得出来 —— 「任意多条」不是「一条」。
    current = items.get("act-second")
    assert current is not None
    items.resolve(
        current.resolve(
            at=T0 + timedelta(minutes=2),
            state=ActionItemState.DISMISSED,
            by=OWNER,
            expected_version=1,
            resolution="这条不需要跟进。",
        ),
        expected_version=1,
    )
    assert items.create(make_item(action_id="act-third")).revision_written == 1


def test_resolving_updates_in_place_not_as_a_new_version(
    items: ActionItemStore,
) -> None:
    """处置**就地更新那一行**，不是追加一版 —— 追加的实现会让每一件被处置过的待办
    永远是未结的（旧的 ``open`` 行还躺在索引里），而报告上只显示「只有一条」。"""
    items.create(make_item())
    current = items.get("act-1")
    assert current is not None
    write = items.resolve(
        current.resolve(
            at=T0 + timedelta(minutes=1),
            state=ActionItemState.DONE,
            by=OWNER,
            expected_version=1,
            resolution="办理完毕。",
        ),
        expected_version=1,
    )
    assert write.revision_written == 2
    assert write.item.revision == 2
    # ``expected_version`` 记的是**这一次写基于哪一版**（1），不是它自己那一版。
    assert write.item.expected_version == 1
    assert write.item.state is ActionItemState.DONE
    assert len(items.items(workspace=WORKSPACE)) == 1
    assert len(items.items(workspace=WORKSPACE, open_only=True)) == 0


def test_resolving_with_a_stale_version_is_refused_and_writes_nothing(
    items: ActionItemStore,
) -> None:
    items.create(make_item())
    current = items.get("act-1")
    assert current is not None
    first = current.resolve(
        at=T0 + timedelta(minutes=1),
        state=ActionItemState.CLAIMED,
        by=OWNER,
        expected_version=1,
    )
    items.resolve(first, expected_version=1)

    # 另一个拿着 ``expected_version=1`` 的人（他读到的是更早的那一版，而盘上已经是
    # 第 2 版了）——
    # 而他要写的是**另一件处置**，所以它不是重发。
    stale = current.resolve(
        at=T0 + timedelta(minutes=2),
        state=ActionItemState.WAITING,
        by=OTHER_OWNER,
        expected_version=1,
        blocked_by="等对方回信。",
    )
    with pytest.raises(ActionItemConflictError) as exc:
        items.resolve(stale, expected_version=1)
    # 报文里**两个数字都在**：期望版本与盘上那一版。少了任何一个，
    # 「自己太慢」与「对面在打架」就分不开，而它们的处置不同。
    message = str(exc.value)
    assert "手上的版本是 1" in message
    assert "盘上当前是第 2 版" in message
    # 那一次陈旧写的**什么都没落盘**：盘上仍然是第一次处置写下的第 2 版。
    settled = items.get("act-1")
    assert settled is not None
    assert (settled.revision, settled.state) == (2, ActionItemState.CLAIMED)


def test_resending_the_same_disposition_is_idempotent(
    items: ActionItemStore,
) -> None:
    """**同一次处置重发**：不写，且回盘上那一行。

    两个半边缺一不可：只看内容摘要会放行「基于另一版的同一次处置」，
    只看版本会让「同一版上两次不同的处置」被当成重发（第二个人写的字就此消失）。
    """
    items.create(make_item())
    current = items.get("act-1")
    assert current is not None
    first = current.resolve(
        at=T0 + timedelta(minutes=1),
        state=ActionItemState.DONE,
        by=OWNER,
        expected_version=1,
        resolution="办理完毕。",
    )
    items.resolve(first, expected_version=1)

    again = items.resolve(first, expected_version=1)
    assert again.revision_written is None
    assert again.item == items.get("act-1")


def test_a_different_disposition_on_a_stale_base_is_not_a_resend(
    items: ActionItemStore,
) -> None:
    """**同一次处置**的两个半边之一换了，就不是重发 —— 它要被拒。"""
    items.create(make_item())
    current = items.get("act-1")
    assert current is not None
    first = current.resolve(
        at=T0 + timedelta(minutes=1),
        state=ActionItemState.DONE,
        by=OWNER,
        expected_version=1,
        resolution="办理完毕。",
    )
    items.resolve(first, expected_version=1)

    # 内容摘要不同（说的是另一句话）⇒ 不是重发。
    different = first.model_copy(
        update={"resolution": "另一个人写的另一句话。", "resolution_by": OTHER_OWNER}
    )
    with pytest.raises(ActionItemConflictError):
        items.resolve(different, expected_version=1)


def test_resolving_an_unknown_action_id_is_refused(items: ActionItemStore) -> None:
    with pytest.raises(ActionItemConflictError) as exc:
        items.resolve(make_item(action_id="act-never").resolve(
            at=T0,
            state=ActionItemState.DONE,
            by=OWNER,
            expected_version=1,
            resolution="没有这一件。",
        ), expected_version=1)
    assert "act-never" in str(exc.value)


def test_items_orders_open_first_then_due_then_id(items: ActionItemStore) -> None:
    """顺序是契约的一部分：报告的第一行必须是「现在最该看的那一件」。

    每一件挂**不同**的主体：那条 partial unique index 盖的是
    ``(workspace, action_type, scope_ref)``，同一主体上的第二件未结待办**建不出来**
    （那一条判据在 ``test_a_duplicate_open_item_is_an_insert_failure`` 里）。
    """
    items.create(
        make_item(
            action_id="act-later",
            scope_id="acct-later",
            due_at=T0 + timedelta(days=3),
        )
    )
    items.create(make_item(action_id="act-nodue", scope_id="acct-nodue"))
    items.create(
        make_item(
            action_id="act-sooner",
            scope_id="acct-sooner",
            due_at=T0 + timedelta(days=1),
        )
    )
    items.create(
        make_item(action_id="act-b", scope_id="acct-b", due_at=T0 + timedelta(days=1))
    )

    done = items.create(make_item(action_id="act-done", scope_id="acct-done"))
    closed = items.resolve(
        done.item.resolve(
            at=T0 + timedelta(minutes=1),
            state=ActionItemState.DONE,
            by=OWNER,
            expected_version=1,
            resolution="办完了。",
        ),
        expected_version=1,
    )
    assert closed.revision_written == 2

    assert [item.action_id for item in items.items(workspace=WORKSPACE)] == [
        "act-b",
        "act-sooner",
        "act-later",
        "act-nodue",
        "act-done",
    ]


def test_open_only_and_state_are_two_different_filters(
    items: ActionItemStore,
) -> None:
    items.create(make_item(action_id="act-open", scope_id="acct-open"))
    waiting = items.create(make_item(action_id="act-waiting", scope_id="acct-waiting"))
    items.resolve(
        waiting.item.resolve(
            at=T0 + timedelta(minutes=1),
            state=ActionItemState.WAITING,
            by=OWNER,
            expected_version=1,
            blocked_by="等对方回信。",
        ),
        expected_version=1,
    )
    assert [i.action_id for i in items.items(workspace=WORKSPACE, open_only=True)] == [
        "act-open",
        "act-waiting",
    ]
    assert [
        i.action_id for i in items.items(workspace=WORKSPACE, state=ActionItemState.WAITING)
    ] == ["act-waiting"]
    # 两个都给时按 ``state`` —— 一个点名了具体状态的人不该拿到另外两个。
    assert [
        i.action_id
        for i in items.items(
            workspace=WORKSPACE, state=ActionItemState.OPEN, open_only=True
        )
    ] == ["act-open"]


def test_items_filters_by_owner_and_workspace(items: ActionItemStore) -> None:
    items.create(make_item(action_id="act-mine", scope_id="acct-mine", owner=OWNER))
    items.create(
        make_item(action_id="act-theirs", scope_id="acct-theirs", owner=OTHER_OWNER)
    )
    items.create(make_item(action_id="act-nobody", scope_id="acct-nobody", owner=None))
    assert [i.action_id for i in items.items(workspace=WORKSPACE, owner=OWNER)] == [
        "act-mine"
    ]
    assert items.items(workspace=OTHER_WORKSPACE) == ()

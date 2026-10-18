"""``SessionBindingStore`` 的契约判据 —— 身份是**会话**，不是调用方给的那个 id。

这一层只有三个方法，要守的失败也只有一个，但它有两种长相，而两种都不报错：

1. **按 id 找**：同一个会话带着一个新铸的 id 写第二次 —— 按 id 的实现会插第二行，
   而那条插入撞在 ``UNIQUE (workspace, principal, client_kind, client_session_ref)``
   上，报出来的是一句「有人先写了」（要重读），实际发生的事是「同一个会话又动了一下
   焦点」（什么都不用做）。处置相反，而报错看起来一样。
2. **按 id 覆盖**：同一个 ``binding_id`` 配了另一个会话 —— 静默覆盖会让**前一个会话**
   的焦点凭空变成另一个人的。

（三条**否定**——不授权、不是外键、不进业务幂等——住在
``tests/test_session_binding_negatives.py``：它们不是这一层的判据，而是这一层
「没有那一格」这件事在调用点上的形态。）

## 两个实现跑同一份

``SqliteSessionBindingStore``（主存）与 ``InMemorySessionBindingStore``（测试替身）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemorySessionBindingStore
from marketops.adapters.sqlite.session import SqliteSessionBindingStore
from marketops.domain.errors import StoreError
from marketops.domain.session import SessionBinding, session_binding_id
from marketops.ports.session import SessionBindingStore

WORKSPACE = "ws-contract"

T0 = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)

IMPLEMENTATION_PARAMS = ("sqlite", "memory")


def test_the_contract_runs_against_every_implementation() -> None:
    """实现清单缩水时「两个实现都通过」会变成「一个实现通过」。"""
    assert set(IMPLEMENTATION_PARAMS) == {"sqlite", "memory"}


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> SessionBindingStore:
    if request.param == "sqlite":
        return SqliteSessionBindingStore(tmp_path / "marketops.db")
    return InMemorySessionBindingStore()


def make_binding(
    *,
    principal: str = "alice",
    client_kind: str = "claude-code",
    client_session_ref: str | None = "sess-0001",
    focus_refs: tuple[str, ...] = ("workstream:ws-1",),
    report_cursor: str | None = None,
    workspace: str = WORKSPACE,
    last_seen: datetime = T0,
    expires_at: datetime | None = None,
) -> SessionBinding:
    """一条绑定。``binding_id`` **按会话推出来**（``session_binding_id``）—— 唯一的合法写法。

    ``client_session_ref=None`` 时推不出来，那种情形调用方自己给 id：见
    ``make_anonymous_binding``。
    """
    assert client_session_ref is not None, "无会话引用时请用 make_anonymous_binding"
    return SessionBinding(
        binding_id=session_binding_id(workspace, principal, client_kind, client_session_ref),
        workspace=workspace,
        principal=principal,
        client_kind=client_kind,
        client_session_ref=client_session_ref,
        focus_refs=focus_refs,
        report_cursor=report_cursor,
        expires_at=expires_at,
        last_seen=last_seen,
    )


def make_anonymous_binding(
    *,
    binding_id: str = "sb-anonymous-0001",
    principal: str = "alice",
    client_kind: str = "claude-code",
    workspace: str = WORKSPACE,
    focus_refs: tuple[str, ...] = (),
    last_seen: datetime = T0,
) -> SessionBinding:
    """客户端没有稳定 session id 的那种（§7.1 ``:271`` 逐字）。

    ``client_session_ref=None`` 在主键的一半上是 NULL，而 SQLite 认为两个 NULL
    互不相等 —— 于是同一个人的两条无 id 绑定各自成立。
    """
    return SessionBinding(
        binding_id=binding_id,
        workspace=workspace,
        principal=principal,
        client_kind=client_kind,
        client_session_ref=None,
        focus_refs=focus_refs,
        report_cursor=None,
        expires_at=None,
        last_seen=last_seen,
    )


# ----------------------------------------------------------------------
# 第一步：插入
# ----------------------------------------------------------------------
def test_the_first_binding_is_created(store: SessionBindingStore) -> None:
    written = store.put_binding(make_binding())
    assert written.created is True
    assert written.binding.focus_refs == ("workstream:ws-1",)


def test_a_binding_is_readable_by_its_id(store: SessionBindingStore) -> None:
    binding = make_binding()
    store.put_binding(binding)

    assert store.get_binding(binding.binding_id) is not None
    assert store.get_binding("sb-nothing") is None


# ----------------------------------------------------------------------
# 第二步：同一个会话再写一次 = 就地更新，**同一行、同一个 id**
# ----------------------------------------------------------------------
def test_the_same_session_writes_in_place_and_keeps_the_original_id(
    store: SessionBindingStore,
) -> None:
    """同一个会话换了个焦点 ⇒ 同一行、同一个 ``binding_id``、``created=False``。

    一个「按 id 找」的实现会在这里插第二行并撞在唯一键上，报出一句「有人先写了」。
    """
    first = store.put_binding(make_binding(focus_refs=("workstream:ws-1",)))
    assert first.created is True

    moved = first.binding.refocused(
        focus_refs=("workstream:ws-2", "view:sv-1"),
        report_cursor="cursor-7",
        at=T0 + timedelta(minutes=5),
    )
    second = store.put_binding(moved)

    assert second.created is False
    assert second.binding.binding_id == first.binding.binding_id
    assert second.binding.focus_refs == ("workstream:ws-2", "view:sv-1")
    assert second.binding.report_cursor == "cursor-7"
    # 盘上只有一行 —— 「我换了一下在看哪个视图」不留下一条要清理的痕迹。
    assert len(store.bindings(workspace=WORKSPACE)) == 1


def test_a_new_id_for_the_same_session_lands_on_the_same_row(
    store: SessionBindingStore,
) -> None:
    """同一个会话带着一个**新铸的** id 写第二次：还是那一行。

    这一条与上面那条是两个用例，因为它们红的实现不同：上面那条红在「按 id 找」，
    这一条红在「拿 id 当身份、不认会话」—— 后者会让同一个会话在盘上变成两行，
    而「这个会话上次在看什么」从此有两个答案。
    """
    first = store.put_binding(make_binding())
    assert store.get_binding("sb-invented-by-caller") is None

    clone = first.binding.model_copy(update={"binding_id": "sb-invented-by-caller"})
    second = store.put_binding(clone)

    assert second.created is False
    # **回的是盘上那一行的 id**，不是本次交上来的那个 —— 一个直接回传入参的实现
    # 会让调用方拿着一个盘上不存在的 id 去做下一次写入。
    assert second.binding.binding_id == first.binding.binding_id
    assert store.get_binding("sb-invented-by-caller") is None
    assert len(store.bindings(workspace=WORKSPACE)) == 1


# ----------------------------------------------------------------------
# 同一个 id 配另一个会话：拒绝
# ----------------------------------------------------------------------
def test_one_id_for_two_sessions_is_refused(store: SessionBindingStore) -> None:
    """同一个 ``binding_id`` 配另一个会话 ⇒ ``StoreError``，而且第一个会话没被动过。

    静默覆盖会让**前一个会话**的焦点凭空变成另一个人的。
    """
    alice = store.put_binding(make_binding(principal="alice"))
    bob = make_binding(principal="bob", client_session_ref="sess-0002").model_copy(
        update={"binding_id": alice.binding.binding_id}
    )

    with pytest.raises(StoreError):
        store.put_binding(bob)

    kept = store.get_binding(alice.binding.binding_id)
    assert kept is not None and kept.principal == "alice"
    assert len(store.bindings(workspace=WORKSPACE)) == 1


def test_one_id_for_the_same_principal_on_another_client_is_refused(
    store: SessionBindingStore,
) -> None:
    """同一个人也不行：会话四格里换了 ``client_kind`` 就是另一个会话。"""
    first = store.put_binding(make_binding(client_kind="claude-code"))
    other = make_binding(client_kind="codex", client_session_ref="sess-0009").model_copy(
        update={"binding_id": first.binding.binding_id}
    )

    with pytest.raises(StoreError):
        store.put_binding(other)


# ----------------------------------------------------------------------
# 没有稳定 session id 的那种
# ----------------------------------------------------------------------
def test_two_anonymous_bindings_for_one_person_coexist(store: SessionBindingStore) -> None:
    """``client_session_ref`` 为 NULL 时两条绑定各自成立 —— 它们**不是**同一个会话。

    把 NULL 当成「相等」的实现在这里只会留下一行，而两个没有 id 的客户端看到的是
    同一个焦点。
    """
    store.put_binding(make_anonymous_binding(binding_id="sb-0001"))
    store.put_binding(make_anonymous_binding(binding_id="sb-0002", focus_refs=("view:sv-2",)))

    assert [row.binding_id for row in store.bindings(workspace=WORKSPACE)] == [
        "sb-0001",
        "sb-0002",
    ]

    # 没有会话引用时，那个 id **就是**身份的一部分：换一个 id 是另一次会话（第三行），
    # 而不是「同一个会话又动了一下焦点」。理由见
    # ``adapters/sqlite/session.py::_by_session`` 那一节 —— 一个把 NULL 当相等的实现
    # 会在这里把两个陌生客户端合成一行，而 B 读到的是 A 的焦点。
    third = make_anonymous_binding(binding_id="sb-0003", focus_refs=("view:sv-3",))
    assert store.put_binding(third).created is True
    assert len(store.bindings(workspace=WORKSPACE)) == 3

    # 而**同一个**匿名 id 再写一次仍然是就地更新：还是那一行，焦点换掉。
    same = make_anonymous_binding(binding_id="sb-0001", focus_refs=("view:sv-9",))
    written = store.put_binding(same)
    assert written.created is False
    assert written.binding.binding_id == "sb-0001"
    assert written.binding.focus_refs == ("view:sv-9",)
    assert len(store.bindings(workspace=WORKSPACE)) == 3


# ----------------------------------------------------------------------
# 清单
# ----------------------------------------------------------------------
def test_bindings_are_ordered_and_filtered(store: SessionBindingStore) -> None:
    """按 ``(principal, client_kind, binding_id)`` 升序，``principal=None`` 是所有人。"""
    store.put_binding(make_binding(principal="bob", client_kind="claude-code"))
    store.put_binding(
        make_binding(principal="alice", client_kind="codex", client_session_ref="sess-0002")
    )
    store.put_binding(
        make_binding(principal="alice", client_kind="claude-code", client_session_ref="sess-0003")
    )

    rows = store.bindings(workspace=WORKSPACE)
    assert [(row.principal, row.client_kind) for row in rows] == [
        ("alice", "claude-code"),
        ("alice", "codex"),
        ("bob", "claude-code"),
    ]
    assert [row.principal for row in store.bindings(workspace=WORKSPACE, principal="bob")] == ["bob"]
    assert store.bindings(workspace="ws-elsewhere") == ()

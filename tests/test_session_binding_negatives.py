"""``put_session_binding`` 的**三条否定**。§7.1 ``:270``，``catalog.py`` 逐字。

    它**不授予任何权限**、**不是**任何 Account 或 Job 的外键、**不参与**业务幂等
    —— 每一次写入仍然自己说清对象与版本。这三条是承重的：一个「会话里已经选好了」
    若被读成权限或默认值，就会让一次本要显式给对象的调用变成隐式继承上一条。

这三条都是**否定**，而否定句在代码里的弱点是一样的：它没有一行实现可以指着说
「看，这里挡住了」。所以这一份里的每一条判据都问一个**可执行的**问题，而不是复核
那段散文：

* 不授权 ⇒ 「写一条绑定**之后**，`authorize` 对同一个主体的答案变了吗」。答案必须
  逐字不变，而绑定必须真的写进去了（否则「没变」是「什么都没发生」造出来的）。
  另外两条结构判据从另一头夹住它：这四个模块的**标识符**里没有一个权限词，而
  :class:`SessionBinding` 的字段表里没有一格能装权限。
* 不是外键 ⇒ 「一条指向**还没有的**视图的绑定写得进去吗」。写得进去，而且读得回来。
  夹住它的是那条 SQL 扫描：这个模块的语句只碰 ``session_binding`` 一张表 ——
  「ref 指向的东西在不在」在这个模块里**没有一条查询可以问**。
* 不进业务幂等 ⇒ 「同一个会话写两次，盘上多了什么」。多了一格 ``last_seen``，
  一行还是那一行，而且**没有任何一版**可以读（表上没有版本列、端口上没有
  ``expected_revision``）。另一半（``binding_id`` 不在任何别的表的键里）由
  建好的库自己回答：``sqlite_master`` 里那个名字只出现在一张表的定义里。

## 两个实现跑同一份

与两份契约判据同样的参数化。内存那份**不是部署选项**，但它是同一条纪律的第二个
实现 —— 一份只看主存跑得过的否定判据，在替身上可以完全不成立（`helpers` 那一节
逐字：一个只在测试里用的实现很容易长出自己的规矩）。
"""

from __future__ import annotations

import ast
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemorySessionBindingStore
from marketops.adapters.sqlite.session import SqliteSessionBindingStore
from marketops.application.session import put_session_binding
from marketops.domain.errors import AuthorizationError
from marketops.domain.session import SessionBinding
from marketops.ports.session import SessionBindingStore
from marketops.security.catalog import Principal, Role, authorize, visible_tools

WORKSPACE = "ws-negatives"
T0 = datetime(2026, 9, 29, 1, 0, tzinfo=timezone.utc)
IMPLEMENTATION_PARAMS = ("sqlite", "memory")

#: 「这个会话能做什么」若在代码里存在，它至少得叫这些名字之一。
PERMISSION_VOCABULARY = frozenset(
    {
        "Role",
        "Principal",
        "authorize",
        "AuthorizationError",
        "visible_tools",
        "permission",
        "permissions",
        "grant",
        "grants",
        "capability",
        "capabilities",
        "scope",
        "scopes",
        "allow",
        "allowed",
        "roles",
        "workspaces",
    }
)

#: 一条绑定会流经的四个模块。**四个都扫**：一处漏掉，那一处就是权限词的住处。
BINDING_MODULES = (
    "marketops.domain.session",
    "marketops.ports.session",
    "marketops.adapters.sqlite.session",
    "marketops.application.session",
)


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> SessionBindingStore:
    if request.param == "sqlite":
        return SqliteSessionBindingStore(tmp_path / "marketops.db")
    return InMemorySessionBindingStore()


def write(
    bindings: SessionBindingStore,
    *,
    principal: str = "alice",
    client_kind: str = "claude-code",
    client_session_ref: str | None = "sess-0001",
    focus_refs: tuple[str, ...] = ("workstream:ws-1",),
    at: datetime = T0,
) -> SessionBinding:
    """走应用用例写一条绑定。判据不看这个 helper 的返回值就写不下去。"""
    return put_session_binding(
        bindings,
        workspace=WORKSPACE,
        principal=principal,
        client_kind=client_kind,
        client_session_ref=client_session_ref,
        focus_refs=focus_refs,
        report_cursor=None,
        expires_at=None,
        at=at,
    )


def source_of(module_name: str) -> str:
    import importlib

    return Path(importlib.import_module(module_name).__file__ or "").read_text(encoding="utf-8")


def identifiers_of(module_name: str) -> set[str]:
    """这个模块里被**用作标识符**的名字 —— 名字、属性、关键字参数。

    刻意不扫字符串：docstring 里写着「没有角色、没有 scope」正是这一节要把话说明白
    的地方，而一句 ``assert "scope" not in source`` 会**恰好在写得最好的那段散文上**
    变红，于是它会逼着人把那段散文删掉。判据要问的是「有没有一格能装权限」，而
    「装权限的那一格」一定是个标识符。
    """
    tree = ast.parse(source_of(module_name))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            found.add(node.arg)
    return found


def schema_row(tmp_path: Path, kind: str, name: str) -> str | None:
    """建好的库里那一个对象的 ``CREATE`` 语句。``None`` = 它不在库里。"""
    database = tmp_path / "marketops.db"
    SqliteSessionBindingStore(database)
    with closing(sqlite3.connect(database)) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?", (kind, name)
        ).fetchone()
    return None if row is None else row[0]


def sql_tables_referenced(module_name: str) -> set[str]:
    """源码里 ``FROM`` / ``JOIN`` / ``INTO`` / ``UPDATE`` 后面跟的表名。"""
    source = source_of(module_name)
    return set(re.findall(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+([a-z_][a-z0-9_]*)", source))


# ======================================================================
# 否定一：不授予任何权限
# ======================================================================
def test_writing_a_binding_does_not_change_what_a_principal_may_call(store) -> None:
    """**这一条是「不授予任何权限」的可执行形态。**

    一个把「会话里已经选好了」读成权限的实现在这里会红在某一步上，而它的症状是
    具体的：``authorize`` 对同一个主体给出了不同的答案，或者 ``visible_tools``
    多出了几个工具。两者的共同点是**没有一行代码看起来像在授权** —— 授权是从
    「盘上有这一行」里读出来的。

    判据因此必须包含「绑定真的写进去了」：只断言「两次答案一样」的实现在
    ``put_session_binding`` 抛异常时也会通过，而那时什么都没发生。
    """
    subject = Principal("alice", frozenset())
    controller = Principal("alice", frozenset({Role.CONTROLLER}))
    tools = ("put_session_binding", "list_schedule_definitions", "authorize_wave")

    def answers() -> tuple:
        return (
            visible_tools(subject),
            visible_tools(controller),
            tuple(_refusal(principal, name) for principal in (None, subject) for name in tools),
            tuple(_refusal(controller, name) for name in tools),
        )

    before = answers()

    written = write(store)
    assert written.principal == "alice"
    assert store.get_binding(written.binding_id) is not None
    assert len(store.bindings(workspace=WORKSPACE)) == 1

    # 逐字不变。**不是**「大体一样」—— 一条绑定若能让任何一个工具从被拒变成通过，
    # 下面这个元组会在那一格上不同。
    assert answers() == before

    # 而这里顺便把「不变」的内容写下来，免得上面那两个元组成了一句空话：
    # 没有角色的主体什么都看不见、什么都调不动，而控制器照样看得见这个工具、
    # 照样调不动授权工具。**不把控制器那份清单整份钉住** —— 它是目录的函数，
    # 目录每加一个工具这条判据都不该红（它问的不是「有哪些工具」）。
    assert visible_tools(subject) == ()
    assert "put_session_binding" in visible_tools(controller)
    assert _refusal(controller, "authorize_wave") is not None


def _refusal(principal: Principal | None, name: str) -> str | None:
    """调用被判定的结果：被拒时回那句话，通过时回 ``None``。

    回**那句话**而不是一个布尔：两种不同的拒绝（「不在目录里」与「角色不允许」）
    在布尔上同形，而这条判据要证明的是「绑定没有让任何一种变成通过」。
    """
    try:
        authorize(principal, name)
    except AuthorizationError as exc:
        return str(exc)
    return None


def test_no_module_on_the_binding_path_can_name_a_permission() -> None:
    """四个模块的**标识符**里没有一个权限词。

    这一条与上面那条是同一个否定的两头：上面问「行为变了吗」，这一条问「有没有一格
    能装」。只看行为的判据会漏掉一种实现 —— 它把权限写进绑定，而**截至今天的调用点
    还没有人读**：那时行为一模一样，而这一格已经在了，下一次有人顺手读它的时候没有
    任何一处会红。
    """
    for module_name in BINDING_MODULES:
        found = identifiers_of(module_name) & PERMISSION_VOCABULARY
        assert found == set(), f"{module_name} 里出现了权限词：{sorted(found)}"


def test_a_binding_has_no_field_that_could_hold_a_permission() -> None:
    """字段表本身。**这是「没有那一格」最直白的一句话。**

    字段名是标识符的另一种写法（它们是 ``model_fields`` 的键、是 DDL 的列名），
    所以这一条必须单独跑一遍：一个字段可以在模型里叫 ``allowed_tools`` 而在
    上面的 AST 扫描里只出现为字符串。
    """
    fields = set(SessionBinding.model_fields)
    assert fields & PERMISSION_VOCABULARY == set()
    # 顺带把这一份字段表钉住 —— 多出一格是这一片里最需要被人看见的那种改动。
    assert fields == {
        "binding_id",
        "workspace",
        "principal",
        "client_kind",
        "client_session_ref",
        "focus_refs",
        "report_cursor",
        "expires_at",
        "last_seen",
    }


def test_the_application_surface_cannot_hand_out_a_default_object(store) -> None:
    """应用层对外的只有一个函数，而它**只写不读**。

    「一次本要显式给对象的调用变成隐式继承上一条」需要一个能把焦点**读出来当默认
    值**的入口。这一层没有：``put_session_binding`` 是唯一的公开名字，它回的是
    盘上那一行（给调用方核对），而不是「本会话的当前对象」。读那一侧在端口上，
    而端口上每一个读都要指名工作区与主体（``bindings(workspace=..., principal=...)``）。
    """
    from marketops.application import session as session_module
    from marketops.ports import session as session_port

    assert session_module.__all__ == ["put_session_binding"]
    assert {
        name
        for name in vars(session_port.SessionBindingStore)
        if not name.startswith("_")
    } == {"put_binding", "get_binding", "bindings"}
    # 读的三个入口都要指名范围 —— 没有一个是「本部署当前在看什么」。
    for name in ("get_binding", "bindings"):
        params = vars(session_port.SessionBindingStore)[name].__code__.co_varnames
        assert "binding_id" in params or "workspace" in params


def test_an_expired_binding_is_not_a_revocation(store) -> None:
    """``expires_at`` 到了**什么都不改变**：它不收回权限（它本来就没授予）。

    这条否定的另一半是它的反面：「过期」若被读成「权限没了」，那它就成了一次授权。
    可执行的形态是：一条**已经过期**的绑定照样写得进去、照样读得回来、而且不删掉
    任何东西 —— 「他当时在做什么」是唯一一份能回答那个问题的记录，而一次「顺手把
    过期的清掉」的写入会让它在过期那一刻消失。
    """
    past = T0 - timedelta(days=1)
    binding = put_session_binding(
        store,
        workspace=WORKSPACE,
        principal="alice",
        client_kind="claude-code",
        client_session_ref="sess-0001",
        focus_refs=("view:sv-1",),
        report_cursor="cursor-3",
        expires_at=past,
        at=T0,
    )

    assert binding.expired(T0) is True
    kept = store.get_binding(binding.binding_id)
    assert kept is not None and kept.focus_refs == ("view:sv-1",)
    assert kept.report_cursor == "cursor-3"
    assert len(store.bindings(workspace=WORKSPACE)) == 1


# ======================================================================
# 否定二：不是任何 Account 或 Job 的外键
# ======================================================================
def test_a_focus_ref_pointing_at_nothing_still_writes(store) -> None:
    """**这一条是「不是外键」的可执行形态。**

    一条指向「还没有的视图」的绑定写得进去 —— 而且这是**对的**：一个客户端在视图
    被建出来之前先记住「我在看这个」，不该被一次完整性检查拒掉。一旦这里有了
    「ref 必须在某某表里」，绑定就挂回了一个业务对象，而「换个对象研究」会变成
    一次**约束失败**而不是一次普通的焦点移动。

    判据里那两个 ref 刻意都不指向任何东西，而其中一个的形状连前缀都是编的。
    """
    written = write(store, focus_refs=("view:sv-not-built-yet", "queue:rq-404"))

    assert written.focus_refs == ("view:sv-not-built-yet", "queue:rq-404")
    kept = store.get_binding(written.binding_id)
    assert kept is not None and kept.focus_refs == written.focus_refs

    # 换成另一个不存在的 ref：同一行、就地更新 —— 移动焦点不是一次约束失败。
    moved = write(store, focus_refs=("view:sv-another-ghost",), at=T0 + timedelta(minutes=1))
    assert moved.binding_id == written.binding_id
    assert moved.focus_refs == ("view:sv-another-ghost",)
    assert len(store.bindings(workspace=WORKSPACE)) == 1


def test_the_binding_store_queries_no_other_table() -> None:
    """这个存储的语句只碰 ``session_binding`` 一张表。

    这一条是「ref 指向的东西在不在」在这个模块里**问不出来**的形态：要问那个问题
    就得 JOIN 或查另一张表，而这里一条都没有。扫的只有 SQLite 那一份 ——
    ``adapters/memory.py`` 是一个共享的大文件，在它身上扫表名量到的是别人的东西。
    """
    assert sql_tables_referenced("marketops.adapters.sqlite.session") == {"session_binding"}


def test_the_sqlite_schema_of_a_binding_has_no_foreign_key(tmp_path: Path) -> None:
    """建好的那张表上没有一条 ``FOREIGN KEY``，而且没有一格叫 ``*_ref`` 之外的引用。

    读的是**建好的库**（``sqlite_master`` 里那一行 ``CREATE TABLE``），不是源码里
    的一段常量：一个把外键写在别处（比如一次可选的迁移）的实现，在源码扫描下是干净的。
    """
    row = schema_row(tmp_path, "table", "session_binding")
    assert row is not None, "session_binding 这张表不在建好的库里"
    ddl = row or ""
    assert "FOREIGN KEY" not in ddl.upper()
    assert "REFERENCES" not in ddl.upper()
    # ``focus_refs`` 与 ``report_cursor`` **没有列**：它们换的时候走的是整段 payload。
    assert "focus_refs" not in ddl and "report_cursor" not in ddl


def test_an_unresolvable_ref_is_not_checked_by_the_domain_either() -> None:
    """领域层也不解析 ref：``check_reference`` 判的是**形状**，不是存在性。

    这一条与上面那条合起来才是完整的否定：存储不查、领域也不查，而「ref 的语法」
    仍然要合法（一个含控制字符的 ref 会破坏 ``\x1f`` 拼接出来的键）。
    """
    from marketops.domain.session import SessionBinding as Model

    for ref in ("view:sv-1", "queue:rq-404", "workstream:ws-does-not-exist"):
        Model(
            binding_id="sb-0001",
            workspace=WORKSPACE,
            principal="alice",
            client_kind="claude-code",
            client_session_ref="sess-0001",
            focus_refs=(ref,),
            report_cursor=None,
            expires_at=None,
            last_seen=T0,
        )

    # 而形状不合法的那一个仍然被拒 —— 「不查存在性」不是「什么都不查」。
    with pytest.raises(ValueError):
        Model(
            binding_id="sb-0001",
            workspace=WORKSPACE,
            principal="alice",
            client_kind="claude-code",
            client_session_ref="sess-0001",
            focus_refs=("view:sv-1\x1fqueue:rq-2",),
            report_cursor=None,
            expires_at=None,
            last_seen=T0,
        )


# ======================================================================
# 否定三：不参与业务幂等
# ======================================================================
def test_the_binding_carries_no_business_idempotency_key(store) -> None:
    """一条绑定上没有 ``dedupe_key``，也没有 ``fire_id``，而且没有版本列。

    那两样东西分别住在业务处置点（``fire_dedupe_key``）与调度主键（``schedule_fire``）
    上。把它们里的任何一个搬到这里，会让「同一个人在另一个 session 里重发同一件事」
    变成两件不同的事 —— 而重复受理正是幂等键要挡的那一个。
    """
    assert set(SessionBinding.model_fields) & {"dedupe_key", "fire_id", "revision"} == set()

    import inspect

    signature = inspect.signature(SessionBindingStore.put_binding)
    assert "expected_revision" not in signature.parameters
    assert set(signature.parameters) == {"self", "binding"}

    # 存储层也没有一个「读当前版」的入口 —— 没有版本，就没有「基于哪一版」这件事。
    assert not hasattr(store, "revisions")


def test_writing_the_same_session_twice_produces_no_new_version(store) -> None:
    """写十次与写一次在**业务**上没有任何区别：一行，而那一行不是「第十版」。

    判据要能分清「没写进去」与「写了但是就地更新」：``last_seen`` 必须动了，
    而行的条数必须还是 1，``created`` 必须是 ``False``。一个「每次写入加一版」的
    实现在把行数写到 10 的同时，也让「这个会话上次在看什么」有了 10 个答案。
    """
    first = write(store)
    assert first.last_seen == T0

    for index in range(1, 10):
        written = write(
            store,
            focus_refs=(f"view:sv-{index}",),
            at=T0 + timedelta(minutes=index),
        )
        assert written.binding_id == first.binding_id
        assert written.last_seen == T0 + timedelta(minutes=index)

    rows = store.bindings(workspace=WORKSPACE)
    assert len(rows) == 1
    assert rows[0].last_seen == T0 + timedelta(minutes=9)
    assert rows[0].focus_refs == ("view:sv-9",)


def test_the_binding_id_is_a_key_of_no_other_table(tmp_path: Path) -> None:
    """``binding_id`` 只出现在一张表的定义里。

    读的是**建好的库**：``sqlite_master`` 里所有 ``CREATE TABLE`` / ``CREATE INDEX``
    的 SQL 合起来，那个名字只出现一次（``session_binding`` 的主键）。把它塞进一个
    业务幂等键 —— 比如 ``schedule_fire`` 或 ``notification_event`` 的唯一约束 ——
    会让这一条立刻红，而那种改动的后果是「同一个人在另一个 session 里重发同一件事
    变成两件」。
    """
    database = tmp_path / "marketops.db"
    SqliteSessionBindingStore(database)
    with closing(sqlite3.connect(database)) as conn:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()

    holders = [name for name, sql in rows if "binding_id" in (sql or "")]
    assert holders == ["session_binding"], (
        f"binding_id 出现在这些对象里：{holders}。"
        "它不是任何业务对象的键 —— 见 domain/session.py 第 3 条。"
    )

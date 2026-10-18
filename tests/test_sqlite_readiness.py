"""部署之前那一次**只读**的探问：这个状态库现在能不能被这一版代码打开。

## 它为什么单独有一组判据

同一个问题 ``ensure_schema`` 也答，但答在**写路径**上：它抛，而抛出去需要一个
正在写的人接着。2026-09-23 的现场里没有那个人 —— 一个 v1 的库让**每一条**读写
该库的工具都失败，而失败长成的样子是调用方上下文里一句没有 message 的
``Error executing tool``（``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §G2）。
「工具坏了」与「库还没迁移」处置不同，而当时唯一分得出它们的东西是一句没有正文
的错误。

所以这一层的判据有两组，而它们问的不是同一件事：

* **它答得对不对** —— 八种处境各读一次（下面第一节）。
* **它答的时候动没动盘** —— 「只读」在 ``readiness.py`` 的 docstring 里被拆成
  三条具体的边界（不建库、看得见 WAL、伴生文件会被建出来），三条各有一条执行器
  （第二节）。一条写成散文的「只读」会在这三条里任意一条上被违反而没人发现。

## 这里**不**重测三句拒绝的措辞

那句措辞住在 ``db.read_schema_state``，而「它会不会抛、抛的是哪一句」在
``tests/test_sqlite_migrate.py``（版本）与 ``tests/test_sqlite_pool.py``（绑定）
里已经被钉住了。这一组钉的是**另一个方向**：同一个返回值被**第二个读者**拿到时，
仍然是同一句话 —— 所以断言的形状是「这里读出来的 message 与 ``ensure_schema``
抛出来的那一句逐字相同」，而不是把措辞抄第二遍。

## 夹具怎么来

真写路径造库（``db.connect`` + ``db.ensure_schema``），再按用例下调版本那一格。
手工造一个「像 v2 的库」在这里没有意义 —— 被问的是「版本那一格写着什么」，
而 ``ensure_schema`` 读的也只有那一格。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from marketops.adapters.sqlite import db
from marketops.adapters.sqlite.readiness import DatabaseReadiness, read_database_readiness
from marketops.domain.errors import StoreError

#: 一个**当前版本**的库：真写路径造出来，盖着章、没有行。
def _current(db_path: Path) -> Path:
    conn = db.connect(db_path)
    try:
        db.ensure_schema(conn)
    finally:
        conn.close()
    return db_path


def _set_version(db_path: Path, value: str) -> Path:
    """把版本那一格改成一个给定的值。**不重放 DDL、不盖章** —— 这就是那一步的手工版。"""
    conn = db.connect(db_path)
    try:
        db.set_meta(conn, db.SCHEMA_VERSION_KEY, value)
    finally:
        conn.close()
    return db_path


# ----------------------------------------------------------------------
# 第一节：八种处境
# ----------------------------------------------------------------------
def test_a_database_that_is_not_there_yet_is_absent_not_a_failure(tmp_path: Path) -> None:
    """盘上没有这个文件 ⇒ ``absent``，而且**起得来**。

    这一格不是故障：第一次写入本来就要把库建出来。把它报成故障，会让每一个
    全新的部署在第一次启动前先看到一句拒绝。
    """
    target = tmp_path / "state" / "marketops.db"
    readiness = read_database_readiness(target)

    assert readiness.standing is db.SchemaStanding.ABSENT
    assert readiness.startable is True
    assert readiness.recorded is None, "没读到的版本是「无从得知」，不是 0"
    assert readiness.expected == db.SCHEMA_VERSION
    assert readiness.message == "", "这一格没什么要说的 —— 与「省了」不是一回事"


def test_an_empty_database_is_uninitialized_and_will_be_stamped(tmp_path: Path) -> None:
    """文件在、``schema_meta`` 不在 ⇒ ``uninitialized``，同样起得来。

    它与上一格的区别是「谁建了它」，而这一层**刻意不区分**「本服务还没写过的空
    文件」与「别人建的一个空库」：``ensure_schema`` 对两者的动作是同一个（重放
    DDL、盖章），而自检若在这里说得比它更细，就会出现「自检说这个库不行、
    启动时它照常盖了章」。自检的每一句话都必须是启动时**真的会发生**的那件事。
    """
    target = tmp_path / "marketops.db"
    sqlite3.connect(target).close()  # 一个零字节的文件就是一个合法的空库

    readiness = read_database_readiness(target)

    assert readiness.standing is db.SchemaStanding.UNINITIALIZED
    assert readiness.startable is True
    assert readiness.recorded is None
    assert readiness.message == ""


def test_a_database_someone_else_initialised_reads_the_same_as_an_empty_file(
    tmp_path: Path,
) -> None:
    """有一张别人的表、没有 ``schema_meta`` —— 与零字节文件**同形**。

    这是上一条的另一半：断言的是那两格**相等**，不是「都是 uninitialized」。
    一个想「说得更细」的实现会在这里给出一句「这个库不是本服务的」，而那句话
    在启动时不会发生（``ensure_schema`` 照样重放 DDL 并盖章）。
    """
    target = tmp_path / "marketops.db"
    conn = sqlite3.connect(target)
    conn.execute("CREATE TABLE someone_elses (x INTEGER)")
    conn.commit()
    conn.close()

    readiness = read_database_readiness(target)
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()

    assert readiness.standing is read_database_readiness(empty).standing
    assert readiness.startable is True


def test_a_current_database_is_current_with_nothing_to_say(tmp_path: Path) -> None:
    """版本对、没有行 ⇒ ``current``，``message`` 是空串。

    ``message`` 为空是判据本身：``--check`` 把一个非空的 ``message`` 当作
    「这一版代码打不开它」，而 ``_serve()`` 按 ``startable`` 拒绝 —— 两者都读
    这一个字段，所以一个「有点什么要说」的 current 会让一个健康的部署被判成坏的。
    """
    readiness = read_database_readiness(_current(tmp_path / "marketops.db"))

    assert readiness.standing is db.SchemaStanding.CURRENT
    assert readiness.startable is True
    assert (readiness.recorded, readiness.expected) == (db.SCHEMA_VERSION, db.SCHEMA_VERSION)
    assert readiness.message == ""


def test_a_database_from_before_this_build_names_the_migration_command(tmp_path: Path) -> None:
    """旧库的拒绝要**点名那条命令**，并把那个版本**自己欠着什么**说出来。

    §15(c) 逐字要求那句诊断长成 ``schema v1, code expects v2, run ...`` 的形状 ——
    因为「不做自动迁移」与「不告诉人怎么做」是两件事。两个版本各有一句自己的
    note（v1 欠血统、v2 欠词表），所以这里除了命令之外还要验**是那一句 note**：
    一句笼统的「版本旧了」答不出运维要判断的那一问（这个库是怎么来的、还值不值得迁）。
    """
    from marketops.adapters.sqlite.db import _VERSION_NOTES

    behind = _set_version(_current(tmp_path / "marketops.db"), "2")
    readiness = read_database_readiness(behind)

    assert readiness.standing is db.SchemaStanding.BEHIND
    assert readiness.startable is False
    assert (readiness.recorded, readiness.expected) == (2, db.SCHEMA_VERSION)
    assert f"python -m marketops.adapters.sqlite.migrate --db {behind}" in readiness.message
    assert _VERSION_NOTES[2] in readiness.message, "报的是这一个版本自己那一句，不是笼统的版本旧了"


def test_a_database_newer_than_the_code_is_refused_without_a_migration_command(
    tmp_path: Path,
) -> None:
    """比代码新的版本：拒绝，但**不说「跑那条迁移」**。

    迁移只有一个方向。对一个 v9 的库说「先跑一次迁移」会把人送到一条走不通的路上，
    而它读起来像一条可执行的建议 —— 这是这条判据真正的风险点，所以断言的是
    **那句话不出现**，而不是「它会拒绝」（后者上面那条已经覆盖了旧的那个方向）。
    """
    ahead = _set_version(_current(tmp_path / "marketops.db"), "9")
    readiness = read_database_readiness(ahead)

    assert readiness.standing is db.SchemaStanding.AHEAD
    assert readiness.startable is False
    assert (readiness.recorded, readiness.expected) == (9, db.SCHEMA_VERSION)
    assert "adapters.sqlite.migrate" not in readiness.message


def test_a_version_that_is_not_a_number_is_unknown_and_never_reported_as_zero(
    tmp_path: Path,
) -> None:
    """版本那一格不是一个版本号 ⇒ ``unknown``，``recorded`` 是 ``None``。

    这一格以前会以一句裸 ``ValueError`` 从 ``int(current)`` 里炸出来 —— 一句回溯
    看起来像代码坏了，而事实是那个库的那一格被写坏了。``recorded`` 必须是
    ``None`` 而不是 0：一个把读不出来报成 0 的实现，会让「没有版本号」与
    「版本是 v0」在两个不同的库里长得一样。
    """
    broken = _set_version(_current(tmp_path / "marketops.db"), "v1")
    readiness = read_database_readiness(broken)

    assert readiness.standing is db.SchemaStanding.UNKNOWN
    assert readiness.startable is False
    assert readiness.recorded is None, "读不出来的一格写成 0，就是把未知写成已知"


def test_rows_without_a_workspace_binding_are_refused_before_the_first_read(
    tmp_path: Path,
) -> None:
    """有行、却没有工作区绑定 ⇒ ``unbound``，起不来。

    这个状态在写路径上构造不出来（第一行写入就会绑），所以它只可能来自一次篡改
    或一次半途而废的手工操作。它在这里当场被拒，而不是等到某一次读把那些行读成
    「这个部署的」—— 那时报告上看起来只是「证据更多了」。
    """
    target = _current(tmp_path / "marketops.db")
    conn = db.connect(target)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO market_account (account_id, workspace, payload, stored_at) "
            "VALUES (?, ?, ?, ?)",
            ("an-account", "a-workspace", "{}", "2026-09-24T00:00:00Z"),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()

    readiness = read_database_readiness(target)

    assert readiness.standing is db.SchemaStanding.UNBOUND
    assert readiness.startable is False
    assert "没有工作区绑定" in readiness.message


def test_a_file_that_is_not_a_database_is_unreadable_and_reports_the_original_words(
    tmp_path: Path,
) -> None:
    """不是一个数据库文件 ⇒ ``unreadable``，并把 sqlite 那句原话照报出来。

    那句原话（这里是 ``file is not a database``）是唯一能区分「路径指错了」与
    「这个文件坏了」的东西 —— 换成一个自己拼的结论，两种故障就被抹成一句
    「这个库有问题」，而它们的排查方向完全不同。
    """
    target = tmp_path / "marketops.db"
    target.write_bytes(b"this is not a database, it is a text file\n")

    readiness = read_database_readiness(target)

    assert readiness.standing is db.SchemaStanding.UNREADABLE
    assert readiness.startable is False
    assert readiness.recorded is None
    assert "file is not a database" in readiness.message
    assert str(target) in readiness.message, "要点名是哪一个文件 —— 运维手里只有路径"


def test_a_directory_in_place_of_the_database_is_unreadable_not_an_exception(
    tmp_path: Path,
) -> None:
    """路径上是一个**目录** ⇒ 也是 ``unreadable``，而不是从这里抛出去。

    ``sqlite3.connect`` 在拿不到一个可打开的文件时抛 —— 而这一次探问跑在
    ``_compose`` 里，那里抛出去的异常没有任何人接着：``--check`` 会变成一堆堆栈，
    而它的契约是「形状固定，故障路径上也一样」。所以两个 ``except sqlite3.Error``
    都要在。这一条走的是**连接那一个**（``connect`` 就失败），上一条走的是
    **读那一个**（连上了、第一句查询才失败）。
    """
    target = tmp_path / "marketops.db"
    target.mkdir()

    readiness = read_database_readiness(target)

    assert readiness.standing is db.SchemaStanding.UNREADABLE
    assert readiness.startable is False
    assert "打不开状态库" in readiness.message


@pytest.mark.parametrize(
    ("standing", "startable"),
    [
        (db.SchemaStanding.ABSENT, True),
        (db.SchemaStanding.UNINITIALIZED, True),
        (db.SchemaStanding.CURRENT, True),
        (db.SchemaStanding.BEHIND, False),
        (db.SchemaStanding.AHEAD, False),
        (db.SchemaStanding.UNKNOWN, False),
        (db.SchemaStanding.UNBOUND, False),
        (db.SchemaStanding.UNREADABLE, False),
    ],
    ids=lambda value: value.value if isinstance(value, db.SchemaStanding) else str(value),
)
def test_startable_is_derived_from_the_standing(
    standing: db.SchemaStanding, startable: bool
) -> None:
    """八种处境里**只有三种**起得来，而这一格是从 ``standing`` 算出来的。

    逐个列出来不是为了复述实现：这一格是「这个部署起不起得来」的唯一答案，
    ``--check`` 的退出码与 ``_serve()`` 的拒绝都读它。做成一个另填的字段
    （或两处各写一遍判据），就会出现「报告说可以起、启动时拒绝」—— 而那正是
    ``--check`` 与 ``_serve()`` 共用一次读数要消灭的东西。

    **前两种都在里面**，这一条是本组最容易写错的地方：``absent`` 与
    ``uninitialized`` 答的是「还没有历史」，而不是「有历史但对不上」。
    """
    readiness = DatabaseReadiness(
        path="/somewhere/marketops.db",
        standing=standing,
        recorded=None,
        expected=db.SCHEMA_VERSION,
        message="",
    )
    assert readiness.startable is startable


def test_every_standing_has_a_headline_of_its_own(tmp_path: Path) -> None:
    """八种处境各给一行摘要，且**互不相同**。

    与其他几句断言不同，这一条不测措辞好不好 —— 它测的是设计上的一个必须：
    ``headline`` 会与 ``message`` 一起被印在 ``--check`` 的输出里。一个把两种处境
    归成同一行的实现，会把「库比代码新」与「库根本打不开」在报告里抹成一句，
    而这两件事的处置一个是指向匹配版本的工具、一个是去看路径与权限。

    互不相同是**充分**条件里最省事的一个：它不规定每句该怎么写，只排除「两种
    处境共用一行」这一种错误。
    """
    lines = {
        standing: DatabaseReadiness(
            path="/somewhere/marketops.db",
            standing=standing,
            recorded=1,
            expected=db.SCHEMA_VERSION,
            message="",
        ).headline
        for standing in db.SchemaStanding
    }

    assert len(set(lines.values())) == len(db.SchemaStanding), lines
    assert all(line.strip() for line in lines.values())


def test_the_payload_shape_is_the_same_for_every_standing(tmp_path: Path) -> None:
    """``payload()`` 恒为这七个键 —— 每个处境都是，包括「没有 state」。

    ``--check`` 的输出会被脚本解析，而一个会变形的输出会让解析它在**故障路径**
    上崩掉，而故障路径正是它被写出来的理由。这一条按**所有**处境跑一遍，因为
    只有「每个处境都同一组键」才是那个契约。
    """
    target = _current(tmp_path / "marketops.db")
    payload = read_database_readiness(target).payload()

    assert set(payload) == {
        "path",
        "standing",
        "recorded",
        "expected",
        "startable",
        "headline",
        "message",
    }
    assert payload["path"] == str(target)
    assert payload["standing"] == "current"


def test_render_puts_the_command_on_its_own_like_a_person_would_read_it(tmp_path: Path) -> None:
    """``render()`` 是 ``_serve()`` 打印的那一段：一行摘要，然后是整句。

    没有整句时**只有一行**（不留一个空行）—— 终端里一个空行会被读成「下面还有
    内容没打完」，而那正是「起得来」与「起不来」在日志里最容易混起来的地方。
    """
    healthy = read_database_readiness(_current(tmp_path / "healthy.db"))
    assert healthy.render() == f"状态库 {healthy.path}：{healthy.headline}"

    behind = read_database_readiness(_set_version(_current(tmp_path / "behind.db"), "2"))
    rendered = behind.render()
    assert rendered.startswith(f"状态库 {behind.path}：{behind.headline}\n")
    assert "adapters.sqlite.migrate" in rendered


# ----------------------------------------------------------------------
# 第二节：它答的时候动没动盘
# ----------------------------------------------------------------------
def test_asking_about_a_missing_database_creates_nothing_at_all(tmp_path: Path) -> None:
    """库不在时**连打开都不打开** —— 于是不建库、也不建它的父目录。

    ``--check`` 是只读的自检，而一个会把目录树建出来的自检在「目录还没准备好」
    这种真故障面前会先把故障修掉一半，于是运维看不到它
    （``tests/test_mcp_entry.py`` 钉着那条断言）。这一条是它在**适配器层**的
    另一半：那个「不建目录」的行为是从这里来的，而这里也单独钉一次 ——
    否则那句话只有一个执行器，而它在另一层。

    这一条**不是**「盘上一个字节都不多」（下一条说明了为什么）。
    """
    target = tmp_path / "never-created" / "marketops.db"

    read_database_readiness(target)

    assert not target.exists()
    assert not target.parent.exists()


def test_reading_an_existing_database_files_only_the_two_companion_files(tmp_path: Path) -> None:
    """已有的库读一遍之后：**原来的文件一个字节都没变**，多出来的只有伴生文件。

    这是「只读」在这个仓库里的确切含义，而它必须写成这一种形状 —— ``mode=ro``
    连一个 WAL 的库时会**建出** ``-wal`` / ``-shm``（实测）。所以：

    * 一个「盘上什么都没有多」的断言在这里是**假的**，写下来就是一条假的判据；
    * 而一个「它不改库的内容」的断言是真的，且正是这条探问被允许跑在一个活库上的
      全部理由。

    取的是 ``.exists()`` 与字节，不是 mtime：mtime 会被一次 checkpoint 或一个
    什么都没写的 close 改掉，用它测「改没改」会得到与内容无关的答案。
    """
    target = _current(tmp_path / "marketops.db")
    before = {path.name: path.read_bytes() for path in sorted(tmp_path.glob("marketops.db*"))}

    read_database_readiness(target)

    after = {path.name: path.read_bytes() for path in sorted(tmp_path.glob("marketops.db*"))}
    assert {name for name in after if name in before} == set(before)
    for name, content in before.items():
        assert after[name] == content, f"{name} 的内容被这一次「只读」探问改掉了"
    assert set(after) - set(before) <= {"marketops.db-wal", "marketops.db-shm"}, (
        "除了那两个伴生文件之外，这次探问不该在盘上多出任何东西"
    )


def test_it_sees_a_version_that_is_still_only_in_the_wal(tmp_path: Path) -> None:
    """写在 ``-wal`` 里、还没 checkpoint 的那一格，**读得到**。

    这一条是本组里最容易被换成一个「更安全」的读法而静默失效的地方。备份那条
    路径用的是 ``mode=ro&immutable=1``（那边就是要读主文件那一刻的字节），而
    ``immutable=1`` **看不见 WAL** —— 实测的失败原文是 ``no such table:
    schema_meta``，也就是「表还不在」被读成「这个库是空的」。

    而最坏的形态不是报错，是**过期**：迁移刚提交、还没 checkpoint 时，那个读法会
    说「这个库还是 v1」，而它已经迁完了。所以这里造一个**活着的写者**：它把一个
    新的版本号只写进 WAL，不 checkpoint、也不退出，然后断言读到的就是那一个新值
    —— 主文件里那一个旧值读出来会让这条转红。

    末尾那一段是这一条的**前提**，不是重复：它当场证明那个场景真的区分了两种读法
    （``immutable=1`` 看到的是别的东西），否则一个「主文件与 WAL 恰好一致」的
    夹具会让这条判据对错误的实现照样绿。
    """
    target = _current(tmp_path / "marketops.db")
    writer = db.connect(target)
    try:
        # 真正让这一格留在 WAL 里的是「写者还开着」—— checkpoint 发生在最后一个
        # 连接关闭时。下面这一个值只在 WAL 里，主文件里仍然是 SCHEMA_VERSION。
        db.set_meta(writer, db.SCHEMA_VERSION_KEY, "2")

        readiness = read_database_readiness(target)

        assert readiness.standing is db.SchemaStanding.BEHIND, (
            "读到的是主文件里那个旧版本号 —— 也就是这次读看不见 WAL"
        )
        assert readiness.recorded == 2

        blinded = sqlite3.connect(f"file:{target}?mode=ro&immutable=1", uri=True)
        try:
            seen: object = blinded.execute(
                "SELECT value FROM schema_meta WHERE key = ?", (db.SCHEMA_VERSION_KEY,)
            ).fetchone()
        except sqlite3.Error as exc:
            seen = f"{type(exc).__name__}: {exc}"
        finally:
            blinded.close()
        assert seen != ("2",), (
            "``immutable=1`` 也看到了那一格 —— 那么这个夹具不再区分两种读法，"
            f"上面那条断言就不再有内容（它看到的是 {seen!r}）"
        )
    finally:
        writer.close()


def test_the_verdict_here_is_the_sentence_the_write_path_raises(tmp_path: Path) -> None:
    """同一份库：这里读出来的 ``message`` 与 ``ensure_schema`` 抛的**逐字相同**。

    这条判据的是「判据与措辞只有一份」那件事，而它有一个很具体的失败形态：
    自检说要跑 A 那条命令、真正启动时说要跑 B。两个表面各拼一遍那句话就必然走到
    那里，而工单里贴的永远是先跑到的那句。

    所以断言是**相等**而不是「都提到了迁移」：后者对一个漂开了几个字的副本照样绿。
    ``where`` 那一格两处都从库里读（``PRAGMA database_list``），所以路径也应当相同。
    """
    target = _set_version(_current(tmp_path / "marketops.db"), "2")
    readiness = read_database_readiness(target)

    conn = db.connect(target)
    try:
        with pytest.raises(StoreError) as excinfo:
            db.ensure_schema(conn)
    finally:
        conn.close()

    assert readiness.message == str(excinfo.value)
    assert readiness.message != "", "这一条的前提是那个库真的被拒了"

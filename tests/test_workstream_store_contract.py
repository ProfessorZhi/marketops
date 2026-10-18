"""``WorkstreamStore`` / ``EffectiveSceneStore`` 契约：**同一组判据，两个实现各跑一遍**。

两个实现：

* ``adapters/sqlite/workstreams.py`` —— 主存。幂等的载体是 DDL 上那条 UNIQUE
  ``(workspace, scene_id, scene_version, idempotency_key)``，CAS 的载体是
  ``WHERE workstream_id = ? AND revision = ?`` 加 ``rowcount``。
* ``adapters/memory.py`` —— 测试替身。**没有锁、没有事务**（并发不在这里）。

## 这里装着端口 docstring 逐字点名的那两条负向用例

``marketops/ports/workstreams.py`` 在模块末尾写了：「两条都是**写路径**的判据，
所以它们各自要有一个负向用例 —— 见契约测试里
``test_a_second_launch_with_another_body_is_refused`` 与
``test_an_effective_scene_version_cannot_be_rewritten``」。名字逐字如上，
因为一个改过名的负向用例在报告里读起来与「这条判据没有执行器」一样。

## 有一条判据**刻意不在这里**

「写完重开一个 store 还能读到」只对持久化实现成立，内存实现按定义做不到。
与 ``test_outreach_store_contract.py`` / ``test_scene_store_contract.py`` 同一条
处置：契约里出现第一个例外之后，第二个就不会有人再争论了。

## 那半句最容易被漏掉的话：「返回值必须是读回来的那一行」

``put_launch`` 的端口 docstring 逐字：「**返回值必须是读回来的那一行**，不是入参
对象」。一个直接回传入参的实现在报告上与真正的读回一模一样 —— 所以它有一条自己的
用例（``test_a_replay_hands_back_the_row_on_disk_not_the_argument``），而它用的
量具是**两次调用之间被改掉的两格**（``created_at`` / ``launched_by``）：只有真的
读了盘，第二次那两格才会是**第一次**的值。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryEffectiveSceneStore, InMemoryWorkstreamStore
from marketops.adapters.sqlite.workstreams import (
    SqliteEffectiveSceneStore,
    SqliteWorkstreamStore,
)
from marketops.domain.errors import StoreError
from marketops.domain.util import sha256_text
from marketops.domain.workstream import (
    EffectiveCell,
    EffectiveScene,
    LaunchRecord,
    SceneConflict,
    Workstream,
    WorkstreamQueue,
    WorkstreamState,
    effective_scene_digest,
    launch_id_of,
    workstream_id,
)

UTC = timezone.utc

T0 = datetime(2026, 9, 29, 1, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
WORKSPACE = "ws-natural-grass"
OTHER_WORKSPACE = "ws-rye-blend"
SCENE = "scene-uk-natural-grass"
KEY = "launch-2026-09-29-natural-grass"


def _frozen(scene_id: str = SCENE, version: int = 1) -> str:
    """``SceneVersion.frozen_sha256`` 的形状：**截断到 32 位**的摘要。

    写死的常数摘要也能过校验，但那样两版不同的 Scene 会带同一个 ``compiled_from``，
    而下面的判据里有一条判的就是「换一版声明是不是另一份编译结果」。从内容算，
    判据才在判东西。
    """
    return sha256_text(f"{scene_id}@{version}").lower()[:32]


# --- 两份实现 -----------------------------------------------------------------


@pytest.fixture(params=["sqlite", "memory"])
def launches(request, tmp_path: Path):
    """``WorkstreamStore`` 的两个实现之一。名字进测试 id —— 红了时一眼看得见是谁。"""
    if request.param == "sqlite":
        return SqliteWorkstreamStore(tmp_path / "state" / "marketops.db")
    return InMemoryWorkstreamStore()


@pytest.fixture(params=["sqlite", "memory"])
def scenes(request, tmp_path: Path):
    """``EffectiveSceneStore`` 的两个实现之一。"""
    if request.param == "sqlite":
        return SqliteEffectiveSceneStore(tmp_path / "state" / "marketops.db")
    return InMemoryEffectiveSceneStore()


# --- 构造器 -------------------------------------------------------------------


def make_launch(**overrides) -> LaunchRecord:
    payload = {
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "scene_version": 1,
        "idempotency_key": KEY,
        "workstream_id": workstream_id(SCENE, 1),
        "request_digest": sha256_text("第一次请求"),
        "created_at": T0,
        "launched_by": "alice",
    }
    payload.update(overrides)
    payload.setdefault(
        "launch_id",
        launch_id_of(
            workspace=payload["workspace"],
            scene_id=payload["scene_id"],
            scene_version=payload["scene_version"],
            idempotency_key=payload["idempotency_key"],
        ),
    )
    return LaunchRecord(**payload)


def make_stream(ordinal: int = 1, **overrides) -> Workstream:
    payload = {
        "workspace": WORKSPACE,
        "scene_id": SCENE,
        "scene_version": 1,
        "ordinal": ordinal,
        "launch_id": launch_id_of(
            workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
        ),
        "state": WorkstreamState.RUNNING,
        "revision": 1,
        "queue": WorkstreamQueue.RUN_ADMITTED,
        "created_at": T0,
        "updated_at": T0,
    }
    payload.update(overrides)
    payload.setdefault("workstream_id", workstream_id(payload["scene_id"], ordinal))
    return Workstream(**payload)


def make_cell(field: str = "geo_scope", **overrides) -> EffectiveCell:
    value = overrides.pop("value", "GB")
    payload = {
        "field": field,
        "value": value,
        "source": "policy-uk-grass",
        "provenance": "declared",
        "unit": "-",
        "locale": "-",
        "owner": WORKSPACE,
        "precedence": 0,
    }
    payload.update(overrides)
    payload.setdefault("value_sha256", sha256_text(value))
    return EffectiveCell(**payload)


def make_scene(**overrides) -> EffectiveScene:
    payload = {
        "scene_id": SCENE,
        "version": 1,
        "workspace": WORKSPACE,
        "compiled_from": _frozen(),
        "at": T0,
        "compiler": "preview_effective_scene",
        "cells": (make_cell(),),
    }
    payload.update(overrides)
    return EffectiveScene(**payload)


# --- LaunchRecord -------------------------------------------------------------


def test_a_launch_is_readable_by_its_business_key(launches):
    """四格业务键全在签名里 —— 一个只按 ``launch_id`` 读的实现在重发那条路上没有可比的量。"""
    assert launches.get_launch(
        workspace=WORKSPACE,
        scene_id=SCENE,
        scene_version=1,
        idempotency_key=KEY,
    ) is None

    written = make_launch()
    launches.put_launch(written)

    found = launches.get_launch(
        workspace=WORKSPACE,
        scene_id=SCENE,
        scene_version=1,
        idempotency_key=KEY,
    )
    assert found == written
    # 四格里的每一格都真的进了键：少判一格，一条拿别的场景名来读的调用会读到它。
    assert (
        launches.get_launch(
            workspace=WORKSPACE,
            scene_id="scene-de-natural-grass",
            scene_version=1,
            idempotency_key=KEY,
        )
        is None
    )
    assert (
        launches.get_launch(
            workspace=WORKSPACE,
            scene_id=SCENE,
            scene_version=2,
            idempotency_key=KEY,
        )
        is None
    )
    assert (
        launches.get_launch(
            workspace=WORKSPACE,
            scene_id=SCENE,
            scene_version=1,
            idempotency_key="launch-something-else",
        )
        is None
    )
    assert (
        launches.get_launch(
            workspace=OTHER_WORKSPACE,
            scene_id=SCENE,
            scene_version=1,
            idempotency_key=KEY,
        )
        is None
    )


def test_a_replay_hands_back_the_row_on_disk_not_the_argument(launches):
    """**这一条是「返回值必须是读回来的那一行」的执行器。**

    第二份请求与第一份**内容同摘要**（同一个请求重发），而它的 ``created_at`` /
    ``launched_by`` 是另两格。一个直接回传入参的实现会把第二次那两格交出去 ——
    而它在报告上与真正的读回一模一样。

    量具刻意选这两格：它们**不进** ``request_digest``（那个摘要判的是「这一次意图
    的内容是不是同一份」），所以它们可以被改掉而不把这一次重发变成一次冲突。
    """
    first = make_launch()
    launches.put_launch(first)

    second = make_launch(created_at=T1, launched_by="bob")
    replayed = launches.put_launch(second)

    assert replayed is not second
    assert replayed.created_at == T0, "回的是入参对象，不是盘上那一行"
    assert replayed.launched_by == "alice", "回的是入参对象，不是盘上那一行"
    # 盘上那一行没有被第二次写改掉。
    assert (
        launches.get_launch(
            workspace=WORKSPACE,
            scene_id=SCENE,
            scene_version=1,
            idempotency_key=KEY,
        )
        == first
    )


def test_a_second_launch_with_another_body_is_refused(launches):
    """**端口 docstring 逐字点名的那一条负向用例。**

    键撞上、摘要不等 ⇒ 报错并点名。少了它，「同一个键交另一份请求」会静默拿回第一次
    那一行，而调用方以为自己开的那一轮跑的是他要的东西。
    """
    first = make_launch()
    launches.put_launch(first)

    with pytest.raises(StoreError) as caught:
        launches.put_launch(make_launch(request_digest=sha256_text("另一份请求")))

    message = str(caught.value)
    assert first.request_digest in message, "拒绝要点名盘上那一份的摘要"
    assert sha256_text("另一份请求") in message, "拒绝要点名本次那一份的摘要"
    # 「一个字都没写」：盘上还是第一次那一行。
    assert (
        launches.get_launch(
            workspace=WORKSPACE,
            scene_id=SCENE,
            scene_version=1,
            idempotency_key=KEY,
        )
        == first
    )


def test_a_launch_from_another_workspace_never_lands(launches):
    """别的工作区的一次启动**写不进去**，而且一行都不留。

    ## 它为什么判的是「写被拒」而不是「读不到」

    ``launch_record`` 在 ``db.py::OWNED_TABLES`` 里，而那一份清单的用途逐字是
    「一个装了两个部署的行的库要能被当场拒绝」。所以这条判据的落点是**工作区绑定**
    —— 两个实现在比较业务键之前先撞上它，而「另一个工作区的那一行读不出来」是它的
    **后果**（那一行根本进不来），不是一条独立的读过滤。

    真正会在这里出错的实现是「按四格业务键收下、不判这个库绑在谁身上」的那种：
    它会把别的租户的一次启动与自己那一次混装在同一个库里，而两行都读得回来 ——
    报告上只显示「这次启动找到了一条记录」。

    形状与 ``test_scheduling_store_contract.py::test_a_fire_from_another_workspace_never_lands``
    逐条相同（那一条的 docstring 也写着为什么断言的是 ``StoreError`` 而不是更内层的
    那一类冲突）。
    """
    first = launches.put_launch(make_launch())

    with pytest.raises(StoreError) as caught:
        launches.put_launch(make_launch(workspace=OTHER_WORKSPACE, launched_by="carol"))

    assert OTHER_WORKSPACE in str(caught.value), "拒绝要点名是哪一个工作区"

    mine = launches.get_launch(
        workspace=WORKSPACE, scene_id=SCENE, scene_version=1, idempotency_key=KEY
    )
    assert mine == first and mine.launched_by == "alice"
    assert (
        launches.get_launch(
            workspace=OTHER_WORKSPACE,
            scene_id=SCENE,
            scene_version=1,
            idempotency_key=KEY,
        )
        is None
    ), "被拒的那一次一行都没落"


# --- Workstream：一次创建、CAS 更新 --------------------------------------------


def test_a_workstream_reads_back_as_none_when_it_is_not_there(launches):
    """``None``，**不是**一个空壳对象 —— 一个默认值填满的行会让「没这个工作流」
    与「它什么字段都没有」同形。"""
    assert launches.get_workstream(workstream_id(SCENE, 7)) is None


def test_a_workstream_is_created_once(launches):
    """``expected_revision=None`` 的意思是**必须不存在**，而不是「放行」。"""
    first = make_stream()
    launches.put_workstream(first, expected_revision=None)
    assert launches.get_workstream(first.workstream_id) == first

    with pytest.raises(StoreError) as caught:
        launches.put_workstream(make_stream(name="另一次"), expected_revision=None)

    message = str(caught.value)
    assert first.workstream_id in message, "拒绝要点名是哪一个 id"
    assert "revision=1" in message, "拒绝要报出盘上那一版是哪一版"
    assert launches.get_workstream(first.workstream_id) == first


def test_a_first_create_at_a_revision_above_one_is_refused(launches):
    """带着别的 revision 创建，会让「这个 id 已经存在」与「我写的是第 n 版」变成一件事。"""
    with pytest.raises(StoreError):
        launches.put_workstream(make_stream(revision=2), expected_revision=None)
    assert launches.get_workstream(workstream_id(SCENE, 1)) is None


def test_a_stale_revision_does_not_overwrite(launches):
    """**CAS 的执行器**：过期的那一次不覆盖新的一次。

    去掉 ``WHERE revision = ?``（或让 ``rowcount`` 不被看），第二次写会成功 ——
    而「有人先改了」这件事在盘上不留痕。
    """
    launches.put_workstream(make_stream(), expected_revision=None)

    fresh = make_stream(
        revision=2, state=WorkstreamState.PAUSED, pause_reason="等资料", updated_at=T1
    )
    launches.put_workstream(fresh, expected_revision=1)
    assert launches.get_workstream(fresh.workstream_id) == fresh

    stale = make_stream(revision=2, name="我读到的是第 1 版", updated_at=T1)
    with pytest.raises(StoreError) as caught:
        launches.put_workstream(stale, expected_revision=1)

    message = str(caught.value)
    assert "第 1 版" in message, "拒绝要点名调用方读到的那一版"
    assert "第 2 版" in message, "拒绝要点名盘上现在是哪一版"
    # 新的一次还在：过期的没有覆盖它。
    assert launches.get_workstream(fresh.workstream_id) == fresh


def test_an_update_to_a_workstream_that_is_not_there_names_the_id(launches):
    """「不存在」与「过期了」的处置相反（去查 id / 去重读再重试），报文必须分得开。"""
    ghost = make_stream(revision=4, ordinal=4)
    with pytest.raises(StoreError) as caught:
        launches.put_workstream(ghost, expected_revision=3)

    message = str(caught.value)
    assert ghost.workstream_id in message
    assert "不存在" in message
    assert "过期" not in message, "两种原因合成一句会让处置变得不可判"


def test_a_write_that_skips_a_revision_is_refused(launches):
    """「改完对象直接写回」跳过了一次自增 —— 于是并发的另一个写者会**成功**。"""
    launches.put_workstream(make_stream(), expected_revision=None)
    with pytest.raises(StoreError):
        launches.put_workstream(make_stream(revision=3, updated_at=T1), expected_revision=1)
    assert launches.get_workstream(workstream_id(SCENE, 1)).revision == 1


def test_a_workstream_is_listed_for_the_workspace_the_library_is_bound_to(launches):
    """``workspace`` 是必填关键字：省略它的签名会让「本工作区的每一个」变成「盘上的每一个」。

    升序是**字符串**的升序（端口 docstring 逐字「按 ``workstream_id`` 升序」），
    而 id 里带 scene_id —— 所以两个场景的工作流是按场景名交错的，不是按序号。

    **它今天判得到的那一半变窄了。** 原先这一条把另一行（``workspace=OTHER_WORKSPACE``
    的另一个 scene）真写进库里、再判它读不回来 —— 而那一步在 SQLite 那份上被
    ``bind_workspace`` 拒了（``workstream`` 在 ``db.py::OWNED_TABLES`` 里），于是
    同一组断言在两个实现上一个靠「写被拒」成立、一个靠「写进去了但筛掉」成立。
    现在「另一个工作区那一行读不出来」由**构造**保证
    （见 ``test_a_workstream_from_another_workspace_never_lands``），
    这一条判的是清单本身：哪些在、按什么次序。
    与 ``test_scheduling_store_contract.py::test_definitions_are_scoped_to_one_workspace``
    同一条处置。
    """
    launches.put_workstream(make_stream(ordinal=1), expected_revision=None)
    launches.put_workstream(make_stream(ordinal=2), expected_revision=None)
    launches.put_workstream(
        make_stream(ordinal=1, scene_id="scene-de-natural-grass"), expected_revision=None
    )

    mine = launches.workstreams(workspace=WORKSPACE)
    assert [stream.workstream_id for stream in mine] == [
        workstream_id("scene-de-natural-grass", 1),
        workstream_id(SCENE, 1),
        workstream_id(SCENE, 2),
    ]
    assert launches.workstreams(workspace=OTHER_WORKSPACE) == ()
    assert launches.workstreams(workspace="ws-nobody") == ()


def test_a_workstream_from_another_workspace_never_lands(launches):
    """别的工作区的一个工作流**写不进去**，而且一行都不留。

    ``workstream`` 在 ``db.py::OWNED_TABLES`` 里，所以一个库只装一个工作区 ——
    这条判据的落点是绑定，而两个实现给出的是同一个答案（原先一个收下、一个被拒，
    因为内存那份没有绑定那一格）。
    """
    launches.put_workstream(make_stream(ordinal=1), expected_revision=None)

    with pytest.raises(StoreError) as caught:
        launches.put_workstream(
            make_stream(
                ordinal=1, workspace=OTHER_WORKSPACE, scene_id="scene-se-rye-blend"
            ),
            expected_revision=None,
        )

    assert OTHER_WORKSPACE in str(caught.value), "拒绝要点名是哪一个工作区"
    assert launches.workstreams(workspace=OTHER_WORKSPACE) == ()
    assert launches.next_ordinal("scene-se-rye-blend") == 1, "序号那一格没有被它动过"


def test_ordinals_come_from_one_place(launches):
    """序号是业务键的一半，而它只有一个来源。

    两处各数一遍的那天，两个工作流会拿到同一个 ``workstream_id``，而存储那一层
    只报一句主键冲突。
    """
    assert launches.next_ordinal(SCENE) == 1, "空盘上第一个是 1，不是 0"

    launches.put_workstream(make_stream(ordinal=1), expected_revision=None)
    assert launches.next_ordinal(SCENE) == 2
    launches.put_workstream(make_stream(ordinal=2), expected_revision=None)
    assert launches.next_ordinal(SCENE) == 3

    # 按 scene_id 数，不是全局数 —— 另一个场景的第一个仍然是 1。
    assert launches.next_ordinal("scene-de-natural-grass") == 1
    # 另一个工作区的另一个场景**写不进来**（库绑在本工作区上），而它被拒之后
    # 本场景的序号照旧 —— 一次被拒的写不许动到任何一格。
    with pytest.raises(StoreError):
        launches.put_workstream(
            make_stream(
                ordinal=1, workspace=OTHER_WORKSPACE, scene_id="scene-se-rye-blend"
            ),
            expected_revision=None,
        )
    assert launches.next_ordinal(SCENE) == 3


# --- EffectiveScene：只增不改 --------------------------------------------------


def test_an_effective_scene_is_readable_by_scene_and_version(scenes):
    written = make_scene()
    scenes.put_scene(written)

    found = scenes.get_scene(SCENE, 1)
    assert found == written
    assert found.digest == effective_scene_digest(written), (
        "读回来的那一份要能重算出同一个摘要 —— 否则『还是编译时那一份吗』没有判据"
    )
    assert scenes.get_scene(SCENE, 2) is None
    assert scenes.get_scene("scene-de-natural-grass", 1) is None
    assert scenes.versions(SCENE) == (1,)
    assert scenes.versions("scene-de-natural-grass") == ()


def test_versions_name_what_is_on_disk_and_they_are_ordered(scenes):
    """``versions`` 是 ``get_effective_scene`` 那条拒绝的原料：它要把盘上有的版本点名写出来。"""
    scenes.put_scene(make_scene(version=1))
    scenes.put_scene(make_scene(version=3, compiled_from=_frozen(version=3)))
    scenes.put_scene(make_scene(version=2, compiled_from=_frozen(version=2)))

    assert scenes.versions(SCENE) == (1, 2, 3)
    assert [scene.version for scene in scenes.scenes(workspace=WORKSPACE)] == [1, 2, 3]


def test_an_effective_scene_version_cannot_be_rewritten(scenes):
    """**端口 docstring 逐字点名的那一条负向用例**，也是任务书 §5 承重点 4 的变异靶子。

    同一个 ``(scene_id, version)`` 配另一份内容 ⇒ 报错并点名。一个「已存在就覆盖」
    的实现会让同一 ``(scene, version)`` 在两次部署之间成为两份不同的东西，而引用
    它的每一处都看不出来 —— 「这一版当时编译成什么」会随读它的时刻变。
    """
    first = make_scene(cells=(make_cell(value="GB"),))
    scenes.put_scene(first)

    rewritten = make_scene(cells=(make_cell(value="DE"),), at=T1, compiler="start_workstream")
    with pytest.raises(StoreError) as caught:
        scenes.put_scene(rewritten)

    message = str(caught.value)
    assert repr(SCENE) in message, "拒绝要点名是哪一个 scene_id"
    assert "1" in message, "拒绝要点名是哪一版"
    assert first.digest in message and rewritten.digest in message, (
        "拒绝要点名两份摘要 —— 「不是同一份」要说得出来是哪两份"
    )
    # 盘上还是第一份：改写没有发生。
    assert scenes.get_scene(SCENE, 1) == first


def test_the_same_compilation_twice_is_idempotent_though_the_clock_moved(scenes):
    """同一份内容、两次动作 ⇒ 幂等，**不是**冲突。

    这是冻结表 §3.2 那条链要求的：``preview_effective_scene`` 先编译、看到的就是
    这一版，``start_workstream`` 再把它冻结落库 —— 两次的 ``at`` / ``compiler``
    是**这次动作**的两格，不是**这份内容**的两格。判据若做成整份负载逐字节相等，
    预览之后启动会在 ``put_scene`` 上报一次冲突，而那条链根本走不通。

    判据是内容摘要（``effective_scene_digest`` 不含那两格），所以这一条与上一条
    并不矛盾：上一条改的是**格子里的值**，摘要跟着变。
    """
    first = make_scene(at=T0, compiler="preview_effective_scene")
    scenes.put_scene(first)

    scenes.put_scene(make_scene(at=T1, compiler="start_workstream"))

    stored = scenes.get_scene(SCENE, 1)
    assert stored == first, "先落盘的那一份胜出：后一次不覆盖它"
    assert stored.at == T0 and stored.compiler == "preview_effective_scene"
    assert scenes.versions(SCENE) == (1,)


def test_a_foreign_workspace_reads_an_empty_effective_scene_list(scenes):
    """跨工作区读不到 —— 落在**存储**那一层按工作区过滤，而不是靠调用方记得自己过滤。

    ``get_scene`` 的签名里没有 ``workspace``（它按 ``(scene_id, version)`` 取，
    与冻结的 DDL 主键一致），所以这条判据的落点是 ``scenes()``：
    一个按工作区枚举的读者读到的必须是本工作区的那几份。

    **它今天判得到的那一半变窄了**：原先这一条先把另一份（``scene-se-rye-blend``
    / ``OTHER_WORKSPACE``）写进库再判读不回来，而那一步在 SQLite 那份上被
    ``bind_workspace`` 拒了（``effective_scene`` 在 ``db.py::OWNED_TABLES`` 里）。
    现在「别人的那一份不在库里」由构造保证
    （见 ``test_an_effective_scene_from_another_workspace_never_lands``），
    这一条判的是枚举那一格：本工作区的读得回来，别的工作区与不存在的工作区都读到空。
    """
    scenes.put_scene(make_scene())

    mine = scenes.scenes(workspace=WORKSPACE)
    assert [scene.scene_id for scene in mine] == [SCENE]
    assert scenes.scenes(workspace=OTHER_WORKSPACE) == ()
    assert scenes.scenes(workspace="ws-nobody") == ()


def test_an_effective_scene_from_another_workspace_never_lands(scenes):
    """别的工作区的一版有效场景**写不进去**，而且一行都不留。

    ``(scene_id, version)`` 是主键、比工作区那一列更靠前，所以这里用的是**另一个
    scene_id**：判的不是主键，而是绑定那一层。
    """
    scenes.put_scene(make_scene())

    with pytest.raises(StoreError) as caught:
        scenes.put_scene(
            make_scene(scene_id="scene-se-rye-blend", workspace=OTHER_WORKSPACE)
        )

    assert OTHER_WORKSPACE in str(caught.value), "拒绝要点名是哪一个工作区"
    assert scenes.scenes(workspace=OTHER_WORKSPACE) == ()
    assert scenes.versions("scene-se-rye-blend") == ()


def test_the_conflicts_are_kept_apart_from_the_cells(scenes):
    """冲突与格子是两张表：合成一张的实现在盘上把一处冲突写成了一格普通的值。"""
    scene = make_scene(
        cells=(make_cell(field="offer", value="offer-a"),),
        conflicts=(
            SceneConflict(
                field="geo_scope",
                readings=("policy-uk-grass", "policy-eu-grass"),
                detail="两份政策给出不同的国家集合",
            ),
        ),
        not_reported=("sales_motion",),
    )
    scenes.put_scene(scene)

    stored = scenes.get_scene(SCENE, 1)
    assert [cell.field for cell in stored.cells] == ["offer"]
    assert [conflict.field for conflict in stored.conflicts] == ["geo_scope"]
    assert stored.not_reported == ("sales_motion",)
    assert stored.digest == effective_scene_digest(scene)

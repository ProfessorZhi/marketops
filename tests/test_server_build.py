"""服务报在数据根里的那份构建（§15(b)）：写的那一侧与读的那一侧。

## 它为什么单独有一组判据

判据本身不在这里，在 ``build_stamp.compare_builds``（那一组在
``tests/test_build_stamp.py``）。这里钉的是**一份指纹穿过进程边界的那一段路**：
服务把它写下去（``publish_server_build``，在 ``interfaces/mcp`` 的 ``_serve`` 里，
排在 ``run()`` **之前**）、worker 把它读回来（``read_server_build``）。

这段路有三个不会自己叫出来的失效方式，所以各有一条执行器：

1. **写出来的不是 ``payload()`` 那个形状。** 两处各拼一遍形状的那一天，读的一方会
   按一个**旧的**形状解析一个新写的文件 —— 而它长得像「这个文件是坏的」。
2. **写到一半被读到。** 读的人可能在服务正在写的那一刻打开它，而一份被读了一半的
   JSON 会以「这个文件是坏的」的形状出现 —— 那是**误导**：这个文件没有坏。
3. **读不出来的时候说成「没报过」。** 「文件不在」与「文件在、但读不出来」处置不同
   （前者问服务起没起过，后者问这个文件怎么了），而合成一句之后，一个磁盘问题会说成
   一个部署事实。

## 这里**不**重测那份指纹本身

``read_build_stamp`` 怎么读 git、读不出来怎么说，在 ``tests/test_build_stamp.py``
里被逐格钉过。这一组用一份**手搭的**指纹当输入（``_stamp``）就不必再跑 git ——
被问的是「这一份东西能不能原样穿过去」。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import pytest

from marketops.adapters import server_build as server_build_module
from marketops.adapters.build_stamp import LOADED_BUILD, BuildStamp
from marketops.adapters.server_build import (
    SERVER_BUILD_FILENAME,
    publish_server_build,
    read_server_build,
    server_build_path,
)


def _stamp(
    *,
    package_file: str = "/p/__init__.py",
    source_tree: str = "/p",
    revision: str | None = "a" * 40,
    dirty: bool | None = False,
    read_at: str = "2026-09-24T00:00:00+00:00",
    errors: Mapping[str, str] | None = None,
) -> BuildStamp:
    """一份手搭的指纹（与 ``tests/test_build_stamp.py`` 里那份同形，各自独立：
    两个文件测的是两件事，共用一个助手会把它们的寿命绑在一起）。"""
    return BuildStamp(
        package_file=package_file,
        source_tree=source_tree,
        revision=revision,
        dirty=dirty,
        read_at=read_at,
        errors=dict(errors or {}),
    )


def _runtime(tmp_path: Path) -> Path:
    """一个数据根。**刻意不是 ``tmp_path`` 本身**：真实的数据根下还有 ``state/``
    以外的目录，用一层子目录才问得出「这个文件是不是在它该在的地方」。"""
    return tmp_path / "runtime"


def _write(data_root: Path, body: str) -> Path:
    """把一份**给定的**正文放到服务该写的位置（给要摆坏文件的测试用）。"""
    path = server_build_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ----------------------------------------------------------------------
# 第一节：写下去的那一份
# ----------------------------------------------------------------------
def test_the_receipt_lands_where_both_sides_look_for_it(tmp_path: Path) -> None:
    """路径是 ``<数据根>/state/server-build.json``，目录不存在时被建出来。

    路径单独成函数（而不是两处各拼一次）的理由就在这里：写它的那一侧与读它的那一侧
    是两个进程，拼错的症状是「worker 说这个部署上还没有服务报过构建」—— 一句看起来
    像部署事实、实际是拼写差异的话。
    """
    data_root = _runtime(tmp_path)
    assert not data_root.exists(), "前提：这个数据根还没有被碰过"

    written = publish_server_build(data_root, stamp=_stamp())

    assert written == server_build_path(data_root)
    assert written == data_root / "state" / SERVER_BUILD_FILENAME
    assert written.is_file()


def test_the_receipt_is_the_stamp_plus_who_wrote_it(tmp_path: Path) -> None:
    """文件内容 = ``payload()`` 那六个键 **加 ``pid``**，一个不多一个不少。

    ``pid`` 是给读的人用的：一个不说自己是谁写的回执，在「服务重起过没有」这个问题上
    帮不上忙 —— 而那正是这一格要答的。
    """
    stamp = _stamp(errors={"revision": "读不出来"}, revision=None, dirty=None)
    written = publish_server_build(_runtime(tmp_path), stamp=stamp)

    body = json.loads(written.read_text(encoding="utf-8"))

    assert set(body) == set(stamp.payload()) | {"pid"}
    assert {key: body[key] for key in stamp.payload()} == stamp.payload()
    assert body["pid"] == os.getpid()


def test_publishing_without_a_stamp_reports_the_loaded_build(tmp_path: Path) -> None:
    """不给指纹时写的是 :data:`LOADED_BUILD` —— 也就是 ``_serve`` 走的那条路。

    这一条是**接线判据**：生产路径上没有人传 ``stamp=``，所以「缺省是什么」就是
    「服务报的是哪一份」。缺省若漂到一个现场重读的指纹上，它答的就成了「写文件那一刻
    那棵树是什么样」，而那个方向的错正是 ``build_stamp`` 存在的理由（它刻意不懒读）。
    """
    written = publish_server_build(_runtime(tmp_path))

    body = json.loads(written.read_text(encoding="utf-8"))

    assert {key: body[key] for key in LOADED_BUILD.payload()} == LOADED_BUILD.payload()


def test_a_receipt_is_written_as_text_so_it_can_be_read_by_a_person(tmp_path: Path) -> None:
    """落地的是一个**人读得懂**的文件：缩进、键有序、以换行结尾。

    运维在这个文件上做的事是「看一眼、比一比、贴进工单」。一行的 JSON 三样都做不到，
    而紧凑格式在这里没有任何好处 —— 读它的是 worker 与 ``cat``，不是高频路径。

    键有序另有一条理由：**两份回执的 diff** 是人拿它们互相比对的方式，而无序的键会让
    同一个部署的两份回执看起来处处不同。
    """
    written = publish_server_build(_runtime(tmp_path), stamp=_stamp())

    raw = written.read_text(encoding="utf-8")
    lines = raw.splitlines()

    assert raw.endswith("\n")
    assert lines[0] == "{" and lines[-1] == "}"
    assert all(line.startswith('  "') for line in lines[1:-1]), lines
    keys = [line.strip().split('"')[1] for line in lines[1:-1]]
    assert keys == sorted(keys), f"键没有排序：{keys}"


def test_publishing_twice_leaves_no_temporary_file(tmp_path: Path) -> None:
    """连写两次之后，``state/`` 里只有那一个文件（临时文件不留残渣）。

    临时文件是**实现**（原子换入要用它），但它落在数据根里，也就是落在一个运维会去
    ``ls`` 的地方。留残渣的代价不是磁盘，是「这些是什么东西」。
    """
    data_root = _runtime(tmp_path)

    publish_server_build(data_root, stamp=_stamp(revision="b" * 40))
    publish_server_build(data_root, stamp=_stamp(revision="c" * 40))

    assert sorted(path.name for path in (data_root / "state").iterdir()) == [
        SERVER_BUILD_FILENAME
    ]
    assert read_server_build(data_root).stamp == _stamp(revision="c" * 40)


# ----------------------------------------------------------------------
# 第二节：写不出来的那一次
# ----------------------------------------------------------------------
def test_a_swap_that_fails_leaves_the_previous_receipt_untouched(tmp_path: Path, monkeypatch) -> None:
    """换入失败 ⇒ 抛 ``OSError``，上一份**逐字节不变**，临时文件被清掉。

    这一条钉的是「原子」那一句话里**唯一**读不出来的半句：换入不成时，读的人拿到的
    必须是**完整的一旧**，不能是半新半旧。名字换了、留残渣、或者把旧的那份删掉，
    在这里都会红。
    """
    data_root = _runtime(tmp_path)
    publish_server_build(data_root, stamp=_stamp(revision="b" * 40))
    before = server_build_path(data_root).read_bytes()

    def refuse(src, dst, **kwargs):  # noqa: ANN001 -- 与 os.replace 同形
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(server_build_module.os, "replace", refuse)

    with pytest.raises(OSError) as excinfo:
        publish_server_build(data_root, stamp=_stamp(revision="c" * 40))

    assert excinfo.value.errno == 28
    assert server_build_path(data_root).read_bytes() == before
    assert sorted(path.name for path in (data_root / "state").iterdir()) == [
        SERVER_BUILD_FILENAME
    ], "换入失败之后临时文件还在"


def test_a_publish_that_cannot_be_written_does_not_mask_its_own_failure(tmp_path: Path) -> None:
    """写不出来的**那一次**要被报出来，而不是被清理临时文件时的第二个错盖掉。

    ``state`` 是个普通文件时，``mkdir`` 就会失败；清理那一句（``tmp.unlink``）在这个
    形状下**也会**失败（它的父路径不是一个目录）。清理必须吞掉自己的错，否则读那一行
    的人看到的是一句关于 ``…server-build.json.<pid>.tmp`` 的话 —— 而他要修的是那个
    ``state``。两句话的分辨成本很高，因为两句都是 ``OSError``。
    """
    data_root = _runtime(tmp_path)
    state = data_root / "state"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("这里本该是个目录\n", encoding="utf-8")

    with pytest.raises(OSError) as excinfo:
        publish_server_build(data_root, stamp=_stamp())

    assert str(state) in str(excinfo.value), f"点名的不是那个占位的文件：{excinfo.value}"
    assert ".tmp" not in str(excinfo.value), f"被清理时的错盖掉了：{excinfo.value}"
    assert state.read_text(encoding="utf-8") == "这里本该是个目录\n", "占位文件被改动了"


# ----------------------------------------------------------------------
# 第三节：读回来的那一次
# ----------------------------------------------------------------------
def test_a_stamp_survives_the_round_trip(tmp_path: Path) -> None:
    """写下去的与读回来的**逐格相等**，这就是这条边界的用途。"""
    data_root = _runtime(tmp_path)
    stamp = _stamp(revision=None, dirty=None, errors={"revision": "读不出来", "dirty": "读不出来"})
    publish_server_build(data_root, stamp=stamp)

    reading = read_server_build(data_root)

    assert reading.readable
    assert reading.stamp == stamp
    assert reading.pid == os.getpid()
    assert reading.problem == ""
    assert reading.path == server_build_path(data_root)


def test_a_data_root_where_no_service_ever_published_says_exactly_that(tmp_path: Path) -> None:
    """文件不在 ⇒ 读不出来，理由说的是「这个数据根上还没有服务报过构建」。

    理由里还要给出**第二种**情形（服务起的时候用的是另一个数据根）：两者的症状都是
    「文件不在」，而只有后者是那个真正危险的部署形状 —— 两侧读写的 runtime 状态根本
    不是一个。
    """
    reading = read_server_build(_runtime(tmp_path))

    assert not reading.readable
    assert reading.stamp is None and reading.pid is None
    assert "还没有服务报过" in reading.problem
    assert str(server_build_path(_runtime(tmp_path))) in reading.problem


@pytest.mark.parametrize(
    ("body", "why"),
    [
        pytest.param("这不是 JSON\n", "不是一份 JSON", id="乱文本"),
        pytest.param("[]\n", "不是一个对象", id="数组"),
        pytest.param('"一个字符串"\n', "不是一个对象", id="字符串"),
        pytest.param("[1, 2, 3]\n", "不是一个对象", id="数字数组"),
    ],
)
def test_a_receipt_that_is_not_a_json_object_says_what_it_is(
    tmp_path: Path, body: str, why: str
) -> None:
    """坏文件要说清是哪一种坏（不是 JSON / 不是一个对象），并带上路径。

    这两句各自对应一件不同的事：前者是「有人写了别的东西进去」，后者是「这个文件曾经
    是个 JSON，但不是这份回执」。合成一句「文件是坏的」会让读的人去查一个没坏的东西。
    """
    data_root = _runtime(tmp_path)
    written = _write(data_root, body)

    reading = read_server_build(data_root)

    assert not reading.readable
    assert why in reading.problem, f"{reading.problem}（这里要说的是「{why}」）"
    assert str(written) in reading.problem


def test_a_receipt_that_is_not_a_stamp_names_the_cell(tmp_path: Path) -> None:
    """形状不对 ⇒ 复用 ``from_payload`` 那句点名（不在这里拼第二句）。"""
    data_root = _runtime(tmp_path)
    body = dict(_stamp().payload())
    body["dirty"] = 0
    _write(data_root, json.dumps(body))

    reading = read_server_build(data_root)

    assert not reading.readable
    assert "不是一份构建指纹" in reading.problem
    assert "dirty" in reading.problem


@pytest.mark.parametrize(
    "pid",
    [
        pytest.param(None, id="缺这一格"),
        pytest.param("1234", id="字符串"),
        pytest.param(True, id="true"),
        pytest.param(1.5, id="小数"),
    ],
)
def test_a_receipt_without_a_usable_pid_is_still_readable(tmp_path: Path, pid: object) -> None:
    """``pid`` 那一格不可用时，**指纹照样读得出来**，``pid`` 是 ``None``。

    这一格是**附注**，不是指纹的一部分：把它的缺失升级成「读不出来」会让一次「服务
    报过构建、只是没说自己是谁」在 worker 那里变成「这个部署上还没有服务报过构建」
    —— 那是一句不成立的话，而且它指向的处置（去看服务起没起）完全不对。

    ``true`` 单列一条：JSON 的 ``true`` 落回 Python 是 ``bool``，而 ``bool`` 是 ``int``
    的子类 —— 不显式排除它，一个 ``pid: true`` 会被当成 pid 1。
    """
    data_root = _runtime(tmp_path)
    body = dict(_stamp().payload())
    if pid is None:
        body.pop("pid", None)
    else:
        body["pid"] = pid
    _write(data_root, json.dumps(body))

    reading = read_server_build(data_root)

    assert reading.readable, reading.problem
    assert reading.stamp == _stamp()
    assert reading.pid is None
    assert reading.problem == ""


def test_a_receipt_that_cannot_be_read_says_it_could_not_be_read(tmp_path: Path) -> None:
    """文件在、但读不动 ⇒ 说的是「读不了」，**不是**「还没报过」。

    这两句的处置不同：一句让人去看服务起没起过，另一句让人去看这个文件与它的权限。
    合成一句之后，一个磁盘问题会被读成一个部署事实。
    """
    data_root = _runtime(tmp_path)
    written = _write(data_root, "{}\n")
    written.chmod(0o000)
    try:
        reading = read_server_build(data_root)
    finally:
        written.chmod(0o600)

    assert not reading.readable
    assert "读不了" in reading.problem
    assert "还没有服务报过" not in reading.problem


def test_the_problem_is_empty_exactly_when_there_is_a_stamp(tmp_path: Path) -> None:
    """``problem`` 为空 **当且仅当** 读到了指纹 —— 两格不会各说各的。

    读的人按 ``problem`` 决定说什么，按 ``stamp`` 决定判什么。两者在一条路径上不一致
    的那一天，回执会一边说「读不出来」一边把一份指纹交给核对 —— 而核对是会放行的。
    """
    good = _runtime(tmp_path / "good")
    publish_server_build(good, stamp=_stamp())
    broken = _runtime(tmp_path / "broken")
    _write(broken, "不是一个 JSON\n")
    assert broken.joinpath("state", SERVER_BUILD_FILENAME).is_file(), "前提：坏文件确实落在那了"

    for root in (good, broken, _runtime(tmp_path / "never")):
        reading = read_server_build(root)
        assert reading.readable == (reading.stamp is not None)
        assert (reading.problem == "") == reading.readable

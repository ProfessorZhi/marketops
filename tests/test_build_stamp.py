"""构建指纹：回执要能分辨「陈旧的进程」与「最新的进程」。

``marketops.__version__`` 是常量，答不了这个问题 —— 一个 09-22 起的服务与一个刚起的
服务报同一个数字，而它们加载的代码可以差三个提交。

本文件钉的是**诊断本身**：哪一棵树、哪个 revision、干不干净。最要紧的一条不是
「读得出来」，而是「读不出来的时候说的是读不出来」—— 一个把读不出来降级成
``False`` 的实现，会让一份无人可复现的构建在回执里看起来是干净检出。

这一组**不造临时仓库**：被测对象读的就是**运行中那棵树**（``REPO_ROOT``），所以
判据是「指纹与 git 自己对这棵树说的话一致」。造一棵假树只能验一个假的场景，而
真树在盘上，随时可以问。

后面两节钉的是本模块的**第二问**（§15(b)）：一份指纹要穿过一个进程边界（服务把它
写下来、worker 读回来），所以 ``payload()`` 与 ``from_payload()`` 必须互为逆；而
两份指纹比下来只有三种结论，其中 ``dirty`` **参与报告、不参与判定** —— 那一条是
判据里唯一一处「刻意不拒绝」，所以它有自己的测试与理由。
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from marketops.adapters import build_stamp as build_stamp_module
from marketops.adapters.build_stamp import (
    LOADED_BUILD,
    BuildComparison,
    BuildStamp,
    compare_builds,
    read_build_stamp,
)
from marketops.config import PACKAGE_DIR, REPO_ROOT

#: ``payload()`` 的键。**写死在这里**，所以实现多一格或少一格都会红。
PAYLOAD_KEYS = {"package_file", "source_tree", "revision", "dirty", "read_at", "errors"}

#: 一条真实 git 拒绝时的 stderr（本机实测的那一句）。
_NOT_A_REPOSITORY = "fatal: not a git repository (or any of the parent directories): .git"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """在本仓里跑一次 git。**只出现在这个测试文件里**，用来取「答案应该是什么」。

    根用绝对路径、参数里不带任何文件路径：记忆 `git -C 会把相对文件路径拼两遍`
    —— 那种失败的表现形式是「读出来是空」，会让这里的比对变成假绿。
    """
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _fake_run(result: tuple[int | None, str, str], calls: list | None = None):
    """一个不跑 git 的 ``run``。返回码给 ``None`` = 「没起来」，这是两条故障之一。"""

    def run(root: Path, argv: tuple[str, ...]) -> tuple[int | None, str, str]:
        if calls is not None:
            calls.append((root, argv))
        return result

    return run


# ----------------------------------------------------------------------
# 它读的是哪棵树
# ----------------------------------------------------------------------
def test_the_stamp_agrees_with_git_about_the_running_tree() -> None:
    """``revision`` / ``dirty`` 与 git 自己说的**逐字一致**。

    这一条同时是「解析对了没有」的判据：commit id 少一位、``status`` 的换行没
    剥掉、把「有输出」读成「干净」，都会在这里表现出来。
    """
    stamp = read_build_stamp()

    assert stamp.source_tree == str(REPO_ROOT)
    assert stamp.package_file == str(PACKAGE_DIR / "__init__.py")
    assert stamp.errors == {}, f"本机读得出来，不该有理由：{stamp.errors}"

    head = _git("rev-parse", "HEAD").stdout.strip()
    status = _git("status", "--porcelain").stdout.strip()
    assert stamp.revision == head
    assert stamp.dirty is bool(status), f"git 说有 {len(status.splitlines())} 处改动"


def test_the_answer_does_not_depend_on_the_working_directory(tmp_path, monkeypatch) -> None:
    """换一个 cwd，读到的还是那棵树。

    记忆 `cwd 会遮住 .pth，让 venv 静默解析到错的 revision`：从某个检出目录里跑探针，
    测的是「碰巧站着的那个」revision，而且不报错。所以这里站到别处去读，答案必须不变。

    ``LOADED_BUILD`` 与现场读的那一份**都要**不变：前者是回执里发的那个，后者是
    将来有人再读一遍时走的路径 —— 只有一份对的话，两份在报告里长得一样。
    """
    before = read_build_stamp()
    monkeypatch.chdir(tmp_path)

    after = read_build_stamp()
    assert (after.source_tree, after.revision, after.dirty) == (
        before.source_tree,
        before.revision,
        before.dirty,
    )
    assert (LOADED_BUILD.source_tree, LOADED_BUILD.revision) == (
        before.source_tree,
        before.revision,
    )


def test_reading_is_read_only_and_anchored_to_one_tree(monkeypatch) -> None:
    """读的那一次**只读**，而且「哪棵树」只有一个定位来源。

    这两件事在回执上**看不出来**，所以只能用一次注入口把它们钉住：

    * ``--no-optional-locks``：不带它时 ``status`` 会顺手刷新索引并取索引锁 ——
      一个状态面不该写它正在报告的那棵树，也不该在别人拿着锁的时候去抢。
    * 子进程环境里没有任何 ``GIT_*``：``GIT_DIR`` / ``GIT_WORK_TREE`` 一旦在环境里，
      ``-C`` 就不再是那棵树的唯一定位来源，于是「这份回执说的是哪棵树」取决于启动
      这个服务的人当时站在什么 shell 里。
    """
    calls: list[dict] = []

    def fake_subprocess_run(argv, **kwargs):  # noqa: ANN001 -- 与 subprocess.run 同形
        calls.append({"argv": list(argv), "env": dict(kwargs.get("env") or {})})
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(build_stamp_module.subprocess, "run", fake_subprocess_run)
    read_build_stamp()

    assert [call["argv"] for call in calls] == [
        ["git", "--no-optional-locks", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        ["git", "--no-optional-locks", "-C", str(REPO_ROOT), "status", "--porcelain"],
    ]
    for call in calls:
        assert not [key for key in call["env"] if key.startswith("GIT_")], call["env"]


# ----------------------------------------------------------------------
# 「读不出来」不得变成「干净」
# ----------------------------------------------------------------------
def test_a_tree_git_refuses_is_not_a_clean_tree() -> None:
    """不是仓库时，两格都是 ``None``，而且**各有各的理由**。

    ``None`` 不能是 ``False``：``False`` 是「读了、干净」。把前者降级成后者，一份
    仓外的、无人可复现的构建会在回执里看起来是一次干净检出。
    """
    stamp = read_build_stamp(run=_fake_run((128, "", _NOT_A_REPOSITORY)))

    assert stamp.revision is None
    assert stamp.dirty is None
    assert stamp.payload()["dirty"] is None, "读不出来被写成了 False"
    assert set(stamp.errors) == {"revision", "dirty"}, stamp.errors
    assert "not a git repository" in stamp.errors["revision"]


def test_a_git_that_never_started_says_so_differently() -> None:
    """「git 没起来」与「git 说这棵树不行」不是同一条理由。

    处置不同：前者去看这个进程能不能起 git（PATH、权限），后者去看这棵树。合成一句
    会让运维去查一个没有坏的东西 —— 而两句话在回执里长得一样时，没人分得开。
    """
    refused = read_build_stamp(run=_fake_run((128, "", _NOT_A_REPOSITORY)))
    never = read_build_stamp(run=_fake_run((None, "", "FileNotFoundError: git")))

    assert never.revision is None and never.dirty is None
    assert "没被起来" in never.errors["revision"]
    assert never.errors["revision"] != refused.errors["revision"]


def test_an_empty_stdout_is_not_a_revision() -> None:
    """退 0 但 stdout 是空的：按读不出来算，不当成「没有 commit」之外的任何东西。

    空串会一路通过 ``.strip()``，然后在回执里占住 ``revision`` 那一格 —— 它与
    「没有这一格」在调用方那里长得一样，而这两个 ``None`` 的含义完全不同。
    """
    stamp = read_build_stamp(run=_fake_run((0, "   \n", "")))

    assert stamp.revision is None
    assert "空的 commit id" in stamp.errors["revision"]
    assert stamp.dirty is False, "status 退了 0、没有输出 —— 这是「读了、干净」"


def test_an_empty_status_means_clean_and_a_non_empty_one_does_not() -> None:
    """``dirty`` 的两个取值都要有输入走得到 —— 否则它是一条恒真的健康旗。"""
    clean = read_build_stamp(run=_fake_run((0, "", "")))
    touched = read_build_stamp(run=_fake_run((0, " M marketops/config.py\n", "")))

    assert clean.dirty is False
    assert touched.dirty is True


# ----------------------------------------------------------------------
# 形状
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "stamp",
    [
        pytest.param(LOADED_BUILD, id="运行中那棵树"),
        pytest.param(read_build_stamp(run=_fake_run((128, "", _NOT_A_REPOSITORY))), id="读不出来"),
    ],
)
def test_the_payload_shape_is_fixed_and_the_errors_key_never_disappears(stamp: BuildStamp) -> None:
    """键恒为六个，``errors`` 键**恒在**（读得出来时是空对象）。

    「读得出来」与「没有这一格」在解析方那里必须是两件事：一个会消失的键，会让
    「这个构建读不出 revision」与「这个构建还没有这一格」（老版本的服务面）
    变成同一个判断。
    """
    payload = stamp.payload()
    assert set(payload) == PAYLOAD_KEYS
    assert payload["errors"] == dict(stamp.errors)
    assert (payload["revision"] is None) == ("revision" in stamp.errors)
    assert (payload["dirty"] is None) == ("dirty" in stamp.errors)


def test_the_stamp_is_read_in_exactly_one_place() -> None:
    """``read_build_stamp`` 在 ``marketops/`` 里**只有一个**调用点（``LOADED_BUILD``）。

    两个表面各读一次的话，同一个进程会报出两份指纹 —— 它们在报告里长得一样，而
    只有一份是「加载时」的。懒读的那一份答的是「刚才那棵树是什么样」：在一次
    已经把树改过、又提交过的部署里，它给的正是**新的** revision，而那个方向的错
    是本模块存在的理由。

    这条与 ``test_mcp_control_tools`` 里那条「``_DISPATCH_TOOL`` 与引用者同真同假」
    同一个形状：**一处生产**这件事，靠散文守不住，要有一条能数出来的判据。
    """
    callers = sorted(
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "marketops").rglob("*.py")
        if "__pycache__" not in path.parts and "read_build_stamp(" in path.read_text("utf-8")
    )
    assert callers == ["marketops/adapters/build_stamp.py"], (
        f"构建指纹多了一个生产点：{callers}"
    )


# ----------------------------------------------------------------------
# 一份指纹穿过一个进程边界：payload() 与 from_payload() 互为逆
# ----------------------------------------------------------------------
# 现场是 §15(b)：服务把自己加载的构建写到盘上，worker 读回来再比。写的那一侧与读的
# 那一侧若各写一遍形状，读的一方会按一个**旧的**形状解析一个新写的文件 —— 而它长得
# 像「这个文件是坏的」，不是「有人改了形状」。
def test_from_payload_is_the_inverse_of_payload() -> None:
    """读回来的那一份与写出去的那一份**逐格相等**。

    逐格的比较用 ``payload()`` 而不是 ``==``：``BuildStamp`` 是 frozen dataclass，
    相等是逐字段的，而 ``errors`` 那一格在 ``from_payload`` 里被复制过一次 —— 用
    形状比，验的是「写出去的东西读得回来」，那正是这条判据要的。
    """
    assert BuildStamp.from_payload(LOADED_BUILD.payload()).payload() == LOADED_BUILD.payload()


def test_from_payload_round_trips_a_stamp_whose_two_values_are_unknown() -> None:
    """``None`` 的那两格**原样**回来，不会变成 ``False`` 或空串。

    这是 ``from_payload`` 最容易出的那一种错：JSON 里 ``null`` 落回 Python 是
    ``None``，但只要中间有一处写成 ``payload.get("dirty", False)``，一份「读不出来」
    的构建就会在穿过进程边界之后变成「干净」—— 而边界那头的读者正是拿它去判部署的人。

    用**真读一次**的那一份（而不是手搭一个两格为 ``None`` 的）：手搭的形状是我以为的
    「读不出来」，而 ``read_build_stamp`` 真有这条路径，问它就够了。
    """
    unreadable = read_build_stamp(run=_fake_run((128, "", _NOT_A_REPOSITORY)))
    assert unreadable.revision is None and unreadable.dirty is None, "这条测试的前提没了"

    back = BuildStamp.from_payload(unreadable.payload())

    assert back.revision is None
    assert back.dirty is None
    assert back.errors == dict(unreadable.errors)


def test_a_payload_that_is_missing_keys_names_every_one_of_them() -> None:
    """缺键时把**缺的每一个**都点出来（不是只报第一个）。

    读这句拒绝的人手里有那个坏掉的文件：只报第一个缺的，他要改一轮、再被拒一次。
    ``errors`` 那一格也在这份清单里 —— 它是一个**恒在**的键（读得出来时是空对象），
    不是可选的，所以「老版本的服务面」不会在这一侧被当成「这一格不存在」而放过。
    """
    with pytest.raises(ValueError) as excinfo:
        BuildStamp.from_payload({})

    message = str(excinfo.value)
    assert all(key in message for key in PAYLOAD_KEYS), message

    without_errors = LOADED_BUILD.payload()
    del without_errors["errors"]
    with pytest.raises(ValueError) as partial:
        BuildStamp.from_payload(without_errors)

    assert "errors" in str(partial.value)
    assert "package_file" not in str(partial.value), "已经给了的键不该出现在缺键清单里"


@pytest.mark.parametrize(
    ("cell", "value", "why"),
    [
        pytest.param("dirty", 0, "``0`` 既不是 true 也不是 false：bool 是 int 的子类，"
                                 "反过来的判法会静默放行它", id="dirty 是 0"),
        pytest.param("dirty", "false", "字符串 ``'false'`` 在真值判断里是真的", id="dirty 是字符串"),
        pytest.param("revision", 3, "revision 只允许字符串或 null", id="revision 是数字"),
        pytest.param("package_file", "", "空串不是一个路径 —— 这一格空了，「从哪棵树起的」"
                                        "就没有答案", id="空串"),
        pytest.param("errors", [], "errors 必须是一个对象", id="errors 是数组"),
        pytest.param("errors", {"revision": 3}, "errors 的每一格都要是字符串", id="errors 的值不是字符串"),
    ],
)
def test_a_payload_whose_cell_is_the_wrong_shape_names_that_cell(
    cell: str, value: object, why: str
) -> None:
    """坏掉的那一格要被**点名**（而不是一句「形状不对」）。

    这句拒绝会出现在 worker 的 stderr 与它的 ``run.json`` 里，读它的人手里有那个文件
    —— 点名是那一侧唯一能照着做的事。

    每一格都不是假想的形状：``dirty`` 与 ``revision`` 是**三值**的，中间的 ``null``
    合法、而 ``0`` / ``"false"`` 不合法 —— 后两个落进去之后，比对会拿着一个既不是真
    也不是假的值继续走，而那个值在两份指纹里长得一样。
    """
    payload = dict(LOADED_BUILD.payload())
    payload[cell] = value

    with pytest.raises(ValueError) as excinfo:
        BuildStamp.from_payload(payload)

    assert cell in str(excinfo.value), f"{why}（实际消息：{excinfo.value}）"


# ----------------------------------------------------------------------
# 两份指纹怎么比（§15(b)）
# ----------------------------------------------------------------------
# 判据只有四种情形：换树、换提交、判不了、一致。下面按这四种排。
def _stamp(
    *,
    package_file: str = "/p/__init__.py",
    source_tree: str = "/p",
    revision: str | None = "a" * 40,
    dirty: bool | None = False,
    read_at: str = "2026-09-24T00:00:00+00:00",
    errors: Mapping[str, str] | None = None,
) -> BuildStamp:
    """一份手搭的指纹。**不做成 fixture**：下面每一条都要看清单处改动的那一格，
    而 fixture 会把「这条测试到底在比什么」藏进参数表里。"""
    return BuildStamp(
        package_file=package_file,
        source_tree=source_tree,
        revision=revision,
        dirty=dirty,
        read_at=read_at,
        errors=dict(errors or {}),
    )


def test_two_stamps_of_the_same_tree_and_commit_agree_with_nothing_to_say() -> None:
    """一致时 ``message`` 是**空串**，不是一句「一切都好」。

    这一格会被原样写进 ``run.json``：一句「一致」是一个**主张**，而它旁边那一格
    ``verdict`` 已经说了同一件事。空的取值在这里是「没有什么要说的」，调用方按
    ``agrees`` 判，不按句子判。
    """
    verdict = compare_builds(_stamp(), _stamp(read_at="2026-09-24T01:00:00+00:00"))

    assert verdict.verdict is BuildComparison.AGREES
    assert verdict.agrees
    assert verdict.message == ""


def test_a_different_tree_is_proven_before_anything_else_is_looked_at() -> None:
    """两棵树不同 ⇒ ``DIFFERS``，**哪怕 revision 读不出来**。

    顺序是判据的一部分：树不同这一件事是**证明得了**的（两个不同的目录就是两个不同的
    目录），而 revision 那一格只是更强的一句话。反过来先判 revision，会在「换树 +
    revision 读不出来」时给出一句「判不了」—— 而事实是判得了，而且判得很死。
    """
    verdict = compare_builds(
        _stamp(),
        _stamp(source_tree="/elsewhere", revision=None, errors={"revision": "读不出来"}),
    )

    assert verdict.verdict is BuildComparison.DIFFERS
    assert "两侧加载的不是同一棵树" in verdict.message
    assert "/elsewhere" in verdict.message and "/p" in verdict.message


def test_the_same_tree_at_a_different_commit_differs() -> None:
    """同一棵树、服务停在别的提交上 ⇒ ``DIFFERS``（「起了很久的进程」这一格）。"""
    verdict = compare_builds(_stamp(revision="b" * 40), _stamp(revision="c" * 40))

    assert verdict.verdict is BuildComparison.DIFFERS
    assert "b" * 40 in verdict.message and "c" * 40 in verdict.message


@pytest.mark.parametrize("side", ["worker", "服务"])
def test_a_revision_that_cannot_be_read_on_either_side_is_unprovable(side: str) -> None:
    """某一侧的 ``revision`` 读不出来 ⇒ ``UNPROVABLE``。

    读不出来**不是**「不一致」，也不是「一致」：同一棵树上的两个进程可以是在两个提交
    上分别起的，而这一格读不出来之后就没有第二个依据了。三种结论都要能被调用方看见
    —— 一个把 ``None`` 当成「和对方一样」的实现，会让一次无从判定在报告里变成一次
    核对通过。
    """
    broken = _stamp(revision=None, errors={"revision": "读不出来"})
    worker, server = (broken, _stamp()) if side == "worker" else (_stamp(), broken)

    verdict = compare_builds(worker, server)

    assert verdict.verdict is BuildComparison.UNPROVABLE
    assert side in verdict.message, "要说清楚是哪一侧读不出来"
    assert "读不出来" in verdict.message


@pytest.mark.parametrize("side", ["worker", "服务"])
def test_a_dirty_cell_that_cannot_be_read_does_not_stop_the_dispatch(side: str) -> None:
    """某一侧的 ``dirty`` 读不出来 ⇒ **仍然按 revision 判**（不因为这一格停下）。

    这一条与上面那条是一对，且是刻意的**不**拒绝：``dirty`` 本来就不参与判定
    （理由见 ``compare_builds`` 的 docstring），所以它读不出来时并没有多失去一个依据
    —— ``revision`` 还读得出来，而那个比较正是这里要的。

    它不是假想的形状：``git status`` 比 ``git rev-parse`` 慢得多，也更容易被索引锁或
    超时挡住，所以「revision 读得出来、dirty 读不出来」是这套代码真的会走到的组合。
    这一格仍然进回执（``run.json`` 里那一份 ``payload()``），由人判。
    """
    broken = _stamp(dirty=None, errors={"dirty": "读不出来"})
    worker, server = (broken, _stamp()) if side == "worker" else (_stamp(), broken)

    verdict = compare_builds(worker, server)

    assert verdict.agrees, verdict.message
    assert broken.payload()["dirty"] is None, "读不出来仍要被报出来，只是不参与判定"


def test_an_uncommitted_change_on_both_sides_still_agrees() -> None:
    """两侧都是同一棵树上的未提交改动 ⇒ **一致**（这一条决定了判据能不能用）。

    把 ``dirty`` 提成拒绝的理由会让工作树里跑不出任何一轮研究，而 §15 要求测试部署
    真的跑、§16 要求一轮有界真实队列。这一条与 ``test_a_different_tree_...`` 是同一个
    判据的两个方向：**证明得了的说死，证明不了的只说它证明不了**。
    """
    assert compare_builds(_stamp(dirty=True), _stamp(dirty=True)).agrees
    assert compare_builds(_stamp(dirty=False), _stamp(dirty=True)).agrees, (
        "两侧的 dirty 取值不同也一致 —— 判据里根本没有这一格"
    )


def test_the_verdict_payload_carries_the_same_wording() -> None:
    """``payload()['message']`` 与 ``message`` 是**同一句**（不是第二次拼的）。

    ``payload()`` 进 ``run.json``、``message`` 进 stderr —— 两处若各拼一遍，工单里贴的
    那一句与记录里那一句会是两句不同的话。
    """
    verdict = compare_builds(_stamp(), _stamp(source_tree="/elsewhere"))

    assert verdict.payload() == {
        "verdict": verdict.verdict.value,
        "message": verdict.message,
    }
    assert verdict.verdict.value == "differs"

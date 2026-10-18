"""Phase G5a：外部执行器适配器 —— 两个**退 0 的错法**，以及一条不读退出码的纪律。

## 为什么这个文件里的判据长这样

``marketops/adapters/quay_cli.py`` 里那十条实测（M1–M10）每一条都值一条判据，但
其中两条**特别贵**，因为它们的错法不报错：

* **M7** —— 列表查询的过滤旗标是 ``--label``（单数）。写成建/改任务那个
  ``--labels``（复数）会**退 0 并返回全表**。而调用方用 ``find`` 的结果决定要不要
  ``create``，于是「全表非空」会被读成「已经建过了」—— 对每一个单元都为真，也就是
  一个**恒不派发**的派发器。它会安静地什么也不做。
* **M4 + M5** —— ``task edit`` 是 upsert，而 ``--title`` 就是那把钥匙。迁移时顺手
  带上它，一个被删掉的外部任务会在目标状态上**被复活**，调用方看到一次成功。
  资源凭空出现，报告里一行异常都没有。

两条都不是「实现时小心一点」能守住的，所以判据分两层：

1. **argv 层**（纯函数，不用起进程）：直接断言 ``_find_argv`` / ``_transition_argv``
   产出的列表。M7 与 M4 的错法在这一层就红，且红得能读懂。
2. **子进程层**（``tests/fake_quay_cli.py``，一个真的子进程）：假 CLI **照着实测
   行为**实现那两个陷阱 —— 它真的忽略复数拼法、真的在有 ``--title`` 时 upsert。
   于是「适配器写对了」这件事不是由一条断言说的，是由**对面照着真行为反应**说的。

## 一条正对照，专为「不读退出码」

``nonzero_ok`` 模式下假 CLI 输出一切如实、只把退出码改成 7。适配器必须**照样
接受**（M3：同一个门的同一个裁决，0.11.0 退 0、本构建退 1 —— 那个数字不承载判断）。
任何人给 ``_invoke`` 加一句 ``if rc: raise``，这里就红。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from marketops.adapters.quay_cli import (
    QuayCliDispatcher,
    _create_argv,
    _find_argv,
    _transition_argv,
)
from marketops.domain.errors import ConfigError, DispatchError, ExternalStateError
from marketops.ports.dispatch import ExecutionUnit

FAKE = Path(__file__).with_name("fake_quay_cli.py")
JOB = "0123456789abcdef"
UNIT_ID = "fedcba9876543210"
LABELS = ("marketops-job:0123456789abcdef", "marketops-item:fedcba9876543210")


#: 正文的**哨兵**：本文件只判「适配器有没有把调用方给的这一段原样写上去」，
#: 所以它必须是测试自己的一段字，而不是从 ``application/dispatch.py`` 那里
#: 抄来的模板 —— 抄来的话，「适配器原样转发」与「两边都渲染了同一个模板」
#: 在断言上就无法区分。
BODY = "工作单元 fedcba9876543210 的正文（测试的哨兵）"


def unit(
    *,
    job_id: str = JOB,
    unit_id: str = UNIT_ID,
    title: str = "Devon 第一件",
    body: str = BODY,
) -> ExecutionUnit:
    return ExecutionUnit(unit_id=unit_id, job_id=job_id, title=title, body=body)


def build(root: Path, *, mode: str = "ok", timeout: float = 20.0) -> QuayCliDispatcher:
    return QuayCliDispatcher(
        cli_path=FAKE,
        node_path=Path(sys.executable),
        root=root,
        timeout_seconds=timeout,
        env={"FAKE_QUAY_MODE": mode},
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """一个含 ``.quay/config.yml`` 的工作区根 —— 假 CLI 与真 CLI 都要求它存在（M10）。"""
    store = tmp_path / "store"
    (store / ".quay").mkdir(parents=True)
    (store / ".quay" / "config.yml").write_text("provider: native\n", encoding="utf-8")
    return store


@pytest.fixture
def cli(root: Path) -> QuayCliDispatcher:
    return build(root)


def written(root: Path) -> dict:
    """直接读假 CLI 的那本账 —— 用来断言「什么**没有**被写下」。"""
    path = root / ".quay" / "tasks.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


# ----------------------------------------------------------------------
# 第一层：argv。两个退 0 的错法在这一层就红
# ----------------------------------------------------------------------
def test_list_filters_with_the_singular_flag_repeated(root: Path) -> None:
    """M7：列表用 ``--label``（单数），**一个 label 一次**，且不得出现复数拼法。

    写成 ``--labels`` 不会报错 —— 它会静默忽略过滤、返回全表。那条路径上
    「查到的东西非空」会被读成「已经建过了」。
    """
    argv = _find_argv(LABELS, cli_root=root)

    assert argv.count("--label") == 2
    assert "--labels" not in argv
    assert argv[:2] == ["task", "list"]
    assert argv[argv.index("--label") + 1] == LABELS[0]


def test_create_uses_the_plural_flag_joined_by_commas(root: Path) -> None:
    """建任务是**另一套**拼法（M8）。两处刻意不同，因为对面的两个子命令不同。

    这条与上一条成对存在：只钉一边的话，「把两处统一成一种拼法」这个看起来完全
    合理的重构会静默毁掉其中一边。
    """
    argv = _create_argv(
        "MOPS-01234567-fedcba98", unit(), labels=LABELS, status="ready", cli_root=root
    )

    assert "--labels" in argv
    assert "--label" not in argv
    assert argv[argv.index("--labels") + 1] == ",".join(LABELS)
    assert argv[argv.index("--title") + 1] == "Devon 第一件"


def test_create_writes_the_body_the_caller_gave_verbatim(root: Path) -> None:
    """正文**随单元走**，适配器一个字都不加工。

    这是一个真的缺口收口前的形状：只带 ``--title`` 的 ``create`` 让本仓能在外部
    建出一个任务、却说不出那件任务是什么，而 ``WorkItem`` 的 docstring 早就声明
    「内容由 ``ExecutionRef`` 指向的 Quay 任务携带」。所以 ``--body`` 是从调用方
    一路原样写到对面的 —— 适配器**不**渲染、不截断、不排版：它不知道这一段该说
    什么（那是 ``application/dispatch.py`` 的事），而一个在这里加工的适配器会让
    「本仓写下去的」与「外部读到的」成为两份不同的东西。

    ``--body-file`` 是实测存在的另一个旗标（两者互斥），这里刻意不用：它会要求
    先写一个临时文件，而本机的 ``/tmp`` 是共享的（见 ``records`` 里那一条），
    正文本身也不长。
    """
    argv = _create_argv(
        "MOPS-01234567-fedcba98", unit(), labels=LABELS, status="ready", cli_root=root
    )

    assert argv[argv.index("--body") + 1] == BODY
    assert "--body-file" not in argv


def test_transition_carries_neither_title_nor_labels(root: Path) -> None:
    """M4 + M6：迁移的 argv 里**没有** ``--title``，也**没有** ``--labels``。

    ``--title`` 是那把把一次迁移变成一次创建的钥匙：本地映射指向的外部任务被删掉
    之后，带上它的迁移会**静默把任务复活**在目标状态上，而调用方看到的是成功。

    ``--labels`` 带上去则是**整体替换**（对面文档逐字：``replaces existing
    labels``）—— 每次状态变化顺手重写关联，等于每一次都在赌自己记得全那一组。
    """
    argv = _transition_argv(
        "MOPS-01234567-fedcba98", expected="todo", status="ready", cli_root=root
    )

    assert argv[:3] == ["task", "edit", "MOPS-01234567-fedcba98"]
    assert argv[argv.index("--expect-status") + 1] == "todo"
    assert argv[argv.index("--status") + 1] == "ready"
    assert "--title" not in argv
    assert "--labels" not in argv
    assert "--label" not in argv


# ----------------------------------------------------------------------
# 第二层：真的起子进程
# ----------------------------------------------------------------------
def test_find_on_an_empty_workspace_is_zero_rows_not_an_error(cli: QuayCliDispatcher) -> None:
    """**这是整个端口最重要的一条**：查到零条是一个答案，不是一次失败。

    调用方用 ``find`` 的结果决定要不要 ``create``。把「查询没跑成」也报成 ``()``，
    就会让每一次重试都新建一个任务 —— 而且每一次都伴随着一次成功的 ``create``。
    """
    assert cli.find(labels=LABELS) == ()


def test_find_is_an_and_join_over_labels(cli: QuayCliDispatcher, root: Path) -> None:
    """M7 在**子进程**这一层的执行器。

    假 CLI 照着真行为忽略复数拼法。所以适配器若把两种拼法搞反，这里的 ``find``
    会返回**两个**任务而不是一个 —— 判据以「比该查到的多」的样子红，而不是以
    一句报错的样子红。
    """
    cli.create(unit(), labels=(LABELS[0], LABELS[1]), status="todo")
    cli.create(
        unit(unit_id="00000000000000aa"),
        labels=(LABELS[0], "marketops-item:00000000000000aa"),
        status="todo",
    )

    assert len(cli.find(labels=(LABELS[0],))) == 2
    assert len(cli.find(labels=LABELS)) == 1
    assert cli.find(labels=(LABELS[0], "marketops-item:00000000000000ff")) == ()


def test_create_reads_back_the_labels_it_asked_for(cli: QuayCliDispatcher) -> None:
    """``create`` 的返回值是**对面报回来的样子**，不是一句「我建好了」。

    label 是这条边界上唯一的关联，而写不上去的 label 会让那个任务从 ``find``
    那边看等于不存在。
    """
    task = cli.create(unit(), labels=LABELS, status="todo")

    assert task.external_id == "MOPS-01234567-fedcba98"
    assert set(LABELS) <= set(task.labels)
    assert task.status == "todo"


def test_creating_the_same_unit_twice_leaves_one_task(cli: QuayCliDispatcher) -> None:
    """幂等：两个并发的派发者算出同一个 id，第二个是原地对齐而不是新建。"""
    first = cli.create(unit(), labels=LABELS, status="todo")
    second = cli.create(unit(), labels=LABELS, status="todo")

    assert first.external_id == second.external_id
    assert len(cli.find(labels=LABELS)) == 1


def test_create_is_an_upsert_so_it_cannot_test_existence(
    cli: QuayCliDispatcher, root: Path
) -> None:
    """M5：对一个已存在的 id ``create`` 会**退 0 并改掉标题**。

    这条判据钉的是一个**否定的事实**：``create`` 不能当存在性判据用。它不报错，
    所以「试着建一下，建成了就是新的」这句话是错的 —— 它对每一个 id 都成立。
    调用方要先 ``find``、再 ``create``（那个顺序由 G5b 负责）。
    """
    cli.create(unit(title="第一版标题"), labels=LABELS, status="todo")
    again = cli.create(unit(title="第二版标题"), labels=LABELS, status="todo")

    assert again.external_id == "MOPS-01234567-fedcba98"
    assert len(written(root)) == 1
    assert cli.find(labels=LABELS)[0].external_id == again.external_id


def test_a_create_that_silently_drops_the_labels_is_refused(root: Path) -> None:
    """**回读是判据，不是保险。**

    ``drop_labels`` 模式下对面一切正常回报、退 0、任务也确实写下去了 —— 只有
    ``--labels`` 没落盘。没有回读这一条就完全看不出来：本地会记下一条指向一个
    **查不回来**的任务的映射，而那个任务从 ``find`` 那边看等于不存在。
    """
    cli = build(root, mode="drop_labels")

    with pytest.raises(DispatchError) as refused:
        cli.create(unit(), labels=LABELS, status="todo")

    assert "查回来的是 0 条" in str(refused.value)
    # 而任务**确实**存在 —— 所以这个失败不能被「它没建成」解释掉。
    assert "MOPS-01234567-fedcba98" in written(root)


def test_transition_moves_the_status_and_keeps_the_labels(cli: QuayCliDispatcher) -> None:
    """正对照：合规的迁移真的会改状态，**且不会碰 label**（M6）。

    没有这一条，上面「不带 ``--labels``」那条判据可能只是「什么都没发生」。
    """
    task = cli.create(unit(), labels=LABELS, status="todo")

    moved = cli.transition(task.external_id, expected="todo", status="ready")

    assert moved.status == "ready"
    assert set(LABELS) <= set(moved.labels)
    assert cli.find(labels=LABELS)[0].status == "ready"


def test_a_stale_transition_is_an_external_state_error(cli: QuayCliDispatcher) -> None:
    """M2：CAS 冲突 ⇒ ``ExternalStateError``，且两条状态都进消息。

    对面退 1 而 stdout 是**空的** —— 所以这条路径只能从 stderr 读。它必须与
    「任务不存在」分开命名（两种处置不同），也必须与 ``DispatchError`` 分开
    （那是基础设施故障）。
    """
    task = cli.create(unit(), labels=LABELS, status="todo")
    cli.transition(task.external_id, expected="todo", status="ready")

    with pytest.raises(ExternalStateError) as refused:
        cli.transition(task.external_id, expected="todo", status="done")

    message = str(refused.value)
    assert "'todo'" in message
    assert "'ready'" in message


def test_a_transition_never_resurrects_a_missing_task(
    cli: QuayCliDispatcher, root: Path
) -> None:
    """M4 + M11：对着一个**不存在**的外部任务迁移 ⇒ 拒绝，且它**没有**被建出来。

    **两半都要断言。**「没被建出来」那一半是事实；而「消息说得出是哪一种」那一半
    才是适配器不带 ``--title`` 的**真正理由** —— 实测（M11）带 ``--title`` 时对面
    把「任务不存在」报成一次 CAS 冲突、并把读到的状态写成 ``null``。两边的任务都
    没被建出来，所以只断言第一半的话，这条判据对 ``--title`` 一个字都说不出
    （变异测试当场证明了：第一版只有第一半，把 ``--title`` 注入进去它照绿）。
    """
    with pytest.raises(ExternalStateError) as refused:
        cli.transition("MOPS-01234567-fedcba98", expected="todo", status="ready")

    assert "没有这个任务" in str(refused.value)
    assert "null" not in str(refused.value)
    assert "MOPS-01234567-fedcba98" not in written(root)


def test_the_two_missing_task_messages_are_what_they_are(root: Path) -> None:
    """M4 与 M11 的**正对照**：同一个「任务不存在」在两种 argv 下是两句不同的话。

    这条不测适配器 —— 它测**替身本身**。上面那条判据的价值完全取决于替身是否真的
    照着两种措辞反应；没有这一条，替身哪天被简化成一种措辞，上面那条就会静静地
    变成一条只测「没被建出来」的判据。
    """
    without_title = _transition_argv("MOPS-x", expected="todo", status="ready", cli_root=root)
    # 插在 ``--json`` 之前，而不是随便找个位置塞进去 —— 塞在 ``--status`` 与它的值
    # 之间会让 ``--status`` 变成一个没有值的旗标，那样这一发测的就不是 ``--title``
    # 而是「参数写坏了」，而它照样会退 1（一个因为错误原因而红的对照）。
    at = without_title.index("--json")
    with_title = [*without_title[:at], "--title", "MUTANT", *without_title[at:]]

    def stderr_of(argv: list[str]) -> str:
        done = subprocess.run(
            [sys.executable, str(FAKE), *argv],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "FAKE_QUAY_MODE": "ok"},
        )
        assert done.returncode == 1
        return done.stderr

    assert "does not exist yet" in stderr_of(without_title)
    assert "CAS conflict" in stderr_of(with_title)
    assert '"null"' in stderr_of(with_title)


def test_a_query_that_did_not_run_is_not_zero_rows(root: Path) -> None:
    """M10：工作区根不对 ⇒ **抛** ``DispatchError``，**不是**返回 ``()``。

    这一条与 ``test_find_on_an_empty_workspace_is_zero_rows_not_an_error`` 是同一个
    不变量的两半，而这一半才是贵的那个：把「查询没跑成」报成零条，会让调用方
    去建一个已经存在的任务，而且每一次都伴随着一次成功的 ``create``。
    """
    empty = root.parent / "not-a-workspace"
    empty.mkdir()
    cli = build(empty)

    with pytest.raises(DispatchError) as refused:
        cli.find(labels=LABELS)

    assert "config.yml" in str(refused.value)


# ----------------------------------------------------------------------
# 不读退出码 —— 一条正对照
# ----------------------------------------------------------------------
def test_a_payload_is_accepted_even_when_the_exit_code_is_not_zero(root: Path) -> None:
    """**正对照**：退出码 7，输出一切如实 —— 必须被接受。

    M3 是这条判据的理由：同一个门失败，0.11.0 退 **0**、本构建退 **1**。那个数字
    连跨构建都不稳定，所以它不能承载判断。任何人给 ``_invoke`` 加一句
    ``if rc: raise``，这里就红 —— 而那句 ``if`` 是几乎不可能不写的直觉。
    """
    cli = build(root, mode="nonzero_ok")

    task = cli.create(unit(), labels=LABELS, status="todo")

    assert task.external_id == "MOPS-01234567-fedcba98"
    assert len(cli.find(labels=LABELS)) == 1


def test_a_silent_success_is_refused(root: Path) -> None:
    """退 0、什么都没有 ⇒ ``DispatchError``。

    这是退出码那一枚硬币的另一面：既然非零不代表失败，零也就不代表成功。判断
    只有一次 —— stdout 上有没有一份可读的 payload。
    """
    cli = build(root, mode="silent")

    with pytest.raises(DispatchError) as refused:
        cli.find(labels=LABELS)

    assert "可读的答案" in str(refused.value)


def test_unreadable_output_is_an_infrastructure_failure(root: Path) -> None:
    """输出读不懂 ⇒ ``DispatchError``（基础设施），**不是** ``ExternalStateError``。

    两者的处置相反：前者去查工具链，后者去重读状态。把读不懂的输出按后者处置
    会让调用方永远在读一个它读不懂的东西。
    """
    cli = build(root, mode="garbage")

    with pytest.raises(DispatchError):
        cli.find(labels=LABELS)


def test_a_timeout_is_dispatch_error_and_says_it_heard_nothing(root: Path) -> None:
    """超时 ≠ 对面拒绝。说成拒绝会让调用方去重读状态，而真相是我们没听到答案。"""
    cli = build(root, mode="slow", timeout=0.5)

    with pytest.raises(DispatchError) as refused:
        cli.find(labels=LABELS)

    assert "没有返回" in str(refused.value)


# ----------------------------------------------------------------------
# 形状与拒绝
# ----------------------------------------------------------------------
def test_an_empty_label_set_is_refused(cli: QuayCliDispatcher) -> None:
    """空的那一组不是「全都要」，是「不加过滤」—— 在外部那张混着别人工作的表上，
    它是一次越界读。"""
    with pytest.raises(ValueError):
        cli.find(labels=())


@pytest.mark.parametrize("label", ["a,b", " a", "a ", "", "a\nb"])
def test_a_label_that_cannot_be_written_and_read_back_is_refused(
    cli: QuayCliDispatcher, label: str
) -> None:
    """M8：含逗号的 label 会被对面**切成两个**（静默），首尾空白的写进去就查不回来。

    这两个都是「需要规范化的输入」，而本仓库对它的处置一律是**拒绝** —— 一个会替
    调用方修剪的实现更友好，但它把一次错误变成两个不同的 label 静默合并。
    """
    with pytest.raises(ValueError):
        cli.find(labels=(label,))


def test_create_refuses_an_empty_status_or_title(cli: QuayCliDispatcher) -> None:
    with pytest.raises(ValueError):
        cli.create(unit(), labels=LABELS, status="")
    with pytest.raises(ValueError):
        cli.create(unit(title=""), labels=LABELS, status="todo")


def test_create_refuses_an_empty_body(cli: QuayCliDispatcher) -> None:
    """没有正文的条目在外部那张表上是一行「不知道要干什么」的东西。

    与空标题同一条取向、不同一条理由：标题是给人**扫**的，正文是给人**读**的。
    两者都允许为空的话，那一行就只剩 id 与 label 了 —— 而 label 是写给查询的，
    不是写给人看的。

    **拒在本地**：对面收一个空 ``--body`` 不会报错（实测它照建），所以放它过去
    的代价是「一次成功的派发」加上一行没人能读懂的看板条目。
    """
    with pytest.raises(ValueError):
        cli.create(unit(body=""), labels=LABELS, status="todo")


def test_the_constructor_fails_closed_on_a_bad_path(tmp_path: Path, root: Path) -> None:
    """三样缺一不可，且在**构造时**抛。

    一个「建得起来、派发时才炸」的执行器在部署自检里是完全隐形的 —— 而它炸的
    那一刻恰好是一次真的派发。
    """
    with pytest.raises(ConfigError):
        QuayCliDispatcher(cli_path=tmp_path / "nope", node_path=Path(sys.executable), root=root)
    with pytest.raises(ConfigError):
        QuayCliDispatcher(cli_path=Path("relative/quay.js"), node_path=Path(sys.executable), root=root)
    with pytest.raises(ConfigError):
        QuayCliDispatcher(
            cli_path=FAKE, node_path=Path(sys.executable), root=root / "not-there"
        )


def test_the_child_does_not_inherit_the_parent_environment(cli: QuayCliDispatcher) -> None:
    """子进程的环境是**构造出来的**，不是继承来的。

    继承会让这条边界的行为取决于调用方的 shell（「同一份代码在两台机器上跑出两种
    结果」），何况继承会把父进程手里的凭据一并无偿交给一个子进程。
    """
    assert set(cli._env) == {"PATH", "HOME", "LANG", "LC_ALL", "FAKE_QUAY_MODE"}
    assert cli._env["PATH"].startswith(str(Path(sys.executable).parent))


# ----------------------------------------------------------------------
# 第三层：**入站**的 argv —— Quay 的 driver 交给 launcher 的那一份
# ----------------------------------------------------------------------
# 上面那两层的方向是「本仓 → 外部」；这一层的方向相反，钉的是 M12：Quay 的
# ``launchArgv`` 拼出来的 argv，以及它的 worker prompt 里**唯一**那句能被读成
# 任务身份的话。两条都是实测形状（0.11.0 的
# ``scripts/dist/worker-driver.js``，``buildWorkerPrompt`` 与 ``buildContinueWorkerPrompt``
# 各有一句一模一样的 ``Task: ${task}. Repo root: ${root}.``）。
#
# 为什么这一层值得单独钉：研究 worker 拿到的**唯一**任务身份就是这句 prompt。
# 读宽一点（比如只找 ``Task:`` 不看后半句）会在 Quay 换一句话的那天**静默读错**
# —— 读出一个像 id 的字符串，然后拿它去查一个不存在的工作单元。读严一点，那天
# 得到的是一次明确的拒绝。所以判据钉的是**完整的那一句**。

from marketops.adapters.quay_cli import (  # noqa: E402
    TASKS_DIRNAME,
    WorkerPromptTarget,
    read_task_labels,
    split_worker_argv,
    worker_mcp_servers,
    worker_prompt_target,
)

#: 0.11.0 的 ``buildWorkerPrompt`` 前两句，逐字。后半段（实现链）与我们的判据无关，
#: 但留着 —— 它是一个**真的**会出现在 ``-p`` 里的串，而判据要用真串。
DRIVER_PROMPT = (
    "You are a per-task worker in the quay repo (SPEC-worker-driven-inner §5 阶段 2). "
    "Task: MOPS-1b1b7f82-6d783321. Repo root: /home/operator/artifacts/marketops-mvp-quay. "
    "Run the implementation chain: (1) create an isolated git worktree for "
    "MOPS-1b1b7f82-6d783321, then immediately provision it by running "
    "`bash kernel/scripts/dispatch-worktree-setup.sh <the worktree path you created in step 1>`"
)


def _launch_argv(prompt: str = DRIVER_PROMPT) -> list[str]:
    """``launchArgv`` 在 ``task-worker`` 这个 role 上拼出来的那一串，逐项照抄。

    取值来自实测：``--settings`` 是 ``pickSettingsFile`` 选中的那个文件，
    ``--strict-mcp-config --mcp-config <json>`` 只在 ``mcpBlacklist`` 非空时出现
    （``mcpConfigArgvSuffix``），``--model`` / ``--bare`` 在 profile 没配时**不出现**。
    ``--session-id`` 由 ``runOneWorker`` 追加（``const argv = injectSessionId ?
    [...workerArgv, "--session-id", sessionId] : workerArgv;``），所以在 ``-p`` **之后**
    —— 转发的就包含它。``--worker-cmd-exact`` 时才是唯一不追加的路径，而那条路在生产
    里够不着（只有测试会传它）。

    ``--mcp-config`` 的值是**逐字**实测的那一段（本机 MCP 服务的地址 + 一个从环境展开
    的令牌占位符）。占位符是 ``.mcp.json`` 里写着的那五个字符，不是任何一份凭据 ——
    凭据只在 vault 里，而这里要的是**形状**：driver 传内联 JSON、里面的 ``${VAR}``
    由子 claude 自己展开（实测：``--mcp-config`` 的 JSON 里 ``${VAR}`` 会被展开）。
    """
    return [
        "--settings",
        "/home/operator/artifacts/marketops-mvp-quay/.claude/launch.settings.json",
        "--exclude-dynamic-system-prompt-sections",
        "--prompt-suggestions",
        "false",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{"marketops":{"type":"http","url":"http://127.0.0.1:8787/mcp",'
        '"headers":{"Authorization":"Bearer ${MARKETOPS_MCP_TOKEN}"}}}}',
        "-n",
        "marketops-mvp-task-worker",
        "-p",
        prompt,
        "--session-id",
        "6f1c0f7a-1a1e-4a5f-9b0c-2f6a4d1e7b30",
    ]


def test_the_prompt_is_lifted_out_and_everything_else_is_forwarded(
) -> None:
    """``-p`` 与它的值被取走，**其余每一项原样、原序**转发给 delegate。

    转发而不是重建：``--settings`` / ``--mcp-config`` / ``--session-id`` 是**工作区**
    给这个 worker 的配置（谁装哪个 MCP、用哪份 settings 由部署决定）。在这一层重新
    拼一遍，就是同一份配置的第二个实现 —— 而漂移的那一次会让 worker 拿到的 MCP 面
    与工作区声明的不是同一个，两边都看不出异常。
    """
    forwarded, prompt = split_worker_argv(_launch_argv())

    assert prompt == DRIVER_PROMPT
    assert "-p" not in forwarded
    assert DRIVER_PROMPT not in forwarded
    at = _launch_argv().index("-p")
    assert list(forwarded) == _launch_argv()[:at] + _launch_argv()[at + 2 :]


def test_the_forwarded_argv_keeps_quays_own_configuration_flags() -> None:
    """具体点名四样：settings、strict-mcp-config、mcp-config、session-id。

    上面那条是「集合相等」，这条是「那几样**真的**在里面」—— 集合相等在列表同时
    丢掉两项时仍然成立。
    """
    forwarded, _ = split_worker_argv(_launch_argv())

    assert "--settings" in forwarded
    assert "--strict-mcp-config" in forwarded
    assert "--mcp-config" in forwarded
    assert "--session-id" in forwarded


# ----------------------------------------------------------------------
# 同一份 argv 的另一件事：它给子 claude 开出多大的工具面
# ----------------------------------------------------------------------
# 上面几条钉的是任务身份；这几条钉的是**权限**。两者共用同一条 argv 是这条边界的
# 性质，不是巧合：那一串既是「你去干哪件活」，也是「你能用哪些工具」。后者读不出来
# 的时候，读出来的东西必须是拒绝 —— 一个读成空集的实现会让「一个 server 都没配」
# 与「名单拼不出来」在下游长得一模一样，而在下游这两件事的处置完全相反。
#
# 判据钉在**两个旗标**上而不是 ``.quay/profiles.yml`` 上，因为那份声明与最终 argv
# 之间那段代码是**失败开放**的（见 ``worker_mcp_servers`` 的 docstring）：名单为空
# 时 driver 把 ``--strict-mcp-config`` 与 ``--mcp-config`` 一起省掉，于是「没配」与
# 「子进程能看见宿主上的一切」在 argv 上是同一个形状。


def _argv_with_config(value: str, *, strict: bool = True) -> list[str]:
    """实测 argv，只把 ``--mcp-config`` 的值换成 ``value``（并可去掉 strict 旗标）。"""
    argv = _launch_argv()
    at = argv.index("--mcp-config")
    argv[at + 1] = value
    return argv if strict else [a for a in argv if a != "--strict-mcp-config"]


def test_the_measured_argv_yields_exactly_the_marketops_server() -> None:
    """正对照：**已知输入**（实测逐字的那一串）→ 恰好 ``("marketops",)``。

    这条是其余几条的基准。没有它，「拒绝」那几条可以靠一个恒拒绝的实现全绿。
    """
    forwarded, _ = split_worker_argv(_launch_argv())

    assert worker_mcp_servers(forwarded) == ("marketops",)


def test_the_servers_come_out_of_the_json_not_out_of_the_flag_order() -> None:
    """读的是 ``mcpServers`` 的**键集**，不是出现过哪个名字的文本。

    这里放两个 server 并把顺序倒过来：结果按名字排序，且两个都在。排序是因为
    「恰好是哪几个」不依赖拼那份 JSON 的那一侧的键序。
    """
    forwarded, _ = split_worker_argv(
        _argv_with_config('{"mcpServers":{"quay":{"type":"http"},"marketops":{"type":"http"}}}')
    )

    assert worker_mcp_servers(forwarded) == ("marketops", "quay")


def test_a_config_with_no_servers_key_is_an_empty_tool_surface() -> None:
    """``mcpServers`` 键不在 ⇒ 空元组，**不是**拒绝：那是一句真话（一个都没配）。

    它与下面那几种的区别是「有没有答案」。空集在下游会被判据拒掉（那里要求恰好
    等于 ``("marketops",)``），所以这条真话不会变成一次放行。
    """
    forwarded, _ = split_worker_argv(_argv_with_config("{}"))

    assert worker_mcp_servers(forwarded) == ()


def test_an_argv_without_the_strict_flag_is_refused() -> None:
    """少了 ``--strict-mcp-config`` ⇒ 拒绝回答，而不是「读出来是空的」。

    缺它的含义不是「没有 MCP」，是「这份清单会与宿主上其余来源**合并**」—— 那个
    集合不在这条 argv 里，所以这个函数对那个问题没有答案。这正是 driver 的失败开放
    那条路（``blacklist.length === 0`` ⇒ 两个旗标一起消失）的形状，所以这条判据是
    那道 fail-open 在**本仓**这一侧的对应物：对面失败开放的那天，这里拒绝派发。
    """
    forwarded, _ = split_worker_argv(_argv_with_config("{}", strict=False))

    with pytest.raises(ValueError, match=r"--strict-mcp-config"):
        worker_mcp_servers(forwarded)


def test_strict_without_a_config_is_refused() -> None:
    """只有 strict、没有清单 ⇒ 拒绝：严格模式配一份不存在的清单 = 什么都看不到。"""
    argv = [a for a in _launch_argv() if a != "--mcp-config"]
    argv = [a for a in argv if not a.startswith('{"mcpServers"')]

    with pytest.raises(ValueError, match=r"--mcp-config"):
        worker_mcp_servers(argv)


def test_two_configs_are_refused_rather_than_resolved() -> None:
    """两处 ``--mcp-config`` ⇒ 拒绝。挑一处就是猜，而两份清单是两个工具面。"""
    argv = _launch_argv() + ["--mcp-config", "{}"]

    with pytest.raises(ValueError, match="两"):
        worker_mcp_servers(argv)


def test_a_config_flag_with_no_value_is_refused() -> None:
    """``--mcp-config`` 在末尾 ⇒ 拒绝，不是当成空清单。"""
    argv = [a for a in _launch_argv() if not a.startswith('{"mcpServers"')]

    with pytest.raises(ValueError, match=r"--mcp-config"):
        worker_mcp_servers(argv)


def test_a_config_value_that_is_not_json_is_refused() -> None:
    """值是**路径**（而不是内联 JSON）⇒ 拒绝：那说明这条 argv 换了形状。

    ``--mcp-config`` 在别的 claude 版本上收的是文件路径。认下来就会去猜那个文件里
    写了什么 —— 而这不是这条边界能做的事（它读不到，也不该去读）。
    """
    forwarded, _ = split_worker_argv(_argv_with_config("/ws/.mcp.json"))

    with pytest.raises(ValueError, match="JSON"):
        worker_mcp_servers(forwarded)


@pytest.mark.parametrize("value", ['{"mcpServers":3}', '[]', '"marketops"'])
def test_a_json_value_of_the_wrong_shape_is_refused(value: str) -> None:
    """JSON 合法但形状不对 ⇒ 拒绝。三种：``mcpServers`` 不是对象、顶层不是对象、
    顶层是字符串。**读成空集**这三种都会变成一次静默的「没有工具面」。"""
    forwarded, _ = split_worker_argv(_argv_with_config(value))

    with pytest.raises(ValueError):
        worker_mcp_servers(forwarded)


def test_an_argv_without_the_prompt_flag_is_refused() -> None:
    """没有 ``-p`` ⇒ 拒绝，且消息里点名是哪一个旗标。

    回落到「取最后一个参数」或「读环境变量」都会**静默**挑一个不是任务身份的东西，
    而那时 worker 会去领一件不属于这条任务的工作。
    """
    argv = [a for a in _launch_argv() if a != "-p" and a != DRIVER_PROMPT]

    with pytest.raises(ValueError, match=r"-p"):
        split_worker_argv(argv)


def test_two_prompt_flags_are_refused_rather_than_resolved() -> None:
    """两次 ``-p`` ⇒ 拒绝。挑一个（前一个还是后一个）都是**猜**。"""
    argv = _launch_argv() + ["-p", "Task: OTHER-1. Repo root: /tmp."]

    with pytest.raises(ValueError, match="两"):
        split_worker_argv(argv)


def test_a_prompt_flag_with_no_value_is_refused() -> None:
    """``-p`` 在末尾、后面什么都没有 ⇒ 拒绝，不是当成空串。"""
    argv = _launch_argv()[:-2] + ["-p"]

    with pytest.raises(ValueError, match=r"-p"):
        split_worker_argv(argv)


def test_the_task_and_the_workspace_root_come_out_of_the_same_sentence() -> None:
    """任务身份与工作区根出自**同一句**，一起拿出来。

    两者分开解析会让「这条 prompt 说的是另一个工作区的任务」变成一件看不出来的事：
    id 读出来了、根也读出来了，而它们不是一对。
    """
    target = worker_prompt_target(DRIVER_PROMPT)

    assert target == WorkerPromptTarget(
        task_id="MOPS-1b1b7f82-6d783321",
        repo_root="/home/operator/artifacts/marketops-mvp-quay",
    )


def test_the_task_id_stops_before_the_full_stop() -> None:
    """id 到句号为止 —— 这正是「读严一点」要挡住的那种读法。

    一个宽松的读法（读到空白为止，于是把结尾那个句号一起吞掉）会得到一个
    ``…6d783321.``。它会一路通过（字符串非空、看着像 id），然后在对面查不到 ——
    于是一次「prompt 换了形状」被报成「任务不见了」。
    """
    target = worker_prompt_target("Task: MOPS-abc. Repo root: /work/w.")

    assert target.task_id == "MOPS-abc"
    assert target.repo_root == "/work/w"


def test_only_one_full_stop_is_stripped_from_the_workspace_root() -> None:
    """剥**一个**句号就停，其余还给路径。

    句号是这句 prompt 的句号，但目录名以句号结尾是合法的。于是「根恰好以句号结尾」
    在 prompt 里长成两个句号，而 ``rstrip(".")`` —— 看起来只是把上一个判据写得更
    通用一点 —— 会把目录名改掉（``/work/w.`` 变成 ``/work/w``）。改的是**别人工作区的
    路径**，所以症状是 worker 在另一棵树上干活，两边各自的日志都正常。

    这条判据存在之前，注释里「只剥一个」这句话没有任何执行器：把它改成 ``rstrip``
    整个套件照样全绿。
    """
    target = worker_prompt_target("Task: MOPS-abc. Repo root: /work/w..")

    assert target.repo_root == "/work/w."


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "You are a per-task worker. Task: MOPS-abc.",
        "Task MOPS-abc. Repo root: /work/w.",
        "Task: MOPS-abc. Repo root:",
        "Task: . Repo root: /work/w.",
        "Task: MOPS-abc. Repo root: /work/w. Task: MOPS-def. Repo root: /work/w.",
    ],
)
def test_a_prompt_that_does_not_carry_the_measured_sentence_once_is_refused(
    prompt: str,
) -> None:
    """六种「像是有、其实不是」的 prompt **全都**拒绝。

    * 少半句（没有 ``Repo root:``）—— 那一句换了形状，而我们读的是它的形状；
    * 少 id（``Task: . Repo root: …``）—— 一个宽松到允许空 id 的读法会把它读成
      空串或句号，然后**继续往下走**；
    * **两句**（最后一条）—— 那时任何「第一句还是第二句」的选择都是猜，而两条
      工作单元是两件不同的工作。
    """
    with pytest.raises(ValueError):
        worker_prompt_target(prompt)


def test_a_value_that_looks_like_the_flag_makes_it_refuse_not_guess() -> None:
    """某个**别的**参数恰好取值 ``-p`` ⇒ 两处不可区分 ⇒ 拒绝，不挑一处。

    这条钉的不是「实现够聪明」，是**它承认自己看不出来**。argv 本身分不出旗标与
    恰好取了同一个值的参数（那需要知道前面每个旗标的元数），所以剩下的选择只有两个：
    挑一处，或者拒绝。挑错了的表现是 worker 去领一件不属于这条任务的工作，而
    两次调用各自的日志都正常 —— 所以这里要的是拒绝。

    真的有人往这条路线上加一个取值可能是 ``-p`` 的旗标时，红的是这条判据，而不是
    某个凌晨的静默误派发。
    """
    argv = ["--settings", "-p", "-n", "w", "-p", DRIVER_PROMPT]

    with pytest.raises(ValueError, match="两处"):
        split_worker_argv(argv)


# ----------------------------------------------------------------------
# 入站的任务文件：driver 落盘的那一份（M12 的另一半）
# ----------------------------------------------------------------------
# argv 那一半只给出**任务 id**；工作单元 id 只在**文件**的 label 里。两种读法
# （问一次 CLI / 读工作区里这个文件）选后者的理由写在 ``read_task_labels`` 的上一节
# 注释里，一句话是：这台机器上至少三代 Quay，从 ``PATH`` 摸到的那个读的可能是
# 另一个 store —— 症状是任务读得到、label 却是别人的。
#
# 这里钉的是那条读法的**代价**：对 provider 落盘格式有依赖，所以形状一不对就拒绝。

TASK_ID = "MOPS-1b1b7f82-6d783321"
WORK_ITEM_ID = "6d783321a9df9b2f2c6bf4ace0cd9b26"

#: 文件里写的那两个 label。取值是实测的（``marketops-mvp-quay/tasks/`` 那几条），
#: 刻意用**另一个**工作单元 id：这一层读的是文件，不是任何一条会话状态。
LABELS_IN_FILE = (f"marketops-job:{JOB}", f"marketops-item:{WORK_ITEM_ID}")

#: 五种「文件在、形状不对」。每一种的正确处置都是拒绝，而**不是**读出一组空的 label。
MALFORMED_TASKS = {
    "没有前言块": f"id: {TASK_ID}\nstatus: ready\n",
    "前言块没结束": f"---\nid: {TASK_ID}\nlabels:\n  - marketops-item:{WORK_ITEM_ID}\n",
    "前言不是合法 YAML": f"---\nid: {TASK_ID}\nlabels: [unclosed\n---\n",
    "labels 不是一个列表": f"---\nid: {TASK_ID}\nlabels: marketops-item:{WORK_ITEM_ID}\n---\n",
    "labels 里有一个不是字符串": (
        f"---\nid: {TASK_ID}\nlabels:\n  - marketops-item:{WORK_ITEM_ID}\n  - [nested]\n---\n"
    ),
}


def _task_file(root: Path, body: str, task_id: str = TASK_ID) -> Path:
    directory = root / TASKS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{task_id}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _well_formed(task_id: str = TASK_ID) -> str:
    """实测形状的前言块，逐字（0.11.0 的 ``marketops-mvp-quay/tasks/*.md``）。"""
    return (
        f'---\nid: {task_id}\ntitle: "one-subject"\nstatus: ready\nlabels:\n'
        f"  - {LABELS_IN_FILE[0]}\n  - {LABELS_IN_FILE[1]}\n"
        "parent: null\nchildren: []\nextra: {}\n---\n"
    )


def test_the_reader_hands_back_the_labels_verbatim(tmp_path: Path) -> None:
    """读进来的就是文件里写的那两个字符串：不挑、不 strip、不去重。

    「哪一条才是工作单元」是 ``application.dispatch.work_item_id_from_labels`` 的事。
    这一层要是顺手替它挑一条，读者就再也分不出「文件里只有作业 label」与「文件里
    那一条被这一层吃掉了」。
    """
    _task_file(tmp_path, _well_formed())

    assert read_task_labels(tmp_path, TASK_ID) == LABELS_IN_FILE


def test_a_task_file_that_is_not_there_is_refused(tmp_path: Path) -> None:
    """文件不在 ⇒ 抛 ``ConfigError``，不是空元组。

    两者在下游都会变成一次拒绝，但这里那句话说的是「driver 派的任务不在这里」
    （指向工作区），而空的一组的说法是「这条任务不是我们派的」（指向派发方）——
    同一件坏事、两个方向完全不同的排查。
    """
    with pytest.raises(ConfigError):
        read_task_labels(tmp_path, TASK_ID)


@pytest.mark.parametrize("case", sorted(MALFORMED_TASKS))
def test_a_task_file_whose_shape_is_off_is_refused_not_read_as_empty(
    tmp_path: Path, case: str
) -> None:
    """五种形状不对 ⇒ 拒绝，**不**读成一组空 label。

    读成空的话，下游会报一句听起来完全合理的话：「这条任务不是我们派的」。而真相是
    **文件换了形状** —— 同一件事、两种修法（一个去查谁派的，一个去查 provider 的
    落盘格式变没变），所以拒绝必须发生在读的这一侧。

    这条判据出现之前，那句「形状不对就拒绝」**只写在 docstring 里**：把校验换成
    一个宽容的读法（labels 不是列表就返回 ``()``、逐项滤掉非字符串），套件照样全绿
    —— 实测到过。docstring 里的话没有执行器时，它只是一句自觉。
    """
    _task_file(tmp_path, MALFORMED_TASKS[case])

    with pytest.raises(ConfigError):
        read_task_labels(tmp_path, TASK_ID)

"""``adapters/agent_runtime.py`` 的三条声明，各配一个执行器。

这个文件很短，因为那个适配器也很短 —— 但它那三行 docstring 里的每一句都是
**别处做了会出事**的决定，所以每一句都要有一个能被一个具体破坏打红的地方：

* 「退出码原样交回」—— 破坏它（``return 0``、或者 ``if rc: raise``）之后，driver 在
  ``worker-outcome.jsonl`` 里记下的结局会与 delegate 真正的结局分家，而那条记录是
  **唯一**一份「这一轮为什么没成」；
* 「不捕获 stdout / stderr」—— 破坏它（``capture_output=True``）之后，这一层要么
  把现场丢掉，要么自己转述一行「delegate 退 3」。后者更坏：一条被转述过的失败
  看起来与本层自己的配置错一模一样；
* 「不折 OSError」—— 破坏它（``except OSError: return 1``）之后，「起不来」（配置错）
  与「跑完但失败」（研究没成）在退出码上不可区分，而两者的处置相反。

替身是真子进程，理由与 ``tests/test_research_worker.py`` 同一处：让对面**照着自己
收到了什么**说话，而不是由一条断言替它说。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from marketops.adapters.agent_runtime import run_delegate


def write_program(tmp_path: Path, body: str) -> Path:
    """一个真的可执行文件，解释器就是跑测试这个。"""
    program = tmp_path / "delegate"
    program.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    program.chmod(0o755)
    return program


@pytest.mark.parametrize("code", [0, 3, 130])
def test_the_childs_exit_code_comes_back_unchanged(tmp_path: Path, code: int) -> None:
    """0 / 3 / 130 都原样回来 —— 不解释、不替换、不吞。

    130 是刻意选的：它是一次 ``SIGINT`` 的常规退出码。一个「非零就归 1」的规范化
    实现会在这里红，而它的后果是把「人按了 Ctrl-C」记成一次普通的失败。
    """
    program = write_program(tmp_path, f"raise SystemExit({code})\n")

    assert run_delegate([str(program)]) == code


def test_the_child_writes_to_this_process_own_streams(tmp_path: Path, capfd) -> None:
    """子进程的 stdout / stderr 直接落在这两个流上，**中间没有一层**。

    捕获再转发时，长的输出会因为缓冲而在崩溃那一刻整段消失；捕获后不转发，现场
    就只剩本层自己写的一行。所以这里断的是「它出现我这边」——一个
    ``capture_output=True`` 的实现会红。

    **必须是 ``capfd`` 而不是 ``capsys``。** 后者换掉的是 ``sys.stdout`` 这个
    Python 对象，而子进程继承的是那个对象的**文件描述符**（fd 1）—— 一个捕获在
    对象层的替身看不见它写下的任何东西。这条不是风格：用 ``capsys`` 时这条判据
    恒红，而红的原因会是「工具选错了」，不是「实现捕获了输出」。
    """
    program = write_program(
        tmp_path, "import sys\nprint('delegate 说了一句话')\nprint('另一句', file=sys.stderr)\n"
    )

    assert run_delegate([str(program)]) == 0

    captured = capfd.readouterr()
    assert "delegate 说了一句话" in captured.out
    assert "另一句" in captured.err


def test_a_program_that_cannot_be_spawned_raises_instead_of_returning_a_code(
    tmp_path: Path,
) -> None:
    """不在盘上的程序 ⇒ ``OSError``，**不是**一个退出码。

    这一层分不出「起不来」与「跑完但失败」，所以它不替调用方选一个数字：调用方
    （``interfaces/research_worker.py``）把前者读成配置错（退 2、且没派发），把后者
    读成一次真实的失败（原样交回）。折成数字就是把这次分类搬到一个看不见的地方。
    """
    with pytest.raises(OSError):
        run_delegate([str(tmp_path / "not-a-program")])

"""Phase G5c：执行器在**组合根**上的接线（``interfaces/composition.py``）。

## 三个取值，三件不同的事

``MARKETOPS_DISPATCH_EXECUTOR`` 只有两个被认得的取值，而它们各自的处置不同：

* **没设 / 空串** —— 落回 ``local``。本机任务板是**正式**执行器，不需要任何第三方
  服务；「一个新部署没有派发路径」因此不是缺省状态。
* **``none``** —— 这个部署决定不派发。``default_dispatcher`` 返回 ``None``（与
  ``wire_research`` 不接没配 key 的检索提供方同一条取向）。
* **别的任何字符串** —— 这是一个**拼错了**的部署。处置是把话说到位：抛
  ``ConfigError``。落回缺省会让一个拼错的取值表现为「用本地板」，而它与
  「本部署不派发」在第一次受理之前都看不出来 —— 两件不同的事，一句话。

## 第二件事：能力表读的是**对象**，不是配置

``_integrations`` 的 dispatch 行从传进来的 ``dispatcher`` 读 ``runtime_ready``，
不从 ``Settings`` 重推。这两者在部署路径上恰好一致（``_serve`` 一定构造，构造不出来
就退 2），而在**直接构造服务面**的调用方那里不同 —— 那条不同正是本判据要钉住的
东西，它在 ``tests/test_mcp_control_tools.py`` 里。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from marketops.adapters.local_board import LocalBoardDispatcher
from marketops.config import Settings
from marketops.domain.errors import ConfigError
from marketops.interfaces.composition import default_dispatcher


def test_an_unset_executor_means_the_local_board(settings: Settings) -> None:
    """没设执行器：**本地板**，不是 ``None``。

    Issue #2 的验收条款逐字：``MARKETOPS_DISPATCH_*`` 都没配的部署里，一次真实的
    job 受理 → 领取 → 结算要能走完。缺省值因此必须是一个真的执行器。
    """
    unset = replace(settings, dispatch_executor="", dispatch_root=None)
    built = default_dispatcher(unset)
    assert isinstance(built, LocalBoardDispatcher)


def test_the_local_board_lives_under_the_data_root_by_default(
    settings: Settings, tmp_path: Path
) -> None:
    """``dispatch_root`` 没给时落在 ``<data_root>/dispatch`` —— 与 ``state_dir``
    一族同一条：写死一处，「同一份代码跑在不同机器上」就变成「每个环境一份代码」。"""
    built = default_dispatcher(replace(settings, dispatch_root=None))
    assert isinstance(built, LocalBoardDispatcher)
    assert built.path == settings.data_root / "dispatch" / "board.sqlite3"


def test_no_executor_is_a_fact_not_an_error(settings: Settings) -> None:
    """``none``：``None``，不是异常。

    这与 ``wire_research`` 在没配 key 时不接检索提供方是同一条取向 —— **没有的
    入口优于有入口再加一条注意事项**。返回一个「建得起来、派发时才炸」的对象会
    更坏：它在部署自检里完全隐形。
    """
    assert default_dispatcher(replace(settings, dispatch_executor="none")) is None


def test_an_unrecognised_executor_is_refused_not_downgraded(settings: Settings) -> None:
    """拼错的取值：**拒绝**，不降级成「本部署不派发」，也不落回本地板。

    错误消息点名的是**环境变量**与**两个合法取值**：读到它的人手里有环境变量，
    没有 ``Settings`` 那个类。
    """
    with pytest.raises(ConfigError) as raised:
        default_dispatcher(replace(settings, dispatch_executor="board-ish"))
    message = str(raised.value)
    assert "MARKETOPS_DISPATCH_EXECUTOR" in message
    assert "local" in message and "none" in message


def test_an_unwritable_root_fails_at_construction(settings: Settings, tmp_path: Path) -> None:
    """根建不出来：**在构造时**抛，不留到第一次派发。

    适配器自己那条纪律（「一个建得起来、派发时才炸的执行器在部署自检里完全
    隐形的」）在组合根上必须同样成立 —— 否则这条纪律只在有人直接 new 适配器时
    生效，而部署走的是这里。

    下半段是**正对照**：把根换成真目录，它建得起来。没有这一条，上面那句在一个
    恒抛的实现上同样成立。
    """
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("我不是一个目录\n", encoding="utf-8")
    broken = replace(settings, dispatch_executor="local", dispatch_root=blocked)
    with pytest.raises(ConfigError):
        default_dispatcher(broken)

    working = replace(broken, dispatch_root=tmp_path / "board")
    assert isinstance(default_dispatcher(working), LocalBoardDispatcher)

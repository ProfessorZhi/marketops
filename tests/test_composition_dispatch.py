"""Phase G5c：外部执行器在**组合根**上的接线（``interfaces/composition.py``）。

## 这条路径上最容易混成一句话的两个问题

``Settings.quay_enabled`` 只有三样都给全时才为真。所以它**不是**「配了 vs 没配」
的判据，而是「配全了 vs 没配全」的判据，而那两半各自的处置不同：

* **三样一个都没给** —— 本部署没有执行器。这是一条**事实**，处置是「不派发」，
  于是 ``default_dispatcher`` 返回 ``None``（与 ``wire_research`` 不接没配 key
  的检索提供方同一条取向）。
* **只给了一两样** —— 这是一个**配错了**的部署。处置是把话说到位，于是它抛
  ``ConfigError``。按前者处置会把它降级成能力表上一格 ``runtime_ready: false``，
  而那一格读起来与「本部署没打算派发」逐字相同 —— 两件不同的事，一句话。

## 第二件事：能力表读的是**对象**，不是配置

``_integrations`` 的 quay 行从传进来的 ``quay`` 读 ``runtime_ready``，不从
``Settings`` 重推。这两者在部署路径上恰好一致（``_serve`` 一定构造，构造不出来
就退 2），而在**直接构造服务面**的调用方那里不同 —— 那条不同正是本判据要钉住的
东西，它在 ``tests/test_mcp_control_tools.py`` 里。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from marketops.adapters.quay_cli import QuayCliDispatcher
from marketops.config import Settings
from marketops.domain.errors import ConfigError
from marketops.interfaces.composition import default_dispatcher

FAKE_CLI = Path(__file__).with_name("fake_quay_cli.py")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """一个含 ``.quay/config.yml`` 的工作区根（真 CLI 与假 CLI 都要求它存在）。"""
    store = tmp_path / "store"
    (store / ".quay").mkdir(parents=True)
    (store / ".quay" / "config.yml").write_text("provider: native\n", encoding="utf-8")
    return store


def test_no_executor_configured_is_a_fact_not_an_error(settings: Settings) -> None:
    """三样一个都没给：``None``，不是异常。

    这与 ``wire_research`` 在没配 key 时不接检索提供方是同一条取向 —— **没有的
    入口优于有入口再加一条注意事项**。返回一个「建得起来、派发时才炸」的对象会
    更坏：它在部署自检里完全隐形。
    """
    assert settings.quay_enabled is False, "本用例的前提就是没配"
    assert default_dispatcher(settings) is None


@pytest.mark.parametrize(
    "supplied",
    [
        ("cli",),
        ("node",),
        ("root",),
        ("cli", "node"),
        ("cli", "root"),
        ("node", "root"),
    ],
)
def test_a_half_configured_executor_is_refused_not_downgraded(
    settings: Settings, tmp_path: Path, supplied: tuple[str, ...]
) -> None:
    """只给了一两样：**拒绝**，不降级成「本部署没有执行器」。

    这是本文件里最重要的一条。六种残缺组合都必须在**构造时**说出来，因为按
    ``None`` 处置会让它长成能力表上一句「本部署没有配置外部执行器」—— 而那句话
    是假的，有人明明配了两样。``Settings.quay_enabled`` 对这两种情况都给 ``False``，
    所以区别只能在这里做，也只能被这条判据看见。

    错误消息点名的是**环境变量**而不是 ``Settings`` 的字段：读到它的人手里有
    环境变量，没有这个类。
    """
    values = {
        "cli": FAKE_CLI,
        "node": Path(sys.executable),
        "root": tmp_path / "store",
    }
    configured = replace(
        settings,
        quay_cli_path=values["cli"] if "cli" in supplied else None,
        quay_node_path=values["node"] if "node" in supplied else None,
        quay_root=values["root"] if "root" in supplied else None,
    )
    assert configured.quay_enabled is False, "残缺配置在 Settings 上就是假 —— 这正是它会被降级的原因"

    with pytest.raises(ConfigError) as raised:
        default_dispatcher(configured)

    # 消息必须把「给全了哪些」与「缺哪一个」分开说 —— 只报一句「配了一半」的人
    # 还得回去数环境变量。所以判据不是「三个名字都出现过」（那只要把三样都列
    # 一遍就成立了），而是**哪一个出现在哪一半**。
    given_side, separator, missing_side = str(raised.value).partition("缺 ")
    assert separator, f"消息没有说缺哪一样：{raised.value}"
    for name in ("MARKETOPS_QUAY_CLI", "MARKETOPS_QUAY_NODE", "MARKETOPS_QUAY_ROOT"):
        if name.replace("MARKETOPS_QUAY_", "").lower() in supplied:
            assert name in given_side, f"{name} 给了却被列在「缺」那一侧：{raised.value}"
        else:
            assert name in missing_side, f"没点名缺的 {name}：{raised.value}"


def test_a_path_that_is_not_on_disk_fails_at_construction(
    settings: Settings, root: Path
) -> None:
    """三样齐了而路径不在盘上：**在构造时**抛，不留到第一次派发。

    适配器自己那条纪律（「一个建得起来、派发时才炸的执行器在部署自检里是完全
    隐形的」）在组合根上必须同样成立 —— 否则这条纪律只在有人直接 new 适配器时
    生效，而部署走的是这里。

    下半段是**正对照**：同一组参数、把那条不存在的路径换成真路径，它建得起来。
    没有这一条，上面那句在一个恒抛的实现上同样成立。
    """
    broken = replace(
        settings,
        quay_cli_path=root / "没有这个文件.js",
        quay_node_path=Path(sys.executable),
        quay_root=root,
    )
    with pytest.raises(ConfigError):
        default_dispatcher(broken)

    working = replace(broken, quay_cli_path=FAKE_CLI)
    built = default_dispatcher(working)
    assert isinstance(built, QuayCliDispatcher)


def test_the_root_is_only_checked_for_existence_at_construction(
    tmp_path: Path, settings: Settings
) -> None:
    """构造期只判「在不在盘上」，「这里是不是一个工作区根」由 CLI 在某次真实调用里答。

    这一条钉的是一件**没做的事**，而它有代价：一个不含 ``.quay/config.yml`` 的
    普通目录也建得出执行器，于是「根不成立」要等到第一次派发（或 ``--root`` 失败
    关闭那一刻，见适配器 M10）才被说出来。

    代价是**被选的**：判「是不是根」要起一个子进程，而构造发生在
    ``get_capabilities`` 每被读一次的地方 —— §2 逐字不许健康检查去调计费后端，
    同一条理由不许它起进程。有人在构造期加上这条校验时，这个用例会红，
    而它红的位置正好是那条取舍要被重新讨论的地方。
    """
    store = tmp_path / "not-a-store"
    store.mkdir()
    configured = replace(
        settings,
        quay_cli_path=FAKE_CLI,
        quay_node_path=Path(sys.executable),
        quay_root=store,
    )
    assert not (store / ".quay").exists(), "本用例的前提：这不是一个工作区根"
    assert isinstance(default_dispatcher(configured), QuayCliDispatcher)

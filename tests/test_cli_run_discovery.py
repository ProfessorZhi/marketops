"""``run-discovery``：运维这一侧的开放网络发现入口。

## 这个文件测的是**前置条件与退出码**，不是那条链本身

链本身（检索 → 取页面 → 证据 → 工件 → 回执）在 ``tests/test_discovery_run.py``
里测，用的是注入的替身。这里测的是 CLI 这一层的两条纪律，两条都**不联网**：

1. **参数与配置不成立时，一次检索都没发出去就退出 2。** 这一条比它看起来重要：
   工件名被占用、profile 没声明额度、范围名不是规范形式 —— 这三件事在跑到一半
   才发现时，钱已经花掉了。所以它们必须发生在**第一发查询之前**，而判据是
   「``serper`` 一次都没被碰到」。
2. **``--dry-run`` 是真的不联网、不写盘。** 它存在的理由是让运维先看清这一轮会
   发什么；一个偷偷发了一发的 ``--dry-run`` 会让「先看看」变成一次花钱的操作，
   而报告上看不出来。

## 它为什么不是 MCP 工具

与 ``read-motherpool`` 同一个理由（见 ``_cmd_run_discovery`` 的 docstring）：
这一条会真的花预算。用户面向的服务面入口是
``submit_research(input_kind=discovery_scope)`` —— 那一侧收的是**已经跑出来的
范围**。判据在 ``tests/test_mcp_job_tools.py``。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketops.config import DEFAULT_PROFILE, Settings
from marketops.interfaces.cli import build_parser, main

_SCOPE = "devon-2026-discovery"
_REGION = "Devon"


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """一个装好的部署：数据根已建好，``SERPER_API_KEY`` **未设置**。

    走环境变量而不是把 ``Settings`` 塞进 ``main``：CLI 走的是 ``load_settings``，
    而那一段装载逻辑正是被测的东西之一（「没 key 就退 2」这条判据全靠它）。
    """
    data_root = tmp_path / "runtime"
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(data_root))
    monkeypatch.delenv("MARKETOPS_PROFILE", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    resolved = Settings(
        data_root=data_root,
        profiles_dir=Path(__file__).resolve().parents[1] / "profiles",
        profile=DEFAULT_PROFILE,
    )
    resolved.ensure_data_dirs()
    return resolved


def _run(*argv: str) -> int:
    return main(["run-discovery", *argv])


def _base(*extra: str) -> list[str]:
    return ["--scope", _SCOPE, "--region", _REGION, *extra]


# --- --dry-run：不联网、不写盘、把该说的说出来 -------------------------------


def test_a_dry_run_exits_zero_and_writes_nothing(deployed: Settings) -> None:
    """``--dry-run`` 只展开检索式。**一个工件、一份回执都不许出现。**"""
    assert _run(*_base("--dry-run")) == 0

    assert list(deployed.inputs_dir.glob("*")) == []
    assert list(deployed.benchmarks_dir.glob("*")) == []


def test_a_dry_run_does_not_need_a_serper_key(deployed: Settings) -> None:
    """展开检索式是**纯函数**，所以它不需要凭据 —— 而这正是它存在的理由。

    需要 key 的话，「先看看这一轮会发什么」这件事就变成一件要先去要凭据的事，
    而它本来不需要。
    """
    assert _run(*_base("--dry-run")) == 0


def test_a_dry_run_names_the_plan_the_ceiling_and_the_artifact(
    deployed: Settings, capsys
) -> None:
    """计划、上限、工件名三样都要说出来，而且**「发不完」这件事也要说出来**。"""
    assert _run(*_base("--dry-run")) == 0

    out = capsys.readouterr()
    assert "DRY RUN" in out.out
    assert "未发起任何网络请求" in out.out
    assert _SCOPE in out.err, "工件名要出现在 stderr 那一行"
    assert "配置装载、profile 装载、检索式展开" in out.out, "已完成的那几件事要写清楚"


def test_a_dry_run_says_when_the_plan_is_longer_than_the_ceiling(
    deployed: Settings, capsys
) -> None:
    """计划条数超过额度时必须点出来，否则「计划了 542 条」会被读成「会发 542 条」。

    这一轮的真实形状是：profile 的词表乘积远大于 profile 声明的额度，所以
    **计划总是发不完**。这不是错误，但它必须被读出来 —— 停下来时是「额度用尽」，
    而它与「候选枯竭」是两条不同的读数。
    """
    assert _run(*_base("--dry-run")) == 0

    out = capsys.readouterr().out
    assert "发不完" in out
    assert "额度用尽" in out
    # 正控：计划条数与上限都要印出来，否则上面那句话没有可比的两个数。
    assert "将会执行：" in out
    assert "上限：" in out


# --- 前置条件不成立：退出码 2 -------------------------------------------------


def test_a_missing_region_is_a_parser_error(deployed: Settings) -> None:
    """``--region`` 必填、没有缺省值 —— 缺了它是 argparse 那一层的错，退出码 2。

    给它编一个缺省值（比如 ``GB``）等于替这一次运行做了一个它没做过的决定，
    而那个决定会逐字进工件、进回执、进每一个下游读数，且不留痕。
    """
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["run-discovery", "--scope", _SCOPE])

    assert excinfo.value.code == 2


def test_a_blank_region_exits_two(deployed: Settings, capsys) -> None:
    """空白地区同样不成立 —— 一条绑着空地区的检索式会变成一次全局检索。"""
    assert _run("--scope", _SCOPE, "--region", "   ", "--dry-run") == 2

    err = capsys.readouterr().err
    assert "配置错误" in err
    assert "--region" in err


def test_a_scope_that_needs_normalizing_exits_two(deployed: Settings, capsys) -> None:
    """范围名会变成文件名 —— 需要规范化的名字是**调用方给错了**。"""
    assert _run("--scope", "Devon 2026", "--region", _REGION, "--dry-run") == 2

    assert "配置错误" in capsys.readouterr().err


def test_a_limit_below_one_exits_two(deployed: Settings, capsys) -> None:
    assert _run(*_base("--limit", "0", "--dry-run")) == 2

    assert "至少是 1" in capsys.readouterr().err


def test_a_negative_max_candidates_exits_two(deployed: Settings, capsys) -> None:
    assert _run(*_base("--max-candidates", "-1", "--dry-run")) == 2

    assert "不能是负数" in capsys.readouterr().err


def test_an_occupied_artifact_name_exits_two_before_spending(
    deployed: Settings, capsys
) -> None:
    """工件名已被占用 ⇒ 在**发第一发查询之前**退出 2。

    这一条是整组里最重要的一个：名字在跑之前就是已知的，而「付过钱之后才发现
    这个名字被占用」是一次已经花了钱的失败。
    """
    (deployed.inputs_dir / f"{_SCOPE}.jsonl").write_text(
        json.dumps({"name": "already here"}) + "\n", encoding="utf-8", newline="\n"
    )

    assert _run(*_base()) == 2

    err = capsys.readouterr().err
    assert "配置错误" in err
    assert f"{_SCOPE}.jsonl" in err


def test_a_real_run_without_a_serper_key_exits_two_and_says_the_two_options(
    deployed: Settings, capsys
) -> None:
    """没 key ⇒ 2，而且话里要给出两条可走的路（``--dry-run`` / 放 key）。"""
    assert _run(*_base()) == 2

    err = capsys.readouterr().err
    assert "SERPER_API_KEY" in err
    assert "--dry-run" in err
    assert list(deployed.inputs_dir.glob("*")) == [], "退 2 之前不许留下任何东西"


def test_the_key_check_happens_after_the_artifact_name_check(
    deployed: Settings, capsys
) -> None:
    """两条前置条件的**先后**是要紧的：名字这一条先说。

    反过来的话，一个「名字被占用 + 没有 key」的部署会先被告知去配 key ——
    配好之后重跑，才发现真正的拦路者。话要说在**离处置最近**的那一处。
    """
    (deployed.inputs_dir / f"{_SCOPE}.jsonl").write_text("x\n", encoding="utf-8", newline="\n")

    assert _run(*_base()) == 2

    err = capsys.readouterr().err
    assert f"{_SCOPE}.jsonl" in err
    assert "SERPER_API_KEY" not in err


# --- 参数被真的传下去了 -------------------------------------------------------


def test_the_scope_limit_and_cap_reach_the_plan_and_the_ceiling_line(
    deployed: Settings, capsys
) -> None:
    """``--limit`` 与 ``--max-candidates`` 要真的进那两条读物，不是被静默忽略。

    ``--limit`` 出现在「每条最多 N 条结果」那一行，``--max-candidates`` 出现在
    上限那一行 —— 两处都是给人读的读数，而它们是这一次运行的参数唯一的出口。
    """
    assert _run(*_base("--limit", "3", "--max-candidates", "7", "--dry-run")) == 0

    out = capsys.readouterr().out
    assert "每条最多 3 条结果" in out
    assert "最多 7 个" in out


def test_no_fetch_is_reported_as_choosing_not_to_fetch_not_as_a_failure(
    deployed: Settings, capsys
) -> None:
    """``--no-fetch`` 是一次**选择**，所以它的读数不许长成一次失败。

    它只在 ``--dry-run`` 下有一条可断言的话（真跑起来之后不再印这一段），
    所以这里钉的是「这个开关存在、能被解析、而且不改变 plan 那一段的形状」。
    """
    assert _run(*_base("--no-fetch", "--dry-run")) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out
    # 计划那一段与不带 ``--no-fetch`` 时是同一件事：取不取页面**不改变发什么检索**。
    assert json.loads(out.split("\n== DRY RUN ==")[0])["frozen_region"] == _REGION


def test_the_dry_run_prints_a_real_page_of_the_plan(deployed: Settings, capsys) -> None:
    """计划那一份 JSON 里要有真的检索式，而不是一个空列表。

    空列表会读成「这一轮的词表一条都展不开」—— 而那是一条会失败关闭的读数
    （``build_open_web_queries`` 拒绝零条），所以它不该在**成功**的那条路径上出现。
    """
    assert _run(*_base("--dry-run")) == 0

    plan = json.loads(capsys.readouterr().out.split("\n== DRY RUN ==")[0])
    assert plan["queries_planned"] > 0, "成功路径上的计划不可能是空的"
    assert plan["frozen_region"] == _REGION
    assert plan["source_method"] == "search.engine.v1"
    assert plan["skipped"] == [], "这一份 profile 的模板应当全部展开得出"

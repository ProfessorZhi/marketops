"""``marketops trial preflight``（§26）：四值判词、七行，以及它**不做什么**。

## 这一份测的是什么，不是什么

它测**判据**，不测措辞：四值之间的次序、七行的形状在故障路径上不变、以及三个
「不许发生」——不许建库、不许联网、不许打印凭据。三个不许是本文件里承重的那一半：
一个「会顺手把故障修掉一半」或者「悄悄花掉一次计费调用」的自检，比没有自检更坏，
因为它的产物是一句**看起来通过了**的话。

## 为什么不与 ``--check`` 合并成一份测试

两个表面问的是**不同的问题**，而本文件里有一条判据专门钉住那个差别：
一份受众写错了的环，``--check`` 是绿的（进程起得来）而这一条是红的（谁都调不动）。
``tests/test_mcp_entry.py::test_check_shows_the_two_sides_of_an_audience_mismatch``
是那个差别的前一半，而它**不断言失败**是有意的。把它们并进一个文件，会让后来的人
以为那是同一条判据的两个实现。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from marketops.config import REPO_ROOT
from marketops.security.catalog import TOOL_CATALOG
from marketops.interfaces.mcp.__main__ import (
    PREFLIGHT_BLOCKED_CODE,
    PREFLIGHT_BLOCKED_CONFIG,
    PREFLIGHT_BLOCKED_EXTERNAL,
    PREFLIGHT_READY,
    PreflightReport,
    PreflightRow,
    _PREFLIGHT_CHECKS,
    _PREFLIGHT_MEANING,
    _PREFLIGHT_SEVERITY,
)
from tests.mcp_harness import MCP_URL, ring_entry, ring_payload

#: 四条判词，写下来一次。少一条会让下面那条「四值各有各的话」静默变成三条。
VERDICTS = (
    PREFLIGHT_READY,
    PREFLIGHT_BLOCKED_CONFIG,
    PREFLIGHT_BLOCKED_EXTERNAL,
    PREFLIGHT_BLOCKED_CODE,
)


def _row(check: str, status: str) -> PreflightRow:
    return PreflightRow(check=check, status=status, detail="", facts={})


def _hermetic(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    """把 ``MARKETOPS_*`` 清干净再设给定的那几个。

    **必须清**，不能只设：这台机器上跑着的服务面很可能带着一整组生产 env，
    而 ``load_settings()`` 读的就是环境。只设不删的写法会在这台机器上红、在
    一台干净的机器上绿 —— 那种判据测的是「谁在跑它」而不是代码。
    """
    for name in [key for key in os.environ if key.startswith("MARKETOPS_")]:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _ring(tmp_path: Path, *principals: tuple[str, str], workspaces=None) -> Path:
    """一份最小令牌环。与 ``tests/test_mcp_entry.py`` 的写盘逻辑同一形状。

    这里没有 import 那一个而是重抄一遍：``test_mcp_entry`` 是**另一个表面**的
    测试文件，两个测试模块互相 import 会让「跑其中一个」变成「跑那一半的单例与
    夹具」。代价是这一段有第二份 —— 而它是六行纯数据，不是判据。
    """
    from marketops.security.catalog import Role
    from marketops.security.tokens import mint

    entries = []
    for principal, role in principals:
        _, digest = mint(principal, [Role(role)])
        extra = {} if workspaces is None else {"workspaces": workspaces}
        entries.append(ring_entry(digest, principal, [role], **extra))
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "mcp-tokens.json"
    path.write_text(json.dumps(ring_payload(*entries)), encoding="utf-8")
    return path


def _run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """跑一次真的命令行。清掉宿主机的 ``MARKETOPS_*`` 再叠加给定的那几个。"""
    full = {k: v for k, v in os.environ.items() if not k.startswith("MARKETOPS_")}
    full.update(env)
    return subprocess.run(
        [sys.executable, "-m", "marketops.interfaces.cli", *argv],
        cwd=REPO_ROOT,
        env=full,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _report(monkeypatch: pytest.MonkeyPatch, **env: str) -> PreflightReport:
    _hermetic(monkeypatch, **env)
    from marketops.interfaces.mcp.__main__ import trial_preflight

    return trial_preflight()


# ----------------------------------------------------------------------
# 判词：次序，以及「四值各有各的话」
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ((PREFLIGHT_READY,) * 7, PREFLIGHT_READY),
        ((PREFLIGHT_READY, PREFLIGHT_BLOCKED_EXTERNAL), PREFLIGHT_BLOCKED_EXTERNAL),
        ((PREFLIGHT_BLOCKED_EXTERNAL, PREFLIGHT_BLOCKED_CONFIG), PREFLIGHT_BLOCKED_CONFIG),
        ((PREFLIGHT_BLOCKED_CONFIG, PREFLIGHT_BLOCKED_CODE), PREFLIGHT_BLOCKED_CODE),
    ],
)
def test_the_verdict_is_the_heaviest_row_not_the_first(statuses, expected) -> None:
    """判词取**最重的那一行**。

    取第一行或最后一行的实现在这四个用例上都会红，而它们的差别不是风格：
    §26 那句「``BLOCKED_EXTERNAL`` 的前提是代码侧已经全部做完」要求代码没做完时
    判词必须落在 ``BLOCKED_CODE`` 上 —— 一个按位置取值的实现会把一份「代码没接完、
    也没有凭据」的部署印成 ``BLOCKED_EXTERNAL``，于是下一步是去找人开访问权，
    而真正该动的是接线。
    """
    rows = tuple(_row(check, status) for check, status in zip(_PREFLIGHT_CHECKS, statuses, strict=False))
    assert PreflightReport(rows=rows).verdict == expected


def test_all_four_verdicts_have_a_severity_and_a_sentence() -> None:
    """四值这件事只有**一处**完整的定义，而这一处必须一个不漏。

    三张表的键集合必须相同：漏一格 ``_PREFLIGHT_MEANING`` 的后果是渲染时
    ``KeyError``（响），而漏一格 ``_PREFLIGHT_SEVERITY`` 的后果是 ``max`` 在
    一条没有登记的行上抛（也响）—— 但**加第五个判词时只补了其中一张**的后果是
    静默的：渲染仍然跑得完，只是少说一句「下一步动什么」。这条判据钉的是那个。
    """
    assert set(_PREFLIGHT_SEVERITY) == set(VERDICTS), (
        "严重度表与四值不是同一组 —— 取 max 会在没登记的那一行上抛"
    )
    assert set(_PREFLIGHT_MEANING) == set(VERDICTS), (
        "判词说明与四值不是同一组 —— 少的那一个渲染时不会有任何提示"
    )
    for name in VERDICTS:
        assert _PREFLIGHT_MEANING[name].strip(), f"{name} 的说明是空的"


# ----------------------------------------------------------------------
# 形状：七行，故障路径上也一样
# ----------------------------------------------------------------------
def test_the_configuration_failure_still_answers_every_row(monkeypatch) -> None:
    """连配置都读不出来时，报告仍是**七行**。

    这一条照着 ``_payload`` 那句「形状固定：即使配置装载失败也保持同一组键」，
    理由逐字相同：一个会变形的输出会让解析它的脚本在**故障路径**上崩掉，
    而故障路径正是它被写出来的理由。
    """
    report = _report(monkeypatch)
    assert [row.check for row in report.rows] == list(_PREFLIGHT_CHECKS)
    assert report.verdict == PREFLIGHT_BLOCKED_CONFIG
    assert report.rows[0].check == "config"
    # 第一行是**失败的那一行**，后面几行是「没问到」——两者分开写，不复制同一句原因。
    assert "MARKETOPS_DATA_ROOT" in report.rows[0].detail
    # 「后面几行没被问到」的判据不是它们的措辞，而是：它们说的是**同一句话**，
    # 且这一句不是失败那一行的原因。一个把失败原因抄到每一行上的实现会在这里红，
    # 而那份报告读起来像是「七行都答过了」—— 实际是同一句话说了七遍。
    tail = {row.detail for row in report.rows[1:]}
    assert len(tail) == 1, f"后面几行各说各话，说明它们各自被问过了：{sorted(tail)}"
    assert report.rows[0].detail not in tail
    # 而整个报告**一格事实都没有**：装配在第一行就停下了，它什么都没量到。
    # 这一条防的是一个从缺省值里造出事实来的实现 —— 那种事实读起来与量到的
    # 一模一样，下一步就会有人照着它去改别的东西。
    assert all(not row.facts for row in report.rows), "第一行就停下了，却有行带着事实"


def test_a_working_deployment_still_answers_every_row(tmp_path, monkeypatch) -> None:
    ring = _ring(tmp_path / "tokens", ("claude-control", "controller"))
    report = _report(
        monkeypatch,
        MARKETOPS_DATA_ROOT=str(tmp_path / "state"),
        MARKETOPS_MCP_TOKENS=str(ring),
    )
    assert [row.check for row in report.rows] == list(_PREFLIGHT_CHECKS)
    assert report.verdict in VERDICTS
    # config / endpoint / auth / database / budget 这五行在这个部署上都答得出来。
    answered = {row.check: row.status for row in report.rows}
    for check in ("config", "endpoint", "auth", "database", "budget"):
        assert answered[check] == PREFLIGHT_READY, f"{check} 应当就绪：{answered[check]}"


def test_the_json_payload_has_the_same_four_keys_on_every_row(tmp_path) -> None:
    """``--json`` 的每一行都是同样四个键 —— 供脚本读的那一份不能变形。"""
    done = _run(
        ["trial", "preflight", "--json"],
        {"MARKETOPS_DATA_ROOT": str(tmp_path / "state")},
    )
    assert done.returncode == 1, (done.stdout, done.stderr)
    payload = json.loads(done.stdout)
    # 只设数据根、不设令牌环 ⇒ 这个部署**不可能**是 ``READY``。钉住的是这个，
    # 不是「它落在哪一条阻塞判词上」：后者取决于这份构建接了多少工具，而那是一个
    # 随接线进度变的量 —— 把今天的读数写死在这里，会让这条判据在接线推进时
    # **因为一个无关的进步**而变红。
    assert payload["verdict"] in VERDICTS
    assert payload["verdict"] != PREFLIGHT_READY
    assert [row["check"] for row in payload["rows"]] == list(_PREFLIGHT_CHECKS)
    for row in payload["rows"]:
        assert set(row) == {"check", "status", "detail", "facts"}, row
        assert row["status"] in VERDICTS


def test_the_exit_code_follows_the_verdict_and_nothing_else(monkeypatch) -> None:
    """退出码是判词的一对一映射，**两个方向**都要钉。

    只测「不就绪退 1」那一半的话，一个永远退 1 的实现会全绿 —— 而它把这四值
    变成了三值（``READY`` 再也表达不出来）。
    """
    from marketops.interfaces import cli as cli_module
    from marketops.interfaces.mcp import __main__ as entry

    def fake(verdict: str) -> PreflightReport:
        rows = tuple(
            _row(check, verdict if check == "code" else PREFLIGHT_READY)
            for check in _PREFLIGHT_CHECKS
        )
        return PreflightReport(rows=rows)

    for verdict, expected in (
        (PREFLIGHT_READY, 0),
        (PREFLIGHT_BLOCKED_CONFIG, 1),
        (PREFLIGHT_BLOCKED_EXTERNAL, 1),
        (PREFLIGHT_BLOCKED_CODE, 1),
    ):
        monkeypatch.setattr(entry, "trial_preflight", lambda v=verdict: fake(v))
        assert cli_module.main(["trial", "preflight"]) == expected, verdict


# ----------------------------------------------------------------------
# 三个「不许发生」
# ----------------------------------------------------------------------
def test_it_does_not_create_the_state_directory_or_the_database(tmp_path, monkeypatch) -> None:
    """一个会把目录树建出来的自检，会在「目录还没准备好」这种真故障面前先把故障
    修掉一半 —— 而下一次读数就不再是它当初的样子。

    判据是「数据根底下**一个新条目都没有**」，不是「数据库文件不存在」：后者会
    放过一个先建 ``state/`` 再建库的中间状态，而那正是 ``ensure_data_dirs()``
    做的事（``_serve()`` 会调它，这一条路径不调）。
    """
    root = tmp_path / "runtime"
    root.mkdir()
    before = sorted(p.name for p in root.iterdir())
    report = _report(monkeypatch, MARKETOPS_DATA_ROOT=str(root))
    assert report.rows[0].status == PREFLIGHT_READY, report.rows[0].detail
    assert sorted(p.name for p in root.iterdir()) == before == []


def test_it_never_reaches_the_network(tmp_path, monkeypatch) -> None:
    """§7.2 逐字：「一个**不执行真实付费请求**的 trial-preflight」。

    这一条钉的是那条要求**可执行的那一半**：没有网络调用 ⇒ 没有一次按调用计费的
    检索。把名字解析与建连都换成会抛的替身，让「悄悄发了一次请求」变成一个异常
    而不是一条安静的成功 —— 后者在报告上长得与「什么都没发生」完全一样。
    """
    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("试运行自检不该联网")

    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    report = _report(monkeypatch, MARKETOPS_DATA_ROOT=str(tmp_path / "state"))
    assert [row.check for row in report.rows] == list(_PREFLIGHT_CHECKS)


def test_it_never_prints_a_credential(tmp_path) -> None:
    """两格凭据都塞进去，输出的每一处都不许出现它们的值。

    ``provider`` 那一行报的是**存在性**，而存在性正是那种最容易顺手写成取值的
    地方（``serper = sk-…`` 看起来甚至更有用）。这一条是那个诱惑的执行器。
    """
    serper = "sentinel-serper-value-9f2a"
    typesafe = "sentinel-typesafe-value-4b71"
    done = _run(
        ["trial", "preflight", "--json"],
        {
            "MARKETOPS_DATA_ROOT": str(tmp_path / "state"),
            "SERPER_API_KEY": serper,
            "TYPESAFE_API_KEY": typesafe,
        },
    )
    assert serper not in done.stdout
    assert typesafe not in done.stdout
    assert serper not in done.stderr
    assert typesafe not in done.stderr
    # 对照：两样**真的**被读到了，否则上面四条断言在一个永远为假的布尔上也成立。
    payload = json.loads(done.stdout)
    provider = next(row for row in payload["rows"] if row["check"] == "provider")
    assert provider["facts"]["serper"] is True
    assert provider["facts"]["jev"] is True


# ----------------------------------------------------------------------
# auth 那一行：它判的是「有没有人能动这个部署」
# ----------------------------------------------------------------------
def test_a_ring_that_grants_nothing_is_never_ready(tmp_path, monkeypatch) -> None:
    """「谁都不许」有两种形状，两种都不是 ``READY`` —— 而它们走**两条不同的路**。

    (a) 没配（``MARKETOPS_MCP_TOKENS`` 为空）：``__main__._load_ring`` **不抛**，
        返回 ``(None, 理由)``（那个 docstring 逐字：「这一层要把失败讲出来」），
        于是这一行**真的被问到了**，带着三格事实、判词落在 ``BLOCKED_CONFIG``。

    (b) 配了一个**空环**：``TokenRing.from_file`` 在装载期就抛（``tokens.py``：
        「空环与配错是同一件事」），``_compose`` 在那一步停下 —— 于是这一行是
        「没问到」，三格事实是空的。

    (b) 不是这一行的分支。这一行里那句 ``if not principals`` 在今天的装载器下
    **够不着**（零令牌的环到不了这里），而判据钉的是**真正的**那一处：装载期拒绝。
    留着那个分支是因为它守的状态本身还在（「环装上了但一个主体都没有」），换一个
    不拒绝空环的装载器它就会活过来 —— 但那一天到来之前，谁都不该以为它有测试。
    """
    unset = _report(monkeypatch, MARKETOPS_DATA_ROOT=str(tmp_path / "state"))
    auth = next(row for row in unset.rows if row.check == "auth")
    assert auth.status == PREFLIGHT_BLOCKED_CONFIG
    assert auth.facts["principals"] == 0
    assert unset.verdict != PREFLIGHT_READY

    empty = _ring(tmp_path / "tokens")
    refused = _report(
        monkeypatch,
        MARKETOPS_DATA_ROOT=str(tmp_path / "state"),
        MARKETOPS_MCP_TOKENS=str(empty),
    )
    row = next(r for r in refused.rows if r.check == "auth")
    assert row.status == PREFLIGHT_BLOCKED_CONFIG
    assert row.facts == {}, "这一行根本没被问到，却带着事实 —— 那就是装配停下的位置报错了"
    assert "令牌环" in row.detail
    assert refused.verdict == PREFLIGHT_BLOCKED_CONFIG


def test_a_ring_for_another_resource_is_not_ready(tmp_path, monkeypatch) -> None:
    """受众错配：环里的令牌每一个都会在传输层 401。

    这一行必须红，而它红的**理由**要看得见 —— 两个值都摆在 ``facts`` 里。
    受众错配与令牌写错在传输层上是同一个 401，所以「本进程认哪个资源」与
    「环里实际写着哪个」必须由同一个进程的一次输出同时给出。
    """
    ring = _ring(tmp_path / "tokens", ("claude-control", "controller"))
    renamed = "http://mcp.internal.example.co.uk/mcp"
    report = _report(
        monkeypatch,
        MARKETOPS_DATA_ROOT=str(tmp_path / "state"),
        MARKETOPS_MCP_TOKENS=str(ring),
        MARKETOPS_MCP_RESOURCE=renamed,
    )
    auth = next(row for row in report.rows if row.check == "auth")
    assert auth.status == PREFLIGHT_BLOCKED_CONFIG
    assert auth.facts["audiences"] == [MCP_URL]
    assert renamed in auth.detail


def test_a_ring_minted_for_a_scene_this_deployment_does_not_serve(tmp_path, monkeypatch) -> None:
    """工作区错配。判据与 ``RoleGuard`` 每一次 ``tools/call`` 用的是同一个。

    判的是环里全部工作区的**并集**：这一行问的是「有没有人能动这个部署」，而
    逐个主体地判会产出「有的令牌能动、有的不能」—— 那是一条诊断，不是一道闸。
    """
    ring = _ring(
        tmp_path / "tokens", ("claude-control", "controller"), workspaces=["another-scene"]
    )
    report = _report(
        monkeypatch,
        MARKETOPS_DATA_ROOT=str(tmp_path / "state"),
        MARKETOPS_MCP_TOKENS=str(ring),
    )
    auth = next(row for row in report.rows if row.check == "auth")
    assert auth.status == PREFLIGHT_BLOCKED_CONFIG
    assert auth.facts["workspaces"] == ["another-scene"]
    assert auth.facts["workspace"] != "another-scene"


def test_the_two_surfaces_disagree_about_a_mismatch_and_that_is_the_point(
    tmp_path,
) -> None:
    """同一个部署：``--check`` 退 0，试运行自检退 1。

    这不是矛盾，是两个问题。``--check`` 问「这个进程起不起来」——一份受众写错了的
    环挡不住进程启动，所以它绿，而它做的事是**把比对的两边都印出来**（那正是
    ``tests/test_mcp_entry.py`` 那条判据钉住的）。试运行自检问「能不能拿它开一次
    真实试运行」——每个令牌都 401 的部署开不了，所以它红。

    写成一条判据而不是两段散文，是因为「同步两个表面的行为」是本仓最容易被
    顺手做掉的一件事，而它在这里恰好是错的。
    """
    ring = _ring(tmp_path / "tokens", ("claude-control", "controller"))
    env = {
        "MARKETOPS_DATA_ROOT": str(tmp_path / "state"),
        "MARKETOPS_MCP_TOKENS": str(ring),
        "MARKETOPS_MCP_RESOURCE": "http://mcp.internal.example.co.uk/mcp",
    }
    done = _run(["trial", "preflight"], env)
    assert done.returncode == 1

    full = {k: v for k, v in os.environ.items() if not k.startswith("MARKETOPS_")}
    full.update(env)
    check = subprocess.run(
        [sys.executable, "-m", "marketops.interfaces.mcp", "--check"],
        cwd=REPO_ROOT,
        env=full,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["ok"] is True


# ----------------------------------------------------------------------
# code 那一行：它读的是 ``_register`` 的三分，不是自己重算一遍
# ----------------------------------------------------------------------
def test_the_code_row_reports_a_partition_of_the_catalog(tmp_path, monkeypatch) -> None:
    """三格互不相交且并集是整份目录 —— 这个性质由 ``ToolBacking`` 保证，这一行只是搬。

    少了「并集是整份目录」这半条，一个「谁都没接住」的名字会从三格里一起漏掉，
    而它的表现是自检说一切就绪。
    """
    report = _report(monkeypatch, MARKETOPS_DATA_ROOT=str(tmp_path / "state"))
    code = next(row for row in report.rows if row.check == "code")
    facts = code.facts
    assert (
        facts["backed"] + facts["not_registered"] + facts["stubbed"] == facts["catalog_total"]
    ), f"三格没盖满目录：{facts}"
    # 相位直方图也是那个三分的一层细分，所以它也要盖满 ``stubbed`` —— 这一条在
    # 「一条都没未接线」时同样成立（两边都是 0），因此它不随接线进度变红。
    assert sum(facts["stubbed_phases"].values()) == facts["stubbed"], (
        f"未接线的条数与相位直方图对不上：{facts}"
    )
    assert set(facts["stubbed_phases"]) <= {spec.phase for spec in TOOL_CATALOG.values()}, (
        f"相位直方图里出现了目录之外的相位名：{facts['stubbed_phases']}"
    )


def test_the_code_row_follows_the_stub_count(tmp_path, monkeypatch) -> None:
    """判据是 ``stubbed`` 是不是空，**不是** ``backed`` 多不多。

    ``not_registered`` 那一格是**本部署明确裁掉**的能力（今天只有外发那几条），
    它有自己的行、也有自己的处置。把它算成「没接线」会让一份配置不了外发的部署
    拿到 ``BLOCKED_CODE``，而它该去动的是部署，不是接线。
    """
    report = _report(monkeypatch, MARKETOPS_DATA_ROOT=str(tmp_path / "state"))
    code = next(row for row in report.rows if row.check == "code")
    expected = PREFLIGHT_BLOCKED_CODE if code.facts["stubbed"] else PREFLIGHT_READY
    assert code.status == expected, (
        f"未接线 {code.facts['stubbed']} 条、裁掉 {code.facts['not_registered']} 条，"
        f"判词却是 {code.status}"
    )


# ----------------------------------------------------------------------
# budget 那一行：它答的上界在哪
# ----------------------------------------------------------------------
def test_the_budget_row_reports_the_declared_ceilings(tmp_path, monkeypatch) -> None:
    """两个数字都来自冻结的 profile，而这一行**不**回答「够不够」。

    答「够不够」需要一条业务判断（这一轮批多少），而 §29 那条「不要替 Member-A
    批准预算」在这里的形状就是这样：自检把上限报出来，选多少是人做的事。
    """
    report = _report(monkeypatch, MARKETOPS_DATA_ROOT=str(tmp_path / "state"))
    budget = next(row for row in report.rows if row.check == "budget")
    assert budget.status == PREFLIGHT_READY
    assert budget.facts["search_max_queries"] > 0
    assert budget.facts["fetch_max_documents"] > 0
    assert budget.facts["profile_id"] == "uk-acme-natural-grass"


def test_a_profile_without_a_declared_ceiling_is_a_configuration_blocker(
    tmp_path, monkeypatch
) -> None:
    """没有声明上限 ⇒ ``BLOCKED_CONFIG``，而**不是** ``provider`` 那一行的缺凭据。

    两件事的处置相反：额度声明在 profile 里（版本化、可评审、本机就能改），
    凭据在别人的手里。合成一句话会让读的人去找错的人。
    """
    from marketops.domain.errors import BudgetNotConfiguredError
    from marketops.interfaces.mcp import __main__ as entry

    def _refuse(_bundle):
        raise BudgetNotConfiguredError("profile 'x' 没有声明 search.max_queries")

    # 打在**组合根**那一份名字上，不是 ``application.budget`` 上：组合根是
    # ``from ...application.budget import ...`` 把这两个函数**取进自己的命名空间**的，
    # 打在被取的那一侧对调用点没有任何影响 —— 而那种打错了的替身会让这一条**照绿**
    # 而它守着的那个分支一次都没跑到。
    monkeypatch.setattr(entry, "declared_search_ceiling", _refuse)
    report = _report(monkeypatch, MARKETOPS_DATA_ROOT=str(tmp_path / "state"))
    budget = next(row for row in report.rows if row.check == "budget")
    assert budget.status == PREFLIGHT_BLOCKED_CONFIG
    assert "search.max_queries" in budget.detail
    # 后面那一行是**没问到**，不是「也已经答过了」—— 判据是 ``facts`` 空。
    code = next(row for row in report.rows if row.check == "code")
    assert code.status == PREFLIGHT_BLOCKED_CONFIG
    assert code.facts == {}


# ----------------------------------------------------------------------
# provider 那一行：飞书**报出来**，但不由它判词（§20）
# ----------------------------------------------------------------------
#: 名字里**不带** ``MARKETOPS_`` 前缀的那几格。``_hermetic`` 只清 ``MARKETOPS_*``
#: （它自己那一段逐字说了为什么必须清），而下面两条判据问的是「飞书配了没有」。
#: 这台机器上真有一个 ``FEISHU_APP_ID`` 的话，那两条测的就成了「谁在跑它」。
_UNPREFIXED_CREDENTIALS = (
    "SERPER_API_KEY",
    "TYPESAFE_API_KEY",
    "LAYA_CHECKPOINT_DIR",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "FEISHU_BITABLE_APP_TOKEN",
    "FEISHU_BITABLE_TABLE_ID",
)


def _clear_unprefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _UNPREFIXED_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)


def _provider_row(report: PreflightReport) -> PreflightRow:
    return next(row for row in report.rows if row.check == "provider")


def _executor(tmp_path: Path) -> dict[str, str]:
    """执行器三样，**给真文件**。

    第一版这里给的是三条路径字符串，两条判据于是在一个空 ``facts`` 上断言：
    组合根在装载期判 ``cli_path`` 是否存在（``ConfigError: cli_path 不存在``），
    不存在时装配停在 ``auth`` 那一行，后面的行是「没有问到」而不是「答过了」。
    这一段的形状因此与 ``REPO_ROOT`` 下真实的那三样一致：入口是一个真文件。
    """
    cli = tmp_path / "quay.js"
    node = tmp_path / "node"
    cli.write_text("", encoding="utf-8")
    node.write_text("", encoding="utf-8")
    return {
        "MARKETOPS_QUAY_CLI": str(cli),
        "MARKETOPS_QUAY_NODE": str(node),
        "MARKETOPS_QUAY_ROOT": str(tmp_path),
    }



def test_feishu_missing_is_reported_and_never_drives_the_verdict(tmp_path, monkeypatch) -> None:
    """检索与执行器都在、飞书不在 ⇒ 这一行仍是 ``READY``，而 ``feishu`` 那一格是 ``False``。

    两半都要判，缺任何一半这条判据都会变成一条假的绿灯：

    * ``READY``：§20 把飞书定成**核心试运行的阻塞项之外**。让它的存在性去驱动判词，
      这一条就永远出不来 ``READY``，而「永远出不来」在报告上与「这个部署还不合格」
      长得一模一样。
    * ``feishu`` 那一格**在** ``facts`` 里且为 ``False``：删掉它才是相反的错 ——
      缺口从报告里消失，而「我们不报它」与「它是好的」在读的人眼里是同一行。

    执行器三样按 ``_executor`` 造出来（入口是真文件），而**不启动它** —— 这一行问的是
    「配了没有」，``docs/MULTI_SESSION_PLUGIN_AND_NOTIFICATION_ARCHITECTURE.md`` 之外
    任何一处都不该由一次自检顺手派发一件工作。
    """
    _clear_unprefixed(monkeypatch)
    report = _report(
        monkeypatch,
        MARKETOPS_DATA_ROOT=str(tmp_path / "state"),
        SERPER_API_KEY="sentinel-serper-value-9f2a",
        **_executor(tmp_path),
    )
    provider = _provider_row(report)
    assert provider.facts["feishu"] is False
    assert provider.facts["blocking"] == []
    assert provider.status == PREFLIGHT_READY


def test_serper_missing_blocks_and_feishu_is_not_added_to_that_list(tmp_path, monkeypatch) -> None:
    """检索提供方缺 ⇒ ``BLOCKED_EXTERNAL``，而**那个缺的清单里没有飞书**。

    两条判词把读的人送到两个地方：``serper`` 那一格是「去要一把钥匙」，
    飞书那一格今天是「这条路径还不存在」。合成一句会让后一件事看起来只是前者。
    """
    _clear_unprefixed(monkeypatch)
    report = _report(
        monkeypatch,
        MARKETOPS_DATA_ROOT=str(tmp_path / "state"),
        **_executor(tmp_path),
    )
    provider = _provider_row(report)
    assert provider.status == PREFLIGHT_BLOCKED_EXTERNAL
    assert provider.facts["blocking"] == ["serper"]
    assert provider.facts["feishu"] is False
    # 对照：缺的确实是 serper 而不是「一个都没配」—— 否则上面那条清单为空也成立。
    assert provider.facts["quay"] is True

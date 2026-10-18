"""``read-motherpool``：把一份母池工件读成回执，并从 CLI 说出下一步。

## 为什么这条子命令必须有测试

它是 §2 E1 那条链在**运维这一侧**的入口：上游交进来一份导出 → 读成回执 →
回执的 id 就是 ``submit_research`` 的 ``input_ref``。这条链上任何一环断了，
表现都不是一次报错，而是一次**看起来跑完了**的运行。所以这里钉住的是三件事：

1. **回执真的落了盘**，而且落的就是 ``inputs_dir`` 下那个名字。
2. **读数如实打印**，包括「这一份不能直接拿去受理」那一条 —— 整池都是
   ``pending`` 与「读入失败」在报告里长得一样，除非有人把它说出来。
3. **退出码**：``0`` 含「一行都不可研究」，``2`` 是参数/配置不成立。

第 3 条与 ``reconcile`` 对 NEEDS_OPERATOR 的态度逐字一致：一个把「有东西要人看」
退成非零的实现，会让自动化在**该看报告**时以为**工具坏了**，而两种处置相反。

## ``$(...)`` 与「干净的一行」在这里不适用

``read-motherpool`` 打的是给人读的 JSON（与 ``reconcile`` 同类），不是
``mint-token`` 那种被 ``$( )`` 吃的单行输出。所以这里不钉「stdout 只有一行」——
那是另一条命令的判据，照抄过来会把一条错误的约束固定进测试。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketops.config import DEFAULT_PROFILE, Settings
from marketops.interfaces.cli import main

#: 一份**诚实**的母池：按对象类型编号的导出，常态长相。
#:
#: 里面有只有代号的、重复的、同名不同地点的、缺域名的 —— 也就是「不要求人工
#: 先整理成完整 Account」这句话要覆盖的那几种。取中性名字：``marketops/``
#: 与 ``tests/`` 的扫描判据都不许出现市场词，而这份 fixture 不是市场数据。
_POOL = "".join(
    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
    for row in [
        {
            "type": "node",
            "id": "1001",
            "name": "Example Riverside Club",
            "town": "Exeter",
            "website": "https://www.riverside.example",
        },
        {"type": "node", "id": "1002"},
        {"type": "node", "id": "1001", "name": "Example Riverside Club (again)"},
        {"type": "node", "id": "1003", "name": "Example Riverside Club", "town": "Bristol"},
    ]
)


@pytest.fixture
def deployed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """一个装好的部署：``inputs_dir`` 已建好，工件已就位。

    ``MARKETOPS_DATA_ROOT`` 是 ``load_settings`` 唯一的入口，而 CLI 走的是它 ——
    所以必须经由环境变量，不能把 ``Settings`` 直接塞进 ``main``（那会绕过被测的
    那一段装载逻辑）。
    """
    data_root = tmp_path / "runtime"
    monkeypatch.setenv("MARKETOPS_DATA_ROOT", str(data_root))
    monkeypatch.delenv("MARKETOPS_PROFILE", raising=False)
    # 这个夹具的**唯一**用途是准备好 ``inputs_dir``（CLI 自己走 ``load_settings``）。
    # 所以它构造的 Settings 不需要与 CLI 拿到的那个是同一个对象 —— 要的是同一个
    # ``data_root``，而那一个从左边的环境变量来。
    resolved = Settings(
        data_root=data_root,
        profiles_dir=Path(__file__).resolve().parents[1] / "profiles",
        profile=DEFAULT_PROFILE,
    )
    resolved.ensure_data_dirs()
    (resolved.inputs_dir / "pool-2026.jsonl").write_text(_POOL, encoding="utf-8", newline="\n")
    return resolved


def _run(*argv: str) -> int:
    return main(["read-motherpool", *argv])


def _receipts(settings: Settings) -> list[Path]:
    return sorted(settings.inputs_dir.glob("*.receipt.json"))


# --- 正常路径 -----------------------------------------------------------------


def test_a_pool_reads_and_the_receipt_lands_in_inputs_dir(deployed, capsys) -> None:
    code = _run("--artifact", "pool-2026", "--batch", "batch-01", "--profile", "some-profile")

    assert code == 0
    written = _receipts(deployed)
    assert len(written) == 1
    assert written[0].parent == deployed.inputs_dir

    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["artifact_ref"] == "pool-2026"
    assert payload["source_method_id"] == "import.register.v1"
    assert payload["batch_id"] == "batch-01"
    assert payload["profile_id"] == "some-profile"
    assert payload["raw_row_count"] == 4
    # stdout 是那份回执本身（给人读的），所以从它就能读出读数。
    assert json.loads(capsys.readouterr().out.split("\n读数：")[0])["receipt_id"] == payload["receipt_id"]


def test_the_next_step_names_the_receipt_id_as_the_input_ref(deployed, capsys) -> None:
    """下一条命令要用的字符串必须**自己说出来**。

    这一格是整条链的接缝：回执落盘之后，受理那一步收的是一个 ``input_ref``，
    而它就是回执 id。让运维自己去文件里翻那一行，等于把一个可以自动说出来的
    事实变成一次可能抄错的复制。
    """
    _run("--artifact", "pool-2026")
    receipt_id = json.loads(_receipts(deployed)[0].read_text(encoding="utf-8"))["receipt_id"]

    err = capsys.readouterr().err
    assert "submit_research" in err
    assert "source_receipt" in err
    assert receipt_id in err


def test_the_counts_line_says_every_disposition(deployed, capsys) -> None:
    """读数逐项列出，而不是一句「成功 N 行」。

    ``pending`` 不是失败，但它也绝不是产出 —— 它是一次**还没发生**的补全。
    把这两种混成一个数，读的人只会相信对自己方便的那一半。
    """
    _run("--artifact", "pool-2026")

    err = capsys.readouterr().err
    assert "raw=4" in err
    # resolved=2：同名不同地点的那两条**各自**是一个主体（回执刻意不合并它们）。
    # 只在名字上归并会把这个数读成 1，而丢掉的那一条在报告里只剩一个 ``merged``。
    assert "resolved=2" in err
    assert "merged=1" in err
    assert "excluded=0" in err
    assert "pending=1" in err
    assert "failed=0" in err


def test_a_replay_writes_no_second_receipt(deployed) -> None:
    """重放幂等 —— 盘上仍是一份回执。"""
    assert _run("--artifact", "pool-2026", "--batch", "batch-01") == 0
    assert _run("--artifact", "pool-2026", "--batch", "batch-01") == 0

    assert len(_receipts(deployed)) == 1


def test_a_declared_exclusion_is_accepted_and_recorded(deployed, capsys) -> None:
    """``--exclusion`` 是这份 profile **已声明**的那几项。"""
    (deployed.inputs_dir / "pool-2026.jsonl").write_text(
        _POOL + json.dumps({"type": "node", "id": "1009", "name": "Somewhere", "exclusions": "exclusion-a"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    assert _run("--artifact", "pool-2026", "--exclusion", "exclusion-a") == 0

    payload = json.loads(_receipts(deployed)[0].read_text(encoding="utf-8"))
    excluded = [row for row in payload["dispositions"] if row["disposition"] == "excluded"]
    assert [row["exclusion_ids"] for row in excluded] == [["exclusion-a"]]
    assert "excluded=1" in capsys.readouterr().err


# --- 如实说话 -----------------------------------------------------------------


def test_a_pool_with_nothing_resolvable_says_so_and_still_exits_zero(
    deployed, capsys
) -> None:
    """整池只有代号：回执落盘、**大声说它不能直接拿去受理**、退出码仍是 0。

    退出码这一格是刻意的：回执是一个如实的读数（每一行都有处置、``blocker``
    说着缺什么），不是这次调用失败。让它在非零上退出，会让自动化在「有东西要人看」
    时以为「这个工具坏了」—— 而两种处置相反。
    """
    (deployed.inputs_dir / "thin.jsonl").write_text(
        '{"type": "node", "id": "1"}\n{"type": "node", "id": "2"}\n',
        encoding="utf-8",
        newline="\n",
    )

    assert _run("--artifact", "thin") == 0

    err = capsys.readouterr().err
    assert "不能" in err and "受理研究" in err
    assert "身份补全" in err
    assert "submit_research" not in err, "不能一边说受理不了、一边给出下一步的受理命令"
    assert len(_receipts(deployed)) == 1


# --- 参数与配置不成立：退出码 2 ---------------------------------------------------


def test_a_missing_artifact_exits_two(deployed, capsys) -> None:
    """工件不存在 → 2，而且消息里说得出找过哪几个名字。"""
    assert _run("--artifact", "not-here") == 2

    err = capsys.readouterr().err
    assert "配置错误" in err
    assert "not-here.jsonl" in err


def test_a_name_that_needs_normalizing_exits_two(deployed, capsys) -> None:
    """名字会变成文件名 —— 需要规范化的名字是**调用方给错了**，不是部署事实。"""
    assert _run("--artifact", "Pool 2026") == 2

    assert "配置错误" in capsys.readouterr().err


def test_an_undeclared_exclusion_exits_two(deployed, capsys) -> None:
    """来源行命中了没声明过的排除项 → **整份拒绝**，一个文件都不写。

    收下它等于让输入发明一条 profile 从没声明过的规则，而那条规则会静默地从
    池子里删行、报告上只显示 ``excluded=1``。
    """
    (deployed.inputs_dir / "pool-2026.jsonl").write_text(
        json.dumps({"type": "node", "id": "9", "name": "Somewhere", "exclusions": "exclusion-z"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    assert _run("--artifact", "pool-2026", "--exclusion", "exclusion-a") == 2

    err = capsys.readouterr().err
    assert "未声明的排除项" in err
    assert "exclusion-a" in err, "拒绝的话要说得出这一份**声明过**什么"
    assert _receipts(deployed) == [], "整份拒绝意味着盘上不该出现任何回执"


def test_an_unregistered_source_method_exits_two(deployed, capsys) -> None:
    """来源方法是闭集 —— 失败关闭，而不是接受一个自由文本。"""
    assert _run("--artifact", "pool-2026", "--source-method", "made.up.v1") == 2

    assert "未知的来源方法" in capsys.readouterr().err

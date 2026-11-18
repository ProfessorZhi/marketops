"""A6000 交接清单与交接文档必须**可被判据验**，而不是靠人记得更新。

``benchmark/local-runtime/SYNC_MANIFEST.json`` 是一份声明：「这几个数据集哈希、
这几个文档、这几个 artifacts 还缺」。声明会漂：数据集改了一行、artifacts 取回来了，
清单本身不会自己知道 —— 而它读起来仍然完全正常。

所以这里判四件事：

1. 清单里写的数据集哈希与盘上的字节一致（漂了就红）；
2. 清单点名的每个文件都真的在盘上（路径写错会让第 1 条**静默**变成恒真）；
3. ``missing_artifacts`` 里的每个文件都**确实不在**仓库里 —— 取回之后必须更新清单，
   而不是留下一句「待取得」在那里骗下一个人；
4. ``HANDOFF.md`` 的十节都在，且两份新文件里没有密钥形态的字符串。

第 5 条是判据自证：给一个错的哈希，第 1 条必须真的红。否则「哈希对得上」可能只是
因为比较的两边都没在算。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_RUNTIME = REPO_ROOT / "benchmark" / "local-runtime"
MANIFEST_PATH = LOCAL_RUNTIME / "SYNC_MANIFEST.json"
HANDOFF_PATH = LOCAL_RUNTIME / "HANDOFF.md"

#: ``HANDOFF.md`` 必须包含的十节（任务书逐字要求）。
REQUIRED_HANDOFF_SECTIONS: tuple[str, ...] = (
    "项目目标",
    "当前 GitHub 基线",
    "已有测试集规模",
    "已有真实 Pilot 结果",
    "目前未解决的问题",
    "A6000 后续工作",
    "Crawl4AI Local 部署要求",
    "MinerU Local GPU 部署要求",
    "迭代测试集的规则",
    "如何运行现有 Benchmark",
)

#: 密钥形态。只扫这两份新文件 —— 全仓扫描是 ``.gitleaks.toml`` 的职责，
#: 这里挡的是「本次新增的文件里混进了一把 key」。
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _hash_mismatches(manifest: dict) -> list[str]:
    """清单里所有 ``{"sha256": ...}`` 条目与盘上字节的差异。

    清单的结构是「分组 → 仓库相对路径 → {sha256, bytes}」，所以**键本身就是路径**，
    不要在前面再叠一层分组名。
    """
    problems: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, dict) and isinstance(value.get("sha256"), str):
                    target = REPO_ROOT / key
                    if not target.is_file():
                        problems.append(f"{key}: 清单点名但不在盘上")
                    elif _sha256(target) != value["sha256"]:
                        problems.append(f"{key}: 字节与清单哈希不一致")
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(manifest)
    return problems


# --------------------------------------------------------------------------------------
# 本体
# --------------------------------------------------------------------------------------


def test_the_manifest_parses_and_has_a_version() -> None:
    data = _manifest()
    assert data.get("schema_version") == 1, f"未知的清单版本：{data.get('schema_version')!r}"


def test_every_recorded_hash_matches_the_bytes_on_disk() -> None:
    """数据集或文档改了却没更新清单 —— 这正是这份文件最容易失效的方式。"""
    problems = _hash_mismatches(_manifest())
    assert not problems, "清单与盘上内容不一致：\n" + "\n".join(f"  - {p}" for p in problems)


def test_missing_artifacts_are_really_absent_from_the_repository() -> None:
    """取回 artifacts 之后必须更新清单，不能留一句「待取得」骗下一个人。

    这是**不变量**而不是一次性检查：文件一旦进仓，这条就会红，提示「把清单改对」。
    """
    data = _manifest()
    listed = data["missing_artifacts"]["files"]
    present = [name for name in listed if any(REPO_ROOT.rglob(name))]
    assert not present, (
        "这些 artifacts 已经在仓库里了，但清单仍标为缺失 —— 请更新 "
        f"missing_artifacts：{present}"
    )


def test_the_missing_artifact_count_matches_the_list() -> None:
    data = _manifest()
    listed = data["missing_artifacts"]["files"]
    assert data["missing_artifacts"]["count"] == len(listed), (
        "count 与 files 长度不一致："
        f"{data['missing_artifacts']['count']} != {len(listed)}"
    )
    assert len(set(listed)) == len(listed), "missing_artifacts 里有重复项"


def test_the_pilot_run_is_recorded_as_a_non_conclusion() -> None:
    """Pilot 跑过 ≠ 可以拿来选型。这一格是给下一个人看的刹车。"""
    run = _manifest()["pilot_run"]
    assert run["total_calls"] == run["search_calls"] + run["fetch_calls"] + run["e2e_calls"]
    assert run["total_calls"] == 180
    assert run["gold_status_at_run"] == "all_draft"
    assert run["precondition_met"] is False
    assert run["is_selection_conclusion"] is False


def test_handoff_document_has_every_required_section() -> None:
    text = HANDOFF_PATH.read_text(encoding="utf-8")
    headings = re.findall(r"^## (\d+)\. (.+)$", text, flags=re.MULTILINE)
    numbers = [int(number) for number, _ in headings]
    assert numbers == list(range(1, 11)), f"章节编号不是 1..10：{numbers}"
    titles = [title.strip() for _, title in headings]
    for required in REQUIRED_HANDOFF_SECTIONS:
        assert any(required in title for title in titles), f"HANDOFF.md 缺章节：{required}"


def test_the_synced_files_carry_no_credential_shaped_strings() -> None:
    for path in (HANDOFF_PATH, MANIFEST_PATH):
        text = path.read_text(encoding="utf-8")
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            assert match is None, f"{path.name} 里出现密钥形态的字符串：{match.group(0)[:12]}…"


# --------------------------------------------------------------------------------------
# 判据自证
# --------------------------------------------------------------------------------------


def test_the_hash_check_can_actually_turn_red() -> None:
    """给一个假的哈希，比较器必须真的响。

    否则「哈希都对得上」可能只是因为两边都没在算。
    """
    data = _manifest()
    first_doc = next(iter(data["included"]["documents"]))
    poisoned = json.loads(json.dumps(data))
    poisoned["included"]["documents"][first_doc] = {"sha256": "0" * 64}
    problems = _hash_mismatches(poisoned)
    assert problems, "投毒后的清单仍然被判为一致 —— 这个比较器没有在看哈希"


def test_a_missing_file_is_reported_not_skipped() -> None:
    """路径写错时必须报「不在盘上」，而不是静默跳过（跳过 = 恒真）。"""
    poisoned = {
        "included": {"documents": {"benchmark/definitely-not-here.md": {"sha256": "0" * 64}}}
    }
    problems = _hash_mismatches(poisoned)
    assert any("不在盘上" in problem for problem in problems), problems


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

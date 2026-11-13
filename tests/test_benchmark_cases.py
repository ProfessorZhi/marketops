"""数据集本身是可校验的产物。

三条性质，每一条对应一个会静默毁掉结论的失效：

* **schema** —— 一条用例少一个字段，评分器会把它当 ``null`` 算，而不是当错误；
* **split 互斥且不重叠** —— 同一个 case_id 出现在两个 split 里，「holdout 表现好」
  可能只是记住了 development；
* **校验和** —— 数据集一改，旧分数必须自动失效。校验和里含文件名，因此改名也算改。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.runner.models import (
    Dataset,
    DatasetError,
    _structural_check,
    dataset_checksum,
    load_cases,
    load_dataset,
    validate_case,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PILOT = REPO_ROOT / "benchmark" / "datasets" / "pilot"


def test_the_pilot_dataset_has_the_promised_shape() -> None:
    ds = load_dataset(PILOT, validate=True)
    assert ds.counts == {"search": 24, "fetch": 28, "e2e": 10}
    assert ds.duplicate_ids() == []


def test_every_split_has_cases_and_the_splits_partition_the_dataset() -> None:
    ds = load_dataset(PILOT)
    sizes = {name: sum(ds.split(name).counts.values())
             for name in ("development", "validation", "holdout")}
    assert all(size > 0 for size in sizes.values())
    assert sum(sizes.values()) == sum(ds.counts.values())


def test_no_case_id_appears_in_two_splits() -> None:
    """金标层面更强的那条性质（同一实体不跨 split）无法机器判定，但 id 必须唯一。"""
    ds = load_dataset(PILOT)
    seen: dict[str, str] = {}
    for name in ("development", "validation", "holdout"):
        for case in ds.split(name).search + ds.split(name).fetch + ds.split(name).e2e:
            assert case["case_id"] not in seen, f"{case['case_id']} 跨了 split"
            seen[case["case_id"]] = name


def test_the_pilot_is_all_draft_because_no_human_has_confirmed_it() -> None:
    """pilot 的 62 条全部是 ``draft``。这是事实，也是它不能出结论的原因。

    这条判据会在有人手工升了一部分之后变红 —— 那时应当改成「至少有一条
    ``human_verified``」，而不是删掉它。
    """
    ds = load_dataset(PILOT)
    statuses = {c["label_status"] for layer in ("search", "fetch", "e2e")
                for c in getattr(ds, layer)}
    assert statuses == {"draft"}


def test_the_checksum_is_stable_across_loads() -> None:
    assert load_dataset(PILOT).checksum == load_dataset(PILOT).checksum


def test_the_checksum_changes_when_a_case_changes(tmp_path: Path) -> None:
    """不变量：数据集一改，任何旧计分卡都不可比。"""
    src = PILOT / "search" / "pilot.jsonl"
    dst = tmp_path / "search"
    dst.mkdir()
    (dst / "pilot.jsonl").write_bytes(src.read_bytes())
    before = dataset_checksum([dst / "pilot.jsonl"])
    text = src.read_text(encoding="utf-8").replace('"top_k": 10', '"top_k": 5', 1)
    (dst / "pilot.jsonl").write_text(text, encoding="utf-8", newline="\n")
    assert dataset_checksum([dst / "pilot.jsonl"]) != before


def test_renaming_a_case_file_changes_the_checksum(tmp_path: Path) -> None:
    """文件名进哈希：否则「同一份内容换了文件名」会被当成同一次运行。"""
    src = PILOT / "search" / "pilot.jsonl"
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_bytes(src.read_bytes())
    b.write_bytes(src.read_bytes())
    assert dataset_checksum([a]) != dataset_checksum([b])


def test_a_case_missing_a_required_field_is_rejected() -> None:
    case = json.loads((PILOT / "search" / "pilot.jsonl").read_text(encoding="utf-8").splitlines()[0])
    broken = dict(case)
    broken.pop("gold_label")
    with pytest.raises(DatasetError, match="gold_label"):
        validate_case(broken, "search")


def test_an_illegal_enum_is_rejected() -> None:
    case = json.loads((PILOT / "fetch" / "pilot.jsonl").read_text(encoding="utf-8").splitlines()[0])
    broken = dict(case, task_type="not_a_task_type")
    with pytest.raises(DatasetError, match="task_type"):
        validate_case(broken, "fetch")


def test_the_structural_fallback_catches_the_same_class_of_errors() -> None:
    """没有 ``jsonschema`` 的环境里也必须红，否则「离线可用」是一句空话。

    直接调用退化的那一支 —— 这正是 ``import jsonschema`` 失败时走的路。
    """
    schema = json.loads(
        (REPO_ROOT / "benchmark" / "schemas" / "search-case.schema.json").read_text(encoding="utf-8")
    )
    case = json.loads((PILOT / "search" / "pilot.jsonl").read_text(encoding="utf-8").splitlines()[0])
    _structural_check(case, schema)  # 合法的必须通过
    # 期望值必须取自 schema 自己的 ``const``：曾经这里用的是包级 SCHEMA_VERSION
    # （``marketops.benchmark.v1``），与用例的 ``…case.v1`` 不是一回事，于是每一条
    # 合法用例在没装 jsonschema 的机器上都被拒。
    with pytest.raises(DatasetError, match="marketops.benchmark.case.v1"):
        _structural_check(dict(case, schema_version="wrong"), schema)
    with pytest.raises(DatasetError):
        _structural_check({k: v for k, v in case.items() if k != "query"}, schema)


def test_a_corrupt_line_names_itself(tmp_path: Path) -> None:
    """第 800 行坏了不能连累前 799 行 —— 但错误必须指出是哪一行。"""
    p = tmp_path / "search" / "broken.jsonl"
    p.parent.mkdir()
    p.write_text('{"case_id": "ok"}\n{not json}\n', encoding="utf-8", newline="\n")
    with pytest.raises(DatasetError, match="broken.jsonl:2"):
        load_cases(p)


def test_comments_and_blank_lines_are_skipped(tmp_path: Path) -> None:
    """JSONL 会被手工编辑好几个月，留一行注释是刚需。"""
    p = tmp_path / "search" / "c.jsonl"
    p.parent.mkdir()
    p.write_text('# note\n\n{"case_id": "a"}\n', encoding="utf-8", newline="\n")
    assert load_cases(p) == [{"case_id": "a"}]


def test_split_returns_a_dataset_whose_files_are_unchanged() -> None:
    """split 只筛用例，不改身份 —— 校验和仍然属于整份数据集。"""
    ds = load_dataset(PILOT)
    dev = ds.split("development")
    assert isinstance(dev, Dataset)
    assert dev.checksum == ds.checksum
    assert dev.counts["search"] < ds.counts["search"]


def test_label_status_is_counted_not_assumed() -> None:
    """计分卡要能自证「这份金标是 draft 还是人确认过」。

    全部按 draft 计数而不是留空：空字典读起来像「没有状态这回事」，而 pilot 现在
    恰恰是**全部未确认** —— 这正是那些数字还不能用的原因。
    """
    ds = load_dataset(PILOT)
    assert ds.label_status() == {"draft": 62}
    assert sum(ds.label_status().values()) == sum(ds.counts.values())


def test_label_status_counts_mixed_states(tmp_path: Path) -> None:
    """升级一条就会变成两格；这里钉住的是「按字段数，不按层数」。"""
    for layer, body in (
        ("search", '{"case_id": "s1", "label_status": "draft"}\n'
                   '{"case_id": "s2", "label_status": "human_verified"}\n'),
        ("fetch", '{"case_id": "f1", "label_status": "disputed"}\n'),
        ("e2e", '{"case_id": "e1", "label_status": "human_verified"}\n'),
    ):
        p = tmp_path / layer / "c.jsonl"
        p.parent.mkdir()
        p.write_text(body, encoding="utf-8", newline="\n")
    assert load_dataset(tmp_path).label_status() == {
        "disputed": 1, "draft": 1, "human_verified": 2
    }

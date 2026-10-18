"""``marketops/application/motherpool.py`` —— 母池读入的那条路径。

这里有三种测试，各自防的东西不一样：

1. **形状**：工件找不找得到、读不读得出来、一行读坏了会怎样。
2. **判定**：一行最后去了哪里 —— ``resolved`` / ``merged`` / ``excluded`` /
   ``pending`` / ``failed``，以及**为什么**是那一个。
3. **重放**：同一份工件读两次，盘上不出现第二条回执。

第 2 类里最要紧的两条是**否定的**，写成它们自己的测试：

* 名字与地点都一样的**两行不合并在**一起（见
  :func:`test_same_name_and_location_are_not_merged`）——
  「看起来像就是同一个」是这一层拿不出证据的判断。
* 一个裸 ``id`` 列**不构成**来源标识（见
  :func:`test_a_bare_id_column_is_not_a_source_identity`）——
  它可能是行号，也可能是类别码；拿它当归并键会把五十行合成一行，
  而报告上只显示 ``merged=49``。

这两条如果哪天被改掉，报告仍然全绿 —— 只是它会开始少报行。所以它们值得
自己一个测试名，而不是混在一条大断言里。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from marketops.application.motherpool import (
    ARTIFACT_SUFFIXES,
    RECEIPT_SUFFIX,
    candidate_from,
    dispositions_from,
    find_artifact,
    flatten,
    load_receipt,
    read_artifact,
    read_motherpool,
    receipt_path,
    usable_name,
    write_receipt,
)
from marketops.domain.errors import ConfigError
from marketops.domain.receipt import Disposition, verify_receipt_id

_CREATED_AT = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)

#: 只在这一个测试文件里出现的排除项 id。
#:
#: **不是**市场词表，是一份 profile 会声明的形状 —— ``marketops/`` 整包受
#: ``tests/test_no_hardcoded_market_knowledge.py`` 管，而那份词表在 profile 一侧。
#: 这里要验的是「未声明的排除项 id 会整份拒绝」，所以得有两个**假**的 id 才能
#: 区分「声明过」与「没声明过」。取中性名字，免得读的人以为引擎认识它们。
_DECLARED = ("exclusion-a", "exclusion-b")


def _write(tmp_path: Path, name: str, body: str, *, suffix: str = ".jsonl") -> Path:
    path = tmp_path / f"{name}{suffix}"
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def _jsonl(tmp_path: Path, rows: list[dict[str, object]], *, name: str = "pool") -> Path:
    body = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    return _write(tmp_path, name, body)


def _read(tmp_path: Path, name: str = "pool", **overrides: object):
    kwargs: dict[str, object] = {
        "source_method_id": "import.register.v1",
        "batch_id": "batch-01",
        "declared_exclusion_ids": _DECLARED,
        "profile_id": "some-profile",
        "created_at": _CREATED_AT,
    }
    kwargs.update(overrides)
    return read_motherpool(tmp_path, name, **kwargs)  # type: ignore[arg-type]


def _counts(receipt) -> dict[str, int]:
    """按处置分类的行数。

    ``raw`` **不在**这里 —— 它不是一种处置，它是分母（``receipt.raw_row_count``）。
    把它混进这个字典会让「哪一个数是行数、哪一个是去向」在断言里看不出区别。
    """
    return dict(receipt.counts)


def _disposition_counts(resolved=0, merged=0, excluded=0, pending=0, failed=0) -> dict[str, int]:
    return {
        "resolved": resolved,
        "merged": merged,
        "excluded": excluded,
        "pending": pending,
        "failed": failed,
    }


# --- 找工件 -------------------------------------------------------------------


def test_artifact_is_found_by_name_not_by_path(tmp_path: Path) -> None:
    """收的是**名字**，不是路径。

    一个收路径的入口等于让调用方决定服务去读哪一份文件 —— 那是一次越权读盘。
    这里同时钉住「后缀是猜的」：调用方不必知道它是 ``.csv`` 还是 ``.jsonl``。
    """
    for suffix in ARTIFACT_SUFFIXES:
        target = tmp_path / f"pool{suffix}"
        if target.exists():
            target.unlink()
    _write(tmp_path, "pool", "name\nVenue A\n", suffix=".csv")
    assert find_artifact(tmp_path, "pool").name == "pool.csv"


def test_no_artifact_raises_and_says_what_it_tried(tmp_path: Path) -> None:
    """找不到时说清楚试过哪些名字 —— 「文件不在」与「名字写错了」是两回事。"""
    with pytest.raises(ConfigError, match="找不到工件"):
        find_artifact(tmp_path, "pool")


def test_two_artifacts_with_one_name_are_refused(tmp_path: Path) -> None:
    """同名两份是**歧义**，不是「随便挑一份」。

    随便挑的那一份的读数会出现在报告里，而没有人选过它。
    """
    _write(tmp_path, "pool", "name\nVenue A\n", suffix=".csv")
    _jsonl(tmp_path, [{"name": "Venue A"}])
    with pytest.raises(ConfigError, match="不止一份"):
        find_artifact(tmp_path, "pool")


def test_artifact_name_that_needs_rewriting_is_refused(tmp_path: Path) -> None:
    """需要规范化的名字在这里就报错。

    名字会变成文件名，所以两个不同的名字能落到同一个文件上 —— 那时读的是哪一份
    没有答案。拒绝，不替调用方改写。
    """
    with pytest.raises(ValueError):
        find_artifact(tmp_path, "Pool 2026")


# --- 读工件 -------------------------------------------------------------------


def test_jsonl_keeps_file_order_as_ordinal(tmp_path: Path) -> None:
    """顺序决定 ``ordinal``，所以它有意义：重排同一个工件是另一批。"""
    _jsonl(tmp_path, [{"name": "A"}, {"name": "B"}, {"name": "C"}])
    records = read_artifact(tmp_path / "pool.jsonl")
    assert [record.payload["name"] for record in records] == ["A", "B", "C"]


def test_jsonl_blank_lines_are_not_rows(tmp_path: Path) -> None:
    """空行**不是**一行数据。

    把它算进来会让 ``raw_row_count`` 包含排版空行，而那个数会被当下游的分母用。
    这是**跳过**，不是**丢掉**：它从来不是数据，所以它不需要一个处置。
    """
    _write(tmp_path, "pool", '{"name": "A"}\n\n   \n{"name": "B"}\n')
    records = read_artifact(tmp_path / "pool.jsonl")
    assert len(records) == 2


def test_a_broken_jsonl_line_is_one_bad_row_not_a_bad_file(tmp_path: Path) -> None:
    """JSONL 一行坏掉只坏那一行 —— 那种格式本来就是一行一条。"""
    _write(tmp_path, "pool", '{"name": "A"}\n{"name": \n{"name": "C"}\n')
    records = read_artifact(tmp_path / "pool.jsonl")
    assert [record.error != "" for record in records] == [False, True, False]


def test_a_checkbox_list_in_jsonl_is_a_bad_row(tmp_path: Path) -> None:
    """一行是一个列表也不是一行数据 —— 它读不出「哪一列是什么」。"""
    _write(tmp_path, "pool", '["A", "B"]\n')
    records = read_artifact(tmp_path / "pool.jsonl")
    assert len(records) == 1
    assert "不是一个对象" in records[0].error


def test_csv_reads_with_a_bom(tmp_path: Path) -> None:
    """带 BOM 的 CSV 里 ``name`` 那一列**认得出**。

    这是一条真会发生的失败：Excel 与不少导出工具会带 BOM，而用 ``utf-8`` 读时
    第一个表头会变成「U+FEFF 紧跟着 name」—— 于是整池的行都认不出名字、
    全部落进 ``pending``，而报告上看不出发生过什么（每一行都有理由，理由还都对）。
    """
    (tmp_path / "pool.csv").write_bytes("name,osm_type,osm_id\nVenue A,node,1\n".encode("utf-8-sig"))
    receipt = _read(tmp_path)
    assert _counts(receipt) == _disposition_counts(resolved=1)
    assert receipt.dispositions[0].resolved_name == "Venue A"


def test_csv_empty_rows_are_rows(tmp_path: Path) -> None:
    """CSV 里一条记录占一行，所以一个全空的 CSV 行是**一条内容为空的记录**。

    与 JSONL 的空行相反 —— 那里连记录都不是。差别来自格式本身，不是取舍。
    """
    _write(tmp_path, "pool", "name,city\nVenue A,Exeter\n,\n", suffix=".csv")
    assert len(read_artifact(tmp_path / "pool.csv")) == 2


def test_json_document_can_be_a_bare_list(tmp_path: Path) -> None:
    _write(tmp_path, "pool", json.dumps([{"name": "A"}, {"name": "B"}]), suffix=".json")
    assert len(read_artifact(tmp_path / "pool.json")) == 2


def test_json_document_can_wrap_its_rows(tmp_path: Path) -> None:
    """带 ``rows`` 的对象也认 —— 导出工具两种都写。"""
    _write(tmp_path, "pool", json.dumps({"rows": [{"name": "A"}]}), suffix=".json")
    assert len(read_artifact(tmp_path / "pool.json")) == 1


def test_json_document_without_rows_raises(tmp_path: Path) -> None:
    """一个对象既不是列表、也没有 ``rows`` —— 拒绝，不去猜。

    「一个对象就是一行」也说得通，但那时「一份单行的导出」与「一份读错了的
    导出」长得一样，而这两种的处置相反。
    """
    _write(tmp_path, "pool", json.dumps({"name": "A"}), suffix=".json")
    with pytest.raises(ConfigError, match="rows"):
        read_artifact(tmp_path / "pool.json")


def test_a_broken_yaml_document_is_a_bad_file_not_a_bad_row(tmp_path: Path) -> None:
    """整份文档读不出来**不是**「三百行里有一行不行」。

    一份坏掉的 YAML 兜住成三百条 ``failed`` 会把一次解析错误渲染成数据烂。
    """
    _write(tmp_path, "pool", "rows: [unclosed", suffix=".yaml")
    with pytest.raises(ConfigError, match="读不出来"):
        read_artifact(tmp_path / "pool.yaml")


def test_yaml_document_reads(tmp_path: Path) -> None:
    _write(tmp_path, "pool", yaml.safe_dump([{"name": "A"}, {"name": "B"}]), suffix=".yaml")
    assert len(read_artifact(tmp_path / "pool.yaml")) == 2


def test_a_document_row_that_is_not_an_object_is_a_bad_row(tmp_path: Path) -> None:
    """文档格式里一行不是对象是**一行**的问题（它在那一行的位置上）。"""
    _write(tmp_path, "pool", json.dumps([{"name": "A"}, 7]), suffix=".json")
    records = read_artifact(tmp_path / "pool.json")
    assert [record.error != "" for record in records] == [False, True]


def test_an_unknown_suffix_is_refused(tmp_path: Path) -> None:
    _write(tmp_path, "pool", "name\nVenue A\n", suffix=".tsv")
    with pytest.raises(ConfigError, match="不认识的后缀"):
        read_artifact(tmp_path / "pool.tsv")


# --- 摊平 ---------------------------------------------------------------------


def test_nested_columns_are_promoted_one_level() -> None:
    """嵌套一层会被展开，而且**限定名与裸名同时存在**。

    两个不同的嵌套块里有同名格时，盘上看得出这件事 —— 而不是被谁覆盖。
    """
    flat = flatten({"tags": {"name": "Venue A", "sport": "x"}, "name": "outer"})
    assert flat["tags.name"] == "Venue A"
    assert flat["tags.sport"] == "x"
    # 外层同名格优先：外层说 ``outer``，嵌套说 ``Venue A``。
    assert flat["name"] == "outer"


def test_the_outer_column_wins_over_the_nested_one() -> None:
    """外层那一格是上游**明确写在顶层**的值，比嵌套里的更具体。"""
    assert flatten({"tags": {"name": "nested"}, "name": "outer"})["name"] == "outer"


def test_two_nested_blocks_keep_both_qualified_names() -> None:
    """两个嵌套块有同名格时，两个限定名都在 —— 一个都没被覆盖。"""
    flat = flatten({"a": {"name": "from-a"}, "b": {"name": "from-b"}})
    assert flat["a.name"] == "from-a"
    assert flat["b.name"] == "from-b"
    # 裸名是**先出现的那个**，而另一个仍然读得出来（带上它的限定名）。
    assert flat["name"] == "from-a"


def test_scalars_pass_through_verbatim() -> None:
    flat = flatten({"count": 3, "note": "a b", "flag": True, "nothing": None})
    assert flat["count"] == 3
    assert flat["note"] == "a b"
    assert flat["flag"] is True
    assert flat["nothing"] is None


# --- 什么样的一格算名字 ---------------------------------------------------------


def test_an_empty_name_cell_is_not_a_name() -> None:
    assert usable_name("", "node:1") == ""
    assert usable_name("   ", "node:1") == ""


def test_a_name_without_any_letter_is_not_a_name() -> None:
    """``12345`` 与 ``--`` 读不出一个指称对象。"""
    assert usable_name("12345", "") == ""
    assert usable_name("--", "") == ""


def test_a_name_that_is_just_the_identity_is_not_a_name() -> None:
    """以标识回填名称是导出的常态，而那样的「名字」不是指称对象。

    它是同一个标识换了个写法。这条挡的是 2026-09-23 那次事故在母池这一层的
    对应物 —— 那次 worker 拿内部 slug 去检索，9 次查询全落在无关的同名物上。
    这里挡在更前面：这样的一行连 work item 都建不出来。
    """
    assert usable_name("12345", "node:12345") == ""
    assert usable_name("node:12345", "node:12345") == ""
    assert usable_name("NODE:12345", "node:12345") == ""


def test_the_identity_tail_is_recognised_for_a_slash_form_too() -> None:
    """``type/id`` 那一种写法同样认得出来 —— 两种分隔符都要试。

    上游按对象类型编号的那套导出写 ``type/id``，登记册那类写 ``namespace:key``。
    只认其中一种，另一种的标识回填就会**通过**，于是那一行有了一个假名字。
    """
    assert usable_name("12345", "node/12345") == ""
    assert usable_name("osm-node-12345", "node/12345") == "osm-node-12345"


def test_a_real_name_that_merely_contains_digits_is_a_name() -> None:
    """判据是「有没有一个字母」，不是「纯不纯」—— 一个真名字里带数字很常见。"""
    assert usable_name("Pitch 2", "node:1") == "Pitch 2"


def test_a_name_is_stripped() -> None:
    assert usable_name("  Venue A  ", "node:1") == "Venue A"


# --- 一行 → 一个候选 -----------------------------------------------------------


def test_the_type_and_id_pair_becomes_the_source_identity() -> None:
    """「对象类型 + 编号」那一对拼成来源标识。

    这套编号是**上游自己说**的标识，所以它是一等可用的归并键。
    """
    candidate = candidate_from({"type": "node", "id": "12345", "name": "Venue A"}, ordinal=0)
    assert candidate.source_identity == "node:12345"


def test_a_bare_id_column_is_not_a_source_identity() -> None:
    """**裸 ``id`` 不构成来源标识。**

    它是这里最容易做错的一步。一份导出里那个叫 ``id`` 的列可能是主键，
    也可能是行号，还可能是**类别码** —— 五十行共享同一个 ``id: "golf"``
    是完全正常的。拿它当归并键，那五十行会合成一行，而报告上只显示
    ``merged=49``：一条看起来像「去重做得很干净」的读数。
    """
    candidate = candidate_from({"id": "12345", "name": "Venue A"}, ordinal=0)
    assert candidate.source_identity == ""


def test_a_non_object_type_does_not_get_paired_with_the_id() -> None:
    """``type`` 取的不是那套按对象类型编号的词时，不拼标识。

    一个表的 ``type`` 列写着 ``club`` —— 那是一个类别，不是对象的种类。
    拼出来的 ``club:12345`` 会让**每一个同类的对象**共享前缀，而它看起来
    与一条真来源标识一模一样。
    """
    candidate = candidate_from({"type": "club", "id": "12345", "name": "Venue A"}, ordinal=0)
    assert candidate.source_identity == ""


def test_an_explicit_identity_column_wins_over_the_pair() -> None:
    candidate = candidate_from(
        {"source_identity": "reg/abc-1", "type": "node", "id": "9", "name": "A"}, ordinal=0
    )
    assert candidate.source_identity == "reg/abc-1"


def test_an_empty_identity_column_falls_through_to_the_pair() -> None:
    """空的那一格是**缺席**，不是「上游说它没有标识」。"""
    candidate = candidate_from(
        {"source_identity": "", "type": "way", "id": "77", "name": "A"}, ordinal=0
    )
    assert candidate.source_identity == "way:77"


def test_the_original_columns_are_kept_verbatim() -> None:
    """每一列逐字留着 —— 「补齐」那一步将来要回头的原始列都在。

    这里同时看 ``2.0`` → ``2``：导出工具常把编号写成浮点，留着小数点会让
    同一个 id 在两次导出之间变成两个 —— 一次静默的重复。
    """
    candidate = candidate_from(
        {"name": "Venue A", "osm_id": 2.0, "note": "  a b  ", "tags.sport": "x"}, ordinal=0
    )
    assert candidate.row.attributes["osm_id"] == "2"
    assert candidate.row.attributes["note"] == "  a b  "
    assert candidate.row.attributes["name"] == "Venue A"


def test_a_bool_column_reads_as_json_not_as_python() -> None:
    """``True`` 在报告里读起来像代码，而上游写的是 JSON。"""
    candidate = candidate_from({"name": "A", "flag": True}, ordinal=0)
    assert candidate.row.attributes["flag"] == "true"


def test_a_domain_that_does_not_parse_only_empties_that_cell() -> None:
    """解析不出来的域名**不降级这一行**。

    判据是名字，不是那一列。一个真名字的行就是一个可研究的主体；那一列的问题
    只影响那一格。原值留在 ``attributes`` 里，比对得出来。
    """
    candidate = candidate_from({"name": "Venue A", "website": "not a host at all"}, ordinal=0)
    assert candidate.domain == ""
    assert candidate.row.attributes["website"] == "not a host at all"
    assert candidate.name == "Venue A"


def test_a_domain_is_canonicalised_through_the_pairing_rule() -> None:
    """域名走 ``pairing.canonical_host`` —— 配对键与这一格必须是同一条规则。

    自己再写一份会立刻与它漂移，而漂移的那一次表现为「配对不上」，
    不是一次报错。
    """
    candidate = candidate_from({"name": "A", "website": "https://WWW.Example.CO.UK/x?y=1"}, ordinal=0)
    assert candidate.domain == "example.co.uk"


def test_exclusions_can_be_a_list_or_a_separated_string() -> None:
    """上游两种都写：真列表，或一段分号/竖线/逗号分开的文本。"""
    assert candidate_from({"name": "A", "exclusions": ["exclusion-a"]}, ordinal=0).exclusions == (
        "exclusion-a",
    )
    assert candidate_from({"name": "A", "exclusions": "exclusion-a; exclusion-b"}, ordinal=0).exclusions == (
        "exclusion-a",
        "exclusion-b",
    )
    assert candidate_from({"name": "A", "exclusions": "exclusion-b | exclusion-a"}, ordinal=0).exclusions == (
        "exclusion-b",
        "exclusion-a",
    )


def test_an_empty_exclusion_cell_is_absence_not_a_rule() -> None:
    assert candidate_from({"name": "A", "exclusions": ""}, ordinal=0).exclusions == ()
    assert candidate_from({"name": "A", "exclusions": []}, ordinal=0).exclusions == ()


# --- 判去向 -------------------------------------------------------------------


def _candidates(rows: list[dict[str, object]]):
    return [candidate_from(row, ordinal=index) for index, row in enumerate(rows)]


def _dispose(rows: list[dict[str, object]], *, declared=_DECLARED):
    return dispositions_from(
        _candidates(rows), declared_exclusion_ids=frozenset(declared), artifact_ref="pool"
    )


def test_a_row_with_a_name_and_an_identity_is_resolved() -> None:
    (row,) = _dispose([{"type": "node", "id": "1", "name": "Venue A", "town": "Exeter"}])
    assert row.disposition is Disposition.RESOLVED
    assert row.resolved_name == "Venue A"
    assert row.resolved_location == "Exeter"


def test_a_row_with_only_an_identity_is_pending_not_resolved() -> None:
    """这是 Member-C 说的那种行：「纯 osm 只有代码 id」。

    它**不产生 subject** —— 身份补全还没发生，所以一次检索都不该发。
    """
    (row,) = _dispose([{"type": "node", "id": "1"}])
    assert row.disposition is Disposition.PENDING
    assert "身份补全" in row.reason


def test_a_row_whose_name_is_its_own_id_is_pending() -> None:
    (row,) = _dispose([{"type": "node", "id": "1", "name": "1"}])
    assert row.disposition is Disposition.PENDING


def test_a_row_with_neither_identity_nor_name_is_pending() -> None:
    (row,) = _dispose([{"note": "whatever"}])
    assert row.disposition is Disposition.PENDING
    assert row.source_identity == ""


def test_the_second_occurrence_of_an_identity_is_merged_into_the_first() -> None:
    """同一个上游对象出现两次是两行，第二行指着第一行。

    「300 行进来的、270 行出去」这件事因此答得出少了的那 30 行去哪了。
    """
    first, second = _dispose(
        [
            {"type": "node", "id": "1", "name": "Venue A"},
            {"type": "node", "id": "1", "name": "Venue A (dup)"},
        ]
    )
    assert first.disposition is Disposition.RESOLVED
    assert second.disposition is Disposition.MERGED
    assert second.merged_into == first.row_id
    assert second.merged_into != second.row_id


def test_same_name_and_location_are_not_merged() -> None:
    """**同名同地点不合并。** 这是本模块最该被钉住的一条。

    「同名同地点是同一个东西」是一个**判断**，而这一层拿不出证据支持它 ——
    一个村子里两个不同单位共用同一个名字和同一个邮编是常见的。

    代价是同一块场地可能进来两行，于是下游多做一次研究。但另一个方向的代价
    更大：两条**本来不同**的行被并成一条，那条丢掉的行在报告里只剩一个
    ``merged``，而「它到底是不是同一个」这个问题再也没有答案。

    与 ``pairing.keys_with_multiple_organisations`` 是同一条纪律：
    **看起来像的，报告，不解决。**
    """
    first, second = _dispose(
        [
            {"name": "Venue A", "town": "Exeter"},
            {"name": "Venue A", "town": "Exeter"},
        ]
    )
    assert first.disposition is Disposition.RESOLVED
    assert second.disposition is Disposition.RESOLVED
    assert second.ordinal == 1


def test_a_pending_row_does_not_absorb_a_resolved_one() -> None:
    """只有标识的那一行来了又走，后一条有名字的行仍然研究得了。

    这是一条真会发生的顺序：一份导出里同一个对象先出现一行只有 id 的骨架、
    再出现一行带名字的完整记录。
    """
    skeleton, full = _dispose(
        [
            {"type": "node", "id": "1"},
            {"type": "node", "id": "1", "name": "Venue A"},
        ]
    )
    assert skeleton.disposition is Disposition.MERGED
    assert full.disposition is Disposition.RESOLVED


def test_the_first_resolvable_occurrence_is_the_winner() -> None:
    """赢家是**序号最小且可研究的那一个**，所以这个选择是确定的（重放得住）。"""
    first, second, third = _dispose(
        [
            {"type": "node", "id": "1", "name": "Venue A"},
            {"type": "node", "id": "1"},
            {"type": "node", "id": "1", "name": "Another"},
        ]
    )
    assert first.disposition is Disposition.RESOLVED
    assert second.disposition is Disposition.MERGED
    assert second.merged_into == first.row_id
    assert third.disposition is Disposition.MERGED
    assert third.merged_into == first.row_id


def test_an_excluded_row_is_excluded_and_names_the_declaration() -> None:
    (row,) = _dispose([{"type": "node", "id": "1", "name": "A", "exclusions": "exclusion-a"}])
    assert row.disposition is Disposition.EXCLUDED
    assert row.exclusion_ids == ("exclusion-a",)
    assert row.reason


def test_an_excluded_row_carries_no_subject_fields() -> None:
    """被排除的行**不是**一个可以开工的主体 —— 它的名字不写进 resolved_name。"""
    (row,) = _dispose([{"type": "node", "id": "1", "name": "A", "exclusions": "exclusion-a"}])
    assert row.resolved_name == ""


def test_an_undeclared_exclusion_refuses_the_whole_batch() -> None:
    """未声明的排除项 id → **整份拒绝**，不降级、不跳过。

    收下它等于让输入**发明**一条 profile 从没声明过的规则 —— 而那条规则会静默地
    从池子里删行，报告上只显示 ``excluded=1``。记成 ``failed`` 则会把一次
    **导出与 profile 版本不匹配**渲染成「这一行读坏了」。两种处置相反，
    报告必须分得开。
    """
    with pytest.raises(ConfigError, match="未声明的排除项"):
        _dispose([{"type": "node", "id": "1", "name": "A", "exclusions": "exclusion-z"}])


def test_the_refusal_names_the_rows_and_the_declared_set() -> None:
    """拒绝的话要能照着做：哪几行、命中的是什么、这一份声明过什么。

    「有未声明的排除项」这句话本身修不了任何东西。
    """
    with pytest.raises(ConfigError) as excinfo:
        _dispose(
            [
                {"type": "node", "id": "1", "name": "A"},
                {"type": "node", "id": "2", "name": "B", "exclusions": "exclusion-z"},
            ]
        )
    message = str(excinfo.value)
    assert "'exclusion-z'" in message
    assert "[1]" in message
    assert "exclusion-a" in message


def test_one_bad_row_is_enough_to_refuse_the_batch() -> None:
    """不是只拒绝那一行 —— 整批都不落盘。

    一份「大部分行读进来了」的结果，与一份「全部行读进来了」的结果在报告上
    分不开，而前者会让下游以为这份导出与这一版 profile 是配得上的。
    """
    with pytest.raises(ConfigError):
        _dispose(
            [
                {"type": "node", "id": "1", "name": "A"},
                {"type": "node", "id": "2", "name": "B", "exclusions": "exclusion-z"},
            ]
        )


def test_an_excluded_row_never_becomes_the_merge_target() -> None:
    """被排除的行**不能**当另一行的归宿。

    一条按规则该排除的行，不该因为「它是这一批里第一个」而收下别的行 ——
    那会让排除规则在报告上看起来生效了，实际上它下面挂着一条没被排除的行。
    """
    excluded, later = _dispose(
        [
            {"type": "node", "id": "1", "name": "A", "exclusions": "exclusion-a"},
            {"type": "node", "id": "1", "name": "B"},
        ]
    )
    assert excluded.disposition is Disposition.EXCLUDED
    assert later.disposition is Disposition.RESOLVED
    assert later.resolved_name == "B"


def test_an_excluded_row_is_never_merged_even_when_its_identity_repeats() -> None:
    """排除先于归并：命中排除项的那一行按排除处置，**不**被折进一个赢家。

    把它折进去会让报告上的 ``excluded`` 计数变小，而它命中的那条规则在池子
    外面无从核对。
    """
    first, second = _dispose(
        [
            {"type": "node", "id": "1", "name": "A", "exclusions": "exclusion-a"},
            {"type": "node", "id": "1", "name": "B", "exclusions": "exclusion-a"},
        ]
    )
    assert first.disposition is Disposition.EXCLUDED
    assert second.disposition is Disposition.EXCLUDED


def test_a_row_hitting_two_declarations_carries_both() -> None:
    (row,) = _dispose(
        [{"type": "node", "id": "1", "name": "A", "exclusions": "exclusion-a,exclusion-b"}]
    )
    assert row.exclusion_ids == ("exclusion-a", "exclusion-b")


def test_duplicate_exclusions_on_one_row_are_reported_once() -> None:
    """同一行把同一个排除项写了两遍 —— 它仍然只是一个排除项。"""
    (row,) = _dispose(
        [{"type": "node", "id": "1", "name": "A", "exclusions": "exclusion-a; exclusion-a"}]
    )
    assert row.exclusion_ids == ("exclusion-a", "exclusion-a")


# --- 整份回执 -----------------------------------------------------------------


def test_every_raw_row_has_exactly_one_disposition(tmp_path: Path) -> None:
    """**这是这一层的全部承诺**：进来的每一行都有去向。

    混合了五种去向的一份池子 —— 有名字的、只有 id 的、重复的、被排除的、
    名字就是 id 的。五类加起来必须等于原始行数，而序号恰好是 ``0..n-1``。
    """
    _write(
        tmp_path,
        "pool",
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in [
                {"type": "node", "id": "1", "name": "Venue A", "town": "Exeter"},
                {"type": "node", "id": "2", "name": "1"},
                {"type": "node", "id": "3", "name": "Venue C", "exclusions": "exclusion-a"},
                {"type": "node", "id": "1", "name": "Venue A again"},
                {"type": "node", "id": "4"},
            ]
        )
        + "not json\n",
    )
    receipt = _read(tmp_path)
    assert receipt.raw_row_count == 6
    assert len(receipt.dispositions) == 6
    assert sorted(row.ordinal for row in receipt.dispositions) == [0, 1, 2, 3, 4, 5]
    counts = _counts(receipt)
    assert counts == _disposition_counts(resolved=1, merged=1, excluded=1, pending=2, failed=1)
    # 这一条断言才是这个测试的名字：五种去向加起来**恰好**等于进来的行数。
    # 它比逐个数字更耐改 —— 判定变了，这条仍然要求总数守恒。
    assert sum(counts.values()) == receipt.raw_row_count


def test_a_bad_line_gets_a_disposition_too(tmp_path: Path) -> None:
    """读不出来的行**不静默消失** —— 它占一个序号、带一个理由。

    丢掉它会让整份回执的序号不连续，而那正是「每一原始行都有一个处置」
    这句话要防的事。
    """
    _write(tmp_path, "pool", '{"name": "A"}\nnot json\n{"name": "C"}\n')
    receipt = _read(tmp_path)
    assert receipt.raw_row_count == 3
    assert _counts(receipt)["failed"] == 1
    assert receipt.dispositions[1].disposition is Disposition.FAILED
    assert "不是合法 JSON" in receipt.dispositions[1].reason


def test_an_empty_artifact_is_refused(tmp_path: Path) -> None:
    """空工件**不是**一次成功的读入：它什么都没产出。

    把它读成一份 ``raw=0`` 的回执，等于用一份空结果冒充一次跑过的母池 ——
    而报告上它与「这一份导出本来就没有行」分不开。
    """
    _write(tmp_path, "pool", "")
    with pytest.raises(ConfigError, match="一行都没有"):
        _read(tmp_path)


def test_a_wholly_pending_pool_reports_a_blocker(tmp_path: Path) -> None:
    """整池只有标识 → 回执**自己说**它阻塞在哪里。

    一条空的 ``resolved`` 与一条「每一行都缺身份补全」在读数上不一样，
    而报告要能区分它们。
    """
    _jsonl(tmp_path, [{"type": "node", "id": "1"}, {"type": "node", "id": "2"}])
    receipt = _read(tmp_path)
    assert receipt.resolved_rows == ()
    assert "身份补全" in receipt.blocker


def test_a_pool_with_one_resolved_row_has_no_blocker(tmp_path: Path) -> None:
    """有一条能开工的行，回执就不阻塞 —— 它不替下游决定「够不够」。"""
    _jsonl(tmp_path, [{"type": "node", "id": "1"}, {"type": "node", "id": "2", "name": "A"}])
    assert _read(tmp_path).blocker == ""


def test_the_receipt_carries_what_the_caller_declared(tmp_path: Path) -> None:
    """scope / cutoff / cursor / profile_id 原样带过去。

    它们不参与判定，它们回答那个事后一定会被问的问题：「这份回执里的
    ``excluded`` 是按哪一版声明下的？」
    """
    _jsonl(tmp_path, [{"name": "A"}])
    receipt = _read(
        tmp_path, scope="some/profile:run-1", cutoff="2026-09-01", cursor="offset=40"
    )
    assert receipt.scope == "some/profile:run-1"
    assert receipt.cutoff == "2026-09-01"
    assert receipt.cursor == "offset=40"
    assert receipt.source_method_id == "import.register.v1"
    assert receipt.profile_id == "some-profile"


def test_an_unregistered_source_method_is_refused(tmp_path: Path) -> None:
    """来源方法是一个**闭集**，失败关闭。"""
    _jsonl(tmp_path, [{"name": "A"}])
    with pytest.raises(ConfigError, match="未知的来源方法"):
        _read(tmp_path, source_method_id="made.up.v1")


# --- 内容寻址与重放 ---------------------------------------------------------------


def test_reading_the_same_artifact_twice_yields_the_same_receipt_id(tmp_path: Path) -> None:
    """**重放幂等。** 同一份工件、同一批参数 → 同一个回执 id。

    这是「重放不会重复建 Account」能成立的前提：下游按 id 幂等，而 id 只由内容
    决定 —— 时间、机器、路径都不进它。
    """
    _jsonl(tmp_path, [{"name": "A"}, {"name": "B"}])
    first = _read(tmp_path)
    second = _read(tmp_path, created_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert first.receipt_id == second.receipt_id


def test_a_different_batch_yields_a_different_receipt_id(tmp_path: Path) -> None:
    """批次不同就是另一份回执 —— 同一次导出在两个批次下不是同一次读入。"""
    _jsonl(tmp_path, [{"name": "A"}])
    assert _read(tmp_path).receipt_id != _read(tmp_path, batch_id="batch-02").receipt_id


def test_changing_one_row_changes_the_receipt_id(tmp_path: Path) -> None:
    """哪一行得到了哪个处置**在散列里**。

    否则「同一个 id」这句话会漏掉一次行级的对调 —— 而一次对调正好是
    「两条不同的东西换了名字」的形状。
    """
    _jsonl(tmp_path, [{"name": "A"}, {"name": "B"}])
    before = _read(tmp_path)
    _jsonl(tmp_path, [{"name": "B"}, {"name": "A"}])
    after = _read(tmp_path)
    assert before.receipt_id != after.receipt_id


def test_a_changed_artifact_is_a_different_receipt_even_with_identical_rows(
    tmp_path: Path,
) -> None:
    """工件本身进了 id：字节变了就是另一次读入，哪怕处置一模一样。

    两个不同的来源文件恰好产出同一套处置时，它们不是同一次读入 ——
    「读的是哪一份字节」这个问题要的是这一格。
    """
    _jsonl(tmp_path, [{"name": "A"}])
    before = _read(tmp_path)
    _write(tmp_path, "pool", '{"name": "A", "x": 1}\n')
    after = _read(tmp_path)
    assert before.artifact_sha256 != after.artifact_sha256
    assert before.receipt_id != after.receipt_id


def test_a_freshly_read_receipt_verifies(tmp_path: Path) -> None:
    _jsonl(tmp_path, [{"name": "A"}])
    assert verify_receipt_id(_read(tmp_path))


# --- 落盘与读回 ----------------------------------------------------------------


def test_write_then_load_round_trips(tmp_path: Path) -> None:
    _jsonl(tmp_path, [{"type": "node", "id": "1", "name": "A"}, {"type": "node", "id": "2"}])
    receipt = _read(tmp_path)
    path = write_receipt(tmp_path, receipt)
    assert path.name == f"{receipt.receipt_id}{RECEIPT_SUFFIX}"
    assert load_receipt(tmp_path, receipt.receipt_id) == receipt


def test_replaying_a_write_is_a_no_op(tmp_path: Path) -> None:
    """**重放不新建任何东西。**

    这条正是「重放幂等」在盘上的样子：同一份回执写两次，盘上仍是一份，
    而且它的修改时间不动 —— 它没有被重写过。
    """
    _jsonl(tmp_path, [{"name": "A"}])
    receipt = _read(tmp_path)
    path = write_receipt(tmp_path, receipt)
    before = path.stat().st_mtime_ns
    assert write_receipt(tmp_path, receipt) == path
    assert path.stat().st_mtime_ns == before
    assert sorted(p.name for p in tmp_path.glob(f"*{RECEIPT_SUFFIX}")) == [path.name]


def test_write_refuses_to_overwrite_a_changed_receipt(tmp_path: Path) -> None:
    """盘上那份被改过 → **拒绝**，不覆盖。

    路径是内容寻址的，所以同一个 id 下出现另一份内容只有一种解释。覆盖它会销毁
    那次改动的证据，而那正是要回答「谁改了什么」的东西。
    """
    _jsonl(tmp_path, [{"name": "A"}])
    receipt = _read(tmp_path)
    path = receipt_path(tmp_path, receipt.receipt_id)
    path.write_text('{"receipt_id": "tampered"}\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="已经存在"):
        write_receipt(tmp_path, receipt)


def test_write_refuses_an_existing_file_whose_content_was_edited(tmp_path: Path) -> None:
    """被改过、但**仍然读得回来**的那一份同样拒绝。

    这一条与上一条是两种形态：上一条改坏了形状（读不回来），这一条只改了内容
    （``resolved_location`` 换了个值）——整份文件照样能读成一份 ``SourceReceipt``，
    唯一的破绽是它与自己的 id 对不上。判据必须两种都拦得住。
    """
    _jsonl(tmp_path, [{"type": "node", "id": "1", "name": "A", "town": "Exeter"}])
    receipt = _read(tmp_path)
    path = receipt_path(tmp_path, receipt.receipt_id)
    payload = json.loads(
        json.dumps(receipt.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    )
    payload["dispositions"][0]["resolved_location"] = "Somewhere Else"
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="自洽的"):
        write_receipt(tmp_path, receipt)
    # 拒绝之后盘上那份**原样留着** —— 它是那次改动的证据。
    assert "Somewhere Else" in path.read_text(encoding="utf-8")


def test_a_replay_does_not_rewrite_the_first_reading_time(tmp_path: Path) -> None:
    """**这一条是我本来写错的那条判据的回归。**

    ``created_at`` 刻意不在 ``receipt_id`` 的推导里 —— 在里面的话，同一份工件在
    10:00 与 10:05 读出来会是两份不同的回执，而「重放幂等」正是要挡这个。
    所以两次读：**id 相同、字节不同**（只有那一格不同）。

    于是判据不能是「逐字节相同」：那会把一次正常重放读成一次「盘上那份被改过」。
    判据是「盘上那份仍然是一份自洽的、id 相符的回执」。后果是**第一次读的时刻
    留在盘上**，重放不重写它。
    """
    _jsonl(tmp_path, [{"type": "node", "id": "1", "name": "A", "town": "Exeter"}])
    first = _read(tmp_path)
    path = write_receipt(tmp_path, first)

    later = _read(tmp_path, created_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert later.created_at != first.created_at
    assert later.receipt_id == first.receipt_id

    assert write_receipt(tmp_path, later) == path
    assert load_receipt(tmp_path, first.receipt_id).created_at == first.created_at
    assert len(list(tmp_path.glob(f"*{RECEIPT_SUFFIX}"))) == 1


def test_write_refuses_a_receipt_whose_id_does_not_match_its_content(tmp_path: Path) -> None:
    """内容与 id 对不上的回执**不落盘**。

    落盘之后，「这份回执没被动过」这个问题就只剩信任了。所以写之前验一次 —
    盘上就不可能出现那样的文件。
    """
    _jsonl(tmp_path, [{"name": "A"}])
    receipt = _read(tmp_path)
    forged = receipt.model_copy(update={"receipt_id": "receipt-0000000000000000"})
    with pytest.raises(ConfigError, match="对不上"):
        write_receipt(tmp_path, forged)


def test_load_refuses_a_file_whose_name_disagrees_with_its_id(tmp_path: Path) -> None:
    """文件名与 ``receipt_id`` 必须一致。

    这个目录里「有哪几份回执」的答案来自文件名，「这一份是谁」来自内容 ——
    两者不一致时，按名字找与按内容找会给出两个不同的集合。
    """
    _jsonl(tmp_path, [{"name": "A"}])
    receipt = _read(tmp_path)
    body = json.dumps(
        receipt.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    )
    (tmp_path / f"some-other-name{RECEIPT_SUFFIX}").write_text(body + "\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="不一致"):
        load_receipt(tmp_path, "some-other-name")


def test_load_refuses_a_receipt_whose_content_was_edited(tmp_path: Path) -> None:
    """被改过的回执仍然**可读**，所以它必须在这里被拦下。

    这里改的是**结构上仍然合法**的一格（把一条 resolved 行的地点换掉）——
    整份文件照样能被 ``SourceReceipt`` 读进来，唯一的破绽是 id 与内容对不上。
    这正是要验的那条判据：``verify_receipt_id`` 是这种改动**唯一**会撞上的东西。

    它是「这一批行读成了什么」的唯一权威，而权威被改过之后，下游每一个读数
    都建立在一个没有出处的值上。
    """
    _jsonl(tmp_path, [{"type": "node", "id": "1", "name": "A", "town": "Exeter"}])
    receipt = _read(tmp_path)
    path = write_receipt(tmp_path, receipt)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["dispositions"][0]["resolved_location"] == "Exeter"
    payload["dispositions"][0]["resolved_location"] = "Somewhere Else"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="被改过"):
        load_receipt(tmp_path, receipt.receipt_id)


def test_load_refuses_an_edit_that_breaks_the_shape_too(tmp_path: Path) -> None:
    """结构**不合法**的改动更早一步就被拦下 —— 那时它连一份回执都不是。

    与上一条是两个不同的守卫：那一条验的是「内容与 id 自洽」（一份合法的、
    被改过的文件），这一条验的是「它还是一份回执」（一份改坏了的文件）。
    """
    _jsonl(tmp_path, [{"type": "node", "id": "1", "name": "A"}])
    receipt = _read(tmp_path)
    path = write_receipt(tmp_path, receipt)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["dispositions"][0]["disposition"] = "excluded"
    payload["dispositions"][0]["exclusion_ids"] = ["exclusion-a"]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="能读懂"):
        load_receipt(tmp_path, receipt.receipt_id)


def test_load_refuses_a_missing_receipt(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="找不到回执"):
        load_receipt(tmp_path, "receipt-0000000000000000")


def test_load_refuses_a_receipt_with_an_unknown_field(tmp_path: Path) -> None:
    """``extra="forbid"``：多出来的一格不是可以忽略的。

    多出来的东西只可能来自两种地方 —— 一份比本仓新的回执，或一次手工改写。
    两种都应该在这里被看见，而不是被静默丢掉。
    """
    _jsonl(tmp_path, [{"name": "A"}])
    receipt = _read(tmp_path)
    path = write_receipt(tmp_path, receipt)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["surprise"] = 1
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="能读懂"):
        load_receipt(tmp_path, receipt.receipt_id)


def test_the_written_receipt_is_lf_only_and_utf8(tmp_path: Path) -> None:
    """盘上那份是按 ``ensure_ascii=False`` + LF 写的。

    回执里有给人读的中文理由，把它们转义成十六进制转义序列之后，直接看这份文件
    就读不出发生了什么 —— 而它正是那个要被直接看的文件。
    """
    _jsonl(tmp_path, [{"type": "node", "id": "1"}])
    path = write_receipt(tmp_path, _read(tmp_path))
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert "身份补全".encode("utf-8") in raw


# --- 一条端到端的读入 --------------------------------------------------------------


def test_a_realistic_pool_reads_end_to_end(tmp_path: Path) -> None:
    """一份**诚实**的 fixture：按对象类型编号的导出，常态长相。

    里面有：只有 ID 的行、重复的对象、同名不同地点的两个单位、缺域名的、
    多出来的一列、以及一条命中排除项的。逐条钉住它会去哪里 ——
    这份测试的用处是「有人动了判定之后，第一眼就能看出是哪一条变了」。
    """
    _jsonl(
        tmp_path,
        [
            # 00 完整的一条
            {
                "type": "node",
                "id": "1001",
                "tags": {"name": "Riverside Club", "sport": "some-sport"},
                "town": "Exeter",
                "website": "https://www.riverside.example",
            },
            # 01 只有 ID —— Member-C 说的那种行
            {"type": "node", "id": "1002"},
            # 02 同一个对象又来一次
            {"type": "node", "id": "1001", "tags": {"name": "Riverside Club (again)"}},
            # 03 同名**不同地点** —— 不合并
            {"type": "node", "id": "1003", "tags": {"name": "Riverside Club"}, "town": "Bristol"},
            # 04 缺域名，但有名字
            {"type": "node", "id": "1004", "tags": {"name": "Hilltop Club"}},
            # 05 命中已声明的排除项
            {"type": "node", "id": "1005", "tags": {"name": "Meadow Club"}, "exclusions": "exclusion-a"},
            # 06 名字那一格写的是自己的编号
            {"type": "node", "id": "1006", "tags": {"name": "1006"}},
        ],
    )
    receipt = _read(tmp_path)

    assert receipt.raw_row_count == 7
    assert _counts(receipt) == _disposition_counts(
        resolved=3,  # 1001 / 1003 / 1004
        merged=1,  # 1001 的第二次出现
        excluded=1,  # 1005
        pending=2,  # 1002（只有 id）与 1006（名字就是 id）
    )

    resolved = {row.ordinal: row for row in receipt.dispositions if row.disposition is Disposition.RESOLVED}
    assert resolved[0].resolved_name == "Riverside Club"
    assert resolved[0].claimed_official_domain == "riverside.example"
    assert resolved[3].resolved_name == "Riverside Club"
    assert resolved[3].resolved_location == "Bristol"
    assert resolved[4].claimed_official_domain == ""
    # 嵌套的列在回执里读得出来（``tags.name`` 与裸 ``name`` 都在原始行里）。
    assert resolved[0].source_identity == "node:1001"


def test_the_pool_fixture_can_be_written_and_read_back(tmp_path: Path) -> None:
    """端到端：读入 → 落盘 → 读回，中间没有任何东西被改写。"""
    _jsonl(tmp_path, [{"type": "node", "id": "1", "name": "A"}, {"type": "node", "id": "2"}])
    receipt = _read(tmp_path)
    write_receipt(tmp_path, receipt)
    assert load_receipt(tmp_path, receipt.receipt_id) == receipt

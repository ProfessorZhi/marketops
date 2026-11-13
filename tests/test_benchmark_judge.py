"""盲判官：身份必须看不见，证据必须在场，校准不过就不能当结论。

三条会静默毁掉排名的失效，各钉一组判据：

* **不盲** —— 载荷里带厂商名，判官就会按品牌打分而不是按结果打分；
* **无证据** —— 一个可以随便写分数的判官，其分数无法被反驳，也无法被复用；
* **未校准** —— 一个从不说「不相关」的判官能在 agreement 上拿高分而毫无用处。

校准那一组故意把 TP/FP/TN/FN 四个格子都填上：只测 agreement 会让上面那个
「从不说否」的判官通过。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.runner.judge import (
    DIMENSIONS,
    JUDGE_PROMPT_VERSION,
    JUDGE_SCHEMA_VERSION,
    CalibrationReport,
    JudgeError,
    blind_labels,
    build_e2e_payload,
    build_fetch_payload,
    build_search_payload,
    calibrate,
    calibration_grade,
    load_verdicts,
    summaries,
    validate_verdict,
    verdict_to_binary,
    write_sealed_mapping,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
QUOTE = "Fédération Française de Football"


# --------------------------------------------------------------------------------------
# Blind payloads
# --------------------------------------------------------------------------------------


def _search_case() -> dict:
    return {
        "case_id": "SRCH-FR-001",
        "market": "FR",
        "language": "fr",
        "query": "fédération française de football",
        "task_type": "official_website",
        "top_k": 10,
    }


def _results() -> dict[str, list[dict]]:
    return {
        "serper": [{"rank": 1, "title": QUOTE, "url": "https://fff.example/", "snippet": "s"}],
        "serpapi": [{"rank": 1, "title": "Autre", "url": "https://other.example/", "snippet": "s"}],
        "tinyfish": [{"rank": 1, "title": "Trois", "url": "https://third.example/", "snippet": "s"}],
    }


def test_blind_labels_are_reproducible_for_a_replay() -> None:
    """同一次运行重放时必须得到同一组标签，否则两次评分无法比较。"""
    first = blind_labels(["serper", "serpapi", "tinyfish"], salt="run-1")
    second = blind_labels(["serper", "serpapi", "tinyfish"], salt="run-1")
    assert first == second
    assert sorted(first.values()) == ["candidate_a", "candidate_b", "candidate_c"]


def test_the_label_order_varies_across_salts() -> None:
    """同一个厂商如果永远是 ``candidate_a``，位置本身就是身份线索。

    单次比较会偶发相同（shuffle 本来就可能不动），所以看一组盐里是否出现过
    **不同的排序**，而不是断言某一次必然不同。
    """
    names = ["serper", "serpapi", "tinyfish"]
    orders = {
        tuple(blind_labels(names, salt=f"s{i}").get(name) for name in names)
        for i in range(20)
    }
    assert len(orders) > 1


@pytest.mark.parametrize("builder", ["search", "fetch", "e2e"])
def test_the_payload_carries_no_provider_name(builder: str) -> None:
    """输入里明明有厂商名，输出里一个都不能有 —— 两头都断言。

    只断言输出没有厂商名，会在「输入本来就没带」时假绿；所以先证明输入带了。
    """
    providers = ["serper", "serpapi", "tinyfish", "deepseek", "crawl4ai"]
    case = {"case_id": "X-1", "url": "https://fff.example/", "scenario": "s",
            "market": "FR", "language": "fr", "query": "q"}
    if builder == "search":
        raw = {
            p: [{"rank": 1, "title": QUOTE, "url": f"https://site-{i}.example/"}]
            for i, p in enumerate(providers)
        }
        assert all(p in raw for p in providers)
        payload, labels = build_search_payload(case, raw, salt="x")
    elif builder == "fetch":
        raw = {p: f"page body {i}" for i, p in enumerate(providers)}
        assert all(p in raw for p in providers)
        payload, labels = build_fetch_payload(case, raw, salt="x")
    else:
        raw = {p: {"entities": [{"name": f"Entity {i}"}]} for i, p in enumerate(providers)}
        assert all(p in raw for p in providers)
        payload, labels = build_e2e_payload(case, raw, salt="x")
    haystack = json.dumps(payload, ensure_ascii=False).lower()
    for provider in providers:
        assert provider not in haystack, f"{builder} 载荷里出现了厂商名 {provider}"
    assert set(labels) == set(providers)


def test_the_label_map_is_the_inverse_of_the_payload() -> None:
    payload, labels = build_search_payload(_search_case(), _results(), salt="x")
    shown = {c["candidate"] for c in payload["candidates"]}
    assert shown == set(labels.values())
    assert len(shown) == len(labels)


def test_fetch_payload_truncates_and_says_so() -> None:
    """截断必须显式标记：一段被截掉的正文与一段没读完的正文不是同一件事。"""
    payload, _ = build_fetch_payload(
        {"case_id": "FECH-1", "url": "https://x.example/"},
        {"reader": "y" * 500},
        salt="x",
        max_chars=100,
    )
    candidate = payload["candidates"][0]
    assert len(candidate["extracted_text"]) == 100
    assert candidate["truncated"] is True


def test_the_sealed_mapping_is_byte_identical_when_rewritten(tmp_path: Path) -> None:
    mapping = {"SRCH-FR-001": {"serper": "candidate_b", "serpapi": "candidate_a"}}
    a = write_sealed_mapping(mapping, tmp_path / "a.json")
    b = write_sealed_mapping(mapping, tmp_path / "b.json")
    assert a.read_bytes() == b.read_bytes()


# --------------------------------------------------------------------------------------
# Verdict validation
# --------------------------------------------------------------------------------------


def _dim(score: float | None = 0.9, quote: str = QUOTE) -> dict:
    return {
        "score": score,
        "confidence": 0.8,
        "evidence": [{"quote": quote, "source_ref": "results[0].title"}] if quote else [],
        "rationale": "the title matches the query",
    }


def _verdict(payload: dict, *, score: float = 0.9, quote: str = QUOTE,
             abstained: bool = False, reason: str | None = None,
             blind: bool = True, case_id: str | None = None) -> dict:
    return {
        "schema_version": JUDGE_SCHEMA_VERSION,
        "verdict_id": "v-1",
        "case_id": case_id or payload["case"]["case_id"],
        "layer": payload["layer"],
        "judge": {"provider": "jev", "model": "jev-1", "prompt_version": JUDGE_PROMPT_VERSION},
        "blind": blind,
        "dimensions": {name: _dim(score, quote) for name in DIMENSIONS},
        "overall": {"score": score, "confidence": 0.8, "rationale": "overall"},
        "abstained": abstained,
        "abstain_reason": reason,
        "created_at": "2026-11-13T00:00:00Z",
    }


@pytest.fixture(name="payload")
def _payload_fixture() -> dict:
    return build_search_payload(_search_case(), _results(), salt="x")[0]


def test_a_well_formed_verdict_is_accepted(payload: dict) -> None:
    validate_verdict(_verdict(payload), payload)


def test_the_example_verdict_matches_the_schema(payload: dict) -> None:
    """``validate_verdict`` 是结构检查；模式文件才是契约。两者都得过。"""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (REPO_ROOT / "benchmark" / "schemas" / "judge-verdict.schema.json").read_text(
            encoding="utf-8"
        )
    )
    verdict = _verdict(payload)
    jsonschema.Draft202012Validator(schema).validate(verdict)


def test_a_scored_dimension_without_evidence_is_rejected(payload: dict) -> None:
    """没有证据的分数无法被核实，因此不能进排名。"""
    verdict = _verdict(payload)
    verdict["dimensions"]["search_relevance"] = _dim(0.9, quote="")
    with pytest.raises(JudgeError, match="cites no evidence"):
        validate_verdict(verdict, payload)


def test_a_quote_that_is_not_in_the_payload_is_rejected(payload: dict) -> None:
    """判官编造引用是这类系统最常见的失效，必须能自动发现。"""
    verdict = _verdict(payload, quote="this sentence was never in the payload")
    with pytest.raises(JudgeError, match="does not appear in the payload"):
        validate_verdict(verdict, payload)


def test_an_abstention_must_state_its_reason(payload: dict) -> None:
    verdict = _verdict(payload, abstained=True, reason=None)
    with pytest.raises(JudgeError, match="abstention must state its reason"):
        validate_verdict(verdict, payload)
    verdict["abstain_reason"] = "the payload does not contain the page body"
    validate_verdict(verdict, payload)


def test_a_verdict_from_an_unblinded_payload_is_refused(payload: dict) -> None:
    with pytest.raises(JudgeError, match="unblinded"):
        validate_verdict(_verdict(payload, blind=False), payload)


def test_a_verdict_for_another_case_is_refused(payload: dict) -> None:
    with pytest.raises(JudgeError, match="case_id"):
        validate_verdict(_verdict(payload, case_id="SRCH-FR-999"), payload)


def test_an_out_of_range_dimension_score_is_refused(payload: dict) -> None:
    verdict = _verdict(payload)
    verdict["dimensions"]["freshness"]["score"] = 1.5
    with pytest.raises(JudgeError, match=r"within \[0, 1\]"):
        validate_verdict(verdict, payload)


# --------------------------------------------------------------------------------------
# Binary collapse
# --------------------------------------------------------------------------------------


def test_abstention_collapses_to_none_not_false(payload: dict) -> None:
    """弃权记成 False 会让「什么都不判」看起来像「判了不相关」。"""
    assert verdict_to_binary(_verdict(payload, abstained=True, reason="x")) is None


def test_a_missing_overall_score_is_not_a_negative_verdict(payload: dict) -> None:
    verdict = _verdict(payload)
    verdict["overall"]["score"] = None
    assert verdict_to_binary(verdict) is None


def test_the_threshold_decides_the_binary(payload: dict) -> None:
    assert verdict_to_binary(_verdict(payload, score=0.6), threshold=0.6) is True
    assert verdict_to_binary(_verdict(payload, score=0.59), threshold=0.6) is False


# --------------------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------------------


def _decided(case_id: str, score: float, *, abstained: bool = False) -> dict:
    return {
        "case_id": case_id,
        "abstained": abstained,
        "abstain_reason": "cannot judge" if abstained else None,
        "overall": {"score": score, "confidence": 0.8, "rationale": ""},
        "dimensions": {"search_relevance": {"score": score}},
    }


def test_calibration_fills_all_four_cells() -> None:
    """TP=6 FP=2 FN=1 TN=3：agreement 0.75，FPR 0.4，FNR 1/7。

    只报 agreement 的校准会同时掩盖 FP 与 FN —— 一个从不说「否」的判官在
    agreement 上能拿满分，在这里 FPR=1.0。
    """
    verdicts, labels = [], {}
    plan = [  # (judge score, human score) -> cell
        *[(0.9, 0.9)] * 6,  # TP
        *[(0.9, 0.1)] * 2,  # FP
        *[(0.1, 0.9)] * 1,  # FN
        *[(0.1, 0.1)] * 3,  # TN
    ]
    for i, (judge, human) in enumerate(plan):
        cid = f"c{i}"
        verdicts.append(_decided(cid, judge))
        labels[cid] = {"overall": human}

    report = calibrate(verdicts, labels)
    assert report.sample_size == 12
    assert report.agreement == pytest.approx(0.75)
    assert report.false_positive_rate == pytest.approx(0.4)
    assert report.false_negative_rate == pytest.approx(1 / 7)
    assert report.mean_absolute_error is not None


def test_calibration_skips_cases_without_a_human_label() -> None:
    """没有人工标签的用例校准不了任何东西 —— 它不能抬高样本量。"""
    report = calibrate([_decided("a", 0.9), _decided("b", 0.1)], {"a": {"overall": 0.9}})
    assert report.sample_size == 1
    assert report.abstentions == 0


def test_abstentions_count_toward_the_sample_but_not_agreement() -> None:
    """弃权是「没判」，不是「判对」也不是「判错」。"""
    report = calibrate(
        [_decided("a", 0.9), _decided("b", 0.9, abstained=True)],
        {"a": {"overall": 0.9}, "b": {"overall": 0.9}},
    )
    assert report.sample_size == 2
    assert report.abstentions == 1
    assert report.agreement == pytest.approx(1.0)


def test_calibration_of_nothing_is_none_not_zero() -> None:
    report = calibrate([], {})
    assert (report.sample_size, report.agreement, report.mean_absolute_error) == (0, None, None)


def test_per_dimension_mae_is_reported_only_where_both_sides_scored() -> None:
    verdict = _decided("a", 0.8)
    report = calibrate([verdict], {"a": {"overall": 0.8, "dimensions": {"search_relevance": 0.4}}})
    assert report.per_dimension_mae["search_relevance"] == pytest.approx(0.4)
    assert report.per_dimension_mae["freshness"] is None


# --------------------------------------------------------------------------------------
# Grade
# --------------------------------------------------------------------------------------


def test_a_small_sample_is_never_called_calibrated() -> None:
    """9 个样本全对也不够 —— 「全对」在小样本上不携带信息。"""
    report = CalibrationReport(sample_size=9, agreement=1.0, false_positive_rate=0.0)
    assert calibration_grade(report) == "insufficient_sample"


def test_low_agreement_is_uncalibrated() -> None:
    report = CalibrationReport(sample_size=20, agreement=0.75, false_positive_rate=0.1)
    assert calibration_grade(report) == "uncalibrated"


def test_a_high_false_positive_rate_is_uncalibrated_even_with_good_agreement() -> None:
    """稀有正例的分布里，agreement 高而 FPR 高是常态 —— 必须单独看 FPR。"""
    report = CalibrationReport(sample_size=40, agreement=0.9, false_positive_rate=0.4)
    assert calibration_grade(report) == "uncalibrated"


def test_a_good_judge_is_calibrated() -> None:
    report = CalibrationReport(sample_size=40, agreement=0.9, false_positive_rate=0.1)
    assert calibration_grade(report) == "calibrated"


# --------------------------------------------------------------------------------------
# Summaries / IO
# --------------------------------------------------------------------------------------


def test_summaries_exclude_abstentions_from_the_means(payload: dict) -> None:
    verdicts = [
        dict(_verdict(payload, score=1.0), usage={"cost_usd": 0.002}),
        dict(_verdict(payload, score=0.0, abstained=True, reason="x"), usage={"cost_usd": 0.001}),
    ]
    out = summaries(verdicts)
    assert out["verdicts"] == 2
    assert out["abstentions"] == 1
    assert out["mean_overall"] == pytest.approx(1.0)
    assert out["total_cost_usd"] == pytest.approx(0.003)


def test_a_single_verdict_file_loads_as_a_list(tmp_path: Path, payload: dict) -> None:
    p = tmp_path / "one.json"
    p.write_text(json.dumps(_verdict(payload)), encoding="utf-8")
    assert len(load_verdicts(p)) == 1


def test_a_non_list_json_verdict_file_is_refused(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text('"a string"', encoding="utf-8")
    with pytest.raises(JudgeError, match="one verdict or a list"):
        load_verdicts(p)

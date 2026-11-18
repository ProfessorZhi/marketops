"""命令行端到端：真数据集 + 录像目录 → 计分卡 → Markdown。

这里判的不是 argparse 解析，而是**产物契约**：

* 计分卡必须过 ``scorecard.schema.json`` —— 报告是给别人读的，字段漂了就不能用;
* 录了响应的 provider 一定成一条 arm，没录的目录一定不成 —— 否则「没测」会
  在表里变成「测了、0 分」;
* 关键指标不能恒为 ``null`` —— 一张永远 ``n/a`` 的计分卡与没有计分卡等价。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark.runner.cli import main

REPO_ROOT = Path(__file__).resolve().parents[1]
PILOT = REPO_ROOT / "benchmark" / "datasets" / "pilot"
SCHEMA = REPO_ROOT / "benchmark" / "schemas" / "scorecard.schema.json"


def _cases(layer: str) -> list[dict]:
    path = PILOT / layer / "pilot.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _dump(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def _recorded(root: Path) -> Path:
    """One run where every layer has a correct arm and a deliberately bad one."""
    root.mkdir(parents=True, exist_ok=True)
    _dump(root / "manifest.json", {
        "providers": {
            "alpha": {
                "version": "1.0", "endpoint": "recorded",
                "region_params_sent": {"gl": "FR"}, "api_quota_used": 24,
            }
        }
    })

    for case in _cases("search"):
        gold = [r["url"] for r in case["gold_label"].get("relevant_urls") or []]
        _dump(root / "alpha" / f"{case['case_id']}.json", {
            "results": [
                {"rank": i + 1, "title": "gold", "url": url, "snippet": ""}
                for i, url in enumerate(gold)
            ],
            "_meta": {"latency_ms": 100.0 + len(gold), "cost_usd": 0.001},
        })
        _dump(root / "beta" / f"{case['case_id']}.json", {
            "results": [{"rank": 1, "title": "noise", "url": "https://noise.example/"}],
            "_meta": {"latency_ms": 50.0, "cost_usd": 0.015},
        })

    for case in _cases("fetch"):
        gold = case.get("gold_label") or {}
        if case.get("expected_outcome") == "content":
            text = " ".join(gold.get("expected_text_markers") or []) + " " + "filler " * 200
            good = {"ok": True, "status": 200, "text": text}
            bad = {"ok": True, "status": 200, "text": "Loading…"}
        else:
            good = {"ok": False, "status": 403, "error_code": "no_content"}
            bad = {"ok": True, "status": 200, "text": "Log in to continue"}
        _dump(root / "reader_a" / f"{case['case_id']}.json",
              dict(good, _meta={"latency_ms": 300.0, "cost_usd": 0.002}))
        _dump(root / "reader_b" / f"{case['case_id']}.json",
              dict(bad, _meta={"latency_ms": 800.0, "cost_usd": 0.0}))

    for case in _cases("e2e"):
        gold = (case.get("gold_label") or {}).get("entities") or []
        _dump(root / "cfg_good" / f"{case['case_id']}.json", {
            "entities": [
                {"entity_id": e["entity_id"], "name": e.get("canonical_name", ""),
                 "official_url": e.get("official_url"), "contacts": []}
                for e in gold
            ],
            "_meta": {"latency_ms": 900.0, "cost_usd": 0.01},
        })
        _dump(root / "cfg_bad" / f"{case['case_id']}.json", {
            "entities": [{"entity_id": "GHOST", "name": "Invented FC", "contacts": []}],
            "_meta": {"latency_ms": 900.0, "cost_usd": 0.01},
        })

    # A directory whose file names match no case in any layer: it recorded nothing
    # usable, so it must not become an arm anywhere.
    _dump(root / "stray" / "NOT-A-CASE.json", {"results": []})
    return root


def _arm(scorecard: dict, block: str, name_key: str, name: str) -> dict | None:
    return next((a for a in scorecard[block] if a[name_key] == name), None)


@pytest.fixture(name="scored")
def _scored_fixture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[dict, Path]:
    recorded = _recorded(tmp_path / "recorded")
    out = tmp_path / "out" / "scorecard.json"
    assert main(["score", "--dataset", str(PILOT), "--recorded", str(recorded),
                 "--out", str(out), "--run-id", "test-run"]) == 0
    capsys.readouterr()
    return json.loads(out.read_text(encoding="utf-8")), out


# --------------------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------------------


def test_validate_loads_the_real_pilot(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", "--dataset", str(PILOT)]) == 0
    out = capsys.readouterr().out
    assert "24 search, 28 fetch, 10 e2e" in out
    assert "development" in out and "holdout" in out


def test_a_missing_dataset_exits_2_with_a_readable_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(["validate", "--dataset", "no/such/dataset"])
    err = capsys.readouterr().err
    assert code == 2
    assert err.startswith("error: ") and "no such dataset directory" in err


# --------------------------------------------------------------------------------------
# score
# --------------------------------------------------------------------------------------


def test_the_scorecard_matches_its_schema(scored: tuple[dict, Path]) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    scorecard, _ = scored
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(scorecard)


def test_every_recorded_provider_becomes_exactly_one_arm(scored: tuple[dict, Path]) -> None:
    scorecard, _ = scored
    assert [a["provider"] for a in scorecard["search"]] == ["alpha", "beta"]
    assert [a["reader"] for a in scorecard["fetch"]] == ["reader_a", "reader_b"]
    assert [a["configuration"] for a in scorecard["e2e"]] == ["cfg_bad", "cfg_good"]


def test_a_directory_that_matched_no_case_is_not_an_arm(scored: tuple[dict, Path]) -> None:
    """``stray`` 只说明「那里有个目录」，不说明测过任何东西。"""
    scorecard, _ = scored
    for block, key in (("search", "provider"), ("fetch", "reader"), ("e2e", "configuration")):
        assert _arm(scorecard, block, key, "stray") is None


def test_the_good_search_arm_beats_the_noise_arm(scored: tuple[dict, Path]) -> None:
    scorecard, _ = scored
    alpha = _arm(scorecard, "search", "provider", "alpha")
    beta = _arm(scorecard, "search", "provider", "beta")
    assert alpha["metrics"]["recall_at_10"] == pytest.approx(1.0)
    assert beta["metrics"]["recall_at_10"] == pytest.approx(0.0)
    # the 12 cases with no gold relevant urls are unmeasurable, so the mean is not 1.0
    assert alpha["metrics"]["recall_at_10"] is not None


def test_search_money_and_latency_are_reported(scored: tuple[dict, Path]) -> None:
    scorecard, _ = scored
    alpha = _arm(scorecard, "search", "provider", "alpha")
    beta = _arm(scorecard, "search", "provider", "beta")
    assert alpha["cost"]["total_usd"] > 0
    assert beta["cost"]["total_usd"] > alpha["cost"]["total_usd"]
    assert alpha["latency_ms"]["p50"] is not None
    # no gold hit means no denominator: null, not 0 and not infinity
    assert beta["cost"]["per_100_relevant_results_usd"] is None


def test_fetch_reports_useful_content_and_false_success_not_null(
    scored: tuple[dict, Path],
) -> None:
    """两个最关键的口径必须算得出来。

    曾经 ``FetchArm.metrics`` 把 per-case 的原始信号当比率求均值，
    ``useful_content_rate`` 与 ``shell_page_false_success_rate`` 因此恒为 null ——
    一张「付钱买空壳」永远显示 ``n/a`` 的计分卡没有存在的意义。
    """
    scorecard, _ = scored
    a = _arm(scorecard, "fetch", "reader", "reader_a")
    b = _arm(scorecard, "fetch", "reader", "reader_b")
    assert a["metrics"]["useful_content_rate"] == pytest.approx(1.0)
    assert a["metrics"]["shell_page_false_success_rate"] == pytest.approx(0.0)
    assert b["metrics"]["useful_content_rate"] < 1.0
    assert b["metrics"]["shell_page_false_success_rate"] is not None
    assert b["metrics"]["shell_page_false_success_rate"] > 0.0


def test_e2e_reports_task_completion_not_null(scored: tuple[dict, Path]) -> None:
    scorecard, _ = scored
    good = _arm(scorecard, "e2e", "configuration", "cfg_good")
    bad = _arm(scorecard, "e2e", "configuration", "cfg_bad")
    assert good["metrics"]["task_completion_rate"] == pytest.approx(1.0)
    assert good["metrics"]["entity_recall"] == pytest.approx(1.0)
    assert bad["metrics"]["task_completion_rate"] == pytest.approx(0.0)
    assert bad["metrics"]["false_positive_rate"] == pytest.approx(1.0)


def test_manifest_metadata_reaches_the_environment_block(scored: tuple[dict, Path]) -> None:
    scorecard, _ = scored
    providers = scorecard["environment"]["providers"]
    assert providers["alpha"]["version"] == "1.0"
    assert providers["alpha"]["api_quota_used"] == 24
    # unrecorded metadata must not be invented
    assert providers["beta"]["version"] == "unknown"


def test_the_default_scoring_window_is_the_holdout_split(scored: tuple[dict, Path]) -> None:
    scorecard, _ = scored
    assert scorecard["dataset"]["split"] == "holdout"
    assert scorecard["dataset"]["case_counts"] == {"search": 24, "fetch": 28, "e2e": 10}
    assert scorecard["dataset"]["label_status"] == {"draft": 62}
    assert scorecard["run"]["mode"] == "replay"
    assert "judge: none" in " ".join(scorecard["notes"])


def test_scoring_a_split_narrows_the_cases(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded = _recorded(tmp_path / "recorded")
    out = tmp_path / "dev.json"
    assert main(["score", "--dataset", str(PILOT), "--recorded", str(recorded),
                 "--out", str(out), "--split", "development"]) == 0
    capsys.readouterr()
    scorecard = json.loads(out.read_text(encoding="utf-8"))
    assert scorecard["dataset"]["split"] == "development"
    assert scorecard["dataset"]["case_counts"] == {"search": 11, "fetch": 13, "e2e": 4}
    assert _arm(scorecard, "search", "provider", "alpha")["cases"] == 11


def test_markdown_and_ablation_are_written(scored: tuple[dict, Path]) -> None:
    _, out = scored
    md = out.with_suffix(".md").read_text(encoding="utf-8")
    assert "## Search" in md and "## Fetch" in md and "## End to end" in md
    assert "n/a" in md  # the metrics that could not be computed stay visible
    # The warning belongs on the artefact people quote, not only in the docs.
    assert "draft=62" in md and "not decision-grade" in md
    ablation = out.with_suffix(".ablation.md").read_text(encoding="utf-8")
    assert "recall drop" in ablation
    assert "beta" in ablation


def test_a_corrupt_recorded_file_exits_2_instead_of_half_scoring(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded = _recorded(tmp_path / "recorded")
    # A half-written file for a case that IS in the dataset: scoring it as "no results"
    # would turn a corruption into a provider failure.
    first = _cases("search")[0]["case_id"]
    (recorded / "alpha" / f"{first}.json").write_text("{broken", encoding="utf-8")
    code = main(["score", "--dataset", str(PILOT), "--recorded", str(recorded),
                 "--out", str(tmp_path / "x.json")])
    assert code == 2
    assert capsys.readouterr().err.startswith("error: ")


# --------------------------------------------------------------------------------------
# report / calibrate
# --------------------------------------------------------------------------------------


def test_report_renders_from_a_saved_scorecard(
    scored: tuple[dict, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out = scored
    target = tmp_path / "again.md"
    assert main(["report", "--scorecard", str(out), "--out", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == out.with_suffix(".md").read_text(encoding="utf-8")
    capsys.readouterr()


def test_calibrate_prints_a_machine_readable_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    verdicts = tmp_path / "verdicts.json"
    labels = tmp_path / "labels.json"
    verdicts.write_text(json.dumps([
        {"case_id": "a", "abstained": False, "overall": {"score": 0.9},
         "dimensions": {"search_relevance": {"score": 0.9}}},
        {"case_id": "b", "abstained": False, "overall": {"score": 0.1},
         "dimensions": {"search_relevance": {"score": 0.1}}},
    ]), encoding="utf-8")
    labels.write_text(json.dumps({"a": {"overall": 0.9}, "b": {"overall": 0.1}}), encoding="utf-8")

    assert main(["calibrate", "--verdicts", str(verdicts), "--labels", str(labels)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sample_size"] == 2
    assert payload["agreement"] == pytest.approx(1.0)
    assert payload["abstentions"] == 0


def test_a_scorecard_that_does_not_state_its_label_status_is_invalid(
    scored: tuple[dict, Path],
) -> None:
    """schema 里 ``label_status`` 是 required，不是可选装饰。

    少这一格，读者无法分辨「金标是人确认过的」还是「还是 draft」—— 而 pilot 现在
    全部是 draft。判这条是为了让「删掉这个字段」变成一次 schema 报错。
    """
    jsonschema = pytest.importorskip("jsonschema")
    scorecard, _ = scored
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    broken = dict(scorecard)
    broken["dataset"] = {k: v for k, v in scorecard["dataset"].items() if k != "label_status"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(broken)

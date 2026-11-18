"""基准指标的判据：分母、空集、壳页、证据。

这几条不是「函数算得对不对」的自证，每一条钉的是一个**会被误读成能力差异**的性质：

* ``precision_at_k`` 的分母是 k —— 只返回 3 条且全对的引擎不是「更准」，是「没答完」；
* 算不出来的是 ``None``，不是 ``0`` —— 「没观测到」与「观测到是零」在业务上相反；
* 登录墙返回了 200 与正文，必须记成假成功 —— 否则付钱买回来的空壳会被当成证据；
* 没有 ``evidence_url`` 的联系人不计入任何正例 —— 待核是一种状态，不是一种正确。
"""

from __future__ import annotations

import pytest

from benchmark.runner.metrics import (
    DeliveredEntity,
    FetchResult,
    GoldLabel,
    SearchResult,
    ablation,
    aggregate_fetch,
    cost_per_result,
    domain_diversity,
    duplicate_rate,
    e2e_scores,
    extract_emails,
    extract_phones,
    fetch_case_scores,
    normalize_url,
    ndcg_at_k,
    official_source_rate,
    pareto_front,
    percentile,
    precision_at_k,
    reciprocal_rank,
    recall_at_k,
    unique_recall,
)

GOLD = GoldLabel(
    relevant={normalize_url("https://a.example/x"): 3, normalize_url("https://b.example/y"): 2},
    official={normalize_url("https://a.example/x")},
)


def _results(*urls: str) -> list[SearchResult]:
    return [SearchResult(url=u, rank=i + 1) for i, u in enumerate(urls)]


# --------------------------------------------------------------------------------------
# URL normalisation
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "HTTP://WWW.Example.com/a/?utm_source=x#frag",
        "https://example.com/a",
        "http://example.com/a/",
        "https://www.example.com/a",
    ],
)
def test_url_spellings_of_the_same_page_collapse(raw: str) -> None:
    """四种拼法必须是同一个 URL。

    不归一化时，``http://www.x/a/`` 与 ``https://x/a`` 会被算成两条结果，
    于是 precision 偏低、unique recall 偏高 —— 两个方向的偏差同时出现且互相掩盖。
    """
    assert normalize_url(raw) == "https://example.com/a"


def test_url_normalisation_keeps_real_query_parameters() -> None:
    """只去跟踪参数。去掉 ``?id=7`` 会把两个不同的页面合成一个。"""
    assert normalize_url("https://x.example/p?id=7&utm_campaign=z") == "https://x.example/p?id=7"


# --------------------------------------------------------------------------------------
# Search metrics
# --------------------------------------------------------------------------------------


def test_precision_divides_by_k_not_by_what_the_provider_returned() -> None:
    """返回 3 条、对 3 条，P@10 是 0.3 而不是 1.0。

    这是整份标准里最容易被写错的一条：按返回条数做分母，会把「答得少」奖励成「答得准」。
    """
    got = precision_at_k(_results("https://a.example/x"), GOLD, 10)
    assert got == pytest.approx(0.1)
    three = _results("https://a.example/x", "https://b.example/y", "https://c.example/z")
    assert precision_at_k(three, GOLD, 10) == pytest.approx(0.2)


def test_recall_and_mrr() -> None:
    assert recall_at_k(_results("https://a.example/x"), GOLD, 10) == pytest.approx(0.5)
    assert reciprocal_rank(_results("https://c.example/z", "https://a.example/x"), GOLD) == (
        pytest.approx(0.5)
    )
    assert reciprocal_rank(_results("https://c.example/z"), GOLD) == 0.0


def test_metrics_over_an_empty_gold_label_are_none_not_zero() -> None:
    """没有金标的用例不能得出 0 分 —— 那是「没量」，不是「量到 0」。"""
    empty = GoldLabel()
    assert recall_at_k(_results("https://a.example/x"), empty, 10) is None
    assert reciprocal_rank(_results("https://a.example/x"), empty) is None
    assert ndcg_at_k(_results("https://a.example/x"), empty, 10) is None


def test_ndcg_ranks_the_relevance_3_hit_above_the_relevance_2_one() -> None:
    best = ndcg_at_k(_results("https://a.example/x", "https://b.example/y"), GOLD, 10)
    worst = ndcg_at_k(_results("https://b.example/y", "https://a.example/x"), GOLD, 10)
    assert best == pytest.approx(1.0)
    assert worst < best


def test_official_rate_is_measured_against_the_gold_official_set() -> None:
    """不是看 TLD —— 一个 ``.gouv.fr`` 也可能是聚合站，判定只认金标里标了 official 的那个。"""
    both = _results("https://a.example/x", "https://b.example/y")
    assert official_source_rate(both, GOLD) == pytest.approx(0.5)
    assert official_source_rate(both, GoldLabel(relevant=GOLD.relevant)) is None


def test_diversity_and_duplicates() -> None:
    same_host = _results("https://a.example/1", "https://a.example/2")
    assert domain_diversity(same_host) == pytest.approx(0.5)
    assert duplicate_rate(_results("https://a.example/1", "https://a.example/1/")) == (
        pytest.approx(0.5)
    )
    assert domain_diversity([]) is None


def test_unique_recall_credits_only_the_provider_that_found_it_alone() -> None:
    x = "https://a.example/x"
    y = "https://b.example/y"
    out = unique_recall(
        {"p1": _results(x, y), "p2": _results(x)}, GoldLabel(relevant=GOLD.relevant)
    )
    assert out["p1"] == pytest.approx(0.5)  # y is p1-only
    assert out["p2"] == pytest.approx(0.0)


# --------------------------------------------------------------------------------------
# Latency / cost
# --------------------------------------------------------------------------------------


def test_percentile_is_nearest_rank_and_empty_is_none() -> None:
    assert percentile([10, 20, 30, 40], 50) == 20.0
    assert percentile([10, 20, 30, 40], 95) == 40.0
    assert percentile([], 95) is None


def test_cost_per_result_refuses_to_divide_by_zero_hits() -> None:
    assert cost_per_result(0.5, 0) is None
    assert cost_per_result(0.5, 250) == pytest.approx(0.2)


# --------------------------------------------------------------------------------------
# Fetch metrics
# --------------------------------------------------------------------------------------

CONTENT_CASE = {
    "case_id": "F-CONTENT",
    "task_type": "static_html",
    "expected_outcome": "content",
    "gold_label": {
        "expected_text_markers": ["Fédération Française de Football"],
        "min_text_chars": 500,
        "expected_emails": ["contact@fff.fr"],
        "expected_phones": [],
        "expected_links": [],
    },
}

WALL_CASE = {
    "case_id": "F-WALL",
    "task_type": "antibot_challenge",
    "expected_outcome": "no_content",
    "gold_label": {
        "expected_text_markers": [],
        "min_text_chars": 0,
        "expected_emails": [],
        "expected_phones": [],
        "expected_links": [],
        "forbidden_strings": ["Log in"],
    },
}


def test_extractors() -> None:
    assert extract_emails("Écrire à Contact@FFF.FR.") == ["contact@fff.fr"]
    assert extract_phones("Tél. +33 1 44 20 83 00") == ["33144208300"]
    assert extract_phones("le 7 mai 2019") == []


def test_a_read_that_returns_the_real_content_is_not_a_shell() -> None:
    text = "Fédération Française de Football — contact@fff.fr — " + ("x" * 800)
    scores = fetch_case_scores(
        FetchResult(url="https://www.fff.fr/", ok=True, text=text), CONTENT_CASE
    )
    assert scores["shell_page"] is False
    assert scores["text_coverage"] == pytest.approx(1.0)
    assert scores["email_recall"] == pytest.approx(1.0)


def test_a_read_that_returns_chrome_is_a_shell_page() -> None:
    """ok=true、非空、没有正文 —— 这正是「付了钱买回空壳」的形状。"""
    scores = fetch_case_scores(
        FetchResult(url="https://x.example/", ok=True, text="Loading…"), CONTENT_CASE
    )
    assert scores["shell_page"] is True


def test_a_login_wall_returned_as_content_is_a_false_success() -> None:
    """``no_content`` 用例里任何一次「成功」都是假成功，哪怕认出了登录文案。

    这一条钉的是一个**曾经写反**的判断：把 ``blocked_marker`` 当成「读对了」的标志，
    于是返回登录页反而被记成成功。
    """
    scores = fetch_case_scores(
        FetchResult(url="https://x.example/", ok=True, text="Log in to continue"), WALL_CASE
    )
    assert scores["blocked_marker"] is True
    assert scores["shell_page"] is True


def test_a_reader_that_correctly_refuses_the_wall_is_not_penalised() -> None:
    scores = fetch_case_scores(
        FetchResult(url="https://x.example/", ok=False, error_code="no_content"), WALL_CASE
    )
    assert scores["shell_page"] is False


def test_contact_precision_and_recall_are_none_when_the_gold_is_silent() -> None:
    """pilot 的金标里没有邮箱 —— 于是这两个指标是 ``None``，不是 1.0。

    记成 1.0 会让「什么都没断言」看起来像「全对」。
    """
    bare = {"case_id": "F-BARE", "task_type": "static_html", "expected_outcome": "content",
            "gold_label": {"expected_text_markers": [], "min_text_chars": 0,
                           "expected_emails": [], "expected_phones": [], "expected_links": []}}
    scores = fetch_case_scores(
        FetchResult(url="https://x.example/", ok=True, text="y" * 500), bare
    )
    assert scores["email_precision"] is None
    assert scores["email_recall"] is None


def test_aggregate_fetch_keeps_no_content_cases_out_of_the_useful_rate() -> None:
    """``useful_content_rate`` 的分母是 content 用例；登录墙进 ``shell_page`` 那一格。"""
    good = fetch_case_scores(
        FetchResult(url="https://a.example/", ok=True,
                    text="Fédération Française de Football " + "x" * 800),
        CONTENT_CASE,
    )
    wall = fetch_case_scores(
        FetchResult(url="https://b.example/", ok=True, text="Log in to continue"), WALL_CASE
    )
    agg = aggregate_fetch([good, wall])
    assert agg["useful_content_rate"] == pytest.approx(1.0)
    assert agg["shell_page_false_success_rate"] == pytest.approx(1.0)
    assert aggregate_fetch([])["useful_content_rate"] is None


# --------------------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------------------

E2E_CASE = {
    "case_id": "E-1",
    "acceptance": {"min_entity_recall": 0.5, "min_verified_contacts": 1,
                   "evidence_required": True, "max_false_positive_rate": 0.5},
    "gold_label": {
        "entities": [{"entity_id": "OM", "canonical_name": "Olympique de Marseille",
                      "official_url": "https://www.om.fr/"}],
        "contacts": [{"entity_id": "OM", "value": "contact@om.fr", "kind": "email",
                      "evidence_url": "https://www.om.fr/contact"}],
    },
}


def test_entity_matched_by_official_url_counts_even_without_the_id() -> None:
    delivered = [DeliveredEntity(entity_id="", name="", official_url="https://om.fr")]
    scores = e2e_scores(delivered, E2E_CASE)
    assert scores["entity_recall"] == pytest.approx(1.0)
    assert scores["verified_entities"] == 1


def test_a_contact_without_an_evidence_url_is_not_verified() -> None:
    """没有页面的地址不算结果 —— 它算负债。"""
    delivered = [
        DeliveredEntity(
            entity_id="OM",
            name="Olympique de Marseille",
            contacts=({"kind": "email", "value": "contact@om.fr", "evidence_url": ""},),
        )
    ]
    scores = e2e_scores(delivered, E2E_CASE)
    assert scores["evidence_completeness"] == pytest.approx(0.0)
    assert scores["verified_contacts"] == 0
    assert scores["task_completed"] == 0.0


def test_a_delivery_that_claims_no_contact_is_not_blocked_by_the_evidence_rule() -> None:
    """``evidence_required`` 要求「每条声明的联系人都有页面」，而不是「必须有联系人」。

    曾经这里把 ``evidence_completeness is None`` 折成 0，于是每一个只交实体的
    用例都永远不可能验收 —— 那是评分器的性质，不是那次运行的性质。
    """
    delivered = [
        DeliveredEntity(
            entity_id="OM", name="Olympique de Marseille", official_url="https://www.om.fr/"
        )
    ]
    entity_only = dict(E2E_CASE, acceptance=dict(E2E_CASE["acceptance"], min_verified_contacts=0))
    scores = e2e_scores(delivered, entity_only)
    assert scores["evidence_completeness"] is None
    assert scores["task_completed"] == 1.0


def test_a_delivery_with_an_unsubstantiated_contact_still_fails_acceptance() -> None:
    """同一把尺子的另一半：声明了却没有证据，必须不完成。"""
    delivered = [
        DeliveredEntity(
            entity_id="OM",
            name="Olympique de Marseille",
            contacts=({"kind": "email", "value": "contact@om.fr", "evidence_url": ""},),
        )
    ]
    assert e2e_scores(delivered, E2E_CASE)["task_completed"] == 0.0


def test_an_invented_entity_lands_in_the_false_positive_rate() -> None:
    delivered = [
        DeliveredEntity(entity_id="OM", name="Olympique de Marseille"),
        DeliveredEntity(entity_id="GHOST", name="Invented FC"),
    ]
    scores = e2e_scores(delivered, E2E_CASE)
    assert scores["false_positive_rate"] == pytest.approx(0.5)
    assert scores["entity_precision"] == pytest.approx(0.5)


# --------------------------------------------------------------------------------------
# Ensemble
# --------------------------------------------------------------------------------------


def test_ablation_reports_the_recall_lost_when_a_provider_is_removed() -> None:
    out = ablation({"p1": {"x", "y"}, "p2": {"x"}}, 2)
    assert out["p1"]["recall_drop"] == pytest.approx(0.5)  # y is p1-only
    assert out["p2"]["recall_drop"] == pytest.approx(0.0)  # p2 adds nothing


def test_pareto_front_drops_dominated_points() -> None:
    points = [
        {"q": 0.8, "c": 1.0},  # dominated by the third
        {"q": 0.5, "c": 0.1},  # cheapest
        {"q": 0.8, "c": 0.5},  # same quality, cheaper than the first
    ]
    assert pareto_front(points, "q", "c") == [1, 2]

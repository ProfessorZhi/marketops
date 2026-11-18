"""各厂商响应信封 → 统一结果。

这个模块是基准里唯一知道厂商形状的地方，所以它也是唯一会把「解析不了」伪装成
「没找到」的地方：一个不认识的信封如果返回空列表，计分卡上它与一次真的零结果
长得一模一样。判据因此是**必须抛错**。
"""

from __future__ import annotations

import pytest

from benchmark.runner.normalize import (
    NormalizeError,
    e2e_delivered,
    fetch_result,
    search_results,
    search_rows,
)


def test_the_benchmark_shape_is_accepted_first() -> None:
    """手写的 fixture 不必模仿任何一家的信封 —— 这是离线测试的成本优势。"""
    rows = search_rows("anything", {
        "results": [
            {"rank": 1, "title": "A", "url": "https://a.example/", "snippet": "s"},
            {"title": "no url"},  # dropped, not crashed
            {"rank": 9, "url": "https://b.example/"},
        ]
    })
    assert [r["url"] for r in rows] == ["https://a.example/", "https://b.example/"]
    assert rows[1]["rank"] == 9


def test_serper_organic_and_places() -> None:
    organic = {"organic": [{"title": "A", "link": "https://a.example/", "snippet": "s"}]}
    assert search_rows("serper", organic)[0]["url"] == "https://a.example/"
    places = {"places": [{"title": "P", "website": "https://p.example/", "category": "pool"}]}
    assert search_rows("serper", places)[0]["url"] == "https://p.example/"


def test_serpapi_organic_and_local() -> None:
    organic = {"organic_results": [{"title": "A", "link": "https://a.example/"}]}
    assert search_rows("serpapi", organic)[0]["url"] == "https://a.example/"
    local = {"local_results": [{"title": "L", "link": "https://l.example/"}]}
    assert search_rows("serpapi", local)[0]["url"] == "https://l.example/"


@pytest.mark.parametrize("provider", ["deepseek", "tinyfish"])
def test_deepseek_and_tinyfish_share_a_shape(provider: str) -> None:
    body = {"results": [{"title": "R", "url": "https://r.example/", "summary": "s"}]}
    rows = search_rows(provider, body)
    assert rows[0]["url"] == "https://r.example/"


def test_an_unknown_envelope_raises_instead_of_returning_nothing() -> None:
    """认不出来就红。返回 ``[]`` 会让一次解析失败在报告里变成「这家什么都没找到」。"""
    with pytest.raises(NormalizeError, match="unrecognised search envelope"):
        search_rows("brand-new", {"items": []})


def test_empty_results_are_a_real_zero_not_an_error() -> None:
    """``organic: []`` 是「查了，确实没有」。它与信封不认识的处置必须不同。"""
    assert search_rows("serper", {"organic": []}) == []


def test_search_results_assigns_rank_from_position() -> None:
    body = {"results": [{"url": "https://a.example/"}, {"url": "https://b.example/"}]}
    rows = search_results("x", body)
    assert [r.rank for r in rows] == [1, 2]


def test_fetch_body_must_declare_ok() -> None:
    """没有 ``ok`` 的 fetch 记录无法区分成功与失败，必须报错而不是默认成功。"""
    with pytest.raises(NormalizeError, match="'ok'"):
        fetch_result("http", {"text": "..."}, "https://x.example/")


def test_fetch_result_keeps_the_error_code() -> None:
    got = fetch_result(
        "crawl4ai",
        {"ok": False, "status": 403, "error_code": "rate_limited"},
        "https://x.example/",
    )
    assert got.ok is False
    assert got.error_code == "rate_limited"
    assert got.status == 403


def test_e2e_body_must_hold_an_entities_list() -> None:
    with pytest.raises(NormalizeError, match="entities"):
        e2e_delivered("cfg", {"rows": []})
    assert e2e_delivered("cfg", {"entities": []}) == {"entities": []}

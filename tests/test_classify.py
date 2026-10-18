"""响应分类的测试。

分类是所有下游决策的输入：升级浏览器、记 blocked、还是提取内容。
分类错了，后面全错 —— 所以这里对每一类都给出正例与**反例**
（尤其是「正常页面上写着 log in，不能被误判成需要登录」）。
"""

from __future__ import annotations

import pytest

from marketops.adapters.classify import (
    assess_http_response,
    classify_block,
    looks_like_empty_shell,
    visible_text,
)
from marketops.domain.enums import BlockReason, FetchOutcome

NORMAL_HTML = """
<html><body>
  <h1>Example Coatings Ltd</h1>
  <p>We supply decorative coatings across the UK.</p>
  <p>Contact our sales team for trade enquiries and specification support.</p>
  <ul><li>Trade counter open Monday to Friday.</li></ul>
</body></html>
"""

SPA_SHELL = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'

CLOUDFLARE_CHALLENGE = """
<html><head><title>Just a moment...</title></head>
<body><div class="cf-chl-..."">Checking your browser before accessing</div></body></html>
"""


def _assess(status: int, body: str, content_type: str = "text/html") -> tuple:
    return assess_http_response(status_code=status, content_type=content_type, text=body)


# --- 正常路径 -----------------------------------------------------------------


def test_normal_html_is_ok() -> None:
    assert _assess(200, NORMAL_HTML) == (FetchOutcome.OK, None)


def test_pdf_is_ok() -> None:
    outcome, block = _assess(200, "%PDF-1.7 ...", content_type="application/pdf")
    assert outcome is FetchOutcome.OK
    assert block is None


# --- 空壳页 -------------------------------------------------------------------


def test_spa_shell_is_empty_shell() -> None:
    assert _assess(200, SPA_SHELL) == (FetchOutcome.EMPTY_SHELL, None)


def test_looks_like_empty_shell_needs_both_signals() -> None:
    """只有「文本少」不足以判空壳 —— 短页面是合法的。"""
    assert looks_like_empty_shell(SPA_SHELL) is True
    assert looks_like_empty_shell("<html><body><p>Short but real.</p></body></html>") is False
    assert looks_like_empty_shell(NORMAL_HTML) is False


def test_visible_text_strips_scripts_and_styles() -> None:
    html = "<html><style>p{color:red}</style><body><p>Hello</p><script>x=1</script></body></html>"
    text = visible_text(html)
    assert "Hello" in text
    assert "color:red" not in text
    assert "x=1" not in text


# --- 受阻（终态）---------------------------------------------------------------


def test_waf_challenge_is_blocked() -> None:
    outcome, block = _assess(200, CLOUDFLARE_CHALLENGE)
    assert outcome is FetchOutcome.BLOCKED
    assert block is BlockReason.WAF_CHALLENGE


def test_http_403_is_blocked() -> None:
    outcome, block = _assess(403, "<html><body>Forbidden</body></html>")
    assert outcome is FetchOutcome.BLOCKED
    assert block is BlockReason.HTTP_403


def test_http_429_is_blocked() -> None:
    outcome, block = _assess(429, "<html><body>Too many requests</body></html>")
    assert outcome is FetchOutcome.BLOCKED
    assert block is BlockReason.HTTP_429


def test_paywall_is_blocked() -> None:
    outcome, block = _assess(200, "<p>Subscribe to read the full article</p>")
    assert outcome is FetchOutcome.BLOCKED
    assert block is BlockReason.PAYWALL


def test_waf_wins_over_login_marker() -> None:
    """challenge 页上常同时写着 \"log in\"。先判登录会把「被拦截」误报成「需要登录」。"""
    body = "<html><title>Just a moment...</title><body>Please log in to continue</body></html>"
    assert classify_block(status_code=200, text=body) is BlockReason.WAF_CHALLENGE


def test_plain_page_is_not_blocked() -> None:
    """反例：普通主页不触发任何标记。"""
    assert classify_block(status_code=200, text="<p>Trade portal homepage</p>") is None


def test_login_marker_phrase_alone_is_enough() -> None:
    """边界：页面上出现 ``LOGIN_MARKERS`` 的**精确短语**就判需登录 ——
    即使它读起来只是一个正常的「登录后查看账户」提示。

    这条锁的是「匹配标记才触发」，不是「提到登录才触发」：判据是短语本身。
    原来的写法只在注释里说这件事，而断言用的是另一个不含标记的页面 ——
    于是「提到登录不触发」这句声明既没有被锁定，也不成立。
    """
    body = "<html><body><h1>Trade Portal</h1><p>Please log in to view your account.</p></html>"
    assert classify_block(status_code=200, text=body) is BlockReason.LOGIN_REQUIRED


# --- 其他状态码 ---------------------------------------------------------------


@pytest.mark.parametrize("status", [404, 410])
def test_not_found_is_terminal(status: int) -> None:
    assert _assess(status, "<html><body>Not found</body></html>") == (
        FetchOutcome.NOT_FOUND,
        None,
    )


def test_server_error_is_error_not_blocked() -> None:
    """500 是服务端故障，不属于「不可公开访问」，两者的处置不同。"""
    outcome, block = _assess(500, "<html><body>Server error</body></html>")
    assert outcome is FetchOutcome.ERROR
    assert block is None


def test_empty_non_html_body_is_error() -> None:
    assert _assess(200, "   ", content_type="text/plain")[0] is FetchOutcome.ERROR


# --- 状态码未观测到（``None``，而 ``None`` 不是 200）-----------------------------
#
# 浏览器那一层有时报不出状态码（库换过字段名、渲染根本没经过 HTTP）。以前调用点
# 写的是 ``status_code or 200``，于是「不知道」被折成一个**成功**的读数。这一组
# 用例把折算之后的判据钉住：``None`` 按**正文可用性**分类，而不是按 200 分类。


def test_an_unobserved_status_falls_back_to_body_availability() -> None:
    """正文可用 → ``OK``。这不是「HTTP 正常」，是「没有异常可判」。"""
    assert _assess(None, NORMAL_HTML) == (FetchOutcome.OK, None)


def test_an_unobserved_status_still_escalates_on_an_empty_shell() -> None:
    """正文是空壳 → 仍然要升级浏览器。

    ``None`` 不走 200 那条路也必须走到这里：把「不知道」当成功而跳过升级，
    等于让一次 SPA 首屏永远停在空壳上，而报告上它是一次成功的抓取。
    """
    assert _assess(None, SPA_SHELL) == (FetchOutcome.EMPTY_SHELL, None)


def test_an_unobserved_status_does_not_swallow_a_challenge_page() -> None:
    """拦截指纹是从**正文**读的，与状态码无关 —— 一条明确的「你被拦住了」
    不该因为状态码没量到而漏过去。"""
    outcome, block = _assess(None, CLOUDFLARE_CHALLENGE)
    assert outcome is FetchOutcome.BLOCKED
    assert block is BlockReason.WAF_CHALLENGE


def test_an_unobserved_status_cannot_produce_a_status_code_refusal() -> None:
    """反例，也是这一格最重要的那条：**没有状态码就不许有状态码结论**。

    ``403`` 之所以是 ``HTTP_403``，是因为状态码说的是 403 —— 不是因为它正文里
    写着 "Forbidden"。少了状态码，这两个理由（``NOT_FOUND`` 与 ``>= 400``）
    就没有对应物，而不是「约等于 200」，也不是「约等于 403」。
    """
    assert _assess(None, "<html><body><p>Forbidden</p></body></html>") == (FetchOutcome.OK, None)
    assert classify_block(status_code=None, text="<html><body><p>Not found</p></body></html>") is None


def test_an_unobserved_status_with_no_body_is_error() -> None:
    assert _assess(None, "   ", content_type="text/plain")[0] is FetchOutcome.ERROR

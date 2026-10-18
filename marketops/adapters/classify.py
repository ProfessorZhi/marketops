"""抓取结果的分类：把「技术性可渲染失败」与「不可公开访问」分开。

这两类失败的处置**完全相反**，混为一谈是这类 pipeline 最常见的事故来源：

* 技术性失败（空壳页 / 需要 JS）→ 升级 browser 重试一次。
* 不可公开访问（WAF challenge / 403 / paywall / 需登录）→ **终态**，
  改走其他公开来源。不允许换 UA、换代理、绕 challenge。

本模块是纯函数集合：给同样的输入永远给同样的输出，因此升级决策可以被穷举测试。
"""

from __future__ import annotations

import re

from ..domain.enums import BlockReason, FetchOutcome

#: WAF / bot-challenge 的指纹。只用于**识别并放弃**，不用于绕过。
WAF_MARKERS: tuple[str, ...] = (
    "just a moment",
    "checking your browser",
    "attention required",
    "enable javascript and cookies to continue",
    "cf-chl-",
    "cf_chl_opt",
    "px-captcha",
    "captcha-delivery.com",
    "incapsula incident id",
    "akamai bot manager",
    "please verify you are a human",
    "ddos protection by",
)

LOGIN_MARKERS: tuple[str, ...] = (
    "sign in to continue",
    "please log in to view",
    "login required",
    "you must be logged in",
)

PAYWALL_MARKERS: tuple[str, ...] = (
    "subscribe to read",
    "this content is for subscribers",
    "subscribers only",
)

#: 空壳页判定用：真正承载内容的标签。
_TEXT_BEARING = re.compile(r"<(p|article|li|h1|h2|h3|table|section)\b", re.IGNORECASE)
_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

#: 可见正文低于这个字符数、且以 SPA 挂载点为主，判为空壳。
EMPTY_SHELL_TEXT_THRESHOLD = 200

_SPA_MOUNT_MARKERS: tuple[str, ...] = (
    'id="root"',
    "id='root'",
    'id="app"',
    "id='app'",
    'id="__next"',
    "data-reactroot",
    "ng-app",
    "id=\"__nuxt\"",
)


def visible_text(html: str) -> str:
    """粗略提取可见正文。只用于「内容量」判断，不用于抽取。"""
    without_scripts = _SCRIPT_STYLE.sub(" ", html)
    return _WS.sub(" ", _TAG.sub(" ", without_scripts)).strip()


def looks_like_empty_shell(html: str) -> bool:
    """返回 200 但正文为空壳（典型 SPA 首屏）。

    判定条件同时看「内容太少」和「存在 SPA 挂载点」——只看长度会把
    正常的短页面误判成空壳。
    """
    text = visible_text(html)
    if len(text) >= EMPTY_SHELL_TEXT_THRESHOLD:
        return False
    lowered = html.lower()
    if any(marker in lowered for marker in _SPA_MOUNT_MARKERS):
        return True
    # 完全没有正文标签也是一种空壳（例如只返回了一个 <html><body></body>）
    return len(_TEXT_BEARING.findall(html)) == 0


def is_html(content_type: str, text: str = "") -> bool:
    if "html" in content_type.lower():
        return True
    if content_type:
        return False
    head = text[:2048].lower()
    return "<html" in head or "<!doctype html" in head


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle in haystack for needle in needles)


def classify_block(*, status_code: int | None, text: str) -> BlockReason | None:
    """判定是否为「不可公开访问」。返回 None 表示不是。

    顺序有讲究：先看 WAF 指纹再看登录/paywall —— WAF 页面上常常也写着
    "log in"，先判登录会把「被拦截」误报成「需要登录」。
    """
    lowered = text[:200_000].lower()

    if _contains_any(lowered, WAF_MARKERS):
        return BlockReason.WAF_CHALLENGE
    if status_code == 403:
        return BlockReason.HTTP_403
    if status_code == 429:
        return BlockReason.HTTP_429
    if status_code == 401:
        return BlockReason.LOGIN_REQUIRED
    if _contains_any(lowered, PAYWALL_MARKERS):
        return BlockReason.PAYWALL
    if _contains_any(lowered, LOGIN_MARKERS):
        return BlockReason.LOGIN_REQUIRED
    return None


def assess_http_response(
    *,
    status_code: int | None,
    content_type: str,
    text: str,
) -> tuple[FetchOutcome, BlockReason | None]:
    """把一次 HTTP 响应归到 ``FetchOutcome``。

    这是升级决策的唯一入口。返回 ``(outcome, block_reason)``。

    ## ``status_code`` 是 ``int | None``，而 ``None`` 不是 200

    浏览器那一层有时报不出状态码（库换过字段名、渲染根本没经过 HTTP）。以前
    调用点写的是 ``status_code or 200``，于是三种不同的处境被折成一个**成功**的
    读数：一次「不知道」会被判成「HTTP 正常」，而报告上它与一个真的量到的 200
    同形。这一格现在收 ``None``，并按**正文可用性**分类：

    * 正文可用 → ``OK`` / ``EMPTY_SHELL``（该升级的还是升级）；
    * 正文不可用 → ``ERROR``。

    正文里的拦截指纹（WAF / 登录 / paywall）与状态码无关，仍然照判 —— 它们
    本来就是从正文读的，而 ``None`` 不该让一条明确的「你被拦住了」漏过去。
    ``404/410`` 与 ``>= 400`` 两条则**没有** ``None`` 的对应物：没有状态码时
    它们不成立，而不是「约等于 200」。

    ``None`` 该不该在报告上继续往上走，由调用方决定：``FetchResult.status_code``
    是 ``int | None``，它把 ``None`` 原样带上去，而不是在这里补一个数。
    """
    if status_code in (404, 410):
        return FetchOutcome.NOT_FOUND, None

    block = classify_block(status_code=status_code, text=text)
    if block is not None:
        return FetchOutcome.BLOCKED, block

    if status_code is not None and status_code >= 400:
        return FetchOutcome.ERROR, None

    if is_html(content_type, text):
        if looks_like_empty_shell(text):
            return FetchOutcome.EMPTY_SHELL, None
        return FetchOutcome.OK, None

    # 非 HTML（PDF / 纯文本 / JSON）：能拿到就是 OK。
    if text.strip():
        return FetchOutcome.OK, None
    return FetchOutcome.ERROR, None


__all__ = [
    "WAF_MARKERS",
    "LOGIN_MARKERS",
    "PAYWALL_MARKERS",
    "EMPTY_SHELL_TEXT_THRESHOLD",
    "visible_text",
    "looks_like_empty_shell",
    "is_html",
    "classify_block",
    "assess_http_response",
]

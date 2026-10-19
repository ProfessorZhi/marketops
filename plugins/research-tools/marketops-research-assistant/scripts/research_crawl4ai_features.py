#!/usr/bin/env python3
"""Crawl4AI 本地能力的**特性层**：抽取、深爬、seeder、会话、产物与证据清单。

## 这一层的边界（先说清它不是什么）

本模块**不发起任何网络动作**，也**不读任何凭据**。它只做四件事：

1. 把 Agent 给的「我想抽什么」翻译成一个**已审核的、闭集的**抽取规格；
2. 把种子 URL 与上限翻译成一个**有界**的爬取计划（页数 / 深度 / 域名 / 字节 / 秒数）；
3. 对**已经取回的字节**做确定性的 CSS / XPath / 正则抽取；
4. 把每一页的处置写成一张 append-only 的证据清单。

真正的浏览器与辅助 HTTP 在 ``research_adapter_crawl4ai.py`` 里，并且**必须**经出口。
这一层被单独拆出来，是因为它是可穷举、可离线判死的部分 —— 网络那一半不可穷举，
把一个不可穷举的东西和一个可穷举的东西写在同一个文件里，判据就只能长在最外层。

## Crawl4AI 本地是 **T1**，不是零成本

owner brief §4 把「Crawl4AI 本地浏览器抽取」放在 T1，而 CONVDEC §4 紧接着写死了
理由：**「For local Crawl4AI, include CPU/time/pages/bandwidth and storage ceilings
even if external API price is zero.」** 所以本模块里**没有**「免费的抓取」这种取值：

* :class:`CrawlResourceBudget` 的每一格都必须显式给出，且必须 ≤ :data:`HARD_CEILINGS`；
  给不出上限的请求得到 :attr:`CrawlRefusal.UNBOUNDED_CRAWL_REFUSED`，而不是一个
  「合理缺省值」—— 缺省值在这里的失败方式是安静的：一次没有上限的深爬在报告上
  与一次有界的深爬长得一样，区别只在**它什么时候停**。
* :class:`BudgetMeter` 记的是**配额单位**（页、字节、秒、产物件数），**不是 USD**。
  契约 §2 的硬约束是金额与配额不得相加、不得互换；这一层连 USD 都不出现，
  所以它结构上不可能违反那一条。

## 三条禁止（写在拒绝码里，不写在文档里）

| 禁止 | 拒绝码 | 为什么 |
|---|---|---|
| Agent 提供任意 ``js_code`` / 页面内脚本 | :attr:`CrawlRefusal.ARBITRARY_JS_FORBIDDEN` | 一段 Agent 提供的 JS 在目标站点的上下文里执行，等于把「只读抓取」变成「在别人页面上跑我们的代码」；出口看得见连了哪个 host，看不见隧道里执行了什么 |
| 站点登录 / profile / 会话注入 | :attr:`CrawlRefusal.SITE_LOGIN_FORBIDDEN` | T4（brief §4）；本 Wave 的授权是匿名公开浏览 |
| 无边界的深爬 | :attr:`CrawlRefusal.UNBOUNDED_CRAWL_REFUSED` | T1 的成本是**我们自己的** CPU、带宽与磁盘；没有上限的爬虫是对本机与目标站点的双重消耗 |

拒绝码是**闭集**（:class:`CrawlRefusal`）。契约 §5.3 的 ``reason_code`` 闭集里有
``CRAWL4AI_LOCAL_NOT_INSTALLED`` 与 ``EGRESS_REFUSED``；本模块新增的名字全部带
``CRAWL_`` / ``ARTIFACT_`` / ``EXTRACTION_`` 前缀，**不与那一张表混用** ——
它们要在集成时由 INTEGRATOR 决定是把哪几个并入 §5.3，哪一个留在 adapter 内部。
见 ``docs/wave1-coordination/status/W-E.md`` 的 ``CONTRACT-CHANGE-REQUEST``。

## 抽取为什么是自己实现的，而不是调用 Crawl4AI 的策略

仓库**没有**任何 HTML 解析依赖（``lxml`` / ``bs4`` / ``soupsieve`` 都不在依赖树里，
``beautifulsoup4`` 是被刻意删掉的），而本 Wave 不允许给插件加新依赖。于是这一层用
stdlib 的 ``html.parser`` 建一棵**有界**的 DOM，并实现一个**显式声明**的 CSS / XPath
子集。这个子集是判据而不是近似值：

* 不支持的写法得到 :attr:`CrawlRefusal.SELECTOR_UNSUPPORTED`，
  **绝不**退化成「匹配不到」—— 「这个选择器我们不支持」与「这一页没有这个字段」
  在下游是同一种空值，而它们的处置完全不同（一个要改选择器，一个要改事实判断）。
* 装了真 Crawl4AI SDK 时，adapter 优先把它自己的抽取策略交给 SDK；
  本模块的引擎是**确定性回退**，也是判据那一侧唯一的可穷举面。

## 证据清单为什么在这里

契约 §3.5 的 ``EvidenceEnvelope`` 要 ``content_sha256``、``retrieved_at``、
``extraction_method``、``caveats``；这一层产出其中的形状，但不产生 ``ledger_id``
（那是 B 的 ``UsageLedger``）。**没有 ledger 链接的证据不可导出**是契约的硬要求，
所以本模块产出的清单行**故意**留一个空的 ``ledger_id`` 字段，由一个
:meth:`EvidenceManifest.require_bound` 在导出前判死。
"""
from __future__ import annotations

import hashlib
import re
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from html.parser import HTMLParser
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "ArtifactPolicy",
    "BudgetMeter",
    "CrawlPlan",
    "CrawlRefusal",
    "CrawlResourceBudget",
    "CRAWL4AI_CAPABILITY_REFERENCE",
    "EvidenceManifest",
    "ExtractionMode",
    "ExtractionOutcome",
    "ExtractionSpec",
    "FieldSpec",
    "HARD_CEILINGS",
    "ManifestRow",
    "PROVIDER",
    "SelectorKind",
    "SessionPolicy",
    "TIER",
    "TRANSPORT",
    "apply_extraction",
    "content_sha256",
    "domain_of",
    "domain_permitted",
    "forbidden_feature_reasons",
    "normalise_allowed_domains",
    "parse_artifact_policy",
    "parse_extraction_spec",
    "parse_session_policy",
    "parse_sitemap",
    "plan_deep_crawl",
    "render_reference_markdown",
    "robots_allowed",
]

PROVIDER = "crawl4ai"
#: 契约 §6 钉死的 transport，不得自行更改。
TRANSPORT = "local_sdk"
#: owner brief §4 / CONVDEC §1：本地浏览器抽取是 T1。
TIER = "T1"


class CrawlRefusal(str, Enum):
    """本层的拒绝理由**闭集**。

    与 ``security/network.py::TargetRefusal`` 同一条纪律：开放字符串的第一个拼错的
    名字会静静开出一条新类别，而下游按类别分支的地方会落进 ``else``。
    """

    ARBITRARY_JS_FORBIDDEN = "ARBITRARY_JS_FORBIDDEN"
    """Agent 提供了页面内脚本 / ``js_code`` / ``eval`` 一类字段。"""

    SITE_LOGIN_FORBIDDEN = "SITE_LOGIN_FORBIDDEN"
    """请求里带了登录、凭据、profile、cookie 注入或验证码绕过的意图。"""

    UNBOUNDED_CRAWL_REFUSED = "UNBOUNDED_CRAWL_REFUSED"
    """上限缺失，或超过 :data:`HARD_CEILINGS`。"""

    OFF_DOMAIN_SEED_REFUSED = "OFF_DOMAIN_SEED_REFUSED"
    """种子/链接落在批准域名之外。深爬**只**在一个域集合内游走。"""

    CRAWL_PAGE_CAP_EXCEEDED = "CRAWL_PAGE_CAP_EXCEEDED"
    CRAWL_DEPTH_CAP_EXCEEDED = "CRAWL_DEPTH_CAP_EXCEEDED"
    CRAWL_DOMAIN_CAP_EXCEEDED = "CRAWL_DOMAIN_CAP_EXCEEDED"
    CRAWL_BYTE_CAP_EXCEEDED = "CRAWL_BYTE_CAP_EXCEEDED"
    CRAWL_TIME_CAP_EXCEEDED = "CRAWL_TIME_CAP_EXCEEDED"

    ARTIFACT_CAP_EXCEEDED = "ARTIFACT_CAP_EXCEEDED"
    """截图 / PDF / 缓存件数或字节数超上限。"""

    EXTRACTION_SPEC_INVALID = "EXTRACTION_SPEC_INVALID"
    """抽取规格的形状不成立（未知键、缺字段、类型不对）。"""

    SELECTOR_UNSUPPORTED = "SELECTOR_UNSUPPORTED"
    """选择器不在本层显式支持的子集内。**不**退化成「匹配不到」。"""

    SESSION_POLICY_VIOLATION = "SESSION_POLICY_VIOLATION"
    """会话策略非法（超时长为负、要求持久化凭据等）。"""

    EVIDENCE_NOT_BOUND = "EVIDENCE_NOT_BOUND"
    """证据行没有绑定 ledger —— 契约 §3.5：无 ledger 链接的证据不可导出。"""


class CrawlRefusalError(ValueError):
    """带**闭集理由码**的拒绝。

    继承 ``ValueError`` 是为了与插件既有的理由码式拒绝
    （``research_provider_extensions.py`` 全用 ``raise ValueError("CODE")``）同形，
    集成时不需要两套异常处置。``str(exc)`` 是理由码本身，与既有一致。
    """

    def __init__(self, refusal: CrawlRefusal, detail: str = "",
                 code: str | None = None) -> None:
        super().__init__(code or refusal.value)
        self.refusal = refusal
        self.detail = detail
        #: 契约 §5.3 那一张表里的理由码。它比 :attr:`refusal` **宽**：
        #: ``CrawlRefusal`` 判的是「这一层的参数不行」，而 §5.3 的表里还有
        #: ``EGRESS_REFUSED`` / ``CRAWL4AI_LOCAL_NOT_INSTALLED`` 这类
        #: 「上游或出口说不行」。两者在下游处置不同（前者改请求、后者换来源），
        #: 所以分开两格，而不是把 §5.3 的名字硬塞进这一层的闭集。
        self.code = code or refusal.value


def _refuse(refusal: CrawlRefusal, detail: str = "") -> None:
    raise CrawlRefusalError(refusal, detail)


# ---------------------------------------------------------------------------
# 资源上限：T1 的成本是我们自己的机器
# ---------------------------------------------------------------------------

#: 任何一次本地爬取的**硬顶**。上限超过这里的请求被拒，而不是被裁剪 ——
#: 裁剪会让「我要爬 5000 页」与「我要爬 120 页」得到同一个行为，而报告上
#: 那一格写的是请求者填的那个数。
HARD_CEILINGS: Mapping[str, int | float] = {
    "max_pages": 200,
    "max_depth": 4,
    "max_domains": 4,
    "max_bytes": 64 * 1024 * 1024,
    "max_seconds": 900.0,
    "max_browser_seconds": 600.0,
    "max_artifacts": 200,
}


#: ``0`` 是一个**合法且更严**的取值的那几格，而不是「没填」。
#:
#: ``max_depth=0`` 说的是「只抓种子，不跟链接」—— 它比 ``1`` 更保守。
#: 把它一起判成「没给上限」会让一次刻意的最小抓取无法表达，
#: 于是调用方会写 ``1`` 来绕过，而那次抓取就真的会多跟一层。
_ALLOW_ZERO: frozenset[str] = frozenset({"max_depth"})


@dataclass(frozen=True)
class CrawlResourceBudget:
    """一次本地爬取的资源上限。**每一格都必须显式给出。**

    没有默认值是有意的：``max_pages=None`` 那种「不填就是不限」的接口，
    它的失败方式是一次没人察觉的无界爬取。这里不填（``0`` 或负数）一样是拒绝 ——
    唯一例外是 :data:`_ALLOW_ZERO` 里那几格，``0`` 在那里表达的是**更紧**的界。
    """

    max_pages: int
    max_depth: int
    max_domains: int
    max_bytes: int
    max_seconds: float
    max_browser_seconds: float
    max_artifacts: int

    def __post_init__(self) -> None:
        for name, ceiling in HARD_CEILINGS.items():
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                _refuse(CrawlRefusal.UNBOUNDED_CRAWL_REFUSED,
                        f"{name} 不是数字：{value!r}")
            if value < 0 or (value == 0 and name not in _ALLOW_ZERO):
                _refuse(
                    CrawlRefusal.UNBOUNDED_CRAWL_REFUSED,
                    f"{name}={value!r}。上限必须是一个正数 —— 「不填」不是「不限」，"
                    "它在报告上与「有界」同形，区别只在什么时候停。",
                )
            if value > ceiling:
                _refuse(
                    CrawlRefusal.UNBOUNDED_CRAWL_REFUSED,
                    f"{name}={value!r} 超过硬顶 {ceiling}。超过硬顶的请求被拒，"
                    "不被裁剪：裁剪会让两个不同的请求得到同一个行为。",
                )

    def as_row(self) -> dict[str, int | float]:
        """给证据清单用的一行。**这是配额单位，不是金额。**"""
        return {
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "max_domains": self.max_domains,
            "max_bytes": self.max_bytes,
            "max_seconds": self.max_seconds,
            "max_browser_seconds": self.max_browser_seconds,
            "max_artifacts": self.max_artifacts,
        }


@dataclass
class BudgetMeter:
    """把消耗记在**配额单位**上。**这里没有 USD，也没有汇率。**

    契约 §2：「配额单位（credits / requests / steps / tokens / pages）与 USD
    不得相加、不得互换」。本类连 USD 字段都没有，所以那条不变量在这里是结构性成立的,
    而不是靠一处 ``if`` 守着的。
    """

    budget: CrawlResourceBudget
    pages: int = 0
    bytes_read: int = 0
    seconds: float = 0.0
    browser_seconds: float = 0.0
    artifacts: int = 0
    domains: set[str] = field(default_factory=set)

    # -- 读 ---------------------------------------------------------------
    def remaining_pages(self) -> int:
        return self.budget.max_pages - self.pages

    def remaining_bytes(self) -> int:
        return self.budget.max_bytes - self.bytes_read

    def exhausted(self) -> bool:
        return (self.remaining_pages() <= 0
                or self.remaining_bytes() <= 0
                or self.seconds >= self.budget.max_seconds)

    def snapshot(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "bytes": self.bytes_read,
            "seconds": round(self.seconds, 3),
            "browser_seconds": round(self.browser_seconds, 3),
            "artifacts": self.artifacts,
            "domains": tuple(sorted(self.domains)),
            "limits": self.budget.as_row(),
            "units": "pages|bytes|seconds|artifacts",
        }

    # -- 写：每一格都**先判后记** ------------------------------------------
    def consume_page(self, url: str, *, domain: str) -> None:
        """记一页。域名上限在**进入队列之前**判，见 :func:`plan_deep_crawl`。"""
        if self.remaining_pages() <= 0:
            _refuse(CrawlRefusal.CRAWL_PAGE_CAP_EXCEEDED,
                    f"已抓 {self.pages} 页，上限 {self.budget.max_pages}。")
        self.pages += 1
        self.domains.add(domain)

    def consume_bytes(self, count: int) -> None:
        if count < 0:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID, "字节数不能为负")
        if count > self.remaining_bytes():
            _refuse(CrawlRefusal.CRAWL_BYTE_CAP_EXCEEDED,
                    f"本次 {count} 字节会把总量推到 {self.bytes_read + count}，"
                    f"上限 {self.budget.max_bytes}。")
        self.bytes_read += count

    def consume_seconds(self, seconds: float) -> None:
        if seconds < 0:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID, "秒数不能为负")
        self.seconds += seconds
        if self.seconds > self.budget.max_seconds:
            _refuse(CrawlRefusal.CRAWL_TIME_CAP_EXCEEDED,
                    f"累计 {self.seconds:.3f}s 超过上限 {self.budget.max_seconds}s。")

    def consume_browser_seconds(self, seconds: float) -> None:
        if seconds < 0:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID, "秒数不能为负")
        self.browser_seconds += seconds
        if self.browser_seconds > self.budget.max_browser_seconds:
            _refuse(CrawlRefusal.CRAWL_TIME_CAP_EXCEEDED,
                    f"浏览器累计 {self.browser_seconds:.3f}s 超过上限 "
                    f"{self.budget.max_browser_seconds}s。")

    def consume_artifact(self) -> None:
        if self.artifacts >= self.budget.max_artifacts:
            _refuse(CrawlRefusal.ARTIFACT_CAP_EXCEEDED,
                    f"已产出 {self.artifacts} 件，上限 {self.budget.max_artifacts}。")
        self.artifacts += 1


# ---------------------------------------------------------------------------
# 抽取规格：把「我想抽什么」翻译成一个已审核的形状
# ---------------------------------------------------------------------------

class SelectorKind(str, Enum):
    CSS = "css"
    XPATH = "xpath"
    REGEX = "regex"


class ExtractionMode(str, Enum):
    MARKDOWN = "markdown"
    TEXT = "text"
    HTML = "html"
    STRUCTURED = "structured"


#: **一律拒绝**的键。它们是「把只读抓取变成在别人页面上执行我们的代码」或
#: 「从匿名公开浏览变成登录浏览」的入口。键名按 Crawl4AI 与其生态里的真实拼法写，
#: 因为要挡的是**Agent 会写的那个键**，不是我们发明的名字。
_FORBIDDEN_JS_KEYS = frozenset({
    "js_code", "javascript", "js", "script", "scripts", "eval",
    "on_load", "onload", "js_only", "wait_for_js", "execute_script",
    "navigate_and_execute", "inject_script",
})
_FORBIDDEN_LOGIN_KEYS = frozenset({
    "login", "signin", "sign_in", "username", "user_name", "password", "passwd",
    "credentials", "credential", "cookies", "cookie", "auth", "authorization",
    "session_token", "token", "profile", "profiles", "user_data_dir",
    "storage_state", "stealth", "undetected", "captcha", "solve_captcha",
    "bypass", "proxy_auth", "vault",
})
_FORBIDDEN_KEYS = _FORBIDDEN_JS_KEYS | _FORBIDDEN_LOGIN_KEYS

_FIELD_KEYS = frozenset({"name", "kind", "selector", "regex", "pattern",
                         "required", "many", "attribute"})
_SPEC_KEYS = frozenset({"mode", "fields", "max_chars", "text", "markdown", "html"})

#: 正则的长度上限。一条超长正则是 ReDoS 的常见载体；这一格挡不住所有回溯，
#: 但它把「随手贴一段一万字符的正则」变成一个当场可见的拒绝。
_MAX_REGEX_CHARS = 400


@dataclass(frozen=True)
class FieldSpec:
    """一个字段怎么抽。``kind`` 决定 ``selector`` 怎么解释。"""

    name: str
    kind: SelectorKind
    selector: str
    required: bool = False
    many: bool = False
    attribute: str | None = None


@dataclass(frozen=True)
class ExtractionSpec:
    """一次抽取的完整规格。``mode=structured`` 时 ``fields`` 必须非空。"""

    mode: ExtractionMode
    fields: tuple[FieldSpec, ...] = ()
    max_chars: int = 200_000

    def field_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.fields)


def forbidden_feature_reasons(payload: Any) -> tuple[CrawlRefusal, ...]:
    """在``payload``里找禁止的意图。**递归**，因为真实的注入会藏在嵌套里。

    返回**全部**命中的理由，而不是第一个：一次请求里同时带 ``js_code`` 与
    ``password`` 时，只报第一个会让第二次修复看起来是「又冒出来一个新的」。
    """
    found: list[CrawlRefusal] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                lowered = str(key).strip().casefold()
                if lowered in _FORBIDDEN_JS_KEYS:
                    if CrawlRefusal.ARBITRARY_JS_FORBIDDEN not in found:
                        found.append(CrawlRefusal.ARBITRARY_JS_FORBIDDEN)
                if lowered in _FORBIDDEN_LOGIN_KEYS:
                    if CrawlRefusal.SITE_LOGIN_FORBIDDEN not in found:
                        found.append(CrawlRefusal.SITE_LOGIN_FORBIDDEN)
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(payload)
    return tuple(found)


def parse_extraction_spec(payload: Mapping[str, Any] | None) -> ExtractionSpec:
    """把 Agent 给的抽取请求翻译成一个已审核的 :class:`ExtractionSpec`。

    **未知键是拒绝，不是忽略。** 忽略的后果是双向的：调用方以为某个选项生效了
    （它没有），而判据那一侧看不见「有一个我们没认出来的键」这件事发生过。
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, Mapping):
        _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                f"抽取规格必须是一个映射，收到的是 {type(payload).__name__}")

    for refusal in forbidden_feature_reasons(payload):
        _refuse(refusal, "抽取规格里出现了禁止的键。")

    unknown = {str(key) for key in payload} - _SPEC_KEYS
    if unknown:
        _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                f"未知的抽取规格键：{sorted(unknown)}。未知键被拒绝而不是忽略 —— "
                "忽略会让调用方以为一个没有生效的选项生效了。")

    raw_mode = str(payload.get("mode", ExtractionMode.MARKDOWN.value)).strip().casefold()
    try:
        mode = ExtractionMode(raw_mode)
    except ValueError:
        _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                f"未知的抽取模式 {raw_mode!r}；"
                f"允许的是 {sorted(item.value for item in ExtractionMode)}")

    raw_fields = payload.get("fields") or ()
    if not isinstance(raw_fields, (list, tuple)):
        _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID, "fields 必须是一个列表")
    if mode is ExtractionMode.STRUCTURED and not raw_fields:
        _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                "mode=structured 但 fields 是空的 —— 一次没有任何字段的结构抽取"
                "只会返回一个空字典，而它看起来与「这一页确实没有字段」同形。")

    fields: list[FieldSpec] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_fields):
        if not isinstance(raw, Mapping):
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                    f"fields[{index}] 必须是一个映射")
        for refusal in forbidden_feature_reasons(raw):
            _refuse(refusal, f"fields[{index}] 里出现了禁止的键。")
        unknown = {str(key) for key in raw} - _FIELD_KEYS
        if unknown:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                    f"fields[{index}] 有未知键：{sorted(unknown)}")
        name = str(raw.get("name", "")).strip()
        if not name:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID, f"fields[{index}] 缺 name")
        if name in seen:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                    f"字段名 {name!r} 重复 —— 后一个会覆盖前一个，而报告上看不出来")
        seen.add(name)

        raw_kind = str(raw.get("kind", SelectorKind.CSS.value)).strip().casefold()
        try:
            kind = SelectorKind(raw_kind)
        except ValueError:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                    f"fields[{index}].kind={raw_kind!r} 不是 "
                    f"{sorted(item.value for item in SelectorKind)} 之一")

        selector = raw.get("selector", raw.get("regex", raw.get("pattern", "")))
        selector = str(selector or "").strip()
        if not selector:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                    f"fields[{index}] 缺 selector")
        if kind is SelectorKind.REGEX and len(selector) > _MAX_REGEX_CHARS:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                    f"fields[{index}] 的正则有 {len(selector)} 字符，超过 "
                    f"{_MAX_REGEX_CHARS} —— 超长正则是回溯爆炸的常见载体")
        # 支持子集的判定放在**规格**这一层，不放抽取那一层。理由见
        # ``validate_selector`` 的 docstring：在抽取那一层，两件事已经同形了。
        validate_selector(kind, selector)

        attribute = raw.get("attribute")
        attribute = str(attribute).strip() if attribute is not None else None
        if attribute is not None and not re.fullmatch(r"[A-Za-z_:][-A-Za-z0-9_:.]*",
                                                      attribute):
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                    f"fields[{index}].attribute={attribute!r} 不像一个属性名")

        fields.append(FieldSpec(
            name=name,
            kind=kind,
            selector=selector,
            required=bool(raw.get("required", False)),
            many=bool(raw.get("many", False)),
            attribute=attribute,
        ))

    max_chars = payload.get("max_chars", 200_000)
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID, "max_chars 必须是正整数")

    return ExtractionSpec(mode=mode, fields=tuple(fields), max_chars=max_chars)


# ---------------------------------------------------------------------------
# 一个有界 DOM：stdlib html.parser + 显式声明的选择器子集
# ---------------------------------------------------------------------------

class _Node:
    """DOM 节点。``attrs`` 的键是小写属性名，值是字符串（布尔属性为空串）。"""

    __slots__ = ("tag", "attrs", "children", "text", "parent")

    def __init__(self, tag: str, attrs: Mapping[str, str]) -> None:
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: list[_Node] = []
        self.text: list[str] = []
        self.parent: _Node | None = None

    def iter(self) -> Iterable[_Node]:
        yield self
        for child in self.children:
            yield from child.iter()

    def own_text(self) -> str:
        return "".join(self.text)

    def all_text(self) -> str:
        parts = list(self.text)
        for child in self.children:
            parts.append(child.all_text())
        return "".join(parts)

    def classes(self) -> frozenset[str]:
        return frozenset((self.attrs.get("class") or "").split())


_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
})


class _DomBuilder(HTMLParser):
    """把 HTML 建成一棵树，**并且有界**。

    ``max_nodes`` 触顶时停止建树并置位 :attr:`truncated`。截断必须是一个**可见
    的读数**：一棵被悄悄截断的树在抽取那一侧表现为「这一页没有这个字段」，
    而那与事实无关。
    """

    def __init__(self, *, max_nodes: int) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self.stack: list[_Node] = [self.root]
        self.max_nodes = max_nodes
        self.nodes = 1
        self.truncated = False

    def _append(self, node: _Node) -> _Node:
        if self.nodes >= self.max_nodes:
            self.truncated = True
            return self.stack[-1]
        node.parent = self.stack[-1]
        self.stack[-1].children.append(node)
        self.nodes += 1
        return node

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.casefold(), {
            (key or "").casefold(): (value if value is not None else "")
            for key, value in attrs
        })
        node = self._append(node)
        if tag.casefold() not in _VOID_TAGS and not self.truncated:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.casefold() not in _VOID_TAGS and len(self.stack) > 1:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        target = tag.casefold()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == target:
                del self.stack[index:]
                return
        # 未配对的闭合标签：忽略而不是猜 —— 猜出来的树在抽取那一侧
        # 与真实结构无法区分。

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].text.append(data)


@dataclass(frozen=True)
class _Element:
    node: _Node


_SIMPLE_TOKEN = re.compile(
    r"^(?P<tag>\*|[A-Za-z][-A-Za-z0-9]*)?"
    r"(?P<rest>(?:[.#][-_A-Za-z0-9]+|\[[^\]]+\])*)$"
)
#: 支持的属性比较运算。``|=`` 一类的「近似」运算**不在**里面 —— 它们落进
#: else 分支时的失败方式与「这一页没有这个属性」同形，而那正是这一层要消灭的歧义。
_ATTR_OPERATORS = frozenset({"=", "~=", "^=", "$=", "*="})
#: ``[attr]`` / ``[attr=v]`` / ``[attr="v"]`` / ``[attr^="v"]`` / ``[attr$='v']``。
#: 值那一格刻意宽收（``.*?``）再在两处 :func:`_strip_quotes` 掉引号：把引号写进
#: 正则里会让 ``'42'`` 与 ``"42"`` 需要两条分支，而多出来那条分支的失败方式是
#: 「这个属性选择器就是匹配不到」—— 与「这一页没有这个属性」同形。
_ATTR_TOKEN = re.compile(r"^\[([-A-Za-z0-9_:.]+)(?:\s*([~^$*|]?=)\s*(.*?))?\]$")


def _strip_quotes(value: str | None) -> str:
    text = (value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


#: 一个已解析的复合条件：``(kind, name, operator, expected)``。
#: ``kind`` ∈ ``{"class", "id", "attr"}``；``attr`` 的 ``operator`` 为 ``None``
#: 表示「属性存在即可」，``"~="`` 一类的运算留给求值那一半。
Condition = tuple[str, str, str | None, str | None]


def parse_compound(token: str) -> tuple[str | None, tuple[Condition, ...]]:
    """把一个复合选择器读成 ``(tag, conditions)``。**先解析，后比较。**

    两段分开有一个具体的理由：如果形态检查与比较写在同一段循环里，一个形态不合法
    的属性运算（``a[href|=en]``）只会在 tag **已经匹配**的节点上才被发现 ——
    于是它在「目标 tag 恰好不在这棵树上」时静默通过，而那就是「不支持」
    被读成「没有值」的那条路。
    """
    match = _SIMPLE_TOKEN.match(token)
    if not match:
        _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                f"复合选择器 {token!r} 不在支持子集内。支持的形态："
                "``tag`` / ``.class`` / ``#id`` / ``*`` / ``[attr]`` / ``[attr=\"v\"]`` "
                "及它们的拼接。")
    tag = match.group("tag")
    rest = match.group("rest") or ""
    conditions: list[Condition] = []
    index = 0
    while index < len(rest):
        char = rest[index]
        if char in ".#":
            end = index + 1
            while end < len(rest) and rest[end] not in ".#[":
                end += 1
            value = rest[index + 1:end]
            if not value:
                _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                        f"{token!r} 里有一个空的 ``{char}`` 段。")
            conditions.append(("class" if char == "." else "id", value, None, None))
            index = end
            continue
        end = rest.find("]", index)
        if end < 0:
            _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                    f"{token!r} 的属性段没有闭合的 ``]``。")
        end += 1
        attr = _ATTR_TOKEN.match(rest[index:end])
        if not attr:
            _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                    f"属性选择器 {rest[index:end]!r} 不在支持子集内。")
        name, operator, expected = attr.group(1), attr.group(2), _strip_quotes(attr.group(3))
        if operator is not None and operator not in _ATTR_OPERATORS:
            _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                    f"属性运算符 {operator!r} 不在支持子集内"
                    f"（支持 {sorted(_ATTR_OPERATORS)}）。")
        conditions.append(("attr", name, operator, expected if operator else None))
        index = end
    if tag is None and not conditions:
        _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                "``*`` 是唯一能独立成段的通配写法；空的一段没有意义。")
    return tag, tuple(conditions)


def _match_compound(node: _Node, token: str) -> bool:
    """求值。**它不做任何形态判定** —— 形态那一半在 :func:`parse_compound` 里，
    所以一个不合法的选择器不可能因为「树上恰好没有这个 tag」而被放过。"""
    tag, conditions = parse_compound(token)
    if tag and tag != "*" and node.tag != tag.casefold():
        return False
    for kind, name, operator, expected in conditions:
        if kind == "class":
            if name not in node.classes():
                return False
            continue
        if kind == "id":
            if node.attrs.get("id") != name:
                return False
            continue
        if name not in node.attrs:
            return False
        if operator is None:
            continue
        actual = node.attrs[name]
        value = expected or ""
        if operator == "=" and actual != value:
            return False
        if operator == "~=" and value not in actual.split():
            return False
        if operator == "^=" and not actual.startswith(value):
            return False
        if operator == "$=" and not actual.endswith(value):
            return False
        if operator == "*=" and value not in actual:
            return False
    return True


def _parse_selector(selector: str) -> list[tuple[str, str]]:
    """把一个 CSS 选择器读成 ``[(combinator, compound), ...]``。

    支持的组合子是**后代**（空格）与**直接子**（``>``）。``+`` / ``~`` / 伪类
    / ``:has`` 一律 ``SELECTOR_UNSUPPORTED`` —— 它们要实现得对才有意义，
    而「差不多对」的 ``:has`` 会让判据那一侧读出一个错误的字段。
    """
    text = selector.strip()
    if not text or text.count("(") or ":" in text.split("[")[0] or "," in text:
        _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                f"选择器 {selector!r} 不在支持子集内（不支持伪类、分组、函数）。")
    steps: list[tuple[str, str]] = []
    buffer: list[str] = []
    combinator = " "
    index = 0
    while index < len(text):
        char = text[index]
        if char == "[":
            end = text.find("]", index)
            if end < 0:
                _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                        f"选择器 {selector!r} 的属性段没有闭合的 ``]``")
            buffer.append(text[index:end + 1])
            index = end + 1
            continue
        if char == ">":
            pending = "".join(buffer).strip()
            if pending:
                steps.append((combinator, pending))
                buffer = []
            combinator = ">"
            index += 1
            continue
        if char.isspace():
            pending = "".join(buffer).strip()
            if pending:
                steps.append((combinator, pending))
                buffer = []
                combinator = " "
            # 空白紧挨着 ``>`` 时**不**把组合子改回后代 —— ``body > ul`` 里的
            # 那个空格是 ``>`` 的排版，不是第二个后代组合子。
            index += 1
            continue
        buffer.append(char)
        index += 1
    steps.append((combinator, "".join(buffer).strip()))
    if any(not compound for _, compound in steps):
        _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED, f"选择器 {selector!r} 有空的一段")
    return steps


#: 节点测试。**不含 ``:``** —— 轴语法（``following-sibling::b``）与命名空间
#: （``svg:rect``）都走 ``:``，而把它们当成一个标签名会让一个不支持的写法
#: 静静地匹配不到东西：读起来就是「这一页没有这个元素」。
_XPATH_TAG = re.compile(r"\*|[A-Za-z_][-A-Za-z0-9_.]*")


def _parse_xpath(selector: str) -> tuple[tuple[str, str, str | None], ...]:
    """XPath 子集：``//tag``、``//tag[@attr]``、``//tag[@attr='v']``、``/a/b``、``*``。

    返回 ``((axis, tag, predicate|None), ...)``，``axis`` ∈
    ``{"descendant", "child"}``。**tail（``/text()`` / ``/@attr``）不在这里解析** ——
    它由 :func:`_xpath_tail` 先摘掉，因为 tail 不是一步，它是对最后一步取什么值的
    一个决定，而把它当成一步会让 ``//a/@href`` 在步数上与 ``//a/b`` 长得一样。
    """
    text = selector.strip()
    if not text:
        _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED, "空的 XPath")
    index = 0
    steps: list[tuple[str, str, str | None]] = []
    while index < len(text):
        if text.startswith("//", index):
            axis, index = "descendant", index + 2
        elif text[index] == "/":
            axis, index = "child", index + 1
        else:
            _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                    f"XPath {selector!r} 的 ``{text[index:]!r}`` 段不在支持子集内："
                    "每一步都必须以 ``/`` 或 ``//`` 开头。")
        match = _XPATH_TAG.match(text, index)
        if not match:
            _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                    f"XPath {selector!r} 的 ``{text[index:]!r}`` 段不在支持子集内："
                    "节点测试只能是标签名或 ``*``。")
        tag = match.group(0)
        index = match.end()
        predicate = None
        if index < len(text) and text[index] == "[":
            close = text.find("]", index)
            if close < 0:
                _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                        f"XPath {selector!r} 的谓词没有闭合的 ``]``。")
            predicate = text[index:close + 1]
            index = close + 1
        if index < len(text) and text[index] not in "/":
            _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                    f"XPath {selector!r} 的 ``{text[index:]!r}`` 段不在支持子集内。")
        steps.append((axis, tag, predicate))
    return tuple(steps)


@dataclass
class HtmlDocument:
    """一棵有界的 DOM。构造即解析；``truncated`` 是一个**要读的读数**。"""

    root: _Node
    truncated: bool
    node_count: int

    @classmethod
    def parse(cls, html: str, *, max_nodes: int = 20_000) -> HtmlDocument:
        builder = _DomBuilder(max_nodes=max_nodes)
        builder.feed(html)
        builder.close()
        return cls(root=builder.root, truncated=builder.truncated,
                   node_count=builder.nodes)

    def elements(self) -> list[_Node]:
        return [node for node in self.root.iter() if node.tag != "#document"]

    def select_css(self, selector: str) -> list[_Node]:
        steps = _parse_selector(selector)
        current: list[_Node] = [self.root]
        for index, (combinator, compound) in enumerate(steps):
            found: list[_Node] = []
            if index == 0:
                candidates = [node for node in self.root.iter()
                              if node.tag != "#document"]
            elif combinator == ">":
                candidates = [child for node in current for child in node.children]
            else:
                candidates = [node for parent in current
                              for node in parent.iter() if node is not parent]
            for node in candidates:
                if node.tag == "#document":
                    continue
                if _match_compound(node, compound):
                    found.append(node)
            current = found
            if not current:
                return []
        return current

    def select_xpath(self, selector: str) -> list[_Node]:
        base, _ = _xpath_tail(selector)
        steps = _parse_xpath(base)
        current: list[_Node] = [self.root]
        for axis, tag, predicate in steps:
            found: list[_Node] = []
            for node in current:
                if axis == "descendant":
                    pool = [item for item in node.iter() if item is not node]
                else:
                    pool = list(node.children)
                for candidate in pool:
                    if candidate.tag == "#document":
                        continue
                    if tag != "*" and candidate.tag != tag.casefold():
                        continue
                    if predicate and not _xpath_predicate(candidate, predicate):
                        continue
                    found.append(candidate)
            current = found
            if not current:
                return []
        return current

    def select(self, kind: SelectorKind, selector: str) -> list[_Node]:
        if kind is SelectorKind.CSS:
            return self.select_css(selector)
        if kind is SelectorKind.XPATH:
            return self.select_xpath(selector)
        _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                "regex 不是一种节点选择器 —— 它作用在文本上，见 apply_extraction")


_XPATH_PREDICATE = re.compile(
    r"^@(?P<name>[-A-Za-z0-9_:.]+)(?:\s*=\s*['\"](?P<value>[^'\"]*)['\"])?$"
)


def parse_xpath_predicate(pred: str) -> tuple[str, str | None]:
    """``[@attr]`` / ``[@attr='v']`` → ``(attr, None|'v')``。其余一律拒绝。

    **结构判定**与**求值**分开，理由与 :func:`validate_selector` 同一条：
    一个 ``[position()>1]`` 如果只在求值时报错，那么它在报告上与「没有匹配」
    同形；分成两段之后，第一种在规格那一层就死了。
    """
    inner = pred[1:-1].strip() if pred.startswith("[") and pred.endswith("]") else pred
    match = _XPATH_PREDICATE.match(inner)
    if not match:
        _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED,
                f"XPath 谓词 {pred!r} 不在支持子集内"
                "（只支持 [@attr] 与 [@attr='v']，不支持位置与函数谓词）。")
    return match.group("name"), match.group("value")


def _xpath_predicate(node: _Node, pred: str) -> bool:
    name, expected = parse_xpath_predicate(pred)
    if name not in node.attrs:
        return False
    return True if expected is None else node.attrs[name] == expected


def validate_selector(kind: SelectorKind, selector: str) -> None:
    """在**抽取之前**判「这个选择器在不在支持子集里」。

    为什么判定放在这里而不是等抽取时发现：一次抽取的产物是**字段**，
    而「选择器不支持」与「这一页没有这个字段」在产物里都是「没有值」。
    等到抽取时才判，两者已经同形了 —— 所以要在**规格**这一层就拒，
    那时它们还是两件不同的事（一个要改请求，一个要改事实判断）。
    """
    if kind is SelectorKind.CSS:
        # 只做**结构**解析，不接触任何节点 —— 形态判定不能依赖「树上有没有这个 tag」。
        for _, compound in _parse_selector(selector):
            parse_compound(compound)
    elif kind is SelectorKind.XPATH:
        base, attribute = _xpath_tail(selector)
        for _, _, predicate in _parse_xpath(base):
            if predicate:
                parse_xpath_predicate(predicate)
        if attribute is not None and not re.fullmatch(r"[-A-Za-z0-9_:.]+", attribute):
            _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED, f"XPath 属性名 {attribute!r} 不合法")
    elif kind is SelectorKind.REGEX:
        try:
            re.compile(selector)
        except re.error as exc:
            _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID, f"正则编译不过：{exc}")
    else:  # pragma: no cover - SelectorKind 是闭集
        _refuse(CrawlRefusal.SELECTOR_UNSUPPORTED, f"未知的选择器种类 {kind!r}")


@dataclass(frozen=True)
class ExtractionOutcome:
    """一次抽取的结果。

    **没有** ``unsupported`` 这一格：不支持的写法在
    :func:`parse_extraction_spec` 那一层已经被拒了，能走到这里的选择器
    全部在支持子集内。「不支持」与「没有值」是两件事，而两件事不该在
    同一个返回值里用同一个形状表达。
    """

    mode: str
    text: str
    fields: Mapping[str, Any]
    truncated: bool = False


def _node_value(node: _Node, field_spec: FieldSpec) -> str:
    if field_spec.attribute:
        return node.attrs.get(field_spec.attribute, "")
    return node.all_text().strip()


def _xpath_tail(selector: str) -> tuple[str, str | None]:
    """把 ``//a/@href`` 拆成 ``("//a", "href")``；``//a/text()`` → ``("//a", None)``。"""
    match = re.search(r"/(@[-A-Za-z0-9_:.]+|text\(\))$", selector)
    if not match:
        return selector, None
    tail = match.group(1)
    return selector[:match.start()], (None if tail == "text()" else tail[1:])


def apply_extraction(spec: ExtractionSpec, *, html: str, url: str | None = None,
                     max_nodes: int = 20_000) -> ExtractionOutcome:
    """对**已经取回的字节**做确定性抽取。**这里没有网络，也没有 JS。**

    ``mode=markdown`` 走一个明确的、保守的 HTML→Markdown 降级（标题、段落、列表、
    链接、``mailto:``）；它**不是** Crawl4AI 的 Readability 结果，报告上不得
    把它说成「provider 抽取的 Markdown」。真正的 provider 抽取结果由 adapter
    从 SDK 取，本函数是确定性的回退与判据面。
    """
    if not isinstance(html, str):
        _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID, "html 必须是字符串")
    if len(html) > spec.max_chars * 8:
        _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                f"输入 {len(html)} 字符超过 max_chars={spec.max_chars} 的 8 倍上限")

    document = HtmlDocument.parse(html, max_nodes=max_nodes)

    if spec.mode is ExtractionMode.HTML:
        return ExtractionOutcome(mode=spec.mode.value, text=html[:spec.max_chars],
                                 fields={}, truncated=document.truncated)

    if spec.mode is ExtractionMode.MARKDOWN:
        markdown = _to_markdown(document)
        return ExtractionOutcome(mode=spec.mode.value, text=markdown[:spec.max_chars],
                                 fields={}, truncated=document.truncated)

    if spec.mode is ExtractionMode.TEXT:
        body = [node for node in document.elements() if node.tag in {"body"}]
        source = body[0] if body else document.root
        text = re.sub(r"[ \t]*\n[ \t]*", "\n", source.all_text())
        return ExtractionOutcome(mode=spec.mode.value, text=text[:spec.max_chars],
                                 fields={}, truncated=document.truncated)

    fields: dict[str, Any] = {}
    for field_spec in spec.fields:
        value = _extract_field(document, field_spec, html)
        if value in (None, [], "") and field_spec.required:
            # ``required`` 不在这里改值 —— 它只标记「这个字段是必须的」，
            # 由调用方决定一次缺字段是 BLOCKED 还是 PARTIAL。在这一层把
            # 「缺」改成别的形状会抹掉那个区别。
            value = None
        fields[field_spec.name] = value
    return ExtractionOutcome(mode=spec.mode.value, text="", fields=fields,
                             truncated=document.truncated)


def _extract_field(document: HtmlDocument, field_spec: FieldSpec, html: str) -> Any:
    if field_spec.kind is SelectorKind.REGEX:
        matches = [match.group(1) if match.groups() else match.group(0)
                   for match in re.finditer(field_spec.selector, html)]
        if field_spec.many:
            return matches
        return matches[0] if matches else None

    if field_spec.kind is SelectorKind.XPATH:
        base, attribute = _xpath_tail(field_spec.selector)
        nodes = document.select_xpath(base)
        values = [
            (node.attrs.get(attribute, "") if attribute else node.all_text().strip())
            for node in nodes
        ]
    else:
        nodes = document.select_css(field_spec.selector)
        values = [_node_value(node, field_spec) for node in nodes]

    values = [value for value in values if value]
    if field_spec.many:
        return values
    return values[0] if values else None


_HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_SKIP_TO_MARKDOWN = frozenset({"script", "style", "noscript", "template", "head"})


def _to_markdown(document: HtmlDocument) -> str:
    """保守的 HTML→Markdown 降级。**每一处取舍都写在这里，不留给调用方猜。**

    * ``<a>`` 输出 ``[text](href)``；``mailto:`` 原样保留（§来源笔记：邮箱是
      一等材料）。
    * ``<script>`` / ``<style>`` / ``<head>`` 整棵子树**不输出** —— 把它们当正文
      是「抽取结果异常」最常见的成因。
    * 不做实体展开之外的清理、不做 Readability、不猜正文边界。
    """
    lines: list[str] = []

    def walk(node: _Node) -> None:
        if node.tag in _SKIP_TO_MARKDOWN:
            return
        if node.tag in _HEADING_LEVELS:
            text = node.all_text().strip()
            if text:
                lines.append("#" * _HEADING_LEVELS[node.tag] + " " + text)
            return
        if node.tag == "li":
            text = re.sub(r"\s+", " ", node.all_text()).strip()
            if text:
                lines.append("- " + text)
            return
        if node.tag == "a":
            text = re.sub(r"\s+", " ", node.all_text()).strip()
            href = node.attrs.get("href", "").strip()
            if text and href:
                lines.append(f"[{text}]({href})")
            elif text:
                lines.append(text)
            return
        if node.tag in {"p", "div", "section", "article", "br", "tr"}:
            text = re.sub(r"\s+", " ", "".join(node.text)).strip()
            if text:
                lines.append(text)
            for child in node.children:
                walk(child)
            return
        if node.own_text().strip():
            lines.append(re.sub(r"\s+", " ", node.own_text()).strip())
        for child in node.children:
            walk(child)

    for node in document.root.children:
        walk(node)
    return "\n\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# 深爬：一个**有界**的宽度优先计划
# ---------------------------------------------------------------------------

def domain_of(url: str) -> str:
    """一个 URL 的域名，``www.`` 去掉（``www`` 与裸域是同一个站点）。

    **公开的**：深爬的边界判定要在 adapter 那一侧逐链接做，而两处各写一份
    「怎么算同一个域」迟早会漂移，漂移的结果是一个本该被拦下的外链被放行。
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalise_allowed_domains(allowed: Sequence[str]) -> tuple[str, ...]:
    """把一份批准域名清单规范化。**空清单不是「全部」。**"""
    entries = tuple(sorted({str(item).strip().casefold().lstrip(".")
                            for item in allowed if str(item).strip()}))
    if not entries:
        _refuse(CrawlRefusal.OFF_DOMAIN_SEED_REFUSED,
                "allowed_domains 是空的 —— 空名单是谁都不许，不是谁都可以。")
    return entries


def domain_permitted(url: str, allowed: Sequence[str]) -> bool:
    """``url`` 的域名是否落在批准清单内（含子域）。"""
    host = domain_of(url)
    if not host:
        return False
    return any(host == item or host.endswith("." + item) for item in allowed)


@dataclass(frozen=True)
class CrawlPlan:
    """一份爬取计划：``queue`` 是**已经判过**的顺序，``refused`` 是被挡下的。"""

    queue: tuple[str, ...]
    refused: tuple[tuple[str, str], ...]
    domains: tuple[str, ...]
    stop_reason: str

    def __len__(self) -> int:
        return len(self.queue)


def plan_deep_crawl(seeds: Sequence[str], *,
                    budget: CrawlResourceBudget,
                    allowed_domains: Sequence[str],
                    link_extractor: Callable[[str], Sequence[str]] | None = None) -> CrawlPlan:
    """把种子展开成一个**有界**的 BFS 计划。

    三条上限在**入队之前**判，不在抓取之后再截断：

    * 页数 —— 达到 ``max_pages`` 就停，``stop_reason`` 写 ``page_cap_reached``；
    * 深度 —— 超过 ``max_depth`` 的链接**根本不入队**；
    * 域名 —— 不在 ``allowed_domains`` 的链接入 ``refused``，理由
      :attr:`CrawlRefusal.OFF_DOMAIN_SEED_REFUSED`。

    ``link_extractor`` 为 ``None`` 时计划只含种子（离线可判的部分）。给了它时，
    它必须是**纯函数**（由调用方从已取回的字节里抽链接）—— 本函数自己不发请求。
    """
    allow = normalise_allowed_domains(allowed_domains)

    def permitted(url: str) -> bool:
        return domain_permitted(url, allow)

    queue: list[str] = []
    refused: list[tuple[str, str]] = []
    seen: set[str] = set()
    frontier: list[tuple[str, int]] = []
    for seed in seeds:
        url = str(seed).strip()
        if not url:
            continue
        if not permitted(url):
            refused.append((url, CrawlRefusal.OFF_DOMAIN_SEED_REFUSED.value))
            continue
        if url not in seen:
            seen.add(url)
            frontier.append((url, 0))

    stop_reason = "complete"
    while frontier:
        if len(queue) >= budget.max_pages:
            stop_reason = "page_cap_reached"
            refused.extend((url, CrawlRefusal.CRAWL_PAGE_CAP_EXCEEDED.value)
                           for url, _ in frontier)
            break
        url, depth = frontier.pop(0)
        queue.append(url)
        if len({domain_of(item) for item in queue}) > budget.max_domains:
            queue.pop()
            refused.append((url, CrawlRefusal.CRAWL_DOMAIN_CAP_EXCEEDED.value))
            stop_reason = "domain_cap_reached"
            continue
        if link_extractor is None:
            continue
        if depth >= budget.max_depth:
            continue
        for link in link_extractor(url) or ():
            target = str(link).strip()
            if not target or target in seen:
                continue
            if not permitted(target):
                refused.append((target, CrawlRefusal.OFF_DOMAIN_SEED_REFUSED.value))
                continue
            if len(queue) + len(frontier) >= budget.max_pages:
                refused.append((target, CrawlRefusal.CRAWL_PAGE_CAP_EXCEEDED.value))
                stop_reason = "page_cap_reached"
                continue
            seen.add(target)
            frontier.append((target, depth + 1))

    domains = tuple(sorted({domain_of(item) for item in queue}))
    return CrawlPlan(queue=tuple(queue), refused=tuple(refused), domains=domains,
                     stop_reason=stop_reason)


# ---------------------------------------------------------------------------
# sitemap / robots：解析是本地纯函数，取回由 adapter 经出口做
# ---------------------------------------------------------------------------

_SITEMAP_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)


def parse_sitemap(payload: str, *, max_urls: int = 200) -> tuple[str, ...]:
    """读 sitemap（``urlset`` / ``sitemapindex`` / ``sitemap.txt``）。

    **不猜**：非 http(s) 的 ``<loc>`` 一律丢掉（``javascript:`` 一类的条目在真实
    sitemap 里出现过），超过 ``max_urls`` 的部分被截断 —— 而截断这件事由返回值
    的长度体现，调用方必须自己判，不在这里静默。
    """
    if not isinstance(payload, str):
        _refuse(CrawlRefusal.EXTRACTION_SPEC_INVALID, "sitemap 必须是字符串")
    if max_urls <= 0:
        _refuse(CrawlRefusal.UNBOUNDED_CRAWL_REFUSED, "max_urls 必须是正数")
    found: list[str] = []
    for match in _SITEMAP_LOC.finditer(payload):
        url = match.group(1).strip()
        if url.startswith(("http://", "https://")) and url not in found:
            found.append(url)
        if len(found) >= max_urls:
            break
    if not found:
        for line in payload.splitlines():
            url = line.strip()
            if url.startswith(("http://", "https://")) and url not in found:
                found.append(url)
            if len(found) >= max_urls:
                break
    return tuple(found)


def robots_allowed(robots_txt: str, *, path: str, user_agent: str = "*") -> bool:
    """一个**保守**的 robots 判据。

    只实现 ``User-agent`` / ``Disallow`` / ``Allow`` 三条。**没有** sitemap、
    crawl-delay 与通配符（``*`` / ``$``）—— 但那不是「忽略」，那些行会被原样
    跳过并**不**改变判定。

    缺省是**不许**：``robots_txt`` 为空串时返回 ``False``。理由与
    ``security/catalog.py`` 的空集同一条 —— 「没读到」与「读到了但没写规则」
    处置相同（停），而另一边的缺省会让一次取回失败表现成「站点允许」。
    """
    if not isinstance(robots_txt, str) or not robots_txt.strip():
        return False
    target = path if path.startswith("/") else "/" + path
    best_allow: int | None = None
    best_disallow: int | None = None
    unproven: list[str] = []
    current_applies = False
    saw_rule = False
    for raw in robots_txt.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().casefold()
        value = value.strip()
        if key == "user-agent":
            current_applies = value == "*" or value.casefold() == user_agent.casefold()
            continue
        if not current_applies or key not in {"disallow", "allow"}:
            continue
        saw_rule = True
        if "*" in value or value.endswith("$"):
            # 通配规则：本实现**不解释**它，而「不解释」不等于「当成允许」。
            # 记下来，最后按「证不出允许 ⇒ 不许」处置。
            unproven.append(f"{key}:{value}")
            continue
        if key == "disallow" and value and target.startswith(value):
            best_disallow = max(best_disallow or 0, len(value))
        elif key == "allow" and value and target.startswith(value):
            best_allow = max(best_allow or 0, len(value))
    if unproven:
        # 有通配规则落在适用的那一组里，而我们证不出「这条路径被允许」。
        # 与 security/catalog.py 的空集同一条：证不出来的那一边选「停」。
        return False
    if not saw_rule:
        # 适用的那一组里一条规则都没有 ⇒ 按 robots 的通行语义，没有限制。
        # **这与「读不到 robots」是两件事**：那一种在上面的空串分支里已经拒了。
        return True
    if best_disallow is None:
        return True
    if best_allow is None:
        return False
    return best_allow >= best_disallow


# ---------------------------------------------------------------------------
# 会话与产物：控制面
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionPolicy:
    """一个**受控**的浏览会话。

    ``persist_state=False`` 是唯一允许的取值：本 Wave 授权的是匿名公开浏览
    （``adapters/crawl4ai_fetcher.py`` 的文件头：「本模块不提供任何登录、验证码
    求解或反检测能力的接口 —— 不是『没实现』，是**不提供**」）。
    """

    ttl_seconds: float
    persist_state: bool = False
    reuse_within_run: bool = True
    max_pages_per_session: int = 25

    def __post_init__(self) -> None:
        if self.persist_state:
            _refuse(CrawlRefusal.SITE_LOGIN_FORBIDDEN,
                    "persist_state=True 会把一次匿名浏览变成一个持久的登录态载体。")
        if not (0 < self.ttl_seconds <= 600):
            _refuse(CrawlRefusal.SESSION_POLICY_VIOLATION,
                    f"ttl_seconds={self.ttl_seconds!r} 不在 (0, 600] 内")
        if not (0 < self.max_pages_per_session <= 50):
            _refuse(CrawlRefusal.SESSION_POLICY_VIOLATION,
                    f"max_pages_per_session={self.max_pages_per_session!r} 不在 (0, 50] 内")


def parse_session_policy(payload: Mapping[str, Any] | None) -> SessionPolicy:
    payload = payload or {}
    if not isinstance(payload, Mapping):
        _refuse(CrawlRefusal.SESSION_POLICY_VIOLATION, "会话策略必须是一个映射")
    for refusal in forbidden_feature_reasons(payload):
        _refuse(refusal, "会话策略里出现了禁止的键。")
    allowed = {"ttl_seconds", "persist_state", "reuse_within_run",
               "max_pages_per_session"}
    unknown = {str(key) for key in payload} - allowed
    if unknown:
        _refuse(CrawlRefusal.SESSION_POLICY_VIOLATION,
                f"未知的会话策略键：{sorted(unknown)}")
    return SessionPolicy(
        ttl_seconds=float(payload.get("ttl_seconds", 120.0)),
        persist_state=bool(payload.get("persist_state", False)),
        reuse_within_run=bool(payload.get("reuse_within_run", True)),
        max_pages_per_session=int(payload.get("max_pages_per_session", 25)),
    )


@dataclass(frozen=True)
class ArtifactPolicy:
    """截图 / PDF / 缓存。**默认全关。**

    这三样都是**额外**成本（CPU、磁盘、时间），而在一次「只想要正文」的抓取里
    它们全部是浪费。所以它们的缺省是 ``False``，而不是「反正也花不了多少」——
    T1 的成本是我们自己的机器，而「反正也花不了多少」正是 T1 被误读成 T0 的地方。
    """

    screenshot: bool = False
    pdf: bool = False
    cache: bool = False
    max_bytes_per_artifact: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        if not (0 < self.max_bytes_per_artifact <= HARD_CEILINGS["max_bytes"]):
            _refuse(CrawlRefusal.ARTIFACT_CAP_EXCEEDED,
                    f"max_bytes_per_artifact={self.max_bytes_per_artifact!r} 超出允许范围")

    def enabled(self) -> tuple[str, ...]:
        return tuple(name for name in ("screenshot", "pdf", "cache") if getattr(self, name))


def parse_artifact_policy(payload: Mapping[str, Any] | None) -> ArtifactPolicy:
    payload = payload or {}
    if not isinstance(payload, Mapping):
        _refuse(CrawlRefusal.ARTIFACT_CAP_EXCEEDED, "产物策略必须是一个映射")
    for refusal in forbidden_feature_reasons(payload):
        _refuse(refusal, "产物策略里出现了禁止的键。")
    unknown = {str(key) for key in payload} - {"screenshot", "pdf", "cache",
                                              "max_bytes_per_artifact"}
    if unknown:
        _refuse(CrawlRefusal.ARTIFACT_CAP_EXCEEDED, f"未知的产物策略键：{sorted(unknown)}")
    return ArtifactPolicy(
        screenshot=bool(payload.get("screenshot", False)),
        pdf=bool(payload.get("pdf", False)),
        cache=bool(payload.get("cache", False)),
        max_bytes_per_artifact=int(payload.get("max_bytes_per_artifact",
                                               8 * 1024 * 1024)),
    )


# ---------------------------------------------------------------------------
# 证据清单：append-only，且**导出前必须已绑定 ledger**
# ---------------------------------------------------------------------------

def content_sha256(payload: bytes | str) -> str:
    """内容的字节 hash。字符串按 UTF-8 编码 —— **不做任何规范化**。"""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class ManifestRow:
    """一页一行。``ledger_id`` 由 B 的 ``UsageLedger`` 在预约之后回填。

    形状对着契约 §3.5 的 ``EvidenceEnvelope``，缺的那几个（``derived_refs``、
    ``verified_state``）由证据层（W-F）加 —— 本层只产它自己能证明的那几格。
    """

    run_id: str
    operation_id: str
    provider: str
    capability_id: str
    url: str | None
    retrieved_at: datetime
    content_sha256: str
    extraction_method: str
    outcome: str
    artifact_refs: tuple[str, ...] = ()
    ledger_id: str | None = None
    egress_decision_ref: str | None = None
    caveats: tuple[str, ...] = ()

    def as_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "operation_id": self.operation_id,
            "provider": self.provider,
            "capability_id": self.capability_id,
            "url": self.url,
            "retrieved_at": self.retrieved_at.astimezone(timezone.utc).isoformat(),
            "content_sha256": self.content_sha256,
            "extraction_method": self.extraction_method,
            "outcome": self.outcome,
            "artifact_refs": list(self.artifact_refs),
            "ledger_id": self.ledger_id,
            "egress_decision_ref": self.egress_decision_ref,
            "caveats": list(self.caveats),
        }


class EvidenceManifest:
    """一次 Run 的证据清单。**append-only**（契约 IL-8 的同一纪律）。

    没有 ``remove`` / ``update``：更正靠再追加一行，而不是改写历史。
    """

    def __init__(self, *, run_id: str) -> None:
        self.run_id = run_id
        self._rows: list[ManifestRow] = []

    def add(self, row: ManifestRow) -> ManifestRow:
        if row.run_id != self.run_id:
            _refuse(CrawlRefusal.EVIDENCE_NOT_BOUND,
                    f"清单属于 Run {self.run_id!r}，收到的是 {row.run_id!r}")
        self._rows.append(row)
        return row

    @property
    def rows(self) -> tuple[ManifestRow, ...]:
        return tuple(self._rows)

    def unbound(self) -> tuple[ManifestRow, ...]:
        """没有 ledger 链接的行。**它们不可导出**（契约 §3.5）。"""
        return tuple(row for row in self._rows if not row.ledger_id)

    def require_bound(self) -> None:
        """导出前的判死。有未绑定的行时拒绝，并**逐行点名**。"""
        missing = self.unbound()
        if missing:
            _refuse(
                CrawlRefusal.EVIDENCE_NOT_BOUND,
                f"{len(missing)} 行证据没有 ledger 链接（契约 §3.5：无 ledger 链接的"
                f"证据不可导出）：{[(row.url, row.operation_id) for row in missing]}",
            )

    def to_jsonl(self) -> str:
        import json

        self.require_bound()
        return "\n".join(json.dumps(row.as_row(), ensure_ascii=False, sort_keys=True)
                         for row in self._rows)


# ---------------------------------------------------------------------------
# 给 W-G 的能力参考（Skill / references 由 W-G 拥有，本模块只产出内容）
# ---------------------------------------------------------------------------

#: Crawl4AI 在本 Wave 里的能力面。**这是给 W-G 打包 Skill 用的输入**，
#: 不是一份安装说明。``scripts/`` 下的文件由 W-E 拥有，
#: ``plugins/research-tools/**`` 的其余部分（含 ``skills/marketops-crawl4ai``
#: 与 ``references/providers/crawl4ai.md``）由 W-G 拥有 —— 见 §3 所有权表。
CRAWL4AI_CAPABILITY_REFERENCE: Mapping[str, Any] = {
    "provider": PROVIDER,
    "transport": TRANSPORT,
    "tier": TIER,
    "effect_class": "READ_ONLY",
    "cost_meter": "pages|bytes|seconds|browser_seconds|artifacts",
    "cost_note": "本地不是零成本：CPU/时间/页数/带宽/存储都必须有上限（CONVDEC §4）",
    "capabilities": (
        {
            "capability_id": "crawl4ai.fetch_page",
            "description": "渲染一个已批准的公开页面，取回渲染后的 HTML 与正文文本。",
            "params": ("url", "mode", "max_chars"),
        },
        {
            "capability_id": "crawl4ai.extract",
            "description": "对已取回的字节做 CSS / XPath / 正则抽取（单字段或结构化）。",
            "params": ("url", "fields", "mode", "max_chars"),
        },
        {
            "capability_id": "crawl4ai.deep_crawl",
            "description": "在一个批准域集合内做有界 BFS；页数/深度/域名/字节/秒数全部有上限。",
            "params": ("seeds", "allowed_domains", "limits", "max_depth"),
        },
        {
            "capability_id": "crawl4ai.sitemap_seed",
            "description": "读 sitemap / robots 作为种子来源；取回本身经出口。",
            "params": ("sitemap_urls", "allowed_domains", "limits"),
        },
        {
            "capability_id": "crawl4ai.screenshot",
            "description": "可选：页面截图。默认关闭，逐次显式开启。",
            "params": ("url", "limits", "artifacts"),
        },
        {
            "capability_id": "crawl4ai.pdf",
            "description": "可选：页面 PDF。默认关闭，逐次显式开启。",
            "params": ("url", "limits", "artifacts"),
        },
    ),
    "always_refused": {
        "arbitrary_js": [item.value for item in (CrawlRefusal.ARBITRARY_JS_FORBIDDEN,)],
        "site_login": [item.value for item in (CrawlRefusal.SITE_LOGIN_FORBIDDEN,)],
        "unbounded_crawl": [item.value for item in (CrawlRefusal.UNBOUNDED_CRAWL_REFUSED,)],
    },
    "egress": {
        "requirement": "浏览器与全部辅助 HTTP（robots / sitemap / 深爬 / 抽取取回）"
                       "必须经唯一的受管出口；没有出口时是 LOCAL_CRAWL_NOT_SAFE_TO_RUN，"
                       "不是无管控回退。",
        "contract_owner": "W-A SECURITY",
        "unresolved": "DECISION-01（插件如何拿到唯一的 EgressProxy 实现）",
    },
    "readiness": "CODED",
    "readiness_note": "离线可测；UPSTREAM_SCHEMA_VERIFIED 需要 DECISION-04 授权的"
                      "只读握手，本 Wave 未授权。",
}


def render_reference_markdown() -> str:
    """把上面的参考渲染成 Markdown 片段，供 W-G 直接放进 Skill / references。"""
    lines = [
        "# Crawl4AI 能力面（Wave-1 受管能力网关）",
        "",
        f"- provider: `{CRAWL4AI_CAPABILITY_REFERENCE['provider']}`",
        f"- transport: `{CRAWL4AI_CAPABILITY_REFERENCE['transport']}`",
        f"- tier: `{CRAWL4AI_CAPABILITY_REFERENCE['tier']}`"
        f"（{CRAWL4AI_CAPABILITY_REFERENCE['cost_note']}）",
        f"- meter: `{CRAWL4AI_CAPABILITY_REFERENCE['cost_meter']}`",
        f"- readiness: `{CRAWL4AI_CAPABILITY_REFERENCE['readiness']}`",
        "",
        "## 能力",
        "",
    ]
    for item in CRAWL4AI_CAPABILITY_REFERENCE["capabilities"]:
        lines.append(f"- `{item['capability_id']}` — {item['description']}")
        lines.append(f"  - 参数子集：`{', '.join(item['params'])}`")
    lines += [
        "",
        "## 一律拒绝",
        "",
        "- Agent 提供的任意 `js_code` / 页面内脚本",
        "- 站点登录、凭据、profile、cookie 注入、验证码绕过",
        "- 无页数/深度/域名上限的爬取",
        "",
        "## 出口",
        "",
        CRAWL4AI_CAPABILITY_REFERENCE["egress"]["requirement"],
        f"（contract owner: {CRAWL4AI_CAPABILITY_REFERENCE['egress']['contract_owner']}；"
        f"未决：{CRAWL4AI_CAPABILITY_REFERENCE['egress']['unresolved']}）",
        "",
    ]
    return "\n".join(lines)

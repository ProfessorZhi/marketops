#!/usr/bin/env python3
"""Crawl4AI 本地 SDK 的 **ProviderAdapter**（契约 §6），外加它与唯一出口的接缝。

## 这一层负责什么，不负责什么

负责：把一次已授权的 ``CapabilityRequest`` 变成一次**有界**的本地抓取 / 抽取，
并把结果装成契约 §3.4 的形状；以及**唯一的出口检查**。

**不负责**（契约 IA-2）：tier / 预算 / 政策判定。本模块里**没有**一处读 tier、
读钱包、读 Grant 的代码 —— 重复判定会制造第二个政策源，而两个政策源分歧时
没有哪一个是「对」的。调用方给的是**已经过 Policy 与 Ledger.reserve 的**请求。

**不负责**（契约 IA-3）：重试。本模块**没有**任何循环重试。派发后的失败就是失败；
一次超时的重试会在上游留下第二次调用，而我们的账上只有一行。

## 出口（IA-4）：本模块最重要的那一条

契约 IA-4 逐字：「浏览器与辅助 HTTP（robots / sitemap seeder / 深爬 / CSS 抽取
取回）**全部**经 EgressProxy；不经代理时返回 ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``，
**而不是**无管控回退。」

于是本模块里**不存在**「有出口就用、没有就直连」这条分支。出口只有两种状态：

* **已绑定**（:class:`EgressBinding`）：浏览器的 ``proxy_config`` 指向它，
  辅助 HTTP 的 opener 也指向它；两者都拿不到别的出口。
* **未绑定**（:data:`UNBOUNDED_EGRESS` 之外的一切缺省）：每一次
  :meth:`Crawl4AIAdapter.execute` 直接 ``BLOCKED`` / ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``，
  **一次网络动作都不发生**。

这一格与 DECISION-01 的关系：契约 A 拥有出口的实现
（``marketops/adapters/egress_proxy.py`` + ``marketops/adapters/crawl4ai_fetcher.py``），
而插件**不带** core 包。DECISION-01 的默认立场是「vendoring + hash 钉死 + 单一维护源」，
owner 未裁决。所以本模块**只定义接缝**，把「谁能当出口」写成一个可校验的结构
（:func:`verify_egress_binding`），由 INTEGRATOR 在集成时把 A 的实现接上。
在那之前，缺省路径是**安全的那一边**：不跑。

## 辅助 HTTP 的三条纪律

1. **不跟随重定向**（IA-5，沿用 ``research_official_mcp.py`` 与 ``http.py`` 的纪律）。
   一个 302 到内网的跳转是一次「判过的 URL 与连上的 URL 不是同一个」。
2. **有界读取**：超过上限的连接被掐断并报 ``CRAWL_BYTE_CAP_EXCEEDED``，
   而不是读完之后再判 —— 后者已经把字节和内存花掉了。
3. **不重试**：``urllib`` 的 opener 默认不重试，这里也不要「顺手加一个」。

## 这一层没有宣称做到的事

* ``actual_usd`` 恒为 ``None``、``actual_source`` 恒为 ``NONE``。本地 SDK **没有**
  厂商计价，而「免费」不是它的价格 —— CPU、带宽、磁盘与时间是真的，
  它们以**配额单位**记在预算表上（CONVDEC §4）。把本地抓取写成 ``$0.00``
  正是契约 §4 明令不许的那件事（「Never equate a reserved attempt or a successful
  response to $0 actual charge」）。
* ``schema_verdict`` 恒为 ``UNVERIFIED``。``probe_schema()`` 需要 DECISION-04
  授权的只读握手，本 Wave 未授权 —— 所以它返回 ``upstream_tools_list: None``，
  **绝不伪造一张快照**。
* 挑战页 / 权限墙的正文**不落盘**、不作为内容返回（判据在
  :func:`_challenge_markers`）。这一层沿用 ``crawl4ai_fetcher.py`` 的处置：
  Cloudflare 的 ``cf-mitigated: challenge`` 头**或**正文指纹命中即为挑战页。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol, Sequence

import research_crawl4ai_features as features
from research_crawl4ai_features import (
    ArtifactPolicy,
    BudgetMeter,
    CrawlRefusal,
    CrawlRefusalError,
    CrawlResourceBudget,
    EvidenceManifest,
    ExtractionMode,
    ExtractionSpec,
    ManifestRow,
    PROVIDER,
    SessionPolicy,
    TRANSPORT,
    apply_extraction,
    content_sha256,
    forbidden_feature_reasons,
    parse_artifact_policy,
    parse_extraction_spec,
    parse_session_policy,
    parse_sitemap,
    plan_deep_crawl,
)

__all__ = [
    "ADAPTER_RESULT_FIELDS",
    "ALL_REASON_CODES",
    "ArtifactRef",
    "BROWSER_EXTRA_ARGS",
    "BROWSER_SECURITY_ARGS",
    "BROWSER_SECURITY_BOUNDARY",
    "CAPABILITIES",
    "DNS_REBINDING_GUARD",
    "WEBRTC_NON_PROXIED_UDP_GUARD",
    "browser_launch_violations",
    "hardened_browser_extra_args",
    "CONTRACT_ANALOGUE",
    "CONTRACT_REASON_CODES",
    "READINESS_VERDICTS",
    "REVIEWED_CAPABILITY_HINTS",
    "Crawl4AIAdapter",
    "CrawlOutcome",
    "EgressBinding",
    "EgressDecisionView",
    "EGRESS_CONTRACT_ATTRIBUTES",
    "LOCAL_CRAWL_NOT_SAFE_TO_RUN",
    "LoopbackEgressBinding",
    "UNBOUND_EGRESS",
    "UpstreamRefusal",
    "auxiliary_http_get",
    "crawl4ai_available",
    "render_outcome_json",
    "to_adapter_result_fields",
    "verify_egress_binding",
]

#: 本 adapter 服务的 capability_id 全集。必须与 Registry 交叉一致（§6）。
CAPABILITIES: tuple[str, ...] = (
    "crawl4ai.fetch_page",
    "crawl4ai.extract",
    "crawl4ai.deep_crawl",
    "crawl4ai.sitemap_seed",
    "crawl4ai.screenshot",
    "crawl4ai.pdf",
)

#: 契约 §6：不经代理时的返回值。**不是**一个可以继续走的软开关。
LOCAL_CRAWL_NOT_SAFE_TO_RUN = "LOCAL_CRAWL_NOT_SAFE_TO_RUN"

#: 契约 §5.3 的 ``reason_code`` 闭集，**逐字抄下来**。
#: 本模块只允许从这个集合 ∪ :data:`_ADAPTER_LOCAL_REASONS` 里取理由码；
#: 由 ``test_wave1_crawl4ai_adapter.py`` 判死，所以新增一个名字必须同时改这里
#: 与 status 文件里的 CONTRACT-CHANGE-REQUEST。
CONTRACT_REASON_CODES: frozenset[str] = frozenset({
    "POLICY_TIER_NOT_GRANTED", "POLICY_DOMAIN_NOT_ALLOWED", "POLICY_GRANT_EXPIRED",
    "POLICY_GRANT_REVOKED", "BUDGET_RUN_CEILING", "BUDGET_PROVIDER_CEILING",
    "BUDGET_PROJECT_CEILING", "BUDGET_OPERATION_CEILING", "PRICE_UNKNOWN_FAIL_CLOSED",
    "PRICING_SOURCE_MISSING", "DUPLICATE_OPERATION", "OPERATION_ID_COLLISION",
    "HOST_PREVIOUSLY_BLOCKED", "CAPABILITY_UNKNOWN", "CAPABILITY_PARAM_NOT_ALLOWED",
    "CAPABILITY_SCHEMA_DRIFT", "CAPABILITY_NOT_AVAILABLE", "EGRESS_REFUSED",
    "CRAWL4AI_LOCAL_NOT_INSTALLED", "UNKNOWN_CHARGE_OUTSTANDING",
    "RESEARCH_NETWORK_DISABLED", "WALLET_PRECONDITION_FAILED", "GRANT_SIGNATURE_INVALID",
})

#: 本地口径的理由码：它们是 §5.3 之外**由本模块产出**的名字。
#: 为什么不是回落成自由文本：契约 §5.3 最后一行「禁止回落到自由文本」。
_ADAPTER_LOCAL_REASONS: frozenset[str] = frozenset({
    LOCAL_CRAWL_NOT_SAFE_TO_RUN,
    CrawlRefusal.ARBITRARY_JS_FORBIDDEN.value,
    CrawlRefusal.SITE_LOGIN_FORBIDDEN.value,
    CrawlRefusal.UNBOUNDED_CRAWL_REFUSED.value,
    CrawlRefusal.OFF_DOMAIN_SEED_REFUSED.value,
    CrawlRefusal.CRAWL_PAGE_CAP_EXCEEDED.value,
    CrawlRefusal.CRAWL_DEPTH_CAP_EXCEEDED.value,
    CrawlRefusal.CRAWL_DOMAIN_CAP_EXCEEDED.value,
    CrawlRefusal.CRAWL_BYTE_CAP_EXCEEDED.value,
    CrawlRefusal.CRAWL_TIME_CAP_EXCEEDED.value,
    CrawlRefusal.ARTIFACT_CAP_EXCEEDED.value,
    CrawlRefusal.EXTRACTION_SPEC_INVALID.value,
    CrawlRefusal.SELECTOR_UNSUPPORTED.value,
    CrawlRefusal.SESSION_POLICY_VIOLATION.value,
    "SOURCE_CHALLENGE_PAGE",
    "SOURCE_ACCESS_BLOCKED",
    "SOURCE_EMPTY_EXTRACT",
    "LOCAL_CRAWL_FAILED",
    "LOCAL_CRAWL_RUNTIME_FAILED",
    "LOCAL_BROWSER_NOT_INSTALLED",
    "CRAWL_REDIRECT_REFUSED",
    "SOURCE_ANTIBOT_HEURISTIC",
    "ARTIFACT_EMPTY_FROM_SDK",
    "ROBOTS_DISALLOWED",
})

ALL_REASON_CODES: frozenset[str] = CONTRACT_REASON_CODES | _ADAPTER_LOCAL_REASONS

#: 本地码 → §5.3 闭集里的**诚实对应码**。**这不是一张「差不多的翻译表」。**
#:
#: 为什么需要它：集成侧的网关（``research_gateway.py::_result_refusal_code``）
#: 只接受 §5.3 闭集里的成员，一个闭集外的码会让它抛 ``ContractViolation`` ——
#: 那是**对的**，因为「把一个拼错的码翻译成一个像样的码」正是那张闭集存在的理由。
#: 所以本模块不能自己发明一个新码就交给它，而必须指出：这个本地条件在契约里
#: **本来叫什么**。
#:
#: 每一行的判据是「契约里那一条说的就是这件事」，而不是「下游只要一个字符串」：
#:
#: * ``LOCAL_CRAWL_NOT_SAFE_TO_RUN`` → ``RESEARCH_NETWORK_DISABLED``：
#:   没有安全出口 ⇒ 研究网络这条路被关掉。这一对应不是我发明的 ——
#:   本模块要取代的那个冻结文件
#:   （``research_provider_extensions.py::_reserve``）用的就是同一个码，
#:   条件也同样是「没有获准联网」。
#: * 四个 ``CRAWL_*_CAP_EXCEEDED`` / ``UNBOUNDED_CRAWL_REFUSED`` / ``ARTIFACT_CAP_EXCEEDED``
#:   → ``BUDGET_OPERATION_CEILING``：§5.2 的「per-operation 上限」讲的正是
#:   一次操作自己的配额上限；本地爬取的页/字节/秒/产物就是那一格。
#: * ``OFF_DOMAIN_SEED_REFUSED`` → ``POLICY_DOMAIN_NOT_ALLOWED``：域名允许清单，
#:   逐字同一件事。
#: * ``ARBITRARY_JS_FORBIDDEN`` / ``EXTRACTION_SPEC_INVALID`` / ``SELECTOR_UNSUPPORTED``
#:   / ``SESSION_POLICY_VIOLATION`` → ``CAPABILITY_PARAM_NOT_ALLOWED``：
#:   请求的参数（或参数的取值）不在这个能力允许的集合里。
#: * ``SITE_LOGIN_FORBIDDEN`` → ``POLICY_TIER_NOT_GRANTED``：owner brief §4 把
#:   「站点登录 / profile」放在 T4，而本 Wave 的授权里 T4 是「Off」。
#: * ``LOCAL_BROWSER_NOT_INSTALLED`` → ``CRAWL4AI_LOCAL_NOT_INSTALLED``：
#:   §5.3 自己的名字就是「本地这套 Crawl4AI 栈用不了」。
CONTRACT_ANALOGUE: Mapping[str, str] = {
    LOCAL_CRAWL_NOT_SAFE_TO_RUN: "RESEARCH_NETWORK_DISABLED",
    CrawlRefusal.UNBOUNDED_CRAWL_REFUSED.value: "BUDGET_OPERATION_CEILING",
    CrawlRefusal.CRAWL_PAGE_CAP_EXCEEDED.value: "BUDGET_OPERATION_CEILING",
    CrawlRefusal.CRAWL_DEPTH_CAP_EXCEEDED.value: "BUDGET_OPERATION_CEILING",
    CrawlRefusal.CRAWL_DOMAIN_CAP_EXCEEDED.value: "BUDGET_OPERATION_CEILING",
    CrawlRefusal.CRAWL_BYTE_CAP_EXCEEDED.value: "BUDGET_OPERATION_CEILING",
    CrawlRefusal.CRAWL_TIME_CAP_EXCEEDED.value: "BUDGET_OPERATION_CEILING",
    CrawlRefusal.ARTIFACT_CAP_EXCEEDED.value: "BUDGET_OPERATION_CEILING",
    CrawlRefusal.OFF_DOMAIN_SEED_REFUSED.value: "POLICY_DOMAIN_NOT_ALLOWED",
    CrawlRefusal.ARBITRARY_JS_FORBIDDEN.value: "CAPABILITY_PARAM_NOT_ALLOWED",
    CrawlRefusal.EXTRACTION_SPEC_INVALID.value: "CAPABILITY_PARAM_NOT_ALLOWED",
    CrawlRefusal.SELECTOR_UNSUPPORTED.value: "CAPABILITY_PARAM_NOT_ALLOWED",
    CrawlRefusal.SESSION_POLICY_VIOLATION.value: "CAPABILITY_PARAM_NOT_ALLOWED",
    CrawlRefusal.SITE_LOGIN_FORBIDDEN.value: "POLICY_TIER_NOT_GRANTED",
    "LOCAL_BROWSER_NOT_INSTALLED": "CRAWL4AI_LOCAL_NOT_INSTALLED",
}

#: **派发之后**才发生的失败。它们不是 ``BLOCKED``。
#:
#: §2 的 ``OutcomeCode`` 把两件事分得很清楚：``BLOCKED`` 是「本地拒绝，**未触达
#: 上游**」，``FAILED`` 是「上游明确失败」。一次 403、一张挑战页、一次跟随重定向、
#: 一次浏览器崩掉 —— 这些**都发生过**：请求出去了。把它们记成 ``BLOCKED``
#: 会让账上写着「我们没出去」，而那是假的。
#:
#: 它同时解释了为什么这几个码不需要 :data:`CONTRACT_ANALOGUE` 里的对应码：
#: 网关只在 ``outcome == "BLOCKED"`` 时要求闭集成员。
_POST_DISPATCH_CODES: frozenset[str] = frozenset({
    "CRAWL_REDIRECT_REFUSED",
    "SOURCE_CHALLENGE_PAGE",
    "SOURCE_ACCESS_BLOCKED",
    "SOURCE_EMPTY_EXTRACT",
    "LOCAL_CRAWL_FAILED",
    "LOCAL_CRAWL_RUNTIME_FAILED",
    "SOURCE_ANTIBOT_HEURISTIC",
    "ARTIFACT_EMPTY_FROM_SDK",
})


def _reason(code: str) -> str:
    """理由码必须落在闭集里。写错一个名字在**开发期**就炸，不在报告里。"""
    if code not in ALL_REASON_CODES:
        raise AssertionError(f"理由码 {code!r} 不在闭集里 —— 见 CONTRACT_REASON_CODES")
    return code


def _contract_code(code: str) -> str:
    """把本地码翻成网关能读的那一格。

    已经在 §5.3 里的原样返回；本地码查 :data:`CONTRACT_ANALOGUE`；
    **查不到的抛断言** —— 一个没有契约对应码的 BLOCKED 在集成时会让网关抛
    ``ContractViolation``，而那种失败在集成那一刻才出现，离写下这个码的人很远。
    """
    if code in CONTRACT_REASON_CODES:
        return code
    try:
        return CONTRACT_ANALOGUE[code]
    except KeyError:
        raise AssertionError(
            f"本地理由码 {code!r} 没有契约对应码 —— 把它加进 CONTRACT_ANALOGUE 并说明"
            "契约里哪一条说的就是这件事，或者按 §8 走 CONTRACT-CHANGE-REQUEST。"
        ) from None


# ---------------------------------------------------------------------------
# 出口接缝：结构可校验，缺省是「不跑」
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EgressDecisionView:
    """出口对**一次请求**的判决。字段与 ``EgressProxy.EgressDecision`` 同形。

    刻意是这个形状而不是直接复用 core 的 dataclass：插件不带 core 包
    （DECISION-01），而一个 duck-typed 的视图让 A 的实现不需要为了插件改签名。
    集成时由 :func:`verify_egress_binding` 判「接上的那个东西真的回答了这七格」。
    """

    method: str
    target: str
    port: int
    allowed: bool
    refusal: str = ""
    address: str = ""
    detail: str = ""


#: 一个合法的出口绑定**必须**能回答的名字。少一个就是没接上。
EGRESS_CONTRACT_ATTRIBUTES: tuple[str, ...] = (
    "implementation",
    "contract_sha256",
    "proxy_url",
    "decisions",
    "check",
    "browser_config_kwargs",
)

#: 浏览器启动参数里的**那一格承重项**：关掉 Chromium 对回环地址的隐式绕过。
#:
#: 少了它，一个页面可以直接连 ``127.0.0.1:6379`` 这类本机服务，而那条路
#: **不会**在出口上留下任何判决 —— 它绕过的正是出口本身。所以它不是一个
#: 「最好有」的参数，它是「这个绑定还算不算出口」的判据之一。
LOOPBACK_BYPASS_GUARD = "--proxy-bypass-list=<-loopback>"


class EgressBinding(Protocol):
    """唯一出口的**接缝**。实现在 W-A（``research_egress.py`` / ``marketops_safe_egress``）。

    ``check(url)`` 是**预检**：它在真正发请求之前回答「这个 URL 准不准」。
    它**不能**替代代理那一层的判定 —— 代理是连接被钉住的那个位置，预检只是
    让一次注定被拒的抓取不至于先把浏览器拉起来。

    ``browser_config_kwargs()`` 是**浏览器那一格**：它返回
    ``{"proxy_config": {...}, "extra_args": [...]}``，直接喂
    ``BrowserConfig(**kwargs, headless=…)``。

    ## 为什么这一格必须由出口给，而不是 adapter 自己拼

    ``proxy_config`` 与 ``extra_args`` 是**一份定义**（A 的
    ``guarded_http.browser_proxy_config`` / ``browser_egress_arguments``）。本模块
    早先自己写了一份同值的常量 —— 那种重复的失败方式是：改一处的那一天，
    另一处不会红，而现象是「出口配了但没接上」，看起来完全正常。
    接口上只留一个来源，重复就不可能发生。
    """

    implementation: str
    contract_sha256: str
    proxy_url: str

    def decisions(self) -> Sequence[EgressDecisionView]: ...

    def check(self, url: str) -> EgressDecisionView: ...

    def browser_config_kwargs(self) -> Mapping[str, object]: ...


def verify_egress_binding(binding: object) -> tuple[str, ...]:
    """结构校验。返回**违反项**的元组（空元组 = 合格）。

    只判形状，不判行为：一个形状对而行为错的出口在测试里是一个**独立的**发现，
    不是这一层能替它证明的事。所以这里的返回值是「缺了什么」，
    不是「这个出口是安全的」。

    **一个例外，而它是刻意的**：``browser_config_kwargs`` 的返回值要过一条
    *内容*判据 —— 那一格里必须带着 :data:`LOOPBACK_BYPASS_GUARD`。理由不是
    「形状不对」，而是「``extra_args`` 的形状是对的、少一个字符串就等于没有出口」：
    一条对不上的判据在这里是可以穷举的，所以它留在这里比留在一次真抓取里好。
    """
    violations: list[str] = []
    for name in EGRESS_CONTRACT_ATTRIBUTES:
        if not hasattr(binding, name):
            violations.append(f"missing:{name}")
    for name in ("implementation", "contract_sha256", "proxy_url"):
        value = getattr(binding, name, None)
        if not isinstance(value, str) or not value.strip():
            violations.append(f"not_a_nonempty_string:{name}")
    proxy_url = getattr(binding, "proxy_url", "")
    if isinstance(proxy_url, str) and proxy_url.strip():
        parsed = urllib.parse.urlsplit(proxy_url)
        if parsed.scheme != "http" or (parsed.hostname or "") not in {
            "127.0.0.1", "::1", "localhost"
        }:
            violations.append(
                "proxy_not_loopback:出口只能是本机回环上的正向代理（见 "
                "egress_proxy.py 的构造期拒绝）"
            )
    for name in ("decisions", "check", "browser_config_kwargs"):
        if not callable(getattr(binding, name, None)):
            violations.append(f"not_callable:{name}")

    kwargs = None
    if callable(getattr(binding, "browser_config_kwargs", None)):
        try:
            kwargs = binding.browser_config_kwargs()
        except Exception as exc:  # noqa: BLE001 - 形状对了但一调就炸也是一种不合格
            violations.append(f"browser_config_kwargs_raised:{type(exc).__name__}")
        else:
            if not isinstance(kwargs, Mapping):
                violations.append("browser_config_kwargs_not_a_mapping")
            else:
                proxy_config = kwargs.get("proxy_config")
                if not isinstance(proxy_config, Mapping) or not str(
                        proxy_config.get("server") or "").strip():
                    violations.append("browser_proxy_config_missing_server")
                extra_args = kwargs.get("extra_args")
                if not isinstance(extra_args, (list, tuple)):
                    violations.append("browser_extra_args_not_a_sequence")
                elif LOOPBACK_BYPASS_GUARD not in extra_args:
                    violations.append(
                        "browser_extra_args_without_loopback_guard:"
                        "少了它，一个页面可以直接连本机服务而出口上不留判决"
                    )
    if isinstance(kwargs, Mapping) and isinstance(proxy_url, str) and proxy_url:
        server = str((kwargs.get("proxy_config") or {}).get("server") or "")
        if server and server != proxy_url:
            # 两格各说一个代理地址：浏览器去了一个，预检问的是另一个。
            violations.append("browser_proxy_disagrees_with_binding_proxy_url")
    return tuple(violations)


@dataclass(frozen=True)
class _UnboundEgress:
    """缺省出口：**它是「没有出口」这件事本身**，不是一个宽松的替身。

    ``check`` 恒拒绝、``proxy_url`` 是空串。任何把它当成「可以用」的代码路径
    都会在 :func:`auxiliary_http_get` 那一步被挡住 —— 因为空串不是一个合法的
    代理地址，而 ``urllib`` 配一个空代理会**直连**。那正是契约 IA-4 要消灭的
    回退，所以这里是显式拒绝而不是留空。
    """

    implementation = "unbound"
    contract_sha256 = ""
    proxy_url = ""

    def decisions(self) -> tuple[EgressDecisionView, ...]:
        return ()

    def check(self, url: str) -> EgressDecisionView:  # noqa: ARG002 - 形状即契约
        return EgressDecisionView(
            method="UNBOUND", target="", port=0, allowed=False,
            refusal=LOCAL_CRAWL_NOT_SAFE_TO_RUN,
            detail="没有绑定出口：本机没有可用的受管正向代理。",
        )

    def browser_config_kwargs(self) -> dict[str, object]:
        """**空字典，不是一个「够用」的配置。**

        一次 ``BrowserConfig(**{})`` 会用库的缺省值把浏览器拉起来 —— 而缺省值是
        **没有代理**。所以这一格刻意是空的：它让 :func:`verify_egress_binding`
        判不合格（``browser_proxy_config_missing_server`` +
        ``browser_extra_args_not_a_sequence``），而不是给出一个看起来能用的配置。
        """
        return {}


UNBOUND_EGRESS: EgressBinding = _UnboundEgress()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 结果信封：字段名逐格对着 §3.4，但**不 import 那个共享模块**
# ---------------------------------------------------------------------------

#: 契约 §3.4 ``AdapterResult`` 的字段全集。由测试判死：本模块产出的映射
#: 必须**恰好**是这些键。共享类型由 B 拥有（``research_contract.py``），
#: 本模块不 import 也不复制它 —— 只产出一个能 ``AdapterResult(**payload)`` 的映射。
ADAPTER_RESULT_FIELDS: tuple[str, ...] = (
    "outcome", "payload", "artifacts", "upstream_ref", "actual_usd",
    "actual_source", "quota_units_used", "transport", "schema_verdict", "caveats",
)

#: 契约 §3.4 ``ArtifactRef`` 的最小形状（本模块只产出这三格）。
ARTIFACT_REF_FIELDS: tuple[str, ...] = ("ref", "kind", "sha256")


@dataclass(frozen=True)
class ArtifactRef:
    """一份落盘证据的**引用**，不是内容本身（§3.4 的注释逐字如此）。"""

    ref: str
    kind: str
    sha256: str

    def as_row(self) -> dict[str, str]:
        return {"ref": self.ref, "kind": self.kind, "sha256": self.sha256}


@dataclass(frozen=True)
class CrawlOutcome:
    """adapter 的返回值。字段与 §3.4 ``AdapterResult`` 一一对应。"""

    outcome: str
    payload: Mapping[str, Any] | None = None
    artifacts: tuple[ArtifactRef | str, ...] = ()
    upstream_ref: str | None = None
    actual_usd: Decimal | None = None
    actual_source: str = "NONE"
    quota_units_used: Decimal | None = None
    transport: str = TRANSPORT
    schema_verdict: str = "UNVERIFIED"
    caveats: tuple[str, ...] = ()
    #: 本模块**额外**携带的一格：理由码。契约 §3.4 没有它，而 §7 IF-5 要求
    #: 拒绝是闭集码。集成时它进 ``payload["reason"]``，由 INTEGRATOR 定。
    reason: str = ""

    def as_adapter_result_fields(self) -> dict[str, Any]:
        return to_adapter_result_fields(self)


def to_adapter_result_fields(outcome: CrawlOutcome) -> dict[str, Any]:
    """把本模块的结果摊成契约 §3.4 的字段映射。

    ``reason`` **不在**这十个键里 —— 它是本模块额外带的一格，按 §7 IF-5 应当进
    ``payload["reason"]``。摊平函数把它放进 ``payload``，而不是给映射加第十一个键：
    多出来的键会让 ``AdapterResult(**payload)`` 在集成时抛 ``TypeError``。
    """
    payload: dict[str, Any] = dict(outcome.payload or {})
    if outcome.reason:
        payload["reason"] = outcome.reason
    return {
        "outcome": outcome.outcome,
        "payload": payload or None,
        "artifacts": tuple(item if isinstance(item, str) else item.as_row()
                           for item in outcome.artifacts),
        "upstream_ref": outcome.upstream_ref,
        "actual_usd": outcome.actual_usd,
        "actual_source": outcome.actual_source,
        "quota_units_used": outcome.quota_units_used,
        "transport": outcome.transport,
        "schema_verdict": outcome.schema_verdict,
        "caveats": outcome.caveats,
    }


# ---------------------------------------------------------------------------
# 脱敏（IA-6）：上游响应是不可信输入
# ---------------------------------------------------------------------------

_SECRET_KEY = re.compile(
    r"(secret|token|password|passwd|api[_-]?key|authorization|cookie|credential|"
    r"private[_-]?key|session[_-]?id|bearer)",
    re.IGNORECASE,
)
_MAX_STRING = 20_000
_CLEANED = "<redacted>"


def _clean(value: Any, *, depth: int = 0) -> Any:
    """把上游响应过一遍脱敏（IA-1 + IA-6）。

    * 键名命中 :data:`_SECRET_KEY` ⇒ 值**整个**换成 :data:`_CLEANED`
      （「脱敏要消的是值不是键名」—— 留下键名是把结构说清楚，留下值才是泄漏）。
    * 超长字符串被截断并**标记**（不静默截断：静默截断让「内容真的到此为止」
      与「我们截了」在下游同形）。
    * 深度上限防止一个自引用结构把脱敏本身变成一次栈溢出。
    """
    if depth > 12:
        return "<depth-limit>"
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            cleaned[name] = (_CLEANED if _SECRET_KEY.search(name)
                             else _clean(item, depth=depth + 1))
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_clean(item, depth=depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) > _MAX_STRING:
            return value[:_MAX_STRING] + f"<truncated:{len(value) - _MAX_STRING}>"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<uncleanable:{type(value).__name__}>"


# ---------------------------------------------------------------------------
# 辅助 HTTP：经出口、不跟随重定向、有界
# ---------------------------------------------------------------------------

_CHALLENGE_HEADER = "cf-mitigated"
_CHALLENGE_MARKERS = (
    "cf-chl-", "verify you are human", "captcha challenge",
    "just a moment...", "checking your browser",
)


#: crawl4ai 0.9.3 在 ``crawl_result.error_message`` 里给反爬启发式写的那个前缀。
#: **本机实测的字符串**，不是猜的：一次合成页面（372 字节、37 可见字符）会得到
#: ``"Blocked by anti-bot protection: Structural: minimal_text on small page (…)"``，
#: 而同一页的 ``html`` / ``markdown`` 里**内容是全的**（JS 也跑过了）。
#: A 的出口给**自己发的**应答打的标记头（``marketops_safe_egress.egress_proxy``
#: 的 ``PROXY_MARKER_HEADER``）。它存在的理由是「哪个 403 是出口发的」这件事要能读出来：
#: **出口发的 403 不是来源的观测**，而**上游自己回的 403** 是。
#:
#: 本机实测：crawl4ai 0.9.3 把 ``response_headers`` 报成**第一个**响应的那一份，
#: 所以浏览器自己跟了一个重定向、落到出口的 403 上时，这个头**看不到**。
#: 因此它是一条**纵深防御**（SDK 哪天把它露出来了，这一格就会响），
#: 而真正承重的是 :func:`_redirected_away`。
PROXY_MARKER_HEADER = "X-MarketOps-Egress-Proxy"


def _attribution(taken: Mapping[str, Any], requested: str) -> dict[str, Any]:
    """这一次取回的**准确归因**：请求的 URL、最终落到的 URL、状态码、有没有跳转。

    为什么单独成函数：它是**两条**结果路径（正文与产物）共用的那一格，
    而「请求 URL」与「最终 URL」在报告上是两件事 —— 一次 302 之后，
    前者是我们问的那一个，后者是浏览器真正读到的那一个。只报前者会让一份
    **别的文档**看起来像是我们请求的那一页的产物。
    """
    # **同一个判据**：``redirected_url`` 存在**不等于**去了别处 —— 本机实测里
    # 一个正常页也会被 SDK 写上这个字段，而它的值与请求的 URL 相同。
    # 用 ``_redirected_away`` 而不是 ``bool(...)``，两条读数才不会互相打架。
    away = _redirected_away(taken, requested)
    final_url = away or str(taken.get("url") or requested)
    return {
        "requested_url": requested,
        "final_url": final_url,
        "status_code": taken.get("status_code"),
        "redirected": bool(away),
        "redirected_to": away or None,
        "sdk_redirected_url": str(taken.get("redirected_url") or "") or None,
    }


def _proxy_marker_present(headers: Mapping[str, Any]) -> bool:
    """响应头里有没有 :data:`PROXY_MARKER_HEADER`（大小写不敏感）。"""
    return any(str(key).casefold() == PROXY_MARKER_HEADER.casefold() for key in headers)


def _redirected_away(outcome: Mapping[str, Any], requested: str) -> str:
    """浏览器有没有去别的地方。**返回它去的那一处，没去就返回空串。**

    这是 IA-5（不跟随重定向）在**浏览器**这一侧的落点，而它比状态码可靠得多。

    本机实测（crawl4ai 0.9.3 + 真 Chromium）：一次 HTTP 302 **和**一次
    ``<meta http-equiv=refresh>`` 都会让 Chromium 自己跟过去；库报回来的
    ``status_code`` 是**第一个**响应的（302 或 200），而 ``html`` 是**最终**文档的。
    只看状态码的话，meta-refresh 那一次会以「200 + 一段正文」的样子通过 ——
    而那段正文其实是**出口的拒绝说明**。``redirected_url`` 把这两件事都盖住了：
    它一旦存在且与请求的 URL 不同，我们手里这份文档就**不是我们要的那一份**。

    比较时去掉 fragment：``#frag`` 不改变文档身份，而一个纯锚点跳转不该被读成
    一次「去了别处」。
    """
    landed = str(outcome.get("redirected_url") or "").strip()
    if not landed:
        return ""
    return "" if landed.split("#", 1)[0] == requested.split("#", 1)[0] else landed


ANTIBOT_ERROR_PREFIX = "Blocked by anti-bot protection"


def runtime_failure_detail(exc: Exception) -> str:
    """Keep a closed runtime diagnosis, never arbitrary exception text or URLs."""
    text = str(exc).casefold()
    kind = "SDK_EXCEPTION"
    if "asyncio.run() cannot be called" in text or "event loop is already running" in text:
        kind = "EVENT_LOOP_RUNNING"
    elif "event loop is closed" in text:
        kind = "EVENT_LOOP_CLOSED"
    elif "database is locked" in text:
        kind = "DATABASE_LOCKED"
    elif isinstance(exc, UnicodeError):
        kind = "ENCODING_ERROR"
    elif isinstance(exc, TimeoutError):
        kind = "RUNTIME_TIMEOUT"
    elif isinstance(exc, OSError):
        # Keep numeric OS diagnoses, never exception messages, URLs or paths.
        kind = "OS_ERROR"
        frames = []
        tb = exc.__traceback__
        while tb is not None:
            frames.append(Path(tb.tb_frame.f_code.co_filename).name)
            tb = tb.tb_next
        if "async_logger.py" in frames and "console.py" in frames:
            kind = "CONSOLE_WRITE_FAILED"
        for label, value in (("ERRNO", exc.errno), ("WINERROR", getattr(exc, "winerror", None))):
            if type(value) is int and 0 <= value <= 65535:
                kind += f"_{label}_{value}"
    name = type(exc).__name__
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", name):
        name = "Exception"
    return name + ":" + kind


def _sdk_failure_code(error_message: object) -> str:
    """``success=False`` 到底是哪一件事。**闭集的两格，不是字符串比较散落各处。**

    crawl4ai 0.9.3 的 ``is_blocked`` 是一个**内容启发式**（页小、可见文本少、
    没有语义元素就判成「被反爬挡了」），它自己的注释写着
    「false positives are cheap (the fallback mechanism rescues them)」——
    而我们**没有**那个 fallback，所以误判会原样落到收据上。

    两种情形必须分开报，因为读的人要的答案不同：

    * :data:`SOURCE_ANTIBOT_HEURISTIC` —— 库**怀疑**这一页是被挡下的；
      取回本身是成功的（`html` 有内容）。它是一条要人来判的读数，
      不是一次传输失败。
    * :data:`LOCAL_CRAWL_FAILED` —— 其余的 ``success=False``。
    """
    text = str(error_message or "")
    if text.startswith(ANTIBOT_ERROR_PREFIX):
        return "SOURCE_ANTIBOT_HEURISTIC"
    return "LOCAL_CRAWL_FAILED"


def _challenge_markers(headers: Mapping[str, str], body: str) -> str:
    """判断这是不是一张挑战页。返回命中的标记（空串 = 不是）。

    两路都要判，理由是 ``crawl4ai_fetcher.py`` 量到的那个：一个被挡下的页面
    **可以**带着 200 与 ``success=True`` 回来，于是只看状态码会把一张
    「我不是人」的中间页当成一篇研究材料。
    """
    for key, value in headers.items():
        if str(key).casefold() == _CHALLENGE_HEADER and str(value).casefold() == "challenge":
            return f"header:{_CHALLENGE_HEADER}"
    probe = body[:4000].casefold()
    for marker in _CHALLENGE_MARKERS:
        if marker in probe:
            return f"body:{marker}"
    return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """IA-5：**不跟随重定向**。

    ``urllib`` 的缺省是跟随（最多 10 跳），而「跟随」正是「我们判过的 URL 与
    真正连上的 URL 不是同一个」的实现方式。这里把它变成一个明确的拒绝。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(
            req.full_url, code, f"CRAWL_REDIRECT_REFUSED:{newurl}", headers, fp,
        )


@dataclass(frozen=True)
class AuxiliaryResponse:
    """辅助 HTTP 的回应。``body`` 是**已解码**的文本（可能是空串）。"""

    status: int
    url: str
    body: str
    bytes_read: int
    headers: Mapping[str, str] = field(default_factory=dict)


def auxiliary_http_get(
    binding: EgressBinding,
    url: str,
    *,
    timeout_s: float = 15.0,
    max_bytes: int = 2 * 1024 * 1024,
    accept: str = "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9",
) -> AuxiliaryResponse:
    """经出口取一份辅助材料（robots / sitemap / 链接页）。

    三条纪律都在这里落地，且**顺序**是承重的：

    1. **先验出口绑定**。没有出口 ⇒ :data:`LOCAL_CRAWL_NOT_SAFE_TO_RUN`，
       在构造 opener **之前**。顺序反过来会留下一个「装了 opener 但没用」的
       中间态，而那个中间态正是下一次有人把 ``if`` 改成 ``if True`` 的地方。
    2. **预检** ``binding.check(url)``。被拒 ⇒ ``EGRESS_REFUSED``，
       并把出口给的 ``refusal`` 原样带出来（不重新命名 —— 理由是出口的，
       不是我们的）。
    3. **有界读取**：``read(max_bytes + 1)``，多读一字节只是用来判「超了」。
    """
    violations = verify_egress_binding(binding)
    if violations:
        raise _egress_error(LOCAL_CRAWL_NOT_SAFE_TO_RUN,
                            detail=f"出口绑定不合格：{list(violations)}")
    if not binding.proxy_url:
        # 显式再来一次：空代理地址在 urllib 里等于**直连**，那正是 IA-4 禁止的回退。
        raise _egress_error(LOCAL_CRAWL_NOT_SAFE_TO_RUN)

    _managed_browser_url(url)
    decision = binding.check(url)
    if not decision.allowed:
        raise _egress_error("EGRESS_REFUSED", detail=f"{decision.refusal} {decision.detail}".strip())

    from research_always_proxy import AlwaysProxyHandler
    # Through the egress proxy even for a host NO_PROXY names: the guard lives there.
    opener = urllib.request.build_opener(
        AlwaysProxyHandler({"http": binding.proxy_url,
                            "https": binding.proxy_url}),
        _NoRedirect(),
    )
    request = urllib.request.Request(url, method="GET", headers={
        "Accept": accept,
        "User-Agent": "MarketOps-Research/1.0 (+anonymous public browsing)",
    })
    try:
        with opener.open(request, timeout=timeout_s) as response:
            raw = response.read(max_bytes + 1)
            headers = {str(key): str(value) for key, value in response.headers.items()}
            status = int(getattr(response, "status", 0) or 0)
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        if "CRAWL_REDIRECT_REFUSED" in str(exc):
            raise _egress_error("CRAWL_REDIRECT_REFUSED",
                                detail=f"{exc.code} -> {exc.reason}") from None
        if exc.code in {401, 403, 429}:
            raise _egress_error("SOURCE_ACCESS_BLOCKED", detail=f"HTTP {exc.code}") from None
        raise _egress_error("LOCAL_CRAWL_RUNTIME_FAILED", detail=f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise _egress_error("LOCAL_CRAWL_RUNTIME_FAILED",
                            detail=type(exc).__name__) from None

    if len(raw) > max_bytes:
        raise CrawlRefusalError(
            CrawlRefusal.CRAWL_BYTE_CAP_EXCEEDED,
            f"{url} 的响应超过 {max_bytes} 字节上限；连接已掐断。",
        )
    body = raw.decode("utf-8", errors="replace")
    marker = _challenge_markers(headers, body)
    if marker:
        # 挑战页的正文**不返回**：它既不是材料，也不该被下游镜像到盘上。
        raise _egress_error("SOURCE_CHALLENGE_PAGE", detail=marker)
    return AuxiliaryResponse(status=status, url=final_url, body=body,
                             bytes_read=len(raw), headers=headers)


class UpstreamRefusal(ValueError):
    """出口 / 上游 / 本地运行的拒绝。理由码取自契约 §5.3 的那张闭集。

    **刻意不是** :class:`CrawlRefusalError`：``CrawlRefusal`` 判的是「这一层的
    参数不行」（改请求），而 ``EGRESS_REFUSED`` / ``SOURCE_CHALLENGE_PAGE`` /
    ``LOCAL_CRAWL_RUNTIME_FAILED`` 判的是「这条路走不通」（换来源或重试另说）。
    两者在下游的处置不同，合成一个类型会让那个区别只在读代码时才看得见。
    """

    def __init__(self, code: str, detail: str = "") -> None:
        code = _reason(code)
        super().__init__(code)
        self.code = code
        self.detail = detail


def _egress_error(code: str, *, detail: str = "") -> UpstreamRefusal:
    return UpstreamRefusal(code, detail)


#: 受管浏览器只允许这两个 scheme。``file:`` / ``ftp:`` / ``data:`` 一旦进了
#: Chromium，浏览器自己就能读到本机文件或内联内容，而出口的 HTTP 判据看不见它们。
_WEB_BROWSER_SCHEMES = frozenset({"http", "https"})


def _managed_browser_url(url: str) -> str:
    """把一个**要交给浏览器**的 URL 判成 http(s)，否则抛拒绝。

    这一格是 scheme 与凭据的边界，不是 DNS 边界（DNS 由出口与
    ``--host-resolver-rules`` 管）。判定发生在 ``egress.check`` **之前**：
    一个 ``file:///etc/passwd`` 的目标连出口都不该问，更不该把浏览器拉起来。
    """
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in _WEB_BROWSER_SCHEMES:
        raise _egress_error(
            "EGRESS_REFUSED",
            detail=f"受管浏览器只允许 http/https，收到 scheme={scheme or '<none>'!r}",
        )
    if not parts.hostname:
        raise _egress_error("EGRESS_REFUSED", detail="URL 没有 hostname")
    if parts.username or parts.password:
        raise _egress_error("EGRESS_REFUSED",
                            detail="URL 不得携带凭据（userinfo）")
    try:
        parts.port
    except ValueError:
        raise _egress_error("EGRESS_REFUSED", detail="URL 的端口无效") from None
    return url


# ---------------------------------------------------------------------------
# SDK 接线：可注入，因为没有 crawl4ai 的离线环境要能判「我们写了那些决定」
# ---------------------------------------------------------------------------

def crawl4ai_available() -> bool:
    try:
        return importlib.util.find_spec("crawl4ai") is not None
    except (ImportError, ValueError):
        return False


def _crawl4ai_module() -> Any:
    import importlib

    return importlib.import_module("crawl4ai")


#: WebRTC 自己开套接字，**不经过** ``--proxy-server``。``disable_non_proxied_udp``
#: 是 Chromium 的 WebRTC IP handling policy 里唯一同时做到「WebRTC 走代理」与
#: 「不允许非代理 UDP」的取值；没有它，一个页面可以用 STUN/ICE 把本机地址与
#: 一条不走代理的 UDP 路径同时暴露出来，而出口上一条判决都不会留下。
#:
#: 开关名是**实测**的：本机 Playwright Chromium 1243 的二进制里存在
#: ``webrtc-ip-handling-policy`` 与取值 ``disable_non_proxied_udp``，
#: **不存在**带 ``force-`` 前缀的写法。写错前缀的参数会被 Chromium 静默忽略，
#: 而配置看起来完全正常 —— 这正是这一格需要二进制读数而不是记忆的原因。
WEBRTC_NON_PROXIED_UDP_GUARD = (
    "--webrtc-ip-handling-policy=disable_non_proxied_udp"
)

#: 浏览器自己的 DNS。代理只接管 HTTP(S)；Chromium 仍可在本地解析名字，这正是
#: DNS rebinding 的第一步（判据看到 A，连接落到 B）。``MAP * ~NOTFOUND`` 关掉
#: 本地解析，名字交给代理那一侧解析 —— 出口的判据就在那里。
#: ``EXCLUDE`` 只留给本机代理自身（``localhost`` / ``127.0.0.1`` 不是 DNS 目标）。
DNS_REBINDING_GUARD = (
    "--host-resolver-rules=MAP * ~NOTFOUND,EXCLUDE localhost,EXCLUDE 127.0.0.1"
)

#: 受管浏览器**不可协商**的四条启动护栏。前两条来自 A 的 guarded_http；
#: 后两条是本 Lane 加的 WebRTC / DNS 边界。出口可以补充别的参数，但不能把
#: 这四条换掉或降级 —— 见 :func:`browser_launch_violations`。
BROWSER_SECURITY_ARGS: tuple[str, ...] = (
    LOOPBACK_BYPASS_GUARD,
    "--disable-quic",
    WEBRTC_NON_PROXIED_UDP_GUARD,
    DNS_REBINDING_GUARD,
)

#: 与 ``marketops/adapters/crawl4ai_fetcher.py::_browser_config`` 对齐的那两格，
#: 加上本 Lane 的 WebRTC / DNS 两格。**顺序是承重的**：出口给的参数在前，
#: 缺失的强制项按这里的顺序补在后面；同名项只保留一个。
BROWSER_EXTRA_ARGS: tuple[str, ...] = BROWSER_SECURITY_ARGS

#: 只检查启动参数能说什么、**不能**说什么。``FLAG_ENFORCED`` 的意思是
#: 「这一格写进了 BrowserConfig」，不是「已在本机网络上量到隔离」。OS 级 UDP
#: 边界必须由真实 Chromium + 抓包/系统观测来判；本 Lane 不做这件事，所以那两格
#: 是 ``UNPROVEN`` / ``NOT_RUN``，且不得被绿色测试读成 ``verified``。
BROWSER_SECURITY_BOUNDARY: Mapping[str, str] = {
    "webrtc_non_proxied_udp": "FLAG_ENFORCED",
    "local_dns_resolution": "FLAG_ENFORCED",
    "quic_udp": "FLAG_ENFORCED",
    "os_level_udp_isolation": "UNPROVEN",
    "runtime_udp_measurement": "NOT_RUN",
}

#: 出口给的 ``extra_args`` 里，这些形态会**改写或绕过**上面四条护栏。
#: 名字是稳定的判据读数，测试与 transport 都读它。
_CONFLICTING_FLAG_PREFIXES: tuple[tuple[str, str], ...] = (
    ("--webrtc-ip-handling-policy=", "browser_webrtc_policy_override"),
    ("--host-resolver-rules=", "browser_host_resolver_rules_override"),
    ("--proxy-server=", "browser_proxy_server_override"),
    ("--proxy-bypass-list=", "browser_proxy_bypass_override"),
)


def browser_launch_violations(egress_kwargs: Mapping[str, object]) -> tuple[str, ...]:
    """判一份 ``BrowserConfig`` kwargs 能不能用来启动受管浏览器。

    返回**违反项**（空元组 = 可以启动）。这一层判三件事：

    1. 代理必须从 ``proxy_config.server`` 来，且是**本机回环**上的 http 代理。
       没有代理就是没有出口 —— 不允许「配了但没接上」的形状。
    2. ``extra_args`` 必须是字符串序列。
    3. 出口**不得**用同名参数把护栏降级（例如把 WebRTC policy 改回 ``default``）、
       另指一个代理（``--proxy-server=``）、关掉代理（``--no-proxy-server``）
       或扩大 bypass 名单（``--proxy-bypass-list=*``）。

    判形状不等于判行为：这里说「这份配置可以被启动」，不说「网络已经隔离」。
    """
    violations: list[str] = []
    if not isinstance(egress_kwargs, Mapping):
        return ("browser_config_kwargs_not_a_mapping",)
    proxy_config = egress_kwargs.get("proxy_config")
    server = (str(proxy_config.get("server") or "").strip()
              if isinstance(proxy_config, Mapping) else "")
    if not server:
        violations.append("browser_proxy_config_missing_server")
    else:
        parsed = urllib.parse.urlsplit(server)
        if parsed.scheme != "http" or (parsed.hostname or "") not in {
                "127.0.0.1", "::1", "localhost"}:
            violations.append("browser_proxy_not_loopback")
    extra_args = egress_kwargs.get("extra_args")
    if not isinstance(extra_args, (list, tuple)):
        violations.append("browser_extra_args_not_a_sequence")
        return tuple(dict.fromkeys(violations))
    if any(not isinstance(arg, str) or not arg.strip() for arg in extra_args):
        violations.append("browser_extra_args_not_strings")
    for arg in extra_args:
        if not isinstance(arg, str):
            continue
        # 不带 ``=`` 的同名 switch 也可能把值放在下一个 argv 里；同样不许。
        if arg in {"--no-proxy-server", "--proxy-server"}:
            violations.append("browser_proxy_server_override")
            continue
        if arg in {"--webrtc-ip-handling-policy", "--host-resolver-rules",
                   "--proxy-bypass-list"}:
            violations.append("browser_flag_value_not_verified")
            continue
        if arg == "--enable-quic" or arg.startswith("--enable-quic="):
            violations.append("browser_quic_reenabled")
            continue
        for prefix, name in _CONFLICTING_FLAG_PREFIXES:
            if not arg.startswith(prefix):
                continue
            # 与本模块的强制项逐字相同的参数是幂等的，不算降级。
            if arg not in BROWSER_SECURITY_ARGS:
                violations.append(name)
            break
    return tuple(dict.fromkeys(violations))


def hardened_browser_extra_args(egress_kwargs: Mapping[str, object]) -> list[str]:
    """出口给的参数 + 四条强制护栏。**返回新列表，不改调用方的那一份。**

    去重是承重的：同一个 switch 出现两次时，Chromium 取哪一份不是我们能依赖的
    契约。强制项放在后面，是为了让「缺少时补上」这件事在读数上看得见。
    """
    raw = egress_kwargs.get("extra_args")
    merged: list[str] = []
    for arg in raw if isinstance(raw, (list, tuple)) else ():
        if isinstance(arg, str) and arg.strip() and arg not in merged:
            merged.append(arg)
    for mandatory in BROWSER_EXTRA_ARGS:
        if mandatory not in merged:
            merged.append(mandatory)
    return merged


def _build_browser_config(module: Any, *, egress_kwargs: Mapping[str, object],
                          user_agent: str, headless: bool = True) -> Any:
    """浏览器的出口。**这是浏览器唯一的出口设置点，而配置来自出口自己。**

    用 ``proxy_config`` 而不是 ``proxy``：``crawl4ai_fetcher.py`` 实测过，
    固定版本里 ``BrowserConfig.proxy`` 已标 deprecated 并会在一条路径上被忽略 ——
    照抄那个字段名会得到一个「配了但没接上」的部署，而它看起来完全正常。

    出口给的 ``extra_args`` 原样保留；**四条安全护栏由本模块补齐，且出口不能
    降级它们**（:func:`browser_launch_violations`）。判不合格时抛
    ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``：这一次浏览器不启动，且**没有直连回退**。
    """
    violations = browser_launch_violations(egress_kwargs)
    if violations:
        raise _egress_error(
            LOCAL_CRAWL_NOT_SAFE_TO_RUN,
            detail="浏览器启动配置不合格：" + ", ".join(violations),
        )
    return module.BrowserConfig(
        headless=headless,
        # MCP is a background transport, not a terminal. A progress write to a
        # closed/invalid host stderr must never turn fetched HTML into failure.
        verbose=False,
        user_agent=user_agent,
        proxy_config=dict(egress_kwargs.get("proxy_config") or {}),
        extra_args=hardened_browser_extra_args(egress_kwargs),
    )


def _build_run_config(module: Any, *, page_timeout_ms: int,
                      artifact: str | None = None) -> Any:
    """每一次渲染的 run config。**三格都是判定，一格都不是缺省。**

    * ``max_retries=0``：库的缺省今天恰好是 0，而缺省不是判定 —— 一次升级把它改成 3
      会让每个挑战页被试三次，而我们的账上仍然只写一行。
    * ``cache_mode="BYPASS"``：读缓存会让两次抓取读到同一份旧正文，而回执上写着
      「刚刚抓到了」。
    * ``page_timeout``：一个有界的等待，从请求来，不用库的缺省。
    """
    kwargs: dict[str, object] = {
        "page_timeout": page_timeout_ms,
        # arun overwrites the logger's verbosity from this separate config.
        "verbose": False,
        "cache_mode": "BYPASS",
        "max_retries": 0,
    }
    if artifact:
        # 产物在**这一个** config 上请求。实测：不在它上面要，读回来的永远是空。
        kwargs[str(artifact)] = True
    return module.CrawlerRunConfig(**kwargs)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

_REQUIRED_PARAM_KEYS: Mapping[str, frozenset[str]] = {
    "crawl4ai.fetch_page": frozenset({"url", "mode", "max_chars", "limits", "extraction"}),
    "crawl4ai.extract": frozenset({"url", "html", "extraction", "limits", "max_chars"}),
    "crawl4ai.deep_crawl": frozenset({"seeds", "allowed_domains", "limits", "max_depth",
                                     "extraction", "sitemap_urls"}),
    "crawl4ai.sitemap_seed": frozenset({"sitemap_urls", "allowed_domains", "limits",
                                       "extraction"}),
    "crawl4ai.screenshot": frozenset({"url", "limits", "artifacts"}),
    "crawl4ai.pdf": frozenset({"url", "limits", "artifacts"}),
}

_LIMIT_KEYS = ("max_pages", "max_depth", "max_domains", "max_bytes", "max_seconds",
               "max_browser_seconds", "max_artifacts")


#: 本 adapter 的 capability_id → **reviewed registry** 的名字。**这是给集成侧读的提示，
#: 不是一张它会自动采信的授权表。**
#:
#: 两套词汇都不是错的：adapter 命名的是它**技术上能做**什么（``crawl4ai.pdf``），
#: registry 命名的是**已审核、已定价**的那几格（``render`` / ``extract``）。
#: 用 adapter 的名字去预约会得 ``UNREVIEWED_CAPABILITY`` —— 那是 registry 在正确工作。
#:
#: 本机对着集成 ``d88b176`` 实测：它的 ``REVIEWED_CAPABILITY`` 只覆盖
#: ``crawl4ai.fetch_page``，其余五个抛 ``WiringIncomplete``；而它表里另有两条
#: （``crawl4ai.crawl_local`` / ``crawl4ai.crawl_cloud``）指向**本 adapter 不提供**的名字。
#: 下面这张表是 E 侧认为的**完整**对应，供 INTEGRATOR 对齐 —— 它不改变任何一侧的行为。
#:
#: **registry 里 crawl4ai 只有两格**（``render`` 与 ``extract``），所以五个「取一页/渲染」
#: 能力都落到 ``render``。要在 registry 里区分 PDF 与截图、深爬与单页，那是 **W-B 的
#: registry** 要加条目的事（一次 §8 变更），不是在 adapter 里造名字能解决的。
REVIEWED_CAPABILITY_HINTS: Mapping[str, str] = {
    "crawl4ai.fetch_page": "render",
    "crawl4ai.extract": "extract",
    "crawl4ai.deep_crawl": "render",
    "crawl4ai.sitemap_seed": "render",
    "crawl4ai.screenshot": "render",
    "crawl4ai.pdf": "render",
}

#: :meth:`Crawl4AIAdapter.readiness` 的**闭集**结论。
READINESS_VERDICTS: tuple[str, ...] = (
    "READY",
    "EGRESS_NOT_BOUND",
    "SDK_NOT_INSTALLED",
)


class Crawl4AIAdapter:
    """契约 §6 的 ``ProviderAdapter``。``provider`` 是类属性，Registry 靠它配。

    构造参数里的每一个可注入项都只有一个理由：**让它可判**。

    * ``egress`` —— 出口。缺省 :data:`UNBOUND_EGRESS`，于是缺省路径不联网。
    * ``sdk_available`` —— 有没有 crawl4ai。缺省查 ``find_spec``。
    * ``module_loader`` —— 真库的取法。测试注入假模块来判「我们写了那几格决定」。
    * ``clock`` —— 单调时钟。测试用它证明「超时是真的按秒判的」，而不是靠 sleep。
    """

    provider = PROVIDER

    def __init__(
        self,
        *,
        egress: EgressBinding | None = None,
        sdk_available: Callable[[], bool] | None = None,
        module_loader: Callable[[], Any] | None = None,
        clock: Callable[[], float] | None = None,
        user_agent: str = "MarketOps-Research/1.0 (+anonymous public browsing)",
        artifact_root: str | None = None,
        material_writer: Callable | None = None,
        cache_base_directory: str | None = None,
    ) -> None:
        self._egress = egress or UNBOUND_EGRESS
        self._sdk_available = sdk_available or crawl4ai_available
        self._module_loader = module_loader or _crawl4ai_module
        self._clock = clock or time.monotonic
        self._user_agent = user_agent
        self._artifact_root = artifact_root
        self._material_writer = material_writer
        self._cache_base_directory = cache_base_directory
        self._last_decisions: tuple[EgressDecisionView, ...] = ()

    # -- §6 Protocol ------------------------------------------------------
    def capabilities(self) -> tuple[str, ...]:
        return CAPABILITIES

    def readiness(self) -> Mapping[str, object]:
        """**不执行任何东西**地回答问题：「这个 adapter 现在能干活吗，差什么？」

        为什么需要它：集成的 ``assemble_adapters()`` 是 ``Crawl4AIAdapter()`` ——
        不传出口。于是**构造成功**、而每一次 ``execute`` 都 ``BLOCKED`` /
        ``LOCAL_CRAWL_NOT_SAFE_TO_RUN``。本机对着集成 ``d88b176`` 实测过这条：
        装配表里有 crawl4ai，请求却全被拒，而报告上看不出「缺的是出口」还是
        「这台机器没装浏览器」。

        一格结论、一句该做什么，**闭集**：
        ``READY`` / ``EGRESS_NOT_BOUND`` / ``SDK_NOT_INSTALLED``。
        """
        violations = list(verify_egress_binding(self._egress))
        if not violations:
            # 形状合格还不够：出口给的启动参数可能把护栏降级。把这件事放在
            # readiness 里，操作员在第一次 execute 之前就能看见。
            try:
                launch_kwargs = self._egress.browser_config_kwargs()
            except Exception as exc:  # noqa: BLE001 - 形状对了但一调就炸
                violations.append(
                    f"browser_config_kwargs_raised:{type(exc).__name__}")
            else:
                violations.extend(browser_launch_violations(launch_kwargs))
        sdk = bool(self._sdk_available())
        if violations:
            verdict, action = "EGRESS_NOT_BOUND", (
                "把 W-A 的 SafeEgressSession 包成 EgressBinding 传进构造器；"
                f"当前不合格项：{list(violations)}")
        elif not sdk:
            verdict, action = "SDK_NOT_INSTALLED", (
                "在这个解释器里装 crawl4ai（requirements-crawl-local.txt）。")
        else:
            verdict, action = "READY", ""
        assert verdict in READINESS_VERDICTS
        return {
            "verdict": verdict,
            "action": action,
            "provider": PROVIDER,
            "egress_bound": not violations,
            "egress_violations": list(violations),
            "sdk_available": sdk,
            "capabilities": list(CAPABILITIES),
            "reviewed_capability_hints": dict(REVIEWED_CAPABILITY_HINTS),
            # 启动参数能保证什么、不能保证什么，**在这里说清楚**。只检查 flags
            # 不是网络实测：OS 级 UDP 边界仍是 UNPROVEN，测量仍是 NOT_RUN。
            "browser_security": dict(BROWSER_SECURITY_BOUNDARY),
        }

    def probe_schema(self) -> Mapping[str, object]:
        """只读握手。**本 Wave 没有授权做**（DECISION-04），所以这里不伪造快照。

        返回的不是一张快照，而是「我们没能拿到快照」这件事的三个可区分取值：
        没装 SDK / 没接出口 / 未授权。三者在报告上是三句不同的话。
        """
        if not self._sdk_available():
            status, detail = "AUTH_REQUIRED", "CRAWL4AI_LOCAL_NOT_INSTALLED"
        elif verify_egress_binding(self._egress):
            status, detail = "AUTH_REQUIRED", LOCAL_CRAWL_NOT_SAFE_TO_RUN
        else:
            status, detail = "AUTH_REQUIRED", "DECISION-04_NOT_AUTHORIZED"
        return {
            "status": status,
            "provider": PROVIDER,
            "transport": TRANSPORT,
            "upstream_tools_list": None,
            "schema_verdict": "UNVERIFIED",
            "detail": detail,
            "note": ("未获授权的只读握手不会产生快照。此处**不**伪造一张："
                     "一张假快照会让 verify_schema 报 MATCH，而那个 MATCH 是假的。"),
        }

    def execute(self, req: Any) -> CrawlOutcome:
        """执行一次已授权的请求。**前置条件：已过 Policy 与 Ledger.reserve。**

        ``req`` 是 duck-typed 的（契约 §3.1 ``CapabilityRequest``）：
        读 ``capability_id`` / ``params`` / ``run_id`` / ``operation_id``。
        不 import 那个共享类型 —— B 拥有它，而这里只需要四个只读属性。
        """
        capability_id = str(getattr(req, "capability_id", "") or "")
        run_id = str(getattr(req, "run_id", "") or "")
        operation_id = str(getattr(req, "operation_id", "") or "")
        params = getattr(req, "params", None) or {}
        if not isinstance(params, Mapping):
            return self._refusal("CAPABILITY_PARAM_NOT_ALLOWED", "params 必须是一个映射")

        if capability_id not in CAPABILITIES:
            # IR-1：未知 id ⇒ 本地 BLOCKED，**不触达网络**。
            return self._refusal("CAPABILITY_UNKNOWN", f"未知的 capability_id {capability_id!r}",
                                 capability_id=capability_id)

        # **顺序是承重的**：禁止的意图（任意 JS / 站点登录 / 凭据）先判，再判
        # 越权参数。两者都会拒绝，但理由码不同，而读的人要的答案不同 ——
        # 「你带了 js_code」与「你带了一个我不认识的键」是两句话。
        # 反过来写的话，``js_code`` 会以「越权参数」的身份被拒，而那条报告
        # 读起来像一次拼写问题。
        for refusal in forbidden_feature_reasons(params):
            return self._refusal(_reason(refusal.value), "请求里出现了禁止的键",
                                 capability_id=capability_id)

        allowed = _REQUIRED_PARAM_KEYS[capability_id]
        unknown = {str(key) for key in params} - allowed
        if unknown:
            # IR-2：越权参数 ⇒ BLOCKED。
            return self._refusal("CAPABILITY_PARAM_NOT_ALLOWED",
                                 f"不在 allowed_params 里的键：{sorted(unknown)}",
                                 capability_id=capability_id)

        try:
            if capability_id == "crawl4ai.extract" and params.get("html") is not None:
                return self._execute_local_extract(capability_id, run_id, operation_id, params)
            if capability_id == "crawl4ai.sitemap_seed":
                return self._execute_sitemap_seed(capability_id, run_id, operation_id, params)
            if capability_id == "crawl4ai.deep_crawl":
                return self._execute_deep_crawl(capability_id, run_id, operation_id, params)
            return self._execute_single(capability_id, run_id, operation_id, params)
        except UpstreamRefusal as exc:
            return self._route_refusal(exc.code, exc.detail, capability_id)
        except CrawlRefusalError as exc:
            return self._route_refusal(exc.code, exc.detail, capability_id)

    def _route_refusal(self, code: str, detail: str, capability_id: str,
                       meter: BudgetMeter | None = None) -> CrawlOutcome:
        """一个码落在 ``BLOCKED`` 还是 ``FAILED``，由**它发生的位置**决定。"""
        if code in _POST_DISPATCH_CODES:
            return self._failure(code, detail, capability_id=capability_id, meter=meter)
        outcome = self._refusal(code, detail, capability_id=capability_id)
        if meter is not None:
            # 派发前被拒也留一份配额读数：全零，而全零是**一个读数**。
            # 少了它，「这一格没填」与「这一格是 0」在下游同形。
            payload = dict(outcome.payload or {})
            payload["meter"] = meter.snapshot()
            outcome = replace(outcome, payload=payload,
                              quota_units_used=Decimal(meter.pages))
        return outcome

    def cancel(self, upstream_ref: str) -> CrawlOutcome:
        """幂等取消。**本地 SDK 没有可取消的上游操作**，如实说。

        契约 §6 的 ``cancel`` 注释是「仅当上游声明该操作幂等时可用」。
        ``local_sdk`` 这一档没有可以取消的远端对象（浏览器进程是**我们**起的，
        它的生命周期由预算与超时管），所以这里返回 ``CAPABILITY_NOT_AVAILABLE``，
        而不是假装取消成功 —— 一个假的成功会让调用方以为远端已经停了。
        """
        return CrawlOutcome(
            outcome="BLOCKED",
            reason=_reason("CAPABILITY_NOT_AVAILABLE"),
            payload={
                "reason": _reason("CAPABILITY_NOT_AVAILABLE"),
                "upstream_ref": str(upstream_ref or ""),
                "explanation": ("local_sdk 没有可取消的上游操作；浏览器进程由本机的"
                                "预算与超时管，不由上游句柄管。"),
            },
            caveats=("cancel 返回的是「这条路上没有这个动作」，不是「已经取消了」。",),
        )

    # -- 内部 -------------------------------------------------------------
    def _refusal(self, code: str, detail: str, *, capability_id: str = "") -> CrawlOutcome:
        """把一次拒绝装成 §3.4 的形状。

        **两格一起给**，因为下游要的答案不同：

        * ``caveats`` 的第一格是 **§5.3 闭集成员**（:func:`_contract_code`）——
          集成侧的网关只认这一格（``_result_refusal_code``），而一个闭集外的码
          会让它抛 ``ContractViolation``。这不是为了哄它，是因为它拒得对：
          一张能被翻译成「像样的码」的表会让那张闭集失去可审计性。
        * ``payload["reason"]`` 是**精确的本地码**，供人读与供判据断言 ——
          「上限超了」与「域名不在批准清单里」都翻成 ``BUDGET_OPERATION_CEILING``
          与 ``POLICY_DOMAIN_NOT_ALLOWED`` 之后，只剩这一格能区分它们。

        派发之后的失败不走这里（见 :meth:`_failure` 与 :data:`_POST_DISPATCH_CODES`）。
        """
        reason = _reason(code)
        if code in _POST_DISPATCH_CODES:
            # 一个编程错误：这几个码是 ``FAILED``，它们发生过，不是「没触达上游」。
            raise AssertionError(
                f"{code!r} 是派发之后的失败，必须走 _failure —— 记成 BLOCKED 会说"
                "「我们没出去」，而那是假的。"
            )
        return CrawlOutcome(
            outcome="BLOCKED",
            reason=reason,
            payload={"reason": reason, "contract_reason_code": _contract_code(reason),
                     "detail": _clean(detail), "capability_id": capability_id or None},
            caveats=(_contract_code(reason),
                     "本地拒绝，未触达上游（契约 IL-4：pre_dispatch 的本地拒绝 "
                     "⇒ BLOCKED + NO_CHARGE_PROVEN）。"),
        )

    def _failure(self, code: str, detail: str, *, capability_id: str = "",
                 meter: BudgetMeter | None = None) -> CrawlOutcome:
        """**已经派发过**的失败：§2 的 ``FAILED``，不是 ``BLOCKED``。

        只对 :data:`_POST_DISPATCH_CODES` 里的码有效 —— 其余的在
        :meth:`_route_refusal` 那一步就走了 ``BLOCKED``。这一句用断言钉住，
        因为「一个没派发过的码被记成 FAILED」会让账上说「我们出去了」。
        """
        reason = _reason(code)
        if reason not in _POST_DISPATCH_CODES:
            raise AssertionError(
                f"{reason!r} 不在 _POST_DISPATCH_CODES 里 —— 没派发过的失败记成"
                "FAILED 会反过来把「没出去」说成「出去了」。"
            )
        payload = {"reason": reason, "detail": _clean(detail),
                   "capability_id": capability_id or None}
        quota: Decimal | None = None
        if meter is not None:
            # 配额单位（页 / 秒 / 产物），**不是** USD：本地这一档没有厂商计价，
            # 而「一次失败的抓取也烧掉了本机的资源」是一件要记账的事。
            quota = Decimal(meter.pages)
            payload["meter"] = meter.snapshot()
        return CrawlOutcome(
            outcome="FAILED", reason=reason, payload=payload,
            transport=TRANSPORT, schema_verdict="UNVERIFIED",
            quota_units_used=quota,
            caveats=(reason,
                     "请求已经发出（上游明确失败），所以这是 FAILED 而不是 BLOCKED。"),
        )

    def _require_egress(self) -> EgressBinding:
        if verify_egress_binding(self._egress):
            raise _egress_error(LOCAL_CRAWL_NOT_SAFE_TO_RUN)
        return self._egress

    def _persist_artifact(self, payload: bytes, *, kind: str, digest: str) -> str | None:
        """把产物落到 ``artifact_root`` 下，返回绝对路径；没配就返回 ``None``。

        **文件名只由 hash 与 kind 决定**，不掺任何调用方给的字符串 —— 一次路径穿越
        只可能来自文件名那一格，而这一格没有可变输入。根必须是绝对路径，且写完
        之后校验它确实落在根里（``resolve()`` 之后比前缀），不是靠「拼起来像」。

        没配根时**不落盘**，引用退回一个合成标签，并在 caveats 里说明 ——
        一个指向不存在文件的引用与一个不存在的引用在报告上是两件事。
        """
        if not self._artifact_root:
            return None
        root = Path(self._artifact_root).expanduser()
        if not root.is_absolute():
            raise CrawlRefusalError(
                CrawlRefusal.EXTRACTION_SPEC_INVALID,
                f"artifact_root 必须是绝对路径，收到 {self._artifact_root!r}")
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = (root / f"{digest}.{kind}").resolve()
        if root != target.parent:
            raise CrawlRefusalError(
                CrawlRefusal.EXTRACTION_SPEC_INVALID,
                "产物路径逃出了 artifact_root —— 文件名只由 hash 决定，不该发生")
        target.write_bytes(payload)
        return str(target)

    def _refuse_unusable_page(self, outcome: Mapping[str, Any], url: str,
                              html: str, headers: Any) -> None:
        """这一份文档能不能当作**请求的那个 URL** 的材料。不能就抛。

        **判定的顺序是承重的**，而且它来自本机实测（crawl4ai 0.9.3 + 真 Chromium）：

        1. **浏览器有没有去别处**（``redirected_url``）。一次 HTTP 302 **和**一次
           ``<meta http-equiv=refresh>`` 都会让 Chromium 自己跟过去，而库报回来的
           ``status_code`` 是**第一个**响应的、``html`` 是**最终**文档的。被出口
           拒绝的那一跳，最终文档就是**出口的拒绝说明** —— 只看状态码的话，
           那一段说明会以「200 + 一段正文」的样子被记成来源材料。
        2. **出口自发应答的标记头**（纵深防御；SDK 今天不露它，见常量注释）。
        3. **3xx**：没跟随，所以手里这份不是那一页。
        4. 401/403/429：上游明确拒绝。
        5. ``success=False``：分辨反爬启发式与真失败。
        6. 空正文。
        """
        landed = _redirected_away(outcome, url)
        if landed:
            raise _egress_error(
                "CRAWL_REDIRECT_REFUSED",
                detail=f"浏览器被带到了 {landed}，而不是请求的那个 URL",
            )
        if _proxy_marker_present(headers if isinstance(headers, Mapping) else {}):
            raise _egress_error("EGRESS_REFUSED",
                                detail="这一份文档的响应带着出口自发应答的标记头")
        status = outcome.get("status_code")
        if isinstance(status, int) and 300 <= status < 400:
            raise _egress_error("CRAWL_REDIRECT_REFUSED", detail=f"HTTP {status}（未跟随）")
        if status in {401, 403, 429}:
            raise _egress_error("SOURCE_ACCESS_BLOCKED", detail=f"HTTP {status}")
        if not outcome.get("success"):
            message = str(outcome.get("error_message") or "")
            raise _egress_error(_sdk_failure_code(message), detail=message[:200])
        if not html.strip():
            raise _egress_error("SOURCE_EMPTY_EXTRACT")

    def _require_egress_kwargs(self, egress: EgressBinding) -> Mapping[str, object]:
        """出口给的那一份浏览器配置。**在它被交给任何浏览器之前判一次。**

        两个来源各写一份字符串的失败方式是安静的：改了一处、另一处照旧，
        而现象是「出口配了但没接上」。所以这里只做一次转交，
        并且把出口给的东西原样带下去（``verify_egress_binding`` 已经判过
        那一份里带着回环绕过护栏）。

        Lane G 再加一条：出口**不能**用同名参数把 WebRTC/DNS/代理护栏降级。
        这一判在 ``execute`` 的派发前发生，所以不合格时整个操作是 ``BLOCKED``，
        而不是逐页 ``FAILED`` —— 一次浏览器都不该起来。
        """
        kwargs = dict(egress.browser_config_kwargs())
        violations = browser_launch_violations(kwargs)
        if violations:
            raise _egress_error(
                LOCAL_CRAWL_NOT_SAFE_TO_RUN,
                detail="浏览器启动配置不合格：" + ", ".join(violations),
            )
        return kwargs

    def _require_sdk(self) -> Any:
        if not self._sdk_available():
            raise UpstreamRefusal("CRAWL4AI_LOCAL_NOT_INSTALLED",
                                  "本机没有装 crawl4ai（requirements-crawl-local.txt）。")
        return self._module_loader()

    def _budget(self, params: Mapping[str, Any]) -> CrawlResourceBudget:
        raw = params.get("limits")
        if not isinstance(raw, Mapping):
            # 缺上限就是拒绝，不是给一个「合理缺省」。
            raise CrawlRefusalError(
                CrawlRefusal.UNBOUNDED_CRAWL_REFUSED,
                "缺少 limits。T1 的每一格上限都必须显式给出 —— "
                "「不填」在这里的失败方式与「有界」同形。",
            )
        unknown = {str(key) for key in raw} - set(_LIMIT_KEYS)
        if unknown:
            raise CrawlRefusalError(CrawlRefusal.UNBOUNDED_CRAWL_REFUSED,
                                    f"未知的 limits 键：{sorted(unknown)}")
        missing = [key for key in _LIMIT_KEYS if key not in raw]
        if missing:
            raise CrawlRefusalError(
                CrawlRefusal.UNBOUNDED_CRAWL_REFUSED,
                f"limits 缺 {missing}。上限是**整组**要给的：只给一半时，另一半"
                "会落回缺省，而缺省在报告上看不出来。",
            )
        return CrawlResourceBudget(**{key: raw[key] for key in _LIMIT_KEYS})

    def _extract_spec(self, params: Mapping[str, Any], *,
                      default_mode: ExtractionMode) -> ExtractionSpec:
        raw = params.get("extraction")
        spec = parse_extraction_spec(raw)
        if raw is None:
            spec = ExtractionSpec(mode=default_mode)
        return spec

    def _artifact_policy(self, params: Mapping[str, Any]) -> ArtifactPolicy:
        return parse_artifact_policy(params.get("artifacts"))

    def _session_policy(self, params: Mapping[str, Any]) -> SessionPolicy:
        return parse_session_policy(params.get("session"))

    # -- 纯本地抽取（T0 计算，不联网）--------------------------------------
    def _execute_local_extract(self, capability_id: str, run_id: str, operation_id: str,
                               params: Mapping[str, Any]) -> CrawlOutcome:
        spec = self._extract_spec(params, default_mode=ExtractionMode.STRUCTURED)
        html = params.get("html")
        if not isinstance(html, str):
            raise CrawlRefusalError(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                                    "html 必须是字符串")
        started = self._clock()
        result = apply_extraction(spec, html=html)
        elapsed = self._clock() - started
        manifest = EvidenceManifest(run_id=run_id)
        manifest.add(ManifestRow(
            run_id=run_id, operation_id=operation_id, provider=PROVIDER,
            capability_id=capability_id, url=None,
            retrieved_at=datetime.now(timezone.utc),
            content_sha256=content_sha256(html),
            extraction_method=f"local:{spec.mode.value}",
            outcome="OK",
            caveats=("输入是调用方给的字节，不是本 adapter 取回的 —— "
                     "来源账上它没有 egress 决定。",),
        ))
        payload = {
            "mode": result.mode,
            "fields": _clean(dict(result.fields)),
            "text": result.text,
            "document_truncated": result.truncated,
            "local_compute_seconds": round(elapsed, 6),
            # 纯本地抽取也产一行证据：下游要能回答「这份 payload 是从哪份字节来的」，
            # 而那一行里的 ``egress_decision_ref`` 是 ``None`` —— 「这一次没有出口
            # 决定」本身是一个要读的读数，不是缺失。
            "manifest": [row.as_row() for row in manifest.rows],
        }
        return CrawlOutcome(outcome="OK", payload=payload, transport=TRANSPORT,
                            schema_verdict="UNVERIFIED",
                            quota_units_used=Decimal(0),
                            caveats=("纯本地抽取：无网络、无厂商计价、无 ledger 行。",))

    # -- 单页抓取 ---------------------------------------------------------
    def _execute_single(self, capability_id: str, run_id: str, operation_id: str,
                        params: Mapping[str, Any]) -> CrawlOutcome:
        egress = self._require_egress()
        budget = self._budget(params)
        meter = BudgetMeter(budget=budget)
        session = self._session_policy(params)
        artifacts = self._artifact_policy(params)
        spec = self._extract_spec(params, default_mode=ExtractionMode.MARKDOWN)

        try:
            return self._execute_single_metered(capability_id, run_id, operation_id,
                                                params, egress, budget, meter, session,
                                                artifacts, spec)
        except (CrawlRefusalError, UpstreamRefusal) as exc:
            # **一次失败也烧掉了配额。** 走到这里说明计量表已经建起来了；
            # 把 meter 带进失败结果，「这一页花掉了一个浏览器秒」才留在账上。
            # 不带的后果是 `quota_units_used: null` —— 而那与「什么都没消耗」
            # 在报告上同形，只有对账的人才会发现它不对。
            return self._route_refusal(exc.code, exc.detail, capability_id, meter=meter)

    def _execute_single_metered(self, capability_id: str, run_id: str, operation_id: str,
                                params: Mapping[str, Any], egress: EgressBinding,
                                budget: CrawlResourceBudget, meter: BudgetMeter,
                                session: SessionPolicy, artifacts: ArtifactPolicy,
                                spec: ExtractionSpec) -> CrawlOutcome:
        url = str(params.get("url") or "").strip()
        if not url:
            raise CrawlRefusalError(CrawlRefusal.EXTRACTION_SPEC_INVALID, "缺 url")
        _managed_browser_url(url)
        decision = egress.check(url)
        if not decision.allowed:
            raise _egress_error("EGRESS_REFUSED",
                                detail=f"{decision.refusal} {decision.detail}".strip())

        browser_kwargs = self._require_egress_kwargs(egress)

        if capability_id in {"crawl4ai.screenshot", "crawl4ai.pdf"}:
            return self._execute_artifact(capability_id, run_id, operation_id, params,
                                          meter, artifacts, egress, browser_kwargs)
        if capability_id == "crawl4ai.fetch_page":
            import research_walls
            if research_walls.is_data_url(url):
                return self._execute_data(capability_id, run_id, operation_id, url, egress,
                                          budget, meter, artifacts, decision)

        module = self._require_sdk()
        started = self._clock()
        outcome = self._run_sdk_once(module, url=url,
                                     egress_kwargs=browser_kwargs,
                                     page_timeout_ms=int(budget.max_seconds * 1000))
        elapsed = self._clock() - started
        meter.consume_seconds(elapsed)
        meter.consume_browser_seconds(elapsed)
        meter.consume_page(url, domain=_domain(url))

        html = str(outcome.get("html") or "")
        headers = outcome.get("headers") or {}
        marker = _challenge_markers(headers if isinstance(headers, dict) else {}, html)
        if marker:
            raise _egress_error("SOURCE_CHALLENGE_PAGE", detail=marker)
        self._refuse_unusable_page(outcome, url, html, headers)

        meter.consume_bytes(len(html))
        primary_ids = ()
        if self._material_writer is not None:
            # These are the actual validated browser bytes, before extraction.
            # The composition-owned store returns Run-owned artifact IDs.
            primary_ids = tuple(self._material_writer(run_id, operation_id, url,
                                                       html.encode("utf-8")))
        extraction = apply_extraction(spec, html=html, url=url)
        manifest = EvidenceManifest(run_id=run_id)
        artifact_refs: tuple[ArtifactRef, ...] = ()
        manifest.add(ManifestRow(
            run_id=run_id, operation_id=operation_id, provider=PROVIDER,
            capability_id=capability_id, url=url,
            retrieved_at=datetime.now(timezone.utc),
            content_sha256=content_sha256(html),
            extraction_method=f"local_sdk:{spec.mode.value}",
            outcome="OK",
            artifact_refs=tuple(primary_ids) + tuple(item.ref for item in artifact_refs),
            egress_decision_ref=f"{decision.method} {decision.target}:{decision.port}",
            caveats=(f"session ttl={session.ttl_seconds}s；"
                     "persist_state 恒为 False。",),
        ))
        payload = {
            "url": url,
            **_attribution(outcome, url),
            "mode": extraction.mode,
            "fields": _clean(dict(extraction.fields)),
            "text": extraction.text[:spec.max_chars],
            "document_truncated": extraction.truncated,
            "meter": meter.snapshot(),
            "manifest": [row.as_row() for row in manifest.rows],
        }
        return CrawlOutcome(
            outcome="OK", payload=_clean(payload), transport=TRANSPORT,
            artifacts=primary_ids,
            schema_verdict="UNVERIFIED",
            quota_units_used=Decimal(meter.pages),
            caveats=("本地 SDK 无厂商计价：配额单位是 pages/bytes/seconds，"
                     "**不是** USD，也不折算。",),
        )

    def _execute_data(self, capability_id: str, run_id: str, operation_id: str, url: str,
                      egress: EgressBinding, budget: CrawlResourceBudget, meter: BudgetMeter,
                      artifacts: ArtifactPolicy, decision: Any) -> CrawlOutcome:
        """A data endpoint (JSON/CSV/API) is read as plain HTTP through the same egress.

        A browser turns an export (Content-Disposition: attachment) into a download
        and the page read fails: Opendatasoft exports failed this way on every portal
        tried, so 12,772 rows were paged 100 at a time instead. The raw bytes are what
        the bulk importer needs anyway.
        """
        started = self._clock()
        response = auxiliary_http_get(
            egress, url, timeout_s=max(15.0, float(budget.max_seconds)),
            max_bytes=int(artifacts.max_bytes_per_artifact),
            accept="application/json,text/csv,application/geo+json,application/xml;q=0.9,*/*;q=0.5")
        elapsed = self._clock() - started
        meter.consume_seconds(elapsed)
        meter.consume_page(url, domain=_domain(url))
        meter.consume_bytes(response.bytes_read)
        if not response.body.strip():
            raise _egress_error("SOURCE_EMPTY_EXTRACT", detail="empty data response")
        primary_ids = ()
        if self._material_writer is not None:
            primary_ids = tuple(self._material_writer(run_id, operation_id, url,
                                                       response.body.encode("utf-8")))
        manifest = EvidenceManifest(run_id=run_id)
        manifest.add(ManifestRow(
            run_id=run_id, operation_id=operation_id, provider=PROVIDER,
            capability_id=capability_id, url=url,
            retrieved_at=datetime.now(timezone.utc),
            content_sha256=content_sha256(response.body),
            extraction_method="local_http:data",
            outcome="OK", artifact_refs=tuple(primary_ids),
            egress_decision_ref=f"{decision.method} {decision.target}:{decision.port}",
            caveats=("Data endpoint read without a browser.",),
        ))
        payload = {"url": url, "final_url": response.url, "mode": "raw_data",
                   "content_type": response.headers.get("Content-Type", ""),
                   "bytes": response.bytes_read, "text": response.body[:2000],
                   "document_truncated": response.bytes_read > 2000,
                   "meter": meter.snapshot(),
                   "manifest": [row.as_row() for row in manifest.rows]}
        return CrawlOutcome(
            outcome="OK", payload=_clean(payload), transport=TRANSPORT,
            artifacts=primary_ids, schema_verdict="UNVERIFIED",
            quota_units_used=Decimal(meter.pages),
            caveats=("Local HTTP read: no vendor charge.",))

    def _execute_artifact(self, capability_id: str, run_id: str, operation_id: str,
                          params: Mapping[str, Any], meter: BudgetMeter,
                          artifacts: ArtifactPolicy, egress: EgressBinding,
                          browser_kwargs: Mapping[str, object]) -> CrawlOutcome:
        want = "screenshot" if capability_id == "crawl4ai.screenshot" else "pdf"
        if want not in artifacts.enabled():
            # 默认关闭，且**逐次**开启。默认开着的截图/PDF 在一次「只要正文」的
            # 抓取里是纯浪费，而 T1 的成本是我们自己的机器。
            raise CrawlRefusalError(
                CrawlRefusal.ARTIFACT_CAP_EXCEEDED,
                f"{want} 没有被显式开启（artifacts.{want}=False）。",
            )
        module = self._require_sdk()
        url = str(params.get("url") or "").strip()
        # 这一次渲染**之前**出口上已有多少条判决 —— 后面用差集说清「这一次渲染期间
        # 出口拒绝了哪些目标」。**不**能用总数：同一个 session 里前面那一跳的判决
        # 会被算进来，而报告上它看起来像这一次的。
        decisions_before = len(egress.decisions())
        started = self._clock()
        taken = self._run_sdk_artifact(
            module, url=url, kind=want, egress_kwargs=browser_kwargs,
            max_bytes=artifacts.max_bytes_per_artifact)
        elapsed = self._clock() - started
        meter.consume_seconds(elapsed)
        meter.consume_browser_seconds(elapsed)
        meter.consume_page(url, domain=_domain(url))

        # **同一份页面来源判据**（不是第二份）。产物那一路早先没有它，于是
        # 一次「浏览器被带到出口拒绝页」的抓取会产出一份**装着出口拒绝说明的
        # PDF**并被记成 OK —— 那是「被拒的结果不得落成证据」那条规则的漏洞。
        self._refuse_unusable_page(
            taken, url, str(taken.get("html") or ""),
            taken.get("headers") if isinstance(taken.get("headers"), Mapping) else {})

        render_decisions = tuple(egress.decisions())[decisions_before:]
        render_refusals = tuple(d for d in render_decisions if not d.allowed)
        payload_artifacts = taken["bytes"]
        meter.consume_artifact()
        meter.consume_bytes(len(payload_artifacts))
        digest = content_sha256(payload_artifacts)
        persisted = self._persist_artifact(payload_artifacts, kind=want, digest=digest)
        ref = ArtifactRef(ref=persisted or f"{capability_id}:{operation_id}", kind=want,
                          sha256=digest)
        manifest = EvidenceManifest(run_id=run_id)
        manifest.add(ManifestRow(
            run_id=run_id, operation_id=operation_id, provider=PROVIDER,
            capability_id=capability_id, url=url,
            retrieved_at=datetime.now(timezone.utc),
            content_sha256=ref.sha256, extraction_method=f"local_sdk:{want}",
            outcome="OK", artifact_refs=(ref.ref,),
            caveats=(f"单件上限 {artifacts.max_bytes_per_artifact} 字节；"
                     "产物内容不进入 payload，只给引用与 hash。",),
        ))
        return CrawlOutcome(
            outcome="OK",
            payload={
                "url": url,
                **_attribution(taken, url),
                "artifact": ref.as_row(),
                "artifact_persisted": persisted is not None,
                "egress_decision_ref": (
                    f"{taken.get('status_code')}:{len(render_decisions)}"
                    f"/{len(render_refusals)}"),
                "egress_decisions": len(render_decisions),
                "egress_refusals": [
                    {"target": d.target, "refusal": d.refusal} for d in render_refusals
                ],
                "meter": meter.snapshot(),
                "manifest": [row.as_row() for row in manifest.rows],
            },
            artifacts=(ref,), transport=TRANSPORT, schema_verdict="UNVERIFIED",
            quota_units_used=Decimal(meter.artifacts),
            caveats=(
                "产物只以引用 + hash 的形式返回；内容本身不经过本层。",
                *((f"本次渲染期间出口拒绝了 {len(render_refusals)} 个目标；"
                   "产物是浏览器**看到的那一页**，因此可能包含浏览器对这些拒绝应答的渲染。"
                   "那些字不是来源材料。",) if render_refusals else ()),
            ),
        )

    # -- sitemap seeder ---------------------------------------------------
    def _execute_sitemap_seed(self, capability_id: str, run_id: str, operation_id: str,
                              params: Mapping[str, Any]) -> CrawlOutcome:
        egress = self._require_egress()
        budget = self._budget(params)
        meter = BudgetMeter(budget=budget)
        allowed = features.normalise_allowed_domains(params.get("allowed_domains") or ())
        sitemap_urls = params.get("sitemap_urls")
        if not isinstance(sitemap_urls, (list, tuple)) or not sitemap_urls:
            raise CrawlRefusalError(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                                    "sitemap_urls 必须是非空列表")

        seeds: list[str] = []
        fetched: list[dict[str, Any]] = []
        for raw_url in sitemap_urls:
            url = str(raw_url).strip()
            # 取回 sitemap/robots 本身是**辅助 HTTP**，所以它经出口（IA-4）。
            response = auxiliary_http_get(
                egress, url,
                timeout_s=min(15.0, budget.max_seconds),
                max_bytes=min(budget.max_bytes, 2 * 1024 * 1024),
            )
            meter.consume_bytes(response.bytes_read)
            meter.consume_page(url, domain=_domain(url))
            meter.consume_seconds(0.0)
            found = parse_sitemap(response.body, max_urls=budget.max_pages)
            fetched.append({"url": url, "bytes": response.bytes_read,
                            "urls_found": len(found),
                            "sha256": content_sha256(response.body)})
            seeds.extend(found)

        # **先判域名，再问 robots。** 顺序是承重的：一个域外的种子不该让我们跑到
        # 那个域上去取一份 robots.txt —— 那既是一次没必要的网络动作，也会把它
        # 被拒的理由写成「robots 读不到」而不是「这个域不在批准清单里」。
        # 本机实测过反过来的版本：sitemap 里那条 outside.invalid 触发了第二次
        # robots 取回，`refused` 里于是出现 `EGRESS_REFUSED` 而不是
        # `OFF_DOMAIN_SEED_REFUSED` —— 报告上指向了错误的排查方向。
        off_domain = [seed for seed in seeds
                      if not features.domain_permitted(seed, allowed)]
        in_domain = [seed for seed in seeds if features.domain_permitted(seed, allowed)]

        # robots：**缺省不许**。取不到 robots 时不假装站点允许
        # （``robots_allowed`` 对空串返回 False），所以只在能证明允许时才保留种子。
        #
        # 一个 origin 一次取回 —— 逐种子取会让配额与请求数随种子数放大，
        # 而 robots 是**站点级**的一份文件，不是每页一份。
        robots_cache: dict[str, Any] = {}
        robots_log: list[dict[str, Any]] = []
        permitted: list[str] = []
        robots_refused: list[dict[str, str]] = []
        for seed in in_domain:
            parts = urllib.parse.urlsplit(seed)
            origin = f"{parts.scheme}://{parts.netloc}"
            if origin not in robots_cache:
                robots_url = origin + "/robots.txt"
                try:
                    response = auxiliary_http_get(
                        egress, robots_url,
                        timeout_s=min(15.0, budget.max_seconds),
                        max_bytes=min(budget.max_bytes, 256 * 1024),
                    )
                except (CrawlRefusalError, UpstreamRefusal) as exc:
                    # 取不到 robots ⇒ 证不出允许 ⇒ 这个 origin 的种子全部拒。
                    # **不**静默放过：那会把一次取回失败读成站点许可。
                    robots_cache[origin] = {"allowed": None, "code": exc.code}
                else:
                    meter.consume_bytes(response.bytes_read)
                    meter.consume_page(robots_url, domain=_domain(robots_url))
                    robots_cache[origin] = {"allowed": response.body,
                                            "bytes": response.bytes_read,
                                            "sha256": content_sha256(response.body),
                                            "code": ""}
                robots_log.append({"origin": origin, **{
                    k: v for k, v in robots_cache[origin].items() if k != "allowed"}})
            cached = robots_cache[origin]
            if cached["allowed"] is None:
                robots_refused.append({"url": seed, "reason": cached["code"]})
                continue
            if features.robots_allowed(cached["allowed"], path=parts.path or "/"):
                permitted.append(seed)
            else:
                robots_refused.append({"url": seed,
                                       "reason": _reason("ROBOTS_DISALLOWED")})

        plan = plan_deep_crawl(permitted, budget=budget, allowed_domains=allowed)
        manifest = EvidenceManifest(run_id=run_id)
        for item in fetched:
            manifest.add(ManifestRow(
                run_id=run_id, operation_id=operation_id, provider=PROVIDER,
                capability_id=capability_id, url=item["url"],
                retrieved_at=datetime.now(timezone.utc),
                content_sha256=item["sha256"],
                extraction_method="local_sdk:sitemap",
                outcome="OK", caveats=("sitemap 的取回经出口；它是一个普通页面。",),
            ))
        return CrawlOutcome(
            outcome="OK",
            payload=_clean({
                "sitemaps": fetched,
                "seeds": list(plan.queue),
                "refused": ([{"url": url,
                              "reason": _reason(CrawlRefusal.OFF_DOMAIN_SEED_REFUSED.value)}
                             for url in off_domain]
                            + robots_refused
                            + [{"url": url, "reason": reason}
                               for url, reason in plan.refused]),
                "robots": robots_log,
                "stop_reason": plan.stop_reason,
                "domains": list(plan.domains),
                "meter": meter.snapshot(),
                "manifest": [row.as_row() for row in manifest.rows],
            }),
            transport=TRANSPORT, schema_verdict="UNVERIFIED",
            quota_units_used=Decimal(meter.pages),
            caveats=("种子只作为计划：本能力**不**抓取种子页，抓取由 deep_crawl 做。",),
        )

    # -- 深爬 -------------------------------------------------------------
    def _execute_deep_crawl(self, capability_id: str, run_id: str, operation_id: str,
                            params: Mapping[str, Any]) -> CrawlOutcome:
        egress = self._require_egress()
        budget = self._budget(params)
        meter = BudgetMeter(budget=budget)
        allowed = features.normalise_allowed_domains(params.get("allowed_domains") or ())
        seeds = [str(item) for item in (params.get("seeds") or ())]
        if not seeds:
            raise CrawlRefusalError(CrawlRefusal.EXTRACTION_SPEC_INVALID,
                                    "seeds 必须是非空列表")
        spec = self._extract_spec(params, default_mode=ExtractionMode.MARKDOWN)
        module = self._require_sdk()
        browser_kwargs = self._require_egress_kwargs(egress)
        manifest = EvidenceManifest(run_id=run_id)
        rows: list[dict[str, Any]] = []
        refused: list[dict[str, str]] = []

        # 边抓边扩的 BFS。**为什么不是先用 plan_deep_crawl 规划好再抓**：
        # 那个函数需要一个 link_extractor，而链接只有在**页被取回之后**才存在 ——
        # 先规划后抓会让 link_extractor 永远返回空（没有一行被取回过），
        # 于是一个「深爬」退化成一个「只抓种子」而报告上完全看不出来。
        # plan_deep_crawl 仍然用于 sitemap seeder 那条**静态种子**的路径。
        from collections import deque

        frontier: deque[tuple[str, int]] = deque()
        seen: set[str] = set()
        stop_reason = "complete"
        for seed in seeds:
            url = seed.strip()
            if not url:
                continue
            if not features.domain_permitted(url, allowed):
                refused.append({"url": url,
                                "reason": _reason(CrawlRefusal.OFF_DOMAIN_SEED_REFUSED.value)})
                continue
            if url not in seen:
                seen.add(url)
                frontier.append((url, 0))

        while frontier:
            # **上限在进入抓取之前判**：触顶就停，不是抓完再截断。
            if meter.remaining_pages() <= 0:
                stop_reason = "page_cap_reached"
                break
            if meter.seconds >= budget.max_seconds:
                stop_reason = "time_cap_reached"
                break
            url, depth = frontier.popleft()
            try:
                _managed_browser_url(url)
            except UpstreamRefusal as exc:
                rows.append({"url": url, "outcome": "BLOCKED",
                             "reason": _reason(exc.code), "detail": exc.detail})
                continue
            decision = egress.check(url)
            if not decision.allowed:
                rows.append({"url": url, "outcome": "BLOCKED",
                             "reason": _reason("EGRESS_REFUSED"),
                             "detail": f"{decision.refusal} {decision.detail}".strip()})
                continue
            started = self._clock()
            try:
                page = self._run_sdk_once(module, url=url,
                                          egress_kwargs=browser_kwargs,
                                          page_timeout_ms=int(budget.max_seconds * 1000))
            except (CrawlRefusalError, UpstreamRefusal) as exc:
                rows.append({"url": url, "outcome": "FAILED", "reason": exc.code})
                continue
            elapsed = self._clock() - started
            meter.consume_seconds(elapsed)
            meter.consume_browser_seconds(elapsed)
            meter.consume_page(url, domain=features.domain_of(url))
            html = str(page.get("html") or "")
            try:
                # **同一份判据，不是第二份。** 一次深爬里的一页与一次单页抓取
                # 落在同一批拒绝条件上；两处各写一份的那一天，改一处的那一处不会红。
                self._refuse_unusable_page(page, url, html, page.get("headers") or {})
            except (CrawlRefusalError, UpstreamRefusal) as exc:
                rows.append({"url": url, "outcome": "FAILED", "reason": exc.code})
                continue
            meter.consume_bytes(len(html))
            extraction = apply_extraction(spec, html=html, url=url)
            rows.append({
                "url": url, "outcome": "OK",
                "sha256": content_sha256(html),
                "text": extraction.text[:spec.max_chars],
                "fields": _clean(dict(extraction.fields)),
            })
            manifest.add(ManifestRow(
                run_id=run_id, operation_id=operation_id, provider=PROVIDER,
                capability_id=capability_id, url=url,
                retrieved_at=datetime.now(timezone.utc),
                content_sha256=content_sha256(html),
                extraction_method=f"local_sdk:{spec.mode.value}", outcome="OK",
                egress_decision_ref=f"{decision.method} {decision.target}:{decision.port}",
            ))
            if depth >= budget.max_depth:
                continue
            for link in _links(html):
                # 相对链接先补成绝对 —— 不补的话每一个 ``/a`` 都会因为
                # 「没有 hostname」被判成外链，于是一次站内深爬会在第一层就停。
                target = urllib.parse.urljoin(url, link)
                target = target.split("#", 1)[0]
                if not target or target in seen:
                    continue
                if not target.startswith(("http://", "https://")):
                    continue
                if not features.domain_permitted(target, allowed):
                    refused.append({"url": target,
                                    "reason": _reason(
                                        CrawlRefusal.OFF_DOMAIN_SEED_REFUSED.value)})
                    continue
                if len(seen) >= budget.max_pages:
                    refused.append({"url": target,
                                    "reason": _reason(
                                        CrawlRefusal.CRAWL_PAGE_CAP_EXCEEDED.value)})
                    stop_reason = "page_cap_reached"
                    continue
                if len({features.domain_of(item) for item in seen | {target}}) > budget.max_domains:
                    refused.append({"url": target,
                                    "reason": _reason(
                                        CrawlRefusal.CRAWL_DOMAIN_CAP_EXCEEDED.value)})
                    continue
                seen.add(target)
                frontier.append((target, depth + 1))

        return CrawlOutcome(
            outcome="OK",
            payload=_clean({
                "pages": rows,
                "refused_at_plan": refused,
                "stop_reason": stop_reason,
                "meter": meter.snapshot(),
                "manifest": [row.as_row() for row in manifest.rows],
            }),
            transport=TRANSPORT, schema_verdict="UNVERIFIED",
            quota_units_used=Decimal(meter.pages),
            caveats=("深爬的每一页都单独记账；超限之前停，超限之后的那一页不被抓。",
                     "本地 SDK 无厂商计价：配额单位不是 USD。"),
        )

    # -- 真库调用：**每一个出口都只有一次** --------------------------------
    def _crawler_options(self, module: Any) -> dict[str, Any]:
        extra = ({"base_directory": self._cache_base_directory}
                 if self._cache_base_directory is not None else {})
        factory = getattr(module, "AsyncLogger", None)
        if callable(factory):
            from rich.console import Console

            class ProgressSink:
                def write(self, text):
                    return len(text)

                def flush(self):
                    pass

            # The SDK can force warnings even with verbose=False. Keep its
            # formatted file log, but give its console a private discard sink;
            # never redirect process-global stdout/stderr in parallel workers.
            base = self._cache_base_directory or os.environ.get("CRAWL4_AI_BASE_DIRECTORY") or str(Path.home())
            extra["logger"] = factory(log_file=str(Path(base) / ".crawl4ai" / "crawler.log"),
                                      verbose=False, console=Console(file=ProgressSink(), force_terminal=False))
        return extra

    def _run_sdk_once(self, module: Any, *, url: str, egress_kwargs: Mapping[str, object],
                      page_timeout_ms: int) -> dict[str, Any]:
        """起一次浏览器、抓一页。**没有重试循环。**

        IA-3：派发后的失败不得触发 adapter 内部重试。所以这里是一个直筒：
        构造 → ``arun`` → 读结果。库自己的 ``max_retries`` 被
        :func:`_build_run_config` 钉成 0。
        """
        import asyncio

        browser_config = _build_browser_config(
            module, egress_kwargs=egress_kwargs, user_agent=self._user_agent)
        run_config = _build_run_config(module, page_timeout_ms=page_timeout_ms)

        async def run() -> dict[str, Any]:
            extra = self._crawler_options(module)
            async with module.AsyncWebCrawler(config=browser_config, **extra) as crawler:
                result = await crawler.arun(url=url, config=run_config)
                return {
                    "html": getattr(result, "html", "") or "",
                    "success": bool(getattr(result, "success", False)),
                    "error_message": str(getattr(result, "error_message", "") or ""),
                    "redirected_url": (getattr(result, "redirected_url", None) or None),
                    "status_code": getattr(result, "status_code", None),
                    "headers": getattr(result, "response_headers", None) or {},
                    "url": getattr(result, "url", url),
                }

        try:
            return asyncio.run(run())
        except (CrawlRefusalError, UpstreamRefusal):
            raise
        except Exception as exc:  # noqa: BLE001 - 理由码化，不泄漏异常文本
            message = str(exc).casefold()
            if "executable doesn't exist" in message or "browser executable" in message:
                raise _egress_error("LOCAL_BROWSER_NOT_INSTALLED") from None
            raise _egress_error("LOCAL_CRAWL_RUNTIME_FAILED",
                                detail=runtime_failure_detail(exc)) from None

    def _run_sdk_artifact(self, module: Any, *, url: str, kind: str,
                          egress_kwargs: Mapping[str, object],
                          max_bytes: int) -> dict[str, Any]:
        """截图 / PDF。**有界**，而且**空产物是一句拒绝，不是一个成功的空件**。

        ## 返回的是**整次取回的读数**，不是光秃秃的字节

        早先这一格只回 ``bytes``，于是它**绕过了页面来源判据** ——
        :meth:`_refuse_unusable_page` 装在那条正文取回的路上，而这一条不在。
        本机实测（真 Chromium + A 的真出口）的量出来的后果：

        * ``crawl4ai.pdf`` 打在一个 **302 到诱饵**的页上 ⇒ ``outcome: OK``、
          1 件产物、38,573 字节，而**用 ``pypdf`` 抽出来的正文就是出口自己的拒绝说明**
          （"'decoy.example' 属于保留名字空间（…），公网 DNS 里不会有它"）；
        * ``<meta http-equiv=refresh>`` 那一路同样；
        * 一个真 403 的页面 ⇒ 也是 ``OK`` + 一份**空白** PDF。

        所以现在这一格把 :meth:`_run_sdk_once` 的那六格**原样带回来**，
        由调用方过**同一份**判据；产物只是其中一格。

        ## 两个实测出来的坑（crawl4ai 0.9.3，本机真跑）

        1. **产物必须在 run config 上请求。** 只读 ``result.screenshot`` 而不在
           ``CrawlerRunConfig(screenshot=True)`` 里要它，得到的永远是空 —— 一个
           「取到了 0 字节的截图」在收据上与「取到了一张截图」都叫 OK，
           而两者差着一个真实的读数。
        2. **截图回来的是 base64 字符串，不是 bytes。** ``isinstance(x, bytes)``
           在这里恒假，于是上面那个坑会被**静默**地再踩一次：分支走 else、
           返回 ``b""``、然后一切看起来正常。
        """
        import asyncio

        browser_config = _build_browser_config(
            module, egress_kwargs=egress_kwargs, user_agent=self._user_agent)
        run_config = _build_run_config(module, page_timeout_ms=60_000,
                                       artifact=kind)

        async def run() -> dict[str, Any]:
            extra = self._crawler_options(module)
            async with module.AsyncWebCrawler(config=browser_config, **extra) as crawler:
                result = await crawler.arun(url=url, config=run_config)
                return {
                    "html": getattr(result, "html", "") or "",
                    "success": bool(getattr(result, "success", False)),
                    "status_code": getattr(result, "status_code", None),
                    "headers": getattr(result, "response_headers", None) or {},
                    "error_message": str(getattr(result, "error_message", "") or ""),
                    "redirected_url": (getattr(result, "redirected_url", None) or None),
                    "url": getattr(result, "url", url) or url,
                    "artifact": getattr(result, kind, None),
                }

        try:
            outcome = asyncio.run(run())
        except (CrawlRefusalError, UpstreamRefusal):
            raise
        except Exception as exc:  # noqa: BLE001
            raise _egress_error("LOCAL_CRAWL_RUNTIME_FAILED",
                                detail=runtime_failure_detail(exc)) from None

        raw = outcome.get("artifact")
        payload = _artifact_bytes(raw, kind)
        if not payload:
            # **不产出一个空的「成功」**：一份 0 字节的证据落盘之后，
            # 它在下游与一份真的截图长得一模一样，只是没人会去核对它的大小。
            raise _egress_error(
                "ARTIFACT_EMPTY_FROM_SDK",
                detail=f"{kind}: SDK 返回了 {type(raw).__name__}，解出来是 0 字节",
            )
        if len(payload) > max_bytes:
            raise CrawlRefusalError(
                CrawlRefusal.ARTIFACT_CAP_EXCEEDED,
                f"{kind} 产物 {len(payload)} 字节超过单件上限 {max_bytes}。",
            )
        return {**outcome, "bytes": payload}


def _artifact_bytes(raw: object, kind: str) -> bytes:
    """把 SDK 给的产物变成字节。**两种形状都认，其余一律当作没有。**

    实测（crawl4ai 0.9.3）：``screenshot`` 是 base64 **字符串**；
    ``pdf`` 的形状随版本变。所以这里不猜：``bytes`` 原样收，``str`` 按 base64 解
    （解不开就当没有），别的一律当没有 —— 一个「长得不像产物」的返回值不做第二次尝试。
    """
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str) and raw:
        import base64
        import binascii

        try:
            return base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            return b""
    return b""


def _domain(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


_LINK = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')""",
                   re.IGNORECASE)


def _links(html: str) -> tuple[str, ...]:
    """从 HTML 里抽 ``<a href>``。**不做解析**：深爬的链接抽取必须便宜，
    而一棵完整 DOM 在这里是浪费；正文抽取那一侧才需要 DOM。
    """
    found: list[str] = []
    for match in _LINK.finditer(html):
        href = (match.group(1) or match.group(2) or "").strip()
        if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
            found.append(href)
    return tuple(found)


def build_manifest_jsonl(manifest: EvidenceManifest) -> str:
    """给集成/测试用的薄封装：清单导出前必须已绑定 ledger。"""
    return manifest.to_jsonl()


# ---------------------------------------------------------------------------
# 一个**绑定了出口**的最小实现，供测试与本地演示
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LoopbackEgressBinding:
    """结构上合格、**行为上从不放行**的出口绑定。

    它存在的理由只有一个：让「绑定合格但出口拒绝」与「绑定不合格」成为两个
    可区分的读数。**它不是一个可用的出口** —— 真出口在 W-A。
    """

    implementation: str = "marketops.adapters.egress_proxy.EgressProxy"
    contract_sha256: str = "0" * 64
    proxy_url: str = ""
    recorded: tuple[EgressDecisionView, ...] = ()

    def decisions(self) -> tuple[EgressDecisionView, ...]:
        return self.recorded

    def check(self, url: str) -> EgressDecisionView:
        return EgressDecisionView(method="GET", target=url, port=443, allowed=False,
                                  refusal="not_implemented_in_wave1_plugin",
                                  detail="本次未绑定真出口。")

    def browser_config_kwargs(self) -> dict[str, object]:
        """**空字典**，理由与 :meth:`_UnboundEgress.browser_config_kwargs` 同：
        一个「够用」的配置会用库的缺省值把浏览器拉起来，而缺省值是没有代理。"""
        return {}


def _sorted_capabilities() -> tuple[str, ...]:
    return tuple(sorted(CAPABILITIES))


def _json_default(value: Any) -> str:
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def render_outcome_json(outcome: CrawlOutcome) -> str:
    """一条可核对的收据行。**脱敏之后**才序列化（IA-1）。"""
    return json.dumps(to_adapter_result_fields(outcome), ensure_ascii=False,
                      sort_keys=True, default=_json_default)

"""§10 的统一抓取入口：``fetch_document(request, mode=auto/http/browser)``。

## 它取代了什么

在这之前，「HTTP 成功就不要启动浏览器」这条规则写在
``application/pipeline.py::_process_url`` 里。那意味着**每一个新调用方**都要把
同一条规则再写一遍 —— 服务面的 ``fetch_document`` 工具、``crawl_site``
（``application/site_crawl.py``：它的每一页都经由这里，包括升级判定）、每一个离线
脚本。抄第二遍的时候没人会发现，抄错的时候只有一个调用方会错，
而那个错法长得完全正常：多开了一次浏览器（慢、贵），或者少开了一次
（拿回一个空壳，然后在报告里被记成「这个页面没有邮箱」）。

所以编排收敛到这里，pipeline 与服务面拿到的是**同一个** ``fetch_document``。
判据 ``tests/test_acquisition_service.py`` 对着本类直接测。

## 为什么它不是 ``ports/acquisition.py`` 里第四个 ABC

那条纪律写在 ``ports/acquisition.py`` 的文件头：**等第二个实现出现再提取**。
今天这件事只有一种正确做法，写一个永远只有一个子类的 ABC 不增加可替换性。
判据是「有没有第二个实现」，不是「这个类看起来像不像基础设施」。

## 为什么 ``browser_seconds`` 在这里测

报告里的浏览器秒数一直是**这一次调用占用的墙钟时间**（`time.perf_counter()`
夹住整次 ``browser.fetch``，含 ``asyncio.run`` 的起停），而 ``FetchResult
.elapsed_ms`` 是 fetcher 在自己的计时器内测到的渲染时间。两者不是同一个量，
差得也不多 —— 但把它换成后者而不说明，等于把一条历史指标的含义悄悄改掉，
而指标的唯一用途就是跨运行比较。

## 失败是一个结果

本模块不抛「抓不动」的异常。浏览器没接线、接线了但依赖没装、目标被安全判据
拒绝 —— 三种都返回 ``FetchResult``，理由写在 ``reason`` / ``error`` 上。
这正是 ``FetchResult`` 存在的意义（见其 docstring），而在编排层抛异常会让
「结果可分类」这件事在最需要它的那一层失效。

**安全判据不在这里重新实现。** ``check_target`` 由每个 fetcher 在发请求之前
调用，本层只负责选层级。在这里再判一次就是一份判据两个实现，而漂移的那一份
会表现为「编排层说可以、fetcher 说不可以」，或者更糟的反过来。
"""

from __future__ import annotations

import time
from dataclasses import replace

from ..domain.enums import FetchOutcome, FetchTier
from ..ports.acquisition import (
    BrowserFetcher,
    DocumentFetch,
    FetchMode,
    FetchRequest,
    FetchResult,
    HttpFetcher,
    mode_allows,
)
from .escalation import decide as decide_escalation

__all__ = ["DocumentAcquisition"]


#: 「没接线」与「接线了但依赖没装」是两句话。合并成一句的代价是排查方向被引错：
#: 前者要去改构建（谁忘了传 ``browser=``），后者要去装依赖（Playwright 浏览器）。
_REASON_NOT_WIRED = "browser_not_wired"
_REASON_UNAVAILABLE = "browser_unavailable"


class DocumentAcquisition:
    """把两个 tier 的 fetcher 合成一个按 mode 分派的入口。

    Args:
        http: 直连抓取器（必填 —— 没有它就没有 ``auto`` 的起点）。
        browser: 浏览器抓取器。``None`` 表示**本部署没有接线浏览器**，这与
            「接了但 ``available`` 为 False」是两件事，理由见上面那两个常量。
    """

    def __init__(self, *, http: HttpFetcher, browser: BrowserFetcher | None = None) -> None:
        self._http = http
        self._browser = browser

    @property
    def http(self) -> HttpFetcher:
        """直连那一层的抓取器，**只读**。

        它今天只有一个消费者（``interfaces/composition.py::default_crawl``），
        而那一个消费者的存在理由是「多页抓取的前沿与页面必须共用同一个抓取器」——
        两处各起一个会让超时、UA、重试策略变成一条没人观察的差异。

        一个返回私有字段的 property 通常值得怀疑（它把「外面该看什么」交给外面
        决定）。这里写下来的判据是：**它没有 setter**，而把 ``SiteCrawl`` 那一侧
        改成收一个 ``HttpFetcher`` 参数会让「两份」重新变得可表达 —— 那正是这个
        属性要消灭的东西。等第二个消费者出现时再看它是不是该变成端口。
        """
        return self._http

    # ------------------------------------------------------------------
    def fetch_document(
        self, request: FetchRequest, *, mode: FetchMode = FetchMode.AUTO
    ) -> DocumentFetch:
        """抓一份文档。``auto`` 时由 ``escalation.decide`` 决定要不要进浏览器。"""
        if mode is FetchMode.BROWSER:
            return self._browser_only(request, mode)

        if not mode_allows(mode, FetchTier.HTTP):
            raise ValueError(f"{mode!r} 不允许走 HTTP —— mode_allows 与分派表漂移了")

        initial = self._http.fetch(request)
        attempts = [initial]

        if mode is FetchMode.HTTP:
            # mode 拦下了升级。**这条理由要留下**：「没升级」与「升不了级」在
            # 报告上同形，而它们的处置完全不同（一个是调用方的选择，一个是缺陷）。
            return DocumentFetch(
                request=request,
                mode=mode,
                final=initial,
                attempts=tuple(attempts),
                decision="mode_disallows:browser",
            )

        blocked = self._unavailable_reason()
        decision = decide_escalation(
            initial,
            browser_available=not blocked,
            unavailable_reason=blocked or "browser_unavailable",
        )
        if not decision.escalate or self._browser is None:
            return DocumentFetch(
                request=request,
                mode=mode,
                final=initial,
                attempts=tuple(attempts),
                decision=decision.reason,
            )

        started = time.perf_counter()
        rendered = self._browser.fetch(request)
        browser_seconds = time.perf_counter() - started
        attempts.append(rendered)
        return DocumentFetch(
            request=request,
            mode=mode,
            # 最终结果取**浏览器那一次**，即使它比 HTTP 更差：升级之后的
            # 结论应当是关于那个 tier 的。混着取（成功用浏览器的、失败用 HTTP 的）
            # 会让 ``tier`` 字段与 ``body_path`` 指向两个不同的抓取。
            #
            # ``escalated_from`` 在这里补上，因为**只有这一层知道**：浏览器
            # fetcher 看不见触发它的那次 HTTP 结果，而 ``FetchResult`` 声明了这个
            # 字段。声明了却没人填的字段比没有这个字段更坏 —— 它看起来像一条记录，
            # 读起来永远是一个「这次不是升级来的」的假结论。
            final=replace(rendered, escalated_from=initial.outcome),
            attempts=tuple(attempts),
            decision=decision.reason,
            browser_seconds=browser_seconds,
        )

    # ------------------------------------------------------------------
    def _unavailable_reason(self) -> str:
        """浏览器用不了的原因；用得返回空串。两条 mode 路径共用同一句判断。

        ``auto`` 与 ``browser`` 都要说这句话，而它们对「用不了」的处置完全相反
        （前者停在 HTTP 结果上，后者明确失败）—— 处置不同、**理由必须同一个**，
        否则同一个部署在两种 mode 下给出两种诊断，排查的人会以为它们不是一件事。
        """
        if self._browser is None:
            return _REASON_NOT_WIRED
        if not self._browser.available:
            return _REASON_UNAVAILABLE
        return ""

    def _browser_only(self, request: FetchRequest, mode: FetchMode) -> DocumentFetch:
        """``mode=browser``：一次直连请求都不发。

        没接线 / 不可用时返回一个 **ERROR 结果**，既不抛异常，也**不退回 HTTP**。
        退回是最坏的那种处置：调用方要的是渲染后的页面，拿到的是一份需要 JS 的
        空壳，而两者在报告上只差一个 tier 字段。
        """
        blocked = self._unavailable_reason()
        if blocked:
            detail = (
                "本部署没有接线浏览器（browser=None）"
                if blocked == _REASON_NOT_WIRED
                else "浏览器已接线，但运行时依赖不可用（crawl4ai / Playwright 未安装）"
            )
            return self._refused(request, mode, blocked, detail)
        started = time.perf_counter()
        rendered = self._browser.fetch(request)
        return DocumentFetch(
            request=request,
            mode=mode,
            final=rendered,
            attempts=(rendered,),
            decision="mode_requires:browser",
            browser_seconds=time.perf_counter() - started,
        )

    @staticmethod
    def _refused(
        request: FetchRequest, mode: FetchMode, reason: str, detail: str
    ) -> DocumentFetch:
        """没有浏览器可用的那一次回执。

        ``tier`` 记 ``BROWSER`` 而不是 ``HTTP``：这一次**属于浏览器层级**，
        它只是没能发生。记成 HTTP 会让「指定 browser 的调用有多少次真的渲染了」
        这个问题在指标上永远答不出来。
        """
        result = FetchResult(
            request=request,
            outcome=FetchOutcome.ERROR,
            tier=FetchTier.BROWSER,
            final_url=request.url,
            error=detail,
            reason=reason,
        )
        return DocumentFetch(
            request=request, mode=mode, final=result, attempts=(), decision=reason
        )

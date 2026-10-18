"""``Crawl4AIBrowserFetcher`` —— BrowserFetcher 的第一实现（Crawl4AI / Playwright）。

**匿名公开浏览模式。** 允许：JavaScript、cookie、localStorage、普通 session state、cache。
禁止：登录目标站点、访问 private content、CAPTCHA 绕过、突破 access control。
本模块不提供任何登录、验证码求解或反检测能力的接口 —— 不是「没实现」，是**不提供**。

crawl4ai 是**可选**运行依赖。未安装时本模块仍可 import，只是 ``available`` 为 False、
``fetch`` 抛 ``ParserUnavailableError``。这样「没装浏览器」不会阻塞整个测试套件。

> 注：crawl4ai 的 Python API 在小版本间有变动（构造参数、结果字段名）。
> 本模块通过 ``_build_run_kwargs`` / ``_extract_html`` 两个窄接口隔离该变动，
> adapter contract（输入 ``FetchRequest``、输出 ``FetchResult``）保持稳定。

## 出口：这一层能同时做到「判」与「钉」的那一格

采集设计 §7 逐字：「所有URL（初始、重定向、浏览器主导航、iframe/worker请求和
子资源）在网络动作前检查允许协议、目标解析、私有/回环/link-local/元数据网段、
端口及来源策略；DNS解析与实际连接绑定」，而紧接着那一句是：「若所锁浏览器接口
不能在发送前拦截全部必要请求，组合根需采用可验证的受管网络层/拦截器，或禁用该
模式并明确阻塞；不能只保留『最终URL不合规就不存』宣称已安全」。

逐跳插判据（Playwright 的 ``context.route``）只给得出前半句：它拦得住请求，
拦不住「名字在我们判完之后换一个答案」—— 因为真正连出去的是 Chromium，
它自己解析。所以这一层走的是**受管出口**：每次抓取起一个
``adapters/egress_proxy.py`` 的本地正向代理，把浏览器的 ``--proxy-server`` 指到它。
于是浏览器**没有**别的出口：每一次网络动作（含 iframe / worker / 子资源，
服务端 worker 发的也算 —— 它们走的是同一套网络栈）都在那里被判过，
并且连接到的是**判过的那个地址**。这一条由
``tests/test_browser_egress_live.py`` 真开一次浏览器、指着一个自己立起来的
本机服务验过：那一跳被拒，而本机服务上**一次连接都没有**。

出口那一格还钉了另外两个参数（``--proxy-bypass-list=<-loopback>`` /
``--disable-quic``），它们是**防御**而不是本机读数所必需的那两格 ——
实测与理由写在 :meth:`_browser_config` 上，不要把它们读成「删掉测试就会红」。

代价写在明处：https 是端到端握手的隧道，出口看得见「连了哪个 host」，
看不见隧道里的 URL。所以 ``redirect_trace`` 在浏览器这一层仍然只有我们请求的
那一跳 —— 区别不在「能不能观测」，而在理由：不是因为 Chromium 不可观测，
而是因为那一层在 TLS 里。被拒的目标另有一格
（``FetchResult.egress_refusals``），因为「这一页想做而没做成的事」与
「这次抓取的结果」是两件事。

## 这一层**没有**宣称做到的事

``constrains_egress = True`` 说的是「每一次请求都过判据、且连接钉在判过的地址上」，
不多不少。**没有**说的是：内容可信（出口看不见 https 的内容，也就读不出正文里
的 prompt injection —— §7 那一条的处置在别处）、没有指纹（匿名浏览不等于不被
识别，见 ``tests/test_no_evasion.py``）、或者这一层比直连更安全。
落点判据（``_fetch_async`` 里渲染之后那次 ``check_target(final_url)``）**仍然
只是检测**：它挡的是「不把一次落点不合规的渲染写成一条看起来来路清白的研究
证据」，而不是「那一跳没发生过」—— 真正的预防在出口与入口。

## 诊断、来源账与尝试账：与直连那一层逐格同形

这一层此前**只产回收执、不产诊断**，于是它的每一个出口都让下游面对同一个歧义：
``FetchResult.diagnosis`` 是 ``None``。``None`` 的意思是「这一层没有产生诊断」，
而不是「这一次没有异常」—— 而两者在报告上同形，正是 §5 要消灭的那类读数。
今天这一层与 ``HttpxFetcher`` 一样：**每一个出口都带诊断**，每一份回执都过一次
``AccessGovernor``，每次 ``fetch()`` 都往尝试账上写**恰好一行**。

三处差异是刻意的，各自都有理由：

* **``method=MethodKind.BROWSER``**，而不是 ``HTTP``。它是
  ``attempt_id`` 的种子之一：写死 ``HTTP`` 会让这一跳算出一个 HTTP 尝试的 id，
  于是同一个 URL 的两条尝试从此不在一个命名空间里。
* **预算那两格不在这里定。** 这一层不消费任何计费额度，但「这一次尝试与一份预留的
  关系」不由此决定 —— 它由**请求**上那一格推出来（``request.reservation_id`` →
  ``domain/fetch_attempt.py::settlement_of``）。于是从直连升级上来的那一次，两行账
  押着同一份预留，而两行都是自己从请求上读的，不靠谁记得传。
* **挑战页的正文不落盘。** 判据是 ``AccessDiagnosis.body_may_be_kept``，而它由
  ``diagnose`` 回答：回应头 ``cf-mitigated: challenge`` **或**正文挑战指纹成立时
  为假（§4：「Cloudflare challenge/CAPTCHA 正文或对应头 → ``waf_challenge``；
  **不当网页证据**」）。此前这里只判 ``html.strip()``，于是一个正文里写着
  ``just a moment`` 的挑战页会被原样镜像到盘上 —— 而那正是本层最不该留下的东西。

治理层的顺序有一条承重约束：**可用性检查在 ``permit.admit()`` 之前**。``admit``
在放行的那一刻就记一次尝试，而 ``admit``/``record`` 必须成对 —— 一个「这台机器
没装浏览器」的部署不该让来源账多记一次，否则那本账会把部署问题读成站点问题。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from ..config import Settings
from ..domain.access import (
    AccessDiagnosis,
    ActionTaken,
    Observation,
    SuspectedCause,
    diagnose,
    safe_response_headers,
)
from ..domain.access_governance import AccessGovernor, Admission
from ..domain.enums import BlockReason, FetchOutcome, FetchTier
from ..domain.errors import ParserUnavailableError
from ..domain.fetch_attempt import MaterialProvenance, MethodKind
from ..domain.util import sha256_text, utc_now
from ..ports.acquisition import BrowserFetcher, FetchRequest, FetchResult
from ..ports.dns import DnsFailureKind, DnsResolutionError, HostResolver
from ..ports.fetch_attempt import FetchAttemptStore
from ..security.network import TargetVerdict, check_resolved, check_target
from .classify import assess_http_response
from .egress_proxy import EgressProxy
from .fetch_attempts import attempt_from, refusal_diagnosis, require_diagnosis
from .page_mirror import RENDERED_SUFFIX, store_page_bytes
from .resolution import SystemResolver

_DEFAULT_PORT: dict[str, int] = {"http": 80, "https": 443}

try:  # pragma: no cover - 取决于本地是否装了可选依赖
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

    _CRAWL4AI_IMPORT_ERROR = ""
except Exception as exc:  # noqa: BLE001 - 任何 import 期失败都等价于「不可用」
    AsyncWebCrawler = None  # type: ignore[assignment]
    BrowserConfig = None  # type: ignore[assignment]
    CrawlerRunConfig = None  # type: ignore[assignment]
    _CRAWL4AI_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def crawl4ai_available() -> bool:
    """crawl4ai 是否可 import。"""
    return AsyncWebCrawler is not None


def crawl4ai_unavailable_reason() -> str:
    return _CRAWL4AI_IMPORT_ERROR or "ok"


class Crawl4AIBrowserFetcher(BrowserFetcher):
    """基于 crawl4ai（底层 Playwright）的浏览器抓取器。

    Args:
        settings: 提供超时、UA 与 mirrors 目录。
        headless: 默认 True。无头匿名浏览，不注入登录态。
        resolver: 名字 → 地址。测试注入一个假的，**生产路径上是系统解析器**。
        egress: 受管出口。生产路径上永远是 ``None``（每次抓取起一个），传进来是给
            测试用的（见 :meth:`_browser_config`）。
        governor: 来源账。``None`` = 本抓取器自建一本（单测与离线重放要它如此），
            生产路径由组合根注入**进程那同一本**。语义与 ``HttpxFetcher`` 那一格
            逐字相同 —— 二十个账号共用的必须是同一本。
        attempts: 尝试账本的**工厂**，或 ``None``。``None`` 是缺省，含义是
            **不记账**：直接构造一个抓取器不该在盘上留下任何东西，而构造
            ``SqliteFetchAttemptStore`` 会 ``ensure_schema``，也就是会建库 ——
            所以这里收的是工厂而不是实例（与 ``HttpxFetcher`` 同一条理由）。
        material: 这一个实例拿到的正文是从哪来的。缺省 ``SYNTHETIC``，而**那正是
            绝大多数构造点的正确答案** —— 注入 ``egress`` / ``resolver`` 的那些
            构造点渲染的是我们自己立起来的靶子。真相与 ``HttpxFetcher`` 那一格
            逐字相同，见 ``ports/acquisition.py::Fetcher.material``。
    """

    name = "crawl4ai"
    tier = FetchTier.BROWSER

    #: 这一层把浏览器接到受管出口上（``adapters/egress_proxy.py``），所以
    #: 「每一次请求都过判据、且连接钉在判过的地址上」成立。理由与代价写在
    #: 模块 docstring 里 —— 尤其是它**没有**宣称的那几件。
    constrains_egress = True

    def __init__(
        self,
        settings: Settings,
        *,
        headless: bool = True,
        resolver: HostResolver | None = None,
        egress: EgressProxy | None = None,
        governor: AccessGovernor | None = None,
        attempts: Callable[[], FetchAttemptStore] | None = None,
        material: MaterialProvenance = MaterialProvenance.SYNTHETIC,
    ) -> None:
        self._settings = settings
        self.material = material
        self._headless = headless
        self._resolver = resolver or SystemResolver()
        self._attempts = attempts
        #: 记下第一次要写账时才构造出来的那个 store。每实例一份 memo，**不做模块级**：
        #: 一个模块级的 memo 会在测试里静默地绑到**第一份** settings 的库上，而
        #: 那个失败看起来像「第二个部署什么都没有记」。
        self._attempt_store: FetchAttemptStore | None = None
        #: 受管出口。生产路径上永远是 ``None``（每次抓取起一个），传进来是给测试
        #: 用的：一个允许「受控的本机目标」的出口，让「子资源被拒、而页面照常
        #: 渲染」这条判据能在**不向真实内网探测**的前提下被验到（§7 末句）。
        self._egress = egress
        # **这一层也持有一本来源账，缺省自建。** 理由与 ``HttpxFetcher`` 那一段
        # 逐字相同，而它在这里还多一层：这个抓取器是**升级路径** —— 一次 HTTP 失败
        # 触发的浏览器请求与下一次直连请求落在**同一个来源**上。两处各拿一本干净的
        # 账，就是 §5 那条坏结局的成因。
        self._governor = governor if governor is not None else AccessGovernor()

    @property
    def available(self) -> bool:
        return crawl4ai_available()

    def fetch(self, request: FetchRequest) -> FetchResult:
        """抓一次，**并留下这一次尝试的一行账**。与 ``HttpxFetcher.fetch`` 同形。

        ``started_at`` 取在 ``_run`` **之前**：它是 ``attempt_id`` 的种子之一，
        所以它必须量的是这次抓取的开始 —— 在 ``_run`` 返回之后取，会让两次相邻的
        抓取在秒级精度下算出同一个 id。``_run`` 的结果**原样**返回给调用方：
        记账是这一层的附加动作，它不改回执、不吞回执。

        落账失败**传播**，不吞 —— 静默丢掉账本就是「报告看起来正常」那个失败本身。
        """
        started_at = utc_now()
        result = self._run(request)
        store = self._attempt_store_or_none()
        if store is not None:
            store.put_attempt(
                attempt_from(
                    self._settings,
                    request,
                    result,
                    # ``method`` 与 ``material`` 这两格**必须写出来**，不给缺省
                    # （理由见 ``fetch_attempts`` 那个模块）：``BROWSER`` 是这一跳真的
                    # 用过的方法，``material`` 是这一个实例对自己材料的声明。
                    #
                    # 预算那两格不在这里 —— 它们由 ``request.reservation_id`` 推出来
                    # （同一个模块），所以一次 ``auto`` 抓取从直连升级到浏览器时，
                    # 两行账押着的是**同一份**预留：请求对象就是那一个。
                    method=MethodKind.BROWSER,
                    # 与上面两格同一条理由（见 ``fetch_attempts`` 那个模块），而它
                    # 在这一层还多一层：浏览器这一跳常常是**一次 HTTP 失败触发的**，
                    # 于是同一次抓取的两行账落在同一个 scope 下，只有这一格区分得开
                    # 「第一行是现场页面、第二行是我自己立起来的靶子」。
                    material=self.material,
                    started_at=started_at,
                    ended_at=utc_now(),
                )
            )
        return result

    def _attempt_store_or_none(self) -> FetchAttemptStore | None:
        """账本。``attempts=None`` → ``None`` → 不记账（见构造参数那一格）。"""
        if self._attempts is None:
            return None
        if self._attempt_store is None:
            self._attempt_store = self._attempts()
        return self._attempt_store

    def _run(self, request: FetchRequest) -> FetchResult:
        """入口判据 → 解析判据 → 可用性 → 来源账 → 一次渲染。**每个出口都带诊断。**"""
        started = time.perf_counter()

        # 判据在**可用性检查之前**。顺序是有意的：一个不安全目标与「本机有没有
        # 装浏览器」无关，它的结论不该随一台机器的依赖状态而变 —— 否则同一份
        # 输入在两台机器上得到两种结果，而其中一种会把排查方向引向依赖安装。
        verdict = check_target(request.url)
        if not verdict.allowed:
            return self._refused(request, verdict, started)

        # 同一个入口再加上解析那一半：一个公开域名解析到内网地址时，浏览器**一次
        # 都不会启动**。判据与 ``HttpxFetcher`` 用的是同一对函数，这里的差别只在
        # 于我们**没有**把连接钉在那个地址上（Chromium 自己解析，见模块 docstring）
        # —— 所以这一步窄一点：它挡得住一个坏名字，挡不住名下的子资源。
        resolved = self._resolve(request, started)
        if isinstance(resolved, FetchResult):
            return resolved

        # **可用性在 permit 之前**（理由见模块 docstring）：``admit`` 在放行的
        # 那一刻就记一次尝试，而这里根本发不出去 —— 让来源账为一台没装浏览器的
        # 机器记一笔，会把部署问题读成站点问题。
        if not self.available:
            raise ParserUnavailableError(
                "crawl4ai 不可用，无法执行 browser fetch。\n"
                f"原因：{crawl4ai_unavailable_reason()}\n"
                "安装：在项目 venv 中 pip install -e '.[browser]' 并安装 Playwright 浏览器。"
            )

        # **一次调用一个 permit**：这一层一轮只发一次请求（没有服务端说「现在就试」
        # 就再发第二次的场合 —— 重定向由 Chromium 自己跟，不在我们的循环里）。
        # 所以判定的顺序只有一问：现在能不能发。
        permit = self._governor.begin(request.url)
        admission = permit.admit()
        if not admission.allowed:
            return self._governed(request, admission, started)

        result = asyncio.run(self._fetch_async(request, started))
        # **收口点。** 这一层每一个出口都带诊断，所以 ``require_diagnosis`` 走到
        # 这里不该抛；它在这里的价值不是兜底，而是让 ``record`` **无条件被调用** ——
        # 一个 ``if diagnosis is not None`` 会让来源账静默地学不到这次失败。
        diagnosis = require_diagnosis(result, method=MethodKind.BROWSER)
        return replace(result, diagnosis=permit.record(diagnosis))

    def _resolve(self, request: FetchRequest, started: float) -> str | FetchResult:
        """入口的解析判据。通过时返回要去的地址，否则返回一份结果。

        回执里带的是**原来那份请求**，不是照 URL 新造一个 —— 后者会把 ``scope``
        与 ``purpose`` 丢成缺省值，于是一次被拒绝的浏览器抓取会记在一个没有归属
        的主体上，而报告上它与「这次运行什么都没抓到」同形。
        """
        parts = urlsplit(request.url)
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORT.get(parts.scheme.lower(), 443)
        try:
            addresses = self._resolver.resolve(host, port)
        except DnsResolutionError as exc:
            return self._dns_failure(request, exc, started)
        verdict = check_resolved(host, addresses)
        if not verdict.allowed:
            return self._refused(request, verdict, started)
        return addresses[0]

    # ------------------------------------------------------------------
    def _dns_failure(
        self, request: FetchRequest, exc: DnsResolutionError, started: float
    ) -> FetchResult:
        """解析失败**不是**一次策略拒绝 —— 所以它不是 ``BLOCKED``。

        判据与结论与 ``HttpxFetcher._dns_failure`` **逐字同一张表**（``ports/dns.py``
        自己写下了那两句话：``NONEXISTENT`` 说的是**这个来源可能没了**，
        ``TIMEOUT``/``FAILURE`` 说的是**我们这次没问到**），搬过来的只有一件事：
        这里没有 ``sent`` 可写，因为这一跳**一次都没发出去**。

        此前这一支的 ``diagnosis`` 是 ``None``，于是它收成的尝试账会写着
        ``cause=none, action=proceeded`` —— 一次解析失败被记成「一切正常」。
        """
        if exc.kind is DnsFailureKind.NONEXISTENT:
            cause, action = SuspectedCause.SOURCE_GONE, ActionTaken.STOPPED_METHOD
        else:
            cause, action = SuspectedCause.NETWORK_UNKNOWN, ActionTaken.DEFERRED_BACKOFF
        return FetchResult(
            request=request,
            outcome=FetchOutcome.ERROR,
            tier=FetchTier.BROWSER,
            final_url=request.url,
            block_reason=None,
            error=str(exc),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            reason=f"dns:{exc.kind.value}",
            diagnosis=AccessDiagnosis(cause=cause, action=action, detail=str(exc)),
        )

    # ------------------------------------------------------------------
    def _governed(self, request: FetchRequest, admission: Admission, started: float) -> FetchResult:
        """治理层说**不发**。**一个字节都没发出去。**

        结论与理由与 ``HttpxFetcher._governed`` 逐行相同（``BLOCKED`` 而不是
        ``ERROR``：那是终态，不会有任何一层把它读成「再试一次或许就好了」；
        ``block_reason`` 是 ``UNKNOWN``：这一跳没有观测，所以没有任何一条细分理由
        成立），差别只有两格：

        * ``tier`` 是 ``BROWSER``；
        * ``redirect_trace`` 留空，而 **http 那一层这里写的是 ``sent``**。理由是
          两边 ``sent`` 的含义不同：那一层 ``sent`` 是「真的发起过的那几跳」，
          而这一层是**一次调用一个 permit**、链上只有我们请求的那一跳 —— 到了
          ``_governed`` 连那一跳都没发生过。写 ``(request.url,)`` 会把「本来要去的
          地址」记成「去过的地方」，而这一格的名字是后者。
        """
        return FetchResult(
            request=request,
            outcome=FetchOutcome.BLOCKED,
            tier=FetchTier.BROWSER,
            final_url=request.url,
            body_path=None,
            block_reason=BlockReason.UNKNOWN,
            error=admission.detail,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            reason=f"governed:{admission.action.value}",
            diagnosis=AccessDiagnosis(
                cause=admission.cause,
                action=admission.action,
                detail=admission.detail,
            ),
        )

    # ------------------------------------------------------------------
    async def _fetch_async(self, request: FetchRequest, started: float) -> FetchResult:
        """一次渲染，配上它自己那一个受管出口。

        ``started`` 由 ``_run`` 传进来（不再是本协程自己取）：``elapsed_ms`` 量的
        是**这次抓取**从入口判据起的耗时，而不是从浏览器开始启动起的 —— 后者会把
        「这个名字解析了 3 秒」从报告里抹掉。
        """
        # 每次抓取一个出口：一次渲染里的全部请求共用它，于是「这一页试过什么」
        # 记在一处。传进来的那个（测试）由调用方负责起停。
        proxy = self._egress or EgressProxy()
        if self._egress is None:
            proxy.start()
        try:
            return await self._render(request, started, proxy)
        finally:
            if self._egress is None:
                proxy.stop()

    async def _render(
        self, request: FetchRequest, started: float, proxy: EgressProxy
    ) -> FetchResult:
        browser_config = self._browser_config(proxy)
        # 每次抓取使用全新匿名上下文：不持久化登录态、不加载用户 profile。
        # 这是「匿名公开浏览」在实现层面的落点，不要改成 persistent context。
        run_config = self._run_config()

        try:
            async with AsyncWebCrawler(config=browser_config) as crawler:
                result = await crawler.arun(url=request.url, config=run_config)
        except Exception as exc:  # noqa: BLE001 - 渲染失败不致命，转为可分类结果
            return self._render_failure(request, exc, proxy, started)

        html = _extract_html(result)
        status_code = _status_code_of(result)
        final_url = str(getattr(result, "url", request.url) or request.url)

        # 渲染结束之后**再判一次**落点。
        #
        # 这一条是**检测，不是预防**，而差别要写出来：出口挡得住「连到哪」，
        # 挡不住「落在哪」—— 一次重定向的**目标**是出口判过的（每一跳都是一次
        # 请求，都过判据），而这里问的是另一个问题：渲染之后的最终 URL 是不是
        # 一个我们愿意把内容收下来的地址。它挡住的是**下一步** —— 不把一次落点
        # 不合规的渲染写成一条看起来来路清白的研究证据（``body_path`` 会是
        # ``None``，理由是 ``unsafe_target``，调用方不会拿到那份 HTML）。
        #
        # 真正的预防在出口（每一次请求）与 ``fetch`` 的入口判据（一次浏览器都
        # 不会启动）上。三者的分工不是重复：入口判「值不值得开浏览器」，
        # 出口判「每一次网络动作」，落点判「这份内容收不收」。
        landed = check_target(final_url)
        if not landed.allowed:
            return self._refused(
                request,
                landed,
                started,
                note=f"浏览器渲染之后落在了 {final_url}；",
                # 这一跳**真的发出去了**（浏览器到了 ``final_url``），所以链上有它。
                # 与成功那一支写同一个值是刻意的：拒绝的是「收不收这份内容」，
                # 不是「去没去过」。
                redirect_trace=(request.url,),
            )

        outcome, block_reason = assess_http_response(
            status_code=status_code,
            content_type="text/html",
            text=html,
        )
        if outcome is FetchOutcome.EMPTY_SHELL:
            # HTTP 层判空壳 → 升级 browser；browser 渲染**后仍为空壳**，
            # 说明这个「空壳」是真实内容，不是渲染问题。不再无限套娃。
            outcome = FetchOutcome.RENDER_FAILURE

        observation = Observation(
            observed_status=status_code,
            headers=safe_response_headers(_response_headers_of(result)),
            text=html,
            # **``body_usable`` 取折叠之后那一格**，与 ``http.py`` 不同，理由要说清：
            # 那一层的空壳还会升级给浏览器，所以「空壳」在那里等于「这份正文还能用
            # 来判点什么」；而到了这一层空壳已经折叠成 ``RENDER_FAILURE``，是一次
            # **终态失败**。照抄 ``outcome in (OK, EMPTY_SHELL)`` 会让一份渲染失败的
            # 回执落成一个 ``cause=none, action=proceeded`` 的诊断 —— 而尝试账上
            # **没有** ``outcome`` 这一格，那一行于是与一次真正的成功读不出区别。
            body_usable=outcome is FetchOutcome.OK,
            url=request.url,
            final_url=final_url,
        )
        diagnosis = diagnose(observation)

        if not diagnosis.body_may_be_kept and outcome in (
            FetchOutcome.OK,
            FetchOutcome.RENDER_FAILURE,
        ):
            # **``cf-mitigated: challenge`` 是一个响应头，而分类器只看状态码与正文。**
            # 一个 200、带那个头、正文里没有挑战指纹的页面，``assess_http_response``
            # 会答 OK —— 于是那一页的正文会作为业务证据落盘。而 §4 逐字：
            # 「Cloudflare challenge/CAPTCHA 正文或对应头 → ``waf_challenge``；
            # **不当网页证据**」。诊断读到了那个头（``Observation.challenge_header``），
            # 所以这里**以诊断为准**。
            #
            # 与 ``http.py::_response_exchange`` 那一条是同一句话、同一张判据表，
            # 只差一格：那一层的空壳还没折叠，所以它含 ``EMPTY_SHELL``；这一层的
            # 空壳已经折叠成 ``RENDER_FAILURE``，而一个挑战页正好常常就是一个
            # 一百来字节的空壳 —— 所以这一层要含的是 ``RENDER_FAILURE``。
            # 少了它，那次抓取的结论会写成 ``render_failure``、``block_reason`` 为空，
            # 而诊断说 ``waf_challenge`` —— 同一份回执里两个结论。
            outcome = FetchOutcome.BLOCKED
            block_reason = BlockReason.WAF_CHALLENGE

        body_path = None
        digest = ""
        # **正文落盘以诊断为准**（``body_may_be_kept``），而不是以 ``html.strip()``
        # 为准。此前这里只判后者，于是一个正文里写着 ``just a moment`` 的挑战页
        # 会被原样镜像到盘上 —— 而那正是本层最不该留下的东西（§4）。
        # ``body_may_be_kept`` 今天只在 ``WAF_CHALLENGE`` 上为假，所以这是一次纯粹的
        # 收紧：403 / 429 / 付费墙的正文此前落盘、此后仍然落盘（与 ``http.py``
        # 那一层在这件事上的不一致是既有的，不在本切片范围内）。
        if html.strip() and diagnosis.body_may_be_kept:
            body_path = self._persist(request, html, url=final_url)
            digest = sha256_text(html)

        # 状态码没量到这件事**必须写在回执上**。分类那一格仍然按正文可用性走
        # （``assess_http_response`` 收 ``None``，理由在它的 docstring 里），
        # 但「这一次的结果是对着一个没有状态码的渲染下的判断」是一条独立的事实 ——
        # 它最常见的成因是库换了一次字段名，而那种事在报告上不能长得像一次
        # 顺利的抓取。调用方按 ``reason`` 就能把它数出来。
        refusals = proxy.refusals
        reason = f"browser:{outcome.value}"
        if status_code is None:
            reason += ";status_unknown"
        if refusals:
            # 「渲染出来是空的」与「它想连的东西被我们挡了」在报告上必须是两句话。
            # 只数条数、不在这里铺开目标：目标已经在 ``egress_refusals`` 那一格上，
            # 而同一条信息有两个副本时，读的人不知道该信哪一个。
            reason += f";egress_refused:{len(refusals)}"

        return FetchResult(
            request=request,
            outcome=outcome,
            tier=FetchTier.BROWSER,
            final_url=final_url,
            status_code=status_code,
            content_type="text/html",
            # 与 ``http.py`` 同一句话：这一格非有不可，因为尝试账要在三个月后回答
            # 「那天服务端回了什么」，而一份只留状态码的记录答不出 ``Retry-After``
            # 与 ``cf-mitigated`` 这类**决定过处置**的东西 —— 后者正是上面那次改判
            # 的依据，读不出来它就没法复核。观测里**已经洗过**（构造 ``Observation``
            # 时走的就是 ``safe_response_headers``）。
            response_headers=dict(observation.headers),
            body_path=body_path,
            body_sha256=digest,
            body_bytes=len(html.encode("utf-8")),
            block_reason=block_reason,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            reason=reason,
            # 链上只有**我们请求的那一跳**。https 是端到端握手的隧道，出口看得见
            # 「连了哪个 host」，看不见隧道里那个 URL 走到了哪里，所以中间那几跳
            # 在这一层看不见 —— 补出它们就是编造。理由写在 ``ports/acquisition.py``
            # 那一格（它同时与 ``egress_refusals`` 划清分工：一个说「怎么到这儿」，
            # 一个说「哪些没去成」）。
            redirect_trace=(request.url,),
            egress_refusals=refusals,
            diagnosis=diagnosis,
        )

    def _render_failure(
        self, request: FetchRequest, exc: BaseException, proxy: EgressProxy, started: float
    ) -> FetchResult:
        """渲染本身抛了。**这一支也带诊断。**

        诊断从一次 ``transport_error`` 观测来，而不是折成 ``cause=none``：这一层
        在渲染失败时**没有拿到任何可分类的东西**，而 ``domain/access.py`` 的
        ``_NETWORK_ERROR_KINDS`` 把「认得出来是网络类的异常」与「别的」分开，
        于是这条诊断至少答得出
        「是一次网络类故障，还是浏览器自己出的问题」—— 两者都归
        ``TRANSIENT_OR_UNKNOWN`` 或 ``NETWORK_UNKNOWN``，都是**可退避**的根因，
        于是同一个来源上反复渲染失败会像别的传输故障一样推进熔断计数。
        记成 ``CONTENT_UNUSABLE`` 就没有这回事（它不在 ``SETBACK_CAUSES`` 里），
        一个持续渲染不出来的站点会被无限重试。
        """
        refusals = proxy.refusals
        # 同一个标记在两条分支上都出现，理由在这一条上更硬：一次**失败**的渲染
        # 与一次「失败，而且它想连的三个东西被我们挡了」是两件事，而按
        # ``reason`` 数失败的人只会看到前一句。目标本身在 ``egress_refusals``
        # 上，不在这里重复。
        reason = "browser_render_error"
        if refusals:
            reason += f";egress_refused:{len(refusals)}"
        observation = Observation(
            url=request.url,
            # 这一跳没有落点：浏览器没渲染出任何东西来，所以「落在哪」就是「本来
            # 要去哪」。写一个 ``final_url`` 而不写它，会让报告里出现一个看起来
            # 到过的地址。
            final_url=request.url,
            transport_error=f"{type(exc).__name__}: {exc}",
            transport_error_kind=type(exc).__name__,
        )
        return FetchResult(
            request=request,
            outcome=FetchOutcome.RENDER_FAILURE,
            tier=FetchTier.BROWSER,
            final_url=request.url,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            reason=reason,
            redirect_trace=(request.url,),
            egress_refusals=refusals,
            diagnosis=diagnose(observation),
        )

    def _browser_config(self, proxy: EgressProxy) -> BrowserConfig:
        """浏览器配置：**出口**是这里接上的，别处没有第二条路。

        出口本身只有一格是承重的（``proxy_config``），另两格是**防御**。
        这个区分是本机实测出来的，写在这里因为它决定了「删掉某一格测试会不会红」：

        * ``proxy_config``：浏览器的全部网络动作从这里走。**这一格是承重的** ——
          少了它，``page.example.co.uk`` 这种「只存在于测试里」的主机名根本解析不了，
          页面渲染不出来。用 ``proxy_config`` 而不是 ``proxy`` 也是实测的选择：
          这个固定版本里 ``BrowserConfig.proxy`` 已被标为 deprecated 并在一条路径上
          被忽略（``browser_manager.py`` 那处警告），照抄字段名会得到一个
          「配了但没接上」的部署，而它看起来完全正常。
        * ``--proxy-bypass-list=<-loopback>``：关掉 Chromium 文档里那条**对回环地址
          的隐式绕过**。实测（Chromium 1243 + Playwright 的 ``--proxy-server``）：
          **这一版本来就没有那条隐式规则** —— 不加这一格，一个指向
          ``http://127.0.0.1:PORT/steal.js`` 的 ``<script>`` 仍然走代理、仍然被拒。
          而 ``--proxy-bypass-list`` 本身**确实被遵守**：显式把 ``localhost,127.0.0.1``
          写进去，同一个请求就直连到了本机服务、出口上一条记录都没有。
          所以这一格挡的不是今天的读数，是「换一个 Chromium 或换一种配置方式之后
          那条隐式规则开始生效」—— 它**不是**本机读数所必需的那一格。
        * ``--disable-quic``：UDP 那条路（HTTP/3）。**未实测**：本机没有可构造的
          HTTP/3 目标，所以这一格是一条「不靠上游默认值」的防御，而不是一条量到的
          事实。写完它之后不要顺手在某处宣称 HTTP/3 已经被挡住。

        **这一格为什么在 ``BrowserConfig`` 上，而不是在 ``CrawlerRunConfig`` 上**：
        实测（0.9.3），浏览器启动参数里的 ``--proxy-server`` 取自
        ``BrowserConfig.proxy_config``（``browser_manager.py:122``），而 run config
        上那一格是**每个上下文的重载**，且会被 ``arun`` 里那个防反爬重试循环覆写
        （``async_webcrawler.py:449`` 的 ``config.proxy_config = _proxy``，那个
        ``_proxy`` 从 run config 自己的列表里来，今天只有一个 ``None``）。
        两件事都要说清：今天那个循环只跑一次，所以它**不会**关掉这一格（出口仍然
        说了算，``tests/test_browser_egress_live.py`` 量到的就是这个）；但把出口地址
        搬到 run config 上，等于把它交给一个我们不控制的循环去覆写 —— 而覆写成
        ``None`` 之后浏览器仍会「正常」渲染，报告上什么都看不出来。

        ``headless`` 与 UA 与出口无关，但它们必须与浏览器一起被构造 —— 分成两处
        拼配置会让「这次抓取用的是哪个 UA」在某一条路径上悄悄不同。
        """
        return BrowserConfig(
            headless=self._headless,
            user_agent=self._settings.user_agent,
            proxy_config={"server": proxy.url},
            extra_args=[
                "--proxy-bypass-list=<-loopback>",
                "--disable-quic",
            ],
        )

    def _run_config(self) -> CrawlerRunConfig:
        """每一次渲染的 run config。**三格都是判定，一格都不是缺省。**

        ## ``max_retries=0``

        §5：「SDK 隐藏重试必须可观察，且不得双重放大」。这一格不是摆设 ——
        实测（0.9.3）``async_webcrawler.py:405`` 写的是
        ``_max_attempts = 1 + getattr(config, "max_retries", 0)``，而那个循环的触发
        条件是**防反爬判定**（日志 tag 是 ``ANTIBOT``，消息是
        ``Anti-bot retry {attempt}/{max_retries}``）。所以：

        * 它今天为 0 **只是因为那是库的缺省值**，而缺省不是判定 —— 一次库升级把它
          改成 3，就会让每一个挑战页被试三次，而这件事在我们的账上**看不见**：
          一次 ``fetch()`` 仍然只写一行账，于是报告上的尝试数一个字不变，而我们对
          那个来源真的发出的请求数翻了四倍。同源共享限流正是按那个数算的。
        * 直连那一层有一条同形的判据（``HttpxFetcher`` 显式 ``retries=0``）。
          两层都钉，是因为「不许有隐藏重试」不是一个只对其中一层成立的要求。

        ## ``cache_mode="BYPASS"``

        读缓存会让两次抓取读到同一份旧正文，而回执上写着「刚刚抓到了」——
        在采集这件事上，「这一页现在是什么」是唯一有意义的读数。

        ## ``page_timeout``

        一个**有界**的等待。它同时是这一层唯一的超时来源：库自己的缺省在这里不适用，
        因为「等多久算失败」是部署的决定，该从 ``Settings`` 读。

        ## 为什么它被提成一个具名方法

        与 ``_browser_config`` 同一条理由：**它得能被判据够到**。库是可选依赖，
        而这一格要判的是「我们写了那个决定」与「库真的读了它」两件事 ——
        前者在替身那一层就能判，后者只有真库能判，两条判据共用同一个构造点，
        才不会有第三条路径悄悄绕过它。
        """
        return CrawlerRunConfig(
            page_timeout=int(self._settings.browser_timeout_seconds * 1000),
            cache_mode="BYPASS",
            max_retries=0,
        )

    def _refused(
        self,
        request: FetchRequest,
        verdict: TargetVerdict,
        started: float,
        *,
        note: str = "",
        redirect_trace: tuple[str, ...] = (),
    ) -> FetchResult:
        """判据拒绝了这一跳。与 ``HttpxFetcher._refused`` 同形 ——
        两个抓取器给出同一种结果，调用方才不必按层级分支。

        ``body_path`` 一定是 ``None`` —— 一次被拒绝的抓取不得留下镜像，
        否则「抓到了但标记为拒绝」会在审计时节外生枝。

        ## 诊断：``block_reason`` 只有一格，而拒绝有九种

        ``block_reason`` 恒为 ``UNSAFE_TARGET``（那是**这一类结果**的名字），
        所以它答不出「是哪一种拒绝」。而两种拒绝的处置不同：地址不安全要记下
        这个目标并改来源，重定向类拒绝要修的是这一条链 —— 判据与
        ``HttpxFetcher._refused`` 逐字共用（:func:`refusal_diagnosis`），
        ``cause_evidence_ref`` 留空（一次地址判据拒绝的证据就是 ``reason`` 里
        那个理由码，不是一份外部凭据）。

        此前这一支的 ``diagnosis`` 是 ``None``，于是一次被地址判据拦下的抓取
        在尝试账上会写成 ``cause=none, action=proceeded``。那是最不该出错的一格：
        §5 要记下来的正是**没有产出证据的那些尝试**。

        ``redirect_trace`` 由调用方给：入口判据拒绝时它为空（一次都没发出去），
        落点判据拒绝时是 ``(request.url,)``（那一跳真的去了）。
        """
        assert verdict.refusal is not None, "拒绝必须有理由码"
        cause, action = refusal_diagnosis(verdict.refusal)
        return FetchResult(
            request=request,
            outcome=FetchOutcome.BLOCKED,
            tier=FetchTier.BROWSER,
            final_url=request.url,
            body_path=None,
            block_reason=BlockReason.UNSAFE_TARGET,
            error=f"{note}{verdict.detail}",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            reason=f"target_refused:{verdict.refusal.value}",
            redirect_trace=redirect_trace,
            diagnosis=AccessDiagnosis(cause=cause, action=action, detail=verdict.detail),
        )

    def _persist(self, request: FetchRequest, html: str, *, url: str) -> Path:
        # 与 `http.py` 落在**同一棵**目录树下、用**同一个**界：浏览器抓取与直连
        # 抓取是同一次抓取的两个 tier，落在两棵按不同界切的树里会让「这个账号的
        # 页面镜像在哪」有两个答案。约定与写入判据在 `page_mirror.py` 一处定义，
        # 这一层只给扩展名 —— 浏览器 tier 的产物恒为 ``.rendered.html``。
        return store_page_bytes(
            self._settings,
            scope=request.scope,
            url=url,
            payload=html.encode("utf-8"),
            extension=RENDERED_SUFFIX,
        )


def _extract_html(result: object) -> str:
    """从 crawl4ai 结果里取 HTML，兼容字段名变动。"""
    for attribute in ("html", "cleaned_html", "fit_html"):
        value = getattr(result, attribute, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _response_headers_of(result: object) -> dict[str, str]:
    """从 crawl4ai 结果里取响应头，交给 ``safe_response_headers`` 洗一遍。

    ## 这一格是**量出来的**，不是照着某一个版本的名字写的

    写它之前本机真的渲染过一次（探针与输出在
    ``artifacts/work-items/marketops/TARGET-CONVERGENCE-B15H/``）：起一个本机服务，
    回 ``cf-mitigated: challenge`` 与 ``retry-after: 42``，让 crawl4ai 渲染它。
    实测（0.9.3）：

    * ``response_headers`` **存在**，是一个 ``dict``，键**已经小写**
      （``cf-mitigated`` / ``retry-after`` 都在里面）；
    * ``headers`` 那一格**不存在** —— 所以不要照 ``httpx`` 的习惯去读它；
    * ``dir(result)`` 只列出 ``_results`` 一项（这个类覆盖了 ``dir()``），
      所以字段名只能经 ``getattr`` 探，**不能从 ``dir()`` 读**。

    这一格为什么非有不可：``Observation.challenge_header`` 读的就是这里来的
    ``cf-mitigated``，而 ``diagnose`` 的两次改判（挑战页 → ``BLOCKED``、
    ``retry_after_s`` → 有界退避）都挂在它上面。读不到它，§4 那一条就只能靠正文指纹，
    而一个正文干净的挑战页会原样落盘。

    洗的办法与 ``http.py`` 共用同一个白名单（``safe_response_headers``）：这一层
    同样会往尝试账与回执上写头，而「哪几个头可以带出去」不该有两个答案。
    认不出来的形状（不是 ``Mapping``）返回空字典，而不是抛 —— 与
    ``_extract_html`` / ``_status_code_of`` 同一条纪律：库的字段名变动不该让一次
    已经量好的抓取变成一次崩溃，而「一个头都没有」在这一格有确切含义。
    """
    raw = getattr(result, "response_headers", None)
    if not isinstance(raw, Mapping):
        return {}
    return safe_response_headers({str(key): str(value) for key, value in raw.items()})


def _status_code_of(result: object) -> int | None:
    """浏览器那一层报出来的状态码；**读不出来时是 ``None``，不是 0**。

    端口那一格声明的就是 ``int | None``（``ports/acquisition.py``），而这里原先写的是
    ``int(getattr(result, "status_code", 0) or 0)`` —— 它把三种不同的处境压成同一个
    ``0``：库换过一次字段名、渲染根本没有经过 HTTP、以及一个理论上不存在的 HTTP 0。
    下游把它们当成一次测量接着走（``0`` 不是 4xx/5xx，于是分类那一步读出来是
    「没有异常」），而报告上它与一个真的量到的状态码同形。

    判不出来就是判不出来：这一格原样是 ``None``，一路进 ``FetchResult.status_code``
    与 ``Evidence.http_status``。分类那一步收的也是这个 ``None``（``assess_http_response``
    的签名是 ``int | None``）—— 调用点以前写 ``status_code or 200``，那个折算已经
    从句法上拿掉，所以「没有状态码」不再有任何一条路能变成 200。

    **那条折算今天并没有造出不同的分类结果**，这一点也要说清楚，否则会被人读成
    「修好了一处误判」：``or 200`` 与 ``None`` 落进同一批分支（``200`` 不在
    ``(404, 410)`` 里、也不 ``>= 400``，而拦截指纹本来就从正文读）。所以它不是一次
    改判，是一次**把那个写法从代码里拿掉** —— 一个能写 ``or`` 的地方迟早会有人在
    下游再写一次，而那时候落点不一定还是同一批分支。真正的可观测差别在 ``reason``
    上：状态码没量到时它写 ``status_unknown``。
    """
    raw = getattr(result, "status_code", None)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


__all__ = ["Crawl4AIBrowserFetcher", "crawl4ai_available", "crawl4ai_unavailable_reason"]

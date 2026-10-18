"""``HttpxFetcher`` —— 默认抓取层级。

原则：**先 HTTP，且大多数情况到此为止**。浏览器是例外路径，不是默认路径。

镜像落盘的**位置约定**在 ``page_mirror.py`` 一处定义。文件名是 **URL** 的
散列前缀，不是内容的 —— 这里此前写成「内容寻址」，还带一句由它推出的
「不同 URL 内容相同不会产生重复文件」，而按 URL 命名时那句是错的。名字的职责
是**定位**（同一 URL 重复抓取落回同一个文件），字节的职责是**校验**
（``content_sha256`` 逐字节重算）。两者的分工写在 ``page_mirror.py``。

## 重定向是**我们**跟的，不是 httpx 跟的

``follow_redirects=True`` 是这一族保护里最常见的漏洞形态，而且它在研究场景里
一点也不罕见：一个公开站点回 ``302 Location: http://169.254.169.254/``，客户端
自己跟过去，业务代码从头到尾只见过那个公开域名。

所以这里关掉自动跟随，自己走一条**每跳都过判据**的有界循环。被拒绝的那一跳
**根本不会被发出** —— 事后检查 ``response.url`` 的实现只能「发现」一次 SSRF，
而那时请求已经离开了进程。

## 每一跳先解析、后判断、再把连接钉在那个地址上

采集设计 §7 逐字：「DNS 解析与实际连接绑定，防只检输入后 DNS 或重定向改向」。
只判 URL 里的名字挡不住 ``http://a-name-that-resolves-to-169.254.169.254/``，
而「判完之后让客户端自己再解析一次」是一个 TOCTOU：两次解析之间那个名字可以
换一个答案（DNS rebinding），而两次都会通过各自那一关。

所以每一跳走三步，顺序不能换：

1. ``check_target`` 判字面量（协议、凭据、保留名、字面 IP）。
2. ``HostResolver.resolve`` **真的问一次** DNS，``check_resolved`` 对**每一个**
   返回的地址点头才继续 —— 有一个不全局可路由就整条拒。
3. 把这一跳的地址**改写进请求 URL**，同时把
   ``Host`` 头与 TLS 的 ``sni_hostname`` 恢复成原来的名字。于是 socket 连的是
   我们判过的那个地址，而站点看到的仍然是它自己的域名、证书校验也仍对着它。

第 3 步是本模块与 `httpx` 之间唯一一处需要解释的接缝：``httpcore`` 用请求扩展
``sni_hostname`` 作为 TLS 的 ``server_hostname``（``httpcore`` 1.0.9，
``_sync/connection.py`` 与 ``_async/connection.py`` 各一处），证书校验因此仍然
对着**原主机名**做。少了这一格，钉住地址就会把 https 抓取变成一次证书不匹配。

## 因此 ``response.url`` 不再被当作事实读

钉过之后，``httpx`` 从请求上取到的 ``response.url`` 是**那个地址**，不再是那个
域名。所以本模块自己记着 ``url``（上面那个循环变量）并用它算 ``final_url``、
解析相对 ``Location``、给镜像命名。以前这些读的是 ``response.url`` ——
在钉住之后，那个读数会把 ``https://example.co.uk/a`` 写成 ``https://93.184.216.34/a``，
而镜像目录、证据的 ``source_url`` 与 ``final_url`` 会一起跟着错。

## 每一次尝试都留一行账，而记账的地方在**收口点**

:meth:`HttpxFetcher.fetch` 是唯一一个「这次抓取结束了，结果在这里」的地方，所以
账落在它这里：先取一个 ``started_at``，跑一次 ``_run``，然后不管结果是拿到页面、
超时、被拒还是被治理层拦下，都由 :func:`_attempt_from` 收成一条
:class:`~marketops.domain.fetch_attempt.FetchAttempt` 交给账本
（``ports/fetch_attempt.py``）。

**为什么不散在内层各个出口各记一次**：出口有六个（地址判据拒、解析失败、治理层
拒付、传输失败、正常收口、重定向类拒绝），而设计 §5 要记下来的**恰恰是被拒的那
几类** —— 一次 403、一次挑战页、一次地址判据拦下都**产不出一条证据**。把它们散在
六个出口上，第七个出口出现的那天就会少一行，而「少一行」与「那次抓取根本没发生」
在账上同形 —— 于是那份记录解释不了它本来要解释的那件事。

**注入的是工厂，而且缺省不记账。** 直接构造一个抓取器（大量单测、离线重放）
时 ``attempts=None``，一个字节都不落盘；生产路径由组合根注入
``lambda: SqliteFetchAttemptStore(...)``。必须是工厂而不是实例：构造那个 store 会
``ensure_schema``，也就是**会建库**（与 ``default_scene_store`` 逐条同一条理由）。

**``fetch`` 中途抛异常时不落账。** 账是从一份 ``FetchResult`` 收出来的，而异常路径
上没有任何结果可收。写在这里而不是让它看起来完整：一次让内层抛出去（例如镜像
落盘失败）的抓取在尝试账上不留痕。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

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
from ..domain.fetch_attempt import MaterialProvenance, MethodKind
from ..domain.util import sha256_text, utc_now
from ..ports.acquisition import FetchRequest, FetchResult, HttpFetcher
from ..ports.dns import DnsFailureKind, DnsResolutionError, HostResolver
from ..ports.fetch_attempt import FetchAttemptStore
from ..security.network import (
    MAX_REDIRECTS,
    TargetRefusal,
    TargetVerdict,
    check_resolved,
    check_target,
)
from .classify import assess_http_response
from .fetch_attempts import attempt_from, refusal_diagnosis
from .page_mirror import store_page_bytes
from .resolution import SystemResolver

#: 默认端口。**不是为了拼 URL**（``urlsplit().port`` 已经给了缺省），而是为了
#: 判断 ``Host`` 头里要不要写端口 —— 显式写 ``:443`` 与不写是同一个地址，
#: 但有些虚拟主机按字面量匹配，多写一个端口会得到一个 404 而看不出原因。
_DEFAULT_PORT: dict[str, int] = {"http": 80, "https": 443}

#: 按 content-type 推断的镜像文件扩展名。
_EXTENSION_BY_TYPE: tuple[tuple[str, str], ...] = (
    ("html", ".html"),
    ("pdf", ".pdf"),
    ("json", ".json"),
    ("xml", ".xml"),
    ("plain", ".txt"),
    ("csv", ".csv"),
)


def _extension_for(content_type: str, url: str) -> str:
    lowered = content_type.lower()
    for needle, extension in _EXTENSION_BY_TYPE:
        if needle in lowered:
            return extension
    tail = url.rsplit("/", 1)[-1].lower()
    for extension in (".pdf", ".html", ".htm", ".txt", ".json", ".xml"):
        if tail.endswith(extension):
            return extension
    return ".bin"


@dataclass(frozen=True)
class _Exchange:
    """这一跳**真的换回来的**东西：一次 HTTP 响应，或一次传输层失败。

    两者放同一个类型里，是因为它们在调用方看来是同一个问题的两种答案 ——
    「这一跳结束了，结果在这里」。拆成两个类型会让下面那条判据（治理层拒付时
    把最后一份观测的 ``action`` 改写掉）要在两个分支里各写一遍，而两个分支里
    总有一个会在下一次改动时漏掉。

    ``response`` 为 ``None`` 就是「没有响应对象」那一支。它**不是**「状态码读不出来」：
    后者由 ``observation.observed_status is None`` 表达，而那样的记录仍然带着一个
    响应对象。

    它不进 ``ports/``：端口那份回执（``FetchResult``）是给下游的，这一份是
    ``fetch`` 循环内部的一次交接，字段名与 ``httpx`` 绑在一起。
    """

    url: str
    observation: Observation
    diagnosis: AccessDiagnosis
    response: httpx.Response | None = None
    #: 这一跳归到哪个 ``FetchOutcome``。``response is None`` 时它是
    #: ``TIMEOUT`` / ``ERROR``（由抛出来的异常类型决定，见 ``_exchange``）。
    outcome: FetchOutcome = FetchOutcome.ERROR
    block_reason: BlockReason | None = None
    raw: bytes = b""
    text: str = ""
    content_type: str = ""
    truncated: bool = False
    error: str = ""


class HttpxFetcher(HttpFetcher):
    """基于 httpx 的抓取器。

    Args:
        settings: 提供超时、UA、大小上限、mirrors 目录与工作区。
        client: 测试注入。
        resolver: 名字 → 地址。测试注入一个假的，**生产路径上是系统解析器**。
            它必须可注入，否则「这个名字解析到 169.254.169.254」那条判据只能靠
            真实 DNS 才能走到 —— 而 §7 明令不向真实内网或云 metadata 探测。
        governor: 来源账。``None`` = 本抓取器自建一本（单测与离线重放要它如此），
            生产路径由组合根注入**进程那同一本**。
        attempts: 尝试账本的**工厂**，或 ``None``。``None`` 是缺省，含义是
            **不记账**：直接构造一个抓取器不该在盘上留下任何东西，而构造
            ``SqliteFetchAttemptStore`` 会 ``ensure_schema``，也就是会建库 ——
            所以这里收的是工厂而不是实例（与 ``default_scene_store`` 逐条同一条
            理由，见本模块 docstring 的「每一次尝试都留一行账」）。
        material: 这一个实例拿到的正文是从哪来的。缺省 ``SYNTHETIC``，而**那正是
            绝大多数构造点的正确答案** —— 注入 ``client`` / ``resolver`` 的那些
            构造点是替掉了网络的，它们拿到的页面是我们自己造的。真出网的组合根
            显式写 ``LIVE``。完整理由见 ``ports/acquisition.py::Fetcher.material``。
    """

    name = "httpx"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
        resolver: HostResolver | None = None,
        governor: AccessGovernor | None = None,
        attempts: Callable[[], FetchAttemptStore] | None = None,
        material: MaterialProvenance = MaterialProvenance.SYNTHETIC,
    ) -> None:
        self._settings = settings
        self.material = material
        self._attempts = attempts
        #: 记下第一次要写账时才构造出来的那个 store。每实例一份 memo，**不做模块级**：
        #: 一个模块级的 memo 会在测试里静默地绑到**第一份** settings 的库上，而
        #: 那个失败看起来像「第二个部署什么都没有记」。
        self._attempt_store: FetchAttemptStore | None = None
        self._client = client or httpx.Client(
            timeout=settings.http_timeout_seconds,
            # 关掉自动跟随 —— 见模块 docstring。注入的 client 也由调用点显式
            # 传入 follow_redirects=False，两处都写，因为「注入的 client 恰好
            # 也没开」是一次运气，不是一条约束。
            follow_redirects=False,
            headers={"User-Agent": settings.user_agent},
            # **SDK 的隐藏重试钉到 0。** ``httpx`` 的 ``HTTPTransport`` 默认就是 0，
            # 但「默认是 0」与「我们说它是 0」是两件事：一次库升级、一个包装过的
            # ``Client``、或者谁把 transport 换成别的东西，都会让这一层在**我们数不到**
            # 的地方重发请求，而 §5 要求每一次发出都可见、且不许与上层的重试双重放大。
            # 显式传一个 transport 是这条要求唯一的落点 —— 它可读回（判据里那条
            # ``transport._pool._retries``），而一个默认值是读不回来的。
            transport=httpx.HTTPTransport(retries=0),
        )
        self._resolver = resolver or SystemResolver()
        # **一个抓取器持有一本来源账。** 缺省自己建一本，而生产路径上由组合根注入
        # 那**同一个**（见 ``interfaces/composition.py``）—— 20 个账号各拿一本账时，
        # 每一个都以为自己是从零开始的，而这正是 §5 那条坏结局的成因。
        self._governor = governor if governor is not None else AccessGovernor()

    def fetch(self, request: FetchRequest) -> FetchResult:
        """抓一次，**并留下这一次尝试的一行账**。

        两件事在这里合成一件，顺序是承重的：

        1. ``started_at`` 取在 ``_run`` **之前**。它是 ``attempt_id`` 的种子之一
           （见 ``domain/fetch_attempt.py::attempt_id``），所以它必须量的是这次
           抓取的开始 —— 在 ``_run`` 返回之后取，会让两次相邻的抓取在秒级精度下
           算出同一个 id。
        2. ``_run`` 的结果**原样**返回给调用方。记账是这一层的附加动作，它不改
           回执、不吞回执、也不因为自己失败而让回执变成别的东西。

        ## 落账失败**传播**，不吞

        账本抛出来（库不可写、这一行不属于这个库、同 id 配了不同内容）时异常往上
        走，调用方就看不到这份回执了。这是刻意的：静默丢掉账本正是「没有生产者而
        报告看起来正常」那个失败本身，而它比一次显式的失败更坏 —— 后者至少有人
        会去看。

        ## 为什么记账在``fetch`` 而不是 ``_run``

        ``_run`` 的每一个出口都返回一份回执，但**入口只有一个**。把记在入口上的
        东西放到出口上，就得在六个出口各写一遍，而漏掉的那一个的表现是账上少一行。
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
                    # ``method`` **必须写出来**，不给缺省（理由见 fetch_attempts 那个
                    # 模块）：``HTTP`` 是这一跳真的用过的方法。
                    #
                    # 预算那两格不在这里 —— 它们由 ``request.reservation_id`` 推出来
                    # （同一个模块）。这一层没有「自己知不知道自己押着什么」的问题：
                    # 请求上写着，账上就写着。
                    method=MethodKind.HTTP,
                    # 这一格也是**说出来的**，而它说的是**这一个实例**是不是在
                    # 抓真实的第三方页面。它不是从 URL 或从回执推的 —— 一个抓
                    # 受控夹具的实例与一个抓现场页面的实例在回执上同形。
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
        started = time.perf_counter()

        url = request.url
        # 真的**发起过**的那几跳，按顺序。它替代了「当前 URL」那种写法：后者在
        # 进入第 k 轮时已经含着一个还没发出去的地址，于是「被拒的那一跳不在链上」
        # 这句话会被一个简单实现违反，而违反的方式看不出来。被拒 / 解析失败时它
        # 正好停在出事之前；超时或传输错误时它含着自己 —— 那份回执说的就是那一跳，
        # 把它从链上抹掉会让报告少一个地址。
        sent: list[str] = []
        for hop in range(MAX_REDIRECTS + 1):
            verdict = check_target(url)
            if not verdict.allowed:
                return self._refused(request, verdict, started, at=url, sent=sent)

            resolved = self._resolve(url, request, started, sent=sent)
            if isinstance(resolved, FetchResult):
                return resolved

            exchange = self._exchange(request, url, resolved, started, sent=sent)
            if isinstance(exchange, FetchResult):
                return exchange
            response = exchange.response
            if response is None:
                # **一次没有响应对象的交换。** 这里此前是一句 ``assert response is
                # not None``，而它的前提是假的：``_exchange`` 把 ``_Exchange`` 交回来
                # 的条件是「诊断的动作不是 ``DEFERRED_BACKOFF``」，而传输层失败在
                # **同一个来源的第三次**上会打开熔断、``record`` 返回 ``CIRCUIT_OPENED``
                # —— 那也是一次传输层失败，于是这里收到的正是一个 ``response=None``
                # 的交换。现场是 ``AssertionError``，而它把一份**已经量好了的**回执
                # （观测、诊断、处置都在 ``exchange`` 里）换成了一个崩溃。
                #
                # 处置与 ``_exchange`` 里那条传输失败的路**逐字同一条**：交给
                # ``_completed``，它本来就同时认这两支（见它的 docstring）。
                return self._completed(request, exchange, started, at=url, sent=sent)

            if not 300 <= response.status_code < 400:
                return self._completed(request, exchange, started, at=url, sent=sent)

            location = response.headers.get("location", "")
            if not location:
                return self._refused(
                    request,
                    TargetVerdict(
                        False,
                        TargetRefusal.REDIRECT_WITHOUT_LOCATION,
                        f"{response.status_code} 响应没有 Location —— 下一跳无法确定，"
                        "不能当作「页面拿到了」",
                    ),
                    started,
                    at=url,
                    sent=sent,
                )
            if hop == MAX_REDIRECTS:
                return self._refused(
                    request,
                    TargetVerdict(
                        False,
                        TargetRefusal.REDIRECT_LIMIT,
                        f"跟随了 {MAX_REDIRECTS} 次重定向仍未到达终点 —— 实践里这就是环",
                    ),
                    started,
                    at=url,
                    sent=sent,
                )
            # 相对 Location 按**当前这一跳**的地址解析，而不是按最初请求的地址。
            # 基准是 ``url`` 而不是 ``response.url``：后者在钉住之后是那个地址。
            url = str(httpx.URL(url).join(location))
        else:  # pragma: no cover - 循环体在 hop == MAX_REDIRECTS 时必然 return
            raise AssertionError("重定向循环没有终点")

    # ------------------------------------------------------------------
    def _exchange(
        self, request: FetchRequest, url: str, address: str, started: float, *, sent: list[str]
    ) -> _Exchange | FetchResult:
        """这一跳**真的发一次请求**，并把结果记进来源账。

        ## 一个 ``FetchPermit`` 兜一跳，不是一次调用一次

        permit 的界是**这一跳**的重试预算。重定向的下一跳另起一个 permit ——
        一次 302 不是一次失败，让它消耗掉重试预算会让一条长链在最后一跳上报
        「预算用尽」，而那一跳看起来像一次被重试刷满的故障。

        ## 本函数**不 sleep**

        它只问治理层「现在能不能发」。服务端说「等一下」（429 / 503 / 传输层失败）时，
        等待期被记在**来源**上（``OriginState.not_before``），这次访问到此为止 ——
        把一次 ``Retry-After: 300`` 变成一次 300 秒的 ``sleep`` 会占住一个 worker，
        而 §5 要的正是「等待被共享、被看见」（下一次抓取那个来源的调用会撞上它，
        无论是哪个账号发的）。所以同一个调用里再发第二次的场合只有一个：**服务端明说
        「现在就试」**（``Retry-After: 0``，或一个已经过去的 HTTP 日期）。那一格仍然
        有界 —— 一个永远回 ``Retry-After: 0`` 的来源会被 ``max_attempts_per_fetch``
        挡住，而报告上它是一次 ``STOPPED_METHOD``。这正是「有界重试」在本层的含义：
        界不是「重试几次」，而是「连着说现在就试的那个来源，我们最多陪它几次」。
        """
        permit = self._governor.begin(url)
        last: _Exchange | None = None
        sent_this_hop = False

        for _ in range(self._governor.policy.max_attempts_per_fetch):
            admission = permit.admit()
            if not admission.allowed:
                return self._refuse(request, admission, last, started, at=url, sent=sent)

            if not sent_this_hop:
                # 链上记的是**跳**，不是**次**：同一个地址重试两次仍然是同一个地址。
                # 写在放行之后而不是循环之前，是因为「真的发出去了」这句话只有在放行
                # 之后才成立 —— 一次都没放行的那一跳不在链上，与被地址判据拒掉的那一跳
                # 是同一条纪律。
                sent.append(url)
                sent_this_hop = True

            outgoing = _pin(self._client, url, address)
            try:
                response = self._client.send(outgoing, follow_redirects=False)
            except httpx.TimeoutException as exc:
                last = self._transport_exchange(url, exc, FetchOutcome.TIMEOUT)
            except httpx.HTTPError as exc:
                last = self._transport_exchange(url, exc, FetchOutcome.ERROR)
            else:
                last = self._response_exchange(response, url)

            # **判定与处置分开走两条路**：``diagnose``（纯函数）说这一次是什么，
            # ``record`` 把它记进来源账并回答「这一次的处置是不是该改写」。改写只在
            # 治理层知道而诊断不知道的事情上发生（熔断打开、探针闭合、等待期未到）。
            last = replace(last, diagnosis=permit.record(last.diagnosis))
            if last.diagnosis.action is not ActionTaken.DEFERRED_BACKOFF:
                return last

        # 循环自然用尽只发生在「每一次都被放行、每一次都推迟」时，也就是服务端连着说
        # 「现在就试」的那一种。此时再问一次：拿到的一定是预算用尽（拒付**不计数**），
        # 于是最后那次观测带着「不再试了」的处置回去，而不是一份读起来像还要重试的回执。
        admission = permit.admit()
        assert not admission.allowed, (
            "循环用尽之后 permit 必须拒付 —— 预算是它自己的界。一个仍然放行的 permit "
            "会让这个循环停在一个没有结论的状态上（而在那里它只能猜一个动作）"
        )
        return self._refuse(request, admission, last, started, at=url, sent=sent)

    # ------------------------------------------------------------------
    def _response_exchange(self, response: httpx.Response, url: str) -> _Exchange:
        """一次**拿到了响应对象**的答案：观测 → 分类 → 诊断。"""
        content_type = response.headers.get("content-type", "")
        raw = response.content[: self._settings.http_max_bytes]
        truncated = len(response.content) > len(raw)
        text = _decode(raw, content_type)

        outcome, block_reason = assess_http_response(
            status_code=response.status_code,
            content_type=content_type,
            text=text,
        )
        observation = Observation(
            observed_status=response.status_code,
            headers=safe_response_headers(response.headers),
            text=text,
            body_usable=outcome in (FetchOutcome.OK, FetchOutcome.EMPTY_SHELL),
            url=url,
            # 这一跳**没有**跳去别处（重定向由本模块自己跟，见模块 docstring），
            # 所以它落在自己身上。刻意不读 ``response.url`` —— 钉住之后那一格是
            # **地址**，不是域名。
            final_url=url,
        )
        diagnosis = diagnose(observation)

        if outcome in (FetchOutcome.OK, FetchOutcome.EMPTY_SHELL) and not diagnosis.body_may_be_kept:
            # **``cf-mitigated: challenge`` 是一个响应头，而分类器只看状态码与正文。**
            # 一个 200、带那个头、正文里没有挑战指纹的页面，``assess_http_response``
            # 会答 OK —— 于是那一页的正文会作为业务证据落盘。而 §4 逐字：
            # 「Cloudflare challenge/CAPTCHA 正文或对应头 → ``waf_challenge``；
            # **不当网页证据**」。诊断读到了那个头（``Observation.challenge_header``），
            # 所以这里**以诊断为准**：结论改成 ``BLOCKED``，正文一个字节都不落盘
            # （``_completed`` 只镜像 OK / EMPTY_SHELL），也不再有任何升级到浏览器的理由。
            #
            # 这是分类器与诊断之间**唯一**能分叉的地方：正文指纹那一边两份字面量是包含
            # 关系（``domain/access.py`` 的挑战指纹是 ``classify.WAF_MARKERS`` 的子集），
            # 所以正文里的挑战漏不过来。
            outcome = FetchOutcome.BLOCKED
            block_reason = BlockReason.WAF_CHALLENGE

        return _Exchange(
            url=url,
            observation=observation,
            diagnosis=diagnosis,
            response=response,
            outcome=outcome,
            block_reason=block_reason,
            raw=raw,
            text=text,
            content_type=content_type,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    def _transport_exchange(
        self, url: str, exc: httpx.HTTPError, outcome: FetchOutcome
    ) -> _Exchange:
        """一次**没有响应对象**的答案：传输层失败。

        ``observed_status`` 留空，而不是折成一个数：一次没问到状态码的访问不是一次
        ``200``，也不是一次 ``0``。``status_code or 200`` 把三种处境折成同一个读数的
        那一类错，在 ``domain/access.py`` 的 ``Observation`` 上有一个专门的位置 ——
        这里是它的生产者。
        """
        observation = Observation(
            url=url,
            final_url=url,
            transport_error=str(exc),
            transport_error_kind=type(exc).__name__,
        )
        return _Exchange(
            url=url,
            observation=observation,
            diagnosis=diagnose(observation),
            outcome=outcome,
            error=str(exc),
        )

    # ------------------------------------------------------------------
    def _completed(
        self,
        request: FetchRequest,
        exchange: _Exchange,
        started: float,
        *,
        at: str,
        sent: Sequence[str],
    ) -> FetchResult:
        """把一次交换收成回执。**响应与传输失败共用这一条路。**

        共用是有意的：两者的差别只有「有没有状态码与正文」，而 ``reason`` 的拼法、
        耗时、链、诊断都挂在同一个地方 —— 分成两条路时，总有一条会漏掉诊断这一格，
        而漏掉它的表现是那份回执看起来「一切正常」（``diagnosis`` 为 ``None`` 的读法
        见 ``FetchResult`` 那一格）。
        """
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response = exchange.response
        if response is None:
            return FetchResult(
                request=request,
                outcome=exchange.outcome,
                tier=FetchTier.HTTP,
                final_url=at,
                block_reason=None,
                error=exchange.error,
                elapsed_ms=elapsed_ms,
                reason=f"transport:{exchange.outcome.value}",
                redirect_trace=tuple(sent),
                diagnosis=exchange.diagnosis,
            )

        body_path: Path | None = None
        digest = ""
        # 只镜像「有用」的响应体：错误页与拦截页不值得占空间，
        # 但它们的**文本**仍会进 FetchResult.error / reason 供事后判断。
        #
        # 「挑战页的正文不落盘」这条纪律的落点不在这一行，而在
        # ``_response_exchange`` 里那次改写：一个必须扣下正文的响应**先**被改判成
        # ``BLOCKED``，然后才走到这里。写在那一处而不是这里，是因为「要不要留正文」
        # 是诊断答得出的问题（``body_may_be_kept``），而这里只看结论。
        if exchange.outcome in (FetchOutcome.OK, FetchOutcome.EMPTY_SHELL):
            body_path = self._persist(
                request, exchange.raw, content_type=exchange.content_type, url=at
            )
            # 摘要按**解码后文本**算：Windows 与 Linux 对同一页面得到同一 sha256，
            # 而按原始字节算会被换行/编码差异污染。
            digest = sha256_text(exchange.text)

        reason = f"http:{response.status_code}"
        if exchange.truncated:
            reason += f";truncated_at={self._settings.http_max_bytes}"

        return FetchResult(
            request=request,
            outcome=exchange.outcome,
            tier=FetchTier.HTTP,
            final_url=at,
            status_code=response.status_code,
            content_type=exchange.content_type,
            # 观测里**已经洗过**的那些响应头（``_response_exchange`` 构造
            # ``Observation`` 时走的就是 ``safe_response_headers``）。这里与传输失败
            # 那一支共用同一句话：那一支的 ``observation.headers`` 是空字典，于是
            # 上面的 ``response_headers`` 也是空的 —— 而空字典有确切含义（这次没有
            # 任何响应头可带），不是「一个白名单头都没有」。
            #
            # 这一格非有不可，是因为尝试账要在三个月后回答「那天服务端回了什么」，
            # 而一份只留状态码的记录答不出 ``Retry-After: 120`` 与 ``cf-mitigated``
            # 这类**决定过处置**的东西。
            response_headers=dict(exchange.observation.headers),
            body_path=body_path,
            body_sha256=digest,
            body_bytes=len(exchange.raw),
            block_reason=exchange.block_reason,
            elapsed_ms=elapsed_ms,
            reason=reason,
            redirect_trace=tuple(sent),
            diagnosis=exchange.diagnosis,
        )

    # ------------------------------------------------------------------
    def _refuse(
        self,
        request: FetchRequest,
        admission: Admission,
        last: _Exchange | None,
        started: float,
        *,
        at: str,
        sent: Sequence[str],
    ) -> FetchResult:
        """治理层说**不发**。

        ## 有上一次观测时，为什么不是也报一句 ``governed:*``

        「一次 429 之后被等待期拦下」与「一次都没发出去的熔断拒付」是两件事。前者我们
        真的拿到了一份响应 —— 状态码、``Retry-After``、响应头都在；把它丢掉换成一句
        ``governed:deferred_backoff``，报告里那条 URL 就变成一次**没有观测**的失败，
        而排查要看的第一样东西正是「那一跳看到了什么」。所以有观测就带着观测走，只把
        ``action`` 换成治理层那一格：**动作是治理层的判断，观测是这一跳的事实**，
        两者本来就该并排写在同一份回执上。

        ``cause`` 保持诊断那一格而不取 ``admission.cause``：这一份回执说的是**这一跳**，
        而 ``Admission.cause`` 说的是**这个来源的账**。两者今天由同一次 ``record`` 写入
        因而相等（``FetchPermit.admit`` 的预算那一问读的正是它），但让回执的根因跟着
        这一跳的观测走，才不会在某天有人让两本账分开算时静默换一个说法。
        """
        if last is None:
            return self._governed(request, admission, started, at=at, sent=sent)
        detail = "；".join(part for part in (last.diagnosis.detail, admission.detail) if part)
        merged = replace(
            last,
            diagnosis=replace(last.diagnosis, action=admission.action, detail=detail),
        )
        return self._completed(request, merged, started, at=at, sent=sent)

    # ------------------------------------------------------------------
    def _governed(
        self,
        request: FetchRequest,
        admission: Admission,
        started: float,
        *,
        at: str,
        sent: Sequence[str],
    ) -> FetchResult:
        """**一个字节都没发出去。** 熔断打开、半开探针用完、或第一次 ``admit``
        就撞上等待期时走这一条。

        ``BLOCKED`` 而不是 ``ERROR``：这是一个**终态**判断（``TERMINAL_OUTCOMES``
        含它），于是不会有任何一层把它当成「再试一次或许就好了」—— 升级到浏览器尤其
        不行，那等于对一个我们已经决定先放一放的来源多发请求。``block_reason`` 是
        ``UNKNOWN``：这一跳没有观测，所以没有任何一条细分理由成立，而 ``UNKNOWN``
        正是那句诚实的回答（见 ``AccessDiagnosis`` 对 ``UNKNOWN`` 的说明）。

        ``final_url`` 是**本来要去**的那个地址，``body_path`` 是 ``None``：
        「我们连问都没问」与「问了被拒」在这一格上必须能分开。
        """
        return FetchResult(
            request=request,
            outcome=FetchOutcome.BLOCKED,
            tier=FetchTier.HTTP,
            final_url=at,
            body_path=None,
            block_reason=BlockReason.UNKNOWN,
            error=admission.detail,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            reason=f"governed:{admission.action.value}",
            redirect_trace=tuple(sent),
            diagnosis=AccessDiagnosis(
                cause=admission.cause,
                action=admission.action,
                detail=admission.detail,
            ),
        )

    # ------------------------------------------------------------------
    def _resolve(
        self, url: str, request: FetchRequest, started: float, *, sent: Sequence[str]
    ) -> str | FetchResult:
        """解析 + 判断。通过时返回**要连上去的那个地址**，否则返回一份拒绝结果。

        返回一个联合类型而不是抛异常：两条路都是正常结果，而异常的路径会让
        「拒绝」与「解析失败」在调用方的 ``except`` 里合并 —— 它们的处置相反
        （前者终态、后者可退避重试）。
        """
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORT.get(parts.scheme.lower(), 443)
        try:
            addresses = self._resolver.resolve(host, port)
        except DnsResolutionError as exc:
            return self._dns_failure(request, exc, started, at=url, sent=sent)

        verdict = check_resolved(host, addresses)
        if not verdict.allowed:
            return self._refused(request, verdict, started, at=url, sent=sent)
        return addresses[0]

    def _dns_failure(
        self,
        request: FetchRequest,
        exc: DnsResolutionError,
        started: float,
        *,
        at: str,
        sent: Sequence[str],
    ) -> FetchResult:
        """解析失败**不是**一次策略拒绝 —— 所以它不是 ``BLOCKED``。

        ``BLOCKED`` 说「我们不允许自己去那里」，而这里的事实是「这一次没问到」
        或「这个名字不存在」。合成一栏的后果是：一个暂时解析不出的域名会被记成
        「这个目标不合规」，而它本该被有界退避后再试一次。

        ``reason`` 里带上三种失败中的哪一种（``dns:nonexistent`` / ``dns:timeout``
        / ``dns:failure``）：下游按它决定「这个来源可能没了」还是「再试一次」。

        ## 诊断：三种失败对应 ``ports/dns.py`` 已经写下的两句话

        那个端口自己把这件事说清楚了 —— 「``NONEXISTENT`` 说的是**这个来源可能
        没了**，``TIMEOUT`` / ``FAILURE`` 说的是**我们这次没问到**。前者往
        ``SOURCE_GONE`` 走，后者往「有界退避后再试」走」。这里只是把那张表落成
        一个诊断，而不是重新判断一次。

        此前这一支**没有诊断**（``diagnosis`` 为 ``None``），于是它收成的尝试账
        会写着 ``cause=none, action=proceeded`` —— 一句「这一次没有任何异常、
        正常继续」。一次解析失败被记成一切正常，正是 §5 要消灭的那种读数。
        """
        if exc.kind is DnsFailureKind.NONEXISTENT:
            cause, action = SuspectedCause.SOURCE_GONE, ActionTaken.STOPPED_METHOD
        else:
            cause, action = SuspectedCause.NETWORK_UNKNOWN, ActionTaken.DEFERRED_BACKOFF
        return FetchResult(
            request=request,
            outcome=FetchOutcome.ERROR,
            tier=FetchTier.HTTP,
            final_url=at,
            block_reason=None,
            error=str(exc),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            reason=f"dns:{exc.kind.value}",
            redirect_trace=tuple(sent),
            diagnosis=AccessDiagnosis(cause=cause, action=action, detail=str(exc)),
        )

    # ------------------------------------------------------------------
    def _persist(
        self, request: FetchRequest, payload: bytes, *, content_type: str, url: str
    ) -> Path:
        """把这次抓到的字节落盘，并返回**装着这些字节**的那个文件的路径。

        这一层只负责挑扩展名（``_extension_for`` 知道 ``content-type`` 与 URL 尾巴
        说的是什么）。**写下去的判据只在 ``page_mirror.store_page_bytes`` 一处**：
        同 URL 重复抓到同样的字节不重复写、内容变了落到第二个文件上。理由写在
        那里，而它曾经在本文件与浏览器那一侧各写了一遍 —— 两个答案不同
        （浏览器那侧无条件覆盖），错的不是措辞。
        """
        return store_page_bytes(
            self._settings,
            scope=request.scope,
            url=url,
            payload=payload,
            extension=_extension_for(content_type, url),
        )

    def _refused(
        self,
        request: FetchRequest,
        verdict: TargetVerdict,
        started: float,
        *,
        at: str,
        sent: Sequence[str],
    ) -> FetchResult:
        """判据拒绝了这一跳。

        ``BLOCKED`` 而不是 ``ERROR``：这不是一次传输故障，重试同样会被拒。
        ``UNSAFE_TARGET`` 而不是 ``UNKNOWN``：报告要能回答「为什么没抓到」，
        而 ``UNKNOWN`` 会把「我们不允许自己去那里」与「那个站点出了问题」
        混成一栏。

        ``body_path`` 一定是 ``None`` —— 一次被拒绝的抓取不得留下镜像，
        否则「抓到了但标记为拒绝」会在审计时节外生枝。

        ``sent`` 是**真的发起过**的那几跳（不含被拒的这一跳）。它在重定向之后
        的拒绝上不是空的，而那正是它存在的理由：只写一个空链会让「我们问了
        example.co.uk，它把我们指向一个解析到内网的名字，我们拒了」读成
        「我们什么都没问就拒了」。

        ## 诊断：``block_reason`` 只有一格，而拒绝有九种

        ``block_reason`` 恒为 ``UNSAFE_TARGET``（那是**这一类结果**的名字），
        所以它答不出「是哪一种拒绝」。而两种拒绝的处置不同：地址不安全要记下
        这个目标并改来源，重定向类拒绝要修的是这一条链。所以诊断按
        :data:`~marketops.adapters.fetch_attempts.UNSAFE_REFUSALS` 与
        :data:`~marketops.adapters.fetch_attempts.UNROUTABLE_REFUSALS` 分开，
        ``cause_evidence_ref`` 留空 —— 一次地址判据拒绝的证据就是 ``reason`` 里
        那个理由码，不是一份外部凭据。

        此前这一支**没有诊断**，于是一次被地址判据拦下的抓取在尝试账上会写成
        ``cause=none, action=proceeded``。那是最不该出错的一格：§5 要记下来的
        正是**没有产出证据的那些尝试**，而它们本该是这张账上最清楚的一批。
        """
        refusal = verdict.refusal
        assert refusal is not None, "拒绝必须有理由码"
        cause, action = refusal_diagnosis(refusal)
        return FetchResult(
            request=request,
            outcome=FetchOutcome.BLOCKED,
            tier=FetchTier.HTTP,
            final_url=at,
            body_path=None,
            block_reason=BlockReason.UNSAFE_TARGET,
            error=verdict.detail,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            reason=f"target_refused:{refusal.value}",
            redirect_trace=tuple(sent),
            diagnosis=AccessDiagnosis(cause=cause, action=action, detail=verdict.detail),
        )


def _pin(client: httpx.Client, url: str, address: str) -> httpx.Request:
    """把这一跳钉在 ``address`` 上，并把身份恢复成原来的名字。

    三处改写，缺一不可：

    * **URL 的主机名**换成地址 —— 于是 ``httpcore`` 连的就是它，不会再解析一次。
    * **``Host`` 头**换回原来的名字（带非默认端口时带上端口）—— 虚拟主机与
      ``httpcore`` 都按它选站点。
    * **``extensions["sni_hostname"]``** 设成原来的名字 —— ``httpcore`` 用它作为
      TLS 的 ``server_hostname``，证书校验因此仍对着域名做。

    用 ``client.build_request`` 而不是 ``httpx.Request(...)``：前者把客户端级的
    默认头（User-Agent 等）合并进来，而后者不会 —— 换了之后站点看到的 UA 会变，
    而这件事在测试里与生产上都不报错。
    """
    original = httpx.URL(url)
    host = original.host
    port = original.port or _DEFAULT_PORT.get(original.scheme, 443)

    literal = f"[{address}]" if ":" in address else address
    authority = literal if port == _DEFAULT_PORT.get(original.scheme) else f"{literal}:{port}"
    pinned = urlunsplit(
        (
            original.scheme,
            authority,
            original.raw_path.decode("ascii", errors="surrogateescape"),
            "",
            "",
        )
    )
    request = client.build_request("GET", pinned)
    request.headers["Host"] = host if port == _DEFAULT_PORT.get(original.scheme) else f"{host}:{port}"
    request.extensions["sni_hostname"] = host
    return request


def _decode(raw: bytes, content_type: str) -> str:
    """宽松解码。

    不依赖平台默认编码；显式 UTF-8 优先，失败则替换错误字节 ——
    「解码失败」不应该让一次本可成功的抓取变成失败。
    """
    if "pdf" in content_type.lower():
        return ""  # PDF 交给 DocumentParser，不在这里当文本解
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


__all__ = ["HttpxFetcher"]

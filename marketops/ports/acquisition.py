"""统一抓取接口。

三方契约，缺一不可：

1. ``HttpFetcher`` 与 ``BrowserFetcher`` 返回**同一个** ``FetchResult`` 类型，
   使二者可互换、可比较、可审计。
2. 是否升级到 browser 只读 ``FetchOutcome``（见 ``escalation.decide``），
   不看 URL 形态、不看域名 —— 启发式猜测会让「HTTP 成功却仍启动浏览器」复活。
3. ``BLOCKED`` 是终态。它表示目标站点明确拒绝公开访问，处置是改走其他公开来源。

Browser 使用**匿名公开浏览**模式。允许 JS / cookie / localStorage / 普通 session
state / cache；禁止登录目标站点、访问 private content、CAPTCHA 绕过、突破 access control。

实现（HTTP 直取 / Crawl4AI）在 ``marketops/adapters/``（``http.py`` /
``crawl4ai_fetcher.py``）。本模块只定义契约与层级常量，**不得**出现任何厂商名。

## §10 的统一入口为什么**不是**在这里再加一个 ABC

``FetchMode`` / ``DocumentFetch`` / ``mode_allows`` 住在本模块，因为它们是被多方
读的词汇（服务面要按 mode 名生成工具参数，编排层要按 mode 分派）。但**统一入口
本身**是一个具体类（``application/acquisition.py::DocumentAcquisition``），不是
第四个端口，理由是本包文件头自己订的那条纪律：**等第二个实现出现再提取**。

今天 ``fetch_document`` 只有一个实现 —— 「HTTP + 浏览器 + 升级判据」这件事只有
一种正确做法。为它写一个 ABC，产出的是一个永远只有一个子类的抽象：它不增加
可替换性（没人可换），只增加一层需要同步维护的签名。

这条纪律有一个已成立的对照：``ports.store`` 与 ``ports.budget`` 都是**第二个
实现出现时**才提取的（导出通道 + 测试替身），两组实现跑同一份契约测试。抓取这
一层要等到出现第二个编排方式（例如将来的 ``crawl_site`` 复用同一个 service，或
一个只做离线的重放实现），那时提取才有内容。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from ..domain.access import AccessDiagnosis
from ..domain.budget import check_reservation_id
from ..domain.enums import BlockReason, FetchOutcome, FetchTier
from ..domain.fetch_attempt import MaterialProvenance
from ..domain.util import check_canonical_id


@dataclass(frozen=True)
class FetchRequest:
    """一次抓取请求。

    ``scope`` 是这次抓取**记在谁的名下**，也是页面镜像的目录名，因此它必须
    **已经是**文件名（``check_canonical_id``）。空值是允许的，而且它有一个明确的
    含义：**这次抓取不属于任何主体**（镜像落进 ``unnamed``）—— 它不是「随便哪个
    账号」，所以不需要为它编一个 id。非空时不规范就抛，且抛在**发请求之前**。

    名字是 ``scope`` 而不是 ``account_id``：与
    :class:`~marketops.ports.search.SearchQuery` 同一处置、同一条理由（那一格在
    ``application/discovery_run.py`` 上收下的是**一轮运行的名字**，不是账号），
    完整论证见那边的 docstring。这里只补一件它没有的事：这一格的值会**逐字**成为
    ``Evidence.account_id``（见 ``adapters/http.py`` 与 ``adapters/crawl4ai_fetcher.py``）
    —— 于是「这一页记在谁名下」与「这次抓取记在谁名下」在盘上是同一个字符串，
    而不是两处各自算一遍。两处各算一遍的那一天，一条证据会归到一个没有那次抓取的
    主体上，而报告上只显示「这次运行什么都没抓到」。
    """

    url: str
    scope: str = ""
    purpose: str = "page"
    #: 这一次抓取**押着的那一份预留**（本账本里的一条，``RunBudget.reserve`` 的返回值）。
    #: 空串 = 这一次抓取与预算没有关系，而它是一个**明确的**含义，不是「还没填」：
    #: 抓取这一层今天不消费任何计费额度，所以绝大多数调用方（``discovery_run`` /
    #: ``pipeline`` / ``site_crawl`` / ``sitemap_frontier`` / MCP 的 ``research``）
    #: 本来就该留空 —— 留空不是漏了一格，它们真的没有押着什么。
    #:
    #: ## 为什么它在请求上，而不是多一个参数
    #:
    #: 它是**这一次抓取的性质**，与 ``scope``／``purpose`` 同一侧：这次请求记在谁
    #: 名下、为了什么、押着哪一份额度。放在请求上还买到两条：
    #:
    #: * ``DocumentAcquisition.fetch_document`` 把**同一个请求对象**交给直连与浏览器
    #:   两条路（见下面 ``FetchResult`` 的说明），于是「一次 auto 抓取的两个抓取器
    #:   押的是同一份额度」是**同一个对象**保证的，不需要在两条路径上各传一次。
    #: * 加参数的话要改的是 ``Fetcher`` 协议与每一个实现、每一个测试替身 —— 而
    #:   今天真的会用它的只有 ``refresh._recheck_page`` 一处。
    #:
    #: ## 为什么它过形状判据，而 ``FetchAttempt`` 那一格不过
    #:
    #: 这里记的是**我们自己的账本**里那一条，来源只有 ``reservation_id()`` 一个；
    #: 而落盘那一格允许是一份供应商的引用。理由写在
    #: ``domain/budget.py::check_reservation_id`` 上。
    reservation_id: str = ""

    def __post_init__(self) -> None:
        if self.scope:
            check_canonical_id(self.scope, "FetchRequest.scope")
        if self.reservation_id:
            check_reservation_id(self.reservation_id, "FetchRequest.reservation_id")


@dataclass(frozen=True)
class FetchResult:
    """一次抓取的完整结果。

    ``body_path`` 指向落盘镜像；``body_sha256`` 让证据可以指回**具体字节**，
    而不是「某个 URL 当时的某个版本」。
    """

    request: FetchRequest
    outcome: FetchOutcome
    tier: FetchTier
    final_url: str = ""
    status_code: int | None = None
    content_type: str = ""
    #: 这一跳响应头里**过了白名单**的那些（``domain/access.py::safe_response_headers``）。
    #:
    #: 为什么是这一小队而不是整份响应头：设计 §3 逐字要求「原始敏感
    #: headers/cookies/key/IP 不出报告」，而白名单在 ``domain/access.py`` 一处定义。
    #: 这一层的取值来自那里，落盘的 ``FetchAttempt.response_headers`` 也取自那里
    #: （并且**再洗一遍** —— 见那个模型的 ``_only_safe_headers``）。
    #:
    #: 为什么它要在这里，而不是让 ``FetchAttempt`` 的构造者自己去量：一次尝试的
    #: 观测**只存在于这次调用里**。两个抓取器各有一个收口点（``HttpxFetcher``
    #: 从响应对象取，``Crawl4AIBrowserFetcher`` 从库的结果对象取，
    #: ``adapters/crawl4ai_fetcher.py::_response_headers_of``），函数一返回就再也
    #: 拿不到那个对象了；而事后从别处重建响应头是不可能的 —— 那已经不是那一次的
    #: 观测了。
    #:
    #: 空字典有一个明确的含义：**这次没有任何响应头可带**（被拒付、DNS 失败、
    #: 传输层失败 —— 根本没有响应对象）。它**不是**「响应里一个白名单头都没有」，
    #: 后者会得到一个非空但可能只含 ``content-type`` 的字典。两者的区别与
    #: ``observed_status`` 的 ``None`` 是同一件事：没有值就是没有值。
    response_headers: dict[str, str] = field(default_factory=dict)
    body_path: Path | None = None
    body_sha256: str = ""
    body_bytes: int = 0
    block_reason: BlockReason | None = None
    error: str = ""
    elapsed_ms: int = 0
    #: 为什么是这个结果（含升级来源），审计用。
    reason: str = ""
    #: 这一跳之后跟过的**每一跳地址**，按顺序；第一项是最初请求的那个 URL。
    #: 只有真的**发起过**的那几跳在链上 —— 被拒的那一跳不在（它没发生），而
    #: 超时或传输错误的那一跳**在**（这份回执说的就是它，抹掉它会少一个地址）。
    #:
    #: 采集设计 §3 把 redirect trace 列为 ``FetchAttempt`` 的一格（「URL/finalURL/
    #: redirect trace」）。它在这一层就产生了，所以在这里带上，而不是让下游从
    #: ``final_url`` 反推 —— 后者答不出「中间经过谁」，而那正是审计一次
    #: 「这个 URL 怎么变成那个域名的」要看的东西。
    #:
    #: 浏览器那一层只放得进**我们请求的那一跳**：https 是端到端握手的隧道，
    #: 出口看得见「连了哪个 host」，看不见隧道里那个 URL 走到了哪里
    #: （见 ``adapters/egress_proxy.py`` 与 ``adapters/crawl4ai_fetcher.py`` 的
    #: 模块 docstring）。所以浏览器下出现「链长 1，而 ``final_url`` 已经是别的
    #: 地址」不是矛盾 —— 中间那几跳不在链上，因为那一层看不见它们，
    #: 而把它们补出来会是编造。
    redirect_trace: tuple[str, ...] = ()
    #: 浏览器那一层里**被受管出口拒绝、因而从未发出**的那些目标（``host:port``）。
    #:
    #: 直连那一层不填这一格：它的每次拒绝都**是一次结果**（``outcome=BLOCKED``），
    #: 而一次浏览器渲染里可能有好几次拒绝，那些都不是这次抓取的结果 —— 它们是
    #: **这一页想做而没做成的事**。把它们抹掉会让「渲染出来是空的」与「它想连的
    #: 东西被我们挡了」在报告上同形，而这两句的处置完全不同。
    #:
    #: 空元组不等于「这一层没有受管出口」：那件事由 ``BrowserFetcher.constrains_egress``
    #: 单独回答（见 ``ports/acquisition.py`` 里那个属性与组合根的判据）。
    egress_refusals: tuple[str, ...] = ()
    #: 本结果是由哪次 HTTP 结果触发的升级；HTTP 直取时为 None。
    escalated_from: FetchOutcome | None = None
    #: 这一次访问的**诊断**：疑似根因 + 我们的处置 + 人话（``domain/access.py``）。
    #:
    #: 与 ``outcome`` 分开，理由与那个模块里「观测与根因分开」是同一条：``outcome``
    #: 答的是「拿到东西了吗」（升级决策按它走，闭集很小），``diagnosis`` 答的是
    #: 「为什么没拿到、这一次我们怎么处置的」。一次 403 与一次 WAF 挑战在 ``outcome``
    #: 上都是 ``BLOCKED``，而它们的处置**不同** —— 前者保存诊断、走合法替代来源，
    #: 后者的正文**不得**进入业务证据。
    #:
    #: ``None`` 表示**这一层没有产生诊断**，而不是「这一次没有异常」。后者是
    #: ``diagnosis.cause is SuspectedCause.NONE`` 那一句判断。两者必须分得开，
    #: 而分开它们靠的是**这一格由生产者保证非空**：两个抓取器（``HttpxFetcher``
    #: 与 ``Crawl4AIBrowserFetcher``）今天每一个出口都带诊断，走到收账那一步
    #: 还见 ``None`` 就是有人加了一条不带的出口 —— ``adapters/fetch_attempts.py::require_diagnosis``
    #: 会为此报错，而不是把它折成 ``cause=none, action=proceeded``。
    #:
    #: 类型保留可空（而不是收紧成必填）也是刻意的：``None`` 在这个联合里有确切
    #: 含义，把它从类型里拿掉，那个含义就只能靠约定维持 —— 而约定在下一个
    #: ``FetchResult(...)`` 字面量上不会报错。
    diagnosis: AccessDiagnosis | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is FetchOutcome.OK

    @property
    def blocked(self) -> bool:
        return self.outcome is FetchOutcome.BLOCKED


class Fetcher(ABC):
    """抓取器基类。"""

    name: str = "abstract"
    tier: FetchTier = FetchTier.HTTP

    @abstractmethod
    def fetch(self, request: FetchRequest) -> FetchResult:
        """抓取单个 URL。

        实现**不得**抛 ``AccessBlockedError`` 之外的网络异常到调用方 ——
        传输失败应被表达为 ``FetchOutcome.TIMEOUT`` / ``ERROR`` 的 ``FetchResult``，
        以便 pipeline 统一处理与统计。
        """

    @property
    def available(self) -> bool:
        """运行依赖是否就绪（如 crawl4ai / 浏览器二进制）。"""
        return True

    #: 这个实例拿到的正文**是从哪来的**（现场 / 我们自己造的）。
    #:
    #: 这是**实例**属性而不是类属性，而且它必须是：同一个 ``HttpxFetcher`` 类在
    #: 生产里抓真实的第三方页面，在测试里被注入一个 ``httpx.MockTransport`` 与一个
    #: 假解析器 —— 而**类别相同、材料不同**。写成类属性或从 URL 形状反推，都会让
    #: 一份自造页面读成现场覆盖，而报告上看不出那一天。
    #:
    #: 所以声明的落点是**构造这一个实例的那一处**：真出网的组合根写 ``LIVE``
    #: （见 ``interfaces/composition.py`` 的 ``default_http`` / ``default_acquisition``），
    #: 替换了网络的那一处**什么都不写**，于是它落在缺省的 ``SYNTHETIC`` 上。
    #: 两条判据各钉一半：``tests/test_material_provenance.py`` 断言组合根那两个实例
    #: 是 ``LIVE``，以及一个裸构造的实例是 ``SYNTHETIC``。
    #:
    #: 缺省为什么是 ``SYNTHETIC``（而不是反过来的「缺省 live」）写在
    #: :class:`~marketops.domain.fetch_attempt.MaterialProvenance` 上：
    #: ``LIVE`` 是一条**肯定声明**，漏写时覆盖率被低报，而不是被高报。
    material: MaterialProvenance = MaterialProvenance.SYNTHETIC


class HttpFetcher(Fetcher):
    """直接 HTTP 抓取。默认层级，绝大多数目标应当停在这里。"""

    tier = FetchTier.HTTP


class BrowserFetcher(Fetcher):
    """浏览器渲染抓取。仅在 ``escalation.decide`` 允许时被调用。"""

    tier = FetchTier.BROWSER

    #: 本实现能不能**在请求发出之前**约束浏览器会发的全部请求（主导航、重定向、
    #: iframe / worker、子资源），并把连接钉在判过的地址上。
    #:
    #: 采集设计 §7 逐字：「所有URL（初始、重定向、浏览器主导航、iframe/worker请求
    #: 和子资源）在网络动作前检查……DNS解析与实际连接绑定」，而紧接着的那一句是
    #: 「若所锁浏览器接口不能在发送前拦截全部必要请求，组合根需采用可验证的受管
    #: 网络层/拦截器，或禁用该模式并明确阻塞」。
    #:
    #: 所以这一格不是一句自夸，它是**组合根读的那个输入**：``default_acquisition``
    #: 只在它为真时才把浏览器接上，为假时它在构造期就抛（``interfaces/composition.py``
    #: 里那段）。把这件事写在端口上而不是每个实现自己心里，是因为
    #: 「接了，但只能在事后检查」正是 §7 明令不许当成安全的那一种 ——
    #: 一个小写漏的类属性会让它悄悄发生。
    #:
    #: ``tier`` 是一个**值**（所有实现都一样），这一格是**每个实现自己的事实**。
    #: 它不叫 ``safe``：那是个形容词，读的人没法从它推出「它对什么做了约束」。
    constrains_egress: bool = False


# --- §10 的统一入口：mode 与回执 --------------------------------------------


class FetchMode(str, Enum):
    """§10 的三个 mode。取值的字符串就是工具参数里的那个词。

    三者的区别是**谁来决定层级**：

    ==============  ==========================================================
    ``auto``        ``auto`` 由 ``application/escalation.decide`` 决定 ——
                    HTTP 成功就不开浏览器。
    ``http``        调用方指定只用直连。不满足就是那个结果，**不升级**。
    ``browser``     调用方指定用浏览器。不发那次直连请求。
    ==============  ==========================================================

    ``http`` 与 ``browser`` 不是「auto 的两个快捷方式」，它们**取消了**那条
    升级判据：说只用 HTTP 的调用方要的是一个便宜的探测结果，而不是一个被
    悄悄升级过的页面。所以回执里必须写明是 mode 拦下了升级（见
    :class:`DocumentFetch` 的 ``decision``）—— 否则「没升级」与「升不了级」
    在报告上同形。
    """

    AUTO = "auto"
    HTTP = "http"
    BROWSER = "browser"


def mode_allows(mode: FetchMode, tier: FetchTier) -> bool:
    """这个 mode 允不允许走这个层级。纯函数，2 × 2 + ``auto`` 两行就是全表。"""
    if mode is FetchMode.AUTO:
        return True
    return (mode is FetchMode.HTTP) == (tier is FetchTier.HTTP)


@dataclass(frozen=True)
class DocumentFetch:
    """一次 ``fetch_document`` 的完整回执。

    ``attempts`` **保留走过的每一次**而不只留最终结果。这是回执存在的理由：
    HTTP 那一次是 ``http_js_required`` / ``http_empty_shell`` 这些计数器唯一的
    来源；只回最终结果，会让「先 JS_REQUIRED 再浏览器成功」在报告上读成
    「HTTP 一次都没失败过」—— 而那个数字正是「这个站该不该进浏览器」的答案。

    ``decision`` 是升级与否的**理由**（``escalation.decide`` 的 ``reason``，
    或 mode 拦下升级时的那一句）。它是一个字符串而不是布尔：``False`` 会把
    「HTTP 成功所以不需要」与「目标是终态所以不该」与「调用方说了只用 HTTP」
    三件事合成一个。

    ``browser_seconds`` 由编排层测，不从 ``elapsed_ms`` 反推 —— 两者不是同一个
    量，理由写在 ``application/acquisition.py``。
    """

    request: FetchRequest
    mode: FetchMode
    final: FetchResult
    attempts: tuple[FetchResult, ...] = ()
    decision: str = ""
    browser_seconds: float = 0.0


__all__ = [
    "DocumentFetch",
    "FetchMode",
    "FetchRequest",
    "FetchResult",
    "Fetcher",
    "HttpFetcher",
    "BrowserFetcher",
    "mode_allows",
]

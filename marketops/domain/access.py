"""访问故障的**观测**与**根因**：两件事，两个字段，一条不许互相冒充的纪律。

采集设计 §3 逐字：

    观测和根因分开：``observed_status=403``、``challenge_detected=true``、
    ``suspected_cause=ip_policy``、``cause_evidence_ref=...``。没有证据时是
    unknown，不因为某页有 Cloudflare 就填「出口IP封禁」。

这句话里有两个不同的东西，本模块把它们分开：

* :class:`Observation` —— **量到了什么**。状态码、有没有 ``cf-mitigated``、
  正文里有没有挑战指纹、传输层报了什么错。它是事实，可复现，不含猜测。
* :class:`AccessDiagnosis` —— **我们据此认为发生了什么**。它是判断，因此
  必须带一句人话、一个闭集里的取值，以及**在需要时**的证据引用。

## 为什么 ``ip_policy`` 在枚举里，却没有任何一条代码路径能产出它

设计 §4 列了一张表，其中一行是「HTTP 403 且根因不明 → ``access_denied_unknown``，
保存诊断，有限人工/运维复核及合法替代来源」，不应做的是「把任何 403 写
``ip_banned``」。同一份设计 §4 又说明：**Ray ID 不是封 IP 证明** —— 官方资料
只把它定义为「关联经过 Cloudflare 的请求」的标识，普通请求也可能带 ``cf-ray``；
403 可以由源站权限规则或 Cloudflare 触发，多种规则都成立。

于是本模块的处置不是「写一条注释提醒大家别猜」，而是让猜测**无法表达**：

* :func:`diagnose` 是唯一的机器判定入口，它的返回集合里**不含** ``IP_POLICY``。
* ``IP_POLICY`` 只能由 :func:`with_cause_evidence` 产生，而那个函数**要求**
  一个非空的 ``cause_evidence_ref``（站点方的匹配规则/日志、明确错误说明、
  或批准的诊断证据），且它的 docstring 写明调用者是谁。

这一条的形状与本仓 ``DecisionMode`` 里**没有** ``automated_acceptance`` 是同一个：
一个不存在的取值，比一个「存在但没人敢用」的取值可靠 —— 后者会在某次赶时间的
改动里被用上。差别是这里它仍然需要被**表达**（人工确实可能确认它），所以它是
一个需要额外凭据才能抵达的取值，而不是一个不存在的取值。

## 观测与业务证据之间还有第三条纪律

设计 §4 的表里，「Cloudflare challenge/CAPTCHA 正文或对应头 → ``waf_challenge``；
**不当网页证据**，暂停此方法」。这条纪律的落点不在本模块（本模块只产出诊断），
但它决定了 :attr:`ActionTaken.WITHHOLD_BODY` 这个取值存在：一个 ``waf_challenge``
的正文**不得**进入 :class:`~marketops.domain.evidence.Evidence`。抓取层据此
把 ``body_path`` 留空。

## 为什么 ``Retry-After`` 也要在这里解析

设计 §4：「429 + 可用 ``Retry-After`` → 按等待期 + 有界退避，**共享** origin/
credential 限流」。这个头有两种合法写法（秒数、HTTP 日期），而两种写法的解析
差别正是一个**静默错**的高发处：把 ``Retry-After: 120`` 当成日期解析会得到
``None``，于是「服务端说了等两分钟」被读成「服务端没说」—— 后者会立刻重试。
所以解析函数是显式的、可穷举测试的，且**解析不出来就是 ``None``**，不猜。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Mapping

__all__ = [
    "AccessDiagnosis",
    "ActionTaken",
    "CHALLENGE_HEADERS",
    "Observation",
    "RETRY_AFTER_MAX_SECONDS",
    "SuspectedCause",
    "diagnose",
    "parse_retry_after",
    "safe_response_headers",
    "with_cause_evidence",
]


class SuspectedCause(str, Enum):
    """**疑似**根因的闭集。

    每一个成员对应采集设计 §4 那张表里的一行「诊断与默认动作」。取值的粒度就是
    那一行的粒度：多一个成员等于多一条没人写下来的处置，少一个成员会让两种不同
    的处置挤进同一格。

    刻意**没有** ``ip_banned`` / ``asn_blocked`` 这类「封禁边界」的取值 ——
    见模块 docstring。它们唯一合法的表达是 :attr:`IP_POLICY` 配证据引用，
    而 :attr:`IP_POLICY` 不由任何机器判定产出。
    """

    #: 这一次没有任何异常。它是默认值，也是「正常公开页面」那一行的结论。
    NONE = "none"
    #: 有异常，但没有任何证据把它归到下面任何一类。**这是诚实的答案**，
    #: 不是「还没分析完」—— 设计要求没有证据时就是 unknown。
    UNKNOWN = "unknown"
    #: DNS/TCP/TLS 失败，或明确的本地出网错误。处置是检查自有主机配置与共享健康，
    #: **不是**推断网站不存在或无联系人。
    NETWORK_UNKNOWN = "network_unknown"
    #: 出口在**我们这一侧**就被拒了（受管网络层/代理策略）。与 ``NETWORK_UNKNOWN``
    #: 分开，因为「我们的出口策略拦了」与「网线那边不通」要改的东西不同。
    EGRESS_DENIED = "egress_denied"
    #: 站点或 robots 政策拒绝。处置是记规则/版本、转允许来源或请求许可。
    POLICY_BLOCKED = "policy_blocked"
    #: 401 / 登录页 / 订阅权限要求。本阶段匿名采集在此停止该路径。
    AUTH_REQUIRED = "auth_required"
    #: 403，且根因不明。**这是 403 的默认结论**，不是 ``IP_POLICY``。
    ACCESS_DENIED_UNKNOWN = "access_denied_unknown"
    #: Cloudflare challenge / CAPTCHA 正文或 ``cf-mitigated: challenge``。
    #: 正文不得进入业务证据。
    WAF_CHALLENGE = "waf_challenge"
    #: 429（或带 ``Retry-After`` 的限流响应）。按等待期 + 有界退避，共享 origin 限流。
    RATE_LIMITED = "rate_limited"
    #: 503 / 短期超时 / 断线。按策略有限重试，未知计费先对账。
    TRANSIENT_OR_UNKNOWN = "transient_or_unknown"
    #: 404/410 或域迁移线索。**不推出**「整个组织消失」。
    SOURCE_GONE = "source_gone"
    #: 拿到了 2xx，但正文是挑战页/错误页/占位。不得进入业务证据。
    CONTENT_UNUSABLE = "content_unusable"
    #: 目标地址不在本进程允许访问的范围内（``security/network.py`` 的判据）。
    #: 处置是记录并放弃这个目标 —— 改来源、换 IP、换 UA 都不适用。
    UNSAFE_TARGET = "unsafe_target"
    #: 出口 IP/ASN 层面的策略拒绝。**只能由带证据的显式判定产生**
    #: （:func:`with_cause_evidence`）；:func:`diagnose` 永远不产出它。
    IP_POLICY = "ip_policy"


class ActionTaken(str, Enum):
    """**这一次我们做了什么**。与根因分开，因为同一个根因的处置会随 policy 变。

    设计 §4 那张表的第三列（「不应做」）不是这里的取值 —— 那是一组**不存在**的
    动作（求解 CAPTCHA、指纹伪装、暗换出口、换 IP），它们在本产品里没有实现，
    因此也没有名字。有名字的只有做过的。
    """

    #: 正常继续。
    PROCEEDED = "proceeded"
    #: 升级到浏览器渲染（``application/escalation.decide`` 允许时）。
    ESCALATED_TO_BROWSER = "escalated_to_browser"
    #: 按 ``Retry-After`` / 有界退避推迟，**不**立即重试。
    DEFERRED_BACKOFF = "deferred_backoff"
    #: 来源级熔断被打开，这个方法暂时不再发请求。
    CIRCUIT_OPENED = "circuit_opened"
    #: 熔断半开时发出的**有界探针**。探针数量有上限，且不是绕封禁。
    HALF_OPEN_PROBE = "half_open_probe"
    #: 停用这个方法/来源，改走其他获准来源。
    STOPPED_METHOD = "stopped_method"
    #: 这一跳被本进程的地址判据拒绝，请求**没有发出**。
    REFUSED_UNSAFE_TARGET = "refused_unsafe_target"
    #: 正文不可用，**不落盘、不入证据**。
    WITHHOLD_BODY = "withhold_body"
    #: 生成了待人工/运维处置的访问复核项。
    REVIEW_REQUESTED = "review_requested"


#: 能**明确**指认标准 Challenge Page 的响应头（Cloudflare 官方文档 CF2）。
#: 值为 ``challenge`` 时是明确观测；**缺少这个头不等于没有其他拦截**，
#: 所以它是充分条件，不是必要条件（正文指纹仍然独立生效）。
CHALLENGE_HEADERS: tuple[str, ...] = ("cf-mitigated",)

#: ``Retry-After`` 最多认到这么久。超过它的一律当作「服务端没有给一个我们愿意等的
#: 时间」—— 一个 ``Retry-After: 86400`` 在语义上等于「今天别来了」，把它当成
#: 一次 24 小时的 sleep 只会让 worker 挂在那里，而 run 的 deadline 早就过了。
RETRY_AFTER_MAX_SECONDS = 3600

_HTTP_DATE_HINT = re.compile(r"[A-Za-z]{3},")


@dataclass(frozen=True)
class Observation:
    """一次访问**实测到了什么**。只有事实，没有判断。

    所有字段都可以缺 —— 缺是正常的（传输失败时没有状态码），而缺的表示是
    ``None`` / 空串，**不是**一个像 ``0`` / ``200`` 这样的占位值。这条纪律正是
    本切片存在的原因之一：``status_code or 200`` 把三种处境压成同一个数，
    下游于是把它当成一次测量接着走。
    """

    #: HTTP 状态码。**nullable**，读不出来就是 ``None``。
    observed_status: int | None = None
    #: 传输层错误的原文（``timeout`` / ``ConnectError`` …）。空串 = 没有传输层错误。
    transport_error: str = ""
    #: 传输错误的类名，用于分诊（``ConnectTimeout`` / ``ConnectError`` /
    #: ``ReadTimeout`` …）。空串 = 没有。
    transport_error_kind: str = ""
    #: 我们**自己**的出口层给出的拒绝理由（受管网络层）。空串 = 没有。
    egress_denial: str = ""
    #: 站点政策拒绝（robots / 明确声明）。空串 = 没有。
    policy_denial: str = ""
    #: 安全的响应头（见 :func:`safe_response_headers`）。大小写按 HTTP 惯例小写。
    headers: Mapping[str, str] = field(default_factory=dict)
    #: 正文（已解码）。只在判定需要时给；不落盘。
    text: str = ""
    #: 正文里是否有可用内容（由抓取层的 ``assess_http_response`` 判定）。
    body_usable: bool = False
    #: 这次抓取最终落在了哪个地址（可能在重定向之后）。
    final_url: str = ""
    #: 请求的地址。``final_url != url`` 说明发生了重定向。
    url: str = ""

    @property
    def challenge_header(self) -> bool:
        """响应头里有没有**明确**的挑战信号。"""
        for name in CHALLENGE_HEADERS:
            if self.headers.get(name, "").strip().lower() == "challenge":
                return True
        return False

    @property
    def redirected(self) -> bool:
        return bool(self.final_url) and bool(self.url) and self.final_url != self.url


@dataclass(frozen=True)
class AccessDiagnosis:
    """判断 + 人话 + （需要时）证据引用。"""

    cause: SuspectedCause
    action: ActionTaken
    #: 人话。拒绝/异常时非空 —— 只有理由码的报告需要读代码才能解释。
    detail: str
    #: ``challenge_detected``：**有没有观测到**挑战。与 ``cause`` 分开，因为
    #: 一个页面可以既带挑战指纹、又因为别的原因失败。
    challenge_detected: bool = False
    #: 服务端给的等待秒数（已解析、已截断）。``None`` = 没给或解析不出来。
    retry_after_s: int | None = None
    #: 疑似根因的凭据引用。除 :attr:`SuspectedCause.IP_POLICY` 外可以为空。
    cause_evidence_ref: str = ""

    @property
    def is_refusal(self) -> bool:
        """这次是不是「站点/网络明确拒绝了我们」—— 终态的那一类。"""
        return self.cause in _REFUSAL_CAUSES

    @property
    def body_may_be_kept(self) -> bool:
        """这次的正文允不允许进入业务证据。

        设计 §4 逐字：「Cloudflare challenge/CAPTCHA 正文或对应头 → ``waf_challenge``；
        **不当网页证据**」。这一格把这个判定变成一个可读的布尔，而不是散在
        调用点的 ``if``。
        """
        return self.cause is not SuspectedCause.WAF_CHALLENGE


_REFUSAL_CAUSES: frozenset[SuspectedCause] = frozenset(
    {
        SuspectedCause.POLICY_BLOCKED,
        SuspectedCause.AUTH_REQUIRED,
        SuspectedCause.ACCESS_DENIED_UNKNOWN,
        SuspectedCause.WAF_CHALLENGE,
        SuspectedCause.CONTENT_UNUSABLE,
        SuspectedCause.UNSAFE_TARGET,
        SuspectedCause.IP_POLICY,
        SuspectedCause.EGRESS_DENIED,
    }
)

#: 能识别出「这是挑战页」的正文指纹 —— 与 ``adapters/classify.py::WAF_MARKERS``
#: 是同一批，但**刻意不 import 它**：``domain/`` 不依赖 ``adapters/``（分层纪律），
#: 而这里的用法也不同（那边判 ``BlockReason``，这里判 ``challenge_detected``）。
#: 两份字面量的漂移由 ``tests/test_access_diagnosis.py`` 里一条「两边都认得
#: 同一个样本」的判据钉住。
_CHALLENGE_TEXT_MARKERS: tuple[str, ...] = (
    "just a moment",
    "checking your browser",
    "cf-chl-",
    "cf_chl_opt",
    "captcha-delivery.com",
    "please verify you are a human",
)

_LOGIN_TEXT_MARKERS: tuple[str, ...] = (
    "sign in to continue",
    "please log in to view",
    "login required",
    "you must be logged in",
)

_PAYWALL_TEXT_MARKERS: tuple[str, ...] = (
    "subscribe to read",
    "this content is for subscribers",
    "subscribers only",
)

#: 传输错误类名 → 根因。**不在表里的落 ``TRANSIENT_OR_UNKNOWN``**，
#: 因为「没见过这个错误名」不等于「这是一次永久失败」。
_NETWORK_ERROR_KINDS: frozenset[str] = frozenset(
    {
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "TimeoutError",
        "gaierror",
        "SSLError",
        "SSLCertVerificationError",
        "ConnectionResetError",
        "RemoteProtocolError",
        "LocalProtocolError",
    }
)


def _contains_any(haystack: str, needles: tuple[str, ...]) -> bool:
    return any(needle in haystack for needle in needles)


def diagnose(observation: Observation) -> AccessDiagnosis:
    """把一次观测判成一份诊断。**纯函数**，可穷举测试。

    判定顺序即优先级，取自设计 §4 那张表自上而下：

    1. **自有出口层拒绝** —— 我们这一侧的问题，与站点无关。
    2. **传输层错误** —— 网络未通。**比状态码优先**：一次既超时又带缓存状态码的
       记录，说「网络未通」比说「403」更接近事实。
    3. **站点政策拒绝**。
    4. **挑战**：响应头 ``cf-mitigated: challenge`` **或**正文指纹。两者是
       **或**的关系 —— 官方文档说缺少那个头不等于没有其他拦截。
    5. **401 / 登录页 / paywall** → ``auth_required``。
    6. **403** → ``access_denied_unknown``（**永不** ``ip_policy``）。
    7. **429** → ``rate_limited``（带 ``Retry-After`` 时解析出来）。
    8. **404/410** → ``source_gone``。
    9. **5xx** → ``transient_or_unknown``。
    10. **3xx** → ``none``（一次重定向不是一次内容失败；跟不跟由地址判据决定）。
    11. **2xx 但正文不可用** → ``content_unusable``。
    12. 其余 → ``none``。
    """
    headers = {k.lower(): v for k, v in observation.headers.items()}
    challenge_by_header = observation.challenge_header
    lowered = observation.text[:200_000].lower()
    challenge_by_text = _contains_any(lowered, _CHALLENGE_TEXT_MARKERS)
    challenge_detected = challenge_by_header or challenge_by_text
    status = observation.observed_status

    if observation.egress_denial:
        return AccessDiagnosis(
            SuspectedCause.EGRESS_DENIED,
            ActionTaken.REVIEW_REQUESTED,
            f"自有出口层拒绝：{observation.egress_denial}",
        )

    if observation.transport_error:
        kind = observation.transport_error_kind
        cause = (
            SuspectedCause.NETWORK_UNKNOWN
            if kind in _NETWORK_ERROR_KINDS
            else SuspectedCause.TRANSIENT_OR_UNKNOWN
        )
        detail = (
            f"传输层失败（{kind or '未分类'}）：{observation.transport_error}"
        )
        if cause is SuspectedCause.NETWORK_UNKNOWN:
            detail += " —— 这是网络/自有出口问题，不是「网站不存在」"
        return AccessDiagnosis(cause, ActionTaken.DEFERRED_BACKOFF, detail)

    if observation.policy_denial:
        return AccessDiagnosis(
            SuspectedCause.POLICY_BLOCKED,
            ActionTaken.STOPPED_METHOD,
            f"站点/robots 政策拒绝：{observation.policy_denial}",
        )

    # 挑战先于状态码判定：一个 200 的挑战页与一个 403 的挑战页，处置是同一条。
    if challenge_detected:
        how = []
        if challenge_by_header:
            how.append("响应头 cf-mitigated: challenge")
        if challenge_by_text:
            how.append("正文挑战指纹")
        return AccessDiagnosis(
            SuspectedCause.WAF_CHALLENGE,
            ActionTaken.STOPPED_METHOD,
            "观测到 WAF 挑战（"
            + "、".join(how)
            + f"），状态码 {status if status is not None else '未读出'}"
            "；正文不作为业务证据，改走批准来源",
            challenge_detected=True,
            retry_after_s=parse_retry_after(headers.get("retry-after")),
        )

    if status == 401 or _contains_any(lowered, _LOGIN_TEXT_MARKERS) or _contains_any(
        lowered, _PAYWALL_TEXT_MARKERS
    ):
        return AccessDiagnosis(
            SuspectedCause.AUTH_REQUIRED,
            ActionTaken.STOPPED_METHOD,
            "需要登录或订阅权限；本阶段匿名采集在此停止该路径",
            challenge_detected=False,
        )

    if status == 403:
        # 这里是整张表最要紧的一格：**403 的默认结论是「根因不明」**。
        # 有没有 cf-ray、有多少个站同时 403，都不改变这一格 —— Ray ID 只关联
        # 请求，403 可以由源站权限规则或 Cloudflare 触发（设计 §4，CF1/CF3）。
        return AccessDiagnosis(
            SuspectedCause.ACCESS_DENIED_UNKNOWN,
            ActionTaken.REVIEW_REQUESTED,
            "HTTP 403 且根因不明；保存诊断并走合法替代来源，"
            "不得据此判定出口 IP 被封",
        )

    if status == 429:
        retry_after = parse_retry_after(headers.get("retry-after"))
        detail = "HTTP 429 限流"
        detail += (
            f"；服务端要求等待 {retry_after} 秒"
            if retry_after is not None
            else "；服务端未给可用的 Retry-After，按有界退避"
        )
        return AccessDiagnosis(
            SuspectedCause.RATE_LIMITED,
            ActionTaken.DEFERRED_BACKOFF,
            detail,
            retry_after_s=retry_after,
        )

    if status in (404, 410):
        return AccessDiagnosis(
            SuspectedCause.SOURCE_GONE,
            ActionTaken.STOPPED_METHOD,
            f"HTTP {status}：来源失效；保留旧证据时间，不据此推断组织消失",
        )

    if status is not None and status >= 500:
        return AccessDiagnosis(
            SuspectedCause.TRANSIENT_OR_UNKNOWN,
            ActionTaken.DEFERRED_BACKOFF,
            f"HTTP {status}：按策略有限重试，未知计费先对账",
        )

    if status is not None and 300 <= status < 400:
        # **一次重定向不是一次内容失败。** 来源给了我们一个正常的回应，只是它说
        # 「去别处」。少了这一格，一次 302 会落到下面「正文不可用 → 内容问题」
        # 那一栏 —— 而那句判断会把「来源是通的」读成「这一页有问题」，并且让
        # ``CONTENT_UNUSABLE`` 出现在一个根本不该有内容的重定向响应上。
        #
        # 跟不跟下一跳**不在这里判**：那是 ``security/network.py`` 的地址判据，
        # 因为它要问的是「那个地址能不能去」，不是「这个来源怎么了」。
        return AccessDiagnosis(
            SuspectedCause.NONE,
            ActionTaken.PROCEEDED,
            f"HTTP {status}：这一跳是一次重定向响应，来源是通的；"
            "跟不跟由下一跳自己的地址判据决定",
        )

    if status is None:
        # **读不出状态码**。这不等于 200，也不等于失败 —— 它是一次没有 HTTP
        # 层观测的记录（例如浏览器渲染没报状态）。判据只剩正文可用性这一条，
        # 且这一格的存在本身就是「不许把它折成 200」的落点。
        if not observation.body_usable:
            return AccessDiagnosis(
                SuspectedCause.CONTENT_UNUSABLE,
                ActionTaken.WITHHOLD_BODY,
                "状态码未观测到，且正文不可用；不折算成 HTTP 200，正文不入证据",
            )
        return AccessDiagnosis(
            SuspectedCause.NONE,
            ActionTaken.PROCEEDED,
            "状态码未观测到，正文可用 —— 按正文可用性判定，HTTP 状态如实留空",
        )

    if not observation.body_usable:
        return AccessDiagnosis(
            SuspectedCause.CONTENT_UNUSABLE,
            ActionTaken.WITHHOLD_BODY,
            f"HTTP {status} 但正文不可用（挑战/错误/占位）；正文不入证据",
        )

    return AccessDiagnosis(SuspectedCause.NONE, ActionTaken.PROCEEDED, "")


def with_cause_evidence(cause: SuspectedCause, evidence_ref: str, detail: str) -> AccessDiagnosis:
    """产生一个**带证据**的根因判定。**这是 ``IP_POLICY`` 唯一的入口。**

    设计 §4 逐字：「要确认 IP/ASN 规则需站点方的匹配规则/日志、明确错误说明或
    批准的诊断证据；本项目不通过轮换身份探测封禁边界。」

    所以这个函数**要求** ``evidence_ref`` 非空并且拒绝空白串 —— 一个没有出处的
    「出口 IP 被封」不是诊断，是某个人记得。调用者是人工/运维复核路径与批准的
    诊断导入，**不是** :func:`diagnose`。
    """
    ref = evidence_ref.strip()
    if not ref:
        raise ValueError(
            "带证据的根因判定必须给出 cause_evidence_ref（站点方的匹配规则/日志、"
            "明确错误说明或批准的诊断证据）；没有出处的根因不是诊断"
        )
    if cause is SuspectedCause.NONE:
        raise ValueError("NONE 不是一个需要证据的根因 —— 它表示这一次没有异常")
    return AccessDiagnosis(
        cause=cause,
        action=ActionTaken.REVIEW_REQUESTED,
        detail=detail or f"根因 {cause.value}，凭据 {ref}",
        cause_evidence_ref=ref,
    )


def parse_retry_after(value: object, *, now: datetime | None = None) -> int | None:
    """解析 ``Retry-After``。**两种合法写法都认，认不出就是 ``None``。**

    RFC 9110 允许两种取值：一个非负整数秒数，或一个 HTTP-date。日期那一支要
    减去当前时刻，得到的是**秒数**而不是一个时刻 —— 因为消费它的是「这次的
    退避多久」，而一个绝对时刻会在两个时区不同的 worker 上被读成两个等待期。

    返回值上限 :data:`RETRY_AFTER_MAX_SECONDS`：超过它的一律当作「服务端没有给
    一个我们愿意等的时间」，返回 ``None`` —— 见那个常量的说明。
    大于这个上限时返回 ``None`` 而不是截断到上限，因为截断会让我们在一个
    服务端明说「今天别来」的源上按一小时一次地刷，那正是「重试刷门」。
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    if raw.isdigit():
        seconds = int(raw)
        return seconds if 0 <= seconds <= RETRY_AFTER_MAX_SECONDS else None

    # 日期形态。只在**看起来像**日期时才交给解析器：``email.utils`` 对一堆
    # 垃圾串会抛，而抛与「不是一个日期」在这里是同一件事。
    if not _HTTP_DATE_HINT.match(raw):
        return None
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    delta = (moment - reference).total_seconds()
    if delta < 0:
        # 一个已经过去的时刻 = 现在就可以重试。0 而不是 None：服务端确实给了
        # 一个时间，只是它已经过去了 —— 与「没给」是两件事。
        return 0
    seconds = int(delta)
    return seconds if seconds <= RETRY_AFTER_MAX_SECONDS else None


#: 永远不进报告的响应头（可能带凭据、可能指认出口身份）。
#:
#: 设计 §3 逐字：「原始敏感 headers/cookies/key/IP 不出报告」。这是一份**白名单**
#: 而不是黑名单：黑名单的第一个遗漏就是一次泄漏，而白名单的遗漏只是一格缺失。
_SAFE_HEADER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "content-type",
        "content-length",
        "content-encoding",
        "content-language",
        "retry-after",
        "cf-mitigated",
        "cf-ray",
        "server",
        "date",
        "last-modified",
        "etag",
        "location",
        "x-robots-tag",
        "vary",
        "cache-control",
    }
)


def safe_response_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """只留下白名单里的响应头，键小写。

    ``cf-ray`` 在白名单里是**刻意的**：它是设计 §4 点名要能读到的东西
    （「Ray ID 用于关联经过 Cloudflare 的请求」），把它挡在报告之外，就没法
    解释「为什么这次不给结论」。它在白名单里**不等于**它可以推出任何根因 ——
    见 :func:`diagnose` 的 ``403`` 那一格。
    """
    if not headers:
        return {}
    out: dict[str, str] = {}
    for key, value in headers.items():
        lowered = str(key).strip().lower()
        if lowered in _SAFE_HEADER_ALLOWLIST:
            out[lowered] = str(value).strip()
    return out

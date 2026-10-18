"""来源级的等待与熔断 —— **按来源记账，不是按账号**。

采集设计 §4 逐字要求 429 走「共享 origin 限流」，§5 要求「来源级熔断」、「有界
重试」、「Retry-After」遵守，并点名了一条具体的坏结局：

    一个同源挑战页上的 20 个账号不得生成 20 个无界重试套件。

那个坏结局不是靠「记得少发几次」避免的，而是靠**账记在来源上**。20 个账号访问
同一个站点，是我们对**同一个服务端**发了 20 次请求；按账号记账的实现里，每一次
都以为自己是从零开始的，于是「20 个各自重试到底的套件」正好是它的产出 —— 而它在
报告上看起来只是「20 个账号各自失败了一次」，与真正的 20 次独立失败同形。

## 三件事各占一层，且都**有界**

* **一次访问的重试**由 :class:`FetchPermit` 兜住：它自己记着这次已经发了几次，
  ``max_attempts_per_fetch`` 到了就不再放行。这个界不住在调用方的循环里 ——
  一个写在循环条件里的界，第一个 ``continue`` 就绕过去了。
* **一个来源的等待期**由 :class:`OriginState.not_before` 兜住：429 与 5xx 的退避
  记在**来源**上，于是 A 账号收到的 ``Retry-After`` 会让 B 账号也等待。它同时
  兜住「退避被当成一次 sleep」那一类：本模块从不 sleep，它只回答「还要等多久」。
* **一个来源的熔断**由 :class:`OriginState.open_until` 兜住：连续若干次挫折之后
  打开，打开期间**一个请求都不发**；冷却结束后只放行**有界**个半开探针。

## 哪些失败算「来源的挫折」，哪些不算

这不是一个可以随手扩的表。**只有可能与我们这一侧的处境有关的失败**计入
（:data:`SETBACK_CAUSES`）：429、挑战页、403、5xx 与传输层失败。另外三类刻意
**不计**，理由各不相同，逐条写在 :data:`SETBACK_CAUSES` 与 :data:`NOT_A_SETBACK`：

* **401 / 登录页 / paywall / 404** —— 这些是页面**自身的属性**。一个站点有登录页
  与一个站点在拦我们，在熔断账上没有区别的必要，混进去的后果是一个正常站点被
  20 个登录页熔断。
* **本地判据拒绝**（``UNSAFE_TARGET`` / ``EGRESS_DENIED``）—— 请求**根本没有发出**。
  把「我们没有发请求」记成「来源出了问题」，账会指着错的地方，而且它会让一次
  配置错误表现为一次对外的故障。
* **``IP_POLICY``** —— 它只能由带证据的人工作出（``access.with_cause_evidence``）。
  一次人工判定不是一次流量观测，把它计进熔断等于让一份报告改行为。

## 「记进来源账」与「这一次怎么处置」是两件事

上面那张表回答的是**要不要把一次失败记到这个来源的账上**。它**不**回答这一次该推迟
还是该停 —— 两者容易混成一句「这类失败很糟」，而混掉的后果是两个方向各错一次：

* **推迟那一类**（:data:`DEFERRED_CAUSES` —— 429、5xx、传输层失败）：同一个请求
  隔一段再发一次**确实可能拿到别的结果**（服务端缓过来了、限流窗口过去了），所以
  它走 ``DEFERRED_BACKOFF``：记下一个最早可以重试的时刻，**并且**记一笔挫折。
* **停止那一类**（挑战页、403）：重发一次不会把它变成另一个结果。它同样记一笔
  挫折（同一个来源连着拦我们，账要看得见），但处置是 ``STOPPED_METHOD``，
  这一次访问到此为止。

把停止那一类也写成「推迟」，等于对一个明说「不」的站点按时重试；把推迟那一类写成
「停止」，等于 §4 那句「5xx 按策略有限重试」在实现里被静默取消 —— 而报告上那一次
的 ``action`` 会写 ``stopped_method``，读起来像是我们**决定**不再试了。

## 时钟注入，因此判据里没有 sleep

``now`` 默认 ``time.monotonic``：本模块要的是**时长**（``Retry-After`` 已被
``parse_retry_after`` 解析成秒数），而墙上时钟的一次 NTP 校正会凭空造出一个等待期
或抹掉一个冷却。判据注入一个手摇的时钟，于是「等 120 秒之后半开」这件事不需要
真的等 120 秒，也不会在慢机器上偶发失败。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from urllib.parse import urlsplit

from .access import AccessDiagnosis, ActionTaken, SuspectedCause

__all__ = [
    "AccessGovernor",
    "AccessPolicy",
    "Admission",
    "DEFERRED_CAUSES",
    "FetchPermit",
    "OriginReport",
    "OriginState",
    "NOT_A_SETBACK",
    "SETBACK_CAUSES",
    "STOPPED_CAUSES",
    "origin_key",
]

#: 默认端口。与 ``adapters/http.py`` 那一份同值，但**不是**同一个东西：那边用它
#: 拼 ``Host`` 头与判断要不要写端口，这边用它决定两个 URL 是不是同一个来源。
_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}

#: 计入来源熔断账的失败。见模块 docstring 最后一节。
SETBACK_CAUSES: frozenset[SuspectedCause] = frozenset(
    {
        SuspectedCause.RATE_LIMITED,
        SuspectedCause.WAF_CHALLENGE,
        SuspectedCause.ACCESS_DENIED_UNKNOWN,
        SuspectedCause.TRANSIENT_OR_UNKNOWN,
        SuspectedCause.NETWORK_UNKNOWN,
    }
)

#: **不进**熔断账的那几类，连同各自不进的理由。写成一张表而不是散在 ``if`` 里，
#: 是为了让「为什么 401 不算」有一个可读的出处 —— 下一个人要加一类的时候，
#: 先要在这张表前面回答同一个问题。
#:
#: 它与 :data:`SETBACK_CAUSES` **恰好是一个分区**：每一个 ``SuspectedCause``
#: 要么进账、要么在这张表里有一条写下来的理由。这条判据由
#: ``tests/test_access_governance.py`` 执行 —— 只写「应该是分区」，
#: 下一个人加一个根因时就会漏掉这里，而漏掉的表现是它默默地不进账。
NOT_A_SETBACK: dict[SuspectedCause, str] = {
    SuspectedCause.AUTH_REQUIRED: "页面自身的属性：一个登录页与一个被拦的页在对我们这一侧的处境上不是一回事",
    SuspectedCause.POLICY_BLOCKED: "站点已明确声明拒绝；重试与等待都不改变它，它是一次 STOPPED_METHOD 而不是一次挫折",
    SuspectedCause.SOURCE_GONE: "404/410 说的是这个 URL 没了，不是这个来源在拦我们",
    SuspectedCause.CONTENT_UNUSABLE: "正文不可用通常是页面自己的问题；它已经由 WITHHOLD_BODY 处置",
    SuspectedCause.UNSAFE_TARGET: "本地判据拒绝，请求**没有发出**",
    SuspectedCause.EGRESS_DENIED: "自有出口层拒绝，请求**没有发出**",
    SuspectedCause.IP_POLICY: "只能由带证据的人工作出；一次人工判定不是一次流量观测",
    SuspectedCause.NONE: "这一次没有任何异常",
    SuspectedCause.UNKNOWN: "没有任何证据把它归到任何一类 —— 没有证据的失败不记来源的账",
}

#: :data:`SETBACK_CAUSES` 里**推迟着重试**的那一半，见模块 docstring 那一节。
#: 它们的共同点是「同一个请求隔一段时间再发一次**可能拿到别的结果**」——
#: 这是 §4 说的「按策略有限重试」，也是「有界退避」里「退避」那一半的落点。
DEFERRED_CAUSES: frozenset[SuspectedCause] = frozenset(
    {
        SuspectedCause.RATE_LIMITED,
        SuspectedCause.TRANSIENT_OR_UNKNOWN,
        SuspectedCause.NETWORK_UNKNOWN,
    }
)

#: :data:`SETBACK_CAUSES` 里**停止重试**的那一半：重发一次不会改变结果。
#: 它仍然记一笔挫折（同一个来源连着拦我们，账要看得见），但处置是
#: :attr:`ActionTaken.STOPPED_METHOD`。推导而不是另写一份字面量 —— 两份字面量
#: 迟早会漂移，而漂移的表现是某一类失败**同时**属于两边或哪一边都不属于。
STOPPED_CAUSES: frozenset[SuspectedCause] = SETBACK_CAUSES - DEFERRED_CAUSES


def origin_key(url: str) -> str:
    """URL → 来源键：``scheme://host[:port]``，小写，默认端口省略。

    三个选择都是承重的：

    * **按来源而不是按账号**（模块 docstring 的整条理由）。
    * **带 scheme**：``http://`` 与 ``https://`` 在服务端常常是两套配额与两套规则；
      合成一个键会让一次 http 的 429 去挡住 https 那一侧。
    * **默认端口省略**：``https://a.example:443`` 与 ``https://a.example`` 是同一个
      服务端。写成两个键会让同一个站点有两份互不知情的账，而那种重复恰好只在
      「有人手写了一个带端口的 URL」时出现 —— 最难被发现的那种分裂。

    **没有 host 的 URL 抛 ``ValueError``**，不返回空串：空串会变成一张所有人都往里
    记账的共享账（比不记账更糟），而「这个来源键算不出来」必须在调用点当场可见。
    调用点在 ``check_target`` 之后，那里协议与主机都已经过判据。
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        raise ValueError(
            f"算不出来源键：{url!r} 没有 scheme 或没有主机名。\n"
            "来源级的账记在一个算不出来的键上，会让两个无关的站点共享同一个等待期与"
            "同一个熔断 —— 而报告上它看起来只是「那一批都慢」。"
        )
    port = parts.port or _DEFAULT_PORTS.get(scheme, 0)
    if port == _DEFAULT_PORTS.get(scheme, 0):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


@dataclass(frozen=True)
class AccessPolicy:
    """本部署愿意为一个来源的挫折付多少。**每一格都有界，坏值当场抛。**

    缺省值是一组**保守**的值，而它们的意义是「20 个账号撞上同一个挑战页时，对外
    发出的请求数是 3 而不是 20」：阈值 3 次连续挫折打开熔断，之后每一个账号的第
    一次尝试都会被 :attr:`ActionTaken.CIRCUIT_OPENED` 拦下，一个字节都发不出去。
    """

    #: 一次访问最多发几次。**1 表示不重试**；2 表示「首次 + 一次重试」。
    max_attempts_per_fetch: int = 2
    #: 退避的起点与倍率（没有 ``Retry-After`` 时用）。
    backoff_base_s: float = 2.0
    backoff_factor: float = 2.0
    backoff_max_s: float = 60.0
    #: 连续几次挫折打开来源熔断。
    circuit_setback_threshold: int = 3
    #: 熔断的第一次冷却，以及它每次重新打开时的翻倍上限。
    circuit_cooldown_s: float = 300.0
    circuit_cooldown_max_s: float = 3600.0
    #: 每次冷却结束后放行几个探针。**有界**：用完就不再放行，直到下一次重新打开。
    half_open_probes: int = 1

    def __post_init__(self) -> None:
        if self.max_attempts_per_fetch < 1:
            raise ValueError(
                "max_attempts_per_fetch 至少是 1 —— 0 表示一次都不发，那不是重试策略，"
                "是「这个来源不用了」，而后者该由 policy 的另一格表达"
            )
        if self.backoff_base_s < 0:
            raise ValueError("backoff_base_s 不能为负：负的等待期等于没有等待期")
        if self.backoff_factor < 1:
            raise ValueError(
                "backoff_factor 小于 1 会让重试越来越密 —— 那是「重试刷门」，"
                "而本模块存在的理由之一就是不许它发生"
            )
        if self.backoff_max_s < self.backoff_base_s:
            raise ValueError("backoff_max_s 小于 backoff_base_s：上限比起点还低")
        if self.circuit_setback_threshold < 1:
            raise ValueError("circuit_setback_threshold 至少是 1")
        if not 0 < self.circuit_cooldown_s <= self.circuit_cooldown_max_s:
            raise ValueError(
                "冷却必须是正的，且不超过 circuit_cooldown_max_s —— "
                "一个 0 冷却的熔断等于没有熔断，而它会让「已熔断」这个读数是假的"
            )
        if self.half_open_probes < 1:
            raise ValueError(
                "半开至少要有 1 个探针，否则熔断永远闭不上：冷却结束之后没有任何请求"
                "会发出去，于是来源永远不会被重新判定为可用"
            )

    def backoff_for(self, setbacks: int) -> float:
        """第 ``setbacks`` 次连续挫折之后该等多久。**有上限。**"""
        if setbacks <= 0:
            return 0.0
        grown = self.backoff_base_s * (self.backoff_factor ** (setbacks - 1))
        return min(grown, self.backoff_max_s)

    def cooldown_for(self, opened_count: int) -> float:
        """第 ``opened_count`` 次打开时冷却多久。**有上限。**"""
        if opened_count <= 1:
            return self.circuit_cooldown_s
        grown = self.circuit_cooldown_s * (self.backoff_factor ** (opened_count - 1))
        return min(grown, self.circuit_cooldown_max_s)


@dataclass
class OriginState:
    """一个来源的账。**可变**，且只在 :class:`AccessGovernor` 里存在一份。

    它不冻结是有意的：它记录的是「截至此刻」的处境，而把每次变化做成一个新对象
    会让「同一个来源有两份账」变成一种可以表达的状态 —— 那正是本模块要消灭的东西。
    对外只给 :class:`OriginReport` 那种冻结的读数。
    """

    #: 连续挫折次数。一次正常返回清零。
    consecutive_setbacks: int = 0
    #: 熔断到期时刻（单调时钟）。``0.0`` 表示闭合。
    open_until: float = 0.0
    #: 半开时还剩几个探针。
    probes_left: int = 0
    #: 下一次最早可以发请求的时刻（``Retry-After`` / 退避）。``0.0`` 表示现在就可以。
    not_before: float = 0.0
    #: 这个来源一共被打开过几次。它决定冷却翻到哪一档。
    opened_count: int = 0
    #: 最近一次的根因码。给报告用，不参与任何判定。
    last_cause: str = ""

    @property
    def open(self) -> bool:
        return self.open_until > 0.0


def _cause_of(value: str) -> SuspectedCause:
    """来源账上那个根因字符串 → 枚举。**读不出来就是 ``UNKNOWN``。**

    账上的空串（这个来源还没有过任何结果）与一个读不出来的值，答的都是同一句话：
    「我们拦住了你，但账上没写清是为什么」。折成 ``UNKNOWN`` 而不是 ``NONE`` ——
    ``NONE`` 的意思是「这一次没有异常」，那是一句**判断**，而这里没有据以判断的东西。

    ``ValueError`` 是可能的（``str`` 是开放的），但只有本模块写它，所以真出现就
    说明有人绕过 :meth:`FetchPermit.record` 改过账；此时报告里一个 ``unknown``
    比一次抛异常好 —— 抛异常会让一次已经发生的拦截在回执里消失。
    """
    try:
        return SuspectedCause(value)
    except ValueError:
        return SuspectedCause.UNKNOWN


@dataclass(frozen=True)
class OriginReport:
    """一个来源的**只读**读数。给报告、给判据、给事后排查用。

    它带 ``wait_s`` 与 ``open_for_s`` 两个**时长**而不是两个时刻：读的人问的是
    「还要等多久」，而单调时钟上的时刻对他没有意义。
    """

    origin: str
    consecutive_setbacks: int
    open_for_s: float
    probes_left: int
    opened_count: int
    wait_s: float
    last_cause: str = ""


@dataclass(frozen=True)
class Admission:
    """**发请求之前那一问**的答案。

    它同时回答两个不同的问题，而这两个问题以前是分开写在调用方的：

    * 现在能不能发（:attr:`allowed`）；
    * 我们**以为**自己在做什么（:attr:`action`）—— 这一格是给报告的，它让
      「推迟了」与「熔断了」与「放行了一个探针」在事后读得出来。
    """

    allowed: bool
    action: ActionTaken
    #: **为什么**。拒付时它答的是「我们手上那份账说这个来源怎么了」：熔断来自
    #: 那几次挫折的根因，预算用尽来自这一次已经记下的根因。没有它，报告里
    #: 每一行被拦下的抓取都只剩一句 ``blocked``，而「等冷却」与「查网络」是
    #: 两条完全不同的下一步。
    #:
    #: 默认 ``NONE`` 只出现在放行的那条路上（放行 = 没有异常），而且**不是**
    #: 「还没有结果」的表示 —— 那一种由 :meth:`FetchPermit.admit` 单独判成
    #: ``UNKNOWN``，见 ``_last_cause_or_unknown``。
    cause: SuspectedCause = SuspectedCause.NONE
    wait_s: float = 0.0
    detail: str = ""


@dataclass
class FetchPermit:
    """**一次访问**的许可。它自己就是那个界：记着这次已经发了几次。

    生命周期是 ``admit() -> 发请求 -> record(诊断)``，可以重复最多
    ``max_attempts_per_fetch`` 轮。两处纪律：

    * :meth:`admit` **在放行的那一刻就记一次尝试**，而不是等 ``record``。一次发出
      去之后崩在半路的请求仍然是对外发过的请求；把计数挂在 ``record`` 上，会让
      「发送后进程死掉」这一格不计费用，而那恰好是重试最容易失控的场景。
    * :meth:`admit` 与 :meth:`record` 必须成对。放行了却不 ``record``，来源账会
      停在上一次的状态上 —— 这一点写在方法 docstring 里，而它没有别的强制手段：
      强行要求成对只能靠一个会改变控制流的上下文管理器，而那会让「发不出去、
      直接返回一份拒绝结果」这条正常路径变得难写。
    """

    origin: str
    policy: AccessPolicy
    state: OriginState
    clock: Callable[[], float]
    attempts: int = 0
    _probe_in_flight: bool = False
    #: 上一次的结果是不是「推迟之后再试」那一类。见 :meth:`admit` 的判定顺序。
    _retry_allowed: bool = False
    #: 有没有 ``record`` 过。**「还没有结果」与「结果是终态」是两件事**：前者
    #: （刚放行、请求在途、或调用方没 ``record``）不该被「上一次的结果是 X」那句话
    #: 挡住 —— 那句话会指着一个不存在的上一次，而 ``_last_cause`` 的初值
    #: ``NONE`` 会让它在报告上写成「上一次正常」。
    _has_result: bool = False
    _last_cause: SuspectedCause = SuspectedCause.NONE

    # ------------------------------------------------------------------
    def _last_cause_or_unknown(self) -> SuspectedCause:
        """记下过结果就用它，没有就 ``UNKNOWN``。

        **不是** ``_last_cause`` 本身：它的初值是 ``NONE``，而 ``NONE`` 在报告里
        的意思是「这一次没有任何异常」。一次还没拿到结果就撞上预算的访问，用
        ``NONE`` 报出来会读成「一切正常，我们却停了」—— 而 ``UNKNOWN`` 说的是
        实话：账上还没有任何一次结果。
        """
        return self._last_cause if self._has_result else SuspectedCause.UNKNOWN

    def admit(self) -> Admission:
        """现在能不能再发一次请求。

        ## 判定的顺序是承重的

        先问**这次访问的预算**，再问**上一次的结果许不许重试**，最后问**来源的处境**：

        * 反过来（先来源后预算）会让一次已经用尽重试的访问在熔断打开时被记成
          ``CIRCUIT_OPENED`` —— 而那个来源可能根本没有熔断，报告上却多了一次熔断。
        * 先问预算还有一个副作用是对的：``max_attempts_per_fetch=1`` 的实现
          （不重试）在熔断打开时仍然得到 ``CIRCUIT_OPENED``，因为它第一次
          ``admit`` 时预算还没用尽。
        * 中间那一问是「403 不能靠重试变成 200」的落点。**它必须由工具来问**：
          一个把「可不可以重试」写在调用方 ``if`` 里的实现，会在有人加一条新的
          终态根因时漏掉那一处，而漏掉的表现是「重试了一次本来不该重试的请求」。
          它只在**真的记下过结果**之后才生效 —— 「还没有结果」（刚放行、请求在途）
          不是一次终态，对它的处置由预算那一问兜住。

        来源那一段内部：熔断打开 → 冷却未到则拒；冷却已到则**只放行有界个探针**；
        没有熔断但有等待期 → 拒并给出剩余秒数。
        """
        now = self.clock()
        if self.attempts >= self.policy.max_attempts_per_fetch:
            return Admission(
                False,
                ActionTaken.STOPPED_METHOD,
                cause=self._last_cause_or_unknown(),
                detail=(
                    f"这次访问的重试预算已用尽（最多 {self.policy.max_attempts_per_fetch} 次）——"
                    "同一个来源继续重试不会改变上一次的结果"
                ),
            )
        if self._has_result and not self._retry_allowed:
            return Admission(
                False,
                ActionTaken.STOPPED_METHOD,
                cause=self._last_cause,
                detail=(
                    f"上一次的结果是 {self._last_cause.value} —— 它不是「推迟之后再试」"
                    "的那一类（那一类的动作是 DEFERRED_BACKOFF）。同一个请求重发一次"
                    "不会把它变成另一个结果，所以这次访问到此为止"
                ),
            )
        if self.state.open:
            if now < self.state.open_until:
                return Admission(
                    False,
                    ActionTaken.CIRCUIT_OPENED,
                    cause=_cause_of(self.state.last_cause),
                    wait_s=self.state.open_until - now,
                    detail=(
                        f"来源 {self.origin} 的熔断打开中，还有 "
                        f"{self.state.open_until - now:.0f} 秒冷却；这期间一个请求都不发"
                    ),
                )
            if self.state.probes_left <= 0:
                return Admission(
                    False,
                    ActionTaken.CIRCUIT_OPENED,
                    cause=_cause_of(self.state.last_cause),
                    detail=(
                        f"来源 {self.origin} 的半开探针已用完（本次冷却放行 "
                        f"{self.policy.half_open_probes} 个）；等下一次冷却结束"
                    ),
                )
            self.state.probes_left -= 1
            self._probe_in_flight = True
            return Admission(
                True,
                ActionTaken.HALF_OPEN_PROBE,
                cause=_cause_of(self.state.last_cause),
                detail=(
                    f"来源 {self.origin} 的冷却结束，放行一个**有界**探针"
                    f"（还剩 {self.state.probes_left} 个）；探针不是绕封禁，它的结果"
                    "只决定熔断闭合还是重新打开"
                ),
            )
        if now < self.state.not_before:
            return Admission(
                False,
                ActionTaken.DEFERRED_BACKOFF,
                cause=_cause_of(self.state.last_cause),
                wait_s=self.state.not_before - now,
                detail=(
                    f"来源 {self.origin} 的等待期未到，还有 "
                    f"{self.state.not_before - now:.0f} 秒（服务端给的 Retry-After 或退避）"
                ),
            )
        self.attempts += 1
        return Admission(True, ActionTaken.PROCEEDED)

    # ------------------------------------------------------------------
    def record(self, diagnosis: AccessDiagnosis) -> AccessDiagnosis:
        """把这一次的结果记进来源账，**并返回一份动作被治理层改写过的诊断**。

        改写的是 :attr:`AccessDiagnosis.action` 那一格，其余（根因、人话、证据、
        等待秒数）原样带过 —— 判断是 ``access.diagnose`` 的，处置是这里的。
        这一分工是刻意的：同一个根因在不同 policy 下处置不同，而报告要能同时读到
        两件事，所以**不能**把诊断写死在判定里。

        返回值可能是同一个对象（不需要改写时），也可能是一个 :func:`dataclasses.replace`
        出来的副本；调用方**不要**依赖身份相等。
        """
        cause = diagnosis.cause
        now = self.clock()
        was_probe = self._probe_in_flight
        self._probe_in_flight = False
        self.state.last_cause = cause.value
        self._has_result = True
        self._last_cause = cause

        action: ActionTaken
        if cause is SuspectedCause.NONE:
            # 一次正常返回把这个来源的连续挫折清零，并清掉等待期。
            self.state.consecutive_setbacks = 0
            self.state.not_before = 0.0
            action = ActionTaken.PROCEEDED
        elif cause in SETBACK_CAUSES:
            self.state.consecutive_setbacks += 1
            if cause in DEFERRED_CAUSES:
                # 服务端给了等待期就用它（只有 429 会给），否则用**有界**退避。
                wait_s = (
                    float(diagnosis.retry_after_s)
                    if diagnosis.retry_after_s is not None
                    else self.policy.backoff_for(self.state.consecutive_setbacks)
                )
                # ``max`` 而不是赋值：另一条路径可能已经把一个更远的等待期记在这里，
                # 而把它拉近等于替服务端缩短了一次它明确要求的等待。
                self.state.not_before = max(self.state.not_before, now + wait_s)
                action = ActionTaken.DEFERRED_BACKOFF
            else:
                action = ActionTaken.STOPPED_METHOD
        else:
            # 页面自身的属性、本地判据拒绝、或人工判定：**来源账不动**。
            action = diagnosis.action

        if was_probe:
            if cause in SETBACK_CAUSES:
                # 探针失败 = 来源仍然不可用：立即重新打开，冷却翻倍（有上限）。
                self._open(now)
                action = ActionTaken.CIRCUIT_OPENED
            else:
                # 探针拿到了一个**正常**的回答（哪怕那个页面本身是 404 或登录页）：
                # 来源是通的，熔断闭合。
                #
                # **动作不改写成 PROCEEDED。** 熔断闭合是关于**来源**的一件事，
                # 不是关于这次访问的一件事：一个 401 探针的诊断动作是
                # ``STOPPED_METHOD``，改写成 ``PROCEEDED`` 会让调用方以为这一页的
                # 正文可以用 —— 而熔断记账不该顺带把一个业务判定也翻过去。
                self._close()
        elif self.state.consecutive_setbacks >= self.policy.circuit_setback_threshold:
            self._open(now)
            action = ActionTaken.CIRCUIT_OPENED

        self._retry_allowed = action is ActionTaken.DEFERRED_BACKOFF
        if action is diagnosis.action:
            return diagnosis
        return _with_action(diagnosis, action)

    # ------------------------------------------------------------------
    def _open(self, now: float) -> None:
        """打开熔断。冷却按打开次数翻倍，**有上限**。"""
        self.state.opened_count += 1
        self.state.open_until = now + self.policy.cooldown_for(self.state.opened_count)
        self.state.probes_left = self.policy.half_open_probes
        # 熔断打开期间那个等待期已经没有意义了 —— 留着它会让冷却结束之后**还**要
        # 等一段，而报告上会显示成「冷却到了却仍然不发」，原因看不出来。
        self.state.not_before = 0.0

    def _close(self) -> None:
        self.state.open_until = 0.0
        self.state.probes_left = 0
        self.state.consecutive_setbacks = 0
        # ``opened_count`` 刻意**不清零**：它记的是「这个来源一共被打开过几次」，
        # 是一个历史事实。清零会让一个反复出问题的来源永远停在第一档冷却上。


def _with_action(diagnosis: AccessDiagnosis, action: ActionTaken) -> AccessDiagnosis:
    """复制一份诊断，只换 ``action``。

    用 ``dataclasses.replace`` 而不是手写构造：``AccessDiagnosis`` 加一格时，
    手写的那一处会静默地丢一个字段，而丢的那个字段（例如 ``retry_after_s``）
    恰好是下游要读的。
    """
    return replace(diagnosis, action=action)


@dataclass
class AccessGovernor:
    """全部来源的账。**一个抓取器持有一个**，跨账号、跨 work item。

    这是本模块的承重处：账必须是**进程级共享**的。一个每次抓取都新建的 governor
    与没有 governor 是同价的 —— 20 个账号会各自拿着一个干净的账本，而报告上
    每一个都只失败了一次。
    """

    policy: AccessPolicy = field(default_factory=AccessPolicy)
    now: Callable[[], float] = time.monotonic
    _origins: dict[str, OriginState] = field(default_factory=dict)

    def begin(self, url: str) -> FetchPermit:
        """起一次访问。``url`` 决定它落在哪个来源的账上。"""
        origin = origin_key(url)
        state = self._origins.setdefault(origin, OriginState())
        return FetchPermit(origin=origin, policy=self.policy, state=state, clock=self.now)

    def snapshot(self) -> tuple[OriginReport, ...]:
        """全部来源的只读读数，按来源名排序（可复现）。"""
        now = self.now()
        reports = [
            OriginReport(
                origin=origin,
                consecutive_setbacks=state.consecutive_setbacks,
                open_for_s=max(0.0, state.open_until - now) if state.open else 0.0,
                probes_left=state.probes_left,
                opened_count=state.opened_count,
                wait_s=max(0.0, state.not_before - now),
                last_cause=state.last_cause,
            )
            for origin, state in self._origins.items()
        ]
        return tuple(sorted(reports, key=lambda report: report.origin))

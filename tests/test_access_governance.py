"""B15-d：来源级的等待与熔断 —— 「20 个账号」那一条点名判据的落点。

采集设计 §5 逐字要求「有界重试」「按 origin 共享限流」「来源级熔断」「Retry-After」
遵守，并点名了一条具体的坏结局：**一个同源挑战页上的 20 个账号不得生成 20 个无界
重试套件**。

这一组判据的承重处不是「熔断会打开」（那是一个正例，任何实现都写得出来），而是
下面三件**否定式**的事：

* 账按**来源**记而不是按账号 —— 所以 20 个账号撞同一个挑战页时，对外发出的请求数
  是 3 而不是 20。判据里同时跑一遍「按账号记账」的对照，把那个 20 量出来：一个
  没有人量过的数字，读起来与「已经防住了」是一样的。
* 一个来源收到的 ``Retry-After`` 会挡住**另一个账号**对同一个来源的请求。
* 熔断的半开探针**有界**：冷却结束不是「重新放开」，是「放行 N 个」。

全部判据注入一个手摇时钟：本模块从不 sleep，它只回答「还要等多久」。
"""

from __future__ import annotations

import pytest

from marketops.domain.access import (
    ActionTaken,
    Observation,
    SuspectedCause,
    diagnose,
)
from marketops.domain.access_governance import (
    DEFERRED_CAUSES,
    NOT_A_SETBACK,
    SETBACK_CAUSES,
    STOPPED_CAUSES,
    AccessGovernor,
    AccessPolicy,
    origin_key,
)


class _Clock:
    """手摇的单调时钟。判据里没有一次 sleep。"""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _challenge(url: str):
    """一个同源挑战页：403 + 挑战指纹 ⇒ ``waf_challenge``。"""
    return diagnose(
        Observation(url=url, observed_status=403, text="Just a moment...", body_usable=False)
    )


def _forbidden(url: str):
    """一个干净的 403（没有挑战指纹）⇒ ``access_denied_unknown``。"""
    return diagnose(Observation(url=url, observed_status=403, text="no", body_usable=False))


def _server_error(url: str):
    return diagnose(Observation(url=url, observed_status=503, text="x", body_usable=False))


def _rate_limited(url: str, retry_after: str | None):
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return diagnose(
        Observation(url=url, observed_status=429, headers=headers, text="slow down", body_usable=False)
    )


def _ok(url: str):
    return diagnose(Observation(url=url, observed_status=200, text="<html>x</html>", body_usable=True))


def _fetch(governor: AccessGovernor, url: str, reply, *, limit: int = 50):
    """照抓取器那个循环跑一遍：``admit`` → 真的发一次 → ``record``，直到 ``admit`` 说不许。

    返回 ``(真的发出去的次数, 每一次的动作)``。**发出去了才算一次** —— 被拦下的
    ``admit`` 不产生请求，这正是「拒绝发生在网络动作之前」在治理层的对应物。
    """
    permit = governor.begin(url)
    sent = 0
    actions: list[ActionTaken] = []
    for _ in range(limit):
        admission = permit.admit()
        if not admission.allowed:
            actions.append(admission.action)
            return sent, actions
        sent += 1
        actions.append(permit.record(reply).action)
    raise AssertionError("循环没有被界住 —— 治理层本该在某一轮说不许")


# --- 第一组：来源键 ----------------------------------------------------------


def test_one_server_is_one_key() -> None:
    """大小写、尾斜杠、显式的默认端口都不产生第二个键。"""
    assert origin_key("https://A.Example.co.uk/x") == "https://a.example.co.uk"
    assert origin_key("https://a.example.co.uk:443/x?y=1") == "https://a.example.co.uk"
    assert origin_key("http://a.example.co.uk:80/") == "http://a.example.co.uk"
    assert (
        origin_key("https://a.example.co.uk/a")
        == origin_key("https://a.example.co.uk/b")
        == "https://a.example.co.uk"
    )


def test_http_and_https_are_two_keys() -> None:
    """两套配额、两套规则。合成一个键会让一次 http 的 429 挡住 https 那一侧。"""
    assert origin_key("http://a.example/x") != origin_key("https://a.example/x")


def test_a_non_default_port_is_its_own_key() -> None:
    assert origin_key("https://a.example:8443/x") == "https://a.example:8443"
    assert origin_key("https://a.example:8443/x") != origin_key("https://a.example/x")


def test_a_url_without_a_host_raises_instead_of_sharing_one_book() -> None:
    """**不返回空串。** 空串会变成一张所有人都往里记账的共享账，比不记账更糟。"""
    for bad in ("", "not a url", "/relative/path", "https:///nohost"):
        with pytest.raises(ValueError, match="来源键"):
            origin_key(bad)


# --- 第二组：哪些失败进熔断账 ------------------------------------------------


def test_every_suspected_cause_is_either_a_setback_or_has_a_written_reason() -> None:
    """两个集合恰好是一个分区，且「不算」的那一边每一条都有理由。

    只把「应该是分区」写在注释里，下一个人加一个根因时就会漏掉这里 ——
    而漏掉的表现是它**默默地不进账**，报告上什么都看不出来。
    """
    assert SETBACK_CAUSES | set(NOT_A_SETBACK) == set(SuspectedCause)
    assert not (SETBACK_CAUSES & set(NOT_A_SETBACK))
    for cause, why in NOT_A_SETBACK.items():
        assert why.strip(), f"{cause} 在不进账那一边，但没有写下理由"


def test_an_ordinary_login_page_does_not_open_the_breaker() -> None:
    """**负向判据。** 20 个登录页不得把一个正常站点熔断。

    把它们计进账的实现在报告上「更保守」，代价是一个有登录页的站点在第四次访问
    之后就再也不发请求了 —— 而那看起来像「这个来源不稳定」。
    """
    clock = _Clock()
    governor = AccessGovernor(
        AccessPolicy(circuit_setback_threshold=3, half_open_probes=1), now=clock
    )
    url = "https://a.example/private"
    for _ in range(5):
        sent, _actions = _fetch(
            governor, url, diagnose(Observation(url=url, observed_status=401, body_usable=False))
        )
        assert sent == 1
    assert governor.snapshot()[0].consecutive_setbacks == 0
    assert governor.snapshot()[0].open_for_s == 0.0
    # 下一个账号仍然发得出去。
    assert governor.begin(url).admit().allowed


def test_a_request_we_never_sent_does_not_open_the_breaker() -> None:
    """**请求根本没有发出。** 把「我们没有发请求」记成「来源出了问题」，
    账会指着错的地方，而且一次配置错误会表现成一次对外的故障。

    这里的诊断来自**我们自己的出口层**（``egress_denial`` ⇒ ``egress_denied``），
    它是本地判据拒绝里唯一一条 ``diagnose`` 真的产出得出来的路径 —— 另外几类
    （``unsafe_target``）在判据层就返回了，根本走不到治理层，这一格也就不该假装
    能测它们。
    """
    clock = _Clock()
    policy = AccessPolicy(circuit_setback_threshold=2)
    governor = AccessGovernor(policy, now=clock)
    url = "https://blocked.example/x"
    for _ in range(4):
        permit = governor.begin(url)
        assert permit.admit().allowed  # 治理层放行；拒的是判据层
        permit.record(
            diagnose(Observation(url=url, body_usable=False, egress_denial="目标是内网地址"))
        )
    report = governor.snapshot()[0]
    assert report.consecutive_setbacks == 0
    assert report.open_for_s == 0.0
    assert governor.begin(url).admit().allowed


# --- 第三组：§5 点名的那一条 -------------------------------------------------


def test_twenty_accounts_on_one_challenge_page_do_not_make_twenty_retry_suites() -> None:
    """**本切片的核心判据。**

    同一个来源、20 个账号、每一个都撞上挑战页。账按来源记，于是：

    * 前 3 个账号各发出 1 次（第 3 次挫折把熔断打开）；
    * 第 4 到第 20 个账号**一次都发不出去**，第一次 ``admit`` 就是 ``CIRCUIT_OPENED``；
    * 没有任何一个账号发出第二次 —— 挑战页是不可重试的那一类。

    对外发出的请求总数是 :attr:`AccessPolicy.circuit_setback_threshold`，不是 20。
    """
    clock = _Clock()
    policy = AccessPolicy(circuit_setback_threshold=3, max_attempts_per_fetch=4)
    governor = AccessGovernor(policy, now=clock)
    url = "https://one-host.example/listing"

    total_sent = 0
    blocked_by_circuit = 0
    for _account in range(20):
        sent, actions = _fetch(governor, url, _challenge(url))
        total_sent += sent
        assert sent <= 1, "挑战页不是「推迟之后再试」那一类，一个账号不该发第二次"
        if actions[0] is ActionTaken.CIRCUIT_OPENED:
            blocked_by_circuit += 1

    # 第 3 个账号的那一次把熔断打开，所以它的 ``record`` 也是 CIRCUIT_OPENED；
    # 第 4 到第 20 个账号的**第一次** ``admit`` 就已经是了。
    assert blocked_by_circuit == 18
    assert total_sent == 3, f"20 个账号对外发了 {total_sent} 次"
    assert governor.snapshot()[0].opened_count == 1


def test_account_scoped_books_would_send_twenty_times() -> None:
    """上面那条判据的**对照**：把账按账号记会是什么读数。

    这不是在测产品，是在证明上一条判据抓得住东西 —— 一个每个账号一本新账的实现
    同样会让「前 3 个账号」那三条断言成立，唯独总数是 20。没有这一遍对照，
    ``total_sent == 3`` 读起来与「恰好成立」没有区别。
    """
    url = "https://one-host.example/listing"
    naive_sent = 0
    for _account in range(20):
        clock = _Clock()
        per_account = AccessGovernor(AccessPolicy(circuit_setback_threshold=3), now=clock)
        sent, _actions = _fetch(per_account, url, _challenge(url))
        naive_sent += sent
    assert naive_sent == 20


def test_a_rate_limit_on_one_account_holds_back_the_next_account() -> None:
    """共享 origin 限流的**另一半**：等待期记在来源上，不记在账号上。

    一个账号收到的 ``Retry-After`` 会让另一个账号也等待 —— 因为它说的是
    「这个服务端现在不想收请求」，而那句话与是谁在敲没有关系。
    """
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(), now=clock)
    url = "https://one-host.example/a"

    sent, actions = _fetch(governor, url, _rate_limited(url, "120"))
    assert sent == 1
    assert actions[0] is ActionTaken.DEFERRED_BACKOFF

    other = governor.begin("https://one-host.example/b")
    admission = other.admit()
    assert not admission.allowed
    assert admission.action is ActionTaken.DEFERRED_BACKOFF
    assert admission.wait_s == pytest.approx(120.0)


def test_a_different_source_is_not_held_back() -> None:
    """来源级的账不互相连坐。"""
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(), now=clock)
    _fetch(governor, "https://a.example/x", _rate_limited("https://a.example/x", "600"))
    for _ in range(4):
        _fetch(governor, "https://a.example/y", _forbidden("https://a.example/y"))
    assert governor.begin("https://b.example/z").admit().allowed


def test_one_source_being_open_does_not_hold_back_another() -> None:
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(circuit_setback_threshold=2), now=clock)
    for _ in range(2):
        _fetch(governor, "https://a.example/x", _forbidden("https://a.example/x"))
    assert governor.snapshot()[0].open_for_s > 0.0
    assert not governor.begin("https://a.example/x").admit().allowed
    assert governor.begin("https://b.example/x").admit().allowed


# --- 第四组：有界 ------------------------------------------------------------


def test_one_fetch_cannot_exceed_its_attempt_budget() -> None:
    """一次访问最多发 ``max_attempts_per_fetch`` 次，5xx 也一样。

    这个界住在 permit 上而不是调用方的循环条件里：写在循环条件里的界，
    第一个 ``continue`` 就绕过去了。
    """
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(max_attempts_per_fetch=2), now=clock)
    url = "https://a.example/x"
    permit = governor.begin(url)

    assert permit.admit().allowed
    permit.record(_server_error(url))
    clock.advance(2.0)  # 第一次退避
    assert permit.admit().allowed
    permit.record(_server_error(url))
    third = permit.admit()
    assert not third.allowed
    assert third.action is ActionTaken.STOPPED_METHOD
    assert "预算" in third.detail


def test_a_permit_counts_a_send_that_never_comes_back() -> None:
    """``admit`` 放行的那一刻就记一次尝试，不等 ``record``。

    一次发出去之后崩在半路的请求仍然是对外发过的请求。把计数挂在 ``record`` 上，
    会让「发送后进程死掉」这一格不计费，而那恰好是重试最容易失控的场景。
    """
    governor = AccessGovernor(AccessPolicy(max_attempts_per_fetch=2), now=_Clock())
    permit = governor.begin("https://a.example/x")
    assert permit.admit().allowed
    assert permit.admit().allowed
    assert not permit.admit().allowed  # 一次都没有 record 过
    assert permit.attempts == 2


def test_a_403_is_not_retried_even_with_budget_left() -> None:
    """**403 不能靠重试变成 200。** 预算还剩也不重试。

    这一问必须由工具来问：写在调用方 ``if`` 里的实现，会在有人加一条新的终态
    根因时漏掉那一处，而漏掉的表现是「重试了一次本来不该重试的请求」。
    """
    governor = AccessGovernor(AccessPolicy(max_attempts_per_fetch=5), now=_Clock())
    url = "https://a.example/x"
    permit = governor.begin(url)
    assert permit.admit().allowed
    permit.record(_forbidden(url))
    second = permit.admit()
    assert not second.allowed
    assert "access_denied_unknown" in second.detail


def test_a_challenge_page_is_not_retried_either() -> None:
    governor = AccessGovernor(AccessPolicy(max_attempts_per_fetch=5), now=_Clock())
    url = "https://a.example/x"
    permit = governor.begin(url)
    permit.admit()
    permit.record(_challenge(url))
    assert not permit.admit().allowed


# --- 第四组之二：推迟 vs 停止 -------------------------------------------------
#
# 「记进来源账」与「这一次怎么处置」是两件事（见模块 docstring）。下面两张表把它
# 钉住：**推迟那一类必须真的重试一次，停止那一类必须一次都不重试**。两张表都不
# 只看诊断 —— 诊断的动作由 ``access.diagnose`` 决定，看得是这里改写之后的那一格。

_DEFERRED_CASES: list[tuple[str, object]] = [
    ("429 带 Retry-After", lambda url: _rate_limited(url, "30")),
    ("429 不带 Retry-After", lambda url: _rate_limited(url, None)),
    ("503", _server_error),
    (
        "连接超时",
        lambda url: diagnose(
            Observation(
                url=url, transport_error="timed out", transport_error_kind="ConnectTimeout"
            )
        ),
    ),
]

_STOPPED_CASES: list[tuple[str, object]] = [
    ("403", _forbidden),
    ("挑战页", _challenge),
    ("404", lambda url: diagnose(Observation(url=url, observed_status=404, body_usable=False))),
]


def _first_attempt(governor: AccessGovernor, url: str, reply):
    """发一次并把结果记下来，返回 ``(permit, 治理层改写后的诊断)``。"""
    permit = governor.begin(url)
    admission = permit.admit()
    assert admission.allowed, admission.detail
    return permit, permit.record(reply(url))


@pytest.mark.parametrize(
    "reply", [case for _label, case in _DEFERRED_CASES], ids=[label for label, _ in _DEFERRED_CASES]
)
def test_a_deferred_cause_is_retried_after_its_wait(reply) -> None:
    """推迟那一类：**马上不行，等过之后行**。两半都要，缺一半就成了两个不同的实现。

    只断言「等过之后可以」的实现可以在第一次就放行；只断言「马上不行」的实现可以
    永远不放行 —— 而后者报告上写的是 ``deferred_backoff``，读起来像是「我们会再试」。
    """
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(max_attempts_per_fetch=2), now=clock)
    url = "https://a.example/x"
    permit, diagnosis = _first_attempt(governor, url, reply)

    assert diagnosis.action is ActionTaken.DEFERRED_BACKOFF
    assert not permit.admit().allowed, "推迟那一类被立刻重试了 —— 退避那一半没有生效"
    clock.advance(3600.0)
    assert permit.admit().allowed, "等待期过了仍然不许重试 —— 有限重试那一半没有生效"


@pytest.mark.parametrize(
    "reply", [case for _label, case in _STOPPED_CASES], ids=[label for label, _ in _STOPPED_CASES]
)
def test_a_stopped_cause_is_never_retried(reply) -> None:
    """停止那一类：等多久都不重试。"""
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(max_attempts_per_fetch=2), now=clock)
    url = "https://a.example/x"
    permit, diagnosis = _first_attempt(governor, url, reply)

    assert diagnosis.action is ActionTaken.STOPPED_METHOD
    for waited in (0.0, 10.0, 3600.0, 86400.0):
        clock.advance(waited)
        assert not permit.admit().allowed, "停止那一类被重试了"


def test_the_two_halves_of_the_setback_table_are_a_partition() -> None:
    """推迟与停止加起来**恰好**是挫折集合，两边都不空。

    写成推导而不是第二份字面量，就是为了让「这里漏了一类」不可能发生：
    :data:`SETBACK_CAUSES` 里加一个成员却忘了在 ``record`` 里给它一个处置，
    会让它掉进 ``else``（处置跟着诊断走）—— 报告上看起来像一条正常的分支。
    """
    assert DEFERRED_CAUSES and STOPPED_CAUSES
    assert DEFERRED_CAUSES | STOPPED_CAUSES == SETBACK_CAUSES
    assert not (DEFERRED_CAUSES & STOPPED_CAUSES)


# --- 第五组：熔断的打开、半开与闭合 ------------------------------------------


def test_the_breaker_opens_only_after_the_threshold() -> None:
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(circuit_setback_threshold=3), now=clock)
    url = "https://a.example/x"
    for expected in (1, 2):
        _fetch(governor, url, _forbidden(url))
        report = governor.snapshot()[0]
        assert report.consecutive_setbacks == expected
        assert report.open_for_s == 0.0, "还没到阈值就打开了熔断"
    _fetch(governor, url, _forbidden(url))
    assert governor.snapshot()[0].open_for_s == pytest.approx(300.0)


def test_a_half_open_probe_is_bounded_and_its_failure_reopens_the_breaker() -> None:
    """冷却结束不是「重新放开」，是「放行 N 个探针」。"""
    clock = _Clock()
    policy = AccessPolicy(
        circuit_setback_threshold=2,
        circuit_cooldown_s=100.0,
        circuit_cooldown_max_s=1000.0,
        half_open_probes=1,
        max_attempts_per_fetch=8,
    )
    governor = AccessGovernor(policy, now=clock)
    url = "https://a.example/x"
    for _ in range(2):
        _fetch(governor, url, _forbidden(url))
    assert governor.snapshot()[0].open_for_s == pytest.approx(100.0)

    clock.advance(50.0)
    mid = governor.begin(url).admit()
    assert not mid.allowed and mid.action is ActionTaken.CIRCUIT_OPENED
    assert mid.wait_s == pytest.approx(50.0)

    clock.advance(60.0)  # 冷却已过
    probe = governor.begin(url)
    first = probe.admit()
    assert first.allowed and first.action is ActionTaken.HALF_OPEN_PROBE
    probe.record(_forbidden(url))
    # 探针失败 ⇒ 立即重新打开，冷却翻倍。
    assert governor.snapshot()[0].open_for_s == pytest.approx(200.0)
    assert governor.snapshot()[0].probes_left == 1

    # 冷却结束前一个都放不出去。
    assert not governor.begin(url).admit().allowed
    # 第二个探针额度只属于**下一次**打开，不是这一次 —— 冷却没到就还是熔断。
    clock.advance(200.0)
    again = governor.begin(url).admit()
    assert again.allowed and again.action is ActionTaken.HALF_OPEN_PROBE


def test_the_number_of_probes_let_through_is_exactly_the_policy_number() -> None:
    """冷却结束之后放行的探针数**恰好**是 policy 里那个数。

    这一格必须用 ``half_open_probes > 1``。把它设成 1 时，扣减那一句在任何一条判据
    里都**不会被执行到**：每次重新打开都会把计数拨回 policy 的值，而一个窗口里只问
    一次的实现看不出「减没减」—— 判据会绿，守的却是一个没跑过的分支。
    """
    clock = _Clock()
    policy = AccessPolicy(
        circuit_setback_threshold=2,
        circuit_cooldown_s=10.0,
        half_open_probes=3,
        max_attempts_per_fetch=10,
    )
    governor = AccessGovernor(policy, now=clock)
    url = "https://a.example/x"
    for _ in range(2):
        _fetch(governor, url, _forbidden(url))
    assert governor.snapshot()[0].probes_left == 3

    clock.advance(10.0)
    kinds = [governor.begin(url).admit().action for _ in range(4)]

    assert kinds[:3] == [ActionTaken.HALF_OPEN_PROBE] * 3
    assert kinds[3] is ActionTaken.CIRCUIT_OPENED, "第 4 个探针被放行了 —— 探针数没有减少"
    assert governor.snapshot()[0].probes_left == 0


def test_the_cooldown_grows_but_stops_at_the_ceiling() -> None:
    policy = AccessPolicy(
        backoff_factor=2.0, circuit_cooldown_s=100.0, circuit_cooldown_max_s=150.0
    )
    assert policy.cooldown_for(1) == 100.0
    assert policy.cooldown_for(2) == 150.0
    assert policy.cooldown_for(20) == 150.0
    assert policy.cooldown_for(0) == 100.0


def test_the_backoff_grows_and_stops_at_the_ceiling() -> None:
    policy = AccessPolicy(backoff_base_s=2.0, backoff_factor=2.0, backoff_max_s=10.0)
    assert [policy.backoff_for(n) for n in (0, 1, 2, 3, 4, 99)] == [0.0, 2.0, 4.0, 8.0, 10.0, 10.0]


def test_a_successful_probe_closes_the_breaker() -> None:
    clock = _Clock()
    policy = AccessPolicy(circuit_setback_threshold=2, circuit_cooldown_s=10.0)
    governor = AccessGovernor(policy, now=clock)
    url = "https://a.example/x"
    for _ in range(2):
        _fetch(governor, url, _forbidden(url))
    clock.advance(10.0)
    permit = governor.begin(url)
    admission = permit.admit()
    assert admission.action is ActionTaken.HALF_OPEN_PROBE
    refined = permit.record(_ok(url))
    assert refined.action is ActionTaken.PROCEEDED
    report = governor.snapshot()[0]
    assert report.open_for_s == 0.0
    assert report.consecutive_setbacks == 0
    # ``opened_count`` 不清零：它是一个历史事实，决定下次打开的冷却档位。
    assert report.opened_count == 1

    # 闭合必须**可观测**，而 ``open_for_s == 0`` 一个人扛不起这一格：探针恰好发生在
    # 冷却到期的那一刻，``open_until`` 本来就过去了，于是「有没有真的闭合」在那条
    # 读数上看不出来 —— 一个从不闭合的实现也报 0。下一个请求立刻发得出去，才是
    # 「闭合」这件事本身。
    following = governor.begin(url).admit()
    assert following.allowed and following.action is ActionTaken.PROCEEDED


def test_a_probe_that_hits_a_login_page_still_closes_the_breaker() -> None:
    """探针拿到一个**正常**的回答就够了，哪怕那个页面本身是 401。

    熔断问的是「这个来源还通不通」，不是「这个页面给不给我们看」。
    把 401 算成探针失败的实现，会让一个需要登录的站点永远处于半开状态。
    """
    clock = _Clock()
    governor = AccessGovernor(
        AccessPolicy(circuit_setback_threshold=2, circuit_cooldown_s=10.0), now=clock
    )
    url = "https://a.example/private"
    for _ in range(2):
        _fetch(governor, url, _forbidden(url))
    clock.advance(10.0)
    permit = governor.begin(url)
    assert permit.admit().action is ActionTaken.HALF_OPEN_PROBE
    refined = permit.record(diagnose(Observation(url=url, observed_status=401, body_usable=False)))
    assert governor.snapshot()[0].open_for_s == 0.0
    # 闭合是关于**来源**的，不是关于这次访问的：这一页仍然要给登录，所以动作还是
    # ``STOPPED_METHOD``。把它改写成 ``PROCEEDED`` 会让调用方以为正文可以用。
    assert refined.action is ActionTaken.STOPPED_METHOD
    assert refined.cause is SuspectedCause.AUTH_REQUIRED
    # 而来源那一侧的闭合照样成立：下一个请求发得出去。
    assert governor.begin(url).admit().allowed


# --- 第六组：Retry-After 的两种处置 ------------------------------------------


def test_retry_after_is_obeyed() -> None:
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(), now=clock)
    url = "https://a.example/x"
    _fetch(governor, url, _rate_limited(url, "120"))
    admission = governor.begin(url).admit()
    assert admission.wait_s == pytest.approx(120.0)
    clock.advance(120.0)
    assert governor.begin(url).admit().allowed


def test_a_later_shorter_retry_after_does_not_shorten_the_wait() -> None:
    """更近的等待期不得把已经记下的更远的那个拉近。

    拉近等于替服务端缩短了一次它明确要求的等待，而报告上「我们等了多久」会说
    一个比服务端要求更小的数。

    这一格**必须绕过 ``admit``**：等待期一记下，同一个来源上的下一个 ``admit``
    就会被拒，于是走 ``_fetch`` 的那个版本根本到不了 ``record``，``max`` 也就
    一次都没被执行到 —— 判据会绿，而它守的是一个没跑过的分支。真实场景里这条
    路径是**并发**：另一个请求已经在途，它带着一个更近的 ``Retry-After`` 回来。
    """
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(), now=clock)
    url = "https://a.example/x"
    _fetch(governor, url, _rate_limited(url, "120"))

    clock.advance(10.0)
    inflight = governor.begin("https://a.example/y")
    assert not inflight.admit().allowed, "等待期没挡住同一个来源上的第二次 admit"
    inflight.record(_rate_limited("https://a.example/y", "5"))  # 在途的那一个回来了

    admission = governor.begin("https://a.example/z").admit()
    assert admission.wait_s == pytest.approx(110.0), "更近的 Retry-After 把等待期拉近了"


def test_a_retry_after_beyond_the_ceiling_falls_back_to_bounded_backoff() -> None:
    """``Retry-After: 86400`` 是「今天别来」，不是「睡 24 小时」。

    它由 ``access.parse_retry_after`` 判成「服务端没有给一个我们愿意等的时间」，
    于是这里走的是**有界**退避。判据断言那个界真的在 —— 只断言「解析不出来」
    会让一个把它当成 86400 秒的实现通过。
    """
    clock = _Clock()
    policy = AccessPolicy(backoff_base_s=2.0, backoff_max_s=60.0)
    governor = AccessGovernor(policy, now=clock)
    url = "https://a.example/x"
    _fetch(governor, url, _rate_limited(url, "86400"))
    admission = governor.begin(url).admit()
    assert 0 < admission.wait_s <= policy.backoff_max_s


# --- 第七组：读数与坏 policy -------------------------------------------------


def test_the_report_names_why_a_source_is_holding_back() -> None:
    clock = _Clock()
    governor = AccessGovernor(AccessPolicy(), now=clock)
    url = "https://a.example/x"
    _fetch(governor, url, _rate_limited(url, "30"))
    report = governor.snapshot()[0]
    assert report.origin == "https://a.example"
    assert report.last_cause == SuspectedCause.RATE_LIMITED.value
    assert report.wait_s == pytest.approx(30.0)
    assert report.opened_count == 0


def test_the_snapshot_is_sorted_so_a_report_can_be_compared() -> None:
    governor = AccessGovernor(now=_Clock())
    for host in ("c.example", "a.example", "b.example"):
        governor.begin(f"https://{host}/x").admit()
    assert [report.origin for report in governor.snapshot()] == [
        "https://a.example",
        "https://b.example",
        "https://c.example",
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts_per_fetch": 0},
        {"backoff_base_s": -1.0},
        {"backoff_factor": 0.5},
        {"backoff_base_s": 10.0, "backoff_max_s": 1.0},
        {"circuit_setback_threshold": 0},
        {"circuit_cooldown_s": 0.0},
        {"circuit_cooldown_s": 100.0, "circuit_cooldown_max_s": 10.0},
        {"half_open_probes": 0},
    ],
)
def test_an_unbounded_policy_is_refused_at_construction(kwargs: dict) -> None:
    """**坏的界当场抛，不静默取一个「合理的」缺省。**

    静默兜底的后果是：写错一格的人拿到的是另一组界，而报告上那组界的名字与他写
    的名字一样。
    """
    with pytest.raises(ValueError):
        AccessPolicy(**kwargs)

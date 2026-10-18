"""§5 时间性模型的行为断言。

本文件保护两条命题，它们是一枚硬币的两面：

    **今天抓到一个旧文档，不等于今天验证了那个事实。**
    **这一轮没看到一个人，不等于这个人走了。**

第一条防的是**把旧的读成新的**（一份 2021 年的 PDF 落进库里就当成现任名单）。
第二条防的是反方向：把一次**观测失败**读成一条关于人的事实。两者的共同点是
「数据里长得一模一样」—— 前者是 ``fetched_at`` 与 ``source_published_at`` 同形，
后者是「没看到」与「离开了」同形。

其余测试都是这两条命题在各条路径上的展开。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.domain.temporal import (
    Currentness,
    Freshness,
    FreshnessPolicy,
    FreshnessReason,
    TemporalAssertion,
    assertion_id,
    evaluate_freshness,
)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _policy() -> FreshnessPolicy:
    return FreshnessPolicy(
        policy_version="probe.v1",
        current_within_days=365,
        historical_after_days=1095,
    )


def _assertion(**overrides) -> TemporalAssertion:
    """构造一条断言。``currentness`` 刻意留默认 —— 一条刚观测到的断言，
    还没跑过 :func:`evaluate_freshness`，诚实的取值就是「尚未判定」。
    """
    base = dict(
        assertion_id="a" * 32,
        subject_ref="account:probe-001",
        predicate="holds_role",
        value="grounds manager",
        observed_at=NOW,
    )
    base.update(overrides)
    return TemporalAssertion(**base)


def test_a_freshly_observed_assertion_is_unknown_until_evaluated() -> None:
    """默认值不是任一具体结论。

    若它默认成 ``current``，「忘了判」就会静默变成「当前成立」；
    若它没有默认值，调用方只能随手填一个 —— 那同样是猜。
    """
    a = _assertion(source_published_at=NOW - timedelta(days=1))
    assert a.currentness is Currentness.UNKNOWN


# ─────────────────────── 模型自身的硬约束 ───────────────────────


def test_current_without_any_time_anchor_is_rejected() -> None:
    """本模块最重要的一条不变量。

    声称「当前的」，却既不知道来源何时发布、也从未重新验证 —— 这正是把
    五年前的 PDF 读成今日事实的那个状态，所以它必须在构造期就不可能成立。
    """
    with pytest.raises(ValueError, match="没有任何时间锚点"):
        _assertion(currentness=Currentness.CURRENT)


def test_current_with_source_publication_is_allowed() -> None:
    a = _assertion(
        currentness=Currentness.CURRENT,
        source_published_at=NOW - timedelta(days=10),
    )
    assert a.currentness is Currentness.CURRENT


def test_current_with_only_a_reverification_time_is_allowed() -> None:
    """重新验证时刻本身就是锚点 —— 来源可能压根不写发布时间。"""
    a = _assertion(
        currentness=Currentness.CURRENT,
        last_verified_at=NOW - timedelta(days=3),
    )
    assert a.currentness is Currentness.CURRENT


def test_observation_cannot_precede_publication() -> None:
    with pytest.raises(ValueError, match="不可能在它发布之前就观测到它"):
        _assertion(source_published_at=NOW + timedelta(days=1))


def test_the_impossible_time_predicate_has_one_definition() -> None:
    """校验器与调用点读的是**同一句判据**，所以不可能给出不同答案。

    这条测试存在的理由是一个具体的失败：``application/freshness`` 要在构造断言
    **之前**问一次「这一刻成立吗」（它要的是一个可计数的结果，不是一次异常），
    而它不能把判据抄一份。抄一份的后果不是「两边不一致会红」—— 是**不会红**：
    校验器按自己那份判它不合法，调用点按自己那份判它合法，于是一行不可能的
    时刻要么继续抛（fail-soft 没做成），要么安静地带着未来的 ``age_days`` 落库。

    所以这里比的是两者的**边界行为**，三种输入各一次：早于、等于、晚于。
    """
    observed = NOW
    cases = {
        "past": (NOW - timedelta(days=1), ""),
        "same instant": (NOW, ""),
        # 边界取等号：同一瞬间既不算早也不算晚 —— 本地缓存、秒级时间戳解析都会
        # 落到这里，判成「不可能」会拒掉一批本来正常的材料。
        "future": (NOW + timedelta(days=1), "不可能在它发布之前就观测到它"),
        "absent": (None, ""),
    }

    for label, (published, expected) in cases.items():
        reason = TemporalAssertion.impossible_source_time(published, observed)
        assert (expected in reason) if expected else (reason == ""), label

        try:
            _assertion(source_published_at=published)
        except ValueError as exc:
            assert reason and reason in str(exc), label
        else:
            assert reason == "", f"{label}：校验器放行了，而判据说不可能"


def test_an_impossible_time_is_not_actionable_and_is_not_unknown() -> None:
    """这一档既不支撑判定，**也不与** ``UNKNOWN`` 同格 —— 两者要分开数。

    ``UNKNOWN`` 是「材料本来就缺时刻」，``INVALID_SOURCE_TIME`` 是「有一个时刻，
    而它不可能是对的」。合成一格的话，一次解析错误会在报告里变成一片「时间不明」，
    而没有人会去看一个材料缺项。

    这一档**不由** :func:`evaluate_freshness` 产出（它手里没有可判的断言 —— 那条
    断言根本构造不出来），所以这里直接构造那一格结论来问 ``is_actionable``。
    """
    impossible = Currentness.INVALID_SOURCE_TIME

    assert impossible is not Currentness.UNKNOWN
    assert impossible is not Currentness.STALE, "不是「时间过去了」，恰恰相反"

    conclusion = Freshness(
        assertion_id="a" * 32,
        currentness=impossible,
        reason=FreshnessReason.INVALID_SOURCE_TIME,
        policy_version="probe.v1",
        evaluated_at=NOW,
    )

    assert conclusion.is_actionable is False
    assert conclusion.age_days is None, "算不出的年纪不许拿一个负数顶上"


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(ValueError, match="naive datetime"):
        _assertion(observed_at=datetime(2026, 9, 22, 12, 0))


def test_conflicting_requires_a_named_counterpart() -> None:
    with pytest.raises(ValueError, match="必须能指出和谁冲突"):
        _assertion(currentness=Currentness.CONFLICTING)


def test_empty_validity_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="有效区间是空的"):
        _assertion(
            valid_from=NOW - timedelta(days=1),
            valid_to=NOW - timedelta(days=2),
        )


def test_policy_windows_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="两档窗口不可区分"):
        FreshnessPolicy(
            policy_version="probe.v1",
            current_within_days=400,
            historical_after_days=400,
        )


def test_assertion_id_separates_two_observations_of_the_same_wording() -> None:
    """同一句话在两个时刻读到，是两条断言。

    合并它们会让「她当年是」悄悄变成「她现在也是」—— 那正是 §5 要防的事。
    """
    first = assertion_id(
        subject_ref="account:probe-001",
        predicate="holds_role",
        value="grounds manager",
        observed_at=NOW - timedelta(days=1800),
    )
    second = assertion_id(
        subject_ref="account:probe-001",
        predicate="holds_role",
        value="grounds manager",
        observed_at=NOW,
    )
    assert first != second


# ─────────────────────── evaluate_freshness ───────────────────────


def test_recent_publication_is_current() -> None:
    verdict = evaluate_freshness(
        _assertion(source_published_at=NOW - timedelta(days=30)),
        _policy(),
        now=NOW,
    )
    assert verdict.currentness is Currentness.CURRENT
    assert verdict.reason is FreshnessReason.WITHIN_POLICY_WINDOW
    assert verdict.age_days == 30


def test_the_2021_document_is_not_current_today() -> None:
    """§5 的头号例子，逐字复现。

    一份五年前的文档，今天被抓到。``observed_at`` 是今天，
    但 ``source_published_at`` 是五年前 —— 结论必须是它不构成当前的验证。
    """
    verdict = evaluate_freshness(
        _assertion(
            source_published_at=datetime(2021, 5, 4, tzinfo=timezone.utc),
            observed_at=NOW,
        ),
        _policy(),
        now=NOW,
    )
    assert verdict.currentness is not Currentness.CURRENT
    assert verdict.reason is FreshnessReason.BEYOND_POLICY_WINDOW
    assert verdict.age_days is not None and verdict.age_days > 365 * 4


def test_a_source_declared_ended_period_is_historical_rather_than_stale() -> None:
    """历史与过期是两种不同的「旧」，因为下一步动作不同。

    来源自己写了区间的终点（2018–2021 年任场地主管）—— 那是一条历史事实，
    按「那时」读即可，**不需要**重新验证。判据是来源声明的区间，
    不是谁给它贴的标签。
    """
    verdict = evaluate_freshness(
        _assertion(
            source_published_at=datetime(2021, 5, 4, tzinfo=timezone.utc),
            valid_to=datetime(2021, 5, 4, tzinfo=timezone.utc),
        ),
        _policy(),
        now=NOW,
    )
    assert verdict.currentness is Currentness.HISTORICAL
    assert verdict.reason is FreshnessReason.DESCRIBES_A_PAST_STATE


def test_a_recorded_historical_verdict_survives_reevaluation() -> None:
    """重新求值不得把「历史」降级成「过期」。

    历史是一条**更强**的结论（来源当时说的是对的），丢掉它等于丢信息。
    这是 :func:`evaluate_freshness` 读 :attr:`TemporalAssertion.currentness`
    的两种情形之一。
    """
    verdict = evaluate_freshness(
        _assertion(
            source_published_at=datetime(2021, 5, 4, tzinfo=timezone.utc),
            currentness=Currentness.HISTORICAL,
        ),
        _policy(),
        now=NOW,
    )
    assert verdict.currentness is Currentness.HISTORICAL


def test_a_recorded_current_verdict_does_not_renew_itself() -> None:
    """反面：上次判成 ``current``，不构成这次判成 ``current`` 的任何理由。

    这条钉住 :func:`evaluate_freshness` docstring 里「它从不读 CURRENT」
    那一半。若本函数回头认自己上一次的产出，一个过期的判定就会靠自我指认
    一直续期下去 —— 而那正好是 §5 要防的那类错误读法。
    """
    verdict = evaluate_freshness(
        _assertion(
            source_published_at=datetime(2019, 1, 1, tzinfo=timezone.utc),
            currentness=Currentness.CURRENT,
        ),
        _policy(),
        now=NOW,
    )
    assert verdict.currentness is Currentness.STALE


def test_reverification_outranks_a_publication_date() -> None:
    """2019 年发布、2026 年仍在现任名单上的岗位，是当前的。

    重新验证是比发布时间更强的证据 —— 这也是 ``last_verified_at``
    必须与 ``observed_at`` 分开的那个理由。
    """
    verdict = evaluate_freshness(
        _assertion(
            source_published_at=datetime(2019, 1, 1, tzinfo=timezone.utc),
            last_verified_at=NOW - timedelta(days=20),
        ),
        _policy(),
        now=NOW,
    )
    assert verdict.currentness is Currentness.CURRENT
    assert verdict.reason is FreshnessReason.RECENTLY_REVERIFIED


def test_observation_alone_never_counts_as_reverification() -> None:
    """反面：只重抓、不重验，不得升级为 current。

    这条与上一条是一对。上一条说「重新验证可以胜过发布时间」，
    这一条说「重新抓取不能」。``observed_at`` 是 NOW 而
    ``last_verified_at`` 缺席 —— 结论必须仍不是 current。
    """
    verdict = evaluate_freshness(
        _assertion(
            source_published_at=datetime(2019, 1, 1, tzinfo=timezone.utc),
            observed_at=NOW,
            last_verified_at=None,
        ),
        _policy(),
        now=NOW,
    )
    assert verdict.currentness is Currentness.STALE


def test_conflict_is_decided_before_recency() -> None:
    """一件事有两个不相容的版本时，先去解决冲突，不要用「哪个更新」盖过去。"""
    verdict = evaluate_freshness(
        _assertion(
            source_published_at=NOW - timedelta(days=1),
            contradicted_by=["b" * 32],
        ),
        _policy(),
        now=NOW,
    )
    assert verdict.currentness is Currentness.CONFLICTING
    assert verdict.reason is FreshnessReason.CONTRADICTED


def test_no_time_anchor_is_unknown_not_current() -> None:
    verdict = evaluate_freshness(
        _assertion(source_published_at=None, last_verified_at=None),
        _policy(),
        now=NOW,
    )
    assert verdict.currentness is Currentness.UNKNOWN
    assert verdict.reason is FreshnessReason.NO_TIME_ANCHOR


def test_unknown_and_conflicting_are_not_actionable() -> None:
    """歧义与未知不得支撑业务判定 —— 宁可留空，不可猜。"""
    for value in (Currentness.UNKNOWN, Currentness.CONFLICTING):
        verdict = evaluate_freshness(
            _assertion(
                source_published_at=None if value is Currentness.UNKNOWN else NOW,
                contradicted_by=["b" * 32] if value is Currentness.CONFLICTING else [],
            ),
            _policy(),
            now=NOW,
        )
        assert verdict.currentness is value
        assert verdict.is_actionable is False


def test_current_and_historical_are_actionable() -> None:
    current = evaluate_freshness(
        _assertion(source_published_at=NOW - timedelta(days=5)), _policy(), now=NOW
    )
    historical = evaluate_freshness(
        _assertion(
            source_published_at=datetime(2021, 5, 4, tzinfo=timezone.utc),
            valid_to=datetime(2021, 5, 4, tzinfo=timezone.utc),
        ),
        _policy(),
        now=NOW,
    )
    assert (current.currentness, current.is_actionable) == (Currentness.CURRENT, True)
    assert (historical.currentness, historical.is_actionable) == (
        Currentness.HISTORICAL,
        True,
    )


def test_a_fact_that_outlived_its_window_is_not_actionable() -> None:
    """``STALE`` 也不支撑业务判定 —— 而它的理由与另外三条都不同。

    它不是歧义，不是未知，也不是一次观测失败：这份材料曾经说的是对的，而且
    它没说什么时候结束。正因为如此它才**不能**被读成今天成立 ——
    「曾经是当前的」与「今天是当前的」是两句话，这一格手里只有前一句。

    补在 §9·4 的变异检查之后。``mutate-s9-4.py`` 的 M5 把
    :attr:`Freshness.is_actionable` 改成「只要不是 ``UNKNOWN`` 就算」，
    转红的**只有两条**（``UNKNOWN``/``CONFLICTING`` 那一条、``NOT_SEEN``
    那一条）—— 也就是说 ``STALE`` 那一格此前**没有任何一条测试量过**：
    六个取值里只有它没有一句断言提过 ``is_actionable``。它不冲突、不未知，
    只是旧，而「旧」在报告上读起来像一个程度问题 —— 这正是
    :class:`Currentness` 的说明里要先驳掉的那句（「区别不是程度，是原因」）。
    加上这一条，六个取值的可支撑性才各自都有执行器。
    """
    verdict = evaluate_freshness(
        _assertion(source_published_at=datetime(2019, 1, 1, tzinfo=timezone.utc)),
        _policy(),
        now=NOW,
    )

    assert verdict.currentness is Currentness.STALE
    assert verdict.is_actionable is False


# --- §9·4 反方向的第二个错误：没找到一个人，不等于这个人走了 ---------------------
#
# 本文件开头两条命题里的第一条（「今天抓到一个旧文档，不等于今天验证了那个事实」）
# 守的是**把旧的读成新的**。这一节守的是方向相反的那一半，而它在数据里更容易发生：
# 「这一轮没看到」与「这个人离开了」在证据上同形，所以第一个实现复查的人如果
# 手边没有一档诚实的取值，就会顺手挑一个更近的 —— ``STALE``，或者一个
# ``valid_to`` 日期 —— 而挑出来的那个会是错的。


def test_a_recheck_that_did_not_see_the_route_is_not_a_departure() -> None:
    """复查时没再看到它 → ``NOT_SEEN``，而**不是**任何形式的「结束了」。

    这条断言还得钉住顺序：一份**上个月**发布、仍在窗口内的材料，复查时没看到它，
    绝不能被时间判据抢先判成 ``CURRENT`` —— 那等于说「它还在」，而这正是这次
    复查没有证实的那一句。
    """
    verdict = evaluate_freshness(
        _assertion(source_published_at=NOW - timedelta(days=30)),
        _policy(),
        now=NOW,
        seen_on_recheck=False,
    )

    assert verdict.currentness is Currentness.NOT_SEEN
    assert verdict.reason is FreshnessReason.NOT_SEEN_ON_RECHECK
    assert verdict.currentness is not Currentness.HISTORICAL, "「没看到」不是「来源说了终点」"
    # 它不假装自己没有时间锚点：观测失败与时间无关，年龄照实算。
    assert verdict.age_days == 30


def test_not_seen_is_not_actionable_in_either_direction() -> None:
    """它不支撑任何关于这个人的判定 —— 包括「他还在」与「他走了」两个方向。"""
    verdict = evaluate_freshness(
        _assertion(source_published_at=NOW - timedelta(days=30)),
        _policy(),
        now=NOW,
        seen_on_recheck=False,
    )

    assert verdict.is_actionable is False


def test_a_source_declared_end_wins_over_a_failed_recheck() -> None:
    """来源说了终点，而复查时没看到它 —— 材料里那条更强，判 ``HISTORICAL``。

    这两种情形在一次**成功**的复查里本来就该同时出现：来源写的是
    「2018–2021 年任场地主管」，那一页今天仍然那么写，而今天「看不到这个人现任」
    恰恰就是它那句话的意思。让观测失败盖过来源声明，等于用「我没看到」删掉一条
    材料事实 —— 而这一档还会被带进下一轮（见 ``_carried_currentness``），
    所以删掉的东西不会自己回来。
    """
    verdict = evaluate_freshness(
        _assertion(
            source_published_at=datetime(2021, 5, 4, tzinfo=timezone.utc),
            valid_to=datetime(2021, 5, 4, tzinfo=timezone.utc),
        ),
        _policy(),
        now=NOW,
        seen_on_recheck=False,
    )

    assert verdict.currentness is Currentness.HISTORICAL
    assert verdict.reason is FreshnessReason.DESCRIBES_A_PAST_STATE


def test_seeing_it_again_is_not_verifying_it() -> None:
    """``seen_on_recheck=True`` **不产生任何新结论** —— 两个方向都是原样。

    这是本仓那条分工的直接后果：``observed_at``（又看到一次）与
    ``last_verified_at``（确认它仍然成立）不是同一件事。如果「又看到了」能顶替
    验证，那么每一次抓取都会把一条两年前的材料续期成当前的 —— 正是本文件开头
    那条命题要防的事，只是换了个入口。
    """
    old = _assertion(source_published_at=datetime(2021, 5, 4, tzinfo=timezone.utc))
    fresh = _assertion(source_published_at=NOW - timedelta(days=30))

    for assertion in (old, fresh):
        without = evaluate_freshness(assertion, _policy(), now=NOW)
        with_recheck = evaluate_freshness(
            assertion, _policy(), now=NOW, seen_on_recheck=True
        )
        assert with_recheck == without, "「又看到了」不得改变任何结论"

    assert evaluate_freshness(old, _policy(), now=NOW).currentness is Currentness.STALE


def test_not_doing_a_recheck_is_neither_seeing_nor_not_seeing() -> None:
    """缺省 ``None`` = **这一轮没有做复查**，而不是「看到了」。

    取值的两个方向都要钉住：没做复查时，一份新材料的结论是 ``CURRENT``、一份旧
    材料是 ``STALE`` —— 也就是说这一格**不参与**，而不是替每一行假装我们刚确认
    过它还在。

    本函数**分辨不出** ``None`` 与 ``True``（上一条测试钉的就是「又看到了」不产生
    结论，所以两者在结论上同效），所以缺省取谁在行为上一样 —— 取 ``None`` 是为了
    日志：``True`` 在那里是一句我们没做过的声明。真正会改结论的是 ``False``：
    那会把每一次运行都读成「整张表都没看到」。
    """
    fresh = evaluate_freshness(
        _assertion(source_published_at=NOW - timedelta(days=30)), _policy(), now=NOW
    )
    old = evaluate_freshness(
        _assertion(source_published_at=datetime(2021, 5, 4, tzinfo=timezone.utc)),
        _policy(),
        now=NOW,
    )

    assert fresh.currentness is Currentness.CURRENT
    assert old.currentness is Currentness.STALE


def test_not_seen_needs_no_time_anchor() -> None:
    """「复查时没看到」是一条不依赖时刻的结论。

    这一条钉的是顺序：``NOT_SEEN`` 的分支排在「没有时间锚点 → ``UNKNOWN``」
    之前。反过来写，一次**真的**发生过、也确实被记下来的观测失败会被读成
    「判据没能开工」—— 而那是另一句话，下游的动作也不同（一个回去找那一页，
    一个去补时间）。
    """
    verdict = evaluate_freshness(
        _assertion(source_published_at=None, last_verified_at=None),
        _policy(),
        now=NOW,
        seen_on_recheck=False,
    )

    assert verdict.currentness is Currentness.NOT_SEEN
    assert verdict.age_days is None, "没有锚点就是 None，不许拿 now 顶上"

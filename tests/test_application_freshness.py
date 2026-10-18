"""``application/freshness`` —— 把一行的时刻判成结论，并写回那一行。

这个文件守的是模块 docstring 里那几句**断言**。它们都是那种「听着很对、但没有任何
执行器」的话，所以每一条都在下面有一个具体的测试：

* 没有时限声明 ⇒ **失败**，不落缺省天数（``policy_of``）；
* 没有观测时刻的行 ⇒ 判据**不开工**，且不许拿 ``now`` 顶上（``NO_TIME_ANCHOR``）；
* 结论写回时**真的过了一遍行级判据** —— 这一条是 ``rebuild`` 存在的全部理由，
  所以它必须能证明 ``model_copy`` 会漏、而 ``rebuild`` 不漏；
* ``CONFLICTING`` **今天没有生产者**，这是刻意的而不是遗漏。

判定本身（哪个取值、为什么）在 ``tests/test_domain_temporal.py``。这里不重复测它。
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from marketops.application.metrics import AccountMetrics, RunReport
from marketops.application.freshness import (
    FreshnessOutcome,
    assertion_for,
    judge,
    policy_of,
    rebuild,
)
from marketops.domain.entities import ContactRoute, RouteOpportunity
from marketops.domain.enums import Cohort
from marketops.domain.errors import ConfigError
from marketops.domain.profile import ProfileBundle
from marketops.domain.temporal import (
    Currentness,
    Freshness,
    FreshnessPolicy,
    FreshnessReason,
)

WORKSPACE = "test-workspace"
ACCOUNT_ID = "a1"

#: 一个固定的「现在」。测试里的时刻全部由它推出来 —— 用一个真实时钟会让这些判据
#: 在某个闰日或时区变更时自己变红，而红的原因与它们要守的东西无关。
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def _policy(*, calibrated: bool = False, current_within_days: int = 180) -> FreshnessPolicy:
    return FreshnessPolicy(
        policy_version="test-v1",
        current_within_days=current_within_days,
        historical_after_days=730,
        calibrated=calibrated,
    )


def _route(**overrides) -> ContactRoute:
    payload = {
        "workspace": WORKSPACE,
        "account_id": ACCOUNT_ID,
        "email": "grounds@example.co.uk",
        "email_domain": "example.co.uk",
        "evidence_ids": ["e1"],
    }
    payload.update(overrides)
    return ContactRoute(**payload)


def _opportunity(**overrides) -> RouteOpportunity:
    payload = {
        "workspace": WORKSPACE,
        "account_id": ACCOUNT_ID,
        "role_title": "Grounds Manager",
        "evidence_ids": ["e1"],
    }
    payload.update(overrides)
    return RouteOpportunity(**payload)


class _Bundle:
    """只带 ``profile_id`` 与 ``freshness`` 两格的 bundle，其余字段不构造。

    ``policy_of`` 只读这两格，而 ``ProfileBundle`` 的其余字段都要从一份真实的
    profile 推出来（规则、词表、场景文件、版本）。为了读一格属性去造一整套，
    会让这个文件红在 profile 的别处 —— 那时候改的就不是这里的东西了。
    """

    def __init__(self, profile_id: str, freshness: FreshnessPolicy | None) -> None:
        self.profile_id = profile_id
        self.freshness = freshness


# --- 没有声明就是一次失败 ------------------------------------------------------


def test_absent_declaration_fails_closed() -> None:
    """不声明时限 ⇒ ``ConfigError``，**不落一组缺省天数**。

    缺省的那组数字（180/730 之类）看起来完全合理，于是每一行产出都会显得是按某个
    业务标准算出来的 —— 而没有任何人认过那组数字。
    """
    with pytest.raises(ConfigError) as excinfo:
        policy_of(_Bundle("no-declaration", None))

    # 断言的是「这句话点名了是哪个 profile，并指出了声明该写在哪」—— 不是
    # 「消息里出现了某个词」。后者会因为提示语改一个字而失效。
    message = str(excinfo.value)
    assert "no-declaration" in message
    assert "freshness" in message
    assert "profile.yaml" in message


def test_declared_policy_is_returned_as_is() -> None:
    policy = _policy(calibrated=True)
    assert policy_of(_Bundle("declared", policy)) is policy


def test_absent_declaration_has_no_default_object() -> None:
    """``ProfileBundle.freshness`` 的缺省是 ``None``，**不是一份能用的策略**。

    上面那条测的是 ``policy_of`` 的反应，这条测的是它**为什么**反应得起来：缺省若
    是一组天数，那份 bundle 会静默地拿到一份看起来完全合理的口径，而每一行产出都会
    显得是按它算出来的。
    """
    defaults = {item.name: item.default for item in fields(ProfileBundle)}

    assert defaults["freshness"] is None


# --- 没有时刻锚点的行不判 ------------------------------------------------------


def test_row_without_an_anchor_is_not_judged() -> None:
    """没有 ``first_seen_at`` ⇒ ``NO_TIME_ANCHOR``，取值为 ``None``，且行**原样返回**。

    不许拿 ``now`` 或 ``fetched_at`` 顶上：那会让一条本来无从复核的行看起来判过了，
    而结论是「stale」—— 那是**另一个结论**。
    """
    route = _route()
    assert route.first_seen_at is None

    judgement = judge(route, _policy(), now=NOW)

    assert judgement.outcome is FreshnessOutcome.NO_TIME_ANCHOR
    assert judgement.currentness is None
    assert judgement.row.freshness is None
    assert judgement.row is route, "没判就不该重建成新行"


def test_opportunities_are_judged_the_same_way() -> None:
    """两族行走的是同一套判据 —— 这一条防的是「只接了路线那一半」。"""
    opportunity = _opportunity()
    assert judge(opportunity, _policy(), now=NOW).outcome is FreshnessOutcome.NO_TIME_ANCHOR

    seen = _opportunity(first_seen_at=NOW - timedelta(days=1))
    judgement = judge(seen, _policy(), now=NOW)
    assert judgement.outcome is FreshnessOutcome.JUDGED
    assert judgement.row.freshness is not None


def test_assertion_for_reads_the_same_id_the_row_validator_checks() -> None:
    """断言 id 取自 ``row.expected_assertion_id``，**不在这里重算一份**。

    重算的后果很具体：两边各自自洽、只是判的不是同一条断言，于是校验永远通过、
    结论永远挂错行。这条测试拿行自己算出的那个 id 直接比。
    """
    route = _route(first_seen_at=NOW - timedelta(days=10))

    assertion = assertion_for(route)

    assert assertion is not None
    assert assertion.assertion_id == route.expected_assertion_id
    assert assertion.subject_ref == route.assertion_subject_ref
    assert assertion.predicate == route.assertion_predicate
    assert assertion.observed_at == route.first_seen_at


def test_assertion_for_is_none_without_an_anchor() -> None:
    assert assertion_for(_route()) is None


# --- 一个不可能的来源时刻：判它，但判的是一句关于材料的话 ----------------------


def test_a_future_publish_time_is_judged_not_raised() -> None:
    """来源自述的发布时刻晚于观测时刻 ⇒ **判出这一档**，不抛，也不钳日期。

    这条测试守的是 §5 的 fail-soft，而它的失败模式很具体：``bind`` 写下一行带
    未来日期的材料（页面上写了排期、或年份被解析错），判据在构造断言时抛
    ``ValueError``，而调用点只接 ``MarketOpsError`` —— 于是**整轮研究死在一个
    坏页面上**，报告里连「哪一页」都没有。

    三种偷懒的修法各自错在哪：

    * 抛出去让调用点接 —— 那一页还是会让整轮停（只不过停在一个更好的位置）；
    * 静默丢掉这一行 —— 报告上少一行，看不出为什么；
    * 把日期钳到今天 —— 最坏的一种：一行算不出年纪的证据会变成刚刚验证过的。

    所以这里要的是：结论写回那一行、理由就是判据原文、``is_actionable`` 为假。
    """
    route = _route(
        first_seen_at=NOW - timedelta(days=1),
        source_published_at=NOW + timedelta(days=30),
    )

    judgement = judge(route, _policy(), now=NOW)

    assert judgement.outcome is FreshnessOutcome.INVALID_SOURCE_TIME
    assert judgement.currentness is Currentness.INVALID_SOURCE_TIME
    assert judgement.row is not route, "结论要贴回行上，所以是新的一行"
    assert judgement.row.source_published_at == NOW + timedelta(days=30), (
        "日期**原样留着** —— 钳过的日期会让这一行看起来是一份正常材料"
    )

    conclusion = judgement.row.freshness
    assert conclusion is not None
    assert conclusion.assertion_id == route.expected_assertion_id, (
        "结论挂的是这一行自己算出的那个断言 id，否则行级判据会红"
    )
    assert conclusion.reason is FreshnessReason.INVALID_SOURCE_TIME
    assert conclusion.age_days is None
    assert conclusion.is_actionable is False


def test_the_impossible_time_is_counted_apart_from_a_missing_anchor() -> None:
    """它算「判过了」，「没有锚点」不算 —— 两者的下一步动作不同。

    报告上必须分得开：「缺时刻」要等材料，「时刻不可能」要有人去看那个解析器。
    合成一格的话，一次坏解析会安静地变成一片「材料缺时间」。
    """
    metrics = AccountMetrics(account_id=ACCOUNT_ID, cohort=Cohort.KNOWN_SUCCESS)
    metrics.record_freshness(
        judge(
            _route(
                first_seen_at=NOW - timedelta(days=1),
                source_published_at=NOW + timedelta(days=30),
            ),
            _policy(),
            now=NOW,
        )
    )

    assert metrics.freshness_judged == 1
    assert metrics.freshness_without_a_time_anchor == 0
    assert metrics.currentness == {"invalid_source_time": 1}


# --- 判一次、写回那一行 --------------------------------------------------------


def test_judged_row_carries_a_conclusion_about_itself() -> None:
    """有观测时刻、但来源没说发布时间：**判了**，而答案是 ``unknown``。

    这一条把两种「没有结论」摆在一起，它们必须不同：

    * ``JUDGED`` + ``currentness=unknown`` —— 判据跑完了，它说「时间锚点不足」；
    * ``NO_TIME_ANCHOR`` + 无取值 —— 判据**没能开工**（见上一条测试）。

    结论挂在这一行自己身上（id 对得上），并且 ``is_actionable`` 为假 —— 一条
    unknown 的断言撑不起任何业务判定。
    """
    route = _route(first_seen_at=NOW - timedelta(days=10))

    judgement = judge(route, _policy(), now=NOW)

    assert judgement.outcome is FreshnessOutcome.JUDGED
    fresh = judgement.row.freshness
    assert fresh is not None
    assert fresh.currentness is Currentness.UNKNOWN
    assert fresh.reason is FreshnessReason.NO_TIME_ANCHOR
    assert fresh.assertion_id == route.expected_assertion_id
    assert fresh.policy_version == "test-v1"
    assert fresh.is_actionable is False
    assert judgement.currentness is Currentness.UNKNOWN


def test_conclusion_says_whether_the_policy_was_calibrated() -> None:
    """未标定的口径必须**落在每一份结论上**，不只落在策略对象上。

    版本号答「按什么算的」，这一格答「那套东西当时算不算获批口径」—— 同一个版本号
    下的数字被批准之后版本号不必变，而此前算出的每一条结论的解读都变了。
    """
    route = _route(first_seen_at=NOW - timedelta(days=10))

    uncalibrated = judge(route, _policy(calibrated=False), now=NOW).row.freshness
    calibrated = judge(route, _policy(calibrated=True), now=NOW).row.freshness

    assert uncalibrated is not None and calibrated is not None
    assert uncalibrated.policy_calibrated is False
    assert calibrated.policy_calibrated is True
    # 两份策略的版本号刻意相同 —— 差异只在那一位「有没有人认过」上。
    assert uncalibrated.policy_version == calibrated.policy_version


def test_old_source_is_stale_not_current() -> None:
    """窗口外的行判成 ``stale`` —— 「今天抓到一份旧文档」不构成「今天验证了它」。"""
    route = _route(
        first_seen_at=NOW,
        source_published_at=NOW - timedelta(days=900),
    )

    fresh = judge(route, _policy(), now=NOW).row.freshness

    assert fresh is not None
    assert fresh.currentness is Currentness.STALE
    assert fresh.reason is FreshnessReason.BEYOND_POLICY_WINDOW
    assert fresh.age_days == 900


def test_historical_is_carried_into_the_next_evaluation() -> None:
    """已判为历史的断言不因重新求值而降级成 ``stale``。

    历史是一条更强的结论（来源当时说的是对的），而这条搬运在
    ``_carried_currentness`` 里 —— 它只带这一种取值进来。
    """
    historical = Freshness(
        assertion_id="x" * 32,
        currentness=Currentness.HISTORICAL,
        reason=FreshnessReason.DESCRIBES_A_PAST_STATE,
        policy_version="test-v0",
        evaluated_at=NOW - timedelta(days=1),
    )
    route = _route(first_seen_at=NOW, source_published_at=NOW - timedelta(days=900))
    # 行的判据要求那一格是关于**这一行**的，所以按本行的 id 换掉之后才贴得上去。
    carried = historical.model_copy(update={"assertion_id": route.expected_assertion_id})
    route = rebuild(route, freshness=carried)

    fresh = judge(route, _policy(), now=NOW).row.freshness

    assert fresh is not None
    assert fresh.currentness is Currentness.HISTORICAL


def test_current_is_never_carried_into_the_next_evaluation() -> None:
    """上一次判成 ``current`` **不构成**这次也是 current 的任何理由。

    这一条的问法刻意选在**最痛的那一行**上：只有观测时刻（``first_seen_at``）、
    来源没说发布时间、也没有重验过。把上一次的 ``current`` 原样带进来的话，
    构造 ``TemporalAssertion`` 时就会撞上 ``_current_requires_a_time_anchor`` ——
    那条判据说得很清楚：声称「现在成立」而没有任何时间锚点，正是会把 2021 年的
    PDF 读成 2026 年事实的那个状态。所以这一行的诚实结论是 ``unknown``，
    而它必须**判得出来**（不是抛，也不是继承上一次）。
    """
    previous = Freshness(
        assertion_id="x" * 32,
        currentness=Currentness.CURRENT,
        reason=FreshnessReason.WITHIN_POLICY_WINDOW,
        policy_version="test-v1",
        evaluated_at=NOW - timedelta(days=1),
    )
    route = _route(first_seen_at=NOW)
    carried = previous.model_copy(update={"assertion_id": route.expected_assertion_id})
    route = rebuild(route, freshness=carried)

    judgement = judge(route, _policy(), now=NOW)

    assert judgement.outcome is FreshnessOutcome.JUDGED
    assert judgement.currentness is Currentness.UNKNOWN, "上一次的 current 被原样带了过来"
    assert judgement.row.freshness is not None
    assert judgement.row.freshness.reason is FreshnessReason.NO_TIME_ANCHOR


def test_a_previous_current_does_not_rescue_an_aged_source() -> None:
    """来源确实说过发布时间、但已经很旧：结论是 ``stale``，与上一次无关。

    **这一半不是判别性的**：判据根本不读断言上的 ``CURRENT``（`evaluate_freshness`
    的两条读法里没有它），所以带不带那一次取值，这一档的结论都一样。留着它是因为
    「上一次判过 current、这次材料旧了」这个组合在真实数据里会出现，而它该落在
    stale 上这件事值得有一条写明了的断言。真正证明「不许原样带进来」的是上一条。
    """
    previous = Freshness(
        assertion_id="x" * 32,
        currentness=Currentness.CURRENT,
        reason=FreshnessReason.WITHIN_POLICY_WINDOW,
        policy_version="test-v1",
        evaluated_at=NOW - timedelta(days=1),
    )
    route = _route(first_seen_at=NOW, source_published_at=NOW - timedelta(days=900))
    carried = previous.model_copy(update={"assertion_id": route.expected_assertion_id})
    route = rebuild(route, freshness=carried)

    fresh = judge(route, _policy(), now=NOW).row.freshness

    assert fresh is not None
    assert fresh.currentness is Currentness.STALE


# --- §9·4 反方向的第二个错误：没找到一个人，不等于这个人走了 ---------------------
#
# 判定（哪一档、为什么）在 ``tests/test_domain_temporal.py``。这一节只问三件
# **落地**上的事：这一格能不能被继承、它会不会写出一个日期、以及报告上它是不是
# 一个能被看见的数。


def test_a_previous_not_seen_becomes_unknown_on_the_assertion() -> None:
    """「复查时没看到」不得被当成这条断言上一次的取值带进下一轮。

    **这一条量的是断言上那一格，不是结论** —— 与上面
    ``test_a_previous_current_does_not_rescue_an_aged_source`` 的处境相同：
    今天的 ``evaluate_freshness`` 不读 ``NOT_SEEN``（它只读 ``HISTORICAL``），
    所以把这一格原样带进去，结论今天不会变。真正会变的是**将来那个人**：谁把
    ``NOT_SEEN`` 加进读法，谁就会同时得到一个会自我续期的观测失败 ——
    「上一次没看到」变成「这一次也没看到」的依据，而这两件事之间没有因果关系。
    那一行今天刚好落在最痛的位置上（只有观测时刻、来源没说发布时间），
    于是它必须判得出来，而不是继承上一次。
    """
    previous = Freshness(
        assertion_id="x" * 32,
        currentness=Currentness.NOT_SEEN,
        reason=FreshnessReason.NOT_SEEN_ON_RECHECK,
        policy_version="test-v1",
        evaluated_at=NOW - timedelta(days=1),
    )
    route = _route(first_seen_at=NOW)
    carried = previous.model_copy(update={"assertion_id": route.expected_assertion_id})
    route = rebuild(route, freshness=carried)

    assertion = assertion_for(route)

    assert assertion is not None
    assert assertion.currentness is Currentness.UNKNOWN, "一次观测失败被继承成了下一轮的依据"


def test_a_failed_recheck_writes_no_validity_end() -> None:
    """复查没看到它，那一行**不因此多出任何日期**。

    §9 逐字：「新页面不见某人只触发复查，不生成虚构离职日期」。所以判完之后
    ``valid_to`` 仍然为空（没有「他哪天走的」），``last_verified_at`` 也仍然为空
    （**又看了一次不是又验证了一次**），而 ``source_published_at``（材料自己的
    时刻）原样不动 —— 它没有变，复查改变的是我们对它的**观测**。
    """
    published = NOW - timedelta(days=30)
    route = _route(first_seen_at=NOW, source_published_at=published)

    judgement = judge(route, _policy(), now=NOW, seen_on_recheck=False)

    assert judgement.outcome is FreshnessOutcome.JUDGED
    row = judgement.row
    assert row.freshness is not None
    assert row.freshness.currentness is Currentness.NOT_SEEN
    assert row.valid_to is None, "不得生成一个「他哪天走的」"
    assert row.last_verified_at is None, "「又看了一次」不是「又验证了一次」"
    assert row.source_published_at == published


def test_the_recheck_signal_reaches_both_row_families() -> None:
    """两族行共用同一条判据 —— 机会行同样不许把「没看到」读成「走了」。

    ``RouteOpportunity`` 正是 §9 里「有岗位但没有邮箱」的那一族，而它**同样**
    要被维护：一份材料上找不到这个岗位，与该岗位被撤销是两件事。
    """
    for row in (
        _route(first_seen_at=NOW, source_published_at=NOW - timedelta(days=30)),
        _opportunity(first_seen_at=NOW, source_published_at=NOW - timedelta(days=30)),
    ):
        judgement = judge(row, _policy(), now=NOW, seen_on_recheck=False)
        assert judgement.outcome is FreshnessOutcome.JUDGED
        assert judgement.currentness is Currentness.NOT_SEEN


def test_not_seen_is_counted_as_its_own_value_in_the_distribution() -> None:
    """报告上「没看到」与「时间旧了」必须是**两个数**。

    它们对读的人指向两个动作：一个回去找那一页现在写在哪，一个重新验证。
    合成一个数，两种处置在报告上就变成同一件事 —— 而 ``currentness``
    这一格的全部意义就是让读的人不必翻代码就知道下一步做什么。
    """
    metrics = AccountMetrics(account_id=ACCOUNT_ID, cohort=Cohort.KNOWN_SUCCESS)
    aged = _route(first_seen_at=NOW, source_published_at=NOW - timedelta(days=900))
    unseen = _route(
        account_id="a2",
        first_seen_at=NOW,
        source_published_at=NOW - timedelta(days=30),
    )

    metrics.record_freshness(judge(aged, _policy(), now=NOW))
    metrics.record_freshness(judge(unseen, _policy(), now=NOW, seen_on_recheck=False))

    assert metrics.currentness == {"stale": 1, "not_seen": 1}
    assert metrics.freshness_judged == 2


# --- rebuild 真的跑校验（这是它存在的全部理由）---------------------------------


def test_rebuild_runs_the_row_validator() -> None:
    """把结论贴到一个**没有时刻锚点**的行上 ⇒ 红。

    ``_freshness_belongs_to_this_row`` 的第一条：有结论而算不出断言 id 的行，
    结论无从复核。这条测试同时是行级判据是否真的在构造路径上的证据。
    """
    judged = judge(_route(first_seen_at=NOW - timedelta(days=1)), _policy(), now=NOW).row
    assert judged.freshness is not None

    with pytest.raises(ValidationError):
        rebuild(judged, first_seen_at=None)


def test_model_copy_would_have_missed_that() -> None:
    """**同一次改写**走 ``model_copy`` 不会红 —— 这就是 ``rebuild`` 存在的理由。

    没有这一条，上面那条测试可以用任何一种构造方式通过，而「``model_copy`` 不走
    校验」这句写在模块 docstring 里的话就没有执行器。
    """
    judged = judge(_route(first_seen_at=NOW - timedelta(days=1)), _policy(), now=NOW).row

    copied = judged.model_copy(update={"first_seen_at": None})

    assert copied.first_seen_at is None
    assert copied.freshness is not None, (
        "model_copy 竟然跑了校验 —— 那么本仓「不变量在构造上生效」的说法需要重新读一遍"
    )


def test_rebuild_rejects_a_conclusion_about_another_row() -> None:
    """另一行的结论挂上来 ⇒ 红。这是「复制一行改内容」最容易造成的走丢。"""
    other = _route(
        email="someone.else@example.co.uk", first_seen_at=NOW - timedelta(days=3)
    )
    conclusion = judge(other, _policy(), now=NOW).row.freshness
    assert conclusion is not None

    with pytest.raises(ValidationError):
        rebuild(_route(first_seen_at=NOW), freshness=conclusion)


def test_rebuild_keeps_the_other_fields() -> None:
    route = _opportunity(first_seen_at=NOW, role_title="Head of Grounds")

    rebuilt = rebuild(route, freshness=None)

    assert rebuilt.role_title == "Head of Grounds"
    assert rebuilt.first_seen_at == NOW
    assert type(rebuilt) is RouteOpportunity


# --- CONFLICTING 今天没有生产者（刻意）----------------------------------------


def test_a_binding_conflict_is_not_a_contradicted_assertion() -> None:
    """行上的 ``has_conflict`` **不**折成 ``contradicted_by``。

    那是两件事：``has_conflict`` 是绑定层的冲突（同一页上多个邮箱、定不下谁对应谁，
    SOP L105），而 ``contradicted_by`` 要的是**另一条断言的 id** —— 那一维不在这
    一行上。折进去就得替对方编一个 id，而 id 是内容寻址的：编出来的无法复核。
    """
    conflicting = _route(
        first_seen_at=NOW - timedelta(days=1),
        has_conflict=True,
        source_published_at=NOW - timedelta(days=1),
    )

    assertion = assertion_for(conflicting)
    assert assertion is not None
    assert assertion.contradicted_by == []

    judgement = judge(conflicting, _policy(), now=NOW)
    assert judgement.outcome is FreshnessOutcome.JUDGED
    assert judgement.currentness is Currentness.CURRENT, (
        "绑定层冲突被当成了断言冲突 —— 这一格的判据换了"
    )


def test_conflicting_has_no_producer_in_this_layer() -> None:
    """本模块**不产出** ``CONFLICTING``：那一档要么来自 ``contradicted_by``（这里
    永远为空），要么来自别处 —— 而今天没有别处。

    这条测试是「刻意留空」与「漏了」之间的那条分界线：哪天有人真的接上了
    ``contradicted_by``，它会红，而红的时候要改的是这句说明，不是把它删掉。
    """
    route = _route(first_seen_at=NOW - timedelta(days=1))
    produced = {
        judge(row, _policy(), now=NOW).currentness
        for row in (
            route,
            _route(first_seen_at=NOW, source_published_at=NOW - timedelta(days=900)),
            _route(first_seen_at=NOW),
            _opportunity(),
        )
    }

    assert Currentness.CONFLICTING not in produced


# --- 指标：两种「没有结论」分开记 ---------------------------------------------
#
# 这一节在**指标**这一层测 ``record_freshness``，而不是从 pipeline 那边够。
# 理由是具体的一条：``NO_TIME_ANCHOR`` 今天在流水线上**产生不出来**（时刻由 bind
# 写下），所以「判过的行数」与「判据没能开工的行数」分不分得开，在那条链路上没有
# 任何执行器 —— 而这两个数读起来差得很远。


def _metrics() -> AccountMetrics:
    return AccountMetrics(account_id=ACCOUNT_ID, cohort=Cohort.KNOWN_SUCCESS)


def test_a_judged_row_lands_in_the_currentness_distribution() -> None:
    metrics = _metrics()
    route = _route(first_seen_at=NOW, source_published_at=NOW - timedelta(days=1))

    metrics.record_freshness(judge(route, _policy(), now=NOW))

    assert metrics.freshness_judged == 1
    assert metrics.freshness_without_a_time_anchor == 0
    assert metrics.currentness == {"current": 1}


def test_a_row_the_judge_could_not_start_is_not_counted_as_judged() -> None:
    metrics = _metrics()

    metrics.record_freshness(judge(_route(), _policy(), now=NOW))

    assert metrics.freshness_judged == 0
    assert metrics.freshness_without_a_time_anchor == 1
    assert metrics.currentness == {}, "没判过的行不许在取值分布里留下一个数"


def test_the_distribution_starts_empty_not_all_zero() -> None:
    """零分母时是**空表**：``{"stale": 0}`` 会让「一次都没判」看起来像「判了、都很新」。"""
    assert _metrics().currentness == {}
    assert _metrics().to_dict()["currentness"] == {}


def test_the_run_report_merges_the_distribution_across_accounts() -> None:
    report = RunReport(run_id="r1")
    for account_id, days in (("a1", 1), ("a2", 900)):
        metrics = AccountMetrics(account_id=account_id, cohort=Cohort.KNOWN_SUCCESS)
        route = _route(
            account_id=account_id,
            first_seen_at=NOW,
            source_published_at=NOW - timedelta(days=days),
        )
        metrics.record_freshness(judge(route, _policy(), now=NOW))
        report.accounts.append(metrics)

    payload = report.to_dict()

    assert payload["currentness"] == {"current": 1, "stale": 1}
    assert payload["freshness_judged"] == 2
    assert payload["freshness_without_a_time_anchor"] == 0

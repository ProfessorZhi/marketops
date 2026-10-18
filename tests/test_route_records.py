"""路线记录的**两层**（§6）：规范键、折叠、以及一张拒收回执的身份。

## 为什么判据住在这里而不是 ``test_evidence_store_contract.py``

那一份问的是「三个存储实现是不是同一个东西」，所以它只写**能对着三个实现各跑一遍**
的判据。而这一份问的是**领域层自己**的判定 —— 它们是纯函数与模型的判据，与落盘
无关：规范键折不折点号、折叠是不是取最高等级、一张收据的哪几格是身份。把它们写进
契约文件，只能得到一个「三个实现都对、而规则本身错」的绿灯。

## §6 逐字要的两条，在这里各有一条执行器

* 「不做 Gmail 风格的点号/加号剥离」→
  ``test_the_key_folds_case_and_nothing_else``；
* 「不要只取最高等级：``D + X + X`` ≠ ``D``」→ ``test_folding_is_not_max``。

两条都是**反向**判据：它们证明的是一件**没有**发生的事，而本仓对这类判据的处置是
让它们点名那个没有被做出来的合并（「``a.b@`` 与 ``ab@`` 是两个不同的收件人」），
而不是断言一个数字。一个只断言「键有 16 位十六进制」的用例，在实现开始剥离点号时
照样绿。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from marketops.domain.enums import ContactField, Grade, SourceKind
from marketops.domain.route_records import (
    REJECTION_BOOKKEEPING_FIELDS,
    CanonicalContactRoute,
    RouteObservation,
    RouteRejection,
    RouteRejectionCode,
    canonical_route_key,
    fold_observation,
    identity_mismatch,
    observation_id_for,
    rejection_id_for,
    route_email_key,
)
from marketops.domain.util import check_canonical_id
from tests.test_evidence_store_contract import (
    FETCHED_AT,
    WORKSPACE,
    make_observation,
    make_rejection,
    make_route,
)

LATER = FETCHED_AT + timedelta(days=3)


def _observation(*, grade: Grade, observed_at: datetime, email: str = "grounds@example.co.uk"):
    return make_observation(observed_at=observed_at, grade=grade, email=email)


# --- 规范键：只小写，不折点号、不折加号 ---------------------------------------


def test_the_key_folds_case_and_nothing_else() -> None:
    """``Sales@X.CO.UK`` 与 ``sales@x.co.uk`` 是同一个键 —— 而**只有**这两者。

    两条反向判据各自挡掉一种「静默合并」：

    * ``a.b@x.co.uk`` 与 ``ab@x.co.uk`` 是**两个**收件人。点号折叠只在 Gmail 那一家
      成立（那是它的别名规则，不是 SMTP 的），在 RFC 5321 下点号是本地部分的一个
      普通字符。折了它们，两个不同的人变成一条路线，而报告上只显示「去重掉一条」；
    * ``a+tag@x.co.uk`` 与 ``a@x.co.uk`` 同理（加号标记同样是**收件方**才解释的
      约定，本仓没有据以合并的任何依据）。

    §6 逐字要求的就是这一条，所以它必须有一条**会因折叠而变红**的判据 —— 一句
    「不做点号剥离」的注释不是执行器。
    """
    assert route_email_key(" Sales@X.CO.UK ") == route_email_key("sales@x.co.uk")
    assert route_email_key("a.b@x.co.uk") != route_email_key("ab@x.co.uk")
    assert route_email_key("a+tag@x.co.uk") != route_email_key("a@x.co.uk")


def test_the_key_separates_what_must_not_merge() -> None:
    """规范键的三个分量各自挡掉一种合并（SOP：账户 ID + 产品场景 + 小写邮箱）。

    * 少了 ``workspace`` —— 两个部署关于同一个账号的路线互相覆盖；
    * 少了 ``account_id`` —— 一个共享域下两个组织的 ``info@`` 变成一条；
    * 少了邮箱那一半 —— 一条路线上的两个入口被读成一个。
    """
    base = canonical_route_key(workspace=WORKSPACE, account_id="a1", email="info@x.co.uk")

    assert base != canonical_route_key(
        workspace="another-scene", account_id="a1", email="info@x.co.uk"
    )
    assert base != canonical_route_key(workspace=WORKSPACE, account_id="a2", email="info@x.co.uk")
    assert base != canonical_route_key(
        workspace=WORKSPACE, account_id="a1", email="sales@x.co.uk"
    )


def test_the_key_is_already_a_file_name() -> None:
    """键落盘时不被改写：它已经过了 ``check_canonical_id``。

    这一条钉的是「id 与其文件名是同一个东西」那条纪律在**派生** id 上的成立 ——
    一个会算出 ``route-A.B`` 的公式，会让两套路线在某个存储上合并进一个文件。
    """
    key = canonical_route_key(workspace=WORKSPACE, account_id="a1", email="sales@x.co.uk")
    assert check_canonical_id(key, "canonical_key") == key


# --- 折叠：顺序无关的是**读数**，规则是三条 -----------------------------------


def test_folding_is_not_max() -> None:
    """``D + X + X`` 折出来**不是** ``D``（§6 逐字要求的那一条）。

    取最高等级会让一条旧的、看着更好的读法在它的证据早就变了之后继续活着 —— 而报告
    上它看起来完全正常（一条 D 级路线，无可指摘）。当前读法由**最近一次看**回答，
    更早的那些读法一条都没丢：它们在观察表里。
    """
    first = _observation(grade=Grade.D, observed_at=FETCHED_AT)
    folded = fold_observation(None, first, at=first.observed_at)
    assert folded.projection.route.grade is Grade.D

    second = _observation(grade=Grade.X, observed_at=FETCHED_AT + timedelta(hours=1))
    folded = fold_observation(folded.projection, second, at=second.observed_at)
    assert folded.projection.route.grade is Grade.X
    assert folded.projection.previous_grade is Grade.D

    third = _observation(grade=Grade.X, observed_at=FETCHED_AT + timedelta(hours=2))
    folded = fold_observation(folded.projection, third, at=third.observed_at)

    assert folded.projection.route.grade is Grade.X, "最近一次看是 X，不是历史上最好的 D"
    assert folded.projection.revision == 3
    assert len(folded.projection.observation_ids) == 3, "更早的读法一条都没被丢掉"


def test_a_replayed_observation_changes_nothing() -> None:
    """重放一次结算：``applied=False``，读数一个字都没改，``revision`` 不涨。

    这是「跨重试幂等」在领域层的那一半 —— 存储那一半（写第二次是覆盖而不是新行）
    由 ``test_evidence_store_contract.py`` 判。
    """
    observation = _observation(grade=Grade.B, observed_at=FETCHED_AT)
    first = fold_observation(None, observation, at=observation.observed_at)
    again = fold_observation(first.projection, observation, at=observation.observed_at)

    assert again.applied is False
    assert again.projection == first.projection
    assert again.reason, "不生效也要说得出来（重放 vs 迟到的写）"


def test_a_late_observation_does_not_overwrite_the_reading() -> None:
    """迟到的写不覆盖当前读法 —— 而它**仍然进历史**（由调用方写观察表）。

    定序是 ``(observed_at, observation_id)``：第一格是「我们**看到那一页**的时刻」，
    第二格只在同一瞬间的比较里起作用。**不是**按到达顺序、也不是墙钟 —— 后者会让
    两次结算的写入顺序（由调度决定）决定谁赢。
    """
    newer = _observation(grade=Grade.B, observed_at=LATER)
    current = fold_observation(None, newer, at=newer.observed_at).projection

    older = _observation(grade=Grade.A, observed_at=FETCHED_AT)
    folded = fold_observation(current, older, at=newer.observed_at)

    assert folded.applied is False
    assert folded.projection.route.grade is Grade.B


def test_the_reading_does_not_depend_on_arrival_order() -> None:
    """两个 worker 以任意顺序提交同一批观察 → **读数逐位相同**（§6.2 并发安全）。

    顺序相关的那几格（``revision`` / ``observation_ids`` / ``previous_grade``）记的是
    「这个存储改写了几次」，它们**不**具备这个性质，而那是刻意的 —— 类 docstring
    里写着理由。所以这条判据把两者分开断言：读数必须一样，改写账允许不一样。
    """
    a = _observation(grade=Grade.D, observed_at=FETCHED_AT)
    b = _observation(grade=Grade.B, observed_at=LATER)

    forward = fold_observation(
        fold_observation(None, a, at=a.observed_at).projection, b, at=b.observed_at
    ).projection
    backward = fold_observation(
        fold_observation(None, b, at=b.observed_at).projection, a, at=a.observed_at
    ).projection

    assert forward.route == backward.route
    assert forward.last_observed_at == backward.last_observed_at
    assert forward.last_observation_id == backward.last_observation_id
    assert forward.revision != backward.revision, "改写账本来就与到达顺序有关"


def test_two_observations_of_one_instant_are_ordered_by_id() -> None:
    """同一瞬间的两条观察按 ``observation_id`` 定序 —— 于是提交顺序不起作用。

    这条定序是**规则的定序**，不是关于世界的断言：它存在的唯一目的，是让两个
    worker 交上两条同一时刻的观察时，盘上落下来的那一行与提交顺序无关。
    """
    one = _observation(grade=Grade.A, observed_at=FETCHED_AT, email="one@example.co.uk")
    two = _observation(grade=Grade.D, observed_at=FETCHED_AT, email="one@example.co.uk")
    assert one.observation_id != two.observation_id, "两条观察的等级不同，id 必然不同"

    winner = max((one, two), key=lambda item: (item.observed_at, item.observation_id))
    forward = fold_observation(
        fold_observation(None, one, at=FETCHED_AT).projection, two, at=FETCHED_AT
    ).projection
    backward = fold_observation(
        fold_observation(None, two, at=FETCHED_AT).projection, one, at=FETCHED_AT
    ).projection

    assert forward.route.grade is winner.route.grade
    assert backward.route.grade is winner.route.grade


def test_previous_grade_is_none_only_for_the_first_reading() -> None:
    """``None`` 说的是「没有上一个」，与「上一个也是 D」是两句话。

    只留当前值的话，等级从 A 掉到 D（要去看那一页改了什么）与一直是 D 在报告上同形。
    """
    first = fold_observation(
        None, _observation(grade=Grade.D, observed_at=FETCHED_AT), at=FETCHED_AT
    )
    assert first.projection.previous_grade is None

    second = fold_observation(
        first.projection,
        _observation(grade=Grade.D, observed_at=LATER),
        at=LATER,
    )
    assert second.projection.previous_grade is Grade.D


# --- 一条观察的 id：内容寻址，而「哪一趟跑的」不在原像里 ----------------------


def test_a_replay_of_one_observation_gets_the_same_id() -> None:
    """同一次结算重放一次算出同一个 ``observation_id``。

    所以「重放幂等」在这一层就成立 —— 不用等到存储那一层去查重，因为 id 相同，
    写下去就是同一行。
    """
    route = make_route()
    key = canonical_route_key(
        workspace=route.workspace, account_id=route.account_id, email=route.email
    )
    kwargs = dict(
        workspace=route.workspace,
        account_id=route.account_id,
        canonical_key=key,
        observed_at=FETCHED_AT,
        route=route,
    )

    assert observation_id_for(**kwargs) == observation_id_for(**kwargs)


def test_the_grade_is_part_of_an_observation_but_its_evidence_is_not_enough() -> None:
    """只在等级上不同的两条观察是**两条观察** —— 它们说的是两件不同的事。

    反过来说：同一等级、同一证据重看一次是**一条**。这条判据把「重看」与「重新
    判定」分开，而它们此前共用一条路径。
    """
    same = _observation(grade=Grade.D, observed_at=FETCHED_AT)
    regraded = _observation(grade=Grade.A, observed_at=FETCHED_AT)

    assert same.observation_id != regraded.observation_id
    assert same.canonical_key == regraded.canonical_key, "两条观察指的是同一条路线"


def test_an_observation_does_not_carry_the_run_that_made_it() -> None:
    """观察上**没有** ``run_id`` —— 那是记账，不是这一条观察的内容。

    原像里加进它，同一次结算重跑一次就会得到两条 id 不同的观察，而它们是同一件事。
    这条判据用一个多余的字段去撞 ``extra="forbid"``：它同时证明了这个模型**不接受**
    这一类字段，而不是「今天恰好没人传」。
    """
    observation = _observation(grade=Grade.D, observed_at=FETCHED_AT)
    assert set(observation.model_dump()) == {
        "observation_id",
        "canonical_key",
        "workspace",
        "account_id",
        "observed_at",
        "route",
    }

    with pytest.raises(ValidationError):
        RouteObservation(**observation.model_dump(), run_id="run-1")


def test_an_observation_must_be_one_story() -> None:
    """工作区、账号、规范键、route 四格必须说的是**同一件事**。

    分开校验不够：一条 ``workspace`` 属于甲的观察、``route`` 属于乙，是「跨工作区
    读」在盘上的形状；而键与 route 对不上会让**另一条路线**的投影按这一条的等级被
    改写 —— 那是一次静默的错误定级。
    """
    observation = _observation(grade=Grade.D, observed_at=FETCHED_AT)

    with pytest.raises(ValidationError, match="工作区"):
        RouteObservation(**{**observation.model_dump(), "workspace": "another-scene"})
    with pytest.raises(ValidationError, match="规范键"):
        RouteObservation(**{**observation.model_dump(), "canonical_key": "route-0000000000000000"})
    with pytest.raises(ValidationError):
        RouteObservation(**{**observation.model_dump(), "account_id": "a2"})


def test_a_projection_cannot_be_built_from_nothing() -> None:
    """``observation_ids`` 不能为空 —— 一条当前读法必然来自至少一次观察。

    空的清单读起来像「读过、什么都没有」，而它的实际含义是「这一行没有来源」。
    """
    projection = fold_observation(
        None, _observation(grade=Grade.D, observed_at=FETCHED_AT), at=FETCHED_AT
    ).projection

    with pytest.raises(ValidationError, match="observation_ids"):
        CanonicalContactRoute(**{**projection.model_dump(), "observation_ids": ()})
    with pytest.raises(ValidationError, match="不在 observation_ids"):
        CanonicalContactRoute(
            **{**projection.model_dump(), "last_observation_id": "obs-0000000000000000"}
        )


# --- 一张拒收回执：身份与改写账 -----------------------------------------------


def test_a_receipt_says_which_page_and_when_but_not_which_run() -> None:
    """``rejection_id`` 的原像里有页面与时刻，**没有** run。

    两半各自挡一件事：

    * 有页面与时刻 —— 同一个邮箱在**另一个**页面上被拒是另一条记录（它是另一份
      证据），同一页重看一次是同一张收据（幂等）；
    * 没有 run —— 重试一次、两个 worker 同时跑同一批、维护轮次重看同一页，写的都是
      同一行。把它算进原像，「再算一遍不会多出一行」这句话就不成立了。
    """
    base = make_rejection()

    assert make_rejection(source_url="https://example.co.uk/team").rejection_id != base.rejection_id
    assert (
        make_rejection(occurred_at=LATER).rejection_id != base.rejection_id
    ), "另一刻看到的另一份材料"
    assert make_rejection(run_id="run-2").rejection_id == base.rejection_id
    assert (
        make_rejection(rule="SOP L126（2026-09 版）").rejection_id == base.rejection_id
    ), "换了策略版本重判同一份材料，还是那张收据"


def test_the_code_is_identity_not_bookkeeping() -> None:
    """``foreign_domain`` 与 ``unbound_email`` 是**两张收据**，不是同一张改了理由。

    它们的处置不同（一张到此为止，一张要回那一页找那张卡片），所以 ``code`` 在原像
    里。反过来，它也不能被当成改写账覆盖掉 —— 那会让一条今天改判的候选与它昨天的
    那一类共用一张收据，而报告里「哪一类在涨」从此算不出来。
    """
    assert REJECTION_BOOKKEEPING_FIELDS == ("run_id", "work_item_id", "rule")
    assert "code" not in REJECTION_BOOKKEEPING_FIELDS

    foreign = make_rejection(code=RouteRejectionCode.FOREIGN_DOMAIN)
    unbound = make_rejection(code=RouteRejectionCode.UNBOUND_EMAIL)
    assert foreign.rejection_id != unbound.rejection_id


def test_the_bookkeeping_can_be_rewritten_but_the_identity_cannot() -> None:
    """比的是 :meth:`RouteRejection.identity`，而它**恰好**去掉那三格。

    「恰好」是承重的：少去一格会让合法的第二次结算抛异常，多去一格会让一条**不同
    的**拒收盖掉原来那条。所以这条判据从两个方向各钉一次。
    """
    first = make_rejection()
    rewritten = make_rejection(run_id="run-2", work_item_id="wi-2", rule="SOP L126（2026-09 版）")
    different = make_rejection(candidate="another.person@example.co.uk")

    assert identity_mismatch(first, rewritten) == ()
    assert set(first.identity()) == set(first.model_dump(mode="json")) - set(
        REJECTION_BOOKKEEPING_FIELDS
    )
    # ``rejection_id`` 也在身份里（它就是这两张收据不同的**名字**）。真实调用点上
    # 它永远不出现在差异里 —— 适配器只拿**同一个 id** 的两张收据来比（见
    # ``put_route_rejection`` 的三处实现）。这里两条是不同候选算出的不同 id，
    # 所以两格都报出来。
    assert identity_mismatch(first, different) == ("candidate", "rejection_id")


def test_a_blank_candidate_belongs_to_empty_value_and_nothing_else() -> None:
    """空的候选只在 ``empty_value`` 那一类上合法，反之亦然 —— **双向**。

    两个方向各挡一种误读：候选为空而类别是别的，收据上会是一行「foreign_domain 拒了
    ``''``」（真正发生的事是这一格没抽出东西来）；类别是 ``empty_value`` 而候选非空，
    那一类的定义就不成立了。两类拒收的处置不同，所以这不是措辞问题。
    """
    empty = make_rejection(candidate="   ", code=RouteRejectionCode.EMPTY_VALUE)
    assert empty.candidate == "   ", "候选按它本来的样子存，不替它去空白"

    with pytest.raises(ValidationError, match="empty_value"):
        make_rejection(candidate="", code=RouteRejectionCode.FOREIGN_DOMAIN)
    with pytest.raises(ValidationError, match="empty_value"):
        make_rejection(candidate="sales@example.co.uk", code=RouteRejectionCode.EMPTY_VALUE)


def test_a_receipt_keeps_the_literal_it_rejected() -> None:
    """``candidate`` 是**哪一列上的哪一条字面值**，由 ``field`` 回答。

    五类拒收里有两类拒的不是邮箱（``ineligible_source`` 拒任意字段上的字面值、
    ``empty_value`` 拒一条坏行），所以一张只有 ``email`` 的收据会逼出两种坏写法：
    捏一个邮箱，或写成空串（读起来像「没有候选」，而它是「有一条不合格的候选」）。
    """
    rejected = make_rejection(
        candidate="Grounds Manager",
        field=ContactField.ROLE,
        code=RouteRejectionCode.INELIGIBLE_SOURCE,
        source_kind=SourceKind.SEARCH_SNIPPET,
    )

    assert rejected.candidate == "Grounds Manager"
    assert rejected.field is ContactField.ROLE
    assert rejected.reason.startswith("ineligible_source")


def test_a_receipt_says_which_rule_it_was_judged_under() -> None:
    """``rule`` 与 ``detail`` 是两格：一格指向规则（SOP 条款号 / policy 版本），
    一格是判据原文。两个都不是替它编的一句话。"""
    rejected = make_rejection(rule="SOP L121", detail="邮箱域 example.com 不在该账号的域名集里")

    assert rejected.rule == "SOP L121"
    assert rejected.reason == "entry_elsewhere —— 邮箱域 example.com 不在该账号的域名集里"


def test_a_receipt_must_be_aware_of_time() -> None:
    """拒收的时刻必须带时区 —— 报告要按它排序，而一个 naive 的时刻在跨时区的
    两份导出里会排成两个样子。"""
    with pytest.raises(ValidationError, match="时区"):
        RouteRejection(**{**make_rejection().model_dump(), "occurred_at": datetime(2026, 9, 15)})


def test_the_rejection_id_is_recomputed_the_same_way() -> None:
    """构造器与 :func:`rejection_id_for` 是同一处公式 —— 不是「碰巧算得一样」。

    这条判据存在的理由：一个手工拼原像的构造器与一个真的公式，只要有一格的序列化
    不同（时刻的写法、候选的大小写），就会在**别的**实现上算出别的 id，而那看起来
    像「存储把两条拒收缩成了一条」。
    """
    rejected = make_rejection()
    assert rejected.rejection_id == rejection_id_for(
        workspace=rejected.workspace,
        account_id=rejected.account_id,
        code=rejected.code,
        candidate=rejected.candidate,
        source_url=rejected.source_url,
        occurred_at=rejected.occurred_at,
    )


def test_the_key_form_is_used_for_deduplication_not_the_literal() -> None:
    """判定走键形式（``Sales@`` 与 ``sales@`` 是同一张收据），报告走原文。

    与路线那一侧同一条纪律：**存下来的那一格**保持页面上看到的样子，因为它是证据；
    而「这是不是同一件事」按规范键回答，因为那是判定。
    """
    lower = make_rejection(candidate="sales@example.co.uk")
    mixed = make_rejection(candidate="Sales@Example.CO.UK")
    assert lower.rejection_id == mixed.rejection_id
    assert lower.candidate != mixed.candidate

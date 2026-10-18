"""模型层不变量的测试。

这些测试守护的是「让事故不可能发生」的那几行代码 —— 它们比任何流程文档都
更值得被测试覆盖。三组：

1. 本阶段的硬约束（``research_only`` / ``send_authorized``）；
2. 「账号 ≠ 联系路线」：等级挂在线路上，线路必须有字面邮箱，P3 不是线路；
3. 场地资产是**关系表**上的键，不是名字 —— 同名场地串号要表达不出来。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from marketops.domain.account import MarketAccount
from marketops.domain.entities import (
    AccountVenueRelation,
    ContactRoute,
    Person,
    ResponsibleEntity,
    Role,
    RouteOpportunity,
    VenueAsset,
    route_subject_ref,
    venue_asset_id_for,
)
from marketops.domain.enums import (
    AccountStatus,
    BusinessFunction,
    Cohort,
    ContactField,
    DomainVerdict,
    Grade,
    GradeReason,
    RecordBlockPriority,
    RoleClass,
    RouteStatus,
    SourceKind,
    VenueRelation,
)
from marketops.domain.evidence import (
    AccountRecord,
    Evidence,
    EvidenceMoments,
    ResearchRun,
    evidence_id,
    moments_from_evidence,
)
from marketops.domain.temporal import (
    Currentness,
    Freshness,
    FreshnessPolicy,
    FreshnessReason,
    TemporalAssertion,
    assertion_id,
    evaluate_freshness,
)
from marketops.domain.util import slugify
from tests.conftest import DEFAULT_PROFILE

#: 这一批构造用的工作区 = 测试用的那个 profile（同 ``tests/test_cli_mint_token.py``
#: 的 ``WORKSPACE``）。**模型上没有默认值** —— 这里有一个是合法的，因为它是测试
#: 构造器：缺了血统的构造在一个测试里是「谁都没说」，而不是一条静默的放行。
WORKSPACE = DEFAULT_PROFILE

FETCHED_AT = datetime(2026, 9, 15, tzinfo=timezone.utc)


def _evidence(**overrides) -> Evidence:
    payload = {
        "evidence_id": "e1",
        "workspace": WORKSPACE,
        "account_id": "a1",
        "source_url": "https://example.co.uk/contact",
        "source_kind": SourceKind.FETCHED_HTML,
        "literal_value": "grounds@example.co.uk",
        "content_sha256": "deadbeef",
        "fetched_at": FETCHED_AT,
        "field": ContactField.EMAIL,
    }
    payload.update(overrides)
    return Evidence(**payload)


def _route(**overrides) -> ContactRoute:
    payload = {"workspace": WORKSPACE, "account_id": "a1", "email": "grounds@example.co.uk"}
    payload.update(overrides)
    return ContactRoute(**payload)


def _asset(**overrides) -> VenueAsset:
    """一块场地。``first_seen_at`` 与 ``workspace`` 都是必填 —— 见模型文档。"""
    payload = {
        "workspace": WORKSPACE,
        "venue_asset_id": "v1",
        "name": "Main Pitch",
        "first_seen_at": FETCHED_AT,
    }
    payload.update(overrides)
    return VenueAsset(**payload)


def _relation(**overrides) -> AccountVenueRelation:
    """一条责任关系。四个必填项一并在场：``workspace``、两个 id、``first_seen_at``，
    外加**至少一条证据**（空表在校验期报错，见 ``AccountVenueRelation`` 的类文档）。

    与 ``_asset`` 一样，这里有一个缺省是**合法的**：它是测试构造器，缺了血统在
    测试里读作「谁都没说」，不是一条静默的放行。
    """
    payload = {
        "workspace": WORKSPACE,
        "account_id": "a1",
        "venue_asset_id": "v1",
        "relation": VenueRelation.MAINTAINS,
        "evidence_ids": ["e1"],
        "first_seen_at": FETCHED_AT,
    }
    payload.update(overrides)
    return AccountVenueRelation(**payload)


def _market_account(**overrides) -> MarketAccount:
    """账号池里的一行。必填项三样：``workspace`` / ``account_id`` / ``organisation``
    / ``first_seen_at``，外加**至少一条证据**（空表在校验期报错，见
    ``MarketAccount`` 的模块 docstring）。

    与上面两个构造器一样，这里的缺省是**合法的**：它是测试构造器。
    """
    payload = {
        "workspace": WORKSPACE,
        "account_id": "a1",
        "organisation": "Example Trust",
        "evidence_ids": ["e1"],
        "first_seen_at": FETCHED_AT,
    }
    payload.update(overrides)
    return MarketAccount(**payload)


# --- send_authorized / research_only -----------------------------------------


@pytest.mark.parametrize(
    "model_payload",
    [
        {"account_id": "a1", "email": "x@example.co.uk"},
        {"account_id": "a1", "role_title": "Grounds Manager"},
    ],
    ids=["contact-route", "route-opportunity"],
)
def test_send_authorized_true_is_rejected(model_payload: dict) -> None:
    """本阶段不允许构造 send_authorized=True 的对象 —— 产出与机会都不允许。

    这条校验刻意放在模型层：它让「忘记关闭发送」在**构造时**就失败，
    而不是等到某天真的发出去才被发现。
    """
    cls = ContactRoute if "email" in model_payload else RouteOpportunity
    with pytest.raises(ValidationError, match="send_authorized"):
        cls(**model_payload, send_authorized=True)


def test_research_only_false_is_rejected() -> None:
    with pytest.raises(ValidationError, match="research_only"):
        _route(research_only=False)


def test_research_run_defaults_are_research_only() -> None:
    run = ResearchRun(
        run_id="r1", profile="p", started_at=datetime(2026, 9, 15, tzinfo=timezone.utc)
    )
    assert run.research_only is True
    assert run.send_authorized is False


def test_research_run_rejects_research_only_false() -> None:
    with pytest.raises(ValidationError, match="research_only"):
        ResearchRun(
            run_id="r1",
            profile="p",
            started_at=datetime(2026, 9, 15, tzinfo=timezone.utc),
            research_only=False,
        )


# --- 账号 ≠ 联系路线 ----------------------------------------------------------


def test_a_route_requires_a_literal_email() -> None:
    """SOP L125/L126 的 A 与 B **都**要求「真实公开业务邮箱」。

    这是类型层的落地：没有邮箱的对象**构造不出来**。因此「有具名角色但没有邮箱」
    不可能被标成 B —— 那是一条 ``RouteOpportunity``。
    """
    with pytest.raises(ValidationError, match="email"):
        ContactRoute(account_id="a1")


def test_padded_email_is_rejected() -> None:
    with pytest.raises(ValidationError, match="首尾空白"):
        _route(email=" grounds@example.co.uk ")


def test_route_has_no_account_level_aggregate_fields() -> None:
    """路线模型里**没有**账号级的聚合字段。

    账号有多少条路线、账号是否 eligible，都不是「这一条路线」能回答的。
    把账号聚合塞进路线判定，会让「组织对、人不对」无处安放。
    """
    fields = set(ContactRoute.model_fields)
    for forbidden in ("account_status", "route_count", "net_new_ab", "cohort"):
        assert forbidden not in fields, f"ContactRoute 不该有 {forbidden}"


def test_p3_cannot_be_a_route() -> None:
    """P3 的定义是「有场地/责任关系证据但没有邮箱」，与「必须有邮箱」互斥。"""
    with pytest.raises(ValidationError, match="P3"):
        _route(block_priority=RecordBlockPriority.P3)


def test_opportunity_defaults_to_p3_and_has_no_email() -> None:
    opportunity = RouteOpportunity(
        workspace=WORKSPACE, account_id="a1", role_title="Grounds Manager"
    )

    assert opportunity.block_priority is RecordBlockPriority.P3
    assert "email" not in RouteOpportunity.model_fields
    assert opportunity.reason == "no_literal_email"


def test_sent_statuses_cannot_be_constructed() -> None:
    """``send_authorized=False`` 时，已发送状态在类型层面不可达。"""
    for status in (
        RouteStatus.SENT_STAGE_1,
        RouteStatus.SENT_STAGE_4,
        RouteStatus.REPLY,
        RouteStatus.BOUNCED,
    ):
        with pytest.raises(ValidationError, match="send_authorized"):
            _route(status=status)


@pytest.mark.parametrize(
    "status",
    [RouteStatus.OBSERVED, RouteStatus.QUEUED_CANDIDATE, RouteStatus.MANUAL_REVIEW],
)
def test_reachable_statuses_are_allowed(status: RouteStatus) -> None:
    assert _route(status=status).status is status


# --- 证据与判定的关系 ---------------------------------------------------------


def test_non_x_grade_requires_evidence() -> None:
    """没有证据的 A/B/C/D 是不允许存在的 —— 那等于凭空判定。"""
    with pytest.raises(ValidationError, match="evidence_ids"):
        _route(grade=Grade.A)


def test_x_grade_without_evidence_is_allowed() -> None:
    route = _route()
    assert route.grade is Grade.X
    assert route.grade_reason is GradeReason.NO_ELIGIBLE_EVIDENCE


def test_non_x_grade_with_evidence_is_allowed() -> None:
    assert _route(grade=Grade.B, evidence_ids=["e1"]).grade is Grade.B


def test_a_document_with_no_declared_publication_time_says_none() -> None:
    """``None`` 是**唯一**能说「来源没有声明发布时刻」的取值。

    用一个缺省的时刻顶上（今天、或者抓取时刻）会让这两件事在数据里同形：一份
    2019 年的 PDF 与一页今天的公告。而这一格存在的全部理由就是能把它们分开。
    """
    assert _evidence().source_published_at is None


@pytest.mark.parametrize("field", ["fetched_at", "source_published_at"])
def test_a_naive_moment_on_an_evidence_row_is_refused(field: str) -> None:
    """两个时刻走同一条规则。

    校验只加在新字段上的话，差别不出现在写入的那一刻，而出现在**读**的那一刻
    —— 「这份证据有多旧」是发布时刻与取回时刻之差，naive 与 aware 相减直接抛
    ``TypeError``，而那一次崩离原因很远。
    """
    with pytest.raises(ValidationError, match="带时区"):
        _evidence(**{field: datetime(2026, 9, 24)})


@pytest.mark.parametrize("field", ["fetched_at", "source_published_at"])
def test_an_offset_on_an_evidence_row_is_kept(field: str) -> None:
    """只拒绝 naive，**不**归一成 UTC —— 与资产池那三行同一条决定。

    ``to_utc`` 的理由是「时刻被格式化成 ISO 字符串存进**列**，而字符串顺序只在
    偏移量相同时等于瞬间顺序」。这两个时刻不是列（``Evidence`` 整条以 ``payload``
    一列 JSON 落盘，没有查询按它们排序），所以那条规则要挡的东西这里不存在。
    这里还多一层：``source_published_at`` 是从来源的字面里读出来的，把 ``+01:00``
    改写成 ``Z`` 之后，「来源是这么说的」就变了形。
    """
    plus_one = timezone(timedelta(hours=1))
    at = datetime(2019, 5, 1, 9, 0, tzinfo=plus_one)
    assert getattr(_evidence(**{field: at}), field).utcoffset() == timedelta(hours=1)


# --- 场地资产是关系表上的键，不是名字 -----------------------------------------


def test_the_source_identity_is_a_source_handle_not_an_account_identity() -> None:
    """``osm:node/123`` 是**来源**的编号，不是账号身份，也不是场地键。

    这条判据挡的是一个具体的诱惑：OSM 编号稳定、唯一、好当键。而它认的是**一块
    场地** —— 同一个 Council 的两百块场地会给出两百个这样的编号。拿它当账号键，
    就是把 SOP L72「账号是实体，不是单个 pitch」原样缝回去。

    所以这里断言的是**三件事**，不是一件：形状照 ``<namespace>:<value>``；两半
    分别取得到（``osm_type`` 与 ``osm_id`` 就是它们）；以及**没有**一个把来源
    编号折成账号 id 的入口 —— ``AccountVenueRelation.account_id`` 是规范 id，
    来源编号里的 ``:`` 与 ``/`` 在那里过不了校验。
    """
    asset = _asset(source_identity="osm:node/123")
    assert asset.source_parts() == ("osm", "node/123")

    # 两半就是上游的两个字段，谁都取不到也别猜 —— 没有 "/" 时第二段是整个 tail。
    assert _asset(source_identity="site:example.org").source_parts() == (
        "site",
        "example.org",
    )
    assert _asset().source_parts() == ("", "")

    # 来源编号**不是**账号 id：形状就不对。
    with pytest.raises(ValidationError):
        _relation(
            account_id=asset.source_identity,
            relation=VenueRelation.OWNS,
        )

    with pytest.raises(ValidationError, match="命名空间"):
        _asset(source_identity="node/123")
    with pytest.raises(ValidationError, match="空的"):
        _asset(source_identity="osm:")


def test_the_domain_verdict_and_its_fields_are_always_one_story() -> None:
    """官网问题的三种答案各自**只有一个**合法形状，其余组合构造不出来。

    这一格要挡的是审计里那条读法：``official_domain`` 装着 SERP 抓回来的主机，
    却被读成「这是它的官网」。拆开之后，危险的就不再是「填错了」而是「填了
    一半」—— 一个写着域名、判定却是 ``unknown`` 的行，读它会得到「还没查」，
    而它已经带着一个没人验过的域名。

    **集合那一格是 ``verified_official_domains``，单数格是它的读法。** 一个组织
    可能有不止一个官网（SOP L103 要求「最终域名与责任实体关系可解释」，没有要求
    只有一个），所以「证得了几个」是集合的问题，而 ``verified_official_domain``
    只在**恰好一个**时才给值 —— 它是一句方便话，不是第二份证据。两者不一致的行
    构造不出来，否则「它有几个官网」会由两格各说各话。
    """
    confirmed = _asset(
        discovered_url="https://directory.example/list",
        candidate_domain="directory.example",
        verified_official_domain="example.org",
        verified_official_domains=["example.org"],
        domain_verdict=DomainVerdict.CONFIRMED,
        domain_evidence_ids=["e-domain"],
    )
    # 候选域名与证得的官网**不相等**是合法的、常见的一行：在目录页上看到它，
    # 官网在别处。它读作「候选被推翻过」—— 所以前者不许当后者的缺省值。
    assert confirmed.candidate_domain != confirmed.verified_official_domain

    # 两个官网的组织：集合有两个，单数格于是**为空** —— 而判定仍是 confirmed。
    # 这一行是 §4.2 「一个组织多个官网」的落点，它必须能构造出来。
    two = _asset(
        verified_official_domains=["north.example.org", "south.example.org"],
        domain_verdict=DomainVerdict.CONFIRMED,
        domain_evidence_ids=["e-domain"],
    )
    assert two.verified_official_domain == ""

    absent = _asset(
        domain_verdict=DomainVerdict.ABSENT,
        domain_evidence_ids=["e-domain"],
    )
    assert absent.verified_official_domain == ""
    assert absent.verified_official_domains == []

    unknown = _asset()
    assert unknown.domain_verdict is DomainVerdict.UNKNOWN

    # 三值就是三个，没有第四个「空」成员：空串不是一个判定结果。
    assert {member.value for member in DomainVerdict} == {"unknown", "confirmed", "absent"}

    for bad in (
        {"domain_verdict": DomainVerdict.CONFIRMED, "domain_evidence_ids": ["e"]},
        {"domain_verdict": DomainVerdict.CONFIRMED, "verified_official_domain": "example.org"},
        {"domain_verdict": DomainVerdict.CONFIRMED, "verified_official_domains": ["example.org"]},
        {"domain_verdict": DomainVerdict.ABSENT,
         "verified_official_domains": ["example.org"], "domain_evidence_ids": ["e"]},
        {"domain_verdict": DomainVerdict.ABSENT},
        {"verified_official_domains": ["example.org"]},
        {"domain_evidence_ids": ["e"]},
        # 两格各说各话：集合里两个，单数格却说其中一个。
        {"verified_official_domains": ["a.example.org", "b.example.org"],
         "verified_official_domain": "a.example.org"},
        # 单数格有值而集合为空 —— 这是拆开之后最容易写出来的那一种。
        {"verified_official_domain": "example.org"},
        # 集合里的项不是规范形式 / 是空的 / 重复。重复会让「唯一官网」那句
        # 由一句重复造出来（两元素的集合让 len(...)==1 那条判据成立）。
        {"verified_official_domains": ["Example.ORG"]},
        {"verified_official_domains": ["example.org", "example.org"]},
        {"verified_official_domains": [""]},
    ):
        with pytest.raises(ValidationError, match="domain_verdict|不一致|规范形式|重复项|是空的"):
            _asset(**bad)


def test_evidence_may_be_recorded_without_claiming_a_verdict_on_the_site() -> None:
    """场地本身的证据与域名判定是**两件事**：前者的存在不蕴含后者被查过。

    这是一条反向的判据。上面那条把「有域名证据却没有判定」堵死了，而这里要证明
    堵的不是「有证据就不行」—— 一份证明这块场地叫什么、在哪儿的页面，对
    「它的官网是哪个」一个字都没说。把它读成一次域名判定，正是本仓反复写的
    那台「伪造证据的机器」。
    """
    asset = _asset(evidence_ids=["e-name", "e-location"])
    assert asset.domain_verdict is DomainVerdict.UNKNOWN
    assert asset.domain_evidence_ids == []
    assert asset.last_verified_at is None, "进池子那一次不是一次「重新看过」"


def test_the_upstream_claim_is_not_a_verdict_and_does_not_need_one() -> None:
    """「**上游说**它的官网是 X」与「**我们验过**它是 X」在盘上是两格，不是一格。

    这一格存在的理由是**它没有的东西**：一条上游声明带着域名、却一条证据也没有。
    只准写 ``verified_official_domain`` 的话，这样的一行要么根本写不进去（缺证据），
    要么就得把判定写成 ``confirmed`` —— 后者是把别人写的一句话记成一次验证，
    而审计里那条被拆开的读法正是从这种「两件事记成一件」开始的。所以这里断言它
    带着域名而判定仍是 ``unknown``，且**不许**有任何证据。
    """
    claimed = _asset(claimed_official_domain="example.org")
    assert claimed.domain_verdict is DomainVerdict.UNKNOWN
    assert claimed.verified_official_domain == ""
    assert claimed.domain_evidence_ids == []

    # 形状判据与 ``RowDisposition.official_domain`` 是同一个函数：带 scheme、端口或
    # 路径的值在这里就报错 —— 那种值拿去跟别的域名比，永远不相等。
    for bad in ("https://example.org", "example.org/path", "example.org:443"):
        with pytest.raises(ValidationError, match="claimed_official_domain"):
            _asset(claimed_official_domain=bad)


def test_a_handle_comes_from_the_source_number_when_there_is_one() -> None:
    """有来源编号时按编号取句柄，名字只跟着走。

    这条判据挡的是**重新导出**：上游改了拼写（``Main Pitch`` → ``Main Field``）、
    或者这一行在文件里换了位置，池子里的句柄都必须不动。动一下，下一轮导入会把
    每一块场地都读成新发现的 —— 而报告上那只是一句「本轮新增 N 块场地」。
    """
    pitch = venue_asset_id_for(
        source_identity="osm:node/123", name="Main Pitch", location="Devon"
    )
    assert pitch == venue_asset_id_for(
        source_identity="osm:node/123", name="Main Field", location="Devon"
    ), "同一块场地改了名，句柄必须不动"

    assert venue_asset_id_for(
        source_identity="osm:node/124", name="Main Pitch", location="Devon"
    ) != pitch, "另一个编号是另一块场地，哪怕名字一模一样"


def test_a_handle_without_a_source_number_comes_from_the_name_and_the_place() -> None:
    """没有来源编号时按内容取：名字与地点相同就是同一块场地。

    这一格与 ``SourceReceipt.distinct_resolved_count`` 用的是**同一个键**
    （``resolved_dedupe_key``）。两处各写一套的话，「这份回执里有几个东西」与
    「这一批落进池子几块场地」会各说各话，而两个数字都看着很正常。
    """
    named = venue_asset_id_for(source_identity="", name="Main Pitch", location="Devon")
    assert named == venue_asset_id_for(
        source_identity="  ", name="Main Pitch", location="Devon"
    ), "首尾空白不是编号，也不该造出第二块场地"
    assert named == venue_asset_id_for(
        source_identity="", name="  Main Pitch  ", location=" Devon "
    ), "上游多打一个空格，不是另一块场地"
    assert named != venue_asset_id_for(
        source_identity="", name="Main Pitch", location="Cornwall"
    ), "地点不同就是另一块场地"

    # 两个 id 空间不重叠：同一句话在两种取名方式下不相撞。
    assert named.startswith("named-")
    assert venue_asset_id_for(
        source_identity="osm:node/123", name="", location=""
    ).startswith("src-")


def test_a_handle_is_safe_as_a_filename_fragment() -> None:
    """这一格会被当作文件名片段用，所以它必须是 ``slugify`` 的不动点。

    上游的字符串可以含 ``:`` ``/`` 与任意 Unicode（``osm:node/123`` 就含 ``:``），
    而句柄不能 —— 一个含 ``/`` 的值会被当成路径，把两条证据写进同一个文件里，
    而报告上只是「证据更多了」。
    """
    for identity in ("osm:node/123", "site:example.org", "ünïcode:nämé/ø", ""):
        handle = venue_asset_id_for(
            source_identity=identity, name="Pitch #1 / Main", location="Devon, UK"
        )
        assert slugify(handle) == handle, f"{handle!r} 不是一个 slugify 不动点"
        assert handle == handle.strip()
        assert not any(ch in handle for ch in '/\\:*?"<>|')


#: 三个池子行的构造器。这条判据在三个模型上是**同一句话**（实现也只有一个，
#: ``domain/entities.py::_timestamps_must_be_aware``），所以用同一组用例问它们。
POOL_ROWS = pytest.mark.parametrize(
    "build", [_asset, _relation, _market_account], ids=["asset", "relation", "market-account"]
)


@POOL_ROWS
@pytest.mark.parametrize("field", ["first_seen_at", "last_verified_at"])
def test_a_naive_timestamp_on_a_pool_row_is_refused(field: str, build) -> None:
    """naive 的时间戳的失败方式不在它落盘的时候，而在**下一次折入**的时候。

    合并规则拿它跟已有那一行比先后（``min`` / ``max``），而 Python 对 naive 与
    aware 的比较是 ``TypeError`` —— 一次崩，发生在离原因很远的地方。
    """
    with pytest.raises(ValidationError, match="带时区"):
        build(**{field: datetime(2026, 9, 24)})


@POOL_ROWS
def test_an_offset_is_kept_rather_than_rewritten_to_utc(build) -> None:
    """只拒绝 naive，**不**归一成 UTC。

    池子里的时刻只用来比先后，而跨偏移量的比较本来就按瞬间走。写成 ``to_utc``
    会让「写进去什么瞬间、读出来什么瞬间」变成一句空话 —— 而
    ``tests/test_asset_store_contract.py`` 里那条正对照正是拿偏移量做判据的。
    """
    plus_eight = timezone(timedelta(hours=8))
    at = datetime(2026, 9, 24, 9, 0, tzinfo=plus_eight)
    assert build(first_seen_at=at).first_seen_at.utcoffset() == timedelta(hours=8)


# --- §5 责任关系：四段键、证据、新鲜度 ----------------------------------------


def test_two_scenes_judging_the_same_pair_are_two_rows_not_one() -> None:
    """同一块场地、同一个账号，在两个 Scene 下判出的关系**各自一行**。

    §5/§6 那条「一个 Scene 的结论不得渗进另一个」在盘上就落在 ``workspace`` 那
    一段：少了它，这两行会撞成同一个键，后写的那一条会静静地盖掉前一条 —— 而
    报告上只显示「关系还在」。
    """
    here = _relation(workspace=WORKSPACE)
    there = _relation(workspace="another-scene")
    assert here.relation_key() != there.relation_key()
    assert {here.workspace, there.workspace} == {WORKSPACE, "another-scene"}


@pytest.mark.parametrize("bad", ["", "A Scene", "a/b", "a:b", " a"])
def test_a_relation_workspace_must_be_canonical(bad: str) -> None:
    """``workspace`` 与两个 id 走**同一条**判据（``check_canonical_id``）。

    松一档的写法是给它一个默认值「当前 Scene」—— 那条路看起来更友好，实际是把
    「没人写血统」变成一次不留痕的放行，而且它正是在这里最危险：Scene 的身份是
    授权链的第一段。
    """
    with pytest.raises(ValidationError):
        _relation(workspace=bad)


def test_a_relation_needs_a_workspace_a_venue_and_a_time() -> None:
    """四个必填格缺任何一格都构造不出来 —— 缺了它们这一行读起来像个查过的判定。"""
    for missing in ("workspace", "account_id", "venue_asset_id", "first_seen_at"):
        payload = _relation().model_dump()
        del payload[missing]
        with pytest.raises(ValidationError):
            AccountVenueRelation(**payload)


def test_a_relation_without_evidence_is_not_written() -> None:
    """「一条证据都没有的关系**不应**被写出来」是判词，不是自觉。

    空的 ``evidence_ids`` 与「还没有证据，先收下」在盘上长得一样：下游读到的都是
    「我们查到了」。所以空表在这里直接报错，而正确的做法是先不写这一行。
    """
    with pytest.raises(ValidationError, match="至少要一条证据"):
        _relation(evidence_ids=[])

    # 正对照：``unresolved`` 也是要凭据的判定，与其余五个一样严。
    with pytest.raises(ValidationError, match="至少要一条证据"):
        _relation(relation=VenueRelation.UNRESOLVED, evidence_ids=[])


def test_a_decision_id_on_a_relation_is_a_canonical_id_too() -> None:
    """产生这一行的那几个判定 id 与另外三个 id 走**同一条**判据。

    空表在这里**不报错**（手工那条路径写下来的行就是这一种：「这一行不是判出来的」），
    而给了值就一个都不能是需要改写的：这一格将来要拿去做 ``get_decision`` 的入参，
    而需要一个 id 被规范化才能查得到，说明交上来的那个字符串与台账里的不是同一个。
    ``record_id`` 是 ``sha256_text(...)[:32]``，本来就不动点。
    """
    assert _relation().decision_record_ids == [], "不给就是「没有判定支撑」，不是缺省成假值"
    assert _relation(decision_record_ids=["rec-1"]).decision_record_ids == ["rec-1"]

    for bad in ("", "Rec 1", "rec/1", "rec:1", " rec-1"):
        with pytest.raises(ValidationError):
            _relation(decision_record_ids=[bad])


def test_a_relation_key_names_all_four_parts() -> None:
    """键是派生的，四段都在里面 —— 报告里「哪一条关系」指的就是它。"""
    relation = _relation(
        workspace=WORKSPACE,
        account_id="trust",
        venue_asset_id="v1",
        relation=VenueRelation.BOOKING_CONTROL,
    )
    assert relation.relation_key() == f"{WORKSPACE}/trust/v1/booking_control"


def test_unresolved_is_not_a_synonym_for_no_relation() -> None:
    """``unresolved`` 说的是「知道有关系、但关系还没定下来」，不是「没关系」。

    它与「没有这一行」是两件事：一条 ``unresolved`` 行会带着证据落在盘上，而
    「没有这一行」在数据上什么都不留 —— 两者的下一步也不同（一个是去读已经拿到
    的那一页，一个是去查）。
    """
    seen = _relation(relation=VenueRelation.UNRESOLVED)
    assert seen.relation is VenueRelation.UNRESOLVED
    assert seen.relation_key().endswith("/unresolved")
    assert seen.evidence_ids


def test_the_key_of_a_relation_is_the_asset_id_not_the_venue_name() -> None:
    """SOP L78：场地**不是**账号。「同名场地串号」要在这里表达不出来。

    一条关系由 ``(workspace, account_id, venue_asset_id, relation)`` 四段确定；
    用 ``venue_name`` 做键时，两个 Council 各有一个 "Main Pitch" 会被并成一处。
    """
    fields = set(AccountVenueRelation.model_fields)
    assert "venue_name" not in fields
    assert "venue_asset_id" in fields

    relation = _relation(relation=VenueRelation.MAINTAINS)
    assert relation.relation is VenueRelation.MAINTAINS


def test_one_account_can_own_many_venues_and_one_venue_many_relations() -> None:
    """一个 Trust 管多块场地、一块场地由多个组织分别负责 —— 都表达得出来。"""
    relations = [
        _relation(account_id="trust", venue_asset_id="v1", relation=VenueRelation.OWNS),
        _relation(account_id="trust", venue_asset_id="v2", relation=VenueRelation.OWNS),
        _relation(account_id="contractor", venue_asset_id="v1"),
    ]
    assert len({(r.account_id, r.venue_asset_id) for r in relations}) == 3


@pytest.mark.parametrize("bad_id", ["a/b", "a\\b", "a:b", "a*b", "a?b", " a"])
def test_account_id_rejects_path_and_reserved_chars(bad_id: str) -> None:
    """id 会被用作文件名，必须先挡住路径字符。三个模型用同一套校验。"""
    with pytest.raises(ValidationError):
        AccountRecord(account_id=bad_id, organisation="X", cohort=Cohort.KNOWN_SUCCESS)
    with pytest.raises(ValidationError):
        _route(account_id=bad_id)
    with pytest.raises(ValidationError):
        _relation(account_id=bad_id, relation=VenueRelation.OWNS)
    with pytest.raises(ValidationError):
        _asset(venue_asset_id=bad_id)
    with pytest.raises(ValidationError):
        _market_account(account_id=bad_id)


def test_account_record_accepts_clean_id() -> None:
    record = AccountRecord(
        account_id="acme-001", organisation="X", cohort=Cohort.PREVIOUSLY_BLOCKED
    )
    assert record.cohort is Cohort.PREVIOUSLY_BLOCKED


def test_account_record_status_defaults_to_discovered() -> None:
    """账号的缺省状态是 discovered —— 不是 eligible 也不是 hold。"""
    from marketops.domain.enums import AccountStatus

    record = AccountRecord(account_id="a1", organisation="X", cohort=Cohort.KNOWN_SUCCESS)
    assert record.status is AccountStatus.DISCOVERED


def test_a_missing_country_is_not_a_country() -> None:
    """三处 ``country`` 的缺省都是 ``None`` —— 缺省值不得充当一次没人做过的判定。

    后果最直接的一处是 ``AccountRecord``：它的 ``country`` 会进事实字典，而
    ``grading.yaml`` 有一条 ``requires: ["!country in [gb, uk]"]``。缺省填 ``"UK"``
    时，一条没写国家的记录会让那条排除规则**静默不触发**，报告读成「在区域内」
    —— 判词会因此从「没人查过」变成「查过，没问题」。

    四处**一起**断言，因为它们是同一个错误的三份拷贝。只钉住 ``AccountRecord``
    的话，另外两处可以各自改回 ``"UK"`` 而没人看得见 —— 而场地与责任实体上迟早
    也会出现按国家判定，届时错法与今天逐字相同。§6 的账号池行是第四处，它的
    ``country`` 同样会进判定（「这个账号在不在范围内」），所以它必须一起被问。
    """
    assert (
        AccountRecord(account_id="a1", organisation="X", cohort=Cohort.KNOWN_SUCCESS).country
        is None
    )
    assert _asset().country is None
    assert ResponsibleEntity(entity_id="a1", name="Example Trust").country is None
    assert _market_account().country is None


# --- §6 账号池：一行答得出什么、答不出什么 ------------------------------------


#: §6 逐字点名的那几格，以及它在**这一行**上的载具。
#:
#: 两份名单分开写是有意的：「关联场地」与「责任关系」不在这一行上（由 §5 的关系表
#: 回答），而这条断言把那条边界钉成可执行的 —— 一段写在 docstring 里的「它们归
#: 关系表」在有人顺手加一个 ``linked_assets`` 字段时不会红。
_SECTION_SIX_ON_THE_ROW = {
    "account_id",
    "workspace",
    "organisation",
    "aliases",
    "domains",
    "addresses",
    "country",
    "evidence_ids",
    "first_seen_at",
    "last_verified_at",
    "status",
}

#: §6 点名、但**故意**不落在这行上的那两格。
_SECTION_SIX_BY_JOIN = {"linked_assets", "responsibility_relations"}


def test_the_row_carries_every_cell_section_six_names() -> None:
    """§6 的清单在这行上是**可执行**的：少一格就红。

    它防的不是「漏了一个字段」，是**两个字段各自漂**：合并规则漏在表外的格子
    永远取已有的那一行（``application/accounts.py`` 里那句话），所以「这一行有
    哪些格子」是合并规则必须被想到的那份清单 —— 靠读 docstring 记得它有十一格，
    在第十一次改动时就不成立了。
    """
    assert set(MarketAccount.model_fields) == _SECTION_SIX_ON_THE_ROW


def test_the_venues_an_account_is_joined_to_are_not_cells_on_this_row() -> None:
    """「关联场地」与「责任关系」由 §5 的关系表回答，**不在这行上再存一份**。

    存两遍的代价不是那两格：``这个账号管着哪几块场地`` 会有两个定义处，而关系表
    那一份还会折（证据取并集、时刻只往一个方向走、同一块场地会被反复判），这一份
    不会。两份数据里的同一句话，一份会变、一份不变 —— 读的人迟早拿到过时的那一份，
    而报告上完全看不出来。

    这条判据问的是**形状**：这一行上一个场地句柄的槽位都没有。一个
    ``linked_asset_ids`` 字段就是这条边界的反面，而它看起来很自然。
    """
    fields = set(MarketAccount.model_fields)
    assert not (fields & _SECTION_SIX_BY_JOIN)
    assert "venue_asset_id" not in fields
    assert "venue_asset_ids" not in fields
    assert "relations" not in fields


def test_two_scenes_holding_the_same_account_are_two_rows_not_one() -> None:
    """同一个组织在两个 Scene 下是**两行**，两个 ``status`` 互不影响。

    §6 的「客观事实可以复用、Scene 判定不可以」在盘上落在 ``workspace`` 那一段。
    少了它，两行会撞成同一个键，后写的那一次把前一次的状态静静盖掉 —— 而报告上
    只显示「这个账号还在池子里」。这正是「一个 Scene 的结论渗进另一个」的样子：
    它不报错，它只是让第二个 Scene 看见一个自己从没做过的判定。
    """
    here = _market_account(workspace=WORKSPACE, status=AccountStatus.ELIGIBLE)
    there = _market_account(workspace="another-scene")

    assert here.workspace != there.workspace
    assert here.status is AccountStatus.ELIGIBLE
    # 第二个 Scene 的行从 discovered 开始 —— 它自己没有判过，就没有可继承的结论。
    assert there.status is AccountStatus.DISCOVERED


@pytest.mark.parametrize("status", list(AccountStatus))
def test_every_admission_status_section_six_names_is_reachable(status: AccountStatus) -> None:
    """§6 点名的七个状态各自**构造得出来** —— 闭集、且没有一格是死值。

    这条与「它们必须互相区分」是同一件事的两半：一个永远构造不出来的成员在数据上
    与「这一格没人填」长得一样，而它在判定里已经被当成一个分支在用了。
    """
    assert _market_account(status=status).status is status


@pytest.mark.parametrize("blank", ["", " ", " Example Trust", "Example Trust "])
def test_a_market_account_without_a_name_is_not_written(blank: str) -> None:
    """名字空着的一行写不出来 —— 而 ``""`` 在这里**没有**「还没查」这个意思。

    ``country`` 的方言是 ``None`` = 未确认、``""`` = 查了没有；而这一格不是那个
    方言：一行账号的身份就是它的名字，池子里的行是**已经研究过的东西**。没有名字
    的行在报告里是一行没有主语的判定。首尾空白也一并挡住 —— 与那两格自由文本
    同一条理由（``ordered_union`` 按字符串去重，``" X"`` 与 ``"X"`` 是两个值）。
    """
    with pytest.raises(ValidationError):
        _market_account(organisation=blank)


def test_a_market_account_without_evidence_is_not_written() -> None:
    """空证据表在校验期就报错 —— 与 ``AccountVenueRelation`` 逐字同一条。

    报错而不是「先收下、等有证据再补」：这一行的错法是**读起来像查过了**，而一条
    等着补证据的行在下游与一条查过的行长得一模一样。
    """
    with pytest.raises(ValidationError, match="一条证据都没有"):
        _market_account(evidence_ids=[])


@pytest.mark.parametrize(
    "bad_domain", ["https://example.org", "example.org/contact", "Example.ORG", "example.org:443"]
)
def test_a_domain_cell_is_a_bare_host(bad_domain: str) -> None:
    """带 scheme、带路径、带大写、带端口的值在这里报错，**不被规范化**。

    这一格下游是拿去跟别的主机比（「这是不是同一个组织」），而
    ``https://example.org`` 与 ``example.org`` 永远不相等，差别在报告上看不出来。
    拒绝而不是改写：改写会把两个不同的 id 静默合并，而拒绝是调用方当场看得见的。
    """
    with pytest.raises(ValidationError):
        _market_account(domains=[bad_domain])


def test_a_domain_cell_holds_several_hosts_and_keeps_them() -> None:
    """一个组织可以有主站与分区站 —— 这一格是**复数**，所以顺序与内容都要留住。"""
    account = _market_account(domains=["example.org", "example-trust.org.uk"])
    assert account.domains == ["example.org", "example-trust.org.uk"]


@pytest.mark.parametrize("field", ["aliases", "addresses"])
@pytest.mark.parametrize("bad", ["", " Example PC", "Example PC "])
def test_a_padded_alias_or_address_is_refused(field: str, bad: str) -> None:
    """自由文本那一格也要挡首尾空白 —— 它是**会咬人**的。

    合并规则用 ``ordered_union`` 去重，而 ``" Example PC"`` 与 ``"Example PC"``
    是两个不相等的字符串：同一个别名会被记成两条，报告上读起来像「它还有另一个
    名字」。空串同理 —— 一个空的别名在报告里是一行没有内容的「也叫过」。
    """
    with pytest.raises(ValidationError):
        _market_account(**{field: [bad]})


def test_the_admission_status_is_not_a_route_grade() -> None:
    """这一行上**没有**等级这一格，也没有任何聚合出来的账号等级。

    两条轴正交（``AccountStatus`` 的类文档），而合并它们的诱惑在这个模型上特别大：
    「这个账号值不值得跟」听起来像一个数。等级挂在 ``ContactRoute`` 上，属于某一次
    联系研究，不属于账号 —— 一个 ``grade`` 字段会把「组织对、人不对」这种真实且
    常见的情形重新变成写不出来的状态。
    """
    fields = set(MarketAccount.model_fields)
    assert "grade" not in fields
    assert not {name for name in fields if "grade" in name}


# --- 人与岗位 -----------------------------------------------------------------


def test_person_id_is_content_addressed() -> None:
    """同一账号下的同一个字面姓名就是同一个人 —— 幂等来自这里。"""
    first = Person.for_account(account_id="a1", full_name="Jane Doe")
    second = Person.for_account(account_id="a1", full_name=" jane doe ")
    assert first.person_id == second.person_id

    other = Person.for_account(account_id="a2", full_name="Jane Doe")
    assert other.person_id != first.person_id


def test_person_without_a_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="full_name"):
        Person(person_id="p1", full_name="   ")


def test_role_carries_class_and_business_function_separately() -> None:
    """class 与 business_function 是两个独立事实：grounds manager 是
    operational + grounds；压成一个字段，C 级「与场地业务口径绑定」就没法表达。"""
    role = Role.for_account(
        account_id="a1",
        title="Grounds Manager",
        role_class=RoleClass.OPERATIONAL,
        business_function=BusinessFunction.GROUNDS,
    )
    assert role.role_class is RoleClass.OPERATIONAL
    assert role.business_function is BusinessFunction.GROUNDS


def test_role_without_a_title_is_rejected() -> None:
    with pytest.raises(ValidationError, match="title"):
        Role(role_id="r1", title="")


def test_responsible_entity_id_shares_the_account_id_space() -> None:
    """同一个组织在名单里是账号、在场地关系里是责任实体 —— 用同一个 id 空间。"""
    assert ResponsibleEntity(entity_id="a1", name="Example Trust").entity_id == "a1"


# --- 来源资格与字面性 ---------------------------------------------------------


def test_snippet_evidence_is_not_binding_eligible() -> None:
    assert _evidence(source_kind=SourceKind.SEARCH_SNIPPET).binding_eligible is False


@pytest.mark.parametrize(
    "kind",
    [SourceKind.FETCHED_HTML, SourceKind.FETCHED_PDF, SourceKind.FETCHED_TEXT],
)
def test_fetched_sources_are_binding_eligible(kind: SourceKind) -> None:
    assert _evidence(source_kind=kind).binding_eligible is True


def test_evidence_rejects_empty_or_padded_literal() -> None:
    with pytest.raises(ValidationError, match="literal_value"):
        _evidence(literal_value="   ")
    with pytest.raises(ValidationError, match="literal_value"):
        _evidence(literal_value=" grounds@example.co.uk ")


# --- 证据 id 的内容寻址 -------------------------------------------------------


_EVIDENCE_ID_INPUTS = {
    "account_id": "a1",
    "source_url": "https://example.co.uk/contact",
    "field": ContactField.EMAIL,
    "literal_value": "grounds@example.co.uk",
    "digest": "deadbeef",
}


def test_an_evidence_id_changes_with_every_input_it_is_derived_from() -> None:
    """id 的**每一个**入参都要真的进原像。

    只钉「同样的输入得到同样的 id」是不够的 —— 那是这件事最便宜的一半。
    丢掉 ``account_id`` 的写法（``sha256(source_url + literal + digest)``）
    在「重复抓取是幂等的」那条判据下**完全绿**，而它的后果是两个账号在同一页
    上发现的同一处字面证据共用一个 id：第二条写入撞「同 id 配不同内容」而整次
    失败，或者更坏 —— 内容恰好也相同，于是两个账号共用一行证据。

    所以这条判据逐个入参变异一次，每次断言 id 变了。
    """
    original = evidence_id(**_EVIDENCE_ID_INPUTS)
    for key, other in (
        ("account_id", "a2"),
        ("source_url", "https://example.co.uk/about"),
        ("field", ContactField.ROLE),
        ("literal_value", "hire@example.co.uk"),
        ("digest", "cafebabe"),
    ):
        assert evidence_id(**{**_EVIDENCE_ID_INPUTS, key: other}) != original, key


def test_a_document_level_evidence_id_is_not_any_field_evidence_id() -> None:
    """``field=None``（文档级证据）与四个 ``ContactField`` **各占一格**。

    这一格是 §3 ``get_evidence`` 那行「evidence id + bounded locator」能指到
    一整个文档的前提，见 ``application/evidence_ingest.py``。撞格的表现不是报错
    而是静默合并 —— 第二个写入撞上「同 id 配不同内容」，而那看起来像 id 被手工
    指定过，排查方向完全错。占位取空串而不是某个字段的名字，正是为了这一格与
    那四格都不同。
    """
    without_field = {key: value for key, value in _EVIDENCE_ID_INPUTS.items() if key != "field"}
    ids = {evidence_id(**without_field, field=field) for field in (None, *ContactField)}
    assert len(ids) == len(ContactField) + 1


# --- 血统：没写就构造不出来 ---------------------------------------------------

#: 三行带血统的产出，以及它们**除 workspace 之外**的必填字段。
#:
#: 三个模型各自一份，而不是合成一个参数化：它们的字段本来就不同，而这条判据问的是
#: 「少了血统这一格会怎样」—— 用一份共用的 payload 就测不出「少的是哪一格」。
_OWNED_MODELS = {
    "evidence": (
        Evidence,
        {
            "evidence_id": "e1",
            "account_id": "a1",
            "source_url": "https://example.co.uk/contact",
            "source_kind": SourceKind.FETCHED_HTML,
            "literal_value": "grounds@example.co.uk",
            "content_sha256": "deadbeef",
            "fetched_at": FETCHED_AT,
            "field": ContactField.EMAIL,
        },
    ),
    "contact-route": (ContactRoute, {"account_id": "a1", "email": "grounds@example.co.uk"}),
    "route-opportunity": (RouteOpportunity, {"account_id": "a1", "role_title": "Grounds Manager"}),
    "market-account": (
        MarketAccount,
        {
            "account_id": "a1",
            "organisation": "Example Trust",
            "evidence_ids": ["e1"],
            "first_seen_at": FETCHED_AT,
        },
    ),
}


@pytest.mark.parametrize("model_payload", _OWNED_MODELS.values(), ids=list(_OWNED_MODELS))
def test_an_object_without_lineage_cannot_be_constructed(model_payload) -> None:
    """**没写血统就构造不出来** —— 而这就是「写一个没有血统的对象会立即失败」。

    这一条判据的产出不是一个错误码，是一条**不可达**：三种对象在类型层就要求
    ``workspace``，所以没有任何一条写路径需要记得检查它，也不存在「忘了检查」的
    那一天。一个「缺省 = 当前 Scene」的默认值会把这件事反过来 —— 没写血统变成一次
    **不留痕的放行**，而这正是这一格要消灭的东西（见 ``domain/evidence.py`` 的
    ``Evidence.workspace``）。
    """
    model, payload = model_payload

    with pytest.raises(ValidationError, match="workspace"):
        model(**payload)


@pytest.mark.parametrize("model_payload", _OWNED_MODELS.values(), ids=list(_OWNED_MODELS))
def test_the_lineage_field_is_required_and_has_no_default(model_payload) -> None:
    """上面那条是**行为**，这一条是**形状**：这一格必填、且没有默认值。

    两条都要有。行为那条在有人给出一个默认值时会红，而形状这条说的是「红的是哪一
    个字段」—— 一个默认值加上一条「校验它是不是当前部署」的 validator 也能让行为
    那条绿，而那时「没写」与「写了当前部署」在盘上已经是同一件事。
    """
    model, _ = model_payload

    field = model.model_fields["workspace"]
    assert field.is_required(), "workspace 必须是必填的（一个默认值等于一次静默放行）"


@pytest.mark.parametrize("blank", ["", "Not Canonical", "uk acme", None])
def test_a_value_that_is_not_a_workspace_name_is_not_lineage(blank) -> None:
    """空格、大小写之外的写法、``None``：都不是「另一个工作区」，是**没写**。

    工作区名同时是一个目录名（``profiles_dir / profile``），所以判据是
    ``check_canonical_id`` —— 它**不做规范化**。规范化会把两个不同的名字静默合并成
    一个（于是两份产出落进同一个目录），这比拒绝它们坏得多。
    """
    with pytest.raises(ValidationError, match="workspace"):
        _evidence(workspace=blank)
    with pytest.raises(ValidationError, match="workspace"):
        _route(workspace=blank)
    with pytest.raises(ValidationError, match="workspace"):
        RouteOpportunity(workspace=blank, account_id="a1", role_title="Grounds Manager")
    # §6 的两张池子行 —— ``workspace`` 的**取值**判据（上面 ``_OWNED_MODELS`` 那条管
    # 的是「缺了这一格」，两条是分开的：一个带默认值的字段过得了那条、过不了这条）。
    with pytest.raises(ValidationError, match="workspace"):
        _market_account(workspace=blank)
    with pytest.raises(ValidationError, match="workspace"):
        _relation(workspace=blank)


# --- §9 路线那一族的时刻与当前性 ----------------------------------------------


def _opportunity(**overrides) -> RouteOpportunity:
    """一条未解决的路线机会。"""
    payload = {
        "workspace": WORKSPACE,
        "account_id": "a1",
        "role_title": "Grounds Manager",
    }
    payload.update(overrides)
    return RouteOpportunity(**payload)


#: 路线那一族的两行。与 ``POOL_ROWS`` 分开写，因为**字段集合不同**：池子行带两格
#: 时刻，路线行带五个时刻加一个判定结论。判据的实现只有一处
#: （``entities._timestamps_must_be_aware``），但每一格都要单独挂上去 ——
#: 合成一组的话，「路线行漏挂了一格」这件事就没有判据了。
ROUTE_ROWS = pytest.mark.parametrize(
    "build", [_route, _opportunity], ids=["contact-route", "route-opportunity"]
)

#: 路线那一族的五个时刻。
ROUTE_MOMENTS = (
    "first_seen_at",
    "last_verified_at",
    "source_published_at",
    "valid_from",
    "valid_to",
)

#: 一条路线被**第一次看到**的时刻。断言 id 的原像里有它，所以下面每个反例都要
#: 先有一个带锚点的行。
FIRST_SEEN = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

#: 另一个工作区（也是 ``slugify`` 不动点）。同一个 ``account_id`` 在两个工作区里
#: 是两行不同的东西 —— 判据见 ``Evidence._account_id_is_canonical``。
OTHER_WORKSPACE = "another-scene"


def _freshness_of(row: ContactRoute | RouteOpportunity, **overrides) -> Freshness:
    """按**这一行自己**的时刻与内容算出结论 —— 这是唯一合法的构造方式。"""
    payload = {
        "assertion_id": assertion_id(
            subject_ref=route_subject_ref(row.workspace, row.account_id),
            predicate=row.assertion_predicate,
            value=row.asserted_value,
            observed_at=row.first_seen_at,
        ),
        "currentness": Currentness.STALE,
        "reason": FreshnessReason.BEYOND_POLICY_WINDOW,
        "policy_version": "uncalibrated-test",
        "evaluated_at": FIRST_SEEN,
        "age_days": 5,
    }
    payload.update(overrides)
    return Freshness(**payload)


@ROUTE_ROWS
def test_a_route_row_written_before_section_nine_still_loads(build) -> None:
    """六个新格全都有默认值 —— 这是「加一格不是一次迁移」的类型层那一半。

    库里已经有落盘的行（整行以 ``payload`` 一列 JSON 存着），它们没有这些键。
    要能被读回来就必须有一个诚实的缺省，而 ``None`` 在这里读作「这一格没有值」:
    不是「它没有发生」，也不是某个用别的时刻顶上的值。

    ``None`` 与「判过、结论是 unknown」是两件事：前者是「还没判」，后者是
    「判了，判不出来」。
    """
    row = build()
    for field in (*ROUTE_MOMENTS, "freshness"):
        assert getattr(row, field) is None, field


@ROUTE_ROWS
@pytest.mark.parametrize("field", ROUTE_MOMENTS)
def test_a_naive_moment_on_a_route_row_is_refused(field: str, build) -> None:
    """五个时刻一起被校验。与池子行那两条是同一句话（实现只有一处）；
    分开问的理由是字段集合不同 —— 「不归一成 UTC」那一半由池子行那两条钉住。
    """
    with pytest.raises(ValidationError, match="带时区"):
        build(**{field: datetime(2026, 9, 24)})


def test_the_assertion_of_a_route_is_spelled_out_here() -> None:
    """这一行代表哪条断言 —— **写死在判据里**，不由被测代码自己去算。

    下面那些用例拿 ``row.asserted_value`` 算期望值，于是它们问的是「这一格与它自己
    一致吗」；至于谓语取值的字面、值里该放什么、主语带不带工作区，那样问不到。
    这里换一种写法：期望值用字面写出来。
    """
    route = _route(
        person_name="Jane Doe",
        role_title="Grounds Manager",
        first_seen_at=FIRST_SEEN,
    )
    literal = assertion_id(
        subject_ref=f"account:{WORKSPACE}:a1",
        predicate="published_contact_email",
        value="grounds@example.co.uk",
        observed_at=FIRST_SEEN,
    )

    accepted = _route(
        person_name="Jane Doe",
        role_title="Grounds Manager",
        first_seen_at=FIRST_SEEN,
        freshness=_freshness_of(route, assertion_id=literal),
    )

    assert accepted.freshness is not None
    # 值**只有**邮箱：姓名与岗位是这条断言的依据，不是它断言的内容。
    assert route.asserted_value == "grounds@example.co.uk"


def test_the_assertion_of_an_opportunity_names_the_person_and_the_role() -> None:
    """机会没有邮箱，它断言的是「这个账号上有这样一个人/岗位上还没有可联系的邮箱」。

    两格一起进值：只有姓名与只有岗位是两件不同的事。
    """
    opportunity = _opportunity(person_name="Jane Doe", first_seen_at=FIRST_SEEN)
    literal = assertion_id(
        subject_ref=f"account:{WORKSPACE}:a1",
        predicate="unresolved_contact_route",
        value="Jane Doe\x1fGrounds Manager",
        observed_at=FIRST_SEEN,
    )

    accepted = _opportunity(
        person_name="Jane Doe",
        first_seen_at=FIRST_SEEN,
        freshness=_freshness_of(opportunity, assertion_id=literal),
    )

    assert accepted.freshness is not None


@ROUTE_ROWS
def test_a_freshness_conclusion_about_this_row_is_accepted(build) -> None:
    """正对照。先有它，下面的反例才说明得了事：反例可能只是因为**别的原因**才红
    （``StrictModel`` 会拒绝未知字段、必填字段缺失也会红）。"""
    row = build(first_seen_at=FIRST_SEEN)
    accepted = build(first_seen_at=FIRST_SEEN, freshness=_freshness_of(row))

    assert accepted.freshness is not None
    assert accepted.freshness.currentness is Currentness.STALE


@ROUTE_ROWS
def test_a_freshness_conclusion_without_a_time_anchor_is_refused(build) -> None:
    """没有 ``first_seen_at`` 就算不出断言 id —— 这一格无从复核。

    它长得完全正常（有取值、有原因、有策略版本、有 32 位 id），所以它会被读成
    「这一行也是判过的」。而真相是这一格属于另一条有锚点的行。
    """
    row = build(first_seen_at=FIRST_SEEN)
    with pytest.raises(ValidationError, match="first_seen_at"):
        build(freshness=_freshness_of(row))


def test_a_freshness_conclusion_about_another_email_is_refused() -> None:
    """复制一行改内容最容易把这格带过去 —— 改的是**值**，而这一格是那条断言的结论。"""
    route = _route(first_seen_at=FIRST_SEEN)
    with pytest.raises(ValidationError, match="另一行"):
        _route(
            email="other@example.co.uk",
            first_seen_at=FIRST_SEEN,
            freshness=_freshness_of(route),
        )


def test_a_freshness_conclusion_from_another_account_is_refused() -> None:
    route = _route(first_seen_at=FIRST_SEEN)
    with pytest.raises(ValidationError, match="另一行"):
        _route(account_id="a2", first_seen_at=FIRST_SEEN, freshness=_freshness_of(route))


def test_a_freshness_conclusion_from_another_workspace_is_refused() -> None:
    """同一个 ``account_id`` 在两个工作区里是**两个**账号。

    这条反例问的是主语里那一维：主语不算工作区的话，两个部署里同名的账号会得到
    同一条断言 id，而「这条结论是关于哪件事的」就此撞车 —— 撞了没有任何地方会报错。
    """
    route = _route(first_seen_at=FIRST_SEEN)
    with pytest.raises(ValidationError, match="另一行"):
        _route(
            workspace=OTHER_WORKSPACE,
            first_seen_at=FIRST_SEEN,
            freshness=_freshness_of(route),
        )


def test_a_freshness_conclusion_from_a_route_is_refused_on_an_opportunity() -> None:
    """两族之间的串味：字段集合完全一样，所以「复制过去」是最省事的写法。

    它们的**谓语**不同（一条说有公开邮箱，一条说这个岗位上还没有邮箱），因此
    「这一格判的是哪条断言」也一定不同。
    """
    route = _route(first_seen_at=FIRST_SEEN)
    with pytest.raises(ValidationError, match="另一行"):
        _opportunity(first_seen_at=FIRST_SEEN, freshness=_freshness_of(route))


# --- §9·2b 那一格「这份口径认过没有」------------------------------------------


def test_a_policy_is_uncalibrated_unless_it_says_otherwise() -> None:
    """``calibrated`` 的缺省是 ``False`` —— 缺省是「没人认过」，不是「已批准」。

    与 ``domain/maintenance.py`` 的 ``RefreshRule.calibrated`` 是同一条纪律：一组
    看起来合理的天数不是业务事实。
    """
    policy = FreshnessPolicy(
        policy_version="uncalibrated-v1", current_within_days=180, historical_after_days=730
    )

    assert policy.calibrated is False


def test_a_conclusion_carries_the_calibration_of_its_own_policy() -> None:
    """结论上那一格由**算它的那份策略**决定，不取缺省。"""
    assertion = TemporalAssertion(
        assertion_id="0" * 32,
        subject_ref=f"account:{WORKSPACE}:a1",
        predicate="published_contact_email",
        value="grounds@example.co.uk",
        observed_at=FIRST_SEEN,
        source_published_at=FIRST_SEEN,
    )
    calibrated = evaluate_freshness(
        assertion,
        FreshnessPolicy(
            policy_version="approved-v1",
            current_within_days=180,
            historical_after_days=730,
            calibrated=True,
        ),
        now=FIRST_SEEN,
    )

    assert calibrated.currentness is Currentness.CURRENT, "构造错了 —— 先要有结论可比"
    assert calibrated.policy_calibrated is True


def test_a_conclusion_written_before_the_calibration_flag_still_loads() -> None:
    """§9·2b 之前落盘的结论没有 ``policy_calibrated`` 这一格 —— 它必须读得回来。

    这是「加一格可选字段不是一次迁移」在**这一格**上的执行器：读回来的取值是
    ``False``（未标定），也就是失败关闭的那一侧 —— 一条老结论不会因为少了一格
    就默认成了「按获批口径算的」。
    """
    route = _route(first_seen_at=FIRST_SEEN)
    payload = route.model_dump(mode="json")
    payload["first_seen_at"] = FIRST_SEEN.isoformat()
    payload["freshness"] = {
        key: value
        for key, value in _freshness_of(route).model_dump(mode="json").items()
        if key != "policy_calibrated"
    }

    loaded = ContactRoute.model_validate(payload)

    assert loaded.freshness is not None
    assert loaded.freshness.policy_calibrated is False


# --- §9 一组证据行折出来的两个时刻 --------------------------------------------


def test_moments_of_one_page_are_that_page_s_own_times() -> None:
    """同源的几行折出来就是那一页的时刻：取回时刻与来源声明的发布时刻各一个。"""
    page = datetime(2019, 5, 1, tzinfo=timezone.utc)
    rows = [
        _evidence(fetched_at=FETCHED_AT, source_published_at=page),
        _evidence(
            evidence_id="e2",
            field=ContactField.NAME,
            literal_value="Jane",
            fetched_at=FETCHED_AT,
            source_published_at=page,
        ),
    ]
    assert moments_from_evidence(rows) == EvidenceMoments(FETCHED_AT, page)


def test_the_first_seen_moment_is_the_earliest_fetch_not_the_latest() -> None:
    """取**最早**那一次，所以方向是安全的：跨材料的一行不会因为「今天又抓到一次」
    而变成「今天刚见到」—— 那个读法会让下游去重验一件刚看过的事。"""
    later = FETCHED_AT + timedelta(days=3)
    rows = [
        _evidence(fetched_at=later),
        _evidence(evidence_id="e2", field=ContactField.NAME, literal_value="Jane"),
    ]
    assert moments_from_evidence(rows).first_seen_at == FETCHED_AT


def test_one_page_that_declares_a_date_and_another_that_says_nothing() -> None:
    """沉默**不是**另一个取值。

    一份声明过、另一份什么都没说，不是一个分歧：没有别的时间声称过。
    把沉默当成分歧会让这一格变成 ``None``，而 ``None`` 在那套判定里的读法是
    「没有锚点」—— 一个**已知**的发布时刻会被读成「不知道」。
    """
    page = datetime(2019, 5, 1, tzinfo=timezone.utc)
    rows = [
        _evidence(source_published_at=page),
        _evidence(evidence_id="e2", field=ContactField.NAME, literal_value="Jane"),
    ]
    assert moments_from_evidence(rows).source_published_at == page


def test_two_materials_with_two_publication_dates_leave_that_moment_empty() -> None:
    """两份材料各自声明了不同的发布日 → 不挑。

    与 ``page_publication_time`` 在同一页上遇到两个日期时的处置是同一条纪律，
    只是搬到了上一层：挑一个（哪怕挑更近的）就是替材料认领「哪一天管这一处内容」。
    """
    rows = [
        _evidence(source_published_at=datetime(2019, 5, 1, tzinfo=timezone.utc)),
        _evidence(
            evidence_id="e2",
            field=ContactField.NAME,
            literal_value="Jane",
            source_published_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        ),
    ]
    assert moments_from_evidence(rows).source_published_at is None


def test_no_evidence_rows_give_no_moments() -> None:
    moments = moments_from_evidence([])

    assert moments.first_seen_at is None
    assert moments.source_published_at is None


# --- 契约严格性 ---------------------------------------------------------------


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _route(unexpected_field=1)


def test_models_are_frozen() -> None:
    route = _route()
    with pytest.raises(ValidationError):
        route.email = "x@example.com"  # type: ignore[misc]

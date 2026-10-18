"""证据绑定的测试。

本文件守护两条不变量，它们都不是流程约定而是**类型层**的拒绝：

1. **搜索结果里的邮箱不能直接成为路线。**
   必须「抓取真实公开页面 → 在镜像中发现 literal email → 保存 evidence」。
2. **没有 literal 邮箱就不是一条路线。**
   SOP v1 L126 对 A 与 B **都**要求真实公开业务邮箱。有具名角色却拿不到邮箱时
   产出的是 ``RouteOpportunity`` —— 它是有效产出，但它不是一条可联系的路线，
   因此不能叫 B。旧 pipeline 把 B 读成「有具名角色但没有邮箱」，那是错的。

第二条是本次收敛里最容易回退的一处：把机会当成路线会让「拿到路线的比例」
看起来比实际高，而没有任何一处会报错。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from marketops.adapters.text_pdf import HtmlDocumentParser
from marketops.domain.bind import (
    BindingSubject,
    REASON_ENTRY_ELSEWHERE,
    REASON_INELIGIBLE_SOURCE,
    REASON_UNBOUND_EMAIL,
    RouteVocabulary,
    bind_from_text,
)
from marketops.domain.entities import RouteOpportunity
from marketops.domain.route_records import RouteRejectionCode
from marketops.domain.enums import (
    BusinessFunction,
    ContactField,
    DomainVerdict,
    EntryKind,
    RecordBlockPriority,
    RoleClass,
    RouteBinding,
    SourceKind,
)
from tests.conftest import DEFAULT_PROFILE

FETCHED_AT = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)

#: 这一批构造用的工作区 = 测试用的那个 profile。绑定层把它当成**输入**收下，而不是
#: 自己去问配置 —— 一个会自己去问的绑定层会让「这一行属于谁」取决于进程环境，
#: 那正是 §3.2 要消灭的东西。
WORKSPACE = DEFAULT_PROFILE

#: 测试词表。绑定层不内建业务词，因此每条断言都得先告诉它「什么算什么」。
#: 这与真实 profile 无关 —— 真实词表在 ``profiles/<id>/role-matrix.yaml``。
VOCAB = RouteVocabulary(
    matchable_role_terms=("grounds manager", "head groundskeeper", "finance director"),
    role_classes={
        "grounds manager": RoleClass.OPERATIONAL,
        "head groundskeeper": RoleClass.DECISION,
        "finance director": RoleClass.EXCLUDED,
    },
    business_functions={
        "grounds manager": BusinessFunction.GROUNDS,
        "head groundskeeper": BusinessFunction.GROUNDS,
        "finance director": BusinessFunction.FINANCE,
    },
    generic_mailbox_locals=("info", "enquiries"),
    business_entry_locals={
        "grounds": BusinessFunction.GROUNDS,
        "facilities": BusinessFunction.FACILITIES,
    },
)

SNIPPET_TEXT = "Trade enquiries — contact our sales office at sales@example.co.uk for pricing."

#: 两种入口同页：一个业务口径入口、一个泛化官方邮箱。没有岗位证据，
#: 因此不构成归属冲突，两条都成立。
ENTRY_PAGE = """
<html><body>
  <h1>Example Coatings Ltd</h1>
  <p>Grounds team: grounds@example.co.uk</p>
  <p>General enquiries: info@example.co.uk</p>
</body></html>
"""

#: 具名岗位 + 邮箱在**同一记录块**里 —— 这是强绑定的来源。
NAMED_PAGE = """
<html><body>
  <h1>Example Coatings Ltd</h1>
  <p>Grounds Manager: Jane Doe</p>
  <p>Email: jane.doe@example.co.uk</p>
</body></html>
"""


def _bind(account, text: str, source_kind: SourceKind, **overrides):
    """跑一次绑定。

    第一格收的是 ``AccountRecord`` 而**这一层要的是** :class:`BindingSubject`
    —— 投影在 ``_bind`` 里做一次，于是这个文件里的每一条判据都不必知道绑定层
    用了账号的哪几格。它用的正是这里投影的那三格：``account_id`` /
    ``organisation`` / ``domain``（单数那个变成一元集合）。
    """
    payload = {
        "workspace": WORKSPACE,
        "subject": BindingSubject.from_account_record(account),
        "text": text,
        "source_kind": source_kind,
        "source_url": "https://example.co.uk/contact",
        "content_sha256": "abc123",
        "fetched_at": FETCHED_AT,
        "vocabulary": VOCAB,
    }
    payload.update(overrides)
    return bind_from_text(**payload)


# --- 核心不变量 1：SERP snippet 永不产出路线 -----------------------------------


def test_search_snippet_never_produces_routes(account) -> None:
    """SERP snippet 里的邮箱一律拒绝 —— 即使它格式完全正确。"""
    result = _bind(account, SNIPPET_TEXT, SourceKind.SEARCH_SNIPPET)

    assert result.routes == []
    assert result.opportunities == []
    assert result.evidence == []
    assert len(result.rejected) == 1
    assert result.rejected[0].reason.startswith(REASON_INELIGIBLE_SOURCE)
    assert result.rejected[0].literal_value == "sales@example.co.uk"


def test_snippet_rejection_covers_role_and_name_too(account) -> None:
    """不只邮箱 —— snippet 里的任何字段都不能成为证据。"""
    text = "Grounds Manager: Jane Doe — jane.doe@example.co.uk"
    result = _bind(account, text, SourceKind.SEARCH_SNIPPET)
    assert result.routes == []
    assert len(result.rejected) == 3  # email + name + role


# --- 抓取页面产出的两类东西 ---------------------------------------------------


def test_fetched_html_produces_routes(account) -> None:
    """同样一段内容，来自真实抓取的页面时就能进入结果。"""
    result = _bind(account, ENTRY_PAGE, SourceKind.FETCHED_HTML)

    assert len(result.routes) == 2
    assert {route.email for route in result.routes} == {
        "grounds@example.co.uk",
        "info@example.co.uk",
    }
    assert result.evidence
    assert all(item.source_kind is SourceKind.FETCHED_HTML for item in result.evidence)


def test_entry_kind_separates_business_entry_from_generic_mailbox(account) -> None:
    """C 与 D 的分界是**入口性质**，不是等级本身 —— 所以它要在绑定期就分好。

    ``grounds@`` 与 ``info@`` 都是「官方入口」，但一个带场地业务口径、
    一个只是泛化信箱。把两者压成同一个 entry_kind，C/D 就没法区分。
    """
    result = _bind(account, ENTRY_PAGE, SourceKind.FETCHED_HTML)
    by_email = {route.email: route for route in result.routes}

    business = by_email["grounds@example.co.uk"]
    assert business.entry_kind is EntryKind.OFFICIAL_BUSINESS_ENTRY
    assert business.business_function is BusinessFunction.GROUNDS
    # P2 的原文是「相关岗位**或部门**与邮箱绑定」—— 部门邮箱正是这一级。
    assert business.block_priority is RecordBlockPriority.P2

    generic = by_email["info@example.co.uk"]
    assert generic.entry_kind is EntryKind.GENERIC_OFFICIAL
    assert generic.business_function is BusinessFunction.UNKNOWN
    assert generic.block_priority is RecordBlockPriority.P4


def test_named_role_in_the_same_block_is_a_strong_binding(account) -> None:
    """同块具名岗位 + 域名一致 = 强绑定（SOP L125/L126 都要求它）。"""
    result = _bind(account, NAMED_PAGE, SourceKind.FETCHED_HTML)

    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.email == "jane.doe@example.co.uk"
    assert route.person_name == "Jane Doe"
    assert route.role_title == "Grounds Manager"
    assert route.role_class is RoleClass.OPERATIONAL
    assert route.business_function is BusinessFunction.GROUNDS
    assert route.entry_kind is EntryKind.NAMED_ROLE
    assert route.binding is RouteBinding.STRONG
    assert route.block_priority is RecordBlockPriority.P1
    assert route.has_conflict is False


def test_role_without_a_person_is_p2(account) -> None:
    """同块有岗位、没有具名人 —— P2 说的是这件事，不是「URL 像 /staff/」。"""
    text = "<p>Grounds Manager</p><p>grounds.office@example.co.uk</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.role_title == "Grounds Manager"
    assert route.person_name == ""
    assert route.block_priority is RecordBlockPriority.P2


# --- 核心不变量 2：没有邮箱就不是路线 -----------------------------------------


def test_named_role_without_email_is_an_opportunity_not_a_route(account) -> None:
    """有具名岗位、没有邮箱 —— 这是**未解决的路线机会**，不是一条 B 级路线。

    SOP L126 的 B 要求「真实公开业务邮箱」。把它记成 B 就等于声称我们取得了
    一条可联系的路线，而我们没有 —— 那是本文件要拦的最好发生的事。
    """
    text = "<p>Our Grounds Manager leads specification.</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.routes == [], "没有 literal 邮箱不得产出路线"
    assert len(result.opportunities) == 1
    opportunity = result.opportunities[0]
    assert opportunity.role_title == "Grounds Manager"
    assert opportunity.role_class is RoleClass.OPERATIONAL
    assert opportunity.block_priority is RecordBlockPriority.P3
    assert opportunity.evidence_ids, "机会也必须有证据 —— 否则它只是一个猜测"
    assert not hasattr(opportunity, "email"), "机会按定义没有邮箱这一列"


def test_opportunity_ids_are_evidence_backed(account) -> None:
    """机会要能用证据读回来 —— 它是一份待办，不是一句印象。"""
    text = "<p>Our Grounds Manager leads specification.</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    evidence_ids = {item.evidence_id for item in result.evidence}
    assert set(result.opportunities[0].evidence_ids) <= evidence_ids


def test_opportunity_needs_a_literal_name_or_role() -> None:
    """一个既没有姓名也没有岗位的「机会」只是「这里什么都没有」。"""
    with pytest.raises(ValidationError, match="姓名或字面岗位"):
        RouteOpportunity(workspace=WORKSPACE, account_id="a1")


def test_two_names_without_an_email_are_two_opportunities(account) -> None:
    """一页两个具名、没有邮箱 —— **不得**抛异常，也不得丢掉任何一处。

    这是本文件里唯一一条覆盖「构造期抛错」的判据，而它守的是一整类页面：
    英国一个村委/俱乐部的委员会页通常正好列两三个具名而不给邮箱。

    修之前这条路径抛 ``ValidationError``（``RouteOpportunity`` 要求至少有一个
    字面姓名或岗位），而两处命中被同一个 ``len(...) == 1`` 过滤同时清空 ——
    名字都不唯一。改法不是给那个模型放松要求，是**不把路线那一侧的归属判据
    搬到没有东西可供归属的地方**：不合并，逐处产出。
    """
    text = "<p>Contact: Jane Doe</p><p>Contact: John Roe</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.routes == []
    assert [item.person_name for item in result.opportunities] == ["Jane Doe", "John Roe"]
    assert all(item.role_title == "" for item in result.opportunities)


def test_an_ambiguous_page_still_reports_every_literal_it_found(account) -> None:
    """两个具名 + 两个岗位、没有邮箱：三处字面命中，三处都要报出来。

    报出来的东西**互不认领**：每条机会要么只带姓名、要么只带岗位。
    「这一页写了 Grounds Manager」与「这一页写了 Jane Doe」是两条事实，
    「Jane Doe 是 Grounds Manager」是第三条 —— 而第三条我们没有依据。
    """
    text = "<p>Grounds Manager: Jane Doe</p><p>Head Groundskeeper: John Roe</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.routes == []
    reported = {(item.person_name, item.role_title) for item in result.opportunities}
    assert reported == {
        ("Jane Doe", ""),
        ("", "Grounds Manager"),
        ("", "Head Groundskeeper"),
    }
    assert all(
        bool(item.person_name) != bool(item.role_title) for item in result.opportunities
    ), "不唯一时不得合并 —— 一条机会同时带姓名与岗位就是在替我们认领归属"
    assert all(item.evidence_ids for item in result.opportunities)


def test_combining_needs_both_sides_to_be_unique(account) -> None:
    """合并的前提是**两边各自唯一**：一边唯一、另一边不唯一时也不合并。

    这条把上一条与「一个具名岗位、没有邮箱 → 一条机会」连起来：差别只在
    岗位那一边多了一处命中。
    """
    unique = _bind(account, "<p>Grounds Manager: Jane Doe</p>", SourceKind.FETCHED_HTML)
    assert [(item.person_name, item.role_title) for item in unique.opportunities] == [
        ("Jane Doe", "Grounds Manager")
    ]

    ambiguous = _bind(
        account,
        "<p>Grounds Manager: Jane Doe</p><p>Head Groundskeeper leads the team.</p>",
        SourceKind.FETCHED_HTML,
    )
    assert [(item.person_name, item.role_title) for item in ambiguous.opportunities] == [
        ("Jane Doe", ""),
        ("", "Grounds Manager"),
        ("", "Head Groundskeeper"),
    ]


# --- 保守归属 -----------------------------------------------------------------


def test_multiple_emails_plus_role_are_flagged_as_conflict(account) -> None:
    """一个页面里多个邮箱 + 一个角色 → 无法判断谁是谁。不猜，标记冲突。"""
    text = "<p>Grounds Manager</p><p>grounds@example.co.uk</p><p>info@example.co.uk</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert len(result.routes) == 2
    assert all(route.has_conflict for route in result.routes)
    assert result.false_attribution_risk is True
    assert all(route.role_title == "" for route in result.routes)


def test_no_role_terms_means_no_role_evidence(account) -> None:
    """profile 未提供角色词表时，不猜角色 —— 分类保持 UNKNOWN。"""
    text = "<p>Grounds Manager: Jane Doe</p><p>jane.doe@example.co.uk</p>"
    result = _bind(
        account, text, SourceKind.FETCHED_HTML, vocabulary=RouteVocabulary()
    )

    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.role_title == ""
    assert route.role_class is RoleClass.UNKNOWN
    # 姓名仍然抽得到：它是**标签**驱动的，不依赖业务词表。
    assert route.person_name == "Jane Doe"
    # 没有岗位分类、也没有可识别的入口性质时，记录块层级是 None ——
    # 这个取值有含义：「SOP 没有定义这一级」。
    assert route.block_priority is None


# --- 排除类别优先于入口性质 ---------------------------------------------------


def test_excluded_role_makes_the_entry_excluded(account) -> None:
    """写着 Finance 的邮箱是财务入口 —— 类别判定优先于入口判定。

    SOP L129 把财务放进 X。若先看邮箱 local part，一个 ``info@`` 会先被判成
    泛化官方入口（D），财务岗位就被这条兜底路径洗白了。
    """
    text = "<p>Finance Director: Jane Doe</p><p>info@example.co.uk</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.role_class is RoleClass.EXCLUDED
    assert route.entry_kind is EntryKind.EXCLUDED
    assert route.business_function is BusinessFunction.FINANCE


# --- 归属边界 -----------------------------------------------------------------


def test_foreign_domain_email_is_not_attributed(account) -> None:
    """同页出现的其他组织邮箱不得归属目标账户（SOP L121）。

    **这一格只在域名权威是** ``confirmed`` **时才成立。** 「这个邮箱的域与账号的
    域不同」是一个结论，而它要求先知道账号的域是哪一个 —— 那正是
    ``domain_verdict`` 回答的问题。所以下面的三种拼法各有一条判据：
    证得过（拒）、只是转述（不拒、按入口证据活）、没查过（不拒）。
    """
    subject = BindingSubject.from_account_record(account)
    assert subject.domain_verdict is DomainVerdict.UNKNOWN

    confirmed = replace(
        subject,
        domains=("example.co.uk",),
        domain_verdict=DomainVerdict.CONFIRMED,
    )
    text = "<p>Grounds Manager</p><p>grounds@other-org.example</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML, subject=confirmed)

    assert result.routes == []
    assert [item.code for item in result.rejected] == [RouteRejectionCode.FOREIGN_DOMAIN]


def test_a_roster_domain_is_a_quotation_and_does_not_reject_on_its_own(account) -> None:
    """**G1 的反例之一：unknown 的域名不得当成已确认的假。**

    ``AccountRecord.domain`` 是一份清单里的一格 —— 别人写下来的一句话。拿它当
    域名权威，会得到两种都错的结局之一：清单那一格过期/写错时，本账号官网上登着
    的邮箱被拒成「外来的」；而清单说不出的那些账号，则一条拒信都不会有。

    所以这一层的规则是：域名权威只有 ``confirmed`` 一种写法（见
    ``BindingSubject`` 的类文档）。这一格里清单写着 ``example.co.uk``，而判定是
    ``unknown`` —— 于是 ``other-org.example`` 这条**不因域名被拒**。它按同块里
    的岗位证据活着（SOP L105 的记录块语义），等级是 ACCOUNT 那一档。
    """
    text = "<p>Grounds Manager</p><p>grounds@other-org.example</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert [item.code for item in result.rejected] == []
    (route,) = result.routes
    assert route.email == "grounds@other-org.example"
    # 证据不够时它落成机会（hold），而不是落成一条被拒的绑定 —— 两件事在
    # 报告里必须分得开：前者是「还没证明」，后者是「证明了不是它」。
    assert result.opportunities == []


def test_unbound_email_is_rejected_rather_than_graded(account) -> None:
    """既无同块岗位/姓名、也不是业务入口或泛化信箱的邮箱还不构成一条路线。

    ``sales@`` 落在这里：它不是 D（D 是泛化官方邮箱），也不是 C（C 要求业务
    口径入口），更不是 A/B（要求具名角色）。证据全部保留，但不产出路线 ——
    「它是什么等级」这个问题在这里没有答案，而不是答案是 X。
    """
    text = "<p>Trade enquiries: sales@example.co.uk</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.routes == []
    assert result.evidence, "证据必须保留 —— 拒绝的是路线，不是这次抓取"
    assert any(item.reason.startswith(REASON_UNBOUND_EMAIL) for item in result.rejected)


# --- 证据质量 -----------------------------------------------------------------


def test_evidence_records_context_and_digest(account) -> None:
    result = _bind(account, ENTRY_PAGE, SourceKind.FETCHED_HTML)
    email_evidence = [item for item in result.evidence if item.field is ContactField.EMAIL]

    assert email_evidence[0].content_sha256 == "abc123"
    assert "grounds@example.co.uk" in email_evidence[0].context
    assert email_evidence[0].source_url == "https://example.co.uk/contact"


def test_evidence_ids_are_content_addressed(account) -> None:
    """同一处字面证据重复出现时 id 相同 —— 这让写入天然幂等。"""
    first = _bind(account, ENTRY_PAGE, SourceKind.FETCHED_HTML)
    second = _bind(account, ENTRY_PAGE, SourceKind.FETCHED_HTML)
    assert [item.evidence_id for item in first.evidence] == [
        item.evidence_id for item in second.evidence
    ]


# --- §9：这一页是什么时候的 ---------------------------------------------------


def test_every_evidence_row_carries_the_page_publication_time(account) -> None:
    """发布时刻是**这一页**的性质，所以同一页的所有行说的是同一件事。

    这一格是 §9 与 ``fetched_at`` 的分水岭：一份 2019 年的 PDF 今天被我们抓下来，
    ``fetched_at`` 是今天，而 ``source_published_at`` 是 2019 —— 两者分开之前，
    「Jane 在 2019 年是场地主管」与「今天的页面上说 Jane 是场地主管」在数据里
    长得一模一样。
    """
    text = ENTRY_PAGE.replace(
        "<h1>Example Coatings Ltd</h1>",
        "<h1>Example Coatings Ltd</h1><p>Published: 1 March 2026</p>",
    )
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.evidence, "这一页应当产出证据"
    assert {item.source_published_at for item in result.evidence} == {
        datetime(2026, 3, 1, tzinfo=timezone.utc)
    }
    assert all(item.fetched_at == FETCHED_AT for item in result.evidence)


def test_a_page_without_a_publication_date_says_so_with_none(account) -> None:
    """页面没写发布日期 → ``None``，而**不是**取回时刻。

    用 ``fetched_at`` 顶上会让每一页都看起来是刚发布的，而那正是这一格要消灭的
    那个读法。``None`` 在这里有唯一一个意思：来源没有声明。
    """
    result = _bind(account, ENTRY_PAGE, SourceKind.FETCHED_HTML)

    assert result.evidence
    assert {item.source_published_at for item in result.evidence} == {None}


def test_a_page_with_two_different_publication_dates_does_not_pick_one(account) -> None:
    """两个互不相同的发布日 → 不挑，留 ``None``。

    挑更近的那一个看起来更保守，实际是在替这份材料认领「哪一天管这一处内容」，
    而依据不在页面上。判据与「A 页的姓名 + B 页的邮箱」是同一条。
    """
    text = ENTRY_PAGE.replace(
        "<h1>Example Coatings Ltd</h1>",
        "<h1>Example Coatings Ltd</h1>"
        "<p>Published 1 March 2026</p><p>Published 4 April 2026</p>",
    )
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.routes, "这一页仍然是一条路线 —— 发布时间缺失不阻断绑定"
    assert {item.source_published_at for item in result.evidence} == {None}


def test_every_route_carries_the_moments_of_the_page_it_came_from(account) -> None:
    """§9·2：路线那一行自己带两个时刻 —— 取回时刻与来源声明的发布时刻。

    它们**折自这一行手上的那几行证据**（``evidence.moments_from_evidence``），
    不是在这里另算一遍：取回时刻取最早那次、发布时刻不唯一时留空，判据只有一处。
    """
    text = ENTRY_PAGE.replace(
        "<h1>Example Coatings Ltd</h1>",
        "<h1>Example Coatings Ltd</h1><p>Published: 1 March 2026</p>",
    )
    result = _bind(account, text, SourceKind.FETCHED_HTML)
    published = datetime(2026, 3, 1, tzinfo=timezone.utc)

    assert result.routes, "这一页应当产出路线"
    for route in result.routes:
        assert route.first_seen_at == FETCHED_AT
        assert route.source_published_at == published
        # 这两格今天**没有生产者**：重新抓一次材料不是重新验证，所以它们只能
        # 留给「真的去核过」的那一步 —— 在那之前，诚实的取值是「没有」。
        assert route.last_verified_at is None
        assert route.valid_from is None and route.valid_to is None


def test_a_route_from_an_undated_page_keeps_the_fetch_moment_only(account) -> None:
    """页面没写发布日期：这一行仍然知道「我们什么时候看到的」，但不知道「它是
    什么时候的」。

    这两件事分开**正是** §9 要的：把 ``fetched_at`` 顶上发布时刻，会让一份 2019 年
    的老页面在今天看起来是刚发布的；反过来因为不知道发布时刻就连取回时刻都不记，
    丢掉的是唯一一个我们知道的事实。
    """
    result = _bind(account, ENTRY_PAGE, SourceKind.FETCHED_HTML)

    assert result.routes
    for route in result.routes:
        assert route.first_seen_at == FETCHED_AT
        assert route.source_published_at is None


def test_a_page_that_does_not_pick_a_publication_date_gives_the_route_none(account) -> None:
    """一页两个发布日 → 那一格是 ``None``，而路线照旧成立。

    判定缺失**不阻断绑定**：这一格是描述这一行的，不是允许它存在的条件。
    """
    text = ENTRY_PAGE.replace(
        "<h1>Example Coatings Ltd</h1>",
        "<h1>Example Coatings Ltd</h1>"
        "<p>Published 1 March 2026</p><p>Published 4 April 2026</p>",
    )
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.routes
    for route in result.routes:
        assert route.source_published_at is None
        assert route.first_seen_at == FETCHED_AT


def test_an_opportunity_carries_the_same_two_moments(account) -> None:
    """机会与路线走同一条判据（同一个函数、同两个格）。

    机会也会过期：那个人可能已经离任，或者已经有邮箱了 —— 而「我们什么时候看到
    这条线索」与「来源说它是什么时候的」在两种行上逐字相同。
    """
    text = "<p>Published: 1 March 2026</p><p>Our Grounds Manager leads specification.</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.opportunities, "这一页应当产出机会"
    for opportunity in result.opportunities:
        assert opportunity.first_seen_at == FETCHED_AT
        assert opportunity.source_published_at == datetime(2026, 3, 1, tzinfo=timezone.utc)


# --- literal 性 ---------------------------------------------------------------


def test_obfuscated_email_is_recorded_but_not_decoded(account) -> None:
    """JS 混淆的邮箱只登记事实，不还原 —— 与「这里没有邮箱」是两回事。"""
    text = "<p>Contact: sales (at) example (dot) co.uk</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.obfuscated_only is True
    assert result.routes == []
    assert result.opportunities == []
    assert result.evidence == []


def test_plain_page_without_emails_yields_nothing(account) -> None:
    result = _bind(account, "<p>We sell paint.</p>", SourceKind.FETCHED_HTML)
    assert result.routes == []
    assert result.opportunities == []
    assert result.evidence == []
    assert result.rejected == []


def test_only_the_domain_is_lowercased(account) -> None:
    """归一化只动域名。

    本地部分在 RFC 5321 下是大小写敏感的，随手 lower() 会把两个不同的
    邮箱并成一个 —— 那是 false attribution 的一种，而且看不出来。
    """
    text = "<p>Grounds Manager</p><p>Grounds.Office@Example.co.uk</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    route = result.routes[0]
    assert route.email == "Grounds.Office@example.co.uk"
    assert route.email_domain == "example.co.uk"


# --- §9·3 归属要有位置证据：A 处的姓名不得配在 B 处的邮箱上 ----------------------
#
# 这一节守的是 ``P1`` 在 ``grading.yaml`` 里的原文：**「相关人 / 岗位 / 邮箱在同一
# record block 内 —— 归属可以确认」**。在这一节之前，绑定的这一层**从不计算块**：
# 整页有一个姓名、有一个邮箱，就产出 P1 + STRONG，而两处可以隔着整篇文章。
#
# 这一节里的每一条都成对出现：一条证明**隔开的不再成立**，一条证明**挨着的仍然成立**。
# 只有前一半，把窗口收到 0 也会全绿；只有后一半，把窗口放到整页也会全绿。


def test_a_name_from_another_entry_does_not_make_a_route(account) -> None:
    """人名在一张卡片上、邮箱在另一处，中间没有任何关系证据 → 不缝成路线。

    这是 §9 逐字禁止的那个读法。这一页上「Grounds Manager: Jane Doe」与那条邮箱
    之间隔着一段正文：页面**没有**说过这条邮箱是 Jane 的，而 ``P1`` 的含义正是
    「页面说过」。
    """
    text = (
        "Grounds Manager: Jane Doe\n"
        "Our grounds are open to members throughout the year.\n"
        "Email: jane.doe@example.co.uk"
    )
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert result.routes == [], "同页不等于同一个人"
    assert any(item.reason.startswith(REASON_ENTRY_ELSEWHERE) for item in result.rejected)
    # 两处命中都还在 —— 拒绝的是这条归属，不是这次抓取。
    assert {item.field.value for item in result.evidence} == {"name", "role", "email"}


def test_the_person_and_the_email_on_one_line_are_a_strong_binding(account) -> None:
    """同一行上的人 + 岗位 + 邮箱 → P1 + STRONG。收窗口不能收到把这一类也砍掉。"""
    text = "Grounds Manager: Jane Doe — jane.doe@example.co.uk"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.person_name == "Jane Doe"
    assert route.role_title == "Grounds Manager"
    assert route.block_priority is RecordBlockPriority.P1
    assert route.binding is RouteBinding.STRONG


def test_one_line_below_is_still_the_same_card(account) -> None:
    """卡片排成两行（``Contact:`` 一行、``Email:`` 一行）仍然算同一张卡片。

    这是**刻意留下的**那一侧：窗口取 0 会让这一类页面（自建站与 CMS 里最常见的
    联系页写法）一条路线都产不出来。判据本身与其边界在
    ``tests/test_extract.py::test_two_entries_away_is_not_the_same_entry``。
    """
    text = "Contact: Jane Doe\nEmail: jane.doe@example.co.uk"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert len(result.routes) == 1
    assert result.routes[0].person_name == "Jane Doe"
    assert result.routes[0].binding is RouteBinding.STRONG


def test_a_person_on_one_of_several_emails_line_is_still_not_attributed(account) -> None:
    """多个邮箱并存时，**位置不解决归属** —— 印在同一行也不算。

    挨着是最容易说服自己的那一档（名字与邮箱之间只隔一个破折号），而它恰恰不是
    证据：同一张卡片上可以印两个邮箱，一个联系人的名字旁边挂着的可能是前台信箱。
    所以这一支的规则不是「按位置挑最近的」，而是**整支不归属**。

    这一条是补上来的：把 ``single`` 写成常量 ``True``（每个邮箱各自按窗口归属）
    跑变异检查时，**只有一条**测试转红，而且它红在 ``has_conflict`` 从真变假 ——
    也就是说「多邮箱一律不归属」这条规则此前**没有任何一条测试直接量过**，
    它只是被另一条判据的副产品碰到了。§9 把这件事写成两句不同的话：
    「A 处的人配 B 处的邮箱」与「同页不等于同一个人」，各自都要有自己的执行器。
    """
    text = "<p>Contact: Jane Doe — grounds@example.co.uk</p><p>info@example.co.uk</p>"
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert len(result.routes) == 2
    assert all(route.person_name == "" for route in result.routes), (
        "名字与其中一条邮箱印在同一行，但它不得被算作那条邮箱的人"
    )
    assert all(route.block_priority is not RecordBlockPriority.P1 for route in result.routes)


def test_a_role_from_another_entry_does_not_upgrade_the_route_to_p1(account) -> None:
    """人挨着邮箱、岗位在别处 → 不得算 P1。

    P1 要求人、岗位、邮箱**三者**在同一张卡片上。岗位来自别处时，这条产出仍然
    是一条路线（人是可确认的），但它没有 ``P1`` 所声称的那件事，因此层级落到
    ``None`` —— 那个取值的意思正是「SOP 没有定义这一级」。
    """
    text = (
        "Contact: Jane Doe — jane.doe@example.co.uk\n"
        "Our grounds are open to members throughout the year.\n"
        "Grounds Manager"
    )
    result = _bind(account, text, SourceKind.FETCHED_HTML)

    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.person_name == "Jane Doe"
    assert route.role_title == "", "别处的岗位不得算在这张卡片上"
    assert route.block_priority is None
    assert route.entry_kind is EntryKind.NAMED_ROLE


def test_the_window_is_read_from_the_parsed_page_not_from_the_source_layout(
    account, tmp_path
) -> None:
    """同一页 HTML，压缩写与缩进写，绑定结论必须一样。

    绑定的输入是解析器交出来的文本，而**换行的条数**取决于源文件怎么排版：块级标签
    变成换行符，源文件自己的缩进换行也留在里面。窗口只数非空行，所以一份压缩过的
    HTML 与一份每个标签各占一行的 HTML 得到同一个答案 —— 它们是同一个页面。
    """
    card = "<p>Contact: Jane Doe</p><p>Grounds Manager — jane.doe@example.co.uk</p>"
    # 压缩写：整份 HTML 一行。缩进写：每个块级标签各占一行，于是标签之间多出
    # 空行（源文件的缩进换行也留在文本里）。两份是同一个页面。
    minified = "<html><body><h1>Example Coatings Ltd</h1>" + card + "</body></html>"
    pretty = (
        "<html>\n  <body>\n    <h1>Example Coatings Ltd</h1>\n    "
        + card.replace("</p><p>", "</p>\n    <p>")
        + "\n  </body>\n</html>\n"
    )

    def _text(name: str, html: str) -> str:
        path = tmp_path / name
        path.write_text(html, encoding="utf-8")
        return HtmlDocumentParser().parse(path, content_type="text/html").text

    route = _bind(account, _text("minified.html", minified), SourceKind.FETCHED_HTML).routes
    other = _bind(account, _text("pretty.html", pretty), SourceKind.FETCHED_HTML).routes

    assert len(route) == len(other) == 1
    assert route[0].email == other[0].email == "jane.doe@example.co.uk"
    assert route[0].person_name == other[0].person_name == "Jane Doe"
    assert route[0].block_priority is other[0].block_priority is RecordBlockPriority.P1


def test_an_email_whose_entry_says_nothing_still_reports_the_other_reason(account) -> None:
    """「这一页上没有人」与「人在别处」是两句不同的话，拒绝原因分开报。

    没有这一条，``entry_elsewhere`` 可以被写成 ``unbound_email`` 而全绿 —— 于是
    「这一页没给出归属」与「这一页给了别处的人」在报告上同形，而它们的处置不同：
    后者要的是回那个页面找这张卡片。
    """
    elsewhere = _bind(
        account,
        "Grounds Manager: Jane Doe\nMembers' notes\nsales@example.co.uk",
        SourceKind.FETCHED_HTML,
    )
    nothing = _bind(
        account,
        "Members' notes\nsales@example.co.uk",
        SourceKind.FETCHED_HTML,
    )

    assert [item.code for item in elsewhere.rejected] == [
        RouteRejectionCode.ENTRY_ELSEWHERE
    ]
    assert [item.code for item in nothing.rejected] == [
        RouteRejectionCode.UNBOUND_EMAIL
    ]

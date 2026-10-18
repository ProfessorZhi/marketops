"""§4.2 的八条判据：**官网归属**这件事到底是怎么被算出来的。

这一份测的是 ``domain/domain_attribution.py`` 这个**纯函数**，以及它在下游那一处
的落点（``bind.py`` 的 L121 拒信）。

它存在的理由是 2026-09-26 那次审计读到的一件事：``official_domain`` 那一格装着
**SERP 抓回来的主机**，却被下游读成「这是它的官网」。于是「这个邮箱的域与账号的域
不同 ⇒ 它不是这个账号的」这条拒信，第一条前提是一台目录站或一份社媒页的主机名 ——
报告上每一行都看起来正常。

所以这里每一条判据都问同一个问题的不同侧面：**这句话是谁说的、凭什么。**
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from marketops.domain.bind import (
    BindingSubject,
    RouteVocabulary,
    bind_from_text,
)
from marketops.domain.domain_attribution import (
    OfficialDomainVocabulary,
    PageObservation,
    attribute_official_domain,
    host_is_explained_by,
    mentioned_hosts,
    organisation_tokens,
)
from marketops.domain.enums import Cohort, DomainVerdict, SourceKind
from marketops.domain.route_records import RouteRejectionCode
from marketops.domain.evidence import AccountRecord

#: 一份**测试用的**词表。它不读 profile，也不含任何真实市场词 ——
#: 判据是「词表是怎么用的」，不是「词表里有哪些词」。
VOCAB = OfficialDomainVocabulary(
    third_party_hosts=("facebook.com", "instagram.com", "wikipedia.org", "bookings.example"),
    name_stopwords=("the", "and", "club", "ltd"),
    min_name_matches=2,
    min_word_length=4,
)

#: 这一组常量是**配合**的：组织名有两个够长的实词，而主机的名字里两个都读得出来
#: （``min_name_matches=2`` 要求两个）。写测试时最容易错的是挑一台只对得上一个词的
#: 主机 —— 那会让判据红在一个与所考之事无关的地方。
ORGANISATION = "Riverside Athletic Club"
SITE_HOST = "riverside-athletic.example.co.uk"
SITE = f"https://{SITE_HOST}/contact"
NORTH_HOST = "riverside-athletic-north.example.org"

#: 绑定层不内建业务词，所以这里要给它一份。**只有一条岗位词、没有分类** ——
#: 本文件考的是域名归属，角色分几类与它无关。
BINDING_VOCAB = RouteVocabulary(
    matchable_role_terms=("grounds manager",),
    role_classes={},
    business_functions={},
    generic_mailbox_locals=("info",),
    business_entry_locals={},
)

FETCHED_AT = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def page(host: str, *, url: str = "", text: str = "", evidence: tuple[str, ...] = ("ev-1",)):
    """一页。``evidence`` 默认非空 —— 「读过一页」这件事在报告里就是那一行证据。"""
    return PageObservation(host=host, url=url or f"https://{host}/", text=text, evidence_ids=evidence)


# === 1. 社媒页指向真实官网 =====================================================


def test_a_social_page_pointing_at_the_real_site_confirms_the_real_site() -> None:
    """**facebook.com → 真实组织官网。** 这是审计里那条读法的正面版本。

    我们取回的是一台社媒主机，而那一页自己写着这个组织的官网。候选里于是有**两台**：
    社媒那台（profile 声明过的第三方 ⇒ 拒）与正文里写出来的那台（名字读得出来、
    有这一页的证据 ⇒ 确认）。结论是一台真实主机，不是一个社媒地址。
    """
    finding = attribute_official_domain(
        ORGANISATION,
        (
            page(
                "www.facebook.com",
                url="https://www.facebook.com/riversideathletic",
                text=f"{ORGANISATION} — official site: {SITE}",
            ),
        ),
        VOCAB,
    )

    assert finding.verdict is DomainVerdict.CONFIRMED
    assert finding.official_domains == (SITE_HOST,)
    assert finding.confirmed_domain == SITE_HOST
    assert [host for host, _ in finding.rejected_hosts] == ["www.facebook.com"]
    assert "第三方" in dict(finding.rejected_hosts)["www.facebook.com"]
    # 判定带证据 —— 一个 confirmed 却没有出处的结论读起来像一句断言。
    assert finding.evidence_ids == ("ev-1",)


def test_a_third_party_matches_on_label_boundaries_only() -> None:
    """``facebook.com`` 命中 ``m.facebook.com``，**不**命中 ``riversidefacebook…``。

    子串匹配在这里会静默错放：一台名字里带 ``facebook`` 的主机是这个组织自己的
    官网，而它会被当成社媒排除掉 —— 「排除得太宽」在报告上长得像一次正常判定。
    """
    finding = attribute_official_domain(
        "Riverside Facebook Club",
        (page("riverside-facebook.example", text="Riverside Facebook Club"),),
        VOCAB,
    )
    assert finding.verdict is DomainVerdict.CONFIRMED
    assert finding.official_domains == ("riverside-facebook.example",)


# === 2. 第三方托管页面 → 目标组织 ==============================================


def test_a_booking_page_is_never_the_venue_official_domain() -> None:
    """**第三方托管页面 → 目标组织。** 预约页说明「你可以在那里订到它」，
    一个字都没说「那是它的官网」。

    判据不是「这个主机长得像预约站」—— 那是关键词黑名单，本仓明确禁止。
    判据是 profile **声明过**它，而声明是一份可审计的词表（``third_party_hosts``）。
    """
    finding = attribute_official_domain(
        ORGANISATION,
        (
            page(
                "bookings.example",
                url="https://bookings.example/venues/riverside-athletic-club",
                text=f"Book {ORGANISATION} — a natural grass pitch in Devon.",
            ),
        ),
        VOCAB,
    )

    assert finding.verdict is DomainVerdict.UNKNOWN, (
        "这一批页面里只有一台第三方主机 —— 它能证明的只有「这台不是它的官网」，"
        "证明不了「它没有官网」"
    )
    assert finding.official_domains == ()
    assert [host for host, _ in finding.rejected_hosts] == ["bookings.example"]
    # 而它是**我们读到过的一台主机**，所以台账上说得出来。
    assert finding.discovered_hosts == ("bookings.example",)


# === 3. 共享域 → 两个组织 ======================================================


def test_a_shared_host_is_confirmed_for_neither_organisation() -> None:
    """**同一个共享域 → 两个组织。** 英国基层俱乐部大量挂在聚合域名下。

    判据是名字：聚合域的名字里读不出这个组织的实词，所以对**两边**都判不出来。
    这一条的意义在于它**没有**做的事 —— 它没有把「这一页提到了它」当成归属，
    也没有把两台候选里挑一台当结论。两边都是 ``unknown``，于是下游那一条
    「域不同 ⇒ 拒信」的前提不存在，两个组织的邮箱都不会因为这条被拒。
    """
    shared = page(
        "Example Clubs.example",
        url="https://Example Clubs.example/clubs",
        text="Example Clubs — Exeter Harriers and Tiverton Rovers.",
    )

    for organisation in ("Exeter Harriers", "Tiverton Rovers"):
        finding = attribute_official_domain(organisation, (shared,), VOCAB)
        assert finding.verdict is DomainVerdict.UNKNOWN, organisation
        assert finding.official_domains == (), organisation
        assert [host for host, _ in finding.rejected_hosts] == ["Example Clubs.example"]


# === 4. 一个组织多个官网 =======================================================


def test_two_explainable_hosts_are_both_confirmed_and_the_singular_cell_stays_empty() -> None:
    """**一个组织多个官网。** 主站与分区站都在这一批页面里，两台**都是**它的官网。

    从两台里挑一台（比如取第一个、或取最长的那一个）会让「这个组织的官网」在两次
    运行里给出两个不同的答案，而报告上两次都看起来正常。所以集合有两个元素，
    而 ``confirmed_domain`` 那一格 —— 它是**一个格子** —— 留空。
    """
    finding = attribute_official_domain(
        ORGANISATION,
        (
            page(SITE_HOST, text=f"{ORGANISATION}", evidence=("ev-1",)),
            page(NORTH_HOST, text=f"{ORGANISATION} — North", evidence=("ev-2",)),
        ),
        VOCAB,
    )

    assert finding.verdict is DomainVerdict.CONFIRMED
    assert finding.official_domains == (NORTH_HOST, SITE_HOST)
    assert finding.confirmed_domain == "", (
        "两个官网时单数那一格留空是刻意的：随便挑一个会让「它的官网」有两个答案"
    )
    # 证据跟着主机顺序走（字典序），所以是 ``ev-2`` 在前 —— 顺序不承重，
    # **集合**承重：两台主机的证据都在，没有一台被悄悄漏掉。
    assert set(finding.evidence_ids) == {"ev-1", "ev-2"}


# === 5. 同名、不同国家 =========================================================


def test_the_same_name_in_another_country_is_not_this_organisations_site() -> None:
    """**同名不同地区。** 分家这件事落在**身份**那一边（§4 B10），而这里钉的是
    域名归属这一侧的读法：一台主机读得出这个名字，**不等于**它属于**这个**组织。

    ``discovered_hosts`` 说的是「我们真的取回了哪几台」，``official_domains`` 说的是
    「其中哪几台是它的」。判据看不见第二个组织的存在 —— 所以这一层**不能**代替身份
    分家：另一国的那一台在这批页面里没出现过，它就不会出现在这一格的任何一处。
    """
    australia = attribute_official_domain(
        ORGANISATION,
        (page("riverside-athletic.example.au", text=f"{ORGANISATION} (Australia)"),),
        VOCAB,
    )
    assert australia.official_domains == ("riverside-athletic.example.au",)

    home = attribute_official_domain(ORGANISATION, (page(SITE_HOST, text=ORGANISATION),), VOCAB)
    assert home.official_domains == (SITE_HOST,)
    assert "riverside-athletic.example.au" not in home.official_domains


# === 6/7/8. 下游那一条拒信的三种拼法 ===========================================


def _subject(domains: tuple[str, ...], verdict: DomainVerdict) -> BindingSubject:
    return BindingSubject(
        account_id="acct-riverside",
        organisation=ORGANISATION,
        domains=domains,
        domain_verdict=verdict,
    )


def _bind(subject: BindingSubject, text: str):
    return bind_from_text(
        workspace="uk-acme-natural-grass",
        subject=subject,
        text=text,
        source_kind=SourceKind.FETCHED_HTML,
        source_url=SITE,
        content_sha256="a" * 64,
        fetched_at=FETCHED_AT,
        vocabulary=BINDING_VOCAB,
    )


def test_a_cross_domain_email_is_not_rejected_when_a_page_attributed_that_domain() -> None:
    """**6：跨域邮箱 + 页面上有归属证据。**

    「跨域」这个词只有在**已经知道它是哪一个域**的时候才成立，而那个知是判定那三格
    （``verified_official_domains`` + ``domain_verdict``）说的。所以这条邮箱活下来
    的机制是：**它自己的域就在那个集合里** —— 一页把它说成这个组织的官网，判定那
    一步于是把它收进集合（见本文件第 1 条：``mentioned_hosts`` 认得出页面正文里
    的地址）。

    这一格与第 8 条的区别不是「有没有岗位证据」（两边都有），而是**那个域是不是
    这个组织的**。判据里没有任何一处能只凭邮箱本身回答这个问题。
    """
    subject = _subject((SITE_HOST, "group-mx.example"), DomainVerdict.CONFIRMED)
    result = _bind(subject, "<p>Grounds Manager</p><p>grounds@group-mx.example</p>")

    assert [item.code for item in result.rejected] == []
    assert [route.email for route in result.routes] == ["grounds@group-mx.example"]


def test_a_cross_domain_email_with_no_evidence_that_the_domain_is_its_own_is_rejected() -> None:
    """**7：跨域邮箱 + 没有任何证据说那个域属于这个账号。**

    处置是 ``foreign_domain``：域名权威已经确认（这个账号的官网是 ``SITE_HOST``），
    而这个邮箱的域既不在那个集合里，也没有任何一页把它说成这个组织的 ——
    于是它落在 SOP L121 的那一句话里。

    同块里的岗位证据**救不了它**。这一条是有意的：归属与「这页上写着谁」是两件事，
    而把它们合成一件，正是审计里那条「同页出现 ⇒ 归到目标账号」。
    """
    subject = _subject((SITE_HOST,), DomainVerdict.CONFIRMED)
    result = _bind(subject, "<p>Grounds Manager</p><p>grounds@group-mx.example</p>")

    assert result.routes == []
    assert [item.code for item in result.rejected] == [RouteRejectionCode.FOREIGN_DOMAIN]

    # 而**证据不够**的那一侧（域名还没确认）又是另一种处置：「还没证明」而不是
    # 「证明了不是它」。两种处置分得开，是这一层最要紧的一件事。
    unknown = _bind(
        _subject((SITE_HOST,), DomainVerdict.UNKNOWN),
        "<p>Reach us at sales@group-mx.example</p>",
    )
    assert unknown.routes == []
    assert [item.code for item in unknown.rejected] == [RouteRejectionCode.UNBOUND_EMAIL]


def test_another_organisations_email_on_the_same_page_is_rejected() -> None:
    """**8：同页出现另一家组织的邮箱。** SOP L121 逐字：不得因同页出现而归属目标账户。

    这一条要求域名权威**是 confirmed**（见下一条判据）：域不同的拒信是一句关于
    **归属**的断言，而它要先知道归属是什么。
    """
    subject = _subject((SITE_HOST,), DomainVerdict.CONFIRMED)
    result = _bind(subject, "<p>Grounds Manager</p><p>grounds@other-org.example</p>")

    assert result.routes == []
    assert [item.code for item in result.rejected] == [RouteRejectionCode.FOREIGN_DOMAIN]


# === G1 的反例：把 unknown 当成「已确认的假」 ==================================


@pytest.mark.parametrize(
    "verdict, domains",
    [
        (DomainVerdict.UNKNOWN, (SITE_HOST,)),
        (DomainVerdict.UNKNOWN, ()),
        (DomainVerdict.ABSENT, ()),
    ],
)
def test_an_unconfirmed_domain_never_rejects_a_cross_domain_email(
    verdict: DomainVerdict, domains: tuple[str, ...]
) -> None:
    """**G1 的反例之一。** 域名权威没有确认时，跨域邮箱**不得**因域名被拒。

    三种拼法各说一件事，而三条都必须不拒：

    * ``unknown`` + 有域：那是**转述**（清单里的一格、别人写下来的）。拿它拒信，
      会把本账号官网上登着的邮箱拒成「外来的」—— 而清单那一格是会过期的。
    * ``unknown`` + 无域：从来没查过。
    * ``absent`` + 无域：查过、确实没有官网。**没有官网就没有任何一个域可以让
      邮箱「不属于它」** —— 这一格最容易被读成「所以任何域都不可信」，而那是
      凭空造出来的一条拒信。

    三种情况下邮箱仍要凭**同页的归属证据**活着：同块里的岗位证据在，它成立；
    不在，它是 hold。两条都在下面断言，因为「不拒」与「接受」不是同一句话。
    """
    subject = _subject(domains, verdict)

    with_evidence = _bind(subject, "<p>Grounds Manager</p><p>grounds@group-mx.example</p>")
    assert [item.code for item in with_evidence.rejected] == []
    assert [route.email for route in with_evidence.routes] == ["grounds@group-mx.example"]

    without = _bind(subject, "<p>Reach us at sales@group-mx.example</p>")
    assert [item.code for item in without.rejected] == [RouteRejectionCode.UNBOUND_EMAIL]


def test_the_legacy_projections_keep_the_domain_authority_unknown() -> None:
    """两条老路（清单记录、账号池）投影出来的一律是 ``unknown``。

    ``AccountRecord.domain`` 是清单里的一格 —— 转述。而 ``from_market_account``
    没有域名权威可给：账号池那一行说的是「有一个组织」，没说它的官网是哪一个。
    两者都留 ``unknown``，代价写在各自的 docstring 里：**跨域邮箱从此不会被这一层
    拒掉**，它按入口证据活或落成 hold。
    """
    record = AccountRecord(
        account_id="acct-riverside",
        organisation=ORGANISATION,
        cohort=Cohort.KNOWN_SUCCESS,
        domain=SITE_HOST,
    )
    subject = BindingSubject.from_account_record(record)

    # 清单那一格进得了**候选**（它是我们手上唯一一个域名），但进不了**权威**。
    assert subject.domains == (SITE_HOST,)
    assert subject.domain_verdict is DomainVerdict.UNKNOWN


# === 生产者本身的边界 ==========================================================


def test_an_unreadable_organisation_name_yields_unknown_not_absent() -> None:
    """组织名里读不出实词时是 ``unknown``，**不是** ``absent``。

    两者在回执上由 ``domain_reason`` 分开，而处置相反：``absent`` 会被人当成
    「所以它的官网不存在」，而这里发生的事只是「这一格没有可对照的标准」。
    """
    finding = attribute_official_domain("Ltd", (page(SITE_HOST, text="x"),), VOCAB)
    assert finding.verdict is DomainVerdict.UNKNOWN
    assert finding.rejected_hosts == ((SITE_HOST, "组织名里读不出任何实词"),)
    assert "unknown" in finding.reason


def test_a_host_without_any_evidence_cannot_be_confirmed() -> None:
    """一个没有出处的主机不叫确认 —— 即使它的名字读得出来。

    ``domain_verdict=confirmed`` 在模型上要求 ``domain_evidence_ids`` 非空。这一条
    把这个要求往上游推了一格：**不能确认**发生在生产者这里，而不是等到构造
    ``VenueAsset`` 时才炸 —— 那时「哪台主机、为什么」已经丢了。
    """
    finding = attribute_official_domain(
        ORGANISATION,
        (page(SITE_HOST, text=ORGANISATION, evidence=()),),
        VOCAB,
    )
    assert finding.verdict is DomainVerdict.UNKNOWN
    assert finding.rejected_hosts == (
        (SITE_HOST, "还没有任何一页证据支撑它 —— 一个没有出处的主机不叫确认"),
    )


def test_the_producer_never_invents_a_domain_from_a_lookalike_string() -> None:
    """正文里不猜主机。判据是「这一页自己写出来的地址」，不是「像地址的字符串」。

    三种写法（带 scheme 的 URL、``www.`` 开头的裸主机、邮箱的 ``@`` 后面）都认；
    看起来像主机名的散文一个都不认 —— 那种「猜」在报告上与一条真观察逐字相同。
    """
    assert mentioned_hosts("see. the. website. now") == ()
    assert mentioned_hosts(f"our site: {SITE}") == (SITE_HOST,)
    assert mentioned_hosts(f"our site: www.{SITE_HOST}") == (SITE_HOST,)
    assert mentioned_hosts(f"write to info@{SITE_HOST}") == (SITE_HOST,)


def test_the_first_label_rule_keeps_suffix_words_out() -> None:
    """三个界一起构成判据，缺一个都会放宽：``co``/``uk`` 这类后缀词不算。

    这一格在今天被 ``min_word_length`` 挡着，而那是一道会被一份新 profile 的词表
    悄悄挪开的界 —— 所以第 3 条（至少一个匹配词落在**第一个标签**里）要单独钉住：
    名字被写进去的是**这台主机自己的名字**，不是它的后缀。
    """
    tokens = organisation_tokens(ORGANISATION, VOCAB)
    assert tokens == ("riverside", "athletic")

    ok, why = host_is_explained_by(SITE_HOST, tokens, VOCAB)
    assert ok is True, why

    # 两个实词都在，但一个都不在第一个标签里（``example``）—— 不成立。
    ok, why = host_is_explained_by(f"example.{SITE_HOST}", tokens, VOCAB)
    assert ok is False
    assert "后缀" in why

    # 只读得出一个实词 —— 也不成立。``min_name_matches`` 是判据的一半。
    ok, why = host_is_explained_by("riverside.example.co.uk", tokens, VOCAB)
    assert ok is False
    assert "少于" in why


def test_a_page_observation_carries_what_it_read_and_what_it_can_cite() -> None:
    """``PageObservation`` 的形状是判据的一部分：**正文**与**证据**都要在。

    少了正文，判定只能在主机名上跑；少了证据，判定就没有出处。两者一起构成
    「我们真的读过这一页」这句话的全部内容。
    """
    empty = replace(page(SITE_HOST, text=ORGANISATION), evidence_ids=())
    finding = attribute_official_domain(ORGANISATION, (empty,), VOCAB)
    assert finding.verdict is DomainVerdict.UNKNOWN

"""``canonical_host`` —— 配对键的规范化规则，**逐条**钉住。

配对键存在的唯一理由是 ``C - B = Skill 净新增合格 Account`` 这个差值要成立：
两支臂的候选必须落到**同一个单位**上，否则「Skill 多找到了一个」与
「同一件事被两边各写了一遍」在报告上是同一行。

因此本文件对模块 docstring 里写下的每一条规则都给出**正反例**：scheme、
``www.``、路径/查询串/fragment、端口、大小写、IDN、裸主机名，以及
「拿不到 host 时失败关闭」。断言用的是**产出的值本身**，不是散文。

最后两条是**跨模块**的不变量：产出的键必须能真的存进
``AccountCandidate.pairing_key``（规范形式），且抓取层的字节精确同一性
**没有**被这里的宿主级粗化悄悄改掉 —— 两件事刻意不同，见
:func:`test_page_variants_are_one_pairing_key_and_six_fetches`。
"""

from __future__ import annotations

import pytest

from marketops.application.pairing import (
    canonical_host,
    keys_with_multiple_organisations,
    pairing_key_for,
)
from marketops.domain.candidate import PAIRING_KEY_MAX_LENGTH, AccountCandidate
from marketops.domain.util import check_canonical_id, sha256_text

HOST = "probe.example.org"


def candidate(**overrides) -> AccountCandidate:
    """一个最小可构造的候选；``official_domain`` / 证据按需覆盖。"""
    payload: dict = {
        "candidate_id": "probe-candidate-001",
        "organisation": "Example Sports Trust",
        "venue_name": "Example Recreation Ground",
        "source_method_id": "search.engine.v1",
    }
    payload.update(overrides)
    return AccountCandidate(**payload)


# --- scheme ------------------------------------------------------------------


def test_scheme_is_not_part_of_the_key() -> None:
    """http 与 https 是**同一个 host**：配对单位是主机，不是传输层。"""
    assert canonical_host("http://probe.example.org/contact") == HOST
    assert canonical_host("https://probe.example.org/contact") == HOST


def test_the_two_schemes_produce_the_same_key() -> None:
    assert canonical_host(f"http://{HOST}/a") == canonical_host(f"https://{HOST}/b")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://probe.example.org/x",
        "mailto:someone@probe.example.org",
        "javascript:void(0)",
        "tel:+441234567890",
        "file:///etc/hosts",
        "data:text/plain,hello",
    ],
)
def test_non_web_schemes_are_refused(url: str) -> None:
    """只有 http/https 被接受。

    别的 scheme 要么没有主机、要么指的不是一个网页归属：把 ``ftp://`` 折成
    同一个键，是在断言「两者是同一个所有者」，而这件事没有任何证据。
    """
    with pytest.raises(ValueError):
        canonical_host(url)


# --- www. --------------------------------------------------------------------


def test_one_leading_www_label_is_dropped() -> None:
    """``www.`` 是**别名标签**，不是主机身份的一部分。

    ``slugify`` 不会摘掉它（实测 ``www.example.co.uk`` 原样保留），所以这条
    必须显式写下来 —— 否则同一台主机的两种拼写会把配对流拆成互不相干的两组，
    而报告上只是「多了几行」。
    """
    assert canonical_host("https://www.probe.example.org/contact") == HOST
    assert canonical_host("https://WWW.Probe.Example.Org/") == HOST


def test_www_is_dropped_exactly_once() -> None:
    assert canonical_host("https://www.www.probe.example.org/") == "www.probe.example.org"


def test_www_is_dropped_only_in_the_leading_position() -> None:
    assert canonical_host("https://probe.www.example.org/") == "probe.www.example.org"
    assert canonical_host("https://probe.example.www/") == "probe.example.www"


def test_other_subdomain_labels_are_kept() -> None:
    """只认 ``www`` 这一个别名标签，其余子域一律保留。

    这是**刻意的保守**：``ww2`` / ``web`` / ``m`` 不是约定俗成的别名，
    而把子域折成注册域（eTLD+1）会**最大化**聚合域名的合并 —— 见模块
    docstring 里那条没被完全堵上的缝。
    """
    for kept in (
        "ww2.probe.example.org",
        "web.probe.example.org",
        "m.probe.example.org",
        "club-a.probe.example.org",
    ):
        assert canonical_host(f"https://{kept}/") == kept


def test_www_is_kept_when_dropping_it_would_leave_a_single_label() -> None:
    """``www.example`` 少一个标签也还是主机名，不去掉就没有第二个标签可留。"""
    assert canonical_host("https://www.example/") == "www.example"


# --- 路径 / 查询串 / fragment / 尾斜杠 ---------------------------------------


def test_trailing_slash_path_query_and_fragment_are_dropped() -> None:
    assert canonical_host(f"https://{HOST}/") == HOST
    assert canonical_host(f"https://{HOST}/contact/team/?page=2#form") == HOST
    assert canonical_host(f"https://{HOST}") == HOST


def test_page_variants_are_one_pairing_key_and_six_fetches() -> None:
    """**边界**：配对键是宿主级的，抓取层的同一性是字节精确的。这一段不做抓取去重。

    ``marketops/adapters/http.py`` 的 ``_persist`` 用 ``sha256_text(url)[:16]``
    给镜像命名，刻意**不**规范化。实测后果就是同一个页面的 6 个 ``?page=N``
    变体是 6 次抓取 / 6 个镜像文件，却只有**一个**配对身份。

    两件事都不能改：抓取层去重会改变「抓到几页」这个读数（而它是指标的一部分），
    而配对层不粗化则会让同一块场地的两条候选在报告里算成两条。所以这里钉住
    **两个数同时成立**，而不是让其中一个去迁就另一个。
    """
    variants = [f"https://{HOST}/clubhouse-4719.html?page={n}" for n in range(1, 7)]

    assert len({canonical_host(url) for url in variants}) == 1
    assert len({sha256_text(url)[:16] for url in variants}) == 6


# --- 端口 --------------------------------------------------------------------


def test_default_ports_are_dropped() -> None:
    assert canonical_host(f"https://{HOST}:443/x") == HOST
    assert canonical_host(f"http://{HOST}:80/x") == HOST


def test_non_default_ports_are_also_dropped() -> None:
    """非默认端口**同样**不进键 —— 代价是明写下来的，不是没想到。

    端口属于传输端点，不属于主机身份；而配对单位是主机。另一个方向
    （把 ``:8443`` 编码进键）会造出一个永远配不上裸主机拼写的键，
    于是**同一台主机被算成两条净新增** —— 那正是这个模块要避免的失真方向。
    代价：同一台主机上两个不同的服务会被并成一个单位，与聚合域名同源。
    """
    assert canonical_host(f"https://{HOST}:8443/contact") == HOST
    assert canonical_host(f"{HOST}:8443") == HOST


def test_a_port_never_survives_into_the_key() -> None:
    """键里不得出现 ``:`` —— ``check_slug_safe`` 会直接拒绝这种 id。

    这条不是洁癖：键要存进 ``AccountCandidate.pairing_key``，那里走的是
    ``check_canonical_id``；带冒号的键在构造期就报错，等于整个产出者不可用。
    """
    for url in (f"https://{HOST}:443/", f"https://{HOST}:8443/", f"{HOST}:8443"):
        assert ":" not in canonical_host(url)


def test_a_malformed_port_is_refused() -> None:
    with pytest.raises(ValueError):
        canonical_host(f"https://{HOST}:not-a-port/")


# --- 大小写 ------------------------------------------------------------------


def test_case_is_folded() -> None:
    """DNS 大小写不敏感，但**缩略**不会自动发生：``idna`` 对纯 ASCII 标签原样返回。"""
    assert canonical_host("HTTPS://PROBE.EXAMPLE.ORG/Contact") == HOST
    assert canonical_host("Probe.Example.Org") == HOST


def test_uppercase_and_lowercase_spellings_share_one_key() -> None:
    assert canonical_host("HTTPS://PROBE.Example.ORG/A") == canonical_host(f"http://{HOST}/b")


# --- IDN ---------------------------------------------------------------------


def test_idn_is_punycoded_so_both_spellings_agree() -> None:
    """Unicode 与 punycode 是同一台主机的两种写法 → 同一个键。"""
    assert canonical_host("https://münchen.example/x") == "xn--mnchen-3ya.example"
    assert canonical_host("https://XN--MNCHEN-3YA.example/x") == "xn--mnchen-3ya.example"
    assert canonical_host("https://münchen.example/x") == canonical_host(
        "https://xn--mnchen-3ya.example/y"
    )


def test_an_unencodable_idn_is_refused() -> None:
    with pytest.raises(ValueError):
        canonical_host("https://a..b/x")


# --- 主机名语法 --------------------------------------------------------------


def test_a_trailing_root_dot_is_the_same_host() -> None:
    assert canonical_host(f"https://{HOST}./contact") == HOST


def test_a_bare_host_is_accepted() -> None:
    """``official_domain`` 存的就是裸域名（``check_slug_safe`` 挡掉 scheme 与路径）。"""
    assert canonical_host(HOST) == HOST
    assert canonical_host("WWW.Probe.Example.Org") == HOST


@pytest.mark.parametrize(
    "value, why",
    [
        ("", "空串"),
        ("   ", "只有空白"),
        ("probe.example.org ", "首尾空白"),
        ("probe example.org", "内部空白"),
        ("/contact", "相对路径，没有主机"),
        ("?page=2", "查询串，没有主机"),
        ("#form", "fragment，没有主机"),
        ("probe.example.org/contact", "没有 scheme 却带路径 —— 无从判定这是不是一个 URL"),
        ("//probe.example.org/x", "协议相对 URL —— 同上，形状不确定"),
        ("https://", "scheme 之后没有主机"),
        ("https:///path", "空主机"),
        ("https://user:pw@probe.example.org/", "带 userinfo（可能是凭据，且不是主机身份）"),
        ("https://probe", "单标签主机 —— 不是公开的域名"),
        ("https://localhost/x", "单标签主机"),
        ("https://192.168.0.1/x", "IP 字面量 —— 末标签纯数字，不是域名"),
        ("https://[::1]/x", "IPv6 字面量"),
        ("https://probe_host.example/x", "标签含下划线，不是合法主机名"),
        ("https://-leading.example.org/x", "标签以连字符开头"),
        ("https://trailing-.example.org/x", "标签以连字符结尾"),
        ("https://a..b/x", "空标签"),
    ],
)
def test_inputs_without_a_usable_host_are_refused(value: str, why: str) -> None:
    """**失败关闭**：拿不到主机就抛，不返回一个「看起来像空域名」的值。

    返回空串会让「这个候选没有官网」与「它的官网这一栏写坏了」在数据上
    长得一样（``marketops/domain/candidate.py`` 已经为同一理由把这两条路分开）。
    所以这里抛错，由调用方决定这一行是否进 hold。
    """
    with pytest.raises(ValueError):
        canonical_host(value)


def test_labels_longer_than_the_dns_bound_are_refused() -> None:
    with pytest.raises(ValueError):
        canonical_host(f"https://{'x' * 64}.example.org/")


def test_the_key_bound_is_the_shared_constant_and_both_sides_are_pinned() -> None:
    """253 是完整域名的上限；界只定义在 ``candidate.py`` 一处，这里只消费它。"""
    at_bound = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])
    assert len(at_bound) == PAIRING_KEY_MAX_LENGTH
    assert canonical_host(f"https://{at_bound}/") == at_bound

    over_bound = at_bound + "a"
    assert len(over_bound) == PAIRING_KEY_MAX_LENGTH + 1
    with pytest.raises(ValueError):
        canonical_host(f"https://{over_bound}/")


# --- 同一性：同主机同键、异主机异键 ------------------------------------------


def test_every_spelling_of_one_host_shares_one_key() -> None:
    spellings = [
        "probe.example.org",
        "PROBE.EXAMPLE.ORG",
        "http://probe.example.org/",
        "https://probe.example.org/contact?x=1#y",
        "https://probe.example.org:443/",
        "https://www.probe.example.org/team",
        "https://probe.example.org./",
    ]
    assert {canonical_host(value) for value in spellings} == {HOST}


def test_different_hosts_do_not_share_a_key() -> None:
    hosts = [
        "probe.example.org",
        "other.example.org",
        "probe.example.com",
        "club.probe.example.org",
        "ww2.probe.example.org",
    ]
    keys = [canonical_host(f"https://{host}/") for host in hosts]
    assert len(set(keys)) == len(hosts)


def test_every_key_is_storable_in_the_candidate_contract() -> None:
    """**跨模块不变量**：产出者产出的键必须能真的存进 ``pairing_key``。

    规范形式的判据是 ``check_canonical_id``（与 ``candidate.py`` 同一个入口、
    同一个界）。这里同时走一遍真实的构造，因为「校验函数通过」与
    「模型接受」是两件事：字段的约束只有模型自己说了算。
    """
    urls = [
        "https://probe.example.org/contact",
        "https://WWW.Probe.Example.Org:8443/a?b=1#c",
        "https://münchen.example/x",
        HOST,
    ]
    for url in urls:
        key = canonical_host(url)
        assert check_canonical_id(key, "pairing_key", max_length=PAIRING_KEY_MAX_LENGTH) == key
        assert candidate(official_domain=key, pairing_key=key).pairing_key == key


# --- 产出者：证据驱动 --------------------------------------------------------


def test_a_domain_with_evidence_is_paired() -> None:
    row = candidate(official_domain=HOST, domain_evidence_ids=["ev-001"])
    assert pairing_key_for(row) == HOST


def test_a_candidate_without_a_domain_has_no_pairing_identity() -> None:
    """没有域名 ⇒ 空键 ⇒ 不参与配对（下游 hold），不是配到一个空串上。"""
    assert pairing_key_for(candidate()) == ""


def test_a_domain_without_domain_evidence_has_no_pairing_identity() -> None:
    """**证据驱动**：域名这一栏自己不算数，得有一条指着它的证据。

    一条没有出处的域名，与随手写的域名，在报告里长得一样。配上对之后两支臂
    会在一个没人能证明的单位上对齐 —— 而那个对齐会直接进 ``C - B``。
    """
    assert pairing_key_for(candidate(official_domain=HOST)) == ""
    assert pairing_key_for(candidate(official_domain=HOST, domain_evidence_ids=[])) == ""


def test_an_unusable_domain_is_loud_rather_than_silently_unpaired() -> None:
    """有域名但用不了 ⇒ 抛，不退化成空键。空键的含义是「没有域名」。"""
    with pytest.raises(ValueError):
        pairing_key_for(candidate(official_domain="probe", domain_evidence_ids=["ev-001"]))


# --- 聚合域名：把「看不出来」变成「看得出来」 --------------------------------


def test_two_organisations_on_one_host_are_reported() -> None:
    """一个 host 下两个责任实体 —— 这是**报告上看得出来**的那一半。

    这不是黑名单：判定完全由候选行自己算出来（同一个键、不同的
    ``organisation``），引擎里没有任何一个域名。
    """
    rows = [
        candidate(candidate_id="a-001", official_domain=HOST, pairing_key=HOST),
        candidate(
            candidate_id="a-002",
            organisation="Another Sports Club",
            official_domain=HOST,
            pairing_key=HOST,
        ),
    ]
    assert keys_with_multiple_organisations(rows) == {
        HOST: ("Another Sports Club", "Example Sports Trust")
    }


def test_one_organisation_on_one_host_is_not_reported() -> None:
    rows = [
        candidate(candidate_id="a-001", official_domain=HOST, pairing_key=HOST),
        candidate(candidate_id="a-002", official_domain=HOST, pairing_key=HOST),
    ]
    assert keys_with_multiple_organisations(rows) == {}


def test_rows_without_a_pairing_key_are_not_clustered() -> None:
    """空键不是「一个大家都属于的单位」—— 它是「不配对」。"""
    rows = [
        candidate(candidate_id="a-001"),
        candidate(candidate_id="a-002", organisation="Another Sports Club"),
    ]
    assert keys_with_multiple_organisations(rows) == {}


def test_the_cluster_uses_the_recorded_key_not_a_recomputation() -> None:
    """用的是**行上记着的**键（报告就是按它配对的），不是现场重算的。

    重算会与报告用的键分叉，于是「报告说配上了、检测说没有」——
    两个都自称是同一份配对结果，而差别看不出来。
    """
    rows = [
        candidate(candidate_id="a-001", official_domain="other.example.org", pairing_key=HOST),
        candidate(
            candidate_id="a-002",
            organisation="Another Sports Club",
            official_domain="third.example.org",
            pairing_key=HOST,
        ),
    ]
    assert set(keys_with_multiple_organisations(rows)) == {HOST}


def test_the_report_is_sorted_so_two_runs_agree() -> None:
    rows = [
        candidate(
            candidate_id="a-001",
            organisation="Zeta Trust",
            pairing_key=HOST,
            official_domain=HOST,
        ),
        candidate(
            candidate_id="a-002",
            organisation="Alpha Trust",
            pairing_key=HOST,
            official_domain=HOST,
        ),
        candidate(
            candidate_id="a-003",
            organisation="Mid Trust",
            pairing_key=HOST,
            official_domain=HOST,
        ),
    ]
    assert keys_with_multiple_organisations(rows) == {HOST: ("Alpha Trust", "Mid Trust", "Zeta Trust")}

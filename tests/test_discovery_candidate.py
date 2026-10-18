"""``AccountCandidate`` —— 发现期的**候选**账号契约。

这一层独立于 ``AccountRecord``，理由是硬的：``Cohort``（``marketops/domain/enums.py``）
是**闭集**（``known_success`` / ``accessible_no_core_email`` / ``previously_blocked``），
``contracts/account-record.schema.json`` 的 ``required`` 含 ``cohort``，
``marketops/application/cohorts.py`` 断言总数 == ``plan.total``，
``tests/test_cohorts.py`` 钉死 10/10/10。发现出来的账号**没有** cohort，
也没有诚实的值可填 —— 所以候选不是「字段不全的账号记录」，是另一个东西。

本文件守住五件事：

1. **三个 id 空间分开**：发现期身份（``candidate_id``）、配对身份
   （``pairing_key``，可为空）、账号身份（本层**不存在**）。
2. **没有 snippet 字段**：``application/pipeline.py::_scan_snippet`` 的计数器
   靠「SERP 里出现过邮箱、绑定被拒绝」证明那条不变量还在被考验；带 snippet
   的旁路会让它朝「一切正常」说谎。
3. **没有 cohort 字段**：见上。
4. ``source_method_id`` **必填且非空**：SOP L82 要求每一行都能回到发现方法。
5. **生产者的入口比契约严**：:func:`new_candidate` 收的是注册表成员本身，
   不是一个同名的字符串。只比 id 会把注册表降级成一张词表。

断言用的是**模型自身的事实**（``model_fields`` / 契约 JSON），不是散文。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marketops.domain.candidate import (
    PAIRING_KEY_MAX_LENGTH,
    AccountCandidate,
    new_candidate,
)
from marketops.domain.errors import ConfigError
from marketops.domain.sources import SOURCE_METHODS, SourceMethod
from marketops.domain.util import _UNSAFE_ID_CHARS, FILENAME_ID_MAX_LENGTH
from marketops.schema import CONTRACT_MODELS, CONTRACTS_DIR, build_contracts

CANONICAL_ID = "probe-candidate-001"
HOST = "probe.example.org"


def candidate(**overrides) -> AccountCandidate:
    """一个最小可构造的候选。``source_method_id`` 是必填的 —— 见 SOP L82。"""
    payload: dict = {
        "candidate_id": CANONICAL_ID,
        "organisation": "Example Sports Trust",
        "venue_name": "Example Recreation Ground",
        "source_method_id": "search.engine.v1",
    }
    payload.update(overrides)
    return AccountCandidate(**payload)


# --- 1. 发现期身份：candidate_id 必须是规范形式 ---------------------------------


def test_a_minimal_candidate_carries_the_chain_fields() -> None:
    """链路要求的字段族都在：组织、场馆资产、来源方法、证据指针。"""
    row = candidate()

    assert row.candidate_id == CANONICAL_ID
    assert row.organisation == "Example Sports Trust"
    assert row.venue_name == "Example Recreation Ground"
    assert row.source_method_id == "search.engine.v1"
    assert row.evidence_ids == []


def test_candidate_id_accepts_a_canonical_id() -> None:
    assert candidate(candidate_id="probe-candidate-001").candidate_id == CANONICAL_ID


@pytest.mark.parametrize(
    "bad_id, why",
    [
        ("https://probe.example.org/contact", "URL 直接派生 —— 含 : 与 /"),
        ("probe.example.org/contact", "带路径的 host"),
        ("Probe-Candidate-001", "大写会被 slugify 改写"),
        ("probe candidate 001", "空格会被 slugify 改写"),
        ("probe-candidate-001 ", "首尾空白"),
        ("", "空 id"),
    ],
)
def test_candidate_id_must_be_canonical(bad_id: str, why: str) -> None:
    """非规范 id 一律在构造期被拒。

    ``candidate_id`` 会经由 ``LocalEvidenceStore`` 变成 ``<id>.jsonl`` 的
    文件名片段，而 ``slugify`` 会把两个不同的 id 映射到同一个文件 ——
    store 不报错，两个候选的证据被静默合并。
    """
    with pytest.raises(ValidationError) as exc:
        candidate(candidate_id=bad_id)
    assert "candidate_id" in str(exc.value), why


def test_a_url_can_never_be_a_candidate_id() -> None:
    """从 URL 直接派生 candidate_id 会炸，而且不是巧合。"""
    with pytest.raises(ValidationError):
        candidate(candidate_id="https://probe.example.org/contact")

    # 原因在 util 的禁用字符集里 —— 这一条把「为什么炸」钉住，
    # 免得将来有人把它当成一个可以顺手放宽的校验。
    assert ":" in _UNSAFE_ID_CHARS
    assert "/" in _UNSAFE_ID_CHARS


def test_candidate_id_reuses_the_filename_id_max_length() -> None:
    """界必须复用 ``FILENAME_ID_MAX_LENGTH``，不能另写一个字面量。

    发现期证据经由同一个 store 落盘，将来 ``Evidence.account_id`` 就是它；
    两处各写一个界，漂移的那一次会让「校验通过的 id」在落盘时被截断。
    """
    longest = "a" * FILENAME_ID_MAX_LENGTH
    assert candidate(candidate_id=longest).candidate_id == longest

    with pytest.raises(ValidationError):
        candidate(candidate_id="a" * (FILENAME_ID_MAX_LENGTH + 1))


# --- 2. 配对身份：pairing_key 可为空，且与「非空但不规范」分得开 ----------------


def test_pairing_key_may_be_empty() -> None:
    """没有官网的候选没有配对身份 —— 它不参与配对，而不是配到一个空串上。"""
    assert candidate().pairing_key == ""
    assert candidate(pairing_key="").pairing_key == ""


def test_pairing_key_accepts_a_canonical_host() -> None:
    assert candidate(pairing_key=HOST).pairing_key == HOST


@pytest.mark.parametrize(
    "bad_key",
    [
        "Probe.Example.ORG",
        "https://probe.example.org",
        "probe.example.org/contact",
        "probe.example.org:443",
        " probe.example.org",
    ],
)
def test_pairing_key_refuses_a_non_canonical_host(bad_key: str) -> None:
    """非空就必须是规范 host。

    ``Probe.Example.ORG`` 与 ``probe.example.org`` 是同一台主机；
    放行前者会让同一台主机的两个拼写变成**两个配对组**，
    于是 paired 比较里两支臂被拆成互不相干的两半，而报告上只是「多了几行」。
    """
    with pytest.raises(ValidationError) as exc:
        candidate(pairing_key=bad_key)
    assert "pairing_key" in str(exc.value)


def test_empty_pairing_key_is_not_where_a_bad_key_lands() -> None:
    """空与非规范必须是两条不同的路。

    如果非规范值被悄悄改写成空串，「这个候选没有官网」与「这个候选的官网写错了」
    在数据上就长得一样 —— 前者是事实，后者是缺陷，而缺陷会被当成事实读下去。
    """
    empty = candidate(pairing_key="")
    assert empty.pairing_key == ""

    with pytest.raises(ValidationError):
        candidate(pairing_key="Probe.Example.ORG")

    # 两个可配的身份必须真的不同，否则「配对单位」不成立。
    assert candidate(pairing_key=HOST).pairing_key != empty.pairing_key


def test_pairing_key_has_its_own_bound() -> None:
    longest = "a" * PAIRING_KEY_MAX_LENGTH
    assert candidate(pairing_key=longest).pairing_key == longest

    with pytest.raises(ValidationError):
        candidate(pairing_key="a" * (PAIRING_KEY_MAX_LENGTH + 1))


# --- 3. 禁止的字段：snippet / cohort / account_id ------------------------------


def test_candidate_has_no_snippet_field() -> None:
    """snippet 字段不存在 —— 与 ``_scan_snippet`` 计数器是同一条不变量。

    候选行里带 snippet，等于允许一个绕过检索工具的邮箱从旁路进入发现层 ——
    ``_scan_snippet`` 的计数器于是开始说谎，方向恰好是「一切正常」。
    """
    assert "snippet" not in AccountCandidate.model_fields


def test_candidate_has_no_cohort_field() -> None:
    """``Cohort`` 是闭集，契约把 ``cohort`` 列进了 ``required`` —— 这里没有它。"""
    assert "cohort" not in AccountCandidate.model_fields


def test_candidate_has_no_account_id_field() -> None:
    """``account_id`` 只在晋级成 ``AccountRecord`` 时才存在。

    加一个本轮恒为 ``None`` 的槽位是臆造：它会读成「这个候选的账号 id 还没填」，
    而事实是「本轮不产生账号记录」。
    """
    assert "account_id" not in AccountCandidate.model_fields


def test_candidate_refuses_a_snippet_smuggled_in_as_an_extra_field() -> None:
    """未知字段一律报错，而不是被静默丢弃。"""
    with pytest.raises(ValidationError) as exc:
        candidate(snippet="Reach us at someone@probe.example.org")
    assert "snippet" in str(exc.value)


# --- 4. source_method_id 必填 --------------------------------------------------


def test_source_method_id_is_required() -> None:
    """缺了必须报错 —— 不是默认空串。

    SOP L82 逐字：「搜索结果、单页新闻或单一 contact 页只能作补充证据，
    不能当全量母池」。这一层的候选池按 SOP 属于补充证据；
    每行都必须带它，否则报告会被读成母池。它也是「来源族多样性」唯一算得出来的依据。
    """
    with pytest.raises(ValidationError) as exc:
        AccountCandidate(
            candidate_id=CANONICAL_ID,
            organisation="Example Sports Trust",
            venue_name="Example Recreation Ground",
        )
    assert "source_method_id" in str(exc.value)


@pytest.mark.parametrize("blank", ["", "   "])
def test_source_method_id_may_not_be_blank(blank: str) -> None:
    """必填字段不接受「填了个空」—— 一个空串在这里等于没有出处。"""
    with pytest.raises(ValidationError) as exc:
        candidate(source_method_id=blank)
    assert "source_method_id" in str(exc.value)


@pytest.mark.parametrize("field", ["organisation", "venue_name"])
def test_the_chain_ends_need_a_non_blank_subject(field: str) -> None:
    """账号的定义是「拥有 / 运营 / 维护 / 预约控制 / 采购场地的实体」。

    没有责任实体的行不是候选账号，没有场地的行不在这条链路上。
    """
    with pytest.raises(ValidationError) as exc:
        candidate(**{field: ""})
    assert field in str(exc.value)

    with pytest.raises(ValidationError):
        candidate(**{field: "   "})


# --- 5. 抓取统计与表面事实：未记录与「记录为零 / 确认为假」分得开 --------------


def test_page_counts_are_unrecorded_by_default() -> None:
    """默认是 ``None``（未记录），不是 0。

    0 与「没记」在报告里长得一样，而引擎把 ``None`` 读成「未确认 ⇒ 不决定子句」、
    把 0 读成「抓了 0 页」（一个**已确认**的值 ⇒ ``!any_page_fetched`` 判为真），
    与 rules 里那条 hold 的距离就只剩一个计数器的默认值。
    未确认不是「假」，所以它也不会被取反成真 —— 它只是让规则**没判**。
    """
    row = candidate()
    assert row.pages_fetched is None
    assert row.pages_blocked is None

    assert candidate(pages_fetched=0).pages_fetched == 0
    assert candidate(pages_fetched=7, pages_blocked=2).pages_blocked == 2


@pytest.mark.parametrize("field", ["pages_fetched", "pages_blocked"])
def test_page_counts_may_not_be_negative(field: str) -> None:
    with pytest.raises(ValidationError) as exc:
        candidate(**{field: -1})
    assert field in str(exc.value)


def test_surface_evidence_confirmed_is_three_valued() -> None:
    """三值：未查（``None``）/ 查到了（``True``）/ 查过，没有（``False``）。

    资格判定里那条人造表面的规则前提是
    ``account.evidence.natural_grass.verified == false`` —— 它**不能**写成取反：
    ``!surface_evidence_confirmed`` 在未确认时也为真，会把「还没查」读成
    「确认没有」。契约必须能把这三件事分开。
    """
    assert candidate().surface_evidence_confirmed is None

    confirmed_true = candidate(surface_evidence_confirmed=True)
    confirmed_false = candidate(surface_evidence_confirmed=False)

    assert confirmed_true.surface_evidence_confirmed is not confirmed_false.surface_evidence_confirmed
    assert confirmed_false.surface_evidence_confirmed is not None


def test_official_domain_may_be_empty_and_must_be_a_bare_host() -> None:
    """官方域名可为空（还没找到官网），但不得是一个 URL。"""
    assert candidate().official_domain == ""

    row = candidate(official_domain=HOST, domain_evidence_ids=["ev-domain-1"])
    assert row.official_domain == HOST
    assert row.domain_evidence_ids == ["ev-domain-1"]

    with pytest.raises(ValidationError) as exc:
        candidate(official_domain="https://probe.example.org/contact-us")
    assert "official_domain" in str(exc.value)


def test_the_candidate_carries_evidence_pointers_not_text() -> None:
    """证据是指针 + 哈希，不是正文 —— 这里只有 id。"""
    row = candidate(evidence_ids=["ev-1", "ev-2"], surface_evidence_ids=["ev-3"])
    assert row.evidence_ids == ["ev-1", "ev-2"]
    assert row.surface_evidence_ids == ["ev-3"]


# --- 6. 生产者入口：来源是一个成员，不是一个字符串 -----------------------------


def test_new_candidate_derives_the_id_from_the_registered_member() -> None:
    """入口收注册表成员本身，id 由它派生 —— 调用方不需要把 id 抄一遍。"""
    member = SOURCE_METHODS["search.engine.v1"]
    row = new_candidate(
        member,
        candidate_id=CANONICAL_ID,
        organisation="Example Sports Trust",
        venue_name="Example Recreation Ground",
    )
    assert row.source_method_id == member.id


def test_new_candidate_refuses_a_bare_string() -> None:
    """一段文本不是来源 —— 类型层面就写不出来。"""
    with pytest.raises(ConfigError):
        new_candidate(
            "search.engine.v1",  # type: ignore[arg-type]
            candidate_id=CANONICAL_ID,
            organisation="Example Sports Trust",
            venue_name="Example Recreation Ground",
        )


def test_new_candidate_refuses_an_unregistered_id() -> None:
    with pytest.raises(ConfigError):
        new_candidate(
            SourceMethod(id="search.other.v1", family="public_search", answers="x"),
            candidate_id=CANONICAL_ID,
            organisation="Example Sports Trust",
            venue_name="Example Recreation Ground",
        )


def test_new_candidate_refuses_a_lookalike_member() -> None:
    """同 id 但不是注册表里那个对象 —— 只比 id 会把注册表降级成一张词表。

    这条判据的意义是：加一个来源必须改 ``sources.py``，
    而那是一次进了版本控制的决定。
    """
    registered = SOURCE_METHODS["search.engine.v1"]
    lookalike = SourceMethod(
        id=registered.id, family=registered.family, answers=registered.answers
    )
    assert lookalike == registered  # 值相等 —— 判据不能是相等
    assert lookalike is not registered  # 身份不等 —— 判据是身份

    with pytest.raises(ConfigError):
        new_candidate(
            lookalike,
            candidate_id=CANONICAL_ID,
            organisation="Example Sports Trust",
            venue_name="Example Recreation Ground",
        )


def test_new_candidate_refuses_a_second_source_method_id() -> None:
    """id 由入口派生，再传一次是个说不清的问题（不一致时谁赢）。"""
    with pytest.raises(ConfigError):
        new_candidate(
            SOURCE_METHODS["search.engine.v1"],
            candidate_id=CANONICAL_ID,
            organisation="Example Sports Trust",
            venue_name="Example Recreation Ground",
            source_method_id="crawl.site.v1",
        )


# --- 7. 契约层面：下游只看得到 JSON Schema -------------------------------------


def test_the_candidate_contract_is_exported() -> None:
    assert CONTRACT_MODELS["account-candidate"] is AccountCandidate
    assert (CONTRACTS_DIR / "account-candidate.schema.json").is_file()


def test_the_contract_keeps_the_three_id_spaces_apart() -> None:
    schema = build_contracts()["account-candidate"]
    properties = schema["properties"]

    assert "candidate_id" in properties
    assert "pairing_key" in properties
    # 账号身份本层不存在；cohort 是 AccountRecord 的东西，候选物化不了。
    assert "account_id" not in properties
    assert "cohort" not in properties
    assert "snippet" not in properties


def test_the_contract_makes_the_chain_fields_mandatory() -> None:
    required = set(build_contracts()["account-candidate"]["required"])

    assert {"candidate_id", "organisation", "venue_name", "source_method_id"} == required

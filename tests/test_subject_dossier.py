"""受理那一刻冻住的**主体身份**，以及「这份身份够不够开工」。

## 这里守的是 2026-09-23 那次真实运行

worker 拿到 ``kent-003``，花掉 9 次 Serper 查询去搜这个内部 slug，十条命中全是
无关的同名物；它拿不到 Example School One —— 因为这个仓库当时**把身份丢掉了**：
``known_accounts_subjects`` 读出一整行，只留下 ``account_id``。

所以这组判据分两半：

1. **身份在不在**（:class:`~marketops.domain.subject.SubjectDossier` 装了什么、
   回执里给出去的是什么）；
2. **不够开工时有没有在花钱之前拒绝**（:func:`~marketops.domain.subject.identity_refusal`）。

第 2 条是这一轮全部价值所在：判据说「不够」而 worker 照样发出查询，与没有这条
判据不可区分。所以「零检索、零模型调用」这条要求有两个执行器 —— 这里的
:func:`identity_refusal`（它只读字段，没有别的出路），以及
``tests/test_job_inputs.py`` 里那条「整份拒绝 ⇒ 一个 work item 都不编译」。
"""

from __future__ import annotations

import pytest

from marketops.domain.enums import DomainVerdict
from marketops.domain.subject import (
    SubjectDossier,
    dossier_from_payload,
    identity_refusal,
)


def _dossier(**overrides: object) -> SubjectDossier:
    """一份**完整**的身份，各判据只覆盖自己关心的那一格。

    刻意逐格给全而不是 ``SubjectDossier(**{"account_id": ..., **overrides})`` 加
    一堆缺省：缺省值在这里是判据的一部分（``organisation`` 的缺省是空串，而空串
    正是要拒绝的那一种），所以让每一条判据把它关心的那几格写出来。
    """
    fields: dict[str, object] = {
        "account_id": "kent-003",
        "organisation": "Example School One",
        "domain": "",
        "country": None,
        "source_ref": "artifacts/marketops-runtime/inputs/kent-2026-09-21.yaml",
        "input_sha256": "a" * 64,
    }
    fields.update(overrides)
    return SubjectDossier(**fields)  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# 一、判据：一份身份够不够开工
# ----------------------------------------------------------------------
def test_a_bare_slug_is_not_an_identity() -> None:
    """**这一条就是那次运行的形状**：清单里没有名字，只有内部 id。

    判据必须说「不够」—— 而它说不够的后果被上面那句「零检索、零模型调用」兜着，
    所以这里只断这件事：返回的是一条说得出的理由，不是空串。
    """
    refusal = identity_refusal(_dossier(organisation=""))

    assert refusal, "只有 account_id 的身份被判成够了 —— 那一次 9 次查询就是这么花掉的"
    assert "kent-003" in refusal


def test_the_internal_id_written_into_the_name_field_is_still_a_bare_slug() -> None:
    """名字那一格写的是 id 本身 ⇒ 还是只有 slug。

    这一格不是构造出来的：清单的 ``organisation`` 那一格填内部 id 是**已经发生过**
    的形态（``kent-003`` 就是一个既有 id 形状又占着名字位置的例子）。放宽这一条
    等于「只要有字就算名字」—— 而判据存在的全部理由就是挡住这种包。
    """
    assert identity_refusal(_dossier(organisation="kent-003"))
    # 大小写与首尾空白是同一个内部 id 的三种写法，不是三个名字。
    assert identity_refusal(_dossier(organisation="KENT-003"))
    assert identity_refusal(_dossier(organisation="  kent-003  "))


def test_a_name_without_a_domain_is_enough() -> None:
    """``domain`` 不知道**不必**拒绝 —— 先搜名字、再确认官网是一条完整的路径。

    这条判据的方向很要紧：把「没有域名」也拒掉，会让过去那两轮真正跑通的实验
    （从已知账号 + 名字开始）变成不可受理 —— 而它们跑通了，说明这条路存在。
    """
    assert identity_refusal(_dossier(organisation="Example School One", domain="")) == ""


def test_a_domain_without_a_name_is_enough() -> None:
    """反过来也够了：有官网就直接能开始确认。

    与上一条一起，说明判据的**或**是真的或 —— 一条只认名字（或只认域名）的实现
    会在这两条之一上红。
    """
    assert identity_refusal(_dossier(organisation="", domain="client30.example.org")) == ""


def test_the_refusal_says_where_to_fix_it() -> None:
    """拒绝要指向**那份清单** —— 否则读的人知道不够，不知道该去哪儿补。

    这是本仓对拒绝的一贯要求（同 ``ConfigError`` 里「看的是哪个目录」那条）：
    一句「输入不足」在报告里与一次崩溃的差别只有语气。
    """
    refusal = identity_refusal(_dossier(organisation=""))

    assert "kent-2026-09-21.yaml" in refusal
    assert "domain 可以没有" in refusal or "域名可以没有" in refusal


def test_the_judge_does_not_decide_whether_a_name_is_real() -> None:
    """**判据的边界，而且它是可执行的**：判断的不是名字真假、够不够独特。

    「Example School Two」通过 —— 它与「Example School One」在这条判据下没有区别。
    这不是疏忽：要判断够不够独特就得有关于这个市场的知识，而 ``marketops/`` 里
    不许有那种东西（``tests/test_no_hardcoded_market_knowledge.py``）。消歧是
    worker 的活，它拿到的材料正是为那件事准备的。

    写下这一条是为了让边界**红得出来**：谁要是哪天给这个函数加一条「名字太常见
    就拒绝」，他会先在这里看到自己在推一条本仓判死的线。
    """
    assert identity_refusal(_dossier(organisation="Example School Two")) == ""


# ----------------------------------------------------------------------
# 二、装着的是什么：线索、以及刻意不装的东西
# ----------------------------------------------------------------------
def test_the_dossier_carries_exactly_the_identity_fields() -> None:
    """字段集是**判定**，所以它是一条判据，不是一份文档。

    ``cohort`` / ``status`` / 先前等级是**上一次工作的结论**，``notes`` 是自由文本
    且任务包会被模型逐字读。两者都刻意不在里面（理由见 ``domain/subject.py``）。
    把其中任何一个加回来的人会先在这里红 —— 而那时他要面对的问题是「凭什么让历史
    标签决定这一次怎么做」，不是一个要去改的断言。

    ``domains`` / ``domain_verdict`` 是 2026-09-26 加进来的那一对，理由见
    ``tests/test_bind.py::test_a_roster_domain_is_a_quotation_and_does_not_reject_on_its_own``：
    没有它们，绑定那一层就只剩「清单里那一格」可当域名权威，而那一格是**转述**。
    它们是身份，不是历史标签 —— 所以进得来。
    """
    assert set(_dossier().payload()) == {
        "account_id",
        "organisation",
        "domain",
        "domains",
        "domain_verdict",
        "country",
        "source_ref",
        "input_sha256",
    }


def test_the_payload_round_trips() -> None:
    """写下去的身份与读回来的是**同一个** —— 快照那条链的地基。"""
    before = _dossier(country="GB", domain="client30.example.org")

    assert dossier_from_payload(before.payload()) == before


def test_a_payload_missing_a_field_is_refused_not_defaulted() -> None:
    """缺一格就拒绝，不补默认值。**这一条挡的是一次静默放行。**

    ``payload.get(name, "")`` 的兜底会把 ``null`` 变成字符串 ``"None"`` —— 一个
    名叫 ``None`` 的组织。它非空、也不等于 ``account_id``，于是
    :func:`identity_refusal` 会放行它，worker 拿着这个名字去检索。所以这里要求
    每一格都在、每一格都是字符串。
    """
    payload = _dossier().payload()
    del payload["organisation"]

    with pytest.raises(ValueError) as refused:
        dossier_from_payload(payload)

    assert "organisation" in str(refused.value)


@pytest.mark.parametrize("value", [None, 3, ["Example Town Two"]], ids=repr)
def test_a_non_string_field_is_refused_not_coerced(value: object) -> None:
    """``str()`` 一个 ``None`` 会得到 ``"None"``，于是缺席变成内容。

    三格各是一种真实的来路：``null``（别的东西写的 JSON）、数字、数组。
    """
    payload = _dossier().payload()
    payload["organisation"] = value

    with pytest.raises(ValueError):
        dossier_from_payload(payload)


def test_country_is_the_one_field_that_may_be_absent() -> None:
    """``country`` 的 ``null`` 是「输入没说」，不是「没有这个国家」。

    它与别处的缺省曾经是一个结论（``AccountRecord.country`` 的缺省一度是 ``"UK"``，
    那是一次没人做过的判定），所以这里保持可空，并且**往返之后还是 null**。
    """
    assert dossier_from_payload(_dossier(country=None).payload()).country is None
    assert dossier_from_payload(_dossier(country="GB").payload()).country == "GB"


def test_domains_and_the_verdict_are_absent_on_a_payload_written_before_them() -> None:
    """**旧快照读得回来。** 2026-09-26 之前写下的 ``work_item_snapshot`` 没有那两格。

    它们与 ``country`` 同一类：缺席是「这一份没写」，而不是一个结论。方向也是
    失败关闭的那一边 —— ``()`` 与 ``unknown`` 的含义正是「不做判定」，于是绑定
    那一层不会因为读不到就拒掉一个跨域邮箱。

    与 ``_TEXT_FIELDS`` 上那几条**相反**的处置（那几条缺一个就整份拒绝）不是
    松紧不一致，而是同一个问题有两个答案：**缺席的默认值是不是一个结论。**
    ``organisation`` 缺了默认成空串，而空串是一句「我不知道它是谁」的结论 ——
    拒。这两格缺了默认成「不判定」，而那就是它们本来就该有的值。
    """
    payload = _dossier().payload()
    del payload["domains"]
    del payload["domain_verdict"]

    restored = dossier_from_payload(payload)
    assert restored.domains == ()
    assert restored.domain_verdict is DomainVerdict.UNKNOWN
    # 而别的东西一个都没被这两格的缺席带走。
    assert restored.organisation == "Example School One"


def test_a_written_verdict_is_never_silently_fallen_back_to_unknown() -> None:
    """写坏了的判定值整份拒绝 —— **不退化成 unknown**。

    这两件事在报告里长得一样（都是「没判定」），而处置相反：一个是「还没查」，
    另一个是「有人写错了一个值」。退回 unknown 会把后者渲染成前者，而这一格的
    全部价值就是「域名权威只有 confirmed 一种写法」。
    """
    payload = _dossier().payload()
    payload["domain_verdict"] = "probably"
    with pytest.raises(ValueError):
        dossier_from_payload(payload)

    payload = _dossier().payload()
    payload["domains"] = "not-a-list"
    with pytest.raises(ValueError):
        dossier_from_payload(payload)


# ----------------------------------------------------------------------
# 三、给 worker 看的那一份
# ----------------------------------------------------------------------
def test_empty_clues_are_absent_not_empty_strings() -> None:
    """缺席与空串在**动作**上是相反的。

    ``"domain": ""`` 读起来是「确认过没有官网」—— 那让人放弃这条线；而事实是
    「输入没给」，那让人去找。所以缺的线索不进回执。

    同时断 ``account_id`` 一定在：它是 worker 回传结论时的那个 id，缺席会让一份
    结算挂不上任何东西。
    """
    clues = _dossier(domain="", country=None).as_clues()

    assert clues == {
        "account_id": "kent-003",
        "organisation": "Example School One",
    }
    assert "domain" not in clues and "country" not in clues


def test_the_clues_carry_every_line_the_input_actually_gave() -> None:
    """正对照：输入给了什么，回执里就有什么。

    没有这一条，「只回 account_id」的实现（也就是这次要修的那个形状）会在上面
    那条上**全绿** —— 上面断的是「空的没进来」，它一个字都没说非空的要进来。
    """
    clues = _dossier(domain="client30.example.org", country="GB").as_clues()

    assert clues["organisation"] == "Example School One"
    assert clues["domain"] == "client30.example.org"
    assert clues["country"] == "GB"

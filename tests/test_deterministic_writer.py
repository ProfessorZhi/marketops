"""确定性写作者（``adapters/deterministic_writer.py``）的判据。

## 这个文件为什么存在

``marketops/ports/outreach.py`` 把 ``DraftWriter`` 的三条约束写成了散文：不得编造、
不得改写收件人与权限、不得交回一个既没有正文也没有理由的结果。散文不会失败 ——
而这三条各自对应一次真实事故（一封编了预算的信寄到了客户手里、一封被顺手改了
收件人的信、一条读作「跑过了」的空稿）。所以每条都要有一个会因此变红的测试。

本文件因此**不测存储、不测 sqlite、不碰 ``application/``**：这个实现是一个纯函数，
它的每一条判据都可以由一份手写的 :class:`~marketops.ports.outreach.DraftRequest`
直接触发。用 ``tests/test_outreach_contract.py`` 里的 ``FixedWriter`` 那种替身测不到
这里的任何一条 —— 那些替身不做拼装，所以它们对「拼出来的东西是什么」没有意见。

## 另有一组判据问的不是信，而是**这个写作者是不是它自称的那一个**

``WRITER_ID`` 与 ``generation_key`` 算在一起，所以它错了的后果不在信上、在**回执**上：
换了一个实现而 id 不动，重跑一遍得到的稿子被读成 ``unchanged``，而报告上一切正常。
这一组因此问三件事：那一格是不是**算出来**的（现场重算对得上）、改本模块的任何一处
是不是都会换一个 id、以及同一个 revision 在 LF 与 CRLF 两种检出下是不是同一个 id。

## 这一层守不住的那一半，也写在这里

数字那条判据（``test_the_body_has_no_number_that_the_request_did_not_give``）只覆盖
「编造」里含数字的那一类。一个编出来的地名、人名或产品能力可以一个数字都没有 ——
它靠的是「话术只从 ``ApprovedClaim.text`` 逐字进来」这条结构约束，不是一条判据。
把它写成一条测试会让人以为判据比实际更宽。
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from marketops.adapters.deterministic_writer import (
    _SKELETONS,
    WRITER_ID,
    DeterministicDraftWriter,
    writer_content_digest,
)
from marketops.domain.outreach import (
    ApprovedClaim,
    OutreachStage,
    TemplateFamily,
    WritingPolicy,
)
from marketops.ports.outreach import DraftRequest

REPO_ROOT = Path(__file__).resolve().parents[1]
WRITER_PATH = REPO_ROOT / "marketops" / "adapters" / "deterministic_writer.py"

APPROVED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _family(**overrides: object) -> TemplateFamily:
    fields: dict[str, object] = {
        "family_id": "fam-1",
        "revision": 1,
        "stage": OutreachStage.STAGE_1,
        "role_class": "buyer",
        "locale": "en-GB",
        "product": "prod-1",
        "writing_policy_id": "pol-1",
    }
    fields.update(overrides)
    return TemplateFamily(**fields)  # type: ignore[arg-type]


def _policy(**overrides: object) -> WritingPolicy:
    fields: dict[str, object] = {"policy_id": "pol-1", "revision": 1}
    fields.update(overrides)
    return WritingPolicy(**fields)  # type: ignore[arg-type]


def _claim(claim_id: str = "claim-1", text: str = "we cover the whole county.") -> ApprovedClaim:
    """``text`` 刻意**以小写字母开头**：它是逐字的输入，不得被「顺一下大小写」。"""
    return ApprovedClaim(
        claim_id=claim_id,
        revision=1,
        text=text,
        source_ref="ev-claim-1",
        approved_by="ops",
        approved_at=APPROVED_AT,
    )


def _request(**overrides: object) -> DraftRequest:
    fields: dict[str, object] = {
        "account_id": "acct-1",
        "organisation": "Example Coatings Ltd",
        "family": _family(),
        "policy": _policy(),
        "stage": OutreachStage.STAGE_1,
        "locale": "en-GB",
        "claims": (_claim(),),
        "facts": (("club_size", "1200 members", "ev-2"),),
    }
    fields.update(overrides)
    return DraftRequest(**fields)  # type: ignore[arg-type]


def _writer(**overrides: object) -> DeterministicDraftWriter:
    return DeterministicDraftWriter(**overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 确定性
# --------------------------------------------------------------------------


def test_the_same_request_twice_gives_the_same_bytes() -> None:
    """守 §5 的 ``generation_key``：同一份输入两次调用必须逐字节相同。

    它守的是一个**不报错**的失败：正文换了而 key 没变，调用方会把这一稿读成
    ``unchanged`` 而丢掉，于是换出去的还是旧那一封 —— 报告上一切正常。
    """
    writer = _writer()
    request = _request()
    first = writer.draft(request)
    second = writer.draft(request)
    assert first == second
    assert first.body.strip(), "一稿正常的信不该是空的 —— 否则这条判据是空转的"


def test_permuting_the_inputs_does_not_change_the_letter() -> None:
    """``generation_key`` 是按**排序后**的输入算的，正文也必须如此。

    ``DraftRequest.generation_key`` 的 docstring 逐字写着排序的理由：同一组事实换个
    顺序不是一次输入变化。若正文跟着传参顺序变，那么同一份输入会有两个 key 相同、
    内容不同的结果 —— 「这份输入变了没有」这个问题就没有答案了。

    所以这里同时断两件事：key 相同**且**正文逐字相同。
    """
    writer = _writer()
    forward = _request(
        claims=(_claim("claim-1", "we cover the whole county."), _claim("claim-2", "we answer within a day.")),
        facts=(("club_size", "1200 members", "ev-2"), ("country", "a country", "ev-3")),
    )
    backward = _request(
        claims=(_claim("claim-2", "we answer within a day."), _claim("claim-1", "we cover the whole county.")),
        facts=(("country", "a country", "ev-3"), ("club_size", "1200 members", "ev-2")),
    )
    assert forward.generation_key(
        writer_id=writer.writer_id, policy_version=writer.policy_version
    ) == backward.generation_key(writer_id=writer.writer_id, policy_version=writer.policy_version)
    assert writer.draft(forward) == writer.draft(backward)


def test_the_writer_id_names_the_implementation_and_the_policy_version_comes_from_outside() -> None:
    """守端口 docstring 的两条：``writer_id`` 是实现名 + 内容指纹，``policy_version`` 不是。

    ``policy_version`` 是策略那一侧的东西 —— 写作者拿不到它。写死一个字面量的后果是
    策略改了而 ``generation_key`` 不变：重跑的那一稿会被读成 ``unchanged`` 而不落库。
    缺省必须是空串（「我不知道」），而不是一个像版本号的值。
    """
    assert _writer().writer_id == WRITER_ID
    assert _writer().policy_version == ""
    writer = _writer(policy_version="rev-7")
    assert writer.policy_version == "rev-7"
    assert writer.draft(_request()).writer_id == writer.writer_id


# --------------------------------------------------------------------------
# 写作者的身份是**算出来的**，不是维护出来的
# --------------------------------------------------------------------------


def test_the_version_half_of_the_writer_id_is_the_digest_of_this_file() -> None:
    """``WRITER_ID`` 的指纹那一半**就是**本模块源码的 sha256 —— 现场重算并与它比。

    守的是上一轮验收报告里的那条缺口：「``WRITER_ID`` 没有任何东西固定它的内容」
    （``docs/PRODUCT_LOOP_ACCEPTANCE_2026-09-24.md`` §H 第 9 条）。修法不是「记得加一」，
    而是让那一格由内容算出来 —— 下面这三件事各挡一种回归：

    1. **手写回一个改不动的版本号。** 那样第一句就红：盘上那一份的摘要与常量对不上。
    2. **实现名被悄悄改掉。** 名字那一半是回执上给读的人看的那一格，换它等于换一个
       写作者的名字，历史回执里那一格会指向另一个实现 —— 要么别换，要么换的时候
       知道自己在换。
    3. **这个进程加载的代码不是盘上这一份。** 这条不显眼但真实：常量是 import 那一刻
       读到的文本算的，而这里重读一次盘上的文件。同尺寸的改动骗得过 ``.pyc`` 的校验
       （记忆 `pyc 缓存会骗过同尺寸的「还原」`），那种情况下两者对不上 —— 而它对
       ``generation_key`` 就是一次静默的「换了一个实现而 key 不动」。

    摘要**不**由 ``writer_content_digest`` 再算一遍，而是用 ``hashlib`` 直接散这个文件的
    字节：一个判据重复被测代码的算法，两边同时写错就一起绿了。这也正是 docstring 里
    那句「第二个人拿一台 shell 独立算一遍」的可执行形式 —— 本仓的检出是 LF，
    所以 ``sha256sum marketops/adapters/deterministic_writer.py`` 的前 12 个字符
    与下面这一句逐字相同。
    """
    raw = WRITER_PATH.read_bytes()
    assert b"\r" not in raw, (
        "这个文件里有 CR —— 那样 ``sha256sum`` 与这里的算法就对不上，"
        "而「第二个人独立算一遍」这句话会变成一句空话"
    )
    name, separator, digest = WRITER_ID.partition("+")
    assert separator == "+", f"WRITER_ID 应当是实现名 + 内容指纹，现在这个形状是 {WRITER_ID!r}"
    assert name == "deterministic-composer-v1"
    assert digest == hashlib.sha256(raw).hexdigest()[:12]
    assert digest == writer_content_digest(raw.decode("utf-8"))
    assert len(digest) == 12


def test_the_digest_ignores_the_line_endings_of_the_checkout() -> None:
    """一份 CRLF 检出与一份 LF 检出是**同一份代码**，不该得到两个写作者。

    ``git`` 的 ``core.autocrlf`` 会把同一棵树在两种工作区里写成两种字节。指纹散的是
    源码文本，所以归一那一步就是这一格的全部依赖 —— 去掉它，同一个 revision 在
    两种检出来源上会算出两个 id，而回执上读起来是「有人换了实现」。
    """
    source = WRITER_PATH.read_text(encoding="utf-8")
    assert "\n" in source, "对照组：源码里有换行，否则这条判据是空转的"
    assert writer_content_digest(source) == writer_content_digest(source.replace("\n", "\r\n"))
    assert writer_content_digest(source) != writer_content_digest(source + "\n"), (
        "归一不许顺手把内容也归一掉 —— 加了内容而指纹不动，这一格就白算了"
    )


@pytest.mark.parametrize(
    ("before", "after", "what"),
    [
        ("a first note", "an opening note", "en-GB 骨架里的一句措辞"),
        ("Thanks for your time.", "Thanks so much for your time.", "en-US 骨架里的落款"),
        (
            "sentences.append(skeletons.closer)",
            "sentences.insert(0, skeletons.closer)",
            "拼装那一处的顺序（落款那一句）",
        ),
        (
            "if len(body) <= policy.max_body_chars:",
            "if len(body) < policy.max_body_chars:",
            "一条判据（长度上限的边界）",
        ),
        (
            '_RESERVED_TOKENS: tuple[str, ...] = ("organisation", "stage_phrase", "call_to_action")',
            '_RESERVED_TOKENS: tuple[str, ...] = ("organisation", "stage_phrase")',
            "保留槽位表",
        ),
        (
            r'_TOKEN_RE = re.compile(r"\{\{([^{}]*)\}\}")',
            r'_TOKEN_RE = re.compile(r"\{\{([^{}]+)\}\}")',
            "占位符那个正则",
        ),
    ],
)
def test_a_changed_anything_in_this_module_moves_the_writer_id(
    before: str, after: str, what: str
) -> None:
    """改本模块的**任何**一处都会换一个 id —— 每一类各给一行。

    上面那半句的完整成立靠的是**指纹怎么算**（散整个文件的文本），不是靠这份清单把
    所有可能的改动枚举完 —— 枚举不完。这一组要挡的是另一种修法：只散骨架表，
    于是改一条判据（比如长度上限那个边界）、改一个正则、改拼装那一处的分隔符时
    id 一动不动，而正文可能因此从「写得出来」变成「超长 hold」。那正是
    ``generation_key`` 存在的理由被反过来用：**换一个实现而 key 相同**。

    每一行都先断言 ``before`` 真的在源码里 —— 一段找不到的旧文本会让
    ``replace`` 什么都不做，于是这条判据在「源码早就改过」之后会一直绿。
    """
    source = WRITER_PATH.read_text(encoding="utf-8")
    assert before in source, f"清单过期了：源码里找不到 {before!r}（{what}）"
    mutated = source.replace(before, after, 1)
    assert mutated != source, f"{what}：替换没有改变源码，这一行是空转的"
    assert writer_content_digest(mutated) != writer_content_digest(source), (
        f"只改了{what}，而 WRITER_ID 的指纹没动 —— 那一版的实现换了名字却没换 id"
    )


def test_attachments_are_empty_because_none_were_given() -> None:
    """写作者没有被给定任何附件 —— 凭空添一个附件是这一层不该有的自由。

    附件是清单与授权的一部分（``ManifestEntry`` / ``OutreachManifest``），不是写出来的。
    """
    assert _writer().draft(_request()).attachments == ()


# --------------------------------------------------------------------------
# 边界：它够不着收件人、抑制与发送状态
# --------------------------------------------------------------------------


def test_the_writer_has_no_second_entry_point_to_a_store_transport_or_manifest() -> None:
    """§5：写作者不得修改收件人、发件人、权限、抑制或发送状态。

    前半条（不许改）的执行器是**类型本身**：``DraftRequest`` 里没有这些字段 ——
    「有但不许改」与「没有」是两件事，这一层选的是后者。后半条（**不许从别处取**）
    靠的是入口只有一处：``draft`` 只收一个 ``request``，而实例上除了那个策略版本
    之外什么都不带 —— 没有存储、没有运输、没有清单可查。于是「顺手核一下抑制名单」
    在这个实现里没有入口可走。

    这两格都不是散文：签名多一个参数、或者实例上多一个字段，这条就红。
    """
    assert tuple(inspect.signature(DeterministicDraftWriter.draft).parameters) == (
        "self",
        "request",
    ), "写作者只许有一个入口：这一份请求"
    assert set(vars(_writer())) == {"_policy_version"}, (
        "实例上除了策略版本不许带别的东西 —— 一个缓存/句柄字段就是「从别处取」的入口"
    )


# --------------------------------------------------------------------------
# 两条 hold 路径
# --------------------------------------------------------------------------


def test_a_request_with_no_claims_and_no_facts_is_held() -> None:
    """§5：「个性化缺失时使用已批准的通稿措辞或**停下**，而不是猜」。

    两样输入都没有、策略又没有通稿措辞那一档时，写作者必须停下来，而不是交出
    一封语法正确、一个具体内容都没有的信 —— 那种信在报告上与一封个性化的信一样。
    """
    proposal = _writer().draft(_request(claims=(), facts=()))
    assert proposal.held_reason, "什么都缺的时候必须 hold"
    assert "已批准的说法" in proposal.held_reason
    assert "事实" in proposal.held_reason
    assert "generic_wording_id" in proposal.held_reason


def test_a_generic_wording_id_is_a_licence_to_write_without_personalisation() -> None:
    """§5 的另一半：策略给了通稿措辞那一档时，**不** hold。

    而它落下来的是一封只用骨架的通用信 —— 不是那段通稿措辞本身（本层拿不到它的
    正文，只有一个 id），所以正文里**不得**出现那个 id：那是一个内部标识，
    写进信里就是把内部标识当话术寄出去。
    """
    proposal = _writer().draft(
        _request(claims=(), facts=(), policy=_policy(generic_wording_id="gen-wording-1"))
    )
    assert proposal.held_reason == ""
    assert proposal.body.strip()
    assert "gen-wording-1" not in proposal.body
    assert proposal.facts_used == ()
    assert proposal.claim_ids == ()


def test_a_locale_without_skeletons_is_held_and_not_written_in_english() -> None:
    """选不出语言的 locale 必须 hold —— **不得拿英语凑数**。

    骨架表是按语言选的。用另一种语言拼出来的一封信，收件人收到的是一封他没有读过
    那句话的信，而库里那一行说「locale: fr-FR」—— 报告上完全正常。
    """
    proposal = _writer().draft(_request(locale="fr-FR"))
    assert "fr-FR" in proposal.held_reason
    assert proposal.body == "", "语言选不出来时不交回正文"


def test_a_policy_locale_that_contradicts_the_request_is_held() -> None:
    """策略要求的语言与请求给的语言不一致时，不挑一边 —— 停下来。

    挑任何一边都是一个我们没有被授权做的选择（用哪边的语言写，取决于谁在读），
    而两边的收件人不同。
    """
    proposal = _writer().draft(
        _request(locale="en-US", policy=_policy(required_locale="en-GB"))
    )
    assert "en-GB" in proposal.held_reason and "en-US" in proposal.held_reason
    assert proposal.body == ""


def test_holds_never_carry_a_body_or_a_citation() -> None:
    """本实现从不交回一份被 hold 的正文（见模块 docstring 的取舍）。

    一份带正文的 ``HELD`` 稿子会落进库里，而库里的正文是可以被复制的 ——
    ``hold`` 这个标记在**行**上，不在**字**里。
    """
    requests = [
        _request(claims=(), facts=()),
        _request(locale="fr-FR"),
        _request(policy=_policy(max_body_chars=1)),
        _request(policy=_policy(required_placeholders=("nothing_fills_this",))),
    ]
    for request in requests:
        proposal = _writer().draft(request)
        assert proposal.held_reason, request
        assert (proposal.body, proposal.subject, proposal.facts_used, proposal.claim_ids) == (
            "",
            "",
            (),
            (),
        ), proposal.held_reason


@pytest.mark.parametrize(
    "draft_request",
    [
        _request(claims=(), facts=()),
        _request(organisation=""),
        _request(locale="de-DE"),
        _request(claims=(_claim("claim-1", "a claim\nwith a newline in it."),)),
        _request(facts=(("club_size", "{{x}}", "ev-2"),)),
        _request(policy=_policy(max_body_chars=1)),
    ],
    ids=["no-input", "empty-organisation", "unknown-locale", "newline-in-claim", "token-in-value", "tiny-limit"],
)
def test_every_result_is_either_a_body_or_a_reason(draft_request: DraftRequest) -> None:
    """端口 docstring 第三条：「不得返回一个没有正文也没有理由的结果」。

    写不出信不抛异常（抛 = 「这次没跑成」，而这些都是**跑成了**的结论）。这条测试
    把「抛了」也算作红：``draft`` 一抛，这条就 error —— 那就是它变红的样子。
    每一份结果里，正文与理由**恰好有一个**：两个都空是一个坏了的实现（下游会记
    ``failed`` 并去修写作者），两个都有则是本实现刻意不用的一半（见模块 docstring）。
    """
    proposal = _writer().draft(draft_request)
    has_body = bool(proposal.body.strip())
    has_reason = bool(proposal.held_reason)
    assert has_body != has_reason, (
        f"正文={proposal.body!r}，理由={proposal.held_reason!r} —— "
        "两者必须恰好有一个"
    )


# --------------------------------------------------------------------------
# 引用：逐对抄自请求
# --------------------------------------------------------------------------


def test_facts_used_is_copied_pair_by_pair_from_the_request() -> None:
    """``facts_used`` 的形状是 ``(事实名, 证据 id)``，**逐对**来自请求。

    这一格是「待核实的引用」：下游拿它去 ``EvidenceStore`` 查。所以：
    * 报出来的每一对都必须真的在请求里（编一对 = §5 的「不得编造」，而症状是
      下游查不到 —— 或者更糟，查得到别的一行）；
    * 用进正文的那一条的**值**必须真的在正文里，否则「我用了这条事实」是一句
      没法核对的话（``tests/test_outreach_contract.py`` 里那个替身曾经把事实的
      **值**填进证据 id 那一格，那个 bug 正是这条判据要挡的）。
    """
    request = _request(
        facts=(("club_size", "1200 members", "ev-2"), ("country", "a country", "ev-3"))
    )
    proposal = _writer().draft(request)
    given = {(name, evidence_id) for name, _value, evidence_id in request.facts}
    assert set(proposal.facts_used) == given
    assert all(len(pair) == 2 for pair in proposal.facts_used)
    for _name, value, _evidence_id in request.facts:
        assert value in proposal.body, value


def test_claim_ids_are_a_subset_of_the_request_and_each_text_is_verbatim() -> None:
    """``claim_ids`` 是请求里那些 id 的子集，而且报出来的每一条都**逐字**出现在正文里。

    逐字这一半是承重的：改写过的句子不再是那条被批准的说法，而库里记的仍然是同一个
    ``claim_id`` —— 一次「意思差不多」的改写正好是 §5 要禁的那种事。
    说法以小写字母开头（见 :func:`_claim`），所以「顺手把首字母大写」也会让这条红。
    """
    request = _request(
        claims=(_claim("claim-1", "we cover the whole county."), _claim("claim-2", "we answer within a day."))
    )
    proposal = _writer().draft(request)
    assert set(proposal.claim_ids) == {claim.claim_id for claim in request.claims}
    for claim in request.claims:
        assert claim.text in proposal.body, claim.claim_id
    assert "We cover the whole county" not in proposal.body, "说法不得被顺成句首大写"


# --------------------------------------------------------------------------
# 策略：逐条都要有一个执行器
# --------------------------------------------------------------------------


def test_the_two_locales_produce_two_different_letters() -> None:
    """locale 真的在选骨架 —— 不是同一套话换一个标签。"""
    british = _writer().draft(_request(locale="en-GB"))
    american = _writer().draft(_request(locale="en-US"))
    assert british.body != american.body
    assert british.subject != american.subject
    for proposal in (british, american):
        assert "Example Coatings Ltd" in proposal.body
        assert "Example Coatings Ltd" in proposal.subject


def test_the_stage_selects_the_opening_sentence() -> None:
    """正文是「骨架 × 阶段」拼的：换一个阶段换的必须是正文，不是只换个字段。

    §6 要求第二段与第一段不同 —— 若两段拼出逐字相同的一封信，收件人收到的是同一封
    信的第二遍，而库里两行都记着「已发出」。
    """
    request = _request()
    first = _writer().draft(request)
    second = _writer().draft(_request(stage=OutreachStage.STAGE_2))
    assert first.body != second.body
    assert _SKELETONS["en-GB"].stage_phrases[OutreachStage.STAGE_2] in second.body
    assert _SKELETONS["en-GB"].stage_phrases[OutreachStage.STAGE_1] not in second.body


def test_the_call_to_action_is_present_exactly_when_the_policy_requires_it() -> None:
    """``require_call_to_action`` 的两侧都要有执行器。

    一封没有 CTA 的商业信在报告里与一封正常的信长得一样，而它多半不会有人回 ——
    所以「必须有」那一侧要有一条明确的句子承担它；而 ``False`` 时那句**不得**出现，
    否则这个字段是个摆设。
    """
    cue = _SKELETONS["en-GB"].call_to_action
    required = _writer().draft(_request(policy=_policy(require_call_to_action=True)))
    assert cue in required.body
    not_required = _writer().draft(_request(policy=_policy(require_call_to_action=False)))
    assert cue not in not_required.body


def test_a_required_placeholder_the_body_does_not_carry_is_held() -> None:
    """``required_placeholders``：正文里没有它 → hold，而且**点名**是哪一个。

    这里用的是一个「算得出来但正文里没有」的槽位（``call_to_action`` 配上
    ``require_call_to_action=False``）。这正是这条判据与「能不能填出来」分开的地方：
    一个要求了行动号召、又把行动号召关掉的策略是一份矛盾的策略，处置是让人去看策略，
    不是替它挑一边。
    """
    policy = _policy(
        required_placeholders=("{{call_to_action}}",), require_call_to_action=False
    )
    proposal = _writer().draft(_request(policy=policy))
    assert "call_to_action" in proposal.held_reason
    assert proposal.body == ""

    same_but_required = _writer().draft(
        _request(policy=_policy(required_placeholders=("call_to_action",)))
    )
    assert same_but_required.held_reason == ""
    assert _SKELETONS["en-GB"].call_to_action in same_but_required.body


def test_a_required_placeholder_naming_a_fact_is_carried_and_cited() -> None:
    """要求占位符时必须同时把它**用进正文**与**报进引用** —— 两件是同一件。"""
    proposal = _writer().draft(_request(policy=_policy(required_placeholders=("club_size",))))
    assert proposal.held_reason == ""
    assert "1200 members" in proposal.body
    assert proposal.facts_used == (("club_size", "ev-2"),)


def test_a_required_placeholder_that_no_input_can_fill_is_held() -> None:
    """补不出来的占位符 → hold。判词里报的是策略里的**原写法**（``{{x}}`` 与 ``x``
    是同一种写法的两半，只认一种会让判据变成关于拼写的）。"""
    policy = _policy(required_placeholders=("{{venue_contact}}", "venue_contact"))
    proposal = _writer().draft(_request(policy=policy))
    assert "venue_contact" in proposal.held_reason
    assert "{{venue_contact}}" in proposal.held_reason
    assert proposal.body == ""


def test_a_body_over_the_limit_is_held_rather_than_truncated() -> None:
    """``max_body_chars``：拼不下就 hold，**不许静默截断**。

    截断会把一句话砍成两半，而砍掉的那一半可能正好是那个「不」字 —— 发出去的正文与
    批准过的正文不再是同一份，而摘要记的是改之前那一个。
    """
    full = _writer().draft(_request())
    limit = len(full.body) - 1
    proposal = _writer().draft(_request(policy=_policy(max_body_chars=limit)))
    assert proposal.held_reason, "超过上限必须 hold"
    assert str(limit) in proposal.held_reason and str(len(full.body)) in proposal.held_reason
    assert proposal.body == "", "不得交回一段被截断的正文"


def test_no_link_appears_when_the_policy_allows_none() -> None:
    """``allowed_link_prefixes`` 空集合 = **一个链接都不许**（失败关闭）。

    正面：一份正常的输入拼出来的正文与主题里一个链接都没有 —— 骨架里没有 URL，
    值也只有调用方给的那几个。
    """
    proposal = _writer().draft(_request(facts=(("country", "a country", "ev-3"),)))
    assert proposal.held_reason == ""
    for text in (proposal.body, proposal.subject):
        assert "//" not in text and "www." not in text and "http" not in text.lower()


def test_a_link_that_is_not_on_the_allow_list_is_held() -> None:
    """反面：链接从**输入**里进来（一条说法/一条事实的值里带着 URL）时，

    一条空的许可清单必须让它停下来 —— 否则「一个链接都不许」这句话只在没链接的时候
    成立，而那正是它不需要成立的场合。判词里点名是哪一条链接，因为处置是去改那条输入。
    """
    link = "https://elsewhere.example/pricing"
    policy = _policy(allowed_link_prefixes=())
    proposal = _writer().draft(
        _request(policy=policy, claims=(_claim("claim-1", f"our prices are at {link}"),))
    )
    assert link in proposal.held_reason
    assert proposal.body == ""


def test_a_link_whose_prefix_is_allowed_passes_and_another_link_does_not() -> None:
    """许可清单的另一侧：在清单上的前缀放行，不在的照旧停下。

    只做前一半的判据等于「什么都拒」，而那会让这条策略变成一个没人能用的字段。
    """
    allowed = _policy(allowed_link_prefixes=("https://ok.example/",))
    good = _writer().draft(
        _request(policy=allowed, claims=(_claim("claim-1", "see https://ok.example/pricing"),))
    )
    assert good.held_reason == ""
    assert "https://ok.example/pricing" in good.body

    bad = _writer().draft(
        _request(policy=allowed, claims=(_claim("claim-1", "see https://no.example/pricing"),))
    )
    assert "https://no.example/pricing" in bad.held_reason
    assert bad.body == ""


def test_an_unfilled_variable_in_a_value_is_held_not_sent() -> None:
    """§5：「校验……未填充变量」。值里又出现占位符时，替换**不递归**（见 ``_render``）。

    递归替换会把一条事实的值变成一处入口：值里写 ``{{organisation}}`` 就能把别处的
    输入搬进这一句，而 ``facts_used`` 记的仍然是那条事实的证据 id。不递归的代价是那个
    变量原样留在正文里 —— 所以必须有这一条判据，否则寄出去的是我们内部的变量名。
    """
    proposal = _writer().draft(
        _request(facts=(("club_size", "{{organisation}}", "ev-2"),))
    )
    assert "organisation" in proposal.held_reason
    assert proposal.body == ""


def test_a_body_that_cannot_be_encoded_as_utf8_is_held() -> None:
    """§5：「校验 UTF-8」。唯一编不出来的是**孤立的代理码位**。

    它落在正文里的那一天坏的是发信那一步（SMTP 会话中途断掉，而那一封的账记在
    「投递未知」上），所以它要在这里停下来。
    """
    proposal = _writer().draft(_request(organisation="Example \ud800 Ltd"))
    assert "UTF-8" in proposal.held_reason
    assert proposal.body == ""


def test_a_subject_that_cannot_be_built_is_a_hold_not_a_broken_draft() -> None:
    """主题里带换行 → hold。

    换行不是排版问题：一封主题里带换行的信在拼报文那一步会变成**两个头**，而第二个头
    是这里写的 —— 那是头部注入。让它在构造草稿那一步才炸，症状是「这一条 failed」，
    而真正该做的事是去看请求里那个组织名。
    """
    proposal = _writer().draft(_request(organisation="Example\nCoatings Ltd"))
    assert "主题" in proposal.held_reason
    assert proposal.body == "" and proposal.subject == ""


def test_a_subject_over_the_length_limit_is_held_not_truncated() -> None:
    """主题超过 ``MAX_SUBJECT_CHARS`` → hold。截断那一半也要有人执行。

    ``OutreachDraft`` 对主题的这条上限是硬的。写作者这一侧是唯一能在**构造草稿之前**
    就说出「这一稿不该发」的地方；让它在构造那一步才炸，症状是这一条 ``failed``，
    而那说的是写作者坏了 —— 真正该改的是请求里那个组织名。

    输入是一段 500 字符的组织名（超过上限本身，而不是刚好压线）。
    """
    proposal = _writer().draft(_request(organisation="X" * 500))
    assert "主题" in proposal.held_reason
    assert "400" in proposal.held_reason, "判词里要报出上限是多少"
    assert proposal.body == "" and proposal.subject == ""


# --------------------------------------------------------------------------
# 不得编造：结构判据
# --------------------------------------------------------------------------


def test_the_body_has_no_number_that_the_request_did_not_give() -> None:
    """§5 的「不得编造」在数字这一半上的执行器。

    正文里出现的每一段数字都必须能在请求给的字符串（组织名、事实的值、说法的原文）
    里找到 —— 骨架、阶段措辞与其余句子一律不含数字。所以往骨架里写一个年份、
    一个预算或一个台数，这条就红。

    **它只覆盖含数字的那一类编造**：一个编出来的地名、人名或产品能力里可以一个数字
    都没有。见本文件模块 docstring 末尾。
    """
    request = _request(
        facts=(("club_size", "1200 members", "ev-2"),),
        claims=(_claim("claim-1", "we answer within 2 days."),),
    )
    proposal = _writer().draft(request)
    given = " ".join(
        [request.organisation, *(value for _n, value, _e in request.facts)]
        + [claim.text for claim in request.claims]
    )
    numbers = re.findall(r"\d+", proposal.body)
    assert numbers, "这份输入给了数字，正文里应当有 —— 否则这条判据是空转的"
    for number in numbers:
        assert number in given, f"正文里的 {number!r} 不在请求给的任何字符串里"


def test_no_literal_in_the_writer_is_a_whole_letter() -> None:
    """§5 逐字禁止「预生成几百份静态模板」—— 判据是：本模块里**没有一封信**。

    一封信是若干段落拼起来的，而段落之间那个空行只出现在调用的那一刻
    （``_compose`` 里把句子拼成正文的那一行）。所以任何**有内容**的字符串字面量里
    出现换行，就说明有人把一整封信写进了常量 —— 而那一天 ``draft`` 就退化成了一个
    查表函数。

    纯空白的字面量（段落之间的那个分隔符）是过滤掉的那一类：它不是一句话，
    把段落拼起来这件事本来就该在代码里看得见。

    注释与 docstring 豁免（它们是说明），字面量不豁免。
    """
    tree = ast.parse(WRITER_PATH.read_text(encoding="utf-8"), filename=str(WRITER_PATH))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))

    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
        and node.value.strip()  # 纯空白的字面量是段落分隔符，不是一句话
    ]
    assert len(literals) >= 10, (
        f"只扫到 {len(literals)} 个字面量 —— 扫描可能已经失效，那时「没有一封信」会变成假绿"
    )
    offenders = [text for text in literals if "\n" in text]
    assert not offenders, (
        f"这些字面量里含换行，也就是把一封信写进了常量：{offenders!r}。"
        "正文必须在调用时由骨架拼出来。"
    )


def test_every_locale_covers_every_stage() -> None:
    """骨架表的覆盖测试：每个语言都要有每一个阶段的措辞，且没有一句是空的。

    它守着 ``_compose`` 里那个「骨架表里没有这个阶段」的兜底分支**今天不可达**——
    将来多一个阶段时，症状会是一句话的结论，而不是一个 ``KeyError``。
    """
    assert set(_SKELETONS) >= {"en-GB", "en-US"}, "至少要有两种语言，否则「按 locale 选表」是空话"
    for locale, skeletons in _SKELETONS.items():
        assert set(skeletons.stage_phrases) == set(OutreachStage), locale
        for sentence in (
            skeletons.subject,
            skeletons.opening,
            skeletons.fact_line,
            skeletons.closer,
            skeletons.call_to_action,
            *skeletons.stage_phrases.values(),
        ):
            assert sentence.strip(), f"{locale} 里有一句是空的"
            assert "\n" not in sentence, f"{locale} 的一句骨架里含换行"


def test_a_duplicate_fact_name_is_held_not_guessed() -> None:
    """两条事实同名时，正文里那个值该引用哪一条证据**没有答案** —— 不得猜。

    取第一条是猜，而猜错的那天报告上完全正常：下游拿一个**查得到**的证据 id 去核实，
    查到的那句话不是正文里的那一句。
    """
    proposal = _writer().draft(
        _request(facts=(("club_size", "1200 members", "ev-2"), ("club_size", "900 members", "ev-9")))
    )
    assert "club_size" in proposal.held_reason
    assert proposal.body == ""


def test_a_fact_name_that_collides_with_a_reserved_slot_is_held() -> None:
    """一条与骨架槽位同名的事实会让那个槽位有两个值 —— 同样没有答案。"""
    proposal = _writer().draft(
        _request(facts=(("organisation", "Example Coatings Ltd", "ev-7"),))
    )
    assert "organisation" in proposal.held_reason
    assert proposal.body == ""


def test_a_fact_name_with_a_brace_is_held_because_it_can_never_be_a_placeholder() -> None:
    """第三条同类的路：事实名里有花括号。

    花括号是占位符的语法，而 ``_TOKEN_RE`` 匹配的名字里**不许有**花括号 —— 于是
    ``club{size`` 这个名字永远填不进正文，而它照样得进 ``facts_used``：那一格会说
    「我用了这条事实」，而正文里一个字都没有它。这不是一个可以由写作者猜的缺口
    （去掉花括号猜一个名字、或者丢掉这条事实，两条路都是改输入），所以是 hold。
    """
    proposal = _writer().draft(
        _request(facts=(("club{size}", "1200 members", "ev-2"),))
    )
    assert "club{size}" in proposal.held_reason
    assert proposal.facts_used == () and proposal.body == ""

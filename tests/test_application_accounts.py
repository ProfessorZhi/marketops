"""``merge_accounts``：账号池那一行的折叠规则，**逐条**。

这一份测的是规则本身，不是存储有没有照它办（那是 ``tests/test_account_store_contract.py``）。
两份的边界与另外两张表逐字相同：这里全是纯函数，没有库、没有配置、没有时钟。

## 这一份与另外两张表的测试差在哪

资产池那一份有一整段叫「冲突」，关系表那一份有一整段叫「四段键逐段测」。这一份没有冲突
那一段（账号行上**根本没有判定那一格**，见 ``application/accounts.py`` 的模块 docstring），
也没有键那一段（键就是 ``account_id`` 一个字符串，与资产池同形）。它多出来的是**三格
「空」的方言**，而这三格恰好是这一行上最容易被静默吃掉的东西：

* ``country``：``None`` 是「还没查」、``""`` 是「查了，是没有」、非空是「查到了什么」；
* ``status``：``discovered`` 是「还没有判定」，不是七种判定里的一种；
* ``organisation``：它是这一行的**主语**，所以它的方向与其余每一格都相反。

三条各有一段，且各有用例走**两个方向**（该覆盖的覆盖了、不该覆盖的没被覆盖）——
只测一个方向的判据挡不住「干脆全都覆盖」或「干脆全都不覆盖」这两条改法。

## 下半：生产者（``remember_accounts``）

下面那一段测的是「一行账号交上去之后落到哪里去了」，用的是内存替身、接的是**真规则**
（``merge_accounts``）—— 换成人造规则（存储契约测试里那条留记号的 ``probe_merge``），
这一半就不再是生产的形状，而它要判的正是生产路径上每一行的去向。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from marketops.adapters.memory import InMemoryAccountPoolStore, InMemoryEvidenceStore
from marketops.application.accounts import (
    AccountDisposition,
    AccountOutcome,
    merge_accounts,
    remember_accounts,
)
from marketops.domain.account import MarketAccount
from marketops.domain.enums import AccountStatus
from marketops.domain.errors import StoreError
from marketops.ports.pool import AccountPoolStore
from tests.test_evidence_store_contract import make_evidence

UTC = timezone.utc
T0 = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
EARLIER = T0 - timedelta(days=30)
LATER = T0 + timedelta(days=30)
WORKSPACE = "uk-acme-natural-grass"
OTHER_WORKSPACE = "uk-something-else"


def account(**overrides) -> MarketAccount:
    payload = {
        "workspace": WORKSPACE,
        "account_id": "acct-1",
        "organisation": "Example Parish Council",
        "evidence_ids": ["ev-1"],
        "first_seen_at": T0,
    }
    payload.update(overrides)
    return MarketAccount(**payload)


# --- 第一行 -------------------------------------------------------------------


def test_folding_into_nothing_is_the_row_itself() -> None:
    """池子里没有这一行时，折进去的就是它本身 —— 规则不该在第一次写入时改造它。"""
    incoming = account()
    assert merge_accounts(None, incoming) == incoming


def test_a_new_row_enters_without_a_verdict() -> None:
    """一行新账号带着 ``discovered`` 进池子 —— 那是「还没有判定」，不是一次判定。

    这条看着像在描述缺省值，其实是在钉**这一族里最容易滑过去的一格**：新进池子的行
    如果带着一个真状态（比如 ``eligible``），池子就会在没有任何人判过的第一天报出一个
    准入结论，而报告上完全看不出来它从哪儿来。
    """
    assert merge_accounts(None, account()).status is AccountStatus.DISCOVERED
    assert merge_accounts(None, account()).last_verified_at is None


# --- 键对不上 -----------------------------------------------------------------


@pytest.mark.parametrize("field,value", [("account_id", "acct-2"), ("workspace", OTHER_WORKSPACE)])
def test_a_key_that_does_not_match_is_refused(field: str, value: str) -> None:
    """折进来的对象与库里那一行必须说的是同一个账号、同一个工作区。

    照折的话，一次写错的 id 会把**另一个账号**的行整个改写掉，而报告上它看起来只是
    那个账号的信息变了 —— 这是本层最坏的一种失败。
    """
    with pytest.raises(StoreError):
        merge_accounts(account(), account(**{field: value}))


def test_the_refusal_says_which_two_accounts_they_were() -> None:
    """报错要说得出**是哪两个**账号 —— 否则调用点只知道「键不对」。"""
    with pytest.raises(StoreError) as caught:
        merge_accounts(account(), account(account_id="acct-2"))
    message = str(caught.value)
    assert "'acct-1'" in message
    assert "'acct-2'" in message
    assert f"'{OTHER_WORKSPACE}'" in message or f"'{WORKSPACE}'" in message


# --- 两个时刻：各走各的方向 ------------------------------------------------------


def test_first_seen_only_moves_earlier() -> None:
    """重放一次旧研究不该把「我们何时第一次记下它」推后。

    推后之后，池子会声称一个早就认识的账号是刚发现的 —— 而那一格是「这个账号进池子
    多久了」唯一的依据。
    """
    assert merge_accounts(account(), account(first_seen_at=EARLIER)).first_seen_at == EARLIER
    assert merge_accounts(account(), account(first_seen_at=LATER)).first_seen_at == T0


def test_last_verified_only_moves_later_and_none_does_not_participate() -> None:
    """``None`` 说的是「从那以后没再看过」，所以它既不被覆盖、也不覆盖别人。

    两个方向都走一遍：来行没看过（``None``）时已有的那个时刻要留着；来行看过时新的
    那个要接上。只测一个方向挡不住「``None`` 直接覆盖」与「永远不更新」这两条改法，
    而它们在下游是同一件事的两面 —— 复核清单上「从没复核过」与「刚复核过」会读成
    同一个答案。
    """
    verified = account(last_verified_at=T0)
    assert merge_accounts(verified, account()).last_verified_at == T0
    assert merge_accounts(account(), verified).last_verified_at == T0
    assert merge_accounts(verified, account(last_verified_at=LATER)).last_verified_at == LATER
    assert merge_accounts(account(), account()).last_verified_at is None


# --- 四个清单格：并集、保序、只增不减 --------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["aliases", "domains", "addresses", "evidence_ids"],
)
def test_the_list_cells_are_unioned_in_order_and_never_subtract(field: str) -> None:
    """第二次独立看到同一件事是**加强**，不是重复；而**没提到**不是「没有了」。

    一次后来的研究没提到某个域名、某个别名、某条证据，不是「它不再是那样」—— 本层
    表达不出「一条曾经成立的事实不再成立」（要有那件事自己的凭据）。所以是并集。

    保序也是判据的一部分：顺序是「先看到的在前」，人读证据链时就是这个顺序。
    """
    existing = account(**{field: ["a", "b"]})
    incoming = account(**{field: ["b", "c"]})
    assert getattr(merge_accounts(existing, incoming), field) == ["a", "b", "c"]


def test_every_list_cell_the_model_has_is_unioned_rather_than_overwritten() -> None:
    """模型上**每一个**清单格都必须被合起来 —— 而这一条**自己去找**那几格。

    ``MarketAccount`` 上加一个清单格、合并规则却不认它，那一格会永远停在第一次写入时
    的样子（合并结果取的是已有的那一行），而报告上那只是「它一直没变」。手抄一份格子
    清单在这里没有用：抄下来的那份清单**不会**因为模型长了新格子而变红，它对「漏了一
    格」一个字都没说。所以这里按**类型**从模型上取（``list[str]`` 且空表是缺省），
    加一格就自动多问一句。
    """
    cells = [
        name
        for name, field in MarketAccount.model_fields.items()
        if field.annotation == list[str] and field.default_factory is not None
    ]
    assert cells, "这一条的前提是这一行上确实有清单格 —— 一格都没有时它什么都没测"
    for field in cells:
        merged = merge_accounts(account(**{field: ["a", "b"]}), account(**{field: ["b", "c"]}))
        assert getattr(merged, field) == ["a", "b", "c"], f"{field} 没有被并起来"


# --- ``country`` 的三态 --------------------------------------------------------


def test_a_missing_country_does_not_erase_a_known_one() -> None:
    """``None`` = **还没查**，所以它不参与合并 —— 与 ``VenueAsset.country`` 同一条。"""
    known = account(country="GB")
    assert merge_accounts(known, account(country=None)).country == "GB"
    assert merge_accounts(account(country=None), known).country == "GB"


def test_an_empty_country_is_evidence_and_therefore_participates() -> None:
    """``""`` = **查了，是没有**。它是一条结论，不是「什么都没说」。

    这一格最容易写成 ``if incoming.country:``（按真值判断），而那会把这条证据
    静默丢掉，同时把「还没查」与「查过没有」读成同一件事 —— 而那两件事的下一步不同
    （一个是去查，一个是别查了）。
    """
    assert merge_accounts(account(country="GB"), account(country="")).country == ""


def test_a_country_is_replaced_by_a_later_real_value() -> None:
    """真的查到了什么，就按后来那个 —— 与上面两条一起构成本格完整的四支。"""
    assert merge_accounts(account(country="IE"), account(country="GB")).country == "GB"


# --- ``status``：``discovered`` 不覆盖一个真判定 ---------------------------------


def test_a_row_that_carries_no_verdict_does_not_erase_one() -> None:
    """一次重新研究把 ``eligible`` 打回 ``discovered``，报告上只是「这一行更新了」。

    这是这一格最容易出错的形状：重新研究交上来的行带的是**缺省值**
    （``discovered``），而缺省值是「还没有判定」。按「后看到的为准」处理，池子会在
    每一次重研究之后把 Scene 判过的准入结论**整批抹掉**，而报告上读起来像「这一轮
    没有合格的账号了」。
    """
    eligible = account(status=AccountStatus.ELIGIBLE)
    assert merge_accounts(eligible, account()).status is AccountStatus.ELIGIBLE


def test_a_real_verdict_replaces_a_real_verdict() -> None:
    """但一个**真的**判定要能改另一个 —— 否则 §6 的「逐 Scene 的评估」永远改不了。

    两个都判过时按后来那个走，而这不是一条质量判断：本层没有「谁的判定更权威」的
    判据（来源可信度、时间先后都不是它），所以按「后看到的为准」——与资产池第 3 条
    同一条处置。**这一格里没有冲突可抛**：``status`` 是一个 Scene 的评估，它会随
    Scene 的产品与证据变化，不是一个关于世界的事实。
    """
    assert (
        merge_accounts(
            account(status=AccountStatus.ELIGIBLE), account(status=AccountStatus.HOLD)
        ).status
        is AccountStatus.HOLD
    )


def test_a_verdict_lands_on_a_row_that_had_none() -> None:
    """第三支与第四支：``discovered`` 那一侧的两个方向都要通。"""
    assert (
        merge_accounts(account(), account(status=AccountStatus.RESEARCHED)).status
        is AccountStatus.RESEARCHED
    )
    assert (
        merge_accounts(account(), account()).status is AccountStatus.DISCOVERED
    ), "两个都没判过，结果仍然是「还没判过」"


# --- ``organisation``：这一行唯一一个「先看到的为准」 ----------------------------


def test_the_name_that_was_recorded_first_stays_the_name() -> None:
    """**这是这份规则里唯一一处不按「后看到的为准」走的格子。**

    一份报告的抬头不该在没有任何证据变化的情况下换个名字 —— 那一次重新研究带上来的
    证据全都一样，只有写法不同，而读报告的人没有任何一栏看得出这件事发生过。
    """
    first = account(organisation="Example PC")
    second = account(organisation="Example Parish Council")
    assert merge_accounts(first, second).organisation == "Example PC"
    assert merge_accounts(second, first).organisation == "Example Parish Council"


def test_the_other_spelling_is_recorded_rather_than_dropped() -> None:
    """不改名字**不等于**把另一个写法丢掉：它进 ``aliases``。

    两者的合集（``{organisation} ∪ set(aliases)``）只增不减 —— 这是「A 方案不丢信息」
    这句话的可执行形态。
    """
    merged = merge_accounts(
        account(organisation="Example PC"), account(organisation="Example Parish Council")
    )
    assert merged.aliases == ["Example Parish Council"]


def test_the_name_it_carries_now_is_never_also_listed_as_an_alias() -> None:
    """别名那一格的定义是「**其余**写法」，所以当前这个名字不在里面。

    ★ 这一条钉的是**合并规则顺手修掉的一种构造**：来行自己把当前名字列成别名
    （``organisation="X", aliases=["X"]``）是模型允许的（模型上没有这条判据），
    而折一次之后它必须自己消失 —— 否则报告上会出现「名称：X；别名：X」这种读起来
    像坏掉的行。修在合并里而不是加一条拒绝判据，是因为它不是一个错误输入：那个写法
    在那一次研究的上下文里是对的（那个组织确实用过这个名字），只是它现在正好是主语。
    """
    merged = merge_accounts(
        account(organisation="Example PC"),
        account(organisation="Example PC", aliases=["Example PC", "Example Parish Council"]),
    )
    assert merged.aliases == ["Example Parish Council"]


def test_names_never_leave_the_row_once_recorded() -> None:
    """名字的全集只增不减 —— 三轮研究各给一个写法，三个都要在。

    这一条与上一条合起来才是完整的：减的只有「当前那一个」，别的什么都不减。
    """
    first = account(organisation="Example PC")
    second = account(organisation="Example Parish Council")
    third = account(organisation="Example Town One P.C.")
    merged = merge_accounts(merge_accounts(first, second), third)
    assert merged.organisation == "Example PC"
    assert merged.aliases == ["Example Parish Council", "Example Town One P.C."]


# --- 合并出来的行要重新过模型校验 ------------------------------------------------


def test_the_merged_row_goes_through_the_model_validators() -> None:
    """合并结果**不许**绕开 ``MarketAccount`` 的校验器。

    ``model_copy(update=...)`` 不跑校验器，而模型上的校验器是好几条判据的**唯一**
    执行器（「至少要一条证据」「时间戳必须带时区」）。合并出来的行绕开它们，就等于给
    「一行没有证据的账号」或者「一个 naive 的时刻」被写进池子开了一条路 —— 而它在下游
    读起来是「我们查到了」。

    ## 为什么这一行是 ``model_construct`` 造的

    与 ``tests/test_application_relations.py`` 里那一条逐字同一个理由：走正常构造的话，
    ``existing`` 与 ``incoming`` 都已经过了校验器，于是**它们**的合并结果不可能不合法。
    所以这一条问的其实是「这道校验还在不在」：``model_construct`` 造一行**按正常路径造
    不出来的**已有行（naive 的 ``last_verified_at``），来行走 ``None``（合法，且
    ``later_of`` 里不参与比较），于是那一格原样进到结果里 —— 只有 ``model_validate``
    会拦住它。换成 ``model_copy``，下面的 ``ValidationError`` 不会出现。
    """
    smuggled = MarketAccount.model_construct(
        **{**account().model_dump(), "last_verified_at": datetime(2026, 9, 24, 1, 0)}
    )
    with pytest.raises(ValidationError):
        merge_accounts(smuggled, account())


# --- 生产者：一批账号 → 逐行的去向（``remember_accounts``） ----------------------
#
# 这一段与上面那一段的分工，与 ``tests/test_application_relations.py`` 逐字相同：
# 上面判的是规则本身，这里判的是**每一行交上去之后落到哪里去了**。


def pool() -> AccountPoolStore:
    """接**真**合并规则的内存替身 —— 理由见文件 docstring 的那一小段。"""
    return InMemoryAccountPoolStore(merge_accounts)


def evidence_store(*evidence_ids: str) -> InMemoryEvidenceStore:
    """一个只装了这几条证据的库 —— 「解不开的引用」才是这一层要拒绝的东西。"""
    store = InMemoryEvidenceStore()
    for evidence_id in evidence_ids:
        store.put_evidence(make_evidence(evidence_id=evidence_id, account_id="acct-1"))
    return store


def claim(
    account_id: str = "acct-1",
    organisation: str = "Example Parish Council",
    evidence_ids: Sequence[str] = ("ev-1",),
    **overrides: Any,
) -> dict[str, Any]:
    """一条账号的**原文**（字典，不是模型）—— 生产者收下的就是这个形状。"""
    payload: dict[str, Any] = {
        "account_id": account_id,
        "organisation": organisation,
        "evidence_ids": list(evidence_ids),
    }
    payload.update(overrides)
    return payload


def dispatch(
    rows: Sequence[Mapping[str, Any]],
    *,
    accounts: AccountPoolStore,
    evidence: InMemoryEvidenceStore,
) -> tuple[AccountOutcome, ...]:
    return remember_accounts(
        rows,
        store=accounts,
        evidence=evidence,
        workspace=WORKSPACE,
        seen_at=T0,
    )


def test_a_clean_batch_writes_one_row_per_claim() -> None:
    """三行互不相同的账号 → 三条去向、池子里三行。

    ``reason`` 在这三条上都是空串：这一格说的是「为什么没写」或「折进了第几行」，
    一次顺利的写入没有理由要说 —— 给它填一句「成功」，读的人就得去分辨「有理由」
    与「有内容」两件事。
    """
    accounts = pool()
    outcomes = dispatch(
        [
            claim(),
            claim(account_id="acct-2", organisation="Example Town Council"),
            claim(account_id="acct-3", organisation="Example RFC"),
        ],
        accounts=accounts,
        evidence=evidence_store("ev-1"),
    )
    assert [outcome.disposition for outcome in outcomes] == [AccountDisposition.WRITTEN] * 3
    assert [outcome.ordinal for outcome in outcomes] == [0, 1, 2]
    assert len(accounts.list_accounts()) == 3
    assert [outcome.reason for outcome in outcomes] == ["", "", ""]


def test_the_workspace_is_the_parameter_and_every_row_lands_on_it() -> None:
    """一次调用说的是**一个 Scene** 的一批账号 —— 行里的 ``workspace`` 全部等于参数。

    一个能填 ``workspace`` 的入参等于让一次调用把行写进另一个 Scene（两张表在同一个
    库文件里）。§6 的「客观事实可以跨 Scene 复用」说的是第二个 Scene 写**它自己的**
    那一行，不是一次调用替另一个 Scene 写。
    """
    accounts = pool()
    dispatch(
        [claim(), claim(account_id="acct-2")],
        accounts=accounts,
        evidence=evidence_store("ev-1"),
    )
    assert {row.workspace for row in accounts.list_accounts()} == {WORKSPACE}


def test_a_second_claim_about_the_same_account_folds_and_names_the_earlier_row() -> None:
    """同一批里两行说了同一个 ``account_id``：池子里只有一行，而第二行**另有**一种去向。

    把折叠藏进 ``WRITTEN`` 里，读的人会从一个行数里得出一个账号数 ——「这次调用写了
    两行」与「这个市场多了两个账号」是两句不同的话。

    ``reason`` 要指名**第几行**：只说「折了」的话，交上来二十行的人不知道折的是哪两行。
    """
    accounts = pool()
    outcomes = dispatch(
        [claim(evidence_ids=["ev-1"]), claim(evidence_ids=["ev-2"])],
        accounts=accounts,
        evidence=evidence_store("ev-1", "ev-2"),
    )
    assert [item.disposition for item in outcomes] == [
        AccountDisposition.WRITTEN,
        AccountDisposition.FOLDED,
    ]
    assert "第 0 行" in outcomes[1].reason
    assert len(accounts.list_accounts()) == 1, "两行说的是同一个账号，池子里就该只有一行"


def test_the_fold_takes_the_union_and_not_the_last_row() -> None:
    """折叠走的是**那一条**公共规则：清单格与证据取并集、保序，而不是「后一行覆盖前一行」。

    这条与存储契约测试里那条「存储调用了接进来的规则」不是同一件事：那边判的是存储
    有没有喊规则，这边判的是**喊对了规则**。
    """
    accounts = pool()
    dispatch(
        [
            claim(evidence_ids=["ev-1"], domains=["client05.example.org"], aliases=["Example PC"]),
            claim(
                evidence_ids=["ev-2", "ev-1"],
                domains=["client05.example.org", "www.client05.example.org"],
                aliases=["Example Parish Council"],
            ),
        ],
        accounts=accounts,
        evidence=evidence_store("ev-1", "ev-2"),
    )
    landed = accounts.list_accounts()[0]
    assert landed.evidence_ids == ["ev-1", "ev-2"]
    assert landed.domains == ["client05.example.org", "www.client05.example.org"]
    assert landed.aliases == ["Example PC"]


def test_the_outcome_carries_the_row_that_is_in_the_pool() -> None:
    """``row`` 是**池子里现在那一行**（``upsert_account`` 的返回值），不是交上去的那一份。

    报出交上去的那一份，读的人会以为池子里就是那样 —— 而折叠之后证据是并集、
    ``organisation`` 是**第一次**记下的那个。
    """
    accounts = pool()
    outcomes = dispatch(
        [claim(), claim(organisation="Example PC", evidence_ids=["ev-2"])],
        accounts=accounts,
        evidence=evidence_store("ev-1", "ev-2"),
    )
    landed = accounts.list_accounts()[0]
    assert outcomes[1].row == landed
    assert outcomes[1].row is not None
    assert outcomes[1].row.evidence_ids == ["ev-1", "ev-2"], (
        "第二行交上来的是 ['ev-2']，而它拿回去的是池子里那一行的全部凭据"
    )
    assert outcomes[1].row.organisation == "Example Parish Council", (
        "第二行交上来的是另一个写法，而池子里那一行的主语仍然是第一次记下的那个"
    )


def test_the_same_evidence_twice_in_one_claim_lands_once() -> None:
    """同一条证据（以及同一个域名）写两遍**不是**两次独立看到 —— 去重发生在写之前。"""
    accounts = pool()
    dispatch(
        [
            claim(
                evidence_ids=["ev-1", "ev-1", "ev-2", "ev-1"],
                domains=["a.example", "a.example"],
            )
        ],
        accounts=accounts,
        evidence=evidence_store("ev-1", "ev-2"),
    )
    landed = accounts.list_accounts()[0]
    assert landed.evidence_ids == ["ev-1", "ev-2"]
    assert landed.domains == ["a.example"]


def test_a_second_call_about_the_same_account_accumulates_and_still_leaves_one_row() -> None:
    """两次**调用**之间的重折：走的是同一条规则，池子里仍然只有一行。

    这一条与上面那条批量内折叠是两件事，而它们的去向**不同**：``FOLDED`` 说的是
    「同一批里前面的某一行」，所以第二次调用交上来的那一行是 ``WRITTEN``。
    """
    accounts = pool()
    evidence = evidence_store("ev-1", "ev-2")
    first = dispatch([claim(evidence_ids=["ev-1"])], accounts=accounts, evidence=evidence)
    second = dispatch([claim(evidence_ids=["ev-2"])], accounts=accounts, evidence=evidence)
    assert [item.disposition for item in first + second] == [
        AccountDisposition.WRITTEN,
        AccountDisposition.WRITTEN,
    ]
    assert len(accounts.list_accounts()) == 1
    assert accounts.list_accounts()[0].evidence_ids == ["ev-1", "ev-2"]


def test_recording_an_account_does_not_verify_it() -> None:
    """``last_verified_at`` 留 ``None`` —— 记下一个账号**不是**重新核实了它。

    填上它，「这个账号多久没复核了」这个只在池子建起来之后才有意义的问句，会在第一次
    写入那天就得到「刚刚复核过」，而下一轮维护会把它们整批跳过。
    """
    accounts = pool()
    outcomes = dispatch([claim()], accounts=accounts, evidence=evidence_store("ev-1"))
    assert [item.row.last_verified_at for item in outcomes if item.row] == [None]
    assert accounts.list_accounts()[0].last_verified_at is None
    assert accounts.list_accounts()[0].first_seen_at == T0, (
        "第一次记下的时刻取的是 seen_at 参数（不是时钟），所以这一条能被逐条断言"
    )


def test_one_bad_row_does_not_carry_the_batch() -> None:
    """§6 逐字：「一行失败不牵连其余」。一行引用了解不开的证据，前后两行照写。"""
    accounts = pool()
    outcomes = dispatch(
        [
            claim(),
            claim(account_id="acct-2", organisation="Example Town Council", evidence_ids=["nope"]),
            claim(account_id="acct-3", organisation="Example RFC"),
        ],
        accounts=accounts,
        evidence=evidence_store("ev-1"),
    )
    assert [item.disposition for item in outcomes] == [
        AccountDisposition.WRITTEN,
        AccountDisposition.EVIDENCE_UNKNOWN,
        AccountDisposition.WRITTEN,
    ]
    assert len(accounts.list_accounts()) == 2
    assert outcomes[1].ordinal == 1, "去向要带序号 —— 「我交的第几行没进去」靠它回答"


def test_an_unresolvable_evidence_reference_is_refused_by_name() -> None:
    """拒绝要说得出**是哪一条**引用，而且要说清那一行的后果。"""
    accounts = pool()
    outcomes = dispatch(
        [claim(evidence_ids=["ev-1", "never-stored"])],
        accounts=accounts,
        evidence=evidence_store("ev-1"),
    )
    assert outcomes[0].row is None
    assert "never-stored" in outcomes[0].reason
    assert "ev-1" not in outcomes[0].reason, "解不开的才是要说的，解得开的不该被列进来"
    assert "这一行不写" in outcomes[0].reason
    assert accounts.list_accounts() == (), "拒绝掉的行不许留下痕迹"


def test_a_row_that_is_not_an_account_is_rejected_on_its_own() -> None:
    """构造不出 ``MarketAccount`` 的那一行有自己的去向，其余照写。

    缺证据的那一行走的是**行**那条判据（「一个没有依据的账号写不出来」，在
    ``domain/account.py`` 上），所以它落在 ``REJECTED`` 上。这一条要说清的是它**从哪道
    闸门**走的：``AccountClaim`` 收下了这一行（``evidence_ids`` 在它那儿没有下限），
    拒的是后面那一次构造 —— 两句话的前缀不同，而调用方要靠它决定是去改输入的**形状**，
    还是去补这一行的**内容**。
    """
    accounts = pool()
    outcomes = dispatch(
        [
            claim(),
            {"account_id": "acct-2", "organisation": "Example Town Council", "evidence_ids": []},
            claim(account_id="acct-3", organisation="Example RFC"),
        ],
        accounts=accounts,
        evidence=evidence_store("ev-1"),
    )
    assert [item.disposition for item in outcomes] == [
        AccountDisposition.WRITTEN,
        AccountDisposition.REJECTED,
        AccountDisposition.WRITTEN,
    ]
    assert outcomes[1].reason.startswith("这一行不是一个能写下来的账号："), (
        "拒它的是行那一道判据 —— 认错了闸门，调用方会去改一个本来就对的地方"
    )
    assert "一条证据都没有" in outcomes[1].reason
    assert outcomes[1].row is None
    assert len(accounts.list_accounts()) == 2


def test_a_broken_row_still_says_which_row_it_was() -> None:
    """一行连字典都不是时，去向里回显的是**调用方交来的原文**（用于对账）。

    ``account_id`` 与 ``organisation`` 两格都要回显：一条被拒的行往往正是 id 或名字
    本身有问题，只留一格的话，交上来二十行的人认不出那是哪一行。
    """
    accounts = pool()
    outcomes = dispatch(
        [{"account_id": 7, "organisation": None}, claim()],
        accounts=accounts,
        evidence=evidence_store("ev-1"),
    )
    assert outcomes[0].disposition is AccountDisposition.REJECTED
    assert outcomes[0].account_id == "7", "数字照它交来的样子回显，不当成 0，也不当成空"
    assert outcomes[0].organisation == "None"
    assert "不是一个账号" in outcomes[0].reason


@pytest.mark.parametrize("status", ["eligible", "hold", "researched"])
def test_a_verdict_the_caller_gives_is_what_lands(status: str) -> None:
    """判定取**调用方交来的那一个** —— §6 要的七个取值里，非缺省的那几个是可以写下来的。

    这一格是逐 Scene 的评估（§6），而这一层没有第二个来源能算出它：池子里的 ``status``
    是**写下来的那一次判定**，不是「此刻按现行规则重跑一遍」（后者是 ``list_accounts``
    对清单行做的事）。所以它必须能从生产者这一侧进来，否则那七个取值只是文档里的一句话。
    """
    accounts = pool()
    outcomes = dispatch(
        [claim(status=status)], accounts=accounts, evidence=evidence_store("ev-1")
    )
    assert outcomes[0].row is not None
    assert outcomes[0].row.status.value == status


def test_a_status_outside_the_seven_is_one_row_s_fate() -> None:
    """取值不在那七个里 → **这一行**不写，理由里点名七个可用取值。

    在更早的地方把它当类型收下（``AccountStatus``），意味着一行写错了枚举会把整次调用
    炸掉，而同一批里其余的账号与它无关 —— 与 ``RelationClaim.relation`` 逐字同一条。
    """
    accounts = pool()
    outcomes = dispatch(
        [claim(status="probably_fine"), claim(account_id="acct-2", organisation="Example RFC")],
        accounts=accounts,
        evidence=evidence_store("ev-1"),
    )
    assert outcomes[0].disposition is AccountDisposition.REJECTED
    assert "probably_fine" in outcomes[0].reason
    for member in AccountStatus:
        assert member.value in outcomes[0].reason, f"{member.value} 没有被列出来"
    assert outcomes[1].disposition is AccountDisposition.WRITTEN


def test_an_empty_status_is_not_the_same_as_omitting_it() -> None:
    """``""`` 是**给错了**（走 ``REJECTED``），而**不给**这一格是「还没有判定」。

    两者的下一步不同：前者要改那一行输入，后者是这批研究本来就没做判定。所以缺省取的是
    ``discovered``（一个真的取值），空串不取 —— 这与 ``RelationClaim.relation`` 的缺省
    恰好相反，因为那边连「还没定下来」（``unresolved``）也是一次要凭据的判定。
    """
    accounts = pool()
    outcomes = dispatch(
        [claim(), claim(account_id="acct-2", organisation="Example RFC", status="")],
        accounts=accounts,
        evidence=evidence_store("ev-1"),
    )
    assert outcomes[0].row is not None
    assert outcomes[0].row.status is AccountStatus.DISCOVERED
    assert outcomes[1].disposition is AccountDisposition.REJECTED


def test_a_re_research_that_gives_no_verdict_does_not_erase_the_one_in_the_pool() -> None:
    """「这次没判」不许把上一次的判定抹掉 —— 生产者的方向与合并规则的第 4 条接得上。

    这是这两半住在同一个模块里的**理由本身**：一个只由生产者填、合并规则却不认识的
    缺省值，会在每一次重研究之后把 Scene 判过的结论整批抹掉，而报告上读起来像
    「这一轮没有合格的账号了」。
    """
    accounts = pool()
    evidence = evidence_store("ev-1", "ev-2")
    dispatch([claim(status="eligible", evidence_ids=["ev-1"])], accounts=accounts, evidence=evidence)
    second = dispatch([claim(evidence_ids=["ev-2"])], accounts=accounts, evidence=evidence)
    assert second[0].row is not None
    assert second[0].row.status is AccountStatus.ELIGIBLE
    assert second[0].row.evidence_ids == ["ev-1", "ev-2"]


def test_a_row_that_is_both_a_repeat_and_unwritable_is_reported_as_unwritable() -> None:
    """判据的顺序：**能不能写下来**在前，「重不重复」在后。

    反过来的顺序会把这一行报成 ``FOLDED``，而那句话在纸面上是真的 —— 这个账号确实在
    第 0 行进池子了。于是交上来的一行**带着一个写不下来的判定**（比如一个不存在的
    ``status``），回执上却是「重复，跳过」：那一格错误的值从这一侧**再也不会被提起**，
    而池子里这一行的判定停在之前那个上，报告上读起来像「这一批的判定就是这个」。

    重复是一种**后果**，不是一条判据 —— 只有能写下来的行才谈得上「与第 0 行说的是同一个
    账号」。
    """
    accounts = pool()
    outcomes = dispatch(
        [claim(status="eligible"), claim(status="probably_fine")],
        accounts=accounts,
        evidence=evidence_store("ev-1"),
    )
    assert [item.disposition for item in outcomes] == [
        AccountDisposition.WRITTEN,
        AccountDisposition.REJECTED,
    ]
    assert "probably_fine" in outcomes[1].reason
    assert len(accounts.list_accounts()) == 1

"""``application/relations.py`` 这一层的两条判据：**合并规则**与**生产者**。

那两份工作住在同一个模块里（理由写在模块 docstring 上），判据也就住同一个文件，
但它们测的是两件事，所以这里分上下两半：

* **上半：合并规则**（``merge_relations``）。它是**纯函数**（不读盘、不写盘、不碰
  时钟），所以逐条测它，一个存储都不需要。
* **下半：生产者**（``remember_relations``）。它要用一个存储，用的是内存替身 ——
  这一层要判的是「一行判定交上去之后落到哪里去了」，与存储怎么落盘无关。
* **末段：依据**（``remember_researched_relations``）。它多问一句「这条结论背后
  那次判定站不站得住」，所以它要用一个判定台账。台账在这个文件里是替身，理由与
  下半同一条；**真实台账写出来的记录长什么样**由另一条路证明：那条路由 ``decide``
  真的写一条记录、再由 ``submit_findings`` 真的消费它，
  在 ``tests/test_mcp_responsibility_relations.py``。两份判据分工是「这一层读对
  了没有」与「它读的那些格子在真实记录上存在吗」，合成一份会让后者被前者遮住。

「存储有没有把接进来的规则用上」是第三件事，在
``tests/test_relation_store_contract.py`` 里对着**两个**实现测。三份判据合成一份会让
「规则对」「规则被调用了」「生产者给出对的去向」互相遮蔽 —— 三条里先红的那一条，读起来
会是另外两条。

## 这里每一条都对应一个**曾经会被静默吃掉**的东西

* ``first_seen_at`` 被一次重放的旧判定推后 —— 表会声称一条早就知道的关系是刚发现的；
* ``last_verified_at`` 被推回 —— 「最后复核过它」永远停在第一次；
* 一次复核带进来的新证据**替掉**已有那几条 —— 而报告上只显示「证据变了」；
* 一个写错的键把**另一条**关系整个改写掉 —— 那条关系看起来只是「信息变了」；
* 一批里有一行坏判定把整批带走（或反过来：整批都被收下，其中一行是空的）；
* 「这次调用写了 4 行」被读成「这个市场多了 4 条责任」（而其中两条说的是同一条）。

所以这一份里的断言大多是**否定式**的（「没有被推后」「没有被抹掉」「另一行没受影响」），
而不是「等于新值」。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from marketops.adapters.memory import (
    InMemoryAccountVenueRelationStore,
    InMemoryEvidenceStore,
)
from marketops.application.decision_specs import compile_specs
from marketops.application.evidence_refs import MAX_LISTED_REFS
from marketops.application.merge import later_of, ordered_union
from marketops.application.relations import (
    RelationDisposition,
    RelationOutcome,
    merge_relations,
    remember_relations,
    remember_researched_relations,
)
from marketops.domain.entities import AccountVenueRelation
from marketops.domain.enums import DecisionOutcome, DecisionTask, VenueRelation
from marketops.domain.errors import StoreError
from marketops.domain.findings import Findings, RelationFinding
from marketops.ports.decisions import DecisionRecordStore, DecisionSpec, PinnedScene
from marketops.ports.pool import AccountVenueRelationStore
from tests.test_evidence_store_contract import make_evidence

UTC = timezone.utc
T0 = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
EARLIER = T0 - timedelta(days=30)
LATER = T0 + timedelta(days=30)
WORKSPACE = "uk-acme-natural-grass"


def relation(**overrides) -> AccountVenueRelation:
    payload = {
        "workspace": WORKSPACE,
        "account_id": "acct-1",
        "venue_asset_id": "v1",
        "relation": VenueRelation.MAINTAINS,
        "evidence_ids": ["ev-1"],
        "first_seen_at": T0,
    }
    payload.update(overrides)
    return AccountVenueRelation(**payload)


# --- 这一份规则的字段清单 ------------------------------------------------------


#: 关系行上**不由**那三条规则处置的格子，以及各自的规则。
#:
#: 它存在的唯一理由是让下面那条判据能把「有一格没人处置」变成一个红 ——
#: ``merge_relations`` 的合并结果是 ``{**existing.model_dump(), **changes}``，
#: 没进 ``changes`` 的格子就取**已有的那一行**：那一格会停在这一行落进表里时的样子，
#: 一次新的判定再怎么带着它也传不进去，而报告上读起来只是「这一条关系的这一栏一直
#: 没变」（资产池那一份的同一张表，理由逐字相同）。
RELATION_CELLS_WITH_THEIR_OWN_RULE: dict[str, str] = {
    # 键的四段：与表里那一行不一致时抛 StoreError，不合并。
    "workspace": "键的一段 —— 折错了抛 StoreError",
    "account_id": "键的一段 —— 同上",
    "venue_asset_id": "键的一段 —— 同上",
    # ``relation`` 是键的一段这件事本身就是 §5 的判定：同一个账号对同一块场地可以
    # 同时有 owns 与 maintains，所以「换个关系」不是一次合并，是另一行。
    "relation": "键的一段 —— 所以 owns 与 maintains 是两行，不是一次冲突",
    # 时刻：一个只往早走，一个只往晚走（None 不参与）。
    "first_seen_at": "只往早走（min）",
    "last_verified_at": "只往晚走（max），None 是「没再看」不是「时间零点」",
    # 证据指针：并集、保序。
    "evidence_ids": "并集、保序 —— 是指针不是正文",
    # 判定指针：与证据同一条（并集、保序）。一条关系被再判一次是**加强**，不是把
    # 上一次作废 —— 只留最新那一个 id 会让更早那次调用从结果上反查不到，而它带进来
    # 的证据还在 ``evidence_ids`` 那一格里。
    "decision_record_ids": "并集、保序 —— 与 evidence_ids 逐字同一条",
}


def test_every_cell_on_a_relation_row_has_a_decided_fate() -> None:
    """每一格都必须**被这份规则处置过** —— 要么在那三条规则里，要么在下面那份名单里。

    这是 §5·1 加字段时那句「这里必须被想到」的执行器。没有它，那一句是一句自觉：
    新加的格子落在名单外，处置是「永远取已有那一行」，而 ``merge_relations`` 里没有
    任何一行代码会因为漏了它而报错。
    """
    cells = set(AccountVenueRelation.model_fields)
    decided = set(RELATION_CELLS_WITH_THEIR_OWN_RULE)
    assert cells - decided == set(), (
        "这些格子没人处置 —— 它们会**永远取表里已有的那一行**，也就是从第二次判定起"
        f"再没人能改到它们：{sorted(cells - decided)}\n"
        "把它们加进 merge_relations 的 changes（并在这里写清规则），或者加进本文件的"
        " RELATION_CELLS_WITH_THEIR_OWN_RULE。"
    )
    assert decided - cells == set(), (
        f"这份名单里有多余的名字（模型上已经没有这些格子了）：{sorted(decided - cells)}"
    )


# --- 三条规则 ----------------------------------------------------------------


def test_the_earliest_first_seen_wins() -> None:
    """``first_seen_at`` 只往**早**走。

    重放一次旧判定不该把它推后：推后之后，表会声称一条早就知道的关系是刚发现的，
    而「这条关系我们看了多久」正是 §11 排复核顺序时要用的那一格。
    """
    merged = merge_relations(relation(first_seen_at=T0), relation(first_seen_at=EARLIER))
    assert merged.first_seen_at == EARLIER


def test_a_later_verification_wins_and_an_absent_one_does_not() -> None:
    """``last_verified_at`` 只往**晚**走，而 ``None``（没再看过）不参与。**

    两个方向都要测：
    * 来行带着一个更晚的时刻 → 落进去（这是「复核过了」的正常路径）；
    * 来行是 ``None``、已有的行有一个时刻 → **不被抹成 ``None``**。这一半才是要点：
      一次重放（或者一次没有真的重新看过的写入）如果把这一格抹掉，复核清单上这一条
      会永远排在最前，而它其实刚刚被看过。
    """
    verified = relation(last_verified_at=LATER)
    assert merge_relations(relation(), verified).last_verified_at == LATER
    assert merge_relations(verified, relation()).last_verified_at == LATER


def test_a_verification_never_moves_backwards() -> None:
    """更早的一次复核不覆盖更晚的那一次 —— 同一格的另一个方向。"""
    merged = merge_relations(relation(last_verified_at=LATER), relation(last_verified_at=T0))
    assert merged.last_verified_at == LATER


def test_evidence_accumulates_and_never_replaces() -> None:
    """证据取**并集**、保序：第二次独立看到同一件事是**加强**，不是重复。

    顺序是承重的 —— 「先看到的在前」，人读证据链时就是这个顺序。所以已有的那几条
    必须原样在前面，新来的接在后面。
    """
    merged = merge_relations(
        relation(evidence_ids=["ev-1", "ev-2"]), relation(evidence_ids=["ev-3"])
    )
    assert merged.evidence_ids == ["ev-1", "ev-2", "ev-3"]


def test_the_same_evidence_twice_lands_once() -> None:
    """同一个 ``evidence_id`` 折两次只留一条 —— 并集去掉重复，而顺序照第一次出现。"""
    merged = merge_relations(
        relation(evidence_ids=["ev-1", "ev-2"]), relation(evidence_ids=["ev-2", "ev-3"])
    )
    assert merged.evidence_ids == ["ev-1", "ev-2", "ev-3"]


def test_a_first_fold_is_the_row_itself() -> None:
    """表里没有这一行时，折进去的就是它本身 —— 规则不该在第一次写入时改造它。"""
    incoming = relation(relation=VenueRelation.UNRESOLVED, evidence_ids=["ev-9"])
    assert merge_relations(None, incoming) is incoming


def test_the_decisions_accumulate_and_never_replace() -> None:
    """判定 id 与证据逐字同一条：**并集、保序**。

    一条关系被再判一次是加强，不是把上一次作废。这一格只留最新那一个的话，更早那次
    调用从结果上就反查不到了 —— 而它带进来的证据还在这一行的 ``evidence_ids`` 里，
    于是那一行挂着一条无主的证据。
    """
    merged = merge_relations(
        relation(decision_record_ids=["rec-1", "rec-2"]),
        relation(decision_record_ids=["rec-3"]),
    )
    assert merged.decision_record_ids == ["rec-1", "rec-2", "rec-3"]


def test_the_same_decision_twice_lands_once() -> None:
    """同一次判定被折两次只留一条 —— 重放不是第二次判定。"""
    merged = merge_relations(
        relation(decision_record_ids=["rec-1", "rec-2"]),
        relation(decision_record_ids=["rec-2", "rec-3"]),
    )
    assert merged.decision_record_ids == ["rec-1", "rec-2", "rec-3"]


# --- 没有冲突这一支，而这不是漏掉的 --------------------------------------------


def test_two_relations_between_the_same_pair_never_meet_in_a_merge() -> None:
    """§5 的「不强制单选」在**这一层**的形态：它根本走不到合并。

    ``relation`` 是键的一段，所以 ``owns`` 与 ``maintains`` 是两条行 —— 而合并只在
    **同一行**上发生。把它写成一个「哪个关系赢」的规则，等于在代码里把
    「一个 Asset 可以同时有多种责任」这条要求反过来实现了一遍。
    """
    with pytest.raises(StoreError, match="owns"):
        merge_relations(relation(relation=VenueRelation.OWNS), relation())


# --- 键对不上：抛，不折 --------------------------------------------------------


@pytest.mark.parametrize(
    "field, value",
    [
        ("account_id", "acct-2"),
        ("venue_asset_id", "v2"),
        ("relation", VenueRelation.OPERATES),
        ("workspace", "uk-something-else"),
    ],
    ids=["account", "venue", "relation", "workspace"],
)
def test_folding_a_different_key_is_refused(field: str, value: str) -> None:
    """**四段里有一段不同，说的就是不同的东西。**

    照折的话，一次写错的键会把另一条关系整个改写掉，而报告上它看起来只是那条关系的
    信息变了 —— 这是本层最坏的一种失败（它不报错，只是把一条判定换成了另一条）。
    四段逐段测：只挡住其中一段的实现会在参数化的另外三个里红。
    """
    with pytest.raises(StoreError):
        merge_relations(relation(), relation(**{field: value}))


def test_the_refusal_says_which_two_keys_they_were() -> None:
    """报错要说得出**是哪两条**关系 —— 否则调用点只知道「键不对」。"""
    with pytest.raises(StoreError) as caught:
        merge_relations(relation(), relation(account_id="acct-2"))
    message = str(caught.value)
    assert f"{WORKSPACE}/acct-1/v1/maintains" in message
    assert f"{WORKSPACE}/acct-2/v1/maintains" in message


# --- 合并出来的行要重新过模型校验 ----------------------------------------------


def test_the_merged_row_goes_through_the_model_validators() -> None:
    """合并结果**不许**绕开 ``AccountVenueRelation`` 的校验器。

    ``model_copy(update=...)`` 不跑校验器，而模型上的校验器是好几条判据的**唯一**
    执行器（「至少要一条证据」「时间戳必须带时区」）。合并出来的行绕开它们，就等于
    给「一条空证据的关系被写进表里」开了一条路 —— 而它在下游读起来是「我们查到了」。

    ## 为什么这一行是 ``model_construct`` 造的

    走正常构造的话，``existing`` 与 ``incoming`` 都已经过了校验器，于是**它们**的合并
    结果不可能不合法（三个 ``changes`` 都是从两边已校验的取值上算出来的）。所以这一条
    要问的其实是「这道校验还在不在」：用 ``model_construct`` 造一行**按正常路径造不出来
    的**已有行（naive 的 ``last_verified_at``），来行走 ``None``（合法且不参与 ``later_of``
    的比较），于是这一格原样进到结果里 —— 只有 ``model_validate`` 会拦住它。
    换成 ``model_copy``，下面的 ``ValidationError`` 不会出现。
    """
    smuggled = AccountVenueRelation.model_construct(
        **{**relation().model_dump(), "last_verified_at": datetime(2026, 9, 24, 1, 0)}
    )
    with pytest.raises(ValidationError):
        merge_relations(smuggled, relation())


# --- 两个原语本身（``application/merge.py``） ----------------------------------


def test_later_of_treats_none_as_absent_not_as_the_beginning_of_time() -> None:
    """``None`` 不参与 —— 它不是「比任何时刻都早」。

    这一条是 ``later_of`` 单独被测的理由：写成 ``None`` 当零点的话，「只见过一次」
    的那一块场地会在复核清单上永远排最后，而它恰恰是最该被复核的那一块。
    """
    assert later_of(None, T0) == T0
    assert later_of(T0, None) == T0
    assert later_of(T0, LATER) == LATER
    assert later_of(None, None) is None, "两个都没看过，给的是一个「没有」，不是某个时刻"


def test_ordered_union_keeps_the_first_occurrence_order() -> None:
    """并集是**保序**的，而且**只并、不减** —— 撤下一条证据需要它自己的凭据。"""
    assert ordered_union(["a", "b"], ["b", "c"]) == ["a", "b", "c"]
    assert ordered_union([], ["a"]) == ["a"]
    assert ordered_union(["a"], []) == ["a"]


# --- 生产者：一批判定 → 逐行的去向（``remember_relations``） --------------------
#
# 下面这一半测的是「一行判定交上去之后落到哪里去了」。它要用一个存储，用的是内存
# 替身，而且**接的是真规则**（``merge_relations``）：这里若换成人造规则（store 契约
# 测试里那条留记号的 ``probe_merge``），这一半就不再是生产的形状 —— 而它要判的正是
# 生产路径上每一行的去向。


def relation_store() -> AccountVenueRelationStore:
    """接**真**合并规则的内存替身。理由见上面那段。"""
    return InMemoryAccountVenueRelationStore(merge_relations)


def evidence_store(*evidence_ids: str) -> InMemoryEvidenceStore:
    """一个只装了这几条证据的库 —— 「解不开的引用」才是这一层要拒绝的东西。"""
    store = InMemoryEvidenceStore()
    for evidence_id in evidence_ids:
        store.put_evidence(make_evidence(evidence_id=evidence_id, account_id="acct-1"))
    return store


def claim(
    account_id: str = "acct-1",
    relation: str = "maintains",
    evidence_ids: Sequence[str] = ("e1",),
) -> dict[str, Any]:
    """一条判定的**原文**（字典，不是模型）—— 生产者收下的就是这个形状。"""
    return {
        "account_id": account_id,
        "relation": relation,
        "evidence_ids": list(evidence_ids),
    }


def dispatch(
    rows: Sequence[Mapping[str, Any]],
    *,
    relations: AccountVenueRelationStore,
    evidence: InMemoryEvidenceStore,
    venue_asset_id: str = "v1",
    decision_record_ids: Sequence[Sequence[str]] | None = None,
) -> tuple[RelationOutcome, ...]:
    return remember_relations(
        rows,
        store=relations,
        evidence=evidence,
        workspace=WORKSPACE,
        venue_asset_id=venue_asset_id,
        seen_at=T0,
        decision_record_ids=decision_record_ids,
    )


def test_a_clean_batch_writes_one_row_per_claim() -> None:
    """三行互不相同的判定 → 三条去向、三行表。

    ``reason`` 在这三条上都是空串：这一格说的是「为什么没写」或「折进了第几行」，
    一次顺利的写入没有理由要说 —— 而给它填一句「成功」，读的人就得去分辨「有理由」
    与「有内容」两件事。
    """
    relations = relation_store()
    outcomes = dispatch(
        [
            claim(relation="maintains"),
            claim(account_id="acct-2", relation="owns"),
            claim(account_id="acct-3", relation="booking_control"),
        ],
        relations=relations,
        evidence=evidence_store("e1"),
    )
    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.WRITTEN] * 3
    assert [outcome.ordinal for outcome in outcomes] == [0, 1, 2]
    assert len(relations.list_relations()) == 3
    assert [outcome.reason for outcome in outcomes] == ["", "", ""]


def test_the_venue_is_the_parameter_and_every_row_lands_on_it() -> None:
    """一次调用说的是**一块场地**上的责任 —— 行里的 ``venue_asset_id`` 全部等于参数。

    让每一行自己带场地 id，等于允许一次调用把行写进任意几块场地，而报告上那是一张
    平平的关系表：「这一行是关于哪块场地的」没有一处能出错的地方，也就没有一处能
    发现写错了。
    """
    relations = relation_store()
    dispatch(
        [claim(account_id="acct-1"), claim(account_id="acct-2")],
        relations=relations,
        evidence=evidence_store("e1"),
        venue_asset_id="v-acme-devon",
    )
    assert {row.venue_asset_id for row in relations.list_relations()} == {"v-acme-devon"}


def test_a_second_claim_about_the_same_relation_folds_and_names_the_earlier_row() -> None:
    """同一批里两行说了同一条关系：表里只有一行，而第二行**另有**一种去向。

    把折叠藏进 ``WRITTEN`` 里，读的人会从一个行数里得出一个关系数 ——「这次调用写了
    两行」与「这个市场多了两条责任」是两句不同的话。

    ``reason`` 要指名**第几行**：只说「折了」的话，交上来二十行的人不知道折的是哪
    两行，也就没法回去核对那一行是不是重复。
    """
    relations = relation_store()
    outcomes = dispatch(
        [claim(evidence_ids=["e1"]), claim(evidence_ids=["e2"])],
        relations=relations,
        evidence=evidence_store("e1", "e2"),
    )
    assert [item.disposition for item in outcomes] == [
        RelationDisposition.WRITTEN,
        RelationDisposition.FOLDED,
    ]
    assert "第 0 行" in outcomes[1].reason
    assert len(relations.list_relations()) == 1, "两行说的是同一条关系，表里就该只有一行"


def test_the_fold_takes_the_evidence_union_and_not_the_last_row() -> None:
    """折叠走的是**那一条**公共规则：证据取并集、保序，不是「后一行覆盖前一行」。

    这条与存储契约测试里那条「存储调用了接进来的规则」不是同一件事：那边判的是存储
    有没有喊规则，这边判的是**喊对了规则**。一个「第二次直接把 incoming 写回去」的
    实现在单行上是完全正确的（那一行确实在），只有把证据并集与下面的返回值两半一起
    看，才分得出它。
    """
    relations = relation_store()
    dispatch(
        [claim(evidence_ids=["e1"]), claim(evidence_ids=["e2", "e1"])],
        relations=relations,
        evidence=evidence_store("e1", "e2"),
    )
    assert relations.list_relations()[0].evidence_ids == ["e1", "e2"]


def test_the_outcome_carries_the_row_that_is_in_the_table() -> None:
    """``row`` 是**表里现在那一行**（``upsert_relation`` 的返回值），不是交上去的那一份。

    报出交上去的那一份，读的人会以为表里就是那样 —— 而折叠之后表里的证据是并集、
    ``first_seen_at`` 是更早的那个。
    """
    relations = relation_store()
    outcomes = dispatch(
        [claim(evidence_ids=["e1"]), claim(evidence_ids=["e2"])],
        relations=relations,
        evidence=evidence_store("e1", "e2"),
    )
    landed = relations.list_relations()[0]
    assert outcomes[1].row == landed
    assert outcomes[1].row is not None
    assert outcomes[1].row.evidence_ids == ["e1", "e2"], (
        "第二行交上来的是 ['e2']，而它拿回去的是表里那一条关系的全部凭据"
    )


def test_the_same_evidence_twice_in_one_claim_lands_once() -> None:
    """同一条证据写两遍**不是**两次独立看到 —— 去重发生在交进存储之前。"""
    relations = relation_store()
    dispatch(
        [claim(evidence_ids=["e1", "e1", "e2", "e1"])],
        relations=relations,
        evidence=evidence_store("e1", "e2"),
    )
    assert relations.list_relations()[0].evidence_ids == ["e1", "e2"]


def test_a_second_call_about_the_same_relation_accumulates_and_still_leaves_one_row() -> None:
    """两次**调用**之间的重折：走的是同一条规则，表里仍然只有一行。

    这一条与上面那条批量内折叠是两件事，而它们的去向**不同**：``FOLDED`` 说的是
    「同一批里前面的某一行」，所以第二次调用交上来的那一行是 ``WRITTEN``（这次调用
    确实写了一次），证据并集照样进表 —— 折叠是一次**报告**，不是第二套合并规则。
    """
    relations = relation_store()
    evidence = evidence_store("e1", "e2")
    first = dispatch([claim(evidence_ids=["e1"])], relations=relations, evidence=evidence)
    second = dispatch([claim(evidence_ids=["e2"])], relations=relations, evidence=evidence)
    assert [item.disposition for item in first + second] == [
        RelationDisposition.WRITTEN,
        RelationDisposition.WRITTEN,
    ]
    assert len(relations.list_relations()) == 1
    assert relations.list_relations()[0].evidence_ids == ["e1", "e2"]
    assert second[0].row is not None
    assert second[0].row.evidence_ids == ["e1", "e2"], (
        "第二次交上来的只有 ['e2']，而它拿回去的是表里那一行（并集）—— 报出交上来的"
        "那一份，读的人会以为那一条关系的凭据就只剩这一条了"
    )


def test_recording_a_judgement_does_not_verify_the_relation() -> None:
    """``last_verified_at`` 留 ``None`` —— 记下一条判定**不是**重新证实了这条关系。

    填上它，「这条关系多久没复核了」这个只在表建起来之后才有意义的问句，会在第一次
    写入那天就得到「刚刚复核过」，而下一轮维护会把它们整批跳过。这与
    ``assets_from_receipt`` 不填 ``last_verified_at`` 是同一条纪律。
    """
    relations = relation_store()
    outcomes = dispatch(
        [claim(evidence_ids=["e1"]), claim(evidence_ids=["e2"])],
        relations=relations,
        evidence=evidence_store("e1", "e2"),
    )
    assert [item.row.last_verified_at for item in outcomes if item.row] == [None, None]
    assert relations.list_relations()[0].last_verified_at is None


def test_one_bad_row_does_not_carry_the_batch() -> None:
    """§5 逐字：「单条失败不得破坏整批」。一行引用了解不开的证据，前后两行照写。"""
    relations = relation_store()
    outcomes = dispatch(
        [
            claim(evidence_ids=["e1"]),
            claim(account_id="acct-2", evidence_ids=["never-stored"]),
            claim(account_id="acct-3", relation="owns", evidence_ids=["e1"]),
        ],
        relations=relations,
        evidence=evidence_store("e1"),
    )
    assert [item.disposition for item in outcomes] == [
        RelationDisposition.WRITTEN,
        RelationDisposition.EVIDENCE_UNKNOWN,
        RelationDisposition.WRITTEN,
    ]
    assert len(relations.list_relations()) == 2
    assert outcomes[1].ordinal == 1, "去向要带序号 —— 「我交的第几行没进去」靠它回答"


def test_an_unresolvable_evidence_reference_is_refused_by_name() -> None:
    """拒绝要说得出**是哪一条**引用 —— 只说「证据有问题」，交二十行的人无从下手。

    而且它要说出这一行的**后果**：一条关系挂的依据必须是已经落盘的材料，引用一条
    不存在的证据与一条没有依据的关系是同一件事，只是它看起来像有依据。
    """
    relations = relation_store()
    outcomes = dispatch(
        [claim(evidence_ids=["e1", "never-stored"])],
        relations=relations,
        evidence=evidence_store("e1"),
    )
    assert outcomes[0].row is None
    assert "never-stored" in outcomes[0].reason
    assert "e1" not in outcomes[0].reason, "解不开的才是要说的，解得开的不该被列进来"
    assert "这一行不写" in outcomes[0].reason
    assert relations.list_relations() == (), "拒绝掉的行不许留下痕迹"


def test_a_long_list_of_unresolvable_references_is_folded_into_a_count() -> None:
    """解不开的引用太多时**折成数字**，而不是把二十个 id 塞进一句话里。

    折的地方是 ``application/evidence_refs.py``（那个渲染只有一个定义处，逐条拒绝
    与整批拒绝共用它）—— 这里判的是生产者真的走了那一条，而不是自己拼了一句。
    """
    relations = relation_store()
    missing = [f"never-{index}" for index in range(MAX_LISTED_REFS + 5)]
    outcomes = dispatch(
        [claim(evidence_ids=missing)],
        relations=relations,
        evidence=evidence_store("e1"),
    )
    assert "never-0" in outcomes[0].reason
    assert "never-5" in outcomes[0].reason, "前 10 条要列出来"
    assert "never-10" not in outcomes[0].reason, "第 11 条起折成数字"
    assert f"另有 5 条未列出，共 {MAX_LISTED_REFS + 5} 条" in outcomes[0].reason


def test_a_relation_outside_the_closed_set_is_rejected_with_the_field_named() -> None:
    """取值不在闭集里是**这一行**的去向，不是整次调用的失败。

    所以理由里要看得见 ``relation`` 这一格与它可用的取值，而同一批里其余的行照写 ——
    把这一行当类型在更早的地方收下，意味着一行写错枚举会把整批炸掉。
    """
    relations = relation_store()
    outcomes = dispatch(
        [claim(relation="runs"), claim(account_id="acct-2", relation="owns")],
        relations=relations,
        evidence=evidence_store("e1"),
    )
    assert outcomes[0].disposition == RelationDisposition.REJECTED
    assert outcomes[0].row is None
    assert "relation:" in outcomes[0].reason
    assert "unresolved" in outcomes[0].reason, "理由要列得出可用的取值"
    assert outcomes[1].disposition == RelationDisposition.WRITTEN
    assert len(relations.list_relations()) == 1


def test_an_empty_account_id_is_rejected_with_the_field_named() -> None:
    """一个不知道是谁的责任关系，写进表里就是一条无主的关系 —— 不写，并说出是哪一格。

    空字典也要走到这条拒绝上来（``RelationClaim`` 的三个字段都有缺省，理由写在那个
    模型上）：换成必填字段，缺一个键就变成「你少给了一个键」，与「你给的账号名不规范」
    说成了同一句。
    """
    relations = relation_store()
    outcomes = dispatch([{}, claim()], relations=relations, evidence=evidence_store("e1"))
    assert outcomes[0].disposition == RelationDisposition.REJECTED
    assert "account_id" in outcomes[0].reason
    assert len(relations.list_relations()) == 1


def test_a_claim_without_a_single_evidence_id_is_rejected() -> None:
    """一条没有证据的关系行读起来是「我们查到了」，事实是「我们猜过」。

    那条判据住在模型上（``AccountVenueRelation`` 的 ``model_validator``），这里只判
    生产者**没有绕开**它 —— 一个自己拼行、跳过校验的实现在这条上会红。

    理由里没有字段路径，而这不是缺的：那条判据管的是**整行**（模型级校验器报出来的
    错误没有 ``loc``），句子本身说得出它要的是什么。渲染的那条规则在
    ``tests/test_application_validation.py``。
    """
    relations = relation_store()
    outcomes = dispatch(
        [claim(evidence_ids=[])], relations=relations, evidence=evidence_store()
    )
    assert outcomes[0].disposition == RelationDisposition.REJECTED
    assert "至少要一条证据" in outcomes[0].reason
    assert ": 至少要一条证据" not in outcomes[0].reason, (
        "模型级判据没有路径可指，理由里不该出现一个空的字段名"
    )
    assert relations.list_relations() == ()


def test_a_field_the_claim_does_not_have_is_rejected_rather_than_ignored() -> None:
    """多给一格就是这一行构造不出来 —— 静默忽略等于让调用点以为自己说了什么。

    一个把多余字段丢掉的实现，会让「我以为我标了这条判定的来源」在报告上显示成
    「这条判定没有来源」，而两者读起来一样。
    """
    relations = relation_store()
    outcomes = dispatch(
        [{**claim(), "why": "俱乐部章程第 3 页"}],
        relations=relations,
        evidence=evidence_store("e1"),
    )
    assert outcomes[0].disposition == RelationDisposition.REJECTED
    assert "why" in outcomes[0].reason
    assert relations.list_relations() == ()


def test_a_rejected_row_echoes_what_was_handed_in() -> None:
    """没写进去的行，``account_id`` / ``relation`` 是**调用方交来的原文**，只用于对账。

    它不做任何规范化：交来的 ``7`` 与 ``None`` 照样子回显 —— 这一格要说的是「你交的
    是这个」，而不是「它应该是什么」。同时它是这一层唯一一处会碰到未校验取值的地方，
    所以它不许在这些取值上做判断。
    """
    relations = relation_store()
    outcomes = dispatch(
        [{"account_id": 7, "relation": None}], relations=relations, evidence=evidence_store()
    )
    assert outcomes[0].account_id == "7"
    assert outcomes[0].relation == "None"
    assert outcomes[0].row is None


def test_an_empty_batch_is_an_empty_answer() -> None:
    """一批空判定 → 零条去向、零行，且**没碰过存储**（连工作区绑定都不建立）。"""
    relations = relation_store()
    assert dispatch([], relations=relations, evidence=evidence_store()) == ()
    assert relations.list_relations() == ()


# --- 这一行背后的判定（``decision_record_ids``） --------------------------------


def test_a_hand_written_row_says_it_was_not_judged() -> None:
    """手工那条路径交上来的行，``decision_record_ids`` 是**空表**。

    空表在这张表上只有一个意思：「这一行不是一次判定得出的」。它是这一格能与
    「有判定支撑」分开的唯一读法 —— 而 ``RelationClaim`` 是 ``extra="forbid"`` 的，
    所以交行的人**填不了**这一格（填了整行被拒，见
    :func:`test_a_field_the_claim_does_not_have_is_rejected_rather_than_ignored`）。
    被判出来的那些行走的是另一条生产者，它写下去的 id 是当场回库核对过的。
    """
    relations = relation_store()
    dispatch([claim(relation="maintains")], relations=relations, evidence=evidence_store("e1"))

    (row,) = relations.list_relations()
    assert row.decision_record_ids == []
    assert row.evidence_ids == ["e1"], "反面：这一格没被顺手清掉 —— 有证据就是有证据"


def test_the_decision_column_lands_on_the_row_it_lines_up_with() -> None:
    """两行、两组判定 id → 各归各行（按位置对齐，不按内容认）。"""
    relations = relation_store()
    dispatch(
        [claim(relation="maintains"), claim(account_id="acct-2", relation="owns")],
        relations=relations,
        evidence=evidence_store("e1"),
        decision_record_ids=[["rec-1"], ["rec-2", "rec-3"]],
    )

    by_account = {row.account_id: row.decision_record_ids for row in relations.list_relations()}
    assert by_account == {"acct-1": ["rec-1"], "acct-2": ["rec-2", "rec-3"]}


def test_a_decision_column_that_does_not_line_up_is_refused_before_any_write() -> None:
    """两列不等长 → 抛，且表里**一行都没有**。

    错位会让一条关系的判定挂到另一条关系上，而报告上两行都正常 —— 所以它不是一次
    可以「按最短的那一列对齐」的容错。写到一半才抛同样不行：那时表里躺着一批半成品，
    而它们与一次正常的部分写入分不开。
    """
    relations = relation_store()
    with pytest.raises(StoreError) as excinfo:
        dispatch(
            [claim(relation="maintains"), claim(account_id="acct-2", relation="owns")],
            relations=relations,
            evidence=evidence_store("e1"),
            decision_record_ids=[["rec-1"]],
        )
    assert "按位置对齐" in str(excinfo.value)
    assert relations.list_relations() == ()


# --- 依据：每一条结论背后的那次判定（``remember_researched_relations``） ---------
#
# 这三段的分界是**问题不同**，不是代码不同：上面两段问「表里那一行对不对」，
# 这一段问「凭什么写它」。所以这里的断言大多是「这一行**没写**」以及「没写的那一行
# 说的是哪一条」，而正控（写下去了）每一条「不许写」旁边都有一条同形。


#: 这一件工作的主语。它是这一层要核对的两格之一（另一格是工作单元）。
ACCOUNT = "acct-1"
WORK_ITEM = "wi-1"


class _Records(DecisionRecordStore):
    """一个只装了几条回执的判定台账替身 —— 这一层只问 ``get_decision`` 那一格。

    另外三个方法留着抛，而不是各返回一个空值：一个**被用到**的方法静默返回
    ``None`` 会让「这一层读错了格子」伪装成「这件工作没有可引用范围」，
    而那条错在报告上长得像一条业务结论。

    ``reads`` 记下**问过哪几个 id**：有几条断言要说的不是「读到了什么」，
    而是「根本就没有去读」—— 一次不该发生的读在这里是一个可数的对象。
    """

    def __init__(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        self._records = dict(records)
        self.reads: list[str] = []

    def get_decision(self, record_id: str) -> Mapping[str, Any] | None:
        self.reads.append(record_id)
        return self._records.get(record_id)

    def check_scope(self, work_item_id: str, refs: Sequence[str] = ()) -> str:
        raise AssertionError("这一层不读工作的可引用范围")

    def decision_for_case(
        self, work_item_id: str, provider: str, case_id: str
    ) -> Mapping[str, Any] | None:
        raise AssertionError("这一层不按 case 查判定")

    def pinned_scene(self, work_item_id: str) -> PinnedScene | None:
        raise AssertionError("这一层不读工作钉住的 Scene 版本")

    def put_decision(self, work_item_id: str, receipt: Mapping[str, Any]) -> Mapping[str, Any]:
        raise AssertionError("这一层不写判定")


@pytest.fixture
def spec(bundle) -> DecisionSpec:
    """**真实**编译出来的责任关系题面。

    取编译出来的那一份而不是手搓一个：``_relation_choice`` 按**题面**的题号去读
    答案，而题号是 ``application/decision_specs.QUESTION_IDS`` 决定的 —— 手搓的
    题面若与它不同，这一段的每条用例都会在「读不到答案」那一步被拒，而它们各自
    要测的那一格一次都不会被执行到。

    题面不存在时**直接红**，不用 ``pytest.skip``：这份 profile 没有声明这道题时，
    §3 那条链在本部署里根本成立不了，跳过会把那件事从报告上抹掉。
    """
    compiled = compile_specs(bundle).get(DecisionTask.RESPONSIBILITY_RELATION)
    assert compiled is not None, (
        "这份 profile 没有声明 responsibility_relation 题面 —— §3 的责任关系链"
        "要求它存在，而本文件全部用例都以它为前提"
    )
    return compiled


_ABSENT = object()


def receipt(
    *,
    spec: DecisionSpec,
    record_id: str = "rec-1",
    choice: str = "maintains",
    work_item_id: str = WORK_ITEM,
    subject_ref: str = ACCOUNT,
    spec_id: str | None = None,
    outcome: str = DecisionOutcome.ANSWERED.value,
    reason: str = "",
    answers: Any = _ABSENT,
) -> dict[str, Any]:
    """一条判定回执的**库形状**：回执本身 + 这一行自己的三个事实。

    三个事实（``work_item_id`` / ``subject_ref`` / ``recorded_at``）由存储的
    ``_row`` 拼上去，不从 payload 里读 —— 它们住在列上，所以这里也摆在顶层，
    与 ``adapters/sqlite/decisions.py::_row`` 同形。摆错一层的话，这一层读到的
    是 ``None``，而 ``None != 目标`` 会把一条好记录判成「拿错了工作」。
    """
    if answers is _ABSENT:
        answers = [
            {
                "question_id": "relation",
                "kind": "choice",
                "choice": choice,
                "distribution": None,
                "confidence": 0.9,
            }
        ]
    return {
        "record_id": record_id,
        "spec_id": spec.spec_id if spec_id is None else spec_id,
        "spec_version": spec.version,
        "provider": "jev",
        "outcome": outcome,
        "reason": reason,
        "answers": answers,
        "work_item_id": work_item_id,
        "subject_ref": subject_ref,
        "recorded_at": T0.isoformat(),
    }


def relation_finding(
    relation: str = "maintains",
    decision_id: str = "rec-1",
    evidence_refs: Sequence[str] = ("e1",),
) -> RelationFinding:
    return RelationFinding(
        relation=relation, decision_id=decision_id, evidence_refs=tuple(evidence_refs)
    )


def research(
    *findings: RelationFinding,
    records: DecisionRecordStore | None,
    spec: DecisionSpec | None,
    venue_asset_id: str = "v1",
    venue_refusal: str = "",
    relations: AccountVenueRelationStore | None = None,
    evidence: InMemoryEvidenceStore | None = None,
) -> tuple[tuple[RelationOutcome, ...], AccountVenueRelationStore]:
    """跑一遍生产路径，把「逐行去向」与「表」一起交出来。"""
    table = relation_store() if relations is None else relations
    outcomes = remember_researched_relations(
        Findings(relations=tuple(findings)),
        store=table,
        evidence=evidence_store("e1") if evidence is None else evidence,
        decisions=records,
        spec=spec,
        workspace=WORKSPACE,
        account_id=ACCOUNT,
        work_item_id=WORK_ITEM,
        venue_asset_id=venue_asset_id,
        venue_refusal=venue_refusal,
        seen_at=T0,
    )
    return outcomes, table


def test_a_relation_backed_by_a_real_decision_is_written(*, spec: DecisionSpec) -> None:
    """正控：那一次判定真的在台账里、真的答的就是这个关系 → 这一行落表。

    没有这一条，下面每一条「不许写」都可以靠「这一层什么都不写」通过。
    """
    records = _Records({"rec-1": receipt(spec=spec)})
    outcomes, table = research(relation_finding("maintains"), records=records, spec=spec)

    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.WRITTEN]
    stored = table.list_relations()
    assert [(row.account_id, row.relation) for row in stored] == [
        (ACCOUNT, VenueRelation.MAINTAINS)
    ]
    assert stored[0].venue_asset_id == "v1"
    assert stored[0].decision_record_ids == ["rec-1"], (
        "这一行要说得出是哪一次判定得出的 —— 上一轮真实验收从关系表反查产生它的那次"
        "调用，只能走「证据集合恰相同 + 答案取值恰相同」，而那不是因果证明"
    )


def test_each_row_names_the_decision_that_produced_that_row(*, spec: DecisionSpec) -> None:
    """一批两条结论、两次判定 → 每一行指的都是**它自己**那一次。

    一行上写一个「本批第一次判定」的 id 也能让上面那条正控变绿 —— 而它在盘上是一条
    错得看不出来的连线（两条关系的依据被换成了同一次调用）。
    """
    records = _Records(
        {
            "rec-1": receipt(spec=spec, record_id="rec-1", choice="maintains"),
            "rec-2": receipt(spec=spec, record_id="rec-2", choice="owns"),
        }
    )
    outcomes, table = research(
        relation_finding("maintains", "rec-1"),
        relation_finding("owns", "rec-2"),
        records=records,
        spec=spec,
    )
    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.WRITTEN] * 2

    named = {
        row.relation.value: row.decision_record_ids for row in table.list_relations()
    }
    assert named == {"maintains": ["rec-1"], "owns": ["rec-2"]}


def test_a_decision_that_cannot_be_read_back_refuses_only_its_own_row(*, spec: DecisionSpec) -> None:
    """一批三条，中间那条指着一个不存在的判定 → **只有它**没写，序号还是它的。

    序号是这一条真正的判据：那一行失败之后，第三条的去向必须仍报 ``2`` ——
    报成 ``1`` 的话，读的人会拿着这个号去查另一行，而那一行是写下去了的。
    """
    records = _Records({"rec-1": receipt(spec=spec, record_id="rec-1", choice="owns")})
    outcomes, table = research(
        relation_finding("owns", "rec-1"),
        relation_finding("maintains", "rec-does-not-exist"),
        relation_finding("booking_control", "rec-3"),
        records=records,
        spec=spec,
    )
    assert [outcome.ordinal for outcome in outcomes] == [0, 1, 2]
    assert [outcome.disposition for outcome in outcomes] == [
        RelationDisposition.WRITTEN,
        RelationDisposition.REJECTED,
        RelationDisposition.REJECTED,
    ]
    assert "rec-does-not-exist" in outcomes[1].reason
    assert "解不开" in outcomes[1].reason
    # 第三行是**另一次**读：它指的那个 id 也不在台账里，而与第二条无关。
    assert records.reads == ["rec-1", "rec-does-not-exist", "rec-3"]
    assert [row.relation for row in table.list_relations()] == [VenueRelation.OWNS]


@pytest.mark.parametrize(
    ("field", "value", "what"),
    [
        ("work_item_id", "wi-other", "不是为这一件工作做的"),
        ("subject_ref", "acct-2", "的主语是"),
        ("spec_id", "page-triage-v1", "不是责任关系那道题"),
    ],
)
def test_a_decision_about_another_work_subject_or_question_refuses_the_row(
    *, spec: DecisionSpec, field: str, value: str, what: str
) -> None:
    """三格错位的判定的处置**都是不写**，而理由各自点名是哪一格错。

    合成一句「依据不成立」会让三种处置看起来一样 —— 而它们指向三个不同的地方：
    去核对派发（工作）、去核对账号（主语）、去翻题面（问题）。
    """
    records = _Records({"rec-1": receipt(spec=spec, **{field: value})})
    outcomes, table = research(relation_finding("maintains"), records=records, spec=spec)

    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED]
    assert what in outcomes[0].reason
    assert table.list_relations() == ()


@pytest.mark.parametrize(
    ("outcome_value", "what"),
    [
        (DecisionOutcome.ABSTAINED.value, "没有给出结论"),
        (DecisionOutcome.UNAVAILABLE.value, "没有给出结论"),
    ],
)
def test_an_abstention_is_a_settlement_but_not_a_relation_conclusion(
    *, spec: DecisionSpec, outcome_value: str, what: str
) -> None:
    """弃权与缺容量都是一次**真的发生过**的判定，而它们不是一条关系结论。

    它们各自是诚实的结局，处置是重判或把后端接上 —— 而从它们推出一个取值，
    表里那一行会以「判定得出的」身份存在，判定却什么都没说。
    """
    records = _Records(
        {"rec-1": receipt(spec=spec, outcome=outcome_value, reason="后端只有英文权重")}
    )
    outcomes, table = research(relation_finding("maintains"), records=records, spec=spec)
    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED]
    assert what in outcomes[0].reason
    assert "后端只有英文权重" in outcomes[0].reason
    assert table.list_relations() == ()


def test_a_decision_that_named_a_different_relation_refuses_the_row(*, spec: DecisionSpec) -> None:
    """判定答的是 ``maintains``，而这一行写的是 ``owns`` —— 不写。

    这是整条判据的落点：答案与结论对不上时，表里那一行会以「判定得出的」身份
    存在，而判定说的是另一件事，两者在盘上留下的记录同形。
    """
    records = _Records({"rec-1": receipt(spec=spec, choice="maintains")})
    outcomes, table = research(relation_finding("owns"), records=records, spec=spec)

    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED]
    assert "'maintains'" in outcomes[0].reason and "'owns'" in outcomes[0].reason
    assert table.list_relations() == ()


def test_the_sixth_member_is_refused_by_the_grounding_not_by_the_table(
    *, spec: DecisionSpec
) -> None:
    """``unresolved`` 不在题面的五个选项里，所以它连一次比较都对不上 —— 拒在依据那一层。

    这一条钉住的是**拒在哪一层**：``remember_relations`` 也拒绝闭集外的取值，
    但那时表已经被读过一遍（证据引用核对先发生），而这里的问题不是那一行写不写得下去，
    是**它背后的判定根本没出过这个选项**。两层的理由落点不同，读的人该去的地方也不同。
    """
    records = _Records({"rec-1": receipt(spec=spec, choice="maintains")})
    outcomes, table = research(relation_finding("unresolved"), records=records, spec=spec)

    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED]
    assert "答的不是这个关系" in outcomes[0].reason
    # 而**不是** ``remember_relations`` 那句「不在闭集里」—— 那一条要到表那一层才发作。
    assert "闭集" not in outcomes[0].reason
    assert table.list_relations() == ()


def test_an_answer_that_is_not_a_choice_refuses_the_row(*, spec: DecisionSpec) -> None:
    """回答的键是对的，但它的形状不是选择题 —— 也拒。

    这一格在**真**记录上到不了（``compile_specs`` 编译出来的那道题是 ``CHOICE``），
    所以它靠这里的一条替身记录被执行 —— 与 ``test_mcp_decision_tools`` 里
    ``_answer_view`` 那条同一个理由：够不着的分支没有执行器，而它恰好是最容易被
    顺手折平的一格（把是否题的概率读成一个选项的得分）。
    """
    records = _Records(
        {
            "rec-1": receipt(
                spec=spec,
                answers=[{"question_id": "relation", "kind": "yes_no", "probability": 0.8}],
            )
        }
    )
    outcomes, _ = research(relation_finding("maintains"), records=records, spec=spec)
    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED]
    assert "不是一个选择题答案" in outcomes[0].reason


def test_an_answer_about_another_question_is_not_read_as_this_one(*, spec: DecisionSpec) -> None:
    """记录里只有 ``band`` 那一题的回答 → 读不到 ``relation``，拒。

    按题号读而不是按「第一条回答」读，是这一格的全部内容：两条记录在盘上同形，
    而按位置读的那一份会把页面档位当成责任关系。
    """
    records = _Records(
        {
            "rec-1": receipt(
                spec=spec,
                answers=[
                    {"question_id": "band", "kind": "choice", "choice": "A", "confidence": 0.9}
                ],
            )
        }
    )
    outcomes, _ = research(relation_finding("maintains"), records=records, spec=spec)
    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED]
    assert "没有关于 'relation' 的回答" in outcomes[0].reason


def test_a_production_without_relations_asks_the_ledger_nothing() -> None:
    """空产出 → 空去向，且**一次都没去读台账**（台账可以是没接的那一个）。

    与 ``require_evidence_refs`` 的「只有缺口就不查库」同一条：让一趟无关的读决定
    这次调用成不成功，会把「本部署没接判定台账」变成一个与这份产出内容无关的拒绝理由。
    """
    records = _Records({})
    outcomes, table = research(records=records, spec=None)
    assert outcomes == ()
    assert records.reads == []
    assert table.list_relations() == ()


def test_a_profile_without_the_question_refuses_every_row_and_names_it(
    *, spec: DecisionSpec
) -> None:
    """题面没声明（``spec=None``）→ 每一行各自得到同一条理由，**且一条都没去读**。

    「这一批一行都不写」与「整批失败」是两句话：回执里仍然逐行报出来，而行数
    与产出对得上。写成抛异常的话，一次配置缺失会看起来像一次产出有问题。
    """
    records = _Records({"rec-1": receipt(spec=spec)})
    outcomes, table = research(
        relation_finding("maintains", "rec-1"),
        relation_finding("owns", "rec-2"),
        records=records,
        spec=None,
    )
    assert [outcome.ordinal for outcome in outcomes] == [0, 1]
    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED] * 2
    assert all("题面" in outcome.reason for outcome in outcomes)
    assert records.reads == []
    assert table.list_relations() == ()


def test_an_unwired_ledger_refuses_every_row_and_says_which_wiring_is_missing(
    *, spec: DecisionSpec
) -> None:
    """台账没接 → 同一条理由逐行，且理由说的是**接线事实**而不是「这块场地没有负责方」。

    这两件事的处置指向完全相反的方向（去组合根接线 / 去研究），而它们在报告上
    都会表现为「一行都没写」。
    """
    outcomes, table = research(
        relation_finding("maintains"), records=None, spec=spec, venue_asset_id="v1"
    )
    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED]
    assert "判定台账" in outcomes[0].reason
    assert table.list_relations() == ()


def test_a_venue_that_cannot_be_named_refuses_every_row_before_any_lookup(
    *, spec: DecisionSpec
) -> None:
    """场地说不出来 → 一行都不写，且**没读过台账**。

    读一次台账再拒，会让「场地说不出来」与「判定不成立」在同一份回执里都表现为
    一条 ``REJECTED`` —— 而前者的处置是去修派生那一步，根本不用重判。
    """
    records = _Records({"rec-1": receipt(spec=spec)})
    outcomes, table = research(
        relation_finding("maintains"),
        records=records,
        spec=spec,
        venue_asset_id="",
        venue_refusal="这一批的来源回执是按 discovery_scope 编译的，里面没有场地那一行。",
    )
    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED]
    assert "场地" in outcomes[0].reason
    assert "discovery_scope" in outcomes[0].reason
    assert records.reads == []
    assert table.list_relations() == ()


def test_without_a_reason_the_generic_one_still_stands(*, spec: DecisionSpec) -> None:
    """调用方说不出场地为什么没派生出来时，那条通用理由照样成立 —— 它是实话。

    这一格是 ``venue_refusal`` 不是绕过开关的另一半：它**只**能让那句话更具体，
    换掉的是措辞，不是判词。
    """
    outcomes, _ = research(
        relation_finding("maintains"), records=_Records({}), spec=spec, venue_asset_id=""
    )
    assert [outcome.disposition for outcome in outcomes] == [RelationDisposition.REJECTED]
    assert "这一件工作研究的场地" in outcomes[0].reason

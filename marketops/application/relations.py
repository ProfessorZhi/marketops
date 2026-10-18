"""责任关系表：**一批判定怎么落进表里**（生产者），以及**一次新的判定怎么折进已有的
那一行**（合并规则）。

## 两份工作为什么住在同一个模块里

与 ``application/pool.py`` 逐字同一条：它们说的是同一件东西的两半 ——「这一行的哪些
格子由生产者填」与「同一个键上的两行怎么折」。分开写会让那两张清单各改各的，而它们
必须一起改：一个只由生产者填、合并规则却不认识的字段，第二次写入时传不进去（合并
结果取的是已有的那一行），于是那一格会永远停在这一行第一次落进池子时的样子，而报告
上那只是「它一直没变」。

## 与资产池共用哪些、不共用哪些

折的方向与资产池是同一条（``application/pool.py`` 的第一句话）：**后看到的为准；
但「看到空的」不是一次观察。** 两张表差的不是这句话，而是**哪些格子需要这句话**：

* 关系表上没有「上游说的一句话」这类格子 —— 每一格都是我们自己的判定或时刻；
* 关系表上没有**两个判定共用一个槽位**的地方（见下面「没有冲突这一支」）。

两条原语（``later_of`` / ``ordered_union``）与资产池逐字相同，所以它们住在
``application/merge.py`` 里，两个模块都调那一个定义处。

## 三条规则

1. ``first_seen_at`` 只往**早**走（``min``）。它是「我们第一次记下这块场地由谁负责」，
   而重放一次旧判定不该把它推后 —— 推后之后，表会声称一条早就知道的关系是刚发现的。
2. ``last_verified_at`` 只往**晚**走（``None`` 不参与，见
   :func:`~marketops.application.merge.later_of`）。它与 ``Observation.observed_at``
   同一条纪律：读一遍本地记录**不是**一次观察，所以这个字段只由真的重看过的那一步填。
3. ``evidence_ids`` 取**并集**（保序、去重）。第二次独立看到同一件事是**加强**，不是
   重复；而证据行本身在证据库里只增不改，所以并集是「这些行都关于这一条关系」。
4. ``decision_record_ids`` 取**并集**（保序、去重），理由与第 3 条同一条：一条关系
   被再判一次是加强，而不是把上一次的判定作废。只留最新那一个 id 会让**更早那次
   调用从结果上反查不到**，而它带进来的证据还在这一行的第 3 条并集里 —— 那一行于是
   挂着一条无主的证据。

## 没有冲突这一支，而这不是漏掉的

资产池那条 :class:`~marketops.domain.errors.AssetConflictError` 挡的是「**一个槽位、
两个判定**」（两个 ``confirmed`` 配了两个不同的域名）。关系表上那个槽位**不在同一行
里**：``relation`` 是键的一段，所以「这块场地是 owns 还是 maintains」不是一次合并能
遇到的问题 —— 它是**两条行**，两条都要在（§5 要求同一个 Asset 可以同时有
``owner`` / ``operator`` / ``maintainer`` / …，不强制单选）。于是这里没有可以挑一个的
地方，也就没有可抛的冲突。

## 两条生产者**不是**一回事，也不合并

本模块有两条写关系的路，它们共用的只有 ``AccountVenueRelation`` 与合并规则：

* :func:`remember_relations` —— 一批**判定行**怎么落进表里。调用方给的是
  ``{account_id, relation, evidence_ids}``：**关系就是它说的那个**。
* :func:`remember_researched_relations` —— 一次**研究**怎么落进表里。调用方给的是
  一份 ``Findings``：每条关系结论还带一个 ``decision_id``，而这一层会去把那条判定
  读回来，核对它是不是这一件工作的、答的是不是这个关系。核对不过的行**不写**。

合并成一条（加一个「有没有 decision_id」的参数）会让「关系由谁定的」变成**调用点
可以选的东西**，而选中的那一天没有一处会红：一条凭印象连起来的关系会以与一条判定
得出的关系完全相同的形状落表。§3 点名禁止的正是那件事（看到官网就默认 owner）。

## 它不判断的事（写下来，不是忘掉）

* **不判断哪个关系更接近事实。** 本层没有来源可信度的判据，所以按第 1、2 条处理时刻，
  证据按第 3 条累积。缺的是判据，不是一行代码。
* **不动四个键。** 折进来的对象与库里那一行必须说的是同一块场地、同一个账号、同一个
  工作区、同一种关系，否则是调用方拿错了一个对象（``StoreError``）—— 照折的话，一次
  写错的键会把**另一条**关系整个改写掉，而报告上它看起来只是那条关系的信息变了。
* **不撤销。** 一条关系被推翻（复核时发现这个组织其实不负责这块场地）在本层**表达不
  出来**：那一格是「它曾经成立、现在不成立了」，要有它自己的凭据，而本仓今天没有这一
  格。盘上因此分不开「一条还没被重新证实的关系」与「一条已经被推翻的关系」—— 两者的
  下一步不同（一个是去复核，一个是去撤下），所以这一格属于 §11 的新鲜度，与
  「哪一行是按哪一版规则判的」落在同一处（``docs/DATA_MODEL.md`` 记了那条边界）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import ValidationError

from ..domain.base import StrictModel
from ..domain.entities import AccountVenueRelation
from ..domain.enums import DecisionOutcome
from ..domain.errors import ConfigError, StoreError
from ..domain.findings import Findings, RelationFinding
from ..ports.decisions import DecisionRecordStore, DecisionSpec
from ..ports.pool import AccountVenueRelationStore
from ..ports.store import EvidenceStore
from .evidence_refs import listed_unknown_refs
from .merge import later_of, ordered_union
from .validation import as_text, one_line_reason

__all__ = [
    "RelationClaim",
    "RelationDisposition",
    "RelationOutcome",
    "merge_relations",
    "remember_relations",
    "remember_researched_relations",
]


def merge_relations(
    existing: AccountVenueRelation | None, incoming: AccountVenueRelation
) -> AccountVenueRelation:
    """把 ``incoming`` 折进 ``existing``；``existing is None`` 就是它本身。

    返回值**重新过了一遍模型校验**（``model_validate`` 而不是 ``model_copy``）：
    ``model_copy(update=...)`` 不跑校验器，而 ``AccountVenueRelation`` 上
    ``_a_relation_without_evidence_is_not_written`` 是「一条关系至少要一条证据」的
    **唯一**执行器 —— 合并出来的行绕开它，正是「一条空证据的关系被写进表里」的路径
    （它在下游读起来是「我们查到了」）。

    纯函数：不读盘、不写盘、不碰时钟。所以这一条规则可以在没有库、没有配置的情况下被
    逐条测出来（``tests/test_application_relations.py``）。
    """
    if existing is None:
        return incoming
    if existing.relation_key() != incoming.relation_key():
        raise StoreError(
            f"要折进来的是一条关系 {incoming.relation_key()}，而表里那一行是 "
            f"{existing.relation_key()}。\n"
            "四段里有一段不同，说的就是不同的东西，所以这不是一次合并 —— 调用方拿错了"
            "对象。\n"
            "照折的话，一次写错的键会把另一条关系整个改写掉，而报告上它看起来只是那条"
            "关系的信息变了。"
        )

    changes: dict[str, object] = {
        # 只往早走 / 只往晚走：见模块 docstring 的第 1、2 条。
        "first_seen_at": min(existing.first_seen_at, incoming.first_seen_at),
        "last_verified_at": later_of(existing.last_verified_at, incoming.last_verified_at),
        # 证据取并集、保序：已有的在前，新来的按顺序接上（第 3 条）。
        "evidence_ids": ordered_union(existing.evidence_ids, incoming.evidence_ids),
        # 判定 id 同理（第 4 条）：一条关系被再判一次时，两次调用都指得出来。
        "decision_record_ids": ordered_union(
            existing.decision_record_ids, incoming.decision_record_ids
        ),
    }
    return AccountVenueRelation.model_validate({**existing.model_dump(), **changes})


# ----------------------------------------------------------------------
# 生产者：一批判定怎么落进表里
# ----------------------------------------------------------------------
class RelationDisposition(str, Enum):
    """一次 :func:`remember_relations` 里，**每一行**落到哪里去了。

    四种，闭集。它**不是** ``domain/receipt.py::Disposition`` 的复用，两者各有一个
    成员恰好也叫「归并」而含义不同：那边说的是「母池的一份导出里这一原始行读成了
    什么」（可研究 / 归并 / 排除 / 待补全 / 读失败），这边说的是「一条责任判定有没有
    写进表里」。共用一套取值会让任何一边加成员时另一边跟着改，而它们没有共同的
    下游 —— 那正是本仓「两处写同一句话就是缺陷」要防的形状。

    ``WRITTEN`` 与 ``FOLDED`` 分开，是因为它们**都落盘了**却不是同一件事：一批里
    两行说了同一条关系时，表里只有一行，而「这次调用写了 4 行」与「这个市场多了
    4 条责任」是两句不同的话。把折叠藏进 ``WRITTEN`` 里，读的人会从一个行数里
    得出一个关系数。
    """

    WRITTEN = "written"
    #: 这一行与**同一批里**前面的某一行说的是同一条关系，两条折成了一条（证据取并集）。
    FOLDED = "folded"
    #: 这一行有证据引用，而那些引用在这个库里解不开 —— 一行都没写。
    EVIDENCE_UNKNOWN = "evidence_unknown"
    #: 这一行**没写**。两种到达方式，合并在这里是因为处置是同一个（回到产出那一侧
    #: 重判）：它本身构造不出一个能写下来的关系（关系不在闭集里、账号 id 不规范、
    #: 一条证据都没有……），或者它的**依据不成立**（见
    #: :func:`remember_researched_relations`：判定解不开、不是这一件工作的、没给出
    #: 结论、答的不是这个关系……）。
    #:
    #: 依据不成立的那一支**不**另设一个成员：本枚举说的是「这一行落到哪去了」，
    #: 而它们落到的是同一个地方（没写、要重判）。理由里那一句才是分辨它们的读法 ——
    #: 为一个「谁的问题」的细分加一个成员，会让 ``by_disposition`` 那几个计数在
    #: 每一个只走手工路的工具里多出一个恒为 0 的格子。
    REJECTED = "rejected"


class RelationClaim(StrictModel):
    """调用方交进来的一条责任判定。**它还不是一行关系** —— 这是它存在的全部理由。

    ``relation`` 是**原文**（``str``）而不是 :class:`~marketops.domain.enums.VenueRelation`：
    取值不在闭集里是**一行**的去向（``REJECTED``，理由里点名可用取值），而在更早的
    地方把它当类型收下，意味着一行写错了枚举会把整次调用炸掉 —— 同一批里其余的判定
    与它无关。

    ## 三个字段都有缺省，而这不是「可以省」

    缺省值让一个**空字典**也是一条能构造出来的判定：它会走到「账号 id 不规范」那条
    拒绝上去，理由里点名 ``account_id``。换成必填字段，缺一个键就变成一个
    ``ValidationError``，而那条错误要说的是「这一行构造不出来」—— 它说不清缺的是
    哪一格的**业务**后果（一条不知道是谁的责任关系，写进表里就是一条无主的关系）。
    两种处置指向的都是同一件事（这一行不写），但第二种把「你少给了一个键」与
    「你给的账号名不规范」说成了同一句。

    ``evidence_ids`` 是**指针**（``Evidence.evidence_id`` 同空间）。这里的 ``tuple``
    只保证形状：那一条「至少一条证据」的判据住在
    :meth:`~marketops.domain.entities.AccountVenueRelation._a_relation_without_evidence_is_not_written`，
    这里不再写一遍 —— 两处各写一遍，先漂的那一处正是唯一被读取的那一处。
    """

    account_id: str = ""
    relation: str = ""
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RelationOutcome:
    """一条判定交上去之后的去向。

    ``row`` 是**表里现在那一行**（``upsert_relation`` 的返回值），不是交上去的那一行：
    第二次折同一条关系之后，证据是并集、``first_seen_at`` 是更早的那个 —— 报出交上去
    的那一份，读的人会以为表里就是那样。``None`` 只有一个意思：这一行没写进去
    （``EVIDENCE_UNKNOWN`` / ``REJECTED``）。

    ``account_id`` / ``relation`` 在没写进去时是**调用方交来的原文**，只用于对账
    （「我交的第 3 行为什么没进去」）。写进去的那两格以 ``row`` 为准。
    """

    ordinal: int
    account_id: str
    relation: str
    disposition: RelationDisposition
    row: AccountVenueRelation | None = None
    #: 没写进去时的理由；也用来记「折进了第几行」。``WRITTEN`` 时是空串。
    reason: str = ""


def remember_relations(
    rows: Sequence[Mapping[str, Any]],
    *,
    store: AccountVenueRelationStore,
    evidence: EvidenceStore,
    workspace: str,
    venue_asset_id: str,
    seen_at: datetime,
    decision_record_ids: Sequence[Sequence[str]] | None = None,
) -> tuple[RelationOutcome, ...]:
    """一批责任判定 → 表里那几行，**逐行给出去向**。

    ## 一次调用说的是**一块场地**上的责任

    ``venue_asset_id`` 是参数而不是每一行里的一个字段：§5 要的是「这块场地由谁负责」，
    而一次研究的产出就是「我刚看过这块场地，找到了这几个负责方」。让每一行自己带
    场地 id，等于允许一次调用把行写进任意几块场地，而报告上那是一张平平的关系表 ——
    「这一行是关于哪块场地的」没有一处能出错的地方，也就没有一处能发现写错了。

    ## 逐行判，一行失败不牵连其余

    §5 与 ``application/outreach.py::_as_draft`` 是同一条：一行坏判定不该让整批
    都不做。所以这一层的**每一种失败都是一条去向**，不是异常 —— 除了
    :class:`~marketops.domain.errors.StoreError`：那个说的是存储本身（这一行属于别的
    工作区、键绑不上），它一次就不是「这一行的问题」，让它穿出去由调用点处置。

    ## 去重、折叠，都走**同一条**合并规则

    一行的证据先按出现顺序去重（同一条证据写两遍不是两次独立看到），随后
    ``upsert_relation`` 自己折 —— 同一批里两行说了同一条关系时，第二次拿到的
    ``existing`` 就是第一次写进去的那一行，证据按 ``merge_relations`` 的第 3 条取
    并集。这里**不**预先合并、也**不**先查一次表：另一套合并规则就是第二个定义处，
    而两份合并规则迟早对同一对行给出两个结果。

    ``FOLDED`` 因此是一次**报告**，不是一次处置：这一行与同一批里某一个更早的序号
    说的是同一条关系，那个序号写在它的 ``reason`` 里。

    ## 它不填的格子，与不填的理由

    * ``last_verified_at`` 留 ``None``。记下一条判定**不是**重新证实了这条关系
      （与 ``assets_from_receipt`` 不填 ``last_verified_at`` 同一条纪律）。填上它，
      「这条关系多久没复核了」这个只在表建起来之后才有意义的问句，会在第一次写入
      那天就得到「刚刚复核过」，而下一轮维护会把它们整批跳过。
    * ``first_seen_at`` 取 ``seen_at``（参数，不是时钟）：它是「我们**何时**第一次
      记下这条关系」，由调用方定义的那一刻给出 —— 一条要能被逐条断言的规则不该
      自己去问现在几点。
    * ``workspace`` 取参数（工具层给本部署的 profile），不从行里读：一个能填
      workspace 的入参等于让一次调用把行写进另一个 Scene，而两张表在同一个库文件里。
      §5 的「同一块场地与同一个账号在两个 Scene 下可以判出不同的关系」说的是两个
      Scene **各自**写自己那一行，不是一次调用替另一个 Scene 写。

    ## 它判断不了的事

    **它不知道这块场地在不在池子里。** 那是 ``VenueAssetStore.get_asset`` 的事，由
    工具层在调用本函数**之前**问 —— 这一步在这里做的话，本函数要么多收一个端口
    （而这个端口与它判的东西无关），要么把「查不到」也做成一条逐行的去向，而那时
    整批的行都会得到同一个理由。

    ## ``decision_record_ids`` 为什么是参数，而**不在** ``RelationClaim`` 上

    它是**每一行**背后那些判定的 id（按 ``rows`` 的位置对齐，见下）。放进
    ``RelationClaim`` 会多出一个能力：交一批判定行的人可以自己声明「这一行是某一次
    判定得出的」—— 而这一层**没有判定台账**，它核不了（``RelationClaim`` 是
    ``extra="forbid"`` 的，所以今天那个键连传都传不进来）。声明不了但写得下的那一格，
    正是 §3 点名禁止的形状：一条凭印象连起来的关系，落表之后与一条判定得出的关系
    完全同形。所以承载它的是一个**只由本模块的另一条生产者填**的参数。

    ``None``（缺省）= 这一批没有任何一行有判定支撑，写下来的行如实带空表 —— 手工那条
    路径（``remember_responsibility``）就是这一种。给了值时必须与 ``rows`` **等长**：
    错位会让一条关系的依据挂到另一条关系上，而报告上两行都正常。长度对不上是调用方
    的编程错误（本模块内部两个调用点自己造的），所以当场抛，且**在写任何一行之前**抛
    —— 写到一半才抛会留下一批半成品，而它们与一次正常的部分写入分不开。
    """
    if decision_record_ids is not None and len(decision_record_ids) != len(rows):
        raise StoreError(
            f"判定 id 那一列有 {len(decision_record_ids)} 组，而交上来的判定行有 "
            f"{len(rows)} 行 —— 两列按位置对齐，错位会把一条关系的判定挂到另一条关系上，"
            "而报告上两行都正常。\n"
            "这是调用方拼错了列（本模块内部的两个调用点各造各的），所以一行都不写。"
        )

    outcomes: list[RelationOutcome] = []
    #: 这一批里**第一次**出现某个四段键的那一行序号。键用结构形态（元组）而不是
    #: ``relation_key()`` 那个串：拼串会把两个不同的键拼成同一个串，而这是字典键。
    first_row_at: dict[tuple[str, str, str, str], int] = {}

    for ordinal, raw in enumerate(rows):
        try:
            claim = RelationClaim.model_validate(raw)
        except ValidationError as exc:
            outcomes.append(
                RelationOutcome(
                    ordinal=ordinal,
                    account_id=as_text(raw.get("account_id")),
                    relation=as_text(raw.get("relation")),
                    disposition=RelationDisposition.REJECTED,
                    reason=f"这一行不是一个判定：{one_line_reason(exc)}",
                )
            )
            continue

        try:
            row = AccountVenueRelation(
                workspace=workspace,
                account_id=claim.account_id,
                venue_asset_id=venue_asset_id,
                relation=claim.relation,
                # 同一条证据写两遍不是两次独立看到（``dict.fromkeys`` 保序去重）。
                evidence_ids=list(dict.fromkeys(claim.evidence_ids)),
                # 这一行背后那几次判定。``None`` 是「这一批没有一行有判定支撑」，
                # 而 ``()`` 是某一行自己那一组是空的 —— 两者的行长得一样，但只有
                # 前者的调用方被允许省略（见本函数的 docstring）。
                decision_record_ids=list(
                    () if decision_record_ids is None else decision_record_ids[ordinal]
                ),
                first_seen_at=seen_at,
            )
        except ValidationError as exc:
            outcomes.append(
                RelationOutcome(
                    ordinal=ordinal,
                    account_id=claim.account_id,
                    relation=claim.relation,
                    disposition=RelationDisposition.REJECTED,
                    reason=f"这一行不是一个能写下来的关系：{one_line_reason(exc)}",
                )
            )
            continue

        unknown = [
            ref for ref in row.evidence_ids if evidence.get_evidence(ref) is None
        ]
        if unknown:
            outcomes.append(
                RelationOutcome(
                    ordinal=ordinal,
                    account_id=claim.account_id,
                    relation=claim.relation,
                    disposition=RelationDisposition.EVIDENCE_UNKNOWN,
                    reason=(
                        f"这些证据引用在这个库里解不开：{listed_unknown_refs(unknown)}。"
                        "一条关系挂的依据必须是**已经落盘**的材料 —— 引用一条不存在的"
                        "证据，与一条没有依据的关系是同一件事，只是它看起来像有依据。"
                        "这一行不写；同一批里其余的行不受影响。"
                    ),
                )
            )
            continue

        stored = store.upsert_relation(row)
        earlier = first_row_at.get(row.relation_key_parts())
        if earlier is None:
            first_row_at[row.relation_key_parts()] = ordinal
            outcomes.append(
                RelationOutcome(
                    ordinal=ordinal,
                    account_id=stored.account_id,
                    relation=stored.relation.value,
                    disposition=RelationDisposition.WRITTEN,
                    row=stored,
                )
            )
            continue
        outcomes.append(
            RelationOutcome(
                ordinal=ordinal,
                account_id=stored.account_id,
                relation=stored.relation.value,
                disposition=RelationDisposition.FOLDED,
                row=stored,
                reason=(
                    f"同一批里第 {earlier} 行已经说过这条关系，两条合成了一条"
                    "（证据取并集）—— 表里仍然只有一行。"
                ),
            )
        )

    return tuple(outcomes)


# ----------------------------------------------------------------------
# 生产者：一次研究的责任结论怎么落进表里
# ----------------------------------------------------------------------
def remember_researched_relations(
    findings: Findings,
    *,
    store: AccountVenueRelationStore,
    evidence: EvidenceStore,
    decisions: DecisionRecordStore | None,
    spec: DecisionSpec | None,
    workspace: str,
    account_id: str,
    work_item_id: str,
    venue_asset_id: str,
    venue_refusal: str = "",
    seen_at: datetime,
) -> tuple[RelationOutcome, ...]:
    """一次研究的责任结论 → 表里那几行，**每条结论先过它的判定**。

    ## 它比 :func:`remember_relations` 多做的那件事

    ``findings.relations`` 里的每一条都带 ``decision_id``（领域层强制，见
    :class:`~marketops.domain.findings.RelationFinding`），而那条判定要**真的存在、
    真的关于这一件工作、真的答的就是这个关系**。核对不过的那一行不写。

    这不是一次额外的检查，它是 §3 那两句话的执行器：「Jev 必须真正被真实 Research
    路径消费」与「禁止看到官网就默认 owner」。少了它，``decision_id`` 只是一个
    必填字符串 —— 研究员随手写一个 canonical id 就能让一条凭印象的关系落表，而它
    与一条判定得出的关系在盘上完全同形。

    ## 五条判据，每一条对应一个真实的错法

    1. **记录解得开**（``get_decision`` 不是 ``None``）—— 指一个不存在的 id 是最
       省事的伪造。
    2. **``work_item_id`` 是这一件**：一条在**另一个账号**的工作里做出的判定，不能
       给这一件工作当依据。回执里有这一格（``_row`` 的三个行事实之一），所以这一条
       是直接比出来的，不是推的。
    3. **``subject_ref`` 是这个账号**：与第 2 条不同 —— 同一件工作里，主语只有一个，
       而这一条问的是「这条判定是不是关于**我们要写的那个人/组织**」。两格都留着，
       因为它们在存储里是两个不同的键，而合成一句会让「拿错了工作」与「拿错了主体」
       的处置看起来一样（前者去核对 attempt，后者去核对账号）。
    4. **``spec_id`` 是责任关系那道题**：页档位与角色口径的判定读起来也是一条正常
       记录，而拿它当责任关系的依据，等于把「这一页是几档」当成了「谁负责」。
    5. **答案是 ``answered`` 且 choice 等于声称的 ``relation``**：弃权（``ABSTAINED``
       / ``UNAVAILABLE``）是一个诚实的结局，但它**不是**一个关系结论 —— 从它推出
       任何取值都是我们自己补的。答案与声称不一致更直接：那说明这一行写错了。

    第 5 条里 ``choice == finding.relation`` 那一步是这套判据的落点，也是 §3 那五
    个可判取值之所以在闭集里的原因（``application/decision_specs.py`` 的
    ``CLASSIFIABLE_RELATIONS``）：题面出的是那五个，所以答案只可能是那五个之一，
    而声称的取值不在那五个里时它必然对不上 —— 不需要在这里再判一次闭集。

    ## 一条前提：**这块场地要说得出来**

    ``venue_asset_id`` 为空时不写任何一行：一条责任关系说的是「谁对**哪块**场地
    负责」，场地不知道的时候写下来的那一行，在任何一份按场地读的报告里都出现不了
    （与 ``product_loop`` 那一条同一条理由）。研究面上这个值来自
    ``promotion.venue_handle_for_account``（按回执重新派生），派生不出来就是空串 ——
    而空串在这里是一条**每条都拒**的理由，不是一次静默的跳过。

    ## ``venue_refusal`` 为什么是一格参数，而它**不是**一个旁路开关

    场地派生不出来有四种到达方式，处置各不相同（这一份回执不是当初编译这件工作的
    那一份 / 这一行不是 ``resolved`` / 这一类输入的主体不是从回执来的 / 池子里没有
    这块场地），而**只有调用方知道是哪一种**：它手里有作业、有回执、有资产池，这一
    层没有。所以调用方把那条理由交进来，这里照它拒每一行。

    这一格**加不进一条写不下去的行**：它只在 ``venue_asset_id`` 为空时被读，而那时
    每一行本来就已经是 ``REJECTED`` —— 它换掉的只是那句话的**具体程度**。反方向的
    参数（一个能免掉某条判据的开关）才是本仓要防的那种东西，见模块 docstring 里
    「两条生产者不是一回事」那一段。

    调用方两种都不给时，下面那条通用理由照样成立（它说的是实话：这个值本该来自
    这一件工作研究的场地）。

    ``work_item_id`` / ``account_id`` 由调用方从**工作单元**读（不是从 ``findings``
    里读）：产出是待核对的那些东西，拿它自己声称的主语去核对它自己，等于没查。
    同一条纪律见 ``application/evidence_refs.require_evidence_refs``。

    ## 逐行判，一行失败不牵连其余

    与 :func:`remember_relations` 同一条（§5）。前四条判据里任何一条不过，**那一行**
    得到一条 ``REJECTED``，其余照走。整批共用的前提（接线、场地、题面）不成立时
    每一行各自得到同一条理由 —— 那不是「整批失败」，而是每一行都写不下来，而回执
    里仍然逐行报出来。

    ## 它多写的那一格：判定的 id 落到**行上**

    上面五条判据是写入**当时**的一次核对 —— 核对完了，那条记录与这一行的连线在盘上
    就没有痕迹了。上一轮真实验收量到的正是这件事：从关系表反查产生它的那次调用，
    只能走「证据集合恰相同 + 答案取值恰相同」，而那**不是**因果证明（同一个账号对
    同一批页面可以问出两个不同的问题，两条判定引用同一批证据、给出同一个取值）。

    所以核对过的那个 id 跟着行一起落盘（``AccountVenueRelation.decision_record_ids``），
    反查从此是一次**按主键的读**：这一行说得出是哪几次调用产生的，而那些 id 都能用
    ``DecisionRecordStore.get_decision`` 读回来，读回来的记录自己带着工作单元、
    题面与版本。工作单元与 ``spec_version`` **不**再抄一份到行上：抄一份就是那句话
    的第二个定义处，而两次抄写迟早说的不是同一次调用 —— 它们本来就是那条记录自己的
    列（``_row`` 的三个行事实），按 id 读得到。

    ## 它不填的格子

    与 :func:`remember_relations` 逐字相同（``first_seen_at`` 取 ``seen_at``、
    ``last_verified_at`` 留空、``workspace`` 取参数）。这一层不重写那三条 ——
    底下那次调用会把它们做掉。手工那条路径（``remember_responsibility``）写下来的
    行则带着**空**的 ``decision_record_ids``：它没有判定台账，也不该去借一个 —
    §3 要求的是关系由真实判定支撑，而一条由人写下来的关系如实说「我不是判出来的」，
    比它自称判过要好。

    ## 判定的 ``spec_version`` 为什么不在这里比

    题面改版之后，旧判定是按**上一版**题面做的，而这一行仍然要写下来：它是一次
    真的发生过的判定。版本没有被丢掉，它跟着 ``decision_id`` 走 —— 那条记录里有
    ``spec_id`` / ``spec_version`` / 输入哈希 / 后端，而这一行与它的连线就是行上那
    一格 ``decision_record_ids``（上面那一段）。在这里比版本会把「判过的」变成
    「用当前这一版判过的」，那是另一个问题（§11 的重判，
    ``AccountVenueRelation`` 的 docstring 里记着为什么那一格今天不存在）。
    """
    researched = findings.relations
    if not researched:
        # 这一次产出没有责任结论。一次查库都不做 —— 与 ``require_evidence_refs``
        # 的「只有缺口就不查库」同一条：让一趟无关的读来决定这次调用成不成功，
        # 会把「本部署没接判定台账」变成一个与这份产出的内容无关的拒绝理由。
        return ()

    blocked = _relation_channel_refusal(
        decisions=decisions,
        spec=spec,
        workspace=workspace,
        account_id=account_id,
        work_item_id=work_item_id,
        venue_asset_id=venue_asset_id,
        venue_refusal=venue_refusal,
    )
    if blocked:
        return tuple(
            RelationOutcome(
                ordinal=ordinal,
                account_id=account_id,
                relation=finding.relation,
                disposition=RelationDisposition.REJECTED,
                reason=blocked,
            )
            for ordinal, finding in enumerate(researched)
        )

    assert decisions is not None and spec is not None  # noqa: S101 - 见上：blocked 为空即两者都在
    survivors: list[tuple[int, RelationFinding]] = []
    outcomes: list[RelationOutcome] = []
    for ordinal, finding in enumerate(researched):
        reason = _relation_grounding_refusal(
            finding,
            decisions=decisions,
            spec=spec,
            account_id=account_id,
            work_item_id=work_item_id,
        )
        if reason:
            outcomes.append(
                RelationOutcome(
                    ordinal=ordinal,
                    account_id=account_id,
                    relation=finding.relation,
                    disposition=RelationDisposition.REJECTED,
                    reason=reason,
                )
            )
            continue
        survivors.append((ordinal, finding))

    # 只有过了判定的那些行进那一次调用 —— 于是「同一批里两行说了同一条关系」的折叠
    # 仍然发生在**同一批**里（这些行本来就属于同一份产出），而失败的那些没有在表上
    # 留下任何东西，也就不需要事后撤掉。
    written = remember_relations(
        [
            {
                "account_id": account_id,
                "relation": finding.relation,
                "evidence_ids": list(finding.evidence_refs),
            }
            for _, finding in survivors
        ],
        store=store,
        evidence=evidence,
        workspace=workspace,
        venue_asset_id=venue_asset_id,
        seen_at=seen_at,
        # 每一行背后**恰好一次**判定：上面那五条判据已经把它读回来核对过了，所以
        # 这里写下去的是那个真的读得回来的 id，不是研究员交上来的那个字符串。
        decision_record_ids=[[finding.decision_id] for _, finding in survivors],
    )
    # 序号要换回这一份产出里的那一个：``remember_relations`` 数的是交给它的那个序列，
    # 而失败的行不在里面。不换的话，一份 6 行的产出里第 5 行失败了，回执会把第 6 行
    # 的去向报成「第 5 行」—— 而读的人据此去查的是另一行。
    originals = {index: ordinal for index, (ordinal, _) in enumerate(survivors)}
    outcomes.extend(
        replace(outcome, ordinal=originals[outcome.ordinal]) for outcome in written
    )
    outcomes.sort(key=lambda outcome: outcome.ordinal)
    return tuple(outcomes)


def _no_decision(finding: RelationFinding, detail: str) -> str:
    """一条结论的那次判定站不住时，那句共同的前半段 + 调用方给的后半句。

    三种「依据不成立」各接各的后半句，但前半段与结尾那句处置**只有这一处** ——
    分开写的话，先漂的那一处正是被读的那一处，而这里漂掉的会是「这一行到底为什么
    没写」。
    """
    return (
        f"这一行没写：责任关系结论 {finding.relation!r} 指的判定 "
        f"{finding.decision_id!r} {detail}\n"
        "关系表里的每一行都要指得出是哪一次判定得出的（§3），所以这一行**留在"
        "原地**：这是一次 hold，不是「这块场地没有负责方」。重判一次、或者换一条"
        "有判定支撑的结论再来。"
    )


def _relation_channel_refusal(
    *,
    decisions: DecisionRecordStore | None,
    spec: DecisionSpec | None,
    workspace: str,
    account_id: str,
    work_item_id: str,
    venue_asset_id: str,
    venue_refusal: str,
) -> str:
    """整批共用的前提。返回空串表示都成立。

    它们合成一条理由（而不是四句各说各的）是因为处置只有一条：**这一件工作还写不了
    责任关系**，去看接线与派发，不是去看某一行结论。

    ``venue_refusal`` 在 ``venue_asset_id`` 为空时**取代**下面那条通用理由：调用方
    知道场地为什么说不出来（哪一步派生的、池子里有没有它），这一层不知道 —— 而
    「不知道是哪一块场地」与「回执读不出来」的处置是两个地方。
    """
    missing: list[str] = []
    if decisions is None:
        missing.append("判定台账")
    if spec is None:
        missing.append("本 Scene 声明的责任关系题面")
    if missing:
        return (
            f"这一件工作写不了责任关系：本服务实例没有接 {'、'.join(missing)}。\n"
            "§3 要的是每一条关系都由一次真实的判定支撑 —— 而这里连那条判定都读不回来，"
            "所以这一批一行都不写。这是一条**接线 / 声明**事实（组合根没有构造那个存储，"
            "或者这份 profile 没有 decisions.responsibility_relation），不是「这块场地"
            "没有负责方」—— 后者的处置是去研究，前者是去接线。"
        )
    if not (venue_asset_id or "").strip() and venue_refusal.strip():
        return (
            "这一件工作写不了责任关系：这一批每一条都说不出**是哪一块场地**。\n"
            f"{venue_refusal.strip()}\n"
            "一条责任关系要说清「谁、对哪块场地、负什么责」，还要能追回做出这个判断的"
            "那一次判定 —— 少了任何一格，写下来的行读起来仍是一条正常的关系。"
            "这一批一行都不写。"
        )
    for value, what, where in (
        (workspace, "workspace", "本部署的 Scene"),
        (account_id, "account_id", "这一件工作的主体"),
        (work_item_id, "work_item_id", "这一件工作"),
        (venue_asset_id, "venue_asset_id", "这一件工作研究的场地"),
    ):
        if not (value or "").strip():
            return (
                f"这一件工作写不了责任关系：{what} 是空的（它本该来自{where}）。\n"
                "一条责任关系要说清「谁、对哪块场地、负什么责」，还要能追回做出这个"
                "判断的那一次判定 —— 少了任何一格，写下来的行读起来仍是一条正常的关系。"
                "这一批一行都不写。"
            )
    return ""


def _relation_grounding_refusal(
    finding: RelationFinding,
    *,
    decisions: DecisionRecordStore,
    spec: DecisionSpec,
    account_id: str,
    work_item_id: str,
) -> str:
    """一条结论的那次判定站不站得住。返回空串表示站得住。"""
    record = decisions.get_decision(finding.decision_id)
    if record is None:
        return _no_decision(
            finding, "在这个部署的判定台账里解不开（没有这条记录）。"
        )

    recorded = (record.get("work_item_id") or "").strip()
    if recorded != work_item_id:
        return _no_decision(
            finding,
            f"不是为这一件工作做的：它记在 work item {recorded!r} 上，而这一件是 "
            f"{work_item_id!r}。\n"
            "判定可以跨工作复用的话，一份关于 A 的判断就能支撑关于 B 的关系 —— 而"
            "报告上两者完全同形。",
        )

    subject = (record.get("subject_ref") or "").strip()
    if subject != account_id:
        return _no_decision(
            finding,
            f"的主语是 {subject!r}，而这一件工作的主体是 {account_id!r}。\n"
            "同一件工作只研究一个主体，所以这是一次拿错了判定 —— 那条判定是关于"
            "另一个账号的。",
        )

    recorded_spec = (record.get("spec_id") or "").strip()
    if recorded_spec != spec.spec_id:
        return _no_decision(
            finding,
            f"不是责任关系那道题：它的 spec_id 是 {recorded_spec!r}，而本 Scene 的"
            f"责任关系题面是 {spec.spec_id!r}。\n"
            "另外两道题（页面档位、角色口径）的记录读起来同样正常，拿它们当依据等于"
            "把「这一页是几档」当成了「谁负责」。",
        )

    outcome = (record.get("outcome") or "").strip()
    if outcome != DecisionOutcome.ANSWERED.value:
        return _no_decision(
            finding,
            f"没有给出结论（outcome={outcome!r}；后端说的理由是 "
            f"{(record.get('reason') or '').strip() or '（空）'}）。\n"
            "弃权是一个诚实的结局，但它**不是**一个关系结论 —— 从它推出任何取值都是"
            "我们自己补的。",
        )

    choice, why = _relation_choice(record, spec)
    if why:
        return _no_decision(finding, why)
    if choice != finding.relation:
        return _no_decision(
            finding,
            f"答的不是这个关系：判定选了 {choice!r}，而这一行写的是 "
            f"{finding.relation!r}。\n"
            "答案与结论对不上时，表里那一行会以「判定得出的」身份存在，而判定说的是"
            "另一件事 —— 两者在盘上留下的记录同形。",
        )
    return ""


def _relation_choice(
    record: Mapping[str, Any], spec: DecisionSpec
) -> tuple[str, str]:
    """判定里那一题的答案，以及取不到时的理由。``(choice, "")`` 或 ``("", reason)``。

    题号取自**题面**（``choices_by_question`` 只有选择题），不取自回执：回执是别人
    写下来的那一份，而「我该在哪一题上读答案」是我们自己的结构。

    ``compile_spec`` 造的题面恰好一道题，所以这里多于一道就是**拿错了题面对象**（不是
    一次判定的事）—— 抛 ``ConfigError``，与 ``ports.decisions._one_answer`` 里
    「题面与回执对不上」那一类同处置：那是配置事故的签名，不是某一条结论的问题。
    """
    choices = spec.choices_by_question
    if len(choices) != 1:
        raise ConfigError(
            f"题面 {spec.spec_id!r} 有 {len(choices)} 道选择题，而责任关系这道题"
            f"恰好一道（{sorted(choices)}）。\n"
            "这一层按题面读答案，题面不是那一道时读出来的是另一道题的答案 —— 而它"
            "会以一条正常结论的形状落表。"
        )
    question_id = next(iter(choices))

    answers = record.get("answers")
    if not isinstance(answers, Sequence) or isinstance(answers, (str, bytes)):
        return "", f"的回答不是一个序列（实际 {type(answers).__name__}）"
    for answer in answers:
        if not isinstance(answer, Mapping) or answer.get("question_id") != question_id:
            continue
        choice = answer.get("choice")
        if isinstance(choice, str) and choice:
            return choice, ""
        return "", (
            f"里关于 {question_id!r} 的那条回答不是一个选择题答案"
            f"（kind={answer.get('kind')!r}）—— 是否题的记录读不出一个类别"
        )
    return "", f"里没有关于 {question_id!r} 的回答"

"""一次研究的**产出**：观察、候选、缺口（``MCP_SERVICE.md`` §3 ``submit_findings``）。

## 为什么结论必须挂证据

``ContactRoute`` 已经立过同一条判据（``domain/entities.py`` 的
``_evidence_required_for_non_x``：非 X 的判定没有 ``evidence_ids`` 直接拒绝），
本模块把它搬到发现期：:class:`Finding` 的 ``evidence_refs`` 不可为空 ——
**一条追不到依据的结论在类型上就写不出来**，而不是靠评审时记得去问。

§4 逐字：「an extracted summary does not replace the evidence used for literal
occurrence and person/role/Account binding」——所以这里挂的是证据 id，不是
「我读过一页」。

## 这条判据**查不到**的那一半，以及它现在住在哪

本层校验的是「引用非空、去重、形如本项目的证据 id」—— 三条都是形状判据，它们
对一个精心编造的 id 与一条真证据给出同一种回答。

**存在性不在这一层。** 它是作业存储与证据存储的**联合状态**，而领域层连
``EvidenceStore`` 都不能 import（``ports/`` 依赖 ``domain/``，反过来会成环）。
它住在 ``application/evidence_refs.py``，唯一同时看得见两边的一层，唯一的实现，
由 ``submit_findings`` 在结算**之前**调用 —— 引用解不开就整份拒绝。

写在这里是为了让读这个模块的人不必翻到那一层才知道「这里不查什么」。此前这段
说的是「端口上没有按 id 取的入口，所以引一条不存在的证据仍然可能」—— 那句话在
``EvidenceStore.get_evidence`` 落地的那一天就过期了，而一条过期的缺口说明与一条
永真的检查是同一类东西：都让人以为有人已经查过了。

## 缺口是另一种东西，所以不是 ``Finding``

``gaps`` 是**结论的缺席**：「我没能确认 X」。它按定义没有证据可挂，做成
``Finding`` 会逼每一个缺口伪造一条依据 —— 于是「有依据的结论」与「没有结论」在
数据里长得一样。这与 ``EvidenceStore`` 把 ``routes`` / ``opportunities`` 分成两个
集合是同一条理由（见 ``ports/store.py``）。

## 计数是这里唯一的业务数字，所以重复项是硬错误

报告上的「候选 N 个」是从这三个元组的长度算出来的。同一个 statement 出现两次
会**直接变成**一个虚高的业务数字，而没有任何一处会红 —— 它就是 §8B 说的
「false count」最便宜的一种。所以重复的 statement / gap 直接拒绝。

引用是例外，而且不对称是刻意的：``evidence_refs`` 是一个**集合**，顺序与重复都
不承载信息，所以排序去重；statement 承载计数，所以拒绝。

## 责任关系是**第四种**产出，而它不是 ``Finding`` 换了个名字

``relations`` 里的一条说的是「这个账号对这块场地负哪种责」，它由
:class:`RelationFinding` 承载。它与 ``Finding`` 有三处不同，每一处都是承重的：

* 它**必须**带一条判定记录的 id（``decision_id``）。这一格是 §3「决策真正被下游
  消费」唯一可能的执行器：一条没有判定的关系结论在类型上就写不出来，于是
  「Jev 判过、而下游按它的答案落了库」不是一句自觉，是一条校验器。
* 它的取值来自一个**闭集**（哪个 ``relation`` 取值合法由 Scene 的 profile 决定，
  见 ``application/decision_specs.py``）。闭集不在这一层校验 —— 领域层读不到
  profile，而在这里写死一份取值表就是第二个定义处。它由
  ``application/relations.py`` 按**同一份闭集**判，两处判据因此不可能漂移。
* 它**不参与** ``claim_count``。报告上的「候选 N 个」是研究结论的条数，而一条
  责任关系是落进另一张表的行 —— 把两者合成一个数字，读的人会拿它去对关系表的
  行数，而那两件事不相等。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .util import check_canonical_id, sha256_text

__all__ = [
    "Finding",
    "Findings",
    "RelationFinding",
    "findings_payload",
    "findings_from_payload",
    "findings_sha256",
]


def _unique_check(values: tuple[str, ...], field: str, *, what: str) -> tuple[str, ...]:
    """去空白 + 非空 + 去重检查。三个元组共用一份，理由见模块 docstring。"""
    cleaned: list[str] = []
    for raw in values:
        value = (raw or "").strip()
        if not value:
            raise ValueError(f"{field} 里有一条空字符串 —— 一条空的{what}不是一条{what}。")
        cleaned.append(value)
    duplicates = sorted({item for item in cleaned if cleaned.count(item) > 1})
    if duplicates:
        raise ValueError(
            f"{field} 里有重复项：{duplicates}。\n"
            "报告上的条数是从这些元组算出来的，所以重复项会直接变成一个虚高的业务"
            "数字，而没有任何一处会报错。",
        )
    return tuple(cleaned)


@dataclass(frozen=True)
class Finding:
    """一条**有依据的**结论。

    ``statement`` 两侧空白会被去掉：``"x"`` 与 ``" x "`` 是同一条结论，留着它
    们就是给「同一句话写两遍」留一个形状不同的入口。
    """

    statement: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        statement = (self.statement or "").strip()
        if not statement:
            raise ValueError("statement 不能为空。")
        object.__setattr__(self, "statement", statement)

        if not self.evidence_refs:
            raise ValueError(
                f"结论 {statement!r} 没有挂任何证据。\n"
                "任何结论都必须能追到一条已抓取的公开文档 —— 一条追不到依据的结论"
                "不是结论，而它在报告里与一条真结论长得完全一样。"
            )
        refs = tuple(sorted({(ref or "").strip() for ref in self.evidence_refs}))
        for ref in refs:
            check_canonical_id(ref, "evidence_refs")
        object.__setattr__(self, "evidence_refs", refs)

    def as_dict(self) -> dict[str, object]:
        return {"statement": self.statement, "evidence_refs": list(self.evidence_refs)}


@dataclass(frozen=True)
class RelationFinding:
    """**这个账号对这块场地负哪种责** —— 一次研究能交上来的第四种产出。

    ## 它为什么必须带 ``decision_id``

    §3 要求 Jev 的判定**真正被下游消费**，而不是「跑过几次、台账上有几行」。
    ``decision_id`` 是那件事唯一的执行器：一条关系结论必须指得出是哪一次判定
    得出的，而 ``application/relations.py`` 会去把那条记录读回来、核对它的题面
    是不是 ``responsibility_relation``、核对它的答案就是这个 ``relation``。
    没有这一格，「判定」与「落库的关系」之间只有一句自觉 —— 而自觉在下一次
    改动里最先消失，且消失时没有任何一处会红。

    它**不是**「先写结论、再补依据」的那种可选字段。一条由人写在纸上、由模型
    顺手连起来的关系，正是 §3 点名禁止的那几件事（看到官网就默认 owner）的
    形状：它们都说得通，只是没有任何一次判定发生过。

    ## ``relation`` 是**原文**，闭集不在这里

    ``str`` 而不是 :class:`~marketops.domain.enums.VenueRelation`：取值不在闭集里
    是**一行**的去向（``REJECTED``，理由里点名可用取值），而在更早的地方把它当
    类型收下，意味着一行写错了取值会把整次结算炸掉 —— 同一份产出里其余的结论
    与它无关（``RelationClaim`` 逐字同一条理由）。闭集住在 Scene 的 profile 里，
    而领域层读不到 profile，所以判它的地方只有一个：应用层。
    """

    relation: str
    decision_id: str
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        relation = (self.relation or "").strip()
        if not relation:
            raise ValueError(
                "一条责任关系结论没有说它是哪种关系。\n"
                "「有关系、但不知道是哪种」是一个**结论**（`unresolved`），"
                "不是一个可以留空的格子 —— 留空的那一行在下游读起来与「还没判」"
                "完全一样，而两者的下一步不同。"
            )
        object.__setattr__(self, "relation", relation)

        decision_id = (self.decision_id or "").strip()
        if not decision_id:
            raise ValueError(
                f"责任关系结论 {relation!r} 没有说它是哪一次判定得出的。\n"
                "§3 要的是判定**真正被下游消费**：一条指不出判定的关系结论，"
                "与一条由人凭印象连起来的关系在数据里长得一样。"
            )
        check_canonical_id(decision_id, "decision_id")
        object.__setattr__(self, "decision_id", decision_id)

        refs = tuple(sorted({(ref or "").strip() for ref in self.evidence_refs}))
        if not refs:
            raise ValueError(
                f"责任关系结论 {relation!r} 没有挂任何证据。\n"
                "``AccountVenueRelation`` 那条「一条关系至少要一条证据」在这里就"
                "该被挡住 —— 否则这一行会先落进产出、再在落库时被拒，而报告上"
                "「交上去的」与「写下来的」对不上。"
            )
        for ref in refs:
            check_canonical_id(ref, "evidence_refs")
        object.__setattr__(self, "evidence_refs", refs)

    def as_dict(self) -> dict[str, object]:
        return {
            "relation": self.relation,
            "decision_id": self.decision_id,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True)
class Findings:
    """一个 attempt 的产出。**可以为空**，而且空是一个诚实的业务结果。

    §8B 逐字：「No fixed positive A/B count is required: zero is an honest
    business outcome」。一个跑过、什么都没找到的 attempt 提交的正是
    ``Findings()`` —— 它落盘之后，报告说 ``stored: 0`` 才是**真的**：
    那 0 背后有一次真实的运行。

    这也是它与「还没有任何结算」的分界（``get_job`` 的 ``results.available``）：
    前者是一次诚实的空结果，后者是一个还没走完的通路。
    """

    observations: tuple[Finding, ...] = ()
    candidates: tuple[Finding, ...] = ()
    gaps: tuple[str, ...] = ()
    #: 这个账号对**这块场地**负哪种责。这里是「一块场地」而不是「几块」：一次研究
    #: 的主体是一个账号，而它被派去研究的是一块具体的场地（
    #: ``application/promotion.py`` 的派生），所以这一份产出的责任结论全部关于
    #: 同一块场地。这样一来，``relation`` 在同一个元组里出现两次就是一句话写了两
    #: 遍（下面是硬错误），而不是合法的「同一块场地上两种不同的责任」—— 后者是
    #: **两条不同的** ``relation`` 取值，各自一条。
    relations: tuple[RelationFinding, ...] = ()

    def __post_init__(self) -> None:
        for field, values in (
            ("observations", self.observations),
            ("candidates", self.candidates),
        ):
            seen: set[str] = set()
            for finding in values:
                if finding.statement in seen:
                    raise ValueError(
                        f"{field} 里 {finding.statement!r} 出现了两次。\n"
                        "同一个 statement 写两遍会直接变成一个虚高的条数，"
                        "而没有任何一处会报错。"
                    )
                seen.add(finding.statement)
        object.__setattr__(self, "gaps", _unique_check(self.gaps, "gaps", what="缺口"))

        # 与上面那两条同一条判据、同一条理由：报告上「这个市场多了几条责任关系」
        # 是从关系表算出来的，而**交上来的**那几行如果有一句写了两遍，两处就对不上
        # —— 对不上的表现不是报错，是一次没人会去核的对账。
        #
        # 两个维度都查：同一个 ``relation`` 写两遍是重复，同一次 ``decision`` 得出
        # 两个关系结论则意味着**判定被读出了它没有说的东西** —— 后者更坏，因为它
        # 看起来像两次独立的发现。
        seen_relations: set[str] = set()
        seen_decisions: set[str] = set()
        for entry in self.relations:
            if entry.relation in seen_relations:
                raise ValueError(
                    f"relations 里 {entry.relation!r} 出现了两次。\n"
                    "这块场地与这个账号之间的这一种责任只能说一次：两行会折成"
                    "同一行（键含 ``relation``），而报告上的条数会虚高一倍。"
                )
            if entry.decision_id in seen_decisions:
                raise ValueError(
                    f"判定 {entry.decision_id!r} 得出了两条不同的责任关系。\n"
                    "一次判定回答的是一道题，所以它的答案只有一个 —— 拿同一条记录"
                    "去支撑两个结论，读起来像两次独立的发现。"
                )
            seen_relations.add(entry.relation)
            seen_decisions.add(entry.decision_id)

    @property
    def is_empty(self) -> bool:
        """**跑过了但什么都没产出** —— 不是「还没跑」（见类 docstring）。"""
        return not (self.observations or self.candidates or self.gaps or self.relations)

    @property
    def claim_count(self) -> int:
        """结论条数。缺口不计入 —— 它是结论的缺席，不是一条结论。

        **责任关系也不计入。** 报告上的「候选 N 个」数的是研究结论，而一条责任
        关系是落进另一张表的行（``account_venue_relation``）。把它们合成一个数字，
        读的人会拿它去对关系表的行数 —— 而那两件事不相等，且没有一处会说明。
        """
        return len(self.observations) + len(self.candidates)

    @property
    def relation_count(self) -> int:
        """责任关系结论的条数。与 ``claim_count`` 分开报，理由见上。"""
        return len(self.relations)

    def as_dict(self) -> dict[str, object]:
        return findings_payload(self)


def findings_payload(findings: Findings) -> dict[str, object]:
    """规范形状。**两个适配器共用这一份**，理由与 ``request_fingerprint`` 相同：
    两处各序列化一遍，漂移的那天同一份 findings 会有两个内容散列，于是
    「同一次重发」被读成「一次新的产出」。
    """
    return {
        "observations": [finding.as_dict() for finding in findings.observations],
        "candidates": [finding.as_dict() for finding in findings.candidates],
        "gaps": list(findings.gaps),
        "relations": [entry.as_dict() for entry in findings.relations],
    }


def findings_from_payload(payload: object) -> Findings:
    """``findings_payload`` 的逆。形状不对时抛 —— 不猜、不补默认值。

    ``relations`` 那一格**按缺席读成空元组**，而这是本模块唯一一处容忍缺键的地方。
    理由不是宽容：这个载荷有**历史行**。``relations`` 落地之前结算的那一批产出
    盘上真的没有这一格，而它们当时的含义逐字就是「这一次没有责任结论」——
    把缺键读成「这份载荷写坏了」会让那些行再也读不回来（``get_job`` 读的是同一份
    载荷），而那不是一次「失败关闭」，是一次**让历史消失**。

    反过来说清楚它不接受什么：这一格**在的时候**必须是一个列表，里面每一格必须
    是 ``RelationFinding`` 认得的形状。少了键是历史，写坏了是现在 —— 两者必须
    分得开，因为处置完全不同。
    """
    if not isinstance(payload, dict):
        raise ValueError(f"findings 的载荷应该是一个对象，拿到 {type(payload).__name__}。")
    try:
        observations = tuple(
            Finding(statement=entry["statement"], evidence_refs=tuple(entry["evidence_refs"]))
            for entry in payload["observations"]
        )
        candidates = tuple(
            Finding(statement=entry["statement"], evidence_refs=tuple(entry["evidence_refs"]))
            for entry in payload["candidates"]
        )
        gaps = tuple(payload["gaps"])
        relations = tuple(
            RelationFinding(
                relation=entry["relation"],
                decision_id=entry["decision_id"],
                evidence_refs=tuple(entry["evidence_refs"]),
            )
            for entry in payload.get("relations", ())
        )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"findings 的载荷形状不对：{exc}") from exc
    return Findings(
        observations=observations, candidates=candidates, gaps=gaps, relations=relations
    )


def findings_sha256(findings: Findings) -> str:
    """一次产出的内容散列 —— 「这次提交与已经落盘的那次是不是同一次」。

    ``sort_keys`` 与紧凑分隔符都是必要的：``json.dumps`` 的默认输出带空格，
    而**同一份 findings 在两个 Python 版本上可能给出不同空白**，那时同一个
    内容会算出两个散列，重发被读成冲突。
    """
    return sha256_text(
        json.dumps(findings_payload(findings), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )

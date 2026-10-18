"""候选的资格判定 —— **共享的**判定引擎，两支臂跑的是同一个。

上游的 Account Discovery 冒烟实验比较臂 B（确定性查询规划器）与臂 C
（Claude Code + 研究 Skill），报告输出 ``C - B``。用户规格 §3 的硬要求是：
Skill 可以重写查询、发现新的来源族、识别新术语、决定继续还是停止，
**但不得改变最终的 Account 资格判定规则 —— 两支臂跑同一个判定引擎**。
如果两边的判定规则不是同一份，``C - B`` 量的就不是发现能力，而是判定能力 ——
恰好是这一轮要测的量的反面。

所以本模块**不实现**任何判定：它把候选行投影成事实，然后把事实交给
``marketops/application/grading.py::account_outcome_from_facts`` ——
与账号路径 ``grade_account`` 调的是同一个函数、同一份
``profiles/*/grading.yaml``。三件事因此同时成立：

1. **规则只有一份**，而且仍然只在 ``profiles/*/`` 里。本模块**不新增、不修改、
   不覆盖**任何规则，也没有一条「候选专用的」规则 —— 一条都没有。
2. **事实键集相同**：:func:`candidate_facts` 产出的键集恒等于
   ``ACCOUNT_QUALIFICATION_FACT_NAMES``，与 ``account_facts`` 逐字相同。求值循环
   把这件事当硬前提（见 ``marketops/application/grading.py`` 的
   ``_require_the_qualification_key_set``：键缺失与值 ``None`` 同判之后，
   漏一个键与「还没有生产者」就再也分不开了）。
3. **结果同型**：:class:`CandidateQualification` 里包的是 ``AccountOutcome``
   本身，不是它的复制品 —— 两条路的结果可以直接 ``==`` 比较（判词也逐字相同）。

# 为什么候选必须走同一套规则

``grade_account(account, bundle)`` 收的是 ``AccountRecord``，而本轮**不产出**
``AccountRecord``（``Cohort`` 是闭集，见 ``marketops/domain/candidate.py`` 的类文档）。
如果候选为了能被判定而另写一套规则（哪怕「看起来一样」），两支臂的差异里就会
混进判定差异，而那个差异在报告上没有任何东西能把它认出来。

# 投影是**字面**的，取值该是什么就是什么

字段名与 ``marketops/domain/facts.py`` 的 ``CANDIDATE_FACT_NAMES`` 逐字同名，
所以投影几乎是字面量 —— 改名会让「引擎认识的事实」与「候选行上的字段」中间
多出一层需要同步维护的映射，而漂移的那一次不会有任何症状：规则只是**永不命中**。

两个计数（``pages_fetched`` / ``pages_blocked``）折成「有没有」：规则表只问
「抓过吗 / 被挡过吗」（``!any_page_fetched`` / ``any_page_blocked``）。
``None``（**没有记录**）与 ``0``（**记了，是 0**）在这一步**不能**折成同一个值：
``None`` 保持 ``None``（未确认），``0`` 折成 ``False``（已确认）。候选行仍然把
它们分开存着（``marketops/domain/candidate.py`` 的字段文档），而规则方言现在也把
它们分开判 —— ``None`` 让规则**不决定**（进 ``unchecked``），``0`` 让规则
**确认为假**（不命中，但**不**进 ``unchecked``）。把 ``None`` 折成 ``False``，会把
「还没查」读成「查了，是 0」；把 ``None`` 换成哨兵字符串，会在 truthy 子句里读成
「确认有」。

# 区域：生产者已经接上，而它产出的第一格是「未知」

``country`` 在候选侧曾经**没有生产者**：候选行没有任何字段携带区域，于是
``profiles/*/grading.yaml`` 的 ``out_of_region``（``!country in [...]``）永远判不了。
后来引擎收紧了语义 —— 未确认在**任何**极性下都不决定子句 —— 所以这条规则的输出
从「由缺省命中」变成了「**没判**」（``unchecked`` / ``UNRESOLVED_FACTS``）。
那是正确的中间态，但缺口本身还在。

现在候选行有了 ``country`` 字段，生产者是
``marketops/application/classification.py``：从**页面正文**（构成国名）与
**主机名的标签后缀**（``.uk`` 一族）读出来。两条判据都带得出处，而它们的产出是
**三态**的：

* 读出在范围内 ⇒ ``gb`` ⇒ 子句求值、确认为假 ⇒ ``out_of_region`` 不命中，
  而且**离开 ``unchecked``**（「核对过，不是越界」是一条结论）；
* 读出在范围外 ⇒ 今天**产不出来**：词表里只有范围内的取值，因为枚举世界会
  静默过期（见 ``profile.yaml`` 的 ``classification`` 一节）。这是**记录下来的
  缺口**，不是被掩盖的：它落在下面第 3 条上；
* 读不出来 ⇒ ``unknown`` ⇒ 投影成 ``None`` ⇒ 子句不决定 ⇒ 规则留在 ``unchecked``
  ⇒ 账号停在 ``discovered``。**不自动通过资格**，也不冒充越界。

第 3 条是本层最要紧的一格：``unknown`` 与「记了，是空」在这条投影上必须分开，
而它们分开的地方**只有这一个函数** —— 字段那一侧拒绝空串（见
``marketops/domain/candidate.py`` 的 ``_classification_readings_are_conclusions``），
投影这一侧把 ``unknown`` 折成 ``None``，两条合起来才让那句「空串不再是结论」成立。

候选侧的形状钉在
``tests/test_discovery_qualify.py::test_the_region_fact_has_a_candidate_side_producer``，
它原来钉的是「每一行都由缺省命中」，后来钉的是「每一行都**不判**」，
现在钉的是「读数就是这个值，而且读不出来的那一格仍然**不判**」。

# 另一条未决项：来源族

``source_method_id`` 的注册表（``marketops/domain/sources.py``）只覆盖**引擎自己
能产出**的发现方法。SOP L82 要的「每个来源有独立的 ``source_method_id``」指的是
**具体的来源**（某个目录、某个登记册），而规格 §3 说 Skill 可能发现**新的来源族**
—— 那些属于市场知识，住在 ``profiles/*/`` 一侧。两者今天没有对齐，本轮也**没有
验证任何**来源族，所以不往注册表里加任何成员（那是臆造）。张力记录在
``marketops/domain/sources.py`` 的「一层不在这里的东西」一节。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.candidate import AccountCandidate
from ..domain.enums import AccountStatus, EligibilityReason
from ..domain.profile import CLASSIFICATION_UNKNOWN, ProfileBundle
from .grading import AccountOutcome, account_outcome_from_facts

#: 候选行上**仍然没有**生产者的账号级事实，及其恒取的 ``None``。
#:
#: ``has_notes`` 是这个缺口今天剩下的形状：候选是**发现期**的一行，而「备注」是
#: operator 写下来的东西 —— 发现期没有任何一步产生它，也不该产生。
#: **不得**在这里填一个默认值 —— 一个编出来的值会让点它的规则拿到一个**看起来像
#: 证据**的取值：它从此要么命中、要么确认为假，**再也不会进 ``unchecked``** ——
#: 也就是说「这一格还没有值」这件事从报告上消失了。那是这一层最贵的失败：
#: 编造的值不会有任何症状，只会让「没查」变得与「查过」长得一样。
_UNPRODUCED_ACCOUNT_FACTS: dict[str, object] = {
    "has_notes": None,
}


def _reading(value: str) -> str | None:
    """分类读数 → 事实值。``unknown`` **折成 ``None``**（未确认），其余照抄。

    这一行是「``unknown`` 是正式结论」这句话在**求值**这一侧的落点，而它与
    字段那一侧的校验器是一对（见 ``marketops/domain/candidate.py``）：
    那一侧拒绝空串，这一侧把 ``unknown`` 折成未确认。两条缺一不可 ——
    只有前一条，``unknown`` 会作为一个非空字符串进方言，而 ``bool("unknown")``
    为真，``!country in [gb, uk]`` 会被它**真的**触发；只有后一条，
    空串会照旧冒充一条已确认的结论。

    其余取值**逐字照抄**：本函数不认识任何一个取值（词表住 profile），
    它只认识那**一个**保留写法。
    """
    return None if value == CLASSIFICATION_UNKNOWN else value


def _any(count: int | None) -> bool | None:
    """把计数折成「有没有」。``None``（没有记录）**保持** ``None``。

    这两个值今天**不再同判**：``None`` 是未确认（规则不决定、进 ``unchecked``），
    ``False`` 是已确认（「记了，是 0」⇒ 规则确认为假、**不**进 ``unchecked``）。
    所以这一行是**承重的**，不是折算便利：把 ``None`` 折成 ``False`` 会把
    「还没查」读成「查了，是 0」，而后者是一个结论。
    """
    return None if count is None else count > 0


def candidate_facts(candidate: AccountCandidate) -> dict[str, object]:
    """把 ``AccountCandidate`` 投影成**账号准入事实**。只做字面投影，不做推断。

    产出的键集恒等于 ``ACCOUNT_QUALIFICATION_FACT_NAMES`` —— 与
    ``marketops/application/grading.py::account_facts`` 逐字相同。这不是巧合也不是
    约定：求值循环 ``account_outcome_from_facts`` 把它当硬前提，键集不同直接抛
    （键缺失与值 ``None`` 现在同判，所以漏一个键与「还没有生产者」分不开 ——
    这正是那条断言今天要守的东西）。

    投影照字段名逐字对应，取值**该是什么就是什么**：

    * ``organisation_kind`` / ``venue_environment`` / ``surface_class`` /
      ``country`` —— 取同名字段，再经 :func:`_reading` 把保留写法 ``unknown``
      折成 ``None``（未确认）。**这不是「对齐两条路」**：``unknown`` 与 ``None``
      说的是同一件事（「没有证据」），把它们在**求值**这一侧接上，是为了让
      「还没有值」只有一种形态进方言；而字段那一侧拒绝空串，是为了让「记了，是空」
      不会伪装成它（见 ``marketops/domain/candidate.py``）。
    * ``surface_evidence_confirmed`` —— 直接取同名字段（**三值**，不折）。
    * ``exclusions_hit`` —— 同名字段（规则只问「是不是非空」）；
    * ``any_page_fetched`` / ``any_page_blocked`` —— 由两个计数折成（见 :func:`_any`）；
    * ``has_domain`` —— ``bool(official_domain)``：域名这一栏自己就说明有没有；
    * ``has_notes`` —— **恒为 ``None``**，因为候选行上没有这个字段、也没有别的
      生产者（见 :data:`_UNPRODUCED_ACCOUNT_FACTS`）。

    **本函数不编造任何取值。** 逐字取候选行的字段值（那四格过一遍
    :func:`_reading`），没有生产者的那一个键就是 ``None``。所以 ``""`` 只要
    进得来，它在方言里仍然是**已确认的空**（「记了，是空」），规则会判它为假 ——
    本层**不得**把那个改写成 ``None`` 来「对齐」两条路；挡住它的地方在字段的
    校验器上，那是一处**构造期**的拒绝，不是一次投影期的改写。
    """
    return {
        # 候选行上仍然没有生产者的那一个 —— 见 _UNPRODUCED_ACCOUNT_FACTS。
        **_UNPRODUCED_ACCOUNT_FACTS,
        # 域名这一栏自己就说明有没有。``bool("") is False``。
        "has_domain": bool(candidate.official_domain),
        # 计数折成「有没有」。
        "any_page_fetched": _any(candidate.pages_fetched),
        "any_page_blocked": _any(candidate.pages_blocked),
        # 逐字同名的五个：字段名 = 事实名，因此是字面投影，没有中间映射。
        # 前四个是**分类读数**，过 _reading（``unknown`` → ``None``）；
        # 第五个是三值的，不在读数词表里，照抄。
        "exclusions_hit": list(candidate.exclusions_hit),
        "venue_environment": _reading(candidate.venue_environment),
        "organisation_kind": _reading(candidate.organisation_kind),
        "surface_class": _reading(candidate.surface_class),
        "country": _reading(candidate.country),
        "surface_evidence_confirmed": candidate.surface_evidence_confirmed,
    }


@dataclass(frozen=True)
class CandidateQualification:
    """一条候选的资格判定结果 —— 结论 + **回到证据的指针**。

    ``outcome`` 是账号侧那个 :class:`~marketops.application.grading.AccountOutcome`
    **本身**（不是复制品、不是「同形的另一个类」）：两条路的结果因此可以逐字段
    比较，判词也逐字相同。给两条路各写一个结果类型，等于让「它们是不是同一套
    规则」变成一个无法直接回答的问题。

    三组指针与判定并列存放，不合并成一句散文：人的下一条问题永远是「凭什么」，
    ``outcome.detail`` 回答「哪条规则」，指针回答「去看哪几条证据」。合成一个
    字段之后，复核这件事就只剩一个人能做 —— 而 false attribution 正是这一层
    最需要被逐条看的失败。
    """

    candidate_id: str
    outcome: AccountOutcome
    #: 行级证据（责任实体 / 场馆资产 / 来源）。
    evidence_ids: tuple[str, ...] = ()
    #: 支撑表面判定的证据。
    surface_evidence_ids: tuple[str, ...] = ()
    #: 支撑官方域名的证据。
    domain_evidence_ids: tuple[str, ...] = ()

    @property
    def status(self) -> AccountStatus:
        return self.outcome.status

    @property
    def excludes(self) -> bool:
        """是否被规则明确排除。``hold`` 不算排除 —— 两条路同一条语义。"""
        return self.outcome.excludes

    @property
    def reason(self) -> EligibilityReason:
        return self.outcome.reason


def qualify_candidate(
    candidate: AccountCandidate, bundle: ProfileBundle
) -> CandidateQualification:
    """判定一条候选的账号资格 —— 用的是**账号侧那一份**求值循环。

    Args:
        candidate: 待判定的候选行。
        bundle: 账号准入规则的唯一来源（``profiles/*/grading.yaml``）。

    Returns:
        结论 + 证据指针。规则集为空时停在 ``discovered``（``NO_RULE_FIRED``），
        与账号侧逐字相同 —— 两条路都不自带规则。规则集不空、但引用的事实
        **未确认**时同样是 ``discovered``，判词是 ``UNRESOLVED_FACTS`` 且
        ``outcome.unchecked`` 非空：那是「这条规则没判」，不是「核对过、不命中」。
    """
    outcome = account_outcome_from_facts(candidate_facts(candidate), bundle)
    return CandidateQualification(
        candidate_id=candidate.candidate_id,
        outcome=outcome,
        evidence_ids=tuple(candidate.evidence_ids),
        surface_evidence_ids=tuple(candidate.surface_evidence_ids),
        domain_evidence_ids=tuple(candidate.domain_evidence_ids),
    )


__all__ = [
    "CandidateQualification",
    "candidate_facts",
    "qualify_candidate",
]

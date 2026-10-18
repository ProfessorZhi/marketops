"""两条**互不决定**的判定：账号准入 与 路线定级。

SOP v1 L188 画的是两个状态机，不是一个：

============================  ==========================================
``grade_account``             账号：``discovered → eligible/hold/irrelevant/
                              duplicate → researched → archived``
``grade_route``               路线：``observed → A/B/C/D/X → queued_candidate
                              → prepared_pending_authorization → ...``
============================  ==========================================

**它们不互相决定。** 「组织对、人不对」是常见情形：账号 eligible，而已拿到的
那条路线是 X。反过来，一条 A 级路线也不能把一个 duplicate 的账号救回来。
把两者合成一个函数，就无法表达这两种情形 —— 那不是简化，是删信息。

引擎**不内建任何等级含义**：

* 语义来自 ``profiles/<profile>/grading.yaml``；
* 引擎拿不到可求值的规则时**拒绝判定**，返回 ``X`` + ``GradeReason.RULES_UNKNOWN``，
  并把 ``bundle.unknowns`` 的原因写进 ``detail``；
* 绝不退回「默认算 A」或「按通用惯例猜一个」—— 那会让编造的规则被当成真规则用。

事实词汇是中性的，因此不违反「业务规则只进 profiles/」：引擎认识
``role_class``，不认识 ``grounds supervisor``；认识 ``business_function``，
不认识 ``bookings`` 到底算不算业务入口 —— 那是 profile 的 ``grading.yaml`` 说的。

**账号侧有一条不对称的语义，必须记住**：排除规则求不了值时**不得命中**。
一条空的排除规则若被读成「恒真」，会把整个名单一次清空，而且看不出是错的。
所以 ``AccountRule.requires is None`` 的含义是「不可求值、永不命中、列进 unchecked」，
与等级规则的 ``requires == ()``（显式恒真）**不是**同一件事。

## 它为什么住在 ``application/`` 而不是顶层 ``grading/``（B7b）

本模块从 ``marketops/grading/engine.py`` 搬来（原来的包只有这一个模块 + 一个
``__init__``）。§2 的目标树两侧都点到了这件事，而它们指的是不同的东西：

    domain/       纯领域：实体、值对象、**规则**。禁止 import ...
    application/  用例编排：job 生命周期、预算、发现、**定级流程**

**规则**住在 ``domain/profile.py`` —— ``AccountRule`` / ``GradeRule`` / ``ProfileBundle``
是规则的**形状**；本模块不做别的，就是把它们**求值**（「遇到分支怎么走」）。
所以「规则」那半句已经有人满足了，而「定级流程」这半句在此之前没有落点：
``import marketops.grading.engine`` 从外面看像一个与 ``domain/`` ``application/``
``ports/`` 并列的**第五层**，而分层模型里没有它的位置。

搬进 ``application/`` **没有**新增任何执行器，这一点要说清楚：今天真正管着本模块的
仍然只有 ``tests/test_adapters_hold_the_vendors.py``（它扫 ``marketops/`` 全局，
B7a-3 之后连"点名具体厂商 adapter"也管）。收益不是"多了约束"，而是**答案只有一个**：
问「这一层是哪一层」，从前会得到一个与 ``domain/`` ``application/`` ``ports/``
``adapters/`` 并列的第五个名字，而分层模型里没有它。

还有一条**现在仍然没有执行器**的话，写在这里免得被读成已经成立：
``application/`` 不得 import ``adapters/``（任何 adapter，不只厂商那几个）。
B7a-2 之后事实上成立 —— ``application/`` ``domain/`` ``ports/`` 三个包对
``adapters/`` 的 import 是零 —— 但它是**观察**，不是判据；今天只有"点名厂商 adapter"
那一半被拦得住，`from ..adapters.jsonl.store import ...` 这类不会被拦。

代价写在明处：``marketops.application.grading`` 这个名字不再与 ``grading.yaml`` 的
``schema: marketops/grading.v2`` 逐字对应。那一个是**契约标识符**（写进 profile、
会被下游引用），路径改它不会变，也不需要变 —— 契约名与模块路径从来不是一件事。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..domain.enums import (
    AccountStatus,
    EligibilityReason,
    EntryKind,
    Grade,
    GradeReason,
)
from ..domain.errors import GradingError
from ..domain.evidence import AccountRecord
from ..domain.entities import ContactRoute
from ..domain.facts import (
    ACCOUNT_FACT_NAMES,
    ACCOUNT_QUALIFICATION_FACT_NAMES,
    CANDIDATE_FACT_NAMES,
    ROUTE_FACT_NAMES,
    KIND_EQUALS,
    KIND_IN,
    parse_clause,
)
from ..domain.profile import ProfileBundle

#: 路线定级顺序。先 A 后 D —— 在 grading.yaml 里越靠前的等级优先级越高。
EVALUATION_ORDER: tuple[Grade, ...] = (Grade.A, Grade.B, Grade.C, Grade.D)

#: 引擎提供的**路线级**通用事实。词表本身在 ``marketops/domain/facts.py`` ——
#: 装载层要拿同一份来做 fail-closed 校验，因此不能在这里再写一遍。
FACT_NAMES: tuple[str, ...] = ROUTE_FACT_NAMES


@dataclass(frozen=True)
class GradeOutcome:
    """路线判定结果。``reason`` 与 ``detail`` 比 ``grade`` 本身更值得审计。"""

    grade: Grade
    reason: GradeReason
    detail: str = ""


@dataclass(frozen=True)
class AccountOutcome:
    """账号准入结果。

    ``unchecked`` 列出**未判**的排除规则 id，按它们被检查到的顺序。「未判」是
    一个形状，三种形态：``requires is None``、子句点名了引擎不认识的事实、
    或子句引用的事实**未确认**（键缺失 **或** 值 ``None`` —— 两者是一件事，
    见 :func:`evaluate_clause`）。

    **``unchecked`` 非空不蕴含 ``status == discovered``。** 循环会在第一条命中的
    规则处停下并连 ``unchecked`` 一起返回，所以一条排在命中规则**前面**的未判
    规则会留在 ``unchecked`` 里，而 ``status`` 是那条命中规则声明的状态。两者说
    的不是一件事：``status`` 是**结论**，``unchecked`` 是**结论旁边那些没被判的
    规则**。读成一件事，就会得出「有未判规则 ⇒ 没排除任何账号」这个错的推论。

    ``unchecked`` 为**空**时 ``status`` 也不必然是 ``discovered``：那表示排到
    结论为止的每一条规则都**判了**（命中，或确认为假），一条都没跳过。
    """

    status: AccountStatus
    reason: EligibilityReason
    detail: str = ""
    unchecked: tuple[str, ...] = ()

    @property
    def excludes(self) -> bool:
        """这个账号是否被规则明确排除。``hold`` 不算排除 —— 它是要人工看的。"""
        return self.status in (AccountStatus.IRRELEVANT, AccountStatus.DUPLICATE)


def route_facts(
    route: ContactRoute, account_domains: Iterable[str] = ()
) -> dict[str, object]:
    """把 ``ContactRoute`` 投影成通用事实。

    这是规则与数据之间唯一的翻译层。新增事实必须同时加入 ``FACT_NAMES``，
    否则 grading.yaml 里写错的事实名会被当作 False 而静默失效。

    ## 为什么收的是**域名集合**，不是 ``AccountRecord``

    这条判据要回答的只有一件事：「这个邮箱域是不是目标账号自己的域」。而
    ``AccountRecord`` 只是**今天**装这件事的那个对象 —— 池子里的账号是
    :class:`~marketops.domain.account.MarketAccount`，它有 ``domains``（复数，
    一个组织常有主站与分区站），没有 ``domain``。收 ``AccountRecord`` 的后果是
    两条生产路径喂进来的东西不一样：CLI 那条能给一个 ``AccountRecord``，而
    ``submit_findings`` 那条只有池子里的账号 —— 于是同一条路线在两个入口下
    判出不同的等级，而这在报告上只是「多了几个 X」。

    收集合还有一个当下的好处：``BindingSubject`` 把域规范化过一次
    （小写、去空白、去重、排序），两边读的是同一份规范化结果，这里不必再猜
    「调用方给我的那个是不是已经小写了」。
    """
    domains = {value.strip().lower() for value in account_domains if value and value.strip()}
    return {
        "has_literal_email": bool(route.email),
        "has_person": bool(route.person_name),
        "has_role": bool(route.role_title),
        "has_evidence": bool(route.evidence_ids),
        "has_conflict": bool(route.has_conflict),
        "email_domain_matches_account": bool(route.email_domain and route.email_domain in domains),
        "binding": route.binding.value,
        "entry_kind": route.entry_kind.value,
        "role_class": route.role_class.value,
        "business_function": route.business_function.value,
        # ``None`` 投影成 "unknown"：规则表里写 ``block_priority==P1`` 时，
        # 一条不属于任何记录块层级的路线不会因为「None 恰好等于 None」而命中。
        "block_priority": (
            route.block_priority.value if route.block_priority is not None else "unknown"
        ),
    }


def account_facts(account: AccountRecord) -> dict[str, object]:
    """把 ``AccountRecord`` 投影成账号级事实。只做字面投影，不做推断。

    键集**恒定** —— 恒等于 ``ACCOUNT_QUALIFICATION_FACT_NAMES``（账号自身字段 ∪
    候选事实）。候选事实（场地环境、表面类别、抓取统计那一组）在**账号这一侧**
    还没有生产者（``AccountRecord`` 上没有那些字段），因此一律取 ``None``。
    **它们从哪来**：候选行的字段名与这里的键**逐字同名**，投影那一步在
    ``marketops/application/qualify.py::candidate_facts``
    （``organisation_kind`` / ``venue_environment`` / ``surface_class`` /
    ``surface_evidence_confirmed`` 直接取字段，其余由同名列表与计数投影）。
    名字逐字同名就是为了让那一步「取错了哪个字段」在命名上无处可藏。
    两条路因此各有一份投影，但**只有一份求值循环**（见
    :func:`account_outcome_from_facts`）—— 投影是取值问题，循环是判定问题。

    为什么键集恒定，而不是「传了候选就多出一组事实」：那会让同一份
    ``grading.yaml`` 因为调用方式不同得出两个判定结果 —— 而账号资格判定**不得**
    取决于有没有候选对象。一个随输入伸缩的事实字典正好会违反它，只是形态变了：
    ``evaluate_clause`` 今天把**键缺失**与**值 ``None``** 读成同一件事
    （未确认 ⇒ 不决定子句 ⇒ 规则进 ``unchecked``），所以少一个键**不再**在判词
    上留下自己的痕迹 —— 它看起来与「这条事实的生产者还没落地」逐字相同，
    而后者是我们**在等**的东西，前者是我们要修的 bug。键集相同让「未确认」
    只剩一个来源：词表里那些还没有生产者的键。

    两支臂/两条路可比的前提正是「判定结果不随调用方式变」。这条不变量被
    :func:`account_outcome_from_facts` 开头变成硬失败
    （:func:`_require_the_qualification_key_set`）。

    这条不变量的**守卫**换了，值得写下来：「键缺失」与「值 ``None``」现在同判
    （都表示未确认 ⇒ 规则不决定 ⇒ 进 ``unchecked``），所以少一个键不再在判词上
    留下自己的痕迹。于是没有任何别的地方能把「调用方漏了一个键」与「这条事实的
    生产者还没落地」分开 —— 前者是 bug、后者是在等的状态，而两者在判词、
    在 ``unchecked``、在 ``detail`` 上逐字相同。键集恒定把「未确认」的来源收缩成
    一个可枚举的清单：恰好是词表里那些还没有生产者的名字。

    ``None`` 在这套方言里的语义是**未确认，因此不决定任何子句**（包括取反）。
    这个方向是刻意的：**没有证据就排除账号，比留着它危险得多**。反过来写
    （``None`` 当作命中，或取反成真）会让一批还没研究过的账号在没有任何证据的
    情况下被清出名单，而名单看起来只是「变小了」。

    这个 ``None`` 不是理论上的：``country`` 在 ``AccountRecord`` 上的缺省就是它
    （``domain/evidence.py``），因为那条字段的旧缺省 ``"UK"`` 恰好是一台
    **伪造证据**的机器 —— 一条没写国家的记录会让 ``out_of_region`` 静默不触发，
    而报告读成「在区域内」。

    **这条投影里 ``None`` 有两组来源，必须分开读**：账号自身的三个事实里，今天
    唯一可能没有值的就是 ``country``（另两条是 ``bool(...)``，恒不是 ``None``）；
    候选那七个（``**dict.fromkeys(...)``）**恒为 ``None``**，因为账号这一侧没有
    它们的生产者。前一组是「这条记录没写」，后一组是「这一层根本没有这个字段」——
    两者在判词上都落进 ``unchecked``，但修法完全不同。这句话在这里，是因为上面
    那句「键集恒定」把「谁今天可能没有值」收缩成了一份可枚举的清单。
    """
    return {
        "country": account.country,
        "has_domain": bool(account.domain),
        "has_notes": bool(account.notes),
        **dict.fromkeys(CANDIDATE_FACT_NAMES),
    }


def grade_route(
    route: ContactRoute,
    bundle: ProfileBundle,
    *,
    account_domains: Iterable[str] = (),
) -> GradeOutcome:
    """判定单条联系路线。

    ``account_domains`` 是目标账号自己的域（见 :func:`route_facts`）；默认空集
    = 「这个账号的域没有确认」，于是 ``email_domain_matches_account`` 为假 ——
    未确认不得让子句成立，与本仓其余各处同一条。
    """
    if route.has_conflict:
        return GradeOutcome(
            Grade.X,
            GradeReason.CONFLICTING_EVIDENCE,
            "同一文档中存在多个邮箱与姓名/角色证据，无法确定归属 —— 保守降级为 X",
        )

    if not route.evidence_ids:
        return GradeOutcome(
            Grade.X,
            GradeReason.NO_ELIGIBLE_EVIDENCE,
            "没有任何合格证据（SERP snippet 不算证据）",
        )

    if route.entry_kind is EntryKind.EXCLUDED:
        # 与 has_conflict 同样是**短路**：SOP v1 L129/L131 说的是类别不得进入
        # 有效主池，不是「降权后仍可能落进 C/D」。规则表里写不出来这件事 ——
        # 规则表求的是「命中哪一级」，这里否定的是「可以成为任何一级」。
        return GradeOutcome(
            Grade.X,
            GradeReason.EXCLUDED_ENTRY,
            f"岗位/入口属于排除类别（role_class={route.role_class.value}，"
            f"business_function={route.business_function.value}）—— 不进入有效主池",
        )

    defined = [
        grade
        for grade in EVALUATION_ORDER
        if (rule := bundle.rule_for(grade)) is not None and not rule.is_unknown
    ]

    if not defined:
        # 原因由 profiles 提供：引擎不猜「为什么没有规则」—— 可能业务没给，
        # 也可能业务给了而引擎求不了值。这两种对读审计的人是两件事。
        return GradeOutcome(
            Grade.X,
            GradeReason.RULES_UNKNOWN,
            "没有可求值的 A/B/C/D 判定规则，拒绝判定。"
            f"原因：{'；'.join(bundle.unknowns) or '未记录'}",
        )

    facts = route_facts(route, account_domains)
    unknown_clauses: list[str] = []

    for grade in defined:
        rule = bundle.rule_for(grade)
        # ``defined`` 已滤掉 is_unknown，因此这两项必然非 None；断言在此是为了
        # 让「判定时用到未知规则」变成显式失败，而不是被 None 悄悄当成空条件。
        assert rule is not None and rule.definition is not None and rule.requires is not None
        matched, bad_clauses = evaluate_requires(rule.requires, facts)
        unknown_clauses.extend(bad_clauses)
        if matched:
            return GradeOutcome(
                grade, GradeReason.RULE_MATCHED, f"{grade.value}: {rule.definition}"
            )

    detail = "没有等级规则匹配当前事实"
    if unknown_clauses:
        detail += f"；规则引用了未知事实：{', '.join(sorted(set(unknown_clauses)))}"
    return GradeOutcome(Grade.X, GradeReason.RULES_UNKNOWN, detail)


def grade_account(account: AccountRecord, bundle: ProfileBundle) -> AccountOutcome:
    """判定账号准入。**只**回答「它值不值得研究」，不碰任何路线等级。

    三条保守规则，一条规则只要落进任何一条就**不命中**并进 ``unchecked``：

    * ``requires is None`` ⇒ 规则压根没有可求值的子句；
    * 子句点名了事实字典里没有的事实 ⇒ 同上（:func:`evaluate_requires` 回报
      未知事实名）。所以「引擎不认识这个名字」与「调用方漏了一个键」在这里
      长得一样，键集恒定才分得开它们；
    * 子句引用的事实**未确认** ⇒ 同上。**这是第三条的独立价值**：前两条是
      「引擎没有这个事实」，这一条是「引擎有这个事实、也认识这个名字，但今天
      没有值」—— 而它同样**不决定**子句，在任何极性下都不。

    第三条是**值**的形态，前两条是**名**的形态；判词是同一种
    （``UNRESOLVED_FACTS``），差别在 ``detail`` 与 ``unchecked`` 的读法上。

    ``discovered`` 的含义是「没有证据把它移开」。调用方据此保留账号自身的
    ``status``（``researched`` / ``archived`` 这类由流程推进的状态不该被这次
    判定退回）—— 所以本函数是纯函数，不写回任何东西。

    ``discovered`` 的**判词**要分开读，它们说的是两件不同的事：
    ``UNRESOLVED_FACTS`` 是「有规则没判」（``unchecked`` 非空），
    ``NO_RULE_FIRED`` 是「排到结论为止每一条都判了，一条都没命中」。后者才是
    「核对过，确实没有」；前者**不是** —— 它不排除账号，但那是因为没有证据，
    不是因为核对过。
    """
    return account_outcome_from_facts(account_facts(account), bundle)


def _require_the_qualification_key_set(facts: Mapping[str, object]) -> None:
    """两条路交给求值循环的事实键集必须**相同** —— 这里把它变成硬失败。

    ``evaluate_clause`` 今天把**键缺失**与**值 ``None``** 读成同一件事
    （未确认 ⇒ 子句不决定 ⇒ 规则进 ``unchecked``、判词 ``UNRESOLVED_FACTS``）。
    所以这条不变量**不再是**判定语义本身的前提 —— 同一个事实不会再因为
    「调用方式」不同得出两个判定结果，两条路对同一个 ``None`` 做的是同一件事。

    它仍然被留着，而且地位比之前更重，理由换了：**现在没有任何别的地方能把
    「调用方漏了一个键」与「这条事实的生产者还没落地」分开。** 前者是 bug、
    后者是我们在等的状态，而两者在判词、在 ``unchecked``、在 ``detail`` 上
    逐字相同。键集相等把「未确认」的来源收缩成一个可枚举的词表：未确认的键
    恰好是 ``ACCOUNT_QUALIFICATION_FACT_NAMES`` 里取值仍为 ``None`` 的那些。
    少了这个约束，一个拼错的键会让一条规则永远停在「等生产者」那个状态里，
    而它等的东西根本不会来。

    两支臂可比的全部意义是判定结果不随调用方式变；这条断言现在是它的**守卫**，
    不再是它的实现。少一个键在这里抛，比在报告里变成一行看起来正常的判词便宜。
    """
    expected = set(ACCOUNT_QUALIFICATION_FACT_NAMES)
    actual = set(facts)
    if actual == expected:
        return
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    raise GradingError(
        "账号准入的事实字典键集必须等于 ACCOUNT_QUALIFICATION_FACT_NAMES："
        f"缺 {missing or '无'}；多 {extra or '无'}。"
        "键缺失与值为 None 现在同判（未确认 ⇒ 规则不决定 ⇒ 进 unchecked），"
        "所以漏一个键与「这条事实还没有生产者」在判词上无法区分 ——"
        "这正是必须在这里抛的原因。"
    )


def account_outcome_from_facts(
    facts: Mapping[str, object], bundle: ProfileBundle
) -> AccountOutcome:
    """账号准入的**求值循环** —— 账号路径与候选路径唯一的一份。

    :func:`grade_account`（账号行）与候选路径共用它：两条路因此跑同一个
    判定引擎，判定结果只取决于**事实值**与 ``profiles/*/grading.yaml``，
    不取决于「这一行是账号还是候选」。候选那一侧的调用点是
    ``marketops/application/qualify.py::qualify_candidate``，它把
    ``candidate_facts`` 的投影喂进来 —— 两份投影、一份循环，这条分工
    由 ``tests/test_discovery_qualify.py::test_both_paths_call_the_one_shared_loop``
    盯着（它测的是**形状**：两个调用点确实都调到了这**一个**函数）。

    **不推断、不加规则、不认识任何取值**：规则只来自 ``bundle.account_rules``；
    这个函数不认识 ``muga`` 或任何具体取值 —— 那些住在 ``profiles/*/``。

    三条保守规则（原文见 :func:`grade_account`），一条规则只要落进任何一条就
    **不命中**并进 ``unchecked``：

    * ``requires is None`` ⇒ 规则压根没有可求值的子句；
    * 子句点名了事实字典里没有的事实 ⇒ 同上（:func:`evaluate_requires` 回报
      未知事实名）。所以键集必须先被 :func:`_require_the_qualification_key_set`
      卡住，否则「引擎不认识这个名字」与「调用方漏了一个键」在这里长得一样；
    * 子句引用的事实**未确认**（值 ``None``）⇒ 同上。**这是第三条的独立价值**：
      前两条是「引擎没有这个事实」，这一条是「引擎有这个事实、也认识这个名字，
      但今天没有值」—— 而它今天同样**不决定**子句，在任何极性下都不。

    第三条是**值**的形态，前两条是**名**的形态；判词是同一种（``UNRESOLVED_FACTS``），
    差别在 ``detail`` 与 ``unchecked`` 的读法上：``unchecked`` 里的规则是
    「我们**没有**拿证据核对过它」，不是「核对过、它不命中」。这两件事在报告上
    必须分得开，因为后者是结论、前者只是还没结论 —— 见 ``EligibilityReason``。
    """
    _require_the_qualification_key_set(facts)
    unchecked: list[str] = []

    for rule in bundle.account_rules:
        if rule.requires is None:
            unchecked.append(rule.id)
            continue
        matched, bad_clauses = evaluate_requires(rule.requires, facts)
        if bad_clauses:
            # 未判：规则没写 requires，或子句点名了引擎不认识 / 今天没有值的事实。
            # 三种形态同判 —— 「求不了值」不是三个理由，是一件事。
            unchecked.append(rule.id)
            continue
        if matched:
            return AccountOutcome(
                rule.status,
                EligibilityReason.RULE_FIRED,
                f"{rule.id}: {rule.description}" if rule.description else rule.id,
                tuple(unchecked),
            )

    if unchecked:
        return AccountOutcome(
            AccountStatus.DISCOVERED,
            EligibilityReason.UNRESOLVED_FACTS,
            "账号排除规则引用了尚无生产者的事实，拒绝据此排除："
            + "、".join(unchecked),
            tuple(unchecked),
        )

    return AccountOutcome(
        AccountStatus.DISCOVERED,
        EligibilityReason.NO_RULE_FIRED,
        "没有账号准入规则命中 —— 保持 discovered，不据此推进状态",
    )


def evaluate_requires(
    requires: tuple[str, ...], facts: Mapping[str, object]
) -> tuple[bool, list[str]]:
    """求值 ``requires``（合取）。返回 (是否全部满足, **未确认**的事实名列表)。

    子句方言在 ``marketops/domain/facts.py`` —— 与装载层校验用的是同一份解析。

    每条子句的结果是**三值**的：真 / 假 / 未确认（见 :func:`evaluate_clause`）。
    合取因此不是 ``and`` 那么简单：

    ==========================  ==============================  ===================
    各子句的取值                 整条 ``requires``               返回值
    ==========================  ==============================  ===================
    有任一条**确认为假**         假 —— 已被决定，翻不过来         ``(False, [])``
    没有假、有未确认             未确认（算不出真假）             ``(False, [名字…])``
    全部确认为真                 真                              ``(True, [])``
    ==========================  ==============================  ===================

    **「确认为假」压过「未确认」**：``False and 未确认 == False``，一条已确认为假
    的子句已经决定了整条合取。这时**不**回报后面的未确认事实名 —— 那不是丢信号：
    第二个返回值的唯一用途是让调用方把这条规则记进 ``unchecked``（「这条规则
    没判」），而这条规则**判了**，判词是「不命中」。把它也记进 ``unchecked``，
    ``unchecked`` 就不再是那个能分开「判为假」与「没判」的信号。

    **未确认不短路**：一条子句未确认时继续看后面的子句，把未确认的事实名收全。
    在第一个未确认处停下也能得到「规则不命中」，但那时 ``unchecked`` 记的是
    「碰巧排在第一条的那个事实」—— 一个随子句顺序漂移的任意子集。这个列表是
    这一层唯一能分开「判为假」与「没判」的信号，它必须由**事实**决定，
    不能由**子句顺序**决定。

    未知事实名一律记为「未满足」**并回报**，而不是静默当作 False ——
    grading.yaml 里写错一个词不应该变成「这条规则永远不生效」的静默故障。
    """
    unresolved: list[str] = []
    for clause in requires:
        satisfied, unconfirmed = evaluate_clause(clause, facts)
        if unconfirmed is None and not satisfied:
            # 已确认的子句明确为假 —— 整条合取已定，取反与否都翻不过来。
            return False, []
        if unconfirmed is not None:
            unresolved.append(unconfirmed)
    if unresolved:
        return False, unresolved
    return True, []


def evaluate_clause(clause: str, facts: Mapping[str, object]) -> tuple[bool, str | None]:
    """求值一条子句。返回 (子句是否为真, **未确认**的事实名)。

    **未确认 = 键缺失 或 值 ``None``**，两者是**一件事**，因此走同一条分支：
    子句**在任何极性下都不决定**（既不为真也不为假），回报 ``(False, 事实名)``。
    「不决定」在第一个返回值上长得像「假」，因为调用方只能用合取去用它；
    分得开这两件事的是第二个返回值 —— 它让引用它的规则落进 ``unchecked``
    （「我们没拿证据核对过它」），而不是落进「核对过、不命中」。

    **为什么值 ``None`` 不是「假」。** ``""`` / ``[]`` / ``False`` / ``0`` 是
    **证据**（「记了，是没有 / 没有命中」），``None`` 是**还没有证据**（「还没查」）。
    前者取反是一次真的取反，后者取反什么也做不了 —— 把未确认取反成真，正是
    「没有证据就排除账号」那条禁忌的入口：``grading.yaml`` 里的
    ``!country in [gb, uk]`` 是一条**取反且没有正面守卫**的规则，而 ``country``
    在候选侧没有生产者。旧行为把「还没查区域」取反成「确认越界」，于是**每一行**
    候选都被判成 ``irrelevant`` —— 报告上看起来只是「候选都不合格」。

    **已确认的空值照旧求值**：它们**是**证据，必须继续可判、可被取反。把
    「记了是没有」与「还没查」合成一件事，是同一个错误的另一半 —— 它会让一条
    确认不成立的排除规则永久静默，而那种静默与「这条规则本来就不该命中」逐字相同。

    ``None`` **不能**换成 ``"unconfirmed"`` 这类非空字符串哨兵：那是一个**已确认**
    的值（``bool("unconfirmed") is True``），会让排除规则误触发 —— 也就是说它把
    「还没查」重新读成「查过了」。

    **这条路今天已经有消费者了**：``account_facts`` 的 ``country`` 在
    ``AccountRecord`` 没写国家时就是 ``None``（那条字段的缺省从 ``"UK"`` 改成了它，
    见 ``domain/evidence.py``）。所以 ``grading.yaml`` 的 ``!country in [gb, uk]``
    对一条没写国家的记录**不决定**，规则落进 ``unchecked`` —— 那是能被看见的
    「没人查过」，而缺省 ``"UK"`` 给出的是一条被判为「在区域内」的静默放行。
    **路线侧**的投影今天仍不产出 ``None``，那条没有消费者的路照旧留着判据，
    它守的是路线侧事实生产者落地那天。
    """
    parsed = parse_clause(clause)
    if parsed is None:
        # 语法不合法。装载层已经拦过一次；这里兜底是因为 ``requires`` 也可能
        # 由调用方直接构造（测试、未来的其他 loader）。
        return False, clause.strip() or clause

    if parsed.fact not in facts or facts[parsed.fact] is None:
        return False, parsed.fact

    actual = facts[parsed.fact]
    if parsed.kind is KIND_IN:
        # 空取值集合是「谁都不属于」，不是「谁都属于」。
        result = str(actual).strip().lower() in parsed.values
    elif parsed.kind is KIND_EQUALS:
        result = str(actual).strip().lower() == parsed.values[0]
    else:
        result = bool(actual)

    return (not result if parsed.negate else result), None


__all__ = [
    "GradeOutcome",
    "evaluate_clause",
    "evaluate_requires",
    "AccountOutcome",
    "grade_route",
    "grade_account",
    "account_outcome_from_facts",
    "route_facts",
    "account_facts",
    "FACT_NAMES",
    "ACCOUNT_FACT_NAMES",
    "EVALUATION_ORDER",
]

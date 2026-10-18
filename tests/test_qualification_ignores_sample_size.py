"""**低样本不改变客户资格** —— 计数（样本量）不得进入资格判定。

B16 关闭条件 5 的后半句逐字（``docs/B16_ACCEPTANCE_MATRIX_2026-09-28.md:415``）：

    ### 关闭条件 5 · Source yield 变化只能改批准范围内排序；低样本不会自动拆 Scene
    或改变客户资格

矩阵给它记的机制（``:433``）是「**类型闭集上成立**」：资格判定链上样本量只以三态
进入，``application/qualify.py::_any`` 把两个计数折成「有没有」，事实键集是闭集。

**机制那一半今天是真的，但今天没有任何一条判据会在「有人把整数计数接进资格判定」
时变红** —— 矩阵结尾那句「而那一步会红」实测不成立。今天在守的四条都是**漂移守卫**
（防「只改了一半」：词表与两条投影一起长就绿），不是**意图守卫**。所以这一格是
「今天如此」，不是「被守住」—— 与矩阵 ``:667`` 那句纪律（「一个只靠『那条路还不存在』
成立的约束，不算被守住」）是同一句话。

这一条把「今天如此」变成「被守住」。判据的形状照
``tests/test_research_strategy.py::test_only_the_mcp_tools_can_write_a_scene_record``
那个**派生闭集**样板，做的是它的**字段版**：

1. **计数字段集是派生的**（:func:`_count_fields`）：扫 ``AccountCandidate`` 的模型字段
   注解，取**形状上收整数**的那些。判据里**不写** ``pages_fetched`` / ``pages_blocked``
   任何一个名字 —— 明天有人给候选行加一个 ``page_count: int | None``，它自动进射程。
2. **取值不变性**（本片的承重判据）：对每一个派生出来的字段，把它的取值在**任意两个
   量级**之间换（1 / 2 / 3 / 7 / 100 / 10**6）之后，事实字典与两条路给出的
   ``AccountOutcome`` 都不许变。这比「键集相等」强的地方在于它判的是**值**：
   一个整数键接进来，键集相等照样绿，这里会红。
3. **未确认是第三个类别，单独断言**（:func:`test_the_unconfirmed_count_is_not_the_confirmed_zero`）：
   ``None`` 与 ``0`` 今天**不同判**（未确认 ⇒ 规则不决定；记了是 0 ⇒ 确认为假），
   那是 ``_any`` 的**承重行**，**不是回归**。

# 两条路各测一半，理由分开写

* **候选路**（``qualify_candidate``）是计数真正待着的那条路：字段在候选行上，投影在
  ``application/qualify.py::candidate_facts``。它的不变性是**实测**的。
* **账号路**（``grade_account`` / ``application/grading.py::account_outcome_from_facts``）
  今天**结构上看不见计数**：计数字段不在 ``AccountRecord`` 上，``account_facts`` 对那
  几个键一律取 ``None``。所以它的那一半是**结构断言**（计数字段与账号行字段不相交、
  账号行上没有任何整数形状的字段、账号侧那几个键恒为未确认），外加一次**引擎级**的
  实测（把候选侧的计数事实嫁接进账号侧的事实字典，判定仍然不随量级动）—— 不是
  「跑一遍 grade_account 看它变不变」，那样跑出来的是一句空话。账号行那一条**独立于
  候选行的派生**：它按注解形状扫 ``AccountRecord`` 自己的字段，所以一个换了个名字的
  ``page_count: int | None`` 落在账号行上也会红。

# 本判据**判不了**的两处（明写，免得被读成「样本量永远进不了判定」）

* **量级恒定、类别可变的投影抓不住**：``bool(count)`` 这种形状在量级上恒等，
  所以取值不变性对它绿 —— 而那也是**对的**，它确实把量级丢了，资格不可能随样本量动。
  这一条抓的是「**量级**能改变判定」。
* **阶梯是有限的**：1 到 10**6。一条阈值高于 10**6 的投影（``count > 10**9``）不会被
  这组取值分开。扩展办法是往 :data:`_MAGNITUDES` 里加取值，不是缩小它。
* **派生只看形状，不看语义**：计数字段集派生自 ``AccountCandidate`` 的注解，所以一个
  **不是整数形状**的样本量载体（``str`` 计数、``Decimal``、``list[page]``）落在候选行上
  时，它既不进这个集，这一条也不判它。今天两个计数都是 ``int | None``，所以它不是
  当下的缺口；它是一条**边界**，写在这里免得这条判据被读成「样本量永远进不了判定」。
"""

from __future__ import annotations

from typing import Any

from marketops.application.grading import (
    AccountOutcome,
    account_facts,
    account_outcome_from_facts,
)
from marketops.application.qualify import candidate_facts, qualify_candidate
from marketops.domain.candidate import AccountCandidate, new_candidate
from marketops.domain.enums import AccountStatus, Cohort, EligibilityReason, Grade
from marketops.domain.evidence import AccountRecord
from marketops.domain.facts import parse_clause
from marketops.domain.profile import AccountRule, GradingRule, ProfileBundle
from marketops.domain.sources import SOURCE_METHODS
from tests.fakes import synthetic_scene_files

#: 量级阶梯。它的作用是「同样非零，换一个量级」。
#:
#: 它**不含** ``None``，也**不含** ``0``：那两个是**另外两个类别**，不是两个量级 ——
#: 把它们混进来，就等于宣称「未确认与记了是 0 同判」，而 ``_any`` 的承重行说的恰好
#: 是相反的。三态各自的样子见
#: :func:`test_the_unconfirmed_count_is_not_the_confirmed_zero`。
_MAGNITUDES: tuple[int, ...] = (1, 2, 3, 7, 100, 10**6)

#: 取值的**三个类别**：未确认 / 记了是 0 / 记了非零。用它派生「这个字段的取值能碰到
#: 事实字典里的哪些键」—— 那是「这个计数有没有被投影」的行为判据。
_CLASS_VALUES: tuple[object, ...] = (None, 0, 1)


# --- 派生 ---------------------------------------------------------------------


def _union_members(annotation: Any) -> tuple[Any, ...]:
    """一个注解的成员。

    ``int | None`` 是 ``types.UnionType``、``Optional[int]`` 是 ``typing.Union``，
    两者的 ``__args__`` 都是成员元组；非联合注解的成员就是它自己。两者都要认 ——
    只认一种写法，另一种形状的新字段会**静默**落在射程外，而那种漏掉没有任何症状。
    """
    members = getattr(annotation, "__args__", None)
    return tuple(members) if members else (annotation,)


def _admits_an_integer(annotation: Any) -> bool:
    """注解**形状上收整数**吗。

    ``bool`` 明确排除，而且必须按 **``is``** 排：``bool`` 是 ``int`` 的子类，
    一条 ``issubclass`` 式的判断会把 ``surface_evidence_confirmed: bool | None``
    一起吞进来，而那一格是**三值判定**（还没查 / 查到了 / 查过且没有），不是计数 ——
    把它当成计数，这一条判据就会去断言「判定不看那一格」，而那件事是**假**的。
    """
    for member in _union_members(annotation):
        if member is bool:
            continue
        if isinstance(member, type) and issubclass(member, int):
            return True
    return False


def _count_fields() -> tuple[str, ...]:
    """``AccountCandidate`` 上**形状是计数**的字段，**派生**出来的。

    派生源是模型字段的**注解**（``marketops/domain/candidate.py``），不是一份名单。
    名单是「今天有这两个」的记录；这里要的是闭集 —— 明天有人加一个
    ``page_count: int | None``，它必须自动进下面每一条判据的射程，不需要谁记得回来改
    测试。今天派生出来的是 ``pages_fetched`` / ``pages_blocked``（本条判据里没有一处
    写它们的名字，这正是派生非空转那条实测要验的事）。
    """
    return tuple(
        name
        for name, field in AccountCandidate.model_fields.items()
        if _admits_an_integer(field.annotation)
    )


def _bool_shape_fields() -> tuple[str, ...]:
    """注解里带 ``bool`` 的字段。派生它只为一件事：**反控派生本身在判形状**。

    ``bool`` 是 ``int`` 的子类，所以「按形状收整数」这条派生有一个最自然的写错法
    （``issubclass`` 而不是 ``is``），它会把三值判定那一格吞进计数集。这条字段集非空，
    下面那条「两个集不相交」才有分量；一个空集会让它空转。
    """
    return tuple(
        name
        for name, field in AccountCandidate.model_fields.items()
        if any(member is bool for member in _union_members(field.annotation))
    )


# --- 夹具 ---------------------------------------------------------------------


def _candidate(**overrides: object) -> AccountCandidate:
    """一条最小可构造的候选。

    走 ``new_candidate``（注册表成员）而不是裸构造，理由与别处相同：生产者路径长什么样，
    测试就长什么样。
    """
    return new_candidate(
        SOURCE_METHODS["search.engine.v1"],
        candidate_id="probe-candidate-001",
        organisation="Example Sports Trust",
        venue_name="Example Recreation Ground",
        **overrides,
    )


def _account(**overrides: object) -> AccountRecord:
    """一条账号行（与 ``tests/test_discovery_qualify.py`` 同形）。"""
    payload: dict[str, object] = {
        "account_id": "probe-account-001",
        "organisation": "Example Sports Trust",
        "cohort": Cohort.KNOWN_SUCCESS,
        "country": "UK",
    }
    payload.update(overrides)
    return AccountRecord(**payload)


def _bundle(*, account_rules: tuple[AccountRule, ...] = ()) -> ProfileBundle:
    """一个规则可控的合成 bundle（形状照 ``tests/test_discovery_qualify.py`` 的先例）。

    刻意**合成**：真实 profile 的规则是业务事实的一部分，把它当输入会让「引擎读不读
    这个计数」与「这份 SOP 写了什么」纠缠在一起。真实 profile 上的判定由读 ``bundle``
    夹具的那几条测。
    """
    rules = {grade: GradingRule(grade=grade, definition=None, requires=()) for grade in Grade}
    scene_files = synthetic_scene_files()
    return ProfileBundle(
        profile_id="probe",
        status="PROBE",
        rules=rules,
        role_terms=(),
        scene_version=scene_files.version,
        scene_files=scene_files,
        unknowns=tuple(f"grading.{grade.value}" for grade in Grade),
        account_rules=account_rules,
    )


# --- 读数 ---------------------------------------------------------------------


def _facts_for(field: str, value: object) -> dict[str, object]:
    """把 ``field`` 置成 ``value`` 的那条候选，投影出来的事实字典。"""
    return candidate_facts(_candidate(**{field: value}))


def _keys_that_move(field: str, values: tuple[Any, ...]) -> frozenset[str]:
    """把 ``field`` 在这几个取值上换一圈，事实字典里**动过**的键。

    **派生自行为**，不是一份写死的映射表：不问「``pages_fetched`` 投影到哪个事实名」，
    只问「换它的取值，字典里哪个键跟着动」。所以它同时是「这个计数有没有被投影」
    （动过任何键）与「量级有没有漏进字典」（在量级上动过键）的判据 ——
    将来有人把计数接到另一个键上，这里跟着走。
    """
    views = [_facts_for(field, value) for value in values]
    moved: set[str] = set()
    for left in views:
        for right in views:
            moved |= {key for key in left if left[key] != right[key]}
    return frozenset(moved)


def _keys_reached_by_counts() -> frozenset[str]:
    """所有派生计数字段在**三个类别**上碰到的事实键 —— 计数的全部入海口。"""
    reached: set[str] = set()
    for field in _count_fields():
        reached |= _keys_that_move(field, _CLASS_VALUES)
    return frozenset(reached)


def _rules_referencing(bundle: ProfileBundle, names: frozenset[str]) -> frozenset[str]:
    """``requires`` 里点了这些事实名的规则 id。派生自子句解析 —— 不写死规则名。"""
    found: set[str] = set()
    for rule in bundle.account_rules:
        for clause in rule.requires or ():
            parsed = parse_clause(clause)
            if parsed is not None and parsed.fact in names:
                found.add(rule.id)
    return frozenset(found)


def _outcome_fields(outcome: AccountOutcome) -> tuple[object, ...]:
    """判定结果**逐字段**摊开：四个字段各自比，不靠 ``__eq__``。

    整对象 ``==`` 在今天是等价的（frozen dataclass），但「逐字段相等」这句话要的是
    「四个字段各自相等」：哪天有人给 ``AccountOutcome`` 加一个字段，逐字段比会**看见**
    它，``==`` 也会 —— 而哪天有人给它写一个自定义 ``__eq__``（把 detail 排除掉之类），
    ``==`` 就不再是这句话了，逐字段比还是。
    """
    return (outcome.status, outcome.reason, outcome.detail, outcome.unchecked)


# --- 1. 派生出来的集非空，而且真的在判形状 ------------------------------------


def test_the_count_field_set_is_derived_and_not_empty() -> None:
    """计数字段集是**派生**的、非空，而且派生真的在看注解形状。

    非空是承重的：一个空的派生集会让下面每一条取值不变性**在空气上成立** ——
    那些断言全绿，而它们绿的原因是「没有东西可测」。所以这里先钉住它非空。

    反控三处。**第一处**：``bool`` 形状的字段集非空（否则下面那条不相交的断言空转）。
    **第二处**：两个集不相交 —— 钉住派生按 ``is`` 判而不是按 ``issubclass`` 判；
    ``bool`` 是 ``int`` 的子类，写错的那一次会把三值判定那一格吞进计数集。
    **第三处**：派生集是全部字段的**真子集** —— 一条「把所有字段都收进来」的派生
    也能让前两条成立，而它对任何字段都不判事。

    这里**不写**「今天恰好两个」：那个数字每加一个字段就要改一次，改的那次是纯粹的
    复述，而它给的保护（「有人删掉一个字段」）由下面 `_keys_reached_by_counts` 的非空转
    覆盖得更准 —— 删掉一个**被投影的**字段会让事实字典少一个键，那一条会红。
    """
    counts = _count_fields()
    assert counts, (
        "一个计数字段都没派生出来 —— 下面每一条取值不变性都会在空气上成立。"
        "派生看的是 AccountCandidate 模型字段的注解形状（收整数的那些）。"
    )
    bools = _bool_shape_fields()
    assert bools, (
        "反控空转：AccountCandidate 上一个 bool 形状的字段都没有，"
        "「两个集不相交」这句话就没有分量"
    )
    assert not (set(counts) & set(bools)), (
        f"bool 形状的字段被当成计数派生进来了：{sorted(set(counts) & set(bools))}。"
        "bool 是 int 的子类 —— 派生必须按 `is` 判，不能按 `issubclass` 判。"
    )
    assert len(counts) < len(AccountCandidate.model_fields), (
        f"派生集等于全部字段（{len(counts)} 个）—— 它没有在判形状，只是在数数"
    )
    for name in counts:
        assert _admits_an_integer(AccountCandidate.model_fields[name].annotation), (
            f"{name!r} 的注解形状上不收整数，却被派生成计数了"
        )


# --- 2. 承重判据：量级进不了事实字典 ------------------------------------------


def test_a_count_field_magnitude_never_reaches_the_fact_dictionary() -> None:
    """计数的**量级**不许出现在事实字典里 —— 判定读的就是这个字典。

    这条是本片的承重判据。它比「键集相等」强的地方是**它判值**：把整数计数当成一个
    新事实键接进来（键集跟着一起长，四条漂移守卫全绿），只要那个键的值带着量级，
    这里当场红。它也比「两条路的判定结果相等」强：判定只看得见字典，而字典相等蕴含
    判定相等 —— 反过来不成立，一条只读 ``!any_page_fetched`` 的规则表对
    ``True`` 与 ``100`` 给出同一个判定，于是**判定相等在量级上根本分不出来**
    （实测读数见交接报告：``_any`` 换成裸计数以后，四条相关判据与判定结果全绿，
    只有这一条红）。

    **非空转**：至少有一个派生字段的取值在三个类别上能碰到事实字典
    （``_keys_reached_by_counts()`` 非空）。它空着的意思不是「计数不会影响判定」，
    而是「计数根本没被投影」—— 那时上面那圈断言对空气成立，绿得毫无意义。

    反面（拿掉守卫就要红的反控）是把 ``application/qualify.py`` 的
    ``_any(candidate.pages_fetched)`` 换成裸计数：这一条红，其余几条绿。
    """
    fields = _count_fields()
    reached = _keys_reached_by_counts()
    assert reached, (
        f"非空转：派生出来的计数字段 {fields} 在三个类别上都没有让事实字典动过任何键 ——"
        "计数没有被投影进判定，下面那圈断言在空气上成立"
    )

    for field in fields:
        moved = _keys_that_move(field, _MAGNITUDES)
        assert not moved, (
            f"{field!r} 的**量级**递进了事实字典：换一个非零取值就动了 {sorted(moved)}。"
            "计数只以「有没有」进入判定（application/qualify.py::_any）；量级一旦进得来，"
            "一条 `in [1, 2, 3]` 形状的规则就能让样本量改变客户资格 ——"
            "而那也是 B16 关闭条件 5 明文禁止的那件事。"
        )


# --- 3. 两条路：判定不随量级动 -------------------------------------------------


def test_both_paths_give_the_same_outcome_for_every_count_magnitude(
    bundle: ProfileBundle,
) -> None:
    """**两条路**对每一个量级给出的 ``AccountOutcome`` **逐字段相等**。

    候选路这一半是实测的：同一个候选，把每个派生计数字段从 1 换成 10**6（中间还有
    2/3/7/100），``qualify_candidate`` 给出的四个字段逐字相同；而且它**等于**把同一组
    事实直接交给共享求值循环的结果 —— 候选路没有在循环之外动过结果（补一句判词、
    把未判算成不命中，都在这里响）。

    账号路那一半的**实测**在 :func:`test_the_account_path_cannot_see_a_count`：
    ``grade_account`` 收的是 ``AccountRecord``，而计数字段不在那张行上 ——
    所以要「把同一个计数交给账号路」只能靠往事实字典里嫁接，见那一条。

    这条**不比**「一条账号行与一条候选行给出同一个结论」：那是取值问题不是引擎问题 ——
    两条路的投影今天在某些键上取值不同（未确认 vs 已确认），结论因此可以合法地不同。
    """
    for field in _count_fields():
        reference_row = _candidate(**{field: _MAGNITUDES[0]})
        reference = _outcome_fields(qualify_candidate(reference_row, bundle).outcome)
        for magnitude in _MAGNITUDES:
            row = _candidate(**{field: magnitude})
            outcome = qualify_candidate(row, bundle).outcome
            assert _outcome_fields(outcome) == reference, (
                f"{field!r} 换成 {magnitude} 之后判定变了：{_outcome_fields(outcome)} != "
                f"{reference}。样本量改变了客户资格。"
            )
            assert outcome == account_outcome_from_facts(candidate_facts(row), bundle), (
                f"{field!r}={magnitude} 时候选路径在共享求值循环之外动了结果 ——"
                "判定不再是事实的函数了"
            )


# --- 4. 账号路：结构上看不见计数，引擎级也不随量级动 ---------------------------


def test_the_account_path_cannot_see_a_count(bundle: ProfileBundle) -> None:
    """账号路今天**结构上**看不见计数 —— 而且把计数硬塞给它，它也不随量级动。

    四件事，分开说，因为它们的失败方式不同：

    1. **计数字段不在 ``AccountRecord`` 上**。``grade_account`` 只收那一张行；一个计数
       字段落在它上面，账号路立刻对样本量敏感 —— 这一步是结构断言，也是这条路上唯一
       「有人接进来就会红」的地方。
    2. **账号行上一个整数形状的字段都没有**（同样是派生出来的，不是名单）：今天
       ``AccountRecord`` 的 7 个字段里整数形状的是 0 个。一个整数落在这一行上**不必然**
       改变判定（``account_facts`` 今天没有任何一格读整数），所以这句话是有意收在
       「形状」这一层的：它报的是「样本量进资格判定的下一步已经出现」，不是「已经漏了」。
       第 1 条只在**同名**时红，这一条不是 —— 换个名字的 ``page_count`` 也拦住。
    3. **账号侧对计数的入海口恒为未确认**（``account_facts`` 对候选那一组键一律取
       ``None``，见 ``application/grading.py`` 的原文）。入海口之一的键一旦有了值，
       账号路就开始读样本量。
    4. **引擎级的实测**：把候选侧的计数事实嫁接进账号侧的事实字典（键集不变，仍是那个
       闭集），交给账号路的求值循环，判定在 1 与 10**6 上逐字段相同。第 1、3 条是
       「今天看不见」，第 4 条是「就算递到手上也不分」。只写前几条的话，
       这里量的是「那个字段还不存在」—— 正是矩阵 ``:667`` 点名的、
       **不算被守住**的那种约束。
    """
    counts = _count_fields()
    overlap = set(counts) & set(AccountRecord.model_fields)
    assert not overlap, (
        f"计数字段跑到账号行上了：{sorted(overlap)}。grade_account 只看得见 AccountRecord ——"
        "计数字段落在它上面，账号路立刻对样本量敏感。"
    )

    integer_shaped_row_fields = [
        name
        for name, field in AccountRecord.model_fields.items()
        if _admits_an_integer(field.annotation)
    ]
    assert not integer_shaped_row_fields, (
        f"账号行上出现了整数形状的字段：{integer_shaped_row_fields}。账号行（"
        f"{len(AccountRecord.model_fields)} 个字段）今天一个整数形状的字段都没有，"
        "``account_facts`` 里也没有任何一格读整数 —— 一个整数落在这一行上就是"
        "「样本量进客户资格」的下一步：要么把它挪回候选行，要么在报告里说清"
        "它为什么不可能被任何规则读到（这一条报的是形状，不是已经漏了）。"
    )

    reached = _keys_reached_by_counts()
    assert reached, "非空转：计数没有被投影，下面两条对账号路的断言都在空气上成立"

    facts = account_facts(_account())
    for key in sorted(reached):
        assert facts[key] is None, (
            f"账号侧对 {key!r} 有了生产者（读到 {facts[key]!r}）。这一组键在账号行上"
            "没有生产者（application/grading.py::account_facts 逐字写着它恒为 None）——"
            "它一旦有值，账号路就开始读样本量，而那条路上根本没有样本量可言。"
        )

    def grafted(magnitude: int) -> dict[str, object]:
        """账号侧的事实 + 候选侧在这个量级上的计数事实（键集不变）。"""
        counts_view = {
            key: _facts_for(field, magnitude)[key]
            for field in counts
            for key in _keys_that_move(field, _CLASS_VALUES)
        }
        return {**facts, **counts_view}

    reference = _outcome_fields(account_outcome_from_facts(grafted(_MAGNITUDES[0]), bundle))
    for magnitude in _MAGNITUDES:
        assert _outcome_fields(account_outcome_from_facts(grafted(magnitude), bundle)) == reference, (
            f"账号路的求值循环对量级 {magnitude} 给出了不同的判定 ——"
            "两条路共用的那个循环，在计数上不再只问「有没有」"
        )


# --- 5. 未确认不是「记了是 0」—— 单独断言，别混进取值不变性 ---------------------


def test_the_unconfirmed_count_is_not_the_confirmed_zero(bundle: ProfileBundle) -> None:
    """``None``（未确认）与 ``0``（记了，是 0）**不同判** —— 这是正确行为，不是回归。

    这一条与 :data:`_MAGNITUDES` 分开，是刻意的：取值不变性那几条里的取值全是**非零**，
    因为 ``None`` 与任何一个数都**不该**同判。``application/qualify.py::_any`` 的承重行
    是 ``return None if count is None else count > 0``：把 ``None`` 折成 ``False``，会把
    「还没查」读成「查了，是 0」，而后者是一条结论 —— 一条还没核对的排除规则会在报告上
    变成「核对过、不命中」。所以这条断言不是「容差」，它钉的正是那一行。

    三段：

    * **真实 profile**：未确认时，引用计数的规则**一条都没判**（全部落在 ``unchecked``）；
      记了是 0 时，那些规则**判了**（不命中，也不进 ``unchecked``）。而且两者的差别**恰好**
      是那些规则 —— 多一条、少一条都说明有别的东西在跟着样本量动；
    * **合成对照**（单条规则，取自派生出来的键）：三个类别各自的样子被逐个钉住 ——
      未确认 ⇒ ``UNRESOLVED_FACTS`` 且规则进 ``unchecked``；0 ⇒ 规则**命中**；非零 ⇒
      规则**确认为假**（不命中、不进 ``unchecked``）。三个类别三个结果，
      没有两个塌成一个。
    * 合成那一段**不写死任何键名**：键来自 :func:`_keys_that_move`，规则由它构造。
    """
    counts = _count_fields()
    reached = _keys_reached_by_counts()
    assert reached, "非空转：计数没有被投影，下面「未确认与 0 不同判」在空气上成立"

    referencing = _rules_referencing(bundle, reached)
    assert referencing, (
        f"真实 profile 里没有一条规则引用计数递进来的键（{sorted(reached)}）——"
        "下面那条「差别恰好是它们」会退化成一个 0 == 0 的空断言，读起来却像通过。"
    )

    unrecorded = _candidate(**{field: None for field in counts})
    recorded_zero = _candidate(**{field: 0 for field in counts})
    unrecorded_outcome = qualify_candidate(unrecorded, bundle).outcome
    zero_outcome = qualify_candidate(recorded_zero, bundle).outcome

    assert set(referencing) <= set(unrecorded_outcome.unchecked), (
        "未确认时，引用计数的规则必须一条都没判（全进 unchecked）——"
        f"漏了 {sorted(set(referencing) - set(unrecorded_outcome.unchecked))}："
        "未确认被当成了一次判定"
    )
    assert not (set(referencing) & set(zero_outcome.unchecked)), (
        "记了是 0 时，引用计数的规则必须判了（不命中，也不进 unchecked）——"
        f"却还在 unchecked 里：{sorted(set(referencing) & set(zero_outcome.unchecked))}"
    )
    assert set(unrecorded_outcome.unchecked) - set(zero_outcome.unchecked) == set(referencing), (
        "未确认与「记了是 0」的差别**恰好**应当是那些引用计数的规则，实际差："
        f"{sorted(set(unrecorded_outcome.unchecked) - set(zero_outcome.unchecked))}。"
        "多出来的那些说明还有别的东西跟着样本量动。"
    )

    for field in counts:
        for key in sorted(_keys_that_move(field, _CLASS_VALUES)):
            synthetic = _bundle(
                account_rules=(
                    AccountRule(
                        id="probe-count",
                        description="计数探测",
                        status=AccountStatus.HOLD,
                        requires=(f"!{key}",),
                    ),
                )
            )
            none_outcome = qualify_candidate(_candidate(**{field: None}), synthetic).outcome
            zero_outcome = qualify_candidate(_candidate(**{field: 0}), synthetic).outcome
            nonzero_outcome = qualify_candidate(
                _candidate(**{field: 10**6}), synthetic
            ).outcome

            assert none_outcome.reason is EligibilityReason.UNRESOLVED_FACTS, (
                f"{field}=None 时规则被当成判过了（{none_outcome.reason}）——"
                "未确认必须不决定任何子句"
            )
            assert none_outcome.unchecked == ("probe-count",), (
                f"{field}=None 时规则没有进 unchecked：{none_outcome.unchecked}"
            )
            assert zero_outcome.status is AccountStatus.HOLD, (
                f"{field}=0 时 `!{key}` 没有命中（{zero_outcome.status}）——"
                "「记了是 0」是一条已确认的结论，取反必须真的取反"
            )
            assert zero_outcome.reason is EligibilityReason.RULE_FIRED, (
                f"{field}=0 时规则没有命中：{zero_outcome.reason}"
            )
            assert nonzero_outcome.reason is EligibilityReason.NO_RULE_FIRED, (
                f"{field}=10**6 时规则没有确认为假：{nonzero_outcome.reason}"
            )
            assert nonzero_outcome.unchecked == (), (
                f"{field}=10**6 时规则没判：{nonzero_outcome.unchecked}"
            )

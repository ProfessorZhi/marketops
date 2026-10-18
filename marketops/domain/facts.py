"""事实词汇与子句方言 —— 引擎与 profile 装载层共用的**唯一**一份。

为什么单独一个模块：``application/grading.py`` 要**求值**子句，装载器要
在**装载期**校验子句。两边各写一份正则，就会有一边先漂移 —— 而漂移的那一份
会把一个语法错误读成「这条规则不生效」。那种故障不会报错，只会让某个等级
永远拿不到，然后被人解释成「业务就是这样」。

因此这里同时提供：

* :data:`ROUTE_FACT_NAMES` / :data:`ACCOUNT_FACT_NAMES` /
  :data:`CANDIDATE_FACT_NAMES` —— 事实词表；
* :func:`parse_clause` —— 把一条子句解析成 ``(事实名, 是否取反, 比较方式, 取值)``；
* :func:`clause_is_well_formed` —— 装载期的 fail-closed 判据。

方言刻意保持极小，一共三种形式::

    has_literal_email                 事实为真
    !has_conflict                     事实为假
    binding==strong                   事实等于某值
    business_function in [a, b, c]    事实属于某个取值集合

**事实词汇是中性的**，所以放在通用层不违反「业务规则只进 profiles/」：
这里认识 ``role_class``，不认识 ``grounds supervisor``；认识
``business_function``，不认识 ``bookings`` 到底算不算业务入口。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 引擎提供的**路线级**通用事实。
#:
#: ``has_literal_email`` 之所以是一等公民：SOP v1 L126 对 A 与 B **都**要求
#: 「真实公开业务邮箱」。把它写成一条可求值的事实，规则表里就能显式地要求它，
#: 而不是靠「模型碰巧要求了非空邮箱」来间接保证。
ROUTE_FACT_NAMES: tuple[str, ...] = (
    "has_literal_email",
    "has_person",
    "has_role",
    "has_evidence",
    "has_conflict",
    "email_domain_matches_account",
    "binding",
    "entry_kind",
    "role_class",
    "business_function",
    "block_priority",
)

#: 引擎能求值的**账号级**事实 —— 全部是 ``AccountRecord`` 自身的字段，有真生产者。
#:
#: SOP 的排除条件引用的 ``account.attributes.*`` 这类研究产出**不在这里**，
#: 而在下面的 :data:`CANDIDATE_FACT_NAMES` 里。那些规则今天因为「值恒为 ``None``」
#: （**未确认**）落进 ``unchecked``，判词 ``UNRESOLVED_FACTS`` —— 与「事实名缺失」
#: **同判**：未确认的两种形态（键缺失 / 值 ``None``）是**一件事**，见
#: ``application/grading.py::evaluate_clause``。
#:
#: 所以缺口既没有关闭、也没有换形态：它仍然在，但**读得出**了 —— 报出来的是
#: 「这条规则没判」（``unchecked`` 点名了它），而不是伪装成「核对过、不命中」。
ACCOUNT_FACT_NAMES: tuple[str, ...] = ("country", "has_domain", "has_notes")

#: 描述**场地资产与研究产出**的事实。它们不是账号自身的字段 —— 载体是候选行
#: （``marketops/domain/candidate.py::AccountCandidate``），账号这一侧没有。
#:
#: 之所以单独一张表、而不是并进 :data:`ACCOUNT_FACT_NAMES`：这两组名字回答的是
#: 两个不同的问题。「这个账号的国家字段是什么」与「这块场地是什么环境、什么表面、
#: 我们抓到了几个页面」不是同一件事 —— ``docs/SOP_MAPPING.md`` §4.1 把
#: ``account.attributes.*`` / ``account.evidence.*`` / ``account.pages.*`` 分成
#: 不同命名空间，词表跟着那个分法走。并进一张表会让「账号字段缺一个」和
#: 「场地资产还没被抽取」在数据上长得一模一样，而它们的修法完全不同。
#:
#: 在**账号**这一侧没有任何生产者，因此 ``account_facts`` 对它们一律取 ``None`` ——
#: 在规则方言里是**未确认**，于是引用它们的规则**不决定、不命中、进 ``unchecked``**。
#: 值从候选行来，而候选行的字段名与这里的名字**逐字同名**
#: （``marketops/domain/candidate.py::AccountCandidate``）；把那一行投影成这张词表的
#: 代码是 ``marketops/application/qualify.py::candidate_facts``。名字同名是为了让
#: 那一步「取错了哪个字段」无处可藏 —— 投影只有一处转换（两个计数折成「有没有」），
#: 其余逐字取字段。
CANDIDATE_FACT_NAMES: tuple[str, ...] = (
    "exclusions_hit",
    "venue_environment",
    "organisation_kind",
    "surface_class",
    "surface_evidence_confirmed",
    "any_page_fetched",
    "any_page_blocked",
)

#: 装载期校验账号准入规则时用的**并集**。
#:
#: 装载层必须同时放行两类规则：已经接上生产者的，与**写好了、只是还没有生产者**的。
#: 只放行前者，作者就没法把一条已知缺少生产者的事实写成 ``requires`` ——
#: 他只能退回散文（``when``），而散文不可求值、也不进 ``unchecked``，
#: 于是「等生产者」这件事在数据里看不见了。
#:
#: 并集**只**用于装载期校验。引擎求值用的键集是 ``account_facts`` 的返回值，
#: 两者是**分开的检查**：装载期问「这个词引擎认识吗」，运行期问「这个键现在**有值**吗」。
#: 它们会漂移，而漂移的后果在判词上与「规则没写 requires」完全一样
#: （``DISCOVERED`` + ``UNRESOLVED_FACTS``）—— 所以 ``tests/test_grading.py`` 里
#: 有一条机制测试专门钉住漂移之后的行为。**这次改动让那句话更重而不是更轻**：
#: 运行期少一个键不再有独立症状（键缺失与值 ``None`` 同判），
#: 于是「引擎不认识这个词」与「还没有值」在判词上是同一件事。
ACCOUNT_QUALIFICATION_FACT_NAMES: tuple[str, ...] = ACCOUNT_FACT_NAMES + CANDIDATE_FACT_NAMES

#: 三种比较方式。
KIND_TRUTHY = "truthy"
KIND_EQUALS = "equals"
KIND_IN = "in"

_IN_CLAUSE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s+in\s+\[(?P<values>.*)\]$")
_FACT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class ParsedClause:
    """一条已解析的子句。``raw`` 保留原文，供报错时原样回显。"""

    fact: str
    negate: bool
    kind: str
    values: tuple[str, ...]
    raw: str


def _split_values(text: str) -> tuple[str, ...]:
    values: list[str] = []
    for raw in text.split(","):
        item = raw.strip().strip("'\"")
        if item:
            values.append(item.lower())
    return tuple(values)


def parse_clause(clause: str) -> ParsedClause | None:
    """解析一条子句。**格式不合法时返回 None** —— 由调用方决定怎么失败。

    引擎把 None 当作「求不了值」（不命中并回报），装载层把它当作配置错误。
    同一份解析、两种处置，是因为「写错一条规则」在装载期是能修的，
    在运行期只能被记下来。
    """
    text = clause.strip()
    if not text:
        return None

    negate = text.startswith("!")
    if negate:
        text = text[1:].strip()
    if not text:
        return None

    in_match = _IN_CLAUSE_RE.match(text)
    if in_match:
        fact = in_match.group(1)
        return ParsedClause(
            fact=fact,
            negate=negate,
            kind=KIND_IN,
            values=_split_values(in_match.group("values")),
            raw=clause.strip(),
        )

    if "==" in text:
        fact, _, expected = text.partition("==")
        fact = fact.strip()
        if not _FACT_NAME_RE.match(fact):
            return None
        return ParsedClause(
            fact=fact,
            negate=negate,
            kind=KIND_EQUALS,
            values=(expected.strip().strip("'\"").lower(),),
            raw=clause.strip(),
        )

    if not _FACT_NAME_RE.match(text):
        return None
    return ParsedClause(fact=text, negate=negate, kind=KIND_TRUTHY, values=(), raw=clause.strip())


def clause_is_well_formed(clause: str, allowed: tuple[str, ...]) -> str | None:
    """装载期判据：合法则返回 ``None``，否则返回一句人话的错误原因。"""
    parsed = parse_clause(clause)
    if parsed is None:
        return f"不是合法子句（支持 fact / !fact / fact==value / fact in [a, b]）：{clause!r}"
    if parsed.fact not in allowed:
        return (
            f"引用了未知事实 {parsed.fact!r}。可用事实：{', '.join(allowed)}。\n"
            "  写错一个词不会让规则「宽松一点」，只会让它静默地永不命中。"
        )
    if parsed.kind is KIND_IN and not parsed.values:
        return (
            f"in [] 是空取值集合 —— 它会永不命中。要表达「这一条暂时没有取值」，"
            f"应当省略整条 requires 而不是写空集合：{clause!r}"
        )
    return None


__all__ = [
    "ROUTE_FACT_NAMES",
    "ACCOUNT_FACT_NAMES",
    "CANDIDATE_FACT_NAMES",
    "ACCOUNT_QUALIFICATION_FACT_NAMES",
    "ParsedClause",
    "parse_clause",
    "clause_is_well_formed",
    "KIND_TRUTHY",
    "KIND_EQUALS",
    "KIND_IN",
]

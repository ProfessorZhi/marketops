"""**SavedView** —— 一份可以被重复执行的查询（``MULTI_SESSION_PLUGIN_AND_NOTIFICATION_
ARCHITECTURE.md`` §5.2 那八格）。

## 它是什么，以及它**不是**什么

§5.2 逐字：「SavedView 只是查询，不产生外部调用、不产生预算。一个 Account 可同时出现
在多个 View；移动/读某个 View 不复制 Account 行。」三句话决定了这个模块的三条边界：

* **它只是一份查询。** 这个类里没有任何东西可以发起一次检索、抓取或模型调用 ——
  它连一个端口的名字都不出现（判据 ``tests/test_mcp_views_is_offline.py`` 算的是
  ``views`` 那一族的 import 闭包，而不只是这一条自律）。
* **它不产生预算。** 保存一份视图与「为它开一个 Watch」是两件事，而后者才是要权限与
  预算的那一次写入（``catalog.py`` 里 ``put_saved_view`` 的 summary 逐字：「保存不是
  花钱或要权限的那一步，为它开一个 Watch 才是」）。这一条在这个模块里表现为
  **一个不存在的字段**：``SavedView`` 上没有任何预算格，所以「保存时顺手设一个上限」
  在这里写不出来。
* **它不复制事实。** ``query`` 里能出现的东西只有**筛选条件**与**被点名的对象 id**；
  一个 Account 的字段值一个都不住在这里。这就是「一个 Account 出现在三个视图里是
  一个 Account 读了三次，不是三行」在类型上的落点：三份 View 各自持有一段查询，
  而那份事实只有一行，住在账号池里。

## ``query`` 为什么按**存的原样**留着，而不是存编译结果

``get_saved_view`` 那条 summary 逐字：「**每一个查询形状的格都按存的原样回**，所以
调用方能分辨「我写的过滤」与「编译器的读法」—— 只回后者会让一处改写无处对照」。

所以这一格是 :class:`~typing.Any` 的原始文档（``dict``），不是
:class:`CompiledQuery`：存编译结果会在写入那一刻把一次改写固化下来，此后读的人再也
看不到「我当初写的是什么」。编译是**读的时候**做的（:func:`compile_view_query`），
于是「我写的」与「读法」在同一个回执里并排放着，可对照。

## 一份跑不动的查询必须**点名是哪一部分**

``run_saved_view`` 那条 summary 逐字：「一份跑不动的查询要点名**是哪一部分**跑不动，
而不是少回几行 —— 少回的行与「本来就没有」在报告上同形，而这一条正是「换个读法就少
几个客户」那类错的入口」。

这条纪律在这个模块里是一个**形状**：编译的产物不是一个抛不抛异常的判定，而是一个
:class:`CompileOutcome` —— 它带着 ``unrunnable`` 那一串，每一项有 ``part``（形如
``filter[1].op``）与 ``reason``。写路径**不拒**一份跑不动的查询（一份查询今天跑不动
不是「这条记录不合法」：``country`` 那一格还没有生产者时它照样是一份可以存下的意图），
但两个出口都必须把那一串原样报出来 —— ``put_saved_view`` 的回执报它，
``run_saved_view`` 在拒绝整次读数时也报它。

**不报而绕过**在这里是做不到的：扣掉一个跑不动的部分就得到「一份更小的查询」，
而它的行数与「这些对象本来就不满足」在回执上同形。
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, ValidationError, field_validator

from marketops.domain.account import AccountStatus, MarketAccount
from marketops.domain.base import StrictModel
from marketops.domain.errors import SavedViewConflictError
from marketops.domain.util import (
    check_canonical_id,
    sha256_text,
    to_utc,
    utc_now,
)

__all__ = [
    "ACCOUNT_ATTRIBUTE",
    "COMPARES_BY_FIELD",
    "CompileOutcome",
    "Compare",
    "CompiledQuery",
    "AccountField",
    "SavedView",
    "SavedViewConflictError",
    "SubjectKind",
    "UnrunnablePart",
    "ViewPredicate",
    "ViewSort",
    "ViewVisibility",
    "compile_view_query",
    "PredicateVerdict",
    "matches_account",
    "account_value",
    "project_accounts",
]


class ViewVisibility(str, Enum):
    """这条视图谁读得到。

    §5.2 的 ``owner/visibility`` 两格。两个成员，不多不少：它们回答的是**读**的问题
    （这个工作区里谁读得到这一条），而不是「谁能改」—— 改一份视图的判据落在
    ``put_saved_view`` 上（它要 ``_CONTROL``），与可见性是两件事。

    ``PRIVATE`` 不是「加密」也不是「不落盘」：它是一条**读路径**的判据，落在
    ``application/views.py`` 的三个读出口上（读不到时是拒绝，不是一份空结果 ——
    空结果与「它对你不可见」在报告上同形，而前者的处置是去建一条、后者是去问它的
    负责人）。一条只写在文档里的可见性没有任何执行器，所以它必须落在读路径上。
    """

    PRIVATE = "private"
    SHARED = "shared"


class SubjectKind(str, Enum):
    """视图读的是**哪一类**共享事实。

    只有一个成员，而这不是「还没写完」：本片能读的那个投影是账号池
    （``ports/pool.py::AccountPoolStore``），而 ``ACCOUNT`` 是它唯一的主体。列出这个
    枚举而不是让 ``query`` 里随便写一个字符串，是因为**不认识的那一类必须被点名**：
    ``subject_kind: "route"`` 落在 :func:`compile_view_query` 上会成为一条
    ``unrunnable``（``part="subject_kind"``），而不是一次「按 route 筛」被静默地
    当成「按 account 筛」—— 后者会回一批看起来完全正常的行。
    """

    ACCOUNT = "account"


class AccountField(str, Enum):
    """可以被筛的账号字段。**闭集**，而每一个成员都有对应的读法。

    ``ACCOUNT_ATTRIBUTE`` 把每一项映到 ``domain/account.py::MarketAccount`` 上的一格：
    这个映射住在这里（而不是住在应用层），是为了让「筛什么」与「读哪一格」只有一处
    定义 —— 两处各写一遍的那天，「按域名筛」在一处读 ``domains``、在另一处读
    ``organisation``，而两份回执各自都正常。
    """

    COUNTRY = "country"
    STATUS = "status"
    ORGANISATION = "organisation"
    DOMAIN = "domain"
    LAST_VERIFIED_AT = "last_verified_at"
    FIRST_SEEN_AT = "first_seen_at"


#: 字段 → ``MarketAccount`` 上那一格的名字。**一处定义**，见 :class:`AccountField`。
ACCOUNT_ATTRIBUTE: dict[AccountField, str] = {
    AccountField.COUNTRY: "country",
    AccountField.STATUS: "status",
    AccountField.ORGANISATION: "organisation",
    AccountField.DOMAIN: "domains",
    AccountField.LAST_VERIFIED_AT: "last_verified_at",
    AccountField.FIRST_SEEN_AT: "first_seen_at",
}


class Compare(str, Enum):
    """比较算子。闭集，且**每个字段只准用它真的做得到的那些**。"""

    IS = "is"
    IS_NOT = "is_not"
    IN = "in"
    CONTAINS = "contains"
    BEFORE = "before"
    AFTER = "after"
    #: 「这一格是空的」 —— 对 ``country`` 而言它**不是**「不在英国」：
    #: ``None``（还没查）与 ``""``（查了，是没有）都落进这一档，而两者本来都
    #: 不是一条关于这个组织的回答（``domain/account.py`` 那一格的 docstring 逐字）。
    #: 要区分那两种情况得给 ``is`` 一个具体的值，而不是用这一档。
    IS_EMPTY = "is_empty"
    IS_SET = "is_set"


#: 每个字段准用哪些算子。**这就是那条「合法组合」的判据**，不是一句注释。
COMPARES_BY_FIELD: dict[AccountField, frozenset[Compare]] = {
    AccountField.COUNTRY: frozenset(
        {Compare.IS, Compare.IS_NOT, Compare.IN, Compare.IS_EMPTY, Compare.IS_SET}
    ),
    AccountField.STATUS: frozenset({Compare.IS, Compare.IS_NOT, Compare.IN}),
    AccountField.ORGANISATION: frozenset(
        {
            Compare.IS,
            Compare.IS_NOT,
            Compare.CONTAINS,
            Compare.IS_EMPTY,
            Compare.IS_SET,
        }
    ),
    AccountField.DOMAIN: frozenset(
        {
            Compare.IS,
            Compare.IS_NOT,
            Compare.IN,
            Compare.CONTAINS,
            Compare.IS_EMPTY,
            Compare.IS_SET,
        }
    ),
    AccountField.LAST_VERIFIED_AT: frozenset(
        {Compare.BEFORE, Compare.AFTER, Compare.IS_EMPTY, Compare.IS_SET}
    ),
    AccountField.FIRST_SEEN_AT: frozenset({Compare.BEFORE, Compare.AFTER}),
}

#: 不带值的算子。它决定了 ``value`` 是必填还是必须缺席 —— 一个「忽略掉多余的
#: ``value``」的实现会让 ``is_empty: "GB"`` 看起来像一次筛国家，而它一次都没筛。
VALUELESS: frozenset[Compare] = frozenset({Compare.IS_EMPTY, Compare.IS_SET})

#: 收一个**集合**的算子。
LISTED: frozenset[Compare] = frozenset({Compare.IN})

#: 收一个**时刻**的算子。
TEMPORAL: frozenset[Compare] = frozenset({Compare.BEFORE, Compare.AFTER})


def _check_id(value: str, field: str) -> str:
    return check_canonical_id(value, field)


class ViewPredicate(StrictModel):
    """一条筛选。三格：哪个字段、怎么比、比什么。

    ``value`` 的形状由 ``(field, op)`` 决定，不是自由文本 —— 校验器逐条写明。放宽它
    的代价不是「多收了几种写法」，而是把一次写错的查询变成一份**静默更小**的结果：
    ``after: "last week"``（一句人话）在宽容的实现里会与「空字符串」同形，而那是一次
    「本来就没有」。
    """

    field: AccountField
    op: Compare
    value: str | list[str] | None = None

    @field_validator("value")
    @classmethod
    def _shape(cls, value: str | list[str] | None) -> str | list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            if value != value.strip():
                raise ValueError("value 首尾不能带空白（一个尾随空格会让筛选永远不命中）")
            return value
        if not value:
            raise ValueError("IN 的取值表不能是空的：一个空的取值表筛不出任何东西，"
                             "而它与「这一条没写」在报告上同形")
        for item in value:
            if not isinstance(item, str) or item != item.strip():
                raise ValueError("取值表里每一项都得是非空、无首尾空白的字符串")
        return value

    def describe(self) -> str:
        """这一条筛选的**一行**说法。回执与拒绝里各要用一次，所以只有这一处。"""
        if self.value is None:
            return f"{self.field.value} {self.op.value}"
        if isinstance(self.value, list):
            return f"{self.field.value} {self.op.value} [{', '.join(self.value)}]"
        return f"{self.field.value} {self.op.value} {self.value}"


class ViewSort(StrictModel):
    """排序：一个字段加一个方向。

    ``field`` 取的是 :class:`AccountField` 的同一张表 —— 「能筛的东西」与「能排的东西」
    在这里是同一批。分成两张表会让「按 country 筛、按 country 排」这句话里那个
    ``country`` 有两个定义处，而它们分家的表现是「排序结果与筛选条件说的不是同一格」。
    """

    field: AccountField
    descending: bool = False


class CompiledQuery(StrictModel):
    """**读法** —— 一份对得上盘上那份原样文档的、可执行的查询。

    它只出现在回执里（``get_saved_view`` / ``put_saved_view`` / ``run_saved_view`` 的
    ``compiled`` 那一格），**不落盘**：盘上留着的是原样文档（见模块 docstring）。

    ``subjects`` 是**被点名的对象 id**（§5.2 的「对象集合」那一半）。它们只是 id ——
    那次运行要去账号池里把它们读回来，而读回来的东西不写进任何视图：这是「不复制
    Account 行」在类型上的第二个落点。
    """

    subject_kind: SubjectKind
    subjects: tuple[str, ...] = ()
    predicates: tuple[ViewPredicate, ...] = ()
    sort: ViewSort | None = None
    columns: tuple[AccountField, ...] = ()
    limit: int | None = Field(default=None, ge=1)

    @property
    def digest(self) -> str:
        """这份读法的内容摘要。两次读到的读法是不是同一份，看它。

        用**内容摘要**而不是「第几版」：同一个问题在另一条链上（``RefreshPlan.
        policy_version`` 那一格的 docstring）逐字论证过 —— 计数做不到这件事，
        因为它住在库里，于是同一条内容在两次部署里会拿到两个号。
        """
        parts = [f"kind={self.subject_kind.value}"]
        parts += [f"subject={subject}" for subject in self.subjects]
        parts += [f"pred={predicate.describe()}" for predicate in self.predicates]
        if self.sort is not None:
            parts.append(
                f"sort={self.sort.field.value}:"
                f"{'desc' if self.sort.descending else 'asc'}"
            )
        parts += [f"col={column.value}" for column in self.columns]
        if self.limit is not None:
            parts.append(f"limit={self.limit}")
        return sha256_text("\n".join(parts))[:16]


class UnrunnablePart(StrictModel):
    """**哪一部分**跑不动，以及为什么。

    ``part`` 是一个**定位**（``filter[1].op`` / ``columns[2]`` / ``subject_kind``），
    不是一个类别名：``op`` 这个字单独出现时，读的人还得自己去找是第几条筛选。
    定位与原因两格都必填 —— 一条只有原因没有定位的拒绝，读起来与「这份查询整体不行」
    同形，而处置完全不同（改那一格 vs 重写这份查询）。
    """

    part: str
    reason: str


class CompileOutcome(StrictModel):
    """编译的产物：**要么**一份读法，**要么**一串点名了位置的跑不动。

    两格可以同时有内容（``filter[0]`` 好、``filter[1]`` 跑不动），而且那种情况下
    ``compiled`` 为 ``None`` —— 一份由「好的那几条」拼出来的查询会回一批**看起来
    正常**的行，而它比调用方写的那份查询小。少回的行与「本来就没有」在报告上同形，
    那正是这条纪律要挡的读法。
    """

    compiled: CompiledQuery | None = None
    unrunnable: tuple[UnrunnablePart, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.unrunnable and self.compiled is not None


#: 查询文档里**认识**的格。多一格就是一次拼错的字段名，而它必须被点名 —— 一个
#: 忽略多余格子的实现会让 ``{"filter": [...], "sorrt": {...}}`` 静默地不排序。
_KNOWN_KEYS: frozenset[str] = frozenset(
    {"subject_kind", "subjects", "filter", "sort", "columns", "limit"}
)


def _as_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def compile_view_query(raw: Any) -> CompileOutcome:
    """把一份**原样**的查询文档编译成读法，或者点名哪些部分跑不动。

    这个函数**不抛**（除了它在入口处对「这压根不是一份查询文档」的处理 —— 那种情况
    也落成 ``unrunnable`` 的一条，``part="query"``）：调用方拿到的永远是一份可以说给
    人听的结果，而不是一次异常。理由与 ``application/plan_preview.py`` 那条同源 ——
    一份读数要报的是「哪里不行」，不是「构造不出来」。
    """
    problems: list[UnrunnablePart] = []
    document = _as_dict(raw)
    if document is None:
        return CompileOutcome(
            unrunnable=(
                UnrunnablePart(
                    part="query",
                    reason=(
                        f"查询文档是一个 {type(raw).__name__}，不是一个对象。"
                        "一份查询的形状是 "
                        '{"subject_kind": "account", "filter": [...], "sort": {...}}。'
                    ),
                ),
            )
        )

    for key in sorted(set(document) - _KNOWN_KEYS):
        problems.append(
            UnrunnablePart(
                part=f"query.{key}",
                reason=(
                    f"不认识的格 {key!r}。认识的格是 {sorted(_KNOWN_KEYS)} —— "
                    "多出来的格必须**点名**，而不是被忽略：忽略会让一次拼错的字段名"
                    "看起来像一次生效过的筛选。"
                ),
            )
        )

    kind_raw = document.get("subject_kind", SubjectKind.ACCOUNT.value)
    subject_kind: SubjectKind | None = None
    try:
        subject_kind = SubjectKind(kind_raw)
    except ValueError:
        problems.append(
            UnrunnablePart(
                part="subject_kind",
                reason=(
                    f"subject_kind 是 {kind_raw!r}，不是一个认识的对象类别"
                    f"（认识的：{[kind.value for kind in SubjectKind]}）。"
                    "**不**把它当成默认的那一类：那会让一次「按 route 筛」静默地"
                    "回一批账号。"
                ),
            )
        )

    subjects = _subjects(document.get("subjects"), problems)
    predicates = _predicates(document.get("filter"), problems)
    sort = _sort(document.get("sort"), problems)
    columns = _columns(document.get("columns"), problems)
    limit = _limit(document.get("limit"), problems)

    if problems:
        return CompileOutcome(unrunnable=tuple(problems))
    assert subject_kind is not None  # problems 为空时它是构造出来的
    return CompileOutcome(
        compiled=CompiledQuery(
            subject_kind=subject_kind,
            subjects=subjects,
            predicates=predicates,
            sort=sort,
            columns=columns,
            limit=limit,
        )
    )


def _subjects(raw: Any, problems: list[UnrunnablePart]) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(
            UnrunnablePart(
                part="subjects",
                reason=f"subjects 是一个 {type(raw).__name__}，不是一个 id 列表。",
            )
        )
        return ()
    out: list[str] = []
    for index, item in enumerate(raw):
        try:
            out.append(_check_id(item, f"subjects[{index}]"))
        except (ValueError, TypeError) as exc:
            problems.append(
                UnrunnablePart(part=f"subjects[{index}]", reason=str(exc))
            )
    return tuple(out)


def _predicates(raw: Any, problems: list[UnrunnablePart]) -> tuple[ViewPredicate, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(
            UnrunnablePart(
                part="filter",
                reason=(
                    f"filter 是一个 {type(raw).__name__}，不是一个筛选列表。"
                    "一份字典会被读成「按它筛」，而它的键在 ``MarketAccount`` 上"
                    "一个都不是字段名。"
                ),
            )
        )
        return ()
    out: list[ViewPredicate] = []
    for index, item in enumerate(raw):
        document = _as_dict(item)
        if document is None:
            problems.append(
                UnrunnablePart(
                    part=f"filter[{index}]",
                    reason=f"这一条筛选是一个 {type(item).__name__}，不是一个对象。",
                )
            )
            continue
        unknown = sorted(set(document) - {"field", "op", "value"})
        if unknown:
            problems.append(
                UnrunnablePart(
                    part=f"filter[{index}]",
                    reason=f"不认识的格 {unknown} —— 一条筛选只有 field / op / value。",
                )
            )
            continue
        field_raw = document.get("field")
        try:
            field = AccountField(field_raw)
        except ValueError:
            problems.append(
                UnrunnablePart(
                    part=f"filter[{index}].field",
                    reason=(
                        f"字段是 {field_raw!r}，不是可筛的账号字段"
                        f"（可筛的：{[item.value for item in AccountField]}）。"
                    ),
                )
            )
            continue
        op_raw = document.get("op")
        try:
            op = Compare(op_raw)
        except ValueError:
            problems.append(
                UnrunnablePart(
                    part=f"filter[{index}].op",
                    reason=(
                        f"算子 {op_raw!r} 不认识（认识的："
                        f"{[item.value for item in Compare]}）。"
                    ),
                )
            )
            continue
        allowed = COMPARES_BY_FIELD[field]
        if op not in allowed:
            problems.append(
                UnrunnablePart(
                    part=f"filter[{index}].op",
                    reason=(
                        f"{field.value} 不能用 {op.value}"
                        f"（它能用的：{sorted(item.value for item in allowed)}）。"
                        "这里的组合表就是判据 —— 一个把不合法组合放过去的实现会回一批"
                        "**看起来正常**的行，而它们没有按调用方写的那句话筛过。"
                    ),
                )
            )
            continue
        value = document.get("value")
        if op in VALUELESS and value is not None:
            problems.append(
                UnrunnablePart(
                    part=f"filter[{index}].value",
                    reason=f"{op.value} 不带值，而这里给了 {value!r}。",
                )
            )
            continue
        if op not in VALUELESS and value is None:
            problems.append(
                UnrunnablePart(
                    part=f"filter[{index}].value",
                    reason=f"{op.value} 要一个值，而这里没有给。",
                )
            )
            continue
        if op in LISTED and not isinstance(value, list):
            problems.append(
                UnrunnablePart(
                    part=f"filter[{index}].value",
                    reason=f"{op.value} 要一个取值表，而这里是 {value!r}。",
                )
            )
            continue
        if op not in LISTED and isinstance(value, list):
            problems.append(
                UnrunnablePart(
                    part=f"filter[{index}].value",
                    reason=f"{op.value} 要一个值，而这里给了一张表 {value!r}。",
                )
            )
            continue
        if op is Compare.IS or op is Compare.IS_NOT:
            named = _enum_value_problem(field, value)
            if named is not None:
                problems.append(
                    UnrunnablePart(part=f"filter[{index}].value", reason=named)
                )
                continue
        if op in TEMPORAL:
            moment = _moment_problem(value)
            if moment is not None:
                problems.append(
                    UnrunnablePart(part=f"filter[{index}].value", reason=moment)
                )
                continue
        try:
            out.append(ViewPredicate(field=field, op=op, value=value))
        except ValidationError as exc:
            problems.append(
                UnrunnablePart(part=f"filter[{index}].value", reason=str(exc))
            )
    return tuple(out)


def _enum_value_problem(field: AccountField, value: Any) -> str | None:
    """``is`` / ``is_not`` 配 ``status`` 时，值必须是 ``AccountStatus`` 的成员。

    只对这一格判：其余字段的自由文本没有闭集可比，而 ``status`` 有 —— 一个不在那个
    闭集里的值会让筛选永远不命中，于是「状态拼错了」与「池子里没有那种状态的账号」
    在回执上同形。
    """
    if field is not AccountField.STATUS:
        return None
    try:
        AccountStatus(value)
    except ValueError:
        return (
            f"status 是 {value!r}，不是一个账号状态"
            f"（认识的：{[item.value for item in AccountStatus]}）。"
        )
    return None


def _moment_problem(value: Any) -> str | None:
    if not isinstance(value, str):
        return f"时刻取一个 ISO-8601 字符串，而这里是 {value!r}。"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return (
            f"{value!r} 不是一个 ISO-8601 时刻（例如 2026-09-28T00:00:00+00:00）。"
        )
    if parsed.tzinfo is None:
        return (
            f"{value!r} 没有带时区。一个省掉时区的时刻会被按服务所在机器的本地时区"
            "解释，于是「这一段窗口里有哪些账号」取决于服务跑在哪里。"
        )
    return None


def _sort(raw: Any, problems: list[UnrunnablePart]) -> ViewSort | None:
    if raw is None:
        return None
    document = _as_dict(raw)
    if document is None:
        problems.append(
            UnrunnablePart(
                part="sort",
                reason=f"sort 是一个 {type(raw).__name__}，不是一个对象。",
            )
        )
        return None
    unknown = sorted(set(document) - {"field", "descending"})
    if unknown:
        problems.append(
            UnrunnablePart(part="sort", reason=f"不认识的格 {unknown}。")
        )
        return None
    field_raw = document.get("field")
    try:
        field = AccountField(field_raw)
    except ValueError:
        problems.append(
            UnrunnablePart(
                part="sort.field",
                reason=f"排序字段 {field_raw!r} 不是可筛的账号字段。",
            )
        )
        return None
    descending = document.get("descending", False)
    if not isinstance(descending, bool):
        problems.append(
            UnrunnablePart(
                part="sort.descending",
                reason=f"descending 是 {descending!r}，不是一个布尔值。",
            )
        )
        return None
    return ViewSort(field=field, descending=descending)


def _columns(raw: Any, problems: list[UnrunnablePart]) -> tuple[AccountField, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        problems.append(
            UnrunnablePart(
                part="columns",
                reason=f"columns 是一个 {type(raw).__name__}，不是一个字段列表。",
            )
        )
        return ()
    out: list[AccountField] = []
    for index, item in enumerate(raw):
        try:
            out.append(AccountField(item))
        except ValueError:
            problems.append(
                UnrunnablePart(
                    part=f"columns[{index}]",
                    reason=(
                        f"列 {item!r} 不是一个认识字段"
                        f"（认识的：{[field.value for field in AccountField]}）。"
                        "**不**把它当作默认列：那会让一次读错的列名回一批别处的字段，"
                        "而调用方要看的正是那一格。"
                    ),
                )
            )
    return tuple(out)


def _limit(raw: Any, problems: list[UnrunnablePart]) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        problems.append(
            UnrunnablePart(
                part="limit",
                reason=(
                    f"limit 是 {raw!r}。它要么是正整数，要么是 null（要全部）—— "
                    "0 与负数会回一份空结果，而它与「这一条都没筛到」同形。"
                ),
            )
        )
        return None
    return raw


class SavedView(StrictModel):
    """一份被存下来的查询。

    七格，逐字对着 §5.2：

    ``view_id`` / ``revision``（主键）、``workspace`` / ``owner`` / ``name``
    （``name`` 是扁平列，因为「这个人的同名视图是哪一条」要被答出来）、
    ``visibility``、``query``（原样文档）、``created_at`` / ``updated_at``。

    这个类**不认识存储**，也**不认识端口**：它是一份值。把它写下去、把它读回来、
    在写事务里判「当前版重名」这三件事住在 ``ports/views.py`` 与
    ``adapters/sqlite/views.py``（判据 ``tests/test_views_store_contract.py``）。

    ## ``revision`` 与「历史版本会重名」

    同名判据只作用在**当前版**上（那一版是每个 ``view_id`` 的最大 ``revision``）。
    历史版本保留着它们当时的名字，而改名正是产生新版的理由 —— 所以库里出现两条
    同名的 ``saved_view`` 行是正常的，其中只有一条是当前版。判据是
    ``(workspace, owner, name)`` 上的**当前版**不得重名，而它**不是**一条 DDL 唯一
    索引：一条普通的唯一索引会把历史版本也一起挡掉，于是「改名」这件事在库上
    做不成 —— 那正是「改名要产生新版」这句话的反面。
    """

    view_id: str
    revision: int = Field(ge=1)
    workspace: str
    owner: str
    name: str
    visibility: ViewVisibility = ViewVisibility.SHARED
    #: **原样**存着的查询文档。见模块 docstring 那一节。
    query: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    note: str = ""

    @field_validator("view_id")
    @classmethod
    def _view_id(cls, value: str) -> str:
        return _check_id(value, "view_id")

    @field_validator("workspace")
    @classmethod
    def _workspace(cls, value: str) -> str:
        return _check_id(value, "workspace")

    @field_validator("owner")
    @classmethod
    def _owner(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(
                "owner 不能为空或首尾带空白：它是 §5.2 那一格负责人，"
                "而这条视图的同名判据是按 (workspace, owner, name) 判的 —— "
                "一个空 owner 会让所有人的视图落进同一个命名空间。"
            )
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(
                "name 不能为空：它是人认这条视图的那一个名字，"
                "而空名字在回执里读起来与「这个字段没回」同形。"
            )
        return value

    @field_validator("created_at", "updated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return to_utc(value)

    @property
    def content_digest(self) -> str:
        """这一版**业务内容**的摘要。两个存储实现用它判「这一次要写的是不是已经就是
        盘上那一份」。

        它**不含** ``revision`` / ``created_at`` / ``updated_at``：这三格记的是「这是
        第几版、什么时候落的」，而它们不同**正是**新版存在的理由。把任一格放进来，
        「重发同一份视图」就会每一次都产生一个新版本 —— 于是「有人反复点保存」在盘上
        留下几十版，而「这条视图上次改是什么时候」读成一个刚刚的时刻。

        ``query`` 用 ``sort_keys`` 序列化：同一个筛选字典的两种键序是**同一份内容**
        （``adapters/sqlite/payload.py::dump_payload`` 逐字同一条理由），而一份按
        插入顺序序列化的摘要会把它们算成两版。
        """
        parts = [
            f"workspace={self.workspace}",
            f"owner={self.owner}",
            f"name={self.name}",
            f"visibility={self.visibility.value}",
            f"query={json.dumps(self.query, ensure_ascii=False, sort_keys=True)}",
            f"note={self.note}",
        ]
        return sha256_text("\n".join(parts))[:16]

    def next_revision(self, *, at: datetime | None = None) -> "SavedView":
        """这一版的下一版：同名、同 owner、同可见性、同查询，``revision + 1``。

        ``created_at`` **原样带过去**：它记的是「这条视图是哪一刻被存下的」，
        而改名不是一次新的创建。写路径用它（``put_saved_view`` 的「改一份」那一半），
        于是「哪些格子会被这一版改掉」在类型上就看得见：只有 ``revision``、
        ``updated_at`` 与调用方显式换掉的那几格。
        """
        return self.model_copy(
            update={"revision": self.revision + 1, "updated_at": to_utc(at or utc_now())}
        )


# --------------------------------------------------------------- 执行一份读法
#: 一条筛选在**这一行**上判出来的三档。第三档是这一族的承重点：
#: 「这一行在这一条上**没有答案**」不能落进「不命中」——
#: 一份 ``country is_not GB`` 的查询会把 ``country=None``（**还没查**）的那些行
#: 一起筛掉，而它们落到报告上看起来就是「不在英国的那些已经列全了」。
class PredicateVerdict(str, Enum):
    """**命中 / 不命中 / 判不了**。第三档不是「不命中」。"""

    MATCH = "match"
    MISS = "miss"
    UNDECIDED = "undecided"


#: 标量字段「空」的两种写法。``MarketAccount.country`` 那一格的 docstring 逐字：
#: ``None`` 是**还没查**，``""`` 是**查了、是没有** —— 两者都不是一条关于这个组织的
#: 回答，所以两格在筛选上同样地判不了（要区分它们就给 ``is`` 一个具体的值，
#: 那是 ``Compare.IS_EMPTY`` 那一档 docstring 里说的事）。
_ABSENT: tuple[object, ...] = (None, "")


def account_value(account: MarketAccount, field: AccountField) -> object:
    """``MarketAccount`` 上这一格的值。**只有这一处**知道字段与属性的对应关系。

    它自己是那一格，不是它的字符串写法：``last_verified_at`` 交出去的是
    ``datetime | None``（要拿来比时刻），``domains`` 交出去的是一个字符串序列。
    在回执里要的是另一个样子（``ViewRow.value`` 那一格自己渲染），那是渲染，
    不是读取 —— 两件事分开写，是为了让「按时刻筛」不必先把时刻变成字符串再比。
    """
    return getattr(account, ACCOUNT_ATTRIBUTE[field])


def _as_present(value: object) -> bool:
    """这一格**有没有**一个可用的值。空集合与空串同款：都不是一条回答。"""
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple)) and not value:
        return False
    return True


def _scalar(value: object) -> str:
    """把一个值折成可比较的字符串。时刻走 ``isoformat``（含偏移量），
    枚举走 ``value``，其余走 ``str``。"""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, datetime):
        return to_utc(value).isoformat()
    return str(value)


def _verdict(predicate: ViewPredicate, account: MarketAccount) -> PredicateVerdict:
    """一条筛选在这一行上的三档判定。**这是一族的全部执行器**，回执里的计数、
    行本身、以及 ``undecided`` 那一格都从它来 —— 三处各判一遍的实现会让
    「少回的那几行」与「判不了的那几行」在报告上打架。

    ## 判不了的两种来源，各自都不是「不命中」

    * **这一格是空的**（``None`` / ``""`` / 空集合）而算子要一个值：没查过的事实
      判不了任何一条子句。唯一例外是 ``is_empty`` 与 ``is_set`` —— 那两条问的
      **正是**「这一格空不空」，所以它们对空值有答案。
    * **``is_not`` 遇上空的这一格**：``country is_not "GB"`` 对 ``country=None``
      **不命中也不判不了 —— 它判不了**。这一条最容易写错，而写错的方向恰好是
      让答案看起来更好：把没查过的那些**拢进结果**（读成「不一定在英国」）或
      把它们**静默筛掉**（读成「不在英国」）都会让报告说「按国家筛过了」。
    """
    value = account_value(account, predicate.field)
    present = _as_present(value)

    if predicate.op is Compare.IS_EMPTY:
        return PredicateVerdict.MATCH if not present else PredicateVerdict.MISS
    if predicate.op is Compare.IS_SET:
        return PredicateVerdict.MATCH if present else PredicateVerdict.MISS
    if not present:
        return PredicateVerdict.UNDECIDED

    wanted = predicate.value
    if isinstance(value, (list, tuple)):
        # 集合型字段（今天只有 ``domains``）：**任何一个元素**满足就是命中。
        # 一个「把列表拼成字符串再 contains」的实现会让 ``domain is "x.com"``
        # 命中 ``["notx.com"]``，而报告上那一行看起来正常。
        items = [_scalar(item) for item in value]
        if predicate.op is Compare.IS:
            hit = any(item == wanted for item in items)
        elif predicate.op is Compare.IS_NOT:
            hit = not any(item == wanted for item in items)
        elif predicate.op is Compare.IN:
            table = predicate.value if isinstance(predicate.value, list) else []
            hit = any(item in table for item in items)
        elif predicate.op is Compare.CONTAINS:
            needle = _scalar(wanted).casefold()
            hit = any(needle in item.casefold() for item in items)
        else:  # pragma: no cover —— 时刻算子不在这张字段的合法组合里
            return PredicateVerdict.UNDECIDED
        return PredicateVerdict.MATCH if hit else PredicateVerdict.MISS

    here = _scalar(value)
    if predicate.op is Compare.IS:
        hit = here == _scalar(wanted)
    elif predicate.op is Compare.IS_NOT:
        hit = here != _scalar(wanted)
    elif predicate.op is Compare.IN:
        table = predicate.value if isinstance(predicate.value, list) else []
        hit = here in [_scalar(item) for item in table]
    elif predicate.op is Compare.CONTAINS:
        # 大小写不敏感，而且**不改写**被筛的值：``organisation`` 与 ``domains``
        # 在来源里的大小写并不统一（``domain/account.py`` 的 ``domains`` 要求裸主机、
        # 全小写，但 ``organisation`` 是「照来源写的那个」）。
        hit = _scalar(wanted).casefold() in here.casefold()
    elif predicate.op in (Compare.BEFORE, Compare.AFTER):
        moment = value if isinstance(value, datetime) else None
        other = _parse_moment(wanted)
        if moment is None or other is None:  # pragma: no cover —— 模型已保证两边是时刻
            return PredicateVerdict.UNDECIDED
        hit = to_utc(moment) < other if predicate.op is Compare.BEFORE else to_utc(moment) > other
    else:  # pragma: no cover —— Compare 是闭集
        return PredicateVerdict.UNDECIDED
    return PredicateVerdict.MATCH if hit else PredicateVerdict.MISS


def _parse_moment(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        return to_utc(raw)
    if not isinstance(raw, str):
        return None
    try:
        return to_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def matches_account(query: CompiledQuery, account: MarketAccount) -> PredicateVerdict:
    """这一行过不过这份读法：三档，不是布尔。

    被点名的 ``subjects`` 是**第一道**：不在名单上的行不判 —— 而那与「判不了」
    是两件事（一个不在名单上的行不是一个有疑问的行）。所以它落 :attr:`MISS`。

    一条筛选判不了**整行就是判不了**（不继续看后面的筛选）：后面的筛选可能命中，
    而「命中了几条、判不了几条」拼出来的那个布尔会让人以为整行判过了。
    """
    if query.subjects and account.account_id not in query.subjects:
        return PredicateVerdict.MISS
    if query.subject_kind is not SubjectKind.ACCOUNT:  # pragma: no cover —— 闭集只有一项
        return PredicateVerdict.UNDECIDED
    undecided = False
    for predicate in query.predicates:
        verdict = _verdict(predicate, account)
        if verdict is PredicateVerdict.MISS:
            return PredicateVerdict.MISS
        if verdict is PredicateVerdict.UNDECIDED:
            undecided = True
    return PredicateVerdict.UNDECIDED if undecided else PredicateVerdict.MATCH


def sort_key(account: MarketAccount, field: AccountField) -> tuple[bool, str]:
    """排序键：**空值往后**，其余按字符串折出来的样子比。

    ``(是不是空, 值)`` 这个两元组是 ``ports/views.py::items`` 与 ``domain/maintenance``
    共用的那一款（「空值最后」）。空值参与排序**不是**把它读成最小值：一份
    「按国家排」的结果第一行是「还没查」，读起来像「这些都是没查的」。
    """
    value = account_value(account, field)
    if not _as_present(value):
        return (True, "")
    if isinstance(value, (list, tuple)):
        return (False, "\u0000".join(sorted(_scalar(item) for item in value)))
    return (False, _scalar(value).casefold())


def project_accounts(
    query: CompiledQuery,
    accounts: "list[MarketAccount] | tuple[MarketAccount, ...]",
) -> tuple[tuple[MarketAccount, ...], int, int]:
    """在一批账号上跑一份读法：``(行, 判不了的行数, 被截掉的行数)``。

    **这一层不碰磁盘、不碰网络、不认识任何端口** —— 它拿到的是一批已经读回来的
    ``MarketAccount``。所以「``run_saved_view`` 零 provider」在这一层是构造：
    要花钱或者要授权的东西根本到不了这里。

    ``limit`` 截掉的那些**要报出来**（``application/caps.py`` 那一句：「截断必须被
    记录」）：一份被截到 50 行的查询与一份本来只有 50 行的查询，在报告上同形。
    """
    rows: list[MarketAccount] = []
    undecided = 0
    for account in accounts:
        verdict = matches_account(query, account)
        if verdict is PredicateVerdict.MATCH:
            rows.append(account)
        elif verdict is PredicateVerdict.UNDECIDED:
            undecided += 1
    if query.sort is not None:
        rows.sort(
            key=lambda account: sort_key(account, query.sort.field),  # type: ignore[union-attr]
            reverse=query.sort.descending,
        )
    else:
        rows.sort(key=lambda account: account.account_id)
    dropped = 0
    if query.limit is not None and len(rows) > query.limit:
        dropped = len(rows) - query.limit
        rows = rows[: query.limit]
    return tuple(rows), undecided, dropped

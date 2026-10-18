"""``source_method_id`` 是一个**闭集**注册表 —— 未知值必须失败关闭。

这一条不是为了整洁：报告里那句 ``C - B = Skill 净新增合格 Account`` 要成立，
两支臂的候选池必须能被读成**同一个东西**，而那个东西按 SOP 属于
**补充证据**而不是母池。每一行带不带 ``source_method_id``、带的值认不认，
决定了报告会不会被读成它没资格成为的那种东西。

因此本文件守住三件事：

1. **闭集**：只有注册表里的 id 能被解析，未知值抛 ``ConfigError``。
   自由格式的 id 会让「来源族多样性」这个指标算不出来 —— 每个拼写变体
   都是一个新族，而指标只会显示「多样性很高」。
2. **注册表自身是一致的**：键等于成员自己的 id、族是声明过的、每个族都有人、
   每个成员都说得出它能回答什么。
3. **值是市场中性的**：id 里没有厂商名 —— 换一个检索 provider 不该改变指标，
   也不该改变历史上已经记下来的那些行。
"""

from __future__ import annotations

import re

import pytest

from marketops.domain.errors import ConfigError
from marketops.domain.sources import (
    SOURCE_FAMILIES,
    SOURCE_METHODS,
    resolve_source_method,
    source_family,
    source_family_counts,
)

#: id 的形状：小写点分。它是**报告里的取值**，不是文件名，所以判据是形状而不是
#: 落盘规范形式 —— 但它必须能被 grep、能被排序、能一眼看出不属于别的东西。
ID_SHAPE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")

#: 检索 / 研究工具的厂商名。方法名描述的是**怎么找到的**，不是**用谁找的**：
#: provider 换了（或者哪天两条腿用不同 provider），指标与历史行都不该跟着变。
VENDOR_NAMES = ("serper", "google", "bing", "duckduckgo", "brave", "claude")


# --- 闭集 --------------------------------------------------------------------


def test_unknown_ids_fail_closed() -> None:
    """未知 id 抛错，绝不静默接受。

    静默接受等于「来源」这一栏可以随便写 —— 于是「来源族多样性」这个指标
    失去依据，而报告上它仍然是一个数。
    """
    for unknown in (
        "",
        "   ",
        "search",
        "search.engine",
        "search.engine.v2",
        "SEARCH.ENGINE.V1",
        "search.engine.v1 ",
        "unknown.method.v1",
    ):
        with pytest.raises(ConfigError):
            resolve_source_method(unknown)


def test_a_vendor_flavoured_id_is_not_registered() -> None:
    """``search.serper.v1`` 是候选契约测试里「必填字段给个样例」用的夹具值，不是来源。

    它不是被禁掉的，是**没被注册**的：方法不认厂商。要加一个新来源，改这份
    注册表 —— 而那是一次可审计的、进了版本控制的决定。
    """
    with pytest.raises(ConfigError):
        resolve_source_method("search.serper.v1")


def test_an_unknown_id_cannot_be_looked_up_as_a_family() -> None:
    with pytest.raises(ConfigError):
        source_family("search.nothing.v1")


def test_a_known_id_resolves_to_its_own_member() -> None:
    for method_id, member in SOURCE_METHODS.items():
        resolved = resolve_source_method(method_id)
        assert resolved.id == method_id
        assert resolved is member


# --- 注册表自身的一致性 ------------------------------------------------------


def test_the_registry_is_not_empty() -> None:
    assert SOURCE_METHODS, "注册表是空的 —— 下面的断言全会假通过"


def test_every_key_matches_the_member_id() -> None:
    for key, member in SOURCE_METHODS.items():
        assert key == member.id, f"键与成员 id 不一致：{key!r} != {member.id!r}"


def test_every_id_is_shaped_as_a_dotted_lowercase_name() -> None:
    for method_id in SOURCE_METHODS:
        assert ID_SHAPE.match(method_id), f"id 形状不对（报告里要能 grep、能排序）：{method_id!r}"


def test_no_vendor_name_appears_in_an_id() -> None:
    for method_id in SOURCE_METHODS:
        for vendor in VENDOR_NAMES:
            assert vendor not in method_id.casefold(), (
                f"{method_id!r} 把厂商名写进了方法 id —— 换 provider 不该改变指标，"
                "也不该让历史上的行变成另一种来源。"
            )


def test_every_member_names_a_declared_family() -> None:
    """族是**闭集**：一个拼错的族名会变成一个凭空多出来的族。"""
    for member in SOURCE_METHODS.values():
        assert member.family in SOURCE_FAMILIES, (
            f"{member.id!r} 声明了未定义的族 {member.family!r}；"
            f"可用的族只有 {sorted(SOURCE_FAMILIES)}。"
        )


def test_every_declared_family_has_at_least_one_member() -> None:
    """没有成员的族是一个空抽屉 —— 它会读成「这一类来源查过了，没有」。"""
    declared = {member.family for member in SOURCE_METHODS.values()}
    assert declared == set(SOURCE_FAMILIES), (
        f"声明了却没有成员的族：{sorted(set(SOURCE_FAMILIES) - declared)}"
    )


def test_every_member_says_what_it_answers() -> None:
    """每个成员都要说得出它能回答什么、它属于哪一族。"""
    for member in SOURCE_METHODS.values():
        assert member.answers.strip() == member.answers and member.answers, (
            f"{member.id!r} 没有写下它能回答什么"
        )


# --- 族多样性：注册表存在的理由 ----------------------------------------------


def test_family_counts_count_per_family() -> None:
    counts = source_family_counts(
        ["search.engine.v1", "search.engine.site_scoped.v1", "search.engine.v1"]
    )
    assert counts == {
        source_family("search.engine.v1"): 2,
        source_family("search.engine.site_scoped.v1"): 1,
    }


def test_family_counts_of_nothing_is_empty() -> None:
    assert source_family_counts([]) == {}


def test_family_counts_fail_closed_on_an_unknown_id() -> None:
    """一个没出处的候选不能让统计悄悄少一行 —— 少掉的那一行看起来像「没有」。"""
    with pytest.raises(ConfigError):
        source_family_counts(["search.engine.v1", "unknown.method.v1"])

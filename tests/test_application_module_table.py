"""``application/__init__.py`` 那张模块表是**穷举**的。

## 为什么一条目录需要执行器

那张表在写下来的时候是对的，而它的失效方式不是变红 —— 是**少一行**。少一行不会
让任何东西坏掉：没有人 import 那张表，新模块照样被 import、照样跑、照样有测试。
它的唯一消费者是「想知道这一层住着什么」的人，而那个人的处境恰好是**不知道有
什么可查**，所以他会以为表就是全部。

本仓对这类文档的立场是反复写下过的：没有执行器的话只是自觉。所以这里遍历
``marketops/application/`` 下的每一个模块，逐个要求它在表里有一行。

## 它不检查那一行写得对不对

「``caps`` 这一行说清了它在做什么吗」是一条判据判不了的话。能判的是它**在不在**，
以及**名字拼对了没有** —— 而名字拼错正是「少一行」最常见的样子。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
APPLICATION = REPO / "marketops" / "application"
INIT = APPLICATION / "__init__.py"

#: 表里的一行：``| ``模块名`` | 它编排的是什么 |``
_ROW = re.compile(r"^\|\s*``([a-z_]+)``\s*\|(.+)\|\s*$")


def _listed() -> dict[str, str]:
    """表里列出的模块 → 那一行的说明（去掉首尾空白）。"""
    rows: dict[str, str] = {}
    for line in INIT.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match is not None and match.group(1) != "模块":
            rows[match.group(1)] = match.group(2).strip()
    return rows


def _present() -> set[str]:
    """本目录下真实存在的模块名。"""
    return {
        path.stem
        for path in APPLICATION.glob("*.py")
        if path.name != "__init__.py" and not path.name.startswith("_")
    }


def test_the_walker_sees_both_sides_at_all() -> None:
    """两个人口都不是空的 —— 否则下面那条判据是在真空上成立的。

    一次坏掉的正则（例如表头格式改了）会让 ``_listed()`` 返回 ``{}``，而
    「没有未列出的模块」在一个人都没有时是真话。
    """
    assert len(_present()) >= 20, sorted(_present())
    assert len(_listed()) >= 20, sorted(_listed())


def test_every_module_under_application_has_a_row() -> None:
    """**本文件的全部意义。** 新加一个模块而不写一行，这里转红。"""
    missing = sorted(_present() - set(_listed()))

    assert missing == [], (
        f"``marketops/application/`` 下这些模块没有在 __init__ 的模块表里：{missing}。\n"
        "加一行，一句话说清它编排的是什么 —— 那张表的读者是「不知道有什么可查」的人。"
    )


def test_no_row_names_a_module_that_is_not_there() -> None:
    """反方向：表里指向一个不存在的模块。

    这一半更容易被漏掉，而它更坏：一个幽灵行会让读的人去找一个永远找不到的文件，
    然后怀疑自己的路径写错了。搬走或删掉模块时，表要跟着走。
    """
    ghosts = sorted(set(_listed()) - _present())

    assert ghosts == [], (
        f"__init__ 的模块表里这些名字在本目录下不存在：{ghosts}。\n"
        "模块搬走或删掉时，那一行要跟着走。"
    )


def test_every_row_says_something() -> None:
    """每一行都要有说明，而且不是一句「见上文」。

    空说明比没有那一行更坏：它让表看起来是完整的。
    """
    empty = sorted(name for name, description in _listed().items() if not description)

    assert empty == [], f"这些行的说明是空的：{empty}"

"""同一句判据的两个写法：``domain/action_item.py::OPEN_STATES`` 与 DDL 里那条 ``WHERE``。

``db.py`` 那条 ``action_item_open_by_subject`` 是**这半句话唯一的执行者**：

    ``WHERE state IN ('open', 'claimed', 'waiting')``

而领域那一侧给的是 :data:`OPEN_STATES`。两处说的必须是**同一件事** —— 分家的那天，
后果不是一条报错，是「同一个主体上可以有两条未结的待办」或者「已结的占着格子」，
两者都只在报告里现形（``domain/action_item.py`` 的模块 docstring 逐字：
「那条用例从 ``adapters/sqlite/db.py`` 的 DDL 文本里把 ``WHERE`` 里那几个字面量读出来，
与本模块的 ``OPEN_STATES`` 逐字比」）。

## 这份为什么不靠注释

因为 ``db.py`` 是 **MAIN 独占**的文件（另一个 Agent 不许改它）。一句「两处说的是同一件
事」的注释在有人改了 ``OPEN_STATES`` 而没改 DDL 的那天不会响 —— 而那一天真的会发生：
加一个状态（比如 ``blocked``）是一件看起来完全局部的改动。

## 它读的是**文本**，不是库

那份 DDL 是仓库里的一段源码，所以这条判据不需要一个库、不需要 ``sqlite3``、也不受
「库是不是已经建好了」影响。一个跑在临时库上的版本会在库里没有这条索引时**静默地
跳过**（``IF NOT EXISTS`` 与一次旧库升上来的情形），而那条跳过看起来与通过一样。
"""

from __future__ import annotations

import re
from pathlib import Path

from marketops.domain.action_item import OPEN_STATES, TERMINAL_STATES, ActionItemState

DDL_PATH = Path(__file__).resolve().parent.parent / "marketops" / "adapters" / "sqlite" / "db.py"

INDEX_NAME = "action_item_open_by_subject"

#: 从索引名往后找到的那句 ``WHERE state IN (...)``。允许折行与任意空白：
#: 一份靠固定列宽的判据会在有人重排 DDL 时红，而那时的红与判据无关。
_WHERE = re.compile(
    r"CREATE\s+UNIQUE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+"
    + INDEX_NAME
    + r"\b.*?WHERE\s+state\s+IN\s*\(([^)]*)\)",
    re.DOTALL | re.IGNORECASE,
)


def _declared_open_states() -> list[str]:
    """DDL 里那几个字面量，按它写下来的次序。"""
    text = DDL_PATH.read_text(encoding="utf-8")
    found = _WHERE.search(text)
    assert found is not None, (
        f"``db.py`` 里找不到 ``{INDEX_NAME}`` 那条 ``CREATE UNIQUE INDEX ... WHERE state IN (...)``。\n"
        "这一条判据的**全部意义**就是那句 WHERE，所以找不到时不能跳过："
        "要么索引被改名了，要么它被换成了一个不带 WHERE 的普通唯一索引 —— "
        "后者会让已结的待办占着格子，而报告上只显示「这个主体的待办只有一条」。"
    )
    return re.findall(r"'([^']*)'", found.group(1))


def test_the_index_covers_exactly_the_open_states() -> None:
    """``WHERE state IN (...)`` 与 ``OPEN_STATES`` **逐字相同**（连次序一起比）。

    次序也进比较，是因为它读起来是一条能核对的读数：两边都按生命周期的顺序写
    （``open`` → ``claimed`` → ``waiting``），所以一条「集合相同而次序不同」的红
    会让人当场看出是不是有人重排过其中一处。
    """
    declared = _declared_open_states()
    assert declared == [state.value for state in OPEN_STATES]


def test_the_index_does_not_cover_a_terminal_state() -> None:
    """**已结的不在 ``WHERE`` 里** —— 「已结的可以有任意多条」这句话的落点。

    这一条是上一条的另一半，单独写出来是因为它有**独立**的失败方式：一个把
    ``OPEN_STATES`` 与 ``TERMINAL_STATES`` 加在一起当成 WHERE 的实现（「状态总是要
    全列的」）能通过「覆盖了所有未结状态」那一句，而它会让同一个账号上做过三次的
    「核实转介联系人」再也建不出第四次。
    """
    declared = set(_declared_open_states())
    for state in TERMINAL_STATES:
        assert state.value not in declared


def test_open_and_terminal_partition_the_state_enum() -> None:
    """两半合起来**正好**是 ``ActionItemState`` 的全部 —— 不多、不少、不重叠。

    这一格与 DDL 无关，它是 ``OPEN_STATES`` / ``TERMINAL_STATES`` 自己的完整性问题：
    一个第三个状态（比如 ``blocked``）加进枚举而没进任何一半时，上面那条文本对账
    照样绿（DDL 没变），而那条索引会**看不见**那个新状态 —— 于是它能被无限地重复创建。
    """
    open_states = set(OPEN_STATES)
    terminal = set(TERMINAL_STATES)
    assert open_states & terminal == set()
    assert open_states | terminal == set(ActionItemState)
    # 两半各自都没有重复项。
    assert len(OPEN_STATES) == len(open_states)
    assert len(TERMINAL_STATES) == len(terminal)

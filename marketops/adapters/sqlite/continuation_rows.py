"""``continuation`` 表的**写**那一半：列序、摊平、插入，以及「欠账只写一次」。

## 为什么它从 ``jobs.py`` 里搬出来

在这一片之前，「一条业务事件欠下下一步」只有三个产出者（受理 / 结算 / 作业状态迁移），
而它们**全都在 ``JobStore`` 上**，所以列序与那条 ``INSERT`` 住在 ``jobs.py`` 里没有任何
问题。§9 那一句「**裁决后自动重评受影响对象**」加进来之后有第四个产出者，而它落在
**另一个端口**上（``ReviewCaseStore.record_resolution``）—— 那份写入与它自己那条
``human_resolution`` 必须在**同一个**写事务里（理由逐字见 ``ports/jobs.py`` 的
「续跑意图」一节：写在事务之前会为一件没发生的事欠账，写在之后会在一次崩溃里永久
丢掉欠账）。

于是这份 INSERT 有了两个调用方，而两个调用方各写一份列序的形态是：某天加一列，
只改了一处，另一处**悄悄写空**那个字段。搬到这里的正是「一份定义、两处调用」这一步，
不是「为了好看」。

## 「没有才写」为什么在这里，以及它现在是**够不着的**

一条重放落到同一个主键上，而那时候那一行可能已经被控制器推进过了。把它退回
``PENDING`` 会让「同一个事件欠下的下一步只有一行」变成一句空话，且表现是
「做完的事又欠了一遍」。所以 ``owe`` 是「没有才写」，不是 ``INSERT OR REPLACE``。

**它现在是第二道闸，而且前两道把它遮住了**（这是实测，不是推断）：``admit`` 在
``existing is not None`` 处直接 ``return``，``settle`` 在 ``_already_done`` 处直接
``return`` —— 那几条产出者路径上的重发**都走不到这里**。所以从端口出发的契约判据
覆盖不了它，它唯一的执行器是 ``tests/test_sqlite_continuation.py`` 里那条直接调
``owe`` 的判据。

留着它的理由不是「以后可能有用」：``owe`` 是**跨端口共享的写入口**，而第四个产出者
（§9 的「裁决后自动重评」）就是从一个**别人的端口**走进来的，它的重发没有上游短路
替它挡。今天够不着，是因为那几条路径各有自己的一道闸，而不是因为这条守卫多余。

**调用方必须已经在写事务里** —— 这个模块不开事务，它只在这条连接上说话。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import Enum

from ...domain.continuation import Continuation, ContinuationIntent, open_continuation
from ...domain.util import to_utc

__all__ = ["CONTINUATION_COLUMNS", "flatten", "insert_continuation", "owe"]


#: ``continuation`` 表里那几列，按插入顺序。``INSERT`` 用元组占位符，所以顺序在这里
#: 说一次 —— 与 ``jobs.py::_row_to_continuation`` 的那份字段表是同一条纪律：两处各写
#: 一遍列名之后，加一列只会让其中一处漏掉，而漏掉的形态是一个**悄悄写空**的字段。
CONTINUATION_COLUMNS = (
    "continuation_id",
    "scene_id",
    "kind",
    "business_key",
    "subject_kind",
    "subject_ref",
    "why",
    "state",
    "due_at",
    "epoch",
    "holder",
    "checkpoint",
    "attempts",
    "reason",
    "blocked_on",
    "created_at",
    "updated_at",
)


def _marks(values: Sequence[str]) -> str:
    """``?, ?, ?`` —— 长度只由 ``len(values)`` 决定。**绝不用 f-string 拼值本身**。"""
    return ",".join("?" * len(values))


def flatten(row: Continuation) -> tuple:
    """按 :data:`CONTINUATION_COLUMNS` 的顺序取值，并把非 SQLite 能存的类型摊平。

    三个闭集枚举取 ``.value``（存字符串，不存成员名 —— 成员名改了不影响盘上的行，
    而一个存成员名的库会在改名那天读不出来，且读不出来的表现是**整个表**报错）。
    时刻统一按 ``to_utc(...).isoformat()``：与这张库其它每一处写时刻的方式逐字相同，
    于是「读回来的那个时刻是哪个时区」这件事只有一个答案。
    """
    dumped = row.model_dump()
    values: list = []
    for name in CONTINUATION_COLUMNS:
        value = dumped[name]
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, datetime):
            value = to_utc(value).isoformat()
        values.append(value)
    return tuple(values)


def insert_continuation(conn, row: Continuation) -> None:
    values = flatten(row)
    conn.execute(
        f"INSERT INTO continuation ({', '.join(CONTINUATION_COLUMNS)}) "
        f"VALUES ({_marks(CONTINUATION_COLUMNS)})",
        values,
    )


def owe(conn, intents: Sequence[ContinuationIntent], *, at: datetime) -> None:
    """把一次业务事件欠下的下一步写进 outbox。**调用方必须已在写事务里。**

    已有的行一格不动 —— 理由见模块 docstring。
    """
    for intent in intents:
        row = open_continuation(intent, now=at)
        exists = conn.execute(
            "SELECT 1 FROM continuation WHERE continuation_id = ?",
            (row.continuation_id,),
        ).fetchone()
        if exists is None:
            insert_continuation(conn, row)

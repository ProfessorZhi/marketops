"""两张池子共用的合并**原语**：时刻只往一个方向走，证据取并集。

## 为什么它们是模块，而不是留在 ``pool.py`` 里

``application/pool.py``（场地资产池，§4）与 ``application/relations.py``（责任关系表，
§5）各自都要把一份新观察折进已有的那一行，而这两件事的规则**逐字相同**：

* ``last_verified_at`` 只往**晚**走（``None`` 不参与）；
* ``evidence_ids`` 取并集、保序、去重。

同一句话写在两个模块里，就是本仓那条「两处写同一句话是缺陷」的成因：改其一、漏其二
的那一次，两张表的时刻会开始朝**相反**的方向走，而报告上两边都只是「时间对得上」。

提取的时机是按纪律来的（**等第二个实现出现再提取**）：关系表是第二个用户，在它之前
这两个函数留在 ``pool.py`` 里是对的，而它们现在有三个调用点（资产池两处 + 关系表）。
第 §6 的账号池会带来更多 —— 那一张表的证据与新鲜度是同一条纪律。

## 这里**只有** ``later_of``，没有 ``earlier_of``

``first_seen_at`` 那一半（只往早走）在两处都是就地一个 ``min``，因为它**必填**（模型上
没有默认值、也不会是 ``None``），于是没有需要小心的分支。``last_verified_at`` 不同：
它的 ``None`` 是「从那以后没再看过」，而 ``max(None, t)`` 会抛 —— 写成函数是为了让
「``None`` 不参与」这句话有一个可执行的落点，而不是让每个调用点各自记得先判空。

**这不是一条可以顺手改成「``None`` 当时间零点」的规则**：那样两块场地里「只见过一次」
的那一块会在复核清单上永远排最后，而它恰恰是最该被复核的那一块。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

__all__ = ["later_of", "ordered_union"]


def later_of(left: datetime | None, right: datetime | None) -> datetime | None:
    """两个时刻里晚的那个；``None`` 不参与（**不是**「比任何时刻都早」）。

    与 ``Observation.observed_at`` 同一条纪律：这个字段的 ``None`` 说的是「从那以后
    没再看过」，而它是可复核与否的判据 —— 把它读成时间零点，等于把一块从没复核过的
    场地读成刚刚复核过。

    两个都是 ``None`` 时给 ``None``：**没有一次真的重新看过**，不是一个时刻。
    """
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right)


def ordered_union(left: Sequence[str], right: Sequence[str]) -> list[str]:
    """并集、保序、去重。顺序是承重的：它是「先看到的在前」，人读证据链时就是这个顺序。

    **只并集，不减。** 证据是**指针**（``evidence_ids`` 指向证据库里只增不改的行），
    所以并集的意思是「这些行都关于这一条」，而不是「这些行现在还有效」。撤下一条证据
    需要「它被撤销了」这件事本身有凭据，而本层没有那一格 —— 见
    ``application/relations.py`` 模块 docstring 末尾那条边界。
    """
    seen = list(left)
    for item in right:
        if item not in seen:
            seen.append(item)
    return seen

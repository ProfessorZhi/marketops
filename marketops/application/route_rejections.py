"""§6.3 的**读**那一半：把落盘的拒收回执数成一张分布，并且逐条留着。

## 为什么要有这个模块 —— 拒收已经落盘了，但没有人读它

``produce_routes`` 从这一版起把每一条拒收写成一张
:class:`~marketops.domain.route_records.RouteRejection`（见 ``ports/store.py`` 的
``put_route_rejection``）。落盘本身不是目的：一个只写不读的表，与在这个模块出现
之前那个「算出来就丢掉的列表」，在**报告**上完全同形 —— 前者甚至更糟，因为它看起来
像已经处理过了。

所以这一层回答的是 §6.3 逐字要的那两件事：**分布**（每一类各多少）与**具体项**
（是哪几条）。两件都要，而它们不是「摘要与详情」的关系：

* 只有分布时，「这一页没有联系人」（正常，什么也不用做）与「域名判错了」（要修）
  在报告上是同一个数字 —— 而它们的下一步相反；
* 只有具体项时，一份几千行的回执里看不出**哪一类在涨**。运维要读的是那个趋势。

## 分类只在这里做一次

绑定层给的是 ``RouteRejectionCode``（闭集枚举），不是一句 ``reason``。这个模块把它
数出来，MCP 那一侧只做协议映射（§22：处理器不判定）—— ``adapters/mcp_server`` 里
那份回执块调的就是这里的 :func:`tally`，不是自己再数一遍。

两处各数一遍的后果不是多几行代码：``by_code`` 是**闭集全列**的（计数为 0 的类别也
在），而「少列一个 0」与「这一类真的没发生过」在报告上长得一样 —— 两份实现只要有一
处忘了补新枚举成员，那一类就会从报告里静默消失，而消失之后它看起来像从没发生过。

## 两格分布而不是一格

``by_code`` 回答「是哪一类」，``by_run`` 回答「是哪一趟」。第二格存在的理由是一件
运维上分得开、而报告上分不开的事：**一次性的与每一趟都在发生的**。同一类拒收三十条
来自同一趟（那一页特别脏）与来自三十趟（域名归属判据坏了），处置完全不同 —— 前者
是数据问题，后者是规则问题，而 ``by_code`` 那一格两者一模一样。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from ..domain.route_records import RouteRejection, RouteRejectionCode
from ..ports.store import EvidenceStore


def _empty_tally() -> dict[str, int]:
    """每一类都记 0 的那张分布表。见模块 docstring 里为什么闭集全列。"""
    return {code.value: 0 for code in RouteRejectionCode}


@dataclass(frozen=True)
class RejectionTally:
    """一张拒收分布 + 落在这张分布下面的那些回执。

    :attr:`items` 的顺序是 ``(occurred_at, rejection_id)`` —— 与存储读回来的顺序
    逐字相同，于是同一份库在两次导出里是逐字节相同的两段 JSON。这一条不是洁癖：
    一个按到达顺序（也就是字典顺序）排列的报告，会让两份「内容一样」的导出看起来
    不一样，而那种差异最后总会被当成「有人改过东西」。

    :attr:`truncated` 必须**说出来**：截断是调用方钉的上限（MCP 面的响应大小），
    而「只返回了前 20 条」与「一共就 20 条」是一句话的两种读法，报告里不能含糊。
    """

    total: int
    by_code: Mapping[str, int]
    #: ``(run_id, 条数)``，按条数降序、同数按 ``run_id`` 升序。空串那一档是
    #: 「收据上没有记哪一趟」—— 只在手工构造的回执上出现（``RouteContext.run_id``
    #: 是必填且非空白的），所以它一旦非空就是一句真话：有一批收据不知道来路。
    by_run: tuple[tuple[str, int], ...]
    items: tuple[RouteRejection, ...]
    truncated: bool


def tally(
    rejections: Sequence[RouteRejection], *, limit: int | None = None
) -> RejectionTally:
    """分类、计数、排序、截断。**纯函数**，不碰存储。

    它单独存在（而不是并进 :func:`history` 里）的理由与 ``fold_observation`` 那条
    相同：``submit_findings`` 手上的那一批拒收**还没有落盘读过一遍**（它们就是这一趟
    刚生成的），却要报同一张分布。让它俩各写一份分类，两份的差别只会在「某一类的
    数字在两处不一样」时暴露，而那看起来像存储出了问题。

    ``limit=None`` 是不截断。上限由**调用方**钉：那是响应大小的协议关切（MCP 面的
    ``MAX_ITEMS``），不是这一层的事实 —— 这一层说得出「一共有多少条」，钉多少条
    出去是接口的事。
    """
    by_code = _empty_tally()
    per_run: dict[str, int] = {}
    for item in rejections:
        by_code[item.code.value] += 1
        per_run[item.run_id] = per_run.get(item.run_id, 0) + 1

    ordered = sorted(rejections, key=lambda item: (item.occurred_at, item.rejection_id))
    return RejectionTally(
        total=len(ordered),
        by_code=MappingProxyType(by_code),
        # 条数降序、同数按 run_id 升序：**必须有一个总序**，否则两份内容相同的
        # 分布会因为字典插入顺序不同而排成两个样子。
        by_run=tuple(sorted(per_run.items(), key=lambda pair: (-pair[1], pair[0]))),
        items=tuple(ordered if limit is None else ordered[:limit]),
        truncated=limit is not None and len(ordered) > limit,
    )


def history(
    store: EvidenceStore, account_id: str, *, limit: int | None = None
) -> RejectionTally:
    """一个账号历来被拒的那些。**这是「持久化」这句话的唯一执行器。**

    落盘的收据要有人按账号读回来才算数 —— 只写不读的那一张表，与不做持久化在报告
    上同形（见模块 docstring）。读的是 ``list_route_rejections``，所以三个存储实现
    各自的顺序与归属判据（``owned``）就是这一层的顺序与归属判据，这里不重新判一次：
    一个在本层再筛一遍账号的实现，会让「存储按绑定拒掉了别的工作区的行」与「这里
    筛掉了」在报告上同形。

    没有拒收的账号返回一张 ``total=0``、闭集全 0 的分布 —— **不是** ``None``。
    「这个账号从来没被拒过」与「这一格没有算过」是两句不同的话，而在报告上后者只
    应该出现在调用方压根没读的地方。
    """
    return tally(store.list_route_rejections(account_id), limit=limit)


__all__ = ["RejectionTally", "history", "tally"]

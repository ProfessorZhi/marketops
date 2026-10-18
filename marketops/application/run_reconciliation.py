"""一次 Run 的**对账**：Delta 那一侧的主张，与池子那一侧的事实。

## 它把两份**独立**的记录对起来

=========================================  ==========================================
哪一侧                                      谁写的
=========================================  ==========================================
``delta.effects``                          这一轮**自己的主张**，由调用方交上来
``folded``                                 调用方**主张**这一轮折了哪几行 —— 一串
                                            池子句柄，由 ``fold_claim`` 归一化
``assets.get_asset``                       池子**现在**的样子（另一张表、另一时刻）
``runs.folds_of``                          **账**：这一轮真的往池子里写过的那些行，
                                            由写池子那一个事务记下 —— 见 §「主张
                                            与账」那一节，以及 ``close_run``
=========================================  ==========================================

**主张是句柄，不是回执对象**（e4）。``promote_receipt`` 的回执里含**没有落盘**的
行（跨工作区、证据取不回来），拿它当主张会让一次没写成的折入算作写成了 —— 这是
这一层在一开始就写下的一句。今天收句柄，是因为**知道「哪些行落了盘」的是账本**，
不是调用方手里那个对象的某一格：账记的是池子那一行**在不在**（见下一节）。一份
由回执原样转发出来的主张会多报，而它多报的那些**在 ``close_run`` 的第一道闸上**
被拒。

**两侧不能由同一次计算产出。** 把 Delta 从 ``folded`` 里推出来（「折了什么就记
什么」）会让对账退化成自查 —— 而自查永远通过，它也永远发现不了这一层唯一要发现
的那件事：**池子变大了，而报告上说没变**（§B17 逐字：「'池子变大'包括纠错和
currentness 改变，不只计算新增行」）。

## 主张与账：``folded`` 是前者，``runs.folds_of`` 是后者

``folded`` 是**调用方递上来的一份主张**，而本模块的三个方向**全都只看主张**：
``unclaimed_folds`` 问的是「``folded`` 里有谁没在 Delta 里」，所以 ``folded``
是空的时候它**恒空**。于是一条走得通的假账是：折了却没记，结束时主张 ``folded=()``
—— 账照旧平（三个方向都空），池子却变大了。

**那一半不在本模块里，因为它是纯的**：它只收三个参数，拿不到 Run 那一侧的读。
账由 ``RunStore.folds_of`` 读（它读不出来会抛，**绝不返回零** —— 见那个方法的
docstring），而 :func:`close_run` 拿它先核一遍主张，核过了才调本模块。两处的次序
是有理由的：主张与账对不上时，连「该对哪一份 Delta」都还没有答案。

## 为什么「折进去了」的判据是**账本**，不是那份回执、也不是主张

回执是**打算**折什么，一次折入的结局是**折成了什么** —— 两者之间隔着一次拒绝
（跨工作区、证据取不回来、名字那一格过不了）。拿回执当这一侧，等于让一次没有落盘
的写在对账里算作落盘：那一行在池子里不存在，而 Delta 会因为「回执上有它」被判成
对得上。

**e4 之前**这一格的执行者是 ``claimed_folds``：它把 ``PromotionOutcome.asset is
not None`` 的那些滤出来当主张。**之后**是 ``RunStore.folds_of`` —— 账本记的是
「池子那一行落了盘」（``domain/fold.py`` 的模块 docstring），而它与池子的那一行
**同生共死**（同一个写事务）。两者读的不是同一件事，而账本那一侧强：过滤器只知道
``asset`` 那一格**是不是空的**，账本知道那一行**在不在**。一个把两者当同一件事的
实现，会让「回执说写了、池子说没有」这一格重新变成一句没人能判的话。

账本那一侧的判据在多数的折入上与 ``asset is not None`` 同结论，而**不同结论的那
一格是承重的**：``asset`` 非空不蕴含账号写成了 —— 资产写下去、账号那一行因为别的
原因没写，是 ``asset_only`` 与一种 ``rejected`` 的形状；而场馆那一行**确实**在池子
里，所以这一轮确实让池子变大了，账本也因此记了它。

## 方向一：Delta 说池子里有它，而池子读不出来

九格里**五格**（``promoted`` / ``updated`` / ``superseded`` / ``withdrawn`` /
``unchanged``）逐字主张「池子里已有的那一行」，判断的执行形式是
``PoolEffect.requires_an_existing_row``（那一格的理由写在 ``domain/run_delta.py``
的第四节）。另外四格说的都是「它**没**进池子」或「刚第一次看到」，不对池子的
状态主张任何事，所以这一层不对它们判 —— 判了会把一个正常的 ``held`` 读成事故。

两侧都读得通、而这一层要挡住的形态：**一条 ``updated`` 指着一个池子里从来没有过
的键**。报告上它与一条真的纠正逐字相同，而它下面什么都没有。

``get_asset`` 对「这一行在、但不属于本库的部署」**抛错**而不是返回 ``None``
（端口那一处的定义）。这里照旧让它穿出去：**跨工作区与「没有那一行」不是同一件
事**，把前者读成后者会让一次授权链上的越界长得像一个正常的池子读数。与
``application/promotion.py::_discovery_evidence`` 那一条逐字同一条纪律。

## 方向二：这一轮往池子里写了，而 Delta 说没写

两个落点，**分开报**：

* **没记**（``unclaimed_folds``）：这一轮折进去了，Delta 里连这个键都没有。
* **记反了**（``contradicted_folds``）：Delta 提了这个键，但把它记成了「它没进
  池子」（``excluded`` / ``held`` / ``blocked``）。

两者都是「池子变大了而报告说没变」，但**处置相反**：前者要补一行处置，后者要
**改**那一行已经写下的处置。合成一格的话，读的人分不出该补还是该改 —— 而这一步
的产出正是给人去改的东西。

## 这一层**不判**的四件事，逐条写下

1. **不判「这一批该不该折」。** 准入与排除是 Scene 的规则（``excluded`` 那一格的
   理由），这里读的是「折了没有」。
2. **不判池子那一侧的全集。** 这一层没有「这一轮的 scope」这个概念：
   ``VenueAsset`` 上没有 selector，所以「池子里有而 Delta 没提」**只在 `folded`
   这一侧问得出来**（这一轮真的写下的那些）。一个按整个池子判的实现会把上一轮
   折进去的行整批读成「这一轮没记」。
3. **不判 ``folded`` 是不是真的 —— 那件事在 :func:`close_run` 里做。** 本层是纯的
   （三个参数、三个方向），拿不到 Run 那一侧的账（``RunStore.folds_of`` 要
   ``runs``）。所以「主张与账是否一致」是**另一个函数**的前置判据，两个函数各自
   只答自己那份问题：本层读的是「Delta 与主张之间对不对得上」，``close_run`` 读的是
   「主张与账对不对得上」。合成一个函数，就得给本层加一个只有一半调用会填的参数
   —— 那正是本仓在 ``ports/runs.py`` 里明说过要避开的形态。
4. **不判 ``routes`` / ``evidence`` / ``artifacts`` / ``reviews`` / ``usage``。**
   那五格的主语不是池子：它们各自有各自的存储与各自的地址空间，拿业务键去对它们
   是把两个空间缝在一起。

## 幂等：同一份 Delta 重放不重复

对账对重放**不敏感**，两处各有一条：

* ``folded`` 先经 :func:`fold_claim` **升序去重**再比。一批里两行可能折进同一块
  场地（没有来源编号、名字与地点又相同的那两行），池子那边落的是**一行** ——
  不去重的话，一份只点了这个键一次的 Delta 会被读成「漏记了一次」，而漏记与
  「同一件事被两行说过」是两回事。
  去重与升序各自的理由写在 :func:`fold_claim` 里，**不再抄一份**：它是主张的唯一
  规范化点，两处调用（本层与 ``close_run``）共用它，否则「账与主张对不上」会在
  一个读法上成立、在另一个读法上不成立。
* 判词由**键集**决定，不由计数决定。按新增行数对账的实现读不出纠错与 currentness
  改变 —— 它们对计数是**零**，而那正是 §B17 那句话点名的形状。

一条更硬的依赖写在 ``application/pool.py::venue_handle_for_row`` 那边：句柄的输入里
**没有** ``ordinal``，所以同一个对象在两次导入里取到同一个键，对账的两侧才在同一个
地址空间上说话。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from pydantic import model_validator

from ..domain.base import StrictModel
from ..domain.enums import RunStatus
from ..domain.errors import RunReconciliationError, RunStateError
from ..domain.fold import FoldReceipt
from ..domain.run import Run
from ..domain.run_delta import DeltaCell, RunDelta
from ..ports.pool import VenueAssetStore
from ..ports.runs import RunStore, unknown_run_refusal
from ..ports.views import ActionItemStore
from .run_followups import open_followups

#: 报一份对账时逐项最多列几个键。**是硬上限**：一份写了三百条差异的报文，读的人
#: 只会去读头两行 —— 与 ``adapters/mcp_server/product_loop.py`` 那些上限同一条理由。
_MAX_LISTED = 5


class MissingPoolRow(StrictModel):
    """Delta 说池子里有这一行，而池子读不出来。

    ``key`` 之外还带着 ``cell``：这一条差异**自己**说得清是哪一格的主张没有落点
    （「这一行被这一轮撤回了，而池子里从来没有过它」与「这一行被这一轮改写了，
    而池子里没有它」是两句不同的话）。只带 ``key`` 的话，读的人必须回头去翻那份
    Delta —— 而差异表与 Delta 分家的那天（同一轮的两份读数），翻回去读到的是另一份。
    """

    key: str
    cell: DeltaCell


class RunDeltaReconciliation(StrictModel):
    """一份 Delta 与「这一轮折进去了什么」之间的对账读数。

    ## 一个键**一种读法**，而且每组升序去重

    这条不变量由校验器守着，不是靠构造方自觉：一个键同时出现在 ``agreed`` 与
    ``unclaimed_folds`` 里，意味着「这一轮折进去了」与「这一轮没折它」在同一个
    读数里都成立 —— 而对账的全部用途就是让人照着它去改那一条。读得通的错读数比
    读不出来的更坏。

    **但它不是「六个键表两两不相交」**，那样写会把定义判成违规。方向一的三格
    （``agreed`` / ``missing_rows`` / ``named_without_a_pool_claim``）确实两两不相交
    —— 它们是 Delta 点名的键的一个**划分**，一个键在那里有且只有一种读法。
    方向二不在那个划分里，它与它有两处**必然的**关系：

    * ``contradicted_folds ⊆ named_without_a_pool_claim``：一个「记成了没进池子」的键
      按定义就是四格点名的那个键。两边说的是同一件事 —— 一边是 Delta 的主张，一边
      是池子的事实 —— 而**方向二的存在就是这两件事不同**。所以这里不是两种互斥的
      读法，是同一格的两种来源。校验器仍然把这条写成判据：一个漏了那道过滤器的实现
      （``contradicted = folded − agreed``）会产出「Delta 记成了它没进池子」，而那一格
      里写的是 ``promoted`` —— 一句假话。
    * ``unclaimed_folds`` 与 ``named`` 不相交：那一格说的是「Delta 里连这个键都没有」。

    ``folded`` 还要有一个归宿：每一个折进去的键要么在 ``agreed``（Delta 记了、
    池子里也有）、要么在 ``unclaimed_folds``（Delta 没记）、要么在
    ``contradicted_folds``（Delta 记反了）、要么在 ``missing_rows``（Delta 记了、
    而池子读不回来）。**哪一组都不落**的一个键读起来像「这一轮折了一个不存在的东西」。

    ``missing_rows`` 那一格**必须算归宿**：折进去过的键仍然可能读不回来（池子那一侧
    的写没落盘、或者这一份 ``folded`` 来自另一次部署的回执）。不把它算上的话，这一
    条不变量会把一个**真的报得出来的读数**判成构造错误 —— 而
    :func:`reconcile_run_delta` 的承诺是「永远返回，不抛」。一个把可能发生的输入
    变成异常的不变量，比没有它更坏：它会让人以为那种状态不存在。

    升序的理由与 ``VenueAssetStore.list_assets`` 那条相同：报告的截断丢的是尾部，
    顺序不确定时 ``_MAX_LISTED`` 砍掉的那一段就只是噪音。

    ## 六个键表都是**必填、无缺省**

    与 :class:`~marketops.domain.run_delta.RunDelta` 那九格不同。那边是「这一轮
    产出了什么」，少填一格的意思通常是「没有」；这边是**判词本身**，六格里任何
    一格被漏掉都会被 ``is_reconciled`` 读成「这一组没有差异」——
    而漏掉的恰好是 ``unclaimed_folds`` 时，一次「池子变大了而报告说没变」就这么
    被读成了账平。所以这里没有缺省：少一个字段是构造不出来，不是默认空着。

    ## 两格是**推导**的，不是存下来的

    :attr:`named` 与 :attr:`is_reconciled` 都是属性 —— 与 ``RunDelta.counts`` 同一条
    纪律：存下来的那一份与键表分家的那天，两处都读得通，而没有一处会红。
    """

    #: 调用方**主张**这一轮折进池子的业务键，由 :func:`fold_claim` 归一化（升序去重）。
    #: 走 :func:`close_run` 时它已被账核过、**等于**账；直接调
    #: :func:`reconcile_run_delta` 的调用方交上来的仍是一份主张 —— 那一条路没有
    #: ``runs``，读不到账。
    folded: tuple[str, ...]
    #: 两边对得上：Delta 主张池子里有它（五格之一），池子那一行也在。
    agreed: tuple[str, ...]
    #: 方向一。五格主张了、而池子读不出来的那些。
    missing_rows: tuple[MissingPoolRow, ...]
    #: 另外四格点名的键：它们不对池子主张任何事，所以这一层不对它们判。
    named_without_a_pool_claim: tuple[str, ...]
    #: 方向二之一：折进去了，Delta 里没有这个键。
    unclaimed_folds: tuple[str, ...]
    #: 方向二之二：折进去了，Delta 把它记成了「没进池子」。
    contradicted_folds: tuple[str, ...]

    @model_validator(mode="after")
    def _the_books_are_flat(self) -> RunDeltaReconciliation:
        groups = (
            ("folded", self.folded),
            ("agreed", self.agreed),
            ("missing_rows", tuple(one.key for one in self.missing_rows)),
            ("named_without_a_pool_claim", self.named_without_a_pool_claim),
            ("unclaimed_folds", self.unclaimed_folds),
            ("contradicted_folds", self.contradicted_folds),
        )
        for name, keys in groups:
            if list(keys) != sorted(keys) or len(set(keys)) != len(keys):
                raise ValueError(
                    f"{name} 不是升序去重的：{list(keys)!r}。\n"
                    "顺序与去重都由构造方负责：这份读数的用途就是让人照着它去改那"
                    "一条，重复的键会让人以为有两处要改。"
                )
        # 方向一的三格是 Delta 点名的键的一个**划分**：一个键在那里有且只有一种读法。
        # ``folded`` 与方向二的两格不在这个检查里 —— 它们不是第三种读法，见类文档。
        direction_one = (
            ("agreed", self.agreed),
            ("missing_rows", tuple(one.key for one in self.missing_rows)),
            ("named_without_a_pool_claim", self.named_without_a_pool_claim),
        )
        for index, (left_name, left) in enumerate(direction_one):
            for right_name, right in direction_one[index + 1 :]:
                shared = sorted(set(left) & set(right))
                if shared:
                    raise ValueError(
                        f"{left_name} 与 {right_name} 有共同的键：{shared!r}。\n"
                        "方向一的三格两两不相交 —— 一个键同时落进两格，意味着两种互斥"
                        "的读法都成立，而读的人正是照着这份读数去改那一条。"
                    )
        # 「记成了没进池子」只收**四格**点名的键。漏掉那道过滤器的实现会写出一句
        # 假话（那一格里写的是 promoted / updated 这一类主张池子里有它的格）。
        strays = sorted(set(self.contradicted_folds) - set(self.named_without_a_pool_claim))
        if strays:
            raise ValueError(
                f"contradicted_folds 里有 {len(strays)} 个键不是四格点名的：{strays!r}。\n"
                "那一格的含义是「Delta 把它记成了没进池子」，所以它只能收四格"
                "（discovered / excluded / held / blocked）点名的键。"
            )
        # 「没记」说的是 Delta 里连这个键都没有，所以它不可能同时是 Delta 点过名的。
        strays = sorted(set(self.unclaimed_folds) & set(self.named))
        if strays:
            raise ValueError(
                f"unclaimed_folds 里有 {len(strays)} 个键是 Delta 点过名的：{strays!r}。\n"
                "那一格的含义是「Delta 里连这个键都没有」；点过名的键要么对上了、要么"
                "记反了、要么读不回来。"
            )
        # 折进去的每一个键都要有一个归宿，四格之一。
        settled = (
            set(self.agreed)
            | set(self.unclaimed_folds)
            | set(self.contradicted_folds)
            | {one.key for one in self.missing_rows}
        )
        strays = sorted(set(self.folded) - settled)
        if strays:
            raise ValueError(
                f"folded 里有 {len(strays)} 个键在另外五格里都没有归宿：{strays!r}。\n"
                "折进去的每一个键要么 Delta 记了（agreed / contradicted）、要么没记"
                "（unclaimed）、要么记了而读不回来（missing_rows）—— 哪一组都不落的键"
                "读起来像「这一轮折了一个不存在的东西」。"
            )
        return self

    @property
    def named(self) -> tuple[str, ...]:
        """Delta 点名的全部业务键，升序。**推导**，见类文档末节。"""
        keys = set(self.agreed) | set(self.named_without_a_pool_claim)
        keys |= {one.key for one in self.missing_rows}
        return tuple(sorted(keys))

    @property
    def is_reconciled(self) -> bool:
        """三个方向都空 = 这一份 Delta 与这一轮折进池子的那些行说的是同一件事。"""
        return not (self.missing_rows or self.unclaimed_folds or self.contradicted_folds)


def fold_claim(handles: Iterable[str]) -> tuple[str, ...]:
    """一份折入主张的**唯一规范化**：升序、去重。``()`` 是一个真的读数，不是缺席。

    去重按**业务键的集合语义**（``sorted(set(...))``），而不是
    ``application/merge.py::ordered_union``：后者的 docstring 写的是「并集、保序、
    去重」，而它去的是**两侧之间**的重复（``seen = list(left)`` 把左表原样收下）。
    在这里用它，会看起来复用了那把唯一的尺子，而实际一个重复都没去掉。

    **一份函数、两个调用点**（本模块与 :func:`close_run`）不是复用洁癖：主张的
    **读出方式**在两处必须逐字相同，否则「账与主张对不上」会在一个读法上成立、
    在另一个读法上不成立 —— 而两处报的都是「对不上」或者都不是，看不出来是哪一种。

    它**不判**哪些句柄真的落了盘：那是 ``RunStore.folds_of`` 的事，而两者的差别
    写在模块 docstring 第二节（账本记的是池子那一行**在不在**，不是一个过滤器能
    替代的）。
    """
    return tuple(sorted({handle for handle in handles}))


def reconcile_run_delta(
    delta: RunDelta,
    *,
    folded: Iterable[str],
    assets: VenueAssetStore,
) -> RunDeltaReconciliation:
    """把一份 Delta 与这一轮的折入对起来。见模块 docstring。

    Args:
        delta: 这一轮**自己的主张**。它由做事的那些代码写下来，不由这一层推导 ——
            见模块 docstring 第二节。
        folded: 调用方**主张**这一轮折进池子的那些**池子句柄**。它**不是**那份回执
            的近义词：回执里含没有落盘的行（被拒的那些），拿它当这一侧会让一次没
            写成的折入算作写成了。是不是真的折了，本层不判（它读不到账本）——
            :func:`close_run` 拿账核过之后才调本层。
        assets: 池子。方向一按业务键逐条问它「这一行在不在」。

    Returns:
        一份读数。**它永远返回，不抛**（除了 :meth:`VenueAssetStore.get_asset`
        自己那两种抛：库的问题、跨工作区）。对不上不是异常，是一个要人看的读数 ——
        而「要不要因此不让这一轮结束」是调用方的事，见 :func:`close_run`。
    """
    # 同一块场地在 ``folded`` 里出现两次是**正常**的（一批里两行折进同一个句柄），
    # 池子那边落的是**一行**。归一化只有一处（``fold_claim``），见它的 docstring。
    folded_keys = fold_claim(folded)

    # ``RunDelta._one_disposition_per_key`` 保证一个键只有一条处置，所以这张表不是
    # 「最后一个赢」—— 它是那个不变量在这一层的执行点。
    effects = {effect.key: effect for effect in delta.effects}

    agreed: list[str] = []
    missing: list[MissingPoolRow] = []
    outside: list[str] = []
    for key in sorted(effects):
        effect = effects[key]
        if not effect.requires_an_existing_row:
            outside.append(key)
        elif assets.get_asset(key) is None:
            missing.append(MissingPoolRow(key=key, cell=effect.cell))
        else:
            agreed.append(key)

    contradicted = tuple(key for key in folded_keys if key in set(outside))
    return RunDeltaReconciliation(
        folded=folded_keys,
        agreed=tuple(agreed),
        missing_rows=tuple(missing),
        named_without_a_pool_claim=tuple(outside),
        unclaimed_folds=tuple(key for key in folded_keys if key not in effects),
        contradicted_folds=contradicted,
    )


def close_run(
    *,
    runs: RunStore,
    assets: VenueAssetStore,
    items: ActionItemStore,
    run_id: str,
    delta: RunDelta,
    status: RunStatus,
    folded: Iterable[str],
    at: datetime,
) -> Run:
    """结束这一轮：**先对账，账平了才停表**。返回结束后的那一行。

    ## 三道判据与一趟开待办，次序是承重的

    1. **主张与账**（本函数自己的第一句）：``runs.folds_of(run_id)`` 读这一轮**真的**
       往池子里写过的那些行，与 ``folded`` 里主张的那些逐键比。这**两道闸用的是同一个
       :func:`fold_claim`**，不是各写一遍 —— 写两遍的话，一次比较在某一个读法上不等、
       在另一个读法上相等，而两处报的都是「不一致」。
    2. **Delta 与主张**：``reconcile_run_delta`` 那三个方向。
    3. **Delta 的 ``actions``**：那几格 id 由下一趟写，而一份从 ``get_run`` 读回来的
       Delta 带着它们 —— ``application/run_followups.py::open_followups`` 会逐条问
       那只跟进池「这一条读不读得回来」，读不到就拒（见那个模块的最后一节）。
    4. **开待办**（``open_followups``）：这一轮产出的路线要有人去核实转介联系人，
       那些活儿在这一趟落成 ``ActionItem``，它们的 id 写回 ``delta.actions``。

    ## 为什么第 4 步在 :meth:`RunStore.stop` **之前**

    反过来的话，盘上会先落下一份**点着 ``actions`` 而按着那些 id 一个都读不到**的
    Delta —— 待办还没开。那正是这一格要防的那份假回执（``run_followups`` 的模块
    docstring 第三节讲的是同一件事的另一半）。代价如实记在这里：``stop`` 若在开完
    待办之后才拒（今天唯一够得着的是 ``RunStateError``：这一轮已经以另一套产出结束
    过），那几条 ``create`` 已经落盘了。**它不是半个 Run**：一件待办是一条独立的、
    有人要去做的活儿，而它在同一主体上的重复创建被那条 partial unique index 挡住 ——
    拿正确的 Delta 重做一次不会再长出第二条。

    前三条判据拒的时候这一趟**一个字节都没落**（``open_followups`` 一次都没被调到）：
    被拒的结束不留痕迹这句话，在这一步上仍然逐字成立。

    ## ``items`` 为什么是必填的

    与 ``runs`` / ``assets`` 同一条规矩：一个缺省的 ``None`` 会让「这个部署忘了接
    跟进池」推迟成「这一轮产出的路线一件待办都没开」—— 而后者在报告上与「这一轮
    没有产出路线」逐字相同。它是第三个存储，也是**第 3、4 步唯一的执行者**。

    第 1 道是 §B17 条件 2 的承重那一半：``folded`` 在盘上没有任何第二处能佐证它，
    于是「折了却没记」与「真的什么都没折」在账上同形 —— 而池子已经变大了。账是权威，
    主张降级成一份**要被核对的说法**。

    ## ``folded`` 收的是句柄，不是回执对象（e4）

    调用方交上来的是**一串池子句柄**（``application/pool.py::venue_handle_for_row``
    那种字符串），本函数交给 :func:`reconcile_run_delta` 的也是同一串。此前收的是
    ``promote_receipt`` 的 ``PromotionOutcome`` 序列，那一支在受理解析面上**造不出来**
    —— ``PromotionOutcome.asset`` 要么是真 ``VenueAsset``，要么是空，MCP 那一层如果
    照着回执拼一个，报出去的就是伪造的字段。收句柄消掉了这个伪造点，而它**不削弱**
    第 1 道闸：账核的是句柄，判据仍是「池子那一行在不在」。

    ## 为什么对账在这个函数里，而不是让调用方自己去看

    §B17 条件 2 要求「Run 结束有完整 Delta，**业务键双向对账**」—— 那是一句关于
    **结束**这个动作的话，不是一句关于「顺便提供一个读数」的话。把它挪到调用点上，
    分家的形态是两次结束里有一次忘了看，而报告上那一次与这一次逐字相同。

    账不平就**不结束**：这一轮留在 running，盘上一个字节都没动（``runs.stop``
    一次都没被调到）。这与 ``RunStore.stop`` 那三种拒绝是同一种处置 ——
    一个**可以被重做**的状态，而不是一次半途的写。

    ## 它不是「结束」的第二个入口

    ``RunStore.stop`` 照旧是唯一的写路径，这个函数只是给它加了两道前置判据。所以
    ``stop`` 那条幂等（同一套结局与产出重放拿回同一行）在这里原样成立：账平之后
    重放一次，两道闸照旧平，``stop`` 照旧返回同一行。

    Raises:
        RunReconciliationError: 主张与账对不上（第 1 道），对账有差（第 2 道），或者
            Delta 的 ``actions`` 里有一个读不回来的 id（第 3 道）。三种消息各自点名是
            哪几条、以及属于哪一个方向。
        RunFoldLedgerError: 这一轮的折入账读不出来（不在册、或者账本不在）。
            **不按零折入处理** —— 那是这一道闸存在的全部理由，见 ``RunStore.folds_of``。
        RunStateError / RunSpecError: 见 :meth:`RunStore.stop`。
        OpenActionItemExistsError / StoreError / EvidenceError: 见
            ``application/run_followups.py::open_followups``。
    """
    recorded = runs.folds_of(run_id)
    claimed = fold_claim(folded)
    if claimed != fold_claim(one.venue_asset_id for one in recorded):
        raise RunReconciliationError(
            _claim_refusal(run_id, claimed=claimed, recorded=recorded)
        )
    books = reconcile_run_delta(delta, folded=folded, assets=assets)
    if not books.is_reconciled:
        raise RunReconciliationError(_refusal(run_id, books))
    # 工作区从**盘上那一行**取，不由调用方交：它是那些待办要挂上去的那一格，而
    # 「这一次调用以为自己在哪」不是它的来源（``RunSpec.workspace`` 是受理那一刻写下的）。
    current = runs.get(run_id)
    if current is None:
        # 构造不出来的状态：上面 ``folds_of`` 读不出来时会抛，所以走到这里说明它在册。
        # 照 ``stop`` 的同一句话答（``unknown_run_refusal`` 逐字来自 ports 那一层），
        # 不在这里另发明一种说法。
        raise RunStateError(unknown_run_refusal(run_id))
    opened = open_followups(
        items=items,
        workspace=current.spec.workspace,
        run_id=run_id,
        delta=delta,
        at=at,
    )
    return runs.stop(
        run_id,
        delta=delta.model_copy(update={"actions": opened}),
        status=status,
        at=at,
    )


def _claim_refusal(
    run_id: str,
    *,
    claimed: tuple[str, ...],
    recorded: tuple[FoldReceipt, ...],
) -> str:
    """主张与账对不上时的那段话。**两个方向分开报**，与 ``_refusal`` 同一种排法。

    方向不同，处置不同：账上有而主张里没有，是**漏记**（主张那一侧少了一份东西，
    而要补的正是它）；主张里有而账上没有，是**一次没折成的折入被当成折了** ——
    那要看的是为什么没落盘，不是去补一份主张。

    两边都只列前 ``_MAX_LISTED`` 条：一段能照着重做的话，不靠把整张表打出来。
    """
    lines = [
        f"Run {run_id} 交上来的主张与它自己的折入账对不上。",
        "账是**权威**：每一笔都由写池子那一行的同一个事务记下（§B17 条件 2），所以"
        "两者不等时，错的是主张那一侧 —— 而**不结束这一轮**。",
    ]
    recorded_keys = tuple(sorted({one.venue_asset_id for one in recorded}))
    by_handle = {one.venue_asset_id: one for one in recorded}
    unclaimed = tuple(key for key in recorded_keys if key not in set(claimed))
    unrecorded = tuple(key for key in claimed if key not in set(recorded_keys))
    if unclaimed:
        listed = unclaimed[:_MAX_LISTED]
        lines.append(
            f"\n这一轮**真的折进去了**、而主张里没有（{len(unclaimed)} 条）—— "
            "这就是「池子变大了而报告说没变」："
        )
        lines.extend(
            f"  - {key}（折入时刻 {by_handle[key].at.isoformat()}，来源回执 "
            f"{by_handle[key].receipt_id!r} 第 {by_handle[key].ordinal} 行）"
            for key in listed
        )
        if len(unclaimed) > _MAX_LISTED:
            lines.append(f"  …还有 {len(unclaimed) - _MAX_LISTED} 条，未列出。")
        lines.append(
            "把这几行的处置补进这一轮的 Delta、并把它们写进交给本函数的 ``folded`` —— "
            "账不是被改的那一侧。"
        )
    if unrecorded:
        listed = unrecorded[:_MAX_LISTED]
        lines.append(
            f"\n主张说折进去了、而账上没有（{len(unrecorded)} 条）—— "
            "这一次折入没有落盘："
        )
        lines.extend(f"  - {key}" for key in listed)
        if len(unrecorded) > _MAX_LISTED:
            lines.append(f"  …还有 {len(unrecorded) - _MAX_LISTED} 条，未列出。")
        lines.append(
            "要看的是**为什么没落盘**：账记的是池子那一行**有没有写下去**，由写它的"
            "同一个事务记下，没有第二种读法 —— 主张说它折了，不构成它折了。"
        )
    return "\n".join(lines)


def _refusal(run_id: str, books: RunDeltaReconciliation) -> str:
    """把一份对账读数摊成一段能照着重做的话。见 :class:`RunReconciliationError`。"""
    lines = [
        f"Run {run_id} 的账对不上：它交上来的 Delta 与它折进池子的那些行说的"
        "不是同一件事。",
    ]
    if books.missing_rows:
        listed = books.missing_rows[:_MAX_LISTED]
        lines.append(
            f"\nDelta 说池子里有它（{len(books.missing_rows)} 条），而池子读不出来："
        )
        # 这一轮**折过**它没有，决定了处置：折过而读不回来，要去看池子那一侧的写；
        # 从没折过，是这份 Delta 记了一条没发生的事。两者都在 ``books`` 里，
        # 所以这一行是读出来的，不是猜的。
        folded = set(books.folded)
        lines.extend(
            f"  - {one.key}（这一轮的处置是 {one.cell.value}；"
            + ("这一轮折过它，池子那一侧的写没落盘" if one.key in folded else "这一轮没折过它")
            + "）"
            for one in listed
        )
        if len(books.missing_rows) > _MAX_LISTED:
            lines.append(f"  …另有 {len(books.missing_rows) - _MAX_LISTED} 条")
        lines.append(
            "  处置：这一条的主张没有落点 —— 去看池子那一侧的写，或者去看这一份 Delta "
            "为什么记了一条没发生的事。"
        )
    if books.unclaimed_folds:
        listed = books.unclaimed_folds[:_MAX_LISTED]
        lines.append(
            f"\n这一轮折进了池子（{len(books.unclaimed_folds)} 条），Delta 里一个字"
            "都没有："
        )
        lines.extend(f"  - {key}" for key in listed)
        if len(books.unclaimed_folds) > _MAX_LISTED:
            lines.append(f"  …另有 {len(books.unclaimed_folds) - _MAX_LISTED} 条")
        lines.append(
            "  处置：给这几条补上处置。**这一条正是「池子变大了而报告说没变」**—— "
            "纠错与 currentness 改变对「新增了几行」是零，所以按计数对账读不出它。"
        )
    if books.contradicted_folds:
        listed = books.contradicted_folds[:_MAX_LISTED]
        lines.append(
            f"\n这一轮折进了池子（{len(books.contradicted_folds)} 条），而 Delta 把它"
            "记成了「没进池子」："
        )
        lines.extend(f"  - {key}" for key in listed)
        if len(books.contradicted_folds) > _MAX_LISTED:
            lines.append(f"  …另有 {len(books.contradicted_folds) - _MAX_LISTED} 条")
        lines.append(
            "  处置：改这几条的处置（excluded / held / blocked 都在说「它没进池子」，"
            "而池子里已经有它了）。"
        )
    lines.append(
        "\n账平之前这一轮不结束（它还留在 running，盘上一个字节都没动）。"
    )
    return "\n".join(lines)


__all__ = [
    "MissingPoolRow",
    "RunDeltaReconciliation",
    "close_run",
    "fold_claim",
    "reconcile_run_delta",
]

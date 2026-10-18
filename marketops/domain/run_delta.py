"""RunDelta —— 一轮 Run 之后，**每一件它碰过的东西去了哪里**（计划 §B17）。

契约原文是 §B17 末尾那一句：「每个 Run 结束持久化 ``RunDelta``：discovered、
promoted、updated、superseded/withdrawn、excluded、held、blocked、unchanged、routes、
evidence/artifacts、Action/Review、usage/unknown usage、next_recommendations。」

## 为什么是「每一行的处置」，不是「一张计数表」

先例是 ``domain/receipt.py``：母池回执记的同样是**每一行的去向**，而不是一个
「导入了 N 行」。理由在那里写得很清楚，在这里逐字成立 —— 一条静默丢掉的行与
「那一行本来就不在池子里」在报告里长得一样，而前者的后果是剩下的行被当成全池。

所以 :attr:`RunDelta.counts` 是**推导出来的**，不是存下来的：存下来的那一份与
处置表分家的那天（有人只加了一条处置、忘了加计数），报告上两处都读得通，而没有
一处会红。

## 「池子变大」不只是新增

§B17 逐字：「'池子变大'包括纠错和 currentness 改变，不只计算新增行。」九格里只有
``discovered`` / ``promoted`` 是新增，其余七格里有纠错（``updated`` /
``superseded`` / ``withdrawn``）、有范围判定（``excluded``）、有停住（``held`` /
``blocked``），还有一格是**什么都没变**（``unchanged``）。

最后那一格是承重的。没有它，「这一轮重新看过、结论不变」与「这一轮什么都没做」
在回执上同形 —— 而 §B17 条件 2 要求后者（合法零新增）也能以 ``unchanged`` /
``held`` / ``blocked`` 结束。哪一个结局词允许什么样的 Delta，由
``ports/runs.py::ending_refusal`` 回答（两个实现共用那一份，与 ``admission_refusal``
同一条分工）。

## 一个业务键只能有一个处置

构造器拒绝同一个 ``key`` 出现在两条 :class:`PoolEffect` 上。这不是洁癖：切片 c 的
双向对账（每一个计数回得到池子里那一行、池子里每一行回得到计数）只有在
「键 → 处置」是一个**函数**时才成立。允许一个键既是 ``discovered`` 又是
``updated``，对账的两边都会读得通，而多出来的那一行没人知道是哪一行。

## 三格必须说得出理由，另外六格必须带得出名字

* ``excluded`` / ``held`` / ``blocked`` 都在说「它没进池子」，而三条的**处置完全
  不同**：一条要人去看排除项写得对不对，一条要人补齐缺的那样东西，一条是来源
  那边的问题。不给理由，三者在报告上同形 —— 与
  ``application/caps.py::TruncationDecision``（截断必须被记录）是同一条纪律。
* 其余六格都在主张池子里**一个有名字的对象**上的事实，所以必须带得出那个名字。
  另三格可以没有名字 —— 与 ``receipt.py`` 里 ``pending`` 的行带不了
  ``resolved_name`` 是同一条纪律：一个连指称对象都拿不出来的行，只能停在「待定」，
  不能声称自己是一条池子事实。

## 五格主张「池子里**已有的那一行**」，另外四格不主张

这是第三个划分，与上面两组**不是**同一件事（上面那一组问的是「这一行带不带得出
名字」，这一组问的是「这一行对不对池子的**状态**主张任何事」）：

* ``promoted`` / ``updated`` / ``superseded`` / ``withdrawn`` / ``unchanged`` ——
  五格逐字说的是「池子里已有的那一行」（取成一行 / 被改写 / 被取代 / 被撤回 /
  重新看过结论不变）。没有那一行，这五句里每一句都无从成立：没法「不变」一个
  不存在的东西。
* ``discovered`` / ``excluded`` / ``held`` / ``blocked`` —— 四格说的是「刚第一次
  看到」或「它**没**进池子」。池子里有没有那一行，对它们**不构成**主张，所以
  切片 c 的对账不对它们判（判了会把一个正常的 ``held`` 读成事故）。

执行形式是 :attr:`PoolEffect.requires_an_existing_row`：切片 c 的**方向一**
（Delta 说池子里有它，池子读不出来）就按这一格问。两侧都读得通的形态是
「一条 ``updated`` 指着一个池子里从来没有过的键」—— 报告上它与一条真的纠正
逐字相同，而它下面什么都没有。

## ``Action`` 那半格随 B24 进来了，``artifacts`` 那半格仍只收引用

§B17 的清单里那一格是「Action/Review」，两半今天都在：``reviews`` 是 Review 那一半
（``domain/review.py::case_id_for`` 的产物），:attr:`RunDelta.actions` 是 Action
那一半 —— 这一轮产出的路线要有人去核实转介联系人，那些活儿由
``application/run_followups.py`` 在**收盘那一趟**开成 ``ActionItem``
（``domain/action_item.py``），并把它们的 ``action_id`` 写回本格。

它与 ``routes`` / ``evidence`` / ``artifacts`` / ``reviews`` / ``usage`` 那五格走
**同一族校验器**（``_ids_are_distinct_and_not_blank``），理由也逐字相同：它们都是
「一串 id」，一个空元素读起来像一条引用、一个重复的引用会被下游数两遍，而去重是
**调用方**的事。两处不同，写在这里：

* **谁写。** 那五格由调用方在载荷里声明 —— ``run_reconciliation`` 那一段逐字说过
  它们各自有各自的存储与地址空间，收盘那一趟拿它们之间没有可缝的地方。本格
  **不由调用方声明**：开待办的是收盘那一趟，它写回来的是自己刚读到的那些 id。
  重放一次必须算出**同一批** id，否则 ``ports/runs.py::stopping_outcome`` 的指纹
  与盘上那一份不同，一次重放会被读成「同一轮以两种结局结束过」。
* **谁判。** 也正因为写它的是收盘那一趟，那一趟手里**就是**那只跟进池：所以本格里
  每一个 id 都被真的读回来过（``run_followups`` 用 ``ActionItemStore.get`` 逐条问），
  「一份 Delta 点了一个盘上不存在的 action」在类型上走不完。那五格没有这一步，
  不是因为它们更松，而是因为判它们要在收盘那一刻同时握着五个存储 —— 那正是
  ``run_reconciliation`` 明说过不做的那一种缝合。

**``artifacts`` 收的是引用，不是内容。** 今天有形状定义的只有 ``evidence``
（``domain/evidence.py::evidence_id``）。``artifacts`` 只判「非空、无重复」，
不判形状：一个我这一层说不出的形状，判了就是替别人定契约。

## 「接下来做什么」是给人看的，不是触发器

:attr:`RunDelta.recommendations` 是一串散文。§B17 条件 3 逐字：manual 的 Run 走完
之后**保持 idle，绝不自行扩下一批**。所以这一格在类型上只是字符串：一个能拿去起
下一轮 Run 的结构，会让那句话在盘上无处成立。起下一轮是 ``bounded_auto`` 的授权
窗口该管的事（见 :class:`~marketops.domain.enums.ExecutionMode`）。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType

from pydantic import ValidationInfo, field_validator, model_validator

from .base import StrictModel

#: 必须说得出理由的三格。见模块 docstring「三格必须说得出理由」那一节。
_MUST_JUSTIFY: frozenset[str] = frozenset({"excluded", "held", "blocked"})

#: 必须带得出指称对象的六格。见模块 docstring「三格必须说得出理由」那一节。
_MUST_NAME: frozenset[str] = frozenset(
    {"discovered", "promoted", "updated", "superseded", "withdrawn", "unchanged"}
)

#: 主张「池子里**已有的那一行**」的五格。见模块 docstring「五格主张」那一节。
_MUST_HAVE_A_ROW: frozenset[str] = frozenset(
    {"promoted", "updated", "superseded", "withdrawn", "unchanged"}
)


class DeltaCell(str, Enum):
    """这一轮对池子做下的**一种**事。闭集，九个成员，逐格一句它答什么。

    ``str`` 混入与 :class:`~marketops.domain.enums.RunStatus` 同一条理由：写进报告
    的是 ``"held"``，不是一个读的人要去别处查表的数字。

    为什么是一格一格的而不是两个布尔（「新增」/「变动」）：**每一格的处置不同**。
    一个 ``held`` 要人去补东西，一个 ``excluded`` 要人去看排除项，一个 ``blocked``
    要人去问来源那边 —— 折成一个「变动了」之后，报告能说的只剩「有变化」。
    """

    #: 这一轮**第一次**看到它：此前池子里没有这一行。
    DISCOVERED = "discovered"
    #: 从发现期的候选**取成**池子里的一行（``application/promotion.py`` 的产物）。
    #: 与 ``discovered`` 分开：看到与收下是两件事，而「看到了但没收」正是要读出来的那一格。
    PROMOTED = "promoted"
    #: 池子里已有的那一行被这一轮改写了（某一格属性变了，纠错就落在这一格）。
    UPDATED = "updated"
    #: 两个身份**归一**了：已有的那一行被另一行取代。
    SUPERSEDED = "superseded"
    #: 已有的那一行被**撤回**：这一轮查明它不该在池子里。
    #: 与 ``superseded`` 分开：一个是「换成了那一行」，一个是「它根本不该在」——
    #: 后者要让下游把引用过它的东西一并处理，前者不用。
    WITHDRAWN = "withdrawn"
    #: 按这一版 Scene 的准入/排除规则**不收**。
    EXCLUDED = "excluded"
    #: 缺东西，停在待办（缺名字、缺证据、要人回答一个问题）。
    #: **不是失败** —— 它是 §B17 条件 2 里「合法零新增仍能结束」的三种收法之一。
    HELD = "held"
    #: 取不到（来源不可达、被拒、超时）。与 ``held`` 分开：那一条要人补东西，
    #: 这一条要人去问来源那边，而把两者合成一格会让「要不要再试一次」没有依据。
    BLOCKED = "blocked"
    #: 重新看过，**结论不变**。承重的一格，见模块 docstring 第二节。
    UNCHANGED = "unchanged"


class PoolEffect(StrictModel):
    """一条处置：**哪一个**业务键、**去了哪里**、凭什么。"""

    cell: DeltaCell
    #: 业务键。今天池子那一侧是 ``application/pool.py::venue_handle_for_row`` 的
    #: 产物 —— 切片 c 的双向对账就是拿这一格对回池子里那一行。
    key: str
    #: 人类可读的指称对象。哪几格必须填、哪几格可以不填，见模块 docstring「三格必须说得出理由」那一节。
    label: str = ""
    #: 为什么是这个处置。三格必填，见模块 docstring「三格必须说得出理由」那一节。
    why: str = ""

    @field_validator("key")
    @classmethod
    def _key_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "key 不得为空。它是这一条处置对着的**那一行** —— 空着的时候，"
                "切片 c 的双向对账（计数回得到行、行回得到计数）两边都读得通，"
                "而差的那一行没人知道是哪一行。"
            )
        return value

    @model_validator(mode="after")
    def _cell_asks_for_what_it_needs(self) -> PoolEffect:
        if self.cell.value in _MUST_JUSTIFY and not self.why.strip():
            raise ValueError(
                f"{self.cell.value} 的处置必须带一条理由（why）。\n"
                "excluded / held / blocked 都在说「它没进池子」，而三条的处置完全不同"
                "（去看排除项 / 补那样东西 / 问来源那边）—— 不给理由，三者在报告上同形。"
            )
        if self.cell.value in _MUST_NAME and not self.label.strip():
            raise ValueError(
                f"{self.cell.value} 的处置必须带得出指称对象（label）。\n"
                "它主张的是池子里一个有名字的对象上的事实，而一条只有句柄的处置行"
                "读不出那是什么 —— 与 receipt.py 里 pending 的行带不了 resolved_name "
                "是同一条纪律。"
            )
        return self

    @property
    def requires_an_existing_row(self) -> bool:
        """这一格是不是在主张「池子里**已经有**那一行」。见模块 docstring「五格主张」那一节。

        **它是这一格的语义，不是调用方的选择**：`updated` 与 `held` 的差别不在
        「谁来看这一行」，而在前者说了一件关于池子当前状态的事、后者没有。
        所以这是一个属性而不是一个参数 —— 一个能传的开关会让同一条处置在两次
        对账里被要求两件不同的事。

        问这一格的是切片 c 的对账（``application/run_reconciliation.py``）：它拿
        五格里的每一个 ``key`` 去问池子「这一行在不在」，而另外四格一个都不问。
        """
        return self.cell.value in _MUST_HAVE_A_ROW


class UnaccountedUsage(StrictModel):
    """**花掉了、账本上没有**的那些调用。

    这一格存在，是因为本仓那条纪律「算不出来就抛，不返回 0」
    （``application/choices.py`` 的 ``spent_for`` 先例）：一个把算不出来的量记成 0 的
    回执，在报告上看起来与「这一轮没花钱」一模一样，而两者要人做的事相反。

    所以它是**逐条点名**的，不是一个计数：``provider`` 说的是去问谁，``calls`` 说的是
    问几次，``reason`` 说的是为什么算不出来（超时？provider 那边没有账单？）。

    ``provider`` 今天是一个自由串，**没有闭集** —— 闭在这里就是替账单那边定一个
    我这一层说不出的词表。它填的是账单上会出现的那家名字。
    """

    provider: str
    calls: int
    reason: str

    @field_validator("provider", "reason")
    @classmethod
    def _named(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(
                f"UnaccountedUsage.{info.field_name} 不得为空 —— 一条说不出「去问谁」"
                "或「为什么算不出来」的未记账，与一条忘了填的记录在盘上一模一样。"
            )
        return value

    @field_validator("calls")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                "calls 至少是 1。**零次的未记账不是一只空着的手**：它会在求和时"
                "贡献 0，于是「有几笔没对上」这个数被自己抹平。没有这一笔，就不要"
                "构造这一条。"
            )
        return value


class RunDelta(StrictModel):
    """一轮 Run 的完整产出。**一次结束一份**，与结局在同一个写事务里落下。

    :attr:`usage` **不记金额**，只记**哪几笔**：金额只有一个权威
    （``ports/budget.py`` 的那一处 ``SUM``），在回执里再抄一个数就是第二个答案 ——
    而两个答案分家的那天，读者会照回执上那个数去做决定。要钱数就拿着这几个
    ``reservation_id`` 去账本读。记不上的那几笔走 :attr:`unaccounted`。
    """

    #: 逐行的处置。**计数由它推导**，见模块 docstring 第一节。
    effects: tuple[PoolEffect, ...] = ()
    #: 这一轮产出的路线（``domain/entities.py`` 那一族的 id）。
    routes: tuple[str, ...] = ()
    #: 这一轮抓到的证据（``domain/evidence.py::evidence_id`` 的产物）。
    evidence: tuple[str, ...] = ()
    #: 这一轮写下的产物引用。见模块 docstring「``artifacts`` 那半格」那一节。
    artifacts: tuple[str, ...] = ()
    #: 这一轮开出的 Review 案件（``domain/review.py::case_id_for`` 的产物）。
    reviews: tuple[str, ...] = ()
    #: 这一轮产出的路线要有人跟进，那些活儿的 ``action_id``
    #: （``domain/action_item.py::ActionItem.opened`` 的产物）。**由收盘那一趟写回来**，
    #: 不由调用方声明 —— 见模块 docstring 第六节。
    actions: tuple[str, ...] = ()
    #: 这一轮记下的预留（``domain/budget.py::reservation_id`` 的产物）。见类文档。
    usage: tuple[str, ...] = ()
    #: 花掉了但账本上没有的。见 :class:`UnaccountedUsage`。
    unaccounted: tuple[UnaccountedUsage, ...] = ()
    #: 给人看的下一步。见模块 docstring「接下来做什么」那一节 —— 它**不是**触发器。
    recommendations: tuple[str, ...] = ()

    @field_validator("routes", "evidence", "artifacts", "reviews", "actions", "usage")
    @classmethod
    def _ids_are_distinct_and_not_blank(
        cls, value: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} 里不得有空元素 —— 空元素读起来像一条引用。")
        if len(set(value)) != len(value):
            raise ValueError(
                f"{info.field_name} 里有重复的引用：它们会被下游数两遍。\n"
                "去重是**调用方**的事（``application/merge.py::ordered_union`` 就是那把"
                "尺子），不在这里悄悄做 —— 悄悄做会让「这一轮到底写了几次」读不出来。"
            )
        return value

    @model_validator(mode="after")
    def _one_disposition_per_key(self) -> RunDelta:
        seen: dict[str, str] = {}
        for effect in self.effects:
            previous = seen.get(effect.key)
            if previous is not None:
                raise ValueError(
                    f"业务键 {effect.key!r} 在这一份 Delta 里有两个处置："
                    f"{previous} 与 {effect.cell.value}。\n"
                    "切片 c 的双向对账只有在「键 → 处置」是一个函数时才成立；"
                    "允许一个键有两个处置，对账的两边都会读得通，而多出来的那一行"
                    "没人知道是哪一行。"
                )
            seen[effect.key] = effect.cell.value
        return self

    @property
    def counts(self) -> Mapping[DeltaCell, int]:
        """逐格的计数，**由处置表推导**。九格恒在（没有的那几格是 0）。

        九格恒在而不是「用到的才有」：报告那边 ``counts[cell]`` 的读法因此不需要
        先判在不在 —— 而「不在」与「是 0」在那一段代码里会长得一样。
        """
        tally = {cell: 0 for cell in DeltaCell}
        for effect in self.effects:
            tally[effect.cell] += 1
        return MappingProxyType(tally)

    @property
    def touched(self) -> bool:
        """这一轮**碰过**至少一行池子里的东西。

        它答的不是「有没有新增」—— 那是 ``counts[DISCOVERED] + counts[PROMOTED]``。
        两件事分开，因为 §B17 条件 2 要的正是「零新增但碰过一堆东西」这种结束。
        """
        return bool(self.effects)

    @property
    def is_empty(self) -> bool:
        """九格全空：这一轮**没有留下任何**说过它做过什么的记录。

        它与 :attr:`touched` 是两个问题：``touched`` 问「池子上动过没有」，
        ``is_empty`` 问「整份回执上有没有一件事可查」。只有 ``effects`` 的一场
        研究（比如只抓到证据、没有任何池子处置）``touched`` 是 ``False`` 而
        ``is_empty`` 也是 ``False``。

        ``Completed`` 的 Run **必须**不是空的（``ports/runs.py::ending_refusal``）：
        「跑完了，什么都没碰」与「压根没跑」在回执上必须不同。

        :attr:`actions` 这一格也数进来，尽管它今天**推不出**独立的证据：它是从
        :attr:`routes` 算出来的，而 routes 非空时这里早就不是空了。收它进来不是
        为了多拦一种情形，而是因为「整份回执上有没有一件事可查」这句话的主语是
        **每一格** —— 漏掉一格的那天，一格能独立变非空的格子会在这里被读成空。
        """
        return not any(
            (
                self.effects,
                self.routes,
                self.evidence,
                self.artifacts,
                self.reviews,
                self.actions,
                self.usage,
                self.unaccounted,
                self.recommendations,
            )
        )

    @property
    def keys(self) -> tuple[str, ...]:
        """这一轮碰过的业务键，按处置表上的顺序。切片 c 对账的左边。"""
        return tuple(effect.key for effect in self.effects)


__all__ = [
    "DeltaCell",
    "PoolEffect",
    "RunDelta",
    "UnaccountedUsage",
]

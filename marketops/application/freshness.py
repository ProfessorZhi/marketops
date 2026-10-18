"""把「这一行今天还能不能用」判出来，并把结论**写回那一行**。

判据不在这个模块里。 ``domain/temporal.py`` 是唯一一处按时间锚点算出
``currentness`` 的地方（:func:`~marketops.domain.temporal.evaluate_freshness`），
``domain/profile.py`` 是唯一一处承载时限声明的地方，``domain/entities.py`` 是唯一
一处持有那两族行的地方。本模块只做一件事：**在两者之间搬一次，并说清没搬成的是哪一些**。

## 行的时刻 → 断言

一条路线行不是一条 ``TemporalAssertion``：它多了邮箱、姓名、等级，少了
``subject_ref`` 与 ``predicate`` 的显式取值。转换只在这一处做，而它必须与行上那条
行级判据（``entities._freshness_belongs_to_this_row``）用的是**同一个 id 公式** ——
所以断言 id 取自 ``row.expected_assertion_id``，不在这里重算一遍。重算的后果很具体：
两边各自自洽、只是判的不是同一条断言，于是校验永远通过、结论永远挂错行。

## 没有时刻锚点的行**不判**，也不替它挑一个时刻

``first_seen_at`` 为空时这一行没有观测时刻，而断言 id 的原像里就有它 —— 换句话说
**这条断言的身份算不出来**。这一格诚实的取值是「没判过」，于是它留 ``None``，
并且按 ``NO_TIME_ANCHOR`` 单独计数。

不许拿 ``fetched_at``、更不许拿 ``now`` 顶上：那会让一条本来无从复核的行看起来
判过了，而报告上只看得出「判了，结论是 stale」—— 那是**另一个结论**。这条与
``Freshness`` 的 ``UNKNOWN``（判了、判不出来）也不同：``UNKNOWN`` 是判据给出的答案，
``NO_TIME_ANCHOR`` 是**判据没能开工**。两者都留空，但原因不同，所以计数也分开。

## ``has_conflict`` **不**折成 ``contradicted_by``

``evaluate_freshness`` 的第一条分支是「被另一条断言反驳 → conflicting」，它读的是
``assertion.contradicted_by``。路线行上的 ``has_conflict`` 是**另一件事**：它是绑定层
的冲突（同一页上有多个邮箱而无法确定哪一条对应哪个人，SOP L105），不是「另一条不相容
的断言」—— 后者要的是对方那条断言的 **id**，而那一维不在这一行上。

把 ``has_conflict`` 折进去就得替对方编一个 id，而 id 是内容寻址的：编出来的那个
无法复核，且 ``TemporalAssertion._conflicting_requires_a_counterpart`` 会当成
「指得出和谁冲突」放行。所以 ``CONFLICTING`` 这一档**今天没有生产者**，这是刻意的，
不是遗漏（本模块的测试里有一条把它钉住）。

## 重建成新行，而不是 ``model_copy``

``model_copy`` **不走校验**（pydantic 的行为）。用它把结论贴上去，那一格就永远
不会被行级判据复核一次 —— 而判据存在的理由正是「结论会走丢」。所以这里走
``model_validate``：把这一行序化成一份普通的字典、放进结论、再**构造一次**。
代价是每行一次往返；换来的是每一行落盘之前都真的过了一遍自己的判据。

同一个理由适用于 ``grade``：等级也是往行上贴的一格，见
:func:`~marketops.application.pipeline` 的用法。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TypeVar

from ..domain.entities import ContactRoute, RouteOpportunity
from ..domain.errors import ConfigError
from ..domain.profile import ProfileBundle
from ..domain.temporal import (
    Currentness,
    Freshness,
    FreshnessPolicy,
    FreshnessReason,
    TemporalAssertion,
    evaluate_freshness,
)

#: 那两族行。它们在**本模块**里被当成同一件事处理：都带同样的五个时刻与那一格结论，
#: 判据也共用（``entities._freshness_belongs_to_this_row``）。
Row = ContactRoute | RouteOpportunity
_RowT = TypeVar("_RowT", ContactRoute, RouteOpportunity)


class FreshnessOutcome(str, Enum):
    """一次判定**发生了什么**。

    这两档必须分开，因为它们不是同一种「没有结论」：

    * :attr:`JUDGED` —— 判了。结论在那一行上（取值可能是 ``unknown``，那也是判据
      给出的答案）。
    * :attr:`NO_TIME_ANCHOR` —— **判据没能开工**：这一行没有观测时刻，断言 id 算不
      出来。它今天没有生产者（时刻由 ``bind`` 写下），但一个空锚点的行在库里是可能
      存在的，而它必须能被数出来。
    * :attr:`INVALID_SOURCE_TIME` —— 判据开工了，**但它手里的那个时刻不成立**：
      来源自述的发布时刻晚于我们观测到它的时刻。结论照样写回那一行
      （``Currentness.INVALID_SOURCE_TIME``），所以这不是「没判」，是判出了一个
      关于**材料**的结论。

    第三档必须与前两档分开，是因为它们的**下一步动作**不同：``NO_TIME_ANCHOR``
    等一个观测时刻，``INVALID_SOURCE_TIME`` 等一个人去看那个解析器 —— 而合起来
    报成「时间不明」的话，一次坏解析会安静地变成一片「材料缺时间」。
    """

    JUDGED = "judged"
    NO_TIME_ANCHOR = "no_time_anchor"
    INVALID_SOURCE_TIME = "invalid_source_time"


@dataclass(frozen=True)
class InvalidSourceTime:
    """一行声称的时刻不可能成立 —— 连同那句**为什么**。

    这里带的是理由文本而不是异常，与 :func:`assertion_for` 的取舍是同一条：
    一个坏页面不该让整轮研究停下来（§5），而它也不该什么都不留。抛出会打死整轮；
    静默丢掉会让这一行看起来只是「时间不明」；把日期钳到今天则更坏 —— 那是把
    一条算不出年纪的证据写成刚验证过的。所以：如实报出，计数，继续。
    """

    reason: str


@dataclass(frozen=True)
class Judgement:
    """一行 + 它的判定结论。"""

    row: Row
    outcome: FreshnessOutcome

    @property
    def currentness(self) -> Currentness | None:
        """判出来的取值；没判过时 ``None``（不是 ``UNKNOWN`` —— 那是判据的答案）。"""
        if self.row.freshness is None:
            return None
        if self.outcome not in (
            FreshnessOutcome.JUDGED,
            FreshnessOutcome.INVALID_SOURCE_TIME,
        ):
            return None
        return self.row.freshness.currentness


def policy_of(bundle: ProfileBundle) -> FreshnessPolicy:
    """这份 bundle 声明的时限。**没声明就是一次失败**，不落一个缺省。

    与 ``application/decision_specs.compile_spec`` 同一条：缺的那一侧在**调用时**
    显式失败。这里失败得比那里更硬，是因为 ``currentness`` 会落到每一行产出上，
    而一行带着「按某份口径算出的 stale」与一行什么都没带的区别，在读的人眼里
    不是「少了点东西」，是「这行的时效已经判过了」。
    """
    if bundle.freshness is None:
        raise ConfigError(
            f"profile {bundle.profile_id!r} 没有声明 freshness 时限，无法判定产出是否仍当前。\n"
            "  时限是业务口径，只能来自 profile.yaml 的 freshness 块"
            "（policy_version / current_within_days / historical_after_days）。\n"
            "  这里不替它取一组缺省天数：那组数字看起来完全合理，却没有任何人认过，"
            "而每一行产出都会显得是按某个业务标准算出来的。"
        )
    return bundle.freshness


def _carried_currentness(previous: Freshness | None) -> Currentness:
    """把上一轮判据的取值**按需要**带进来，只带一种：``HISTORICAL``。

    ``evaluate_freshness`` 会读断言的当前取值，而它只认 ``HISTORICAL`` ——
    一条已经判定为历史的断言不因重新求值而降级成 ``STALE``（历史是一条更强的
    结论：来源当时说的是对的）。

    其余取值一律转成 ``UNKNOWN`` 再喂进去，包括 ``CURRENT``。这不是洁癖：
    判据**从不读自己上一次的产出**，否则一个过期的判定会靠自我指认永远续期。
    在这里原样传一遍 ``CURRENT`` 就等于替判据破例（传进去的和它读到的虽然不同名，
    但效果一样），而且 ``TemporalAssertion`` 还有一条判据要求
    ``currentness=current`` 必须带时间锚点 —— 一条只有观测时刻的行会在构造时就红。

    ``NOT_SEEN`` 与 ``CURRENT`` 走同一条路（落回 ``UNKNOWN``），而理由是**更硬**的
    那一头：``HISTORICAL`` 之所以能带进来，是因为它是一条关于**历史**的结论，
    而重算不改变历史（那一页当年确实那么写）。``NOT_SEEN`` 恰恰相反 —— 它是一条
    关于**这一次观测**的结论。把它带进下一轮，等于说「上一次复查没看到它」是
    「这一次复查也没看到它」的理由，而这两件事之间没有任何因果关系：恰恰是
    **下一次复查**可能看到它（条目挪了位置、站点改回来了），而这条结论会挡住那次
    更正。一次观测失败如果可以被继承，「没看到」就会在一次运行里固化成「一直没看到」。
    """
    if previous is not None and previous.currentness is Currentness.HISTORICAL:
        return Currentness.HISTORICAL
    return Currentness.UNKNOWN


def assertion_for(row: Row) -> TemporalAssertion | InvalidSourceTime | None:
    """这一行代表的那条断言；**没有时刻锚点时为 ``None``**。

    ``None`` 不用异常表达，因为「这一行没有观测时刻」不是错误，是一种必须被
    计数与处置的处境（见模块 docstring）。调用方据此落 ``NO_TIME_ANCHOR``。

    同样不抛的还有 :class:`InvalidSourceTime`：那一行的时刻连成立都不成立，
    而构造 :class:`~marketops.domain.temporal.TemporalAssertion` 会为此抛出。
    这里**在构造之前**先问一次判据（``TemporalAssertion.impossible_source_time``
    —— 校验器读的是同一个函数，所以两处不可能给出不同答案），把那次抛出换成
    一个可计数的结果。理由就是 §5 的 fail-soft：一个页面上的未来日期是一条关于
    **那一页**的坏消息，不是关于整轮研究的。
    """
    assertion_id = row.expected_assertion_id
    if assertion_id is None:
        return None
    impossible = TemporalAssertion.impossible_source_time(
        row.source_published_at, row.first_seen_at  # type: ignore[arg-type] - 上面已判过非空
    )
    if impossible:
        return InvalidSourceTime(reason=impossible)
    return TemporalAssertion(
        assertion_id=assertion_id,
        subject_ref=row.assertion_subject_ref,
        predicate=row.assertion_predicate,
        value=row.asserted_value,
        source_published_at=row.source_published_at,
        # ``observed_at`` 与池子行上的 ``first_seen_at`` 是**同一个瞬间**，只是
        # 断言这一层用的词是 ``observed_at``（见 ``domain/temporal.py`` 的模块文档）。
        observed_at=row.first_seen_at,  # type: ignore[arg-type] - 上面已判过非空
        last_verified_at=row.last_verified_at,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        currentness=_carried_currentness(row.freshness),
        evidence_ids=list(row.evidence_ids),
    )


def rebuild(row: _RowT, **updates: object) -> _RowT:
    """把这一行**重新构造一次**，带上几格新值。

    走 ``model_validate`` 而不是 ``model_copy``：后者不跑任何校验器，于是「刚贴上去
    的那几格是不是自洽的」没有判据。这里每贴一次都要过一次 —— 落到库里的每一行
    因此都真的过过自己的判据，而不是「构造时过一次、后来改了就说不准」。

    代价是每行一次序列化往返。它换掉的是本仓最贵的一类缺陷：一行看起来正常、
    内容却与它自己声称的判据不符。
    """
    return type(row).model_validate({**row.model_dump(), **updates})


def judge(
    row: _RowT,
    policy: FreshnessPolicy,
    *,
    now: datetime,
    seen_on_recheck: bool | None = None,
) -> Judgement:
    """判这一行，并把结论写到返回的那一行上。

    判定本身只在 :func:`~marketops.domain.temporal.evaluate_freshness` 里；
    这里做的是「行的时刻 → 断言 → 结论 → 新的一行」这一趟搬运，以及把
    **没搬成的那一类**如实报出来。

    ``seen_on_recheck`` 一路透传下去，**不在这里加工**。它是 §9·4 的另一半：
    复查时没再看到这一行，产出的是一次**观测失败**（``Currentness.NOT_SEEN``），
    不是一条「他走了」。缺省 ``None`` = 这一轮没有做复查 —— 本函数的两族行
    （路线与机会）共用这一条，所以「复查」这件事对两者的意思必须相同。

    这里**没有**参数能写 ``valid_to``，而这要如实说成一条**调用点**上的事实，
    不是结构上的保证：:func:`rebuild` 是通用的，它接任意字段，所以「写不出虚构的
    离职日期」今天靠的是本模块与 pipeline 的每一处重建都只传那三格
    （``freshness`` / ``grade`` / ``grade_reason``）。要把它变成结构上的保证，
    需要一条行级判据 ——「``valid_to`` 必须指得出声明它的那行证据」—— 而那一行
    证据要在库里查，行上只有 id。今天没有这条判据，记在这里，不假装已经有了。
    """
    assertion = assertion_for(row)
    if assertion is None:
        return Judgement(row=row, outcome=FreshnessOutcome.NO_TIME_ANCHOR)
    if isinstance(assertion, InvalidSourceTime):
        return Judgement(
            row=rebuild(
                row,
                freshness=Freshness(
                    assertion_id=row.expected_assertion_id,  # type: ignore[arg-type]
                    currentness=Currentness.INVALID_SOURCE_TIME,
                    reason=FreshnessReason.INVALID_SOURCE_TIME,
                    policy_version=policy.policy_version,
                    policy_calibrated=policy.calibrated,
                    evaluated_at=now,
                    # 不算 ``age_days``：那个数字要拿 ``now - source_published_at`` 算，
                    # 而在这一档里它是**负的**。一个负的年纪比没有年纪更坏 —— 报告上
                    # 会读到「-300 天前发布」，那看起来像一个数值问题，而不是一行
                    # 本来就不成立的时刻。理由在 ``reason`` 那一格里，写的是判据原文。
                    age_days=None,
                ),
            ),
            outcome=FreshnessOutcome.INVALID_SOURCE_TIME,
        )
    conclusion = evaluate_freshness(
        assertion, policy, now=now, seen_on_recheck=seen_on_recheck
    )
    return Judgement(row=rebuild(row, freshness=conclusion), outcome=FreshnessOutcome.JUDGED)


__all__ = [
    "FreshnessOutcome",
    "InvalidSourceTime",
    "Judgement",
    "Row",
    "assertion_for",
    "judge",
    "policy_of",
    "rebuild",
]

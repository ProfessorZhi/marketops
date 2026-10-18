"""§B17 的**增量面**：第二轮复用已沉淀事实，只补新/缺/过期。

权威是 ``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:256`` 条件 1 的逐字：

    同一 Workstream 连续两个 manual Run：第二轮明确复用第一轮已沉淀事实，只补新/
    缺/过期；重复提交同一 idempotency key 不重复池子或成本。

（``Workstream`` 全仓不存在 —— 见 ``docs/B17_DESIGN_SLICES_2026-09-28.md`` 的差量 1：今天承载那一格的
是 ``workspace``。所以这里读的是 :attr:`Run.spec.workspace`。本模块只判前半句；
后半句是下面第六节那一条拒绝。）

## 一、四格里**三格已经在盘上**，本片不做第二处判定

``domain/maintenance.py::aspect_states`` 已经给**每一格（含没到期的）**一个结论，
它的 docstring 逐字写着「它是**读法**，不是第二处判定」。三格照抄：

* ``AspectStatus.NEVER_OBSERVED`` → :attr:`IncrementVerdict.NEW`（从没看过这一面）
* ``AspectStatus.DUE`` → :attr:`IncrementVerdict.EXPIRED`（看过，窗口过了）
* ``AspectStatus.CURRENT`` → **还要问第二根轴**（看过，窗口未到）

三格照抄，**但处置必须分得开** —— ``AspectStatus`` 对 ``NEVER_OBSERVED`` 那一档
单独加了一句：「它也**到期**，但不与前者合并……两者的处置都是「去看」，但报告上必须
分得开 —— 一个说「维护跑得对不对」，一个说「这个市场建起来了没有」。」本片的 ``NEW``
与 ``EXPIRED`` 就是那句「分得开」的落地，而 ``reason`` 是它唯一说得出口的地方：一档
说「这一格还没有任何基线」，一档说「有基线、该复核了」。合成一档会让那两句话在同一份
报告上读成同一件事。

## 二、真正的缺口是第四格：「缺」

``CURRENT`` 不够，而理由不是「钟不准」—— 是 ``SOURCE_COVERAGE`` 这一格会用**一个
空盘点满足钟**。一个主体名下一条可绑定证据都没有时，``census_for`` 照样返回
（``rows=0``、``store_present=True``），``run_once`` 照样记一条观测。于是这一格在钟上
是 ``CURRENT`` —— 而 ``AspectStatus.CURRENT`` 的处置逐字是「记着它，别动」—— 它欠的
却是一笔**从来没被满足**的账。钟只会把它推得更远，不会让它露出来。

所以这一格要问第二根轴：**盘点**。判据是 ``EvidenceCensus.new_source_gaps``，而它的
docstring 逐字说它「数的是「几笔待办」，不是「几个来源」」—— 手上的 N 条
``SEARCH_SNIPPET`` 不是 N 笔待办，它们是一件事。

**天真的划分（``works = NEVER_OBSERVED ∪ DUE``，也就是今天的 ``what_is_due``）会把
这样一个主体整个报成「复用」** —— 而「复用」是一句「有东西可复用」的话，那里没有东西。
这与切片 c 挡住的错同形：报告上说没变，而事实是缺。

## 三、只有 ``SOURCE_COVERAGE`` 那一格问盘点

``application/refresh.py`` 逐字：「``SOURCE_COVERAGE`` 那一面的主语就是**我们手上有
哪些来源** —— 那些东西本来就住在库里，数一遍它们**就是在**观测这一面，而且是完整地
观测」。别的面的主语不是盘点，拿它去问那些面是把两个主语混成一个。

## 四、优先级：钟先判，盘点只在 ``CURRENT`` 上问

「新」压过「缺」。两者都可能是空盘子，但「缺」的前提是「我们看过、结论是没有」，而
从没看过的那一格欠的是**第一次去看**。于是盘点**只在 ``CURRENT`` 上被调用** ——
「到期的格子不读盘」与「别的面不读盘」都是这一条的直接后果，不是两处各自省下的一次读。

## 五、格子空间是闭的

``known = 观测里出现过的 ∪ 显式声明的``（``_known_subjects`` 逐字：「一个会自己发现
主体的选择器必然也会自己**发明**主体」）。所以「不在格子里」**不是**复用 —— 一个从未
声明、从未观测过的对象不在这一份读法里，把它读成复用正是切片 c 那个错的反面。

## 六、开工之前的那一句

``admit`` 是幂等的：同一个键重发拿回的是**同一行**，哪怕那一行已经结束
（``application/run_admission.py`` 逐字：「幂等由主键保证，不是靠写之前查一下」）。
于是「重发同一个键」在**受理**上长得像成功，而它要挡的是第二轮真的又跑一遍 —— 池子
第二次折、成本第二次花，而报告上第一轮与第二轮逐字相同。挡它的位置只有这一处：
开工之前。已经结束的一轮**一格都不产出**，存储也就一次都不会被读到。

## 七、这一层**不判**的四件事

不判「哪一批该看」（那是 Scene 的规则、``what_is_due`` 的位置）；不判成本与上限
（条件 1 没有提上限，``limit`` 与截断是 ``application/caps.py`` 那一族的事）；不判
``RunDelta``（那是结束面，``application/run_reconciliation.py``）；不判「上一轮记下的
观测是不是真的」（要让它不可伪造需要「哪一轮记了哪一格」落盘，而今天没有 —— 那是条件 7
的正面问题，本片不为它猜一个形状）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from enum import Enum

from pydantic import field_validator, model_validator

from ..domain.base import StrictModel
from ..domain.errors import RunStateError
from ..domain.maintenance import (
    Aspect,
    AspectState,
    AspectStatus,
    ChangeSignal,
    Observation,
    RefreshPlan,
    aspect_states,
)
from ..domain.run import Run
from ..domain.util import check_canonical_id, to_utc
from ..ports.store import EvidenceStore
from .refresh import census_for

#: B17 的 ``run_id`` 是 ``sha256_text(...)[:32]`` —— 32 位小写十六进制。
_RUN_ID_LENGTH = 32

_HEX_DIGITS = frozenset("0123456789abcdef")


class IncrementVerdict(str, Enum):
    """第二轮拿到这一格之后该做什么。**四格各自对应一种处置**，不是四档严重程度。

    * ``REUSE`` —— 上一轮看过，窗口未到，而且**有东西可复用**。处置：不动它。
    * ``NEW`` —— 从没看过这一面。处置：这一轮第一次去看。
    * ``MISSING`` —— 上一轮看过、窗口未到，而名下**一条能当依据的来源都没有**。
      处置：去给它找一条能当依据的来源。
    * ``EXPIRED`` —— 上一轮看过，窗口过了。处置：重取。

    ``MISSING`` 与 ``EXPIRED`` 分开、``MISSING`` 与 ``NEW`` 分开，是三条不同的处置：
    一个说「去找一条」，一个说「去看一眼」，一个说「把手上这条再取一遍」。合成一档
    会让其中两档永远没人查（``domain/reuse.py`` 那一族对「处置不同、理由要分开」写的
    是同一条纪律）。
    """

    REUSE = "reuse"
    NEW = "new"
    MISSING = "missing"
    EXPIRED = "expired"


#: 由**钟**说了算的两档处置。``CURRENT`` 刻意不在表里 —— 它是唯一一档还要问盘盘的，
#: 于是「这张表覆盖了除 ``CURRENT`` 以外的每一档」是一条判得出来的话（没有执行器的
#: 覆盖表会在一档新状态出现时静默漏掉它）。
_VERDICT_OF_STATUS: dict[AspectStatus, IncrementVerdict] = {
    AspectStatus.NEVER_OBSERVED: IncrementVerdict.NEW,
    AspectStatus.DUE: IncrementVerdict.EXPIRED,
}


class IncrementCell(StrictModel):
    """一个 ``(主体, 面)`` 在这一轮里的处置。

    八个格与 :class:`~marketops.domain.maintenance.AspectState` **同形**（多出来的
    只有 ``verdict`` 与 ``reason``）—— 因为它是那一份读法加一根轴的**投影**，不是
    第二份读数：``status`` 换成了处置、``stale_reason`` 换成了给人看的理由，而两个
    时间格逐字照抄（``NEVER_OBSERVED`` 的两个都是 ``None``）。

    ``reason`` **必填、无缺省**：它是给人看的那一句，四档处置要分得开。留一个缺省
    会把「这一格为什么是这个处置」变成可选 —— 而这一步的产出正是给人去照着做的东西。

    ``policy_version`` 跟着**格**走而不是只在这一份增量上写一次：``RefreshPlan``
    的 docstring 逐字写着「§11 要求每一格都带着 ``policy_version``」。
    """

    subject_kind: str
    subject_id: str
    aspect: Aspect
    verdict: IncrementVerdict
    reason: str
    #: 上一轮真的看过这一面的时刻。``NEW`` 那一档没有 —— 它从没被看过。
    last_verified_at: datetime | None = None
    #: 这一格的下一个时刻。``NEW`` 那一档没有（还没看过，就没有「下一次」）。
    #: ``DUE`` 那一档装的是**本轮逾期的起点**，不是「拖到第几轮」。
    next_due_at: datetime | None = None
    policy_version: str = ""

    @field_validator("subject_kind", "subject_id")
    @classmethod
    def _non_empty(cls, value: str, info) -> str:
        if not value or value != value.strip():
            raise ValueError(f"{info.field_name} 不能为空或首尾带空白")
        return value

    @field_validator("reason")
    @classmethod
    def _says_why(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "reason 不得为空。这一格的处置是给人照着做的，一句说不出理由的"
                "处置与一档猜出来的处置在报告上长得一样。"
            )
        return value

    @field_validator("last_verified_at", "next_due_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)


class RunIncrement(StrictModel):
    """一轮 Run 开工前的一份增量读数。

    ## ``cells`` 是一份**划分**

    同一个 ``(主体, 面)`` 有两个结论，就是在这一点上说了两句不同的话 —— 而
    :attr:`works` 会把这一格数两遍（于是「这一轮要补几格」有两个答案）。所以校验器
    守着它，不是靠构造方自觉：一份读得通而数错的读数，比一份读不出来的更坏。

    ## 次序不在这里定义一遍

    格子按 ``aspect_states`` 给出的次序（``sorted(known)`` × ``ALL_ASPECTS``）原样
    带出来 —— 那里的注释逐字写着「构造次序**就是**返回值次序……排两遍等于把「次序是
    什么」写成两处」。所以这里没有一条排序校验器，那会是第二处。

    ## 表格四档都出现，**零也出现**

    :attr:`counts` 覆盖 :class:`IncrementVerdict` 的每一个成员，缺的那一档是 ``0``
    而不是缺席 —— 与 ``EvidenceCensus.not_measured`` 同一条纪律：说不出来就写明白，
    绝不让「这一轮没有过期的」与「这一轮没测过期」在报告上长得一样。
    """

    #: B17 的 ``run_id``，**内容寻址**（32 位小写十六进制）。见
    #: :func:`marketops.domain.run.run_id`。
    #:
    #: 这一格刻意不接受 ``run-20260926t101530`` 那种形状：
    #: ``application/pipeline.py::_derived_run_id()`` 造的是**标签**，同一个名字今天
    #: 指着两个空间（``docs/B17_DESIGN_SLICES_2026-09-28.md`` 的差量 3）。两者形状本来
    #: 就不撞，所以挡住它们并排躺在同一列里的唯一位置是这一格的校验器。
    run_id: str
    workspace: str
    #: 这一轮按哪个选择器跑 —— 增量是**对着一个选择器**读的，不是对着整座池子。
    selector: str
    #: 这一份读数取在什么时候。**必填**，与 ``RunSpec`` 的 ``cutoff`` 同一条理由：
    #: 时钟从外面进来，一个缺省成「现在」的时刻会让同一份输入在两次调用里得到两份
    #: 不同的读数，而读者以为它们可比。
    at: datetime
    cells: tuple[IncrementCell, ...]

    @field_validator("run_id")
    @classmethod
    def _content_addressed(cls, value: str) -> str:
        if len(value) != _RUN_ID_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
            raise ValueError(
                f"run_id 不是 32 位小写十六进制：{value!r}。\n"
                "B17 的 run_id 是**内容寻址**的（domain.run.run_id），而这个形状的"
                "邻居是 application/pipeline.py::_derived_run_id() 造的标签"
                "（run-20260926t101530）—— 两者指着两个空间。**这里不规范化**："
                "一个需要被改写的值，与另一个值会落在同一列里，而报告上看不出来。"
            )
        return value

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("selector")
    @classmethod
    def _selector_is_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "selector 不得为空。增量是**对着一个选择器**读的 —— 一个空的选择器"
                "读起来像「整座池子」，而它真实的含义只会是「没人说清是哪一批」。"
            )
        return value

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return to_utc(value)

    @model_validator(mode="after")
    def _cells_are_a_partition(self) -> "RunIncrement":
        check_partition(self.cells)
        return self

    @property
    def works(self) -> tuple[IncrementCell, ...]:
        """这一轮**真的要动手**的那些格子 —— 「只补新/缺/过期」的那三档，按原次序。

        复用不是一件事：它是「什么都不做」。所以它不在这张表里，而 ``REUSE`` 之外
        的三档逐字对应条件 1 点名的那三件事。

        判定住在 :func:`works_of` 里（切片 g 抽出的）—— §253 的 Run preview 要报
        的是**同一个数**，两处各判一次就是这句话的第二份实现。
        """
        return works_of(self.cells)

    @property
    def counts(self) -> dict[IncrementVerdict, int]:
        """四档各自的格子数。**零也出现**，见类文档。

        同 :attr:`works`：判定住在 :func:`counts_of` 里。
        """
        return counts_of(self.cells)


def works_of(cells: Iterable[IncrementCell]) -> tuple[IncrementCell, ...]:
    """**要动手**的那几格 —— ``REUSE`` 之外的三档，按原次序。**一处判定，两处共用**。

    两处是 :attr:`RunIncrement.works`（受理**之后**那一份读数）与
    ``application/run_preview.py::IncrementReading.work``（受理**之前**那一份）——
    §253 的「预计补哪些缺口」就是这一串，两个面报的必须是同一个数。

    ``REUSE`` 不在这张表里，因为复用不是一件事：它是「什么都不做」。所以
    「要动手的」= 「不是 ``REUSE`` 的」，而这一句只在这里说一次。
    """
    return tuple(cell for cell in cells if cell.verdict is not IncrementVerdict.REUSE)


def counts_of(cells: Iterable[IncrementCell]) -> dict[IncrementVerdict, int]:
    """四档各自的格子数 —— **零也出现**。**一处判定，两处共用**，同 :func:`works_of`。

    「缺的那一档是 ``0`` 而不是缺席」这条纪律写在 :class:`RunIncrement` 的类文档里
    （与 ``EvidenceCensus.not_measured`` 同一条）：说不出来就写明白，绝不让「这一轮
    没有过期的」与「这一轮没测过期」在报告上长得一样。判定落在这一处，两个面
    （``RunIncrement.counts`` 与 ``IncrementReading.counts``）各得到同样四个键。
    """
    counted = Counter(cell.verdict for cell in cells)
    return {verdict: counted.get(verdict, 0) for verdict in IncrementVerdict}


def check_partition(cells: Iterable[IncrementCell]) -> None:
    """格子的划分判据：同一个 ``(主体, 面)`` 出现两次就抛。**一处判定，两处共用**。

    两处是 :class:`RunIncrement`（受理**之后**那一份读数）与
    ``application/run_preview.py::IncrementReading``（受理**之前**那一份）—— 两者都
    报 ``counts``，而一份有重复格子的读数会把那一格数两遍，于是「这一轮要补几格」
    在同一份读数里有两个答案。判定只有这一份：两处各自「再判一次」是这句话的第二份
    实现，而它漂移的那天，两个面报出两个不同的数，两边都自洽。

    两处**处置不同**（一处抛在 pydantic 的校验器里，一处抛在 ``__post_init__`` 里），
    但那是包装，不是判定 —— 报出来的措辞逐字相同。
    """
    keys = [(cell.subject_kind, cell.subject_id, cell.aspect) for cell in cells]
    doubled = sorted(
        (key for key, count in Counter(keys).items() if count > 1),
        key=lambda key: (key[0], key[1], key[2].value),
    )
    if doubled:
        listed = "、".join(
            f"{kind}/{subject_id}/{aspect.value}" for kind, subject_id, aspect in doubled
        )
        raise ValueError(
            f"同一格有两个结论：{listed}。\n"
            "cells 是一份**划分**，同一个 (主体, 面) 只能有一个处置 —— 两个的话，"
            "works 会把这一格数两遍，而「这一轮要补几格」会在同一份读数里有两个答案。"
        )


def plan_increment(
    *,
    run: Run,
    plan: RefreshPlan,
    observations: tuple[Observation, ...],
    evidence_store: EvidenceStore | None,
    now: datetime,
    signals: tuple[ChangeSignal, ...] = (),
    subjects: tuple[tuple[str, str], ...] = (),
) -> RunIncrement:
    """这一轮对着已沉淀的事实读一遍，回一份「哪些复用、哪些要补」。

    ``observations`` 是**已沉淀的事实**（``maintenance_store`` 上那些），
    ``subjects`` 是显式声明的主体。两者合起来就是格子空间，与 ``what_is_due``
    同一份定义 —— 本函数不自己再算一遍。

    ``evidence_store`` 为 ``None`` 的语义**逐字**来自 ``census_for``：库文件还不
    存在（``tools.read_store`` 的契约），于是 ``rows=0``、``store_present=False``。
    那不是一次「读不到」，是一次**读到了空**。两份读数都指向同一笔欠账，分开的是
    诊断面。

    Raises:
        RunStateError: 这一轮已经结束了。见模块 docstring 第六节 —— 这是条件 1
            后半句唯一挡得住的位置，而它**在存储被读到之前**发生。
        ValueError: 观测不属于这一轮的工作区。复用的范围止于工作区边界
            （``Observation.workspace`` 是授权链的第一段），读别人的观测再把它们记成
            这一轮的复用，是一次不留痕的跨工作区读。
    """
    workspace = run.spec.workspace
    if run.is_stopped:
        raise RunStateError(
            f"Run {run.run_id} 已经结束（stop_reason={run.stop_reason.value}），"
            "不再开工。\n"
            "挡在这里而不是挡在受理：同一个 idempotency key 重发时，admit 拿回的是"
            "**同一行**（幂等由主键保证），所以「重发一个已经结束的键」在受理那一步"
            "长得像一次成功 —— 而它会让第二轮真的又跑一遍：池子第二次折、成本第二次"
            "花，而报告上第一轮与第二轮逐字相同。处置是重读这一轮现在到哪一步了。"
        )

    cells = increment_cells(
        workspace=workspace,
        plan=plan,
        observations=observations,
        evidence_store=evidence_store,
        at=now,
        signals=signals,
        subjects=subjects,
    )
    return RunIncrement(
        run_id=run.run_id,
        workspace=workspace,
        selector=run.spec.selector,
        at=now,
        cells=cells,
    )


def increment_cells(
    *,
    workspace: str,
    plan: RefreshPlan,
    observations: tuple[Observation, ...],
    evidence_store: EvidenceStore | None,
    at: datetime,
    signals: tuple[ChangeSignal, ...] = (),
    subjects: tuple[tuple[str, str], ...] = (),
) -> tuple[IncrementCell, ...]:
    """格子结论 —— ``plan_increment`` 与 §B17 的 Run preview **共用的那一份**。

    两样都不在这一层，而两样都不是顺手省的：

    * **没有 ``Run``。** 上面那个函数收一个，是为了它的两件与「一轮」有关的
      事（已经结束了没有、`run_id` 与 `selector` 落进承载它的那份读数），而那
      两件事与**格子怎么判**无关。preview（``application/run_preview.py``）正
      是在**受理之前**问同一件事的，那一刻世界上还没有那一行 —— 而给它造一个
      假的 ``Run`` 是被禁的：``domain/run.py::run_id`` 逐字写着「服务端一旦能
      自己造一个键，它就能造出第二个 Run」。
    * **没有「已经结束了没有」那个前提。** 它挡的是「第二轮真的又跑一遍」，
      而那件事只在**开工**时说得通；preview 不开工。少了它不代表那一格没了：
      它是 ``plan_increment`` 的第一条，判据在 ``tests/test_run_increment.py``。

    ## 跨工作区那条守卫**搬到了这里**，而它一个字没改

    它比的是「观测属不属于这个工作区」，那句话与「这一轮是谁」无关 —— 于是它
    的**唯一**一处实现落在这一层，两个调用方各得到一次同样的保护。措辞逐字
    不变（``ValueError``，报出是哪一格、属于哪个工作区），因为它是调用方要读
    到、要据它改东西的一句结果。

    留在 ``plan_increment`` 里的那一段（结束判定）**先**发生，所以那条函数对
    外看到的次序没有变。

    Raises:
        ValueError: 观测不属于这个工作区。复用的范围止于工作区边界
            （``Observation.workspace`` 是授权链的第一段），读别人的观测再把
            它们记成这一轮的复用，是一次不留痕的跨工作区读，而报告上它看起来
            只是「复用得很充分」。
    """
    for observation in observations:
        if observation.workspace != workspace:
            raise ValueError(
                f"观测 {observation.subject_kind}/{observation.subject_id}/"
                f"{observation.aspect.value} 属于工作区 {observation.workspace!r}，"
                f"而这一次读的是 {workspace!r}。\n"
                "复用的范围止于工作区边界（Observation.workspace 是授权链的第一段）——"
                "把别的观测读成这一轮的复用，是一次不留痕的跨工作区读，而报告上它"
                "看起来只是「复用得很充分」。"
            )
    return tuple(
        _cell_of(
            state,
            workspace=workspace,
            evidence_store=evidence_store,
            at=at,
        )
        for state in aspect_states(
            plan=plan,
            observations=observations,
            signals=signals,
            now=at,
            subjects=subjects,
        )
    )


def _cell_of(
    state: AspectState,
    *,
    workspace: str,
    evidence_store: EvidenceStore | None,
    at: datetime,
) -> IncrementCell:
    """一格一个结论。**判据全在别处**：钟在 ``aspect_states`` 里，盘在 ``census_for`` 里。

    这一层只做两次查表与一次取舍（第四节：钟先判，盘点只在 ``CURRENT`` 上问）。
    """
    if state.status is AspectStatus.CURRENT:
        # 唯一一档还要问盘盘的（第四节）。其余每一档由钟说了算，读盘是多余的一步。
        return _current_cell(
            state, workspace=workspace, evidence_store=evidence_store, at=at
        )

    verdict = _VERDICT_OF_STATUS.get(state.status)
    if verdict is None:  # pragma: no cover —— 有判据钉住这张表覆盖除 CURRENT 以外的每一档
        raise ValueError(
            f"{state.subject_kind}/{state.subject_id}/{state.aspect.value}："
            f"AspectStatus 是 {state.status.value}，而 run_increment 的划分里没有它。\n"
            "新加一档而没有在这里给它一个处置，会让它静默落进「复用」—— 而「复用」"
            "是一句「有东西可复用」的话。"
        )
    if verdict is IncrementVerdict.NEW:
        return _cell(state, verdict=verdict, reason="从没看过这一面 —— 这一轮第一次去看")
    return _cell(
        state,
        verdict=verdict,
        reason=f"上一轮看过（{state.last_verified_at.isoformat()}），窗口已过 —— 这一轮重取",
    )


def _current_cell(
    state: AspectState,
    *,
    workspace: str,
    evidence_store: EvidenceStore | None,
    at: datetime,
) -> IncrementCell:
    """钟说「记着它，别动」的那一格 —— 而它可能是本片唯一要补的那种。

    **只有 ``SOURCE_COVERAGE`` 那一面问盘点**（第三节）：它的主语就是「我们手上有
    哪些来源」，数一遍它们就是在观测这一面。别的面的主语不是盘点，拿它去问是把两个
    主语混成一个 —— 而且会白读一遍盘。
    """
    if state.aspect is not Aspect.SOURCE_COVERAGE:
        return _cell(
            state,
            verdict=IncrementVerdict.REUSE,
            reason=(
                f"上一轮看过（{state.last_verified_at.isoformat()}），窗口未到 —— "
                "这一轮不动它"
            ),
        )

    census = census_for(
        store=evidence_store,
        workspace=workspace,
        subject_kind=state.subject_kind,
        subject_id=state.subject_id,
        at=at,
    )
    if census.new_source_gaps:
        where = "库在" if census.store_present else "库文件还不存在"
        return _cell(
            state,
            verdict=IncrementVerdict.MISSING,
            reason=(
                f"钟上还没到期（{state.last_verified_at.isoformat()} 看过），而盘点说"
                f"一条能当依据的来源都没有：{census.rows} 行证据、{where} —— "
                "这一轮去找一条"
            ),
        )
    return _cell(
        state,
        verdict=IncrementVerdict.REUSE,
        reason=(
            f"上一轮看过（{state.last_verified_at.isoformat()}），窗口未到，"
            f"且盘上有能当依据的来源（{census.rows} 行）—— 这一轮不动它"
        ),
    )


def _cell(
    state: AspectState, *, verdict: IncrementVerdict, reason: str
) -> IncrementCell:
    return IncrementCell(
        subject_kind=state.subject_kind,
        subject_id=state.subject_id,
        aspect=state.aspect,
        verdict=verdict,
        reason=reason,
        last_verified_at=state.last_verified_at,
        next_due_at=state.next_due_at,
        policy_version=state.policy_version,
    )


__all__ = [
    "IncrementCell",
    "IncrementVerdict",
    "RunIncrement",
    "check_partition",
    "counts_of",
    "increment_cells",
    "plan_increment",
    "works_of",
]

"""§B17 的 **Run preview**：把「编之前先看一眼」接到**一次跑**上（切片 g）。

## 权威句

执行计划 §B17 正文逐字：

    Run preview 必须先报告：已有事实复用多少、预计补哪些缺口、计划可能调用哪些
    provider、预算/stop、哪些歧义会进入 Review。Preview 本身零 provider 调用。

（``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:253``。同一件事在 §445 的启动流程里再
出现一次，逐字：「→ preview ResearchContext / EffectiveResearchPlan / 已有池子复用 /
预算 / stop」，而在 ``:102`` 那条启动用例里是「启动用例先 preview」。）

**这一句不在 B17 的七条关闭条件里**（七条是 ``:231-237`` 与 ``:262-268`` 那两组编号
句），它住在正文里 —— 这条差量登记在 ``docs/B17_G_RUN_PREVIEW_2026-09-28.md`` 第一节。
设计表的切片行把它记成「条件 1 的后半」，而条件 1 的后半句逐字是「重复提交同一
idempotency key 不重复池子或成本」：那半句由 a/c 两片（幂等主键 + 双向对账）与 d 片
（增量）承担，本片**不认领**它。

## 与 ``application/plan_preview.py`` 的分工：同一句话，**两个主语**

``plan_preview`` 做的是**计划层**：它的输入只有一份声明与一份上下文，答不出任何要读
池子的问题，所以 §253 那五项被**逐条**写进 :attr:`PlanPreview.not_reported`，每条带
一句为什么。这一层做的是 **Run 层**：它读得到三本本地存储（维护账、证据盘点、Run
记录）与（有窗口时）预算账本，于是五项里**三项**答得出来，另外两项照旧带着计划层那
两句 —— 带的方式见下面那节。

两个面**不合并**，理由不是分层好看：``plan_preview`` 是 §B16 关闭条件 1 的产物
（「同一自然语言任务稳定编译可复现 plan digest；**preview 零 provider 调用**」），
它的签名里一份存储都收不下，而那是那条判据能成立的原因。把本地存储塞进那一个签名，
会让 B16 那条「零 provider 调用」从**构造**退化成一句要人自觉的话（
``domain/research_context.py`` 的模块 docstring 写过这条论证的本体：「一个连端口都
收不到的编译器花不了钱」）。

## 零 provider 调用仍然是**构造**，只是与计划层不同款

``plan_preview`` 拿不到端口 —— 它连存储都不收。这一层**必须**读三样本地存储，少了
它们「复用了多少」答不出来，所以构造换一种写法：**签名只收存储与已经读出来的值**，
不收任何花钱的端口，并且**不读时钟**（``at`` 由外面给，与 ``admission_refusal``
同款）。判据照 ``tests/test_plan_preview.py`` 那一族的 AST 量具，白名单**明写三样**
（``ports.runs`` / ``ports.store`` / ``ports.budget`` —— 它们是本地存储），**明禁**
``ports.search`` / ``ports.acquisition`` 那一族（它们花钱）。

更强的半条：这一层的**类型注解**里不得出现任何检索/抓取端口 —— 它拿不到，所以
「不花一次检索」在这里不是自觉，是它没有可以调用的东西。

## 次序**照抄**受理面，而不是另排一遍

一份 preview 报的「预算/stop」就是 ``admit`` 那三步的读数，一步不多、一步不少：

1. **到期核验**（``admission.due_refusal``，切片 f）—— 在 ``spec_of`` 之后、
   ``store.admit`` **之前**，也就是**在** ``admission_refusal`` **之前**。所以
   :class:`StopVerdict` 的第一档是 ``NOT_DUE`` 而不是 ``REQUEST_REFUSED``。
2. **:func:`~marketops.ports.runs.admission_refusal`** —— 这一份请求本身走不走得通。
3. **重放检查**（``store.get(run_id)`` 命中就返回那一行，**不再问窗口**）。
4. **:func:`~marketops.ports.runs.window_refusal`** —— 这条链是不是已经到了一条 stop。

第 3 步与第 4 步的次序是本片最要紧的一条读数：**一次重发不会被链的阈值拦下**（那条路
上根本没人问它），所以「这条链已经到 stop 了」与「这次提交会被拒」不是同一句话。
它有一条判据钉住 —— 「链已经到 stop 之后重发仍报 ``REPLAY`` 而不是
``CHAIN_REFUSED``」。

前一步说了话 ⇒ 后面的格是**没问**（``None``），不是「问了、过了」：判读的人按字段序
读 :class:`StopReadout` 即可，而 ``__post_init__`` 把这条不变量钉住（一组蕴含式，见
那个类）。

## 五项逐项对账

==================================  =============================================
§253 里那一项                       这一层怎么处置
==================================  =============================================
已有事实复用多少                     **答得上**：``already_reused`` —— 复用那一档
                                    的格子（逐格带 ``reason``）
预计补哪些缺口                       **答得上**：``gaps`` —— ``NEW`` / ``MISSING``
                                    / ``EXPIRED`` 三档
计划可能调用哪些 provider           **原样带着计划层那一格**：``plan['sources']``
                                    报出计划开出来的每一条来源方法（逐条带族与
                                    「它能回答什么」）；「哪条方法由哪个 provider
                                    执行」仍在 ``not_reported`` 里（全仓今天没有那
                                    个映射，计划层的理由逐字带着）
预算 / stop                         **答得上**：``budget_and_stop`` —— 本轮的信封与
                                    截止、窗口那三格、以及受理面四步的读数
                                    （``verdict`` 五档 + 三条拒词）
哪些歧义会进入 Review               **原样带着**：``plan['held']`` / ``plan[
                                    'recorded']``；「把 hold 落成待办」仍在
                                    ``not_reported`` 里
==================================  =============================================

## ``not_reported`` 用**减法**，不重抄也不并集

这一层的 :attr:`RunPreview.not_reported` = ``plan_preview.NOT_REPORTED`` **减去**本层
答得出来的那三条（「已有事实复用多少」「预计补哪些缺口」「停在哪（stop 条件）」）。
留下来的那几条**逐字**是计划层那一批（引用同一份常量里的同一个对象，不重写一个字），
于是「同一句话的第二份实现」在结构上写不出来。

``HOLD`` 时它是**五条**：计划这一次没编出来，那三条就没人答得了，它们**加回来**、
``reason`` 换成本次那一句（见 ``_HOLD_REASONS``）。这正是
:class:`~marketops.application.plan_preview.NotReported` 的定义（「这一层答不上来的
一项，**以及为什么**」）。

## 为什么 ``outcome`` 是一个属性

:attr:`RunPreview.outcome` 委托 :attr:`PlanPreview.outcome`，不另设一格：这一层的
结局**就是**计划层的结局（``READY`` / ``HOLD`` 两档今天没有第三个来源），两处各写
一遍的那天，两格会在某次改动后分家，而报告上它们必须说的是同一件事。

## 这一层**不判**的几件事

* **不判某个模式「能不能用」**：三个模式的前提（``manual`` 无、``bounded_auto``
  要窗口、``watch`` 要到期）各住在 ``ports/runs.py`` 与 ``admission``，这一层只
  **读**那两条判定给的字符串，不重判。
* **不判「谁有权签一份窗口」**：那是身份/授权层（B01/B02），见 ``runs.py`` 那一节。
* **不判成本与上限**（``application/caps.py`` 那一族）、**不判上一轮记下的观测是不是
  真的**（那是 §B17 条件 7 的正面问题）。
* **不写任何一行、不派发任何东西、不碰账本**（账本在这一层只被**读**过一次，而那次
  读的结果是这一份读数的**输入**）。所以它与 ``admit_run`` 那条「受理不是执行」的
  界线同款，只是比它更靠前：这一层连受理都不是。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..domain.enums import BudgetLine, ExecutionMode
from ..domain.maintenance import ChangeSignal, Observation
from ..domain.run import AuthorizationWindow, Run, RunBudgetLine, RunSpec, run_id
from ..domain.scene import SceneVersion
from ..ports.budget import BudgetLedger
from ..ports.runs import (
    ChainProgress,
    RunStore,
    admission_refusal,
    window_refusal,
)
from ..ports.scenes import MaintenanceStore, SceneStore
from ..ports.store import EvidenceStore
from .not_reported import NotReported
from .plan_preview import (
    NOT_REPORTED,
    PlanPreview,
    PreviewOutcome,
)
from .run_admission import due_refusal, spent_of
from .run_increment import (
    IncrementCell,
    IncrementVerdict,
    check_partition,
    counts_of,
    increment_cells,
    works_of,
)


class StopVerdict(str, Enum):
    """这一次提交会落在受理面四步的哪一步。**闭集，五个成员。**

    ``str`` 混入是为了报告：``as_dict()`` 里写出来的是 ``"replay"``，不是一个读的人
    要去别处查表的数字。

    ## 次序**就是**受理面的次序，而成员是按那个次序排的

    :meth:`StopReadout.verdict` 的判读顺序与 ``admit_run`` 里那四步逐字同序，所以
    谁先说话谁就是这一档。成员按同一个次序写出来，读的人不必回去翻 ``admit_run``。

    ## 为什么 ``NOT_DUE`` 排在 ``REQUEST_REFUSED`` **前面**

    这不是优先级声明，是 ``admit_run`` 里那两步的**位置**：``_require_due`` 的调用点
    在 ``spec_of`` 之后、``store.admit`` 之前 —— 也就是在 ``admission_refusal`` 之前。
    一个把 ``NOT_DUE`` 排在后面的枚举，会让一份 watch 的 preview 对一个**必将被拒**
    的提交报 ``OPEN``：那正是本片要防的假绿（
    ``docs/B17_G_RUN_PREVIEW_2026-09-28.md`` 第三节决定 2）。
    """

    #: 这一批主体上**没有**到期/信号（``watch`` 独有的一步，也是最靠前的一步）。
    NOT_DUE = "not_due"
    #: 这一份请求今天走不通。链**没有被问**。
    REQUEST_REFUSED = "request_refused"
    #: 重发：这个键已经在盘上，拿回**那一行**；链的阈值这一步**不被问**。
    REPLAY = "replay"
    #: 这条链已经到了一条 stop —— 现在提交会被拒。
    CHAIN_REFUSED = "chain_refused"
    #: 四步都没有话说：按当下的盘，这一份请求受理得下来。
    OPEN = "open"


def _budget_lines(lines: tuple[RunBudgetLine, ...]) -> list[dict[str, Any]]:
    """一份信封在报告里的形状。**逐格都在**，包括额度为 0 的那一格（0 与缺席不同）。

    ``None`` 在这一层不出现：``RunSpec.budget`` 与 ``AuthorizationWindow.budget``
    都**不得为空**（各自的校验器），所以「没声明」在这两处不是一种状态。
    """
    return [{"line": one.line.value, "ceiling": one.ceiling} for one in lines]


@dataclass(frozen=True)
class StopReadout:
    """受理面四步的读数。**字段序就是那个次序**，一步不多、一步不少。

    ## 三格 ``str | None``，``None`` 一律读作「**没问**」

    ``due_refusal`` / ``request_refusal`` / ``chain_refusal`` 各是一步的返回值，而
    ``None`` 说的是**那一步没有走到**（前一步已经说了话，或者这一步对这份规格没有
    主语）。这一格不能写成空串：``""`` 是「问了，答案是走得通」，而它与「没问」在
    报告上必须分得开 —— 前者是一句有依据的放行，后者只是一片空白。它们是
    ``admission_refusal`` / ``window_refusal`` **原样**交出来的那句话，这一层一个字
    都不改写（那两条的分工逐字是「判据一份、两个实现共用、返回字符串而不抛异常」）。

    ``due_refusal`` 的 ``None`` 还有一层：它同时说「这个模式不问到期」。不变量钉的是
    「``due_refusal is not None`` ⟺ ``execution_mode is WATCH``」，所以那两层是同一
    件事，不是一个可以漂的约定。

    ## ``spent`` 逐科目都在，**缺席的是 0**

    ``None`` = 没读账本（今天有两条路：非自动模式，或者账本没接上）。有值时它是
    ``BudgetLine`` 的**每一个**成员各一格 —— 账本上没记过的科目在这里是 ``0``，与
    ``window_refusal`` 那两个求和口径（``spent.get(line, 0)``）逐字同一份。让缺席
    留在元组里的话，「这条线没花过」与「这一格没人填」在报告上同形。

    ## 不变量：只有直接构造才违反得成（这里不写条数 —— 它是**随下面这段文字而变**的）

    ``RunPreview`` 由 :func:`run_preview` 唯一构造，而下面这些是**同一个事实的第二
    个说法**所挡住的路（``tests/test_plan_preview.py`` 对 ``PlanPreview`` 用的是同
    一条做法，那三条也是只有直接构造才违反）。它们分两组：

    * **次序组**（前面说了话 ⇒ 后面的读数缺席）：``due_refusal`` 有值且非空 ⇒ 后面
      四格全 ``None``；``request_refusal`` 非空 ⇒ 后面三格全 ``None``；
      ``existing`` 命中 ⇒ ``chain`` / ``chain_refusal`` / ``spent`` 全 ``None``。
    * **主语组**（一句话有没有主语）：``window is None`` ⇒ ``chain`` /
      ``chain_refusal`` / ``spent`` 全 ``None``（一条没有窗口的规格不属于任何一条
      链，「累计」这句话对它没有主语）；``chain_refusal is not None`` ⇒ ``chain is
      not None``；``spent is not None`` ⇒ ``chain is not None``（逐笔求和的输入就是
      链上那几笔预留）；``chain is not None`` ⇒ ``chain.window_digest`` 与
      ``window.digest`` **相等**（一个读错链的读数会给出一个看起来完全正常的数，见
      ``chain_progress`` 那条参数为什么关键字化）。

    还有一条是**缺席要说得出来**：``window`` 在场、``chain`` 缺席、而前四步**没有
    一步说过话** ⇒ 抛。这一格是本类存在的理由之一 —— 一份「什么都没读、什么都没说」
    的读数会在报告上长成 ``OPEN``，而 ``OPEN`` 是一句「受理得下来」的话。
    """

    #: 这一轮的信封（``RunSpec.budget``）。**不得为空** —— 见 ``_budget_lines``。
    budget: tuple[RunBudgetLine, ...]
    #: 这一轮的运行截止（``RunSpec.cutoff``）。全仓唯一的**运行**截止。
    cutoff: datetime
    #: 这条链的范围。``None`` = 这份规格不属于任何一条链（``manual`` / ``watch``）。
    window: AuthorizationWindow | None
    #: 第 1 步：这一批主体上有没有到期/信号。见类文档。
    due_refusal: str | None
    #: 第 2 步：这一份请求本身走不走得通。
    request_refusal: str | None
    #: 第 3 步：这个 ``run_id`` 此刻在不在盘上（``RunStore.get`` 的 ``None`` 语义）。
    existing: Run | None
    #: 第 4 步的输入：这条链的累计花费（逐科目）。见类文档。
    spent: tuple[tuple[BudgetLine, int], ...] | None
    #: 第 4 步的输入：这条链已经走了多远。
    chain: ChainProgress | None
    #: 第 4 步：这条链是不是已经到了一条 stop。
    chain_refusal: str | None

    def __post_init__(self) -> None:
        self._earlier_steps_silence_the_rest()
        self._every_sentence_needs_a_subject()

    def _earlier_steps_silence_the_rest(self) -> None:
        """次序组：前面一步说了话 ⇒ 后面的读数**没问**（``None``），不是「问了、过了」。"""
        if self.due_refusal:
            later = (
                self.request_refusal,
                self.existing,
                self.spent,
                self.chain,
                self.chain_refusal,
            )
            if any(item is not None for item in later):
                raise ValueError(
                    "第 1 步（这一批主体上有没有到期）已经说了话，而后面几步的读数"
                    "还在：这一份读数同时主张「watch 今天受理不了」与「这条链走到哪"
                    "了」—— 后一句从来没有被问过。\n"
                    "两件事的处置相反（一个去换一批主体或改回 manual，一个去挪窗口），"
                    "而报告上它们会读成同一行。"
                )
        if self.request_refusal:
            later = (self.existing, self.spent, self.chain, self.chain_refusal)
            if any(item is not None for item in later):
                raise ValueError(
                    "第 2 步（这一份请求走不走得通）已经说了话，而重放检查与链的读数"
                    "还在。\n"
                    "「这一次请求本身不成立」与「这一次会被链的阈值拦下」是两句话，"
                    "处置也不同（改请求 / 再要一次授权）—— 前者排在前面的原因是它"
                    "在受理面上真的排在前面。"
                )
        if self.existing is not None:
            later = (self.spent, self.chain, self.chain_refusal)
            if any(item is not None for item in later):
                raise ValueError(
                    "重放检查命中了（这个键已经在盘上），而链的读数还在。\n"
                    "受理面那条路径的承诺是「拿回**同一行**」—— 它**不再问窗口**。"
                    "在这里补一份链的读数，会让一次重发在报告上看起来像一次新的"
                    "受理：一条已经走到 stop 的链上，重发同一个键仍然拿回那一行，"
                    "而这份读数会把它写成「这次提交会被拒」。"
                )

    def _every_sentence_needs_a_subject(self) -> None:
        """主语组：一句话有没有主语；以及**缺席要说得出来**。"""
        if self.window is None:
            if self.chain is not None or self.chain_refusal is not None or self.spent is not None:
                raise ValueError(
                    "这一份规格没有窗口（``manual`` / ``watch``），而链的读数还在。\n"
                    "「这条链累计走到哪了」这句话对一条没有窗口的规格**没有主语** —— "
                    "它不属于任何一条授权链（``progress_of`` 逐字：「``run.spec.window`` "
                    "为 ``None`` 的行自然落选」）。读到了一份累计，说明这一层拿的是"
                    "**另一条链**的读数。"
                )
            return
        if self.chain_refusal is not None and self.chain is None:
            raise ValueError(
                "链的拒词在，而链的读数不在：一句话说出了口，却没有它据以说的那份"
                "读数。\n"
                "``window_refusal`` 的四条都拿 ``progress`` 做判据，所以一份缺席的"
                "读数只能来自一个接错线的调用点 —— 而它此后会被读成「这条链被判过」。"
            )
        if self.spent is not None and self.chain is None:
            raise ValueError(
                "累计花费在，而链的读数不在。\n"
                "逐科目求和的那几笔预留是**从链上读来的**（``ChainProgress."
                "reservations``）—— 一份没有链的花费读数，只可能来自另一个工作区。"
            )
        if self.chain is not None and self.chain.window_digest != self.window.digest:
            raise ValueError(
                f"链上那份累计是**另一条链**的：读数的窗口摘要是 "
                f"{self.chain.window_digest}，而这一份规格的窗口摘要是 "
                f"{self.window.digest}。\n"
                "一个读错了链的读数会给出一个看起来完全正常的数（轮数、对象数都"
                "有），于是这一份读数会拿着另一条链的累计去判 —— 走得多就被凭空卡住、"
                "走得少就被凭空放行，而两个方向在报告上都看不出来。"
            )
        if self.chain is None and self.chain_refusal is None and self.spent is None:
            # 走到这里：窗口在场、前四步一步都没说话、而链一格都没读。
            if not self.due_refusal and self.request_refusal is None and self.existing is None:
                raise ValueError(
                    "窗口在场，而链的读数**不在**，也没有一步说过话。\n"
                    "这一份读数会在报告上读成 ``OPEN`` —— 而 ``OPEN`` 是一句「按当下"
                    "的盘，这一份请求受理得下来」的话。它现在没有依据：链那三个阈值"
                    "里有两个（对象数、累计预算）根本没有被读过。\n"
                    "缺席要么说得出来（前一步说了话，于是这一步没问），要么就不该"
                    "缺席 —— 这一格是本类存在的理由之一。"
                )

    @property
    def verdict(self) -> StopVerdict:
        """这一次提交落在受理面四步的哪一步。**判读顺序就是那个次序**，见类文档。"""
        if self.due_refusal:
            return StopVerdict.NOT_DUE
        if self.request_refusal:
            return StopVerdict.REQUEST_REFUSED
        if self.existing is not None:
            return StopVerdict.REPLAY
        if self.chain_refusal:
            return StopVerdict.CHAIN_REFUSED
        return StopVerdict.OPEN

    def as_dict(self) -> dict[str, Any]:
        """写进报告的形状。

        三条拒词**原样**写出来（``None`` 就是 ``None``）：它们是受理面那两条判据给的
        句子，这一层一个字都不改写。``existing`` 报的是「这个键此刻在不在盘上」的
        **那一行**，而**不**报一个「这次会不会是重发」的布尔 —— 受理回执刻意不下那个
        判断（``runs.py::_receipt`` 逐字：「这一层刻意不替调用方下这个判断」），
        这里同样不下：多出来的那个布尔会在下一次有人改受理语义时变成一处没人维护的
        口径。
        """
        return {
            "verdict": self.verdict.value,
            "this_round": {
                "budget": _budget_lines(self.budget),
                "cutoff": self.cutoff.isoformat(),
            },
            "chain": None
            if self.window is None
            else {
                "digest": self.window.digest,
                "max_objects": self.window.max_objects,
                "budget": _budget_lines(self.window.budget),
                "not_after": self.window.not_after.isoformat(),
            },
            "progress": None
            if self.chain is None
            else {
                "runs": self.chain.runs,
                "open_runs": self.chain.open_runs,
                "objects": self.chain.objects,
                "reservations": list(self.chain.reservations),
            },
            "spent": None
            if self.spent is None
            else {line.value: units for line, units in self.spent},
            "due_refusal": self.due_refusal,
            "request_refusal": self.request_refusal,
            "chain_refusal": self.chain_refusal,
            "existing": None
            if self.existing is None
            else {
                "created_at": self.existing.created_at,
                "updated_at": self.existing.updated_at,
                "is_stopped": self.existing.is_stopped,
                "stop_reason": None
                if self.existing.stop_reason is None
                else self.existing.stop_reason.value,
            },
        }


@dataclass(frozen=True)
class IncrementReading:
    """一格一格的处置 —— **受理之前**那一份读数（§253 的前两项就是它）。

    ## 它**没有** ``run_id``，而这不是省一格

    :class:`~marketops.application.run_increment.RunIncrement`（受理**之后**那一份）
    有两格这里没有：``run_id`` 与 ``selector``。后者的理由与前者同款 —— 这一份读数取
    在**受理之前**，那一刻世界上还没有那一行，而给它造一个假的 ``Run`` 是被禁的：
    ``domain/run.py::run_id`` 逐字写着「服务端一旦能自己造一个键，它就能造出第二个
    Run，而『自行扩下一批』正是从那里开始的」。

    ## 三条计数是**输入规模**，不是判定

    ``observations`` / ``signals`` / ``subjects`` 报的是喂进 :func:`increment_cells`
    的三样各多少条，一条判定也不含。它们在这里的理由与 ``get_run_increment`` 的
    ``inputs`` 逐字同款：格子空间由「观测里出现过的 ∪ 显式声明的」决定，所以一份
    **零格**的读数必须与「喂进去的是零条」一起读，否则它会像「这一轮没有事要做」。

    ## 划分判据与 ``RunIncrement`` **共用一份**

    :func:`~marketops.application.run_increment.check_partition` 在这里被调一次，而
    两处的**处置**不同（那边抛在 pydantic 的校验器里，这边抛在 ``__post_init__``
    里）—— 那是包装，不是判定：报出来的措辞逐字相同，因为重复的格子会让 :attr:`counts`
    把那一格数两遍，于是「这一轮要补几格」在同一份读数里有两个答案。
    """

    #: 每一格的处置，按 ``aspect_states`` 给出的次序。
    cells: tuple[IncrementCell, ...]
    #: 喂进去的**已沉淀事实**有几条（``maintenance.observations``）。
    observations: int
    #: 喂进去的**变更信号**有几条。
    signals: int
    #: 显式声明的主体有几个。
    subjects: int

    def __post_init__(self) -> None:
        check_partition(self.cells)
        if self.observations < 0 or self.signals < 0 or self.subjects < 0:
            raise ValueError(
                "三条计数各是一个条数，不得为负。\n"
                "一份负的输入规模会让「喂进去的是零条」与「喂进去的没数清」在报告上"
                "同形 —— 而前者的处置是去沉淀事实，后者是去查这一处读数。"
            )

    @property
    def reuse(self) -> tuple[IncrementCell, ...]:
        """**已经有东西可复用**的那几格 —— §253 第一项的正面答案。

        「复用不是一件事：它是『什么都不做』」（``RunIncrement.works`` 逐字），
        所以这一串就是「不动的那些」。判定住在
        :func:`~marketops.application.run_increment.works_of` 的另一面（同一个
        枚举、同一个 ``REUSE``），所以两个面报的复用数不可能是两个数。
        """
        return tuple(cell for cell in self.cells if cell.verdict is IncrementVerdict.REUSE)

    @property
    def work(self) -> tuple[IncrementCell, ...]:
        """**这一轮要动手**的那几格 —— §253 第二项的答案。

        判定住在 :func:`~marketops.application.run_increment.works_of` 里，
        ``RunIncrement.works`` 调的是同一份。
        """
        return works_of(self.cells)

    @property
    def counts(self) -> dict[IncrementVerdict, int]:
        """四档各自的格子数。**零也出现**，见
        :func:`~marketops.application.run_increment.counts_of`。"""
        return counts_of(self.cells)


@dataclass(frozen=True)
class RunPreview:
    """一次 Run preview 的全部产物。

    ``frozen`` 且只由 :func:`run_preview` 构造（照 ``plan_preview.PlanPreview`` 与
    ``caps.TruncationDecision``）。``__post_init__`` 挡住三件「两个字段各写一遍就会有
    一个自洽的假答案」的事：

    * **四样同时有或同时无**：计划编出来了（``plan.plan``）、有规格（``spec``）、有
      stop 读数（``stop``）、有格子读数（``increment``）。``HOLD`` 时四样全缺席 ——
      没编出计划就没有规格（``spec_of`` 的输入之一就是那份计划），没有规格就没有
      「这一次能不能受理」，也就没有「按这份计划要补哪些格子」。
    * **被答的名字出现与否 ⟺ 计划编出来了**：``not_reported`` 的名字集合**恰好**是
      常量给出的那一份（``READY`` 时是留下来的那几条，``HOLD`` 时是全部五条）。这一
      条挡的是「静默地把一条答不上来的东西滤掉」——那种报告看起来只是短了一条。
    * **``due_refusal is not None`` ⟺ 模式是 ``watch``**：一步没被问与一个模式不问
      它是同一件事（见 :class:`StopReadout`），所以这两格必须一起动 —— 只改一处的
      那天，一份 ``manual`` 的读数会带着一句「这一批主体上没有到期」。

    三条都**只有直接构造才能违反**，而 ``tests/test_run_preview.py`` 逐个构造一遍：
    一个没人能违反的守卫与一个从不响的守卫，在报告上长得一样。
    """

    #: 这一份读数取在什么时候。``admission_refusal`` 与到期核验都用它，格子读数也用
    #: 它 —— **一次调用里只有一个时刻**。
    at: datetime
    #: 计划层的读数，**整块**带在这里（见 :meth:`as_dict`）。
    plan: PlanPreview
    #: 这一次会写下的那一行规格。``HOLD`` 时为 ``None``（见类文档）。
    spec: RunSpec | None
    #: 受理面四步的读数。``HOLD`` 时为 ``None``。
    stop: StopReadout | None
    #: 一格一格的处置。``HOLD`` 时为 ``None``。
    increment: IncrementReading | None
    #: §253 里这一层**答不上来**的那几项（减法之后的）。见模块 docstring。
    not_reported: tuple[NotReported, ...]

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError(
                "at 是一个 naive 时刻，而本仓的时刻一律带时区（``domain/util.py::"
                "to_utc``）。\n"
                "一个省掉时区的时刻会被按读它那台机器的本地时区解释，而这里拿它去"
                "比的就是「截止时刻在不在将来」—— 于是「这一轮能不能受理」取决于"
                "这份读数在哪里跑。"
            )
        self._four_are_paired()
        self._the_answered_names_match_the_outcome()
        self._due_is_asked_exactly_on_watch()

    def _four_are_paired(self) -> None:
        paired = (
            self.plan.plan is not None,
            self.spec is not None,
            self.stop is not None,
            self.increment is not None,
        )
        if len(set(paired)) != 1:
            raise ValueError(
                "计划编出来了没有、有没有规格、有没有 stop 读数、有没有格子读数 —— "
                "这四样必须同时有或同时无，"
                f"现在是 plan={'有' if paired[0] else '无'}、"
                f"spec={'有' if paired[1] else '无'}、"
                f"stop={'有' if paired[2] else '无'}、"
                f"increment={'有' if paired[3] else '无'}。\n"
                "一份「计划编出来了却没有规格」的读数，会让读者以为受理那一步已经"
                "被算过；而「没有计划却有格子读数」更坏 —— 格子读数是对着**那份"
                "计划**读的，没有它就没有可以被读的格子空间。"
            )

    def _the_answered_names_match_the_outcome(self) -> None:
        expected = (
            _EXPECTED_WHEN_HOLD
            if self.plan.outcome is PreviewOutcome.HOLD
            else _EXPECTED_WHEN_READY
        )
        actual = frozenset(item.item for item in self.not_reported)
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                "``not_reported`` 的名字与这一次的结局对不上："
                f"少的是 {missing}，多的是 {extra}。\n"
                "它是 ``plan_preview.NOT_REPORTED`` 的一次**减法**（答得出来的那几条"
                "不在里面），所以这一格的名字集合是**推得出来的**：计划编出来了就是"
                f"{sorted(_EXPECTED_WHEN_READY)}，没编出来就是全部五条。\n"
                "名字对不上最可能是**静默滤掉**了一条 —— 那种报告看起来只是短了一条，"
                "而它少掉的正是「这一层答不上来」的那句交代。"
            )

    def _due_is_asked_exactly_on_watch(self) -> None:
        if self.stop is None:
            return
        if (self.stop.due_refusal is not None) != (
            self.spec is not None and self.spec.execution_mode is ExecutionMode.WATCH
        ):
            raise ValueError(
                "到期核验那一步问了没有，与这份规格的模式对不上：只有 ``watch`` 的"
                "来源是「到期/信号」，别的两个模式的来源不是它。\n"
                "两格各写一遍的那天，一份 ``manual`` 的读数会带着一句「这一批主体上"
                "没有到期」—— 而那句话对它没有主语（``manual`` 不看主体）。"
            )

    @property
    def outcome(self) -> PreviewOutcome:
        """这一次预览的结局 —— **委托**计划层那一格，不另设一处。见模块 docstring。"""
        return self.plan.outcome

    @property
    def run_id(self) -> str | None:
        """这一次会写下的那个键（``domain/run.py::run_id``，内容寻址）。``HOLD`` 时无。

        这一格是**读数**，不是判断：报告里报出它，是为了让读的人自己拿它去问
        「这个键此刻在不在盘上」—— 而那一问的答案就在 ``stop`` 里（``existing``）。
        本层不替调用方把它压成一个「这次会不会是重发」的布尔，理由见
        :meth:`StopReadout.as_dict`。
        """
        if self.spec is None:
            return None
        return run_id(self.spec.workspace, self.spec.idempotency_key)

    def as_dict(self) -> dict[str, Any]:
        """写进报告的形状 —— 也是 MCP 只读面交出去的那一份。

        ## ``plan`` 整块原样嵌进来，不重排

        §253 那五项的落点**分散在两层**（模块 docstring 的表）：复用与缺口在这一层，
        provider 在 ``plan['sources']``，歧义在 ``plan['held']`` / ``plan['recorded']``。
        把计划那一块拆开再拼一遍的后果是**同一份读数有两个形状**，而它们在某一格上
        分家的那天，读的人拿到的是哪一份取决于他读了哪个键。

        ## ``not_reported`` 在这一层与在 ``plan`` 里**不是同一份**

        顶层那一份是**这一层**答不上来的（减法之后：``READY`` 时三条、``HOLD`` 时
        五条）；``plan['not_reported']`` 是**计划层**那一份（五项全在，逐条带它自己
        的理由）。两份都报出来是有意的 —— provider 与待办那两条在两层都成立，而
        「同一句话」在这里是**同一个对象**（``READY`` 时逐字引用），不是重抄。
        """
        return {
            "at": self.at.isoformat(),
            "outcome": self.outcome.value,
            "run_id": self.run_id,
            "already_reused": self._cells_block(self._reuse_cells(), key="count"),
            "gaps": self._cells_block(self._work_cells(), key="count"),
            "counts": None
            if self.increment is None
            else {verdict.value: count for verdict, count in self.increment.counts.items()},
            "inputs": None
            if self.increment is None
            else {
                "observations": self.increment.observations,
                "signals": self.increment.signals,
                "subjects": self.increment.subjects,
            },
            "budget_and_stop": None if self.stop is None else self.stop.as_dict(),
            "plan": self.plan.as_dict(),
            "not_reported": [
                {"item": item.item, "reason": item.reason} for item in self.not_reported
            ],
            "note": _NOTE,
        }

    def _reuse_cells(self) -> tuple[IncrementCell, ...]:
        return () if self.increment is None else self.increment.reuse

    def _work_cells(self) -> tuple[IncrementCell, ...]:
        return () if self.increment is None else self.increment.work

    def _cells_block(
        self, cells: tuple[IncrementCell, ...], *, key: str
    ) -> dict[str, Any] | None:
        """一串格子 + 它的条数。``HOLD`` 时整块是 ``None``。

        ``None`` 而不是 ``{"count": 0, "cells": []}``：这两件事的处置相反 —— 一个说
        「计划这一次没编出来，所以没有格子空间可读」，一个说「读过了，这一档是零」。
        塌成一格会让 ``HOLD`` 在报告上读起来像「复用了零格」。
        """
        if self.increment is None:
            return None
        return {
            key: len(cells),
            "cells": [cell.model_dump(mode="json") for cell in cells],
        }


#: §253 里这一层**答得出来**的那几条的**逐字名字**。
#:
#: 名字取自 ``plan_preview.NOT_REPORTED``，一个字不改 —— 这一份是那一次的**减法**的
#: 过滤器，而过滤器上多一个字或少一个字，都会让一条答不上来的东西**静默地**留在
#: 或离开报告（两种都在报告上只表现为短了一条 / 多了一条）。判据钉的是
#: ``set(_HOLD_REASONS) == set(_ANSWERED_WHEN_READY)``（两条对照，见
#: ``tests/test_run_preview.py``）。
_ANSWERED_WHEN_READY = (
    "已有事实复用多少",
    "预计补哪些缺口",
    "停在哪（stop 条件）",
)

#: ``HOLD`` 时那三条各自换上的理由。**键集与上面那一份逐字相同** —— 那一条对照判据
#: 挡的是「过滤器用的名字与这里写的名字漂开」，而漂开之后多出来的那一条会被
#: ``KeyError`` 撞出来（好），少掉的那一条会静默地用计划层那句「要读池子才知道」
#: （坏：那一句在 ``HOLD`` 下是**错**的，池子没读是因为计划没编出来）。
_HOLD_REASONS: dict[str, str] = {
    "已有事实复用多少": (
        "计划这一次没编出来（HOLD），所以没有一份计划去对着池子读 —— 而「复用了"
        "多少」这句话的主语正是那份计划开出来的那批格子。\n"
        "先把要人回答的那几条答清楚（``plan['held']`` 逐条点名），再问这一项。"
    ),
    "预计补哪些缺口": (
        "计划这一次没编出来（HOLD），所以格子空间不存在 —— 「缺口 = 目标与池子的"
        "差集」里的那个**目标**就是计划。\n"
        "报一个数出来（哪怕是 0）都会让「还没编出计划」读成「这一片没有缺口」。"
    ),
    "停在哪（stop 条件）": (
        "计划这一次没编出来（HOLD），所以没有一份规格去读预算与阈值。\n"
        "条件 4 的三个阈值（对象数 / 累计预算 / 截止时间）都是**规格上**的，而规格"
        "由 ``spec_of`` 从编出来的那一份计划拼 —— 没有计划就没有这一轮的信封与"
        "截止，也就没有「停在哪」可答。"
    ),
}

#: ``READY`` 时 ``not_reported`` 应当**恰好**是这一集（那三条被滤掉）。
_EXPECTED_WHEN_READY = frozenset(
    item.item for item in NOT_REPORTED if item.item not in _ANSWERED_WHEN_READY
)

#: ``HOLD`` 时它应当**恰好**是全部五条。
_EXPECTED_WHEN_HOLD = frozenset(item.item for item in NOT_REPORTED)

#: 报告上那段话：**§253 那五项各落在哪个键上**，逐条写出来。
#:
#: 它不长，但它挡的是一种很难看出来的报告：读者看到一个写着 ``outcome: ready`` 的
#: 对象、十几个键，然后**自己猜**哪一格答的是「预算/stop」。五项里有三项的落点不在
#: 同名键上（provider 在 ``plan['sources']``、歧义在 ``plan['held']``），所以那份
#: 猜法在最好的情况下是猜对两次。
_NOTE = (
    "这是一份**读数**，不是一次受理：它没有写任何一行、没有派发任何东西、没有碰"
    "预算账本（账本只被**读**过一次，而那次读的结果是这份读数的输入）。\n"
    "§253 要求 preview 报告的五项，各落在：**已有事实复用多少** → ``already_reused``"
    "（复用那一档的格子，逐格带理由）；**预计补哪些缺口** → ``gaps``（``new`` / "
    "``missing`` / ``expired`` 三档）；**计划可能调用哪些 provider** → "
    "``plan['sources']``（逐条来源方法；「哪条方法由哪个 provider 执行」今天没有"
    "映射，仍在 ``not_reported`` 里）；**预算 / stop** → ``budget_and_stop``（本轮的"
    "信封与截止、窗口三格、以及受理面四步的读数与三条拒词）；**哪些歧义会进入 "
    "Review** → ``plan['held']``（硬的那几条，逐条点名）与 ``plan['recorded']``"
    "（记下来但不拦路的）。\n"
    "``not_reported`` 是**这一层**答不上来的那几项（``plan['not_reported']`` 是计划层"
    "那一份，五项全在）：这一份是计划层那一份减去本层答得出来的条目之后的**同一批"
    "对象**，逐字引用，不是重抄。计划没编出来时（``outcome='hold'``）它全长五条 —— "
    "复用、缺口、stop 那三条此时也没人答得了。\n"
    "``run_id`` 是这一次**会写下**的那个键（内容寻址）；它此刻在不在盘上问的是 "
    "``budget_and_stop.existing``。这一层不替调用方把它压成「这次会不会是重发」的"
    "一个布尔，理由与受理回执刻意不报 ``replayed`` 逐字相同。\n"
    "``counts`` 四档都在（缺的那一档是 0），请与 ``inputs`` 一起读：零格配零观测是"
    "「这里还没有任何沉淀下来的事实可复用」，**不是**「这一轮没有事要做」。"
)


def held_report(*, plan_report: PlanPreview, at: datetime) -> RunPreview:
    """计划**没编出来**时的那一份读数 —— §253 那五项全在 ``not_reported`` 里。

    ``run_preview`` 的那一支**调的就是这里**，所以「一份 HOLD 的读数长什么样」只有
    这一处实现。它单独是一个公开函数，理由与 ``run_preview`` 那一支不同款：这一份
    读数**一个字都不读**（没有计划就没有规格、没有「这一次能不能受理」、也没有
    「按这份计划要补哪些格子」），所以每一个存储句柄在这里都是**用不到**的 ——
    而 MCP 面（``adapters/mcp_server/runs.py``）正是在「计划编不出来」那一刻拿到
    答案的，那一刻它还没有展开 Run 记录的理由（受理面在同一格上也不展开：
    ``_preview`` 抛在 ``runs()`` 之前）。

    传一份**编出来了**的 ``plan_report`` 会被 :class:`RunPreview` 的守卫拒（那一条
    要的是「被答的名字出现与否 ⟺ 计划编出来了」，而 ``ready=False`` 给的是全五条）。
    """
    return RunPreview(
        at=at,
        plan=plan_report,
        spec=None,
        stop=None,
        increment=None,
        not_reported=_not_reported(ready=False),
    )


def run_preview(
    *,
    store: RunStore,
    scene: SceneVersion,
    plan_report: PlanPreview,
    spec: RunSpec | None,
    at: datetime,
    observations: tuple[Observation, ...] = (),
    evidence_store: EvidenceStore | None = None,
    ledger: BudgetLedger | None = None,
    signals: tuple[ChangeSignal, ...] = (),
    subjects: tuple[tuple[str, str], ...] = (),
    scene_store: SceneStore | None = None,
    maintenance: MaintenanceStore | None = None,
) -> RunPreview:
    """这一次提交会落在哪里、会复用多少、要补哪些 —— **动手之前**先看一眼。

    它报的就是 ``admit_run`` 会判的那几件事，读的是 ````admit_run`` 会读的那几本账，
    一步不多、一步不少（次序见模块 docstring）。所以这份读数**不是**对受理结果的
    预测，它是**同一个判据的另一次调用** —— 两次调用之间盘会变，而那时正确的读法是
    「受理面拿到的是更新的那一份」。

    Args:
        store: Run 记录。这里只**读**它两样：``get``（重放检查）与 ``chain_progress``
            （链走到哪了）。不写、不派发。
        scene: 盘上那一版 Scene。两处用它：``scene.scene_id`` 进到期核验，
            ``scene.refresh`` 是格子读数的那份复核计划。
        plan_report: 计划层的读数（``preview_plan`` 的产物）。**计划只在这里收一次**
            —— 不再另收一个 ``EffectiveResearchPlan``，否则「这一份预览对着哪份计划」
            会有两个答案。``HOLD`` 时它里面没有计划，这一层也就不报规格与格子。
        spec: 这一次会写下的那份规格，由 ``spec_of`` 造（``admit_run`` 内部造的是
            **同一个生产者**造的第二份，所以两份的指纹逐字相等 —— 有一条判据钉住
            这件事）。``HOLD`` 时给 ``None``。
        at: 这一次读数取在什么时候。**必填、且只有一个** —— 到期核验、截止判定与
            格子读数都用它：三个判定拿三个时刻的话，一份读数内部就会自相矛盾，而
            报告上看不出来。
        observations / signals / subjects: 格子读数的三样输入（与 ``plan_increment``
            同款）。``subjects`` 同时喂给到期核验 —— 两处问的是**同一批主体**，
            一处是「它们在盘上有多少格」，一处是「它们里有没有到期的」。
        evidence_store: 证据盘点（``application/refresh.py::census_for`` 读它）。
            ``None`` 的语义逐字来自那个函数的契约：库文件还不存在 ⇒ ``rows=0``、
            ``store_present=False``。那不是「读不到」，是**读到了空**。
        ledger: 预算账本。只有**有窗口**时被读一次（链上那几笔预留各是多少 —— 金额
            只有账本答得了，``RunStore`` 按设计读不到）。没接上时链的第四步自己会说
            （``window_refusal`` 的 fail-closed），这一层**不**把它读成「还没花过」。
        scene_store / maintenance: 到期核验要的两本（只在 ``watch`` 上用；缺了会被
            拒，不是被跳过 —— 见 :func:`due_refusal`）。

    Returns:
        一份读数。``HOLD`` 时 ``spec`` / ``stop`` / ``increment`` 三者都是 ``None``
        —— 计划没编出来，这三样都没有主语。

    Raises:
        ValueError: ``spec`` 与 ``scene`` 说的不是同一版场景（``spec.selector`` 与
            ``scene.scene_id`` 不等），或者 ``at`` 是一个 naive 时刻。两条都是调用点
            接错了线，处置是修调用点 —— 不是一次「这一次走不通」的拒绝。
        RunReconciliationError: 链上某笔预留账本读不到（:func:`spent_of` 抛）。
            它是**事故**不是拒绝，与受理面那一处逐字同款：算不出来就抛，不返回 0。
    """
    if spec is not None and spec.selector != scene.scene_id:
        raise ValueError(
            f"这一份规格是对着场景 {spec.selector!r} 写的，而这一次读数拿的是 "
            f"场景 {scene.scene_id!r} 的那一版。\n"
            "规格里的工作区与对象范围**取自**这一版场景（``spec_of`` 逐字：「不能由"
            "调用方给」），而格子读数与到期核验都读这一版 —— 两者对不上时，报告上"
            "会是一份**自洽的错读数**：它报的每一格都属于另一个场景，而没有一个键"
            "看得出来。\n"
            "处置是把两样配对（同一个 ``_plan_context`` 出来的版本），不是改这一格。"
        )

    if plan_report.outcome is PreviewOutcome.HOLD:
        return held_report(plan_report=plan_report, at=at)

    if spec is None:  # pragma: no cover - 上面那一支已经收下 HOLD，这里只有接错线
        raise ValueError(
            "计划编出来了，而规格不在：这一层要报「这一次能不能受理」，而那句话的"
            "主语就是那份规格（``domain/run.py::RunSpec``）。\n"
            "受理面是拿同一份计划现造规格的（``spec_of``），所以这一格的缺席只可能"
            "来自一个接错线的调用点 —— 而一份没有规格的读数会在报告上长成一个"
            "「什么都对」的形状。"
        )

    stop = _stop_readout(
        store=store,
        spec=spec,
        at=at,
        ledger=ledger,
        scene_store=scene_store,
        maintenance=maintenance,
        scene_id=scene.scene_id,
        subjects=subjects,
    )
    # 格子读数**照算**，哪怕这一次提交会被拒 —— 那正是它有用的时候：一份走不通的
    # 提交里，「池子里已经有什么」仍然是一句读得出来的话，而它读的是本地存储，
    # 一次 provider 调用都不花（模块 docstring 那一节）。
    increment = IncrementReading(
        cells=increment_cells(
            workspace=spec.workspace,
            plan=scene.refresh,
            observations=observations,
            evidence_store=evidence_store,
            at=at,
            signals=signals,
            subjects=subjects,
        ),
        observations=len(observations),
        signals=len(signals),
        subjects=len(subjects),
    )
    return RunPreview(
        at=at,
        plan=plan_report,
        spec=spec,
        stop=stop,
        increment=increment,
        not_reported=_not_reported(ready=True),
    )


def _stop_readout(
    *,
    store: RunStore,
    spec: RunSpec,
    at: datetime,
    ledger: BudgetLedger | None,
    scene_store: SceneStore | None,
    maintenance: MaintenanceStore | None,
    scene_id: str,
    subjects: tuple[tuple[str, str], ...],
) -> StopReadout:
    """受理面那四步，照**同一个次序**问同样那几个函数；前一步说了话就停在那里。

    ## 为什么是「停在那里」而不是「都问一遍」

    都问一遍的形态是**报告里出现一句真的、但不会发生的话**：第 1 步已经说了「这批
    主体上没有到期」，而第 4 步照样报出「这条链已经到 stop 了」—— 两句都是真的，
    而读的人会去挪窗口。受理面上后面那一步根本不会被走到，所以这里也不走
    （:class:`StopReadout` 的三条次序不变量就是这句话的判据）。

    ## 每一步都是**同一个函数**，不是「同样的判断」

    :func:`~marketops.application.run_admission.due_refusal` /
    :func:`~marketops.ports.runs.admission_refusal` /
    :func:`~marketops.ports.runs.window_refusal` 三条，与 ``admit_run`` 调的是
    **同一批函数**。接住异常或者重写一遍判断都不在这里做 —— 那正是
    ``application/plan_preview.py`` 模块 docstring 拒过的做法：两处对同一份判据的
    读法不一致，会永远只表现为「preview 说得比受理面好听」，而没有一处会红。

    ## 这份读数**不是**在写事务里读的，而那不是一处洞

    受理面必须把 ``chain_progress`` 读在写事务里（两个同时提交的自动受理里，后一个
    要看见前一个刚插进去的那一行）。这一层不下任何决定 —— 它给的是**一份读数**，
    所以两次读之间另一个进程结束了一轮，在这里只表现为一份稍旧的对象数/累计。那
    一刻正确的读法是「受理面拿到的是更新的那一份」，而**不是**「preview 会挡下
    并发」：本层一个字节都不写，也就没有可以拿来挡并发的锁。
    """
    due: str | None = None
    if spec.execution_mode is ExecutionMode.WATCH:
        due = due_refusal(
            scene_store=scene_store,
            maintenance=maintenance,
            scene_id=scene_id,
            at=at,
            subjects=subjects,
        )
    if due:
        return StopReadout(
            budget=spec.budget,
            cutoff=spec.cutoff,
            window=spec.window,
            due_refusal=due,
            request_refusal=None,
            existing=None,
            spent=None,
            chain=None,
            chain_refusal=None,
        )

    request = admission_refusal(spec, at=at)
    if request:
        return StopReadout(
            budget=spec.budget,
            cutoff=spec.cutoff,
            window=spec.window,
            due_refusal=due,
            request_refusal=request,
            existing=None,
            spent=None,
            chain=None,
            chain_refusal=None,
        )

    existing = store.get(run_id(spec.workspace, spec.idempotency_key))
    if existing is not None:
        # 命中就停在这里：受理面那条路径**不再问窗口**（``RunStore.admit`` 的次序
        # 第 2 条）。一条已经走到 stop 的链上，重发同一个键照旧拿回那一行。
        return StopReadout(
            budget=spec.budget,
            cutoff=spec.cutoff,
            window=spec.window,
            due_refusal=due,
            request_refusal=request,
            existing=existing,
            spent=None,
            chain=None,
            chain_refusal=None,
        )

    window = spec.window
    if window is None:
        return StopReadout(
            budget=spec.budget,
            cutoff=spec.cutoff,
            window=None,
            due_refusal=due,
            request_refusal=request,
            existing=None,
            spent=None,
            chain=None,
            chain_refusal=None,
        )

    chain = store.chain_progress(workspace=spec.workspace, window_digest=window.digest)
    # 账本没接上时 ``spent`` 留 ``None``：那一格不是「还没花过」，而 ``window_refusal``
    # 的第四条**就是**为它写的（读不到 ⇒ 拒）。在这里悄悄填一份全零，等于把一次
    # 部署漏接读成一条还有额度的链。
    spent = None
    if ledger is not None:
        spent = _per_line(spent_of(ledger, chain))
    return StopReadout(
        budget=spec.budget,
        cutoff=spec.cutoff,
        window=window,
        due_refusal=due,
        request_refusal=request,
        existing=None,
        spent=spent,
        chain=chain,
        chain_refusal=window_refusal(
            spec,
            progress=chain,
            # ``spent`` 缺席时**原样递 None**：那个函数的第四条就是为它写的（读不到
            # 账本 ⇒ 拒）。把它换成一个空映射，等于替它回答「这条链一分钱没花过」——
            # 一次部署漏接会因此读成一条还有满额度的链。
            spent=None if spent is None else dict(spent),
        ),
    )


def _per_line(totals: Mapping[BudgetLine, int]) -> tuple[tuple[BudgetLine, int], ...]:
    """账本读数 → 逐科目的那一份。**缺席的是 0**，不是缺席（见 :class:`StopReadout`）。

    顺序取 ``BudgetLine`` 的声明次序 —— 一个按字典序排的读法会让报告里的行序随
    科目名变，而读者在比较两份读数时会把行序当成一种信息。
    """
    return tuple((line, totals.get(line, 0)) for line in BudgetLine)


def _not_reported(*, ready: bool) -> tuple[NotReported, ...]:
    """§253 的五项里，这一层答不上来的那几项 —— **减法**，不是重抄也不是并集。

    * ``ready``：把本层答得出来的那三条**滤掉**，留下来的几条是计划层那一批里的
      **同一个对象**（逐字引用，不重写一个字）。
    * ``not ready``（``HOLD``）：五条全在，其中三条换成本次那一句（
      ``_HOLD_REASONS``），另外两条**仍是同一个对象** —— 它们在两层都成立
      （provider 没有映射、``ReviewReason`` 缺一个成员），换措辞只会造出同一句话的
      第二份实现。
    """
    if ready:
        return tuple(item for item in NOT_REPORTED if item.item not in _ANSWERED_WHEN_READY)
    return tuple(
        NotReported(item=item.item, reason=_HOLD_REASONS[item.item])
        if item.item in _HOLD_REASONS
        else item
        for item in NOT_REPORTED
    )


__all__ = [
    "IncrementReading",
    "RunPreview",
    "StopReadout",
    "StopVerdict",
    "held_report",
    "run_preview",
]

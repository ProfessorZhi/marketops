"""持续经营的调度：**选、领、跑、结**，以及「没拿到」的那一半。

## 这一层只做四件事，每一件都有一个明确的「不做」

1. **选**（:func:`due_work`）—— 调 ``domain.maintenance.what_is_due``，把库里的
   观测与信号喂进去。它自己**不发明主体**：主体只有两个来源，库里的观测与调用方
   显式声明的 ``subjects``（``what_is_due`` 的 docstring 已逐字说明）。一个会自己
   发现主体的选择器必然也会自己发明主体。
2. **领**（:func:`run_once` 里的 ``store.claim``）—— 认领即回执。拿没拿到的判据
   是存储回的那个 ``won``，**不是**时刻比较：``now`` 在这一层是参数而不是时钟，
   两个驱动传同一个 ``now`` 时「比对时刻」会给出两个 ``True``（见 ``ClaimOutcome``）。
3. **跑** —— 调用方给的 ``work`` 回调。它**不做外联、不认识 provider**：真正去
   重核一格的那些东西在别处，本层只负责「它什么时候跑、跑了记什么」。
4. **结** —— 成功 ``DONE`` + ``result_ref``；抛异常 ``FAILED`` + 原因；回调**自己
   声明**失败（``MaintenanceOutcome.failure``，B22 加上）也记 ``FAILED`` ——
   那一种有产出而没做成，理由写在那个字段上。

「跑」与「结」之间还有一格，它是本层唯一一处**写维护账**的地方：回调在
``MaintenanceOutcome`` 里声明 ``observed`` 时，这一格才记一条
:class:`~marketops.domain.maintenance.Observation`。它为什么不按「回执写了 DONE」
推、为什么不按「回调没抛异常」推，写在 ``MaintenanceOutcome.observed`` 上。

## 关于状态：一次失败不得毁掉整批

``work`` 抛异常 → 那一格记 ``FAILED``（带原因），**循环继续**。这与 §5 对批量生成的
要求是同一条：一件坏掉不应该让其余的都不做。而反过来，**一批里有一件失败就不能
报成整批成功** —— 所以 :class:`MaintenanceRun` 把 ``done`` 与 ``failed`` 分成两个
字段，而不是一个「处理了 N 件」。

## 暂停/归档只停**派发**，这是本模块唯一一处业务判断

§4：暂停或归档停止**新的**研究或外联派发，但回信、退订、投诉、退信照常受理。
维护 tick 是「派发」这一侧 —— 它要发起新的外部工作、要花钱，所以暂停的场景
**不派**。

不派的方式是**跳过并说出来**（``MaintenanceRun.not_ticking``），不是抛异常：
一个每五分钟 tick 一次的定时器对着一个被暂停的场景抛异常，会让「配置错了」
与「这个场景被暂停了」在运维眼里长得一样 —— 而前者要立刻查，后者是正常状态。
也不是返回空元组：空元组与「这一轮没有到期的格子」在报告上长得一样，而那正是
``application/choices.py`` 已经定过的纪律（算不出来就抛，不返回 0）。

## 用**生效版本**选工作，而不是最新版本

``what_is_due`` 要一个 ``plan``，plan 来自某一版 Scene。这里取 ``effective_version``：
新写了一版但没批准，不该改变在跑的这件事的节奏 —— 那是 §4 的「不得中途换规则」
在派发这一侧的落地。而 ``DueWork`` 自己带着 ``scene_version``，认领时又写进回执，
所以「这格是按哪一版规则排上的」在事后是可查的。

## ``aspects``：**认领之前**过滤，这是本模块唯一一处会和存储打架的地方

一个部署声明得出自己复核得了哪几个面（``application/refresh.py`` 的
``REFRESHABLE_ASPECTS``）。剩下那几个面的格子**不许被认领**，理由在存储层的
契约里（``ports/scenes.py`` 的 ``MaintenanceStore.claim``）：一格如果已经有
``FAILED`` 的回执，再认领一次会写**新的一行**、``attempt`` 加一。于是
「认领了但立刻失败」这个做法会让账每 tick 涨一行，而账上只显示「这格重试得很勤」
—— 真实原因（本部署没有这一面的实现）一个字都不在账上。

所以过滤发生在 ``what_is_due`` 之后、``claim`` 之前，被滤掉的格子进
:attr:`MaintenanceRun.out_of_scope`。它与 ``failed`` 是两件事：一个是「不在本
部署的范围内」（要做就得先写那条实现），一个是「跑了、挂了」（要去看为什么）。

顺带定下 ``limit`` 的作用面：上限作用在**我打算做的那些格子**上，不是作用在
「世界上到期的格子」上。反过来做的话，一个只复核得了一个面的部署，它的 20 格
上限会被其余四面的格子吃掉，于是「上限 20」实际做成了 4 件 —— 而账上只显示
「这一轮做了 4 件」。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from ..domain.errors import ConfigError
from ..domain.maintenance import (
    ALL_ASPECTS,
    Aspect,
    DueWork,
    MaintenanceClaim,
    MaintenanceState,
    Observation,
    RecheckState,
    SourceDelta,
    claim_of,
    uncovered_aspects,
    what_is_due,
)
from ..domain.scene import SceneRecord, SceneState, SceneVersion
from ..ports.scenes import MaintenanceStore, SceneStore
from .caps import TruncationDecision, apply_cap


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now 必须带时区 —— 见 domain/util.to_utc 的说明")
    return now.astimezone(timezone.utc)


@dataclass(frozen=True)
class MaintenanceOutcome:
    """一次复核做出来的东西。

    ``result_ref`` 是**指向产出的引用**，不是产出本身 —— 一格复核的产出可能是
    几十条证据，回执里放不下也不该放（回执要被逐行读完）。它的形状与
    ``Evidence.source_ref`` / ``DueWork.source_ref`` 一致。

    「做完了，什么都没变」是一个**合法**的 outcome，它的 ``result_ref`` 可以指向
    一次「复核了、结论不变」的记录。它与「抛了异常」的区别由状态字段承载，不由
    这里承载。
    """

    result_ref: str
    #: 只有 ``SOURCE_COVERAGE`` 那一面会产出它；其余面为 ``None``（= 这一面没有这个概念）。
    source_delta: SourceDelta | None = None
    #: 这一面的内容**是哪一刻真的拿到的**。``run_once`` 按它决定要不要记一条
    #: :class:`~marketops.domain.maintenance.Observation`，以及那条观测上的时刻。
    #:
    #: ## 为什么是回调自己声明，而不是 ``run_once`` 推出来
    #:
    #: 到期判定建在「上次真的看到它是什么时候」上（``Observation.observed_at``
    #: 就是 §11 的 ``last_verified_at``），所以写一条观测等于把这一格的
    #: 下一次复核推后一个周期。于是「该不该记」既不能由 ``run_once`` 按「回调没抛
    #: 异常」推（一个只读本地缓存的回调跑得好好的，而它并没有看世界），也不能由
    #: 存储层按「回执写了 DONE」推（那是**这一格做过了**，不是**这一面看过了**）。
    #: 只有回调自己说得出 —— 它就是那个知道自己的输入是从哪儿来的人。
    #:
    #: ## 为什么是**时刻**而不是一个 ``bool``
    #:
    #: 因为复用是一次判定（§11「可复用则返回引用，**不调用供应商**」），而复用到的
    #: 那一份内容是**过去某一刻**取回的。一个 ``bool`` 只能表达「记一条观测」，
    #: 而 ``run_once`` 会顺手把 ``now`` 写进那一格 —— 于是「这一面其实五天前就
    #: 看过了」被记成「刚刚看过」，复核周期凭空长了五天，而账上显示的是
    #: 「核得很及时」。时刻由回调带回来，复用的那一格才真的按内容的年纪续期。
    #:
    #: ## 缺省为什么是 ``None``，以及这里折平了什么
    #:
    #: 缺省 ``None`` 是失败关闭的那一侧：一个**说不出来**的回调不会因为「看起来
    #: 跑成了」而把复核周期推后半年。而 ``None`` 同时覆盖三件事 —— 「这一次没真的
    #: 看」（抓取不可达）、「这一面根本没有『看』这个概念」（一次纯计算的复核）、
    #: 以及「这一面的内容拿到了、但拿到的是空的一份」。三者的处置相同（都不写观测），
    #: 而观测是它今天唯一的消费者。等到有第二个消费者（比如「哪些格子从来没被真的
    #: 看过」那类报告）时这里就该分得更细 —— 这句话写在这里，
    #: 免得那一次被当成一次普通的加字段。
    observed_at: datetime | None = None
    #: 这一次复核落在 :class:`~marketops.domain.maintenance.RecheckState` 的哪一格。
    #: 与 ``observed_at`` 一样由回调声明，理由一样：只有它知道这一趟是「复用了」
    #: 还是「抓回来发现没变」。``None`` = 这一面不走复核这条路（纯计算的复核）。
    #:
    #: 它**不是** ``observed_at`` 的派生量：``REUSED`` 与 ``UNCHANGED`` 都写观测、
    #: 且都指向一份内容，区别是前者连一次抓取都没有发生。把它们合成一格会让
    #: 「这一轮到底花了多少次抓取」从账上消失 —— 那正是本片要证的第一个数。
    recheck: RecheckState | None = None
    #: 这一趟**跑完了、也留下了产出，却没有做成**的原因。空串 = 没有失败。
    #:
    #: ## 为什么这个类需要一个失败字段（而它在 B22 之前不需要）
    #:
    #: 之前只有两种结局：抛异常（``run_once`` 记 ``FAILED``）或者做成了（记
    #: ``DONE``）。B22 带来了第三种，而它不可能折进前两种：
    #:
    #: * **它不能抛。** 一次够不着的重查**有产出** —— 盘上那份复核记录写着
    #:   「这一页这次没拿到，理由是 502」，而那正是人要去读的东西。抛出去之后
    #:   连 ``result_ref`` 都没有了，账上只剩一句异常文本。
    #: * **它不能记 ``DONE``。** 这一格的真实状态是「不知道」（没看到世界的任何一面），
    #:   而 ``DONE`` 在存储层是一条**不可再认领**的回执
    #:   （``MaintenanceStore.claim`` 只在最新一行是 ``FAILED`` 时才开新尝试）——
    #:   于是这一格此后每一轮 tick 都在计划里显示「该做」，而同一时刻的 tick
    #:   显示「已经做过了」，两份报告互相矛盾，而真相是这件事从来没办成。
    #:
    #: 所以失败由回调**说出来**，而 ``run_once`` 照它结账。它与异常那条路的区别
    #: 只在**产出还在不在**：异常那一格没有 ``result_ref``，这一格有。
    failure: str = ""

    @property
    def observed(self) -> bool:
        """这一次复核真的把这一面的内容拿到手了吗。见 :attr:`observed_at`。"""
        return self.observed_at is not None

    @property
    def ok(self) -> bool:
        """这一趟有没有做成。**抛异常不在这里** —— 那种情形连对象都没有。"""
        return not self.failure.strip()

    def __post_init__(self) -> None:
        if not self.result_ref.strip():
            raise ConfigError(
                "result_ref 不得为空。一次没有产出的复核在回执里与「跑挂了」"
                "长得一样，而两者的处置不同：前者什么都不用做，后者要去查。"
            )
        if self.failure.strip():
            # 「失败不写刚验证」在这里**从一句话变成一条约束**：这一格的真实状态
            # 是「不知道」，而一条观测会把真正的复核推后一个周期。两者同时出现
            # 只可能意味着有人把失败读成了「刚核过」。
            if self.observed_at is not None:
                raise ConfigError(
                    f"failure={self.failure!r} 与 observed_at={self.observed_at!r} "
                    "同时出现。\n"
                    "失败的复核不得声明「看到了」—— 一条观测会把这一格的下次复核"
                    "推后一个周期，而这次我们什么都没看到。"
                )
            if self.recheck is not RecheckState.UNREACHABLE:
                raise ConfigError(
                    f"failure={self.failure!r} 而 recheck={self.recheck!r}。\n"
                    "跑完了却没做成的那些理由里，今天只有一种：够不着（不可达）。"
                    "别的取值意味着有一段失败没有名字。"
                )
        if self.observed_at is not None and self.observed_at.tzinfo is None:
            # 一个没有时区的时刻会被当成本地时间读，而这一格的值直接决定
            # 「下一轮什么时候再核」——差八小时在账上是一句「核得比较勤」，
            # 在业务上是把复核周期挪了一天。失败关闭。
            raise ConfigError(
                f"observed_at={self.observed_at!r} 没有时区。\n"
                "它会被写进 Observation.observed_at（§11 的 last_verified_at），"
                "于是「这一面是什么时候看的」在报告里差一个时区而看不出来。"
            )


@dataclass(frozen=True)
class MaintenanceRun:
    """一轮 tick 的完整账。

    ``done`` / ``failed`` / ``not_claimed`` 三段分开，因为它们是三件事：
    做成了、做了但挂了、**根本不是我做的**。第三段最容易被漏掉 —— 一个并发驱动
    抢走的格子若被静默丢掉，两个驱动各自的报告都会显示「这格我没做」，
    而合起来看就是「没人做过」。
    """

    at: datetime
    workspace: str
    #: 本轮**实际拿到并跑完（成功）**的格子。
    done: tuple[MaintenanceClaim, ...] = ()
    #: 跑了但失败的格子，带原因。
    failed: tuple[MaintenanceClaim, ...] = ()
    #: 没拿到的格子：别人在做，或者已经做完。**这不是失败。**
    not_claimed: tuple[MaintenanceClaim, ...] = ()
    #: 被跳过的场景与理由。理由是一个字符串而不是一个枚举：它要说的是
    #: 「paused: 2026-09-01 起因 operator_pause」这类要读的话。
    not_ticking: tuple[tuple[str, str], ...] = ()
    #: 到期但**不在本次 tick 范围内**的格子（``aspects`` 滤掉的）。带着完整对象，
    #: 而不是折成一个数：报告要回答「哪些主体、哪一面没人管」，一个数字答不了。
    #: **认领之前**滤掉的理由写在模块 docstring 里（``claim`` 会在 ``FAILED`` 上
    #: 写新行）。
    out_of_scope: tuple[DueWork, ...] = ()
    #: 各格报上来的来源增量，**逐格一条，不做合计**。见 :class:`SourceDelta`。
    source_deltas: tuple[SourceDelta, ...] = ()
    #: 本轮**真的记下**的观测，逐格一条。只有回调在 outcome 里声明了
    #: :attr:`MaintenanceOutcome.observed` 的格子才会出现在这里。
    #:
    #: 它既不是 ``done`` 的副本也不是它的计数：``done`` 是**这一格做完了**，
    #: 而这是**这一面看过了** —— 一个只重排了缓冲区的复核两者都有，一个只读本地
    #: 缓存的复核只有前者。报告把这一格列出来，是为了让「这一轮把哪几格的复核
    #: 周期推后了」在账上看得见：那件事发生在下一次到期，而不是这一轮的失败里。
    observations: tuple[Observation, ...] = ()
    #: 本轮各格报上来的复核状态，**逐格一条，不做合计**。见
    #: :attr:`MaintenanceOutcome.recheck`。只有回调声明了的格子才会出现在这里。
    rechecks: tuple[RecheckState, ...] = ()
    #: **承诺要维护、而本次 tick 的 ``aspects`` 里没有**的那些面，按 ``ALL_ASPECTS``
    #: 次序。§11 逐字：「未接的 aspect 报 coverage gap，**不能通过 out_of_scope 把
    #: 必需功能从验收分母删掉**」。
    #:
    #: 它与 ``out_of_scope`` 是两件事：那个数随库存变（这一轮有哪些格子没人管），
    #: 这个数只随部署变（有几面根本没实现）。合成一个字段的后果是「有两面从来没
    #: 实现过」每轮都被人读成一个会变的数，而它不会变 —— 它只会被忽略。
    coverage_gaps: tuple[Aspect, ...] = ()
    #: 本轮接到的到期工作数（含没拿到的），**不含** ``out_of_scope``。
    due_count: int = 0
    truncation: TruncationDecision | None = None

    @property
    def claimed_count(self) -> int:
        """**我**跑了几格。不含 ``not_claimed``。"""
        return len(self.done) + len(self.failed)

    @property
    def out_of_scope_by_aspect(self) -> tuple[tuple[str, int], ...]:
        """按 ``ALL_ASPECTS`` 的顺序列出被滤掉的面与格数，**零个的不列**。

        顺序钉在 ``ALL_ASPECTS`` 上而不是按计数排序：一份每次跑都换个次序的报告
        人眼没法比对两次运行（``domain/maintenance.py`` 对 ``RefreshPlan`` 用的是
        同一条判据）。零个的不列，是因为「这一面本部署做不了」与「这一面这一轮
        没有到期的格子」是两件事，而后者不该在「被滤掉」这张表里出现。
        """
        counted: dict[Aspect, int] = {}
        for item in self.out_of_scope:
            counted[item.aspect] = counted.get(item.aspect, 0) + 1
        return tuple((aspect.value, counted[aspect]) for aspect in ALL_ASPECTS if aspect in counted)

    @property
    def rechecks_by_state(self) -> tuple[tuple[str, int], ...]:
        """按 ``RecheckState`` 的声明次序列出各状态与格数，**零个的不列**。

        判据与 :attr:`out_of_scope_by_aspect` 同一条：次序钉死在枚举上，人眼才能
        比对两次运行；零个的不列，是因为「这一轮没有复用的格子」与「这一轮没有
        任何复核」在报告上是两件事。

        它存在是因为本片要证的第一个数就是**抓取次数**：``REUSED`` 与 ``UNCHANGED``
        都写观测、都指向一份内容，差别只在有没有真的发出去一个请求。两者在
        ``observations`` 那一格上长得一样 —— 只有这张表分得开。
        """
        counted: dict[RecheckState, int] = {}
        for state in self.rechecks:
            counted[state] = counted.get(state, 0) + 1
        return tuple(
            (state.value, counted[state]) for state in RecheckState if state in counted
        )

    @property
    def ok(self) -> bool:
        """本轮有没有失败。**空的一轮也是成功的** —— 没有到期的工作不是故障。

        ``coverage_gaps`` **不**参与这个判词：一个部署少写了两面实现是**它自己的
        事实**，不是这一轮失败。把它折进 ``ok`` 会让每一轮都是红的，于是红不再
        意味着任何事 —— 而它要在报告里以一个**数字**被读到，见那个字段。
        """
        return not self.failed


def split_by_aspect(
    items: Iterable[DueWork],
    aspects: Iterable[Aspect] | None,
) -> tuple[tuple[DueWork, ...], tuple[DueWork, ...]]:
    """把到期工作分成 ``(本次范围内的, 本次范围外的)``，**保序**。

    ``aspects`` 为 ``None`` 表示不设范围 —— 全部在范围内，第二个元组为空。
    「不设范围」与「范围是空集」是两件事，所以这里刻意不用一个空的默认值：
    后者会让一次忘记传参的调用静默地什么都不做，而账上显示为「这一轮没有到期的
    格子」。

    Raises:
        ConfigError: 传进来的不是 ``Aspect`` 成员。**失败关闭** —— 一个字符串
            ``"source_coverage"`` 在这里比不相等、于是**每一格**都被判成范围外，
            一次 tick 就会安静地什么都不做。那条路只有异常能堵住。
    """
    if aspects is None:
        return tuple(items), ()
    wanted: set[Aspect] = set()
    for aspect in aspects:
        if not isinstance(aspect, Aspect):
            raise ConfigError(
                f"aspects 里出现了 {aspect!r}（{type(aspect).__name__}），不是 Aspect 成员。\n"
                "一个不属于闭集的值在这里与任何格子都不相等，于是每一格都会被判成"
                "「范围外」，而报告上只显示「这一轮没有要做的事」。"
            )
        wanted.add(aspect)
    inside: list[DueWork] = []
    outside: list[DueWork] = []
    for item in items:
        (inside if item.aspect in wanted else outside).append(item)
    return tuple(inside), tuple(outside)


def effective_version(scene_store: SceneStore, scene_id: str) -> SceneVersion | None:
    """这个场景**生效版本**的内容；还没有生效版本（``DRAFT``）时 ``None``。

    ## 为什么是「生效版本」而不是「最新版本」

    新写了一版但没批准，不该改变在跑的这件事的节奏 —— 那是 §4 的「不得中途换规则」
    在派发这一侧的落地（模块 docstring 里那一节逐字如此）。而 ``DRAFT`` 回 ``None``
    **不是错误**：记录在、生效版本不在，是这个市场还没开张。

    ## 为什么它是一个共享的函数（B22）

    两个调用方要的是同一样东西：:func:`due_work` 拿它选工作，而复核执行器拿它的
    ``refresh`` 判「手上这一份证据还算不算新鲜」—— B22 接上外部重查之后，第二个读点
    就不再能省了（``application/refresh.py`` 的页面复核要这一版 Scene 的 ``after_days``）。
    两处各读一遍 ``get_record`` + ``get_version`` 的代价不是多两行，是**一次 tick 里
    可能用上两份不同的计划**（中间有人批准了一版），而两处各自的报告都自洽。

    Raises:
        ConfigError: 场景不存在，或者它的生效版本在库里读不到 —— 后者说明库被人动过
            （生效版本号指向一份不存在的版本）。
    """
    record = _require(scene_store, scene_id)
    if record.effective_version is None:
        if record.state is SceneState.ACTIVE:  # pragma: no cover —— ACTIVE 必然有生效版本
            raise ConfigError(f"scene={scene_id!r} 是 ACTIVE 却没有生效版本，库被改过。")
        return None
    version = scene_store.get_version(scene_id, record.effective_version)
    if version is None:
        raise ConfigError(
            f"scene={scene_id!r} 的生效版本是第 {record.effective_version} 版，"
            "而库里读不到它。"
        )
    return version


def due_work(
    *,
    scene_store: SceneStore,
    maintenance_store: MaintenanceStore,
    scene_id: str,
    now: datetime,
    subjects: Iterable[tuple[str, str]] = (),
    limit: int | None = None,
    aspects: Iterable[Aspect] | None = None,
) -> tuple[tuple[DueWork, ...], TruncationDecision | None]:
    """选出这个场景这一轮该复核的格子。

    返回 ``(工作, 截断决定)``。没截断时第二个是 ``None`` —— **不是**一个
    ``dropped=0`` 的决定：本仓的纪律是「没有发生过的决定不该有一个对象」，
    而 ``TruncationDecision`` 自带 ``cap``/``kept`` 字段，凭空造一个会让人
    以为这里设过上限。

    ``aspects`` 滤掉的那些格子**不在这里报出来** —— 这个函数只回答「要做哪些」。
    要看被滤掉的是哪些（以及为什么它们不该被认领），走 :func:`run_once` 的
    ``out_of_scope``：那一侧才拿着统一的 ``aspects`` 与完整的 ``MaintenanceRun``。

    Raises:
        ConfigError: 场景不存在，或者处于不派发新工作的状态。
    """
    now = _utc(now)
    record = _require(scene_store, scene_id)
    if record.state is not SceneState.ACTIVE:
        raise ConfigError(
            f"scene={scene_id!r} 当前是 {record.state.value}，不接受新的派发。\n"
            "维护 tick 会发起新的外部工作（要花钱），所以它落在「派发」这一侧；"
            "受理侧（回信/退信/退订）不看这个状态，那是另一条路径。"
        )
    version = effective_version(scene_store, scene_id)
    if version is None:  # pragma: no cover —— ACTIVE 必然有生效版本
        raise ConfigError(f"scene={scene_id!r} 是 ACTIVE 却没有生效版本，库被改过。")

    observations = maintenance_store.observations(workspace=record.workspace)
    signals = maintenance_store.signals(workspace=record.workspace)
    picked = what_is_due(
        scene_id=scene_id,
        scene_version=version.version,
        plan=version.refresh,
        observations=observations,
        signals=signals,
        now=now,
        subjects=tuple(subjects),
    )
    inside, _outside = split_by_aspect(picked, aspects)
    if limit is None:
        return inside, None
    kept, decision = apply_cap(inside, limit, key=lambda item: item.dedupe_key)
    return tuple(kept), decision


def run_once(
    *,
    scene_store: SceneStore,
    maintenance_store: MaintenanceStore,
    workspace: str,
    now: datetime,
    work: Callable[[DueWork], MaintenanceOutcome],
    scenes: Sequence[str] | None = None,
    subjects: Mapping[str, Iterable[tuple[str, str]]] | None = None,
    limit: int | None = None,
    aspects: Iterable[Aspect] | None = None,
) -> MaintenanceRun:
    """跑一轮：遍历该跑的场景，逐格认领、执行、结账。

    Args:
        work: **每格调一次**。它抛出的任何异常都会被记成这一格的 ``FAILED``，
            并带上异常文本；循环继续。它返回的 ``result_ref`` 进回执，而它声明的
            ``observed`` 决定要不要为这一格记一条观测（见
            :attr:`MaintenanceOutcome.observed`）。
        scenes: 要 tick 哪些场景。``None`` = 本工作区**全部**（由
            ``records(workspace=...)`` 定序）。
        subjects: ``scene_id`` → 该场景显式声明的主体。缺一个场景的键时按**空**
            处理：一个从未声明过主体的场景不会凭空产生工作，它的账是
            「这一轮 0 件」而不是「我猜它该看这些」。
        limit: **每个场景**的上限，作用在 ``aspects`` 之内那些格子上（理由见
            模块 docstring 的最后一节）。截断被记录在 ``truncation`` 里，且丢弃的
            是按 ``what_is_due`` 排好序的尾部（拖得最久的排在前面，所以砍掉的是
            最近才到期的）。``None`` = 不封顶。
        aspects: 本次 tick 做哪几个面。``None`` = 不做筛选（全部）。范围外的格子
            进 ``out_of_scope``，**不认领** —— 理由见模块 docstring。

    Returns:
        :class:`MaintenanceRun`。**它不是异常驱动的** —— 一轮里有的格子失败，
        返回值仍然返回，失败在 ``failed`` 里。
    """
    now = _utc(now)
    # ``aspects`` 在这一层要读**三次以上**（每个场景一次 ``split_by_aspect``，
    # 外加最后那一次 ``uncovered_aspects``）。一个一次性的可迭代对象会在第二个
    # 场景上变成空的，而后果不是报错 —— 是「第一个场景筛了、其余场景一个都没筛」，
    # 报告上显示为「后面那些场景的面全都做得了」。所以先在入口收成元组。
    aspects = None if aspects is None else tuple(aspects)
    if scenes is None:
        targets = tuple(record.scene_id for record in scene_store.records(workspace=workspace))
    else:
        targets = tuple(scenes)
    declared = subjects or {}

    done: list[MaintenanceClaim] = []
    failed: list[MaintenanceClaim] = []
    not_claimed: list[MaintenanceClaim] = []
    skipped: list[tuple[str, str]] = []
    outside: list[DueWork] = []
    deltas: list[SourceDelta] = []
    observations: list[Observation] = []
    rechecks: list[RecheckState] = []
    due_count = 0
    truncations: list[TruncationDecision] = []

    for scene_id in targets:
        record = _require(scene_store, scene_id)
        if record.workspace != workspace:
            raise ConfigError(
                f"scene={scene_id!r} 属于工作区 {record.workspace!r}，本次 tick 的是 "
                f"{workspace!r}。跨工作区 tick 必须先经过授权判定，而不是在这里顺手扩大范围。"
            )
        if not record.accepts_new_work:
            skipped.append((scene_id, skip_reason(record)))
            continue

        # 先要**全部**到期工作、**不封顶**，再分范围、再封顶。三步的顺序是这一段
        # 的全部内容：先封顶会让范围外的格子占掉上限（见模块 docstring），
        # 先认领再分范围会让范围外的格子每 tick 在账上写一行。
        everything, _ignored = due_work(
            scene_store=scene_store,
            maintenance_store=maintenance_store,
            scene_id=scene_id,
            now=now,
            subjects=declared.get(scene_id, ()),
            limit=None,
            aspects=None,
        )
        inside, rejected = split_by_aspect(everything, aspects)
        outside.extend(rejected)
        if limit is None:
            picked: tuple[DueWork, ...] = inside
            decision = None
        else:
            kept, decision = apply_cap(inside, limit, key=lambda item: item.dedupe_key)
            picked = tuple(kept)
        if decision is not None:
            truncations.append(decision)
        due_count += len(picked)

        for due in picked:
            receipt = claim_of(due, workspace=workspace, at=now)
            outcome = maintenance_store.claim(receipt)
            if not outcome.won:
                not_claimed.append(outcome.claim)
                continue
            try:
                produced = work(due)
            except Exception as error:  # noqa: BLE001 —— 见下
                # 捕获**一切**：``work`` 的实现会去碰网络、解析、模型。让它自己
                # 决定「哪些异常算失败」的代价是：一个没想到的异常类型会让整轮
                # 在中间断掉，而前面那些已经认领的格子留在 CLAIMED 上 —— 报告里
                # 它们显示为「有人在跑」，而那个人就是刚刚死掉的这一次。
                # 所以这里按类型捕获是**故意的错误做法**，而失败原因逐字保留。
                failed.append(
                    _settle(
                        outcome.claim,
                        MaintenanceState.FAILED,
                        at=now,
                        failure=f"{type(error).__name__}: {error}",
                    )
                )
                maintenance_store.finish(failed[-1])
                continue
            if not produced.ok:
                # **跑完了、有产出、但没做成**（B22）。结账与「抛了异常」那一支
                # 同一格 —— ``FAILED`` 是存储层唯一可再认领的状态，而这一格确实
                # 该在下一次 tick 再试（理由写在 ``MaintenanceOutcome.failure``）。
                # ``result_ref`` 照带：盘上那份记录是这一趟唯一的产出，扔了它
                # 就等于把「为什么没拿到」也一起扔掉。
                failed.append(
                    _settle(
                        outcome.claim,
                        MaintenanceState.FAILED,
                        at=now,
                        result_ref=produced.result_ref,
                        failure=produced.failure,
                    )
                )
                maintenance_store.finish(failed[-1])
                if produced.source_delta is not None:
                    deltas.append(produced.source_delta)
                if produced.recheck is not None:
                    rechecks.append(produced.recheck)
                continue
            done.append(
                _settle(
                    outcome.claim,
                    MaintenanceState.DONE,
                    at=now,
                    result_ref=produced.result_ref,
                )
            )
            maintenance_store.finish(done[-1])
            if produced.source_delta is not None:
                deltas.append(produced.source_delta)
            if produced.recheck is not None:
                rechecks.append(produced.recheck)
            # **观测在结账之后写。** 顺序是有内容的：一格记了 DONE 却没有观测，
            # 是「这一格做过了、但没人真的看过这一面」—— 那是可以有的状态
            # （``observed_at is None`` 的回调），而反过来（有观测、回执还挂在 CLAIMED
            # 上）只会出现在一次中途崩掉之后，那时这一格下一次会被重领，观测却
            # 已经在盘上，于是那一次复核的周期被推后了一个谁也说不清的时长。
            if produced.observed_at is None:
                continue
            observation = Observation(
                workspace=workspace,
                subject_kind=due.subject_kind,
                subject_id=due.subject_id,
                aspect=due.aspect,
                # **不是 ``now``**：复用到的内容可能取回于几天前，而这一格的值
                # 决定下一轮什么时候再核。写成 ``now`` 会把「这一面其实五天前就
                # 看过了」记成「刚刚看过」，于是复核周期凭空长了五天，而报告上
                # 显示的是「核得很及时」。见 :attr:`MaintenanceOutcome.observed_at`。
                observed_at=produced.observed_at,
                # 指向**产出**而不是这一次运行：没有来源的观测无法回溯，而回溯
                # 要回答的是「你当时看到的到底是什么」—— 那是盘点盘在盘上的那一份，
                # 不是「某个时刻跑过一轮」。
                source_ref=produced.result_ref,
            )
            record_observation(store=maintenance_store, observation=observation)
            observations.append(observation)

    return MaintenanceRun(
        at=now,
        workspace=workspace,
        done=tuple(done),
        failed=tuple(failed),
        not_claimed=tuple(not_claimed),
        not_ticking=tuple(skipped),
        out_of_scope=tuple(outside),
        source_deltas=tuple(deltas),
        observations=tuple(observations),
        rechecks=tuple(rechecks),
        # ``aspects is None`` = 不设范围（全部），于是没有「未接」可言。这不是
        # 一个可以顺手写成的 ``()``：见 :func:`~marketops.domain.maintenance.uncovered_aspects`
        # 与 ``MaintenanceRun.coverage_gaps``。
        coverage_gaps=() if aspects is None else uncovered_aspects(aspects),
        due_count=due_count,
        truncation=_merge_truncations(truncations),
    )


def record_observation(*, store: MaintenanceStore, observation: Observation) -> None:
    """一次**真的**看到这一面 → 一行观测。

    这个转发函数存在的理由是它要说清一句话：``what_is_due`` 的到期判定建在
    「上次真的看到它是什么时候」上，所以**任何**让复核看起来做过、却没有真的
    拿到内容的动作，都不许写这里。``domain/maintenance.py`` 的 ``Observation``
    已经逐字写了这一点，而这里再点一次是因为本层才是那个会被顺手调用的位置。

    唯一的生产调用点在 :func:`run_once` 里，而它只在回调**自己声明**了
    ``MaintenanceOutcome.observed`` 时才走到这里 —— 见那个字段的 docstring。

    **变更信号没有这样一个转发函数**，尽管 :meth:`MaintenanceStore.put_signal`
    是它的兄弟。理由不是省一个函数：观测的危险是「写了一条没发生过的」，
    而那种东西没人拦得住（它是回调的一句声明）；信号的危险是「没有理由就说变了」，
    而那一条已经由 ``ChangeSignal`` 自己的校验器挡死（空理由构造不出来）。
    转发函数在这里的作用是**载一句话**，那里没有要载的话。
    """
    store.put_observation(observation)


def _settle(
    claim: MaintenanceClaim,
    state: MaintenanceState,
    *,
    at: datetime,
    result_ref: str = "",
    failure: str = "",
) -> MaintenanceClaim:
    """把一条 CLAIMED 回执置为终态。

    用 ``model_copy`` 而不是重新构造：``claim`` 是存储层刚给我的那一行，
    这里只改状态与产出。``model_copy`` **不校验**，所以 ``at`` 已经过 ``_utc``。
    """
    return claim.model_copy(
        update={
            "state": state,
            "finished_at": at,
            "result_ref": result_ref,
            "failure": failure,
        }
    )


def _merge_truncations(decisions: Sequence[TruncationDecision]) -> TruncationDecision | None:
    """把各场景的截断合成一个。

    **一个场景都没截断时返回 ``None``**，不返回一个零值决定。有多个时，
    ``cap`` 取共同值（同一轮用的是同一个 ``limit``），其余相加。``dropped_items``
    跨场景拼起来 —— 它们本来就是不同的去重键，不会混淆。
    """
    if not decisions:
        return None
    caps = {decision.cap for decision in decisions}
    if len(caps) != 1:
        raise ConfigError(
            f"同一轮里出现了多个不同的上限 {sorted(caps, key=repr)}。"
            "每轮只有一个 limit，出现两个意味着有人在中途改了它。"
        )
    return TruncationDecision(
        kept=sum(d.kept for d in decisions),
        dropped=sum(d.dropped for d in decisions),
        cap=decisions[0].cap,
        dropped_items=tuple(item for d in decisions for item in d.dropped_items),
    )


def skip_reason(record: SceneRecord) -> str:
    """这一轮为什么跳过这个场景。

    **公开**（原来叫 ``_skip_reason``）：第二个调用点到了 ——
    ``application/continuation.py`` 的控制器也要对被暂停的场景说同一句话。抄一遍
    那句话的代价不是文案不一致，是同一秒里维护侧说「暂停」而控制器说「可以开工」。

    **判词不是在这里编的**：它逐字来自 :attr:`SceneRecord.dispatch_refusal`，
    研究派发侧问的是同一个问题（``adapters/mcp_server/tools.py`` 的
    ``_scene_gate``）。抽到 domain 之前这两处会各有一份，而两份漂移的形态不是
    文案不一致 —— 是同一秒里维护侧说「暂停」而研究侧说「可以开工」。

    唯一的差别在最后一行：维护侧落到这里时场景一定不接受新工作，而 ``ACTIVE``
    在 ``SCENE_TRANSITIONS`` 里是可能的一格（本函数也被用来解释「为什么这一轮
    它没有到期的工作」）。那时回它自己的状态名，而不是一句空话。
    """
    return record.dispatch_refusal or record.state.value


def _require(store: SceneStore, scene_id: str) -> SceneRecord:
    record = store.get_record(scene_id)
    if record is None:
        raise ConfigError(f"没有 scene_id={scene_id!r} 这个场景。")
    return record


__all__ = [
    "MaintenanceOutcome",
    "MaintenanceRun",
    "due_work",
    "effective_version",
    "run_once",
    "record_observation",
    "skip_reason",
    "split_by_aspect",
]

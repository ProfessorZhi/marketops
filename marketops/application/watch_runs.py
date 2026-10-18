"""§17 的第四格：**到期的 Watch → 一次 watch Run**。

那条链是四格：``SavedView → enable Watch`` → ``due object`` → **``one watch Run``**。
前三格已经在盘上（``application/views.py`` 的 ``put_watch`` 与 ``watch_due_requests``），
而那条扫描在 ``marketops/`` 里**零生产调用方** —— 到期的请求出来了，没有任何东西把它
送去受理。本模块就是那一步，**只有那一步**。

## 它是驱动，不是调度器

本模块不读时钟（``at`` 是参数）、不睡、不起线程、不重试，也没有「等下一次」的循环
—— ``watch_due_requests`` 自己那句逐字：「这份扫描不是一个调度器：它答的是『现在有
哪些到期了』。把它变成一个在自己进程里睡的循环，就是本片最不该做的那件事」。
**什么时候醒来是外面那个驱动的事**（``interfaces/trigger_driver.py`` 那一次醒来；
接线请求见报告）。

## 一件事实只有一处来源，而且都在盘上

* **Scene / workspace**：按 ``scene_id`` 从 ``scene_store`` 读 **生效的那一版**
  （``get_record`` → ``effective_version`` → ``get_version``）。本模块**不接**调用方
  递进来的 ``SceneVersion``、也**不接** ``workspace``。受理出来的 Run 的 ``workspace``
  因此是从盘上那一版取的 —— ``run_admission.py::spec_of`` 是 ``RunSpec`` 的唯一生产者，
  而它那一格读的正是 ``scene.workspace``。
* **扫哪个工作区**：取**生效那一版**（``SceneVersion``）的 ``workspace``，不是参数的
  —— 它与受理时递进去的那个 ``scene`` 是**同一次读盘读出来的同一个对象**，于是
  「扫哪些 Watch」与「这行 Run 属于谁」不可能分岔。记录（``SceneRecord``）上也有
  一格 ``workspace``，在自洽的库里两者是同一个串；本模块不另外读它一次，因为那是
  同一条事实的第二个出处（这一处的取舍写在报告里）。
* **幂等键**：``WatchDueRequest.idempotency_key``（推导属性，含 ``watch_id`` 与
  ``due_at``）。本模块**不生成键**：一个在这里现编的键会让「同一份到期请求重放一次」
  在盘上开出第二行 Run。
* **这一批主体**：请求上的 ``subjects``；请求**没点名**而它指着一份视图时，把视图展开
  成 ``(account, account_id)``。驱动**不做翻译，只做并集**（§2.14 ②）；
  实现取的是那张表里更窄的那一支 —— **只在「没点名 subjects」时展开**，
  给一个既点名主体又指视图的 Watch 补上视图里那些人，等于替它扩大了范围。
* **这次允许用多少**：这一条 Watch 自己声明的 ``ceilings``。**没有第二个生产者** ——
  ``admit_run`` 的 ``budget`` 那一格逐字：「由调用方给 —— 上游还没有按窗口算本轮信封
  的生产者」。
* **谁在展开视图**：``Watch.owner``（从盘上那条 Watch 读）。到期请求里没有 actor 那一
  格，而一条 Watch 的 owner 是「这一趟替谁做」在盘上的唯一答案；把 actor 做成参数，
  就是把一次权限判定交给调用方 —— 而这一步判的正是私有视图能不能被别人碰。

## 三件必须分得开的事（回执上）

:class:`WatchRunOutcome` 三档，而它们不是「成功 / 失败」两档：

* ``ADMITTED`` —— 建出了一行 Run（``run_id`` 非空）；
* ``REFUSED`` —— **试过了**，被闸门拒（这一批主体上一条到期的都没有、视图展开不出来、
  这一版 Watch 在扫描之后被人改过）；``reason`` 是那道闸门自己的话；
* ``BLOCKED`` —— **构造上执行不了**（``blocked_reason`` 非空：目标视图已经不在了），
  连试都不试，``subjects`` 恒为空。

G2' 逐字：「**单次 ``claimed=0`` 不能证明后两者**」—— 第二、三条 Watch 今天说「没有
到期的」是**该发生的**，而它与「驱动压根没看它」必须可区分。所以回执**每一份请求都有
一行**，并另带 ``servable_seen`` / ``blocked_seen`` 两个数：结算少了一行，就与「扫描
少看到一条」对不上。

## 两条不许越的线

* ``blocked_reason`` 非空的那份**不许被补上 subjects**（``domain/watch.py:199-203``
  逐字：一份执行不了的请求建不出 Run 是**构造**，不是自觉）。这一支里本模块一次
  ``admit_run`` 都不调，也一个字都不碰它的 ``subjects``。
* ``application/run_admission.py`` 是切片 F 的活文件：本模块只**调**它，一个字都不改。

## 关于「同一 Account 落在 3 个 View 里」

去重**不在这一层**，也**不需要一个新键**：它是维护账 due 格的性质
（``domain/maintenance.py::due_dedupe_key`` 的分段**不含 view、不含 watch**）。
一次 sweep 之内三条 Watch 各自建出一行 Run（``admit_run`` 的 ``_require_due`` 读的是
**观测与信号**，不是认领），把它们的账合起来的是**下一次**到期 —— 第一趟的观测落地
之后，三条全被同一道闸门拒。实测读数与它和 TASK-H §3.2 的差别写在报告里。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..domain.enums import ExecutionMode
from ..domain.errors import ConfigError, RunSpecError, StoreError
from ..domain.research_context import ResearchContext
from ..domain.research_plan import EffectiveResearchPlan
from ..domain.run import RunBudgetLine
from ..domain.scene import SceneRecord, SceneVersion
from ..domain.subject import SUBJECT_KIND_ACCOUNT
from ..domain.watch import Watch, WatchDueRequest
from ..ports.pool import AccountPoolStore
from ..ports.runs import RunStore
from ..ports.scenes import MaintenanceStore, SceneStore
from ..ports.views import SavedViewStore, WatchStore
from .run_admission import admit_run
from .views import record_watch_check, run_saved_view, watch_due_requests

__all__ = [
    "WatchRunOutcome",
    "WatchRunRow",
    "WatchRunReceipt",
    "run_due_watches",
]


class WatchRunOutcome(str, Enum):
    """这一份到期请求的最后处置。**三档，不是两档。**

    ``REFUSED`` 与 ``BLOCKED`` 分开是这一族最要紧的一格：「试过了、被闸门拒」是一个
    关于**世界**的答案（这一期已经有人刷过了 / 这一批主体上一条到期的都没有），
    而「执行不了」是一个关于**这份请求**的答案（它指着的东西没有了，没人知道）。
    合成一档之后，「该做的做完了」与「压根没跑」在回执上同形 —— 而它们的处置相反。
    """

    ADMITTED = "admitted"
    REFUSED = "refused"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WatchRunRow:
    """一份到期请求的处置。**一行对应一份请求，一份都不许少。**

    ``subjects`` 是**真的送进受理的那一批**（``BLOCKED`` 恒为空，不是「算出来是空」）：
    报告上一格空的主体集与一次没送出去的受理长得一样，而它们的处置不同。

    ``next_due_at`` 是推走之后的下一次检查时刻，``None`` = **没有推**。推不推的规则
    只有一条：**这一份请求走到了受理闸门才推**（见 :func:`run_due_watches`）——
    ``next_due_at`` 推不走的表现是这台 Watch 每被问一次就到期一次
    （``domain/watch.py:386-390`` 逐字），而「没走到闸门」的那两支（执行不了、视图
    展开不出来）**还没「看完」**，把它们的时刻推走等于把一件要处置的事静默一周。
    """

    watch_id: str
    watch_revision: int
    view_id: str | None
    outcome: WatchRunOutcome
    subjects: tuple[tuple[str, str], ...] = ()
    run_id: str | None = None
    #: 未受理的**理由**（哪一道闸门、原话）。受理成了的这一格恒为空。
    reason: str = ""
    #: 「把下一次检查推走」没做成时的那句话。**与 ``reason`` 分开**：一行已经建出 Run 的
    #: 回执里混进一句失败说明，会让「这一份没受理」与「受理了但那一格没写下去」同形。
    check_note: str = ""
    next_due_at: datetime | None = None

    def __post_init__(self) -> None:
        """两格是三档的**构造**，不是回执送达时的自觉（照 ``Run`` 那三格的写法）。

        * ``run_id`` 有值 ⟺ 受理成了 —— 一个 ``REFUSED`` 却带着 ``run_id`` 的行会
          让「被拒」与「建了一行」同时为真；
        * 非受理的行**必须**有一句理由、受理成了的**必须**没有 —— 一句空理由在报告上
          与「没有原因」同形，而三档里除了 ``ADMITTED`` 之外的每一档都恰恰是要有人去
          处置的；反过来，一行建出了 Run 却带着一句「理由」，读的人会去找那道不存在的
          闸门。
        """
        if (self.run_id is not None) is not (self.outcome is WatchRunOutcome.ADMITTED):
            raise ValueError(
                f"{self.watch_id!r} 这一行是 {self.outcome.value}，而 run_id="
                f"{self.run_id!r}。受理成了才有 run_id，也只有受理成了才有 —— "
                "两者同时错开会让「被闸门拒」与「建出了一行 Run」在一份回执里同时成立。"
            )
        if bool(self.reason) is (self.outcome is WatchRunOutcome.ADMITTED):
            raise ValueError(
                f"{self.watch_id!r} 这一行是 {self.outcome.value}，而理由是 {self.reason!r}。"
                "一份没有理由的未受理行在报告上与「什么事都没有」同形；而一行已经建出 "
                "Run 的回执里带上一句理由，读的人会去找那道不存在的闸门。"
            )
        if self.outcome is WatchRunOutcome.BLOCKED and self.subjects:
            raise ValueError(
                f"{self.watch_id!r} 这一行执行不了，却带着 {len(self.subjects)} 个主体。"
                "一份执行不了的请求建不出 Run 是构造（``domain/watch.py:199-203``）——"
                "替它补一批主体就是把「它指着的东西没有了，没人知道」变成一个看起来"
                "成功的 Run。"
            )

    @property
    def attempted(self) -> bool:
        """送到受理闸门了吗。**``BLOCKED`` 是这一族里唯一「没试」的那一档。**"""
        return self.outcome is not WatchRunOutcome.BLOCKED

    @property
    def advanced(self) -> bool:
        """下一次检查推走了吗。见 :attr:`next_due_at`。"""
        return self.next_due_at is not None


@dataclass(frozen=True)
class WatchRunReceipt:
    """一次 sweep 的完整回执：**每一份请求一行，两份计数一个不少**。

    ``servable_seen`` / ``blocked_seen`` 是**扫描自己报的数**，与 ``rows`` 并列是刻意的：
    少了它们，「有几份请求没被处理」这个问题在回执上问不出来（那时一行都没有与一份都
    没漏长得一样）。

    ``scene_id`` / ``scene_version`` / ``workspace`` 三格是**这一次用的是盘上哪一版**的
    读数，不是参数的回显 —— 一个把参数抄进回执的实现会让「受理用的那一版」与「报告说
    的那一版」分家，而分家的那天报告是自洽的。
    """

    scene_id: str
    scene_version: int
    workspace: str
    at: datetime
    rows: tuple[WatchRunRow, ...]
    servable_seen: int
    blocked_seen: int

    def _with(self, outcome: WatchRunOutcome) -> tuple[WatchRunRow, ...]:
        return tuple(row for row in self.rows if row.outcome is outcome)

    @property
    def admitted(self) -> tuple[WatchRunRow, ...]:
        return self._with(WatchRunOutcome.ADMITTED)

    @property
    def refused(self) -> tuple[WatchRunRow, ...]:
        return self._with(WatchRunOutcome.REFUSED)

    @property
    def blocked(self) -> tuple[WatchRunRow, ...]:
        return self._with(WatchRunOutcome.BLOCKED)

    @property
    def attempted(self) -> tuple[WatchRunRow, ...]:
        return tuple(row for row in self.rows if row.attempted)

    @property
    def every_request_is_answered(self) -> bool:
        """扫描看到的每一份请求都在 ``rows`` 里。**这就是「拒 ≠ 没跑」那一格。**"""
        return len(self.rows) == self.servable_seen + self.blocked_seen


def run_due_watches(
    *,
    watches: WatchStore,
    views: SavedViewStore,
    pool: AccountPoolStore,
    runs: RunStore,
    scene_store: SceneStore,
    maintenance: MaintenanceStore,
    scene_id: str,
    context: ResearchContext,
    plan: EffectiveResearchPlan,
    cutoff: datetime,
    at: datetime,
) -> WatchRunReceipt:
    """这一刻到期的每一份 Watch：能受理的受理，不能的**如实分档报出来**。

    顺序是**先读盘、再扫描、再逐份受理**，而每一步的输入都来自上一步：

    1. ``scene_store`` 里那一版 Scene（记录 → 生效版本）—— 工作区、受理用的
       ``scene=`` 都由它给；
    2. ``watch_due_requests(workspace=`` 上面那一格 ``)`` —— 一份请求都不在这一层
       重新算（`is_due` / `blocked_reason` 的判据都在域层，第二次实现在这里只会分岔）；
    3. 逐份：``BLOCKED`` 直接记账（不试），其余展开出主体之后调 ``admit_run``；
       走到闸门的那一份，无论受理成没成，都把 ``next_due_at`` 推走。

    ``REFUSED`` 的不是异常**逃出去**：受理面的拒绝（``RunSpecError``）是一条**结果**，
    它是这一族里最常见的正常读数（「这一期已经有人刷过了」），把它抛出去会让一次
    三条 Watch 的 sweep 在第一份被拒时就整批停下 —— 而剩下那两档的读数正是要看的。

    Raises:
        ConfigError: 这个 ``scene_id`` 不在盘上、还没有生效版本、或者它记录里写着的那一版
            读不出来。三种都是**整批**判不了，不是某一份请求的事：判不了就不能放行
            （与 ``_plan_context`` 逐字同一条处置）。**与之相对**，某一份请求自己的
            ``ConfigError``（展开它的视图时抛出来的那些）在这里被收成那一行的
            ``REFUSED`` 理由 —— 详见 :func:`_subjects_of`。
    """
    record = scene_store.get_record(scene_id)
    if record is None:
        raise ConfigError(
            f"scene={scene_id!r} 不在盘上。\n"
            "  到期的 Watch 要在某个场景的**复核节奏**上判，而一个不存在的场景后面既没有"
            "生效版本、也没有节奏 —— 这一趟一次都不该受理。"
        )
    version = _effective_version(scene_store, record)
    workspace = version.workspace
    sweep = watch_due_requests(store=watches, views=views, workspace=workspace, at=at)
    rows: list[WatchRunRow] = []
    for request in sweep.servable:
        rows.append(
            _admit_one(
                request=request,
                watches=watches,
                views=views,
                pool=pool,
                runs=runs,
                scene=version,
                scene_store=scene_store,
                maintenance=maintenance,
                context=context,
                plan=plan,
                cutoff=cutoff,
                at=at,
            )
        )
    for request in sweep.blocked:
        rows.append(
            WatchRunRow(
                watch_id=request.watch_id,
                watch_revision=request.watch_revision,
                view_id=request.view_id,
                outcome=WatchRunOutcome.BLOCKED,
                reason=request.blocked_reason,
            )
        )
    return WatchRunReceipt(
        scene_id=scene_id,
        scene_version=version.version,
        workspace=workspace,
        at=at,
        rows=tuple(rows),
        servable_seen=len(sweep.servable),
        blocked_seen=len(sweep.blocked),
    )


def _effective_version(scene_store: SceneStore, record: SceneRecord) -> SceneVersion:
    """这一版**正在执行**的 Scene。**两句话分开说**：没有生效版本 / 生效版本读不出来。

    ``None`` 只对「记录自己说还没有生效版本」成立 —— 那时这个市场还没开张，受理不了
    任何东西，而这是一个正常状态（一份 DRAFT 记录）。记录说**有**、而库里读不到那一版
    是另一句话：存储不自洽，正在执行的规则不见了。把它也折成 ``None`` 的后果是报告上
    说「还没开张」，而它其实开着 —— 一个要人去查存储的问题被读成一句正常状态。
    措辞与判据与 ``product_loop.py::_plan_context`` 逐字同源。
    """
    if record.effective_version is None:
        raise ConfigError(
            f"Scene {record.scene_id!r} 还没有生效版本 —— 这个市场的复核节奏还没定下来。\n"
            "  一份 DRAFT 记录受理不了任何东西，所以这一趟一份 Watch 都不该受理："
            "到期的判据要读生效那一版的复核节奏。"
        )
    version = scene_store.get_version(record.scene_id, record.effective_version)
    if version is None:
        raise ConfigError(
            f"Scene {record.scene_id!r} 的记录写着生效版本是第 {record.effective_version} "
            "版，而库里读不到那一版。\n"
            "  「还没有生效版本」与「生效版本读不出来」是两句话：前者是这个市场还没开张，"
            "后者是存储不自洽 —— 此刻正在执行的规则不见了。把它读成「还没开张」会让报告"
            f"自洽而错误。请先核对这一份 Scene 的版本历史（record 里 "
            f"latest_version={record.latest_version}，state={record.state.value}）。"
        )
    return version


def _admit_one(
    *,
    request: WatchDueRequest,
    watches: WatchStore,
    views: SavedViewStore,
    pool: AccountPoolStore,
    runs: RunStore,
    scene: SceneVersion,
    scene_store: SceneStore,
    maintenance: MaintenanceStore,
    context: ResearchContext,
    plan: EffectiveResearchPlan,
    cutoff: datetime,
    at: datetime,
) -> WatchRunRow:
    """一份到期请求的处置。**这一支是「试过了」，试的方式写在下面四步里。**

    1. 重读这条 Watch。拿的是 **owner**（展开视图时的 actor）与**当前版号**：扫描与受理
       之间有人改过它时，手上这份请求描述的是**另一版规则**，照它受理就是拿旧规则去动
       新对象 —— 拒，并把那一版号报出来（不推进 ``next_due_at``：那一版还没被看过）。
    2. 算出这一批主体（没点名就展开视图）。展开不出来（视图是别人的私有视图、或者它的
       查询今天跑不动）时**不试受理**：一次「主体算不出来」的受理请求会带着空主体集
       撞上闸门，报告上就分不出「视图跑不动」与「视图里一个人都没有」。
    3. 调 ``admit_run(execution_mode=WATCH, ...)``。**唯一那条闸门在这里**，本模块不
       另判一次「今天有没有到期的」—— 判据的第二个出处会让两道闸门分岔的那天，报告
       说的是其中一道。
    4. 推 ``next_due_at``。规则与理由见 :attr:`WatchRunRow.next_due_at`。
    """
    watch = watches.get_watch(request.watch_id)
    if watch is None:
        return WatchRunRow(
            watch_id=request.watch_id,
            watch_revision=request.watch_revision,
            view_id=request.view_id,
            outcome=WatchRunOutcome.REFUSED,
            reason=(
                f"这条 Watch 在扫描（第 {request.watch_revision} 版）之后、受理之前"
                "被删了：盘上已经没有它。\n"
                "  一份请求描述的是一版已经不存在的规则，照它受理就会拿旧规则去动对象。"
                "这不是「没什么要做的」—— 是「有人把它拿掉了，而这一份请求还举着它」。"
            ),
        )
    if watch.revision != request.watch_revision:
        return WatchRunRow(
            watch_id=request.watch_id,
            watch_revision=request.watch_revision,
            view_id=request.view_id,
            outcome=WatchRunOutcome.REFUSED,
            reason=(
                f"这条 Watch 手上这一份是第 {request.watch_revision} 版，而盘上现在是第 "
                f"{watch.revision} 版 —— 有人在我们看它的那一刻改了它。\n"
                "  照旧版受理就是拿一批它自己已经不要的主体去刷一遍；请让下一次扫描"
                "拿新的一版再来。"
            ),
        )

    subjects, refusal = _subjects_of(request=request, watch=watch, views=views, pool=pool)
    if refusal:
        return WatchRunRow(
            watch_id=request.watch_id,
            watch_revision=request.watch_revision,
            view_id=request.view_id,
            outcome=WatchRunOutcome.REFUSED,
            reason=refusal,
        )

    try:
        run = admit_run(
            runs,
            scene=scene,
            context=context,
            plan=plan,
            budget=tuple(
                RunBudgetLine(line=ceiling.line, ceiling=ceiling.units)
                for ceiling in request.ceilings
            ),
            cutoff=cutoff,
            idempotency_key=request.idempotency_key,
            at=at,
            execution_mode=ExecutionMode.WATCH,
            scene_store=scene_store,
            maintenance=maintenance,
            subjects=subjects,
        )
    except RunSpecError as exc:
        advanced, note = _advance(watches=watches, request=request, at=at)
        return WatchRunRow(
            watch_id=request.watch_id,
            watch_revision=request.watch_revision,
            view_id=request.view_id,
            outcome=WatchRunOutcome.REFUSED,
            subjects=subjects,
            reason=str(exc),
            check_note=note,
            next_due_at=advanced,
        )
    advanced, note = _advance(watches=watches, request=request, at=at)
    return WatchRunRow(
        watch_id=request.watch_id,
        watch_revision=request.watch_revision,
        view_id=request.view_id,
        outcome=WatchRunOutcome.ADMITTED,
        subjects=subjects,
        run_id=run.run_id,
        check_note=note,
        next_due_at=advanced,
    )


def _subjects_of(
    *,
    request: WatchDueRequest,
    watch: Watch,
    views: SavedViewStore,
    pool: AccountPoolStore,
) -> tuple[tuple[tuple[str, str], ...], str]:
    """这一批主体，以及答不出来时的**理由**（空串 = 答出来了）。**两个都回。**

    只回主体不回理由的那天，「视图指着一个不存在的东西」与「视图里今天一个账号都没有」
    会一起变成一批空主体 —— 而后者是正常的（下一次也许就有了），前者要人去改。

    **展开用的是这条 Watch 的 owner，不是调用方给的 actor**：私有视图只有它的所有者
    能跑，而「这一趟替谁做」在盘上的唯一答案是这一条 Watch 自己写的 owner。

    展开**抛出来的** ``ConfigError`` 在这里被读成这一份请求的**理由**，而不是让整批停
    下：它答的是这份视图的事（可见性不匹配、视图刚好被删），而一次 sweep 里还有别的
    请求，它们的读数正是要看的。让它逃出去的表现是「一条别人私有的视图让整个驱动的
    回执一行都不剩」—— 而那与「这一刻没有到期的」在报告上同形。
    """
    named = tuple(request.subjects)
    if named or request.view_id is None:
        # 点过名的主体**原样送进去**（§2.14 ②：驱动不做翻译，只在没点名那一支上展开）。
        # 既没点名也没指视图在这个域里构造不出来（``Watch`` 的 ``model_post_init``）——
        # 真出现时照送：闸门会用它自己的话把这一份拒掉，而不是在这里另造一句判词。
        return named, ""
    try:
        expanded = run_saved_view(
            views=views, pool=pool, view_id=request.view_id, actor=watch.owner
        )
    except ConfigError as exc:
        return (), str(exc)
    if expanded.refusal:
        return (), expanded.refusal
    return (
        tuple(sorted({(SUBJECT_KIND_ACCOUNT, row.account_id) for row in expanded.rows})),
        "",
    )


def _advance(
    *, watches: WatchStore, request: WatchDueRequest, at: datetime
) -> tuple[datetime | None, str]:
    """把 ``next_due_at`` 推到 ``at + check_every_days``；推不动时**照实说**。

    推走用的是 ``record_watch_check``（这一族唯一的日常写入），它的 CAS 基准是这一份
    请求上的版本号 —— 与上面那次重读同一个基准，所以「有人改了它」在两处是同一句话。

    推不动**不是**这一份请求的失败：那一行 Run 已经建出来了，把回执丢掉会让调用方
    以为没受理。所以那一句话进 ``check_note``（不是 ``reason``），而 ``next_due_at``
    留 ``None``：下一次扫描若再看到它，那是**真的**下一次到期，不是「没推走」被读成
    了到期。
    """
    try:
        write = record_watch_check(
            store=watches,
            watch_id=request.watch_id,
            expected_revision=request.watch_revision,
            at=at,
        )
    except (ConfigError, StoreError) as exc:
        return None, (
            f"\n  另外：「看完之后把下一次检查推走」这一步没做成（{exc}）—— "
            "它不影响上面这一份受理，但这一条 Watch 的下次检查时刻还停在原处。"
        )
    return write.watch.next_due_at, ""

"""工作池的**编排**：视图、监控、跟进池（B24、§5.2–§5.4）。

## 这一层做的是三件「只有它才能做」的事

* **把一份原样文档变成一份读法，再把读法跑在一批账号上。** 编译住在域里
  （``domain/saved_view.py::compile_view_query``），执行也住在域里
  （``project_accounts``）；这一层做的是**把它们接上盘上那一行**：
  读视图、读池子、把结果与「判不了的那几行」一起报出来。
* **把一条 Watch 的到期变成一份请求。** 域里那两格是纯函数
  （``Watch.due_request`` / ``Watch.after_check``），这一层提供的是**谁在哪一刻问它**，
  以及**目标视图还在不在**那个只有读得到库的人才答得上的问题。
* **把一件待办放上盘、把一件处置折进去。** 判据在域里（``require_creatable`` 与
  ``ActionItem.resolve``），这一层提供的是「读当前那一版、把它交给纯函数、把结果写回」
  这条链，以及那条链上唯一需要人来决定的事：``expected_version`` 从哪来。

## 零 provider / 零预算 / 零 Run：这一条是**构造**

``run_saved_view`` 这一族**到不了**任何检索/抓取端口，也不需要任何账本 —— 不是
「我们记得不要调用」，是这一族的 import 闭包里根本没有那两个端口
（判据见 ``tests/test_mcp_views_are_offline.py``：它算 AST 闭包，白名单精确相等）。

三件东西因此是结构性的，不是纪律性的：

* **零 provider**：签名里收的是 ``SavedViewStore`` 与 ``AccountPoolStore``，
  而 ``project_accounts`` 拿到的是一批**已经在内存里的** ``MarketAccount``。
  要花钱或者要授权的东西根本到不了这里。
* **零预算**：这一族不 import ``ports.budget``，签名里也没有账本。§5.2 逐字：
  「SavedView 只是查询，不产生外部调用、不产生预算」。
* **零 Run**：这一层不 import ``ports.runs``，也不 import ``application.run_admission``。
  跑一份视图**不是**开一轮研究，它连 ``RunSpec`` 都构造不出来。

对照 ``application/preview_launch.py`` 那一族的写法（同一个理由：一条写在文档里的
「不会发生」在第一万次修改里失效的方式是静默的）。

## 那两个「如实报」的落点

* ``ViewRun.accounts_read`` 与 ``ViewRow.account_id``：**一行结果是一行引用**，
  不是一个账号的副本。同一个 Account 出现在三份视图里，就是那一个账号被读了三次、
  行出现三次 —— 而不是三个账号（§5.2 逐字：「一个 Account 可同时出现在多个 View；
  移动/读某个 View 不复制 Account 行」）。要看出这一点，回执里必须留着 ``account_id``
  而不是把账号折成文本；三份视图一起跑时，``accounts_read`` 是三，去重之后是一。
* ``ViewRun.unrunnable`` / ``ViewRun.refusal``：跑不动的查询**点名是哪一部分**，
  并且**一行都不回**。少回的那几行与「本来就没有」在报告上同形，而这一条正是
  「换个读法就少几个客户」那类错的入口（``catalog.py`` 的 summary 逐字）。

## 判不了的行是**第三个桶**

``ViewRun.undecided`` 不是 ``rows`` 的一部分，也不是被丢掉的：它是「这些行在这一条上
**没有答案**」—— 例如 ``country is_not "GB"`` 遇上一行 ``country=None``（还没查）。
把它算成命中会让没查过的行混进结果；把它算成不命中会让它静默消失。两个方向都会让
报告说「按国家筛过了」而其实没有。理由逐字见 ``domain/saved_view.py::_verdict``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from ..domain.action_item import (
    ActionItem,
    ActionItemState,
    ActionType,
    ScopeKind,
    require_creatable,
    scope_ref,
)
from ..domain.errors import ConfigError, SavedViewConflictError, StoreError
from ..domain.saved_view import (
    AccountField,
    CompiledQuery,
    SavedView,
    UnrunnablePart,
    ViewVisibility,
    compile_view_query,
    project_accounts,
)
from ..domain.scene import SceneState
from ..domain.util import to_utc, utc_now
from ..domain.watch import (
    Watch,
    WatchDueRequest,
    WatchTargetState,
)
from ..ports.pool import AccountPoolStore
from ..ports.scenes import SceneStore
from ..ports.views import (
    ActionItemStore,
    ActionItemWrite,
    SavedViewStore,
    ViewWrite,
    WatchStore,
    WatchWrite,
)

__all__ = [
    "ViewRow",
    "ViewRun",
    "SavedViewCard",
    "WatchCard",
    "WatchSweep",
    "put_saved_view",
    "get_saved_view",
    "list_saved_views",
    "run_saved_view",
    "run_saved_views",
    "put_watch",
    "watch_card",
    "list_watches",
    "watch_due_requests",
    "record_watch_check",
    "open_action_item",
    "resolve_action_item",
    "list_action_items",
]


# ---------------------------------------------------------------------- 视图
@dataclass(frozen=True)
class ViewRow:
    """一行结果。**它是一行引用，不是一个账号。**

    ``account_id`` 是必有的那一格，而且它**不**跟着 ``columns`` 走：一个只按调用方
    点名的列渲染的实现在 ``columns=[]`` 时会回一行没有主语的文本，而那种行没法与
    池子里那一行对上 —— 「读了三次」这句话就再也验不了。

    ``cells`` 是 ``(列名, 渲染出来的值)`` 的序列而不是 dict：``ViewRow`` 要能进
    ``frozenset``、要能比较，而 dict 两样都不行（本仓对回执对象的既有做法）。
    """

    account_id: str
    cells: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ViewRun:
    """跑一份视图的回执。**一次读数，什么都没写。**

    ``refusal`` 非空时 ``rows`` 一定是空的，而且 ``refusal`` 里**点名了是哪一部分**
    跑不动 —— 两件事在同一个回执里，是因为它们必须一起读：一份「零行且没说为什么」
    的回执与「这一批就是没有匹配的」同形。
    """

    view_id: str
    view_revision: int
    workspace: str
    owner: str
    visibility: ViewVisibility
    #: **存的原样**（§5.2 的 ``query/filter/version``），与 ``compiled`` 并排回 ——
    #: 只回后者会让一处改写无处对照（``catalog.py`` 的 ``get_saved_view`` 逐字）。
    query: dict
    compiled: CompiledQuery | None
    unrunnable: tuple[UnrunnablePart, ...] = ()
    rows: tuple[ViewRow, ...] = ()
    #: 这一趟从池子里读回来的账号行数。**不是** ``len(rows)``：被筛掉的那些也读过了。
    accounts_read: int = 0
    #: 判不了的行数（第三个桶，见模块 docstring）。
    undecided: int = 0
    #: 被 ``limit`` 截掉的行数。截断必须被记录（``application/caps.py``）。
    dropped: int = 0
    refusal: str = ""

    @property
    def ok(self) -> bool:
        return not self.refusal


@dataclass(frozen=True)
class SavedViewCard:
    """``get_saved_view`` 的一行读数：原样文档与编译读法**并排**。"""

    view: SavedView
    compiled: CompiledQuery | None
    unrunnable: tuple[UnrunnablePart, ...] = ()

    @property
    def query(self) -> dict:
        return self.view.query


def _require_view(store: SavedViewStore, view_id: str) -> SavedView:
    view = store.current(view_id)
    if view is None:
        raise ConfigError(
            f"view_id={view_id!r} 在盘上没有任何一版。\n"
            "去看这个 id 是不是抄错了 —— 一份不存在的视图与一份已经删掉的视图，"
            "读法上完全一样，而它们的处置不同（一个是换 id，一个是换目标）。"
        )
    return view


def put_saved_view(
    *,
    store: SavedViewStore,
    view_id: str,
    workspace: str,
    owner: str,
    name: str,
    query: dict,
    expected_revision: int | None,
    visibility: ViewVisibility = ViewVisibility.PRIVATE,
    note: str = "",
    at: datetime | None = None,
) -> ViewWrite:
    """存一份或改一份视图。``expected_revision=None`` = **必须不存在**。

    **这一条不要预算、不要权限**（§5.2 逐字：「保存 View 不是」）。所以这个签名里
    没有账本、没有授权窗口、没有封套 —— 而这不是「恰好没有」，是这一片最重要那条
    承重点的一半：另一半在 :func:`put_watch` 上，它要。

    改一份时 ``created_at`` **原样带过去**（``SavedView.next_revision`` 逐字）：
    它记的是「这条视图是哪一刻被存下的」，而改名不是一次新的创建。

    视图的所有者那一格**改不了**：``owner`` 是这条视图的键的一半
    （``(workspace, owner, name)``），换一个 owner 就是把这条视图交给别人 ——
    那件事的入口不是这一条工具（今天也没有那条入口，如实记在报告里）。
    """
    moment = to_utc(at or utc_now())
    if expected_revision is None:
        view = SavedView(
            view_id=view_id,
            revision=1,
            workspace=workspace,
            owner=owner,
            name=name,
            visibility=visibility,
            query=query,
            created_at=moment,
            updated_at=moment,
            note=note,
        )
    else:
        current = _require_view(store, view_id)
        if current.workspace != workspace:
            raise StoreError(
                f"view_id={view_id!r} 属于工作区 {current.workspace!r}，"
                f"本次写入声称的是 {workspace!r}。"
            )
        if current.owner != owner:
            raise SavedViewConflictError(
                f"view_id={view_id!r} 的 owner 是 {current.owner!r}，本次写入声称是 "
                f"{owner!r}。\n"
                "``owner`` 是这条视图的键的一半，所以换一个 owner 不是一次普通修改 —— "
                "那是把这条视图交给别人，而那个动作有自己的处置（今天没有这条入口）。"
            )
        view = current.next_revision(at=moment).model_copy(
            update={
                "name": name,
                "visibility": visibility,
                "query": query,
                "note": note,
            }
        )
    return store.put_view(view, expected_revision=expected_revision)


def get_saved_view(*, store: SavedViewStore, view_id: str) -> SavedViewCard:
    """读一份视图：查询、版本、负责人、可见性、最后变动时刻，**并排**编译读法。

    编译在这里做（而不是让调用方拿着原样文档自己去编）：同一次读里必须两样都有，
    分两次读会让「我写的过滤」与「编译器的读法」来自两个不同的快照。
    """
    view = _require_view(store, view_id)
    outcome = compile_view_query(view.query)
    return SavedViewCard(view=view, compiled=outcome.compiled, unrunnable=outcome.unrunnable)


def list_saved_views(
    *, store: SavedViewStore, workspace: str, owner: str | None = None
) -> tuple[SavedView, ...]:
    """本工作区的视图。``owner=None`` = 不过滤（**不是**「只看我的」）。"""
    return store.views(workspace=workspace, owner=owner)


def _run_one(
    *, views: SavedViewStore, pool: AccountPoolStore, view_id: str, actor: str
) -> ViewRun:
    view = _require_view(views, view_id)
    if view.visibility is ViewVisibility.PRIVATE and view.owner != actor:
        raise ConfigError(
            f"view_id={view_id!r} 的可见性是 private，owner 是 {view.owner!r}，"
            f"而本次读取的 actor 是 {actor!r}。\n"
            "可见性存在盘上，而这**一次读取**是它唯一的执行器 —— 一个只把 private "
            "存下来而从不读它的实现，会让这个字段在报告上看起来生效过。"
        )
    outcome = compile_view_query(view.query)
    if not outcome.ok:
        # **一行都不回，而且点名是哪一部分。** 一份由「活下来的那几条」拼出来的结果
        # 会回一批看起来正常的行，而它比调用方写的那份查询小 —— 少回的行与
        # 「本来就没有」在报告上同形（``CompileOutcome`` 的 docstring 逐字）。
        return ViewRun(
            view_id=view.view_id,
            view_revision=view.revision,
            workspace=view.workspace,
            owner=view.owner,
            visibility=view.visibility,
            query=dict(view.query),
            compiled=None,
            unrunnable=outcome.unrunnable,
            refusal=(
                f"view_id={view_id!r} 的查询有 {len(outcome.unrunnable)} 处跑不动，"
                "所以这一趟一行都不回："
                + "；".join(
                    f"{part.part} —— {part.reason}" for part in outcome.unrunnable
                )
            ),
        )
    accounts = pool.list_accounts()
    rows, undecided, dropped = project_accounts(outcome.compiled, accounts)
    return ViewRun(
        view_id=view.view_id,
        view_revision=view.revision,
        workspace=view.workspace,
        owner=view.owner,
        visibility=view.visibility,
        query=dict(view.query),
        compiled=outcome.compiled,
        rows=tuple(
            ViewRow(
                account_id=account.account_id,
                cells=tuple(
                    (column.value, _render(getattr(account, _ATTRIBUTE[column])))
                    for column in outcome.compiled.columns
                ),
            )
            for account in rows
        ),
        accounts_read=len(accounts),
        undecided=undecided,
        dropped=dropped,
    )


def run_saved_view(
    *, views: SavedViewStore, pool: AccountPoolStore, view_id: str, actor: str
) -> ViewRun:
    """跑**一份**存下的查询并回行。**零 provider、零预算、零 Run。**

    见模块 docstring：三件事都是构造（这一族的 import 闭包里没有那两个端口，
    也没有账本与 Run）。这个签名是它们的第二重落点 —— 这里没有可以传进来的
    端口，所以「顺手确认一下这家厂商的官网还在不在」在这条路上写不出来。
    """
    return run_saved_views(views=views, pool=pool, view_ids=(view_id,), actor=actor)[0]


def run_saved_views(
    *,
    views: SavedViewStore,
    pool: AccountPoolStore,
    view_ids: Sequence[str],
    actor: str,
) -> tuple[ViewRun, ...]:
    """按调用方给的次序跑若干份视图，**一份一个回执**。

    ## 为什么池子要**每份视图各读一遍**

    这不是浪费，这是那句验收口径的字面意思：同一个 Account 出现在三份视图里是
    **一个 Account 读了三次**，不是三行。三份视图共用一次读会把
    ``accounts_read`` 变成 1，于是「同一个账号被读了三遍」这件事在回执上再也读不出来 ——
    而它正是要报出来的那一件事（一个「已经读过就别再读了」的优化在这里恰好把判据
    删掉）。调用方要去重，就去重 ``ViewRow.account_id``：那是它的决定，而
    「读了几遍」是这一层的读数。

    ## 一份跑不动，别的照跑

    编译失败的那一份回一份 ``refusal`` 明说的空回执（不是整批抛），因为一份查询的
    形状错与另外几份无关 —— 而整批抛会让调用方看不到哪些是好的。
    """
    return tuple(
        _run_one(views=views, pool=pool, view_id=view_id, actor=actor)
        for view_id in view_ids
    )


#: 列 → ``MarketAccount`` 的属性。域那一层的 ``ACCOUNT_ATTRIBUTE`` 是**定义**所在，
#: 这里只是同一张表在渲染时的一个别名 —— 不重写一遍（重写的那天，「按域名筛」会在一处
#: 读 ``domains``、在另一处读 ``organisation``）。
_ATTRIBUTE = {
    AccountField.COUNTRY: "country",
    AccountField.STATUS: "status",
    AccountField.ORGANISATION: "organisation",
    AccountField.DOMAIN: "domains",
    AccountField.LAST_VERIFIED_AT: "last_verified_at",
    AccountField.FIRST_SEEN_AT: "first_seen_at",
}


def _render(value: object) -> str:
    """一格值渲染成回执里的文本。**空的那些要说出来是什么空**。

    ``None`` 渲染成 ``"未确认"`` 而不是 ``""``：``MarketAccount.country`` 那一格
    docstring 逐字（``None`` = 还没查、``""`` = 查了是没有），而把两者都渲染成
    空串会让一份「按国家筛」的结果在报告上把「没查过」读成「没有」。
    """
    if value is None:
        return "未确认"
    if value == "":
        # ``""`` 是**证据**（查了，是没有），与 ``None``（还没查）不是同一句话
        # （``domain/account.py::MarketAccount.country`` 那一格逐字）。
        return "(查过：没有)"
    if isinstance(value, (list, tuple)):
        return ", ".join(sorted(str(item) for item in value))
    if isinstance(value, datetime):
        return to_utc(value).isoformat()
    return str(getattr(value, "value", value))


# ---------------------------------------------------------------------- 监控
@dataclass(frozen=True)
class WatchCard:
    """``list_watches`` 的一行。**目标视图没了要如实报成那个样子。**

    ``target_state`` 是**读出来**的（调用方拿 ``views.current(view_id)`` 的结果进来），
    不是从 ``enabled`` 推的。一条指着已删视图的 Watch 若是被渲染成「不活跃」，
    那两句完全不同的话（「有人关了它」与「它指的东西没有了」）就在报告上同形 ——
    而它们的处置分别是「去问为什么关的」与「重新存一份视图指过去」。
    """

    watch: Watch
    target_state: WatchTargetState
    state_note: str

    @property
    def enabled(self) -> bool:
        return self.watch.enabled

    @property
    def target_missing(self) -> bool:
        return self.target_state is WatchTargetState.VIEW_MISSING


@dataclass(frozen=True)
class WatchSweep:
    """一次到期扫描：**可执行的**与**执行不了的**分开。

    ``servable`` 里的每一份都能直接喂进既有的 ``admit_run(execution_mode=WATCH)``
    （它的 ``subjects`` / ``aspects`` / ``ceilings`` / ``idempotency_key`` 各是一格）。
    ``blocked`` 里的每一份**建不出 Run** —— 那是构造，不是自觉：它们的 ``subjects``
    是空的，过不了 ``admit_run`` 那条「这一批主体上必须有一个到期的格子」的判据。

    这一层**不创建任何 Run**，也**没有任何定时器**：全仓唯一的 trigger driver 是
    调度那一片的 ``schedule_definition`` / ``fire_schedule``，它们问到这里，拿到
    ``servable``，再走**既有**的受理路径。
    """

    servable: tuple[WatchDueRequest, ...] = ()
    blocked: tuple[WatchDueRequest, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.servable and not self.blocked


def put_watch(
    *,
    store: WatchStore,
    scene_store: SceneStore,
    scene_id: str,
    watch_id: str,
    workspace: str,
    owner: str,
    name: str,
    ceilings: Sequence[object],
    notify_on: Sequence[object],
    view_id: str | None = None,
    subjects: Sequence[tuple[str, str]] = (),
    aspects: Sequence[object] = (),
    check_every_days: int = 1,
    next_due_at: datetime | None = None,
    expected_revision: int | None = None,
    enabled: bool = True,
    note: str = "",
    at: datetime | None = None,
) -> WatchWrite:
    """开一条或改一条监控。**这一条要权限、要预算。**

    与 :func:`put_saved_view` 的差别就是那条承重点，而它在**签名**上：这里的两格
    ``ceilings`` 与 ``notify_on`` 都必填且非空（域那一侧判空），而上一族连一个可以
    传进来的账本都没有。§5.3 逐字：「启用 Watch 是写操作，需要权限和预算；保存 View 不是」。

    ## 暂停的工作流上开 Watch 被**拒绝**，而不是排队

    ``scene_store`` 与 ``scene_id`` 都**没有缺省值**。判据与执行它的那一处是同一个
    —— ``application/maintenance.py::due_work`` 在场景不是 ``ACTIVE`` 时就抛
    ``ConfigError``。在这里再判一次不是重复：那条判据在**派发**那一刻才说话，而
    等到那一刻，盘上已经躺着一条「用户以为开着、实际一次都不会响」的 Watch 了。
    排队比拒绝更坏，因为它让「我明明开了监控」与「它一次都不会响」同时为真
    （``catalog.py`` 的 ``put_watch`` 那一条 summary 逐字）。

    为什么不给缺省：一个可以省略 ``scene_store`` 的签名会让「没接上」悄悄变成
    「没暂停」—— 而这两件事的处置分别是「去接线」与「可以开」。同一条纪律见
    ``domain/watch.py::Watch.due_request`` 的 ``view_exists`` 那一格。

    Raises:
        ConfigError: 场景不存在，或者它今天不派发新工作。
    """
    record = _require_scene_active(scene_store, scene_id)
    moment = to_utc(at or utc_now())
    if expected_revision is None:
        watch = Watch(
            watch_id=watch_id,
            revision=1,
            workspace=workspace,
            owner=owner,
            name=name,
            view_id=view_id,
            subjects=tuple(subjects),
            aspects=tuple(aspects),
            check_every_days=check_every_days,
            ceilings=tuple(ceilings),
            notify_on=tuple(notify_on),
            enabled=enabled,
            next_due_at=next_due_at,
            created_at=moment,
            updated_at=moment,
            note=note,
        )
    else:
        current = store.get_watch(watch_id)
        if current is None:
            raise ConfigError(
                f"watch_id={watch_id!r} 在盘上没有任何一版，而本次调用说它读到的是"
                f"第 {expected_revision} 版。"
            )
        watch = current.next_revision(at=moment).model_copy(
            update={
                "name": name,
                "view_id": view_id,
                "subjects": tuple(subjects),
                "aspects": tuple(aspects),
                "check_every_days": check_every_days,
                "ceilings": tuple(ceilings),
                "notify_on": tuple(notify_on),
                "enabled": enabled,
                "next_due_at": next_due_at if next_due_at is not None else current.next_due_at,
                "note": note,
            }
        )
    if record.workspace != workspace:
        raise StoreError(
            f"scene={scene_id!r} 属于工作区 {record.workspace!r}，"
            f"而这条 Watch 声称的是 {workspace!r}。"
        )
    return store.put_watch(watch, expected_revision=expected_revision)


def watch_card(*, watch: Watch, view_exists: bool | None) -> WatchCard:
    """一条 Watch 的一行读数。**一处定义**。

    清单（:func:`list_watches`）与写入回执（``put_watch`` 那一条工具）都要这一格，而
    两处各写一遍的那天，「刚开的这一条指着一条不存在的视图」与「清单里这一条指着一条
    不存在的视图」会给出两个答案 —— 而其中只有一个会随着目标视图被删掉而变。

    ``view_exists`` 的两档含义与 ``Watch.target_state`` 逐字相同：``None`` = 这条
    Watch 没指视图（``view_id is None``，它直接点名主体），``True`` / ``False`` =
    读过了。**不把 ``None`` 折成 ``False``**：那会让「不指视图」与「指的视图不在」
    同形，而后者是一条要处置的结论。
    """
    state = (
        WatchTargetState.SUBJECTS
        if watch.view_id is None
        else watch.target_state(view_exists=view_exists)
    )
    return WatchCard(watch=watch, target_state=state, state_note=watch.state_note())


def list_watches(
    *,
    store: WatchStore,
    views: SavedViewStore,
    workspace: str,
    enabled: bool | None = None,
) -> tuple[WatchCard, ...]:
    """监控清单。**目标视图已被删掉的如实报成那个样子**（见 :class:`WatchCard`）。

    目标视图在不在**只有这里读得到**（``views.current``），所以它在这里判、在这里
    随每一行报出去。``view_id`` 是 ``None`` 的那一条（直接点名主体的）报
    ``SUBJECTS``，而它**不是**「视图在」的同义词。判据本身在 :func:`watch_card` 里
    —— 这一条只负责**每一条各读一次** ``views.current``。
    """
    return tuple(
        watch_card(
            watch=watch,
            view_exists=(
                None if watch.view_id is None else views.current(watch.view_id) is not None
            ),
        )
        for watch in store.watches(workspace=workspace, enabled=enabled)
    )


def watch_due_requests(
    *,
    store: WatchStore,
    views: SavedViewStore,
    workspace: str,
    at: datetime,
) -> WatchSweep:
    """这一刻到期的请求。**只产生请求，不创建 Run、不写任何东西。**

    到期的判据在存储那一侧（``due_watches`` 用的是扁平列与那条索引），这里做的是
    把每一条到期变成一份**可执行的请求**，并且把执行不了的那些**单独报出来**。

    这份扫描不是一个调度器：它答的是「现在有哪些到期了」。把它变成一个在自己进程里
    睡的循环，就是本片最不该做的那件事 —— 全仓唯一的 trigger driver 是调度那一片的。
    """
    servable: list[WatchDueRequest] = []
    blocked: list[WatchDueRequest] = []
    for watch in store.due_watches(workspace=workspace, at=at):
        exists = (
            None if watch.view_id is None else views.current(watch.view_id) is not None
        )
        request = watch.due_request(view_exists=exists, at=at)
        if request is None:  # pragma: no cover —— due_watches 已经筛过一遍
            continue
        (servable if request.servable else blocked).append(request)
    return WatchSweep(servable=tuple(servable), blocked=tuple(blocked))


def record_watch_check(
    *,
    store: WatchStore,
    watch_id: str,
    expected_revision: int,
    at: datetime,
) -> WatchWrite:
    """看完一次之后把 ``next_due_at`` 推走。**这是这条 Watch 唯一的日常写入。**

    CAS 的载体与 ``put_watch`` 是同一个（``(watch_id, revision)`` 的主键插入），
    而它的意义在这里最明显：一次「今天的检查看完了」与一次「有人刚改了这条规则」
    撞上时，让推时刻的那一次赢，会把那次改动静默吃掉（刚被关掉的它又亮起来、刚改的
    ``check_every_days`` 回到旧值）—— 而那一版是**人**写的。

    ``desired`` 由 ``Watch.after_check`` 算：它把 ``next_due_at`` 推到
    ``at + check_every_days``，``revision`` 留给这一层（纯函数不动版本号）。
    """
    current = store.get_watch(watch_id)
    if current is None:
        raise ConfigError(
            f"watch_id={watch_id!r} 在盘上没有任何一版，本次调用以为它在第 "
            f"{expected_revision} 版。"
        )
    if current.revision != expected_revision:
        raise StoreError(
            f"watch_id={watch_id!r} 手上的基准是第 {expected_revision} 版，"
            f"而盘上当前是第 {current.revision} 版 —— 有人先写过它了。\n"
            "**不覆盖**：一次「把下一次检查推走」撞上「有人刚改了这条规则」时，"
            "覆盖会让那次改动静默消失（比如刚被关掉的它又亮起来）。请重读再决定。"
        )
    advanced = current.after_check(at=at).model_copy(
        update={"revision": current.revision + 1}
    )
    return store.put_watch(advanced, expected_revision=expected_revision)


def _require_scene_active(scene_store: SceneStore, scene_id: str):
    """这个场景今天派发新工作吗。判据与执行它的那一处是同一个（``due_work``）。

    在这里接住 ``due_work`` 的那条 ``ConfigError`` 而不是自己再判一遍：两处各判
    一遍的那天，「能不能开 Watch」与「它会不会响」会给出两个答案 —— 而它们的差别
    只在有人真的开了之后才现形，也就是在最贵的那一刻。
    """
    record = scene_store.get_record(scene_id)
    if record is None:
        raise ConfigError(
            f"scene={scene_id!r} 不在盘上。\n"
            "一条 Watch 要绑在一个场景上才说得清「它盯的哪些方面、由谁执行」—— "
            "一个不存在的场景后面没有生效版本，也就没有要监控的 aspect。"
        )
    if record.state is not SceneState.ACTIVE:
        raise ConfigError(
            f"scene={scene_id!r} 当前是 {record.state.value}，不接受新的派发。\n"
            "所以这条 Watch 现在**开不出来**：它指向的那台机器今天不派发新工作，"
            "而开一条不会响的 Watch 会让「我明明开了监控」与「它一次都不会响」"
            "同时为真。等它恢复之后再开，或者换一个在跑的场景。"
        )
    return record


# -------------------------------------------------------------------- 跟进池
def open_action_item(
    *,
    store: ActionItemStore,
    action_id: str,
    workspace: str,
    scope_kind: ScopeKind,
    scope_id: str,
    action_type: ActionType,
    reason: str,
    owner: str | None = None,
    due_at: datetime | None = None,
    blocked_by: str = "",
    evidence_refs: Sequence[str] = (),
    note: str = "",
    at: datetime | None = None,
) -> ActionItemWrite:
    """把一件新待办放进池子。

    ``scope_ref`` 由 ``scope_ref(kind, id)`` **算出来**，不由调用方拼：它是那条
    partial unique index 的一半，而一个手拼的实现在 ``#`` 的哪一边放什么都可能 ——
    于是「同一主体上的第二条」在库上成了另一个主体，约束一次都不响。

    重复创建**不是**在这一层判的：``store.create`` 会吃到一次插入失败，并把它翻成
    ``OpenActionItemExistsError``（点名盘上那一条的 ``action_id``）。这一层不写
    「先查一下」—— 先查后插在两个写者之间有一个窗口，而判据住在应用代码里时，
    另一条路径、另一个进程、一次手写的 SQL 都挡不住（``ports/views.py`` 逐字）。
    """
    item = ActionItem.opened(
        action_id=action_id,
        workspace=workspace,
        scope_ref_value=scope_ref(scope_kind, scope_id),
        action_type=action_type,
        reason=reason,
        owner=owner,
        due_at=due_at,
        blocked_by=blocked_by,
        evidence_refs=tuple(evidence_refs),
        at=at,
        note=note,
    )
    require_creatable(item)
    return store.create(item)


def resolve_action_item(
    *,
    store: ActionItemStore,
    action_id: str,
    expected_version: int,
    state: ActionItemState,
    by: str,
    resolution: str = "",
    owner: str | None = None,
    blocked_by: str = "",
    due_at: datetime | None = None,
    evidence_refs: Sequence[str] | None = None,
    at: datetime | None = None,
) -> ActionItemWrite:
    """把一件待办收成 done/dismissed、或改派、或挂起。

    ``expected_version`` **没有缺省值**（``ActionItem.resolve`` 逐字）：补上的那个
    默认值恰好是「盘上现在那一版」，于是「人在自己屏幕上看到的版本」这条判据会在
    调用方不写的时候自己成立 —— 而它正是这一条工具唯一能防住「两个人同时收同一个」
    的地方。

    读当前那一版在这里（存储那一侧也要读，但那是为了给拒绝措辞）；这一层读它是为了
    把「改派」与「挂起」这两条**没有换状态**的处置也算出来：``owner`` 与 ``blocked_by``
    只在这一层能改，而它们各自带的判据（``waiting`` 必须说清被谁挡着）在域那一侧。
    """
    current = store.get(action_id)
    if current is None:
        raise ConfigError(
            f"action_id={action_id!r} 在盘上不存在。"
            "去看这个 id 是不是抄错了 —— 这一条与「你手上的版本过期了」是两件事。"
        )
    changed = current.resolve(
        at=to_utc(at or utc_now()),
        state=state,
        by=by,
        expected_version=expected_version,
        resolution=resolution,
        owner=owner,
        blocked_by=blocked_by,
        due_at=due_at,
        evidence_refs=None if evidence_refs is None else tuple(evidence_refs),
    )
    return store.resolve(changed, expected_version=expected_version)


def list_action_items(
    *,
    store: ActionItemStore,
    workspace: str,
    owner: str | None = None,
    state: ActionItemState | None = None,
    open_only: bool = False,
) -> tuple[ActionItem, ...]:
    """跟进池的一页。**读它不改变任何东西**（``catalog.py`` 的 summary 逐字）。

    ``open_only`` 的缺省是 ``False`` 而不是 ``True``：一个「默认只回未结的」的实现
    会让「他有一条做完的」与「他从来没做过」在报告上同形，而 ``state`` 那一格点名
    的是**具体哪一个**，两者的差别见 ``ports/views.py::ActionItemStore.items``。
    """
    return store.items(workspace=workspace, state=state, owner=owner, open_only=open_only)

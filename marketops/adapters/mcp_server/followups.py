"""工作池的**监控与跟进那块**处理器（B24 的第五条加上后四条）。

五条工具：``put_watch`` / ``list_watches`` / ``list_action_items`` /
``open_action_item`` / ``resolve_action_item``。

``open_action_item`` 是这一族里**最后接上工具面的那一条**：应用层的那个用例
（``application/views.py``）与它的两条判据在切片之初就在了，缺的一直是**面**
（``tests/test_mcp_views.py`` 的模块 docstring 记着那条缺口）。它接在这里而不是
另起一个模块，是因为它 ``import`` 的东西与这一族**逐格相同**（见下面「这一族
不创建 Run、不起定时器」那一节），多一个模块只会让同一条纪律有两份签名。

## 与 ``views.py`` 的分家，就是那一条承重点本身

同一个工作池的四条读法是**不要预算**的（``views.py``），另外四条里有**一条要预算**：
``put_watch``。§5.3 逐字「启用 Watch 是写操作，需要权限和预算；保存 View 不是」。

所以分成两个模块不是因为四条四条一样多，是因为**那一格必须在 import 图上看得见**：
``views_handlers`` 的签名里**没有** ``ledger_factory``（一个拿得到账本的保存视图路径，
多了一处可以顺手 ``set_ceiling`` 的位置，而那件事发生时没有任何一条判据会响）；
``followups_handlers`` 的签名里**有**它，而且它真的写。

两条判据各钉一半（``tests/test_mcp_views_are_offline.py``）：视图那一族的 import 闭包里
**不许**出现 ``ports.budget``；这一族**必须**出现它。少掉后半句，「要预算」可以靠一个
根本没接账本的模块名达成 —— 而那一半的判据会全绿。

## ``put_watch`` 的预算那一半写的是什么

写的是**封套**：``WatchCeiling`` 逐条 ``set_ceiling(scope, line, units, source=...)``，
``scope`` 由 ``domain/watch.py::watch_budget_scope`` 推（那一个函数里记着为什么按
``watch_id`` 而不是按 ``revision`` 分账）。``source`` 是这条纪律的另一半 ——
``ports/budget.py`` 逐字「调用方不是不许改上限，是改了要**留名**，而且这个名要能从
账本本身读回来」，所以它写成 ``watch:<id>:by:<当事人>``：**谁**把哪条 Watch 的封套
定成了多少，账本自己答得出来。

**支出方今天不预留在那个 scope 上**，这一点如实记在 ``watch_budget_scope`` 的 docstring
与报告里，不在这里假装成立。

## 顺序：先写盘，再写账；账写不下时**说出盘上已经有什么**

``set_ceiling`` 只有一种失败：调低到**低于已占用**（``ports/budget.py`` 逐字）。那时
盘上已经躺着这一版 Watch 了，所以这一层**不把它折成「什么都没发生」** —— 回执里
``watch`` 与 ``revision_written`` 都在，``envelope`` 那一格说明哪几个科目写下去了、
哪一个被拒、以及拒它的原文。把两件事合成一句「失败了」会让「Watch 没建」与
「Watch 建了、封套没设」在报告上同形，而它们的处置不同（一个重发，一个去改上限）。

## 这一族**不创建 Run、不起定时器**

``put_watch`` 落一行规则、写一份封套；``list_watches`` 读；``list_action_items`` 读；
``open_action_item`` 往池子里放一件；``resolve_action_item`` 处置一件。**没有任何一条**
在这里建 Run 或排一个将来会发生的事 ——
「Watch 只产生 due request，Run 由既有 admission 创建」这句话在这条路上的形状是：
这一族连 ``ports.runs`` 都不 import（``application/views.py`` 里没有那条路，这一层也
没有）。全仓唯一的 trigger driver 是调度那一片的 ``schedule_definition`` /
``fire_schedule``；这一族**一个定时器都没有**，判据钉在
``tests/test_mcp_views_are_offline.py``（它扫 ``time.sleep`` / ``threading`` /
``asyncio`` / ``sched`` —— 一个想自己「每天看一眼」的实现一定会用到其中一个）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Sequence

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ...application.views import list_action_items as _apply_items
from ...application.views import list_watches as _apply_watches
from ...application.views import open_action_item as _apply_open
from ...application.views import put_watch as _apply_watch
from ...application.views import resolve_action_item as _apply_resolve
from ...application.views import watch_card as _card_of
from ...config import Settings
from ...domain.action_item import (
    ActionItem,
    ActionItemState,
    ActionType,
    ScopeKind,
    split_scope_ref,
)
from ...domain.errors import (
    ActionItemConflictError,
    ConfigError,
    MarketOpsError,
    OpenActionItemExistsError,
    StoreError,
)
from ...domain.watch import Watch, watch_budget_scope
from ...ports.budget import BudgetLedger
from ...ports.scenes import SceneStore
from ...ports.views import ActionItemStore, SavedViewStore, WatchStore
from ._shared import Handler, readable, require_scene, store_failure
from .views import _actor, _moment

__all__ = ["followups_handlers", "open_action_item_handler"]


# ----------------------------------------------------------------------
# 投影
# ----------------------------------------------------------------------
def _watch_receipt(card: Any) -> dict[str, Any]:
    """一条监控的一行读数。**四格都在，而它们的组合才是那句话。**

    ``enabled`` / ``target_state`` / ``target_missing`` / ``state_note`` 四格一起读，
    才能把「关了」与「目标没了」分开 —— 那是 ``catalog.py`` 的 ``list_watches`` 逐字
    要求的那件事（「目标视图已被删掉的 watch 如实报成那个样子，**不是静默变不活跃**」）。

    只回 ``enabled`` 是不够的：一条指着已删视图的 Watch 在只有那一格时读起来与一条
    安静待着的 Watch 一模一样，而它们的处置相反（去问为什么关的 / 重新存一份视图指过去）。
    ``target_missing`` 是 ``target_state`` 那一格算出来的**布尔读法**，两格一起给是因为
    「这一格是什么取值」与「这一格是不是那一件值得抬头看的事」是两个人问的问题。

    ``view_id`` 是 ``None`` 的时候（直接点名主体）``target_state`` 是 ``subjects`` ——
    而它**不是**「视图在」的同义词（``WatchTargetState`` 逐字）。
    """
    watch: Watch = card.watch
    return {
        "watch_id": watch.watch_id,
        "revision": watch.revision,
        "workspace": watch.workspace,
        "owner": watch.owner,
        "name": watch.name,
        "view_id": watch.view_id,
        "subjects": [[kind, subject] for kind, subject in watch.subjects],
        "aspects": [aspect.value for aspect in watch.aspects],
        "check_every_days": watch.check_every_days,
        "ceilings": [
            {"line": ceiling.line.value, "units": ceiling.units}
            for ceiling in watch.ceilings
        ],
        "notify_on": [when.value for when in watch.notify_on],
        "enabled": card.enabled,
        "next_due_at": (
            None if watch.next_due_at is None else watch.next_due_at.isoformat()
        ),
        "target_state": card.target_state.value,
        "target_missing": card.target_missing,
        "state_note": card.state_note,
        "note": watch.note,
        "created_at": watch.created_at.isoformat(),
        "updated_at": watch.updated_at.isoformat(),
        "content_digest": watch.content_digest,
    }


def _item_receipt(item: ActionItem) -> dict[str, Any]:
    """一件待办的一行读数。``list`` 与 ``resolve`` **共用这一份**。

    ``subject`` 是从 ``scope_ref`` **读回来**的（``split_scope_ref``，域里那唯一的解码
    入口），而 ``scope_ref`` 那一个扁平列**原样也留着**：读不回来时（库被改过、有人手拼
    过）``subject`` 是 ``None`` 而 ``scope_ref`` 照旧在回执里 —— 一个把读不回来的值
    静默丢掉、或者当成 ``account`` 的实现，会让一条挂在路线上的待办看起来挂在账号上
    （``split_scope_ref`` 逐字要防的正是这件事）。

    ``disposition_digest`` 与 ``expected_version`` 一并给：前者是「同一次处置被重发」
    那条幂等判据的一半，后者是下一次处置要带回来的那一格。调用方要能**从这份回执里**
    拿到它们，而不是回头再读一次（两次读之间有人写过的话，那两个数就分家了）。
    """
    try:
        kind, subject_id = split_scope_ref(item.scope_ref)
        subject: dict[str, str] | None = {"kind": kind.value, "subject_id": subject_id}
    except ValueError:
        subject = None
    return {
        "action_id": item.action_id,
        "revision": item.revision,
        "workspace": item.workspace,
        "scope_ref": item.scope_ref,
        "subject": subject,
        "action_type": item.action_type.value,
        "owner": item.owner,
        "state": item.state.value,
        "is_open": item.is_open,
        "due_at": None if item.due_at is None else item.due_at.isoformat(),
        "blocked_by": item.blocked_by,
        "reason": item.reason,
        "evidence_refs": list(item.evidence_refs),
        "resolution": item.resolution,
        "resolution_by": item.resolution_by,
        "expected_version": item.expected_version,
        "disposition_digest": item.disposition_digest,
        "created_at": item.created_at.isoformat(),
        "resolved_at": (
            None if item.resolved_at is None else item.resolved_at.isoformat()
        ),
        "updated_at": item.updated_at.isoformat(),
        "note": item.note,
    }


# ----------------------------------------------------------------------
# 写：预算
# ----------------------------------------------------------------------
def _seed_envelope(*, ledger: BudgetLedger, watch: Watch, actor: str) -> dict[str, Any]:
    """把这一版 Watch 的封套写进账本，并回一份「写下去了什么」的读数。

    **它会抛**（``BudgetError`` 那一族）—— 但那不是这一层的错，是账本在说「这个上限
    调不下来」。调用点接住它，并把「盘上已经有什么」一起报出去（见模块 docstring 的
    「顺序」那一节）。这里不接，是因为接住之后要做的事属于**那一次调用的回执**，
    而不是这一段的。

    ``source`` 里的当事人是**令牌上那个**（:func:`~marketops.adapters.mcp_server.views._actor`
    给的），不是参数 —— 与 ``owner`` 同一条纪律：一个能传的署名让调用方把一次上限变更
    记在别人头上，而账本上那一格看起来完全正常。
    """
    scope = watch_budget_scope(workspace=watch.workspace, watch_id=watch.watch_id)
    seeded: list[dict[str, Any]] = []
    for ceiling in watch.ceilings:
        source = f"watch:{watch.watch_id}:by:{actor}"
        ledger.set_ceiling(scope, ceiling.line, ceiling.units, source=source)
        seeded.append(
            {
                "scope": scope,
                "line": ceiling.line.value,
                "units": ceiling.units,
                "source": source,
            }
        )
    return {
        "budget_scope": scope,
        "ceilings": seeded,
        "note": (
            "封套写在 scope 上，由 domain.watch.watch_budget_scope 推导（按 watch_id，"
            "不按 revision —— 改规则不重开一个账）。source 记下是谁把它定成了多少，"
            "这一格从账本本身读得回来。\n"
            "**今天没有支出方预留到这个 scope 上**：账本那一侧的支出仍按 "
            "(profile, 账号) / (profile, 发现范围) 分账。封套是写下来、读得回来的，"
            "而「谁的预留要落进这个账」这条链接还没有接上。"
        ),
    }


# ----------------------------------------------------------------------
# 五条工具
# ----------------------------------------------------------------------
def _put_watch(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    views: Callable[[], SavedViewStore],
    watches: Callable[[], WatchStore],
    ledger_factory: Callable[[], BudgetLedger],
    scene_id: str | None,
    watch_id: str,
    name: str,
    ceilings: Sequence[Any],
    notify_on: Sequence[Any],
    expected_revision: int | None,
    view_id: str | None,
    subjects: Sequence[Any] | None,
    aspects: Sequence[str] | None,
    check_every_days: int,
    next_due_at: str | None,
    enabled: bool,
    note: str,
) -> dict[str, Any]:
    """开一条或改一条监控。**这一条要权限、要预算。**

    ## 三件在这一层判的事，各自的理由

    * **场景**：``require_scene`` 走本部署服务的那个市场 —— 与视图那一族**相反**，理由
      在那一族的 docstring 里（视图的键里没有 scene）。
    * **暂停**：判据在 ``application/views.py::_require_scene_active``，用的是
      ``due_work`` 的**同一条**判据（不是在这里再判一遍）。这一层只把它接住、翻成一条
      可读回执 —— 它是「调用方要据它改东西」的结局，不是一次崩溃。
    * **封套**：写盘**之后**才写账（见模块 docstring 的顺序那一节）。账写不下去时
      回执里两件事**都在**。
    """
    chosen = require_scene(settings, scene_id)
    principal = _actor()
    moment = _moment(next_due_at, "next_due_at")
    try:
        write = _apply_watch(
            store=watches(),
            scene_store=scenes(),
            scene_id=chosen,
            watch_id=watch_id,
            workspace=settings.profile,
            owner=principal.name,
            name=name,
            ceilings=ceilings,
            notify_on=notify_on,
            view_id=view_id,
            subjects=() if subjects is None else subjects,
            aspects=() if aspects is None else aspects,
            check_every_days=check_every_days,
            next_due_at=moment,
            expected_revision=expected_revision,
            enabled=enabled,
            note=note,
        )
    except ValidationError as exc:
        # 这一版 Watch 构造不出来（没有上限 / 没有通知时机 / 两个目标都没有 /
        # 科目写重了 / 认不出的 aspect）。**逐字段点名**：调用方要据它改参数。
        raise ToolError("这条 Watch 构造不出来：\n" + readable(exc)) from exc
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise store_failure(exc) from exc

    written = write.revision_written
    if written is None:
        landing = (
            f"盘上当前那一版（第 {write.watch.revision} 版）与这一次要写的一字不差，"
            "所以没有新版本落盘。"
        )
    else:
        landing = f"这一版（第 {written} 版）已经落盘。"
    try:
        # 目标视图在不在**在这一次调用里也答一次**：刚写下的这一版可能就是指着一条
        # 已删（或还没存过）的视图 —— 那是合法的，而它必须在那一次的回执里就看得见，
        # 不能等到别人去读清单。判据与清单共用 ``watch_card``（一处定义）。
        card = _card_of(
            watch=write.watch,
            view_exists=(
                None
                if write.watch.view_id is None
                else views().current(write.watch.view_id) is not None
            ),
        )
    except StoreError as exc:
        raise store_failure(exc) from exc
    receipt: dict[str, Any] = {
        "watch": _watch_receipt(card),
        "revision_written": written,
        "note": landing,
    }
    try:
        receipt["envelope"] = _seed_envelope(
            ledger=ledger_factory(), watch=write.watch, actor=principal.name
        )
    except MarketOpsError as exc:
        # 账本那一侧说「这个上限调不下来」（``BudgetError`` 那一族）。**它不折成
        # 「什么都没发生」**：盘上已经躺着这一版 Watch 了，所以回执里两件事都在。
        #
        # 捕 ``MarketOpsError`` 而不是逐个列 ``BudgetError`` 的子类：那个清单会随着
        # ``ports/budget.py`` 长大而漂移，而漂移的那一次是一条**未处理异常**（读起来
        # 像故障，而不是一条要给人看的拒绝）。捕得宽而**不吞** —— 异常原文照给。
        #
        # ``StoreError`` 也是 ``MarketOpsError`` 的兄弟，上面那一段已经先接过它了
        # （它发生在写盘那一半），走到这里的是账本自己的拒绝。
        receipt["envelope"] = {
            "error": str(exc),
            "note": (
                "这一版 Watch **已经在盘上**（见 watch / revision_written 两格），"
                "而它的封套没有全部写进账本。"
                "最常见的原因是把某个科目调到了低于已占用的量 —— 那会让账本立刻处于"
                "「已经超支」的状态，而超支之后所有后续预留都被拒。"
            ),
        }
    return receipt


def _list_watches(
    *,
    settings: Settings,
    watches: Callable[[], WatchStore],
    views: Callable[[], SavedViewStore],
    enabled: bool | None,
) -> dict[str, Any]:
    """监控清单。**目标视图已被删掉的如实报成那个样子**（见 :func:`_watch_receipt`）。

    ``views`` 是**必填的**，而这不是接线上的便利：目标视图在不在只有这里读得到
    （``views.current``），一个缺省的空读面会让**每一条** Watch 都报成
    ``view_missing`` —— 那是「这个部署没接上视图存储」伪装成的一条业务读数，而它是
    最容易被当成真事的那一种（「目标没了」是一条**要处置**的结论）。
    """
    try:
        cards = _apply_watches(
            store=watches(), views=views(), workspace=settings.profile, enabled=enabled
        )
    except StoreError as exc:
        raise store_failure(exc) from exc
    receipts = [_watch_receipt(card) for card in cards]
    return {
        "workspace": settings.profile,
        "watches": receipts,
        "count": len(receipts),
        "enabled_filter": enabled,
        "targets_missing": [
            item["watch_id"] for item in receipts if item["target_missing"]
        ],
        "note": (
            "target_state 是**读出来的**（拿目标视图此刻在不在进来判），不是从 enabled "
            "推的：一条指着已删视图的 Watch 若是被渲染成「不活跃」，"
            "「有人关了它」与「它指的东西没有了」就在报告上同形。"
            "targets_missing 只是把那一格拎出来，方便先看这几条。"
        ),
    }


def _items(
    *,
    settings: Settings,
    items: Callable[[], ActionItemStore],
    owner: str | None,
    state: str | None,
    open_only: bool,
) -> dict[str, Any]:
    """跟进池的一页：**它是耐久下一步工作的一个投影**，读它不改变任何东西。

    ``open_only`` 的缺省是 ``false`` 而不是 ``true``（``application/views.py::list_action_items``
    逐字）：一个「默认只回未结的」实现会让「他有一条做完的」与「他从来没做过」在报告上
    同形。``state`` 点名的是**具体哪一个**状态，与 ``open_only`` 是两件事 —— 两个都
    收下，而这一层不把前者翻译成后者。
    """
    chosen: ActionItemState | None = None
    if state is not None and str(state).strip():
        try:
            chosen = ActionItemState(str(state).strip())
        except ValueError as exc:
            raise ToolError(
                f"state 是 {state!r}，它不是一个待办状态"
                f"（认识的：{[item.value for item in ActionItemState]}）。\n"
                "  一个认不出的状态被静默当成「不过滤」，会让一份本该只有几行的清单"
                "回出整个池子 —— 而它读起来完全正常。"
            ) from exc
    try:
        rows = _apply_items(
            store=items(),
            workspace=settings.profile,
            owner=owner,
            state=chosen,
            open_only=open_only,
        )
    except StoreError as exc:
        raise store_failure(exc) from exc
    return {
        "workspace": settings.profile,
        "action_items": [_item_receipt(row) for row in rows],
        "count": len(rows),
        "owner_filter": owner,
        "state_filter": None if chosen is None else chosen.value,
        "open_only": open_only,
        "note": (
            "scope_ref 原样回、subject 是从它读回来的那一对（读不回来时 subject 是 "
            "null 而 scope_ref 照旧在）—— 一条挂在路线上的待办不许看起来挂在账号上。"
            "expected_version 是下一次处置要带回来的那一格；disposition_digest 是"
            "「同一次处置被重发」那条幂等判据的一半。"
        ),
    }


def _open(
    *,
    settings: Settings,
    items: Callable[[], ActionItemStore],
    action_id: str,
    scope_kind: str,
    scope_id: str,
    action_type: str,
    reason: str,
    owner: str | None,
    due_at: str | None,
    blocked_by: str,
    evidence_refs: Sequence[str] | None,
) -> dict[str, Any]:
    """把一件**新**待办放进池子。**建一件待办要权限，但不花预算、不建 Run。**

    ## ``workspace`` 不是入参，而且这不是省一格

    它取自 ``settings.profile``（与 ``_items`` / ``_list_watches`` 同一条），因为
    ``(workspace, action_type, scope_ref)`` 上那条 partial unique index 的第一格就是
    它：一个能填的 ``workspace`` 让调用方把「同一主体上的第二条未结待办」建到另一个
    工作区里 —— 约束一次都不响，而两条记录各自都正常。越权的那一次在
    ``RoleGuard`` 那一层就被拒（令牌的 ``workspaces`` 与 ``settings.profile`` 比对），
    到不了这里。

    ## ``owner`` 在这里是入参，理由与两条写工具**不同**

    ``put_saved_view`` / ``put_watch`` 上 ``owner`` 是 ``(workspace, owner, …)`` 那条
    键的一半，所以那里它只能来自令牌。这里不是：待办的键里**没有 owner**
    （``(workspace, action_type, scope_ref)``），而「派给谁」这一格
    ``resolve_action_item`` 本来就允许改派（「一个人可以替别人关掉一件工作」，
    ``ActionItem.resolve`` 逐字）。缺省 ``None`` = **没人认领**，而这不是一个残值：
    §B24 那个「有通用邮箱但缺负责人」的例子要建的正是一件**没有负责人**的待办
    （``ActionItem`` 的 validator 逐字：「没人认领时它应当是 None」），把缺省写成
    「建件人自己」会让那一类积压在报告上看起来已经有人管了。

    ## 重复不是在这一层判的

    ``application/views.py::open_action_item`` 逐字：``store.create`` 会吃到一次插入
    失败并把它翻成 ``OpenActionItemExistsError``（点名盘上那一条的 ``action_id``）。
    这里**不写「先查一下」** —— 先查后插在两个写者之间有一个窗口，而判据住在应用代码
    里时，另一条路径、另一个进程、一次手写的 SQL 都挡不住。
    捕得**宽而准**：``OpenActionItemExistsError`` 与 ``StoreError`` 是兄弟，两个都要
    接住并各自说各自的话（前者「去处理那一条」，后者「存储事故 / 同一个 id 配了另一份
    内容」），合成一句会让两种处置压成一格。

    ## 同一个 ``action_id`` 再发一次是**幂等**的，而回执说得出

    ``revision_written`` 为 ``None``、``note`` 明说：内容一字不差时盘上那一件原样回来
    （``ports/views.py::ActionItemStore.create`` 逐字），不产生第二条历史。
    """
    try:
        kind = ScopeKind(str(scope_kind).strip())
    except ValueError as exc:
        raise ToolError(
            f"scope_kind 是 {scope_kind!r}，它不是一个主体种类"
            f"（认识的：{[item.value for item in ScopeKind]}）。\n"
            "  它是 ``scope_ref`` 的主语那一半（``account#acct-1``），而 ``scope_ref``"
            " 是那条 partial unique index 的一半 —— 猜一个（或者当成分不出主语的自由"
            "字符串）会让「同一主体上的第二条」在库上成了另一个主体。"
        ) from exc
    try:
        wanted = ActionType(str(action_type).strip())
    except ValueError as exc:
        raise ToolError(
            f"action_type 是 {action_type!r}，它不是一个待办类型"
            f"（认识的：{[item.value for item in ActionType]}）。\n"
            "  这四种各自的完成条件不同，所以它们不能合成一个 TODO；一个自由字符串"
            "会在跟进池里长出一类**没有完成条件**的工作，而它永远留在池子里。"
        ) from exc
    moment = _moment(due_at, "due_at")
    try:
        write = _apply_open(
            store=items(),
            action_id=action_id,
            workspace=settings.profile,
            scope_kind=kind,
            scope_id=scope_id,
            action_type=wanted,
            reason=reason,
            owner=owner,
            due_at=moment,
            blocked_by=blocked_by,
            evidence_refs=() if evidence_refs is None else evidence_refs,
        )
    except ValidationError as exc:
        # 「reason 不能是空的」「waiting 必须说清被谁挡着」这一类**跨格**的判据由模型
        # 兜住（``ActionItem`` 的 model validator）。逐字段点名：调用方要据它改参数。
        raise ToolError("这一件待办构造不出来：\n" + readable(exc)) from exc
    except OpenActionItemExistsError as exc:
        # 挂在 ``StoreError`` **之前**：它不是 ``StoreError`` 的子类，两者是
        # ``MarketOpsError`` 的兄弟，所以少了这一条它不落进下面那一格。
        #
        # **这一条就是那句话到得了调用方的唯一原因** —— 「不接它结果也一样，只是靠
        # 兜底格式化器带过去」是一个**读错过的结论**，端到端实测把它推翻了。SDK
        # ``mcp/server/mcpserver/tools/base.py`` 对处理器抛出的每一条**非** ``MCPError``
        # 分四支，其中两支的措辞成形于 ``:204`` 与 ``:207``（``ToolError`` 与
        # ``UnexpectedToolError`` 都保留内层文本，前面加一句 ``Error executing tool <名>:``），
        # 而**别的任何异常**在 ``:210`` 落成
        # ``UnexpectedToolError(f"Error executing tool {self.name}")`` —— 一句话里
        # **没有异常文本**（同处注释逐字：「the exception's own text stays on the server」）。
        # ``OpenActionItemExistsError`` 是 ``MarketOpsError`` 的子类，既不是 ``MCPError``
        # 也不是 ``ToolError``，原样逃出去就落进 ``:210`` 那一支：调用方读到的是光秃秃的
        # ``Error executing tool open_action_item``，**盘上那一条的 ``action_id`` 一个字
        # 都到不了他手上**。实测两个半边的回执逐字（真 ``ClientSession`` ↔ 真服务端，
        # 只吃 in-memory 流不吃网络）：
        #
        #   接住 → ``Error executing tool …: 同一主体上的这一类未结待办已经有一条：action_id=act-7（state=open）。``
        #   逃出 → ``Error executing tool …``
        #
        # 所以留着这一格不只是「让代码点名这个结局」（``errors.py::OpenActionItemExistsError``
        # 逐字要求消息点名盘上那一条的 ``action_id``），**这个结局的可观测性本身就挂在这一格上**：
        # 删掉它，「去处理那一条」这个处置就没了主语。判据钉的是那句消息本身，且它是一条
        # **活的**判据 —— 把这里的重抛换成 ``raise exc``（等价于删掉这一格）会立刻转红，
        # 红的原因是那句被点名的 ``act-new`` 不在回执里（``test_mcp_views.py`` 第六节）。
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise store_failure(exc) from exc
    written = write.revision_written
    if written is None:
        landing = (
            "盘上那一件与这一次要建的**一字不差**（同一个 ``action_id``、同一份内容），"
            "所以什么都没写 —— 回给你的那一件是盘上那一件。"
        )
    else:
        landing = (
            f"这一件落盘了（第 {written} 版，state {write.item.state.value}）。"
            "下一次处置要带回来的那一格在 ``item.expected_version`` 里 —— "
            "它是「同一次处置被重发」那条幂等判据的一半，"
            "从这份回执里拿得到，不必回头再读一次。"
        )
    return {
        "item": _item_receipt(write.item),
        "revision_written": written,
        "note": landing,
    }


def open_action_item_handler(
    *, settings: Settings, items: Callable[[], ActionItemStore]
) -> Handler:
    """B24 的第五条**工具面那一格**：把 :func:`_open` 接成一个 MCP handler。

    ## 这一格与目录那一行是**一次落地** —— 撤也要一起撤

    ``server.py`` 的 ``_register`` 逐字：**目录里没有的名字是一个构造期拒绝** ——
    ``ValueError("这些工具名不在目录里，注册不出任何东西：{...}")``。所以这一条的两格
    （``catalog.py`` 里那一条 ``_spec(...)``、下面 ``followups_handlers`` 返回值里那一格）
    **不能分两次落地**，而两个方向的半边各自坏得不一样：

    * 只有这一格、没有目录那一行 —— 整个组合根起不来（``__main__.py`` 的 ``_compose``
      → ``build_server``），全仓所有走 ``build_server`` 的用例一起转红。实测逐字。
    * 只有目录那一行、没有这一格 —— 它在 ``tools/list`` 上**出现了**，而调用回一句
      「本构建尚未接线」。这个形态在 ``tools/list`` 上与接好的形态逐字同形，只在真去
      调一次时才分得出。

    ``__main__.py`` 在调度那一格上为同一件事写过一条纪律，逐字：「两件事分两次做会造出
    一个「接了但一次研究都开不出来」的中间态，而那个中间态在 ``tools/list`` 上与接好的
    形态逐字同形」。**这条工具的两格是同一次落地的**（目录那一行由 MAIN 落、这一格在
    本文件里落），各自的判据在 ``tests/test_mcp_views.py`` 第六节：那里走的是一次真的
    ``tools/call``，所以「接好了」这件事有读数，而不是靠读代码。
    """
    def open_action_item(
        action_id: str,
        scope_kind: str,
        scope_id: str,
        action_type: str,
        reason: str,
        owner: str | None = None,
        due_at: str | None = None,
        blocked_by: str = "",
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        return _open(
            settings=settings,
            items=items,
            action_id=action_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            action_type=action_type,
            reason=reason,
            owner=owner,
            due_at=due_at,
            blocked_by=blocked_by,
            evidence_refs=evidence_refs,
        )

    return open_action_item


def _resolve(
    *,
    items: Callable[[], ActionItemStore],
    action_id: str,
    expected_version: int,
    state: str,
    resolution: str,
    owner: str | None,
    blocked_by: str,
    due_at: str | None,
    evidence_refs: Sequence[str] | None,
) -> dict[str, Any]:
    """把一条待办收成 done/dismissed、改派、或挂起。**要 ``expected_version``。**

    ``expected_version`` **没有缺省值**，而这不是风格：补上的那个默认值恰好是「盘上
    现在那一版」，于是「人在自己屏幕上看到的版本」这条判据会在调用方不写的时候**自己
    成立** —— 而它正是这一条工具唯一能防住「两个人同时收同一个」的地方
    （``domain/action_item.py::ActionItem.resolve`` 逐字）。

    重发同一次处置是幂等的，而回执**说得出**这一次是不是重发
    （``revision_written`` 为 ``null``、``note`` 明说）：「你刚才那次成功了」与
    「你第二次点的时候什么都没发生」是两句不同的话。

    ``by`` 取自传输层（:func:`~marketops.adapters.mcp_server.views._actor`），不是参数：
    一个能传的 ``by`` 让调用方可以把「是谁处置的」写成别人，而那条历史看起来完全正常。
    """
    try:
        wanted = ActionItemState(str(state).strip())
    except ValueError as exc:
        raise ToolError(
            f"state 是 {state!r}，它不是一个待办状态"
            f"（认识的：{[item.value for item in ActionItemState]}）。\n"
            "  「收成某一种状态」与「它的状态没变」是两件事，所以这一格不猜。"
        ) from exc
    principal = _actor()
    moment = _moment(due_at, "due_at")
    try:
        write = _apply_resolve(
            store=items(),
            action_id=action_id,
            expected_version=expected_version,
            state=wanted,
            by=principal.name,
            resolution=resolution,
            owner=owner,
            blocked_by=blocked_by,
            due_at=moment,
            evidence_refs=evidence_refs,
        )
    except ValidationError as exc:
        # 「收成终态必须带一句 resolution」这一类**跨格**的判据由模型兜住
        # （``ActionItem`` 的 model validator）。逐字段点名：处置就是改参数的事。
        raise ToolError("这一条处置构造不出来：\n" + readable(exc)) from exc
    except ConfigError as exc:
        # 「这个 action_id 在盘上不存在」与「你手上的版本过期了」是**两句不同的话**
        # （``application/views.py::resolve_action_item`` 逐字），而它们在这里是两种
        # 异常：前者 ConfigError、后者 ActionItemConflictError（StoreError 的兄弟）。
        raise ToolError(str(exc)) from exc
    except ActionItemConflictError as exc:
        # 上面那一句说的是「两句不同的话」，而这一条是**后一句的执行器**：
        # 兄弟异常只被 ``ConfigError`` 那一条接住是不成立的，它同样会逃出去。
        #
        # 逃出去之后调用方读到的是光秃秃的一句
        # ``Error executing tool resolve_action_item`` —— 而这里丢掉的那句话逐字是
        # 「你手上的版本是 1，而盘上当前是第 2 版（状态 done）—— 有人先处置过它了。
        # 请重读这一件再决定」。那正是承重点 5 唯一能防住「两个人同时收同一个」的
        # 地方，也是 ``catalog.py`` 的 summary 逐字承诺的那句「版本**过期**时被拒
        # 而不是盖掉更新的那些事实」——被拒，而不是崩掉。
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise store_failure(exc) from exc
    written = write.revision_written
    if written is None:
        landing = (
            "盘上那一件已经是这一次处置的结果（同一次处置被**重发**），"
            "所以这一次什么都没写 —— 回给你的那一件是盘上那一件。"
        )
    else:
        landing = f"这一件收到了第 {written} 版（当前状态 {write.item.state.value}）。"
    return {
        "item": _item_receipt(write.item),
        "revision_written": written,
        "note": landing,
    }


def followups_handlers(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    views: Callable[[], SavedViewStore],
    watches: Callable[[], WatchStore],
    items: Callable[[], ActionItemStore],
    ledger_factory: Callable[[], BudgetLedger],
) -> dict[str, Handler]:
    """监控与跟进这一族**已接线**的那五条，按工具名索引。

    Args:
        settings: 配置。``workspace`` 与场景都从它来（见 ``views.py`` 的模块 docstring）。
        scenes: 场景存储的**工厂**。``put_watch`` 用它判「这个场景今天派不派发新工作」。
            **必填、没有缺省**：一个可以省略它的签名会让「没接上」悄悄变成「没暂停」，
            而这两件事的处置分别是「去接线」与「可以开」。
        views: 视图存储的**工厂**。``put_watch`` 与 ``list_watches`` 都用它答
            「目标视图还在不在」。**必填**，理由与 ``scenes`` 同款：缺省会让每一条都报
            ``view_missing``（见 :func:`_list_watches`）。
        watches / items: 监控与待办的**工厂**。同理必填：一个缺省的空读面会让
            「没有这一族存储」与「这个工作区一条都没有」同形。
        ledger_factory: 每次调用开一个账本（``sqlite3`` 的连接不能跨线程）。
            **必填**，与上面几格的理由不同：它是这一族与 ``views.py`` 那一族分家的
            那一格本身。``None`` 会让「要预算」这句话在这一族里没有执行器 ——
            而它正是 §5.3 那半句的落点。

    Returns:
        五条工具。**没有任何一条建 Run、排定时器，也没有一条 import ``ports.runs``。**
        五条里**只有 ``put_watch`` 花预算**（``ledger_factory``）：``open_action_item``
        建一件待办是一次写，但它不产生外部调用、不建 Run、不写账 —— 这一格没变。
    """

    def put_watch(
        watch_id: str,
        name: str,
        ceilings: list[dict],
        notify_on: list[str],
        expected_revision: int | None,
        scene_id: str | None = None,
        view_id: str | None = None,
        subjects: list[list[str]] | None = None,
        aspects: list[str] | None = None,
        check_every_days: int = 1,
        next_due_at: str | None = None,
        enabled: bool = True,
        note: str = "",
    ) -> dict[str, Any]:
        return _put_watch(
            settings=settings,
            scenes=scenes,
            views=views,
            watches=watches,
            ledger_factory=ledger_factory,
            scene_id=scene_id,
            watch_id=watch_id,
            name=name,
            ceilings=ceilings,
            notify_on=notify_on,
            expected_revision=expected_revision,
            view_id=view_id,
            subjects=subjects,
            aspects=aspects,
            check_every_days=check_every_days,
            next_due_at=next_due_at,
            enabled=enabled,
            note=note,
        )

    def list_watches(enabled: bool | None = None) -> dict[str, Any]:
        return _list_watches(
            settings=settings, watches=watches, views=views, enabled=enabled
        )

    def list_action_items(
        owner: str | None = None,
        state: str | None = None,
        open_only: bool = False,
    ) -> dict[str, Any]:
        return _items(
            settings=settings, items=items, owner=owner, state=state, open_only=open_only
        )

    def resolve_action_item(
        action_id: str,
        expected_version: int,
        state: str,
        resolution: str = "",
        owner: str | None = None,
        blocked_by: str = "",
        due_at: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        return _resolve(
            items=items,
            action_id=action_id,
            expected_version=expected_version,
            state=state,
            resolution=resolution,
            owner=owner,
            blocked_by=blocked_by,
            due_at=due_at,
            evidence_refs=evidence_refs,
        )

    return {
        "put_watch": put_watch,
        "list_watches": list_watches,
        "list_action_items": list_action_items,
        # 第五条。它落在这里的这一格与 ``catalog.py`` 那一行是**一次落地** ——
        # 只有目录行时这一条在 ``tools/list`` 上出现了而调不动（一个对外承诺过的名字
        # 回一句「本构建尚未接线」，而那与接好的形态逐字同形）；只有这一格时
        # ``_register`` 让整个组合根起不来。逐字理由见 :func:`open_action_item_handler`。
        "open_action_item": open_action_item_handler(settings=settings, items=items),
        "resolve_action_item": resolve_action_item,
    }

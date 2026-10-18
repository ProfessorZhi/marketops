"""工作池的**视图那一族**处理器（B24 的前四条）。

四条工具：``put_saved_view`` / ``get_saved_view`` / ``list_saved_views`` /
``run_saved_view``。判据全部住在 ``application/views.py``（那一层的 docstring 写清了
三件结构性的事与两个「如实报」）；这一层做四件事：取时刻、把当事人从传输层读出来、
把 domain 与存储的拒绝翻成一条可读回执、把应用层的值对象**一个格子不加**地交出去。

## 这一族**没有账本**，而且是签名上的没有

``views_handlers`` 的形参是 ``settings`` / ``views`` / ``pool`` 三个。没有
``ledger_factory``，没有 ``scene_store``，没有 ``scenes`` —— 而这不是「恰好没接」：

§5.2 逐字「SavedView 只是查询，不产生外部调用、不产生预算」，而 §5.3 那条
「启用 Watch 是写操作，需要权限和预算；**保存 View 不是**」是同一件事的另一半。
两个半边各需要一个执行器，而**保存视图那一边的执行器就是这个签名**：一个拿得到
账本的 ``put_saved_view`` 在读取路径上多了一格可以顺手 ``set_ceiling`` 的位置，而
那件事发生的时候没有任何一条判据会响 —— 它只是一次多出来的写入。

对照 :mod:`marketops.adapters.mcp_server.followups`（同一片的另一半）：那一族**要**
账本，而且它 ``set_ceiling``。两份签名并排读，才是那条承重点在代码里的样子。

## 零 provider / 零预算 / 零 Run：这一族到不了

* **零 provider**：这一族的 import 闭包里没有 ``ports.search`` / ``ports.acquisition``。
  它 import 的是 ``application.views``（那一层收的是 ``SavedViewStore`` 与
  ``AccountPoolStore``，而 ``project_accounts`` 拿到的是一批**已经在内存里的**
  ``MarketAccount``）。
* **零预算**：没有账本可拿，也没有 ``ports.budget``。
* **零 Run**：没有 ``ports.runs``，也没有 ``application.run_admission``。跑一份视图
  连一个 ``RunSpec`` 都构造不出来。

判据是 ``tests/test_mcp_views_are_offline.py``：它走 AST 算这一族的 import 闭包，
**白名单精确相等**，并另有一条反面要求（闭包里必须真的有 ``ports.views`` 与
``ports.pool``）—— 少掉反面的话，一个什么都不读的空壳也能通过「到不了网络」那一条。

## 为什么 ``require_scene`` 不在这条路上

启动预览那一族用它，是因为「这一版能不能启动」必须钉在**本部署服务的那个市场**上。
视图不是：``saved_view`` 的扁平列里只有 ``workspace``，没有 ``scene_id``（§5.2 的
字段逐字也没有），而一份视图的键是 ``(workspace, owner, name)``。所以这一族读的是
``settings.profile`` **本身**，而不是「某个场景恰好等于它」—— 多转一道的场景判据会
让「本部署的工作区」与「被点名的场景」这两个概念在这里第一次分家，而分家的形态是
一份写在 A 市场、读在 B 市场的视图（两条记录各自都正常）。

## 两个「如实报」在这一层的落点

* ``run_saved_view`` 的回执**每条 run 各带** ``accounts_read`` 与
  ``accounts_distinct``，并在最外层给一份合计。三份视图打到同一个账号上时，前者的
  合计是 3、后者是 1 —— 「一个 Account 读了三次」这句话在**一份回执里**读得出来，
  而不是要读的人自己跨三次调用去数。
* ``run_saved_view`` 跑不动时**一行都不回**，``refusal`` 里逐条点名 ``part`` 与
  ``reason``。回执里也永远有 ``unrunnable`` 那一格（可能为空）—— 一个「失败时没有
  这一格」的投影会让「跑不动」与「这一版没有它」同形。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Sequence

from mcp.server.mcpserver.exceptions import ToolError

from ...application.views import (
    SavedViewCard,
    ViewRun,
    get_saved_view as _get_view,
    list_saved_views as _list_views,
    put_saved_view as _put_view,
    run_saved_views as _run_views,
)
from ...config import Settings
from ...domain.errors import ConfigError, SavedViewConflictError, StoreError
from ...domain.saved_view import CompiledQuery, SavedView, ViewVisibility
from ...ports.pool import AccountPoolStore
from ...ports.views import SavedViewStore, ViewWrite
from ...security.catalog import Principal
from ._shared import Handler, store_failure
from .guard import current_principal

__all__ = ["views_handlers"]


def _actor() -> Principal:
    """这一次调用的当事人。**经传输层不可达**（无权者到不了这四条工具）。

    留下它不是为了断言一个事实，而是为了让绕过中间件的调用者（测试、直接调处理器的
    那一侧）拿到一句能读的拒绝，而不是一个 ``AttributeError`` —— 与
    ``review._actor`` 同一个理由、同一个分支形状。

    它在这一族的用处与 ``review`` 那一边**不完全一样**，值得写清：议决那一族的 ``by``
    要进历史，所以它连 ``roles`` 都要收（「这个人当年有没有资格做这件事」）；这一族
    只有一格用得到主体 —— ``owner``。而 ``owner`` 是 ``(workspace, owner, name)``
    那条判据的一半，所以它**必须**来自令牌，不能来自参数：一个能传的 ``owner`` 让
    调用方把视图存进别人的命名空间，而那条记录看起来完全正常。
    """
    principal = current_principal()
    if principal is None:
        raise ToolError(
            "这个动作需要主体：视图的 owner 取自传输层的 Bearer 令牌，不取自任何参数。"
            "一个能传的 owner 让调用方可以把视图存进别人的命名空间 —— "
            "而那条记录在盘上看起来完全正常。"
        )
    if not principal.name.strip():
        raise ToolError(
            "这枚令牌没有主体名（sub 与 client_id 都是空的），所以这一次调用没有可记的"
            " owner —— 而 owner 是 (workspace, owner, name) 那条判据的一半。重铸一枚令牌。"
        )
    return principal


def _moment(value: str | None, field: str) -> datetime | None:
    """回执里那一格时刻。``None`` 原样回 ``None``（**不是**「现在」）。

    **这一个定义服务两个族**（本模块与 ``followups.py``）：``followups`` 从本模块引它，
    而不是再抄一份。本包今天已经有五份 ``_moment``（``fetch_attempt`` / ``outreach`` /
    ``review`` / ``runs``），第六份不该由这一片加进来 —— 而它不能住进 ``_shared.py``，
    因为那会让四个不判时刻的族也把它拉进闭包（``_shared`` 是**闭包成员**，不是 import
    图上的叶子）。

    不带时区的写法（``2026-09-29T10:00:00``）在这里能被 ``fromisoformat`` 解析出来，
    却会按服务所在机器的本地时区解释 —— 于是「这一刻到期了没有」取决于服务跑在哪里。
    所以它在这里被拒，而不是被补上一个时区。
    """
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolError(
            f"{field} 是 {text!r}，读不成一个时刻：{exc}\n"
            "  要 ISO-8601 的形状，例如 2026-09-29T10:00:00+08:00。"
        ) from exc
    if parsed.tzinfo is None:
        raise ToolError(
            f"{field} 是 {text!r}，它没有带时区。\n"
            "  一个省掉时区的时刻会被按服务所在机器的本地时区解释，于是「这一刻到期了"
            "没有」取决于服务跑在哪里。"
        )
    return parsed


# ----------------------------------------------------------------------
# 投影：**一份定义、多个出口**
# ----------------------------------------------------------------------
def _view_receipt(view: SavedView) -> dict[str, Any]:
    """一份视图的一行读数。``put`` / ``get`` / ``list`` **三处共用这一份**。

    三处各写一份的那天，同一条 ``view_id`` 在三个工具里会长得不一样 —— 而这是它最不
    该有的样子（``review._case_view`` 逐字同一条）。

    ``query`` 是**存的原样**（§5.2 的 ``query/filter/version``），与 ``get_saved_view``
    另外回的 ``compiled`` 并排：只回后者会让「我写的过滤」与「编译器的读法」这两件事
    合成一件，而「一处改写无处对照」正是这条 summary 要挡的读法。

    ``content_digest`` 一并给：它是两个存储实现判「这一次要写的是不是已经就是盘上那
    一份」的那一格，也是调用方能自己回答「有人在我之后改过它吗」的那一格。
    """
    return {
        "view_id": view.view_id,
        "revision": view.revision,
        "workspace": view.workspace,
        "owner": view.owner,
        "name": view.name,
        "visibility": view.visibility.value,
        "query": dict(view.query),
        "note": view.note,
        "created_at": view.created_at.isoformat(),
        "updated_at": view.updated_at.isoformat(),
        "content_digest": view.content_digest,
    }


def _compiled_receipt(compiled: CompiledQuery | None) -> dict[str, Any] | None:
    """编译出来的**读法**。``None`` 原样回 ``None``，不回一个空对象。

    空对象与「没有读法」在报告上同形，而它们的处置不同：前者要去查这一版为什么编不出
    来（``unrunnable`` 就在旁边），后者是「这一份视图本来就不带查询」。

    ``predicates`` 每一条**带 ``describe()``**：那一行说法是域里那一个方法给的
    （筛选与拒绝里各要用一次，所以定义在域里），这一层再拼一遍的后果是同一份筛选在
    回执里与在拒绝里读起来不一样。
    """
    if compiled is None:
        return None
    return {
        "subject_kind": compiled.subject_kind.value,
        "subjects": list(compiled.subjects),
        "predicates": [
            {
                "field": predicate.field.value,
                "op": predicate.op.value,
                "value": predicate.value,
                "describe": predicate.describe(),
            }
            for predicate in compiled.predicates
        ],
        "sort": (
            None
            if compiled.sort is None
            else {
                "field": compiled.sort.field.value,
                "descending": compiled.sort.descending,
            }
        ),
        "columns": [column.value for column in compiled.columns],
        "limit": compiled.limit,
        "digest": compiled.digest,
    }


def _unrunnable_receipt(parts: Sequence[Any]) -> list[dict[str, str]]:
    """跑不动的那几处：``part`` 是一个**定位**，``reason`` 是为什么。

    两格都必填，而且这一层不合并它们 —— 把 ``filter[0]`` 与 ``filter[3]`` 合成一句
    「这份查询有几处不行」会让读的人自己去找是哪几处，而「改哪一格」与「重写整份查询」
    的处置完全不同。
    """
    return [{"part": part.part, "reason": part.reason} for part in parts]


def _run_receipt(run: ViewRun) -> dict[str, Any]:
    """一次运行的一行读数。

    ``accounts_read`` 与 ``accounts_distinct`` **两格都在**，而这不是「多给一格」：
    前者的定义是「这一趟从池子里读回来的账号行数」（被筛掉的也算读过），后者的定义是
    「回给你的那些行里有几个不同的 account_id」。同一批视图打到同一个账号上时它们不等，
    而**那个差就是那句验收口径**（「同一个 Account 出现在三个视图里是一个 Account 读了
    三次，不是三行」）。只留前者，那句话读不出来；只留后者，「读了几遍」这件事消失，
    而一个顺手去重的实现在报告上看不出区别。

    ``cells`` 是 ``[[列名, 值], ...]`` 而**不是**一个 dict：dict 会吃掉重复的列名，而
    ``columns`` 是调用方写的（``filter`` 那一族的形状不禁止重复）。少回一格与「本来
    就没有这一列」在报告上同形 —— 那正是这一族最不该有的读法。
    """
    return {
        "view_id": run.view_id,
        "view_revision": run.view_revision,
        "workspace": run.workspace,
        "owner": run.owner,
        "visibility": run.visibility.value,
        "query": dict(run.query),
        "compiled": _compiled_receipt(run.compiled),
        "unrunnable": _unrunnable_receipt(run.unrunnable),
        "ok": run.ok,
        "rows": [
            {
                "account_id": row.account_id,
                "cells": [[column, value] for column, value in row.cells],
            }
            for row in run.rows
        ],
        "accounts_read": run.accounts_read,
        "accounts_distinct": len({row.account_id for row in run.rows}),
        "undecided": run.undecided,
        "dropped": run.dropped,
        "refusal": run.refusal,
    }


# ----------------------------------------------------------------------
# 四条工具
# ----------------------------------------------------------------------
def _put(
    *,
    settings: Settings,
    views: Callable[[], SavedViewStore],
    view_id: str,
    name: str,
    query: dict,
    expected_revision: int | None,
    visibility: str,
    note: str,
) -> dict[str, Any]:
    """存一份或改一份视图。**这一条不要预算、不要权限**（见模块 docstring）。

    ## ``expected_revision`` 没有缺省值

    它必须由调用方写出来，两个取值各是一句话：``null`` =「我认为这个 view_id 在盘上
    还不存在」，数字 =「我在改第 N 版」。给它一个缺省（无论哪个）都会让「我忘了写」
    静默地变成其中一句话 —— 而这两句话的后果相反：一句是「建一条新的」，另一句是
    「覆盖那一版」。``domain/saved_view.py::SavedView.next_revision`` 与
    ``ports/views.py::SavedViewStore.put_view`` 的 docstring 各论证过一次，这里是它在
    工具面上的落点。

    ``visibility`` **没有缺省值**，理由与 ``expected_revision`` 同款而不是「照抄一遍」：
    它是**存在盘上的一条读路径判据**（``run_saved_view`` 那一边唯一的执行器），而一个
    缺省的可见性会让「我忘了写」静默地变成其中一个取值。两个取值的方向相反 ——
    缺省成 ``private`` 会让一次本该共享的视图没人看得见（而它读起来只是「别人没存过
    这个」），缺省成 ``shared`` 会让一条本该私有的视图对全工作区可读。所以由调用方说。

    它收字符串、在这里翻成枚举：翻不了时回一句点名**认识的取值**的话，而不是让
    pydantic 在底下抛一行 ``value is not a valid enumeration member``。
    """
    try:
        chosen = ViewVisibility(visibility)
    except ValueError as exc:
        raise ToolError(
            f"visibility 是 {visibility!r}，它不是一个可见性"
            f"（认识的：{[item.value for item in ViewVisibility]}）。\n"
            "  一个认不出的可见性被静默当成 shared，会让一条本该私有的视图对全工作区"
            "可读 —— 而那份回执看起来完全正常。"
        ) from exc
    principal = _actor()
    try:
        write = _put_view(
            store=views(),
            view_id=view_id,
            workspace=settings.profile,
            owner=principal.name,
            name=name,
            query=query,
            expected_revision=expected_revision,
            visibility=chosen,
            note=note,
        )
    except (ConfigError, SavedViewConflictError) as exc:
        # **这一族最要紧的两条拒绝，而它们都不是 ``StoreError``。**
        #
        # * ``ConfigError`` —— 本次调用说「我在改第 N 版」，而盘上这个 id 一版都没有
        #   （``_require_view``）。它不是崩溃：调用方要据它换 ``expected_revision``
        #   或换 id。
        # * ``SavedViewConflictError`` —— 三个成因一个类型：``(workspace, owner, name)``
        #   上已经有**另一条**当前版、手上的基准过期、或者这条视图的 owner 不是本次
        #   调用的人。三条的报文各自都说清了该改哪一格（``ports/views.py`` 逐字），
        #   所以这里原样交出去，不另拼一句。
        #
        # ## 不接它们会发生什么（实测过）
        #
        # 它们**逃出去**，而逃出去之后调用方读到的是一句光秃秃的
        # ``Error executing tool put_saved_view``：存储层原本给的那句话
        # （「这个名字下已经有一条当前版：view_id=… 第 n 版」）**一个字都不在**。
        # ``_shared.store_failure`` 的 docstring 对 ``StoreError`` 论证过同一件事
        # （「不接它会发生什么（实测过）」那一节），这一条是它在 B24 这半边的兑现：
        # 只接 ``StoreError`` 是不够的，因为这一族最深的两条判据住在**它的兄弟**上，
        # 而「重名要失败」与「基准过期要失败」正是这一片的两条承重点在工具面上的形状。
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise store_failure(exc) from exc
    return _put_receipt(write)


def _put_receipt(write: ViewWrite) -> dict[str, Any]:
    """一次视图写入的回执。``revision_written`` 那一格是这一段话的主体。

    ``None`` 是「**没有新版本落盘**」：盘上当前那一版的内容与这一次要写的一字不差。
    它与「刚落了一版」在报告里是两句不同的话——「你刚才那次成功了」与「你第二次点的
    时候什么都没发生」。所以这一层把那一格**连同它是什么意思**一起给出去：一个只回
    ``revision_written`` 的回执，读的人要自己去端口 docstring 里查 ``None`` 是哪一边。
    """
    written = write.revision_written
    if written is None:
        note = (
            f"盘上当前那一版（第 {write.view.revision} 版）的内容与这一次要写的"
            "一字不差，所以没有新版本落盘；回给你的**就是盘上那一版**，"
            "不是这一次提交的对象。"
        )
    elif write.view.revision == 1:
        note = f"这个 view_id 在盘上原本没有任何一版，这一版（第 {written} 版）是刚建出来的。"
    else:
        note = f"这一版（第 {written} 版）刚落盘；上一版仍在盘上，改名前它叫什么读得到。"
    return {
        "view": _view_receipt(write.view),
        "revision_written": written,
        "note": note,
    }


def _get(*, views: Callable[[], SavedViewStore], view_id: str) -> dict[str, Any]:
    """读一份视图：**原样文档与编译读法并排**（见 :func:`_view_receipt`）。

    两样在一次读里一起取（``application/views.py::get_saved_view`` 逐字）：分两次读会
    让它们来自两个不同的快照，于是「我写的过滤」与「编译器的读法」对不上的那一格，
    读起来像一次改写而不是一次并发。
    """
    try:
        card: SavedViewCard = _get_view(store=views(), view_id=view_id)
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise store_failure(exc) from exc
    return {
        "view": _view_receipt(card.view),
        "compiled": _compiled_receipt(card.compiled),
        "unrunnable": _unrunnable_receipt(card.unrunnable),
    }


def _list(
    *, settings: Settings, views: Callable[[], SavedViewStore], owner: str | None
) -> dict[str, Any]:
    """本工作区的视图。``owner=null`` = **不过滤**（不是「只看我的」）。

    那不是随手选的缺省：一个「默认只看自己的」实现会让同一条工作区里别人存的视图从
    报告里消失，而消失与「他没有存过」同形 —— 而 A/B 两条路线各自的负责人正是靠看见
    对方的视图来对齐的。要看自己的，显式传自己的名字。

    清单里每一行带着它自己的**版本与最后变动**（``updated_at``）：名字不是身份，
    ``view_id`` 才是，而「我昨天看的那个名字下面今天是什么」只有这两格答得出来。
    """
    try:
        rows = _list_views(store=views(), workspace=settings.profile, owner=owner)
    except StoreError as exc:
        raise store_failure(exc) from exc
    return {
        "workspace": settings.profile,
        "views": [_view_receipt(row) for row in rows],
        "count": len(rows),
        "owner_filter": owner,
    }


def _run(
    *,
    views: Callable[[], SavedViewStore],
    pool: Callable[[], AccountPoolStore],
    view_ids: Sequence[str],
) -> dict[str, Any]:
    """跑若干份存下的查询并回行。**零 provider、零预算、零 Run。**

    ## 为什么是 ``view_ids``（一个清单）而不是一个 ``view_id``

    验收口径逐字是「同一个 Account 出现在**三个视图里**是一个 Account 读了三次」——
    它说的是三份视图放在一起看时的那个读数。一次只跑一份的话，那句话只能靠调用方跨
    三次调用自己数，而**回执上没有一处**能同时看到两份视图各自读了什么。清单让它成为
    一份回执里的一格。

    清单里一份就是一串 ``view_id`` 各是一个元素 —— 跑一份视图就是只写一项。**不许为空**：
    一份零项的回执（``runs: []``、``accounts_read: 0``）与「这些视图一条都没匹配上」
    在报告上同形，而它其实什么都没跑。

    ``accounts_read`` 是**按视图各读一遍池子**累加的（``run_saved_views`` 逐字），所以
    同一个账号出现在三份视图里时它是 3 —— 一个「已经读过就别再读了」的优化在这里恰好
    把判据删掉。要去重就去重 ``rows[].account_id``：那是调用方的决定，而读了几遍是
    这一层的读数。

    ``actor`` 取自传输层（:func:`_actor`），因为 ``private`` 那一条可见性判据要靠它。
    """
    ids = [str(item) for item in view_ids]
    if not ids:
        raise ToolError(
            "view_ids 是空的。\n"
            "  一份零项的回执读起来像「这些视图一条都没匹配上」—— 而它其实什么都没跑，"
            "一个 view_id 都没有验证过。要跑哪几份就写哪几份。"
        )
    blank = [item for item in ids if not item.strip()]
    if blank:
        raise ToolError(
            f"view_ids 里有空白的项（{blank!r}）：一个空串读不成 view_id，而它会以"
            "「这个 id 在盘上不存在」的形状回来 —— 那与「你写错了 id」是两件事。"
        )
    principal = _actor()
    try:
        runs = _run_views(
            views=views(), pool=pool(), view_ids=ids, actor=principal.name
        )
    except ConfigError as exc:
        # 可见性那一条（``private`` 且 owner 不是本次 actor）走这里。它是**结果**，
        # 不是崩溃：调用方要据此换一条视图或去问它的负责人。
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise store_failure(exc) from exc
    receipts = [_run_receipt(run) for run in runs]
    return {
        "runs": receipts,
        "summary": {
            "views": len(receipts),
            "rows": sum(len(item["rows"]) for item in receipts),
            "accounts_read": sum(item["accounts_read"] for item in receipts),
            "accounts_distinct": len(
                {
                    row["account_id"]
                    for item in receipts
                    for row in item["rows"]
                }
            ),
            "undecided": sum(item["undecided"] for item in receipts),
            "dropped": sum(item["dropped"] for item in receipts),
            "runnable": sum(1 for item in receipts if item["ok"]),
            "refused": sum(1 for item in receipts if not item["ok"]),
        },
        "note": (
            "accounts_read 是这一趟从池子里读回来的账号行数（同一个账号出现在三份视图里"
            "就是 3）；accounts_distinct 是回给你的那些行里有几个不同的 account_id。"
            "两者不等正是「一个 Account 读了三次，不是三个账号」这句话的读数 —— "
            "这里没有去重，去重是调用方在 rows[].account_id 上的决定。"
        ),
    }


def views_handlers(
    *,
    settings: Settings,
    views: Callable[[], SavedViewStore],
    pool: Callable[[], AccountPoolStore],
) -> dict[str, Handler]:
    """视图这一族**已接线**的那四条，按工具名索引。

    Args:
        settings: 配置。**本部署的工作区就是它的 ``profile``** —— 这一次调用的
            ``workspace`` 从它来，不从参数来（见模块 docstring 那一节）。
        views: 视图存储的**工厂**（``sqlite3`` 的连接不能跨线程）。**必填、没有缺省**：
            一个可以为 ``None`` 的接线口会让「没有视图存储」与「这个工作区一份视图都
            没有」在报告上同形，而两者的处置分别是「去接线」与「去存一份」。
        pool: 账号池的**工厂**。同样必填，理由同款 —— 而且这一格更硬：``run_saved_view``
            读的就是它，一个缺省的空池会让每一份视图都跑出「零行」，那是一条**看起来
            正常的**结果。

    Returns:
        四条工具。**没有任何一条读账本、读场景、读 Run** —— 见模块 docstring。
    """

    def put_saved_view(
        view_id: str,
        name: str,
        query: dict,
        expected_revision: int | None,
        visibility: str,
        note: str = "",
    ) -> dict[str, Any]:
        return _put(
            settings=settings,
            views=views,
            view_id=view_id,
            name=name,
            query=query,
            expected_revision=expected_revision,
            visibility=visibility,
            note=note,
        )

    def get_saved_view(view_id: str) -> dict[str, Any]:
        return _get(views=views, view_id=view_id)

    def list_saved_views(owner: str | None = None) -> dict[str, Any]:
        return _list(settings=settings, views=views, owner=owner)

    def run_saved_view(view_ids: list[str]) -> dict[str, Any]:
        return _run(views=views, pool=pool, view_ids=view_ids)

    return {
        "put_saved_view": put_saved_view,
        "get_saved_view": get_saved_view,
        "list_saved_views": list_saved_views,
        "run_saved_view": run_saved_view,
    }

"""一轮 Run 收盘时，**要人跟进的活儿**从哪来、怎么落、以及它今天只覆盖一格。

§B17 末尾那句清单里的「Action/Review」是一格的两半：Review 那一半住在
``domain/review.py``（``case_id_for`` 的产物，由 ``application/review.py`` 打开），
**Action 那一半**就是本模块 —— 它把这一轮产出的路线变成 ``ActionItem``
（``domain/action_item.py``，§5.4）。``RunDelta.actions`` 那一格由它写回来。

## 哪一格今天有生产者：**只有「路线 → 核实转介联系人」**

``ActionType`` 有四个成员（§5.4 逐字），而今天在这条链上**只有一条**映射做得出：

============================  ==========================================
这一轮产出了什么               开哪一件工作
============================  ==========================================
一条**路线**（``delta.routes``）  ``VERIFY_REFERRAL_CONTACT``，挂在
                              ``ScopeKind.ROUTE`` 上，``scope_id`` 是那条路线
============================  ==========================================

三条理由，逐条都不是「顺手」：

* **路线的主语就是那个联系方式。** 一条路线的 id 是内容寻址的，原像是
  ``(workspace, account_id, route_email_key(email))``
  （``domain/route_records.py::canonical_route_key``）—— 也就是说，一条路线**本身
  就是一个邮箱地址在盘上的身份**。「核实转介联系人」正是对这份内容做的一件事，
  所以它挂在这一条路线上，而不是挂在账号或整个 Run 上。
* **这句话在仓里已经被写下过一次。** ``errors.py::OpenActionItemExistsError``
  逐字转述 ``adapters/sqlite/db.py`` 那条 partial unique index 的注释：「没有它，
  每个 Run 都会新增一条「核实转介联系人」，而报告上看起来只是「待办多了一些」」。
  本模块就是那句话里那个**生产者**：它每轮真的会去开一条，而那条索引挡住第二条。
* **它是**一个**业务映射，不是一个兜底分支。** 一份 Delta 里没有路线，本模块就
  **一件都不开** —— 见 :func:`followups_for` 的判据与 ``tests/test_run_followups.py``
  里那条反例。一个「反正每轮都开一条」的实现会让上面那句话反过来成立。

## 另外三条今天**开不出来**，而这是一条记在册的缺口

``FILL_RESPONSIBILITY`` / ``REVIEW_CONFLICT`` / ``APPROVE_DRAFT`` **不接生产线**，
因为全仓找不到「哪一种变化 → 这一类工作」的生产者权威。照 §7 的纪律，缺口写在明处
而不是补一条猜出来的映射 —— 一个猜出来的映射此后会被读成「这里已经判过」。三条
理由里点名的都是**本仓已有的载体**，这不是风格：``application/inputs.py`` 的
``_no_producer_reason`` 那一格逐字要求「理由必须点名本仓已经**有的**载体
（``marketops/`` 下的模块路径），而不只是说什么东西「不在本仓库里」」—— 一句
「本仓没有 X」会在 X 被造出来的那天变成一句错话，而它印在一份要人照着做的报告里。

* ``REVIEW_CONFLICT``：这一格**仓里明文宣布过没有生产者**。
  ``domain/review.py`` 的 ``ReviewReason.EVIDENCE_CONFLICT`` 那一格逐字：
  「路线级的 has_conflict 判定发生在**研究 worker 的结算那一趟**……而它今天只有
  一个读者：``marketops/application/pipeline.py`` 里 ``metrics.conflicts`` 那个
  计数，没有一处 MCP 工具读它。……所以这一格今天**开不出待办**，而这句话是写下来
  的：报告里 EVIDENCE_CONFLICT 的 0 是「没有生产者」，不是「量过了，是零」」。
  （那个读者逐字在 ``application/pipeline.py`` 的 ``metrics.conflicts += 1``。）
* ``FILL_RESPONSIBILITY``：``responsibility`` 那一格**有**生产者，而它是个**人** ——
  ``adapters/mcp_server/product_loop.py::remember_responsibility`` 把一条责任关系
  手写进表，``application/relations.py`` 收下它并如实记着它「不是判出来的」。
  缺的是**判定**那一侧：全仓没有一处会因为「谁负责」这一格缺席而停下来，于是没有
  「该补了」这条结论 —— 没有结论，就没有开待办的那句话。
* ``APPROVE_DRAFT``：草稿那一族**在仓里**（``domain/outreach.py`` 的
  ``OutreachDraft`` / ``DraftRef``），等它的人那一环也在
  （``adapters/mcp_server/outreach.py::authorize_wave``；
  ``WaveState.PREPARED_PENDING_AUTHORIZATION`` 就是「等人批」那一步）。它接不上
  收盘这一趟：那一族走的是**另一条链**（``draft_outreach`` → ``prepare_wave`` →
  ``authorize_wave`` → ``send_wave``），而本轮 Delta 的九格里没有一格收草稿
  （``artifacts`` 只收引用，而草稿的 id 空间没有一格定义过 —— 见
  ``domain/run_delta.py`` 第六节）。要接它，正确的落点是那条链自己的「等人授权」
  那一步，不是这里。

搜索过的地方与读数写在交回报告里（第三节），不在这里再抄一份。

## 幂等：判据是那条 partial unique index，读回只用来点亮 id

同一个主体上的同类未结待办只应有一条，判据**不是**本模块里的一次查询，而是
``(workspace, action_type, scope_ref)`` 上那条 partial unique index
（``ports/views.py::ActionItemStore.create`` 逐字：「判据落在那条 partial unique
index 上，**不是**「写之前先查一下」」）。所以这里的形状是：

1. 铸一个**新的** ``action_id``（``domain/action_item.py::new_action_id``），
2. ``create`` 一次；盘上已经有一条未结的时它抛 ``OpenActionItemExistsError``，
3. **然后**才读回盘上那一条，取它的 ``action_id``。

第 3 步**不参与判定**（一次「查到了就跳过」的实现写出来长得一样，而它在两个写者之间
会放行两条）；它在这里有一个具体得多的用途：**重放要算出同一批 id**。
``ports/runs.py::stopping_outcome`` 比的是整份 Delta 的指纹，而此刻盘上那一份 Delta
的 ``actions`` 正是第一次收盘读回来的那些 id —— 读不回同一批，一次正常的重放会被
读成「同一轮以两种结局结束过」（``RunStateError``）。这也是 ``action_id``
**必须**是随机的、不能内容寻址的原因（那一条写在 ``new_action_id`` 的 docstring 里）。

## 次序：开待办在**停表之前**，以及那个窗口

``close_run`` 里的次序是「两道闸 → 开待办 → 停表」。反过来（先停表再开）会留下一份
**点着 ``actions`` 而按着那些 id 一个都读不到**的 Delta —— 那正是这一格要防的假回执。

代价如实记在这里：``stop`` 若在**已经开完待办之后**才拒（唯一还够得着的是
``RunStateError``：这一轮已经以另一套产出结束过），那几条 ``create`` 已经落盘了。
它不是「半个 Run」：一件待办是一条独立的、有人要去做的活儿，而它的重复创建被那条
索引挡住 —— 拿正确的 Delta 重做一次，同一主体上不会再长出第二条。

## 调用方声明的那一串也要读得回来

``RunDelta.actions`` 由收盘这一趟写，但一份从 ``get_run`` 读回来的 Delta
（那上面**带着** ``actions``）也要能原样交回来。所以调用方声明的那一串会先逐条
``ActionItemStore.get`` 问一遍：读不到就拒（``RunReconciliationError``）。跨工作区
那一条不在本层翻 —— 存储自己会拒（``get`` 里那道 ``owned`` / ``_own``），而
「不属于这个部署」与「没有这一条」被读成同一件事，正是那两处存在的理由。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from ..domain.action_item import (
    ActionItem,
    ActionType,
    ScopeKind,
    new_action_id,
    require_creatable,
    scope_ref,
)
from ..domain.base import StrictModel
from ..domain.errors import OpenActionItemExistsError, RunReconciliationError
from ..domain.run_delta import RunDelta
from ..ports.views import ActionItemStore
from .merge import ordered_union

__all__ = [
    "Followup",
    "followups_for",
    "open_followups",
]

#: 这一轮产出的**路线**上要人做的那类工作。见模块 docstring 第一节。
ROUTE_FOLLOWUP_TYPE = ActionType.VERIFY_REFERRAL_CONTACT


class Followup(StrictModel):
    """一条「这一轮产出上要人做的事」：挂在**哪一个主体**上、是哪一类工作。

    它**不含** ``action_id``：id 是落盘那一刻才铸出来的（``new_action_id``），而
    这一格说的是「该建一件什么」。合成一个模型会让「该建什么」在两次运行之间看起来
    是同一个答案 —— 而**建不建得成**由盘上那条索引回答，不由这里答。
    """

    scope_kind: ScopeKind
    scope_id: str
    action_type: ActionType


def followups_for(routes: Iterable[str]) -> tuple[Followup, ...]:
    """这一轮产出的路线 → 要开的那些活儿。**纯函数，不碰盘。**

    保序、不去重 —— 两件事各自有理由而它们不是同一条：顺序要留着（``close_run``
    按这个次序把 id 写回 Delta，而重放要算出同一串），去重不做（``routes`` 那一格
    自己那条校验器已经拒了重复；在这里悄悄去一次，会让「这一轮到底产出了几条路线」
    读不出来 —— 与 ``RunDelta._ids_are_distinct_and_not_blank`` 逐字同一条纪律）。

    一条路线一件工作，不是「一批一件」：``scope_ref`` 是
    ``route#<route_id>``，而那条唯一索引盖的是 ``(workspace, action_type,
    scope_ref)`` —— 按批开一件，会让「这条路线没人核实过」在盘上问不出来。

    今天 ``routes`` 那一格**没有子类**（``ContactRoute`` 上没有「转介」这一格），
    所以每一条路线都开同一类工作。哪一天路线分出了子类，正确的动作是改这一条映射
    与它的判据，不是在这里加一个自由的类型串（``ActionType`` 的 docstring 逐字
    说过这件事）。
    """
    return tuple(
        Followup(
            scope_kind=ScopeKind.ROUTE,
            scope_id=route_id,
            action_type=ROUTE_FOLLOWUP_TYPE,
        )
        for route_id in routes
    )


def open_followups(
    *,
    items: ActionItemStore,
    workspace: str,
    run_id: str,
    delta: RunDelta,
    at: datetime,
) -> tuple[str, ...]:
    """把这一轮的跟进活儿放进池子，返回 ``RunDelta.actions`` 该记的那一串 id。

    ``followups_for(delta.routes)`` 逐条开；盘上已经有那条未结的时**读回它的 id**
    （见模块 docstring 第三节），所以在同一个主体上重放不会新增第二件待办。

    返回的是 ``ordered_union(调用方声明的那一串, 这一轮开出来/读回来的那一串)``：
    并集、保序、去重（``application/merge.py::ordered_union``）。这个并集是
    ``get_run`` → ``close_run`` 那条往返的一部分 —— 从盘上读回来的 Delta 带着
    ``actions``，而它交回来时**不能**被丢掉（丢掉的话指纹与盘上那一份不同，
    ``stop`` 会把一次重放读成一次冲突）。

    Raises:
        RunReconciliationError: 调用方声明的那一串里有一个 id 在这只跟进池里读不到。
            见模块 docstring 最后一节。
        OpenActionItemExistsError: 插入被那条索引拒了，而**读回也读不出唯一那一条**
            （读不出时原样抛出去：那句话里已经点了名，而「读不回来」本身是要人去看的
            事，不是能被绕过去的事）。
        StoreError / EvidenceError: 存储事故；跨工作区那一条由存储自己拒（见模块
            docstring 最后一节）。
    """
    _require_declared_are_readable(items, workspace=workspace, declared=delta.actions)
    opened = tuple(
        _open_one(items, workspace=workspace, run_id=run_id, one=one, at=at)
        for one in followups_for(delta.routes)
    )
    return tuple(ordered_union(delta.actions, opened))


def _open_one(
    items: ActionItemStore,
    *,
    workspace: str,
    run_id: str,
    one: Followup,
    at: datetime,
) -> str:
    """开一条，或者（盘上已经有那条未结的时）读回它的 id。见模块 docstring 第三节。"""
    item = ActionItem.opened(
        action_id=new_action_id(),
        workspace=workspace,
        scope_ref_value=scope_ref(one.scope_kind, one.scope_id),
        action_type=one.action_type,
        reason=_reason(run_id, one),
        at=at,
    )
    require_creatable(item)
    try:
        return items.create(item).item.action_id
    except OpenActionItemExistsError:
        existing = _rival(items, workspace=workspace, one=one)
        if existing is None:
            # 那条索引说已经有一条未结的了，而按 (类型, 主体) 读不回来**唯一**的一条。
            # 原样抛出去：它的消息里已经点名了盘上那一条，而这里读不回来的原因是别的
            # —— 一个「读不回就当新的一条收下」的实现会在同一主体上留下两条未结的。
            raise
        return existing


def _rival(
    items: ActionItemStore, *, workspace: str, one: Followup
) -> str | None:
    """盘上那条**未结**的同主体同类待办的 ``action_id``；读不出唯一的一条时 ``None``。

    走端口（``items``）而不是自己写 SQL：那一条索引的世界只有存储那一层看得见，
    而这里问的是「它刚才拒的是哪一条」。**它不参与判定**（见模块 docstring 第三节）：
    走到这里时插入**已经**被拒过了。
    """
    ref = scope_ref(one.scope_kind, one.scope_id)
    rivals = tuple(
        found.action_id
        for found in items.items(workspace=workspace, open_only=True)
        if found.action_type == one.action_type and found.scope_ref == ref
    )
    if len(rivals) == 1:
        return rivals[0]
    return None


def _require_declared_are_readable(
    items: ActionItemStore, *, workspace: str, declared: Sequence[str]
) -> None:
    """调用方声明的那一串 id 必须逐条读得回来。见模块 docstring 最后一节。

    这是本格与 ``routes`` / ``evidence`` / ``artifacts`` / ``reviews`` / ``usage``
    那五格**唯一的**不同之处，而它做得到只是因为收盘这一趟手里**就是**那只跟进池：
    ``reconcile_run_delta`` 判不了那五格里的 id，因为它拿不到五个存储
    （它自己的 docstring 第四节点名了不做那种缝合）。一条点着一个不存在的 action 的
    Delta，在 ``get_run`` 上读起来与一条真的产出过待办的 Delta 逐字相同。
    """
    for action_id in declared:
        if items.get(action_id) is None:
            raise RunReconciliationError(
                f"这份 Delta 的 actions 里点了一个读不到的 action_id：{action_id!r}。\n"
                "这一格记的是**挂在盘上那些待办上的 id**，所以每一个都要读得回来 —— "
                "一个读不回来的 id 在报告上与一条真的产出过待办的记录逐字相同，而"
                "它下面什么都没有。\n"
                "正确动作是把它从 actions 里去掉（它要么是抄错了，要么属于另一个"
                "工作区或另一本库 —— 那两种都会在这里被读成「读不到」，而它们的处置"
                "与「抄错了一个 id」不同：去看它是从哪来的）。"
            )


def _reason(run_id: str, one: Followup) -> str:
    """这一件为什么会有。**必填**（``ActionItem.reason``），所以它由判据算出来。

    它说的是**这一轮做下了什么**（产出了这条路线），不是「这个联系人没有核实过」——
    后者本层判不了（一件核实做完之后，同一条路线可以再被产出一次）。
    """
    return (
        f"Run {run_id} 产出了路线 {one.scope_id}，它要有人去核实转介联系人。\n"
        "一条路线的身份就是那个联系方式本身（它的 id 由 workspace / 账号 / 邮箱算出来，"
        "``domain/route_records.py::canonical_route_key``），所以这件工作挂在这一条"
        "路线上。"
    )

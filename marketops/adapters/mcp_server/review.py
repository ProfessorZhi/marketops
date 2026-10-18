"""§12 的**收件箱**工具：读待办、认领、裁决、批准，以及两版之间的影响预览。

``domain/review.py`` 落下了那两件对象与它们之间的判据，``ports/review.py`` 落下了
它的持久化，``application/review.py`` 落下了编排（开案、认领、裁决、批准、那道闸门），
``adapters/mcp_server/tools.py`` 落下了**生产者**（受理时被身份判据拒掉的那几行落成
待办）。本模块是第五块：**调用点**。少了它，那四块都在，而「人从哪里看见这一件事、
从哪里答它」这句话没有落点 —— 一件没人读得到的待办与一件不存在的待办，在报告上
完全同形。

## 七条工具，三组角色

* **读**（``list_review_cases`` / ``get_review_case``）—— ``_REVIEW_READ``：控制器
  与授权者。给授权者读，是因为他要批准的东西不能是盲批（那一格的理由逐字写在
  ``security/catalog.py`` 的 ``_REVIEW_READ`` 上）。
* **控制**（``get_review_flow`` / ``claim_review_case`` / ``resolve_review_case`` /
  ``get_scene_impact``）—— ``_CONTROL``。
* **批准**（``approve_policy_change``）—— ``_AUTHORIZE``，且**不并入** ``_CONTROL``：
  §9 要的是另一个人，而「另一个人」在角色这一维上的执行方式就是**两组角色不相交**。

角色本身**不在这里判**：那是 ``security/catalog.py`` 的事，一处定义。

## 认领为什么不是 ``resolve`` 的一种

两件不同的动作：认领改的是**当前状态**（谁在做），裁决追加一条**历史**并推进状态。
两者共用同一条并发判据（``expected_version``）是刻意的 —— 两个人同时领走一件与
两个人同时裁决一件，是同一个失败（后写的把前一次整个吃掉，而两边都以为自己成功了）。

## 人从哪里来

``by`` **不是参数**。三个写动词的当事人一律取自传输层已确立的主体
（``guard.current_principal``），理由与 ``authorize_wave`` / ``control_scene`` 逐字
相同：一个能传的 ``by`` 让调用方可以写下别人的名字，而那条记录看起来完全正常。

``roles`` 同理取自**令牌**，而它答的是「这个人当年有没有资格做这件事」——
事后回看时令牌已经不可复现，这一格是唯一读得到的依据（``HumanResolution`` 的判据
逐字：roles 为空要被拒）。

## 时间戳不是参数

``at`` 一律取服务器时钟。一个可以传的时刻，让「谁先提的、谁后批的」变成调用方的
输入 —— 而那个先后正是 ``inbox_flow`` 判「最后一个事件是什么」的依据。

## ``resolution_id`` 由这一层算，而它**不是**内容寻址的

``ports/review.py`` 逐字：这个端口**不去重**，它记的是一件已经发生的事。所以
:func:`_resolution_id` 把**这一次事件**算进去 —— 哪件案件、什么动作、谁做的、从哪一版
做的、什么时候 —— 而时间戳在里面是承重的：同一件案件「放下 → 重开 → 再放下」是三次
不同的事件，只按内容算会让第三次撞上第一次的主键。

撞主键因此是一次**真实的重复写入**（``adapters/sqlite/review.py`` 用裸 ``INSERT``
就是为了让它报出来），而不是一次重放。而真正挡住「一次决定被做两遍」的是
``expected_version``：第一次写完之后案件的版本就变了，第二次提交带着旧版本，在存储层
被整次拒绝。**两道都在**，挡的是两件不同的事（同一件事被记成两条 / 被做两次）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ...application.review import (
    approve_policy_change as approve_use_case,
)
from ...application.review import (
    claim_case,
    flow,
    impact,
    resolve_case,
)
from ...config import Settings
from ...domain.errors import ConfigError, ReviewConflictError, StoreError
from ...domain.review import (
    PENDING_STATES,
    POLICY_APPROVAL_OUTCOMES,
    HumanResolution,
    ResolutionOutcome,
    ReviewCase,
    ReviewFlow,
)
from ...domain.util import check_canonical_id, sha256_text, to_utc, utc_now
from ...ports.review import ReviewCaseStore
from ...ports.scenes import SceneStore
from ...security.catalog import Principal
from .guard import current_principal
from ._shared import readable as _readable
from .tools import Handler, require_scene

__all__ = [
    "MAX_CASES",
    "RESOLUTION_ID_PREFIX",
    "review_handlers",
]

#: ``list_review_cases`` 一次最多回多少件。与 ``list_scenes`` 同一条：封顶的缺省
#: **会被报出来**（``truncation``），而不封顶的缺省会让响应大小由库决定。
#:
#: 这个数比别的列表小，而那不是随手挑的：收件箱是一件一件**手工**处理的东西，
#: 它的规模是「一个人一周要看的件数」，不是「一个池子的行数」。
MAX_CASES = 200

#: 一条人的决定的 id 前缀。与 ``domain/review.py`` 的 ``CASE_ID_PREFIX``（``rc``）
#: 同一族形状，但**定义在这里**：端口逐字把这个 id 交给调用方（它不是内容寻址的），
#: 所以它的形状是这一层的事 —— 领域那一侧只声明「有这么一格」。
RESOLUTION_ID_PREFIX = "rr"

#: 三个写动词共用的那句话（模块 docstring 的「人从哪里来」）。写一次、三处引用 ——
#: 三处各写一份的形态是其中一处不再说清「为什么不取自参数」，而那一处的调用方
#: 会自己加一个 ``by`` 形参。
_WHO = (
    "当事人取自传输层已认证的主体（Bearer 令牌），**不取自任何参数** —— "
    "一个能传的 by 让调用方可以写下别人的名字，而那条记录看起来完全正常。"
)


def review_handlers(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    review: Callable[[], ReviewCaseStore] | None,
) -> dict[str, Handler]:
    """本构建的收件箱处理器，按工具名索引。

    Args:
        settings: 配置。``get_scene_impact`` 用它核「这次说的是不是本部署服务的那个
            市场」（``tools.require_scene``，与 ``product_loop`` 那三条场景工具同一条）。
        scenes: 场景存储的**工厂**。``resolve_review_case`` 用它核「一条提议挂起的
            那一版真的存在」（``application/review.py::resolve_case`` 逐字要求它
            **必填**：一个可以省略它的调用点，会在「提议挂的是哪一版」这件事上静默地
            什么都不查），``get_scene_impact`` 用它算两版之间的影响。它与
            ``product_loop`` 收到的是**同一个**工厂：两处各建一份的形态是「提案核的是
            A 库的版本、激活的是 B 库的场景」。
        review: 收件箱存储的**工厂**（``sqlite3`` 的连接不能跨线程）。``None`` 表示
            这个服务实例没有接收件箱 —— 那时**七条工具一条都不注册**，而不是注册成
            返回空列表的实现。空的收件箱与没有收件箱在报告上必须分得开：前者的处置是
            「现在没有待办」，后者是「去接线」，而一个恒回 ``[]`` 的实现让两者同形。
            这与 ``decisions.py`` 在 ``records=None`` 时的取向不同，那是刻意的 ——
            那一边工具照常接线并回一句说得清理由的拒绝，因为**判定本身**还在（只是
            存不下来）；这一边没有存储就**没有待办可读**，工具在不在就是那件事本身。
    """
    if review is None:
        return {}

    def list_review_cases(limit: int | None = MAX_CASES) -> dict[str, Any]:
        return _list_cases(review=review, limit=limit)

    def get_review_case(case_id: str) -> dict[str, Any]:
        return _get_case(review=review, case_id=case_id)

    def get_review_flow(window_start: str = "", window_end: str = "") -> dict[str, Any]:
        return _get_flow(review=review, window_start=window_start, window_end=window_end)

    def claim_review_case(case_id: str, expected_version: int) -> dict[str, Any]:
        return _claim(review=review, case_id=case_id, expected_version=expected_version)

    def resolve_review_case(
        case_id: str,
        outcome: str,
        answer: str,
        expected_version: int,
        proposed_version: int | None = None,
    ) -> dict[str, Any]:
        return _resolve(
            scenes=scenes,
            review=review,
            case_id=case_id,
            outcome=outcome,
            answer=answer,
            expected_version=expected_version,
            proposed_version=proposed_version,
        )

    def approve_policy_change(
        case_id: str, answer: str, expected_version: int
    ) -> dict[str, Any]:
        return _approve(
            review=review,
            case_id=case_id,
            answer=answer,
            expected_version=expected_version,
        )

    def get_scene_impact(
        from_version: int, to_version: int, scene_id: str | None = None
    ) -> dict[str, Any]:
        return _impact(
            settings=settings,
            scenes=scenes,
            scene_id=scene_id,
            from_version=from_version,
            to_version=to_version,
        )

    return {
        "list_review_cases": list_review_cases,
        "get_review_case": get_review_case,
        "get_review_flow": get_review_flow,
        "claim_review_case": claim_review_case,
        "resolve_review_case": resolve_review_case,
        "approve_policy_change": approve_policy_change,
        "get_scene_impact": get_scene_impact,
    }


# ----------------------------------------------------------------------
# 读
# ----------------------------------------------------------------------
def _case_view(case: ReviewCase, *, with_history: bool = False) -> dict[str, Any]:
    """一件待办的投影。**两处共用一份**，所以「列表里那一行」与「按 id 读回来的
    那一件」逐键相同 —— 两份投影漂移的那天，同一条 ``case_id`` 在两个工具里看起来
    是两件事，而这是它最不该有的样子。
    """
    view: dict[str, Any] = {
        "case_id": case.case_id,
        "workspace": case.workspace,
        "scene_id": case.scene_id,
        "reason": case.reason.value,
        "subject_ref": case.subject_ref,
        "affected_refs": list(case.affected_refs),
        "state": case.state.value,
        "claimed_by": case.claimed_by,
        "queue": case.queue,
        "options": [item.value for item in case.options],
        "recommendation": (
            None if case.recommendation is None else case.recommendation.value
        ),
        "policy_version": case.policy_version,
        "proposed_version": case.proposed_version,
        "version": case.version,
        "opened_at": case.opened_at.isoformat(),
        "updated_at": case.updated_at.isoformat(),
        "closed_at": None if case.closed_at is None else case.closed_at.isoformat(),
        # 合并过的那一件**指向谁**。它必须读得到：一件被合并掉的待办与一件从来没
        # 存在过的待办，在只有状态列的报告上是同一个样子。
        "merged_into": case.merged_into,
        # 「这一件现在还算未决吗」由 :data:`PENDING_STATES` 算出来，**不由读的人按
        # state 猜**：那三个成员是一个闭集，猜的那一份会在加一个状态时静默答错。
        "outstanding": case.state in PENDING_STATES,
    }
    if with_history:
        # ``evidence_ids`` 只在按 id 读时给：列表里每一行都带上它，会让一份两百行
        # 的清单把证据引用一并展开，而清单要回答的是「现在有哪几件」。
        view["evidence_ids"] = list(case.evidence_ids)
    return view


def _resolution_view(row: HumanResolution) -> dict[str, Any]:
    """一条人的决定。``roles`` 逐条给出：它答的是「这个人当年有没有资格做这件事」，
    而令牌今天已经不可复现 —— 省掉它，等于把那条判据的唯一依据从记录里抹掉。
    """
    return {
        "resolution_id": row.resolution_id,
        "case_id": row.case_id,
        "outcome": row.outcome.value,
        "answer": row.answer,
        "by": row.by,
        "roles": list(row.roles),
        "expected_version": row.expected_version,
        "proposed_version": row.proposed_version,
        "at": row.at.isoformat(),
    }


_CASE_NOTE = (
    "state 的五格是两族：open / claimed / awaiting_approval 是**未决**（outstanding "
    "为 true），resolved / dismissed 是**关掉**。claimed 与 open 分开是有用的："
    "「有没有人在做这一件」与「有没有人做过决定」是两件事。\n\n"
    "options 与 recommendation 来自**这一个原因**那一张表（domain.review.INTAKE），"
    "不是这一层算的 —— 同一个原因在两个入口下给人两套选项这件事，只有并排看才看得出来。\n\n"
    "关掉的那几件也在列表里：一件被答复过的问题与一件从来不存在的问题必须分得开，"
    "而只列未决的话它们同形。\n\n"
    "version 是**乐观锁序号**（认领与裁决各 +1），不是策略版本 —— 后者是 "
    "policy_version。两个名字不一样，因为它们是两种东西：前者是你提交时那一格 "
    "expected_version 的来路，后者是「这一件当时是按哪一版规则判的」。\n\n"
    "affected_refs 是**开案那一刻**受影响对象的快照（§12 逐字）：开了待办之后对象"
    "还可能变，而人读到的必须是当时那一串。"
)


def _list_cases(
    *, review: Callable[[], ReviewCaseStore], limit: int | None
) -> dict[str, Any]:
    """读收件箱。**未决的在前、早的在前**（顺序是端口契约的一部分，由存储保证）。

    封顶与 ``list_scenes`` 同一条：给了 ``limit`` 就截断，而**截断被报出来** ——
    一份静默变短的收件箱读起来像「就这几件」。
    """
    if limit is not None and (
        not isinstance(limit, int) or isinstance(limit, bool) or limit < 1
    ):
        raise ToolError(
            f"limit 是 {limit!r}。它要么是正数，要么是 null（要全部）—— "
            "0 与负数在这里没有别的意思，而它们看起来像「这个收件箱是空的」。"
        )
    rows = review().list_cases()
    shown = rows if limit is None else rows[:limit]
    pending = sum(1 for row in rows if row.state in PENDING_STATES)
    return {
        "cases": [_case_view(row) for row in shown],
        "counts": {
            "returned": len(shown),
            "total": len(rows),
            "outstanding": pending,
            "closed": len(rows) - pending,
        },
        "truncation": (
            None
            if limit is None or len(shown) == len(rows)
            else {
                "limit": limit,
                "dropped": len(rows) - len(shown),
                "note": (
                    "还有这么多件没回。它们在**后面**（未决的在前、早的在前），"
                    "所以被丢下的都是最晚开、且已经关掉的那些。"
                ),
            }
        ),
        "note": _CASE_NOTE,
    }


def _get_case(*, review: Callable[[], ReviewCaseStore], case_id: str) -> dict[str, Any]:
    """按 id 读一件，连同它**历来的每一条决定**。

    历史一起给，而不是另开一个工具：一件待办没有「只看当前状态」的用法 ——
    「谁提的、谁批的、上一次是什么时候动过」全在那一串上，而分两次读的调用方得
    自己把两个时刻对齐。

    ``found: false`` 是**返回值**、不是错误：与 ``get_evidence`` 的取向一致 ——
    「这个 id 没有案子」是一个答案。而 ``claim`` / ``resolve`` / ``approve`` 那三条
    路上，同样的输入是**一次拒绝**（它们要写，而写必须有对象）。
    """
    wanted = _case_id(case_id)
    store = review()
    case = store.get_case(wanted)
    if case is None:
        return {
            "found": False,
            "case": None,
            "resolutions": [],
            "note": (
                f"没有 case_id 为 {wanted!r} 的待办。这与「那件待办没有任何决定」是"
                "两件事：后者会是一个 found 为 true、resolutions 为空表的案子。\n"
                "case_id 是内容寻址的（工作区 + 场景 + 原因 + 主体），所以它读不回来"
                "通常意味着两件事之一：这一件从来没有被开过，或者它属于另一个工作区。"
            ),
        }
    return {
        "found": True,
        "case": _case_view(case, with_history=True),
        "resolutions": [_resolution_view(row) for row in store.list_resolutions(wanted)],
        "note": _CASE_NOTE,
    }


def _get_flow(
    *,
    review: Callable[[], ReviewCaseStore],
    window_start: str,
    window_end: str,
) -> dict[str, Any]:
    """一段窗口里的流水，外加**当场算过的那条恒等式**。

    窗口按 ISO-8601 收，两端都可省。省掉时按 ``application/review.py`` 的
    ``DEFAULT_FLOW_DAYS`` 那样一个**说得出口**的值算，而不是「全部历史」—— 后者在对账
    时答的是另一个问题（累计），而它在报告上只显示成一个更大的数。

    ``reconciles`` 是**判据**，不是一句说明：它把 §12 那条恒等式当场算一遍，对不上
    就是 false，而 ``equation`` 是那条式子的当前取值。只回一串计数、让读的人自己加，
    等于把这条判据交给每一个读者各实现一次。
    """
    start = _moment(window_start, "window_start")
    end = _moment(window_end, "window_end")
    if start is not None and end is not None and start > end:
        raise ToolError(
            f"窗口是空的：window_start {start.isoformat()} 晚于 window_end "
            f"{end.isoformat()}。倒过来的窗口不是「没有事件」，是两端写错了 —— "
            "而它会回一份各项为零的流水，读起来像「这一段时间什么都没发生」。"
        )
    result: ReviewFlow = flow(store=review(), window_start=start, window_end=end)
    return {
        "window_start": result.window_start.isoformat(),
        "window_end": result.window_end.isoformat(),
        # 字段名逐字取自 ``ReviewFlow``（pending_start / pending_end，不是
        # pending_at_start）—— 一处改名而另一处不改，读的人会以为有两个数。
        "pending_start": result.pending_start,
        "opened": result.opened,
        "reopened": result.reopened,
        "resolved": result.resolved,
        "dismissed": result.dismissed,
        "merged": result.merged,
        "transferred": result.transferred,
        "pending_end": result.pending_end,
        "reconciles": result.reconciles,
        "equation": result.equation,
        "note": (
            "恒等式："
            + result.equation
            + "（domain.review.ReviewFlow.reconciles：开头未决 + 期间新开 + 期间重开 "
            "− 期间关掉 − 期间合并 == 结尾未决）。窗口是左闭右开的 [start, end)，所以"
            "一个落在边界上的事件属于**后**一段。\n\n"
            "merged 与 transferred **另记**（§12 逐字）：把它们塞进 resolved 会让"
            "「人处理了几件」多出几个从来没有人看过的数。被合并的那一件**它自己的事件"
            "不计数** —— 它的宿主案件有自己的事件序列，两份都算等于同一件事被处理两次。\n\n"
            "transferred 在本切片**恒为 0**，而这不是一个被量出来的零：转移（把一件"
            "待办交给另一个队列或人）今天没有生产者，所以没有任何一格能算出它。它留在"
            "恒等式里而不删掉，是因为 §12 那句话里两个词并列。\n\n"
            "reconciles 为 false 时要看的是 equation 与逐格的差额，不是总数：它意味着"
            "窗口的两端之间有一件事既没被记成新开、也没被记成关掉（或反过来被记了两次）。"
        ),
    }


# ----------------------------------------------------------------------
# 写：认领、裁决、批准
# ----------------------------------------------------------------------
def _claim(
    *,
    review: Callable[[], ReviewCaseStore],
    case_id: str,
    expected_version: int,
) -> dict[str, Any]:
    """把一件 ``OPEN`` 的待办认领走。"""
    wanted = _case_id(case_id)
    _version(expected_version, "expected_version")
    actor = _actor()
    try:
        case = claim_case(
            store=review(),
            case_id=wanted,
            by=actor.name,
            at=utc_now(),
            expected_version=expected_version,
        )
    except ReviewConflictError as exc:
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise ToolError(f"认领没能落盘：{exc}") from exc
    return {
        "case": _case_view(case),
        "claimed_by": case.claimed_by,
        "note": (
            "认领改的是**当前状态**（谁在做），不追加历史 —— 它不是一次决定，所以它"
            "不进只增不改的那张表。\n\n"
            "它与裁决共用同一条并发判据（expected_version）：两个人同时领走一件时，"
            "后写的会被拒，而不是静默顶掉前一个的认领（而他的屏幕上还写着「我在做"
            "这一件」）。只有 OPEN 那一件可以被认领 —— 已经关了、或者已经在等批准的"
            "那一件都不是「待认领」：等批准等的是**另一个人的一次批准**。\n\n" + _WHO
        ),
    }


def _resolve(
    *,
    scenes: Callable[[], SceneStore],
    review: Callable[[], ReviewCaseStore],
    case_id: str,
    outcome: str,
    answer: str,
    expected_version: int,
    proposed_version: int | None,
) -> dict[str, Any]:
    """把人的一条决定折进它那一件。**一次写事务**（端口逐字）。"""
    wanted = _case_id(case_id)
    _version(expected_version, "expected_version")
    actor = _actor()
    chosen = _outcome(outcome)

    if chosen in POLICY_APPROVAL_OUTCOMES:
        # 批准走**另一条路**（另一个工具、另一组角色）。在这里放行等于让一条控制面的
        # 工具做一次授权面的动作 —— 而两条路的角色要求不同这件事只在目录上看得见。
        raise ToolError(
            f"{chosen.value!r} 不走这一条：批准一件待办提出的规则变更要用 "
            "approve_policy_change（它要求 authorizer 角色，而这一条要求 controller）。\n"
            "  把两者合成一条工具，等于让控制器可以批准自己提的变更 —— 而 §9 那句"
            "「获授权者批准」的全部价值就是**它不是一个代理能自己走到的位置**。"
        )

    store = review()
    case = _on_disk(store, wanted)
    resolution = _resolution(
        case=case,
        actor=actor,
        outcome=chosen,
        answer=answer,
        expected_version=expected_version,
        proposed_version=proposed_version,
    )
    try:
        updated = resolve_case(store=store, resolution=resolution, scenes=scenes())
    except ReviewConflictError as exc:
        raise ToolError(str(exc)) from exc
    except ConfigError as exc:
        # 「提议挂的那一版真的存在」那一条判据抛的就是它。它是**输入**的问题
        # （那一版还没写），处置是 add_scene_version，不是重读一遍再试。
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise ToolError(f"决定没能落盘：{exc}") from exc
    return {
        "case": _case_view(updated),
        "resolution": _resolution_view(resolution),
        "note": (
            "这条决定**已经写进只增不改的历史**，而案件那一行被推进到它的结果上。\n\n"
            "expected_version 是你在自己屏幕上看到的那个 case.version：盘上已经不是它时"
            "整次拒绝（陈旧 attempt 不覆盖新结果）。它挡的是「两个人同时裁决同一件，"
            "最后一个写的赢而两边都以为自己成功了」。\n\n"
            "propose_policy_change 必须写明 proposed_version，而那一版必须**真的存在**："
            "一条挂在不存在版本上的提议永远等不到批准，而它在报告上只是一件挂着的事。"
            "提议**不生效** —— 案件转 awaiting_approval，等另一位获授权者的 "
            "approve_policy_change。\n\n"
            "结果必须真的改变状态：对一件已经 resolved 的案件再 dismiss 一次会被拒。"
            "要改一件已经裁决过的案件，先重开它（reopen）—— 那句话写下了「上一次的答案"
            "不再作数」，而重复一条同样的决定没有写下任何东西。\n\n" + _WHO
        ),
    }


def _approve(
    *,
    review: Callable[[], ReviewCaseStore],
    case_id: str,
    answer: str,
    expected_version: int,
) -> dict[str, Any]:
    """由**获授权者**批准一条已写在盘上的提议。**必须是另一个人。**"""
    wanted = _case_id(case_id)
    _version(expected_version, "expected_version")
    actor = _actor()
    store = review()
    case = _on_disk(store, wanted)
    resolution = _resolution(
        case=case,
        actor=actor,
        outcome=ResolutionOutcome.APPROVE_POLICY_CHANGE,
        answer=answer,
        expected_version=expected_version,
        # 批准**不挂版本**：挂起哪一版是那条提议的事（它在只增不改的历史上，
        # ``proposed_version`` 逐字非空）。批准这一格填一个版本号会被
        # ``HumanResolution`` 的判据当场拒绝 —— 而那是同一条判据的第二处实现。
        proposed_version=None,
    )
    try:
        updated = approve_use_case(store=store, resolution=resolution)
    except ReviewConflictError as exc:
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise ToolError(f"批准没能落盘：{exc}") from exc
    return {
        "case": _case_view(updated),
        "resolution": _resolution_view(resolution),
        "note": (
            "批准之后那一版才可以 activate：control_scene(activate, version=N) 在批准"
            "之前会以「有一件待办正等着它被批准」拒绝它。批准**不直接生效** —— "
            "它解除的是 control_scene 那道闸门，让那一版能被激活。\n\n"
            "批准人取自令牌，而**同一个人不能既提议又批准**：代码分不出两个令牌是不是"
            "铸给了同一个人，但它读得出一条提议与一次批准上写的是不是同一个名字。\n\n"
            "角色本身不在这里判：那是 security/catalog.py 的事，一处定义 —— 这条工具是"
            "目录里 authorizer 那一组仅有的两员之一（另一员是 authorize_wave）。"
        ),
    }


def _impact(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    scene_id: str | None,
    from_version: int,
    to_version: int,
) -> dict[str, Any]:
    """两版之间的影响预览。**与 add_scene_version 回执里那份是同一个实现。**"""
    chosen = require_scene(settings, scene_id)
    for value, name in ((from_version, "from_version"), (to_version, "to_version")):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ToolError(
                f"{name} 是 {value!r}。版本号是从 1 开始的整数 —— 一个不是版本号的东西"
                "会让这次比较对着一个不存在的版本算出一份看起来正常的影响。"
            )
    result = impact(
        scenes(), scene_id=chosen, from_version=from_version, to_version=to_version
    )
    return {
        "scene_id": chosen,
        "from_version": from_version,
        "to_version": to_version,
        "impact": result.model_dump(mode="json"),
        "note": (
            "这一份与 add_scene_version 回执里的那份**是同一次计算**"
            "（application/scenes.py::impact_between，一处定义）—— 两处各写一遍的形态是"
            "「工具读出来的影响」与「写这一版时回执里的影响」各算各的，而两份报告都自洽。\n\n"
            "它是一次**预览**：不写任何东西，也不改变哪一版在生效。"
        ),
    }


# ----------------------------------------------------------------------
# 收下参数的几个小闸
# ----------------------------------------------------------------------
def _case_id(value: str) -> str:
    """收下一个 ``case_id``。空的与非规范的一律拒绝。

    理由与 ``tools._account_key`` 同形：一个需要被规范化的 id 会静默指向**另一个**
    对象（这里是另一件待办），而报告上什么都看不出来。
    """
    text = (value or "").strip()
    if not text:
        raise ToolError(
            "case_id 不能为空。它是 list_review_cases / get_review_case 回执里那一格 "
            "—— 内容寻址的，所以同一个 id 每次指向同一件待办。"
        )
    try:
        return check_canonical_id(text, "case_id")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def _version(value: int, name: str) -> int:
    """收下一个乐观锁序号。"""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ToolError(
            f"{name} 是 {value!r}。它是你在自己屏幕上看到的那个 case.version"
            "（从 1 开始）—— 一个不是版本号的东西会让这次调用**看起来**是一次 CAS，"
            "而实际上它比的是一个从来不存在的东西。"
        )
    return value


def _outcome(value: str) -> ResolutionOutcome:
    """把一个结果名收进闭集。未知的**拒绝**，不回落到某一条分支。

    与 ``product_loop._control_scene`` 对 ``action`` 的处置逐字同一条：一个自由字符串
    会被实现静默当成某一条分支处理，于是 ``outcome='Dismiss'`` 与
    ``outcome='dismiss'`` 得到两种不同的结果，而调用方以为它们一样。
    """
    text = (value or "").strip()
    try:
        return ResolutionOutcome(text)
    except ValueError:
        raise ToolError(
            f"没有名为 {text!r} 的结果。人是这几个："
            f"{sorted(item.value for item in ResolutionOutcome)}。\n"
            "  批准（approve_policy_change）不在这里 —— 它走另一条工具，因为它的"
            "角色要求不同。"
        ) from None


def _moment(value: str, name: str) -> datetime | None:
    """收下一个 ISO-8601 时刻。空串 = 没给（由应用层取缺省）。

    带时区是**要求**（``to_utc`` 逐字：naive 不得进入本系统）。一个省掉时区的时刻
    在这里能被 ``fromisoformat`` 解析出来，却会让窗口的两端按服务器的本地时区解释
    —— 于是「这一段窗口里发生了什么」取决于服务跑在哪台机器上。
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ToolError(
            f"{name} 是 {text!r}，不是一个 ISO-8601 时刻"
            "（例如 2026-09-26T00:00:00+00:00）。"
        ) from None
    try:
        return to_utc(parsed)
    except ValueError as exc:
        raise ToolError(
            f"{name} 是 {text!r}，它没有带时区：{exc}\n"
            "  一个省掉时区的时刻会被按服务所在机器的本地时区解释，于是「这一段窗口里"
            "发生了什么」取决于服务跑在哪里。"
        ) from exc


def _actor() -> Principal:
    """这一次调用的当事人。**经传输层不可达**（无权者到不了这三个工具）。

    留着它不是为了断言一个事实，而是为了让绕过中间件的调用者（测试、直接调处理器的
    那一侧）拿到一句能读的拒绝，而不是一个 ``AttributeError`` —— 与
    ``product_loop._require_principal``、``tools._claim`` 同款分支同一个理由。

    ``name`` 与 ``roles`` 也在这里收：两格都**必非空**（``HumanResolution`` 的判据
    逐字，认领那一侧 ``domain.review.claim`` 同一条），而让它们走到模型构造期才报，
    调用方拿到的会是一句「这一条决定构造不出来」加一行字段名 —— 而真正要说的是
    「这枚令牌上没有可记的主体」。两件事的处置不同：前者改调用，后者重铸令牌。
    """
    principal = current_principal()
    if principal is None:
        raise ToolError(
            "这个动作需要主体：当事人会被写进待办的历史，而主体取自传输层的 "
            "Bearer 令牌，不取自任何参数。"
        )
    if not principal.name.strip():
        raise ToolError(
            "这枚令牌没有主体名（sub 与 client_id 都是空的），所以这一次调用没有可记的"
            "当事人 —— 而「这件事是谁决定的」必须有一个写在盘上的答案。重铸一枚令牌。"
        )
    if not principal.roles:
        raise ToolError(
            "这枚令牌没有带任何已知角色，所以这一次调用没有可记的授权依据 —— 历史里"
            "那一格（roles）答的是「这个人当年有没有资格做这件事」，而令牌事后不可"
            "复现，它是唯一读得到的依据。"
        )
    return principal


def _on_disk(store: ReviewCaseStore, case_id: str) -> ReviewCase:
    """读一件**必须存在**的待办，不存在就拒绝。

    三个写动词都要它，而它们要的是同一件东西：这一件的 ``workspace``。决定上的那一格
    必须是**那一件案件的**工作区（``domain.review.apply_resolution`` 逐字会比），但它
    不能从参数来 —— 一个能传的 workspace 让调用方把一条决定写到另一个工作区的案件上，
    而存储那一层会拒（``apply_resolution`` 的 workspace 判据与 ``owned`` 的绑定判据
    各拒一次）—— 拒是对的，但那时调用方已经拿到一个不含定位信息的错。

    它**不是**并发判据：真正比 ``expected_version`` 的是存储在写事务里的那一次读。
    这里读到的版本可能已经旧了，而那时该发生的是一次 ``ReviewConflictError``，由存储
    报出来 —— 那正是「陈旧 attempt 不覆盖新结果」唯一能被强制的地方。
    """
    case = store.get_case(case_id)
    if case is None:
        raise ToolError(
            f"没有 case_id 为 {case_id!r} 的待办，所以这一条决定没有对象。一个字都没写。\n"
            "  这与「那一件没有任何决定」是两件事：后者读得回来，只是历史是空的。"
        )
    return case


def _resolution(
    *,
    case: ReviewCase,
    actor: Principal,
    outcome: ResolutionOutcome,
    answer: str,
    expected_version: int,
    proposed_version: int | None,
) -> HumanResolution:
    """造一条决定。**判据在 domain，不在这里** —— 这一层只把它的拒绝翻成一条可读的
    回执（``tools._readable``）。

    四个字段由**这一层**填而不是由 domain 兜底：

    * ``by`` / ``roles`` —— 取自令牌（模块 docstring 的「人从哪里来」）。
    * ``at`` —— 取服务器时钟。一个可以传的时刻让「谁先提的、谁后批的」变成调用方的
      输入，而那个先后正是 ``inbox_flow`` 判「最后一个事件是什么」的依据。
    * ``resolution_id`` —— 由这一次事件算出来（:func:`_resolution_id`）。
    * ``workspace`` —— 取自**那一件案件**（:func:`_on_disk`），不取自参数。
    """
    at = utc_now()
    try:
        return HumanResolution(
            resolution_id=_resolution_id(
                case_id=case.case_id,
                outcome=outcome,
                by=actor.name,
                expected_version=expected_version,
                at=at,
            ),
            case_id=case.case_id,
            workspace=case.workspace,
            outcome=outcome,
            answer=answer,
            by=actor.name,
            roles=tuple(sorted(role.value for role in actor.roles)),
            expected_version=expected_version,
            at=at,
            proposed_version=proposed_version,
        )
    except ValidationError as exc:
        raise ToolError("这一条决定构造不出来：\n" + _readable(exc)) from exc


def _resolution_id(
    *,
    case_id: str,
    outcome: ResolutionOutcome,
    by: str,
    expected_version: int,
    at: datetime,
) -> str:
    """这一次事件的 id。**不是内容寻址的**（模块 docstring 最后一节）。

    原像里有五样，各去掉一个的后果：

    * 去掉 ``case_id`` —— 两个不同案件上同一时刻的同一种决定会撞主键，而撞主键是一次
      ``StoreError``：一次正常的写入会被报成「已经在盘上了」。
    * 去掉 ``outcome`` —— 同一件案件的 correct_object 与 dismiss 撞在一起。
    * 去掉 ``by`` —— 两个人对同一件做同一种决定会撞在一起，而两次调用各自都正常。
    * 去掉 ``expected_version`` —— 「从哪一版做的」这件事在 id 上没有落点，而它与
      ``HumanResolution.expected_version`` 那一格答的是同一个问题。
    * 去掉 ``at`` —— 上面那条路就断了：「放下 → 重开 → 再放下」里第一次与第三次的
      原像只剩版本能分开它们，而版本在 REOPEN 之后可能回到同一个数。这条路是**真实
      会走**的（``REOPEN`` 的全部用途就是让一件事被做第二次）。

    分隔符是 U+0000（NUL）：``by`` 是自由文本（可能含空格），而任何**可打印**的分隔符
    都可能出现在它里面 —— 那会让两个不同的元组拼出同一个原像，也就是两件事撞上同一个
    id。写 U+0000 而不是那个转义写法，是因为 ``tests/test_portability.py`` 会把源码里的
    反斜杠字面量当成路径来审 —— 那条规则挡的是真缺陷，这里改措辞，不放宽规则。
    """
    digest = sha256_text(
        "\x00".join((case_id, outcome.value, by, str(expected_version), at.isoformat()))
    )[:16]
    return f"{RESOLUTION_ID_PREFIX}-{digest}"

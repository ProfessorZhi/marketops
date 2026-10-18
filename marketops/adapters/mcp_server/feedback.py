"""反馈环的八个处理器（§B23）。

判据全部住在 ``application/feedback.py`` 与 ``application/feedback_preview.py``（它们的
docstring 写清了作用域的缺省为什么是拒绝、提得起补丁的是哪几类、批准为什么不在这里）。
这一层只做四件事：取时刻、把当事人从**令牌**里取出来、把三种拒绝翻成一条可读回执、
把应用层那些 ``as_dict()`` 原样交出去。**一个格子都不在这里重算。**

## 当事人取自令牌，不取自任何参数

``submit_feedback`` 的 ``principal``、两条提案的 ``proposed_by`` 都来自
``guard.current_principal()`` —— 传输层用 Bearer 令牌确立了身份，而一个能由载荷填写的
「谁说的」此后会被当成一个人真的说过的话读回去，并且**两处各自都正常**。

这里**没有**引 ``review.py`` 那个 ``_actor()``（它是私有的，而且它多要一样东西）：
它要 ``principal.roles`` 非空，因为一条 ``HumanResolution`` 上记着 ``roles`` ——
「这个人当年有没有资格做这件事」。这一层的三件对象上**没有 roles 那一格**，所以在这里
要它，会因为一样**不被记录的东西**拒掉一枚令牌，而拒绝里点名的字段在盘上的行里
根本找不到。身份从哪里来只有一处定义（``guard.current_principal``）；而「哪些角色
字段必须非空」按消费者不同，这是对的，不是重复。

## 读是返回值，写是拒绝

与 ``review._get_case`` 逐字同一条：``get_feedback`` 读不到时返回 ``found: false`` ——
「这个 id 没有反馈」是一个答案。而 ``preview_feedback_impact`` 读不到时是**一次拒绝**，
理由与 ``preview_launch`` 逐字相同：预览读的是**某一条已经写下的反馈**，它不存在时没有
可预览的东西 —— 一份「什么都不改」的读数与「你写错了 id」同形，而前者看起来像一条
结论。

## 为什么这一族必须够不到 ``tools.py``（以及 ``search`` / ``acquisition``）

``preview_feedback_impact`` 的目录行逐字：**零 provider、零写入 —— 预览不花钱、不留痕**。
那一条不是自觉：``tests/test_mcp_feedback_is_offline.py`` 算的是这一族从处理器出发的
import 闭包，要求它够不到任何网络能力。``tools.py`` 经 ``adapters.build_stamp`` 够得到
``subprocess``，而那份口径逐字是「真的会对外发起请求、**或起一个外部进程**」。

所以这一族宁可绕路也不 import 那一族：``require_scene`` 与 ``store_failure`` 从
``._shared`` 引（那里现在是**定义**所在）。

``settings`` 是必填的，而它在这里的用途只有一处：``settings.profile`` 就是这一行反馈
所属的**工作区**（与 ``launch.py`` 逐字同一条）。这一族**不收** ``scene_id``：八条工具的
目录行里没有这一格，而「本部署服务哪个场景」由 deployed profile 回答，不由每一次调用
回答。

``review=None`` 是**允许的**，而且那不是「降级」：``list_policy_patches`` 的
「批准了没有」有四档，其中一档就是 ``UNREADABLE``（没拿到那个存储）。把它折进
「还没批」，会让一个只是没接上存储的部署读起来像「这些补丁都在等着人」。

``scenes=None`` 同样是**允许的**，同一条理由：「**应用了没有**」有五档，其中一档也是
``UNREADABLE``（没拿到场景存储）。把它折进「还没产版」，会让一个只是没接上存储的
部署读起来像「这些补丁都还没落地」—— 而两者的处置相反（前者去接线，后者去写那一版）。
批准（``standing``）与应用（``application``）是**两档**：一次批准不产生任何一版。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ...application.feedback import (
    MAX_FEEDBACK,
    draft_candidate,
    draft_feedback,
    draft_patch,
    list_candidates,
    list_feedback,
    list_patches,
    propose_candidate,
    propose_patch,
    read_feedback,
    standing_of,
    submit_feedback,
)
from ...application.feedback_preview import feedback_impact
from ...config import Settings
from ...domain.errors import ConfigError, IdempotencyConflictError, StoreError
from ...domain.feedback import (
    FeedbackKind,
    RiskClass,
    Scope,
    ScopeKind,
)
from ...domain.util import utc_now
from ...ports.feedback import FeedbackStore
from ...ports.review import ReviewCaseStore
from ...ports.scenes import SceneStore
from ...ports.store import EvidenceStore
from ._shared import Handler, readable, store_failure
from .guard import current_principal

__all__ = ["feedback_handlers"]

#: ``_construct`` 保住的那个类型 —— 它只做转手，不改类型。
_T = TypeVar("_T")
#: ``_enum`` 的入参：一个 ``str, Enum`` 的**类**。
_E = TypeVar("_E", bound=Enum)


# ------------------------------------------------------------------ 小工具
def _actor_name() -> str:
    """这一次调用的当事人名。**经传输层不可达**（无权者到不了这三条写路径）。

    留着它不是为了断言一个事实，而是为了让绕过中间件的调用者（测试、直接调处理器的
    那一侧）拿到一句能读的拒绝，而不是一个 ``AttributeError`` —— 与
    ``review._actor`` / ``product_catalog._principal_name`` 同款分支同一个理由。

    只要 ``name``，不要 ``roles``：理由见模块 docstring（这一层的对象上没有 roles 那一格）。
    """
    principal = current_principal()
    if principal is None:
        raise ToolError(
            "这个动作需要主体：它是「谁说的」那一格，而主体取自传输层的 Bearer 令牌，"
            "不取自任何参数。"
        )
    if not principal.name.strip():
        raise ToolError(
            "这枚令牌没有主体名（sub 与 client_id 都是空的），所以这一次调用没有可记的"
            "当事人 —— 而「这句话是谁说的」必须有一个写在盘上的答案。重铸一枚令牌。"
        )
    return principal.name


def _construct(what: str, build: Callable[[], _T]) -> _T:
    """构造一件对象，把**构造期**的两种拒绝各摊成一条可读回执。

    两种拒绝，两条不同的理由：

    * ``ValidationError``（pydantic）—— 与 ``product_catalog`` / ``review`` 那一族
      同一条：``str(ValidationError)`` 会把输入值原样回显，在一条工具回执里那是
      几十行，而调用方要的只是「哪一个字段、为什么不行」。
    * ``ConfigError``（领域）—— ``draft_feedback`` 的作用域解析会抛它：一格都没点名
      是**拒绝**（而不是退回工作区），点名的引用与点名的 ``scope_kind`` 对不上也是
      拒绝。那几句话本来就是写给读的人看的（含下一步），原样转出去。

    第二种是**补上来的**，补之前的表现值得写下来：一条不给 scope 的反馈提交上来，
    回执是 ``Error executing tool submit_feedback`` —— 领域那句「不替你填 workspace，
    因为自然语言永远不改全局策略」一个字都没到达调用方。而那正是承重点 1 在工具面上
    的落点：**它必须读得出来，否则「缺省是一次拒绝」在协议上不存在。**

    **它包的是构造那一次调用，不是处理器。** 一个包住处理器的装饰器（``*args,
    **kwargs``）会把工具的签名抹平成「没有参数」，而 ``server.add_tool`` 是从签名推
    工具 schema 的 —— 那八条工具会在 ``tools/list`` 上看起来不吃任何参数，
    而这一条**不会报错**。
    """
    try:
        return build()
    except ValidationError as exc:
        raise ToolError(f"{what}构造不出来：\n" + readable(exc)) from exc
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc


def _enum(cls: type[_E], value: object, what: str) -> _E:
    """闭集的一格。**报错里把可选的几格列全。**

    枚举构造抛的是 ``ValueError``（不是 pydantic 的 ``ValidationError``），所以上面
    那一条接不住它 —— 而 README 那份回执口径要的是「哪个字段、为什么不行」。
    """
    try:
        return cls(value)  # type: ignore[call-arg]
    except ValueError as exc:
        members = [getattr(item, "value", item) for item in cls]  # type: ignore[attr-defined]
        raise ToolError(f"{what} 是 {value!r}，而可选的只有 {members}。") from exc


def _scope_of(scope_kind: str | None, scope_ref: str | None) -> Scope | None:
    """把 ``kind`` + ``ref`` 凑成一个 :class:`Scope`，两格都给才算数。

    **两格缺一就是一次拒绝**，而且拒绝由 ``Scope`` 自己给（它要求 ``ref`` 非空）。
    在这里给一个缺省（比如 ``or ""``）会让「他没说作用域」与「他说了一个空引用」同形，
    而后者此后会被当成一条落在无名之物上的建议读回去。
    """
    if scope_kind is None:
        if scope_ref is not None:
            raise ToolError(
                f"给了 scope_ref={scope_ref!r} 却没给 scope_kind —— 一个引用本身说不出"
                "它说的是哪一层。\n"
                f"可选的类别：{[kind.value for kind in ScopeKind]}。"
            )
        return None
    return _construct(
        "这一个作用域",
        lambda: Scope(kind=_enum(ScopeKind, scope_kind, "scope_kind"), ref=scope_ref or ""),
    )


def _wrote(exc: ConfigError | IdempotencyConflictError | StoreError) -> ToolError:
    """一次**写**被拒时的回执。三种拒绝各是一种可读的结果，不是崩溃。

    * ``ConfigError`` —— 调用方给的东西推不出对象（作用域缺一格、来源写法不认识、
      类别提不起补丁、案件已经关掉）。``domain/errors.py`` 里它的 docstring 是
      「配置缺失或非法（通常来自环境变量）」，而这一层用法与
      ``adapters/mcp_server/review.py`` 那一处逐字相同：**它是输入的问题**。
    * ``IdempotencyConflictError`` —— 同一把幂等键配了不同的话。``domain/errors.py``
      那一格逐字要求它「显性失败」：静默保留先写的那条，会让「我提交的那句话被记下了」
      与「盘上那条是别的什么」变成两件事。
    * ``StoreError`` —— 存储层说过的失败（跨工作区、内容寻址被绕过）。

    三条都接，而不是只接 ``StoreError``：不接的那两条会逃出去，调用方读到的是一句
    光秃秃的 ``Error executing tool submit_feedback``，而领域原本给了一句很准的话。
    这正是 ``_shared.store_failure`` 的 docstring 里那段实测过的情形。

    ``StoreError`` 那一条**转手给** ``_shared.store_failure``，不在这里再写一遍：
    那一条不是这一族专属的（``launch`` / ``research`` 都从同一个函数出），而两处
    各写一遍的表现是同一句领域原话在两条路径上被包成两种措辞 —— 两条各自都读得通，
    所以没有一处会响。
    """
    if isinstance(exc, StoreError):
        return store_failure(exc)
    return ToolError(str(exc))


def _read(*, store: Callable[[], FeedbackStore], feedback_id: str):
    """按 id 读一条**必须存在**的反馈，不存在就拒绝。**预览与提案共用这一条。**

    它**不是** ``get_feedback``：那一条读不到时返回 ``found: false``（读是返回值）。
    这里的两个调用点都要写 —— ``preview_feedback_impact`` 要一个主语，
    ``propose_*`` 要一个来源 —— 而写必须有对象。
    """
    wanted = (feedback_id or "").strip()
    if not wanted:
        raise ToolError("feedback_id 是空的。")
    event = store().get_feedback(wanted)
    if event is None:
        raise ToolError(
            f"没有 feedback_id 为 {wanted!r} 的反馈（这个工作区）。\n"
            "  feedback_id 是内容寻址的（工作区 + principal + 幂等键），所以它读不回来"
            "通常意味着两件事之一：这一条从来没有被提交过，或者它属于另一个工作区。\n"
            "  用 list_feedback 看看这个工作区里现在有什么，或者用 submit_feedback 提交它。"
        )
    return event


# ------------------------------------------------------------------ 一、反馈
def _submit(
    *,
    settings: Settings,
    feedback: Callable[[], FeedbackStore],
    kind: str,
    idempotency_key: str,
    literal_feedback: str,
    artifact_ref: str | None,
    source_session: str,
    target_ref: str | None,
    run_ref: str | None,
    workstream_ref: str | None,
    scope_kind: str | None,
    scope_ref: str | None,
) -> dict[str, Any]:
    """落一条反馈。**这一行只写事件 —— 它不改全局策略，一个字节都不改。**

    回执里 ``admitted`` 与 ``replayed`` 是**同一件事的两面**（后者是前者的取反，
    由 ``FeedbackAdmission`` 自己算），两个都给是因为读的人会先看其中一个：
    「今天新增了几条」看 ``admitted``，「这句话重复提交了吗」看 ``replayed``。
    """
    actor = _actor_name()
    at = utc_now()
    event = _construct(
        "这一条反馈",
        lambda: draft_feedback(
            workspace=settings.profile,
            principal=actor,
            idempotency_key=idempotency_key,
            kind=_enum(FeedbackKind, kind, "kind"),
            literal_feedback=literal_feedback,
            artifact_ref=artifact_ref,
            source_session=source_session,
            target_ref=target_ref,
            run_ref=run_ref,
            workstream_ref=workstream_ref,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            at=at,
        ),
    )
    try:
        admission = submit_feedback(store=feedback(), event=event)
    except (ConfigError, IdempotencyConflictError, StoreError) as exc:
        raise _wrote(exc) from exc
    stored = admission.event
    return {
        "feedback_id": stored.feedback_id,
        "workspace": stored.workspace,
        "principal": stored.principal,
        "kind": stored.kind.value,
        "ring": stored.ring.value,
        "proposed_scope": stored.proposed_scope.as_dict(),
        "created_at": stored.created_at.isoformat(),
        "admitted": admission.admitted,
        "replayed": admission.replayed,
        "note": _NOTE,
    }


def _get(
    *, feedback: Callable[[], FeedbackStore], feedback_id: str
) -> dict[str, Any]:
    """按 id 读一条，连同**此后在它之上又提了什么**。读不到是 ``found: false``。"""
    wanted = (feedback_id or "").strip()
    reading = read_feedback(store=feedback(), feedback_id=wanted)
    if reading is None:
        return {
            "found": False,
            "feedback": None,
            "note": (
                f"没有 feedback_id 为 {wanted!r} 的反馈。这与「这一条上面还什么都没"
                "提过」是两件事 —— 后者会是一个 found 为 true、raised_on_it 两张表都"
                "为空的读数。\nfeedback_id 是内容寻址的（工作区 + principal + 幂等键），"
                "所以它读不回来通常意味着两件事之一：这一条从来没有被提交过，"
                "或者它属于另一个工作区。"
            ),
        }
    return {"found": True, "feedback": reading.as_dict(), "note": _NOTE}


def _list(
    *,
    feedback: Callable[[], FeedbackStore],
    scope_kind: str | None,
    scope_ref: str | None,
    limit: int | None,
) -> dict[str, Any]:
    """按作用域筛。**匹配到的条数与返回的条数一起报。**

    两个数分开给不是排版：目录行逐字「「只匹配到三条」与「匹配到三百条、这里显示三条」
    是两种完全不同的结论」。``matched`` 是过滤之后的**总数**，它在存储里与那一页在
    同一个读事务里算出来，所以两个数不会在两次查询之间漂移。

    ``limit`` 的上界是 :data:`~marketops.application.feedback.MAX_FEEDBACK`：一个
    调用方给的更大的数**被压到那个上界**，而不是照办 —— 「一次回执多大」由这一侧持有。
    """
    scope = _scope_of(scope_kind, scope_ref)
    cap = MAX_FEEDBACK
    asked = cap if limit is None else min(limit, cap)
    page = list_feedback(
        store=feedback(),
        scope_kind=None if scope is None else scope.kind,
        scope_ref=None if scope is None else scope.ref,
        limit=asked,
    )
    return {
        "matched": page.matched,
        "returned": page.returned,
        "truncated": page.truncated,
        "limit": asked,
        "cap": cap,
        "scope": None if scope is None else scope.as_dict(),
        "events": [row.model_dump(mode="json") for row in page.events],
        "note": (
            "matched 是**过滤之后的全部**条数，returned 是这一页真的回了的条数 —— "
            "两个数在同一个读事务里算出来。matched 是 3 与「matched 是 300、"
            "这里只显示 3 条」是两种完全不同的结论。"
        ),
    }


def _preview(
    *,
    settings: Settings,
    feedback: Callable[[], FeedbackStore],
    feedback_id: str,
    applied_scope_kind: str | None,
    applied_scope_ref: str | None,
    changes: Sequence[str] | None,
) -> dict[str, Any]:
    """一条反馈**若被采纳**会改到什么。**零 provider、零写入。**

    时刻取自服务端时钟（与 ``launch.py`` 逐字同一条）：这一份读数里有好几处判定
    （跨了几档、哪几格受保护），各读一次时钟会让它自相矛盾。

    ``applied_at`` 缺省是**反馈自己说的那一格**（``application/feedback_preview``
    逐字）。一个把缺省填成工作区的实现在这里读起来只是「影响面更大」，
    而它此后会被当成一条有人主张过的全局规则读回去。
    """
    event = _read(store=feedback, feedback_id=feedback_id)
    applied = _scope_of(applied_scope_kind, applied_scope_ref)
    impact = feedback_impact(
        event=event,
        at=utc_now(),
        applied_at=applied,
        changes=tuple(changes or ()),
    )
    return impact.as_dict()


# ------------------------------------------------------------------ 二、候选
def _propose_candidate(
    *,
    settings: Settings,
    feedback: Callable[[], FeedbackStore],
    observation: str,
    generalises_kind: str,
    generalises_ref: str,
    risk: str,
    source_feedback_ids: Sequence[str] | None,
    evidence_refs: Sequence[str] | None,
    note: str,
) -> dict[str, Any]:
    """提一条候选。**它是提案不是批准** —— 没有任何一处把它当权威读。

    ``generalises`` 由 ``kind`` + ``ref`` 两格进来，而它**不能是工作区**：那条判据在
    领域里（``ExperienceCandidate`` 的校验器），拒绝的话点名的是「这一步要一个**人**」
    —— 一条想推广到整个工作区的候选，是慢环里一步做不到的事。
    """
    actor = _actor_name()
    candidate = _construct(
        "这一条候选",
        lambda: draft_candidate(
            workspace=settings.profile,
            observation=observation,
            generalises=_construct(
                "这一个作用域",
                lambda: Scope(
                    kind=_enum(ScopeKind, generalises_kind, "generalises_kind"),
                    ref=generalises_ref,
                ),
            ),
            risk=_enum(RiskClass, risk, "risk"),
            proposed_by=actor,
            source_feedback_ids=tuple(source_feedback_ids or ()),
            evidence_refs=tuple(evidence_refs or ()),
            note=note,
            at=utc_now(),
        ),
    )
    try:
        admission = propose_candidate(store=feedback(), candidate=candidate)
    except (ConfigError, IdempotencyConflictError, StoreError) as exc:
        raise _wrote(exc) from exc
    stored = admission.candidate
    return {
        "candidate_id": stored.candidate_id,
        "revision": stored.revision,
        "state": stored.state.value,
        "workspace": stored.workspace,
        "observation": stored.observation,
        "generalises": stored.generalises.as_dict(),
        "risk": stored.risk.value,
        "source_feedback_ids": list(stored.source_feedback_ids),
        "evidence_refs": list(stored.evidence_refs),
        "proposed_by": stored.proposed_by,
        "proposed_at": stored.proposed_at.isoformat(),
        "admitted": admission.admitted,
        "replayed": admission.replayed,
        "note": (
            "它是一条**提案**：没有任何一处把它当权威读 —— 它不进任何一版声明，"
            "也不改变任何一次判定。它的下游只有 propose_policy_patch，而那一份补丁"
            "同样只是提案；批准走既有的 approve_policy_change（另一个人）。\n"
            + _NOTE
        ),
    }


def _list_candidates(
    *,
    feedback: Callable[[], FeedbackStore],
    evidence: Callable[[], EvidenceStore],
) -> dict[str, Any]:
    """候选清单：状态、范围、**证据条数**（读得出来的与写下来的分开给）。

    ``evidence`` 是必填工厂（理由见 ``application/feedback.py::list_candidates``）：
    一个可以省略它的调用点，会在「这条候选立在哪份材料上」这件事上静默地什么都不查，
    而那一格的默认读数恰好是目录行点名要防的那个 ``0``。
    """
    rows = list_candidates(store=feedback(), evidence=evidence())
    return {
        "count": len(rows),
        "candidates": [row.as_dict() for row in rows],
        "note": (
            "evidence_total 是**写下时说的**条数，evidence_read 是**今天读得出来的**"
            "条数。两个数分开给：evidence_read 是 0 而 evidence_total 是 3 的意思是"
            "「三条引用一条都取不到」，它与「这条候选没立在任何证据上」"
            "（evidence_total 是 0）是两件完全不同的事，而一个数会把它们压成一个 0。"
        ),
    }


# ------------------------------------------------------------------ 三、补丁
def _propose_patch(
    *,
    settings: Settings,
    feedback: Callable[[], FeedbackStore],
    review: Callable[[], ReviewCaseStore] | None,
    scenes: Callable[[], SceneStore] | None,
    source_ref: str,
    changes: Sequence[str],
    target_version: int,
    monitoring: str,
    rollback: str,
    risk: str,
    rationale: str,
    review_case_id: str | None,
) -> dict[str, Any]:
    """提一份补丁。**批准不在这里** —— 批准是 ``approve_policy_change``。

    回执是那一份补丁的**批准读数**（``list_policy_patches`` 的同一形状），所以提完
    立刻就知道它落在哪一档上：``not_attached``（还没归案）/ ``waiting``（在等一个人）/
    ``approved``（已经批了 —— 一次立刻重放同一份提案时会看到这一档）。
    ``approved`` 那一档只有可能来自**盘上已经有的**那条批准决定，不可能由这一条路径
    产生：``PolicyPatchState`` 里没有 ``approved`` 这个成员。

    回执里还有**应用**那一档（``application``）：批准与生效是两件事，见
    :class:`marketops.application.feedback.PatchApplication`。``scenes`` 缺省
    ``None`` 时那一档如实报成 ``unreadable``。
    """
    actor = _actor_name()
    patch = _construct(
        "这一份补丁",
        lambda: draft_patch(
            workspace=settings.profile,
            source_ref=source_ref,
            changes=tuple(changes),
            target_version=target_version,
            monitoring=monitoring,
            rollback=rollback,
            risk=_enum(RiskClass, risk, "risk"),
            rationale=rationale,
            proposed_by=actor,
            review_case_id=review_case_id,
            at=utc_now(),
        ),
    )
    try:
        admission = propose_patch(
            store=feedback(), patch=patch, review=None if review is None else review()
        )
    except (ConfigError, IdempotencyConflictError, StoreError) as exc:
        raise _wrote(exc) from exc
    return {
        **standing_of(
            patch=admission.patch,
            review=None if review is None else review(),
            scenes=None if scenes is None else scenes(),
        ).as_dict(),
        "admitted": admission.admitted,
        "replayed": admission.replayed,
    }


def _list_patches(
    *,
    feedback: Callable[[], FeedbackStore],
    review: Callable[[], ReviewCaseStore] | None,
    scenes: Callable[[], SceneStore] | None,
) -> dict[str, Any]:
    """补丁清单。**每一份的批准读数与应用读数逐份摊开**（四档，不是两档）。"""
    rows = list_patches(
        store=feedback(),
        review=None if review is None else review(),
        scenes=None if scenes is None else scenes(),
    )
    return {
        "count": len(rows),
        "patches": [row.as_dict() for row in rows],
        "note": (
            "standing 有**四**档：not_attached（还没归到任何一件案上，今天连被批准的"
            "资格都没有）/ waiting（在等一个人）/ approved（另一个人批过这一版）/ "
            "unreadable（**这一层没拿到复核存储，所以答不上来**）。\n"
            "unreadable 与 waiting 分开给，是因为把没查折进还没批，会让一个只是没接上"
            "存储的部署读起来像「这些补丁都在等着人」，而它此后会被当成一条真实的"
            "待办队列去催。\n"
            "application 有**五**档：not_approved（还没批）/ not_produced（**批了，而"
            "那一版在盘上不存在**）/ produced（那一版在盘上，而当前生效的不是它）/ "
            "effective（下一次 Run 用的就是它）/ unreadable（没拿到场景存储，答不上来）。\n"
            "批准**不产生**任何一版，所以 approved 与 effective 是两档：原先只报"
            "standing 的时候，一份从没被写下来的版本号会被读成「已经生效了」。\n"
            "monitoring / rollback 是**已生效那一版**带着的（所以没生效时是 null）；"
            "提案自己写的那两条在 declared_monitoring / declared_rollback 上。"
            "一个不能回滚的策略版本和一个能回滚的，在部署那一刻是两件事。\n"
            "rollback_version / rollback_possible 是**机器可读的撤回**：它读的是那一版"
            "自己的 ``SceneVersion.supersedes``（钉住必须等于 version-1），"
            "不是 declared_rollback 那句自由文本。"
        ),
    }


# ------------------------------------------------------------------ 工厂
_NOTE = (
    "这一条**只写事件**：它不改任何策略，也不改变任何一次判定。\n"
    "「自然语言永远不改全局策略」这句话的全部落点就是这一行 —— 一条反馈此后"
    "要走的路（事实路径 / 眼前这一轮 / 候选 / 补丁 → 另一个人批准）由它的 kind 决定，"
    "而每一步都要各自过一次 preview。"
)


def feedback_handlers(
    *,
    settings: Settings,
    feedback: Callable[[], FeedbackStore],
    evidence: Callable[[], EvidenceStore],
    review: Callable[[], ReviewCaseStore] | None = None,
    scenes: Callable[[], SceneStore] | None = None,
) -> dict[str, Handler]:
    """§B23 的八条，按工具名索引。**名字与角色已冻结**（全部 ``_CONTROL``，phase ``K``）。

    ``evidence`` 是必填的（``list_experience_candidates`` 要它核每一条引用）；
    ``review`` 可缺省，而缺省**不是降级**：那一格会如实报成 ``unreadable``
    （见模块 docstring）。

    ``scenes`` 是**应用**那一档的存储，同样可缺省、同样缺省即 ``unreadable``：
    它回答的是「批了以后那一版在不在盘上、是不是当前的生效版」。缺它不影响
    批准那一档，也不影响任何一次写入。

    回执就是应用层那些对象的 ``as_dict()``，一个格子不加、一个格子不减 ——
    与 ``launch_handlers`` 逐字同一条：报告的**形状**与判据的**形状**是同一个对象的
    两个出口，这一层再包一层（加一个 ``_note``、或者把一张表拍平成一句散文）会造出
    第二处定义，而分家的表现是「同一条盘上事实，报告里说一套、判据里说另一套」。

    ``note`` 那两格**不是**第二种定义：它们说的是**怎么读这份回执**（两个数为什么
    分开给、四档各自是什么意思），而不是这份回执里任何一格的值。
    """

    def submit_feedback_tool(
        kind: str,
        idempotency_key: str,
        literal_feedback: str = "",
        artifact_ref: str | None = None,
        source_session: str = "",
        target_ref: str | None = None,
        run_ref: str | None = None,
        workstream_ref: str | None = None,
        scope_kind: str | None = None,
        scope_ref: str | None = None,
    ) -> dict[str, Any]:
        return _submit(
            settings=settings,
            feedback=feedback,
            kind=kind,
            idempotency_key=idempotency_key,
            literal_feedback=literal_feedback,
            artifact_ref=artifact_ref,
            source_session=source_session,
            target_ref=target_ref,
            run_ref=run_ref,
            workstream_ref=workstream_ref,
            scope_kind=scope_kind,
            scope_ref=scope_ref,
        )

    def get_feedback_tool(feedback_id: str) -> dict[str, Any]:
        return _get(feedback=feedback, feedback_id=feedback_id)

    def list_feedback_tool(
        scope_kind: str | None = None,
        scope_ref: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        return _list(
            feedback=feedback, scope_kind=scope_kind, scope_ref=scope_ref, limit=limit
        )

    def preview_feedback_impact_tool(
        feedback_id: str,
        applied_scope_kind: str | None = None,
        applied_scope_ref: str | None = None,
        changes: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        return _preview(
            settings=settings,
            feedback=feedback,
            feedback_id=feedback_id,
            applied_scope_kind=applied_scope_kind,
            applied_scope_ref=applied_scope_ref,
            changes=changes,
        )

    def propose_experience_candidate_tool(
        observation: str,
        generalises_kind: str,
        generalises_ref: str,
        risk: str,
        source_feedback_ids: Sequence[str] | None = None,
        evidence_refs: Sequence[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _propose_candidate(
            settings=settings,
            feedback=feedback,
            observation=observation,
            generalises_kind=generalises_kind,
            generalises_ref=generalises_ref,
            risk=risk,
            source_feedback_ids=source_feedback_ids,
            evidence_refs=evidence_refs,
            note=note,
        )

    def list_experience_candidates_tool() -> dict[str, Any]:
        return _list_candidates(feedback=feedback, evidence=evidence)

    def propose_policy_patch_tool(
        source_ref: str,
        changes: Sequence[str],
        target_version: int,
        monitoring: str,
        rollback: str,
        risk: str,
        rationale: str,
        review_case_id: str | None = None,
    ) -> dict[str, Any]:
        return _propose_patch(
            settings=settings,
            feedback=feedback,
            review=review,
            scenes=scenes,
            source_ref=source_ref,
            changes=changes,
            target_version=target_version,
            monitoring=monitoring,
            rollback=rollback,
            risk=risk,
            rationale=rationale,
            review_case_id=review_case_id,
        )

    def list_policy_patches_tool() -> dict[str, Any]:
        return _list_patches(feedback=feedback, review=review, scenes=scenes)

    return {
        "submit_feedback": submit_feedback_tool,
        "get_feedback": get_feedback_tool,
        "list_feedback": list_feedback_tool,
        "preview_feedback_impact": preview_feedback_impact_tool,
        "propose_experience_candidate": propose_experience_candidate_tool,
        "list_experience_candidates": list_experience_candidates_tool,
        "propose_policy_patch": propose_policy_patch_tool,
        "list_policy_patches": list_policy_patches_tool,
    }
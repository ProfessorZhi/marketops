"""反馈环的**编排**：写一条反馈、读它、提候选、提补丁，以及把批准读数摊开（§B23）。

## 这一层做什么，以及那些事为什么不在别处

领域（``domain/feedback.py``）只懂三件对象与它们之间的判据 —— 它知道作用域的次序、
知道哪五格不能动、知道每一种 ``kind`` 走哪一个环；存储（``adapters/sqlite/feedback.py``）
只懂怎么把它们放上盘。这一层做的是**只有它才能做的三件事**：

* **把一次提交变成一件对象**。``draft_*`` 那一族：算 id（内容寻址）、定 revision、
  解析作用域、读一次时钟。这些是**编排**的性质 —— 它们要看好几个对象，而领域只看得见
  一个。放在这里还有一个更硬的理由：`resolve_scope` 的缺省是**拒绝**，而那一条只有在
  「谁在什么时候调它」这一层才说得清（见下）。
* **跨对象的那几条判据**。一条补丁的 ``source_ref`` 指的是谁、那条反馈是不是提得起
  补丁的那一类、它归到的那件复核案是不是真的在等一个决定 —— 每一条都跨两个对象，
  所以领域里没有一处放得下它们。
* **把「这一条现在怎么样」读出来**。``list_candidates`` 要拿证据存储去核每一条引用；
  ``list_patches`` 要拿复核存储去读「批准了没有」。**这两件事都是读数，不是判定** ——
  它们不改变任何东西，也正因为如此它们必须**分开报**：见下面「四档，不是两档」。

## 作用域的缺省是**拒绝**，这一层只是不替它兜底

``resolve_scope`` 在「一格都没点名」时抛 ``ConfigError``，而 ``submit_feedback`` 的
目录行逐字「**自然语言永远不改全局策略** —— 这一行只写事件」。这一层对这一条唯一的
贡献是：**不给它任何缺省**。``draft_feedback`` 把三个引用原样递下去，不做
``or workspace`` 之类的补全 —— 一个补全的地方就是一处会漂移的默认宽度，而那条判据的
全部内容就是「默认有多宽」。

所以一条连对象、Run、工作流都没点名、也没点名作用域的反馈，在**盘上一个字节都没写**
之前就被拒了。这不是严格，这是那条判据唯一可能的落点。

## 提得起补丁的**不是**「慢环那几类」

判据是 :data:`~marketops.domain.feedback.POLICY_PATCHABLE_KINDS`：「它会不会产生一版」。
``workstream_policy`` 落在**快**环上（它不进 replay/evaluation），而它的处置是
「preview 影响后由上级批准为 Workstream/Segment policy 新版」—— 它产生一版，所以
它提得起补丁；``run_instruction`` 也落在快环上，而它一个版本也不产生，所以它提不起。

两条拒绝各有一条**不同的逃逸通道**，所以它们是两条话而不是一条：

* ``fact_correction`` → 走**已有**的事实/证据路径（``put_evidence`` →
  ``application/evidence_refs.py``）；
* ``run_instruction`` → 走**眼前这一轮**（本 Run/下一 Run 的计划，在途 attempt 冻结）。

一条笼统的「这一类不能提补丁」会让两条完全不同的路在报告上同形，而读的人下一步要做的
事完全不同。

## 批准**不在这里**，而且这里没有第二条路

``propose_policy_patch`` 只到提案为止。``PolicyPatchState`` 里**没有** ``approved``
这个成员 —— 这不是省略，是这一片最要紧的一处类型判据：一个能把自己标成已批准的枚举
成员，迟早会被某一处写上。批准只有一条路，``application/review.py::approve_policy_change``
（角色 ``_AUTHORIZE``，且必须**另一个人**），这一层不碰它、不绕它、也不给它开第二个入口。

``PolicyPatch.changes`` 装的是**格子名**（``POLICY_CELLS`` 的成员），**不是值**。这一条
也是同族的：一个能装策略**值**的字段，就是一处能悄悄把 ``country`` 塞进一份声称只改
``product`` 的补丁里的地方。

## 四档，不是两档

``list_patches`` 报的「批准了没有」有**四**种答案，而它们必须分得开：

* ``APPROVED`` —— 那件复核案上有一条 ``APPROVE_POLICY_CHANGE``，且它不是提议的人写的；
* ``WAITING`` —— 它挂在一件还没关的案上，而那条批准决定还没出现；
* ``NOT_ATTACHED`` —— 它还没归到任何一件案上，所以今天它连被批准的**资格**都没有；
* ``UNREADABLE`` —— **没有给复核存储**，所以这个问题在这里答不上来。

最后一档是承重的。把「没查」折进「还没批」，会让一个只是没接上存储的部署读起来像
「这些补丁都在等着人」，而它此后会被当成一条真实的待办队列去催。所以 ``review=None``
时 ``standing`` 是 ``UNREADABLE``，并且 ``standing_reason`` 点名缺的是哪个存储。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..domain.errors import ConfigError
from ..domain.feedback import (
    FEEDBACK_ID_PREFIX,
    POLICY_PATCHABLE_KINDS,
    PolicyPatch,
    PolicyPatchState,
    RiskClass,
    Scope,
    ScopeKind,
    candidate_id_for,
    candidate_source,
    ExperienceCandidate,
    CandidateState,
    FeedbackEvent,
    FeedbackKind,
    feedback_id_for,
    feedback_source,
    patch_id_for,
    resolve_scope,
)
from ..domain.review import (
    PENDING_STATES,
    POLICY_APPROVAL_OUTCOMES,
    POLICY_PROPOSAL_OUTCOMES,
    ReviewCase,
)
from ..domain.util import utc_now
from ..ports.feedback import (
    CandidateAdmission,
    FeedbackAdmission,
    FeedbackPage,
    FeedbackStore,
    PatchAdmission,
)
from ..domain.scene import SceneState
from ..ports.review import ReviewCaseStore
from ..ports.scenes import SceneStore
from ..ports.store import EvidenceStore
from .review import pending_proposal

#: ``list_feedback`` 一次最多回几条。**封顶在调用点，不封顶在存储**（端口逐字）：
#: 封多少是一个关于「一次回执多大」的决定，而它由交付面持有。两千是照
#: ``adapters/mcp_server/tools.py`` 那一族里既有的量级取的 —— 它要的是「一次回执
#: 大得离谱时截断」，不是「把结果压到看得完」。
MAX_FEEDBACK = 2000


# ================================================================== 一、反馈事件
def draft_feedback(
    *,
    workspace: str,
    principal: str,
    idempotency_key: str,
    kind: FeedbackKind,
    literal_feedback: str = "",
    artifact_ref: str | None = None,
    source_session: str = "",
    target_ref: str | None = None,
    run_ref: str | None = None,
    workstream_ref: str | None = None,
    scope_kind: ScopeKind | str | None = None,
    scope_ref: str | None = None,
    at: datetime | None = None,
) -> FeedbackEvent:
    """把一次提交凑成一条 :class:`FeedbackEvent`。**推不出一个作用域就拒绝。**

    ``feedback_id`` 由 ``(工作区, principal, 幂等键)`` 算出来（``feedback_id_for``），
    所以「同一个人用同一把键说同一句话」是**同一件东西** —— 存储那一侧因此可以把
    「重放」与「新写」分开报，而调用方不需要自己先去查一次。

    ``at`` 缺省读一次时钟（与 ``application/review.py::case_for`` 同款）。

    Raises:
        ConfigError: 作用域推不出来（一格都没点名），或点名的作用域与点名的引用对不上。
            两条都由 ``resolve_scope`` 抛出，这一层**不接住、不给缺省** —— 理由见模块
            docstring。
    """
    moment = utc_now() if at is None else at
    return FeedbackEvent(
        feedback_id=feedback_id_for(
            workspace=workspace, principal=principal, idempotency_key=idempotency_key
        ),
        workspace=workspace,
        principal=principal,
        idempotency_key=idempotency_key,
        kind=kind,
        proposed_scope=resolve_scope(
            scope_kind=scope_kind,
            scope_ref=scope_ref,
            target_ref=target_ref,
            run_ref=run_ref,
            workstream_ref=workstream_ref,
        ),
        literal_feedback=literal_feedback,
        artifact_ref=artifact_ref,
        source_session=source_session,
        target_ref=target_ref,
        run_ref=run_ref,
        workstream_ref=workstream_ref,
        created_at=moment,
    )


def submit_feedback(*, store: FeedbackStore, event: FeedbackEvent) -> FeedbackAdmission:
    """把一条反馈放上盘。**这一行只写事件 —— 它不改任何策略。**

    ``admitted=False`` 是重放（同一个 principal 拿同一把幂等键说同一句话）。那**不是**
    一次失败，但它也不是一次新写入，而「今天新增了几条反馈」必须答得出来 —— 所以它由
    这一格分开报，不折成一个布尔。

    这一层一个判据都不加：能在这里拒的东西（类别、作用域、原话与产物引用至少有一个）
    都在领域里拒掉了，而**再判一遍是第二处定义**：它漂移的表现是「领域放行的东西
    编排层拒了」，两处各自都正常，读的人却只看得到一处。
    """
    return store.admit_feedback(event)


@dataclass(frozen=True)
class FeedbackReading:
    """``get_feedback`` 的全部内容：那一条，以及**此后在它之上又提了什么**。

    ``literal_feedback`` 原话**逐字**出去（目录行逐字：「原话**逐字**回，不折成摘要 ——
    折过的版本是这一侧的读法，而后来的人要判的是当初到底说了什么」）。所以
    :meth:`as_dict` 里没有 ``summary`` 这一格，也**不许**有人加：加的那一天，读的人就
    再也拿不到原话了。
    """

    event: FeedbackEvent
    #: 立在这条反馈上的候选（``source_feedback_ids`` 含它）。新的在前。
    candidates: tuple[ExperienceCandidate, ...]
    #: 以这条反馈为来源的补丁（``source_ref == feedback:<id>``）。新的在前。
    patches: tuple[PolicyPatch, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.event.feedback_id,
            "workspace": self.event.workspace,
            "principal": self.event.principal,
            "kind": self.event.kind.value,
            "ring": self.event.ring.value,
            "proposed_scope": self.event.proposed_scope.as_dict(),
            "literal_feedback": self.event.literal_feedback,
            "artifact_ref": self.event.artifact_ref,
            "source_session": self.event.source_session,
            "target_ref": self.event.target_ref,
            "run_ref": self.event.run_ref,
            "workstream_ref": self.event.workstream_ref,
            "created_at": self.event.created_at.isoformat(),
            "raised_on_it": {
                "candidates": [row.candidate_id for row in self.candidates],
                "patches": [row.patch_id for row in self.patches],
            },
        }


def read_feedback(
    *, store: FeedbackStore, feedback_id: str
) -> FeedbackReading | None:
    """读一条，连同**此后在它之上又提了什么**。没有这个 id 就返回 ``None``。

    「此后提了什么」由**两处**索引回答，而两处都要读：候选挂在
    ``source_feedback_ids`` 上（一条经验可以立在好几条反馈上），补丁挂在 ``source_ref``
    上（``feedback:<id>``，一次引用）。只读一处会让「这条反馈后来怎么样了」在两种
    提法下给出不同的答案，而两处各自都正常。

    这一遍读**不做跨工作区的拒绝**：那一条在存储里（``get_feedback`` 逐字），而这里
    两个列清单的读法本来就只看得到本工作区的行。
    """
    event = store.get_feedback(feedback_id)
    if event is None:
        return None
    wanted = feedback_source(feedback_id)
    return FeedbackReading(
        event=event,
        candidates=tuple(
            row
            for row in store.list_candidates()
            if feedback_id in row.source_feedback_ids
        ),
        patches=tuple(row for row in store.list_patches() if row.source_ref == wanted),
    )


def list_feedback(
    *,
    store: FeedbackStore,
    scope_kind: ScopeKind | str | None = None,
    scope_ref: str | None = None,
    limit: int | None = MAX_FEEDBACK,
) -> FeedbackPage:
    """按作用域筛，新的在前。**匹配到的条数与返回的条数一起报。**

    两个数分开给不是排版：目录行逐字「「只匹配到三条」与「匹配到三百条、这里显示三条」
    是两种完全不同的结论」。``matched`` 是**过滤之后的总数**，它在存储里与那一页
    在同一个读事务里算出来（端口逐字），所以两个数不会在两次查询之间漂移。
    """
    return store.list_feedback(scope_kind=scope_kind, scope_ref=scope_ref, limit=limit)


# ================================================================== 二、经验候选
def draft_candidate(
    *,
    workspace: str,
    observation: str,
    generalises: Scope,
    risk: RiskClass,
    proposed_by: str,
    source_feedback_ids: Sequence[str] = (),
    evidence_refs: Sequence[str] = (),
    note: str = "",
    at: datetime | None = None,
) -> ExperienceCandidate:
    """造一条候选。**状态永远是 ``PROPOSED`` —— 这个函数里没有第二个可能。**

    ``candidate_id`` 由 ``(工作区, 来源反馈, 观察)`` 算出来，而 ``generalises``
    **故意不在原像里**（``candidate_id_for`` 逐字）：改了「想推广到哪」是**同一件
    观察的另一次修订**（同一 id、更高的 ``revision``），不是另一条候选。原像里放
    它，会让同一句经验在盘上变成两条，而「这个观察被提过几次」这个数当场翻倍。

    ``generalises`` 不能是 ``workspace``（领域里的校验器逐字拒）：一条想推广到整个
    工作区的候选，是慢环里一步**做不到**的事 —— 那要一个**人**去开一件复核案。
    """
    moment = utc_now() if at is None else at
    refs = tuple(evidence_refs)
    return ExperienceCandidate(
        candidate_id=candidate_id_for(
            workspace=workspace,
            source_feedback_ids=tuple(source_feedback_ids),
            observation=observation,
        ),
        revision=1,
        workspace=workspace,
        state=CandidateState.PROPOSED,
        observation=observation,
        evidence_refs=refs,
        generalises=generalises,
        risk=risk,
        source_feedback_ids=tuple(source_feedback_ids),
        proposed_by=proposed_by,
        proposed_at=moment,
        updated_at=moment,
        note=note,
    )


def propose_candidate(
    *, store: FeedbackStore, candidate: ExperienceCandidate
) -> CandidateAdmission:
    """把一条候选放上盘。**它是提案不是批准** —— 这一层没有一处把它当权威读。

    ## 为什么这条要核它的来源

    一条 ``run_instruction`` 或 ``fact_correction`` 反馈**不得**先被叫成「经验」再变成
    一条候选。理由不是洁癖：候选是慢环的第一步，而慢环的出口是**一版策略**。于是
    「这一轮先别找 Owner」这句话可以经 候选 → 补丁 → 批准 变成一条**永久规则** ——
    而它在盘上每一步都完全正常，只有把首尾连起来看才看得出那句话不该长期生效。
    这正是 ``POLICY_PATCHABLE_KINDS`` 在补丁那一侧挡的事，而这里挡的是它的上游：
    只在补丁那一侧挡，一条非法候选还会被写进盘，成为一条此后每次读都看得见的、
    说得出名字的「经验」。

    两条拒绝的正文与 ``propose_patch`` 那一侧**同源**（都读
    ``RING_OF`` / ``POLICY_PATCHABLE_KINDS``），所以「换哪条路」那句话只有一处说法。

    Raises:
        ConfigError: 某一条来源反馈不在盘上（这个工作区），或它的类别是走事实路径 /
            眼前这一轮的那两类。
    """
    for feedback_id in candidate.source_feedback_ids:
        event = store.get_feedback(feedback_id)
        if event is None:
            raise ConfigError(
                f"这条候选立在这条反馈上：{feedback_id!r}，而它不在盘上（这个工作区）。\n"
                "一条立在一份取不到的反馈上的候选，在清单里读起来和一条来源正常的候选"
                "一样 —— 而它此后会被当成「有人说过」去读。\n"
                "先提交那条反馈，或者把这一条从 source_feedback_ids 里去掉。"
            )
        if event.kind not in POLICY_PATCHABLE_KINDS:
            raise ConfigError(
                f"{feedback_id!r} 是一条 {event.kind.value!r}，而它**不产生任何版本**，"
                f"所以它立不起一条候选。\n{_the_other_road(event.kind)}"
            )
    return store.put_candidate(candidate)


@dataclass(frozen=True)
class EvidenceReading:
    """一条证据引用读不读得出来。**读不出来与没有是两件事。**

    目录行逐字：「一条**读不出来的**证据引用如实报成读不出来，**不算作零** —— 把它
    读成零，会让一条立在一份取不到的候选上的候选看起来和一条真的没证据的候选一样」。
    """

    ref: str
    #: 这一条读得出来吗。
    readable: bool
    #: 读不出来时的原因（读得出来时是空串）。**点名缺的是哪一件**。
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"ref": self.ref, "readable": self.readable, "reason": self.reason}


@dataclass(frozen=True)
class CandidateReading:
    """``list_experience_candidates`` 的一行：状态、范围、证据条数。

    ## 三个数，不是一个

    ``evidence_total`` 是**写下时说的**条数（对象上的），``evidence_read`` 是**今天读
    得出来的**条数（存储核过一遍）。两个数分开报，是目录行那一句的落点：
    ``evidence_read == 0`` 而 ``evidence_total == 3`` 的意思是「三条引用一条都取不到」，
    它与「这条候选没立在任何证据上」（``evidence_total == 0``）是**两件完全不同的事**，
    而一个数会把它们压成一个 ``0``。
    """

    candidate: ExperienceCandidate
    evidence: tuple[EvidenceReading, ...]

    @property
    def evidence_total(self) -> int:
        """写下时说的条数。由 :attr:`evidence` **算**出来，不另存一格。"""
        return len(self.evidence)

    @property
    def evidence_read(self) -> int:
        """今天读得出来的条数。"""
        return sum(1 for row in self.evidence if row.readable)

    @property
    def unreadable(self) -> tuple[EvidenceReading, ...]:
        return tuple(row for row in self.evidence if not row.readable)

    def as_dict(self) -> dict[str, Any]:
        row = self.candidate
        return {
            "candidate_id": row.candidate_id,
            "revision": row.revision,
            "workspace": row.workspace,
            "state": row.state.value,
            "observation": row.observation,
            "generalises": row.generalises.as_dict(),
            "risk": row.risk.value,
            "source_feedback_ids": list(row.source_feedback_ids),
            "proposed_by": row.proposed_by,
            "proposed_at": row.proposed_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "note": row.note,
            "evidence_total": self.evidence_total,
            "evidence_read": self.evidence_read,
            "evidence": [item.as_dict() for item in self.evidence],
        }


def list_candidates(
    *, store: FeedbackStore, evidence: EvidenceStore
) -> tuple[CandidateReading, ...]:
    """候选清单，**每条候选的证据引用逐条核一遍**。

    ``evidence`` **是必填的**（与 ``application/review.py::resolve_case`` 的 ``scenes``
    同一条）：一个可以省略它的调用点，会在「这条候选立在哪份材料上」这件事上静默地
    什么都不查 —— 而那一格的默认读数（``0``）恰好是目录行点名要防的那个数。

    读不出来**不抛**：这一条读法报的是清单，而清单里的一条坏引用不该让整页读不成。
    它如实进 :attr:`CandidateReading.unreadable`，连同原因。
    """
    readings: list[CandidateReading] = []
    for candidate in store.list_candidates():
        readings.append(
            CandidateReading(
                candidate=candidate,
                evidence=tuple(
                    _read_evidence(evidence, ref) for ref in candidate.evidence_refs
                ),
            )
        )
    return tuple(readings)


def _read_evidence(store: EvidenceStore, ref: str) -> EvidenceReading:
    """一条证据引用读得出来吗。**跨工作区那一条也在这里折成一句可读的原因。**

    ``get_evidence`` 对跨工作区的那一行**抛** ``EvidenceError``（端口逐字，不是返回
    ``None``）。这一条读法不接住它就会让整页清单在一次坏引用上失败，而清单的用途
    恰恰是**看见**那条坏引用。所以这里接住，并把它的正文原样带上 —— 折成一句
    「取不到」会丢掉「是别人的行」这一件读的人必须知道的事。
    """
    try:
        found = store.get_evidence(ref)
    except Exception as exc:  # noqa: BLE001 - 见 docstring：坏引用不该让整页读不成
        return EvidenceReading(ref=ref, readable=False, reason=str(exc))
    if found is None:
        return EvidenceReading(
            ref=ref,
            readable=False,
            reason=(
                "这个工作区的证据存储里没有这个 id 的行。它与「这条候选没有证据」"
                "不是一件事 —— 后者是 evidence_total 为 0。"
            ),
        )
    return EvidenceReading(ref=ref, readable=True)


# ================================================================== 三、策略补丁
def draft_patch(
    *,
    workspace: str,
    source_ref: str,
    changes: Sequence[str],
    target_version: int,
    monitoring: str,
    rollback: str,
    risk: RiskClass,
    rationale: str,
    proposed_by: str,
    review_case_id: str | None = None,
    at: datetime | None = None,
) -> PolicyPatch:
    """造一份补丁。**状态由 ``review_case_id`` 决定，而它只能是那两档之一。**

    ``patch_id`` 由 ``(工作区, 来源, 目标版本)`` 算出来，而 ``changes`` **故意不在
    原像里**（``patch_id_for`` 逐字）：改了「改哪几格」是同一份提案的另一次修订
    （同一 id、更高的 ``revision``），不是另一份提案。原像里放它，会让「这份提案改过
    几次主意」无法回答 —— 每一次改主意都会新开一个 id，而旧的那份看起来像是另一件事。

    ``review_case_id`` 与 ``state`` 之间的那条对应（``review_case_id is None`` ⇔
    ``PROPOSED``）由领域里的校验器钉住 —— 一个挂上了案件却还写着「还没归案」的补丁，
    会让「它现在在等谁」有两个答案。
    """
    moment = utc_now() if at is None else at
    return PolicyPatch(
        patch_id=patch_id_for(
            workspace=workspace, source_ref=source_ref, target_version=target_version
        ),
        revision=1,
        workspace=workspace,
        state=(
            PolicyPatchState.PROPOSED
            if review_case_id is None
            else PolicyPatchState.AWAITING_APPROVAL
        ),
        review_case_id=review_case_id,
        source_ref=source_ref,
        changes=tuple(changes),
        target_version=target_version,
        monitoring=monitoring,
        rollback=rollback,
        risk=risk,
        rationale=rationale,
        proposed_by=proposed_by,
        proposed_at=moment,
        updated_at=moment,
    )


def propose_patch(
    *,
    store: FeedbackStore,
    patch: PolicyPatch,
    review: ReviewCaseStore | None = None,
) -> PatchAdmission:
    """把一份补丁放上盘。**它是提案 —— 批准在另一条路上，而且不在这里。**

    ## 三件在这一层才判得清的事

    1. **来源解析**。``source_ref`` 只有两种写法（``feedback:<id>`` 与
       ``candidate:<id>@<rev>``，两者的格式各只有一处定义，见 ``domain.feedback``）。
       别的写法一律拒 —— 一个写得像来源却不是来源的字符串，会让「这份提案是从哪来的」
       在读的时候变成一个自由文本字段。
    2. **它提不提得起补丁**。一条 ``fact_correction`` 或 ``run_instruction`` 反馈
       提不起（见模块 docstring 那两条逃逸通道）。一条候选则必须真的在盘上。
    3. **它归到的那件复核案**。给了 ``review_case_id`` 就要真的在盘上、且**还没关**
       （``PENDING_STATES``）；一件关掉的案不会再有决定，挂上去的补丁永远等不到那一条
       —— 而它在报告上只是一件挂着的事。

       再一条：那件案上**不能已经有一条等着被批准的提议**。``approve_policy_change``
       批的是「那一件上最后一条提议」，两份补丁挂在同一件上、各自声称产生了那一版，
       会让「这一版是谁提的」有两个答案 —— 那正是 ``pending_proposal`` 那条判据存在
       的理由，所以这里读它，而不另写一套「谁先谁后」。

    ``review`` 缺省 ``None``：**那不影响写** —— 一件案有没有关掉是盘上的事实，而这一层
    拿不到那个存储时唯一诚实的做法是**不假装核过**。给 ``None`` 时第三件事**不判**，
    并且交付面必须在读数里把「批准了没有」报成答不上来（``PatchStanding.UNREADABLE``），
    不能报成「还没批」。

    Raises:
        ConfigError: 来源写法不认识 / 来源不在盘上 / 类别提不起补丁 / 案件不在盘上 /
            案件已经关掉 / 案件上已经有一条等着批准的提议。
    """
    _check_source(store=store, patch=patch)
    if patch.review_case_id is not None:
        _check_case(store=review, patch=patch)
    return store.put_patch(patch)


def _check_source(*, store: FeedbackStore, patch: PolicyPatch) -> None:
    """``source_ref`` 指的是谁，以及它提不提得起补丁。"""
    if patch.source_ref.startswith("feedback:"):
        feedback_id = patch.source_ref.split(":", 1)[1]
        event = store.get_feedback(feedback_id)
        if event is None:
            raise ConfigError(
                f"这份补丁的来源是 {patch.source_ref!r}，而这条反馈不在盘上"
                "（这个工作区）。\n一件来源取不到的提案与一件来源正常的，在清单里"
                "读起来一样 —— 而后者此后会被当成「有人提过」去读。"
            )
        if patch.workspace != event.workspace:
            raise ConfigError(
                f"这份补丁写在工作区 {patch.workspace!r}，而它的来源 "
                f"{feedback_id!r} 属于 {event.workspace!r}。\n"
                "跨工作区的来源是一条读不回来的主张：它此后会被本工作区当成本地的事"
                "读回去，而写下它的那一刻没有任何一处会响。"
            )
        if event.kind not in POLICY_PATCHABLE_KINDS:
            raise ConfigError(
                f"{feedback_id!r} 是一条 {event.kind.value!r}，而它提不起一份策略补丁。\n"
                f"{_the_other_road(event.kind)}"
            )
        return
    if patch.source_ref.startswith("candidate:"):
        candidate_id, _, revision_text = patch.source_ref.split(":", 1)[1].partition("@")
        if not candidate_id or not revision_text.isdigit():
            raise ConfigError(
                f"{patch.source_ref!r} 像一个候选来源，但读不出它指的是哪一条的哪一版。\n"
                f"写法逐字是 {candidate_source('ec-0000000000000000', 1)!r} —— "
                "版本那一段不能省：同一个候选的两次修订是两件不同的事，"
                "而「这份提案立在那一版的观察上」此后必须答得出来。"
            )
        candidate = store.get_candidate(candidate_id, int(revision_text))
        if candidate is None:
            raise ConfigError(
                f"这份补丁的来源是 {patch.source_ref!r}，而这一条候选那一版不在盘上"
                "（这个工作区）。"
            )
        if patch.workspace != candidate.workspace:
            raise ConfigError(
                f"这份补丁写在工作区 {patch.workspace!r}，而它的来源 "
                f"{candidate_id!r} 属于 {candidate.workspace!r}。"
            )
        return
    raise ConfigError(
        f"{patch.source_ref!r} 不是一种来源写法。\n"
        f"今天只有两种：{feedback_source('fb-0000000000000000')!r}（一条反馈，"
        f"以 {FEEDBACK_ID_PREFIX!r} 开头）与 "
        f"{candidate_source('ec-0000000000000000', 1)!r}（一条候选的那一版）。\n"
        "别的写法一律拒：一个写得像来源却不是来源的字符串，会让「这份提案是从哪来"
        "的」在读的时候变成一个自由文本字段。"
    )


def _check_case(*, store: ReviewCaseStore | None, patch: PolicyPatch) -> None:
    """它归到的那件复核案，以及那件案上是不是已经有别的提议在等批准。"""
    if store is None:
        return
    case_id = patch.review_case_id
    assert case_id is not None  # 调用点已经判过
    case = store.get_case(case_id)
    if case is None:
        raise ConfigError(
            f"这份补丁归到的复核案 {case_id!r} 不在盘上。\n"
            "归不到案的补丁不会自动变成一件待办：这一片不新开复核案，也不新开第二条"
            "批准路径 —— 一件案由一个人（或 B20 的拒载收件箱）开出来。"
        )
    if case.workspace != patch.workspace:
        raise ConfigError(
            f"这份补丁写在工作区 {patch.workspace!r}，而复核案 {case_id!r} 属于 "
            f"{case.workspace!r}。"
        )
    if case.state not in PENDING_STATES:
        raise ConfigError(
            f"复核案 {case_id!r} 已经是 {case.state.value!r} —— 它不会再有任何决定，"
            "所以挂上去的补丁永远等不到那一条。\n"
            "而它在报告上只是一件挂着的事，没有一处会响。挂到一件还没关的案上。"
        )
    existing = pending_proposal(store=store, case_id=case_id)
    if existing is not None:
        raise ConfigError(
            f"复核案 {case_id!r} 上已经有一条等着被批准的提议（{existing.by!r} 提的"
            f"第 {existing.proposed_version} 版）。\n"
            "``approve_policy_change`` 批的是**那一件上最后一条提议**，所以两份补丁"
            "挂在同一件上、各自声称产生了那一版，会让「这一版是谁提的」有两个答案。\n"
            "挂到另一件还没提议过的案上，或者先让那一件走完。"
        )


class PatchStanding(str, Enum):
    """一份补丁**批准了没有**。**四档，不是两档。**

    第四档是承重的：见模块 docstring「四档，不是两档」。
    """

    #: 还没归到任何一件复核案上 —— 今天它连被批准的资格都没有。
    NOT_ATTACHED = "not_attached"
    #: 挂在一件还没关的案上，而那条批准决定还没出现。
    WAITING = "waiting"
    #: 那一件案上有一条 ``APPROVE_POLICY_CHANGE``，且批的人**不是**提议的人。
    APPROVED = "approved"
    #: 没有给复核存储，所以这个问题在这里答不上来。**不是 WAITING。**
    UNREADABLE = "unreadable"


class PatchApplication(str, Enum):
    """一份补丁**应用了没有**。**五档，而它是一次执行的结果，不是一个状态标签。**

    ## 它为什么必须与 :class:`PatchStanding` 分开

    「批准了」与「生效了」是两件事，而它们最容易同形的地方正是这里：批准是一条写在
    复核历史里的决定，生效是**场景记录上 ``effective_version`` 真的等于那一版**。
    把后者从前者**推**出来（「批过了，所以第 N 版生效了」）读起来很自然，而它是一条
    没有执行者的主张 —— 第 N 版可能从来没被写过，也可能写下了却没人激活。

    这一档存在的理由就是那条差别：它去**问盘**（``SceneStore``），而不是去问那件案子上
    有没有一条批准。

    ## 为什么 ``UNREADABLE`` 不在 ``NOT_APPROVED`` 里面

    与 :class:`PatchStanding` 的第四档逐字同一条：把「没查」折进「还没到那一步」，
    会让一个只是没接上场景存储的部署读起来像「这些补丁都还没生效」—— 而它与
    「真的一版都没生效」的处置相反（前者去接线，后者去催那一版）。
    """

    #: 还没批准 —— 所以谈不上应用。
    NOT_APPROVED = "not_approved"
    #: 批准了，而这一层**没有拿到场景存储**，所以「应用了没有」答不上来。
    UNREADABLE = "unreadable"
    #: 批准了，而 ``target_version`` 那一版在盘上**根本不存在**。
    NOT_PRODUCED = "not_produced"
    #: 那一版在盘上，但它**不是当前的生效版** —— 下一次 Run 不会用到它。
    PRODUCED = "produced"
    #: 那一版在盘上，**而且就是当前的生效版**。
    EFFECTIVE = "effective"


@dataclass(frozen=True)
class PatchApplicationReading:
    """一份补丁**应用到了哪一步**，以及**怎么撤回它**。

    ## 撤回为什么可以从这一层答出来，而不是从提案那串自由文本里读

    ``PolicyPatch.rollback`` 是一个 ``str``（人写给下一个读的人的处置说明），而
    「退回哪一版」是一件**在盘上有答案**的事：``SceneVersion.supersedes`` 的校验器
    逐字钉住它必须等于 ``version - 1``（``domain/scene.py`` 的
    ``_supersedes_is_the_previous_one``），所以「第 N 版撤回之后是哪一版」不是这一层
    猜的，是**那一版自己说的**。这不是一处新语义 —— ``docs/SCENE_EVOLUTION.md`` §5
    Step 5 逐字「Old versions remain replayable and rollbackable」，而执行那个动词的
    原语也已经在既有路径上（``SceneRecord.activate``，经 ``control_scene``）。

    把撤回目标定成「第 N-1 版」而不是去**解析**那句自由文本，是因为后者的失败形状
    是静默的：一句读不出版本号的说明会让撤回变成一次猜。
    """

    application: PatchApplication
    #: 这一档是怎么来的。**每一句都说得出口** —— 包括「没查」那一档。
    reason: str
    #: 撤回这一版要激活哪一版（= ``SceneVersion.supersedes``）。没有可撤的版本时 ``None``。
    rollback_version: int | None = None
    #: 那一版今天**能不能**被激活（记录在、没归档、版本号在范围内）。
    rollback_possible: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "application": self.application.value,
            "application_reason": self.reason,
            "rollback_version": self.rollback_version,
            "rollback_possible": self.rollback_possible,
        }


@dataclass(frozen=True)
class PatchReading:
    """``list_policy_patches`` 的一行：状态、在等哪一个复核案，**那一版生效了没有**，
    以及**已生效的**那一份产生了哪一版、那一版带着什么监控与回滚路径。

    ## ``monitoring`` / ``rollback`` 为什么与 ``produced_version`` 绑在一起报

    目录行逐字：「回滚路径与版本一起报，是因为一个不能回滚的策略版本和一个能回滚的，
    在部署那一刻是两件事」。所以在**没有生效**的时候，``produced_version`` 是
    ``None``，而这两格**也是 ``None``** —— 它们不是「这份提案打算带着什么」，
    它们是「**已经生效的那一版**带着什么」。提案自己写的那两条在
    :attr:`declared_monitoring` / :attr:`declared_rollback` 上，两处名字不同、含义不同，
    所以读的人不会把一份还没批的提案读成一版带着回滚路径的规则。

    ## 「已生效」为什么是一个**执行结果**（这一格曾经是坏的）

    这三格原先只问 ``standing is APPROVED``，而 ``standing`` 读的是**复核案上有没有
    一条批准决定** —— 于是「第 4 版生效了」这句话在盘上只需要一件案子就说得出口，
    而那一版可以从来没被写过。实测过一次：接上真的 ``SceneStore`` 之后，
    ``produced_version`` 报 ``4``，而 ``scene_version`` 表里没有第 4 版、
    ``scene`` 表里连那条记录都没有。

    现在这三格读 :attr:`application`，而它是去问 ``SceneStore`` 得到的。两处的
    差别不在措辞上：一条批准**不产生**任何一版，产生一版的是
    ``add_scene_version`` + ``control_scene(activate)``，而这一层现在报的是后者**真的
    发生过**。
    """

    patch: PolicyPatch
    standing: PatchStanding
    #: 这一档是怎么来的。**每一句都说得出口** —— 包括「没查」那一档。
    standing_reason: str
    #: 批它的那个人。没批、没查、没归案时是 ``None``。
    approved_by: str | None = None
    #: 那一版应用到了哪一步，以及怎么撤回。见 :class:`PatchApplicationReading`。
    applied: PatchApplicationReading | None = None

    @property
    def application(self) -> PatchApplication:
        """**应用了没有**。没报过就是「还没批准」（见 :class:`PatchApplication`）。"""
        if self.applied is None:  # pragma: no cover - 三处构造点都给了它
            return PatchApplication.NOT_APPROVED
        return self.applied.application

    @property
    def produced_version(self) -> int | None:
        """**已生效的**那一份产生了哪一版。没生效时没有这一版。

        判据是 :attr:`application` 落在 ``EFFECTIVE`` 上 —— 那一版**真的在盘上**，
        而且 ``SceneRecord.effective_version`` 就是它。批准了但没写下、写下了但没激活，
        在这里都是 ``None``，理由在 :attr:`application_reason` 里点名。
        """
        return (
            self.patch.target_version
            if self.application is PatchApplication.EFFECTIVE
            else None
        )

    @property
    def monitoring(self) -> str | None:
        """已生效那一版带着的监控。**没生效时是 ``None``**（见类 docstring）。"""
        return self.patch.monitoring if self.produced_version is not None else None

    @property
    def rollback(self) -> str | None:
        """已生效那一版带着的回滚路径。**没生效时是 ``None``**。"""
        return self.patch.rollback if self.produced_version is not None else None

    @property
    def application_reason(self) -> str:
        """应用那一档是怎么来的。``applied`` 缺省时是一句说得出的话。"""
        if self.applied is None:  # pragma: no cover - 三处构造点都给了它
            return "这一份补丁的读数没有带上应用那一档。"
        return self.applied.reason

    @property
    def rollback_version(self) -> int | None:
        """**撤回要激活哪一版。** 读的是那一版自己的 ``supersedes``，不是自由文本。

        B23 的关闭条件逐字要系统回答「**如何撤回**」—— 这句话此前只落在
        ``PolicyPatch.rollback`` 那句自由文本上（唯一判据是「非空」）。这一格是它的
        机器可读版本：撤回到哪一版、以及撤不撤得回去。
        """
        return None if self.applied is None else self.applied.rollback_version

    @property
    def rollback_possible(self) -> bool:
        """**这一版撤不撤得回去。** 撤回目标在盘上、场景没归档、且在版本范围内。"""
        return False if self.applied is None else self.applied.rollback_possible

    def as_dict(self) -> dict[str, Any]:
        row = self.patch
        return {
            "patch_id": row.patch_id,
            "revision": row.revision,
            "workspace": row.workspace,
            "state": row.state.value,
            "review_case_id": row.review_case_id,
            "source_ref": row.source_ref,
            "changes": list(row.changes),
            "target_version": row.target_version,
            "risk": row.risk.value,
            "rationale": row.rationale,
            "proposed_by": row.proposed_by,
            "proposed_at": row.proposed_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
            "standing": self.standing.value,
            "standing_reason": self.standing_reason,
            "approved_by": self.approved_by,
            # 已生效那一版的三格。没生效时是 null，而不是「提案打算带的」——
            # 后一种读法会把一份还没批的提案读成一版带着回滚路径的规则。
            "produced_version": self.produced_version,
            "monitoring": self.monitoring,
            "rollback": self.rollback,
            # 提案自己写的那两条。**名字不同，含义不同**（见类 docstring）。
            "declared_monitoring": row.monitoring,
            "declared_rollback": row.rollback,
            # 那一版应用到了哪一步、以及怎么撤回（见 PatchApplicationReading）。
            **(
                PatchApplicationReading(
                    application=PatchApplication.NOT_APPROVED, reason=""
                ).as_dict()
                if self.applied is None
                else self.applied.as_dict()
            ),
        }


def list_patches(
    *,
    store: FeedbackStore,
    review: ReviewCaseStore | None = None,
    scenes: SceneStore | None = None,
) -> tuple[PatchReading, ...]:
    """补丁清单，**每一份的批准读数与它的应用读数逐份摊开**。

    ``review`` 缺省 ``None`` 时，那一档报 ``UNREADABLE`` 并点名缺的是哪个存储 ——
    **不报成「还没批」**。``scenes`` 缺省 ``None`` 时，**应用**那一档同样报
    ``UNREADABLE``。两个缺省各自点名自己缺的那个存储：理由见模块 docstring 与
    :class:`PatchStanding` / :class:`PatchApplication`。
    """
    return tuple(
        standing_of(patch=patch, review=review, scenes=scenes)
        for patch in store.list_patches()
    )


def standing_of(
    *,
    patch: PolicyPatch,
    review: ReviewCaseStore | None,
    scenes: SceneStore | None = None,
) -> PatchReading:
    """一份补丁的批准读数。**四档各有各的话，包括「没查」。**

    **公开的**（不在名字前加下划线）是因为它有两个调用点，而两个必须是同一份判据：
    :func:`list_patches` 推清单时用它，``propose_policy_patch`` 刚写完那一份时也用它 ——
    后者不是在「顺便读一下」，它是为了让**刚提完看到的那一档**与随后在清单里看到的那一档
    是同一句话。两处各算一次的表现是它们不一样，而两条读数各自都读得通，所以没有一处会响。

    ``scenes`` 是**应用那一步**的存储（见 :class:`PatchApplication`）。缺省 ``None``
    时应用那档报 ``UNREADABLE``，而**批准那档照旧** —— 两个问题各自的答案互不算数。
    """
    if patch.review_case_id is None:
        return PatchReading(
            patch=patch,
            standing=PatchStanding.NOT_ATTACHED,
            standing_reason=(
                "它还没归到任何一件复核案上（``review_case_id`` 是空的）。\n"
                "所以今天它连被批准的资格都没有：``approve_policy_change`` 批的是"
                "**一件案上的一条提议**，而这一份没有案。\n"
                "今天没有生产者会为一条反馈开出这样一件案 —— 见报告的 WIRING-REQUEST："
                "它要么归到一件已有的、还没关的案上，要么等那一条接线。"
            ),
            applied=_not_applied("它还没归案，所以谈不上批准，也谈不上应用。"),
        )
    if review is None:
        return PatchReading(
            patch=patch,
            standing=PatchStanding.UNREADABLE,
            standing_reason=(
                "这一份归到了复核案 "
                f"{patch.review_case_id!r}，但这一层**没有拿到复核存储** —— "
                "所以「它批了没有」在这里答不上来。\n"
                "这一档**不是**「还没批」：把没查折进还没批，会让一个只是没接上存储的"
                "部署读起来像「这些补丁都在等着人」，而它此后会被当成一条真实的待办"
                "队列去催。缺的是 ReviewCaseStore（见 WIRING-REQUEST）。"
            ),
            applied=PatchApplicationReading(
                application=PatchApplication.UNREADABLE,
                reason=(
                    "「批准了没有」在这里都答不上来（没拿到复核存储），"
                    "而批准是应用的前一步 —— 所以这一档也答不上来。\n"
                    "它**不是**「还没生效」：那一档要说得出「批了、而那一版不在盘上」，"
                    "而这一层连批没批都没读到。"
                ),
            ),
        )
    case = review.get_case(patch.review_case_id)
    if case is None:
        return PatchReading(
            patch=patch,
            standing=PatchStanding.UNREADABLE,
            standing_reason=(
                f"这一份归到了复核案 {patch.review_case_id!r}，而那一件不在盘上。\n"
                "一件读不到的案与一件还没批的案，在报告上长得一样 —— 所以这一档是"
                "「答不上来」，不是「还没批」。"
            ),
            applied=PatchApplicationReading(
                application=PatchApplication.UNREADABLE,
                reason=(
                    "那一件案读不到，所以「批了没有」与「应用了没有」都答不上来。\n"
                    "读不到的场景还有一个后果：**这一份补丁连一个场景都指不出来** —— "
                    "它的 ``target_version`` 要靠案件上的 ``scene_id`` 才落得到具体"
                    "某个场景的版本上（``PolicyPatch`` 自己没有 ``scene_id``）。"
                ),
            ),
        )
    approval = _approval_on(case=case, review=review, patch=patch)
    if approval is None:
        return PatchReading(
            patch=patch,
            standing=PatchStanding.WAITING,
            standing_reason=(
                f"它归在 {case.case_id!r} 上，那一件现在是 {case.state.value!r}，"
                "而上面还没有一条批准这一版的 ``APPROVE_POLICY_CHANGE``。\n"
                "批准走既有的 approve_policy_change，角色 _AUTHORIZE，"
                "而且要**另一个人** —— 这一片不新开第二条批准路径。"
            ),
            applied=_not_applied(
                "它还没批 —— 而批准是应用的前一步。一份还没批的提案在任何场景上"
                "都不该产生一版，所以这一档**不是**「产了但没生效」。"
            ),
        )
    return PatchReading(
        patch=patch,
        standing=PatchStanding.APPROVED,
        standing_reason=(
            f"{approval.by!r} 在 {case.case_id!r} 上批准了第 "
            f"{approval.proposed_version} 版，而提议的是 {patch.proposed_by!r} —— "
            "不是同一个人。上面那三条监控与回滚路径随提案一起被批准（复核案上没有"
            "第二份）。"
        ),
        approved_by=approval.by,
        # 批准**不产生**任何一版：产生一版的是 add_scene_version +
        # control_scene(activate)。所以应用那一档要另去问场景存储。
        applied=_application_of(patch=patch, scenes=scenes, scene_id=case.scene_id),
    )


def _not_applied(reason: str) -> PatchApplicationReading:
    """还没走到「批了」那一步的那几档共用的一条应用读数。"""
    return PatchApplicationReading(application=PatchApplication.NOT_APPROVED, reason=reason)


def _application_of(
    *, patch: PolicyPatch, scenes: SceneStore | None, scene_id: str
) -> PatchApplicationReading:
    """``target_version`` 那一版**在盘上真的存在吗、真的生效了吗**。见 :class:`PatchApplication`。

    三问，每一问都去问 ``SceneStore``（而不是去问那件案子上有没有一条批准）：

    1. ``get_version(scene_id, target_version)`` 读得到吗 —— 读不到就是
       :attr:`PatchApplication.NOT_PRODUCED`。这一条是原先缺的那一步：
       ``_require_scene_version`` 在**提议**那条路上已经核过一次
       （``application/review.py:260``），而**补丁**这一路从来没核过，于是
       ``produced_version`` 可以报一个盘上不存在的版本号。
    2. 记录上 ``effective_version`` 是它吗 —— 是就是 :attr:`PatchApplication.EFFECTIVE`。
    3. 撤回要激活哪一版 —— 读的是 ``SceneVersion.supersedes``（那一版**自己**说的，
       校验器钉住它必须等于 ``version - 1``），不是解析 ``PolicyPatch.rollback``
       那句自由文本。
    """
    if scenes is None:
        return PatchApplicationReading(
            application=PatchApplication.UNREADABLE,
            reason=(
                "这一份已经批过了，但这一层**没有拿到场景存储** —— 所以「那一版在不在"
                "盘上、是不是当前的生效版」在这里答不上来。\n"
                "这一档**不是**「还没产版」：把没查折进没产版，会让一个只是没接上存储"
                "的部署读起来像「这些补丁都还没落地」，而两者的处置相反（前者去接线，"
                "后者去写那一版）。缺的是 SceneStore（见 WIRING-REQUEST）。"
            ),
        )
    version = scenes.get_version(scene_id, patch.target_version)
    if version is None:
        return PatchApplicationReading(
            application=PatchApplication.NOT_PRODUCED,
            reason=(
                f"它已经批了，而场景 {scene_id!r} 的第 {patch.target_version} 版"
                "**在盘上不存在**。\n"
                "批准本身不产生任何一版：产生一版的是 ``add_scene_version``（写下"
                "这一版，内容由人给 —— ``changes`` 装的是格子名，不是值）加 "
                "``control_scene(action='activate', version=...)``。\n"
                "所以这一档**不是**「已经生效了」：读的人此前看到的那一版从来没有"
                "被写下来过，而它在报告上与真的生效了一版长得一样。"
            ),
        )
    record = scenes.get_record(scene_id)
    effective = None if record is None else record.effective_version
    if record is None:
        return PatchApplicationReading(
            application=PatchApplication.PRODUCED,
            reason=(
                f"场景 {scene_id!r} 的第 {patch.target_version} 版在盘上，"
                "而这一条**记录**读不到 —— 没有记录就没有 ``effective_version``，"
                "所以它一版都没生效过。\n"
                "两个读法各自都正常，合起来才是「产了这一版，而没有一版在生效」。"
            ),
        )
    if effective != patch.target_version:
        return PatchApplicationReading(
            application=PatchApplication.PRODUCED,
            reason=(
                f"场景 {scene_id!r} 的第 {patch.target_version} 版在盘上，"
                f"而当前的生效版是 {effective!r} —— **下一次 Run 用的不是你这一版**。\n"
                "要让它生效请调 ``control_scene(action='activate', "
                f"version={patch.target_version})``：那一步会记下批准人，"
                "而这里不会。"
            ),
        )
    rollback = version.supersedes
    #: 撤回要激活的那一版**在盘上**吗。``SceneRecord.activate`` 自己只查
    #: ``1 <= version <= latest_version``，不查那一版是否存在 —— 于是「激活一个
    #: 从没写过的版本号」在它那里是能成的，而结果是 ``effective_version`` 指向一个
    #: 取不到的版本。这一格报的是**能不能真的退回去**，所以那一版必须真的在盘上。
    rollback_exists = (
        rollback is not None and scenes.get_version(scene_id, rollback) is not None
    )
    reachable = (
        rollback is not None
        and record.state is not SceneState.ARCHIVED
        and 1 <= rollback <= record.latest_version
    )
    if rollback_exists and reachable:
        how = (
            f"撤回要激活第 {rollback} 版（读的是这一版自己的 ``supersedes``，"
            "不是 ``declared_rollback`` 那句自由文本），而那一版就在盘上。"
        )
    elif rollback is None:
        how = (
            "**这一版撤不回去**：它的 ``supersedes`` 是空的 —— 它是这个场景的第一版，"
            "没有上一版可退。"
        )
    elif record.state is SceneState.ARCHIVED:
        how = (
            "**这一版撤不回去**：场景已归档，而归档是终态（``SceneRecord.activate`` "
            "会拒）。"
        )
    elif not rollback_exists:
        how = (
            f"**这一版撤不回去**：``supersedes`` 指的是第 {rollback} 版，而那一版"
            "**不在盘上** —— 激活一个从没写过的版本号会让生效版指向一个取不到的版本。"
        )
    else:
        how = (
            f"**这一版撤不回去**：``supersedes`` 指的是第 {rollback} 版，而它超出了"
            f"这个场景写到第 {record.latest_version} 版的范围。"
        )
    return PatchApplicationReading(
        application=PatchApplication.EFFECTIVE,
        reason=(
            f"场景 {scene_id!r} 的第 {patch.target_version} 版在盘上，"
            f"且 ``effective_version`` 就是它 —— 下一次 Run 用的就是这一版。\n" + how
        ),
        rollback_version=rollback,
        rollback_possible=rollback_exists and reachable,
    )


def _approval_on(
    *, case: ReviewCase, review: ReviewCaseStore, patch: PolicyPatch
):
    """那一件案上**批准了这一版**的那条决定，或 ``None``。

    三条一起判，缺一条都不算：

    * 结果必须是 :data:`~marketops.domain.review.POLICY_APPROVAL_OUTCOMES` 里的
      （「批准」是它，别的决定都不是）；
    * 它批的版本必须是**这一份补丁的目标版本** —— 一件案上可能有别的提议，
      而把「那件案批过东西」读成「这份补丁生效了」会让一份还没批的提案提前生效；
    * 批的人不能是提议的人（``approve_policy_change`` 已经拦了这一条，这里是**读**
      的时候再核一遍：读法必须与写法在不同的地方各拒一次，否则一条绕过写入的旧行
      会在读数里变成一条合法批准）。

    ## 「它批的是哪一版」为什么只能从**历史**里读

    批准那一行自己的 ``proposed_version`` 是 ``None``，而且**必须是** ``None``：
    ``HumanResolution._a_proposal_carries_the_version_it_suspends`` 把那一格与
    ``POLICY_PROPOSAL_OUTCOMES`` **绑定**（不说明挂了哪一版的提议会让闸门不知道该拦
    谁；反过来会让那一版永远等不到批准）。所以一条 ``APPROVE_POLICY_CHANGE`` 携带版
    本号是一个**构造不出来**的对象。

    于是「它批的是哪一版」有且只有一个来源：**它前面那条提议**。按 ``list_resolutions``
    的时间顺序扫，记下最近一条 ``PROPOSE_POLICY_CHANGE`` 挂起的版本，批准那一行批的
    就是它（``application/review.py::pending_proposal`` 逐字：「批的是**那一件上最后一条
    提议**」）。

    案件上那一格（``ReviewCase.proposed_version``）**不能**当这个来源：批准会把案件推进
    ``RESOLVED``，而 ``domain.review.apply_resolution`` 在关闭时把它清成 ``None`` ——
    「第 4 版当初是谁批的」此后只有这张只增不改的历史表答得出来（``ports/review.py``
    的 ``list_resolutions`` 逐字写了这件事）。

    **这一条判据曾经是坏的**：它比的是 ``row.proposed_version != patch.target_version``，
    而批准那一行的那一格恒为 ``None`` —— 于是那一条 ``continue`` 吃掉了**每一条**
    批准，``PatchStanding.APPROVED`` 是一个永远不可达的成员。盘上一切照旧：一份已经
    被批准过的补丁读出来是 ``WAITING``，而它与「还没批」在报告上是同一句话。
    """
    suspended: int | None = None
    for row in review.list_resolutions(case.case_id):
        if row.outcome in POLICY_PROPOSAL_OUTCOMES:
            suspended = row.proposed_version
            continue
        if row.outcome not in POLICY_APPROVAL_OUTCOMES:
            continue
        if suspended != patch.target_version:
            continue
        if row.by == patch.proposed_by:
            continue
        return row
    return None


def _the_other_road(kind: FeedbackKind) -> str:
    """「这一类该走哪条路」那一句话。**一处定义，两个提法共用。**

    ``propose_candidate`` 与 ``propose_patch`` 拒的是同一件事的两端（候选的上游与
    补丁的下游），而它们要报给读的人的**下一步**是同一句话。各写一份的表现是：
    同一个类别在两处给出两条不同的建议，而两条各自都读得通。
    """
    if kind is FeedbackKind.FACT_CORRECTION:
        return (
            "事实更正走的是**已有**的事实/证据路径（put_evidence → "
            "application/evidence_refs.py，再定向重评受影响的对象）—— "
            "本层不改它，也不另起一套。\n"
            "让一条事实更正长出一条策略提案，等于给同一条事实造出第二条生效路径，"
            "而两条路径分家的那天，「这条事实改了没有」会有两个答案。"
        )
    if kind is FeedbackKind.RUN_INSTRUCTION:
        return (
            "一轮指示改的是**眼前这一轮**（处置逐字：「仅未来未派发的本 Run/后续 Run "
            "计划；在途 attempt 保持冻结」）—— 它的有效期就是这一轮。\n"
            "让它长出一条策略提案，等于让一次临时指示在下一轮静默地继续生效，"
            "而它在报告上只是一条已经生效的规则。"
        )
    # 剩下的三类都提得起（`POLICY_PATCHABLE_KINDS`）。走到这里只可能是调用点在
    # 判据之外自己编了一个 kind —— 那时报的必须是「没人想过」，不是「换一条路」。
    return (
        f"{kind.value!r} 不在 POLICY_PATCHABLE_KINDS 里，而它也不属于已知的两条"
        "逃逸通道（事实路径 / 眼前这一轮）。\n"
        "这是一处**没人想过**：新加了一个反馈类别，而「它提不提得起补丁」还没有"
        "被回答。补一处 —— domain.feedback 的 DISPOSITION 与 VERSION_PRODUCING_KINDS。"
    )


__all__ = [
    "MAX_FEEDBACK",
    "CandidateReading",
    "EvidenceReading",
    "FeedbackReading",
    "PatchApplication",
    "PatchApplicationReading",
    "PatchReading",
    "PatchStanding",
    "draft_candidate",
    "draft_feedback",
    "draft_patch",
    "list_candidates",
    "list_feedback",
    "list_patches",
    "propose_candidate",
    "propose_patch",
    "read_feedback",
    "standing_of",
    "submit_feedback",
]

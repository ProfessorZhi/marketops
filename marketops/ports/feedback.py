"""反馈环的存储端口 —— 三件对象的读写（§B23）。

## 一个端口装三件对象，而它们是一件事

``feedback_event`` / ``experience_candidate`` / ``policy_patch`` 是**同一条链**上的
三站（一条反馈 → 一条可重复的经验 → 一份策略提案），而它们的读者也重叠：``get_feedback``
要报「此后在它之上又提了什么」，``list_policy_patches`` 要报「它在等哪件复核案、
它的来源是什么」。拆成三个端口会让那条链在三处各有一半，而「这条反馈后来怎么样了」
要跨三个端口才答得出来。

它与 ``ports/review.py`` 的分工是**两条不同的链**：收件箱那一边的对象是**人**的两件
（待办与决定），这一边的是**意见**的三件。两者在 ``policy_patch.review_case_id`` 上
相接 —— 而那是**一次引用**，不是一次合并：补丁不改案件，案件也不知道补丁的存在。

## 哪些动词是内容寻址的

* ``admit_feedback`` **是**：``feedback_id`` 就是 ``(工作区, principal, 幂等键)`` 的
  哈希，所以「同一个幂等键配同一句话」是**同一件事**，重放不报错（而且必须拿回
  同一行）；配的是不同的话，则是一次真实的冲突，报 ``IdempotencyConflictError``。
* ``put_candidate`` / ``put_patch`` **是**：两者的 id 都由它们**自己那件东西**算
  出来（``domain/feedback.py`` 的三个 ``*_id_for``）。同一把键配不同内容报错，而不是
  静默地并存两条 —— 静默并存的那一天，「这条候选想推广到哪」会有两个答案，而两处
  各自都正常。
* **读**不报错（除了跨工作区那一条，见下）：``get_*`` 的 ``None`` 只有一个意思 ——
  没有这个 id 的行。

## 一个存储只装一个工作区

与 ``ports/store.py`` 逐字同一条：写别人的被 ``StoreError`` 拒，读别人的报
``EvidenceError`` 式的拒绝**而不是跳过那一行**。跳过之后一个只装着别人那些行的
工作区返回的是空列表，而它与「这个工作区确实没有反馈」在调用点长得完全一样 ——
越权读因此变成一次静默成功。

## 端口里没有「改」这个动词

三件对象都是**只增**的：一条反馈说下了就是那么说的；一条候选的修订走更高的
``revision``（新的一行，不是就地改）；一份补丁批准之后不可变。所以这里没有
``update_*``，也没有 ``delete_*`` —— 一个不存在的入口比一个存在但写着注意事项的
入口可靠（与 ``ports/budget.py`` 没有 ``release`` 是同一条）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..domain.feedback import (
    ExperienceCandidate,
    FeedbackEvent,
    PolicyPatch,
    ScopeKind,
)

__all__ = [
    "CandidateAdmission",
    "FeedbackAdmission",
    "FeedbackPage",
    "FeedbackStore",
    "PatchAdmission",
]


@dataclass(frozen=True)
class FeedbackAdmission:
    """``admit_feedback`` 的结果。**两个字段都必要**（与 ``CaseAdmission`` 同一条）。

    ``admitted=False`` 是「同一个人用同一把幂等键说过同一句话，它已经在盘上了」。
    那**不是**一次失败 —— 它是幂等键存在的全部理由；但它也不是一次新的写入，而
    「今天新增了几条反馈」这个问题必须答得出来。只看 ``event`` 读不出这个区别。
    """

    #: 盘上那一行 —— 新写的，或**已经在盘上的那一行**。
    event: FeedbackEvent
    #: 这一次调用**是不是**它被写上去的那一次。
    admitted: bool

    @property
    def replayed(self) -> bool:
        return not self.admitted


@dataclass(frozen=True)
class FeedbackPage:
    """``list_feedback`` 的一页。**匹配到的条数与返回的条数分开给。**

    冻结表与目录行逐字：「**匹配到的条数与返回的条数一起报**。两个数分开给，是因为
    「只匹配到三条」与「匹配到三百条、这里显示三条」是两种完全不同的结论」。所以
    ``matched`` 是**过滤之后的总数**（不受 ``limit`` 影响），``events`` 才受它影响。
    """

    #: 符合筛选条件的**总**条数。
    matched: int
    #: 这一次真的返回的那几条，新的在前。
    events: tuple[FeedbackEvent, ...]

    @property
    def returned(self) -> int:
        """返回了几条。由 :attr:`events` **算**出来，不另存一格。"""
        return len(self.events)

    @property
    def truncated(self) -> bool:
        """这一页是不是被截断了。与 ``list_scenes`` 的 ``truncation`` 同一件事。"""
        return self.matched > self.returned


@dataclass(frozen=True)
class CandidateAdmission:
    """``put_candidate`` 的结果。形状与 :class:`FeedbackAdmission` 逐字相同。"""

    candidate: ExperienceCandidate
    admitted: bool

    @property
    def replayed(self) -> bool:
        return not self.admitted


@dataclass(frozen=True)
class PatchAdmission:
    """``put_patch`` 的结果。形状与 :class:`FeedbackAdmission` 逐字相同。"""

    patch: PolicyPatch
    admitted: bool

    @property
    def replayed(self) -> bool:
        return not self.admitted


class FeedbackStore(ABC):
    """反馈、候选与补丁的持久化。"""

    # ------------------------------------------------------------ 反馈
    @abstractmethod
    def admit_feedback(self, event: FeedbackEvent) -> FeedbackAdmission:
        """把一条反馈放上盘。**同一个幂等键第二次调用拿回同一行。**

        ``admitted=False`` 时盘上那一行的 ``created_at`` 与本次传入的**不同**
        （重发当然发生在更晚的时刻），而其余内容必须逐字相同 —— 不同的那一次抛
        :class:`~marketops.domain.errors.IdempotencyConflictError`，理由是
        ``domain/errors.py`` 那一格逐字的：「同一个幂等键配了不同的输入……
        后者必须显性失败」。静默保留先写的那条，会让「我提交的那句话被记下了」
        与「盘上那条是别的什么」变成两件事，而两次调用各自都正常。

        Raises:
            IdempotencyConflictError: 同一把键配了不同的话。
            StoreError: 这一行属于另一个工作区，或内容寻址被绕过。
        """
        raise NotImplementedError

    @abstractmethod
    def get_feedback(self, feedback_id: str) -> FeedbackEvent | None:
        """按 id 读一条。``None`` 只有一个意思：**没有这个 id 的反馈**。

        与 ``get_evidence`` 逐字同一条：跨工作区的那一行**不是** ``None``，而是一次
        拒绝 —— 折成 ``None`` 会让「没有这一条」与「有这一条但不给你看」同形，而
        前者会让人去重新提交一条已经存在的反馈。
        """
        raise NotImplementedError

    @abstractmethod
    def list_feedback(
        self,
        *,
        scope_kind: ScopeKind | None = None,
        scope_ref: str | None = None,
        limit: int | None = None,
    ) -> FeedbackPage:
        """按作用域筛，**新的在前**，并且**两个数一起给**。

        两个参数都缺省时是「这个工作区的全部反馈」（不是「这个库的全部」——
        工作区那一层由存储的绑定回答，不由这个筛选条件回答）。

        ``limit`` 缺省不封顶在这里；封顶是**调用点**的事（``MAX_FEEDBACK``），因为
        封多少是一个关于「一次回执多大」的决定，而它由交付面持有。两个数分开报
        这件事因此是这一格的形状，不是调用点补上去的一句话。
        """
        raise NotImplementedError

    # ------------------------------------------------------------ 候选
    @abstractmethod
    def put_candidate(self, candidate: ExperienceCandidate) -> CandidateAdmission:
        """把一条候选放上盘。**同一个 ``(candidate_id, revision)`` 第二次不覆盖。**

        主键是 ``(candidate_id, revision)``：后来的修订是**新的一行**，而旧那一行
        留在盘上被读得到 —— 「这条候选当初是怎么说的」只有那一行答得出来。

        Raises:
            StoreError: 同一个 ``(id, revision)`` 配了不同的内容 —— 那是一次真实的
                重复写入。它是 ``candidate_id`` 的原像**不含** ``generalises`` 换来
                的那件事：改了想推广到哪、而 id 不变时，必须在这里响。
        """
        raise NotImplementedError

    @abstractmethod
    def get_candidate(
        self, candidate_id: str, revision: int | None = None
    ) -> ExperienceCandidate | None:
        """按 id 读一条。``revision`` 缺省取**最新**的那一版。"""
        raise NotImplementedError

    @abstractmethod
    def list_candidates(self) -> tuple[ExperienceCandidate, ...]:
        """这个工作区的候选清单，新的在前（同刻按 ``candidate_id`` 与 ``revision``）。

        **只列当前版**：一条候选的旧修订不是另一条候选，把它一起列出来会让
        「有几条候选」这个数把同一件事数两遍。
        """
        raise NotImplementedError

    # ------------------------------------------------------------ 补丁
    @abstractmethod
    def put_patch(self, patch: PolicyPatch) -> PatchAdmission:
        """把一份补丁放上盘。**同一个 ``(patch_id, revision)`` 第二次不覆盖。**

        Raises:
            StoreError: 同一个 ``(id, revision)`` 配了不同的内容（同
                :meth:`put_candidate`）。
        """
        raise NotImplementedError

    @abstractmethod
    def get_patch(self, patch_id: str, revision: int | None = None) -> PolicyPatch | None:
        """按 id 读一份。``revision`` 缺省取**最新**的那一版。"""
        raise NotImplementedError

    @abstractmethod
    def list_patches(self) -> tuple[PolicyPatch, ...]:
        """这个工作区的补丁清单，新的在前（同刻按 ``patch_id``）。**只列当前版。**"""
        raise NotImplementedError

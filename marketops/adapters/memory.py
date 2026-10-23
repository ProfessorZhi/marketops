"""内存实现：**测试替身**，不是部署选项。

它存在的理由只有一个，而且是验收要求里的那一条：本地研究流水线必须能在没有
Serper key、没有飞书凭据、**没有磁盘状态**的情况下跑完测试。

## 它为什么必须实现同一个契约

它曾经与 JSONL 实现写在同一个文件里，行为靠「照着写一遍」对齐 —— 而那种对齐
没有任何执行器。B5 之后三个实现跑同一份
``tests/test_evidence_store_contract.py``：它在这里通过而在 SQLite 那里失败，
才是「契约」这个词有意义的样子。

``InMemoryBudgetLedger`` 同理，对着 ``ports/budget.py`` 与
``tests/test_budget_contract.py``。它**不是**给并发用的：SQLite 那份的并发正确性
来自 ``BEGIN IMMEDIATE``，而这里没有锁 —— 跨进程或跨线程的判据只在那份上成立，
见 ``tests/test_budget_ledger.py``。

进程退出即消失，不做持久化，也不保证线程安全 —— 它不是给并发用的，是给
「一次测试里的一段流程」用的。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta

from ..domain.account import MarketAccount
from ..domain.action_item import (
    OPEN_STATES,
    ActionItem,
    ActionItemState,
    require_creatable,
)
from ..domain.budget import BudgetUsage, Reservation, reservation_id
from ..domain.cohort import AccountCohort
from ..domain.continuation import (
    BLOCKED_BACKOFF,
    BlockedOn,
    Continuation,
    ContinuationClaim,
    ContinuationIntent,
    ContinuationKind,
    ContinuationState,
    WakeupOwner,
    after_failure,
    intents_for_admission,
    intents_for_resolution,
    intents_for_settlement,
    open_continuation,
    retry_delay,
)
from ..domain.entities import AccountVenueRelation, RouteOpportunity, VenueAsset
from ..domain.enums import (
    JOB_TRANSITIONS,
    AttemptState,
    BudgetLine,
    JobInputKind,
    JobState,
    ReservationState,
    RunStatus,
    StopReason,
)
from ..domain.errors import (
    ActionItemConflictError,
    BudgetExhaustedError,
    BudgetNotConfiguredError,
    ContinuationError,
    EvidenceError,
    IdempotencyConflictError,
    JobStateError,
    LeaseError,
    OpenActionItemExistsError,
    ReservationStateError,
    ReviewConflictError,
    RunFoldLedgerError,
    RunSpecError,
    RunStateError,
    SavedViewConflictError,
    SceneConflictError,
    ScheduleConflictError,
    StoreError,
    SubscriptionConflictError,
    WaveConflictError,
)
from ..domain.evidence import Evidence
from ..domain.fetch_attempt import FetchAttempt
from ..domain.findings import Findings
from ..domain.fold import FoldAttribution, FoldReceipt
from ..domain.route_records import (
    CanonicalContactRoute,
    RouteObservation,
    RouteRejection,
    fold_observation,
    identity_mismatch,
)
from ..domain.job import (
    ExecutionRef,
    JobAttempt,
    ResearchJob,
    Settlement,
    WorkItem,
    WorkItemSnapshot,
    attempt_id,
    job_id,
    work_item_id,
)
from ..domain.maintenance import (
    ChangeSignal,
    ClaimOutcome,
    MaintenanceClaim,
    MaintenanceState,
    Observation,
)
from ..domain.outreach import (
    DEFAULT_ATTEMPT_LEASE,
    ApprovedClaim,
    AttemptOutcome,
    CorrespondenceEvent,
    MessageState,
    OutreachCampaign,
    OutreachDraft,
    OutreachManifest,
    OutboundAttempt,
    RecipientSuppression,
    SalesHandoff,
    TemplateFamily,
    Wave,
    WaveAuthorization,
    WritingPolicy,
    may_be_settled_by_reconciliation,
)
from ..domain.product import OfferVersion, ProductLine, ProductVersion
from ..domain.profile import SceneFiles
from ..domain.review import (
    HumanResolution,
    ReviewCase,
    apply_resolution,
    claim,
    is_pending,
)
from ..domain.run import Run, RunSpec, run_id
from ..domain.saved_view import SavedView
from ..domain.run_delta import RunDelta
from ..domain.scene import SceneRecord, SceneVersion
from ..domain.subject import SubjectDossier
from ..domain.util import normalise_email, to_utc, utc_now
from ..domain.watch import Watch
from ..domain.workstream import EffectiveScene, LaunchRecord, Workstream, effective_scene_digest
from ..ports.budget import BudgetLedger
from ..ports.claim_withdrawal import ClaimWithdrawalStore
from ..ports.continuation import WakeupRegistry
from ..ports.fetch_attempt import FetchAttemptStore
from ..ports.outreach import OutreachStore
from ..ports.pool import (
    AccountCohortStore,
    AccountMerge,
    AccountPoolStore,
    AccountVenueRelationStore,
    AssetMerge,
    RelationMerge,
    VenueAssetStore,
)
from ..ports.product_catalog import ProductCatalog
from ..ports.scenes import MaintenanceStore, SceneStore
from ..ports.jobs import (
    JobStore,
    repin_refusal,
    resnapshot_refusal,
    settlement_outcome,
)
from ..ports.review import CaseAdmission, ReviewCaseStore
from ..ports.runs import (
    ChainProgress,
    RunStore,
    admission_refusal,
    ending_refusal,
    fold_after_end_refusal,
    fold_ledger_refusal,
    fold_replay_conflict,
    progress_of,
    stopping_outcome,
    unknown_run_refusal,
    window_refusal,
)
from ..ports.store import EvidenceStore
from ..ports.workstreams import EffectiveSceneStore, WorkstreamStore
from ..ports.views import (
    ActionItemStore,
    ActionItemWrite,
    SavedViewStore,
    ViewWrite,
    WatchStore,
    WatchWrite,
)
from ..domain.notification import (
    DeliveryAttempt,
    DeliveryOutcome,
    DeliveryReceipt,
    NotificationEvent,
    NotificationSeverity,
    NotificationSubscription,
)
from ..domain.scheduling import (
    ActionReport,
    FireOutcome,
    ScheduleDefinition,
    ScheduleFire,
    due_schedules,
)
from ..domain.session import SessionBinding
from ..ports.notification import (
    EventWrite,
    NotificationStore,
    ReceiptWrite,
    SubscriptionWrite,
)
from ..ports.scheduling import (
    FireWrite,
    ScheduleStore,
    ScheduleWrite,
)
from ..ports.session import BindingWrite, SessionBindingStore

def _put_or_refuse(bucket: dict, key, model, *, what: str, why: str) -> None:  # noqa: ANN001
    """「同一个键配不同内容是报错，不是覆盖」——两张表共用的一段。

    键是一个字符串（:meth:`InMemoryOutreachStore.put_authorization` 按
    ``authorization_id`` 去重）。**三张配置表不走这里**：它们的键是 ``(id, 版)``，
    多两条判据（要不要接在最新一版之后），那两条住在 :func:`_put_config`。
    """
    existing = bucket.get(key)
    if existing is None:
        bucket[key] = model
        return
    if existing == model:
        return
    raise StoreError(
        f"{what} {key!r} 已经存在，而本次的内容与库里那一份不同。\n**不覆盖** —— {why}"
    )


def _put_config(bucket: dict, ident: str, model, *, what: str, why: str) -> None:  # noqa: ANN001
    """「同一个 ``(id, 版)`` 配不同内容是报错，不是覆盖」——三张配置表共用的一段。

    抽出来是因为三个调用点逐字相同，而不是因为它们将来会一样 —— 它们的**语义**
    已经一样了：模板族、写作策略、已批准说法都是「引用它的是 ``(id, 版)``，所以
    那一格底下的内容不得变」。SQLite 那份有一个同名同义的 ``_put_config``，
    连三条判据的顺序都一样（那一版在不在 / 内容一样吗 / 是不是接在最新一版之后）。
    """
    revision = model.revision
    existing = bucket.get((ident, revision))
    if existing is not None:
        if existing == model:
            return
        raise StoreError(
            f"{what} {ident!r} 第 {revision} 版已经在库里，而本次的内容与那一版不同。\n"
            f"**不覆盖** —— 引用它的是 (id, 版)，{why}\n"
            f"要改内容，请发布下一版（第 {revision + 1} 版）。"
        )
    newest = max(
        (version for key, version in bucket if key == ident), default=None
    )
    if newest is not None and revision != newest + 1:
        raise StoreError(
            f"{what} {ident!r} 现在最新的版本是第 {newest} 版，而本次要落的是"
            f"第 {revision} 版。\n"
            f"**新版本必须接在最新一版之后**（本次应当是第 {newest + 1} 版）——"
            "跳版会让中间那些版号的去向没有答案，而报告上只显示「多了一个版本」。"
        )
    bucket[(ident, revision)] = model


def _latest_by_id(bucket: dict) -> tuple:  # noqa: ANN001
    """每个 id 的**最新**一版，按 id 排序。**只挑最新，不删旧的。**

    读路径返回最新版、写路径保留全部版本，这两件事不矛盾：写信要用的是现在的话术，
    而历史版本必须还在 —— 一条草稿记的是 ``(id, 版)``，把旧版丢掉会让「这一稿当时
    按哪一版写的」变成一句查不到的话。
    """
    newest: dict[str, tuple[int, object]] = {}
    for (ident, version), model in bucket.items():
        if ident not in newest or version > newest[ident][0]:
            newest[ident] = (version, model)
    return tuple(newest[ident][1] for ident in sorted(newest))


def _revalidate(model, replacement: dict):  # noqa: ANN001, ANN202
    """改过的对象**重新过一遍校验器**。

    ``model_copy(update=...)`` **不校验** —— 这是本仓反复踩到的一处。改动只发生在
    存储层，所以重验放在这里，而不是指望调用方改之前先自己验一遍。
    ``SqliteOutreachStore`` 那份同名同义。
    """
    merged = model.model_dump()
    merged.update(replacement)
    return type(model).model_validate(merged)


def _moved(model, **changes):  # noqa: ANN001, ANN202
    """``_revalidate`` 的关键字版：``_moved(row, state=..., reason=...)``。

    与它同一件事（改完重新过一遍校验器），只是调用处短一些。两个都在，而不是把
    ``_revalidate`` 改成收 ``**kwargs``：它已有的调用点传的是一个**算出来的**字典，
    那种调用写成 ``**d`` 就把键名检查丢掉了 —— 而键名检查正是这一层要的
    （``state`` 拼错时 ``model_validate`` 会因为 ``extra="forbid"`` 报错，
    ``**{'staet': ...}`` 则会安静地什么都不改）。
    """
    return _revalidate(model, changes)


def _require_text(value: str, field: str) -> str:
    """非空且无首尾空白。两个作业实现各有一份 —— 它是**形状**检查，不是契约。

    契约是异常类型与状态转移（由 ``tests/test_job_contract.py`` 在两个实现上各
    跑一遍）。一条形状检查若提进领域层，会让「哪些字段必须是字符串」变成领域层
    对存储的知识。
    """
    if not value or value != value.strip():
        raise ValueError(f"{field} 不能为空或首尾带空白")
    return value


def _require_epoch(epoch: int) -> int:
    """``epoch`` 必须是从 1 起的正整数。两个实现各有一份，与 ``_require_text`` 同理
    —— 它是**形状**检查，不是契约（契约由 ``tests/test_continuation_contract.py``
    在两个实现上各跑一遍）。"""
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
        raise ValueError(f"epoch 必须是从 1 起的正整数，收到 {epoch!r}。")
    return epoch


class InMemoryEvidenceStore(EvidenceStore):
    """不落盘的实现。行为与另外两个一致，判据见契约测试。

    ## 工作区绑定由**第一行写入**建立 —— 与 SQLite 那一份同形

    它没有 ``Settings`` 可读（构造参数为空是刻意的：一个测试替身不该要求一份部署
    配置），所以它的绑定只能是「第一行写进来的那个 ``workspace``」—— 与
    ``adapters/sqlite/evidence_store.py`` 完全同一个形状，判据也一样：第二个工作区
    的写入被拒，读到的行必须属于这个绑定。

    这一份里「绑定的行」与「读到的行」不可能不一致（没有第二个写者、没有盘上的
    历史行），所以它没有 ``_owned`` 那一层 —— 但**判据仍要实现**：三个实现跑同一份
    契约测试，一个只在自己那份上成立的拒绝会让契约在别处静默失效。
    """

    def __init__(self) -> None:
        self._workspace: str | None = None
        self._evidence: dict[str, dict[str, Evidence]] = {}
        self._routes: dict[str, CanonicalContactRoute] = {}
        self._observations: dict[str, dict[str, RouteObservation]] = {}
        self._rejections: dict[str, dict[str, RouteRejection]] = {}
        self._opportunities: dict[str, list[RouteOpportunity]] = {}

    def bind_workspace(self, workspace: str) -> None:
        """记下这个替身属于哪个工作区。已经绑了别的就拒绝（与 SQLite 那份同一句话）。"""
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise EvidenceError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按账号取到的这一行是谁的」没有答案，"
                "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
                "要用另一个工作区，换一个库（或先把这个库移开）。"
            )

    def put_evidence(self, evidence: Evidence) -> None:
        self.bind_workspace(evidence.workspace)
        bucket = self._evidence.setdefault(evidence.account_id, {})
        existing = bucket.get(evidence.evidence_id)
        if existing is None:
            bucket[evidence.evidence_id] = evidence
            return
        if existing != evidence:
            raise EvidenceError(
                f"已有 evidence_id={evidence.evidence_id!r} 的证据，但内容与本次不同。\n"
                "这个 id 是内容寻址的，所以同一个 id 配不同内容只可能意味着 id 被手工指定过。"
            )

    def list_evidence(self, account_id: str) -> list[Evidence]:
        return [
            self._own(item, where=f"evidence[{item.evidence_id}]")
            for item in self._evidence.get(account_id, {}).values()
        ]

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        """``None`` 只有一个意思：**没有这个 id 的行**（与另外两个实现一致）。"""
        for bucket in self._evidence.values():
            found = bucket.get(evidence_id)
            if found is not None:
                return self._own(found, where=f"evidence[{evidence_id}]")
        return None

    def record_route(self, observation: RouteObservation) -> CanonicalContactRoute:
        self.bind_workspace(observation.workspace)
        history = self._observations.setdefault(observation.account_id, {})
        history[observation.observation_id] = observation
        current = self._routes.get(observation.canonical_key)
        folded = fold_observation(current, observation, at=observation.observed_at)
        self._routes[observation.canonical_key] = folded.projection
        return folded.projection

    def list_routes(self, account_id: str) -> list[CanonicalContactRoute]:
        return [
            self._own(item, where="canonical_contact_route")
            for key, item in sorted(self._routes.items())
            if item.account_id == account_id
        ]

    def list_route_observations(self, account_id: str) -> list[RouteObservation]:
        return sorted(
            (
                self._own(item, where="route_observation")
                for item in self._observations.get(account_id, {}).values()
            ),
            key=lambda item: (item.observed_at, item.observation_id),
        )

    def put_route_rejection(self, rejection: RouteRejection) -> None:
        """一张收据一行。**身份必须一致，改写账换新的**（见 :meth:`RouteRejection.identity`）。

        替身与两个真实现走同一条判据，逐字同一条：此前这一版比的是**整个对象**，
        于是同一份材料第二次被结算（``run_id`` 变了，而 ``rejection_id`` 的原像里
        没有它）在测试替身上就抛 ``EvidenceError``，而两个真实现那时也各抛各的 ——
        「三处都错」看起来像「契约就是这样」。
        """
        self.bind_workspace(rejection.workspace)
        bucket = self._rejections.setdefault(rejection.account_id, {})
        existing = bucket.get(rejection.rejection_id)
        if existing is not None:
            drift = identity_mismatch(existing, rejection)
            if drift:
                raise EvidenceError(
                    f"已有 rejection_id={rejection.rejection_id!r} 的拒绝记录，"
                    f"但身份与本次不同：{'、'.join(drift)}。\n"
                    "这个 id 是内容寻址的（工作区、账号、原因码、候选、来源页、时间），"
                    "而上面这几格都在原像里 —— 同一个 id 配不同的它们，只可能意味着 "
                    "id 被手工指定过。\n"
                    "（恰相反，run_id / work_item_id / rule 属于改写账：它们不在原像里，"
                    "同一张收据被再写一次时会被换掉。）"
                )
        # 覆盖而不是保留先写的那条：改写账要留**最近一次**拒它的那一趟。
        bucket[rejection.rejection_id] = rejection

    def list_route_rejections(self, account_id: str) -> list[RouteRejection]:
        return sorted(
            (
                self._own(item, where="route_rejection")
                for item in self._rejections.get(account_id, {}).values()
            ),
            key=lambda item: (item.occurred_at, item.rejection_id),
        )

    def put_opportunity(self, opportunity: RouteOpportunity) -> None:
        self.bind_workspace(opportunity.workspace)
        self._opportunities.setdefault(opportunity.account_id, []).append(opportunity)

    def list_opportunities(self, account_id: str) -> list[RouteOpportunity]:
        return [
            self._own(item, where="route_opportunity")
            for item in self._opportunities.get(account_id, ())
        ]

    def _own(self, item, *, where: str):  # noqa: ANN001 - 三个模型共用一条判据
        if item.workspace == self._workspace:
            return item
        raise EvidenceError(
            f"{where} 写着 workspace={item.workspace!r}，而这个存储绑在 "
            f"{self._workspace!r} 上。\n"
            "这一行不属于本存储的部署，所以它**不被返回**，也不被跳过 —— 跳过会让"
            "「这条不属于你」与「没有这一条」在调用点长成同一个空结果。"
        )


class InMemoryBudgetLedger(BudgetLedger):
    """不落盘的账本。语义与 SQLite 那份一致，判据见契约测试。

    **没有锁。** 这一份的用途是「同一条流程内的额度记账」，不是替并发正确性
    背书 —— 多进程下真正的上限保证只由 ``SqliteBudgetLedger`` 提供。把它当并发
    账本用，会得到一个在单进程测试里正确、在多 worker 下超支的实现。
    """

    def __init__(self) -> None:
        self._ceilings: dict[tuple[str, BudgetLine], int] = {}
        self._sources: dict[tuple[str, BudgetLine], str] = {}
        self._reservations: dict[str, Reservation] = {}

    def set_ceiling(
        self, scope: str, line: BudgetLine, units: int, *, source: str
    ) -> None:
        if units < 0:
            raise ValueError("上限不能为负")
        if not source.strip():
            raise ValueError("设定上限必须写明来源（source）—— 见 §8：调用方不得静默扩大上限")
        held = self._held(scope, line)
        if units < held:
            raise BudgetExhaustedError(
                f"{scope} / {line.value} 已占用 {held}，不能把上限设为 {units}。\n"
                "先处置在途的预留（结算或退款），再调低上限。"
            )
        self._ceilings[(scope, line)] = units
        self._sources[(scope, line)] = source.strip()

    def reserve(
        self, scope: str, line: BudgetLine, units: int, *, idempotency_key: str
    ) -> Reservation:
        if units <= 0:
            raise ValueError("预留必须是正数 —— 0 单位不占额度，等于没有预留")
        if not idempotency_key.strip():
            raise ValueError("幂等键不能为空：没有键就无法回答『这是重试还是新请求』")
        rid = reservation_id(scope, line, idempotency_key)

        existing = self._reservations.get(rid)
        if existing is not None:
            if existing.line is not line or existing.units != units:
                raise IdempotencyConflictError(
                    f"幂等键 {idempotency_key!r} 已经用于 "
                    f"{existing.line.value}/{existing.units}，现在被用于 {line.value}/{units}。\n"
                    "同一个键的承诺是『重发不会执行两次』，不是『同一个键可以改内容』。"
                )
            return existing  # 重发：不重复扣

        ceiling = self._ceilings.get((scope, line))
        if ceiling is None:
            raise BudgetNotConfiguredError(
                f"{scope} / {line.value} 没有配置上限。\n"
                "未配置**不等于**无限：把缺省当无限，会让『忘写配置』与『故意放开』"
                "在行为上不可区分。先 set_ceiling（要写明 source），再用。"
            )
        held = self._held(scope, line)
        if held + units > ceiling:
            raise BudgetExhaustedError(
                f"{scope} / {line.value} 上限 {ceiling}，已占用 {held}（含在途），"
                f"本次还要 {units}。\n"
                "已占用**包含尚未结算的预留** —— 一次超时未对账的尝试会一直扣着它的额度。"
            )

        created = Reservation(
            reservation_id=rid,
            scope=scope,
            line=line,
            units=units,
            state=ReservationState.RESERVED,
            idempotency_key=idempotency_key,
            created_at=utc_now().isoformat(),
        )
        self._reservations[rid] = created
        return created

    def spend(self, reservation_id: str) -> Reservation:
        return self._settle(reservation_id, ReservationState.SPENT, note=None)

    def refund(self, reservation_id: str, *, reason: str) -> Reservation:
        if not reason.strip():
            raise ValueError(
                "退款必须写明理由。\n"
                "这是刻意的：超时之后并不确定对方是否已经计费，自动退款等于替 provider "
                "做了『这次不算钱』的假设，而它错误时只在账单上可见。"
            )
        return self._settle(reservation_id, ReservationState.REFUNDED, note=reason.strip())

    def usage(self, scope: str) -> tuple[BudgetUsage, ...]:
        lines = sorted(
            (line for (owner, line) in self._ceilings if owner == scope),
            key=lambda line: line.value,
        )
        return tuple(
            BudgetUsage(
                line=line,
                ceiling=self._ceilings[(scope, line)],
                spent=self._sum(scope, line, ReservationState.SPENT),
                reserved=self._sum(scope, line, ReservationState.RESERVED),
                refunded=self._sum(scope, line, ReservationState.REFUNDED),
            )
            for line in lines
        )

    def remaining(self, scope: str, line: BudgetLine) -> int:
        for entry in self.usage(scope):
            if entry.line is line:
                return entry.remaining
        raise BudgetNotConfiguredError(
            f"{scope} / {line.value} 没有配置上限。"
            "不要把它读成 0 —— 0 的意思是「用完了」，而这两件事的处置不同。"
        )

    def open_reservations(self, scope: str | None = None) -> tuple[Reservation, ...]:
        rows = [
            item
            for item in self._reservations.values()
            if item.state is ReservationState.RESERVED
            and (scope is None or item.scope == scope)
        ]
        return tuple(sorted(rows, key=lambda item: (item.created_at, item.reservation_id)))

    def get(self, reservation_id: str) -> Reservation | None:
        return self._reservations.get(reservation_id)

    # ------------------------------------------------------------------
    def _settle(
        self, reservation_id: str, target: ReservationState, *, note: str | None
    ) -> Reservation:
        current = self._reservations.get(reservation_id)
        if current is None:
            raise ReservationStateError(f"没有这条预留：{reservation_id}")
        if current.state is target:
            return current  # 幂等：重复结算不重复计
        if current.state is not ReservationState.RESERVED:
            raise ReservationStateError(
                f"预留 {reservation_id} 当前是 {current.state.value}，不能变成 {target.value}。\n"
                + (
                    "已经花掉的钱不能退款 —— 那不是记账，是改写用量。"
                    if target is ReservationState.REFUNDED
                    else "已经退款的预留不能再结算 —— 额度已经还回去了。"
                )
            )
        settled = Reservation(
            reservation_id=current.reservation_id,
            scope=current.scope,
            line=current.line,
            units=current.units,
            state=target,
            idempotency_key=current.idempotency_key,
            created_at=current.created_at,
            settled_at=utc_now().isoformat(),
            note=note if note is not None else current.note,
        )
        self._reservations[reservation_id] = settled
        return settled

    def _held(self, scope: str, line: BudgetLine) -> int:
        """已占用 = spent + reserved。``refunded`` **不计** —— 见 ``ports/budget.py``。"""
        return self._sum(scope, line, ReservationState.SPENT) + self._sum(
            scope, line, ReservationState.RESERVED
        )

    def _sum(self, scope: str, line: BudgetLine, state: ReservationState) -> int:
        return sum(
            item.units
            for item in self._reservations.values()
            if item.scope == scope and item.line is line and item.state is state
        )


class InMemoryJobStore(JobStore):
    """不落盘的作业记录。语义与 SQLite 那份一致，判据见契约测试。

    **没有事务。** SQLite 那份的每一条读-判-写都在 ``BEGIN IMMEDIATE`` 里；这里
    只有一条普通语句序列。单进程测试里两者行为相同，多进程下只有那一份守得住
    「同一件事不会被做两遍」—— 把这一份当并发记录用，会得到一个在单进程测试里
    正确、在两个 worker 下重复派发的实现。
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ResearchJob] = {}
        self._items: dict[str, WorkItem] = {}
        self._refs: dict[str, ExecutionRef] = {}
        # 按 work_item_id 索引，与 SQLite 那份的主键一致。**可能没有条目** ——
        # 「没有钉」是一个要处理的状态，不是「数据坏了」。
        self._pins: dict[str, WorkItemSnapshot] = {}
        #: 版本 → 那批字节。与 SQLite 的 scene_snapshot 表同构（内容寻址）。
        self._scenes: dict[str, SceneFiles] = {}
        self._attempts: dict[str, JobAttempt] = {}
        # 按 **work_item_id** 索引，与 SQLite 那份的主键一致：一个 work item
        # 最多一条结算（见 ports/jobs.py 的 ``settlement``）。
        self._settlements: dict[str, Settlement] = {}
        # 按 **continuation_id** 索引，与 SQLite 那份的主键一致 —— 于是「同一个事件
        # 欠下的下一步只有一行」在这里也不是一条纪律，而是字典的键。
        self._continuations: dict[str, Continuation] = {}

    # ------------------------------------------------------------------
    # 受理
    # ------------------------------------------------------------------
    def admit(
        self,
        *,
        scene_id: str,
        input_kind: JobInputKind,
        input_ref: str,
        idempotency_key: str,
        request_sha256: str,
    ) -> ResearchJob:
        _require_text(scene_id, "scene_id")
        _require_text(input_ref, "input_ref")
        _require_text(idempotency_key, "幂等键")
        if len(request_sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in request_sha256
        ):
            raise ValueError(
                "request_sha256 必须是 64 位小写十六进制。\n"
                "它唯一的用途是判断「同一个键是不是同一个请求」，所以一个形状不对的"
                "值不会报错 —— 它只会让**每一个**请求都与别的请求不同，于是重发被读成"
                "新请求。"
            )

        jid = job_id(scene_id, idempotency_key)
        existing = self._jobs.get(jid)
        if existing is not None:
            if existing.request_sha256 != request_sha256:
                raise IdempotencyConflictError(
                    f"幂等键 {idempotency_key!r} 在场景 {scene_id!r} 下已经用于另一个请求"
                    f"（{existing.request_sha256[:16]}… ≠ {request_sha256[:16]}…，"
                    f"作业 {jid}）。\n"
                    "同一个键的承诺是『重发不会受理两次』，不是『同一个键可以改内容』。"
                )
            return existing

        at = utc_now()
        now = at.isoformat()
        created = ResearchJob(
            job_id=jid,
            scene_id=scene_id,
            input_kind=input_kind,
            input_ref=input_ref,
            idempotency_key=idempotency_key,
            request_sha256=request_sha256,
            state=JobState.QUEUED,
            reason="",
            created_at=now,
            updated_at=now,
        )
        self._jobs[jid] = created
        # 同一个事件欠下的下一步，与受理同时（SQLite 那份在同一个写事务里；这里
        # 没有事务，只有顺序 —— 见类 docstring）。**重发走不到这里**：上面那句
        # ``return existing`` 在它之前。
        self.owe(intents_for_admission(jid, scene_id), at=at)
        return created

    def get(self, job_id: str) -> ResearchJob | None:
        return self._jobs.get(job_id)

    # ------------------------------------------------------------------
    # 状态（CAS）
    # ------------------------------------------------------------------
    def set_state(
        self, job_id: str, state: JobState, *, expected: JobState, reason: str = ""
    ) -> ResearchJob:
        if state in (JobState.PAUSED, JobState.BLOCKED, JobState.CANCELLED):
            if not reason.strip():
                raise JobStateError(
                    f"转进 {state.value} 必须写明理由。\n"
                    "§6 要求『Cancel reports what actually stopped』—— 一条没有理由的"
                    "取消无法回答那句话，而它和『跑完了』在报告里长得一样。"
                )
        elif reason.strip():
            raise JobStateError(
                f"转进 {state.value} 不接受理由（收到 {reason!r}）。\n"
                "一条恢复不出自任何人的判断；允许它带理由，就等于允许把一次恢复写成"
                "一次判断，而恢复是可以被随便按下很多次的那个动作。"
            )

        current = self._jobs.get(job_id)
        if current is None:
            raise JobStateError(f"没有这个作业：{job_id}")
        if current.state is not expected:
            raise JobStateError(
                f"CAS 不匹配：作业 {job_id} 期望的状态是 {expected.value!r}，"
                f"实际是 {current.state.value!r} —— 另一个写入者先改了它；整次拒绝。\n"
                "读到的状态与据此写下的状态必须在同一个写事务里。没有这一条，两个"
                "控制端同时 pause / cancel 时最后一个写的赢，而**两边都以为自己成功了**。"
            )
        if state not in JOB_TRANSITIONS[current.state]:
            allowed = sorted(s.value for s in JOB_TRANSITIONS[current.state]) or ["（终态）"]
            raise JobStateError(
                f"{current.state.value} → {state.value} 不是合法转移；"
                f"从 {current.state.value} 可以去的只有 {allowed}。"
            )
        at = utc_now()
        moved = replace(current, state=state, reason=reason.strip(), updated_at=at.isoformat())
        self._jobs[job_id] = moved
        # 与 SQLite 那份逐字同一条：作业状态换了，它欠下的**派发意图**跟着换。
        self._rearm(current, state, reason=reason.strip(), at=at)
        return moved

    # ------------------------------------------------------------------
    # 编译
    # ------------------------------------------------------------------
    def compile_work_items(
        self, job_id: str, subjects: Sequence[SubjectDossier], *, scene: SceneFiles
    ) -> tuple[WorkItem, ...]:
        if not subjects:
            raise ValueError(
                "work item 清单不能为空。\n"
                "一个没有任何 work item 的作业在报告里长得像『已经做完』—— 而它其实是"
                "一次编译失败。"
            )
        for index, subject in enumerate(subjects):
            if not isinstance(subject, SubjectDossier):
                raise ValueError(
                    f"subjects[{index}] 应该是一个 SubjectDossier，"
                    f"拿到 {type(subject).__name__}。\n"
                    "这里**不接**裸的 account_id：一个只有 slug 的任务包正是这次要修"
                    "的那个缺口（worker 拿到 kent-003，拿不到 Example School One）。"
                )
        _require_text(scene.version, "scene.version")

        job = self._jobs.get(job_id)
        if job is None:
            raise JobStateError(f"没有这个作业：{job_id}")
        if not job.accepts_results:
            raise JobStateError(
                f"作业 {job_id} 已取消，不能再编译 work item。\n"
                "取消是终态：此后受理的任何工作都不会有人验收。"
            )
        existing = self.work_items(job_id)
        if existing:
            if tuple(item.subject_ref for item in existing) != tuple(
                subject.account_id for subject in subjects
            ):
                raise JobStateError(
                    f"作业 {job_id} 已经有一批不同的 work item（{len(existing)} 个）。\n"
                    "同一份输入必须编译出同一批，否则『多了一倍候选』与『多了一批工作』"
                    "在报告上不可区分。要改工作集，是另一个作业。"
                )
            refusal = repin_refusal(
                self._pins.get(existing[0].work_item_id), scene.version, job_id
            ) or resnapshot_refusal(
                [
                    None
                    if (snapshot := self._pins.get(item.work_item_id)) is None
                    or snapshot.subject is None
                    else json.dumps(
                        snapshot.subject.payload(), ensure_ascii=False, sort_keys=True
                    )
                    for item in existing
                ],
                subjects,
                job_id,
            )
            if refusal:
                raise JobStateError(refusal)
            return existing  # 重发：同一份输入、同一份规则，编译出同一批
        now = utc_now().isoformat()
        # 内容的版本与字节一起落定（SQLite 那份写在同一张表、同一个事务里）。
        self._scenes.setdefault(scene.version, scene)
        created = tuple(
            WorkItem(
                work_item_id=work_item_id(job_id, ordinal),
                job_id=job_id,
                ordinal=ordinal,
                subject_ref=subject.account_id,
                stop_reason=None,
                created_at=now,
                updated_at=now,
            )
            for ordinal, subject in enumerate(subjects)
        )
        # ``zip(..., strict=True)`` 不是风格：``created`` 与 ``subjects`` 一一对应，
        # 而按 ``created`` 单独循环会把上面那个推导式**泄漏出来的** ``subject``
        # 当成每一件的主体 —— 于是所有工作单元都钉着最后那一个身份，而**没有任何
        # 一处会报错**（数量对、id 对、版本对）。契约判据里那条「同一份输入编译两次
        # 拿回同一批」在第二个实现上就是被这个错红的。
        for item, subject in zip(created, subjects, strict=True):
            self._items[item.work_item_id] = item
            # 与工作单元在**同一步**落定：这里没有事务，所以更要把两句写在一起，
            # 而不是「先建工作单元，回头再钉」。SQLite 那份把它们放在同一个写事务里。
            self._pins[item.work_item_id] = WorkItemSnapshot(
                work_item_id=item.work_item_id,
                scene_version=scene.version,
                pinned_at=now,
                subject=subject,
            )
        return created

    def work_items(self, job_id: str) -> tuple[WorkItem, ...]:
        return tuple(
            sorted(
                (item for item in self._items.values() if item.job_id == job_id),
                key=lambda item: item.ordinal,
            )
        )

    def get_work_item(self, work_item_id: str) -> WorkItem | None:
        return self._items.get(work_item_id)

    # ------------------------------------------------------------------
    # 派发
    # ------------------------------------------------------------------
    def map_execution(self, work_item_id: str, *, dispatch_task_id: str) -> ExecutionRef:
        _require_text(dispatch_task_id, "dispatch_task_id")
        item = self._items.get(work_item_id)
        if item is None:
            raise JobStateError(f"没有这个 work item：{work_item_id}")
        job = self._jobs[item.job_id]
        if not job.admits_dispatch:
            raise JobStateError(
                f"作业 {job.job_id} 处于 {job.state.value}，不放行新的派发"
                f"（理由：{job.reason or '—'}）。\n"
                "§6：Pause normally prevents new dispatch and lets accepted in-flight "
                "results settle。派发就是这里，没有第二个入口。"
            )
        existing = self._refs.get(work_item_id)
        if existing is not None:
            if existing.dispatch_task_id != dispatch_task_id:
                raise JobStateError(
                    f"work item {work_item_id} 已经映射到 派发任务 "
                    f"{existing.dispatch_task_id!r}，不能再映射到 {dispatch_task_id!r}。\n"
                    "映射是写一次的：同一件昂贵的事被派发两遍时，**两边都会正常跑完**，"
                    "而报告上只会显示花了两次。"
                )
            return existing
        created = ExecutionRef(
            work_item_id=work_item_id,
            dispatch_task_id=dispatch_task_id,
            mapped_at=utc_now().isoformat(),
        )
        self._refs[work_item_id] = created
        return created

    def execution_ref(self, work_item_id: str) -> ExecutionRef | None:
        return self._refs.get(work_item_id)

    def work_item_snapshot(self, work_item_id: str) -> WorkItemSnapshot | None:
        return self._pins.get(work_item_id)

    def scene_files(self, scene_version: str) -> SceneFiles | None:
        return self._scenes.get(scene_version)

    # ------------------------------------------------------------------
    # 租约
    # ------------------------------------------------------------------
    def lease(self, work_item_id: str, *, holder: str) -> JobAttempt:
        _require_text(holder, "holder")
        item = self._items.get(work_item_id)
        if item is None:
            raise JobStateError(f"没有这个 work item：{work_item_id}")
        job = self._jobs[item.job_id]
        if not job.accepts_results:
            raise JobStateError(
                f"作业 {job.job_id} 已取消，不再出租 work item。\n"
                "取消是终态：此后受理的任何工作都不会有人验收。"
            )
        if not job.admits_dispatch:
            raise LeaseError(
                f"作业 {job.job_id} 处于 {job.state.value}，不放行新的租约"
                f"（理由：{job.reason or '—'}）。"
            )
        if work_item_id not in self._refs:
            raise LeaseError(
                f"work item {work_item_id} 还没有派发到执行器，不能出租。\n"
                "没有 ExecutionRef 就没有租约 —— 这是 §6 那句「the controller cannot "
                "substitute an outer-shell worker launch for dispatch」的可执行"
                "形式：绕过派发 直接开一个 worker 在代码里写不出来。"
            )
        if item.is_stopped:
            raise LeaseError(
                f"work item {work_item_id} 已经停止（{item.stop_reason.value}），不再出租。\n"
                "停止理由是一次性写下的；重跑会覆盖它。"
            )
        live = self._live_attempt(work_item_id)
        if live is not None:
            if live.holder == holder:
                return live
            raise LeaseError(
                f"work item {work_item_id} 已经租给 {live.holder!r}"
                f"（attempt {live.ordinal}，{live.leased_at}），不能再租给 {holder!r}。\n"
                "排他性是 §8B『no duplicate expensive work』那一句的载体：两个 worker "
                "同时跑同一件昂贵的事时，**两边都会以为自己是对的**。"
            )
        ordinal = 1 + max(
            (a.ordinal for a in self._attempts.values() if a.work_item_id == work_item_id),
            default=-1,
        )
        created = JobAttempt(
            attempt_id=attempt_id(work_item_id, ordinal),
            work_item_id=work_item_id,
            ordinal=ordinal,
            holder=holder,
            state=AttemptState.LEASED,
            leased_at=utc_now().isoformat(),
            settled_at=None,
            note="",
        )
        self._attempts[created.attempt_id] = created
        return created

    def release(self, attempt_id: str, *, reason: str) -> JobAttempt:
        if not reason.strip():
            raise ValueError(
                "交还租约必须写明理由。\n"
                "这是唯一的收回路径（租约不按时钟自动失效），所以理由不是礼节：它是"
                "「为什么这件工作没有结果」在报告里唯一的那句话。"
            )
        return self._close(attempt_id, AttemptState.RELEASED, note=reason.strip())

    def settle(
        self, attempt_id: str, *, stop_reason: StopReason, findings: Findings
    ) -> Settlement:
        current = self._attempts.get(attempt_id)
        if current is None:
            raise JobStateError(f"没有这条租约：{attempt_id}")
        item = self._items[current.work_item_id]
        recorded = self._settlements.get(item.work_item_id)
        already = (
            ""
            if recorded is None or item.stop_reason is None
            else settlement_outcome(item.stop_reason, recorded.findings)
        )
        if self._already_done(
            current,
            AttemptState.SETTLED,
            already=already,
            outcome=settlement_outcome(stop_reason, findings),
        ):
            assert recorded is not None
            return replace(recorded, attempt=current, replayed=True)
        job = self._jobs[item.job_id]
        if not job.accepts_results:
            raise JobStateError(
                f"作业 {job.job_id} 已取消，不受理这次结果。\n"
                "§6 要求取消报告『what actually stopped』；受理一个取消之后才到的 "
                "findings，会让报告里多出一批没人授权的工作。"
            )
        at = utc_now()
        now = at.isoformat()
        settled = replace(current, state=AttemptState.SETTLED, settled_at=now)
        self._attempts[attempt_id] = settled
        self._items[item.work_item_id] = replace(
            item, stop_reason=stop_reason, updated_at=now
        )
        settlement = Settlement(
            attempt=settled, findings=findings, recorded_at=now, replayed=False
        )
        self._settlements[item.work_item_id] = settlement
        # 同上：一次**新的**结算欠下一次复核。它在上面那句已取消的 ``raise`` 之后，
        # 所以一次被拒绝的结算不留行 —— 与 SQLite 那份是同一条判据。
        snapshot = self._pins.get(item.work_item_id)
        self.owe(
            intents_for_settlement(
                item.work_item_id,
                job.scene_id,
                subject=None if snapshot is None else snapshot.subject,
            ),
            at=at,
        )
        return settlement

    def settlement(self, work_item_id: str) -> Settlement | None:
        return self._settlements.get(work_item_id)

    def attempts(self, work_item_id: str) -> tuple[JobAttempt, ...]:
        return tuple(
            sorted(
                (a for a in self._attempts.values() if a.work_item_id == work_item_id),
                key=lambda a: a.ordinal,
            )
        )

    # ------------------------------------------------------------------
    # 续跑意图（§10）
    # ------------------------------------------------------------------
    def continuations(
        self,
        *,
        scene_ids: Sequence[str],
        states: Iterable[ContinuationState] | None = None,
    ) -> tuple[Continuation, ...]:
        if not scene_ids:
            return ()
        wanted = set(scene_ids)
        keep = None if states is None else {s for s in states}
        if keep is not None and not keep:
            return ()
        rows = [
            row
            for row in self._continuations.values()
            if row.scene_id in wanted and (keep is None or row.state in keep)
        ]
        # 顺序是契约的一部分（见 ports/jobs.py::continuations）。
        return tuple(sorted(rows, key=lambda row: (row.due_at, row.continuation_id)))

    def continuation(self, continuation_id: str) -> Continuation | None:
        return self._continuations.get(continuation_id)

    def continuation_counts(self, *, scene_ids: Sequence[str]) -> dict[ContinuationState, int]:
        counts = {state: 0 for state in ContinuationState}
        wanted = set(scene_ids)
        for row in self._continuations.values():
            if row.scene_id in wanted:
                counts[row.state] += 1
        return counts

    def claim_continuation(
        self, continuation_id: str, *, holder: str, epoch: int, now: datetime
    ) -> ContinuationClaim:
        _require_text(holder, "holder")
        _require_epoch(epoch)
        at = to_utc(now)
        current = self._continuations.get(continuation_id)
        if current is None:
            raise ContinuationError(f"没有这条续跑意图：{continuation_id}")
        claimable = current.state in (ContinuationState.PENDING, ContinuationState.BLOCKED)
        if not (claimable and current.is_due(at)):
            return ContinuationClaim(continuation=current, won=False)
        claimed = _moved(
            current,
            state=ContinuationState.CLAIMED,
            holder=holder,
            epoch=epoch,
            blocked_on=None,
            reason="",
            due_at=at,
            updated_at=at,
        )
        self._continuations[continuation_id] = claimed
        return ContinuationClaim(continuation=claimed, won=True)

    def finish_continuation(
        self,
        continuation_id: str,
        *,
        state: ContinuationState,
        holder: str,
        epoch: int,
        now: datetime,
        checkpoint: str = "",
        reason: str = "",
        blocked_on: BlockedOn | None = None,
    ) -> Continuation:
        _require_text(holder, "holder")
        _require_epoch(epoch)
        at = to_utc(now)
        blocked = state is ContinuationState.BLOCKED
        if blocked != (blocked_on is not None):
            raise ValueError(
                f"state={state.value} 与 blocked_on={blocked_on} 对不上。\n"
                "被挡住时要说清是**哪一类**理由（暂停 / 缺容量 / 等人），没被挡住时不得"
                "留下一个 —— 分类读的正是这一格，而它对不上时报告只会说『没做成』。"
            )
        if state is ContinuationState.FAILED and not reason.strip():
            raise ValueError(
                "一次失败必须写明理由。\n"
                "报告里那一格就是这句话；说不出原因的失败在账上只剩『没做成』，"
                "而它的处置（人去看一眼、还是继续重试）完全取决于那句话。"
            )
        current = self._continuations.get(continuation_id)
        if current is None:
            raise ContinuationError(f"没有这条续跑意图：{continuation_id}")
        if current.state is not ContinuationState.CLAIMED:
            raise ContinuationError(
                f"续跑意图 {continuation_id} 不在 CLAIMED，而在 {current.state.value}："
                "只有领到手的那一代可以结账。"
            )
        if (current.holder, current.epoch) != (holder, epoch):
            raise ContinuationError(
                f"续跑意图 {continuation_id} 现在在 {current.holder!r} 的第 "
                f"{current.epoch} 代手里，而调用方报的是 {holder!r} 的第 {epoch} 代。"
            )
        if state is not ContinuationState.CLAIMED and not current.may_move_to(state):
            raise ContinuationError(f"{current.state.value} → {state.value} 不是合法转移。")
        attempts = current.attempts + (1 if state is ContinuationState.FAILED else 0)
        # 与 SQLite 那份逐字同一条：执行器报的是「这一次失败了」，盘上记的是「这件事
        # 到哪儿了」。
        landed = after_failure(attempts) if state is ContinuationState.FAILED else state
        due_at = at
        if landed is ContinuationState.PENDING:
            due_at = at + retry_delay(attempts)
        elif blocked:
            due_at = at + BLOCKED_BACKOFF
        finished = _moved(
            current,
            state=landed,
            attempts=attempts,
            checkpoint=checkpoint or current.checkpoint,
            reason=reason.strip(),
            blocked_on=blocked_on if landed is ContinuationState.BLOCKED else None,
            due_at=due_at,
            updated_at=at,
        )
        self._continuations[continuation_id] = finished
        return finished

    # ------------------------------------------------------------------
    # outbox 的共写（与 SQLite 那份逐个判断对齐）
    # ------------------------------------------------------------------
    def owe(self, intents: Sequence[ContinuationIntent], *, at: datetime) -> None:
        """欠下下一步。**没有才写** —— 与 ``continuation_rows.owe`` 逐字同一条判据。

        公开而不是私有：裁决那一侧是它的第二个调用方
        （``InMemoryReviewCaseStore.record_resolution``），正如 ``continuation_rows.owe``
        在 SQLite 那一侧同时服务 ``JobStore`` 与 ``ReviewCaseStore``。私有化会逼第二个
        调用方自己写一遍「没有才写」—— 两处各写一遍的形态是加一条规则只改一半，
        而漏掉的那一半表现成「做完的事又欠了一遍」。
        """
        for intent in intents:
            row = open_continuation(intent, now=at)
            # **已有的行一格不动**（与 SQLite 那份的「没有才写」逐字同一条）。
            self._continuations.setdefault(row.continuation_id, row)

    def _rearm(
        self, job: ResearchJob, state: JobState, *, reason: str, at: datetime
    ) -> None:
        kind = ContinuationKind.DISPATCH_BATCH
        for cid, row in list(self._continuations.items()):
            if row.kind is not kind or row.business_key != job.job_id:
                continue
            if state is JobState.QUEUED:
                if row.state is ContinuationState.BLOCKED and row.blocked_on is BlockedOn.PAUSE:
                    self._continuations[cid] = _moved(
                        row,
                        state=ContinuationState.PENDING,
                        blocked_on=None,
                        reason="",
                        due_at=at,
                        updated_at=at,
                    )
                continue
            if state is JobState.CANCELLED:
                if row.state in (ContinuationState.PENDING, ContinuationState.BLOCKED):
                    self._continuations[cid] = _moved(
                        row,
                        state=ContinuationState.DONE,
                        blocked_on=None,
                        reason=reason,
                        updated_at=at,
                    )
                continue
            if row.state is not ContinuationState.PENDING:
                # ``CLAIMED`` 的行在一个控制器手里：把它从别人手里改走，两个写入者
                # 都会以为自己是对的。见 SQLite 那份 ``_rearm``。
                continue
            blocked_on = BlockedOn.PAUSE if state is JobState.PAUSED else BlockedOn.CAPACITY
            self._continuations[cid] = _moved(
                row,
                state=ContinuationState.BLOCKED,
                blocked_on=blocked_on,
                reason=reason,
                due_at=at,
                updated_at=at,
            )

    # ------------------------------------------------------------------
    def _live_attempt(self, work_item_id: str) -> JobAttempt | None:
        for attempt in self.attempts(work_item_id):
            if attempt.is_live:
                return attempt
        return None

    @staticmethod
    def _already_done(
        current: JobAttempt, target: AttemptState, *, already: str, outcome: str
    ) -> bool:
        """与 ``SqliteJobStore`` 那份逐条同义。判据见 ``tests/test_job_contract.py``。"""
        if current.state is AttemptState.LEASED:
            return False
        if current.state is not target:
            raise JobStateError(
                f"租约 {current.attempt_id} 已经是 {current.state.value}，不能再"
                f"{'结算' if target is AttemptState.SETTLED else '交还'}。\n"
                "只有活租约能被结算或交还 —— 已经定局的那条不是「再来一次」，而是另一个"
                "决定，而它已经有结论了。"
            )
        if already != outcome:
            raise JobStateError(
                f"租约 {current.attempt_id} 已经以 {already!r} 定局，不能改成 {outcome!r}。\n"
                "同一个 attempt 的结局是一次性写下的：允许改写它，『重发』就成了一条"
                "改结论的旁路。"
            )
        return True

    def _close(self, aid: str, target: AttemptState, *, note: str) -> JobAttempt:
        current = self._attempts.get(aid)
        if current is None:
            raise JobStateError(f"没有这条租约：{aid}")
        if self._already_done(current, target, already=current.note, outcome=note):
            return current
        closed = replace(
            current, state=target, settled_at=utc_now().isoformat(), note=note
        )
        self._attempts[aid] = closed
        return closed


class InMemorySceneStore(SceneStore):
    """Scene 的版本与当前状态。**没有锁** —— 与其余内存实现同一条边界。

    它存在的理由与 ``InMemoryEvidenceStore`` 相同：验收要求里那条「本地流水线在
    没有磁盘状态的情况下能跑完」。并发正确性只由 ``SqliteSceneStore`` 背书 ——
    这里的 CAS 是「查一次字典再写一次」，两个线程中间插入一次写就会双双成功，
    而那正是 SQLite 那份的 ``BEGIN IMMEDIATE`` 要挡的。把并发判据也放进共享契约，
    就得给这里加锁：那不是「补齐契约」，是把一个测试替身伪装成并发存储。
    """

    def __init__(self) -> None:
        self._versions: dict[tuple[str, int], SceneVersion] = {}
        self._records: dict[str, SceneRecord] = {}

    # ------------------------------------------------------------ 版本
    def put_version(self, version: SceneVersion) -> None:
        key = (version.scene_id, version.version)
        existing = self._versions.get(key)
        if existing is None:
            self._versions[key] = version
            return
        if existing.frozen_sha256 != version.frozen_sha256:
            raise StoreError(
                f"已有 scene_id={version.scene_id!r} version={version.version} 的版本，"
                f"但摘要与本次不同。**不覆盖** —— 覆盖会让按版本号引用它的工作单元"
                f"静默换了规则。"
            )
        if existing.workspace != version.workspace:
            raise StoreError(
                f"scene_id={version.scene_id!r} version={version.version} 已属于工作区 "
                f"{existing.workspace!r}，本次声称的是 {version.workspace!r}。"
            )

    def get_version(self, scene_id: str, version: int) -> SceneVersion | None:
        return self._versions.get((scene_id, version))

    def versions(self, scene_id: str) -> tuple[SceneVersion, ...]:
        return tuple(
            version
            for (sid, _), version in sorted(self._versions.items())
            if sid == scene_id
        )

    # ------------------------------------------------------------ 状态
    def put_record(self, record: SceneRecord, *, expected_revision: int | None) -> None:
        current = self._records.get(record.scene_id)
        if expected_revision is None:
            if record.revision != 0:
                raise StoreError(
                    f"首次创建 scene={record.scene_id!r} 时 revision 必须是 0，"
                    f"本次是 {record.revision}。"
                )
            if current is not None:
                raise SceneConflictError(
                    f"scene={record.scene_id!r} 已经存在（工作区 {current.workspace!r}，"
                    f"revision={current.revision}），本次调用声称它是首次创建。"
                )
            self._records[record.scene_id] = record
            return
        if record.revision != expected_revision + 1:
            raise StoreError(
                f"scene={record.scene_id!r} 的写入 revision 必须是 "
                f"{expected_revision + 1}（读到的 {expected_revision} 加一），"
                f"本次是 {record.revision}。状态变更必须走 SceneRecord 上的那几个方法。"
            )
        if current is None:
            raise SceneConflictError(
                f"scene={record.scene_id!r} 不存在，本次调用以为它在第 "
                f"{expected_revision} 版。一个字都没写。"
            )
        if current.revision != expected_revision:
            raise SceneConflictError(
                f"scene={record.scene_id!r} 现在是第 {current.revision} 版，"
                f"本次调用读到的是第 {expected_revision} 版。一个字都没写。\n"
                f"库里那条属于工作区 {current.workspace!r}，本次写的是 "
                f"{record.workspace!r} —— 若两者不同，这更可能是一次配错根目录的写入。"
            )
        self._records[record.scene_id] = record

    def get_record(self, scene_id: str) -> SceneRecord | None:
        return self._records.get(scene_id)

    def records(self, *, workspace: str | None = None) -> tuple[SceneRecord, ...]:
        return tuple(
            record
            for _, record in sorted(self._records.items())
            if workspace is None or record.workspace == workspace
        )


class InMemoryVenueAssetStore(VenueAssetStore):
    """场地资产池的不落盘实现。判据见 ``tests/test_asset_store_contract.py``。

    ## 工作区绑定与 SQLite 那一份同形

    第一行写入建立绑定，第二个工作区的写入被拒 —— 与 ``InMemoryEvidenceStore``
    逐字同一套话（那边解释了为什么一个没有 ``Settings`` 的替身仍然要实现这条判据）。

    ## 折，且**只按 id**

    ``upsert_asset`` 是折（池子是累积并修订的），身份是 ``venue_asset_id``：
    按内容插入会让「同一块场地被重新看到」在报告上读成「这个市场多了一块场地」。

    ## 合并规则是接进来的，不是这里写的

    与 SQLite 那一份同一个构造参数（``ports/pool.py::AssetMerge``）：两份实现必须
    用**同一条**规则，而规则属于 ``application/pool.py``。两份各写一遍，就是
    「契约测试看着两个实现，而两个实现各自长出了自己的语义」那个失败。

    ## 它**没有**SQLite 那一份的事务边界

    ``upsert_asset`` 在这里是「读字典、算、写字典」三步，中间可以插进另一次调用 ——
    那正是 ``BEGIN IMMEDIATE`` 要挡的。共享契约里因此**没有**并发判据
    （与 ``InMemoryEvidenceStore`` 逐字同一条处置）：把一个测试替身伪装成并发存储，
    比让它明说自己没有锁更坏。

    ## ``ledger``：折入账要接在哪一轮 Run 上

    与 SQLite 那一份**不是同一个形状**，因为那边池子与 Run 记录本来就是同一个
    ``db_path`` 上的两张表（一个库一份写事务），而这里两个替身是两个独立对象、状态
    不共享 —— 所以「这一笔账记到哪」必须由构造参数接进来，缺省 ``None`` 就是「这个
    池子没有接账」。

    **``fold`` 给了而 ``ledger`` 是 ``None`` ⇒ 抛 ``StoreError``，不是静默忽略。**
    静默忽略正是这条账要挡的那件事：一次没记会让「这一次没写」与「这个功能还没造」
    在盘上同形，而池子那一行已经落下去了。既有构造点全都不传 ``ledger``、也不传
    ``fold``，所以这一个缺省值不会让任何一处变红。

    **原子性按「先问后写」拿，不是按事务拿**：``upsert_asset`` 先合并、再让
    ``ledger.record_fold`` 把三条判定问完，最后才写字典型 —— 会失败的三步全在两次
    字典写之前，而那两步不会失败。见 ``InMemoryRunStore.record_fold``。
    """

    def __init__(
        self, merge: AssetMerge, ledger: InMemoryRunStore | None = None
    ) -> None:
        self._merge = merge
        #: 折入账的落点。``None`` = 这个池子没有接账本，于是它不接受 ``fold=``。
        self._ledger = ledger
        self._workspace: str | None = None
        self._assets: dict[str, VenueAsset] = {}

    def bind_workspace(self, workspace: str) -> None:
        self._require_workspace(workspace)
        if self._workspace is None:
            self._workspace = workspace

    def _require_workspace(self, workspace: str) -> None:
        """**只问不改**的那一半 —— ``bind_workspace`` 是它加一句设值。

        分成两句是为了 ``upsert_asset`` 的折入那一支：那里要先把「会不会被拒」问完
        （连同记账那三条判定的次序），再动任何一格状态。合并成一句的话，一次被拒的
        折入会留下一个已经把库绑到某个工作区上的替身 —— SQLite 那边整笔回滚，绑定
        也跟着回滚。判词一份，两个调用点共用。
        """
        if self._workspace is None or self._workspace == workspace:
            return
        raise StoreError(
            f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
            f"{workspace!r}。\n"
            "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有答案，"
            "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
            "要用另一个工作区，换一个库（或先把这个库移开）。"
        )

    def upsert_asset(
        self, incoming: VenueAsset, *, fold: FoldAttribution | None = None
    ) -> VenueAsset:
        if fold is None:
            self.bind_workspace(incoming.workspace)
            merged = self._merge(self._assets.get(incoming.venue_asset_id), incoming)
            self._assets[merged.venue_asset_id] = merged
            return merged
        if self._ledger is None:
            raise StoreError(
                "这一笔折入带着归属（Run "
                f"{fold.run_id}），而这个池子没有接折入账本。\n"
                "**不静默丢掉这一笔归属。** 丢掉它会让「这一次没记」与「这个功能还没造」"
                "读成同一件事，而池子那一行照样落了盘 —— 那正是 §B17 条件 2 要挡的假账："
                "折了却没记，结束时报一份空主张，而账是平的。\n"
                "接账本的办法是构造时给 ``ledger=``（一个 ``InMemoryRunStore``）；"
                "一次**不属于任何一轮**的折入则应当留空 ``fold=None`` —— 那是一个被"
                "声明出来的事实，不是一句要猜的话。"
            )
        # 次序是承重的，它买的是「同生共死」：**会失败的四步全在两次字典写之前**。
        #   1. ``_require_workspace`` —— 只问不改（所以一次被拒的折入不会把库绑上）；
        #   2. ``self._merge`` —— 合并规则是纯的，它不改任何状态；
        #   3. ``record_fold`` —— 三条判定都在里面，任何一条抛出去都还没写过一个字；
        #   4. ``bind_workspace``（此时不可能再拒）与两行列写。
        # SQLite 那份拿这件事靠一个写事务；这里没有事务可拿，所以靠次序。
        # 次序与 SQLite 那份逐位对齐：绑定那一条判定在最前（那边 ``bind_workspace``
        # 也是第一句），于是同一个输入在两个实现上给出**同一条**拒词。
        self._require_workspace(incoming.workspace)
        merged = self._merge(self._assets.get(incoming.venue_asset_id), incoming)
        self._ledger.record_fold(
            fold, venue_asset_id=merged.venue_asset_id, at=utc_now()
        )
        self.bind_workspace(incoming.workspace)
        self._assets[merged.venue_asset_id] = merged
        return merged

    def get_asset(self, venue_asset_id: str) -> VenueAsset | None:
        found = self._assets.get(venue_asset_id)
        if found is None:
            return None
        return self._own(found, where=f"venue_asset[{venue_asset_id}]")

    def list_assets(self) -> tuple[VenueAsset, ...]:
        return tuple(
            self._own(asset, where=f"venue_asset[{asset_id}]")
            for asset_id, asset in sorted(self._assets.items())
        )

    def _own(self, asset: VenueAsset, *, where: str) -> VenueAsset:
        if asset.workspace == self._workspace:
            return asset
        raise StoreError(
            f"{where} 写着 workspace={asset.workspace!r}，而这个存储绑在 "
            f"{self._workspace!r} 上。\n"
            "这一行不属于本存储的部署，所以它**不被返回**，也不被跳过 —— 跳过会让"
            "「这条不属于你」与「没有这一条」在调用点长成同一个空结果。"
        )


class InMemoryAccountVenueRelationStore(AccountVenueRelationStore):
    """责任关系表的不落盘实现。判据见 ``tests/test_relation_store_contract.py``。

    ## 工作区绑定、折、合并规则：三条都与 ``InMemoryVenueAssetStore`` 同形

    绑定：第一行写入建立绑定，第二个工作区的写入被拒。折：身份是**四段键**
    （``relation_key()``），按内容插入会让「同一条关系又被复核了一次」在报告上读成
    「这个市场多了一条责任关系」。合并规则由构造参数接进来 —— 两份实现必须用同一条
    规则，而规则属于 ``application/relations.py``。

    ## 字典的键是**四段元组**，不是 ``relation_key()`` 那个字符串

    两者出自**同一个**方法（``relation_key_parts()``），但元组是**结构**而字符串是
    **拼出来的形状**：拿字符串当键时，一个含分隔符的取值会让两行撞进同一个格子
    （``a/b`` 与 ``a`` + ``b`` 拼出同一个串），而模型上的 ``check_canonical_id``
    恰好把这种取值挡在了外面 —— 两道判据同时成立才安全，叠在一起就多一层「只有一道
    没拦住才发现」的运气。字典用元组，排序用 ``relation_key()``（人要读它）。

    ## 它**没有**SQLite 那一份的事务边界

    ``upsert_relation`` 在这里是「读字典、算、写字典」三步，中间可以插进另一次调用
    —— 与 ``InMemoryVenueAssetStore`` 逐字同一条处置：把一个测试替身伪装成并发存储，
    比让它明说自己没有锁更坏。
    """

    def __init__(self, merge: RelationMerge) -> None:
        self._merge = merge
        self._workspace: str | None = None
        self._relations: dict[tuple[str, str, str, str], AccountVenueRelation] = {}

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有答案，"
                "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
                "要用另一个工作区，换一个库（或先把这个库移开）。"
            )

    def upsert_relation(self, incoming: AccountVenueRelation) -> AccountVenueRelation:
        self.bind_workspace(incoming.workspace)
        key = incoming.relation_key_parts()
        merged = self._merge(self._relations.get(key), incoming)
        self._relations[merged.relation_key_parts()] = merged
        return merged

    def relations_for_account(self, account_id: str) -> tuple[AccountVenueRelation, ...]:
        return self._sorted(
            row for row in self._relations.values() if row.account_id == account_id
        )

    def relations_for_asset(self, venue_asset_id: str) -> tuple[AccountVenueRelation, ...]:
        return self._sorted(
            row for row in self._relations.values() if row.venue_asset_id == venue_asset_id
        )

    def list_relations(self) -> tuple[AccountVenueRelation, ...]:
        return self._sorted(self._relations.values())

    def _sorted(self, rows: Iterable[AccountVenueRelation]) -> tuple[AccountVenueRelation, ...]:
        """排序键只有一处定义（``relation_key()``），两个实现都不许自己定一个顺序。"""
        return tuple(
            sorted((self._own(row) for row in rows), key=lambda relation: relation.relation_key())
        )

    def _own(
        self, relation: AccountVenueRelation, *, where: str | None = None
    ) -> AccountVenueRelation:
        if relation.workspace == self._workspace:
            return relation
        raise StoreError(
            f"{where or f'account_venue_relation[{relation.relation_key()}]'} 写着 "
            f"workspace={relation.workspace!r}，而这个存储绑在 {self._workspace!r} 上。\n"
            "这一行不属于本存储的部署，所以它**不被返回**，也不被跳过 —— 跳过会让"
            "「这条不属于你」与「没有这一条」在调用点长成同一个空结果。"
        )


class InMemoryAccountPoolStore(AccountPoolStore):
    """账号池的不落盘实现。判据见 ``tests/test_account_store_contract.py``。

    ## 工作区绑定、折、合并规则：三条都与上面两个同形

    绑定：第一行写入建立绑定，第二个工作区的写入被拒。折：身份是 ``account_id``
    （按内容插入会让「同一个账号又被研究了一次」在报告上读成「这个市场多了一个
    账号」）。合并规则由构造参数接进来 —— 两份实现必须用同一条规则，而规则属于
    ``application/accounts.py``。

    ## 它只有两个读，而这是这一张表与另外两张的**同一处**差别

    没有按域名 / 名字 / 地址找账号的入口（``ports/pool.py::AccountPoolStore`` 的
    类文档）。一个测试替身上多一条「顺手」的查询路径，比主存上多一条更坏 —— 主存上
    那一条至少要有人写 SQL，而替身上那一条只差一个字典推导，于是它会先出现在测试里，
    再被当成契约的一部分照抄进主存。

    ## 它**没有**SQLite 那一份的事务边界

    ``upsert_account`` 在这里是「读字典、算、写字典」三步，中间可以插进另一次调用
    —— 与上面两个内存实现逐字同一条处置：把一个测试替身伪装成并发存储，比让它明说
    自己没有锁更坏。
    """

    def __init__(self, merge: AccountMerge) -> None:
        self._merge = merge
        self._workspace: str | None = None
        self._accounts: dict[str, MarketAccount] = {}

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有答案，"
                "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
                "要用另一个工作区，换一个库（或先把这个库移开）。"
            )

    def upsert_account(self, incoming: MarketAccount) -> MarketAccount:
        self.bind_workspace(incoming.workspace)
        merged = self._merge(self._accounts.get(incoming.account_id), incoming)
        self._accounts[merged.account_id] = merged
        return merged

    def get_account(self, account_id: str) -> MarketAccount | None:
        found = self._accounts.get(account_id)
        if found is None:
            return None
        return self._own(found, where=f"market_account[{account_id}]")

    def list_accounts(self) -> tuple[MarketAccount, ...]:
        return tuple(
            self._own(account, where=f"market_account[{account_id}]")
            for account_id, account in sorted(self._accounts.items())
        )

    def _own(self, account: MarketAccount, *, where: str) -> MarketAccount:
        if account.workspace == self._workspace:
            return account
        raise StoreError(
            f"{where} 写着 workspace={account.workspace!r}，而这个存储绑在 "
            f"{self._workspace!r} 上。\n"
            "这一行不属于本存储的部署，所以它**不被返回**，也不被跳过 —— 跳过会让"
            "「这条不属于你」与「没有这一条」在调用点长成同一个空结果。"
        )


class InMemoryFetchAttemptStore(FetchAttemptStore):
    """访问尝试账的不落盘实现。判据见 ``tests/test_fetch_attempt_store_contract.py``。

    ## 三条与上面几个同形，另有一条是这一张表独有

    绑定：第一行写入建立绑定，第二个工作区的写入被拒（``StoreError`` —— 与
    ``InMemoryAccountPoolStore`` 同一条：读侧要报错而不是跳过，而读侧报错时**不能**
    用 ``EvidenceError``，因为那会让调用方走错分支）。

    幂等与冲突：同一个 ``attempt_id`` 配相同内容 → 幂等；配不同内容 → **抛错**。
    这一条与证据那一份看起来一样，理由却不同 —— 证据 id 是内容寻址的，所以冲突
    只可能来自手工指定；而 ``attempt_id`` 算的是**这一次尝试**（谁、哪一跳、什么
    时候开始的），所以同 id 不同内容意味着两个生产者对同一次尝试说了两套话。

    顺序：与端口那一句逐字同一条 —— 按 ``(started_at, attempt_id)`` 降序，尾部那个
    ``attempt_id`` 不是装饰，同刻的一批行必须有稳定顺序（见
    ``ports/fetch_attempt.py::list_attempts``）。

    ## 独有的那一条：**三个筛子都是「有没有真的传」**

    ``scope=""`` 是一个值（不属于任何主体的尝试），``scope=None`` 是「不筛」。
    所以下面写的都是 ``is not None``，而这不是风格 —— 写成真值判断会让
    「不属于任何主体的抓取非常多」被读成「不筛」，两者的表现在报告上同形。
    主存那一份把这句理由写在 SQL 的拼接处，这里把它写在同样的位置上。
    """

    def __init__(self) -> None:
        self._workspace: str | None = None
        self._attempts: dict[str, FetchAttempt] = {}

    def bind_workspace(self, workspace: str) -> None:
        """记下这个替身属于哪个工作区。已经绑了别的就拒绝。"""
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有答案，"
                "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
                "要用另一个工作区，换一个库（或先把这个库移开）。"
            )

    def put_attempt(self, attempt: FetchAttempt) -> None:
        self.bind_workspace(attempt.workspace)
        existing = self._attempts.get(attempt.attempt_id)
        if existing is None:
            self._attempts[attempt.attempt_id] = attempt
            return
        if existing != attempt:
            raise StoreError(
                f"已有 attempt_id={attempt.attempt_id!r} 的记录，但内容与本次不同。\n"
                "这个 id 算的是**这一次尝试**（谁、哪一跳、什么时候开始的），"
                "所以同一个 id 配不同内容只可能意味着两个人对同一次尝试说了两套话"
                "（例如一个说拿到了 200、另一个说超时）。静默保留先写的那条，会让"
                "事后那句「这个 URL 那天到底怎么样了」有一个看不出来的错误答案。"
            )

    def get_attempt(self, attempt_id: str) -> FetchAttempt | None:
        """``None`` 只有一个意思：**没有这个 id 的行**（与主存那一份一致）。"""
        found = self._attempts.get(attempt_id)
        if found is None:
            return None
        return self._own(found, where=f"fetch_attempt[{attempt_id}]")

    def list_attempts(
        self,
        *,
        scope: str | None = None,
        url: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[FetchAttempt]:
        rows = [self._own(row, where="fetch_attempt") for row in self._attempts.values()]
        if scope is not None:
            rows = [row for row in rows if row.scope == scope]
        if url is not None:
            rows = [row for row in rows if row.url == url]
        if since is not None:
            # 与主存那一份同一个刻度：比较前先归一，免得 ``+08:00`` 与 ``Z`` 两个
            # 串按字典序比出一个像答案的东西。
            floor = to_utc(since)
            rows = [row for row in rows if row.started_at >= floor]
        rows.sort(key=lambda row: (row.started_at, row.attempt_id), reverse=True)
        return rows if limit is None else rows[:limit]

    def _own(self, attempt: FetchAttempt, *, where: str) -> FetchAttempt:
        if attempt.workspace == self._workspace:
            return attempt
        raise StoreError(
            f"{where} 写着 workspace={attempt.workspace!r}，而这个存储绑在 "
            f"{self._workspace!r} 上。\n"
            "这一行不属于本存储的部署，所以它**不被返回**，也不被跳过 —— 跳过会让"
            "「这条不属于你」与「没有这一条」在调用点长成同一个空结果。"
        )


class InMemoryAccountCohortStore(AccountCohortStore):
    """冻结成员名单的不落盘实现（§B12）。判据见 ``tests/test_cohort_store_contract.py``。

    ## 与上面三个不同：它**没有**构造参数

    合并规则不在这里接，因为没有一条规则可接 —— 一份名单的内容完全由 ``cohort_id``
    决定（那个 id 是内容寻址的），所以「同一个键配不同内容」这件在前三张表上必须报错
    的事，在这一张表上**是两个不同的键**（``ports/pool.py`` 的模块 docstring）。

    唯一会变的一格是 ``created_at``，取更早的那一个。**它是就地一个 ``min``，不是一条
    可以让人选错的策略** —— 这一点必须与前三张表分开说，不然一个未来的读者会以为
    「这一张表只是碰巧还没有合并规则」。

    ## 它**没有**SQLite 那一份的事务边界

    同上面三个：读—改—写的三步之间可以插进另一次调用。这一张表上那一步只改
    ``created_at`` 一格，所以重叠的后果比前三张表小，但**不是零**（两次调用各读到
    「还没有」、各写各的，后写的那一次把前一次挤掉 —— 而两次写的 ``created_at`` 里
    更早的那一个才是对的）。一个测试替身伪装成并发存储，比让它明说没有锁更坏。
    """

    def __init__(self) -> None:
        self._workspace: str | None = None
        self._cohorts: dict[str, AccountCohort] = {}

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有答案，"
                "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
                "要用另一个工作区，换一个库（或先把这个库移开）。"
            )

    def put_cohort(self, cohort: AccountCohort) -> AccountCohort:
        """写入，返回落盘后的那一份（``created_at`` 取更早的那一个）。"""
        self.bind_workspace(cohort.workspace)
        existing = self._cohorts.get(cohort.cohort_id)
        stored = cohort
        if existing is not None and existing.created_at < cohort.created_at:
            stored = cohort.model_copy(update={"created_at": existing.created_at})
        self._cohorts[stored.cohort_id] = stored
        return stored

    def get_cohort(self, cohort_id: str) -> AccountCohort | None:
        found = self._cohorts.get(cohort_id)
        if found is None:
            return None
        return self._own(found, where=f"account_cohort[{cohort_id}]")

    def list_cohorts(self) -> tuple[AccountCohort, ...]:
        """按 ``(created_at, cohort_id)`` 排序，**早的在前**。"""
        return tuple(
            self._own(cohort, where=f"account_cohort[{cohort.cohort_id}]")
            for cohort in sorted(
                self._cohorts.values(), key=lambda c: (c.created_at, c.cohort_id)
            )
        )

    def _own(self, cohort: AccountCohort, *, where: str) -> AccountCohort:
        if cohort.workspace == self._workspace:
            return cohort
        raise StoreError(
            f"{where} 写着 workspace={cohort.workspace!r}，而这个存储绑在 "
            f"{self._workspace!r} 上。\n"
            "这一行不属于本存储的部署，所以它**不被返回**，也不被跳过 —— 跳过会让"
            "「这条不属于你」与「没有这一条」在调用点长成同一个空结果。"
        )


class InMemoryMaintenanceStore(MaintenanceStore):
    """观测、变更信号与复核回执。**没有锁**，边界同上。"""

    def __init__(self) -> None:
        #: 观测按主键去重，所以存成 dict 而不是 list —— 幂等由键本身保证。
        self._observations: dict[tuple, Observation] = {}
        self._signals: dict[tuple, ChangeSignal] = {}
        self._claims: dict[tuple[str, int], MaintenanceClaim] = {}

    # ------------------------------------------------------------ 观测
    def put_observation(self, observation: Observation) -> None:
        self._observations.setdefault(_observation_key(observation), observation)

    def observations(
        self, *, workspace: str, subject_kind: str | None = None
    ) -> tuple[Observation, ...]:
        rows = [
            item
            for item in self._observations.values()
            if item.workspace == workspace
            and (subject_kind is None or item.subject_kind == subject_kind)
        ]
        rows.sort(
            key=lambda o: (o.subject_kind, o.subject_id, o.aspect.value, o.observed_at, o.source_ref)
        )
        return tuple(rows)

    # ------------------------------------------------------------ 信号
    def put_signal(self, signal: ChangeSignal) -> None:
        self._signals.setdefault(_signal_key(signal), signal)

    def signals(self, *, workspace: str) -> tuple[ChangeSignal, ...]:
        rows = [item for item in self._signals.values() if item.workspace == workspace]
        rows.sort(
            key=lambda sig: (
                sig.signalled_at,
                sig.subject_kind,
                sig.subject_id,
                sig.aspect.value,
            )
        )
        return tuple(rows)

    # ------------------------------------------------------------ 回执
    def claim(self, claim: MaintenanceClaim) -> ClaimOutcome:
        if claim.state is not MaintenanceState.CLAIMED:
            raise StoreError(
                f"claim 只接受 CLAIMED 的回执，本次是 {claim.state.value}"
                f"（{claim.dedupe_key!r}）。"
            )
        existing = self._latest_claim(claim.dedupe_key)
        if existing is None:
            self._claims[(claim.dedupe_key, 1)] = claim
            return ClaimOutcome(claim=claim, won=True)
        _check_same_cell(existing, claim)
        # 只有 FAILED 产生下一条 —— 见 ``adapters/sqlite/scenes.py`` 的同一条判据。
        if existing.state is not MaintenanceState.FAILED:
            return ClaimOutcome(claim=existing, won=False)
        attempt = existing.attempt + 1
        retried = claim.model_copy(update={"attempt": attempt})
        self._claims[(claim.dedupe_key, attempt)] = retried
        return ClaimOutcome(claim=retried, won=True)

    def finish(self, claim: MaintenanceClaim) -> None:
        if claim.state is MaintenanceState.CLAIMED:
            raise StoreError(
                f"finish 只接受终态回执，本次是 CLAIMED（{claim.dedupe_key!r}）。"
            )
        stored = self._claims.get((claim.dedupe_key, claim.attempt))
        if stored is None:
            raise StoreError(
                f"没有 dedupe_key={claim.dedupe_key!r} attempt={claim.attempt} 这条回执，"
                "本次却要结掉它。回执只能由认领产生。"
            )
        if stored.state is not MaintenanceState.CLAIMED:
            if stored.model_dump(mode="json") == claim.model_dump(mode="json"):
                return  # 同一个终态写第二次：幂等
            raise StoreError(
                f"dedupe_key={claim.dedupe_key!r} attempt={claim.attempt} 已经是 "
                f"{stored.state.value}，本次要写成 {claim.state.value}。\n"
                "两次不同的结束意味着有两个执行者都以为自己在跑同一次尝试。"
            )
        self._claims[(claim.dedupe_key, claim.attempt)] = claim

    def claims(self, scene_id: str) -> tuple[MaintenanceClaim, ...]:
        rows = [item for item in self._claims.values() if item.scene_id == scene_id]
        rows.sort(
            key=lambda c: (c.claimed_at, c.subject_kind, c.aspect.value, c.attempt)
        )
        return tuple(rows)

    # ------------------------------------------------------------ 内部
    def _latest_claim(self, dedupe_key: str) -> MaintenanceClaim | None:
        found = [
            item for (key, _), item in self._claims.items() if key == dedupe_key
        ]
        if not found:
            return None
        return max(found, key=lambda item: item.attempt)


def _observation_key(observation: Observation) -> tuple:
    """观测的主键。与 ``adapters/sqlite`` 那条 PK 逐字段一致 —— 不一致会让两个
    实现去重的范围不同，而那正是契约测试要固定下来的东西。"""
    return (
        observation.workspace,
        observation.subject_kind,
        observation.subject_id,
        observation.aspect.value,
        observation.observed_at,
        observation.source_ref,
    )


def _signal_key(signal: ChangeSignal) -> tuple:
    return (
        signal.workspace,
        signal.subject_kind,
        signal.subject_id,
        signal.aspect.value,
        signal.signalled_at,
        signal.reason,
    )


def _check_same_cell(existing: MaintenanceClaim, claim: MaintenanceClaim) -> None:
    """``dedupe_key`` 必须真的指同一格。见 ``adapters/sqlite/scenes.py`` 的同名判据。"""
    for name in ("workspace", "scene_id", "scene_version", "subject_kind", "subject_id", "period"):
        if getattr(existing, name) != getattr(claim, name):
            raise StoreError(
                f"dedupe_key={claim.dedupe_key!r} 已经属于 "
                f"{getattr(existing, name)!r}（{name}），本次要认领的是 "
                f"{getattr(claim, name)!r}。键是内容寻址的，同一个键指了两格意味着"
                "记录是手工构造的。"
            )
    if existing.aspect is not claim.aspect:
        raise StoreError(
            f"dedupe_key={claim.dedupe_key!r} 已经属于 aspect={existing.aspect.value!r}，"
            f"本次是 {claim.aspect.value!r}。"
        )


__all__ = [
    "InMemoryEvidenceStore",
    "InMemoryVenueAssetStore",
    "InMemoryAccountPoolStore",
    "InMemoryFetchAttemptStore",
    "InMemoryAccountCohortStore",
    "InMemoryBudgetLedger",
    "InMemoryJobStore",
    "InMemoryRunStore",
    "InMemorySceneStore",
    "InMemoryMaintenanceStore",
    "InMemoryOutreachStore",
    "InMemoryReviewCaseStore",
    "InMemoryWakeupRegistry",
    "InMemoryProductCatalog",
    "InMemorySavedViewStore",
    "InMemoryWatchStore",
    "InMemoryActionItemStore",
    "InMemoryWorkstreamStore",
    "InMemoryEffectiveSceneStore",
    "InMemoryScheduleStore",
    "InMemoryNotificationStore",
    "InMemorySessionBindingStore",
]


class InMemoryWakeupRegistry(WakeupRegistry):
    """不落盘的唤醒归属：**测试替身**，一个工作区一行。

    边界与其余内存实现逐字相同：**没有锁**。``acquire`` 在这里的形状是「读一次
    字典、判一次、再写一次」，两个线程中间插进一次写就会双双认为自己拿到了 ——
    而那正是 ``SqliteWakeupRegistry`` 的 ``BEGIN IMMEDIATE`` 要挡的，也正是
    「唯一唤醒 owner」这条最不能出错的地方。所以这一份**只用于单进程测试**：
    任何一次真的部署都得用 SQLite 那一份。

    它存在的理由与其余内存实现相同：本地流水线必须能在没有磁盘状态的情况下跑完测试。
    """

    def __init__(self) -> None:
        self._owners: dict[str, WakeupOwner] = {}

    def acquire(self, workspace: str, *, holder: str, now: datetime) -> WakeupOwner | None:
        _require_text(workspace, "workspace")
        _require_text(holder, "holder")
        at = to_utc(now)
        current = self._owners.get(workspace)
        if current is None:
            owner = WakeupOwner(
                workspace=workspace, holder=holder, epoch=1, acquired_at=at, heartbeat_at=at
            )
            self._owners[workspace] = owner
            return owner
        if not current.is_stale(at):
            # **同名也不算「是我」** —— 见 ``ports/continuation.py::acquire``。
            return None
        owner = WakeupOwner(
            workspace=workspace,
            holder=holder,
            epoch=current.epoch + 1,
            acquired_at=at,
            heartbeat_at=at,
        )
        self._owners[workspace] = owner
        return owner

    def heartbeat(self, workspace: str, *, holder: str, epoch: int, now: datetime) -> bool:
        _require_text(workspace, "workspace")
        _require_text(holder, "holder")
        current = self._owners.get(workspace)
        # 三格全对才算续期，与 SQLite 那份逐字同一条。
        if current is None or (current.holder, current.epoch) != (holder, epoch):
            return False
        self._owners[workspace] = _revalidate(
            current, {"heartbeat_at": to_utc(now)}
        )
        return True

    def release(self, workspace: str, *, holder: str, epoch: int) -> bool:
        _require_text(workspace, "workspace")
        _require_text(holder, "holder")
        current = self._owners.get(workspace)
        if current is None or (current.holder, current.epoch) != (holder, epoch):
            return False
        del self._owners[workspace]
        return True

    def current(self, workspace: str) -> WakeupOwner | None:
        return self._owners.get(workspace)


class InMemoryOutreachStore(OutreachStore):
    """外向链的内存实现：**测试替身**，不是部署选项。

    边界与其余内存实现逐字相同：**没有锁**。``begin_attempt`` 在这里的形状是
    「查一次字典、判一次、再写一次」，两个线程中间插进一次写就会双双认为自己
    拿到了这一封 —— 而那正是 ``SqliteOutreachStore`` 那份的 ``BEGIN IMMEDIATE``
    要挡的。把并发判据也放进共享契约，就得给这里加锁：那不是「补齐契约」，
    是把一个测试替身伪装成并发存储。

    它存在的理由其余内存实现相同，而且是验收要求里的那一条：本地流水线必须能在
    没有密钥、没有外部服务、**没有磁盘状态**的情况下跑完测试。邮件那一侧同理，
    见 ``adapters/mail_capture.py`` —— 它也是一个真服务，不是 mock。
    """

    def __init__(self) -> None:
        #: 三张配置表都按 ``(id, 版)`` 存，**每一版一行**。读路径只回最新一版，旧版
        #: 留着 —— 一条草稿记的是 ``(id, 版)``，丢掉旧版会让那句记录变成一句查不到的话。
        self._families: dict[tuple[str, int], TemplateFamily] = {}
        self._policies: dict[tuple[str, int], WritingPolicy] = {}
        self._claims: dict[tuple[str, int], ApprovedClaim] = {}
        self._campaigns: dict[str, OutreachCampaign] = {}
        self._drafts: dict[tuple[str, int], OutreachDraft] = {}
        self._manifests: dict[str, OutreachManifest] = {}
        self._waves: dict[str, Wave] = {}
        self._authorizations: dict[str, WaveAuthorization] = {}
        self._attempts: dict[str, OutboundAttempt] = {}
        self._suppressions: dict[tuple[str, str], RecipientSuppression] = {}
        self._events: dict[str, CorrespondenceEvent] = {}
        self._events_by_dedupe: dict[str, str] = {}
        self._handoffs: dict[str, SalesHandoff] = {}

    # ------------------------------------------------------------ 配置

    def put_family(self, family: TemplateFamily) -> None:
        _put_config(
            self._families,
            family.family_id,
            family,
            what="模板族",
            why="改掉那一版会让已经按它写过稿的记录指向一族它当时没见过的配置。",
        )

    def families(self) -> tuple[TemplateFamily, ...]:
        return _latest_by_id(self._families)

    def put_writing_policy(self, policy: WritingPolicy) -> None:
        _put_config(
            self._policies,
            policy.policy_id,
            policy,
            what="写作策略",
            why="改掉那一版会让「这一稿按哪一版策略写的」在记录上仍然指着同一格，"
            "而那一格底下的东西已经换了。",
        )

    def writing_policies(self) -> tuple[WritingPolicy, ...]:
        return _latest_by_id(self._policies)

    def put_claim(self, claim: ApprovedClaim) -> None:
        _put_config(
            self._claims,
            claim.claim_id,
            claim,
            what="已批准说法",
            why="改掉那一版会让一条已经写进草稿的说法在记录上仍然「有出处」，"
            "而那句话已经变了。",
        )

    def approved_claims(self) -> tuple[ApprovedClaim, ...]:
        return _latest_by_id(self._claims)

    # ------------------------------------------------- 活动、批次、清单、授权

    def put_campaign(self, campaign: OutreachCampaign) -> None:
        existing = self._campaigns.get(campaign.campaign_id)
        if existing is None:
            self._campaigns[campaign.campaign_id] = campaign
            return
        if (existing.workspace, existing.scene_id) != (
            campaign.workspace,
            campaign.scene_id,
        ):
            raise SceneConflictError(
                f"活动 {campaign.campaign_id!r} 属于 workspace={existing.workspace!r} / "
                f"scene={existing.scene_id!r}，本次声称的是 "
                f"workspace={campaign.workspace!r} / scene={campaign.scene_id!r}。\n"
                "一个活动**不跨 Scene** —— 跨 Scene 的活动就是两个活动。"
            )
        if existing != campaign:
            raise StoreError(
                f"活动 {campaign.campaign_id!r} 已经存在，而本次的内容不同。不覆盖。"
            )

    def campaign(self, campaign_id: str) -> OutreachCampaign | None:
        return self._campaigns.get(campaign_id)

    def put_draft(self, draft: OutreachDraft) -> None:
        key = (draft.draft_id, draft.draft_version)
        existing = self._drafts.get(key)
        if existing is None:
            self._drafts[key] = draft
            return
        if existing != draft:
            raise StoreError(
                f"草稿 {draft.draft_id!r} 第 {draft.draft_version} 版已经存在，"
                f"而本次的内容不同（库里那版正文摘要 "
                f"{existing.body_sha256[:16]}…，本次是 {draft.body_sha256[:16]}…）。\n"
                "**不覆盖** —— 覆盖会让一份绑在旧正文摘要上的授权静默继续有效，"
                "而盘上看起来只是「再生成了一次」。要改内容就出一版新的。"
            )

    def draft(self, draft_id: str, version: int) -> OutreachDraft | None:
        return self._drafts.get((draft_id, version))

    def latest_drafts(self, *, wave_id: str) -> tuple[OutreachDraft, ...]:
        newest: dict[str, OutreachDraft] = {}
        for (draft_id, _), draft in self._drafts.items():
            if draft.wave_id != wave_id:
                continue
            current = newest.get(draft_id)
            if current is None or draft.draft_version > current.draft_version:
                newest[draft_id] = draft
        return tuple(newest[key] for key in sorted(newest))

    def put_manifest(self, manifest: OutreachManifest) -> None:
        existing = self._manifests.get(manifest.wave_id)
        if existing is None:
            self._manifests[manifest.wave_id] = manifest
            return
        if existing.manifest_sha256 == manifest.manifest_sha256 and existing == manifest:
            return
        raise StoreError(
            f"批次 {manifest.wave_id!r} 已经有一份清单，而本次这份的内容不同"
            f"（库里 {existing.manifest_sha256[:16]}…，本次 "
            f"{manifest.manifest_sha256[:16]}…）。\n"
            "**不覆盖** —— 换掉一份清单会让绑在旧摘要上的授权全部失去意义，"
            "而盘上只显示「清单更新了」。要换内容就换一个 wave。"
        )

    def manifest(self, wave_id: str) -> OutreachManifest | None:
        return self._manifests.get(wave_id)

    def put_wave(self, wave: Wave, *, expected_revision: int | None) -> None:
        if expected_revision is None:
            if wave.revision != 0:
                raise StoreError(
                    f"首次创建批次 {wave.wave_id!r} 时 revision 必须是 0，"
                    f"本次是 {wave.revision}。"
                )
            if wave.wave_id in self._waves:
                current = self._waves[wave.wave_id]
                raise IdempotencyConflictError(
                    f"批次 {wave.wave_id!r} 已经存在（工作区 {current.workspace!r}，"
                    f"revision={current.revision}），本次调用声称它是首次创建。\n"
                    "这条判据不看内容是否相同：`expected_revision=None` 的承诺是"
                    "「这是我建的第一个」，而一个已经存在的行让它不成立。"
                )
            self._check_manifest_agrees(wave)
            self._waves[wave.wave_id] = wave
            return
        if wave.revision != expected_revision + 1:
            raise StoreError(
                f"批次 {wave.wave_id!r} 的写入 revision 必须是 {expected_revision + 1}"
                f"（读到的 {expected_revision} 加一），本次是 {wave.revision}。"
            )
        current = self._waves.get(wave.wave_id)
        if current is None:
            raise WaveConflictError(
                f"批次 {wave.wave_id!r} 不存在，本次调用以为它在第 "
                f"{expected_revision} 版。一个字都没写。"
            )
        if current.revision != expected_revision:
            raise WaveConflictError(
                f"批次 {wave.wave_id!r} 现在是第 {current.revision} 版，"
                f"本次调用读到的是第 {expected_revision} 版。一个字都没写。\n"
                f"库里那条属于工作区 {current.workspace!r}，本次写的是 "
                f"{wave.workspace!r} —— 若两者不同，这更可能是一次配错根目录的写入。"
            )
        self._check_manifest_agrees(wave)
        self._waves[wave.wave_id] = wave

    def _check_manifest_agrees(self, wave: Wave) -> None:
        """见 ``adapters/sqlite/outreach.py`` 模块 docstring 的 ``put_wave`` 一节。"""
        if wave.revision < 2:
            return
        manifest = self._manifests.get(wave.wave_id)
        if manifest is None:
            raise StoreError(
                f"批次 {wave.wave_id!r} 已经走到 revision={wave.revision}，"
                "而它**没有**一份冻结清单。\n"
                "一份没有清单的批次不得进入授权与投递 —— 授权绑的就是清单摘要。"
            )
        if manifest.manifest_sha256 != wave.manifest_sha256:
            raise StoreError(
                f"批次 {wave.wave_id!r} 现在的清单摘要与库里那一份不一致"
                f"（库里 {manifest.manifest_sha256[:16]}…，本次 "
                f"{wave.manifest_sha256[:16]}…）。\n"
                "一个批次的清单在写下之后**不得被换掉**：授权绑的是摘要，"
                "换掉之后那些授权在记录上依然存在、依然「有效」，而它们批的是另一批信。"
            )

    def wave(self, wave_id: str) -> Wave | None:
        return self._waves.get(wave_id)

    def put_authorization(self, authorization: WaveAuthorization) -> None:
        _put_or_refuse(
            self._authorizations,
            authorization.authorization_id,
            authorization,
            what="授权",
            why="一次撤回应当是一条**新**记录（旧的那条留着），"
            "因为「谁在什么时候撤回了什么」正是事后要查的第一件事。"
            "就地改写那条记录会让那次撤回在盘上从来没有发生过。",
        )

    def authorizations(self, *, wave_id: str) -> tuple[WaveAuthorization, ...]:
        return tuple(
            sorted(
                (a for a in self._authorizations.values() if a.wave_id == wave_id),
                key=lambda a: (a.authorized_at, a.authorization_id),
            )
        )

    # ------------------------------------------------------------ 投递

    def attempt(self, send_key: str) -> OutboundAttempt | None:
        return self._attempts.get(send_key)

    def begin_attempt(
        self, attempt: OutboundAttempt, *, at: datetime, lease: timedelta | None = None
    ) -> AttemptOutcome:
        existing = self._attempts.get(attempt.send_key)
        if existing is None:
            self._attempts[attempt.send_key] = attempt
            return AttemptOutcome(attempt=attempt, won=True)
        if not existing.is_stale_at(at, lease=lease or DEFAULT_ATTEMPT_LEASE):
            # **这一封不是我的** —— 已定局，或者开出去还没到 lease。
            return AttemptOutcome(attempt=existing, won=False)
        # 同一个 send_key 的第 n 次尝试。次数由**行上那个值**加一，不由调用方给。
        opened = _revalidate(
            attempt,
            {
                "attempt": existing.attempt + 1,
                "state": MessageState.NOT_SENT,
                "started_at": attempt.started_at,
                "finished_at": None,
                "provider_message_id": "",
                "failure": "",
            },
        )
        self._attempts[attempt.send_key] = opened
        return AttemptOutcome(attempt=opened, won=True)

    def finish_attempt(self, attempt: OutboundAttempt) -> None:
        if attempt.finished_at is None:
            raise StoreError(
                f"结算 outreach_attempt/{attempt.send_key} 时没有完成时刻。"
            )
        current = self._attempts.get(attempt.send_key)
        if current is None:
            raise StoreError(
                f"没有 send_key={attempt.send_key!r} 这条尝试，不能结算。\n"
                "结算一条**没开始过**的投递，意味着有人凭空写下了一次发送。"
            )
        if current.state is not MessageState.NOT_SENT:
            if current.state is attempt.state:
                return
            if not may_be_settled_by_reconciliation(current, attempt):
                raise StoreError(
                    f"send_key={attempt.send_key!r} 已经以 {current.state.value} 定局，"
                    f"不能改成 {attempt.state.value}。\n"
                    "同一个逻辑发送的结局是一次性写下的：允许改写它，"
                    "「这一封到底发出去了没有」就没有答案了。"
                )
        if attempt.attempt != current.attempt:
            raise StoreError(
                f"send_key={attempt.send_key!r} 现在在第 {current.attempt} 次尝试，"
                f"而本次结算声称是第 {attempt.attempt} 次。"
            )
        self._attempts[attempt.send_key] = attempt

    def attempts(self, *, wave_id: str) -> tuple[OutboundAttempt, ...]:
        return tuple(
            sorted(
                (a for a in self._attempts.values() if a.wave_id == wave_id),
                key=lambda a: (a.recipient, a.draft_id),
            )
        )

    def attempts_by_recipient(self, recipient: str) -> tuple[OutboundAttempt, ...]:
        wanted = normalise_email(recipient)
        return tuple(
            sorted(
                (a for a in self._attempts.values() if a.recipient == wanted),
                key=lambda a: (a.started_at, a.wave_id, a.draft_id),
            )
        )

    # ------------------------------------------------------------ 抑制

    def put_suppression(self, suppression: RecipientSuppression) -> None:
        key = (suppression.recipient, suppression.reason.value)
        existing = self._suppressions.get(key)
        if existing is None:
            self._suppressions[key] = suppression
            return
        if existing == suppression:
            return
        raise StoreError(
            f"{suppression.recipient!r} 因为 {suppression.reason.value} 已经在抑制名单里，"
            "而本次这一条与库里那条不同。\n"
            "**不覆盖** —— 一条解除之后再收到同一理由的投诉是**两条**事实。"
            "就地改写会让「这个人被解除过抑制吗」永远答不上来。"
        )

    def suppressions(self, recipient: str) -> tuple[RecipientSuppression, ...]:
        wanted = normalise_email(recipient)
        return tuple(
            sorted(
                (s for (who, _), s in self._suppressions.items() if who == wanted),
                key=lambda s: (s.suppressed_at, s.reason.value),
            )
        )

    def is_suppressed(self, recipient: str, *, at) -> bool:
        moment = to_utc(at)
        return any(s.is_active_at(moment) for s in self.suppressions(recipient))

    # ------------------------------------------------------- 事件与交接

    def put_event(self, event: CorrespondenceEvent) -> bool:
        seen = self._events_by_dedupe.get(event.dedupe_key)
        if seen is None:
            self._events[event.event_id] = event
            self._events_by_dedupe[event.dedupe_key] = event.event_id
            return True
        existing = self._events[seen]
        if existing == event:
            return False
        raise StoreError(
            f"事件去重键 {event.dedupe_key[:16]}… 已经存在"
            f"（event_id={seen!r}），而本次这条的内容不同。\n"
            "同一个 (sendbox, provider_message_id) 下的两条**不同**事件的处置"
            "只能由人决定：既可能是提供方复用了消息 id，也可能是我们解析错了。"
            "静默丢掉新的那条会让第二种情况永远查不出来。"
        )

    def event(self, event_id: str) -> CorrespondenceEvent | None:
        return self._events.get(event_id)

    def events(self, *, workspace: str) -> tuple[CorrespondenceEvent, ...]:
        return tuple(
            sorted(
                (e for e in self._events.values() if e.workspace == workspace),
                key=lambda e: (e.received_at, e.event_id),
            )
        )

    def put_handoff(self, handoff: SalesHandoff) -> None:
        # **覆盖写** —— 与抑制相反。交接的状态推进是「这件事正在走」，
        # 读的人要的是当前值；抑制的解除是「这件事结束了」，要留痕。
        self._handoffs[handoff.handoff_id] = handoff

    def handoffs(self, *, workspace: str) -> tuple[SalesHandoff, ...]:
        return tuple(
            sorted(
                (h for h in self._handoffs.values() if h.workspace == workspace),
                key=lambda h: (h.created_at, h.handoff_id),
            )
        )


class InMemoryReviewCaseStore(ReviewCaseStore):
    """Review Inbox 的内存实现：**测试替身**，不是部署选项。

    边界与其余内存实现逐字相同：**没有锁**。``record_resolution`` 在这里的形状是
    「查一次字典、判一次版本、再写一次」，两个线程中间插进一次写就会双双认为自己
    成功 —— 而 SQLite 那一份的 ``BEGIN IMMEDIATE`` 正是为了不让它发生
    （``adapters/sqlite/review.py``）。一个测试替身伪装成并发存储，比让它明说没有锁
    更坏，所以这一句写在这里而不是省掉。

    ## 它仍然必须把三件拒绝做对

    没有锁不等于可以少判据。判据里有两条与并发**无关**，而是关于这两件对象本身：

    * 一件已经裁决过的案件**不重开**（``open_case`` 的 ``opened=False``）——
      重开是人做的（``ResolutionOutcome.REOPEN``）；
    * 一条决定与案件当前状态不相容就整次拒绝。

    这两条在 ``apply_resolution`` 与版本比对上，两个实现逐字相同 —— 因为它们不是
    存储策略，是领域规则。

    ## 它必须拿到 outbox，而且**没有缺省值**

    裁决落账要**同时**欠下一次对受影响对象的重评（架构 §9：「裁决后自动重评受影响
    对象」），而欠的那一行落在 ``continuation`` 表里 —— 在这个替身里，那张表是
    ``InMemoryJobStore`` 的一个字典。SQLite 那一侧不需要这个参数：两份写入
    （``review_case`` / ``human_resolution`` 与 ``continuation``）落在**同一个
    ``db_path``** 上，所以 ``record_resolution`` 自己开一个写事务就同时盖住它们。

    这里给不出缺省值。一个 ``jobs=None`` 的缺省会让「裁决欠下一次重评」在
    **每一个**用内存 store 的测试里静默失效 —— 而那些测试正是用来判
    「这条链走通了没有」的，它们会全绿。
    """

    def __init__(self, *, jobs: InMemoryJobStore) -> None:
        self._workspace: str | None = None
        self._cases: dict[str, ReviewCase] = {}
        self._resolutions: dict[str, list[HumanResolution]] = {}
        self._jobs = jobs

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有答案，"
                "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
                "要用另一个工作区，换一个库（或先把这个库移开）。"
            )

    def open_case(self, case: ReviewCase) -> CaseAdmission:
        self.bind_workspace(case.workspace)
        existing = self._cases.get(case.case_id)
        if existing is not None:
            return CaseAdmission(case=self._own(existing), opened=False)
        self._cases[case.case_id] = case
        self._resolutions.setdefault(case.case_id, [])
        return CaseAdmission(case=case, opened=True)

    def get_case(self, case_id: str) -> ReviewCase | None:
        found = self._cases.get(case_id)
        if found is None:
            return None
        return self._own(found)

    def list_cases(self) -> tuple[ReviewCase, ...]:
        """**未决的在前、早的在前**（同刻按 ``case_id``）—— 与 SQLite 那一份同序。

        第一格用 ``is_pending`` 而不是 ``state in PENDING_STATES``：未决的是哪三个
        只能有一处定义，散成第二份就会漂移，而漂移的表现是报告的第一行换了一件。
        """
        return tuple(
            self._own(case)
            for case in sorted(
                self._cases.values(),
                key=lambda c: (0 if is_pending(c.state) else 1, c.opened_at, c.case_id),
            )
        )

    def list_resolutions(self, case_id: str) -> tuple[HumanResolution, ...]:
        """早的在前（同刻按 ``resolution_id``）—— 与 SQLite 那一份同序。"""
        return tuple(
            sorted(
                self._resolutions.get(case_id, ()), key=lambda r: (r.at, r.resolution_id)
            )
        )

    def claim(self, case_id: str, *, by: str, at: datetime, expected_version: int) -> ReviewCase:
        """认领一件。**没有锁**（见类 docstring），判据与 SQLite 那一份逐字相同。

        **不 ``bind_workspace``**：这一件已经在盘上了，而「这一行属不属于本存储」由
        :meth:`_own` 判 —— 与 SQLite 那一份用 :func:`owned` 是同一条。
        """
        case = self._read_for_update(case_id, expected_version)
        changed = claim(case, by=by, at=at)
        self._cases[changed.case_id] = changed
        return changed

    def record_resolution(
        self, resolution: HumanResolution, *, expected_version: int
    ) -> ReviewCase:
        self.bind_workspace(resolution.workspace)
        case = self._read_for_update(resolution.case_id, expected_version)
        recorded = self._resolutions.setdefault(case.case_id, [])
        if any(row.resolution_id == resolution.resolution_id for row in recorded):
            raise StoreError(
                f"human_resolution[{resolution.resolution_id}] 已经在盘上了。\n"
                "这个 id **不是**内容寻址的 —— 它是调用方给的一次事件 id，所以"
                "撞主键是一次真实的重复写入，而不是一次重放。静默吞掉它，会让"
                "「同一件事被记成两条」这件事在盘上读不出来：流水账上那一格会"
                "多算一次，而两次写的内容可能已经不同了。"
            )
        updated = apply_resolution(case, resolution)
        self._cases[updated.case_id] = updated
        recorded.append(resolution)
        # **与上面那三行相邻，中间没有可能抛出的判定** —— 判据全在它们之前，与 SQLite
        # 那一份逐字同一条理由（写在拒绝之前会为一件没发生的事欠账；写在后面则可能
        # 在一次崩溃里永久丢掉欠账）。这里没有事务可开，所以「相邻」就是这一层能给出
        # 的全部。
        #
        # **而这一句的位置在内存侧判不出来**（实测三遍）：陈旧版本那条拒绝发生在拿到
        # ``case`` 之前，够不着它；「已记过」那条撞的是同一个 ``continuation_id``，被
        # 「没有才写」吸收；而把它挪到 ``apply_resolution`` 之前也没用 ——
        # ``intents_for_resolution`` **自己**会先算一次 ``apply_resolution``（它要判
        # 「这一件还在待办里吗」），所以在任何会被拒的输入上，它比那一句更早抛。
        # 真正判得动这个位置的是 SQLite 那一侧的事务性：
        # ``tests/test_review_store_contract.py`` 里那条注入判据（让欠账那一步抛，
        # 断言决定与案件一起回滚）。
        self._jobs.owe(intents_for_resolution(case, resolution), at=utc_now())
        return updated

    def _read_for_update(self, case_id: str, expected_version: int) -> ReviewCase:
        """读这一件并核版本。**认领与裁决共用**，与 SQLite 那一份逐字同一条。

        提出来而不是各写一遍：两处各写一遍的形态是「一边核了、一边忘了」，而忘了的
        那一边的表现是**一次静默的丢失**（后写的把前写的整个吃掉，两边都以为自己
        成功了），两份报告各自都自洽。
        """
        case = self._cases.get(case_id)
        if case is None:
            raise ReviewConflictError(
                f"{case_id!r} 不在盘上，本次调用以为它在第 {expected_version} 版。"
                "一个字都没写。"
            )
        case = self._own(case)
        if case.version != expected_version:
            raise ReviewConflictError(
                f"{case.case_id!r} 现在是第 {case.version} 版（状态 "
                f"{case.state.value}），本次调用读到的是第 {expected_version} 版。"
                "一个字都没写。\n"
                "这一条挡的是两个人同时动同一件：最后写的那个赢，而**两边都以为"
                "自己成功了**。重读那一件，按它的当前状态重新决定。"
            )
        return case

    def _own(self, case: ReviewCase, *, where: str = "") -> ReviewCase:
        if case.workspace == self._workspace:
            return case
        raise StoreError(
            f"{where or f'review_case[{case.case_id}]'} 写着 workspace="
            f"{case.workspace!r}，而这个存储绑在 {self._workspace!r} 上。\n"
            "这一行不属于本存储的部署，所以它**不被返回**，也不被跳过 —— 跳过会让"
            "「这条不属于你」与「没有这一条」在调用点长成同一个空结果。"
        )


def _require_sha256(value: str) -> str:
    """``spec_sha256`` 的形状判据。两个 Run 实现各有一份 —— 它是**形状**检查，
    不是契约（契约是异常类型与受理语义，由 ``tests/test_run_contract.py`` 在两个
    实现上各跑一遍）。与 ``_require_text`` 同一条分工。

    它唯一的用途是判断「同一个键是不是同一个请求」，所以一个形状不对的值不会报错
    —— 它只会让**每一次**提交都与别的不同，于是重发被读成新的。
    """
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(
            "spec_sha256 必须是 64 位小写十六进制。\n"
            "它是「同一个键配的是不是同一份规格」的唯一依据 —— 形状不对时那个判断"
            "会恒答「不是」，于是重发被受理成第二轮。"
        )
    return value


class InMemoryRunStore(RunStore):
    """不落盘的 Run 记录。语义与 SQLite 那份一致，判据见契约测试。

    **没有事务。** SQLite 那份的受理是一次 ``BEGIN IMMEDIATE`` 里的读-判-写；这里
    只有一条普通语句序列。单进程测试里两者行为相同，多进程下只有那一份守得住「同一
    轮不会被受理两次」—— 把这一份当并发记录用，会得到一个在单进程测试里正确、在两个
    提交者下开出两轮的实现。

    与 ``InMemoryJobStore`` 逐字同一条边界，也同一条理由：它存在只是为了让本地
    流水线能在没有磁盘状态的情况下跑完测试。
    """

    def __init__(self) -> None:
        #: 这个替身属于哪个工作区。``None`` = 还没写过任何一行。
        self._workspace: str | None = None
        #: 按 ``run_id`` 索引，与 SQLite 那份的主键一致 —— 于是「同一个键不会受理
        #: 两次」在这里也不是一条纪律，而是字典的键。
        self._runs: dict[str, Run] = {}
        #: 折入账，与 ``_runs`` 同键。**键在 = 账本在**（``admit`` 与那一行同时写），
        #: 值可以是空元组列表 —— 那正是「这一轮一行都没折」这个**真读数**。
        #: 它不是 SQLite 那份的 ``run_fold_ledger`` 与 ``run_fold`` 两张表：那边两张
        #: 是因为 SQL 没有「一个空集合」，这里一个 dict 的两层就是那件事本身。
        self._folds: dict[str, list[FoldReceipt]] = {}

    def admit(
        self,
        spec: RunSpec,
        *,
        spec_sha256: str,
        at: datetime,
        spent: Mapping[BudgetLine, int] | None = None,
    ) -> Run:
        _require_sha256(spec_sha256)
        refusal = admission_refusal(spec, at=at)
        if refusal:
            raise RunSpecError(refusal)

        rid = run_id(spec.workspace, spec.idempotency_key)
        existing = self._runs.get(rid)
        if existing is not None:
            if existing.spec_sha256 != spec_sha256:
                raise IdempotencyConflictError(
                    f"幂等键 {spec.idempotency_key!r} 在工作区 {spec.workspace!r} 下"
                    f"已经用于另一份规格（{existing.spec_sha256[:16]}… ≠ "
                    f"{spec_sha256[:16]}…，Run {rid}）。\n"
                    "同一个键的承诺是「重发不会受理两次」，不是「同一个键可以改内容」。"
                    "要改目标、信封或截止，那是**另一个 Run** —— 改这一份会让"
                    "「这一轮当时是按什么跑起来的」再也读不回来。"
                )
            # 重放**不问窗口**。次序与 SQLite 那份逐字相同，理由也在那里（链的累计
            # 会变，而「拿回同一行」这个承诺不变）。这里多一句：这个替身里没有并发，
            # 所以次序在这里不挡任何东西 —— 它被照着写，是因为契约测试拿两个实现的
            # **报文**逐字对比：次序一分家，同一份规格在两边会得到**不同的拒绝理由**，
            # 而那个分家不抛异常、只在一条比对面报出来。
            return existing

        if spec.window is not None:
            # 这一句**不是**第二份「只有带窗口的自动模式才判累计」判据 ——
            # :func:`window_refusal` 自己对无窗口的规格也返回空串，那条判据只有一份。
            # 这一句在算**参数**：``progress`` 要按这条链的摘要去读，而无窗口的规格
            # 没有摘要（``RunSpec.window`` 是 ``None``），读不出「这条链」是哪一条。
            refusal = window_refusal(
                spec,
                progress=self.chain_progress(
                    workspace=spec.workspace, window_digest=spec.window.digest
                ),
                spent=spent,
            )
            if refusal:
                raise RunSpecError(refusal)

        now = at.isoformat()
        created = Run(
            run_id=rid,
            spec=spec,
            spec_sha256=spec_sha256,
            created_at=now,
            updated_at=now,
            stop_reason=None,
            delta=None,
            stopped_at=None,
        )
        # 绑定与那一行**同时**发生，位置与 SQLite 那份逐字相同（在「重发」那一支之后、
        # 在写之前）—— 重发拿回的那一行落盘那次已经把库绑好了，所以那一支不必再判，
        # 与 ``adapters/sqlite/review.py`` 对 ``resolve`` 那一处的说法同一条理由。
        self.bind_workspace(spec.workspace)
        self._runs[rid] = created
        # 折入账开成空的：有账、里面零行。与那一行**同时**写 —— 位置对着 SQLite 那份
        # 同一条 INSERT 的第二句（两张表同一笔）。重发那一支在上面就 return 了，所以
        # 账永远不会被重开，也就不会把已记下的折入擦掉。
        self._folds[rid] = []
        return created

    def bind_workspace(self, workspace: str) -> None:
        """记下这个替身属于哪个工作区。已经绑了别的就拒绝。

        拒词与 ``adapters/sqlite/db.py::bind_workspace`` **逐字相同** —— 那一份是
        主存那条路径的唯一来源，两个实现各写一遍的话，漂开的那一处不会有人读。
        （中间那句里的「账号」是那份判词里就有的词，而它是``db.bind_workspace``
        **所有**调用点共用的一句：``fetch_attempt`` / ``pool`` 也一样带着它。）
        """
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按账号取到的这一行是谁的」没有答案，"
                "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
                "要用另一个工作区，换一个库（或先把这个库移开）。"
            )

    def get(self, run_id: str) -> Run | None:  # noqa: A002 - 与端口签名逐字一致
        return self._runs.get(run_id)

    # ------------------------------------------------------------------
    # 这一轮折过哪几行
    # ------------------------------------------------------------------
    def folds_of(self, run_id: str) -> tuple[FoldReceipt, ...]:  # noqa: A002
        """见 ``ports/runs.py::RunStore.folds_of``。

        「账本不在」抛而不是返回空元组 —— 那是这一条的全部要点，与 SQLite 那份逐字
        相同（``fold_ledger_refusal`` 是同一份判词）。
        """
        entries = self._folds.get(run_id)
        if entries is None:
            raise RunFoldLedgerError(
                fold_ledger_refusal(run_id, run_exists=run_id in self._runs)
            )
        return tuple(entries)

    def record_fold(
        self,
        fold: FoldAttribution,
        *,
        venue_asset_id: str,
        at: datetime,
    ) -> None:
        """把「这一轮折了池子这一行」记下来 —— SQLite 那份 ``record_fold`` 的同义实现。

        **没有事务**：与这个替身其余的写法一样，它靠「会失败的都问完再改」来拿到同等
        语义 —— 三条判定全在**任何一次写之前**，改的两步（追加回执）不会失败。调用点
        （``InMemoryVenueAssetStore.upsert_asset``）也是照这个次序排的：它先合并、再调
        这里，最后才写字典型。三处都是「先问后写」，所以在任何一条拒词之下，池子与账本
        都停在调用前的样子。

        与 SQLite 那份必须**逐字**给出同一份报文（``fold_ledger_refusal`` /
        ``fold_after_end_refusal`` / ``fold_replay_conflict`` 三条判词都是共用的）；
        判定本身的两份抄写与 ``_already_ended`` 那一对同一种处置 —— 分家的形态是
        契约测试里那一条比对面报出来。
        """
        entries = self._folds.get(fold.run_id)
        if entries is None:
            raise RunFoldLedgerError(
                fold_ledger_refusal(fold.run_id, run_exists=fold.run_id in self._runs)
            )
        current = self._runs[fold.run_id]
        if current.stop_reason is not None:
            raise RunStateError(
                fold_after_end_refusal(
                    fold.run_id, stop_reason=current.stop_reason.value
                )
            )
        for one in entries:
            if one.receipt_id == fold.receipt_id and one.ordinal == fold.ordinal:
                if one.venue_asset_id == venue_asset_id:
                    return  # 重放：同一笔，不写第二遍
                raise StoreError(
                    fold_replay_conflict(
                        fold.run_id,
                        fold.receipt_id,
                        fold.ordinal,
                        recorded=one.venue_asset_id,
                        incoming=venue_asset_id,
                    )
                )
        entries.append(
            FoldReceipt(
                run_id=fold.run_id,
                receipt_id=fold.receipt_id,
                ordinal=fold.ordinal,
                venue_asset_id=venue_asset_id,
                at=at,
            )
        )

    def chain_progress(self, *, workspace: str, window_digest: str) -> ChainProgress:
        """见 ``ports/runs.py::RunStore.chain_progress``。

        **筛选一件都不做**：全部行交给共用的那一份折叠。这个替身是绑工作区的
        （``bind_workspace``），所以「取全部」与「取这个工作区的」在这里本来就是同一
        批 —— 而多写一次筛选就是第二处判据，它的漂移形态见 ``progress_of`` 的
        docstring。这条不作为纪律留给下一个实现：SQLite 那一份**真的多写了一次**
        ``WHERE workspace = ?``（那是为了走索引），所以两处分家的可能是真实存在的，
        由契约测试里那条「两个实现逐字段相同」兜着。
        """
        return progress_of(workspace, window_digest, self._runs.values())

    def stop(
        self,
        run_id: str,  # noqa: A002 - 与端口签名逐字一致
        *,
        delta: RunDelta,
        status: RunStatus,
        at: datetime,
    ) -> Run:
        current = self._runs.get(run_id)
        if current is None:
            raise RunStateError(unknown_run_refusal(run_id))
        if self._already_ended(current, run_id=run_id, status=status, delta=delta):
            return current
        # 「这一套结局**能不能**成立」在「它是不是已经写过的那一套」之后问 —— 两条
        # 路径的顺序在 SQLite 那份里逐字相同（一次已经结束过的重放该得到的是「与先前
        # 那一套不同」，不是「这个结局本身不合法」）。
        refusal = ending_refusal(status, delta)
        if refusal:
            raise RunSpecError(refusal)
        now = at.isoformat()
        stopped = replace(
            current,
            stop_reason=status,
            delta=delta,
            stopped_at=now,
            updated_at=now,
        )
        self._runs[run_id] = stopped
        return stopped

    @staticmethod
    def _already_ended(
        current: Run, *, run_id: str, status: RunStatus, delta: RunDelta
    ) -> bool:
        """``True`` = 这一轮已经以**同一套**结局与产出结束过，这是重放；否则抛。

        与 ``SqliteRunStore._already_ended`` 逐条同义（也与 ``_already_done`` 那一对
        同形）：判据见 ``tests/test_run_ending_contract.py``，其中一条比对两个实现
        给出的报文**逐字相同**。

        ``False`` 在调用点只有一个意思：这一轮**还没结束**。比的是
        :func:`~marketops.ports.runs.stopping_outcome` 那个指纹，不是两个模型 ——
        理由在那边（两个实现各写一次「怎么算同一套结局」的时候，分家的形态是其中
        一个接受了另一个拒绝的重放，而两边都返回成功）。

        ``ending_refusal`` 不在这里判：那是「这一套结局**能不能**成立」，与「它是不是
        已经写过的那一套」是两个问题，而前者在**没结束过**的那条路径上才需要问。
        """
        if not current.is_stopped:
            return False
        if stopping_outcome(current.stop_reason, current.delta) == stopping_outcome(
            status, delta
        ):
            return True
        raise RunStateError(
            f"Run {run_id} 已经结束了（{current.stop_reason.value}，"
            f"停表于 {current.stopped_at}），而这一次带上来的结局是 {status.value}、"
            "产出与先前那一份不同。\n"
            "一个 Run 只有一份结局与一份产出。**不覆盖** —— 覆盖会让后到的那一份悄悄"
            "取代先落盘的那一份，而先落盘那一份的产出**没有任何一处记着它丢过**。\n"
            "如果这是同一轮的两个不同结果，那是两个 Run 而不是一个。"
        )


def _product_conflict(subject: str) -> StoreError:
    """同键不同内容的那条拒词。

    消息里必须点名 ``subject``：报的异常是 ``StoreError``，而**它同时是「这个库打不开」
    的类型**。一条不分主语的冲突回执让两件处置相反的事共用一个名字，而契约判据正是靠
    「点名了没有」把两者分开（``tests/test_product_catalog_contract.py`` 的三条冲突判据
    都断言主语在消息里）。

    这里不列「哪几格不同」，SQLite 那一份列 —— 它手上已经有一对 payload dict，而这份
    替身手上是两个模型，列出来要多 dump 两次。两个实现的消息因此不完全一致；契约不判
    那个，它判的是「点名了主语」。
    """
    return StoreError(
        f"{subject} 已经有一份不同的内容。\n"
        "版号一经写下就不再变：每一处引用都只写 (id, 2)，而『第 2 版』在两次部署"
        "之间变成两份不同的东西时，**没有任何一处调用点**能看出来。不覆盖 —— "
        "要改内容就写下一个版号。"
    )


class InMemoryProductCatalog(ProductCatalog):
    """产品目录的不落盘实现。判据见 ``tests/test_product_catalog_contract.py``。

    ## 幂等边界与 SQLite 那一份逐字同源，别的不是

    「同键不同内容 → ``StoreError``，消息点名主语」在两个实现上必须一样，因为契约判据
    在两个实现上跑同一份。并发不被本类保证 —— 与这个文件里其余每一个替身一样：它服务
    的是「形状对不对」，不是并发。

    ## 为什么不是「写进一个 dict 就完了」

    因为幂等边界要真的判。``self._versions[key] = version`` 这种写法在「同键同内容是
    幂等」那条判据上也绿 —— 它只是把那条判据的**反面**（同键不同内容必须报错）悄悄
    吞了。``tests/test_product_catalog_contract.py`` 里的 ``_ConflictsAreIgnored`` 就是
    这个形状，被当作正对照钉着。
    """

    def __init__(self) -> None:
        self._versions: dict[tuple[str, int], ProductVersion] = {}
        self._offers: dict[tuple[str, int], OfferVersion] = {}
        self._lines: dict[str, ProductLine] = {}

    def put_version(self, version: ProductVersion) -> None:
        key = (version.product_id, version.version)
        existing = self._versions.get(key)
        if existing is None:
            self._versions[key] = version
            return
        if existing != version:
            raise _product_conflict(f"product_id={version.product_id!r} version={version.version}")

    def get_version(self, product_id: str, version: int) -> ProductVersion | None:
        return self._versions.get((product_id, version))

    def versions(self, product_id: str) -> tuple[ProductVersion, ...]:
        return tuple(
            version for (pid, _), version in sorted(self._versions.items()) if pid == product_id
        )

    def put_offer(self, offer: OfferVersion) -> None:
        key = (offer.offer_id, offer.version)
        existing = self._offers.get(key)
        if existing is None:
            self._offers[key] = offer
            return
        if existing != offer:
            raise _product_conflict(f"offer_id={offer.offer_id!r} version={offer.version}")

    def get_offer(self, offer_id: str, version: int) -> OfferVersion | None:
        return self._offers.get((offer_id, version))

    def put_line(self, line: ProductLine) -> None:
        existing = self._lines.get(line.line_id)
        if existing is None:
            self._lines[line.line_id] = line
            return
        if existing != line:
            raise _product_conflict(f"line_id={line.line_id!r}")

    def get_line(self, line_id: str) -> ProductLine | None:
        return self._lines.get(line_id)


class InMemorySavedViewStore(SavedViewStore):
    """不落盘的视图存储：**测试替身**，不是部署选项。

    ## 名字判据在这里最容易做错

    判据是「``(workspace, owner, name)`` 上不得有**另一条当前版**」—— 三格限定词
    在 SQLite 那一份里写成 SQL 的 WHERE，在这里写成三个 ``if``。一个图省事写成
    「按名字查一次字典」的替身会把**历史版本**也算进去，于是改名在测试里做不成 ——
    而测试会红，红的原因看起来却像「改名这条功能没实现」。

    边界与其余内存实现逐字相同：**没有锁**。``_require_name_free`` 那一圈遍历与
    随后的 ``append`` 之间没有事务包着，两个线程中间插进一次写就会双双认为自己
    成功 —— 而 SQLite 那一份的 ``BEGIN IMMEDIATE`` 正是为了不让它发生。所以这一份
    **只用于单进程测试**：任何一次真的部署都得用 SQLite 那一份。
    """

    def __init__(self) -> None:
        self._workspace: str | None = None
        #: ``view_id`` → 它的**全部**版本（只增）。当前版是 ``max(revision)``。
        self._rows: dict[str, list[SavedView]] = {}

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有答案，"
                "而它的表现不是报错，是两批行被静静地混在一起返回。\n"
                "要用另一个工作区，换一个库（或先把这个库移开）。"
            )

    def put_view(self, view: SavedView, *, expected_revision: int | None) -> ViewWrite:
        if view.revision != (expected_revision or 0) + 1:
            raise StoreError(
                f"view_id={view.view_id!r} 的写入版本必须是 "
                f"{(expected_revision or 0) + 1}（基准 {expected_revision} 加一），"
                f"本次是 {view.revision}。"
            )
        self.bind_workspace(view.workspace)
        current = self._current(view.view_id)
        self._require_base(view, current, expected_revision)
        if current is not None and current.content_digest == view.content_digest:
            return ViewWrite(view=current, revision_written=None)
        self._require_name_free(view)
        self._rows.setdefault(view.view_id, []).append(view)
        return ViewWrite(view=view, revision_written=view.revision)

    def current(self, view_id: str) -> SavedView | None:
        return self._current(view_id)

    def views(self, *, workspace: str, owner: str | None = None) -> tuple[SavedView, ...]:
        return tuple(
            sorted(
                (
                    view
                    for view_id in self._rows
                    if (view := self._current(view_id)) is not None
                    and view.workspace == workspace
                    and (owner is None or view.owner == owner)
                ),
                key=lambda view: (view.owner, view.name, view.view_id),
            )
        )

    # ------------------------------------------------------------ 内部
    def _current(self, view_id: str) -> SavedView | None:
        rows = self._rows.get(view_id)
        if not rows:
            return None
        return self._own(max(rows, key=lambda view: view.revision))

    def _require_base(
        self, view: SavedView, current: SavedView | None, expected_revision: int | None
    ) -> None:
        if expected_revision is None:
            if current is not None:
                raise SavedViewConflictError(
                    f"view_id={view.view_id!r} 已经在了（当前第 {current.revision} 版，"
                    f"名字 {current.name!r}），本次调用声称它是首次创建。\n"
                    "要改它，就把 expected_revision 写成你读到的那一版；"
                    "要新建一条，就换一个 view_id。"
                )
            return
        if current is None:
            raise SavedViewConflictError(
                f"view_id={view.view_id!r} 在盘上没有任何一版，而本次调用说它读到的是"
                f"第 {expected_revision} 版。"
            )
        if current.revision != expected_revision:
            raise SavedViewConflictError(
                f"view_id={view.view_id!r} 手上的基准是第 {expected_revision} 版，"
                f"而盘上当前是第 {current.revision} 版（名字 {current.name!r}）—— "
                "有人先写了。请重读它的当前版再决定。"
            )

    def _require_name_free(self, view: SavedView) -> None:
        for view_id, rows in self._rows.items():
            if view_id == view.view_id:
                continue
            rival = max(rows, key=lambda row: row.revision)
            if (
                rival.workspace == view.workspace
                and rival.owner == view.owner
                and rival.name == view.name
            ):
                raise SavedViewConflictError(
                    f"({view.workspace!r}, {view.owner!r}, {view.name!r}) 这个名字下已经"
                    f"有一条当前版：view_id={rival.view_id!r} 第 {rival.revision} 版。\n"
                    "要改**那一条**，就用它的 view_id 写下一版；要新建一条，就换一个名字。"
                )

    def _own(self, view: SavedView, *, where: str = "") -> SavedView:
        if view.workspace == self._workspace:
            return view
        raise StoreError(
            f"{where or f'saved_view[{view.view_id}]'} 写着 workspace="
            f"{view.workspace!r}，而这个存储绑在 {self._workspace!r} 上。"
        )


class InMemoryWatchStore(WatchStore):
    """不落盘的监控存储：**测试替身**，不是部署选项。

    ``due_watches`` 在这一份里是**读出来再筛**，在 SQLite 那一份里是那条
    ``watch_by_due`` 索引。两边的**答案**必须逐字相同（契约测试对两份一起跑），
    而差别的落点只有一处：SQLite 那一份用扁平列 ``enabled`` / ``next_due_at``，
    这一份用对象上的同名字段。

    **没有锁**（与其余内存实现逐字相同），所以只用于单进程测试。
    """

    def __init__(self) -> None:
        self._workspace: str | None = None
        self._rows: dict[str, list[Watch]] = {}

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。"
            )

    def put_watch(self, watch: Watch, *, expected_revision: int | None) -> WatchWrite:
        if watch.revision != (expected_revision or 0) + 1:
            raise StoreError(
                f"watch_id={watch.watch_id!r} 的写入版本必须是 "
                f"{(expected_revision or 0) + 1}（基准 {expected_revision} 加一），"
                f"本次是 {watch.revision}。"
            )
        self.bind_workspace(watch.workspace)
        current = self._current(watch.watch_id)
        if expected_revision is None:
            if current is not None:
                raise StoreError(
                    f"watch_id={watch.watch_id!r} 已经在了（当前第 {current.revision} 版），"
                    "本次调用声称它是首次创建。"
                )
        elif current is None:
            raise StoreError(
                f"watch_id={watch.watch_id!r} 在盘上没有任何一版，而本次调用说它读到的是"
                f"第 {expected_revision} 版。"
            )
        elif current.revision != expected_revision:
            raise StoreError(
                f"watch_id={watch.watch_id!r} 手上的基准是第 {expected_revision} 版，"
                f"而盘上当前是第 {current.revision} 版 —— 有人先写了。请重读再决定。"
            )
        elif current.content_digest == watch.content_digest:
            return WatchWrite(watch=current, revision_written=None)
        self._rows.setdefault(watch.watch_id, []).append(watch)
        return WatchWrite(watch=watch, revision_written=watch.revision)

    def get_watch(self, watch_id: str) -> Watch | None:
        return self._current(watch_id)

    def watches(self, *, workspace: str, enabled: bool | None = None) -> tuple[Watch, ...]:
        return tuple(
            sorted(
                (
                    watch
                    for watch_id in self._rows
                    if (watch := self._current(watch_id)) is not None
                    and watch.workspace == workspace
                    and (enabled is None or watch.enabled == enabled)
                ),
                key=lambda watch: watch.watch_id,
            )
        )

    def due_watches(self, *, workspace: str, at: datetime) -> tuple[Watch, ...]:
        moment = to_utc(at)
        return tuple(
            sorted(
                (
                    watch
                    for watch in self.watches(workspace=workspace, enabled=True)
                    if watch.next_due_at is not None and to_utc(watch.next_due_at) <= moment
                ),
                key=lambda watch: (watch.next_due_at, watch.watch_id),
            )
        )

    def _current(self, watch_id: str) -> Watch | None:
        rows = self._rows.get(watch_id)
        if not rows:
            return None
        return self._own(max(rows, key=lambda row: row.revision))

    def _own(self, watch: Watch, *, where: str = "") -> Watch:
        if watch.workspace == self._workspace:
            return watch
        raise StoreError(
            f"{where or f'watch[{watch.watch_id}]'} 写着 workspace={watch.workspace!r}，"
            f"而这个存储绑在 {self._workspace!r} 上。"
        )


class InMemoryActionItemStore(ActionItemStore):
    """不落盘的跟进池：**测试替身**，不是部署选项。

    ## 这一份里没有 partial unique index，于是那条判据要在这里**另写一遍**

    这是三份替身里唯一一处**判据换了一个执行者**的地方：SQLite 那一份靠
    ``action_item_open_by_subject`` 这条 DDL 索引，这里靠一次显式的查找。两者的
    语义必须逐字相同（契约测试对两份一起跑），而**这条替换本身是有代价的**：
    索引挡得住另一条路径、另一个进程、一次手写的 SQL，这里的 ``if`` 挡不住。
    所以「重复创建是插入失败」这条要求在**真的部署**上由索引执行，而这一份只
    复现它的**答案**。

    **没有锁**（与其余内存实现逐字相同），所以只用于单进程测试 —— 而这一份比
    另外两份更吃亏：``_open_rival`` 与随后的那次赋值之间那个窗口，在 SQLite 那一侧
    是被索引关掉的。
    """

    def __init__(self) -> None:
        self._workspace: str | None = None
        #: ``action_id`` → **一行**（就地更新，不是只增版本）。见 ``domain/action_item.py``。
        self._rows: dict[str, ActionItem] = {}

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。"
            )

    def create(self, item: ActionItem) -> ActionItemWrite:
        require_creatable(item)
        self.bind_workspace(item.workspace)
        existing = self._rows.get(item.action_id)
        if existing is not None:
            if _memory_same_opening(existing, item):
                return ActionItemWrite(item=existing, revision_written=None)
            raise StoreError(
                f"action_id={item.action_id!r} 已经有一版，而它与本次内容不同"
                f"（盘上第 {existing.revision} 版，类型 {existing.action_type.value}，"
                f"主语 {existing.scope_ref!r}）。"
            )
        rival = self._open_rival(item)
        if rival is not None:
            raise OpenActionItemExistsError(
                f"({item.workspace!r}, {item.action_type.value!r}, {item.scope_ref!r}) 上"
                "已经有一条**未结**的待办了，而这是同一主体上的同一类工作的第二条。\n"
                f"盘上那一条是 action_id={rival.action_id!r}（状态 {rival.state.value}）。\n"
                "同一主体上的同一类未结待办只应有一条 —— 没有这条约束，每个 Run 都会新增"
                "一条「核实转介联系人」，而报告上看起来只是「待办多了一些」。\n"
                "**已结的可以有任意多条**：它做完之后，同一主体上的下一件就建得出来了。"
            )
        self._rows[item.action_id] = item
        return ActionItemWrite(item=item, revision_written=item.revision)

    def resolve(self, item: ActionItem, *, expected_version: int) -> ActionItemWrite:
        current = self._rows.get(item.action_id)
        if current is None:
            raise ActionItemConflictError(
                f"action_id={item.action_id!r} 在盘上不存在。"
            )
        current = self._own(current)
        if current.workspace != item.workspace:
            raise StoreError(
                f"action_id={item.action_id!r} 属于工作区 {current.workspace!r}，"
                f"本次写入声称的是 {item.workspace!r}。"
            )
        if current.revision != expected_version:
            if (
                current.expected_version == expected_version
                and current.disposition_digest == item.disposition_digest
            ):
                return ActionItemWrite(item=current, revision_written=None)
            raise ActionItemConflictError(
                f"action_id={item.action_id!r} 手上的版本是 {expected_version}，"
                f"而盘上当前是第 {current.revision} 版（状态 {current.state.value}）"
                "—— 有人先处置过它了。请重读这一件再决定。"
            )
        if item.revision != expected_version + 1:
            raise StoreError(
                f"处置 action_id={item.action_id!r} 时新版本必须是 "
                f"{expected_version + 1}，本次是 {item.revision}。"
            )
        self._rows[item.action_id] = item
        return ActionItemWrite(item=item, revision_written=item.revision)

    def get(self, action_id: str) -> ActionItem | None:
        found = self._rows.get(action_id)
        if found is None:
            return None
        return self._own(found)

    def items(
        self,
        *,
        workspace: str,
        state: ActionItemState | None = None,
        owner: str | None = None,
        open_only: bool = False,
    ) -> tuple[ActionItem, ...]:
        """同序与 SQLite 那一份逐字相同：``(未结在前, due_at 空值往后, due_at,
        action_id)``。第一格用 ``OPEN_STATES`` 成员而不是三个字面量 —— 与 SQL 那一侧
        那个 ``IN (?, ?, ?)`` 用的是同一个来源。

        ``owner`` 与 SQLite 那一份逐字同义：``None`` = **不过滤**（不是「没有主人的」，
        那一问今天没有入口 —— 与 ``views(owner=...)`` 同一条）。
        """
        rows = [item for item in self._rows.values() if item.workspace == workspace]
        if owner is not None:
            rows = [item for item in rows if item.owner == owner]
        if state is not None:
            rows = [item for item in rows if item.state is state]
        elif open_only:
            rows = [item for item in rows if item.state in OPEN_STATES]
        return tuple(
            sorted(
                rows,
                key=lambda item: (
                    0 if item.state in OPEN_STATES else 1,
                    1 if item.due_at is None else 0,
                    item.due_at or item.created_at,
                    item.action_id,
                ),
            )
        )

    def _open_rival(self, item: ActionItem) -> ActionItem | None:
        for other in self._rows.values():
            if (
                other.workspace == item.workspace
                and other.action_type == item.action_type
                and other.scope_ref == item.scope_ref
                and other.state in OPEN_STATES
            ):
                return other
        return None

    def _own(self, item: ActionItem, *, where: str = "") -> ActionItem:
        if item.workspace == self._workspace:
            return item
        raise StoreError(
            f"{where or f'action_item[{item.action_id}]'} 写着 workspace="
            f"{item.workspace!r}，而这个存储绑在 {self._workspace!r} 上。"
        )


def _memory_same_opening(left: ActionItem, right: ActionItem) -> bool:
    """``adapters/sqlite/views.py::_same_opening`` 的内存版。**两处各写一遍**是因为
    两份实现不互相 import（一个适配器 import 另一个适配器是一条不该有的边）；
    判据本身在契约测试上对两份一起跑。"""
    def shape(item: ActionItem) -> dict:
        payload = item.model_dump(mode="json")
        payload.pop("created_at", None)
        payload.pop("updated_at", None)
        return payload

    return shape(left) == shape(right)


class InMemoryWorkstreamStore(WorkstreamStore):
    """``workstream`` 与 ``launch_record`` 两张表的不落盘替身。

    语义与 ``adapters/sqlite/workstreams.py`` 逐条一致，判据在
    ``tests/test_workstream_store_contract.py`` 上**对两份一起跑**（契约测试那条
    「两个实现逐字段相同」正是为这件事要的）。

    **幂等在这里也是字典的键，不是一条纪律。** 业务键
    ``(workspace, scene_id, scene_version, idempotency_key)`` 就是 ``_launches``
    的键 —— 与 SQLite 那份那条 UNIQUE 是同一条判据的两种载体，而不是两处各判一遍。
    **但并发不在这里**：这一个没有事务、没有锁（理由与 ``InMemoryRunStore`` 逐字
    相同），两个写者同时交同一个键时只有 SQLite 那份守得住。
    """

    def __init__(self) -> None:
        #: 四格业务键 → 那一条记录。键的形状与 DDL 上那条 UNIQUE 逐字段一致。
        self._launches: dict[tuple[str, str, int, str], LaunchRecord] = {}
        #: ``workstream_id`` → 那一行。与 SQLite 那份的主键一致。
        self._streams: dict[str, Workstream] = {}
        #: 这个替身属于哪个工作区。**与 SQLite 那份同一条判据**：两张表（``workstream``
        #: / ``launch_record``）都在 ``db.py::OWNED_TABLES`` 里，而那一份清单的
        #: 用途是「一个装了两个部署的行的库要能被当场拒绝」（``db.py`` 上那条注释
        #: 逐字）。少了这一格，契约里「另一个工作区那一行读不出来」只能靠**把那一行
        #: 真写进去**来判 —— 而那一步在 SQLite 那份上已经被绑定拒了，于是同一组判据
        #: 在两个实现上测的是两件事。
        self._workspace: str | None = None

    def bind_workspace(self, workspace: str) -> None:
        """记下这个替身属于哪个工作区。已经绑了别的就拒绝。

        拒词与 ``adapters/sqlite/db.py::bind_workspace`` 逐字相同 —— 与
        ``InMemoryRunStore.bind_workspace`` / ``InMemoryScheduleStore.bind_workspace``
        同一条理由：主存那份是唯一来源，两个实现各写一遍的话，漂开的那一处不会有人读。
        """
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有"
                "答案，而它的表现不是报错，是两批行被静静地混在一起返回。"
            )

    # ------------------------------------------------------------------
    # LaunchRecord
    # ------------------------------------------------------------------
    def put_launch(self, record: LaunchRecord) -> LaunchRecord:
        # 绑定在**最前面**，位置与 ``adapters/sqlite/workstreams.py::put_launch`` 里
        # 那一句逐字相同（在那条 INSERT 之前，重发那一支也在它之后）。
        self.bind_workspace(record.workspace)
        key = (
            record.workspace,
            record.scene_id,
            record.scene_version,
            record.idempotency_key,
        )
        existing = self._launches.get(key)
        if existing is None:
            self._launches[key] = record
            # 交出去的**是盘上那一行**（这里就是刚放进去的那一个），不是入参对象 ——
            # 端口那一份的 docstring 逐字要求这一条，而它在两条路上必须是同一个形状。
            return self._launches[key]
        if existing.request_digest == record.request_digest:
            return existing
        raise StoreError(
            f"启动 {existing.launch_id!r} 已经用这个键记过了，而这一次交上来的"
            f"内容不是同一份：盘上是 {existing.request_digest}，本次是 "
            f"{record.request_digest}。\n"
            "  「同一个请求回同一个 launch」说的是**同一个请求**：键相同而内容"
            "不同是一次冲突，静默拿回第一次那一行会让调用方以为自己开的那一轮"
            "跑的是他要的东西。要改内容就换一个键。"
        )

    def get_launch(
        self,
        *,
        workspace: str,
        scene_id: str,
        scene_version: int,
        idempotency_key: str,
    ) -> LaunchRecord | None:
        return self._launches.get(
            (workspace, scene_id, scene_version, idempotency_key)
        )

    # ------------------------------------------------------------------
    # Workstream
    # ------------------------------------------------------------------
    def next_ordinal(self, scene_id: str) -> int:
        top = max(
            (
                stream.ordinal
                for stream in self._streams.values()
                if stream.scene_id == scene_id
            ),
            default=0,
        )
        return top + 1

    def put_workstream(
        self, stream: Workstream, *, expected_revision: int | None
    ) -> None:
        if expected_revision is None:
            if stream.revision != 1:
                raise StoreError(
                    f"首次创建 workstream={stream.workstream_id!r} 时 revision 必须是 1，"
                    f"本次是 {stream.revision}。\n"
                    "带着别的 revision 创建，会让「这个 id 已经存在」与「我写的是"
                    "第 n 版」变成同一件事 —— 而库里那一条的 revision 与它无关。"
                )
            # 绑定只在**创建**这一支：与 ``adapters/sqlite/workstreams.py::_insert``
            # 逐字同一个位置（``_update`` 那一支刻意不绑 —— 那一行已经在盘上了，
            # 「它属不属于这个库」早在它落盘那一次就判过了，见 ``db.py::bind_workspace``
            # 那一节）。
            self.bind_workspace(stream.workspace)
            if stream.workstream_id in self._streams:
                existing = self._streams[stream.workstream_id]
                raise StoreError(
                    f"workstream={stream.workstream_id!r} 已经存在（工作区 "
                    f"{existing.workspace!r}，revision={existing.revision}），"
                    "而本次调用声称它是首次创建。\n"
                    "业务键是 ``<scene_id>#<ordinal>``，而它唯一的来源是"
                    "``next_ordinal`` —— 两个写者同时读到同一个序号时，**只有一个人**"
                    "该拿到这个 id，而这件事只有存储答得了。"
                )
            self._streams[stream.workstream_id] = stream
            return

        if stream.revision != expected_revision + 1:
            raise StoreError(
                f"workstream={stream.workstream_id!r} 的写入 revision 必须是 "
                f"{expected_revision + 1}（读到的 {expected_revision} 加一），本次是 "
                f"{stream.revision}。\n"
                "这一条挡的是「改完对象直接写回」：那跳过了一次自增，于是并发的另一个"
                "写者会**成功** —— 而它以为自己在比的那个版本从来没存在过。"
            )
        current = self._streams.get(stream.workstream_id)
        if current is None:
            raise StoreError(
                f"workstream={stream.workstream_id!r} 不存在，而本次调用以为它在第 "
                f"{expected_revision} 版。一个字都没写。"
            )
        if current.revision != expected_revision:
            raise StoreError(
                f"workstream={stream.workstream_id!r} 现在是第 {current.revision} 版，"
                f"而本次调用读到的是第 {expected_revision} 版。一个字都没写。\n"
                "  这是 CAS 的拒绝：过期的那一次不覆盖新的一次，因为覆盖之后"
                "「有人先改了」这件事在盘上不留痕。处置是重新读一遍再决定。"
            )
        self._streams[stream.workstream_id] = stream

    def get_workstream(self, workstream_id: str) -> Workstream | None:
        return self._streams.get(workstream_id)

    def workstreams(self, *, workspace: str) -> tuple[Workstream, ...]:
        return tuple(
            stream
            for _, stream in sorted(self._streams.items())
            if stream.workspace == workspace
        )


class InMemoryEffectiveSceneStore(EffectiveSceneStore):
    """``effective_scene`` 一张表的不落盘替身。**没有 update 路径。**

    与 SQLite 那份同一条承重点：``(scene_id, version)`` 第二次写、内容不同 ⇒
    ``StoreError``；内容逐字相同 ⇒ 幂等。变异测试打的就是这一条 ——
    「已存在就覆盖」的半成品在这里会红（``tests/test_workstream_store_contract.py``
    那条 ``test_an_effective_scene_version_cannot_be_rewritten``）。
    """

    def __init__(self) -> None:
        self._scenes: dict[tuple[str, int], EffectiveScene] = {}
        #: 见 ``InMemoryWorkstreamStore`` 那一格：``effective_scene`` 也在
        #: ``db.py::OWNED_TABLES`` 里，所以「一个库只装一个工作区」这条判据在
        #: 两个实现上必须是同一句话。
        self._workspace: str | None = None

    def bind_workspace(self, workspace: str) -> None:
        """记下这个替身属于哪个工作区。已经绑了别的就拒绝（拒词与主存逐字相同）。"""
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有"
                "答案，而它的表现不是报错，是两批行被静静地混在一起返回。"
            )

    def put_scene(self, scene: EffectiveScene) -> None:
        # 与 ``adapters/sqlite/workstreams.py::put_scene`` 的 ``_bind`` 同一个位置。
        self.bind_workspace(scene.workspace)
        key = (scene.scene_id, scene.version)
        existing = self._scenes.get(key)
        if existing is None:
            self._scenes[key] = scene
            return
        # 判据是**内容摘要**，不是整份负载逐字节相等 —— 与 SQLite 那份同一格。
        # ``effective_scene_digest`` 不含 ``at`` / ``compiler``（那是**这一次动作**
        # 的两格，不是**这一份内容**的两格），所以同一次编译被重发时时钟往前走一格
        # 不会把它读成一次冲突。两处判据不同的话，「不可变」在其中一个实现上会变成
        # 「不可重发」，而契约测试正是为这种分家写的。
        if existing.digest != scene.digest:
            raise StoreError(
                f"effective_scene {scene.scene_id!r} 第 {scene.version} 版已经写过了，"
                f"而这一次的内容不是同一份：盘上是 {existing.digest}，本次是 "
                f"{scene.digest}。\n"
                "  这一版是**不可变**的（B02-b）：同一 ``(scene, version)`` 在两次"
                "部署之间成了两份不同的东西时，引用它的每一处都看不出来 —— 而"
                "「这一版当时编译成什么」这个问题的答案会随读它的时刻变。\n"
                "  处置是发一版新的 Scene 再编译，不是改写这一版。"
            )

    def get_scene(self, scene_id: str, version: int) -> EffectiveScene | None:
        scene = self._scenes.get((scene_id, version))
        if scene is None:
            return None
        # 与 SQLite 那份的 ``_check_scene_row`` 同一条：读回来时重算一遍摘要。
        # 一个只在写的时候算摘要的实现，在这一行被就地改过之后读不出任何异样。
        if effective_scene_digest(scene) != scene.digest:
            raise StoreError(
                f"effective_scene {scene_id!r}@{version} 的摘要对不上：负载里是 "
                f"{scene.digest}，按内容重算是 {effective_scene_digest(scene)}。\n"
                "  这一行被就地改过。摘要在这里的作用正是回答「这一版还是编译时的"
                "那一份吗」，而它对不上意味着那个问题的答案是「不是」。"
            )
        return scene

    def versions(self, scene_id: str) -> tuple[int, ...]:
        return tuple(
            sorted(version for (ident, version) in self._scenes if ident == scene_id)
        )

    def scenes(self, *, workspace: str) -> tuple[EffectiveScene, ...]:
        found = (
            scene
            for scene in self._scenes.values()
            if scene.workspace == workspace
        )
        return tuple(sorted(found, key=lambda scene: (scene.scene_id, scene.version)))


class InMemoryScheduleStore(ScheduleStore):
    """不落盘的调度存储：**测试替身**，不是部署选项。

    ## 它必须让「幂等来自插入失败」这句话在这里也成立

    ``claim_fire`` 在这一份里是 ``if key in self._fires`` —— 一次字典查询，而它在
    **单线程下与「先查再决定」逐字同形**。这不是一处可以「反正测试是单线程」的
    简化：契约测试要能看见那个差别，而看见它的方式不是并发，是**顺序**：
    一个先 ``get_fire`` 再决定跑不跑的实现，在 ``perform`` 里读盘时会读到
    ``None``（占位还没写），而这里读得到那一行占位。判据因此落在
    「动作跑的时候盘上有没有那一行」上，而不是落在两条线程的竞态上 ——
    竞态测不出来（它不稳），而这件事必须被测出来。

    边界与其余内存实现逐字相同：**没有锁**。``claim_fire`` 与真正的写入之间没有
    事务包着，所以跨线程的判据只对 SQLite 那一份成立（``BEGIN IMMEDIATE``）。
    """

    def __init__(self) -> None:
        self._workspace: str | None = None
        #: ``schedule_id`` → 它的**全部**版本（只增）。当前版是 ``max(revision)``。
        self._rows: dict[str, list[ScheduleDefinition]] = {}
        #: 主键 ``(schedule_id, schedule_revision, fire_id)`` → 那一行。**它一版只写两次**
        #: （一次占位、一次结论），而键不变。
        self._fires: dict[tuple[str, int, str], ScheduleFire] = {}

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。\n"
                "一个库只装一个工作区的产出：混装之后「按 id 取到的这一行是谁的」没有"
                "答案，而它的表现不是报错，是两批行被静静地混在一起返回。"
            )

    # ------------------------------------------------------------ 声明
    def put_definition(
        self, definition: ScheduleDefinition, *, expected_revision: int | None
    ) -> ScheduleWrite:
        if definition.schedule_revision != (expected_revision or 0) + 1:
            raise StoreError(
                f"schedule_id={definition.schedule_id!r} 的写入版本必须是 "
                f"{(expected_revision or 0) + 1}（基准 {expected_revision} 加一），"
                f"本次是 {definition.schedule_revision}。"
            )
        self.bind_workspace(definition.workspace)
        current = self._current(definition.schedule_id)
        self._require_base(definition, current, expected_revision)
        if current is not None and current.content_digest == definition.content_digest:
            return ScheduleWrite(definition=current, revision_written=None)
        self._rows.setdefault(definition.schedule_id, []).append(definition)
        return ScheduleWrite(definition=definition, revision_written=definition.schedule_revision)

    def get_definition(
        self, schedule_id: str, schedule_revision: int
    ) -> ScheduleDefinition | None:
        found = next(
            (
                row
                for row in self._rows.get(schedule_id, [])
                if row.schedule_revision == schedule_revision
            ),
            None,
        )
        return None if found is None else self._own(found)

    def definitions(self, *, workspace: str | None = None) -> tuple[ScheduleDefinition, ...]:
        current = (self._current(ident) for ident in self._rows)
        return tuple(
            sorted(
                (
                    row
                    for row in current
                    if row is not None and (workspace is None or row.workspace == workspace)
                ),
                key=lambda row: row.schedule_id,
            )
        )

    def due(
        self, *, at: datetime, workspace: str | None = None
    ) -> tuple[ScheduleDefinition, ...]:
        """这一刻真的该点火的那些。判据**不在这里**，在 :func:`due_schedules`。

        这里只做一次调用转发，而这件事本身是刻意的：一份把 ``d.due(at)`` 在这里
        重写一遍的实现，会在「清单说它该醒了、驱动说它不到期」那天给出一个
        与 SQLite 那份不同的答案，而两份实现都各自自洽。
        """
        return due_schedules(self.definitions(workspace=workspace), at=at)

    # ------------------------------------------------------------ fire
    def claim_fire(self, fire: ScheduleFire) -> FireWrite:
        self.bind_workspace(fire.workspace)
        key = (fire.schedule_id, fire.schedule_revision, fire.fire_id)
        stored = self._fires.get(key)
        if stored is None:
            self._fires[key] = fire
            return FireWrite(fire=fire, recorded=True)
        if stored.workspace != fire.workspace or stored.fired_at != fire.fired_at:
            raise IdempotencyConflictError(
                f"fire_id={fire.fire_id!r}（schedule_id={fire.schedule_id!r} 第 "
                f"{fire.schedule_revision} 版）已经有一次 fire，而它属于另一次："
                f"盘上是 workspace={stored.workspace!r} / "
                f"fired_at={stored.fired_at.isoformat()}，本次是 "
                f"workspace={fire.workspace!r} / fired_at={fire.fired_at.isoformat()}。"
            )
        return FireWrite(fire=self._own_fire(stored), recorded=False)

    def settle_fire(self, fire: ScheduleFire) -> FireWrite:
        if fire.pending:
            raise ValueError(
                f"{fire.schedule_id!r}@{fire.fire_id!r} 交上来的 ``outcome`` 是 "
                "``pending``：占位不是一个结论。"
            )
        key = (fire.schedule_id, fire.schedule_revision, fire.fire_id)
        stored = self._fires.get(key)
        if stored is None:
            raise StoreError(
                f"fire_id={fire.fire_id!r}（schedule_id={fire.schedule_id!r} 第 "
                f"{fire.schedule_revision} 版）不在盘上，而本次调用要写上它的结论。\n"
                "结论只能写在一个**已经被抢下**的占位上（先 claim_fire 再 settle_fire）。"
            )
        if not stored.pending:
            return FireWrite(fire=self._own_fire(stored), recorded=False)
        self._fires[key] = fire
        return FireWrite(fire=fire, recorded=True)

    def get_fire(
        self, schedule_id: str, schedule_revision: int, fire_id: str
    ) -> ScheduleFire | None:
        stored = self._fires.get((schedule_id, schedule_revision, fire_id))
        return None if stored is None else self._own_fire(stored)

    def fires(
        self, *, workspace: str, schedule_id: str | None = None
    ) -> tuple[ScheduleFire, ...]:
        return tuple(
            sorted(
                (
                    self._own_fire(fire)
                    for fire in self._fires.values()
                    if fire.workspace == workspace
                    and (schedule_id is None or fire.schedule_id == schedule_id)
                ),
                key=lambda fire: (fire.fired_at, fire.schedule_id, fire.fire_id),
            )
        )

    # ------------------------------------------------------------ 内部
    def _current(self, schedule_id: str) -> ScheduleDefinition | None:
        rows = self._rows.get(schedule_id)
        if not rows:
            return None
        return self._own(max(rows, key=lambda row: row.schedule_revision))

    def _require_base(
        self,
        definition: ScheduleDefinition,
        current: ScheduleDefinition | None,
        expected_revision: int | None,
    ) -> None:
        if expected_revision is None:
            if current is not None:
                raise ScheduleConflictError(
                    f"schedule_id={definition.schedule_id!r} 已经在了（当前第 "
                    f"{current.schedule_revision} 版），本次调用声称它是首次声明。"
                )
            return
        if current is None:
            raise ScheduleConflictError(
                f"schedule_id={definition.schedule_id!r} 在盘上没有任何一版，而本次"
                f"调用说它读到的是第 {expected_revision} 版。"
            )
        if current.schedule_revision != expected_revision:
            raise ScheduleConflictError(
                f"schedule_id={definition.schedule_id!r} 手上的基准是第 "
                f"{expected_revision} 版，而盘上当前是第 {current.schedule_revision} 版"
                "—— 有人先写了。请重读它的当前版再决定写哪一版。"
            )

    def _own(self, row: ScheduleDefinition) -> ScheduleDefinition:
        if row.workspace == self._workspace:
            return row
        raise StoreError(
            f"schedule_definition[{row.schedule_id}@{row.schedule_revision}] 写着 "
            f"workspace={row.workspace!r}，而这个存储绑在 {self._workspace!r} 上。"
        )

    def _own_fire(self, fire: ScheduleFire) -> ScheduleFire:
        if fire.workspace == self._workspace:
            return fire
        raise StoreError(
            f"schedule_fire[{fire.schedule_id}#{fire.fire_id}] 写着 "
            f"workspace={fire.workspace!r}，而这个存储绑在 {self._workspace!r} 上。"
        )


class InMemoryNotificationStore(NotificationStore):
    """不落盘的通知存储：**测试替身**，不是部署选项。

    三张表在这里是三个容器，而**它们的判据一个字都没简化**：事件的去重键、
    订阅的版本 CAS、收据的四格主键。「替身把判据放宽」的代价不是测试变松，
    是**判据本身只活在一个实现里** —— 于是「重试产生新行」这句话在契约测试里
    测不出来，而它在 SQLite 上是真的。
    """

    def __init__(self) -> None:
        self._workspace: str | None = None
        self._events: dict[str, NotificationEvent] = {}
        #: ``(workspace, dedupe_key)`` → ``event_id``。**去重键那一格在这里是主键的一半**，
        #: 与 SQLite 上那条 UNIQUE 逐字对应。
        self._by_dedupe: dict[tuple[str, str], str] = {}
        self._subscriptions: dict[str, list[NotificationSubscription]] = {}
        #: 主键 ``(event_id, subscription_id, sink, attempt)`` → 那一行。**只追加。**
        self._receipts: dict[tuple[str, str, str, int], DeliveryReceipt] = {}

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。"
            )

    # ------------------------------------------------------------ 事件
    def put_event(self, event: NotificationEvent) -> EventWrite:
        self.bind_workspace(event.workspace)
        known = self._by_dedupe.get((event.workspace, event.dedupe_key))
        if known is not None:
            stored = self._events[known]
            if stored.model_dump(mode="json") != event.model_dump(mode="json"):
                raise StoreError(
                    f"dedupe_key={event.dedupe_key!r} 上已经有一条事件"
                    f"（event_id={stored.event_id!r}），而它与本次的内容不同。\n"
                    "同一个去重键配两份内容意味着两个不同的业务变化共用了同一个键 —— "
                    "静默留下第一条会让第二条消失。"
                )
            return EventWrite(event=stored, recorded=False)
        if event.event_id in self._events:
            raise StoreError(
                f"event_id={event.event_id!r} 已经被另一条事件用了，而去重键 "
                f"{event.dedupe_key!r} 上没有行 —— event_id 必须是从 "
                "(workspace, dedupe_key) 推出来的。"
            )
        self._events[event.event_id] = event
        self._by_dedupe[(event.workspace, event.dedupe_key)] = event.event_id
        return EventWrite(event=event, recorded=True)

    def get_event(self, event_id: str) -> NotificationEvent | None:
        stored = self._events.get(event_id)
        return None if stored is None else self._own_event(stored)

    def events(
        self,
        *,
        workspace: str,
        kind: str | None = None,
        severity: NotificationSeverity | None = None,
        limit: int | None = None,
    ) -> tuple[NotificationEvent, ...]:
        found = sorted(
            (
                self._own_event(event)
                for event in self._events.values()
                if event.workspace == workspace
                and (kind is None or event.kind == kind)
                and (severity is None or event.severity is severity)
            ),
            key=lambda event: (event.occurred_at, event.event_id),
        )
        return tuple(found if limit is None else found[:limit])

    # ------------------------------------------------------------ 订阅
    def put_subscription(
        self, subscription: NotificationSubscription, *, expected_revision: int | None
    ) -> SubscriptionWrite:
        if subscription.revision != (expected_revision or 0) + 1:
            raise StoreError(
                f"subscription_id={subscription.subscription_id!r} 的写入版本必须是 "
                f"{(expected_revision or 0) + 1}（基准 {expected_revision} 加一），"
                f"本次是 {subscription.revision}。"
            )
        self.bind_workspace(subscription.workspace)
        current = self._current_subscription(subscription.subscription_id)
        self._require_base(subscription, current, expected_revision)
        if current is not None and current.content_digest == subscription.content_digest:
            return SubscriptionWrite(subscription=current, revision_written=None)
        self._subscriptions.setdefault(subscription.subscription_id, []).append(subscription)
        return SubscriptionWrite(
            subscription=subscription, revision_written=subscription.revision
        )

    def get_subscription(
        self, subscription_id: str, revision: int
    ) -> NotificationSubscription | None:
        found = next(
            (
                row
                for row in self._subscriptions.get(subscription_id, [])
                if row.revision == revision
            ),
            None,
        )
        return None if found is None else self._own_subscription(found)

    def subscriptions(
        self, *, workspace: str, principal: str | None = None
    ) -> tuple[NotificationSubscription, ...]:
        current = (self._current_subscription(ident) for ident in self._subscriptions)
        return tuple(
            sorted(
                (
                    row
                    for row in current
                    if row is not None
                    and row.workspace == workspace
                    and (principal is None or row.principal == principal)
                ),
                key=lambda row: row.subscription_id,
            )
        )

    # ------------------------------------------------------------ 收据
    def record_receipt(self, receipt: DeliveryReceipt) -> ReceiptWrite:
        self.bind_workspace(receipt.workspace)
        key = (receipt.event_id, receipt.subscription_id, receipt.sink, receipt.attempt)
        stored = self._receipts.get(key)
        if stored is not None:
            if stored.model_dump(mode="json") != receipt.model_dump(mode="json"):
                raise StoreError(
                    f"delivery_receipt[{receipt.event_id}/{receipt.subscription_id}/"
                    f"{receipt.sink}#{receipt.attempt}] 上已经有另一个结果了。\n"
                    "同一个 (事件, 订阅, sink, 第几次) 配两份结果意味着覆盖 —— "
                    "而覆盖会让「投了三次都没成」在盘上变成「投了一次」。"
                )
            return ReceiptWrite(receipt=stored, recorded=False)
        self._receipts[key] = receipt
        return ReceiptWrite(receipt=receipt, recorded=True)

    def receipts(
        self,
        *,
        workspace: str,
        event_id: str | None = None,
        subscription_id: str | None = None,
    ) -> tuple[DeliveryReceipt, ...]:
        return tuple(
            sorted(
                (
                    self._own_receipt(receipt)
                    for receipt in self._receipts.values()
                    if receipt.workspace == workspace
                    and (event_id is None or receipt.event_id == event_id)
                    and (
                        subscription_id is None
                        or receipt.subscription_id == subscription_id
                    )
                ),
                key=lambda receipt: (
                    receipt.event_id,
                    receipt.subscription_id,
                    receipt.sink,
                    receipt.attempt,
                ),
            )
        )

    def attempts(self, *, event_id: str, subscription_id: str, sink: str) -> int:
        used = [
            attempt
            for (got_event, got_sub, got_sink, attempt) in self._receipts
            if (got_event, got_sub, got_sink) == (event_id, subscription_id, sink)
        ]
        return max(used) if used else 0

    # ------------------------------------------------------------ 内部
    def _current_subscription(
        self, subscription_id: str
    ) -> NotificationSubscription | None:
        rows = self._subscriptions.get(subscription_id)
        if not rows:
            return None
        return self._own_subscription(max(rows, key=lambda row: row.revision))

    def _require_base(
        self,
        subscription: NotificationSubscription,
        current: NotificationSubscription | None,
        expected_revision: int | None,
    ) -> None:
        if expected_revision is None:
            if current is not None:
                raise SubscriptionConflictError(
                    f"subscription_id={subscription.subscription_id!r} 已经在了（当前第 "
                    f"{current.revision} 版），本次调用声称它是首次创建。"
                )
            return
        if current is None:
            raise SubscriptionConflictError(
                f"subscription_id={subscription.subscription_id!r} 在盘上没有任何一版，"
                f"而本次调用说它读到的是第 {expected_revision} 版。"
            )
        if current.revision != expected_revision:
            raise SubscriptionConflictError(
                f"subscription_id={subscription.subscription_id!r} 手上的基准是第 "
                f"{expected_revision} 版，而盘上当前是第 {current.revision} 版 —— "
                "有人先写了。"
            )

    def _own_event(self, event: NotificationEvent) -> NotificationEvent:
        if event.workspace == self._workspace:
            return event
        raise StoreError(
            f"notification_event[{event.event_id}] 写着 workspace={event.workspace!r}，"
            f"而这个存储绑在 {self._workspace!r} 上。"
        )

    def _own_subscription(
        self, subscription: NotificationSubscription
    ) -> NotificationSubscription:
        if subscription.workspace == self._workspace:
            return subscription
        raise StoreError(
            f"notification_subscription[{subscription.subscription_id}] 写着 "
            f"workspace={subscription.workspace!r}，而这个存储绑在 "
            f"{self._workspace!r} 上。"
        )

    def _own_receipt(self, receipt: DeliveryReceipt) -> DeliveryReceipt:
        if receipt.workspace == self._workspace:
            return receipt
        raise StoreError(
            f"delivery_receipt[{receipt.event_id}#{receipt.attempt}] 写着 "
            f"workspace={receipt.workspace!r}，而这个存储绑在 {self._workspace!r} 上。"
        )


class InMemorySessionBindingStore(SessionBindingStore):
    """不落盘的会话绑定存储：**测试替身**，不是部署选项。

    ``put_binding`` 的两步判据在这里逐字重放：先按 ``binding_id`` 找（撞车就抛），
    再按**会话四格**找（找到就替换那一行，没找到就插入）。第二步是「就地更新」
    那一半，而它的身份是**会话**、不是那个 id —— 见
    ``ports/session.py::put_binding``。
    """

    def __init__(self) -> None:
        self._workspace: str | None = None
        #: ``binding_id`` → 那一行。**没有版本**：同一个会话再写一次是替换。
        self._rows: dict[str, SessionBinding] = {}

    def bind_workspace(self, workspace: str) -> None:
        if self._workspace is None:
            self._workspace = workspace
            return
        if self._workspace != workspace:
            raise StoreError(
                f"这个状态库已经绑在工作区 {self._workspace!r} 上，本次写入的对象属于 "
                f"{workspace!r}。"
            )

    def put_binding(self, binding: SessionBinding) -> BindingWrite:
        self.bind_workspace(binding.workspace)
        existing = self._rows.get(binding.binding_id)
        if existing is not None and self._session(existing) != self._session(binding):
            raise StoreError(
                f"binding_id={binding.binding_id!r} 在盘上属于另一个会话：盘上是 "
                f"{self._session(existing)}，本次是 {self._session(binding)}。"
            )
        twin = self._by_session(binding)
        if twin is not None:
            # **``binding_id`` 取盘上那一行的**：它是这一行的身份，而「同一个会话
            # 又动了一下焦点」改的是焦点，不是这个会话是谁。回交上来那个 id 会让
            # 调用方拿着一个盘上不存在的 id 去做下一次写入（那次写入会新插一行）。
            kept = binding.model_copy(update={"binding_id": twin.binding_id})
            self._rows[kept.binding_id] = kept
            return BindingWrite(binding=kept, created=False)
        self._rows[binding.binding_id] = binding
        return BindingWrite(binding=binding, created=True)

    def get_binding(self, binding_id: str) -> SessionBinding | None:
        found = self._rows.get(binding_id)
        return None if found is None else self._own(found)

    def bindings(
        self, *, workspace: str, principal: str | None = None
    ) -> tuple[SessionBinding, ...]:
        return tuple(
            sorted(
                (
                    self._own(row)
                    for row in self._rows.values()
                    if row.workspace == workspace
                    and (principal is None or row.principal == principal)
                ),
                key=lambda row: (row.principal, row.client_kind, row.binding_id),
            )
        )

    # ------------------------------------------------------------ 内部
    @staticmethod
    def _session(binding: SessionBinding) -> tuple[str, str, str, str | None]:
        """会话四格。**``binding_id`` 不在里面** —— 理由见端口那一句。"""
        return (
            binding.workspace,
            binding.principal,
            binding.client_kind,
            binding.client_session_ref,
        )

    def _by_session(self, binding: SessionBinding) -> SessionBinding | None:
        """按会话四格找那一行。**没有会话引用的那一支按 ``binding_id`` 找。**

        理由与 SQLite 那一份逐字相同（``adapters/sqlite/session.py::_by_session``）：
        ``client_session_ref`` 是 ``None`` 时那四格里有一格没有内容，而两个都没有 id
        的会话不是同一个会话 —— 合成一行会让 B 读到 A 的焦点。这一支的身份因此是
        调用方给的那个 ``binding_id``。
        """
        if binding.client_session_ref is None:
            found = self._rows.get(binding.binding_id)
            if found is None or found.client_session_ref is not None:
                return None
            return found if self._session(found)[:3] == self._session(binding)[:3] else None
        wanted = self._session(binding)
        for row in self._rows.values():
            if self._session(row) == wanted:
                return row
        return None

    def _own(self, binding: SessionBinding) -> SessionBinding:
        if binding.workspace == self._workspace:
            return binding
        raise StoreError(
            f"session_binding[{binding.binding_id}] 写着 workspace="
            f"{binding.workspace!r}，而这个存储绑在 {self._workspace!r} 上。"
        )


class RecordingSink:
    """接住投递、把每一次尝试记下来的 sink。**测试替身**。

    它实现 ``ports/notification.py::NotificationSink``（一个 ``Protocol``，所以
    这里不需要继承，只需要 ``name`` 与 ``deliver`` 逐字同名同签名）。

    ## ``name`` 为什么是构造参数而不是类属性

    收据上的 ``sink`` 列取自订阅，而不是取自 sink 自己 —— 于是「订阅写着 ``inbox``
    而实际送到别处」在盘上读不出来（端口那一格逐字）。测「投给两个 sink 是两张
    收据」需要**两个名字不同的真 sink**，而如果这里把名字写死成一个类属性，
    那个测试就只能造两个类，或者去改订阅上的名字 —— 后者会让被测的那条判据
    （按名字把订阅接到 sink 上）在测试里消失。

    ## 它记的是 ``DeliveryAttempt`` 的**入参**，不是它自己编的结果

    ``accepted`` 是这条替身**声明**的，而它证明不了「对方收到了」。真 sink 的
    ``accepted`` 之所以可证，是因为它落下的那件事在本机读得回来（
    ``adapters/notification.py::InboxSink`` 写的是事件行本身）—— 这条替身没有
    那一格，所以它只能用于**判据是「投了几次、投给谁」**的那些测试。
    """

    def __init__(self, name: str = "recording", *, outcome: DeliveryOutcome = DeliveryOutcome.ACCEPTED,
                 message_ref: str | None = None) -> None:
        self.name = name
        self._outcome = outcome
        self._message_ref = message_ref
        #: 每一次 ``deliver`` 的 ``(event, subscription)``，按调用次序。**只追加。**
        self.calls: list[tuple[NotificationEvent, NotificationSubscription]] = []

    @property
    def attempts(self) -> int:
        """收下过几次。**它不等于「投递成功几次」** —— 见类 docstring。"""
        return len(self.calls)

    def deliver(
        self, event: NotificationEvent, subscription: NotificationSubscription
    ) -> DeliveryAttempt:
        self.calls.append((event, subscription))
        return DeliveryAttempt(
            outcome=self._outcome,
            message_ref=self._message_ref or f"recording:{event.event_id}",
            detail={"sink": self.name},
        )


class FailingSink:
    """总是失败、并说清下一次什么时候可以再试的 sink。**测试替身**。

    它存在的理由是验收里那条最容易写错的话：「一个 sink 投递失败**只重试通知，
    不重跑研究**」。要让那句话可观测，失败必须是**真的可重复**的（否则第二问
    「重试之后研究跑了几次」问不出来），而且要带 ``retry_after`` ——
    ``application/notification.py::deliver_event`` 只在 ``retry_after`` 没到的时候
    回 ``WAITING``，一个不填 ``retry_after`` 的失败替身会把「等一等再重试」那条
    分支在测试里变成「立刻无限重试」。

    ## 它回的是 ``UNKNOWN``，不是某个「失败」

    ``DeliveryOutcome`` **只有两个**成员（``ACCEPTED`` 与 ``UNKNOWN``），
    没有第三个叫「失败」的。这不是枚举少写了一个：一个出口没能把东西交出去时，
    我们知道的只是「没确认收到」，而**不知道**对方到底收没收到 —— 网络超时、
    对端 500、连接被中间设备吃掉，三种都长得一样。把这种情形报成 ``FAILED``
    等于替对方说一句「你确实没收到」，而那是**没有证据**的一句话；
    ``accepted`` 与 ``unknown`` 这两个成员合起来说的正是「有证据的收到」和
    「没有证据」，这也正是 ``put_notification_receipt`` 那条诚实性要求
    （回执只能报这两种，不许编一个 ``delivered``）在端口上的形态。

    失败的原因走 ``failure`` 那一格，重试的时刻走 ``retry_after``。
    """

    def __init__(self, name: str = "failing", *, retry_after: timedelta | None = None,
                 failure: str = "这个出口在这条测试里永远失败") -> None:
        self.name = name
        self._retry_after = retry_after
        self._failure = failure
        self.calls: list[tuple[NotificationEvent, NotificationSubscription]] = []

    @property
    def attempts(self) -> int:
        return len(self.calls)

    def deliver(
        self, event: NotificationEvent, subscription: NotificationSubscription
    ) -> DeliveryAttempt:
        self.calls.append((event, subscription))
        return DeliveryAttempt(
            outcome=DeliveryOutcome.UNKNOWN,
            failure=self._failure,
            retry_after=(
                None
                if self._retry_after is None
                else to_utc(event.occurred_at) + self._retry_after
            ),
            detail={"sink": self.name},
        )


class RecordingScheduleAction:
    """被调度点着跑的那个动作。**测试替身**，只记账，真的什么也不做。

    ## ``perform_calls`` 是这一片里最承重的一个计数器

    两条验收判据都读它，而且都读**「它有没有被调用」**、不是「它返回了什么」：

    * 「duplicate fire 幂等」= 同一个 ``fire_id`` 点第二次，它还是 1 次；
    * 「空队列不调用 LLM」= 没有到期的调度时，它是 **0 次**。

    前者之所以要这样测：一个重放返回同一份 ``ActionReport`` 的实现，在报告上与
    真的挡住了重跑**逐字同形**。后者同理：一次「due check 之后才跑」只是在
    代码里挪了一行，而它在返回值上留下的痕迹是「报告里 fired=0」——
    与本条判据要问的问题（**有没有花钱**）无关。

    ## ``observer``：让「占位已经在盘上了」这句话可测

    ``perform`` 在**动作之前**就被调用，而那时调用方已经 ``claim_fire`` 过。
    一个把 ``claim_fire`` 挪到动作**之后**的实现（或者干脆去掉）在这次调用里
    看得出来：``observer(definition, fire_id)`` 拿到的那个回调会在动作**跑的时候**
    去读盘，读到 ``None`` 就意味着「占位还没写」。变异测试要的就是这个观测点 ——
    没有它，「幂等来自插入失败」这句活只能靠读代码来确认。
    """

    def __init__(
        self,
        *,
        next_due_at: datetime | None = None,
        outcome: FireOutcome = FireOutcome.RUN_ADMITTED,
        raises: Exception | None = None,
        observer: Callable[[ScheduleDefinition, str], None] | None = None,
    ) -> None:
        self._next_due_at = next_due_at
        self._outcome = outcome
        self._raises = raises
        self._observer = observer
        #: 每一次 ``perform`` 的 ``(definition, fire_id, at)``，按调用次序。**只追加。**
        self.perform_calls: list[tuple[ScheduleDefinition, str, datetime]] = []

    @property
    def attempts(self) -> int:
        return len(self.perform_calls)

    def perform(
        self, definition: ScheduleDefinition, *, fire_id: str, at: datetime
    ) -> ActionReport:
        self.perform_calls.append((definition, fire_id, at))
        if self._observer is not None:
            self._observer(definition, fire_id)
        if self._raises is not None:
            raise self._raises
        return ActionReport(
            outcome=self._outcome,
            message=f"{definition.schedule_id}@{fire_id} 由测试替身执行",
            next_due_at=self._next_due_at,
            detail={"action_kind": definition.action_spec.kind.value},
        )


class InMemoryClaimWithdrawalStore(ClaimWithdrawalStore):
    """撤回一条说法的两个能力，内存实现 —— **测试替身**，不是部署选项。

    它**不自己存东西**：读与写都转手给同一个 :class:`InMemoryOutreachStore` 实例。
    这一点是刻意的，而且它就是 SQLite 那一份的形状 —— ``SqliteClaimWithdrawalStore``
    与 ``SqliteOutreachStore`` 指着**同一个库文件**（同一批表），所以「撤回之后草稿
    读得回来」在那两份上成立的方式是一样的。两份各存一份的实现会让
    ``drafts_citing`` 在一个库里筛，而撤回写进另一个库，两边都自洽、都跑得过。

    边界与其余内存实现逐字相同：**没有锁**。「一个事务」在这里的形状是「先全判、
    后全写」加一次失败回滚（见 :meth:`withdraw`），而不是 ``BEGIN IMMEDIATE``。
    """

    def __init__(self, outreach: InMemoryOutreachStore) -> None:
        self._outreach = outreach

    def drafts_citing(
        self, *, workspace: str, claim_id: str
    ) -> tuple[OutreachDraft, ...]:
        """本工作区里引用了这条说法的草稿，每份取**最新**一版，按 ``draft_id`` 排序。

        与 ``SqliteClaimWithdrawalStore`` 那份逐条对齐：最新一版按 ``draft_id`` 在
        **工作区内**取（一份草稿的版本不跨工作区），过滤是 ``claim_id in
        draft.claim_ids`` 这个**结构化引用**，不是正文关键词。
        """
        newest: dict[str, OutreachDraft] = {}
        for draft in self._outreach._drafts.values():
            if draft.workspace != workspace:
                continue
            current = newest.get(draft.draft_id)
            if current is None or draft.draft_version > current.draft_version:
                newest[draft.draft_id] = draft
        return tuple(
            draft
            for draft in (newest[key] for key in sorted(newest))
            if claim_id in draft.claim_ids
        )

    def attempts(self, *, workspace: str) -> tuple[OutboundAttempt, ...]:
        """本工作区的全部投递尝试，按 ``(started_at, send_key)`` 排序。

        排序与 SQL 那条 ``ORDER BY started_at, send_key`` 逐字同一条：回执里那句
        「已经发过了」带着它的出处（哪一条尝试），而两张表读出来的顺序不同时，
        同一份数据在两个实现上会给出两句不同的话。
        """
        return tuple(
            sorted(
                (
                    attempt
                    for attempt in self._outreach._attempts.values()
                    if attempt.workspace == workspace
                ),
                key=lambda attempt: (attempt.started_at, attempt.send_key),
            )
        )

    def withdraw(
        self, *, retirement: ApprovedClaim, drafts: tuple[OutreachDraft, ...]
    ) -> None:
        """**在副本上写，写完才换上去** —— 这就是这一份的「一个事务」。

        判据本身**一条都不在这里**：每一行交给
        ``InMemoryOutreachStore.put_claim`` / ``put_draft`` 去问 —— 「同一个
        ``(id, 版)`` 配另一份内容报错、同内容幂等」那两条判断在那一处写着（那一处
        也是 SQLite 那份的对应物），在这里再写一遍就是同一句话的第二份实现。

        于是「全成或全不成」在这里的形状是**换字典**：先把两张表换成副本，让两个
        ``put_*`` 在副本上跑（它们照旧会按同一份判据抛），抛了就换回原来那两张
        （一行的变化都没有留下），没抛就留着副本。没有第二套写入路径、也没有第二
        份判断，而半改的状态在结构上不可达 —— 只有一次赋值。

        ``StoreError`` 是这条路径上**唯一**会抛的东西（两个 ``put_*`` 只抛它），
        所以只接它：接宽了会把一次真的崩掉读成「一次被拒的撤回」。
        """
        outreach = self._outreach
        claims, drafts_table = outreach._claims, outreach._drafts
        outreach._claims, outreach._drafts = dict(claims), dict(drafts_table)
        try:
            outreach.put_claim(retirement)
            for draft in drafts:
                outreach.put_draft(draft)
        except StoreError:
            outreach._claims, outreach._drafts = claims, drafts_table
            raise

"""``application/outreach.py`` 契约：**外向链的五段，每一段的「停下来」都有一个执行器**。

## 为什么这个文件必须存在

``marketops/application/outreach.py`` 的 docstring 花了很长篇幅论证「为什么那些
检查必须在一处、且排成一段没有分支可以绕过的序列」。一段写在文档里的序列是
**自觉**：下一个改动的人只要少写一个 ``if``，整段话就变成假的，而没有任何东西
会红。本仓对这件事只有一条处置 —— 每一条边界配一个执行器。这个文件就是那批
执行器。

## 三个替身，各自守一件事

* :class:`FixedWriter` —— 一份**确定**的稿子，让「同一份输入跑两次」可判。
* :class:`RaisingWriter` —— 一个会抛的写作者。它守 §5 的「单条失败不得毁掉整批」。
* :class:`RecordingTransport` —— 只记下字节、**从不真的发**。它守的是「这一批
  到底把几封交给了传输」这个问题，而它是报告上唯一诚实的那个数。

## 两个实现，同一组判据

``store`` 与 ``scenes`` 各自按 ``sqlite`` / ``memory`` 参数化（与
``tests/test_outreach_store_contract.py`` 同一处置）：接在这里的理由不是「再测
一遍存储」，而是**编排层依赖的存储语义**（``won``、``is_suppressed`` 是查不是
读缓存、``begin_attempt`` 的 CAS）在两个实现上一致。一个只对 sqlite 成立的编排
会在内存替身上把「不得重复发」测成一句空话。

## 有一条判据刻意**不在这里**

「``dispatch_wave`` 之后重开一个进程还能读到这次投递」只对持久化实现成立。它
在 ``tests/test_sqlite_outreach.py`` 那一侧。
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from marketops.adapters.memory import InMemoryOutreachStore, InMemorySceneStore
from marketops.adapters.sqlite.outreach import SqliteOutreachStore
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.application.freshness import judge
from marketops.application.outreach import (
    INTAKE_STATUSES,
    KEY_CONTACT_GRADES,
    DispatchOutcome,
    DraftingOutcome,
    OutreachRules,
    check_prior_stages,
    dispatch_wave,
    drafting_receipt,
    generate_drafts,
    generation_receipt_path,
    load_generation_receipt,
    lapse_authorization,
    mark_authorized,
    plan_drafting,
    plan_wave,
    prepare_wave,
    reconcile_unknown,
    record_authorization,
    send_key_for,
    stop_followup_for,
)
from marketops.domain.entities import ContactRoute
from marketops.domain.enums import (
    BudgetLine,
    EntryKind,
    Grade,
    GradeReason,
    RoleClass,
    RouteBinding,
    RouteStatus,
)
from marketops.domain.errors import ConfigError, MarketOpsError
from marketops.security.authorization import (
    AuthorityCheck,
    AuthorityVerdict,
    NoAuthoritySource,
)
from marketops.domain.maintenance import RefreshPlan
from marketops.domain.outreach import (
    KIND_DISPOSITION,
    AuthorizationProvenance,
    CorrespondenceEvent,
    CorrespondenceKind,
    DraftState,
    MessageState,
    OutreachCampaign,
    OutreachPolicyDecl,
    OutreachStage,
    RecipientSuppression,
    StageWait,
    SuppressionReason,
    TemplateFamily,
    WaveAuthorization,
    WaveState,
    WritingPolicy,
    stops_further_outreach,
)
from marketops.application.scenes import pause
from marketops.domain.scene import (
    PauseReason,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.temporal import FreshnessPolicy
from marketops.domain.util import sha256_text
from marketops.ports.outreach import DraftProposal, DraftRequest, DraftWriter

UTC = timezone.utc
T0 = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(days=3)

WORKSPACE = "ws-natural-grass"
SCENE = "scene-uk-natural-grass"
CAMPAIGN = "campaign-uk-grass"
WAVE = "wave-0001"
SENDBOX = "outreach@marketops.example"
POLICY = "policy-uk-grass-stage-1"
FAMILY = "family-uk-grass-stage-1"

#: 本文件的时限口径。**它不是业务口径**，所以没有对应物住在 ``profiles/`` 或
#: ``docs/`` 里 —— 它只是「判得出一条结论」所需要的那个参数。天数是**够松**的：
#: 本文件造出来的时刻都挤在 ``T0`` 前后几天里，所以判出来恒为 ``current``。
#:
#: ``current_within_days`` 与 ``historical_after_days`` 之间那条严格不等式是
#: :class:`FreshnessPolicy` 自己的判据，不是本文件加的；这里照它写。
_POLICY = FreshnessPolicy(
    policy_version="test-v1",
    current_within_days=180,
    historical_after_days=730,
)


@pytest.fixture(params=["sqlite", "memory"])
def backend(request, tmp_path: Path):
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。

    ``(outreach, scenes)`` 成对给出：编排层同时依赖这两个端口，而「两个端口
    来自同一层后端」是它们能一起描述同一件事的前提。
    """
    if request.param == "sqlite":
        db = tmp_path / "state" / "marketops.db"
        return SqliteOutreachStore(db), SqliteSceneStore(db)
    return InMemoryOutreachStore(), InMemorySceneStore()


@pytest.fixture
def store(backend):
    return backend[0]


@pytest.fixture
def scenes(backend):
    return backend[1]


#: 当前用例的回执目录（单元素列表 = 可替换的模块级变量）。
_RECEIPTS: list[Path] = [Path("state/outreach-receipts")]


@pytest.fixture(autouse=True)
def receipts_home(tmp_path: Path):
    """每个用例一个回执目录，用完即弃（``tmp_path`` 由 pytest 清理）。"""
    _RECEIPTS[0] = tmp_path / "receipts"
    return _RECEIPTS[0]


# --- 替身 ---------------------------------------------------------------------


class FixedWriter(DraftWriter):
    """一份确定的稿子。``bodies`` 按 ``account_id`` 覆盖默认正文。"""

    def __init__(self, *, writer_id: str = "writer-test", bodies: dict[str, str] | None = None):
        self._id = writer_id
        self._bodies = bodies or {}
        self.requests: list = []

    @property
    def writer_id(self) -> str:
        return self._id

    @property
    def policy_version(self) -> str:
        return "policy-rev-1"

    def draft(self, request) -> DraftProposal:
        self.requests.append(request)
        body = self._bodies.get(request.account_id, f"给 {request.organisation} 的一封短信。")
        return DraftProposal(
            subject=f"关于 {request.organisation} 的天然草",
            body=body,
            # 引用**逐对**抄自请求。这一行以前写着
            # ``facts_used=(("organisation", request.organisation),)`` —— 把事实
            # 的**值**填进了证据 id 那一格。它凑巧没红，因为当时没有任何地方
            # 核过这一对；而下游是会拿着这一格去 ``EvidenceStore`` 查的。
            facts_used=tuple(
                (name, evidence_id) for name, _value, evidence_id in request.facts
            ),
            writer_id=self._id,
        )


class HeldWriter(FixedWriter):
    """只写得出「我还差一个输入」的写作者。**它不猜** —— §5 逐字。

    ``body`` 给定时是**另一种** hold：写作者用一句已批准的通用措辞落了笔，同时
    说「这一条要人看一眼」。两者的落库形状不同，见下面两条判据。
    """

    def __init__(self, *, body: str = "", **kwargs):
        super().__init__(**kwargs)
        self._held_body = body

    def draft(self, request) -> DraftProposal:
        self.requests.append(request)
        return DraftProposal(
            subject="关于贵俱乐部的天然草" if self._held_body else "",
            body=self._held_body,
            held_reason="这条线索缺具名收件人，不得猜一个",
            writer_id=self._id,
        )


class BrokenWriter(FixedWriter):
    """在**指定**账号上抛。别的账号照常 —— 这正是「单条失败不毁整批」。"""

    def __init__(self, *, fail_on: str, **kwargs):
        super().__init__(**kwargs)
        self._fail_on = fail_on

    def draft(self, request) -> DraftProposal:
        if request.account_id == self._fail_on:
            raise RuntimeError("上游写作服务 503")
        return super().draft(request)


class SubjectlessWriter(FixedWriter):
    """正文写得出、**主题写不出**的写作者。它给的提议构造不出一份草稿。"""

    def __init__(self, *, fail_on: str, **kwargs):
        super().__init__(**kwargs)
        self._fail_on = fail_on

    def draft(self, request) -> DraftProposal:
        if request.account_id != self._fail_on:
            return super().draft(request)
        self.requests.append(request)
        return DraftProposal(
            subject="",
            body=f"给 {request.organisation} 的一封短信。",
            writer_id=self._id,
        )


class MixedWriter(FixedWriter):
    """一次里给出**三种**结局：一个账号上抛、一个账号只 hold、其余照写。

    三个替身各跑一趟会得到三份回执，而「六格之和等于冻结对象集」这句话只有在
    **一次调用里有多个结局**时才真的被考到 —— 一次只有一种结局的回执，一个把六格
    全折成「有没有稿子」的实现照样绿。
    """

    def __init__(self, *, fail_on: str, hold_on: str, **kwargs):
        super().__init__(**kwargs)
        self._fail_on = fail_on
        self._hold_on = hold_on

    def draft(self, request) -> DraftProposal:
        if request.account_id == self._fail_on:
            raise RuntimeError("上游写作服务 503")
        if request.account_id == self._hold_on:
            self.requests.append(request)
            return DraftProposal(
                held_reason="这条线索缺具名收件人，不得猜一个", writer_id=self._id
            )
        return super().draft(request)


class EmptyWriter(DraftWriter):
    """什么都不写，也不给理由。**这是一个坏掉的实现，不是一个结论。**"""

    @property
    def writer_id(self) -> str:
        return "writer-empty"

    @property
    def policy_version(self) -> str:
        return "policy-rev-1"

    def draft(self, request) -> DraftProposal:
        return DraftProposal()


class InventingWriter(FixedWriter):
    """写得出正文，但引用一条它**没有被给定**的证据。§5 逐字：「不得编造」。

    这是最容易发生也最难发现的一种：正文读起来完全正常，引用的形状也完全
    正常（``(事实名, 证据 id)``），只有把那一对拿回请求里比一下才知道它是编的。
    """

    def __init__(self, *, evidence_id: str = "ev-invented", **kwargs):
        super().__init__(**kwargs)
        self._evidence_id = evidence_id

    def draft(self, request) -> DraftProposal:
        self.requests.append(request)
        return DraftProposal(
            subject=f"关于 {request.organisation} 的天然草",
            body=f"给 {request.organisation} 的一封短信。",
            facts_used=(("seed_rate", self._evidence_id),),
            writer_id=self._id,
        )


class ClaimingWriter(FixedWriter):
    """引用一句**没有被批准**的话术。同一条判据的另一半。"""

    def draft(self, request) -> DraftProposal:
        self.requests.append(request)
        return DraftProposal(
            subject=f"关于 {request.organisation} 的天然草",
            body=f"给 {request.organisation} 的一封短信。",
            claim_ids=("claim-01-no-herbicide",),
            writer_id=self._id,
        )


class RecordingTransport:
    """只记账，从不真的发。``outcomes`` 按收件人决定这次回什么。

    ``provider_ids`` 给出**这一封在提供方那边的标识**，与结局无关。它不是
    「unknown 也带标识」这种别扭的组合 —— 真实里它就是「连接建立、对面把标
    识给了我们、然后在读回话时超时」，而那正是 :func:`reconcile_unknown` 能
    按标识去问的唯一前提。不给标识的那些（既没回话也没留标识）只能停在
    ``still_unknown``。
    """

    def __init__(
        self,
        *,
        sender_identity: str = SENDBOX,
        outcomes: dict[str, str] | None = None,
        provider_ids: dict[str, str] | None = None,
        raise_on: str = "",
        status_answer: str = "unknown",
    ):
        self._sender = sender_identity
        self._outcomes = outcomes or {}
        self._provider_ids = provider_ids or {}
        self._raise_on = raise_on
        self._status_answer = status_answer
        self.sent: list[dict] = []
        self.asked: list[str] = []

    @property
    def sender_identity(self) -> str:
        return self._sender

    def send(self, *, entry, subject: str, body: str, from_address: str):
        from marketops.ports.outreach import SendReceipt

        if self._raise_on and entry.recipient == self._raise_on:
            # 抛 = 「可能出去了」。调用方必须记 UNKNOWN，而不是 FAILED。
            raise TimeoutError("连接在 DATA 之后断了")
        self.sent.append(
            {
                "recipient": entry.recipient,
                "subject": subject,
                "body": body,
                "from": from_address,
            }
        )
        message_id = self._provider_ids.get(entry.recipient)
        outcome = self._outcomes.get(entry.recipient, "accepted")
        if outcome == "refused":
            return SendReceipt(outcome="refused", accepted=False, reason="550 邮箱不存在")
        if outcome == "unknown":
            return SendReceipt(
                outcome="unknown",
                accepted=False,
                message_id=message_id or "",
                reason="提供方没有回话",
            )
        return SendReceipt(
            outcome="accepted", accepted=True, message_id=message_id or f"<mid-{len(self.sent)}@x>"
        )

    def status(self, message_id: str):
        from marketops.ports.outreach import SendReceipt

        self.asked.append(message_id)
        if self._status_answer == "delivered":
            return SendReceipt(outcome="accepted", accepted=True, message_id=message_id)
        return SendReceipt(outcome="unknown", accepted=False, reason="窗口之外")


# --- 构造器 -------------------------------------------------------------------


def make_scene(*, state: SceneState = SceneState.ACTIVE, scene_id: str = SCENE) -> SceneRecord:
    payload = {
        "scene_id": scene_id,
        "workspace": WORKSPACE,
        "state": state,
        "latest_version": 1,
        "effective_version": 1,
        "created_at": T0,
        "updated_at": T0,
    }
    if state is SceneState.PAUSED:
        from marketops.domain.scene import PauseReason

        payload["pause_reason"] = PauseReason.OPERATOR
    if state is SceneState.DRAFT:
        payload["effective_version"] = None
    return SceneRecord(**payload)


def _decl(**overrides) -> OutreachPolicyDecl:
    """缺省那一份，逐项覆盖。**走构造器而不是 ``model_copy``。**

    ``model_copy(update=...)`` 在 pydantic v2 上**不重跑校验器**，于是用它写的
    「缺一段必须被拒」会在一个根本不会拒绝的实现上变绿 —— 这类假绿最难发现，
    因为它只在有人真的去删一个校验器时才显形。本文件里所有「构造期必须拒绝」
    的判据都走这一个入口。
    """
    default = OutreachPolicyDecl.uncalibrated_default()
    payload = {
        "max_per_wave": default.max_per_wave,
        "max_per_recipient": default.max_per_recipient,
        "max_per_account": default.max_per_account,
        "waits": default.waits,
        "prior_touches": default.prior_touches,
    }
    payload.update(overrides)
    return OutreachPolicyDecl(**payload)


def _scene_version(*, outreach: OutreachPolicyDecl) -> SceneVersion:
    """一版 Scene，只为拿它的 ``frozen_sha256``。

    ``outreach`` 是**必填**参数：本函数的全部用途就是比对「只有触达策略不同」的
    两版摘要，给它一个缺省会让「忘了传」静默变成「两份一样」—— 而那正是要测的
    那个否命题。
    """
    return SceneVersion(
        scene_id=SCENE,
        workspace=WORKSPACE,
        version=1,
        region=("GB-England-Devon",),
        scenario="sports-surface-maintenance",
        product="acme-natural-grass",
        admissions=("sports-club",),
        exclusions=("golf-course",),
        role_policy="role-policy-v1",
        source_plan=("mother-pool-import",),
        budget=(SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        refresh=RefreshPlan.uncalibrated_default(),
        outreach=outreach,
        profile_id="uk-acme-natural-grass",
        profile_version="2026-09-01",
        created_at=T0,
    )


def _judged(route: ContactRoute) -> ContactRoute:
    """把这条路线**按生产路径判一次时效**，返回带着结论的那一行。

    ## 为什么这里不手写一个 :class:`Freshness`

    手写一个是**能过**的 —— 只要 ``assertion_id`` 抄对了 ``expected_assertion_id``。
    但那样一来，「这一稿为什么是 review-only」这条链上的第一格
    （:func:`review_reasons` 读的 ``is_actionable``）就成了**本文件自己造的事实**，
    而它要测的恰恰是生产代码判出来的那一格。走 :func:`judge` 就是走 ``pipeline``
    用的同一个入口：断言的 id 由 ``assertion_for`` 从行上算出来，结论由
    ``evaluate_freshness`` 给，重建走 ``model_validate``（于是行级判据真的复核了
    一次）。三条都不是本文件决定的。

    没有时刻锚点的行**照样能过这里**：``assertion_for`` 返回 ``None``，
    :class:`Judgement` 原样带回那一行、``freshness`` 留空 —— 那正是生产语义
    （``NO_TIME_ANCHOR`` 是「判据没能开工」，与「判过、结论是 unknown」不同）。
    """
    return judge(route, _POLICY, now=T0).row


def make_route(
    *,
    account_id: str = "account-0001",
    email: str = "grounds@example.co.uk",
    grade: Grade = Grade.A,
    status: RouteStatus = RouteStatus.OBSERVED,
    evidence_ids: list[str] | None = None,
    organisation: str = "Example Cricket Club",
    binding: RouteBinding = RouteBinding.STRONG,
    entry_kind: EntryKind = EntryKind.NAMED_ROLE,
    role_class: RoleClass = RoleClass.UNKNOWN,
    first_seen_at: datetime | None = T0,
) -> ContactRoute:
    """一条**充分**的路线：绑定判过、有观测时刻、时效判过且当前。

    ## 为什么缺省落在「充分」这一侧

    本文件里绝大多数用例问的不是「这条够不够」，而是「够了之后这条链怎么走」。
    而 ``plan_drafting`` 会把一条 ``binding=none``（从来没人判过）或
    ``freshness`` 为空的路线判成 ``review_only``（见 :func:`review_reasons`），
    而 review-only 的稿子**不进清单** —— ``prepare_wave`` 只收 ``GENERATED``。
    缺省若落在「不够」那一侧，三十来个用例会一起红，而红的原因与它们各自要测的
    事毫无关系。这不是把判据放宽：判据一条没动，只是本文件默认造一条过了判据的行。

    ``entry_kind`` 与 ``role_class`` 也在这里，因为 §B13 的关闭条件要求
    「A/B/C/D 各有合适草稿」——那一批用例要**造得出**四种入口与几档角色路线，
    而不是每条路线都长成同一个样子。

    要走「不够」的那一侧，显式传 ``binding=RouteBinding.NONE`` 或
    ``first_seen_at=None``。两条路都走构造器（不用 ``model_copy``），理由见
    :func:`_decl`。
    """
    route = ContactRoute(
        # 血统是**必填、无默认值**的，所以这个构造器必须说清它 —— 见
        # ``tests/test_evidence_store_contract.py`` 的 ``WORKSPACE`` 那段说明：
        # 这是测试构造器（不是模型），有一个值是合法的；模型上没有。
        workspace=WORKSPACE,
        account_id=account_id,
        organisation=organisation,
        email=email,
        grade=grade,
        grade_reason=GradeReason.RULE_MATCHED,
        status=status,
        evidence_ids=["evidence-0001"] if evidence_ids is None else evidence_ids,
        binding=binding,
        entry_kind=entry_kind,
        role_class=role_class,
        first_seen_at=first_seen_at,
        source_published_at=None if first_seen_at is None else first_seen_at - timedelta(days=10),
    )
    return _judged(route)


def make_rules(*, max_per_wave: int = 20, max_per_recipient: int = 3, waits=None, priors=None):
    return OutreachRules(
        max_per_wave=max_per_wave,
        max_per_recipient=max_per_recipient,
        allowed_wait=waits if waits is not None else {stage: timedelta(days=2) for stage in OutreachStage},
        allowed_prior_touches=priors if priors is not None else {stage: 3 for stage in OutreachStage},
    )


def seed(
    store,
    *,
    routes=(),
    stages=(OutreachStage.STAGE_1,),
    role_class: str = "grounds_manager",
) -> None:
    """把模板族与策略送进库 —— 没有它们，``generate_drafts`` 一条都产不出来。

    ``role_class`` 是**这一族的角色路线**（``family_for`` 第一层逐字比的那一格）。
    它可覆盖，因为 §B13 的关闭条件要求「A/B/C/D 各有**合适的**草稿」—— 而
    「合适」在 C/D 那一侧的意思是「有一族是为它们写的」，见
    :func:`seed_another_family`。
    """
    for stage in stages:
        family_id = FAMILY if stage is OutreachStage.STAGE_1 else f"family-uk-grass-{stage.value}"
        policy_id = POLICY if stage is OutreachStage.STAGE_1 else f"policy-uk-grass-{stage.value}"
        store.put_writing_policy(WritingPolicy(policy_id=policy_id, revision=1))
        store.put_family(
            TemplateFamily(
                family_id=family_id,
                revision=1,
                stage=stage,
                role_class=role_class,
                locale="en-GB",
                product="natural-grass",
                writing_policy_id=policy_id,
            )
        )


def seed_another_family(
    store, *, family_id: str, role_class: str, stage: OutreachStage = OutreachStage.STAGE_1
) -> None:
    """再落一族，服务**另一条角色路线**。C/D 要写出稿子就得有这一族。

    ## 为什么 C/D 不能蹭第一族

    :func:`~marketops.application.outreach.family_for` 的第二层是「本阶段字典序
    最小的那一族」，而它只对 :data:`FALLBACK_TO_STAGE_FOR_GRADES`（A/B）开放。
    一条 C/D 的路线因此**必须**有一族角色路线与它逐字相同 —— 否则它要么没有稿子，
    要么用上写给关键联系人的那一套话术，而后者正是 §8 逐字要挡的「不冒充具名
    决策人」。所以这条要求不是「多一层配置」，是那句话的**可执行形态**：
    想让 C/D 有稿子，就得有人写一族给它们。
    """
    policy_id = f"{family_id}-policy"
    store.put_writing_policy(WritingPolicy(policy_id=policy_id, revision=1))
    store.put_family(
        TemplateFamily(
            family_id=family_id,
            revision=1,
            stage=stage,
            role_class=role_class,
            locale="en-GB",
            product="natural-grass",
            writing_policy_id=policy_id,
        )
    )


def seed_campaign(store) -> OutreachCampaign:
    campaign = OutreachCampaign(
        campaign_id=CAMPAIGN,
        workspace=WORKSPACE,
        scene_id=SCENE,
        name="UK 天然草 首次接触",
        stage=OutreachStage.STAGE_1,
        family_id=FAMILY,
        created_at=T0,
    )
    store.put_campaign(campaign)
    return campaign


def run_plan(
    store,
    *,
    routes,
    rules=None,
    stage=OutreachStage.STAGE_1,
    at=T0,
    wave_id=WAVE,
    scene_version=1,
):
    return plan_wave(
        routes=list(routes),
        store=store,
        campaign_id=CAMPAIGN,
        wave_id=wave_id,
        workspace=WORKSPACE,
        scene_id=SCENE,
        scene_version=scene_version,
        stage=stage,
        rules=rules or make_rules(),
        at=at,
    )


def run_drafting(
    store,
    *,
    routes,
    stage=OutreachStage.STAGE_1,
    at=T0,
    wave_id=WAVE,
    scene_version=1,
):
    """**起草**那一侧的计划（:func:`plan_drafting`）。

    与 :func:`run_plan` **并列**，而不是合成一个带开关的入口 —— 生产侧也是两个
    函数、两条用例（§8 逐字：「草稿准备与发送队列准备分成两个用例」）。合成一个
    带开关的入口之后，「起草这一趟读了发送前序没有」这件事在测试里就再也问不
    出来了：``plan_wave`` 产出的那份**带着**配额与批上限筛过的行，而在一个
    ``DraftingPlan`` 里它与本函数产出的那份长得一模一样。
    """
    return plan_drafting(
        routes=list(routes),
        store=store,
        families=store.families(),
        campaign_id=CAMPAIGN,
        wave_id=wave_id,
        workspace=WORKSPACE,
        scene_id=SCENE,
        scene_version=scene_version,
        stage=stage,
        at=at,
    )


def _a_request(*, facts=()):
    """一份**合法**的 :class:`DraftRequest`，只是 ``facts`` 随用例给。

    ``family`` 与 ``policy`` 取的是本文件诸构造器的那两个值：请求里这两格
    只是被携带，写作者拿到的正文不经过它们，所以照抄 ``seed()`` 写入库的那份
    就够了 —— 一份在这里另造一个不重名的族，会让「这一条红的到底是哪一件事」
    变得要读两处才知道。
    """
    return DraftRequest(
        account_id="account-0001",
        organisation="Example Cricket Club",
        family=TemplateFamily(
            family_id=FAMILY,
            revision=1,
            stage=OutreachStage.STAGE_1,
            role_class="grounds_manager",
            locale="en-GB",
            product="natural-grass",
            writing_policy_id=POLICY,
        ),
        policy=WritingPolicy(policy_id=POLICY, revision=1),
        stage=OutreachStage.STAGE_1,
        locale="en-GB",
        facts=facts,
    )


def draft_them(
    store, routes, *, writer=None, bodies=None, facts=None, at=T0, wave_id=WAVE, receipts_dir=None
):
    writer = writer or FixedWriter(bodies=bodies)
    return generate_drafts(
        plan=run_drafting(store, routes=routes, at=at, wave_id=wave_id),
        store=store,
        writer=writer,
        campaign_id=CAMPAIGN,
        locale="en-GB",
        facts_by_account=facts or {},
        organisation_by_account={},
        receipts_dir=receipts_dir or _receipts_dir(),
        at=at,
    )


def _receipts_dir() -> Path:
    """本用例的回执目录。由 :func:`receipts_home` 换成一个临时目录。

    **不许落到仓库里**：回执含正文摘要与写作策略版本，是运行数据。写成模块级
    的可替换值而不是 ``tmp_path`` 直传，是因为 ``draft_them`` 的调用点太多，
    而每个调用点各自拼一次路径正是出错的地方。
    """
    return _RECEIPTS[0]


def freeze(
    store, routes, *, rules=None, at=T0, wave_id=WAVE, valid_until=T2, window_start=None, window_end=T2
):
    """冻一批发送清单。**它自己算那份** ``WavePlan`` **。**

    与 :func:`draft_them` 一样收 ``routes`` 而不是一份计划 —— 两条用例各自从
    路线出发，与生产里那两个入口逐字同形（``plan_wave`` 与 ``plan_drafting``
    各读一遍同一批路线）。让它们共用一份计划，就等于在测试里假装存在一条
    「先排发送、再照着起草」的调用链，而那条链正是 §8 要拆掉的东西。
    """
    campaign = seed_campaign(store)
    return prepare_wave(
        plan=run_plan(store, routes=routes, rules=rules, at=at, wave_id=wave_id),
        store=store,
        campaign=campaign,
        sendbox=SENDBOX,
        window_start=T0 - timedelta(days=1) if window_start is None else window_start,
        window_end=window_end,
        valid_until=valid_until,
        at=at,
    )


def authorise(
    store,
    manifest,
    *,
    approved_count=None,
    at=T0,
    expires_at=T2,
    authorization_id="auth-0001",
    provenance=AuthorizationProvenance.BEARER_PRINCIPAL,
):
    auth = WaveAuthorization(
        authorization_id=authorization_id,
        wave_id=manifest.wave_id,
        workspace=manifest.workspace,
        scene_id=manifest.scene_id,
        manifest_sha256=manifest.manifest_sha256,
        authorized_by="operator:member-a",
        authority_ref="approval-ticket-0001",
        authorized_at=at,
        expires_at=expires_at,
        approved_count=len(manifest.entries) if approved_count is None else approved_count,
        # 这一支测试的是投递链本身；受管来源标记是 MCP authorize_wave 写入的，
        # 这里显式声明同一个取值，否则 `dispatch_wave` 会按默认的 UNVERIFIED
        # 整批拒绝 —— 那一条由本节后面的授权边界用例单独钉住。
        provenance=provenance,
    )
    record_authorization(authorization=auth, store=store, at=at)
    mark_authorized(wave_id=manifest.wave_id, authorization_id=auth.authorization_id, store=store, at=at)
    return auth


def a_sendable_wave(
    store, scenes, *, recipients=("grounds@example.co.uk",), bodies=None, rules=None, window_start=None
):
    """一条能发出去的批次，作为下面各条判据的起点。**它自己不带断言。**"""
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [
        make_route(account_id=f"account-{index:04d}", email=email)
        for index, email in enumerate(recipients, start=1)
    ]
    draft_them(store, routes, bodies=bodies)
    manifest = freeze(store, routes, rules=rules, window_start=window_start)
    authorise(store, manifest)
    return routes, manifest


def do_dispatch(store, scenes, transport, *, rules=None, at=T1, wave_id=WAVE):
    return dispatch_wave(
        wave_id=wave_id,
        store=store,
        scenes=scenes,
        transport=transport,
        rules=rules or make_rules(),
        at=at,
    )


# --- 一、规则：没有缺省值 ------------------------------------------------------


def test_a_wait_that_was_never_configured_is_not_a_wait_of_zero():
    """§6 逐字：「未配置时**不得臆造间隔**」。

    一个 ``allowed_wait`` 缺项时返回 ``timedelta(0)`` 的实现会让「没配」与
    「配成不等」在读的人眼里长得一样，而后果是第 1 段与第 2 段被挤在一起发出去。
    """
    rules = make_rules(
        waits={OutreachStage.STAGE_1: timedelta(days=1)},
        priors={OutreachStage.STAGE_1: 3},
    )
    with pytest.raises(ConfigError) as caught:
        rules.wait_for(OutreachStage.STAGE_2)
    assert "第 2 段" in str(caught.value)

    with pytest.raises(ConfigError):
        rules.prior_touch_budget(OutreachStage.STAGE_2)


def test_required_names_every_thing_that_is_missing():
    """``required`` 的读者是配置装载点，它必须说得出**缺的是哪一项**。"""
    with pytest.raises(ConfigError) as caught:
        OutreachRules.required(max_per_wave=20)
    message = str(caught.value)
    assert "outreach.max_per_recipient" in message
    assert "outreach.allowed_wait" in message
    assert "outreach.allowed_prior_touches" in message
    assert "outreach.max_per_wave" not in message


def test_a_cap_of_zero_is_refused_because_it_reads_like_an_empty_pool():
    with pytest.raises(ConfigError):
        make_rules(max_per_recipient=0)


# --- 一之二、声明的策略：每一个数都要人给，且缺省是**未标定** ------------------


def test_the_default_policy_names_every_item_it_has_not_calibrated():
    """缺省那一份**逐项**点名未标定，而不是一个布尔。

    ``RefreshPlan.uncalibrated`` 是同一条：只说一个 ``False`` 会让「全部已标定」
    与「一项已标定、五项没有」在报告上长得一样，而人据此不知道该去批哪一份策略。
    """
    declared = OutreachPolicyDecl.uncalibrated_default()

    assert declared.is_calibrated is False
    assert sorted(declared.uncalibrated) == [
        "outreach.prior_touches[stage-2]",
        "outreach.prior_touches[stage-3]",
        "outreach.prior_touches[stage-4]",
        "outreach.waits[stage-2]",
        "outreach.waits[stage-3]",
        "outreach.waits[stage-4]",
    ], "缺省里每一项都必须出现在未标定清单上"


def test_calibrating_one_item_still_leaves_the_others_pending():
    """标定一项只让那一项离开清单 —— 这正是「逐项」与「一个布尔」的分界。"""
    default = OutreachPolicyDecl.uncalibrated_default()
    partly = _decl(
        waits=tuple(
            StageWait(stage=row.stage, after_hours=row.after_hours, calibrated=True)
            if row.stage is OutreachStage.STAGE_2
            else row
            for row in default.waits
        )
    )

    assert partly.is_calibrated is False
    assert "outreach.waits[stage-2]" not in partly.uncalibrated
    assert "outreach.waits[stage-3]" in partly.uncalibrated


@pytest.mark.parametrize("missing", ["waits", "prior_touches"])
def test_a_missing_stage_is_not_a_stage_without_a_limit(missing):
    """缺一段**不是**「这一段不设限」，是「没配」。

    编排层对没配的处置是**拒绝发这一段**（``OutreachRules.wait_for`` 抛）。如果在
    声明这一层放过它，那份拒绝会推迟到第一次真的要发第 3 段时才发生 —— 而那时
    已经有一封发出去了。
    """
    default = OutreachPolicyDecl.uncalibrated_default()
    with pytest.raises(ValidationError) as caught:
        _decl(
            **{
                missing: tuple(
                    row
                    for row in getattr(default, missing)
                    if row.stage is not OutreachStage.STAGE_3
                )
            }
        )
    assert "stage-3" in str(caught.value)


def test_the_first_stage_cannot_be_declared_a_wait():
    """第一段没有上一段，所以两张表里都没有它的位置。

    给它配一条的效果是让那个数**永远不会被读到**，而配的人会以为自己设了什么 ——
    与「配了一个拼错的键」是同一种沉默。
    """
    default = OutreachPolicyDecl.uncalibrated_default()
    with pytest.raises(ValidationError) as caught:
        _decl(
            waits=(
                StageWait(stage=OutreachStage.STAGE_1, after_hours=24),
                *default.waits[1:],
            )
        )
    assert "stage-1" in str(caught.value)


def test_a_wait_of_zero_is_refused_because_it_is_the_railway_of_no_wait():
    """``after_hours=0`` 会让四封信在一个 tick 里连着出去 —— 那就是「臆造间隔」，
    只是它以 0 的形式出现，而 0 读起来像「不等」。"""
    with pytest.raises(ValidationError):
        StageWait(stage=OutreachStage.STAGE_2, after_hours=0)


def test_the_rules_converted_from_a_declaration_carry_the_hours_as_deltas():
    """小时 → ``timedelta`` 的换算只有一处，所以它必须是对的那一种。

    ``hours=24`` 写成 ``days=24`` 会让第 2 段等上 24 天 —— 而这两个数在
    ``timedelta`` 上都是合法对象，没有任何一层会报错。
    """
    declared = OutreachPolicyDecl.uncalibrated_default()
    rules = OutreachRules.from_declaration(declared)

    assert rules.wait_for(OutreachStage.STAGE_2) == timedelta(hours=48)
    assert rules.max_per_wave == declared.max_per_wave
    assert rules.max_per_recipient == declared.max_per_recipient
    assert rules.max_per_account == declared.max_per_account
    for row in declared.waits:
        assert rules.wait_for(row.stage) == timedelta(hours=row.after_hours)
    for row in declared.prior_touches:
        assert rules.prior_touch_budget(row.stage) == row.allowed_prior


def test_the_calibration_flag_is_part_of_the_frozen_digest():
    """把同一个数字从「未标定」升级成「已批准」是**内容不同**的一版。

    摘要不变的实现会让那次升级在受控内容上无迹可寻 —— 而授权门与 wave manifest
    绑的正是这个摘要，于是「这一批按哪份规则做的」那个问题的答案不会跟着变。
    """
    default = OutreachPolicyDecl.uncalibrated_default()
    calibrated = _decl(
        waits=tuple(
            StageWait(stage=row.stage, after_hours=row.after_hours, calibrated=True)
            for row in default.waits
        )
    )
    assert calibrated.waits[0].after_hours == default.waits[0].after_hours

    first = _scene_version(outreach=default)
    second = _scene_version(outreach=calibrated)

    assert first.frozen_sha256 != second.frozen_sha256, (
        "标定状态改了而摘要没变 —— 一次「批准了这个数」的升级在受控内容上消失了"
    )


def test_changing_a_wait_changes_the_frozen_digest():
    """节奏改了，摘要必须跟着变 —— 这是它进摘要的全部理由。"""
    default = OutreachPolicyDecl.uncalibrated_default()
    slower = _decl(
        waits=tuple(
            StageWait(stage=row.stage, after_hours=row.after_hours + 1)
            if row.stage is OutreachStage.STAGE_2
            else row
            for row in default.waits
        )
    )
    assert _scene_version(outreach=default).frozen_sha256 != _scene_version(
        outreach=slower
    ).frozen_sha256


# --- 二、筛选 -----------------------------------------------------------------


def test_only_key_contact_grades_enter_the_first_touch(store):
    """C/D 不进首次接触。**这张表在代码里，不散在分支里。**"""
    assert "C" not in KEY_CONTACT_GRADES and "D" not in KEY_CONTACT_GRADES
    plan = run_plan(
        store,
        routes=[
            make_route(account_id="account-0001", email="a@example.co.uk", grade=Grade.A),
            make_route(account_id="account-0002", email="b@example.co.uk", grade=Grade.C),
        ],
    )
    assert [row.account_id for row in plan.entries] == ["account-0001"]
    assert len(plan.skipped) == 1
    assert "等级不是关键联系人：C" == plan.skipped[0].reason


def test_a_route_that_already_left_the_intake_is_not_planned_again(store):
    """重发一条已经进了发送链的路线，人会收到两封。"""
    assert RouteStatus.OBSERVED in INTAKE_STATUSES
    # 类型层**不可能**构造出这个状态（``ContactRoute`` 的
    # ``_cannot_be_sent_without_authorization``），所以这一格只能用
    # ``model_copy`` 造 —— 那正是「库里有、而正常路径写不出来」的含义。
    left_the_intake = make_route().model_copy(
        update={"status": RouteStatus.SENT_STAGE_1}
    )
    plan = run_plan(store, routes=[left_the_intake])
    assert plan.entries == ()
    assert "已经离开入口状态" in plan.skipped[0].reason


def test_a_route_with_no_evidence_is_not_planned(store):
    """一封说不出凭什么发的信，事后无法解释。"""
    route = make_route().model_copy(update={"evidence_ids": []})
    plan = run_plan(store, routes=[route])
    assert plan.entries == ()
    assert plan.skipped[0].reason == "这条路线没有证据"


def test_suppression_is_read_at_the_moment_of_planning(store):
    """抑制是**查**出来的，不是读缓存的。"""
    store.put_suppression(
        RecipientSuppression(
            recipient="grounds@example.co.uk",
            reason=SuppressionReason.UNSUBSCRIBE,
            source_ref="event-0001",
            suppressed_at=T0 - timedelta(days=1),
        )
    )
    plan = run_plan(store, routes=[make_route()])
    assert plan.entries == ()
    assert plan.skipped[0].reason == "收件人在抑制名单上"


def test_one_person_is_one_row_even_when_two_routes_point_at_them(store):
    """**按收件人去重，不是按路线。** 同一个人的两条路线是两条记录，但他是一个人。"""
    plan = run_plan(
        store,
        routes=[
            make_route(account_id="account-0001", email="same@example.co.uk"),
            make_route(account_id="account-0002", email="same@example.co.uk"),
        ],
    )
    assert len(plan.entries) == 1
    assert plan.skipped[0].reason == "这一批里同一个人出现了两次"


def test_one_account_is_one_row_even_with_two_people(store):
    """反过来的那一半：同一家单位的两个人不该在同一批里各收一封。"""
    plan = run_plan(
        store,
        routes=[
            make_route(account_id="account-0001", email="one@example.co.uk"),
            make_route(account_id="account-0001", email="two@example.co.uk"),
        ],
    )
    assert len(plan.entries) == 1
    assert plan.skipped[0].reason == "这一批里同一个账号出现了两次"


def test_the_wave_cap_records_what_it_left_out(store):
    """被截掉的那部分必须被记录 —— 与 ``application/caps.py`` 同一条。"""
    plan = run_plan(
        store,
        routes=[
            make_route(account_id=f"account-{index:04d}", email=f"{index}@example.co.uk")
            for index in range(1, 4)
        ],
        rules=make_rules(max_per_wave=1),
    )
    assert len(plan.entries) == 1
    assert len(plan.skipped) == 2
    assert plan.skipped_line.startswith("planned=1 skipped=2")
    assert "这一批已经到上限（1）" in plan.skipped[0].reason


def test_a_recipient_that_already_used_up_their_quota_is_not_planned(store, scenes):
    """跨批次的历史一起算 —— 一个只查本批次的实现会让同一个人在两个活动里各收一封。

    第一封必须**真的出去过**：配额算的是 ``attempts_by_recipient``，而一次停在
    授权门之前的派发不会留下任何一次尝试 —— 那种情况下第二封本来就该照发。
    """
    a_sendable_wave(store, scenes, rules=make_rules(max_per_recipient=1))
    sent = do_dispatch(store, scenes, RecordingTransport(), rules=make_rules(max_per_recipient=1))
    assert sent.outcomes[0].state == "sent"

    again = run_plan(
        store, routes=[make_route()], wave_id="wave-0002", rules=make_rules(max_per_recipient=1)
    )
    assert again.entries == ()
    assert "已经收满了（上限 1）" in again.skipped[0].reason


def test_the_plan_order_is_a_fact_not_an_accident(store):
    """换一次调用顺序，得到同一份计划 —— 否则「第几条」不是一个稳定的事实。"""
    routes = [
        make_route(account_id=f"account-{index:04d}", email=f"{index}@example.co.uk")
        for index in range(1, 6)
    ]
    forward = run_plan(store, routes=routes, wave_id="wave-0001")
    backward = run_plan(store, routes=list(reversed(routes)), wave_id="wave-0001")
    assert [row.send_key for row in forward.entries] == [
        row.send_key for row in backward.entries
    ]
    keys = [row.send_key for row in forward.entries]
    assert keys == sorted(keys)


# --- 三、生成 -----------------------------------------------------------------


def test_a_writer_that_writes_nothing_and_says_nothing_is_a_failure(store):
    """那是一个坏掉的实现，不是一个结论 —— 记 ``failed``，不写任何行。"""
    seed(store)
    routes = [make_route()]
    outcome = draft_them(store, routes, writer=EmptyWriter())
    assert [receipt.outcome for receipt in outcome.receipts] == ["failed"]
    assert "既没有写出正文，也没有给出一句话的理由" in outcome.receipts[0].reason
    assert outcome.drafts == ()


def test_a_facts_entry_without_an_evidence_id_is_refused_at_the_request(store):
    """``facts`` 的第三格是承重的：它要么在请求里，要么写作者只能编。

    两格的元素以前是**唯一**的写法，而那正是「端口没有实现能满足自己的契约」的
    形状。现在它报错的地方收在请求这一层 —— 而不是等到 ``generation_key``
    去拆它，那时报出来的位置会指向写作者。
    """
    seed(store)
    plan = run_plan(store, routes=[make_route()])
    assert plan.entries  # plan 是活的（免得这条测试在一个空计划上通过）

    request = _a_request()
    assert request.facts == ()
    assert request.generation_key(writer_id="w", policy_version="p")  # 空 facts 也算得出

    # 判词**逐条**匹配，不是「抛了 ValueError 就算」：两格的元素在一次解包里
    # 也会抛 ValueError，于是前一条断言可以在判据被拿掉之后照旧通过 —— 这正是
    # 本仓那条「判据要因为**那个**理由红」的写法。
    with pytest.raises(ValueError, match="应当是 3 格"):
        _a_request(facts=(("seed_rate", "35g/m2"),))
    for bad in (
        (("", "35g/m2", "ev-0001"),),  # 没有事实名
        (("seed_rate", "", "ev-0001"),),  # 没有值
        (("seed_rate", "35g/m2", ""),),  # **有事实但没有证据支撑**
    ):
        with pytest.raises(ValueError, match="三格都不得为空"):
            _a_request(facts=bad)


def test_the_evidence_id_is_part_of_the_input_fingerprint(store):
    """同一个值、换一条证据支撑 ⇒ **不是**同一份输入。§5：「只给输入变了的生成」。

    不算进去的话，重新取证之后旧稿会被读成 ``unchanged`` 而留下来 —— 而
    ``unchanged`` 恰好是那个不会让人再去看一眼的结局。
    """
    first = _a_request(facts=(("seed_rate", "35g/m2", "ev-0001"),)).generation_key(
        writer_id="w", policy_version="p"
    )
    second = _a_request(facts=(("seed_rate", "35g/m2", "ev-0002"),)).generation_key(
        writer_id="w", policy_version="p"
    )
    same = _a_request(facts=(("seed_rate", "35g/m2", "ev-0001"),)).generation_key(
        writer_id="w", policy_version="p"
    )
    assert first != second
    assert first == same  # 同一个输入仍然是同一个 key —— 上面那条不是抖动的产物


def test_a_writer_may_not_cite_an_evidence_row_it_was_not_given(store):
    """§5 逐字：「不得编造」。**成对**判 —— 事实名对上了、id 是编的也算编。

    这条判据以前**只写在端口的 docstring 里**：``facts_used`` 是「待核实的
    引用」，``claim_ids``「必须是请求里 claims 的子集 —— 由调用方校验」。而
    没有任何调用方校验过它。于是一个把证据 id 编得像模像样的写作者在这里一条红
    都不会有，它的稿子带着一条指向虚空的引用落库，而 §5 那条要求以「下游查到
    查不到是下游的事」的形式静默失效。

    先跑一遍**诚实的**写作者：不先证明这条请求生得出稿子，下面那条 failed 就
    可能来自任何一个别的原因。
    """
    seed(store)
    routes = [make_route()]
    facts = {"account-0001": (("seed_rate", "35g/m2", "ev-0001"),)}

    honest = draft_them(store, routes, facts=facts)
    assert [receipt.outcome for receipt in honest.receipts] == ["generated"]
    assert honest.drafts[0].facts_used == (("seed_rate", "ev-0001"),)

    # 换一个 writer_id ⇒ 另一份 generation_key ⇒ 写作者**真的会被调用**
    # （否则 ``unchanged`` 那一步会先返回，这条判据就永远走不到）。
    outcome = draft_them(
        store, routes, writer=InventingWriter(writer_id="writer-invented"), facts=facts
    )
    assert [receipt.outcome for receipt in outcome.receipts] == ["failed"]
    assert "没有被给定的事实" in outcome.receipts[0].reason
    assert outcome.drafts == ()
    # 理由会落盘留档，而那一格里是**写作者的输出**（一个模型可以把客户正文塞进
    # 证据 id）。所以只报条数，不把那串字符串抄进回执。
    assert "ev-invented" not in outcome.receipts[0].reason


def test_a_writer_may_not_cite_a_claim_nobody_approved(store):
    """同一条判据的另一半：``claim_ids`` 必须是请求里 ``claims`` 的子集。"""
    seed(store)
    routes = [make_route()]

    outcome = draft_them(store, routes, writer=ClaimingWriter())
    assert [receipt.outcome for receipt in outcome.receipts] == ["failed"]
    assert "没有被批准的话术" in outcome.receipts[0].reason
    assert outcome.drafts == ()
    assert "claim-01-no-herbicide" not in outcome.receipts[0].reason


def test_a_hold_without_a_body_writes_a_receipt_and_no_draft_row(store, scenes):
    """§5 逐字：「个性化缺失时用已批准的一般措辞或 hold，**不得猜**」。

    一个没有正文的 hold **落不下一行草稿** —— 而这不是缺口，是
    :class:`OutreachDraft` 那条「正文不得为空：一封空的商业信不是一个草稿」的
    直接后果。所以「为什么这一条没发」由**回执**回答，而那正是生成回执存在的
    理由：输入、模型、策略版本、以及这一句理由。
    """
    seed(store)
    routes = [make_route()]
    outcome = draft_them(store, routes, writer=HeldWriter())

    assert [receipt.outcome for receipt in outcome.receipts] == ["held"]
    receipt = outcome.receipts[0]
    assert receipt.draft_version == 0  # 一版都没有产出，而不是「第 0 版」
    assert outcome.drafts == ()
    assert store.latest_drafts(wave_id=WAVE) == ()

    # 回执真的落了盘，而且能读回来 —— 只在内存里的理由，第二天没人查得到。
    written = load_generation_receipt(generation_receipt_path(_receipts_dir(), receipt))
    assert written == receipt
    assert "缺具名收件人" in written.reason

    manifest = freeze(store, routes)
    assert manifest.entries == ()
    # 清单只说得出「没有稿子」：回执在哪，理由就在哪。见本文件末尾的收尾说明。
    assert manifest.excluded[0] == ("grounds@example.co.uk", "这一条没有生成出任何一稿")


def test_a_held_draft_is_written_but_never_reaches_the_manifest(store, scenes):
    """带正文的 hold：稿子落库（状态看得见是 held），但清单里没有它。"""
    seed(store)
    routes = [make_route()]
    outcome = draft_them(store, routes, writer=HeldWriter(body="通用措辞，等人工确认具名收件人"))

    assert [receipt.outcome for receipt in outcome.receipts] == ["held"]
    stored = store.draft(outcome.drafts[0].draft_id, 1)
    assert stored is not None and stored.state is DraftState.HELD
    assert stored.body == "通用措辞，等人工确认具名收件人"

    manifest = freeze(store, routes)
    assert manifest.entries == ()
    assert manifest.excluded[0][1] == "最新一稿的状态是 held"


def test_the_same_input_twice_writes_one_version(store):
    """§5 逐字：「只给新增的或输入变了的对象生成」。"""
    seed(store)
    routes = [make_route()]
    first = draft_them(store, routes)
    assert [receipt.outcome for receipt in first.receipts] == ["generated"]

    second = draft_them(store, routes, at=T1)
    assert [receipt.outcome for receipt in second.receipts] == ["unchanged"]
    assert second.receipts[0].draft_version == 1
    assert store.draft(first.drafts[0].draft_id, 2) is None


def test_changed_input_writes_a_second_version_and_keeps_the_first(store):
    """§5：「内容更新形成新版本」。上一版**不删也不改** —— 它是一份真的生成过的稿子。

    「第 2 版取代了第 1 版」这件事由**新那一版**的 ``supersedes`` 与回执上的
    ``superseded_version`` 记录。把第 1 版原地标成 ``DraftState.SUPERSEDED``
    看起来更省事，但那是一次对**已冻结版次**的改写 —— ``put_draft`` 对「同版次
    不同内容」就是要报错，因为「谁在什么时候把哪一版改成了什么」在事后是查不出
    来的。方向由新那一版说，旧那一版一个字都不动。
    """
    seed(store)
    routes = [make_route()]
    first = draft_them(store, routes)
    draft_id = first.drafts[0].draft_id

    second = draft_them(
        store, routes, facts={"account-0001": (("seed_rate", "35g/m2", "ev-0001"),)}, at=T1
    )
    assert [receipt.outcome for receipt in second.receipts] == ["generated"]
    assert second.receipts[0].draft_version == 2
    assert second.receipts[0].superseded_version == 1
    assert second.drafts[0].supersedes == 1

    v1 = store.draft(draft_id, 1)
    assert v1.state is DraftState.GENERATED
    assert v1.body == first.drafts[0].body  # 逐字未动
    assert store.draft(draft_id, 2).state is DraftState.GENERATED


def test_one_broken_draft_does_not_take_the_batch_with_it(store):
    """§5 逐字：「单条失败不得毁掉整批」。"""
    seed(store)
    routes = [
        make_route(account_id="account-0001", email="a@example.co.uk"),
        make_route(account_id="account-0002", email="b@example.co.uk"),
    ]
    outcome = draft_them(store, routes, writer=BrokenWriter(fail_on="account-0002"))
    by_outcome = {receipt.outcome for receipt in outcome.receipts}
    assert by_outcome == {"generated", "failed"}
    failed = [receipt for receipt in outcome.receipts if receipt.outcome == "failed"][0]
    assert "RuntimeError" in failed.reason and "503" in failed.reason


def test_a_proposal_that_cannot_become_a_draft_fails_alone(store):
    """写作者给的东西构造不出一份草稿时，**只有这一条**失败。

    ``OutreachDraft`` 有几条硬约束（正文不得为空、主题不得为空、主题里不得有换
    行），而提议是外部写作者给的。让 pydantic 的异常从 :func:`generate_drafts`
    里穿出去，一份坏提议会把这一批里其余那些**好的**稿子一起带走 —— 而 §5 逐字
    要求「单条失败不得破坏整批」。理由要带上**字段名**：回执会被读、被留档，而
    「不合法」三个字定位不到任何东西。
    """
    seed(store)
    routes = [
        make_route(account_id="account-0001", email="a@example.co.uk"),
        make_route(account_id="account-0002", email="b@example.co.uk"),
    ]
    outcome = draft_them(store, routes, writer=SubjectlessWriter(fail_on="account-0002"))

    by_outcome = {receipt.outcome for receipt in outcome.receipts}
    assert by_outcome == {"generated", "failed"}
    failed = [receipt for receipt in outcome.receipts if receipt.outcome == "failed"][0]
    assert "subject" in failed.reason and "主题不得为空" in failed.reason
    assert "给 Example Cricket Club 的一封短信。" not in failed.reason, (
        "理由里不得带上正文 —— 回执会被读、被留档，而输入值是客户邮件的正文"
    )
    assert len(outcome.drafts) == 1


def test_a_family_pointing_at_a_policy_nobody_wrote_stops_that_draft(store):
    """一条不存在的策略意味着**没有人审过这些话术**。"""
    store.put_family(
        TemplateFamily(
            family_id=FAMILY,
            revision=1,
            stage=OutreachStage.STAGE_1,
            role_class="grounds_manager",
            locale="en-GB",
            product="natural-grass",
            writing_policy_id="policy-that-does-not-exist",
        )
    )
    outcome = draft_them(store, [make_route()])
    assert outcome.receipts[0].outcome == "failed"
    assert "policy-that-does-not-exist" in outcome.receipts[0].reason


def test_the_writer_cannot_be_handed_the_recipient_or_the_send_state(store):
    """§5 逐字：「写作者不得修改收件人、发件人、权限、抑制或发送状态」。

    那一条的落地方式不是「写作者要自觉」，而是 :class:`DraftRequest` **根本没
    有这些字段** —— 于是「顺手把收件人改一下」在类型层就写不出来。
    """

    forbidden = {"recipient", "sendbox", "from_address", "status", "send_authorized", "email"}
    assert forbidden & set(DraftRequest.__dataclass_fields__) == set()


# --- 三之二、B13：起草是一个**独立用例** ---------------------------------------


def _one_of_each_grade() -> list[ContactRoute]:
    """A/B/C/D 各一条，**角色路线分成两族**，附一条 X。

    ## 为什么「A/B 一族、C/D 一族」不是装饰

    ``role_class`` 是 ``family_for`` 第一层逐字比的那一格。四条都写成同一条角色
    路线时，C/D 会掉进第二层而被 ``FALLBACK_TO_STAGE_FOR_GRADES`` 挡下 —— 那时
    「C/D 没有稿子」与「C/D 有稿子但用的是关键联系人那一族」两种实现在这条夹具上
    同形。分成两族之后，下面那条判据才能问出「C/D 的稿子用的是**哪一族**」。
    """
    return [
        make_route(
            account_id="account-a",
            email="a@example.co.uk",
            grade=Grade.A,
            entry_kind=EntryKind.NAMED_ROLE,
            role_class=RoleClass.DECISION,
        ),
        make_route(
            account_id="account-b",
            email="b@example.co.uk",
            grade=Grade.B,
            entry_kind=EntryKind.NAMED_ROLE,
            role_class=RoleClass.DECISION,
        ),
        make_route(
            account_id="account-c",
            email="c@example.co.uk",
            grade=Grade.C,
            entry_kind=EntryKind.OFFICIAL_BUSINESS_ENTRY,
            role_class=RoleClass.OPERATIONAL,
        ),
        make_route(
            account_id="account-d",
            email="d@example.co.uk",
            grade=Grade.D,
            entry_kind=EntryKind.GENERIC_OFFICIAL,
            role_class=RoleClass.OPERATIONAL,
        ),
    ]


def test_a_b_c_d_each_get_a_draft_written_for_their_own_route(store):
    """§B13 关闭条件：「已知合格 A/B/C/D fixture **各有合适草稿**」。

    ## 「合适」由两件事一起构成

    1. **有四份稿子**，四种等级各一条 —— 少了哪一级，报告上只会显示「这一批小了
       一点」；
    2. **C/D 用的不是 A/B 那一族**。§8 逐字：「C/D 草稿需**明确是部门/通用入口，
       不冒充具名决策人**」。C/D 蹭到关键联系人那一族时，第 1 条照样成立 ——
       所以这一条判据必须问出 ``family_id``，不能只数草稿条数。
    """
    seed(store, role_class=RoleClass.DECISION.value)
    seed_another_family(
        store, family_id="family-uk-grass-stage-1-generic", role_class=RoleClass.OPERATIONAL.value
    )
    routes = _one_of_each_grade()

    plan = run_drafting(store, routes=routes)
    assert [target.recipient for target in plan.targets] == [
        "a@example.co.uk",
        "b@example.co.uk",
        "c@example.co.uk",
        "d@example.co.uk",
    ]
    # 四条都是按**角色路线**挑中的，没有一条落在「本阶段那一族」的兜底上 ——
    # 兜底本身不是错，但落在那里的 C/D 就是「没有一族是为它们写的」。
    assert {target.family_match for target in plan.targets} == {"role_class"}

    by_recipient = {target.recipient: target for target in plan.targets}
    key_contact_family = by_recipient["a@example.co.uk"].family_id
    general_family = by_recipient["c@example.co.uk"].family_id
    assert key_contact_family == FAMILY
    assert general_family != key_contact_family, "C/D 不得套用写给具名决策人的那一族"
    assert by_recipient["d@example.co.uk"].family_id == general_family

    outcome = draft_them(store, routes=routes)
    assert [receipt.outcome for receipt in outcome.receipts] == ["generated"] * 4
    assert {draft.state for draft in outcome.drafts} == {DraftState.GENERATED}

    # 入口性质与等级是**起草时冻结**在草稿上的两格（``OutreachDraft.entry_kind`` /
    # ``route_grade``）。回查路线会让同一份稿子在两次读之间声称两个等级。
    stored = {draft.account_id: draft for draft in store.latest_drafts(wave_id=WAVE)}
    assert stored["account-c"].entry_kind is EntryKind.OFFICIAL_BUSINESS_ENTRY
    assert stored["account-c"].route_grade is Grade.C
    assert stored["account-d"].entry_kind is EntryKind.GENERIC_OFFICIAL
    assert stored["account-d"].route_grade is Grade.D
    assert stored["account-a"].entry_kind is EntryKind.NAMED_ROLE


def test_x_is_not_drafted_and_the_reason_names_the_rule_that_stopped_it(store):
    """§8 逐字：「**X 不进入正常草稿**」。而「不进入」要说得出**是哪一步拦的**。

    两条 X 走的是**两条不同的判据**，理由必须不同：一条是入口性质（不相关岗位 /
    无场地绑定的泛化入口），一条是有具名角色却没判出合格证据。把它们合成一句
    「不适合联系」的实现，在报告上分不出「规则说它不该联系」与「我们没判出来」——
    而后者是一个要去补研究的状态。
    """
    seed(store)
    excluded_entry = make_route(
        account_id="account-x1",
        email="x1@example.co.uk",
        grade=Grade.X,
        entry_kind=EntryKind.EXCLUDED,
    )
    unjudged = make_route(account_id="account-x2", email="x2@example.co.uk", grade=Grade.X)
    assert unjudged.entry_kind is EntryKind.NAMED_ROLE, "第二条要走的是**等级**那一条判据"

    plan = run_drafting(store, routes=[excluded_entry, unjudged])
    assert plan.targets == ()
    assert [(row.recipient, row.reason) for row in plan.skipped] == [
        ("x1@example.co.uk", "入口性质不是可起草的那三种：excluded"),
        ("x2@example.co.uk", "等级是 X：未判定或不该联系，不产正常草稿"),
    ]

    outcome = draft_them(store, routes=[excluded_entry, unjudged])
    assert outcome.receipts == ()
    assert outcome.drafts == ()
    assert store.latest_drafts(wave_id=WAVE) == ()


def test_a_route_that_already_left_the_intake_still_gets_its_draft(store):
    """§8 逐字：草稿准备「**不要求** SMTP、manifest 授权或**已发送前序**」。

    判据是一条**调用链**事实，不是一句措辞：同一条 ``sent_stage_1`` 的路线，
    ``plan_wave`` 不收它（那是重发保护），而 ``plan_drafting`` 收它 —— 第二段的信
    照样写得出来。两处读的是同一批路线，结论不同，而两个结论各自都对。
    """
    seed(store)
    left_the_intake = make_route().model_copy(update={"status": RouteStatus.SENT_STAGE_1})

    assert run_plan(store, routes=[left_the_intake]).entries == ()
    assert [target.recipient for target in run_drafting(store, routes=[left_the_intake]).targets] == [
        "grounds@example.co.uk"
    ]

    outcome = draft_them(store, routes=[left_the_intake])
    assert [receipt.outcome for receipt in outcome.receipts] == ["generated"]
    assert store.latest_drafts(wave_id=WAVE) != ()
    # 起草这一趟**不产生**发送侧的任何东西：没有清单，没有授权。
    assert store.wave(WAVE) is None
    assert store.authorizations(wave_id=WAVE) == ()


def test_drafting_has_no_parameter_that_could_send_anything(store):
    """「不要求 SMTP」在**类型层**也成立，而不仅是在这条用例里没接传输。

    这一条判的是签名：两个入口都**收不下**传输、清单或授权。靠「本用例没传」
    是弱判据 —— 一个给 ``generate_drafts`` 加了 ``transport=None`` 缺省参数的
    实现会在这里绿着，而它在另一个调用点上就能真的发信。
    """
    forbidden = {
        "transport",
        "mailer",
        "smtp",
        "manifest",
        "authorization",
        "wave_authorization",
        "sendbox",
    }
    for function in (plan_drafting, generate_drafts):
        assert not set(inspect.signature(function).parameters) & forbidden, function.__name__


def test_a_route_the_binding_never_judged_is_review_only_not_sendable(store):
    """§8 逐字：「准入、当前性或**绑定**不够时只给明确标注的 review-only/held 状态」。

    两半都要：草稿**写得出**（review-only 不是「不发」，它是一封写好了、等人去看
    的信），而它**不进清单**（``prepare_wave`` 只收 ``GENERATED``）。

    这条路线只能用构造器直传造出来：生产里 ``domain/bind.py`` 根本不产出
    ``binding=NONE`` —— 本该是 NONE 的候选在那一层就被 ``UNBOUND_EMAIL`` 拒收了。
    所以一条**存着的** ``NONE`` 意思是「它的绑定从来没有人判过」（见
    :func:`~marketops.application.outreach.review_reasons` 的 docstring）。
    """
    seed(store)
    unrouted = make_route(binding=RouteBinding.NONE)

    plan = run_drafting(store, routes=[unrouted])
    assert [target.review_only for target in plan.targets] == [True]
    assert "绑定不够" in plan.targets[0].review_reasons[0]

    outcome = draft_them(store, routes=[unrouted])
    assert [receipt.outcome for receipt in outcome.receipts] == ["review_only"]
    assert "绑定不够" in outcome.receipts[0].reason
    stored = store.draft(outcome.drafts[0].draft_id, 1)
    assert stored is not None and stored.state is DraftState.REVIEW_ONLY

    assert freeze(store, [unrouted]).entries == ()


def test_a_row_nobody_judged_the_freshness_of_is_review_only(store):
    """另一档「不够」：**当前性**。而它与绑定那一档的理由**不是同一句**。

    ``freshness is None`` 是「还没判过」（``ContactRoute.freshness`` 的 docstring
    逐字如此），不是「判过、还行」。本仓对未确认的一贯读法是「未确认」。
    一条没有时刻锚点的路线走的是同一条路（``assertion_for`` 返回 ``None``）。
    """
    seed(store)
    unjudged = make_route(account_id="account-0002", email="pro@example.co.uk", first_seen_at=None)
    assert unjudged.freshness is None, "没有时刻锚点 = 判据没能开工，那一格留空"
    assert unjudged.binding is RouteBinding.STRONG, "这一条要测的是当前性那一档，不是绑定"

    plan = run_drafting(store, routes=[unjudged])
    assert [target.review_reasons for target in plan.targets] == [
        ("当前性未确认：这条路线还没有判过时效（freshness 为空）",)
    ]
    outcome = draft_them(store, routes=[unjudged])
    assert [receipt.outcome for receipt in outcome.receipts] == ["review_only"]
    assert freeze(store, [unjudged]).entries == ()


def test_the_business_receipt_accounts_for_the_frozen_target_set(store):
    """§B13 逐字：「以**冻结对象集**对账 ``accepted / held / excluded / failed /
    reused`` 及**下一层写入**」。

    ## 六格是一个划分，而这一点是可执行的

    ``excluded`` 是计划**之外**那一侧（被筛选挡下的），其余五格是计划里的每一条
    目标各占其一。所以「总数对得上」这句话有两个含义，两个都要查：计划里的每一条
    在回执里**恰好出现一次**，而回执里不出现计划外的人。
    """
    seed(store)
    routes = [
        make_route(account_id="account-0001", email="a@example.co.uk"),
        make_route(account_id="account-0002", email="b@example.co.uk"),
        make_route(account_id="account-0003", email="c@example.co.uk"),
        # 第四条在**筛选**那一层就被挡下：它进不了计划，所以它在回执里只有一句话
        # 的理由，没有档位。
        #
        # 它只能这样造。``ContactRoute`` 自己钉着「非 X 的等级必须挂证据」，所以
        # 「等级不是 X 而 ``evidence_ids`` 为空」这一格**正常写路径写不出来**
        # （``rebuild``／``model_validate`` 一律拦下）—— 与
        # :func:`test_a_route_with_no_evidence_is_not_planned` 同一件事、同一手法。
        # 也就是说筛选里那一条守的是「库里的行被绕过校验写进来」，不是「上游忘了填」。
        # §B13 那句「不能仅检查非空/hash」的承载点**不在这里**：``plan_drafting``
        # 手上没有证据库，它能问的只有「有没有挂」。真的去向证据库核对
        # 「这几条 id 在不在、是不是这个账号的」是 MCP 那一层的事。
        make_route(account_id="account-0004", email="d@example.co.uk").model_copy(
            update={"evidence_ids": []}
        ),
    ]
    writer = MixedWriter(fail_on="account-0003", hold_on="account-0002")
    plan = run_drafting(store, routes=routes)
    outcome = generate_drafts(
        plan=plan,
        store=store,
        writer=writer,
        campaign_id=CAMPAIGN,
        locale="en-GB",
        facts_by_account={},
        organisation_by_account={},
        receipts_dir=_receipts_dir(),
        at=T0,
    )

    receipt = drafting_receipt(plan, outcome)
    assert receipt.counts == {
        "accepted": 1,
        "review_only": 0,
        "held": 1,
        "excluded": 1,
        "failed": 1,
        "reused": 0,
    }
    assert len(plan.targets) == 3, "计划里有三条目标，第四条在筛选那一层就被挡下"
    assert receipt.rows_written == outcome.rows_written
    assert set(receipt.targets) == {target.recipient for target in plan.targets}
    assert [reason for _, reason in receipt.excluded] == ["这条路线没有证据"]
    # 逐条**指名**，而不只是数条数：一个把三格的顺序写反的实现，条数照样对得上。
    assert dict((recipient, disposition) for recipient, disposition, _ in receipt.rows) == {
        "a@example.co.uk": "accepted",
        "b@example.co.uk": "held",
        "c@example.co.uk": "failed",
    }
    assert receipt.summary_line.startswith("accepted=1 review_only=0 held=1")
    # ``rows_written`` 是「**及下一层写入**」，而它**不等于** ``len(drafts)``：
    # 无正文的 hold 没有行可写，却照样是一条有结局的目标。
    assert receipt.rows_written == 1 < len(plan.targets)


def test_re_running_the_batch_is_reused_not_a_second_write(store):
    """重跑一遍：六格里的 ``reused`` 是**这一格**在两次运行之间稳定的证据。

    没有它，「第二趟把每一格都清空了」与「第二趟什么都没发生」在读数上一样 ——
    而第二趟真的会来（一个批次被授权之前会被重算很多次）。
    """
    seed(store)
    routes = [make_route(account_id="account-0001", email="a@example.co.uk")]
    first = drafting_receipt(
        run_drafting(store, routes=routes), draft_them(store, routes)
    )
    assert first.counts["accepted"] == 1 and first.rows_written == 1

    second_plan = run_drafting(store, routes=routes)
    second = drafting_receipt(second_plan, draft_them(store, routes))
    assert second.counts == {
        "accepted": 0,
        "review_only": 0,
        "held": 0,
        "excluded": 0,
        "failed": 0,
        "reused": 1,
    }
    assert second.rows_written == 0, "输入逐字没变就不该再写一版（§5：「只给输入变了的生成」）"


def test_a_receipt_that_cannot_be_reconciled_is_refused_not_padded(store):
    """对不上就抛，**不补一行**。两个方向各测一次。

    补一行的实现在报告上与本函数产出的**一模一样**，而两者回答的问题不同：这份
    回执的全部意义是「每一条都有话说」。一个在漏报时自己造一条的实现让这句话永远
    为真，于是它再也测不出来。
    """
    seed(store)
    routes = [make_route(account_id="account-0001", email="a@example.co.uk")]
    plan = run_drafting(store, routes=routes)
    outcome = draft_them(store, routes)

    with pytest.raises(ConfigError, match="没有任何生成回执"):
        drafting_receipt(plan, DraftingOutcome(receipts=()))

    stranger = replace(outcome.receipts[0], draft_id="draft-someone-else")
    with pytest.raises(ConfigError, match="不在这一份计划的目标集里"):
        drafting_receipt(plan, DraftingOutcome(receipts=(stranger,), drafts=()))
# --- 四、冻结 -----------------------------------------------------------------


def test_a_candidate_without_a_draft_is_excluded_not_dropped(store):
    """缺稿的候选按「这一批少发一条」处置，而不是让整批发不出去。"""
    seed(store)
    routes = [
        make_route(account_id="account-0001", email="a@example.co.uk"),
        make_route(account_id="account-0002", email="b@example.co.uk"),
    ]
    outcome = draft_them(store, routes, writer=BrokenWriter(fail_on="account-0002"))
    assert len(outcome.drafts) == 1

    manifest = freeze(store, routes)
    assert len(manifest.entries) == 1
    assert manifest.allowed_count == 1
    assert [reason for _, reason in manifest.excluded] == ["这一条没有生成出任何一稿"]


def test_freezing_stops_at_prepared_pending_authorization(store):
    """§6 逐字：「**默认停在** ``prepared_pending_authorization``」。"""
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    manifest = freeze(store, routes)
    wave = store.wave(manifest.wave_id)
    assert wave.state is WaveState.PREPARED_PENDING_AUTHORIZATION
    assert wave.manifest_sha256 == manifest.manifest_sha256


def test_the_manifest_order_is_the_delivery_send_key_order(store):
    """授权取的是前 N 行，所以「第几行」必须与投递时看到的是同一个序。"""
    seed(store)
    routes = [
        make_route(account_id=f"account-{index:04d}", email=f"{index}@example.co.uk")
        for index in range(1, 5)
    ]
    draft_them(store, routes)
    manifest = freeze(store, routes)
    expected = sorted(
        send_key_for(
            workspace=WORKSPACE,
            wave_id=manifest.wave_id,
            draft_id=entry.draft.draft_id,
            draft_version=entry.draft.draft_version,
            recipient=entry.recipient,
        )
        for entry in manifest.entries
    )
    actual = [
        send_key_for(
            workspace=WORKSPACE,
            wave_id=manifest.wave_id,
            draft_id=entry.draft.draft_id,
            draft_version=entry.draft.draft_version,
            recipient=entry.recipient,
        )
        for entry in manifest.entries
    ]
    assert actual == expected


# --- 五、授权 -----------------------------------------------------------------


def test_an_authorization_that_covers_another_manifest_is_refused(store):
    """一份覆盖不了的授权**不得**登记 —— 登记了它在盘上就与有效的一样。

    这里比的是**重新算出来的**清单摘要：篡改授权记录里的那个值挡不住
    「有人改了清单」，而后者才是这个判据存在的全部理由。
    """
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    manifest = freeze(store, routes)

    tampered = WaveAuthorization(
        authorization_id="auth-0001",
        wave_id=manifest.wave_id,
        workspace=manifest.workspace,
        scene_id=manifest.scene_id,
        manifest_sha256=sha256_text("别的清单"),
        authorized_by="operator:member-a",
        authority_ref="approval-ticket-0001",
        authorized_at=T0,
        expires_at=T2,
        approved_count=1,
    )
    with pytest.raises(MarketOpsError) as caught:
        record_authorization(authorization=tampered, store=store, at=T0)
    assert "不一致" in str(caught.value)
    assert store.authorizations(wave_id=manifest.wave_id) == ()


def test_an_authorization_without_a_manifest_is_refused(store):
    orphan = WaveAuthorization(
        authorization_id="auth-0001",
        wave_id=WAVE,
        workspace=WORKSPACE,
        scene_id=SCENE,
        manifest_sha256=sha256_text("x"),
        authorized_by="operator:member-a",
        authority_ref="ticket-1",
        authorized_at=T0,
        expires_at=T2,
        approved_count=0,
    )
    with pytest.raises(MarketOpsError) as caught:
        record_authorization(authorization=orphan, store=store, at=T0)
    assert "没有清单" in str(caught.value)


def test_the_wave_does_not_move_to_authorized_without_the_record(store):
    """``AUTHORIZED`` 是一份授权存在这件事的**投影**，不是「有人点了头」。"""
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    freeze(store, routes)
    with pytest.raises(MarketOpsError) as caught:
        mark_authorized(wave_id=WAVE, authorization_id="auth-never-recorded", store=store, at=T0)
    assert "没有一份此刻有效的授权" in str(caught.value)
    assert store.wave(WAVE).state is WaveState.PREPARED_PENDING_AUTHORIZATION


# --- 六、投递前的八道 ----------------------------------------------------------


def test_a_paused_scene_sends_nothing(store, scenes):
    """§4 逐字：「暂停/归档停止新的研究或外联派发」。

    暂停走的是**真的那个用例**（``application.scenes.pause``），不是手工改一行
    记录 —— 手工写回会跳过 revision 自增，而存储拒收正是为了这个。
    """
    _, manifest = a_sendable_wave(store, scenes)
    pause(store=scenes, scene_id=SCENE, at=T1, reason=PauseReason.OPERATOR)

    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport)
    assert transport.sent == []
    assert report.outcomes[0].state == "skipped"
    assert "Scene 不在运行中" in report.outcomes[0].detail
    assert store.attempts(wave_id=manifest.wave_id) == ()


def test_the_scene_is_read_from_the_store_not_from_the_caller(store, scenes):
    """库里的状态才是判据。调用方手里那份**读的时候**是对的，这不算数。"""
    _, manifest = a_sendable_wave(store, scenes)
    stale = make_scene()  # 调用方手上那份：它读到的时候真的是 ACTIVE
    assert stale.state is SceneState.ACTIVE
    pause(store=scenes, scene_id=SCENE, at=T1, reason=PauseReason.OPERATOR)

    report = do_dispatch(store, scenes, RecordingTransport())
    assert "Scene 不在运行中" in report.outcomes[0].detail
    assert store.attempts(wave_id=manifest.wave_id) == ()


def test_an_unauthorized_wave_sends_nothing(store, scenes):
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    manifest = freeze(store, routes)

    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport)
    assert transport.sent == []
    assert "不是已授权" in report.outcomes[0].detail
    assert store.attempts(wave_id=manifest.wave_id) == ()


def test_a_lapsed_authorization_sends_nothing(store, scenes):
    """§6 逐字：「内容发生任何受控改动都使上一次授权失效」。"""
    _, manifest = a_sendable_wave(store, scenes)
    lapse_authorization(wave_id=manifest.wave_id, reason="正文改了一个字", store=store, at=T1)

    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport)
    assert transport.sent == []
    assert "不是已授权" in report.outcomes[0].detail


def test_a_sendbox_that_is_not_the_manifest_one_sends_nothing(store, scenes):
    """不是某一封的问题，是这一整个部署的问题 —— 所以它一封都不发。"""
    _, manifest = a_sendable_wave(store, scenes)
    transport = RecordingTransport(sender_identity="someone-else@marketops.example")
    report = do_dispatch(store, scenes, transport)
    assert transport.sent == []
    assert "不在清单上" in report.outcomes[0].detail
    assert store.attempts(wave_id=manifest.wave_id) == ()


def test_outside_the_window_nothing_is_sent(store, scenes):
    """窗口**还没开**也一封不发 —— 「早」与「晚」是同一道门的两侧。

    授权在 T2 之前一直有效，所以停在 T1 的这一发**不是**被授权守卫挡下的：要
    让这条判据测的是窗口，就得让窗口关着而授权开着。
    """
    _, manifest = a_sendable_wave(store, scenes, window_start=T1 + timedelta(hours=1))
    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport, at=T1)
    assert transport.sent == []
    assert "不在" in report.outcomes[0].detail
    assert store.attempts(wave_id=manifest.wave_id) == ()

    # 同一批货，窗口开了就发得出去 —— 否则上面那条也可能只是「这一批本来就发不出去」。
    opened = do_dispatch(store, scenes, transport, at=T1 + timedelta(hours=2))
    assert opened.outcomes[0].state == "sent"


def test_the_approved_count_is_what_bounds_the_batch(store, scenes):
    """「批准 10 条、清单 20 行」是常态，而范围取的是前 ``approved_count`` 行。"""
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [
        make_route(account_id=f"account-{index:04d}", email=f"{index}@example.co.uk")
        for index in range(1, 4)
    ]
    draft_them(store, routes)
    manifest = freeze(store, routes)
    assert len(manifest.entries) == 3
    authorise(store, manifest, approved_count=2)

    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport)
    assert len(transport.sent) == 2
    assert len(report.outcomes) == 2


def test_the_body_sent_is_the_body_in_the_store_not_one_handed_in(store, scenes):
    """传输拿到的正文是从库里读出来的**那一份**，与清单摘要逐字相符。"""
    _, manifest = a_sendable_wave(store, scenes, bodies={"account-0001": "正文甲"})
    transport = RecordingTransport()
    do_dispatch(store, scenes, transport)
    assert [row["body"] for row in transport.sent] == ["正文甲"]
    assert transport.sent[0]["from"] == SENDBOX
    entry = manifest.entries[0]
    assert sha256_text(transport.sent[0]["body"]) == entry.draft.body_sha256


def test_a_body_changed_in_the_store_is_not_sent(store, scenes):
    """清单摘要存在的**唯一**理由就是这一条：有人改了库里的正文。

    这一格**只能**绕过 ``put_draft`` 造出来：它对「同版次不同内容」报错，而那
    正是它该做的。要模拟的是绕过它的那一类改动 —— 手工 SQL、另一台机器上的
    一次迁移、一个直接对着库跑的老脚本。所以下面直接写存储，并明确说这是
    「有人直接改了库」，不是「应用改了一稿」。
    """
    _, manifest = a_sendable_wave(store, scenes)
    entry = manifest.entries[0]
    stored = store.draft(entry.draft.draft_id, entry.draft.draft_version)
    assert stored is not None
    _force_body_into_the_store(store, stored, "换过的正文")

    stored_after = store.draft(entry.draft.draft_id, entry.draft.draft_version)
    assert stored_after.body == "换过的正文"  # 篡改真的生效了，不是被静默拒绝

    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport)
    assert transport.sent == []
    assert "内容被改过" in report.outcomes[0].detail


def _force_body_into_the_store(store, draft, body: str) -> None:
    """把一行草稿的正文直接改成 ``body``，**不经过** ``put_draft``。

    这一条是**由测试本身**承认的违规动作：它只在内存字典或 SQL 里写，两种后
    端各走各的那一处。把它写成一个绕不过去的接口，就等于给应用开了一条它本来
    没有的改稿通道。
    """
    doctored = draft.model_copy(update={"body": body})
    if isinstance(store, InMemoryOutreachStore):
        store._drafts[(draft.draft_id, draft.draft_version)] = doctored
        return

    from marketops.adapters.sqlite.db import session, write_transaction
    from marketops.adapters.sqlite.payload import dump_payload

    with session(store._path) as conn, write_transaction(conn):
        conn.execute(
            "UPDATE outreach_draft SET payload = ? WHERE draft_id = ? AND draft_version = ?",
            (
                dump_payload(doctored.model_dump(mode="json")),
                draft.draft_id,
                draft.draft_version,
            ),
        )


def test_a_suppressed_recipient_is_skipped_at_delivery_time(store, scenes):
    """抑制在**投递那一刻**再查一次 —— 冻结之后到发送之间他可能刚退订。"""
    _, manifest = a_sendable_wave(store, scenes)
    store.put_suppression(
        RecipientSuppression(
            recipient=manifest.entries[0].recipient,
            reason=SuppressionReason.UNSUBSCRIBE,
            source_ref="event-0002",
            suppressed_at=T0 + timedelta(minutes=30),
        )
    )
    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport)
    assert transport.sent == []
    assert report.outcomes[0].detail == "收件人在抑制名单上"


# --- 七、投递的结局 ------------------------------------------------------------


def test_an_accepted_delivery_is_recorded_and_reaches_the_transport(store, scenes):
    _, manifest = a_sendable_wave(store, scenes)
    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport)

    assert len(transport.sent) == 1
    assert report.outcomes[0].state == "sent"
    assert report.outcomes[0].reached_the_transport
    attempt = store.attempts(wave_id=manifest.wave_id)[0]
    assert attempt.state is MessageState.ACCEPTED_BY_PROVIDER
    assert attempt.provider_message_id.startswith("<mid-")
    assert attempt.authorization_id == "auth-0001"


def test_a_refused_delivery_is_failed_and_may_be_retried(store, scenes):
    """``refused`` 与 ``unknown`` 分开的唯一理由是**处置相反**。"""
    _, manifest = a_sendable_wave(store, scenes)
    transport = RecordingTransport(outcomes={"grounds@example.co.uk": "refused"})
    report = do_dispatch(store, scenes, transport)
    assert report.outcomes[0].state == "refused"
    assert store.attempts(wave_id=manifest.wave_id)[0].state is MessageState.FAILED


def test_a_transport_that_raises_is_unknown_and_is_never_sent_twice(store, scenes):
    """**这个文件里最重要的一条。**

    「提供方可能收了」被当成「没发出去」而重发，就是有人收到两封。所以第一次
    抛之后落 ``DELIVERY_UNKNOWN``，第二次派发**不得**再把它交给传输。
    """
    _, manifest = a_sendable_wave(store, scenes)
    transport = RecordingTransport(raise_on="grounds@example.co.uk")
    first = do_dispatch(store, scenes, transport)
    assert first.outcomes[0].state == "unknown"
    assert transport.sent == []
    assert store.attempts(wave_id=manifest.wave_id)[0].state is MessageState.DELIVERY_UNKNOWN

    second = do_dispatch(store, scenes, transport, at=T1 + timedelta(minutes=1))
    assert second.outcomes[0].state == "duplicate"
    assert transport.sent == []


def test_two_drivers_on_the_same_wave_do_not_double_send(store, scenes):
    """两个驱动同时进来：只有一个 ``won``，另一个是 ``duplicate`` 而**不是**失败。"""
    _, manifest = a_sendable_wave(store, scenes)
    # 手工把这一批开成「正在发」的那一格，模拟另一个驱动已经开出去了。
    entry = manifest.entries[0]
    from marketops.domain.outreach import DraftRef, OutboundAttempt

    key = send_key_for(
        workspace=WORKSPACE,
        wave_id=manifest.wave_id,
        draft_id=entry.draft.draft_id,
        draft_version=entry.draft.draft_version,
        recipient=entry.recipient,
    )
    store.begin_attempt(
        OutboundAttempt(
            send_key=key,
            workspace=WORKSPACE,
            wave_id=manifest.wave_id,
            draft_id=entry.draft.draft_id,
            draft_version=entry.draft.draft_version,
            draft=DraftRef(
                draft_id=entry.draft.draft_id,
                draft_version=entry.draft.draft_version,
                body_sha256=entry.draft.body_sha256,
            ),
            route_ref=entry.route_ref,
            account_id=entry.account_id,
            recipient=entry.recipient,
            sendbox=entry.sendbox,
            stage=entry.stage,
            authorization_id="auth-0001",
            manifest_sha256=manifest.manifest_sha256,
            started_at=T1,
        ),
        at=T1,
    )
    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport, at=T1 + timedelta(minutes=1))
    assert transport.sent == []
    assert report.outcomes[0].state == "duplicate"
    assert "不得重复发" in report.outcomes[0].detail


# --- 八、第 2–4 段 -------------------------------------------------------------


def _entry_for(store, scenes, *, stage, wave_id, recipient, **overrides):
    """造一条清单项，只为了单独测 :func:`check_prior_stages`。"""
    from marketops.domain.outreach import DraftRef, ManifestEntry

    return ManifestEntry(
        draft=DraftRef(draft_id="draft-x", draft_version=1, body_sha256=sha256_text("b")),
        route_ref="account-0001",
        account_id="account-0001",
        recipient=recipient,
        sendbox=SENDBOX,
        stage=stage,
        family_id=FAMILY,
        writing_policy_id=POLICY,
        evidence_ids=("evidence-0001",),
    )


def _record_attempt(store, *, stage, recipient, state, at, wave_id=WAVE, key=None):
    from marketops.domain.outreach import DraftRef, OutboundAttempt

    key = key or f"send-{stage.value}-{recipient}".replace("@", "-").replace(".", "-")
    store.begin_attempt(
        OutboundAttempt(
            send_key=key,
            workspace=WORKSPACE,
            wave_id=wave_id,
            draft_id="draft-x",
            draft_version=1,
            draft=DraftRef(draft_id="draft-x", draft_version=1, body_sha256=sha256_text("b")),
            route_ref="account-0001",
            account_id="account-0001",
            recipient=recipient,
            sendbox=SENDBOX,
            stage=stage,
            authorization_id="auth-0001",
            manifest_sha256=sha256_text("manifest"),
            started_at=at,
        ),
        at=at,
    )
    row = store.attempt(key)
    # ``FAILED`` **必须**写明原因（``OutboundAttempt`` 拒收没有理由的失败）。
    # 这一格不是样板文字：事后读的人要能分辨「对端拒收」与「我们这边坏了」。
    failure = "550 邮箱不存在" if state is MessageState.FAILED else ""
    store.finish_attempt(
        row.model_copy(update={"state": state, "finished_at": at, "failure": failure})
    )


def test_the_first_touch_has_no_prior_condition(store, scenes):
    """把它写成「要求前一阶段存在」会让**第一封信永远发不出去**，
    而症状是「这一批没人可发」。"""
    entry = _entry_for(store, scenes, stage=OutreachStage.STAGE_1, wave_id=WAVE, recipient="a@example.co.uk")
    assert check_prior_stages(entry=entry, store=store, rules=make_rules(), at=T1) == ""


def test_a_second_touch_needs_a_confirmed_first_one(store, scenes):
    """「我们提交给服务器了」不等于「对方收到了」。"""
    entry = _entry_for(store, scenes, stage=OutreachStage.STAGE_2, wave_id=WAVE, recipient="a@example.co.uk")
    _record_attempt(
        store,
        stage=OutreachStage.STAGE_1,
        recipient="a@example.co.uk",
        state=MessageState.ACCEPTED_BY_PROVIDER,
        at=T0,
    )
    reason = check_prior_stages(entry=entry, store=store, rules=make_rules(), at=T1)
    assert "已经被确认" in reason


def test_a_second_touch_before_the_wait_is_refused(store, scenes):
    entry = _entry_for(store, scenes, stage=OutreachStage.STAGE_2, wave_id=WAVE, recipient="a@example.co.uk")
    _record_attempt(
        store,
        stage=OutreachStage.STAGE_1,
        recipient="a@example.co.uk",
        state=MessageState.DELIVERED,
        at=T0,
    )
    rules = make_rules(waits={**{s: timedelta(days=2) for s in OutreachStage}})
    assert "还不到第 2 段的等待期" in check_prior_stages(
        entry=entry, store=store, rules=rules, at=T0 + timedelta(hours=1)
    )
    assert check_prior_stages(entry=entry, store=store, rules=rules, at=T2) == ""


def test_a_second_touch_beyond_the_configured_touch_budget_is_refused(store, scenes):
    entry = _entry_for(store, scenes, stage=OutreachStage.STAGE_2, wave_id=WAVE, recipient="a@example.co.uk")
    for index in range(2):
        _record_attempt(
            store,
            stage=OutreachStage.STAGE_1,
            recipient="a@example.co.uk",
            state=MessageState.DELIVERED,
            at=T0,
            key=f"send-prior-{index}",
        )
    rules = make_rules(waits={s: timedelta(0) for s in OutreachStage}, priors={s: 1 for s in OutreachStage})
    reason = check_prior_stages(entry=entry, store=store, rules=rules, at=T2)
    assert "超过配置的 1" in reason


def test_a_first_touch_that_was_never_delivered_does_not_invite_a_second(store, scenes):
    """上一段被拒（``FAILED``）不算确认 —— 不能对一个从来没收到第一封信的人
    继续打扰。"""
    entry = _entry_for(store, scenes, stage=OutreachStage.STAGE_2, wave_id=WAVE, recipient="a@example.co.uk")
    _record_attempt(
        store,
        stage=OutreachStage.STAGE_1,
        recipient="a@example.co.uk",
        state=MessageState.FAILED,
        at=T0,
    )
    assert "已经被确认" in check_prior_stages(entry=entry, store=store, rules=make_rules(), at=T2)


# --- 九、对账 -----------------------------------------------------------------


def test_reconciliation_settles_an_unknown_and_leaves_the_unaskable_alone(store, scenes):
    """问不到**不代表没发** —— 把它们写成 ``FAILED`` 就是「可以重试」，
    而那是有人收到两封的另一条成因。"""
    _, manifest = a_sendable_wave(store, scenes)
    transport = RecordingTransport(raise_on="grounds@example.co.uk")
    do_dispatch(store, scenes, transport)
    attempt = store.attempts(wave_id=manifest.wave_id)[0]
    assert attempt.state is MessageState.DELIVERY_UNKNOWN
    assert attempt.provider_message_id == ""

    outcomes = reconcile_unknown(
        wave_id=manifest.wave_id, store=store, transport=transport, at=T1
    )
    assert [row.state for row in outcomes] == ["still_unknown"]
    assert "无法按标识问" in outcomes[0].detail
    assert transport.asked == []  # 没有标识 → 不问，更不按地址问
    assert store.attempts(wave_id=manifest.wave_id)[0].state is MessageState.DELIVERY_UNKNOWN


def test_reconciliation_writes_a_settled_answer(store, scenes):
    """拿到了标识的那些：问一次，然后把结局**写定**。

    标识是这一格的入口条件 —— 没有它就只能停在 ``still_unknown``（上一条）。
    所以它必须是在**投递那一刻**记下的，事后补一个标识是补不上去的：
    ``finish_attempt`` 对已经是终态的行是幂等的。
    """
    _, manifest = a_sendable_wave(store, scenes)
    transport = RecordingTransport(
        outcomes={"grounds@example.co.uk": "unknown"},
        provider_ids={"grounds@example.co.uk": "<mid-known@x>"},
    )
    do_dispatch(store, scenes, transport)
    unknown = store.attempts(wave_id=manifest.wave_id)[0]
    assert unknown.state is MessageState.DELIVERY_UNKNOWN
    assert unknown.provider_message_id == "<mid-known@x>"
    assert transport.sent  # 到达了传输 —— 所以这一封「可能出去了」

    asking = RecordingTransport(status_answer="delivered")
    outcomes = reconcile_unknown(wave_id=manifest.wave_id, store=store, transport=asking, at=T1)
    assert [row.state for row in outcomes] == ["settled"]
    assert asking.asked == ["<mid-known@x>"]
    assert store.attempts(wave_id=manifest.wave_id)[0].state is MessageState.ACCEPTED_BY_PROVIDER


# --- 十、停发 -----------------------------------------------------------------


def test_a_reply_stops_everything_still_pending_for_that_person(store, scenes):
    """§7 逐字：「新到的有效人类回信应**先保守地暂停后续外联**，然后再分类」。

    「先」在这里是可执行的：这个函数不产出交接、不读回信正文，所以它能在分类
    还没跑、甚至分类失败的时候单独发生。
    """
    _, manifest = a_sendable_wave(store, scenes)
    first = do_dispatch(store, scenes, RecordingTransport())
    assert first.outcomes[0].state == "sent"

    entry = manifest.entries[0]
    key = send_key_for(
        workspace=WORKSPACE,
        wave_id=manifest.wave_id,
        draft_id=entry.draft.draft_id,
        draft_version=entry.draft.draft_version,
        recipient=entry.recipient,
    )
    # 同一个人的下一次触达已经开出去了但还没结算。
    from marketops.domain.outreach import DraftRef, OutboundAttempt

    pending = OutboundAttempt(
        send_key="send-pending-0001",
        workspace=WORKSPACE,
        wave_id="wave-0002",
        draft_id="draft-y",
        draft_version=1,
        draft=DraftRef(draft_id="draft-y", draft_version=1, body_sha256=sha256_text("y")),
        route_ref=entry.route_ref,
        account_id=entry.account_id,
        recipient=entry.recipient,
        sendbox=SENDBOX,
        stage=OutreachStage.STAGE_2,
        authorization_id="auth-0002",
        manifest_sha256=sha256_text("manifest-2"),
        started_at=T1,
    )
    store.begin_attempt(pending, at=T1)

    stopped = stop_followup_for(
        event_send_key=key, store=store, at=T1, note="对面回了信（回信正文不参与这个判断）"
    )
    assert stopped == 1
    assert store.attempt("send-pending-0001").state is MessageState.FAILED
    assert "保守停发" in store.attempt("send-pending-0001").failure
    # 已经出去的那一封**不受影响** —— 停发不是撤回。
    assert store.attempt(key).state is MessageState.ACCEPTED_BY_PROVIDER


def test_stopping_without_a_reason_is_refused(store):
    with pytest.raises(MarketOpsError) as caught:
        stop_followup_for(event_send_key="send-0001", store=store, at=T0, note="   ")
    assert "写明理由" in str(caught.value)


def test_a_reply_that_matches_nothing_stops_nothing_and_says_so(store):
    """**0 是一个真实的结论**（没有待发的），不是失败。"""
    assert stop_followup_for(event_send_key="send-not-ours", store=store, at=T0, note="回信") == 0


def test_a_dispatch_report_tells_skips_apart_from_deliveries():
    """把 ``skipped`` 算进「发了几封」会让「发了两封」与「两条候选」变成同一句话。"""
    assert not DispatchOutcome(send_key="k", state="skipped", detail="").reached_the_transport
    assert not DispatchOutcome(send_key="k", state="duplicate", detail="").reached_the_transport
    for state in ("sent", "refused", "unknown"):
        assert DispatchOutcome(send_key="k", state=state, detail="").reached_the_transport


# --- 十一、说过停的回信 ----------------------------------------------------------


def _a_stopping_event(store, *, attempt, kind=CorrespondenceKind.REFUSAL, at=T1 + timedelta(hours=1)):
    """把这个人的一条回信落进库里。**它关联到我们真的发出去的那一次尝试。**

    处置取自 domain 的处置表，不是这里手写的：这一条要造的是「收信侧真的会写下的
    那一行」，而它的形状由那张表定（``SUPPRESSING_KINDS`` 由同一张表推出来）。
    """
    store.put_event(
        CorrespondenceEvent(
            event_id=f"corr-{kind.value}",
            workspace=WORKSPACE,
            sendbox=SENDBOX,
            kind=kind,
            provider_message_id=f"<reply-{kind.value}@example.co.uk>",
            send_key=attempt.send_key,
            sender=attempt.recipient,
            subject="Re: your note about the square",
            body="No thanks, we are not interested.",
            raw_ref="mail-capture://000001.eml",
            raw_sha256=sha256_text(f"raw-{kind.value}"),
            received_at=at,
            ingested_at=at,
            disposition=KIND_DISPOSITION[kind],
        )
    )


def test_only_a_machine_reply_and_a_transient_bounce_let_the_next_wave_through():
    """「哪些回信算停」是 domain 那张表的答案 —— 这里把它**逐字**钉一遍。

    手写一份期望值是有意的：这一条是**第二个来源**，而它要抓的正是「有人改了
    处置表，而没有任何人说得出这一改意味着什么」。所以它按**分类**列全，而不是
    按「处置不是 RECORD_ONLY」重算一遍 —— 后者与实现是同一个式子，永远同意。
    """
    expected = {
        CorrespondenceKind.HUMAN_REPLY: True,
        CorrespondenceKind.REFUSAL: True,
        CorrespondenceKind.UNSUBSCRIBE: True,
        CorrespondenceKind.COMPLAINT: True,
        CorrespondenceKind.HARD_BOUNCE: True,
        # 机器回的不是人，邮箱满了不是人离开了 —— 只有这两个不停。
        CorrespondenceKind.SOFT_BOUNCE: False,
        CorrespondenceKind.AUTO_REPLY: False,
    }
    assert set(expected) == set(CorrespondenceKind), "分类表长了一个成员，这一条要重新逐字审一遍"
    assert {kind: stops_further_outreach(kind) for kind in CorrespondenceKind} == expected


def test_a_reply_that_says_stop_is_not_planned_again(store, scenes):
    """§7 逐字：「明确拒绝 → 停止跟进」。

    这一条的**前提**是库里没有抑制行 —— 拒绝不写抑制（抑制是跨 Campaign 长期有效的
    事实，而拒绝是关于这次接洽的），所以只查抑制名单的实现会在这里把那个人**照常
    排进下一批**。下面那句断言就是这个前提，少了它，挡住计划的可能是另一条规则，
    而这一条什么都没证明。
    """
    _, first = a_sendable_wave(
        store, scenes, recipients=("grounds@example.co.uk", "pro@example.co.uk")
    )
    transport = RecordingTransport()
    assert do_dispatch(store, scenes, transport).summary_line == "sent=2"

    said_no = next(
        row for row in store.attempts(wave_id=first.wave_id) if row.recipient.startswith("grounds@")
    )
    _a_stopping_event(store, attempt=said_no)

    assert not store.is_suppressed(said_no.recipient, at=T1 + timedelta(hours=2)), (
        "拒绝不写抑制行 —— 这一条测的是抑制之外的那一条规则"
    )

    second = run_plan(
        store,
        routes=[
            make_route(account_id="account-0001", email="grounds@example.co.uk"),
            make_route(account_id="account-0002", email="pro@example.co.uk"),
        ],
        wave_id="wave-0002",
        at=T1 + timedelta(hours=2),
    )
    assert [entry.recipient for entry in second.entries] == ["pro@example.co.uk"]
    refused = next(row for row in second.skipped if row.recipient == said_no.recipient)
    assert "refusal" in refused.reason
    assert "不再跟进" in refused.reason


def _a_second_wave_ready_to_go(store, scenes):
    """第一次外联真的发出去了，第二次**计划、写稿、冻清单、授权都做完了**。"""
    _, first = a_sendable_wave(store, scenes)
    transport = RecordingTransport()
    assert do_dispatch(store, scenes, transport).summary_line == "sent=1"

    second = [make_route()]
    draft_them(store, second, at=T1, wave_id="wave-0002")
    manifest = freeze(store, second, at=T1, wave_id="wave-0002")
    assert manifest.wave_id == "wave-0002", "第二封必须落在第二批上，不是第一批"
    authorise(store, manifest, at=T1, authorization_id="auth-0002")
    return first, transport


def test_a_reply_that_says_stop_blocks_a_wave_that_was_already_frozen(store, scenes):
    """§6 逐字：**每一次**投递前重查……**回信**。

    计划做在冻清单之前，而一封「不要再联系我」完全可能落在计划之后、投递之前 ——
    那一刻这封信还没离机，所以这里是最后一个能挡住它的地方。这一条走的是那条缝：
    清单已经授权、窗口已经开着、前序段与额度都够，只有这一条拦得住。
    """
    first, transport = _a_second_wave_ready_to_go(store, scenes)
    said_no = store.attempts(wave_id=first.wave_id)[0]
    _a_stopping_event(store, attempt=said_no)

    report = do_dispatch(
        store, scenes, transport, at=T1 + timedelta(hours=2), wave_id="wave-0002"
    )
    assert len(transport.sent) == 1, "第二次外联一封都不该出去"
    assert report.outcomes[0].state == "skipped"
    assert "refusal" in report.outcomes[0].detail
    assert store.attempts(wave_id="wave-0002") == ()


@pytest.mark.parametrize(
    "kind",
    [CorrespondenceKind.AUTO_REPLY, CorrespondenceKind.SOFT_BOUNCE],
    ids=lambda value: value.value,
)
def test_a_reply_that_does_not_say_stop_lets_the_frozen_wave_go_out(store, scenes, kind):
    """同一批已经冻好的货，回信换了分类就发得出去。

    这是上面那两条的**对照**：没有它，「第二次外联被挡住了」也可能只是「第二次
    外联本来就发不出去」—— 一条永远为真的判据看起来与一条正确的判据一样绿。
    """
    first, transport = _a_second_wave_ready_to_go(store, scenes)
    _a_stopping_event(store, attempt=store.attempts(wave_id=first.wave_id)[0], kind=kind)

    report = do_dispatch(
        store, scenes, transport, at=T1 + timedelta(hours=2), wave_id="wave-0002"
    )
    assert report.summary_line == "sent=1"
    assert len(transport.sent) == 2


# --- 九、授权边界：真实副作用入口的最后核验 ----------------------------------
#
# 这一节对应 public Issue #6 的验收目标。它不是第二套引擎：用的仍然是
# `record_authorization` / `dispatch_wave` / `lapse_authorization`，只是把
# 「授权从哪来」「执行者是谁」「批次中途失效」三格补成可拒绝的判据。
#
# 每一条都在**传输层之前**断言 `transport.sent`。只断言返回值的写法，
# 会把一次「报告说拒绝了、字节其实已经出去」读成绿。


class _LapsingTransport(RecordingTransport):
    """在第一封离开之后调用一个回调 —— 用来模拟「批次执行中授权被撤回」。"""

    def __init__(self, *, on_first, **kwargs):
        super().__init__(**kwargs)
        self._on_first = on_first

    def send(self, *, entry, subject: str, body: str, from_address: str):
        receipt = super().send(
            entry=entry, subject=subject, body=body, from_address=from_address
        )
        if len(self.sent) == 1:
            self._on_first()
        return receipt


class _FixedAuthoritySource:
    """一个按给定判决回答的来源替身。不读环境、不读网络。"""

    def __init__(self, verdict: AuthorityVerdict, detail: str = ""):
        self._verdict = verdict
        self._detail = detail
        self.asked: list[dict] = []

    def check(self, **kwargs) -> AuthorityCheck:
        self.asked.append(kwargs)
        return AuthorityCheck(self._verdict, self._detail)


def _authorization(manifest, **overrides):
    values = dict(
        authorization_id="auth-0001",
        wave_id=manifest.wave_id,
        workspace=manifest.workspace,
        scene_id=manifest.scene_id,
        manifest_sha256=manifest.manifest_sha256,
        authorized_by="operator:member-a",
        authority_ref="approval-ticket-0001",
        authorized_at=T0,
        expires_at=T2,
        approved_count=len(manifest.entries),
        provenance=AuthorizationProvenance.BEARER_PRINCIPAL,
    )
    values.update(overrides)
    return WaveAuthorization(**values)


def _three_recipient_wave(store, scenes, *, expires_at=T2):
    """三封的批次：足够在一次投递之后观察「剩下的还发不发」。"""
    recipients = (
        "grounds@example.co.uk",
        "office@example.co.uk",
        "hello@example.co.uk",
    )
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [
        make_route(account_id=f"account-{index:04d}", email=email)
        for index, email in enumerate(recipients, start=1)
    ]
    draft_them(store, routes)
    manifest = freeze(store, routes)
    authorise(store, manifest, expires_at=expires_at)
    return manifest


def test_an_unverified_authorization_never_reaches_the_transport(store, scenes):
    """默认 ``UNVERIFIED`` 的授权是「绕过受管入口登记」的那条路径。

    它可能由任何一段能 import 应用层的代码构造出来；``dispatch_wave`` 在
    **真正投递之前**整批拒绝，并报 ``BLOCKED_TRUST_SOURCE``。判据钉在
    ``transport.sent == []`` 上：只断言返回值会把「报告拒绝了、字节已出去」
    读成绿。
    """
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    manifest = freeze(store, routes)
    authorise(store, manifest, provenance=AuthorizationProvenance.UNVERIFIED)

    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport)

    assert transport.sent == []
    assert report.outcomes[0].state == "skipped"
    assert "BLOCKED_TRUST_SOURCE" in report.outcomes[0].detail


def test_the_executor_and_the_authorizer_must_be_two_different_subjects(store, scenes):
    """批准与执行落在同一个主体上时，整批不发。

    调用方给出的 ``executor`` 与授权上的 ``authorized_by`` 相同 —— 这正是
    「自批自发」的形状。MCP 的 ``send_wave`` 总是把传输层已认证的主体传进来；
    这里直接给同一个名字。
    """
    _, manifest = a_sendable_wave(store, scenes)
    transport = RecordingTransport()
    report = dispatch_wave(
        wave_id=manifest.wave_id,
        store=store,
        scenes=scenes,
        transport=transport,
        rules=make_rules(),
        at=T1,
        executor="operator:member-a",
    )

    assert transport.sent == []
    assert report.outcomes[0].state == "skipped"
    assert "批准" in report.outcomes[0].detail


def test_revocation_during_a_batch_stops_the_remaining_messages(store, scenes):
    """批次开始时有授权，第一封之后被退回待授权 —— 剩下的不得再发。

    这是 TOCTOU：批次开始时取到的是一份快照，而副作用发生在循环里。
    逐封重查读的是**存储里的当前事实**（批次状态），不是开始时的那个对象。
    """
    manifest = _three_recipient_wave(store, scenes)
    transport = _LapsingTransport(
        on_first=lambda: lapse_authorization(
            wave_id=manifest.wave_id,
            reason="授权在执行中被撤回",
            store=store,
            at=T1,
        )
    )
    report = dispatch_wave(
        wave_id=manifest.wave_id,
        store=store,
        scenes=scenes,
        transport=transport,
        rules=make_rules(),
        at=T1,
        clock=lambda: T1,
    )

    assert len(transport.sent) == 1
    assert report.outcomes[0].state == "sent"
    assert [row.state for row in report.outcomes[1:]] == ["skipped", "skipped"]
    assert "退回" in report.outcomes[1].detail


def test_expiry_during_a_batch_stops_the_remaining_messages(store, scenes):
    """授权在批次执行中过期 —— 逐封重查用的时钟能看见这件事。

    默认 ``clock`` 是批次的 ``at``，批次一般很短；一个可能跨过 ``expires_at``
    的调用方必须传 ``utc_now``。这一条用注入时钟把「跨过去」变成确定的：
    第一封在 T1 出发（授权仍在有效期内），第二封的时钟已经越过 ``expires_at``。
    """
    manifest = _three_recipient_wave(store, scenes, expires_at=T1)
    moments = iter((T1, T1 + timedelta(seconds=1), T1 + timedelta(seconds=2)))
    transport = RecordingTransport()
    report = dispatch_wave(
        wave_id=manifest.wave_id,
        store=store,
        scenes=scenes,
        transport=transport,
        rules=make_rules(),
        at=T1,
        clock=lambda: next(moments),
    )

    assert len(transport.sent) == 1
    assert report.outcomes[0].state == "sent"
    assert [row.state for row in report.outcomes[1:]] == ["skipped", "skipped"]
    assert "过期" in report.outcomes[1].detail


def test_a_blocked_trust_source_refuses_to_register_the_authorization(store, scenes):
    """没有受管批准记录来源时，登记这一步就拒绝。

    本仓今天没有接入任何来源，所以 ``NoAuthoritySource`` 的判决是
    ``BLOCKED_TRUST_SOURCE`` —— 把它当成通过，会让「没有验证」与
    「验证通过」在盘上长得一样。
    """
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    manifest = freeze(store, routes)
    authorization = _authorization(manifest)

    with pytest.raises(MarketOpsError) as caught:
        record_authorization(
            authorization=authorization,
            store=store,
            at=T0,
            authority_source=NoAuthoritySource(),
        )

    assert "BLOCKED_TRUST_SOURCE" in str(caught.value)
    assert store.authorizations(wave_id=manifest.wave_id) == ()


@pytest.mark.parametrize(
    "verdict",
    [AuthorityVerdict.NOT_FOUND, AuthorityVerdict.UNTRUSTED],
    ids=lambda value: value.value,
)
def test_an_untrusted_verdict_is_refused_before_the_record_is_written(store, scenes, verdict):
    """``NOT_FOUND`` 与 ``UNTRUSTED`` 分开报，但都不登记。"""
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    manifest = freeze(store, routes)

    with pytest.raises(MarketOpsError) as caught:
        record_authorization(
            authorization=_authorization(manifest),
            store=store,
            at=T0,
            authority_source=_FixedAuthoritySource(verdict, "合成来源的回答"),
        )

    assert verdict.value in str(caught.value)
    assert store.authorizations(wave_id=manifest.wave_id) == ()


def test_a_verified_authority_source_is_accepted_and_asked_with_the_bound_facts(store, scenes):
    """正向对照：``VERIFIED`` 才登记，而且问的是这次批准绑定的全部事实。

    没有这一条，「所有来源都被拒绝」也会让上面三条绿 —— 一个永远拒绝的
    验证器与一个正确的验证器在负测上同形。
    """
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    manifest = freeze(store, routes)
    authorization = _authorization(manifest)
    source = _FixedAuthoritySource(AuthorityVerdict.VERIFIED, "合成工单存在且覆盖本次范围")

    record_authorization(
        authorization=authorization, store=store, at=T0, authority_source=source
    )

    assert [row.authorization_id for row in store.authorizations(wave_id=manifest.wave_id)] == [
        authorization.authorization_id
    ]
    assert source.asked == [
        {
            "authority_ref": authorization.authority_ref,
            "authorized_by": authorization.authorized_by,
            "workspace": authorization.workspace,
            "scene_id": authorization.scene_id,
            "action": "outreach.send",
            "target": authorization.manifest_sha256,
        }
    ]


def test_an_authority_source_that_crashes_is_a_refusal_not_a_pass(store, scenes):
    """验证器崩溃按拒绝处理。一个没有答案的来源不是一次批准。"""
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    manifest = freeze(store, routes)

    class _Crashing:
        def check(self, **kwargs):
            raise RuntimeError("来源不可达")

    with pytest.raises(MarketOpsError) as caught:
        record_authorization(
            authorization=_authorization(manifest),
            store=store,
            at=T0,
            authority_source=_Crashing(),
        )

    assert "验证失败按**拒绝**处理" in str(caught.value)
    assert store.authorizations(wave_id=manifest.wave_id) == ()


def test_no_authorization_sends_nothing(store, scenes):
    """没有授权时一封都不发 —— 这是本节的基线，防止「拒绝」被别的原因解释。"""
    scenes.put_record(make_scene(), expected_revision=None)
    seed(store)
    routes = [make_route()]
    draft_them(store, routes)
    manifest = freeze(store, routes)

    transport = RecordingTransport()
    report = do_dispatch(store, scenes, transport)

    assert manifest.wave_id == WAVE
    assert transport.sent == []
    assert report.outcomes[0].state == "skipped"
    assert "授权" in report.outcomes[0].detail

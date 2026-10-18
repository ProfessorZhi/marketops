"""外向链的**编排**：写稿 → 冻结批次 → 受控授权 → 执行投递 → 对账。

这一层回答「一次触达要按什么顺序、每一步在什么地方可能被挡住」，
不回答「什么叫一个合格的收件人」（``domain/``），也不回答「怎么把一个字节
交给对端」（``adapters/``）。

## 它为什么必须存在，而不是让调用方按顺序调几个 store

因为 §6 那句「**默认停在** ``prepared_pending_authorization``」是一句**顺序**
要求：批次在没有人批准之前不得进入投递，而批准之后**每一次**投递前还要
重查八件事。把那些检查留在调用方手里，第一个调用方就会漏掉第八件 ——
而漏掉的那一次在记录上完全看不出来（那一封成功了）。

所以这里是唯一一处「投递前该查什么」有代码的地方，而它的形状是
**一段没有分支可以绕过的序列**：:func:`dispatch_wave` 里每一个 ``continue``
都写下了它跳过的那一条与为什么。

## 五段，各自守一次不同的失败

| 段 | 守的失败 |
|---|---|
| ``plan`` | 把不该发的、已经发过的、被抑制的**在花任何钱之前**挡掉 |
| ``generate_drafts`` | 写作者编造事实、缺个性化硬猜、以及「同一份输入重跑一次又生成一稿」 |
| ``prepare_wave`` | 冻结之后有人换了内容 —— 清单摘要一变，上一次授权就失效 |
| ``dispatch_wave`` | **越权发出去了**：这是整条链上唯一会造成不可撤销后果的一段 |
| ``reconcile`` | 「提供方可能收了」被当成「没发出去」而重发 —— 有人收到两封 |

## 三件刻意**不做**的事

1. **不构造任何 adapter。** 传输、写作者、存储全部注入。``dispatch_wave``
   收一个 :class:`~marketops.ports.outreach.MailTransport`，而它不知道那是
   SMTP、是一个本地捕获服务，还是一个测试替身 —— 这一层唯一需要知道的是
   :attr:`~marketops.ports.outreach.MailTransport.sender_identity`。
2. **不产生授权。** :func:`record_authorization` 只把一份**已经存在**的授权
   记录落库并核对它真的覆盖这份清单。它没有「批准」这个动作 —— 一个人
   （或一条受管记录）批准之后，那份批准在这里被**登记**。一个能自己产生授权的
   函数，就是一个可以说「我批准了」的模型。
3. **不改写正文。** 发给传输的 ``body`` 是从库里读出来的草稿正文字节，
   与清单上的 ``body_sha256`` 逐字比对之后才交出去。写作者产出的东西在
   ``generate_drafts`` 那一段就已经定稿。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from ..domain.entities import ContactRoute
from ..domain.enums import EntryKind, Grade, RouteBinding, RouteStatus
from ..domain.errors import ConfigError, MarketOpsError
from ..domain.outreach import (
    DraftRef,
    DraftState,
    ManifestEntry,
    MessageState,
    OutreachCampaign,
    OutreachDraft,
    OutreachManifest,
    OutreachPolicyDecl,
    OutreachStage,
    OutboundAttempt,
    TemplateFamily,
    Wave,
    WaveAuthorization,
    WaveState,
    WritingPolicy,
    stops_further_outreach,
)
from ..domain.scene import SceneState
from ..domain.util import check_canonical_id, normalise_email, sha256_text, to_utc
from ..ports.outreach import (
    DraftProposal,
    DraftRequest,
    DraftWriter,
    MailTransport,
    OutreachStore,
)
from ..ports.scenes import SceneStore
from .validation import one_line_reason

#: 摘要拼接用的分隔符。用一个**不可能出现在标识里**的字节，否则
#: ``("ab", "c")`` 与 ``("a", "bc")`` 会算出同一个摘要。
_SEP = "\x1f"

# --- 规则：**缺省值一律是「什么都不许」** ------------------------------------


@dataclass(frozen=True)
class OutreachRules:
    """一次触达的**部署策略**。每一个数字都必须由人给，没有一个有缺省。

    ## 为什么没有缺省值

    §4 逐字：「有效期来自已批准的策略，或者被显式标为**未标定的默认值**」，
    而 §6 对第 2–4 段更严：「必须严格读已确认的上一阶段与允许的等待期/次数，
    **未配置时不得臆造间隔**」。一个带缺省的 ``wait=timedelta(days=4)`` 是
    最坏的一种：它读起来像一个业务事实，而它只是某个下午有人随手写下的数。

    所以 :meth:`required` 在构造时就拒绝缺项，而 :func:`plan_wave` 需要它的
    那一刻会拿到一条说得出「缺的是哪一项」的 :class:`~marketops.domain.errors.ConfigError`。

    ## ``allowed_wait`` 是**下界**，不是目标

    「至少等这么久」与「正好等这么久」在编排上是两件事：前者允许一次调度
    晚到几小时，后者会把一次正常的时间漂移判成违规。这里取前者 —— 一个把下界
    当目标实现的调度器，会在时钟回拨时把两段挤在一起。
    """

    #: 同一段允许发多少封（上限）。**不是配额** —— 一个批次远小于它也是正常的。
    max_per_wave: int
    #: 一个收件人**一共**最多收几封（跨批次）。到顶之后就再也不发。
    max_per_recipient: int
    #: 第 2–4 段至少要等上一段被确认之后多久。键是**本段**（要发的那一段）。
    allowed_wait: dict[OutreachStage, timedelta]
    #: 第 2–4 段允许的「上一段之后一共发过几封」上限（含上一段本身）。
    allowed_prior_touches: dict[OutreachStage, int]
    #: 一个账号在整条链上最多被联系几次。**与 ``max_per_recipient`` 分开**：
    #: 同一个账号可以有三条路线、三个不同的人，而「这家单位被打扰了几次」
    #: 是另一个问题。
    max_per_account: int = 1

    def __post_init__(self) -> None:
        if self.max_per_wave < 0 or self.max_per_recipient < 1 or self.max_per_account < 1:
            raise ConfigError(
                "max_per_wave 不得为负，max_per_recipient 与 max_per_account 不得小于 1。\n"
                "0 会让每一条路线都被判成「超了」，而报告上只显示「这一批没人可发」——"
                "与「池子是空的」逐字相同。"
            )

    @classmethod
    def required(
        cls,
        *,
        max_per_wave: int | None = None,
        max_per_recipient: int | None = None,
        max_per_account: int | None = None,
        allowed_wait: dict[OutreachStage, timedelta] | None = None,
        allowed_prior_touches: dict[OutreachStage, int] | None = None,
    ) -> "OutreachRules":
        """从配置里读一份规则；**缺任何一项就抛**，并点名缺的是哪一项。"""
        missing = [
            name
            for name, value in (
                ("outreach.max_per_wave", max_per_wave),
                ("outreach.max_per_recipient", max_per_recipient),
                ("outreach.allowed_wait", allowed_wait),
                ("outreach.allowed_prior_touches", allowed_prior_touches),
            )
            if value is None
        ]
        if missing:
            raise ConfigError(
                "触达规则缺项：" + "、".join(missing) + "。\n"
                "§6 逐字：「未配置时不得臆造间隔」—— 每一个数都必须由人给，"
                "因为一个看起来像业务事实的缺省值会被读成业务事实。"
            )
        return cls(
            max_per_wave=max_per_wave,  # type: ignore[arg-type]
            max_per_recipient=max_per_recipient,  # type: ignore[arg-type]
            max_per_account=max_per_account if max_per_account is not None else 1,
            allowed_wait=allowed_wait,  # type: ignore[arg-type]
            allowed_prior_touches=allowed_prior_touches,  # type: ignore[arg-type]
        )

    @classmethod
    def from_declaration(cls, declared: OutreachPolicyDecl) -> "OutreachRules":
        """从 Scene 某一版声明的策略**换算**过来。

        ## 为什么是换算而不是别名

        声明的单位是**小时**（见 ``domain.outreach.StageWait`` 的理由），而这一层
        用的是 ``timedelta``。换算只有这一处，所以「小时到哪里变成 delta」这个
        问题只有一个答案。两处各写一次乘法，第二次就会有人写成 ``days``。

        ``calibrated`` 那一格**不带过来**：它不是编排层需要知道的东西，而且
        把它复制一份到这里，会让「这份规则标定过没有」在盘上有两个来源。
        需要它的地方（回执）读的是 ``SceneVersion.outreach`` 本身。
        """
        return cls(
            max_per_wave=declared.max_per_wave,
            max_per_recipient=declared.max_per_recipient,
            max_per_account=declared.max_per_account,
            allowed_wait={
                row.stage: timedelta(hours=row.after_hours) for row in declared.waits
            },
            allowed_prior_touches={
                row.stage: row.allowed_prior for row in declared.prior_touches
            },
        )

    def wait_for(self, stage: OutreachStage) -> timedelta:
        """第 ``stage`` 段要求的等待期。**没有配置就抛**，不返回 0。

        返回 ``timedelta(0)`` 会让「没配」与「配成不等」在读的人眼里一样，
        而后果是两段被挤在一起发出去 —— 那正是 §6 要挡的「臆造间隔」。
        """
        try:
            return self.allowed_wait[stage]
        except KeyError:
            raise ConfigError(
                f"第 {stage.ordinal} 段没有配置等待期（outreach.allowed_wait）。\n"
                "§6 逐字：未配置时**不得臆造间隔** —— 缺项必须停下来，"
                "而不是按 0 或按一个顺手写的天数走。"
            ) from None

    def prior_touch_budget(self, stage: OutreachStage) -> int:
        """第 ``stage`` 段之上允许有几封已确认的前序段。没有配置就抛。"""
        try:
            return self.allowed_prior_touches[stage]
        except KeyError:
            raise ConfigError(
                f"第 {stage.ordinal} 段没有配置前序段上限"
                "（outreach.allowed_prior_touches）。\n"
                "与等待期同一条：缺项必须停下来。"
            ) from None


# --- 计划：候选、跳过与理由 ---------------------------------------------------


@dataclass(frozen=True)
class PlannedEntry:
    """一条**通过筛选**的候选：它会进清单。"""

    route_ref: str
    account_id: str
    organisation: str
    recipient: str
    stage: OutreachStage
    family_id: str
    writing_policy_id: str
    evidence_ids: tuple[str, ...]
    send_key: str


@dataclass(frozen=True)
class SkippedEntry:
    """一条**没过筛**的候选，以及为什么。

    它存在的理由与 ``application/caps.py`` 的 ``TruncationDecision`` 逐字相同：
    被挡掉的那部分必须被记录。一个静默丢掉的候选在报告上与「池子里没有它」
    长得一样，而两者的处置相反（一个要去查为什么被挡，一个要去补池子）。
    """

    route_ref: str
    account_id: str
    recipient: str
    stage: OutreachStage
    reason: str


@dataclass(frozen=True)
class WavePlan:
    """一次筛选的结论。**它不含任何正文** —— 正文是下一段的事。"""

    campaign_id: str
    wave_id: str
    workspace: str
    scene_id: str
    #: 这份计划是拿**哪一版** Scene 的规则筛出来的（``SceneVersion.version``）。
    #:
    #: 它在计划上而不是在调用点上，是因为**规则是计划的一部分**：``rules`` 是那一版
    #: 的 ``outreach`` 声明，而计划里的每一条筛选都按它做。草稿要记的是这个数
    #: （§8 的可追溯性），而它只该有一个来源 —— 让起草那一处自己再传一遍，它就可以
    #: 与真正用来筛的数不同，而两份回执各自自洽。
    scene_version: int
    stage: OutreachStage
    entries: tuple[PlannedEntry, ...]
    skipped: tuple[SkippedEntry, ...]

    @property
    def skipped_line(self) -> str:
        """一句给报告用的摘要。**不判成败**，只列事实。"""
        counts: dict[str, int] = {}
        for row in self.skipped:
            key = row.reason.split("：", 1)[0]
            counts[key] = counts.get(key, 0) + 1
        parts = " ".join(f"{key}={counts[key]}" for key in sorted(counts))
        return f"planned={len(self.entries)} skipped={len(self.skipped)} {parts}".rstrip()


# 等级 → 能不能联系。**这是一张表，不是散在分支里的判断。**
#:
#: 取值来自 SOP v1 L125–L129：A/B 是「关键决策职能 + 真实公开业务邮箱 + 强绑定」，
#: C/D 是业务入口或通用入口。§6 逐字：「C/D 必须标为业务入口/通用入口，
#: **不得**与 A/B 混称关键联系人」。所以 C/D **不进**首次接触的批次 ——
#: 它们不是「差一点的联系人」，是另一种东西。
KEY_CONTACT_GRADES: frozenset[str] = frozenset({"A", "B"})


def send_key_for(
    *, workspace: str, wave_id: str, draft_id: str, draft_version: int, recipient: str
) -> str:
    """一次逻辑发送的标识。**由五个分量派生，不由调用方给。**

    与 ``domain/budget.py::reservation_id``、``ports/decisions.py`` 的 ``record_id``
    同一条：一个必须**两处算出同一个值**的标识，各写一遍就迟早会漂。
    它此后出现在 ``OutboundAttempt.send_key`` 上，而那是一个
    ``check_canonical_id`` 字段 —— 所以返回值必须逐字是一个 slug。
    """
    parts = [
        workspace,
        wave_id,
        draft_id,
        str(draft_version),
        normalise_email(recipient),
    ]
    return check_canonical_id(
        sha256_text("\x1f".join(parts))[:32], "send_key"
    )


# --- 一、筛选 ----------------------------------------------------------------


def plan_wave(
    *,
    routes: Sequence[ContactRoute],
    store: OutreachStore,
    campaign_id: str,
    wave_id: str,
    workspace: str,
    scene_id: str,
    scene_version: int,
    stage: OutreachStage,
    rules: OutreachRules,
    at: datetime,
) -> WavePlan:
    """把候选路线筛成一份**计划**。**它不写任何东西，也不发任何东西。**

    筛选的八个条件，逐条对应一次真实事故：

    1. **等级**：C/D 不进首次接触（见 :data:`KEY_CONTACT_GRADES`）。
    2. **状态**：已经进入发送链、回信、被抑制的路线不再进批次。
       重发一条 ``sent_stage_1`` 的路线，人会收到两封。
    3. **抑制**：按 ``at`` **查**库（不是读缓存）。
    4. **证据**：``evidence_ids`` 为空的路线不得进 —— 一封说不出凭什么发的信，
       事后无法解释。
    5. **说过停的回信**：见 :func:`stopped_by_reply`。它与第 3 条是**两件事** ——
       拒绝不写抑制行（domain 的处置表），所以只查抑制名单会让一个说了「不用了」
       的人在下一批里照常收到信。
    6. **收件人额度**：同一个人跨批次的封数上限。
    7. **批内去重**：同一个收件人在这一批里只进一次。**这是按收件人而不是按
       路线**：同一个人的两条路线是两条记录，但他是一个人。
    8. **批上限**：``rules.max_per_wave``。
    """
    moment = to_utc(at)
    planned: list[PlannedEntry] = []
    skipped: list[SkippedEntry] = []
    seen_recipients: set[str] = set()
    seen_accounts: set[str] = set()

    def skip(route: ContactRoute, recipient: str, reason: str) -> None:
        skipped.append(
            SkippedEntry(
                route_ref=route.account_id,
                account_id=route.account_id,
                recipient=recipient,
                stage=stage,
                reason=reason,
            )
        )

    for route in routes:
        recipient = normalise_email(route.email)
        if route.grade.value not in KEY_CONTACT_GRADES:
            skip(route, recipient, f"等级不是关键联系人：{route.grade.value}")
            continue
        if route.status not in INTAKE_STATUSES:
            # 只放行「还没进过发送链」的路线。见 docstring 第 2 条。
            skip(route, recipient, f"这条路线已经离开入口状态：{route.status.value}")
            continue
        if not route.evidence_ids:
            skip(route, recipient, "这条路线没有证据")
            continue
        if store.is_suppressed(recipient, at=moment):
            skip(route, recipient, "收件人在抑制名单上")
            continue
        stopped = stopped_by_reply(store=store, workspace=workspace, recipient=recipient)
        if stopped:
            # 抑制名单只装得下退订/投诉/硬退信。一个人回信说「不用了」不写抑制行
            # （那是关于这次接洽的，见 domain 的处置表），但他**照样不该再收到** ——
            # 这条判据没有别的地方能替它，因为出信侧是唯一会「再发一封」的一方。
            skip(route, recipient, stopped)
            continue
        if recipient in seen_recipients:
            skip(route, recipient, "这一批里同一个人出现了两次")
            continue
        if route.account_id in seen_accounts:
            skip(route, recipient, "这一批里同一个账号出现了两次")
            continue
        if len(store.attempts_by_recipient(recipient)) >= rules.max_per_recipient:
            skip(route, recipient, f"这个收件人已经收满了（上限 {rules.max_per_recipient}）")
            continue
        if len(planned) >= rules.max_per_wave:
            skip(route, recipient, f"这一批已经到上限（{rules.max_per_wave}）")
            continue

        planned.append(
            PlannedEntry(
                route_ref=route.account_id,
                account_id=route.account_id,
                organisation=route.organisation,
                recipient=recipient,
                stage=stage,
                family_id="",
                writing_policy_id="",
                evidence_ids=tuple(route.evidence_ids),
                send_key=send_key_for(
                    workspace=workspace,
                    wave_id=wave_id,
                    draft_id=_draft_id_for(wave_id, recipient),
                    draft_version=1,
                    recipient=recipient,
                ),
            )
        )
        seen_recipients.add(recipient)
        seen_accounts.add(route.account_id)

    # 计划的行序按 ``send_key`` 定：它是内容的摘要，所以换一台机器、换一次调用
    # 顺序得到同一份计划。**清单的行序在 :func:`prepare_wave` 里另定**（那里才
    # 知道草稿的真实版本号，而 ``approved_slice`` 取前 N 行靠的正是那个序）。
    ordered = tuple(sorted(planned, key=lambda row: row.send_key))
    return WavePlan(
        campaign_id=campaign_id,
        wave_id=wave_id,
        workspace=workspace,
        scene_id=scene_id,
        scene_version=scene_version,
        stage=stage,
        entries=ordered,
        skipped=tuple(skipped),
    )


#: 允许进批次的**入口状态**。``QUEUED_CANDIDATE`` 是线路被选中的那一刻，
#: ``OBSERVED`` 是它刚被读出来 —— 两者都「还没被联系过」。
#:
#: 用一张**白名单**而不是黑名单：``RouteStatus`` 会继续长（``REPLY`` 之后还会有
#: 别的），而黑名单的失效方式是**静默放行一个新状态**——一个没人想过的新状态
#: 会被当成「还没联系过」。白名单的失效方式是「一个新状态进不来」，
#: 那是一条看得见的症状。
INTAKE_STATUSES: frozenset[RouteStatus] = frozenset(
    {RouteStatus.OBSERVED, RouteStatus.QUEUED_CANDIDATE}
)


def _draft_id_for(wave_id: str, recipient: str) -> str:
    """这一批里发往这个收件人的那份稿子的 id。

    由 ``(wave_id, recipient)`` 派生而不是由调用方给：一个可以让调用方指定
    ``draft_id`` 的接口，会让两封信共用一个 id，而 ``put_draft`` 对
    「同 id 同版本、内容不同」是报错的 —— 于是那一次失败会发生在**第二封信**
    上，看起来像「写作者不稳定」。
    """
    return check_canonical_id(
        "draft-" + sha256_text(f"{wave_id}\x1f{normalise_email(recipient)}")[:24], "draft_id"
    )


# --- 一之二、起草的筛选：与发送队列**不是同一件事** ----------------------------
#
# §8 逐字：「**草稿准备与发送队列准备分成两个用例。** Research v1 的草稿不要求
# SMTP、manifest 授权或已发送前序；后续正式外发仍必须通过全部独立安全门。」
#
# 分成两个函数，而不是给 :func:`plan_wave` 加一个模式开关。加开关是错的，理由与
# :class:`~marketops.domain.temporal.Freshness` 分成「结论 + 理由」同一条：一个
# 带开关的函数会让「这一次按哪一套判据筛的」在**调用点**上读不出来，而两套判据的
# 失效方向是**相反**的 ——
#
# * 发送那一套宁可少发：漏掉一个人只是少做一次触达；
# * 起草那一套宁可多写：漏掉一个人是一条**看不见**的路线，而它正是上一轮真实验收
#   里那个 ``planned=0 / skipped=22`` 的形状。
#
# 一个开关迟早会被两处的调用方各按自己那一侧的直觉填，而两次都自洽。

#: 等级从强到弱的**批准顺序**（§8 逐字：「SOP 允许 A→B→C→D 候选序列」）。
#:
#: 它只用于**排序**（A 先写），不用于筛选 —— 「谁可以写」由
#: :data:`DRAFTABLE_ENTRY_KINDS` 与 :func:`plan_drafting` 判。把顺序与准入合成
#: 一张表，会让「C 排在 A 后面」很快被读成「没有 A 才写 C」，而 §8 说的是候选
#: 序列，不是回退链：C 出现了、A 也出现了，两条都该有草稿。
GRADE_ORDER: tuple[str, ...] = ("A", "B", "C", "D")


def grade_rank(grade: str) -> int:
    """等级在批准顺序里的位置。**不认识的等级排在最后，不抛。**

    与「不认识就抛」相比这是刻意选的：``Grade`` 是闭集，但计划里可能出现一个
    本函数没见过的取值（上游将来加了 E）。抛会让整批起草停在一个**排序**问题上，
    而排序错了的后果只是「这一批里先写谁」—— 不是「写错人」。
    """
    return GRADE_ORDER.index(grade) if grade in GRADE_ORDER else len(GRADE_ORDER)


#: 允许进正常草稿的**入口性质**。§8 逐字：「C/D 草稿需明确是部门/通用入口，
#: 不冒充具名决策人……**X 不进入正常草稿**」。
#:
#: 白名单而不是黑名单，与 :data:`INTAKE_STATUSES` 同一条理由：``EntryKind`` 会
#: 继续长，而黑名单的失效方式是**静默放行一个新取值**。这里的三个取值恰好就是
#: A/B（``NAMED_ROLE``）、C（``OFFICIAL_BUSINESS_ENTRY``）、D（``GENERIC_OFFICIAL``），
#: 而 ``EXCLUDED`` 是 X —— 它落在外面是**看得见的**：一条被排除的路线，带理由。
DRAFTABLE_ENTRY_KINDS: frozenset[EntryKind] = frozenset(
    {EntryKind.NAMED_ROLE, EntryKind.OFFICIAL_BUSINESS_ENTRY, EntryKind.GENERIC_OFFICIAL}
)

#: 允许「角色路线对不上就退回本阶段那一族」的等级。见 :func:`family_for`。
#:
#: 只在 A/B 上允许，因为退回的后果**按等级不同**：一族写给关键联系人的话术套在
#: 一条部门/通用入口上，正是 §8 逐字要挡的那件事（「不冒充具名决策人」）。而 A
#: 退回 B 是安全的 —— 两者都是「关键决策职能 + 真实公开业务邮箱」，差别在角色，
#: 不在收信人的性质。
#:
#: 这条取舍的代价是实打实的：一个**还没发布**过 C/D 族的部署，会看到 C/D 的路线
#: 全部落进「没有面向这一类入口的模板族」，而那是正确的读数 —— 它说的是「批准过的
#: 话术里还没有给部门/通用入口的那一套」，下一步是去发布那一族，不是让它借用
#: 关键联系人的那一份。
FALLBACK_TO_STAGE_FOR_GRADES: frozenset[str] = frozenset({"A", "B"})

#: :func:`family_for` 是按哪一条规则挑出那一族的。**它进回执** ——
#: 一次「退回了本阶段那一族」的挑选与一次「角色路线逐字对上」的挑选，在正文上
#: 可以长得一样，而在「这一稿为什么用这套话术」这个问题上答案完全不同。
FAMILY_MATCH_ROLE_CLASS = "role_class"
FAMILY_MATCH_STAGE = "stage"


def family_for(
    families: Sequence[TemplateFamily],
    *,
    stage: OutreachStage,
    role_class: str,
) -> tuple[TemplateFamily | None, str]:
    """按 **(阶段, 角色路线)** 挑一族模板，并说出是按哪一条规则挑的。

    两层，逐层都是确定性的（同层多族时取 ``family_id`` 字典序最小）：

    1. ``role_class`` —— 这个阶段里 ``role_class`` **逐字相同**的那一族。
    2. ``stage`` —— 这个阶段字典序最小的那一族。

    第 2 层不是静默兜底：它是**记下来的**（``FAMILY_MATCH_STAGE`` 进回执），
    而且只对 :data:`FALLBACK_TO_STAGE_FOR_GRADES` 里的等级开放 —— 判在哪一层
    开放的是调用方（它才知道等级），本函数只负责挑与报出挑法。

    ## 为什么第 1 层是**逐字**比较，而不是「把两个词表对齐」

    模板族的 ``role_class`` 是配置里的一个自由字符串，路线的 ``role_class`` 是
    ``RoleClass`` 那六个取值之一。两者**今天不是同一个词表**（族那一侧由发布工具
    填，测试里出现过 ``grounds_manager`` 这种角色词，而路线那一侧是 ``operational``
    这种层级词）。一个「看起来等价就匹配」的实现会在两份词表之间**猜**一次映射，
    而猜错的后果是静默地换掉一封信的话术 —— 本仓对这类猜的处置一律是：不猜，
    把它变成一条读得出来的事实。所以第 1 层只认逐字相等，匹配不上就落第 2 层，
    而回执说得出落到了第 2 层。

    ## 为什么这里**没有**「用计划点名的 ``family_id``」那一层

    原来有，读的是 :class:`PlannedEntry.family_id`。删掉它是因为那个值在生产里
    **不是人选出来的**：``adapters/mcp_server/outreach.py`` 的 ``_campaign_family_id``
    用 ``family_for_stage`` 取本阶段 ``family_id`` 字典序最小的那一族填进去，也就是
    「本阶段的缺省族」。拿「缺省族」当「点名」，等于给**每一条**路线都套上同一族
    —— 角色匹配这一层（§B13 要求的那句「选择对应模板」）会**整层失效**，症状是
    「C/D 的稿子用了写给关键联系人的那一套话术」，正是 §8 逐字要挡的
    「不冒充具名决策人」。

    两族在库里恒等，所以这一层今天分不出任何东西。将来真要做「活动点名一族」，
    先要回答的是「**一个活动里两种角色路线各用哪一族**」—— 那是个活动侧的映射，
    不是这里的第四个分支。
    """
    pool = [family for family in families if family.stage is stage]
    ordered = sorted(pool, key=lambda family: family.family_id)
    for family in ordered:
        if family.role_class == role_class:
            return family, FAMILY_MATCH_ROLE_CLASS
    if ordered:
        return ordered[0], FAMILY_MATCH_STAGE
    return None, ""


def review_reasons(route: ContactRoute) -> tuple[str, ...]:
    """这条路线**为什么还不够直接进发送队列**。空元组 = 够了。

    §8 逐字：「准入、当前性或绑定不够时只给**明确标注的** review-only/held 状态，
    **不称 outreach-ready**」。这一格就是那句话的前半截。

    ## 它为什么是一张表，而不是起草时顺手判几句

    「够了」是一个**结论**，而结论要能被人反驳。散在起草流程里的几个 ``if``
    只会产出一个 ``review_only`` 布尔；而这个函数产出的是**逐条理由**，回执里
    读得到「是绑定不够还是当前性不够」——两者下一步做的事不同（一个去核邮箱归属，
    一个去重新验证），合成一个布尔之后就分不出来了。

    ## 三条判据，各对应一个**有生产者**的事实

    * **绑定** —— ``RouteBinding.NONE`` 是「无法证明这个邮箱属于这个组织」
      （``domain/enums.py``）。它**不是**正常产出的取值：``domain/bind.py`` 只产出
      ``STRONG`` 与 ``ACCOUNT``，而一条本该是 ``NONE`` 的候选在那一层就被
      ``UNBOUND_EMAIL`` 拒收了。所以一条**存着的**路线带着 ``NONE``，意思是
      「它的绑定从来没有人判过」。
    * **当前性** —— ``freshness is None`` 是「还没判过」（``ContactRoute.freshness``
      的 docstring 逐字如此），**不是**「判过、还行」。本仓对未确认的一贯读法是
      「未确认」，不是「没问题」；在这里它的后果就是这一条不能自称可外发。
    * **当前性（判过但不成立）** —— ``Freshness.is_actionable`` 为假的那几档
      （``STALE`` / ``UNKNOWN`` / ``CONFLICTING`` / ``NOT_SEEN`` /
      ``INVALID_SOURCE_TIME``）。判据**复用** ``domain/temporal.py`` 那一格，不另立
      一套「算不算旧」——§9 逐字：「复用现有版本机制，不新建一套平行权威」。

    ## 它**不**判的两件事

    收件人是不是被抑制、有没有说过停 —— 那两条在 :func:`plan_drafting` 里，是
    **排除**而不是 review-only：一封写给一个退订过的人的信，不值得任何人去看。
    """
    reasons: list[str] = []
    if route.binding is RouteBinding.NONE:
        reasons.append("绑定不够：无法证明这个邮箱属于这个组织（binding=none，从来没有人判过）")
    if route.freshness is None:
        reasons.append("当前性未确认：这条路线还没有判过时效（freshness 为空）")
    elif not route.freshness.is_actionable:
        reasons.append(
            f"当前性不够：{route.freshness.currentness.value}"
            f"（{route.freshness.reason.value}）"
        )
    return tuple(reasons)


@dataclass(frozen=True)
class DraftTarget:
    """一条**要起草**的路线。它与 :class:`PlannedEntry` 是**两个用例**的两种行。

    同一批里，同一条路线可以只出现在一边：一条 C 级的部门入口**该有草稿**，
    而它**不进**首次接触的发送队列（§8：「C/D …不挤占 A/B 研究或容量」）。
    把两种行合成一个类型，第一个实现就会把「计划里有它」读成「它会发出去」——
    而那正是上一轮 ``planned=0`` 的另一面。

    ``send_key`` **不在这一行上**：它是「一次逻辑发送」的标识，而一条 review-only
    的路线没有发送可言。给它算一个，等于在盘上留下一个指向一次不可能发生的发送的
    id（``dispatch_wave`` 那一侧按它取行，取不到就跳过，而报告上只显示「跳过了」）。
    """

    route_ref: str
    account_id: str
    organisation: str
    recipient: str
    stage: OutreachStage
    #: 这一条路线是哪一类入口与哪一级 —— **起草时冻结**，写进草稿。
    #:
    #: 冻结而不是「用的时候回查路线」：路线的等级会被下一次定级改写，而一稿已经
    #: 发出去的信号是按当时那一级写的。§8 逐字：「新证据或策略使旧草稿过时，
    #: 不能静默换文」——回查会让同一份草稿在两次读之间声称两个不同的等级。
    entry_kind: EntryKind
    route_grade: Grade
    #: 这一族模板是按哪一条规则挑出来的（:data:`FAMILY_MATCH_*` 之一）。进回执。
    family_match: str
    family_id: str
    writing_policy_id: str
    evidence_ids: tuple[str, ...]
    #: 非空 ⟺ 这一稿写出来也只能是 ``review_only``，不得直接进发送队列。
    #: 逐条理由见 :func:`review_reasons`。
    review_reasons: tuple[str, ...] = ()

    @property
    def review_only(self) -> bool:
        return bool(self.review_reasons)


@dataclass(frozen=True)
class DraftingPlan:
    """一次**起草**筛选的结论。**它不含任何正文**，也**不含任何发送标识**。"""

    campaign_id: str
    wave_id: str
    workspace: str
    scene_id: str
    scene_version: int
    stage: OutreachStage
    targets: tuple[DraftTarget, ...]
    skipped: tuple[SkippedEntry, ...]

    @property
    def skipped_line(self) -> str:
        """与 :attr:`WavePlan.skipped_line` 逐字同形 —— 两处的理由都按
        ``：`` 之前那一段分组，所以两份回执的读数可以并排看。"""
        counts: dict[str, int] = {}
        for row in self.skipped:
            key = row.reason.split("：", 1)[0]
            counts[key] = counts.get(key, 0) + 1
        parts = " ".join(f"{key}={counts[key]}" for key in sorted(counts))
        return f"targets={len(self.targets)} skipped={len(self.skipped)} {parts}".rstrip()


def plan_drafting(
    *,
    routes: Sequence[ContactRoute],
    store: OutreachStore,
    families: Sequence[TemplateFamily],
    campaign_id: str,
    wave_id: str,
    workspace: str,
    scene_id: str,
    scene_version: int,
    stage: OutreachStage,
    at: datetime,
) -> DraftingPlan:
    """把候选路线筛成一份**起草计划**。**它不写任何东西，也不发任何东西。**

    ## 它比 :func:`plan_wave` 少判三件事，而这三件正是「发送前序」

    §8 逐字：「草稿准备与发送队列准备分成两个用例……**不要求 SMTP、manifest 授权
    或已发送前序**」。逐个对上 :func:`plan_wave` 的八条：

    | 判据 | 起草这一侧 | 为什么 |
    |---|---|---|
    | 入口状态（``INTAKE_STATUSES``） | **不判** | 「已经离开入口状态」是发送侧的重发保护。一条 ``sent_stage_1`` 的路线**照样要有第二段的草稿** —— 判它等于「第二段的信永远写不出来」，而那是这条链最贵的一步 |
    | 收件人额度（``max_per_recipient``） | **不判** | 那是**跨批次的封数上限**，是一次编排决定。起草不消耗它：写完不发是常态（一个批次被授权之前，额度一封都没用掉） |
    | 批上限（``max_per_wave``） | **不判** | 同上。这里没有任何「这一批最多几封」的批准数，而凭一个顺手写的数字截断起草，会让「第 21 个人没有草稿」在报告上读成「她不够格」 |

    留下来的五条**两边都判**，因为它们不是发送前序，是「这封信该不该写」：

    1. **入口性质**（:data:`DRAFTABLE_ENTRY_KINDS`）：X 不产正常草稿。
    2. **等级**：``Grade.X`` 不产正常草稿 —— 与第 1 条各判各的，因为一条
       ``entry_kind=NAMED_ROLE`` 而 ``grade=X`` 的路线是「有具名角色但没判出合格
       证据」，那是另一件事，理由也应当不同。
    3. **证据**：``evidence_ids`` 为空的路线不得起草 —— 一封说不出凭什么写的信，
       事后无法解释。
    4. **抑制与停发**：与发送侧逐字同一处（``store.is_suppressed`` 与
       ``stopped_by_reply``）。写给一个退订过的人，不值得任何人去看。
    5. **批内去重**：同一个账号、同一个收件人在这一批里只进一次。

    ## 去重与排序合成一趟

    候选按 ``(等级顺序, 收件人, 账号)`` 排序后**走一趟**，边走边记下见过的账号与
    收件人：留下的那条一定是这个账号（这个人）能挑到的最强的一条，而落选的那些
    带得出一句说得出口的理由（「同一个账号已经有更强的一条」）。两趟（先去重再
    排序）会得到同一批行，但它把「谁留下了」与「按什么顺序留」写成了两处判断 ——
    两处迟早在某次改动后给出两个答案。

    ## 行序按 **(等级顺序, 收件人)**

    §8 逐字：「A/B/C/D **按批准策略排序**」。这个序是**冻结的**：同一份输入两次
    跑出同一份计划，所以「第 3 个是谁」在两次读数里是同一件事。
    """
    moment = to_utc(at)
    planned: list[tuple[tuple[int, str, str], DraftTarget]] = []
    skipped: list[SkippedEntry] = []
    taken_accounts: dict[str, int] = {}
    taken_recipients: dict[str, int] = {}

    def skip(route: ContactRoute, recipient: str, reason: str) -> None:
        skipped.append(
            SkippedEntry(
                route_ref=route.account_id,
                account_id=route.account_id,
                recipient=recipient,
                stage=stage,
                reason=reason,
            )
        )

    candidates: list[tuple[tuple[int, str, str], ContactRoute, str]] = []
    for route in routes:
        recipient = normalise_email(route.email)
        if route.entry_kind not in DRAFTABLE_ENTRY_KINDS:
            skip(route, recipient, f"入口性质不是可起草的那三种：{route.entry_kind.value}")
            continue
        if route.grade is Grade.X:
            skip(route, recipient, "等级是 X：未判定或不该联系，不产正常草稿")
            continue
        if not route.evidence_ids:
            skip(route, recipient, "这条路线没有证据")
            continue
        if store.is_suppressed(recipient, at=moment):
            skip(route, recipient, "收件人在抑制名单上")
            continue
        stopped = stopped_by_reply(store=store, workspace=workspace, recipient=recipient)
        if stopped:
            skip(route, recipient, stopped)
            continue
        candidates.append(((grade_rank(route.grade.value), recipient, route.account_id), route, recipient))

    for key, route, recipient in sorted(candidates, key=lambda row: row[0]):
        rank = key[0]
        stronger_account = taken_accounts.get(route.account_id)
        if stronger_account is not None and stronger_account <= rank:
            skip(
                route,
                recipient,
                f"同一个账号已经有更强的一条路线进了这一批（等级序 {stronger_account}）",
            )
            continue
        stronger_recipient = taken_recipients.get(recipient)
        if stronger_recipient is not None and stronger_recipient <= rank:
            skip(
                route,
                recipient,
                f"同一个人已经有更强的一条路线进了这一批（等级序 {stronger_recipient}）",
            )
            continue
        family, match = family_for(
            families, stage=stage, role_class=route.role_class.value
        )
        if family is None:
            skip(route, recipient, f"这一阶段还没有任何模板族（stage={stage.value}）")
            continue
        if match is FAMILY_MATCH_STAGE and route.grade.value not in FALLBACK_TO_STAGE_FOR_GRADES:
            skip(
                route,
                recipient,
                f"这一阶段没有面向 {route.role_class.value!r} 的模板族，而等级 "
                f"{route.grade.value} 不得套用关键联系人的那一族（不冒充具名决策人）",
            )
            continue
        taken_accounts[route.account_id] = rank
        taken_recipients[recipient] = rank
        planned.append(
            (
                key,
                DraftTarget(
                    route_ref=route.account_id,
                    account_id=route.account_id,
                    organisation=route.organisation,
                    recipient=recipient,
                    stage=stage,
                    entry_kind=route.entry_kind,
                    route_grade=route.grade,
                    family_match=match,
                    family_id=family.family_id,
                    writing_policy_id=family.writing_policy_id,
                    evidence_ids=tuple(route.evidence_ids),
                    review_reasons=review_reasons(route),
                ),
            )
        )

    ordered = tuple(target for _key, target in sorted(planned, key=lambda row: row[0]))
    return DraftingPlan(
        campaign_id=campaign_id,
        wave_id=wave_id,
        workspace=workspace,
        scene_id=scene_id,
        scene_version=scene_version,
        stage=stage,
        targets=ordered,
        skipped=tuple(skipped),
    )


# --- 二、生成 ----------------------------------------------------------------


@dataclass(frozen=True)
class GenerationReceipt:
    """一次生成动作的回执。§5 逐字要求「按条目存生成回执、输入/模型/策略版本、
    正文与附件摘要、幂等键」。"""

    draft_id: str
    draft_version: int
    #: ``generated`` / ``review_only`` / ``held`` / ``unchanged`` / ``failed``。
    #:
    #: ``review_only`` 是**写出来了但还不够发**：稿子落库、``state=REVIEW_ONLY``，
    #: 而它 **写得出正文** —— 与 ``held`` 的区别正在这里，也在处置上。§8 逐字：
    #: 「准入、当前性或绑定不够时只给明确标注的 review-only/held 状态，不称
    #: outreach-ready」。那一句把两者并列，因为它们**都不是** outreach-ready，
    #: 而把它们合成一个判决会让「该去补输入」与「该去核那条路线」变成同一件事。
    outcome: str
    generation_key: str
    writer_id: str
    policy_version: str
    body_sha256: str
    subject_sha256: str
    reason: str = ""
    #: 这一版取代了哪一版（0 = 没有取代任何人）。
    #:
    #: 「上一版被取代了」这件事**不写进上一版那一行**：一版草稿的内容是不可改的
    #: （``OutreachStore.put_draft`` 对「同版次不同内容」报错，理由见那里），而
    #: 就地把它标成 ``superseded`` 正是那样一次改写 —— 它会连带改掉正文摘要所
    #: 绑定的那一行。方向由**新版本**记录：``supersedes`` 指向旧的，回执指向它。
    superseded_version: int = 0

    @property
    def receipt_ref(self) -> str:
        """这份回执在盘上的名字。**内容寻址** —— 由下面这些字段派生。

        它与 :attr:`~marketops.domain.outreach.OutreachDraft.generation_receipt_ref`
        是**同一个值**：草稿上那个字段指向的就是描述它的这份回执，两处各算一遍
        就迟早会漂。所以草稿的构造顺序是「先算出回执，再把它的 ref 填进草稿」。
        """
        return check_canonical_id(
            "gen-"
            + sha256_text(
                _SEP.join(
                    [
                        self.draft_id,
                        str(self.draft_version),
                        self.generation_key,
                        self.outcome,
                        self.writer_id,
                        self.policy_version,
                        self.body_sha256,
                        self.subject_sha256,
                        str(self.superseded_version),
                        self.reason,
                    ]
                )
            )[:24],
            "generation_receipt_ref",
        )

    @property
    def wrote_a_row(self) -> bool:
        """这条回执**对应库里的一行**吗 —— 也就是「这一趟真的往库里写了吗」。

        判据是 ``draft_version > 0`` 且不是 ``unchanged``：

        * ``generated`` / ``review_only`` —— 写下第 N 版，有行。
        * ``held`` **带正文** —— 也写下第 N 版（``state=HELD``），**有行**。「这一稿
          不能用」不等于「没写」：那一行必须留在库里，因为它就是「按这份输入写出来的
          是这个样子」这个结论本身，也是下一次有人问「为什么这一条没发」时唯一的读物。
        * ``held`` **不带正文** —— ``draft_version`` 是 0：正文不得为空，这一条**没有
          行可写**，而回执照写（§5 要求 hold 的理由读得回来）。
        * ``held`` 而**输入逐字没变**（一次性就在库里的 hold）—— ``draft_version``
          也是 0：这一趟什么都没写，库里那一版是**上一次**写的，本次回执的理由里
          带着它那一份回执的 ref。
        * ``unchanged`` —— 说的是库里**已经**那一行。 ``draft_version`` > 0（它必须
          说得出自己在说哪一版），而这一趟什么都没写 —— 所以这一格要单独排掉。
        * ``failed`` —— 没有行。

        ## 它**不等于** ``outcome == "generated"``

        后者会把每一次 hold 都漏掉，而 hold 恰恰是最需要有人去看的那一批（§5 逐字：
        「个性化缺失时用已批准的一般措辞或 hold，不得猜」）。想知道「这一稿能不能发」，
        问的是 :attr:`sendable`，不是这一条 —— 「这一批写了几行」与「这一批有几行能发」
        是两个数，合成一个会让它们在报告上互相打架。
        """
        return self.outcome != "unchanged" and self.draft_version > 0

    @property
    def sendable(self) -> bool:
        """这一稿够进发送队列吗。**只有 ``generated`` 是。**"""
        return self.outcome == "generated"


@dataclass(frozen=True)
class DraftingOutcome:
    """一整批生成的结果。**一条失败不得毁掉整批**（§5 逐字）。"""

    receipts: tuple[GenerationReceipt, ...]
    drafts: tuple[OutreachDraft, ...] = field(default_factory=tuple)

    @property
    def rows_written(self) -> int:
        """这一趟往库里写了几行。**由回执推出来**，不由 ``drafts`` 数。

        ``drafts`` 里含 ``unchanged`` 的那些（它们是库里已有的行，被端出来是为了让
        调用方读得到它们），所以 ``len(drafts)`` 在任何一次重跑里都大于真正写下的行数
        —— 而那正是「重跑看起来又写了一整批」这个假读数的成因。这一格与
        :attr:`GenerationReceipt.wrote_a_row` 逐条对齐，两者只有一个判决处。
        """
        return sum(1 for receipt in self.receipts if receipt.wrote_a_row)

    @property
    def summary_line(self) -> str:
        counts: dict[str, int] = {}
        for row in self.receipts:
            counts[row.outcome] = counts.get(row.outcome, 0) + 1
        return " ".join(f"{key}={counts[key]}" for key in sorted(counts))


#: 业务回执上的六个词。**五个来自 §B13 那一句**
#: （「以冻结对象集对账 ``accepted / held / excluded / failed / reused``」），
#: 第六个是 §8 自己拆出来的一半（§8 逐字：「准入、当前性或绑定不够时只给明确标注的
#: ``review-only`` / ``held`` 状态，不称 outreach-ready」）。
#:
#: 六格是**一个划分**：计划里的每一条目标最后恰好落在前五格之一，``excluded`` 是
#: 计划**之外**那一侧（被筛选挡下的那些）。划分有一条可执行的判据 —— 见
#: :func:`drafting_receipt` 的对账。
DISPOSITIONS: tuple[str, ...] = (
    "accepted",
    "review_only",
    "held",
    "excluded",
    "failed",
    "reused",
)

#: ``GenerationReceipt.outcome`` → 业务档位。**两张表都是穷尽的**，查不到就抛 ——
#: 见 :func:`drafting_receipt`：给 ``outcome`` 加一个取值的实现必须同时回答
#: 「它在业务回执上算哪一格」，而漏答的后果是回执**少一条**。
#:
#: ``review_only`` 与 ``held`` 各占一格，而不是合成一个「not ready」：它们的**下一步
#: 相反**（去核那条路线 / 去补那个输入）。这两格合起来才是 §8 那句里的
#: 「review-only/held」那一对。
_DISPOSITION_BY_OUTCOME: dict[str, str] = {
    "generated": "accepted",
    "review_only": "review_only",
    "held": "held",
    "failed": "failed",
    "unchanged": "reused",
}


@dataclass(frozen=True)
class DraftingReceipt:
    """一次起草的**业务回执**：这一批里每一条去了哪一格。

    ## 它为什么不是「``GenerationReceipt`` 再加一列」

    :class:`GenerationReceipt` 是**一次生成动作**的记录（幂等键、正文摘要、策略
    版本），它答的是「这一稿是谁按哪一版写出来的」。业务回执答的是另一个问题：
    「**这一批的主体**每一条去哪了」—— 而问这个问题的人手里拿的是**计划**。
    两者不是同一个粒度：``unchanged`` 的那一条这趟没有生成动作，而它在业务上照样
    要有个去处（``reused``）；被筛选挡下的那些**根本不在计划里**，所以它们在这份
    回执上只有一句话的理由（``excluded``），没有一个草稿 id。

    ## 冻结对象集在对账里的位置

    ``targets`` 是这一批的**收件人**，逐条，顺序就是计划里的顺序。对账的含义是：
    它里面每一条在 :attr:`rows` 里**恰好出现一次**，而 :attr:`rows` 里不许出现它
    以外的收件人。两半都要查 —— 只查一半的实现会让「回执总数对了」与「每一条都
    有话说」变成两件事（一条漏报 + 一条多报，总数可以恰好相抵）。
    """

    campaign_id: str
    wave_id: str
    stage: OutreachStage
    #: 冻结的对象集：这一批要起草的收件人，按计划的顺序。
    targets: tuple[str, ...]
    #: 逐条的档位：``(收件人, 档位, 理由)``。理由为空串 ⟺ 档位是 ``accepted``
    #: 或 ``reused``（那两格没有「为什么」可说）。
    rows: tuple[tuple[str, str, str], ...]
    #: 被筛选挡下的：``(收件人, 理由)``。它们**不在** ``targets`` 里。
    excluded: tuple[tuple[str, str], ...]
    #: 这一趟往库里写了几行（:attr:`DraftingOutcome.rows_written`）。
    #: §B13 逐字要的「及**下一层写入**」就是这一格。
    rows_written: int

    @property
    def counts(self) -> dict[str, int]:
        """六个词各几条。键是**全部六个**，一条都没有的那些是 0 —— 缺键会让
        「这一批没有失败的」与「这份回执不认识失败这一格」在读数上一样。"""
        counts = {name: 0 for name in DISPOSITIONS}
        for _recipient, disposition, _reason in self.rows:
            counts[disposition] += 1
        counts["excluded"] = len(self.excluded)
        return counts

    @property
    def summary_line(self) -> str:
        counts = self.counts
        parts = " ".join(f"{name}={counts[name]}" for name in DISPOSITIONS)
        return f"{parts} rows_written={self.rows_written}"


def drafting_receipt(plan: DraftingPlan, outcome: DraftingOutcome) -> DraftingReceipt:
    """把一份计划与一次生成对成一张业务回执。**对不上就抛，不修**。

    ## 为什么这里要抛，而不是把缺失的那几条补成一行

    补一行的实现是「查得到就写真的、查不到就写个占位的」—— 它在报告上产出的东西
    与本函数**一模一样**，而两者回答的问题不同：这份回执的全部意义是「每一条都有
    话说」，一个在漏报时自己造一条的实现让这句话永远为真，于是它**再也测不出来**。

    ## 它查的两件事

    1. **``outcome`` 在业务回执上有位置。** 一个没有位置的新取值会让这一条**静默
       消失**（``dict.get`` 返回 ``None``、跳过），而总数少一条的报告看起来只是
       「这一批小了一点」。
    2. **计划与回执覆盖同一个收件人集合。** 两个方向都查：漏报的（计划里有、回执里
       没有）与多报的（回执里有、计划里没有）。只查一个方向时，一漏一多可以相抵。
    """
    expected: dict[str, str] = {}
    for target in plan.targets:
        expected[_draft_id_for(plan.wave_id, target.recipient)] = target.recipient

    order = {draft_id: index for index, draft_id in enumerate(expected)}
    numbered: list[tuple[int, tuple[str, str, str]]] = []
    seen: set[str] = set()
    for receipt in outcome.receipts:
        recipient = expected.get(receipt.draft_id)
        if recipient is None:
            raise ConfigError(
                f"生成回执 {receipt.draft_id!r} 不在这一份计划的目标集里。\n"
                "  计划是**冻结的对象集**：回执只能报计划点过名的那几条。一条计划外的"
                "条目会让「这一批的主体是谁」在一份回执里出现两个答案。"
            )
        disposition = _DISPOSITION_BY_OUTCOME.get(receipt.outcome)
        if disposition is None:
            raise ConfigError(
                f"生成回执的 outcome={receipt.outcome!r} 在业务回执上没有对应的档位。\n"
                f"  有档位的只有：{'、'.join(sorted(_DISPOSITION_BY_OUTCOME))}。\n"
                "  给 ``outcome`` 加一个取值，就要同时回答「它在业务回执上算哪一格」——"
                "漏答的那一条会**静默消失**，而报告上只显示「这一批小了一点」。"
            )
        seen.add(receipt.draft_id)
        numbered.append((order[receipt.draft_id], (recipient, disposition, receipt.reason)))

    missing = sorted(set(expected) - seen)
    if missing:
        raise ConfigError(
            "计划里这些目标没有任何生成回执："
            + "、".join(f"{expected[key]}（{key}）" for key in missing)
            + "。\n  「这一条没发」必须读得回来：漏报的一条在报告上会读成「这一批里"
            "本来就没有它」，而它与「它被跳过了、因为……」是不同的两件事。"
        )

    return DraftingReceipt(
        campaign_id=plan.campaign_id,
        wave_id=plan.wave_id,
        stage=plan.stage,
        targets=tuple(target.recipient for target in plan.targets),
        rows=tuple(row for _index, row in sorted(numbered, key=lambda item: item[0])),
        excluded=tuple((row.recipient, row.reason) for row in plan.skipped),
        rows_written=outcome.rows_written,
    )


#: 回执文件的后缀。与母池回执（``application/motherpool.py``）同一处置：
#: 回执是一份**可被引用**的工件，所以它有文件名、有后缀、内容寻址。
GENERATION_RECEIPT_SUFFIX = ".json"


def generation_receipt_path(receipts_dir: Path, receipt: GenerationReceipt) -> Path:
    """这份回执在盘上的位置。**id 与文件名的主干是同一个名字。**"""
    return receipts_dir / f"{receipt.receipt_ref}{GENERATION_RECEIPT_SUFFIX}"


def write_generation_receipts(
    receipts_dir: Path, receipts: Sequence[GenerationReceipt]
) -> tuple[Path, ...]:
    """把一批回执写到 ``receipts_dir`` 下，返回路径。**重放不重写、不覆盖。**

    ## 为什么它必须存在

    §5 逐字要求「按条目存生成回执」。而
    :attr:`~marketops.domain.outreach.OutreachDraft.generation_receipt_ref`
    是一个**指向的东西** —— 一个指向不存在的东西的引用，与一句「我们存过回执了」
    在盘上长得一样，而它挡不住任何事。所以那个 ref 必须有生产者。

    ## 同名不同内容 → 拒绝，不覆盖

    路径是内容寻址的，所以同一个名字下出现另一份内容只有一种解释：盘上那份被
    写过。覆盖它会销毁那次改动的证据，而那正是要回答「这一版正文是什么时候被谁
    换掉的」的东西。所以这里与 ``write_receipt`` 同一条：**重放跳过，异内容报错**。
    """
    written: list[Path] = []
    for receipt in receipts:
        path = generation_receipt_path(receipts_dir, receipt)
        payload = json.dumps(
            _receipt_payload(receipt), ensure_ascii=False, indent=2, sort_keys=True
        )
        if path.exists():
            if path.read_text(encoding="utf-8") != payload + "\n":
                raise ConfigError(
                    f"{path} 已经存在，而内容与本次这份回执不同。\n"
                    "路径按回执内容寻址，所以同一个名字下出现另一份内容只有一种解释："
                    "盘上那份被写过。覆盖它会销毁「谁改了哪一版」的证据。"
                )
            written.append(path)
            continue
        receipts_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8", newline="\n")
        written.append(path)
    return tuple(written)


def _receipt_payload(receipt: GenerationReceipt) -> dict[str, object]:
    """回执落盘的形状。``receipt_ref`` 一并写进去 —— 读的人要先能验自己。"""
    return {
        "receipt_ref": receipt.receipt_ref,
        "draft_id": receipt.draft_id,
        "draft_version": receipt.draft_version,
        "outcome": receipt.outcome,
        "generation_key": receipt.generation_key,
        "writer_id": receipt.writer_id,
        "policy_version": receipt.policy_version,
        "body_sha256": receipt.body_sha256,
        "subject_sha256": receipt.subject_sha256,
        "superseded_version": receipt.superseded_version,
        "reason": receipt.reason,
    }


def load_generation_receipt(path: Path) -> GenerationReceipt:
    """读回一份回执，并验它**自己**（``receipt_ref`` 与内容自洽）。

    Raises:
        ConfigError: 读不出来、字段不认识、或 ref 与内容对不上。
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ConfigError(f"{path} 读不回来：{type(error).__name__}: {error}") from error
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} 里不是一个对象。")
    body = {key: value for key, value in payload.items() if key != "receipt_ref"}
    try:
        receipt = GenerationReceipt(**body)
    except TypeError as error:
        raise ConfigError(f"{path} 的字段与回执对不上：{error}") from error
    if receipt.receipt_ref != payload.get("receipt_ref"):
        raise ConfigError(
            f"{path} 的内容与它自称的 ref 对不上 —— 盘上那份被改过，"
            "或者它本来就不是按本模块算出来的。"
        )
    return receipt


def generate_drafts(
    *,
    plan: DraftingPlan,
    store: OutreachStore,
    writer: DraftWriter,
    campaign_id: str,
    locale: str,
    facts_by_account: dict[str, tuple[tuple[str, str, str], ...]],
    organisation_by_account: dict[str, str],
    receipts_dir: Path,
    at: datetime,
    policy_version: str = "",
) -> DraftingOutcome:
    """给起草计划里的每一条路线写一稿。**逐条独立**：一条失败不影响其余的。

    ## 七个「必须停下来」的条件

    1. **清单上找不到这一族模板或这一份策略** —— 一条不存在的策略意味着
       没有人审过这些话术。
    2. **写作者抛异常** —— 记成 ``failed`` 并把异常文本带上。不吞。
    3. **写作者什么都没写也没有理由** —— 记成 ``failed``：那是一个坏了的实现，
       不是一个结论。
    4. **``held_reason`` 非空** —— 稿子（若写得出正文）落库但 ``state=HELD``，
       **不进清单**。§5 逐字：「个性化缺失时用已批准的一般措辞或 hold，不得猜」。
       写不出正文的 hold **没有行可写**（``OutreachDraft`` 的正文不得为空），
       但回执照写 —— 两者都是「这一条没发」，而该做的事不同。
    5. **目标带着 review 理由** —— 稿子落库但 ``state=REVIEW_ONLY``，
       **不进清单**。理由在**路线**那一侧（见 :func:`review_reasons`），与第 4 条
       的方向相反：那一条要去补输入，这一条要去核路线。
    6. **输入没变** —— 已经有一稿的 ``generation_key`` 与本次逐字相同、
       **且状态档位也相同** → ``unchanged``，不写第二版。§5 逐字：「只给新增的
       或输入变了的对象生成」。
    7. **写作者引用了它没有被给定的东西** —— ``facts_used`` 或 ``claim_ids``
       里出现了请求中不存在的那一对。§5 逐字：「不得编造」。这一条**在 hold
       之前**判，见 :func:`_uncited_inputs`：hold 可以带正文，带正文就写行，
       而这条判据守的是「库里的每一行只引用被给定过的东西」。

    ## 第 6 条为什么要连**状态档位**一起比

    一份「输入逐字没变、而这一条路线刚刚被判定为不够」的稿子，若按
    ``generation_key`` 相同就判 ``unchanged``，那一稿会**原样留在库上**、
    状态仍是 ``GENERATED``，而 ``prepare_wave`` 只放行 ``GENERATED`` ——
    于是「这条路线现在不够了」这件事在发送侧**一点痕迹都没有**。这与 §8 那句
    「新证据或策略使旧草稿过时，不能静默换文」是同一条：旧版**不改也不删**
    （它当时确实是那么写的），新版带着新档位落成第 N+1 版，方向由回执记下。

    ## ``held_reason`` 先判、空正文后判，顺序有后果

    一个「我缺一个输入」的写作者**通常什么都写不出来**。先判空正文的实现会把
    每一次 hold 都记成 ``failed``，而 ``failed`` 的处置是去修写作者、hold 的处置
    是去补输入 —— 报告上两者都读作「这一条没发」，该做的事相反。

    ## 它**不**碰的三样东西

    收件人、发件人、权限 —— :class:`~marketops.ports.outreach.DraftRequest`
    根本没有这些字段。发送状态同理：草稿上唯一的那个布尔是
    ``send_authorized=False``，而它是 ``Literal[False]``。

    ## ``receipts_dir`` 不是一个可选的日志目录

    §5 逐字要求「按条目存生成回执」，而草稿上的
    ``generation_receipt_ref`` 指向的就是这里写下的那一份。给它一个
    ``None`` 缺省会让「这一次没存回执」与「回执存了」在调用点看不出区别 ——
    于是那个 ref 迟早指向一个不存在的东西。所以它是必填的。
    """
    moment = to_utc(at)
    families = {family.family_id: family for family in store.families()}
    policies = {policy.policy_id: policy for policy in store.writing_policies()}
    live_claims = tuple(
        claim for claim in store.approved_claims() if claim.is_live_at(moment)
    )

    receipts: list[GenerationReceipt] = []
    drafts: list[OutreachDraft] = []

    for target in plan.targets:
        draft_id = _draft_id_for(plan.wave_id, target.recipient)
        # 计划**点名了**族，所以这里不再是「挑一族」而是「确认它还在」：``family_id``
        # 为空只可能来自一个不经过 :func:`plan_drafting` 的计划，而那条路已经没有了。
        # 保留 ``family_for_stage`` 这个兜底会让「清单换了、计划还指着旧族」在
        # 这里被**静默换掉**，而 §8 逐字要的是「新证据或策略使旧草稿过时，不能静默换文」。
        family = families.get(target.family_id)
        if family is None:
            receipts.append(
                _bad(draft_id, f"清单上的模板族不存在：{target.family_id!r}", writer, policy_version)
            )
            continue
        policy = policies.get(family.writing_policy_id)
        if policy is None:
            receipts.append(
                _bad(draft_id, f"写作策略 {family.writing_policy_id!r} 不存在", writer, policy_version)
            )
            continue

        request = DraftRequest(
            account_id=target.account_id,
            organisation=organisation_by_account.get(target.account_id, target.organisation),
            family=family,
            policy=policy,
            stage=plan.stage,
            locale=locale,
            claims=tuple(
                claim for claim in live_claims if claim.claim_id in family.approved_claim_ids
            ),
            facts=facts_by_account.get(target.account_id, ()),
        )
        key = request.generation_key(
            writer_id=writer.writer_id, policy_version=policy_version
        )

        existing = _latest_draft(store, draft_id)
        if existing is not None and existing.generation_key == key:
            settled = _settled_outcome(existing.state, review_only=target.review_only)
            if settled is not None:
                if settled == "held":
                    why = (
                        "这一条在本次之前就已经是 hold：输入逐字没变，没有再写一版。"
                        f"那一版的回执是 {existing.generation_receipt_ref}，那里记着为什么。"
                    )
                else:
                    why = "；".join(target.review_reasons)
                receipts.append(
                    GenerationReceipt(
                        draft_id=draft_id,
                        # 这一趟**没有**产出任何版本，所以是 0 —— 与「hold 而写不出
                        # 正文」那一格同一种读法。库里那一版是上一次写的，回执的理由
                        # 里带着它那一份回执的 ref；报 ``existing.draft_version`` 会让
                        # :attr:`GenerationReceipt.wrote_a_row` 说这一趟写了东西。
                        draft_version=0 if settled == "held" else existing.draft_version,
                        outcome=settled,
                        generation_key=key,
                        writer_id=writer.writer_id,
                        policy_version=policy_version,
                        body_sha256=existing.body_sha256,
                        subject_sha256=existing.subject_sha256,
                        reason=why,
                    )
                )
                drafts.append(existing)
                continue

        try:
            proposal = writer.draft(request)
        except Exception as error:  # noqa: BLE001 — 原因逐字带上，不吞
            receipts.append(
                _bad(
                    draft_id,
                    f"写作者失败：{type(error).__name__}: {error}",
                    writer,
                    policy_version,
                    key,
                )
            )
            continue

        # 引用判据在 hold 之前判，因为它的判词是「库里的每一行 ``facts_used``
        # 只包含**被给定**的引用」—— 那是一条关于库的不变量，而 hold 可以带正文，
        # 带正文就写行。先判 hold 的实现会把一条编出来的证据 id 连同 ``HELD``
        # 一起落库，于是那条不变量只在有人去读的时候才成立。
        #
        # 顺序的代价是：一个既 hold 又编引用的写作者会被告知「引用错了」而不是
        # 「你还差一个输入」。两者都指向「换一个写作者」，而后者会让人去补一个
        # 输入、再跑一遍、再收到同一条 failed。
        uncited = _uncited_inputs(request, proposal)
        if uncited:
            receipts.append(
                _bad(draft_id, f"写作者{uncited}", writer, policy_version, key)
            )
            continue

        # ``held_reason`` 先判，正文后判 —— 顺序在这里是一件有后果的事。
        # 一个「我缺一个输入」的写作者**通常什么都写不出来**：先判空正文的实现会
        # 把每一次 hold 都记成 ``failed``（「写作者既没写正文也没给理由」），
        # 而 ``failed`` 的处置是去修写作者，hold 的处置是去补输入。两者在报告上
        # 都是「这一条没发」，而该做的事相反。
        held = bool(proposal.held_reason)
        if not held and not proposal.body.strip():
            receipts.append(
                _bad(
                    draft_id,
                    "写作者既没有写出正文，也没有给出一句话的理由",
                    writer,
                    policy_version,
                    key,
                )
            )
            continue

        version = 1 if existing is None else existing.draft_version + 1
        if held and not proposal.body.strip():
            # 一次**不留稿**的 hold。``OutreachDraft`` 的正文不得为空（一封空的
            # 商业信不是一个草稿），所以这一条**没有行可写** —— 而它仍然要有回执：
            # §5 要求「个性化缺失时 hold」，「为什么这一条没发」必须读得回来。
            receipts.append(
                GenerationReceipt(
                    draft_id=draft_id,
                    draft_version=0,
                    outcome="held",
                    generation_key=key,
                    writer_id=proposal.writer_id or writer.writer_id,
                    policy_version=policy_version,
                    body_sha256="",
                    subject_sha256="",
                    reason=proposal.held_reason,
                )
            )
            continue

        # 三种档位，而判序**有后果**：hold 压过 review-only。一个说「我缺一个输入」的
        # 写作者给出的正文是不能用的，而 review-only 说的是「这稿能用，但要有人先核
        # 那条路线」—— 对一份不能用的一稿说「可以去核路线了」，是把人支到错的那一步上。
        # 反过来问「这一条能不能发」：两者都不能，所以两者都不许叫 outreach-ready。
        outcome = "held" if held else ("review_only" if target.review_only else "generated")
        # 回执上的 ``reason`` 答的是「为什么这一条是这个结论」。``review_only`` 的
        # 理由住在**路线**那一侧，所以它逐条抄自计划，而不是由写作者给 —— 写作者
        # 看不见绑定与时效，让它去说这两件事只会得到一句编的。
        reason = (
            "；".join(target.review_reasons) if outcome == "review_only" else proposal.held_reason
        )

        # 回执**先算**，草稿再把它的 ref 填进去 —— 两处各算一遍就迟早会漂，
        # 而草稿上那个字段的全部意义是「指向描述它的那一份回执」。
        receipt = GenerationReceipt(
            draft_id=draft_id,
            draft_version=version,
            outcome=outcome,
            generation_key=key,
            writer_id=proposal.writer_id or writer.writer_id,
            policy_version=policy_version,
            body_sha256=sha256_text(proposal.body),
            subject_sha256=sha256_text(proposal.subject),
            reason=reason,
            superseded_version=version - 1 if version > 1 else 0,
        )
        draft, why = _as_draft(
            receipt=receipt,
            proposal=proposal,
            target=target,
            plan=plan,
            campaign_id=campaign_id,
            locale=locale,
            family=family,
            policy=policy,
            version=version,
            moment=moment,
        )
        if draft is None:
            # 写作者给的东西**构造不出一份草稿**（空主题、主题里有换行……）。
            # 这一条要**逐条**失败：让 pydantic 的异常穿出去，一份坏提议会把整批
            # 的稿子一起带走 —— 而这一批里其余的每一稿都是好的。
            receipts.append(
                _bad(draft_id, f"写作者给的草稿不合法：{why}", writer, policy_version, key)
            )
            continue
        store.put_draft(draft)
        # 上一版**不删也不改**：它是一份真的生成过的稿子，而版次的内容是不可改的
        # （``put_draft`` 对「同版次不同内容」报错 —— 就地标成 ``superseded``
        # 正是那样一次改写）。「第 2 版取代了第 1 版」由回执上那一个字段记录方向。
        receipts.append(receipt)
        drafts.append(draft)

    # 回执在**所有条目都跑完之后**才落盘：一次生成里已经产生的事实不该因为
    # 后面某一条失败而只写进去一半。
    write_generation_receipts(receipts_dir, receipts)
    return DraftingOutcome(receipts=tuple(receipts), drafts=tuple(drafts))


def _latest_draft(store: OutreachStore, draft_id: str) -> OutreachDraft | None:
    """这一份稿子的最新一版。版本号从 1 往上**连续**（见 ``OutreachDraft`` 的
    ``supersedes`` 校验），所以遇到第一个缺口就停。"""
    latest: OutreachDraft | None = None
    version = 1
    while True:
        found = store.draft(draft_id, version)
        if found is None:
            return latest
        latest = found
        version += 1


def family_for_stage(families, stage: OutreachStage):
    """按阶段挑一族模板。**这一处只在计划没有点名族的时候用。**

    计划里的 ``family_id`` 为空是正常的第一版形态（活动还没建）。挑法的规则：
    阶段对上、且 ``family_id`` 字典序最小 —— 确定性优先，「随便挑一个」
    会让同一份输入两次跑出两封信。

    ## 它是**公开的**，而且理由不是「外部要用」

    产品环那一段（``adapters/mcp_server/outreach.py``）在建活动时要当场写下
    ``family_id``，而它写下的那个 id 必须与这里后来真正用来写信的那一族**是同一
    个**。让它照抄这条挑法，改规则的那一天就有两个答案，而**没有任何一处会红**
    —— 活动的 ``family_id`` 是一句声明，写信用的是另一族，报告上两处各自都说得通。
    所以那一段调的是这个函数，而不是这条规则。

    公开它是为了让那次调用读起来不像一次越界。下划线版本留着的那些函数是「外部
    没有正当理由碰的」；这一个有。
    """
    candidates = sorted(
        (family for family in families.values() if family.stage is stage),
        key=lambda family: family.family_id,
    )
    return candidates[0] if candidates else None


def _uncited_inputs(request: DraftRequest, proposal: DraftProposal) -> str:
    """写作者引用了它**没有被给定**的东西吗？返回一句话的理由，或空串。

    §5 逐字：「不得编造痛点、设备、预算、责任人姓名、购买意向或产品能力」。
    而 ``DraftProposal`` 上那两格 —— ``facts_used``（用了哪一条证据）与
    ``claim_ids``（说了哪一句已批准的话术）—— 正是这句话的落点。

    端口把这条判据写成了**调用方的责任**（``claim_ids`` 那一句是逐字写的：
    「必须**是请求里 ``claims`` 的一个子集 —— 由调用方校验」），而在加这个函数
    之前，**没有任何调用方执行它**。这正是本仓说的那种「只写在文档里的边界」：
    一个会把证据 id 编得像模像样的写作者在这里一条红都不会有，而它的稿子会带着
    一条指向虚空的引用落库 —— §5 那条要求以「零条待核实引用」或「引用查不到」
    的形态静默失效，报告上看起来一切正常。

    判据是**成对**的：``facts_used`` 的 ``(事实名, 证据 id)`` 必须逐对出现在
    请求的 ``facts`` 里。只比 id 会漏掉「拿了正确的 id、配了一个不相干的
    事实名」—— 而下游是按这一对去查的。

    **理由里只报条数，不带写作者给的字符串。** 理由会被落盘留档，而这里面
    每一格都是``写作者``（模型）的输出：一个模型可以把客户正文塞进事实名
    那一格。条数足够把这一次生成定位回来 —— 请求在回执的 ``generation_key``
    上。
    """
    given_facts = {(name, evidence_id) for name, _value, evidence_id in request.facts}
    given_claims = {claim.claim_id for claim in request.claims}
    invented_facts = set(proposal.facts_used) - given_facts
    invented_claims = set(proposal.claim_ids) - given_claims
    parts = []
    if invented_facts:
        parts.append(f"引用了 {len(invented_facts)} 条没有被给定的事实")
    if invented_claims:
        parts.append(f"引用了 {len(invented_claims)} 条没有被批准的话术")
    return "、".join(parts)


#: 回执上的 ``outcome`` → 草稿上该落成哪一个 :class:`DraftState`。
#:
#: 一张表，而不是在 :func:`_as_draft` 里再写一遍那个三元表达式：同一个判决在两处
#: 各写一遍，迟早会在有人给 ``outcome`` 加一个取值时漏掉一处 —— 而漏掉的那一处是
#: **静默**的：一份 ``review_only`` 的稿子落成 ``GENERATED``，然后被
#: :func:`prepare_wave` 放行。表是穷尽的，查不到就 ``KeyError``，那是这一格唯一
#: 可接受的失效方式。
#:
#: ``failed`` 与 ``unchanged`` **不在这张表里**，因为它们不产生新的一版：前者没有
#: 行可写，后者写的就是库里那一行。它们出现在这里会让「回执说失败、草稿说能发」
#: 这种组合构造得出来。
_DRAFT_STATE_BY_OUTCOME: dict[str, DraftState] = {
    "generated": DraftState.GENERATED,
    "review_only": DraftState.REVIEW_ONLY,
    "held": DraftState.HELD,
}


def _settled_outcome(state: DraftState, *, review_only: bool) -> str | None:
    """输入逐字没变时，库里那一版**已经**把结论说死了，还是必须重写一版。

    返回 ``None`` = **必须重写**。返回一个字符串 = 不必重写，且本次的回执该报它。

    ## 为什么状态档位也要比，而 ``generation_key`` 不够

    一份「输入逐字没变、而这一条路线刚刚被判成不够」的稿子，若按 key 相同就报
    ``unchanged``，那一稿会**原样留在库上**、状态仍是 ``GENERATED`` —— 于是
    「这条路线现在不够了」这件事在发送侧一点痕迹都没有，而 ``prepare_wave`` 只放行
    ``GENERATED``。反过来同样成立：一条路线被核实之后，只按 key 比会让那份
    ``REVIEW_ONLY`` 永远发不出去。两个方向都要各写一版，方向由新版的档位记下。

    ## 为什么 ``HELD`` 是例外，且它报的是 ``held`` 而不是 ``unchanged``

    ``HELD`` 是**写作者**在**同一份 key** 下给的结论，所以它不会因为这一趟重跑而
    变 —— 重写一版只会得到同一份 hold，外加一个白涨的版次。所以它是「不必重写」。

    ## 为什么除了 ``GENERATED``，报的都不是 ``unchanged``

    ``unchanged`` 是 §5 那句「只给新增的或输入变了的对象生成」的落点，它读作
    「**这一条已经好了、我什么都没动**」—— 而 ``HELD`` 与 ``REVIEW_ONLY`` 都**不是
    「好了」**。让它们报 ``unchanged`` 的后果在**第二趟**上显出来：第一趟报
    ``held`` / ``review_only``，第二趟报 ``unchanged``，于是业务回执上那两格在重跑
    里空掉，而同一组冻结目标集在两次读数里给出两种结论 —— 「这一条不能发」这件事
    只在第一趟看得见。结论要跟着**对象的档位**走，不跟着「这一趟写了没有」走；
    后者由 :attr:`GenerationReceipt.wrote_a_row` 说，两者各答各的。
    """
    if state is DraftState.HELD:
        return "held"
    if state is not _state_for(review_only=review_only):
        return None
    return "review_only" if review_only else "unchanged"


def _state_for(*, review_only: bool) -> DraftState:
    """这一轮**本来会**落成的档位（写作者还没开口时的那一档）。

    它与 :data:`_DRAFT_STATE_BY_OUTCOME` 是同一件事的两半，所以 ``held`` 不在
    这里 —— ``_settled_outcome`` 先把它判掉了。
    """
    return DraftState.REVIEW_ONLY if review_only else DraftState.GENERATED


def _bad(
    draft_id: str,
    reason: str,
    writer: DraftWriter,
    policy_version: str,
    key: str = "",
) -> GenerationReceipt:
    return GenerationReceipt(
        draft_id=draft_id,
        draft_version=0,
        outcome="failed",
        generation_key=key,
        writer_id=writer.writer_id,
        policy_version=policy_version,
        body_sha256="",
        subject_sha256="",
        reason=reason,
    )


def _as_draft(
    *,
    receipt: GenerationReceipt,
    proposal: DraftProposal,
    target: DraftTarget,
    plan: DraftingPlan,
    campaign_id: str,
    locale: str,
    family: TemplateFamily,
    policy: WritingPolicy,
    version: int,
    moment: datetime,
) -> tuple[OutreachDraft | None, str]:
    """把一份提议变成一行草稿。**构造失败是一条结论，不是一个异常。**

    ``state`` **由回执推出来**（:data:`_DRAFT_STATE_BY_OUTCOME`），不由这里再判一遍：
    「这一版是什么档位」只许有一个判决处，而它就是 ``receipt.outcome``。两处各判一次
    的实现会在两处之间造出一段漂移，而漂移的那一侧（草稿上那个 ``state``）正是
    :func:`prepare_wave` 放行时唯一读的东西。

    ``OutreachDraft`` 有几条硬约束（正文不得为空、主题不得为空、主题里不得有换
    行……），而 ``DraftProposal`` 是**外部写作者**给的。让 pydantic 的异常从这里
    穿出去，一次坏提议会把整批的稿子一起带走 —— §5 逐字要求「单条失败不得破坏
    整批」，而一个能毁掉整批的实现正好违反它。

    返回的第二个值是**说得出的理由**：渲染住在
    :func:`~marketops.application.validation.one_line_reason`（「一次校验失败怎么摊成
    一行」的唯一定义处），它只取字段路径与 pydantic 的消息，**不带输入值** —— 回执会
    被读、被留档，而输入值是客户邮件的正文。那一处还有第二个调用方：
    ``application/relations.py`` 的逐行去向（「一行坏判定不得毁掉整批」）。
    """
    try:
        draft = OutreachDraft(
            draft_id=receipt.draft_id,
            draft_version=version,
            supersedes=None if version == 1 else version - 1,
            workspace=plan.workspace,
            scene_id=plan.scene_id,
            scene_version=plan.scene_version,
            campaign_id=campaign_id,
            wave_id=plan.wave_id,
            route_ref=target.route_ref,
            account_id=target.account_id,
            # 入口性质与路线等级**冻结在草稿上**。§8 逐字：「C/D 草稿需明确是部门/
            # 通用入口，不冒充具名决策人」。计划的筛选已经保证了一条草稿与它的等级
            # 相容（见 ``FALLBACK_TO_STAGE_FOR_GRADES``），而那件事的全部意义只有在
            # 草稿**自己记着**它是哪一档时才对读它的人可见：日后族表变了、这一稿被
            # 拿去复用，正文里那句「致贵校后勤负责人」还留在盘上，而它当初是写给一条
            # ``GENERIC_OFFICIAL`` 的 —— 只查族 id 是看不出这一点的。
            entry_kind=target.entry_kind,
            route_grade=target.route_grade,
            family_id=family.family_id,
            family_revision=family.revision,
            writing_policy_id=policy.policy_id,
            writing_policy_revision=policy.revision,
            writer_id=receipt.writer_id,
            stage=plan.stage,
            locale=locale,
            subject=proposal.subject,
            body=proposal.body,
            body_sha256=receipt.body_sha256,
            subject_sha256=receipt.subject_sha256,
            claim_ids=proposal.claim_ids,
            facts_used=proposal.facts_used,
            attachments=proposal.attachments,
            state=_DRAFT_STATE_BY_OUTCOME[receipt.outcome],
            generation_key=receipt.generation_key,
            generation_receipt_ref=receipt.receipt_ref,
            created_at=moment,
        )
    except ValidationError as error:
        return None, one_line_reason(error)
    return draft, ""


# --- 三、冻结批次 -------------------------------------------------------------


def prepare_wave(
    *,
    plan: WavePlan,
    store: OutreachStore,
    campaign: OutreachCampaign,
    sendbox: str,
    window_start: datetime,
    window_end: datetime,
    valid_until: datetime,
    at: datetime,
) -> OutreachManifest:
    """把计划里的草稿冻成一份**清单**，并把批次推进到
    :attr:`~marketops.domain.outreach.WaveState.PREPARED_PENDING_AUTHORIZATION`。

    ## 缺一稿的候选**不进清单**，进 ``excluded``

    一份点了名却没有正文的行，会让清单的 ``allowed_count`` 与真实能发出去的
    条数不一致 —— 而授权绑的是摘要，摘要变了授权就失效，于是那一条的处置
    应该是「这一批少发一条」而不是「这一批发不出去」。所以缺稿的候选被记进
    ``excluded``，理由写得出是哪一条、缺的是什么。

    ## 清单的行序按**投递时用的那个** ``send_key`` 定

    :meth:`~marketops.domain.outreach.WaveAuthorization.approved_slice` 取的是
    前 ``approved_count`` 行，所以「第几行」必须是一个说得清的事实。这里用的
    是与 :func:`_deliver_one` 逐字同一个 :func:`send_key_for`（含草稿的**真实
    版本号**）—— 若在这里按收件人排序、在投递时按 ``send_key`` 取，那么
    「批准了哪 10 条」在两处会是两个不同的答案，而报告上两边都说得通。
    """
    moment = to_utc(at)
    entries: list[tuple[str, ManifestEntry]] = []
    excluded: list[tuple[str, str]] = []

    for planned in plan.entries:
        draft_id = _draft_id_for(plan.wave_id, planned.recipient)
        draft = _latest_draft(store, draft_id)
        if draft is None:
            excluded.append((planned.recipient, "这一条没有生成出任何一稿"))
            continue
        if draft.state is not DraftState.GENERATED:
            excluded.append((planned.recipient, f"最新一稿的状态是 {draft.state.value}"))
            continue
        entries.append(
            (
                send_key_for(
                    workspace=plan.workspace,
                    wave_id=plan.wave_id,
                    draft_id=draft.draft_id,
                    draft_version=draft.draft_version,
                    recipient=planned.recipient,
                ),
                ManifestEntry(
                    draft=DraftRef(
                        draft_id=draft.draft_id,
                        draft_version=draft.draft_version,
                        body_sha256=draft.body_sha256,
                    ),
                    route_ref=planned.route_ref,
                    account_id=planned.account_id,
                    recipient=planned.recipient,
                    sendbox=normalise_email(sendbox),
                    stage=planned.stage,
                    family_id=draft.family_id,
                    writing_policy_id=draft.writing_policy_id,
                    evidence_ids=planned.evidence_ids,
                ),
            )
        )

    ordered = tuple(row for _, row in sorted(entries, key=lambda pair: pair[0]))
    manifest = OutreachManifest(
        wave_id=plan.wave_id,
        campaign_id=campaign.campaign_id,
        workspace=plan.workspace,
        scene_id=plan.scene_id,
        entries=ordered,
        window_start=to_utc(window_start),
        window_end=to_utc(window_end),
        allowed_count=len(ordered),
        excluded=tuple(excluded),
        valid_until=to_utc(valid_until),
        created_at=moment,
    )
    store.put_manifest(manifest)
    store.put_wave(
        Wave(
            wave_id=plan.wave_id,
            campaign_id=campaign.campaign_id,
            workspace=plan.workspace,
            scene_id=plan.scene_id,
            state=WaveState.PREPARED_PENDING_AUTHORIZATION,
            manifest_sha256=manifest.manifest_sha256,
            revision=0,
            created_at=moment,
            updated_at=moment,
        ),
        expected_revision=None,
    )
    return manifest


# --- 四、受控授权 -------------------------------------------------------------


def record_authorization(
    *, authorization: WaveAuthorization, store: OutreachStore, at: datetime
) -> WaveAuthorization:
    """登记一份**已经存在**的授权，并把它绑定的清单核对一遍。

    ## 这个函数**不产生**授权

    它没有 ``approve`` 这个动词。来的那份 :class:`WaveAuthorization` 上带着
    ``authorized_by`` 与 ``authority_ref`` —— 一个本进程不认识、也无法验证的
    外部主体。§6 逐字：「模型写下『我批准』、普通控制者自授一个角色、
    或者 ``send_authorized=true`` 都产生不了授权」。所以这里能做的最多是把
    一份**已经有的**记录落库，并核对它真的覆盖这份清单。

    ## 核对是**重新算**出来的

    ``covers()`` 根据清单重新计算 ``manifest_sha256`` 再比对 —— 抄进来的值
    挡得住「有人改了授权记录」，挡不住「有人改了清单」，而后者才是这个判据
    存在的全部理由。
    """
    moment = to_utc(at)
    manifest = store.manifest(authorization.wave_id)
    if manifest is None:
        raise MarketOpsError(
            f"授权指向的批次 {authorization.wave_id!r} 没有清单。\n"
            "授权绑的是清单摘要，而这里没有摘要可比 —— 先冻结批次。"
        )
    problem = authorization.covers(manifest, moment)
    if problem:
        raise MarketOpsError(
            f"这份授权覆盖不了这份清单：{problem}\n"
            "授权与清单不一致时**不得**登记 —— 登记一份覆盖不了的授权，"
            "会让它在盘上看起来与一份有效的授权一样。"
        )
    store.put_authorization(authorization)
    return authorization


def mark_authorized(
    *, wave_id: str, authorization_id: str, store: OutreachStore, at: datetime
) -> None:
    """把批次推进到 ``AUTHORIZED`` —— **只在**对应那份授权已经登记之后。"""
    moment = to_utc(at)
    live = [
        row
        for row in store.authorizations(wave_id=wave_id)
        if row.authorization_id == authorization_id and row.is_live_at(moment)
    ]
    if not live:
        raise MarketOpsError(
            f"批次 {wave_id!r} 上没有一份此刻有效的授权 {authorization_id!r}。\n"
            "``AUTHORIZED`` 是**一份授权存在**这件事的投影，不是「有人点了头」——"
            "没有那份记录时这个状态写不出来。"
        )
    current = _require_wave(store, wave_id)
    store.put_wave(current.moved_to(WaveState.AUTHORIZED, at=moment), expected_revision=current.revision)


def lapse_authorization(
    *, wave_id: str, reason: str, store: OutreachStore, at: datetime
) -> None:
    """把批次标记回 ``AUTHORIZATION_LAPSED``。内容改了、授权撤了都走这里。"""
    moment = to_utc(at)
    if not reason.strip():
        raise MarketOpsError(
            "把一批退回待授权必须写明理由。\n"
            "一条说不出为什么的退回与一次「有人手滑」在记录上长得一样。"
        )
    current = _require_wave(store, wave_id)
    store.put_wave(
        current.moved_to(WaveState.AUTHORIZATION_LAPSED, at=moment),
        expected_revision=current.revision,
    )


def _require_wave(store: OutreachStore, wave_id: str) -> Wave:
    wave = store.wave(wave_id)
    if wave is None:
        raise MarketOpsError(f"批次 {wave_id!r} 不存在。")
    return wave


# --- 五、执行投递 -------------------------------------------------------------


@dataclass(frozen=True)
class DispatchOutcome:
    """一封的结局。**六个取值，各自一种处置。**"""

    send_key: str
    state: str  # sent / refused / unknown / skipped / duplicate / failed
    detail: str = ""

    @property
    def reached_the_transport(self) -> bool:
        """这一次调用**真的把字节交给了传输层**吗。

        它是报告上唯一诚实的「我们尝试发出了几封」——``sent`` 与 ``unknown``
        与 ``refused`` 都到了传输，而 ``skipped`` 与 ``duplicate`` 没有。
        把 ``skipped`` 算进去会让「这一批发了 20 封」与「这一批 20 条候选」
        变成同一句话。
        """
        return self.state in ("sent", "refused", "unknown")


@dataclass(frozen=True)
class DispatchReport:
    outcomes: tuple[DispatchOutcome, ...]

    @property
    def summary_line(self) -> str:
        counts: dict[str, int] = {}
        for row in self.outcomes:
            counts[row.state] = counts.get(row.state, 0) + 1
        return " ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "nothing"


def dispatch_wave(
    *,
    wave_id: str,
    store: OutreachStore,
    scenes: SceneStore,
    transport: MailTransport,
    rules: OutreachRules,
    at: datetime,
) -> DispatchReport:
    """把一批**已经授权**的信发出去。**逐封判定、逐封记录。**

    ## 每一次投递前重查的八件事，以及它们为什么必须在**这里**

    §6 逐字：「**每一次**投递前重查授权、Scene/Campaign 状态、新鲜度、
    跨线程/跨 Scene 的工作区发送历史、重复、抑制、回信、退信、待定投递、
    先前阶段与时间窗」。这一段把它们排成一条**没有分支可以绕过**的序列：
    每一个 ``continue`` 都是一次跳过，而每一次跳过都写下了理由。

    顺序是有理由的，不是随便排的：

    1. **Scene 是否在跑** —— 一个暂停的 Scene 不得继续外联（§4 逐字：
       「暂停/归档停止新的研究或外联派发」）。
    2. **批次状态** —— 只有 ``AUTHORIZED`` / ``SENDING`` 才发得出去。
    3. **清单还在不在、摘要还对不对** —— 有人换过内容就退回待授权。
    4. **授权此刻还有效吗** —— :meth:`WaveAuthorization.covers`，它**重算**摘要。
    5. **发送箱一致吗** —— 清单上的与传输自报的不是同一个 → 一封都不发。
       这一条在**循环之外**判：它不是某一封的问题，是这一整个部署的问题。
    6. **时间窗** —— 早于 ``window_start`` 或晚于 ``valid_until`` 都不发。
    7. **收件人这一封的资格**：抑制、**说过停的回信**（:func:`stopped_by_reply` ——
       计划做在冻清单之前，而一封「不要再联系我」完全可能落在那之后）、这一批里
       重复、跨批次封数、前序段。
    8. **``begin_attempt`` 的 ``won``** —— 最后一道，由存储的
       ``BEGIN IMMEDIATE`` 背书。

    ## Scene 是**从库里读的**，不是调用方传进来的

    ``scenes`` 是参数而 ``SceneRecord`` 不是。一个接 ``SceneRecord`` 的签名
    会让人把一个刚才读到的对象递进来，而第 1 条的判据就变成了「我读它的时候
    它在跑」—— 一个已经暂停了十分钟的 Scene 照样会把这一批发出去，而记录上
    完全看不出来。所以这里的 Scene 状态是**此刻这一个**：它的
    ``scene_id`` 来自批次自己（批次不会换 Scene，见 ``SceneConflictError``）。

    ## ``won=False`` 不是错误

    它与「被抑制」一样是一条**跳过**，而它是最后一道：两个驱动同时对同一封信
    进来时只有一个 ``won``。把它记成失败会让「并发保护生效了」看起来像
    「发信失败了」。
    """
    moment = to_utc(at)
    wave = _require_wave(store, wave_id)

    scene = scenes.get_record(wave.scene_id)
    if scene is None:
        return _stopped(f"批次指向的 Scene {wave.scene_id!r} 在库里找不到")
    if scene.state is not SceneState.ACTIVE:
        return _stopped(f"Scene 不在运行中：{scene.state.value}")

    if wave.state not in (WaveState.AUTHORIZED, WaveState.SENDING):
        return _stopped(f"批次状态是 {wave.state.value}，不是已授权")
    manifest = store.manifest(wave_id)
    if manifest is None:
        return _stopped("这一批没有清单")

    authorization = _live_authorization(store, wave_id, manifest, moment)
    if authorization is None:
        return _stopped("这一批此刻没有一份覆盖当前清单的授权")
    if not manifest.entries:
        return _stopped("这一批的清单是空的")
    # 发送箱一致在**循环之外**判：它不是某一封的问题，是这一整个部署的问题。
    listed = {normalise_email(entry.sendbox) for entry in manifest.entries}
    if normalise_email(transport.sender_identity) not in listed:
        return _stopped(
            f"传输自报的发送箱 {transport.sender_identity!r} 不在清单上的 "
            f"{sorted(listed)!r} 里 —— 一封都不发"
        )
    if not (manifest.window_start <= moment <= manifest.valid_until):
        return _stopped(
            f"现在 {moment.isoformat()} 不在 "
            f"[{manifest.window_start.isoformat()}, "
            f"{manifest.valid_until.isoformat()}] 之内"
        )

    outcomes: list[DispatchOutcome] = []
    for entry in authorization.approved_slice(manifest):
        outcomes.append(
            _deliver_one(
                entry=entry,
                manifest=manifest,
                authorization=authorization,
                store=store,
                transport=transport,
                rules=rules,
                at=moment,
            )
        )
    return DispatchReport(outcomes=tuple(outcomes))


def _stopped(detail: str) -> DispatchReport:
    """一次**在循环之外**就停下的派发：整批一条都不发，并说明为什么。

    ``send_key`` 用空串：这批没有发出去任何一封，所以没有任何一个逻辑发送
    标识可以说 —— 填一个假的会让报告上出现一条不存在的发送。
    """
    return DispatchReport(
        outcomes=(DispatchOutcome(send_key="", state="skipped", detail=detail),)
    )


def _live_authorization(
    store: OutreachStore, wave_id: str, manifest: OutreachManifest, at: datetime
) -> WaveAuthorization | None:
    """这一批此刻**覆盖当前清单**的那一份授权。多份都覆盖时取最后批准的那份。

    为什么不在 ``authorizations`` 上取「最新的一条」：一条更新的授权可能
    **不覆盖**这份清单（有人改了内容之后重新批了别的），而那时正确的结论是
    「现在没有授权」，不是「用旧的那份」。
    """
    covering = [
        row
        for row in store.authorizations(wave_id=wave_id)
        if not row.covers(manifest, at)
    ]
    if not covering:
        return None
    return max(covering, key=lambda row: (row.authorized_at, row.authorization_id))


def _deliver_one(
    *,
    entry: ManifestEntry,
    manifest: OutreachManifest,
    authorization: WaveAuthorization,
    store: OutreachStore,
    transport: MailTransport,
    rules: OutreachRules,
    at: datetime,
) -> DispatchOutcome:
    """一封的完整序列。见 :func:`dispatch_wave` 的 docstring 第 6–8 条。

    ## 每一条 ``skipped`` 都带上这一封的 ``send_key``

    报告里的标识列在这条路径上必须是**同一个东西**。一个在跳过时填
    ``draft_id``、在发出时填 ``send_key`` 的实现，会让「这一封跳过了」
    这句话在事后查不到是哪一封 —— 而跳过恰恰是最需要回查的那一类
    （「为什么他没收到」）。所以先把 ``send_key`` 算出来，再用。
    """
    recipient = normalise_email(entry.recipient)
    send_key = send_key_for(
        workspace=manifest.workspace,
        wave_id=manifest.wave_id,
        draft_id=entry.draft.draft_id,
        draft_version=entry.draft.draft_version,
        recipient=recipient,
    )

    draft = store.draft(entry.draft.draft_id, entry.draft.draft_version)
    if draft is None:
        return DispatchOutcome(send_key, "skipped", "清单上的那一稿不在库里")
    body_sha = sha256_text(draft.body)
    if body_sha != entry.draft.body_sha256:
        # 正文与清单上的摘要不一致 → **不发**。这一条挡的是「有人改了库里的正文」，
        # 而那正是清单摘要存在的理由。
        return DispatchOutcome(
            send_key,
            "skipped",
            "库里的正文与清单上的摘要不一致 —— 内容被改过",
        )

    reason = check_prior_stages(
        entry=entry,
        store=store,
        rules=rules,
        at=at,
    )
    if reason:
        return DispatchOutcome(send_key, "skipped", reason)

    if store.is_suppressed(recipient, at=at):
        return DispatchOutcome(send_key, "skipped", "收件人在抑制名单上")
    # §6 逐字要求**每次**投递前重查（「跨线程/跨 Scene 工作区发送历史、重复、抑制、
    # **回复**、退信、待投递、先前的阶段和时间窗口」）。计划做在冻清单之前，而一封
    # 「不要再联系我」完全可能落在计划与投递之间 —— 那一刻这封信还没离机，所以这里
    # 是**最后**一个能挡住它的地方。
    stopped = stopped_by_reply(store=store, workspace=manifest.workspace, recipient=recipient)
    if stopped:
        return DispatchOutcome(send_key, "skipped", stopped)
    if len(store.attempts_by_recipient(recipient)) >= rules.max_per_recipient:
        return DispatchOutcome(
            send_key,
            "skipped",
            f"这个收件人已经收满了（上限 {rules.max_per_recipient}）",
        )

    attempt = OutboundAttempt(
        send_key=send_key,
        workspace=manifest.workspace,
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
        recipient=recipient,
        sendbox=entry.sendbox,
        stage=entry.stage,
        authorization_id=authorization.authorization_id,
        manifest_sha256=manifest.manifest_sha256,
        started_at=at,
    )
    outcome = store.begin_attempt(attempt, at=at)
    if not outcome.won:
        return DispatchOutcome(
            send_key,
            "duplicate",
            f"这一封已经有主（{outcome.state.value}）—— 不得重复发",
        )

    try:
        receipt = transport.send(
            entry=entry,
            subject=draft.subject,
            body=draft.body,
            from_address=entry.sendbox,
        )
    except Exception as error:  # noqa: BLE001 — 抛 = 可能出去了，记 UNKNOWN
        store.finish_attempt(
            outcome.attempt.model_copy(
                update={
                    "state": MessageState.DELIVERY_UNKNOWN,
                    "finished_at": at,
                    "failure": f"{type(error).__name__}: {error}",
                }
            )
        )
        return DispatchOutcome(
            send_key,
            "unknown",
            "传输抛了 —— 可能已经出去，不得重发，先对账",
        )

    state, failure = _message_state_for(receipt)
    store.finish_attempt(
        outcome.attempt.model_copy(
            update={
                "state": state,
                "finished_at": at,
                "provider_message_id": receipt.message_id,
                "failure": failure,
            }
        )
    )
    if state is MessageState.ACCEPTED_BY_PROVIDER:
        return DispatchOutcome(send_key, "sent", receipt.message_id)
    if state is MessageState.FAILED:
        return DispatchOutcome(send_key, "refused", receipt.reason)
    return DispatchOutcome(send_key, "unknown", receipt.reason or "提供方没有给出确定的答复")


def _message_state_for(receipt) -> tuple[MessageState, str]:
    """传输的回话 → 投递状态。**唯一**一处翻译。

    ``unknown`` 落到 :attr:`MessageState.DELIVERY_UNKNOWN` 而不是 ``FAILED``：
    两者的处置相反（先对账 / 可以重试），而把它们合成一个的实现会让「超时了
    但对面可能收了」在下一次调用里被重发 —— 那是有人收到两封的成因。
    """
    if receipt.outcome == "accepted":
        return MessageState.ACCEPTED_BY_PROVIDER, ""
    if receipt.outcome == "refused":
        return MessageState.FAILED, receipt.reason
    return MessageState.DELIVERY_UNKNOWN, receipt.reason or "提供方没有给出确定的答复"


def check_prior_stages(
    *, entry: ManifestEntry, store: OutreachStore, rules: OutreachRules, at: datetime
) -> str:
    """第 2–4 段的资格。返回空串 = 可以发；否则一条说得出的理由。

    ## §6 这一条逐字：「必须严格读**已确认的**上一阶段与允许的等待期/次数」

    三件事各有一次真实的失败：

    * **上一段没被确认** —— 「我们提交给服务器了」不等于「对方收到了」。
      按 ``ACCEPTED_BY_PROVIDER`` 就放行第二个阶段，会在对端拒收之后继续打扰
      一个从来没收到第一封信的人。所以合格的前序状态只有
      :attr:`MessageState.DELIVERED`（或到达了它）。
    * **等待期没到** —— 一个人今天收到第一封、一小时后收到第二封。
    * **次数超了** —— 一个人被同一件事骚扰四次。

    ## 第一段没有前序条件

    ``stage.previous is None`` 时这里只回空串。把它写成「要求前一阶段存在」的
    实现会让**第一封信永远发不出去**，而症状是「这一批没人可发」。
    """
    previous = entry.stage.previous
    if previous is None:
        return ""

    history = store.attempts_by_recipient(normalise_email(entry.recipient))
    priors = [row for row in history if row.stage is previous]
    confirmed = [
        row
        for row in priors
        if row.state in (MessageState.DELIVERED, MessageState.BOUNCED)
    ]
    if not confirmed:
        return (
            f"第 {entry.stage.ordinal} 段要求第 {previous.ordinal} 段已经被确认"
            "（送达或退信），而这条记录上看不到"
        )
    latest = max(row.finished_at or row.started_at for row in confirmed)
    wait = rules.wait_for(entry.stage)
    if to_utc(at) - latest < wait:
        return (
            f"还不到第 {entry.stage.ordinal} 段的等待期：上一段确认于 "
            f"{latest.isoformat()}，要求至少等 {wait}"
        )
    budget = rules.prior_touch_budget(entry.stage)
    if len(priors) > budget:
        return (
            f"这个收件人在第 {entry.stage.ordinal} 段之前的已发出封数是 "
            f"{len(priors)}，超过配置的 {budget}"
        )
    return ""


def stopped_by_reply(*, store: OutreachStore, workspace: str, recipient: str) -> str:
    """这个人有没有一条**说过停**的回信。返回一条理由，空串 = 没有。

    ## 它与「在抑制名单上」是两件事，而两件都要查

    §7 逐字：「有效人工回复 → 停止相关自动跟进」、「明确拒绝 → 停止跟进」。
    ``store.is_suppressed(...)`` 只回答得了其中一半：按 domain 的处置表，**拒绝**
    与**人工审核不写抑制行**（:data:`~marketops.domain.outreach.SUPPRESSING_KINDS`
    只收退订 / 投诉 / 硬退信），因为抑制是一个跨 Campaign 长期有效的事实，而拒绝是
    关于这次接洽的。于是「停发」这条规则若只挂在抑制名单上，一个说过「不用了」的人
    会在**下一批**里照常收到信 —— 而每一行记录都显示处置正确。

    ## 怎么找到这个人的回信

    主链接是 :attr:`~marketops.domain.outreach.CorrespondenceEvent.send_key` —— 那条
    事件关联到我们发出去的哪一次尝试，与 :attr:`OutboundAttempt.send_key` 是同一个
    值。第二个是 ``sender``：关联不上的事件（``send_key`` 为空）**照样可以是一条
    「不要再联系我」**，所以它在地址对得上时也停。两个方向不对称 —— 多停一封的代价
    是这个人少收一封信，少停一封的代价是一个说过不要的人继续收到信 —— 所以这里取
    保守的那一边。

    ## 判据在 domain，这里只做查

    「哪些回信算停」是
    :func:`~marketops.domain.outreach.stops_further_outreach` 那张表的答案，本层
    不重写一份：一个在用例层再写一遍的判据，漂起来没有任何执行器会响。

    多条命中时返回**按 ``event_id`` 排序的第一条**：同一份库在两台机器上要给出同一
    句理由，否则清单与跳过记录一比对就像是变了。
    """
    recipient = normalise_email(recipient)
    keys = {row.send_key for row in store.attempts_by_recipient(recipient)}
    hits = sorted(
        (
            event
            for event in store.events(workspace=workspace)
            if stops_further_outreach(event.kind)
            and (event.send_key in keys if event.send_key else event.sender == recipient)
        ),
        key=lambda event: event.event_id,
    )
    if not hits:
        return ""
    event = hits[0]
    return (
        f"这个人回信说过停：{event.kind.value}（处置 {event.disposition.value}）"
        f"—— 按 §7 不再跟进，重开要人来做"
    )


# --- 六、对账 -----------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationOutcome:
    """一次对账的结论。**它只说这一次问到了什么**，不替人决定。"""

    send_key: str
    state: str  # settled / still_unknown / not_found
    detail: str = ""


def reconcile_unknown(
    *, wave_id: str, store: OutreachStore, transport: MailTransport, at: datetime
) -> tuple[ReconciliationOutcome, ...]:
    """把这一批里 ``DELIVERY_UNKNOWN`` 的那些**问清楚**。

    ## 它为什么存在

    §6 逐字：「提供方可能已受理但超时的结果是 ``delivery_unknown`` —— 先对账，
    不要盲目重试」。没有这一段，那些行会永远停在 ``DELIVERY_UNKNOWN``，
    而它们是**唯一**一种「不得重发、又没有结局」的行。

    ## 问不到不代表没发

    传输查不到时返回 ``unknown``（不是抛）—— 一封很久以前的信在提供方的窗口
    之外，与「这封信没发出去」在盘上是两件事。所以这里**只**把真的拿到确定
    答复的那些写定，其余的原样留着，并给出 ``still_unknown``。把它们写成
    ``FAILED`` 会让它们变得「可以重试」—— 那是有人收到两封的另一条成因。
    """
    settled: list[ReconciliationOutcome] = []
    for attempt in store.attempts(wave_id=wave_id):
        if attempt.state is not MessageState.DELIVERY_UNKNOWN:
            continue
        if not attempt.provider_message_id:
            settled.append(
                ReconciliationOutcome(
                    attempt.send_key,
                    "still_unknown",
                    "没有提供方标识 —— 无法按标识问（按地址问就是地址探测，禁止）",
                )
            )
            continue
        try:
            receipt = transport.status(attempt.provider_message_id)
        except Exception as error:  # noqa: BLE001
            settled.append(
                ReconciliationOutcome(
                    attempt.send_key,
                    "still_unknown",
                    f"对账这次没问到：{type(error).__name__}: {error}",
                )
            )
            continue
        if receipt.outcome == "unknown":
            settled.append(
                ReconciliationOutcome(
                    attempt.send_key, "still_unknown", receipt.reason or "提供方没有结论"
                )
            )
            continue
        state, failure = _message_state_for(receipt)
        store.finish_attempt(
            attempt.model_copy(
                update={
                    "state": state,
                    "finished_at": to_utc(at),
                    "provider_message_id": receipt.message_id or attempt.provider_message_id,
                    "failure": failure,
                }
            )
        )
        settled.append(ReconciliationOutcome(attempt.send_key, "settled", state.value))
    return tuple(settled)


# --- 七、把回信变成「停发」 ---------------------------------------------------


def stop_followup_for(
    *, event_send_key: str, store: OutreachStore, at: datetime, note: str
) -> int:
    """一条进来的回信 → **停掉这个主体上所有还没出去的投递**。

    ## 它为什么在这一层，而不在收信那一层

    收信那一层（``application/correspondence.py``）回答「对面说了什么、该怎么
    处置」。而「有哪些还没出去的投递要停掉」是**发送这一层**的知识 ——
    只有它知道 ``begin_attempt`` 之前会查什么。§7 逐字要求「新到的有效人类
    回信应先保守地暂停后续外联，然后再分类」，所以这个函数是那条要求的落地：
    它**先于**分类执行，且分类失败也照样执行。

    ## 停的是**这个主体**，不是这一封信

    同一个收件人在别处还排队着的那几封要一起停 —— 一个人回过信之后，
    他名下任何一个待发的东西都不该再出去。所以这里按 ``send_key`` 找到那一行，
    再按它的 ``recipient`` 找出整族的未发记录。

    ## 它**不**产出 :class:`~marketops.domain.outreach.SalesHandoff`

    交接是「对面说了什么」的结论，由 ``application/correspondence.py`` 产出；
    这里只做「停发」这一件机械的事。两者分开的理由就是上面那条：停发必须能在
    分类还没跑、甚至分类失败的时候单独发生。

    返回停掉了几条。**0 是一个真实的结论**（没有待发的），不是失败。
    """
    moment = to_utc(at)
    if not note.strip():
        raise MarketOpsError(
            "停发必须写明理由。\n"
            "一条说不出为什么的停发与一次「有人手滑取消了」在记录上长得一样。"
        )
    anchor = store.attempt(event_send_key) if event_send_key else None
    if anchor is None:
        # 关联不上回信：处置是交给人看（调用方负责），这一层没有东西可停。
        return 0
    stopped = 0
    for row in store.attempts_by_recipient(anchor.recipient):
        if row.state is not MessageState.NOT_SENT:
            continue
        store.finish_attempt(
            row.model_copy(
                update={
                    "state": MessageState.FAILED,
                    "finished_at": moment,
                    "failure": f"回信到达，先保守停发：{note}",
                }
            )
        )
        stopped += 1
    return stopped


__all__ = [
    "GENERATION_RECEIPT_SUFFIX",
    "DISPOSITIONS",
    "DraftingOutcome",
    "DraftingPlan",
    "DraftingReceipt",
    "DispatchOutcome",
    "DispatchReport",
    "DraftTarget",
    "GenerationReceipt",
    "KEY_CONTACT_GRADES",
    "OutreachRules",
    "PlannedEntry",
    "ReconciliationOutcome",
    "SkippedEntry",
    "WavePlan",
    "check_prior_stages",
    "dispatch_wave",
    "drafting_receipt",
    "family_for",
    "family_for_stage",
    "generate_drafts",
    "generation_receipt_path",
    "lapse_authorization",
    "load_generation_receipt",
    "mark_authorized",
    "plan_drafting",
    "plan_wave",
    "prepare_wave",
    "record_authorization",
    "reconcile_unknown",
    "review_reasons",
    "send_key_for",
    "stop_followup_for",
    "write_generation_receipts",
]

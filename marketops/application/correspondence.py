"""回信 / 退信 / 退订 / 投诉 → 抑制 + 销售交接。**顺序就是判据本身。**

## 这一层做的三件事，以及它们的次序

``ingest`` 吃一批原始事件（``ports/correspondence.py`` 读进来的），按三步走：

1. **压制**（:func:`_hold_everything`）—— 对**每一条**先问存储「这个人现在在抑制
   名单上吗」（``store.is_suppressed(recipient, at=at)``，**不是**读一份缓存），
   并定下一条**最保守的**处置。§7 逐字：「新到的人类回信应先保守地暂停后续外联，
   然后再分类」。这一整个循环跑完才轮到第二步 —— 于是「先压制、后分类」不是一句
   注释，而是**调用次序**：分类器被调到的那一刻，压制已经生效了（契约测试用一个
   记账的分类器与一层记账的存储把这条次序钉住）。
2. **分类**（:func:`_decide`）—— 只对**来源没说分类**的那些条目调
   :class:`~marketops.ports.correspondence.ReplyClassifier`。来源自己给了分类的
   条目直接按 domain 的表走，分类器不参与 —— 一封退信是收信服务器按 RFC 回的，
   让一个模型去「再确认一下」只是给了它一次推翻确定事实的机会。
3. **落库**（:func:`_persist`）—— 事件先落库，然后才是它的副作用（抑制行、
   交接行）。§7 逐字：「先持久化、再推进游标」：游标只在**这一批的条目都落库
   之后**才前进，某一条失败时它停在**那一条之前**（``IngestionReceipt.stopped_at``），
   于是重读会重放那一条，而不是跳过它。

## 它不做什么

* **不发明第三张表。** 分类 → 处置只有 domain 那一张
  （:data:`~marketops.domain.outreach.KIND_DISPOSITION`）；抑制理由按**同名取值**
  从 :class:`~marketops.domain.outreach.SuppressionReason` 取出，于是两边词汇表
  哪天分岔了，构造枚举的那一刻就会抛。
* **不发任何外部通知。** 交接停在 ``PENDING_LOCAL``；人工审核只落一条
  ``MANUAL_REVIEW`` 的事件行。§7 逐字：「模糊回信 → manual_review_hold，
  **不默认发外部通知卡**」。
* **不替人做换人的决定。** 回信里说「请联系某某」时，处置是人工审核 + 一条说得
  出的理由。§7 逐字：「字面上的替代联系人 → awaiting_Member_A_confirmation，
  **不自动重发或新建外联**」。
* **不停止别的批次。** 「停止相关自动跟进」在本层的落地是**事件行上那条处置**
  （``STOP_FOLLOWUP`` / ``HANDOFF``）与抑制行 —— 出信侧按这些事实筛人是
  ``application/outreach.py`` 那一半的事。本层不假装自己停了什么：这里没有任何
  一行代码会去动一条已经排好的清单。

## ``REFUSAL`` 为什么不进抑制名单

判据是**处置表**：只有处置是 ``SUPPRESS`` 的分类才写抑制行。所以退订 / 投诉 /
硬退信写抑制行，而 ``REFUSAL``（处置 ``STOP_FOLLOWUP``）与 ``MANUAL_REVIEW`` 不写。
理由是后果的方向：抑制是一个**跨 Campaign 长期有效**的事实
（``RecipientSuppression`` 的 docstring 逐字），而拒绝是关于这次接洽的。把一次
「这次不用了」记成「永远不得联系」，代价是这个人再也不会被联系到，而记录上一切正常。

**但「不写抑制行」不等于「还会再收到信」** —— 那是另一条规则，两条都要有：

* 本层落的是**事实**（事件行上的处置）。
* ``application/outreach.py`` 的 ``stopped_by_reply`` 在计划与投递两处按那个事实
  筛人。少了它，一个说过「不用了」的人会在下一批里照常收到信，而每一行记录都
  显示处置正确 —— 这正是两张表当初漂起来时看不见的那种坏。

``domain/outreach.py`` 的 ``SUPPRESSING_KINDS`` 由处置表**推出来**（不是另写一张
集合），所以「两张表漂了」这件事在构造上不可能发生。

## 关于证据 id

§7 逐字要求「产出一条**有证据的** SalesHandoff」，而 :class:`SalesHandoff` 拒绝空
``evidence_ids``。本层**造不出真的** ``Evidence`` 行：``domain/evidence.py`` 的
``source_kind`` 只有四个成员（检索摘要、抓下来的 HTML / PDF / 纯文本），没有一个
叫「回信」；而 ``Evidence.account_id`` 是以「我们研究过的那个账号」为键的。硬塞
一个来源种类，等于伪造一条「这封回信是从某个网页上抓来的」证据 —— 那比没有证据
更坏。

所以证据 id 从**事件自己**的字段派生（``workspace`` + ``sendbox`` + 提供方消息 id
+ 原件摘要）：它稳定、可复算、指向的就是那份原件。**当前边界**：它回答「是哪一份
原件」，不回答「原件里哪一句话支持了什么」。后者要等 ``source_kind`` 多一个成员，
而那是 ``domain/`` 的事，本层不动它。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ..domain.errors import StoreError
from ..domain.outreach import (
    KIND_DISPOSITION,
    CorrespondenceDisposition,
    CorrespondenceEvent,
    CorrespondenceKind,
    HandoffState,
    OutboundAttempt,
    RecipientSuppression,
    SalesHandoff,
    SuppressionReason,
    Wave,
)
from ..domain.util import check_canonical_id, normalise_email, sha256_text, to_utc
from ..ports.correspondence import (
    Classification,
    CorrespondenceBatch,
    CorrespondenceItem,
    CursorState,
    ReplyClassifier,
)
from ..ports.outreach import OutreachStore

#: 派生 id 的分隔符。与 ``domain/util.py`` 里几处派生用的是同一个字符（ASCII 的
#: Unit Separator）：它不可能出现在任何一个真实的字段值里，于是「a‖b」与「ab‖」
#: 不会撞到同一个摘要上。
SEP = "\x1f"

#: 压制阶段落在事件上的那条理由。§7 逐字：「新到的人类回信应先保守地暂停后续
#: 外联，然后再分类」—— 于是这条理由说的是**次序**，不是内容。
CONSERVATIVE_HOLD = (
    "先保守地暂停后续外联，然后再分类（§7）。这条事件的处置在分类出结果之前"
    "就已经定下；分类若说不出它是什么，处置就停在这里。"
)

#: 「请找别人」这类**字面**线索。检测是保守的，见 :func:`_alternate_contact`。
#:
#: 它应该属于 profile / 配置（每种语言、每个部署的说法不一样），今天住在代码里
#: 是因为还没有第二个部署来证明那个配置面该长什么样 —— 与 ``ports/`` 的
#: 「等第二个实现出现再提取端口」是同一条纪律。
ALTERNATE_CONTACT_CUES: tuple[str, ...] = (
    "please contact",
    "please reach out to",
    "you should contact",
    "speak with",
    "get in touch with",
    "请联系",
    "請聯繫",
    "你可以联系",
)

#: 正文里的邮箱地址。刻意宽松（它只用来**排除**，不用来确认）：严格的那一条判据
#: 是「与发件人不同的地址**恰好一个**」，见 :func:`_alternate_contact`。
_ADDRESS_IN_TEXT = re.compile(r"[^\s,;:<>()\[\]\"'|]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


@dataclass(frozen=True)
class HeldEvent:
    """**压制**的结论 —— 一条在分类器被调用**之前**就定下的处置。

    它是 :class:`IngestionReceipt` 的第一个字段，因为它是第一步的产物：读一条
    回执时，「当时压住了什么」与「后来分成了什么」是两件事。
    """

    provider_message_id: str
    #: 我们要压制的那个人。关联得上时是那次发送的收件人；关联不上时是发件人
    #: （可能为空 —— 提供方没给发件人地址）。
    correspondent: str
    #: 这一刻他是否已经在抑制名单上（当场问的存储）。
    already_suppressed: bool
    #: 不看分类时最保守的处置。来源给了分类就按表；没给就是 ``MANUAL_REVIEW``。
    disposition: CorrespondenceDisposition
    note: str = ""


@dataclass(frozen=True)
class IngestedEvent:
    """**落库**的结论 —— 这一条最终写下去了什么。"""

    event_id: str
    provider_message_id: str
    kind: CorrespondenceKind
    disposition: CorrespondenceDisposition
    #: 这一条**之前已经落过库**（同一份原件重放、或游标回退之后重读）。重放不写
    #: 第二次事件，也不写第二条交接 —— 见 :func:`_persist`。
    replayed: bool = False
    handoff_id: str = ""
    #: 交接**现在**在存储里的状态。重放时它是库里那一条的状态（可能是
    #: ``NOTIFIED`` / ``ACCEPTED``）—— 这一格存在的理由，就是让「重放没有把一条
    #: 已经被销售接下的交接打回待办」这件事**看得见**。
    handoff_state: HandoffState | None = None
    #: 抑制行没写成时的原因（空 = 要么没打算写、要么写成了）。
    suppression_note: str = ""
    hold_reason: str = ""


@dataclass(frozen=True)
class IngestionReceipt:
    """``ingest`` 一次调用得出了什么结论。

    ## 三个字段就是三个步骤，顺序就是它们在这里的顺序

    ``holds`` 是第一步（压制，**在分类之前**整批跑完），``ingested`` 是第三步
    （落库，按处理顺序）。``cursor`` 回答「游标推到哪了」—— §7 逐字：「先持久化、
    再推进游标」，所以它是**最后**才定下来的那一格：没有它，调用方只能猜自己
    该不该把游标往前挪，而猜错的方向是**丢掉一段来信**。

    ## ``cursor`` 与 ``cursor_state`` 是两件事

    ``cursor`` 是「下一次从这里读」，``cursor_state`` 是「这个游标还算不算数」
    （提供方可以作废它，见 :class:`~marketops.ports.correspondence.CursorState`）。
    没落库时 ``cursor`` 退回 ``from_cursor`` 并给出 ``stopped_at``：接收方不需要
    猜，它读得出「哪一条之前是安全的」。
    """

    at: datetime
    workspace: str
    holds: tuple[HeldEvent, ...]
    ingested: tuple[IngestedEvent, ...]
    cursor: str
    cursor_state: CursorState
    from_cursor: str = ""
    #: 非空 = 这一条没落库（原因在 :attr:`failure` 里），游标没越过它。
    stopped_at: str = ""
    failure: str = ""

    @property
    def advanced(self) -> bool:
        """游标能不能推到 ``cursor``。**一个字段的两种读法**，不是第二份状态。"""
        return not self.stopped_at


@dataclass(frozen=True)
class _Plan:
    """一条条目的完整计划：压制结论 + 最终事件 + 要写的副作用。

    它是**内部**类型（前缀下划线）：调用方读的是 :class:`IngestionReceipt`，
    而计划里那两行（抑制、交接）说的是「打算这么写」，写没写成在回执里。
    """

    hold: HeldEvent
    item: CorrespondenceItem
    workspace: str
    at: datetime
    #: 关联不上的理由（空 = 关联上了）。压制那一步就算出来的，不重算第二遍。
    unlinked: str = ""
    attempt: OutboundAttempt | None = None
    wave: Wave | None = None
    event: CorrespondenceEvent | None = None
    suppression: RecipientSuppression | None = None
    handoff: SalesHandoff | None = None


def ingest(
    batch: CorrespondenceBatch,
    *,
    store: OutreachStore,
    classifier: ReplyClassifier,
    workspace: str,
    at: datetime,
) -> IngestionReceipt:
    """把一批原始事件收进库里，并定下每一条的处置。

    Args:
        batch: 一次读取的结论（条目 + 游标）。**空批次是正常的** —— 它就是
            「这一段没有来信」，此时回执里只有游标，没有别的。
        store: 状态库。这一层只认 ``ports.outreach.OutreachStore``，不认识
            sqlite 还是内存。
        classifier: 分类器。**只在来源没给分类时才被调用**，而且**在压制之后**。
        workspace: 这一批属于哪个 workspace。它是**调用方给的**，不是从消息里猜
            的：提供方消息 id 只在 ``(sendbox, id)`` 上唯一，而 workspace 是我们
            这边的归属。一个批次里混着两个 workspace 的来信时要分开喂。
        at: 这次处理的时刻。**带时区**，且不得早于任何一条的 ``received_at``
            （库里会拒 —— 我们不可能在它发生之前就知道）。

    Returns:
        这一次的结论。**处理失败不抛**：某一条落库失败时游标不前进，原因写在
        ``stopped_at`` / ``failure`` 里，调用方读得到「停在哪一条、为什么」。

    Raises:
        ValueError: 参数本身不合法（``at`` 没带时区、``workspace`` 不是规范 id）。
            它与「这一批处理失败」是两件事：前者重试多少次都一样。
    """
    moment = _utc(at)
    space = check_canonical_id(workspace, "workspace")

    holds = _hold_everything(batch, store=store, workspace=space, at=moment)
    plans = [_decide(hold, store=store, classifier=classifier) for hold in holds]

    ingested: list[IngestedEvent] = []
    stopped_at = ""
    failure = ""
    for plan in plans:
        try:
            ingested.append(_persist(plan, store=store, at=moment))
        except Exception as error:  # noqa: BLE001 —— 见下
            # **这一条失败，后面的不做了。** §7 要求游标只在事件落库之后才前进，
            # 而游标是一个游标：越过一条没落库的事件，就等于**丢掉它**。
            # 异常类型不挑：抛出来的是存储的实现（``StoreError``、sqlite 的
            # ``InterfaceError``），也可能是 pydantic 在说「这一行写不进去」——
            # 三者的处置在这里一样（停在这一条、把原文给人看），所以这里不假装
            # 自己分得清。
            stopped_at = plan.event.event_id
            failure = f"{type(error).__name__}: {error}"
            break

    settled = not stopped_at
    return IngestionReceipt(
        at=moment,
        workspace=space,
        # 交出去的是**压制那一步的结论**（``HeldEvent``），不是内部计划：``_Plan``
        # 还带着分类器的输入与「打算写什么」，调用方读的是「当时压住了什么」。
        holds=tuple(plan.hold for plan in plans),
        ingested=tuple(ingested),
        # 没有失败 → 推到提供方给的下一格；有失败 → 退回**这一批读出来的那一格**。
        cursor=batch.cursor if settled else batch.from_cursor,
        cursor_state=batch.cursor_state,
        from_cursor=batch.from_cursor,
        stopped_at=stopped_at,
        failure=failure,
    )


# ----------------------------------------------------------------------
# 第一步：压制（**在分类器被碰到之前，整批跑完**）
# ----------------------------------------------------------------------


def _hold_everything(
    batch: CorrespondenceBatch, *, store: OutreachStore, workspace: str, at: datetime
) -> list[_Plan]:
    """给每一条定下一条最保守的处置。**这一步不碰分类器。**

    它只做两件事：问存储「这个人在抑制名单上吗」，以及读「这条事件关联到哪一次
    发送」。两件都是**查询** —— §7 要求抑制是查出来的，所以这里是
    ``store.is_suppressed(recipient, at=at)``，而不是本层缓存的任何一份影子状态。
    """
    return [_hold(item, store=store, workspace=workspace, at=at) for item in batch.items]


def _hold(
    item: CorrespondenceItem, *, store: OutreachStore, workspace: str, at: datetime
) -> _Plan:
    """一条条目的压制。**此时还没有最终事件。**

    处置先按来源自己给的分类定（``None`` 就用最保守的那一格），因为那一步不需要
    任何外部调用 —— 而**这一步的结论在分类器被调用之前就已经完整**，正是「先保守
    地暂停后续外联，然后再分类」那句话的形状。
    """
    attempt, unlinked = _link(item, store=store)
    correspondent = attempt.recipient if attempt is not None else item.sender
    already_suppressed = bool(correspondent) and store.is_suppressed(correspondent, at=at)
    if item.kind is not None:
        disposition = KIND_DISPOSITION[item.kind]
        note = f"来源自己给了分类 {item.kind.value}，按表压到 {disposition.value}。"
    else:
        disposition = CorrespondenceDisposition.MANUAL_REVIEW
        note = f"来源没说这是什么，先压在最保守的一格（MANUAL_REVIEW）：{unlinked}。"
    if already_suppressed:
        note += (
            f" 另外，{correspondent!r} 这一刻已经在抑制名单上 —— 分类器不得把这条"
            "抑制翻过来。"
        )
    return _Plan(
        hold=HeldEvent(
            provider_message_id=item.provider_message_id,
            correspondent=correspondent,
            already_suppressed=already_suppressed,
            disposition=disposition,
            note=note,
        ),
        item=item,
        workspace=workspace,
        at=at,
        unlinked=unlinked,
        attempt=attempt,
    )


def _link(
    item: CorrespondenceItem, *, store: OutreachStore
) -> tuple[OutboundAttempt | None, str]:
    """这条事件关联到哪一次发送。**关联不上时返回 ``None`` 与一句理由。**

    关联不上**不是**错误：它意味着我们收到了一封不知道对应哪封信的回信，而那个
    事实必须存下来、交给人看（§7：一条不知道对应哪封信的回信绝不能被丢掉）。
    所以这里返回的是「为什么关联不上」，不是抛异常。

    ## 三条路，次序是承重的：``send_key`` → **投递状态通知** → 引用

    :func:`_link_by_send_key` 是第一条：事件自己带了我们发出去时用的那个键，那么
    它**精确地**指着一次尝试，没有第二种解释。

    第二条是 :func:`_link_by_bounce`：一封投递状态通知（DSN）自己带着**被退的
    地址**与**它报告的那一封**（RFC 3464 的 ``Final-Recipient`` 与
    ``Original-Message-ID``）。这两格是收信服务器写下的，与 ``send_key`` 一样是
    「说出来的事实」而不是「推断出来的结论」—— 所以它在第三条**之前**。

    第三条是 :func:`_link_by_reference`：事件没带 ``send_key``（或者带的那个在库里
    查不到）时，改用**对方回显的那一封**去查 —— 我们发信时把**我们自己生成**的
    ``Message-ID`` 记在 ``OutboundAttempt.provider_message_id`` 上
    （``application/outreach.py`` 的 ``dispatch_wave``：两个传输实现都返回我们生成
    的那一个），而对方的回信把那个字符串原样抄在 ``In-Reply-To`` 里。

    **次序不能换。** 前两条各是一次**精确**的空间（我们的去重键 / 服务器报出的那一
    对事实），第三条要求「发件人的历史里恰好一次写着这个 Message-ID」，它多两个前提
    （发件人没写错、这个标识在库里唯一）。把引用放在前面，会把一个**本来能精确
    关联**的事件降级成一次模糊匹配 —— 而"降级"的症状不是红，是关联到了**隔壁
    那一次发送**：处置照常执行，跟进却停在了另一个人那一封上。

    ## 第三条路对一封 DSN 是**结构性**走不通的

    这不是「很少见」：投递状态通知的发件人是对方那台收信服务器
    （``MAILER-DAEMON@…``），而第三条路先在**发件人的**投递历史里找 —— 查一台邮件
    守护进程的历史，答案恒为零条；它也没有 ``In-Reply-To``（那是**人**回信时才有的
    头字段），函数会在第一次判断就返回。所以在第二条路之前，一封硬退信**没有**任何
    关联得上的可能，而 §7 那两条（抑制、停跟进）都以「关联得上」为前提 —— 它们
    一条都不会产生，报告上却只显示「一条进人工审核的事件」。

    ## 关联上了之后，事件记的是**那一次尝试的** ``send_key``

    ``_link`` 的返回值一路走到 :func:`_event`：``send_key=plan.attempt.send_key``。
    所以走引用这条路关联上的事件，记的不是引用、也不是空串，而是**那次尝试真正的
    键**。这不是细节，它是这条路存在的理由之一：``application/outreach.py`` 的
    :func:`~marketops.application.outreach.stopped_by_reply` 按
    ``event.send_key`` 找「说过停的回信」（它的次链是发件人地址，而次链在转发、
    别名、两个人共用一个信箱时会把停发落在错的人身上）。一封「不要再联系我」的回信
    如果两条链都没接上，它就会在**下一批**里照常收到信，而每一行记录都显示处置正确。
    """
    attempt, unlinked = _link_by_send_key(item, store=store)
    if attempt is not None:
        return attempt, ""
    attempt, bounce_reason = _link_by_bounce(item, store=store)
    if attempt is not None:
        return attempt, ""
    attempt, reference_reason = _link_by_reference(item, store=store)
    if attempt is not None:
        return attempt, ""
    # 三条路都没成：把**三条**理由都给出来。只报一条的实现在排查时会指向错的方向
    # ——「没有带 send_key」「投递状态通知里那一对被退的地址/那一封对不上」「按引用
    # 也没关联上」是三条不同的线索（第一条说来源没给证据，第二条说这封退信说的不是
    # 我们发过的那一封，第三条说我们这边没有那一封）。
    return None, (
        f"{unlinked}；按投递状态通知关联也没成：{bounce_reason}；"
        f"按引用再关联一次也没成：{reference_reason}"
    )


def _link_by_send_key(
    item: CorrespondenceItem, *, store: OutreachStore
) -> tuple[OutboundAttempt | None, str]:
    """第一条路：事件自己带的 ``OutboundAttempt.send_key``。

    （这一段是本函数原先的全部内容，逐字保留 —— 见 :func:`_link`：「次序不能换」
    说的是这条路径**先被问**，它也一个字都没被改动。）
    """
    if not item.send_key:
        return None, "这条事件没有带 send_key，不知道对应哪一封信"
    try:
        key = check_canonical_id(item.send_key, "send_key")
    except ValueError as error:
        # 一个形状不对的 send_key 与「没有 send_key」是同一件事的两面：都关联不上。
        # 这里**不抛**：抛出去会让整批停在这一条上，而这一条能做的事（把原件存
        # 下来、交给人看）一件都不比别的条目少。
        return None, f"send_key={item.send_key!r} 的形状用不了（{error}）"
    attempt = store.attempt(key)
    if attempt is None:
        return None, f"send_key={key!r} 在库里关联不到任何一次发送"
    return attempt, ""


def _link_by_bounce(
    item: CorrespondenceItem, *, store: OutreachStore
) -> tuple[OutboundAttempt | None, str]:
    """第二条路：一封**投递状态通知**自己说的那一对被退的地址与那一封。

    ## 它修的是哪一种真实的关联不上

    在它之前，一封硬退信**结构上**关联不上任何一次发送，于是 §7 的两条要求
    （硬退信 → 按 SOP 抑制并保留证据；以及「关联不上就不抑制」那一刻的保守）
    只剩下后一半。前面那些是它的成因：投递状态通知的发件人是对方那台收信服务器，
    不是被退的收件人；它也不会带 ``In-Reply-To``（那是人回信时的头字段）。

    而它**有** ``Final-Recipient``（谁没收到）与 ``Original-Message-ID``（我们发出去
    的那封信的 ``Message-ID``）—— 两格都写在块里，收信服务器填的。缺的从来不是
    事实，是有人去读它们。

    ## 判据：**恰好一条**才认。零条与多条都返回 ``None``，理由不同

    查询范围是 ``store.attempts_by_recipient(item.bounce_recipient)``，而这一格
    取自通知本身。**这保证了一件事**：关联上之后 ``plan.attempt.recipient`` 必然
    就是那个被退的地址 —— 而 :func:`_suppression` 写下抑制行用的正是它。所以这条
    路**不可能**把抑制写到另一个人身上，哪怕下面挑错了「哪一次发送」（挑错的代价
    是「哪一封被记了事件」，不是「谁被拉黑」）。

    ``Original-Message-ID`` 给了就在候选里按它筛；没给就不筛 —— 但那只在候选**恰好
    一条**时才有结论：同一个地址收过两封而我们不知道退的是哪一封时，两个答案都说得
    通，而挑错的症状是后续的停跟进挂到了另一封上。

    多条为什么不是「挑一个」，与 :func:`_link_by_reference` 逐字同一条理由：猜错的
    那一次会让停跟进与交接挂到另一封上，而记录上处置照常正确。
    """
    if not item.bounce_recipient:
        return None, (
            "这条事件没带被退的地址（它不是一份投递状态通知，或者那份通知里没有"
            "可用的 Final-Recipient）"
        )
    candidates = store.attempts_by_recipient(item.bounce_recipient)
    if not candidates:
        return None, (
            f"我们从来没有给 {item.bounce_recipient!r} 发过信 —— 而这一格取自那份"
            "投递状态通知自己写的 Final-Recipient。"
        )
    reference = item.bounce_original_message_id
    matches = (
        [row for row in candidates if row.provider_message_id == reference]
        if reference
        else list(candidates)
    )
    if not matches:
        return None, (
            f"{item.bounce_recipient!r} 的投递历史里没有任何一次发送写着 "
            f"Message-ID {reference}（那份通知的 Original-Message-ID）。\n"
            "这一条与「没有带被退的地址」是两条不同的线索：前者说这次投递不是"
            "我们发的，后者说来源没给证据。"
        )
    if len(matches) > 1:
        keys = sorted(row.send_key for row in matches)
        return None, (
            f"{item.bounce_recipient!r} 的投递历史里有 {len(matches)} 次发送都对得上"
            f"（{keys}）。\n"
            "一份通知没有说出是哪一封时，两个答案都说得通 —— 挑一个会让后续的"
            "停跟进挂到另一封上，而那在记录上看不出来。"
        )
    return matches[0], ""


def _link_by_reference(
    item: CorrespondenceItem, *, store: OutreachStore
) -> tuple[OutboundAttempt | None, str]:
    """第二条路：**对方回显的那一封**（``In-Reply-To`` / ``References``）。

    ## 它修的是哪一种真实的关联不上

    在它之前，一封回信能不能关联上，取决于**来源有没有把 ``send_key`` 填进来**。
    而一个真实存在的来源（本仓的 ``adapters/mail_capture_source.py``）**填不出来**：
    对方回信里没有 ``send_key`` 这个东西（它是我们去重用的键，从不出现在信里，
    见 ``domain/outreach.py`` 的 ``OutboundAttempt``）。它填得出来的是
    ``in_reply_to`` —— 而那一格，按契约，**不在这里翻译成 ``send_key``**。
    于是每一封通过捕获目录读回来的回信都关联不上，全部落进人工审核：链路的两端
    各自都建好了，中间那一格是空的。

    这条路把那一格接上：``item.in_reply_to`` 与 ``OutboundAttempt.provider_message_id``
    是**同一个字符串**（我们写进 ``Message-ID`` 的那一个，对方原样抄回来）。

    ## 判据：**恰好一条**才认。零条与多条都返回 ``None``，理由不同

    查询范围是 ``store.attempts_by_recipient(item.sender)`` —— 发件人的**全部**
    投递历史（跨批次，这正是这个端口方法的语义）。范围先用发件人夹住，是因为
    Message-ID 只在「谁写给谁」这个语境里有意义：不夹住的话，一次「同一个正文
    摘要发给同一个人」在另一个收件人身上的重复投递会把候选变多。

    **为什么多条不是「挑一个」**：``provider_message_id`` 由
    ``adapters/mail_capture_transport.py::stable_message_id`` 生成，而它是
    ``(正文摘要, 收件人)`` 的派生值 —— **稳定**是它的设计目标。于是两次不同的
    发送（同一封信、同一个收件人、两个批次）会**合法地**共用同一个 Message-ID。
    这时「对方回的是哪一封」在库里**没有答案**，而猜错的那一次会让后续的停跟进、
    交接挂到另一封上（``stopped_by_reply`` 按 ``send_key`` 找事件）。所以多条与
    零条一样返回 ``None``：它们都是「交给人」，不是「找一条像的」。

    ## 两个前置条件都是「查不了」，不是「查不到」

    ``in_reply_to`` 为空（对方没引用任何一封）与 ``sender`` 为空（提供方没给发件人）
    时**直接返回**，不做那次查询：后者尤其要紧 —— 用空发件人去查全库历史，会用
    「我不知道他是谁」换来一次匹配，而任何一次这样的匹配都是猜。
    """
    if not item.in_reply_to:
        return None, (
            "这条事件没有带对方回显的那一封（In-Reply-To / References 都是空的），"
            "无从按引用找起"
        )
    if not item.sender:
        return None, (
            "这条事件没有发件人地址。按引用找要先知道去**谁**的投递历史里找 —— "
            "不知道对手是谁，任何一次匹配都是猜"
        )
    reference = item.in_reply_to.strip()
    matches = [
        row
        for row in store.attempts_by_recipient(item.sender)
        if row.provider_message_id == reference
    ]
    if not matches:
        return None, (
            f"发件人 {item.sender!r} 的投递历史里，没有任何一次发送写着 "
            f"Message-ID {reference}。\n"
            "这一条与「没有带 send_key」是两条不同的线索：前者说我们这边没有那一封，"
            "后者说来源没给证据。"
        )
    if len(matches) > 1:
        keys = sorted(row.send_key for row in matches)
        return None, (
            f"发件人 {item.sender!r} 的投递历史里有 {len(matches)} 次发送都写着同一个 "
            f"Message-ID {reference}（{keys}）。\n"
            "这个标识在我们这边**不唯一**（它由 (正文摘要, 收件人) 派生，同一封信在"
            "两个批次里会合法地共用它），所以「对方回的是哪一封」在库里没有答案。"
            "挑一个是猜，而猜错会让停跟进与交接挂到另一封上。"
        )
    return matches[0], ""


# ----------------------------------------------------------------------
# 第二步：分类（此时压制已经整批生效）
# ----------------------------------------------------------------------


def _decide(plan: _Plan, *, store: OutreachStore, classifier: ReplyClassifier) -> _Plan:
    """分类，然后按 domain 那张表把处置定下来。**这是整批里的第二个循环。**

    判定的次序写在这里，因为它**就是**判据：先关联、再分类结论、再看抑制、
    再看批次、最后看正文里的替代联系人。每一次「降级」都带一条说得出的理由 ——
    没有理由的 ``MANUAL_REVIEW`` 会被 domain 直接拒掉。
    """
    item = plan.item
    event_id = _event_id(plan.workspace, item)
    kind, hold_reason, failure = _kind_of(plan, classifier=classifier)
    disposition = KIND_DISPOSITION[kind]

    # 1. 关联不上 → 人工审核。既不抑制、也不交接：不知道这个人是谁（收件人取自
    #    哪一次发送），也就不知道该停哪一条跟进，更不该凭空造一条交接。
    if plan.attempt is None:
        return _review(plan, event_id=event_id, kind=kind, why=f"关联不上：{plan.unlinked}")
    # 2. 分类这一步本身没成（模型抛了、或者它说它分不出来）→ 人工审核。
    #    理由说的是「谁没干成什么」，与「内容模糊」分开说。
    if failure:
        return _review(plan, event_id=event_id, kind=kind, why=failure)
    # 3. §7 要求「有效人类回信 → 停止自动跟进 + 产出交接」，而**抑制比交接强**：
    #    一个已经在抑制名单上的收件人的来信，不会因为被分成了有效人类回信而复活。
    #    分类器在这里没有否决权 —— 它能让这条记录带着理由去人工那里，仅此而已。
    if disposition is CorrespondenceDisposition.HANDOFF and plan.hold.already_suppressed:
        return _review(
            plan,
            event_id=event_id,
            kind=kind,
            why=(
                f"{plan.hold.correspondent!r} 已经在抑制名单上，而这封回信被分成了 "
                f"{kind.value}（按表该产出一条销售交接）。两个事实冲突，交给人："
                "一条已经生效的抑制不因为一次分类结果而失效。"
            ),
        )
    # 4. 交接要写到哪个活动、哪个场景，取自这一批（wave）—— 读不到就写不出一条
    #    完整的交接，而一条字段靠猜的交接会把销售带到错的地方。
    wave = store.wave(plan.attempt.wave_id)
    if disposition is CorrespondenceDisposition.HANDOFF and wave is None:
        return _review(
            plan,
            event_id=event_id,
            kind=kind,
            why=(
                f"发送尝试 {plan.attempt.send_key!r} 指向的批次 "
                f"{plan.attempt.wave_id!r} 在库里读不到，于是这条交接属于哪个活动、"
                "哪个场景无从确定。"
            ),
        )
    # 5. 正文里字面地给了另一个联系人 → 人工审核。判据是保守的：见
    #    :func:`_alternate_contact`。只有处置本来会**动起来**的两类才降级：
    #    ``RECORD_ONLY`` 本来就不产生新的外联，``SUPPRESS`` 是最强的一格。
    alternate = _alternate_contact(item)
    if alternate and disposition in (
        CorrespondenceDisposition.HANDOFF,
        CorrespondenceDisposition.STOP_FOLLOWUP,
    ):
        return _review(
            plan,
            event_id=event_id,
            kind=kind,
            why=(
                f"回信里给了一个替代联系人（{alternate!r}），需要人工确认是否换人。"
                "本层不自动重发、不自动新建外联（§7）。"
            ),
        )

    return _Plan(
        hold=plan.hold,
        item=item,
        workspace=plan.workspace,
        at=plan.at,
        unlinked=plan.unlinked,
        attempt=plan.attempt,
        wave=wave,
        event=_event(
            plan, event_id=event_id, kind=kind, disposition=disposition, hold_reason=hold_reason
        ),
        suppression=(
            _suppression(plan, event_id=event_id, kind=kind)
            if _suppresses(disposition)
            else None
        ),
        handoff=(
            _handoff(plan, event_id=event_id, wave=wave)
            if disposition is CorrespondenceDisposition.HANDOFF
            else None
        ),
    )


def _kind_of(plan: _Plan, *, classifier: ReplyClassifier) -> tuple[CorrespondenceKind, str, str]:
    """这条事件的分类，以及分类这一半的理由或失败原因。

    Returns:
        ``(kind, hold_reason, failure)``。``failure`` 非空 = 分类这一步没干成，
        而 :func:`_decide` 必须把**两种失败分开**处置：``分类失败``（模型抛了 /
        返回了读不懂的东西）与 ``弃权``（模型说它分不出来）的理由不同 ——
        前者的处置是「先修好它」，后者是「换个办法再看看」。

    来源自己给了分类时**不调分类器**：那是一条已知的事实，不是一次推断。
    """
    if plan.item.kind is not None:
        return plan.item.kind, "", ""
    # 分类器拿到的是一条**已经压制过**的事件：它的处置是保守的那一格，而
    # hold_reason 说的就是「先压住、再分类」这件事本身。
    conservative = _event(
        plan,
        event_id=_event_id(plan.workspace, plan.item),
        kind=CorrespondenceKind.HUMAN_REPLY,
        disposition=CorrespondenceDisposition.MANUAL_REVIEW,
        hold_reason=CONSERVATIVE_HOLD,
    )
    try:
        answer = classifier.classify(event=conservative)
    except Exception as error:  # noqa: BLE001 —— 见下
        # 模型抛异常、远端超时、SDK 在解析自己的返回值时炸了 —— 对**这一条**来
        # 说都是同一件事：这次分类没有结论。原件已经（且仍然会）落库，处置进人工
        # 审核，而理由说的是「分类失败」，不是「内容不明确」。
        return (
            CorrespondenceKind.HUMAN_REPLY,
            "",
            f"分类失败（{type(error).__name__}: {error}）—— 原件已留存，可以换个"
            "时候或换个分类器重跑；这与「内容不明确」（分类器弃权）是两件事。",
        )
    if not isinstance(answer, Classification):
        return (
            CorrespondenceKind.HUMAN_REPLY,
            "",
            f"分类失败：分类器返回了 {type(answer).__name__}，而不是 Classification。"
            "一条读不出结论的返回值与一次崩溃在这里是同一件事。",
        )
    if answer.kind is None:
        return (
            CorrespondenceKind.HUMAN_REPLY,
            "",
            f"分类器弃权（它说不出这是什么）：{answer.reason}。"
            "弃权不是「有效人类回信」（那会产出一条销售交接），也不是「只记录」"
            "（那会把一封「请不要再联系我」放下）。",
        )
    return answer.kind, answer.reason, ""


def _review(plan: _Plan, *, event_id: str, kind: CorrespondenceKind, why: str) -> _Plan:
    """一条进人工审核的事件。**其余字段一个都不许丢。**

    它落库；它带理由；它不带任何副作用。这是本模块里唯一一处「什么都不做」的
    处置，而它必须是最容易被读出来的那一处。
    """
    return _Plan(
        hold=plan.hold,
        item=plan.item,
        workspace=plan.workspace,
        at=plan.at,
        unlinked=plan.unlinked,
        attempt=plan.attempt,
        wave=plan.wave,
        event=_event(
            plan,
            event_id=event_id,
            kind=kind,
            disposition=CorrespondenceDisposition.MANUAL_REVIEW,
            hold_reason=why,
        ),
    )


# ----------------------------------------------------------------------
# 第三步：落库（事件在先，副作用在后；失败时游标停在这一条之前）
# ----------------------------------------------------------------------


def _persist(plan: _Plan, *, store: OutreachStore, at: datetime) -> IngestedEvent:
    """把这一条落库，然后写它的副作用。

    ## 重放不写第二次

    同一份原件重放（webhook 重试、游标回退）时，事件已经在库里了 —— 本节
    **不写第二次事件**，也**不写第二条交接**：

    * **事件**：先按 ``dedupe_key`` 读一次库里那一条。为什么不是只靠
      ``put_event`` 的返回值（重放时它返回 ``False``，而本层照样不写第二条交接）：
      ``put_event`` 的重复判据是**逐字节相等**，而事件里有一格 ``ingested_at`` 是
      **这一次**跑的时刻 —— 于是「同一封回信在两天后重放」在存储层是一件**冲突**
      （同键不同内容，抛 ``StoreError``），不是一次重放。先读出来，本层才分得开
      这两件事：``raw_sha256`` 相同 → 同一份原件，是重放；不同 → 一个键装了两份
      原件，那正是存储层该报的冲突，交给它报。
    * **交接**：先查 ``store.handoffs(workspace=...)`` 里有没有挂着**同一封回信**
      （``event_id``）的那一条。**不是**靠 ``put_handoff`` 的覆盖写来幂等：覆盖写
      会把一条已经被销售 ``NOTIFIED`` / ``ACCEPTED`` 的交接悄悄打回
      ``PENDING_LOCAL``，而报告上那看起来只是「更新了一下」。
    * **抑制**：先查这一刻他是否已经在抑制名单上（或者同一 (收件人, 理由) 的那一
      行已经在库里）。存储层把同一 (收件人, 理由) 的**第二次不同内容**的写入判成
      ``StoreError``（一条解除之后再收到同一理由的投诉是两条事实，它拒绝就地
      改写），所以本层不重复写 —— 那一次投诉的**新事实**落在事件行上，而它才是
      这件事的证据。

    重放时副作用仍然**补写**（写之前查过，所以是幂等的）：上一次跑到一半、崩在
    写交接之前的那些条目，会在重读时被补上，而不是永远缺一条。
    """
    existing = _stored_event(store, plan.event)
    if existing is None:
        # 事件在先：§7 要求「先持久化、再推进游标」，而这条原始事件就是那个
        # 「持久化」。写入抛异常 → 这一条整个失败（异常抛给 ingest），游标不前。
        replayed = not store.put_event(plan.event)
    elif existing.raw_sha256 == plan.item.raw_sha256:
        replayed = True
    else:
        # 同一个去重键、不同的原件。**这不是重放** —— 把判断交回给存储层，它是
        # 「一个键能不能装下两份内容」的权威，而它的答案是抛。
        store.put_event(plan.event)
        raise StoreError(
            f"去重键已存在（event={existing.event_id}，原件 {existing.raw_sha256[:12]}…），"
            f"而这一份原件是 {plan.item.raw_sha256[:12]}…：存储层没有把它判成冲突。\n"
            "一个不判冲突的存储会让两封不同的信共用一条记录 —— 那是**静默合并**，"
            "比崩溃难查得多。"
        )

    suppression_note = _write_suppression(plan, store=store, at=at)
    handoff_id, handoff_state = _write_handoff(plan, store=store)
    return IngestedEvent(
        event_id=plan.event.event_id,
        provider_message_id=plan.item.provider_message_id,
        kind=plan.event.kind,
        disposition=plan.event.disposition,
        replayed=replayed,
        handoff_id=handoff_id,
        handoff_state=handoff_state,
        suppression_note=suppression_note,
        hold_reason=plan.event.hold_reason,
    )


def _write_suppression(plan: _Plan, *, store: OutreachStore, at: datetime) -> str:
    """写那行抑制。返回「为什么没写」；空串 = 写成了（或者本来就不用写）。

    两次查询都是**当场问存储**（§7 要求抑制是查出来的）：``is_suppressed`` 回答
    「现在这个人被抑制着吗」，``suppressions`` 回答「这个 (收件人, 理由) 有没有过
    一行」。第二个问题必须问，因为存储层把**同一个 (收件人, 理由) 的第二次不同
    内容的写入**判成冲突：一条被解除过之后又来的同理由投诉，在那一层装不下第二条。
    """
    suppression = plan.suppression
    if suppression is None:
        return ""
    recipient = suppression.recipient
    if store.is_suppressed(recipient, at=at):
        return (
            f"{recipient!r} 这一刻已经在抑制名单上，不写第二行"
            "（那一行已经回答了「这个人被抑制过」；这一次投诉本身落在这条事件行上）。"
        )
    same_reason = [
        row for row in store.suppressions(recipient) if row.reason is suppression.reason
    ]
    if same_reason:
        return (
            f"{recipient!r} 已经有过一条 (收件人, 理由)={suppression.reason.value} 的"
            "抑制行、但已被解除。存储层把同一个 (收件人, 理由) 记成**一条**事实，"
            "写不下第二条：这一条新事实落在事件行上，要不要重新加抑制、用什么理由加，"
            "交给人。"
        )
    store.put_suppression(suppression)
    return ""


def _write_handoff(plan: _Plan, *, store: OutreachStore) -> tuple[str, HandoffState | None]:
    """写那条交接，或者认出它已经在库里。返回 ``(handoff_id, 库里的状态)``。

    判据是 ``event_id``（哪一封回信），**不是** ``handoff_id``：后者的算法住在
    本模块里（派生 id 的算法哪天改了，重复到达就会多出一条交接），而 ``event_id``
    是「哪一封回信」本身。库里那一条的状态被**原样报回去** —— 一条已经被销售接下
    的交接，在重放之后仍然是被接下的。
    """
    handoff = plan.handoff
    if handoff is None:
        return "", None
    for existing in store.handoffs(workspace=handoff.workspace):
        if existing.event_id == handoff.event_id:
            return existing.handoff_id, existing.state
    store.put_handoff(handoff)
    return handoff.handoff_id, handoff.state


def _stored_event(
    store: OutreachStore, event: CorrespondenceEvent
) -> CorrespondenceEvent | None:
    """库里与这条同键的那一条（没有就返回 ``None``）。

    ``events(workspace=...)`` 是端口上唯一的按 workspace 读事件的入口，所以这里是
    一次扫描。**一次调用读一批**，而这一批就是「这个 workspace 收到过的全部来信」
    —— 重放判据要求在写入之前看到它，否则 see-then-write 的两步只能靠 ``put_event``
    的返回值，而那个返回值分不出「重放」与「同键不同原件」（见 :func:`_persist`）。
    """
    for candidate in store.events(workspace=event.workspace):
        if candidate.dedupe_key == event.dedupe_key:
            return candidate
    return None


# ----------------------------------------------------------------------
# 构造：事件、抑制行、交接、证据 id
# ----------------------------------------------------------------------


def _event(
    plan: _Plan,
    *,
    event_id: str,
    kind: CorrespondenceKind,
    disposition: CorrespondenceDisposition,
    hold_reason: str = "",
) -> CorrespondenceEvent:
    """一条事件。**逐字带上原件的那两样**（``raw_ref`` / ``raw_sha256``）。

    ``settled_note`` 写的是**这次处置打算执行的那一步**（``handoff:<id>`` /
    ``suppress:<收件人>:<理由>``）：它随事件行一起写下，读起来是一个决定；而「写
    没写成」在 :class:`IngestionReceipt` 里 —— 那是这一次跑的结论。两者分开，是
    因为这个字段**不可改写**（同一去重键的第二次写入要么逐字节相等、要么是冲突），
    而一次跑的结果本来就可能与决定不同。
    """
    item = plan.item
    return CorrespondenceEvent(
        event_id=event_id,
        workspace=plan.workspace,
        sendbox=item.sendbox,
        kind=kind,
        provider_message_id=item.provider_message_id,
        send_key=plan.attempt.send_key if plan.attempt is not None else "",
        sender=item.sender,
        subject=item.subject,
        body=item.body,
        raw_ref=item.raw_ref,
        raw_sha256=item.raw_sha256,
        received_at=item.received_at,
        ingested_at=plan.at,
        disposition=disposition,
        settled_note=_settled_note(plan, kind=kind, disposition=disposition),
        hold_reason=hold_reason,
    )


def _settled_note(
    plan: _Plan, *, kind: CorrespondenceKind, disposition: CorrespondenceDisposition
) -> str:
    """这一条打算做什么。**与决策一起写下**，见 :func:`_event`。"""
    if disposition is CorrespondenceDisposition.HANDOFF:
        return f"handoff:{_handoff_id(plan.workspace, _event_id(plan.workspace, plan.item))}"
    if disposition is CorrespondenceDisposition.SUPPRESS:
        reason = _suppression_reason(kind, disposition)
        return f"suppress:{plan.attempt.recipient}:{reason.value}"
    return ""


def _suppression(plan: _Plan, *, event_id: str, kind: CorrespondenceKind) -> RecipientSuppression:
    """一行持久抑制。**立刻**（§7）—— 收件人取那次发送的收件人，不是发件人。

    取 ``attempt.recipient`` 而不是 ``item.sender``：退信与退订是**收信服务器或
    收信人**发回来的，而我们要抑制的是**我们发过的那个地址**。两者正常情况下相同，
    而不同时（转发、别名、退信由 postmaster 代发）抑制错地址的代价是那个人继续
    收到信。
    """
    return RecipientSuppression(
        recipient=plan.attempt.recipient,
        reason=_suppression_reason(kind, KIND_DISPOSITION[kind]),
        source_ref=event_id,
        suppressed_at=plan.at,
        note=f"来自一条 {kind.value} 事件（提供方消息 id {plan.item.provider_message_id}）。",
    )


def _suppression_reason(
    kind: CorrespondenceKind, disposition: CorrespondenceDisposition
) -> SuppressionReason:
    """分类 → 抑制行上的理由。**键取自 domain 的两张表，本层不重写一张。**

    判据是**处置**（只有 ``SUPPRESS`` 才写抑制行），而理由按**同名取值**从
    :class:`SuppressionReason` 里取：两个闭集的词汇表哪天分岔了，构造枚举的那一刻
    就会抛 ``ValueError``，而不是静默写下一行理由错位的抑制。
    """
    if not _suppresses(disposition):
        raise StoreError(f"{kind.value} 的处置是 {disposition.value}，它不该产生抑制行。")
    return SuppressionReason(kind.value)


def _suppresses(disposition: CorrespondenceDisposition) -> bool:
    """「这条回信要不要落一行持久抑制」—— **判据是处置表**，见模块 docstring。

    这里原先还有一条下界校验（「处置是 ``SUPPRESS`` 就必须在
    ``SUPPRESSING_KINDS`` 里」），挡的是「同一个文件里两张表漂了」。而那张集合现在
    **由处置表推出来**（见 ``domain/outreach.py``），漂移在构造上不可能发生 ——
    一条永远不会响的校验只会让人以为这里有人看着。判据只有一处，就是这一句。
    """
    return disposition is CorrespondenceDisposition.SUPPRESS


def _handoff(plan: _Plan, *, event_id: str, wave: Wave | None) -> SalesHandoff:
    """一条销售交接。``state`` 停在 ``PENDING_LOCAL`` —— 推给外部系统是别人的事。

    ``reply_body`` 是**逐字**的正文：交接的核心就是让销售读原文（``SalesHandoff``
    的 docstring 逐字）。``workspace`` / ``scene_id`` / ``campaign_id`` 取自那一次
    发送与它所属的批次，**不从消息里猜**。
    """
    item = plan.item
    attempt = plan.attempt
    return SalesHandoff(
        handoff_id=_handoff_id(plan.workspace, event_id),
        workspace=plan.workspace,
        scene_id=wave.scene_id,
        campaign_id=wave.campaign_id,
        account_id=attempt.account_id,
        route_ref=attempt.route_ref,
        event_id=event_id,
        recipient=attempt.recipient,
        reply_body=item.body,
        evidence_ids=_evidence_ids(plan.workspace, item),
        state=HandoffState.PENDING_LOCAL,
        created_at=plan.at,
    )


def _event_id(workspace: str, item: CorrespondenceItem) -> str:
    """事件 id。**从「哪一份原件」派生**，于是重放拿到同一个 id。

    里面有 ``workspace``：提供方消息 id 只在 ``(sendbox, id)`` 上唯一，而 workspace
    是我们这边的归属。同一个 ``(sendbox, 消息 id)`` 出现在两个 workspace 里时，
    两条事件 id 不同、去重键相同 —— 那正是存储层该报的冲突（同一份原件不可能属于
    两个 workspace）。
    """
    digest = sha256_text(SEP.join(("correspondence", workspace, item.sendbox,
                                   item.provider_message_id)))
    return check_canonical_id(f"event-{digest[:16]}", "event_id")


def _handoff_id(workspace: str, event_id: str) -> str:
    """交接 id。**从事件 id 派生**：一封回信只可能有一条交接。

    它是重放防线里的第二道（第一道是 :func:`_write_handoff` 按 ``event_id`` 查库里
    那一条）：两道防线是同一个事实的两种写法，而第二道在没有第一道的地方仍然成立
    —— 两个驱动**并发**跑到这里时，``put_handoff`` 的覆盖写落在同一个 ``handoff_id``
    上，于是库里的交接仍然只有一条。
    """
    digest = sha256_text(SEP.join(("handoff", workspace, event_id)))
    return check_canonical_id(f"handoff-{digest[:16]}", "handoff_id")


def _evidence_ids(workspace: str, item: CorrespondenceItem) -> tuple[str, ...]:
    """回信这条证据的 id。**见模块 docstring 的「关于证据 id」。**

    它由事件自己的字段派生，而不是引用一枚 ``Evidence`` 行：本层造不出真的
    ``Evidence``（``source_kind`` 的闭集里没有「回信」这一种），而伪造一个来源种类
    比没有证据更坏。这个 id 回答的是「是哪一份原件」—— 它可复算、稳定，并且与
    ``raw_ref`` / ``raw_sha256`` 指向同一件东西。
    """
    digest = sha256_text(
        SEP.join(("reply", workspace, item.sendbox, item.provider_message_id, item.raw_sha256))
    )
    return (check_canonical_id(f"correspondence-{digest[:16]}", "evidence_id"),)


def _alternate_contact(item: CorrespondenceItem) -> str:
    """正文里字面地给出的那个**替代联系人**；没有就返回空串。

    ## 为什么保守到这个程度

    §7 要求「字面上的替代联系人 → awaiting_Member_A_confirmation，不自动重发或新建
    外联」。也就是说：**认错了**的代价是白占一次人工确认，**漏认了**的代价是这封
    回信按正常路走（可能是产出一条交接、可能是停止跟进）—— 后者更贵，但认错的
    代价也不是零，而判据只有正文里那几个字。所以这里要求**两个条件同时成立**：

    1. 正文里有一个**字面**的线索短语（``please contact``、``请联系`` 之类）；
    2. 正文里**恰好有一个**与发件人不同的地址。

    只有第一条不认 —— 「请联系我们」是一句套话，它会让每封带这句话的回信都进人工
    队列。只有第二条不认 —— 签名、页脚、退订说明里的地址到处都是。「恰好一个」而
    不是「至少一个」：两个以上的地址（签名的、抄送的、法务的）说明这段正文里有一
    个人名表，而**换谁**正是这里不能猜的那件事。

    它仍然会漏（换了语言、说「我的同事」而不给地址）。漏掉的那些走它们本来的处置
    —— 这条判据只负责**不猜**，不负责认全。
    """
    folded = item.body.casefold()
    if not any(cue in folded for cue in ALTERNATE_CONTACT_CUES):
        return ""
    sender = normalise_email(item.sender)
    found = {normalise_email(address.rstrip(".")) for address in _ADDRESS_IN_TEXT.findall(item.body)}
    others = sorted(address for address in found if address and address != sender)
    if len(others) != 1:
        return ""
    return others[0]


def _utc(moment: datetime) -> datetime:
    """带时区的时刻。**naive 的现在被拒绝** —— 与 ``application/maintenance.py`` 同一条。"""
    if moment.tzinfo is None:
        raise ValueError("at 必须带时区 —— 见 domain/util.to_utc 的说明")
    return to_utc(moment)

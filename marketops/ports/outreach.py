"""``OutreachStore`` / ``MailTransport``：外向链的两个接缝。

## 为什么两个端口，而不是一个「外向后台」

它们守的失败**方向相反**：

* ``OutreachStore`` 守的是**记错了**：一封信发出去过，而库里没有那条记录；
  或者反过来，库里有一条「已发出」而它从未出去。它是一份账，账错了之后
  每一份报告都错，而且**没有任何一处会红**。
* ``MailTransport`` 守的是**发出去了**：它是唯一一个真的会把字节交给外界的
  接缝，所以它是授权判定、抑制判定、去重判定**全部之后**才允许被调用的那一个。

合成一个「发送服务」接口会让「谁负责检查授权」这个问题没有位置：调用方会
（合理地）以为那个服务自己会检查，而那个服务会（合理地）以为调用方已经查过了。
分成两个之后，检查在哪一段是明确的：在 ``application/outreach.py`` 里，
**在 ``transport.send`` 之前**。

## ``MailTransport`` 上为什么没有 ``suppress`` / ``authorize``

因为那两件事是**判断**，不是传输。一个带 ``authorize()`` 的传输接口会有第一个
实现把授权判定放在实现里，于是换一个后端就换了一套授权规则 —— 而那正是
§6 逐字要挡的「授权必须绑定到确切的清单」。

## 这个端口上**没有**的东西，逐条都是刻意的

* 没有 ``send_batch``。批量是编排的事（``application/outreach.py`` 逐封判定、
  逐封记录），一个批量入口会让「第 17 封失败」的处置变成「这一批失败」，
  而前面 16 封已经出去了。
* 没有 ``probe(address)`` / ``verify(address)``。§6 逐字禁止 SMTP 地址探测。
  这个端口**没有那个动词** —— 不是「有但不许调」，是没有地方可以调。
* 没有 ``status(message_id)`` 之外的读取。投递结果的读取是**对账**
  （``reconcile``），它按逻辑发送标识问「这一封怎么了」，而不是按对方地址问。

本模块**不得**出现任何厂商名，也不得 import 任何传输或存储引擎
（``smtplib`` / ``sqlite3`` / ``requests`` …）—— 判据由
``tests/test_ports_are_contracts_only.py`` 执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..domain.outreach import (
    ApprovedClaim,
    AttemptOutcome,
    CorrespondenceEvent,
    ManifestEntry,
    OutreachCampaign,
    OutreachDraft,
    OutreachManifest,
    OutboundAttempt,
    OutreachStage,
    RecipientSuppression,
    SalesHandoff,
    TemplateFamily,
    Wave,
    WaveAuthorization,
    WritingPolicy,
)
from ..domain.util import check_canonical_id, sha256_text


@dataclass(frozen=True)
class SendReceipt:
    """一次 ``send`` 的回话。**它只说提供方说了什么，不替我们下结论。**

    ``outcome`` 是三个取值之一（不是布尔）：

    * ``ACCEPTED`` —— 对方接收了这次投递，``message_id`` 是它给的标识。
    * ``REFUSED`` —— 对方明确拒了。``reason`` 说得出为什么。**可以重试。**
    * ``UNKNOWN`` —— **可能出去了、也可能没有**（超时、连接中断）。处置是
      先对账。与 ``REFUSED`` 分开的唯一理由是它们的处置相反，而从调用点看
      两者都是「没成功」。

    这个类是 **dataclass 而不是 pydantic 模型**：它是**传输层的一次回话**，
    不是一个要被持久化、要被报告引用的领域对象。领域对象是它被翻译成的
    :class:`~marketops.domain.outreach.OutboundAttempt`。做成模型会让传输层
    的实现被迫知道领域模型的校验规则，而那不是它的事。
    """

    outcome: str
    accepted: bool
    message_id: str = ""
    reason: str = ""
    #: 提供方给的原始回话，逐字。**不解析** —— 解析是翻译层的事，
    #: 而这里留原件是为了「对账时发现结论不对」这一天还有东西可看。
    raw_response: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in ("accepted", "refused", "unknown"):
            raise ValueError(
                f"outcome={self.outcome!r} 不是 accepted/refused/unknown 之一。\n"
                "三个取值各自对应一种处置：受理照常、可以重试、先对账。"
                "多一个取值就意味着多一种没人写过的处置。"
            )
        if self.accepted != (self.outcome == "accepted"):
            raise ValueError("accepted 与 outcome 不一致 —— 同一件事有两个字段就会漂移")
        if self.outcome == "refused" and not self.reason.strip():
            raise ValueError("明确被拒必须写明原因")
        if self.outcome == "accepted" and not self.message_id.strip():
            raise ValueError(
                "受理必须带回提供方给的标识。\n"
                "没有它，对账那一步就没有一个可以问对方的键 —— 而"
                "「提供方受理了」这句话本身在事后是不可核对的。"
            )


class MailTransport(ABC):
    """把**一封已经写好、已经判定过、并且已经记过账**的信交给外界。

    ## 它拿到的是一行清单项，不是一份草稿

    ``entry`` 是冻结清单里的那一行 —— 收件人、发送箱、阶段、正文摘要都在上面。
    契约之所以是这个形状，是因为**这一层不得改写正文**：给它一个
    :class:`~marketops.domain.outreach.OutreachDraft`，第一个实现就会顺手
    「把签名补上」或者「按后端要求调一下格式」，于是发出去的东西与批准的东西
    不再是同一份 —— 而这在记录上完全看不出来，因为摘要记的是**改之前**那一个。

    ``body`` 与 ``subject`` 单独传（而不是从别处读）是因为**实现不该去读库**：
    它是一段可以被单独测的传输。它们与 ``entry.draft.body_sha256`` 是否一致
    由调用方负责校验 —— 传输层校验不了，它没有那份草稿。
    """

    @abstractmethod
    def send(
        self,
        *,
        entry: ManifestEntry,
        subject: str,
        body: str,
        from_address: str,
    ) -> SendReceipt:
        """发一封。**抛出**的异常由调用方记成 ``DELIVERY_UNKNOWN``。

        为什么抛异常 ≠ ``UNKNOWN`` 这件事写在契约里：一次「连不上」与一次
        「超时了但对面可能收了」在实现里长得几乎一样，而它们的处置不同。
        实现若分不清，就抛 —— 调用方会保守地记 ``UNKNOWN``，那是安全的那一边。
        """

    @abstractmethod
    def status(self, message_id: str) -> SendReceipt:
        """问一封**已经发出去的信**现在怎么样了。对账用。

        它按提供方的标识问，**不按收件人地址问** —— 后者就是地址探测，
        被 §6 明确禁止。查不到时返回 ``UNKNOWN``（不是抛）：一封很久以前的信
        在提供方的窗口之外，与「这封信没发出去」在盘上是两件事，
        而调用方**必须**能区分它们 —— 所以这个接口的回答里带 ``reason``。
        """

    @property
    @abstractmethod
    def sender_identity(self) -> str:
        """这个传输**自己**是谁（发送箱地址）。

        它存在是为了让「清单上的发送箱」与「实际用的发送箱」不一致这件事
        看得见：两者不同时，调用方拒绝发送。一个不暴露自己身份的传输接口，
        会让「我们用的是哪个发送箱」变成部署时的秘密。
        """


class OutreachStore(ABC):
    """模板族、草稿、清单、授权、尝试、抑制、事件与交接的存储契约。

    ## 幂等边界：三种不同的行为，各自有理由

    * **版本化对象**（草稿、清单、Wave）：``put`` 时同一个版本号配不同内容是
      **报错**（有人手工改过 id），内容相同是幂等。与
      ``EvidenceStore.put_evidence`` / ``SceneStore.put_version`` 同一条纪律。
    * **回执型对象**（尝试）：按 :attr:`OutboundAttempt.send_key` 去重。
      同一次逻辑发送**永远只有一行** —— 这是「不得重复发」的执行器本身。
      重试是**同一个 send_key 的第 2 次尝试**，所以 ``attempt`` 由行数与
      ``begin_attempt`` 一起决定，不由调用方给。
    * **累积型对象**（事件、交接）：**不去重**。同一封回信被重放两次，
      第二次会得到一个不同的 ``event_id``（从内容算），而 ``lines`` 上
      因此看得到两行 —— 那正是「重放」与「两封一样的信」需要被分开的地方。

    ## 抑制是**查**出来的，不是推**出来的**

    ``is_suppressed`` 是异步的查询而不是读时缓存：抑制名单会被另一个进程改
    （投诉进来那一刻），而一个读缓存的实现会让「他刚投诉完、我们又发了一封」
    这件事在盘上看起来完全正常。
    """

    # --- 配置：模板族、写作策略、已批准说法 ---------------------------------

    # --- 配置：模板族、写作策略、已批准说法 ---------------------------------

    # 三张配置表是**版本化对象**，不是「一个 id 一行」。
    #
    # 它们的键是 ``(id, revision)``，判据与草稿逐字同一条：
    #
    # * 同一个 ``(id, revision)`` 配**相同**内容 → 幂等（同一次发布重放不报错）；
    # * 同一个 ``(id, revision)`` 配**不同**内容 → 报错。这就是「历史修订不可变」，
    #   而它不是洁癖：一条已经生成过的草稿记的是 ``(id, revision)``，改掉那一版会让
    #   那句记录**指向一份它当时没见过的配置**，而盘上看起来一切正常。
    # * 新的 ``revision`` 必须**接在最新一版之后**（``latest + 1``）。跳版会让中间
    #   那些版号的去向没有答案，而报告上只显示「多了一个版本」。—— 与
    #   ``OutreachDraft`` 那条 ``supersedes == draft_version - 1`` 同一条理由。
    #
    # 三个读法返回的都是**每个 id 的最新一版**，而不是全部版本。读和写这样分家不是
    # 矛盾：写信要用的是现在的话术，而**旧版必须还在** —— 丢掉它，「这一稿当时按哪一版
    # 写的」就变成一句查不到的话。今天没有任何调用方需要读某一版历史（真要读，加一个
    # 带 ``revision`` 参数的读法就行），但**保留**本身有调用方：上面第 1、2 条判据
    # 就是对着已经落盘的那些版本做的判断。

    @abstractmethod
    def put_family(self, family: TemplateFamily) -> None:
        """写入一族模板的**一版**。键是 ``(family_id, revision)``。"""

    @abstractmethod
    def families(self) -> tuple[TemplateFamily, ...]:
        """每个 ``family_id`` 的**最新**一版，按 ``family_id`` 升序。"""

    @abstractmethod
    def put_writing_policy(self, policy: WritingPolicy) -> None:
        """写入一份写作策略的**一版**。键是 ``(policy_id, revision)``。"""

    @abstractmethod
    def writing_policies(self) -> tuple[WritingPolicy, ...]:
        """每个 ``policy_id`` 的**最新**一版，按 ``policy_id`` 升序。"""

    @abstractmethod
    def put_claim(self, claim: ApprovedClaim) -> None:
        """写入一条已批准说法的**一版**。键是 ``(claim_id, revision)``。"""

    @abstractmethod
    def approved_claims(self) -> tuple[ApprovedClaim, ...]:
        """每个 ``claim_id`` 的**最新**一版，按 ``claim_id`` 升序。
        **不在这里过滤过期** —— 过期是相对于 ``at`` 的判定，由调用方带着它的时刻做。
        一个在库里过滤的实现会让「这条说法在这一刻还算不算数」这个问题的答案取决于
        查询发生在什么时候。"""

    # --- 活动、批次、清单、授权 ---------------------------------------------

    @abstractmethod
    def put_campaign(self, campaign: OutreachCampaign) -> None: ...

    @abstractmethod
    def campaign(self, campaign_id: str) -> OutreachCampaign | None: ...

    @abstractmethod
    def put_draft(self, draft: OutreachDraft) -> None:
        """写入一版草稿。同 ``(draft_id, draft_version)`` 幂等；不同内容报错。"""

    @abstractmethod
    def draft(self, draft_id: str, version: int) -> OutreachDraft | None: ...

    @abstractmethod
    def latest_drafts(self, *, wave_id: str) -> tuple[OutreachDraft, ...]:
        """这一批里每一份草稿的**最新一版**，按 ``draft_id`` 升序。"""

    @abstractmethod
    def put_manifest(self, manifest: OutreachManifest) -> None:
        """写入一份清单。同 ``wave_id`` 且摘要相同 → 幂等；摘要不同 → **报错**。

        不是覆盖：一份被换掉的清单会让绑在旧摘要上的授权全部失去意义，
        而盘上只显示「清单更新了」。要换内容就换一个 wave。
        """

    @abstractmethod
    def manifest(self, wave_id: str) -> OutreachManifest | None: ...

    @abstractmethod
    def put_wave(self, wave: Wave, *, expected_revision: int | None) -> None:
        """写 Wave 的状态。``expected_revision=None`` = **必须不存在**（首次创建）。

        与 ``SceneStore.put_record`` 逐字同一条纪律：用 ``None`` 表示「随便写」
        的实现，会让「这已经是第一次创建了」与「我以为这是第一次创建」变成
        同一件事。
        """

    @abstractmethod
    def wave(self, wave_id: str) -> Wave | None: ...

    @abstractmethod
    def put_authorization(self, authorization: WaveAuthorization) -> None:
        """写入一份授权。同 ``authorization_id`` **不得覆盖** —— 一次撤回是
        一条**新**记录（旧的那条留着），因为「谁在什么时候撤回了什么」
        正是事后要查的。"""

    @abstractmethod
    def authorizations(self, *, wave_id: str) -> tuple[WaveAuthorization, ...]:
        """按 ``authorized_at`` 升序列出这一批的全部授权。"""

    # --- 投递 ---------------------------------------------------------------

    @abstractmethod
    def attempt(self, send_key: str) -> OutboundAttempt | None:
        """按逻辑发送标识读回**唯一**那一条。不存在返回 ``None``（不是抛）。"""

    @abstractmethod
    def begin_attempt(
        self, attempt: OutboundAttempt, *, at: datetime, lease: timedelta | None = None
    ) -> AttemptOutcome:
        """开始一次投递尝试。返回「当前那一行」**以及这次是不是我开的**。

        行为（判据是 :meth:`OutboundAttempt.is_stale_at`，纯函数）：
        * 库里没有这个 ``send_key`` → 插入，``won=True``。
        * 库里那条是 ``FAILED``，或者是一条**占住太久**的 ``NOT_SENT``
          （超过 ``lease``）→ 那条的 ``attempt`` 加一，状态回到 ``NOT_SENT``，
          ``won=True``。
        * 库里有一条已终结的（``DELIVERED`` / ``BOUNCED`` / ``ACCEPTED`` /
          ``DELIVERY_UNKNOWN``），或者一条**还没到** ``lease`` 的
          ``NOT_SENT`` → **返回已有的那条，``won=False``**。
          这就是「不得重复发」的全部内容。

        ## 为什么 ``NOT_SENT`` 也走 ``won=False``

        一条 ``NOT_SENT`` 意味着「有人开出去了、还没结算」。两个驱动同时进来时，
        第二个看到的就是这一种 —— 而**从存储这一层看不出来**第一个是否还在跑。
        所以这里只有两个诚实的答案：把它当成活的（``won=False``，保守，可能
        多等一会儿），或者把它当成死的（``won=True``，于是**有人收到两封**）。
        这两者的代价不对称，所以取前者，并用 ``lease`` 给后者留一条出路 ——
        一台真的挂掉的机器不该把这一封永远钉住。

        ``lease=None`` 用 :data:`~marketops.domain.outreach.DEFAULT_ATTEMPT_LEASE`。
        ``at`` 是**参数**而不是时钟：一个测试要能在可控时钟下把「上一台机器
        在 20 分钟前开出去的」造出来，而这只有在时刻由调用方给的时候才做得到。

        为什么返回 ``(那一行, 是不是我开的)`` 而不是只返回那一行：**同一封信
        从两个驱动同时进来**时，两个调用各自都拿到「当前那一行」，
        而只有一个真的开了它 —— 另一个人若照着返回的那一行去发，
        那封信就发了两遍。判据与 ``MaintenanceStore.claim`` 的 ``won`` 是
        同一条（``ClaimOutcome``）：真正知道答案的只有执行了那次插入的存储。

        ``won`` **不能**由调用方比对 ``attempt`` 推出来：两次投递尝试的
        ``attempt`` 可能**不同**（前一次失败了），于是「我拿到的是第 2 次」
        读不出「第 2 次是不是我开的」。
        """

    @abstractmethod
    def finish_attempt(self, attempt: OutboundAttempt) -> None:
        """把一条尝试置为终态。对已经是终态的同一个 ``send_key`` 再调一次是幂等的。

        ## 一个例外，而且只有一个

        结局是一次性写下的：``FAILED`` 不能被改写成 ``ACCEPTED_BY_PROVIDER``，
        因为那样「这一封到底发出去了没有」就没有答案了。**唯一**的例外是从
        ``DELIVERY_UNKNOWN`` 出发的那一次改判，判据在
        :func:`~marketops.domain.outreach.may_be_settled_by_reconciliation` ——
        它服务的正是「先对账」这条要求：``DELIVERY_UNKNOWN`` 本来就不是一个答案，
        拒绝一切改判会让它既不能重发也不能结案。

        写定之后又是不可改的。实现**必须**用那一个函数来判断，不要在适配器里
        另写一份 —— 两边的判据漂开时，症状是一次「问清楚了」被静默丢弃。
        """

    @abstractmethod
    def attempts(self, *, wave_id: str) -> tuple[OutboundAttempt, ...]:
        """这一批的全部尝试，按 ``(recipient, draft_id)`` 升序。"""

    @abstractmethod
    def attempts_by_recipient(self, recipient: str) -> tuple[OutboundAttempt, ...]:
        """这个收件人**历史上**的全部尝试，跨批次。

        §6 逐字要求每一次投递前重查「跨线程/跨 Scene 的工作区发送历史」。
        一个只查本批次历史的实现，会让同一个人在两个活动里各收到一封 ——
        而两批的记录各自完全正确。
        """

    # --- 抑制 ---------------------------------------------------------------

    @abstractmethod
    def put_suppression(self, suppression: RecipientSuppression) -> None:
        """写入一条抑制。同 ``(recipient, reason)`` 幂等 —— **不覆盖**
        ``released_at``：一条解除之后再收到同一理由的投诉，是**两条**事实。"""

    @abstractmethod
    def suppressions(self, recipient: str) -> tuple[RecipientSuppression, ...]:
        """这个收件人的全部抑制记录，按 ``suppressed_at`` 升序。"""

    @abstractmethod
    def is_suppressed(self, recipient: str, *, at) -> bool:
        """这一刻这个人还禁止联系吗。

        按 ``at`` 判而不是按「有没有记录」判：一条已经被解除的抑制
        （``released_at`` 早于 ``at``）不该继续挡住发送。
        """

    # --- 事件与交接 ---------------------------------------------------------

    @abstractmethod
    def put_event(self, event: CorrespondenceEvent) -> bool:
        """记一条进来的事件。返回**这次是不是新的一条**。

        按 :attr:`CorrespondenceEvent.dedupe_key` 去重（``sendbox`` +
        ``provider_message_id``）。重复时返回 ``False`` 且**不覆盖**已有那条：
        重放的原件与「两条一样的事件」是两件事，而前者不该让后者的记录变样。
        """

    @abstractmethod
    def event(self, event_id: str) -> CorrespondenceEvent | None: ...

    @abstractmethod
    def events(self, *, workspace: str) -> tuple[CorrespondenceEvent, ...]:
        """按 ``(received_at, event_id)`` 升序。**乱序到达是常态**，
        所以顺序按**发生时刻**而不是按落库时刻。"""

    @abstractmethod
    def put_handoff(self, handoff: SalesHandoff) -> None:
        """写入一条交接。同 ``handoff_id`` 幂等；状态变化是**新的一条**记录
        （``handoff_id`` 不变、``state`` 变），所以这里覆盖写。

        与抑制的取舍**相反**，理由是：抑制的解除时刻是「这件事结束了」，
        而交接的状态推进是「这件事正在走」—— 前者要留痕，后者要读当前。
        """

    @abstractmethod
    def handoffs(self, *, workspace: str) -> tuple[SalesHandoff, ...]:
        """按 ``created_at`` 升序。"""



#: 分段拼接的分隔符。与 ``domain/budget.py::SEP``、``ports/decisions.py::_SEP``
#: 同一个理由：三个分段都可能含 ``:`` 或 ``-``，而 ``("a:b", "c")`` 与
#: ``("a", "b:c")`` 拼出来必须不同。US（U+001F）是控制字符，不可能出现在
#: 用户输入里，所以拼接天然无歧义。
_SEP = "\x1f"


@dataclass(frozen=True)
class DraftRequest:
    """写一封信所需的**全部**输入，一次给全。

    ## 为什么是一个对象，而不是一串关键字参数

    因为它有一个摘要。§5 逐字要求「只给新增的或输入变了的对象生成」，而
    「输入变了吗」这个问题的答案必须是**可复现的** —— 一个把输入散成十个关键字
    参数的接口，任何一处改动（多传一个、少传一个、改了顺序）都会让摘要失去
    可比性，而症状是「同一封信被生成了两遍」或者「改过的输入没被重新生成」，
    两者在报告上都只是数字大了一点。

    :meth:`generation_key` 因此是本类的一部分，不是调用方各自算的。

    ## 它**不**含收件人

    收件人在 :class:`~marketops.domain.outreach.ManifestEntry` 上，而这一层不碰
    收件人。§5 逐字：「写作者不得修改收件人、发件人、权限、抑制或发送状态」——
    把地址放进请求里，第一个实现就会「顺手按地址调一下称呼」，于是发出去的东西
    与批准的东西不再是同一份。要个性化到「贵俱乐部」这一级，用的是下面那个
    ``facts``，它指的是**场地与组织**，不是某个人。

    ## ``facts`` 里为什么有第三格

    §5 逐字要求「``facts_used`` 是待核实引用，不是模型自证」，于是
    :class:`DraftProposal.facts_used` 的形状是 ``(事实名, 证据 id)``。请求里
    如果只有 ``(事实名, 值)``，**这个端口就没有实现能满足它自己的契约**：
    写作者面前只剩两条路，而两条都是谎 ——

    * 编一个证据 id，因为除了值它手上没有别的东西。这正是 §5 要禁的那件事。
    * 永远交回空的 ``facts_used``。于是「待核实」这一整条链在下游**永远没有
      东西可核**，而报告上看起来一切正常：每一稿都 ``GENERATED``、都有正文、
      都没有 hold。§5 的这条要求会以「零条待核实引用」的形态静默失效。

    值仍然是值（确定性写作者要拿它去填模板里的占位符）。证据 id 是**同一行的
    第三个格**，不是另起一个平行的列表 —— 两个平行的元组迟早会在某一次过滤
    之后错位一格，而那错位在正文里看不出来，只让引用指错一行。
    """

    account_id: str
    organisation: str
    family: TemplateFamily
    policy: WritingPolicy
    stage: OutreachStage
    locale: str
    #: 可以说的话术，**逐字**。见 :meth:`DraftWriter.draft` 对「不得编造」的说明。
    claims: tuple[ApprovedClaim, ...] = ()
    #: ``(事实名, 值, 证据 id)``。**已经过核实**的那些 —— 调用方负责核实，
    #: 这一层不再验；但这一层**必须**把证据 id 带下来，理由见类 docstring。
    facts: tuple[tuple[str, str, str], ...] = ()

    def __post_init__(self) -> None:
        """``account_id`` 过**共享的那一条**判据，``facts`` 过形状判据。

        与 ``ports/search.py::SearchQuery`` 同一条理由、同一个函数：请求里那个
        id 指的是 ``AccountRecord.account_id`` 那一个值。这里放宽，写作者拿到的
        就是另一个字符串，而它照样能在 :meth:`generation_key` 里算出一个稳定的
        fingerprint —— 于是「同一个账号的两封信」看起来只是两个不同的账号。

        ``facts`` 的形状判据是后加的，因为证据 id 让这三格**变成承重的**：
        :meth:`generation_key` 会把它拆开，一个两格的元素会让那里以一个
        ``ValueError`` 收场，而那个报错会落在**生成**那一步，说得像是写作者
        坏了。同一条判据的两个位置应当由同一个地方报错。
        """
        check_canonical_id(self.account_id, "DraftRequest.account_id")
        for index, fact in enumerate(self.facts):
            if len(fact) != 3:
                raise ValueError(
                    f"DraftRequest.facts[{index}] 有 {len(fact)} 格，应当是 3 格"
                    "（事实名, 值, 证据 id）。"
                )
            name, value, evidence_id = fact
            if not name or not value or not evidence_id:
                raise ValueError(
                    f"DraftRequest.facts[{index}] 三格都不得为空 —— "
                    "「有事实但没有证据支撑」不是一个可以说出口的输入："
                    "它要么不该出现在请求里，要么该由写作者对此交出 held_reason。"
                )

    def generation_key(self, *, writer_id: str, policy_version: str) -> str:
        """这份输入（加写作者与策略版本）的指纹。**同一份输入得到同一个值。**

        三个东西算进去而输入列表里没有，理由是它们各对应一次真实的谎：

        * ``writer_id`` —— 换一个写作者（换模型、换提示版本）会得到不同的正文。
          不算进去，「同一个 key 两次生成得到不同内容」会被读成「写作者不稳定」，
          而事实是「有人换了实现」。
        * ``policy_version`` —— 策略改了而输入没改，正文**应当**重写。
        * ``stage`` 与 ``locale`` 在输入里，但它们各自单独算，因为「第二段的信
          与第一段不同」与「同一个阶段换了语言」是两件事。

        逐字拼接而不是对 ``model_dump`` 求摘要：``facts`` 是元组的元组，顺序由
        调用方给；把它排序再拼，是为了让「同一组事实、换了个顺序」不产生两个
        key —— 那不是一次输入变化，是一次传参顺序的抖动。

        ``facts`` 的**证据 id 也算进去**。摘要是「输入变了吗」的答案，而
        「同一个值换了一条证据支撑」正是一次输入变化：它改的是这一稿**引用了
        什么**，而引用是下游要拿去核的那一格。不算进去的话，重新取证之后旧稿
        会被读成 ``unchanged`` 而留下来，它指向的证据行已经不是这一稿的出处
        —— 而 ``unchanged`` 恰好是那个不会让人再去看一眼的结局。

        ## 三处**版本号**也算进去，而且这一条曾经是缺的

        「策略改了而输入没改，正文应当重写」这句由 ``policy_version`` 承担，而
        ``policy_version`` 是**写作者自报**的：确定性的那一版报空串
        （``DeterministicDraftWriter`` 逐字：本部署今天没有任何一份带版本的写作
        策略）。于是三张配置表在 v4 拿到 ``revision`` 之后，「发布第 2 版」对任何
        **已经起草过**的批次是一次无声的空操作 —— 键没变，起草把它读成
        ``unchanged``，换出去的还是按第 1 版写的那一句。盘上一切正常，而
        第 2 版从来没生效过。

        所以 ``family`` / ``policy`` / ``claim`` 的版本号都进这个键：它们与
        ``evidence_id`` 是同一条理由 —— 版本号是「这一稿引用了哪一份配置」的一部分，
        而配置的第二版**是另一份配置**。
        """
        parts = [
            writer_id,
            policy_version,
            self.account_id,
            #: ``(id, 版)`` 一对一对地进 —— 只算 id 会让「发布新一版」不改变键。
            f"{self.family.family_id}@{self.family.revision}",
            self.family.stage.value,
            f"{self.policy.policy_id}@{self.policy.revision}",
            self.stage.value,
            self.locale,
            self.organisation,
            "|".join(
                sorted(f"{claim.claim_id}@{claim.revision}" for claim in self.claims)
            ),
            "|".join(
                sorted(f"{name}={value}@{evidence_id}" for name, value, evidence_id in self.facts)
            ),
        ]
        return sha256_text(_SEP.join(parts))


@dataclass(frozen=True)
class DraftProposal:
    """写作者交回来的一稿。**它还不是一份草稿。**

    ## 为什么 ``held_reason`` 与正文并存，而不是互斥

    互斥的形状（要么正文要么理由）在下面的情形上没有答案：写作者写出了正文，
    同时知道它的 ``facts_used`` 里有一条**没有证据支撑**。§5 逐字要求
    「个性化缺失时用已批准的一般措辞或 hold，**不得猜**」—— 于是「写出来了但
    不能用」是一个真实且常见的结局，而它必须能同时说出「我写了什么」与
    「为什么不能用」。

    所以 :attr:`body` 为空 ⟺ 写作者什么都没写出来（那是 ``FAILED`` 的近亲），
    而 :attr:`held_reason` 非空 ⟺ **这一稿不得发出**。两者可以同时为真，
    也可以同时为假（这是正常的一稿）。

    ## ``facts_used`` 是**待核实的引用**

    §5 逐字：「``facts_used`` 是待核实引用，不是模型自证」。所以这里的形状是
    ``(事实名, 证据 id)`` —— 指向证据行，而不是一句「我用了这些事实」。
    写作者**不得**把它写成散文，因为下游要拿它去 ``EvidenceStore`` 查，
    查不到就是一次 ``unverified``。
    """

    subject: str = ""
    body: str = ""
    #: ``(事实名, 证据 id)``。见类 docstring。
    facts_used: tuple[tuple[str, str], ...] = ()
    #: 用到的已批准说法的 id。**必须**是请求里 ``claims`` 的一个子集 ——
    #: 由调用方校验，因为这一层不认识规范库。
    claim_ids: tuple[str, ...] = ()
    #: 附件的 ``(文件名, 内容摘要)``。
    attachments: tuple[tuple[str, str], ...] = ()
    #: 非空 ⟺ 这一稿**不得发出**，并说明为什么。见类 docstring。
    held_reason: str = ""
    #: 写作者自报的标识（模型名 + 版本，或确定性实现的名字）。
    writer_id: str = ""


class DraftWriter(ABC):
    """把一份 :class:`DraftRequest` 变成一稿**待审的**正文。

    ## 它为什么不能是一个函数

    因为「写」与「审」是两件事，而它们必须能被分别替换。本仓的第一版实现是
    确定性的（按模板族与策略拼装，不需要任何模型），而第二版会是一个模型
    后端 —— §5 逐字要求「不预生成几百份静态模板」，所以确定性实现**不是**
    最终形态，它只是把「用途、输入、输出回执、校验」这几条边界先立起来，
    让模型那一版接进来时不需要改这些边界。

    ## 三条写作者**不得**做的事，逐条对应一次真实事故

    1. **不得编造**痛点、设备、预算、责任人姓名、购买意向或产品能力（§5 逐字）。
       没有证据的个性化必须走 ``policy.generic_wording_id`` 那一档，或者
       ``held_reason``。判据不在这个端口里 —— 端口管不了「有没有证据」，
       它管的是「写作者说得出它用了哪一条证据」（``facts_used``）。
    2. **不得改写收件人、发件人、权限、抑制或发送状态**（§5 逐字）。落在这里是
       :class:`DraftRequest` **没有**这些字段 —— 不是「有但不许改」。
    3. **不得返回一个没有正文也没有理由的结果**。见 :class:`DraftProposal`。

    ## 抛出与 ``held_reason`` 是两件事

    抛异常 = 「这次生成**没跑成**」（后端不可达、超时）。``held_reason`` =
    「跑成了，但这一稿不能用」。前者要被重试并被记成一次失败，后者是一条
    **结论**。合成一个之后，「模型服务挂了」与「缺一条个性化事实」在队列里
    长得一样，而处置是反的。
    """

    @property
    @abstractmethod
    def writer_id(self) -> str:
        """这个写作者**自己**是谁（实现名 + 版本）。

        它存在是为了让「这一稿是谁写的」不依赖写作者在正文里自报 ——
        一个自报的标识可以被复制粘贴到另一个实现里，而这个属性不行。
        它与 :attr:`DraftRequest.generation_key` 算在一起。
        """

    @property
    @abstractmethod
    def policy_version(self) -> str:
        """这个写作者按的**策略版本**。

        与 :attr:`writer_id` 分开：同一份策略、换一个写作者，与同一个写作者、
        换一份策略，是两次不同的改动，而它们都会改变正文。
        """

    @abstractmethod
    def draft(self, request: DraftRequest) -> DraftProposal:
        """写一稿。**它不写库、不发信、不改状态** —— 那都是调用方的事。"""


__all__ = [
    "AttemptOutcome",
    "DraftProposal",
    "DraftRequest",
    "DraftWriter",
    "MailTransport",
    "OutreachStore",
    "SendReceipt",
]

"""``CorrespondenceSource`` —— **进来的事件**（回信、退信、退订、投诉）从哪里读。

## 这个端口只有一族动词：读一批

§7 要求回信与退信**两条路都接得住**（webhook 推过来的、我们定时去取的）。把这两
件事写进契约的方式有两种，而其中一种是错的：给端口两个方法，一个叫 ``poll``、
一个叫 ``on_webhook``。那样做的代价不是多了一个方法，而是**每一个实现都被迫假装
自己是其中一个** —— 一个 webhook 实现要写一个永不返回空批次的 ``poll``，而读的人
从 ``isinstance`` 与 ABC 上都看不出这件事。

所以这里只有一个 :meth:`CorrespondenceSource.read`：读一批，从某个游标开始，
返回它读到了什么。**「谁在什么时候调它」不属于契约** —— 那是驱动侧的事，而驱动
侧今天还没有（``application/correspondence.py`` 只被调用一次，调用者是谁由接线
决定）。一个把调度写进契约的端口，会让「五分钟 tick 一次」变成一个无法替换的事实。

## 没有「探测地址」这类动词

本仓不发明供应商的事实。这个端口不认识任何一个具体的信箱服务，也不回答「这个
地址存不存在」—— 地址是否存在**只有对面知道**，而我们的答案来自对方发回来的
退信（:class:`~marketops.domain.outreach.CorrespondenceKind.HARD_BOUNCE`），
不是来自我们偷偷去敲一下。

## 每一条必须带上原件

:attr:`CorrespondenceItem.raw_ref` 与 :attr:`CorrespondenceItem.raw_sha256` 是
**必填**的。§7 逐字：「分类失败不得丢失原件」—— 一条只有解析结果的条目，在一次
分类失败之后没有东西可重试；而一条只有摘要的条目挡不住原件被改写。两个都要。

## 游标必须**能够作废**

:class:`CursorState` 是一个枚举，不是 ``bool``。§7 逐字要求「提供方作废了游标」
这件事能被表达出来，而它**不是**「这次什么都没读到」：后者是空批次（``items``
为空、游标照旧），前者是「你手上那个游标从某一刻起不再指向任何东西，先对齐再
重读」。一个 ``ok: bool`` 会把这两件事压成同一个取值，于是调用方只能猜 —— 而
猜错的方向是**丢掉一段来信**。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..domain.outreach import CorrespondenceEvent, CorrespondenceKind
from ..domain.util import normalise_email


class CursorState(str, Enum):
    """这次的游标怎么了。**两个取值都不是「失败」**，这是一个结果类型。"""

    #: 读到了一批，``CorrespondenceBatch.cursor`` 是下一次的起点。
    ADVANCED = "advanced"
    #: 提供方说这个游标作废了（它太旧、它指向的那一段被清了、或者是换了一个
    #: 数据集）。处置是**重新对齐**，不是重试同一个游标 —— 重试一个作废的游标
    #: 会拿到一个空批次，而空批次读起来与「这一段没有来信」逐字相同。
    INVALIDATED = "invalidated"


@dataclass(frozen=True)
class CorrespondenceItem:
    """**一条**原始事件，逐字来自对面。

    ## 这里没有一个字段叫「处置」

    处置是我们这边的决定（:class:`~marketops.domain.outreach.CorrespondenceDisposition`），
    它由 ``application/correspondence.py`` 判定。端口里出现它，就等于让提供方
    替我们决定「这封信算不算投诉」。

    ## ``kind`` 允许是 ``None``，而那是常态

    有的来源**知道**这是什么：一封退信是收信服务器按 RFC 回的，它的分类是确定
    的。有的来源**不知道**：一个 webhook 递过来一个 JSON，里面是一段正文。
    ``None`` 表达的就是后者 —— 「不知道」绝不能被迫编造。一个要求 ``kind`` 必填的
    契约，会逼着 webhook 实现把每一封信都标成 ``HUMAN_REPLY``，而那个默认值
    恰好是最贵的那个（它会产出销售交接）。
    """

    #: 提供方侧的**唯一**标识。与 :attr:`sendbox` 合起来是去重键。
    provider_message_id: str
    #: 这条事件是从哪个信箱读到的（规范化地址）。
    sendbox: str
    #: 原件在哪。**必填** —— 见模块 docstring。
    raw_ref: str
    #: 原件的摘要（64 位小写十六进制）。**必填**。
    raw_sha256: str
    #: 事件**发生**的瞬间，不是我们读到它的瞬间。乱序到达是常态。
    received_at: datetime
    #: 对方的地址（规范化）。空 = 提供方没给。
    sender: str = ""
    subject: str = ""
    #: 正文。**是数据，永远不是指令**（§7 逐字）。这一点落在类型上：它只是
    #: ``str``，而消费它的唯一路径是一张把 ``kind`` 映射到处置的表。
    body: str = ""
    #: 这条事件关联到我们发出去的哪一次尝试（``OutboundAttempt.send_key``）。
    #: 空串 = **关联不上** —— 而那是必须存下来的事实，不是一次错误。
    send_key: str = ""
    #: 对方那封信**引用的**那一封（``In-Reply-To`` / ``References`` 里的
    #: Message-ID，**原样**，尖括号也留着）。空串 = 对方没引用任何一封。
    #:
    #: ## 它不是 ``send_key``，也不是 ``send_key`` 的替代
    #:
    #: ``send_key`` 是我们的**去重键**，对方永远看不到它；而这一格是对方**回显**
    #: 回来的东西 —— 我们写进 ``Message-ID`` 的那个字符串。两者由不同的系统保证
    #: （见 ``OutboundAttempt`` 的类 docstring），所以这里不合并它们，也不在这里
    #: 把它翻译成 ``send_key``：翻译需要一次存储查询，而这一层没有存储。
    #:
    #: 它存在的理由是**一次真实的相关**：一封回信如果既没带 ``send_key``、又
    #: 没带这一格，那它就只能靠发件人地址去猜 —— 而两个人共用一个信箱时，
    #: 猜出来的那一次发送是错的，且错得看不出来。
    in_reply_to: str = ""
    #: 一份投递状态通知（DSN）里**被退的那个地址** —— RFC 3464 的
    #: ``Final-Recipient``。空串 = 这条事件不是一个说得清收件人的 DSN。
    #:
    #: ## 它为什么不挤进 ``sender``
    #:
    #: 一封 DSN 的发件人是**对方那台收信服务器**（``MAILER-DAEMON@…``），不是被退
    #: 的收件人。而这条事件在业务上「是关于谁的」，答案恰恰是被退的那个地址。把
    #: 两件事塞进一格，症状是结构性的：关联的那两条路一条要 ``send_key``（DSN 里
    #: 没有，它是我们的去重键、从不出现在信里）、另一条要 ``In-Reply-To``（DSN 也
    #: 没有，那是**人**回信时才有的头字段），而退到发件人地址去找那一次发送，查的
    #: 是一台邮件守护进程的投递历史 —— 零条。于是每一封硬退信都关联不上，落进人工
    #: 审核，而 §7 要的「硬退信 → 按 SOP 抑制并保留证据」一条都不产生。
    #:
    #: 它**不替代** ``sender``：那一格说的是「谁发来的」，这一格说的是「这封信说的
    #: 是谁」。两者都要用 —— 后者是关联与抑制的主语，前者是「对方是谁」的唯一答案。
    bounce_recipient: str = ""
    #: 这份 DSN 说的是**哪一封**：``Original-Message-ID``（RFC 3464），也就是我们
    #: 发出去时写进 ``Message-ID`` 的那一个字符串。空串 = 来源没给这一格。
    #:
    #: 与 ``in_reply_to`` 分开，尽管两者都指向「我们发出去的那一封」：那一格的头
    #: 字段是 ``In-Reply-To`` / ``References``（**人**回信时引用上一封的方式），这一格
    #: 是**收信服务器**在报告它投递失败的那一封。来源不同、保证也不同，合并会让
    #: 「对方引用了一封」与「服务器在报告一封」在库里长得一模一样。
    bounce_original_message_id: str = ""
    #: 来源**自己**给的分类。``None`` = 来源没给，要我们自己分。
    kind: CorrespondenceKind | None = None

    def __post_init__(self) -> None:
        for field_name in ("provider_message_id", "raw_ref"):
            if not getattr(self, field_name).strip():
                raise ValueError(
                    f"{field_name} 不得为空。\n"
                    "一条没有提供方消息 id 的条目无法去重（主题是对方可以随手改的"
                    "东西），一条没有原件引用的条目在一次分类失败之后没有东西可重试。"
                )
        if normalise_email(self.sendbox) != self.sendbox:
            raise ValueError(
                f"sendbox 必须是规范化地址：{self.sendbox!r}。\n"
                "去重键是 (sendbox, provider_message_id)，而两个写法不同的同一个"
                "信箱会让同一封信去重不掉 —— 症状是那个人收到两次回复。"
            )
        if self.bounce_recipient and normalise_email(self.bounce_recipient) != self.bounce_recipient:
            raise ValueError(
                f"bounce_recipient 必须是规范化地址：{self.bounce_recipient!r}。\n"
                "它会被拿去与 OutboundAttempt.recipient 比对，而那一格是**规范化过的**"
                "（domain 的校验器保证）：大小写或显示名不同的一次比对会得到「不是同一个"
                "人」这个假答案 —— 而那个答案的症状恰好是「这封退信关联不上」，"
                "也就是这一格存在要修的那件事。"
            )
        _check_digest(self.raw_sha256, "raw_sha256")
        if self.received_at.tzinfo is None:
            raise ValueError("received_at 必须带时区 —— 见 util.to_utc 的说明")
        if self.kind is not None and not isinstance(self.kind, CorrespondenceKind):
            raise ValueError(
                f"kind 要么是 None（来源不知道），要么是 CorrespondenceKind 的一个"
                f"取值，收到的是 {self.kind!r}。\n"
                "一个字符串形式的分类会让下游的映射表查不到它 —— 而查不到与"
                "「映射到默认值」在报告上长得一样。"
            )

@dataclass(frozen=True)
class CorrespondenceBatch:
    """**一次读取的结论**：读到了哪些条目 + 游标推到了哪。

    形状与 ``ports/outreach.py`` 的 ``SendReceipt`` 是同一条理由：它是**一次调用
    的结果**，不是一个持久的领域对象。所以它是一个 ``frozen`` dataclass，
    而不是 ``domain/`` 里的一个 ``StrictModel``。
    """

    items: tuple[CorrespondenceItem, ...]
    #: 下一次从这里开始读。``CursorState.INVALIDATED`` 时它必须是空串。
    cursor: str
    cursor_state: CursorState = CursorState.ADVANCED
    #: **这一次是从哪个游标读出来的。** 有它，调用方才答得出「这一批没落库时，
    #: 游标应该退回到哪里」—— 没有它，一次失败的批次只能靠调用方自己记住。
    from_cursor: str = ""
    #: 提供方说的话（作废的原因、对齐建议）。作废时必填。
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.cursor_state, CursorState):
            raise ValueError(f"cursor_state 必须是 CursorState：{self.cursor_state!r}")
        if self.cursor_state is CursorState.INVALIDATED:
            if self.cursor:
                raise ValueError(
                    "游标作废时不得给出新游标。\n"
                    "「这个游标不算数了」与「这是下一个游标」同时为真时，调用方"
                    "无论选哪个都有一半是错的 —— 而错的那一半是接着用一个作废的游标。"
                )
            if not self.note.strip():
                raise ValueError(
                    "游标作废必须说明为什么（note）。\n"
                    "一条只报「作废了」的回执，读的人无从判断是对齐一次就好，"
                    "还是这个来源从此不可信。"
                )
            if self.items:
                raise ValueError(
                    "游标作废时不得同时给条目。\n"
                    "「这个游标不算数」意味着这一次没有读到东西；一个既作废又带"
                    "条目的批次，调用方无法处置 —— 那些条目算不算数？"
                )
        elif self.items:
            for item in self.items:
                if not isinstance(item, CorrespondenceItem):
                    raise ValueError(f"items 里混进了非条目：{item!r}")


class CorrespondenceSource(ABC):
    """原始事件的来源。**一个动词，见模块 docstring。**

    实现要回答的问题只有一个：从 ``cursor`` 开始读一批，读到了什么、下一个游标
    是什么。它**不**回答「什么时候读」、「读到的东西是什么意思」、「该不该
    抑制这个人」—— 三件事都在别处。
    """

    @abstractmethod
    def read(self, *, cursor: str = "") -> CorrespondenceBatch:
        """从 ``cursor`` 开始读一批。

        Args:
            cursor: 上一次的 :attr:`CorrespondenceBatch.cursor`。空串 = 从头读。

        Returns:
            这一批的结论。**读不到东西时返回一个空批次**（``items`` 为空），
            而不是抛异常 —— 「这一段没有来信」是一个正常的结论。

        Raises:
            Exception: 真的读不成的时候（远端连不上、凭据被拒）。**这与空批次
                是不同的事**，所以它抛出来而不是返回一个空批次：一个把连不上
                当成「没有来信」的实现在报告上完全正常，而它的病是**漏收**。
        """
        raise NotImplementedError


@dataclass(frozen=True)
class Classification:
    """分类器给出的**一个**结论。它有三种落法，而第三种是这里的关键。

    * 说得出 → :attr:`kind` 是一个 :class:`~marketops.domain.outreach.CorrespondenceKind`；
    * **说不出 → 弃权**：:attr:`kind` 是 ``None``，:attr:`reason` 说明为什么。

    弃权**不是** ``HUMAN_REPLY``（会把一个看不懂的回信推去产出销售交接），
    也**不是** ``RECORD_ONLY``（会把一封「请不要再联系我」记一笔就放下）。
    它落到 :attr:`~marketops.domain.outreach.CorrespondenceDisposition.MANUAL_REVIEW`。

    做成一个 dataclass 而不是让 ``classify`` 直接返回 ``CorrespondenceKind | None``：
    两者的区别是**理由**。``None`` 只说「不知道」，而一条进人工审核的记录必须
    说得出为什么进了人工审核 —— 否则读的人分不清「模型拿不准」与「模型根本没跑」。
    """

    kind: CorrespondenceKind | None = None
    #: 说得出时是判断依据，说不出时是弃权的理由。
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind is None and not self.reason.strip():
            raise ValueError(
                "弃权必须说明为什么。\n"
                "一条不写理由的弃权，与一次分类器崩溃在记录上长得一样 —— 而"
                "前者的处置是「换个模型再看看」，后者是「先修好它」。"
            )
        if self.kind is not None and not isinstance(self.kind, CorrespondenceKind):
            raise ValueError(f"kind 必须是 CorrespondenceKind 的一个取值：{self.kind!r}")

    @property
    def abstained(self) -> bool:
        """说不出。**它有一个名字**，因为「含糊」这件事必须能被说出来。"""
        return self.kind is None


class ReplyClassifier(ABC):
    """把一条进来的事件分成七类中的一类，**或者明说分不出来**。

    它是本仓第一个**决策 provider** 形状的端口（``domain/`` 不能认识任何一个
    具体的模型，而这一层不能认识任何一家 API）—— 所以它只认
    :class:`Classification`，不认任何 SDK 的返回类型。
    """

    @abstractmethod
    def classify(self, *, event: CorrespondenceEvent) -> Classification:
        """给一条事件分类。

        Args:
            event: **已经压制过的**一条事件（``application/correspondence.py``
                在调用分类器之前就已经判定过抑制）。它带着 ``raw_ref`` /
                ``raw_sha256`` / ``body``，所以一个模型读的就是原件那一份内容。

        Returns:
            说得出就给一个 ``kind``，说不出就弃权。**不得猜** —— 一个「拿不准
            就猜 HUMAN_REPLY」的分类器，会把每一次模型抖动变成一条销售交接。

        Raises:
            Exception: 模型抛异常、远端超时、返回了读不懂的东西。调用方
                （``application/correspondence.py``）**必须**把这一条与「弃权」
                分开处置：分类失败时原件照样落库、处置进人工审核，理由写的是
                「分类失败」而不是「内容不明确」。
        """
        raise NotImplementedError


def _check_digest(value: str, field_name: str) -> None:
    """64 位小写十六进制。**形状错的值与没有值是同一件事。**

    与 ``domain/outreach.py`` 的 ``_as_sha256`` 是同一条判据、同一句理由：它在
    报告里会看起来像有一个答案（「是不是同一份原件」），而实际上答案的形状不对。
    这里再写一遍不是重复实现，而是因为**这一层的输入是外面读进来的**：一个坏
    摘要若能穿过端口，它会在很远的地方以「同一封信判成了两封」的样子暴露出来。
    """
    digest = value.strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError(f"{field_name} 不是 64 位十六进制摘要：{value!r}")


__all__ = [
    "Classification",
    "CorrespondenceBatch",
    "CorrespondenceItem",
    "CorrespondenceSource",
    "CursorState",
    "ReplyClassifier",
]

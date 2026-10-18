"""``RuleBasedReplyClassifier`` —— 一个**不猜**的分类器：命中规则才说话，否则弃权。

## 它为什么是一个规则表，而不是一个模型

``ReplyClassifier`` 是一个**决策 provider** 形状的端口，本仓最终会有一个模型实现
（那份实现要一把凭据、一次网络调用、一份写下来的题面）。本模块不是那份实现的
替身，它是**另一条路线**：它只回答那些**用一张词表就能回答**的问题，而且回答得
完全确定（同一份正文永远得到同一个结论，不联网、不读时钟、不读环境变量）。

两者的分工是清楚的：模型能读「内容相关」，规则表读不出「相关」——那一格必须由
更强的判据来认。所以下面这条纪律不是谦虚，是**本模块存在的理由**：

    **规则表认不出来的东西，一律弃权，绝不猜 ``HUMAN_REPLY``。**

§7 逐字：「模糊回信 → manual_review_hold」。一个「拿不准就当人类回信」的分类器
不是保守，它是最贵的那一个 —— ``HUMAN_REPLY`` 的处置是 ``HANDOFF``（产出一条
销售交接），于是每一次模型的抖动、每一封看不懂的信，都会变成一个销售要去处理的
线索。弃权（:attr:`Classification.kind` 为 ``None``）落到 ``MANUAL_REVIEW``，
而人读一封原件花的时间，比销售跑去跟进一封「谢谢，不用了」便宜得多。

## 四条文字规则，次序由 ``domain/`` 定，不由本文件定

规则本身是一张**冻结的词表**（:data:`TEXT_RULES`），但**谁先被问**不在这里写：
次序由 :func:`_priority` 从 ``domain/outreach.py`` 的两个判据**算出来**——
:data:`~marketops.domain.outreach.SUPPRESSING_KINDS`（要不要长期抑制）与
:func:`~marketops.domain.outreach.stops_further_outreach`（要不要停跟进）。
于是：

    抑制类（退订 / 投诉）  →  停跟进类（明确拒绝）  →  只记录类（自动回复）

这不是排版。一条同时读到「unsubscribe」与「not interested」的正文，结论只取决于
这条次序，而次序如果在本文件里手写一遍，它就会与 ``domain/`` 那张表**各自演化**——
同一个仓库里出现两个「哪个更严重」的答案，而两边都读得通。算出来的次序没有这个
问题：``KIND_DISPOSITION`` 里某个分类的处置哪天变了，它的提级跟着变。

## 拒绝 vs 退订：边界按 ``domain/`` 的字面量取，不在这里另立一张表

「请不要再联系我」与「请把我从名单里去掉」在现实里是同一个人说的同一件事，
在本仓却是两个不同的处置：

* ``REFUSAL`` → ``STOP_FOLLOWUP``：**停相关跟进**，不写持久抑制行。
  ``domain/outreach.py`` 逐字：「拒绝是关于这次接洽的」。
* ``UNSUBSCRIBE`` → ``SUPPRESS``：**立刻进持久抑制名单**，那是一个跨 Campaign
  长期有效的事实（``RecipientSuppression`` 逐字：「跨 Campaign 长期有效」）。

边界因此落在**有没有点到「名单 / 订阅」这个对象**上：``UNSUBSCRIBE`` 的词表要求
正文里出现退订/移出名单这个动作本身（``unsubscribe`` / ``opt out`` / 「退订」/
「退出名单」），而 ``REFUSAL`` 收的是「不要再发给我」这类**针对联系本身**的话。
「不要再给我发信」落在 ``REFUSAL`` 一侧 —— 它说的是不要再发，与 ``domain/`` 给
``REFUSAL`` 举的例子（「不要联系我」「不需要」）是同一句话的形状，而它没有点名单。

两个方向的代价不对称，这也是为什么必须挑一边并写下来：

* 把一次**退订**读成拒绝 —— 少一条抑制行。后果被
  :func:`~marketops.domain.outreach.stops_further_outreach` 兜住（``REFUSAL``
  也停跟进），所以那个人**仍然**不会再收到自动外联；差的是那条可查询、可审计、
  跨活动长期有效的**记录**。
* 把一次**拒绝**读成退订 —— 多一条持久抑制。代价是这个人**再也不会被联系到**，
  而记录上一切正常：一条他自己都没要求过的永久事实。

所以含糊时选 ``REFUSAL``。**本模块不另立第二张表**：「谁抑制」这个问题的答案只有
一处，就是 ``SUPPRESSING_KINDS``，而 :func:`_priority` 是它的消费者。

## 结构信号优先：``event.kind`` 给了就用 —— 但 ``HUMAN_REPLY`` 例外

来源自己声明的分类是**事实**，不是一次推断（``application/correspondence.py`` 的
``_kind_of`` 让来源压过分类器：一封退信是收信服务器按 RFC 回的，让模型去「再确认
一下」只是给了它一次推翻确定事实的机会）。所以 ``event.kind`` 非空时直接用。

**唯一例外是 ``HUMAN_REPLY``，而且它有两个各自独立的理由**：

1. ``CorrespondenceItem.kind`` 的 docstring 逐字写着：一个要求 ``kind`` 必填的
   契约，会逼着 webhook 实现把每一封信都标成 ``HUMAN_REPLY``，「而那个默认值恰好
   是最贵的那个」。也就是说这个取值是**唯一一个会被实现当作缺省值写下来的**——
   它带着「我不知道」的样子，而不是「我知道」的样子。
2. ``_kind_of`` 给分类器的那个合成事件，``kind`` 填的**就是** ``HUMAN_REPLY``
   （占位符，见 ``application/correspondence.py``）。把它当信号读，等于让本分类器
   在任何一封没命中规则的信上返回 ``HUMAN_REPLY`` —— 也就是把弃权偷偷换成一个销售
   交接，恰好是本节第一段要挡住的那件事。

于是：``kind=AUTO_REPLY`` 之类的自报照收，``kind=HUMAN_REPLY`` 读成「来源没给
结构性信号」，继续往下读正文。

## 退信不在这里判

``HARD_BOUNCE`` / ``SOFT_BOUNCE`` 只在**来源**判，判据是 RFC 3464 的结构
（``multipart/report; report-type=delivery-status`` 与 ``Status:`` 字段），见
``adapters/mail_capture_source.py::_structural_kind``。本模块**没有**任何一条
退信词表，这是刻意的：一段正文里写着「你的信无法投递」的文本不是一条 DSN ——
它可能是某个真人转给你看的一封旧退信，也可能是**对方**在说「我的邮箱满了别再发」。
把一段读起来像退信的正文读成一条退信，后果是那个人被写进持久抑制名单，而
「地址不存在」这个结论我们其实一次都没有得到过。

## 字面上的替代联系人**不在这里**判

§7 逐字：「字面上的替代联系人 → awaiting_Member_A_confirmation，不自动重发或新建
外联」。那条判据住在 ``application/correspondence.py::_alternate_contact``，由
``_decide`` 的最后一步执行，判据是「正文里有一个字面线索短语**并且**恰好有一个与
发件人不同的地址」两件事同时成立。本模块不实现它，也不替它做任何一半：一封
「请把我的同事 X 的电话给我」的回信在这里应当**弃权**（它是一条内容相关的真人
回信，规则表认不出「相关」），然后由 ``_decide`` 那一步把它降到人工审核。
在这里加一条「提到了别人」的规则，会让这个判断变成两处，而这两处会给出不同的
「换不换人」。

## 它不碰的东西

不读 ``raw_ref``（读文件是来源层的事：一个在读文件时抛的分类器，会让「分类失败」
与「原件读不到」在记录上混成一件事）、不发网络请求、不 import 任何厂商、不读时钟、
不读环境变量。所以同一份事件永远得到同一个 ``Classification`` —— 这正是本实现比
模型实现**多**出来的那一条性质，也是它值得存在的原因。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.outreach import (
    SUPPRESSING_KINDS,
    CorrespondenceEvent,
    CorrespondenceKind,
    stops_further_outreach,
)
from ..ports.correspondence import Classification, ReplyClassifier


@dataclass(frozen=True)
class TextRule:
    """一条文字规则：**一个分类**、一组词、以及它守的是哪一句。

    ``cues`` 必须是**小写**的：比对发生在 ``str.casefold()`` 之后
    （:meth:`RuleBasedReplyClassifier.classify`），而一条含大写的词在这里
    永远不会命中 —— 它不会红，只会静默失效。

    ``guards`` 是这一条规则**执行的那句要求**，逐字抄来。它不是注释：一条说不出
    自己守哪句要求的规则，在一次误判之后无从判断该改的是词表还是那句话本身。
    """

    kind: CorrespondenceKind
    cues: tuple[str, ...]
    guards: str
    label: str


#: **唯一的文字规则表。** 冻结、模块级、可遍历 —— 与 ``KIND_DISPOSITION`` 同一个
#: 形状：一个可以被人一眼看全、也可以被遍历的清单，比散在 ``if`` 里的词更容易被
#: 检查（一个漏掉的词在散写的分支里看不出来，在一张表里看得出来）。
#:
#: 词表里**没有**任何一个行业名词，全是**语言**层面的模式（``unsubscribe``、
#: 「退订」）—— 行业知识属于 profile，不属于判据，这条由
#: ``tests/test_no_hardcoded_market_knowledge.py`` 守着。
TEXT_RULES: tuple[TextRule, ...] = (
    TextRule(
        kind=CorrespondenceKind.UNSUBSCRIBE,
        label="要求退出名单",
        cues=(
            "unsubscribe",
            "un-subscribe",
            "opt out",
            "opt-out",
            "remove me from",
            "take me off",
            "delete me from",
            "退订",
            "取消订阅",
            "退出名单",
            "移出名单",
            "从名单里删",
        ),
        guards=(
            "§7 逐字：「退订/投诉 → 立即持久抑制」。词表要求正文点到**名单 / 订阅**"
            "这个对象；没有点到的（「不要再给我发信」）走 REFUSAL —— 边界与代价见"
            "本模块 docstring。"
        ),
    ),
    TextRule(
        kind=CorrespondenceKind.COMPLAINT,
        label="投诉",
        cues=(
            "complaint",
            "complain",
            "this is spam",
            "report you",
            "report this",
            "unsolicited",
            "harassment",
            "harassing",
            "cease and desist",
            "legal action",
            "投诉",
            "举报",
            "骚扰",
            "垃圾邮件",
        ),
        guards=(
            "§7 逐字：「退订/投诉 → 立即持久抑制」。投诉与退订同为抑制类，"
            "两者都被问到才轮得到拒绝类 —— 次序由 _priority 从 domain 算出来。"
        ),
    ),
    TextRule(
        kind=CorrespondenceKind.REFUSAL,
        label="明确拒绝",
        cues=(
            "do not contact",
            "don't contact",
            "do not email",
            "don't email",
            "do not send",
            "don't send",
            "stop contacting",
            "stop emailing",
            "stop sending",
            "no further contact",
            "never contact",
            "not interested",
            "no thanks",
            "no thank you",
            "不要再给我发",
            "不要再发",
            "不要再联系",
            "不要联系我",
            "不要发邮件",
            "请勿再联系",
            "不感兴趣",
            "不需要",
        ),
        guards=(
            "§7 逐字：「明确拒绝 → 停止跟进」。它的处置 ``STOP_FOLLOWUP`` **不写**"
            "抑制行（见 ``SUPPRESSING_KINDS``）—— 拒绝是关于这次接洽的，抑制是"
            "跨 Campaign 长期有效的，两张表合成一个会让这个人永远收不到信。"
        ),
    ),
    TextRule(
        kind=CorrespondenceKind.AUTO_REPLY,
        label="自动回复 / 不在办公室",
        cues=(
            "out of office",
            "out-of-office",
            "away from the office",
            "currently away",
            "automatic reply",
            "auto-reply",
            "autoreply",
            "automated response",
            "automatic response",
            "automatically generated",
            "auto generated",
            "this is an automated",
            "自动回复",
            "自动答复",
            "不在办公室",
            "休假中",
            "出差中",
        ),
        guards=(
            "§7 逐字：``AUTO_REPLY``「不是一个有效的人类回复，因此既不停跟进也不"
            "产生交接」（见 ``domain/outreach.py`` 的 ``CorrespondenceKind``）。"
            "**它是这里唯一一个不会停止跟进的分类**，所以它排在最后：一条同时像"
            "自动回复又像拒绝的正文，按拒绝处置。这条规则的已知代价写在"
            ":meth:`RuleBasedReplyClassifier.classify` 的 docstring 里。"
        ),
    ),
)


def _priority(kind: CorrespondenceKind) -> int:
    """这个分类的**提级**：越小越先被问。**由 ``domain/`` 的两个判据算出来。**

    抑制（0）> 停跟进（1）> 只记录（2）。三个数一个都不在本文件里定义：
    第一个来自 :data:`~marketops.domain.outreach.SUPPRESSING_KINDS`，后两个来自
    :func:`~marketops.domain.outreach.stops_further_outreach`（它只把
    ``RECORD_ONLY`` 判成不停）。

    写死一张 ``{UNSUBSCRIBE: 0, REFUSAL: 1, ...}`` 的表同样能让今天的测试全绿，
    而它与 ``KIND_DISPOSITION`` 是两个各自演化的答案：某天有人把一个分类的处置
    改成 ``SUPPRESS``，抑制行会多出来，而这里的提级不会跟着动 ——
    于是一封同时读到两种信号的正文会按**旧的**严重程度判，且没有任何一处会红。
    """
    if kind in SUPPRESSING_KINDS:
        return 0
    if stops_further_outreach(kind):
        return 1
    return 2


#: 规则按提级排好。派生自 :data:`TEXT_RULES` 与 :func:`_priority`，不是第二张表。
#: ``sorted`` 是稳定的，所以同一提级内仍按 :data:`TEXT_RULES` 的书写次序 ——
#: 那一层的次序（退订在投诉之前）不改变任何结论，因为两者同属抑制类。
_ORDERED_RULES: tuple[TextRule, ...] = tuple(
    sorted(TEXT_RULES, key=lambda rule: _priority(rule.kind))
)


class RuleBasedReplyClassifier(ReplyClassifier):
    """词表分类器。**命中才说话，否则弃权。** 见模块 docstring。

    它没有构造参数 —— 没有阈值、没有开关、没有词表的注入点。理由不是「懒得写」：
    一个可以注入词表的分类器，会让「这份部署用的是哪张词表」变成记录里查不到的
    一件事，而**分类的依据**恰恰是审查时要看的东西。要换词表就改这张表，
    它进版本库、进 code review。

    无状态、无 I/O、无时钟。同一个 ``event`` 永远得到同一个 ``Classification``。
    """

    def classify(self, *, event: CorrespondenceEvent) -> Classification:
        """给一条事件分类。三种落法见模块 docstring。

        判据的**次序**：结构自报 → 空正文 → 提级从高到低的文字规则 → 弃权。

        ## 已知的误判方向（写在这里，因为它是一个真的漏）

        文字规则表读不出**语气**，所以 ``AUTO_REPLY`` 那一条会误伤：一封真人写的
        回信里如果出现「我上周不在办公室」这类句子，会被读成自动回复 —— 而
        ``AUTO_REPLY`` 是唯一**不**停止跟进的分类（处置 ``RECORD_ONLY``），
        于是那个人还会收到下一封。反方向（真自动回复被读成人写的）不会发生，
        因为自动回复一旦带 RFC 3834 头就由来源判掉了，而来源的结构判据永远比
        这里的词表强。

        这条规则的词表因此只收**自报家门**的措辞（``automatic reply``、
        「自动回复」）与**当前不在**的状态（``out of office``、「不在办公室」），
        不收「下周休假」这类关于未来的说法。它仍然会漏 —— 漏掉的那些落进弃权，
        也就是人工审核，而那一边是安全的。

        正文**不需要**命中任何规则：弃权是本模块的常态输出，不是失败。
        """
        declared = self._declared(event)
        if declared is not None:
            return declared

        body = event.body
        if not body.strip():
            return Classification(
                reason=(
                    "正文是空的，没有任何可读的文字判据。空的正文不等于「没有内容」"
                    "——内容可能在附件里、或者在只有 HTML 的那一段；把「读不到正文」"
                    "读成「这封信不重要」会让一封投诉变成一条只记录的事件。"
                    "交给人读原件（§7：「模糊回信 → manual_review_hold」）。"
                )
            )

        folded = body.casefold()
        for rule in _ORDERED_RULES:
            hit = next((cue for cue in rule.cues if cue in folded), "")
            if hit:
                return Classification(
                    kind=rule.kind,
                    reason=(
                        f"正文里出现了 {hit!r}（{rule.label}）。判据在 "
                        f"{rule.label} 这一条规则上：{rule.guards}"
                    ),
                )

        return Classification(
            reason=(
                f"正文里没有命中任何一条规则（查过 {len(_ORDERED_RULES)} 类、"
                f"{sum(len(rule.cues) for rule in _ORDERED_RULES)} 个词）。\n"
                "本分类器**读不出「内容相关」**：一封真人写的、内容相关的回信"
                "（「可以，下周二下午聊聊」）与一封读不懂的信，在词表上长得一样。"
                "它不猜 HUMAN_REPLY —— 那会把每一封读不懂的信变成一条销售交接；"
                "也不猜别的分类 —— 每一个都带着一条会被执行的处置。§7 逐字："
                "「模糊回信 → manual_review_hold」。"
            )
        )

    def _declared(self, event: CorrespondenceEvent) -> Classification | None:
        """来源自己声明过分类吗。声明了就直接照用；没声明（或声明的正是那个
        会被当作缺省值写下来的 ``HUMAN_REPLY``）返回 ``None``，让正文接着被判。

        两个理由（``HUMAN_REPLY`` 为什么例外、``_kind_of`` 的占位符）逐条写在
        模块 docstring 里 —— 这一行是那句话的执行器。
        """
        kind = event.kind
        if kind is None or kind is CorrespondenceKind.HUMAN_REPLY:
            return None
        return Classification(
            kind=kind,
            reason=(
                f"来源自己给了分类 {kind.value}，本分类器不再读正文。\n"
                "来源的结构性判据（一封退信是收信服务器按 RFC 回的）比一张词表强，"
                "而让词表去「再确认一下」只是给了它一次推翻确定事实的机会。"
            ),
        )


__all__ = ["RuleBasedReplyClassifier", "TextRule"]

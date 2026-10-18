"""**撤回一条已批准说法**所需要的那两件存储能力。

## 为什么不复用 ``OutreachStore``

``OutreachStore`` 已经答得了「某一批（``wave``）里每份草稿的最新一版」
（``latest_drafts(*, wave_id)``）。而 ``withdraw_claim`` 问的是另外两个问题，
两个都不是按批问的：

1. **本工作区里所有引用了这条说法的草稿** —— 一条说法不绑批次，它跨 campaign、
   跨 wave。按 wave 枚举需要调用方先知道有哪些 wave，而那个列表恰恰是**存储**
   知道、调用方不知道的东西（``OutreachStore`` 没有一个 ``waves()``）。
2. **这一批写是同一个事务** —— 「全成或全不成」不能靠调用方把几次 ``put_draft``
   串起来实现：那几次之间有一次提交，进程在那个缝隙里被杀掉就是一个半改的状态，
   而它正是这条工具最贵的那一格（承重点 3）。

把这两件事加在 ``OutreachStore`` 上是一个可行的方向，但那要动一个别的切片正在
用的端口（``ports/outreach.py``）；本片按纪律**不改别的切片的文件**，而是把这两件
能力单独提成一个契约。两件事都只有一处实现（``adapters/sqlite/claim_withdrawal.py``），
端口按 ``ports/__init__.py`` 那条「等第二个实现出现再提取」的纪律**本来还不该有**；
它今天存在，是因为另一个消费者已经出现了 —— 契约测试要用它，而测试替身就是第二个
实现（``tests/fakes.py`` 的形状）。

## 这一层不知道「草稿该不该进 hold」

端口只答「有哪些」「把这几条写下去」。**哪些进、哪些不进**由
``application/claim_withdrawal.py`` 从证据算出来（已发出去的不撤回），
而这是有意的：那条判断只要落在两层里，漂移的那天两处各自自洽，而报告上
「撤回了几份、放过了几份」这个数就再也对不上了。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from marketops.domain.outreach import ApprovedClaim, OutboundAttempt, OutreachDraft

__all__ = ["ClaimWithdrawalStore"]


class ClaimWithdrawalStore(ABC):
    """撤回一条说法所要的那两个能力：按工作区枚举，与一次原子的写。"""

    @abstractmethod
    def drafts_citing(self, *, workspace: str, claim_id: str) -> tuple[OutreachDraft, ...]:
        """本工作区里**引用了 ``claim_id``** 的草稿，每份取最新一版。

        判据是 ``OutreachDraft.claim_ids`` —— 一个**结构化引用**（草稿自己记着它
        引了哪几条说法），不是正文里的关键词。关键词匹配会让两件事同时发生：
        一条改写过而没有引用过的草稿被误伤，而一条引用了但没提到字面的草稿被漏掉，
        而**两者在报告上都表现为「撤干净了」**。

        ``claim_id`` 不带版本：``OutreachDraft.claim_ids`` 记的正是 ``claim_id``，
        而版本在草稿那一格上不存在。这是实测出来的形状（``domain/outreach.py``
        的 ``claim_ids`` 字段），不是设计选择。
        """
        ...

    @abstractmethod
    def attempts(self, *, workspace: str) -> tuple[OutboundAttempt, ...]:
        """本工作区的全部投递尝试。

        它回答「哪几份草稿**已经出去过**」—— 而那是撤回的**排除条件**（已发出的
        不撤回：把已经出去的东西标成 hold 是一种谎）。这里回的是原始尝试行，
        不是「发过没有」这个布尔：判「出去过」的那条规则住在
        ``domain/outreach.py::SENT_OR_UNKNOWN``，而它有四个成员与两条推理
        （``DELIVERY_UNKNOWN`` 在里、``FAILED`` 不在里）。在这儿再写一遍那个集合
        就是同一句话的第二份实现。
        """
        ...

    @abstractmethod
    def withdraw(self, *, retirement: ApprovedClaim, drafts: tuple[OutreachDraft, ...]) -> None:
        """**一个事务里**写完两件事：那条说法的新版本、以及这几份草稿的新版本。

        入参是**要写下去的整份对象**（不是 id 列表）：端口不做领域判断，
        它只保证「这几行要么全在、要么全不在」。

        两半都必须写，而且必须在同一个事务里：

        * ``retirement`` —— 那条说法的一个新版本，``expires_at`` 落在撤回那一刻。
          说法的退场走**追加一版**而不是改一行：``outreach_claim`` 是
          ``(claim_id, revision)`` 的表，就地改写会让「当时批准的那句话」在盘上
          变成另一句。撤回之后它不再算数，是因为每一个消费者都按
          ``ApprovedClaim.is_live_at(moment)`` 筛（``application/outreach.py:1280``
          逐字是这一条），而 ``approved_claims()`` 只回每个 id 的**最新**一版。
        * ``drafts`` —— 受影响草稿各自的新版本（``state=HELD``）。同一条纪律：
          ``put_draft`` 在 ``(draft_id, draft_version)`` 上幂等、**不同内容报错**，
          所以「进 hold」在这里的形态是出一版新的。

        一个把两半拆成两次调用的实现，会在两次之间留下一个「说法已经撤了、
        草稿还压着」的窗口 —— 而那个窗口里发出去的东西，事后没有人能把它与
        「本来就不该发」的那一半分开。
        """
        ...

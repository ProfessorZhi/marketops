"""``EvidenceStore`` 抽象：研究产出的三个面怎么落盘。

## 为什么是**四个**面，不是一组

``routes`` 与 ``opportunities`` 是两个集合，不是一个集合加一个空判断：一条路线
必须有 literal 邮箱（SOP L126），没有邮箱的是尚未解决的**路线机会**。合成一个
``list_contacts`` 会让「邮箱是否为空」变成每个调用方各自记得检查的约定 ——
而约定会在第一次「先跑通再说」的改动里消失。

第四个面是**拒绝**（``route_rejection``），与路线那两层一起加进来（2026-09-26，
§6）。它既不是路线也不是机会：一条被拒绝的候选**没有**变成一个对象，它是一次
判定及其理由。此前它只活在 ``RouteProduction.rejected`` 这个内存里的列表里，
而那个列表在函数返回时就没了 —— 于是报告里唯一留下的是 ``rejected = 37``，
既没有说 37 条是哪些，也没有说它们为什么不算数。拒绝因此不是「答案的一个子集」，
而是一类**要单独读的产出**，所以它有自己的一对方法，不被塞进 ``list_routes``。

## 为什么这个端口到 B5 才被提出来

它此前一直住在 ``marketops/evidence/store.py`` 里，和 JSONL 实现同一个文件。
B3 记录过为什么不把它提前搬出来：判据是**端口在第二个实现出现时才提取**。
到 B5 为止有三个：JSONL（``adapters/jsonl/``，人类可直接 grep 的审计通道）、
SQLite（``adapters/sqlite/``，事务性的主存）、内存（``adapters/memory.py``，
测试替身）。

三个都通过 ``tests/test_evidence_store_contract.py`` 的**同一组判据** —— 那才
是「这是一个契约」的证据。少于两个实现时写下它，产出的是一个凭想象画的形状，
会在第一个真实实现落地时按那个实现的形状重写一遍。

本模块**不得**出现任何厂商名，也不得 import 任何存储引擎（``sqlite3`` / ``redis``
/ ``pymongo`` …）—— 一份 import 了 ``sqlite3`` 的契约看起来完全正常（ABC 还在、
``isinstance`` 还过），它只是从此只为那一个引擎而存在。判据由
``tests/test_ports_are_contracts_only.py`` 执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.entities import RouteOpportunity
from ..domain.evidence import Evidence
from ..domain.route_records import (
    CanonicalContactRoute,
    RouteObservation,
    RouteRejection,
)


class EvidenceStore(ABC):
    """证据、路线与路线机会的存储契约。

    ## 幂等边界：证据去重，路线与机会**不**去重

    ``put_evidence`` 幂等，因为重复的同一条证据没有新信息。``put_opportunity``
    **累积**：同一个账号的两条未解决机会是两件事，而一个重复出现的机会更可能
    意味着一次没有被合并的重复抓取 —— 那件事应该被看见，而不是被静默吞掉。

    路线**不在**这一条里，它是本模块 2026-09-26 改动的全部内容，理由见下。

    ## 同一 ``evidence_id`` 配不同内容必须报错

    ``evidence_id`` 是**内容寻址**的（见 ``domain.evidence.evidence_id``），所以
    同一个 id 配不同的 payload 只可能意味着 id 被手工指定过。三个实现都必须在
    这一点上显性失败：静默保留先写的那条，会让「证据写进去了」与「我以为写进去
    的这条证据」变成两件事。

    ## 一个存储**只装一个工作区**：写别人的被拒，读别人的**报错**

    三行产出（``Evidence`` / ``ContactRoute`` / ``RouteOpportunity``）都带
    ``workspace``（必填、无默认值，见 ``domain/evidence.py``），而一个存储实例
    或一个落盘位置**只属于一个工作区**：

    * **写**：对象的 ``workspace`` 不是这个存储的工作区 → :class:`EvidenceError`。
      理由不是洁癖：混装之后 ``list_evidence("kent-004")`` 会把两个部署关于同一个
      账号的行**混在一起**返回，而报告上看起来只是「证据更多了」。
    * **读**：某一行的 ``workspace`` 与存储的工作区不一致 → :class:`EvidenceError`，
      **点名那一行**（表 + 主键，JSONL 那一侧还有文件与行号）。
      **不是**跳过那一行、返回剩下的。

    第二条的形状值得写清楚：跳过之后，一个只装着别人那些行的账号返回的是**空列表**，
    而它与「这个账号确实没有证据」在调用点长得**完全一样** —— 越权读因此变成一次
    静默成功，而报告里那句「这个账号没有证据」是**假话**。所以这一条判据的产出是
    拒绝，不是空。

    由此 ``get_evidence`` 的 ``None`` 只剩一个意思：**没有这个 id 的行**。它不再
    意味着「有这一行但不给你看」——后者是 :class:`EvidenceError`。

    三个实现怎么拿到「自己的工作区」，各自不同（``adapters/jsonl`` 从 ``Settings``
    读、``adapters/sqlite`` 记在库里并由第一行写入建立、``adapters/memory`` 同前者），
    而上面两条判据在三个实现上一致 —— 由
    ``tests/test_evidence_store_contract.py`` 一起跑。
    """

    @abstractmethod
    def put_evidence(self, evidence: Evidence) -> None:
        """写入一条证据。同 ``evidence_id`` 且内容相同 → 幂等；内容不同 → 报错。"""

    @abstractmethod
    def list_evidence(self, account_id: str) -> list[Evidence]:
        """列出某账号的全部证据。"""

    @abstractmethod
    def get_evidence(self, evidence_id: str) -> Evidence | None:
        """按 id 取一条证据。取不到返回 ``None`` —— **不抛**。

        ## 为什么是「按 id」而不是「按账号」

        §3 那一行 ``get_evidence`` 的输入形状是「evidence id + bounded locator」，
        而账号视图是 ``get_account`` 的事。两条路回答的是两个问题：「这条引用
        解不解得开」与「这个账号有什么」。合成一条之后，只有已经拿到账号的人才
        验得了引用 —— 而 ``submit_findings`` / ``decide`` 的调用方手里只有一个
        引用。

        **取不到为什么是 ``None`` 而不是异常**：那是一个**结果**。抛出去的话，
        唯一能写下正确处置的地方被夹在 ``except`` 里，而更常见的写法是把它并进
        那个已经接住「库损坏 / 连接失败 / schema 不符」的兜底 —— 于是「这条引用
        是假的」与「存储坏了」在报告上同形。

        ## 三个实现的代价**不一样**，写在这里

        ``evidence_id`` 是内容寻址的（``domain.evidence.evidence_id`` 把
        ``account_id`` 也算进原像），所以三个实现看到的**是同一个 id 空间**。
        但落盘的分片键是 ``account_id``：SQLite 与内存实现各持一份按 id 的索引，
        取一次是 O(1)；``adapters/jsonl`` 的文件名就是账号，按 id 取只能扫目录
        （O(存档量)）。那一份是**导出与审计通道**，不是主存 —— 这条代价是它分片
        方式的结果，不是这个端口的设计缺陷。换分片键要动的是磁盘布局，那件事的
        影响面远大于这里省下的几次 readdir。
        """

    @abstractmethod
    def record_route(self, observation: RouteObservation) -> CanonicalContactRoute:
        """记下一次路线观察，返回折叠之后的**当前投影**。

        ## 为什么 ``put_route`` 被删掉，而不是被加上一个去重

        ``put_route`` 写的是一行**路线**，而读的人（``get_account`` / 汇报 /
        发信计划）问的是「这个账号现在的联系入口是什么」。两个问题共用一个名字，
        后果在 2026-09-26 之前是可见的：每跑一次研究就 ``INSERT`` 一行，
        同一个人的同一条路线于是有 N 行，而报告只会说「路线更多了」。
        加一个「写入时先查重」补不上这个洞，因为**要按什么查重本身就是一个
        判定**：按邮箱？那么同一个人换了个邮箱算不算换了人？按来源页？那么同一页
        重抓两次算两条。把那个判定塞进每个适配器，就是让三个适配器各自回答一遍
        同一个业务问题 —— 而它们会给出三个答案。

        所以这里分成两层，判定留在领域层（:func:`~marketops.domain.route_records.fold_observation`）：
        观察是**历史**（追加、不去重，一次重抓确实发生了一次），投影是**当前读法**
        （唯一、可覆盖）。写上来的是一条观察，读出去的是一条投影。

        ## 原子性：这是一次写，不是两次

        「先追加观察、再折叠、再 upsert 投影」三步必须**在同一个写事务里**完成。
        分成三次调用的话，中间崩溃会留下一条进了历史、没进投影的观察 ——
        而这条路线从此在 ``list_routes`` 里不存在，它**看起来像是那次抓取没发生过**。
        §6.2 要求跨页面 / 跨 worker / 跨重试幂等且并发安全，而幂等的第一步是
        「要么全发生、要么全不发生」。

        ## 并发下同一个观察写两次

        按 ``observation_id`` 幂等（它是内容寻址的，见
        :func:`~marketops.domain.route_records.observation_id_for`），所以两个
        worker 同时交同一次观察，结果与只交一次**逐位相同**，并且第二次也返回
        同一份投影。**不是**「第二次报冲突」—— 两个 worker 观察到同一个事实
        不是一次错误。

        ## 时间上更早的一次写入

        迟到的写**不**覆盖当前读法（SOP L126 的「当前」是时间序，不是到达序），
        但它仍然进历史。所以返回值告诉你的是**当前投影**，不一定是刚交的这条。
        调用方要判断自己这一次有没有生效，读 :attr:`CanonicalContactRoute.last_observation_id`。
        """

    @abstractmethod
    def list_routes(self, account_id: str) -> list[CanonicalContactRoute]:
        """列出某账号的**当前投影**，按 ``canonical_key`` 升序。

        **不是**历史。历史上写着几次同样是研究上的一个事实，而调用方要的
        是「现在是什么」。要历史用 :meth:`list_route_observations`。

        排序键是 ``canonical_key`` 而不是 ``first_observed_at``：后者在
        「先看到 B、后看到 A」与「先看到 A、后看到 B」两种过程下给出不同的顺序，
        于是同一份存档在两个 worker 手里报告出两个列表顺序 —— 而报告是要被
        逐行比对的。键是内容，不随过程变。
        """

    @abstractmethod
    def list_route_observations(self, account_id: str) -> list[RouteObservation]:
        """列出某账号的**全部历史观察**，按 ``(observed_at, observation_id)`` 升序。

        这是 :meth:`list_routes` 那句话的证据链：投影上的 ``observation_ids``
        每一条都在这份列表里解得开。两者**同源**（同一个折叠函数的输入与输出），
        所以「投影说它见过什么」与「历史里有什么」不会各说各话。
        """

    @abstractmethod
    def put_route_rejection(self, rejection: RouteRejection) -> None:
        """写入一条**被拒绝的路线候选**。按 ``rejection_id`` 幂等。

        §6.3：``RouteProduction.rejected`` 此前是被算出来就丢掉的。这一格是
        它落盘的地方 —— 写下来的不是计数，是那一条候选本身（账号、字面邮箱、
        来源 URL、原因代码、规则、证据引用、run / work item、时间）。

        **幂等，而观察是追加**：一次拒绝说的是「这一次运行在那个页面上没有接受
        它」，同一句话重说一次没有新信息，所以 ``rejection_id`` 把
        ``source_url`` 与 ``occurred_at`` 也算进原像 —— 同一个邮箱在**另一个**
        页面上被拒绝是另一条记录，因为它是另一份证据。

        ## 幂等是**覆盖**，不是「相同就跳过」

        同一个 ``rejection_id`` 被写第二次时，两次的内容**必然**在记账那几格上不同
        （``run_id`` 换了，而它不在原像里，否则重跑一次就会多出一行）。所以这一句
        「幂等」的准确含义是：

        * **身份那几格必须逐位相同**（:meth:`~marketops.domain.route_records.RouteRejection.identity`
          的全部，即除 ``run_id`` / ``work_item_id`` / ``rule`` 之外的每一格）——
          不同就抛 :class:`~marketops.domain.errors.EvidenceError`，因为那意味着
          ``rejection_id`` 被手工指定过；
        * **记账那三格换上本次的值**，落在同一行上 —— 收据上留的是**最近一次**拒它
          的那一趟。

        三个实现必须在这两条上一致，``tests/test_evidence_store_contract.py`` 是
        执行器。此前三个实现都要求**整行**逐字节相同，于是重试、两个 worker、维护
        轮次重看同一页各撞一个 ``EvidenceError`` —— 而那看起来像存储坏了。
        """

    @abstractmethod
    def list_route_rejections(self, account_id: str) -> list[RouteRejection]:
        """列出某账号的全部拒绝记录，按 ``(occurred_at, rejection_id)`` 升序。

        升序而不是降序：报告的读法是「先看原因分布，再逐条看」，而逐条那一段
        要能对着时间读一遍。降序会让第一次读的人从最新那条往回收，而新的事
        没有上下文。
        """

    @abstractmethod
    def put_opportunity(self, opportunity: RouteOpportunity) -> None:
        """写入一条未解决的路线机会。累积，不去重。"""

    @abstractmethod
    def list_opportunities(self, account_id: str) -> list[RouteOpportunity]:
        """列出某账号的全部路线机会。"""


__all__ = ["EvidenceStore"]

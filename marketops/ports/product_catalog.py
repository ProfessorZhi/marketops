"""``ProductCatalog``：产品资料与 Offer 的登记契约。

## 它约束的是哪一件事

一句话：**版号一经写下就不再变**。同一个 ``(product_id, version)`` 再写一次，
内容相同是幂等（重启后重放一批登记会走到这条路上），内容不同则必须报错 ——
理由是「每一处引用都只写 ``(id, 2)``，而两次部署之间『第 2 版』可以是两份不同的
东西」，而这件事**没有任何一处调用点**能看出来。

## 冲突报的是 ``StoreError``

纪律与 ``SceneStore.put_version``、``EvidenceStore.put_evidence`` 逐字同源，报的
异常跟随 ``SceneStore``：:class:`~marketops.domain.errors.StoreError`。产品目录与
场景是**同一个形状**（append-only 版号 + 用 revision 做 CAS 的状态），版号是**账**：
重读还是两份内容，只有人能把账对齐，而当事人也不能「换一个版号」—— 那正是改历史。

证据那边用自己的 ``EvidenceError``，因为证据是**产出**、错了要重做研究。处置不同
才分类型这件事写在 ``errors.py`` 里 ``StoreError`` 的 docstring 上，不在这一处另立。

:class:`~marketops.domain.product.ProductLine` 是这条规矩的一个**形状不同**的例子：
它没有版本号（PSM §4.1 的「零行为分组」），所以 ``line_id`` 自己就是那个唯一版本，
同一个 ``line_id`` 写入不同内容同样报错。不是覆盖 —— 覆盖会让「这一组的成员什么
时候变的」从记录里消失，而那一刻正是有人要查的东西。

## 为什么端口到现在才有

``ports/__init__.py`` 立的那条纪律是**等第二个实现出现再提取**，否则写出来的是一份
「看起来设计完了」的猜测。这里两个实现与端口同一天落地（``adapters/sqlite`` 落盘、
``adapters/memory.py`` 测试替身），所以它不是从既有实现里提取的，而是从**两个实现
要一致**这件事里提取的 —— 形状因此被 ``tests/test_product_catalog_contract.py``
钉住，那份判据在两个实现上跑同一份。

## 端口上**没有**的东西

* 没有 ``delete``。版号是记录：退役是一个状态（``ProductStatus.RETIRED``），不是
  一次删除。删掉一个被 Offer 引用过、被草稿回链过的版本，等于让那些东西指向一个
  不存在的东西。
* 没有 ``retire`` / ``approve`` 这类改状态的独立动词。状态是对象自己的 ``status``
  格，由 ``put_version`` 写入。多一个动词就有**两条**改状态的路径，而它们不一致时
  读的人无从判断哪个对 —— 这与 ``ports/scenes.py`` 不存 ``next_due`` 是同一条理由。
* 没有「按名字查」。产品目录的键是 ``(id, version)``；一个按 ``display_name`` 模糊
  找的入口会让「找错了产品」在报告上表现为「找到了」，而 B01 ``:100`` 逐字要求
  「歧义不能猜」。
* 没有任何与价格有关的入口。价格只住在 :class:`~marketops.domain.product.QuotedPrice`
  里、只挂在 Offer 上（判据在 ``tests/test_domain_product.py`` 第 4 组）。端口上多
  一个 ``price_of`` 就等于给价格开了第二处落点。

本模块**不得**出现任何厂商名，也不得 import 任何存储引擎。判据由
``tests/test_ports_are_contracts_only.py`` 执行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.product import OfferVersion, ProductLine, ProductVersion


class ProductCatalog(ABC):
    """产品版本、Offer 与产品线的登记与读回。"""

    @abstractmethod
    def put_version(self, version: ProductVersion) -> None:
        """写入一版产品资料。

        同 ``(product_id, version)`` 且内容相同 → 幂等；不同 →
        :class:`~marketops.domain.errors.StoreError`，且**一个字节都不写**。

        消息里必须点名 ``(product_id, version)``。``StoreError`` 也承载「这个库打不开」，
        而只说「冲突了」的回执既分不出是哪一件事，也分不出是哪一种 —— 判据因此无法
        把「真的冲突了」与「库坏了」分开，一条冲突判据会被一次存储事故点亮。
        """

    @abstractmethod
    def get_version(self, product_id: str, version: int) -> ProductVersion | None:
        """读一版产品资料。不存在返回 ``None`` —— **不是抛异常**。"""

    @abstractmethod
    def versions(self, product_id: str) -> tuple[ProductVersion, ...]:
        """按版本号升序列出该产品的全部版本。

        没有 ``latest()``：**「最新」不是一个可以在这个端口上回答的问题**。读的人
        要的是「我手上这一版」或者「改型号之后该看哪一版」，两者都要求他先说出
        自己的基准 —— 一个隐式的「最新」会在两版同时存在的那一刻悄悄改变含义。
        """

    @abstractmethod
    def put_offer(self, offer: OfferVersion) -> None:
        """写入一版 Offer。幂等边界与 ``put_version`` 逐字相同。

        消息里同样必须点名 ``(offer_id, version)`` —— 理由见 ``put_version``。
        """

    @abstractmethod
    def get_offer(self, offer_id: str, version: int) -> OfferVersion | None:
        """读一版 Offer。不存在返回 ``None``。"""

    @abstractmethod
    def put_line(self, line: ProductLine) -> None:
        """写入一条产品线。

        :class:`~marketops.domain.product.ProductLine` 没有版本号，所以 ``line_id``
        自己就是那个唯一版本：同一个 ``line_id`` 写入不同内容 →
        :class:`~marketops.domain.errors.StoreError`。消息里必须点名 ``line_id``。
        """

    @abstractmethod
    def get_line(self, line_id: str) -> ProductLine | None:
        """读一条产品线。不存在返回 ``None``。

        登记一个产品版本**不需要**先有产品线 —— ``ProductLine`` 是可选分组
        （PSM §4.1「仅在有真实管理需求时作为可选分组」）。
        """


__all__ = ["ProductCatalog"]

"""池子这一族的端口 —— 一个市场**跨运行**留下来的那几张表。

它是 ``ports/store.py``（证据 / 路线 / 机会）与 ``ports/scenes.py``（立项 / 维护）
之外的第三个存储端口模块。**分成第三个不是因为分层的洁癖，而是幂等边界不一样**，
与 ``SceneStore`` 独立出来时逐字同一条理由：

* ``EvidenceStore`` 的边界是「同一 ``evidence_id`` 同一内容」—— 证据只增不改
  （``put_evidence`` 对改过的同 id 直接报错）。
* ``SceneStore`` 的边界是 ``revision`` 的乐观并发：改一条记录必须说出你读的是
  第几版，否则并发的另一个写者会**成功**。
* 这里**既不是只增、也不是乐观并发**：池子是**累积并修订**的 —— 同一块场地会被
  反复看到、同一条责任关系会被反复复核、同一个账号会被反复研究，每次都会带来新的
  一行（新的 URL、新的候选域名、新的证据、一个新的 ``last_verified_at``），而它
  **还是同一块场地、同一条关系、同一个账号**。所以这几张表的主键上的写都是覆盖写，
  而「覆盖时哪些格子不许被抹掉」是一条**业务规则**
  （``application/pool.py`` / ``application/relations.py`` / ``application/accounts.py``），
  不是这里。

## 四张表住同一个模块，但是**四个** ABC

它们是同一个幂等边界（累积并修订）的三个落点，所以同属一个端口模块 ——
``ports/scenes.py`` 里的 ``SceneStore`` 与 ``MaintenanceStore`` 是同一个写法。
分成三个接口，是因为**写它们的不是同一步**：资产行由一份母池回执折进来
（``application/pool.py::assets_from_receipt``），关系行由一次责任判定写下来
（§5），账号行由一次账号研究写下来（§6）。生产者不同、键不同、合并规则不同，
合成一个接口就会让「只接上了资产那一半」变成一种写不出来的状态。

第三个成员（§6 的账号池）落地时把这件事反过来验了一次：这一族里**唯一**被三家
共用的是那两个合并原语（``application/merge.py``），而**接口**一个也没被共用 ——
资产池按 ``venue_asset_id`` 折一行、关系表按四段键折一行、账号池按 ``account_id``
折一行，三者的键连形状都不同。所以这一族的增长方式是**加接口**，不是加参数。

第四个成员（§B12 的冻结成员名单）落在**同一个模块**里，而它**不共享这个幂等边界**
—— 它是内容寻址的幂等覆盖（同 id 的两份内容除 ``created_at`` 外逐位相同），既不
累积也不修订。仍然住在这里，是因为它与账号池是同一件事的两半：名单读的就是池子，
而「池子里有谁」与「这一批做谁」必须住在能一起被读到的地方。见
:class:`AccountCohortStore` 自己的 docstring。

## 四张表为什么不能合进 ``evidence``

``evidence`` 一行回答「凭什么这么说」，这四张表回答「**有什么**」、「**谁负责**」、
「**有哪些账号**」与「**这一批做谁**」。把它们放进一张表之后，「这个市场里已经有
哪些场地」这件事就必须从证据里**推**出来 —— 而推法（按 URL 去重？按域名？按名字？）恰好是这一层最
不该藏起来的东西：``osm:node/123`` 与 ``site:example.org`` 认的不是同一个东西，
一块场地上可以挂着几十条证据，而一条证据可以同时提到三块场地。没有这张表的时候，
每次运行都重新发现一遍，而报告上看起来像「这一轮没找到什么新东西」。责任关系同理：
它是**判定**，不是从证据里能推出来的东西（一条证据可以同时说三块场地的事，而
「这块场地由谁维护」是一次判定）。账号池是同一句话的第三种形状：一条证据可以同时
提到若干个组织，而「这些组织里的哪一个是我们要联系的」不是从那条证据里读得出来的。
名单是第四种：它是**一次读数**（某一刻池子里有谁、按哪条规则读的），而「读数」这件
事证据里连一个字都没有 —— 池子变了之后，那份读数再也推不出来。

## ``workspace`` 是**行自己的**，不是调用方的参数

与 ``Evidence`` / ``Observation`` 同一个写法：血统写在行上，写路径当场说清。
读取侧不传 ``workspace`` —— 一个库绑在一个部署上（``db.py::bind_workspace``），
不属于本库的行**报错**而不是被跳过：跳过会让「这条不属于你」与「没有这一条」
在调用点长成同一个空结果。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from ..domain.account import MarketAccount
from ..domain.cohort import AccountCohort
from ..domain.entities import AccountVenueRelation, VenueAsset
from ..domain.fold import FoldAttribution

__all__ = [
    "AccountCohortStore",
    "AccountMerge",
    "AccountPoolStore",
    "AccountVenueRelationStore",
    "AssetMerge",
    "RelationMerge",
    "VenueAssetStore",
]

#: 把一次新观察折进池子那一行。**规则由 ``application/pool.py`` 给出**，存储只负责
#: 在同一个写事务里调用它 —— 见 :class:`VenueAssetStore`。
#:
#: 第一个参数是**库里已有的那一行**，``None`` = 这一行还不存在。返回值是合并之后
#: 该落盘的那一行。
AssetMerge = Callable[[VenueAsset | None, VenueAsset], VenueAsset]

#: 把一次新的责任判定折进关系表里已有的那一行。**规则由
#: ``application/relations.py`` 给出** —— 与 :data:`AssetMerge` 逐字同一条边界：
#: 第一个参数是库里已有的那一行（``None`` = 还没有），返回值是合并之后该落盘的那一行。
RelationMerge = Callable[
    [AccountVenueRelation | None, AccountVenueRelation], AccountVenueRelation
]

#: 把一次新的账号研究折进账号池里已有的那一行。**规则由
#: ``application/accounts.py`` 给出** —— 与上面两个逐字同一条边界：第一个参数是库里
#: 已有的那一行（``None`` = 还没有），返回值是合并之后该落盘的那一行。
AccountMerge = Callable[[MarketAccount | None, MarketAccount], MarketAccount]


class VenueAssetStore(ABC):
    """场地资产池：``VenueAsset`` 的持久落点。

    **实现必须让 ``upsert_asset`` 幂等**（同一个 ``venue_asset_id`` 折两次，读回来的
    是同一行，且第二次不会多出一行）。注意这与「两次写必须等价」不是一回事：
    第二次写的内容可以不同（池子是累积并修订的），但**行的身份**不变。

    **不认识的行不许悄悄变成新行**：按 ``venue_asset_id`` 折，而不是按内容插入。
    按内容插入会让「同一块场地被重新看到」在报告上读成「这个市场多了一块场地」，
    而那个数正是这一层要报给操作员的。

    ## 这个端口有一个构造参数，而别的端口都没有

    ``__init__`` 收一个 :data:`AssetMerge`。理由是**只有这个端口的写在语义上是一次
    「读—改—写」**：折进去的那一行取决于库里已有的那一行，而这两步必须在同一个写
    事务里（``db.py::write_transaction`` 的 ``BEGIN IMMEDIATE``），否则两个并发的
    worker 会各自读到同一份旧行、各自写回一份新行，**后写的那一次把前一次带进来的
    观察整个吃掉**。

    把规则做成调用参数也能编译通过，但那样同一个库上就可能有两条合并规则，而
    「这一行是怎么变成现在这样的」就没有唯一答案了 —— 与 ``GuardedMailTransport``
    把闸装在构造点上逐字同一条理由（``interfaces/composition.py``）。

    **合并在哪个事务里做，是这个端口的承诺**：实现必须把「读旧行」与「写新行」放进
    同一个写事务（SQLite 那一份靠 ``BEGIN IMMEDIATE``；内存那一份**没有锁**，见它
    自己的 docstring）。这一条写进共享契约测试 —— 它不写，两个实现就各松各的。
    """

    @abstractmethod
    def __init__(self, merge: AssetMerge) -> None:
        """接住合并规则。实现必须把它存下来，在 :meth:`upsert_asset` 里用。"""

    @abstractmethod
    def upsert_asset(
        self, incoming: VenueAsset, *, fold: FoldAttribution | None = None
    ) -> VenueAsset:
        """把一次观察折进池子，返回**落盘后的那一行**。

        返回而不是 ``None``，是因为调用方需要的往往正是「哪些格子被保住了」——
        比如一次重新导入之后，被留下的那个已确认官网。让它再读一遍等于把
        「读到的」与「刚写下的」分成两次快照，而中间可能有人插进来。

        ## ``fold``：这一次池子的写**归属于哪一轮 Run**

        给了它，这一行与那一笔归属就**在同一个写事务里**落下（§B17 条件 2）。这
        不是「顺便记个日志」——它是 ``close_run`` 那一侧唯一的权威：``RunStore.
        folds_of`` 读的就是这些行，而它读不出来的账本不许被读成「零折入」。

        不给它（``None``）是**一个被声明出来的事实**：这一行不属于任何一轮 ——
        研究流程、一次导入、任何没有 Run 在场的折入都是这个形态，而它们今天合法。
        单看存储，两种形态的差别只在有没有那一笔归属；**这个端口不替调用方回答
        「你该不该给」** —— 那是折入那条路径的事（``application/promotion.py``
        收 ``run_id``）。

        **给了一个不存在的、或者已经结束的 ``run_id`` 是一句拒词，不是一次静默的
        忽略**：忽略会让「这一次没记」与「这个功能还没造」在盘上同形，而池子那一行
        已经落下去了 —— 那正是这条账要挡的假账。拒绝时**池子一个字节都不写**。

        Raises:
            RunStateError: ``fold.run_id`` 指的那一轮不存在，或者已经结束了。
            RunFoldLedgerError: 那一轮的账本不在（受理本该把它写下来）。
            StoreError: 实现自己的问题（不属于本库的行、绑在工作区上的第二个
                工作区）。**内存那一份在没接账本时也走这一类**：它有归属却无处记，
                那时抛比静默丢掉诚实。
        """

    @abstractmethod
    def get_asset(self, venue_asset_id: str) -> VenueAsset | None:
        """按 id 取一行。``None`` 只有一个意思：**没有这个 id 的行**。

        这一行存在、但不属于本库的部署时，抛错而不是返回 ``None`` —— 见模块
        docstring 的「``workspace`` 是行自己的」。
        """

    @abstractmethod
    def list_assets(self) -> tuple[VenueAsset, ...]:
        """本库全部的场地资产，按 ``venue_asset_id`` 稳定排序。

        排序是承重的：报告里的截断（``application/caps.py``）丢的是尾部，顺序不确定
        时那份 ``dropped_items`` 就只是一份噪音。所以实现**不许**把顺序留给
        dict 的迭代序或 SQL 的返回序。
        """


class AccountVenueRelationStore(ABC):
    """责任关系表：``AccountVenueRelation`` 的持久落点。

    **实现必须让 ``upsert_relation`` 幂等**（同一条关系折两次，读回来的是同一行，
    且第二次不会多出一行）。与 ``VenueAssetStore`` 一样，这与「两次写必须等价」不是
    一回事：第二次的内容可以不同（证据会增多、``last_verified_at`` 会往后走），但
    **行的身份**不变。

    ``__init__`` 收一个 :data:`RelationMerge`，理由与 ``VenueAssetStore`` 逐字相同：
    只有这一族的写在语义上是一次「读—改—写」，而这两步必须在同一个写事务里，否则
    两个并发的 worker 会各自读到同一份旧行、各自写回一份新行，**后写的那一次把前一次
    带进来的证据整个吃掉** —— 而它的表现不是报错，是一次没人看得见的丢失。

    ## 三个读，而不是一个带筛选参数的读

    ``relations_for_account`` / ``relations_for_asset`` / ``list_relations`` 回答的是
    **三个不同的问题**（这个账号负责什么 / 这块场地由谁负责 / 这个市场里有哪些责任
    关系）。写成 ``list_relations(account_id=None, venue_asset_id=None)`` 也能跑，
    但那会让「按场地读」与「按账号读」在下游变成同一个调用点的两个参数值 —— 而
    §5 的方向是**资产 → 账号**（先有场地，再问谁负责），两者混在一个方法里读不出
    这个方向，也让实现无从按访问路径建索引。
    """

    @abstractmethod
    def __init__(self, merge: RelationMerge) -> None:
        """接住合并规则。实现必须把它存下来，在 :meth:`upsert_relation` 里用。"""

    @abstractmethod
    def upsert_relation(self, incoming: AccountVenueRelation) -> AccountVenueRelation:
        """把一次责任判定折进关系表，返回**落盘后的那一行**。

        返回而不是 ``None``，理由同 ``VenueAssetStore.upsert_asset``：调用方需要的
        往往是「哪些格子被保住了」（比如这一次带进来的证据与已有的那几条并成了什么），
        而再读一遍等于把「读到的」与「刚写下的」分成两次快照。
        """

    @abstractmethod
    def relations_for_account(self, account_id: str) -> tuple[AccountVenueRelation, ...]:
        """这个账号责任范围内的**全部**关系，按四段键稳定排序。

        没有这一条关系的账号给出**空元组**，不是错误：一个账号一块场地都不负责是
        一个正常的答案（名单里的组织可能一块场地都不负责，见 ``ResponsibleEntity``
        的类文档），而它与「这个库坏了」必须分得开。
        """

    @abstractmethod
    def relations_for_asset(self, venue_asset_id: str) -> tuple[AccountVenueRelation, ...]:
        """这块场地的**全部**责任关系，按四段键稳定排序。

        §5 的方向就是这一条：手上有一块场地，问「谁对它负责、凭什么这么说」。
        空元组的意思是「还没有人判过」，**不是**「没人负责」—— 后者要有凭据，
        而本层今天表达不出它（见 ``application/relations.py`` 末尾那条边界）。
        """

    @abstractmethod
    def list_relations(self) -> tuple[AccountVenueRelation, ...]:
        """本库全部的责任关系，按四段键（``relation_key()``）稳定排序。

        排序是承重的，与 ``list_assets`` 逐字同一条理由：报告里的截断丢的是尾部，
        顺序不确定时那份 ``dropped_items`` 只是一份噪音。**排序键只有一处定义**
        （``AccountVenueRelation.relation_key()``），实现不许把顺序留给 dict 的迭代序
        或 SQL 的返回序 —— 否则两个实现在同一批行上会给出不同的顺序，而契约测试
        正是拿这一点互相钉的。
        """


class AccountPoolStore(ABC):
    """账号池：``MarketAccount`` 的持久落点（§6）。

    **实现必须让 ``upsert_account`` 幂等**（同一个 ``account_id`` 折两次，读回来的
    是同一行，且第二次不会多出一行）。与另外两张表一样，这与「两次写必须等价」不是
    一回事：第二次的内容可以不同（别名会增多、证据会增多、Scene 的判定可能变了），
    但**行的身份**不变。

    ``__init__`` 收一个 :data:`AccountMerge`，理由与上面两个逐字相同：只有这一族的
    写在语义上是一次「读—改—写」，而这两步必须在同一个写事务里，否则两个并发的
    worker 会各自读到同一份旧行、各自写回一份新行，**后写的那一次把前一次带进来的
    证据整个吃掉** —— 它的表现不是报错，是一次没人看得见的丢失，且它只在两个
    worker **重叠**的时候发生（§7 的运行形态正是重叠的）。

    ## 这个端口**没有**按域名（或按名字、按地址）找账号的入口

    这是 §6 明令的那一条在接口上的形状：**不得因为同域名 / 同名 / 近似地址就合并
    两个实体**，也不得靠它们把两行认成同一个东西。所以这里只有 ``get_account``
    （按键取）与 ``list_accounts``（取全部）两个读 —— 一个 ``find_by_domain`` 会让
    「这两个账号是不是同一个」在下游变成一个可以顺手问的问题，而它没有诚实的答案：
    英国基层俱乐部大量挂在聚合域名上，两个不同的组织同域名是**常态**，且池子里
    没有任何一列说得清「这两个域名背后是不是同一个法人」。

    域名那一格仍然是有的，它回答的是另一件事（这个组织的官网在哪），形状判据在
    ``MarketAccount._every_domain_is_a_canonical_host`` 里，合并规则**不看它**。

    ## 一个「多一个筛选参数的读」在本层**也**不存在

    与 ``AccountVenueRelationStore`` 的三个读不同，这里没有第二个访问方向 ——
    账号池只有「按键取一行」与「取全部」两种问法（按场地读由关系表回答，
    §6 的读路径把两者接起来）。所以多写一个带筛选参数的读只是同一个问法的第二个
    写法，而它会让人以为「按某某筛选」是这一层的语义。
    """

    @abstractmethod
    def __init__(self, merge: AccountMerge) -> None:
        """接住合并规则。实现必须把它存下来，在 :meth:`upsert_account` 里用。"""

    @abstractmethod
    def upsert_account(self, incoming: MarketAccount) -> MarketAccount:
        """把一次账号研究折进池子，返回**落盘后的那一行**。

        返回而不是 ``None``，理由同上面两个 ``upsert``：调用方需要的往往是「哪些格子
        被保住了」（比如这一次带进来的证据与已有的那几条并成了什么），而再读一遍等于
        把「读到的」与「刚写下的」分成两次快照，中间可能有人插进来。
        """

    @abstractmethod
    def get_account(self, account_id: str) -> MarketAccount | None:
        """按 id 取一行。``None`` 只有一个意思：**没有这个 id 的行**。

        这一行存在、但不属于本库的部署时，抛错而不是返回 ``None`` —— 见模块
        docstring 的「``workspace`` 是行自己的」。这一条在账号池上比另外两张表更要紧：
        §6 要求客观事实可以跨 Scene 复用，而「复用」的正确形态是第二个 Scene 写**它
        自己的**行；一个把别的 Scene 的行读回来的路径会让「复用」变成「共用一行」，
        那正是「一个 Scene 的结论不得渗进另一个」要防的事。
        """

    @abstractmethod
    def list_accounts(self) -> tuple[MarketAccount, ...]:
        """本库全部的账号，按 ``account_id`` 稳定排序。

        排序是承重的，与 ``list_assets`` 逐字同一条理由：报告里的截断丢的是尾部，
        顺序不确定时那份 ``dropped_items`` 只是一份噪音。
        """


class AccountCohortStore(ABC):
    """冻结成员名单（§B12）：:class:`~marketops.domain.cohort.AccountCohort` 的落点。

    **第四张表，不是账号池上的第四个方法。** 与 ``ports/pool.py`` 的模块 docstring
    那条「这一族的增长方式是加接口，不是加参数」同一条：账号池按 ``account_id`` 折
    一行、名单按 ``cohort_id`` 存一份，键连形状都不同，而**写它们的也不是同一步** ——
    账号行由一次账号研究写下来，名单由一次「从池子里读一遍」冻下来。

    放在 ``ports/pool.py`` 而不是 `ports/scenes.py`：它与那一族共享幂等边界的一
    **半**。它不是累积并修订（同一 ``cohort_id`` 的两份内容只能在 ``created_at``
    上不同 —— 其余每一格都在内容寻址的原像里，见 ``domain/cohort.py``），也不是只增
    （重放同一份名单是一次**空写**，而不是一次报错）。它是本仓第三种的第二个实例：
    **内容寻址的幂等覆盖**，与 ``put_route_rejection`` 逐字同形。

    ## 一个「按期或按场景查最近一份」的读**不在这里**

    与 ``AccountPoolStore`` 那条「没有按域名找账号的入口」同一条纪律：最近一份是
    「按 ``created_at`` 排完取最后一个」，而**排序键必须只有一处定义**。
    写成 ``latest_cohort(scene_id)`` 那种读，会让「哪一份是当前的」在存储里有第二个
    定义处，于是 MCP 那一侧的「当前名单」与库里那一份会在某次改动后分家 ——
    而分家的表现是「报告说这一批跑的是 A」与「真的跑了 B」同时成立，两份都自洽。
    """

    @abstractmethod
    def put_cohort(self, cohort: AccountCohort) -> AccountCohort:
        """存一份名单，返回**落盘后的那一份**。

        按 ``cohort_id`` 覆盖写。同一份名单重放一次是**空写**（内容逐位相同），
        ``created_at`` 取更早的那一个 —— 它是「我们第一次把这份读数冻下来」，重放
        一份旧名单不该把它推后（与 ``VenueAsset.first_seen_at`` 同一条纪律）。

        实现必须让这一条成立：**同一个 id 配不同的内容是不可能的** —— 除了
        ``created_at``，其余每一格都在 :func:`~marketops.domain.cohort.cohort_id_for`
        的原像里，而模型校验器会先把一个手工指定的 id 拒掉。所以这里不需要第二道
        身份判据（``put_route_rejection`` 需要，因为它的原像漏了三个记账字段）。
        """

    @abstractmethod
    def get_cohort(self, cohort_id: str) -> AccountCohort | None:
        """按 id 取一份。``None`` 只有一个意思：**没有这个 id 的名单**。

        本库属于另一个部署时抛错而不是返回 ``None`` —— 与 ``get_account`` 逐字
        同一条（``workspace`` 是行自己的）。
        """

    @abstractmethod
    def list_cohorts(self) -> tuple[AccountCohort, ...]:
        """本库全部的名单，按 ``(created_at, cohort_id)`` 稳定排序（**早的在前**）。

        排序承重的理由与 ``list_accounts`` 同一条，而这里还多一条：**「当前那一份」
        就是这份序列的最后一个**，所以这个顺序就是「哪一份是当前的」那条判据的定义处。
        实现不许把顺序留给 SQL 的返回序或 dict 的迭代序 —— 否则两次调用会给出两个
        「当前」，而报告上它们都只是「最近的一份名单」。
        """

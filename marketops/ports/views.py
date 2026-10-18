"""工作池那一族的端口 —— **SavedView / Watch / ActionItem** 的存储契约。

三张表（``db.py`` 的 ``_TARGET_DDL``），两个实现（``adapters/sqlite/views.py`` 主存、
``adapters/memory.py`` 测试替身），跑同一份 ``tests/test_views_store_contract.py`` ——
与 ``ports/scenes.py`` / ``ports/pool.py`` / ``ports/review.py`` 是同一条纪律。

## 三个接口住一个模块，而它们是**三个** ABC

同一个理由的两个半边，都在别处已经写过一次：

* **住一起**（``ports/pool.py`` 那段）：它们是同一个工作池的三张面孔 —— 一份视图、
  盯着它的一条监控、以及它产出的那些待办。``run_saved_view`` 要同时读视图与池子，
  ``list_watches`` 要同时读监控与视图，``resolve_action_item`` 处置的正是视图与
  Watch 那些结果落到人身上的那一格。分成三个模块会让「这三张表是一件事」这件事
  在 import 图上消失。
* **三个 ABC**（``ports/pool.py`` 举过的反例）：写它们的**不是同一步**。视图由人
  存（``put_saved_view``），Watch 由人开（**要权限、要预算**），ActionItem 由研究
  结果 / Watch / 人工裁决**产生**，而处置它的是另一条路。合成一个接口会让「只接上了
  视图那一半」变成一种写不出来的状态 —— 而它**必须**写得出来：视图可以在没有任何
  Watch 的情况下存在，那是最正常的一种状态。

## 三张表都不是「当前状态表」，而它们的「当前版」各有各的读法

三张表的主键都是 ``(id, revision)``，而**当前版**都是 ``max(revision)`` —— 但存进去
的东西的**幂等边界**不一样，这是三个接口各自 docstring 里最长的那一段：

* :class:`SavedViewStore` 是**只增版本**（改名要产生新版，历史版本保留当时的名字）。
* :class:`WatchStore` 也是只增版本，而它多一条：``next_due_at`` 那个扁平列**只在
  当前版上有意义** —— 一条历史版本上的到期时刻不是「这一条到不到期」的答案。
* :class:`ActionItemStore` **不是**只增版本：处置是**就地更新**那一行并把 ``revision``
  加一。理由写在 ``domain/action_item.py`` 的模块 docstring 里（那条 partial unique
  index 与 ``(action_id, revision)`` 两条约束推出来的唯一解）。

**这一条差别是本模块最容易被写错的地方**，因为三张表长得几乎一样。一个「反正是
只增版本」的实现在 ActionItem 上会让每一件被处置过的待办**永远留在未结状态里**
（那条索引盖的是 ``state``，而旧的 ``open`` 行还在），于是同一主体上的下一件再也
建不出来 —— 而报告上显示的是「这个账号的待办一直是那一条」。

## 两个实现必须给出**同一个**异常类型

三条纪律在契约里是**异常**，不是返回值：

* ``SavedViewConflictError`` —— ``(workspace, owner, name)`` 上已经有另一条当前版。
* ``OpenActionItemExistsError`` —— 同一主体上的同一类未结待办已经有一条（**插入
  失败**，不是一次查询的结论）。
* ``ActionItemConflictError`` —— 处置的版本不对，或者与当前状态不相容。

两条实现各自造一个自己的异常类是最容易发生的一种分家：契约测试对内存替身写一遍、
对 sqlite 再写一遍，两边都绿，而**服务面只 catch 得到其中一个** —— 它在另一半
部署上变成一次未处理异常（读起来像故障，而不是一条要给人看的拒绝）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from marketops.domain.action_item import ActionItem, ActionItemState
from marketops.domain.saved_view import SavedView
from marketops.domain.watch import Watch

__all__ = [
    "ActionItemStore",
    "ActionItemWrite",
    "SavedViewStore",
    "ViewWrite",
    "WatchStore",
    "WatchWrite",
]


@dataclass(frozen=True)
class ViewWrite:
    """一次视图写入的结果。**两个字段都必要**。

    ``view`` 是**盘上那一版** —— 新写下去的，或者「已经就是你要的这一份」的那一版。
    返回值必须是读回来的那一行，不是入参对象：一个直接回传入参的实现，在「另一次
    写入已经落过同一份内容」这件事上什么也证明不了，而它读起来与真正的幂等一模一样
    （``ports/workstreams.py::WorkStreamStore.put_launch`` 逐字同一条）。

    ``revision_written=None`` 是「**没有新版本落盘**」：盘上当前那一版的内容与这一次
    要写的一字不差。它与 ``revision_written=1``（这一次把它建了出来）在报告里是两句
    不同的话 —— 「你刚才那次成功了」与「你第二次点的时候什么都没发生」。
    """

    view: SavedView
    revision_written: int | None


@dataclass(frozen=True)
class WatchWrite:
    """一次 Watch 写入的结果。两个字段的理由与 :class:`ViewWrite` 逐字相同。"""

    watch: Watch
    revision_written: int | None


@dataclass(frozen=True)
class ActionItemWrite:
    """一次待办写入的结果。

    ``revision_written=None`` 在这里是**重发**：同一次处置第二次提交上来时，盘上那条
    已经是它的结果了（判据见 :meth:`ActionItemStore.resolve`），于是这一次不写。

    它与 :class:`ViewWrite` 那一格不是一回事，理由值得写清：视图那一侧「没有新版本」
    是**内容相同**（改名/改内容都会产生新版），这一侧是**同一次处置被重发**。
    两边的判据都落在各自 store 的写路径上，而它们不共用一行代码 —— 一条「反正都是
    幂等」的合并会让处置的重发判据里漏掉「基于哪一版」那一半。
    """

    item: ActionItem
    revision_written: int | None


class SavedViewStore(ABC):
    """视图的持久化：**只增版本**，而「当前版」是 ``max(revision)``。"""

    @abstractmethod
    def put_view(
        self, view: SavedView, *, expected_revision: int | None
    ) -> ViewWrite:
        """写一份视图。``expected_revision=None`` = **必须不存在**（首次创建），
        否则必须是盘上当前那一版的版本号。

        与 ``SceneStore.put_record`` / ``WorkstreamStore.put_workstream`` 是同一条
        乐观并发纪律，而它们的载体不同，值得写清：这两处是 ``UPDATE ... WHERE
        revision = ?`` 加 ``rowcount``，这里是**插入一个新版本行**，所以它有**两道**
        判据而它们不是重复的：

        * **基准**：盘上当前那一版的版本号必须**正好**是 ``expected_revision``。
          这一条挡的是「读完之后跳了一版直接写回」（``scenes.py`` 那一处逐字：
          「这一条挡的是「改完对象直接写回」」），它会留下一个**空洞** —— 而在那个
          空洞里，一个拿着旧版本的写者会**成功**。
        * **主键**：``(view_id, revision)`` 上的 ``INSERT``。两个写者同时拿着
          ``expected_revision=2`` 时，两边都算出新版本 3、都通过上面那条基准判据，
          而只有一个人的 ``INSERT`` 落得进去。那个主键冲突必须被翻译成一条
          **说得清主语**的拒绝（有人先写了，请重读），不是一次 ``StoreError``。

        两道都写，而**第二条对 SQLite 那一份是冗余的**，这一句如实写在这里而不是
        含糊过去：``BEGIN IMMEDIATE`` 把两次写串起来，于是 B 的读发生在 A 提交之后，
        基准判据已经看得见 A 那一版。它**不**冗余的地方是内存替身（``adapters/
        memory.py`` 那一份**没有锁**，两个线程之间有一个真实的窗口）与将来任何一个
        读-改-写不同的实现。只留主键判据的实现会放行**跳版写回**；只留基准判据的
        实现在内存替身上会放行两条同基数的写。

        ## 名字的判据在**这一次写的事务里**判，而且只判当前版

        ``(workspace, owner, name)`` 上已经有**另一条**当前版时抛
        :class:`~marketops.domain.errors.SavedViewConflictError`，消息里**点名**
        那一条的 ``view_id`` 与 ``revision``。

        三条限定词各自都是一处会写错的地方：

        * **另一条**（``view_id`` 不同）—— 同一条视图改名字当然会与自己重名，
          那不是冲突。
        * **当前版** —— 历史版本会重名，改名正是产生新版的理由（``db.py`` 那一格
          的注释逐字）。**所以判据不是那条 DDL 上的唯一索引**：一条普通的唯一索引
          会把历史版本一起挡掉，于是「改名」这件事在库上**做不成** —— 而那正是
          「改一个视图的名字不撞车」这条用例要的东西。
        * **这一次写的事务里** —— 先读一遍再写一遍的实现在两个写者之间有一个窗口，
          而这个窗口的产物是两条同名的当前版，报告上只显示「视图多了一条」。

        内容一字不差时**不产生新版本**（``revision_written=None``，判据是
        ``SavedView.content_digest``）。名字判据在内容判据**之后**：重发同一份视图
        时不必去查名字 —— 那一条本来就是「它自己」。

        Raises:
            SavedViewConflictError: 三个成因（名字被另一条当前版占着 / 基准版本不对 /
                声称首次创建而它已经在了），见那个异常自己的 docstring。
            StoreError: 盘打不开之类的存储事故（与「盘上那一条不是你以为的那一条」
                是两件事）。
        """
        raise NotImplementedError

    @abstractmethod
    def current(self, view_id: str) -> SavedView | None:
        """这个 ``view_id`` 的**当前版**（``max(revision)``）。没有就是 ``None``。

        ``None`` 是「这个 id 没有过任何一版」，**不是**读失败 —— 而它必须与
        「读到了但内容对不上」分得开，所以这里返回 ``None`` 而不是抛异常。
        """
        raise NotImplementedError

    @abstractmethod
    def views(
        self, *, workspace: str, owner: str | None = None
    ) -> tuple[SavedView, ...]:
        """本工作区的**全部当前版**，按 ``(owner, name, view_id)`` 升序。

        ``workspace`` 是必填关键字（``ports/workstreams.py::workstreams`` 逐字同一条
        理由）：一个可以省略它的签名会让「本工作区的每一个」在调用点悄悄变成「盘上的
        每一个」，而两者在报告上同形。

        **只返回当前版**：``list_saved_views`` 要的是「这个人现在有哪几份」，
        而不是「这个 id 历史上长什么样」。历史版本今天没有任何一个读者 ——
        它们存在的理由是「改名之后旧名字仍然可查」，而那条查询还没有生产者。
        如实记在端口上而不是假装它已经被读着。
        """
        raise NotImplementedError


class WatchStore(ABC):
    """监控规则的持久化：**只增版本**，而 ``next_due_at`` 只在当前版上有意义。"""

    @abstractmethod
    def put_watch(
        self, watch: Watch, *, expected_revision: int | None
    ) -> WatchWrite:
        """写一条 Watch。语义、CAS 载体与三条限定词与 :meth:`SavedViewStore.put_view`
        逐字相同（``expected_revision=None`` = 必须不存在；否则必须是当前版）。

        同名判据**没有**：两条 Watch 可以叫同一个名字 —— 视图的名字是人用来认一条
        **查询**的（所以重名会让人读错那一份），而 Watch 的名字是给人读的一条说明，
        它的身份是 ``watch_id``。

        内容一字不差地重发时**不产生新版本**（``revision_written=None``）：
        同一条规则再写一遍不是一次改动，而一个每次都产生新版本的实现会让
        「有人反复点保存」在盘上留下几十版，于是「这条 Watch 上次改是什么时候」
        读成一个刚刚的时刻。

        Raises:
            StoreError: 存储事故，以及「同一个 watch_id 的同一个版本号已经在了」
                —— 后者是**两个写者**的产物，消息里要点名当前那一版。
        """
        raise NotImplementedError

    @abstractmethod
    def get_watch(self, watch_id: str) -> Watch | None:
        """读**当前版**。没有就是 ``None``。"""
        raise NotImplementedError

    @abstractmethod
    def watches(
        self, *, workspace: str, enabled: bool | None = None
    ) -> tuple[Watch, ...]:
        """本工作区的**全部当前版**，按 ``watch_id`` 升序。

        ``enabled=None`` = 不过滤（两个都回）。这一格**必须**能表达「不要过滤」：
        一个只能回「已启用的」的读法会让「它被关掉了」与「它不在这个工作区里」
        在报告上同形，而 ``list_watches`` 要报的正好是第一句。

        **排序键只有 ``watch_id``**，而按名字排本该更好读。不这么做的理由与
        :meth:`ActionItemStore.items` 那一格逐字同一条：``name`` 在 ``watch`` 上
        **不是扁平列**（它只在 ``payload`` 里），所以 SQL 那一侧排不了它；而一个
        「SQL 排完、Python 再按名字排一遍」的实现会让两半的次序在并列处打架。
        ``watch_id`` 是规范 id，比较是确定的，于是顺序仍是**全序**的。
        """
        raise NotImplementedError

    @abstractmethod
    def due_watches(self, *, workspace: str, at: datetime) -> tuple[Watch, ...]:
        """``enabled`` 且 ``next_due_at <= at`` 的**当前版**，按 ``(next_due_at,
        watch_id)`` 升序。

        单独一个方法，而不是让调用方把 :meth:`watches` 全读出来自己筛 ——
        ``db.py`` 里那个 ``watch_by_due`` 索引与 ``next_due_at`` 那个扁平列存在的
        全部理由就是这一条查询（「有哪些到期了」是唯一会全表扫的那个问题，而它是
        trigger driver 每次醒来都要问的那一个）。让调用方在 Python 里筛，那两个
        东西就都成了摆设，而它们**是**这一格的执行者。

        **只回当前版**：一条历史版本上的 ``next_due_at`` 不是「这一条到不到期」的
        答案 —— 改过之后的 Watch 按新的节奏走，而那条旧行还躺在索引里。

        **这个方法不创建任何东西。** 它答的是「现在有哪些到期了」；把答案变成一次
        Run 的是 ``application/run_admission.admit_run``（``execution_mode=WATCH``），
        而把 Watch 的下一次检查推走的是 ``application/views.py::record_watch_check``。
        这一层不认识 Run、不认识预算、不认识调度 —— 它也**没有被任何人定期调用**：
        全仓唯一的 trigger driver 是 ``schedule_definition`` 那一侧。
        """
        raise NotImplementedError


class ActionItemStore(ABC):
    """待办的持久化。**它不是只增版本**：处置就地更新那一行并把 ``revision`` 加一。

    理由写在 ``domain/action_item.py`` 的模块 docstring 里，一句话的版本是：那条
    partial unique index 盖的是 ``state``，而一条**追加**的 ``done`` 版本会与仍然躺在
    盘上的 ``open`` 版本（另**一行**）一起落在索引里，于是这一件**永远是未结的**。
    """

    @abstractmethod
    def create(self, item: ActionItem) -> ActionItemWrite:
        """把一件**新**待办放进池子。``domain.action_item.require_creatable`` 的三条
        前置条件由域那一侧判（``revision=1`` / ``expected_version=0`` / ``state=open``）。

        ## 重复创建是**插入失败**

        ``(workspace, action_type, scope_ref)`` 上已经有一条**未结**的时抛
        :class:`~marketops.domain.errors.OpenActionItemExistsError`，消息里点名
        **盘上那一条的 action_id**。

        判据落在那条 partial unique index 上，**不是**「写之前先查一下」。两条理由，
        第二条才是关键：

        * 先查后插在两个写者之间有一个窗口：两边都查到「没有未结的」，两边都插，
          而约束只能拦住其中一个 —— 于是第二个写者拿到一次约束异常（在服务面上读起来
          像故障），而他本来该拿到的是一句「已经有一条了，它的 id 是这个」。
        * 更要紧的是**判据住在哪**。一条写在应用代码里的「先查一下」禁用的东西
          比它写出来的少得多：它挡不住另一条路径、另一个进程、或者一次手写的 SQL
          在同一个主体上再插一条。约束挡得住所有四条路。

        **插入失败之后读回盘上那一条**只用来把消息说清楚（点名它的 id），它不参与
        判定 —— 一个「查到了就拒绝」的实现写出来长得一样，而它在两个人的并发下
        会放行两条。

        ## 同一个 ``action_id`` 再写一次

        ``action_id`` 已经有一版、而内容与这一次一字不差时，返回那一版且
        ``revision_written=None``（重发）；内容不同时抛 ``StoreError`` 并点名那个
        ``action_id`` —— **不是**被当成「重名」：同一个 id 配两份内容是一次真的冲突
        （有人拿同一个 id 写了别的一件事），而它必须停下来。

        Raises:
            OpenActionItemExistsError: 同一主体同一类的未结待办已经有一条。
            StoreError: 存储事故，或同一个 ``action_id`` 配了另一份内容。
        """
        raise NotImplementedError

    @abstractmethod
    def resolve(self, item: ActionItem, *, expected_version: int) -> ActionItemWrite:
        """处置一件待办。判据是 ``WHERE action_id = ? AND revision = ?`` 加
        ``rowcount``（``expected_version`` 就是那个 ``?``），与 ``SceneStore.put_record``
        逐字同一条纪律。

        ## 三条出口，各自的报文必须分得开

        * 盘上没有这个 ``action_id`` ⇒ ``ActionItemConflictError``，点名这个 id
          （「去看是不是抄错了 id」）。
        * 盘上有、而它的 ``revision`` 正好是 ``expected_version`` ⇒ 写；
          新的一行的 ``revision`` 是 ``expected_version + 1``，``expected_version``
          那一格记下这次写的基点。
        * 盘上有、``revision`` 不是 ``expected_version`` ⇒ 分两种：
          - **重发**：盘上那一行的 ``expected_version`` 与这一次相同，**且**它的
            内容摘要（``ActionItem.disposition_digest``）与这一次相同 ⇒ 返回盘上
            那一行，``revision_written=None``。这就是「重发同一次处置是幂等的」
            （``catalog.py`` 里 ``resolve_action_item`` 的 summary 逐字）。
          - 否则 ⇒ ``ActionItemConflictError``，消息里**同时**有期望版本、盘上那一版、
            以及当前状态（「自己太慢」与「对面在打架」的处置不同）。

        重发判据的两个半边**都要有**，缺一不可：只看内容摘要会放行「另一个版本的
        同一次处置」（而那一次是**基于另一版**做的决定，它不能顺理成章地盖在这一版
        上）；只看版本会让「同一版上两次不同的处置」被当成重发（第二个人写的那些字
        就此消失，而盘上只留第一个人的）。

        Raises:
            ActionItemConflictError: 见上。
            StoreError: 存储事故。
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, action_id: str) -> ActionItem | None:
        """读一件（**当前版** = ``max(revision)``）。没有就是 ``None``。"""
        raise NotImplementedError

    @abstractmethod
    def items(
        self,
        *,
        workspace: str,
        state: ActionItemState | None = None,
        owner: str | None = None,
        open_only: bool = False,
    ) -> tuple[ActionItem, ...]:
        """本工作区的待办，**未结的在前**；同组内按 ``(due_at 空值往后,
        due_at, action_id)`` 升序。

        顺序是契约的一部分（``ReviewCaseStore.list_cases`` 逐字同一条）：报告的第一行
        必须是「现在最该看的那一件」，而一个由存储层随便给的顺序会让两次调用给出两个
        「第一件」。

        **排序键里没有 ``created_at``**，而它本该在那里（``list_cases`` 用的就是
        ``opened_at``）。原因是：``created_at`` 在 ``action_item`` 上**不是扁平列**
        （``db.py`` 那一格的注释逐字：扁平列只给「要用它查」的字段），所以 SQL 那一侧
        排不了它；而一个「SQL 排完、Python 再排一遍」的实现会让两半的次序在并列处打架。
        第三根键用 ``action_id`` 补上（它是规范 id，比较是确定的），于是顺序仍然是
        **全序**的 —— 这一条是它必须被写进契约的理由，不是风格。

        ``open_only=True`` 与 ``state=...`` 的差别是**过滤的是哪一个集合**：
        ``open_only`` 用 ``OPEN_STATES``（未结的那三个），``state`` 用调用方点名的
        那一个。两个都给时按 ``state`` —— 一个点名了具体状态的人不该拿到另外两个。

        ``owner`` 是**真的进过滤**的那一格（``catalog.py`` 的 ``list_action_items``
        逐字「可按负责人与状态筛」），而它的档位与 ``watches(enabled=...)`` 同一条：
        ``owner=None`` = **不过滤**。收了参数而不筛的实现在报告上与筛过的一模一样 ——
        「这个人的待办」与「所有人的待办」是两句不同的话，而后者长得更像「事情变多了」。
        **没有主人的那几件今天没有入口**（``owner IS NULL`` 那一问）：它要一个第三种
        档位，而今天没有任何一个读者需要它，所以不在这里先写出来。
        """
        raise NotImplementedError

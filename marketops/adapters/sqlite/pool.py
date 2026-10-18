"""SQLite 池子这一族的四张表：**主存**（``ports/pool.py`` 的落点）。

前三个类（资产池 / 责任关系表 / 账号池）住同一个模块，与那三个 ABC 住同一个端口模块是
同一条理由：它们的幂等边界相同（累积并修订）、落盘方式相同（一半是列、一半是
``payload``）、对事务边界的要求相同。**键的形状各不相同**（一行一个句柄 / 四段拼出来
的键 / 一个 id 加一格血统），而三张表的类文档各写了各的那一份理由。

第四个类（冻结成员名单、§B12）落在这里是**因为它是这一族里的第四张表**，不是因为
它遵守同一条规矩 —— 恰好相反：它不折、它覆盖，而那个「折」正是这里前三张表的共同
理由。第四张表为什么不共享那条边界，写在 ``ports/pool.py`` 的模块 docstring 里；
这一个模块要记住的只有一件事：前三张表的合并规则由构造参数接进来、读旧行与写新行
必须在同一个事务里，而**第四张表没有合并规则可接**（唯一会变的一格是 ``created_at``,
取更早的那一个，一个就地 ``min``，没有第二种折法可以让人选错）。

## 一行怎么落成两半

``venue_asset`` 表里一半是**列**、一半是 ``payload`` 里的 JSON：

* 列里的是**查询得到的那几格** —— ``venue_asset_id``（主键，新行按它覆盖）、
  ``workspace``（血统，读的时候要拿它跟库绑定比）、``source_identity``
  （``osm:node/123`` / ``site:<主机>``，母池那边来的行要能按它找回原行）。
* 其余全在 ``payload`` 里，与 ``scene`` / ``evidence`` 同一个理由：把契约字段摊平成
  列等于把契约钉死在这个存储上，而契约是 domain 的事（它有独立的 JSON Schema，
  见 ``marketops/schema.py`` 与 ``contracts/``）。

## 折，不是只增，也不是覆盖

这个端口与 ``EvidenceStore``（只增、同 id 不同内容就报错）的边界**不一样**，理由
写在 ``ports/pool.py`` 的模块 docstring 里：同一块场地会被反复看到，每次都是一行新
的观察，而它还是同一块场地。所以落盘是 ``ON CONFLICT DO UPDATE``。

**「哪些格子不许被覆盖抹掉」不在这里。** 一条重新导入的原始行带着
``domain_verdict=unknown``，直接覆盖会把上一次证得的官网抹掉 —— 但那是**业务规则**
（合并策略），它住在 ``application/pool.py``，由构造参数接进来（``AssetMerge``），
并且在那里被单独测出来。存储负责的是另一件**它才能负责**的事：把「读旧行」与
「写新行」放进同一个 ``BEGIN IMMEDIATE`` 事务里。

那一条不是洁癖。两个 worker 同时把各自的观察折进同一块场地时，
「先 SELECT 再 UPDATE」的两者都读到同一份旧行、各自算出新行、后写的那一次把前一次
带进来的观察整个吃掉 —— 而它的表现不是报错，是**一次没人看得见的丢失**，
且它只在两个 worker **重叠**的时候发生（§7 的运行形态正是重叠的）。
``application/pool.py`` 里做这件事看起来更自然，但业务层拿不到事务边界：
`upsert_asset` 那一对读写必须落在同一个事务里，而事务是这一层的东西。
"""

from __future__ import annotations

from pathlib import Path

from ...domain.account import MarketAccount
from ...domain.cohort import AccountCohort
from ...domain.entities import AccountVenueRelation, VenueAsset
from ...domain.errors import StoreError
from ...domain.fold import FoldAttribution
from ...domain.util import utc_now
from ...ports.pool import (
    AccountCohortStore,
    AccountMerge,
    AccountPoolStore,
    AccountVenueRelationStore,
    AssetMerge,
    RelationMerge,
    VenueAssetStore,
)
from .db import (
    bind_workspace,
    ensure_schema,
    owned,
    session,
    workspace_binding,
    write_transaction,
)
from .payload import dump_payload as _dump
from .payload import load_payload as _load
from .runs import record_fold


class SqliteVenueAssetStore(VenueAssetStore):
    """基于 SQLite 的场地资产池。

    构造参数是 ``db_path`` 而不是 ``Settings``：与另外几个 store 同一个理由 ——
    存储要能在没有配置、没有 ``os.environ`` 的情况下对着一个临时文件被测出来。

    建表在 ``__init__`` 里做（``ensure_schema`` 幂等）。``CREATE TABLE IF NOT EXISTS``
    是自愈的：**加一张表不需要动 schema 版本号，也不需要一次迁移** ——
    ``db.py`` 的「DDL 只增不改」换来的正是这件事。
    """

    def __init__(self, db_path: Path, merge: AssetMerge) -> None:
        self._path = db_path
        self._merge = merge
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    def upsert_asset(
        self, incoming: VenueAsset, *, fold: FoldAttribution | None = None
    ) -> VenueAsset:
        """绑 + 读旧行 + 合并 + 覆写在**同一个写事务**里。

        顺序是承重的：``BEGIN IMMEDIATE`` 在事务开始就拿写锁（``db.py`` 的模块
        docstring），所以下面那次 ``SELECT`` 与随后的 ``INSERT`` 之间没有别人能插进来。
        写成「事务外先读、再开事务写」看起来一样，而它正好丢掉了这一条。

        ## ``fold`` 落在这里，而不是在调用方那一层拼接

        给了归属时，这一笔账由 :func:`~marketops.adapters.sqlite.runs.record_fold` 在
        **同一个写事务里**写下 —— 就在写 ``venue_asset`` 那条 INSERT 之后、``COMMIT``
        之前。于是它满足 §B17 条件 2 要的那条原子性，而且**不是靠调用方守规矩**：
        池子那一行与那一笔账同生共死，核不过整笔回滚，池子一个字节都不写。

        做不到的是另一种拼法：由 ``promote_receipt`` 先调本方法、再单独调一次 Run
        那一侧的记账。那两次各自开事务，中间那一小段里「池子变了而账上没记」是一个
        **真状态**，而它恰好就是这条账要发现的那件事 —— 一次失败会把假账留在盘上。

        ``record_fold`` 的三条判定（账本在不在 / 这一轮结束没有 / 是不是重放）都在
        这里被调用，所以它们的次序就是本方法的次序：在 ``venue_asset`` 那条 INSERT
        **之后**。这不要紧 —— 整笔在同一个事务里，任何一条抛出去都回滚到调用前。
        放在后面是为了**只有一个调用点**：一个在事务开头另写一遍账本检查的版本，
        会和 ``record_fold`` 里那三条分家。
        """
        with session(self._path) as conn, write_transaction(conn):
            # 池子这一侧按 ``StoreError`` 分支，所以绑定的拒绝也用同一个类型
            # （判词仍是 db.py 里那一份）—— 与 ``get_asset`` 走的 ``owned`` 默认值一致。
            bind_workspace(conn, incoming.workspace, error=StoreError)
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM venue_asset WHERE venue_asset_id = ?",
                (incoming.venue_asset_id,),
            ).fetchone()
            existing = (
                None
                if row is None
                else VenueAsset.model_validate(
                    owned(
                        _load(row["payload"], incoming.venue_asset_id),
                        where=f"venue_asset[{incoming.venue_asset_id}]",
                        bound=bound,
                    )
                )
            )
            merged = self._merge(existing, incoming)
            now = utc_now()
            conn.execute(
                "INSERT INTO venue_asset "
                "(venue_asset_id, workspace, source_identity, payload, stored_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (venue_asset_id) DO UPDATE SET "
                "workspace = excluded.workspace, "
                "source_identity = excluded.source_identity, "
                "payload = excluded.payload, "
                "stored_at = excluded.stored_at",
                (
                    merged.venue_asset_id,
                    merged.workspace,
                    merged.source_identity,
                    _dump(merged.model_dump(mode="json")),
                    now.isoformat(),
                ),
            )
            if fold is not None:
                # 与 ``stored_at`` 同一个时钟读数：这一笔账说的是「这一行是什么时候
                # 折进来的」，而那一行就是上面这条 INSERT 写下的那一行。取两次
                # ``utc_now()`` 会让回执上的时刻与它记账的那一行差一点点 —— 那一点
                # 差别没有意义，但不该由一次偶然的调用顺序产生。
                record_fold(conn, fold, venue_asset_id=merged.venue_asset_id, at=now)
        return merged

    def get_asset(self, venue_asset_id: str) -> VenueAsset | None:
        """按 id 取。``None`` 只有一个意思：**没有这个 id 的行**。

        这一行存在但不属于本库时抛 ``StoreError``（``db.owned``），不是 ``None`` ——
        那两个答案的处置不同（一个是「去建这一行」，一个是「你问错库了」）。
        """
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM venue_asset WHERE venue_asset_id = ?",
                (venue_asset_id,),
            ).fetchone()
        if row is None:
            return None
        return VenueAsset.model_validate(
            owned(
                _load(row["payload"], venue_asset_id),
                where=f"venue_asset[{venue_asset_id}]",
                bound=bound,
            )
        )

    def list_assets(self) -> tuple[VenueAsset, ...]:
        """按 ``venue_asset_id`` 排序 —— 顺序是承重的，见 ``ports/pool.py``。"""
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT venue_asset_id, payload FROM venue_asset ORDER BY venue_asset_id"
            ).fetchall()
        return tuple(
            VenueAsset.model_validate(
                owned(
                    _load(row["payload"], row["venue_asset_id"]),
                    where=f"venue_asset[{row['venue_asset_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )


class SqliteAccountVenueRelationStore(AccountVenueRelationStore):
    """基于 SQLite 的责任关系表。

    与 :class:`SqliteVenueAssetStore` 逐条同构：``db_path`` 而不是 ``Settings``
    （存储要能对着一个临时文件被测出来）、建表在 ``__init__`` 里做
    （``ensure_schema`` 幂等，加表不需要迁移）、合并规则由构造参数接进来。

    ## 三个读都**在 Python 里**排序，不靠 SQL 的 ``ORDER BY``

    与资产池那一份不同，这里刻意不用 ``ORDER BY``：顺序的定义处只有
    ``AccountVenueRelation.relation_key()``，而 SQL 里的 ``ORDER BY a, b, c`` 是**第二
    份**同一个顺序的写法 —— 两份会在某一天按不同的列序漂开（例如 `relation` 那一列
    的排序在 SQLite 与 Python 上对非 ASCII 取值并没有同一套规则），而两个实现在同一
    批行上给出不同的顺序，是共享契约里唯一一条**互相钉**的判据。
    """

    def __init__(self, db_path: Path, merge: RelationMerge) -> None:
        self._path = db_path
        self._merge = merge
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    def upsert_relation(self, incoming: AccountVenueRelation) -> AccountVenueRelation:
        """绑 + 读旧行 + 合并 + 覆写在**同一个写事务**里（理由同资产池那一份）。"""
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, incoming.workspace, error=StoreError)
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM account_venue_relation "
                "WHERE workspace = ? AND account_id = ? AND venue_asset_id = ? "
                "AND relation = ?",
                (
                    incoming.workspace,
                    incoming.account_id,
                    incoming.venue_asset_id,
                    incoming.relation.value,
                ),
            ).fetchone()
            existing = (
                None
                if row is None
                else AccountVenueRelation.model_validate(
                    owned(
                        _load(row["payload"], incoming.relation_key()),
                        where=f"account_venue_relation[{incoming.relation_key()}]",
                        bound=bound,
                    )
                )
            )
            merged = self._merge(existing, incoming)
            conn.execute(
                "INSERT INTO account_venue_relation "
                "(workspace, account_id, venue_asset_id, relation, payload, stored_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (workspace, account_id, venue_asset_id, relation) DO UPDATE SET "
                "payload = excluded.payload, "
                "stored_at = excluded.stored_at",
                (
                    merged.workspace,
                    merged.account_id,
                    merged.venue_asset_id,
                    merged.relation.value,
                    _dump(merged.model_dump(mode="json")),
                    utc_now().isoformat(),
                ),
            )
        return merged

    def relations_for_account(self, account_id: str) -> tuple[AccountVenueRelation, ...]:
        """按账号读。一个不负责任何场地的账号给出空元组，不是错误。"""
        return self._read("account_id = ?", (account_id,))

    def relations_for_asset(self, venue_asset_id: str) -> tuple[AccountVenueRelation, ...]:
        """按场地读 —— §5 的那一个方向。"""
        return self._read("venue_asset_id = ?", (venue_asset_id,))

    def list_relations(self) -> tuple[AccountVenueRelation, ...]:
        """全部行。排序在 Python 里做，见类文档。"""
        return self._read(None, ())

    # ------------------------------------------------------------------
    def _read(
        self, where: str | None, params: tuple[str, ...]
    ) -> tuple[AccountVenueRelation, ...]:
        """一条 ``SELECT`` + 绑定核对 + 排序。三个读共用它，免得三处各写一遍绑定判据。

        ``workspace`` 那一段由**库绑定**负责（不在这里加条件）：一个库装一个工作区，
        而 ``bind_workspace`` 不让第二个进来 —— 在 SQL 里再写一遍 ``workspace = ?``
        会让「这个库是谁的」有两个定义处，而读路径也拿不到调用方的 workspace
        （它是行自己的，不是参数的）。

        行名（``owned`` 与 ``_load`` 报错时用的定位串）取自**列**而不是 payload：
        payload 解不出来时（非法 JSON）报错要能指向那一行，而那时还没有 payload 可读。
        """
        sql = (
            "SELECT workspace, account_id, venue_asset_id, relation, payload "
            "FROM account_venue_relation"
        )
        if where:
            sql += f" WHERE {where}"
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(sql, params).fetchall()
        loaded: list[AccountVenueRelation] = []
        for row in rows:
            label = (
                f"account_venue_relation[{row['workspace']}/{row['account_id']}"
                f"/{row['venue_asset_id']}/{row['relation']}]"
            )
            loaded.append(
                AccountVenueRelation.model_validate(
                    owned(_load(row["payload"], label), where=label, bound=bound)
                )
            )
        return tuple(sorted(loaded, key=lambda relation: relation.relation_key()))


class SqliteAccountPoolStore(AccountPoolStore):
    """基于 SQLite 的账号池（§6）。

    与上面两个逐条同构：``db_path`` 而不是 ``Settings``（存储要能对着一个临时文件被
    测出来）、建表在 ``__init__`` 里做（``ensure_schema`` 幂等，加表不需要迁移）、
    合并规则由构造参数接进来、读旧行与写新行在同一个 ``BEGIN IMMEDIATE`` 事务里。

    ## 排序用 SQL 的 ``ORDER BY``，而关系表那一份刻意不用

    这不是前后不一致，是两张表的键不一样：账号的身份是**一列**（``account_id``），
    而关系表的键是四段拼出来的顺序（``relation_key()`` 是那个顺序的唯一定义处）。
    一列的 ``ORDER BY`` 与 Python 的 ``sorted`` 在 TEXT 上给出同一个顺序（SQLite 的
    默认 BINARY 排序就是字节序，而 UTF-8 的字节序与码点序一致），所以这里没有
    「同一个顺序的第二份写法」这种东西 —— 那一份只会在有多段、有分隔符、要自己定
    先后的时候才出现。资产池那一份用的是同一个写法，理由相同。

    ## 它只有两个读，而这是 §6 那一半的落点

    没有按域名/名字/地址找账号的入口 —— 见 ``ports/pool.py::AccountPoolStore`` 的
    类文档。下面那两条 ``SELECT`` 是这一层通往这一张表的**全部**路径。
    """

    def __init__(self, db_path: Path, merge: AccountMerge) -> None:
        self._path = db_path
        self._merge = merge
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    def upsert_account(self, incoming: MarketAccount) -> MarketAccount:
        """绑 + 读旧行 + 合并 + 覆写在**同一个写事务**里（理由同上面两份）。"""
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, incoming.workspace, error=StoreError)
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM market_account WHERE account_id = ?",
                (incoming.account_id,),
            ).fetchone()
            existing = (
                None
                if row is None
                else MarketAccount.model_validate(
                    owned(
                        _load(row["payload"], incoming.account_id),
                        where=f"market_account[{incoming.account_id}]",
                        bound=bound,
                    )
                )
            )
            merged = self._merge(existing, incoming)
            conn.execute(
                "INSERT INTO market_account (account_id, workspace, payload, stored_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT (account_id) DO UPDATE SET "
                "workspace = excluded.workspace, "
                "payload = excluded.payload, "
                "stored_at = excluded.stored_at",
                (
                    merged.account_id,
                    merged.workspace,
                    _dump(merged.model_dump(mode="json")),
                    utc_now().isoformat(),
                ),
            )
        return merged

    def get_account(self, account_id: str) -> MarketAccount | None:
        """按 id 取。``None`` 只有一个意思：**没有这个 id 的行**。

        这一行存在但不属于本库时抛 ``StoreError``（``db.owned``），不是 ``None`` ——
        两个答案的处置不同（一个是「去写这一行」，一个是「你问错库了」），而 §6 的
        「同一个账号在两个 Scene 下是两行」正是靠这一条守住的：把别的 Scene 的行读回来
        会让「复用客观事实」变成「两行共用一行」。
        """
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM market_account WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        return MarketAccount.model_validate(
            owned(
                _load(row["payload"], account_id),
                where=f"market_account[{account_id}]",
                bound=bound,
            )
        )

    def list_accounts(self) -> tuple[MarketAccount, ...]:
        """按 ``account_id`` 排序 —— 顺序是承重的，见 ``ports/pool.py``。"""
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT account_id, payload FROM market_account ORDER BY account_id"
            ).fetchall()
        return tuple(
            MarketAccount.model_validate(
                owned(
                    _load(row["payload"], row["account_id"]),
                    where=f"market_account[{row['account_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )


class SqliteAccountCohortStore(AccountCohortStore):
    """基于 SQLite 的冻结成员名单（§B12）。**第四张表，不是账号池上的第四个方法。**

    构造参数只有 ``db_path``：与另外三个不同，**没有合并规则**。理由是这一族里唯一
    的那条分界线（``ports/pool.py`` 的模块 docstring）：前三张表的写是一次「读—改—
    写」，所以规则必须由构造参数接进来、而那一对读写必须落在同一个事务里；这里的写
    **不读旧行**——一份名单的内容完全由 ``cohort_id`` 决定（那个 id 是内容寻址的），
    所以「怎么折」这个问题在这一张表上不存在。

    ``created_at`` 是唯一的例外，而它不需要一条规则：取更早的那一个，就地一个
    ``min``（与 ``VenueAsset.first_seen_at`` 逐字同一条，``application/merge.py``
    的模块 docstring 里写了为什么那一半不需要一个 ``earlier_of``）。所以它**没有**
    第二种折法可以让人选错。

    ## 它为什么仍然要一个写事务

    即使没有读—改—写，``INSERT ... ON CONFLICT DO UPDATE`` 与 ``bind_workspace``
    必须在同一个事务里：绑定的那一次写与这一行如果分开提交，中间失败会留下一个
    「绑了工作区、名单没写进去」的库 —— 那不是一次数据丢失（重放会补上），而是
    **第一次写入的绑定来源变了一次**，而那正是 ``db.py`` 里那句「绑定取值只有两个
    来源，两者都拒绝猜」要守的事。
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        with session(self._path) as conn:
            ensure_schema(conn)

    # ------------------------------------------------------------------
    def put_cohort(self, cohort: AccountCohort) -> AccountCohort:
        """把这份名单写进去，返回**落盘后的那一份**。

        ``created_at`` 取更早的那一个：重放一份旧名单不该把它推后 —— 推后之后，
        一份早就冻好的名单会声称自己是刚冻的，而 §B22 的复用判定看的正是这个时刻。
        所以先在同一事务里读一眼已有的那一行（这一步是这一张表上唯一的读—改—写，
        而它改的只有一格）。
        """
        with session(self._path) as conn, write_transaction(conn):
            bind_workspace(conn, cohort.workspace, error=StoreError)
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM account_cohort WHERE cohort_id = ?",
                (cohort.cohort_id,),
            ).fetchone()
            stored = cohort
            if row is not None:
                existing = AccountCohort.model_validate(
                    owned(
                        _load(row["payload"], cohort.cohort_id),
                        where=f"account_cohort[{cohort.cohort_id}]",
                        bound=bound,
                    )
                )
                if existing.created_at < cohort.created_at:
                    stored = cohort.model_copy(update={"created_at": existing.created_at})
            conn.execute(
                "INSERT INTO account_cohort "
                "(cohort_id, workspace, created_at, payload, stored_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (cohort_id) DO UPDATE SET "
                "created_at = excluded.created_at, "
                "payload = excluded.payload, "
                "stored_at = excluded.stored_at",
                (
                    stored.cohort_id,
                    stored.workspace,
                    stored.created_at.isoformat(),
                    _dump(stored.model_dump(mode="json")),
                    utc_now().isoformat(),
                ),
            )
        return stored

    def get_cohort(self, cohort_id: str) -> AccountCohort | None:
        """按 id 取一份。``None`` 只有一个意思：**没有这个 id 的名单**。"""
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            row = conn.execute(
                "SELECT payload FROM account_cohort WHERE cohort_id = ?",
                (cohort_id,),
            ).fetchone()
        if row is None:
            return None
        return AccountCohort.model_validate(
            owned(
                _load(row["payload"], cohort_id),
                where=f"account_cohort[{cohort_id}]",
                bound=bound,
            )
        )

    def list_cohorts(self) -> tuple[AccountCohort, ...]:
        """按 ``(created_at, cohort_id)`` 排序，**早的在前** —— 见 ``ports/pool.py``。

        ``cohort_id`` 是第二格而不是省略掉：``created_at`` 是秒级以下的 ISO 字符串，
        两份名单在同一微秒里冻好是可能的（一次 tick 里对两个场景各读一次），而
        ``ORDER BY`` 在一个并列的键上给出的顺序**由实现决定**。第二格把它钉死 ——
        不然「哪一份是当前的」会在两次调用之间换一个答案，而两份报告都自洽。
        """
        with session(self._path) as conn:
            bound = workspace_binding(conn)
            rows = conn.execute(
                "SELECT cohort_id, payload FROM account_cohort "
                "ORDER BY created_at, cohort_id"
            ).fetchall()
        return tuple(
            AccountCohort.model_validate(
                owned(
                    _load(row["payload"], row["cohort_id"]),
                    where=f"account_cohort[{row['cohort_id']}]",
                    bound=bound,
                )
            )
            for row in rows
        )


__all__ = [
    "SqliteAccountCohortStore",
    "SqliteAccountPoolStore",
    "SqliteAccountVenueRelationStore",
    "SqliteVenueAssetStore",
]

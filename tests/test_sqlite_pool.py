"""``SqliteVenueAssetStore`` 与 ``SqliteAccountVenueRelationStore`` 各自只对自己成立的判据。

共享契约（``tests/test_asset_store_contract.py`` 与 ``tests/test_relation_store_contract.py``）
对两个实现一起跑，而下面这几条**按定义**写不进那两份：

1. 「写完重开一个 store 还能读到」—— 内存实现做不到，与另外两份契约测试同一条处置。
2. 「库里的池子行没被任何东西认领时，这个库不被打开」—— 那是 ``ensure_schema`` 的
   判据，内存实现没有 ``ensure_schema``。

第 2 条同时是 ``db.OWNED_TABLES`` 这份清单的**唯一执行器**：``ensure_schema`` 问的是
「有行却是谁的没答案」，而少列一张表就等于给那张表开一条「混装之后没人发现」的路。
把这一条测在别处（比如只测 ``evidence`` 那一张），那份清单里加没加 ``venue_asset``
就不会有人知道 —— 一个**永不触发的检查**。责任关系表是同一张清单上的第二格，所以它
有第二条同样形状的判据（各测各的，理由见那一条自己的 docstring）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.sqlite.db import (
    OWNED_TABLES,
    WORKSPACE_META_KEY,
    session,
    write_transaction,
)
from marketops.adapters.sqlite.pool import (
    SqliteAccountPoolStore,
    SqliteAccountVenueRelationStore,
    SqliteVenueAssetStore,
)
from marketops.application.accounts import merge_accounts
from marketops.application.pool import merge_assets
from marketops.application.relations import merge_relations
from marketops.config import Settings
from marketops.domain.account import MarketAccount
from marketops.domain.entities import AccountVenueRelation, VenueAsset
from marketops.domain.enums import AccountStatus, VenueRelation
from marketops.domain.errors import StoreError
from marketops.interfaces.composition import (
    default_account_pool_store,
    default_asset_store,
    default_relation_store,
)

UTC = timezone.utc
T0 = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
EARLIER = T0 - timedelta(days=30)
WORKSPACE = "uk-acme-natural-grass"


def _store(db_path: Path) -> SqliteVenueAssetStore:
    return SqliteVenueAssetStore(db_path, merge_assets)


def _asset(**overrides) -> VenueAsset:
    payload = {
        "workspace": WORKSPACE,
        "venue_asset_id": "v1",
        "name": "Main Pitch",
        "first_seen_at": T0,
    }
    payload.update(overrides)
    return VenueAsset(**payload)


def _relation_store(db_path: Path) -> SqliteAccountVenueRelationStore:
    return SqliteAccountVenueRelationStore(db_path, merge_relations)


def _account_store(db_path: Path) -> SqliteAccountPoolStore:
    return SqliteAccountPoolStore(db_path, merge_accounts)


def _account(**overrides) -> MarketAccount:
    payload = {
        "workspace": WORKSPACE,
        "account_id": "acct-1",
        "organisation": "Example Parish Council",
        "evidence_ids": ["ev-1"],
        "first_seen_at": T0,
    }
    payload.update(overrides)
    return MarketAccount(**payload)


def _relation(**overrides) -> AccountVenueRelation:
    payload = {
        "workspace": WORKSPACE,
        "account_id": "acct-1",
        "venue_asset_id": "v1",
        "relation": VenueRelation.MAINTAINS,
        "evidence_ids": ["ev-1"],
        "first_seen_at": T0,
    }
    payload.update(overrides)
    return AccountVenueRelation(**payload)


def test_the_pool_table_is_a_table_the_binding_rule_looks_at() -> None:
    """``venue_asset`` 必须在 ``OWNED_TABLES`` 里，而这一条钉的是**清单本身**。

    下面那条判据会被触发的唯一条件是这张表在清单里；写成「两个断言都在一条测试里」
    是为了让它的红直接指出该改哪里（清单里少一个名字），而不是让人去猜
    「为什么一条与池子无关的判据红了」。
    """
    assert "venue_asset" in OWNED_TABLES


def test_a_row_survives_reopening_the_store(tmp_path: Path) -> None:
    """**这是为什么池子是持久的**：跨运行留下的东西必须真的跨过进程边界。

    用第二个实例读、而不是用同一个实例，是这条判据的全部要点 —— 一个把行留给自己
    那份内存字典的实现会通过任何单实例的判据。
    """
    db_path = tmp_path / "state" / "marketops.db"
    _store(db_path).upsert_asset(_asset(notes="written by the first process"))

    reopened = _store(db_path)
    row = reopened.get_asset("v1")
    assert row is not None
    assert row.notes == "written by the first process"
    assert row.first_seen_at == T0


def test_a_pool_row_in_a_database_nobody_claims_is_refused_on_open(tmp_path: Path) -> None:
    """有池子行、却没有工作区绑定的库**不被认领** —— 在**打开时**就拒绝。

    这个状态写路径构造不出来（第一行写入就会绑，且绑定与那一行在同一个写事务里），
    所以它只可能来自一次手工修改或一次没做完的操作。不认领它的理由是那句话本身：
    「这些行属于谁」没有答案 —— 而按「当前部署」去读，就是把别人的事实读成自己的。

    **这条判据是 ``db.OWNED_TABLES`` 里 ``venue_asset`` 那一格的唯一执行器。**
    清单里少列这张表时，下面这次构造会**成功**，而一个混装过的库从此可以被一路
    打开到第一次读。
    """
    db_path = tmp_path / "state" / "marketops.db"
    _store(db_path).upsert_asset(_asset())
    with session(db_path) as conn, write_transaction(conn):
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))

    with pytest.raises(StoreError, match="没有工作区绑定"):
        _store(db_path)


def test_a_row_of_an_unclaimed_database_is_refused_on_read_too(tmp_path: Path) -> None:
    """读那一行时**也**拒绝（``db.owned`` 的 ``bound is None`` 那一支）。

    与上面那条是两个红点：那条在 ``ensure_schema`` 里，这条在 ``get_asset`` 里。
    只有前者的话，任何**已经在跑**的进程（它的实例早就构造好了）会继续用那个库；
    只有后者的话，一个手工改过的库会一路开到第一次读才发现。

    「已经在跑的实例」正是这里的构造方式：先建好 store（那时绑定还在），再手工把
    绑定删掉，然后**用同一个实例**读 —— 读路径不开 ``ensure_schema``，所以它真的
    会走到 ``db.owned``。写成「再构造一个实例」测的是上面那一条。
    """
    db_path = tmp_path / "state" / "marketops.db"
    store = _store(db_path)
    store.upsert_asset(_asset())
    with session(db_path) as conn, write_transaction(conn):
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))

    with pytest.raises(StoreError, match="没有工作区绑定"):
        store.get_asset("v1")
    with pytest.raises(StoreError, match="没有工作区绑定"):
        store.list_assets()


# --- 组合根那两个说法 ----------------------------------------------------------


def test_the_default_factory_is_lazy_about_creating_the_database(settings: Settings) -> None:
    """``default_asset_store`` 的 docstring 声称「自检那一条路不建库」—— 这一条钉它。

    组合根的契约是「构造服务面需要的一切，**不绑端口、不建运行目录**」。在组合阶段
    就建库，会让一次 ``--check`` 自检留下副作用，而自检本该是只读的。
    """
    factory = default_asset_store(settings)
    assert not settings.database_path.is_file(), (
        "工厂本身不该建库 —— 建库在第一次真的读写池子的时候"
    )
    store = factory()
    assert isinstance(store, SqliteVenueAssetStore)
    assert settings.database_path.is_file(), "建出来的 store 才该建库"


def test_the_default_factory_uses_a_real_merge_rule(settings: Settings) -> None:
    """组合根接进去的是 ``application.pool.merge_assets`` —— 不是一条占位规则。

    判据用的是那条规则**唯一一处报错**：两次矛盾的判定。一个接错规则的 store 在
    「折一行读一行」上完全正常，只有拿一条真判据去问它，才知道接的是哪一条。
    """
    from marketops.domain.enums import DomainVerdict
    from marketops.domain.errors import AssetConflictError

    store = default_asset_store(settings)()
    settled = dict(
        workspace=WORKSPACE,
        venue_asset_id="v1",
        name="Main Pitch",
        first_seen_at=T0,
        domain_evidence_ids=["ev-1"],
    )
    store.upsert_asset(
        VenueAsset(
            **settled,
            domain_verdict=DomainVerdict.CONFIRMED,
            verified_official_domain="a.example",
            verified_official_domains=["a.example"],
        )
    )
    with pytest.raises(AssetConflictError):
        store.upsert_asset(
            VenueAsset(
                **settled,
                domain_verdict=DomainVerdict.CONFIRMED,
                verified_official_domain="b.example",
                verified_official_domains=["b.example"],
            )
        )


# --- 责任关系表：同一组判据的第二份 --------------------------------------------


def test_the_relation_table_is_a_table_the_binding_rule_looks_at() -> None:
    """``account_venue_relation`` 必须在 ``OWNED_TABLES`` 里。

    与资产池那一条逐字同形，而且是**各测各的**：清单里加表是逐张加的，一条判据只看
    它自己那张表在不在 —— 「资产池在清单里」这一件事对关系表一个字都没说。
    """
    assert "account_venue_relation" in OWNED_TABLES


def test_a_relation_survives_reopening_the_store(tmp_path: Path) -> None:
    """跨运行留下的东西必须真的跨过进程边界（用第二个实例读）。"""
    db_path = tmp_path / "state" / "marketops.db"
    _relation_store(db_path).upsert_relation(
        _relation(evidence_ids=["ev-1", "ev-2"], last_verified_at=T0)
    )

    reopened = _relation_store(db_path)
    (row,) = reopened.relations_for_asset("v1")
    assert row.evidence_ids == ["ev-1", "ev-2"]
    assert row.first_seen_at == T0
    assert row.last_verified_at == T0
    assert row.relation is VenueRelation.MAINTAINS


def test_a_relation_row_in_a_database_nobody_claims_is_refused_on_open(tmp_path: Path) -> None:
    """有责任关系行、却没有工作区绑定的库**不被认领** —— 在**打开时**就拒绝。

    这个状态写路径构造不出来（第一行写入就会绑，且绑定与那一行在同一个写事务里），
    所以它只可能来自一次手工修改或一次没做完的操作。**这一条是 ``OWNED_TABLES`` 里
    ``account_venue_relation`` 那一格的执行器**：清单里少列这张表时，下面这次构造会
    **成功**，而一个混装过的库从此可以被一路打开到第一次读。
    """
    db_path = tmp_path / "state" / "marketops.db"
    _relation_store(db_path).upsert_relation(_relation())
    with session(db_path) as conn, write_transaction(conn):
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))

    with pytest.raises(StoreError, match="没有工作区绑定"):
        _relation_store(db_path)


def test_a_relation_row_of_an_unclaimed_database_is_refused_on_every_read(tmp_path: Path) -> None:
    """三个读**每一个**都要拒绝（``db.owned`` 的 ``bound is None`` 那一支）。

    「已经在跑的实例」是这里的构造方式：先建好 store（那时绑定还在），再手工把绑定
    删掉，然后用**同一个实例**读 —— 读路径不开 ``ensure_schema``，所以它真的会走到
    ``db.owned``。三个读共用同一段 ``_read``，而这一条仍然逐个入口问一遍：把它们
    合成一个入口是某个未来的重构，而那一次重构不该因为没人测过而少挡一个方向。
    """
    db_path = tmp_path / "state" / "marketops.db"
    store = _relation_store(db_path)
    store.upsert_relation(_relation())
    with session(db_path) as conn, write_transaction(conn):
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))

    with pytest.raises(StoreError, match="没有工作区绑定"):
        store.relations_for_account("acct-1")
    with pytest.raises(StoreError, match="没有工作区绑定"):
        store.relations_for_asset("v1")
    with pytest.raises(StoreError, match="没有工作区绑定"):
        store.list_relations()


def test_the_pool_tables_in_one_database_do_not_see_each_other(tmp_path: Path) -> None:
    """同一个 ``database_path`` 上的三张表是三张表 —— 写一张不动另一张。

    这一条钉的是「池子这一族的几张表共用一个函数还是几个库」那个问题的答案：它们共用
    库文件（组合根把三者都接在 ``settings.database_path`` 上），而**不共用行**。一个把
    它们塞进同一张表、用某个列区分的实现在这里会红；而这一族的下一张表落地时，这里多
    一行断言就够了 —— 三张表都写在一个库文件上，正是它们最容易互相踩到的形态。

    账号池这一行还多钉一件事：它的键（``account_id``）与关系表四段键里的那一段**同名**，
    而两张表里各有一个 ``acct-1`` 是**正常的**（同一个账号的池子行与它的责任关系）。
    """
    db_path = tmp_path / "state" / "marketops.db"
    _store(db_path).upsert_asset(_asset())
    relation_store = _relation_store(db_path)
    relation_store.upsert_relation(_relation())
    account_store = _account_store(db_path)
    account_store.upsert_account(_account())

    assert [row.venue_asset_id for row in _store(db_path).list_assets()] == ["v1"]
    assert len(relation_store.list_relations()) == 1
    assert relation_store.relations_for_asset("v1")[0].evidence_ids == ["ev-1"]
    assert [row.account_id for row in account_store.list_accounts()] == ["acct-1"]


# --- 组合根那两个说法（责任关系表那一份） --------------------------------------


def test_the_default_relation_factory_is_lazy_about_creating_the_database(
    settings: Settings,
) -> None:
    """``default_relation_store`` 的 docstring 声称惰性 —— 这一条钉它。

    组合根的契约是「构造服务面需要的一切，**不绑端口、不建运行目录**」。
    """
    factory = default_relation_store(settings)
    assert not settings.database_path.is_file(), "工厂本身不该建库"
    store = factory()
    assert isinstance(store, SqliteAccountVenueRelationStore)
    assert settings.database_path.is_file(), "建出来的 store 才该建库"


def test_the_default_relation_factory_uses_a_real_merge_rule(settings: Settings) -> None:
    """组合根接进去的是 ``application.relations.merge_relations``，不是一条占位规则。

    ## 判据是那条规则**可观测的产物**，而不是它唯一的报错

    资产池那一份能用 ``AssetConflictError`` 来问，是因为 ``merge_assets`` 有一个**只有
    它才抛**的错。``merge_relations`` 的那一条 ``StoreError``（四段键对不上）在**存储
    这一侧走不到**：``upsert_relation`` 是按来行的四段键去读已有的那一行的，两者必然
    同键。所以这里退一步用它的产物：``first_seen_at`` 取更早的那个、``evidence_ids``
    取并集 —— 一条 ``lambda existing, incoming: incoming`` 的占位规则两样都会红。

    这条判据比资产池那一条弱，弱在哪里要写下来：它证明的是「接的不是一个直接覆盖的
    占位规则」，不是「接的正是那一个函数」。真规则本身在
    ``tests/test_application_relations.py`` 里逐条测。
    """
    store = default_relation_store(settings)()
    store.upsert_relation(_relation(evidence_ids=["ev-1"]))
    row = store.upsert_relation(_relation(evidence_ids=["ev-2"], first_seen_at=EARLIER))

    assert row.evidence_ids == ["ev-1", "ev-2"], "并集：第二次独立看到同一件事是加强"
    assert row.first_seen_at == EARLIER, "只往早走：重放一次旧判定不该把它推后"


# --- 账号池：同一组判据的第三份 ------------------------------------------------


def test_the_account_table_is_a_table_the_binding_rule_looks_at() -> None:
    """``market_account`` 必须在 ``OWNED_TABLES`` 里。

    与上面两条逐字同形，而且同样是**各测各的**：清单里加表是逐张加的，一条判据只看它
    自己那张表在不在 —— 「责任关系表在清单里」这一件事对账号池一个字都没说。
    """
    assert "market_account" in OWNED_TABLES


def test_an_account_survives_reopening_the_store(tmp_path: Path) -> None:
    """跨运行留下的东西必须真的跨过进程边界（用第二个实例读）。

    账号池上这一条比另外两张更要紧一点：§6 那一层要回答的是「这个市场现在有哪些账号」，
    而那个答案只在**跨运行**的意义上成立 —— 一次运行里的账号是这次研究的产出，一个池子
    是它之后还在的东西。
    """
    db_path = tmp_path / "state" / "marketops.db"
    _account_store(db_path).upsert_account(
        _account(
            aliases=["Example PC"],
            domains=["client05.example.org"],
            addresses=["Main Road, Example Town One"],
            country="GB",
            status=AccountStatus.ELIGIBLE,
            evidence_ids=["ev-1", "ev-2"],
            last_verified_at=T0,
        )
    )

    reopened = _account_store(db_path)
    row = reopened.get_account("acct-1")
    assert row is not None
    assert row.aliases == ["Example PC"]
    assert row.domains == ["client05.example.org"]
    assert row.country == "GB"
    assert row.status is AccountStatus.ELIGIBLE
    assert row.evidence_ids == ["ev-1", "ev-2"]
    assert row.first_seen_at == T0
    assert row.last_verified_at == T0


def test_an_account_row_in_a_database_nobody_claims_is_refused_on_open(tmp_path: Path) -> None:
    """有账号行、却没有工作区绑定的库**不被认领** —— 在**打开时**就拒绝。

    这个状态写路径构造不出来（第一行写入就会绑，且绑定与那一行在同一个写事务里），所以
    它只可能来自一次手工修改或一次没做完的操作。**这一条是 ``OWNED_TABLES`` 里
    ``market_account`` 那一格的执行器**：清单里少列这张表时，下面这次构造会**成功**，
    而一个混装过的库从此可以被一路打开到第一次读。
    """
    db_path = tmp_path / "state" / "marketops.db"
    _account_store(db_path).upsert_account(_account())
    with session(db_path) as conn, write_transaction(conn):
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))

    with pytest.raises(StoreError, match="没有工作区绑定"):
        _account_store(db_path)


def test_an_account_row_of_an_unclaimed_database_is_refused_on_every_read(
    tmp_path: Path,
) -> None:
    """两个读**每一个**都要拒绝（``db.owned`` 的 ``bound is None`` 那一支）。

    「已经在跑的实例」是这里的构造方式：先建好 store（那时绑定还在），再手工把绑定删掉，
    然后用**同一个实例**读 —— 读路径不开 ``ensure_schema``，所以它真的会走到
    ``db.owned``。
    """
    db_path = tmp_path / "state" / "marketops.db"
    store = _account_store(db_path)
    store.upsert_account(_account())
    with session(db_path) as conn, write_transaction(conn):
        conn.execute("DELETE FROM schema_meta WHERE key = ?", (WORKSPACE_META_KEY,))

    with pytest.raises(StoreError, match="没有工作区绑定"):
        store.get_account("acct-1")
    with pytest.raises(StoreError, match="没有工作区绑定"):
        store.list_accounts()


def test_the_account_table_has_no_index_beyond_its_primary_key(tmp_path: Path) -> None:
    """``market_account`` 上**只有**主键那一个索引 —— 这一条是那句 DDL 注释的执行器。

    ``db.py`` 在那张表的注释里写着「只有主键这一个索引，而这是 §6 那一条的落点」：
    ``domains`` / ``aliases`` / ``addresses`` 一律不是查询路径，因为「按域名找账号」正是
    §6 明令不许做的事。注释本身没有任何执行器 —— 加一个 ``CREATE INDEX ON
    market_account (…)`` 的人不会看到它，而索引**就是**那条查询路径的邀请函。

    判据看的是索引的 ``origin``：SQLite 为 ``TEXT PRIMARY KEY`` 自动建一个 ``origin='pk'``
    的索引，所以「唯一那一个的 origin 是 pk」同时排除了「多一个」与「少一个」。
    """
    db_path = tmp_path / "state" / "marketops.db"
    _account_store(db_path).upsert_account(_account())
    with session(db_path) as conn:
        indexes = [row["origin"] for row in conn.execute("PRAGMA index_list('market_account')")]

    assert indexes == ["pk"], (
        f"账号池那张表上除主键外还有别的索引：{indexes}。"
        "§6 明令不得因为同域名 / 同名 / 近似地址合并两个实体 —— 而一个按这些列建的索引"
        "就是那条路的邀请函。"
    )


# --- 组合根那两个说法（账号池那一份） ------------------------------------------


def test_the_default_account_pool_factory_is_lazy_about_creating_the_database(
    settings: Settings,
) -> None:
    """``default_account_pool_store`` 的 docstring 声称惰性 —— 这一条钉它。

    组合根的契约是「构造服务面需要的一切，**不绑端口、不建运行目录**」。
    """
    factory = default_account_pool_store(settings)
    assert not settings.database_path.is_file(), "工厂本身不该建库"
    store = factory()
    assert isinstance(store, SqliteAccountPoolStore)
    assert settings.database_path.is_file(), "建出来的 store 才该建库"


def test_the_default_account_pool_factory_uses_a_real_merge_rule(settings: Settings) -> None:
    """组合根接进去的是 ``application.accounts.merge_accounts``，不是一条占位规则。

    ## 判据是那条规则**可观测的产物**，而且用的是这一张表独有的那一件

    ``merge_accounts`` 的那条 ``StoreError``（键对不上）在存储这一侧走不到：
    ``upsert_account`` 是按来行的 ``account_id`` 去读已有的那一行的，两者必然同键。

    所以这里用它的产物，而用的是**这一张表独有**的那一件：``organisation`` 那一格
    **第一次记下的为准**。一条 ``lambda existing, incoming: incoming`` 的占位规则会让
    名字跟着最后一行走（下面第二个断言会红），而一条把它写反成「取名字更长的那个」的
    规则会红在别处 —— 真规则本身在 ``tests/test_application_accounts.py`` 里逐条测。
    """
    store = default_account_pool_store(settings)()
    store.upsert_account(_account(organisation="Example PC", aliases=[]))
    row = store.upsert_account(
        _account(organisation="Example Parish Council", first_seen_at=EARLIER)
    )

    assert row.organisation == "Example PC", "主语第一次记下的为准（这条规则独有的一支）"
    assert row.aliases == ["Example Parish Council"], "另一个写法进别名，不丢"
    assert row.first_seen_at == EARLIER, "只往早走：重放一次旧研究不该把它推后"

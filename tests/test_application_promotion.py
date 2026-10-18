"""``application/promotion.py``：候选身份 → 场地资产 → **canonical 账号**。

这一份测试的全部理由是一句话：``research_work_item.account_id`` 与
``market_account.account_id`` 曾经是**两个没有交点的空间**，而两份报告各自看起来
都正常。所以除了逐条判据之外，这里最重要的是那几条**对账**的测试 —— 它们问的是
「同一个组织的两个名字指的是不是同一行」，而不是某一个函数返回了什么。

不联网、不接 MCP、不读配置：三张表都是 ``tmp_path`` 上的临时库。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.pool import SqliteAccountPoolStore, SqliteVenueAssetStore
from marketops.application.accounts import merge_accounts
from marketops.application.pool import merge_assets
from marketops.application.promotion import (
    ACCOUNT_ID_PREFIX,
    PromotionDisposition,
    PromotionOutcome,
    canonical_account_id_for,
    discovery_candidate_id,
    promote_receipt,
)
from marketops.application.discovery_run import candidate_id_for
from marketops.domain.entities import venue_asset_id_for
from marketops.domain.enums import AccountStatus, DomainVerdict, SourceKind
from marketops.domain.evidence import Evidence, evidence_id
from marketops.domain.receipt import RowDisposition, SourceReceipt
from marketops.domain.util import check_canonical_id
from tests.test_source_receipt import _receipt, _row

_WORKSPACE = "uk-acme-natural-grass"
_SEEN_AT = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)
_FETCHED_AT = datetime(2026, 9, 24, 15, 30, tzinfo=timezone.utc)
_SHA = "b" * 64
_SITE = "example-town-one-acmes.example"


def _evidence_row(
    site: str,
    *,
    workspace: str = _WORKSPACE,
    literal: str = "Example Town One AcmeCorp natural grass pitch",
    title: bool = False,
) -> Evidence:
    """发现那一轮为这台主机写下的一行证据 —— 形状照 ``document_evidence``。"""
    url = f"https://{site}/"
    literal_value = literal if not title else f"{literal} · home"
    return Evidence(
        workspace=workspace,
        evidence_id=evidence_id(
            account_id=candidate_id_for(site),
            source_url=url,
            field=None,
            literal_value=literal_value,
            digest=_SHA,
        ),
        account_id=candidate_id_for(site),
        source_url=url,
        source_kind=SourceKind.FETCHED_HTML,
        literal_value=literal_value,
        content_sha256=_SHA,
        fetched_at=_FETCHED_AT,
        field=None,
        context="",
        page_locator="document-title" if title else "whole-document",
        parser="html",
        http_status=200,
        tier="http",
    )


class _ForeignEvidence:
    """一个会把**别的工作区**的证据交回来的证据库。

    真的 ``SqliteEvidenceStore`` 在写入那一步就把库绑死在一个工作区上
    （``bind_workspace``），读的时候 ``owned(...)`` 还会再核一次 —— 所以这条越界
    在今天的适配器上**写不进去**，也就走不到 ``promotion`` 的那条判据。

    留着那条判据、并在这里用一个能造出它的替身来测它，不是因为预计它会响，而是
    因为它把「这条证据属于这次要写的那个工作区」这个前提放在取它的地方，而不是
    放在两个模块之间的一句口头约定上 —— ``application/inputs.py::_receipt_dossiers``
    末尾那条（今天同样不会响）是同一个形状，理由也是同一条。
    """

    def __init__(self, inner: SqliteEvidenceStore, foreign: tuple[Evidence, ...]) -> None:
        self._inner = inner
        self._foreign = foreign

    def list_evidence(self, account_id: str) -> list[Evidence]:
        mine = self._inner.list_evidence(account_id)
        return [*mine, *(one for one in self._foreign if one.account_id == account_id)]

    def put_evidence(self, row: Evidence) -> None:
        self._inner.put_evidence(row)

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        return self._inner.get_evidence(evidence_id)


class _World:
    """三张表 + 一份回执。**每个测试一份**，不共享库文件。"""

    def __init__(self, tmp_path) -> None:
        self.evidence = SqliteEvidenceStore(tmp_path / "marketops.db")
        self.assets = SqliteVenueAssetStore(tmp_path / "marketops.db", merge_assets)
        self.accounts = SqliteAccountPoolStore(tmp_path / "marketops.db", merge_accounts)
        self._foreign: tuple[Evidence, ...] = ()

    def discovery_evidence(
        self,
        site: str = _SITE,
        *,
        workspace: str = _WORKSPACE,
        literal: str = "Example Town One AcmeCorp natural grass pitch",
        title: bool = False,
    ) -> Evidence:
        row = _evidence_row(site, workspace=workspace, literal=literal, title=title)
        self.evidence.put_evidence(row)
        return row

    def foreign_evidence(
        self, site: str = _SITE, *, workspace: str = "some-other-scene", **kwargs: object
    ) -> Evidence:
        """一条**写不进这个库**的证据：换一个工作区，交给替身去交回来。"""
        row = _evidence_row(site, workspace=workspace, **kwargs)
        self._foreign = (*self._foreign, row)
        return row

    @property
    def ledger(self):
        return _ForeignEvidence(self.evidence, self._foreign)

    def promote(self, receipt: SourceReceipt):
        return promote_receipt(
            receipt,
            workspace=_WORKSPACE,
            seen_at=_SEEN_AT,
            assets=self.assets,
            accounts=self.accounts,
            evidence=self.ledger,
        )


def _discovery_row(
    ordinal: int,
    *,
    site: str = _SITE,
    name: str = "Example Town One AcmeCorp",
    location: str = "Example Town One",
    **overrides: object,
) -> RowDisposition:
    """一行**发现那一轮**写下来的处置：``source_identity`` 是 ``site:<主机>``。

    观察那两格（我们在哪一页看到的）照 2026-09-26 之后工件的写法给；
    ``claimed_official_domain`` **不给** —— 发现期的工件不再写那一列，
    因为那一列在下游的意思是「上游**说**的官网」，而这一轮没有任何一句话是那个
    意思。给了它，这条测试测的就是一份不存在的工件。
    """
    fields: dict[str, object] = {
        "source_identity": f"site:{site}",
        "discovered_host": site,
        "discovered_url": f"https://{site}/",
        "resolved_name": name,
        "resolved_location": location,
    }
    fields.update(overrides)
    return _row(row_id=f"row-{ordinal:016x}", ordinal=ordinal, **fields)


# === 1. canonical 账号 id 的形状 ==============================================


def test_the_account_id_is_a_pure_function_of_the_venue_handle() -> None:
    handle = venue_asset_id_for(source_identity="site:x.example", name="", location="")
    assert canonical_account_id_for(handle) == canonical_account_id_for(handle)


def test_the_account_id_is_its_own_canonical_form() -> None:
    """它会被当作证据文件的名字（``<account_id>.jsonl``）。需要被规范化的 id 会与
    另一个 id 落到同一个文件上 —— 两个账号静默合并，报告上只显示「证据更多了」。"""
    handle = venue_asset_id_for(source_identity="site:x.example", name="", location="")
    account_id = canonical_account_id_for(handle)
    assert account_id.startswith(ACCOUNT_ID_PREFIX)
    assert check_canonical_id(account_id, "account_id") == account_id


def test_two_venues_never_share_an_account_id() -> None:
    left = venue_asset_id_for(source_identity="site:a.example", name="", location="")
    right = venue_asset_id_for(source_identity="site:b.example", name="", location="")
    assert canonical_account_id_for(left) != canonical_account_id_for(right)


def test_a_handle_that_is_not_canonical_is_refused_at_the_door() -> None:
    with pytest.raises(ValueError):
        canonical_account_id_for("src-0123/../etc")


# === 2. 发现期的身份反查 ======================================================


def test_a_discovery_row_reads_back_to_its_candidate_id() -> None:
    assert discovery_candidate_id(_discovery_row(0)) == candidate_id_for(_SITE)


def test_a_row_that_is_not_from_discovery_has_no_candidate_id() -> None:
    """一份手工导入的 CSV 没有发现期证据 —— 这是结果，不是失败。"""
    assert discovery_candidate_id(_row(row_id="row-1", ordinal=0)) is None
    assert discovery_candidate_id(_discovery_row(0, source_identity="osm:node/12")) is None
    assert discovery_candidate_id(_discovery_row(0, source_identity="site:")) is None


# === 3. 主路径：一行 resolved → 一块场地 + 一个账号 ============================


def test_a_resolved_row_becomes_an_asset_and_a_canonical_account(tmp_path) -> None:
    world = _World(tmp_path)
    world.discovery_evidence()

    (outcome,) = world.promote(_receipt(dispositions=(_discovery_row(0),)))

    assert outcome.disposition is PromotionDisposition.PROMOTED
    assert outcome.account_id.startswith(ACCOUNT_ID_PREFIX)
    assert outcome.reason == ""

    handle = venue_asset_id_for(source_identity=f"site:{_SITE}", name="Example Town One AcmeCorp", location="Example Town One")
    assert outcome.venue_asset_id == handle
    assert outcome.account_id == canonical_account_id_for(handle)

    assert world.assets.get_asset(handle) is not None
    account = world.accounts.get_account(outcome.account_id)
    assert account is not None
    assert account.organisation == "Example Town One AcmeCorp"


def test_the_account_cites_evidence_that_is_anchored_on_the_account(tmp_path) -> None:
    """**这是这一层最重要的判据。**

    账号挂的依据必须能在**它自己的主体**下读回来。直接把发现期那条 ``site-…`` 的
    id 引过来也能让 ``remember_accounts`` 通过（``get_evidence`` 是按 id 全库找的），
    而 ``get_account`` / ``list_evidence`` 都是按主体读的 —— 那时账号那一行写着
    若干 ``evidence_ids``，而它的 ``evidence_count`` 是 0。两句互相矛盾的话并列在
    一份回执里，读的人只会相信对自己方便的那一句。
    """
    world = _World(tmp_path)
    source = world.discovery_evidence()
    if False:  # pragma: no cover - 只是让 ``source`` 在类型检查里被用上
        print(source)

    (outcome,) = world.promote(_receipt(dispositions=(_discovery_row(0),)))

    assert outcome.evidence_ids
    assert source.evidence_id not in outcome.evidence_ids
    under_the_account = world.evidence.list_evidence(outcome.account_id)
    assert len(under_the_account) == len(outcome.evidence_ids)
    for one in under_the_account:
        assert one.account_id == outcome.account_id
        # 观测本身一格都没变 —— 变的只有主语。
        assert one.source_url == source.source_url
        assert one.literal_value == source.literal_value
        assert one.content_sha256 == source.content_sha256
        assert one.fetched_at == source.fetched_at

    # 账号那一行引用的 id 逐条解析得出来（``remember_accounts`` 的判据）。
    assert world.accounts.get_account(outcome.account_id).evidence_ids == list(outcome.evidence_ids)


def test_the_asset_and_the_account_carry_the_same_evidence(tmp_path) -> None:
    world = _World(tmp_path)
    world.discovery_evidence()
    (outcome,) = world.promote(_receipt(dispositions=(_discovery_row(0),)))
    assert list(outcome.asset.evidence_ids) == list(outcome.evidence_ids)
    assert list(outcome.account.evidence_ids) == list(outcome.evidence_ids)


def test_the_account_leaves_what_it_does_not_know_empty(tmp_path) -> None:
    """域名的三态与``country`` 的三态都是**承重的**：

    * ``official_domain`` 是**这块场地**那一页从哪台主机取回，不是这个组织的域名；
    * ``country=None`` 是「还没查」。回执的 ``scope`` 是整批的属性。
    """
    world = _World(tmp_path)
    world.discovery_evidence()
    (outcome,) = world.promote(_receipt(dispositions=(_discovery_row(0),)))

    account = outcome.account
    assert account.country is None
    assert account.domains == []
    assert account.addresses == []
    assert account.aliases == []
    assert account.status is AccountStatus.DISCOVERED
    # 记下一个账号**不是**重新核实了它。
    assert account.last_verified_at is None
    assert account.first_seen_at == _SEEN_AT


def test_the_venue_keeps_the_observation_and_does_not_turn_it_into_a_claim(tmp_path) -> None:
    """§2：``discovered_host`` / ``claimed_official_domain`` / ``verified_official_domain``
    是**三格**，而发现期写下的只是第一格。

    从前这条测试断言 ``claimed_official_domain == _SITE`` —— 而那个断言之所以能成立，
    是因为工件把「我们取回了哪台主机」写进了「上游**说**的官网」那一列。于是这台主机
    在下游被读成一句关于归属的断言，症状正是 2026-09-26 审计里那条「SERP 抓回来的
    主机被读成『这是它的官网』」。现在两格分开了，这条测试改成钉**分开本身**。
    """
    world = _World(tmp_path)
    world.discovery_evidence()
    (outcome,) = world.promote(_receipt(dispositions=(_discovery_row(0),)))

    asset = outcome.asset
    # 观察：我们在哪一页读到它 —— 这一格有值，且它是从观察那两格来的。
    assert asset.discovered_url == f"https://{_SITE}/"
    assert asset.candidate_domain == _SITE
    # 主张与判定：发现期一个都没说。**空不是「查过、确实没有」**，
    # 那是 domain_verdict=absent；这里是「这一轮没下过任何判定」。
    assert asset.claimed_official_domain == ""
    assert asset.verified_official_domain == ""
    assert asset.verified_official_domains == []
    assert asset.domain_verdict is DomainVerdict.UNKNOWN
    assert asset.domain_evidence_ids == []
    assert asset.country is None


# === 4. 重放幂等 ==============================================================


def test_promoting_the_same_receipt_twice_changes_nothing(tmp_path) -> None:
    """一次重跑要是让池子多出一批行，报告上它读起来只是「这一批更大」。"""
    world = _World(tmp_path)
    world.discovery_evidence()
    receipt = _receipt(dispositions=(_discovery_row(0),))

    first = world.promote(receipt)
    before_assets = world.assets.list_assets()
    before_accounts = world.accounts.list_accounts()
    before_evidence = world.evidence.list_evidence(first[0].account_id)

    second = world.promote(receipt)

    assert [one.account_id for one in second] == [one.account_id for one in first]
    assert world.assets.list_assets() == before_assets
    assert world.accounts.list_accounts() == before_accounts
    assert world.evidence.list_evidence(first[0].account_id) == before_evidence


def test_the_same_venue_in_a_second_receipt_folds_into_the_same_account(tmp_path) -> None:
    """``venue_asset_id_for`` 的类文档：拿行号当句柄，一次重新导出会让每块场地都
    看起来是新发现的。账号 id 跟着句柄走，就免费拿到了同一条性质。"""
    world = _World(tmp_path)
    world.discovery_evidence()
    first = world.promote(
        _receipt(dispositions=(_discovery_row(0),), batch_id="batch-1", artifact_sha256="c" * 64)
    )
    second = world.promote(
        _receipt(dispositions=(_discovery_row(0),), batch_id="batch-2", artifact_sha256="d" * 64)
    )
    assert first[0].account_id == second[0].account_id
    assert len(world.accounts.list_accounts()) == 1
    assert len(world.assets.list_assets()) == 1


# === 5. 没有证据就没有账号=====================================================


def test_a_row_whose_page_was_never_fetched_gets_an_asset_and_no_account(tmp_path) -> None:
    world = _World(tmp_path)
    (outcome,) = world.promote(_receipt(dispositions=(_discovery_row(0),)))

    assert outcome.disposition is PromotionDisposition.ASSET_ONLY
    assert outcome.asset is not None
    assert world.assets.get_asset(outcome.venue_asset_id) is not None
    assert world.accounts.get_account(outcome.account_id) is None
    assert len(world.accounts.list_accounts()) == 0
    assert outcome.reason
    assert "没取到" in outcome.reason


def test_a_row_that_is_not_from_discovery_says_so_in_its_own_words(tmp_path) -> None:
    """两种 ``asset_only`` 说的是**两句不同的话**：一句是「这不是发现那一轮的行」，
    另一句是「那一页没取到」。合并成一句会让需要处置的那一种混在里面。"""
    world = _World(tmp_path)
    (outcome,) = world.promote(_receipt(dispositions=(_row(row_id="row-1", ordinal=0),)))

    assert outcome.disposition is PromotionDisposition.ASSET_ONLY
    # 两句理由的判别词各取一处，而不是把整句抄进来 —— 抄整句的话，改一处措辞
    # 就要改测试，而这条测试要钉的是「两种情形说的话不一样」。
    assert "写下的形状" in outcome.reason
    assert "没取到" not in outcome.reason
    assert outcome.asset is not None


# === 6. 跨工作区的证据：拒绝，且一行都不写 ====================================


def test_evidence_from_another_workspace_is_refused_and_nothing_is_written(tmp_path) -> None:
    """跨工作区引用不是「这一行没有证据」（那是一个正常结果，处置是 ``asset_only``），
    它是一次授权链上的越界，必须看得见 —— 而它的处置是**一行都不写**。"""
    world = _World(tmp_path)
    world.foreign_evidence(workspace="some-other-scene")

    (outcome,) = world.promote(_receipt(dispositions=(_discovery_row(0),)))

    assert outcome.disposition is PromotionDisposition.REJECTED
    assert "some-other-scene" in outcome.reason
    assert outcome.asset is None
    assert outcome.account is None
    assert world.assets.list_assets() == ()
    assert world.accounts.list_accounts() == ()
    # 被拒的那一行**没有**把它的证据换个主体写进去（``put_evidence`` 一次都没被
    # 调到 —— 拒绝发生在写入之前）。
    assert world.evidence.list_evidence(outcome.account_id) == []


def test_a_foreign_evidence_row_does_not_hide_behind_the_ones_that_are_ours(tmp_path) -> None:
    """同一台主机下混进一条别的工作区的证据时，整行仍要拒绝 —— 只筛掉那一条会让
    一次越界读起来像一个正常结果。"""
    world = _World(tmp_path)
    world.discovery_evidence()
    world.foreign_evidence(title=True)

    (outcome,) = world.promote(_receipt(dispositions=(_discovery_row(0),)))
    assert outcome.disposition is PromotionDisposition.REJECTED
    assert world.assets.list_assets() == ()


# === 7. 两行取到同一个句柄 ====================================================


def test_two_rows_that_share_a_handle_get_one_asset_and_no_account(tmp_path) -> None:
    """没有来源编号、名字与地点又相同的两行取到同一个句柄，而它们**没有**
    ``site:`` 身份 —— 所以两行都停在 ``asset_only``，池子里只有一块场地。

    这条测试钉的是 ``PromotionDisposition`` 里**没有** ``folded`` 那一格的理由：
    ``folded`` 该出现的那一天，判据是「两行取到同一个句柄**且**都带着发现期证据」，
    而今天取到同一个句柄的两行必然不是发现那一轮写下的。"""
    world = _World(tmp_path)
    world.discovery_evidence()
    receipt = _receipt(
        dispositions=(
            _discovery_row(0, source_identity=""),
            _discovery_row(1, source_identity=""),
        )
    )
    outcomes = world.promote(receipt)

    assert [one.disposition for one in outcomes] == [
        PromotionDisposition.ASSET_ONLY,
        PromotionDisposition.ASSET_ONLY,
    ]
    assert outcomes[0].venue_asset_id == outcomes[1].venue_asset_id
    assert len(world.assets.list_assets()) == 1
    assert world.accounts.list_accounts() == ()


# === 8. 逐行都要有一个去向 ====================================================


def test_every_resolved_row_gets_exactly_one_outcome_in_ordinal_order(tmp_path) -> None:
    world = _World(tmp_path)
    world.discovery_evidence()
    world.discovery_evidence(site="Example Town Five.example")
    receipt = _receipt(
        dispositions=(
            _discovery_row(0, site="Example Town Five.example", name="Example Club One", location="Example Town Five"),
            _row(row_id="row-x", ordinal=1),
            _discovery_row(2),
        )
    )
    outcomes = world.promote(receipt)

    assert [one.ordinal for one in outcomes] == [0, 1, 2]
    assert [one.disposition for one in outcomes] == [
        PromotionDisposition.PROMOTED,
        PromotionDisposition.ASSET_ONLY,
        PromotionDisposition.PROMOTED,
    ]
    assert len(world.accounts.list_accounts()) == 2


def test_a_receipt_with_nothing_resolved_is_an_empty_tuple(tmp_path) -> None:
    world = _World(tmp_path)
    receipt = _receipt(dispositions=(_row(row_id="row-1", ordinal=0, disposition="excluded", reason="x", exclusion_ids=("e",)),))
    assert world.promote(receipt) == ()


def test_a_naive_seen_at_is_refused(tmp_path) -> None:
    world = _World(tmp_path)
    receipt = _receipt(dispositions=(_discovery_row(0),))
    with pytest.raises(Exception):
        promote_receipt(
            receipt,
            workspace=_WORKSPACE,
            seen_at=datetime(2026, 9, 25, 9, 0),
            assets=world.assets,
            accounts=world.accounts,
            evidence=world.evidence,
        )


# === 9. 一处场地、两台主机：两个账号，各挂各的证据 ============================


def test_two_hosts_never_collapse_into_one_account(tmp_path) -> None:
    world = _World(tmp_path)
    world.discovery_evidence()
    world.discovery_evidence(site="Example Town Five.example")
    receipt = _receipt(
        dispositions=(
            _discovery_row(0),
            _discovery_row(1, site="Example Town Five.example", name="Example Club One", location="Example Town Five"),
        )
    )
    outcomes = world.promote(receipt)

    assert len({one.account_id for one in outcomes}) == 2
    for one in outcomes:
        for ref in one.evidence_ids:
            assert world.evidence.get_evidence(ref).account_id == one.account_id


# === 10. 读数自己的判据 =======================================================


def test_a_non_promoted_outcome_without_a_reason_is_not_constructible() -> None:
    with pytest.raises(ValueError):
        PromotionOutcome(
            ordinal=1,
            row_id="row-1",
            source_identity="site:x.example",
            venue_asset_id="src-0123456789abcdef",
            account_id="acct-0123456789abcdef",
            disposition=PromotionDisposition.ASSET_ONLY,
        )


# === 11. 对账：这一层存在的全部理由 ============================================


def test_the_two_id_spaces_intersect_after_a_promotion(tmp_path) -> None:
    """``research_work_item.account_id ∩ market_account.account_id`` 曾经是空集。

    这一条测试问的不是某个函数返回了什么，而是**两本账是不是同一本**：
    研究那一侧会拿到的主体 id，必须真的在账号池里查得到。
    """
    world = _World(tmp_path)
    world.discovery_evidence()
    world.discovery_evidence(site="Example Town Five.example")
    receipt = _receipt(
        dispositions=(
            _discovery_row(0),
            _discovery_row(1, site="Example Town Five.example", name="Example Club One", location="Example Town Five"),
        )
    )
    outcomes = world.promote(receipt)

    research_side = {one.account_id for one in outcomes if one.account is not None}
    pool_side = {one.account_id for one in world.accounts.list_accounts()}

    assert research_side
    assert research_side == pool_side
    for account_id in research_side:
        # §1 的验收：每一个进入研究的账号都能按 id 读回来，而且它的证据读得到。
        account = world.accounts.get_account(account_id)
        assert account is not None
        assert world.evidence.list_evidence(account_id)

"""合并规则本身（``application/pool.py::merge_assets``）的判据。

它对着存储跑不着：规则是**纯函数**（不读盘、不写盘、不碰时钟），所以这里逐条测它。
存储有没有把它用上，是 ``tests/test_asset_store_contract.py`` 的事 —— 两份判据测的
是两件事，合成一份会让「规则对」与「规则被调用了」互相遮蔽。

## 这里每一条都对应一个**曾经会被静默吃掉**的东西

* ``unknown`` 的来行抹掉已确认的官网 —— 池子在一次重新导入之后退回没查过的样子；
* 空白的来行抹掉地址 / 地区 —— 报告上只是「信息变少了」；
* ``last_verified_at`` 被一次更早的观测推回去 —— 「最后看过它」永远是第一次；
* 两个矛盾的判定里悄悄留一条 —— 而那一条看起来像一次正常更新。

所以这一份里的断言大多是**否定式**的（「没有被抹掉」），而不是「等于新值」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from marketops.application.pool import _NON_EMPTY_WINS_FIELDS, assets_from_receipt, merge_assets
from marketops.domain.entities import VenueAsset
from marketops.domain.enums import DomainVerdict
from marketops.domain.errors import AssetConflictError, StoreError
from marketops.domain.profile import CLASSIFICATION_UNKNOWN
from marketops.domain.receipt import Disposition
from tests.test_source_receipt import _receipt, _row

UTC = timezone.utc
T0 = datetime(2026, 9, 24, 1, 0, tzinfo=UTC)
EARLIER = T0 - timedelta(days=30)
LATER = T0 + timedelta(days=30)
WORKSPACE = "uk-acme-natural-grass"


def asset(venue_asset_id: str = "v1", **overrides) -> VenueAsset:
    payload = {
        "workspace": WORKSPACE,
        "venue_asset_id": venue_asset_id,
        "name": "Main Pitch",
        "first_seen_at": T0,
    }
    payload.update(overrides)
    return VenueAsset(**payload)


def confirmed(domain: str = "example.org", **overrides) -> VenueAsset:
    """一行**证得官网**的判定。四格必须一起给 —— 见模型上的校验器。

    **集合是那一格，单数格是它的读法**（``len(...) == 1`` 时才给值）。所以工厂补的是
    ``verified_official_domains``，单数格跟着派生 —— 反过来补（只填单数格）会被模型
    拒掉，而它拒得有道理：一个只有单数格的判定答不出「一共证得了几个」。
    """
    overrides.setdefault("verified_official_domains", [domain])
    overrides.setdefault("verified_official_domain", domain)
    overrides.setdefault("domain_verdict", DomainVerdict.CONFIRMED)
    overrides.setdefault("domain_evidence_ids", ["ev-1"])
    return asset(**overrides)


def absent(**overrides) -> VenueAsset:
    """一行**查过、确实没有官网**的判定。它也要凭据 —— 那也是一个判定。"""
    overrides.setdefault("domain_verdict", DomainVerdict.ABSENT)
    overrides.setdefault("domain_evidence_ids", ["ev-1"])
    return asset(**overrides)


# --- 这份规则的字段清单 --------------------------------------------------------

#: 池子行上**不由**「非空赢」那条规则处置的格子，以及各自的规则。
#:
#: 它存在的唯一理由是让下面那条判据能把「有一格没人处置」变成一个红。
#: ``_NON_EMPTY_WINS_FIELDS`` 是一张靠人维护的表，而漏掉一个格子的后果**不是**它
#: 按别的规则走 —— 合并结果是 ``{**existing.model_dump(), **changes}``，表外的格子
#: 永远取已有的那一行：那一格会停在这一行落进池子时的样子，一次新观察再怎么带着它
#: 也传不进去，而报告上读起来只是「这块场地的这一栏一直没变」。
POOL_CELLS_WITH_THEIR_OWN_RULE: dict[str, str] = {
    # 身份的两半：折进来的对象与已有的行说的不是同一块场地时抛错，不合并。
    "workspace": "身份的一半 —— 折错了抛 StoreError",
    "venue_asset_id": "身份的另一半 —— 同上；这一格就是折的键",
    # 时刻：一个只往早走，一个只往晚走（None 不参与）。
    "first_seen_at": "只往早走（min）",
    "last_verified_at": "只往晚走（max），None 是「没再看」不是「时间零点」",
    # 国家：与那张表同一条规则，只是「没说」有**两种**写法（None = 这一份工件
    # 没有这一栏、unknown = 这一页没说），而两种都不覆盖。
    "country": "非空赢，None（这一份工件没有这一栏）与 unknown（这一页没说）都不覆盖",
    # 分类那三格：由 _CLASSIFICATION_FIELDS 处置，与那张表只差一句 ——
    # ``unknown`` 也是个非空字符串，在「非空赢」下会把上一次真的读出来的值抹掉。
    # 它们**必须一起动**（同一次分类的产出），所以逐格列在这里。
    "venue_type": "非空赢，且 unknown（这一页没说）不覆盖 —— 见 _CLASSIFICATION_FIELDS",
    "venue_environment": "同上",
    "surface": "同上",
    # 域名判定三格由 _merge_domain 一起决定：unknown 不覆盖、unknown 被覆盖、
    # 两个判定不一致就报错。**它们必须一起动**，所以逐格列在这里而不进那张表。
    "domain_verdict": "与下面三格一起由 _merge_domain 决定",
    "verified_official_domain": "同上",
    "verified_official_domains": "同上 —— 它才是集合那一格，单数格是它的读法",
    "domain_evidence_ids": "同上（两边判定一致时取并集）",
    # 证据指针：并集、保序。
    "evidence_ids": "并集、保序 —— 是指针不是正文",
    # 上游的编号：**跟着 id 走**。有编号时句柄就是它的散列，所以同一个句柄上的
    # 两行本来就带着同一个编号 —— 留哪一行的那一个都一样。真出现了两个不同的，
    # 那是调用方自己拼的一行（生产者那两条分支各带一个前缀，撞不上），而本层
    # 不为那种行发明一条更新规则。
    "source_identity": "保留池子里已有的那一个（同一个句柄意味着同一个编号）",
}


def test_every_cell_on_a_pool_row_has_a_decided_fate() -> None:
    """每一格都必须**被这份规则处置过** —— 要么在那张表里，要么在下面那份名单里。

    这是 ``_NON_EMPTY_WINS_FIELDS`` 上那句「加字段时这里必须被想到」的执行器。
    没有它，那一句是一句自觉：新加的格子落在表外，处置是「来行覆盖」，而
    ``merge_assets`` 里没有任何一行代码会因为漏了它而报错。
    """
    cells = set(VenueAsset.model_fields)
    decided = set(_NON_EMPTY_WINS_FIELDS) | set(POOL_CELLS_WITH_THEIR_OWN_RULE)
    assert cells - decided == set(), (
        "这些格子没人处置 —— 它们会**永远取池子里已有的那一行**，也就是从第二次"
        f"观察起就再没人能改到它们：{sorted(cells - decided)}\n"
        "把它们加进 _NON_EMPTY_WINS_FIELDS（规则就是那一条），或者加进本文件的"
        " POOL_CELLS_WITH_THEIR_OWN_RULE 并写清它们自己的规则。"
    )
    assert decided - cells == set(), (
        f"这份名单里有多余的名字（模型上已经没有这些格子了）：{sorted(decided - cells)}"
    )


def test_a_cell_outside_the_rule_table_keeps_the_row_that_is_already_there() -> None:
    """表外的格子**永远取已有的那一行** —— 它既不是「来行覆盖」，也不是「空抹掉非空」。

    这条判据钉的是那条规则的**机制**：合并结果是 ``{**existing.model_dump(),
    **changes}``，没进 ``changes`` 的格子就取已有的那一行。它同时是上面那份名单里
    ``source_identity`` 那一格的规则（同一个句柄本来就意味着同一个编号），
    以及「新加一格忘了表态」时会发生的事 —— 那一格从第二次观察起再没人能改到。
    """
    merged = merge_assets(
        asset(source_identity="osm:node/123"), asset(source_identity="site:example.org")
    )
    assert merged.source_identity == "osm:node/123"


# --- 生产者：回执 → 行 ---------------------------------------------------------


def test_every_resolved_row_becomes_one_asset_in_ordinal_order() -> None:
    """``resolved`` 的行一条一个，顺序照 ``ordinal``。

    只数 ``resolved``：``pending`` / ``excluded`` / ``merged`` / ``failed`` 都是
    「这一行**没有**变成一个可研究的指称对象」，为它们造池子行等于把一份说了
    「这行还缺身份」的回执读成「这行可以开工了」。
    """
    receipt = _receipt(
        dispositions=(
            _row(row_id="row-0", ordinal=0, resolved_name="First Pitch", source_identity=""),
            _row(row_id="row-1", ordinal=1, disposition=Disposition.PENDING, reason="缺名字"),
            _row(row_id="row-2", ordinal=2, resolved_name="Third Pitch", source_identity=""),
        )
    )
    assets = assets_from_receipt(receipt, workspace=WORKSPACE, seen_at=T0)
    assert [item.name for item in assets] == ["First Pitch", "Third Pitch"]


def test_an_inserted_row_does_not_rename_the_assets_behind_it() -> None:
    """**这是句柄不能从 ``row_id`` 派生的那一条。**

    上游下个季度在文件中间多插一行，它后面每一行的 ``row_id`` 都会变
    （序号在散列里）。拿行号当句柄，一次重新导出会让每一块场地都变成新发现的 ——
    报告上是「本轮新增 N 块场地」，而盘上那块场地从上个季度就躺在那儿。
    """
    before = _receipt(
        dispositions=(
            _row(row_id="row-0", ordinal=0, resolved_name="Main Pitch",
                 source_identity="osm:node/123"),
            _row(row_id="row-1", ordinal=1, resolved_name="Second Pitch",
                 source_identity="osm:node/124"),
        )
    )
    after = _receipt(
        dispositions=(
            # 插进来的一行，把后面那两行的 ordinal 与 row_id 都推后了。
            _row(row_id="row-0", ordinal=0, resolved_name="A New Pitch",
                 source_identity="osm:node/999"),
            _row(row_id="row-1", ordinal=1, resolved_name="Main Pitch",
                 source_identity="osm:node/123"),
            _row(row_id="row-2", ordinal=2, resolved_name="Second Pitch",
                 source_identity="osm:node/124"),
        )
    )
    first = {item.name: item.venue_asset_id for item in assets_from_receipt(
        before, workspace=WORKSPACE, seen_at=T0)}
    second = {item.name: item.venue_asset_id for item in assets_from_receipt(
        after, workspace=WORKSPACE, seen_at=T0)}
    assert first["Main Pitch"] == second["Main Pitch"]
    assert first["Second Pitch"] == second["Second Pitch"]
    # 而插入的那一行确实是新的一块场地 —— 这条判据不是「句柄都一样」。
    assert second["A New Pitch"] not in set(first.values())


def test_two_rows_the_receipt_calls_one_thing_get_one_handle() -> None:
    """回执说「这是同一个东西」的两行，在池子里必须落到同一个句柄上。

    键只有一处定义（``resolved_dedupe_key``），两处各写一套的话，「这份回执里有
    几个东西」与「这一批落进池子几块场地」会各说各话，而两个数字都看着很正常。
    """
    rows = (
        _row(row_id="row-0", ordinal=0, resolved_name="Main Pitch", resolved_location="Devon"),
        _row(row_id="row-1", ordinal=1, resolved_name="Main Pitch", resolved_location="Devon"),
        _row(row_id="row-2", ordinal=2, resolved_name="Other Pitch", resolved_location="Devon"),
    )
    receipt = _receipt(dispositions=rows)
    assets = assets_from_receipt(receipt, workspace=WORKSPACE, seen_at=T0)
    assert len(assets) == 3, "生产者**不折** —— 折的规则只有一条，在 merge_assets 里"
    assert len({item.venue_asset_id for item in assets}) == receipt.distinct_resolved_count
    assert receipt.distinct_resolved_count == 2


def test_the_upstream_claim_lands_in_the_claim_cell_and_nowhere_else() -> None:
    """上游那一列**只**进 ``claimed_official_domain``。

    它是别人写下的一句话，不是我们看到的任何东西，更不是一次验证。所以网址那两格、
    已经验得的域名那一格、判定那三格都必须还是空的 —— 一填错，这一行读起来就像
    「我们看过一个页面」或「我们验过它的官网」。
    """
    receipt = _receipt(
        dispositions=(
            _row(row_id="row-0", ordinal=0, resolved_name="Main Pitch",
                 resolved_location="Devon", claimed_official_domain="example.org"),
        )
    )
    (item,) = assets_from_receipt(receipt, workspace=WORKSPACE, seen_at=T0)
    assert item.claimed_official_domain == "example.org"
    assert item.address == "Devon"
    assert item.discovered_url == "" and item.candidate_domain == ""
    assert item.verified_official_domain == ""
    assert item.domain_verdict is DomainVerdict.UNKNOWN
    assert item.domain_evidence_ids == []
    assert item.evidence_ids == [], "回执里的名字是身份补全的产出，不是一条可引用的证据"


def test_reading_a_receipt_is_not_a_reobservation() -> None:
    """``last_verified_at`` 留空，``country`` 留 ``None``。

    前者：读一份回执不是重新看过这些场地一眼，填上它会让「这块场地多久没复核了」
    在第一次导入那天就得到「刚刚复核过」—— 而下一轮维护会因此整批跳过它们。

    后者：回执的 ``scope`` 是**整批**的属性（「这一批的范围是 GB」），不是逐行的
    国家声明。按行写下去，是在替一份没有逐行声明的导出说话。
    """
    receipt = _receipt(
        dispositions=(_row(row_id="row-0", ordinal=0, resolved_name="Main Pitch"),),
        scope="GB",
    )
    (item,) = assets_from_receipt(receipt, workspace=WORKSPACE, seen_at=T0)
    assert item.last_verified_at is None
    assert item.country is None
    assert item.first_seen_at == T0
    assert item.workspace == WORKSPACE


def test_a_receipt_with_nothing_resolved_is_an_empty_tuple_not_an_error() -> None:
    """一份全是 ``pending`` 的回执写不出池子行 —— 那是**结果**，不是故障。

    回执自己会说出理由（``SourceReceipt.blocker``），池子这一层只需要不撒谎：
    一个空元组与一次「有行但读坏了」在下游必须分得开。
    """
    receipt = _receipt(
        dispositions=(
            _row(row_id="row-0", ordinal=0, disposition=Disposition.PENDING, reason="缺身份"),
        )
    )
    assert assets_from_receipt(receipt, workspace=WORKSPACE, seen_at=T0) == ()


def test_a_naive_seen_at_is_refused_at_the_door() -> None:
    """构造池子行的那一刻就拒绝 naive 时刻，而不是等下一次折入时炸在 ``min`` 里。"""
    receipt = _receipt(dispositions=(_row(row_id="row-0", ordinal=0),))
    with pytest.raises(ValidationError, match="带时区"):
        assets_from_receipt(receipt, workspace=WORKSPACE, seen_at=datetime(2026, 9, 24))


def test_a_produced_row_folds_into_the_pool_like_any_other_observation() -> None:
    """生产出来的行是**普通的来行**：它走同一条合并规则，没有特权。

    具体地说，上游那句声明折进去之后既不能变成已确认的官网，也不能被下一次
    没有那一列的重导抹掉。
    """
    claimed = assets_from_receipt(
        _receipt(dispositions=(_row(row_id="row-0", ordinal=0, resolved_name="Main Pitch",
                                   claimed_official_domain="example.org"),)),
        workspace=WORKSPACE,
        seen_at=T0,
    )[0]
    silent = assets_from_receipt(
        _receipt(dispositions=(_row(row_id="row-0", ordinal=0, resolved_name="Main Pitch"),)),
        workspace=WORKSPACE,
        seen_at=T0,
    )[0]

    merged = merge_assets(claimed, silent)
    assert merged.claimed_official_domain == "example.org", "重导没提那一列，不是「上游改口了」"
    assert merged.domain_verdict is DomainVerdict.UNKNOWN
    assert merged.verified_official_domain == "", "上游写的一句话不许被记成一次验证"


# --- 合并规则：第一行 ----------------------------------------------------------


def test_the_first_observation_is_kept_as_it_is() -> None:
    """池子里没有这一行时，规则不改造它 —— 没有「已有的那一行」可以说话。"""
    incoming = asset(notes="from a directory page")
    assert merge_assets(None, incoming) is incoming


# --- 时刻 ---------------------------------------------------------------------


def test_first_seen_only_moves_earlier() -> None:
    """``first_seen_at`` 是「我们何时第一次记下这块场地」，重放旧导入不许把它推后。

    推后之后池子会声称一块早就认识的场地是刚发现的 —— 那是「新发现了 30 块场地」
    这类报告数字的直接来源。
    """
    merged = merge_assets(asset(first_seen_at=T0), asset(first_seen_at=EARLIER))
    assert merged.first_seen_at == EARLIER
    # 反方向也一样：晚的那一次不许把它推后。
    assert merge_assets(asset(first_seen_at=T0), asset(first_seen_at=LATER)).first_seen_at == T0


def test_last_verified_only_moves_later() -> None:
    """``last_verified_at`` 是「最后一次真的重新看过它」，只往晚走。"""
    merged = merge_assets(asset(last_verified_at=T0), asset(last_verified_at=LATER))
    assert merged.last_verified_at == LATER
    assert merge_assets(asset(last_verified_at=LATER), asset(last_verified_at=T0)).last_verified_at == LATER


def test_none_last_verified_is_silence_not_the_epoch() -> None:
    """``None`` 是「从那以后没再看过」，不是「在时间零点看过一次」。

    把它当 ``min`` 参与比较，会让每一次带 ``None`` 的来行把「最后看过它」抹掉 ——
    而那正是这一格存在的意义。
    """
    assert merge_assets(asset(last_verified_at=T0), asset()).last_verified_at == T0
    both_none = merge_assets(asset(), asset())
    assert both_none.last_verified_at is None


# --- 观察到的文字格子 ----------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "venue_type",
        "surface",
        "address",
        "notes",
        "claimed_official_domain",
        "discovered_url",
        "candidate_domain",
    ],
)
def test_an_empty_field_does_not_erase_a_filled_one(field: str) -> None:
    """**空不是一次观察。**

    一条只带了名字的检索摘要折进来时，其余格子必须原样活着。把它当覆盖写，报告上
    那一行只是「信息变少了」—— 而它事实上是「上一次读到的东西被一次没提这件事的
    来源清掉了」。
    """
    merged = merge_assets(asset(**{field: "known"}), asset(**{field: ""}))
    assert getattr(merged, field) == "known"


@pytest.mark.parametrize(
    "field", ["name", "venue_type", "surface", "address", "notes", "claimed_official_domain"]
)
def test_a_filled_field_takes_the_later_reading(field: str) -> None:
    """非空的两个值取后一个。

    **这不是一条质量判断**：本层没有来源可信度的判据，所以按「后看到的为准」。
    把这一点写成判据，是为了让下一个人知道这里缺的是判据，而不是这一行需要改。
    """
    merged = merge_assets(asset(**{field: "old"}), asset(**{field: "new"}))
    assert getattr(merged, field) == "new"


def test_country_none_does_not_erase_a_checked_country() -> None:
    """``country`` 的「没说」之一写成 ``None``（这一份工件没有这一栏），规则同一条。"""
    merged = merge_assets(asset(country="GB"), asset(country=None))
    assert merged.country == "GB"
    assert merge_assets(asset(country=None), asset(country="GB")).country == "GB"


@pytest.mark.parametrize("field", ["venue_type", "venue_environment", "surface"])
def test_an_unknown_reading_does_not_erase_a_read_value(field: str) -> None:
    """**「这一页没说」不是一次观察 —— 即使它是一个非空字符串。**

    这一条挡着的是一个**只在「非空赢」那条规则下才会发生**的失败：发现期读不出来时
    写的是 ``unknown``（§2 逐字要求未确认必须是它，不能是空串），而 ``unknown`` 在
    truthy 判断下为真 —— 于是照那条通用规则折进来，一次没读出任何东西的重导入会把
    上一次真的读出来的值抹掉。症状是池子里最贵的那几格每重导一次消失一次，
    而报告上那一行只是「这一块的信息变少了」。

    它替换不了上面那两条（空串、以及非空取后者）：三条一起才说明这三格被
    ``_CLASSIFICATION_FIELDS`` 处置过。
    """
    merged = merge_assets(asset(**{field: "known"}), asset(**{field: CLASSIFICATION_UNKNOWN}))
    assert getattr(merged, field) == "known"
    # 反过来写也一样：来行是真的读数时它照样要能传进去 —— 上面那一半不是
    # 「这三格从第二次观察起就再也改不动了」的另一种写法。
    assert (
        getattr(merge_assets(asset(**{field: "old"}), asset(**{field: "new"})), field) == "new"
    )


def test_country_unknown_does_not_erase_a_checked_country() -> None:
    """``country`` 的「没说」有第二种写法：``unknown``（这一页没说）。同上。"""
    merged = merge_assets(asset(country="gb"), asset(country=CLASSIFICATION_UNKNOWN))
    assert merged.country == "gb"
    assert merge_assets(asset(country=CLASSIFICATION_UNKNOWN), asset(country="gb")).country == "gb"
    assert (
        merge_assets(asset(country="gb"), asset(country="scot")).country == "scot"
    ), "读出来是值的时候要能传进去"


# --- 官网判定：§4 那一句的落点 --------------------------------------------------


def test_a_reimport_does_not_erase_a_confirmed_official_domain() -> None:
    """**§4 的承重判据。** 一条原始来行（``unknown`` = 这次没查）不许抹掉已证得的官网。

    这是池子最容易退化的方式：母池按季度重导一次，每次的原始行都带着
    ``domain_verdict=unknown``，而照抄会让「我们确认过它的官网」这件事每重导一次
    就消失一次 —— 且报告上那一行看起来完全正常。
    """
    merged = merge_assets(
        confirmed("example.org", domain_evidence_ids=["ev-1"]),
        asset(name="Main Pitch (reimport)"),
    )
    assert merged.domain_verdict is DomainVerdict.CONFIRMED
    assert merged.verified_official_domain == "example.org"
    assert merged.domain_evidence_ids == ["ev-1"]
    # 其余格子照常被这次导入更新 —— 这一条不是「重导不生效」。
    assert merged.name == "Main Pitch (reimport)"


def test_a_claim_is_never_promoted_to_a_verdict() -> None:
    """上游那句声明折多少次、与已经验得的域名是否**逐字相同**，都不改变判定。

    「确认官网」这一步的全部价值在于证据。一句对的话与一次验证过的结论在审计里
    不是同一件事 —— 若合并时按「两者相等就是确认」，那么一条把上游那一列原样抄
    进池子的导入，会静默地让「我们验过它的官网」这件事从此成立，而没有任何一行
    证据为它作证。
    """
    same = merge_assets(
        asset(claimed_official_domain="example.org"), asset(claimed_official_domain="example.org")
    )
    assert same.domain_verdict is DomainVerdict.UNKNOWN

    # 与一条**真的**验过的域名撞上，也不推动判定 —— 判定那一侧由 _merge_domain
    # 单独管，声明这一侧碰不到它。
    merged = merge_assets(confirmed("example.org"), asset(claimed_official_domain="example.org"))
    assert merged.verified_official_domain == "example.org"
    assert merged.domain_evidence_ids == ["ev-1"]

    # 反方向：声明不许被一次**没做判定**的来行抹掉（上面那条参数化判据的落点）。
    assert merge_assets(
        asset(claimed_official_domain="example.org"), absent()
    ).claimed_official_domain == "example.org"


def test_a_verdict_fills_an_unchecked_row() -> None:
    """反方向：池子里那一行还没查过，一次**做了判定**的来行要能把它填上。

    少了这一半，「空不覆盖」会让池子永远停在第一次看到的样子 —— 而那正好是
    §4 要它别停在的地方。
    """
    merged = merge_assets(asset(domain_verdict=DomainVerdict.UNKNOWN), confirmed("example.org"))
    assert merged.domain_verdict is DomainVerdict.CONFIRMED
    assert merged.verified_official_domain == "example.org"
    assert merged.domain_evidence_ids == ["ev-1"]


def test_an_unchecked_row_does_not_erase_an_absent_verdict() -> None:
    """「查过、确实没有」也是一个判定，而它比「还没查」强。

    把它当成空值，会让一次重导把「我们查过，它没有官网」退回「我们还没查」——
    下游按 `unknown` 处置（继续花预算去查）而不是按 `absent`（不再查）。
    """
    merged = merge_assets(absent(), asset())
    assert merged.domain_verdict is DomainVerdict.ABSENT
    assert merged.domain_evidence_ids == ["ev-1"]


def test_two_independent_confirmations_accumulate_their_evidence() -> None:
    """两次独立看到同一件事是**加强**，不是重复：证据取并集。"""
    merged = merge_assets(
        confirmed("example.org", domain_evidence_ids=["ev-1"]),
        confirmed("example.org", domain_evidence_ids=["ev-2"]),
    )
    assert merged.domain_evidence_ids == ["ev-1", "ev-2"]
    assert merged.verified_official_domain == "example.org"


def test_two_confirmations_that_disagree_are_refused() -> None:
    """两个 ``confirmed`` 配了两个不同的域名 —— 报错，不挑一个。

    选哪一条都说得通，而没有一条说得清（本层没有「谁的判定更权威」）。所以两条都
    点出来，交给一次显式的决定。照折会把其中一条静默作废。
    """
    with pytest.raises(AssetConflictError, match="example.org") as caught:
        merge_assets(
            confirmed("example.org", domain_evidence_ids=["ev-1"]),
            confirmed("other.example", domain_evidence_ids=["ev-2"]),
        )
    # 消息里两边都必须在：只说「冲突了」，读的人无法判断要作废哪一条。
    assert "other.example" in str(caught.value)
    assert "ev-1" in str(caught.value) and "ev-2" in str(caught.value)


def test_absent_contradicting_confirmed_is_refused() -> None:
    """「查过确实没有」撞上「这就是它的官网」—— 同一条处置。"""
    with pytest.raises(AssetConflictError):
        merge_assets(absent(), confirmed("example.org"))


def test_the_conflict_error_is_not_a_store_error() -> None:
    """冲突与存储故障是两类失败，而它们的处置相反。

    ``StoreError`` 是「这个库打不开 / 版本不匹配」—— 处置是停机对账；
    ``AssetConflictError`` 是两行业务事实互相矛盾 —— 处置是回看证据、做一次决定。
    共用一个类型会让一次真实的判定冲突在日志里长得像一次存储事故。
    """
    assert not issubclass(AssetConflictError, StoreError)


# --- 证据指针 -----------------------------------------------------------------


def test_evidence_is_accumulated_in_order() -> None:
    """``evidence_ids`` 取并集、保序：已有的在前，新来的接上。

    它是**指针**不是正文（指向的那几行在证据库里只增不改），所以并集是「这些行都
    关于这块场地」。顺序是承重的：人读证据链时读的就是「先看到的在前」。
    """
    merged = merge_assets(
        asset(evidence_ids=["ev-a", "ev-b"]),
        asset(evidence_ids=["ev-b", "ev-c"]),
    )
    assert merged.evidence_ids == ["ev-a", "ev-b", "ev-c"]


def test_evidence_may_be_recorded_without_claiming_a_verdict() -> None:
    """一条与官网无关的证据折进来，不许把域名判定带上。

    这两件事共用 ``Evidence`` 那一张表，但 ``evidence_ids`` 说的是「关于这块场地的
    一切」，``domain_evidence_ids`` 说的是「支撑那个判定的」。混起来之后，
    「凭什么说这是它的官网」会指向一条根本没提官网的行。
    """
    merged = merge_assets(asset(), asset(evidence_ids=["ev-a"]))
    assert merged.evidence_ids == ["ev-a"]
    assert merged.domain_verdict is DomainVerdict.UNKNOWN
    assert merged.domain_evidence_ids == []


# --- 身份 ---------------------------------------------------------------------


def test_folding_another_asset_is_refused() -> None:
    """折进来的对象说的不是同一块场地时，**这不是一次合并**。

    照折的话，一次写错的 id 会把另一块场地的行整个改写掉，而报告上它看起来只是
    那块场地的信息变了。
    """
    with pytest.raises(StoreError, match="v2"):
        merge_assets(asset(), asset("v2"))


def test_folding_across_workspaces_is_refused() -> None:
    with pytest.raises(StoreError, match=WORKSPACE):
        merge_assets(asset(), asset(workspace="uk-something-else"))


# --- 合并出来的行要重新过校验器 ------------------------------------------------


def test_the_merged_row_is_revalidated_not_copied() -> None:
    """合并出来的行必须**重新过一遍校验器**。

    ``model_copy(update=...)`` 不跑校验器，而 ``VenueAsset`` 上
    ``_domain_verdict_agrees_with_the_fields`` 是三格一致性的**唯一**执行器 ——
    用 copy 合并，正是「域名填了但判定还是 unknown」那种行被写进池子里的路径。
    这里用一个**空域名的 CONFIRMED 来行**摆出那个状态：模型层拒绝它，所以它到不了
    合并函数 —— 这条判据钉的是「合并函数自己也不许造出它」。
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="verified_official_domain"):
        confirmed("")

    # 正对照的另一半：合法的两行合并出来仍然是合法的，且三格一致。
    merged = merge_assets(asset(), confirmed("example.org"))
    assert merged.model_dump()["domain_verdict"] == "confirmed"
    assert VenueAsset.model_validate(merged.model_dump()) == merged

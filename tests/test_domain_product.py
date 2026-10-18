"""产品目录的形状：**能被批准、能被引用、能被查缺**。

`docs/PRODUCT_LINE_AND_SCENE_MODEL.md` §2 那张表把 Product/ProductVersion 与
Offer/OfferVersion 各自「不等于」什么写死了。本文件把其中能变成判据的几条钉住 ——
每一条都对应一种「报告上看起来一切正常」的失败：

1. **地区版不能偷偷扩张能力。** §6 逐字「地区版不能偷偷继承无效 claim」。
   一个 region variant 如果能列出产品主档里没有的能力，那一格就成了「某个地区
   做得到」这个说法的**唯一出处**，而它没有出处。
2. **无核实资料只可 draft。** B01 逐字（执行计划 `:100`）。这条要能被拒，否则
   「已批准的能力」与「某个人说的能力」在盘上是同一格。
3. **Offer 引用的是**恰好**那一版产品。** 「第 2 版」在两次部署里可能是两份不同的
   东西，所以引用必须落到 ``(product_id, version)`` 上，不许落到 ``product_id`` 上
   再去解释一次「哪一版」。
4. **价格只有一处落点，而那一格不是数字。** PSM `:19` 逐字「价格可缺省而**不可
   编造**」，`:156` 逐字「无价格可照样研究；草稿不得生成猜价或规格」。两半都要：
   那一格是 ``QuotedPrice | None``（所以「还没报价」写得下来），而 ``QuotedPrice``
   没有出处就造不出来（所以「编造」写不下来）。
5. **原始资料只留索引，不留字节。** B01 逐字（`:100`）「原始资料进受管 artifact
   store；Git 仅获准索引/schema/规则，不上传私有手册/联系人」。这几型里出现一个
   ``content``/``body``/``bytes`` 格，就是那条边界被挪进了这个仓库。
6. **claim 的作用域是加出来的，不是猜出来的。** §6 逐字「复用当前 ApprovedClaim
   并增作用域」。作用域为空的老记录必须**保持旧读数可读**（不限市场），
   而不是被追认一个后人挑的市场。
7. **ProductLine 是可选分组，不是运行边界。** §4.1 逐字「「是否正在经营」由已批准
   launch/active Scene 投影，不维护互相打架的布尔值」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from marketops.domain.outreach import ApprovedClaim
from marketops.domain.product import (
    OfferVersion,
    ProductLine,
    ProductRef,
    ProductStatus,
    ProductVersion,
    QuotedPrice,
    RegionVariant,
    SourceArtifactRef,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(days=30)
#: 严格晚于 ``_claim()`` 的 ``expires_at`` —— 边界当天算活着，所以要多走一天。
EXPIRED = LATER + timedelta(days=1)
OWNER = "probe-owner"
APPROVER = "probe-approver"


# --- 夹具 -------------------------------------------------------------------


def _artifact(artifact_id: str = "paint-spec-2026") -> SourceArtifactRef:
    return SourceArtifactRef(
        artifact_id=artifact_id,
        locator="https://example.invalid/paint-spec.pdf",
        content_sha256="a" * 64,
        fetched_at=NOW,
        media_type="application/pdf",
    )


def _product(**over: object) -> ProductVersion:
    fields: dict[str, object] = {
        "product_id": "acme-pro",
        "version": 1,
        "display_name": "AcmeCorp Pro",
        "capabilities": ("line-marking", "cold-weather-cure"),
        "limits": ("needs-dry-surface",),
        "units": "litre",
        "owner": OWNER,
        "source_refs": (_artifact(),),
        "status": ProductStatus.READY,
        "approved_by": APPROVER,
        "approved_at": NOW,
    }
    fields.update(over)
    return ProductVersion(**fields)  # type: ignore[arg-type]


def _claim(**over: object) -> ApprovedClaim:
    fields: dict[str, object] = {
        "claim_id": "claim-line-marking",
        "revision": 1,
        "text": "适合天然草坪的划线",
        "source_ref": "paint-spec-2026",
        "approved_by": APPROVER,
        "approved_at": NOW,
        "expires_at": LATER,
    }
    fields.update(over)
    return ApprovedClaim(**fields)  # type: ignore[arg-type]


# --- 1. 地区变体只能收窄 ------------------------------------------------------


def test_a_region_variant_cannot_widen_the_product() -> None:
    """变体列出一条主档没有的能力 ⇒ 拒。

    这一格是「某个地区做得到」的唯一出处，所以它必须先被主档覆盖。
    """
    with pytest.raises(ValidationError) as caught:
        _product(
            region_variants=(
                RegionVariant(market="GB", capabilities=("line-marking", "frost-proof")),
            )
        )
    assert "frost-proof" in str(caught.value)


def test_a_region_variant_may_narrow_the_product() -> None:
    """收窄是合法的 —— 上面那条判据不许把正常用法一起拒掉。"""
    product = _product(
        region_variants=(RegionVariant(market="GB", capabilities=("line-marking",)),)
    )
    assert product.region_variants[0].capabilities == ("line-marking",)


def test_one_market_appears_in_at_most_one_variant() -> None:
    """同一个市场两个变体 ⇒ 那一格有两个互相打架的答案。"""
    with pytest.raises(ValidationError) as caught:
        _product(
            region_variants=(
                RegionVariant(market="GB", capabilities=("line-marking",)),
                RegionVariant(market="GB", capabilities=()),
            )
        )
    assert "GB" in str(caught.value)


def test_a_market_is_a_country_code_not_a_region_word() -> None:
    """§2 那张表逐字：GeoScope「不等于只有自由文本 ``Devon`` 的搜索词」。"""
    with pytest.raises(ValidationError):
        _product(region_variants=(RegionVariant(market="Devon", capabilities=()),))


# --- 2. 无核实资料只可 draft --------------------------------------------------


def test_a_version_without_verified_material_cannot_be_ready() -> None:
    """没有来源索引却标成 ``ready`` ⇒ 拒（B01 `:100` 逐字「新型号无核实资料只可 draft」）。"""
    with pytest.raises(ValidationError) as caught:
        _product(source_refs=())
    assert "source_refs" in str(caught.value)


def test_a_ready_version_names_who_approved_it() -> None:
    """``ready`` 是「已核对」，核对者必须是一个 principal，不是自证。"""
    with pytest.raises(ValidationError) as caught:
        _product(approved_by="")
    assert "approved_by" in str(caught.value)


def test_a_draft_is_free() -> None:
    """反面必须成立，否则上面两条会逼着人「先填点什么」才能登记一个新型号 ——
    而登记不等于开工（B01 的标题逐字）。"""
    draft = _product(
        source_refs=(), status=ProductStatus.DRAFT, approved_by="", approved_at=None
    )
    assert draft.status is ProductStatus.DRAFT


def test_a_retired_version_stops_new_offers() -> None:
    """§4.1 逐字「产品归档停止创建新的 Offer，不删除历史结果」。"""
    assert _product(status=ProductStatus.RETIRED).accepts_new_offers is False
    assert _product().accepts_new_offers is True


# --- 3. Offer 引用恰好那一版 --------------------------------------------------


def test_an_offer_names_exact_product_versions() -> None:
    """引用落到 ``(product_id, version)``，不落到 ``product_id`` 上再解释一次。"""
    offer = OfferVersion(
        offer_id="offer-uk-schools",
        version=1,
        workspace="probe-workspace",
        name="UK schools",
        purpose="学期前的场地划线",
        product_refs=(ProductRef(product_id="acme-pro", version=2),),
        markets=("GB",),
        owner=OWNER,
        status=ProductStatus.READY,
        approved_by=APPROVER,
        approved_at=NOW,
    )
    assert offer.product_refs[0].version == 2


def test_a_ready_offer_names_at_least_one_exact_product_version() -> None:
    """一个「已批准」的方案指着零个产品，是「这次到底卖什么」没有答案。"""
    with pytest.raises(ValidationError) as caught:
        OfferVersion(
            offer_id="offer-empty",
            version=1,
            workspace="probe-workspace",
            name="empty",
            purpose="probe",
            markets=("GB",),
            owner=OWNER,
            status=ProductStatus.READY,
            approved_by=APPROVER,
            approved_at=NOW,
        )
    assert "product_refs" in str(caught.value)


def test_a_ready_offer_names_the_markets_it_may_be_used_in() -> None:
    """§6 逐字 OfferVersion 含「市场可用性」；空市场 = 「哪儿都能用」，而那是一条授权。"""
    with pytest.raises(ValidationError) as caught:
        OfferVersion(
            offer_id="offer-anymarket",
            version=1,
            workspace="probe-workspace",
            name="anymarket",
            purpose="probe",
            product_refs=(ProductRef(product_id="acme-pro", version=1),),
            owner=OWNER,
            status=ProductStatus.READY,
            approved_by=APPROVER,
            approved_at=NOW,
        )
    assert "markets" in str(caught.value)


# --- 4. 猜价无处可写（结构判据） ----------------------------------------------

#: 价格/成本这一族的词元。它们在**外围**模型上不该有落点 —— 唯一的落点是
#: ``OfferVersion.price``，而它的类型是 ``QuotedPrice``。
PRICE_WORDS = ("price", "pricing", "cost", "amount", "currency", "margin", "discount")


def test_the_only_place_a_price_can_live_is_a_sourced_quote() -> None:
    """PSM `:19` 逐字「价格可缺省而**不可编造**」。

    两半，缺一条都绿不了：

    * **可缺省** —— 那一格是 ``QuotedPrice | None``，所以「还没报价」是一个可以
      写下来的状态；
    * **不可编造** —— 那一格**不是数字**。一个裸 ``price: str`` 或
      ``price: float`` 格存在的那一天，就有人会在某处填上一个看起来合理的数，
      而它在报告上读起来与一条真的报价一样。
    """
    annotation = OfferVersion.model_fields["price"].annotation
    assert get_args(annotation) == (QuotedPrice, type(None)), (
        "OfferVersion.price 必须是 QuotedPrice | None —— "
        "一个裸数字格会让「不可编造」只剩自觉"
    )


def test_there_is_no_second_place_for_a_price() -> None:
    """价格只有**一处**落点。第二处＝两个会互相打架的读数，而两个都读起来像价格。"""
    for model in (ProductVersion, ProductLine, RegionVariant, SourceArtifactRef):
        for name in model.model_fields:
            assert not any(word in name for word in PRICE_WORDS), (
                f"{model.__name__}.{name} —— 价格只住在 OfferVersion.price 上"
            )
    # 一个已经带 ``price`` 的模型里不能再有第二个价格味的字段。
    others = [name for name in OfferVersion.model_fields if name != "price"]
    for name in others:
        assert not any(word in name for word in PRICE_WORDS), (
            f"OfferVersion.{name} —— 价格只住在 price 那一格上"
        )


def test_a_price_cannot_be_written_without_a_source() -> None:
    """「不可编造」的执行器：没有出处、没有时刻、没有计价基准的报价造不出来。

    三条分开判而不是合成一条，因为它们是**三种不同的编造**：不知道从哪来、
    不知道什么时候的、不知道按什么算的。任一条缺了，那个数字都还写得出来。
    """
    complete: dict[str, object] = {
        "currency": "GBP",
        "amount_minor": 1250,
        "basis": "per litre",
        "source_ref": "paint-spec-2026",
        "quoted_at": NOW,
    }
    assert QuotedPrice(**complete).amount_minor == 1250  # type: ignore[arg-type]

    for missing in ("source_ref", "quoted_at", "basis", "currency"):
        fields = {k: v for k, v in complete.items() if k != missing}
        with pytest.raises(ValidationError) as caught:
            QuotedPrice(**fields)  # type: ignore[arg-type]
        assert missing in str(caught.value)


def test_an_offer_may_carry_no_price_at_all() -> None:
    """§6 逐字「无价格可照样研究」。

    反面必须成立，否则上面那条会逼着人「先报个价」才能登记一个方案 ——
    而那正好是「草稿生成猜价」那条禁令的入口。
    """
    offer = OfferVersion(
        offer_id="offer-unpriced",
        version=1,
        workspace="probe-workspace",
        name="unpriced",
        purpose="probe",
        owner=OWNER,
    )
    assert offer.price is None


def test_a_price_amount_is_an_integer_in_minor_units() -> None:
    """金额不是浮点。用浮点算钱的地方会在某一次加法里造出一个没人批准过的数 ——
    而它看起来完全正常。判据用 ``12.5``：一个整数值的浮点会被宽松模式收下，
    所以探针必须是**分不出来**的那一个。
    """
    with pytest.raises(ValidationError):
        QuotedPrice(
            currency="GBP",
            amount_minor=12.5,  # type: ignore[arg-type]
            basis="per litre",
            source_ref="paint-spec-2026",
            quoted_at=NOW,
        )


def test_a_currency_is_an_iso_code_not_a_word() -> None:
    """自造的单位名会在对账时变成一个没人认得的量。"""
    with pytest.raises(ValidationError):
        QuotedPrice(
            currency="pounds",
            amount_minor=1250,
            basis="per litre",
            source_ref="paint-spec-2026",
            quoted_at=NOW,
        )


# --- 5. 只留索引，不留字节 ----------------------------------------------------


def test_the_catalog_types_carry_no_raw_bytes() -> None:
    """B01 `:100`：原始资料进受管 artifact store，Git 只放索引。

    一个 ``content`` / ``body`` / ``bytes`` 格存在，就是把那条边界挪进了仓库 ——
    而它一旦在类型上，落盘路径（payload/schema/契约）会跟着自动支持它。
    """
    forbidden = {"content", "body", "bytes", "blob", "raw", "payload"}
    digest = {"sha256", "digest", "hash"}
    for model in (
        SourceArtifactRef,
        ProductVersion,
        OfferVersion,
        QuotedPrice,
        ProductLine,
    ):
        for name in model.model_fields:
            words = set(name.split("_"))
            if not words & forbidden:
                continue
            # 例外只有一条，且名字自己要说清它是摘要：``content_sha256`` 是索引，
            # ``content`` 是字节。两格在盘上长得一样，在证据链上差一整条边界。
            assert words & digest, (
                f"{model.__name__}.{name} —— 原始字节不在这几型里，只留定位与摘要"
            )
    assert "content_sha256" in SourceArtifactRef.model_fields


def test_an_artifact_digest_is_a_sha256_not_a_prefix() -> None:
    """摘要要能与别的量具对得上（本仓的证据链是 64 位十六进制）。"""
    with pytest.raises(ValidationError):
        SourceArtifactRef(
            artifact_id="probe",
            locator="https://example.invalid/x",
            content_sha256="a" * 16,
            fetched_at=NOW,
        )


# --- 6. claim 的作用域是加出来的 ----------------------------------------------


def test_a_claim_without_scope_keeps_the_old_reading() -> None:
    """作用域为空 = 那条老记录的意思（不限市场），**不是**「哪个市场都不算」。

    追认一个后人挑的市场，会让一条早已在用的说法在某个地区突然失效 ——
    而没有任何人做过那次决定。
    """
    claim = _claim()
    assert claim.applies_to(product=None, market="GB", locale="en", at=NOW) is True
    assert claim.applies_to(product=None, market="FR", locale="fr", at=NOW) is True


def test_a_market_scoped_claim_is_not_inherited_by_another_market() -> None:
    """§6 逐字「地区版不能偷偷继承无效 claim」—— 这是那条的执行器。"""
    claim = _claim(markets=("GB",))
    assert claim.applies_to(product=None, market="GB", locale="en", at=NOW) is True
    assert claim.applies_to(product=None, market="FR", locale="fr", at=NOW) is False


def test_a_claim_bound_to_a_product_does_not_cover_another_product() -> None:
    """「绑产品/变体」：绑了 A 的说法不是 B 的能力来源。"""
    claim = _claim(product_ref=ProductRef(product_id="acme-pro", version=1))
    assert (
        claim.applies_to(
            product=ProductRef(product_id="acme-pro", version=1),
            market="GB",
            locale="en",
            at=NOW,
        )
        is True
    )
    assert (
        claim.applies_to(
            product=ProductRef(product_id="scanner-x", version=1),
            market="GB",
            locale="en",
            at=NOW,
        )
        is False
    )


def test_a_claim_bound_to_one_product_version_does_not_cover_the_next_one() -> None:
    """绑的是**那一版**：换一版产品是一次重新核对，不是自动继承。"""
    claim = _claim(product_ref=ProductRef(product_id="acme-pro", version=1))
    assert (
        claim.applies_to(
            product=ProductRef(product_id="acme-pro", version=2),
            market="GB",
            locale="en",
            at=NOW,
        )
        is False
    )


def test_liveness_and_scope_are_answered_by_one_question() -> None:
    """§4.2 第 1 步把「来源、适用市场、用途限制、claim 期限」列在一次检查里。

    拆成两个判据会让调用点只记住其中一个，而「过期的说法」与「跨市场的说法」
    在报告上是同一格。

    三条一起判，因为一条只判「过期时为假」的判据**在一个恒为假的实现上也绿**：
    这里同时要求它在期限内、在市场内时为真，并沿用 ``is_live_at`` 那条已经写下来
    的边界（**到期当天算活着**）。
    """
    claim = _claim(markets=("GB",))
    assert claim.applies_to(product=None, market="GB", locale="en", at=LATER) is True
    assert claim.applies_to(product=None, market="GB", locale="en", at=EXPIRED) is False


def test_a_retired_claim_is_not_live_even_while_its_expiry_says_otherwise() -> None:
    """退场**先于**期限：``retired`` 是「这条说法退场了」唯一的承载处。

    前两行是**手工**构造的（``retired=True`` 配上仍在未来的 ``expires_at``，以及
    干脆不写期限的那一种）—— 因为生产路径（``withdraw_claim``）永远同时把
    ``expires_at`` 落在撤回那一刻，于是**只判期限的实现在所有走生产路径的判据上
    都绿**。这两行是那种实现会红的唯一地方，也就是 ``is_live_at`` 里
    ``if self.retired`` 那两行存在的**唯一理由**：不这么写就有两个位在说同一件
    事，而其中之一（期限）可以被手工构造出来的行绕过 —— 那时
    ``application/outreach.py`` 那处按期限筛「这一刻有哪些说法可用」的枚举会把一条
    已经退场的说法当活的选出来，而查缺面按 ``retired`` 判它退场了。

    第二行不是凑数：``expires_at=None`` 在本仓是「不设期限」（``True``，永久有效），
    所以一个把退场实现成「把期限提前」的改法在这里会读成**永久有效**。

    末一行是对照：同一个 ``_claim`` **只差这一位**，期限内就是活的 —— 少了它，一个
    恒返回 ``False`` 的 ``is_live_at`` 也绿。
    """
    assert _claim(retired=True).is_live_at(NOW) is False
    assert _claim(retired=True, expires_at=None).is_live_at(NOW) is False
    assert _claim(retired=False).is_live_at(NOW) is True


def test_a_locale_scoped_claim_is_not_inherited_by_another_language() -> None:
    """§3 条件 3 那条同源：语言不是国家，反过来也一样。

    第三行是判据的**收窄方向**：问句里给一条带地区子标签的 locale（``en-GB``），
    落在 ``en`` 那条说法上必须**算数**。少了它，一个把两串整个比对的实现也全绿 ——
    而那正是「英国 = 英语」被焊进类型的第一步。
    """
    claim = _claim(locales=("en",))
    assert claim.applies_to(product=None, market="GB", locale="en", at=NOW) is True
    assert claim.applies_to(product=None, market="GB", locale="en-GB", at=NOW) is True
    assert claim.applies_to(product=None, market="GB", locale="fr", at=NOW) is False


# --- 7. ProductLine 是可选分组 ------------------------------------------------


def test_a_product_line_is_not_a_running_boundary() -> None:
    """`:110` 逐字「ProductLine 不是运行边界」—— 所以产品与 Offer 的字段里没有它。"""
    for model in (ProductVersion, OfferVersion):
        for name, field in model.model_fields.items():
            assert "line" not in name, f"{model.__name__}.{name}"
            assert "ProductLine" != getattr(field.annotation, "__name__", None)


def test_a_product_line_does_not_keep_a_second_opinion_about_being_live() -> None:
    """§4.1 逐字：「是否正在经营」由已批准 launch/active Scene **投影**，
    不维护互相打架的布尔值。所以这一型里没有布尔格。"""
    for name in ProductLine.model_fields:
        assert not name.startswith("is_") and not name.startswith("has_"), name
    assert not any(
        field.annotation is bool for field in ProductLine.model_fields.values()
    )


def test_a_product_line_records_the_optional_grouping() -> None:
    """§4.1 的最小主档：九个字段，一个不少。"""
    line = ProductLine(
        line_id="line-lawn-care",
        workspace="probe-workspace",
        name="草坪养护",
        customer_problem="场地维护方要一份可核对的作业与用料记录",
        capability_family="surface-care",
        owner=OWNER,
        product_refs=(ProductRef(product_id="acme-pro", version=1),),
        approved_material_refs=("paint-spec-2026",),
    )
    assert line.status is ProductStatus.DRAFT
    assert line.owner == OWNER


# --- 8. 全仓纪律 --------------------------------------------------------------


BAD_ID = "AcmeCorp Pro"


def test_ids_are_canonical() -> None:
    """id 会被当文件名用；需要规范化的 id 会与另一个 id 落到同一个文件上。

    三条分开写而不是参数化：每条走的是**不同的校验器**，而一个参数化的版本
    在某一型漏挂校验器时仍会绿 —— 它只会换一条分支过去，看不出少了一根。
    """
    with pytest.raises(ValidationError):
        SourceArtifactRef(
            artifact_id=BAD_ID,
            locator="https://example.invalid/x",
            content_sha256="a" * 64,
            fetched_at=NOW,
        )
    with pytest.raises(ValidationError):
        _product(product_id=BAD_ID)
    with pytest.raises(ValidationError):
        OfferVersion(
            offer_id=BAD_ID,
            version=1,
            workspace="probe-workspace",
            name="x",
            purpose="x",
            owner=OWNER,
        )
    with pytest.raises(ValidationError):
        ProductLine(line_id=BAD_ID, workspace="probe-workspace", name="x", owner=OWNER)
    with pytest.raises(ValidationError):
        _claim(claim_id=BAD_ID)
    with pytest.raises(ValidationError):
        OfferVersion(
            offer_id="offer-ok",
            version=1,
            workspace=BAD_ID,
            name="x",
            purpose="x",
            owner=OWNER,
        )


def test_times_must_carry_a_timezone() -> None:
    """朴素时间在跨时区的账上是一个说不清的瞬间 —— 本仓一律要求带时区。"""
    with pytest.raises(ValidationError):
        SourceArtifactRef(
            artifact_id="probe",
            locator="https://example.invalid/x",
            content_sha256="a" * 64,
            fetched_at=datetime(2026, 9, 28, 12, 0),
        )
    with pytest.raises(ValidationError):
        _product(approved_at=datetime(2026, 9, 28, 12, 0))

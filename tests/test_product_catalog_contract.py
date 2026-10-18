"""``ProductCatalog`` 的契约：**同一份判据跑在两个实现上**。

## 为什么这一份与 ``tests/test_domain_product.py`` 是两件事

那一份判的是**形状**（类型上能不能造出一个没有出处的报价）。这一份判的是**存储**
（写下去的那一份读回来还是不是同一份）。形状对了而存储漏字段是可能的 —— 一个
``dump`` 少写了一格，读回来的对象在报告上与原来完全一样，只是那一格回到了缺省值。
所以这里的往返判据是**整对象相等**，不是逐格抽查。

## 版本一经写下不再变

``put_version`` 的幂等边界与 ``SceneStore`` / ``EvidenceStore`` 是同一条纪律
（``ports/scenes.py`` 的模块 docstring 逐字写着它）：

* 同一个 ``(product_id, version)`` 再写一次，内容**相同**就是幂等 —— 重启后重放
  一批登记不该报错；
* 内容**不同**就是有人手工改过 id 或者拿新内容盖旧版号 —— 必须报错，因为它会让
  「第 2 版」在两次部署之间是两份不同的东西，而每一处引用都只写 ``(id, 2)``。

报的是 ``StoreError``（跟随 ``SceneStore`` 而不是 ``EvidenceStore``，理由写在
``ports/product_catalog.py`` 的模块 docstring 上）。而 ``StoreError`` 同时是「这个
库打不开」的类型，所以这里的判据**不只判类型**：还要求消息点名了出冲突的那一个
``(id, version)``。少了后半句，一次存储事故就能把一条冲突判据点亮成绿 —— 判据绿了，
而它要判的那件事根本没发生。那一半的判别力由 ``_ReportsWithoutNaming`` 对照。

``ProductLine`` 没有版本号（PSM §4.1 的「零行为分组」），所以它用 ``line_id``
本身当那个唯一版本 —— 同 id 不同内容同样报错。**不是覆盖**：覆盖会让「这一组的
成员什么时候变的」从记录里消失，而那一刻正是有人要查的东西。

## 这一份刻意**不**判什么

按 ``tests/test_evidence_store_contract.py`` 立下的规矩，契约里不许出现第一个例外，
所以「重开一个进程还能读到」这种**落盘独有**的能力不在这里判 —— 内存实现做不到，
而把它写进来就等于在契约里给内存实现开一个口子。那是
``tests/test_sqlite_product_catalog.py`` 的事。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from marketops.domain.errors import StoreError
from marketops.domain.product import (
    ClaimRef,
    OfferVersion,
    ProductLine,
    ProductRef,
    ProductStatus,
    ProductVersion,
    QuotedPrice,
    RegionVariant,
    SourceArtifactRef,
)
from marketops.ports.product_catalog import ProductCatalog

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
WORKSPACE = "probe-workspace"
PRODUCT_ID = "probe-product"
OFFER_ID = "probe-offer"
LINE_ID = "probe-line"

#: 两个实现。新增一个实现时，这里是**唯一**要改的地方。
IMPLEMENTATION_PARAMS: tuple[str, ...] = ("sqlite", "memory")


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def catalog(request: pytest.FixtureRequest, tmp_path: Path) -> ProductCatalog:
    if request.param == "sqlite":
        from marketops.adapters.sqlite.product_catalog import SqliteProductCatalog

        return SqliteProductCatalog(tmp_path / "marketops.db")
    from marketops.adapters.memory import InMemoryProductCatalog

    return InMemoryProductCatalog()


def _material(artifact_id: str = "probe-spec") -> SourceArtifactRef:
    return SourceArtifactRef(
        artifact_id=artifact_id,
        locator="artifact-store://probe/spec.pdf",
        content_sha256="a" * 64,
        fetched_at=NOW,
        media_type="application/pdf",
        note="探针",
    )


def _version(version: int = 1, **overrides: object) -> ProductVersion:
    """一份**填得最满**的产品版本。

    填满是刻意的：判往返时用一份各格都空的样板，会让「漏写了几格」这件事看不出来。
    """
    fields: dict[str, object] = {
        "product_id": PRODUCT_ID,
        "version": version,
        "display_name": "探针产品",
        "capabilities": ("surface-prep", "aftercare"),
        "limits": ("no-synthetic-turf",),
        "units": "litre",
        "region_variants": (RegionVariant(market="GB", capabilities=("aftercare",)),),
        "source_refs": (_material(),),
        "unknowns": ("cure-time-in-humid-climate",),
        "owner": "probe-owner",
    }
    fields.update(overrides)
    return ProductVersion(**fields)  # type: ignore[arg-type]


def _ready_version(version: int = 1) -> ProductVersion:
    return _version(
        version,
        status=ProductStatus.READY,
        approved_by="probe-approver",
        approved_at=NOW,
    )


def _offer(version: int = 1, **overrides: object) -> OfferVersion:
    fields: dict[str, object] = {
        "offer_id": OFFER_ID,
        "version": version,
        "workspace": WORKSPACE,
        "name": "探针方案",
        "purpose": "launch",
        "product_refs": (ProductRef(product_id=PRODUCT_ID, version=1),),
        "markets": ("GB",),
        "locales": ("en",),
        "approved_claim_refs": (ClaimRef(claim_id="probe-claim", revision=1),),
        "price": QuotedPrice(
            currency="GBP",
            amount_minor=1250,
            basis="per litre",
            source_ref="probe-quote-2026",
            quoted_at=NOW,
        ),
        "owner": "probe-owner",
    }
    fields.update(overrides)
    return OfferVersion(**fields)  # type: ignore[arg-type]


def _line(**overrides: object) -> ProductLine:
    fields: dict[str, object] = {
        "line_id": LINE_ID,
        "workspace": WORKSPACE,
        "name": "探针产品线",
        "customer_problem": "surface-prep",
        "capability_family": "turf-care",
        "owner": "probe-owner",
        "product_refs": (ProductRef(product_id=PRODUCT_ID, version=1),),
        "approved_material_refs": ("probe-spec",),
    }
    fields.update(overrides)
    return ProductLine(**fields)  # type: ignore[arg-type]


def _assert_a_conflict_is_reported(catalog: ProductCatalog) -> None:
    """「同 ``(id, version)`` 不同内容必须报错」这条判据**的本体**。

    它**自己**断言，而不是让调用方用 ``pytest.raises`` 接住那个异常 —— 因为
    ``test_the_conflict_criteria_actually_discriminate`` 要拿故意坏掉的实现来调
    它：判据藏在一个 ``pytest.raises`` 里时，那个正对照只能测出「这里有个异常」，
    测不出**是哪条判据**要求的。

    报的是 ``StoreError``，而它同时是「库打不开」的类型，所以这里连**回执点名了
    哪一版**一起判 —— 只判类型的话，一次存储事故会把这条判据点亮成绿。
    """
    catalog.put_version(_version(1))
    try:
        catalog.put_version(_version(1, display_name="改过名字的探针产品"))
    except StoreError as exc:
        assert PRODUCT_ID in str(exc), (
            f"冲突报的是 StoreError，但回执里没有点名产品：{exc!r}。"
            "StoreError 也承载「库打不开」，不分主语的回执让两件处置相反的事共用"
            "一个名字，读日志的人查不出是哪一版出了事。"
        )
        return
    raise AssertionError(
        "同 (product_id, version) 写入了不同的内容，却没有报冲突。"
        "那会让「第 2 版」在两次部署之间是两份不同的东西，"
        "而每一处引用都只写 (id, 2)。"
    )


def _assert_a_line_conflict_is_reported(catalog: ProductCatalog) -> None:
    """同一条纪律落在没有版本号的 :class:`ProductLine` 上（``line_id`` 即唯一版本）。"""
    catalog.put_line(_line())
    try:
        catalog.put_line(_line(name="改过名字的探针产品线"))
    except StoreError as exc:
        assert LINE_ID in str(exc), f"回执里没有点名产品线：{exc!r}"
        return
    raise AssertionError("同 line_id 写入了不同的内容，却没有报冲突。")


def _assert_an_offer_conflict_is_reported(catalog: ProductCatalog) -> None:
    """同一条纪律落在 :class:`OfferVersion` 上（它**有**版本号，形状与产品版本同）。"""
    catalog.put_offer(_offer(1))
    try:
        catalog.put_offer(_offer(1, name="改过名字的探针方案"))
    except StoreError as exc:
        assert OFFER_ID in str(exc), f"回执里没有点名 Offer：{exc!r}"
        return
    raise AssertionError("同 (offer_id, version) 写入了不同的内容，却没有报冲突。")


class _ConflictsAreIgnored(ProductCatalog):
    """**第一条**坏路径，只给正对照用：**根本不报错**。

    ``put_version`` 与 ``put_line`` 把两次写入都当成幂等（它们甚至不存）。其余五格
    不参与那两条判据，于是老实抛 ``NotImplementedError`` —— 这同时把「端口上到底有
    几格」写成了一份活的见证：``ProductCatalog`` 少了任何一格，这个类就不会再是
    它的合法子类，正对照会以 ``TypeError`` 而不是 ``AssertionError`` 失败。
    """

    def put_version(self, version: ProductVersion) -> None:
        return None

    def get_version(self, product_id: str, version: int) -> ProductVersion | None:
        raise NotImplementedError

    def versions(self, product_id: str) -> tuple[ProductVersion, ...]:
        raise NotImplementedError

    def put_offer(self, offer: OfferVersion) -> None:
        return None

    def get_offer(self, offer_id: str, version: int) -> OfferVersion | None:
        raise NotImplementedError

    def put_line(self, line: ProductLine) -> None:
        return None

    def get_line(self, line_id: str) -> ProductLine | None:
        raise NotImplementedError


class _ReportsWithoutNaming(ProductCatalog):
    """**第二条**坏路径：报了错，但回执里没有主语。

    只对照「不报错」那一半会漏掉这一半 —— ``StoreError`` 也承载「库打不开」，一个
    把任何失败都包成 ``StoreError("写不进去")`` 的实现，在「只判类型」的判据下全绿，
    而读日志的人无从知道是哪一版出了事。

    它**第一次写成功、之后才报错**：否则三条判据会在第一行就撞上异常，正对照拿到的
    是 ``StoreError`` 而不是它要找的 ``AssertionError``，看着像通过了。
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def _write_once(self, key: str) -> None:
        if key in self._seen:
            raise StoreError("写不进去")
        self._seen.add(key)

    def put_version(self, version: ProductVersion) -> None:
        self._write_once(f"v:{version.product_id}:{version.version}")

    def get_version(self, product_id: str, version: int) -> ProductVersion | None:
        raise NotImplementedError

    def versions(self, product_id: str) -> tuple[ProductVersion, ...]:
        raise NotImplementedError

    def put_offer(self, offer: OfferVersion) -> None:
        self._write_once(f"o:{offer.offer_id}:{offer.version}")

    def get_offer(self, offer_id: str, version: int) -> OfferVersion | None:
        raise NotImplementedError

    def put_line(self, line: ProductLine) -> None:
        self._write_once(f"l:{line.line_id}")

    def get_line(self, line_id: str) -> ProductLine | None:
        raise NotImplementedError


def test_the_contract_runs_against_every_implementation() -> None:
    """清单本身要有判据，否则删掉一个实现之后契约照样全绿。"""
    assert IMPLEMENTATION_PARAMS == ("sqlite", "memory"), (
        f"实现清单变了：{IMPLEMENTATION_PARAMS}。"
        "少一个实现而契约仍绿，等于那条契约只覆盖了剩下的那一个。"
    )


def test_a_product_version_reads_back_field_for_field(catalog: ProductCatalog) -> None:
    """整对象相等，不是逐格抽查 —— 少写一格时逐格抽查要看运气。"""
    version = _ready_version()
    catalog.put_version(version)
    assert catalog.get_version(PRODUCT_ID, 1) == version


def test_an_unknown_version_reads_back_as_none(catalog: ProductCatalog) -> None:
    """不存在返回 ``None``，**不是**抛异常 —— ``ports/scenes.py`` 逐字同一条。"""
    assert catalog.get_version(PRODUCT_ID, 7) is None
    assert catalog.get_version("probe-absent", 1) is None


def test_rewriting_the_same_version_with_the_same_content_is_idempotent(
    catalog: ProductCatalog,
) -> None:
    """重启后重放一批登记不该报错，也不该多出一行。"""
    catalog.put_version(_version(1))
    catalog.put_version(_version(1))
    assert len(catalog.versions(PRODUCT_ID)) == 1


def test_rewriting_the_same_version_with_different_content_fails(
    catalog: ProductCatalog,
) -> None:
    _assert_a_conflict_is_reported(catalog)
    #: 失败的那一次**一个字节都没写** —— 留下的仍是原来那一份。
    assert catalog.get_version(PRODUCT_ID, 1) == _version(1)


def test_versions_are_listed_in_ascending_order(catalog: ProductCatalog) -> None:
    for version in (3, 1, 2):
        catalog.put_version(_version(version))
    assert [item.version for item in catalog.versions(PRODUCT_ID)] == [1, 2, 3]


def test_an_offer_reads_back_with_its_quoted_price(catalog: ProductCatalog) -> None:
    """报价是这一族里最容易在往返里丢掉的一格 —— 它是**可选**的。"""
    offer = _offer()
    catalog.put_offer(offer)
    read_back = catalog.get_offer(OFFER_ID, 1)
    assert read_back == offer
    assert read_back is not None and read_back.price is not None
    assert read_back.price.amount_minor == 1250
    assert read_back.price.quoted_at == NOW


def test_an_offer_without_a_price_reads_back_without_one(
    catalog: ProductCatalog,
) -> None:
    """反面：没有报价的 Offer 要能原样往返，不许在往返里长出一个缺省报价。"""
    offer = _offer(price=None)
    catalog.put_offer(offer)
    read_back = catalog.get_offer(OFFER_ID, 1)
    assert read_back == offer
    assert read_back is not None and read_back.price is None


def test_an_offer_conflict_is_reported(catalog: ProductCatalog) -> None:
    _assert_an_offer_conflict_is_reported(catalog)
    assert catalog.get_offer(OFFER_ID, 1) == _offer(1)


def test_a_product_line_reads_back(catalog: ProductCatalog) -> None:
    line = _line()
    catalog.put_line(line)
    assert catalog.get_line(LINE_ID) == line


def test_a_product_line_conflict_is_reported(catalog: ProductCatalog) -> None:
    """``ProductLine`` 没有版本号，所以 ``line_id`` 自己就是那个唯一版本。"""
    _assert_a_line_conflict_is_reported(catalog)
    assert catalog.get_line(LINE_ID) == _line()


def test_putting_a_line_twice_with_the_same_content_is_idempotent(
    catalog: ProductCatalog,
) -> None:
    catalog.put_line(_line())
    catalog.put_line(_line())
    assert catalog.get_line(LINE_ID) == _line()


def test_a_product_without_a_line_reads_back(catalog: ProductCatalog) -> None:
    """``ProductLine`` 是**可选**分组（PSM §4.1）。

    登记一个产品版本不需要先有一条产品线 —— 少了这一条判据，一个把
    ``put_version`` 实现成「先查产品线存在」的实现在上面每一条上也全绿。
    """
    catalog.put_version(_version(1))
    assert catalog.get_version(PRODUCT_ID, 1) is not None
    assert catalog.get_line(LINE_ID) is None


def test_the_conflict_criteria_actually_discriminate() -> None:
    """正对照：三条冲突判据在**两种**故意坏掉的实现上都必须全部炸。

    两条坏路径各挡一半：``_ConflictsAreIgnored`` 挡「根本不报错」（冲突分支写成了
    ``pass``，而判据之所以绿只是因为某处别的原因抛了同一个异常）；``_ReportsWithoutNaming``
    挡「报了错但回执没有主语」。少了后者，``assert PRODUCT_ID in str(exc)`` 那半句
    会是一条没有执行器的散文 —— 写着，但从没有东西因它而红。
    """
    for broken in (_ConflictsAreIgnored(), _ReportsWithoutNaming()):
        for assert_it in (
            _assert_a_conflict_is_reported,
            _assert_an_offer_conflict_is_reported,
            _assert_a_line_conflict_is_reported,
        ):
            with pytest.raises(AssertionError):
                assert_it(broken)


#: 契约文件必须能被单独运行（``pytest tests/test_product_catalog_contract.py``）。
if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

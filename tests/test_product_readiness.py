"""b01-c 的判据：**缺什么要说得具体**（B01 关闭条件 8）。

权威句在 `docs/PRODUCT_LINE_AND_SCENE_MODEL.md:229`，逐字：

    启动前返回结构化 missing/invalid/awaiting_approval，不让模型读一段 traceback 猜。

而 B01 的关闭条件（`docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:104`）末句逐字是
「资料缺失返回具体**missing字段**，不用临时SQL救流程」。两句话合起来对「缺什么」这件事
提了三个要求，这一份判据逐条判它们：

1. **结构化**：三条清单各是一族类型化对象，不是一个字符串。所以判据断言的是
   ``field`` / ``subject`` 这些格，而不是去匹配某句话 —— 一句写在报文里的「缺能力」
   与被解析出来的一格，在回执上长得像，在下游完全不是一回事。
2. **具体到字段**：每一条都点名**哪一格**（`capabilities` / `limits` / `unknowns` /
   `source_refs` / `product_refs` / `approved_claim_refs` / `supersedes` …）。
   只报「这一版不完整」的回执，读的人下一步还是得去翻库。
3. **不用临时 SQL**：主语指不到时**拒**，而不是回一份空白的 readiness。空白的
   readiness 与「什么都齐」在回执上同形 —— 这是本文件里最要紧的反面判据。

## 三条清单是**分开**的：它们的下一步动作不同

* ``missing`` —— 下一步是**去补资料 / 去改引用**（B01 `:100` 逐字「新型号无核实资料
  只可 draft」）；
* ``invalid`` —— 下一步是**先修引用关系**：盘上有一条指着不存在的东西，补多少资料
  都修不好它；
* ``awaiting_approval`` —— 什么都不用补，下一步是**人签字**。

前两张**不互斥**（草案可以又缺东西又没人签，两张都报才诚实），而第三张与 ``status``
绑死：非 ``draft`` 不许有它（`test_a_non_draft_reading_cannot_await_a_signature`）。

## ``at`` 是承重的，所以它有一对判据

到期判定必然是「相对于某个时刻」的，而一份读数里只能有一个时刻（`application/
run_preview.py` 的 `run_preview` 逐字「**必填、且只有一个**」）。所以这里两头都判：
naive 时刻**拒**（`test_the_reading_refuses_a_naive_moment`），以及**同一份盘上状态
在两个时刻读出两个答案**（`test_the_same_board_read_at_two_moments_gives_two_answers`）
—— 后一条是前一类的反面：一个把 ``at`` 收下却不用的实现，在别的判据下全绿。

## ``not_reported`` 判的是**差分**，不是「一条常量清单」

照 ``run_preview._not_reported`` 的先例（那段 docstring 逐字：「**减法**，不是重抄也不是
并集」）：与引用了什么无关的那几条在两条读法里是**同一个对象**（逐字引用），而
「引用的那一版读不出来」只在引的不是最新版时出现。三条判据逐条钉住那一对差分
（最新版 + 未过期 ⇒ 什么都不报；最新版 + 过期 ⇒ 报缺失；旧版 ⇒ 报 not_reported
**且不许拿最新版的期限顶替**）。

## 这一份判的是**读**：判据里的写全部走端口

夹具里那些 ``put_*`` 走的是 ``ProductCatalog`` 端口与 ``OutreachStore`` 端口，
所以这一份**不重测**存储（那是 ``tests/test_product_catalog_contract.py`` 与
``tests/test_sqlite_product_catalog.py`` 的事），也不重测 ``applies_to`` 的四轴
（那是 ``tests/test_domain_product.py:384-465`` 的 6 条）。这一份只判：给定盘上
这些东西，读数是不是一个能照着做事的东西。
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import InMemoryOutreachStore, InMemoryProductCatalog
from marketops.application.product_readiness import (
    NOT_REPORTED,
    ProductReadiness,
    ReadinessGap,
    SubjectKind,
    offer_version_readiness,
    product_version_readiness,
    unknown_version_refusal,
)
from marketops.domain.outreach import ApprovedClaim
from marketops.domain.product import (
    ClaimRef,
    OfferVersion,
    ProductRef,
    ProductStatus,
    ProductVersion,
    QuotedPrice,
    RegionVariant,
    SourceArtifactRef,
)

NOW = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
#: 一份读数取在**晚于** ``NOW`` 的一刻 —— 用来证明时刻是承重的。
LATER = NOW + timedelta(days=30)
PRODUCT_ID = "probe-product"
OFFER_ID = "probe-offer"
CLAIM_ID = "probe-claim"


# ----------------------------------------------------------------------
# 夹具：一份最小的盘上状态
# ----------------------------------------------------------------------


def _material(artifact_id: str = "probe-spec") -> SourceArtifactRef:
    return SourceArtifactRef(
        artifact_id=artifact_id,
        locator="artifact-store://probe/spec.pdf",
        content_sha256="a" * 64,
        fetched_at=NOW,
        media_type="application/pdf",
    )


def _version(version: int = 1, **overrides: object) -> ProductVersion:
    """一份**各格都填满**的产品版本。

    填满是刻意的：一张少了几格的样板会让「这一条判据到底判的是哪一格」看不出来 ——
    一个只报了 ``capabilities`` 的实现与一个报了对的实现，在缺格的样板上读起来一样。
    §4.2 第 1 步点名的「用途限制」就是 ``limits``，所以它也在这一份里。
    """
    fields: dict[str, object] = {
        "product_id": PRODUCT_ID,
        "version": version,
        "display_name": "探针产品",
        "capabilities": ("surface-prep",),
        "limits": ("indoor-use-only",),
        "units": "litre",
        "source_refs": (_material(),),
        "owner": "probe-owner",
    }
    fields.update(overrides)
    return ProductVersion(**fields)  # type: ignore[arg-type]


def _ready_version(version: int = 1, **overrides: object) -> ProductVersion:
    """已核对的那一份。``overrides`` 收在字典里再展开 —— 直接当实参传会让
    ``status=RETIRED`` 这种覆盖撞上「重复给值」，而那个 ``TypeError`` 看起来像
    实现坏了。

    **``supersedes`` 不在这里推**：域层没有把它绑到 ``version - 1`` 的校验器
    （b01-b 是在 MCP 登记面上推的，见 ``_superseded_by``），所以夹具要显式写。
    """
    fields: dict[str, object] = {
        "status": ProductStatus.READY,
        "approved_by": "probe-approver",
        "approved_at": NOW,
    }
    fields.update(overrides)
    return _version(version, **fields)


def _offer(version: int = 1, **overrides: object) -> OfferVersion:
    fields: dict[str, object] = {
        "offer_id": OFFER_ID,
        "version": version,
        "workspace": "probe-workspace",
        "name": "探针方案",
        "purpose": "launch",
        "product_refs": (ProductRef(product_id=PRODUCT_ID, version=1),),
        "markets": ("GB",),
        "locales": ("en",),
        "approved_claim_refs": (ClaimRef(claim_id=CLAIM_ID, revision=1),),
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


def _ready_offer(version: int = 1, **overrides: object) -> OfferVersion:
    fields: dict[str, object] = {
        "status": ProductStatus.READY,
        "approved_by": "probe-approver",
        "approved_at": NOW,
    }
    fields.update(overrides)
    return _offer(version, **fields)


def _claim(revision: int = 1, **overrides: object) -> ApprovedClaim:
    fields: dict[str, object] = {
        "claim_id": CLAIM_ID,
        "revision": revision,
        "text": "这一版的能力经核对",
        "source_ref": "probe-spec",
        "approved_by": "probe-approver",
        "approved_at": NOW,
    }
    fields.update(overrides)
    return ApprovedClaim(**fields)  # type: ignore[arg-type]


class _Board:
    """一份盘上状态：产品目录 + 已批准说法，外加两个「读得到它们」的入口。"""

    def __init__(self) -> None:
        self.catalog = InMemoryProductCatalog()
        self.outreach = InMemoryOutreachStore()

    def put_claim(self, claim: ApprovedClaim) -> None:
        self.outreach.put_claim(claim)

    def read_product(
        self,
        product_id: str = PRODUCT_ID,
        version: int = 1,
        *,
        at: datetime = NOW,
    ) -> ProductReadiness | None:
        return product_version_readiness(
            self.catalog, product_id=product_id, version=version, at=at
        )

    def read_offer(
        self,
        offer_id: str = OFFER_ID,
        version: int = 1,
        *,
        at: datetime = NOW,
    ) -> ProductReadiness | None:
        return offer_version_readiness(
            self.catalog,
            offer_id=offer_id,
            version=version,
            claims=self.outreach,
            at=at,
        )


@pytest.fixture
def board() -> _Board:
    return _Board()


def _read(reading: ProductReadiness | None) -> ProductReadiness:
    """把 ``None`` 挡在判据之外：这一份里的主语都是**登记过的**。

    「读不到就返回 ``None``」本身另有三条判据（见「主语与拒词」那一节），所以这里
    写成一个断言不是为了掩盖形状，而是为了让别的判据失败时说得出「它读了哪一份」。
    """
    assert reading is not None, "这一条判据里的主语是登记过的，读不到就是实现错了"
    return reading


def _fields(gaps: tuple[ReadinessGap, ...]) -> set[str]:
    return {gap.field for gap in gaps}


def _all_fields(reading: ProductReadiness) -> set[str]:
    """这一份读数三张清单上出现过的所有格子名。"""
    return _fields((*reading.missing, *reading.invalid, *reading.awaiting_approval))


#: 每一族读数里 ``field`` 允许取的那些格子（逐字取域模型上的字段名）。
#: 完整的判据在 ``test_every_gap_field_is_a_grid_of_the_reading_itself``。
_PRODUCT_GRIDS = frozenset(
    {"capabilities", "limits", "units", "source_refs", "unknowns", "approved_by", "supersedes"}
)
_OFFER_GRIDS = frozenset(
    {
        "purpose",
        "markets",
        "locales",
        "product_refs",
        "approved_claim_refs",
        "approved_by",
        "supersedes",
    }
)


def _subjects(gaps: tuple[ReadinessGap, ...]) -> set[str]:
    return {gap.subject for gap in gaps}


def _find(gaps: tuple[ReadinessGap, ...], field: str) -> ReadinessGap:
    """这一格上的那一条（判据要求它存在且只有一条）。"""
    hits = [gap for gap in gaps if gap.field == field]
    assert len(hits) == 1, f"{field} 那一格上应该有且只有一条，实际 {hits!r}"
    return hits[0]


# ----------------------------------------------------------------------
# 一、每一条缺口都要**说得出是哪一格、以及为什么**
# ----------------------------------------------------------------------


def test_every_gap_names_a_field_and_says_why(board: _Board) -> None:
    """三条清单里的每一条都要带一个字段名与一句理由。

    这一条判的是**形状**，而它不是形式主义：``field`` 是条件 8 逐字要的「具体
    missing 字段」（机器能拿去查的那一格），``why`` 是给人读的那一句。缺了前者，
    调用方仍然要去翻库；缺了后者，回执变成一串没有理由的字段名，读的人不知道
    该补什么。
    """
    board.catalog.put_version(
        _version(capabilities=(), units="", unknowns=("cure-time-in-humid-climate",))
    )
    reading = _read(board.read_product())
    gaps = (*reading.missing, *reading.invalid, *reading.awaiting_approval)
    assert gaps, "这一版缺了三格、又自己声明了一条未知项，三张清单不该是空的"
    for gap in gaps:
        assert gap.subject, f"这一条缺口没有点名是哪一条记录：{gap!r}"
        assert gap.field, f"这一条缺口没有点名字段：{gap!r}"
        assert gap.why.strip(), f"这一条缺口没有说为什么：{gap!r}"
        assert "：" in gap.why or "。" in gap.why, (
            f"这一条理由不像一句话：{gap.why!r} —— 一句「缺能力」与一格字段名"
            "没有区别，而那正是这一条判据要挡的东西"
        )


def test_declared_unknowns_come_back_one_by_one(board: _Board) -> None:
    """这一版**自己声明**的未知项，逐条报出来。

    PSM §6 把「未知项」列为主档的一格。它与「不能做」是两格：前者要一份资料，
    后者要一次改设计（``domain/product.py`` 的 ``ProductVersion`` docstring 逐字）。
    所以它进 ``missing`` 而不是别的清单 —— 那正是「下一步是去补资料」。
    """
    board.catalog.put_version(
        _version(unknowns=("cure-time-in-humid-climate", "winter-overseeding"))
    )
    reading = _read(board.read_product())
    assert _fields(reading.missing) >= {"unknowns"}, (
        f"自己声明的未知项没有进 missing：{[gap.field for gap in reading.missing]}"
    )
    said = " ".join(gap.why for gap in reading.missing if gap.field == "unknowns")
    assert "cure-time-in-humid-climate" in said, (
        f"报的是「有未知项」而不是「哪几条未知项」：{said!r} —— 读的人下一步要"
        "去补的正是那一条，所以他必须看得到它"
    )
    assert "winter-overseeding" in said


def test_every_gap_field_is_a_grid_of_the_reading_itself(board: _Board) -> None:
    """``field`` 永远是**这一份读数的主语**上的一格 —— 不是被引用那条记录上的一格。

    这一条判的是**可操作性**：读的人手上能改的只有这一版（或者改它引的是谁），所以
    每一行都必须落在一张他动得了的表上。指着一条说法说「它的 ``revision`` 有问题」
    看起来更精确，实际是把他送到一个没有东西可改的地方 —— 而缺口的主语仍然是那条
    说法（``subject`` 那一格照旧说得出「是关于谁的」，见 ``ReadinessGap`` 的 docstring）。

    同一条也挡住「同一格的两种命名」：``field`` 不是自由文本，而是域模型上那几格
    名字之一 —— 否则调用方没法按字段名去查，而条件 8 要的「具体 missing 字段」
    又退化成一句话。
    """
    board.catalog.put_version(
        _version(version=3, capabilities=(), limits=(), supersedes=2)
    )
    board.put_claim(_claim())
    board.catalog.put_offer(
        _offer(
            markets=(),
            approved_claim_refs=(ClaimRef(claim_id="probe-ghost", revision=1),),
        )
    )
    product_reading = _read(board.read_product(version=3))
    offer_reading = _read(board.read_offer())
    assert _all_fields(product_reading) <= _PRODUCT_GRIDS, (
        f"产品那一侧出现了不在网格里的字段名：{_all_fields(product_reading)}"
    )
    assert _all_fields(offer_reading) <= _OFFER_GRIDS, (
        f"方案那一侧出现了不在网格里的字段名：{_all_fields(offer_reading)}"
    )
    assert _all_fields(product_reading) and _all_fields(offer_reading), (
        "这一条判据要在有缺口的状态上跑，否则它一条都没看"
    )
    assert "claim:probe-ghost@1" in _subjects(offer_reading.invalid), (
        "缺口的主语照旧要说得出它是关于谁的"
    )


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"capabilities": ()}, "capabilities"),
        ({"limits": ()}, "limits"),
        ({"units": ""}, "units"),
        ({"source_refs": ()}, "source_refs"),
    ],
)
def test_a_missing_axis_is_named_by_its_own_field(
    board: _Board, override: dict[str, object], expected: str
) -> None:
    """主档的每一格缺了，报的是**那一格的名字**。

    四格的处置各不同（去要能力资料 / 去写用途限制 / 去核对单位 / 去补出处），
    所以回执必须分得开。``limits`` 那一格是 §4.2 第 1 步点名的「用途限制」——
    它是这一族里最容易漏掉的一格：别的三格缺了会让这一版**用不了**，而空着的
    ``limits`` 会让它看起来**什么都能干**。
    """
    board.catalog.put_version(_version(**override))
    reading = _read(board.read_product())
    assert _fields(reading.missing) >= {expected}, (
        f"{expected} 缺了却没有被点名：{[gap.field for gap in reading.missing]}"
    )


# ----------------------------------------------------------------------
# 二、资料齐了但没批准 —— 下一步是人
# ----------------------------------------------------------------------


def test_a_complete_draft_awaits_a_signature_and_nothing_else(board: _Board) -> None:
    """各格齐全的 draft：``missing`` / ``invalid`` 都空，只有一条「等签字」。

    「登记不等于开工」这句话在本仓的执行器就是这一条：登记成功的下一步不是
    自动可用，而是**某个人**。而没有得到这一条之前，「资料齐了」与「能用」在
    回执上同形。
    """
    board.catalog.put_version(_version())
    reading = _read(board.read_product())
    assert reading.missing == (), f"这一版各格齐全，missing 不该有东西：{reading.missing}"
    assert reading.invalid == (), f"这一版没有坏引用，invalid 不该有东西：{reading.invalid}"
    assert [gap.field for gap in reading.awaiting_approval] == ["approved_by"], (
        f"等签字的理由是批准那一格：{reading.awaiting_approval}"
    )
    assert reading.status is ProductStatus.DRAFT
    assert not reading.usable, "一份还没批准的版本不能拿去启动"


def test_a_ready_and_complete_version_is_usable(board: _Board) -> None:
    """这一半是同一把尺子的另一半：齐全且已批准 ⇒ 三张清单全空、``usable`` 为真。

    没有这一条，一个「什么都报缺」的实现会全绿 —— 而那比不报还坏。
    """
    board.catalog.put_version(_ready_version())
    reading = _read(board.read_product())
    assert (reading.missing, reading.invalid, reading.awaiting_approval) == ((), (), ())
    assert reading.usable


def test_a_retired_version_is_unusable_and_carries_no_gap(board: _Board) -> None:
    """归档**不是一种缺口**，是一种读数：三张清单都空、``usable`` 仍是假。

    PSM §4.1 逐字「产品归档停止创建新的 Offer，不删除历史结果」。所以它既没有
    「缺什么」也没有「等谁」，只是不能再被新的启动引用 —— 把这一条硬塞进三张
    清单里的任何一张，都会让「补资料」「改引用」「找人签字」这三个下一步里
    冒出一个修不好的。
    """
    board.catalog.put_version(
        _ready_version(status=ProductStatus.RETIRED, limits=(), units="")
    )
    reading = _read(board.read_product())
    assert (reading.missing, reading.invalid, reading.awaiting_approval) == ((), (), ()), (
        "归档那一版即便有几格是空的，也不再问它缺什么 —— 没有人会去补它"
    )
    assert reading.status is ProductStatus.RETIRED
    assert not reading.usable


def test_a_draft_that_also_has_gaps_carries_both_tables(board: _Board) -> None:
    """又缺东西又没人签的草案：**两张清单都报**。

    这是「三张清单不互斥」那半句的判据。把等签字藏起来（只在别的表空时才报）会
    让读的人以为「补完就能跑」；反过来把缺失藏起来，会让人去找一位负责人签一份
    签了也跑不了的草案。两种藏法都会让下一步做错。
    """
    board.catalog.put_version(_version(limits=()))
    reading = _read(board.read_product())
    assert _fields(reading.missing) == {"limits"}
    assert [gap.field for gap in reading.awaiting_approval] == ["approved_by"]
    assert not reading.usable


# ----------------------------------------------------------------------
# 三、**不判**的那几项：价格与逐市场收窄（PSM `:19` 与 `:156`）
# ----------------------------------------------------------------------


def test_an_absent_price_is_not_a_gap(board: _Board) -> None:
    """有没有报价**完全不影响**这份读数 —— 两份只差价格的状态读出同一个答案。

    PSM `:156` 逐字「无价格可照样研究」、`:19` 逐字「价格可缺省而**不可编造**」。
    两句话合起来的工程含义是：``price=None`` 是一个**被批准的状态**，不是待办。
    所以判据不是「价格没进 missing」（那只挡了一半），而是**只差价格的两份状态
    读出逐格相同的三张表** —— 一个把价格接进任何一个判定的实现都会在这条上红，
    而不只是那个多报一格的实现。

    （``QuotedPrice`` 的 docstring 逐字写着为什么这一格不能编：那是这一族里最贵的
    一种错，而「缺价格」一旦被报成缺口，下一步就是去编一个价。）
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim())
    board.catalog.put_offer(_ready_offer())
    priced = _read(board.read_offer())
    board.catalog.put_offer(_ready_offer(version=2, price=None))
    unpriced = _read(board.read_offer(version=2))
    assert (
        priced.missing,
        priced.invalid,
        priced.awaiting_approval,
        priced.not_reported,
        priced.usable,
    ) == (
        unpriced.missing,
        unpriced.invalid,
        unpriced.awaiting_approval,
        unpriced.not_reported,
        unpriced.usable,
    ), "只差价格的两份状态给出了不同的读数 —— 价格不该参与任何一格判定"
    assert unpriced.usable, "无价格可照样研究 —— 而这一版别的都齐了"
    assert "price" not in _all_fields(unpriced)


def test_region_variants_are_not_a_gap(board: _Board) -> None:
    """``region_variants`` 缺席**不是**缺口：它是**收窄**语义，不是一格待补。

    ``domain/product.py`` 的 ``_region_variants_narrow_only`` 把它定义成「在主档
    之上收窄」，所以「一格都没有」的读法是「用主档那一份」，而不是「这一版少了
    逐市场的说明」。判据与价格那条同款：只差这一格的两份状态读出同一个答案。
    """
    board.catalog.put_version(_ready_version())
    without = _read(board.read_product())
    board.catalog.put_version(
        _ready_version(
            version=2,
            region_variants=(RegionVariant(market="GB", capabilities=()),),
        )
    )
    with_variant = _read(board.read_product(version=2))
    assert (
        without.missing,
        without.invalid,
        without.awaiting_approval,
        without.not_reported,
        without.usable,
    ) == (
        with_variant.missing,
        with_variant.invalid,
        with_variant.awaiting_approval,
        with_variant.not_reported,
        with_variant.usable,
    ), "逐市场收窄表进了判定 —— 它是收窄，缺席的意思是「用主档那一份」"
    assert "region_variants" not in _all_fields(with_variant)


# ----------------------------------------------------------------------
# 四、坏引用：补资料修不好的那一类
# ----------------------------------------------------------------------


def test_an_offer_citing_a_product_version_that_is_not_registered_is_invalid(
    board: _Board,
) -> None:
    """引用指不到任何一版 —— 这是 ``invalid``，而且要点名**指到了哪里**。

    b01-b 把引用做成 ``(product_id, version)`` 这一对（理由写在 ``ProductRef`` 的
    docstring 上）。这一对指不到东西时，报告上必须看得见**是那一对**：只说
    「有坏引用」的回执，读的人还是得自己拿 id 去库里比对。
    """
    board.catalog.put_offer(
        _offer(product_refs=(ProductRef(product_id="probe-ghost", version=1),))
    )
    reading = _read(board.read_offer())
    assert _fields(reading.invalid) >= {"product_refs"}, (
        f"指不到的引用没有进 invalid：{[gap.field for gap in reading.invalid]}"
    )
    assert "product:probe-ghost@1" in _subjects(reading.invalid), (
        f"没有点名指到了哪里：{[gap.subject for gap in reading.invalid]}"
    )


def test_a_draft_offer_citing_a_draft_product_version_is_not_a_bad_reference(
    board: _Board,
) -> None:
    """一份 draft 引用一份还没批准的产品版本：**不报坏引用**。

    这一条判的是「指得到、但还没批准」与「指不到」的分界。草稿之间互相引用是
    登记过程中的常态（PSM §4.2 第 1 步逐字「资料不全可保存 draft」），把它读成
    坏引用会让一条正常在途的登记在回执上显示成一个错误。

    而**等签字那一条**报的是 ``approved_by``（找负责人签这一份方案），不是
    ``product_refs``：这一版现在缺的是一个人，不是一条引用 —— 引用那条要等**这份
    方案自己变成 ready** 的那一刻才成立（下一条判据正是那一刻）。
    """
    board.catalog.put_version(_version())  # draft
    board.put_claim(_claim())  # 说法是先登记的，两边都还是草稿
    board.catalog.put_offer(_offer())
    reading = _read(board.read_offer())
    assert reading.invalid == (), f"草稿引用草稿不是坏引用：{reading.invalid}"
    assert [gap.field for gap in reading.awaiting_approval] == ["approved_by"]


def test_a_ready_offer_citing_an_unapproved_product_version_is_invalid(
    board: _Board,
) -> None:
    """同一份引用，主语变成**已批准**之后就成了坏引用。

    条件与上一条只差主语的状态，而那一格正是判据的全部：PSM §4.2 第 1 步逐字
    「登记/选择**已批准** ProductVersion 与 OfferVersion」。一份已批准的方案指着
    一份没人核对过的能力，就是「宣传未证实能力」（B01 `:100` 逐字反对的那件事）。
    """
    board.catalog.put_version(_version())  # draft
    board.put_claim(_claim())
    board.catalog.put_offer(_ready_offer())
    reading = _read(board.read_offer())
    assert _fields(reading.invalid) >= {"product_refs"}, (
        "已批准的方案引用了未批准的产品版本，这不是一条干净的登记："
        f"{[gap.field for gap in reading.invalid]}"
    )
    assert "product:probe-product@1" in _subjects(reading.invalid)
    assert not reading.usable


def test_an_offer_citing_a_claim_that_is_not_registered_is_invalid(board: _Board) -> None:
    """引用的说法在盘上根本没有 —— 与指不到的产品版本同一类。

    PSM §4.2 第 1 步把「claim 期限」列进启动前要检查的五样里。这一条判的是它的
    **前一半**：说法本身在不在。后一半（期限）由另外三条差分判据判
    （见「五」那一节）—— 两半分开，是因为它们的处置不同：一条要去补登记，
    一条指向端口上缺的一个读法。
    """
    board.catalog.put_version(_ready_version())
    board.catalog.put_offer(_ready_offer())  # 没有任何一条 claim 得到登记
    reading = _read(board.read_offer())
    assert _fields(reading.invalid) >= {"approved_claim_refs"}, (
        f"引用了不存在的说法：{[gap.field for gap in reading.invalid]}"
    )
    assert f"claim:{CLAIM_ID}@1" in _subjects(reading.invalid), (
        f"没有点名是哪一条说法：{[gap.subject for gap in reading.invalid]}"
    )


def test_a_citation_newer_than_every_registered_revision_is_invalid(board: _Board) -> None:
    """引用记的版本**比盘上最新版还新** —— 那是编出来的版号，不是坏运气。

    ``ClaimRef`` 是 ``(claim_id, revision)`` 这一对，而 ``revision`` 是调用方写下来的
    一个字面量。写大一位（第 3 版而盘上只有第 2 版）在读取面上与「那一条说法真的
    有过第 3 版、后来被删了」长得像 —— 而后者不成立：两个实现的 ``_latest`` 都逐字
    写着「只挑最新，**不删旧的**」。所以这一格只能是编的，而它必须进 ``invalid``
    而不是 ``missing``：补一版新的修不好一个写错的引用。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim(revision=2))
    board.catalog.put_offer(
        _ready_offer(approved_claim_refs=(ClaimRef(claim_id=CLAIM_ID, revision=3),))
    )
    reading = _read(board.read_offer())
    gap = _find(reading.invalid, "approved_claim_refs")
    assert gap.subject == f"claim:{CLAIM_ID}@3", f"点名的是被引的那一对：{gap.subject}"
    assert "第 2 版" in gap.why, f"理由里要说清盘上最新到第几版：{gap.why!r}"
    assert not reading.usable


def test_a_broken_supersedes_link_is_invalid(board: _Board) -> None:
    """版本链在盘上断了一节。

    b01-b 把 ``supersedes`` 由版本号推出来（``None if version == 1 else version - 1``），
    而 ``domain/product.py`` 里**没有**把两者绑在一起的校验器 —— 所以「第 3 版说自己
    取代第 2 版，而盘上没有第 2 版」这件事今天只有读盘才看得见。这一条就是那个读者。

    报的那一格是 ``supersedes``（这一版自己身上要改的那一格），主语仍是这一版 ——
    断链的另一头不在盘上，所以它没有一个可以指着的主语（见
    ``test_every_gap_field_is_a_grid_of_the_reading_itself``）。
    """
    board.catalog.put_version(_version(version=3, supersedes=2))
    reading = _read(board.read_product(version=3))
    gap = _find(reading.invalid, "supersedes")
    assert gap.subject == "product:probe-product@3", "要改的是这一版"
    assert "第 2 版" in gap.why, f"理由要点名断的是哪一节：{gap.why!r}"


# ----------------------------------------------------------------------
# 五、``not_reported`` 是**差分**：减出来的，不是一条常量清单
# ----------------------------------------------------------------------


def test_the_sources_axis_is_declared_unreported_in_both_readings(board: _Board) -> None:
    """「来源工件在不在盘上」答不上来，而两条读法回的是**同一个对象**。

    参数是量具的形状：``ProductVersion.source_refs`` 里的 ``SourceArtifactRef``
    自述完整（locator、哈希、取回时刻），而全仓**没有任何一处**按 ``artifact_id``
    去读那件工件 —— 所以「出处写下来了」与「出处还在」在这一层是同一格。

    断言用 ``is`` 而不是 ``==``：两份各写一遍的清单在写成的那天是相等的，分家的
    那天不相等 —— 而「同一句话」在这里就是同一个对象（``run_preview`` 的
    ``_not_reported`` 逐字引用计划层那一批，靠的也是同一件事）。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim())
    board.catalog.put_offer(_ready_offer())
    product_reading = _read(board.read_product())
    offer_reading = _read(board.read_offer())
    assert product_reading.kind is SubjectKind.PRODUCT
    assert offer_reading.kind is SubjectKind.OFFER
    assert product_reading.not_reported == NOT_REPORTED
    assert offer_reading.not_reported[0] is product_reading.not_reported[0], (
        "两条读法各写了一遍「答不上来的那一格」—— 它们会在某一天分家"
    )
    assert "artifact_id" in product_reading.not_reported[0].reason, (
        "那一条要点名缺的是**哪一个读法**，否则读的人以为没人做这件事"
    )
    assert product_reading.usable and offer_reading.usable, (
        "答不上一格**不等于**这一版不能用 —— 那会把一条量具的缺口读成一条业务的缺口"
    )


def test_a_live_citation_of_the_latest_revision_reports_nothing_extra(
    board: _Board,
) -> None:
    """引的是最新版、而它在 ``at`` 这一刻没过期 ⇒ 既没有缺口，也没有多出来的
    ``not_reported``。

    这一条与下面两条合起来是一对**差分**的三个端点。少了它，一个「凡引用都报一条
    答不上来」的实现会全绿 —— 而那份回执会让每一条在途的方案都看起来有一格读不出来。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim(expires_at=NOW + timedelta(days=90)))
    board.catalog.put_offer(_ready_offer())
    reading = _read(board.read_offer())
    assert reading.missing == (), f"这一条引用作数：{reading.missing}"
    assert reading.not_reported == NOT_REPORTED, (
        f"引的是最新版，没有读不出来的那一版：{[item.item for item in reading.not_reported]}"
    )
    assert reading.usable


def test_an_expired_citation_of_the_latest_revision_is_a_missing_gap(board: _Board) -> None:
    """引的是最新版、而它在 ``at`` 这一刻**已经过期** ⇒ 一条 ``missing``。

    归因来自 ``domain/outreach.py`` 的 ``ApprovedClaim.why_not_applicable``（四轴里
    期限那一轴）—— 这一条是它在本仓的**第一个生产消费者**。落在 ``missing`` 而不是
    ``invalid`` 是对的：要补的是一条**新的已批准版本**，不是修引用。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim(expires_at=NOW - timedelta(days=1)))
    board.catalog.put_offer(_ready_offer())
    reading = _read(board.read_offer())
    gap = _find(reading.missing, "approved_claim_refs")
    assert gap.subject == f"claim:{CLAIM_ID}@1"
    assert "过期" in gap.why or "已经过了" in gap.why, f"没说是期限那一轴：{gap.why!r}"
    assert reading.not_reported == NOT_REPORTED, "这一版引的是最新版，读得出来"
    assert not reading.usable


def test_a_superseded_citation_is_unreported_and_the_latest_is_not_substituted(
    board: _Board,
) -> None:
    """引的是**旧版**时：如实说读不出来，**不拿最新版的期限顶替**。

    这一条是这一族里最要紧的判据，构造刻意做成陷阱：第 1 版**还没过期**、第 2 版
    （盘上的最新版）**已经过期**。拿最新版顶替的实现会把一条**有效**的引用报成过期
    —— 而报告上那读起来像「资料有问题」，实际是这一层拿错了对象。反过来（第 2 版
    未过期而第 1 版过期）会漏报，两个方向都不行。

    所以：旧版那条引用落在 ``not_reported``（点名被引的版号与盘上最新版号），
    ``missing`` 保持为空、这一版仍然 ``usable`` —— 而**同一条说法引最新版**时
    （下半段）那条过期理由就如实报出来。同一份盘上状态、两个引用，两个答案。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim(revision=1, expires_at=NOW + timedelta(days=90)))
    board.put_claim(_claim(revision=2, expires_at=NOW + timedelta(days=10)))
    board.catalog.put_offer(
        _ready_offer(approved_claim_refs=(ClaimRef(claim_id=CLAIM_ID, revision=1),))
    )
    old = _read(board.read_offer(at=LATER))  # LATER 落在第 2 版之后、第 1 版之前
    assert old.missing == (), (
        f"第 1 版在这一刻还活着，不该报过期：{[gap.why for gap in old.missing]}"
    )
    assert old.invalid == (), f"第 1 版在盘上（只是不是最新版）：{old.invalid}"
    extra = old.not_reported[len(NOT_REPORTED) :]
    assert len(extra) == 1, (
        f"引了旧版却没说那一版读不出来：{[item.item for item in old.not_reported]}"
    )
    assert "第 1 版" in extra[0].item and "第 2 版" in extra[0].reason, (
        f"那一条要点名被引的是哪一版、盘上最新到哪一版：{extra[0]!r}"
    )
    assert "不拿最新版顶替" in extra[0].reason, (
        "理由要说清这一层拒绝顶替，以及为什么 —— 否则读的人会以为是没做"
    )
    assert "删" in extra[0].reason, "那一句要点名缺的是端口上的读法，不是盘上的记录"
    assert old.usable

    board.catalog.put_offer(
        _ready_offer(
            version=2,
            approved_claim_refs=(ClaimRef(claim_id=CLAIM_ID, revision=2),),
        )
    )
    newest = _read(board.read_offer(version=2, at=LATER))
    assert len(newest.not_reported) == len(NOT_REPORTED), "最新版读得出来"
    gap = _find(newest.missing, "approved_claim_refs")
    assert gap.subject == f"claim:{CLAIM_ID}@2", (
        "报的是被引的那一版（第 2 版），而不是被顶替过的那一版"
    )


def test_the_retirement_mark_decides_invalid_and_leaves_unreported_alone() -> None:
    """盘上形状逐字相同的两个世界，**只差 ``retired`` 一个位**：``invalid`` 从空
    变成一条，而 ``not_reported`` **逐字不变**。

    「退场」与「过期」是两个事件，这是那条的执行器。上面那条陷阱判据用的是**同一
    份**形状 —— 它测**过期**那一支：第 2 版只是不活了，第 1 版还活着，于是这一层
    如实说「你引的那一版读不出来」并拒绝拿最新版顶替。这一条把那个位翻过来，其余
    每一格不动。

    两件事一起判，因为**同一个 ``invalid`` 有两种错法**：该有时没有（把 ``retired``
    与「最新版不活」压成同一个判据，于是两个世界的读数一样）、不该有时有（「宁可
    错杀」，于是过期那一支也报 ``invalid``）。

    ``not_reported`` 那一条照旧报、且逐字不变，**不是重复报告**：它说的是端口缺一个
    按 ``(claim_id, revision)`` 读的方法 —— 那是**读法**那一格的事实，与盘上最新那
    一版为什么不活无关（过期也好、退场也好，第 1 版都读不出来）。两份处置（换掉这
    一条引用、补上那个读法）都要做，所以它们并存；把它们当互斥的一条判据，会因为
    出现了一个更重的判词就藏掉一条真话。
    """

    def read(*, withdrawn: bool) -> ProductReadiness:
        world = _Board()
        world.catalog.put_version(_ready_version())
        world.put_claim(_claim(revision=1, expires_at=NOW + timedelta(days=90)))
        world.put_claim(
            _claim(revision=2, expires_at=NOW + timedelta(days=10), retired=withdrawn)
        )
        world.catalog.put_offer(
            _ready_offer(approved_claim_refs=(ClaimRef(claim_id=CLAIM_ID, revision=1),))
        )
        # LATER 落在第 2 版之后、第 1 版之前。
        return _read(world.read_offer(at=LATER))

    expired = read(withdrawn=False)
    withdrawn = read(withdrawn=True)

    # 过期那一支：不报 ``invalid``。第 1 版在盘上（只是不是最新版），要补的是一份
    # 读得出来的版本，不是修引用。
    assert expired.invalid == (), f"只是过期，不该判引用不成立：{expired.invalid}"
    # 退场那一支：同一条引用进 ``invalid``，理由说的是退场。
    gap = _find(withdrawn.invalid, "approved_claim_refs")
    assert gap.subject == f"claim:{CLAIM_ID}@1", (
        "报的是**被引的那一版** —— 要动的是这一条引用，不是盘上最新那一版"
    )
    assert "退场" in gap.why, f"没说清这是退场那一件事：{gap.why!r}"
    # 而 ``not_reported`` 一个字没变：那个位**只**决定 ``invalid`` 那一格。
    assert withdrawn.not_reported == expired.not_reported, (
        f"退场那个位漏进了「读不出来」那一格：{withdrawn.not_reported}"
    )
    assert expired.missing == withdrawn.missing == (), "第 1 版这一刻还活着"
    assert expired.usable and not withdrawn.usable


# ----------------------------------------------------------------------
# 六、四轴的**归因**：哪一轴不作数，说的是哪一句
# ----------------------------------------------------------------------


def test_a_claim_scoped_to_another_market_is_reported_by_that_axis(board: _Board) -> None:
    """说法只写在 GB 里，而方案声明了 GB + FR ⇒ 报的是**市场那一句**。

    这一条判的是归因，不是布尔：``applies_to`` 只回一个「不适用」，而四种不适用
    要的下一步各不相同（这一种是给说法加市场作用域、或者别在这个市场用它）。
    PSM §4.2 第 1 步点名的「适用市场」正是这一轴。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim(markets=("GB",)))
    board.catalog.put_offer(_ready_offer(markets=("GB", "FR")))
    reading = _read(board.read_offer())
    gap = _find(reading.missing, "approved_claim_refs")
    assert "FR" in gap.why and "市场" in gap.why, f"归错轴了：{gap.why!r}"
    assert gap.subject == f"claim:{CLAIM_ID}@1", "缺口指着的应该是那条说法"


def test_a_claim_scoped_to_another_language_is_reported_by_that_axis(board: _Board) -> None:
    """说法只写在 en 里，而方案声明了 en + fr ⇒ 报的是**语言那一句**。

    市场与语言是两格（``locales`` 比的是主语言子标签，``en`` 而不是 ``en-GB``）——
    两格各自独立判，所以这一条的``why`` 里不许出现市场那一轴的措辞。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim(locales=("en",)))
    board.catalog.put_offer(_ready_offer(locales=("en", "fr")))
    reading = _read(board.read_offer())
    gap = _find(reading.missing, "approved_claim_refs")
    assert "fr" in gap.why and "语言" in gap.why, f"归错轴了：{gap.why!r}"
    assert "市场" not in gap.why, "语言那一轴不许借市场那一轴的说法"


def test_a_claim_bound_to_another_product_version_is_reported(board: _Board) -> None:
    """说法绑的是**另一版产品** ⇒ 报出来（``applies_to`` 答不了这一问）。

    这是那个**跨记录**的问题，不是 ``applies_to`` 的四轴：``applies_to(product=None)``
    逐字是「不限」，所以要问的是「这条说法绑的产品是不是这一版方案引用的一版」。
    两种误读各自有代价 —— 不判它，一条关于别的产品的话会挂在这一版方案上；而拿
    ``applies_to`` 去判，问到的会是「它绑的是不是它自己」这种恒真的问题。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim(product_ref=ProductRef(product_id="probe-other", version=1)))
    board.catalog.put_offer(_ready_offer())
    reading = _read(board.read_offer())
    gap = _find(reading.missing, "approved_claim_refs")
    assert "probe-other" in gap.why and PRODUCT_ID in gap.why, (
        f"理由里要同时看得见说法绑的是谁、方案引的是谁：{gap.why!r}"
    )


# ----------------------------------------------------------------------
# 七、``at`` 是承重的：同一份盘上状态，两个时刻两个答案
# ----------------------------------------------------------------------


def test_the_same_board_read_at_two_moments_gives_two_answers(board: _Board) -> None:
    """``at`` 早于到期 ⇒ 可用；``at`` 晚于到期 ⇒ 一条缺失。

    盘上一个字节都没动。一个把 ``at`` 收下却不用的实现（例如拿判据里的常量、或者
    另取一个「现在」）在这条之外全绿 —— 而它读出来的报告会与它自称的时刻对不上，
    报告上看不出来。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim(expires_at=NOW + timedelta(days=20)))
    board.catalog.put_offer(_ready_offer())
    early = _read(board.read_offer(at=NOW))
    late = _read(board.read_offer(at=NOW + timedelta(days=60)))
    assert early.usable and early.missing == (), (
        f"到期日之前这一版是能用的：{[gap.why for gap in early.missing]}"
    )
    assert not late.usable and _fields(late.missing) == {"approved_claim_refs"}, (
        f"到期日之后应有且只有一条缺失：{[gap.field for gap in late.missing]}"
    )
    assert late.at == NOW + timedelta(days=60), "读数要带上它自己的那个时刻"


# ----------------------------------------------------------------------
# 八、主语与拒词：读不到就是读不到，不是一份空读数
# ----------------------------------------------------------------------


def test_the_reading_names_its_own_subject(board: _Board) -> None:
    """读数要点名**它是关于哪一条的**。

    一次调用只问一条，而回执里那句主语是读的人与盘上那一行之间唯一的联系 ——
    少了它，一份从缓存、日志或别人转述里拿到的 readiness 说不清自己说的是谁。
    """
    board.catalog.put_version(_version(version=2))
    assert _read(board.read_product(version=2)).subject == "product:probe-product@2"

    board.put_claim(_claim())
    board.catalog.put_offer(_offer(version=3))
    assert _read(board.read_offer(version=3)).subject == "offer:probe-offer@3"


def test_an_unknown_subject_returns_none_rather_than_an_empty_reading(board: _Board) -> None:
    """主语不在盘上 ⇒ ``None``，**不是**一份三张全空的读数。

    这一条是本文件里最要紧的反面判据：三张全空的读数与「什么都不缺」在回执上
    同形，而后者会让调用方以为可以开跑。``ports/runs.py`` 的 ``unknown_run_refusal``
    对同一类混淆逐字写过：「两种情况都不该被当成『已经结束了』」。
    """
    board.catalog.put_version(_ready_version())
    board.put_claim(_claim())
    board.catalog.put_offer(_ready_offer())
    assert board.read_product(version=99) is None
    assert board.read_product(product_id="probe-ghost") is None
    assert board.read_offer(version=99) is None
    assert board.read_offer(offer_id="probe-ghost") is None


def test_the_unknown_subject_refusal_names_the_subject_and_the_next_step() -> None:
    """那句拒词说得出**是哪一条**、读的是哪一族、以及下一步。

    与 ``ports/runs.py`` 的三条 ``*_refusal`` 同形：判据一份、**返回字符串**、
    由调用方决定放进哪一种拒绝里（那个处理器抛 ``ToolError``）。在这里判它是因为
    它是这份读法对外的另一半 —— 一份读不出东西的回执里，那句话是唯一的产出。
    """
    said = unknown_version_refusal(SubjectKind.PRODUCT, "probe-ghost", 2)
    assert isinstance(said, str), "拒词是**返回**的字符串，不是抛出来的异常"
    assert "probe-ghost" in said and "2" in said, f"没点名是哪一条：{said!r}"
    assert "产品资料" in said, "要点名读的是哪一族"
    assert "登记" in said, "要说出下一步：先把它登记进去，或者核对主语写错了没有"
    assert "方案" in unknown_version_refusal(SubjectKind.OFFER, "probe-ghost", 2)


def test_the_claims_reader_has_no_default() -> None:
    """``claims`` 是一个**必填**参数。

    一个缺省的空读面会让「忘了把它接上」在报告上与「这一版一条说法都没引」同形
    —— 而后者本身就是一条要被报出来的缺口。于是那个接线错误会以一条**正确的**
    缺口的形状出现，谁也看不出漏了哪一根线。
    """
    parameter = inspect.signature(offer_version_readiness).parameters["claims"]
    assert parameter.default is inspect.Parameter.empty, (
        "claims 有缺省值 —— 那会让「没接线」长得像「没引用任何说法」"
    )


# ----------------------------------------------------------------------
# 九、三条守卫：只有直接构造才违反得了，所以逐个构造一遍
# ----------------------------------------------------------------------


def test_the_reading_refuses_a_naive_moment(board: _Board) -> None:
    """naive 时刻**拒**（措辞照 ``RunPreview.__post_init__``）。

    一个省掉时区的时刻会被按读它那台机器的本地时区解释，而这里拿它比的正是
    「被引用的说法到这一刻过期了没有」—— 于是「这一版能不能用」取决于这份读数
    在哪里跑。
    """
    board.catalog.put_version(_ready_version())
    with pytest.raises(ValueError, match="naive 时刻"):
        board.read_product(at=datetime(2026, 9, 29, 9, 0))


def test_a_subject_that_does_not_match_its_kind_is_refused() -> None:
    """主语与种类不许各写一遍。

    两格对不上的那天，报告里的主语与它自称的种类会分家 —— 读的人拿到的是一条
    既像产品又像方案的记录，而按种类去查库的那条路会去错表。
    """
    with pytest.raises(ValueError, match="对不上"):
        ProductReadiness(
            subject="offer:probe-offer@1",
            kind=SubjectKind.PRODUCT,
            status=ProductStatus.DRAFT,
            at=NOW,
        )


def test_a_non_draft_reading_cannot_await_a_signature() -> None:
    """非 ``draft`` 的读数不许带「等批准」。

    一份已经 ``ready`` 的记录在等谁的签字是自相矛盾的（它已经被签过了），而
    ``retired`` 的那一版不会有人去签它 —— 两种误读都会让人去找一个不存在的待办。
    """
    with pytest.raises(ValueError, match="等批准"):
        ProductReadiness(
            subject="product:probe-product@1",
            kind=SubjectKind.PRODUCT,
            status=ProductStatus.READY,
            at=NOW,
            awaiting_approval=(
                ReadinessGap(subject="product:probe-product@1", field="approved_by", why="等。"),
            ),
        )


# ----------------------------------------------------------------------
# 十、回执的形状
# ----------------------------------------------------------------------


def test_as_dict_carries_the_three_lists_and_the_moment(board: _Board) -> None:
    """写进报告的那一份：三张清单 + 时刻 + 主语 + 能不能用。

    ``at`` 一起带上是判据不是装饰：这是一份**带时刻的读数**，缺了它，报告里
    「被引用的说法作不作数」那个结论就没有可复核的时间锚点。
    """
    board.catalog.put_version(_version(capabilities=(), limits=()))
    payload = _read(board.read_product()).as_dict()
    assert payload["subject"] == "product:probe-product@1"
    assert payload["kind"] == "product"
    assert payload["status"] == "draft"
    assert payload["usable"] is False
    assert payload["at"] == NOW.isoformat()
    assert {gap["field"] for gap in payload["missing"]} == {"capabilities", "limits"}
    assert payload["invalid"] == []
    assert [gap["field"] for gap in payload["awaiting_approval"]] == ["approved_by"]
    for gap in payload["missing"]:
        assert set(gap) == {"subject", "field", "why"}, gap
    assert [item["item"] for item in payload["not_reported"]] == [
        item.item for item in NOT_REPORTED
    ]

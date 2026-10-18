"""``application/effective_scene.py`` 自己的判据：**编译、缺席、冲突、读写分家**。

零外部调用那一条不在这个文件里 —— 它钉在
``tests/test_effective_scene_is_offline.py``（AST 走 import 闭包），因为"这一族
够不到检索与抓取"是一个**结构**事实，而这个文件测的是**算出来的东西对不对**。
两者的分工不能互换：一个只跑功能用例的实现永远够不到那个结论。

## 四条各挡一种错法

1. **缺席是逐条点名的一格**（``not_reported``），不是一个空值。没有产品时它进
   **两格**（``product`` 与 ``product_fit``）—— 只记前者的实现让读的人自己去推
   后一句，而"推出来"与"写下来"在报告上不是同一件事。
2. **指名了却读不回来是抛**，不是记一笔缺席。两种缺席的处置相反：没有产品是
   一次合法的编译输入（B01-f 逐字），而指名了一个不存在的版本会让这份结果声称
   一个不存在的产品版本。
3. **冲突不挑赢家**：``SceneConflict`` 没有 ``winner`` 那一格，而一处对不上的读法
   仍然是**两条都在**（``readings`` 是元组、长度 ≥ 2）。
4. **预览不写、冻结写且读回来**。``preview_effective_scene`` 之后盘上一个版本都
   没有；``freeze_effective_scene`` 回的是**盘上那一行**，所以重发同一次编译时
   读到的 ``at`` / ``compiler`` 是**第一次**那一对。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import InMemoryEffectiveSceneStore, InMemoryProductCatalog
from marketops.application.effective_scene import (
    EFFECTIVE_SCENE_NOT_REPORTED,
    compile_effective_scene,
    freeze_effective_scene,
    preview_effective_scene,
    read_effective_scene,
)
from marketops.domain.enums import BudgetLine
from marketops.domain.errors import ConfigError, EvidenceError
from marketops.domain.maintenance import RefreshPlan
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.product import (
    ClaimRef,
    OfferVersion,
    ProductRef,
    ProductStatus,
    ProductVersion,
    QuotedPrice,
    SourceArtifactRef,
)
from marketops.domain.scene import (
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.util import sha256_text
from marketops.domain.workstream import (
    EffectiveScene,
    GeoScope,
    SalesMotion,
    effective_scene_digest,
)

T0 = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=3)
WORKSPACE = "ws-natural-grass"
OTHER_WORKSPACE = "ws-rye-blend"
SCENE = "scene-uk-natural-grass"
PRODUCT = "probe-product"
OFFER = "probe-offer"
COMPILER = "marketops-preview-effective-scene"


# --- 夹具 ----------------------------------------------------------------------


def _scene(version: int = 1, **overrides: object) -> SceneVersion:
    fields: dict[str, object] = {
        "scene_id": SCENE,
        "workspace": WORKSPACE,
        "version": version,
        "region": ("GB-England-Devon",),
        "scenario": "sports-surface-maintenance",
        "product": "acme-natural-grass",
        "admissions": ("sports-club",),
        "exclusions": ("golf-course", "bowling-green"),
        "role_policy": "role-policy-v1",
        "source_plan": ("mother-pool-import",),
        "budget": (SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "refresh": RefreshPlan.uncalibrated_default(),
        "outreach": OutreachPolicyDecl.uncalibrated_default(),
        "profile_id": WORKSPACE,
        "profile_version": "2026-09-01",
        "created_at": T0,
        "supersedes": version - 1 if version > 1 else None,
    }
    fields.update(overrides)
    return SceneVersion(**fields)  # type: ignore[arg-type]


def _record(**overrides: object) -> SceneRecord:
    fields: dict[str, object] = {
        "scene_id": SCENE,
        "workspace": WORKSPACE,
        "state": SceneState.ACTIVE,
        "latest_version": 1,
        "effective_version": 1,
        "created_at": T0,
        "updated_at": T0,
        "pause_reason": None,
    }
    fields.update(overrides)
    return SceneRecord(**fields)  # type: ignore[arg-type]


def _geo(**overrides: object) -> GeoScope:
    fields: dict[str, object] = {
        "countries": ("GB",),
        "regions": ("GB-England-Devon",),
        "exclusions": ("GB-England-Cornwall",),
        "source": "scene-attribution",
        "provenance": "declared",
        "at": T0,
        "owner": WORKSPACE,
    }
    fields.update(overrides)
    return GeoScope(**fields)  # type: ignore[arg-type]


def _motion(**overrides: object) -> SalesMotion:
    fields: dict[str, object] = {
        "motion": "end-user-maintenance",
        "relation": "终端销售",
        "source": "scene-attribution",
        "provenance": "declared",
        "at": T0,
        "owner": WORKSPACE,
    }
    fields.update(overrides)
    return SalesMotion(**fields)  # type: ignore[arg-type]


def _product(version: int = 1, **overrides: object) -> ProductVersion:
    fields: dict[str, object] = {
        "product_id": PRODUCT,
        "version": version,
        "display_name": "探针产品",
        "capabilities": ("surface-prep",),
        "units": "litre",
        "source_refs": (
            SourceArtifactRef(
                artifact_id="probe-spec",
                locator="artifact-store://probe/spec.pdf",
                content_sha256="a" * 64,
                fetched_at=T0,
                media_type="application/pdf",
            ),
        ),
        "owner": "probe-owner",
        "status": ProductStatus.READY,
        "approved_by": "probe-approver",
        "approved_at": T0,
    }
    fields.update(overrides)
    return ProductVersion(**fields)  # type: ignore[arg-type]


def _offer(version: int = 1, **overrides: object) -> OfferVersion:
    fields: dict[str, object] = {
        "offer_id": OFFER,
        "version": version,
        "workspace": WORKSPACE,
        "name": "探针方案",
        "purpose": "launch",
        "product_refs": (ProductRef(product_id=PRODUCT, version=1),),
        "markets": ("GB",),
        "locales": ("en",),
        "approved_claim_refs": (ClaimRef(claim_id="probe-claim", revision=1),),
        "price": QuotedPrice(
            currency="GBP",
            amount_minor=1250,
            basis="per litre",
            source_ref="probe-quote-2026",
            quoted_at=T0,
        ),
        "owner": "probe-owner",
        "status": ProductStatus.READY,
        "approved_by": "probe-approver",
        "approved_at": T0,
    }
    fields.update(overrides)
    return OfferVersion(**fields)  # type: ignore[arg-type]


def _catalog(*, products=(), offers=()) -> InMemoryProductCatalog:
    catalog = InMemoryProductCatalog()
    for product in products:
        catalog.put_version(product)
    for offer in offers:
        catalog.put_offer(offer)
    return catalog


def _compile(scene: SceneVersion | None = None, record: SceneRecord | None = None, **kwargs):
    scene = scene if scene is not None else _scene()
    record = record if record is not None else _record()
    kwargs.setdefault("geo", None)
    kwargs.setdefault("motion", None)
    kwargs.setdefault("product", None)
    kwargs.setdefault("offer", None)
    return compile_effective_scene(
        scene=scene,
        record=record,
        workspace=kwargs.pop("workspace", WORKSPACE),
        at=kwargs.pop("at", T0),
        compiler=kwargs.pop("compiler", COMPILER),
        **kwargs,
    )


def _fields(scene: EffectiveScene) -> set[str]:
    return {cell.field for cell in scene.cells}


def _gap_names(scene: EffectiveScene) -> list[str]:
    """``not_reported`` 里每一条的**格名**（``<格名>: <为什么>`` 的前半截）。"""
    return [gap.split(":", 1)[0] for gap in scene.not_reported]


# --- 1. 缺席：逐条点名，而"没有产品"是两格 --------------------------------------


def test_a_scene_with_nothing_compiled_in_still_compiles():
    """纯市场研究：``geo`` / ``motion`` / 产品都没有，而它**照样编译得出**
    —— 出来的是一份带三条点名的读数，不是一次拒绝。"""
    scene = _compile()

    assert isinstance(scene, EffectiveScene)
    # 五条，字典序（``not_reported`` 是排序过的元组）。没绑产品就没有产品那两格，
    # 也没有方案那一格 —— 而三条缺席各自都说出为什么。
    assert _gap_names(scene) == [
        "geo_scope",
        "offer",
        "product",
        "product_fit",
        "sales_motion",
    ]
    assert scene.conflicts == ()


def test_every_gap_carries_the_registered_sentence_verbatim():
    """``not_reported`` 的每一条都能在 :data:`EFFECTIVE_SCENE_NOT_REPORTED` 里
    找到那一句 —— 一个调用点自己写一句话的实现，会让同一格的缺席有两种说法。"""
    scene = _compile()
    for gap in scene.not_reported:
        name = gap.split(":", 1)[0]
        assert name in EFFECTIVE_SCENE_NOT_REPORTED
        assert EFFECTIVE_SCENE_NOT_REPORTED[name] in gap


def test_the_two_product_gaps_are_two_different_sentences():
    """``product`` 说"没绑产品版本"，``product_fit`` 说"所以这一格在这版上无法给出"。
    只记前者的实现让读的人自己去推后一句 —— 而 B01-f 逐字要的是**明确为 unavailable**。"""
    scene = _compile()
    gaps = {gap.split(":", 1)[0]: gap for gap in scene.not_reported}
    assert gaps["product"] != gaps["product_fit"]
    assert "product" not in _fields(scene), "没绑产品版本就没有那一格"
    assert "product_use" in _fields(scene), "场景自己声明的那一格在，与有没有产品版本无关"


def test_a_named_product_fills_the_cell_and_removes_both_gaps():
    scene = _compile(product=_product(), offer=_offer())

    assert _gap_names(scene) == ["geo_scope", "sales_motion"]
    cells = {cell.field: cell for cell in scene.cells}
    assert cells["product"].value == f"{PRODUCT}@1"
    assert cells["offer"].value == f"{OFFER}@1"


# --- 2. 指名了却读不回来：抛，不是记一笔缺席 ------------------------------------


def test_a_product_named_but_absent_is_an_error_not_a_gap():
    catalog = _catalog()

    with pytest.raises(ValueError) as caught:
        preview_effective_scene(
            scene=_scene(),
            record=_record(),
            workspace=WORKSPACE,
            catalog=catalog,
            at=T0,
            compiler=COMPILER,
            product_id=PRODUCT,
            product_version=9,
        )

    message = str(caught.value)
    assert PRODUCT in message and "9" in message
    assert "读不出来" in message


def test_half_a_product_reference_is_refused():
    """只给一半时"哪一版"没有答案 —— ``ProductRef`` 的 docstring 逐字不许。"""
    with pytest.raises(ValueError) as caught:
        preview_effective_scene(
            scene=_scene(),
            record=_record(),
            workspace=WORKSPACE,
            catalog=_catalog(),
            at=T0,
            compiler=COMPILER,
            product_id=PRODUCT,
        )
    assert "成对" in str(caught.value)


def test_an_offer_alone_is_a_legal_request():
    """方案自带 ``product_refs``，所以"有方案没产品"是一次说得通的输入
    —— 但方案本身必须读得回来。"""
    catalog = _catalog(offers=(_offer(),))
    scene = preview_effective_scene(
        scene=_scene(),
        record=_record(),
        workspace=WORKSPACE,
        catalog=catalog,
        at=T0,
        compiler=COMPILER,
        offer_id=OFFER,
        offer_version=1,
    )
    assert "offer" in _fields(scene)


# --- 3. 冲突不挑赢家 ------------------------------------------------------------


def test_a_scene_region_outside_the_geoscope_is_a_named_conflict_with_both_readings():
    """两条读法都在：一条说"这个场景要做这几个地区"，一条说"批准的范围是这几个"。
    挑一个赢家的实现会让另一条从 ``readings`` 里消失，而报告上它变成了一个值。"""
    scene = _compile(geo=_geo(regions=("GB-England-Cornwall",)))

    assert [conflict.field for conflict in scene.conflicts] == ["geo_scope.coverage"]
    conflict = scene.conflicts[0]
    assert len(set(conflict.readings)) >= 2
    assert any("GB-England-Devon" in reading for reading in conflict.readings)
    assert any("Cornwall" in reading for reading in conflict.readings)
    assert conflict.detail.strip()


def test_a_scene_conflict_has_no_winner_field():
    """**构造上的判据**：加上 ``winner`` 那一格，任何一处 ``max(readings)`` 都会有
    一个自然的落点，而那条落点不会有人去质疑。"""
    from marketops.domain.workstream import SceneConflict

    assert "winner" not in SceneConflict.model_fields


def test_a_covered_region_produces_no_conflict():
    assert _compile(geo=_geo()).conflicts == ()


def test_an_offer_bound_to_another_product_version_is_a_named_conflict():
    """方案绑的产品版本与场景声明的那一个对不上 ⇒ 逐条点名，**不挑**。"""
    scene = _compile(
        product=_product(version=2),
        offer=_offer(product_refs=(ProductRef(product_id=PRODUCT, version=1),)),
    )

    assert [conflict.field for conflict in scene.conflicts] == ["offer.product"]
    assert len(set(scene.conflicts[0].readings)) >= 2


def test_the_declared_product_use_is_not_compared_with_the_catalog_version():
    """``SceneVersion.product`` 是**产品/用途**（一段自由文本），目录里那一版是
    另一条轴上的东西。把 ``acme-natural-grass`` 与 ``probe-product@2`` 拿去
    比对会产出一处**假冲突** —— 而假冲突与真冲突在这一层的报告上同形。"""
    scene = _compile(product=_product(version=2), offer=None)
    assert scene.conflicts == (), "两格都留着，而它们不互相比"
    assert {"product", "product_use"} <= _fields(scene)


# --- 4. 编译本身：纯、有序、摘要稳定 --------------------------------------------


def test_the_cells_are_ordered_by_precedence_then_field():
    scene = _compile(geo=_geo(), motion=_motion(), product=_product(), offer=_offer())
    pairs = [(cell.precedence, cell.field) for cell in scene.cells]
    assert pairs == sorted(pairs)


def test_the_compiled_from_is_the_scene_digest_not_the_version_number():
    scene = _scene(version=2)
    compiled = _compile(scene=scene, record=_record(latest_version=2, effective_version=2))
    assert compiled.compiled_from == scene.frozen_sha256
    assert len(compiled.compiled_from) == 32, "frozen_sha256 是截断过的 32 位"


def test_each_cell_carries_the_digest_of_its_own_value():
    """``value_sha256`` 由这一层的工厂算，不由调用方给 —— 一个可以传进来的摘要
    会让"值被就地改过而摘要没跟上"在类型上说得通。"""
    scene = _compile(geo=_geo(), motion=_motion())
    for cell in scene.cells:
        assert cell.value_sha256 == sha256_text(cell.value)


def test_the_digest_does_not_move_when_only_the_clock_and_the_compiler_move():
    """``at`` 与 ``compiler`` 不在摘要里：它们说的是一次**动作**，不是这份内容。
    装进去会让同一次编译被另一个人重发时读成一次冲突 —— 而重发是幂等的一半。"""
    scene = _scene()
    record = _record()
    first = _compile(scene=scene, record=record, at=T0, compiler="alice")
    second = _compile(scene=scene, record=record, at=T1, compiler="bob")

    assert first.at != second.at
    assert first.compiler != second.compiler
    assert effective_scene_digest(first) == effective_scene_digest(second)


def test_the_same_inputs_compile_to_the_same_bytes():
    scene = _scene()
    record = _record()
    geo, motion, product, offer = _geo(), _motion(), _product(), _offer()
    first = _compile(scene=scene, record=record, geo=geo, motion=motion, product=product, offer=offer)
    second = _compile(scene=scene, record=record, geo=geo, motion=motion, product=product, offer=offer)
    assert first == second


# --- 5. 跨工作区：三处影子对不上就抛 --------------------------------------------


@pytest.mark.parametrize("where", ["scene", "record", "workspace"])
def test_a_workspace_that_does_not_hold_together_is_refused(where: str):
    """三处读的是同一格（工作区），而一次编译里它们相遇 —— 对不上说明有一格
    来自别处，而"这份结果属于谁"没有第二个答案。"""
    scene, record, workspace = _scene(), _record(), WORKSPACE
    if where == "scene":
        scene = _scene(workspace=OTHER_WORKSPACE)
    elif where == "record":
        record = _record(workspace=OTHER_WORKSPACE)
    else:
        workspace = OTHER_WORKSPACE

    with pytest.raises(ValueError) as caught:
        compile_effective_scene(
            scene=scene,
            record=record,
            workspace=workspace,
            geo=None,
            motion=None,
            product=None,
            offer=None,
            at=T0,
            compiler=COMPILER,
        )
    assert "工作区" in str(caught.value)


def test_a_record_for_another_scene_is_refused():
    with pytest.raises(ValueError) as caught:
        _compile(record=_record(scene_id="scene-de-natural-grass"))
    assert "scene" in str(caught.value)


# --- 6. 预览不写、冻结写且读回来 ------------------------------------------------


def test_a_preview_writes_nothing_at_all():
    """预览是读：盘上一个版本都没有。它是零 provider 那条判据的**功能**那一半
    （结构那一半在 offline 测试里）。"""
    store = InMemoryEffectiveSceneStore()
    preview_effective_scene(
        scene=_scene(),
        record=_record(),
        workspace=WORKSPACE,
        catalog=_catalog(products=(_product(),)),
        at=T0,
        compiler=COMPILER,
        product_id=PRODUCT,
        product_version=1,
    )
    assert store.versions(SCENE) == ()
    assert store.scenes(workspace=WORKSPACE) == ()


def test_a_freeze_writes_the_compiled_scene_and_hands_back_the_row_on_disk():
    store = InMemoryEffectiveSceneStore()
    frozen = freeze_effective_scene(
        store=store,
        scene=_scene(),
        record=_record(),
        workspace=WORKSPACE,
        catalog=_catalog(products=(_product(),)),
        at=T0,
        compiler=COMPILER,
        product_id=PRODUCT,
        product_version=1,
    )

    assert store.versions(SCENE) == (1,)
    assert store.get_scene(SCENE, 1) == frozen
    assert frozen.at == T0


def test_a_replayed_freeze_hands_back_the_first_rows_clock_not_this_ones():
    """盘上那一行是**第一次**那一对的 ``at`` / ``compiler``：重发同一次编译
    （内容摘要相等 ⇒ ``put_scene`` 幂等）时回刚拼出来的那个对象，会让调用方读到
    "这一次是什么时候编译的"，而盘上留着的是另一对 —— 只有后者是这一版的事实。"""
    store = InMemoryEffectiveSceneStore()
    scene, record = _scene(), _record()
    catalog = _catalog(products=(_product(),))

    first = freeze_effective_scene(
        store=store,
        scene=scene,
        record=record,
        workspace=WORKSPACE,
        catalog=catalog,
        at=T0,
        compiler="alice",
        product_id=PRODUCT,
        product_version=1,
    )
    second = freeze_effective_scene(
        store=store,
        scene=scene,
        record=record,
        workspace=WORKSPACE,
        catalog=catalog,
        at=T1,
        compiler="bob",
        product_id=PRODUCT,
        product_version=1,
    )

    assert second.at == T0 and second.compiler == "alice"
    assert second == first
    assert store.versions(SCENE) == (1,), "重发不写下第二个版本"


def test_a_freeze_that_compiles_differently_hits_the_immutable_write_path():
    """同一 ``(scene_id, version)`` 上内容不同 ⇒ ``StoreError``，而那一句点名是哪一个
    ``(scene_id, version)``。这是承重点 4 在这一层的落点。"""
    from marketops.domain.errors import StoreError

    store = InMemoryEffectiveSceneStore()
    scene, record = _scene(), _record()
    freeze_effective_scene(
        store=store,
        scene=scene,
        record=record,
        workspace=WORKSPACE,
        catalog=_catalog(products=(_product(),)),
        at=T0,
        compiler=COMPILER,
        product_id=PRODUCT,
        product_version=1,
    )

    with pytest.raises(StoreError) as caught:
        freeze_effective_scene(
            store=store,
            scene=scene,
            record=record,
            workspace=WORKSPACE,
            catalog=_catalog(),
            at=T1,
            compiler=COMPILER,
        )
    assert SCENE in str(caught.value) and "1" in str(caught.value)


# --- 7. 读：三句话分得开 --------------------------------------------------------


def test_reading_a_version_that_was_never_compiled_names_what_is_on_disk():
    store = InMemoryEffectiveSceneStore()
    with pytest.raises(ConfigError) as caught:
        read_effective_scene(store, scene_id=SCENE, version=3, workspace=WORKSPACE)
    message = str(caught.value)
    assert "第 3 版" in message
    assert "（一个都没有）" in message, "盘上有什么要一起说出来"

    store.put_scene(_compile())
    with pytest.raises(ConfigError) as caught:
        read_effective_scene(store, scene_id=SCENE, version=3, workspace=WORKSPACE)
    assert "1" in str(caught.value)


def test_reading_another_workspaces_scene_is_refused_and_names_the_owner():
    """存储那一层按 ``workspace`` 过滤，而"按主键读"绕过它（主键是
    ``(scene_id, version)``，上面没有工作区）。"""
    store = InMemoryEffectiveSceneStore()
    store.put_scene(_compile())

    with pytest.raises(EvidenceError) as caught:
        read_effective_scene(store, scene_id=SCENE, version=1, workspace=OTHER_WORKSPACE)
    message = str(caught.value)
    assert WORKSPACE in message and OTHER_WORKSPACE in message


def test_reading_your_own_scene_hands_it_back():
    store = InMemoryEffectiveSceneStore()
    frozen = _compile()
    store.put_scene(frozen)
    assert read_effective_scene(store, scene_id=SCENE, version=1, workspace=WORKSPACE) == frozen

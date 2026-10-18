"""``preview_effective_scene`` / ``get_effective_scene`` 在**工具面**上的判据。

三条判据在应用层的两份里已经立过了（``tests/test_application_effective_scene.py``
的编译与冲突、``tests/test_mcp_effective_scene_is_offline.py`` 的「够不到」）。这一份
判的是**工具面自己**那几件，而它们只有在这里看得见：

1. **参数表**。``at`` / ``workspace`` / ``compiler`` 一个都不在入参里 —— 时刻取服务端
   时钟、工作区取 ``Settings``、``compiler`` 取工具名。三格都是「调用方挑不了」，
   而一个能挑的时刻会让同一条盘上事实在两个调用方那里得到相反的两份读数。
2. **预览一次写都没有发生**。这一条必须在**真存储**上量：应用层的判据用的是替身，
   而替身数不出「有人绕开应用层直接写库」。这里读的是同一个 ``SqliteEffectiveSceneStore``
   （接线层拿到的就是它），预览前后逐格比对。
3. **读回的就是冻结的那一份**。冻结走应用层、读走工具，两条路径接在同一个库上 ——
   两处各建一份的表现是「刚冻结的那一版读不出来」与「它从来没被冻结过」同形。
4. **读一个不在盘上的版本**：拒绝那句话必须把**盘上有的**版本点名写出来。

## 为什么这些用例直接调处理器，不经 MCP 线

``tests/mcp_harness.py`` 的 ``drive_with_settings`` / ``build_server`` 收的是一组**闭合**
的关键字（``launch=`` / ``feedback=`` / ``views=`` …），而这一族的名字不在里面 ——
这三族今天**还没有接进组合根**（``interfaces/composition.py` 里没有 ``default_workstream_store``
一类的工厂，``interfaces/mcp/__main__.py`` 也没接）。所以走线的用例现在写不出来，
写出来的会是「工具不在目录里」。这一条**不是**本文件的删减理由，而是交回单上的
一项接线请求：补上关键字之后，这一份可以直接改成走线的版本，判据一条不用改。
"""

from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timedelta, timezone

import pytest

from marketops.adapters.memory import InMemoryProductCatalog
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.adapters.sqlite.workstreams import SqliteEffectiveSceneStore
from marketops.adapters.mcp_server.effective_scene import (
    PREVIEW_COMPILER,
    effective_scene_handlers,
)
from marketops.application.effective_scene import (
    EFFECTIVE_SCENE_NOT_REPORTED,
    freeze_effective_scene,
)
from marketops.config import Settings
from marketops.domain.enums import BudgetLine
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
    OutreachPolicyDecl,
    RefreshPlan,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)
from marketops.domain.workstream import EffectiveScene
from mcp.server.mcpserver.exceptions import ToolError

T0 = datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(hours=3)

#: 这一族的场景名**就是**本部署的 profile —— ``require_scene`` 拒绝别的取值，而这是
#: 一条被测对象而不是省事：一个部署只服务一个 Scene。
PRODUCT = "probe-product"
OFFER = "probe-offer"

#: 入参里**不许**出现的名字。三格各自承载一件不由载荷提供的事实。
FORBIDDEN_PARAMETERS = frozenset(
    {"at", "workspace", "compiler", "digest", "compiled_from", "value_sha256"}
)

EXPECTED_PREVIEW_PARAMETERS = frozenset(
    {
        "scene_id",
        "version",
        "geo",
        "motion",
        "product_id",
        "product_version",
        "offer_id",
        "offer_version",
    }
)


# --- 夹具 -----------------------------------------------------------------------


def _scene(scene_id: str, workspace: str, version: int = 1, **overrides: object) -> SceneVersion:
    fields: dict[str, object] = {
        "scene_id": scene_id,
        "workspace": workspace,
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
        "profile_id": workspace,
        "profile_version": "2026-09-01",
        "created_at": T0,
        "supersedes": version - 1 if version > 1 else None,
    }
    fields.update(overrides)
    return SceneVersion(**fields)  # type: ignore[arg-type]


def _record(scene_id: str, workspace: str, **overrides: object) -> SceneRecord:
    fields: dict[str, object] = {
        "scene_id": scene_id,
        "workspace": workspace,
        "state": SceneState.ACTIVE,
        "latest_version": 1,
        "effective_version": 1,
        "created_at": T0,
        "updated_at": T0,
        "pause_reason": None,
    }
    fields.update(overrides)
    return SceneRecord(**fields)  # type: ignore[arg-type]


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


def _offer(workspace: str, version: int = 1, **overrides: object) -> OfferVersion:
    fields: dict[str, object] = {
        "offer_id": OFFER,
        "version": version,
        "workspace": workspace,
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


class Wiring:
    """一次接线：四个工厂 + 三个实例，与组合根交给这一族的东西同形。

    ``scenes`` / ``effective`` 指向**同一个库**（``settings.database_path``）—— 这不是
    为了省事：组合根把 ``scenes`` 交给两处的那条纪律说的就是这件事，而这一份要能看见
    「冻结的一侧与读的一侧分家」。
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.scenes_store = SqliteSceneStore(settings.database_path)
        self.effective_store = SqliteEffectiveSceneStore(settings.database_path)
        self.catalog = InMemoryProductCatalog()
        self.handlers = effective_scene_handlers(
            settings=settings,
            scenes=lambda: self.scenes_store,
            catalogs=lambda: self.catalog,
            effective=lambda: self.effective_store,
        )

    # -- 布置盘上状态 ------------------------------------------------------------

    def declare(self, version: int = 1, **overrides: object) -> SceneVersion:
        """写下**一版声明**（记录 + 那一版），与登记面写下的是同一份东西。"""
        profile = self.settings.profile
        row = _scene(profile, profile, version, **overrides)
        self.scenes_store.put_record(
            _record(profile, profile, latest_version=version, effective_version=version),
            expected_revision=None,
        )
        self.scenes_store.put_version(row)
        return row

    def approve_product_and_offer(self) -> None:
        self.catalog.put_version(_product())
        self.catalog.put_offer(_offer(self.settings.profile))

    # -- 调用面 ------------------------------------------------------------------

    def preview(self, **kwargs: object) -> dict:
        return self.handlers["preview_effective_scene"](**kwargs)  # type: ignore[arg-type]

    def read(self, **kwargs: object) -> dict:
        return self.handlers["get_effective_scene"](**kwargs)  # type: ignore[arg-type]

    def frozen_rows(self) -> tuple[EffectiveScene, ...]:
        return self.effective_store.scenes(workspace=self.settings.profile)


@pytest.fixture
def wiring(settings: Settings) -> Wiring:
    wired = Wiring(settings)
    wired.declare()
    wired.approve_product_and_offer()
    return wired


def _digest_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cell(reading: dict, field: str) -> dict:
    """回执里 ``field`` 那一格格子。找不到时**说出盘上有哪几格** ——
    一条只说 ``KeyError`` 的失败，读的人还以为是自己拼错了字段名。"""
    found = [cell for cell in reading["cells"] if cell["field"] == field]
    assert len(found) == 1, (
        f"回执里没有 {field!r} 这一格（有的：{[c['field'] for c in reading['cells']]}）"
    )
    return found[0]


# --- 1 · 参数表 -----------------------------------------------------------------


def test_the_two_tools_take_no_instant_and_no_workspace(settings: Settings) -> None:
    """这一族的入参集合**恰好**是它自己那一组，三格不由载荷提供的事实一个都不在。

    ``at`` 是这一族最要紧的缺席：一次编译里 ``GeoScope`` / ``SalesMotion`` / 读数
    各带一格时刻，而它们取自**同一次**服务端时钟。一个由调用方挑的时刻会让同一条盘上
    事实在两个调用方那里得到相反的两份读数 —— 两边各自都正常，没有一处会响。
    ``workspace`` 同理（这一族读的是**本部署**的场景）；``compiler`` 是**哪条路径**
    编译了它，填的是工具名。
    """
    preview = set(inspect.signature(Wiring(settings).handlers["preview_effective_scene"]).parameters)
    read = set(inspect.signature(Wiring(settings).handlers["get_effective_scene"]).parameters)

    assert preview & FORBIDDEN_PARAMETERS == set()
    assert read & FORBIDDEN_PARAMETERS == set()
    assert preview == EXPECTED_PREVIEW_PARAMETERS, (
        f"多出来的：{sorted(preview - EXPECTED_PREVIEW_PARAMETERS)}；"
        f"少掉的：{sorted(EXPECTED_PREVIEW_PARAMETERS - preview)}"
    )
    #: 读的那一条只按 ``(scene_id, version)`` 读 —— 少一格就没有主语。
    assert read == {"scene_id", "version"}


def test_the_surface_is_exactly_the_two_read_only_tools(wiring: Wiring) -> None:
    """这一族**只有两条**工具，且两条都在目录里那七条之内。

    多出一条写工具，「零外部调用是构造」这句话的主语就不再是一个读数面 —— 而
    ``tests/test_mcp_effective_scene_is_offline.py`` 那个闭包会照旧全绿
    （它判的是**能力**，不是这一族**做了几件事**）。
    """
    assert set(wiring.handlers) == {"preview_effective_scene", "get_effective_scene"}


# --- 2 · 预览：一次写都没有发生 ------------------------------------------------


def test_a_preview_writes_nothing_at_all(wiring: Wiring) -> None:
    """**在真存储上**量：预览前后，有效场景那个库里一格都没有多。

    应用层那一条用的是替身，而替身数不出「有人绕开应用层直接写库」—— 接线层拿到的
    就是 ``SqliteEffectiveSceneStore``，所以这一条读的是那个实例本身。

    「被预览的那个动作必须还没发生」：一次预览如果顺手落一版，那么「预览过」与
    「冻结过」在盘上同形，而后者是这一族的承重点（有效场景一旦生成就不再被就地改写）。
    """
    before = wiring.frozen_rows()
    reading = wiring.preview()

    assert wiring.frozen_rows() == before == ()
    #: 读那一侧也读不到任何东西 —— 「预览过了」不等于「冻结过了」。
    with pytest.raises(ToolError) as caught:
        wiring.read(version=1)
    assert "1" in str(caught.value)
    #: 回执确实是一份**编译过的**东西（不是一条「什么都没发生」的空壳）。
    assert reading["digest"]
    assert reading["compiled_from"]


def test_the_preview_receipt_names_the_gaps_and_never_a_winner(wiring: Wiring) -> None:
    """回执逐条给的是**答不上来的那几格**与**冲突**，而不是一个取过赢家的答案。

    冻结表 §3.2 逐字「每一处冲突逐条点名而不取赢家」—— 冲突取赢家会让「这里有两份
    说法」这件事从记录里消失，而它正是这一层产出里最该被看见的一格。

    ``not_reported`` 是**句子**（``tuple[str, ...]``），不是一张 ``{格名: 句子}`` 的
    表：缺的那一格与为什么缺在同一行上。判的是那三句**逐字**在，所以一句「没说清
    为什么」的省略在这里红。
    """
    reading = wiring.preview()

    gaps = reading["not_reported"]
    assert isinstance(gaps, list) and gaps
    #: 这一版声明里 ``product`` 是个**裸名字**（``SceneVersion.product`` 是 str），
    #: 而这一条没有点名产品版本 —— 于是产品与方案那几格都在。
    for field, sentence in (
        ("product", EFFECTIVE_SCENE_NOT_REPORTED["product"]),
        ("product_fit", EFFECTIVE_SCENE_NOT_REPORTED["product_fit"]),
        ("offer", EFFECTIVE_SCENE_NOT_REPORTED["offer"]),
    ):
        assert any(field in gap for gap in gaps), f"{field} 那一格没有出现在缺口里"
        assert any(sentence in gap for gap in gaps), f"{field} 的那句话被改写或省略了"
    #: 没有冲突时 ``conflicts`` 是空的；**不是**一个取了赢家的答案。
    assert reading["conflicts"] == []
    assert "winner" not in reading


def test_a_named_product_and_offer_close_the_gaps(wiring: Wiring) -> None:
    """点名了产品与方案之后，回执里那几格真的被填上了 —— 同一份盘上状态。

    判的是「缺口那一行**没有**再出现」，而不是「缺口变少了」：后者在把产品那一格
    换成另一格缺口时也绿。
    """
    gaps = wiring.preview()["not_reported"]
    assert any("product:" in gap for gap in gaps)

    reading = wiring.preview(
        product_id=PRODUCT, product_version=1, offer_id=OFFER, offer_version=1
    )
    assert not any(gap.startswith("product:") for gap in reading["not_reported"])
    assert not any(gap.startswith("offer:") for gap in reading["not_reported"])
    assert reading["digest"]


def test_a_product_that_is_not_on_disk_is_refused_by_name(wiring: Wiring) -> None:
    """指名了一个读不回来的产品 → **一条 ``ToolError``**，且那句话**点到名**。

    「读不回来是一次错误」而「没有点名是一件缺席」—— 两句话的处置相反（去核对版本
    号 / 去登记一版）。把前者答成后者的实现，会在这一条上以「编译成功」的形状出现。

    而 ``ToolError`` 这个**类型**是这一条的另一半：``tools/base.py`` 只对 ``ToolError``
    原样保留措辞，别的异常落成一句光秃秃的 ``Error executing tool …``（见
    ``_refuse`` 的 docstring）。所以这一条同时钉住「说了什么」与「说得出去」。
    """
    with pytest.raises(ToolError) as caught:
        wiring.preview(product_id="no-such-product", product_version=1)
    message = str(caught.value)
    assert "no-such-product" in message
    assert "1" in message


def test_a_geo_table_of_the_wrong_shape_is_refused(wiring: Wiring) -> None:
    """``geo`` 给的不是一张表 → 拒绝，而不是被当成空表收下。

    被当成空表收下的后果是：那份范围的 ``countries`` / ``exclusions`` 是空的，
    于是它看上去像「这个部署没有声明任何范围」—— 缺的那一格与写错的那一格必须分开。
    """
    with pytest.raises(ToolError) as caught:
        wiring.preview(geo=["GB"])  # type: ignore[arg-type]
    assert "geo" in str(caught.value)


def test_geo_given_as_a_table_lands_on_the_reading(wiring: Wiring) -> None:
    """给了一张表就是**一次声明**：``source`` / ``provenance`` 缺省为空 → 拒绝。

    ``GeoScope`` 的校验器逐字要求它们非空（「一份范围和一句模型的猜测在盘上同形」）。
    于是「给了一张没有出处的表」是一次拒绝，而不是一个默认值填满的对象。
    """
    with pytest.raises(ToolError) as caught:
        wiring.preview(geo={"countries": ["GB"], "exclusions": ["GB-England-Cornwall"]})
    assert "provenance" in str(caught.value) or "source" in str(caught.value)

    reading = wiring.preview(
        geo={
            "countries": ["GB"],
            "regions": ["GB-England-Devon"],
            "exclusions": ["GB-England-Cornwall"],
            "source": "scene-attribution",
            "provenance": "declared",
        },
        motion={
            "motion": "end-user-maintenance",
            "relation": "终端销售",
            "source": "scene-attribution",
            "provenance": "declared",
        },
    )
    #: 给了一格 ``geo`` 就是多出一格格子 —— 它不在顶层，而在 ``cells`` 里按 ``field``
    #: 找得到（顶层那几格是这份读数自己的主语与摘要）。
    cell = _cell(reading, "geo_scope")
    assert "GB" in cell["value"]
    assert cell["source"] == "scene-attribution"
    assert cell["provenance"] == "declared"
    #: ``owner`` 缺省取本部署的工作区 —— 一份由这个部署写下来的范围，第一负责人就是它。
    assert cell["owner"] == wiring.settings.profile


# --- 3 · 冻结的那一份读得回来 ---------------------------------------------------


def test_what_was_frozen_is_what_is_read_back(wiring: Wiring) -> None:
    """冻结走应用层，读走工具 —— 两条路接在**同一个库**上。

    两处各建一份存储的表现是「刚冻结的那一版读不出来」与「它从来没被冻结过」同形，
    而两者的处置相反（去查库 / 去冻结一版）。
    """
    frozen = freeze_effective_scene(
        store=wiring.effective_store,
        scene=wiring.scenes_store.get_version(wiring.settings.profile, 1),
        record=wiring.scenes_store.get_record(wiring.settings.profile),
        workspace=wiring.settings.profile,
        catalog=wiring.catalog,
        at=T1,
        compiler="probe",
        geo=None,
        motion=None,
        product_id=PRODUCT,
        product_version=1,
        offer_id=OFFER,
        offer_version=1,
    )

    reading = wiring.read(version=1)
    assert reading["digest"] == frozen.digest
    assert reading["compiled_from"] == frozen.compiled_from
    #: 读的是**冻结时**的那一份，不是按现在重编译一遍 —— ``at`` 是编译那一刻。
    assert reading["at"] == frozen.at.isoformat().replace("+00:00", "Z")


def test_the_preview_and_the_frozen_row_agree_on_the_digest(wiring: Wiring) -> None:
    """同一个 ``(scene_id, version)`` 与同一组编译输入：预览与冻结给出同一个 digest。

    ``digest`` 不进 ``at`` / ``compiler``（``effective_scene_digest`` 只吃内容），
    所以两处各拼一份编译输入的实现会在这里红 —— 而两份回执各自都自洽。
    """
    arguments = dict(
        product_id=PRODUCT,
        product_version=1,
        offer_id=OFFER,
        offer_version=1,
    )
    previewed = wiring.preview(**arguments)
    frozen = freeze_effective_scene(
        store=wiring.effective_store,
        scene=wiring.scenes_store.get_version(wiring.settings.profile, 1),
        record=wiring.scenes_store.get_record(wiring.settings.profile),
        workspace=wiring.settings.profile,
        catalog=wiring.catalog,
        at=T1,
        compiler="probe",
        geo=None,
        motion=None,
        **arguments,
    )
    assert previewed["digest"] == frozen.digest


def test_a_version_that_is_not_on_disk_is_refused_with_what_is(wiring: Wiring) -> None:
    """读一个不在盘上的版本 → 拒绝，并把**盘上有的**版本点名写出来。

    「这一版不存在」与「这一版存在但什么都没编译出来」不是同一件事。盘上一格都没有时
    也要说得出来（``（一个都没有）``），否则「读错了版本号」与「还没冻结过」同形。
    """
    with pytest.raises(ToolError) as caught:
        wiring.read(version=7)
    assert "7" in str(caught.value)
    assert "一个都没有" in str(caught.value)

    #: 冻结两版之后，拒绝里点的是盘上真有的那个版本号。
    for version in (1, 2):
        wiring.scenes_store.put_version(_scene(wiring.settings.profile, wiring.settings.profile, version))
        freeze_effective_scene(
            store=wiring.effective_store,
            scene=wiring.scenes_store.get_version(wiring.settings.profile, version),
            record=wiring.scenes_store.get_record(wiring.settings.profile),
            workspace=wiring.settings.profile,
            catalog=wiring.catalog,
            at=T1,
            compiler="probe",
            geo=None,
            motion=None,
            product_id=PRODUCT,
            product_version=1,
            offer_id=OFFER,
            offer_version=1,
        )
    with pytest.raises(ToolError) as caught:
        wiring.read(version=9)
    message = str(caught.value)
    assert "9" in message
    assert "1" in message and "2" in message


def test_a_version_below_one_is_refused_before_any_read(wiring: Wiring) -> None:
    """``version`` 从 1 起 —— 缺省的那一格（``0``）不是「最新版」。

    给一个缺省会让一次读在没有那一版时悄悄换一期读。这里判的是那条拒绝真的在——
    而 ``read()`` 不带参数走的正是这一条路。
    """
    with pytest.raises(ToolError) as caught:
        wiring.read()
    assert "version" in str(caught.value)


# --- 4 · 跨场景 -----------------------------------------------------------------


def test_another_scene_is_refused_by_name(wiring: Wiring) -> None:
    """本部署只服务一个 Scene —— 点别的名字是拒绝，不是静默退回默认值。

    静默退回会让调用方以为读的是另一个场景的规则。两个工具各判一次：拒绝写在一处
    而另一处漏掉，会让「预览拒绝、读放行」这种分家出现，而两边各自都自洽。
    """
    for call in (wiring.preview, wiring.read):
        with pytest.raises(ToolError) as caught:
            call(scene_id="scene-someone-else")
        message = str(caught.value)
        assert "scene-someone-else" in message
        assert wiring.settings.profile in message


def test_a_scene_that_was_never_declared_is_refused(settings: Settings) -> None:
    """库是空的 → 拒绝说的是「还没有立项过」，而不是「没有这一版」。

    这两句话的下一步不同（去 ``create_scene`` / 去 ``add_scene_version``）。一个把
    前者答成后者的实现，会让一个空的市场看起来像一个写错了版本号的市场。
    """
    wiring = Wiring(settings)
    with pytest.raises(ToolError) as caught:
        wiring.preview()
    assert "立项" in str(caught.value)


# --- 5 · 回执的形状 -------------------------------------------------------------


def test_the_receipt_carries_the_digest_and_the_provenance_of_every_cell(wiring: Wiring) -> None:
    """每一格带来源、出处、单位、语言、负责人与哈希，摘要也在。

    ``digest`` 是 ``EffectiveScene`` 的**属性**而不是字段，而字段与属性在
    ``model_dump()`` 上的差别恰好会让它**缺席** —— 缺了它，「这一版还是编译时的
    那一份吗」在回执上没有答案（而「没有这一格」与「摘要对不上」不同形）。
    """
    reading = wiring.preview(
        product_id=PRODUCT, product_version=1, offer_id=OFFER, offer_version=1
    )

    assert len(reading["digest"]) == 64
    for cell in reading["cells"]:
        assert cell["value_sha256"] == _digest_of(cell["value"]), cell["field"]
        #: 六格是 B02-b 逐字要的：来源、出处、单位、语言、负责人、哈希。
        for key in ("source", "provenance", "unit", "locale", "owner", "value_sha256"):
            assert cell[key].strip(), f"{cell['field']} 的 {key} 是空的"
        #: ``precedence`` 与 ``overrides`` 是另外半句（「优先级逐条写出来」）——
        #: 没有这两格，「为什么是这一个值」在盘上没有答案。
        assert isinstance(cell["precedence"], int)
        assert isinstance(cell["overrides"], list)
    #: 每一格的哈希是那一格**值**的哈希 —— 不是整份读数的哈希。
    assert _cell(reading, "offer")["value"]
    #: 点名的产品被**解成**了 ``<id>@<version>``，而声明里那个裸名字在 ``product_use``
    #: 上原样留着 —— 两格答的是两个问题（「这一版卖什么」与「解到哪一个版本」），
    #: 把后者折成前者会让「这一版引的是第 2 版」这件事从读数上消失。
    assert _cell(reading, "product")["value"] == f"{PRODUCT}@1"
    assert _cell(reading, "product_use")["value"] == "acme-natural-grass"


def test_the_compiler_is_the_tool_name_now_and_the_path_name_when_frozen(wiring: Wiring) -> None:
    """``compiler`` 答的是**哪一条路径**编译了它，而两条路径各答各的。

    调用者的身份在这一层读不到（actor 由守卫解析，不进处理器签名），而 ``compiler``
    进不了 ``digest`` —— 一个每个调用者各不相同的值会让同一次编译在盘上留下谁都说不清
    的一格。所以填的是工具名：预览答 ``preview_effective_scene``，冻结那一侧答
    ``start_workstream``。
    """
    assert PREVIEW_COMPILER == "preview_effective_scene"
    assert wiring.preview()["compiler"] == PREVIEW_COMPILER

"""**B02-b：把声明的那一版 Scene 编译成 EffectiveScene。** 只读、零外部调用。

## 零 provider 是**构造**，不是自觉

``preview_effective_scene`` / ``get_effective_scene`` 是读：不写一行、不发一次检索。
这句话在本模块里的落点**不是**「我们记得不要去检索」，而是**够不到**：

* :func:`compile_effective_scene` 是一个**纯函数** —— 它的签名里没有任何端口，
  连一个存储都没有。它拿到的全是已经读出来的值（照
  ``application/launch_preview.py`` 的先例）。
* :func:`preview_effective_scene` 只多拿一个 ``ProductCatalog``（一个**读**端口：
  产品与方案是声明，不是检索结果）。它拿不到 ``SearchProvider`` / ``Fetcher`` /
  ``DocumentParser`` 中的任何一个。
* :func:`read_effective_scene` 只拿一个 ``EffectiveSceneStore``。

判据钉在 ``tests/test_effective_scene_is_offline.py``：它走 AST 算**这一族模块的
import 闭包**，白名单是**精确相等**的（多一个 import 就红），并且检查三件事 ——
不许出现检索/抓取端口的类名（含注解位置）、不许有 ``open`` / ``urlopen`` /
``connect`` / ``popen`` 这类调用、不许调时钟（``utc_now`` / ``now``）与
``eval`` / ``exec`` / ``compile`` / ``__import__``。**它同一个文件里还有阳性对照**
（喂一段故意的违规源码，判据必须报出来）—— 少了那一条，一个恒绿的判据与一个
真的判据在报告上同形。

## 冻结是**写**，而它写在同一族里

``preview_effective_scene`` / ``read_effective_scene`` 是读（``preview_launch`` 那一族
的先例）；:func:`freeze_effective_scene` 是**编译加落库** —— 冻结表 §3.2 的
``EffectiveScene`` 那一行逐字：「``preview_effective_scene`` 编译（纯函数、零 provider
调用）→ ``start_workstream`` 冻结落库」。它在这一族里而不是在启动那一族里，因为
「编译」这件事只有一处定义：冻结 = 编译 + ``put_scene``，而重写一遍编译是把同一个
判据放进第二个地方。

**零外部调用这句话在这一格上仍然成立**，而且理由与那两个读一样是构造：它拿到的仍然
只有 ``ProductCatalog``（一个读端口），够不到 ``SearchProvider`` / ``Fetcher``。判据
里的 import 闭包因此对这一格没有放松 —— 它判的是**能力**，而这一族今天仍然没有
检索与抓取的能力。

## 冲突**逐条点名，不取赢家**

B02-b 逐字。这一层里它的落点是两件具体的事：

1. :class:`~marketops.domain.workstream.SceneConflict` **没有** ``winner`` 那一格
   —— 加上它，任何一处 ``max(readings)`` 都会有一个自然的落点，而那条落点不会
   有人去质疑。
2. 一处对不上的读法**不会**同时出现在 ``cells`` 里（那是「挑了赢家」的形态）。
   两条是分开的两张表：``cells`` 是算出来的值，``conflicts`` 是没算出来的那部分。

## 「算不出来」与「没有这一格」是两件事

没有 Product 的纯市场研究**照样编译得出**（B01-f 逐字）：``offer`` 那一格不进
``cells``，而是进 :attr:`~marketops.domain.workstream.EffectiveScene.not_reported`
并**点名说清**为什么。两者在报告上的形态差别是这一条的全部内容 —— 一个静默省略
``offer`` 的实现，让「这个场景没有产品」与「产品这一格算不出来」看起来一模一样，
而两者的处置相反（前者什么都不用做，后者要去补资料）。
"""

from __future__ import annotations

from datetime import datetime

from ..domain.errors import ConfigError, EvidenceError
from ..domain.product import OfferVersion, ProductRef, ProductVersion
from ..domain.scene import SceneRecord, SceneVersion
from ..domain.util import sha256_text, to_utc
from ..domain.workstream import (
    EffectiveCell,
    EffectiveScene,
    GeoScope,
    SalesMotion,
    SceneConflict,
)
from ..ports.product_catalog import ProductCatalog
from ..ports.workstreams import EffectiveSceneStore

__all__ = [
    "EFFECTIVE_SCENE_NOT_REPORTED",
    "compile_effective_scene",
    "freeze_effective_scene",
    "preview_effective_scene",
    "read_effective_scene",
]


#: 这一层**答不上来**的那几格，逐条写成一句话（照
#: ``application/launch_preview.py::LAUNCH_NOT_REPORTED`` 的形状）。
#:
#: 那一份的 ``NotReported`` 逐字要求：``reason`` 不是一句「暂不支持」，每一句都要
#: **点名谁答得上**，或者点名今天缺的是哪一个生产者。这一份照办 —— 每一条都写清了
#: 「这一格为什么空着」与「要谁去补」。
#:
#: 写成一张表而不是让每处 ``continue`` 各说一句，是因为这几句话会被**两个**读者
#: 看到（编译面把它写进 ``not_reported``，读回面把它原样交出去），而两处各写一遍的
#: 那天，「读这份有效场景的人」与「编译它的人」读到的「为什么没有这一格」会是两句
#: 不同的话。
#:
#: 一处**缺席**与一处**冲突**不是同一件事，而这张表只装前者：冲突是「有两份说法」
#: （进 :class:`~marketops.domain.workstream.SceneConflict`），缺席是「一份都没有」。
EFFECTIVE_SCENE_NOT_REPORTED: dict[str, str] = {
    "geo_scope": (
        "这一格答不上来是因为这次编译**没有拿到**声明的地理范围（``GeoScope`` 由"
        "调用方读出来再传进来，见 :func:`compile_effective_scene` 的签名）。它**不是**"
        "「这个场景没有范围」—— 那件事在 ``SceneVersion.region`` 上，而它总是有值。"
        "补它的地方是读 GeoScope 的那一层（profile 装载 / 场景归属），不是这一层。"
    ),
    "sales_motion": (
        "同 ``geo_scope``：``SalesMotion`` 由调用方读出来再传进来。PSM §5 的冻结绑定里"
        "它是 ``buyer/sales_motion`` 那一格，而**读它的地方在场景归属那一层** —— "
        "这一层拿到的是一个值，拿不到的是它的来源。"
    ),
    "product": (
        "这一版 Scene 没有绑产品版本（``product_id`` / ``product_version`` 都没给）。"
        "**没有产品是一次合法的编译输入**：PSM §6 逐字「无价格可照样研究」，B01-f 逐字"
        "「没有 Product 的纯市场研究也能启动」。**不是**「产品这一格算不出来」—— "
        "那是另一句话，它出现在产品被指名但读不回来的时候，而那时候这一层是**抛**的"
        "（见 :func:`_read_product`），不是记一笔缺席。"
    ),
    "offer": (
        "这一版 Scene 没有绑方案。同 ``product``：没有方案照样编译得出。"
        "注意 ``offer`` 与 ``product`` 是**两格**：一个方案带着它自己那几版产品的引用"
        "（``OfferVersion.product_refs``），所以「有方案没产品」是一次说得通的输入。"
    ),
    "product_fit": (
        "没有 Product 就没有 ProductFit —— 这一格**明确为 unavailable**，而不是"
        "「空着」。两者的处置相反：unavailable 什么都不用补（这一版就是纯市场研究），"
        "空着要有人去看为什么。答得上这一格的是产品主档那一侧"
        "（``application/product_readiness.py`` 报的是同一件事的读数），而它要一份"
        "已经落盘的产品版本 —— 这一层读的是「有没有点名要它」。"
    ),
}


def _not_reported(name: str, why: str = "") -> str:
    """一条「答不上来」的读数：``<格名>: <为什么>``。

    ``why`` 是这一次调用特有的补充（例如「这一次编译没拿到它」），表里那一句是
    这一格的**通例**。两句合起来，而不是让调用点各写一遍 —— 通例在一个地方定义，
    特例在发生的地方补。
    """
    base = EFFECTIVE_SCENE_NOT_REPORTED.get(name, "")
    if base and why:
        return f"{name}: {base} {why}"
    if base:
        return f"{name}: {base}"
    if why:
        return f"{name}: {why}"
    raise KeyError(
        f"{name!r} 既不在 EFFECTIVE_SCENE_NOT_REPORTED 里，调用点也没给一句话。\n"
        "  一条说不出为什么的缺席，与「这一格本来就是空的」在报告上同形 —— "
        "而这张表就是用来把两者分开的。"
    )


def _cell(
    *,
    field: str,
    value: str,
    source: str,
    provenance: str,
    owner: str,
    precedence: int,
    unit: str = "-",
    locale: str = "-",
    overrides: tuple[str, ...] = (),
) -> EffectiveCell:
    """造一格。``value_sha256`` **由这一格的值散出来**，不由调用方给。

    这不是省一个参数：一个可以传进来的摘要会让「这一格的值被就地改过而摘要没跟上」
    在类型上说得通 —— 而它读起来完全正常。见
    ``domain/workstream.py::EffectiveCell._the_digest_is_the_value``。
    """
    return EffectiveCell(
        field=field,
        value=value,
        source=source,
        provenance=provenance,
        unit=unit,
        locale=locale,
        owner=owner,
        value_sha256=sha256_text(value),
        precedence=precedence,
        overrides=overrides,
    )


def compile_effective_scene(
    *,
    scene: SceneVersion,
    record: SceneRecord,
    workspace: str,
    geo: GeoScope | None,
    motion: SalesMotion | None,
    product: ProductVersion | None,
    offer: OfferVersion | None,
    at: datetime,
    compiler: str,
) -> EffectiveScene:
    """把一版 Scene 编译成 EffectiveScene。**纯函数。**

    入参全是**已经读出来的值** —— 它不碰端口、不碰时钟、不碰存储。三个可选的
    编译输入（``geo`` / ``motion`` / ``product`` + ``offer``）允许为 ``None``，
    而每一种缺席都落成一句**说出来的**读数（见
    :data:`EFFECTIVE_SCENE_NOT_REPORTED`），不是一次静默省略。

    编译结果只有两半：``cells``（算出来的）与 ``conflicts``（对不上的）。
    一处对不上的读法**不会**同时出现在两处 —— 那是「挑了赢家」的形态，
    而 B02-b 逐字不许挑。
    """
    at = to_utc(at)
    if record.scene_id != scene.scene_id:
        raise ValueError(
            f"记录说的是 {record.scene_id!r}，而声明说的是 {scene.scene_id!r}。\n"
            "  两格读自同一次调用的同一个 Scene —— 对不上说明有一格来自别处，"
            "而「这一版在哪个记录底下」没有第二个答案。"
        )
    if workspace != scene.workspace:
        raise ValueError(
            f"这次编译说的是工作区 {workspace!r}，而这一版 Scene 属于 "
            f"{scene.workspace!r}。\n"
            "  EffectiveScene 的可见性是**按工作区**判的（存储那一层按 "
            "``workspace`` 列过滤），所以一份跨工作区编译出来的结果会让一个工作区"
            "读得到另一个工作区的事实 —— 而那正是 B02 那条承重点要挡的东西。"
        )
    if record.workspace != workspace:
        raise ValueError(
            f"这次编译说的是工作区 {workspace!r}，而这一版的**记录**属于 "
            f"{record.workspace!r}。\n"
            "  记录与版本读自同一次调用的同一个场景 —— 对不上说明有一格来自别处"
            "（``SceneRecord.workspace`` 与 ``SceneVersion.workspace`` 是同一格的两个"
            "影子，而这里是它们相遇的地方）。"
        )

    cells: list[EffectiveCell] = []
    conflicts: list[SceneConflict] = []
    gaps: list[str] = []

    # --- GeoScope ------------------------------------------------------
    if geo is None:
        gaps.append(_not_reported("geo_scope"))
    else:
        cells.append(
            _cell(
                field="geo_scope",
                value="|".join(geo.regions) if geo.regions else "|".join(geo.countries),
                source=geo.source,
                provenance=geo.provenance,
                owner=geo.owner,
                precedence=0,
                # 单位写 ``"-"``（范围没有单位）而语言写它的来源语言 —— 两者都是
                # **说出来的**，没有一格是空串。
                unit="-",
                locale="-",
            )
        )
        # 场景声明里那几根地区**词**与 GeoScope 对不上时逐条点名。
        # 它们不是同一个东西（PSM §2 逐字：GeoScope 不等于「只有自由文本 Devon
        # 的搜索词」），所以这里判的不是「相不相等」而是**覆盖**：
        # 一个场景说要研究某个地区而 GeoScope 不含它，是一处真的对不上。
        uncovered = [region for region in scene.region if not geo.covers(region)]
        if uncovered:
            conflicts.append(
                SceneConflict(
                    field="geo_scope.coverage",
                    readings=(
                        f"scene:{scene.scene_id}@{scene.version}.region={','.join(uncovered)}",
                        f"geo_scope:{geo.source}={','.join(geo.regions) or ','.join(geo.countries)}",
                    ),
                    detail=(
                        "场景声明的这几根地区词不在 GeoScope 的覆盖范围内。"
                        "两条都留着：一条说「这个场景要做这几个地区」，一条说"
                        "「批准的范围是这几个」—— 哪一条对不是这一层判得了的事。"
                    ),
                )
            )

    # --- ScenarioProfile ------------------------------------------------
    # 场景的 ``scenario`` 那一格就是这个编译单元的 ScenarioProfile 名字，
    # 而它的**排除清单**来自 ``SceneVersion.exclusions``。
    #
    # ``overrides`` 收的是 exclusion 清单：那几行字正是**这一格压掉的读法**
    # （profile 装载层把它们当作不该再被算作法据的词）。把它逐条写出来而不是折成
    # 一个数，是因为「排除了几条」这个数回答不了「排除了哪几条」—— 而后者才是
    # 一次复盘要问的。
    cells.append(
        _cell(
            field="scenario_profile",
            value=scene.scenario,
            source=f"scene:{scene.scene_id}@{scene.version}",
            provenance="declared",
            owner=workspace,
            precedence=1,
            overrides=tuple(scene.exclusions),
        )
    )

    # --- SalesMotion ----------------------------------------------------
    if motion is None:
        gaps.append(_not_reported("sales_motion"))
    else:
        cells.append(
            _cell(
                field="sales_motion",
                value=motion.motion,
                source=motion.source,
                provenance=motion.provenance,
                owner=motion.owner,
                precedence=0,
                locale=motion.relation,
            )
        )

    # --- 产品与方案 -----------------------------------------------------
    # ``SceneVersion.product`` 是**产品/用途**（PSM §4 的八样之一），一个自由文本；
    # 目录里的产品版本是另一条轴上的东西。两者**不是**同一个字段的两种读法 ——
    # 把 ``工业清洗`` 与 ``ACME-9000@2`` 拿去比对会产出一处假冲突，而假冲突与真
    # 冲突在这一层的报告上同形（都进 ``conflicts``）。所以它们是两格。
    cells.append(
        _cell(
            field="product_use",
            value=scene.product,
            source=f"scene:{scene.scene_id}@{scene.version}",
            provenance="declared",
            owner=workspace,
            precedence=0,
        )
    )
    if product is None:
        gaps.append(_not_reported("product"))
        # B01-f 逐字：没有 Product 的纯市场研究也能启动，且 ProductFit **明确为
        # unavailable**，不是静默省略。两格都记 —— ``product`` 说「没绑产品版本」，
        # ``product_fit`` 说「所以这一格在这一版上无法给出」。只记前者的实现让读的人
        # 自己去推后一句，而「推出来」与「写下来」在报告上不是同一件事。
        gaps.append(_not_reported("product_fit"))
    else:
        cells.append(
            _cell(
                field="product",
                value=f"{product.product_id}@{product.version}",
                source=f"product_catalog:{product.product_id}@{product.version}",
                provenance=product.status.value,
                owner=product.owner,
                precedence=2,
                unit=product.units or "-",
                # 同一条轴上「卖的是什么」，目录这一格比场景那一格说得死（一个版本
                # 对一段自由文本）。被盖住的那一条**逐条写出来**，不折成一个数。
                overrides=(f"product_use={scene.product}",),
            )
        )
    if offer is None:
        gaps.append(_not_reported("offer"))
    else:
        cells.append(
            _cell(
                field="offer",
                value=f"{offer.offer_id}@{offer.version}",
                source=f"product_catalog:{offer.offer_id}@{offer.version}",
                provenance=offer.status.value,
                owner=offer.owner,
                precedence=2,
                unit="-",
                locale=",".join(offer.locales) or "-",
                overrides=(f"product_use={scene.product}",),
            )
        )
        # 方案绑的产品版本与场景声明的那一个对不上 ⇒ 逐条点名，**不挑**。
        if product is not None and offer.product_refs:
            named = ProductRef(product_id=product.product_id, version=product.version)
            if named not in offer.product_refs:
                conflicts.append(
                    SceneConflict(
                        field="offer.product",
                        readings=(
                            f"scene.product={scene.product}",
                            f"offer:{offer.offer_id}@{offer.version}"
                            f".product_refs="
                            + ",".join(f"{r.product_id}@{r.version}" for r in offer.product_refs),
                        ),
                        detail=(
                            "场景声明要做的产品不在这个方案绑的产品清单里。"
                            "一次「方案改绑了别的产品版本」与一次「场景写错了产品」"
                            "在这一层是同一件事，而处置不同 —— 要人去看。"
                        ),
                    )
                )

    # --- 可被覆盖的策略 --------------------------------------------------
    # 这一族取自 ``SceneVersion`` 自己的那几格，是「按哪一版规则做」的正文。
    # 每一格都把**它盖住了谁**逐条写出来（``overrides``）而不是只留一个整数：
    # 「优先级逐条写出来」这句话的落点就是这一格。
    cells.append(
        _cell(
            field="role_policy",
            value=scene.role_policy,
            source=f"scene:{scene.scene_id}@{scene.version}",
            provenance="declared",
            owner=workspace,
            precedence=2,
        )
    )
    for line in scene.budget:
        cells.append(
            _cell(
                field=f"budget.{line.line.value}",
                value=str(line.ceiling),
                source=f"scene:{scene.scene_id}@{scene.version}.budget",
                provenance="declared",
                owner=workspace,
                precedence=2,
                unit="units",
            )
        )
    cells.append(
        _cell(
            field="outreach.max_per_wave",
            value=str(scene.outreach.max_per_wave),
            source=f"scene:{scene.scene_id}@{scene.version}.outreach",
            # ``OutreachPolicyDecl.is_calibrated`` 是一个属性（由 ``uncalibrated``
            # 逐条算出来），不是一格。每一份用到未标定节奏的回执都要说出这一点
            # （那个类的 docstring 逐字），所以这一格不必等下游去发现。
            provenance="calibrated" if scene.outreach.is_calibrated else "uncalibrated",
            owner=workspace,
            precedence=2,
            unit="messages",
        )
    )
    cells.append(
        _cell(
            field="source_plan",
            value="|".join(scene.source_plan),
            source=f"scene:{scene.scene_id}@{scene.version}",
            provenance="declared",
            owner=workspace,
            precedence=1,
            unit="-",
        )
    )

    cells.sort(key=lambda cell: (cell.precedence, cell.field))
    return EffectiveScene(
        scene_id=scene.scene_id,
        version=scene.version,
        workspace=scene.workspace,
        compiled_from=scene.frozen_sha256,
        at=at,
        compiler=compiler,
        cells=tuple(cells),
        not_reported=tuple(sorted(gaps)),
        conflicts=tuple(conflicts),
    )


def preview_effective_scene(
    *,
    scene: SceneVersion,
    record: SceneRecord,
    workspace: str,
    catalog: ProductCatalog,
    at: datetime,
    compiler: str,
    geo: GeoScope | None = None,
    motion: SalesMotion | None = None,
    product_id: str | None = None,
    product_version: int | None = None,
    offer_id: str | None = None,
    offer_version: int | None = None,
) -> EffectiveScene:
    """编译一份**还没有写下去**的 EffectiveScene。

    与 :func:`compile_effective_scene` 的分工只有一条：这一个会去**读**产品与方案
    （``ProductCatalog``，一个读端口）。它仍然够不到检索与抓取 —— 那一半由
    ``tests/test_effective_scene_is_offline.py`` 的 import 闭包钉住。

    产品与方案**成对**出现：只给了 ``offer_id`` 而没有产品的场景是一个合法的请求
    （方案自己带着 ``product_refs``）。两格都给不全的时候**不猜** ——
    指名了但读不回来是一个**错误**，不指名是一个**缺席**，两者的处置相反。
    """
    product = _read_product(
        catalog,
        product_id=product_id,
        product_version=product_version,
        at=at,
        named_because="场景声明了它",
    )
    offer = _read_offer(
        catalog,
        offer_id=offer_id,
        offer_version=offer_version,
        at=at,
    )
    return compile_effective_scene(
        scene=scene,
        record=record,
        workspace=workspace,
        geo=geo,
        motion=motion,
        product=product,
        offer=offer,
        at=at,
        compiler=compiler,
    )


def freeze_effective_scene(
    *,
    store: EffectiveSceneStore,
    scene: SceneVersion,
    record: SceneRecord,
    workspace: str,
    catalog: ProductCatalog,
    at: datetime,
    compiler: str,
    geo: GeoScope | None = None,
    motion: SalesMotion | None = None,
    product_id: str | None = None,
    product_version: int | None = None,
    offer_id: str | None = None,
    offer_version: int | None = None,
) -> EffectiveScene:
    """编译并且**落库**，回盘上那一份。冻结表 §3.2 那条链的第二个箭头。

    三步，一步都不省：

    1. 编译（:func:`preview_effective_scene`，纯函数 + 一次目录读）。
    2. ``put_scene`` —— 不可变的写路径。``(scene_id, version)`` 上内容不同 ⇒
       ``StoreError``，而那句话点名是哪一个 ``(scene_id, version)``。
    3. **读回来**。与 ``put_launch`` 的返回值逐字同一条纪律：交出去的必须是**盘上
       那一行**，不是刚拼出来的那个对象。

    第 3 步在这里比在 ``put_launch`` 那里还多管一件事：重发同一次编译时 ``put_scene``
    是幂等的（摘要相等 ⇒ 什么都不做），所以盘上那一行的 ``at`` / ``compiler`` 是
    **第一次**那一对的。回刚拼出来的那个对象会让调用方读到「这一次是什么时候编译的」，
    而盘上留着的是另一对 —— 两个答案里只有后者是**这一版的事实**。

    ``compiler`` 由调用方给（与 ``preview_effective_scene`` 同一条）。它进不了
    ``digest`` —— 那是**这一次动作**的身份，不是这一份内容的身份；把它装进摘要会让
    同一次编译被另一个人重发时读成一次冲突，而重发是幂等的一半。
    """
    compiled = preview_effective_scene(
        scene=scene,
        record=record,
        workspace=workspace,
        catalog=catalog,
        at=at,
        compiler=compiler,
        geo=geo,
        motion=motion,
        product_id=product_id,
        product_version=product_version,
        offer_id=offer_id,
        offer_version=offer_version,
    )
    store.put_scene(compiled)
    frozen = store.get_scene(compiled.scene_id, compiled.version)
    if frozen is None:  # pragma: no cover - put_scene 成功之后不可能
        raise ConfigError(
            f"有效场景 {compiled.scene_id!r} 第 {compiled.version} 版刚写成功却读不"
            "回来 —— 存储那一层坏了。"
        )
    return frozen


def _read_product(
    catalog: ProductCatalog,
    *,
    product_id: str | None,
    product_version: int | None,
    at: datetime,
    named_because: str,
) -> ProductVersion | None:
    """指名了就必须读得回来。**读不回来是一次错误，不是一个缺席。**

    两种缺席的处置相反：没有产品 ⇒ 编译照样成立、``product_fit`` 明确 unavailable
    （B01-f）；**指名了却读不回来** ⇒ 这一份编译结果会声称一个不存在的产品版本，
    而此后每一处引用它的地方都看不出来。所以后者在这里抛。
    """
    if product_id is None and product_version is None:
        return None
    if product_id is None or product_version is None:
        raise ValueError(
            f"产品和版本必须成对给：product_id={product_id!r}，"
            f"product_version={product_version!r}。\n"
            "  只给一半时「哪一版」没有答案，而 ``ProductRef`` 的 docstring 逐字写着"
            "「只写 product_id 再去解释一次哪一版，是把那次解释留给每个调用点各做"
            "一遍」。"
        )
    found = catalog.get_version(product_id, product_version)
    if found is None:
        raise ValueError(
            f"产品 {product_id!r} 第 {product_version} 版读不出来，而这次编译"
            f"指名要它（{named_because}，取于 {at.isoformat()}）。\n"
            "  读不回来与「没有产品」是两件事：后者是一次合法的编译输入（这一版"
            "有效场景会明确记下 product_fit 不可用），而前者会让这份结果声称一个"
            "不存在的产品版本 —— 此后每一处引用它的地方都看不出来。"
        )
    return found


def _read_offer(
    catalog: ProductCatalog,
    *,
    offer_id: str | None,
    offer_version: int | None,
    at: datetime,
) -> OfferVersion | None:
    """同 :func:`_read_product`。方案可以独立于产品出现（它自带 ``product_refs``）。"""
    if offer_id is None and offer_version is None:
        return None
    if offer_id is None or offer_version is None:
        raise ValueError(
            f"方案和版本必须成对给：offer_id={offer_id!r}，"
            f"offer_version={offer_version!r}。"
        )
    found = catalog.get_offer(offer_id, offer_version)
    if found is None:
        raise ValueError(
            f"方案 {offer_id!r} 第 {offer_version} 版读不出来，而这次编译指名要它"
            f"（取于 {at.isoformat()}）。\n"
            "  与产品那一处同一条：指名了却读不回来是一次错误，不是一个缺席。"
        )
    return found


def read_effective_scene(
    store: EffectiveSceneStore, *, scene_id: str, version: int, workspace: str
) -> EffectiveScene:
    """按 ``(scene_id, version)`` 读一份**已冻结**的。读不到就抛，并把**盘上有的**点名。

    三句话分得开，而它们的处置相反：

    * 不在盘上 ⇒ 把盘上**确实有**的版本号写出来。「这一版不存在」与「这一版存在
      但什么都没编译出来」不是同一件事 —— 后者是盘上有行、内容是空，前者是
      根本没有那一行。一条只报「读不到」的实现把两者说成了同一句。
    * 在盘上、但属于**别的工作区** ⇒ 拒绝，并点名它属于谁。这是跨工作区那条
      承重点在**用例层**的落点：存储那一层按 workspace 过滤，而这里再判一次是因为
      「按主键读」这条路绕过了那层过滤（主键是 ``(scene_id, version)``，
      没有 workspace）。
    * 在盘上、也是本工作区的 ⇒ 返回。

    两个拒绝用的是既有的类型，不是新造的：``ConfigError``（分类与
    ``application/`` 里「没有 ``scene_id`` 这个场景」那一族一致），
    ``EvidenceError``（跨工作区的那一条 —— ``adapters/jsonl/store.py::_require_own``
    就是这个形状的先例，那里的注释逐字：「不属于 → ``EvidenceError``」，
    理由也逐字同一条：混在一起之后「取到的这一行是谁的」没有答案，
    而它的表现不是报错，是两批行被静静地混在一起返回）。
    """
    found = store.get_scene(scene_id, version)
    if found is None:
        known = store.versions(scene_id)
        listed = "、".join(str(item) for item in known) if known else "（一个都没有）"
        raise ConfigError(
            f"场景 {scene_id!r} 的第 {version} 版没有编译过。\n"
            f"  盘上已经编译过的版本：{listed}。\n"
            "  「这一版不存在」与「这一版存在但什么都没编译出来」不是同一件事 —— "
            "前者要去编译它，后者要去看那次编译为什么没有产出。"
        )
    if found.workspace != workspace:
        raise EvidenceError(
            f"场景 {scene_id!r} 第 {version} 版属于工作区 {found.workspace!r}，"
            f"而这次读它的是 {workspace!r}。\n"
            "  有效场景是**按工作区**的事实：一个工作区读得到另一个工作区的有效场景，"
            "等于它的研究范围、来源与负责人对后者可见。\n"
            "  存储那一层按 ``workspace`` 过滤（``scenes``），而「按主键读」这条路绕过"
            "了那层过滤 —— 主键是 ``(scene_id, version)``，上面没有工作区。所以这一处"
            "再判一次，而不是假定调用方只会问自己的场景。"
        )
    return found

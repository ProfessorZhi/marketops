"""有效场景那**两条只读**工具的处理器（B02-b，切片 §3.2 的 EffectiveScene 那一行）。

``preview_effective_scene`` 编译、``get_effective_scene`` 读已经冻结的那一份。两条都在
``_CONTROL`` + 阶段 ``K`` 上，两条都**不写一行**：写那一半在 ``start_workstream``
（冻结表 §3.2 逐字「``preview_effective_scene`` 编译（纯函数、零 provider 调用）→
``start_workstream`` 冻结落库」）。这一层因此不拿 ``EffectiveSceneStore`` 的**写面**：

* 端口是拿了的（``get_effective_scene`` 要按 ``(scene_id, version)`` 读回冻结的那一份，
  而 ``EffectiveSceneStore`` 是一个端口、读写两面同住一处，没有只读的那一半可拿）；
* 而**预览那一条一次都不碰它** —— ``_preview`` 的函数体里没有对 ``effective`` 的调用。
  这一条不是自觉：``tests/test_mcp_effective_scene_is_offline.py`` 属那个名字在本模块里
  出现的**次数**，一次以上就红。少了写的能力，「这一条是读」才不是一句自觉。

## 为什么这一族必须够不到 ``tools.py``

与 ``launch.py`` 那一族逐字同一条理由：``tools.py`` 经 ``adapters.build_stamp`` 够得到
``subprocess``，而这一族的判据是「零外部调用」。判据钉在
``tests/test_effective_scene_is_offline.py`` —— 它算的是**接线面的 import 闭包**
（含这个文件自己），而应用层的闭包由 ``tests/test_effective_scene.py`` 那一份算。

## ``at`` 取自服务端时钟

``preview_effective_scene`` 的回执上有一格 ``at``（这一份编译取在什么时候），而
``GeoScope`` / ``SalesMotion`` 各带一格 ``at``（这一份范围读在什么时候）。三格取自
**同一次** ``utc_now()``：一次读数里有好几个判定，各读一次时钟会让这份读数自相矛盾，
而报告上看不出来（与 ``launch.py`` / ``product_readiness.py`` 逐字同一条）。

## 两条工具**共用一份读数形状**

``_reading`` 是这个模块里唯一的回执生产者，两条工具都用它。两处各拼一份的后果是
「同一份 ``(scene_id, version)``，预览说一套、读回说另一套」—— 两份都自洽，而只有把
它们摆在一起才看得出来（与 ``launch.py`` 交 ``as_dict()`` 原样那一句同一条纪律）。

## ``compiler`` 是**工具名**，不是调用者的身份

``EffectiveScene.compiler`` 要「一个说得出是谁编译了它的名字」。这里填工具名
（``preview_effective_scene`` / ``start_workstream``）：那一格答的是**哪一条路径**
编译了它，而它是稳定的 —— 调用者的身份在这一层读不到（actor 由守卫解析，不进处理器
签名），而且 ``compiler`` 进不了 ``digest``，一个每个调用者各不相同的值会让同一次
编译在盘上留下谁都说不清的一格。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ...application.effective_scene import preview_effective_scene as _compile
from ...application.effective_scene import read_effective_scene as _read_frozen
from ...config import Settings
from ...domain.errors import ConfigError, EvidenceError, StoreError
from ...domain.util import utc_now
from ...domain.workstream import EffectiveScene, GeoScope, SalesMotion
from ...ports.product_catalog import ProductCatalog
from ...ports.scenes import SceneStore
from ...ports.workstreams import EffectiveSceneStore
from ._shared import Handler, readable, require_scene, store_failure
from .launch import _require_record, _require_version

#: ``EffectiveScene.compiler`` 上写的那两格。见模块 docstring 最后一节。
PREVIEW_COMPILER = "preview_effective_scene"

#: 编译一次有效场景可能要读的那几格（产品与方案）在提示里点的那两个名字。
_PRODUCT_KEYS = ("product_id", "product_version", "offer_id", "offer_version")


def _mapping(payload: Mapping[str, Any] | list[Any] | None, *, field: str) -> dict[str, Any]:
    """一格可选的编译输入必须是**一张表**（或者不在）。

    一个列表或一个字符串在这里被收下的话，``GeoScope`` 会拿它拼出一个默认值填满的
    对象 —— 而那份范围的 ``countries`` / ``exclusions`` 会是空的，于是它看上去像
    「这个部署没有声明任何范围」。拒绝比默认值诚实：缺的那一格与写错的那一格要分开。
    """
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ToolError(
            f"``{field}`` 要么不给，要么给一张表，收到的是 "
            f"{type(payload).__name__}。\n"
            "  这一格是**编译输入**：它缺省时这一版有效场景会把对应的那一格记成"
            "「答不上来」（``not_reported`` 里逐条点名），而写错形状时那件事必须看得见 —— "
            "一个被当成空表收下的范围会编译出一份看起来正常的有效场景。"
        )
    return dict(payload)


def geo_of(
    payload: Mapping[str, Any] | list[Any] | None, *, at: Any, workspace: str
) -> GeoScope | None:
    """把一格入参拼成一个 :class:`GeoScope`（不给就不拼）。

    ``source`` / ``provenance`` **没有缺省**：``GeoScope`` 的校验器逐字要求它们非空
    （「一份范围和一句模型的猜测在盘上同形」）。``owner`` 缺省取本部署的工作区 ——
    一份由这个部署写下来的范围，它的第一负责人就是这个部署。
    """
    table = _mapping(payload, field="geo")
    if not table:
        return None
    try:
        return GeoScope(
            countries=tuple(table.get("countries") or ()),
            regions=tuple(table.get("regions") or ()),
            exclusions=tuple(table.get("exclusions") or ()),
            source=table.get("source", ""),
            provenance=table.get("provenance", ""),
            at=at,
            owner=table.get("owner") or workspace,
        )
    except ValidationError as exc:
        raise ToolError("``geo`` 这一格拼不成一个 GeoScope：\n" + readable(exc)) from exc


def motion_of(
    payload: Mapping[str, Any] | list[Any] | None, *, at: Any, workspace: str
) -> SalesMotion | None:
    """把一格入参拼成一个 :class:`SalesMotion`（不给就不拼）。同 :func:`geo_of`。"""
    table = _mapping(payload, field="motion")
    if not table:
        return None
    try:
        return SalesMotion(
            motion=table.get("motion", ""),
            relation=table.get("relation", ""),
            source=table.get("source", ""),
            provenance=table.get("provenance", ""),
            at=at,
            owner=table.get("owner") or workspace,
        )
    except ValidationError as exc:
        raise ToolError("``motion`` 这一格拼不成一个 SalesMotion：\n" + readable(exc)) from exc


def _refuse(exc: BaseException) -> ToolError:
    """一条域层拒词 → 一条 ``ToolError``。**措辞一个字都不改写。**

    ## 为什么这一族必须自己接住这三类（实测过，不是风格问题）

    SDK 的 ``mcp/server/mcpserver/tools/base.py``（**不是本仓的路径** —— 本仓
    ``adapters/mcp_server/`` 下没有 ``base.py``；写全以免下一个人在仓里找）把处理器
    抛出的每一条**非** ``MCPError`` 分进四支，其中两支正是这一族要躲的：``ToolError``
    原样保留措辞（前面加一句 ``Error executing tool <name>:``，``:207``），而**别的
    任何异常**落成 ``UnexpectedToolError("Error executing tool <name>")``（``:210``，
    同处注释逐字「the exception's own text stays on the server」）—— 也就是那句话
    **一个字都到不了调用方**。

    而本函数接的这几类**恰恰落在 ``:210`` 那一支**，不是校验器那一支（``:158``）：
    ``ConfigError`` / ``ValueError`` / ``EvidenceError`` 都是**工具体**（含它调下去的
    应用层）抛出来的，而 ``:158`` 管的是参数校验与 ``default_factory`` 自己炸掉。

    这一族原本只接了 ``StoreError``，于是四条本来就该说出口的拒绝全部落进了第二支：

    * ``ConfigError``：读一个没有编译过的版本 —— 那句「盘上已经编译过的版本：
      （一个都没有）」正是 ``get_effective_scene`` 那条 summary 逐字要求的东西
      （「不在盘上的版本被**拒绝**并把盘上有的版本点名写出来」），而它在今天这条
      路径上**整个丢掉了**，调用方只看到「工具执行出错」；
    * ``ValueError``：指名了一个读不回来的产品 / 方案 —— 那句话点的是**名字与版本号**，
      也就是调用方唯一能据以改东西的两格；
    * ``EvidenceError``：跨工作区（今天被 ``require_scene`` 挡在前面，所以走不到，
      但这一条判据不该依赖另一条判据的次序）。

    ``StoreError`` 走 :func:`~._shared.store_failure`（同一个形状，只是那一支的由来
    写在它的 docstring 上）；``ValidationError`` 走 :func:`~._shared.readable` ——
    ``str`` 会把调用方给的那段载荷整个回显回来，几十行里没有一行是「哪个字段、为什么」。
    ``KeyError``（``_not_reported`` 收到一个不在表里的缺口名）**故意不接**：那是本仓的
    编程错误，而一次崩溃不该伪装成一次调用方的拒绝。
    """
    if isinstance(exc, ValidationError):
        return ToolError("这一份有效场景拼不出来：\n" + readable(exc))
    return ToolError(str(exc))


def _reading(scene: EffectiveScene) -> dict[str, Any]:
    """一份有效场景的回执。**这个模块里唯一的形状定义。**

    ``digest`` 在这里加进去，虽然它是 ``EffectiveScene`` 的属性而不是字段：它是这份
    读数上最要紧的一格（「这一版还是编译时的那一份吗」），而字段与属性在
    ``model_dump()`` 上的差别恰好会让它**缺席**。读的人拿它去比对，而「没有这一格」
    与「摘要对不上」在报告上不同形，所以它必须在。
    """
    return {**scene.model_dump(mode="json"), "digest": scene.digest}


def _preview(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    catalogs: Callable[[], ProductCatalog],
    scene_id: str | None,
    version: int | None,
    geo: Mapping[str, Any] | list[Any] | None,
    motion: Mapping[str, Any] | list[Any] | None,
    product_id: str | None,
    product_version: int | None,
    offer_id: str | None,
    offer_version: int | None,
) -> dict[str, Any]:
    """编译一次，**不落库**（「被预览的那个动作必须还没发生」）。"""
    chosen = require_scene(settings, scene_id)
    at = utc_now()
    try:
        store = scenes()
        # 那两条「读哪一版 / 盘上有什么」的拒绝与启动预览**同一份实现**（``launch.py``
        # 的 ``_require_record`` / ``_require_version``，下划线名字跨模块 import 的
        # 先例是 ``runs.py`` 从 ``product_loop`` 取 ``_plan_context`` 那一行）：它们
        # 答的是同一个问题（这一版声明在不在盘上），而第二份实现漂移的那天，两条工具
        # 会在同一个请求上给出两句不同的话 —— 两边都自洽。
        record = _require_record(store, scene_id=chosen, workspace=settings.profile)
        declared = _require_version(
            store,
            scene_id=chosen,
            version=version,
            latest_version=record.latest_version,
        )
        scene = _compile(
            scene=declared,
            record=record,
            workspace=settings.profile,
            catalog=catalogs(),
            at=at,
            compiler=PREVIEW_COMPILER,
            geo=geo_of(geo, at=at, workspace=settings.profile),
            motion=motion_of(motion, at=at, workspace=settings.profile),
            product_id=product_id,
            product_version=product_version,
            offer_id=offer_id,
            offer_version=offer_version,
        )
    except StoreError as exc:
        raise store_failure(exc) from exc
    except (ConfigError, EvidenceError, ValueError) as exc:
        raise _refuse(exc) from exc
    return _reading(scene)


def effective_scene_handlers(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    catalogs: Callable[[], ProductCatalog],
    effective: Callable[[], EffectiveSceneStore],
) -> dict[str, Handler]:
    """有效场景那两条工具，按工具名索引。

    四个工厂都必填，理由与 ``launch.py`` 那一族逐字相同：``scenes`` 缺省会让「这个
    部署没有场景存储」与「这个 Scene 还没立项」同形；``catalogs`` 缺省会让「忘了接线」
    与「这一版没有绑产品」同形 —— 而后者是一条**正确的**编译结果（B01-f：没有 Product
    的纯市场研究照样编译得出），于是接线漏了会以一条正确读数的形状出现。

    ``effective`` **必须与 ``workstream_handlers`` 收到的是同一个工厂**：冻结的那一侧
    （``start_workstream``）与读的这一侧指向两个库时，两边各自都正常，而
    「刚冻结的那一版读不出来」会与「它从来没被冻结过」同形。这一条与组合根里
    ``scenes`` 交给两处的那条纪律逐字相同。

    ## ``preview_effective_scene`` 收不收 ``scene_version``

    收（叫 ``version``，与 ``preview_launch`` 那一格同名）。不给时取记录里的
    **最新版**：要预览的那一版通常就是刚写下、还没生效的那一版。四格版本号一起报是
    ``preview_launch`` 那一族的形状，而这一条的编译结果**自己带着** ``version``，所以
    「编译的是哪一版」在回执上不含糊。
    """

    def preview_effective_scene(
        scene_id: str | None = None,
        version: int | None = None,
        geo: Mapping[str, Any] | None = None,
        motion: Mapping[str, Any] | None = None,
        product_id: str | None = None,
        product_version: int | None = None,
        offer_id: str | None = None,
        offer_version: int | None = None,
    ) -> dict[str, Any]:
        return _preview(
            settings=settings,
            scenes=scenes,
            catalogs=catalogs,
            scene_id=scene_id,
            version=version,
            geo=geo,
            motion=motion,
            product_id=product_id,
            product_version=product_version,
            offer_id=offer_id,
            offer_version=offer_version,
        )

    def get_effective_scene(scene_id: str | None = None, version: int = 0) -> dict[str, Any]:
        """读一份**已冻结**的。读不到时的拒绝把**盘上有的版本**点名写出来。

        ``version`` 没有缺省：这一条读的是**那一版的事实**，而「最新的一版」在这里
        没有定义（盘上冻结的版本与记录上的最新版是两件事 —— 后者说的是声明）。给一个
        缺省会让一次读在没有那一版时悄悄换一期读。
        """
        chosen = require_scene(settings, scene_id)
        if version < 1:
            raise ToolError(
                f"``version`` 从 1 起，收到的是 {version}。\n"
                "  这一条按 ``(scene_id, version)`` 读一份**已经冻结**的有效场景 —— "
                "没有版本号就没有主语，而「第 0 版」不是一个版本。"
            )
        try:
            scene = _read_frozen(
                effective(), scene_id=chosen, version=version, workspace=settings.profile
            )
        except StoreError as exc:
            raise store_failure(exc) from exc
        except (ConfigError, EvidenceError, ValueError) as exc:
            raise _refuse(exc) from exc
        return _reading(scene)

    return {
        "preview_effective_scene": preview_effective_scene,
        "get_effective_scene": get_effective_scene,
    }


__all__ = ["PREVIEW_COMPILER", "effective_scene_handlers", "geo_of", "motion_of"]

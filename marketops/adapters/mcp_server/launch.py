"""启动预览的**只读处理器**（B01 第五条关闭条件，切片 b01-d）。

``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:104`` 第 5 项逐字八个字：

    启动授权前零外部调用

而同一份文档 ``:102`` 那条启动用例逐字要求「启动用例先 preview：固定研究目标 /
GeoScope / Scenario、可选 Product/Offer、policy、预算/周期与下一步动作；**有批准才
持久受理** Scene/Workstream 及 Job」。两句话合起来是同一件事的两面：被 preview 的
那个动作**必须还没发生**，而 preview 自己必须是一次**读数**。

判据全部住在 ``application/launch_preview.py``（它的 docstring 写清了三张清单从哪
来、``at`` 为什么是参数）；这一层只做四件事：取时刻、把「主语不在盘上 / 是别人的」
翻成一条拒绝、把存储层的失败翻成一条可读回执、把 ``LaunchPreview.as_dict()`` 原样
交出去。**一个格子都不在这里重算。**

## 为什么这一族必须够不到 ``tools.py``

``tools.py`` 经 ``adapters.build_stamp`` 够得到 ``subprocess``，而 ``README`` 那份
口径逐字是「真的会对外发起请求、**或起一个外部进程**」。这一族存在的理由就是「启动
授权前零外部调用」，所以它宁可绕路也不 import 那一族：

* ``require_scene`` 从 ``._shared`` 引（那里现在是**定义**所在，见那个模块的
  docstring）—— 而不是从 ``tools`` 引那个转手；
* ``require_bundle`` 一次都不用：这一层读的是**盘上那一版声明的字面**，而 profile
  目录里的规则由 ``get_scene`` 那一格回答（``_scene`` 的 docstring 逐字写着两者
  「会不一致，而**不一致本身是要被看见的事实**」）。在这里顺手读一遍 profile，还会
  让「这一版声明」与「此刻的规则」在报告里合成一格。

这一条不是自觉：``tests/test_mcp_launch_is_offline.py`` 算的是 import 闭包。

## ``at`` 取自服务端时钟，不是参数

与 ``product_readiness.py`` 那一族逐字同一条。那三张清单的**每一行**都拿同一个时刻
判（被引的说法到这一刻过期了没有、被引的产品版本这一刻是不是 ``retired``），而一个
能倒填的时刻会让同一条盘上事实在两个调用方那里得到相反的两份读数 —— 两边各自都正常，
没有一处会响。

反过来说，这一族**读的是当下**：下一毫秒再读会拿到新的时刻、新的结论，而那不是缓存
失效。``application/launch_preview.py`` 收的 ``at`` 是必填参数，正是因为「一次读数
里有好几处判定，各读一次时钟会让这份读数自相矛盾」。

## 三个工厂都必填，理由各是「缺省会让两件事同形」

* ``scenes``：主语那一版与它的立项记录。缺省会让「这个部署没有场景存储」与「这个
  Scene 还没立项」在报告上同形，而两者的处置不同（一个去接线，一个去立项）。
* ``catalogs`` / ``claims``：被引的产品版本与已批准说法。``claims`` 缺省的理由与
  ``product_readiness.py`` 那里逐字相同 —— 一个缺省的空读面会让「忘了接线」与
  「这一版一条说法都没引」同形，而后者恰好是一条**正确的**缺口
  （``approved_claim_refs`` 空着），于是接线漏了会以一条正确缺口的形状出现。

## 「还没有立项过」与「这一版不在盘上」是两句不同的话

两个都不该被折成一张全空的读数：**三张全空的读数与「什么都不缺」同形**，所以一个
写错场景名或版本号的调用方会拿到一份「可以开跑」的报告。两句拒绝各自点名下一步
（``create_scene`` 立项 / 核对版本号），并且都把记录里的读数（``latest_version`` /
``state``）一起给出来 —— 拒绝里最有用的一格是「盘上现在有什么」。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ...application.launch_preview import launch_preview
from ...config import Settings
from ...domain.errors import StoreError
from ...domain.scene import SceneRecord, SceneVersion
from ...domain.util import utc_now
from ...ports.outreach import OutreachStore
from ...ports.product_catalog import ProductCatalog
from ...ports.scenes import SceneStore
from ._shared import Handler, require_scene, store_failure


def _cross_workspace_refusal(
    *, scene_id: str, row_workspace: str, workspace: str
) -> ToolError:
    """那一行记录属于别的工作区时的那句拒绝。**两句话分开说，因为处置相反。**

    与 ``product_readiness._cross_workspace_refusal`` 同一条：读不到是「没有立项
    过」，读到了但是别人的是「找到的是别人的那一份」—— 后者在这一侧看起来只是一次
    成功的查询，而它此后会被当成本地事实读回去（这一版声明的区域、场景、产品、预算
    与角色策略，都不是本部署核对过的东西）。

    写侧不会产生这样的行（``_create_scene`` 逐字「工作区**就是**本部署的 Scene」，
    用的是同一个来源），所以读到它就是**库不自洽**：同一个库文件被两个
    ``MARKETOPS_PROFILE`` 的部署写过，或者库被换过。这一条守卫只能在这里 —— 库那一
    侧只在**写**的时候绑工作区（``sqlite/db.py::bind_workspace``，由第一行写入建立），
    读路径不查。
    """
    return ToolError(
        f"Scene {scene_id!r} 的记录写着 workspace={row_workspace!r}，"
        f"而本部署服务的是 {workspace!r}。\n"
        "  这一次读被拒，不是因为它找不到 —— 找到的**是别人的那一份**。\n"
        "  跨工作区的读不留痕：它在这一侧看起来只是一次成功的查询，而被读到的那"
        "一份此后会被当成本地事实读回去。"
    )


def _require_record(
    store: SceneStore, *, scene_id: str, workspace: str
) -> SceneRecord:
    """库里那一份记录，或一条说得出「下一步做什么」的拒绝。

    ``SceneRecord`` 的两条缺席理由在这一层只剩一条：库还没建那一种（``get_scene``
    的 ``record_state="no_store"`` / ``product_loop._scene_record`` 的第一段）由
    ``scenes()`` 的构造回答，走到这里时库已经被打开过。**但剩下那一条要说得更细**
    —— 这一层问的是「这一版能不能启动」，所以拒绝里必须带上「盘上现在有什么」。
    """
    record = store.get_record(scene_id)
    if record is None:
        raise ToolError(
            f"{scene_id!r} 还没有立项过（库里读不到记录）。\n"
            "  启动预览读的是**某一版已经写下的声明**，而它不存在时没有可预览的东西 —— "
            "请先用 create_scene 写下第 1 版（可选当场 activate）。"
        )
    if record.workspace != workspace:
        raise _cross_workspace_refusal(
            scene_id=scene_id, row_workspace=record.workspace, workspace=workspace
        )
    return record


def _require_version(
    store: SceneStore, *, scene_id: str, version: int | None, latest_version: int
) -> SceneVersion:
    """要读的那一版，或一条说得出「盘上现在有什么」的拒绝。

    ``version`` 缺省取记录里的**最新版** —— 那是「刚写完、还没受理」的那一版，也是
    一次启动要预览的那一版。选谁在这里决定，而**报告里四格一起报**
    （``version`` / ``latest_version`` / ``effective_version`` / ``is_effective``），
    所以「它读的是不是最新版」是一个读数，不是一个假设（理由见
    ``application/launch_preview.launch_preview`` 的 docstring）。

    ## 记录说**有**、而库里读不到那一版，是拒绝而不是 ``None``

    与 ``product_loop._plan_context`` 逐字同一条：「还没有生效版本」与「生效版本读
    不出来」是两句话 —— 前者是这个市场还没开张，后者是存储被改过、迁移只做了一半、
    或者库被换过。折成同一句的后果是**报告上说「没有这一版」**，而它其实有过、只是
    不见了，于是没有人会去查存储。
    """
    wanted = latest_version if version is None else version
    row = store.get_version(scene_id, wanted)
    if row is None:
        raise ToolError(
            f"Scene {scene_id!r} 的库里读不到第 {wanted} 版"
            f"（记录里说 latest_version={latest_version}）。\n"
            "  启动预览读的是**某一版已经写下的声明**，读不到它就没有可预览的东西 —— "
            "请核对你点名的版本号，或者用 add_scene_version 写下这一版。\n"
            "  「记录说有的那一版读不出来」是**另一句话**：存储不自洽（被改过、迁移只"
            "做了一半、库被换过）。它比「你写错了版本号」严重得多，所以这一条拒绝把两个"
            "读数都写出来，让读的人自己判是哪一种。"
        )
    return row


def _preview_launch(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    catalogs: Callable[[], ProductCatalog],
    claims: Callable[[], OutreachStore],
    scene_id: str | None,
    version: int | None,
    product_id: str | None,
    product_version: int | None,
    offer_id: str | None,
    offer_version: int | None,
) -> dict[str, Any]:
    """一次启动预览：取时刻、读三本本地的账、把判据交给应用层、原样交回。"""
    chosen = require_scene(settings, scene_id)
    at = utc_now()
    try:
        store = scenes()
        record = _require_record(store, scene_id=chosen, workspace=settings.profile)
        declared = _require_version(
            store,
            scene_id=chosen,
            version=version,
            latest_version=record.latest_version,
        )
        preview = launch_preview(
            scene=declared,
            record=record,
            workspace=settings.profile,
            at=at,
            catalog=catalogs(),
            claims=claims(),
            product_id=product_id,
            product_version=product_version,
            offer_id=offer_id,
            offer_version=offer_version,
        )
    except StoreError as exc:
        raise store_failure(exc) from exc
    return preview.as_dict()


def launch_handlers(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    catalogs: Callable[[], ProductCatalog],
    claims: Callable[[], OutreachStore],
) -> dict[str, Handler]:
    """启动预览**已接线**的那一条，按工具名索引。

    它是 ``_CONTROL`` + 阶段 ``J``，与产品环那几条同一条分界：启动是**经营一个市场**
    的动作。工具名取 ``preview_launch`` 而不是 ``launch_preview``，为的是与
    ``submit_run`` 那一边的一对（``preview_run`` / ``submit_run``）同形 —— 目录里
    读工具一律把动词放前面，于是 ``tools/list`` 上「预览一次启动」与「预览一轮 Run」
    挨着出现，而它们的区别（一个预览**受理**、一个预览**开跑**）在名字上就看得见。

    回执就是 ``LaunchPreview.as_dict()`` 那一份，一个格子不加、一个格子不减：报告的
    形状与判据的形状是**同一个对象**的两个出口（与 ``product_readiness_handlers``
    逐字同一条）。这一层再包一层 —— 加一个 ``_note``、或者把三张清单拍平成一句散文
    —— 都会造出第二处定义，而分家的表现是「同一条盘上事实，报告里说一套、判据里说
    另一套」。
    """

    def preview_launch(
        scene_id: str | None = None,
        version: int | None = None,
        product_id: str | None = None,
        product_version: int | None = None,
        offer_id: str | None = None,
        offer_version: int | None = None,
    ) -> dict[str, Any]:
        return _preview_launch(
            settings=settings,
            scenes=scenes,
            catalogs=catalogs,
            claims=claims,
            scene_id=scene_id,
            version=version,
            product_id=product_id,
            product_version=product_version,
            offer_id=offer_id,
            offer_version=offer_version,
        )

    return {"preview_launch": preview_launch}


__all__ = ["launch_handlers"]

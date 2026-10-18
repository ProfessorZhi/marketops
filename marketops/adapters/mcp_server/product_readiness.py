"""产品资料与方案的**查缺面**两个只读处理器（PSM §10，B01 第八条关闭条件）。

两句话各自要求一件否定的事，而两件都要在**启动之前**说得出：

* ``docs/PRODUCT_LINE_AND_SCENE_MODEL.md:229`` 逐字「启动前返回结构化
  missing/invalid/awaiting_approval，**不让模型读一段 traceback 猜**」；
* ``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:104`` 末句逐字「资料缺失返回具体
  missing 字段，不用临时 SQL 救流程」。

所以这一族交出去的是一份**读数**（三张清单 + 一张答不上来的清单），不是一句
「资料不全」，也不是一次让调用方自己去库里拼的判断。判据全部住在
``application/product_readiness.py``，这一层只做三件事：取时刻、把主语不在盘上
翻成一条拒绝、把存储层的失败翻成一条可读回执。**一个格子都不在这里重算。**

## ``at`` **不是**参数

时刻取自服务端时钟（``utc_now()``）。同一条纪律在 ``product_catalog._verifier``
（``approved_at``）与 ``tests/test_mcp_product_loop.py`` 的 ``_FORBIDDEN_PARAMETERS``
里已经写着：一个能倒填的时刻，会让「这一版现在能不能用」这句话的答案取决于谁在问、
什么时候问。这一族尤其：读数里那三张清单的**每一行**都拿同一个时刻判（被引用的说法
到这一刻过期了没有），而一个由调用方挑的时刻会让同一条盘上事实在两个调用方那里
得到相反的两份读数 —— 两边各自都正常，没有一处会响。

反过来说，这一族**读的是当下**：下一毫秒再读会拿到新的时刻、新的结论，而那不是缓存
失效。`decisions.py` 里有一处**相反**的选择（它按载荷给的时刻判，为的是同一次判定的
重试拿到同一个答案），差别在于那边判的是一条**要落盘的记录**，这边交的是一份**当时
的读数**。

## 两个工厂都必填

``catalogs`` 是产品资料与方案的读面（``ports/product_catalog.py``）；``claims`` 是
已批准说法的读面（``ports/outreach.py::approved_claims``），只有方案那一条用得上它。

``claims`` **没有缺省**，理由与 ``application/product_readiness.py`` 那里逐字同一条：
一个缺省的空读面会让「忘了把它接上」与「这一版一条说法都没引」在报告上同形 —— 而后者
恰好是一条要被报出来的缺口（``approved_claim_refs`` 空着），于是接线漏了会以一条
**正确的**缺口的形状出现，谁也看不出。

``catalogs`` 没有缺省的理由与 ``product_catalog.py`` 那句逐字相同：``None`` 是「这个
部署没有产品目录的落点」，那件事由组合根说，不由这里替它猜。

## 读不到那一条 ⇒ 拒绝，不是一张空读数

``application/product_readiness.py`` 的两个读法在主语不在盘上时返回 ``None``，而
``None`` 在这里变成 ``unknown_version_refusal`` 的一句 ``ToolError``。这一条是本片
最要紧的形状：**三张全空的读数与「什么都不缺」同形**，所以一个写错 id 的调用方会拿到
一份「可以开跑」的报告。那份拒绝里逐字写了这件事，也点了下一步（先登记，或者核对
主语写错没有）。

## 方案的版本属于一个工作区，产品版本不属于

``OfferVersion.workspace`` 与 ``ProductVersion``（**没有**那一列）的差别不是历史遗留：
B01 ``:104`` 逐字「一个获批产品版本可被两个 Scene 共用」，产品资料不属于任何一次投放。

于是这两条的读面对**工作区**的处理不同：

* ``get_offer_version_readiness`` 先读那一行、核 ``workspace``，不是本部署的就拒 ——
  与 ``runs.py::_one_run`` 逐字同一条（「跨工作区的读不留痕：它在这一侧看起来只是一次
  成功的查询，而被读到的那一行此后会被当成本地事实读回去」）。库那一侧只在**写**的
  时候绑工作区（``sqlite/db.py::bind_workspace``，由第一行写入建立），读路径不查
  （``SqliteProductCatalog.get_offer`` 是一行 SELECT），所以这一条守卫只能在这里。
* ``get_product_version_readiness`` **没有**这一条：那一版没有工作区可核，而补一列来
  让这个守卫成立正好是 ``:104`` 不要的。

那一行被**读了两遍**（一次为工作区守卫、一次给判据）：判据那一份的输入是端口，不是
一个由调用方带进来的行 —— 一个「把已经读出来的行交进去」的形状会让这一层能塞一份与
盘上不同的记录进去，而报告上的主语仍然写着盘上那个 id。

## 存储层的失败原样上抛，不重写

两个处理器各自把 ``StoreError`` 交给 ``._shared.store_failure``。这里多一处：**工厂
本身**也会抛（``SqliteProductCatalog.__init__`` 里跑 ``ensure_schema``，构造即验证），
所以 ``catalogs()`` / ``claims()`` 的调用也在那一对 try 里面。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ...application.product_readiness import (
    SubjectKind,
    offer_version_readiness,
    product_version_readiness,
    unknown_version_refusal,
)
from ...config import Settings
from ...domain.errors import StoreError
from ...domain.util import utc_now
from ...ports.outreach import OutreachStore
from ...ports.product_catalog import ProductCatalog
from ._shared import Handler, store_failure


def _cross_workspace_refusal(
    *, offer_id: str, version: int, row_workspace: str, workspace: str
) -> ToolError:
    """那一版属于别的工作区时的那句拒绝。**两句话分开说，因为处置相反。**

    与 ``runs.py::_one_run`` 同一条：读不到是「没有这一版」，读到了但是别人的是
    「找到的是别人的那一版」—— 后者在这一侧看起来只是一次成功的查询，而它此后会被
    当成本地事实读回去（那一版引用的产品版本、它靠的已批准说法，都不是本部署核对过
    的东西）。
    """
    return ToolError(
        f"Offer {offer_id!r} 的第 {version} 版写着 workspace={row_workspace!r}，"
        f"而本部署服务的是 {workspace!r}。\n"
        "  这一次读被拒，不是因为它找不到 —— 找到的**是别人的那一版**。\n"
        "  跨工作区的读不留痕：它在这一侧看起来只是一次成功的查询，而被读到的那"
        "一份此后会被当成本地事实读回去。"
    )


def _product_readiness(
    *,
    catalogs: Callable[[], ProductCatalog],
    product_id: str,
    version: int,
) -> dict[str, Any]:
    """产品资料一版的查缺读数。

    **不收 ``settings``**：这一版没有工作区可核（见模块 docstring），于是这一条工具
    一个格子都不来自本部署的配置 —— 主语由参数点名、时刻取自时钟、三条拒绝来自
    应用层。其余处理器族收它，是因为它们各自要 ``settings.profile``。
    """
    at = utc_now()
    try:
        reading = product_version_readiness(
            catalogs(), product_id=product_id, version=version, at=at
        )
    except StoreError as exc:
        raise store_failure(exc) from exc
    if reading is None:
        raise ToolError(unknown_version_refusal(SubjectKind.PRODUCT, product_id, version))
    return reading.as_dict()


def _offer_readiness(
    *,
    settings: Settings,
    catalogs: Callable[[], ProductCatalog],
    claims: Callable[[], OutreachStore],
    offer_id: str,
    version: int,
) -> dict[str, Any]:
    """方案一版的查缺读数。工作区守卫与理由写在模块 docstring 那一节。"""
    at = utc_now()
    try:
        catalog = catalogs()
        row = catalog.get_offer(offer_id, version)
        if row is not None and row.workspace != settings.profile:
            raise _cross_workspace_refusal(
                offer_id=offer_id,
                version=version,
                row_workspace=row.workspace,
                workspace=settings.profile,
            )
        reading = offer_version_readiness(
            catalog, offer_id=offer_id, version=version, claims=claims(), at=at
        )
    except StoreError as exc:
        raise store_failure(exc) from exc
    if reading is None:
        raise ToolError(unknown_version_refusal(SubjectKind.OFFER, offer_id, version))
    return reading.as_dict()


def product_readiness_handlers(
    *,
    settings: Settings,
    catalogs: Callable[[], ProductCatalog],
    claims: Callable[[], OutreachStore],
) -> dict[str, Handler]:
    """查缺面**已接线**的两个处理器，按工具名索引。

    两条都是 ``_CONTROL`` + 阶段 ``J``，与同族那三条（``put_product_version`` /
    ``put_offer_version`` / ``put_product_line``）逐字一致 —— 查缺读的是登记面写下去
    的那几版，两条路给同一组身份，没有放宽任何一处边界。

    回执就是 ``ProductReadiness.as_dict()`` 那一份，一个格子不加、一个格子不减：
    报告的形状与判据的形状是**同一个对象**的两个出口（``as_dict`` 的 docstring 逐字
    写着它「也是 MCP 只读面交出去的那一份」）。这一层再包一层 —— 加一个 ``_note``、
    或者把三张清单拍平成一句散文 —— 都会造出第二处定义，而分家的表现是「同一条盘上
    事实，报告里说一套、判据里说另一套」。
    """

    def get_product_version_readiness(product_id: str, version: int) -> dict[str, Any]:
        return _product_readiness(
            catalogs=catalogs, product_id=product_id, version=version
        )

    def get_offer_version_readiness(offer_id: str, version: int) -> dict[str, Any]:
        return _offer_readiness(
            settings=settings,
            catalogs=catalogs,
            claims=claims,
            offer_id=offer_id,
            version=version,
        )

    return {
        "get_product_version_readiness": get_product_version_readiness,
        "get_offer_version_readiness": get_offer_version_readiness,
    }


__all__ = ["product_readiness_handlers"]

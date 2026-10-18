"""产品目录**登记面**的三个处理器（B01 第四条关闭条件，``:104``）。

三个动词各登记一样东西：``put_product_version``（产品资料的一版）、
``put_offer_version``（某个工作区里 Offer 的一版）、``put_product_line``
（一条产品线）。它们与 ``ports/product_catalog.py`` 的七个方法一一对应 ——
四个 ``get_*`` / ``versions`` 是读面，不在这一片里（b01-c 的查缺面与 b01-d 的启动
预览要用它们，那时再开）。

## 「登记不产生网络调用」在这里是一处**构造**，不是一句保证

B01 ``:104`` 逐字要求「ProductLine/产品资料登记不产生网络调用」。一个否定句不能
靠自觉兑现：一处「不会发生」在第一万次修改里失效的方式是**静默**的 —— 登记面某天
为了「顺手校验一下官网」import 了搜索端口，行为上仍然正确（校验真的成功了），只是
「登记」从此会花钱、会联网、会在断网时失败，而没有任何一条判据会响。

所以这一条被做成**模块拿不拿得到网络能力**：``tests/test_registration_is_offline.py``
从本模块出发做 import 闭包，要求它够不到 ``ports.search`` / ``ports.acquisition``
以及那一批会对外发请求的厂商 SDK。

**这一条真的在管着这个文件。** 两个最自然的写法都会当场让它红，而两条都是实测的：

* ``from .tools import Handler, _readable`` —— ``tools`` →
  ``marketops.adapters.build_stamp`` → ``subprocess``；
* ``from .product_loop import _store_failure`` —— ``product_loop`` →
  ``marketops.application.acquisition`` → ``marketops.ports.acquisition``。

（第一条的 ``subprocess`` 是 ``build_stamp`` 读 git 版本用的，**那不是网络调用** ——
判据的口径故意粗，理由与它的上限一起写在那个文件的 docstring 上。）所以那几样公共
翻译搬去了 ``._shared``：一个只 import 异常类型与标准库的模块。``._shared`` 的
docstring 记了这件事的来龙去脉，这里不重复。

## 闭包而不是类

与 ``product_loop_handlers`` / ``outreach_handlers`` / ``control_handlers`` 同一个
理由：每个处理器都要 ``settings`` 与存储工厂，闭包让「哪一个工具用了哪一样」在签名
上看得见。

``catalogs`` 是**工厂**而不是实例：sqlite 连接不跨线程，一次调用开一次。与其余
处理器族的 ``scenes`` / ``store`` 逐字同一条。它**没有缺省** —— ``None`` 是「这个
部署没有产品目录的落点」，那件事由组合根说，不由这里替它猜。

## ``workspace`` 不是参数

``OfferVersion`` 与 ``ProductLine`` 都有 ``workspace`` 一格，而这一层**不收**它：
两处都取 ``settings.profile``。与本仓 ``create_scene`` 对 ``SceneVersion.workspace``
的处理逐字同源 —— 一个可以填工作区的接口，能让一次登记写进一个**本部署不是**的
工作区，而库那边的表现只是多出一行，回执上完全看不出来。

``ProductVersion`` 反过来：它**没有**工作区，库里也没有那一列。B01 ``:104`` 逐字
「一个获批产品版本可被两个 Scene 共用」——产品资料是「我们卖什么」，它不属于任何
一次投放。所以 ``put_product_version`` 的回执里**刻意没有** ``workspace`` 一格：
一个恒等于本部署名字的字段会被读成「这一版属于这个工作区」，而那正是 ``:104``
不要的读法。

## ``approved_by`` / ``approved_at`` / ``supersedes`` **不是**参数

三格各有一条本仓已经落地过的理由，而它们在这三个工具上同时成立：

* ``approved_by`` —— 取自 ``guard.current_principal()``，也就是传输层用 Bearer
  令牌**已经确立**的那个主体。``tests/test_mcp_product_loop.py:98-111`` 的
  ``_FORBIDDEN_PARAMETERS`` 逐字列着 ``by``（「批准人。取自传输层的令牌，不取自
  载荷」），而 ``outreach.py`` 的 ``authorize_wave`` 把理由写得最直白：「一个能由
  调用方填写的批准人字段，等于一份可以自己签名、自己盖章的审批记录，而记录上完全
  看不出来。」
* ``approved_at`` —— 服务端时钟（``utc_now()``）。同一条注释里的 ``at`` /
  ``created_at``：「能倒填的审计记录不是审计记录。」
* ``supersedes`` —— 由 ``version`` 推出来（``None if version == 1 else version - 1``），
  与 ``product_loop`` 对 ``SceneVersion`` 那一版的处理逐字同源（那里写着「不给调用方
  填」），也与 ``application/outreach.py`` 的 ``supersedes=None if version == 1 else
  version - 1`` 同一条。**这一格在本仓尤其空**：``domain/product.py`` 里 ``ProductVersion``
  与 ``OfferVersion`` **都没有**一条把 ``supersedes`` 绑到 ``version - 1`` 的校验器
  （``SceneVersion._supersedes_is_the_previous_one`` 那一类在这里不存在），所以「第 5
  版取代第 2 版」这句话今天只有本层能挡住。

### 这一节改过一次，改的理由是可查的

本文件先前的写法把这三格收成参数，理由是它们「记的是别处发生过的一件事」，与
``SourceArtifactRef.fetched_at`` / ``QuotedPrice.quoted_at`` 同类。**那个类比不成立。**
那两格说的是本系统**观察不到**的外部事件（某份资料什么时候被抓的、某个供应商什么时候
报的价），没有替代来源，所以调用方给的那份就是唯一的出处；而 ``ready`` 那一版的核对者
与核对时刻**是这一次调用做出的那个断言**，它有一处独立得多的来源（传输层已认证的身份
＋本机时钟）—— 于是载荷里那一份不再是出处，而是一份自签记录。
``ProductVersion._ready_means_verified`` 逐字要的是「『已核对』这件事没有核对者，就只是
一个更自信的 draft」，而「核对者」在盘上只该有一个来源。

两格只在 ``status="ready"`` 时才有值，其余状态一律 ``""`` / ``None``：一份挂在 ``draft``
上的批准记录是一句假话，而它在这里**表达不出来**。

**这一片仍然不决定「谁有权登记一个 ``ready`` 的版本」**（b01-e 才是那件事的落点）；
它保证的是一件更小、也更硬的事：写进那一格的名字**不是调用方自己写的**。

判据是「拒绝」那一侧的形状：``status="ready"`` 而 ``source_refs`` 空着时，拒绝来自
``ProductVersion`` 自己的校验器，不是这一层另写一句。（``_ready_means_verified`` 里
``approved_by`` / ``approved_at`` 那一半从本模块**够不到**：这两格只由本层填，而填不上
的情形在构造之前就先拒了 —— 见 ``_verifier``。）

## 参数拼成 dict 再 ``model_validate``，不是逐格手填构造器

**全部**校验留在域类型上（同一个字段的两个校验器不会分家），而这一层只做一件事：
把 domain 的拒绝翻成一条回执。``readable`` 会把 pydantic 的错误摊成「哪一个字段、
为什么」，所以 ``status="readdy"``、``approved_at="2026-09-24T09:00"``（不带偏移量）
各得到一句点名的话，而不是一句「构造不出来」。``runs.py`` 的 ``_delta`` 是同一形状。

## 存储层的拒绝原样上抛，不重写

「同一个 ``(id, 版)`` 配另一份内容」那句（那版在、内容不同、要发下一版）已经比这里
能补的更具体，重写一遍会让同一件事在存储层与工具层有两种说法 —— 与
``_shared.store_failure`` 那一段逐字同一条理由。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ...config import Settings
from ...domain.errors import StoreError
from ...domain.product import OfferVersion, ProductLine, ProductStatus, ProductVersion
from ...domain.util import utc_now
from ...ports.product_catalog import ProductCatalog
from ._shared import Handler, readable, store_failure
from .guard import current_principal


def _verifier(status: str) -> tuple[str, datetime | None]:
    """``ready`` 那一版的核对者与核对时刻：``(主体名, 本机时钟)``；其余状态两格皆空。

    两格都**不由参数给**，理由写在模块 docstring 那一节里。这里的顺序要紧：
    ``status`` 不是 ``"ready"`` 时直接返回空，于是 ``status="readdy"`` 那种笔误仍然
    由 ``ProductVersion`` 的校验器点名回话（而不是先被这里拒成一条与状态无关的话）。

    ## 为什么本模块自己写这两行，而不 import 现成的那两个

    ``product_loop._require_principal`` 与 ``review`` 里那一条**同款分支**都是本仓
    已有的写法（两处的拒绝语各自点自己的记录，是刻意的）。但它们各自所在的模块
    **正是本模块 import 不到的那两个** —— 实测：``product_loop`` →
    ``marketops.application.acquisition`` → ``ports.acquisition``；``review`` →
    ``tools`` → ``marketops.adapters.build_stamp`` → ``subprocess``。两条都会让
    ``tests/test_registration_is_offline.py`` 当场变红，所以「不抄一份」在这件事上
    没有第三条路可走：机制一行，措辞各家点各家的记录。

    留着一个可读的拒绝而不是让 ``AttributeError`` 逃出去，与那两处同一个理由：
    传输层那一侧先拒。**实测过一次，而且拒得比这里早**：不带令牌的会话连握手都过不去
    （``drive_with_settings(token=None)`` 拿回 ``Server returned an error response``），
    所以 MCP 那条路根本走不到这几行。这几行是给**绕过中间件**的那一侧读的 —— 直接调
    处理器的测试、以及将来某个不走 MCP 的调用方（handler 就是组合根里的一只普通函数）。
    ``tests/test_mcp_product_catalog.py`` 那一条因此走的是直达路径，并在 docstring 里
    写明它判的是哪一条路。
    """
    if status != ProductStatus.READY:
        return "", None
    principal = current_principal()
    if principal is None:
        raise ToolError(
            "这个动作需要主体：它的名字会被写进这一版产品目录记录的核对者一格，"
            "而主体取自传输层的 Bearer 令牌，不取自任何参数。"
        )
    return principal.name, utc_now()


def _superseded_by(version: int) -> int | None:
    """这一版取代了哪一版：**由版本号定**，不由调用方说。

    第 1 版取代不了任何一版（``None``），第 N 版取代第 N-1 版。``version`` 的
    下界由域类型上那一条 ``Field(ge=1)`` 管，所以这里不必再判一次 —— 而
    ``version=1`` 之外的一切都落在这条规则上。

    这一行不是「顺手算一下」，它是**这条链上的唯一守卫**：``domain/product.py``
    里 ``ProductVersion`` / ``OfferVersion`` 都没有 ``supersedes`` 与 ``version``
    的一致性校验器（``SceneVersion`` / ``OutreachDraft`` 那种在那边不存在），
    所以「第 5 版取代第 2 版」这句话一旦能从载荷进来，库里就再也查不出来了。
    """
    return None if version == 1 else version - 1


def _registered(
    *,
    settings: Settings,
    what: str,
    stored: ProductVersion | OfferVersion | ProductLine,
    next_version: int | None,
    note: str,
) -> dict[str, Any]:
    """登记成功的回执。``next_version`` 为 ``None`` 表示这一型**没有版本号**。

    ``stored`` 回的是**落库的那一份**整个，不是调用方给的那几个参数：域层有几格
    有缺省（``status`` 的 ``draft``、``capabilities`` 的空元组、``note`` 的空串），
    在工具层再抄一份缺省看着更友好，但那一份会成为第二处定义 —— 而分家的表现是
    「同一个对象从两个入口写进去，状态不一样」，回执上完全看不出来。

    ``next_version`` 与 ``outreach._published`` 的 ``next_revision`` 同一个用途：
    这些东西的旧版**改不了**（同一 ``(id, 版)`` 配另一份内容会被存储层拒），于是
    「改一处」在调用方那一侧只剩一个答案 —— **写下下一版**。把那个数写进回执，
    是让这件事不必读源码、也不必先撞一次墙。一条只出现在错误消息里的下一步，
    要等到人先失败一次才看得见。
    """
    receipt: dict[str, Any] = {
        "workspace": settings.profile,
        "registered": what,
        "stored": stored.model_dump(mode="json"),
        "_note": note,
    }
    if next_version is not None:
        receipt["next_version"] = next_version
    return receipt


def _put_product_version(
    *,
    settings: Settings,
    catalogs: Callable[[], ProductCatalog],
    product_id: str,
    version: int,
    display_name: str,
    owner: str,
    status: str,
    capabilities: Sequence[str] | None,
    limits: Sequence[str] | None,
    units: str,
    region_variants: Sequence[Any] | None,
    source_refs: Sequence[Any] | None,
    unknowns: Sequence[str] | None,
    note: str,
) -> dict[str, Any]:
    """登记产品资料的一版。"""
    approved_by, approved_at = _verifier(status)
    declared: dict[str, Any] = {
        "product_id": product_id,
        "version": version,
        "display_name": display_name,
        "owner": owner,
        "status": status,
        "capabilities": list(capabilities or ()),
        "limits": list(limits or ()),
        "units": units,
        "region_variants": list(region_variants or ()),
        "source_refs": list(source_refs or ()),
        "unknowns": list(unknowns or ()),
        "approved_by": approved_by,
        "approved_at": approved_at,
        "supersedes": _superseded_by(version),
        "note": note,
    }
    try:
        stored = ProductVersion.model_validate(declared)
    except ValidationError as exc:
        raise ToolError(
            f"这一版产品资料（product_id={product_id!r} version={version}）"
            "构造不出来：\n" + readable(exc)
        ) from exc
    try:
        catalogs().put_version(stored)
    except StoreError as exc:
        raise store_failure(exc) from exc
    return _registered(
        settings=settings,
        what="产品资料",
        stored=stored,
        next_version=stored.version + 1,
        note=(
            f"这一版（product_id={stored.product_id!r} version={stored.version}）"
            "的内容从此不再变：同一对 (product_id, version) 再写一次、内容相同是"
            "幂等的（重启后重放一批登记走的就是这条路），内容不同会被拒。"
            f"要改内容请登记第 {stored.version + 1} 版 —— 旧的这一版留在库里。\n"
            "  回执里没有 workspace：这一版**不属于任何一个工作区**（库里没有那一列）。"
            "B01 :104 逐字要求「一个获批产品版本可被两个 Scene 共用」，而一个归属列会"
            "让第二次共用读起来像越界。"
        ),
    )


def _put_offer_version(
    *,
    settings: Settings,
    catalogs: Callable[[], ProductCatalog],
    offer_id: str,
    version: int,
    name: str,
    purpose: str,
    owner: str,
    status: str,
    product_refs: Sequence[Any] | None,
    markets: Sequence[str] | None,
    locales: Sequence[str] | None,
    approved_claim_refs: Sequence[Any] | None,
    price: dict[str, Any] | None,
    note: str,
) -> dict[str, Any]:
    """登记本工作区里 Offer 的一版。"""
    approved_by, approved_at = _verifier(status)
    declared: dict[str, Any] = {
        "offer_id": offer_id,
        "version": version,
        # 工作区取本部署的那一个，不由调用方给（理由见模块 docstring）。
        "workspace": settings.profile,
        "name": name,
        "purpose": purpose,
        "owner": owner,
        "status": status,
        "product_refs": list(product_refs or ()),
        "markets": list(markets or ()),
        "locales": list(locales or ()),
        "approved_claim_refs": list(approved_claim_refs or ()),
        "price": price,
        "approved_by": approved_by,
        "approved_at": approved_at,
        "supersedes": _superseded_by(version),
        "note": note,
    }
    try:
        stored = OfferVersion.model_validate(declared)
    except ValidationError as exc:
        raise ToolError(
            f"这一版 Offer（offer_id={offer_id!r} version={version}）构造不出来：\n"
            + readable(exc)
        ) from exc
    try:
        catalogs().put_offer(stored)
    except StoreError as exc:
        raise store_failure(exc) from exc
    return _registered(
        settings=settings,
        what="Offer",
        stored=stored,
        next_version=stored.version + 1,
        note=(
            f"这一版（offer_id={stored.offer_id!r} version={stored.version}）的内容"
            "从此不再变：同一对 (offer_id, version) 再写一次、内容相同是幂等的，"
            f"内容不同会被拒。要改内容请登记第 {stored.version + 1} 版。\n"
            "  price 缺省（没给这一格）与给了那一格是**两件事**：前者是「还没人报过"
            "价」，而按 PSM :156 逐字「无价格可照样研究」；后者必须带金额、口径、"
            "谁报的与什么时候 —— 一个没有出处的数字在那一型上构造不出来。"
        ),
    )


def _put_product_line(
    *,
    settings: Settings,
    catalogs: Callable[[], ProductCatalog],
    line_id: str,
    name: str,
    owner: str,
    status: str,
    customer_problem: str,
    capability_family: str,
    product_refs: Sequence[Any] | None,
    approved_material_refs: Sequence[str] | None,
) -> dict[str, Any]:
    """登记一条产品线。"""
    declared: dict[str, Any] = {
        "line_id": line_id,
        "workspace": settings.profile,
        "name": name,
        "owner": owner,
        "status": status,
        "customer_problem": customer_problem,
        "capability_family": capability_family,
        "product_refs": list(product_refs or ()),
        "approved_material_refs": list(approved_material_refs or ()),
    }
    try:
        stored = ProductLine.model_validate(declared)
    except ValidationError as exc:
        raise ToolError(
            f"这条产品线（line_id={line_id!r}）构造不出来：\n" + readable(exc)
        ) from exc
    try:
        catalogs().put_line(stored)
    except StoreError as exc:
        raise store_failure(exc) from exc
    return _registered(
        settings=settings,
        what="产品线",
        stored=stored,
        # 没有下一版可发 —— 见下面那段 note。
        next_version=None,
        note=(
            f"这条产品线（line_id={stored.line_id!r}）的内容从此不再变：同一个 "
            "line_id 再写一次、内容相同是幂等的，内容不同会被拒 —— **不是覆盖**。\n"
            "  它**没有版本号**：PSM §4.1 说它是零行为分组，所以改一份分组没有"
            "「下一版」可发。要改，请用一个新 line_id —— 这个 id 的旧内容留在库里，"
            "因为「这一组的成员什么时候变的」正是有人来这里要查的东西。\n"
            "  产品版本**不必**先有一条产品线：分组是可选的，要求它会让产品线变成"
            "「一个没有分组的产品被就地编出来」的地方。"
        ),
    )


def product_catalog_handlers(
    *,
    settings: Settings,
    catalogs: Callable[[], ProductCatalog],
) -> dict[str, Handler]:
    """登记面**已接线**的三个处理器，按工具名索引。

    ``catalogs`` 必填（包括它只能是一个工厂这一件事）—— 理由写在模块 docstring
    里那一段：``None`` 是「这个部署没有产品目录的落点」，而那件事由组合根说。

    前三个工具的参数表里**没有** ``approved_by`` / ``approved_at`` / ``supersedes``
    —— 前两格取自传输层令牌与本机时钟、第三格由版本号推出来，理由与后果写在模块
    docstring 那一节。它们仍然出现在回执的 ``stored`` 里（那是落库的那一份整个），
    所以「这一次记的是谁、什么时候、取代了哪一版」在回执上看得见，只是**不由载荷
    决定**。``tests/test_mcp_product_catalog.py`` 用参数集合相等钉住这一条。
    """

    def put_product_version(
        product_id: str,
        version: int,
        display_name: str,
        owner: str,
        status: str = "draft",
        capabilities: list[str] | None = None,
        limits: list[str] | None = None,
        units: str = "",
        region_variants: list[dict[str, Any]] | None = None,
        source_refs: list[dict[str, Any]] | None = None,
        unknowns: list[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _put_product_version(
            settings=settings,
            catalogs=catalogs,
            product_id=product_id,
            version=version,
            display_name=display_name,
            owner=owner,
            status=status,
            capabilities=capabilities,
            limits=limits,
            units=units,
            region_variants=region_variants,
            source_refs=source_refs,
            unknowns=unknowns,
            note=note,
        )

    def put_offer_version(
        offer_id: str,
        version: int,
        name: str,
        purpose: str,
        owner: str,
        status: str = "draft",
        product_refs: list[dict[str, Any]] | None = None,
        markets: list[str] | None = None,
        locales: list[str] | None = None,
        approved_claim_refs: list[dict[str, Any]] | None = None,
        price: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _put_offer_version(
            settings=settings,
            catalogs=catalogs,
            offer_id=offer_id,
            version=version,
            name=name,
            purpose=purpose,
            owner=owner,
            status=status,
            product_refs=product_refs,
            markets=markets,
            locales=locales,
            approved_claim_refs=approved_claim_refs,
            price=price,
            note=note,
        )

    def put_product_line(
        line_id: str,
        name: str,
        owner: str,
        status: str = "draft",
        customer_problem: str = "",
        capability_family: str = "",
        product_refs: list[dict[str, Any]] | None = None,
        approved_material_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        return _put_product_line(
            settings=settings,
            catalogs=catalogs,
            line_id=line_id,
            name=name,
            owner=owner,
            status=status,
            customer_problem=customer_problem,
            capability_family=capability_family,
            product_refs=product_refs,
            approved_material_refs=approved_material_refs,
        )

    return {
        "put_product_version": put_product_version,
        "put_offer_version": put_offer_version,
        "put_product_line": put_product_line,
    }


__all__ = ["product_catalog_handlers"]

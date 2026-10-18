"""§10 第 2 步的**查缺面**：一次启动之前，这一版产品资料 / 方案还缺什么（切片 b01-c）。

## 权威句

``docs/PRODUCT_LINE_AND_SCENE_MODEL.md`` §10 逐字：

    启动前返回结构化 missing/invalid/awaiting_approval，不让模型读一段 traceback 猜

（:229）。同一件事在 B01 的第八条关闭条件上逐字：

    资料缺失返回具体missing字段，不用临时SQL救流程

（``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md``:104 末句）。**这一层是那两句里「结构化」
三个字的全部内容** —— 它不返回一句「资料不全」，也不把比对工作留给调用方。

第八条关闭条件的谓语是一个**否定**（「不用临时 SQL 救流程」），而否定句兑现不了自己
（「我们不做那件事」没有执行器）。所以它在这里被做成**构造**：调用方拿到的是一份
说得出**是哪一条记录的哪一格、以及为什么**的读数，那个「拼一条 SQL 看看差什么」的
念头需要的空白（「到底差什么」）不存在。这正是 ``docs/PRODUCT_LINE_AND_SCENE_MODEL.md``
:229 后半句的意思：「不让模型读一段 traceback 猜」—— 猜的前提是有东西可猜。

## 三张清单为什么是**三张**

§10 那句话点了三个名字，而它们是三种**不同的处置**：

| 清单 | 它说的话 | 拿到它的人下一步做什么 |
|---|---|---|
| ``missing`` | 有东西要**补**或要**改** | 补那一格（新的一版），或者改这一版引用的是谁 |
| ``invalid`` | 这份记录**指不到**它自称指着的东西 | 先修引用关系，补别的都白补 |
| ``awaiting_approval`` | 什么都不用补，**只差一个人** | 找负责人核对（``status="ready"``） |

压成一张清单的代价不是不好看：一张「还差 7 项」的表会让第三类被当成前两类去处理
—— 有人会去「补」一个本来只是没人签的东西（造出一版内容一模一样的重复记录），
或者反过来给一份引用已经断了的记录找签字人。``plan_preview.PreviewOutcome`` 的
docstring 对这一类形状论证过一次（「一个叫 ``ok`` 的布尔会把第三个结局读成 ``False``」），
这里是它在**三格**上的版本。

## 两个主语：产品资料的一版 与 方案的一版

两种读法各一个入口（:func:`product_version_readiness` / :func:`offer_version_readiness`），
**不合成一个收 ``kind`` 参数的函数**：两者读的端口不同（方案还要读 ``OutreachStore``
才判得了被引用的说法作不作数）、要补的格子不同、``missing`` 的判据几乎不重叠。合成
一个的结果是一个函数体里两套「如果是产品……否则……」—— 而那种形状下，「产品那一半
忘了判 ``unknowns``」在报告上与「这一版没有 unknowns」同形。

## ``at`` **必填，且只有一个**

到期核验与「被引用的说法在这一刻作不作数」都拿它判，与 ``application/run_preview.py``
的 ``run_preview(*, at=...)`` 是同一条纪律（那段 docstring 逐字：「**必填、且只有一个**
—— 到期核验、截止判定与格子读数都用它：三个判定拿三个时刻的话，一份读数内部就会
自相矛盾，而报告上看不出来」）。naive 时刻在 :meth:`ProductReadiness.__post_init__`
抛 ``ValueError``（措辞照 ``RunPreview``）。这一层**不读时钟**：时刻由调用方给。

## 主语不在盘上 ⇒ 返回 ``None``，不是一份空读数

一份三张全空的读数与「什么都不缺」在报告上**同形**（都是三个空表）—— 而这两种情况
要的下一步完全相反：一个可以去登记，另一个以为可以开跑。``ports/runs.py`` 的
``unknown_run_refusal`` 对同一类混淆写过逐字的一句：「两种情况都不该被当成『已经
结束了』」。所以读不到就返回 ``None``，由调用方渲染成它的那种拒绝；
:func:`unknown_version_refusal` 给出的就是那句话（返回字符串而不抛，与那三条
``*_refusal`` 同形）。

**它住在这里而不是端口上**：``ports/product_catalog.py`` 的 ``get_version`` 逐字
写着「不存在返回 ``None`` —— **不是抛异常**」，那是端口该说的话；而「读不到意味着
什么」只有一个消费者（下面那个 MCP 处理器），本仓的纪律是**等第二个实现出现再提取**
（``application/__init__.py`` 的模块 docstring 写过这条规矩的本体）。

## ``not_reported`` 是**减法**，不是重抄

照 ``run_preview._not_reported`` 的先例，分两半：

* **固定的一半**（:data:`NOT_REPORTED`）：与这一版引用了什么无关，是**这一层**答不上
  来的那些轴。两条读法回来的是**同一个对象**（逐字引用，不重写一个字）—— 同一句话
  的两份实现会在某一天分家。
* **条件的一半**：由**这一版引了什么**决定。今天只有一种：引用的说法版本**旧于盘上
  最新版**时，被引那一版的作用域读不出来（原因见那条工厂函数的 docstring）。
  引的都是最新版时它**为空** —— 一份与主语无关的常量清单做不到这件事，而把它写成
  常量正是「一律地答不上来」与「这一条答不上来」被读成同一句话的来路。

## 这一层不判的东西（写下来，因为「不判」与「忘了判」在报告上同形）

* ``price`` —— PSM 逐字「``None`` = 还没报价，而**照样可以研究**」（``domain/product.py``
  的 ``OfferVersion.price``）。价格缺席**绝不**进任何一张表，判据是一条反面的：
  :func:`offer_version_readiness` 的读法里没有 ``price`` 这个词的用武之地。
* ``region_variants`` —— 它是**收窄**语义（``domain/product.py`` 的
  ``_region_variants_narrow_only``），缺席的意思是「用主档那一份」。一份逐市场的
  收窄表不是「要补的一格」，所以它不进 ``missing``。
* ``ProductLine``（产品线）—— 它在域层是零行为的登记（没有任何判定读它），所以它
  没有查缺面。它的三个字段今天没有消费者，这一点与 ``SourceArtifactRef`` 同款，
  区别是后者被本模块记进 ``not_reported``，因为它**在**§4.2 那张检查表上。
* ``owner`` / ``display_name`` / ``name`` / ``workspace`` —— 构造上非空（域层的
  ``_owner_is_a_principal`` 一族与 ``_non_empty`` 校验器；``OfferVersion.purpose``
  是唯一一个**必填但无校验器**的 ``str``，所以它由本层判）。

## 写入面

**一个字节都不写**，也不花一次检索 —— 与 ``run_preview`` 同款：签名里只收
``ProductCatalog`` 与 ``OutreachStore`` 两个**本地**读面，类型注解里不出现任何检索 /
抓取端口的类名（那两族端口花的是外面的钱）。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..domain.outreach import ApprovedClaim
from ..domain.product import OfferVersion, ProductStatus, ProductVersion
from ..domain.util import to_utc
from ..ports.outreach import OutreachStore
from ..ports.product_catalog import ProductCatalog
from .not_reported import NotReported


class SubjectKind(str, Enum):
    """这份读数的主语是哪一族记录。**闭集，两个成员。**

    ``str`` 混入是为了报告：``as_dict()`` 里写出来的是 ``"product"``，不是一个读的人
    要去别处查表的数字。

    两个成员对应两个入口函数（见模块 docstring「两个主语」那一节）。**没有
    ``CLAIM``**：这一层不读「一条说法齐不齐」（它有没有编号、有没有出处分明是
    ``OutreachStore`` 自己的事），只在缺口里**指着**它 —— 那用一个字符串说法就够，
    那是 :func:`claim_subject`。
    """

    #: ``ProductVersion`` 的一版。
    PRODUCT = "product"
    #: ``OfferVersion`` 的一版。
    OFFER = "offer"

    def subject(self, ident: str, version: int) -> str:
        """这一族记录的主语写法：``product:probe-product@2``。

        **格式只有这一处定义**：``ProductReadiness.__post_init__`` 拿它校验主语与
        种类不许各写一遍（两处各写一遍的那天，报告上的主语与它自称的种类会分家）。
        """
        return f"{self.value}:{ident}@{version}"


def claim_subject(claim_id: str, revision: int) -> str:
    """缺口里指向**一条说法**的主语写法：``claim:uk-claim-1@2``。

    与 :meth:`SubjectKind.subject` 分开，因为一条说法不是这一层的主语族：本模块不
    返回「这条说法还缺什么」的读数（那是 ``OutreachStore`` 自己的事），只是在一条
    缺口里说清**是谁引起的**。
    """
    return f"claim:{claim_id}@{revision}"


@dataclass(frozen=True)
class ReadinessGap:
    """一张清单里的一行：**哪一条记录的哪一格**，以及为什么。

    ``why`` 不是一句「资料不全」：读的人的下一个问题永远是「补什么、为什么」。
    ``plan_preview.NotReported`` 的 docstring 对同一件事论证过一次（「``reason`` 不是
    一句『暂不支持』」），两处的落脚点一致：理由要点名**谁答得上**或**要动哪一格**。

    ## 两格的分工：``subject`` 说**关于谁**，``field`` 说**动哪里**

    ``field`` 永远是**这一份读数的主语**（:attr:`ProductReadiness.subject`）上的
    一格 —— 调用方能照着它去改的那一格。而 ``subject`` 是这条缺口**关于**的那条
    记录：通常就是主体本身（``limits`` 空着、``supersedes`` 断链），但常常是**它
    引用的那一条**（一条说法过期了、一版产品资料不在盘上）。

    分开写（而不是都塞进 ``why``）是因为报告要能数出「这一版引用的东西里有几条有
    问题」，而在散文里数不出来；而 ``field`` 只认主语那一格，是因为**调用方只能改
    这一份读数的主语** —— 指着一条引用说「它的 ``revision`` 有问题」时，手上没有
    能改的东西。
    """

    #: 这条缺口**关于**的那条记录：主体本身，或它引用的那一条。
    subject: str
    #: **主语**上要动的那一格，名字逐字取域模型上的字段名（``limits`` /
    #: ``approved_claim_refs``）。
    field: str
    #: 为什么这一格是缺口，以及下一步是**补**、**改**还是**去问谁**。
    why: str


#: 与这一版引用了什么**无关**、这一层答不上来的轴。两条读法回来的是同一个对象。
NOT_REPORTED: tuple[NotReported, ...] = (
    NotReported(
        item="来源工件在不在盘上",
        reason=(
            "§4.2 第 1 步点名要检查「来源」。这一层答得出的是**有没有写下来** —— "
            "``source_refs`` 里每一条都是自述完整的 ``SourceArtifactRef``"
            "（locator、content_sha256、fetched_at 三格都有校验器，写不出半条）；"
            "答不出的是那些字节**今天还在不在**：``SourceArtifactRef`` 这个类型的名字"
            "在整个 ``marketops/`` 里只出现在它自己的定义与一处 docstring 里，"
            "**没有任何一处按 ``artifact_id`` 去读一件来源工件**。"
            "所以「出处写下来了」与「出处还在」在这一层读成同一格 —— "
            "而两件事都成立才是 §4.2 要的那种来源。"
        ),
    ),
)


def _cited_revision_is_unreadable(
    *, claim_id: str, revision: int, latest: int, at: datetime
) -> NotReported:
    """引用的说法**不是最新版**时那一条「答不上来」。

    ## 为什么它是缺口，而不是被顶替掉的一格

    ``OutreachStore.approved_claims()`` 的 docstring 逐字：「每个 ``claim_id`` 的
    **最新**一版，按 ``claim_id`` 升序。**不在这里过滤过期** —— 过期是相对于 ``at``
    的判定，由调用方带着它的时刻做」。于是被引用的 ``(claim_id, revision)`` 里，
    ``revision`` 旧于最新版时，**那一版的期限与作用域读不出来**。

    ## 为什么不拿最新版顶替

    拿最新那一版的期限或作用域去判被引的那一版，会在最新一版**续过期**的那天把一条
    **有效**的引用报成过期 —— 而那是最坏的一类错：报告上读起来像「资料有问题」，
    实际是这一层拿错了对象。反过来（最新版过期而旧版未过期）它会把一条**过期**的引用
    报成有效，那更坏。两个方向都不行，所以这里如实说读不出来。

    ## 缺的不是记录

    旧版**在盘上**：两个实现（``adapters/memory.py`` 与 ``adapters/sqlite/outreach.py``）
    的 ``_latest`` 那一句逐字都是「只挑最新，**不删旧的**」。缺的是**端口上的一个
    按 ``(claim_id, revision)`` 读的方法** —— 所以这一条指向的是一个可以补的缺口
    （而那正是 ``NotReported`` 该说的话：点名谁答得上）。
    """
    return NotReported(
        item=f"{claim_id} 第 {revision} 版在 {to_utc(at).isoformat()} 还作不作数",
        reason=(
            f"这一版引的是 ``{claim_id}`` 的第 {revision} 版，而盘上这条说法已经有第 "
            f"{latest} 版了：``approved_claims()`` 只给每个 ``claim_id`` 的最新一版，"
            "被引那一版的作用域与期限读不出来。**不拿最新版顶替** —— 那样会在最新一版"
            "续过期时把一条有效的引用报成过期。缺的不是记录（旧版在盘上，两个实现都写着"
            "「只挑最新，不删旧的」），是端口上的一个按 ``(claim_id, revision)`` 读的方法。"
        ),
    )


@dataclass(frozen=True)
class ProductReadiness:
    """一条主档（或方案）在这一刻的查缺读数。

    ``frozen`` 且只由两个读法构造（照 ``plan_preview.PlanPreview``）。
    ``__post_init__`` 挡住三条「两个字段各写一遍就会有一个自洽的假答案」的组合；
    三条都**只有直接构造才能违反**，而 ``tests/test_product_readiness.py`` 逐个构造
    一遍：一个没人能违反的守卫与一个从不响的守卫，在报告上长得一样。
    """

    #: 主语，形如 ``product:probe-product@2``，由 :meth:`SubjectKind.subject` 生成。
    subject: str
    #: 它属于哪一族。
    kind: SubjectKind
    #: 这一版自己的状态。``usable`` 直接读它（见那一处的四档）。
    status: ProductStatus
    #: 这份读数取在什么时候。到期核验与被引说法的作用域都拿它判 —— **一次调用里
    #: 只有一个时刻**（见 :func:`product_version_readiness` 那段）。
    at: datetime
    #: 要补 / 要改的那些格。
    missing: tuple[ReadinessGap, ...] = ()
    #: 指不到它自称指着的东西的那些引用。
    invalid: tuple[ReadinessGap, ...] = ()
    #: 只差一个人核对（``status`` 还是 ``draft``）。**与上面两张不互斥**：草案可以
    #: 又缺东西又没人签，两张都报才诚实 —— 压成一张会让「补完就能跑」成为误读。
    awaiting_approval: tuple[ReadinessGap, ...] = ()
    #: §4.2 那张检查表上这一层**答不上来**的那几项（减法之后的）。见模块 docstring。
    not_reported: tuple[NotReported, ...] = ()

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError(
                "at 是一个 naive 时刻，而本仓的时刻一律带时区（``domain/util.py::"
                "to_utc``）。\n"
                "一个省掉时区的时刻会被按读它那台机器的本地时区解释，而这里拿它比的"
                "就是「被引用的说法到这一刻过期了没有」—— 于是「这一版能不能用」取决"
                "于这份读数在哪里跑。"
            )
        if not self.subject.startswith(f"{self.kind.value}:"):
            raise ValueError(
                f"主语 {self.subject!r} 与种类 {self.kind.value!r} 对不上 —— 主语要由 "
                "``SubjectKind.subject`` 生成，形如 ``product:probe-product@2``。\n"
                "两格各写一遍的那天，报告里的主语与它自称的种类会分家，而读的人拿到"
                "的是一条既像产品又像方案的记录。"
            )
        if self.awaiting_approval and self.status is not ProductStatus.DRAFT:
            raise ValueError(
                f"这一版的状态是 {self.status.value!r}，却带着 "
                f"{len(self.awaiting_approval)} 条「等批准」。\n"
                "等批准的意思是这一版还是 ``draft``：一份已经 ``ready`` 的记录在等谁"
                "的签字是自相矛盾的（它已经被签过了），而 ``retired`` 的那一版不会再"
                "有人去签它 —— 两种误读都会让人去找一个不存在的待办。"
            )

    @property
    def usable(self) -> bool:
        """这一版现在能不能被拿去开跑。

        **四档，不是一个布尔**（读的人要看得出该做什么）：

        * ``status`` 不是 ``ready`` ⇒ 不能用。草案与归档在这里是同一格 ——
          这一版没有被人核对过（或已经不再用），与它缺不缺东西无关。
        * ``missing`` 非空 ⇒ 不能用：有格子要补或要改，而补完之前它自称的范围是
          虚的（例如一条过期说法支撑的市场）。
        * ``invalid`` 非空 ⇒ 不能用：它指着的东西不在盘上，那么**它自称引用了什么
          本身就不可信**，补别的都白补。
        * ``awaiting_approval`` **不影响**这一格：它只在 ``status`` 是 ``draft`` 时
          非空，而 ``draft`` 已经被第一条挡住了。留着它参与判定会让这条性质读不出
          因果关系（一个恒真的合取项）。
        """
        return (
            self.status is ProductStatus.READY
            and not self.missing
            and not self.invalid
        )

    def as_dict(self) -> dict[str, Any]:
        """写进报告的形状 —— 也是 MCP 只读面交出去的那一份。

        ``at`` 一起带上（照 ``RunPreview.as_dict``）：这是一份**带时刻的读数**，
        而缺了它，报告里「被引用的说法作不作数」这个结论就没有可复核的时间锚点。
        """
        return {
            "subject": self.subject,
            "kind": self.kind.value,
            "status": self.status.value,
            "usable": self.usable,
            "at": self.at.isoformat(),
            "missing": [gap_as_dict(gap) for gap in self.missing],
            "invalid": [gap_as_dict(gap) for gap in self.invalid],
            "awaiting_approval": [gap_as_dict(gap) for gap in self.awaiting_approval],
            "not_reported": [
                {"item": item.item, "reason": item.reason} for item in self.not_reported
            ],
        }


def gap_as_dict(gap: ReadinessGap) -> dict[str, str]:
    """一张清单里的一行写进报告的形状 —— **一个定义，两个出口**。

    ## 为什么是自由函数，而不是 ``ReadinessGap.as_dict()``

    这是 ``scene_receipt.py`` 那条分界的同一条（那个模块的 docstring 逐字：「判据在域、
    形状在应用层、由适配层交出去」）：``ReadinessGap`` 是一个**值**，而这里说的是那个
    值**写出来长什么样**。写成方法会让「报告的形状」成为域对象的一部分，那时改一次措辞
    就是在改一个 ``frozen=True`` 的数据类。

    ## 为什么现在要提升成公开的

    它原本是 ``product_readiness.py`` 内部的私有助手（那时只有一个出口，
    :meth:`ProductReadiness.as_dict`）。第二个出口是启动预览
    （``application/launch_preview.py``，切片 b01-d）：那一层**自己**也产出一族
    ``ReadinessGap``（``_rollup`` 那四档），并且要把它们写进报告的同样三格里。两处各写
    一遍 ``{"subject": …, "field": …, "why": …}`` 的那天，它们会在某次改动之后分家 ——
    而报告上两边各自都正常，没有一处会响。

    三个键的名字**不是这一层的自由**：``subject`` 与 ``field`` 的分工写在
    :class:`ReadinessGap` 的 docstring 上（``subject`` 说「关于谁」、``field`` 说
    「动主语上的哪一格」），``why`` 说「补什么、去问谁」。少一个键会让「这一行是关于
    谁」与「要动哪一格」在报告上合成一句话，而调用方照着改的时候没有可改的东西。
    """
    return {"subject": gap.subject, "field": gap.field, "why": gap.why}


def unknown_version_refusal(kind: SubjectKind, ident: str, version: int) -> str:
    """读一条不在盘上的记录时的那句拒绝。**返回字符串而不抛**。

    与 ``ports/runs.py`` 的 ``unknown_run_refusal`` 同形（判据一份、返回字符串、
    由调用方决定放进哪一种拒绝里）。这里只有一个调用方（MCP 处理器，它抛
    ``ToolError``），住在本层是因为这句话说的是**读法**的意思而不是端口的意思：
    端口那句逐字写着「不存在返回 ``None`` —— **不是抛异常**」。
    """
    what = "产品资料" if kind is SubjectKind.PRODUCT else "方案"
    return (
        f"{what} {ident!r} 的第 {version} 版不在盘上。\n"
        "查缺面读的是**已经登记的那一份**。这里不返回一张三张全空的读数 —— "
        "一张空表与「什么都不缺」在报告上同形，而后者会让人以为可以开跑。"
        "要读它，先把它登记进去（或者核对一下主语写错了没有）。"
    )


def product_version_readiness(
    catalog: ProductCatalog,
    *,
    product_id: str,
    version: int,
    at: datetime,
) -> ProductReadiness | None:
    """产品资料的一版还缺什么；**不在盘上返回 ``None``**（见模块 docstring 那一节）。

    ``at`` 必填：这一版自己不带到期的想法（``ProductVersion`` 上没有期限那一格），
    但它引用的东西的判定仍用同一个时刻，而**一次调用里只有一个时刻**是这一层对外的
    承诺（见模块 docstring）。传一个 ``at`` 进来、却在内部另取一个「现在」，会让
    报告上那两行读数的时刻对不上而看不出来。
    """
    stored = catalog.get_version(product_id, version)
    if stored is None:
        return None
    kind = SubjectKind.PRODUCT
    subject = kind.subject(product_id, version)
    if stored.status is ProductStatus.RETIRED:
        return ProductReadiness(
            subject=subject, kind=kind, status=stored.status, at=at,
            not_reported=NOT_REPORTED,
        )
    return ProductReadiness(
        subject=subject,
        kind=kind,
        status=stored.status,
        at=at,
        missing=tuple(_product_missing(stored)),
        invalid=tuple(_product_invalid(catalog, stored)),
        awaiting_approval=_awaiting_approval(subject, stored.status),
        not_reported=NOT_REPORTED,
    )


def offer_version_readiness(
    catalog: ProductCatalog,
    *,
    offer_id: str,
    version: int,
    claims: OutreachStore,
    at: datetime,
) -> ProductReadiness | None:
    """方案的一版还缺什么；**不在盘上返回 ``None``**。

    ``claims`` 是一个**必填**参数，没有缺省值。一个缺省的空读面会让「忘了把它接上」
    在报告上与「这一版一条说法都没引」同形 —— 而后者恰好是一条要被报出来的缺口
    （``approved_claim_refs`` 为空），于是那个错误会以一条**正确的**缺口的形状出现，
    谁也看不出接线漏了。
    """
    stored = catalog.get_offer(offer_id, version)
    if stored is None:
        return None
    kind = SubjectKind.OFFER
    subject = kind.subject(offer_id, version)
    if stored.status is ProductStatus.RETIRED:
        return ProductReadiness(
            subject=subject, kind=kind, status=stored.status, at=at,
            not_reported=NOT_REPORTED,
        )
    latest = {claim.claim_id: claim for claim in claims.approved_claims()}
    return ProductReadiness(
        subject=subject,
        kind=kind,
        status=stored.status,
        at=at,
        missing=tuple(_offer_missing(stored, latest, at=at)),
        invalid=tuple(_offer_invalid(catalog, stored, latest, at=at)),
        awaiting_approval=_awaiting_approval(subject, stored.status),
        not_reported=_offer_not_reported(stored, latest, at=at),
    )


def _awaiting_approval(
    subject: str, status: ProductStatus
) -> tuple[ReadinessGap, ...]:
    """这一版是不是在等一个人核对。**``draft`` 与非 ``draft`` 二分，没有别的分支。**

    与另外两张清单不互斥（见 :attr:`ProductReadiness.awaiting_approval`）：
    ``__post_init__`` 只钉住「非 ``draft`` 不许有这一条」，那是这一格唯一能自相矛盾
    的方向。
    """
    if status is not ProductStatus.DRAFT:
        return ()
    return (
        ReadinessGap(
            subject=subject,
            field="approved_by",
            why=(
                "这一版还是 ``draft`` —— 没有人核对过它。要的是一位负责人"
                "（``owner`` 那一格写着是谁，就去问谁）把它核对成 ``ready``；"
                "登记面那一趟会顺手写下核对人与核对时刻，**不用调用方自己签**"
                "（``adapters/mcp_server/product_catalog.py`` 的 ``_verifier``）。"
                "它进的是「等批准」而不是「要补」：草案本身不是缺陷，它就是一个还"
                "没签的东西 —— 而「补」会让人去造一版内容一样的重复记录。"
            ),
        ),
    )


def _product_missing(stored: ProductVersion) -> Iterator[ReadinessGap]:
    subject = SubjectKind.PRODUCT.subject(stored.product_id, stored.version)
    for unknown in stored.unknowns:
        yield ReadinessGap(
            subject=subject,
            field="unknowns",
            why=(
                f"这一版自己写下了一条今天还不知道的：{unknown!r}。"
                "``unknowns`` 里的东西不会自己变成一个答案 —— 要么查清楚（新的一版），"
                "要么把它降级成一条说得出边界的 ``limits``。"
                "带着它开跑是最坏的那一种：报告上它长得像一条已经处理过的备注。"
            ),
        )
    if not stored.capabilities:
        yield ReadinessGap(
            subject=subject,
            field="capabilities",
            why=(
                "``capabilities`` 是空的：这一版没有写下它能做什么。"
                "一格空的能力面在调用方那里与「什么都能做」同形 —— 而这一层是那条"
                "误读唯一会被挡住的地方。"
            ),
        )
    if not stored.limits:
        yield ReadinessGap(
            subject=subject,
            field="limits",
            why=(
                "``limits`` 是空的：这一版没有写下它**不**能做什么。"
                "PSM §4.2 第 1 步点名的「用途限制」就是这一格 —— "
                "一格空着的限制表与「没有限制」同形，而那是这一条主档最贵的一种误读。"
            ),
        )
    if not stored.units.strip():
        yield ReadinessGap(
            subject=subject,
            field="units",
            why=(
                "``units`` 是空的：这一版的能力按什么单位数（次 / 公顷 / 套）读不出来。"
                "它不妨碍这一版被引用，但任何一句关于数量的话到了下游都没有统一的读法 —— "
                "而这一格是唯一能写下它的地方（域层没有校验器，也没有别处对它做约定）。"
            ),
        )
    if not stored.source_refs:
        yield ReadinessGap(
            subject=subject,
            field="source_refs",
            why=(
                "``source_refs`` 是空的：这一版的能力没有出处。"
                "它说的是**写下来没有**（那些字节今天还在不在是另一件事，见 "
                "``not_reported``）—— 而一格都没有时，连「写下来了」都不成立。"
            ),
        )


def _product_invalid(
    catalog: ProductCatalog, stored: ProductVersion
) -> Iterator[ReadinessGap]:
    subject = SubjectKind.PRODUCT.subject(stored.product_id, stored.version)
    if stored.supersedes is None:
        return
    if catalog.get_version(stored.product_id, stored.supersedes) is not None:
        return
    yield ReadinessGap(
        subject=subject,
        field="supersedes",
        why=(
            f"这一版写着它取代第 {stored.supersedes} 版，而那一版不在盘上。"
            "取代是一条**指向盘上另一行**的引用，指不到时这一版的历史读不出来 —— "
            "而「上一版说了什么」是评审批次与纠错回看的唯一依据。"
        ),
    )


def _offer_missing(
    stored: OfferVersion, latest: dict[str, ApprovedClaim], *, at: datetime
) -> Iterator[ReadinessGap]:
    subject = SubjectKind.OFFER.subject(stored.offer_id, stored.version)
    if not stored.purpose.strip():
        yield ReadinessGap(
            subject=subject,
            field="purpose",
            why=(
                "``purpose`` 是空的：这一版方案没有写下它是干什么的。"
                "它是 ``OfferVersion`` 上**必填却没有校验器**的两个字符串格之一"
                "（另一个是 ``name``）—— 域层挡住了 ``offer_id`` / ``workspace`` / "
                "``owner``，没挡它。这一层只判 ``purpose``：``name`` 是一处都读不到就"
                "会立刻被发现的标题，而 §4.2 第 1 步点名的「用途」就是 ``purpose``，"
                "空着的时候没有任何下游读得出这一版是干什么的。"
            ),
        )
    if not stored.markets:
        yield ReadinessGap(
            subject=subject,
            field="markets",
            why=(
                "``markets`` 是空的：这一版方案没有写下它在哪些市场用。"
                "PSM §4.2 第 1 步点名的「适用市场」就是这一格 —— "
                "空的市场表与「哪个市场都行」同形，而后者是这一层绝不能替它做的断言。"
            ),
        )
    if not stored.locales:
        yield ReadinessGap(
            subject=subject,
            field="locales",
            why=(
                "``locales`` 是空的：这一版方案没有写下它用哪些语言。"
                "它决定被引用的说法要不要逐语言覆盖，所以空着的时候下面那一族"
                "（期限 / 市场 / 语言）一格都问不了。"
            ),
        )
    if not stored.product_refs:
        yield ReadinessGap(
            subject=subject,
            field="product_refs",
            why=(
                "``product_refs`` 是空的：这一版方案没有点名它讲的是哪一版产品资料。"
                "``OfferVersion`` 的 READY 校验器要求这一格与 ``markets`` 都非空 —— "
                "草案可以空着，而空着的草案不可能被核对成 ``ready``，所以这是一条"
                "**必须先补**的格子，不是一条等批准。"
            ),
        )
    if not stored.approved_claim_refs:
        yield ReadinessGap(
            subject=subject,
            field="approved_claim_refs",
            why=(
                "``approved_claim_refs`` 是空的：这一版方案一条被核对过的说法都没引。"
                "一份没有说法的方案仍然可以被登记（域层没有这条校验器），但它输出的"
                "每一句话都没有出处 —— 而「有出处」正是外向链上那句话能被用的前提。"
            ),
        )
    yield from _scope_gaps(stored, latest, at=at)


def _scope_gaps(
    stored: OfferVersion, latest: dict[str, ApprovedClaim], *, at: datetime
) -> Iterator[ReadinessGap]:
    """被引用的说法**在 ``at`` 这一刻作不作数** —— 四轴里的哪一轴不作数就报哪一轴。

    四轴（期限 / 产品 / 市场 / 语言）与 ``domain/outreach.py`` 的
    ``ApprovedClaim.why_not_applicable`` 是**同一处判据**：这里不重写一遍，只把它的
    返回值接过来。``applies_to`` 逐字论证过的「一次检查」在这里是同一个形状 ——
    「过期的说法」与「跨市场的说法」在报告上是同一格，拆开之后调用点只会记住一半。

    ## 产品轴在这一层是**另一件**事，所以不靠 ``why_not_applicable``

    ``applies_to(product=...)`` 问的是「这条说法适用于**这个**产品吗」，而 ``None``
    在它那里逐字是「不限」。这里要问的是**跨记录**的那一个问题：「这条说法绑的产品
    是不是这一版方案引用的一版」。所以：

    * 产品轴在这里自己判（见下面第一个循环）—— 拿 ``why_not_applicable`` 判它会在
      两处都问到「说法绑的是它自己吗」这种恒真的问题；
    * 下面那个叉积里传的 ``product=claim.product_ref`` 因此是**按构造恒真**的一格，
      这样那一趟只剩期限 / 市场 / 语言三轴 —— 而那三轴正是 ``why_not_applicable``
      逐条说得出理由的那三句。

    ## 为什么是 ``markets`` × ``locales`` 的叉积

    ``OfferVersion`` 没有把某一门语言绑到某一个市场上（两格各自是一张扁平的表），
    所以这一版声明的范围就是它们的**叉积**。这个读法是**保守**的：某一格其实不会被
    用到时它照样会报出来，而反过来（某格不被覆盖却报「齐」）是这一层绝不能犯的错。
    按 ``why`` 去重 —— 同一句话不为第 4 格重写一遍（每一句自己点名是哪个市场 /
    哪门语言）。

    ``markets`` 或 ``locales`` 有一格是空的时候**整个跳过**：那时叉积没有格子可问，
    而空格子本身已经是一条要补的（上面那两张表的对应行），在这里再报一句「范围没
    核」是同一件事的第二种说法。
    """
    if not (stored.markets and stored.locales):
        return
    for ref in stored.approved_claim_refs:
        claim = latest.get(ref.claim_id)
        if claim is None or claim.revision != ref.revision:
            # 不在盘上 / 引的版比盘上最新还新 —— 那是 invalid 那两张表的事；
            # 这里再报一条会让同一件事出现在两张清单上，而读者会以为要修两处。
            continue
        if claim.product_ref is not None and claim.product_ref not in stored.product_refs:
            yield ReadinessGap(
                subject=claim_subject(ref.claim_id, ref.revision),
                field="approved_claim_refs",
                why=(
                    f"这一版说法绑的是 product_id={claim.product_ref.product_id!r} "
                    f"version={claim.product_ref.version}，而这一版方案引用的是 "
                    f"{[f'{p.product_id}@{p.version}' for p in stored.product_refs]}。"
                    "说法绑的产品不在方案的引用清单里 —— 引用它等于把一条关于别的产品"
                    "的话挂在这一版方案上。"
                ),
            )
        reasons: dict[str, None] = {}
        for market in stored.markets:
            for locale in stored.locales:
                why = claim.why_not_applicable(
                    product=claim.product_ref, market=market, locale=locale, at=at
                )
                if why:
                    reasons[why] = None
        for why in reasons:
            yield ReadinessGap(
                subject=claim_subject(ref.claim_id, ref.revision),
                field="approved_claim_refs",
                why=why,
            )


def _offer_invalid(
    catalog: ProductCatalog,
    stored: OfferVersion,
    latest: dict[str, ApprovedClaim],
    *,
    at: datetime,
) -> Iterator[ReadinessGap]:
    """这一版方案**指着的东西**还有没有：产品资料那一版、以及被引的说法本身。

    ## 说法那一支为什么判的是**盘上最新那一版**的 ``retired``

    ``approved_claims()`` 只回每个 ``claim_id`` 的最新一版，而「这条说法退场了没有」
    正是按那一版判的：``withdraw_claim`` 撤回一条说法出的就是**最新那一版**
    （``application/claim_withdrawal.py`` 逐字：``revision + 1`` 且 ``retired=True``），
    所以撤了被引用的说法之后，``approved_claims()`` 里那条 id 的最新版带着
    ``retired=True``。

    ## 这一支为什么读 ``retired`` 而**不是**「最新版不活」

    这两件事在盘上曾经不可判别（撤回追加的那一版与一次普通的再批准都是「最新版不活」），
    而它们要求相反的读法 —— ``domain/outreach.py::ApprovedClaim`` 的类 docstring
    「退场与过期是两个事件，这里各有各的位」逐字记着这一次裁决：

    * **最新版续过期**（没有撤回）：被引那一版旧于最新版，而**那一版自己的**期限在端口上
      读不出来 —— 那一格是 ``_offer_missing._scope_gaps`` 的 ``not_reported``
      （``_cited_revision_is_unreadable``），**不许拿最新版的期限顶替**：那样会在最新
      一版续过期的那天把一条**有效**的引用报成过期，而那是最坏的一类错（报告上读起来
      像「资料有问题」，实际是这一层拿错了对象）。
      ``tests/test_product_readiness.py::test_a_superseded_citation_is_unreported_and_the_latest_is_not_substituted``
      就是按这一条钉的，它的夹具刻意做成「第 1 版还没过期、第 2 版（最新版）已经过期」。
    * **撤回了**：整条说法退场。

    所以这一支只认 ``claim.retired``。此前它读的是 ``not claim.is_live_at(at)``，那一版
    会把上面第一种情形也判成退场，与 ``_cited_revision_is_unreadable`` 那条**逐字相反**
    —— 两条批准文本因此各有一半变成假话。

    ## 这一支为什么只在**被引那一版不是最新版**时才报

    两个条件都要（``ref.revision != claim.revision and claim.retired``）：

    * 被引那一版**就是**盘上最新版、而它在**期限上**已经不活时，``_offer_missing._scope_gaps``
      **已经**把这一格报进 ``missing`` 了（那一条 ``why`` 逐字写着「要一条新的已批准
      版本，而不是把过了期的那一条继续用出去」）。这里再报一条会让同一件事出现在两张
      清单上 —— ``_scope_gaps`` 自己的注释逐字写着那会「让读者以为要修两处」；而且
      ``_rollup`` 的 invalid 优先会把外层那一行从 ``missing`` 搬到 ``invalid``，那是
      ``tests/test_launch_preview.py::test_the_same_board_read_at_two_moments_gives_two_answers``
      钉住的处置（它用一条到 ``LATER`` 过期的说法造「盘面一个字没改、答案跟着换」）。

      **这一格今天没有生产者**：``withdraw_claim`` 撤回一条说法必然追加一版，所以
      「被引那一版就是最新版」与「那一版 ``retired``」不可能由生产路径同时产生。它能被
      手工构造出来（直接写一版 ``retired=True`` 并引用它），那时这一支会漏、那一条引用
      落进 ``missing``（处置说「补一份」，而正确的处置是「退场 ⇒ 去改引用」）—— 如实记
      在这里：它是**不可达**，不是被处理过。
    * 被引那一版**旧于**最新版时，``_scope_gaps`` 按它那条「读不出来、不拿最新版顶替」
      的判据**不报**（见 ``_cited_revision_is_unreadable``）—— 于是「这条引用还有没有
      一个活着的出处」在那条路上**没有任何一格**答得出来。**撤回留下的形状正是这一
      种**：``withdraw_claim`` 追加一版，被引的那一版因此一定旧于最新版。这一支补的
      就是这一格；它不替换 ``not_reported`` 那一条，两条说的是两件事（见上一节）。

    ## 为什么它进 ``invalid`` 而**不是** ``missing`` / ``awaiting_approval``

    三种处置相反：``missing`` 是「给这一版补一格」，``awaiting_approval`` 是「在这一
    版上找一个人签字」，而这里要动的是**这一条引用** —— 指着一条已经退场的说法启动
    这件事本身不成立，换一条还在的说法（或重新批准一条）之前，补别的都白补。这与
    ``_rollup`` 把「内层那版已经 ``retired``」放进外层 ``invalid`` 是同一条理由。
    """
    subject = SubjectKind.OFFER.subject(stored.offer_id, stored.version)
    for ref in stored.product_refs:
        cited = catalog.get_version(ref.product_id, ref.version)
        if cited is None:
            yield ReadinessGap(
                subject=SubjectKind.PRODUCT.subject(ref.product_id, ref.version),
                field="product_refs",
                why=(
                    "这一版方案点名的这一版产品资料不在盘上。"
                    "**先修引用关系**：补别的格子都白补 —— 一份指着不存在的东西的方案，"
                    "它自称引用了什么本身就不可信。"
                ),
            )
        elif stored.status is ProductStatus.READY and cited.status is not ProductStatus.READY:
            yield ReadinessGap(
                subject=SubjectKind.PRODUCT.subject(ref.product_id, ref.version),
                field="product_refs",
                why=(
                    f"这一版方案已经是 ``ready``，而它点名的产品资料还是 "
                    f"``{cited.status.value}``。一份核对过的方案引用一份没核对过的资料，"
                    "读的人会以为两层都核对过 —— 而且这个误读不会在别处被挡住："
                    "域层的 ``_ready_means_approved`` 只看方案自己那一版。"
                ),
            )
    for ref in stored.approved_claim_refs:
        claim = latest.get(ref.claim_id)
        if claim is None:
            yield ReadinessGap(
                subject=claim_subject(ref.claim_id, ref.revision),
                field="approved_claim_refs",
                why=(
                    "这一版方案引用的这条说法不在盘上（没有任何一版）。"
                    "先修引用关系：要么把那条说法登记进去，要么把这一条引用去掉。"
                ),
            )
        elif ref.revision > claim.revision:
            yield ReadinessGap(
                subject=claim_subject(ref.claim_id, ref.revision),
                field="approved_claim_refs",
                why=(
                    f"这一版方案引的是第 {ref.revision} 版，而盘上这条说法最新只到第 "
                    f"{claim.revision} 版 —— **引用比盘上的最新版还新**，版号是编的。"
                    "（「有人删过版本」不是解释：两个实现都逐字写着「只挑最新，"
                    "不删旧的」。）"
                ),
            )
        elif ref.revision != claim.revision and claim.retired:
            # 退场那一版的 ``expires_at`` 一定不是 ``None``：``withdraw_claim`` 写它时
            # 逐字把 ``expires_at`` 落在撤回那一刻。两句因此都写得出来 —— 与
            # ``is_live_at`` 那条路不同，这里不必再挡一次 ``None``。
            expires = (
                claim.expires_at.isoformat() if claim.expires_at is not None else "（未写）"
            )
            yield ReadinessGap(
                subject=claim_subject(ref.claim_id, ref.revision),
                field="approved_claim_refs",
                why=(
                    f"这一版方案引的这条说法**已经退场了**：盘上 ``{ref.claim_id}`` 的"
                    f"最新一版是第 {claim.revision} 版，它是一条退场版（``retired``，由 "
                    f"``withdraw_claim`` 写出来，``expires_at`` 落在 {expires}）；"
                    f"这次读的时刻是 {to_utc(at).isoformat()}。\n"
                    "  ``approved_claims()`` 只回每个 ``claim_id`` 的最新一版，而"
                    "「退场」挂在那**最新那一版**上 —— 所以这一格判的是盘上最新那一版，"
                    "不是被引的那一版。\n"
                    "  它进 ``invalid``：指着一条已经退场的说法启动这件事本身不成立。"
                    "要动的是**这一条引用**（换一条还在的说法，或者重新批准一条），"
                    "不是给这一版补一格（``missing``），也不是在这一版上找一个人签字"
                    "（``awaiting_approval``）—— 三种处置相反。"
                ),
            )
    if stored.supersedes is None:
        return
    if catalog.get_offer(stored.offer_id, stored.supersedes) is not None:
        return
    yield ReadinessGap(
        subject=subject,
        field="supersedes",
        why=(
            f"这一版写着它取代第 {stored.supersedes} 版，而那一版不在盘上。"
            "与产品资料那条同款：取代是指向盘上另一行的引用，指不到时这一版的历史"
            "读不出来。"
        ),
    )


def _offer_not_reported(
    stored: OfferVersion, latest: dict[str, ApprovedClaim], *, at: datetime
) -> tuple[NotReported, ...]:
    """固定那一半 + 这一版引出来的那一半（见模块 docstring「减法」那一节）。"""
    cited = tuple(
        _cited_revision_is_unreadable(
            claim_id=ref.claim_id,
            revision=ref.revision,
            latest=claim.revision,
            at=at,
        )
        for ref in stored.approved_claim_refs
        if (claim := latest.get(ref.claim_id)) is not None
        and ref.revision < claim.revision
    )
    return (*NOT_REPORTED, *cited)


__all__ = [
    "NOT_REPORTED",
    "ProductReadiness",
    "ReadinessGap",
    "SubjectKind",
    "claim_subject",
    "gap_as_dict",
    "offer_version_readiness",
    "product_version_readiness",
    "unknown_version_refusal",
]

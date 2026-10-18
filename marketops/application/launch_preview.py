"""启动预览 —— 一次**尚未发生**的启动，在它之前的那份读数（切片 b01-d）。

## 权威句

B01 第五条关闭条件逐字八个字（``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md:104`` 第 5 项）：

    启动授权前零外部调用

而这条关闭条件**没有自己的正文**（八条关闭条件里只有这一条是八个字），它的展开在
``:102`` 那条启动用例里逐字：

    启动用例先preview：固定研究目标/GeoScope/Scenario、可选Product/Offer、policy、
    预算/周期与下一步动作；有批准才持久受理Scene/Workstream及Job。

以及 ``docs/PRODUCT_LINE_AND_SCENE_MODEL.md:122``（§4.2 第 3 步）逐字要预览的东西：

    预览来源入口、准入/排除、角色策略、采集访问边界、资源需求、Cohort 计划、
    成本可观测范围和待批准缺项。

末一句「待批准缺项」与 ``:229`` 逐字同一件事：「启动前返回结构化
missing/invalid/awaiting_approval，**不让模型读一段 traceback 猜**」。**b01-c 是那句
话的产品/方案面**（``application/product_readiness.py`` 交的是「一版产品资料 / 一版
方案还缺什么」），**这一层是它的启动面**：同一个三张清单的形状，主语换成「一次启动」。

## 这一层读的是什么：**盘上那一版已经写下的声明**

切片行的六个格子（研究目标 / GeoScope / Scenario / 可选 Product/Offer / policy / 预算与
周期）在盘上各有一个落点，而且只有一个：

==================================  =============================================
切片行那一格                        它落在哪
==================================  =============================================
研究目标 / Scenario                 ``SceneVersion.scenario``（这一层逐字回显）
GeoScope                            ``SceneVersion.region`` —— **今天不是那个类型**，
                                    见下面「GeoScope 的错位」那一节
可选 Product / Offer                这一层的两个入参（``product_id`` /
                                    ``product_version`` 与 ``offer_id`` /
                                    ``offer_version``）
policy                              ``role_policy`` / ``exclusions``（profile 的
                                    排除清单在立项那一刻并进了后者）
预算与周期                          ``budget`` / ``refresh``
下一步动作                          :attr:`LaunchPreview.next_action`
待批缺项                            ``missing`` / ``invalid`` /
                                    ``awaiting_approval`` 三张清单
==================================  =============================================

**这一层不重建声明。** 一声明的逐格合法性只有一处判据 —— ``domain/scene.py`` 的
``SceneVersion`` 校验器，由 ``product_loop._build_version`` 在**写下去的那一刻**执行。
在这里重判会造出第二处定义，而两处各写一遍的那天，一份**盘上是合法**的声明会在一份
预览里被报成不合法（或反过来），报告上两边各自都正常。所以这一层读的是**已经落盘的
那一版**：它能被读出来，就说明它已经过了那一处判据。

这也正是「有批准才持久受理 Scene/Workstream 及 Job」（``:102``）的次序：``create_scene``
把声明**持久**成一版 ``draft``（持久，但没有受理），这一层读那一版并说出「要启动它
还缺什么」，**受理**（让它生效、然后才谈 Job）是下一步 —— 设计表把它排在紧挨着的
b01-e（「批准与持久受理」）。

### GeoScope 的错位（一处必须在设计时处置的张力）

切片行点名 ``GeoScope``，而 ``GeoScope`` 这个**类型**在设计表里排在 **b02-a**
（``docs/B01_B02_DESIGN_SLICES_2026-09-28.md``）。今天盘上没有这个类型：``SceneVersion
.region`` 是 ``tuple[str, ...]``，域层对它的要求只有「非空、不含空串、无重复」。这一层
**只收今天声明的那个形态**（一串自由文本），并把这条错位登记进 ``not_reported`` 里那条
「地区与市场的相容性」—— 它是这一层**答不出来**的一件事，而理由不是这一层懒：见那一条。

## 零外部调用是**构造**，不是自觉

与 §B17-g 的 ``application/run_preview.py`` 同款（那一层的理由写在它自己的模块
docstring 里）：这一层**必须**读三本本地账才答得出「这一版引的东西还缺什么」，所以
构造换成「签名只收本地存储与已经读出来的值 + 不读时钟（``at`` 由外面给，与
``admission_refusal`` 同款）+ 类型注解里没有检索/抓取端口的类名」。判据是一把 AST 量具
（``tests/test_launch_preview_is_offline.py``），量具本身照 ``tests/test_run_preview.py``
那一族。

**三本账都是本地的**：产品目录（``ports.product_catalog``）、已批准说法
（``ports.outreach``）、以及由调用方读出来的那一版 Scene 与它的记录。这一层**拿不到**
检索与抓取那两个端口 —— 不是「不调用」，是签名里没有能收下它的格子。

反过来说：一次启动**当然**要花外部调用（那是它存在的理由）。这一层的全部内容是
「**授权之前**那次调用**还没发生**」，所以它读的是盘上已经有的东西，一个字节都不新写。

## 这一层判什么、不判什么

判（三条，都由**盘上两条记录之间**的关系得出，不含任何市场知识）：

* **被引的那一版自己就绪了吗** —— 原样调用 b01-c 的两个读法，读数**逐字嵌在**
  ``cited`` 里（不折平、不改写），并在外层出一行**同类**的缺口（见 :func:`_rollup`）。
* **两条引用彼此对得上吗** —— 点名的那一版产品在不在被引那一版方案的
  ``product_refs`` 里。不在 ⇒ ``invalid``：方案里「卖什么」与启动时点名的产品一旦分家，
  跑起来那一批资料按哪一边算，报告上读不出来。
* **那一版声明自己等人批准吗** —— ``outreach.uncalibrated`` 非空（那几个数是缺省，不是
  有人标定过的）。

不判：

* **不判某个地区在不在某个市场里**：``SceneVersion.region`` 是自由文本而
  ``OfferVersion.markets`` 是国家码，判它要一份地理知识，而本仓明令 ``marketops/`` 里
  不得出现市场词。它进 ``not_reported``，理由逐字在那一条里。
* **不判 `SceneVersion.product` 这个名字指的是哪个产品**：那一格是``str``（域层只要求
  非空），而 ``ProductVersion.product_id`` 过 ``check_canonical_id``。拿一个自由文本去比
  一个规范 id，会在「声明里写的是展示名」的那一天造出一条假缺口 —— 而假缺口比没有缺口
  更坏（它让人去改一个本来对的东西）。所以要引哪一版由**入参**点名，不由这一层猜。
* **不判成本、不判资源、不判 Cohort**：那三项要一份**编好的计划**与一份**池子**
  （§B16 的 ``plan_preview`` 与 §B17-g 的 ``run_preview`` 报的是它们）。它们逐条进
  ``not_reported``，每条点名谁答得上。

## 三张清单**不互斥**，而且各有各的下一步

与 b01-c 逐字同一条：``missing`` ⇒ 去补 / 去改，``invalid`` ⇒ 先修引用关系（指着不存在的
东西，补多少资料都白补），``awaiting_approval`` ⇒ 人等签字。一份又缺东西又没人签的声明
两张都报。

:attr:`LaunchPreview.next_action` 是一个**属性**而不是一格：由构造者填的那一格会在某次
改动之后与三张清单分家，而报告上它必须说的是同一件事（``RunPreview.outcome`` 对同一件
事论证过一次）。它的**五档**见那个属性的 docstring —— 四档由三张清单决定，第五档由
**记录的状态**决定，两者的分界写在那一处。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ..domain.product import OfferVersion
from ..domain.scene import SceneRecord, SceneState, SceneVersion
from ..ports.outreach import OutreachStore
from ..ports.product_catalog import ProductCatalog
from .not_reported import NotReported
from .product_readiness import (
    ProductReadiness,
    ReadinessGap,
    SubjectKind,
    gap_as_dict,
    offer_version_readiness,
    product_version_readiness,
)
from .scene_receipt import version_receipt


def launch_subject(scene_id: str, version: int) -> str:
    """这一份读数的主语写法：``launch:uk-acme-natural-grass@2``。

    与 ``SubjectKind.subject`` / ``claim_subject`` 同一形状、同一理由：**格式只有
    这一处定义**，:meth:`LaunchPreview.__post_init__` 拿它校验主语与那两格不许各写
    一遍（两处各写一遍的那天，报告上的主语与它自称的那一版会分家）。

    ``launch:`` 这一族**不是** ``SubjectKind`` 的第三个成员：那个闭集是「盘上的一条
    记录」（产品版本、方案版本），而这一份读数的主语是**一次尚未发生的启动** —— 它
    在盘上没有对应的行，这也正是它读得出来的原因（没有行可写）。
    """
    return f"launch:{scene_id}@{version}"


#: §4.2 第 3 步那八项里，这一层**答得上**的三项。三项之外的那五项逐条列在
#: :data:`LAUNCH_NOT_REPORTED` 里 —— 两处合起来是那八项的一份**划分**
#: （``tests/test_launch_preview.py`` 有一条判据把这件事钉住：并集逐字等于那八项，
#: 交集为空）。分成两处而不是写一张八行的表，是因为答得上的三项在这一层的**别处**
#: 已经有了落点（``declared`` 回声与三张清单），在这里再抄一遍就是第二处定义。
_ANSWERED_ITEMS: tuple[str, ...] = (
    "准入/排除",
    "角色策略",
    "待批准缺项",
)

#: §4.2 第 3 步那八项里这一层**答不上来**的那几项，每条点名**谁答得上**。
#:
#: 与 b01-c 的 ``NOT_REPORTED`` 同一个形状、同一条理由（那个常量的 docstring 逐字：
#: ``reason`` 不是一句「暂不支持」，而是「要点名谁答得上或要动哪一格」）。**这一份
#: 与那一份不是一个对象**：那一份说的是「这一版资料自己的出处还在不在」，
#: 这一份说的是「启动要哪些东西才报得出来」。
LAUNCH_NOT_REPORTED: tuple[NotReported, ...] = (
    NotReported(
        item="来源入口",
        reason=(
            "§4.2 第 3 步点名要预览「来源入口」。这一层答得出的只是**这一版声明里写了"
            "哪些来源计划**（``declared['source_plan']``，逐条是名字），答不出的是那些"
            "名字**解析到哪些入口** —— 那个解析住在池子那一侧（``import_motherpool`` 与"
            "母池台账），要一次**已经落盘的导入**才说得出来。"
        ),
    ),
    NotReported(
        item="采集访问边界",
        reason=(
            "这一层答不上来：这一条要把一份 profile 的**合规声明**落到**一次具体采集**"
            "上（抓什么、以什么姿态、哪一档速率）。合规边界本身写在 profile 的 "
            "``compliance`` 块与 ``docs/ARCHITECTURE.md`` 的「不提供」宣言里，而把声明"
            "落成一次动作，是**受理之后**那一侧的读数（``admit_run`` 与它下游）。"
        ),
    ),
    NotReported(
        item="资源需求",
        reason=(
            "这一层答不上来：报得出「这一轮要花多少」的是**一份编好的计划**"
            "（§B16 的 ``plan_preview`` 报的是同一件事的读数）。编计划要一份 "
            "``ResearchContext``，而那是受理之后才有的东西 —— 这一层读的是一版还"
            "没被受理的声明。"
        ),
    ),
    NotReported(
        item="Cohort 计划",
        reason=(
            "这一层答不上来：Cohort 计划读的是**账号池与配额**（"
            "``application/cohorts.py`` 的 ``freeze_from_pool`` 与 ``CohortPlan``）。"
            "这一版声明里没有池子 —— 池子是导入/清洗那一侧落盘的，与「这一版声明要"
            "经营什么」是两件事。"
        ),
    ),
    NotReported(
        item="成本可观测范围",
        reason=(
            "这一层答不上来：成本要一份**预算账本**才报得出（``application/budget.py`` "
            "的 ``RunBudget`` 读的是这条链上已经花掉与剩下的）。这一版还没有受理过任何"
            "一轮，账本上没有它的行 —— 而一个「已花 0」的读数与「这一层没读账本」在"
            "报告上同形，所以这一层不报它，明说答不上来。"
        ),
    ),
)

#: 这一层**没有**引方案时那一条：它答不出「本来就不需要」与「该引而忘了引」的区别。
_NO_OFFER_CITED = NotReported(
    item="这一次要不要引一个已获批方案",
    reason=(
        "切片行把 Product/Offer 写成**可选**（b01-d 那一行逐字「**可选** "
        "Product/Offer」），而可选不等于不需要：一次纯市场研究本来就不引方案"
        "（那是 b01-f 那条关闭条件），一次投放则要引。这一层没有能力分辨「本来就不"
        "需要」与「该引而忘了引」—— 那是一个**业务判断**，由人（或批准那一步）来说。"
        "这里只如实报：这一次的读数里**没有**方案那一组判定。"
    ),
)


class LaunchNextAction(str, Enum):
    """这一步**该做什么**。五档，闭集。

    ``str`` 混入是为了报告：``as_dict()`` 里写出来的是 ``"fix_citations"``，不是一个
    读的人要去别处查表的数字。与 ``ProductReadiness.usable`` 的四档同一形状、同一
    理由（读的人要看得出该做什么，而不是只拿到一个「不行」）。
    """

    #: 三张清单里有 ``invalid``：**先修引用关系**。指着不存在的东西时补别的都白补。
    FIX_CITATIONS = "fix_citations"
    #: 三张清单里有 ``missing``：去补 / 去改那一格。
    SUPPLY_MISSING = "supply_missing"
    #: 三张清单里有 ``awaiting_approval``：等人签字（或标定）。
    AWAIT_APPROVAL = "await_approval"
    #: 三张清单都空，但记录层面还不让派发（不是这一版、或不是 ``active``）：去受理。
    REQUEST_ACTIVATION = "request_activation"
    #: 三张清单都空，且这一版就是生效版、记录也让派发：下一步是**生成一份 Run 预览**。
    PREVIEW_RUN = "preview_run"


@dataclass(frozen=True)
class LaunchPreview:
    """一次启动在这一刻的读数。

    ``frozen`` 且只由 :func:`launch_preview` 构造（照 ``plan_preview.PlanPreview`` 与
    ``product_readiness.ProductReadiness``）。``__post_init__`` 挡住三条「两个字段各
    写一遍就会有一个自洽的假答案」的组合；三条都**只有直接构造才能违反**，而
    ``tests/test_launch_preview.py`` 逐个构造一遍（一个没人能违反的守卫与一个从不响的
    守卫，在报告上长得一样）。
    """

    #: 主语，形如 ``launch:uk-acme-natural-grass@2``，由 :func:`launch_subject`
    #: 生成。
    subject: str
    #: 被读的那一版 Scene。**它有工作区**（``SceneVersion.workspace``），而这一层的
    #: 入参 ``workspace`` 说的是「本部署服务的是哪一个」—— 两者不相等时这一层不判
    #: （那是调用方的事，见 :func:`launch_preview` 的 docstring）。
    declared: SceneVersion
    #: 这一版所在的**当前状态**（``SceneRecord``）。与 ``declared`` 分开：一版声明落盘
    #: 之后可以被激活、暂停、归档，而**声明本身一个字都不变**。
    record: SceneRecord
    #: 这份读数取在什么时候。三张清单里那几行的判定（被引说法过没过期、被引版本的
    #: 状态）都拿它判 —— **一次调用里只有一个时刻**（见 :func:`launch_preview`）。
    at: datetime
    #: 被引对象的查缺读数，**逐字嵌着**（b01-c 那一层的对象，不折平、不改写）。
    #:
    #: 交出去的是 ``ProductReadiness`` 本身，不是这一层自己的包装 —— 报告里那一格
    #: （``as_dict()``）逐字是它的 ``as_dict()``，而调用方拿到的对象与查缺面两条工具
    #: 交出去的是**同一个类型**。包一层会让「同一条盘上事实」在两个出口各有一个类。
    cited: tuple[ProductReadiness, ...] = ()
    #: 要补 / 要改的那些格。
    missing: tuple[ReadinessGap, ...] = ()
    #: 指不到它自称指着的东西（或指着不该引的东西）的那些引用。
    invalid: tuple[ReadinessGap, ...] = ()
    #: 只差一个人核对或标定。**与上面两张不互斥**：一份又缺东西又等人签的声明两张都报。
    awaiting_approval: tuple[ReadinessGap, ...] = ()
    #: §4.2 那张检查表上这一层**答不上来**的那几项。见 :data:`LAUNCH_NOT_REPORTED`。
    not_reported: tuple[NotReported, ...] = ()

    def __post_init__(self) -> None:
        if self.at.tzinfo is None:
            raise ValueError(
                "at 是一个 naive 时刻，而本仓的时刻一律带时区（``domain/util.py::"
                "to_utc``）。\n"
                "一个省掉时区的时刻会被按读它那台机器的本地时区解释，而这里拿它比的"
                "就是「被引用的东西到这一刻还作不作数」—— 于是「这一版能不能启动」"
                "取决于这份读数在哪里跑。"
            )
        if self.subject != launch_subject(self.declared.scene_id, self.declared.version):
            raise ValueError(
                f"主语 {self.subject!r} 与这一版 Scene（{self.declared.scene_id!r} 第 "
                f"{self.declared.version} 版）对不上 —— 主语要由 :func:`launch_subject` "
                "生成，形如 ``launch:<场景名>@2``。\n"
                "两格各写一遍的那天，报告里的主语会说它读的是另一版 —— 而「哪一版」"
                "恰恰是这份读数最要紧的一格（同一个整数在两次部署里可以是两份不同的"
                "规则）。"
            )
        if self.record.scene_id != self.declared.scene_id:
            raise ValueError(
                f"记录说的是 {self.record.scene_id!r}，而声明说的是 "
                f"{self.declared.scene_id!r}。\n"
                "两格读自同一次调用的同一个 Scene：对不上说明有一格来自别处，而"
                "「这一版在哪个记录底下」没有第二个答案。"
            )
        if self.awaiting_approval and self.record.state is SceneState.ARCHIVED:
            raise ValueError(
                f"记录的状态是 {self.record.state.value!r}，却带着 "
                f"{len(self.awaiting_approval)} 条「等批准」。\n"
                "归档是「这个市场不再经营」，不是「暂时不看」（§4）：一份已经归档的"
                "记录不会再有人去签它 —— 那条「等批准」会让人去找一个不存在的待办。"
                "（b01-c 那一层对 ``retired`` 有一条逐字同源的守卫。）"
            )

    @property
    def is_effective(self) -> bool:
        """这一版**是不是生效版**。

        与 ``record.latest_version`` 分开读（``SceneRecord`` 的 docstring 逐字把两者
        分成两格的理由：改版不生效是一件必须看得见的事）。这一格与「记录让不让派发」
        （``accepts_new_work``）是**两件事**，两件都成立才谈得上开跑：一版 ``active``
        记录底下可以躺着一个还没生效的新版本，一版已生效的版本也可以落在一个暂停了的
        记录里。
        """
        return self.record.effective_version == self.declared.version

    @property
    def next_action(self) -> LaunchNextAction:
        """这一步该做什么。**五档，优先级逐个写在下面**。

        三张清单先于记录状态，因为它们说的是**内容**；记录状态说的是**这一版还没
        被受理**。两者的分界不是分层好看：内容上的缺口是调用方（或他的上游）要去动
        的，记录状态是人去按一个动作（``control_scene``）—— 把它们塞进同一张表会让
        两种「下一步」在报告上同形。

        三张清单之间的优先级：``invalid`` ⇒ ``missing`` ⇒ ``awaiting_approval``。
        与 ``ProductReadiness.usable`` 逐字同一条理由 —— 指着不存在的东西时补多少都
        白补；而一份还缺东西的声明就算今天签了字也不能跑（所以「等签字」排在「去补」
        之后：它不是一个更近的终点）。
        """
        if self.invalid:
            return LaunchNextAction.FIX_CITATIONS
        if self.missing:
            return LaunchNextAction.SUPPLY_MISSING
        if self.awaiting_approval:
            return LaunchNextAction.AWAIT_APPROVAL
        if self.is_effective and self.record.accepts_new_work:
            return LaunchNextAction.PREVIEW_RUN
        return LaunchNextAction.REQUEST_ACTIVATION

    def as_dict(self) -> dict[str, Any]:
        """写进报告的形状 —— 也是 MCP 只读面交出去的那一份。

        ``at`` 一起带上（照 ``RunPreview.as_dict``）：这是一份**带时刻的读数**，而缺了
        它，报告里「被引用的东西到这一刻还作不作数」这个结论就没有可复核的时间锚点。

        ``declared`` 那一格就是 ``create_scene`` / ``add_scene_version`` 回执里那一格
        的**同一个函数**（``application/scene_receipt.py::version_receipt``）—— 少一格
        ``calibrated``、或者多一句措辞，都会让同一条盘上事实在回执里说一套、在预览里
        说另一套。
        """
        return {
            "subject": self.subject,
            "scene_id": self.declared.scene_id,
            "version": self.declared.version,
            "latest_version": self.record.latest_version,
            "effective_version": self.record.effective_version,
            "is_effective": self.is_effective,
            "state": self.record.state.value,
            "accepts_new_work": self.record.accepts_new_work,
            # 记录**自己**那句「为什么现在不能派发」。逐字带着，不在这里重写一遍：
            # 那句话由 domain 算（``SceneRecord.dispatch_refusal`` 的 docstring 逐字
            # 论证过「两处各写一份的后果不是文案不一致」）。
            "dispatch_refusal": self.record.dispatch_refusal,
            "at": self.at.isoformat(),
            "next_action": self.next_action.value,
            "declared": version_receipt(self.declared),
            "cited": [reading.as_dict() for reading in self.cited],
            "missing": [gap_as_dict(gap) for gap in self.missing],
            "invalid": [gap_as_dict(gap) for gap in self.invalid],
            "awaiting_approval": [gap_as_dict(gap) for gap in self.awaiting_approval],
            "not_reported": [
                {"item": item.item, "reason": item.reason} for item in self.not_reported
            ],
        }


#: 被引读数属于哪一族 ⇒ 外层三张清单上的格子名。
#:
#: 由 ``ProductReadiness.kind`` 推 —— 那是那一条记录**自己**说的，不是调用点各写一遍
#: 的。写成一张表而不是在调用点 ``if isinstance(...)``，是因为表格只有一处，而
#: ``isinstance`` 每加一处就是一处可以推反的地方。
_GAP_FIELD: dict[SubjectKind, str] = {
    SubjectKind.PRODUCT: "product_id",
    SubjectKind.OFFER: "offer_id",
}


@dataclass(frozen=True)
class _Cited:
    """一对引用解析到的那一条记录，以及查缺面给它的那份读数。

    ``stored`` 是**已经读出来**的那一条（``ProductVersion`` 或 ``OfferVersion``）——
    它在报告里不出门（出门的是 ``reading``），但 :func:`_cited_offer_not_reported`
    要读它的 ``markets`` 那一格。
    """

    ident: str
    version: int
    stored: Any
    reading: ProductReadiness


def _pair_or_gap(
    *,
    subject: str,
    what: str,
    ident: str | None,
    version: int | None,
    field_ident: str,
    field_version: str,
    neither: ReadinessGap | None,
) -> tuple[tuple[str, int] | None, ReadinessGap | None]:
    """一对 ``(名字, 版本)`` 要么解析成两格，要么给出一条「去补哪一格」。

    **四条分支，不是两条**：两格都给、两格都没给、只给名字、只给版本。后两条的
    ``field`` 都指向**没给的那一格**（``ReadinessGap`` 的契约：``field`` 是调用方
    照着它去改的那一格）—— 写成一条「引用不完整」的话会让读的人自己去猜该补哪一半。

    ``neither`` 由调用方给：产品那一对「两格都没给」是一条缺口（见
    :func:`launch_preview`），方案那一对**不是**（切片行逐字「**可选** Product/Offer」，
    而一次纯市场研究本来就不引方案）。两处的差别是一句业务判断，不由这里替它们猜，
    所以它从外面传进来。
    """
    if ident is not None and version is not None:
        return (ident, version), None
    if ident is None and version is None:
        return None, neither
    if ident is None:
        return None, ReadinessGap(
            subject=subject,
            field=field_ident,
            why=(
                f"这一次点名了{what}的**版本**（第 {version} 版）却没给名字。\n"
                "  「哪一版」不是一个可以脱离名字解释的东西 —— ``ProductRef`` 的 "
                "docstring 逐字：「只写 ``product_id`` 再去解释一次「哪一版」，是把"
                "那次解释留给每个调用点各做一遍」。"
            ),
        )
    return None, ReadinessGap(
        subject=subject,
        field=field_version,
        why=(
            f"这一次点名了{what} {ident!r} 却没给**版本**。\n"
            "  ``ProductRef`` 的 docstring 逐字：「只写 ``product_id`` 再去解释一次"
            "「哪一版」，是把那次解释留给每个调用点各做一遍 —— 而两次部署之间，"
            "「第 2 版」可以是两份不同的东西。」\n"
            "  这一条进「去补」那一张：要动的是**补上版本号**这一格，不是去改已经"
            "写下的东西。"
        ),
    )


def _rollup(
    *,
    subject: str,
    field: str,
    cited: _Cited,
    missing: list[ReadinessGap],
    invalid: list[ReadinessGap],
    awaiting: list[ReadinessGap],
) -> None:
    """被引的那一版自己没就绪时，在外层出一行**同类**的缺口。

    ## 为什么不把内层那几行折平上来

    ``ReadinessGap`` 的契约逐字：「``field`` 永远是**这一份读数的主语**上的一格」。
    内层那几行说的一版产品资料自己缺什么（``owner``、``source_refs``…）—— 那些格子
    在这一层的主语（一次启动）上**不存在**，照着它们改不了。所以内层读数**整份**嵌在
    ``cited`` 里（它有自己的主语），外层这一行只说一句：**下一步那一档与内层同类**。

    ## 四档，不是一档

    内层三张清单哪一张非空，外层就进哪一张 —— 因为「下一步做什么」是由内层决定的：

    * 内层 ``invalid``（它指着一个不在盘上的东西）⇒ 外层 ``invalid``：那时去补外层
      的东西是白补。
    * 内层 ``missing`` ⇒ 外层 ``missing``。
    * 内层只是在等人签（``awaiting_approval``）⇒ 外层 ``awaiting_approval``。
    * 三张都空却**不** ``usable`` ⇒ 那一版的 ``status`` 是 ``retired``：它进
      ``invalid``。撤下来的一版仍然读得回来（它是记录，不是删除），所以这一条**不是**
      「不在盘上」—— 它进 ``invalid`` 是因为**指着它启动**这件事本身不成立：要引的是
      它的后继版本。

    把四档并成一档（例如一律报「等批准」）的代价是：内层缺的是资料时，外层会让调用方
    去找一个人签字 —— 签了字也跑不起来，而报告上那句「下一步」是错的。
    """
    reading = cited.reading
    if reading is None:
        return
    if reading.usable:
        return
    where = "缺 {m} 条 / 坏 {n} 条 / 等签 {k} 条".format(
        m=len(reading.missing), n=len(reading.invalid), k=len(reading.awaiting_approval)
    )
    if reading.invalid:
        target, why_head = invalid, "内层指着一个不在盘上的东西（``invalid``）"
    elif reading.missing:
        target, why_head = missing, "内层有格子要补（``missing``）"
    elif reading.awaiting_approval:
        target, why_head = awaiting, "内层只差一个人核对（``awaiting_approval``）"
    else:
        target, why_head = invalid, (
            "内层的三张清单都是空的，而这一版仍然不 ``usable`` —— 那只可能是它的 "
            "``status`` 是 ``retired``：它已经被撤下来了"
        )
    target.append(
        ReadinessGap(
            subject=subject,
            field=field,
            why=(
                f"被引的那一版自己**还没就绪**：``{reading.subject}`` 的状态是 "
                f"``{reading.status.value}``，{where}。读数的原文逐字在 ``cited`` 里"
                "（它有自己的主语，这一层不折平、不改写）。\n"
                f"  这一行进**同类**的那一张：{why_head} —— 所以下一步那一档与内层相同。"
            ),
        )
    )


def _cited_offer_not_reported(stored: OfferVersion) -> NotReported:
    """引了一版方案时**额外**那一条答不上来的：地区与市场可不可比。

    这一条是这一层最要紧的一条 ``not_reported``，因为它挡住的是一次**假判定**：
    ``SceneVersion.region`` 是自由文本（域层只要求非空、无重复 —— 本仓自己的
    fixture 里写的是 ``("GB-England-Devon",)``），而 ``OfferVersion.markets`` 是**国家
    码**（``_markets_are_country_codes`` → ``util.check_market_country``，只收两位大写
    ISO-3166）。两者求交集要一份**地理知识**，而本仓明令 ``marketops/`` 里不得出现
    市场词（``tests/test_no_hardcoded_market_knowledge.py`` 是执行器）。

    所以它不进 ``invalid``：那会造出一条**假的**缺口（一个 ``("Devon",)`` 的场景配
    一版 ``("GB",)`` 的方案是**正常**的，而在字符串比较下它们是「无交集」）。
    """
    return NotReported(
        item="地区与市场的相容性",
        reason=(
            "这一版声明的 ``region`` 是**自由文本**（``SceneVersion.region`` 是 "
            f"``tuple[str, ...]``，本仓 fixture 里写的是 ``{('GB-England-Devon',)!r}`` "
            "那一类值），而这一版方案的 ``markets`` 是**国家码**（"
            "``util.check_market_country`` 只收两位大写 ISO-3166）。把两者求交集要一份"
            "**地理知识**，而本仓明令 ``marketops/`` 里不得出现市场词"
            "（``tests/test_no_hardcoded_market_knowledge.py``）。所以「这个地区在不在"
            "那一版方案的市场里」这一层**判不出来** —— 它由批准那一步的人判，或者等 "
            "``GeoScope`` 变成一个**类型**（设计表把它排在 b02-a）把那句地理知识搬进域。"
            "这一条宁可答不上来也不字符串比较：``Devon`` 与 ``GB`` 在字符串下是"
            "「无交集」，而那是一次错的判定。"
        ),
    )


def _product_gaps(
    *,
    subject: str,
    catalog: ProductCatalog,
    resolved: tuple[str, int],
    at: datetime,
) -> tuple[list[ReadinessGap], _Cited | None]:
    """产品那一对的 ``invalid``（``missing`` 恒为空），以及在盘时那一份被引读数。

    **一次读判在不在，一次读算查缺**：``catalog.get_version`` 读回来的那一行判「在不在
    盘上」（不在 ⇒ 一条 ``invalid``、没有 ``cited``）；查缺读数由
    ``product_version_readiness`` 自己按**端口**再读一次 —— 不改成「把已经读出来的行
    交进去」的形状，理由与 b01-c 那一层逐字相同：那样这一层能塞一份与盘上不同的记录
    进去，而报告上的主语仍然写着盘上那个 id。

    两次读**同一次调用里**发生，所以「判在不在」与「算查缺」看到的是同一份存储
    （``ProductCatalog`` 的读法不带时刻，它没有「读到的是哪一刻那一版」这一说）。
    """
    ident, version = resolved
    stored = catalog.get_version(ident, version)
    if stored is None:
        return [
            ReadinessGap(
                subject=subject,
                field="product_id",
                why=(
                    f"产品资料 {ident!r} 的第 {version} 版**不在盘上**。查缺面读的是"
                    "**已经登记的那一份**。\n"
                    "  指着不存在的东西时，补别的都白补（b01-c 那一层对同一件事的判词"
                    "逐字如此）：先把这一版登记进去（``put_product_version``），或者"
                    "核对名字 / 版本号写错没有。\n"
                    "  这一条读的是**一次尚未发生的启动**：它引的东西未必跟着一起落了盘。"
                ),
            )
        ], None
    reading = product_version_readiness(catalog, product_id=ident, version=version, at=at)
    assert reading is not None, "上面那一读已经判过它在盘上，这里读不到就是端口不自洽"
    return [], _Cited(ident=ident, version=version, stored=stored, reading=reading)


def _offer_gaps(
    *,
    subject: str,
    workspace: str,
    catalog: ProductCatalog,
    claims: OutreachStore,
    resolved: tuple[str, int],
    product: tuple[str, int] | None,
    at: datetime,
) -> tuple[list[ReadinessGap], _Cited | None]:
    """方案那一对的 ``invalid``（``missing`` 恒为空），以及同工作区时那一份被引读数。

    **两条真判据里这一条**：点名的那一版产品在不在被引那一版方案的 ``product_refs``
    里。不在 ⇒ ``invalid`` —— 方案里「卖什么」与启动时点名的产品一旦分家，跑起来那一批
    资料按哪一边算，报告上读不出来。

    ``at`` 交给 ``offer_version_readiness``：那一层要判「被引的说法过没过期」，而那要
    一个时刻。**它由这里往下传，不由那一层自己取** —— 一次读数里只有一个时刻。
    """
    ident, version = resolved
    stored = catalog.get_offer(ident, version)
    if stored is None:
        return [
            ReadinessGap(
                subject=subject,
                field="offer_id",
                why=(
                    f"方案 {ident!r} 的第 {version} 版**不在盘上**。\n"
                    "  先把它登记进去（``put_offer_version``），或者核对名字 / 版本号"
                    "写错没有 —— 指着不存在的东西时，补别的都白补。"
                ),
            )
        ], None
    if stored.workspace != workspace:
        return [
            ReadinessGap(
                subject=subject,
                field="offer_id",
                why=(
                    f"方案 {ident!r} 的第 {version} 版写着 ``workspace="
                    f"{stored.workspace!r}``，而这一次启动在工作区 {workspace!r}。\n"
                    "  这一条**不是**「不在盘上」—— 它在盘上，是**别人的那一版**。"
                    "``runs.py::_one_run`` 与查缺面两个处理器对同一件事有一条共用的"
                    "判词：跨工作区的读不留痕，它在这一侧看起来只是一次成功的查询，而"
                    "被读到的那一份此后会被当成本地事实用下去。\n"
                    "  所以这一条读数**不把它嵌进 ``cited``**：嵌进去就等于把它当本地"
                    "那一版读了。（查缺面那两条工具在同一种情形上**拒绝**整次调用 —— "
                    "那是因为**它们的主语就是那一版**；这里的主语是一次启动，那一版只是"
                    "它引的一个东西。）"
                ),
            )
        ], None
    invalid: list[ReadinessGap] = []
    if product is not None and product not in {
        (ref.product_id, ref.version) for ref in stored.product_refs
    }:
        listed = sorted((ref.product_id, ref.version) for ref in stored.product_refs)
        invalid.append(
            ReadinessGap(
                subject=subject,
                field="product_id",
                why=(
                    f"这一次同时点名了产品 {product[0]!r} 第 {product[1]} 版与方案 "
                    f"{ident!r} 第 {version} 版，而**那一版方案自己不引这一版产品**"
                    f"（它的 ``product_refs`` 逐条是 {listed}）。\n"
                    "  两条路必有一条要改：引那一版方案本来引的产品，或者换一版引得到"
                    "这个产品的方案。\n"
                    "  这一条必须在启动**之前**说出来：方案里「卖什么」与启动时点名的"
                    "产品一旦分家，跑起来的那一批资料是按哪一边算的，报告上读不出来。"
                ),
            )
        )
    reading = offer_version_readiness(
        catalog, offer_id=ident, version=version, claims=claims, at=at
    )
    assert reading is not None, "上面那一读已经判过它在盘上，这里读不到就是端口不自洽"
    return invalid, _Cited(ident=ident, version=version, stored=stored, reading=reading)


def _outreach_gaps(*, subject: str, declared: SceneVersion) -> Iterator[ReadinessGap]:
    """触达节奏里那几格**未标定**的缺省。

    ``OutreachPolicyDecl.uncalibrated`` 逐条列着是哪几格，这一层只说一句：它们是缺省，
    而 §6 要求「有人批准过的数」与「随手写的缺省」必须分得开（
    ``application/scene_receipt.py`` 把 ``calibrated`` 与 ``after_days`` 一起报出来就是
    为这件事）。
    """
    uncalibrated = declared.outreach.uncalibrated
    if not uncalibrated:
        return
    yield ReadinessGap(
        subject=subject,
        field="outreach",
        why=(
            f"这一版的触达节奏里还有**未标定**的格子：{sorted(uncalibrated)}。\n"
            "  它们是**缺省值**，不是有人批准过的数。它进「等批准」而不是「去补」：要"
            "动的不是调用方自己填一格（那一格填多少、按什么标定，是一次业务决定），是"
            "一位负责人把它标定下来。"
        ),
    )


def launch_preview(
    *,
    scene: SceneVersion,
    record: SceneRecord,
    workspace: str,
    at: datetime,
    catalog: ProductCatalog,
    claims: OutreachStore,
    product_id: str | None = None,
    product_version: int | None = None,
    offer_id: str | None = None,
    offer_version: int | None = None,
) -> LaunchPreview:
    """一版 Scene 的启动预览：它如果现在被启动，还缺什么、等谁、下一步是什么。

    ## 为什么主语是**盘上那一版**，以及「读的是哪一版」怎么看得见

    调用方点名要读哪一版（默认是记录里的**最新版** —— 那是「刚写完、还没受理」的那一
    版）。一次**没有**点名的调用会把最新版读上来，而 ``as_dict()`` 把 ``version`` /
    ``latest_version`` / ``effective_version`` / ``is_effective`` **四格一起**报出来，
    所以「它读的是不是最新版」在报告上是一个读数，不是一个假设。

    这是 b01-c 那一片的 M4 教训的直接后果（拿最新版顶替**被引**的那一版 ⇒ 一条判据专门
    钉住它）：**被引**的那一版不许替换，而**主语**那一版恰恰是调用方在点的 —— 两件事
    形状相同、处置相反，所以这里把三格都写进报告，让读的人自己看。

    ## ``at`` 是参数，不是时钟

    见 ``tests/test_launch_preview_is_offline.py`` 里那条判据的理由。一次读数里那几行
    判定（被引说法过没过期、被引版本的状态）都要用**同一个**时刻，而这一层自己不许取
    「现在」。

    ## ``workspace`` 是「本部署服务的是哪一个」，不是从 ``scene`` 上读的

    从 ``scene.workspace`` 上读会让这条守卫**恒真**（那一格就是被读的那一版自己写着
    的），于是它拦不住任何东西 —— 所以它由调用方给（MCP 那一层给 ``settings.profile``，
    与 ``product_readiness.py`` 的 ``_offer_readiness`` 逐字同一个来源）。``scene`` 自己
    的工作区由**调用方**在更早一步核（``require_scene`` 的名字判定 + 记录那一行的守卫）：
    那一条属于「**主语**不在盘上 / 是别人的」那一族，处置是**拒绝整次读数**，不是出一行
    缺口（b01-c 对同一件事的处置逐字如此）。

    ## 三张清单从哪来（一个都不在这里猜）

    * ``missing``：没点名的产品版本（声明里那一格是**裸名字**，见下）、只给了一半的
      引用、以及被引对象自己缺东西时那一行**同类**的转达。
    * ``invalid``：引了盘上没有的东西、引了别人工作区的方案、方案与产品对不上、
      被引的那一版已经是 ``retired``。
    * ``awaiting_approval``：``outreach.uncalibrated`` 非空、被引对象只是在等人签。

    **``product`` 那一格为什么进 ``missing``**：``SceneVersion.product`` 是 ``str``，
    域层对它的要求只有「非空」。而启动要的是**一个获批产品版本**（B01 ``:104`` 逐字
    「一个获批产品版本可被两个 Scene 共用」）—— 共享的是版本，不是名字。所以「这一次
    没有点名产品版本」是一条**真的缺口**，不是一次形式上的挑剔：它缺席时「那一版批过
    没有、资料齐不齐」这一层一个字都答不出来。切片行把这个入参写成**可选**，说的是
    「对一次只想看看声明长什么样的预览可以省」，不是「它可以一直空着」。
    """
    subject = launch_subject(scene.scene_id, scene.version)
    missing: list[ReadinessGap] = []
    invalid: list[ReadinessGap] = []
    awaiting: list[ReadinessGap] = list(_outreach_gaps(subject=subject, declared=scene))
    cited: list[_Cited] = []

    product, product_gap = _pair_or_gap(
        subject=subject,
        what="产品",
        ident=product_id,
        version=product_version,
        field_ident="product_id",
        field_version="product_version",
        neither=ReadinessGap(
            subject=subject,
            field="product_id",
            why=(
                "这一次预览没有点名要引的**产品版本**，而这一版声明里的 ``product`` 是"
                "一个**裸名字**：``SceneVersion.product`` 是 ``str``，域层对它的要求只有"
                "「非空」（``_non_empty_scalar``）。\n"
                "  启动要的不是一个名字，是**一个获批产品版本** —— B01 ``:104`` 逐字"
                "「一个获批产品版本可被两个 Scene 共用」，共享的是**版本**，不是名字。\n"
                "  把 ``product_id`` 与 ``product_version`` 一起点出来，这一条才答得出"
                "「它批过没有、资料齐不齐」。切片行把这个入参写成**可选**：对一次「先"
                "看看这一版声明长什么样」的预览它可以省，对一次**要启动**的预览它少"
                "不了 —— 同一个调用的两种用途，所以它可选，而**不是**可以一直空着。"
            ),
        ),
    )
    if product_gap is not None:
        missing.append(product_gap)

    offer, offer_gap = _pair_or_gap(
        subject=subject,
        what="方案",
        ident=offer_id,
        version=offer_version,
        field_ident="offer_id",
        field_version="offer_version",
        neither=None,
    )
    if offer_gap is not None:
        missing.append(offer_gap)

    if product is not None:
        invalid_here, product_cited = _product_gaps(
            subject=subject, catalog=catalog, resolved=product, at=at
        )
        invalid.extend(invalid_here)
        if product_cited is not None:
            cited.append(product_cited)
    if offer is not None:
        invalid_here, offer_cited = _offer_gaps(
            subject=subject,
            workspace=workspace,
            catalog=catalog,
            claims=claims,
            resolved=offer,
            product=product,
            at=at,
        )
        invalid.extend(invalid_here)
        if offer_cited is not None:
            cited.append(offer_cited)

    for item in cited:
        _rollup(
            subject=subject,
            field=_GAP_FIELD[item.reading.kind],
            cited=item,
            missing=missing,
            invalid=invalid,
            awaiting=awaiting,
        )

    not_reported = list(LAUNCH_NOT_REPORTED)
    if offer is None:
        not_reported.append(_NO_OFFER_CITED)
    # 「地区与市场的相容性」只在**引了一版方案**时才有意义：没有方案就没有
    # ``markets`` 可比，报出来是一条没有主语的缺项。
    for item in cited:
        if item.reading.kind is SubjectKind.OFFER:
            not_reported.append(_cited_offer_not_reported(item.stored))

    ordered = sorted(cited, key=lambda item: (item.ident, item.version))
    return LaunchPreview(
        subject=subject,
        declared=scene,
        record=record,
        at=at,
        cited=tuple(item.reading for item in ordered),
        missing=tuple(missing),
        invalid=tuple(invalid),
        awaiting_approval=tuple(awaiting),
        not_reported=tuple(not_reported),
    )


__all__ = [
    "LAUNCH_NOT_REPORTED",
    "LaunchNextAction",
    "LaunchPreview",
    "launch_preview",
    "launch_subject",
]

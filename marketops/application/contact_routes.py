"""§4 的唯一一趟：从**材料**走到 ``ContactRoute`` / ``RouteOpportunity``。

## 为什么这必须是一个用例，而不是两个调用点各写一遍

在它之前，路线的生产者只有一个：``pipeline._parse_and_bind``，而它的入口是
``cli.py`` 的 ``run-demo``。于是 MCP 面上**没有任何工具能产出一条路线**——
``get_account`` 读得出路线，是因为有人先在命令行上把它们写进了库。研究结果从
``submit_findings`` 走到联系路线这一步，在服务面上根本不存在。

把这一趟抄进 MCP 处理器会得到一个更坏的结局：两处各自判时效、各自定级，于是
同一条路线经由两个入口落库时**等级可能不同**，而报告上只看得出「多了几个 X」。
所以这里收的是**材料**（一段已解析的文本 + 它的来源），两侧的调用点各按自己的
方式取材料，判据、顺序、落盘全在这一处。

## 一条材料失败，不带走其余材料

``bind_from_text`` 会为**材料本身**的问题抛 ``ValidationError``：页面上的日期被
解析成了未来、来源元数据畸形成不了证据行。这类失败是**关于那一份材料**的，不是
关于这一轮研究的 —— §5 要求一个坏页面不能打死整轮，而「打死」在今天的具体形状
就是：一个 ``ValidationError`` 从判据里穿出来，而调用点只接 ``MarketOpsError``，
于是整轮停在一个页面上，报告里连是哪一页都没有。

所以每一份材料各有一个 :class:`MaterialOutcome`：跑成了记 ``BOUND``（产出可能是
零 —— 那也是一个结论），没跑成记 ``INVALID_MATERIAL`` 并留下判据原文。

**落盘排在全部判断之后**：一份材料要么整份落到库里，要么一行都不落。中途失败而
证据已经写进去，会让「这份材料被拒了」与「它的一部分留下了」同时成立，而下一次
重跑读到那些证据时，没人说得清它们是哪一次来的。

## 落下去的是**观察**，读出来的是**投影**（§6）

这一趟不再往一个「路线表」里追加行。它交的是
:class:`~marketops.domain.route_records.RouteObservation` —— 一次观察，不可变、
只追加；由存储适配器用同一个 :func:`~marketops.domain.route_records.fold_observation`
折出当前读法（``CanonicalContactRoute``，每个规范键一行）并把那一行还回来。

折叠**不在这里做**，理由不是分层洁癖：三个适配器各折一遍就是三份判据，而它们的
分歧只会在「同一条路线在两个部署里等级不同」时暴露 —— 报告上那是「多了几个 X」。
所以这一趟只负责「交一条观察」，折法是域层一处的事。

## 拒收**落盘**，不再算完就丢（§6.3）

在这一版之前，绑定层拒掉的每一条只活在 ``RouteProduction.rejected`` 这个内存列表
里：调度器跑完，它就没了。于是「这一轮拒了 37 条」是唯一读得出来的东西 —— 而 37
条里可能有 30 条是「这一页没有联系人」（正常），3 条是「域名判错了」（要修），
4 条是「有人把另一家组织的邮箱抄了过来」（要改接线）。同一个数字，三种处置。

现在每一条都落成一张 :class:`~marketops.domain.route_records.RouteRejection`：
候选原文与字段、来源与证据、类别与判据原文、依据的规则版本、这一趟的
run / work item、时刻。**这里只做映射**，判词一个字都不重新生成 —— 它是
:class:`~marketops.domain.bind.RejectedBinding` 加三格记账（哪一趟、哪一版规则、
什么时候），而那三格正是绑定层不该知道的东西。

## 这一趟还给维护账写一条变更信号

给某个账号**真的**添了新来源（此前不存在的 ``evidence_id``）时，这一趟当场记一条
:class:`~marketops.domain.maintenance.ChangeSignal`。理由是维护那一侧的
``RefreshPlan``：复核是按周期跑的，而一次新研究是一次**事件** —— 不报的话，这一格
的来源供给要等满一个周期（默认 180 天）才会被重新数一遍，而报告上什么都看不出来。

信号由**写入方**报而不是由维护 tick 自己发现，判据（新行 vs 行）与时刻的取法逐条
写在 :func:`_signal_for_new_sources` 里。

## 不做的事

不抓取、不解析、不检索。这个用例收的是**已经拿到手的文本** —— 抓取要网络、解析
要路由器，两者都是适配器的事，而让它们出现在这里会让这一趟只在联网时可测。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import ValidationError

from ..domain.bind import (
    BindResult,
    BindingSubject,
    RejectedBinding,
    RouteVocabulary,
    bind_from_text,
)
from ..domain.entities import ContactRoute, RouteOpportunity
from ..domain.enums import SourceKind
from ..domain.evidence import Evidence
from ..domain.maintenance import Aspect, ChangeSignal
from ..domain.profile import ProfileBundle
from ..domain.route_records import (
    CanonicalContactRoute,
    RouteObservation,
    RouteRejection,
    RouteRejectionCode,
    canonical_route_key,
    observation_id_for,
    rejection_id_for,
)
from ..domain.temporal import FreshnessPolicy
from ..domain.util import utc_now
from ..ports.scenes import MaintenanceStore
from ..ports.store import EvidenceStore
from .freshness import Judgement, judge, policy_of, rebuild
from .grading import grade_route
from .inputs import SUBJECT_KIND_ACCOUNT


@dataclass(frozen=True)
class RouteMaterial:
    """一份**待绑定的材料**：一段已解析的文本，连同它是从哪来的。

    字段与 :func:`~marketops.domain.bind.bind_from_text` 的关键字一一对应，一处
    不多。多出来的只有 :attr:`origin` —— 它不进任何模型，只进报告，回答的是
    「这一份是哪来的」，而 ``source_url`` 回答不了这个问题：同一次结算里的几条
    证据可以指向同一个 URL（整页抓一次，切几段引用），那时按 URL 读的人会以为
    自己看到的是同一份材料。

    :attr:`subject` 是**绑定这一层真正需要的那三格**（见
    :class:`~marketops.domain.bind.BindingSubject`）。它是 ``AccountRecord`` 与
    ``MarketAccount`` 的公共投影，所以池子里的账号与手写清单里的账号走的是同一条
    绑定路径 —— 这一条正是路线能有两个生产者的前提。
    """

    workspace: str
    subject: BindingSubject
    source_url: str
    source_kind: SourceKind
    text: str
    content_sha256: str
    fetched_at: datetime
    parser: str = ""
    http_status: int | None = None
    tier: str = ""
    #: 这一份材料从哪来（``fetch:<url>`` / ``evidence:<id>``）。**只进报告。**
    origin: str = ""


class MaterialStatus(str, Enum):
    """一份材料的结局。两档都是**结论**，没有「失败」。

    ``BOUND`` 不承诺有产出：一段没有邮箱也没有具名角色的文本照样绑定成功，产出
    是零条路线 —— 而「绑了、什么都没有」与「没绑成」在报告上是两件事（前者可以
    追问这一页写了什么，后者要去看判据）。
    """

    BOUND = "bound"
    INVALID_MATERIAL = "invalid_material"


@dataclass(frozen=True)
class MaterialOutcome:
    """一份材料跑完之后的账。"""

    origin: str
    status: MaterialStatus
    #: 没跑成时是判据原文；跑成了就是空串。
    reason: str = ""
    routes: int = 0
    opportunities: int = 0
    evidence: int = 0


@dataclass(frozen=True)
class RouteContext:
    """这一趟跑的**记账上下文**：§6.3 要求每一张拒收回执说得出「这是哪一趟」。

    ``run_id`` 没有缺省，而且空串会被当场拒绝。理由是
    :class:`~marketops.domain.route_records.RouteRejection` 上那两格的存在意义：
    它们回答「这一条拒收是哪一趟跑的」—— 而一个可以省略、或者可以填空的参数，
    会让所有拒收回执在报告里长得一样，同时每一张都声称自己记了这件事。

    ``work_item_id`` 有缺省（空串），因为它**真的可能没有**：命令行那一趟是拿一个
    手写清单在跑，没有工作单元这一层。空串是「没有这件工作」，不是「忘了填」——
    两件事在这一格上确实分不开，所以它不进任何幂等键（``rejection_id`` 的原像里
    没有它，见 :func:`~marketops.domain.route_records.rejection_id_for`）。
    """

    run_id: str
    work_item_id: str = ""

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError(
                "RouteContext.run_id 不能为空：拒收回执上的 run / work item 是 §6.3 "
                "要求的记账格，而一个可以留空的 run_id 会让每一张回执都声称记过它。"
            )


@dataclass(frozen=True)
class RouteProduction:
    """一趟产出。**全部是数据**，指标由调用方按自己那份帐去记。

    这里刻意不接一个 ``AccountMetrics``：``AccountMetrics`` 带 ``cohort``，而池子
    里的账号没有那一格（它属于手写清单那一代），于是接它会把「谁在数」变成第二个
    需要分叉的地方。返回数据则两条调用点各记各的，判据不受影响。

    ## 读数与账：``observations`` / ``projections`` / ``rejections``

    §6 把路线拆成两层之后，这一趟的产出也分成了两半，而它们在报告上回答的是两个
    不同的问题：

    * :attr:`observations` —— **这一趟看到了什么**。每条绑定一行，含它的观察时刻。
      一次研究重看同一个邮箱就是新的一条观察，两条都在这里；
    * :attr:`projections` —— **折完之后盘上现在是什么**。每个规范键一份，是
      ``store.record_route`` 的返回值，所以它与库里那一行逐字相同（不是这里另外
      算了一遍）；
    * :attr:`rejections` —— **这一趟没收下的那些**，已经落盘（§6.3）。

    三者都不是「更全的那一份」：一条路线被看了三遍是三条观察、一份投影，而一次
    拒收只出现在拒收里。想要「这个账号现在有哪些路线」的读者要的是
    :attr:`projections`，不是 :attr:`observations` —— 后者按定义会随看的次数增长。
    """

    #: 这一趟看到的每一条（含时刻与来源那一趟）。**只增不减** —— 见类 docstring。
    observations: tuple[RouteObservation, ...] = ()
    #: 折叠之后的当前读法，每个规范键一份。**就是盘上那一行。**
    projections: tuple[CanonicalContactRoute, ...] = ()
    #: 这一趟落盘的拒收回执（§6.3）。此前它们**没有任何一处**留得下。
    rejections: tuple[RouteRejection, ...] = ()
    opportunities: tuple[RouteOpportunity, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    #: 每一行的时效判定（判过与没判过的都在 —— 计数要分得开）。
    judgements: tuple[Judgement, ...] = ()
    outcomes: tuple[MaterialOutcome, ...] = ()
    #: 绑定层拒掉的每一条（SOP L121 的归属保护、snippet 不算证据……）。留存而不是
    #: 丢弃：它本身就是指标。
    #:
    #: 与 :attr:`rejections` 的差别是**这一层**的差别：这一格是绑定层的判词
    #: （``RejectedBinding``，不知道这一趟是谁在跑），那一格是落盘的收据
    #: （``RouteRejection``，带 run / work item / 规则版本）。两格一一对应，都在
    #: 这里是因为调用点要计数的那一半（按 code 分类）不需要读库，而要追责的那一半
    #: 必须读得到。
    rejected: tuple[RejectedBinding, ...] = ()
    #: 这一趟**真的**给某个主体添了来源时记下的变更信号，一个主体一条。见
    #: :func:`_signal_for_new_sources`。
    #:
    #: 空元组有两个成因，而它们在这一格上分不出来：真的没有新来源，或者这一趟
    #: 没有维护账可写（``maintenance=None``）。后者是**接线事实**，不是业务结论，
    #: 所以它要由调用方在回执里说出口，不能靠这一格替它说。
    signals: tuple[ChangeSignal, ...] = ()

    @property
    def routes(self) -> tuple[ContactRoute, ...]:
        """这一趟绑定出来的每一行（``observations[i].route``）。

        **它是读数，不是账。** 「这个账号现在有哪些路线」要读 :attr:`projections`
        —— 一条路线被研究三遍时这里有三个元素而那里只有一个。
        """
        return tuple(item.route for item in self.observations)

    @property
    def snippet_bindings_rejected(self) -> int:
        """被拒的绑定里有多少条是 ``SEARCH_SNIPPET`` —— SOP L119 那一类。

        判据读的是 :attr:`RejectedBinding.code`，不是 ``reason`` 的前缀。此前这里
        是 ``item.reason.startswith("ineligible_source")``：``reason`` 是给人读的
        那一句（``f"{code.value} —— {detail}"``），拿它做分类等于把类别与描述合并
        成一个字符串 —— 而描述那一半是会被改写的（改一句措辞就会让这个计数器
        归零，且没有任何一处会红）。
        """
        return sum(
            1 for item in self.rejected if item.code is RouteRejectionCode.INELIGIBLE_SOURCE
        )

    @property
    def invalid_materials(self) -> tuple[MaterialOutcome, ...]:
        return tuple(item for item in self.outcomes if item.status is not MaterialStatus.BOUND)


def _first_problem(exc: ValidationError) -> str:
    """把一次 ``ValidationError`` 压成一句人能读的理由。

    只取**第一条**，并带上它的字段路径：后面那些多半是同一个原因的连带（一行构造
    不过，依赖它的那几格也报错），全部列出来会让报告里的一句话变成一段堆栈，而读的
    人要的是「哪一格、为什么」。
    """
    errors = exc.errors()
    if not errors:
        return str(exc)
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "")).strip()
    return f"{loc}: {message}" if loc else message


def _observation_for(material: RouteMaterial, route: ContactRoute) -> RouteObservation:
    """一条路线 + 它是哪一刻在哪份材料上看到的 → 一条观察。

    ``observed_at`` 取材料的 ``fetched_at``，不取 ``judged_at``：这一条说的是
    「**这一页**上是这么写的」，而那一页的时刻是它被取下来的时刻。取判定时刻的话，
    同一份材料在两次结算里会被看成两条不同时刻的观察 —— 而它们不是两件事，是同
    一份字节被读了两次（观察的内容寻址里因此含有 ``observed_at``：
    它进原像让「同一页同一次取用」重放幂等，见 ``observation_id_for``）。

    ``canonical_key`` 与 ``observation_id`` 都在这里算一次，不让调用点各算一遍 ——
    键算错的表现是两条本该是同一键的观察各写一行投影，而报告上什么都不会不对。
    """
    key = canonical_route_key(
        workspace=material.workspace, account_id=route.account_id, email=route.email
    )
    return RouteObservation(
        observation_id=observation_id_for(
            workspace=material.workspace,
            account_id=route.account_id,
            canonical_key=key,
            observed_at=material.fetched_at,
            route=route,
        ),
        canonical_key=key,
        workspace=material.workspace,
        account_id=route.account_id,
        observed_at=material.fetched_at,
        route=route,
    )


def _rejection_for(
    rejected: RejectedBinding,
    *,
    material: RouteMaterial,
    context: RouteContext,
    rule: str,
) -> RouteRejection:
    """绑定层的一条判词 → 一张可以落盘的收据（§6.3）。

    逐格对应，**没有一处重新判定**：``code`` / ``detail`` / ``evidence_ids`` 原样
    搬过来（它们就是拒的理由与依据），加上去的只有绑定那一层不该知道的三件事 ——
    这是哪一趟（``run_id`` / ``work_item_id``）、按哪一版规则判的（``rule``）、
    什么时候（``occurred_at``）。

    ``rule`` 取的是 ``bundle.scene_version``：拒收的判据（哪些域算这个账号的、
    哪些来源合格）来自 profile 那批字节，而这个版本号正是「那批字节是哪一批」的
    机器可读答案。**不写 SOP 条款号**：条款指针在 profile 里
    （``sop_reference``，那是业务侧声明的），而在这里手抄一张
    「code → 条款号」的表，会在 SOP 改版时安静地过期。

    ## ``occurred_at`` 取材料的 ``fetched_at``，与 ``_observation_for`` 逐字同一条

    它**不是**这一批的判定时刻 —— 这一条曾经取 ``judged_at``，而那让同一份材料在
    两次结算里成为**两张**回执：``rejection_id`` 的原像里有 ``occurred_at``，而
    ``judged_at`` 缺省是 ``utc_now()``。于是「重试一次」「两个 worker 同时跑同一批」
    「维护轮次重看同一页」都会往 ``route_rejection`` 里多写一行，而那一行与上一行
    逐格相同、只差一个时刻 —— 报告上它看起来像「拒收在涨」。

    收据说的是「**这一页上写的这个候选，我们没有收下**」，而那一页的时刻是它被取下来
    的时刻（与 :func:`_observation_for` 的理由逐字相同）。取材料的时刻让同一份材料
    重放幂等，「同一页被**重新**取下来一次」才是新的一条 —— 后者是一次真的新观察，
    两条都对。

    ``run_id`` / ``work_item_id`` 因此是**改写账**：同一份材料第二次被结算时写的是
    同一张收据（主键相同 ⇒ 覆盖），上面留的是**最近一次**拒它的那一趟。这与
    ``CanonicalContactRoute.revision`` 是同一条纪律 —— 「现在是谁」读投影，
    「历来有哪几回」读历史（观察那一边是 ``list_route_observations``）。
    """
    return RouteRejection(
        rejection_id=rejection_id_for(
            workspace=material.workspace,
            account_id=rejected.account_id,
            code=rejected.code,
            candidate=rejected.literal_value,
            source_url=rejected.source_url,
            occurred_at=material.fetched_at,
        ),
        workspace=material.workspace,
        account_id=rejected.account_id,
        candidate=rejected.literal_value,
        field=rejected.field,
        code=rejected.code,
        detail=rejected.detail,
        rule=rule,
        source_kind=rejected.source_kind,
        source_url=rejected.source_url,
        evidence_ids=rejected.evidence_ids,
        run_id=context.run_id,
        work_item_id=context.work_item_id,
        occurred_at=material.fetched_at,
    )


def produce_routes(
    *,
    materials: Sequence[RouteMaterial],
    bundle: ProfileBundle,
    store: EvidenceStore,
    maintenance: MaintenanceStore | None,
    context: RouteContext,
    vocabulary: RouteVocabulary | None = None,
    now: datetime | None = None,
) -> RouteProduction:
    """把一批材料绑定、判时效、定级、落盘。

    Args:
        materials: 已经拿到手的文本。**一份坏材料不影响其余**。
        bundle: 时限口径与等级规则的来源。**没有声明时限就失败**（``policy_of``）——
            那是配置问题，不是材料问题，所以它照样抛：一次没有口径的运行不能把
            它算出来的 ``stale`` 写进每一行产出。它同时是拒收回执上那一格
            ``rule`` 的来源（``scene_version``）。
        maintenance: 维护账。这一趟是**唯一**一处往它里面写变更信号的地方
            （见 :func:`_signal_for_new_sources`）。**没有缺省** —— 一个可以省略
            它的调用点会在「这一趟添了新来源」这件事上静默地什么都不说，而报告里
            看不出区别：那一格的下一次复核要等满一个周期。这正是
            ``application/discovery_run.py`` 那一版的取舍教训：一个「不传就少一组
            事实」的参数，就是同一份判据下的两个行为。

            ``None`` 是**一个要写出来的取值**，意思是「这一位调用方没有维护账」——
            今天只有服务面那种**只接控制面**的实例走这条路（组合根没接维护账时）。
            那时这一趟照常绑定、判时效、定级、落盘，只是不记信号，``signals``
            为空 —— 它与「算过了、没有新来源」在 :class:`RouteProduction` 上确实
            同形，所以**回执里那一句必须由调用方补**（``_researched_routes`` 那里
            写的就是这件事：接线事实，不是「来源没有变化」）。
            装配侧的必填在别处：``PipelineDeps.maintenance`` 没有缺省，因为那是
            **一次装配**里的决定，而这里是一次**调用**里的决定 —— 写在调用点上，
            当场可见。
        context: 这一趟的记账上下文（``run_id`` / ``work_item_id``）。**没有缺省**，
            理由与 ``maintenance`` 逐字相同：一张说不出「哪一趟」的拒收回执，
            与一张忘了填的回执在盘上一模一样，而 §6.3 要的正是前者。
        vocabulary: 来自 profile 的字面词表。缺省是空词表 —— 不猜任何业务词。
        now: 这一批的判定时刻。缺省取当前时刻；显式传入时**整批共用同一个**，
            于是同一批产出里的 ``evaluated_at`` 是可比的。

    Returns:
        一趟产出，含每份材料的结局。
    """
    policy = policy_of(bundle)
    judged_at = now or utc_now()
    #: 拒收回执上那一格「按哪一版规则判的」。整批同一个值 —— 一次调用只有一批
    #: 字节在手上，所以它在同一批里不可能是两个值。
    rule = bundle.scene_version

    observations: list[RouteObservation] = []
    projections: list[CanonicalContactRoute] = []
    rejections: list[RouteRejection] = []
    opportunities: list[RouteOpportunity] = []
    evidence_rows: list[Evidence] = []
    judgements: list[Judgement] = []
    outcomes: list[MaterialOutcome] = []
    rejected: list[RejectedBinding] = []
    #: 主体 → （本次**真的**新落下的证据行）。键是 ``(workspace, account_id)`` 而不是
    #: 账号 id：工作区是授权链的第一段，而信号要按它落账。
    new_sources: dict[tuple[str, str], list[Evidence]] = {}

    for material in materials:
        origin = material.origin or material.source_url
        try:
            bound = bind_from_text(
                workspace=material.workspace,
                subject=material.subject,
                text=material.text,
                source_kind=material.source_kind,
                source_url=material.source_url,
                content_sha256=material.content_sha256,
                fetched_at=material.fetched_at,
                vocabulary=vocabulary,
                parser=material.parser,
                http_status=material.http_status,
                tier=material.tier,
            )
            produced = _judge_and_grade(
                bound=bound,
                subject=material.subject,
                bundle=bundle,
                policy=policy,
                judged_at=judged_at,
            )
        except ValidationError as exc:
            # 材料自己的问题。留下的那句话是**判据原文**，不是这里替它编的一句
            # —— 编出来的那句在报告上看起来像一个业务结论（「这一页有问题」），
            # 而原文说的是哪一格、为什么，读的人据此知道要不要去改解析器。
            outcomes.append(
                MaterialOutcome(
                    origin=origin,
                    status=MaterialStatus.INVALID_MATERIAL,
                    reason=_first_problem(exc),
                )
            )
            continue

        planned_routes, planned_opportunities, produced_judgements = produced
        # 到这里为止这一份材料**一行都还没落盘**：要么整份落，要么一行不落
        # （见模块 docstring）。写失败（``StoreError``）照样抛出去 —— 那是存储
        # 的事故，不是这一份材料的结论。
        for row in bound.evidence:
            # 「这一条是新的吗」要先问一次。``put_evidence`` **不回答这个问题**：
            # 它对同 id 同内容幂等，返回 ``None``（``ports/store.py`` 逐字如此），
            # 于是「写进去了一条新的」与「这一条早就在了」在调用点长得一样。
            # 而这一格要分的正是这两件事：报告一次「来源变了」而实际上只是重发，
            # 会把这一格的下一次复核提前整整一个周期，理由还是一句假话。
            #
            # 不在**端口**上加一个返回值：那一条被三个适配器与若干个调用点共享，
            # 为了一次报告改它的爆炸半径最大，而这里多读一次的代价在那个量级面前
            # 不值一提。
            #
            # ``maintenance`` 为 ``None`` 时**连问都不问**（少一次读）—— 这一趟
            # 的 ``signals`` 反正会是空的，而「算了但没有信号」与「没算」在
            # ``RouteProduction`` 上分不出来，所以不去制造一个看起来算过了的空集。
            if maintenance is not None and store.get_evidence(row.evidence_id) is None:
                new_sources.setdefault((row.workspace, row.account_id), []).append(row)
            store.put_evidence(row)
            evidence_rows.append(row)
        for opportunity in planned_opportunities:
            store.put_opportunity(opportunity)
        # 观察先落、再折。``record_route`` 把那三步包在**一个写事务**里，并且把
        # 折出来的当前读法还回来 —— 所以 ``projections`` 里的每一份就是盘上那一行，
        # 不是这里另算的一遍（见 ``ports/store.py`` 那一格）。
        for route in planned_routes:
            observation = _observation_for(material, route)
            observations.append(observation)
            projections.append(store.record_route(observation))
        # 拒收**落盘**，不再算完就丢（§6.3）。落在这里而不是绑完立刻落：与产出的
        # 一切共享同一个「这一份材料要么整份落、要么一行不落」的边界。
        for item in bound.rejected:
            rejection = _rejection_for(
                item,
                material=material,
                context=context,
                rule=rule,
            )
            store.put_route_rejection(rejection)
            rejections.append(rejection)

        opportunities.extend(planned_opportunities)
        judgements.extend(produced_judgements)
        rejected.extend(bound.rejected)
        outcomes.append(
            MaterialOutcome(
                origin=origin,
                status=MaterialStatus.BOUND,
                routes=len(planned_routes),
                opportunities=len(planned_opportunities),
                evidence=len(bound.evidence),
            )
        )

    signals = tuple(
        _signal_for_new_sources(
            workspace=workspace,
            account_id=account_id,
            rows=rows,
            at=judged_at,
            store=maintenance,
        )
        for (workspace, account_id), rows in sorted(new_sources.items())
    )
    return RouteProduction(
        observations=tuple(observations),
        projections=tuple(projections),
        rejections=tuple(rejections),
        opportunities=tuple(opportunities),
        evidence=tuple(evidence_rows),
        judgements=tuple(judgements),
        outcomes=tuple(outcomes),
        rejected=tuple(rejected),
        signals=signals,
    )


def _signal_for_new_sources(
    *,
    workspace: str,
    account_id: str,
    rows: Sequence[Evidence],
    at: datetime,
    store: MaintenanceStore,
) -> ChangeSignal:
    """这一趟给这个账号新添了来源 → 一条变更信号，并**当场记下**。

    ## 为什么这一格由**写入方**报，而不是由维护 tick 自己发现

    ``ChangeSignal`` 的 docstring 逐字说明了它的用途：周期复核够用、但不够**及时**，
    「一个场地换了运营方，在 90 天的周期里会被当成旧的继续用」。而
    ``application/refresh.py`` 那一侧读的是**已经落盘的**记录 —— 它看得出来
    「现在手上有哪些来源」，却看不出来「比我上次数的时候多了一条」，除非它把两次
    盘点逐份比对（那需要知道**上一次**是哪一次，而它没有账本）。真正知道变化发生了
    的只有这里：刚刚把新行写进去的那只手。

    时间上这一条也说得通：tick 是按周期跑的，而这是一次**事件**。「这一格该重看了」
    这句话从事件那边发出来，下一次 tick 才会把它排上（``DueReason.CHANGE_SIGNALLED``）。

    ## 「真的变了」的判据是**新行**，不是**行**

    只有此前不存在的 ``evidence_id`` 才算新（见上面那一处 ``get_evidence``）。
    同一次研究的重发、或者另一位研究员引用了同一条来源，都不算变化 —— 而它们都会
    让 ``bound.evidence`` 非空。按行数发信号的话，一次重放会把每一格的复核提前一个
    周期，而理由是假的。

    ## ``signalled_at`` 取整批的判定时刻

    与 ``judged_at`` 同一个值（``produce_routes`` 传进来的 ``now``）：一次结算产出的
    所有东西共享一个时刻，于是「这一批是同一次运行的产物」在盘上看得出来。
    它也顺带给了一条幂等性：同一次结算重复绑定（不会发生 —— 调用点按 ``replayed``
    挡掉了）会落到同一个 ``(主体, aspect, 时刻)`` 上，于是不是两行。
    """
    kinds: dict[str, int] = {}
    for row in rows:
        kind = row.source_kind.value
        kinds[kind] = kinds.get(kind, 0) + 1
    tally = "、".join(f"{kind}×{count}" for kind, count in sorted(kinds.items()))
    signal = ChangeSignal(
        workspace=workspace,
        subject_kind=SUBJECT_KIND_ACCOUNT,
        subject_id=account_id,
        aspect=Aspect.SOURCE_COVERAGE,
        signalled_at=at,
        reason=(
            f"这一轮研究为这个账号新落了 {len(rows)} 条来源（{tally}）——"
            "来源供给不再是上一次盘点时的样子，该重看一眼。"
        ),
    )
    store.put_signal(signal)
    return signal


def _judge_and_grade(
    *,
    bound: BindResult,
    subject: BindingSubject,
    bundle: ProfileBundle,
    policy: FreshnessPolicy,
    judged_at: datetime,
) -> tuple[list[ContactRoute], list[RouteOpportunity], list[Judgement]]:
    """判时效 → 定级，**只重建成一次新行**。

    两格都走 :func:`~marketops.application.freshness.rebuild`（而不是
    ``model_copy``），所以每一行落盘之前都真的过过自己的行级判据 —— 见
    ``application/freshness.py`` 的模块文档。定级读的是判过时效的那一行，与落盘的
    那一行因此是同一个对象，不存在「判的和存的不一致」。

    账号的域取自 :attr:`BindingSubject.domains`，**不另收一个账号对象**：同一个
    组织在池子里是 ``MarketAccount``（``domains``，复数）、在手写清单里是
    ``AccountRecord``（``domain``，单数），收其中一个就会让另一条路径喂 ``None``，
    于是同一条路线在两个入口下判出不同的等级 —— 而这在报告上只是「多了几个 X」。
    """
    judgements: list[Judgement] = []

    judged_opportunities: list[RouteOpportunity] = []
    for opportunity in bound.opportunities:
        judgement = judge(opportunity, policy, now=judged_at)
        judgements.append(judgement)
        judged_opportunities.append(judgement.row)

    graded: list[ContactRoute] = []
    for route in bound.routes:
        # 先判时效、再定级。顺序不能反：定级读的是事实，而「这条事实今天还算不算
        # 数」是另一格（见 tests/test_grading.py 里那条「定级不读时刻」）。
        judgement = judge(route, policy, now=judged_at)
        judgements.append(judgement)
        outcome = grade_route(
            judgement.row, bundle, account_domains=subject.domains
        )
        graded.append(
            rebuild(judgement.row, grade=outcome.grade, grade_reason=outcome.reason)
        )

    return graded, judged_opportunities, judgements


__all__ = [
    "MaterialOutcome",
    "MaterialStatus",
    "RouteContext",
    "RouteMaterial",
    "RouteProduction",
    "produce_routes",
]

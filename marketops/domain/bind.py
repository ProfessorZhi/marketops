"""证据绑定：把字面命中变成带证据的 ``ContactRoute``。

**本模块承载全项目最重要的一条不变量：**

    只有 ``BINDING_ELIGIBLE_SOURCES`` 里的来源可以成为路线的依据。
    SERP snippet 里的邮箱一律拒绝 —— 即使它看起来完全有效。

理由是需求原文写死的那句：搜索结果里出现邮箱，必须「抓取真实公开页面/PDF →
在镜像中发现 literal email → 保存 evidence」才能进入结果。把它做成类型层面的
拒绝（而不是流程约定），是因为流程约定会在第一次「先跑通再说」的修改里消失。

另外四条不变量：

* **归属要有位置证据**（§9：「不得 A 页取姓名、B 页取邮箱，中间没有关系证据就缝成
  一条路线」）。姓名/岗位要能算在这条邮箱名下，两者必须落在**同一张卡片的条目窗口**
  里（``extract.in_the_same_entry``）：同一行，或紧邻的一行。此前这一步**什么都没查**
  —— 只要整页有一个姓名与一个邮箱，就产出 ``P1`` + ``STRONG``，而 ``P1`` 在
  ``grading.yaml`` 里的原文是「相关人 / 岗位 / 邮箱在同一 record block 内 ——
  归属可以确认」。那是一条写着「同块」而从不计算块的判据。
* **保守归属**：一个文档里同时出现多个邮箱与姓名/角色时，无法判断谁是谁 ——
  这时不猜，标记 ``has_conflict`` 并让 grading 降到 X。这是 false-attribution
  指标的直接来源。
* **不归属外来域**（SOP L121）：「页面上其他组织的目录邮箱不得因同页出现而
  归属目标账户」。账号域名已知且与该邮箱域不符时，**不产出路线**，
  记一条 ``foreign_domain`` 拒绝。
* **没有邮箱就不是路线**（SOP L126）：A 与 B 都要求真实公开业务邮箱。
  有具名角色但拿不到邮箱时产出 :class:`RouteOpportunity` —— 它是有价值的
  产出，但它不是一条可联系的路线，因此不能叫 B。

**本模块不判定等级。** 它只负责把字面命中整理成一条结构完整的路线，
等级由 ``marketops.application.grading`` 求值。分开的理由很直接：
归属是「这段文字说了什么」，等级是「业务上算哪一级」——
后者要有 SOP 才能回答，前者不需要。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from .enums import (
    BINDING_ELIGIBLE_SOURCES,
    BusinessFunction,
    ContactField,
    DomainVerdict,
    EntryKind,
    Grade,
    GradeReason,
    RecordBlockPriority,
    RoleClass,
    RouteBinding,
    SourceKind,
)
from .account import MarketAccount
from .evidence import AccountRecord, Evidence, evidence_id, moments_from_evidence
from .subject import SubjectDossier
from .util import check_canonical_id, normalise_email
from .extract import (
    LiteralHit,
    extract_emails,
    extract_labelled_names,
    extract_obfuscated_emails,
    extract_roles,
    in_the_same_entry,
    page_publication_time,
)
from .entities import ContactRoute, Person, Role, RouteOpportunity
from .route_records import RouteRejectionCode

#: 无合格来源时，拒绝原因用这个常量，便于统计。
#:
#: 五个取值**同源**于 :class:`~marketops.domain.route_records.RouteRejectionCode`
#: （``.value``）：一张枚举表与一串字符串常量并存的话，第一个记得改的人只会改其中一处，
#: 而症状是报告里同一类拒收有两行 —— 一行是枚举名、一行是旧字符串。
REASON_INELIGIBLE_SOURCE = RouteRejectionCode.INELIGIBLE_SOURCE.value
REASON_EMPTY_VALUE = RouteRejectionCode.EMPTY_VALUE.value
#: 邮箱域与账号域不符 —— SOP L121，不归属目标账户。
REASON_FOREIGN_DOMAIN = RouteRejectionCode.FOREIGN_DOMAIN.value
#: 邮箱没有与任何岗位/姓名同块绑定，也不是可识别的业务入口或泛化官方邮箱。
#: 它不是「坏数据」，是「还不足以成为一条路线」—— 证据都留着。
REASON_UNBOUND_EMAIL = RouteRejectionCode.UNBOUND_EMAIL.value
#: 这一页上**有**具名/岗位，但没有一条落在这条邮箱的条目窗口里（§9）。
#: 与 :data:`REASON_UNBOUND_EMAIL` 分开数，因为它们是两句不同的话，处置也不同：
#: 一句是「这一页没给出归属」，另一句是「这一页给了，但给的是**别处**的人」——
#: 后者要的是回那个页面找这张卡片，而不是把它当成「这一页没有联系人」。
REASON_ENTRY_ELSEWHERE = RouteRejectionCode.ENTRY_ELSEWHERE.value
#: 这一页上有**多个**邮箱，同时还有具名或岗位 —— 谁是谁配不上号。
#: 与 :data:`REASON_ENTRY_ELSEWHERE` 分开，理由与上一条同族：那一句说「人在别处」，
#: 这一句说「人就在这一页上，但这一页上有好几个入口」。两条不同的实话。
REASON_CONFLICTING_EVIDENCE = RouteRejectionCode.CONFLICTING_EVIDENCE.value


@dataclass(frozen=True)
class BindingSubject:
    """**谁**在被绑定 —— 绑定这一层真正需要的那三格，一格不多。

    ## 为什么不是 ``AccountRecord``

    这一层用到账号的地方只有三处：``account_id``（证据与路线的主语）、
    ``organisation``（路线上的那一格）与域名（SOP L121 的不归属外来域）。
    而 ``AccountRecord`` 有七格，其中 ``cohort`` 是一个**闭集必填**字段 ——
    它的取值来自「已知账号实验」的输入契约（``known_success`` /
    ``accessible_no_core_email`` / ``previously_blocked``）。

    于是收 ``AccountRecord`` 的后果是：**只有那份手写清单里出来的账号绑得了**。
    池子里的 ``MarketAccount``（研究产出物化的那些）没有 cohort，而它没有
    诚实的值可填 —— 「这个组织是哪一类已知账号」这句话对一条研究出来的候选
    根本不成立，编一个出来就是本仓明令禁止的那种「看起来正常的编造」。

    今天的形状正是这个后果：路线只有一个生产者，而它只能是 CLI
    （``pipeline.py`` ← 那份手写 YAML）。研究侧跑完一轮，池子里有账号、
    有资产、有关系，而一条路线都产不出来。

    ## 为什么不用「带 ``cohort: Cohort | None`` 的 AccountRecord」

    那是同一个问题的另一种写法：放松一个**输入契约**的必填项，好让**输出侧**
    对象能用它。清单那一侧从此分不出「这份清单忘了写 cohort」与「这一行是从
    池子里来的」，而那是两件处置相反的事（前者是有人在手写文件时漏了一格）。

    ## 三个投影点是**三个**，而不是一个「三者取其一」的函数

    :meth:`from_account_record` / :meth:`from_market_account` /
    :meth:`from_dossier` 各自只认自己的输入。写成一个收 union 的函数会让
    「这一行是从哪来的」在这层消失，而 ``AccountRecord.domain``（一个）、
    ``MarketAccount.domains``（一串）与 ``SubjectDossier.domain``（一个）正是
    那三个来源的形状差异 —— 在这里折平之后，一个多域名的组织会被绑成
    「域名不符」，而报告上那只是一条普通的拒绝。

    ## 第三个投影点为什么是 ``SubjectDossier`` 而不是账号池那一行

    ``SubjectDossier`` 是**受理那一刻冻住的身份**（``domain/subject.py``），它
    与 ``MarketAccount`` 指同一个 ``account_id``（``application/inputs.py`` 逐字
    如此），但两者在一条链上出现的时刻不同：服务面结算一份产出时，手边有的是
    这件工作冻住的主语，而不是「池子里现在的那一行」。去补一次池子读取会引入一
    个**更晚的**真相来源 —— 池子里的组织名与域名是**现在**的，而这一次绑定要
    说的是「这批材料是不是当初交下来的那个主体的」。两者漂移的那天，后一种读法
    会把结果绑到改名之后的组织上，而报告完全正常。

    域名那一格照 :meth:`from_account_record` 的规矩：一个空串是**空集合**
    （「不知道域名」），不是一个叫 ``''`` 的域。

    ## 域名是**集合**，而且在这里就规范化

    判据是「这个邮箱域是不是**这个组织**的域」，所以它是**成员关系**不是相等
    关系。取值在这里统一成小写、去掉首尾空白、丢掉空串、去重后排序 ——
    排序不是为了好看：同一个组织两次绑定要给出逐字相同的判断，而集合的遍历
    顺序在报告里不可见。

    ## ``domains`` 与 ``domain_verdict`` 是**一对**，不是两格

    ``domains`` 说「这个账号的域是哪些」，``domain_verdict`` 说「那是怎么知道的」。
    两格一起才是一句话：

    * ``domains`` 非空 + ``confirmed`` —— 我们**证得了**它的官网，跨域邮箱是
      **确证的外来域**，按 SOP L121 拒（:data:`REASON_FOREIGN_DOMAIN`）；
    * ``domains`` 非空 + ``unknown`` —— 只是**有人说过**它的域是什么（一份清单、
      一份登记册、一次观察）。邮箱不在集合里**不构成**外来域 —— 它只说明
      「我们还没证得这个账号的域」，而那不是「这条邮箱不属于它」。

    第二档必须**不拒**，这条判据有代价也有一句理由：把转述当断言用，正是
    §4.2 说的那条错 —— ``discovered_host``（我们取回这一页的那台主机，一次
    观察）曾经就是账号域名的权威，于是社媒页上那个**真实**域名被判成外来域，
    而目录站的主机被当成它自己的域名。两个方向都错，而理由栏里的话听起来都
    完全正当。

    拒与不拒之间没有第三档：不拒的那一条走「条目内证据」那条路，证据不足时落
    :data:`REASON_UNBOUND_EMAIL`（那是 hold/机会，不是丢）。
    """

    account_id: str
    organisation: str
    domains: tuple[str, ...] = ()
    #: 上面的域名集合到哪一步了。**只有 ``confirmed`` 给权威** —— 见类文档。
    #:
    #: 缺省 ``unknown`` 是失败关闭：漏传这一格的调用点买到的是**一次不做判定**
    #: （跨域邮箱不拒、也不按域名强绑），而不是一次默认通过。
    domain_verdict: DomainVerdict = DomainVerdict.UNKNOWN

    def __post_init__(self) -> None:
        # 判据与 ``MarketAccount`` / ``AccountRecord`` 用**同一个**函数：这一层
        # 不能比写入侧松，否则一条路线会挂到一个池子里并不存在的账号 id 上，
        # 而那份报告完全正常。这里只做一次翻译（``ValueError`` 原样上报）。
        check_canonical_id(self.account_id, "account_id")
        if not self.organisation or self.organisation != self.organisation.strip():
            raise ValueError("organisation 不能为空或首尾带空白")
        normalised = tuple(sorted({d.strip().lower() for d in self.domains if d.strip()}))
        object.__setattr__(self, "domains", normalised)

    @classmethod
    def from_account_record(cls, account: AccountRecord) -> "BindingSubject":
        """清单里的一行（``AccountRecord``）→ 绑定主语。

        ``domain`` 是单数且可以为空串，空串在这里变成**空集合** ——
        「这个账号还没查过域名」与「它有一个叫 '' 的域」是两件事，而后者是
        一条伪造的事实。

        ``domain_verdict`` 留 ``unknown``：清单里那一格是**转述**（谁写的那份
        YAML），不是谁证得的，而 ``AccountRecord`` 上没有任何一格说得清它是怎么
        来的。这一条有代价，且必须写在明处：**跨域邮箱从此不会被这一层拒掉**
        （见 :attr:`domain_verdict` 的说明）。补上它的做法是给清单那一格配一个
        验证产出，不是把 ``confirmed`` 写死在这里。
        """
        return cls(
            account_id=account.account_id,
            organisation=account.organisation,
            domains=(account.domain,) if account.domain else (),
        )

    @classmethod
    def from_market_account(cls, account: MarketAccount) -> "BindingSubject":
        """池子里的一行（``MarketAccount``）→ 绑定主语。

        与 :meth:`from_account_record` 同一条：``MarketAccount.domains`` 是一串
        转述（``accounts.remember_accounts`` 收下来的），判定留 ``unknown``。
        """
        return cls(
            account_id=account.account_id,
            organisation=account.organisation,
            domains=tuple(account.domains),
        )

    @classmethod
    def from_dossier(cls, dossier: SubjectDossier) -> "BindingSubject":
        """一件工作冻住的主语（``SubjectDossier``）→ 绑定主语。

        域名那两格**照抄**冻住的那一对（``domains`` / ``domain_verdict``），而
        这一条是 §4.2 那条链路真正承重的地方：受理时如果已经证得了官网，
        ``domains`` 里就是**证得的那个集合**、判定是 ``confirmed``，于是跨域邮箱
        可以被判成确证的外来域（SOP L121）；没有证得时集合里只有转述值、
        判定是 ``unknown``，于是它**不拒**。

        留意的是**它不带 cohort** —— 这正是这一层当初拒绝收 ``AccountRecord``
        的原因，见类文档。
        """
        return cls(
            account_id=dossier.account_id,
            organisation=dossier.organisation,
            domains=dossier.domains or ((dossier.domain,) if dossier.domain else ()),
            domain_verdict=dossier.domain_verdict,
        )


@dataclass(frozen=True)
class RouteVocabulary:
    """一个 profile 的词表，传给绑定层用。

    绑定层**不内建任何业务词**：它不认识 ``grounds``，只认识「词表里说
    ``grounds`` 属于 grounds 这个业务口径」。所以这个对象是
    ``profiles/<profile>/*.yaml`` 编译结果的搬运工。

    空词表是合法的，含义是「这个 profile 什么都没声明」—— 那时角色一律
    ``UNKNOWN``、邮箱一律不判泛化，绑定层产出的路线同样无法定级。
    宁可少判，不要替 profile 编词。
    """

    #: **用于匹配**的全部角色词：正面词与排除词都要在里面，分类由
    #: ``role_classes`` 决定。
    #:
    #: 名字刻意不叫 ``role_terms``：``ProfileBundle.role_terms`` 是「正面词表」，
    #: 两者同名不同义。这里少放一半的后果是 —— 排除类别**永远不可能被命中**，
    #: 于是 ``role_matrix`` 里整份 ``excluded_roles`` 变成死词表，
    #: 而一条写好了的财务排除规则会在每次运行里安静地不生效。
    matchable_role_terms: tuple[str, ...] = ()
    role_classes: Mapping[str, RoleClass] = field(default_factory=dict)
    business_functions: Mapping[str, BusinessFunction] = field(default_factory=dict)
    generic_mailbox_locals: tuple[str, ...] = ()
    business_entry_locals: Mapping[str, BusinessFunction] = field(default_factory=dict)

    @classmethod
    def from_bundle(cls, bundle) -> "RouteVocabulary":
        """从 ``ProfileBundle`` 取词表。

        只读它需要的字段，因此调用方不需要知道 profile 装载的内部结构。

        匹配词表取的是 ``role_classes`` 的**全部键** —— 也就是正面词与排除词的
        并集 —— 而不是 ``bundle.role_terms``。两者不是同一个集合，
        取后者会让排除类别不可达（见 ``matchable_role_terms``）。
        """
        declared = bundle.declared
        return cls(
            matchable_role_terms=tuple(bundle.role_classes),
            role_classes=dict(bundle.role_classes),
            business_functions=dict(bundle.business_functions),
            generic_mailbox_locals=tuple(
                declared.generic_mailbox_locals if declared is not None else ()
            ),
            business_entry_locals=dict(
                declared.business_entry_locals if declared is not None else {}
            ),
        )


@dataclass(frozen=True)
class RejectedBinding:
    """一条被拒绝的绑定。**保留下来**而不是丢弃 —— 它本身就是指标。

    ## 为什么类别是一格枚举，而不是从 ``reason`` 里 ``split`` 出来

    在此之前调用方是按 ``reason.split(" ")[0]`` 分类的（本仓的测试里当时有十一处这种
    写法，2026-09-26 随本枚举的落地一并改成读 :attr:`code` —— ``grep -rn
    'reason.split' tests/`` 现在没有命中）。字符串前缀在**写入侧**看着够用，在读出侧
    是一句没有执行器的约定：有人把详情那一句改成以空格开头、或者把分隔符从
    ``" —— "`` 换成 ``": "``，分类就静默错位，而报告上只是「某几类的数字变了」。

    所以 :attr:`code` 是一个闭集里的取值，:attr:`reason` 只剩「给人读的那一句」。

    ## 几格上下文为什么在这里而不在调用点拼

    ``account_id`` / ``workspace`` / ``source_url`` / ``evidence_ids`` 在这一层**全部
    已知**，而调用点（``application/contact_routes.py``）只知道「有一个主体在跑」。
    把它们留在调用点拼，等于要求每一个调用点各自把同一份事实再取一遍 —— 而取岔了的
    表现是拒收回执指向另一个账号的证据，那是一次虚假归属，不是一次格式问题。

    ## ``account_id`` / ``workspace`` 为什么没有缺省值

    它们俩原本带 ``""`` 缺省。那让这一层少传一个参数就能构造出一条「没有账号的拒收」，
    而它读起来不像错误 —— ``""`` 在本仓的三值语义里是**确证为空**，不是「还不知道」。
    一条落在空账号上的收据会被按账号检索路径原样读出来，于是「这个账号的拒收」比
    真的多几条，而**没有任何一处报错**（收据自己看着完整）。

    所以两格都必填、都过写入侧同一个判据（:func:`~marketops.domain.util.check_canonical_id`）。
    三个构造点本来就都传了，缺省值从来没有被用上过 —— 它只是留了一条不报错的路。
    """

    source_kind: SourceKind
    literal_value: str
    code: RouteRejectionCode
    #: 判据原文（不含类别前缀）。**不是**替它编的一句 —— 编出来的那句在报告里看起来
    #: 像一个业务结论，而原文说的是哪一格、为什么。
    detail: str
    account_id: str
    workspace: str
    source_url: str = ""
    field: ContactField | None = None
    #: 被这一条拒收**实际称量过**的证据行。空的元组是合法取值：来源不合格那一条
    #: 在生成任何证据之前就返回了，那时确实没有依据可指。
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_canonical_id(self.account_id, "account_id")
        check_canonical_id(self.workspace, "workspace")

    @property
    def reason(self) -> str:
        """人读的那一句：``<类别> —— <详情>``。"""
        return f"{self.code.value} —— {self.detail}" if self.detail else self.code.value


@dataclass
class BindResult:
    """一次绑定的产出。

    ``routes`` 与 ``opportunities`` 是**两个**列表，不是一个列表加一个空字段：
    一条路线必须有 literal 邮箱（SOP L126），没有邮箱的那些是尚未解决的
    路线机会。把它们混在一个列表里，下游迟早会忘记检查邮箱是否为空。
    """

    routes: list[ContactRoute] = field(default_factory=list)
    opportunities: list[RouteOpportunity] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    rejected: list[RejectedBinding] = field(default_factory=list)
    #: 文档里出现了混淆写法但没有可用字面邮箱 —— 与「这里没有邮箱」是两回事。
    obfuscated_only: bool = False

    @property
    def false_attribution_risk(self) -> bool:
        return any(route.has_conflict for route in self.routes)


def _make_evidence(
    *,
    workspace: str,
    subject: BindingSubject,
    hit: LiteralHit,
    field: ContactField,
    source_kind: SourceKind,
    source_url: str,
    digest: str,
    fetched_at: datetime,
    source_published_at: datetime | None,
    parser: str,
    http_status: int | None,
    tier: str,
) -> Evidence:
    return Evidence(
        workspace=workspace,
        evidence_id=evidence_id(
            account_id=subject.account_id,
            source_url=source_url,
            field=field,
            literal_value=hit.value,
            digest=digest,
        ),
        account_id=subject.account_id,
        source_url=source_url,
        source_kind=source_kind,
        literal_value=hit.value,
        content_sha256=digest,
        fetched_at=fetched_at,
        source_published_at=source_published_at,
        field=field,
        context=hit.context,
        parser=parser,
        http_status=http_status,
        tier=tier,
    )


def _local_part(email: str) -> str:
    local, _, _ = email.partition("@")
    return local.strip().lower()


def _local_tokens(local: str) -> tuple[str, ...]:
    """把 local part 切成词。``venue.hire`` / ``venue_hire`` / ``venue-hire`` 同解。

    用**词元精确匹配**而不是子串匹配：``sales@`` 里没有业务词，
    ``grounds@`` 里有。子串匹配会让 ``personnel@`` 这种词元里偶然出现的
    片段误判成业务口径，而这是一次静默的错分类。
    """
    for separator in (".", "_", "-", "+"):
        local = local.replace(separator, " ")
    return tuple(token for token in local.split() if token)


def _classify_email(
    email: str, vocabulary: RouteVocabulary
) -> tuple[EntryKind | None, BusinessFunction]:
    """判断这个邮箱是「谁的入口」。返回 ``(entry_kind, business_function)``。

    ``None`` 表示「既不是可识别的业务入口，也不是泛化官方邮箱」——
    是不是具名角色入口取决于同一记录块里有没有岗位证据，因此由调用方决定。
    """
    local = _local_part(email)
    tokens = _local_tokens(local)

    for token in tokens:
        function = vocabulary.business_entry_locals.get(token)
        if function is not None:
            return EntryKind.OFFICIAL_BUSINESS_ENTRY, function

    if local in {item.lower() for item in vocabulary.generic_mailbox_locals}:
        return EntryKind.GENERIC_OFFICIAL, BusinessFunction.UNKNOWN

    return None, BusinessFunction.UNKNOWN


def bind_from_text(
    *,
    workspace: str,
    subject: BindingSubject,
    text: str,
    source_kind: SourceKind,
    source_url: str,
    content_sha256: str,
    fetched_at: datetime,
    vocabulary: RouteVocabulary | None = None,
    parser: str = "",
    http_status: int | None = None,
    tier: str = "",
) -> BindResult:
    """从一段已解析的文本中绑定路线。

    Args:
        workspace: 这一批产出的授权血统（本部署的 Scene，``Settings.profile``）。
            **必填、无默认值** —— 见 ``Evidence.workspace``。绑定这一层只是把它
            从「知道它的那一层」抄到每一个对象上；它不推断、不回落，因为这一层
            没有第二个知道答案的地方（``AccountRecord`` 上没有 Scene 这一维，
            这正是缺口本身）。
        source_kind: 证据来源。``SEARCH_SNIPPET`` 会被**整体拒绝**。
        vocabulary: 来自 profile 的字面词表。缺省是空词表 —— 不猜任何业务词。
    """
    result = BindResult()
    vocabulary = vocabulary or RouteVocabulary()

    email_hits = extract_emails(text)
    name_hits = extract_labelled_names(text)
    role_hits = extract_roles(text, vocabulary.matchable_role_terms)
    obfuscated_hits = extract_obfuscated_emails(text)
    # 发布时刻是**这一页**的性质，不是某一处命中的性质：下面每一行证据都指向
    # 同一份材料（同一个 ``source_url``、同一个 ``content_sha256``），所以它们
    # 带的是同一个值。这也是它没有进 ``evidence_id`` 原像的理由 —— 它由
    # ``content_sha256`` 那份字节决定，同一个 id 配不上两个不同的发布时刻。
    published_at = page_publication_time(text)

    # --- 不变量 1：来源必须合格 ---
    if source_kind not in BINDING_ELIGIBLE_SOURCES:
        attempted: list[tuple[LiteralHit, ContactField]] = [
            *((hit, ContactField.EMAIL) for hit in email_hits),
            *((hit, ContactField.NAME) for hit in name_hits),
            *((hit, ContactField.ROLE) for hit in role_hits),
        ]
        for hit, field_name in attempted:
            result.rejected.append(
                RejectedBinding(
                    source_kind=source_kind,
                    literal_value=hit.value,
                    code=RouteRejectionCode.INELIGIBLE_SOURCE,
                    # 字段名留在详情里（它回答「拒的是什么」），**不**再当类别的一部分：
                    # 类别已经是一格枚举，而 ``ineligible_source:email`` 那种写法会让
                    # 分类与描述住在同一个字符串里。
                    detail=(
                        f"{field_name.value} —— 搜索引擎 snippet 不能证明该值来自"
                        "目标组织的当前公开页面"
                    ),
                    account_id=subject.account_id,
                    workspace=workspace,
                    source_url=source_url,
                    field=field_name,
                )
            )
        return result

    result.obfuscated_only = bool(obfuscated_hits) and not email_hits

    # --- 生成证据 ---
    def _collect(
        hits: list[LiteralHit], field_name: ContactField
    ) -> list[tuple[LiteralHit, Evidence]]:
        """命中与它的证据行**成对**返回。

        不返回 ``list[Evidence]`` 再在别处按下标对齐：归属判据要读命中的 ``offset``
        （见下面「不变量 2」），而 ``Evidence`` 上没有这一格。按下标对齐会把「跳过了
        一条空值命中」这件事变成一次静默的错位 —— 那条路今天走不到，但它是这个写法
        自带的，而不是谁写错了。
        """
        items: list[tuple[LiteralHit, Evidence]] = []
        for hit in hits:
            if not hit.value.strip():
                result.rejected.append(
                    RejectedBinding(
                        source_kind=source_kind,
                        literal_value=hit.value,
                        code=RouteRejectionCode.EMPTY_VALUE,
                        detail=(
                            f"{field_name.value} 这一格命中的字面值是空的 —— 它不是一条"
                            "候选，是一条坏行"
                        ),
                        account_id=subject.account_id,
                        workspace=workspace,
                        source_url=source_url,
                        field=field_name,
                    )
                )
                continue
            evidence = _make_evidence(
                workspace=workspace,
                subject=subject,
                hit=hit,
                field=field_name,
                source_kind=source_kind,
                source_url=source_url,
                digest=content_sha256,
                fetched_at=fetched_at,
                source_published_at=published_at,
                parser=parser,
                http_status=http_status,
                tier=tier,
            )
            items.append((hit, evidence))
            result.evidence.append(evidence)
        return items

    email_pairs = _collect(email_hits, ContactField.EMAIL)
    name_pairs = _collect(name_hits, ContactField.NAME)
    role_pairs = _collect(role_hits, ContactField.ROLE)
    email_evidence = [evidence for _, evidence in email_pairs]
    name_evidence = [evidence for _, evidence in name_pairs]
    role_evidence = [evidence for _, evidence in role_pairs]

    # --- 不变量 2：保守归属 ---
    # 多个邮箱 + 存在姓名/角色 → 无法判断对应关系，标记冲突。
    ambiguous = len(email_evidence) > 1 and bool(name_evidence or role_evidence)

    # --- 不变量 3：没有邮箱就不是路线（SOP L126）---
    if not email_evidence:
        if role_evidence or name_evidence:
            # 具名角色/姓名证据是有的，缺的只是邮箱。这正是 SOP 里
            # 「有场地与责任关系证据但没有邮箱」那一类（L105 的 P3）。
            # 它不该被丢掉，也不该被标成 B —— 它是一条未解决的路线机会。
            result.opportunities.extend(
                _build_opportunities(
                    workspace=workspace,
                    subject=subject,
                    vocabulary=vocabulary,
                    name_evidence=name_evidence,
                    role_evidence=role_evidence,
                )
            )
        return result

    for email_hit, evidence in email_pairs:
        # 只有一个邮箱时，姓名/角色才可能属于它；否则不归属。
        single = len(email_pairs) == 1

        # --- 不变量 4：归属要看位置（§9）---
        # 先按**条目窗口**筛出跟这条邮箱印在一起的那几处，再要求「各自唯一」。
        # 顺序不能反：先按整页唯一筛、再看位置，等于把「这一页只有一个名字」
        # 当成「这个名字就是这条邮箱的人」—— 而那是两件事（§9 逐字禁止的缝法）。
        #
        # 窗口的判据在 ``extract.in_the_same_entry``，内含「同一条或紧邻的一条」。
        # 这里**不**对多个邮箱那一支放宽：多个邮箱时谁是谁跟位置无关，仍然不归属。
        def _bound(pairs: list[tuple[LiteralHit, Evidence]]) -> list[Evidence]:
            if not single:
                return []
            return [
                item
                for hit, item in pairs
                if in_the_same_entry(text, email_hit.offset, hit.offset)
            ]

        window_names = _bound(name_pairs)
        window_roles = _bound(role_pairs)

        route, reject_reason = _build_route(
            workspace=workspace,
            subject=subject,
            vocabulary=vocabulary,
            email=evidence.literal_value,
            email_evidence=[evidence],
            name_evidence=window_names if len(window_names) == 1 else [],
            role_evidence=window_roles if len(window_roles) == 1 else [],
            ambiguous=ambiguous if not single else False,
            # 「这一页上有人，但不在这条邮箱的卡片上」与「这一页上没有人」是两句
            # 不同的话（见 ``REASON_ENTRY_ELSEWHERE``）。这一格只在拒绝时用得上，
            # 所以取的是**筛唯一之前**的窗口结果：条目里有两处具名时那是另一种
            # 说不清（``REASON_UNBOUND_EMAIL``），不是「人在别处」。
            entry_elsewhere=bool(name_pairs or role_pairs)
            and not (window_names or window_roles),
            # 这一页上有多个邮箱、同时还有具名/岗位 ⇒ 谁是谁配不上号。它必须在
            # 「人在别处」之前被认出来（见 ``_build_route`` 里那段）—— 两个都返回
            # ``None``，所以拒收集合不变，变的是**报告里那一句话说的是不是实话**。
            conflicting=ambiguous and not single,
        )
        if route is None:
            code, detail = reject_reason
            result.rejected.append(
                RejectedBinding(
                    source_kind=source_kind,
                    literal_value=evidence.literal_value,
                    code=code,
                    detail=detail,
                    account_id=subject.account_id,
                    workspace=workspace,
                    source_url=source_url,
                    field=ContactField.EMAIL,
                    # 称量过的那些行：这条候选邮箱自己的，加上这一页上被拿来比对归属的
                    # 具名/岗位。后者只在与归属有关的三个类别里才进得来 —— 域名不符
                    # 那一条跟这一页有几个人没有关系，把它们列进去会让人以为它们
                    # 也参与了那个判定。
                    evidence_ids=_rejection_evidence(
                        code=code,
                        email_evidence=evidence,
                        name_pairs=name_pairs,
                        role_pairs=role_pairs,
                    ),
                )
            )
            continue
        result.routes.append(route)

    return result


def _route_identity(
    subject: BindingSubject,
    vocabulary: RouteVocabulary,
    name_evidence: list[Evidence],
    role_evidence: list[Evidence],
) -> tuple[Person | None, Role | None, str, str, RoleClass, BusinessFunction]:
    """把姓名/岗位证据整理成 ``Person`` / ``Role`` 与它们的字面值、分类。

    分类来自 profile 的词表：词表说 ``grounds supervisor`` 是 operational，
    绑定层就照抄 operational。词表里没有的词 → ``UNKNOWN`` —— 不是猜，
    是「profile 没声明」。
    """
    person = (
        Person.for_account(account_id=subject.account_id, full_name=name_evidence[0].literal_value)
        if name_evidence
        else None
    )
    role_title = role_evidence[0].literal_value if role_evidence else ""
    role_class = RoleClass.UNKNOWN
    business_function = BusinessFunction.UNKNOWN
    if role_evidence:
        key = role_title.strip().lower()
        role_class = vocabulary.role_classes.get(key, RoleClass.UNKNOWN)
        business_function = vocabulary.business_functions.get(key, BusinessFunction.UNKNOWN)
    role = (
        Role.for_account(
            account_id=subject.account_id,
            title=role_title,
            role_class=role_class,
            business_function=business_function,
        )
        if role_evidence
        else None
    )
    return (
        person,
        role,
        person.full_name if person else "",
        role_title,
        role_class,
        business_function,
    )


def _evidence_ids(*groups: list[Evidence]) -> list[str]:
    return [item.evidence_id for group in groups for item in group]


#: 哪些拒收类别「称量过」这一页上的具名与岗位。
#:
#: ``FOREIGN_DOMAIN`` **不在**里面，而这不是遗漏：那一条判的是**域**，与这一页上有
#: 几个人没有关系。把那些行列进去，读的人会以为它们也参与了那个判定 —— 而拒收回执的
#: 全部价值就是「依据还在，去看那几条」。
_ATTRIBUTION_REJECTIONS = frozenset(
    {
        RouteRejectionCode.ENTRY_ELSEWHERE,
        RouteRejectionCode.UNBOUND_EMAIL,
        RouteRejectionCode.CONFLICTING_EVIDENCE,
    }
)


def _rejection_evidence(
    *,
    code: RouteRejectionCode,
    email_evidence: Evidence,
    name_pairs: list[tuple[LiteralHit, Evidence]],
    role_pairs: list[tuple[LiteralHit, Evidence]],
) -> tuple[str, ...]:
    """这条拒收**实际称量过**的证据行。

    候选邮箱自己那一行一定在里面（它就是被拒的那一条）。具名与岗位只在归属类的拒收里
    进来，理由见上面那个闭集。
    """
    ids = [email_evidence.evidence_id]
    if code in _ATTRIBUTION_REJECTIONS:
        ids.extend(item.evidence_id for _, item in (*name_pairs, *role_pairs))
    return tuple(ids)


def _build_route(
    *,
    workspace: str,
    subject: BindingSubject,
    vocabulary: RouteVocabulary,
    email: str,
    email_evidence: list[Evidence],
    name_evidence: list[Evidence],
    role_evidence: list[Evidence],
    ambiguous: bool,
    entry_elsewhere: bool = False,
    conflicting: bool = False,
) -> tuple[ContactRoute | None, tuple[RouteRejectionCode, str] | None]:
    """构造一条路线。

    ``name_evidence`` / ``role_evidence`` 必须**已经**是条目窗口内筛过的
    （调用点的事，见 §9 的不变量 4）—— 本函数据此判断「人和邮箱是不是一张卡片上的」，
    而它不做这个筛选：窗口要读到命中的 ``offset``，那个值不在 ``Evidence`` 上。

    返回 ``(None, (类别, 判据原文))`` 有四种情况，它们**不是**「坏数据」：

    * ``foreign_domain`` —— 邮箱域与**已证得**的账号域名不符（SOP L121）；
    * ``conflicting_evidence`` —— 这一页上有多个邮箱、同时还有具名/岗位，谁是谁配不上号。
      它排在 ``entry_elsewhere`` **之前**判：那一档里「人在别处」与「这一页没有联系人」
      两句**都不是实话**，而按顺序落到它们任何一句上，报告里说出的都是一句听起来完全
      正当的假话（拒收集合本身不变 —— 三个类别都返回 ``None``）；
    * ``entry_elsewhere`` —— 这一页上有具名/岗位，但没有一条落在这条邮箱的条目窗口里。
      §9 要的正是这一档不要被缝成路线：**同页不等于同一个人**；
    * ``unbound_email`` —— 条目窗口里没有唯一的一处具名/岗位（一处都没有，或者不止
      一处），也不是可识别的业务入口或泛化官方邮箱。它**还不足以成为一条路线**：
      SOP L125–L129 的每一级都要求某种归属，而这条邮箱目前没有可确认的归属。
      证据仍然全部保留在 ``BindResult.evidence`` 里，只是不产出路线。
    """
    normalised = normalise_email(email)
    email_domain = normalised.rpartition("@")[2].lower()

    # --- 不变量 4：不归属外来域。**只有证得过的账号域才判得了「外来」。** ---
    #
    # 这一条是 2026-09-26 审计的直接产物，改法与理由是同一件事：判「外来」
    # 需要一个**权威的**账号域名，而 ``domains`` 非空**不等于**权威 ——
    # 它可能只是有人说过（清单、登记册、一次观察）。把转述当断言用，两个方向
    # 都会错：社媒页上那个真实域名会被判成外来域（真邮箱被拒），而目录站的
    # 主机被当成它自己的域名（别人的邮箱被收）。两份错误在报告里都长得正常。
    #
    # 所以：``confirmed`` 才拒。``unknown`` 的那一条**落到下面那条路上** ——
    # 它要的是条目内证据，证据不足时落 ``unbound_email``（hold/机会，不是丢）。
    # 「不得直接拒绝真实跨域邮箱」与「不得直接接受它」这两句在这里是同一行代码。
    if (
        subject.domains
        and email_domain
        and email_domain not in subject.domains
        and subject.domain_verdict is DomainVerdict.CONFIRMED
    ):
        return None, (
            RouteRejectionCode.FOREIGN_DOMAIN,
            "邮箱域与账号域名不符，SOP L121：同页出现的其他组织邮箱不得归属目标账户",
        )

    person, role, person_name, role_title, role_class, role_function = _route_identity(
        subject, vocabulary, name_evidence, role_evidence
    )

    entry_kind, entry_function = _classify_email(normalised, vocabulary)
    business_function = entry_function if entry_function is not BusinessFunction.UNKNOWN else role_function

    # 「人和邮箱在一张卡片上」这件事**由调用点的窗口筛过**：走到这里还带着姓名/岗位，
    # 说明它们与这条邮箱印在一起（``extract.in_the_same_entry``）。所以这个布尔量是
    # 一**结论**而不是一个猜测 —— 它此前是 ``has_same_block``，而那时没有任何一处
    # 计算过「块」。
    has_bound_entry = bool(person_name or role_title)
    if role_class is RoleClass.EXCLUDED:
        # SOP L129/L131：这个**岗位类别**不得进入有效主池。
        # 类别判定优先于入口判定：一个写着「Finance」的 info@ 仍然是财务入口。
        entry_kind = EntryKind.EXCLUDED
    elif entry_kind is None:
        if not has_bound_entry:
            if conflicting:
                return None, (
                    RouteRejectionCode.CONFLICTING_EVIDENCE,
                    "这一页上有多个邮箱，同时还有具名或岗位 —— 谁是谁配不上号。"
                    "SOP L121 与 §9 都要求归属可确认，而这里连「这条邮箱属于哪一处"
                    "具名」都定不下来。证据已保留：两条候选与那几处具名都还读得出来。",
                )
            if entry_elsewhere:
                return None, (
                    RouteRejectionCode.ENTRY_ELSEWHERE,
                    "这一页上有具名或岗位，但没有一条"
                    "与这条邮箱印在同一张卡片的条目窗口里（§9：不得把 A 处的姓名"
                    "配在 B 处的邮箱上）。证据已保留，两处都还读得出来。",
                )
            return None, (
                RouteRejectionCode.UNBOUND_EMAIL,
                "该邮箱所在的条目里没有唯一的一处"
                "岗位或姓名（一处都没有，或者不止一处），也不是业务口径入口或泛化"
                "官方邮箱，因此还不足以构成一条路线"
                "（SOP L125–L129 的每一级都要求归属）。证据已保留。",
            )
        entry_kind = EntryKind.NAMED_ROLE

    # 绑定强度：条目内具名 + 域名一致才是强绑定（SOP L125/L126 都要求强绑定）。
    #
    # 「域名一致」这一半在这里**不看判定**（``verified`` 与 ``claimed`` 都算）：
    # 一个不在集合里的域已经在上面被拒了（证得过时）或者根本没走到这一句
    # （没证过时），所以这一句判的是「邮箱域**是**这个账号的域之一」——
    # 那一句在两种来路下都成立，只是来路不同。把 ``claimed`` 降成非强绑定
    # 会把一整批**真**邮箱推下一级，而 §4.2 要的是别把**别人的**邮箱收进来，
    # 不是别把真的收进来。
    if has_bound_entry and (not subject.domains or email_domain in subject.domains):
        binding = RouteBinding.STRONG
    else:
        binding = RouteBinding.ACCOUNT

    block_priority = _block_priority(
        entry_kind=entry_kind,
        has_person=bool(person_name),
        has_role=bool(role_title),
    )

    moments = moments_from_evidence([*email_evidence, *name_evidence, *role_evidence])

    return (
        ContactRoute(
            workspace=workspace,
            account_id=subject.account_id,
            organisation=subject.organisation,
            person_id=person.person_id if person else "",
            person_name=person_name,
            role_id=role.role_id if role else "",
            role_title=role_title,
            role_class=role_class,
            business_function=business_function,
            entry_kind=entry_kind,
            block_priority=block_priority,
            binding=binding,
            email=normalised,
            email_domain=email_domain,
            evidence_ids=_evidence_ids(email_evidence, name_evidence, role_evidence),
            # grading 尚未运行；先落到「未判定」，由 grading engine 改写。
            grade=Grade.X,
            grade_reason=(
                GradeReason.CONFLICTING_EVIDENCE if ambiguous else GradeReason.RULES_UNKNOWN
            ),
            has_conflict=ambiguous,
            # §9 的两个时刻由**这一行手上那几行证据**折出来，不在这里另算一遍：
            # 取回时刻取最早那次、发布时刻分歧时留空，判据都在
            # ``evidence.moments_from_evidence``。``last_verified_at`` / ``valid_from``
            # / ``valid_to`` 不填 —— 这一层没有它们的信息，而「重新抓了一次」不是
            # 「重新验证过」（``domain/temporal.py`` 的整段理由）。
            first_seen_at=moments.first_seen_at,
            source_published_at=moments.source_published_at,
        ),
        None,
    )


def _block_priority(
    *, entry_kind: EntryKind, has_person: bool, has_role: bool
) -> RecordBlockPriority | None:
    """SOP L105 的记录块绑定层级。

    P2 的原文是「相关**岗位或部门**与邮箱绑定」，所以一个 ``grounds@`` 部门邮箱
    也算 P2 —— 它没有具名人，但它绑定在一个明确的业务口径上。少了这半句，
    业务入口邮箱会落到 ``None``，而那些邮箱恰恰是 SOP 明确定义过的一类。

    ``None`` 仍然是一个**有含义**的取值：这个人/岗位/邮箱的组合不属于 SOP 定义过的
    任何一级（既没有同块的具名人或岗位，也不是可识别的官方入口）。把它强行读成
    P1 或 P4 会让「这一级是什么」变成实现细节；留成 ``None``，规则表会因
    没有等级匹配而 fail closed，读的人看到的是「SOP 没定义这一级」。
    """
    if entry_kind is EntryKind.GENERIC_OFFICIAL:
        return RecordBlockPriority.P4
    if has_person and has_role:
        return RecordBlockPriority.P1
    if has_role or entry_kind is EntryKind.OFFICIAL_BUSINESS_ENTRY:
        return RecordBlockPriority.P2
    return None


def _build_opportunities(
    *,
    workspace: str,
    subject: BindingSubject,
    vocabulary: RouteVocabulary,
    name_evidence: list[Evidence],
    role_evidence: list[Evidence],
) -> list[RouteOpportunity]:
    """把「有具名/岗位证据、没有邮箱」的命中整理成**一条或多条**机会。

    ## 两边各自唯一时才合并

    合并说的是「这一处姓名就处在这个岗位上」。没有邮箱时它仍然是一个**归属**判断，
    所以判据与路线那一侧逐字相同（``bind.py`` 上面那句「只有一个邮箱时，姓名/角色
    才可能属于它」）：**只有两边各自唯一时才敢说它们互相属于**。

    只要有一边不唯一，就不合并，每一处命中各得一条机会。这不是把一条拆成多条
    凑数 —— 它说的是「我们知道这一页上有这两个人、这一个岗位，也知道我们**不知道**
    谁在哪个岗位上」。

    ## 这里此前用的是路线那一侧的 ``len(...) == 1`` 过滤，而它有两个后果都不成立

    上一版把 ``name_evidence if len(name_evidence) == 1 else []`` 照抄过来，
    于是：

    * 两条姓名 + 一条岗位 → 岗位那一条**在读数上消失**（证据行还在，但
      「这一页写了 Grounds Manager」这句话再也读不出来）；
    * 两条姓名 + 没有岗位 → 两边**都是空列表**，而 :class:`RouteOpportunity`
      要求至少有一样 —— 构造当场抛 ``ValidationError``，整次绑定崩掉。

    触发这两条路各只需要一件事：**姓名或岗位那一侧有两处命中**。多命中不需要稀奇页面
    （一页列两个名字就够，``tests/test_bind.py`` 那条判据用的就是两行 ``<p>``），
    而「没有邮箱」不是边缘分支 —— 它是本文件第二条不变量的正面情形，也是 P3 的定义。
    所以一次构造期异常与一条正常产出之间隔着的是一整类页面；而一个从这里抛出去的
    ``ValidationError`` 让调用方连**已经落盘的证据与这一页的字面命中**都读不到，
    那正是本仓要求「失败是一个结果」的理由。

    （那条 ``len(...) == 1`` 过滤要回答的是「多个邮箱时姓名归谁」；没有邮箱时
    **没有任何东西可供归属**，同一条规则搬过来就只剩上面这两个后果。）
    """
    if len(name_evidence) <= 1 and len(role_evidence) <= 1:
        return [
            _build_opportunity(
                workspace=workspace,
                subject=subject,
                vocabulary=vocabulary,
                name_evidence=name_evidence,
                role_evidence=role_evidence,
            )
        ]
    separate: list[RouteOpportunity] = []
    for hit in name_evidence:
        separate.append(
            _build_opportunity(
                workspace=workspace,
                subject=subject,
                vocabulary=vocabulary,
                name_evidence=[hit],
                role_evidence=[],
            )
        )
    for hit in role_evidence:
        separate.append(
            _build_opportunity(
                workspace=workspace,
                subject=subject,
                vocabulary=vocabulary,
                name_evidence=[],
                role_evidence=[hit],
            )
        )
    return separate


def _build_opportunity(
    *,
    workspace: str,
    subject: BindingSubject,
    vocabulary: RouteVocabulary,
    name_evidence: list[Evidence],
    role_evidence: list[Evidence],
) -> RouteOpportunity:
    """构造一条未解决的路线机会（有具名角色，没有 literal 邮箱）。

    ``block_priority`` 固定在 P3，因为 P3 的定义就是这件事
    （SOP L105：「场地和责任关系证据但无邮箱」）。

    调用方**已经**保证了 ``name_evidence`` / ``role_evidence`` 里至多各一条
    （见 :func:`_build_opportunities`）—— 那一条是归属判据，不是本函数的前置
    校验，所以这里不重复断言：一个在这里再挡一次的检查会让「谁负责不许合并」
    有两个答案。
    """
    person, role, person_name, role_title, role_class, role_function = _route_identity(
        subject, vocabulary, name_evidence, role_evidence
    )
    # 与路线那一侧同一条判据、同一个函数：机会也会过期（那个人可能已经离任，或者
    # 已经有邮箱了），而「什么时候看到的」与「来源说它是什么时候的」两件事与路线
    # 逐字相同。
    moments = moments_from_evidence([*name_evidence, *role_evidence])
    return RouteOpportunity(
        workspace=workspace,
        account_id=subject.account_id,
        organisation=subject.organisation,
        person_id=person.person_id if person else "",
        person_name=person_name,
        role_id=role.role_id if role else "",
        role_title=role_title,
        role_class=role_class,
        business_function=role_function,
        block_priority=RecordBlockPriority.P3,
        evidence_ids=_evidence_ids(name_evidence, role_evidence),
        first_seen_at=moments.first_seen_at,
        source_published_at=moments.source_published_at,
    )


__all__ = [
    "BindResult",
    "BindingSubject",
    "RejectedBinding",
    "RouteVocabulary",
    "bind_from_text",
    "REASON_INELIGIBLE_SOURCE",
    "REASON_EMPTY_VALUE",
    "REASON_FOREIGN_DOMAIN",
    "REASON_UNBOUND_EMAIL",
    "REASON_ENTRY_ELSEWHERE",
]

"""跨模块共享的枚举。

放在包顶层而非各子包内，是因为 search / acquisition / grading / evidence 都要引用
同一套取值。重复定义会产生「同一概念两套字面量」的迁移债 —— 那正是跨平台重构时
最容易出错的地方。
"""

from __future__ import annotations

from enum import Enum


class FetchOutcome(str, Enum):
    """一次抓取的终态。

    这**同时**是 browser 升级判定的唯一依据（见 ``application/escalation.py``）。
    把判定收敛到这一个枚举上，是为了让「HTTP 成功就不要启动浏览器」和
    「明确受阻就不要硬闯」成为可单测的断言，而不是散落在调用链里的 if。
    """

    #: HTTP 已拿到完整可用内容 —— 不得启动浏览器。
    OK = "ok"
    #: 页面依赖 JavaScript 才能呈现内容 —— 可升级 browser。
    JS_REQUIRED = "js_required"
    #: 骨架页：返回 200 但正文为空壳（典型 SPA）—— 可升级 browser。
    EMPTY_SHELL = "empty_shell"
    #: 浏览器渲染本身失败（超时 / 崩溃 / 渲染后仍为空）—— 可再试一次 browser。
    RENDER_FAILURE = "render_failure"
    #: 无法正常公开访问 —— **终态，禁止绕过**。
    BLOCKED = "blocked"
    #: 资源不存在（404/410）—— 终态，不升级、不重试。
    NOT_FOUND = "not_found"
    #: 传输层超时 —— 是传输问题，不是渲染问题；不构成启动浏览器的理由。
    TIMEOUT = "timeout"
    #: 其他传输 / 协议错误 —— 同上。
    ERROR = "error"


#: 只有这些终态允许升级到 BrowserFetcher。
BROWSER_ESCALATION_OUTCOMES: frozenset[FetchOutcome] = frozenset(
    {FetchOutcome.JS_REQUIRED, FetchOutcome.EMPTY_SHELL, FetchOutcome.RENDER_FAILURE}
)

#: 这些终态一旦出现就结束该 URL 的处理（不升级、不重试）。
TERMINAL_OUTCOMES: frozenset[FetchOutcome] = frozenset(
    {FetchOutcome.OK, FetchOutcome.BLOCKED, FetchOutcome.NOT_FOUND}
)


class FetchTier(str, Enum):
    """抓取层级。用于审计「浏览器秒数」从哪来。"""

    HTTP = "http"
    BROWSER = "browser"


class QueryOutcome(str, Enum):
    """一次检索的四种终态（见 ``application/billed_search.py``）。

    「没发送」在这里是**三**个成员而不是一个 ``False``，因为三者的处置不同：
    ``SKIPPED`` 是正常的（这条查询在更早的一次运行里已经付过账，重跑时必然
    出现），``EXHAUSTED`` 要停下来并报告，``FAILED`` 要继续下一条。合成一个
    布尔值，报告上「预算耗尽」与「这条以前跑过」会是同一种颜色 —— 而前者是
    一次需要处置的运行中止。
    """

    #: 真的发出去了，且已经结算。
    SENT = "sent"
    #: 这条查询付过账，本次**没有**重发。
    SKIPPED = "skipped_already_paid"
    #: 预留撞穿了上限 —— 这次调用根本没发生。
    EXHAUSTED = "budget_exhausted"
    #: 调用失败。额度**仍然扣着**（见 ``ports/budget.py``）。
    FAILED = "failed"


class SourceKind(str, Enum):
    """证据来源层级。决定它**能否**成为 Contact 的事实依据。"""

    #: 搜索引擎结果页的 snippet。可能来自缓存 / 聚合站 / 竞品站点，
    #: 不能证明邮箱属于目标组织的当前公开页面 —— 永远不可绑定。
    SEARCH_SNIPPET = "search_snippet"
    FETCHED_HTML = "fetched_html"
    FETCHED_PDF = "fetched_pdf"
    FETCHED_TEXT = "fetched_text"


#: 只有「真实抓到并解析过的公开文档」可以作为绑定依据。
BINDING_ELIGIBLE_SOURCES: frozenset[SourceKind] = frozenset(
    {SourceKind.FETCHED_HTML, SourceKind.FETCHED_PDF, SourceKind.FETCHED_TEXT}
)


class ContactField(str, Enum):
    """Contact 上可以被证据支持的最小字段集。"""

    ORGANISATION = "organisation"
    NAME = "name"
    ROLE = "role"
    EMAIL = "email"


class Cohort(str, Enum):
    """§10 第一条 Demo 的三类账号。"""

    KNOWN_SUCCESS = "known_success"
    ACCESSIBLE_NO_CORE_EMAIL = "accessible_no_core_email"
    PREVIOUSLY_BLOCKED = "previously_blocked"


class AccountStatus(str, Enum):
    """**账号**的准入状态 —— SOP v1 L188 左侧状态机。

    账号的单位是「拥有 / 运营 / 维护 / 预约控制 / 采购某个场地资产的责任实体」，
    不是单个 pitch，也不是一个场地名。因此它与 :class:`Grade` **正交**：

    * 本枚举回答「这个组织值不值得研究」；
    * :class:`Grade` 回答「已经拿到的那条联系路线属于哪一级」。

    把两者合成一个枚举，就没法表达「账号 eligible，但已经拿到的路线是 X」
    这种真实且常见的情形（组织对，人不对）。合并不省事，只是把信息删掉。
    """

    DISCOVERED = "discovered"
    ELIGIBLE = "eligible"
    HOLD = "hold"
    IRRELEVANT = "irrelevant"
    DUPLICATE = "duplicate"
    RESEARCHED = "researched"
    ARCHIVED = "archived"


class RouteStatus(str, Enum):
    """**联系路线**的生命周期 —— SOP v1 L188 右侧状态机。

    SOP 把 A/B/C/D/X 画成这条链上的一步。本仓库**不**把 A/B/C/D/X 复制到这里，
    而是让 ``route.grade`` 承载它：一份枚举表描述两个轴，就会有一处先漂移。
    因此这条链读作::

        observed →（grade：A/B/C/D/X）→ queued_candidate → prepared_pending_authorization
                 → sent_stage_1..4 → reply / suppressed / bounced / manual_review / closed

    本阶段的状态断言只有一条：``send_authorized`` 恒为 False，
    因此 :attr:`SENT_STAGE_1` 及其之后的取值**不可能**出现（见 contracts/）。
    """

    OBSERVED = "observed"
    QUEUED_CANDIDATE = "queued_candidate"
    PREPARED_PENDING_AUTHORIZATION = "prepared_pending_authorization"
    SENT_STAGE_1 = "sent_stage_1"
    SENT_STAGE_2 = "sent_stage_2"
    SENT_STAGE_3 = "sent_stage_3"
    SENT_STAGE_4 = "sent_stage_4"
    REPLY = "reply"
    SUPPRESSED = "suppressed"
    BOUNCED = "bounced"
    MANUAL_REVIEW = "manual_review"
    CLOSED = "closed"


#: 本阶段（``send_authorized=false``）不可能到达的状态。任何产出这些状态的路径
#: 都意味着发送链被接上了 —— 那正是 tests/test_models_invariants.py 要拦的事。
UNREACHABLE_WITHOUT_AUTHORIZATION: frozenset[RouteStatus] = frozenset(
    {
        RouteStatus.SENT_STAGE_1,
        RouteStatus.SENT_STAGE_2,
        RouteStatus.SENT_STAGE_3,
        RouteStatus.SENT_STAGE_4,
        RouteStatus.REPLY,
        RouteStatus.BOUNCED,
    }
)


class RoleClass(str, Enum):
    """一个岗位在**路线**里扮什么角色。

    取值与 ``role-matrix.yaml`` 的 ``tier`` 逐字相同 —— 业务侧只声明一次，
    profile 装载层把 term → class 编译出来，引擎只做集合判定。

    :attr:`EXCLUDED` 不是「降权」，是「不得进入有效路线」：SOP v1 L129 把财务
    放进 X，L131 把无场地关联的客服/预订放进 X，招生 / HR / 教学 / 一般行政同理。
    这是一条**类别**判定，不是权重判定 —— 权重可以被下一次调参改回去。
    """

    DECISION = "decision"
    INFLUENCER = "influencer"
    OPERATIONAL = "operational"
    GATEKEEPER = "gatekeeper"
    EXCLUDED = "excluded"
    UNKNOWN = "unknown"


class BusinessFunction(str, Enum):
    """一条路线落在哪个业务口径上。

    SOP v1 L131：``bookings`` 只有在**明确是场地预约 / 场馆运营 / 设施团队入口**时
    才算业务入口；与场地无关的客服 / 预订走 :attr:`CUSTOMER_SERVICE` → X。
    因此 ``bookings`` 与 ``customer_service`` 是**两个**取值，不是一个。
    """

    GROUNDS = "grounds"
    FACILITIES = "facilities"
    ESTATES = "estates"
    VENUE = "venue"
    OPERATIONS = "operations"
    MAINTENANCE = "maintenance"
    BOOKINGS = "bookings"
    PROCUREMENT = "procurement"
    FINANCE = "finance"
    ADMISSIONS = "admissions"
    HR = "hr"
    TEACHING = "teaching"
    ADMINISTRATION = "administration"
    CUSTOMER_SERVICE = "customer_service"
    UNKNOWN = "unknown"


class VenueRelation(str, Enum):
    """责任实体与场地资产之间的关系（SOP v1 L78）。

    ``UNRESOLVED`` 是第六个成员，而它与其他五个**不是同一类取值**：那五个说的是
    「已经判定出来的关系是哪种」，它说的是「这两者之间有关系，但关系还没定下来」。
    把这一维并进枚举，是因为它必须**和那五个共用同一个槽位** —— 一块场地与一个组织
    之间真正的关系只能有一个判定结果，分成两张表或两个字段的话，「两者都有值」
    就成了一种可表达的坏状态，而它读起来像一个额外的发现。

    它与「没有这一行」也**不是**同一件事：没有行 = 不知道这两个东西有没有关系；
    ``UNRESOLVED`` = 知道它们有关系（例如组织页上写着它在管这块场地），但管的是
    维护还是预约控制没读出来。这两者的下一步不同（一个是去查，一个是去读已经
    拿到的那一页），所以不许悄悄合并成「没查过」。

    证据的要求对 ``UNRESOLVED`` 与对其余五个**一样严**：说得出「有关系」也是一个
    要凭据的判定，而它比其余五个更容易滑向「反正是个组织，先连上再说」。
    """

    OWNS = "owns"
    OPERATES = "operates"
    MAINTAINS = "maintains"
    BOOKING_CONTROL = "booking_control"
    PROCURES = "procures"
    UNRESOLVED = "unresolved"


class DomainVerdict(str, Enum):
    """一块场地/资产的**官网问题**被查到了哪一步。

    三值，因为可行的答案有三个，而其中两个今天在盘上长得一样：

    * ``UNKNOWN`` —— **还没查**。这一格不许被读成「没有官网」：没查过的东西
      排除掉，是把「我们没做」写成「它不存在」。
    * ``CONFIRMED`` —— 查到了，某个域名是这个组织的官网（要证据）。
    * ``ABSENT`` —— **查过，确实没有**（要证据）。它与 ``UNKNOWN`` 的区别正是
      「确认不存在」与「没查」的区别；这一格存在的理由就是不让前者冒充后者。

    枚举里没有第四个「空」成员：空串不是这个问题的答案，它是**另一个字段的缺省**
    （:attr:`~marketops.domain.entities.VenueAsset.verified_official_domain` 的 ``""``），
    而把空串当作一个判定结果，就是本仓反复写下的那台「伪造证据的机器」。
    """

    UNKNOWN = "unknown"
    CONFIRMED = "confirmed"
    ABSENT = "absent"


class RouteBinding(str, Enum):
    """一条路线与目标账号之间的绑定强度。"""

    #: 邮箱与具名角色/姓名在**同一记录块**里，且邮箱域与账号一致。
    STRONG = "strong"
    #: 邮箱出现在账号自己的公开页面上，但没有同块具名角色。
    ACCOUNT = "account"
    #: 无法证明归属 —— 例如同页出现的其他组织目录邮箱。
    NONE = "none"


class EntryKind(str, Enum):
    """这条路线是「谁」的入口。A/B 与 C/D 的分界正是它。"""

    #: 具名角色 / 具名人物的入口（A、B）。
    NAMED_ROLE = "named_role"
    #: 与场地业务口径绑定的官方入口（C）。
    OFFICIAL_BUSINESS_ENTRY = "official_business_entry"
    #: 公开真实的泛化官方邮箱（D）。
    GENERIC_OFFICIAL = "generic_official"
    #: 不相关岗位 / 无场地绑定的泛化入口 —— 不得进入有效主池（X）。
    EXCLUDED = "excluded"


class RecordBlockPriority(str, Enum):
    """P1–P4 是**记录块绑定语义**，不是 URL 路径分级（SOP v1 L105）。

    旧 profile 把 ``*/contact*`` / ``*/staff*`` 这类 URL glob 叫作 P1/P2/P3 ——
    那是把「先抓哪个页面」和「邮箱怎么被绑定」两件事挤进了同一个词。
    URL 侧已改名为 ``page_discovery_hints``；这里恢复 P1–P4 的原义。
    """

    #: 相关人 / 岗位 / 邮箱在同一 record block 内。
    P1 = "P1"
    #: 相关岗位或部门与邮箱绑定（同块，但无具名人）。
    P2 = "P2"
    #: 有场地与责任关系证据，**但没有邮箱** —— 所以它只可能出现在
    #: :class:`~marketops.domain.entities.RouteOpportunity` 上，不会出现在路线上。
    P3 = "P3"
    #: 官方通用 contact / booking 兜底，每账号最多一条。
    P4 = "P4"


class Grade(str, Enum):
    """A/B/C/D/X 是**不透明标签**，语义由 profiles 提供，代码不内建含义。

    **定级主语固定是 ``ContactRoute``，不是账号，也不是 ``Person``。**
    SOP v1 L125–L129 的五条定义里，每一条的主语都是「一条路线」：
    关键决策职能 **+** 真实公开业务邮箱 **+** 强绑定。账号有账号的状态机
    （:class:`AccountStatus`），人可以有多条路线，等级只挂在路线上。

    引擎在规则值为 UNKNOWN 时返回 ``X`` 并附原因码，而不是猜一个。
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    #: 未判定。规则未知 / 无合格证据 / 证据冲突 / 不相关岗位都落到这里。
    X = "X"


class GradeReason(str, Enum):
    """为什么得到这个 Grade。审计时比 Grade 本身更重要。"""

    RULE_MATCHED = "rule_matched"
    RULES_UNKNOWN = "rules_unknown"
    NO_ELIGIBLE_EVIDENCE = "no_eligible_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    #: 命中 X 类别（财务 / 招生 / HR / 教学 / 一般行政 / 无场地绑定的客服预订）。
    #: 与 NO_ELIGIBLE_EVIDENCE 分开：那是「没拿到证据」，这是「拿到了，但不该联系」。
    EXCLUDED_ENTRY = "excluded_entry"


class EligibilityReason(str, Enum):
    """为什么账号停在某个 :class:`AccountStatus`。

    :attr:`UNRESOLVED_FACTS` 是本仓当前的真实状态：SOP 的排除规则引用的是
    ``account.attributes.*`` / ``account.pages.*`` 这类**账号级事实**，
    而它们还没有生产者。排除规则求不了值时**不得**当作命中 ——
    那会在没有证据的情况下把账号丢掉，比留在 ``discovered`` 危险得多。

    它与 :attr:`NO_RULE_FIRED` 的差别是**这一对成员存在的全部理由**，而且两者
    都会让账号停在 ``discovered``、都会让名单看起来一模一样：

    * :attr:`UNRESOLVED_FACTS` = 「**有规则没判**」（``AccountOutcome.unchecked``
      非空）。不是结论 —— 它不排除账号，但那是因为没有证据，不是因为核对过。
    * :attr:`NO_RULE_FIRED` = 「排到结论为止每一条都判了（命中，或确认为假），
      一条都没命中」。这才是「核对过，确实没有」。

    把两者并成一个「没命中」，这一层就再也说不出「我们其实没查过」——
    而报告上那句话与「查过了、没问题」逐字相同。
    """

    RULE_FIRED = "rule_fired"
    NO_RULE_FIRED = "no_rule_fired"
    UNRESOLVED_FACTS = "unresolved_facts"


class BlockReason(str, Enum):
    """BLOCKED 的细分原因。

    除 :attr:`UNSAFE_TARGET` 外，全部属于「改走别的公开来源」，不属于「想办法进去」。
    """

    WAF_CHALLENGE = "waf_challenge"
    HTTP_403 = "http_403"
    HTTP_429 = "http_429"
    PAYWALL = "paywall"
    LOGIN_REQUIRED = "login_required"
    UNKNOWN = "unknown"
    #: 目标地址不在本进程允许访问的范围内（非 http(s)、指向私网/环回/保留域名、
    #: 或重定向链把请求带向了那里）。**这一条与上面五条的处置不同**：它不是
    #: 「这个站点拦住了我们」，而是「我们不允许自己去那里」—— 改换来源、
    #: 换 IP、换 UA 都不适用，它的正确处置是记录并放弃这个目标。
    #:
    #: 与上面五条并列放进同一个枚举，是因为调用方问的是同一个问题
    #: （「这一跳为什么没成」），分成两个枚举会让报告里同一栏有两种拼法。
    UNSAFE_TARGET = "unsafe_target"


class BudgetLine(str, Enum):
    """预算的**科目**。

    不是「一个总预算」：搜索一次、抓一个文档、跑一次推理，代价的量纲完全不同，
    合成一个数字之后没人能回答「这次超支是搜索跑多了还是模型跑多了」。而且 §8
    明令浏览器子请求要**单独**计量并受自己的有界策略约束 —— 一个总数字里做不
    到这件事。

    科目的取值来自 §8 逐字列出的四类，不多不少：把它做成开放字符串，第一个
    拼错的名字会静静开出一条新预算线，而它没有上限（见
    ``BudgetNotConfiguredError``）。
    """

    SEARCH = "search"
    DOCUMENT = "document"
    INFERENCE = "inference"
    BROWSER_SUBREQUEST = "browser_subrequest"


class ReservationState(str, Enum):
    """一条预算预留的生命周期。

    **刻意只有三个成员。** 「provider 超时 / 结果未知」不是第四个成员 ——
    它就是 ``RESERVED``，且它应该一直停在 ``RESERVED``，直到有人拿证据来对账。
    §8 要求「在超时或 provider-unknown 时不要默认释放预算」，把这条规则做成
    一个**不存在的状态转移**（没有任何路径把 RESERVED 自动推向 REFUNDED），
    比把它做成一条写在文档里的约定可靠得多：默认行为（什么都不做）恰好是正确的。

    ``REFUNDED`` 因此只能由一个写明理由的显式调用产生。
    """

    RESERVED = "reserved"
    SPENT = "spent"
    REFUNDED = "refunded"


class ExecutionMode(str, Enum):
    """一次 Run 是**被谁授权跑起来的** —— 授权语义，不是速度，也不是批量大小。

    计划 §B17 逐字：「mode 不是权限捷径；pause、预算耗尽、Review hold、
    scope/version 变化都可以阻止下一 Run」。三个成员按**谁在什么时候决定下一轮**
    分开：

    * ``MANUAL`` —— 有人明确提交了这一次。这一轮走完之后保持 idle，**绝不自行
      扩下一批**。
    * ``BOUNDED_AUTO`` —— 在受理时写下的窗口（对象数 / 累计预算 / 截止时间）之内
      可以自己接下一轮；任一阈值到达即停，且**不创建下一 Run**。
    * ``WATCH`` —— 由到期或信号触发的一次维护，不是一次全量研究。

    把它们合成一个 ``auto`` 布尔值的代价在那一句里：一个从人工改成自动的 Run 与
    一个「阈值调大了」的 Run 在记录上会长得一样，而前者是**授权范围**的改变，
    后者是参数调整。

    **它不是「一次能跑多少」**：批大小是信封与 selector 的事。把它编码进这个字段，
    会让「这个 Run 为什么只跑了一半」在回执里得到一个错误的答案。
    """

    MANUAL = "manual"
    BOUNDED_AUTO = "bounded_auto"
    WATCH = "watch"


class RunStatus(str, Enum):
    """一次 Run **怎么结束的** —— 结局判词，不是生命周期。

    ## 它与「这一轮停没停」不是一个轴

    停没停由 ``domain/run.py::Run.stop_reason`` 回答（``None`` ⟺ 还在跑）。这个
    枚举答的是另一件事：停下来的时候，这一轮**算成了什么**。两个轴合成一个字段的
    代价是「跑完了」与「做成了」在盘上只剩一个词 —— 而一个正常结束、却一件事都
    没推进的 Run 是 §B17 条件 2 逐字要求能表达的情形。

    ## 为什么 ``unchanged`` / ``held`` / ``blocked`` 不在这里

    那三个词是 **RunDelta 上的读数**（「这一轮比上一轮多知道了什么」），这个枚举
    说的是「这一次的活干完了没有」。一个零新增的 Run 可以是 ``COMPLETED``（对象
    全走过了，这一轮确实没有新事实），也可以是 ``PARTIAL``（还有对象没走完）。
    把它们塞进这个枚举，会让「这一轮为什么没产出」在记录里只剩一个词可查，
    而那个词答不了这个问题 —— 而那个问句正是条件 2 本身。
    """

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class DecisionTask(str, Enum):
    """一次判定**在判什么** —— 服务端持有的 DecisionSpec 上的标签。

    三个成员来自 ``docs/REQUIREMENTS_DISCOVERY.md`` §9 Experiment 3 逐字列出的
    三族冻结样本（page triage / responsibility relation / role-business-function
    classification），不多不少。

    它**不是**「用哪个后端」：后端是按部署配置选的（``DecisionMode`` 与
    ``Settings.decision_provider``），与判什么无关。两者合成一个字段之后，
    「换一个后端重跑同一批样本」这件事在台账上会读成「判定类型变了」。
    """

    PAGE_TRIAGE = "page_triage"
    RESPONSIBILITY_RELATION = "responsibility_relation"
    ROLE_FUNCTION = "role_function"


class QuestionKind(str, Enum):
    """一道题的**问法**。

    **刻意只有两种。** 少掉的那一种是打分（Likert / 0..N 的程度题）：laya 有它
    （``score``），而本构建不提供 —— 一个分数要变成一条结论，必须再选一个阈值，
    而那正是 §4 明令禁止的东西（「没有独立留出评估支撑的临时阈值不得启用」）。
    少一个入口，就少一处可以做这件事的地方。

    ``YES_NO`` 的取值是端口自己的命名，不是 laya 的 ``noul``：``noul`` 是那个
    包内部的叫法，而端口是**两个**后端共用的词汇，它的名字不该跟着其中一个走。
    """

    CHOICE = "choice"
    YES_NO = "yes_no"


class DecisionOutcome(str, Enum):
    """一次判定的结果状态。**三个成员，处置各不相同。**

    把三个合成一个 ``ok: bool`` 是这一族里最容易犯的错：

    * ``ANSWERED`` —— 后端给了回答。
    * ``ABSTAINED`` —— 后端**能跑**，但对**这一份输入**拒绝作答（例如只有英文
      checkpoint、而输入的脚本不是拉丁字母）。处置是换后端或换输入。
    * ``UNAVAILABLE`` —— 缺凭据 / 缺权重 / 缺容量。这是**部署**的问题，不是输入的
      问题，处置是把它接上。§4：「缺凭据、checkpoint 或容量是 blocker，不是一次
      假装成功的推理。」

    三者的共同点是**都产出记录**：一次没做成的判定也要留痕，否则「为什么这个
    账号没有结论」在事后无法回答。
    """

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    UNAVAILABLE = "unavailable"


class DecisionMode(str, Enum):
    """这条判定**处于哪一种运行模式**（§4 的三种，本构建实现前两种）。

    * ``SHADOW`` —— 两个后端跑同一批冻结样本，答案**不交给**研究侧。
    * ``ASSIST`` —— 一个已配置的后端给出**参考**答案，研究侧看得见，轨迹被标记
      为「受过辅助」。

    第三种（automated acceptance）在本枚举里**没有成员**，这是刻意的：§4 要求
    它「在独立留出评估证明那个确切的判定/阈值之前保持关闭」。一个不存在的取值
    比一个「存在但没人敢用」的取值可靠 —— 后者会在某次赶时间的改动里被用上。

    模式是**逐次调用**的参数而不是后端对象的属性：同一个后端在同一天里既跑影子
    也做辅助，而两者在台账上必须分得开（辅助的判定会进入研究轨迹，影子不会）。
    """

    SHADOW = "shadow"
    ASSIST = "assist"


class JobInputKind(str, Enum):
    """``submit_research`` 的输入只有四种。前三种是 §2 逐字的那三条
    （existing source receipt / known Accounts / approved discovery scope），
    第四种是 §B12 那条（durable cohort 引用）。

    做成闭集而不是一个自由的 ``input_ref``，是因为**「哪一种输入」决定了
    这个作业能不能被验收**：发现范围的产出是候选账号，已知账号的产出是路线。
    一个自由字符串会让这两件事在记录里长得一样，而报告需要分开它们。

    没有成员「手工域名清单」也是逐字来的：§2 另有一句「Do not require
    ... a manually fabricated list of domains」。

    ``COHORT`` **不是**那第四条被拒的东西，两处逐字对比就看得出来：被拒的是
    「人工拼出来的一份域名清单」，而这一条的主体是**系统自己读池子冻下来的**
    一份名单（``freeze_cohort`` 的产物）—— 恰恰是 §B12 用来消掉人工清单的那个
    东西。§B12 的实现逐字要求「维护/草稿/**研究**接受 Scene selector 或 durable
    cohort 引用」，前两条在 §B12 那一批落到了工具面上，第三条当时没有。

    它与 ``DISCOVERY_SCOPE`` 是**同一形状**的先例：后者的 ``input_ref`` 也是
    一个内容寻址的工件名（提交的人手里没有那个值，所以按名字反查回执），
    而不是一个文件名。所以「名字指向库里的一份名单」不是本枚举里的新东西。
    """

    SOURCE_RECEIPT = "source_receipt"
    KNOWN_ACCOUNTS = "known_accounts"
    DISCOVERY_SCOPE = "discovery_scope"
    COHORT = "cohort"


class JobState(str, Enum):
    """作业的**操作者维度** —— 只有放行与不放行，没有「做完了」。

    这个枚举里**没有** ``done`` / ``settled``，这是刻意的，而且是本模块里最
    容易搞错的一处。「这个作业做完了吗」由 work item 回答（每个都带停止理由），
    不由作业状态回答。两个轴合进一个字段的代价是：一个多 work item 的作业在
    第一个 work item 停止时就被读成「完成」，而另一个还在跑。

    同一条纪律在 ``domain/budget.py::BudgetUsage`` 里已经用过一次 —— 那里拒绝把
    ``spent + reserved`` 汇总成一个「已用」，理由同样是「汇总之后某个问题再也
    无法从记录回答」。

    ``BLOCKED`` 与 ``PAUSED`` 分开，是因为**谁能解除它们不同**：pause 由调用方
    自己 resume，block 要等前置条件（缺 key、缺额度、缺 checkpoint）被满足。
    合成一个，``resume`` 就成了一条能清掉阻塞的旁路。
    """

    #: 放行派发。
    QUEUED = "queued"
    #: 不放行**新**派发；已在途的 attempt 照常结算（§6 逐字：Pause normally
    #: prevents new dispatch and lets accepted in-flight results settle）。
    PAUSED = "paused"
    #: 前置条件没满足，且不是调用方能单方面解除的。
    BLOCKED = "blocked"
    #: 终态。**此后不再受理任何结果** —— 一个取消之后才到的 findings 是陈旧的。
    CANCELLED = "cancelled"


#: 合法的作业状态转移。做成一张**表**而不是散在各分支里：一张表能被遍历着测，
#: 而「哪些转移合法」正是 ``control_job`` 要守的东西。
#:
#: ``CANCELLED`` 的出边为空集 —— 终态是「没有边」，不是「有一条边但被拒绝」。
JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.PAUSED, JobState.BLOCKED, JobState.CANCELLED}),
    JobState.PAUSED: frozenset({JobState.QUEUED, JobState.BLOCKED, JobState.CANCELLED}),
    JobState.BLOCKED: frozenset({JobState.QUEUED, JobState.PAUSED, JobState.CANCELLED}),
    JobState.CANCELLED: frozenset(),
}


class AttemptState(str, Enum):
    """一次尝试的终局只有三种，**没有第四种「过期」**。

    §6 要求「attempt/lease protection」，而 外部执行器那侧**没有** attempt 这个概念
    （见 ``docs/MVP_MIGRATION_PLAN.md`` §2.9：attempt 只有三个不相干的住处，
    lease 的 32 处命中全是 ``release``）。所以租约是我们自己的一等概念，包括
    「它怎么结束」这个问题。

    这里**刻意没有** ``EXPIRED``：让租约按时钟自动失效，等于在「那个 worker 死了」
    这个假设上做一次无人复核的判断，而猜错的代价正是 §8B 逐字禁止的
    「duplicate expensive work」—— 两个 worker 同时跑同一件昂贵的事，账上只会
    显示「花了两次」。要收回一个租约，得有一个显式的动词并写明理由（``release``），
    与 ``BudgetLedger.refund`` 是同一条纪律。
    """

    #: 持有中。一个 work item 同时最多一条。
    LEASED = "leased"
    #: 结果已被受理。终态。
    SETTLED = "settled"
    #: 显式交还，**必须写明理由**。这个 work item 因此可以再次出租（重试）。
    RELEASED = "released"


class StopReason(str, Enum):
    """一个 work item **为什么停下** —— 逐字取自 ``docs/DATA_MODEL.md`` §0 的
    停止语义清单。

    它是 ``submit_findings`` 的必要组成部分（§3：attempt/result id、observations、
    candidates、gaps、**stop reason**），也是报告里「这次产出为什么到此为止」
    唯一的机器可读依据。

    没有 ``completed`` / ``success`` 这样的成员：§8B 逐字写着「No fixed positive
    A/B count is required: zero is an honest business outcome」，而 ``GOAL_MET``
    表达的是「该覆盖的都覆盖了」，不是「找到了 N 个」。
    """

    GOAL_MET = "goal_met"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BLOCKED = "blocked"
    AMBIGUOUS_HOLD = "ambiguous_hold"
    NO_USEFUL_NEXT_ACTION = "no_useful_next_action"
    MANUAL_PAUSE = "manual_pause"


__all__ = [
    "FetchOutcome",
    "BROWSER_ESCALATION_OUTCOMES",
    "TERMINAL_OUTCOMES",
    "FetchTier",
    "SourceKind",
    "BINDING_ELIGIBLE_SOURCES",
    "ContactField",
    "Cohort",
    "AccountStatus",
    "RouteStatus",
    "UNREACHABLE_WITHOUT_AUTHORIZATION",
    "RoleClass",
    "BusinessFunction",
    "VenueRelation",
    "RouteBinding",
    "EntryKind",
    "RecordBlockPriority",
    "Grade",
    "GradeReason",
    "EligibilityReason",
    "BlockReason",
    "ExecutionMode",
    "RunStatus",
    "DecisionTask",
    "JobInputKind",
    "JobState",
    "JOB_TRANSITIONS",
    "AttemptState",
    "StopReason",
    "QuestionKind",
    "DecisionOutcome",
    "DecisionMode",
]

"""发现期的**候选账号**契约 —— 账号记录之前的那一层。

这个模型是 ``contracts/account-candidate.schema.json`` 的来源：schema 由
``marketops.schema`` 从本文件导出，并由 ``tests/test_contracts_sync.py`` 断言两者一致。
契约**从代码导出**，不手写。

为什么候选必须是一个独立的东西，而不是「字段不全的 ``AccountRecord``」：
见 :class:`AccountCandidate` 的类文档。一句话 —— ``Cohort`` 是闭集，
而 ``AccountRecord`` 把它列进了 ``required``。
"""

from __future__ import annotations

from pydantic import Field, ValidationInfo, field_validator

from .base import StrictModel as _Strict
from .errors import ConfigError
from .profile import CLASSIFICATION_AXES, CLASSIFICATION_UNKNOWN
from .sources import SOURCE_METHODS, SourceMethod
from .util import FILENAME_ID_MAX_LENGTH, check_canonical_id, check_slug_safe

#: ``pairing_key``（canonical host）的 ``slugify`` 界。**只在这里定义一次。**
#:
#: 它是 DNS 完整域名的上限（253 个字符），而不是某个落盘界 —— ``pairing_key``
#: 不变成文件名。但仍然要传一个界给 :func:`~marketops.domain.util.check_canonical_id`：
#: 规范形式的判据是 ``slugify(value, max_length=此界) == value``，界写大一点
#: 只会让「本来就落不了盘的超长 host」被拒，不会放行任何会被改写的东西。
PAIRING_KEY_MAX_LENGTH = 253


class AccountCandidate(_Strict):
    """一条**候选账号**：从公开网络发现、尚未物化成 ``AccountRecord`` 的一行。

    链路「公共网络 → 候选场馆/资产 → 场地表面与环境证据 → 责任实体 → 官方域名 →
    符合条件的账号」到这里止于候选，本轮**不产出** ``AccountRecord``。
    这一层必须独立存在的理由是硬的：``Cohort``（``marketops/domain/enums.py``）
    是**闭集**（``known_success`` / ``accessible_no_core_email`` /
    ``previously_blocked``），``contracts/account-record.schema.json`` 的
    ``required`` 含 ``cohort``，``marketops/application/cohorts.py`` 断言总数 ==
    ``plan.total``，``tests/test_cohorts.py`` 钉死 10/10/10。发现出来的账号
    **没有** cohort，也没有诚实的值可填 —— 所以本模型里**没有** ``cohort`` 字段，
    也没有一个本轮恒为 ``None`` 的 ``account_id`` 槽位。

    **三个 id 空间分开，不缝成一个：**

    * ``candidate_id`` —— **发现期身份**。它必须满足规范形式
      （``slugify(id, max_length=FILENAME_ID_MAX_LENGTH) == id``）：发现期的证据会
      经由同一个 ``LocalEvidenceStore`` 落盘，``Evidence.account_id`` 现在就要求
      这个形式，候选 id 将来就是它。**从 URL 直接派生 candidate_id 会炸** ——
      ``util._UNSAFE_ID_CHARS`` 含 ``:`` 与 ``/``，任何带 scheme 或路径的串都会被拒。
      这不是不便：这条 id 会被当作 ``<id>.jsonl`` 的文件名，而 ``slugify`` 会把
      两个不同的 id 映射到同一个文件 —— store 不报错，两个候选的证据被静默合并。
      界复用 ``FILENAME_ID_MAX_LENGTH`` 而不是另写一个 60：两处各写一个字面量
      必然会漂移，而漂移的那一次会让「校验通过的 id」在落盘时被截断。
    * ``pairing_key`` —— 两支臂的**配对单位**，canonical host。
      **允许为空**：没有官网的候选没有配对身份，它**不参与配对**，而不是配到一个
      空串上。空串与「非空但不规范」是两条不同的路 —— 后者直接报错，
      不会被悄悄改写成空串（那会让「这个候选没有官网」与「它的官网写错了」
      在数据上长得一样）。规范形式在此同样必要：同一台主机的两种拼写
      会让配对被拆成互不相干的两组，而报告上只是「多了几行」。
    * ``account_id`` —— **本层不存在**。晋级由下一轮定义，届时由
      :class:`~marketops.domain.evidence.AccountRecord` 自己的 validator 重新校验
      （两个 id 空间在那时才发生一次受控的、可审计的映射）。在这里留一个
      恒为 ``None`` 的槽位是臆造：它会读成「这个候选的账号 id 还没填」，
      而事实是「本轮不产生账号记录」；将来真去填它的那一行，
      会绕开 ``AccountRecord`` 的校验。

    **没有 snippet 字段，这是刻意的。** ``marketops/application/pipeline.py`` 的
    ``_scan_snippet`` 靠「SERP 里出现过邮箱、绑定被拒绝」这个计数证明那条不变量
    还在被考验。让它进来，等于允许一个绕过检索工具的邮箱从旁路进入发现层 ——
    那条计数器就开始说谎，而说谎的方向恰好是「一切正常」。

    **不得内联大段原文。** 证据是**指针 + 哈希**（``evidence_ids`` /
    ``surface_evidence_ids`` / ``domain_evidence_ids`` 指向 ``Evidence.evidence_id``），
    不是正文：一行候选读得懂，不依赖证据还能不能读到；而证据一旦被删掉，
    依赖它的判定必须**降级**，不是照旧成立。

    **事实名的绑定规则**（下游把候选投影成引擎准入事实时必须遵守）。
    投影是**字面量、没有中间映射**：``organisation_kind`` / ``venue_environment`` /
    ``surface_class`` / ``surface_evidence_confirmed`` 直接取字段，
    ``exclusions_hit`` 由同名列表投影，``any_page_fetched`` / ``any_page_blocked``
    由两个计数投影。字段名与事实名逐字同名，是为了让「投影取错了哪个字段」
    在命名上无处可藏。**投影本身不在这里** —— 它在
    ``marketops/application/qualify.py::candidate_facts``，与判定循环
    （``application/grading.py::account_outcome_from_facts``）分开：投影是
    「这一行有什么值」，循环是「这些值按 ``grading.yaml`` 判成什么」。

    **生产者请走 :func:`new_candidate`，不要直接构造** —— 它的 ``method`` 参数收
    一个 ``SourceMethod`` 注册表成员并由它派生 ``source_method_id``，因此「写一个
    未注册的来源 id」在类型层面就写不出来。本模型自己仍然收字符串，因为它是**契约**的
    形状（导出的 schema 里那一栏是 ``string``）：契约说值长什么样，不说它的出处。
    来源注册表只覆盖**引擎自己能产出**的发现方法；SOP L82 的「每个来源有独立
    ``source_method_id``」指的是具体来源（某个目录、某个登记册），那些属于
    ``profiles/*/`` 一侧的市场知识，两者今天没有对齐 —— 这一条是**未决项**，
    记录在 ``marketops/domain/sources.py`` 的「一层不在这里的东西」一节，
    这里不扩注册表。
    """

    #: 发现期身份。规范形式 + ``FILENAME_ID_MAX_LENGTH`` —— 见类文档。
    candidate_id: str
    #: **责任实体** —— 谁**拥有 / 运营 / 维护 / 预约控制 / 采购**这块场地。
    #: 这是账号的**定义**（SOP L72：「账户单位是拥有、运营、维护、预约控制或采购的
    #: 实体，不是单个 pitch」），也是本行支撑的第一条判定：这一行说的是哪个组织。
    organisation: str
    #: 组织形态（责任实体的类型）。**市场中性**：取值词表由 profile 提供，
    #: 核心代码不认识任何一个取值。引擎的事实 ``organisation_kind`` 由它投影。
    #:
    #: 缺省是 :data:`~marketops.domain.profile.CLASSIFICATION_UNKNOWN`，**不是空串**。
    #: 四格（含下面三格）同一条纪律，理由见类文档的「四格未确认」一节。
    organisation_kind: str = CLASSIFICATION_UNKNOWN
    #: 场馆资产：场地名。账号是实体、场地是资产，两者在这一行里同时出现但**都不是键** ——
    #: 一个组织管很多场地，一块场地可以由别的组织运营维护。
    venue_name: str
    #: 场馆资产：场地类别。同上，取值词表由 profile 给。
    venue_category: str = ""
    #: 场地环境（室内 / 室外这一维）。支撑「这块场地属不属于目标场景」这条判定。
    #: 事实 ``venue_environment`` 由它投影。缺省 ``unknown`` —— 见上。
    venue_environment: str = CLASSIFICATION_UNKNOWN
    #: 场地表面类别。**市场中性**（词表来自 profile），支撑「这块场地的表面
    #: 是不是目标场景要的那一类」这条判定。事实 ``surface_class`` 由它投影。
    #: 缺省 ``unknown`` —— 见上。
    surface_class: str = CLASSIFICATION_UNKNOWN
    #: 这一行的**国家 / 地区**读数（小写取值，词表来自 profile 的 ``classification``）。
    #: 事实 ``country`` 由它投影 —— 而 ``out_of_region`` 那条规则要的正是它。
    #:
    #: 它**不是** ``AccountRecord.country`` 的预演：那一格是 operator 写下来的账号
    #: 属性，这一格是**从这一页读出来的**，带 ``evidence_ids`` 指着的出处。两者投影到
    #: 同一个事实名上，是因为规则问的是同一个问题（这个组织在不在范围内的区域）；
    #: 差别在于**凭什么**，而那是证据指针的事，不是字段名的事。
    #: 缺省 ``unknown`` —— 见上。
    country: str = CLASSIFICATION_UNKNOWN
    #: 表面判定被证据确认到了什么程度 —— **三值**，不是布尔：
    #: ``None`` 还没查、``True`` 查到了、``False`` 查过且没有。
    #: ``profiles/*/grading.yaml`` 里 ``artificial_surface_only`` 那条的前提条件
    #: 是 ``account.evidence.natural_grass.verified == false``，
    #: **不是**取反。两个形状今天**同判**（未确认在任何极性下都不决定子句，
    #: 见 ``marketops/application/grading.py`` 的 ``evaluate_clause``），
    #: 所以选 ``== false`` 的理由不再是「取反会误命中」，而是它把判据写成了
    #: **正面**的断言：「查过了，是没有」—— 而取反形状的判据依赖引擎对极性的
    #: 处置，那是这一层的不变量，不该由一条业务规则去引用。
    #: 事实 ``surface_evidence_confirmed`` 由它投影。
    surface_evidence_confirmed: bool | None = None
    #: 支撑上面那条表面判定的证据指针。与判定分开放，是因为「判定为真」与
    #: 「凭什么说它真」是两件事 —— 合成一个字段就没法逐条审计 false attribution。
    surface_evidence_ids: list[str] = Field(default_factory=list)
    #: **官方域名**。**可为空**：域名是这一轮要**找出**的东西之一，还没找到就是空，
    #: 不是「一个空的域名」。它也是配对的实际载体（``pairing_key`` 是它的规范形式）。
    official_domain: str = ""
    #: **我们在哪台主机上读到了这一行** —— 一次**观察**，不主张归属。
    #:
    #: 它和 :attr:`official_domain` 是两句话，而发现期那一类行上两者的**取值恰好
    #: 相同**（我们是在那台主机的页面上读到这个组织说它自己是谁的），所以这一格
    #: 看着像冗余。它**不是**：另一类候选行（母池导出、登记册导入）的域名来自上游
    #: 那一列，而那些行**没有**观察，只有转述。合成一格之后「我们看到的」与
    #: 「别人说的」在报告里长得一样，而下落相反 —— 审计里那条
    #: 「SERP 抓回来的主机被读成『这是它的官网』」正是从这里开始的。
    #:
    #: 它**不是**绑定那一层（SOP L121）的账号域名权威。那一层的权威是回执上的
    #: ``verified_official_domains`` + ``domain_verdict``，由
    #: ``application/inputs.py::_binding_domains`` 折进 ``SubjectDossier``。
    #: 候选行到不了那条路上（``BindingSubject`` 没有收候选行的投影点）。
    discovered_host: str = ""
    #: 官方域名的证据指针。一条没有出处的域名与「随手写的域名」在报告里长得一样。
    domain_evidence_ids: list[str] = Field(default_factory=list)
    #: 配对单位，canonical host。**允许为空** —— 见类文档。
    pairing_key: str = ""
    #: **发现方法**（``source_method_id``）。**必填**：发现期的候选池按 SOP L82
    #: 属于**补充证据**而非母池 —— 原文逐字：「搜索结果、单页新闻或单一 contact
    #: 页只能作补充证据，不能当全量母池」。每行都必须带它，否则报告会被
    #: 读成母池，而那是 SOP 唯一明令禁止的读法。它也是「来源族多样性」这个指标
    #: 唯一算得出来的依据：没有出处的候选与「随手挑的一组候选」在报告里长得一样。
    #: 闭集在 ``marketops/domain/sources.py``。
    source_method_id: str
    #: 抓了几页。``None`` = **没有记录**，不是 0 —— 0 与「没记」在报告里长得一样，
    #: 而引擎把 ``None`` 读成「**未确认**」（规则不决定、进 ``unchecked``）、
    #: 把 0 读成「抓了 0 页」（**确认为假**，规则不命中但不进 ``unchecked``）。
    #: 事实 ``any_page_fetched`` 由它投影。
    pages_fetched: int | None = Field(default=None, ge=0)
    #: 被挡了几页。同上，``None`` = 没有记录。事实 ``any_page_blocked`` 由它投影。
    pages_blocked: int | None = Field(default=None, ge=0)
    #: 命中的**硬排除项** id。空列表 = 一条都没命中。取值词表由 profile 给，
    #: 核心代码不认识任何一个取值。事实 ``exclusions_hit`` 由它投影
    #: （规则表只问「是不是非空」）。
    exclusions_hit: list[str] = Field(default_factory=list)
    #: 行级证据指针（责任实体 / 场馆资产 / 来源）。与上面三组专属指针并列，
    #: 不合并：合并之后「这条证据支撑的是域名还是表面」就只剩「哪个先写」能回答。
    evidence_ids: list[str] = Field(default_factory=list)

    @field_validator("candidate_id")
    @classmethod
    def _candidate_id_is_canonical(cls, value: str) -> str:
        """发现期身份的**唯一**校验入口。

        与 ``Evidence.account_id`` / ``AccountRecord.account_id`` 走同一段代码
        （:func:`marketops.domain.util.check_canonical_id`），因为三处说的是同一个约束：
        id 会被当作文件名，而文件名必须与 id 一一对应。
        """
        return check_canonical_id(value, "candidate_id", max_length=FILENAME_ID_MAX_LENGTH)

    @field_validator("pairing_key")
    @classmethod
    def _pairing_key_is_empty_or_a_canonical_host(cls, value: str) -> str:
        """空串放行；非空就必须是规范 host。两者**不得**互相塌缩。"""
        if not value:
            return ""
        return check_canonical_id(value, "pairing_key", max_length=PAIRING_KEY_MAX_LENGTH)

    @field_validator("official_domain", "discovered_host")
    @classmethod
    def _a_bare_host(cls, value: str, info: ValidationInfo) -> str:
        """域名要么为空，要么是一个**裸域名**。

        ``check_slug_safe`` 恰好是这里需要的判据：它挡掉 ``:`` ``/`` 这类字符，
        于是 ``https://host/path`` 不能被当成一个域名存进来 —— 那个值下游会被
        当作 host 去比较（``email_domain_matches_account`` 就是拿域名比的），
        带 scheme 的值在那里永远不相等，而差别看不出来。

        两格共用一条判据：``discovered_host`` 旁边没有 URL 那一格，所以最可能的
        写法错误就是把整条地址写进来 —— 而它在下游同样表现为「这一行没有主机」，
        不是一条报错。
        """
        if not value:
            return ""
        return check_slug_safe(value, info.field_name)

    @field_validator(*CLASSIFICATION_AXES)
    @classmethod
    def _classification_readings_are_conclusions(cls, value: str, info: ValidationInfo) -> str:
        """四格分类读数要么是**一个取值**，要么是 ``unknown`` —— **不接受空串**。

        这一条挡着的是这一层最贵的一类静默错误，而它是**旧行为**：
        这四格曾经缺省成 ``""``，而 ``""`` 在规则方言里是**已确认的空**
        （「记了，是空」）：点它们的规则会确定地不命中、并且**不进** ``unchecked``。
        报告上那看起来像一条结论 —— 「核对过，不是室内、不是人造草、不是 MUGA」——
        而事实上没有任何人查过。

        ``unknown`` 与 ``""`` 的区别就是本仓那条分行线：``unknown`` 是「没查出来」，
        它不决定任何子句（规则进 ``unchecked``、判词 ``UNRESOLVED_FACTS``、
        账号停在 ``discovered``）；``""`` 是「查过了，是空」，它会参与求值、
        会被取反。让两者都能构造出来，等于让这条分行线由**谁构造了这一行**决定。

        取值本身**不在这里校验**：词表住 profile（``classification`` 块），
        核心代码不认识任何一个取值。这里只拦「既不是取值也不是结论」的那一个写法。
        """
        if not value:
            raise ValueError(
                f"{info.field_name} 是空串。\n"
                "  空串在规则方言里读作「**记了，是空**」—— 那是一条已确认的结论："
                "点它的规则会确定地不命中，而且不进 unchecked，报告上看起来完全正常。\n"
                "  「还没查出来」的正式写法是 'unknown'（domain/profile.py 的 "
                "CLASSIFICATION_UNKNOWN）：它不决定任何子句，账号停在 discovered。"
            )
        return value

    @field_validator("source_method_id", "organisation", "venue_name")
    @classmethod
    def _mandatory_text_is_present(cls, value: str, info: ValidationInfo) -> str:
        """必填字段不接受「填了个空」。

        ``source_method_id`` 一个空串等于没有出处，与缺字段同义
        （SOP L82 要求每一行都能回到发现方法）；``organisation`` 与
        ``venue_name`` 空着的那一行，回答不了这条链路问的两个问题
        （哪个组织、哪块场地）。
        """
        if not value.strip():
            raise ValueError(f"{info.field_name} 不能为空或只有空白")
        if value != value.strip():
            raise ValueError(f"{info.field_name} 首尾不得有空白")
        return value


def new_candidate(method: SourceMethod, **fields: object) -> AccountCandidate:
    """**生产者的构造入口** —— 来源必须是一个注册表成员，不是一段文本。

    ``AccountCandidate(source_method_id="...")`` 收一个自由字符串，因为它是**契约**
    的形状（从本模型导出 schema，``source_method_id`` 那一栏是 ``string``），
    而契约描述的是「一个值长什么样」，不是「它的出处」。生产者这一侧不一样：
    它**知道**自己是怎么发现这一行的，所以它不该、也不需要把 id 抄一遍 ——
    抄的那一次就是一次拼写机会，而后果按 ``sources.py`` 的原文是「来源族多样性
    这个指标看起来很高，而没有任何东西支撑它」。

    于是这里把「写法」从纪律变成**类型**：入口收一个 :class:`SourceMethod`，
    id 由成员派生（``method.id``）。写一个未注册的 id 在类型层面就写不出来。

    **身份也查，不只查 id**：自己 ``SourceMethod(id="search.engine.v1", ...)``
    造一个同 id 的成员不算数 —— 只比 id 会把注册表降级成一张词表，而「加一个来源」
    就重新变成一次谁都不用知道的改写。要求身份，加来源就必须改 ``sources.py``，
    那是一次进了版本控制的决定（``sources.py`` 的「为什么是闭集」一节）。

    Args:
        method: ``SOURCE_METHODS`` 里的那个成员**本身**。
        **fields: 其余字段，照 :class:`AccountCandidate` 的签名。

    Returns:
        一条候选行，``source_method_id`` 由 ``method`` 派生。

    Raises:
        ConfigError: ``method`` 不是注册表里的成员（未注册的 id / 裸字符串 /
            自己 new 出来的同 id 成员），或者 ``fields`` 里又想自己传
            ``source_method_id``（两处 id 不一致时谁赢是个说不清的问题，
            一致时那次传参是纯粹的复述）。
    """
    if not isinstance(method, SourceMethod):
        raise ConfigError(
            f"候选的来源必须是一个 SourceMethod 成员，收到的是 {type(method).__name__}"
            f"（{method!r}）。来源是一个闭集：先改 marketops/domain/sources.py。"
        )
    registered = SOURCE_METHODS.get(method.id)
    if registered is not method:
        raise ConfigError(
            f"{method.id!r} 不是注册表里的那个成员"
            + ("" if registered is None else "（id 相同但不是同一个对象）")
            + "。只查 id 会把注册表降级成一张词表；加来源请改 marketops/domain/sources.py。"
        )
    if "source_method_id" in fields:
        raise ConfigError(
            "source_method_id 由入口从 method 派生，不要再传一次 —— "
            "两处 id 不一致时谁赢是个说不清的问题。"
        )
    return AccountCandidate(source_method_id=method.id, **fields)


__all__ = ["PAIRING_KEY_MAX_LENGTH", "AccountCandidate", "new_candidate"]

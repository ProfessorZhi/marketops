"""证据、账号与运行记录的持久化模型。

这些模型是 ``contracts/`` 下 JSON Schema 的**来源**：schema 由 ``marketops.schema``
从本文件导出，并由 ``tests/test_contracts_sync.py`` 断言两者一致。
因此「文档写的契约」与「代码里的契约」不可能漂移。

本模块承载三条硬约束：

1. ``Evidence.literal_value`` 必须是来源文本里的**字面**片段 —— 不做推断、不做猜测补全。
2. 只有 ``BINDING_ELIGIBLE_SOURCES`` 里的来源可以成为证据依据。
3. ``send_authorized`` 恒为 False、``research_only`` 恒为 True —— 构造 True 会直接报错。

**这里没有 Contact。** 定级的主语是 ``ContactRoute``，它的模型在
``marketops/domain/entities.py`` —— 与场地资产、责任实体、人、岗位放在一起，
因为那条路线的形式定义横跨它们全部。本模块只留「账号这条研究输入」
（``AccountRecord``，它的状态是 :class:`~marketops.domain.enums.AccountStatus`）
与「凭什么这么说」（``Evidence``）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import field_validator

from .base import StrictModel as _Strict
from .enums import (
    BINDING_ELIGIBLE_SOURCES,
    AccountStatus,
    Cohort,
    ContactField,
    SourceKind,
)
from .util import check_canonical_id, sha256_text


class AccountRecord(_Strict):
    """名单里的一个待研究账号 —— 拥有 / 运营 / 维护 / 预约控制 / 采购场地的组织。

    ``status`` 是**账号准入**状态（SOP v1 L188 左侧状态机），与路线等级正交：
    组织可以 eligible 而已拿到的路线是 X。判定它的规则在 ``grading.yaml`` 的
    ``x_rules`` —— 见 ``marketops/application/grading.py`` 的 ``grade_account``。

    ``x_rules`` 今天六条，六条都编译出了可求值的 ``requires``。但事实的**供给**
    分成两类，而两类今天落到同一个判词上：

    * **这张记录自己的字段** —— 只有 ``out_of_region`` 引用（``country``），
      有真生产者，写出来就判得了；
    * **候选事实** —— 其余五条引用场地环境 / 组织形态 / 表面类别 / 抓取统计，
      它们的载体是候选行（``marketops/domain/candidate.py``），在**这张记录**
      上一个都没有，引擎手上的值是 ``None``。

    ``None`` 是**未确认**，未确认的子句**在任何极性下都不决定**（包括取反），
    于是这两类都落进 ``unchecked``。判词是 ``unresolved_facts``，**不是**
    ``no_rule_fired`` —— 后者留给「排到结论为止每一条都判了、一条都没命中」，
    是**唯一**能说「核对过，确实没有」的判词，本仓今天还说不出口。

    ``country`` 自己那条也有未确认的形态：**它的缺省曾经是 ``"UK"``**，于是没写
    国家的记录带着一个没人做过的判定进了求值，``out_of_region`` 对它**静默不触发**
    —— 报告上的读法是「这个账号在区域内」。缺省改成 ``None`` 之后它落进
    ``unchecked``，读成「没人查过」，而 ``unchecked`` 是能被看见的。

    ``ProfileBundle.unknowns`` 与 ``DeclaredGrading.ungradeable`` 说的是**装载期**
    的「事实名缺失」，与这里的**运行期**「值未确认」是两件事，一个为空不代表
    另一个为空。

    ``account_id`` 是账号键。**场地名不是键**：一个 Council 管很多场地，
    一个场地可能由别的组织运营维护，一个人可以对多个场地负责 ——
    这些关系在 ``AccountVenueRelation`` 里，不在这个字段里。
    """

    account_id: str
    organisation: str
    cohort: Cohort
    #: 账号准入状态。默认 ``discovered``：刚进名单，还没做过任何判定。
    status: AccountStatus = AccountStatus.DISCOVERED
    domain: str = ""
    #: 组织所在国家。``None`` = **还没查**，不是「查了，不在英国」。
    #:
    #: 这个缺省曾经是 ``"UK"``，而那台机器的行为是**伪造证据**：一条没写国家的
    #: 记录带着一个没人做过的判定进入了 :func:`~marketops.application.grading.
    #: account_facts`，于是 ``grading.yaml`` 的 ``out_of_region``
    #: （``requires: ["!country in [gb, uk]"]``）对它**静默不触发** ——
    #: 报告上的读法是「这个账号在区域内」，而事实是「没人查过它的区域」。
    #:
    #: 现在的方言里 ``None`` 是**未确认**，未确认的子句**在任何极性下都不决定**
    #: （见 ``application/grading.py`` 的 ``evaluate_clause``），于是那条规则落进
    #: ``unchecked``；而 ``unchecked`` 是**能被看见的**，缺省值不是。
    #:
    #: ``""`` 不是这个取值：空串是**证据**（「查了，是没有」），它可以被取反，
    #: 也确实出现在来源记录里。把两者混起来会把「还没查」变成一条可用的排除依据
    #: —— 方向正好反了，因为**没有证据就排除账号，比留着它危险得多**。
    #:
    #: 另有两处同形的缺省（``VenueAsset`` / ``ResponsibleEntity``），理由相同。
    country: str | None = None
    notes: str = ""

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "account_id")


class Evidence(_Strict):
    """一条**字面**证据：在某个已抓取的公开文档中，某个字面片段支撑了某个字段。

    证据不是「结论」，是「结论的依据」。同一条证据可以支撑多个字段，
    但每次支撑都记一条 —— 这样 §10 的 false attribution 才能被逐条审计。
    """

    #: 这一行事实属于哪个**工作区**（部署的 Scene，``Settings.profile``）。
    #:
    #: ## 为什么必须有这一格
    #:
    #: ``evidence`` / ``contact_route`` / ``route_opportunity`` 三张表此前只有
    #: ``account_id``，而账号这一层没有 Scene 这一维 —— 于是「这一条证据属于哪个
    #: Scene」在盘上**没有任何一格可以回答**，只能靠 ``account_id`` 的名字去推。
    #: 一个部署只服务一个 Scene 曾经是唯一在挡这件事的东西（一句话，不是一条
    #: 约束）；它不成立的那一刻，这条缺口就是一条真的跨 Scene 读。
    #:
    #: ## 为什么是 ``workspace`` 而不是 ``scene_id``
    #:
    #: 与 :class:`~marketops.domain.maintenance.Observation` 逐字一致，理由也就是
    #: 那里的那一条：**工作区是授权链的第一段**。原始事实在一个已授权工作区内部
    #: 可以跨 Scene 复用，复用范围止于工作区边界。带 ``scene_id`` 会把这行事实钉在
    #: 一个 Scene 的规则版本上，而「同一个工作区换了 Scene 版本之后，上一次看到的
    #: 那一页还算不算数」是一个 Freshness 问题，不是血统问题。两处用两个字段名会
    #: 让「这条事实是谁的」在同一个仓里有两个答案。
    #:
    #: ## 为什么没有默认值
    #:
    #: 一个「缺省 = 当前 Scene」的默认值会把「**没写血统**」变成一次**不留痕的放行**
    #: —— 而这正是这一格要消灭的东西。``Observation`` 也是这么做的。取值必须由
    #: 写入方**当场说清**，而它的可信来源只有一个：本部署的配置（``Settings.profile``，
    #: 见 ``marketops/application/pipeline.py`` 与 ``application/discovery_run.py``
    #: 的调用点）。历史行的回填走迁移，不走默认值 —— 见
    #: ``marketops/adapters/sqlite/migrate.py``。
    #:
    #: 判据是 ``check_canonical_id``：工作区名同时是一个目录名
    #: （``Settings.profile_dir = profiles_dir / profile``），所以它必须已经是规范
    #: 形式。校验不做规范化 —— 规范化会把两个不同的名字静默合并成一个。
    workspace: str
    evidence_id: str
    account_id: str
    source_url: str
    source_kind: SourceKind
    literal_value: str
    content_sha256: str
    fetched_at: datetime
    #: **来源自己声明**的发布时刻；这一页没写时为 ``None``。
    #:
    #: ## 为什么这一格是 §9 要的，而不是又一个时间字段
    #:
    #: 只有 ``fetched_at`` 时，「2021 年的理事会 PDF 指名 Jane 是场地主管，
    #: 今天被我们抓到」与「今天的页面上说 Jane 是场地主管」在数据里长得一样。
    #: 前者的正确读法是「Jane 在 2021 年是场地主管」（模块
    #: :mod:`marketops.domain.temporal` 的整段理由）。两条路线的**证据**在
    #: 这一格上分得开，而它们的 ``fetched_at`` 完全可能同一天。
    #:
    #: ## 为什么是「唯一一个」而不是「最近一个」
    #:
    #: 取值来自 :func:`~marketops.domain.extract.page_publication_time`，而它
    #: 在一页上有两个互不相同的发布日时给 ``None``。挑一个更近的看起来更保守，
    #: 实际是把「哪一天管这一处内容」这个**归属**判断塞进了一个只该记录事实的
    #: 字段 —— 而依据不在页面上。§9 禁止的缝法是「A 页的姓名 + B 页的邮箱」，
    #: 同一页上两处日期之间没有依据时，同一条纪律说的也是不挑。
    source_published_at: datetime | None = None
    field: ContactField | None = None
    #: 命中处的周围文本，供人工复核。不参与自动化判定。
    context: str = ""
    #: 命中位置描述（如 "page 3" / "block 12"）。
    page_locator: str = ""
    parser: str = ""
    http_status: int | None = None
    tier: str = ""

    @property
    def binding_eligible(self) -> bool:
        """本证据能否作为 Contact 的绑定依据。

        SERP snippet 永远为 False —— 这是「snippet 里的邮箱不能直接成为 Contact」
        在数据层面的表达。
        """
        return self.source_kind in BINDING_ELIGIBLE_SOURCES

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        """与 :class:`~marketops.domain.maintenance.Observation` 同一条判据。

        工作区名是一个路径段（``profiles_dir / profile``），所以校验比
        ``slug-safe`` 严：它必须已经是 ``slugify`` 的不动点。**不截断、不折叠**
        —— 一个需要被规范化的名字与另一个名字会指向同一个目录，而盘上只会有一份。
        """
        return check_canonical_id(value, "workspace")

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        """**这里曾经没有校验，而文件名是从这个字段派生的。**

        ``Evidence`` 是会被落盘的那一个：``<account_id>.jsonl``、
        ``mirrors/<scope>/``。它此前连 ``AccountRecord`` 有的 ``check_slug_safe``
        都没有，所以 ``My Account 001`` 与 ``my-account-001`` 可以同时存在于
        模型层，然后在存储层静默合并进一个文件 —— 报告上只显示「证据更多了」。

        校验放在**模型层**而不是 ``_path_for``：只堵一个实现只保护那一个实现，
        而 ``adapters/jsonl`` / ``adapters/sqlite`` / 将来的第三个看到的应当是
        同一个 id 空间。
        """
        return check_canonical_id(value, "account_id")

    @field_validator("literal_value")
    @classmethod
    def _literal_must_be_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence.literal_value 不能为空 —— 空证据不是证据")
        if value != value.strip():
            raise ValueError("literal_value 首尾不得有空白，否则无法与来源做字面比对")
        return value

    @field_validator("fetched_at", "source_published_at")
    @classmethod
    def _moments_are_timezone_aware(cls, value: datetime | None) -> datetime | None:
        """两个时刻走同一条规则：**必须带时区**。

        为什么两个字段一起校验、而不是只校验新加的那个：它们要一起被读
        （「这份证据有多旧」= 发布时刻与取回时刻之差，§11 的到期判定还要拿它们
        减 ``now``）。一条路走过校验、另一条不走，差别不出现在写入的那一刻，而
        出现在**比较**的那一刻 —— naive 与 aware 相减直接抛 ``TypeError``，而
        那一次崩离原因很远。``fetched_at`` 此前也没有这条校验，于是「取回时刻是
        naive 的」可以一路进到库里；今天所有生产者给的都是 aware 值
        （``utc_now()`` / ``datetime.now(timezone.utc)``），所以这条校验没有改变
        任何一个现有取值，它挡的是下一个生产者。

        ## 只拒绝 naive，**不**归一成 UTC

        与场地资产池那三行同一条决定（判据是
        ``tests/test_models_invariants.py::test_an_offset_is_kept_rather_than_rewritten_to_utc``）：
        **写进去什么瞬间，读出来就是什么瞬间**。这里还多一层 ——
        ``source_published_at`` 是从来源的字面里读出来的，一个写着
        ``+01:00`` 的页面被改写成 ``Z`` 之后，「来源是这么说的」这句话就变了形。

        :func:`~marketops.domain.util.to_utc` 走另一条路，而**它的理由在这里
        不成立**：那条规则挡的是「时刻被格式化成 ISO 字符串存进**列**，而字符串
        的大小顺序只在偏移量相同时才等于瞬间的先后」。这两个时刻不是列 ——
        ``Evidence`` 整条以 ``payload`` 一列 JSON 落盘，没有任何查询按它们排序
        （见 ``adapters/sqlite/evidence_store.py`` 的「为什么 payload 是一列
        JSON 文本」）。所以这里既不缺那条规则要挡的东西，也不该承担它的副作用。
        """
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "fetched_at / source_published_at 必须带时区 —— "
                "naive 与 aware 相减会抛 TypeError，而那是离原因很远的一次崩"
            )
        return value


def evidence_id(
    *,
    account_id: str,
    source_url: str,
    field: ContactField | None,
    literal_value: str,
    digest: str,
) -> str:
    """证据 id 是内容寻址的：同一处字面证据重复出现时 id 相同，写入自然幂等。

    ## 为什么这条规则住在这里

    它此前是 ``domain/bind.py`` 的一个私有函数。今天证据有**两个**生产者
    （``bind.py`` 的 ContactRoute 绑定，与 ``application/evidence_ingest.py``
    的文档级材料），而「同一份证据在两个生产者手里必须是同一个 id」这件事只有
    在一处写才成立 —— 各写一份的漂移表现是同一份材料有两个 id，于是「同一次
    重发」被读成「两条不同的证据」，而报告上两边都正常。

    ## ``field=None`` 占一个空串格子

    ``ContactField`` 的四个取值都不是空串，所以「不挂字段」（文档级证据）与
    四个字段各占一格、互不相撞。撞了的表现不是报错而是静默合并 —— 第二个写入
    撞上「同 id 配不同内容」，而那看起来像 id 被手工指定过。

    ``account_id`` 进原像是**承重**的：去掉它之后，「重复抓取是幂等的」那条判据
    仍然全绿，而两个账号在同一页上发现的同一处字面证据会共用一个 id。判据在
    ``tests/test_models_invariants.py`` 上逐个入参变异一次。
    """
    return sha256_text(
        "\x1f".join(
            [
                account_id,
                source_url,
                field.value if field is not None else "",
                literal_value,
                digest,
            ]
        )
    )[:32]


@dataclass(frozen=True)
class EvidenceMoments:
    """一组证据行**共同**支持的时刻。

    只有两个格，因为今天只有这两个能从材料里读出来（见
    :func:`moments_from_evidence`）。「重新验证时刻」与「有效区间」不是材料的
    性质，是**判断**的产物，它们今天没有生产者 —— 所以这里不替它们留一个
    永远是 ``None`` 的格子假装有。
    """

    first_seen_at: datetime | None
    source_published_at: datetime | None


def moments_from_evidence(rows: Sequence[Evidence]) -> EvidenceMoments:
    """把一组证据行折成路线那一行要带的两个时刻（§9）。

    ## ``first_seen_at`` 取**最早**的那一次取回

    ``fetched_at`` 是「我们什么时候拿到这份材料」。同一页上抽出来的每一行带的是
    同一个值，所以一组同源的行折出来就是它。跨材料时（一条路线由两页的材料支撑）
    取最早的仍然成立：它第一次被看到，就是两页里更早的那一次。取**最新**的会把
    「这条路线我们早就见过」读成「今天刚见到」—— 而没有这一格的下游只能靠再抓
    一次来确认，于是它去重验一件刚看过的事。

    ## ``source_published_at``：**声明过的**那些去重后只剩一个才取它

    它是**这一页**的性质（``extract.page_publication_time`` 已经在一页之内做过
    同一个判断），所以同源的行折出来只有一个值。多份材料时按三档走：

    * 只有一份材料声明过 → 那个时刻（另一份沉默**不是**另一个取值：沉默没有说过
      「它也是这个日期」，也没有说过别的，它什么都没说）；
    * 一份都没有 → ``None``；
    * 两份以上各自声明了**不同**的时刻 → ``None``，不挑。挑一个（哪怕挑更近的）
      就是替这些材料认领「哪一天管这一处内容」，而依据不在材料里 —— 与
      ``page_publication_time`` 在同一页上遇到两个日期时的处置是同一条纪律，
      只是搬到了上一层。

    ``None`` 在这里不表示「没有时刻」，它表示「没有**唯一的**那一个」，
    而 §9 的读法是模糊不清就不是当前。

    空行集合同样两个格都是 ``None``：没有任何材料，就没有任何时刻。
    """
    if not rows:
        return EvidenceMoments(first_seen_at=None, source_published_at=None)
    declared = {row.source_published_at for row in rows if row.source_published_at is not None}
    return EvidenceMoments(
        first_seen_at=min(row.fetched_at for row in rows),
        source_published_at=next(iter(declared)) if len(declared) == 1 else None,
    )


class ResearchRun(_Strict):
    """一次研究运行的元数据。落盘后成为可复现记录的一部分。"""

    run_id: str
    profile: str
    started_at: datetime
    finished_at: datetime | None = None
    accounts_total: int = 0
    accounts_completed: int = 0
    # 同 ContactRoute：``Literal`` 让约束同时进类型、校验与契约。
    research_only: Literal[True] = True
    send_authorized: Literal[False] = False


__all__ = [
    "AccountRecord",
    "Evidence",
    "EvidenceMoments",
    "ResearchRun",
    "evidence_id",
    "moments_from_evidence",
]

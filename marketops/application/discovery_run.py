"""§2 的 ``discovery_scope`` 跑法：发查询、取页面，把结果写回一份**可以重新喂进
受理**的池子。

# 这一格此前是空的，而「空」在这件事上的表现不是报错

``marketops/application/inputs.py`` 的 ``_NO_PRODUCER`` 那句话从今天起改口。
零件一直都在（展开器 ``openweb.py``、配对键 ``pairing.py``、投影 ``qualify.py``、
上限 ``caps.py``），缺的是**把它们串成一圈的那条路径**。没有它的时候，一次
``discovery_scope`` 的受理以 ``BLOCKED`` 收场，理由是一句诚实话；而不诚实的那一种
写法是「跑了、没搜到」—— 两者在报告里长得一样。所以这一格的空不是「少一个功能」，
是**一整类输入根本没有生产者**。

# 这一圈是哪五步

1. :func:`~marketops.application.openweb.build_open_web_queries` —— 检索式来自
   profile 的模板 × 词表，**不是**本跑法现编的；
2. :meth:`~marketops.application.budget.RunBudget.for_scope` +
   :class:`~marketops.application.billed_search.BilledSearch` —— 上限从**冻结的
   profile** 来，额度扣在起飞之前（与账号那一侧同一条规则，只是换了个主语）；
3. 每条命中折成一台**站点**（canonical host）：同一台主机只留第一条命中，
   其余的进 :attr:`DiscoverySite.also_seen_urls`；
4. 逐站点**有界地**取页面 → 解析 → 落成证据行（``evidence_ingest``）→ 用这一页
   **自己写下的** ``<title>`` 给这一行一个指称对象；
5. 候选行 → :func:`~marketops.application.qualify.qualify_candidate`（判定只进
   报告，**不进池子**）→ 按 ``max_candidates`` 截断 → 留下来的写成一份 ``.jsonl``
   工件，再由 :func:`~marketops.application.motherpool.read_motherpool` 读成一份回执。

第 5 步刻意复用母池那一套读入，而不是另写一份「把候选写出去」：工件 → 回执这条
路已经有一条测试过的实现，它免费给出重放幂等（同一份字节 ⇒ 同一个回执 id）、
每一行一个处置、以及 ``artifact_ref`` / ``artifact_sha256`` 两个可核对的字段。
再写一份，就会有第二处需要同步维护的「每一行去了哪里」。

# 这一轮**没有**确立的东西（逐条进 :attr:`DiscoveryRun.caveats`）

* **它没有搜遍全网。** 检索式是 profile 模板展开出来的那几条，停止条件是显式的
  （:class:`DiscoveryStop`），而每一条停止理由回答的都是「这一次运行停下来时是什么
  读数」，没有一条等于「网络里没有别的了」。
* ``organisation`` 与 ``venue_name`` 是**同一次读取的同一个值** —— 这一页自己写下
  的标题。这一行说的是「``<主机>`` 上的一份材料自称 X」，**不是**「X 拥有这个站点」。
  把后者读进去正是聚合站那条缝：英国基层俱乐部大量挂在目录站上，而那种页面的标题
  是**目录站的**标题。
* ``official_domain`` 是**取回它的那台主机**，不是一次「这是它的官网」的判定。它带着
  一条真的证据（那次取回的字节），而 ``pairing_key_for`` 要的正是「一条指着它的
  证据」—— 这条链证明的是「我们在那台主机上读到过东西」，不是别的。
* 同一台主机下的**其余命中不去取**（``also_seen_urls`` 记着它们）。代价写在这里：
  一台主机上挂着多个单位时，这一轮只看得见第一条命中指向的那一个。
* ``venue_environment`` / ``organisation_kind`` / ``surface_class`` / ``country``
  四格由**发现期**产出（``application/classification.py``：这一页的正文与它所在的
  主机名，词表来自 profile 的 ``classification`` 块），随工件列与候选行走到下游。
  分类产出的是**结论**：读出来是某个取值，读不出来是 ``unknown``——而 ``unknown``
  是正式写法、不是空串（空串在规则方言里读作「记了，是空」）。但**这一轮并没有
  查过那些页面没提到的事**：一个基层俱乐部的页面不写「我们是天然草」，读出来的
  就是 ``unknown``，于是点它的规则进 ``unchecked``、账号停在 ``discovered``
  ——这正是 :meth:`DiscoveryRun.qualification_summary` 的 ``by_reason`` 里那一批
  ``UNRESOLVED_FACTS``。把这一节读成「四格都填上了，所以判定都做完了」是这一份
  报告最容易被读错的地方。词表只覆盖**这一份 profile 要认的那些说法**，其余一律
  ``unknown``——那句话不是「这一页什么都没说」，是「这一页没说过认识的说法」。
* **抓取这一侧不受额度账本约束。** MVP 只扣检索（``domain/enums.py`` 的
  ``BudgetLine``），所以这一轮的有界性来自三个**结构**上的界（模板展开出的检索式条数、
  每条检索式的 ``limit``、进池子的 ``max_candidates``），不是来自一条会响的额度线。
  一条 200 条命中、每条都指向新主机的检索式在这里最坏会变成 ``max_candidates`` 次
  抓取 —— 这个数是界，但它不是被账本守住的。要让它被守住，得先有抓取的科目与它的
  消费者。
* 本池按 SOP L82 属**补充证据**，不是母池。每一行都带 ``source_method_id``，
  而报告里那句话不许被读掉。

# 从工作单元回到「我们读过哪几页」

回执那一行给的是 ``row_id``（本仓这一行的句柄）与 ``source_identity``
（``site:<主机>``）。发现期的证据是按 :attr:`AccountCandidate.candidate_id` 落的盘
（那个模型的 docstring 写明「发现期的证据会经由同一个 store 落盘，候选 id 将来就是
它」），而 ``candidate_id = site-<sha256(主机)[:16]>`` 由 :func:`candidate_id_for`
一处派生。所以这条链是**一步推导**：``identity_tail(source_identity)`` → 主机 →
:func:`candidate_id_for` → ``EvidenceStore.list_evidence``。

工件那一行还逐字带着 ``evidence_ids`` 与 ``title_evidence_id``，所以读工件的人不必
推。**回执里没有这两格** —— ``SourceReceipt`` 的契约里没有证据指针，而回执才是工作
单元的输入。于是 worker 拿到的是身份线索，不是发现期读过的那些页面。这是**已知缺口**，
记在验收报告的 E2 一节，不在这里悄悄补一个字段。

# 出口的纪律

:func:`materialize_pool` 在一行都没有时**不写工件**，返回 ``None``：一份空工件不是
一次成功的母池读入（``application/motherpool.py`` 就是这么拒绝它的），而「跑了、
没搜到」与「跑了、一行都没成」必须由 :class:`DiscoveryStop` 说清楚，不能靠一个空文件
表达 —— 那正是「empty list 伪装 completed」。

:func:`run_discovery` 在花掉**任何一次检索之前**先拒绝已存在的工件名：名字在跑之前
就是已知的，而「付过钱之后才发现这个名字被占用」是一次已经花了钱的失败。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from ..domain.budget import BudgetUsage
from ..domain.candidate import AccountCandidate, new_candidate
from ..domain.domain_attribution import (
    DomainFinding,
    OfficialDomainVocabulary,
    PageObservation,
    attribute_official_domain,
)
from ..domain.enums import DomainVerdict, FetchOutcome, QueryOutcome
from ..domain.errors import (
    AccessBlockedError,
    ConfigError,
    ParseError,
    ParserUnavailableError,
    SearchError,
)
from ..domain.profile import (
    CLASSIFICATION_AXES,
    CLASSIFICATION_UNKNOWN,
    ClassificationVocabulary,
    ProfileBundle,
)
from ..domain.receipt import SourceReceipt
from ..domain.util import (
    FILENAME_ID_MAX_LENGTH,
    check_canonical_id,
    sha256_text,
    utc_now,
)
from ..ports.acquisition import FetchMode, FetchRequest, FetchResult
from ..ports.budget import BudgetLedger
from ..ports.documents import ParsedDocument
from ..ports.search import SearchHit, SearchProvider
from ..ports.store import EvidenceStore
from .acquisition import DocumentAcquisition
from .billed_search import BilledSearch, QueryReceipt
from .budget import RunBudget
from .caps import TruncationDecision, apply_cap
from .classification import AssetClassification, classify
from .document_router import DocumentRouter
from .evidence_ingest import document_evidence, title_evidence
from .motherpool import read_motherpool, usable_name, write_receipt
from .openweb import OPEN_WEB_SOURCE_METHOD, OpenWebPlan, OpenWebQuery, build_open_web_queries
from .pairing import canonical_host, pairing_key_for
from .qualify import CandidateQualification, qualify_candidate
from .queries import market_query

#: 工件的后缀。``find_artifact`` 按 ``.jsonl`` 先试，所以这个名字是它认得的。
ARTIFACT_SUFFIX = ".jsonl"

#: 证据键（``Evidence.account_id``）的前缀。它与 ``AccountCandidate.candidate_id``
#: 是同一个字符串 —— 理由见模块 docstring 的「从工作单元回到我们读过哪几页」。
CANDIDATE_ID_PREFIX = "site-"

#: ``source_identity`` 的命名空间。**上游对象是一台主机**，而主机名就是它在网络里
#: 自己的名字 —— 这里不拿本仓的哈希去冒充它（``row_id`` 才是本仓的句柄）。
SOURCE_IDENTITY_NAMESPACE = "site"

#: 分类的四格 —— 「candidate URL → 抓取 → 抽取资产事实」那一步的产出。
#:
#: 住在这里而不是 ``domain/facts.py``：那一份是**引擎认识的事实名**，而这一份是
#: 「工件里哪几列装的是分类读数」。两者今天逐字同名（这正是不需要映射表的原因），
#: 但它们是两件事 —— 前者的消费者是求值循环，后者的消费者是回执读取器。
CLASSIFICATION_COLUMNS: tuple[str, ...] = CLASSIFICATION_AXES

#: 这一轮的候选行上**没有生产者**的准入事实 —— 点它们的规则永远进 ``unchecked``。
#:
#: 两份都在这一层定，因为这一层是候选行的**构造者**：``_candidate_from`` 决定这一行
#: 带哪些值，而这两格它一个都不填（那四格分类读数是另外一回事，见
#: :data:`CLASSIFICATION_COLUMNS`）。逐条：
#:
#: * ``has_notes`` —— 候选行上**连字段都没有**。备注是 operator 写下来的东西，
#:   发现期没有任何一步产生它，也不该产生（``application/qualify.py`` 的
#:   ``_UNPRODUCED_ACCOUNT_FACTS`` 把它的取值钉成 ``None``）。
#: * ``surface_evidence_confirmed`` —— 字段在（``AccountCandidate`` 上，三值），
#:   而**这一条路径不填它**：它是「表面判定被证据确认到了什么程度」，而发现期做的是
#:   **分类**（这一页说了什么），不是判定「这份材料够不够格作证」。把分类读数直接
#:   抄进这一格，等于让「页面提到 grass」变成「已确认是天然草」—— 而
#:   ``artificial_surface_only`` 那条规则问的恰好是后者。
#:
#: 两者都不决定任何子句（``None`` 在任何极性下都不决定），所以它们的可见后果是
#: 「那条规则没判」。这一份清单存在的理由就是让那句话**在报告里读得出来**，
#: 而不是只活在某个人的记忆里。
_NO_CANDIDATE_PRODUCER: tuple[str, ...] = ("has_notes", "surface_evidence_confirmed")

#: 每一次运行都成立的三句读数。写成常量是为了让它们**只写一次**：一句在报告里，
#: 另一句在验收报告里，两处措辞漂开之后，被引用的那一句就不再是这一句了。
_ALWAYS_CAVEATS: tuple[str, ...] = (
    "这一轮没有搜遍全网：检索式是 profile 模板展开出来的那几条，"
    "停止条件是显式的（见 stops 与 queries 两节），没有一条等于「网络里没有别的了」。",
    "这一份池子按 SOP L82 属**补充证据**，不是母池：它的每一行都来自检索，"
    "而检索结果不能当全量母池。每一行都带 source_method_id，读它的人不该把这句读掉。",
    "抓取这一侧不受额度账本约束 —— MVP 只扣检索。这一轮的有界性来自三个结构上的界"
    "（检索式条数、每条检索式的 limit、进池子的 max_candidates），不是一条会响的额度线。",
)


def candidate_id_for(site: str) -> str:
    """一台主机 → 这一轮的发现期身份。**只在这里派生一次。**

    ``<prefix><sha256(主机) 的前 16 位十六进制>``，而不是把主机名 slug 一下：
    规范形式的判据是 ``slugify(id, max_length=60) == id``，而一个长主机名 slug 之后
    会被**截断** —— 两个不同的主机落到同一个文件名上，store 不报错，两条候选的证据
    被静默合并。哈希对所有主机一视同仁：要么都一样短，要么都落不了盘。

    主机名本身不因此丢掉：它在 ``official_domain`` / ``pairing_key`` 上，也在工件的
    ``source_identity``（``site:<主机>``）里。这一格是**本仓的句柄**，不是主机名。
    """
    return f"{CANDIDATE_ID_PREFIX}{sha256_text(site)[:16]}"


def source_identity_for(site: str) -> str:
    """一台主机 → 母池那一行认的来源标识（``site:<主机>``）。

    形状照着 ``domain/receipt.py`` 的约定（``<namespace>:<value>``），取值对引擎不透明
    —— 只不过这里的「上游」是我们自己那一步检索，而对象的身份就是它自己的主机名。
    ``identity_tail`` 因此给出主机名，``usable_name`` 因此会拒掉「名称那一格写的就是
    主机名」的行。
    """
    return f"{SOURCE_IDENTITY_NAMESPACE}:{site}"


class DiscoveryStop(str, Enum):
    """这一轮**停下来时**的读数。**一个元组，不是一个值。**

    一次运行可以同时是「预算用尽」与「上限达到」，而只留一个的话，报告上少掉的那一个
    永远不会被人发现。所以 :attr:`DiscoveryRun.stops` 是元组，而 :attr:`stopped`
    只说「有没有停下来过」。

    **没有一条成员等于「网络里没有别的了」。** 它们说的是这一次运行、在这一次声明的
    范围里发生了什么：

    ====================  ===================================================
    ``BLOCKED``           跑过的每一次抓取都被目标主机拒了（终态，不重试、不绕）
    ``PROVIDER_FAILED``   发出去的每一条查询都失败了 —— 一条结果都没回来
    ``BUDGET_EXHAUSTED``  检索式还没发完，额度就用尽了
    ``CAP_REACHED``       池子在上限处截断了（丢掉的那几条记在 ``truncation`` 里）
    ``NO_EVIDENCE``       这一次没有落成任何一行证据 —— 没有可引用的材料
    ``NO_NAMED_SITE``     有站点、却没有一台给出可用的名字（池子因此是空的）。
                          「池子在上限处被截空」不是这一条，那是 ``CAP_REACHED``
    ====================  ===================================================
    """

    BLOCKED = "blocked"
    PROVIDER_FAILED = "provider_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CAP_REACHED = "cap_reached"
    NO_EVIDENCE = "no_evidence"
    NO_NAMED_SITE = "no_named_site"


class HitOutcome(str, Enum):
    """一条命中折成了什么。**每一条命中都要有一个去向**，没有例外。

    ``SITE`` 是一条新的站点；``SAME_SITE`` 是同一台主机的第二条命中（它进
    ``also_seen_urls``，不另起一行）；``UNUSABLE_URL`` 是折不出主机的 URL
    （``canonical_host`` 抛的那些：非 http(s)、单标签主机、IP 字面量、带 userinfo）。

    第三种必须**有名有姓地**记下来：一次检索回来的十条里有一条折不出来，与那一条
    不存在，在计数上同形 —— 而前者是一次要有人看的解析失败。
    """

    SITE = "site"
    SAME_SITE = "same_site"
    UNUSABLE_URL = "unusable_url"


@dataclass(frozen=True)
class DiscoveryScope:
    """一轮发现的**冻结范围**：它是这一次运行的输入，也是它的名字。

    ``scope`` 同时是三样东西，所以它在这里只验一次：工件的名字、检索镜像与页面镜像的
    子目录名（``SearchQuery.scope`` / ``FetchRequest.scope``）、以及预算账本的
    scope 尾段（``<profile>:<scope>``）。三处都要求它是 ``slugify`` 的不动点，
    而三处各验一次必然有两处先漂移。

    Args:
        scope: 这一轮的名字。规范形式（``check_canonical_id``）。
        region: 绑定到 ``{region}`` 的冻结值。**它的判据不在这里** ——
            ``openweb.build_open_web_queries`` 是那一处的唯一实现，本类不复制一份。
        limit: 每条检索式要多少条结果。小于 1 拒绝：一条「要 0 条结果」的检索式
            什么也产不出，而它会在报告里长得像一次正常的空检索。
        max_candidates: 进池子的候选上界。**没有「不封顶」那一档** ——
            有界正是这个参数存在的理由。0 是合法的（池子里一个都不留），
            它与「不封顶」不是一回事。
        fetch_pages: 要不要真的去取页面。``False`` 时只发检索、不取页面，
            于是没有任何一行能得到名字，池子是空的 —— 这是一个**读得出来**的结果
            （``NO_EVIDENCE`` 与 ``NO_NAMED_SITE`` 两条停止理由都成立），不是一次
            安静的失败。它存在的理由是让「检索这一侧接线了没有」这件事可以单独验证。
    """

    scope: str
    region: str
    limit: int = 10
    max_candidates: int = 25
    fetch_pages: bool = True

    def __post_init__(self) -> None:
        check_canonical_id(self.scope, "scope")
        if self.limit < 1:
            raise ValueError(
                f"limit 必须至少是 1（收到 {self.limit}）。一条要 0 条结果的检索式"
                "什么也产不出，而它会在报告里长得像一次正常的、只是没有命中的检索。"
            )
        if self.max_candidates < 0:
            raise ValueError(
                f"max_candidates 不能是负数（收到 {self.max_candidates}）。"
                "不封顶没有对应的取值 —— 有界是这一格存在的理由。"
            )

    @property
    def artifact_ref(self) -> str:
        """池子工件的名字。与 :attr:`scope` 是同一个字符串：一个运行一个名字。"""
        return self.scope

    def as_dict(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "region": self.region,
            "limit": self.limit,
            "max_candidates": self.max_candidates,
            "fetch_pages": self.fetch_pages,
        }


@dataclass(frozen=True)
class HitRecord:
    """一条命中，以及它去了哪里。**检索这一侧的逐条台账。**

    它不进池子，也不进工件：它是「这次搜索到底回了什么」的答案，而那个答案只属于
    这一次运行。``note`` 在 ``UNUSABLE_URL`` 时是那个 URL 为什么折不出主机。
    """

    url: str
    rank: int
    query: str
    outcome: HitOutcome
    site: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "rank": self.rank,
            "query": self.query,
            "outcome": self.outcome.value,
            "site": self.site,
            "note": self.note,
        }


@dataclass(frozen=True)
class DiscoverySite:
    """一台主机，以及这一轮对它做了什么。**池子的单位是站点，不是 URL。**

    同一台主机下的第二条命中不另起一行，而是进 :attr:`also_seen_urls`：池子的单位与
    配对键（``pairing_key`` = canonical host）是同一个，一行一台主机，于是「池子里有
    几行」与「有几台主机」是同一个数。代价写在模块 docstring 里。

    ``fetch_outcome`` 的 ``None`` 是**没有去取**（不是因为上限停下、就是因为
    ``fetch_pages=False``），而 ``reason`` 在那种情况下说得出是哪一种。
    """

    site: str
    candidate_id: str
    url: str
    query: str
    rank: int
    also_seen_urls: tuple[str, ...] = ()
    fetch_outcome: FetchOutcome | None = None
    tier: str = ""
    http_status: int | None = None
    body_sha256: str = ""
    #: 指称对象：这一页**自己写下**的标题，逐字（来自 ``title_evidence`` 那一行）。
    #: 没有可用的名字时是 ``""`` —— 那时这一行不进池子，但它落过的证据仍然在。
    name: str = ""
    evidence_ids: tuple[str, ...] = ()
    title_evidence_id: str = ""
    reason: str = ""
    #: 这一页读出来的**资产事实**（``classification.py`` 的产出）。``None`` 是
    #: **没有读到可读的正文**（没取到 / 被挡 / 解析失败）—— 与「读了、四格都是
    #: unknown」不是一件事：后者至少有 ``venue_environment`` 那种从主机名或标题上
    #: 读出来的读数。这一格进工件、进候选行、进报告，是「候选 → 场地资产」那一段的
    #: 全部输入。
    classification: AssetClassification | None = None
    #: **这个组织的官网是哪一个**（``domain/domain_attribution.py`` 的产出）。
    #: ``None`` 是**这一页没有可判定的材料**（没取到 / 被挡 / 解析失败）——
    #: 与「读了、判不出来」不是一件事，理由与 :attr:`classification` 逐字相同。
    #:
    #: 它填的是 :attr:`~marketops.domain.entities.VenueAsset.verified_official_domain`
    #: 那一格，**不是** ``claimed_official_domain``，也**不是**「取回这一页的那台主机」
    #: —— 后者是 :attr:`site`，一次观察；把观察当判定用，两个方向都会错（社媒页上
    #: 那个真实域名会被判成外来域，而目录站的主机会被当成它自己的域名）。
    domain_finding: DomainFinding | None = None

    @property
    def named(self) -> bool:
        """这一台有没有给出一个可用的指称对象。"""
        return bool(self.name)

    def classification_values(self) -> dict[str, str]:
        """四格读数。没有分类时四格都是 ``unknown`` —— **不返回空映射**：
        「这一页没读出来」与「这一页读出来是 unknown」在报告上必须分得开，
        而分得开它们的是 ``classification is None``，不是这里的取值。"""
        if self.classification is None:
            return {axis: CLASSIFICATION_UNKNOWN for axis in CLASSIFICATION_AXES}
        return self.classification.groups()

    def domain_values(self) -> dict[str, object]:
        """官网判定的五格（工件列）。**没有判定材料时也写 ``unknown``，不写空。**

        与 :meth:`classification_values` 同一条纪律：``domain_verdict`` 是**正式结论**
        （``unknown`` 就是「没判定」的正式写法），而不写这一列与「读了、是 unknown」
        在回执那一侧分不开 —— 回执那一侧是资产那一层唯一的路。
        """
        finding = self.domain_finding
        if finding is None:
            return {
                "verified_official_domain": "",
                "verified_official_domains": "",
                "domain_verdict": DomainVerdict.UNKNOWN.value,
                "domain_evidence_ids": "",
                "domain_reason": (
                    "这一页没有可判定的材料 —— 要么没有取到可读的正文，要么这一轮的"
                    "profile 没有声明官网判定的参数（``official_domain`` 块）。"
                    "两种情况下这一格都是 unknown（没判定），不是「它没有官网」。"
                ),
            }
        return {
            # 单数那一格**只在恰好一个时给值**。两个官网（主站 + 分区站）是常见的，
            # 而随便挑一个会让「这个组织的官网」在两次运行里给出两个不同的答案，
            # 两次在报告上都看起来正常。全部集合在下一格。
            "verified_official_domain": finding.confirmed_domain,
            "verified_official_domains": ",".join(finding.official_domains),
            "domain_verdict": finding.verdict.value,
            "domain_evidence_ids": ",".join(finding.evidence_ids),
            "domain_reason": finding.reason,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "site": self.site,
            "candidate_id": self.candidate_id,
            "url": self.url,
            "query": self.query,
            "rank": self.rank,
            "also_seen_urls": list(self.also_seen_urls),
            "fetch_outcome": self.fetch_outcome.value if self.fetch_outcome else "",
            "tier": self.tier,
            "http_status": self.http_status,
            "body_sha256": self.body_sha256,
            "name": self.name,
            "evidence_ids": list(self.evidence_ids),
            "title_evidence_id": self.title_evidence_id,
            "reason": self.reason,
            "classification": (
                self.classification.as_dict() if self.classification is not None else None
            ),
        }


@dataclass(frozen=True)
class DiscoveryRun:
    """一次发现运行的全部读数。**内存里的报告，不是盘上的产物。**

    盘上的产物有三样，去向不同：工件与回执在 ``inputs_dir``（它们要能被重新喂进受理），
    运行报告在 ``benchmarks_dir``（它是读数，不是输入）。
    """

    run_id: str
    scope: DiscoveryScope
    profile_id: str
    created_at: datetime
    plan: OpenWebPlan
    queries: tuple[dict[str, object], ...]
    hits: tuple[HitRecord, ...]
    sites: tuple[DiscoverySite, ...]
    pool: tuple[DiscoverySite, ...]
    truncation: TruncationDecision
    qualifications: tuple[CandidateQualification, ...]
    usage: tuple[BudgetUsage, ...]
    stops: tuple[DiscoveryStop, ...]
    caveats: tuple[str, ...]
    #: 这一轮的分类器**能**命中的排除项 id（``ClassificationVocabulary.exclusion_ids``）。
    #:
    #: 它随运行一起走，因为它是这一次运行的**产物**：工件里那一列 ``exclusions_hit``
    #: 是分类器写的，而读回执时「这一列里出现的 id 是不是这一份 profile 声明过的」
    #: 要靠它来判（``application/motherpool.py::dispositions_from``，命中未声明的 id
    #: 整份拒绝）。放在运行上而不是让 :func:`materialize_pool` 自己读 profile：
    #: 那一层收的是 ``(inputs_dir, run)``，让它去读 profile 就等于让「工件里那一列
    #: 是谁写的」有两处答案。词汇表为空（profile 没有 ``classification`` 块）时是
    #: 空元组 —— 那时分类器一格都不产，工件里也没有那一列。
    declared_exclusion_ids: tuple[str, ...] = ()

    @property
    def stopped(self) -> bool:
        return bool(self.stops)

    @property
    def artifact_ref(self) -> str:
        return self.scope.artifact_ref

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        """这一次落成的**全部**证据行，按站点顺序。

        比池子那一侧多：一台取回了材料却没有给出名字的主机同样落了证据（它确实读过），
        而它不进池子。两件事分开之后，「我们读过的东西」与「我们拿去研究的行」不再
        是同一个数 —— 它们本来就不是。
        """
        return tuple(
            evidence_id for site in self.sites for evidence_id in site.evidence_ids
        )

    def hit_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for hit in self.hits:
            counts[hit.outcome.value] = counts.get(hit.outcome.value, 0) + 1
        return counts

    def fetch_counts(self) -> dict[str, int]:
        """抓取结果的分布。**没有去取的那几台单独一格** —— 它们不是一种抓取结果。"""
        counts: dict[str, int] = {}
        for site in self.sites:
            key = site.fetch_outcome.value if site.fetch_outcome else "not_fetched"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def query_counts(self) -> dict[str, int]:
        """检索式的分布，与 :meth:`hit_counts` / :meth:`fetch_counts` 同一个形状。

        ## 「发了几条」只能在这一格里读

        :attr:`queries` 的**条数**是计划展开出来的条数，不是发出去的条数 ——
        没轮上的那些检索式同样占一行（它们必须占，否则「跑了 20 条」与「542 条里
        跑了 20 条」在报告上长得一样）。所以「这一次到底发出去了几条」若要从条数
        上读，读到的数是错的，而它恰恰是这一轮最要紧的一个数。

        ``SENT`` / ``SKIPPED`` / ``FAILED`` / ``EXHAUSTED`` 四档各自成格的理由写在
        :class:`~marketops.domain.enums.QueryOutcome` 上；``not_attempted`` 是第五格
        ——「没轮上」不是一种 ``QueryOutcome``，它是这一层的话（那几条查询根本没
        走到记账那一步）。
        """
        counts: dict[str, int] = {}
        for reading in self.queries:
            key = str(reading.get("outcome", ""))
            counts[key] = counts.get(key, 0) + 1
        return counts

    def qualification_summary(self) -> dict[str, object]:
        """候选行的准入判定**分布**，以及那些没被判的规则。

        ``unchecked_rules`` 非空是常态而不是异常，而它**今天有两个来源**，都必须
        看得见：

        * 这一页没有说过词表认得的说法 ⇒ 四格里至少一格是 ``unknown`` ⇒ 投影成
          未确认 ⇒ 点名它的规则一条都判不了。这是**证据不足**，是本轮的主要形态；
        * 某一格在候选行上**没有生产者**（``facts_without_a_producer``）⇒ 点它的
          规则同样判不了。这是**零件不足**。

        两者在判词上同形（``UNRESOLVED_FACTS``），所以报告必须把它们分开列：
        一个是「再去查」，另一个是「再去写代码」，而修法完全不同。

        ``by_reason`` 里 ``no_rule_fired`` 的那一批才是「排到结论为止每一条都判了、
        一条都没命中」—— 那是「合格」在这一层唯一的机器可读形态，
        **不是** ``discovered`` 这个状态本身：停在 ``discovered`` 的行绝大多数是
        ``unresolved_facts``，把两者合并读会得出「一批合格的账号」这个错误结论。
        """
        by_status: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        unchecked: set[str] = set()
        for qualification in self.qualifications:
            status = qualification.status.value
            by_status[status] = by_status.get(status, 0) + 1
            reason = qualification.reason.value
            by_reason[reason] = by_reason.get(reason, 0) + 1
            unchecked.update(qualification.outcome.unchecked)
        return {
            "count": len(self.qualifications),
            "by_status": by_status,
            "by_reason": by_reason,
            "unchecked_rules": sorted(unchecked),
            "facts_without_a_producer": list(_NO_CANDIDATE_PRODUCER),
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "profile_id": self.profile_id,
            "created_at": self.created_at.isoformat(),
            "scope": self.scope.as_dict(),
            "source_method": OPEN_WEB_SOURCE_METHOD.id,
            "source_family": OPEN_WEB_SOURCE_METHOD.family,
            "artifact_ref": self.artifact_ref,
            "plan": self.plan.summary(),
            "queries": list(self.queries),
            "hits": {
                "count": len(self.hits),
                "by_outcome": self.hit_counts(),
                "records": [hit.as_dict() for hit in self.hits],
            },
            "sites": {
                "count": len(self.sites),
                "fetched": self.fetch_counts(),
                "named": sum(1 for site in self.sites if site.named),
                "records": [site.as_dict() for site in self.sites],
            },
            "pool": {
                "kept": len(self.pool),
                "candidate_ids": [site.candidate_id for site in self.pool],
                "truncation": self.truncation.as_dict(),
            },
            "evidence": {
                "count": len(self.evidence_ids),
                "ids": list(self.evidence_ids),
            },
            "qualifications": self.qualification_summary(),
            "usage": [
                {
                    "line": usage.line.value,
                    "ceiling": usage.ceiling,
                    "spent": usage.spent,
                    "reserved": usage.reserved,
                    "refunded": usage.refunded,
                }
                for usage in self.usage
            ],
            "stops": [stop.value for stop in self.stops],
            "caveats": list(self.caveats),
        }


@dataclass(frozen=True)
class DiscoveryPool:
    """池子落盘之后的三样东西：工件、回执，以及它们在哪。"""

    artifact_ref: str
    artifact_path: Path
    receipt_path: Path
    receipt: SourceReceipt

    def as_dict(self) -> dict[str, object]:
        return {
            "artifact_ref": self.artifact_ref,
            "artifact_path": str(self.artifact_path),
            "receipt_path": str(self.receipt_path),
            "receipt": self.receipt.model_dump(mode="json"),
            "counts_line": self.receipt.counts_line,
            "blocker": self.receipt.blocker,
        }


# --- 跑一轮 -------------------------------------------------------------------

#: ``run_id`` 里时刻那一段的形状：UTC、秒级。``datetime.strftime`` 会把它写成大写
#: 的 ``T``/``Z``，而 :func:`_run_id_for` 会把它折成小写 —— 理由见那里。
_RUN_ID_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def _run_id_for(scope: DiscoveryScope, started: datetime) -> str:
    """这一轮的名字：``<范围名>-<UTC 秒级时刻>``，**已经是它自己的规范形式**。

    这不是一个装饰性的保证。``run_id`` 会被拼成文件名（``benchmarks/<run_id>.json``），
    所以它落在 ``domain/util.py::check_canonical_id`` 管的那一类字段里 ——
    ``get_report`` 读一份报告时走的是**同一条判据**（``adapters/mcp_server/tools.py``
    的 ``_report`` 逐字调它）。两半用同一条判据，前提是产出这一半也满足它。

    ## 为什么时刻那一段要小写

    ``check_canonical_id`` 判的是「这个值是不是 ``slugify`` 的不动点」，而
    ``slugify`` 会把大写折成小写。``strftime('%Y%m%dT%H%M%SZ')`` 产出的
    ``20260929T091920Z`` 因此在写入侧成立、在读取侧不成立：报告照样落盘，而
    ``get_report`` 会以「不是规范形式」拒绝读它 —— 客户端那边看起来就是「这一轮
    没有报告」，而盘上明明有一份。折成小写之后两半看的是同一个名字。

    ## 为什么太长就拒绝，而不是截断

    ``scope`` 本身已经是规范形式（``DiscoveryScope.__post_init__`` 验过），
    至多 :data:`FILENAME_ID_MAX_LENGTH` 个字符；再加上时刻那一段就可能越界，
    而越界的 id 会被 ``slugify`` **截断**。截断不是一一映射 —— 两个不同的范围名
    会落到同一份报告上，而报告上只显示「内容更多了」。所以这里拒绝，并说清是
    范围名太长：它在**花掉任何一次检索之前**发生，改一个名字就能重跑。

    Raises:
        ConfigError: 这个范围名加上时刻超出了 :data:`FILENAME_ID_MAX_LENGTH`。
    """
    run_id = f"{scope.scope}-{started.strftime(_RUN_ID_STAMP_FORMAT).lower()}"
    if len(run_id) > FILENAME_ID_MAX_LENGTH:
        raise ConfigError(
            f"范围名 {scope.scope!r} 太长：这一轮的名字 {run_id!r} 有 {len(run_id)} 个字符，"
            f"而上限是 {FILENAME_ID_MAX_LENGTH}。\n"
            "``run_id`` 会被拼成文件名（``benchmarks/<run_id>.json``），超出的部分会被"
            "截断 —— 两个不同的范围名会落到同一份报告上，而报告上只会显示「内容更多了」。\n"
            f"换一个短一些的范围名（至多 {FILENAME_ID_MAX_LENGTH - len(run_id) + len(scope.scope)} "
            "个字符），或者在这一轮之前改名。"
        )
    return run_id


def run_discovery(
    *,
    bundle: ProfileBundle,
    scope: DiscoveryScope,
    search: SearchProvider,
    acquisition: DocumentAcquisition,
    store: EvidenceStore,
    router: DocumentRouter,
    ledger: BudgetLedger,
    inputs_dir: Path,
    created_at: datetime | None = None,
) -> DiscoveryRun:
    """跑一轮开放网络发现，返回**读数**。写盘是 :func:`materialize_pool` 的事。

    分成两步的理由与 ``read_motherpool`` / ``write_receipt`` 的分法逐字相同：
    判定与落盘之间有一次所有权的交接。这里还多一条 —— 跑一轮要花钱、花时间，
    而「把它写成一份工件」是一次纯本地的决定；合成一步之后，一次落盘失败会把
    已经花掉的检索一起报成「没跑」。

    Args:
        bundle: 冻结的 profile。检索式与额度上限都从它来。
        scope: 这一次运行的冻结范围（也是它的名字）。
        search: 检索端口（不是实现）。
        acquisition: 统一抓取入口（HTTP + 升级到浏览器）。
        store: 证据落盘。
        router: 解析路由（按内容类型选 parser）。
        ledger: 额度账本。上限由 :meth:`RunBudget.for_scope` 从 profile 播种。
        inputs_dir: 工件要落进的目录。**在花掉任何一次检索之前**就用它检查工件名。
        created_at: 这一次运行的时刻。注入是为了让重放可测。

    Raises:
        ConfigError: 工件名已被占用、这一轮的名字超出长度上限（见
            :func:`_run_id_for`）、或检索式一条都展开不出来。三种都在
            **发第一发查询之前**。单条查询失败不算：它是 ``FAILED`` 回执，记进报告。
        BudgetNotConfiguredError: profile 没有声明 ``search.max_queries``。
            也在发查询之前（它是 :meth:`RunBudget.for_scope` 抛的）。
    """
    started = created_at or utc_now()
    # 顺序是要紧的：展开检索式（可能抛）→ 检查工件名（可能抛）→ 播种额度（可能抛）
    # → 才发第一发查询。三件事任何一件不成立时，这一次运行一次检索都没发过。
    plan = build_open_web_queries(bundle, frozen_region=scope.region)
    # 这一轮写下的每一行证据都带**血统**（``Evidence.workspace``），取值只有一处：
    # ``bundle.profile_id``。它必须逐字等于授权那一层用的 ``settings.profile`` ——
    # 那件事由 ``adapters/profile_loader.py`` 的
    # ``_require_declared_profile_id_matches_workspace`` 保证（一份 profile 的 id
    # 与它所在的工作区必须同名），所以这里没有第二个答案可挑。
    #
    # 为什么取 bundle 这一份而不是 settings 那一份：这一层**拿不到** ``Settings``
    # —— ``run_discovery`` 的每一个依赖都是注入的（见上面 Args），而两条取值路径
    # 在盘上被那条判据钉成同一个值。另取一处配置会让「同一批产出上两个 Scene 名」
    # 重新变成可能，那正是这三张表要消灭的形状。
    workspace = bundle.profile_id
    artifact_path = inputs_dir / f"{scope.artifact_ref}{ARTIFACT_SUFFIX}"
    if artifact_path.exists():
        raise ConfigError(
            f"工件 {artifact_path.name} 已经存在（{artifact_path}）。\n"
            "名字在跑之前就是已知的，所以这件事要在**花掉任何一次检索之前**说出来 ——"
            "否则它会变成一次已经付过钱的失败。\n"
            "重跑同一轮：换一个范围名（scope 就是这一轮的名字，它同时是工件名、"
            "检索镜像与页面镜像的子目录名、以及额度记在谁名下那一段）。"
        )
    # 这一轮的名字在这里定下来，与工件名同一个理由：**它在跑之前就是已知的**。
    # 留在下面那个 ``return`` 里现算，会让「这个名字不可用」这件事发生在花钱之后
    # —— 而那时报告已经写成了一份读不回来的文件。
    run_id = _run_id_for(scope, started)
    budget = RunBudget.for_scope(ledger, bundle, scope.scope)

    caveats = list(_ALWAYS_CAVEATS)
    receipts: list[QueryReceipt] = []
    hits: list[HitRecord] = []
    order: list[str] = []
    by_site: dict[str, DiscoverySite] = {}

    billed = BilledSearch(search=search, budget=budget)
    for query in plan.queries:
        receipt = billed.query(
            market_query(
                bundle, text=query.text, scope=scope.scope, purpose="discovery"
            ),
            limit=scope.limit,
        )
        receipts.append(receipt)
        if receipt.outcome is QueryOutcome.EXHAUSTED:
            # 额度用尽：后面的检索式**一条都不发**。它们是这一次运行的读数的一部分
            # （见 ``_query_readings``），所以不从这里消失。
            break
        _fold_hits(receipt, query.text, hits, order, by_site)

    not_attempted = plan.queries[len(receipts) :]
    if not_attempted:
        caveats.append(
            f"还有 {len(not_attempted)} 条检索式**没有发出去**：额度在上一条查询上"
            "用尽了。这一轮对它们没有读数 ——「没搜」与「搜了没结果」不是一件事。"
        )

    # 分类词表只取一次，两个消费者共用：``_read_sites`` 拿它做分类，运行记录拿它
    # 报出「这一轮的分类器能命中哪些排除项」。分两处各取一次的话，第一次有人改了
    # 其中一处（比如换成 ``bundle`` 的另一个来源），工件里那一列与读回执用的那个
    # 集合就会来自两份不同的声明 —— 而症状是一份本来正常的工件被整份拒绝。
    vocabulary = bundle.profile.classification if bundle.profile is not None else None
    # 官网判定的词表与分类词表**从同一处取**（同一个 ``bundle.profile``），理由
    # 逐字相同：两处各取一次，第一次有人改了其中一处，工件里那一列与读回执用的
    # 那个集合就会来自两份不同的声明 —— 而症状是一份本来正常的工件被整份拒绝。
    #
    # ``None`` 是**这个 profile 没有声明**（没有 ``official_domain`` 块），而它不是
    # 「用默认参数判一遍」：判据的参数是市场知识，缺声明时跑出来的结论看起来与
    # 有声明时一模一样，只是参数是编的。缺声明 ⇒ 不判定 ⇒ ``unknown``。
    domain_vocabulary = (
        bundle.profile.official_domain if bundle.profile is not None else None
    )
    capped_out = _read_sites(
        order,
        by_site,
        scope=scope,
        workspace=workspace,
        acquisition=acquisition,
        router=router,
        store=store,
        vocabulary=vocabulary,
        domain_vocabulary=domain_vocabulary,
    )
    sites = tuple(by_site[site] for site in order)

    if capped_out:
        caveats.append(
            f"有 {capped_out} 台站点**没有去取**：池子在上限（max_candidates="
            f"{scope.max_candidates}）处就满了，剩下的不再取。\n"
            "这是一次「达到上限」的停止，不是「候选枯竭」—— 它们与「取过、没读到名字」"
            "不是同一件事，各自记在 ``stops`` 与每个站点的 ``reason`` 里。"
        )
    if not scope.fetch_pages:
        caveats.append(
            "这一轮只发了检索、没有取页面（fetch_pages=False），所以没有一行能得到"
            "名字，池子是空的。这是一个读得出来的结果，不是一次安静的失败。"
        )

    # 截断**只用这一个入口**（``application/caps.py`` 的 ``apply_cap``）：它是本仓
    # 唯一一处「丢掉的那几条记在哪」的实现，而报告里那两个数（keep 了几个、
    # 丢了哪几个）必须是同一份判断的产物。
    #
    # ``_read_sites`` 已经在上限处停了取页面，所以这里的 ``truncated`` 通常是
    # ``False`` —— 那是**构造上**的结果，不是「没有上限」：上限真正生效的地方是
    # 抓取那一侧（``capped_out``），而它进了 ``stops``。两处任一成立都报
    # ``CAP_REACHED``。留着这一步是为了让「哪些行进池子」永远由同一个函数回答 ——
    # 抓取那一侧将来多一个入口时，上限不会因此出现第二个实现。
    kept_ids, truncation = apply_cap(
        [site.candidate_id for site in sites if site.named], scope.max_candidates
    )
    kept = set(kept_ids)
    pool = tuple(site for site in sites if site.candidate_id in kept)

    candidates = [_candidate_from(site) for site in pool]
    qualifications = tuple(qualify_candidate(candidate, bundle) for candidate in candidates)
    if qualifications:
        unchecked = sorted({rule for item in qualifications for rule in item.outcome.unchecked})
        if unchecked:
            caveats.append(
                f"这一轮产出的候选行上，有 {len(unchecked)} 条准入规则**没判**"
                f"（{'、'.join(unchecked)}）。\n"
                "「没判」与「核对过、不命中」不是一件事：这些规则点名的事实里，"
                "有的是**这一页没有说话**（四格分类读数里的 unknown），有的是这一层"
                "根本没有生产者（" + "、".join(_NO_CANDIDATE_PRODUCER) + "）。"
                "未确认在**任何**极性下都不决定子句（见 "
                "marketops/application/qualify.py）—— 所以「候选都不合格」这句话在这一轮"
                "还没有依据。判据是 by_status 与 by_reason：停在 discovered 而理由是 "
                "unresolved_facts 的那些行，是**没判**，不是判过之后没命中。"
            )
        caveats.append(
            "四格分类读数（" + "、".join(CLASSIFICATION_COLUMNS) + "）由发现期产出，"
            "读出来是某个取值、读不出来是 'unknown'。**'unknown' 不决定任何子句**："
            "它在投影里折成未确认，规则留在 unchecked，账号停在 discovered。\n"
            "所以这一份读数**不能**被读成「这一页不是室内、不是人造草、不越界」——"
            "那是一次核对过的结论，而这里报的是「这一页没说过词表认得的说法」。"
            "两者在报告上长得一样，做出来的事完全相反：前者可以据此推进，"
            "后者只能再去查。"
        )
    if any(site.also_seen_urls for site in sites):
        caveats.append(
            "一台主机下的其余命中**没有去取**（记在各自的 also_seen_urls 里）："
            "池子的单位是站点，一行一台主机。一台主机上挂着多个单位时，"
            "这一轮只看得见第一条命中指向的那一个。"
        )

    stops = _stops(
        receipts,
        not_attempted,
        sites,
        cap_reached=truncation.truncated or capped_out > 0,
        fetch_pages=scope.fetch_pages,
    )
    return DiscoveryRun(
        run_id=run_id,
        scope=scope,
        profile_id=bundle.profile_id,
        created_at=started,
        plan=plan,
        queries=_query_readings(plan, receipts, not_attempted),
        hits=tuple(hits),
        sites=sites,
        pool=pool,
        truncation=truncation,
        qualifications=qualifications,
        usage=budget.usage(),
        stops=stops,
        caveats=tuple(caveats),
        declared_exclusion_ids=(
            vocabulary.exclusion_ids() if vocabulary is not None else ()
        ),
    )


def materialize_pool(inputs_dir: Path, run: DiscoveryRun) -> DiscoveryPool | None:
    """把池子写成一份工件，再读成一份回执。**一行都没有时返回 ``None``，不写空文件。**

    两步都在这一个函数里，因为它们是同一个决定的两半：写出来的字节必须**恰好**是
    回执判定的那一份。分给两个调用点之后，第一次有人只调用前一半时，盘上就会多一份
    没有回执的工件 —— 而它读起来与一份正常的工件没有区别。

    工件用 ``"x"`` 打开：**存在就抛 ``FileExistsError``**，不覆盖。
    先问一句再写会留一次竞争窗口，而这一层里「同一个名字下有两份字节」的后果是
    「这份回执指着哪一份工件」这个问题的答案是两个。

    Returns:
        :class:`DiscoveryPool`；这一次一行都没产出时 ``None``。
        ``None`` 的**唯一**含义是「池子是空的」—— 为什么空，答案在
        :attr:`DiscoveryRun.stops` 与每个站点的 ``reason`` 里。

    Raises:
        ConfigError: 工件名已被占用，或 ``read_motherpool`` 拒绝了这份工件
            （每一行都要有一个处置，而它自己验这一条）。
    """
    if not run.pool:
        return None

    rows = [_artifact_row(site, region=run.scope.region) for site in run.pool]
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    path = inputs_dir / f"{run.artifact_ref}{ARTIFACT_SUFFIX}"
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise ConfigError(
            f"工件 {path.name} 已经存在（{path}）—— 拒绝覆盖。\n"
            "一份已经被读成回执的工件是不可变的：改它会让这件事变成「盘上有两份字节，"
            "而回执只指着其中一份」。要重跑就换一个范围名。"
        ) from exc

    receipt = read_motherpool(
        inputs_dir,
        run.artifact_ref,
        source_method_id=OPEN_WEB_SOURCE_METHOD.id,
        # ``batch_id`` 一律留空。它是上游对这一批的称呼，而这一批没有上游 ——
        # 编一个（比如运行 id）会让它读起来像某个人真的这么叫过。
        batch_id="",
        # 这一轮的分类器**会**产出排除项那一列（``_artifact_row`` 写的
        # ``exclusions_hit``），所以这里报的必须是它可能命中的那些 id —— 报一个空
        # 集合会让每一行带排除项的行被判成「命中了未声明的排除项」，整份工件被拒。
        #
        # 这一条同时是「发现期的排除与准入期的排除是**同一份**词表」唯一的执行器：
        # 词表里 ``excludes`` 引用的 id 由 profile 装载期核对过（必须是
        # ``exclusions:`` 里声明过的），回执这一侧再核一次「这一列里出现的 id 是不是
        # 这一轮声明过的」。两次核对之间没有第三条规则，所以一个页面被判成
        # 「3G 场地」这件事，在发现报告与回执里是**同一条**结论，而不是两条恰好
        # 都指向排除的规则。
        declared_exclusion_ids=run.declared_exclusion_ids,
        profile_id=run.profile_id,
        scope=run.scope.scope,
        created_at=run.created_at,
    )
    written = write_receipt(inputs_dir, receipt)
    return DiscoveryPool(
        artifact_ref=run.artifact_ref,
        artifact_path=path,
        receipt_path=written,
        receipt=receipt,
    )


# --- 命中 → 站点 --------------------------------------------------------------


def _fold_hits(
    receipt: QueryReceipt,
    query_text: str,
    hits: list[HitRecord],
    order: list[str],
    by_site: dict[str, DiscoverySite],
) -> None:
    """一次检索的命中按顺序折成站点。**只读 URL，不读 snippet。**

    snippet 在这里连读都不读 —— 它不是证据（``ports/search.py`` 的契约），而且一个
    「顺手看一眼 snippet」的实现就是那条旁路的入口。
    """
    for hit in receipt.hits:
        hits.append(_fold(hit, query_text, order, by_site))


def _fold(
    hit: SearchHit,
    query_text: str,
    order: list[str],
    by_site: dict[str, DiscoverySite],
) -> HitRecord:
    try:
        site = canonical_host(hit.url)
    except ValueError as exc:
        return HitRecord(
            url=hit.url,
            rank=hit.rank,
            query=query_text,
            outcome=HitOutcome.UNUSABLE_URL,
            note=str(exc).splitlines()[0],
        )

    existing = by_site.get(site)
    if existing is None:
        by_site[site] = DiscoverySite(
            site=site,
            candidate_id=candidate_id_for(site),
            url=hit.url,
            query=query_text,
            rank=hit.rank,
        )
        order.append(site)
        return HitRecord(
            url=hit.url, rank=hit.rank, query=query_text, outcome=HitOutcome.SITE, site=site
        )

    # 同一个 URL 出现在两条检索式里是常事（模板之间有重叠），记两次不增加信息。
    # 不同的 URL 落在同一台主机上才是要留痕的那一种。
    seen = (existing.url, *existing.also_seen_urls)
    if hit.url not in seen:
        by_site[site] = replace(existing, also_seen_urls=(*existing.also_seen_urls, hit.url))
    return HitRecord(
        url=hit.url, rank=hit.rank, query=query_text, outcome=HitOutcome.SAME_SITE, site=site
    )


# --- 站点 → 取回 → 证据 → 名字 -------------------------------------------------


def _read_sites(
    order: Sequence[str],
    by_site: dict[str, DiscoverySite],
    *,
    scope: DiscoveryScope,
    workspace: str,
    acquisition: DocumentAcquisition,
    router: DocumentRouter,
    store: EvidenceStore,
    vocabulary: ClassificationVocabulary | None = None,
    domain_vocabulary: OfficialDomainVocabulary | None = None,
) -> int:
    """逐站点取页面、落证据、读名字、分类、判官网。返回**因上限而没有去取**的站点数。

    ``workspace`` 一路传到 :func:`_ingest` 落下的每一行证据上，取值与理由写在
    :func:`run_discovery` 里（只有一处：``bundle.profile_id``）。

    ``vocabulary`` 是分类词表，从 profile 来。它**没有缺省值之外的语义**：
    ``None`` 合法（一份 profile 可以先只有规则、后有分类），那时四格全部读成
    ``unknown``，而理由说的是「profile 没有声明词表」。

    ``domain_vocabulary`` 同理：``None`` 合法（profile 没有 ``official_domain``
    块），那时官网那一格读成 ``unknown``，理由说的是「profile 没有声明参数」。
    **它不是「用默认参数判一遍」** —— 判据的参数是市场知识，缺声明时跑出来的
    结论看起来与有声明时一模一样，只是参数是编的。

    上限在这里生效的方式是「池子满了就不再取」：``max_candidates`` 管的是**进池子的
    行**，而一行要进池子得先有名，所以「已经有 N 个名字了」就是「池子满了」。
    没有名字的站点不占名额 —— 它们是这一次运行的**发现**（这台主机拒了匿名访问、
    这一页没有标题），不是池子里的行。

    返回的那个数**必须被算进停止理由**：在这里停下而报告上不写，一次「达到上限」
    就会与一次「候选枯竭」在报告上同形 —— 前者是我们按自己的上限停的，
    后者是网络里没有再给出东西。两者都要有人做决定，而决定的内容不一样。

    判据是 ``named`` 而不是 ``len(pool)``：``apply_cap`` 之后池子里的行数一样是
    ``max_candidates``，但「因为满了而没取」这件事只在这一层看得见。
    """
    if not scope.fetch_pages:
        for site in order:
            by_site[site] = replace(
                by_site[site], reason="这一轮没有取页面（fetch_pages=False）"
            )
        return 0

    named = 0
    capped_out = 0
    for site in order:
        if named >= scope.max_candidates:
            by_site[site] = replace(
                by_site[site], reason="池子在上限处满了，这一台没有去取"
            )
            capped_out += 1
            continue
        updated, got_name = _read_one(
            by_site[site],
            scope=scope,
            workspace=workspace,
            acquisition=acquisition,
            router=router,
            store=store,
            vocabulary=vocabulary,
            domain_vocabulary=domain_vocabulary,
        )
        by_site[site] = updated
        if got_name:
            named += 1
    return capped_out


def _read_one(
    site: DiscoverySite,
    *,
    scope: DiscoveryScope,
    workspace: str,
    acquisition: DocumentAcquisition,
    router: DocumentRouter,
    store: EvidenceStore,
    vocabulary: ClassificationVocabulary | None = None,
    domain_vocabulary: OfficialDomainVocabulary | None = None,
) -> tuple[DiscoverySite, bool]:
    """一台站点：取一次、解析一次、落证据、读名字、分类、判官网。返回新站点与「有没有名字」。

    **分类在这些出口上都发生**，不只在那条顺路的出口上：被挡的、取到字节读不出正文的
    那两种，同样要有一组读数（它们至少带着主机名，而 ``country`` 的判据里有一条就是
    主机后缀）。没有它，「这一页我们读过、那四格是 unknown」与「这一页我们压根没读到」
    在工件里会是同一种行，而它们的下游处置完全不同。

    **官网判定只在读到正文那一条出口上发生**，理由与上面那条**不是**对称的：判据要在
    页面的字面正文上跑（``domain/domain_attribution.py`` 的三步），而没读到正文时
    手上只有一台主机名 —— 拿它单独判一次，等于把「我们在哪台主机上读到这一行」
    当成「这个组织的官网是谁」，那正是这次审计要拆掉的那句话。所以那些出口上
    ``domain_finding`` 留 ``None``，读数是 ``unknown``（没判定）。
    """
    # 主机名在这一步里不变（``site`` 会被 ``replace`` 换掉好几轮），先取出来：
    # 分类的判据之一是标签后缀，而它读的必须是**取回这一页的那台主机**。
    host = site.site

    def readings(text: str = "") -> AssetClassification:
        """这一页的读数。``text`` 缺省是空串 —— 「没有读到正文」时主机名仍然是判据。"""
        return classify(text, host=host, vocabulary=vocabulary)

    request = FetchRequest(url=site.url, scope=scope.scope, purpose="discovery")
    try:
        fetched = acquisition.fetch_document(request, mode=FetchMode.AUTO)
    except AccessBlockedError as exc:
        # 端口逐字允许实现抛这一个（其余网络异常必须被表达成 ``FetchResult``），
        # 而它是终态。不接住它，一次被拒的抓取会把整轮运行掀掉 —— 而「被拒」
        # 是一条要说出来的读数。
        return (
            replace(
                site,
                fetch_outcome=FetchOutcome.BLOCKED,
                reason=str(exc).splitlines()[0],
                classification=readings(),
            ),
            False,
        )

    result = fetched.final
    site = replace(
        site,
        fetch_outcome=result.outcome,
        tier=result.tier.value,
        http_status=result.status_code,
        body_sha256=result.body_sha256,
    )
    if not result.ok or result.body_path is None:
        return (
            replace(site, reason=_unreadable_reason(result), classification=readings()),
            False,
        )

    try:
        document = router.parse(result.body_path, content_type=result.content_type)
    except (ParseError, ParserUnavailableError) as exc:
        # 取回了字节、这一台解析器读不出正文。它与「没取到」不是一件事，
        # 而两种都不是「这一页没有内容」—— 那一句要说出来得先读到正文。
        return (
            replace(
                site,
                reason=f"取回了字节但读不出正文：{str(exc).splitlines()[0]}",
                classification=readings(),
            ),
            False,
        )

    site, title_literal = _ingest(
        site, document=document, result=result, workspace=workspace, store=store
    )
    # 标题接在正文前面：标题是这一页给自己写的名字，而名字几乎总是它最直白的一句
    # 自我介绍。「Example Indoor Cricket Centre」这种把关键信息全放在标题上的页面，
    # 只读正文会整个判不出来。两份都是这一页的字节，接起来不产生新的字面。
    page_text = f"{document.title}\n{document.text}"
    site = replace(site, classification=readings(page_text))
    if result.outcome is not FetchOutcome.OK:
        return replace(site, reason=_unreadable_reason(result)), False

    name = usable_name(title_literal, source_identity_for(site.site))
    if not name:
        return (
            replace(
                site,
                reason=(
                    "这一页没有写下一个能用的名字（标题为空、没有字母、或者那个值就是"
                    "主机名）。它落成了一行证据，但不进池子 —— 「这一页说它自己叫什么」"
                    "这一格空着，而拿主机名去当名字会让这一行读起来像一个组织。"
                ),
            ),
            False,
        )
    # 官网判定**在这一条出口上**，因为判据的三步都要这一页的字面正文与它落下的
    # 证据（``PageObservation``）—— 那两样只有走到这里才齐。
    #
    # 责任实体的名字用**这一页自己写下的那个**（``name``，刚才那行证据的
    # ``literal_value`` 逐字），而不是别的什么地方推来的：判据问的是「这台主机的
    # 名字里读不读得出**这个组织**的名字」，而这一层手上关于「这个组织」的全部
    # 材料就是这一页的话。用别处的名字会让同一页在两个调用点上判出两个答案。
    finding = (
        attribute_official_domain(
            name,
            (
                PageObservation(
                    host=host,
                    url=result.final_url or site.url,
                    text=page_text,
                    evidence_ids=site.evidence_ids,
                ),
            ),
            domain_vocabulary,
        )
        if domain_vocabulary is not None
        else None
    )
    return replace(site, name=name, domain_finding=finding), True


def _ingest(
    site: DiscoverySite,
    *,
    document: ParsedDocument,
    result: FetchResult,
    workspace: str,
    store: EvidenceStore,
) -> tuple[DiscoverySite, str]:
    """一份解析好的文档 → 两行证据（正文那一行 + 标题那一行）+ 这一页写下的名字。

    返回的字符串是**那一行证据的 ``literal_value`` 逐字**，不是重新从文档取一次
    标题：池子里那一行的名称、证据里那一行的字面、以及镜像字节三样必须能被串成一条
    链，而重取一次就多了一次可能对不上的机会。
    """
    common = {
        # 血统是这一行证据**自己的**一格，不是从 ``account_id`` 推出来的：
        # 账号这一层没有 Scene 这一维，而「一个部署只服务一个 Scene」曾经是
        # 唯一在挡这件事的东西（一句话，不是一条判据）。
        "workspace": workspace,
        "account_id": site.candidate_id,
        "source_url": result.final_url or site.url,
        "document": document,
        "fetched_at": utc_now(),
        "http_status": result.status_code,
        "tier": result.tier.value,
    }
    body = document_evidence(**common)
    title = title_evidence(**common)
    if body is not None and title is not None and body.evidence_id == title.evidence_id:
        # 内容寻址的 id 相撞：这一页的正文逐字等于它的标题（空壳页真的存在）。
        # 两行只有 ``page_locator`` 不同，而 store 会**正确地**拒绝同一个 id 配两份
        # 内容 —— 所以只写一行。留下标题那一行：它的位置更具体（``document-title``
        # 说的是「这一格是这份材料给自己写的名字」），而正文那一行说的是「整篇都在这儿」；
        # 在这个空壳里两句话指同一份字面，前一句信息更多。
        body = None

    evidence_ids: list[str] = []
    for evidence in (body, title):
        if evidence is None:
            continue
        store.put_evidence(evidence)
        evidence_ids.append(evidence.evidence_id)
    return (
        replace(
            site,
            evidence_ids=tuple(evidence_ids),
            title_evidence_id=title.evidence_id if title is not None else "",
        ),
        title.literal_value if title is not None else "",
    )


def _unreadable_reason(result: FetchResult) -> str:
    """一次没有拿到可读内容的抓取，为什么。逐字带上适配器给的理由。"""
    if result.outcome is FetchOutcome.BLOCKED:
        reason = result.block_reason.value if result.block_reason else result.outcome.value
        return (
            f"这一台主机明确拒绝了匿名访问（{reason}）。\n"
            "这是终态：不重试、不换 UA、不换代理、不绕 challenge —— 处置是改走别的"
            "公开来源，而不是在这一次运行里再试一遍。"
        )
    detail = result.error or result.reason or result.outcome.value
    return f"没有取到可读的内容：{result.outcome.value}（{detail}）"


# --- 站点 → 工件的行 ----------------------------------------------------------


def _artifact_row(site: DiscoverySite, *, region: str) -> dict[str, object]:
    """一台站点 → 工件里的一行。**列名是承重的**，理由逐条写在下面。

    * ``source_identity`` —— ``site:<主机>``。上游对象的标识（见 :func:`source_identity_for`）。
    * ``discovered_host`` / ``discovered_url`` —— **我们是在哪儿读到这一行的**。
      前者是取回它的那台主机，后者是那一页的地址。两格都是**观察**，不是主张：
      一台目录站、一份社媒页都会给出一个主机，而它们都不是这个组织的官网。
    * ``verified_official_domain`` / ``verified_official_domains`` / ``domain_verdict``
      / ``domain_evidence_ids`` / ``domain_reason`` —— **判定**（:meth:`DiscoverySite.domain_values`）。
      ``domain_verdict`` 是正式结论，``unknown`` 是「没判定」（**不是**「判定为假」）。
    * ``url`` —— SOP L94 要求每一行带 source URL，而它同时是「我们取的是哪一页」。
      它今天与 ``discovered_url`` 同值（这一轮一台主机只取一页）；两格并存是因为
      它们**不是同一句话** —— 后者说的是「这一行是在哪儿被读到的」，前者说的是
      「SOP L94 要的那一栏」。合成一格的那天，多页抓取落地时就会有人把其中一句
      读成另一句。
    * ``name`` —— 这一页自己写下的标题（那一行证据的 ``literal_value``，逐字）。

    **这一行不再写 ``official_domain``。** 那个列名在下游是「上游**说**的官网」
    （``RowDisposition.claimed_official_domain``），而发现期没有任何一句话是那个
    意思：我们看到的是一台主机（``discovered_host``），证得的是另一个集合
    （``verified_official_domains``）。此前把主机名写进那一格，症状正是审计里那条
    「SERP 抓回来的主机被读成『这是它的官网』」—— 于是账号的域权威是一台目录站的
    主机名，而报告上每一行都看起来正常。
    * ``discovery_region`` —— 这一次运行的冻结区域。**刻意不叫 ``region`` 或任何
      ``LOCATION_KEYS`` 里的名字**：那些会变成 ``resolved_location``，而
      「这一轮的范围是德文郡」与「这个站点在德文郡」是两句话。
    * ``pages_fetched`` / ``pages_blocked`` —— 计数，``0`` 是「确认为假」而不是
      「没有记录」。这一轮取过就是取过。
    * 四格分类读数（:data:`CLASSIFICATION_COLUMNS`）与 ``exclusions_hit`` —— 这一页读出来的
      资产事实。列名与事实名逐字同名是**承重的**：回执读取器按这几列取值
      （``application/motherpool.py`` 的 ``ASSET_FACT_KEYS`` / ``EXCLUSION_KEYS``），
      改一个名字不会有症状 —— 那一格会安静地永远是空的。
      没有读出来时写 ``unknown``（**正式结论**），而不是不写这一列：不写与
      「读了、是 unknown」在回执那一侧分不开，而回执那一侧是资产那一层唯一的路。
      ``exclusions_hit`` 只在命中时出现（一条都没有时空列与不写这一列同义）。
    """
    row: dict[str, object] = {
        "source_identity": source_identity_for(site.site),
        "discovered_host": site.site,
        "discovered_url": site.url,
        "url": site.url,
        "name": site.name,
        "discovery_region": region,
        "discovery_query": site.query,
        "fetch_outcome": site.fetch_outcome.value if site.fetch_outcome else "",
        "fetch_tier": site.tier,
        "http_status": site.http_status if site.http_status is not None else "",
        "body_sha256": site.body_sha256,
        "pages_fetched": 1 if site.fetch_outcome is FetchOutcome.OK else 0,
        "pages_blocked": 1 if site.fetch_outcome is FetchOutcome.BLOCKED else 0,
        **site.classification_values(),
        **site.domain_values(),
    }
    if site.classification is not None and site.classification.exclusions_hit:
        row["exclusions_hit"] = ",".join(site.classification.exclusions_hit)
    if site.evidence_ids:
        row["evidence_ids"] = ",".join(site.evidence_ids)
    if site.title_evidence_id:
        row["title_evidence_id"] = site.title_evidence_id
    if site.also_seen_urls:
        row["also_seen_urls"] = ",".join(site.also_seen_urls)
    return row


def _candidate_from(site: DiscoverySite) -> AccountCandidate:
    """一台站点 → 一条候选行。**生产者入口是 :func:`new_candidate`**，不是构造器。

    ``organisation`` 与 ``venue_name`` 都是那个标题，**这是同一句话说了两遍**，
    而它们是模型上的两个必填字段：这一行支撑的第一条判定是「这一行说的是哪个组织」，
    而发现期手上唯一的名字来源就是这一页给自己写的名字。所以：
    ``organisation == venue_name == <标题>`` —— 代价写在模块 docstring 的
    「这一轮没有确立的东西」一节，不在这里掩盖。

    ``surface_evidence_confirmed`` 留 ``None``（**没有查**）。它有三态，而这里是
    它的第一态：发现期没有做过表面判定，于是任何拿它当依据的规则都不该决定 ——
    ``None`` 恰好就是这个意思（它与取反得到的 ``False`` 不是一回事）。

    ``domain_evidence_ids`` 取这一行落过的**全部**证据：那几行都来自这台主机，
    所以它们是「我们在那台主机上读到过东西」。判据在 ``application/pairing.py`` ——
    一条没有出处的域名与随手写的域名在报告里长得一样，所以这一栏非空是配对成立的
    前提（:func:`_check_pairing` 守它）。

    ``discovered_host`` 与 ``official_domain`` 在这一类行上**同值**，而两格都写：
    这一行是一次观察（我们取回了那台主机的一页），而「它是这个组织的官网」是这一页
    **自己说的话**（它的标题就是这么写的）。两类候选行在这一格上分开：母池导入的
    那些只有 ``official_domain``（转述），没有 ``discovered_host``。合成一格之后
    两者在报告里长得一样，而下落相反。

    官网**判定**（``verified_official_domains`` / ``domain_verdict`` / 逐主机的
    拒绝理由）**不在这张行上**：它是回执行与资产行上的那一组格子
    （``domain/domain_attribution.py`` 的产出经 :func:`_artifact_row` 走到
    ``RowDisposition``），而 ``AccountCandidate`` 不参与绑定那一层 ——
    ``domain/bind.py`` 的三个投影点里没有收候选行的那个。

    四格分类读数与 ``exclusions_hit`` 由 :meth:`DiscoverySite.classification_values`
    与那一页的分类产出填。**没有分类时四格是 ``unknown``，不是空串** ——
    这一条的取舍写在 ``application/qualify.py::_reading`` 与
    ``domain/candidate.py`` 的校验器上：候选行会喂进规则引擎，而空串在那里读作
    「记了，是空」，会让「这一页没读出来」在报告上长成一条结论。

    ``surface_evidence_confirmed`` 与 ``has_notes`` 是这张行上**没有生产者**的两格
    —— 见 :data:`_NO_CANDIDATE_PRODUCER`。
    """
    candidate = new_candidate(
        OPEN_WEB_SOURCE_METHOD,
        candidate_id=site.candidate_id,
        organisation=site.name,
        venue_name=site.name,
        official_domain=site.site,
        discovered_host=site.site,
        pairing_key=site.site,
        domain_evidence_ids=list(site.evidence_ids),
        evidence_ids=list(site.evidence_ids),
        pages_fetched=1,
        pages_blocked=0,
        exclusions_hit=(
            list(site.classification.exclusions_hit) if site.classification is not None else []
        ),
        **site.classification_values(),
    )
    _check_pairing(candidate)
    return candidate


def _check_pairing(candidate: AccountCandidate) -> None:
    """这一行**真的**能配对吗。

    域名那一栏自己不算数：``pairing_key_for`` 还要求至少一条指着它的证据。
    这一行是我们自己写下的（域名就是取回它的主机），所以这条判据今天恒成立 ——
    正因为恒成立，它才必须在**这里**成立：漏掉 ``domain_evidence_ids`` 的那一天，
    症状是这些行安静地全部变成「不可配对」，而报告上它们每一行都长得完全正常。
    """
    derived = pairing_key_for(candidate)
    if derived != candidate.pairing_key:
        raise ConfigError(
            f"候选 {candidate.candidate_id} 的 pairing_key 是 {candidate.pairing_key!r}，"
            f"而按证据派生出来的是 {derived!r}。\n"
            "两者不一致意味着这一行的域名没有证据指着它 —— 那些行会被下游按「不可配对」"
            "处置（hold），而每一行看上去都完全正常。"
        )


# --- 停止理由与报告 -----------------------------------------------------------


def _stops(
    receipts: Sequence[QueryReceipt],
    not_attempted: Sequence[OpenWebQuery],
    sites: Sequence[DiscoverySite],
    *,
    cap_reached: bool,
    fetch_pages: bool,
) -> tuple[DiscoveryStop, ...]:
    """这一轮停下来时的读数。**可以有多个**，顺序固定（见枚举成员的声明序）。

    ``cap_reached`` 由调用方算好传来（「池子在上限处截断了」**或**「有站点因为上限
    没有去取」）。在这一层重新推导会让「因上限没取」这件事丢失 —— 站点列表里那些
    行看上去与「没取到名字」的行完全一样。
    """
    stops: list[DiscoveryStop] = []

    attempted = [
        receipt
        for receipt in receipts
        if receipt.outcome in (QueryOutcome.SENT, QueryOutcome.FAILED)
    ]
    fetched = [site for site in sites if site.fetch_outcome is not None]
    if fetched and all(site.fetch_outcome is FetchOutcome.BLOCKED for site in fetched):
        stops.append(DiscoveryStop.BLOCKED)
    if attempted and not any(receipt.sent for receipt in attempted):
        stops.append(DiscoveryStop.PROVIDER_FAILED)
    if not_attempted:
        stops.append(DiscoveryStop.BUDGET_EXHAUSTED)
    if cap_reached:
        stops.append(DiscoveryStop.CAP_REACHED)
    if fetch_pages and not any(site.evidence_ids for site in sites):
        stops.append(DiscoveryStop.NO_EVIDENCE)
    if not any(site.named for site in sites):
        stops.append(DiscoveryStop.NO_NAMED_SITE)
    return tuple(stops)


def _query_readings(
    plan: OpenWebPlan,
    receipts: Sequence[QueryReceipt],
    not_attempted: Sequence[OpenWebQuery],
) -> tuple[dict[str, object], ...]:
    """**每一条检索式**的读数：发过的、跳过的、失败的、没轮上的各有一格。

    没轮上的那些必须在这里，不能只留一个「预算用尽」的布尔：一份「跑了 4 条」的
    报告与一份「20 条里跑了 4 条」的报告，是两件完全不同的事，而它们的差别就落在
    这几条没有去向的检索式上。

    ## ``cause`` 这一格说的是**根因**，不是「为什么这一行没有结果」

    它取 ``SearchError.suspected_cause`` —— ``domain/access.py`` 那个闭集里的一个
    成员值（``access_denied_unknown`` / ``rate_limited`` / …）。取值只在
    ``isinstance(error, SearchError)`` 时给出，别处是空串。**这不是一个可以省略的
    类型判断**：``receipt.error`` 的类型是 ``MarketOpsError``，而其中一种
    （``BudgetExhaustedError``）在任何请求发出**之前**就发生了，它身上没有、也不该
    有一个「来源为什么拒绝」的判定 —— 直接取属性会在额度用尽那一刻把写报告这行
    崩掉，而那正是最需要这份报告的一次运行。``SearchError`` 是全仓唯一一个按构造
    带访问判定的异常类型，所以这个 ``isinstance`` 不是守卫，是那件事的类型陈述。

    于是空串在这一格里有两种来路（没失败 / 失败但不是来源拒绝），而**同一个行里
    的 ``outcome`` 那格把它们分开**（``sent``/``skipped`` 对 ``exhausted``）——
    这一格才因此不是「同名两义」。反过来，``status`` 那种格之所以不加，是因为
    成功的行压根没有记下状态码，一个 ``null`` 会被读成「问到了、没给我码」。
    """
    readings: list[dict[str, object]] = []
    for query, receipt in zip(plan.queries, receipts):
        error = receipt.error
        readings.append(
            {
                "text": query.text,
                "template": query.template,
                "outcome": receipt.outcome.value,
                "api_calls": receipt.api_calls,
                "hits": len(receipt.hits),
                "settled_already": bool(
                    receipt.reservation is not None and receipt.reservation.settles_itself
                ),
                "error": str(error).splitlines()[0] if error is not None else "",
                "cause": error.suspected_cause.value if isinstance(error, SearchError) else "",
            }
        )
    for query in not_attempted:
        readings.append(
            {
                "text": query.text,
                "template": query.template,
                "outcome": "not_attempted",
                "api_calls": 0,
                "hits": 0,
                "settled_already": False,
                "error": "",
                # 没轮上不是在来源那里失败 —— 没有来源参与过这一行，所以没有
                # 根因可报。见 :func:`_query_readings` 的 docstring。
                "cause": "",
            }
        )
    return tuple(readings)


__all__ = [
    "ARTIFACT_SUFFIX",
    "CANDIDATE_ID_PREFIX",
    "SOURCE_IDENTITY_NAMESPACE",
    "DiscoveryPool",
    "DiscoveryRun",
    "DiscoveryScope",
    "DiscoverySite",
    "DiscoveryStop",
    "HitOutcome",
    "HitRecord",
    "candidate_id_for",
    "materialize_pool",
    "run_discovery",
    "source_identity_for",
]

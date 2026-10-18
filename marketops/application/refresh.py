"""一格到期工作的**复核执行器**：把这一格真的读一遍，然后如实报出读到了什么。

## 两条复核，两种代价

:data:`REFRESHABLE_ASPECTS` 里今天有三个面，而它们做的**不是同一件事**：

* ``source_coverage`` —— **读本地记录**：这个主体手上有哪几条证据、其中哪几条算得上
  依据。这是 :func:`census_for` 干的事，它不联网、不花钱，而且是**满覆盖**的测量
  （那一面的主语就是「我们手上有哪些来源」，见下面「这一层不写观测」那一节）。
* ``contact_person_role`` / ``email_evidence`` —— **真的去外面看一眼**：把这一格名下
  **已知的页面**按 URL 重新取回来，与记录里的指纹比一次。这是 :func:`recheck_pages`
  干的事：它要联网、要花抓取额度，而且**可能够不着**。

两件事都是「复核」，产出形状却不同：前者是一份**盘点**，后者是一份**逐页的复审记录**。
两者都落在 ``result_ref`` 上（``MaintenanceOutcome`` 逐字要求那一格非空），靠记录里的
``kind`` 一格区分 —— 见下面的「两种记录」一节。

## 新来源缺口 ≠ 真实场地新增（只有 ``source_coverage`` 有这一对）

§4 逐字：「新来源缺口 ≠ 真实场地新增 —— 分开数」。这两件事在本模块里是**不对称**的：

* **新来源缺口** —— 「我们知道有个场地，但没有任何来源覆盖它」—— 可以**完全**从
  本地记录回答：这个主体名下有哪几条证据、其中哪几条算得上依据。这是
  :func:`census_for` 干的事。
* **真实场地新增** —— 「来源里真的出现了一个此前不存在的场地」—— **答不出来**。
  一个新场地只可能从一次**新的检索**里出现（``search`` 那一侧），而本模块做的两件事
  ——读本地记录、重取**已知**页面——都不会让一个此前不存在的场地出现。所以
  ``new_venues`` 永远是 ``None``（= 这一项这次没测），由 ``SourceDelta.unmeasured``
  说出来。**写成 0 是不允许的**：「这一轮没测」与「这一轮没有新场地」是两句不同的
  话，而后者是本模块根本说不出的一句话（§3.4）。理由的那一句只有一处定义，
  :data:`NEW_VENUES_REASON`。

## 这一层不写观测，但这两趟都**算**观测（2026-09-25 反转了一条旧纪律）

这一节原来逐字写着「本模块读的是本地已经有的记录，没有拿到任何新内容」，
并据此把 ``observed`` 定成恒假。**那句话把两件事混成了一件**：它说的是「没有拿到
**新**内容」，而它需要说的是「有没有拿到**内容**」。``SOURCE_COVERAGE`` 那一面的
主语就是**我们手上有哪些来源** —— 那些东西本来就住在库里，数一遍它们**就是**在
观测这一面，而且是完整地观测：``census_for`` 读的是这个主体名下**全部**证据行，
一条不漏。所以对这一个面，本地读不是一次「近似」，它是一次**满覆盖**的测量。

被反驳的是哪一个反例：一句话说的是「一次 re-read 不产生新的观测」。那个反例是
真的，而它约束的是**另一个动作** —— 重读一份关于对象的记录（一条资产行、一份回执），
然后声称「对象本身被重新核过了」。那种动作在本模块里不存在，而在
``adapters/mcp_server`` 的 ``promote_receipt`` 那里存在，并且那里**照旧**不写观测
（``test_a_fold_is_not_a_recheck`` 钉着）。两者的分别不是「本地 vs 远程」，
是**这一面的主语是不是就是那份本地记录**。

不变的是那条真正要守的东西：**一次假观测会把真正的复核推迟半年。** 所以写观测的
权限没有下放给这一层 —— 本模块没有 ``MaintenanceStore``，它只在 outcome 里声明
``observed_at``，而写那一行的是 ``application/maintenance.run_once``（维护账的
主人），并且只在这一格真的被认领、真的跑完之后。谁的账谁记账。

（收敛性因此换了机制，而**结论不变**：第一轮 tick 之后这一格有了一条观测，
``what_is_due`` 于是不再把它排上 —— 第二次 tick 的 ``due_count`` 是 **0**，
而不是「排上了、但已做过」。旧机制（``never_observed`` 的 ``period`` 是固定的
``"never"``，于是第二次的 ``dedupe_key`` 与第一次相同、存储回 ``won=False``）
仍然在：它管的是**同一周期内**的重复 tick 与重放，见
``tests/test_application_maintenance.py``。）

## 页面复核：复用判定读**证据行**，不读文件系统

§11 逐字：「获取前检查已有工件、使用权限、内容/解析版本、所需事实与时效；**可复用则
返回引用，不调用供应商**」。这一段的「已有工件」在本层有一个明确的主语：**本工作区
这个主体名下的证据行**。镜像目录（``Settings.mirrors_dir``）**不是**它 —— 那个目录
跨工作区共用，一次别的 Scene 的抓取会在里面留下一份文件；把它当成「已经有了」，等于
让一个工作区拿另一个工作区的抓取给自己续期，而报告上只显示「这一轮没花钱」。所以
**镜像文件在盘上、本工作区没有证据行 ⇒ 仍然抓取**。

一条证据行要满足两条才叫「仍然新鲜」（缺一不可）：``fetched_at`` 落在
``plan.rule_for(aspect).after_days`` 之内，**且**它的 ``parser`` 仍在本部署的解析器
名单里（``router.parser_names``）。第二条是 §11 那一句里「解析版本」那一半：同一个
文件用旧解析器抽出来的字面与用今天这个抽出来的不是同一件事，而换了解析器却按周期
未到就复用，会让「我们换了抽法」在账上永远不出现。

## 一次 tick 里两个面共用同一批页面 ⇒ 抓取**恰好一次**

两个页面面（角色 / 邮箱证据）看的是同一批页面：一个主体名下已知的那些 URL。它们
在同一个 tick 里各自到期时，第一个面把页面取回来，第二个面必须**复用那一趟的结果**
而不是再发一次请求 —— 否则「一次复核看了几页」这个数会按面的个数翻倍，而两次抓的
是同一个东西。

机制是 :func:`build_refresher` 里的一张**内存备忘**（``(subject_kind, subject_id)`` →
:class:`TickPages`）。``build_refresher`` 一次 tick 调一次，所以这张表的寿命**就是一轮
tick**，不会跨轮复用（下一轮那些格的页面已经不是这一批了）。备忘命中的那个面，
``state`` 是 ``REUSED``（它这一趟没有花抓取的钱），而它的逐页结论与第一个面**是同一批
对象** —— 那几页确实在这一趟被看过了，只是钱不是这个面花的。这也是为什么
``REUSED`` 不等于「不写观测」：写不写看的是**这一趟有没有人真的把那些页面看过**，
而备忘命中的那一趟有。反过来，从库里复用到的旧证据（下面第一格）那一趟**没有人看**，
于是它写进账的 ``observed_at`` 是**那份证据被取回的时刻**，不是这一趟的时刻。

备忘只装**每一页都有了结论**的那一趟（哪怕只有一页没看成，也不进备忘）。所以
「一页都没抓、全是库里复用」的那种结果**不进备忘** —— 共享它省不下任何东西，而它
依据的是**这一面自己的**时效窗口：两个页面面的窗口可以不同（``refresh.rules`` 逐面
声明 ``after_days``），把一面的窗口判断当成另一面的，是拿一个没被问过的问题的答案
去回答另一个问题。共享的是**这一趟真的发生的取回**，不是判断。

## 三格状态与「部分看」

* ``REUSED`` —— 一页都没抓（手上的证据都还在时效内、解析器也没换），或者本轮另一个
  面已经把这批页面取回来了。这一格不花抓取的钱。
* ``UNCHANGED`` —— 抓了，页面指纹与记录一致。**一行新证据都没有落。**
* ``CHANGED`` —— 抓了，指纹不同**或**解析版本变了，**并且新证据落了盘**。
* ``UNREACHABLE`` —— 没抓到，或者这一趟**没看全**。它不写观测。

**「没看全」不写观测**，这是最后一格的全部要点：一个主体名下 10 页而这一轮只看了 5
页时，那 5 页没变**不能**给整格续期 —— 剩下 5 页里可能正好有那一页写着「这位联系人
已经换人了」。所以缺一页就整格 ``UNREACHABLE``，而**逐页**的结论照旧写进记录（哪几页
看了、结论是什么、哪几页没看、因为什么）。逐页那一份是这一格可被复核的地方：读的人
看得到「它没看全」是额度不够、还是站点挂了，而不是一句笼统的不可达。

## 抓取额度：一个主体**一次复核**

额度记在 ``BudgetLine.DOCUMENT`` 上，scope 是 ``<profile>:<主体 id>@<这一轮的时刻>``
（:func:`document_scope` 是它唯一的拼法）。**刻意不是「一个主体一个终身上限」**：
账本没有充值机制，一个终身上限意味着一个长期经营的部署在抓满那个数之后**再也复核
不了** —— 而报告上显示的是「没有到期的格子」，不是「额度用尽」。上限本身来自冻结的
profile（``fetch.max_documents``，``RunBudget.for_documents``），没声明就失败关闭。

额度不够时的处置是**整格不可达 + 逐页写出哪几页没看**，不是「挑几页看看，都挺好」：
后者是拿一次部分观测去给整格续期（见上一节）。这一条要人处置，所以它读起来必须像
一条要人处置的账 —— ``document_budget_exhausted`` 那几行里带着已看 / 未看的页数。

## 「这个部署能复核哪些 aspect」是一个**声明**

:data:`REFRESHABLE_ASPECTS` 是本部署**声明**得出来的那几个面，不是从代码里推断出来的。
推断在这里恰好是错的：一个没有联网能力的部署并不因此「不能」复核资产事实 —— 它只是
今天没有那条实现，而这两件事在报告里的处置不同（一个是「不在本部署的范围内」，一个是
「该做而没做」）。声明与派发表必须互为子集，判据在 ``tests/test_application_refresh.py``。

:data:`COVERAGE_GAPS` 是同一句话的另一半，也是**分母**：承诺要维护的面里今天没接的
那几个（§11 逐字：「未接的 aspect 报 coverage gap，不能通过 out_of_scope 把必需功能
从验收分母删掉」）。它由 ``uncovered_aspects`` 算出来，不是手写的差集。

## 两种记录，一个落点

盘点与复审记录都写在 ``root/<workspace>/`` 下，文件名里带内容摘要，靠 ``kind`` 一格
区分（``"census"`` / ``"recheck"``）。``kind`` **进摘要**：一份记录被改成另一种，
重算那一步会红，而不是被当成另一种读出来。

``kind`` 在 B22 之前不存在（那时目录里只可能有一种记录）。读旧文件时缺这一格按
``"census"`` 处置（:func:`_load_record`）—— 那不是猜：写下它的那个版本只写过一种
记录，而**重查记录从写下的第一天起就带这一格**，所以缺省不可能把一份重查读成盘点。

## 盘点为什么要落盘

``result_ref`` 是**指向产出的引用**（``MaintenanceOutcome`` 逐字如此），而不是产出本身。
产出就是这一份盘点或复审记录：它得能被后来的读者打开、与两次 tick 之间对比、在出问题
时被复核。文件名里的摘要取自内容（不含 ``taken_at``），所以「读到的还是那些东西」这件事
是**可核对的**而不是可记忆的：同一个摘要必然是同一份，而盘上写着它是什么时候取的。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..domain.enums import BINDING_ELIGIBLE_SOURCES
from ..domain.errors import (
    BudgetExhaustedError,
    ConfigError,
    EvidenceError,
    ParseError,
    ParserUnavailableError,
)
from ..domain.evidence import Evidence
from ..domain.maintenance import (
    Aspect,
    DueWork,
    RecheckState,
    RefreshPlan,
    SourceDelta,
    uncovered_aspects,
)
from ..domain.util import check_canonical_id, check_slug_safe, sha256_text, to_utc
from ..ports.acquisition import FetchMode, FetchRequest, FetchResult
from ..ports.documents import PageCopier, ParsedDocument
from ..ports.store import EvidenceStore
from .acquisition import DocumentAcquisition
from .budget import RunBudget
from .document_router import DocumentRouter
from .evidence_ingest import document_evidence, title_evidence
from .maintenance import MaintenanceOutcome
from .reuse import SHARED_PREFIX, ReuseLedger

__all__ = [
    "CENSUS_BACKED_ASPECTS",
    "COVERAGE_GAPS",
    "NEW_VENUES_REASON",
    "PAGE_BACKED_ASPECTS",
    "REFRESHABLE_ASPECTS",
    "WIRING_MEANING",
    "EvidenceCensus",
    "PageRecheck",
    "PageTarget",
    "RecheckRecord",
    "TickPages",
    "build_refresher",
    "census_for",
    "census_path",
    "content_digest",
    "document_scope",
    "load_census",
    "load_outcome",
    "load_recheck",
    "missing_wiring",
    "recheck_pages",
    "recheck_path",
    "refresh_source_supply",
    "write_census",
    "write_recheck",
]

#: 本部署**声明**得出来的复核面。见模块 docstring 的最后一节。
#:
#: 三个，而不是七个。要复核一格资产身份 / 属性 / 账号关系 / 产品适配，需要一条本部署
#: 今天没有的能力（把那一格名下的**材料**重新读一遍，而不只是重取页面或数本地记录）；
#: 它们因此落在 ``MaintenanceRun.out_of_scope`` 里被逐格报出来，而不是被认领后记成
#: ``FAILED``（后者的账会把「没实现」读成「跑挂了」，而两者的处置完全不同）。
#:
#: 这份集合在 B22 变大了：两个页面面（联系人与角色 / 邮箱证据）各自拿到了真实的重取
#: 执行器（``recheck_pages``，它真的联网、真的花钱、真的可能够不着）。变的不是「我们
#: 想维护几个面」，是**这条能力从没有到了有**。剩下四个面照旧在
#: :data:`COVERAGE_GAPS` 里被报出来 —— 它们没有因为这一份变大而被从分母里删掉。
REFRESHABLE_ASPECTS: frozenset[Aspect] = frozenset(
    {Aspect.SOURCE_COVERAGE, Aspect.CONTACT_PERSON_ROLE, Aspect.EMAIL_EVIDENCE}
)

#: 靠**重取已知页面**复核的那几个面（:func:`recheck_pages` 做的）。
#:
#: 与 ``REFRESHABLE_ASPECTS`` 的关系是「子集 + 另一种代价」：``source_coverage`` 读本地
#: 记录（不联网），这几个要联网。分成两份而不是在派发处写一个 ``if``，是因为「这一面要
#: 不要去外面拿东西」是**这一层唯一需要知道的那件事**，而它决定了这一格会不会花钱、
#: 会不会够不着、要不要接线（见 :func:`missing_wiring`）。
PAGE_BACKED_ASPECTS: frozenset[Aspect] = frozenset(
    {Aspect.CONTACT_PERSON_ROLE, Aspect.EMAIL_EVIDENCE}
)

#: 靠**读本地记录**复核的那个面（:func:`refresh_source_supply` 做的，落一份盘点）。
#:
#: 手写而不是由 ``REFRESHABLE_ASPECTS - PAGE_BACKED_ASPECTS`` 推出来，理由是**推断会
#: 撒谎**：那个差集只在「每一个非页面面都由盘点回答」成立时才等于这一份，而这句话是
#: 人的一个决定，不是两条集合的性质。新加一个面到 ``REFRESHABLE_ASPECTS`` 而忘了给它
#: 写派发分支时，推出来的那一份会**照旧把新面算进盘点的名下**，于是报告说这一面有产出；
#: 手写的这一份则让新面落进 ``build_refresher`` 末尾那条 ``ConfigError``。两半的并集
#: 必须恰好是 ``REFRESHABLE_ASPECTS``（见 ``tests/test_application_page_refresh.py``
#: 里那条判据）—— 于是「加了一个面」必然要在这一处分一次。
CENSUS_BACKED_ASPECTS: frozenset[Aspect] = frozenset({Aspect.SOURCE_COVERAGE})

#: 本部署**承诺**要维护、而今天**没有接**的那几个面 —— 按 ``ALL_ASPECTS`` 次序。
#:
#: 它是一个**分母**（§11 逐字：「未接的 aspect 报 coverage gap，不能通过 out_of_scope
#: 把必需功能从验收分母删掉」），由 ``uncovered_aspects`` 算出来 —— 不在这里手写一份
#: 差集：手写的那一份在 ``REFRESHABLE_ASPECTS`` 变大的那天不会跟着变，而它看起来仍然
#: 正常（少报的正是刚刚接上的那一面）。
COVERAGE_GAPS: tuple[Aspect, ...] = uncovered_aspects(REFRESHABLE_ASPECTS)

_CENSUS_SUFFIX = ".json"

#: 盘点记录与复审记录在盘上的 ``kind`` 取值。见模块 docstring 的「两种记录，一个落点」。
CENSUS_KIND = "census"
RECHECK_KIND = "recheck"

#: :meth:`EvidenceCensus.as_dict` 比 :meth:`EvidenceCensus.payload` 多出来的那几格。
#:
#: 它们都是**出处**而不是**内容**：什么时候取的、这一份叫什么、算出来的缺口是多少、
#: 哪一项没测。所以它们都不进摘要。
#:
#: 写成一份**共用的**元组，是因为 :func:`load_census` 要靠它把内容那一半挑出来重算
#: 摘要。两处各写一份的话，将来给 ``as_dict`` 加一格出处而忘了改 `load_census`，
#: 后果是**每一份盘点都读不出来** —— 一次由「多了一个字段」引起的全面误报。
#:
#: ``kind`` **不在**这里：它是这一份**是什么**，不是它**什么时候被写的**。放进摘要之
#: 后，一份记录被改成另一种会当场变红（内容与声明的摘要对不上），而不是被当成另一种
#: 读出来 —— 这是这一格存在的全部意义。
_PROVENANCE_KEYS = ("taken_at", "digest", "new_source_gaps", "not_measured")


def content_digest(payload: Mapping[str, Any]) -> str:
    """盘点**内容**的摘要：规范化 JSON 的 sha256 前 32 位。

    ``ensure_ascii=False`` 与 ``sort_keys=True`` 都是必需的：前者让中文字段
    （``not_measured`` 里那句理由）在落盘与重算之间逐字节一致，后者让「同样的内容
    换个插入次序」仍然是同一个摘要。这两条合起来才使 :func:`load_census` 的重算
    有意义 —— 否则它比的是一个每次都可能不同的字符串。
    """
    canonical = json.dumps(dict(payload), sort_keys=True, ensure_ascii=False)
    return sha256_text(canonical)[:32]


@dataclass(frozen=True)
class EvidenceCensus:
    """一个主体手上**有什么来源**的一次盘点。

    ``rows`` 与 ``kinds`` 分别答「有几条」与「哪几类」，两者都要：一个主体可能
    有 40 条证据而全是 ``SEARCH_SNIPPET``（一条都当不了依据），也可能只有 2 条
    而两条都是真抓下来的页面。**只报条数会正好把这两种情形报成一样。**

    ``per_kind`` 保留逐类的条数，``kinds`` / ``bindable_kinds`` 是它的两个投影。
    投影而不是全部：报告里最常问的问题是「这个主体有没有一条能当依据的来源」，
    而那是一个布尔问题，不该让每个读者自己拿 ``BINDING_ELIGIBLE_SOURCES``
    再过滤一遍 —— 判据只该有一个执行器。

    ``store_present`` 是**独立的**一格，不是 ``rows > 0`` 的同义词：库还不存在
    与「库在、这个主体什么都没有」是两件事。前者是一个刚起、还没跑过任何作业的
    部署，后者是「跑过了、颗粒无收」。两者在这一层给出的 ``new_source_gaps``
    **相同**（都是 1 —— 手上确实一条依据都没有），所以分开报的那一格是读的人
    唯一的诊断面：``read_store`` 逐字说过「库文件不存在是「还没有证据」的诚实
    形态」。
    """

    workspace: str
    subject_kind: str
    subject_id: str
    taken_at: datetime
    store_present: bool
    rows: int
    kinds: tuple[str, ...]
    bindable_kinds: tuple[str, ...]
    per_kind: tuple[tuple[str, int], ...]
    #: 最近一次抓取时刻（ISO-8601）；一条证据都没有时是空串 —— **不是** epoch。
    newest_fetched_at: str = ""

    @property
    def uncovered(self) -> bool:
        """这个主体有没有「一条能当依据的来源都没有」这条欠账。"""
        return not self.bindable_kinds

    @property
    def new_source_gaps(self) -> int:
        """``SourceDelta.new_source_gaps`` 的取值。

        **数的是「几笔待办」，不是「几个来源」。** 一个主体名下一条可绑定证据
        都没有，就是**一笔**待办：去给它找一条能当依据的来源。手上那 N 条
        ``SEARCH_SNIPPET`` 不是 N 笔待办，它们是一件事。

        它是状态而不是差值（字段名里的「新」读作「这一轮记下的」）：一笔欠账
        连续三轮都在，是「自动复核解决不了它」——那正是要被人看见的信号，而不是
        一个应当被抹平的数字。
        """
        return 1 if self.uncovered else 0

    def payload(self) -> dict[str, Any]:
        """进摘要的那一部分。**刻意不含 ``taken_at``。**

        含进去的话，同一份记录读两次会是两份不同的盘点（只有时刻那一格不同），
        于是「有没有变」这个问题的答案会永远是没有 —— 而文件名里的摘要正是
        用来回答它。``write_receipt`` 对 ``created_at`` 用的是同一条判据。
        """
        return {
            "kind": CENSUS_KIND,
            "workspace": self.workspace,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "store_present": self.store_present,
            "rows": self.rows,
            "kinds": list(self.kinds),
            "bindable_kinds": list(self.bindable_kinds),
            "per_kind": [[kind, count] for kind, count in self.per_kind],
            "newest_fetched_at": self.newest_fetched_at,
        }

    @property
    def digest(self) -> str:
        """盘点内容的摘要。**同一份盘点必然同一个摘要** —— 它进文件名。"""
        return content_digest(self.payload())

    def as_dict(self) -> dict[str, Any]:
        """落盘的那一份。比 :meth:`payload` 多出来的几格都是**出处**，不进摘要。

        多出来的那几格**逐字**是 :data:`_PROVENANCE_KEYS`：``load_census`` 靠同一份
        元组把它们摘掉再重算摘要，所以两处不可能分家。
        """
        return {
            **self.payload(),
            "taken_at": self.taken_at.isoformat(),
            "digest": self.digest,
            "new_source_gaps": self.new_source_gaps,
            # 这一项为什么是 null，写在盘上而不是留给读的人去猜。
            "not_measured": {"new_venues": NEW_VENUES_REASON},
        }


#: ``new_venues`` 恒为 ``None`` 的理由。**一处定义**，盘上与工具回执读的是同一句。
NEW_VENUES_REASON = (
    "真实场地新增只能从一次**新的检索**里出现（search 那一侧），而本模块做的两件事"
    "——读本地记录、重取**已知**页面——都不会让一个此前不存在的场地出现。所以它是"
    " null（未测），不是 0（测了、没有）。"
)


@dataclass(frozen=True)
class PageTarget:
    """这一格名下**已知的一页**：复核它就是把它再取回来比一次。

    **一页一个 URL**（不是一行证据一个）。同一个 URL 下可能有很多行证据（正文行、标题
    行、抽出来的字段行），它们说着同一页的同一件事，而其中若干行会带同一个
    ``content_sha256``（``domain/bind.py`` 让一页上的每一行都带这份文档的散列）。
    按 URL 去重之后，同一页只算一次抓取 —— 不去重的形态是「一个 URL 被抓了 N 次，
    而 N 是这一页上有几个字段」，报告上则显示为「这一轮看了很多页」。

    ``expected`` 是**文件的**散列（``ParsedDocument.sha256`` 与
    ``Evidence.content_sha256`` 是同一个量）：它与取回来那一份的散列直接可比。**不是**
    ``FetchResult.body_sha256`` —— 那个量是**解码后文本**的散列，两个量在同一个文件上
    就不相等，拿它来比会让每一页都"变了"。

    ``parser`` 是写下这一行时的解析器名：它决定这一行还算不算「解析版本是当前的」
    （见模块 docstring）。``evidence_id`` 留着是为了让记录说得出「这一页上一次是谁写的
    那一行」—— 逐页结论里那一格的全部内容就是引用能不能解回去。
    """

    url: str
    expected: str
    parser: str
    fetched_at: datetime
    evidence_id: str


@dataclass(frozen=True)
class PageRecheck:
    """一页这一趟的结论。**逐页**的一份，不是整格的一份。

    整格的状态（``RecheckRecord.state``）是这几页折出来的，而这一份是它折的依据：
    「这一格说没变」在报告里要能一路查到「哪一页、什么时候取的、比的是哪两个散列、
    拿到了什么状态码」。少了这一层，「没变」就只是一句话。

    ## 三个字段决定它是哪一格

    * ``expected`` / ``observed`` —— 比的那两个散列。``observed`` 为空 = 这一趟没拿到
      可比的东西。
    * ``fetched`` —— 这一趟**为这一页发过请求**。区分「复用了手上的证据」（没发）与
      「发了、没拿到」（发了）的唯一依据；两者都让 ``observed`` 为空。
    * ``deferred`` —— 这一趟**没看**这一页的原因（额度不够、这一页排在被砍掉的那一段
      里）。它与 ``error`` 不同：``error`` 是「看了、没看成」，``deferred`` 是「没看」。

    ``cause`` 是「变了」的时候**为什么算变**：``content``（页面指纹不同）与 ``parser``
    （解析器名不同）各记各的 —— 「世界变了」与「我们换了抽法」是两句不同的话，而它们
    对业务的影响完全不同（见 ``RecheckState.CHANGED``）。
    """

    url: str
    expected: str = ""
    observed: str = ""
    cause: tuple[str, ...] = ()
    parser: str = ""
    #: 写下 ``expected`` 那一行时的解析器名。与 ``parser``（这一次用的那个）分开报：
    #: 两者不同**就是** ``cause`` 里的 ``parser``，而把它折进一个字段就再也说不出
    #: 「从哪个换成了哪个」。
    parser_before: str = ""
    evidence_ids: tuple[str, ...] = ()
    error: str = ""
    reason: str = ""
    #: 这一趟没看这一页的原因（与 ``error`` 不同，见 docstring）。
    deferred: str = ""
    fetched: bool = False
    status_code: int | None = None
    tier: str = ""

    @property
    def compared(self) -> bool:
        """这一趟真的比过一次（拿到了可比的东西）。"""
        return bool(self.expected) and bool(self.observed)

    @property
    def changed(self) -> bool:
        return bool(self.cause)

    @property
    def state(self) -> RecheckState:
        """这一页在这一趟走到了 ``RecheckState`` 的哪一格。

        与 ``RecheckState`` 用同一组词而不是另立一套：逐页与整格的两份报告要能并排读，
        而「这一页复用了」与「这一格复用了」是同一件事在两种粒度上的说法。

        ``error`` **排在 ``cause`` 前面**，这不是随手定的次序：``CHANGED`` 的契约逐字
        是「指纹不同**或**解析版本变了，**于是落了新证据**」，而落不下去的那一页没有
        满足后半句 —— 它的 ``cause`` 记着「世界确实变了」（下一轮还会再看到它），但
        它的结论是「这一趟没拿到可用的读数」。反过来写（``cause`` 优先）的后果是：一次
        **落盘失败**会在整格那一层被读成一次成功的变更，于是整格续期一个周期，而那份
        新内容一行都不在盘上 —— 下一轮不会再有人去看它。
        """
        if self.error:
            return RecheckState.UNREACHABLE
        if self.cause:
            return RecheckState.CHANGED
        if self.observed:
            return RecheckState.UNCHANGED
        if self.expected and not (self.fetched or self.deferred):
            return RecheckState.REUSED
        return RecheckState.UNREACHABLE

    def payload(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "state": self.state.value,
            "expected": self.expected,
            "observed": self.observed,
            "cause": list(self.cause),
            "parser": self.parser,
            "parser_before": self.parser_before,
            "evidence_ids": list(self.evidence_ids),
            "error": self.error,
            "reason": self.reason,
            "deferred": self.deferred,
            "status_code": self.status_code,
            "tier": self.tier,
        }


@dataclass(frozen=True)
class RecheckRecord:
    """一格页面事实的一次复审记录：**这一趟看了哪些页、看到了什么**。

    ``state`` 是这一**格**的结论（四格里的一格），``pages`` 是它的依据。两者都可能
    与「直觉」不同，而那是刻意的：

    * 一页没抓也可能写观测 —— 本轮另一个面把同一批页面取回来了（``REUSED`` 且那些页
      真的被看过）。见模块 docstring 那一节。
    * 十页看了九页仍是 ``UNREACHABLE`` —— 没看全就不给整格续期。逐页的结论照旧在
      ``pages`` 里，读的人看得到是哪一页、为什么。

    ``reused_evidence_ids`` 与 ``evidence_ids`` 分开：前者是**这一趟没有重新取、
    直接回引用的**那些行，后者是这一趟**新落下的**行。合成一份之后，「这一轮花了
    多少钱、换了多少新东西」这两个数就都读不出来了。

    ``policy_version`` 是这一趟依据的那份复核计划（``RefreshPlan.policy_version``）
    —— 「隔多少天算过期」这个判断的出处。空串 = 这一趟没有依据任何计划（没接线的
    那一支）。
    """

    workspace: str
    subject_kind: str
    subject_id: str
    aspect: str
    state: RecheckState
    taken_at: datetime
    pages: tuple[PageRecheck, ...]
    reused_evidence_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    policy_version: str = ""
    note: str = ""

    @property
    def page_set_size(self) -> int:
        """这一趟**该看**的页数。不看的那些也在 ``pages`` 里（带 ``deferred``）。"""
        return len(self.pages)

    @property
    def examined(self) -> int:
        """这一趟**有了结论**的页数。复用 / 没变 / 变了各算一页；没看的不算。"""
        return sum(1 for page in self.pages if page.state is not RecheckState.UNREACHABLE)

    @property
    def changed_pages(self) -> tuple[PageRecheck, ...]:
        return tuple(page for page in self.pages if page.changed)

    def payload(self) -> dict[str, Any]:
        """进摘要的那一部分。**不含 ``taken_at``** —— 理由与 ``EvidenceCensus`` 逐字相同。"""
        return {
            "kind": RECHECK_KIND,
            "workspace": self.workspace,
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "aspect": self.aspect,
            "state": self.state.value,
            "page_set_size": self.page_set_size,
            "examined": self.examined,
            "pages": [page.payload() for page in self.pages],
            "reused_evidence_ids": list(self.reused_evidence_ids),
            "evidence_ids": list(self.evidence_ids),
            "policy_version": self.policy_version,
            "note": self.note,
        }

    @property
    def digest(self) -> str:
        return content_digest(self.payload())

    def as_dict(self) -> dict[str, Any]:
        return {**self.payload(), "taken_at": self.taken_at.isoformat(), "digest": self.digest}


@dataclass(frozen=True)
class TickPages:
    """**一轮 tick** 里某一批页面被看成了什么样 —— 页面备忘里的一格。

    两个页面面看的是同一批页面，而它们可以在同一轮里各自到期。第一个面把页面取回来
    之后把结论放进这里，第二个面直接复用，于是那批页面**一轮只被取一次**。

    ``observed_at`` 是写进去那一格**写进账的**观察时刻，第二个面照抄它、不自己算：
    两格看的是同一批页面、同一批结论，而各自算一遍的后果是同一个事实在同一轮里
    有了两个观察时刻。它是 ``None`` = 那一格没写观测（今天不会发生 —— 见
    ``recheck_pages`` 里写这一格的那个位置，只有「每一页都有了结论」才会走到），
    留着它是为了让那个前提**写在类型里**而不是靠读代码的时候记住。

    备忘的寿命就是一轮 tick：它由 ``build_refresher`` 持有一个局部变量，而那个函数
    一轮调一次。放长它的后果是拿上一轮的结论给这一轮续期，而账上显示的是「这一轮
    没花钱」。
    """

    pages: tuple[PageRecheck, ...]
    observed_at: datetime | None


def _clock(at: datetime) -> datetime:
    """一个带时区的时刻，或者一条拒绝。两条复核路共用同一处判据。"""
    if at.tzinfo is None:
        raise ValueError("at 必须带时区 —— 见 domain/util.to_utc 的说明")
    return to_utc(at)


# ----------------------------------------------------------------------
# 接线：页面复核要哪几样，缺了各意味着什么
# ----------------------------------------------------------------------

#: 页面复核要的四样接线，以及缺了它们各意味着什么。
#:
#: 名字是**回执里的键**（``adapters/mcp_server/product_loop.py`` 逐字转发）与
#: **盘上那条失败记录里的名字**，所以它是一份对外契约：改这里会让报告里那一栏的取值
#: 变掉，而两份报告用的是同一份。
WIRING_MEANING: Mapping[str, str] = {
    "plan": "这一版 Scene 的复核节奏（refresh.rules）—— 没有它判不出「手上这份证据还算不算新鲜」",
    "acquisition": "抓取入口（DocumentAcquisition）—— 没有它就没有把页面取回来的路",
    "router": "解析路由（DocumentRouter）—— 没有它判不出这一页的解析版本还是不是当前的",
    "documents": "抓取额度的入口（RunBudget.for_documents）—— 没有它就没有「一次复核最多看几页」这句声明",
}


def missing_wiring(
    *,
    plan: RefreshPlan | None = None,
    acquisition: DocumentAcquisition | None = None,
    router: DocumentRouter | None = None,
    documents: Callable[[str], RunBudget] | None = None,
) -> tuple[str, ...]:
    """页面复核缺哪几样接线，按 :data:`WIRING_MEANING` 的次序。

    **缺一样不是异常，是一条要报出来的事实**（与抓取层的 ``browser_not_wired`` 同一条
    判据）：一个没接抓取的部署仍然要能 tick —— ``source_coverage`` 那一面照常复核 ——
    而它页面那几格的处置是「这一趟没做成，因为缺 X」。所以这里返回**名字**，由调用方
    决定是写进那一格的记录还是印进回执。

    ``plan`` 也在这一组里，尽管它不是「接线」：它来自**某一版 Scene**（``version.refresh``），
    而一版都没有的场景（``DRAFT``）同样说不出「隔多少天算过期」。它与另外三样的处置
    完全相同（这一格做不成、原因说得出、下一轮再试），所以它们共用一格而不是分成两类。
    """
    return tuple(
        name
        for name, value in (
            ("plan", plan),
            ("acquisition", acquisition),
            ("router", router),
            ("documents", documents),
        )
        if value is None
    )


def document_scope(subject_id: str, at: datetime) -> str:
    """一次复核的抓取额度记在谁名下：``<主体 id>@<这一轮的时刻>``。

    ``RunBudget.for_documents`` 会在前面接上 ``<profile>:``，所以账上的 scope 是
    ``<profile>:<主体 id>@<这一轮的时刻>``。**时刻进 scope 是承重的**：账本没有充值
    机制，一个不带时刻的 label 等于「这个主体一辈子就这么多」，而一个长期经营的部署
    在抓满之后再也复核不了 —— 报告上显示的是「没有到期的格子」（见 ``budget.py``）。

    这里只拼 label，不碰账本：上限从哪来、扣在哪个科目上都是 ``RunBudget`` 的事，
    两处各拼一遍的话，「这一轮的时刻」会在其中一处变成「今天」。
    """
    checked = check_canonical_id(subject_id, "subject_id")
    return f"{checked}@{_clock(at).isoformat()}"


# ----------------------------------------------------------------------
# 落盘：两种记录，一个读法
# ----------------------------------------------------------------------


def census_path(root: Path, census: EvidenceCensus) -> Path:
    """``root/<workspace>/<subject_kind>--<subject_id>--<digest>.json``。

    四段都在文件名或目录名里，因为三者都是**读取时要用到的**：``workspace``
    是授权链的第一段，``subject_kind`` 与 ``subject_id`` 是主体，摘要是「读到
    的是哪一份」。
    """
    name = f"{census.subject_kind}--{census.subject_id}--{census.digest}{_CENSUS_SUFFIX}"
    return root / census.workspace / name


def recheck_path(root: Path, record: RecheckRecord) -> Path:
    """``root/<workspace>/recheck--<kind>--<id>--<aspect>--<digest>.json``。

    ``recheck--`` 前缀是**读的时候**唯一用得上的那一格：目录里只有两种记录，而盘上
    还有 B22 之前写下的盘点（它们的 ``kind`` 那一格是缺的，见模块 docstring）。按
    前缀分开之后，两种记录不可能被读成对方，而 ``kind`` 那一格在内容里再核一次。
    """
    name = (
        f"{RECHECK_KIND}--{record.subject_kind}--{record.subject_id}"
        f"--{record.aspect}--{record.digest}{_CENSUS_SUFFIX}"
    )
    return root / record.workspace / name


def write_census(census: EvidenceCensus, *, root: Path) -> Path:
    """把盘点写到 ``root`` 下，返回路径。**同一个摘要只写一次。**

    已经存在就不重写：内容按定义相同（摘要取自内容），而重写会让盘上那一刻的
    文件 mtime 变新，于是「这份盘点是哪一次取的就没了」—— ``write_receipt``
    对重放取的是同一条处置（第一次读的时刻留在盘上）。
    """
    return _write_once(census_path(root, census), census.as_dict())


def write_recheck(record: RecheckRecord, *, root: Path) -> Path:
    """把复审记录写到 ``root`` 下，返回路径。**同一个摘要只写一次。**

    与 :func:`write_census` 同一条处置，理由也逐字相同（重写会让「这一份是哪一次取的」
    消失）。这里值得单独说一句的是**它带来的一个后果**：内容完全相同的两次复核落在
    同一个文件名上，于是第二轮 tick 的 ``result_ref`` 与第一轮**逐字相同**。那是实话
    ——两趟看到的是同一批页面、同一批结论 —— 而两趟各自在自己的回执上（``claimed_at``
    / ``finished_at``）说得出它们是两次。
    """
    return _write_once(recheck_path(root, record), record.as_dict())


def _write_once(path: Path, payload: Mapping[str, Any]) -> Path:
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _read_verified(path: Path) -> tuple[str, Mapping[str, Any]]:
    """读一份记录、**重算**一次摘要核对它，并报出**它自己说**它是哪一种。

    ## 前两次核对与本文件里那一份的 ``kind`` 无关，所以它们住在这里

    1. **声明的摘要与文件名** —— 对不上说明这一份被挪过位置，或者它是别人拿别处
       的一份改名来的。
    2. **声明的摘要与内容** —— 把内容那一半挑出来（去掉 :data:`_PROVENANCE_KEYS`）
       重算一次。少了这一步，「摘要与文件名相符」只证明了**文件名与文件里写的那个
       字符串一致**，而任何一次对内容的编辑都不会被发现 —— 一句声明与一份内容之间
       没有第三样东西把它们连起来，那声明就只是文件自己说的话。

    第三次核对（「它是不是我要的那一种」）留在 :func:`_load_record` 里，因为那一步
    要的是一件**读的人手里才有的**东西：他想读的是哪一种。

    Raises:
        ConfigError: 文件不在、不是 JSON、不是对象、声明与文件名不符、内容与声明
            不符。**五种都不是「空记录」。** 一个读不出来的 ``result_ref`` 意味着
            那一格的产出无法复核，而报告把它显示成「0 个缺口」会让一次损坏读成
            一次好消息。
    """
    if not path.is_file():
        raise ConfigError(f"记录文件不在：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"记录文件读不出来：{path}（{exc}）") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"记录文件不是一个对象：{path}")
    recorded = payload.get("digest")
    if not isinstance(recorded, str) or recorded not in path.name:
        raise ConfigError(
            f"记录文件的摘要与文件名对不上：{path}\n"
            "文件名里的摘要取自内容（不含 taken_at），对不上说明这一份被挪过位置，"
            "或者它不是按 write_census / write_recheck 写出来的。"
        )
    content = {key: value for key, value in payload.items() if key not in _PROVENANCE_KEYS}
    actual = content_digest(content)
    if actual != recorded:
        raise ConfigError(
            f"记录文件的内容与它自己声明的摘要对不上：{path}\n"
            f"  声明 {recorded}，按内容重算是 {actual}。\n"
            "这一份被改过（或者它不是在 write_census / write_recheck 里写下来的）。"
            "**它不是「0 个缺口」** —— 读不出来与读到一个好消息是两件事。"
        )
    found = payload.get("kind", CENSUS_KIND)
    return found, payload


def _load_record(path: Path, *, kind: str) -> Mapping[str, Any]:
    """读一份记录，核对它，并核对它**是不是我要的那一种**。

    第三种失败（它自己写着另一种）挡住的是**一份盘上的记录被读成另一种**。缺
    ``kind`` 那一格按 ``"census"`` 处置：写下它的那个版本只写过盘点（重查记录从
    写下的第一天起就带这一格），所以缺省有唯一解，不是猜。

    Raises:
        ConfigError: 见 :func:`_read_verified`，外加「它是另一种记录」。
    """
    found, payload = _read_verified(path)
    if found != kind:
        raise ConfigError(
            f"这一份不是 {kind} 记录，它自己写着 {found!r}：{path}\n"
            "两种记录落在同一个目录下，读的人只能靠内容里那一格分辨 —— 与文件名里的前缀"
            "是同一件事的两处说法，而对不上说明这一份被改过名字或改过内容。"
        )
    return payload


def load_census(path: Path) -> Mapping[str, Any]:
    """读一份**盘点**，并核对它（见 :func:`_load_record`）。"""
    return _load_record(path, kind=CENSUS_KIND)


def load_recheck(path: Path) -> Mapping[str, Any]:
    """读一份**复审记录**，并核对它（见 :func:`_load_record`）。"""
    return _load_record(path, kind=RECHECK_KIND)


def load_outcome(path: Path) -> tuple[str, Mapping[str, Any]]:
    """读一份产出，并报出**它是哪一种**（``kind``）。**读的人不必先知道答案。**

    ## 它为什么存在（B22）

    ``result_ref`` 只是一条路径，而同一个目录下住着两种记录（盘点与复审记录）。
    报告与控制面手里只有那条路径，于是「这是哪一种」必须有**一个**地方回答。

    两条别的写法都被否掉了：

    * **按 aspect 猜** —— 那是把 ``build_refresher`` 的派发表抄第二遍，而两个产地
      漂移的形态恰好是最坏的那种：报告把一份复审记录当盘点读，于是那一格显示为
      「读不出来」。**一次真实的重查会被读成一次损坏。**
    * **先试盘点、失败再试复审** —— 「读不出来」于是不再是一个结论，而是「两种都
      不是」的余数；调用方再也分不开「这一份是另一种」与「这一份坏了」。

    所以这一份**自己说它是谁**（``kind`` 进摘要，改它会在内容核对那一步就红），
    而读者照它说的核一次。返回值是 ``(kind, payload)``，``kind`` 的取值是
    ``"census"`` / ``"recheck"``；它可能是第三种拼写（有人手写过一份），所以调用方
    要按**不认识**处置而不是当成某一种 —— 缺省成 ``"census"`` 的那一条只对**没有
    kind 那一格**的旧文件成立（见 :func:`_load_record`）。

    Raises:
        ConfigError: 见 :func:`_read_verified`。
    """
    return _read_verified(path)


# ----------------------------------------------------------------------
# 第一格：来源供给（读本地记录）
# ----------------------------------------------------------------------


def census_for(
    *,
    store: EvidenceStore | None,
    workspace: str,
    subject_kind: str,
    subject_id: str,
    at: datetime,
) -> EvidenceCensus:
    """把 ``subject_id`` 名下的证据读一遍，回一份盘点。

    ``store`` 为 ``None``（库文件还不存在，``tools.read_store`` 的契约）时
    ``rows=0``、``store_present=False``：**手上确实一条都没有**。这不是一次
    「读不到」，而是一次读到了空。

    Raises:
        ValueError: ``subject_id`` 不是规范形式。**拒绝，而不是 ``slugify``** ——
            ``slugify`` 会把两个不同的 id 映到同一个文件名上（本仓已经量过这个
            形态：``Evidence.account_id`` 没有校验器而 ``_path_for`` 会 slugify），
            而后果是两份盘点被静默合并成一份。这里失败关闭。
    """
    checked_id = check_canonical_id(subject_id, "subject_id")
    checked_workspace = check_canonical_id(workspace, "workspace")
    kind = check_slug_safe(subject_kind, "subject_kind")

    rows = [] if store is None else list(store.list_evidence(checked_id))
    per_kind: dict[str, int] = {}
    for row in rows:
        per_kind[row.source_kind.value] = per_kind.get(row.source_kind.value, 0) + 1
    bindable = sorted(kind for kind in per_kind if _is_bindable(kind))
    fetched = [row.fetched_at for row in rows]
    return EvidenceCensus(
        workspace=checked_workspace,
        subject_kind=kind,
        subject_id=checked_id,
        taken_at=_clock(at),
        store_present=store is not None,
        rows=len(rows),
        kinds=tuple(sorted(per_kind)),
        bindable_kinds=tuple(bindable),
        per_kind=tuple(sorted(per_kind.items())),
        newest_fetched_at=max(fetched).isoformat() if fetched else "",
    )


def _is_bindable(kind_value: str) -> bool:
    """``SourceKind`` 的**取值**是不是可绑定的那一类。

    ``BINDING_ELIGIBLE_SOURCES`` 是唯一的判据处（``domain/bind.py`` 逐字：只有
    它里面的来源可以成为路线的依据）。这里按取值比较而不是按枚举成员，是因为
    盘点要能与盘上那份 JSON 逐字对照 —— 一个只在内存里有意义的成员在这里
    没有对应物。取值对不上时**不是**回落成 False：``SourceKind`` 是闭集，
    对不上说明证据行里出现了本仓不认识的层级，而那种行的可绑定性是未知的，
    不是否定的。
    """
    for member in BINDING_ELIGIBLE_SOURCES:
        if member.value == kind_value:
            return True
    return False


def refresh_source_supply(
    *,
    due: DueWork,
    store: EvidenceStore | None,
    root: Path,
    workspace: str,
    at: datetime,
) -> MaintenanceOutcome:
    """复核一格来源供给：读一遍本地记录，落一份盘点，报出缺口。

    它**自己一行观测都不写**，只在 outcome 里声明 ``observed_at`` —— 写那一行的是
    维护账的主人（``run_once``）。这一步反转了什么、没反转什么，写在模块 docstring
    的「这一层不写观测，但这两趟都**算**观测」一节里。

    Raisew:
        ConfigError: 这一格问的不是来源供给。派发侧按
            :data:`REFRESHABLE_ASPECTS` 过滤过，所以这是**调用方给错了**，
            走异常那条通道。
    """
    if due.aspect is not Aspect.SOURCE_COVERAGE:
        raise ConfigError(
            f"refresh_source_supply 只做 {Aspect.SOURCE_COVERAGE.value}，"
            f"这一格问的是 {due.aspect.value}。本部署声明得出来的面在 "
            "application/refresh.py::REFRESHABLE_ASPECTS 里。"
        )
    at = _clock(at)
    census = census_for(
        store=store,
        workspace=workspace,
        subject_kind=due.subject_kind,
        subject_id=due.subject_id,
        at=at,
    )
    path = write_census(census, root=root)
    return MaintenanceOutcome(
        result_ref=str(path),
        source_delta=SourceDelta(
            new_source_gaps=census.new_source_gaps,
            # 永远是 None：见 NEW_VENUES_REASON。**不许写成 0。**
            new_venues=None,
        ),
        # 这一面**真的**看到了：``SOURCE_COVERAGE`` 问的是「我们手上有哪些来源」，
        # 而这个问题的主语就是库里的那些行 —— 把它们整个数一遍**就是**在观测它。
        # 理由写在模块 docstring 的「这一层不写观测，但这两趟都算观测」一节，
        # 连同它反转的那条旧纪律一起。
        #
        # 时刻是 ``at``（这一趟跑的时刻）而不是 ``newest_fetched_at``：这一面的主语
        # 是「手上有什么」，那是一次**此刻**的满覆盖测量 —— 库里一条证据都没有也是
        # 一次测量（测出了「什么都没有」），不能把它的时刻写成空。
        observed_at=at,
    )


# ----------------------------------------------------------------------
# 第二、三格：页面事实（重取已知页面）
# ----------------------------------------------------------------------

#: 一页**这一趟没抓**的原因：本工作区里已有一份仍然新鲜、解析器也没换的证据。
_REUSE_REASON = "evidence_fresh"

#: 本轮另一个面已经把这一批页面取回来了 —— 同一趟里共用，不重复花钱。
_SHARED_REASON = "shared_with_another_aspect_in_this_tick"

#: 复核一页时用的取回方式。**一处定义**：复用台账的键把 ``mode`` 算进去，而请求本身
#: 也带一个 —— 写成两个字面量迟早会漂成一个键说 ``auto``、请求说 ``http``，那时台账
#: 按一个不存在的变体记账，而每一次判定都落成「这一轮没人取过」。
_RECHECK_MODE = FetchMode.AUTO


def _with_sharing(note: str, pages: Sequence[PageRecheck]) -> str:
    """把「其中几页是共享来的」接在整格那一句注后面。

    **认的是逐页的 ``reason``，不是另立一格状态**：共享不改变这一页的结论（它照旧
    被比过一次，变了就照旧落了新证据），改的只是「这次取回是谁付的钱」。把它做成
    第五个 ``RecheckState`` 会让「这一格是 CHANGED 还是 REUSED」这个问句多出一个
    与变化无关的答案，而读那一格的人要找的正是「变了没有」。

    所以整格那一句只说得出「几页、从谁那里」，逐页记录里那一句说的是同一件事的
    细节（``shared_within_range:<捐赠方>;<捐赠方的升级判定>``）。两句都从
    :data:`marketops.application.reuse.SHARED_PREFIX` 认，一处定义。
    """
    shared = [page for page in pages if page.reason.startswith(SHARED_PREFIX)]
    if not shared:
        return note
    donors = sorted({page.reason.split(";")[0][len(SHARED_PREFIX):] for page in shared})
    return (
        f"{note} 其中 {len(shared)} 页的取回是从本工作区另一个主体共享来的"
        f"（{'、'.join(donors)}），那几次没有重复付费。"
    )


def _page_targets(rows: Sequence[Evidence]) -> tuple[PageTarget, ...]:
    """证据行 → 要复核的页面集合。**按 URL 去重，一页一个目标。**

    只收可绑定的来源（``BINDING_ELIGIBLE_SOURCES``）：一条检索摘要背后没有一个「页面」
    可以重取，把它算成一个目标会让这一格每次都去抓一个不存在的东西。``content_sha256``
    为空的行走不到这里（没有指纹就没有可比的量），而那是一种**要说出来**的状态而不是
    静默跳过 —— 今天没有生产者（``evidence_ingest`` 每一行都带它），所以它不在这层
    的判据里，写在这里只是为了下一个改动者知道这一步不是「随手过滤」。

    同一个 URL 有多行时取 ``(fetched_at, evidence_id)`` 最大的那一行：最新的那一行
    说的才是这一页现在的样子，而 ``evidence_id`` 参与比较只是为了让**次序**在两次
    读之间稳定（两行的时刻可以逐微秒相同，那时代码里的「取最大」必须仍然是确定的）。

    返回按 ``(fetched_at, url)`` 升序 —— 额度不够时被砍掉的是**排在前面的那些之后**的
    页，而 `fetched_at` 最旧的那些先看：最久没看过的东西最该先看，这条次序本身就是
    「额度不够时先看谁」的那条规则。
    """
    newest: dict[str, PageTarget] = {}
    for row in rows:
        if not _is_bindable(row.source_kind.value) or not row.content_sha256:
            continue
        target = PageTarget(
            url=row.source_url,
            expected=row.content_sha256,
            parser=row.parser,
            fetched_at=row.fetched_at,
            evidence_id=row.evidence_id,
        )
        current = newest.get(target.url)
        if current is None or (target.fetched_at, target.evidence_id) > (
            current.fetched_at,
            current.evidence_id,
        ):
            newest[target.url] = target
    return tuple(sorted(newest.values(), key=lambda item: (item.fetched_at, item.url)))


def _recheck_page(
    *,
    target: PageTarget,
    acquisition: DocumentAcquisition,
    router: DocumentRouter,
    budget: RunBudget,
    store: EvidenceStore | None,
    workspace: str,
    subject_id: str,
    at: datetime,
    reuse: ReuseLedger | None = None,
) -> PageRecheck:
    """取回一页、解析、与指纹比一次；变了就把新证据落下。

    ## 复用判定排在**预留之前**

    ``reuse`` 非空时先问一次台账（:meth:`ReuseLedger.decision`）：同一个许可范围里
    这一轮已经有人取过这个 URL 且拿到了字节，就**不发这一次请求**，直接把那一份
    字节落进本主体的目录再比一次（见 ``application/reuse.py``）。

    次序是承重的。排在 ``budget.reserve`` **之前**，那一次取回的费用才真的只付一遍
    —— 排在之后，额度已经先扣了，「不重复费用」就只剩一句注释。同理也不走到
    ``settles_itself`` 那一支：那一支的承诺是「重发同一个请求不会执行两次」，而这里
    连发都不发。

    复用命中时这一页**没有**自己的 ``FetchAttempt`` —— 我们没发请求，那一行不该
    存在（理由写在 ``application/reuse.py`` 的模块 docstring）。它的逐页记录照旧
    说自己被共享了，靠的是 ``reason`` 以 ``shared_within_range:`` 开头的那一句 ——
    **不是**靠下面那个 ``fetched=False``：那一格留在内存里（:meth:`PageRecheck.state`
    用它把 ``REUSED`` 与 ``UNREACHABLE`` 分开），而它不进 ``PageRecheck.payload()``
    —— 理由与 :data:`~marketops.application.reuse.SHARED_PREFIX` 同一条。

    ## 账怎么记（与 ``application/billed_search.py`` 逐字同一条）

    三个动作里只有两个会被调用：``reserve`` 在发请求**之前**，``spend`` 在**拿到字节
    之后**。够不着、超时、provider 报错时**没有第三个动作** —— 不 refund、不重试、
    不改预留（§8：那时你并不知道对方是否已经计费）。所以这一页的预留会一直扣着额度，
    直到有人显式退款，而那正是「有一笔没对账」应当留下的样子。

    ``spend`` 的判据是**拿到了字节**（``final.body_path`` 非空），不是「解析成功了」：
    钱是为那一次取回花的，而解析失败不改变对方已经把页面给了我们这个事实。

    ## 预留的名字要进请求

    上面那一份预留的名字**写进这一次抓取的请求**（``FetchRequest.reservation_id``），
    于是这一页真的发出去的那几跳在尝试账上指着它（``adapters/fetch_attempts.py``
    从请求上读出那两格）。**不发这一格也不会报错** —— 那一行会照旧读成
    ``NOT_APPLICABLE``，也就是「这一次没花任何额度」，而事实是额度已经扣住了：
    被拦下、超时、够不着的那几次不 ``spend``，预留就一直扣着（见上一段）。
    于是「有一笔没对账」这件事的唯一痕迹，是账上那一行指着一份预留。

    次序因此是承重的：请求先建（复用判定要用它的 URL），预留拿到之后**才**补上这
    一格 —— 在预留之前那一格还不存在，写在前面就得靠别处补，而漏补不报错。

    ## 解析用哪一个解析器

    ``router.parse`` 自己按文件与 content-type 挑（那是 ``DocumentRouter`` 的职责），
    这里不替它选。**指纹取 ``ParsedDocument.sha256``** —— 那是**文件字节**的散列，与
    ``Evidence.content_sha256`` 是同一个量；``FetchResult.body_sha256`` 是**解码后
    文本**的散列，拿它来比会让每一页都读成"变了"。
    """
    request = FetchRequest(url=target.url, scope=subject_id, purpose="recheck")
    if reuse is not None:
        shared = reuse.reuse(request, mode=_RECHECK_MODE)
        if shared is not None:
            return _recheck_body(
                target=target,
                final=shared,
                # 这一趟**没有为这一页发请求** —— 那是 ``fetched`` 那一格的全部内容。
                fetched=False,
                reason=shared.reason,
                router=router,
                store=store,
                workspace=workspace,
                subject_id=subject_id,
                at=at,
            )
    try:
        reservation = budget.reserve(idempotency_key=f"document:{target.url}")
    except BudgetExhaustedError as error:
        # 额度不够**不是这一页的失败**：这一页根本没被看。理由里带上是哪一条额度、
        # 已用多少（异常自己说得出），因为它要人去改 profile 里的上限。
        return PageRecheck(
            url=target.url,
            expected=target.expected,
            parser_before=target.parser,
            deferred=f"document_budget_exhausted: {error}",
        )
    if reservation.settles_itself:
        # 同一个幂等键已经被结算过：这一轮里这一页已经走过一次（或上一次 tick 的同一
        # 轮重放）。**不重发请求** —— 幂等的承诺就是「重发同一个请求不会执行两次」，
        # 而在这里再发一次是唯一能打破它的做法。
        return PageRecheck(
            url=target.url,
            expected=target.expected,
            parser_before=target.parser,
            deferred="already_settled",
        )

    # 预留的名字在**拿到之后**才进请求：这一格记的是「这一次抓取押着哪一份额度」，
    # 而它在上面那一行之前还不存在。放在构造处（上面）就意味着要另有一处补上它，
    # 而漏补的那一步不报错 —— 账上只会少一句话。
    request = replace(request, reservation_id=reservation.reservation_id)
    fetch = acquisition.fetch_document(request, mode=_RECHECK_MODE)
    final = fetch.final
    if final.body_path is not None:
        budget.spend(reservation)
    if reuse is not None:
        # 记在**预留之后**：拿不到字节的那一次照样进台账（「有人试过、没拿到字节」
        # 与「这一轮没人取过」是两件事），而它只在真的走过这条路径时才该被记下。
        reuse.remember(
            request, mode=_RECHECK_MODE, result=final, decision=fetch.decision
        )
    return _recheck_body(
        target=target,
        final=final,
        fetched=True,
        reason=fetch.decision,
        router=router,
        store=store,
        workspace=workspace,
        subject_id=subject_id,
        at=at,
    )


def _recheck_body(
    *,
    target: PageTarget,
    final: FetchResult,
    fetched: bool,
    reason: str,
    router: DocumentRouter,
    store: EvidenceStore | None,
    workspace: str,
    subject_id: str,
    at: datetime,
) -> PageRecheck:
    """手上这份字节（自己取的或共享来的）与记录比一次；变了就落新证据。

    与 :func:`_recheck_page` 分开，是因为**比一次**这件事与「这份字节怎么来的」
    无关：``fetched`` 与 ``reason`` 是这一页的来源，而解析、比对、落盘这三步对
    两条路径是同一套判据。合并回一处的写法要在中间插一个分支，而那个分支里每一行
    都得重写一遍 —— 两条路径的判据一旦分家，共享来的那一页就会先在某一次改动里
    悄悄少比一样东西。

    ``fetched`` 与 ``reason`` 都由调用方给：它们答的是「这一趟为这一页发过请求没有」
    与「为什么是这个结果」，而这两个问题的答案只有调用方知道。
    """
    common = {
        "url": target.url,
        "expected": target.expected,
        "fetched": fetched,
        "parser_before": target.parser,
        "reason": reason,
        "status_code": final.status_code,
        "tier": final.tier.value,
    }
    if final.body_path is None:
        # 够不着：这一页的世界没有被看到。**不写观测**，由整格的 UNREACHABLE 承载。
        return PageRecheck(error=f"{final.outcome.value}: {final.error}".strip(": "), **common)
    try:
        document = router.parse(final.body_path, content_type=final.content_type)
    except (ParseError, ParserUnavailableError) as error:
        return PageRecheck(error=f"{type(error).__name__}: {error}", **common)

    cause = tuple(
        name
        for name, differs in (
            ("content", document.sha256 != target.expected),
            ("parser", bool(target.parser) and document.parser != target.parser),
        )
        if differs
    )
    if not cause:
        return PageRecheck(observed=document.sha256, parser=document.parser, **common)

    # 变了 —— 而 ``CHANGED`` 的含义逐字是「于是落了新证据」。落不下去的那些情形
    # （存储拒绝、这一页没有可引用的正文）**不是一次变化**：没落到盘上的东西，
    # 下一轮复核还会再看到一次，而那时它仍然说得出「这一页变了」。
    landed, why = _land_changed(
        store=store,
        workspace=workspace,
        subject_id=subject_id,
        url=final.final_url or target.url,
        document=document,
        fetched_at=at,
        http_status=final.status_code,
        tier=final.tier.value,
    )
    if not landed:
        return PageRecheck(
            cause=cause,
            parser=document.parser,
            error=f"changed_but_nothing_landed: {why}",
            **common,
        )
    return PageRecheck(
        observed=document.sha256,
        cause=cause,
        parser=document.parser,
        evidence_ids=landed,
        **common,
    )


def _land_changed(
    *,
    store: EvidenceStore | None,
    workspace: str,
    subject_id: str,
    url: str,
    document: ParsedDocument,
    fetched_at: datetime,
    http_status: int | None,
    tier: str,
) -> tuple[tuple[str, ...], str]:
    """把一页变了之后的新内容落成证据行。

    Returns:
        ``(落下的 id, 落不下去的原因)`` —— **恰好一个非空**。空 id 配上一条原因是
        「这一页变了而一行都没落」，那不是一个可以忽略的返回值：指纹变了而没有任何
        东西落在盘上时，下一轮复核会再看到同一次变更，而这一格永远说不出「它变了
        什么」。所以调用方把它记成逐页**错误**，而不是记成一次变化。

    ## 两行的 id 可能撞成一个 —— 留下标题那一行（照 ``discovery_run`` 的先例）

    ``document_evidence`` 与 ``title_evidence`` 各自按内容寻址，而一页的正文恰好等于
    它的标题时两者会算出**同一个 id**（空壳页真的存在）。存储对「同 id 配两份内容」是
    拒绝，所以只写一行。留下**标题**那一行：两行指同一份字面，而标题那一行的位置更
    具体（``document-title`` 说的是「这是这份材料给自己写的名字」，正文那一行说的是
    「整篇都在这儿」）。判据与理由逐字来自 ``application/discovery_run.py::_ingest``。

    ## 存储自己的那句话要原样带出去

    ``put_evidence`` 拒绝时（同 id 不同内容、工作区与库的绑定不一致）**不在这里另写
    一句解释**：那一句话唯一的读者要靠它决定去查什么，而另写的一句下一版就会与存储
    漂开。所以原样放在返回的第二格里。
    """
    if store is None:
        return (), "这个部署没有证据库（store 为 None）：变更无处落盘"
    common = {
        "workspace": workspace,
        "account_id": subject_id,
        "source_url": url,
        "document": document,
        "fetched_at": fetched_at,
        "http_status": http_status,
        "tier": tier,
    }
    body_row = document_evidence(**common)
    title_row = title_evidence(**common)
    if body_row is not None and title_row is not None:
        if body_row.evidence_id == title_row.evidence_id:
            body_row = None
    landed: list[str] = []
    try:
        for row in (body_row, title_row):
            if row is None:
                continue
            store.put_evidence(row)
            landed.append(row.evidence_id)
    except EvidenceError as error:
        return (), f"{type(error).__name__}: {error}"
    if not landed:
        return (), "这一页没有可引用的正文，也没有写下的标题（两行都落不出来）"
    return tuple(landed), ""


def _recheck_outcome(
    *,
    due: DueWork,
    root: Path,
    workspace: str,
    at: datetime,
    state: RecheckState,
    pages: Sequence[PageRecheck],
    note: str = "",
    policy_version: str = "",
    reused_evidence_ids: Sequence[str] = (),
    evidence_ids: Sequence[str] = (),
    observed_at: datetime | None = None,
    failure: str = "",
) -> MaintenanceOutcome:
    """落一份复审记录，并把它变成一个 ``MaintenanceOutcome``。

    ``source_delta`` 恒为 ``None``：``new_source_gaps`` / ``new_venues`` 是
    ``source_coverage`` 那一面的概念（「这个主体手上有没有依据」），而页面复核答的是
    「我们记得的那几页现在还是不是那样」。把它填成 0 会让这一格在报告里看起来像一次
    来源盘点，而那是另一格的事。
    """
    record = RecheckRecord(
        workspace=check_canonical_id(workspace, "workspace"),
        subject_kind=check_slug_safe(due.subject_kind, "subject_kind"),
        subject_id=check_canonical_id(due.subject_id, "subject_id"),
        aspect=due.aspect.value,
        state=state,
        taken_at=at,
        pages=tuple(pages),
        reused_evidence_ids=tuple(reused_evidence_ids),
        evidence_ids=tuple(evidence_ids),
        policy_version=policy_version,
        note=note,
    )
    path = write_recheck(record, root=root)
    return MaintenanceOutcome(
        result_ref=str(path),
        source_delta=None,
        observed_at=observed_at,
        recheck=state,
        failure=failure,
    )


def recheck_pages(
    *,
    due: DueWork,
    store: EvidenceStore | None,
    root: Path,
    workspace: str,
    at: datetime,
    plan: RefreshPlan | None = None,
    acquisition: DocumentAcquisition | None = None,
    router: DocumentRouter | None = None,
    documents: Callable[[str], RunBudget] | None = None,
    pages_seen: MutableMapping[tuple[str, str], TickPages] | None = None,
    reuse: ReuseLedger | None = None,
) -> MaintenanceOutcome:
    """复核一格页面事实：把这一格名下已知的页面重取一遍，与记录的指纹比一次。

    六件事按次序发生，每一件都有一个「说得出理由的出口」（见下面各段）：

    1. **这一格是不是页面事实** —— 不是就抛（调用方给错了，走异常那条通道）。
    2. **这一格名下有哪几页** —— 从本工作区的证据行算（见模块 docstring：
       复用判定读证据行，不读文件系统）。一页都没有时这一趟**做不成**，而原因是
       「没有可看的页面」而不是「页面没变」—— 它报 ``UNREACHABLE`` + 一条失败理由，
       于是这一格下一轮会被再领一次（``FAILED`` 是存储层唯一可再认领的状态）。
    3. **本轮另一个面有没有已经取过同一批** —— 有就直接复用（``REUSED``）。
    4. **接线齐不齐** —— 缺哪一样就报哪一样（:func:`missing_wiring`），逐页写出
       ``not_wired:<名字>``。
    5. **哪几页可以复用** —— 新鲜的、解析器没换的页不取（``REUSED``，
       ``observed_at`` 是**那一份证据被取回的时刻**）。
    6. **其余逐页取回** —— 一条 URL 一次请求、一笔抓取额度。

    Args:
        pages_seen: 本轮 tick 的**内存备忘**（``(subject_kind, subject_id)`` →
            :class:`TickPages`）。由 :func:`build_refresher` 持有，寿命就是一轮 tick。
            传 ``None`` = 没有备忘（单格调用），于是这一格在这一趟里不会与别人共用
            任何东西。
        reuse: 本轮 tick 的**共享台账**（``application/reuse.py``）。同样由
            :func:`build_refresher` 持有、同样只活一轮，但**跨主体**：它在
            ``pages_seen`` 管不到的地方接着省 —— 备忘管的是「同一个主体、两个面」，
            台账管的是「同一个许可范围、两个主体拿到同一个 URL」。传 ``None`` =
            这一格不与任何人共享取回。

    Raises:
        ConfigError: 这一格不是页面事实。
        BudgetNotConfiguredError: profile 没声明 ``fetch.max_documents``。**刻意不接住**
            —— 那是 §8 的配置事故（未配置不等于无限），要有一条说得出「去写哪一格」的
            异常，而不是一条逐页的 `deferred`。它会被 ``run_once`` 记成这一格 FAILED，
            而那句话逐字保留。
    """
    if due.aspect not in PAGE_BACKED_ASPECTS:
        raise ConfigError(
            f"recheck_pages 只做页面事实"
            f"（{sorted(aspect.value for aspect in PAGE_BACKED_ASPECTS)}），"
            f"这一格问的是 {due.aspect.value}。"
        )
    at = _clock(at)
    workspace = check_canonical_id(workspace, "workspace")
    subject_id = check_canonical_id(due.subject_id, "subject_id")
    subject_kind = check_slug_safe(due.subject_kind, "subject_kind")
    policy_version = "" if plan is None else plan.policy_version
    rows = () if store is None else tuple(store.list_evidence(subject_id))
    targets = _page_targets(rows)

    def settle(
        state: RecheckState,
        pages: Sequence[PageRecheck],
        *,
        note: str = "",
        reused: Sequence[str] = (),
        landed: Sequence[str] = (),
        observed_at: datetime | None = None,
        failure: str = "",
    ) -> MaintenanceOutcome:
        return _recheck_outcome(
            due=due,
            root=root,
            workspace=workspace,
            at=at,
            state=state,
            pages=pages,
            note=note,
            policy_version=policy_version,
            reused_evidence_ids=reused,
            evidence_ids=landed,
            observed_at=observed_at,
            failure=failure,
        )

    if not targets:
        return settle(
            RecheckState.UNREACHABLE,
            (),
            note="这一格名下没有可复核的页面。",
            failure=(
                f"主体 {subject_id} 名下没有一页可复核的来源"
                f"（可绑定的来源：{'有' if store is not None else '库还不存在'}）。\n"
                "这一格要复核的是**已知页面的现状**，而没有页面时它没有可看的东西 ——"
                "该做的是给这个主体补上来源（研究那一侧），不是再 tick 一次这一格。"
            ),
        )

    key = (subject_kind, subject_id)
    shared = None if pages_seen is None else pages_seen.get(key)
    if shared is not None:
        # 备忘里只可能有一批**每一页都有结论**的结果（写那一格的地方见下面），
        # 所以这里不需要再判一次「是不是有页没看成」—— 那件事在写进备忘之前
        # 已经被处置成了 ``UNREACHABLE``。
        return settle(
            RecheckState.REUSED,
            shared.pages,
            note=_SHARED_REASON,
            observed_at=shared.observed_at,
        )

    missing = missing_wiring(
        plan=plan, acquisition=acquisition, router=router, documents=documents
    )
    if missing:
        return settle(
            RecheckState.UNREACHABLE,
            tuple(
                PageRecheck(
                    url=target.url,
                    expected=target.expected,
                    parser_before=target.parser,
                    # 一页一行，理由逐字相同：这一趟四样接线里缺了哪些。**把缺的
                    # 全列出来**（不是只报第一个）—— 逐个发现会让人来回改三轮，
                    # 而这一格本来就是一条待办，它应当一次说清。
                    deferred="not_wired:" + ",".join(missing),
                )
                for target in targets
            ),
            note="缺接线：" + "、".join(missing),
            failure=(
                f"复核页面事实要的四样接线里缺 {list(missing)}：\n"
                + "\n".join(f"  * {name}：{WIRING_MEANING[name]}" for name in missing)
                + "\n缺一样就不发请求（一次取回一半的复核比不做更糟：它会给整格续期）。"
            ),
        )

    # 从这一行起，四样接线都在（``missing`` 为空），静态类型也才说得通。
    assert plan is not None and acquisition is not None
    assert router is not None and documents is not None

    window = timedelta(days=plan.rule_for(due.aspect).after_days)
    cutoff = at - window
    current_parsers = set(router.parser_names)
    reused_pages: list[PageRecheck] = []
    reused_ids: list[str] = []
    stale: list[PageTarget] = []
    for target in targets:
        if target.fetched_at >= cutoff and target.parser in current_parsers:
            reused_pages.append(
                PageRecheck(
                    url=target.url,
                    expected=target.expected,
                    parser=target.parser,
                    parser_before=target.parser,
                    evidence_ids=(target.evidence_id,),
                    reason=_REUSE_REASON,
                )
            )
            reused_ids.append(target.evidence_id)
        else:
            stale.append(target)

    if not stale:
        # 一页都不用取：手上的证据全在时效内，解析器也没换。**这一格没有花抓取的钱**，
        # 而它写进账的时刻是**那份证据被取回的时刻**（最早的那一份）—— 写成这一趟的
        # 时刻会把「其实三个月前就看过」记成「刚刚看过」，复核周期凭空长一个周期。
        return settle(
            RecheckState.REUSED,
            reused_pages,
            note=f"全部 {len(reused_pages)} 页都在时效内（{_REUSE_REASON}）。",
            reused=reused_ids,
            observed_at=min(target.fetched_at for target in targets),
        )

    budget = documents(document_scope(subject_id, at))
    pages: list[PageRecheck] = list(reused_pages)
    landed_ids: list[str] = []
    for target in stale:
        page = _recheck_page(
            target=target,
            acquisition=acquisition,
            router=router,
            budget=budget,
            store=store,
            workspace=workspace,
            subject_id=subject_id,
            at=at,
            reuse=reuse,
        )
        pages.append(page)
        landed_ids.extend(page.evidence_ids)

    chosen = tuple(pages)
    refused = [page for page in chosen if page.state is RecheckState.UNREACHABLE]
    if refused:
        deferred = sum(1 for page in chosen if page.deferred)
        return settle(
            RecheckState.UNREACHABLE,
            chosen,
            note=_with_sharing(
                f"{len(chosen) - len(refused)} / {len(chosen)} 页有了结论"
                f"（其中 {deferred} 页没被看）。",
                chosen,
            ),
            reused=reused_ids,
            landed=landed_ids,
            failure=(
                f"这一格这一趟没看全（{len(refused)} / {len(chosen)} 页没有结论）。\n"
                "没看全就不给整格续期：剩下那几页里可能正写着这一格已经变了。逐页的结论"
                "与每一页没看成的原因都在记录里 —— 那不是一句笼统的不可达。"
            ),
        )
    if pages_seen is not None:
        # 走到这里 ⟹ 每一页都有了结论 ⟹ 没有一个 ``deferred`` ⟹ 除了从库里复用的
        # 那几页，剩下的**都真的取回来了**（``stale`` 非空，所以至少有一页是）。
        # 于是这一趟的观察时刻就是 ``at``，不是别的什么时刻。
        # **一页没看成的结果不进备忘**：让另一个面去撞同一面墙只会再花一次钱，
        # 而它撞出来的那句话不会比这一句更有用。
        pages_seen[key] = TickPages(pages=chosen, observed_at=at)
    changed = [page for page in chosen if page.changed]
    if changed:
        return settle(
            RecheckState.CHANGED,
            chosen,
            note=_with_sharing(f"{len(changed)} 页的指纹或解析版本变了。", chosen),
            reused=reused_ids,
            landed=landed_ids,
            observed_at=at,
        )
    return settle(
        RecheckState.UNCHANGED,
        chosen,
        note=_with_sharing(f"{len(chosen)} 页的指纹与记录一致。", chosen),
        reused=reused_ids,
        landed=landed_ids,
        observed_at=at,
    )


# ----------------------------------------------------------------------
# 派发：一格到期工作 → 一条 outcome
# ----------------------------------------------------------------------


def build_refresher(
    *,
    store: EvidenceStore | None,
    root: Path,
    workspace: str,
    at: datetime,
    plan: RefreshPlan | None = None,
    acquisition: DocumentAcquisition | None = None,
    router: DocumentRouter | None = None,
    documents: Callable[[str], RunBudget] | None = None,
    copy_page: PageCopier | None = None,
) -> Callable[[DueWork], MaintenanceOutcome]:
    """本部署的复核入口：``DueWork`` → ``MaintenanceOutcome``。

    做成闭包而不是类，与 ``product_loop_handlers`` 同一个理由：这一层要知道的那几样
    东西（证据库、落点、工作区、时刻，加上页面复核要的四样）在签名上看得见，而调用方
    每次 tick 取一次新的（``sqlite3`` 的连接不能跨线程）。

    ## 它持有**一轮 tick** 的页面备忘

    两个页面面看的是同一批页面，而它们可以同时到期。备忘（``(subject_kind, subject_id)``
    → 那一趟的逐页结论）让第二个面复用第一趟的结果，于是**一次 tick 里那批页面只被取
    一次**。它做成闭包里的一个局部变量，而不是一个参数或一个类字段，是因为它的寿命
    **恰好**应当是一轮 tick：``build_refresher`` 一次 tick 调一次，下一轮那些格子已经
    不是同一件工作了（``dedupe_key`` 里带着 period）。放长它的后果是拿上一轮的结论给
    这一轮续期，而账上显示的是「这一轮没花钱」。

    缺接线**不抛**：它落在那一格的记录里（``not_wired:<名字>`` 逐页一行 + 一条失败
    理由），因为 ``source_coverage`` 那一面不依赖这四样，而一个没接抓取的部署照样要能
    tick。唯一的例外是 ``documents`` 自己抛 ``BudgetNotConfiguredError``（profile 没
    声明上限）—— 那是配置事故，见 ``recheck_pages`` 的 Raises。

    没有实现的那几个面在这里**抛**，不返回一个「做完了、什么都没变」—— 后者会让
    「这一格核过了」与「这一格没实现」在账上长得一样。正常情况下走不到那一支：
    ``run_once`` 按 :data:`REFRESHABLE_ASPECTS` 在**认领之前**过滤。

    ## 它也持有**一轮 tick** 的跨主体共享台账

    ``pages_seen`` 管的是「同一个主体的两个面」，而两个**不同的主体**在同一个 tick
    里可以各自到期、各自拿着一张写着同一个 URL 的页面清单。台账
    （``application/reuse.py``）接的就是这一段：同一个许可范围里，那个 URL 这一轮
    只取一次，第二个主体拿一份字节的副本，于是那一次取回的费用不重复付。

    范围与寿命都取自**这里**，这不是一个可以调的旋钮：一次 tick 一张台账，而它的
    许可范围就是 ``workspace`` 参数 —— 一次 tick 只服务一个工作区，所以「复用不越
    工作区」是由构造方式成立的，不是由某处比较成立的（理由见那个模块的 docstring）。

    ``copy_page`` 是那份字节落进接收方目录的能力（``ports/documents.py::PageCopier``）。
    缺省 ``None`` 的含义是**这个部署落不下第二份**，于是台账一次都不共享 —— 而不是
    「省掉这一步」。注入点是本函数的两个生产调用方
    （``interfaces/controller.py`` 与 ``adapters/mcp_server/product_loop.py``），
    因为只有它们拿着 ``Settings``。
    """
    pages_seen: dict[tuple[str, str], TickPages] = {}
    # 许可范围在**构造这里**就校验（``ReuseLedger`` 会做），于是非规范的工作区会在
    # 建回调时就被拒，而不是等到某一格真的去复核页面。两处的判据是同一条
    # （``domain/util.py::check_canonical_id``）—— 工作区进路径，它不是一句注释。
    reuse = ReuseLedger(permission_range=workspace, copy_page=copy_page)

    def refresh(due: DueWork) -> MaintenanceOutcome:
        if due.aspect in CENSUS_BACKED_ASPECTS:
            return refresh_source_supply(
                due=due, store=store, root=root, workspace=workspace, at=at
            )
        if due.aspect in PAGE_BACKED_ASPECTS:
            return recheck_pages(
                due=due,
                store=store,
                root=root,
                workspace=workspace,
                at=at,
                plan=plan,
                acquisition=acquisition,
                router=router,
                documents=documents,
                pages_seen=pages_seen,
                reuse=reuse,
            )
        raise ConfigError(
            f"本部署没有 {due.aspect.value} 这一面的复核实现，"
            f"声明得出来的只有 {sorted(a.value for a in REFRESHABLE_ASPECTS)}"
            f"（盘点 {sorted(a.value for a in CENSUS_BACKED_ASPECTS)}、"
            f"页面 {sorted(a.value for a in PAGE_BACKED_ASPECTS)}）。\n"
            "这一格本该在认领**之前**被过滤掉（application/maintenance.py 的 "
            "split_by_aspect）—— 走到这里说明过滤没生效。"
        )

    return refresh

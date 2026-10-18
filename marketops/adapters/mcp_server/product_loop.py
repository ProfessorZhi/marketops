"""产品环（``docs/MCP_PRODUCT_LOOP.md`` §2）那些工具的处理器。

到今天为止是十三条，分成四组：

* **Scene 生命周期** —— ``create_scene`` / ``add_scene_version`` / ``control_scene``。
  问的是「这个市场按什么规则经营、哪一版在生效、改一版之后什么要重做」。
* **池** —— ``import_motherpool`` / ``promote_receipt`` / ``list_assets`` /
  ``list_accounts``。问的是「这一份上游交进来的导出读成了什么」「这个市场已经
  有哪些场地」「这个市场的账号池现在是什么样」。
* **冻结成员名单**（§B12）—— ``freeze_cohort`` / ``list_cohorts`` /
  ``get_cohort``。问的是「这一批工作按谁来做、按什么规则选的、谁没进来以及为什么」。
* **增量维护** —— ``get_maintenance_plan`` / ``run_maintenance`` / ``get_ops_report``。
  问的是「这一轮该复核哪些格子、复核之后看到了什么、这个市场现在经营得怎么样」。

四组住在一起，是因为它们问的是**同一个主语**（一个市场），而不是因为它们的
存储恰好都是 SQLite。

名单那三条**挨着**维护那三条，不是排版：维护那三条的主体从前只有 operator 手写的
``inputs/<name>.yaml`` 一个来源，现在也可以来自一份冻下来的名单（两条路都走
``_subjects`` 这一处派发）。分开摆的话，「这一批主体从哪来」这条判定会被复制成
两份，而分家的形态是「计划说读的是 A」与「真的读了 B」同时成立、两份报告都自洽。

**三组变四组时改的是数，不是结构**，所以这一段现在只说集合里有什么、不数基数 ——
下一个端口进来时不会再有一段话悄悄落后（与 ``product_loop_handlers`` 那段同一条）。

池那四条按**链**的顺序摆：一份工件读成回执（``import_motherpool``）→ 回执里
resolved 的那些行折进场地资产池（``promote_receipt``）→ 池子里现在有什么
（``list_assets``）→ 账号池与准入（``list_accounts``）。前三条是这一段链上
三个不同的动词，所以它们是三条工具而不是一条带 ``action`` 的工具：**读一份工件
不改变任何业务状态**（回执是内容寻址的，重放什么都不写），而**折一次池子是一次
读—改—写**。合成一条之后，「这份工件读得进来、但池子没折成」会变成一次整体失败，
读的人看不出是哪一半没成。

## 维护那三条为什么在这里，而不是另起一个模块

它们与 Scene 那三条共用一次「这个请求说的是哪个 Scene」的判定（``require_scene``
＋库里那份记录），而复核计划本身就是**某一版 Scene 的字段**（``version.refresh``）。
搬走的话，「暂停的场景不派发」这条判定会被复制成两份 —— 而它恰好是那份计划与那份
记录之间唯一的耦合点，复制的那一份会在某次改动后与这一份分家，分家的表现是
「计划显示会跑、tick 什么都没做」。

## 维护那条链上**没有**任何模型输入

复核谁、复核哪一面、什么时候复核，全部来自三样东西：某版 Scene 的 ``refresh``
计划、账号池的清单顺序、以及传进来的时刻。没有一条是从语言模型的输出里读的 ——
这也是 ``work`` 回调（``application/refresh.py``）不经过本模块的原因：它做的事情
由 aspect 决定，不由调用方描述。

## 为什么另起一个模块，而不是加进 ``tools.py``

``tools.py`` 的那一批问的是**一次研究**：受理一个作业、领走一件工作、读一份档案。
这里这一批问的是**一个市场**：它按什么规则经营、哪一版规则从哪一刻起生效、
改了这一版之后哪些已经做过的事要重做、它手上已经有哪几块场地、账号池是哪一份、
这一批工作按谁来做，以及这一轮该复核哪些格子、复核之后看到了什么。

池那四条**尤其容易**被摆错地方，所以单独说一句：``import_motherpool`` /
``promote_receipt`` / ``list_accounts`` 读的是 ``Settings.inputs_dir`` —— 而
``submit_research`` 也读那里。它们不因此属于 ``tools.py``：那几条的主语是
「这个市场的池子」，主语不是某一次作业，而且它们**不改任何作业的状态**。摆过去的
话，「池子是空的」与「这份作业读不到输入」会变成同一个模块里的两句相似的话，
而两者的处置一个是去导出一份清单、一个是去查作业记录。

分开的三个理由，每一个都能单独成立：

* **存储端口不重叠。** 那边是 ``JobStore`` / ``EvidenceStore`` / ``BudgetLedger``，
  这边是 ``SceneStore`` / ``MaintenanceStore`` / ``VenueAssetStore``。合成一张处理器
  表会让「这个部署有没有作业存储」与「这个部署有没有场景存储」看起来是同一件事，
  而它们是两件可以各自缺席的接线。
* **失败不同类。** 一次作业受理失败要改派发，一次立项失败要改**规则**。处置相反
  的两类失败写在同一个文件里，读的人迟早会按错的那一类去读。
* **角色不同。** 这三条只对控制侧开放，而控制侧那十条里已经有两条同时开放给
  研究侧（``get_evidence`` / ``get_decision``）。一张表里混着两种可见性，
  「这一条谁能看见」就只能在每一行上单独回答。

## 业务一律在 ``application/``，这里只做协议映射

本模块没有一行状态机、没有一次 ``store.put_*``。三个处理器各自把参数翻译成
一次 ``application.scenes`` 的调用，再把结果翻译成一条可读回执。理由是那条判据
（``tests/test_adapters_hold_the_vendors.py`` 的邻居们）：**同一条业务规则必须只有
一个执行器**。一旦这里也写一遍「暂停必须写理由」，两个执行器就会在某个下午分家，
而分家的表现是「CLI 拒绝、MCP 放行」——两次调用都看起来正常。

## 两样东西**不是**参数，而且这是刻意的

* **审批人（``by``）。** 激活一个 Scene 就是批准它，而批准人取自传输层已确立的
  身份（``guard.current_principal``），不取自任何载荷字段。一个能由调用方填写的
  批准人字段，等于一份可以自己签名、自己盖章的审批记录 —— 而记录上完全看不出来。
  这与 §6 那句「模型写「我批准」不产生授权」是同一条判据，只是这里的主语是
  Scene 而不是发送。
* **``calibrated``。** 复核节奏可以由操作者给天数（那是业务判断），但**没有**
  任何入参能把一条规则标成「已标定」。标定的含义是「这个数字来自一份已批准的
  策略」，而「调用方在请求里说是」不是那份策略。所以写进去的每条规则都是
  ``calibrated=False``，并且每一份回执都把 ``uncalibrated_aspects`` 报出来。
  判据是 ``test_no_product_loop_parameter_can_certify_a_refresh_rule``。
* **这一批母池怎么来的（``source_method_id``），与它按哪一份排除声明读
  （``declared_exclusion_ids``）。** 两个都不是 ``import_motherpool`` 的入参：
  前者是工件的事实，后者是 ``require_bundle(settings)`` 那一份 profile 的声明。
  一个能填排除清单的参数，等于让一次调用**发明**一条会静默删行的规则 ——
  而删掉的那几行在报告上只剩一个 ``excluded`` 计数。理由的全文在
  ``_import_motherpool`` 的 docstring 里。

## 时间不是参数

``at`` 由服务端取时钟（``domain.util.utc_now``），不接受调用方给一个时刻。
一个可以填写时刻的接口能让调用方把一次激活**倒填**到过去，而审批记录里那一栏
正是「哪一刻批的」—— 它一旦可写，就不再是一条证据。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

from ...application.accounts import (
    AccountDisposition,
    AccountOutcome,
    remember_accounts,
)
from ...application import cohorts as cohort_use_cases
from ...application import refresh as refresh_use_cases
from ...application import scenes as scene_use_cases
from ...application.acquisition import DocumentAcquisition
from ...application.budget import RunBudget
from ...application.caps import apply_cap
from ...application.document_router import DocumentRouter
from ...application.grading import grade_account
from ...application.cohorts import (
    COHORT_REF_PREFIX,
    current_cohort,
    freeze_from_pool,
)
from ...application.inputs import (
    SUBJECT_KIND_ACCOUNT,
    account_subjects,
    accounts_path,
    cohort_subjects,
    known_account_dossiers,
    subjects_for,
)
from ...application.maintenance import due_work, run_once, split_by_aspect
from ...application.motherpool import (
    load_receipt,
    read_motherpool,
    receipt_path,
    write_receipt,
)
from ...application.promotion import PromotionDisposition, PromotionOutcome, promote_receipt
from ...application.relations import (
    RelationDisposition,
    RelationOutcome,
    remember_relations,
)
from ...application.review import block_activation, pending_proposal
from ...application.scene_receipt import instant as _instant
from ...application.scene_receipt import version_receipt as _version_receipt
from ...config import Settings
from ...domain.account import MarketAccount
from ...domain.cohort import (
    WORKABLE_STATUSES,
    AccountCohort,
    CohortExclusion,
    CohortExclusionCode,
    CohortMember,
    CohortSelection,
)
from ...domain.entities import AccountVenueRelation, VenueAsset
from ...domain.enums import (
    AccountStatus,
    BudgetLine,
    DomainVerdict,
    JobInputKind,
    VenueRelation,
)
from ...domain.errors import (
    AssetConflictError,
    ConfigError,
    RunFoldLedgerError,
    RunStateError,
    SceneConflictError,
    StoreError,
)
from ...domain.outreach import (
    OutreachPolicyDecl,
    OutreachStage,
    StageTouches,
    StageWait,
)
from ...domain.receipt import SourceReceipt
from ...domain.subject import identity_refusal
from ...domain.maintenance import (
    ALL_ASPECTS,
    REQUIRED_ASPECTS,
    AspectState,
    AspectStatus,
    DueWork,
    MaintenanceClaim,
    RecheckState,
    RefreshPlan,
    RefreshRule,
    aspect_states,
)
from ...domain.scene import (
    PauseReason,
    SceneBudgetLine,
    SceneImpact,
    SceneRecord,
    SceneStateError,
    SceneVersion,
)
from ...domain.util import check_canonical_id, utc_now
from ...ports.pool import (
    AccountCohortStore,
    AccountPoolStore,
    AccountVenueRelationStore,
    VenueAssetStore,
)
from ...ports.review import ReviewCaseStore
from ...ports.scenes import MaintenanceStore, SceneStore
from ..page_mirror import page_copier
from .guard import current_principal
from ._shared import readable as _readable
from ._shared import store_failure as _store_failure
from .tools import (
    _SCENE_RECORD_ABSENT,
    _SCENE_RECORD_NO_STORE,
    _SCENE_RECORD_NOT_WIRED,
    _SCENE_RECORD_PRESENT,
    Handler,
    read_store,
    require_bundle,
    require_scene,
    write_store,
)

__all__ = ["product_loop_handlers"]

#: 三个动作各自要不要 ``version``、要不要 ``reason``。做成一张表而不是散在分支里：
#: 一张表能被遍历着测，而「哪一个动作带哪个参数」恰恰是本工具最容易写错的地方
#: （把 ``version`` 挂在 ``pause`` 上不会报错，只会静默地什么都不做）。
_SCENE_ACTIONS: Mapping[str, tuple[bool, bool]] = {
    "activate": (True, False),
    "pause": (False, True),
    "resume": (False, False),
    "archive": (False, False),
}

#: ``activate`` 没给版本时的拒绝。**一句话只有一个定义处** —— 它同时也是那条
#: 「最新那一版不是可以推断的默认值」的论证所在，抄第二遍就会先漂第二遍。
_ACTIVATE_NEEDS_VERSION = (
    "activate 必须写明要让**第几版**生效。\n"
    "  「最新那一版」不是一个可以推断的默认：最新版可能是刚写下的、还没看过的那一版，"
    "而让一个没人读过的版本静默生效正好是这条判据要防的事。"
)

_IMPACT_SCOPES = ("refetch", "reassess", "reusable")

#: 母池导入所用来源方法。**闭集里的哪一个由工具决定，不由调用方给** ——
#: 理由写在 :func:`_import_motherpool` 的 docstring 里，那里也是它唯一的读点。
_MOTHERPOOL_METHOD_ID = "import.register.v1"

#: ``list_accounts`` 缺省最多列多少个账号。**是缺省，不是硬上限** ——
#: 调用方显式传 ``limit=None`` 时不封顶（``apply_cap`` 的 ``None`` 只有这一个意思）。
#: 无论哪一边，``summary`` 与 ``account_count`` 都覆盖整份清单，被截掉的部分
#: 由 ``truncation`` 报出来。
#:
#: 50 **不是** ``tools.MAX_ITEMS = 20`` 的松版，两个数各自钉在自己的量级上：
#: ``MAX_ITEMS`` 数的是一条**证据**（URL、摘要、定位符，一条几百字节），
#: 这里数的是一行**账号摘要**（六个短字段 + 一格评分）。共用一个看起来整齐的
#: 常数，只会让其中一个在将来被改错方向。
_MAX_ACCOUNTS = 50

#: ``list_assets`` 缺省最多列多少行场地。**是缺省，不是硬上限** —— 与
#: ``_MAX_ACCOUNTS`` 逐字同一条（``limit=None`` 不封顶，截断由 ``truncation``
#: 报出来，``summary`` 覆盖整个池子）。
#:
#: 100 这个数**不是** ``_MAX_ACCOUNTS`` 的松版，两个数各自钉在自己的量级上：
#: 一行账号摘要数的是一个**已知客户名单**（一个部署几十行），而一行场地摘要数的
#: 是一个**郡的场地**（同一个郡里同一种业态的场地通常上百）。共用一个看起来整齐的
#: 常数，只会让其中一个在将来被改错方向。一行资产摘要比一行账号摘要宽（四格域名
#: 加两个时刻），所以两者也不是纯粹按行数比。
_MAX_ASSETS = 100

#: ``list_responsibilities`` 缺省最多列几条责任关系。**是缺省，不是硬上限** —— 与
#: ``_MAX_ASSETS`` 逐字同一条（``limit=None`` 不封顶，截断由 ``truncation`` 报出来，
#: ``summary`` 覆盖整张表）。
#:
#: 200 比 ``_MAX_ASSETS`` 松，理由有两条，而两条都不是「约等于它的两倍」：一行关系
#: 比一行场地窄（四段键 + 证据条数 + 两个时刻，没有那四格域名），而关系**天然比场地
#: 多** —— 同一块场地可以同时有 owner / operator / maintainer / …（§5 逐字要求这几种
#: 并存，不强制单选），所以按「一块场地最多 5 条」去定这个数，会让一个 40 块场地的
#: 市场在缺省下被砍掉一半。
#:
#: 被砍掉的是**「谁对什么负责」这一类行**，读的人会以为某些场地没有人负责 ——
#: 所以它与别处的封顶一样，两条纪律一条都不能省：``truncation`` 报出砍了多少，
#: ``summary`` 覆盖整张表（在截断之前算），要全部行的调用方显式传 ``None``。
_MAX_RELATIONS = 200

#: ``list_account_pool`` 缺省最多列几行账号。**是缺省，不是硬上限** —— 与
#: ``_MAX_RELATIONS`` 逐字同一条（``limit=None`` 不封顶，截断由 ``truncation`` 报出来，
#: ``summary`` 覆盖整个池子）。
#:
#: 200 与 ``_MAX_RELATIONS`` 数字相同，而这不是「它们是同一个量级」—— 是两条各自算出来
#: 的路撞在了一个数上：一行账号摘要比一行关系宽（三个清单格 + 两个时刻 + 一格判定），
#: 而一个 Scene 的账号池比它的关系表**小**（§7 的真实运行量级是单 Scene 20–50 个账号，
#: 几百条关系）。两个方向各自都要求它比 ``_MAX_ACCOUNTS`` 松 —— 那个 50 数的是
#: operator 维护的**清单文件**里有多少行，而池子是研究产出，同一个人工清单通常只覆盖
#: 一个市场的一小部分。
_MAX_POOL_ACCOUNTS = 200

#: ``import_motherpool`` 最多**预览**多少个主体 —— 只影响回执的长度，
#: 不影响任何判定。完整的一串主体由 ``submit_research`` 自己解析。
_MAX_SUBJECT_PREVIEW = 5

#: ``promote_receipt`` 最多列几行**逐行去向**。它是一个**硬上限**（这条工具没有
#: ``limit`` 入参），而它截的是**渲染**，不是写入 —— 一行都不会因为超出这个数而
#: 不被折进池子。被砍掉的那几条由 ``truncation`` 报出来。
#:
#: 数比 ``_MAX_SUBJECT_PREVIEW`` 松得多，因为这两处截的是不同的东西：那里截的是
#: 「这一批里前五个是谁」（一眼看个样子），这里截的是 §1 的**对账表** —— 逐行的
#: disposition 正是「research 的主语与账号池是同一个空间」这件事唯一的证据，而
#: §14 那一次真实验收的候选量级是 20–50。50 会让一份 50 行的回执恰好在边界上被砍，
#: 而那种「刚好差一条」的截断最容易被读成「本来就这么多」。
_MAX_PROMOTION_ROWS = 100

#: ``get_maintenance_plan`` 缺省最多列几格到期工作。**是缺省，不是硬上限** ——
#: 与 ``_MAX_ACCOUNTS`` 同一条：``None`` 只有一个意思（不封顶），而一次调用
#: 返回一个由池子大小决定的响应不是好的缺省。截断由 ``truncation`` 报出来。
_MAX_DUE = 200

#: ``get_report`` 的 ``freshness.stale`` 最多列几格。**是上限，不是缺省** —— 与
#: ``_MAX_DUE`` 不同：那一格是调用方可以调的量（``limit``），这一格是报告的一节，
#: 报告没有那个入参。取 50 是因为它要给的是**一份能看的名单**（第一批要处理的旧格子），
#: 而不是一份全量导出；少了多少由 ``stale_truncated`` 逐数报出，不静默砍。
_MAX_STALE_ROWS = 50

#: ``run_maintenance`` 每轮每场景缺省最多**做**几格。
#:
#: 它与 ``_MAX_DUE`` 不是同一个数，也不该是：列一列是免费的，做一格要写三样东西
#: （回执、盘点、可能还有观测）。同一条纪律：封顶的缺省被报出来（``truncation``），
#: 要一次做完的调用方显式传 ``None``。
_MAX_TICK = 100

#: 冻结名单那三条工具里，``members`` 与 ``exclusions`` **两栏各自**缺省最多列几行。
#: **是缺省，不是硬上限** —— 与 ``_MAX_POOL_ACCOUNTS`` 逐字同一条（``None`` 只有一个
#: 意思：不封顶；截断由 ``truncation`` 逐栏报出）。
#:
#: 与 ``_MAX_POOL_ACCOUNTS`` **同数不是巧合，是同一个量**：一份名单的成员最多等于池子
#: 里的行数（选出来的那些人本来就都在池子里）。两个数一旦不同，一个 210 行的池子会
#: 在「池子」那一栏显示 200 行、在「名单」这一栏冻出 210 个成员 —— 而两份报告读起来
#: 像两个池子，读的人没有任何一栏能看出它们说的是同一个。
_MAX_COHORT_ROWS = 200


# ``_instant`` 与 ``_version_receipt`` 在本次搬家之后住在
# ``application/scene_receipt.py``（见那个模块的 docstring）：第二个出口出现了
# （启动预览也要报「这一版声明里固定了什么」），而两处各写一遍会在某次改动之后分家。
# 这个模块里三十来处调用点**一个都没改** —— 拿到的仍是同一个名字。


def _build_version(payload: dict[str, Any]) -> SceneVersion:
    """构造一版。**校验器在 domain，不在这里。**"""
    try:
        return SceneVersion(**payload)
    except ValidationError as exc:
        raise ToolError(
            "这一版 Scene 构造不出来：\n"
            + _readable(exc)
            + "\n  （逐字段的合法性由 domain.scene.SceneVersion 判，这里只把它的"
            "拒绝翻成一条可读的回执）"
        ) from exc


def _budget_lines(declared: Mapping[str, int] | None) -> tuple[SceneBudgetLine, ...]:
    """``{"search": 120, ...}`` → 预算科目。

    ``None``（没给）与 ``{"search": 0}``（给了零）**不合并**：前者让
    ``application.scenes.validate`` 报「没有声明任何预算科目」，后者报「科目
    search 的上限是 0 —— 零额度是一次都跑不了」。两条消息的处置不同，所以它们
    必须由两条不同的输入走得出来。
    """
    if declared is None:
        return ()
    known = sorted(line.value for line in BudgetLine)
    lines: list[SceneBudgetLine] = []
    for name, ceiling in declared.items():
        try:
            line = BudgetLine(name)
        except ValueError as exc:
            raise ToolError(
                f"不认识的预算科目 {name!r}。本部署的科目是 {known}。\n"
                "  科目是一个闭集：写错一个名字的结果必须是**这一次调用被拒**，"
                "而不是那条额度被静默丢掉 —— 丢掉的额度在报告上读起来像"
                "「这个科目没花钱」。"
            ) from exc
        lines.append(SceneBudgetLine(line=line, ceiling=ceiling))
    return tuple(sorted(lines, key=lambda item: item.line.value))


def _refresh_plan(declared: Mapping[str, int] | None) -> RefreshPlan:
    """``{"contact_person_role": 30, ...}`` → 复核计划。**每条都是未标定的。**

    没给的 aspect 落 ``RefreshPlan.uncalibrated_default()`` 的那一条。给了的
    只改天数 —— 没有入参能改 ``calibrated``，理由见模块 docstring。
    """
    default = RefreshPlan.uncalibrated_default()
    if not declared:
        return default
    known = [aspect.value for aspect in ALL_ASPECTS]
    unknown = sorted(name for name in declared if name not in set(known))
    if unknown:
        raise ToolError(
            f"不认识的复核面 {unknown}。本部署的面是 {known}。\n"
            "  少报一个面与「这个面永不过期」在调度器眼里长得一样（都选不出工作），"
            "而两者的处置完全不同：一个是遗漏，一个是决定。所以不认识的键必须当场拒绝。"
        )
    return RefreshPlan(
        rules=tuple(
            RefreshRule(aspect=aspect, after_days=declared[aspect.value])
            if aspect.value in declared
            else default.rule_for(aspect)
            for aspect in ALL_ASPECTS
        )
    )


_OUTREACH_LIMIT_KEYS = ("max_per_wave", "max_per_recipient", "max_per_account")


def _outreach_policy(declared: Mapping[str, Any] | None) -> OutreachPolicyDecl:
    """一份声明 → 触达策略。**每一项都是未标定的**，且没有入参能改这件事。

    形状（缺省时整份落 ``uncalibrated_default``）::

        {"max_per_wave": 20, "max_per_recipient": 4, "max_per_account": 1,
         "waits": {"stage-2": 48, "stage-3": 96, "stage-4": 144},
         "prior_touches": {"stage-2": 1, "stage-3": 2, "stage-4": 3}}

    单位是**小时**。与 ``_refresh_plan`` 同一条处置：不认识的键当场拒绝，因为
    「配了一个拼错的键」与「那一项没配」在行为上长得一样 —— 而后者会落回缺省
    值，于是拼错只表现为「我设的值没生效」，没有任何一处会说出来。

    **没有任何入参能把 ``calibrated`` 置真。** 标定的含义是「这个数字来自一份
    已批准的策略」，而调用方在请求里说「这是批准过的」不是那份策略。判据是
    ``test_no_product_loop_parameter_can_certify_a_refresh_rule`` 的邻居。
    """
    default = OutreachPolicyDecl.uncalibrated_default()
    if not declared:
        return default

    unknown = sorted(
        set(declared)
        - set(_OUTREACH_LIMIT_KEYS)
        - {"waits", "prior_touches"}
    )
    if unknown:
        raise ToolError(
            f"不认识的触达规则键 {unknown}。"
            f"本部署认识的键是 {sorted(_OUTREACH_LIMIT_KEYS + ('waits', 'prior_touches'))}。\n"
            "  一个拼错的键会静默落回缺省值，于是症状只是「我设的值没生效」——"
            "而缺省是**未标定**的，两者在报告上都叫「未标定」。"
        )

    def _stage_map(key: str, known: frozenset[OutreachStage]) -> dict[OutreachStage, int]:
        """``{"stage-2": 48, ...}`` → ``{STAGE_2: 48, ...}``，**多余的一个都不收**。

        ``known`` 那一关是承重的，不是重复校验：没有它，``stage-1``（一个**合法**
        的枚举成员、只是这两张表里没有它的位置）会被读出来后悄悄丢掉 —— 于是
        调用方以为自己配了什么，而盘上没有任何一处说过它没生效。这恰恰是上面
        那段 docstring 说的那种沉默。
        """
        raw = declared.get(key)
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            raise ToolError(f"{key} 必须是一个对象（段 → 数字），给的是 {type(raw).__name__}。")
        resolved: dict[OutreachStage, int] = {}
        for name, value in raw.items():
            try:
                stage = OutreachStage(name)
            except ValueError:
                stage = None
            if stage is None or stage not in known:
                raise ToolError(
                    f"{key} 里的段名 {name!r} 不认识。这两张表的段是 "
                    f"{sorted(s.value for s in known)}"
                    " —— 第一段没有上一段，所以它不在这两张表里。"
                )
            if not isinstance(value, int) or isinstance(value, bool):
                raise ToolError(f"{key}[{name!r}] 必须是一个整数，给的是 {value!r}。")
            resolved[stage] = value
        return resolved

    waits = _stage_map("waits", frozenset(row.stage for row in default.waits))
    priors = _stage_map(
        "prior_touches", frozenset(row.stage for row in default.prior_touches)
    )
    limits: dict[str, int] = {}
    for name in _OUTREACH_LIMIT_KEYS:
        value = declared.get(name)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolError(f"{name} 必须是一个整数，给的是 {value!r}。")
        limits[name] = value

    return OutreachPolicyDecl(
        max_per_wave=limits.get("max_per_wave", default.max_per_wave),
        max_per_recipient=limits.get("max_per_recipient", default.max_per_recipient),
        max_per_account=limits.get("max_per_account", default.max_per_account),
        waits=tuple(
            StageWait(stage=row.stage, after_hours=waits.get(row.stage, row.after_hours))
            for row in default.waits
        ),
        prior_touches=tuple(
            StageTouches(
                stage=row.stage, allowed_prior=priors.get(row.stage, row.allowed_prior)
            )
            for row in default.prior_touches
        ),
    )


def _effective_exclusions(
    profile_exclusions: Sequence[str], additional: Sequence[str] | None
) -> tuple[str, ...]:
    """profile 的排除项 ∪ 本次追加的排除项，**排序后**返回。

    ## 为什么只能并，不能减

    §4 逐字要求「未获批的新场景只能 draft/preview，不得用产品扩展绕开已批准
    profile 的排除清单」。所以这个参数叫 ``additional_exclusions`` 而不是
    ``exclusions``：**没有**任何一个入参能表达「去掉 profile 的那一条」。
    这半句由 ``application.scenes.validate`` 的 ``exclusions_cover`` 兜底，
    也由参数名本身在 schema 上说清楚。

    ## 为什么排序

    这是一个**并集**，不是一段调用方给的顺序。两个来源谁先谁后会进
    ``frozen_sha256``（它是授权门绑的那个值），于是同一组排除词按不同顺序写出来
    会得到两个不同的版本摘要 —— 而它们描述的是同一个范围。调用方自己给的
    ``region`` / ``source_plan`` 保持原序（那里顺序是调用方写的），只有这一处
    并集排序。
    """
    merged = set(profile_exclusions)
    merged.update(additional or ())
    return tuple(sorted(merged))


def _version_payload(
    *,
    scene_id: str,
    workspace: str,
    version: int,
    region: Sequence[str],
    scenario: str,
    product: str,
    admissions: Sequence[str],
    exclusions: Sequence[str],
    role_policy: str,
    source_plan: Sequence[str],
    budget: Sequence[SceneBudgetLine],
    refresh: RefreshPlan,
    outreach: OutreachPolicyDecl,
    profile_id: str,
    profile_version: str,
    created_at: datetime,
    note: str,
    supersedes: int | None,
) -> dict[str, Any]:
    """一次立项/改版的**全部**绑定，一个字段不少。

    ``profile_id`` / ``profile_version`` 由**装载出来的那一份 bundle** 填，
    不从参数取：一个能由调用方填 profile 的 Scene 是一份声称遵守 A 规则、实际
    按 B 规则执行的对象，而 ``validate`` 只有在两边**恰好被同时读到**时才拦得住它。
    不给这个入参，就不存在这个窗口。

    ``supersedes`` 也由这里按版本号推算，不给调用方填：它与 ``version`` 由同一条
    规则绑定（``SceneVersion._supersedes_is_the_previous_one``），两处各写一遍
    只会让人以为它们可以不一致。
    """
    return {
        "scene_id": scene_id,
        "workspace": workspace,
        "version": version,
        "region": tuple(region),
        "scenario": scenario,
        "product": product,
        "admissions": tuple(admissions),
        "exclusions": tuple(exclusions),
        "role_policy": role_policy,
        "source_plan": tuple(source_plan),
        "budget": tuple(budget),
        "refresh": refresh,
        "outreach": outreach,
        "profile_id": profile_id,
        "profile_version": profile_version,
        "created_at": created_at,
        "note": note,
        "supersedes": supersedes,
    }


def _record_receipt(record: SceneRecord) -> dict[str, Any]:
    return {
        "scene_id": record.scene_id,
        "workspace": record.workspace,
        "state": record.state.value,
        "latest_version": record.latest_version,
        "effective_version": record.effective_version,
        "pending_version": record.pending_version,
        "accepts_new_work": record.accepts_new_work,
        "pause_reason": record.pause_reason.value if record.pause_reason is not None else None,
        "revision": record.revision,
        "created_at": _instant(record.created_at),
        "updated_at": _instant(record.updated_at),
        # 审批记录**逐条**回，不是只回最后一条：激活是幂等于历史的事，
        # 「谁在什么时候批过哪一版」在只回最后一条时读不出来。
        "approvals": [
            {"version": item.version, "at": _instant(item.at), "by": item.by}
            for item in record.approvals
        ],
    }


def _impact_receipt(impact: SceneImpact) -> dict[str, Any]:
    """改版的影响面。三个键**分别**列出，不是一个「有没有影响」的布尔。

    读的人要回答的是「哪些已经做过的事要重做」，而答案的形状是一组**面**：
    ``reusable`` 是全部 :class:`ImpactScope` 减去前两类（由
    :func:`scene_impact` 保证），所以「这次改动没碰到的面」也读得出来。
    """
    return {
        "from_version": impact.from_version,
        "to_version": impact.to_version,
        **{name: [scope.value for scope in getattr(impact, name)] for name in _IMPACT_SCOPES},
    }


def _validation_receipt(report: scene_use_cases.SceneValidation) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "problems": list(report.problems),
        # **不是 problem。** §4 允许有效期来自「已批准的策略」或一个被显式标注的
        # 未校准缺省值。把它算成拒绝会让每一个尚未校准的场景都装不出来，而那会把
        # 「未校准」推成「不要写这个字段」—— 正是 §4 要防的那件事。它必须被报出来，
        # 只是不阻断。
        "uncalibrated_aspects": [aspect.value for aspect in report.uncalibrated_aspects],
    }


def _require_principal() -> str:
    """本次调用的主体名，没有则拒绝。

    **经传输层不可达**：``RoleGuard`` 用 ``authorize`` 先拒掉无主体的调用，
    处理器根本不会被叫到。留着它不是为了断言一个事实，而是为了绕过中间件的
    调用者（测试、直接调处理器的那一侧）拿到一句能读的拒绝，而不是一个
    ``AttributeError`` —— 与 ``tools._claim`` 里同款分支同一个理由。
    """
    principal = current_principal()
    if principal is None:
        raise ToolError(
            "这个动作需要主体：它会被写进 Scene 的审批记录，而主体取自传输层的 "
            "Bearer 令牌，不取自任何参数。"
        )
    return principal.name


# ----------------------------------------------------------------------
# create_scene
# ----------------------------------------------------------------------
def _create_scene(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    scene_id: str | None,
    region: Sequence[str],
    scenario: str,
    product: str,
    admissions: Sequence[str],
    additional_exclusions: Sequence[str] | None,
    role_policy: str,
    source_plan: Sequence[str],
    budget: Mapping[str, int] | None,
    refresh_days: Mapping[str, int] | None,
    outreach: Mapping[str, Any] | None,
    note: str,
    activate_now: bool,
) -> dict[str, Any]:
    """立项：写入第 1 版，并可选当场激活。

    ``activate_now`` 让「先建草案、再看一眼、再批」与「一次建好并启用」是同一个
    工具的两个取值，而不是两个工具 —— 后者会让「有没有激活过」这件事在两条
    代码路径上各写一遍。无论走哪一支，激活那一步**都要求主体**：
    一次没有批准人的激活会让 ``approvals`` 里凭空多出一条无法归属的记录。
    """
    chosen = require_scene(settings, scene_id)
    bundle = require_bundle(settings)
    store = scenes()
    at = utc_now()
    declared = bundle.profile
    profile_exclusions = tuple(declared.exclusion_ids if declared is not None else ())

    version = _build_version(
        _version_payload(
            scene_id=chosen,
            # 工作区**就是**本部署的 Scene（``settings.profile``）——与
            # ``require_scene`` 用的是同一个来源。两处各推一次会在将来某一天分开，
            # 而分开的形态是「这条令牌被判定属于这里，对象却说不属于」。
            workspace=settings.profile,
            version=1,
            region=region,
            scenario=scenario,
            product=product,
            admissions=admissions,
            exclusions=_effective_exclusions(profile_exclusions, additional_exclusions),
            role_policy=role_policy,
            source_plan=source_plan,
            budget=_budget_lines(budget),
            refresh=_refresh_plan(refresh_days),
            outreach=_outreach_policy(outreach),
            profile_id=bundle.profile_id,
            profile_version=bundle.scene_version,
            created_at=at,
            note=note,
            supersedes=None,
        )
    )

    try:
        record = scene_use_cases.create_draft(store=store, version=version, bundle=bundle)
    except ConfigError as exc:
        # 装不出来（排除清单没覆盖 profile 的那几条 / 没声明预算 / 零额度）
        # 是一条**结果**，不是一次崩溃：调用方要据它改参数。
        raise ToolError(str(exc)) from exc
    except SceneConflictError as exc:
        existing = store.get_record(chosen)
        raise ToolError(
            f"{chosen!r} 已经立项过了"
            + (
                f"（当前状态 {existing.state.value}，最新第 {existing.latest_version} 版，"
                f"生效第 {existing.effective_version} 版）。\n"
                if existing is not None
                else "。\n"
            )
            + "  改规则请用 add_scene_version（它登记新的一版，**不改**已经生效的那一版）；"
            "重建立项会让一个已经被报告引用过的场景的多条历史无处归属。"
        ) from exc
    except StoreError as exc:
        # 库里已经有这个 scene 的第 1 版，而它的摘要与这次要写的不同 ——
        # ``create_draft`` 先 ``put_version`` 再 ``put_record``，所以这条路
        # **一个字节都没落盘**，上面那条记录仍是第一次立项留下的那一份。
        raise _store_failure(exc) from exc

    if activate_now:
        record = scene_use_cases.activate(
            store=store,
            scene_id=chosen,
            version=1,
            at=at,
            by=_require_principal(),
        )

    report = scene_use_cases.validate(version, bundle)
    return {
        "scene": _record_receipt(record),
        "version": _version_receipt(version),
        "validation": _validation_receipt(report),
        "note": (
            "第 1 版是**写下来**的，不是「默认的那一版」：它的内容进 frozen_sha256，"
            "而授权门与 wave manifest 绑的是那个摘要。\n\n"
            "exclusions 是 profile 已批准的那几条 ∪ 本次追加的 —— 只能并，不能减"
            "（参数名是 additional_exclusions，没有一个入参能表达「去掉某一条」）。\n\n"
            "refresh 里每一条都是 calibrated=false：天数可以由你给，但「已标定」"
            "的含义是「这个数字来自一份已批准的策略」，而一次调用不是那份策略。"
            "未标定的面在每一份报告里都会被报出来。"
        ),
    }


# ----------------------------------------------------------------------
# add_scene_version
# ----------------------------------------------------------------------
def _add_scene_version(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    scene_id: str | None,
    region: Sequence[str] | None,
    scenario: str | None,
    product: str | None,
    admissions: Sequence[str] | None,
    additional_exclusions: Sequence[str] | None,
    role_policy: str | None,
    source_plan: Sequence[str] | None,
    budget: Mapping[str, int] | None,
    refresh_days: Mapping[str, int] | None,
    outreach: Mapping[str, Any] | None,
    note: str,
) -> dict[str, Any]:
    """改版：登记新的一版（**不生效**），并报出它相对上一版的影响面。

    ## 没给的字段从上一版继承

    逐字段改而不是整份重写，是因为「我只想改预算」与「我把整份规则重写一遍」
    在回执上长得一样，而后者会让一个漏写的字段静默退回缺省。继承是显式的：
    回执里给的是**改动后**的完整一版，读的人不必去比对两版才知道现在是什么规则。

    ## 为什么不在这里顺手激活

    ``application.scenes.add_version`` 明确不生效。一次「改完即生效」会让在途的
    运行在一半的时候换掉规则，而报告上只是「版本号变大了」——这正是
    ``SceneRecord`` 把 ``latest_version`` 与 ``effective_version`` 分成两个字段
    要防的那件事。要生效请再调 ``control_scene(activate, version=...)``，
    那一步会记下批准人。
    """
    chosen = require_scene(settings, scene_id)
    bundle = require_bundle(settings)
    store = scenes()
    at = utc_now()

    current = store.get_record(chosen)
    if current is None:
        raise ToolError(
            f"{chosen!r} 还没有立项过。\n"
            "  改版是相对**上一版**的一次改动，而上一版不存在时没有可继承的东西 ——"
            "请先用 create_scene 写下第 1 版。"
        )
    previous = store.get_version(chosen, current.latest_version)
    if previous is None:
        raise ToolError(
            f"{chosen!r} 的记录说最新是第 {current.latest_version} 版，而库里读不到那一版。\n"
            "  记录与版本必须能互相指称 —— 读不到只可能意味着有人删过版本，"
            "而版本是 append-only 的。"
        )

    declared = bundle.profile
    profile_exclusions = tuple(declared.exclusion_ids if declared is not None else ())

    def keep(value: Any, fallback: Any) -> Any:
        """``None`` = 「这次不改这一项」。**空列表不是 None** —— 空列表会被
        ``SceneVersion`` 的校验器拒掉（``region`` / ``source_plan`` 不得为空），
        于是「想清空」与「忘了填」不会被悄悄合并成同一件事。"""
        return fallback if value is None else value

    version = _build_version(
        _version_payload(
            scene_id=chosen,
            workspace=settings.profile,
            version=current.latest_version + 1,
            region=keep(region, previous.region),
            scenario=keep(scenario, previous.scenario),
            product=keep(product, previous.product),
            admissions=keep(admissions, previous.admissions),
            exclusions=_effective_exclusions(
                profile_exclusions,
                keep(additional_exclusions, tuple(previous.exclusions)),
            ),
            role_policy=keep(role_policy, previous.role_policy),
            source_plan=keep(source_plan, previous.source_plan),
            budget=_budget_lines(budget)
            if budget is not None
            else previous.budget,
            refresh=_refresh_plan(refresh_days) if refresh_days is not None else previous.refresh,
            outreach=(
                _outreach_policy(outreach) if outreach is not None else previous.outreach
            ),
            profile_id=bundle.profile_id,
            profile_version=bundle.scene_version,
            created_at=at,
            note=note,
            supersedes=previous.version,
        )
    )

    try:
        record, impact = scene_use_cases.add_version(store=store, version=version, bundle=bundle)
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc
    except SceneConflictError as exc:
        raise ToolError(
            f"{chosen!r} 在这一次读与写之间被改过（有人先写了一版）。\n"
            "  这一次写**一个字节都没落盘**。请重读当前状态、重新决定 ——"
            "自动重试等于替人决定「还要不要改」。"
        ) from exc
    except StoreError as exc:
        # 这一版号上已经躺着另一份不同摘要的版本（例如有人手工指定过版本号）。
        raise _store_failure(exc) from exc

    report = scene_use_cases.validate(version, bundle)
    return {
        "scene": _record_receipt(record),
        "version": _version_receipt(version),
        "impact": _impact_receipt(impact),
        "validation": _validation_receipt(report),
        "note": (
            "这一版**没有生效**：记录里的 latest_version 前进了，effective_version 没动。"
            "要让它生效请调 control_scene(action='activate', version=...) ——"
            "那一步会记下批准人，而这里不会。\n\n"
            "impact 是这一次写操作的**产物**，不是一句事后说明：它只在两版同时在手时"
            "才算得出来。reusable 是全部面减去 refetch 与 reassess，所以「这次改动没碰"
            "到的面」也读得出来。"
        ),
    }


# ----------------------------------------------------------------------
# control_scene
# ----------------------------------------------------------------------
#: ``activate`` 的闸门读数。与 ``tools._scene_gate`` 同形、同一条理由：接线事实
#: （``not_wired``）与判词（``clear``）是两句话，而**拦下**那一支不是一格读数 ——
#: 它是一次拒绝（``ToolError``），所以它不需要一个在这里永远读不到的取值。
_SCENE_ACTIVATION_NOT_WIRED = "not_wired"
_SCENE_ACTIVATION_CLEAR = "clear"


def _activation_gate(
    *, review: Callable[[], ReviewCaseStore] | None, scene_id: str, version: int
) -> dict[str, Any]:
    """这一版等不等一件待办的批准（§9 的「提议 → 批准 → 激活」）。等就**拒绝**。

    ## 它拦的是哪一步

    ``add_scene_version`` 写下第 N 版，``resolve_review_case(propose_policy_change,
    proposed_version=N)`` 让那件待办进 ``AWAITING_APPROVAL`` —— 而**让那一版生效**
    正是 §9 要拦住的那一步：批准者还没批，规则就已经在跑了。这里拦的就是它。

    ## 只拦**由待办提议出来**的那一版

    一个普通的、由 ``add_scene_version`` 直接写下再 ``activate`` 的版本**照旧**生效
    （既有行为逐字不变）。理由写在 ``docs/R1_R2_IMPLEMENTATION_LEDGER.md`` 的取证
    结论二里：要第二个人批准的是**一条规则的变更**，不是「把一个 Scene 立项并激活」。
    把闸门做成「activate 一律要批准」会把后者也拦下 —— 而那是一条**没有**依据的
    新规则，它会让每一个还没有收件箱的部署连第 1 版都激活不了。

    ## ``not_wired`` 与 ``_scene_gate`` 选的是同一条

    没有收件箱存储时这一问答不出来。答不出来有两种写法，两边选的是同一个：当作不拦，
    **但把它写进回执**（``decision`` 那一格如实报 ``not_wired``）——「没判」与
    「判成不拦」在报告上必须是两句话，否则读的人会以为这一版被批准过。
    """
    if review is None:
        return {
            "decision": _SCENE_ACTIVATION_NOT_WIRED,
            "case_id": "",
            "note": (
                "这个服务实例没有接收件箱，所以「这一版有没有一件待办在等批准」判不了。"
                "激活照旧 —— 这与「判成不拦」不是同一句话：§9 那道批准在这一格上"
                "**没有被问过**。"
            ),
        }

    store = review()
    waiting = block_activation(store=store, scene_id=scene_id, version=version)
    if waiting is None:
        return {
            "decision": _SCENE_ACTIVATION_CLEAR,
            "case_id": "",
            "note": (
                "这一版没有待办在等批准，激活照常。闸门只拦**由待办提议出来**的那一版"
                "（§9 的提议 → 批准 → 激活），普通的一版不经过它。"
            ),
        }

    # 提议人是**报出来**的那一格，不是拦下的理由 —— 但它决定了「下一步该找谁」：
    # §9 要的是**另一个人**批准（``application/review.py`` 的 ``approve_policy_change``
    # 逐字：同一个人不能既提议又批准），而只报一句「在等批准」的拒绝会让调用方
    # 去问提议人自己。
    proposal = pending_proposal(store=store, case_id=waiting.case_id)
    proposer = f"，提议人 {proposal.by}" if proposal is not None else ""
    raise ToolError(
        f"第 {version} 版不能激活：有一件待办正等着它被批准。\n"
        f"  待办 {waiting.case_id}（{waiting.subject_ref}，{waiting.reason.value}{proposer}）。\n"
        f"  这一版是一条**规则变更**的提案，而 §9 要求它由一位**获授权者**批准 —— "
        "批准不是把同一个人的第二次点击算作同意。\n"
        f"  处置：让**另一个人**跑 approve_policy_change（case_id {waiting.case_id}、"
        f"proposed_version {version} 都在那件待办上），然后再 activate 这一版。\n"
        "  闸门只拦由待办提议出来的那一版：普通的 add_scene_version + activate "
        "照旧直接生效。"
    )


def _control_scene(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    review: Callable[[], ReviewCaseStore] | None,
    scene_id: str | None,
    action: str,
    version: int | None,
    reason: str,
) -> dict[str, Any]:
    """类型化的生命周期动作：activate / pause / resume / archive。

    ## 为什么是一个工具而不是四个

    与 §2 的 ``control_job`` 同一条：四个动词共用同一组前置判定（对象在不在、
    这次转移合不合法、谁在做），拆成四个工具就是四份前置判定，而它们会在某一次
    改动后分家 —— 分家的表现是「暂停能过、归档过不了同一道检查」。

    ## 多余的参数是**错误**，不是被忽略

    ``version`` 挂在 ``pause`` 上不会报错，它只会什么都不做 —— 而调用方以为
    自己指定了「暂停哪一版」。所以 ``_SCENE_ACTIONS`` 把每个动作收什么参数写成
    了一张表，多给一个就拒绝。

    ## archive 是终态

    ``SCENE_TRANSITIONS[ARCHIVED]`` 是空集。这里不在前面再加一道判断 ——
    那会让「没有出边」与「有一条边但被拒绝」变成两件读起来一样的事，
    而它们是两个不同的状态机设计。
    """
    chosen = require_scene(settings, scene_id)
    if action not in _SCENE_ACTIONS:
        raise ToolError(
            f"不认识的动作 {action!r}。本部署的动作是 {sorted(_SCENE_ACTIONS)}。\n"
            "  一个自由字符串会被实现静默当成某一条分支处理 —— 于是 "
            "action='Activate' 与 action='activate' 得到两种不同的结果，"
            "而调用方以为它们一样。"
        )
    wants_version, wants_reason = _SCENE_ACTIONS[action]
    if version is not None and not wants_version:
        raise ToolError(
            f"动作 {action!r} 不收 version：它不改变哪一版生效。\n"
            "  多给一个参数不会被忽略，它会被读成「你指定了某样东西」—— "
            "而这次调用里没有任何一处会用到它。"
        )
    if reason.strip() and not wants_reason:
        raise ToolError(f"动作 {action!r} 不收 reason。")
    if wants_reason and not reason.strip():
        raise ToolError(
            "pause 必须写明理由（" + "、".join(item.value for item in PauseReason) + "）。"
        )

    store = scenes()
    at = utc_now()
    # §9 那道闸门在**写之前**：`activate` 的闸门读数只有进 activate 那一支才存在，而
    # 判词要在写发生之前拿到 —— 拒绝时一个字节都没落盘（与 `_scene_gate` 在
    # `store.admit` 之前同一条：一次被闸门拦下的写入不该先留下半截事实）。
    activation_gate: dict[str, Any] | None = None
    try:
        if action == "activate":
            if version is None:
                raise ToolError(_ACTIVATE_NEEDS_VERSION)
            activation_gate = _activation_gate(
                review=review, scene_id=chosen, version=version
            )
            record = scene_use_cases.activate(
                store=store,
                scene_id=chosen,
                version=version,
                at=at,
                # 批准人取自传输层已确立的身份，**不取自参数**。
                by=_require_principal(),
            )
        elif action == "pause":
            try:
                pause_reason = PauseReason(reason.strip())
            except ValueError as exc:
                raise ToolError(
                    f"不认识的理由 {reason!r}。本部署的理由是 "
                    f"{[item.value for item in PauseReason]}。\n"
                    "  两个理由的处置不同（operator_pause 由操作者自己解除，"
                    "policy_hold 要等那条策略不再成立），所以它们不能合并成一个布尔。"
                ) from exc
            record = scene_use_cases.pause(
                store=store, scene_id=chosen, at=at, reason=pause_reason
            )
        elif action == "resume":
            record = scene_use_cases.resume(store=store, scene_id=chosen, at=at)
        else:
            record = scene_use_cases.archive(store=store, scene_id=chosen, at=at)
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc
    except SceneStateError as exc:
        raise ToolError(str(exc)) from exc
    except SceneConflictError as exc:
        raise ToolError(
            f"{chosen!r} 在这一次读与写之间被改过。这次写一个字节都没落盘。\n"
            "  请重读当前状态、重新决定。"
        ) from exc
    except StoreError as exc:
        # 生命周期动作也写存储（状态列与审批记录），所以同一条纪律在这里同样适用。
        raise _store_failure(exc) from exc

    effective = (
        store.get_version(chosen, record.effective_version)
        if record.effective_version is not None
        else None
    )
    receipt: dict[str, Any] = {
        "scene": _record_receipt(record),
        "effective": _version_receipt(effective) if effective is not None else None,
        "note": (
            "暂停与归档只停**新的**派发与外联；受理侧（回信/退信/退订/投诉）不看这个"
            "状态 —— 暂停一个场景不会让已经在路上的回信收不到。\n\n"
            "resume 不改 effective_version：§4 逐字要求恢复不得重发已发邮件或重吃"
            "整个母池，而那条的执行方式是「恢复不改任何游标」。"
        ),
    }
    if activation_gate is not None:
        # 只在 activate 的回执里出现。加到四个动作上都长出一格恒为 null 的读数，
        # 而「这一版没有待办在等批准」与「这个动作不问那件事」在报告里会变成同一格。
        receipt["activation_gate"] = activation_gate
    return receipt


# ----------------------------------------------------------------------
# list_scenes
# ----------------------------------------------------------------------
#: 一次 ``list_scenes`` 最多返回多少行。与 ``list_accounts`` 同一条理由：封顶的
#: 缺省**会被报出来**（``truncation``），而不封顶的缺省会让一次调用返回一个由库
#: 决定大小的响应。一个部署通常只有一行（一个部署服务一个 Scene），但这个库可以
#: 被换过 ``MARKETOPS_PROFILE`` 的同一批人写进多行 —— 那时「有几个场景、各自什么
#: 状态」是一件要读出来的事。
_MAX_SCENES = 50


def _scene_row(
    record: SceneRecord, effective: SceneVersion | None
) -> dict[str, Any]:
    """一行场景的生命周期读数。

    ``dispatch_refusal`` 与 ``accepts_new_work`` **两格一起给**：一格是判词，一格是
    原文。只给判词的话，读的人看得出「不能派发」却看不出「为什么、谁能解开」——
    而后者是 ``operator_pause``（自己 resume）与 ``policy_hold``（等策略失效）
    唯一的区别。
    """
    return {
        "scene_id": record.scene_id,
        "workspace": record.workspace,
        "state": record.state.value,
        "latest_version": record.latest_version,
        "effective_version": record.effective_version,
        "pending_version": record.pending_version,
        "accepts_new_work": record.accepts_new_work,
        "dispatch_refusal": record.dispatch_refusal,
        "pause_reason": (
            record.pause_reason.value if record.pause_reason is not None else None
        ),
        "revision": record.revision,
        "created_at": _instant(record.created_at),
        "updated_at": _instant(record.updated_at),
        "approvals": [
            {"version": item.version, "at": _instant(item.at), "by": item.by}
            for item in record.approvals
        ],
        # **生效那一版的字节摘要**，不是它的版本号。授权门与 wave manifest 绑的是
        # 这个摘要（``_version_receipt`` 的 frozen_sha256），所以「现在生效的是哪一套
        # 规则」只有它能回答；版本号回答的是「第几次编辑」。
        "effective": (
            None
            if effective is None
            else {
                "version": effective.version,
                "frozen_sha256": effective.frozen_sha256,
                "profile_id": effective.profile_id,
                "profile_version": effective.profile_version,
                "created_at": _instant(effective.created_at),
            }
        ),
    }


def _list_scenes(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore] | None,
    limit: int | None,
) -> dict[str, Any]:
    """这个部署的**全部立项**，以及各自现在是什么状态（§1 的「可随时读取」）。

    ## 为什么 ``get_scene`` 不够

    ``get_scene`` 答的是「我说的那一个场景现在是什么样」，而它要求调用方**先知道
    那个场景的名字**（``scene_id`` 缺省时取本部署的 ``settings.profile``）。于是
    「这个部署里有几个场景、分别是什么状态」在任何工具上都问不出来 ——
    ``application/scenes.py::list_scenes`` 早就写着，而它在 MCP 这一侧没有生产者。
    一个读不到清单的部署，运维只能去翻数据库 —— 而那件事本层是**明令不许**的
    （§18：不能有一个关键步骤要靠人手工改库）。

    ## 它答不了的那一半，指名道姓说出去

    这里只有**立项**（规则与生命周期）。「这个市场经营得怎么样」是另一份报告
    （``get_ops_report``：复核跑到哪儿了、来源供给什么样、哪些格子挂在 FAILED 上）。
    两个都读，才回答得了「这个市场现在能不能用」；本回执的 ``note`` 把这件事写出来，
    免得读的人把「场景是 active」当成「研究在跑」。

    ## 读一次**不建库**

    库文件不存在时这一格是 ``no_store`` 且 ``scenes`` 为空列表 —— 与
    ``get_scene`` 那一格用的是**同一组话**（见 ``tools._SCENE_RECORD_*``，这里直接
    从那边 import，不在这里另编一组）：两处各编一组的话，「还没写过任何东西」与
    「写了、但没有这个场景」会在两份报告里各说一套。
    """
    if scenes is None:
        return {
            "scenes": [],
            "scene_count": 0,
            "record_state": _SCENE_RECORD_NOT_WIRED,
            "summary": None,
            "truncation": apply_cap([], limit)[1].as_dict(),
            "note": (
                "这个服务实例没有接场景存储，所以「这个部署里有哪些立项」读不出来。"
                "这不是「一个都没有」—— 前者的处置是接线，后者是去立项。"
            ),
        }
    if not settings.database_path.is_file():
        return {
            "scenes": [],
            "scene_count": 0,
            "record_state": _SCENE_RECORD_NO_STORE,
            "summary": None,
            "truncation": apply_cap([], limit)[1].as_dict(),
            "note": (
                f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
                "所以这个部署还没有立项过。这是一个正常答案，不是读不出来。"
            ),
        }

    store = scenes()
    records = scene_use_cases.list_scenes(store, workspace=settings.profile)
    rows: list[dict[str, Any]] = []
    for record in records:
        effective = (
            None
            if record.effective_version is None
            else store.get_version(record.scene_id, record.effective_version)
        )
        rows.append(_scene_row(record, effective))

    by_state: dict[str, int] = {}
    for row in rows:
        by_state[row["state"]] = by_state.get(row["state"], 0) + 1
    summary = {
        "by_state": by_state,
        # 「有立项、但还没批准生效」——它是一个**待办**，不是一个状态名：
        # ``draft`` 要有人批准，``pending_version`` 非空要有人让新版生效。
        "awaiting_approval": sum(1 for row in rows if row["effective_version"] is None),
        "with_pending_version": sum(1 for row in rows if row["pending_version"]),
        "dispatchable": sum(1 for row in rows if row["accepts_new_work"]),
    }

    try:
        kept, decision = apply_cap(rows, limit, key=lambda row: row["scene_id"])
    except ConfigError as exc:
        # 与 ``list_accounts`` 同一条：负数上限是**调用方给错了**，所以它要走
        # 工具结果那条通道，而不是从处理器里抛成一次内部错误。
        raise ToolError(str(exc)) from exc

    return {
        "scenes": kept,
        "scene_count": len(rows),
        "record_state": _SCENE_RECORD_PRESENT if rows else _SCENE_RECORD_ABSENT,
        "workspace": settings.profile,
        "summary": summary,
        "truncation": decision.as_dict(),
        "note": (
            "这一格只有**立项**：规则、版本与生命周期。市场经营得怎么样（复核跑到"
            "哪儿了、来源供给什么样、哪些格子挂在 FAILED 上）是另一份报告 —— "
            "`get_ops_report`。两个都读，才回答得了「这个市场现在能不能用」："
            "一个 active 的场景不等于研究在跑。\n\n"
            "effective 是**生效那一版的字节摘要**，不是版本号：授权门与 wave "
            "manifest 绑的是 frozen_sha256，而版本号回答的是「第几次编辑」。\n\n"
            "accepts_new_work 与 dispatch_refusal 两格一起看：前者是判词，后者是"
            "原文（operator_pause 要人点 resume、policy_hold 要等策略失效 —— "
            "差别就在那句话里）。\n\n"
            f"workspace 是本部署的 Scene（{settings.profile}）：本工具只列属于它"
            "的立项。库里若有别的 workspace 的行，它们不属于这个部署，不在这里出现。"
        ),
    }


# ----------------------------------------------------------------------
# import_motherpool
# ----------------------------------------------------------------------
def _motherpool_receipt(receipt: SourceReceipt) -> dict[str, Any]:
    """一份回执的**全部**要说的话，一个字段不少。

    ``counts`` 是五个数而不是一个「有效行数」，``resolved_row_count`` 与
    ``distinct_resolved_count`` **并列**出现 —— 三条纪律各自的理由写在
    ``domain/receipt.py`` 里（``counts`` / ``distinct_resolved_count`` 的属性
    docstring），这里只负责不把它们合并。

    ``declared_exclusion_ids`` 也在里面，而且是**读这份回执要看的第一个东西**：
    整份拒绝那条路径（「命中了未声明的排除项」）的全部依据就是它，而它来自
    profile 而不是参数 —— 不报出来的话，读的人无从知道引擎当时手里是哪几条。
    """
    return {
        "receipt_id": receipt.receipt_id,
        "version": receipt.version,
        "source_method_id": receipt.source_method_id,
        "batch_id": receipt.batch_id,
        "artifact_ref": receipt.artifact_ref,
        "artifact_sha256": receipt.artifact_sha256,
        "created_at": _instant(receipt.created_at),
        "scope": receipt.scope,
        "cutoff": receipt.cutoff,
        "cursor": receipt.cursor,
        "profile_id": receipt.profile_id,
        "raw_row_count": receipt.raw_row_count,
        "counts": receipt.counts,
        "resolved_row_count": receipt.resolved_row_count,
        "distinct_resolved_count": receipt.distinct_resolved_count,
        "counts_line": receipt.counts_line,
        "blocker": receipt.blocker,
    }


def _import_motherpool(
    *,
    settings: Settings,
    artifact_ref: str,
    batch_id: str,
    scope: str,
    cutoff: str,
    cursor: str,
) -> dict[str, Any]:
    """把一份上游交进来的母池工件读成回执，落到 ``inputs_dir``，并报出下游会看到什么。

    ## 两样东西**不是**参数

    **``source_method_id``。** 这条工具做的事字面就是「读一份登记册导出」，而
    ``import.register.v1`` 的 ``answers`` 逐字描述的就是这件事 —— 以及它的边界
    （「它**不能**回答『这个池子覆盖了多少』，一份登记的完整边界由登记方定义」）。
    开一个入参只会让调用方能把登记册导出的行标成 ``search.engine.v1``：两者在
    「来源族多样性」上同族，而在「检索了几次」「覆盖了多少查询」这些读数上
    一个是真一个是假。**「这一批怎么来的」是工件的事实，不是请求的字段** ——
    与 ``create_scene`` 的 ``profile_id`` 不由调用方填是同一条判据。

    **``declared_exclusion_ids``。** 它只从 ``require_bundle(settings)`` 那一份
    profile 来，这是 ``read_motherpool`` 那条判据的另一半：命中了 profile **没
    声明过**的排除项就整份拒绝（``ConfigError``），不降级、不跳过。一个能由调用方
    填的排除清单，等于让一次调用**发明**一条会静默删行的规则，而删掉的那些行在
    报告上只剩一个 ``excluded`` 计数。

    ## 回执在写盘**之前**测「是不是已经有一份」

    ``write_receipt`` 对同一个 ``receipt_id`` 是幂等的（重放什么都不写），而它
    返回的路径在两种情况下逐字相同。所以「这一次真的写了吗」这个问题**只能**
    在写之前问：``.exists()`` 一次，把答案记下来。事后去比对 mtime 或者比对
    字节都是错的 —— 前者把一次重放读成一次写入，后者被 ``created_at`` 破坏
    （它刻意不进 id，所以重放的两份字节不同，见 ``write_receipt`` 的 docstring）。

    ## 主体是**从盘上那一份回执读回来**的

    不是拿手里这个对象算。报告要说的是「``submit_research`` 会看到什么」，而它
    看到的是盘上那一份 —— 中间隔了一次序列化，而序列化正是这类东西最容易出错
    的地方（一个字段被 ``model_dump`` 丢掉，回执仍然是自洽的，只是少了一格）。
    读回来还有一个附带的好处：这一步同时证明**落下去了的那一份是读得回来的**。
    """
    bundle = require_bundle(settings)
    declared = bundle.profile
    if declared is None:  # pragma: no cover —— 装载器保证非空，留它是为了失败关闭
        raise ToolError(
            f"profile {bundle.profile_id!r} 的 bundle 里没有 profile 声明块，母池读不了。\n"
            "排除项只能从那里来：拿一个空元组顶替，会让每一个带排除标记的行都变成"
            "「命中了一条没声明的规则」而整份被拒 —— 一次「这份 profile 没被读进来」"
            "会被渲染成一次数据事故。"
        )

    inputs_dir = settings.inputs_dir
    try:
        receipt = read_motherpool(
            inputs_dir,
            artifact_ref,
            source_method_id=_MOTHERPOOL_METHOD_ID,
            batch_id=batch_id,
            declared_exclusion_ids=declared.exclusion_ids,
            profile_id=bundle.profile_id,
            scope=scope,
            cutoff=cutoff,
            cursor=cursor,
        )
        expected = receipt_path(inputs_dir, receipt.receipt_id)
        preexisting = expected.exists()
        landed = write_receipt(inputs_dir, receipt)
        resolution = subjects_for(
            JobInputKind.SOURCE_RECEIPT, receipt.receipt_id, inputs_dir=inputs_dir
        )
    except ConfigError as exc:
        # 找不到工件 / 工件有歧义 / 整份读不出来 / 名字不是规范形式 /
        # 命中未声明的排除项 / 盘上同名文件不是一份自洽的回执 —— 六条都是
        # **调用方要据它改东西**的结局，所以走工具结果，不走异常链。
        raise ToolError(str(exc)) from exc

    preview = resolution.subjects[:_MAX_SUBJECT_PREVIEW]
    return {
        "receipt": _motherpool_receipt(receipt),
        "receipt_path": str(landed),
        "preexisting": preexisting,
        "research": {
            "ok": resolution.ok,
            "subject_count": len(resolution.subjects),
            "subjects": [
                {
                    "account_id": dossier.account_id,
                    "organisation": dossier.organisation,
                    "domain": dossier.domain,
                }
                for dossier in preview
            ],
            "blocker": resolution.blocker,
        },
        "note": (
            f"回执是**内容寻址**的：id 由「来源方法 + 批次 + 工件摘要 + 处置摘要」派生，"
            f"所以同一份工件在同一份 profile 下读两次，会落到同一个 "
            f"{receipt.receipt_id!r} 上 —— 第二次什么都不写（本次 "
            f"preexisting={preexisting}）。\n\n"
            f"排除项取自 profile 的声明（{list(declared.exclusion_ids)}），**不是**这条"
            "工具的参数：命中了这份清单之外的排除项，整份拒绝而不是跳过那一行 —— "
            "把输入当成规则来源，等于让一次导入发明一条会静默删行的规则。\n\n"
            "counts 五项之和等于 raw_row_count，其中 pending 不是失败：它是「还缺一次"
            "身份补全」，而拿一个只有标识的行去检索正是 2026-09-23 那次事故的形状。"
            "counts_line 末尾两个数并列出现 —— 行数答「读进去多少条」，"
            "distinct_entities 答「里面有多少个东西」，合成一个数会把「25 行是同一块"
            "场地的三种写法」读成「有 25 个客户」。\n\n"
            "batch_id / scope / cutoff / cursor 是**调用方申报的上游出处**，本层无法"
            "核实它们 —— 空串是「缺席」，不是「范围是空的」。\n\n"
            "research 那一段是 submit_research(input_kind=\"source_receipt\", "
            "input_ref=<receipt_id>) 将会看到的东西，它**从盘上那一份读回来**："
            "这才是拿去受理的输入，而不是这次调用手里那个对象。"
        ),
    }


# ----------------------------------------------------------------------
# 池：promote_receipt（回执 → 资产 + canonical 账号）与 list_assets（池子 → 读数）
# ----------------------------------------------------------------------
def _asset_row(asset: VenueAsset) -> dict[str, Any]:
    """一行场地资产的读数。**四格域名一起给**，因为它们答的是四个不同的问题。

    ``claimed_by_source`` 是**上游说的**（一次转述：我们不认识写它的人，也没有
    任何凭据），``discovered_url`` / ``candidate`` 是**我们看到的**，``verified``
    是**我们证过的**。四格合成一格「域名」之后，「这个市场里有多少块场地已经有
    官网了」与「有多少块只有别人说的一句话」就只能回答成同一个数 —— 而后者恰好
    是核验那一步的**待办队列**。

    ``verdict`` 与 ``verified`` 一起给，理由与 ``_scene_row`` 的
    ``accepts_new_work`` 配 ``dispatch_refusal`` 逐字相同：一格是判词，一格是原文。
    只看 ``verified`` 为空，读不出「还没查过」与「查过、确认没有官网」的差别 ——
    而这两件事下一步要做的事相反（一个去查，一个不用查了）。

    ``source_identity`` 也在这里：它是「这一行从哪来」的原文（``osm:node/123``），
    而 ``name`` / ``address`` 是**来源给的那两个格**。两者一起给，是因为
    「这一行的名字是谁说的」这个问题只能靠它回答。
    """
    return {
        "venue_asset_id": asset.venue_asset_id,
        "name": asset.name,
        "venue_type": asset.venue_type,
        "surface": asset.surface,
        "address": asset.address,
        "country": asset.country,
        "source_identity": asset.source_identity,
        "domain": {
            "claimed_by_source": asset.claimed_official_domain,
            "discovered_url": asset.discovered_url,
            "candidate": asset.candidate_domain,
            "verified": asset.verified_official_domain,
            "verdict": asset.domain_verdict.value,
            "evidence_count": len(asset.domain_evidence_ids),
        },
        "evidence_count": len(asset.evidence_ids),
        "first_seen_at": _instant(asset.first_seen_at),
        # ``None`` 是「从那以后没再看过」，**不是**「看过一次在时间零点」——
        # 与 ``application/pool.py::_later`` 那句注解同一条。
        "last_verified_at": (
            None if asset.last_verified_at is None else _instant(asset.last_verified_at)
        ),
    }


def _source_namespace(source_identity: str) -> str:
    """``osm:node/123`` → ``osm``；没有来源编号的行 → ``(none)``。

    汇总按**命名空间**分组而不是按标识：``osm:node/123`` 与 ``site:a.example``
    不是同一种引用（一个说的是一份登记册里的对象，一个是本层从页面上认出的主机），
    把它们分成两组才读得出「这个池子里有多少行是从登记册来的」。逐行列标识那是
    ``_asset_row`` 的事。

    ``(none)`` 是一个**值**而不是缺席：没有来源编号的行（只按名字与地点取句柄的
    那些）合成一格，是为了让「这个池子里有几行认不出出处」有一个数 —— 空键
    ``""`` 在报告里看起来像排版。
    """
    head, separator, _ = source_identity.partition(":")
    return head if separator and head else "(none)"


def _pool_summary(assets: Sequence[VenueAsset]) -> dict[str, Any]:
    """池子整体长什么样。**在截断之前算**，理由与 ``list_accounts`` 逐字相同。

    ## 两个域名数不是同一个数

    ``with_verified_domain`` 是「已经证得官网的场地」，``with_an_unchecked_upstream_claim``
    是「上游给了一句、我们还没对它做出任何判定」。后者是核验那一步的待办；合成
    一个「有域名的」会把待办读成已完成 —— 而这两件事的下一步相反。

    ``never_re_checked`` 与池子的大小一起读：它是「一次维护要复核多少块场地」这个
    问题的答案。**它不是「有多少块场地过时了」**：这一层不知道任何一块场地的
    新鲜度该怎么算（那是 §11 的事），它数的是「``last_verified_at`` 还是空的」。
    """
    by_verdict: dict[str, int] = {}
    by_namespace: dict[str, int] = {}
    for asset in assets:
        verdict = asset.domain_verdict.value
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        namespace = _source_namespace(asset.source_identity)
        by_namespace[namespace] = by_namespace.get(namespace, 0) + 1

    def has_a_lead(asset: VenueAsset) -> bool:
        return bool(
            asset.claimed_official_domain
            or asset.discovered_url
            or asset.candidate_domain
            or asset.verified_official_domain
        )

    return {
        "by_domain_verdict": by_verdict,
        "by_source_namespace": by_namespace,
        "with_verified_domain": sum(
            1 for asset in assets if asset.verified_official_domain
        ),
        "with_an_unchecked_upstream_claim": sum(
            1
            for asset in assets
            if asset.claimed_official_domain
            and asset.domain_verdict is DomainVerdict.UNKNOWN
        ),
        "with_no_domain_lead_at_all": sum(1 for asset in assets if not has_a_lead(asset)),
        "never_re_checked": sum(1 for asset in assets if asset.last_verified_at is None),
    }


def _list_assets(
    *,
    settings: Settings,
    assets: Callable[[], VenueAssetStore] | None,
    limit: int | None,
) -> dict[str, Any]:
    """这个市场的场地资产池现在是什么样：每一行的四格域名、两个时刻、证据条数。

    ## 它答的是「有什么」，不是「谁是谁」

    池子里的一行是**一块场地**（``VenueAsset``），不是账号，也不是联系人。这块
    场地由谁负责、能不能联系、走哪条路线是接下来几步的事（§5 / §9），**不在这一
    格**。一份把「池子里有 40 行」读成「有 40 个客户」的报告，会让后面每一步的
    缺口都看起来像已经有了。

    ## 读一次**不建库**

    与 ``list_scenes`` 逐字同一条：库文件不存在时这一格是 ``no_store``，``assets``
    是空列表 —— 而那不是「这个市场一块场地都没有」。后者是一句业务结论，前者说的
    是「还没有任何东西落进过这个库」。抽掉这一格，一个刚起出来的部署会得到一个
    「池子是空的」，而它的处置是去折一份回执，不是去查为什么没有场地。
    """

    def capped(
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """截断只在这一处发生 —— 三条分支（没接线 / 没建库 / 读到了）走同一句。

        与 ``list_accounts`` 同一条：**负数上限是调用方给错了**，所以它要走工具
        结果那条通道。把它放在这里、而三条分支各写一次 ``apply_cap``，下面那两条
        分支的拒绝就会**从处理器里逃出去**成一次内部错误 —— 一句话里连「上限」
        两个字都没有。一个负数上限在空池子上同样是一次算错，所以它要在这里被拒，
        不因为池子里没有行而侥幸通过。
        """
        try:
            kept, decision = apply_cap(
                rows, limit, key=lambda row: row["venue_asset_id"]
            )
        except ConfigError as exc:
            raise ToolError(str(exc)) from exc
        return kept, decision.as_dict()

    if assets is None:
        return {
            "assets": [],
            "asset_count": 0,
            "pool_state": _SCENE_RECORD_NOT_WIRED,
            "workspace": settings.profile,
            "summary": None,
            "truncation": capped([])[1],
            "note": (
                "这个服务实例没有接场地资产池存储，所以「这个市场已经有哪些场地」"
                "读不出来。这不是「一块场地都没有」—— 前者的处置是接线，后者是去"
                "折一份回执（`promote_receipt`）。"
            ),
        }
    if not settings.database_path.is_file():
        return {
            "assets": [],
            "asset_count": 0,
            "pool_state": _SCENE_RECORD_NO_STORE,
            "workspace": settings.profile,
            "summary": None,
            "truncation": capped([])[1],
            "note": (
                f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
                "所以这个部署还没有任何一块场地落进过池子。这是一个正常答案，"
                "不是读不出来 —— 池子由 `promote_receipt` 折进。"
            ),
        }

    store = assets()
    records = store.list_assets()
    rows = [_asset_row(asset) for asset in records]
    kept, truncation = capped(rows)
    return {
        "assets": kept,
        "asset_count": len(rows),
        "pool_state": _SCENE_RECORD_PRESENT,
        "workspace": settings.profile,
        "summary": _pool_summary(records),
        "truncation": truncation,
        "note": (
            "一行是**一块场地**，不是一个账号、也不是一个联系人：这块场地由谁负责、"
            "能不能联系、走哪条路线是后面几步的事，这一格只说「有什么」。\n\n"
            "domain 四格一起看：claimed_by_source 是**上游说的**（一次转述），"
            "discovered_url / candidate 是**我们看到的**，verified 是**我们证过的**。"
            "合成一格「域名」会让「有多少块场地已经有官网」与「有多少块只有别人说的"
            "一句话」答成同一个数 —— 而后者正是核验那一步的待办。verdict 与 verified "
            "一起给同理：verified 为空读不出「没查过」与「查过、确认没有官网」的差别。\n\n"
            "last_verified_at 为空是「从那以后没再看过」，不是「看过一次在时间零点」——"
            "维护要复核多少块场地看的就是它（summary.never_re_checked）。\n\n"
            "summary 覆盖**整个池子**（在截断之前算），截断的是 assets 那一列："
            "一份 300 行的池子在 limit=100 下仍然是一个 300 行的池子，被砍掉的那 200 行"
            "由 truncation 报出来。\n\n"
            f"workspace 是本部署的 Scene（{settings.profile}）：本工具只列属于它的"
            "场地。库里若有别的 workspace 的行，它们不属于这个部署，这里读不到"
            "（那是一条错误，不是一个空结果）。"
        ),
    }


def _promotion_row(outcome: PromotionOutcome) -> dict[str, Any]:
    """一行处置的读数。**三格与 disposition 一起给**，因为读者下一步总是同一个问题：
    「它折进去了吗」与「它为什么没折进去」。只给 disposition 的话，``asset_only``
    与 ``rejected`` 各自还要再问一次池子。"""
    return {
        "ordinal": outcome.ordinal,
        "row_id": outcome.row_id,
        "account_id": outcome.account_id,
        "venue_asset_id": outcome.venue_asset_id,
        "disposition": outcome.disposition.value,
        "organisation": outcome.organisation,
        "evidence_ids": list(outcome.evidence_ids),
        "evidence_count": len(outcome.evidence_ids),
        # 折进去的那两种给 ``""``：理由那一格是给「为什么没成」用的，而一条成功的行
        # 写一句「因为成了」会让下一个人以为它也有过歧义。
        "reason": outcome.reason,
    }


def _promote_receipt(
    *,
    settings: Settings,
    assets: Callable[[], VenueAssetStore] | None,
    accounts: Callable[[], AccountPoolStore] | None,
    receipt_id: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """把一份回执折进池子：**场地资产 + canonical 账号**，逐行给出去向。

    ## 它接的是哪一段断口（§1）

    这一条原来是 ``promote_receipt``，只折资产。而研究侧的主体当时是 ``row-…``
    —— 一个只在某一份工件里有效的行句柄，与 ``market_account.account_id`` 没有
    交点。于是检索发生了、证据落了盘，而账号池里一个账号都没有：
    ``research_work_item.account_id ∩ market_account.account_id`` 是空集，两份报告
    各自看起来都正常。

    那一步就是这里，而且**只有**这里：``application/promotion.py::promote_receipt``
    是那条链的 application use case（回执行 → 场地资产 → canonical 账号 + 重新锚定
    的证据）。这一层只做协议转换 —— 读入参、取时刻、拿存储、把结果渲染成回执。
    业务规则一行都不在这里（§4：MCP 面负责协议/身份/参数/错误映射，application 负责
    validate/bind/grade/persist）。上一版把「折资产」写在这具函数体里，那正是这条
    规矩的反面，而它当时看起来完全正常。

    ## 为什么改名：原来的名字会撒谎

    它现在写**三张表**（证据、资产、账号）。留着 ``promote_receipt`` 这个名字，读
    工具表的人会以为调用它之后账号池没有变化 —— 而账号池恰恰是 §1 那一段要修的东西。
    工具名是这一层唯一一句能被读到的承诺，所以它跟着行为一起改。

    ## 入参只有 ``receipt_id``，这是刻意的

    * **没有 ``limit``。** 截断一次读是「少看几行」，截断一次写是**少写几行** ——
      而池子上没有任何一栏会显示「这一份回执本来还有 20 行」。一份读不完的回执
      是一次分页问题（``read_motherpool`` 的 ``cursor``），不是一次可以静默截断的
      列表。（返回值里那个 ``truncation`` 截的是**回执的渲染**，不是写入。）
    * **没有 ``seen_at`` / ``at``。** 时刻由服务端取（``utc_now``）。这个时刻写进
      ``first_seen_at``，而它是「我们**何时**第一次记下这块场地」—— 一个可以填时刻
      的接口能让调用方把一块新场地声称成三年前就认识的，而报告上什么都看不出来。
    * **没有 ``workspace``。** 血统是**行自己的**（``ports/pool.py`` 的模块 docstring），
      这里由本部署的 ``settings.profile`` 定。一个能填 workspace 的参数，等于让一次
      调用把行写进另一个部署的池子 —— 而那两个池子在同一个库文件里。
    * **没有 ``source_method_id``。** 与 ``import_motherpool`` 同一条：那份回执已经
      说了这一批是怎么来的，再让调用方说一遍就是给同一件事实两个出处。

    ## 资产与账号**两个存储都必填**，缺一个就说缺的是哪一个

    这一条工具**同时**写场地资产池与账号池。只接了一半的部署跑不了这一步 —— 而它
    必须在**第一次调用**时说清楚缺的是哪一半，而不是折了资产、报一次成功，让调用方
    以为账号也有了。所以两个 ``None`` 各有一条自己的消息。

    （``promote_receipt`` 时代只需要资产池：那时它就只写那一张表。这不是「少了一个
    可选参数」，是这一步的语义变了 —— 它现在产出的是 §1 那条链上的两个主语。）

    ## 证据库用**可写**的那一个（``write_store``）

    跨工作区的证据、账号池里读不回来的证据引用，都是在这一步第一次真的落盘的。读
    路径用 ``read_store``（库不在就是「还没有产物」），写路径用 ``write_store``
    （库不在就建）—— 两句话都在 ``tools`` 里各有一处定义，理由见那两个函数。

    ## 回执是**从盘上读回来的**那一份

    与 ``import_motherpool`` 的 ``research`` 那一段同一条判据：盘上那一份才是权威
    （``load_receipt`` 会验文件名与 id 一致、内容与 id 自洽）。拿调用方手里的那些行
    来折，等于让「读进去的」与「折进去的」可能来自两份不同的东西。

    ## 折是幂等的，而这不靠一次比对

    句柄由来源编号（或名字+地点）派生，账号 id 由句柄派生，证据是内容寻址的 ——
    所以同一份回执折两次，第二次三张表都与折一遍逐字节相同，``first_seen_at`` 也
    只往早走。这条成立的方式是「派生稳定 + 合并规则不抹格子」，不是「先查一次有没有
    折过」—— 后者是一次读—改—写，而并发下那次读给出的答案可能已经不是真的
    （``ports/pool.py`` 逐字写了这件事）。

    ## 半途失败之后重跑是安全的

    逐行折，一行抛出来会让这次调用整体失败，而**前面几行已经落盘了**。这是可以接受
    的，因为重跑不会重复。换成「先攒在一个事务里、要么全成要么全不成」，代价是把
    整份回执的行锁在同一把写锁里 —— 而这一层不需要那条保证，需要的是**部分落盘也说
    得出自己在哪停的**（那句话来自存储层，随失败一起回到调用方）。

    ## 它不看 Scene 的状态

    与 ``import_motherpool`` 一致：池子按 workspace 存，而这个部署只有一个
    workspace，所以这条工具连 ``scene_id`` 都没有。暂停一个 Scene 停的是**派发**，
    不是「把已经交进来的材料读进池子」—— 后者不做的话，恢复之后要重跑一遍导入。
    """
    if assets is None:
        raise ToolError(
            "本服务实例没有接场地资产池存储，所以这份回执折不进去。\n"
            "这是一条**接线事实**（组合根没有构造资产池），不是「这份回执里没有"
            "可折的行」—— 后者的处置是看回执的 counts，前者是去接线。"
        )
    if accounts is None:
        # 这一条与上面那条分开，而不是合成一句「存储没接齐」：缺哪一半决定了去哪
        # 接线，而两条消息各自点名了自己的那一个端口。
        raise ToolError(
            "本服务实例没有接账号池存储，所以这份回执里折不出账号。\n"
            "折资产是折得了的，但**这一条工具不折一半** —— 它产出的正是 §1 那条链上"
            "的两个主语（场地与账号），只写场地那一半会得到一个「看起来成功了」的"
            "回执，而研究那一侧随后按 canonical 账号 id 派件时会一条账号都找不到。"
        )

    inputs_dir = settings.inputs_dir
    try:
        receipt = load_receipt(inputs_dir, receipt_id)
    except ConfigError as exc:
        # 找不到 / 读不出来 / 文件名与 id 不一致 / 内容与 id 对不上（=被改过）——
        # 四条都是**调用方要据它改东西**的结局，所以走工具结果，不走异常链。
        raise ToolError(str(exc)) from exc

    asset_store = assets()
    account_store = accounts()
    evidence = write_store(settings)

    # 折之前先量一次两张表的大小。两次读之间**没有**锁，所以下面那两个数是池子在
    # 两个瞬间的**规模**，不是「这一次调用增加了多少」—— 并发的 worker 期间别的
    # 行也会算进那个差。把差说成「本次新增」在单操作者时是对的、在 §7 那种重叠
    # 运行里就是一句假话，而报告上这两者长得一样。
    assets_before = len(asset_store.list_assets())
    accounts_before = len(account_store.list_accounts())

    try:
        outcomes = promote_receipt(
            receipt,
            workspace=settings.profile,
            seen_at=utc_now(),
            assets=asset_store,
            accounts=account_store,
            evidence=evidence,
            run_id=run_id,
        )
        assets_after = len(asset_store.list_assets())
        accounts_after = len(account_store.list_accounts())
    except StoreError as exc:
        raise _store_failure(exc) from exc
    except AssetConflictError as exc:
        # 与 ``StoreError`` 是兄弟（处置不同），但同样要回到工具结果上去 ——
        # 理由逐字见 :func:`_store_failure`。
        raise ToolError(str(exc)) from exc
    except (RunStateError, RunFoldLedgerError) as exc:
        # 给了 ``run_id`` 而这一笔归属落不下去。**不回退成「不记账也照折」**：
        # 池子的写与那一笔账是同一个事务（§B17 条件 2），所以走到这里时池子一个
        # 字节都没写 —— 这与「折了一半」是两件事，而前者是可以整份重发的。
        # 走工具结果而不是异常链，理由与上面两条同：调用方要据这句话改东西。
        raise ToolError(str(exc)) from exc

    # 三格**恒有**，包括计数为 0 的那一格：``{"promoted": 25}`` 与
    # ``{"promoted": 25, "asset_only": 0, "rejected": 0}`` 在读者那里是两件事，
    # 后者说得出「这一批一行都没被拒」。与 ``_dispatch_block`` 的 ``counts``
    # 同一条纪律（「形状固定不是洁癖」）。
    counts = {disposition.value: 0 for disposition in PromotionDisposition}
    for outcome in outcomes:
        counts[outcome.disposition.value] += 1

    rows = [_promotion_row(outcome) for outcome in outcomes]
    kept, decision = apply_cap(rows, _MAX_PROMOTION_ROWS, key=lambda row: row["row_id"])

    return {
        # 与 ``import_motherpool`` 用的是**同一个**读数函数：一份回执要说的话只有
        # 一处定义，两处各编一组的话，「这一批读成了什么」会在两条工具上各说一套。
        "receipt": _motherpool_receipt(receipt),
        "promotion": {
            # 「折了几行」与「这是几个东西」并列出现，理由同回执的
            # ``resolved_row_count`` / ``distinct_resolved_count``：三行同一个句柄
            # 是一块场地的三种写法，合成一个数会把「这个市场多了一块场地」看成
            # 「多了三块」。``handles`` 数的是**去重后**的场地（也就是账号的可能上界），
            # 而 ``rows`` 数的是折了几行 —— 两个数并列，读的人才分得开「这一批折了
            # 12 行」与「这个市场多了 12 块场地」。
            "rows": len(outcomes),
            "handles": len({outcome.venue_asset_id for outcome in outcomes}),
            "counts": counts,
            "assets_before": assets_before,
            "assets_after": assets_after,
            "accounts_before": accounts_before,
            "accounts_after": accounts_after,
            "rows_detail": kept,
            "truncation": decision.as_dict(),
        },
        "workspace": settings.profile,
        "note": (
            "折的是回执里**resolved** 的那些行，一条不多：merged 是同一块场地的另"
            "一种写法（在回执里已经并掉了）、excluded 命中过声明过的排除项、pending "
            "还缺一次身份补全、failed 是那一行读不出来。四种去向我这一条都不折，"
            "各自的条数看 receipt.counts —— 一份 40 行的回执只折进 12 行，读的人"
            "要能立刻看出另外 28 行去了哪。\n\n"
            "promotion.counts 恒有三格，逐行的去向在 rows_detail 里（被截断时 "
            "truncation 报出砍了几条）：promoted 是场地与账号都折进去了，"
            "asset_only 是**只有场地**（那一页没取到内容，而编一条证据去凑一个账号"
            "正是这一层唯一不能做的事），rejected 是这一行什么都没写。\n\n"
            "账号 id 是**派生**的（acct-<hex16>，由场地句柄来），与 "
            "promote_receipt 折进 market_account 的、以及研究件的主语是同一个值 —— "
            "它**不是** row_id：row-… 是「哪份工件里的第几行」，工件一重排它就变。"
            "所以下一步是 submit_research(input_kind=\"discovery_scope\")，而它读的"
            "主体就是这里写下的那几个账号。\n\n"
            "assets_before / assets_after 与 accounts_before / accounts_after 是两张"
            "表在**两个瞬间**的规模，不是这一次调用的增量：两次读之间没有锁，并发的"
            "worker 期间别的行也会算进那个差。\n\n"
            "折是幂等的：句柄由来源编号（没有编号时按名字与地点）派生，所以同一份"
            "回执折两次不会多出一行，first_seen_at 也只往早走。\n\n"
            "last_verified_at 折完仍然是空的，证据是**发现那一轮**取回的那几条 —— "
            "**读一份回执不是一次重新观测**（``run_maintenance`` 那条「本地记录也算"
            "观测」在这里不适用：它数的那些行**就是**它那一面的主语，而这份回执说的是"
            "别的对象）。填上它，「这块场地多久没复核了」这个只在池子建起来之后才"
            "有意义的问句，会在第一次导入那天就得到「刚刚复核过」，而下一轮维护会把"
            "它们整批跳过。"
        ),
    }



# ----------------------------------------------------------------------
# 责任关系：remember_responsibility（判定 → 表）与 list_responsibilities（表 → 读数）
# ----------------------------------------------------------------------
def _relation_row(
    relation: AccountVenueRelation, names: Mapping[str, str | None]
) -> dict[str, Any]:
    """一行责任关系的读数。

    ## 场地那一格给的是**名字**，而它有可能是 ``None``

    ``venue_asset_id`` 是一个派生的句柄（``src-<hex16>`` / ``named-<hex16>``），
    它认得出「是不是同一块场地」，认不出「是哪一块」。所以这一行带上名字 ——
    否则一份关系表读起来是一串十六进制，而 §5 的问题是「**谁**对**哪块场地**负责」。

    ``None`` 说的是「池子里没有这块场地的行」：那不是排版的缺失，是一条真事实
    （关系写下来了，而那块场地不在池子里）—— 它的处置与「名字是空的」不同。

    ## 证据给的是 **id 列表**，不只是条数

    与 ``_asset_row`` 的 ``evidence_count`` 不同，那里数的是一块场地历次观测攒下的
    引用（可以很长，而读者要的只是「有没有」）。这里一行关系的证据通常是 1~3 条，
    而读者的下一步**就是**去看那几条 —— 一份只有条数的表把「凭什么说这个 Trust
    负责这块场地」挡在了报告外面，而 §5 要的正是那个凭据。``get_evidence`` 收的
    就是这些 id。

    ## 这一行是**判出来的**还是**手工记的**，也在这一行上

    ``decision_record_ids`` 与 ``evidence_ids`` 同一条理由：它是那两种行在表里
    **唯一**的差别 —— 一边走 ``submit_findings``（每条结论当场拿 id 回台账核对过），
    一边走 ``remember_responsibility``（那条工具手里没有判定台账）。只报证据的话，
    一条手工记下来的关系与一条研究判出来的关系读起来一字不差，而 §3 要的正是
    「关系由**真实判定**支撑」这一句可读。

    空表**有**答案，它说的是「这一行不是判出来的」—— 这里没有 ``None``：这一行本来
    就在表里（读的时候它已经存在了）。id 的形状就是 ``decide`` 回执里的 ``record_id``，
    拿它可以读回那一次调用。

    ## ``unresolved`` 不是「没关系」

    见 :class:`~marketops.domain.enums.VenueRelation`：它说的是「有关系，但关系还没
    定下来」。这一行照常给出 —— 把它藏起来（或写成「没关系」）会让一条待查的关系
    看起来像一条查完的结论，而两者的下一步正相反。
    """
    return {
        "relation_key": relation.relation_key(),
        "account_id": relation.account_id,
        "venue_asset_id": relation.venue_asset_id,
        "venue_name": names.get(relation.venue_asset_id),
        "relation": relation.relation.value,
        "evidence_ids": list(relation.evidence_ids),
        "evidence_count": len(relation.evidence_ids),
        "decision_record_ids": list(relation.decision_record_ids),
        "first_seen_at": _instant(relation.first_seen_at),
        # ``None`` 是「从那以后没有重新证实过」，**不是**「证实过一次在时间零点」——
        # 与 ``_asset_row`` 的那一格逐字同一条。
        "last_verified_at": (
            None
            if relation.last_verified_at is None
            else _instant(relation.last_verified_at)
        ),
    }


def _responsibility_summary(
    relations: Sequence[AccountVenueRelation],
    *,
    assets_without_a_relation: int | None,
) -> dict[str, Any]:
    """责任关系表整体长什么样。**在截断之前算**，理由与 ``_pool_summary`` 逐字相同。

    ## ``by_relation`` 把六个成员都列出来，零个也列

    关系是一个**闭集**（``VenueRelation`` 的六个成员），所以「这一种一条都没有」是
    一个**测出来的**事实，不是没有数据。省掉零的那几行，读的人就分不出「这个市场
    没有预约控制方」与「这一栏我今天没算」—— 而这两件事的下一步相反。

    ``assets_with_an_unresolved_relation`` 与 ``by_relation["unresolved"]`` 不是
    同一个数：一个是**有多少块场地上**有这种行，一个是行数。合成一个数会让「一块
    场地上有 3 条待查关系」与「3 块场地各有一条待查」读成同一句话 —— 而前者要查的
    是那一块场地，后者要查的是三块。

    ## 那一格缺失时给 ``None`` 与理由，不给 0

    ``assets_without_a_relation`` 是这张表与池子**合起来**才答得了的问题（池子里
    有多少块场地在这张表上一条关系都没有），所以按场地/按账号读的那两条路上它
    **测不出来**：那时它是 ``None``，理由写在 ``summary_gap`` 里。
    写 0 会把「我没算」说成「一块都没有」—— 而那恰好是这个数唯一会被用错的方向：
    它是**待办队列**（还有哪些场地没研究过责任方），不是「这些场地没有人负责」
    （后者是 §5 明确不许从「没有行」推断出来的结论）。

    它**不是**「有多少块场地过时了」：这一层不知道任何一条关系的新鲜度该怎么算
    （那是 §11 的事），它数的是 ``last_verified_at`` 还是空的那些行。
    """
    by_relation: dict[str, int] = {member.value: 0 for member in VenueRelation}
    for relation in relations:
        by_relation[relation.relation.value] += 1

    return {
        "by_relation": by_relation,
        "assets_with_relations": len({relation.venue_asset_id for relation in relations}),
        "assets_with_an_unresolved_relation": len(
            {
                relation.venue_asset_id
                for relation in relations
                if relation.relation is VenueRelation.UNRESOLVED
            }
        ),
        "accounts_named": len({relation.account_id for relation in relations}),
        "relations_never_re_verified": sum(
            1 for relation in relations if relation.last_verified_at is None
        ),
        "assets_without_a_relation": assets_without_a_relation,
        "summary_gap": (
            ""
            if assets_without_a_relation is not None
            else "assets_without_a_relation 只有**不设场地/账号过滤**的那一次读答得出来："
            "它数的是整个池子里一块关系都没有的场地，而按场地读时池子里的其余场地"
            "根本不在这次读的范围里。这里给 null 而不是 0 —— 0 会被读成「每一块场地"
            "都有负责方」。"
        ),
    }


def _list_responsibilities(
    *,
    settings: Settings,
    assets: Callable[[], VenueAssetStore] | None,
    relations: Callable[[], AccountVenueRelationStore] | None,
    venue_asset_id: str | None,
    account_id: str | None,
    limit: int | None,
) -> dict[str, Any]:
    """读这个市场的责任关系表：谁对哪块场地负什么责、凭什么、什么时候记下的。

    ## 三个问题、三条读法，不是一个过滤器的三种参数

    ``ports/pool.py`` 给这个端口三个读入口（按场地 / 按账号 / 全部），而这一条工具
    把前两个做成两个参数、第三个做成「两个都不给」。**两个都给是拒绝，不是取交集**：
    「这个账号对这块场地负什么责」是**一行**的答案，而按场地读一次就把它给全了 ——
    再发明一条「场地 × 账号」的读法，是让同一句话有两个出处。拒绝的措辞里点名那条
    现成的读法。

    ## 按场地读是 §5 的问题，按账号读是 §6 的问题

    两条都留着，因为它们回答的是两个方向的问题（「这块场地由谁负责」与「这个账号
    在管哪些场地」），而池子建起来之后**两个方向都会被问到**。今天这一条工具是
    ``relations_for_account`` 在生产路径上的第一个读者 —— 一个没有读者的读入口在
    盘上与「已经做完了」长得一样。

    ## 读一次**不建库**

    与 ``list_assets`` 逐字同一条：库文件不存在时这一格是 ``no_store``，``relations``
    是空列表 —— 而那不是「这个市场没有任何责任关系」。后者是一句业务结论，前者说的
    是「还没有任何东西落进过这个库」。
    """

    def capped(
        rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """截断只在这一处发生 —— 三条分支（没接线 / 没建库 / 读到了）走同一句。

        与 ``_list_assets`` 的 ``capped`` 逐字同形：**负数上限是调用方给错了**，
        所以它要走工具结果那条通道，而三条分支各写一次 ``apply_cap`` 会让前两条
        的拒绝从处理器里逃出去成一次内部错误。
        """
        try:
            kept, decision = apply_cap(rows, limit, key=lambda row: row["relation_key"])
        except ConfigError as exc:
            raise ToolError(str(exc)) from exc
        return kept, decision.as_dict()

    if venue_asset_id and account_id:
        raise ToolError(
            "venue_asset_id 与 account_id 只能给一个。\n"
            "两个都给看起来像「取交集」，而那一问的答案本来就是**一行**——"
            "`list_responsibilities(venue_asset_id=...)` 已经把这块场地上的每一行"
            "（含这个账号的那一行）给全了。再发明一条「场地 × 账号」的读法，是让同一"
            "句话有两个出处。\n"
            "要问「这个账号在管哪些场地」就不给 venue_asset_id；要问「这块场地由谁"
            "负责」就不给 account_id。"
        )

    missing = [
        name
        for name, wired in (("场地资产池", assets), ("责任关系表", relations))
        if wired is None
    ]
    if missing:
        return {
            "relations": [],
            "relation_count": 0,
            "responsibility_state": _SCENE_RECORD_NOT_WIRED,
            "workspace": settings.profile,
            "summary": None,
            "truncation": capped([])[1],
            "note": (
                f"这个服务实例没有接：{'、'.join(missing)}。\n"
                "两个都要有：关系表答「谁对哪块场地负责」，而每一行还要拿场地的**名字**"
                "去对上池子里那一块（id 是一个派生的句柄，认得出是不是同一块，认不出"
                "是哪一块）。这不是「一条关系都没有」—— 前者的处置是接线，后者是去"
                "一条条记下来（`remember_responsibility`）。"
            ),
        }
    if not settings.database_path.is_file():
        return {
            "relations": [],
            "relation_count": 0,
            "responsibility_state": _SCENE_RECORD_NO_STORE,
            "workspace": settings.profile,
            "summary": None,
            "truncation": capped([])[1],
            "note": (
                f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
                "所以这个部署还没有任何一条责任关系落过盘。这是一个正常答案，"
                "不是读不出来 —— 关系由 `remember_responsibility` 记下。"
            ),
        }

    asset_store = assets()
    relation_store = relations()
    filtered = bool(venue_asset_id or account_id)
    try:
        if venue_asset_id:
            records = relation_store.relations_for_asset(venue_asset_id)
        elif account_id:
            records = relation_store.relations_for_account(account_id)
        else:
            records = relation_store.list_relations()
        # 名字逐个去取，而不是把整个池子读进来再建索引：按场地读时上面那一批行
        # 通常只指着一两块场地，读全池在几百行的市场上是几十倍的白读。取不到是
        # ``None``（池子里没有这块场地的行），不是一个要跳出循环的错。
        names: dict[str, str | None] = {}
        for record in records:
            if record.venue_asset_id not in names:
                asset = asset_store.get_asset(record.venue_asset_id)
                names[record.venue_asset_id] = None if asset is None else asset.name
        # 「池子里有多少块场地一条关系都没有」是整个池子的事，所以只有不设过滤的
        # 那一次读才去读池子；按场地读时它测不出来（那时是 null 加一句理由，而不是
        # 一个 0 —— 见 ``_responsibility_summary``）。
        assets_without_a_relation = (
            None
            if filtered
            else sum(
                1
                for asset in asset_store.list_assets()
                if asset.venue_asset_id not in {r.venue_asset_id for r in records}
            )
        )
    except StoreError as exc:
        raise _store_failure(exc) from exc

    rows = [_relation_row(record, names) for record in records]
    kept, truncation = capped(rows)
    return {
        "relations": kept,
        "relation_count": len(rows),
        "responsibility_state": _SCENE_RECORD_PRESENT,
        "workspace": settings.profile,
        "summary": _responsibility_summary(
            records, assets_without_a_relation=assets_without_a_relation
        ),
        "truncation": truncation,
        "note": (
            "一行说的是「**这个账号**对**这块场地**负**这一种**责」，不是「这个账号是"
            "什么」：账号本身的组织信息、别名、状态在账号池那一条工具上（§6）。\n\n"
            "同一块场地与同一个账号可以同时有几行（owns / operates / maintains / …），"
            "§5 要求这几种关系并存、不强制单选 —— 所以行数比场地数多，不是重复。\n\n"
            "relation=unresolved 说的是「有关系、但关系还没定下来」，与「没有这一行」"
            "是两件事：没有行 = 不知道它们有没有关系，unresolved = 知道有关系而没查清"
            "是哪一种。summary.assets_with_an_unresolved_relation 就是这条待查队列。\n\n"
            "evidence_ids 可以拿去 get_evidence 逐条读 —— 一条关系至少有一条证据，"
            "这是写入口的硬要求（说得出「有关系」也是一个要凭据的判定）。\n\n"
            "last_verified_at 为空是「从那以后没有重新证实过」，不是「证实过一次在时间"
            "零点」：这一层不知道该按什么周期复核责任（那是 §11 的事）。\n\n"
            "summary.assets_without_a_relation 数的是池子里一块关系都没有的场地，"
            "它**只有不设过滤的那一次读**答得出来（按场地读时是 null 加一句理由）："
            "那是待办队列，不是「这些场地没有人负责」——「没有行」推不出后者。\n\n"
            "summary 覆盖**整张表**（在截断之前算）：一份 300 条关系的表在 limit=200 下"
            "仍然是一个 300 条的表，被砍掉的 100 行由 truncation 报出来。\n\n"
            f"workspace 是本部署的 Scene（{settings.profile}）：本工具只列属于它的行。"
            "库里若有别的 workspace 的行，它们不属于这个部署，这里读不到（那是一条"
            "错误，不是一个空结果）。"
        ),
    }


def _remember_responsibility(
    *,
    settings: Settings,
    assets: Callable[[], VenueAssetStore] | None,
    relations: Callable[[], AccountVenueRelationStore] | None,
    venue_asset_id: str,
    judgements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """把「这块场地由谁负责」的一批判定记进责任关系表，**逐行给出去向**。

    ## 为什么判定的单位是「一块场地 + 若干行」，不是一个扁平的行列表

    §5 要的是 ``VenueAsset ↔ Account`` 的证据关系，而一次研究的产出就是「我刚看过
    **这块**场地，找到了这几个负责方」。让每一行自己带场地 id，等于允许一次调用把
    行写进任意几块场地 —— 而报告上那是一张平平的关系表，「这一行是关于哪块场地的」
    没有一处能出错，也就没有一处能发现写错了。

    ## 三个「不由调用方给」的格子

    * **``workspace``（§5 的 ``scene/version context``）** —— 由本部署的 profile 定。
      能填的话，一次调用就能把行写进另一个 Scene，而两张表在同一个库文件里；§5 的
      「同一块场地与同一个账号在两个 Scene 下可以判出不同的关系」说的是两个 Scene
      **各自**写自己那一行。
    * **``first_seen_at``** —— 由服务端取（``utc_now``）。一个能填时刻的接口能让
      调用方把一条新关系声称成三年前就记下的，而报告上什么都看不出来。
    * **``last_verified_at``** —— 这一次调用**不给**它任何值。记下一条判定不是重新
      证实了这条关系（与 ``promote_receipt`` 不填 ``last_verified_at`` 同一条纪律）：
      填上它，「这条关系多久没复核了」这个只在表建起来之后才有意义的问句，会在第一次
      记录那天就得到「刚刚复核过」，而下一轮维护会把它们整批跳过。

    ## 这条路径写下来的行**没有**判定支撑，而那是如实写下来的

    ``judgements`` 是人交上来的结论，这一条工具手里没有判定台账，也不该去借一个：
    它写下来的每一行 ``decision_record_ids`` 都是空的，而空表只有一个意思 ——
    「这一行不是一次判定得出的」。这一格**不能**由调用方填（``RelationClaim`` 是
    ``extra="forbid"`` 的），所以「这一行有没有判定支撑」不是这条路径能选的东西。

    §3 要的是「关系由真实判定支撑」，那条路是 ``submit_findings``（它的每一条结论
    必须带 ``decision_id`` 并当场回库核对）。两者在表里长得一样，差别**只在**这一格
    —— 所以它是可读的，不是一句自觉。

    ## 一块**已经在池子里**的场地才挂得上关系

    第一件事是 ``get_asset``。查不到就拒绝整次调用，而不是把那条关系绕过资产池写进
    表里 —— 关系表按 ``venue_asset_id`` 索引，一个池子里没有的 id 会让这一行在任何
    一份按场地读的报告里**永远出现不了**（读的时候要拿场地的名字去对上），而它在表里
    躺着，看起来像已经研究过了。拒绝的话里点名两条处置：先折回执，或者核对 id。

    ## 逐行判，一行写不进去不牵连其余

    见 :func:`~marketops.application.relations.remember_relations`：一行坏判定是一条
    **去向**（``rejected`` / ``evidence_unknown``），不是整次失败。所以这一条工具的
    回执里四类去向都要报出来 —— 一份 6 行的判定只落了 2 行，读的人要能立刻看出另外
    4 行去了哪，而不是从「written: 2」去猜。
    """
    missing = [
        name
        for name, wired in (("场地资产池", assets), ("责任关系表", relations))
        if wired is None
    ]
    if missing:
        raise ToolError(
            f"本服务实例没有接：{'、'.join(missing)}，所以这条判定记不进去。\n"
            "两个都要有：关系挂在一块**已经在池子里的**场地上（写之前先按 id 查一次），"
            "而那一行本身落在关系表里。这是一条**接线事实**（组合根没有构造对应的"
            "存储），不是「这块场地没有负责方」—— 后者的处置是去研究，前者是去接线。"
        )

    key = (venue_asset_id or "").strip()
    if not key:
        raise ToolError(
            "venue_asset_id 是**必填**的：一条责任关系说的是「谁对**哪块场地**负责」，"
            "少了这一格就不是一条更宽的判定，而是一条无主的判定。\n"
            "id 由 `promote_receipt` 那边派生（`src-<hex16>` / `named-<hex16>`），"
            "先从 `list_assets` 里取一个。"
        )
    if not settings.database_path.is_file():
        raise ToolError(
            f"还没有写过任何东西（{settings.database_path.name} 尚未创建），所以池子里"
            "没有任何一块场地，这条关系也就挂不上去。\n"
            "这是一个正常答案，不是写不进去 —— 先用 `import_motherpool` 读一份母池、"
            "再用 `promote_receipt` 把它折进场地资产池。"
        )

    asset_store = assets()
    try:
        asset = asset_store.get_asset(key)
    except StoreError as exc:
        raise _store_failure(exc) from exc
    if asset is None:
        raise ToolError(
            f"场地资产池里没有 {key!r} 这块场地，所以这条关系挂不上去。\n"
            "两条处置：把那份母池回执先折进来（`promote_receipt`），或者核对这个 id ——"
            "它是**派生**的句柄（`src-<hex16>` / `named-<hex16>`），不是场地的名字。\n"
            "不绕过池子写这一行，是因为关系按这个 id 索引：池子里没有的 id，这一行在"
            "任何一份按场地读的报告里都出现不了 —— 而它在表里躺着，看起来像研究过了。"
        )

    relation_store = relations()
    # 证据库与关系表是**同一个库文件**（都是 ``settings.database_path``），而上面那次
    # ``get_asset`` 已经证明了它存在（池子里有行），所以这里不可能是 ``None``——
    # ``read_store`` 的 ``None`` 只有一个意思（库文件不在），而那个情形在上面那条
    # 分支里已经说过了。用它而不是再收一个工厂参数：证据库的位置在本仓只有一处
    # （``tools.read_store``），多一个参数就是那一处的第二份拷贝。
    evidence = read_store(settings)
    assert evidence is not None  # noqa: S101 - 见上：上面两条分支已经排除了另一种可能

    try:
        # 折之前先量一次表的大小。两次读之间**没有**锁，所以下面那两个数是表在两个
        # 瞬间的**规模**，不是「这一次调用增加了多少」—— 与 ``_promote_receipt`` 的
        # ``rows_before`` / ``rows_after`` 逐字同一条。
        before = len(relation_store.list_relations())
        outcomes = remember_relations(
            judgements,
            store=relation_store,
            evidence=evidence,
            workspace=settings.profile,
            venue_asset_id=asset.venue_asset_id,
            seen_at=utc_now(),
        )
        after = len(relation_store.list_relations())
    except StoreError as exc:
        raise _store_failure(exc) from exc

    by_disposition = {member.value: 0 for member in RelationDisposition}
    for outcome in outcomes:
        by_disposition[outcome.disposition.value] += 1
    return {
        "venue_asset_id": asset.venue_asset_id,
        "venue_name": asset.name,
        "workspace": settings.profile,
        "outcomes": [_relation_outcome(outcome) for outcome in outcomes],
        "by_disposition": by_disposition,
        "relation_count": len(outcomes),
        "relations_in_the_pool_before": before,
        "relations_in_the_pool_after": after,
        "note": (
            "逐行判，一行写不进去不牵连其余：rejected 是这一行本身构造不出一个能写下来"
            "的关系（关系不在闭集里、账号 id 不规范、一条证据都没有），evidence_unknown "
            "是它挂的引用在这个库里解不开。两类都是**这一行**没写，不是整次调用失败。\n\n"
            "folded 说的是「这一行与同一批里前面某一行是同一条关系，两条合成了一条」"
            "（证据取并集）—— 所以 by_disposition.written 才是这一次**写下去的关系**"
            "条数，relation_count 数的是交上来的行。\n\n"
            "同一块场地与同一个账号可以同时有几条关系（owns / operates / maintains …）："
            "§5 要求这几种并存、不强制单选，所以它们不是同一条关系的几种写法。关系还没"
            "定下来时写 `unresolved` —— 它说的是「有关系、但没查清是哪一种」，比不写"
            "这一行诚实（不写等于说「不知道有没有关系」）。\n\n"
            "last_verified_at 记完仍然是空的：**记下一条判定不是重新证实了它**（与 "
            "promote_receipt 同一条纪律）。decision_record_ids 写下去也是空的：这一条"
            "工具手里没有判定台账，所以它写的关系没有判定支撑 —— 那是这一格的读法"
            "（有判定支撑的那条路是 submit_findings）。relations_in_the_pool_before / "
            "after 是表在两个瞬间的规模，不是这一次调用的增量 —— 两次读之间没有锁。"
        ),
    }


def _relation_outcome(outcome: RelationOutcome) -> dict[str, Any]:
    """一行判定的去向。

    ``row`` 为空（这一行没写）时，``evidence_ids`` / ``evidence_count`` 给的是
    ``None`` 而不是空表与 0：这一行**没有**在表里，所以「它有几条证据」这个问题
    没有答案 —— 写 0 会被读成「这条关系一条证据都没有」，而那是另一件事（那种行
    根本写不进去，见 ``AccountVenueRelation`` 的校验器）。没写的理由在 ``reason``
    那一格，它把处置说全了。

    写进去的那几格以 ``row`` 为准（**表里现在那一行**）：第二次折同一条关系之后
    证据是并集、``first_seen_at`` 是更早的那个，报交上去的那一份会让读的人以为
    表里就是那样。

    ``decision_record_ids`` 是这一行背后那几次判定（``decide`` 回执里那一格叫
    ``record_id``）。它与 ``evidence_ids`` 一样在没写进去时给 ``None``：那一行不在
    表里，「它是哪几次判定得出的」没有答案 —— 而空表有答案，它说的是「这一行不是
    判出来的」（``remember_responsibility`` 那条手工路径写的就是这一种）。
    """
    return {
        "ordinal": outcome.ordinal,
        "account_id": outcome.account_id,
        "relation": outcome.relation,
        "disposition": outcome.disposition.value,
        "evidence_ids": None if outcome.row is None else list(outcome.row.evidence_ids),
        "evidence_count": None if outcome.row is None else len(outcome.row.evidence_ids),
        "decision_record_ids": (
            None if outcome.row is None else list(outcome.row.decision_record_ids)
        ),
        "first_seen_at": (
            None if outcome.row is None else _instant(outcome.row.first_seen_at)
        ),
        "last_verified_at": (
            None
            if outcome.row is None or outcome.row.last_verified_at is None
            else _instant(outcome.row.last_verified_at)
        ),
        "reason": outcome.reason,
    }


# ----------------------------------------------------------------------
# list_accounts
# ----------------------------------------------------------------------
def _account_row(
    *,
    ordinal: int,
    account: Any,
    bundle: Any,
    counts: Mapping[str, int | None],
    refusal: str,
) -> dict[str, Any]:
    """一行账号摘要。**``ordinal`` 在里面，因为它在外面承重。**

    序号决定 ``work_item_id``，所以它是这一行的一部分而不是排版细节：重排一份
    清单会编译出另一批工作（``JobStore.compile_work_items`` 比对整个序列）。
    报告里带着它，「这个账号是第几个」与「这件事为什么换了 id」就答得出来。

    ``notes`` **不转发**。它是自由文本（``AccountRecord`` 的字段说明写着「勿写
    secret」），本回执里没有任何一格需要它，而每转发一处自由文本就是一条凭据
    可能进入 trace 的路。

    ``unchecked`` 逐条列出未判的规则 id，**不折成一个数**：非空不蕴含「放行」，
    它说的是「这些规则没判」。判定与结论是两件事，读成一件事就会得出
    「有未判规则 ⇒ 没排除任何账号」这个错的推论（``AccountOutcome`` 的
    docstring 逐字写了这一点）。
    """
    outcome = grade_account(account, bundle)
    return {
        "ordinal": ordinal,
        "account_id": account.account_id,
        "organisation": account.organisation,
        "cohort": account.cohort.value,
        "domain": account.domain,
        "country": account.country,
        "grade": {
            "status": outcome.status.value,
            "reason": outcome.reason.value,
            "detail": outcome.detail,
            "unchecked": list(outcome.unchecked),
        },
        "stored": dict(counts),
        # 空串 = 这一行拿去派发不会被身份判据挡下。**逐行**报而不是汇总成一句：
        # 「这一份清单能跑」与「这一行能跑」是两个问题，而整份派发是全拒绝
        # （``subjects_for`` 逐字：跳过会让一份五行的清单编译出四个工作单元，
        # 而报告里那个「4」与「清单里本来就只有 4 个」看起来一样）。
        "identity_refusal": refusal,
    }


def _list_accounts(
    *,
    settings: Settings,
    name: str,
    limit: int | None,
) -> dict[str, Any]:
    """这个市场**清单文件里**的每一行现在是什么样：准入判定、已落盘产物条数、能不能派发。

    ## 它读的不是账号**池**

    「账号池」在本文件里只有一个意思 —— 研究产出落盘的那一张表，读它的是
    ``list_account_pool``。这一条读的是 operator 维护的名单（``inputs_dir/<name>.yaml``），
    而名字取得像的那一条（``list_accounts`` vs ``list_account_pool``）在这里正好是
    两件事的分界：一份写着「我打算研究谁」，一份写着「我研究出了谁」。名字上不分，
    读的人没办法知道自己手上那一份是哪一个。

    ## 三个来源，各自答一个不同的问题

    * **清单**（``inputs_dir/<name>.yaml``）答「文件里写着什么」。它的**顺序**是
      ``ordinal``，所以原样保留 —— 按 ``account_id`` 排一次序会让报告更好看，
      同时让每一行的 ``work_item_id`` 换一个值。
    * **profile 的 grading 规则**答「这一行按现行规则判成什么」。走
      ``grade_account``，与派发路径**同一个执行器**；不在这里重写一遍判定。
    * **证据库**答「这一行已经有什么产物」。库不存在时不是 0 —— 见下。

    ## 库不存在时那三个数是 ``None``，不是 ``0``

    ``read_store`` 在库文件不存在时返回 ``None``（「读一次不建库」，那里逐字写了
    为什么读一次**不建库**），而那时这三个计数是 ``None``：**「还没建库」与
    「建了库、这个账号什么都没跑」是两件事**，写成 0 会让一个全新的部署看起来像
    一个跑过而颗粒无收的部署 —— 与 §3.4「未知不得写成 0」同一条。``store_present``
    一格把这一栏整体的可读性说清楚，读的人不必从三格 null 里推。

    ## 汇总**在截断之前**算，而且这是要说出来的

    ``summary`` 覆盖的是清单本身，不是 ``accounts`` 那一列（截断后的）。截断的是
    **这一列**，不是清单 —— 一份 60 行的清单在 ``limit=50`` 下仍然是一份 60 行的
    清单，而一个跟着截断变的汇总会把「池子有多大」变成一个取决于调用参数的数。
    """
    bundle = require_bundle(settings)
    inputs_dir = settings.inputs_dir
    # 名字先验，**先于拼路径**：``accounts_path`` 自己也会抛，但那是 ``ValueError``
    # —— 从处理器里穿出去之后，调用方拿到的是「Error executing tool list_accounts」
    # 而**没有消息**。理由与那条判据本身都写在 ``_account_list_name`` 上。
    checked = _account_list_name(name)
    path = accounts_path(inputs_dir, checked)

    try:
        accounts = cohort_use_cases.load_accounts(path)
        # 身份判据走**派发那条路径自己**的函数（``known_account_dossiers`` +
        # ``identity_refusal``），不在这里重写一遍：两处各判一遍的话，各自改动
        # 的那一次不会报错，只会让「列表说能跑」与「派发拒了」同时成立。
        dossiers = known_account_dossiers(inputs_dir, checked)
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc

    # 只取第一行：``identity_refusal`` 的正文是给 operator 读的（写明了缺哪一格依据、
    # 以及拿它去检索会怎样），逐行转发会把一份 12 行的清单变成一屏说明。第一行是
    # 那句话本身；完整正文在派发拒绝时照旧给得出来。
    refused: dict[str, str] = {}
    for dossier in dossiers:
        reason = identity_refusal(dossier)
        refused[dossier.account_id] = reason.splitlines()[0] if reason else ""

    store = read_store(settings)
    rows: list[dict[str, Any]] = []
    for ordinal, account in enumerate(accounts):
        # ``account_id`` 已经在装载期过了 ``check_canonical_id``
        # （``AccountRecord._account_id_is_canonical``），所以这里可以直接拿它去
        # 查库 —— 它是「读到的就是写下的」那一条的前提，不是一次猜测。
        if store is None:
            counts: dict[str, int | None] = {
                "route_count": None,
                "opportunity_count": None,
                "evidence_count": None,
            }
        else:
            counts = {
                "route_count": len(store.list_routes(account.account_id)),
                "opportunity_count": len(store.list_opportunities(account.account_id)),
                "evidence_count": len(store.list_evidence(account.account_id)),
            }
        rows.append(
            _account_row(
                ordinal=ordinal,
                account=account,
                bundle=bundle,
                counts=counts,
                refusal=refused.get(account.account_id, ""),
            )
        )

    # 汇总按**整份清单**算（理由见 docstring），所以它必须在那一次截断之前建。
    by_cohort: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in rows:
        by_cohort[row["cohort"]] = by_cohort.get(row["cohort"], 0) + 1
        status = row["grade"]["status"]
        by_status[status] = by_status.get(status, 0) + 1

    try:
        kept, decision = apply_cap(rows, limit, key=lambda row: row["account_id"])
    except ConfigError as exc:
        # 负数上限（``apply_cap`` 失败关闭：一个负数不是一个「很小的上限」，是
        # 调用方算错了）是**调用方给错了**，所以它要走工具结果那条通道。
        # 让它从处理器里抛出去，MCP 侧看到的是一次内部错误 —— 与本仓
        # 「失败是一个结果而不是崩溃」相反，而且那句话里没有「上限」两个字。
        raise ToolError(str(exc)) from exc

    return {
        "input_ref": checked,
        "list_path": str(path),
        "account_count": len(rows),
        "accounts": kept,
        "summary": {
            "by_cohort": by_cohort,
            "by_status": by_status,
            "accounts_with_unchecked_rules": sum(
                1 for row in rows if row["grade"]["unchecked"]
            ),
            "dispatchable": sum(1 for row in rows if not row["identity_refusal"]),
        },
        "plan": {
            "expected_total": cohort_use_cases.DEFAULT_PLAN.total,
            "problems": cohort_use_cases.validate_plan(list(accounts)),
        },
        "truncation": decision.as_dict(),
        "store_present": store is not None,
        "note": (
            "accounts 按**文件顺序**排列，ordinal 就是它 —— 那个序号决定 "
            "work_item_id，所以重排清单会编译出另一批工作。截断的是这一列，"
            "不是清单：summary 与 account_count 覆盖整份清单。\n\n"
            f"stored 三项读的是 {settings.database_path.name}。"
            + (
                "库不存在，所以三项都是 null —— **不是 0**：「还没建库」与「建了库、"
                "这个账号什么都没跑」是两件事，写成 0 会让一个全新的部署看起来像"
                "一个跑过而颗粒无收的部署。"
                if store is None
                else "以 account_id 为键，它已经在装载期过了规范形式校验。"
            )
            + "\n\n"
            "grade.unchecked 逐条列出**没有判**的排除规则，不折成一个数："
            "它非空不蕴含放行，说的是「这些规则没判」（引擎手上那个事实未确认）。"
            "它与『排到结论为止每一条都判了、一条都没命中』是两句不同的话，"
            "本仓今天还说不出口后者。\n\n"
            "identity_refusal 逐行给出**派发那条路径自己**的判据：非空 = 这一行没有"
            "可用来做身份发现的依据（既没有名字、也没有域名），拿它去检索会把预算"
            "花在同名物上。整份清单里只要有一行非空，派发就会**整份**拒绝 —— "
            "所以这一格非空的那些行要先补齐。\n\n"
            "plan 是 Demo 队列构成（10/10/10，``cohorts.DEFAULT_PLAN``）的核对，"
            "不是产品环的规则：``problems`` 非空时读作「这份清单与那个构成不一致」，"
            "而不是「这份清单不能用」。\n\n"
            "notes 没有被转发：它是自由文本，本回执没有任何一格需要它。"
        ),
    }


# ----------------------------------------------------------------------
# 账号池（§6）：remember_accounts（账号 → 池）与 list_account_pool（池 → 读数）
#
# 与上面那三段（资产池 / 责任关系表 / 账号**名单**）并列，而它与 ``list_accounts``
# 是**两件事**：那一条读的是 operator 维护的清单文件（``inputs_dir/<name>.yaml``），
# 这一条读的是研究产出落盘的账号池。两条都留着，名字上分得开 —— 一段里两个都叫
# 「账号池」，读的人就没有办法知道自己手上那一份是哪一个。
# ----------------------------------------------------------------------
def _account_pool_row(
    account: MarketAccount, *, linked_assets: Sequence[Mapping[str, Any]] | None
) -> dict[str, Any]:
    """一行账号池的读数。

    ## ``country`` 的三态在这一行上原样保留

    ``None`` = 还没查、``""`` = 查了、是没有、非空 = 查到了什么。它不是排版问题：
    「还没查」与「查过没有」的下一步相反（一个是去查，一个是别查了），而这三个取值
    只有在 ``None`` 被写成 ``""`` 的时候才会在纸上长得一样。

    ## ``status`` 是**记下来的那一次判定**，不是「此刻拿现行规则重跑一遍」

    §6 说这一格是逐 Scene 的评估，而它在这条路径上只有一个来源：写进来时调用方给的
    那个值。重算一遍是 §6 另一条轴上的事（``grade_account`` 那条），两条轴互不决定 ——
    在这一行上把两者合成一格，读的人就分不出「当时判成 hold」与「今天按现行规则判成
    hold」。

    ## ``linked_assets`` 是**接起来的**，本行上不存第二份

    ``domain/account.py`` 上没有「这个账号管哪几块场地」这一格：那是 §5 的关系表答的
    事，存两处的清单迟早有一处是旧的。所以这里取不到时是 ``null`` ——
    「这个部署没有接关系表 / 场地池」，**不是**空列表。空列表说的是「这个账号一块场地
    都不管」，而那是 §5 明确不许从「没有行」推出来的结论。

    ## 证据给 **id 列表**，不只是条数

    与 ``_relation_row`` 逐字同一条：读者的下一步**就是**去看那几条，而 ``get_evidence``
    收的正是这些 id。条数由 ``evidence_count`` 顺带给出。

    ## ``last_verified_at`` 为空是「从那以后没再核实过」

    **不是**「核实过一次、在时间零点」—— 与 ``_asset_row`` / ``_relation_row`` 同一条。
    """
    return {
        "account_id": account.account_id,
        "organisation": account.organisation,
        "aliases": list(account.aliases),
        "domains": list(account.domains),
        "addresses": list(account.addresses),
        "country": account.country,
        "status": account.status.value,
        "evidence_ids": list(account.evidence_ids),
        "evidence_count": len(account.evidence_ids),
        "first_seen_at": _instant(account.first_seen_at),
        "last_verified_at": (
            None if account.last_verified_at is None else _instant(account.last_verified_at)
        ),
        "linked_assets": None if linked_assets is None else [dict(row) for row in linked_assets],
    }


def _account_pool_summary(
    accounts: Sequence[MarketAccount], *, accounts_without_a_relation: int | None
) -> dict[str, Any]:
    """账号池整体长什么样。**在截断之前算**，理由与 ``_pool_summary`` 逐字相同。

    ## ``by_status`` 把七个成员都列出来，零个也列

    ``AccountStatus`` 是一个**闭集**（七个成员），所以「这一种一个都没有」是一个
    **测出来的**事实，不是没有数据。省掉零的那几行，读的人就分不出「这个市场没有
    被搁置的账号」与「这一栏我今天没算」—— 而那两件事的下一步相反。

    它数的是**每一行记下来的那一次判定**，不是拿现行规则重算一遍的结果：
    重算属于另一条轴（见 ``_account_pool_row`` 的 docstring）。

    ## ``accounts_never_re_verified`` 数的是 ``last_verified_at`` 还空着的行

    它是**复核队列**，不是「有多少个账号过时了」：这一层不知道账号该按什么周期复核
    （那是 §11 的事），它只知道哪些行从那以后一次都没核实过。

    ## ``accounts_without_a_relation`` 缺失时给 ``None`` 与理由，不给 0

    它是池子与 §5 那张表**合起来**才答得了的问题，所以按 id 读的那一次、以及关系表
    没接线的部署都答不出来。写 0 会把「我没算」说成「每一个账号都有一块场地挂着」——
    而它恰好是这个数唯一会被用错的方向：它是**待办队列**（还有哪些账号没有挂上场地），
    不是「这些账号不管任何场地」。
    """
    by_status: dict[str, int] = {member.value: 0 for member in AccountStatus}
    for account in accounts:
        by_status[account.status.value] += 1

    return {
        "by_status": by_status,
        "accounts_never_re_verified": sum(
            1 for account in accounts if account.last_verified_at is None
        ),
        "accounts_without_a_relation": accounts_without_a_relation,
        "summary_gap": (
            ""
            if accounts_without_a_relation is not None
            else "accounts_without_a_relation 只有在**不设 account_id 过滤**、且关系表"
            "接过线的那一次读里才算得出来：它数的是整个池子里在 §5 那张表上一条关系都"
            "没有的账号。这里给 null 而不是 0 —— 0 会被读成「每一个账号都挂上了场地」。"
        ),
    }


def _account_pool_outcome(outcome: AccountOutcome) -> dict[str, Any]:
    """一行账号的去向。**四类都报出来**，写法与 ``_relation_outcome`` 逐字相同。

    ``status`` 与 ``evidence_count`` 两格只在**写下来了**（``written`` / ``folded``）的
    行上有值，其余是 ``null`` 加理由（理由就在 ``reason`` 那一格）：一个没写的行没有
    一个「它的判定是什么」的答案，而 ``discovered`` 会是那个看起来像答案的错答案 ——
    它读起来像「这个账号还没有判定」，而事实是「这一行根本没有落进池子」。

    ``reason`` 在顺利写入的行上是空串：那一格说的是「为什么没写」或「折进了第几行」，
    一次顺利的写入没有理由要说 —— 给它填一句「成功」，读的人就得去分辨「有理由」与
    「有内容」两件事。
    """
    return {
        "ordinal": outcome.ordinal,
        "account_id": outcome.account_id,
        "organisation": outcome.organisation,
        "disposition": outcome.disposition.value,
        "reason": outcome.reason,
        "status": None if outcome.row is None else outcome.row.status.value,
        "evidence_count": None if outcome.row is None else len(outcome.row.evidence_ids),
    }


def _remember_accounts(
    *,
    settings: Settings,
    accounts: Callable[[], AccountPoolStore] | None,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """把一批账号记进账号池，**逐行给出去向**。

    ## 三个「不由调用方给」的格子

    * **``workspace``** —— 由本部署的 profile 定。能填的话，一次调用就能把行写进另一
      个 Scene，而那两个 Scene 的行住在同一个库文件里；§6 的「客观事实可以跨 Scene
      复用」说的是第二个 Scene 写**它自己**那一行，不是一次调用替它写。
    * **``first_seen_at``** —— 由服务端取（``utc_now``）。一个能填时刻的接口能让调用方
      把一个刚发现的账号声称成三年前就记下的，而报告上什么都看不出来。
    * **``last_verified_at``** —— 这一次调用**不给**它任何值。记下一个账号不是重新核实了
      它（与 ``promote_receipt`` / ``remember_responsibility`` 同一条纪律）：填上它，
      「这个账号多久没复核了」这个只在池子建起来之后才有意义的问句，会在第一次记录那天
      就得到「刚刚复核过」，而下一轮维护会把它们整批跳过。

    ## 没有 ``venue_asset_id``，而这不是漏掉的

    ``remember_responsibility`` 有它，因为一条责任关系说的是「谁对**哪块**场地负责」。
    一个账号不挂在某一块场地上（它可以同时管十块），而「它管哪几块」是 §5 那张表的事，
    所以这一条的作用域参数只有 ``workspace``。

    ## 为什么每一步都要先有一条证据

    每一行的 ``evidence_ids`` 都要在库里解得开（``remember_accounts`` 逐行核）。
    所以库里一条证据都没有时，**每一行**都会落在 ``evidence_unknown`` 上 —— 库文件
    还不存在就更是如此。那不是一个可以开始的状态，所以这一条在这里就停下并说清处置：
    先让证据落盘（研究工作产出证据），再来记账号。

    ## 逐行判，一行写不进去不牵连其余

    见 :func:`~marketops.application.accounts.remember_accounts`：一行坏行是一条**去向**
    （``rejected`` / ``evidence_unknown`` / ``folded``），不是整次失败。所以这一条工具
    的回执里四类去向都要报出来 —— 一份 6 行的记录只落了 2 行，读的人要能立刻看出另外
    4 行去了哪，而不是从「written: 2」去猜。
    """
    if accounts is None:
        raise ToolError(
            "本服务实例没有接账号池，所以这批账号记不进去。\n"
            "这是一条**接线事实**（组合根没有构造账号池），不是「这一批里没有可记的"
            "账号」—— 后者的处置是去研究，前者是去接线。"
        )
    if not settings.database_path.is_file():
        raise ToolError(
            f"还没有写过任何东西（{settings.database_path.name} 尚未创建）：这一条"
            "每一步都要拿证据引用去库里核对，而库里一条证据都没有时**每一行**都会落在"
            "`evidence_unknown` 上。\n"
            "这是一个正常答案，不是写不进去 —— 先让证据落过盘（研究跑出来的证据），"
            "再来记账号。"
        )

    store = accounts()
    # 证据库与账号池是**同一个库文件**（都是 ``settings.database_path``），而上面那条
    # 分支已经排除了「库还没建」，所以这里不可能是 ``None`` —— ``read_store`` 的
    # ``None`` 只有一个意思（库文件不在）。用它而不是再收一个工厂参数：证据库的位置在
    # 本仓只有一处（``tools.read_store``），多一个参数就是那一处的第二份拷贝。
    evidence = read_store(settings)
    assert evidence is not None  # noqa: S101 - 见上：上面那条分支已经排除了另一种可能

    try:
        # 记之前先量一次池子的大小。两次读之间**没有**锁，所以下面那两个数是池子在两个
        # 瞬间的**规模**，不是「这一次调用增加了多少」—— 与 ``_promote_receipt`` /
        # ``_remember_responsibility`` 逐字同一条。
        before = len(store.list_accounts())
        outcomes = remember_accounts(
            rows,
            store=store,
            evidence=evidence,
            workspace=settings.profile,
            seen_at=utc_now(),
        )
        after = len(store.list_accounts())
    except StoreError as exc:
        raise _store_failure(exc) from exc

    by_disposition = {member.value: 0 for member in AccountDisposition}
    for outcome in outcomes:
        by_disposition[outcome.disposition.value] += 1
    return {
        "workspace": settings.profile,
        "outcomes": [_account_pool_outcome(outcome) for outcome in outcomes],
        "by_disposition": by_disposition,
        "account_count": len(outcomes),
        "accounts_in_the_pool_before": before,
        "accounts_in_the_pool_after": after,
        "note": (
            "逐行判，一行写不进去不牵连其余：rejected 是这一行本身构造不出一个能写下来的"
            "账号（判定不在那七个里、主语是空的、一条证据都没有），evidence_unknown 是它"
            "引用的证据在这个库里解不开，folded 是同一批里前面某一行已经说过这个账号"
            "（第几行在 reason 里）。四种去向都在 by_disposition 里，一份 6 行的记录只"
            "落了 2 行时，另外 4 行去了哪要能一眼看出来。\n\n"
            "written / folded 两类的 status 与 evidence_count 是**池子里现在那一行**的"
            "值，不是交上来的那一份：折叠之后证据是并集、主语是第一次记下的那个。\n\n"
            "status 是**记下来的那一次判定**（discovered = 还没有判定），不是拿现行规则"
            "重算一遍：重算属于另一条轴（准入判定），两条轴互不决定。\n\n"
            "accounts_in_the_pool_before / after 是池子在两个瞬间的规模，不是这一次调用"
            "的增量：两次读之间没有锁，并发的 worker 期间别的行也会算进那个差。\n\n"
            "last_verified_at 写下来仍然是空的 —— **记下一个账号不是重新核实了它**"
            "（``run_maintenance`` 那条「本地记录也算观测」在这里不适用，理由与 "
            "promote_receipt 那一处逐字相同）。填上它，「这个账号多久"
            "没复核了」会在第一次记录那天就得到「刚刚复核过」，而下一轮维护会整批跳过。\n\n"
            "读回来用 `list_account_pool`（池 → 读数）；`list_accounts` 读的是另一件事"
            "（operator 维护的那份清单文件）。"
        ),
    }


def _list_account_pool(
    *,
    settings: Settings,
    accounts: Callable[[], AccountPoolStore] | None,
    assets: Callable[[], VenueAssetStore] | None,
    relations: Callable[[], AccountVenueRelationStore] | None,
    account_id: str | None,
    limit: int | None,
) -> dict[str, Any]:
    """读这个市场的账号池：§6 的那张表，外加它在 §5 那张表上挂着的场地。

    ## 读一次**不建库**

    与 ``list_assets`` / ``list_responsibilities`` 逐字同一条：库文件不存在时这一格是
    ``no_store``、``accounts`` 是空列表 —— 而那不是「这个市场一个账号都没有」。后者是
    一句业务结论，前者说的是「还没有任何东西落进过这个库」。

    ## 没接线时是 ``not_wired``，**不是**一次拒绝

    与 ``list_assets`` / ``list_responsibilities`` 逐字同一条（读工具的这一族只有这一
    种形状；写工具那一族反过来 —— ``remember_accounts`` 没接线就是一次拒绝）。在这
    一条分支上 ``lookup.found`` 是 ``null`` 而**不是** ``false``：问的是「池子里有这
    个账号吗」，而池子不在**这个**部署里 —— ``false`` 会说成「这个市场没有它」，
    那是另一句话。

    ## ``account_id`` 给一个就是 §14 说的 ``get_account``

    同一个行构造器、同一条读法，只是多了过滤 —— 按 id 读**走端口上那个**
    ``get_account``（而不是把整个池子读进来再筛），所以一行与多行的形状必然一致。
    两条读法各建一次行，就是同一句话有两个出处；而分开两条工具（``list_`` 与
    ``get_``）在 §6 这张表上也没有第二个问题要答（§5 那边两个方向都被问到，是因为
    「谁负责这块」与「这个账号管哪些」是两个问题）。池子接上了而查不到时是
    ``lookup.found=false`` 与一个空列表，不是一条错误：**「池子里没有这个账号」是
    一个正常答案**，而它与「读不出来」的处置不同。

    ## 关联场地那一栏接不起来时是 ``null``，不是空列表

    它要 §5 的关系表（谁和这块场地有关）**与**资产池（把派生句柄翻成名字）两样都在。
    少一样时 ``linked_assets`` 是 ``null`` 且 ``linked_assets_gap`` 说明少了哪一样 ——
    一份少了那一栏的报告**不比**一份写着空列表的报告难读，而空列表会被读成
    「这个账号一块场地都不管」。
    """
    missing_stores = [
        name
        for name, wired in (("场地资产池", assets), ("责任关系表", relations))
        if wired is None
    ]
    linked_gap = (
        ""
        if not missing_stores
        else (
            f"linked_assets 这一栏要的 {'、'.join(missing_stores)} 本实例没有接："
            "那一栏是接起来的（账号行上不存第二份），所以这里给 null 而不是空列表 —— "
            "空列表会被读成「这个账号一块场地都不管」，而那是 §5 不许从「没有行」"
            "推出来的结论。"
        )
    )

    def capped(
        account_rows: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """截断只在这一处发生 —— 三条分支走同一句，理由与 ``_list_assets`` 逐字相同。"""
        try:
            kept, decision = apply_cap(
                account_rows, limit, key=lambda row: row["account_id"]
            )
        except ConfigError as exc:
            raise ToolError(str(exc)) from exc
        return kept, decision.as_dict()

    if accounts is None:
        return {
            "accounts": [],
            "account_count": 0,
            "pool_state": _SCENE_RECORD_NOT_WIRED,
            "workspace": settings.profile,
            # ``found`` 是 ``null`` 而不是 ``false``：池子不在**这个**部署里，而
            # 「这个市场没有这个账号」是另一句话（见上面那段 docstring）。
            "lookup": None if account_id is None else {"account_id": account_id, "found": None},
            "linked_assets_gap": linked_gap,
            "summary": None,
            "truncation": capped([])[1],
            "note": (
                "这个服务实例没有接账号池存储，所以这个市场研究出过哪些账号读不出来。"
                "这不是「一个账号都没有」—— 前者的处置是接线，后者是去研究并把研究出的"
                "账号记下来（`remember_accounts`）。"
            ),
        }
    if not settings.database_path.is_file():
        return {
            "accounts": [],
            "account_count": 0,
            "pool_state": _SCENE_RECORD_NO_STORE,
            "workspace": settings.profile,
            "lookup": None if account_id is None else {"account_id": account_id, "found": False},
            "linked_assets_gap": linked_gap,
            "summary": None,
            "truncation": capped([])[1],
            "note": (
                f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
                "所以这个部署还没有任何一行账号落过盘。这是一个正常答案，不是读不出来 ——"
                "账号由 `remember_accounts` 记下。"
            ),
        }

    store = accounts()
    try:
        records = list(store.list_accounts())
        found: bool | None = None
        if account_id is not None:
            # 按 id 读**走端口上那一个读入口**，不是在读回来的整个池子里筛一遍：
            # 端口上那条路今天在生产路径上还没有别的读者（``ports/pool.py`` 的
            # ``get_account``），而一条没有读者的读入口在盘上与「已经做完了」长得一样。
            one = store.get_account(account_id)
            found = one is not None
            records = [] if one is None else [one]

        linked: dict[str, list[dict[str, Any]]] | None = None
        accounts_without_a_relation: int | None = None
        if not missing_stores:
            relation_store = relations()  # type: ignore[misc] - 上面已排除 None
            asset_store = assets()  # type: ignore[misc] - 同上
            names: dict[str, str | None] = {}
            linked = {}
            # 不设过滤时把整张表读一次再按账号索引：一张几百行的表读一次是一条查询，
            # 而逐行去问（``relations_for_account``）是几百条 —— 那正是本工具在这一栏上
            # 唯一会慢的地方。设了过滤时反过来：一行就是一行，读一次那个账号的。
            if account_id is not None:
                pairs: list[tuple[str, AccountVenueRelation]] = [
                    (account_id, row) for row in relation_store.relations_for_account(account_id)
                ]
            else:
                pairs = [(row.account_id, row) for row in relation_store.list_relations()]
            for key, relation in pairs:
                if relation.venue_asset_id not in names:
                    asset = asset_store.get_asset(relation.venue_asset_id)
                    names[relation.venue_asset_id] = None if asset is None else asset.name
                linked.setdefault(key, []).append(
                    {
                        "venue_asset_id": relation.venue_asset_id,
                        "venue_name": names[relation.venue_asset_id],
                        "relation": relation.relation.value,
                        "evidence_ids": list(relation.evidence_ids),
                    }
                )
            if account_id is None:
                # 「池子里有多少个账号一条关系都没有」是整个池子的事，所以只有不设
                # 过滤的那一次读才去算它（见 ``_account_pool_summary``）。
                accounts_without_a_relation = sum(
                    1 for record in records if record.account_id not in linked
                )
    except StoreError as exc:
        raise _store_failure(exc) from exc

    rows = [
        _account_pool_row(
            record, linked_assets=None if linked is None else linked.get(record.account_id, [])
        )
        for record in records
    ]
    kept, truncation = capped(rows)
    return {
        "accounts": kept,
        "account_count": len(rows),
        "pool_state": _SCENE_RECORD_PRESENT,
        "workspace": settings.profile,
        "lookup": None if account_id is None else {"account_id": account_id, "found": bool(found)},
        "linked_assets_gap": linked_gap,
        "summary": _account_pool_summary(
            records, accounts_without_a_relation=accounts_without_a_relation
        ),
        "truncation": truncation,
        "note": (
            "按 account_id 升序排列（两个实现都这么排，顺序不是这一次调用的产物）。\n\n"
            "status 是**记下来的那一次判定**（§6 逐 Scene 的评估），不是拿现行规则重算"
            "一遍 —— 另有一条轴（准入判定）管那个，两条轴互不决定。discovered 是"
            "「还没有判定」，不是七种判定里的一种。\n\n"
            "country 有三态：null = 还没查、\"\" = 查了、是没有、非空 = 查到了什么。"
            "「还没查」与「查过没有」的下一步相反，所以这一格不把 null 写成空串。\n\n"
            "linked_assets 是 §5 那张表接过来的（账号行上不存第二份）：同一个账号在"
            "同一块场地上可以同时有几行（owns / operates / maintains / …），§5 要求这"
            "几种并存、不强制单选。关系本身只有一条时它也会是一条。\n\n"
            "last_verified_at 为空是「从那以后没有重新核实过」，不是「核实过一次在时间"
            "零点」：这一层不知道该按什么周期复核账号（那是 §11 的事）。\n\n"
            "summary 覆盖**整个池子**（在截断之前算）：一份 300 行的池子在 limit=200 下"
            "仍然是一个 300 行的池子，被砍掉的 100 行由 truncation 报出来。"
            "accounts_without_a_relation 只有在不设 account_id 过滤、且关系表接过线的"
            "那一次读里才有值（其余是 null 加理由）—— 它是**待办队列**，不是「这些账号"
            "不管任何场地」。\n\n"
            f"workspace 是本部署的 Scene（{settings.profile}）：本工具只列属于它的行。"
            "库里若有别的 workspace 的行，它们不属于这个部署，这里读不到（那是一条错误，"
            "不是一个空结果）。"
        ),
    }


def _due_row(due: DueWork) -> dict[str, Any]:
    """一格到期工作。``dedupe_key`` 在里面，因为它是这一格的**幂等身份**。

    ``source_ref`` 也转发：``what_is_due`` 在信号制的工作上把它填成**信号的理由**
    （``domain/maintenance.py`` 逐字如此），而「为什么这一格会被提前拉过来」正是
    报告要回答的问题。周期制的工作上它是上一格的来源引用，位置同形。
    """
    return {
        "dedupe_key": due.dedupe_key,
        "scene_version": due.scene_version,
        "subject_kind": due.subject_kind,
        "subject_id": due.subject_id,
        "aspect": due.aspect.value,
        "reason": due.reason.value,
        "period": due.period,
        "due_at": _instant(due.due_at),
        # ``None`` 是「从来没观测过」，不是 epoch（``DueWork`` 逐字如此）。
        "last_observed_at": (
            _instant(due.last_observed_at) if due.last_observed_at is not None else None
        ),
        "source_ref": due.source_ref,
    }


def _state_row(state: AspectState) -> dict[str, Any]:
    """一格的新鲜度（§11 的五个字段）。**没到期的格子也有这一行** —— 见
    ``_get_ops_report`` 的 ``freshness`` 那一节。

    与 ``_due_row`` 的区别值得点一句：那一边的行是**一件工作**（有幂等键、有周期），
    这一边的行是**一格的状态**（下次什么时候看、为什么是旧的）。同一格可能在工作
    那侧有两行（时限一次、信号一次），在这一侧永远只有一行。
    """
    return {
        "subject_kind": state.subject_kind,
        "subject_id": state.subject_id,
        "aspect": state.aspect.value,
        "status": state.status.value,
        # ``None`` 是「从来没核过」，不是 epoch、也不是 0（``AspectState`` 逐字如此）。
        "last_verified_at": (
            _instant(state.last_verified_at) if state.last_verified_at is not None else None
        ),
        # 从未核过时它同样是 ``None``：那一格没有过任何节奏，于是没有「下一次」可写。
        "next_due_at": (
            _instant(state.next_due_at) if state.next_due_at is not None else None
        ),
        "stale_reason": (
            None if state.stale_reason is None else state.stale_reason.value
        ),
    }


def _claim_row(claim: MaintenanceClaim) -> dict[str, Any]:
    """一条回执。``attempt`` 与 ``state`` 都在里面（见 ``MaintenanceClaim``）。"""
    return {
        "dedupe_key": claim.dedupe_key,
        "scene_version": claim.scene_version,
        "subject_kind": claim.subject_kind,
        "subject_id": claim.subject_id,
        "aspect": claim.aspect.value,
        "reason": claim.reason.value,
        "period": claim.period,
        "state": claim.state.value,
        "attempt": claim.attempt,
        "claimed_at": _instant(claim.claimed_at),
        "finished_at": _instant(claim.finished_at) if claim.finished_at is not None else None,
        "result_ref": claim.result_ref,
        "failure": claim.failure,
    }


#: 产出读不出来时的理由。与 ``application/refresh.py`` 的分工是：那边保证**写**下去
#: 的那一份自洽（摘要与内容相符），这边只负责把读不出来的事实说出来。
_OUTCOME_UNREADABLE = (
    "result_ref 指向的那一份产出读不出来（不在盘上 / 不是 JSON / 摘要与文件名对不上"
    "/ 内容与它自己声明的摘要对不上）。这一格的产出无法复核 —— **它不是「0 个缺口」**："
    "把一次损坏读成一次好消息，正是本仓 §3.4 要挡的那件事。"
)


def _outcome_row(claim: MaintenanceClaim) -> dict[str, Any]:
    """从 ``result_ref`` 读回那一格的产出，**按它自己说是哪一种**来报。

    读**盘上那一份**而不是内存里刚构造的：``result_ref`` 的意义就是「产出在这里，
    自己去看」。一个只在内存里对得上的引用于事后没有任何用。

    ## 为什么不能只按 aspect 认

    B22 之后同一个目录下住着两种产出（盘点与复审记录），而 ``source_supply`` 那一节
    数的是「主体手上有没有可绑定的来源」—— 一份复审记录答不了那个问题（它说的是
    「我们记得的那几页现在还是不是那样」）。反过来，把复审记录按盘点读会在摘要核对
    那一步红，于是那些格子会以 ``readable=false`` 出现在 ``subjects_unreadable`` 里
    —— **一次真实的重查被读成一次损坏**。所以这一格的分派读的是记录**自己**写的
    ``kind``（:func:`~marketops.application.refresh.load_outcome`），而不是回执上的
    aspect：aspect 说的是「这一格问的是什么」，而记录说的是「这一份是什么」，两者
    在 B22 之后不再一一对应。

    不认识的 ``kind`` 报 ``readable=false`` 并把它**逐字**写出来。那是「有人手写过
    一份东西」，与「损坏」不同，但处置相同：不许当成某一种来读。
    """
    if not claim.result_ref:
        return {"readable": False, "reason": "这一格没有产出引用（result_ref 为空）。"}
    try:
        kind, payload = refresh_use_cases.load_outcome(Path(claim.result_ref))
    except ConfigError as exc:
        return {"readable": False, "reason": f"{_OUTCOME_UNREADABLE}\n（{exc}）"}
    if kind == refresh_use_cases.CENSUS_KIND:
        return {
            "readable": True,
            "kind": kind,
            "path": claim.result_ref,
            "store_present": payload.get("store_present"),
            "rows": payload.get("rows"),
            "kinds": payload.get("kinds"),
            "bindable_kinds": payload.get("bindable_kinds"),
            "newest_fetched_at": payload.get("newest_fetched_at"),
            "new_source_gaps": payload.get("new_source_gaps"),
            # 未测的那一项**带理由**转发（``application/refresh.py`` 里那一句是唯一
            # 的定义处）。折成 ``null`` 而不带话，读的人只能猜是「没测」还是「漏了」。
            "not_measured": payload.get("not_measured", {}),
        }
    if kind == refresh_use_cases.RECHECK_KIND:
        pages = payload.get("pages", [])
        return {
            "readable": True,
            "kind": kind,
            "path": claim.result_ref,
            "state": payload.get("state"),
            "page_set_size": payload.get("page_set_size"),
            "examined": payload.get("examined"),
            # 变了的页**逐页点出来**：判据是这一页的 ``cause`` 非空，与
            # ``application/refresh.py::PageRecheck.changed`` 逐字同一条。
            #
            # 这一格原先转发的是 ``payload.get("changed_pages")``，而记录里**没有**
            # 这个键（``RecheckRecord.payload`` 里只有 pages / page_set_size /
            # examined）—— 于是它在每一份回执上恒为 ``null``：一个看起来像测量的
            # 空位，读的人分不出「这一轮没有一页变」与「这一格没有人算」。记录自己
            # 说的是逐页那一份里的 ``cause``（``error`` 与它并列，而两者分开正是
            # 「世界变了」与「这一页没看成」要分得开的那条判据），所以这里读它。
            "changed_pages": [page["url"] for page in pages if page.get("cause")],
            "reused_evidence_ids": payload.get("reused_evidence_ids", []),
            "evidence_ids": payload.get("evidence_ids", []),
            "policy_version": payload.get("policy_version", ""),
            "note": payload.get("note", ""),
            "pages": pages,
        }
    return {
        "readable": False,
        "kind": kind,
        "reason": (
            f"result_ref 指向的那一份产出自己写着 kind={kind!r}，而本部署只认得"
            f" {refresh_use_cases.CENSUS_KIND!r} 与 {refresh_use_cases.RECHECK_KIND!r}。\n"
            "它不是损坏（摘要核对通过了），但它**是哪一种**没有定义 —— 当成某一种来读"
            "会替它下一个它没下的结论。"
        ),
    }


def _coverage_receipt(
    *,
    plan: RefreshPlan | None,
    acquisition: DocumentAcquisition | None,
    router: DocumentRouter | None,
    documents: Callable[[str], RunBudget] | None,
) -> dict[str, Any]:
    """**承诺 / 实现 / 缺口**三个数一起给，外加缺哪几样接线。

    §11 逐字：「至少把 R2 产品承诺中的方面全列为 required，而不是通过过滤掉未实现
    aspect 得到全绿」。分母（``REQUIRED_ASPECTS``）住在域层，与实现无关；分子
    （``REFRESHABLE_ASPECTS``）是本部署**声明**得出来的；差集由 ``uncovered_aspects``
    算出来，**不在这里手写** —— 手写的那一份在分子变大的那天不会跟着变，而它看起来
    仍然正常（少报的正是刚刚接上的那一面）。

    三个数**一起**给，任何一个单独给都答不了这一格的问题：只有前两个读不出「差了几面」，
    只有后一个读不出「分母是多少」。四个面今天没接，这是一份**欠账**，不是一个应当被
    折平的数 —— 所以它逐面列出来，也带计数。

    ## ``plan`` 为什么不在 ``missing_wiring`` 里，尽管它算四样接线之一

    计划不是**部署**的性质：它来自某一版 Scene（``version.refresh``）。「这个场景还没有
    生效版本」与「这个部署没接计划」是两句完全不同的话，而后者这一版里构造不出来 ——
    两个调用方都是当场从生效版本上取的（这也是 ``effective_version`` 存在的理由）。
    所以这一格报的是 ``plan_present``，而 ``missing_wiring`` 只数**部署**的那三样。
    """
    missing = refresh_use_cases.missing_wiring(
        plan=plan, acquisition=acquisition, router=router, documents=documents
    )
    deployment_missing = tuple(name for name in missing if name != "plan")
    return {
        "required_aspects": [aspect.value for aspect in REQUIRED_ASPECTS],
        "required_count": len(REQUIRED_ASPECTS),
        "implemented_aspects": sorted(
            aspect.value for aspect in refresh_use_cases.REFRESHABLE_ASPECTS
        ),
        "implemented_count": len(refresh_use_cases.REFRESHABLE_ASPECTS),
        # 实现得出来的那三面各自由哪一条复核回答。**分成两份**而不是在派发处写一个
        # ``if``：它答的是「这一面会不会联网、会不会花钱、要不要接线」。
        "census_backed_aspects": sorted(
            aspect.value for aspect in refresh_use_cases.CENSUS_BACKED_ASPECTS
        ),
        "page_backed_aspects": sorted(
            aspect.value for aspect in refresh_use_cases.PAGE_BACKED_ASPECTS
        ),
        "coverage_gaps": [aspect.value for aspect in refresh_use_cases.COVERAGE_GAPS],
        "coverage_gap_count": len(refresh_use_cases.COVERAGE_GAPS),
        # 缺哪几样接线**全列**（不是只报第一个）：逐样发现会让人来回改三轮，而这是一条
        # 待办，它应当一次说清。缺一样就不发请求（见 ``recheck_pages``）。
        "missing_wiring": list(deployment_missing),
        "wiring_meaning": {
            name: refresh_use_cases.WIRING_MEANING[name] for name in deployment_missing
        },
        "plan_present": plan is not None,
    }


def _scene_record(store: SceneStore, scene_id: str) -> SceneRecord:
    """库里那一份记录，或一条说得出「下一步做什么」的拒绝。

    与 ``add_scene_version`` / ``get_scene`` 用的是同一组话：**记录不在**有两种
    情形（库还没建、库里没有这个场景），而两者的处置不同（一个去立项、一个去查
    为什么库被换过）。这里只说得出后者，因为走到这里时库已经被打开过。
    """
    record = store.get_record(scene_id)
    if record is None:
        raise ToolError(
            f"{scene_id!r} 还没有立项过（库里读不到记录）。\n"
            "  维护的节奏写在某一版 Scene 上，而它不存在时没有可复核的东西 —— "
            "请先用 create_scene 写下第 1 版并 activate。"
        )
    return record


def _account_list_name(name: str) -> str:
    """调用方给的清单名 → 一个**已经是不动点**的名字，或一条说得出口的拒绝。

    ## 为什么这里必须有一条判据

    ``accounts_path`` 拿这个名字去拼 ``<inputs_dir>/<名字>.yaml``，而
    ``check_canonical_id`` 为「拼出来的路径就是那个名字」这件事抛 ``ValueError``。
    那个抛出**穿不出这一层**：处理器里逃出来的裸异常经 SDK 渲染成
    ``Error executing tool <name>`` —— **一句话都没有**。调用方拿到的是一个没有
    消息的失败，能做的只有猜。本仓不接受空消息（一处失败必须说得出它凭什么）。

    ## 为什么不做规范化

    ``My List`` 改写成 ``my-list`` 读起来更友好，但它把一次拼写错误变成两个不同的
    名字静默合并 —— 与 ``check_canonical_id`` 逐字同一条理由。所以这里只翻译，
    不改写：把 ``ValueError`` 换成 ``ToolError``，并把「一份清单该放在哪」写进去。

    四条工具（``list_accounts`` 与维护那三条）共用它。各写一遍的后果与
    ``require_scene`` 那段逐字相同：四处会先分家，而分家的形态是同一个名字在
    ``list_accounts`` 上被拒、在 ``get_maintenance_plan`` 上被接受。
    """
    try:
        return check_canonical_id(name, "input_ref")
    except ValueError as exc:
        raise ToolError(
            f"name 不能当一份账号清单的文件名用：{exc}\n"
            f"  这个参数是 ``<inputs_dir>/<名字>.yaml`` 里的那个名字，服务端**不替你"
            f"改写**它 —— 需要改写的名字会与另一个名字落到同一个文件上，两个不同的"
            f"清单静默合并成一个。"
        ) from exc


def _subject_source(name: str, cohort: str | None, *, label: str) -> tuple[str, str]:
    """「这一批主体从哪来」→ ``("name" | "cohort", 值)``，**恰好一个**。

    §B12 之前只有一个来源：``name``，一份 operator 手写的 ``inputs/<name>.yaml``。
    现在多了一个 —— 一份冻结成员名单（``freeze_cohort`` 冻下来的那个 id）。

    ## 为什么是两个参数、而不是一个「名字」的前缀判别

    一个参数、按 ``coh-`` 前缀分流看起来更省事，而它有一个不会报错的失败形态：
    ``cohort_id`` 是**内容寻址**的（``coh-`` + 十六位十六进制），所以
    ``inputs/coh-3f2a….yaml`` 是一份合法的清单名 —— 两个不同的东西会争同一个字符串，
    而争输的那一个不会报错，只会读到另一份。

    （``name`` 与 ``cohort`` 都是必填是**不能**的：``cohort`` 的两个取值里有一个是
    空串，而空串正是「用最近那一份」。所以必填解决不了这件事，只能靠「恰好一个」。）

    ## 四种组合，各自说一句话

    * ``name`` 非空、``cohort`` 为 ``None`` —— 显式导入（旧路径，保留兼容）；
    * ``name`` 空、``cohort`` 是字符串 —— 一份名单的 id，``""`` = 这个 Scene
      最近冻的那一份；
    * 两个都给 —— 拒绝：这一批到底按哪一份？
    * 两个都没给 —— 拒绝：主体从哪来没有答案。

    「两个都没给」不能兜底成「用当前那一份」：那会让一次**漏传参数**变成一次
    静默的成功，而它跑的是这个 Scene 最近冻的那份名单 —— 报告上完全看不出来。
    本仓的纪律是缺一个生产者要**说得出口**（``inputs.py::_no_producer_reason``），
    这里同一句话。

    ## ``label`` 是**调用方那个参数自己的名字**

    维护那三条叫 ``name``，外联那两条叫 ``pool`` —— 同一个判定、两个参数名。
    拒绝里写死一个名字的话，另一侧的调用方会照着一条**它那儿不存在**的参数去改：
    外联的 schema 里没有 ``name``，而这条拒绝正是要告诉他改哪儿。所以名字由调用方
    传进来（``*`` 之后，没有缺省：写死一个名字的「省事」正是这一格要挡的东西）。
    """
    if name and cohort is not None:
        raise ToolError(
            f"{label} 与 cohort 两个都给了 —— 这一批主体到底按哪一份来没有答案。\n"
            "  两个是**两条不同的路**：\n"
            f"    * {label} —— 一份 operator 手写的清单"
            "（``<inputs_dir>/<名字>.yaml``）；\n"
            "    * cohort —— 一份从账号池里冻下来的成员名单（``freeze_cohort`` 的产物），"
            "空串表示「用这个 Scene 最近冻的那一份」。\n"
            "  只给一个。"
        )
    if cohort is not None:
        return ("cohort", cohort)
    if name:
        return ("name", name)
    raise ToolError(
        f"既没有给 {label}，也没有给 cohort —— 这一批的主体从哪来没有答案。\n"
        "  两条路选一条：\n"
        f"    * {label} —— 一份 operator 手写的清单"
        "（``<inputs_dir>/<名字>.yaml``）；\n"
        "    * cohort —— 一份从账号池里冻下来的成员名单；空串 = 用这个 Scene 最近"
        "冻的那一份（没冻过就先 freeze_cohort）。\n"
        "  **不兜底成「用最近那一份」**：一次漏传参数会让这个 Scene 最近冻的名单"
        "被静默跑一遍，而报告上看不出来跑的是哪一份。"
    )


def _as_of_cell(value: datetime | None) -> str | None:
    """``as_of`` 那一格：**那份冻住的东西自己的时刻**，答不上来就如实 ``None``。

    ## 它与「这一次读发生在什么时候」是两格

    读侧自己的时钟是另一格（这些回执上叫 ``at``，池子回读那一格叫 ``checked_at``），
    两者**不得**互换：拿一个冻住的时刻当「现在」，会让
    ``get_ops_report`` 的 ``freshness`` 把每一格都判成刚复核过，而报告上一切正常。
    反过来把「现在」塞进这一格，§B25 的「两个 session 读到同一个 ``as_of``」当场
    就不成立 —— 两个 session 本来就在两个时刻读。

    ## ``None`` 是答得上来的一个答案

    ``name`` 那条路读的是 operator 手写的 ``inputs/<名字>.yaml``：它没有冻结时刻，
    也没有内容寻址的 id。那里**不编一个** —— 编出来的锚会让一份没有被冻过的清单
    看起来和一份冻过的名单一样可引用，而两者的处置相反（前者去改文件，后者去查
    是哪一份名单）。
    """
    return None if value is None else _instant(value)


@dataclass(frozen=True)
class _SubjectBatch:
    """这一批主体，**连同它是按哪一份冻住的东西读出来的**。

    ## 为什么那份东西要跟主体一起走

    ``_subjects`` 原先只交一串 ``(subject_kind, subject_id)``。调用方要报
    「这一批看的是哪一份」时，只剩两条路：再读一遍存储（而两次读之间库可以被
    换过 —— 冻结名单那份 docstring 逐字写着这一条），或者自己取一次时钟。
    两条都让两份回执各自自洽，而它们真的读了两份不同的东西。

    所以这一型把那份读数**自己**一起交出去：谁读的，谁就把读到的那个对象带上来。
    ``as_of`` 与 ``cohort_id`` 都从它身上出，于是「这一批看的是哪一份」在这份回执
    里只有一个来源 —— 这正是「锚要显式传递，不在第 N 层再取一次时钟（也不再读
    一次库）」那句话的落点。

    ## ``cohort`` 为 ``None`` 是**答得上来的**

    ``name`` 那条路读的是 operator 手写的 ``inputs/<名字>.yaml``：没有被内容寻址的
    id、没有冻结时刻、随写随变。那里不编一个 —— 编出来的锚会让一份没冻过的清单
    看起来和一份冻过的名单一样可引用，而两者的处置相反（前者去改文件，后者去查
    是哪一份名单）。
    """

    subjects: tuple[tuple[str, str], ...]
    #: 这一批主体读的是哪一份冻结名单；``None`` = 这条路读的不是冻结物。
    cohort: AccountCohort | None

    @property
    def as_of(self) -> datetime | None:
        """那份读数**自己的**时刻；读的不是冻结物时是 ``None``。"""
        return None if self.cohort is None else self.cohort.created_at


def _source_receipt(
    settings: Settings,
    source: tuple[str, str],
    *,
    scene_id: str,
    cohorts: Callable[[], AccountCohortStore] | None,
    resolved: AccountCohort | None = None,
) -> dict[str, Any]:
    """「这一批主体看的是哪一份」报出来。**两条路的形状一样，内容不一样。**

    * ``name`` 那条报 ``list_path``：盘上那个文件。它与 ``list_accounts`` 读的是
      同一个文件 —— 两处各拼一遍路径的后果写在 ``accounts_path`` 的 docstring 里。
    * ``cohort`` 那条报 ``cohort_id``。它进回执的理由是**事后回查**：调用方给空串
      表示「用最近那一份」时手里并没有那个 id，而「这一批是谁」这个问题要靠它才
      答得出来。让他自己再算一次「最近那一份」是不行的 —— 两次调用之间可以又冻
      一份，那时他算出来的是另一份，而两份报告都自洽。

    ``cohort`` 那条读不到名单时**不在这里抛**：走到这里时 :func:`_subjects` 已经
    成功读过同一份名单（它先调用），所以 ``None`` 只可能是「两次读之间库被换过」
    或者 ``cohorts`` 没接线 —— 两者都不该让一份已经算好的计划在渲染回执时崩掉。
    这时 ``cohort_id`` 是 ``null`` 而 ``cohort_ref`` 照旧给出，读的人看得出差在哪。
    """
    kind, value = source
    if kind == "name":
        return {
            "kind": kind,
            "name": value,
            "list_path": str(
                accounts_path(settings.inputs_dir, _account_list_name(value))
            ),
        }
    found = None
    if resolved is not None:
        # 调用方已经把名单读出来了（它顺手还要主体），所以这一格直接用它交上来的
        # 那一份 —— **不再读第二遍**。第二遍是有害的：``""`` 那条路上它取的是
        # 「最近冻的那一份」，两次读之间又冻一份的话，这里会报出另一个 ``cohort_id``，
        # 而同一份回执上的 ``as_of`` 说的还是第一份 —— 一份回执指着两份名单，
        # 而它自己读起来完全自洽。
        found = resolved
    elif cohorts is not None:
        store = cohorts()
        found = store.get_cohort(value) if value else current_cohort(
            store, scene_id=scene_id
        )
    return {
        "kind": kind,
        "cohort_ref": value,
        "cohort_id": None if found is None else found.cohort_id,
        "source_ref_prefix": COHORT_REF_PREFIX,
    }


def _subject_batch(
    settings: Settings,
    *,
    source: tuple[str, str],
    scene_id: str,
    pool: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
) -> _SubjectBatch:
    """这一批的主体序列**连同它是按哪一份读出来的**，或一条**说得出口的**拒绝。
    **两个来源，一处派发。**

    抽成函数是因为三个维护工具（外加起草那两条）问的是同一个问题，而它们各写一遍
    try/except 的后果与 ``require_scene`` 那段逐字相同：几处会先分家，分家的形态是
    「计划里说要跑 A」与「真的跑了 B」同时成立，而两份报告都自洽。§B12 又给它加了
    第二条来源，这一条的理由因此更硬：两条路的**形状**必须逐字一致（都是
    ``(subject_kind, subject_id)`` 的序列、都在派发之前拒、都整份拒），而两处各写
    一份的话，差别只会在某一天落在某一个字段上。

    ## ``source`` 由调用方先算好，因为**它也要进回执**

    来源是哪一种不只是这里的分支条件：``get_maintenance_plan`` 的 ``subjects``
    那一栏要报出**看的是哪一份**。在这一层再算一遍的话，报告里那一行与这里真的
    读的那一份会在某次改动后分家 —— 而分家的形态是「报告说读的是 A」与「真的
    读了 B」同时成立，两份都自洽。所以 :func:`_subject_source` 只调用一次，
    结果同时喂给这两处。

    ## 名字那一格先验，**先于**是哪一条路

    与 ``subjects_for`` 逐字同一条：``name`` 拼出的是一条文件名，而一个需要被
    规范化的名字会与另一个名字落到同一个文件上 —— 所以它走 :func:`_account_list_name`
    （``ValueError`` → ``ToolError``，因为裸 ``ValueError`` 穿出处理器之后调用方
    拿到的是「Error executing tool …」，**一句话都没有**）。

    ## 名单那一格不在这里验

    ``cohort`` 的值是一个内容寻址的 id，不是一个文件名 —— 所以它**不该**过
    ``check_canonical_id`` 的同一套界（那条界的理由是「名字会变成文件名」，
    而它不变成文件名）。它该不该存在由 :func:`~marketops.application.cohorts.
    cohort_dossiers` 回答：读不到就是那条 ``blocker``，而它说得比任何名字判据都准。

    Raises:
        ToolError: 这一份来源读不出来（清单不存在 / 名单没冻过 / 库被换过）。
    """
    kind, value = source

    if kind == "name":
        try:
            subjects = account_subjects(settings.inputs_dir, _account_list_name(value))
        except ConfigError as exc:
            raise ToolError(str(exc)) from exc
        # 一份 operator 手写的清单**没有**冻结时刻：它不被内容寻址、没有 id、
        # 随写随变。这里如实报 ``None``，不编一个 —— 理由见 :func:`_as_of_cell`。
        return _SubjectBatch(subjects=subjects, cohort=None)

    # 名单那条路。**没有接线**与**名单读不出来**是两句话，分开说 —— 前者是部署的
    # 接线事实（补工厂），后者是库里的读数（去 freeze 或去查库）。
    if pool is None or cohorts is None:
        raise ToolError(
            "这个部署没有接冻结成员名单的存储（``cohorts``），所以按 cohort 取主体"
            "这条路走不通。\n"
            "  维护与起草这两条链的主体可以来自一份从账号池冻下来的名单，而那需要"
            "两个工厂：``accounts``（读池子）与 ``cohorts``（读名单）。缺的是"
            "组合根那一行接线 —— 不是参数给错了。\n"
            "  另一个选择：给 ``name``，按一份 operator 手写的清单跑（那条路不需要"
            "这两个工厂）。"
        )
    resolution = cohort_subjects(
        pool(), cohorts(), scene_id=scene_id, cohort_ref=value
    )
    if not resolution.ok:
        raise ToolError(resolution.blocker)
    return _SubjectBatch(
        subjects=tuple(
            (SUBJECT_KIND_ACCOUNT, dossier.account_id)
            for dossier in resolution.subjects
        ),
        # ``resolution.frozen`` 由**读出这份名单的那一次读**交上来（见
        # ``CohortReading``）：这里不再碰存储、更不取时钟。两个 session 读同一份
        # 名单拿到的是同一个值；读两份不同的名单拿到两个值。
        cohort=resolution.frozen,
    )


def _subjects(
    settings: Settings,
    *,
    source: tuple[str, str],
    scene_id: str,
    pool: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
) -> tuple[tuple[str, str], ...]:
    """只要主体那一串的投影。**判据一概不在这里** —— 它就是上面那一型的一半。

    这一支留着是因为 ``outreach`` 与 ``runs`` 两条链**只要主体**（它们把
    ``(kind, subject_id)`` 直接喂给编译与派发），而它们不该因为读侧多了一格
    ``as_of`` 就改签名。要那一格就用 :func:`_subject_batch`：它把「这一批看的是
    哪一份」与主体一起交出去，两样同源。

    分成两个名字而不是给 ``_subject_batch`` 加一个开关：一个返回两样、一个返回
    一样，是**两个类型**，而不是同一件事的两个取值。一个 ``only_subjects=True``
    的开关会让调用点读不出自己拿到的是哪一种。
    """
    return _subject_batch(
        settings, source=source, scene_id=scene_id, pool=pool, cohorts=cohorts
    ).subjects


def _plan_context(
    *, settings: Settings, scenes: Callable[[], SceneStore], scene_id: str | None
) -> tuple[SceneStore, SceneRecord, SceneVersion | None, str]:
    """三个工具共同的头部：这个 Scene 的记录、它的**生效版本**、以及要用的池子名。

    ``SceneVersion | None`` 是**合法**的：``DRAFT`` 记录的 ``effective_version``
    为空（还没批准过哪一版生效）。那时没有复核节奏可读 —— 而这不是错误，是
    「这个市场还没开张」，报告要说的是这一句。

    ## 「没有生效版本」与「生效版本读不出来」是三件事里的两件

    ``None`` 只对**记录自己说没有生效版本**成立。记录说**有**、而库里那一版读不到，
    是完全不同的一句话：那一版的节奏是这个市场现在正在执行的规则，读不到它意味着
    存储不自洽（被改过、迁移只做了一半、库被换过）。把它也折成 ``None`` 的后果是
    **报告上说「这个市场还没开张」**，而它其实开着、只是规则不见了 —— 一个要人去查
    存储的问题被读成一句「还没开张」，于是没有人会去查。

    所以那一支**拒绝**（``ToolError``，与 ``_scene_record`` 那一条同一处置）：一句
    指得出场景与版本号的拒绝，比一份自洽而错误回执有用。
    """
    chosen = require_scene(settings, scene_id)
    store = scenes()
    record = _scene_record(store, chosen)
    if record.effective_version is None:
        return store, record, None, chosen
    version = store.get_version(chosen, record.effective_version)
    if version is None:
        raise ToolError(
            f"Scene {chosen!r} 的记录写着生效版本是第 {record.effective_version} 版，"
            "而库里读不到那一版。\n"
            "  「还没有生效版本」与「生效版本读不出来」是两句话：前者是这个市场还没开张"
            "（用 create_scene 立项、activate 开张），后者是存储不自洽 —— 此刻正在执行"
            "的规则不见了。把它读成「还没开张」会让报告自洽而错误，于是没有人会去查这一格。\n"
            f"  请先核对这一份 Scene 的版本历史（record 里 latest_version="
            f"{record.latest_version}，state={record.state.value}）。"
        )
    return store, record, version, chosen


def _get_maintenance_plan(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    maintenance: Callable[[], MaintenanceStore],
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
    acquisition: DocumentAcquisition | None,
    router: DocumentRouter | None,
    documents: Callable[[str], RunBudget] | None,
    scene_id: str | None,
    name: str,
    cohort: str | None,
    limit: int | None,
) -> dict[str, Any]:
    """这个市场这一轮**有哪些格子该复核**，以及哪些不在本部署的范围内。

    ## 计划的两半，缺一不可

    * **节奏**（``version.refresh``）答「隔多少天该看一眼」—— 它是 Scene 的一格，
      所以改它要走一次改版、会留痕。
    * **主体**答「世界上有谁」。这一半**不是** Scene 的字段，而这是有意的：
      ``what_is_due`` 自己发现不了主体、也不该（它的 docstring 逐字写了「一个会
      自己发现主体的选择器必然也会自己发明主体」）。所以主体由调用方指出一个
      **来源**：``cohort``（一份从账号池冻下来的名单，§B12）或 ``name``
      （一份 operator 手写的清单，显式导入那条旧路）。

    ## 不在范围内的那些格子**照样列出来**

    本部署声明得出来的面只有 ``application/refresh.py`` 里那几个
    （``REFRESHABLE_ASPECTS``）。其余面的格子在这一版里是「今天没人管」，而
    「今天没人管」与「这一轮到期的都做完了」在报告上必须分得开 —— 所以它们进
    ``out_of_scope`` 而不是被静默滤掉。

    ## 暂停的场景：计划照给，但要说明它不会被派发

    §4：暂停只停**新的**派发，回信/退订/投诉/退信照常受理。这个工具是派发这一侧
    的，所以它报 ``ticking: false`` 与理由；而把计划一起给出来，是为了让「暂停之后
    恢复时要补哪些格子」答得出来 —— 一个只回「已暂停」的接口答不了那个问题。
    """
    store, record, version, chosen = _plan_context(
        settings=settings, scenes=scenes, scene_id=scene_id
    )
    at = utc_now()
    source = _subject_source(name, cohort, label="name")
    batch = _subject_batch(
        settings,
        source=source,
        scene_id=chosen,
        pool=accounts,
        cohorts=cohorts,
    )
    subjects = batch.subjects

    refresh_receipt: dict[str, Any] | None = None
    due_rows: list[dict[str, Any]] = []
    outside_rows: list[dict[str, Any]] = []
    truncation: dict[str, Any] | None = None

    if version is None:
        # ``DRAFT``：记录在、生效版本不在。计划为空**不是**「没有到期的」，
        # 是「还没有节奏可依据」，所以理由写在 ``refresh`` 那一格里。
        refresh_receipt = None
    else:
        refresh_receipt = {
            "rules": [
                {
                    "aspect": rule.aspect.value,
                    "after_days": rule.after_days,
                    "calibrated": rule.calibrated,
                }
                for rule in version.refresh.rules
            ],
            # 未标定的那几面逐条报出来（不是折成一个数）：一份未标定的计划照常
            # 运转，但**每一份报告都要说得出这一点**（``RefreshPlan`` 逐字如此）。
            "uncalibrated_aspects": [a.value for a in version.refresh.uncalibrated],
            "is_calibrated": version.refresh.is_calibrated,
        }

    if version is not None and record.accepts_new_work:
        try:
            everything, _ignored = due_work(
                scene_store=store,
                maintenance_store=maintenance(),
                scene_id=chosen,
                now=at,
                subjects=subjects,
                limit=None,
            )
        except ConfigError as exc:
            # 场景不是 ACTIVE 时 ``due_work`` 拒（它落在派发那一侧）。走到这里
            # 说明 ``accepts_new_work`` 与那个判定在某次改动后分家了 —— 分开报，
            # 不要在这里兜底成空计划：那会把一次不一致读成「这一轮没有到期的」。
            raise ToolError(str(exc)) from exc
        inside, outside = split_by_aspect(everything, refresh_use_cases.REFRESHABLE_ASPECTS)
        if limit is None:
            kept, decision = inside, None
        else:
            kept, decision = apply_cap(inside, limit, key=lambda item: item.dedupe_key)
        due_rows = [_due_row(due) for due in kept]
        outside_rows = [_due_row(due) for due in outside]
        truncation = decision.as_dict() if decision is not None else None

    return {
        "scene_id": chosen,
        "workspace": record.workspace,
        "at": _instant(at),
        # 这一批主体是**按哪一份**写下来的读数 —— 那份东西自己的时刻，见
        # :func:`_as_of_cell`。它与上面那一格 ``at`` 是两个问题：``at`` 答「这份
        # 计划什么时候算的」（判到期、判新鲜度的基准），``as_of`` 答「算的是哪一份
        # 读数」。合成一格会让新鲜度拿一个冻住的时刻当现在。
        "as_of": _as_of_cell(batch.as_of),
        "state": record.state.value,
        "ticking": record.accepts_new_work,
        "accepts_new_work": record.accepts_new_work,
        "effective_version": record.effective_version,
        "latest_version": record.latest_version,
        "refresh": refresh_receipt,
        "subjects": {
            # 把 ``_subjects`` 交出来的那一份递进去：这一栏的 ``cohort_id`` 与上面
            # 那一格 ``as_of`` 于是同源（同一份名单对象），两格不会各指一份。
            **_source_receipt(
                settings, source, scene_id=chosen, cohorts=cohorts, resolved=batch.cohort
            ),
            "count": len(subjects),
            "preview": [subject_id for _, subject_id in subjects[: _MAX_SUBJECT_PREVIEW]],
            "truncated_preview": max(0, len(subjects) - _MAX_SUBJECT_PREVIEW),
        },
        "due": due_rows,
        "due_count": len(due_rows),
        "out_of_scope": outside_rows,
        "out_of_scope_by_aspect": _by_aspect(outside_rows),
        "refreshable_aspects": sorted(a.value for a in refresh_use_cases.REFRESHABLE_ASPECTS),
        # 计划与缺口**一起**给：一份只列 due 的报告读起来像「就这些」，而它没说的
        # 是「承诺要维护的七个面里，本部署只答得出三个」—— 那正是 §11 不许被
        # ``out_of_scope`` 折掉的那个数。
        "coverage": _coverage_receipt(
            plan=None if version is None else version.refresh,
            acquisition=acquisition,
            router=router,
            documents=documents,
        ),
        "truncation": truncation,
        "note": (
            "due 是这一轮**该做**的格子，按 (due_at, subject_kind, subject_id, aspect) "
            "排序 —— 拖得最久的排在前面，所以截断砍掉的是最近才到期的那些。\n\n"
            "subjects 的来源由 cohort 或 name 指出，两个只给一个（都给了会被拒）。"
            "cohort 那条读的是**冻结的成员名单**（freeze_cohort 的产物，主体是系统"
            "自己发现并晋升的账号），name 那条读的是 operator 手写的清单文件。"
            "两条路的次序都是**来源里的次序**：它会变成 subject_id 的次序，而"
            "what_is_due 的返回值要靠一个稳定次序才可复现。维护层自己发现不了主体，"
            "也不该发现。这一栏里的 cohort_id 是**这一批真的用了哪一份**（给空串 ="
            "用最近冻的那一份时，调用方手里没有那个 id）。\n\n"
            "out_of_scope 是本部署**声明得出的复核面之外**的格子 —— 它们今天没人管，"
            "而这不是「到期了但没做」。要让某一面进入范围，得先在 "
            "application/refresh.py 里写出那条复核。\n\n"
            "coverage 是同一件事的**分母**：承诺要维护的七个面（required_aspects）、"
            "本部署答得出的三个（implemented_aspects）、以及没接的那几个"
            "（coverage_gaps，逐面列出并计数）。它不随这一轮到期的格子数变 —— "
            "out_of_scope 会（库存变了它就变），而这一格只随部署变。"
            "missing_wiring 是**部署**那三样接线缺哪几样；plan_present 单说这一版"
            "Scene 的复核节奏在不在（计划不是部署的性质，它来自某一版 Scene）。\n\n"
            "refresh.rules 里每一条都带 calibrated：本工具**没有任何入参**能把一条"
            "规则标成已标定（标定的含义是「这个数字来自一份已批准的策略」，而"
            "调用方在请求里说是，不是那份策略）。改节奏要走 add_scene_version。\n\n"
            "暂停或归档的场景照常给出计划，但 ticking=false、due 为空：暂停只停"
            "**新的派发**，回信/退信/退订/投诉照常受理 —— 那一侧不看这个状态。"
        ),
    }


def _recheck_state_counts(rows: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    """页面复核的逐格行按 ``RecheckState`` 数一遍，**按枚举次序**。

    与 ``MaintenanceRun.rechecks_by_state`` 同一条判据（那边是域对象，这边是回执行）：
    次序钉死在枚举上是为了两次运行的报告能逐行比对；**零个的照列**，因为这一节答的是
    「这四个状态各有几格」，而缺一个键会有两种读法 ——「这一档是 0」与「这一档没算」。

    读不到状态的行走 ``unreadable`` 一档（那是「本该有状态、却读不出来」），不并进
    任何一个复核状态里。
    """
    counted: dict[str, int] = {state.value: 0 for state in RecheckState}
    counted["unreadable"] = 0
    for row in rows:
        outcome = row.get("outcome") or {}
        state = outcome.get("state") if outcome.get("readable") else "unreadable"
        if state in counted:
            counted[state] += 1
    return [[state, counted[state]] for state in (*RecheckState, "unreadable")]


def _by_aspect(rows: Sequence[Mapping[str, Any]]) -> list[list[Any]]:
    """按 ``ALL_ASPECTS`` 的顺序数一遍，**零个的不列**。

    与 ``MaintenanceRun.out_of_scope_by_aspect`` 同一条判据（那边是域对象，
    这边是回执行）：顺序钉住是为了两次运行的报告能逐行比对；零个的不列是因为
    「这一面本部署做不了」与「这一面这一轮没有到期的格子」是两件事。
    """
    counted: dict[str, int] = {}
    for row in rows:
        aspect = str(row["aspect"])
        counted[aspect] = counted.get(aspect, 0) + 1
    return [
        [aspect.value, counted[aspect.value]]
        for aspect in ALL_ASPECTS
        if aspect.value in counted
    ]


def _run_maintenance(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    maintenance: Callable[[], MaintenanceStore],
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
    acquisition: DocumentAcquisition | None,
    router: DocumentRouter | None,
    documents: Callable[[str], RunBudget] | None,
    scene_id: str | None,
    name: str,
    cohort: str | None,
    limit: int | None,
) -> dict[str, Any]:
    """跑一轮：选、领、跑、结。**一次失败不毁掉整批。**

    ## 时间与主体都不是参数

    时刻由服务端取（``utc_now``）。主体由 ``cohort`` 或 ``name`` 指出的那一份
    定序后喂进去 —— 与 ``get_maintenance_plan`` 走**同一处派发**（:func:`_subjects`）。
    两处各解析一遍的话，「计划里说要跑 A」与「真的跑了 B」会在某次改动后同时
    成立，而两份报告都自洽。

    ## 这一版真的会做什么（B22）

    ``work`` 回调是 ``application/refresh.py`` 的 ``build_refresher``，它按面分派：

    * ``source_coverage`` —— 读一遍本地证据记录、落一份盘点、报出「这一格手上有没有
      可绑定的来源」。**不联网。**
    * ``contact_person_role`` / ``email_evidence`` —— 把这一格名下**已知的页面**按
      URL 重取回来、与记录里的指纹比一次。**会联网、会花抓取额度、会够不着。**
      计划取自**生效版本**（``version.refresh``），复用判定的时效窗口是**这一面**的
      ``after_days``。

    三样接线（``acquisition`` / ``router`` / ``documents``）是**必填参数**而不是
    ``__main__`` 里的缺省：它们由组合根构造（``adapters/`` 不认识
    ``interfaces/composition.py``，分层方向），而 ``None`` 的含义是「这个部署没接
    这一样」—— 那时页面复核逐页报 ``not_wired:<名字>``，**不是**被静默跳过（见
    ``missing_wiring``）。

    ## 观测由谁写

    ``build_refresher`` 这一趟**算**一次观测：``source_coverage`` 数的是主体名下
    **全部**证据行，那是一次满覆盖的测量（那一面的主语就是「我们手上有哪些来源」）；
    页面复核则只在**整格都看全了**的时候才算（缺一页就整格不可达）。它在 outcome 里
    声明这件事，而**写那一行的是** ``run_once``（维护账的主人）—— 所以写观测的权限
    没有下放到回调手里，回调只是报出这一趟看到了什么。回执里 ``observations`` 就是
    本轮**真的记下**的那些行，逐格一条。

    这一格是本工具存在的一半理由：没有观测，``get_ops_report.freshness`` 的
    ``last_verified_at`` 恒为 ``null``、``next_due_at`` 永远算不出来 —— 于是
    「这一格什么时候该看」这个问题在这一版里根本没有答案。另一半是变更信号：
    研究侧 ``submit_findings`` 落下**新的**来源时会写一条，于是那一格的复核不必
    等满一个周期。

    ## 三条分开的账

    ``done`` / ``failed`` / ``not_claimed`` 是**三件事**（做成、做挂、不是我做的），
    ``out_of_scope`` 是第四件（不在范围里）。合成一个「处理了 N 件」会让一次并发
    抢走与一次失败读成同一种东西。

    ``rechecks`` 与 ``coverage_gaps`` 是另外两件。前者答「这一轮那些复核各自看到了
    什么、花了钱没有」（``REUSED`` 与 ``UNCHANGED`` 在 ``observations`` 那一格上长得
    一样，只有这张表分得开）；后者答「承诺要维护的面里本部署没接哪几个」—— 它是
    **分母**，不随这一轮到期的格子数变（§11 逐字：不能通过 ``out_of_scope`` 把必需
    功能从验收分母删掉）。
    """
    store, record, version, chosen = _plan_context(
        settings=settings, scenes=scenes, scene_id=scene_id
    )
    at = utc_now()
    source = _subject_source(name, cohort, label="name")
    batch = _subject_batch(
        settings,
        source=source,
        scene_id=chosen,
        pool=accounts,
        cohorts=cohorts,
    )
    subjects = batch.subjects
    refresh = refresh_use_cases.build_refresher(
        # 证据库用 ``write_store``（**库不存在就建出来**），而**不是** ``read_store``：
        # B22 之后这一趟会**落证据**（页面变了就写新行），而一次要落证据的调用如果
        # 因为库文件还不存在而拒绝，那么第一个证据永远落不了盘 —— 它恰恰是「这个
        # 部署第一次真的跑了」的那一次。只读的那几条工具照旧用 ``read_store``。
        store=write_store(settings),
        root=settings.maintenance_dir,
        workspace=record.workspace,
        at=at,
        plan=None if version is None else version.refresh,
        acquisition=acquisition,
        router=router,
        documents=documents,
        # 第四样接线，而它**不**跟着上面三样从签名上传进来 —— 这不是漏了，是一处
        # 判据：上面那三样各有一个运行时条件（浏览器装没装、MinerU 装没装、profile
        # 有没有声明 ``fetch.max_documents``），所以它们必须由组合根决定装哪一个；
        # 而「把一份字节落进另一个主体的目录」今天**只有一个实现、没有任何条件**，
        # 镜像目录的约定也只在 ``adapters/page_mirror.py`` 一处定义。做成第四个参数
        # 会给出一个没有人在转的旋钮，而读签名的人会以为它可调。分层上也没有障碍：
        # 本文件与 ``page_mirror`` 同在 ``adapters/``（``tools.py`` 早就在用后者）。
        copy_page=page_copier(settings),
    )
    report = run_once(
        scene_store=store,
        maintenance_store=maintenance(),
        workspace=record.workspace,
        now=at,
        work=refresh,
        scenes=(chosen,),
        subjects={chosen: subjects},
        limit=limit,
        aspects=refresh_use_cases.REFRESHABLE_ASPECTS,
    )

    return {
        "scene_id": chosen,
        "workspace": report.workspace,
        "at": _instant(report.at),
        # 这一轮**什么时候跑的**（``report.at``，与喂给 ``run_once(now=)`` 和
        # ``refresh(at=)`` 的那个时刻是同一个）。它与 ``as_of`` 不是一回事：同一个
        # ``as_of`` 的两轮 tick 是两个 ``at``，而同一个 ``at`` 底下的两份读数不可能
        # 有两个 ``as_of``。读侧与写侧在这一格上共用一份定义，正是为了让
        # 「这一轮看的是哪一份」在两个方向上答得一样。
        "as_of": _as_of_cell(batch.as_of),
        "state": record.state.value,
        "effective_version": record.effective_version,
        "latest_version": record.latest_version,
        "ticking": not report.not_ticking,
        "not_ticking": [{"scene_id": sid, "reason": why} for sid, why in report.not_ticking],
        "due_count": report.due_count,
        "claimed_count": report.claimed_count,
        "ok": report.ok,
        "done": [
            {**_claim_row(claim), "outcome": _outcome_row(claim)} for claim in report.done
        ],
        # 失败的那几格**也带产出**（如果有的话）：``result_ref`` 指的那一份记录是这一趟
        # 唯一的产出，扔了它等于把「为什么没拿到」也一起扔掉 —— 一格的逐页结论（哪几页
        # 看了、哪几页没看、因为什么）只在那一份里。抛了异常那一支没有 result_ref，
        # 于是 outcome 报「这一格没有产出引用」，那也是一句实话。
        "failed": [
            {**_claim_row(claim), "outcome": _outcome_row(claim)} for claim in report.failed
        ],
        "not_claimed": [_claim_row(claim) for claim in report.not_claimed],
        "out_of_scope_count": len(report.out_of_scope),
        "out_of_scope_by_aspect": [
            [aspect, count] for aspect, count in report.out_of_scope_by_aspect
        ],
        # 这一轮的主体是**按哪一份**名单定序的 —— 与 ``get_maintenance_plan`` /
        # ``get_ops_report`` 逐字同形的那一栏，见的理由也逐字相同。
        #
        # 这一格在**写**工具上尤其不能省：``cohort`` 的 ``""`` 是「用这个 Scene
        # 最近冻的那一份」，而调用方手里并没有那个 id —— 不报出来的话，「这一轮跑的是
        # 谁」在这份回执上永远只答得出「最近那一份」，而这个说法在两份报告里都成立，
        # 它们读的却可以是两份不同的名单（中间又冻了一次就分家了）。
        "subjects": {
            # 把 ``_subjects`` 交出来的那一份递进去：这一栏的 ``cohort_id`` 与上面
            # 那一格 ``as_of`` 于是同源（同一份名单对象），两格不会各指一份。
            **_source_receipt(
                settings, source, scene_id=chosen, cohorts=cohorts, resolved=batch.cohort
            ),
            "count": len(subjects),
        },
        "refreshable_aspects": sorted(a.value for a in refresh_use_cases.REFRESHABLE_ASPECTS),
        # 承诺 / 实现 / 缺口三个数**一起**给（见 ``_coverage_receipt``）：只给前两个
        # 的报告读不出「差了几面」，只给后一个读不出「分母是多少」。
        "coverage": _coverage_receipt(
            plan=None if version is None else version.refresh,
            acquisition=acquisition,
            router=router,
            documents=documents,
        ),
        # 这一轮那些复核各自看到了什么，**按状态数**（零个的不列，与
        # ``out_of_scope_by_aspect`` 同一条判据）。它与 ``observations`` 不是同一件事：
        # 一格里 ``REUSED`` 与 ``UNCHANGED`` 都写观测、都指向一份内容，差别只在有没有
        # 真的发出去一个请求 —— 抓取次数是这一片要证的第一个数，而它只在这张表里。
        #
        # **逐格的那一份不在这里**：它在 ``done`` / ``failed`` 每一行的 outcome 里
        # （``outcome.state`` + ``outcome.pages``）。在这张表上再拼一份「格 → 状态」
        # 是拿两个各自按自己次序排的序列去对齐，而它们**数目本来就不一样**（数本地
        # 记录的那一面不产生复核状态）。拼出来的那一份会静默错位，且看起来完全正常。
        "rechecks_by_state": [[state, count] for state, count in report.rechecks_by_state],
        "coverage_gaps": [a.value for a in report.coverage_gaps],
        "source_deltas": [
            {
                "new_source_gaps": delta.new_source_gaps,
                "new_venues": delta.new_venues,
                "unmeasured": list(delta.unmeasured),
                "measured": delta.measured,
            }
            for delta in report.source_deltas
        ],
        # 本轮**真的记下**的观测，逐格一条。它不是 ``done`` 的另一份写法：
        # ``done`` 数的是「这一格做过了」，而这一格数的是「这一面**真的**被看到了」
        # ——``observed=False`` 的回调做完了也不在这里出现。两者的差就是
        # ``application/maintenance.py`` 那个 ``observed`` 字段的全部意义。
        "observations": [
            {
                "subject_kind": observation.subject_kind,
                "subject_id": observation.subject_id,
                "aspect": observation.aspect.value,
                "observed_at": _instant(observation.observed_at),
                "source_ref": observation.source_ref,
            }
            for observation in report.observations
        ],
        "truncation": report.truncation.as_dict() if report.truncation is not None else None,
        "note": (
            "done / failed / not_claimed 是三件事，不是一件：做成了、做了但挂了、"
            "**这一格不是我的**（别人在做或者已经做完）。第四件是 out_of_scope —— "
            "不在本部署声明得出的复核面里，它在认领**之前**就被滤掉了。\n\n"
            "done 与 failed 里每一格都带 outcome，它是从 result_ref 指向的**盘上那一份**"
            "产出读回来的（不是内存里的副本）。outcome 有两种：数本地记录的那一面落"
            "盘点（kind=census），两个页面面落复审记录（kind=recheck，带着逐页的结论与"
            "state）。读不出来时 outcome.readable=false，**不是 0 个缺口**。哪一份是哪一份"
            "**由记录自己说了算** —— 不按回执上的 aspect 猜：一次真实的重查被读成一份盘点，"
            "在报告上就是一句「这一格损坏了」。\n\n"
            "rechecks_by_state 是这一轮那些复核按状态数的账（零个的不列）。它答的是"
            "「这一轮花了抓取的钱没有」：REUSED 与 UNCHANGED 在 observations 那一格上长得"
            "一样，差别只在有没有真的发出去一个请求。不可达不写观测，所以它只在这张表里。"
            "逐格的那一份在 done / failed 每一行的 outcome 里，不在这张表上另拼一份"
            "（两个序列数目本来就不一样）。\n\n"
            "coverage_gaps 是**承诺要维护、而本部署没接**的那几个面（§11：不能通过"
            "out_of_scope 把必需功能从验收分母删掉）。它与 out_of_scope_count 不是一回事："
            "那个数随库存变（这一轮有哪些格子没人管），这个数只随部署变。\n\n"
            "subjects 是这一轮**按哪一份**名单定序的：两条路（name / cohort）形状一样、"
            "内容不一样，而 cohort 的空串是「用这个 Scene 最近冻的那一份」—— 调用方给"
            "空串时手里并没有那个 id，所以那一份的 id 在这里报出来。「这一轮跑的是谁」"
            "要答得出来，否则「最近那一份」这句话在两份报告里都成立、而它们读的可以是"
            "两份不同的名单。\n\n"
            "observations 是本轮**真的记下**的观测，逐格一条 —— 它与 done 不是同一件事："
            "done 是「这一格做过了」，而一个回调可以跑完却不声明自己看到了什么。"
            "有了它，get_ops_report.freshness 的 last_verified_at 才第一次有值。\n\n"
            "source_deltas 里每一格里 new_venues 恒为 null：真实场地新增只可能从一次"
            "新的检索里出现，而这一版读的是本地记录、不联网。null = 未测，不是 0 = 测了没有。\n\n"
            "每一格只做一次：dedupe_key 把场景版本、主体、aspect 与周期都编进去，"
            "所以重复 tick 与重放的变更信号都落回同一格（第二次拿到的是 not_claimed）。"
        ),
    }


def _get_ops_report(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    maintenance: Callable[[], MaintenanceStore],
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
    acquisition: DocumentAcquisition | None,
    router: DocumentRouter | None,
    documents: Callable[[str], RunBudget] | None,
    scene_id: str | None,
    name: str,
    cohort: str | None,
) -> dict[str, Any]:
    """这个市场现在经营得怎么样：一处读完的运营报告。

    ## 它答的六个问题

    1. **这个市场还在不在经营**（``scene``）—— 状态、生效版本、暂停理由。
    2. **规则标定了没有**（``refresh``）—— 未标定的那几面逐条列出。一份未标定的
       计划照常运转，但每一份报告都要说得出这一点。
    3. **复核跑到哪儿了**（``ticks``）—— 回执账：各状态几格、最近一次 tick 是
       什么时候、哪些格子还挂在 ``FAILED`` 上（那些是可以重领的，逐格列出）。
    4. **来源供给现在是什么样**（``source_supply``）—— 从**最近一条来源供给回执**
       的 ``result_ref`` 逐格读回盘点，数出「手上没有任何可绑定来源」的主体有几个，
       并**逐行给出来**：那正是要人去处理的一份名单，不是一个计数。
    5. **每一格现在是不是旧的**（``freshness``）—— 逐格给出 ``last_verified_at`` /
       ``next_due_at`` / ``status`` / ``stale_reason``（§11 的四个），外加这一份
       结论是按**哪一份**策略算出来的（``policy_version``）。
    6. **承诺的面里本部署接了几面**（``coverage``）—— 承诺 / 实现 / 缺口三个数
       一起给，外加载紧挨着的那几个面（``page_rechecks``）各自的最后一次复核看到了
       什么。**这一格是分母**：它不随这一轮到期的格子数变（§11 逐字：未接的 aspect
       报 coverage gap，不能通过 ``out_of_scope`` 把必需功能从验收分母删掉）。

    ## ``source_supply`` 只数**答得出那个问题**的那一面

    「这个主体手上有没有可绑定的来源」由 ``source_coverage`` 那一面回答（它读的是
    本地证据行）。两个页面面答的是另一件事 ——「我们记得的那几页现在还是不是那样」
    —— 它们的产出是复审记录，里面**没有** ``bindable_kinds``。把它们的格子算进
    ``subjects_without_any_bindable_source`` 会把「这一面没被问过这个问题」读成
    「这个主体没有来源」；而反过来按盘点读会在摘要核对那一步红，于是那些格子整批
    落进 ``subjects_unreadable`` ——**一次真实的重查被读成一次损坏**。
    所以这一节的分母是 ``source_coverage``，两个页面面另起一节（``page_rechecks``）。

    ## 为什么报告**不做**任何写操作

    它与 ``list_accounts`` 同一条：读一次不建库、不写回执、不动盘点。一份会顺手
    补上缺口的报告，让「报告跑过」与「缺口被处理过」在盘上长得一样。

    ## 读不出来的格子逐格列出，不折成 0

    ``unreadable`` 那一格是独立的：一个 ``result_ref`` 指不到东西意味着那一格的
    产出无法复核，而把它算进「有来源的主体」或「没来源的主体」都是替它下一个
    它不支持的结论。
    """
    store, record, version, chosen = _plan_context(
        settings=settings, scenes=scenes, scene_id=scene_id
    )
    at = utc_now()
    source = _subject_source(name, cohort, label="name")
    batch = _subject_batch(
        settings,
        source=source,
        scene_id=chosen,
        pool=accounts,
        cohorts=cohorts,
    )
    subjects = batch.subjects
    claims = maintenance().claims(chosen)

    by_state: dict[str, int] = {}
    by_aspect: dict[str, int] = {}
    for claim in claims:
        by_state[claim.state.value] = by_state.get(claim.state.value, 0) + 1
        if claim.state.value == "done":
            by_aspect[claim.aspect.value] = by_aspect.get(claim.aspect.value, 0) + 1

    # 每格的**最近**一条（attempt 最大）。同一格有过重试时，只有最后那条是它现在
    # 的样子 —— 把每一条都算一遍会让一份失败过三次的报告看起来像做成了三次。
    #
    # 只收**本部署复核得出**的那些面：另一面的回执（将来可能有的）没有产出可读，
    # 把它算进来会让「这一格读不出来」这一栏被一批本来就没有产出的格子塞满，
    # 而那一栏的意义正是「本该有产出、却读不出来」。
    latest: dict[tuple[str, str, str], MaintenanceClaim] = {}
    for claim in claims:
        if claim.aspect not in refresh_use_cases.REFRESHABLE_ASPECTS:
            continue
        key = (claim.subject_kind, claim.subject_id, claim.aspect.value)
        current = latest.get(key)
        if current is None or claim.attempt > current.attempt:
            latest[key] = claim

    # 三张表各按**它答得出来的那一面**分：来源供给那一节数的是「手上有没有可绑定的
    # 来源」，而那是 ``source_coverage`` 一个问题；两个页面面答的是「我们记得的那几页
    # 现在还是不是那样」，它们的产出里根本没有 ``bindable_kinds``。混在一起的两种读法
    # 都是错的（详见 docstring）：算进「没来源」是把「没被问过」读成「没有」，
    # 按盘点读是把一次真实的重查读成一次损坏。
    unreadable: list[dict[str, Any]] = []
    uncovered: list[dict[str, Any]] = []
    covered = 0
    page_rows: list[dict[str, Any]] = []
    for claim in sorted(
        latest.values(), key=lambda c: (c.subject_kind, c.subject_id, c.aspect.value)
    ):
        outcome = _outcome_row(claim)
        if not outcome.get("readable"):
            unreadable.append({**_claim_row(claim), "outcome": outcome})
            continue
        if claim.aspect not in refresh_use_cases.CENSUS_BACKED_ASPECTS:
            page_rows.append({**_claim_row(claim), "outcome": outcome})
            continue
        if not outcome.get("bindable_kinds"):
            uncovered.append({**_claim_row(claim), "outcome": outcome})
        else:
            covered += 1

    # 从来没有被盘点过的格子：池子里有、账上一条回执都没有。
    #
    # 只按 ``source_coverage`` 算：``never`` 那一格的读法是「这一格欠的是第一次盘点」，
    # 而两个页面面的第一次复核是**另一件事**（它要抓取额度、要接线，而它欠的不一定是
    # 「第一次」—— 一个从没被页面复核过的主体完全可能有好几条证据行）。
    seen = {
        (c.subject_kind, c.subject_id)
        for c in latest.values()
        if c.aspect in refresh_use_cases.CENSUS_BACKED_ASPECTS
    }
    never = [
        {"subject_kind": kind, "subject_id": subject_id}
        for kind, subject_id in subjects
        if (kind, subject_id) not in seen
    ]

    last_tick = max((claim.claimed_at for claim in claims), default=None)

    #: 逐格的新鲜度（§11 的五个字段）。**没到期的格子也在里面** —— 这一节答的是
    #: 「这个市场现在有哪些格子是旧的」，而 ``get_due_work`` 答的是「下一轮做什么」。
    #: 两个问题，**同一份判定**：``aspect_states`` 与 ``what_is_due`` 同源（domain 那
    #: 一侧逐字说明），所以不会出现「报告说它新、队列里却有它」。
    #:
    #: 生效版本不在时给 ``None``，与 ``refresh`` 同一处置：``DRAFT`` 的场景还没有
    #: 「该多久看一次」这句话，于是「旧不旧」无从谈起 —— 那不是「一格都不旧」。
    freshness: dict[str, Any] | None = None
    if version is not None:
        states = aspect_states(
            plan=version.refresh,
            observations=maintenance().observations(workspace=record.workspace),
            signals=maintenance().signals(workspace=record.workspace),
            now=at,
            subjects=subjects,
        )
        by_status = {status.value: 0 for status in AspectStatus}
        for state in states:
            by_status[state.status.value] += 1
        stale = [state for state in states if state.status is not AspectStatus.CURRENT]
        rows = [_state_row(state) for state in stale]
        freshness = {
            "policy_version": version.refresh.policy_version,
            "cells": len(states),
            # 三个状态**每次都列全**（零也列）：缺一个键会有两种读法 ——
            # 「这一档是 0」与「这一档没算」，而这一节的意思正是两者相反。
            "by_status": by_status,
            "stale_by_aspect": _by_aspect(rows),
            "stale_count": len(rows),
            "stale": rows[:_MAX_STALE_ROWS],
            # 少了多少逐格说清：一份被砍过的名单读起来必须不像一份全的。
            "stale_truncated": max(0, len(rows) - _MAX_STALE_ROWS),
        }

    return {
        "scene_id": chosen,
        "workspace": record.workspace,
        "at": _instant(at),
        # 与 ``get_maintenance_plan`` 逐字同一条：``at`` 是这一次读的时刻（它同时是
        # 下面 ``freshness`` 那一节的判定基准），``as_of`` 是这一批主体所依的那份
        # 读数自己的时刻。两个 session 读同一份名单时，后者逐字相同。
        "as_of": _as_of_cell(batch.as_of),
        "scene": {
            "state": record.state.value,
            "effective_version": record.effective_version,
            "latest_version": record.latest_version,
            "pending_version": record.pending_version,
            "accepts_new_work": record.accepts_new_work,
            "pause_reason": (
                record.pause_reason.value if record.pause_reason is not None else None
            ),
            "updated_at": _instant(record.updated_at),
        },
        "refresh": (
            None
            if version is None
            else {
                "rules": [
                    {
                        "aspect": rule.aspect.value,
                        "after_days": rule.after_days,
                        "calibrated": rule.calibrated,
                    }
                    for rule in version.refresh.rules
                ],
                "uncalibrated_aspects": [a.value for a in version.refresh.uncalibrated],
                "is_calibrated": version.refresh.is_calibrated,
            }
        ),
        "subjects": {
            # 这一栏也是**来源 + 条数**：报告里逐格的新鲜度是按这些主体算的，
            # 换一份名单就换一批格子。只说条数会让两份报告看起来一样，而它们
            # 数的是不同的人。
            # 把 ``_subjects`` 交出来的那一份递进去：这一栏的 ``cohort_id`` 与上面
            # 那一格 ``as_of`` 于是同源（同一份名单对象），两格不会各指一份。
            **_source_receipt(
                settings, source, scene_id=chosen, cohorts=cohorts, resolved=batch.cohort
            ),
            "count": len(subjects),
        },
        "freshness": freshness,
        "ticks": {
            "claims": len(claims),
            "by_state": by_state,
            "done_by_aspect": by_aspect,
            "last_tick_at": _instant(last_tick) if last_tick is not None else None,
            # 可以重领的那些：``FAILED`` 的回执（``retryable``）。逐格列出，
            # 因为「哪一格挂了」是下一步要做的动作。
            "retryable": [
                _claim_row(claim)
                for claim in sorted(claims, key=lambda c: (c.claimed_at, c.dedupe_key))
                if claim.retryable
            ],
        },
        # 这一节的**键**叫 ``source_supply``，而七个复核面里对应的那一个叫
        # ``source_coverage``。两个名字指同一件事，保留这个差异是刻意的：
        # 这一节的名字是**已发布的 MCP 输出形状**（§14 要求向后兼容），改它会
        # 让已经接上的读者读不到；而复核面的词表是域概念，§11 逐字定的是后者。
        # 两个名字同时出现在一份报告里会让人愣一下，所以这话写在这里、
        # 也写进了下面 note 的第一句。
        "source_supply": {
            # 这一节的分母是 ``source_coverage``，**不是**本部署复核得出的全部三个面：
            # 见 docstring。逐行的三张表也一样。
            #
            # 键名与 ``page_rechecks.aspects`` **逐字相同**：两节是同一件事的两半
            # （各自的面是哪些、那些面最近说了什么），而一个叫 ``aspect``、另一个叫
            # ``aspects`` 会让读的人以为其中一个是单数（一个面），去猜哪个是哪个。
            "aspects": sorted(a.value for a in refresh_use_cases.CENSUS_BACKED_ASPECTS),
            "subjects_with_bindable_source": covered,
            "subjects_without_any_bindable_source": uncovered,
            "subjects_unreadable": unreadable,
            "subjects_never_ticked": never,
            # 未测的那一项**一处定义、一处转发**。
            "not_measured": {
                "new_venues": refresh_use_cases.NEW_VENUES_REASON,
            },
        },
        # 页面复核那两面的**最近一次**结论，逐格一行。它与 ``source_supply`` 分开是
        # 因为两者答的不是同一个问题（见 docstring）：这一节答「我们记得的那几页现在
        # 还是不是那样」，而那一页没变**不能**被读成「这个主体有来源」。
        "page_rechecks": {
            "aspects": sorted(a.value for a in refresh_use_cases.PAGE_BACKED_ASPECTS),
            "by_state": _recheck_state_counts(page_rows),
            "rows": page_rows,
        },
        "coverage": _coverage_receipt(
            plan=None if version is None else version.refresh,
            acquisition=acquisition,
            router=router,
            documents=documents,
        ),
        "refreshable_aspects": sorted(a.value for a in refresh_use_cases.REFRESHABLE_ASPECTS),
        "out_of_scope_aspects": sorted(
            a.value for a in ALL_ASPECTS if a not in refresh_use_cases.REFRESHABLE_ASPECTS
        ),
        "note": (
            "五个问题各有各的一格：这个市场还在不在经营（scene）、规则标定了没有"
            "（refresh.uncalibrated_aspects）、复核跑到哪儿了（ticks）、来源供给现在"
            "是什么样（source_supply）、每一格现在是不是旧的（freshness）。\n\n"
            "freshness 逐格给出四个字段，**没到期的格子也在里面**：它答的是「这个市场"
            "现在有哪些格子是旧的」，而不是「下一轮做什么」（那是 get_due_work）。"
            "by_status 三档每次都列全（零也列），缺一个键会有两种读法 ——「这一档是 0」"
            "与「这一档没算」。stale 那一列是**要人去处理的名单**，最多列 "
            f"{_MAX_STALE_ROWS} 格，砍了多少由 stale_truncated 逐数报出。"
            "next_due_at 为 null 只有一个意思：这一格**从来没有核过**，所以没有"
            "「下一次」可写 —— 它欠的是第一次，不是 epoch，也不是现在。\n\n"
            "source_supply 这一格是**报告的一节**，与七个复核面里叫 source_coverage 的"
            "那一个指同一件事。名字没跟着面一起改：这一节的键是已发布的输出形状，"
            "改它会让已经接上的读者读不到。\n\n"
            "ticks.retryable 是当前**还可以再领一次**的格子（``retryable``：非 DONE，"
            "也就是 FAILED 与仍在 CLAIMED 上的那些）。失败的格子可以被重领，重领会"
            "产生一行新的回执、attempt 加一 —— 所以这一格下不去只会显示为行数变多。\n\n"
            "source_supply 逐行给出**手上没有任何可绑定来源**的主体：那正是要人去处理的"
            "一份名单。判据是 BINDING_ELIGIBLE_SOURCES（抓到的公开文档），检索摘要不算依据。"
            "这一节的**分母是 source_coverage 那一面**（``aspect`` 那一格写出来），"
            "不是本部署答得出的全部三个面：两个页面面答的是另一件事，混进来会把"
            "「没被问过」读成「没有」。\n\n"
            "subjects_unreadable 是独立的一格：result_ref 指不到东西的格子既不算有来源、"
            "也不算没来源 —— 它那一格的产出无法复核，而把一次损坏读成一次好消息正是"
            "本仓要挡的那件事。\n\n"
            "page_rechecks 是另外两个页面面的**最近一次**结论，逐格一行，按四个复核状态"
            "（REUSED / UNCHANGED / CHANGED / UNREACHABLE）+ 读不出来一档计数（零个也列）。"
            "它答的是「我们记得的那几页现在还是不是那样」，而**页面没变不等于这个主体"
            "有来源** —— 两节合成一节正是上面那条误读的来路。\n\n"
            "coverage 是承诺 / 实现 / 缺口三个数：required_aspects 是产品承诺要维护的七个面，"
            "implemented_aspects 是本部署答得出的三个，coverage_gaps 是没接的那几个"
            "（逐面列出并计数）。**这一格不随库存变** —— 它是分母，不是「这一轮有哪些格子"
            "没人管」（那是 out_of_scope_aspects）。missing_wiring 是部署那三样接线缺哪几样。\n\n"
            "本报告**不做任何写操作**：不建库、不写回执、不补缺口。\n\n"
            "out_of_scope_aspects 是本部署声明得出的复核面之外的那几个 —— 「今天没人管」，"
            "不是「到期了但没做」。"
        ),
    }


# ----------------------------------------------------------------------
# 冻结成员名单（§B12）：账号池 → 一份可回读的读数
#
# 这一组与上面三组的关系：主体那一格从前由 operator 手写的
# ``inputs/<name>.yaml`` 供给（``account_subjects``），现在也可以由**系统自己**
# 从池子里读一份冻下来。三条工具是「读」「列」「取」三个动词，不是一条带 action
# 的工具：冻一次要写、列一次不写、取一次要连池子一起读（那一趟会去核每个成员
# 还在不在），三者的**代价与副作用**不同。
# ----------------------------------------------------------------------
def _selection_receipt(selection: CohortSelection) -> dict[str, Any]:
    """选它的那条规则，逐格报出来。**它就是这份名单的一半身份。**

    ``default_statuses`` 与 ``statuses`` 两格都在，理由不是排版：只报 ``statuses``
    的话，读的人分不出「这一轮只要了 RESEARCHED」与「这一轮按缺省要了三种，而池子
    里恰好只有 RESEARCHED 的那些」—— 前者是一条规则，后者是一条读数，而它们的
    下一步相反（一个是改规则，一个是等池子长出来）。
    """
    return {
        "statuses": [status.value for status in selection.statuses],
        "default_statuses": [status.value for status in WORKABLE_STATUSES],
        "limit": selection.limit,
    }


def _cohort_header(cohort: AccountCohort) -> dict[str, Any]:
    """一份名单的**身份**那一半：id、属于谁、按什么规则、冻于何时、两栏各多少行。

    成员与排除的**内容**不在这里 —— ``freeze_cohort`` 与 ``get_cohort`` 各自
    附上它们，而两者附的方式不同（后者带一次池子回读，见 :func:`_cohort_detail`）。
    """
    return {
        "cohort_id": cohort.cohort_id,
        "workspace": cohort.workspace,
        "scene_id": cohort.scene_id,
        "created_at": _instant(cohort.created_at),
        "selection": _selection_receipt(cohort.selection),
        "member_count": len(cohort.members),
        "exclusion_count": len(cohort.exclusions),
    }


def _exclusion_row(row: CohortExclusion) -> dict[str, Any]:
    """一行被排除的账号。

    ``organisation`` 与 ``status`` 是**冻下来的快照**，不是现读的：只有
    ``account_id`` 的话，读的人要为「这个 id 是谁」去查一次池子，而池子已经变了
    —— 他查到的名字不是当时那个。``reason`` 是 ``code`` 的渲染，两格一起给是为了
    让「机器可判的那一格」（code）与「给人读的那一句」不会各自漂开。
    """
    return {
        "account_id": row.account_id,
        "organisation": row.organisation,
        "status": row.status.value,
        "code": row.code.value,
        "reason": row.reason,
    }


def _member_row(
    member: CohortMember, *, current: MarketAccount | None, checked: bool
) -> dict[str, Any]:
    """一行成员，外加**池子里那一行现在是什么**（没核过时是 ``null``）。

    ## 三格与它们各自的读法

    * ``current`` 为 ``null`` = **这一次没有核**。它与 ``{"in_pool": false}`` 是
      两句不同的话：前者是「没问」，后者是「问了，池子里没有它了」。
    * ``changed`` 为 ``null`` = 比不了（没核，或者那一行已经不在池子里）；
      ``[]`` = 比过了，没变；非空 = 与冻下来的快照比，这几格变了。
      **空列表不是「没核」** —— 把它与 null 合成一个值，一次没核的回读会读成
      「逐格都比过了、一个都没变」，而那是这份名单最乐观的一个结论。
    * 变了的只有两格可能是 ``organisation`` 与 ``status``：名单上冻的就只有
      ``account_id`` / ``organisation`` / ``status``。域名与国家**没有**冻，
      所以它们在这里是「现在是什么」而不是「变了没有」—— 一份两个月前冻的名单里
      的域名是**当时**的域名，而 ``cohort_dossiers`` 逐字写着身份线索要现读。
    """
    row: dict[str, Any] = {
        "account_id": member.account_id,
        "organisation": member.organisation,
        "status": member.status.value,
    }
    if not checked:
        row["current"] = None
        row["changed"] = None
        return row
    if current is None:
        # 下面那一格 ``in_pool: false`` 是**结论**，不是错误：一份名单冻好之后
        # 那一行被取走是可能的，而它的后果是这一份名单再也编译不成工作单元
        # （``cohort_dossiers`` 整份拒）。现在能读到这件事，比等到编译时才炸好。
        row["current"] = {"in_pool": False}
        row["changed"] = None
        return row
    changed: list[str] = []
    if current.organisation != member.organisation:
        changed.append("organisation")
    if current.status is not member.status:
        changed.append("status")
    row["current"] = {
        "in_pool": True,
        "organisation": current.organisation,
        "status": current.status.value,
        "domains": list(current.domains),
        "country": current.country,
    }
    row["changed"] = changed
    return row


def _cohort_detail(
    cohort: AccountCohort,
    *,
    pool_rows: Mapping[str, MarketAccount] | None,
    readback_gap: str,
    limit: int | None,
) -> dict[str, Any]:
    """一份名单的全部要说的话：成员、选择依据、**每一个排除与它的原因**。

    ## 截断逐栏各封一次顶

    ``limit`` 同时管 ``members`` 与 ``exclusions`` 两栏（各封一次，不是合起来封）。
    合成一个总数会让「成员被砍了 3 个」与「排除被砍了 3 个」在报告上同形，而前者
    意味着这一批**做少了**、后者只是名单短了一截。两栏各自的 ``dropped_items``
    逐条给出来，与 ``apply_cap`` 逐字同一条纪律。

    ## ``pool_rows`` 为 ``None`` 有两个来源，所以理由由调用方给

    「这个部署没接池子」与「这一趟没去核」是两句话（前者的处置是接线，后者是
    换一条工具重读），而它们在数据上是同一个 ``None``。所以这一格不自己编一句话：
    ``readback_gap`` 由调用方写，两处各说各的那一句。**不在这里兜底成一句通用的
    「无法回读」** —— 那句话在两种情况下都成立，因而哪一种都没说清。
    """
    checked = pool_rows is not None
    members = [
        _member_row(
            member,
            current=None if pool_rows is None else pool_rows.get(member.account_id),
            checked=checked,
        )
        for member in cohort.members
    ]
    exclusions = [_exclusion_row(row) for row in cohort.exclusions]

    # 原因码的分布**在截断之前**算（整张表），与 ``_pool_summary`` 逐字同一条：
    # 一份被砍过的分布读起来必须不像一份全的。
    by_code: dict[str, int] = {code.value: 0 for code in CohortExclusionCode}
    for row in cohort.exclusions:
        by_code[row.code.value] += 1

    try:
        kept_members, member_cap = apply_cap(
            members, limit, key=lambda row: row["account_id"]
        )
        kept_exclusions, exclusion_cap = apply_cap(
            exclusions, limit, key=lambda row: row["account_id"]
        )
    except ConfigError as exc:
        # 与 ``list_accounts`` 同一条：负数上限是**调用方给错了**，走工具结果那条
        # 通道，而不是从处理器里抛成一次内部错误。
        raise ToolError(str(exc)) from exc

    readback: dict[str, Any] | None = None
    if pool_rows is not None:
        readback = {
            "checked_at": _instant(utc_now()),
            "members_missing_from_pool": [
                member.account_id
                for member in cohort.members
                if member.account_id not in pool_rows
            ],
            "members_changed_since_freeze": [
                row["account_id"] for row in members if row["changed"]
            ],
        }

    return {
        **_cohort_header(cohort),
        "members": kept_members,
        "exclusions": kept_exclusions,
        # 排除的原因码分布，**一个成员都不省**（零也列）。只列非零的那几个会让
        # 「这一档是 0」与「这一档没算」在纸上长得一样，而这一栏的意义正是
        # 让「下一轮就轮到它」（over_limit）与「它被判成不相关」分得开。
        "excluded_by_code": by_code,
        "readback": readback,
        "readback_gap": readback_gap,
        "truncation": {
            "members": member_cap.as_dict(),
            "exclusions": exclusion_cap.as_dict(),
        },
    }


def _selection_statuses(raw: Sequence[str] | None) -> tuple[AccountStatus, ...]:
    """调用方给的状态名 → 一组 ``AccountStatus``，**只接受可研究的那些**。

    ## 为什么名字要在这里翻，而不是直接喂给枚举

    ``CohortSelection(statuses=("eligble",))`` 会抛一个 ``ValueError``，而裸
    ``ValueError`` 穿出处理器之后 SDK 渲染的是 ``Error executing tool …`` ——
    **一句话都没有**。本仓不接受空消息（与 ``_account_list_name`` 逐字同一条）。

    ## 为什么不在 ``WORKABLE_STATUSES`` 里的名字要**拒**

    收下 ``hold`` 意味着把「有人在它身上等一次裁决」的账号放进一份会被派出去研究
    的成员名单里 —— 而名单上那一行看起来完全正常。静默取交集更坏：那样冻结下来的
    ``selection`` 与调用方要的那一条不是同一条规则，而 ``cohort_id`` 是它的内容
    寻址结果，于是报告上写着「按 A 冻的」而实际按 B 冻的。

    这一条是**边界**判据，不是域判据：``CohortSelection`` 的类型允许任何成员
    （它是一个 ``tuple[AccountStatus, ...]``），因为它描述的是「一条规则长什么样」；
    而「哪一条规则可以通过这条工具写下来」是这一层的事。

    Raises:
        ToolError: 有不认识的状态名，或者有一个名字不在可研究的三个里。
    """
    if raw is None:
        return WORKABLE_STATUSES
    known = [status.value for status in AccountStatus]
    chosen: list[AccountStatus] = []
    for name in raw:
        try:
            status = AccountStatus(name)
        except ValueError as exc:
            raise ToolError(
                f"不认识的状态名 {name!r}。账号状态是 {known} 这七个。\n"
                "  状态是一个闭集：写错一个名字的结果必须是**这一次调用被拒**，"
                "而不是那个状态被静默地从选择里丢掉 —— 丢掉之后名单短了一截，"
                "而报告上只显示「池子里没有那些账号」。"
            ) from exc
        if status not in WORKABLE_STATUSES:
            workable = [item.value for item in WORKABLE_STATUSES]
            raise ToolError(
                f"{status.value!r} 不是一个可以放进名单的状态（可以的是 {workable}）。\n"
                "  这个名单会被派出去做维护、起草与研究，所以「谁可以进」这一格"
                "只有一条轴：账号行上那个 status 说它可研究。其余四个状态各有各的"
                "处置：\n"
                "    * hold       —— 有人在它身上等一次裁决；\n"
                "    * irrelevant —— 本 Scene 判过它不相关；\n"
                "    * duplicate  —— 它被判成与池子里另一个账号是同一个；\n"
                "    * archived   —— 已退役。\n"
                "  它们不会因为被写进 statuses 就变得可以研究，而**照收下来的后果**"
                "是一份看起来完全正常的名单里装着一个正在等裁决的账号。\n"
                "  要的是「这一轮只看已经研究过的那些」就给 statuses=[researched] —— "
                "其余每一个被排除的账号都会带上自己的原因码，读得出来为什么没进。"
            )
        chosen.append(status)
    return tuple(chosen)


def _freeze_cohort(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore],
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
    scene_id: str | None,
    statuses: Sequence[str] | None,
    limit: int | None,
    max_rows: int | None,
) -> dict[str, Any]:
    """这个 Scene 的池子 → 一份冻下来的成员名单，**并且写进去**。

    ## 它是「选 cohort」这个动作的全部

    读一遍池子、按一条规则冻结、落盘。它**不派发任何工作**、不花任何钱、不联网 ——
    冻结是一份读数，不是一次运行。所以暂停的场景照样能冻：暂停停的是**新的派发**
    （§4），而「我现在手上有哪些账号」这个问题在暂停期间照样要答得出来。

    ## 排除了谁、为什么，与成员一样重要

    回执里 ``exclusions`` 逐条带原因码（``domain/cohort.py`` 的
    ``CohortExclusionCode``）。这不是附赠的：``excluded = 37`` 里可能有 30 条是
    「这个账号在等人裁决」（正常）、3 条是「它被判成与另一行重复」（要人看一眼）、
    4 条是「这一轮的上限砍到了它」（下一轮就轮到）。同一个数字，三种处置。

    ## 为什么这一趟**不**带池子回读

    ``get_cohort`` 的 ``readback`` 答的是「这一份冻好之后池子动过没有」。刚冻完的
    这一份**就是**刚刚读到的那一份读数，所以那个问题在这里没有第二个答案 —— 而且
    重新读一遍会给出一个可能不同的答案（两次读之间可以有人写池子），把那个差写成
    回读会让一份**刚冻好**的名单显示「有成员变了」。所以这两栏是 ``null``，理由写在
    ``readback_gap`` 里，逐格比对请用 ``get_cohort``。

    ## 两个上限**不是**同一件事，所以是两个参数

    ``limit`` 是**这条选择规则的一部分**（「这一轮最多要几个」），它进 ``cohort_id``
    的原像 —— 改它冻出来的是另一份名单，而被它砍掉的那些账号带 ``over_limit``
    原因码。``max_rows`` 只管**这条回执列几行**：它不进原像、不影响任何一份名单的
    内容，砍掉的行由 ``truncation`` 逐栏报出。合成一个参数会让「我要一份 50 个成员的
    名单」与「我要看见这 300 行」变成同一句话，而它们的下一步完全不同。

    Raises:
        ToolError: 这个部署没接池子或名单存储（写工具那一族：没接线就是一次拒绝，
            不是一条 ``not_wired`` 的正常答案 —— 后者会让一次没冻成读成「冻了一份
            空的」）、这个 Scene 还没立项、状态名非法、或者池子里有别的 Scene 的行。
    """
    missing = [
        name
        for name, wired in (("账号池", accounts), ("冻结成员名单", cohorts))
        if wired is None
    ]
    if missing:
        raise ToolError(
            f"这个服务实例没有接 {'、'.join(missing)} 的存储，所以冻不出一份成员名单。\n"
            "  冻结要读池子、再把结果写回去，两样缺一不可。这不是「池子是空的」"
            "—— 前者的处置是接线，后者是一份冻得出来的空名单。"
        )

    # 走 ``_plan_context`` 而不是 ``require_scene``：这一趟要的是 ``record.workspace``
    # ——名单属于哪个工作区由 **Scene 记录**回答，不由池子回答（池子只有行，每一行
    # 自己带 workspace，它答不了「这个池子属于谁」）。顺带一个后果：没立项过的
    # Scene 冻不出名单，而这是对的 —— 一份名单要有主语。
    _store, record, _version, chosen = _plan_context(
        settings=settings, scenes=scenes, scene_id=scene_id
    )
    selection = CohortSelection(
        statuses=_selection_statuses(statuses), limit=limit
    )
    at = utc_now()
    try:
        cohort = freeze_from_pool(
            accounts(),  # type: ignore[misc] - 上面已排除 None
            cohorts(),  # type: ignore[misc] - 同上
            workspace=record.workspace,
            scene_id=chosen,
            selection=selection,
            at=at,
        )
    except ConfigError as exc:
        raise ToolError(str(exc)) from exc
    except StoreError as exc:
        raise _store_failure(exc) from exc

    return {
        **_cohort_detail(
            cohort,
            pool_rows=None,
            readback_gap=(
                "这一份是**刚刚**冻好的，所以没有做池子回读：刚读到的池子与这一份"
                "名单是同一份读数，而重新读一遍只会给出一个可能不同的答案（两次读"
                "之间可以有人写池子）—— 那个差会被读成「刚冻好就有成员变了」。"
                "要逐格比对请用 get_cohort，它读的是另一个时刻。"
            ),
            limit=max_rows,
        ),
        "scene_id": chosen,
        "state": record.state.value,
        "accepts_new_work": record.accepts_new_work,
        # ``at`` 是**这一次冻结动作**的时刻（它喂给 ``freeze_from_pool(at=)``）；
        # ``as_of`` 是**冻出来的那一份**自己的时刻 —— 它们**可以**不同：同一个池子、
        # 同一条规则重放时 ``put_cohort`` 保留更早的那一个 ``created_at``，于是这一趟
        # 是「刚刚冻的」，而那份读数是「两天前那一次就冻好了的」。合成一格之后，
        # §B22 的复用判定会拿一个刚取到的时刻去比，而它永远判成「不是复用的」。
        "as_of": _instant(cohort.created_at),
        "note": (
            "成员按 **account_id 升序**（池子给的顺序，不是这里排的）：它会变成 "
            "subject_id 的次序，而 work_item_id 由那个次序决定 —— 重排同一份名单会"
            "被编译成另一批工作单元。\n\n"
            "选它的规则就是上面 selection 那一栏，而它**进 cohort_id 的原像**："
            "同一个池子、同一条规则算出来是同一个 id，所以重放不会写出第二份名单"
            "（put_cohort 是一次覆盖写）；规则改一个字就是另一份，而旧的那份还在盘上"
            "读得回来 —— 「这一批当时研究的是谁」永远答得出来。\n\n"
            "exclusions 是**逐条**的，不是「不在名单里」：每一个被排除的账号带一个"
            "原因码。over_limit 是唯一一个不由 status 推出的码 —— 它说的是这一轮的"
            "上限砍到了它，下一轮（或者更大的上限）它就在名单里，把它与"
            "not_relevant 混起来读会把一次配置截断当成一次业务结论。\n\n"
            "这一趟不派发任何工作，所以在暂停的 Scene 上照样能做：暂停停的是新的"
            "派发，而「我手上现在有哪些账号」这个问题在暂停期间照样要答得出来。\n\n"
            f"两栏各自最多列 {_MAX_COHORT_ROWS} 行（缺省，由 max_rows 调），砍了多少"
            "由 truncation 逐栏报出 —— 要全部行的调用方显式传 max_rows=None。\n\n"
            "limit 与 max_rows 是两件事：limit 是**这条选择规则的一部分**（这一轮最多"
            "要几个成员），它进 cohort_id 的原像，被它砍掉的账号带 over_limit 原因码；"
            "max_rows 只管这条回执列几行，不进原像、不改任何一份名单的内容。\n\n"
            "回读请用 get_cohort：它按 id 把这一份连同**池子现在是什么**一起读回来。"
        ),
    }


def _list_cohorts(
    *,
    settings: Settings,
    cohorts: Callable[[], AccountCohortStore] | None,
    scene_id: str | None,
    limit: int | None,
) -> dict[str, Any]:
    """这个部署冻过的**全部**成员名单，早的在前（§B12 的「可随时读取」那一半）。

    ## 为什么不是 ``get_cohort`` 就够

    ``get_cohort`` 答的是「我指的那一份是什么」，而它要求调用方**先知道那个 id**
    —— 而 id 是内容寻址的（``coh-`` + 十六位），没人记得住、也抄不对。所以
    「这个市场冻过几份、分别是什么时候、各有多少成员」在任何工具上都问不出来。
    没有这一条，一次 ``freeze_cohort`` 的产物就只有一个调用方手里的字符串指得到它，
    而那个字符串丢在对话里之后，那一份名单在报告上就不存在了。

    ## 它只读一次，**不建库**

    与 ``list_scenes`` / ``list_account_pool`` 逐字同一条：库文件不存在时这一格是
    ``no_store``、``cohorts`` 是空列表 —— 而那不是「这个市场一份名单都没冻过」。
    后者是一句业务结论，前者说的是「还没有任何东西落进过这个库」。

    ## 没接线时是 ``not_wired``，不是一次拒绝

    读工具的这一族只有这一种形状（写工具那一族反过来）。这一格答的是「这个部署
    冻过哪些名单」，而名单不在**这个**部署里 —— 那与「一份都没冻过」的处置不同。

    ## 不按 Scene 过滤时也在报里说清是哪一份池子

    ``scene_id`` 不给就是**全部**。但库是绑工作区的（``bind_workspace``），所以
    「全部」指的是**本部署**的全部；库里若有别的工作区的行，它们不是「没被过滤
    掉」，而是根本读不出来（``owned`` 当场拒）。
    """
    if cohorts is None:
        return {
            "cohorts": [],
            "cohort_count": 0,
            "cohort_state": _SCENE_RECORD_NOT_WIRED,
            "workspace": settings.profile,
            "scene_id": scene_id,
            "summary": None,
            "truncation": apply_cap([], limit)[1].as_dict(),
            "note": (
                "这个服务实例没有接冻结成员名单的存储，所以这个部署冻过哪些名单读不出来。"
                "这不是「一份都没冻过」—— 前者的处置是接线，后者是去 freeze_cohort。"
            ),
        }
    if not settings.database_path.is_file():
        return {
            "cohorts": [],
            "cohort_count": 0,
            "cohort_state": _SCENE_RECORD_NO_STORE,
            "workspace": settings.profile,
            "scene_id": scene_id,
            "summary": None,
            "truncation": apply_cap([], limit)[1].as_dict(),
            "note": (
                f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
                "所以这个部署还没有冻过任何成员名单。这是一个正常答案，不是读不出来。"
            ),
        }

    try:
        everything = cohorts().list_cohorts()
    except StoreError as exc:
        raise _store_failure(exc) from exc
    records = [
        cohort for cohort in everything if scene_id is None or cohort.scene_id == scene_id
    ]
    rows = [_cohort_header(cohort) for cohort in records]

    by_scene: dict[str, int] = {}
    for cohort in records:
        by_scene[cohort.scene_id] = by_scene.get(cohort.scene_id, 0) + 1
    summary = {
        # 在**截断之前**算，与 ``_pool_summary`` 逐字同一条：一份被砍过的分布
        # 读起来必须不像一份全的。
        "by_scene": by_scene,
        "scenes": len(by_scene),
        # 两份名单的 id 不同而成员序列相同是**可能的**，而它是正常的一句读数：
        # 同一批账号在两条规则下（比如一次要了三种状态、一次只要 researched）
        # 各冻一份。所以这一格不是异常计数，只是让人看得见它。
        "distinct_member_sets": len(
            {tuple(member.account_id for member in cohort.members) for cohort in records}
        ),
    }

    kept, decision = apply_cap(rows, limit, key=lambda row: row["cohort_id"])
    return {
        "cohorts": kept,
        "cohort_count": len(rows),
        "cohort_state": _SCENE_RECORD_PRESENT if rows else _SCENE_RECORD_ABSENT,
        "workspace": settings.profile,
        "scene_id": scene_id,
        "summary": summary,
        "truncation": decision.as_dict(),
        "note": (
            "按 (created_at, cohort_id) 升序，**早的在前** —— 所以最后一条就是这个"
            " Scene 当前的名单。第二格 id 不是多余的：两份名单可以在同一微秒里冻好"
            "（一次 tick 里对两个场景各读一次），而并列的键上的顺序由实现决定，"
            "不定死会让「哪一份是当前的」在两次调用之间换一个答案。\n\n"
            "这一格只有**身份**那一半（id、属于谁、按什么规则、冻于何时、两栏各多少"
            "行）。成员、每一个排除与它的原因要按 id 读回来 —— 那是 get_cohort，"
            "因为它要连池子一起读一遍（成员还在不在）才答得准。\n\n"
            "scene_id 不给就是全部；库是绑工作区的，所以「全部」指本部署的全部。"
        ),
    }


def _get_cohort(
    *,
    settings: Settings,
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
    scene_id: str | None,
    cohort_id: str,
    limit: int | None,
) -> dict[str, Any]:
    """一份名单的**全部**：成员、选择依据、排除与原因，外加池子现在是什么。

    这是「每批可回读」那句话的落点 —— 一批工作派出去之后，要回答的三个问题是
    「这一批是谁」（``members``）、「按什么规则选的」（``selection``）、
    「谁没进来以及为什么」（``exclusions``）。三个都在这一条回执里。

    ## ``cohort_id`` 为空 = 这个 Scene 最近冻的那一份

    与 ``get_maintenance_plan`` 的 ``cohort`` 那一格同一个约定，而这里是**读**：
    空串在这里没有「漏传参数」的风险 —— 它是一条说得出口的读法（最近一份），
    而一份都没有时它会拒，不会给一个空名单。

    ## 跨 Scene 的 id **当场拒**

    ``cohort_id`` 指的那一份属于另一个 Scene 时拒，理由不是洁癖：成员那一栏的回读
    要读池子，而两个 Scene 的池子可以不是同一份（池子的行本身逐 Scene），
    所以一份跨 Scene 的名单在这里读出来的「池子现在是什么」答的是**另一个**池子。
    那会让一份名单的成员看起来被删了。

    ## 这一趟**只读**，不建库

    与 ``list_accounts`` 同一条：库不存在时给 ``no_store`` 与一个空名单的答法，
    而池子没接线时 ``readback`` 是 ``null``（不是「一个都没变」）。
    """
    if cohorts is None:
        raise ToolError(
            "这个服务实例没有接冻结成员名单的存储，所以按 id 读一份名单这条路走不通。\n"
            "  这不是「没有这一份名单」—— 前者的处置是接线，后者是去 freeze_cohort "
            "或换一个 id。用 list_cohorts 能看出这个部署到底冻过哪些（那一条在没接线时"
            "给的是 not_wired，而不是一次拒绝）。"
        )
    chosen = require_scene(settings, scene_id)
    if not settings.database_path.is_file():
        raise ToolError(
            f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
            "所以这个部署还没有冻过任何成员名单 —— 读不到这一份。\n"
            "  这是一个正常答案，不是读不出来：先 freeze_cohort 冻一份。"
        )

    try:
        if cohort_id:
            cohort = cohorts().get_cohort(cohort_id)
            if cohort is None:
                raise ToolError(
                    f"读不到成员名单 {cohort_id!r}。\n"
                    "  id 是内容寻址的（``coh-`` + 十六位），所以一个抄错一个字符的 id "
                    "与一个从来没存在过的 id 在这里长得一样 —— 本层说不出是哪一种。\n"
                    "  用 list_cohorts 列出这个库里真的有的那些。"
                )
        else:
            cohort = current_cohort(cohorts(), scene_id=chosen)
            if cohort is None:
                raise ToolError(
                    f"场景 {chosen!r} 还没有冻过任何成员名单。\n"
                    "  留空 cohort_id 的意思是「用这个 Scene 最近冻的那一份」，而它"
                    "还没有过。\n"
                    "  先 freeze_cohort 冻一份（它的回执里就有 cohort_id），"
                    "或者用 list_cohorts 看这个库里有什么。"
                )
    except StoreError as exc:
        raise _store_failure(exc) from exc

    if cohort.scene_id != chosen:
        raise ToolError(
            f"成员名单 {cohort.cohort_id!r} 属于场景 {cohort.scene_id!r}，"
            f"而本次问的是 {chosen!r}。\n"
            "  名单是**逐 Scene** 的，而这一条回执的成员那一栏要连池子一起读 —— "
            "两个 Scene 的池子可以不是同一份，所以跨 Scene 读出来的「池子现在是什么」"
            "答的是另一个池子，会让一份名单的成员看起来被删了。\n"
            f"  要看这一份：把 scene_id 写成 {cohort.scene_id!r}（本部署只服务一个 "
            "Scene，所以它通常就是 profile 那一个）。"
        )

    pool_rows: dict[str, MarketAccount] | None = None
    gap = ""
    if accounts is None:
        gap = (
            "这个服务实例没有接账号池存储，所以「成员在池子里现在是什么」这一栏读不出来。"
            "那不是「一个都没变」—— 前者的处置是接线，后者是一句读数。成员与排除那两栏"
            "照给：它们读的是**冻下来的**那一份，不经过池子。"
        )
    else:
        try:
            pool_rows = {row.account_id: row for row in accounts().list_accounts()}
        except StoreError as exc:
            raise _store_failure(exc) from exc

    return {
        **_cohort_detail(
            cohort, pool_rows=pool_rows, readback_gap=gap, limit=limit
        ),
        "scene_id": chosen,
        # 这一格是**这一次读**的时刻：它是上面那次池子回读的原子时刻，所以它随
        # 会话而变、随两次调用而变 —— 那是它该有的样子。
        "at": _instant(utc_now()),
        # 这一格是**这一份名单**自己的时刻（``cohort.created_at``），与上面那格
        # 是两个问题。它**逐字**来自这一趟读回来的那一份（不是另读一遍、更不是
        # 取一次时钟），所以两个 session 读同一个 ``cohort_id`` 拿到的是同一个值，
        # 而读两份不同的名单拿到的是两个值。§B25 要的正是前一条，而只测前一条的话
        # 「永远返回一个常量」也能全绿 —— 所以后一条也在测试里。
        #
        # 它与下面 ``created_at`` 同值，但**不是同一格的两个名字**：``created_at``
        # 在 ``_cohort_header`` 里，是这份名单**身份**的一半（``list_cohorts`` 与
        # ``freeze_cohort`` 也报它）；``as_of`` 是这一层对**所有**读侧回执的统一
        # 那一格，让「Codex 和 WorkBuddy 读的是不是同一份」不必逐工具认字段名。
        "as_of": _instant(cohort.created_at),
        "note": (
            "这一条答的是**一批工作派出去之后**要问的三件事：这一批是谁（members）、"
            "按什么规则选的（selection）、谁没进来以及为什么（exclusions）。\n\n"
            "members 里每一行的 current 是**池子现在是什么**，changed 是它与冻下来的"
            "快照比哪几格变了。三态要分清：current 为 null = 这一次没核；"
            "in_pool 为 false = 核了、池子里没有它了（这一份名单从此编译不成工作单元，"
            "``cohort_dossiers`` 会整份拒 —— 现在读到比编译时才炸好）；changed 为 "
            "null = 比不了，[] = 比过了、没变。**[] 不是「没核」**。\n\n"
            "变了的只可能是 organisation 与 status 两格：名单上冻的只有那两格。"
            "域名与国家没冻，所以它们在这里是「现在是什么」而不是「变了没有」—— "
            "身份线索要现读，一份两个月前冻的名单里的域名是**当时**的域名。\n\n"
            "excluded_by_code 七档每次都列全（零也列）：缺一个键会有两种读法 —— "
            "「这一档是 0」与「这一档没算」。\n\n"
            "本工具**不做任何写操作**：不建库、不冻名单、不改池子。"
        ),
    }


# ----------------------------------------------------------------------
# 处理器
# ----------------------------------------------------------------------
def product_loop_handlers(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore] | None,
    maintenance: Callable[[], MaintenanceStore],
    assets: Callable[[], VenueAssetStore] | None,
    relations: Callable[[], AccountVenueRelationStore] | None,
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
    review: Callable[[], ReviewCaseStore] | None,
    acquisition: DocumentAcquisition | None,
    router: DocumentRouter | None,
    documents: Callable[[str], RunBudget] | None,
) -> dict[str, Handler]:
    """产品环里**已接线**的处理器，按工具名索引。

    闭包而不是类：与 ``control_handlers`` 同一个理由 —— 每个处理器都要
    ``settings`` 与存储工厂，闭包让「哪一个工具用了哪一份配置」在签名上看得见。

    ``scenes`` 与 ``maintenance`` 都是**工厂**而不是实例：``sqlite3`` 的连接默认
    不允许跨线程使用，而处理器可能在别的线程上跑（``tools.py`` / ``__main__.py``
    里同款的 ``ledger_factory`` / ``jobs`` / ``records``）。读一次开一次是本仓已有
    的先例。

    每一个工厂**分开**，尽管今天它们指向同一个库文件：``SceneStore`` /
    ``MaintenanceStore`` / ``VenueAssetStore`` / ``AccountVenueRelationStore`` /
    ``AccountPoolStore`` 各自是一个端口，一个部署可以只有其中几个（``get_scene``
    那一路就用一个 ``scenes=None`` 表达「没接存储」）。合成一个参数会让「这个部署
    有没有场景存储」与「它有没有资产池」看起来是同一件事，而它们是几件**可以各自
    缺席**的接线 —— 与 ``ports/`` 把它们分成几个 ABC 是同一条理由。

    这一段原先逐句写着「四个工厂」「四个参数」「那三个可以为 ``None`` 的」。改这一
    条时那几个数是**一起**变的，而这正是它们不该在这里的原因：这段话里没有任何一个
    数有执行器（``ports/`` 那一侧有三个判据钉着端口的形状，这里一个都没有）。现在
    它只说集合，不说基数 —— 下一个端口进来时不会有一段话悄悄落后。

    这些参数都**必填**，包括那几个可以为 ``None`` 的：``None`` 是「这个部署没有
    这一样」，而漏传是一个 ``TypeError``。给它们一个 ``= None`` 的缺省会把两件事
    合成一件 —— 一个忘了接资产池的组合根会得到一个处处 ``not_wired`` 的部署，
    而它在报告上与「本来就没接」长得一样。

    ``acquisition`` / ``router`` / ``documents`` 是**另一类**参数，与上面那组工厂分开
    写：那几个是存储（一次开一个连接），这三个是**外面**——抓取器、解析器、额度账本。
    它们不是工厂，因为 B22 那一趟里每一格都要用同一个实例：额度账本要跨格累加，
    抓取器与解析器各自持有自己的缓存与限速，一格里造两次会把同一趟的两次抓取
    记成两次「第一次」。

    三个都可以是 ``None``，而 ``None`` 在这里是**有后果的**：那个部署仍然能 tick，
    但每一个需要外网的面会以 ``not_wired:<名字>`` 记一格失败，并在 ``coverage``
    回执的 ``missing_wiring`` 里点名。这样「没接」是可读的，而不是静默跳过。

    证据库**不在这里**：它住在 ``tools.read_store``（本仓唯一一处说得出「证据存在
    哪」的地方），而关系那两条工具、以及账号池那一条用它去核证据引用解不解得开 ——
    多一个参数就是那句话的第二份拷贝，两份迟早说的不是同一个文件。
    """

    def create_scene(
        region: list[str],
        scenario: str,
        product: str,
        role_policy: str,
        source_plan: list[str],
        budget: dict[str, int],
        scene_id: str | None = None,
        admissions: list[str] | None = None,
        additional_exclusions: list[str] | None = None,
        refresh_days: dict[str, int] | None = None,
        outreach: dict[str, Any] | None = None,
        note: str = "",
        activate: bool = False,
    ) -> dict[str, Any]:
        return _create_scene(
            settings=settings,
            scenes=scenes,
            scene_id=scene_id,
            region=region,
            scenario=scenario,
            product=product,
            admissions=admissions or (),
            additional_exclusions=additional_exclusions,
            role_policy=role_policy,
            source_plan=source_plan,
            budget=budget,
            refresh_days=refresh_days,
            outreach=outreach,
            note=note,
            activate_now=activate,
        )

    def add_scene_version(
        scene_id: str | None = None,
        region: list[str] | None = None,
        scenario: str | None = None,
        product: str | None = None,
        admissions: list[str] | None = None,
        additional_exclusions: list[str] | None = None,
        role_policy: str | None = None,
        source_plan: list[str] | None = None,
        budget: dict[str, int] | None = None,
        refresh_days: dict[str, int] | None = None,
        outreach: dict[str, Any] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        return _add_scene_version(
            settings=settings,
            scenes=scenes,
            scene_id=scene_id,
            region=region,
            scenario=scenario,
            product=product,
            admissions=admissions,
            additional_exclusions=additional_exclusions,
            role_policy=role_policy,
            source_plan=source_plan,
            budget=budget,
            refresh_days=refresh_days,
            outreach=outreach,
            note=note,
        )

    def control_scene(
        action: str,
        scene_id: str | None = None,
        version: int | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        return _control_scene(
            settings=settings,
            scenes=scenes,
            review=review,
            scene_id=scene_id,
            action=action,
            version=version,
            reason=reason,
        )

    def list_scenes(limit: int | None = _MAX_SCENES) -> dict[str, Any]:
        # 缺省封顶、且封顶**被报出来**（``truncation``）—— 与 ``list_accounts``
        # 逐字同一条理由。要全部行的调用方显式传 ``None``。
        return _list_scenes(settings=settings, scenes=scenes, limit=limit)

    def import_motherpool(
        artifact_ref: str,
        batch_id: str = "",
        scope: str = "",
        cutoff: str = "",
        cursor: str = "",
    ) -> dict[str, Any]:
        return _import_motherpool(
            settings=settings,
            artifact_ref=artifact_ref,
            batch_id=batch_id,
            scope=scope,
            cutoff=cutoff,
            cursor=cursor,
        )

    def promote_receipt_tool(
        receipt_id: str, run_id: str | None = None
    ) -> dict[str, Any]:
        # 内层函数名与 application 那个 use case 同名会把「这一层在做协议转换」
        # 这件事读没：这里只读入参、取时刻、拿存储、渲染回执，业务规则一行都没有。
        #
        # ``run_id`` 在这里，与 ``judgements`` / ``budget`` 同型：**这一批折入属于哪
        # 一轮 Run** 只有调用方知道（Run 那一侧的身份是本部署发出去的，但「这一次导入
        # 是替哪一轮做的」不在盘上任何一处）。缺省 ``None`` 是一个被声明出来的事实
        # —— 一次不属于任何一轮的折入，不是「还没接上」。
        return _promote_receipt(
            settings=settings,
            assets=assets,
            accounts=accounts,
            receipt_id=receipt_id,
            run_id=run_id,
        )

    def list_assets(limit: int | None = _MAX_ASSETS) -> dict[str, Any]:
        # 缺省封顶、且封顶**被报出来**（``truncation``）—— 与 ``list_accounts``
        # 逐字同一条理由。要全部行的调用方显式传 ``None``。
        return _list_assets(settings=settings, assets=assets, limit=limit)

    def remember_responsibility(
        venue_asset_id: str,
        judgements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # ``judgements`` **在**这里，与 ``budget`` / ``refresh_days`` 同型：一块场地
        # 由谁负责是一个业务判断，只有调用方能给。**不在**这里的是 ``workspace``
        # （血统由本部署的 profile 定）、``at``（时刻由服务端取，写进 first_seen_at）
        # 与 ``last_verified_at``（记下一条判定不是重新证实了它）—— 理由写在
        # ``_remember_responsibility`` 的 docstring 里。
        return _remember_responsibility(
            settings=settings,
            assets=assets,
            relations=relations,
            venue_asset_id=venue_asset_id,
            judgements=judgements,
        )

    def list_responsibilities(
        venue_asset_id: str | None = None,
        account_id: str | None = None,
        limit: int | None = _MAX_RELATIONS,
    ) -> dict[str, Any]:
        # 缺省封顶、且封顶**被报出来**（``truncation``）—— 与 ``list_assets`` 逐字
        # 同一条理由。要全部行的调用方显式传 ``None``。
        return _list_responsibilities(
            settings=settings,
            assets=assets,
            relations=relations,
            venue_asset_id=venue_asset_id,
            account_id=account_id,
            limit=limit,
        )

    def list_accounts(
        name: str,
        limit: int | None = _MAX_ACCOUNTS,
    ) -> dict[str, Any]:
        # 缺省是 ``_MAX_ACCOUNTS``，不是 ``None``。``apply_cap`` 的方言里
        # ``None`` 只有一个意思 —— **不封顶** —— 而一份账号清单没有天然的大小
        # （``tools.MAX_ITEMS`` 那个 20 数的是证据条目，不是这里的行）。不封顶的
        # 缺省会让一次调用返回一个由清单决定大小的响应；封顶的缺省则**被报出来**
        # （``truncation``），且 ``summary`` 仍覆盖整份清单，所以「池子有多大」
        # 不取决于这一次调用怎么传参。要全部行的调用方显式传 ``None``。
        return _list_accounts(settings=settings, name=name, limit=limit)

    def remember_accounts(accounts_rows: list[dict[str, Any]]) -> dict[str, Any]:
        # 参数名是 ``accounts_rows`` 而不是 ``accounts``：后者在那个作用域里是**存储
        # 工厂**（外层那个参数）。同名会让「这一批账号」与「账号池落在哪儿」在签名上
        # 看起来是同一件东西，而它们一个是业务判断、一个是接线。
        #
        # 不在参数里的三格：``workspace``（血统由本部署的 profile 定）、``at``
        # （时刻由服务端取，写进 first_seen_at）、``last_verified_at``（记下一个账号
        # 不是重新核实了它）—— 理由写在 ``_remember_accounts`` 的 docstring 里。
        return _remember_accounts(
            settings=settings,
            accounts=accounts,
            rows=accounts_rows,
        )

    def list_account_pool(
        account_id: str | None = None,
        limit: int | None = _MAX_POOL_ACCOUNTS,
    ) -> dict[str, Any]:
        # 缺省封顶、且封顶**被报出来**（``truncation``）—— 与 ``list_assets`` 逐字
        # 同一条理由。要全部行的调用方显式传 ``None``。
        return _list_account_pool(
            settings=settings,
            accounts=accounts,
            assets=assets,
            relations=relations,
            account_id=account_id,
            limit=limit,
        )

    def get_maintenance_plan(
        name: str = "",
        cohort: str | None = None,
        scene_id: str | None = None,
        limit: int | None = _MAX_DUE,
    ) -> dict[str, Any]:
        # ``name`` 与 ``cohort`` 都有缺省，而「两个都没给」由 ``_subject_source``
        # 拒掉 —— 这是 §B12 带来的变化，理由要说清：
        #
        # ``name`` 原来是**必填**的，因为那时它是唯一的来源，而 ``name: str = ""``
        # 那句缺省是一句**谎**（没有一份「缺省的清单」）。现在空串有了一个**真**的
        # 意思：它不是这条路。而「哪条路」这件事无法由 schema 表达 —— 「恰好给一个」
        # 不是 required 能写的东西（``cohort`` 的两个取值里有一个是空串），所以它由
        # ``_subject_source`` 判，给的是**具名拒绝**（两条路各是什么、为什么不能都给、
        # 为什么不能都不给）而不是 schema 那句「缺了 name」。
        return _get_maintenance_plan(
            settings=settings,
            scenes=scenes,
            maintenance=maintenance,
            accounts=accounts,
            cohorts=cohorts,
            acquisition=acquisition,
            router=router,
            documents=documents,
            scene_id=scene_id,
            name=name,
            cohort=cohort,
            limit=limit,
        )

    def run_maintenance(
        name: str = "",
        cohort: str | None = None,
        scene_id: str | None = None,
        limit: int | None = _MAX_TICK,
    ) -> dict[str, Any]:
        return _run_maintenance(
            settings=settings,
            scenes=scenes,
            maintenance=maintenance,
            accounts=accounts,
            cohorts=cohorts,
            acquisition=acquisition,
            router=router,
            documents=documents,
            scene_id=scene_id,
            name=name,
            cohort=cohort,
            limit=limit,
        )

    def get_ops_report(
        name: str = "",
        cohort: str | None = None,
        scene_id: str | None = None,
    ) -> dict[str, Any]:
        return _get_ops_report(
            settings=settings,
            scenes=scenes,
            maintenance=maintenance,
            accounts=accounts,
            cohorts=cohorts,
            acquisition=acquisition,
            router=router,
            documents=documents,
            scene_id=scene_id,
            name=name,
            cohort=cohort,
        )

    def freeze_cohort(
        statuses: list[str] | None = None,
        limit: int | None = None,
        scene_id: str | None = None,
        max_rows: int | None = _MAX_COHORT_ROWS,
    ) -> dict[str, Any]:
        # ``limit`` 与 ``max_rows`` 都缺省 —— 而两个 ``None`` 的意思**相反**：
        # 前者是「这条规则不封顶」（成员全都要），后者是「这条回执不封顶」（全部行
        # 都列）。两个都不是「没给」，所以都不该有一个假的哨兵值。
        return _freeze_cohort(
            settings=settings,
            scenes=scenes,
            accounts=accounts,
            cohorts=cohorts,
            scene_id=scene_id,
            statuses=statuses,
            limit=limit,
            max_rows=max_rows,
        )

    def list_cohorts(
        scene_id: str | None = None,
        limit: int | None = _MAX_COHORT_ROWS,
    ) -> dict[str, Any]:
        return _list_cohorts(
            settings=settings,
            cohorts=cohorts,
            scene_id=scene_id,
            limit=limit,
        )

    def get_cohort(
        cohort_id: str = "",
        scene_id: str | None = None,
        limit: int | None = _MAX_COHORT_ROWS,
    ) -> dict[str, Any]:
        # ``cohort_id`` 的空串有一个**真**的意思（这个 Scene 最近冻的那一份），
        # 不是「漏传」的兜底 —— 所以这里不是必填。它与 ``list_cohorts`` 的缺省
        # 合起来让「我没记住那个 id」也走得通，而「最近那一份」在任何时候都答得出来。
        return _get_cohort(
            settings=settings,
            accounts=accounts,
            cohorts=cohorts,
            scene_id=scene_id,
            cohort_id=cohort_id,
            limit=limit,
        )

    return {
        "create_scene": create_scene,
        "add_scene_version": add_scene_version,
        "control_scene": control_scene,
        "list_scenes": list_scenes,
        "import_motherpool": import_motherpool,
        "promote_receipt": promote_receipt_tool,
        "list_assets": list_assets,
        "remember_responsibility": remember_responsibility,
        "list_responsibilities": list_responsibilities,
        "list_accounts": list_accounts,
        "remember_accounts": remember_accounts,
        "list_account_pool": list_account_pool,
        "freeze_cohort": freeze_cohort,
        "list_cohorts": list_cohorts,
        "get_cohort": get_cohort,
        "get_maintenance_plan": get_maintenance_plan,
        "run_maintenance": run_maintenance,
        "get_ops_report": get_ops_report,
    }

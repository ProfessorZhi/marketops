"""控制目录（``docs/MCP_SERVICE.md`` §2）里**已接线**的工具。

目录里十五条，这一层今天是其中十条 —— Phase C 的五个控制面读，加上 Phase G
的五条（``submit_research`` / ``get_job`` / ``control_job`` / ``get_assignment``
/ ``submit_findings``）。剩下五条住 ``research.py`` 与 ``decisions.py``：
``search_web`` / ``fetch_document`` / ``crawl_site`` / ``decide`` / ``get_decision``
花的是钱与网络，依赖形状与这里不重叠，所以分开。哪一条属于哪个阶段写在
``security/catalog.py`` 的 ``phase`` 字段里：那是可以被读出来、也可以被数出来
的事实，不是这里的一句声明。

**两个角色，两张不同的表。** 上面四条 G 工具里，``get_assignment`` 与
``submit_findings`` 在目录里是 ``_RESEARCH``，``submit_research`` / ``get_job``
是 ``_CONTROL``。这张表不看角色（过滤 ``tools/list`` 与拦截调用的是
``RoleGuard``，它读同一个目录）；这里说的是**已接线**这一件事，而角色是另一个
维度 —— 一处各算一遍，两条线迟早会有一条开始说谎。

**数出来的那一半比声明要紧。** ``tests/test_mcp_isolation.py`` 拿 ``backed``
集合比对每个角色看得见什么，所以「这一层有几条」不是散文 —— 它是 ``handlers``
的长度，而长度一变，那份比对立刻跟着变。

## 三条贯穿本模块的纪律

* **失败是一个结果，不是一次崩溃。** 工具抛 ``ToolError`` 时，SDK 会把它变成
  一条 ``is_error`` 的文本；抛别的异常时，调用方得到的是一句
  ``Error executing tool …``，而真实原因留在服务端日志里 —— 那对调用方不可读、
  也不可编程处理。所以坏输入一律 ``ToolError``，且消息短。
* **凭据永不回显。** ``get_capabilities`` 的输出会进调用方的模型上下文。
  「配没配」可以答，「配的是什么」永远不能答。
* **截断必须自报。** §11 明令禁止静默截断。这里的每一处上限都带一个可见的
  标记（``truncated`` / ``context_truncated`` / ``*_count``），因为一个把 5 条
  读成 2 条而不说的接口，会让「证据只有两条」成为报告里的一个事实。

## Scene ≠ profile 目录里的任意一个

``get_scene`` 只服务 ``settings.profile`` 那一个。集群里同时存在几个 profile
目录不等于这个部署被授权读它们 —— 证据、预算与凭据都属于配置里的那一个。
``get_scene`` 收到别的 id 时**拒绝**，不悄悄退回默认值：静默替换会让调用方
以为自己在读 A 场景的规则，实际读的是 B，而两边差异都会被归因到内容本身。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ... import __version__
from ...adapters.build_stamp import LOADED_BUILD
from ...adapters.crawl4ai_fetcher import crawl4ai_available, crawl4ai_unavailable_reason
from ...adapters.mineru_adapter import mineru_available, mineru_fallback_blocker
from ...adapters.profile_loader import load_profile, load_profile_from_scene_files
from ...adapters.sqlite.evidence_store import SqliteEvidenceStore
from ...application.budget import declared_search_ceiling
from ...application.contact_routes import RouteContext, RouteMaterial, produce_routes
from ...application.decision_specs import compile_specs
from ...application.dispatch import (
    BoardConvergence,
    DispatchOutcome,
    WorkDispatch,
    board_target,
)
from ...application.evidence_refs import evidence_refs_of, require_evidence_refs
from ...application.inputs import RECEIPT_KINDS, receipt_for_subject, subjects_for
from ...application.promotion import unpromoted_subjects, venue_handle_for_account
from ...application.review import open_for as open_review_case
from ...application.route_rejections import RejectionTally, history, tally
from ...application.relations import (
    RelationDisposition,
    RelationOutcome,
    remember_researched_relations,
)
from ...application.research_terminal import research_terminal
from ...application.research_strategy import plan_source_methods
from ...config import Settings
from ...domain.bind import BindingSubject, RouteVocabulary
from ...domain.enums import (
    BudgetLine,
    DecisionTask,
    Grade,
    JobInputKind,
    JobState,
    StopReason,
)
from ...domain.errors import (
    BudgetNotConfiguredError,
    ConfigError,
    EvidenceError,
    IdempotencyConflictError,
    JobStateError,
    LeaseError,
    MarketOpsError,
    ReviewConflictError,
    StoreError,
)
from ...domain.evidence import Evidence
from ...domain.findings import Finding, Findings, RelationFinding
from ...domain.job import ResearchJob, WorkItem, request_fingerprint
from ...domain.profile import ProfileBundle
from ...domain.review import ReviewReason, is_pending
from ...domain.route_records import RouteRejection
from ...domain.sources import resolve_source_method
from ...domain.subject import identity_refusal
from ...domain.util import check_canonical_id, utc_now
from ...ports.budget import BudgetLedger
from ...ports.decisions import (
    DecisionProvider,
    DecisionRecordStore,
    DecisionSpec,
    PinnedScene,
)
from ...ports.dispatch import Dispatcher, WorkerExitRecord
from ...ports.jobs import JobStore
from ...ports.pool import (
    AccountCohortStore,
    AccountPoolStore,
    AccountVenueRelationStore,
    VenueAssetStore,
)
from ...ports.review import CaseAdmission, ReviewCaseStore
from ...ports.scenes import MaintenanceStore, SceneStore
from ...security.catalog import Principal, visible_tools
from ..page_mirror import as_text, read_stored_page
from ._shared import Handler, require_scene
from .guard import current_principal

__all__ = [
    "EVIDENCE_LOCATORS",
    "EVIDENCE_LOCATOR_ARTIFACT",
    "EVIDENCE_LOCATOR_BLOCK",
    "MAX_ARTIFACT_BYTES",
    "control_handlers",
    "plan_step",
    "plan_steps",
    "read_store",
    "require_bundle",
    "require_scene",
]

#: ``get_evidence`` 的定位符闭集 —— ``MCP_SERVICE.md`` §2 那行的两个名词：
#: 「an authorized stored **artifact**/**block**, provenance and transformations」。
#:
#: 闭集而不是自由字符串。一个自由字符串会被实现静默当成缺省值处理，于是
#: ``locator="随便什么"`` 与 ``locator="block"`` 拿到同一份回答，而调用方以为
#: 自己问了别的。「有界定位符」的「有界」有两层：回传的量有界，**可选的定位
#: 方式**也有界。
EVIDENCE_LOCATOR_BLOCK = "block"
EVIDENCE_LOCATOR_ARTIFACT = "artifact"
EVIDENCE_LOCATORS = (EVIDENCE_LOCATOR_BLOCK, EVIDENCE_LOCATOR_ARTIFACT)

#: ``locator="artifact"`` 一次最多回多少**字节**。与 ``MAX_CONTEXT_CHARS`` 同一个
#: 理由：回执是给调用方读的，一份没有上界的回执等于一次内存耗尽。这里定上限的
#: 是**服务端**，不是调用方给的参数 —— 所以没有对应的 ``limit`` 入参。
MAX_ARTIFACT_BYTES = 64 * 1024

#: 一次读取里各类条目最多返回多少条。总数另有 ``*_count`` 字段，所以截断可见。
MAX_ITEMS = 20

#: 单条证据的 ``context`` 最多返回多少字符。超过时逐条标 ``context_truncated``。
MAX_CONTEXT_CHARS = 400

#: 单条证据的 ``literal_value`` 最多返回多少字符。超过时逐条标
#: ``literal_truncated``。
#:
#: 比 ``MAX_CONTEXT_CHARS`` 大两个数量级，因为它是**材料**：拿它的人要读那一页
#: 写了什么，而不是看一处命中周围的几个词。它与
#: ``adapters/mcp_server/research.py`` 的 ``MAX_PREVIEW_CHARS`` 同量级 ——
#: 同一个东西（一页正文）在两个回执里有上界，而那两界不该相差一个数量级，
#: 否则读的人会以为大的那个是「全部」。
#:
#: 库里存的那一格不受这里限制：绑定层要在它里面找姓名、角色与邮箱，而那些东西
#: 常常在一页的后半段 —— 见 ``application/evidence_ingest.MAX_LITERAL_CHARS``。
MAX_LITERAL_PREVIEW_CHARS = 4000

#: ``submit_findings`` 回执里 ``by_grade`` 那一格的键序，**取自枚举本身**而不是手写
#: 一份清单：手写的那一份在加一档等级之后不会报错，只会静默少一格，而读的人会以为
#: 「这一档一个都没有」。
_GRADE_ORDER = tuple(member.value for member in Grade)

_ACCOUNT_RECORD_SOURCE = (
    "本构建没有账号注册表。AccountRecord（account_id / organisation / cohort / domain…）"
    "来自 tests/fixtures/accounts.yaml，而那份清单刻意是空的 —— 真实清单必须由使用者提供。"
    "所以这里是 None，不是「这个账号没有组织名」。"
)

_BOUND_NOTE = (
    f"routes / opportunities / evidence_refs 每类最多返回 {MAX_ITEMS} 条，"
    "总数见对应的 *_count 字段。"
)


def control_handlers(
    *,
    settings: Settings,
    wired: frozenset[str] = frozenset(),
    decisions: Mapping[str, DecisionProvider] | None = None,
    advisory: DecisionProvider | None = None,
    dispatcher: Dispatcher | None = None,
    jobs: Callable[[], JobStore] | None = None,
    ledger_factory: Callable[[], BudgetLedger] | None = None,
    worker_outcomes: Callable[[str], tuple[WorkerExitRecord, ...]] | None = None,
    scenes: Callable[[], SceneStore] | None = None,
    assets: Callable[[], VenueAssetStore] | None = None,
    relation_store: Callable[[], AccountVenueRelationStore] | None = None,
    records: Callable[[], DecisionRecordStore] | None = None,
    maintenance: Callable[[], MaintenanceStore] | None = None,
    accounts: Callable[[], AccountPoolStore] | None = None,
    cohorts: Callable[[], AccountCohortStore] | None = None,
    review: Callable[[], ReviewCaseStore] | None = None,
) -> dict[str, Handler]:
    """本构建**已接线**的处理器，按工具名索引。今天十条（见模块 docstring）。

    闭包而不是类：每个处理器都要 ``settings``，闭包让「哪一个工具用了哪一份
    配置」在签名上看得见，也省掉一个只为传参数而存在的对象。

    Args:
        settings: 配置。
        wired: **这个服务实例**真的有实现、会真的去调某个集成的工具名。
            ``get_capabilities`` 的第三格（``wired``）从它算出来 —— 那一格
            问的是「有没有调用路径」，而调用路径是**这个实例**的事实，
            不是构建的常量。没传时按「只有控制面」算，缺省是保守的那一侧。
        decisions: 本实例的决策后端，按 ``provider.name`` 索引。**传进来的**，
            不在这里构造 —— 一个进程里只能有一份 ``LayaDecisionProvider``
            （它各持一个 421M 参数的 Router，见 ``interfaces/composition.py``）。
            ``None`` 表示这个实例没有决策后端，于是那两行会如实这么报，
            而不是拿 ``Settings`` 里的凭据去猜一个「本该就绪」的答案：
            表要说的是**这个进程**里有什么。
        advisory: ``decide`` 实际会咨询的那一个后端（``decisions`` 里的某个
            成员，或 ``None`` 表示本部署没指定）。**传的是解析出来的对象**，
            不是从 ``Settings`` 重推一遍 —— 重推意味着「配置说什么」与
            「真的会调谁」有两份实现，而它们漂移的那天，表会照着配置说话、
            接线会照着别的东西调用。
        dispatcher: 本实例构造出来的执行器（§6 的派发路径）。``None`` 表示这个
            进程没有把它装起来 —— 与 ``decisions`` 同一条：**表要说的是这个
            进程里有什么**，不是配置里写了什么。
            ``None`` 时 ``submit_research`` 照常受理与编译，只是那一批工作
            **一件都不交出去**，回执里的 ``dispatch`` 如实这么说（``executor_
            configured: false`` 加理由）。这与「没有作业存储」不同：作业仍然
            是持久的，执行器到位之后 ``control_job(resume)`` 会把它们派出去。
        jobs: 持久作业记录的**工厂**。与 ``research.py`` 的 ``ledger_factory``
            同一个理由：``sqlite3`` 的连接默认不允许跨线程使用，而工具处理器
            可能在别的线程上跑。``None`` 表示本实例没有作业存储 —— 那时这五条
            G 工具（``submit_research`` / ``get_job`` / ``control_job`` /
            ``get_assignment`` / ``submit_findings``）都不在这张表里，于是注册
            上去的是 stub，调用方拿到的是「这个服务实例尚未接线（Phase G）」。
            **不用内存实现兜底**：一个受理了却在重启后消失的作业，比一条
            「没有落点」的拒绝坏得多，而它看起来完全正常。
        ledger_factory: 读用量用的账本工厂。``None`` 时 ``get_job`` 照常工作，
            只是用量那一格如实报告「这个实例没有账本可读」—— 而**不是**报 0。
        worker_outcomes: 按任务 id 读外部那本 **worker 退出账**的读器（§1）。由组合根
            注入，所以这一层不认识任何具体的执行器适配器。``None`` 时 ``get_job``
            那一段照常出，只是 ``research_terminals`` 如实报「这个实例读不到外部
            判决」—— 与 ``ledger_factory`` 同一条：缺的是那一格，不是整份报告。
        scenes: 场景存储的**工厂**（与 ``ledger_factory`` 同一个理由：``sqlite3``
            的连接不能跨线程）。**两个工具用同一份**：``get_scene`` 用它读**落盘的
            那一份立项记录**（上面那一块答「profile 目录里现在写着什么规则」，
            那一块答「这个市场被立项成了哪一版、哪一版在生效、谁批的」），
            ``submit_research`` 用它问**这个场景现在让不让开工**（``_scene_gate``）。
            两处各传一份、指向两个库的形态是「受理按 A 库判定、报告按 B 库念」——
            所以这里刻意只有一个参数。``None`` 时两处都如实报**判不出来**，
            而**不是**报「没有立项过」或「可以开工」：这两句话对读的人指向两件
            不同的事（接线 vs 业务结论）。
        assets: 场地资产池的**工厂**（同 ``ledger_factory`` 的理由：``sqlite3``
            的连接不能跨线程）。``submit_findings`` 用它问一件事：一条责任结论
            要说出的那块场地**在不在池子里**。``None`` 时关系那一段照常逐行给
            去向，只是每一行的理由是「本服务实例没有接场地资产池」—— 一条接线
            事实，与「这块场地没有负责方」的处置不同（前者去接线，后者去研究）。
        relation_store: 责任关系表的**工厂**。两张表的行在同一个库文件里，但它们是
            两个端口、两条合并规则、两个生产者，所以是两个参数。它只在**这一次
            结算带了责任结论**时被调用 —— 没有 ``relations`` 的产出连一次查库
            都不做（与 ``require_evidence_refs`` 的「只有缺口就不查库」同一条）。
            名字里带 ``_store`` 不是啰嗦：``submit_findings`` **本身**有一个叫
            ``relations`` 的参数（交上来的那些责任结论），同名会让这两个东西在
            这个函数体里互相遮蔽 —— 而遮蔽的方向由一个闭包参数决定，读的人看不出来。
        records: 判定记录的**工厂**（``SqliteDecisionRecordStore``）。与研究侧
            的 ``decide`` 用的是**同一处构造**（``_compose`` 传同一个工厂给两
            边）：判定台账落在同一个库的同一张表上，两个实例指向两个库的形态是
            「研究员问的那次判定在 A 里、结算时读的是 B」—— 于是每一条责任结论
            都会得到「指的那条判定解不开」，而盘上两份记录都正常。
            ``None`` 时每一行如实报「本服务实例没有接判定台账」。
        maintenance: 维护账的**工厂**。``submit_findings`` 走的那条路
            （``_researched_routes`` → ``produce_routes``）在**真的**落下了新证据
            行时要当场记一条变更信号，所以要把它传进去。与 ``assets`` /
            ``relation_store`` / ``records`` 同一个形状、同一个理由。
            这里**必须与 ``product_loop_handlers`` 收到的是同一个工厂**：那一侧
            读的正是这一侧写的信号，两份实例指向两个库的形态是「结算写了信号、
            维护 tick 永远看不见」，而两边的报告各自都正常。
            ``None`` 时维护账那一段如实报「本服务实例没有接维护账」，而
            ``submit_findings`` 其余部分照常 —— 缺的是那一格，不是整次结算。
        accounts / cohorts: 账号池与冻结成员名单的**工厂**（§B12 那一族）。
            ``submit_research`` 的第四种输入（``input_kind="cohort"``）读的是这
            两样，而不是 ``inputs_dir`` 下的一个文件 —— 一份冻好的名单要能被送去
            研究，靠的就是这两格接线。
            ``None`` 时那一种输入如实返回一条**接线理由**（「这个部署没有接这两
            样」），而不是「名单是空的」：前者是部署的事实，后者是库里的读数，
            而一个没装这一段存储的部署本来就不该看起来像一个空的市场。
            它们与 ``product_loop_handlers`` 收到的是同一组闭包（理由写在那一侧
            的签名上：两者各建一份时，「冻名单的那个人」与「读名单的那个人」会
            指向两个库，而两边的回执都正常）。
        review: 收件箱存储的**工厂**（同 ``ledger_factory`` 的理由：``sqlite3``
            的连接不能跨线程）。受理时被身份判据拒掉的那几行在这里落成待办
            （§12）—— 位置写在 :func:`_open_refusal_cases` 上。
            它必须与 ``review_handlers`` 收到的是**同一个**工厂：那一族读的正是
            这一侧写的待办，两份实例指向两个库的形态是「受理时说开了待办、收件箱
            里一件都查不到」，而两边的报告各自都正常。
            ``None`` 时那一段如实报「这个服务实例没有接收件箱」—— 拒绝本身逐字
            不变，缺的是那一格落点。
    """

    def get_scene(scene_id: str | None = None) -> dict[str, Any]:
        return _scene(settings, scene_id, scenes=scenes)

    def get_account(account_id: str) -> dict[str, Any]:
        return _account(settings, account_id)

    def get_evidence(
        evidence_id: str, locator: str = EVIDENCE_LOCATOR_BLOCK
    ) -> dict[str, Any]:
        return _stored_evidence(settings, evidence_id, locator)

    def get_report(run_id: str | None = None) -> dict[str, Any]:
        return _report(settings, run_id)

    table: dict[str, Handler] = {
        "get_scene": get_scene,
        "get_account": get_account,
        "get_evidence": get_evidence,
        "get_report": get_report,
    }

    # 作业那五条只在有落点时才进这张表 —— 见 ``jobs`` 参数。五条一起进出：
    # 一个能受理、读不回来的作业面，会让调用方在受理成功之后没有任何办法
    # 回答「它现在到哪了」，而那正是 §6 要求持久化时要回答的问题。领活与交活
    # 同理 —— 能派人去做、却交不回来（或反过来，能交活却没人领得到），都会把
    # 调用方留在一个**看着像成功**的半截流程里。控制的同理：一个受理得了、却
    # 叫不停的作业，是这五条里最不该缺的那一条。
    if jobs is not None:

        def submit_research(
            scene_id: str,
            input_kind: str,
            input_ref: str,
            idempotency_key: str,
            source_method: str = "",
        ) -> dict[str, Any]:
            return _submit_research(
                settings=settings,
                jobs=jobs,
                dispatcher=dispatcher,
                # 与 ``get_scene`` 传的是**同一个**工厂（``build_server`` 的
                # ``scenes``）。两份的话，「读记录的那一份」与「判闸门的那一份」
                # 是两个库 —— 而它们漂移的那天，受理按 A 库判定、报告按 B 库念。
                scenes=scenes,
                accounts=accounts,
                cohorts=cohorts,
                review=review,
                scene_id=scene_id,
                input_kind=input_kind,
                input_ref=input_ref,
                idempotency_key=idempotency_key,
                source_method=source_method,
            )

        def get_job(job_id: str) -> dict[str, Any]:
            return _job(
                settings=settings,
                jobs=jobs,
                ledger_factory=ledger_factory,
                dispatcher=dispatcher,
                worker_outcomes=worker_outcomes,
                job_id=job_id,
            )

        def get_assignment(work_item_id: str) -> dict[str, Any]:
            return _assignment(
                settings=settings,
                jobs=jobs,
                ledger_factory=ledger_factory,
                work_item_id=work_item_id,
            )

        def submit_findings(
            work_item_id: str,
            attempt_id: str,
            stop_reason: str,
            observations: list[dict[str, Any]] | None = None,
            candidates: list[dict[str, Any]] | None = None,
            gaps: list[str] | None = None,
            relations: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            return _submit_findings(
                settings=settings,
                jobs=jobs,
                dispatcher=dispatcher,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
                stop_reason=stop_reason,
                observations=observations,
                candidates=candidates,
                gaps=gaps,
                relations=relations,
                assets=assets,
                relation_store=relation_store,
                records=records,
                maintenance=maintenance,
            )

        def control_job(
            job_id: str,
            action: str,
            expected_state: str,
            reason: str = "",
            source_method: str = "",
        ) -> dict[str, Any]:
            return _control_job(
                settings=settings,
                jobs=jobs,
                dispatcher=dispatcher,
                accounts=accounts,
                cohorts=cohorts,
                review=review,
                job_id=job_id,
                action=action,
                expected_state=expected_state,
                reason=reason,
                source_method=source_method,
            )

        table["submit_research"] = submit_research
        table["get_job"] = get_job
        table["get_assignment"] = get_assignment
        table["submit_findings"] = submit_findings
        table["control_job"] = control_job

    # ``get_capabilities`` **最后**定义，因为它的第三格问的是「这个实例里有没有
    # 调用路径」，而到这一行为止这张表才完整。``wired`` 由调用方声明的那一份只
    # 含 §3 的研究侧（``search_web`` 等由 ``research.py`` 注册）；G 这五条进不进
    # 表由**本函数**按 ``jobs`` 决定，所以在调用方重推一遍就是同一个判断的第二份
    # 实现 —— 而它们漂移的那天，表会说着「派发尚未接线」而 ``submit_research``
    # 正在真的派发（恒假的健康旗）。
    def get_capabilities() -> dict[str, Any]:
        return _capabilities(
            settings=settings,
            wired=wired | frozenset(table),
            decisions=decisions,
            advisory=advisory,
            dispatcher=dispatcher,
        )

    table["get_capabilities"] = get_capabilities
    return table


# ----------------------------------------------------------------------
# 收件箱的生产者（§12）：受理时被身份判据拒掉的那几行
# ----------------------------------------------------------------------
def _open_refusal_cases(
    *,
    review: Callable[[], ReviewCaseStore] | None,
    settings: Settings,
    scene_id: str,
    refusals: Sequence[tuple[str, str]],
    at: datetime | None = None,
) -> tuple[tuple[CaseAdmission, ...], str]:
    """把这一趟被**身份判据**拒掉的那几行落成待办，并回报这一趟的处置。

    ## 为什么这件事只能在**拒绝发生的那一处**做

    §12 那句门槛是「政策/身份冲突**且值得处理**的才入 Inbox」，而「值得处理」这一半
    只有这里判得出来：身份冲突的正文是「这份名录里这一行拿不出可用来做身份发现的
    依据」，而**人拿得出答案** —— 那是他自己的名录。所以它不是一条「等下游补齐」的
    阻塞，是一件要人答的待办；而它被拒的那一刻，正是唯一同时知道「哪一个场景、哪一行、
    为什么」的地方。搬到别处（维护 tick、结算时的对账）意味着那一趟要重新推一遍
    「哪一行算身份冲突」，也就是
    :func:`~marketops.domain.subject.identity_refusal` 的第二个实现 —— 它与第一个
    漂移的那天，报告上会出现「受理说这行拒了、收件箱里没有它」。

    ## 待办**不是**「可是它还是跑了」

    拒绝本身逐字不变：这一趟一个 work item 都没建，所以一次检索、一次模型调用都没有
    发生（``inputs.py`` 那一行是承重的）。返回值第二格是接在 ``resolution.blocker``
    后面的一段话，**空串表示这一趟没什么要补的**；待办在这里说的是「这一行要有人去
    名录里补一格」，不是「拒绝被放宽了」。

    ## 四种处置，一个都不能压成另一个

    * 新开了一件（``opened=True``）—— 去看。
    * 盘上已经有一件**未决**的（``opened=False``）—— 同一个问题问第二遍，正常去重。
    * 盘上那一件**已经裁决过**（``opened=False`` 且状态是关掉的）—— 这一格**不重开
      它**（``ports/review.py`` 逐字：重开是**人**做的，由系统自己重开等于把人的一句
      「不值得处理」在下一个批次里静默作废）。所以它必须被**报出来**，而不是当作
      「已经问过了」丢掉 —— 一件被吞掉的问题与一件从来不存在的问题在报告里完全同形。
    * 库本身写不进去（``StoreError`` / ``ReviewConflictError``）—— 也报，且**不吞**：
      待办是这一片要真的落下的东西，它的缺席必须看得见。
      这里**不抛**：被拒是对的、独立的，一次库故障不该把一条正确的拒绝变成一次崩溃
      —— 那时调用方连「这几行被拒了」都读不到。

    四种读的是同一段话里的不同分句，而逐行给出的 ``case_id`` 是**唯一**一把钥匙：
    它把回执里那条拒绝理由（``- <account_id>：<refusal 的第一行>``）与盘上那一件对起来
    —— 少了它，「这几行开了待办」是一句无法核对的话。

    Args:
        review: 收件箱存储的**工厂**（与 ``scenes`` 同一条：``sqlite3`` 的连接不能跨
            线程）。``None`` 表示这个服务实例没有接它 —— 那时如实报「没有落点」，
            而不是静默地什么都不做。
        settings: 配置。**工作区由它定**（``settings.profile``），不是参数：能填的话，
            一次调用就能把待办开进另一个工作区，而账面上两件都正常。
            ``product_loop.py`` 的工具族逐字同一条。
        scene_id: 这个作业落的那个场景。
        refusals: ``SubjectResolution.identity_refusals`` —— ``(主体 id, 理由正文)``。
        at: 开案时刻。``None`` 时取当下（``application.review.case_for``）。
    """
    if not refusals:
        return (), ""
    count = len(refusals)
    if review is None:
        return (), (
            f"\n注意：这 {count} 行身份冲突**没有**落成待办 —— 这个服务实例没有接收件箱"
            "（review 未接线）。被拒的行处置不变，但「谁去把名录里那一格补上」这件事"
            "今天没有落点：它不在任何一张表里，所以下一次读收件箱也读不到它。"
        )

    store = review()
    workspace = settings.profile
    admissions: list[CaseAdmission] = []
    failures: list[str] = []
    for account_id, _reason in refusals:
        try:
            admissions.append(
                open_review_case(
                    store=store,
                    workspace=workspace,
                    scene_id=scene_id,
                    reason=ReviewReason.IDENTITY_CONFLICT,
                    subject_ref=account_id,
                    # 受影响对象的**快照**。对身份冲突来说受影响的就是那一行本身 ——
                    # 而它今天只有一个指称对象，所以这一串就是那一行。写 ``(account_id,)``
                    # 而不是空元组：空元组在这一格的意思是「这一件不针对任何对象」，
                    # 与「针对这一行」在报告上是两句话。
                    affected_refs=(account_id,),
                    at=at,
                )
            )
        except (ReviewConflictError, StoreError) as exc:
            failures.append(f"{account_id}：{exc}")

    fresh = [item for item in admissions if item.opened]
    pending = [item for item in admissions if not item.opened and is_pending(item.state)]
    closed = [item for item in admissions if not item.opened and not is_pending(item.state)]

    def _listing(group: Sequence[CaseAdmission]) -> str:
        return "、".join(
            f"{item.case.case_id}（{item.case.subject_ref}，{item.case.state.value}）"
            for item in group
        )

    parts = [f"\n这 {count} 行身份冲突已经落成待办（§12 的收件箱）："]
    if fresh:
        parts.append(f"  - 新开 {len(fresh)} 件：{_listing(fresh)}")
    if pending:
        parts.append(f"  - 盘上已有一件**未决**的 {len(pending)} 件：{_listing(pending)}")
    if closed:
        parts.append(
            f"  - 盘上那一件**已经裁决过**的 {len(closed)} 件：{_listing(closed)}。"
            "这一格**不重开**它：系统自己重开等于把人的一句「不值得处理」在下一个批次里"
            "静默作废，而两件事在报告里同形。要重开得由人做"
            "（resolve_review_case 的 propose_policy_change / reopen 那一族）。"
        )
    parts.append(
        " 处置：新开的那几件等人答（那是他自己的名录，答得出）；"
        "已在盘上的不必重复问。待办不改变这一趟的结论 —— 一个 work item 都没建，"
        "一次检索都没发生。"
    )
    if failures:
        parts.append(
            f"  **另有 {len(failures)} 行没落成待办**（库写不进去）：\n"
            + "\n".join(f"    - {line}" for line in failures)
            + "\n    它们被拒的处置不变，但这一件事在收件箱里查不到 —— 先修库，"
            "再重发这一次受理（case_id 是内容寻址的，重发不会开出第二件）。"
        )
    return tuple(admissions), "\n".join(parts)


# ----------------------------------------------------------------------
# get_capabilities
# ----------------------------------------------------------------------
def _resolve_and_compile(
    settings: Settings,
    store: JobStore,
    job: ResearchJob,
    *,
    accounts: Callable[[], AccountPoolStore] | None = None,
    cohorts: Callable[[], AccountCohortStore] | None = None,
    review: Callable[[], ReviewCaseStore] | None = None,
) -> tuple[tuple[WorkItem, ...], str]:
    """作业的 ``input_ref`` → 它的工作单元；产不出来时返回一条理由。

    **受理与 resume 共用的唯一一份。** 两处各写一遍的话，同一个作业会按「是从哪条
    路径进来的」得到两套编译语义 —— 而它们在报告里长得一样。

    ``blocker`` 为空 ⟺ 编译成功（``SubjectResolution`` 守着「恰好给出一半」，
    所以这里不需要第二个布尔）。**要不要把作业落成 ``BLOCKED`` 由调用方决定**：
    受理只在「没有旧工作、且状态是 QUEUED」时写，resume 则要看它刚刚写下的那个
    状态 —— 那是调用点的事实，不是这一层的事。

    Raises:
        JobStateError: 作业已取消，或它已经有一批**不同**的工作单元（清单在受理
            之后被改过）。
        ValueError: 编译的形状判据（空 subject 等）。

    名字**不**在这里重新校验：``check_canonical_id`` 在受理时已经过了，而
    ``input_ref`` 是原样存下来的 —— 所以 ``subjects_for`` 在这一条路径上不会
    抛那个 ``ValueError``。（写一句「应该不会抛」而不是包一层 except，是因为
    真抛了说明「存下来的与原样存的」这条前提坏了，那时**响一声**比静默降级好。）

    两个 ``accounts`` / ``cohorts`` 工厂**原样透传**给 ``subjects_for``：只有
    ``input_kind="cohort"`` 那一类用得上它们，而那一类的缺省是**一条说得出的
    阻塞理由**而不是一次崩溃（见 ``inputs._cohort_resolution``）。这里不替它
    判「有没有接线」—— 那句话住在那一类自己的解析器里，两处各写一遍的形态是
    「一边说有、一边说没有」。
    """
    resolution = subjects_for(
        job.input_kind,
        job.input_ref,
        inputs_dir=settings.inputs_dir,
        # 名单是**这一份作业**落着的那个场景上的：一份冻结名单的 id 里编进了
        # workspace 与 scene_id（``cohort_id_for``），而读它的时候要拿同一个
        # 场景去对。从 ``settings`` 推不出来，所以它来自作业本身。
        scene_id=job.scene_id,
        pool=None if accounts is None else accounts(),
        cohorts=None if cohorts is None else cohorts(),
    )
    if not resolution.ok:
        # 身份冲突那一种**额外**开待办（§12）—— 它是唯一一种「人拿得出答案」的拒绝，
        # 而这里是唯一同时知道「哪一个场景、哪一行、为什么」的地方。
        #
        # 追加在 ``blocker`` **后面**、原样保留它：拒绝本身逐字不变（一个 work item
        # 都没建，所以一次检索都没发生），待办说的是「这一行要有人去名录里补一格」。
        # 把它折进 blocker 的正文里会让两件事挤在同一句话上，而它们的处置不同 ——
        # 一条要去查名录，一条要去改输入。
        #
        # 空串时拼接是恒等变换，所以「今天没有身份冲突」这条路一个字符都不多。
        _, review_note = _open_refusal_cases(
            review=review,
            settings=settings,
            scene_id=job.scene_id,
            refusals=resolution.identity_refusals,
        )
        return store.work_items(job.job_id), resolution.blocker + review_note

    # 回执来的主体，它的 ``account_id`` 是一个**派生值**：算得出来，不等于池子里真
    # 有那一行。「算得出来」与「真的折进去了」差着一次 ``promote_receipt`` —— 而那
    # 一次没做过的话，整条链会按一个不存在的账号安静地跑完（检索发生、证据落盘、
    # 路线算出来，而 ``get_account`` 一条都找不到）。这正是 §1 那条断点，只是主语
    # 从 ``row-*`` 换成了 ``acct-*``：换了个名字的空集还是空集。
    #
    # 判据住在 ``application/promotion.py``（一处定义），这里只把它的结论落成阻塞
    # 理由 —— 与 ``resolution.blocker`` 走**同一条**通道、同一个位置：到了
    # ``compile_work_items`` 就已经有工作单元了，而工作单元的失败形态是「worker 领走
    # 它、然后花掉真金白银」。
    #
    # 读一次证据库就够，而且是**读**（``read_store``）：库文件不存在就是「还没有任何
    # 一条证据」，也就是每一个主体都还没折进去 —— 那个答案在这条判据下是对的。
    #
    # **只对 ``RECEIPT_KINDS`` 那两类核。** 它们的 ``account_id`` 是算出来的，所以
    # 才有「算得出来但池子里没有」这个状态可核；``known_accounts`` 的 id 是从 operator
    # 的清单里照抄的，没有派生这一步，把 roster 拿去与账号池对账是**另一件事**
    # （「清单里的账号要不要先落进池子」）。那两件事的失败理由不同，混在一处会让这条
    # 判据的产出不再唯一 —— 理由的完整版写在 ``inputs.RECEIPT_KINDS`` 上。
    if job.input_kind in RECEIPT_KINDS:
        unpromoted = unpromoted_subjects(
            resolution.subjects, evidence=read_store(settings)
        )
        if unpromoted:
            lines = "\n".join(
                f"  - {account_id}：{reason}" for account_id, reason in unpromoted
            )
            return (
                store.work_items(job.job_id),
                (
                    f"作业 {job.job_id} 的 {len(unpromoted)} 个主体在账号池里不存在：\n"
                    f"{lines}\n"
                    "\n这些 id 是**派生**的（acct-<hex16>，由场地句柄来），不是原始主体名"
                    " —— 它们要先被折进池子，才会有账号、有证据、有路线。\n"
                    "处置：先对那一份回执跑一次 promote_receipt（它把场地与 canonical "
                    "账号一起写进去）。\n"
                    "整份拒绝，**不是**跳过那几行：一个 work item 都没有建出来，所以没有"
                    "任何一次检索会为它们发生。"
                ),
            )

    # 要冻住的**整份包**在这里取，而且**只在这里**：本函数是唯一一条把输入变成工作
    # 单元的路径（受理与 resume 共用同一份），所以放在这里等于「没有任何一条路径能
    # 编译出一批没有快照的工作单元」。放到两个调用点上，则每一处各有一次忘记的机会
    # —— 而忘记的后果是静默的：那批工作单元看起来完全正常，只是永远说不出自己
    # 针对谁、按哪一份规则在跑（§6：Running tasks retain their pinned guidance）。
    #
    # 传的是 bundle.scene_files 而不是 bundle.scene_version：那一份里同时住着版本
    # 与字节，所以「盘上钉了一个说不出指称对象的号码」在结构上写不出来。
    #
    # 读在**阻塞判据之后**：输入产不出来时走的是 BLOCKED 那条路，一次装载失败不该
    # 把一条本来只是「清单还没补齐」的路径变成一次错误。
    try:
        bundle = load_profile(settings)
    except ConfigError as exc:
        raise ToolError(f"Scene 装载失败，读不到要冻住的规则版本：{exc}") from exc

    return (
        store.compile_work_items(job.job_id, resolution.subjects, scene=bundle.scene_files),
        "",
    )


def _dispatch_block(
    settings: Settings,
    store: JobStore,
    dispatcher: Dispatcher | None,
    job: ResearchJob,
    *,
    step: str | None = None,
) -> dict[str, Any]:
    """这一次「把作业交出去」做了什么 —— **形状固定**，包括没有执行器的时候。

    形状固定不是洁癖：这个回执会被贴进工单与日志，而一个在故障路径上换形状的
    输出会让解析它的脚本恰好在那条路径上崩掉 —— 而那条路径正是它被写出来的理由
    （同 ``interfaces/mcp/__main__.py::_payload``）。

    四件事分开报，因为它们的四句处置不同：

    * ``executor_configured`` —— 这个进程有没有装起执行器。没有它的时候
      ``counts`` 全是 0 是**事实**，而不是「派了但都失败了」。
    * ``counts`` —— 三个取值各自的件数，恒有三格。重发一个已经派完的作业会得到
      「``counts`` 不为零、``handed_out`` 为 0」，这两个数字一起读才是
      「一件都没有被重新交出去」。
    * ``stopped_at`` / ``blocker`` —— 停在哪件上、为什么。途中的失败**不**回滚
      已经交出去的那几件（端口上没有 delete），所以「派了几件再停」是一个必须
      报得出来的状态。
    * ``board_convergence`` —— 这一趟把**已经结算的那些件**的看板推到了哪
      （``converge_settled``）。它与上面那三个数字是**两笔账**：一个作业可以先派
      5 件、只结算 1 件。它接在这里而不是接在结算那一刻，是因为这一趟问的是
      「外面现在对不对」—— 那个问题的答案在一次进程死掉之后仍然问得出来，而
      「上一次回传成功了没有」问不出来（本仓不记它，记了会撒谎）。

    **外部的标识不在这里。** ``work_item_id → dispatch_task_id`` 那条映射由 ``get_job``
    的 ``work_items[].execution`` 一处报告 —— 两处各报一次，漂移的那天调用方会
    拿到两个都像真的答案。

    ## ``step``：这一批工作是被**计划里的哪一步**交出去的

    §B16 关闭条件 7 的五个被选者里，执行器 是最后一段：这一趟真的去外部建/认领了
    任务，那件事要说得出服务的是哪一步。这一格因此与三条研究工具上那一格**同一个
    形状**、用**同一个** ``plan_step`` 校验，理由也一样 —— 没有它，一次派发在计划
    上是悬空的。

    收下的是**已经解析过**的那一步，不是调用方给的原文：解析在两个门口各自做一次
    （``plan_step(source_method, steps=plan_steps(require_bundle(settings)))``，
    与三条研究工具、``decisions.decide`` 逐字相同的那一句）。搬到这里做的话，
    一次被拒的受理会**先**落下一个作业与一批编译好的 work item、**再**报错，而那条
    错误里没有 job_id —— 调用方无从知道盘上已经多了一个自己叫不出名字的作业，
    换一个幂等键重发还会再造一个。拒绝在门口，等于不给这个状态任何存在的机会
    （与本模块 ``input_ref`` 那条逐字同一条理由）。

    门槛排在 ``dispatcher is None`` 那一格**之前**，理由在门口那一侧：给一个不在
    计划里的步骤是**这次调用**的问题，不是这个进程没装执行器的问题。两者混在一起
    会让「接线没到位」读起来像一次非法输入。

    ``DispatchReceipt`` 上**没有**加这一格，是刻意的：那份回执不在任何工具回执里
    （见上一段：连外部 id 都不在这里），加一个没有读者的字段，与「加字段必须同时
    给出读者」那条纪律相反。读者是这一块。
    """
    if dispatcher is None:
        return {
            "executor_configured": False,
            "counts": {outcome.value: 0 for outcome in DispatchOutcome},
            "handed_out": 0,
            "source_method": step,
            "stopped_at": "",
            # 那句话从能力表那一处取，不在这里另写一句：它已经把「没配」与
            # 「配了、但这个进程没装起来」分开了，而这两句的处置不同。
            "blocker": (
                _why_dispatch_not_ready(settings, dispatcher)
                + "所以这一批工作没有被交出去 —— 它们仍然是持久的 work item，"
                "执行器到位之后 control_job(action=resume) 会把它们派出去。"
            ),
            # 没接线时这一趟**没有跑**，不是「跑了、零件待收敛」。两个数字
            # （0 / 0）在这里长得一样，所以理由写在 blocker 里、计数照给。
            "board_convergence": _convergence_block(None),
            "note": _DISPATCH_NOTE,
        }

    work = WorkDispatch(jobs=store, dispatcher=dispatcher)
    round_ = work.dispatch_job(job.job_id)
    convergence = work.converge_settled(job.job_id)
    counts = round_.by_outcome
    return {
        "executor_configured": True,
        "counts": {outcome.value: counts[outcome] for outcome in DispatchOutcome},
        "handed_out": round_.handed_out,
        "source_method": step,
        "stopped_at": round_.stopped_at,
        "blocker": round_.blocker,
        "board_convergence": _convergence_block(convergence),
        "note": _DISPATCH_NOTE,
    }


def _convergence_block(convergence: BoardConvergence | None) -> dict[str, Any]:
    """派发回执里 ``board_convergence`` 那一块的形状。**一处写。**

    两个调用点（有执行器 / 没执行器）共用它，理由与 ``_dispatch_refused`` 一样：
    两处各搭一遍，漂移的那天调用方会以为它们是两种不同的东西。

    ``convergence is None`` = 这一趟没有跑（执行器没接线）。此时四个计数全是 0，
    而 ``items`` 是空表 —— 这三件事与「跑了、结果是一切到位」在数字上不可区分，
    所以**不许**只看数字：``executor_configured`` 在同一份回执里，它才是那句判词。
    把「没跑」也报成一个 ``BoardConvergence``（用一个假 job_id 之类）会让
    「这一趟读了外部 0 次」与「这一趟根本没读」变成一个东西。
    """
    if convergence is None:
        return {
            "ran": False,
            "considered": 0,
            "pushed": 0,
            "pending": 0,
            "unmapped": 0,
            "unsettled": 0,
            "items": [],
            "note": _CONVERGENCE_NOTE,
        }
    return {
        "ran": True,
        "considered": convergence.considered,
        "pushed": convergence.pushed,
        "pending": convergence.pending,
        "unmapped": convergence.unmapped,
        "unsettled": convergence.unsettled,
        "items": [
            {
                "task_id": sync.task_id,
                "target_status": sync.target_status,
                "observed_status": sync.observed_status,
                "synced": sync.synced,
                "changed": sync.changed,
                "error": sync.error,
            }
            for sync in convergence.syncs
        ],
        "note": _CONVERGENCE_NOTE,
    }


#: ``board_convergence`` 那一块怎么读。**它是一句话，所以它进回执而不是文档**：
#: 「已结算的件都到位了」与「这一趟一件都没扫到」在计数上长得一样（都是零），
#: 而后者可能意味着这个作业一件都还没做完。
_CONVERGENCE_NOTE = (
    "board_convergence 是这一趟之后**已经结算的那些件**的看板收尾：considered 是"
    "这一趟读了外部的件数，pushed 是这一次真的写动了看板的件数，pending 是还没到位的"
    "件数（逐件的原因在 items[].error 里）。considered + unmapped + unsettled 恒等于"
    "这个作业的件数 —— unmapped 是已结算但没有外部映射的（外面从来没有过这件事），"
    "unsettled 是还没结算的。它不是 handed_out 的补充：handed_out 数的是这一趟**交"
    "出去**几件，两个数字本来就不该相等。ran 为假表示这一趟没有跑（执行器没接线）。"
)


#: 派发那一段共用的注解。三格的读法本身就是一句话，所以它写在回执里而不是文档里：
#: 「有作业、没工作单元」与「一件都没交出去」在三个数字上长得一样，只有这句话能把
#: 它们分开。
_DISPATCH_NOTE = (
    "counts 三格恒在，它们的和是这一趟处理过的件数；handed_out 只数真的碰过外部的"
    "那两档（created + adopted）—— already_mapped 是上一趟就交出去的，这一趟连读"
    "都没读。blocker 非空时它说的是「为什么一件都没交出去」或「为什么停在这一件上」；"
    "三格全零、blocker 为空，意思是这个作业一件工作都没有。"
)


def _capabilities(
    *,
    settings: Settings,
    wired: frozenset[str],
    decisions: Mapping[str, DecisionProvider] | None = None,
    advisory: DecisionProvider | None = None,
    dispatcher: Dispatcher | None = None,
) -> dict[str, Any]:
    return {
        "integrations": _integrations(
            settings=settings,
            wired=wired,
            decisions=decisions,
            advisory=advisory,
            dispatcher=dispatcher,
        ),
        "research_only": settings.research_only,
        "send_authorized": settings.send_authorized,
        # 外发**路径**存不存在，与「能不能发信」是两件事，所以是两格。
        # ``outbound_capable`` 是**部署接线**（中继端点、发送箱、凭据、允许列表四样
        # 齐全），``outbound_missing`` 说得出来差哪几样 —— 一个「false」没有这一格
        # 就只是「不行」，运维照着补也无从补起（§6）。
        # 这两格**都不**动 ``send_authorized``：那一格是研究平面的常量，收窄的是
        # 「研究身份能不能发信」，不是「这个部署有没有那条路」。
        "outbound_capable": settings.outbound_capable,
        "outbound_missing": list(settings.outbound_missing),
        "version": __version__,
        # ``version`` 是**产品**版本（一个常量），``build`` 是**这一次的部署**：
        # 哪个 revision、有没有未提交的改动、什么时候读的。两个都要，因为它们
        # 答的不是同一个问题 —— 而只有后者能分辨「陈旧的进程」与「最新的进程」。
        "build": LOADED_BUILD.payload(),
        "note": (
            "research_only / send_authorized / outbound_capable 三格答的不是同一个问题，"
            "别把它们读成一格：前两格是**研究平面**的常量（研究身份能不能发信），第三格是"
            "**这个部署**有没有接上一条外发路径。outbound_capable 为 true 不等于这一批信"
            "可以出门 —— 每一波还要过人类授权与逐封的抑制/重复/新鲜度检查。"
            "\n"
            "last_live_verification 恒为 null：本构建没有任何一处记录过真实的活体校验。"
            "那不是数据缺失，是「从未发生」—— 一个恒绿的健康旗比没有旗更危险，"
            "它会让一次从未验证过的接线看起来是已验证的。\n"
            "build 那一格是**加载时**读的：它答的是「这个进程加载了哪份代码」，"
            "不是「现在盘上是什么」。dirty 为 null 是「没读出来」，不是「干净」——"
            "理由逐格写在那一格的 errors 里。反过来读也成立：一个正在跑的服务若连 "
            "build 这一格都没有，那它加载的是**这一格出现之前**的代码。"
        ),
    }


def _integrations(
    *,
    settings: Settings,
    wired: frozenset[str],
    decisions: Mapping[str, DecisionProvider] | None = None,
    advisory: DecisionProvider | None = None,
    dispatcher: Dispatcher | None = None,
) -> list[dict[str, Any]]:
    """七件套逐项报告。

    ``configured`` / ``runtime_ready`` / ``wired`` 是**三个**问题，不是一个问题：
    凭据配了吗、依赖装了吗、服务面有工具真的会调它吗。合成一个布尔值，就会
    把「配了 key 但还没有调用路径」报成「可用」—— 那一格曾经是本阶段最容易
    被误读的地方（Serper 的 key 在凭据库里，但服务面还没有调用它的工具）。

    三者**互不决定**，这一版正好两端都有实例：一个部署可以 ``configured: false``
    而 ``wired: true``（没配 key 但有工具会调它 —— 那个工具会明确回答「没接线
    检索」），也可以 ``runtime_ready: false`` 而 ``wired: true``（crawl4ai 没装，
    但 ``fetch_document`` 真的会问它能不能用）。所以 ``blocker`` 逐项拼，
    不写成「三者都真才算可用」。

    决策那两行不在这里写死，而是从 ``decisions`` 上的对象读（见 ``_decision_rows``）；
    dispatch 那一行的 ``runtime_ready`` 同源 —— 从 ``dispatcher`` 这个**对象**读，不从
    ``Settings`` 重推（见 ``_why_dispatch_not_ready``）。七件套里唯独这两族曾经是
    一句常量，而常量报的是**写它那天**的事实。
    """
    crawl_ok = crawl4ai_available()
    mineru_ok = mineru_available()
    return [
        {
            "name": "serper",
            "kind": "search",
            "configured": settings.serper_enabled,
            "runtime_ready": True,
            "wired": "search_web" in wired,
            "phase": "D",
            "last_live_verification": None,
            "blocker": _join_blockers(
                "" if settings.serper_enabled else "未配置 SERPER_API_KEY",
                "" if "search_web" in wired else "Phase D：服务面还没有调用检索的工具（search_web 未接线）",
            ),
            "detail": (
                "Serper 走 SearchProvider 端口。检索由 search_web 发起，"
                "额度按账号记在账本上，上限来自冻结的 profile（不是调用方的参数）。"
                "last_live_verification 恒为 null（理由见顶层 note）：它说的是"
                "「这一格没有记录源」，不是「还没跑过」。发过一百次计费调用也不改"
                "这一格 —— 一个随调用次数翻绿的健康旗比没有旗更危险。"
            ),
        },
        {
            "name": "http",
            "kind": "acquisition",
            "configured": True,
            "runtime_ready": True,
            "wired": "fetch_document" in wired,
            "phase": "D",
            "last_live_verification": None,
            "blocker": _join_blockers(
                "" if "fetch_document" in wired else "Phase D：抓取工具（fetch_document）尚未接线",
            ),
            "detail": (
                "httpx 是硬依赖，所以 runtime_ready 恒为真；它仍然占一格，"
                "因为这张表的契约是每一项都有这一格。抓取前会过 URL 判据"
                "（marketops/security/network.py），重定向逐跳检查。"
            ),
        },
        {
            "name": "crawl4ai",
            "kind": "acquisition",
            "configured": True,
            "runtime_ready": crawl_ok,
            # 两个入口都能调到它：``fetch_document`` 的 browser 模式 / auto 升级
            # 路径，以及 ``crawl_site`` 逐页走的那条同一个抓取入口。所以这一格
            # 问的是**有没有人调得到它**，而不是「哪个工具的名字出现在表里」。
            "wired": "fetch_document" in wired or "crawl_site" in wired,
            "phase": "E",
            "last_live_verification": None,
            "blocker": (
                "" if crawl_ok else f"crawl4ai 不可 import：{_clip(crawl4ai_unavailable_reason())}"
            ),
            "detail": (
                "匿名公开浏览：不登录目标站点、不求解 CAPTCHA、不绕过访问控制。"
                "fetch_document 在 mode=browser 时直接用它，mode=auto 时只在"
                "HTTP 结果可渲染失败时才升级（application/escalation.py）；"
                "crawl_site 的每一页同样经由那个入口，所以升级判定只有一份。"
            ),
        },
        {
            "name": "mineru",
            "kind": "document_parse",
            "configured": True,
            "runtime_ready": mineru_ok,
            "wired": "fetch_document" in wired,
            "phase": "D",
            "last_live_verification": None,
            "blocker": _join_blockers(
                _clip(mineru_fallback_blocker()),
                "" if "fetch_document" in wired else "Phase D：复杂 PDF 的 fallback 尚未接进抓取路径",
            ),
            "detail": (
                "可选依赖：缺失时 pipeline 仍可运行，只是复杂 / 扫描件 PDF 无法解析。"
                "它在 DocumentRouter 里是 fallback（轻量 parser 先试），"
                "fetch_document 的解析回执会写明有没有用到 fallback。\n"
                "blocker 说的是「能不能真的产出一份文档」，比 runtime_ready 严一格："
                "依赖装上了而调用点未校准，两者会给出不同的答案，"
                "而 default_router 按前者决定接不接 —— 三处读的是同一个判据。"
            ),
        },
        {
            "name": "dispatch",
            "kind": "dispatch",
            "configured": settings.dispatch_enabled,
            "executor": settings.dispatch_executor,
            # 从**对象**读，不从 Settings 重推：重推意味着「配置说什么」与
            # 「这个进程装起来了什么」有两份实现（与 decisions 同一条）。
            "runtime_ready": dispatcher is not None,
            "wired": bool(_DISPATCH_TOOL) and _DISPATCH_TOOL in wired,
            "phase": "G",
            "last_live_verification": None,
            "blocker": _join_blockers(
                _why_dispatch_not_ready(settings, dispatcher), _why_dispatch_not_called(wired)
            ),
            "detail": (
                "派发是 §6 的那条跨系统动作：把一件工作单元交给执行器，并维持"
                "那条映射（application/dispatch.py）。走的工具是 "
                "``submit_research``（受理那一刻）与 ``control_job(action=resume)``"
                "（恢复那一刻）—— 触发它的是**控制面**，执行它的是 worker 侧。\n"
                "执行器只有两种取值，都在本仓里：``local``（默认）是一张本机 "
                "SQLite 任务板（adapters/local_board.py），服务进程写、runner 进程领，"
                "跨进程可见而并发靠事务；``none`` 是一个写下来的决定，表示这个部署"
                "不派发。**没有第三种取值**，也没有外部工作区可配 —— 那条路已经在 "
                "Issue #2 里整段移除，缺执行器时本仓拒绝派发，从不回退。\n"
                "last_live_verification 恒为 null：派发过一百件工作单元也不改这一格 —— "
                "它要的是有人真的把「派发 → 领取 → 结算」这条链跑通并记下来。"
            ),
        },
        *_decision_rows(decisions, wired, advisory),
    ]


#: 会走到 ``WorkDispatch`` 的那条工具名 —— **新受理的作业**从这里出去。
#:
#: 它曾经是空串，而空串是那条事实的可执行形式：工具目录里**没有任何一条**会派发。
#: H2b 之后不再成立：``submit_research`` 受理并编译之后立刻把这一批工作交出去，
#: ``control_job(resume)`` 重走同一条路（同一个 ``_dispatch_block``，不是第二个
#: 实现）。这个常量报的是**新活从哪出去**的那一条；恢复走的也是它。
#:
#: 它由 ``tests/test_mcp_control_tools.py`` 的一条判据盯着：那条判据扫
#: ``marketops/`` 里 ``WorkDispatch`` 一族名字的引用者，并断言
#: ``bool(引用者) == bool(_DISPATCH_TOOL)`` —— 谁把派发接上、谁就要同时把名字
#: 填进来，否则能力表会一边报 ``wired: false`` 一边有调用路径。
_DISPATCH_TOOL = "submit_research"


def _why_dispatch_not_ready(settings: Settings, dispatcher: Dispatcher | None) -> str:
    """这一行为什么不是「就绪」；是就绪时返回空串。

    两句话对应**两件事**（``_why_not_called`` 的第 1、2 种是同一个形状）：
    「本部署选择了不派发」的处置是改一个环境变量，而「配置说要派发、这个进程
    没把执行器装起来」的处置是看服务面是怎么构造的 —— 合成一句会让运维去改
    一个已经改好的东西。
    """
    if not settings.dispatch_enabled:
        return (
            f"本部署选择不派发（MARKETOPS_DISPATCH_EXECUTOR={settings.dispatch_executor!r}），"
            "所以派发在这个部署上不存在。"
        )
    if dispatcher is None:
        return (
            "配置说这个部署要派发，但**这个进程**没有把执行器构造出来 —— "
            "与「选择不派发」是两件事。"
        )
    return ""


def _why_dispatch_not_called(wired: frozenset[str]) -> str:
    """派发路径为什么不会被调用；会被调用时返回空串。

    两种「为假」要分得开，因为处置不同：「没有工具落点」是**这个版本**没有入口
    （已经不再成立，只在一个把 ``_DISPATCH_TOOL`` 改回空串的构建上出现），
    「工具尚未接线」是**这个进程**没把那条工具注册上来（``jobs`` 为 ``None``）。
    合成一句会让运维去改一个没有东西可改的地方。
    """
    if not _DISPATCH_TOOL:
        return (
            "派发路径没有工具落点（_DISPATCH_TOOL 是空串）。"
            "这不是「工具在、只是没接线」—— 工具目录里没有一条会走到 "
            "WorkDispatch，§6 的派发在**这个构建**里没有入口。"
        )
    if _DISPATCH_TOOL not in wired:
        return f"工具 {_DISPATCH_TOOL!r} 在这个进程里尚未接线"
    return ""


def _decision_rows(
    decisions: Mapping[str, DecisionProvider] | None,
    wired: frozenset[str],
    advisory: DecisionProvider | None,
) -> list[dict[str, Any]]:
    """决策后端的行 —— **判据全部取自 provider 自己**。

    这一格曾经是写死的 ``configured: False``。写死的问题不是它当时不对（当时确实
    没接线），而是它**不会变**：接线之后那两行照旧报「尚未接线」，于是「哪一天该
    把它改过来」变成一件要靠人记得的事。一个恒假的健康旗与一个恒真的健康旗是同
    一类东西 —— 它们都不随事实变化。

    现在 ``configured`` / ``runtime_ready`` / ``blocker`` 逐格读 provider 的属性。
    三者仍然互不决定：今天本机就是「``configured`` 为真、``runtime_ready`` 为假」
    （SDK 装了，钥匙没配）—— §20：缺凭据不得写 PASS。

    ## ``wired`` 在这一族上是**逐后端**的，不是逐工具的

    其余五行问的是「有没有工具会调这一类集成」，所以一个 ``"decide" in wired``
    就够了。决策这一行不行：``decide`` 只咨询**一个**后端（``assist`` 是单臂
    模式，见 ``interfaces/composition.advisory_decision_provider``），另一个今天
    根本没有调用路径。给两行都写 ``True`` 等于说「两个后端都会被调用」，而那件
    事同时是错的、且是这张表最容易被误读的一格（读成「两个都能用」）。

    所以 ``wired`` 认的是**对象同一性**：``decide`` 会调的那一个恰好就是
    ``advisory`` 指向的对象。判据用 ``is`` 而不是 ``name``，因为
    ``advisory_decision_provider`` 是从同一份 ``decisions`` 里取出来的 ——
    一个同名但不是同一个对象的后端意味着这份表与接线用的不是同一批东西，
    而那正是 §11 要禁止的「两个后端两条路径」。

    ## 三种「没有调用路径」是**三件事**

    1. ``decisions is None`` —— 本实例压根没构造后端（配置可能配了，没装起来）。
    2. ``advisory is None`` —— 后端构造了，但本部署没指定辅助后端（``§4`` 要求那
       是一个显式的部署决定，不是「随便挑一个就绪的」）。
    3. ``advisory`` 是别的后端 —— 它没被选中。

    合成一句「未接线」会让运维去接线一个已经接好的东西。第 2 种尤其不能省：它
    的处置是**加一个环境变量**，而第 3 种的处置是**别动它**。
    """
    if advisory is not None and (
        decisions is None or decisions.get(advisory.name) is not advisory
    ):
        # 这条守卫守的是一个**表会说谎**的状态，不是一次手滑。表按对象同一性认
        # 「谁会真的被调用」，所以传进来的 advisory 若不在这份表里，每一行都会
        # 报 ``wired: false`` —— 而 decide 照样会调它。那时「这张表说没有调用
        # 路径」与「调用路径正在跑」同时成立，且没有任何一处看得出来。
        raise ConfigError(
            f"advisory 指向的 {advisory.name!r} 不在这份 decisions 里"
            f"（{'没有传决策后端' if decisions is None else sorted(decisions)}）。"
            "表按对象同一性回答「decide 会调用谁」，所以这两个参数必须来自同一次构造。"
        )
    if decisions is None:
        return [
            {
                "name": name,
                "kind": "decision",
                "configured": False,
                "runtime_ready": False,
                "wired": False,
                "phase": "F",
                "last_live_verification": None,
                "blocker": (
                    "本实例没有构造决策后端。这与「没配凭据」是两件事 —— "
                    "配置里可能配了，但这个进程没把它装起来。"
                ),
                "detail": _DECISION_DETAIL,
            }
            for name in _DECISION_NAMES
        ]
    return [
        {
            "name": provider.name,
            "kind": "decision",
            "configured": provider.configured,
            "runtime_ready": provider.ready,
            "wired": "decide" in wired and provider is advisory,
            "phase": "F",
            # 永远为 null，直到真的有过一次活体判定并留下记录。§2 逐字：这一格
            # 报的是「上次校验是多久以前」，不得是一个恒绿的健康旗。今天没有任何
            # 一处记录过真实的活体校验，所以它是 null —— 那不是数据缺失，是
            # 「从未发生」（与 _capabilities 的 note 是同一句话）。
            "last_live_verification": None,
            "blocker": _join_blockers(provider.blocker, _why_not_called(wired, advisory, provider)),
            "detail": _DECISION_DETAIL,
        }
        for _, provider in sorted(decisions.items())
    ]


def _why_not_called(
    wired: frozenset[str], advisory: DecisionProvider | None, provider: DecisionProvider
) -> str:
    """这个后端**为什么不会被 decide 调用**；它被调用时是空串。"""
    if "decide" not in wired:
        return "Phase F：服务面还没有调用决策后端的工具（decide 未接线）"
    if advisory is None:
        return (
            "本部署没有指定辅助后端（MARKETOPS_DECISION_PROVIDER 未设置），"
            "所以 decide 不咨询任何后端 —— 这不是「它不可用」，"
            "加一个环境变量就是这条路径的处置。"
        )
    if advisory is not provider:
        return f"本部署的辅助后端是 {advisory.name!r}，decide 只咨询它。"
    return ""


#: 没有决策后端时表上仍要有那两行 —— 少一行读出来是「它不在表上」，而不是
#: 「它没就绪」。名字写死在这里是为了那一刻仍然答得出「有哪几个本该在」。
_DECISION_NAMES = ("jev", "laya")

_DECISION_DETAIL = (
    "两个后端同处一个 DecisionProvider 后面（§11），题面与回执的线上形状相同，"
    "所以那份翻译只有一份（ports/decisions.py）。decide 只以 **assist** 调用它们："
    "影子是另一条路径，且**没有 MCP 入口**（§4：影子答案不交给研究侧）。"
)


def _join_blockers(*parts: str) -> str:
    return "；".join(part for part in parts if part)


def _clip(text: str, limit: int = 200) -> str:
    """截断**并说明**截断过。

    静默截断在这里的表现是「依赖没装」的理由看起来像一句完整的话 ——
    而真正的缺失部分恰好是最能指出该装什么的那一段。
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（已截断，原文 {len(text)} 字符）"


# ----------------------------------------------------------------------
# get_scene
# ----------------------------------------------------------------------
def require_bundle(settings: Settings) -> ProfileBundle:
    """本部署的 profile 规则，或一条拒绝。

    与 :func:`require_scene` 一起是从 ``_scene`` 里抽出来的：两个函数回答的是
    「这次调用说的是哪个场景」的**两半** —— 名字对不对，以及那个名字底下的规则
    装不装得出来。``product_loop.py`` 里那三条场景工具两半都要问（立项要按 profile
    的排除清单核对、改版要继承上一版并重新核对），而各写一份的代价不是重复三行，
    是**同一个失败在两条路径上有两种说法**：这里说「Scene 装载失败：…」，
    那里说一句 pydantic 的堆栈。

    那个模块里另有两条**只问后半段**：``import_motherpool`` 与 ``list_accounts``
    的对象不是某个 Scene（回执与清单都不是按 Scene 存的），它们要的只是这份部署
    的规则 —— 排除声明与 grading 词表。所以它们在**一个不立项的部署里照样能用**，
    这是有意的：一个市场的第一步恰恰是「先看看手上这份池子」。

    ``profile`` 装载失败是**部署**问题，但它必须作为一条结果回到调用方：抛出去会
    被 SDK 换成一句通用的 ``UnexpectedToolError``，真实原因留不下来。

    ## 这个模块里还剩三处直接 ``load_profile``，那三处是**故意**的

    ``compile_work_items`` 之前那处说「读不到要冻住的规则版本」，``_assignment``
    那处说「读不到这份 assignment 的规则」，``_budget`` 那处**不抛**、把话写进
    ``blocker`` 一格。它们的措辞各不相同，而不同的不是文案：每一句都点明了**那次
    读是想拿它做什么**。折进这里会让三条路径共用一句「Scene 装载失败：…」——
    看着更整齐，代价是调用方再也读不出是哪一件事没做成。所以判据是「这句话是不是
    与这里逐字相同」，不是「有没有调 ``load_profile``」：``grep load_profile`` 在
    这个模块里仍会数出几处，其中一处就是这个函数自己。

    ``pinned_bundle`` **不在这里列举**：它不读盘上那一份，它收下调用方读好的
    ``effective``，只回答「钉住的那一份是不是它」—— 多读一次盘就会在同一次回执里
    造出两个时刻（见那个函数的 docstring）。
    """
    try:
        return load_profile(settings)
    except ConfigError as exc:
        raise ToolError(f"Scene 装载失败：{exc}") from exc


def plan_steps(bundle: ProfileBundle) -> tuple[str, ...]:
    """这一轮允许从哪儿拿行 —— 计划里 ``strategy.source_methods`` 那一格的取值，
    或者一条拒绝。

    定义的**唯一**出处是 ``application/research_strategy.py::plan_source_methods``
    （连同它为什么是「无场景收窄」那一份、以及方向只往严的一侧错的论证）。这里只做
    一件事：把那个函数抛出来的 ``ConfigError`` 翻成 ``ToolError``。读不到这一格时
    **拒绝**，不退成一张空表 —— 一张空表会让「这份 Scene 说不出这一轮的计划」与
    「这一轮计划里没有步骤」在回执上同形，而后者是一个不可能的状态（声明至少开一
    条路，见 ``_source_methods_cell`` 的 ``if not values``）。

    ## 它为什么住在这里，而不是 ``_shared.py``

    三个调用点都要它：本模块的 ``_assignment``（把这一格交给 worker）、
    ``research.py`` 的三条工具（校验 worker 声称的那一步）、``decisions.decide``。
    而 ``research.py`` 与 ``decisions.py`` 都已经 import 本模块（``require_bundle``），
    所以放在这里，三个调用点各付一行 import。

    ``_shared.py`` 是更自然的家（那份 docstring 自己写着那一条纪律），但它不行：
    它 import 得到 ``application.research_strategy``，而那个模块经 ``ports.acquisition``
    够得到外部调用契约 —— ``_shared.py`` 在 ``product_catalog.py`` 的 import 闭包里，
    而 ``tests/test_registration_is_offline.py`` 要求登记面够不到任何这样一条路。
    放进 ``research.py`` 更不行：本模块 import 它，反过来成环。
    """
    try:
        return plan_source_methods(bundle)
    except ConfigError as exc:
        raise ToolError(
            f"这一步所属的计划读不出来：{exc}\n"
            "「这次调用服务计划里的哪一步」那一格要拿它比对，而读不到计划时**不退回**"
            "「都可以」—— 退回之后那一格就从「计划里的一步」退化成「一个拼写正确的"
            "字符串」，反查答得出来、却答的是一个不存在的步骤。这是**部署**问题"
            "（这份 Scene 的声明开不出一条路），不是这次调用的问题。"
        ) from exc


def require_known_step(value: str) -> str | None:
    """第一半：这一格**在闭集里**吗 —— 通过时返回归一化后的 id，空着时返回 ``None``。

    它**不读盘**：闭集就是 ``domain/sources.py::SOURCE_METHODS`` 那张注册表，而它
    正是 ``EffectiveResearchPlan.source_methods`` 的取值域 —— 校验复用写入侧同一个
    ``resolve_source_method``（读取层只能**更严**不能更松，与 :func:`_canonical` 逐字
    相同的理由）。

    ## 为什么它单独是一个函数（两个时刻）

    因为「名字拼错了」与「这一轮的计划里没有它」是**两件事**，它们的处置时刻不同：

    * 名字拼错是**这次调用**的问题 —— 它必须在任何部署判据**之前**说出来。排在
      「本部署没接检索提供方」之后的话，调用方按那句话去配了 ``SERPER_API_KEY``，
      配好之后同一句拒绝会再出现一次，而两次它都不是真正的原因。
    * 「这一步不属于这一轮的计划」是**这份 Scene** 的问题 —— 它要读声明，所以要
      排在部署判据之后（见 ``research.py`` 三条工具的调用点与那里的注释）。

    两半都仍在**预留额度之前**。返回值与 :func:`plan_step` 同一形状（归一化 id 或
    ``None``），因为第二半正是拿它去比对。
    """
    text = (value or "").strip()
    if not text:
        return None
    try:
        return resolve_source_method(text).id
    except ConfigError as exc:
        raise ToolError(
            f"{exc}\n这一格是「这次调用服务计划里的哪一步」，所以它只接受注册过的"
            "来源方法 id —— 与 `EffectiveResearchPlan.source_methods` 同一张表，"
            "也正是「反查是谁因哪一步被选择」那条反查的锚点。**不接受一个自由的名字**："
            "编出来的步骤名会让那条反查答出一个不存在的步骤，而报告上看不出异常。"
            "这次调用如果确实不属于任何 plan 步骤，就把这一格留空。"
        ) from exc


def plan_step(value: str, *, steps: tuple[str, ...]) -> str | None:
    """这次调用声称的**计划步骤** —— 通过时返回归一化后的 id，空着时返回 ``None``。

    §B16 关闭条件 7 逐字要的是「Serper / HTTP / Crawl4AI / Jev / 执行器的调用能反查
    是谁因哪一个 plan 步骤被选择」。反查要有一格的起点，而这一格在适配器上原本
    **不存在**：一个自由字符串说得出「这次搜的是 contact 页」，说不出「这次搜的是
    计划里的哪一步」，于是每一条 provider 调用在那份计划上都是悬空的。

    ## 两道判据，不是一道

    * **这一格在闭集里**（:func:`require_known_step`）—— 硬编码的来源方法注册表。
    * **这一步在这一轮的计划里**（``steps``）—— 闭集只有五个成员，而一份声明开得出
      的通常更少。只有第一道时，任何**注册过**的名字都能被声称成计划的一步，而
      「反查是谁因哪一步被选择」答出来的是一句没人认过的话。

    第二道判据从调用方收下 ``steps`` 而不是自己去读声明：读法只有一个出处
    （:func:`plan_steps`），而这个函数因此是一个纯函数 —— 不读盘、不问后端，判据
    可以在没有部署的地方跑。

    空串是**合法**的，而且它有话说：这次调用没有声称服务任何 plan 步骤（发现期，
    或一次由人驱动的临时取证）。它不是缺省、更不是 ``None`` 兜底 —— §B16 关闭条件 7
    的后半句逐字「没需要的 provider 不为『集成完整』而强制调用」，所以这一格不许有
    默认值。回执里 ``None`` 与 ``"search.engine.v1"`` 是两句不同的话：前者是「没有
    声称」，后者是「声称了，而且这一条是真的」。

    Raises:
        ToolError: 给了一个不在闭集里的 id，或者给了一个在闭集里、而这一份声明没有
            给它入口的 id。**两条都是拒绝，都不落成 ``None``** —— 一个拼错的步骤名
            若被读成「没有声称」，那条调用在报告上就与「确实没声称」同形，而它们要查
            的是两件相反的事（一处接线错误 vs 一次刻意的临时取证）；一个「注册过、
            但计划里没有」的名字被读成 ``None``，那次可追溯性失效就完全不留痕。
    """
    resolved = require_known_step(value)
    if resolved is None:
        return None
    if resolved not in steps:
        raise ToolError(
            f"这一步不在这份 Scene 这一轮的计划里：{resolved!r}。\n"
            f"这一轮允许的有：{list(steps)}。\n"
            f"{resolved!r} 是注册过的来源方法（`domain/sources.py` 里有它），但**没有任何"
            "一份声明给它入口** —— 声明侧只有三块能开出路：`search.query_templates` 开"
            "`search.engine.v1`，`page_discovery_hints` 开站内检索与站点抓取。所以这一格"
            "不只是「名字拼对了」，它是「这一步真的属于这一轮的计划」。\n"
            "**拒绝而不是读成空**：读成空之后，「worker 声称了计划里没有的一步」在报告上"
            "与「这次调用不属于任何步骤」同形 —— 而前者正是一次可追溯性失效（§B16 关闭"
            "条件 7）。这次调用如果确实不属于任何 plan 步骤，就把这一格留空；如果它属于"
            "一条本仓还没给它入口的路，那是**声明**要改，不是这一格要放宽。"
        )
    return resolved


def pinned_bundle(
    settings: Settings,
    *,
    effective: ProfileBundle,
    pinned: PinnedScene | None,
    work_item_id: str,
) -> ProfileBundle:
    """这件工作**编译时钉住**的那一份规则；读不出来就拒。

    §6 逐字：Running tasks retain their pinned guidance。在判定与结算这两条路上，
    这句话要落成「**这次判定/这次结算执行的是钉住的那一版题面**」—— 从前那两条路
    读的是**调用刹那盘上**的 profile，于是编译之后有人改了 profile，一件在飞的工作
    会在另一份规则下被判定，而回执上写着的是钉住的那一版（见本模块
    ``get_assignment`` 回执里那段说明）。这个函数是那两条路唯一该走的入口。

    ## 为什么 ``effective`` 由调用方递进来

    一次调用里只能有一个「**盘上现在这一份**」。让这里自己 ``load_profile`` 会在
    一次回执里造出两个时刻：调用方那块报 ``scene`` 用的是第一次读的，这里用的是
    第二次读的 —— 而两次之间那个窗口恰好是 profile 被改动的瞬间，也就是版本号
    唯一要紧的时刻（``_scene`` 上面那条注释说的是同一件事）。

    ## 四种输入，三种处置。**没有第四种回落**

    * ``pinned is None``（这件工作没有钉）⇒ **拒**。一件说不出自己按哪一份规则在跑
      的工作，不该被判定或结算 —— 与 ``get_assignment`` 拒「没有冻住的快照」是同一
      句话，因为它们是同一条 §6 规则。它是在钉住这个机制出现之前编译的。
    * 钉住的字节在，且与 ``effective`` **同版本** ⇒ 直接用 ``effective``。
      ``SceneFiles`` 按内容寻址，版本相等就是同一批字节，没有必要再装一遍。
    * 钉住的字节在、版本不同 ⇒ **用钉住的那一批装载**。这正是本函数存在的理由。
    * 读不到字节、而钉住的那一版**就是**盘上现在这一份（版本号相等）⇒ 用
      ``effective``。这是历史行的正控（``marketops-runtime`` 那个库里真的有这样
      几行：回填进 ``scene_snapshot`` 之前只有版本），版本号是内容的摘要，相等
      就是同一批字节，所以「它按哪一份规则跑」照样答得出。
    * 读不到字节、盘上这一份又**不是**它 ⇒ **拒**，并说清是哪一件工作、钉的是哪
      一版、现在生效的又是哪一版。

    回落到盘上那一份为什么会坏：判定会按**另一份**题面执行，而记录上写着的是钉住
    的那一版 —— 「同一件在飞的工作，规则已经换了」这件事在报告上就此消失。

    ``get_assignment`` 走的是同一个函数（那里先问一次，所以正常路径上到这里已经是
    第二次问同一件事）：各写一份的代价不是重复几行，是漂移 —— 而漂移的那一次会让
    一件在派发侧被拒的工作在判定侧被放行。

    Raises:
        ToolError: 上面那两条拒绝，以及钉住的那批字节**在、却装载不出来**（那批
            字节不来自一个完整的 profile 目录，或其中的声明自己写错了）。三条都是
            **部署**问题：与这一次调用无关，处置是还原 profile 或重新受理。
    """
    if pinned is None:
        raise ToolError(
            f"work item {work_item_id} 没有钉住的 Scene 版本，所以这次没有可执行的规则。\n"
            "§6 逐字：Running tasks retain their pinned guidance —— 一件说不出自己按"
            "哪一份规则在跑的工作，不能被判定、也不能被结算。\n"
            "它是在钉住这个机制出现之前编译的（正常路径上不会出现这一格：编译会把版本"
            "与工作单元写在同一个写事务里）。重新受理一次是**另一个作业**。"
        )
    if pinned.files is not None:
        if pinned.files.version == effective.scene_version:
            return effective
        try:
            return load_profile_from_scene_files(pinned.files, settings=settings)
        except ConfigError as exc:
            raise ToolError(
                f"work item {work_item_id} 钉在 {pinned.scene_version}，那一批字节还在，"
                f"但装载不出来：{exc}\n"
                "「字节还在」说的是它们就是当时那一批，**不是**说它们是一份能用的规则 "
                "—— 这一段不来自一个完整的 profile 目录，或者其中的声明本身写错了。"
                "这与「字节没有留下来」是两件事：那一种要去还原盘上的目录，这一种"
                "拿到的就是当时那批字节，坏的是它们自己。\n"
                f"（盘上现在生效的那一份是 {effective.scene_version}。两个版本都写出来，"
                "是因为处置的第一步就是判断「盘上这一份是不是它」—— 只说钉住的那一版，"
                "读的人还得另外去问一次现在生效的是哪一版。）\n"
                "要恢复：把 profile 目录还原成那个版本，或者重新受理一次"
                "（那是**另一个作业**）。"
            ) from exc
    if pinned.scene_version == effective.scene_version:
        return effective
    raise ToolError(
        f"work item {work_item_id} 钉在 {pinned.scene_version}，"
        f"但那一批字节没有留下来，而盘上现在这一份是 {effective.scene_version}。\n"
        "别的组合都答得出「它按哪一份规则在跑」，只有这一种两头落空：快照里没有那批"
        "字节，盘上这一份又不是它 —— 于是**没有任何东西**说得清这件工作当时按什么在跑。"
        "按一件谁也读不出规则的工作继续做下去（派发、判定或结算），比拒绝它坏得多。\n"
        "要恢复：把 profile 目录还原成那个版本，或者重新受理一次（那是**另一个作业**）。"
    )


def _scene(
    settings: Settings,
    scene_id: str | None,
    *,
    scenes: Callable[[], SceneStore] | None = None,
) -> dict[str, Any]:
    require_scene(settings, scene_id)
    bundle = require_bundle(settings)

    return {
        "scene_id": settings.profile,
        "profile_id": bundle.profile_id,
        "status": bundle.status,
        # §2 逐字要的是 "effective policy/guidance version" —— 这个字段就是它。
        # 它由**装载器**算出并挂在 bundle 上，不在这里另算一遍：另算的那一遍读的
        # 是「调用刹那」的目录，而规则是更早那一刻读出来的，于是工具会报出一个
        # 并不描述它返回的这套规则的版本 —— 一个在文件正好被改的瞬间才现形的错，
        # 而那个瞬间恰好是版本号最要紧的时候。
        "scene_version": bundle.scene_version,
        # **那个版本号指称的字节**分别是哪几个文件。只报一个版本号时，
        # 「钉住了这一份规则」与「钉住了一个号码」在回执里长得一样 —— 而这两件事的
        # 区别只在有人问「当时那份规则到底写了什么」时才显形，那已经是事后了。
        # 正文不塞进这一格（这里读的是规则，不是一份 YAML 转储）；逐文件的摘要
        # 让**手里有这些文件的人自己验**，不必相信这一行字。
        "files": [
            {"path": entry.path, "sha256": entry.sha256, "bytes": len(entry.content)}
            for entry in bundle.scene_files.entries
        ],
        "summary": bundle.summary(),
        "has_unknown_rules": bundle.has_unknown_rules,
        "unknowns": list(bundle.unknowns),
        "note": (
            "summary 与装载层给的**是同一份**（ProfileBundle.summary），不是这里另算的一遍 —— "
            "两个来源各算一次必然先漂移一处，而漂移的那一处会表现为「工具说这套规则可用、"
            "引擎说它求不了值」。\n\n"
            "scene_version 是装载器对 profile 目录里**每一个文件**的字节算出的摘要。"
            "它覆盖的不只是这三个 YAML：目录里将来加进的任何指引文件都会自动改变它 —— "
            "所以「指引换了、版本却没动」不会发生。\n\n"
            "files 就是这条摘要覆盖的那批文件本身：路径、**内容的**摘要、字节数。"
            "拿它去比手里的文件，验的是「我读的这一份是不是它说的那一份」—— "
            "这件事只有核对的人自己能做，所以这里给的是可核对的依据而不是一句保证。"
        ),
        **_scene_record(settings, scenes, settings.profile),
    }


#: ``record_state`` 的四个取值。**四个，因为它们是四件事**：
#:
#: * ``not_wired`` —— 这个服务实例没有接场景存储。它是「这个部署不问那件事」，
#:   与「库打不开」处置不同（后者是停机）。
#: * ``no_store`` —— 接了，但库文件还不存在。这是「还没有写过任何东西」的诚实
#:   形态。**读一次不建库**：一次 ``get_scene`` 顺手建出一个空库，会让「还没有
#:   任何产物」与「这里本该有东西」在磁盘上长得一样（``read_store`` 同款判据）。
#: * ``absent`` —— 库在，但这个 Scene 没有记录。这是「profile 目录里有规则、
#:   而这个部署还没有立项过」。
#: * ``present`` —— 有记录。
#:
#: 合成一个布尔（``has_record``）会把前三件事压成同一格，而它们的处置是三件
#: 不同的事：一个要接线，一个要立项，一个要检查是不是写错了 Scene 名。
_SCENE_RECORD_NOT_WIRED = "not_wired"
_SCENE_RECORD_NO_STORE = "no_store"
_SCENE_RECORD_ABSENT = "absent"
_SCENE_RECORD_PRESENT = "present"


def _scene_record(
    settings: Settings,
    scenes: Callable[[], SceneStore] | None,
    scene_id: str,
) -> dict[str, Any]:
    """这个 Scene **落盘的那一份记录**，与它缺席的方式。

    ## 为什么它必须和 ``get_scene`` 的其余部分一起回

    上面那一块答的是「profile 目录里现在写着什么规则」；这一块答的是「这个市场
    被立项成了哪一版、哪一版在生效、谁批的」。两者会不一致（写完一版还没批、
    或者批完之后 profile 又改过），而**不一致本身是要被看见的事实**。分两个工具
    读，调用方就得自己把两次读到的时刻对齐 —— 那件事它做不到。

    没有记录时仍然回这一块，且 ``record`` 是 ``null``：把这一格整个省掉，会让
    「还没立项」与「这个构建没有这个字段」在回执里长得一样。
    """
    if scenes is None:
        return {
            "record": None,
            "record_state": _SCENE_RECORD_NOT_WIRED,
            "record_note": (
                "这个服务实例没有接场景存储，所以「立项成了哪一版」这一格读不出来。"
                "这不是「没有立项过」—— 上面那一块读的是 profile 目录里的规则，"
                "与是否立项过无关。"
            ),
        }
    if not settings.database_path.is_file():
        return {
            "record": None,
            "record_state": _SCENE_RECORD_NO_STORE,
            "record_note": (
                f"还没有写过任何东西（{settings.database_path.name} 尚未创建），"
                "所以这个 Scene 还没有立项过。这是一个正常答案，不是读不出来。"
            ),
        }

    record = scenes().get_record(scene_id)
    if record is None:
        return {
            "record": None,
            "record_state": _SCENE_RECORD_ABSENT,
            "record_note": (
                f"库在，但没有 {scene_id!r} 的立项记录。用 create_scene 写下第 1 版。"
            ),
        }
    return {
        "record": {
            "scene_id": record.scene_id,
            "workspace": record.workspace,
            "state": record.state.value,
            "latest_version": record.latest_version,
            "effective_version": record.effective_version,
            "pending_version": record.pending_version,
            "accepts_new_work": record.accepts_new_work,
            "pause_reason": (
                record.pause_reason.value if record.pause_reason is not None else None
            ),
            "revision": record.revision,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "approvals": [
                {"version": item.version, "at": item.at.isoformat(), "by": item.by}
                for item in record.approvals
            ],
        },
        "record_state": _SCENE_RECORD_PRESENT,
        "record_note": (
            "record 是**落盘**的那一份：它答「立项成了哪一版、哪一版在生效、谁批的」，"
            "而上面那一块答「profile 目录里现在写着什么规则」。两者不一致时，不一致"
            "本身是要看的 —— effective_version 落后于 latest_version 表示有改完还没批的"
            "一版；而 profile 换了一版之后，已生效的那一版绑的仍是当时的 profile_version。"
        ),
    }


# ----------------------------------------------------------------------
# get_account / get_evidence
# ----------------------------------------------------------------------
def _account_key(value: str) -> str:
    """校验一个账号 id 并返回它。

    ``slugify(id) == id`` 是判据的全部：证据是按 ``slugify(account_id)`` 当
    文件名定位的，所以一个**需要被规范化**的 id 会静默读到另一个账号的证据
    （``Foo`` 与 ``foo`` 是同一份文件），而报告上只会显示「证据更多了」。
    拒绝一切需要规范化的输入，就不存在「还有哪一种写法能绕过去」这个问题。

    **判据不在这里实现。** 它住在 ``domain/util.check_canonical_id``，与写入侧
    （``Evidence`` / ``FetchRequest`` / ``SearchQuery``）用的是同一个函数 ——
    读取层只能**更严**不能更松，而"更严"若靠另写一遍来达成，两份必然先漂移一处，
    漂移的那一次会让读到的与写下的不是同一套规则。这里只做一次翻译：
    ``ValueError`` → ``ToolError``，因为服务面的失败要走工具结果，不走异常链。
    """
    try:
        return check_canonical_id(value or "", "account_id")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc


def read_store(settings: Settings) -> SqliteEvidenceStore | None:
    """打开只读用的存储；库文件还不存在时返回 ``None``。

    **读一次不建库。** 一次 ``get_account`` 顺手建出一个空库，会让「还没有
    任何产物」与「这里本该有东西」在磁盘上长得一样 —— 而后者正是需要被看见的
    那种故障。库文件不存在是「还没有证据」的诚实形态。

    库存在但不可写 / schema 版本不符时，``SqliteEvidenceStore.__init__`` 会在
    构造点报错（那里是唯一能回答「这个位置能不能写」的时刻）。

    **公开的**（名字前不再有下划线）是因为第二个模块开始用它：产品环的
    ``list_accounts`` 要报每个账号的路线与证据条数，而它的判据与这里逐字相同
    —— 库不在就是「还没有产物」。另写一份的后果不是多三行，是两条路径对
    「库不存在」给出两种回执，其中一种会被读成「这个账号什么都没有」。
    """
    path = settings.database_path
    if not path.is_file():
        return None
    return SqliteEvidenceStore(path)


def write_store(settings: Settings) -> SqliteEvidenceStore:
    """打开**可写**的存储；库文件不存在就建出来。

    与 :func:`read_store` 的差别只有一条，而它是两条路径各自的正确行为：

    * 读一次**不建库** —— 一次 ``get_account`` 顺手建出一个空库，会让「还没有任何
      产物」与「这里本该有东西」在盘上长得一样，而后者正是需要被看见的那种故障；
    * 写一次**必须建库** —— 一次要落证据的调用如果因为库文件还不存在而拒绝，那么
      第一个证据永远落不了盘（而它恰恰是「这个部署第一次真的跑了」的那一次）。

    两者住在一起，是因为它们是同一个位置的两句话（``settings.database_path``）。
    分开写的话，一次改路径只会改到其中一处，而另一处照旧读写旧文件 —— 本仓已经在
    别处栽过一次这种「同一个东西两处定义」（见 ``composition.py`` 里那一族工厂的
    备注）。
    """
    return SqliteEvidenceStore(settings.database_path)


def _account(settings: Settings, account_id: str) -> dict[str, Any]:
    key = _account_key(account_id)
    store = read_store(settings)
    if store is None:
        return {
            "account_id": key,
            "routes": [],
            "route_count": 0,
            "opportunities": [],
            "opportunity_count": 0,
            "evidence_count": 0,
            "evidence_refs": [],
            # §6.3：**持久化过的**拒收回执那一格。这里与上面几格一样是空表而不是
            # ``None`` —— 库还没建出来，所以「这个账号历来一条都没被拒」与事实相符，
            # 而 ``None`` 在本文件里说的是「这一格没有算过」，那会是一句假话。
            "rejections": _no_rejections_yet(),
            "account_record": None,
            "account_record_source": _ACCOUNT_RECORD_SOURCE,
            "note": f"这个账号还没有任何已落盘的产物（{settings.database_path.name} 尚未创建）。"
            f"这是一个正常答案，不是读不出来。{_BOUND_NOTE}",
        }

    routes = store.list_routes(key)
    opportunities = store.list_opportunities(key)
    evidence = store.list_evidence(key)
    return {
        "account_id": key,
        # §6：读的是**投影**（``CanonicalContactRoute``），不是那条路线本身。多出来
        # 的那几格正是报告要的东西 —— 这个键被改写过几次、第一次与最近一次分别是
        # 什么时候看的、上一次换掉的是什么等级（``previous_grade``：报告里那句
        # 「掉级了」的唯一依据）。路线本体在 ``route`` 那一格下面。
        "routes": [route.model_dump(mode="json") for route in routes[:MAX_ITEMS]],
        "route_count": len(routes),
        "opportunities": [
            opportunity.model_dump(mode="json") for opportunity in opportunities[:MAX_ITEMS]
        ],
        "opportunity_count": len(opportunities),
        "evidence_count": len(evidence),
        "evidence_refs": [_evidence_ref(item) for item in evidence[:MAX_ITEMS]],
        # §6.3 的**读**那一半。``submit_findings`` 的回执报的是**这一趟**拒了什么
        # （那批收据此刻还在内存里），这一格报的是**这个账号历来**拒了什么（从盘上
        # 读回来）。两件事都要：前者回答「刚才发生了什么」，后者回答「这个账号一直
        # 是什么状况」—— 只报前者的话，一次研究跑完就再没有地方看得到那些拒收了，
        # 而那正是「算了就丢」换了一个位置。
        "rejections": _rejections_block(history(store, key, limit=MAX_ITEMS)),
        "account_record": None,
        "account_record_source": _ACCOUNT_RECORD_SOURCE,
        "note": f"这是有界存档。{_BOUND_NOTE}",
    }


def _evidence_ref(evidence: Evidence) -> dict[str, Any]:
    """指向一条证据的引用，不是那条证据本身。

    ``get_account`` 回答的是「这个账号有什么」，材料本身由 ``get_evidence`` 按
    引用取 —— 两件事合成一件会让一次「看一眼账号」顺带搬运全部原文。
    """
    return {
        "evidence_id": evidence.evidence_id,
        "source_url": evidence.source_url,
        "source_kind": evidence.source_kind.value,
        "field": evidence.field.value if evidence.field is not None else None,
        "fetched_at": evidence.fetched_at.isoformat(),
        "content_sha256": evidence.content_sha256,
        "binding_eligible": evidence.binding_eligible,
    }


def _evidence_key(value: str) -> str:
    """一个证据引用必须非空。

    **只查这一件事。** 证据 id 是内容寻址的（``domain/evidence.evidence_id``），
    它的形状由产生它的那一步决定；在这一层再钉一遍形状，就是把「写下的 id」
    与「读得了的 id」分成两套规则 —— 而分家的那天，写入侧新产生的一类 id
    会在这里被拒绝，读的人却以为是那条证据不存在。

    空串则是另一回事：它连「一条引用」都不是，所以是一次调用错误，不是一个结果。
    """
    key = (value or "").strip()
    if not key:
        raise ToolError(
            "evidence_id 不能为空。这个工具按**引用**读 —— 一条引用长什么样由产生"
            "它的那一步决定（见 get_account 的 evidence_refs），这里只负责解它。"
        )
    return key


def _locator_key(value: str) -> str:
    """定位符必须在闭集里，拒绝时说清闭集是什么。"""
    key = (value or "").strip()
    if key not in EVIDENCE_LOCATORS:
        raise ToolError(
            f"locator 必须是 {' 或 '.join(EVIDENCE_LOCATORS)} 之一，实际拿到 {value!r}。"
            "这个参数是一个**闭集**而不是自由字符串：把它当自由字符串的实现会"
            "悄悄按缺省值处理，于是调用方以为自己问了另一种材料。"
        )
    return key


def _stored_evidence(settings: Settings, evidence_id: str, locator: str) -> dict[str, Any]:
    """按**引用**读一条已取得的材料。

    输入形状由 ``MCP_SERVICE.md`` §3 那一行定死：「evidence id + bounded locator」。
    这里此前收的是 ``account_id`` —— 一条按账号**列举**的接口，而那是
    ``get_account`` 的事（它返回有界的 ``evidence_refs``）。两件事合成一件之后
    只有一个后果：**只有已经拿到账号的人才验得了一条引用**，而
    ``submit_findings`` / ``decide`` 的调用方手里只有一个引用。

    ## 三个问题，三答案，不合形

    * ``found`` —— 这条引用**解不解得开**（这个 id 下有没有行）。
    * ``item`` —— 那一行的全部字段（§2 的「provenance and transformations」）。
    * ``artifact.available`` —— 那一行指向的**字节**在不在这台机器上。

    后两个与第一个是不同的问题：库从别的机器恢复过来时，行在、字节不在，而
    「这条引用是假的」与「这台机器没有那份材料」的处置相反 —— 一个去查引用从
    哪来，一个去修部署。合并成一个布尔会把后者读成前者。

    不抛异常也是刻意的：读不到是**一个结果**，抛出去会让调用方以为调用失败、
    于是重试，拿到同一个答案。
    """
    kind = _locator_key(locator)
    key = _evidence_key(evidence_id)
    payload: dict[str, Any] = {
        "evidence_id": key,
        "locator": kind,
        "found": False,
        "reason": "",
        "item": None,
        "artifact": None,
    }

    store = read_store(settings)
    if store is None:
        payload["reason"] = (
            f"这个部署还没有证据库（{settings.database_path.name} 尚未创建），"
            "所以任何引用都解不开。这与「这个 id 下没有行」不同：那要去查引用，"
            "这要去查部署。"
        )
        return payload

    row = store.get_evidence(key)
    if row is None:
        payload["reason"] = (
            f"证据库里没有 {key} 这一行。引用是内容寻址的，所以这一条指的是"
            "一份没有落过盘的材料，或者这个库不是产生那条引用的那一个。"
        )
        return payload

    payload["found"] = True
    payload["item"] = _evidence_item(row)
    if kind == EVIDENCE_LOCATOR_ARTIFACT:
        payload["artifact"] = _artifact(settings, row)
    return payload


def _artifact(settings: Settings, evidence: Evidence) -> dict[str, Any]:
    """按**一行证据**走回它的镜像字节。§3 那行的行为要求是「Reuse already acquired
    material without refetching」—— 所以这一格只落在盘上已有的产物上，一步
    网络请求都不发。

    ``available=True`` 的含义是**字节已按那一行的 ``content_sha256`` 校验过**。
    这件事没有对应的布尔字段：一个恒为 True 的 ``sha256_verified`` 是假形状 ——
    它看起来像在报告一件可能为假的事，而它没有可能为假。校验失败走
    ``available=False`` 加理由，理由里说清是哪一种没找到（见
    ``adapters/page_mirror.read_stored_page``：目录不在 / 前缀不符 / 散列不符
    是三种不同的事，最后一种是一条证据链的证伪信号）。
    """
    page, reason = read_stored_page(
        settings,
        account_id=evidence.account_id,
        source_url=evidence.source_url,
        content_sha256=evidence.content_sha256,
        max_bytes=MAX_ARTIFACT_BYTES,
    )
    if page is None:
        return {
            "available": False,
            "ref": "",
            "byte_length": 0,
            "media": "",
            "text": "",
            "truncated": False,
            "reason": reason,
        }

    text = as_text(page.head, complete=not page.truncated)
    return {
        "available": True,
        "ref": page.relative_ref(settings),
        "byte_length": page.byte_length,
        "media": "text" if text is not None else "binary",
        "text": text or "",
        "truncated": page.truncated,
        "reason": "",
    }


def _evidence_item(evidence: Evidence) -> dict[str, Any]:
    """一条证据的完整投影，外加上下文与正文各自是否被截断的标记。

    逐条标记而不是只给一个总数：报告读者要知道的是**哪一条**短了。静默截断会让
    一段被腰斩的上下文看起来像原文就是这样。

    两格的界分开，因为它们是两件事：

    * ``context`` 是**命中处的周围文本**，本来就只为人工复核，几千字符足够；
    * ``literal_value`` 是**材料本身**，而库里的那一格是完好的（见
      ``application/evidence_ingest.MAX_LITERAL_CHARS`` 的说明 —— 那里现在是
      一个只防病态页面的存储上界）。这里截它，是为了让一次 ``get_evidence``
      的载荷有上界，而**截断要被说出来**：拿到这一行的人若以为手里是全文，
      会据此认为「这一页没有这个联系人」，而那是这一格最贵的错。
    """
    item = evidence.model_dump(mode="json")
    context = item.get("context")
    cut = isinstance(context, str) and len(context) > MAX_CONTEXT_CHARS
    if cut and isinstance(context, str):
        item["context"] = context[:MAX_CONTEXT_CHARS]
    item["context_truncated"] = cut

    literal = item.get("literal_value")
    literal_cut = isinstance(literal, str) and len(literal) > MAX_LITERAL_PREVIEW_CHARS
    if literal_cut and isinstance(literal, str):
        item["literal_value"] = literal[:MAX_LITERAL_PREVIEW_CHARS]
    item["literal_truncated"] = literal_cut
    return item


# ----------------------------------------------------------------------
# get_report
# ----------------------------------------------------------------------
def _report(settings: Settings, run_id: str | None) -> dict[str, Any]:
    key = (run_id or "").strip()
    if not key:
        return {
            "available": _available_runs(settings),
            "note": (
                "没给 run_id 时列出现有报告的文件名（不含扩展名）。"
                "空列表表示这个部署还没有跑过任何一次 run。"
            ),
        }

    # 与账号 id 用的是**同一条判据、同一个界**（``domain/util.check_canonical_id``）：
    # run_id 也会被拼成文件名（``benchmarks/<run_id>.json``）。这里曾经自己写着
    # 一份 max_length=80 的副本，而账号那侧是 60 —— 同一条判据、两个界。
    try:
        check_canonical_id(key, "run_id")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    path = settings.benchmarks_dir / f"{key}.json"
    if not path.is_file():
        raise ToolError(f"没有 {key!r} 这份报告。可用的是：{_available_runs(settings) or '（还没有任何报告）'}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolError(f"报告 {key!r} 读不出来（{type(exc).__name__}）") from exc
    if not isinstance(payload, dict):
        raise ToolError(f"报告 {key!r} 的顶层不是一个对象，不能作为结构化内容返回")
    # 原样返回：报告是产物，这一层不做二次加工 —— 加一层包装会让「报告里有什么」
    # 与「工具返回什么」变成两份需要同步维护的清单。
    return payload


def _available_runs(settings: Settings) -> list[str]:
    directory = settings.benchmarks_dir
    if not directory.is_dir():
        return []
    return sorted(item.stem for item in directory.glob("*.json") if item.is_file())


# ----------------------------------------------------------------------
# submit_research / get_job（Phase G）
# ----------------------------------------------------------------------
#: 一次 ``get_job`` 里 work item 最多返回多少条。总数另有 ``work_items_count``，
#: 所以截断可见（§11 禁止静默截断）。
MAX_WORK_ITEMS = 50

#: 派发闸门**回执里**可能的两个判词。只有两个，因为另外两种结局不是回执：
#: 「没立项」与「记录不接受新工作」都抛 ``ToolError``，调用方拿到的是那句话本身。
#:
#: 一个取值表里放一个永远不会出现的成员，会让读到它的人以为那也是一种回执 ——
#: 这与 ``_SCENE_RECORD_*`` 那条教训是同一件事的两面：合成一个布尔会把「没接线」
#: 与「判成可以」压成同一格，而多列一个不产生的成员会把「拒绝」与「判过」压成
#: 同一格。两种都让回执比它实际知道的更多。
#:
#: * ``not_wired`` —— 这个实例没有接场景存储。**判不了，不是判成可以。**
#: * ``admitted`` —— 有记录、ACTIVE，放行。
_SCENE_GATE_NOT_WIRED = "not_wired"
_SCENE_GATE_ADMITTED = "admitted"

_JOB_NOT_FOUND = (
    "作业 id 是按内容推导的（场景 + 幂等键），所以「写错了」与「受理过又"
    "被删掉」在这里看起来一样 —— 而后者不会发生：取消是一个状态"
    "（cancelled），不是一个删除。"
)


def _scene_gate(
    *,
    settings: Settings,
    scenes: Callable[[], SceneStore] | None,
    scene_id: str,
) -> dict[str, Any]:
    """**受理前**问一次这个 Scene 现在让不让开工。不让就是一条拒绝，不是一格回执。

    ## 为什么这一问必须在这里，而不能等维护侧

    本轮之前，派发侧唯一的场景判定是 :func:`require_scene` —— 它问的是
    「``scene_id`` 是不是本部署服务的那个」，答的是**名字对不对**。生命周期三格
    （``draft`` / ``paused`` / ``archived``）当时只在 ``application/maintenance.py``
    被读，于是「暂停一个场景」停的是维护周期，**研究派发照常受理**：一个暂停中的
    市场仍然可以被 ``submit_research`` 派出一批新的昂贵工作，而回执里没有任何一格
    会说这件事。``domain/scene.py`` 的模块 docstring 早就写着「``PAUSED`` /
    ``ARCHIVED`` 停的是**新的**研究与外联派发」—— 那一句话当时只是对的写法，
    没有对的调用点。

    ## 拒绝发生在 ``store.admit`` **之前**

    与 ``input_ref`` 的名字校验同一条理由（见 ``_submit_research`` 的注释）：
    先受理再拒绝，会在库里留下一个作业行，而它属于一个没人批准的市场 ——
    而且它的幂等键已经被用掉了，调用方改好场景重发会撞上幂等冲突。判定在门口，
    这个状态就没有存在过的机会。

    ## ``not_wired`` 为什么不拒绝

    没有场景存储时这件事实在答不出来。答不出来有两种写法：当作可以（今天的写法，
    也是**在接线之前**唯一可能的写法），或者当作不可以（那会让每一个还没接
    ``SceneStore`` 的部署连研究都做不了，而那是接线事故，不是场景判定）。
    这里选前者，**但把它写进回执**（``dispatch_gate`` 那一格如实报 ``not_wired``
    与理由）—— 「没判」与「判成可以」在报告上必须是两句话，否则读的人会以为
    这个场景被批准过。

    ## 没有立项记录时**拒绝**

    这是本轮新加的一条，而它是有意的：一份立项记录是「有人批准过这个市场」在
    代码里的唯一形态（:class:`SceneVersion` 带着 ``created_at`` 与审批记录）。
    没有它就受理，等于把「先立项」这条 §1 的入口当成可选项。拒绝里给了下一步
    该做什么，所以它是一次**可执行的**拒绝而不是一堵墙。
    """
    if scenes is None:
        return {
            "decision": _SCENE_GATE_NOT_WIRED,
            "scene_state": None,
            "effective_version": None,
            "reason": "",
            "note": (
                "这个服务实例没有接场景存储，所以「这个场景现在让不让开工」判不了。"
                "受理照旧 —— 这与「判成可以」不是同一句话：生命周期三格"
                "（draft/paused/archived）在这一格上**没有被问过**。"
            ),
        }

    record = scenes().get_record(scene_id)
    if record is None:
        raise ToolError(
            f"{scene_id!r} 还没有立项过（库里读不到记录），因此不受理研究派发。\n"
            "  一次研究派发要有对应的立项：区域、场景、产品、角色策略与预算都写在"
            "某一版 Scene 上，而它与这份作业的输入是两件事 —— 先"
            " ``create_scene(..., activate=true)`` 写下第 1 版并让它生效，再受理。"
        )
    if record.dispatch_refusal:
        raise ToolError(
            f"这个 Scene 现在不接受新的研究派发：{record.dispatch_refusal}。\n"
            "  停的是**新的**研究与外联派发；已经在途的工作单元不受影响，"
            "回信、退信、退订也照常受理（见 domain/scene.py 的「暂停域与仍然要跑的事」）。\n"
            f"  当前状态 {record.state.value}，最新第 {record.latest_version} 版，"
            f"生效版本 {record.effective_version}。"
        )

    return {
        "decision": _SCENE_GATE_ADMITTED,
        "scene_state": record.state.value,
        "effective_version": record.effective_version,
        "reason": "",
        "note": (
            "受理按**这一版**生效的规则放行。effective_version 是判这一问时读到的"
            "版本号 —— 作业本身不钉在它上面（作业行存的是 profile 目录的字节摘要），"
            "所以这一格是「当时哪一版在生效」，不是「这份作业以后按哪一版跑」。"
        ),
    }


def _submit_research(
    *,
    settings: Settings,
    jobs: Callable[[], JobStore],
    dispatcher: Dispatcher | None,
    scenes: Callable[[], SceneStore] | None,
    accounts: Callable[[], AccountPoolStore] | None,
    cohorts: Callable[[], AccountCohortStore] | None,
    review: Callable[[], ReviewCaseStore] | None,
    scene_id: str,
    input_kind: str,
    input_ref: str,
    idempotency_key: str,
    source_method: str = "",
) -> dict[str, Any]:
    """受理一次研究请求，返回持久的作业 id 与它的真实状态（§2）。

    ## 为什么没有 ``limits`` 参数

    §2 写的是「optional stricter limits」。本构建**不收**它：调用方给一个
    「最多 5 次检索」，而这个构建里没有任何一处会去读那个数字 —— 一个声明了却
    没人执行的额度上限，比没有这个参数坏得多，它会让调用方以为自己宣告了一条
    边界。派发接上之后这一条**仍然**成立，而且理由更硬：``get_assignment`` 报的是
    ``subject_ref`` 那一行**冻结**的额度，不是调用方给的数，而派发不碰额度。
    真正缺的是「把一条调用方上限变成每个 work item 的实际闸门」的那个东西 ——
    它不在这条路径上。

    今天真实的额度边界是**每个账号**的 ``search.max_queries``，它在第一次
    计费调用时由 ``RunBudget.for_account`` 从冻结的 profile 播种，调用方
    改不动它（见 ``application/budget.py``）。本工具在受理时校验的那件事是
    同一个谓词的**受理侧**：一个连额度都没声明的部署，受理下来的作业永远
    不可能有账号额度可用。

    ## 受理之后立刻做的两件事：编译，然后交出去

    第一件，``input_ref`` 说的工作集变成 durable 的 ``work_item`` 行 —— 不碰网络、
    不建 派发任务、不花一分钱。第二件是派发（§6）：把这一批工作交出去，每一件
    在本地留下一条 ``map_execution`` 的映射。受理与派发**在同一次调用里**，因为
    §6 逐字要求 "Store admission and dispatch intent transactionally"：分两次调，
    「受理了、没派出去」就会与「受理了、正要去派」在报告上长得一样，而两者的处置
    不同（前者要 resume，后者只要等）。

    这么排的理由是那一句「一条没有 input 能走到的判据与一条恒真的健康旗同类」：
    ``get_job`` 要报的进度、``get_assignment`` 要租的活、派发要遍历的清单，全都
    挂在 work item 上 —— 而在这一步之前，本仓没有任何一处会**产出**它们。一个
    受理了却永远没有工作单元的作业，在报告里长得像一次完成（``compile_work_items``
    的 docstring 逐字写着这句）。

    ## 阻塞（``BLOCKED``）在这里第一次有了生产者

    解析不出工作集时，作业落到 ``BLOCKED``，理由就是解析器给的那一句话。§2 逐字
    要求这个工具返回 "truthful queued/blocked state"，而 ``BLOCKED`` 在此之前是
    一个**没有生产者**的状态（``docs/MVP_MIGRATION_PLAN.md`` §2.10 的缺口 #3）——
    一个恒不出现的状态与一个恒出现的状态是同一类东西。

    两个条件是有意的，而且各挡一种谎：

    * **只在作业手里一件工作都没有时**才落 ``BLOCKED``。一个已经编译出工作单元的
      作业不因为「今天读不出那个 input_ref」而变成被阻塞 —— 那是把一次读盘失败
      说成一件没做过的事。
    * **只在 ``QUEUED`` 上落**。``PAUSED`` / ``CANCELLED`` 是控制端的判断，
      受理不是往回写它的地方。

    调用的顺序也有一处讲究：名字**先**校验、再受理。反过来的话，一次非法
    ``input_ref`` 会先落下一个把非法名字写死的作业，调用方改好名字重发就会撞上
    幂等冲突 —— 那个键连同它的作业一起废掉。而这次校验是**三种输入一起过**的
    （直接调 ``check_canonical_id``，不是靠 ``subjects_for`` 顺手校验）：后者对
    另外两类在给出阻塞理由之前就返回了，靠它等于「这条规则只管今天有生产者的
    那一种」—— 而 ``input_ref`` 是一条名字、不是路径，是三种输入共有的约定。

    ## 回执里的 ``dispatch`` 不是「能不能真的开跑」的第二个答案

    ``get_capabilities`` 回答的是**这个部署有什么**（配了没 / 装了没 / 有没有工具
    真的会调它，三格逐项）。这里报的是**这一次调用做了什么**：交出去了几件、有没有
    停下、为什么。一句是能力，一句是动作，而动作那句话只有这个响应说得出 ——
    它是一次调用的事实，别的工具读不到。

    三格里的 ``executor_configured`` 是唯一一句带部署味道的，而它说的是这个进程
    手里有没有那个**对象**（与能力表 dispatch 行的 ``runtime_ready`` 同源：都是从
    ``dispatcher`` 这个参数读的，不重推 ``Settings``）。
    """
    scene = require_scene(settings, scene_id)
    # 两问挨着写：**名字对不对**（``require_scene``）与**这个场景现在让不让开工**
    # （``_scene_gate``）。分开写会让第二问某天被挪到 ``store.admit`` 之后 ——
    # 而那时它挡下的就不是一次派发，是一次**已经落盘的**派发。
    gate = _scene_gate(settings=settings, scenes=scenes, scene_id=scene)

    raw_kind = (input_kind or "").strip()
    try:
        kind = JobInputKind(raw_kind)
    except ValueError:
        raise ToolError(
            f"没有名为 {raw_kind!r} 的输入种类。闭集是："
            f"{'、'.join(sorted(k.value for k in JobInputKind))}。\n"
            "把它做成开放字符串，第一个拼错的名字会静静受理一个谁都不知道该拿什么"
            "去跑的作业，而它在报告里和一次正常的受理长得一样。"
        ) from None

    ref = (input_ref or "").strip()
    if not ref:
        raise ToolError(
            "input_ref 不能为空。它是这个作业**拿什么当输入**的引用 —— "
            f"{kind.value} 这一种类下，空引用意味着「受理了，但不知道受理的是什么」，"
            "而那样的作业在报告里和一次有输入的受理长得一样。"
        )
    key = (idempotency_key or "").strip()
    if not key:
        raise ToolError(
            "idempotency_key 不能为空。它答的是「这是不是同一次意图」——"
            "没有它，丢响应之后的每一次重发都会被受理成一次新的研究。"
        )

    bundle = require_bundle(settings)

    try:
        ceiling = declared_search_ceiling(bundle)
    except BudgetNotConfiguredError as exc:
        raise ToolError(
            f"本部署没有可用的检索额度声明，因此不受理任何作业：{exc}"
        ) from exc

    fingerprint = request_fingerprint(scene_id=scene, input_kind=kind, input_ref=ref)
    # 名字**先**过一遍，再受理。反过来的话，一次非法 ``input_ref`` 会先落下一个
    # 作业、再报错，而那个作业的 ``input_ref`` 已经写死了 —— 调用方改好名字重发
    # 会撞上幂等冲突（同一个 key、不同的请求），于是那个键连同它的作业一起废掉。
    # 拒绝在门口，等于不给这个状态任何存在的机会。
    #
    # **四种输入一起过**：``subjects_for`` 只对 known_accounts 那一类校验名字
    # （另外两类在它给出阻塞理由之前就返回了），在那里校验等于「这条规则只管今天
    # 有生产者的那一种」。而「``input_ref`` 是一条**名字**、不是路径」是四种输入
    # 共有的约定 —— 第一个生产者会照它去拼 ``inputs_dir`` 下的路径。
    #
    # ``cohort`` 那一种不拼路径（它是一条名单 id），**照旧过这里**：给这一条规则
    # 按种类开例外，等于把「哪一类该验」变成一个要维护的清单，而那个清单的第一项
    # 就已经是错的（``discovery_scope`` 的名字同样不拼文件）。名单 id 是
    # ``slugify`` 的不动点，所以它本来就过得了；过不了的那些也确实不该被受理。
    try:
        check_canonical_id(ref, "input_ref")
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    # 「这次受理服务的是计划里的哪一步」（§B16 关闭条件 7）。解析排在 ``store.admit``
    # **之前**，与上面名字那条同一条理由、同一种后果：一次非法步骤名若在受理之后才
    # 被读出来，那条调用已经在盘上留下了一个 job_id 从未回到调用方手里的作业，
    # 换一个幂等键重发还会再造一个。
    #
    # 读的是**生效**那一份 Scene 的计划（``plan_steps``）：受理这一刻还没有「这件
    # 工作钉住的那一份」—— 钉住的版本是 ``_resolve_and_compile`` 在下一段冻住的。
    step = plan_step(source_method, steps=plan_steps(require_bundle(settings)))

    store = jobs()
    try:
        job = store.admit(
            scene_id=scene,
            input_kind=kind,
            input_ref=ref,
            idempotency_key=key,
            request_sha256=fingerprint,
        )
    except IdempotencyConflictError as exc:
        raise ToolError(str(exc)) from exc
    except ValueError as exc:
        # 形状检查（``request_fingerprint`` 的输出恒合法，但存储是**独立的**一层，
        # 它的拒绝必须成为一条可读的结果，而不是一次崩溃）。
        raise ToolError(str(exc)) from exc

    # 受理之后立刻编译：``input_ref`` 说的工作集在这一步变成 durable 的 work item。
    # 这一步不碰网络、不建 派发任务 —— 交出去是下一段。
    try:
        items, blocker = _resolve_and_compile(
            settings, store, job, accounts=accounts, cohorts=cohorts, review=review
        )
    except (JobStateError, ValueError) as exc:
        raise ToolError(f"工作单元编译失败：{exc}") from exc
    if blocker and not items and job.state is JobState.QUEUED:
        # 输入产不出来，而且这个作业手里一件工作都没有：这是一次**真的**阻塞
        # （§2 要求返回 truthful queued/blocked state）。理由原样落在作业上，
        # 不在回执里另编一句。
        job = store.set_state(
            job.job_id, JobState.BLOCKED, expected=job.state, reason=blocker
        )

    # 交出去（§6）。这一步在**编译之后**、在任何外部读之前 —— 一件工作单元先有
    # 本地那一条映射（``map_execution``），才有「谁在跑它」这个问题。
    dispatch = _dispatch_block(settings, store, dispatcher, job, step=step)

    return {
        "job_id": job.job_id,
        "scene_id": job.scene_id,
        "input_kind": job.input_kind.value,
        "input_ref": job.input_ref,
        "state": job.state.value,
        "reason": job.reason,
        "accepted_at": job.created_at,
        "state_note": (
            "state 是**作业**的状态（持久、幂等、重启不丢）：queued 表示受理了、"
            "且没有阻塞在它的输入上；blocked 表示输入产不出工作单元，理由在 reason 里。"
            "work_items.count 报的是这个作业**已经落盘**的工作单元条数 —— 这一次"
            "没有新编译出来的话，它报的是原有的那一批。dispatch 报的是这一次交出去"
            "了什么；有没有 worker 去领、去跑，要问 get_assignment 与 get_job —— "
            "「交出去」与「有人在跑」是两件事。"
        ),
        "work_items": {
            "count": len(items),
            "blocker": blocker,
            "note": (
                "blocker 说的是**这一次读 input_ref** 的结果，与 state 不是同一件事："
                "作业可能早就编译好了工作单元，而那个 input_ref 今天读不出来。"
                "那时 state 仍是 queued，count 仍是原来那一批。"
            ),
        },
        "dispatch": dispatch,
        # 受理前那一问的读数。放在这里而不是并进 ``state_note``：``state`` 是**作业**
        # 的状态，而这一格是**场景**的 —— 两者的关系是「作业落在一个允许开工的场景
        # 上」，不是同一件事的两种说法。拒绝那两种情况走不到这一行（它们抛）。
        "dispatch_gate": gate,
        "budget": {
            "line": "search",
            "scope_shape": f"{bundle.profile_id}:<account_id>",
            "per_account_ceiling": ceiling,
            "source": f"profile:{bundle.profile_id}:search.max_queries",
            "note": (
                "上限是**每个账号**的，不是这个作业的总额 —— 一次运行的总额度因此是"
                "「每账号上限 × 实际跑起来的账号数」，本层不假装知道后面那个数。"
                "账号各自的额度在第一次计费调用时播种，这里只报它从哪来。"
            ),
        },
    }


def _job(
    *,
    settings: Settings,
    jobs: Callable[[], JobStore],
    ledger_factory: Callable[[], BudgetLedger] | None,
    dispatcher: Dispatcher | None,
    worker_outcomes: Callable[[str], tuple[WorkerExitRecord, ...]] | None,
    job_id: str,
) -> dict[str, Any]:
    """读一个作业的真实状态：work item、派发映射、租约、进度、用量、终态回执（§2）。"""
    key = (job_id or "").strip()
    store = jobs()
    job = store.get(key)
    if job is None:
        raise ToolError(f"没有这个作业：{key!r}。{_JOB_NOT_FOUND}")
    # **对象那一维**：作业行自己带着它属于哪个 Scene，而这条工具面此前不看它。
    # 令牌判定答不了这个问题 —— 它答的是「谁准调 get_job」，而一行**别的场景**的
    # 作业读起来与一行本场景的完全同形。这些行从哪来：同一个人把 MARKETOPS_STATE
    # 指到同一个库、又改了 MARKETOPS_PROFILE 时，旧场景的行会留在库里。
    require_scene(settings, job.scene_id)

    items = store.work_items(key)
    rows: list[dict[str, Any]] = []
    dispatching = stopped = 0
    subjects: set[str] = set()
    for item in items[:MAX_WORK_ITEMS]:
        ref = store.execution_ref(item.work_item_id)
        attempts = store.attempts(item.work_item_id)
        subjects.add(item.subject_ref)
        if item.is_stopped:
            stopped += 1
        elif any(attempt.is_live for attempt in attempts):
            dispatching += 1
        rows.append(
            {
                "work_item_id": item.work_item_id,
                "ordinal": item.ordinal,
                "subject_ref": item.subject_ref,
                "stop_reason": None if item.stop_reason is None else item.stop_reason.value,
                "execution": (
                    None
                    if ref is None
                    else {"dispatch_task_id": ref.dispatch_task_id, "mapped_at": ref.mapped_at}
                ),
                "attempts": [
                    {
                        "attempt_id": attempt.attempt_id,
                        "ordinal": attempt.ordinal,
                        "holder": attempt.holder,
                        "state": attempt.state.value,
                        "leased_at": attempt.leased_at,
                        "settled_at": attempt.settled_at,
                        "note": attempt.note,
                    }
                    for attempt in attempts
                ],
            }
        )

    return {
        "job": {
            "job_id": job.job_id,
            "scene_id": job.scene_id,
            "input_kind": job.input_kind.value,
            "input_ref": job.input_ref,
            "state": job.state.value,
            # 与 ``submit_research`` 的 ``state_note`` 同一条判据，只是这里多一句
            # 立刻要说清的事：**``state`` 不是进度格**。
            #
            # 这条注释是验收报告 §N.1 那处观察的落点。那一次的读数是「三个 Job 全是
            # ``queued``，**包括已完全结算的那个**」，于是 ``state`` 看起来像一格
            # 不动的进度。它不是不动 —— 它答的是另一个问题：``JobState`` 里
            # **刻意没有** ``done`` / ``settled``（枚举自己的 docstring 写了为什么），
            # 因为「这个作业做完了吗」由工作单元回答，不由作业状态回答。一件工作
            # 结算完不会把作业推到一个终态，所以已完全结算的作业**应该**是 queued。
            #
            # 一格「看起来像进度、其实答的是另一个问题」的字段比没有字段更坏，所以
            # 这一句必须与那一格在同一块里 —— 放在 notes 里，读的人在那之前就已经
            # 把它误读完了。
            "state_note": (
                "state 是**作业**的状态，不是进度：queued = 受理了且没有阻塞在它的"
                "输入上；paused / cancelled = 控制端按下的；blocked = 输入产不出工作"
                "单元，理由在 reason 里。**这里没有 done / settled，是刻意的** —— "
                "「这个作业做完了吗」由工作单元回答（看 progress 与 research_terminals），"
                "不由作业状态回答。所以一个**全部工作单元都已结算**的作业，state "
                "仍然是 queued，而那是正确的读数，不是一格停了。"
            ),
            "reason": job.reason,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        },
        "work_items": rows,
        "work_items_count": len(items),
        "work_items_truncated": len(items) > MAX_WORK_ITEMS,
        "progress": {
            "work_items": len(items),
            "dispatching": dispatching,
            "stopped": stopped,
            "not_started": len(items) - dispatching - stopped,
            "note": (
                "not_started 是「还没派出租约」，不是「没在跑慢」—— 两者的区别"
                "只有租约能回答，所以它从租约算，不从时间算。"
            ),
        },
        "results": _job_results(store, rows, truncated=len(items) > MAX_WORK_ITEMS),
        "research_terminals": _research_terminals(
            store=store,
            dispatcher=dispatcher,
            worker_outcomes=worker_outcomes,
            items=items[:MAX_WORK_ITEMS],
        ),
        "usage": _job_usage(settings, ledger_factory, sorted(subjects), len(items)),
        "notes": [
            "集成的接线状态由 get_capabilities 逐项报告（configured / runtime_ready / "
            "wired 三格），这里不重复 —— 两处各报一次必然先漂移一处。",
            "work item 与它的停止理由在同一次写入里落盘，所以「停了吗」只有一个答案。",
        ],
    }


def _control_job(
    *,
    settings: Settings,
    jobs: Callable[[], JobStore],
    dispatcher: Dispatcher | None,
    accounts: Callable[[], AccountPoolStore] | None = None,
    cohorts: Callable[[], AccountCohortStore] | None = None,
    review: Callable[[], ReviewCaseStore] | None = None,
    job_id: str,
    action: str,
    expected_state: str,
    reason: str,
    source_method: str = "",
) -> dict[str, Any]:
    """类型化的 pause / resume / cancel，以及**观察到的执行结果**（§2 ``control_job``）。

    ## 为什么必须要 ``expected_state``

    因为一次 pause / cancel 是**两个控制端可以同时按下**的那个动作。不带 CAS 的
    写法（读、判、写）在两个控制端同时按下时最后一个写的赢，而**两边都以为自己
    成功了** —— 报告里留下的是第二次按下，第一次按下的那个人看不到自己的命令
    被吃掉了。这与 执行器的 ``--expect-status`` 是同一条纪律，理由也一样。

    调用方手上本来就有这个值（``get_job`` 返回 ``job.state``），所以这不是多要一个
    参数，是要求它把「我据以决定的那次读」写下来。不匹配时整次拒绝，并把读到的
    那一个报回去。

    ## 闭集是三个动词，不是四个

    ``blocked`` 在 ``JOB_TRANSITIONS`` 里，但**不是**一个控制端动词。一个作业被
    阻塞是**观察到**的一件事（某个上游说不、额度不够、派发任务失败了），不是谁的
    一句命令。把它做成一个可调的动作，调用方就能声明一个没有任何证据的阻塞，而
    报告里它与一次真的阻塞长得一样 —— 而 §6 要求 cancel 说明「what actually
    stopped」，同一条纪律对 blocked 只会更严。

    ``BLOCKED`` 的生产者不在这里，也不该在这里 —— 它是 ``submit_research`` 与
    本函数的 ``resume`` 在**观测到**输入产不出来时落下的（见 ``_resolve_and_compile``
    的调用点）。仍然是「观察到的一件事」，只是观察者换成了那两个写者。

    ## 「实际停了什么」是**写完之后**读到的

    ``observed`` 与那次写入**不在同一个事务里**，而且它不该在：pause 与 cancel
    在这一点上不同（§6 逐字「Pause normally prevents new dispatch and lets
    accepted in-flight results settle」）—— pause 不收回任何在途租约，cancel 也
    不。所以 ``observed`` 里在途的那些**仍然是在途的**，两个动词都不假装它们停了。
    一次状态写入与一次收回在途工作不是一回事，把它们合成一个动作，会让一个
    「已暂停」的作业里继续跑着没人能叫停的东西。

    ## ``resume`` 之后要**重新派发**，这一步在这里

    一个作业被 pause 住的时候，它的工作单元可能一件都还没交出去（暂停是派发的
    反向条件，见 ``JobStore.map_execution``）。所以「恢复」不是一个纯状态动作：
    把状态写回 ``queued`` 之后，本函数走一遍 ``_dispatch_block`` —— 与受理那条
    路径**同一个**实现，不是第二个。

    这也是 ``BLOCKED`` 唯一的解除路径（H2a 之后它会真的出现）：以账号清单为例，
    文件补齐之后 resume 会重新读一次 ``input_ref``、重新编译（幂等）、再派发。
    状态先写、派发后做，顺序不能反 —— 派发要看的是**写完之后**那个状态。

    派发的结果放在 ``dispatch`` 那一格，形状与 ``submit_research`` 里的完全一样。
    它**不是** ``observed``：``observed`` 读的是「现在真的有什么在跑」，而
    ``dispatch`` 说的是「这一次按下 resume 交出去了什么」。
    """
    store = jobs()
    key = (job_id or "").strip()
    current = store.get(key)
    if current is None:
        raise ToolError(f"没有这个作业：{key!r}。{_JOB_NOT_FOUND}")
    # 与 ``_job`` 同一维、同一条理由。这一处更要紧：它是**写** —— 一个跨越场景
    # 的 pause / cancel 会真的去改一个不属于本部署的作业的状态，而回执上它与一次
    # 正常的控制完全同形。
    require_scene(settings, current.scene_id)

    raw_action = (action or "").strip()
    try:
        target = _ACTIONS[raw_action]
    except KeyError:
        raise ToolError(
            f"没有 {raw_action!r} 这个控制动作；可用的是 {sorted(_ACTIONS)}。\n"
            "这里刻意**没有** block：一个作业被阻塞是观察到的一件事，不是谁的一句"
            "命令。做成一个可调的动作，调用方就能声明一个没有任何证据的阻塞。"
        ) from None

    raw_expected = (expected_state or "").strip()
    try:
        expected = JobState(raw_expected)
    except ValueError:
        raise ToolError(
            f"没有 {raw_expected!r} 这个状态；可用的是 "
            f"{sorted(member.value for member in JobState)}。\n"
            "它是你**据以决定**的那次读（get_job 的 job.state）—— 写下来才成其为"
            "一次 CAS，而不是一次盲写。"
        ) from None

    # 「这次控制服务的是计划里的哪一步」（§B16 关闭条件 7）。校验排在 ``set_state``
    # **之前**：``resume`` 会重新派发，而一次注定被拒的 resume 不该先把作业从 paused
    # 写成 queued、再报一句错 —— 那样调用方读到的是一次失败，盘上却是一次已经生效
    # 的状态写入。``pause`` / ``cancel`` 也照验不误：这一格是**声称**，一次不派发的
    # 动作照样可以声称错，而把校验按动词分家等于说「哪个动词该验」是一张要维护的
    # 清单 —— 那张清单的第一项就已经是错的。
    step = plan_step(source_method, steps=plan_steps(require_bundle(settings)))

    try:
        job = store.set_state(key, target, expected=expected, reason=reason or "")
    except JobStateError as exc:
        # 措辞原样透出：CAS 不匹配、非法转移、理由该有而没有 —— 三条规则都写在
        # 存储层，在这里重编一遍就是同一份规则的第二个实现。
        raise ToolError(str(exc)) from exc

    dispatch: dict[str, Any] | None = None
    if target is JobState.QUEUED:
        # resume 不是一次纯状态写入：暂停期间一件都没交出去的工作要在这里交出去。
        # 顺序是**先写状态、再重新解析与编译、最后派发** —— 派发看的是写入之后的
        # 那个状态，而重新解析让「清单补齐了」这件事在 resume 时真的生效（受理那
        # 条路径用的是同一个 ``_resolve_and_compile``，不是第二个实现）。
        try:
            items, blocker = _resolve_and_compile(
                settings, store, job, accounts=accounts, cohorts=cohorts, review=review
            )
        except (JobStateError, ValueError) as exc:
            raise ToolError(f"作业恢复不了：{exc}") from exc
        if blocker and not items:
            # 输入仍然产不出来：把作业放回 BLOCKED，理由就是那条理由。这一格让
            # 「resume 了、但并没有恢复」在回执里是一次**说得清**的失败，而不是
            # 一个照常 queued、然后什么都没发生的作业。
            job = store.set_state(
                job.job_id, JobState.BLOCKED, expected=job.state, reason=blocker
            )
        dispatch = _dispatch_block(settings, store, dispatcher, job, step=step)

    receipt: dict[str, Any] = {
        "job_id": job.job_id,
        "intent": {
            "action": raw_action,
            "target_state": target.value,
            "expected_state": expected.value,
            "reason": reason or "",
        },
        "job": {
            "state": job.state.value,
            "reason": job.reason,
            "updated_at": job.updated_at,
        },
        "observed": _observed(store, job.job_id),
        "notes": [
            "intent 与 job 是两件事：intent 是你要求了什么，job 是它现在是什么。"
            "合成一个 «state» 字段之后，「我按下了暂停」与「它真的暂停了」在回执里"
            "不可区分 —— 而 §6 要求 cancel 说明 what actually stopped。",
            "observed 是**写完之后**读到的，与那次写入不在同一个事务里。理由写在"
            "本函数的 docstring：pause 不收回在途租约，所以它们在这里仍然是**在途**的。",
            "恢复（resume）不接受理由 —— 一条恢复不出自任何人的判断。",
        ],
    }
    if dispatch is not None:
        receipt["dispatch"] = dispatch
        receipt["notes"].append(
            "dispatch 只在 resume 的回执里出现。pause 与 cancel **不派发**，而一个"
            "恒为空的那格会被读成「派发过了、什么都没成」—— 那与「没派发」在报告里"
            "是两件事。"
        )
    return receipt


#: ``control_job`` 的动词 → 目标状态。**三个，不是四个** —— 理由见 ``_control_job``。
_ACTIONS: dict[str, JobState] = {
    "pause": JobState.PAUSED,
    "resume": JobState.QUEUED,
    "cancel": JobState.CANCELLED,
}


def _observed(store: JobStore, job_id: str) -> dict[str, Any]:
    """这个作业现在**真的有**什么在跑、什么停过、什么还没开始。

    数租约而不数时间：``not_started`` 说的是「还没派出过租约」，不是「跑得慢」。
    后者要从时间算，而时间答不了「是不是有人在跑」——那正是 §6 要求
    attempt/lease protection 可核时要回答的问题。
    """
    items = store.work_items(job_id)
    settled: list[str] = []
    in_flight: list[dict[str, str]] = []
    not_started = 0
    for item in items:
        if item.is_stopped:
            settled.append(item.work_item_id)
            continue
        live = [attempt for attempt in store.attempts(item.work_item_id) if attempt.is_live]
        if live:
            in_flight.append(
                {"work_item_id": item.work_item_id, "holder": live[0].holder}
            )
        else:
            not_started += 1
    return {
        "work_items": len(items),
        "settled": len(settled),
        "in_flight": len(in_flight),
        "in_flight_holders": sorted({entry["holder"] for entry in in_flight}),
        "not_started": not_started,
        "settled_work_item_ids": settled[:MAX_ITEMS],
        "truncated": len(settled) > MAX_ITEMS,
        "note": (
            "in_flight 说的是「**现在**有活租约」，而 pause 与 cancel 都不收回它们"
            "（§6 逐字：Pause normally prevents new dispatch and lets accepted "
            "in-flight results settle）。要收回一条，只有 release(reason=...)。"
        ),
    }


def _job_results(
    store: JobStore, rows: list[dict[str, Any]], *, truncated: bool
) -> dict[str, Any]:
    """这个作业的产出（§3 ``submit_findings`` 落下来的那些）。

    ## 「还没有人结算」与「跑过了、什么都没找到」必须分得开

    这是本函数唯一难的地方，而且它正是 G2 那条判据的下一步。那时 ``available``
    恒为 ``False``（产出没有落点），所以那个 0 **不能**报；现在 ``submit_findings``
    落地了，0 有两种来源：

    * ``available: false`` —— 还没有任何 work item 结算。这个作业什么都还没产
      *出来*，不是产出了零个;
    * ``available: true`` 且 ``totals`` 全为 0 —— 跑过了，诚实的零（§8B 逐字：
      No fixed positive A/B count is required: zero is an honest business
      outcome）。

    两者的处置完全不同：前者要等，后者要看这次跑得对不对。

    ## 停了却没有产出的 work item 会被**就地点名**

    它在结构上不可能出现（停止理由与产出在同一个写事务里写），所以真出现时
    只可能来自被人手改过的库。那时报告**不猜**：那一条的 ``findings`` 为
    ``null``，紧挨着一句为什么这不可能是正常的 —— 比默认它「还没结算」诚实，
    因为那个默认是在替一个说谎的记录圆场。
    """
    stopped: list[dict[str, Any]] = []
    totals = {"work_items_with_findings": 0, "observations": 0, "candidates": 0, "gaps": 0}
    for row in rows:
        if row["stop_reason"] is None:
            continue
        settlement = store.settlement(row["work_item_id"])
        if settlement is None:
            stopped.append(
                {
                    "work_item_id": row["work_item_id"],
                    "subject_ref": row["subject_ref"],
                    "stop_reason": row["stop_reason"],
                    "findings": None,
                    "why_no_findings": (
                        "这个 work item 有停止理由却没有对应的产出。停止理由与产出"
                        "在同一个写事务里写下（ports/jobs.py::settle），所以这个状态"
                        "只可能来自被人手改过的库 —— 这里不把它读成「还没结算」。"
                    ),
                }
            )
            continue
        findings = settlement.findings
        totals["work_items_with_findings"] += 1
        totals["observations"] += len(findings.observations)
        totals["candidates"] += len(findings.candidates)
        totals["gaps"] += len(findings.gaps)
        stopped.append(
            {
                "work_item_id": row["work_item_id"],
                "subject_ref": row["subject_ref"],
                "stop_reason": row["stop_reason"],
                "attempt_id": settlement.attempt.attempt_id,
                "recorded_at": settlement.recorded_at,
                "content_sha256": settlement.content_sha256,
                "findings": findings.as_dict(),
            }
        )

    available = totals["work_items_with_findings"] > 0
    results: dict[str, Any] = {
        "available": available,
        "stopped_work_items": stopped,
        "totals": totals,
        "truncated": truncated,
        "note": (
            "totals 数的是**结论**（观察 + 候选），缺口不计入 —— 缺口是结论的缺席，"
            "不是一条结论。available 为真而 totals 全为 0 是一次诚实的空结果："
            "那个 0 背后有一次真实的运行。"
        ),
    }
    if not available:
        results["blocker"] = (
            "还没有任何 work item 结算，所以这里**不报 0 条产出**。\n"
            "0 会被读成「跑过了、什么都没找到」，而真实情况是还没有人结算过 —— "
            "两者的处置不同：一个要等，一个要去看这次跑得对不对。"
        )
    if truncated:
        results["truncated_note"] = (
            f"这个作业的 work item 超过 {MAX_WORK_ITEMS} 条，上面只是前 "
            f"{MAX_WORK_ITEMS} 条 —— 还有产出没有列在这里（§11 禁止静默截断）。"
        )
    return results


def _job_usage(
    settings: Settings,
    ledger_factory: Callable[[], BudgetLedger] | None,
    subjects: list[str],
    work_item_count: int,
) -> dict[str, Any]:
    """这个作业涉及的账号各用了多少额度。

    三件**不同**的事被分开报：这个实例有没有账本可读、这个作业有没有账号可问、
    某个账号的额度有没有配置。都合成一个 ``spent: 0`` 之后，它们全都长成
    「这次没花钱」—— 而账本自己已经立过同一条判据（``remaining`` 对没配置的
    科目**抛**而不是返回 0，见 ``adapters/sqlite/ledger.py``）。

    scope 用 ``bundle.profile_id`` 拼，与 ``RunBudget.for_account`` 是同一个
    来源。用 ``settings.profile``（目录名）拼过一次：两者不一致时这里会去读
    一个没人写过的 scope，于是每个账号都报「没配置」—— 一个**恒假的**用量。
    """
    if ledger_factory is None:
        return {
            "reported": False,
            "blocker": "这个服务实例没有接账本（ledger_factory 未传），所以读不到用量。",
        }
    if not subjects:
        return {
            "reported": False,
            "blocker": (
                f"这个作业还没有编译出 work item（共 {work_item_count} 条），"
                "因此没有任何账号额度可报。这里**不报 0** —— 0 会被读成"
                "「一次检索都没花」，而真实情况是「还不知道该问谁的额度」。"
            ),
        }

    try:
        bundle = load_profile(settings)
    except ConfigError as exc:
        return {"reported": False, "blocker": f"Scene 装载失败，读不到额度：{exc}"}

    ledger = ledger_factory()
    accounts: list[dict[str, Any]] = []
    for subject in subjects:
        scope = f"{bundle.profile_id}:{subject}"
        # **判据接在 `remaining` 上，不是接在 `usage` 是否为空上。**
        # 这两个方法对「没配置」的回答不同：`remaining` 抛
        # `BudgetNotConfiguredError`（账本自己立的判据，见
        # `adapters/sqlite/ledger.py`），而 `usage` 返回一个**空表** ——
        # 空表读起来就是「配了、还没花」。第一版这里读的是 `usage`，于是
        # 每一个账号都被报成 `configured: true` 加一个空列表：一个**恒真**的
        # 健康旗，与「这个账号有额度」不可区分。
        try:
            ledger.remaining(scope, BudgetLine.SEARCH)
        except BudgetNotConfiguredError:
            # 没配置 ≠ 用完了。分开报，因为处置不同：一个要去看配置，
            # 一个要去看这次跑得对不对。
            accounts.append(
                {"subject_ref": subject, "scope": scope, "configured": False, "reason": "no_ceiling"}
            )
            continue
        accounts.append(
            {
                "subject_ref": subject,
                "scope": scope,
                "configured": True,
                "lines": [
                    {
                        "line": entry.line.value,
                        "ceiling": entry.ceiling,
                        "spent": entry.spent,
                        "reserved": entry.reserved,
                        "remaining": entry.remaining,
                    }
                    for entry in ledger.usage(scope)
                ],
            }
        )
    return {"reported": True, "accounts": accounts}


# ----------------------------------------------------------------------
# get_assignment / submit_findings（Phase G3，§3 的 worker 侧）
# ----------------------------------------------------------------------
#: 这几句话是**同一件事**的三个阶段，所以写在一个地方：
#: 「一个 work item 要能被领走，先得派发到执行器；派发之后由 ``get_assignment``
#: 当场领，再由 ``submit_findings`` 结算」。``get_assignment`` 与
#: ``submit_findings`` 都要过这条链，而两处各写一遍会先漂移一处 ——
#: 漂移的形态是一个签得下的租约、却读不出它的任务。
_NO_WORK_ITEM = (
    "work item id 是按内容推导的（作业 + 序号），所以「写错了」与「还没编译出它」"
    "在这里看起来一样 —— 而后者不会发生：work item 一编译出来就在盘上。"
)


def _work_item_or_refuse(store: JobStore, work_item_id: str) -> Any:
    """要一个 work item。不存在时拒绝，不返回一个空壳。"""
    item = store.get_work_item(work_item_id)
    if item is None:
        raise ToolError(f"没有这个 work item：{work_item_id!r}。{_NO_WORK_ITEM}")
    return item


def _claim(store: JobStore, item: Any, principal: Principal | None) -> Any:
    """领活：**没有活租约就当场领一条**，有就拿回那一条。``get_assignment`` 用。

    读的是租约而不是一个「有没有人在跑」的字段：后者会与 ``attempts`` 构成两个
    真相来源，而它们不一致时，报告说「有人在跑」而没有任何人持有它。

    **这个谓词只有领活那一侧能要。** 交活那一侧不能用它 —— 一条已经结算的租约
    正是「重发」要走的路径，而它按定义不是活的（见 ``_attempt_for``）。

    ## 为什么领活住在这里，而不是一条 ``lease`` 工具

    §3 的工具目录里没有 ``lease``。所以「工作是谁的」若只能由一个不存在的入口写
    下去，``get_assignment`` 就永远只会回答「没有活的租约」—— 而那句话读起来像
    一个事实（确实没人持着），实际是一个死循环：没人能领，所以永远没有人持着。
    一条没有入口的边界不是保护，是一块读起来像保护的招牌。

    ## 三次调用分别是三件事，而它们只差在**谁先到**

    * **没有活租约** → 租给**本次调用的主体**（holder 取自传输层的令牌）。
    * **有活租约、是本人** → 拿回同一条。幂等：丢响应之后重发是最正常的动作。
    * **有活租约、是别人** → 照旧交给 ``_require_holder`` 拒绝，消息里带着持有着的名字。

    ## 竞争窗口不在这里判

    读的时候没有、写的时候别人刚领走 —— 这一格由存储层的排他写挡下（``lease``
    对「已经租给别人」直接拒绝）。在这里再判一次就是同一份规则的两个实现，而
    漂移的那一次会让**两个 worker 同时以为这件事是自己的**，那正是 §8B
    「no duplicate expensive work」要挡的东西。存储层的措辞原样透出。

    入参是**已经解析好的** work item，不是 id：调用方（``_assignment``）本来就
    要先读它才知道作业与派发引用，让这里再读一次就是同一个请求里两遍同一个事实
    —— 而两遍读出来的东西没人保证一致，读法上看起来却像有保证。

    Raises:
        ToolError: 存储层拒绝了这次出租（作业不放行、还没派发、已经停止），
            或者没有已认证的主体。

    最后那一条**经传输层不可达**：``RoleGuard`` 用 ``authorize`` 先拒掉无主体的
    调用，handler 根本不会被叫到。留着它不是为了断言一个事实，而是为了**绕过
    middleware 的调用者**（测试、直接调 handler 的那一侧）拿到一句能读的拒绝、
    而不是一个 ``AttributeError`` —— ``_require_holder`` 里那条同款分支同理。
    """
    live = [attempt for attempt in store.attempts(item.work_item_id) if attempt.is_live]
    if live:
        return live[0]
    if principal is None:
        raise ToolError(
            "领活需要一个已认证的主体：没有活的租约时会当场租给**本次调用的主体**，"
            "而主体取自传输层的 Bearer 令牌，不取自任何参数。"
        )
    try:
        return store.lease(item.work_item_id, holder=principal.name)
    except (JobStateError, LeaseError) as exc:
        raise ToolError(str(exc)) from exc


def _attempt_for(
    settings: Settings, store: JobStore, work_item_id: str, attempt_id: str
) -> tuple[Any, Any, Any]:
    """按名字要这个 work item 上的一条尝试。``submit_findings`` 用。

    ## 对象那一维在这里，而且必须在**枚举尝试之前**

    ``_job`` / ``_control_job`` 各自读作业行的时候就问了「这一行属于哪个
    Scene」。这一条路径不读作业行 —— 它读 work item，而 ``WorkItem`` 上
    **没有** ``scene_id`` 这一格（Scene 是作业的，不是工作单元的）。所以这里
    要顺着 ``item.job_id`` 去读一次作业，才问得出那个问题。

    **顺序是这条判据的全部内容。** 下面那条「它上面有哪几条尝试」的回执会把
    这个 work item 上每一条 attempt id 列出来，而 attempt id 是内容推导的
    摘要 —— 一个跨场景的调用方拿到它，就等于拿到了另一份作业的运行记录指纹。
    把 Scene 判在枚举**之后**，那一步就已经发生过一次了，只是拒绝得晚一点。
    这与 ``_assignment`` 里「Scene 先于领活读」是同一条理由：**一次拒绝不该
    顺手交出去任何东西。**

    ## 这是**写**路径，所以它比读那一侧更要紧

    ``_control_job`` 的注释里已经写过这一句。这里还要多一层：``settle`` 落的是
    产出，产出按 ``work_item_id`` 落在**这个部署的状态根**下。一个跨场景的结算
    会真的把一份 findings 写进不属于本部署的那份作业里，而回执上它与一次正常的
    结算完全同形 —— 唯一的差别在读的人脑子里。

    ## 为什么这里**不**要活租约

    因为领活与交活的前置条件根本不是同一条。领活要的是一件**现在有人在做**的
    事（没有就当场做出来，见 ``_claim``）；交活要的是**这条尝试是我的**。而交活
    最常见的到达方式恰恰是「上一次的响应丢了，我重发一次」—— 那时这条租约
    **已经结算过了**，按「必须活」去要求它，等于让每一次合规的重发都撞在一句
    「这条租约已经结束了」上。两个动词在这里不对称是**有意的**，不是漏了一条。

    于是「活不活」不在这一层判：那是 ``settle`` 的事，而它判得比这里细 ——
    活租约写下去，同结局的重发幂等返回，换了结局的拒绝。

    ## 作业行**跟着一起返回**，而不是让调用方再读一次

    上面那条判据已经把它读出来了（不问 Scene 就没有这一条）。交出去是因为
    ``submit_findings`` 后面还要问它**另一件事**：这一件工作的主体是不是从一份
    来源回执来的（``job.input_kind``），而这一格决定「它研究的场地是哪一块」
    答不答得出来。调用方再读一次就是同一个请求里两遍同一个事实 —— 读法上看起来
    像有保证，而两遍之间没有人保证它们一致。

    Raises:
        ToolError: work item 不存在，它指向的作业不在，它属于本部署不服务的
            Scene，或者它上面没有这条尝试。
    """
    item = _work_item_or_refuse(store, work_item_id)
    job = store.get(item.job_id)
    if job is None:
        raise ToolError(
            f"work item {item.work_item_id} 指向的作业 {item.job_id} 不在。{_JOB_NOT_FOUND}"
        )
    require_scene(settings, job.scene_id)
    attempts = store.attempts(work_item_id)
    for attempt in attempts:
        if attempt.attempt_id == attempt_id:
            return item, job, attempt
    # 列出实际存在的那些：调用方手上的 id 只可能来自 get_assignment，所以
    # 「对不上」多半意味着它拿的是**别的** work item 的 assignment —— 看到这里
    # 有哪几条，它自己就能判断。这条路里的 id 是内容推导的 32 位十六进制，
    # 数量也小（一个 work item 的尝试数就是它被领过的次数）。
    known = "、".join(repr(attempt.attempt_id) for attempt in attempts) or "（一条都没有）"
    raise ToolError(
        f"work item {item.work_item_id} 上没有 {attempt_id!r} 这条尝试；"
        f"它上面有：{known}。\n"
        "attempt id 与 work item id 要成对使用，而两个 id 都由 get_assignment "
        "给出 —— 配错的那一对在这里是一个拒绝，不是一次静默的解析。"
    )


def _require_holder(attempt: Any, principal: Principal | None) -> str:
    """只把 assignment 交给**持有它**的那个 worker。

    §6 逐字要求「real process/task ownership」可核 —— 而 ``holder`` 就是那个
    归属。放开这一步，任何一个 researcher 都能读走别人的任务、结算别人的租约，
    而两条路径在报告里都不会留下痕迹：调用方看到的是自己的名字在 ``holder``
    字段里（它自己写的），不是别人的。

    判定的输入是**传输层已确立的身份**（``guard.current_principal``），不是任何
    一个调用参数 —— 与分析面的 ``authorize`` 同一条纪律。
    """
    if principal is None:
        raise ToolError(
            "这条租约不属于你：本次调用没有已认证的主体。\n"
            "归属取自传输层的 Bearer 令牌，不取自任何参数。"
        )
    if attempt.holder != principal.name:
        raise ToolError(
            f"这条租约不属于你：它由 {attempt.holder!r} 持有，而你是 {principal.name!r}。\n"
            "一个 worker 只读得到自己领走的那件工作 —— 放开这一步，任何研究侧主体都"
            "能结算别人的租约，而报告里留下的会是它自己的名字。"
        )
    return attempt.holder


def _decision_catalog(bundle: ProfileBundle) -> list[dict[str, Any]]:
    """这份 assignment 能问的**全部**判定题面 —— 以及拿它们去问的那一格 id。

    ## 为什么这一块必须存在

    判定这条路在服务面上一直是**可达但无人指路**的：``decide`` 收一个 ``spec_id``
    （``adapters/mcp_server/decisions.py`` 的 ``_specs`` 按它精确匹配），而在此之前
    没有任何一处把 id 交到研究员手里。研究员拿不到 id 就不会去问，台账上于是只有
    `decision_record` 表零行 —— 而「零行」与「这份 profile 从来没有声明过判定」
    在报告上长得一模一样。缺的从来不是能力，是一份**回执**：§3 把 ``decide`` 的输入
    写成「work item id, server-owned DecisionSpec id, evidence/state references」，
    前两项在这一份回执里，第三项才是研究员自己的活。

    ## 给出题面**不等于**交出题面的作者权

    这里连题目与选项（key 与描述）一并给出，而 ``decide`` 仍然只收 id —— 两个方向
    是不同的两件事：**读得到**不构成**写得了**。``_specs`` 只按编译出来的 id 匹配，
    没有「收下调用方送来的一份题面」的入口，所以这一块不构成 §4 那条「普通 worker
    不得自造 DecisionSpec」的松口。而描述本身按设计**不含**匹配规则
    （``profile.yaml`` 的 decisions 块刻意避开 contact / staff 这类词，判据在
    ``tests/test_decision_specs.py``），所以给研究员看并不比给后端看更多。

    ## 报的是 ``choices_by_question`` 那一份，不是另抄一遍

    选项 key 直接取自 ``Question.options``，而 ``choices_by_question`` 就是从同一个
    字段算出来的 —— 也就是 ``decide`` 校验「回答里出现了一个闭集之外的选项」时用的
    那一份。各抄一遍的后果不是文案不一致：研究员会照着回执里的一个 key 去写结论，
    而那个 key 在判定那一侧是闭集外的取值，于是那句话**永远对不上任何一条记录**。

    Raises:
        ToolError: profile 声明了判定但编译不过（闭集与声明的选项对不上）。
            **整次拒绝，不降级成一块空的 ``decision_specs``**：一块空表读起来就是
            「这份 profile 没有声明过判定」，于是研究员不会去问，台账上又是零行 ——
            而这一次连「有人写错了选项」都从回执上看不出来。
    """
    try:
        compiled = compile_specs(bundle)
    except ConfigError as exc:
        raise ToolError(
            f"这份 assignment 的判定题面编译失败：{exc}\n"
            "题面是从这份 Scene 的 profile 编译的，所以这是**部署**问题，不是这次"
            "调用的问题。这里整次拒绝，而不是回一块空的 decision_specs —— 一块空表"
            "读起来与「这份 profile 没有声明过判定」一模一样，于是研究员不会去问，"
            "而这一次连「有人写错了选项」都看不出来。"
        ) from exc

    return [
        {
            "spec_id": spec.spec_id,
            "version": spec.version,
            "task": spec.task.value,
            "questions": [
                {
                    "question_id": question.question_id,
                    "kind": question.kind.value,
                    "options": [
                        {"key": option.key, "description": option.description}
                        for option in question.options
                    ],
                }
                for question in spec.questions
            ],
        }
        for spec in compiled.values()
    ]


def _assignment(
    *,
    settings: Settings,
    jobs: Callable[[], JobStore],
    ledger_factory: Callable[[], BudgetLedger] | None,
    work_item_id: str,
) -> dict[str, Any]:
    """一个 worker 领到的这件工作：冻结的场景、输入引用、租约、工具权限、预算（§3）。

    ## 「冻结」现在是**版本 + 字节**的冻结，而分歧要读 ``guidance``

    §6 逐字要的是 Running tasks retain their pinned **guidance**。这件工作编译时
    钉住的 Scene 版本记在存储层（``GuidancePin``），连同**那批字节**
    （``scene_snapshot``，按版本内容寻址）：``pinned_bundle`` 照版本把它们读回来，
    **读不到就不再发放**（照 ``get_assignment`` 那条先例，没有第三种回落）。
    这里报成 ``guidance`` 块；而 ``scene`` 块里那套规则是**现在这一刻**从盘上装载
    的。两者在编译之后有人改过 profile 时不是同一份 —— 回执因此同时给出两个版本
    与一个服务端算好的比较。

    **钉住的那一批字节，是这一片真正拿去执行的那一份**：``decision_specs`` 从它
    编译，``decide`` 与 ``settle`` 走同一个 ``pinned_bundle`` —— 于是「编译之后
    有人改了 profile，一件在飞的工作照旧按老的那一版答题」不再发生。这一点从前
    在 ``notes`` 里被写成「保证的是**说得清**，不是按钉住的那一版执行」，那句话
    已经随这片改动一起改掉了：一句过时的保证比没有保证更坏。

    剩下的边界只有一条，且它写在回执上：盘上那一份**没有**被冻结，``scene`` 块与
    ``guidance.plan_steps`` 读的仍是此刻的目录（后者为什么不能跟着钉，见那一格的
    注释：收下那一步的几条研究工具只拿得到一条 ``Settings``）。两份分家的可见形态
    就是 ``pinned_is_effective: false`` 与 ``content_from``。

    ## 「领」这个动词真的在这里发生

    一件已经派发到执行器、但还没有人持着的工作，**这一次调用就把租约租给调用方**
    （见 ``_claim``）。§3 的工具目录里没有一条 ``lease``，所以如果这里只肯回答
    「没有活的租约」，那件工作就永远没有人能持着 —— 一句读起来像事实的死循环。
    派发（control 面）与领活（worker 面）因此是同一件事的两端：前者把工作交给
    执行器，后者把 执行器 上的那份工作认领到某个具体主体名下。

    ## 「已受理的前置条件」在这一层是**拒绝**，不是一个勾选清单

    §3 要求报「accepted prerequisites」。把它做成一串恒真的布尔值会是一个恒真的
    健康旗 —— 这里既然读得到 assignment，那些前置条件当然都成立。所以它们由
    **缺席时的那句话**回答：哪一条不成立，返回的就是它的一句话，而不是一个
    ``false``。这与 ``get_job`` 对未知 id 的处理是同一条取向。

    这些前置条件里有三条是关于**主体身份**的，而且是三个分开的状态：没有快照、
    快照里没有主体、有主体但那一份身份不够开工（``identity_refusal``）。三条都拒绝，
    三条的处置各不相同 —— 合成一条会让处置的人去错地方。

    ## 工具权限与判定同源

    ``tool_rights`` 直接来自 ``security.catalog.visible_tools`` —— 也就是
    ``RoleGuard`` 过滤 ``tools/list`` 用的那个函数。各算一遍的后果不是文案不一致，
    是**判定不一致**：assignment 承诺一个工具，而中间件在调用它时拒绝，两者都
    按自己的规则行事。

    ## ``decision_specs`` 是 ``decide`` 那条路的路标

    见 ``_decision_catalog``：它交出的是**这件工作钉住的那一版**编译出来的题面 id
    与问法（``pinned_bundle`` 的返回值，不是 ``scene`` 块那一份）。没有它，判定这条
    路**可达而无人指路** —— 工具接线齐全、``decide`` 跑得起来，而没有任何一处把
    spec_id 交给研究员，于是台账上 ``decision_record`` 恒为零行，而那零行与「这份
    profile 没有声明过判定」在报告上不可区分（§F）。
    """
    store = jobs()
    item = _work_item_or_refuse(store, work_item_id)
    job = store.get(item.job_id)
    if job is None:
        raise ToolError(f"work item {item.work_item_id} 指向的作业 {item.job_id} 不在。{_JOB_NOT_FOUND}")

    # §6 那条边界**先于**领活判。存储层也拦（没有 ExecutionRef 的 ``lease`` 报
    # LeaseError），但那里的措辞是关于租约的；放在领活之前，读到这条拒绝的人
    # 拿到的是 §6 的原话 —— 而不是一句「租不出去」，然后自己猜为什么。
    ref = store.execution_ref(item.work_item_id)
    if ref is None:
        raise ToolError(
            f"work item {item.work_item_id} 还没有派发到执行器，所以它不是一个 assignment。\n"
            "§6 逐字：the controller cannot substitute an outer-shell worker launch "
            "for 执行器 dispatch —— 而这条链是由 ``lease`` 反过来守的（没有 ExecutionRef "
            "就没有租约），所以它不该出现；出现了说明这个 work item 的记录被改过。"
        )
    # 快照是**这条记录够不够格当 assignment** 的第二个问题，所以它紧跟着 §6 那条判、
    # 也在领活之前：一次拒绝不该顺手造出一条租约。
    snapshot = store.work_item_snapshot(item.work_item_id)
    if snapshot is None:
        raise ToolError(
            f"work item {item.work_item_id} 没有冻住的快照。\n"
            "§6 逐字：Running tasks retain their pinned guidance —— 一件说不出自己按"
            "哪一份规则在跑的工作，在这里不是一个可以领的 assignment。\n"
            "它是在钉住这个机制出现之前编译的（正常路径上不会出现这一格：编译会把版本"
            "与工作单元写在同一个事务里）。按现在这份规则做，是**另一个作业**。"
        )
    if snapshot.subject is None:
        # 第二个缺席状态，与上一条**分开报**：那一行有真的版本（它确实按某一版规则
        # 跑过），只是没有记录过针对谁。两者都拒绝，但拒绝的理由不同 —— 一条说
        # 「不知道按哪份规则」，一条说「不知道在说谁」。合成一条会让处置的人去错地方。
        raise ToolError(
            f"work item {item.work_item_id} 的快照里没有主体身份。\n"
            "它按 {} 那一版规则跑过（快照里有），但**针对谁**从来没有被记下来 —— "
            "身份快照是 2026-09-23 之后才有的。\n"
            "领走它的人拿不到任何可用来做身份发现的依据，唯一能做的是拿内部 slug 去"
            "检索 —— 那正是这次要修的那个缺口。重新受理一次（那是**另一个作业**），"
            "或者由人给出这一次要研究谁。".format(snapshot.scene_version)
        )
    # 第三个缺席状态，与上面两条**分开报**：规则在、身份也在，只是这一份身份拿不到
    # 任何做身份发现的依据（``organisation`` 空、或只是把内部 id 又写了一遍，且
    # ``domain`` 也空）。
    #
    # 同一个谓词在受理那一刻问过一次（``application/inputs.py``）—— 那里的拒绝是
    # **整份清单**拒绝，一件都不编译。这里是它落地的地方：受理那条路挡不住这一格，
    # 因为一份只有 slug 的身份**在存储层是合法的一行**（``compile_work_items`` 收的是
    # ``SubjectDossier``，它不判内容），而历史那一批工作就是那么编译出来的。于是
    # 「拿内部 slug 去检索」这件事在领活这一侧仍然可能发生 —— 除非这里也问一次。
    #
    # 两次问的是**同一个函数**，不是同一条规则的两份实现：判据只有一个，被问两次。
    # 各写一份的后果是漂移，而漂移的那一次会让一件在受理时被拒的工作，在领活时
    # 被放行 —— 报告上它与一件正常的工作长得一样。
    refusal = identity_refusal(snapshot.subject)
    if refusal:
        raise ToolError(
            f"work item {item.work_item_id} 冻住的主体身份不够开工。\n{refusal}\n"
            "这一条与「没有快照」「快照里没有主体」是**三个**分开的状态，处置也不同："
            "那两条要去补记录，这一条要去补**这份清单里的那一行**。\n"
            "受理侧的同一判据会整份拒绝（一件都不编译），所以走到这里说明这一行是"
            "在身份快照机制出现之前编译的 —— 而它照样不能被发出去：发出去的结果就是"
            "worker 拿这个内部 id 去公网检索。"
        )

    # Scene **先于领活**读：下面那条判据要用到 bundle，而任何一条拒绝都不该顺手
    # 造出一条租约 —— 领活会推高 attempt 序数并占掉一次重试预算，于是「被拒绝」
    # 与「被尝试过」在账上变成同一件事。读 profile 是一次纯读，提前没有代价。
    require_scene(settings, job.scene_id)
    try:
        bundle = load_profile(settings)
    except ConfigError as exc:
        raise ToolError(f"Scene 装载失败，读不到这份 assignment 的规则：{exc}") from exc

    # §6 逐字：Running tasks retain their pinned guidance。这一份 ``pinned`` 是
    # 「**这件工作**按哪一份规则跑」的唯一答案：读它的字节、算它的版本、从它编译
    # 下面那两块。判据（``pinned_bundle``，判定与结算走的是同一个函数）是先问字节
    # 还在不在，再看盘上现在这一份是不是它 —— 两头落空就拒绝，而那一条拒绝发生在
    # 领活之前：一次拒绝不该顺手造出一条租约（领活会推高 attempt 序数、占掉一次
    # 重试预算，于是「被拒绝」与「被尝试过」在账上变成同一件事）。
    pinned_scene = store.scene_files(snapshot.scene_version)
    pinned = pinned_bundle(
        settings,
        effective=bundle,
        pinned=PinnedScene(snapshot.scene_version, pinned_scene),
        work_item_id=item.work_item_id,
    )

    # 题面**在领活之前**编译，与上面那条判据同一个理由：编译不过时整次拒绝，而
    # 一次拒绝不该顺手造出一条租约。编译读的是上面那份 ``pinned``，不另读一次盘
    # —— 另读一次会在「读两次之间 profile 被改过」时让回执上的 ``scene`` 与
    # ``decision_specs`` 出自两份不同的规则。
    #
    # 来源必须是 ``pinned`` 而不是 ``bundle``：这一格交给研究员的是**去判定的那几
    # 个 spec_id**，而 ``decide`` 那一侧现在也从钉住的那一版编译（见
    # ``decisions.py::_specs``）。取了 effective，交出去的 id 与判定时执行的那份
    # 题面就会分家 —— 而这正是本片要关掉的那件事（编译之后有人改了 profile，
    # 一件在飞的工作照旧按老的那一版答题）。
    decision_specs = _decision_catalog(pinned)

    # 「这一轮允许从哪儿拿行」。它读的是**生效**那份 ``bundle``，不是上面那份
    # ``pinned`` —— 与 ``decision_specs`` 恰好相反，而且不是疏忽：收下这一步的那几条
    # 研究工具只拿得到一条 ``Settings``，也就是只读得到生效的那一份。这里若跟着钉住
    # 的字节走，编译之后有人改过 profile 时，服务端会交出一个工具**认不出来的**步骤
    # —— 那种红在报告上看起来像接错了线（见 ``guidance`` 那一格的注释）。
    #
    # 位置：与题面同一处，理由也一样是「一次拒绝不该顺手造出一条租约」（读不出来时
    # 整次拒绝，见 :func:`plan_steps`）。它进 ``guidance.plan_steps``，也就是 worker
    # 手上那一格的**唯一**来路 —— 少了这一句，worker 只能自己编一个步骤名（或者留空），
    # 而两者在报告上与「这一步确实属于这一轮的计划」不可区分（§B16 关闭条件 7）。
    steps = plan_steps(bundle)

    # 顺序是**先解析、再领活、最后验归属**，三者不可换位：领活会把租约租给
    # 「本次调用的主体」，而验证归属必须看到**领完之后**的那条租约 —— 颠倒过来
    # 就成了拿一条还不存在的租约去比 holder。
    principal = current_principal()
    attempt = _claim(store, item, principal)
    _require_holder(attempt, principal)

    return {
        "work_item": {
            "work_item_id": item.work_item_id,
            "ordinal": item.ordinal,
            "subject_ref": item.subject_ref,
        },
        # **这是谁** —— 受理那一刻冻下来的那一份，不是现在读一遍输入文件。
        # ``subject_ref`` 是一个内部主键（``kent-003``）；拿它去检索会花掉整轮预算，
        # 然后交回一份与「这个账号真的没有公开信息」不可区分的结算。所以身份与
        # 主键**一起**回，而且身份是冻住的那一份：输入文件在那之后被改过，
        # 正在飞的那件工作照样按受理时的样子读这张回执（与 guidance 块同一个道理）。
        #
        # 三格出处与一个冻结时刻**跟在线索一起**回，而不是只在线索之外另外报一次。
        # 这一条不是美观问题：``identity_refusal`` 的判据边界写着，消歧**是 worker
        # 的活**（判据只答「包里有没有一个人类可读的指称对象」，答不了「这个名字
        # 是不是它」—— 后者要市场知识，而 ``marketops/`` 里不许有）。做那件事所需
        # 的材料，就是「这一行来自哪份清单的哪一版字节」，所以它必须和名字在同一块里。
        "subject": {
            **snapshot.subject.as_clues(),
            "source_ref": snapshot.subject.source_ref,
            "input_sha256": snapshot.subject.input_sha256,
            # 受理那一刻。``guidance.pinned_at`` 是同一个时刻，但那一格说的是
            # **规则**是什么时候钉的；这一格说的是**身份**。两者今天相同，将来不必
            # 相同 —— 而合并成一格的那天，没有人能从回执上看出它们分了家。
            "frozen_at": snapshot.pinned_at,
        },
        "job": {
            "job_id": job.job_id,
            "scene_id": job.scene_id,
            "input_kind": job.input_kind.value,
            "input_ref": job.input_ref,
            "state": job.state.value,
        },
        "scene": {
            "scene_id": settings.profile,
            "profile_id": bundle.profile_id,
            "status": bundle.status,
            "summary": bundle.summary(),
            "has_unknown_rules": bundle.has_unknown_rules,
            "unknowns": list(bundle.unknowns),
        },
        # §6：Running tasks retain their pinned guidance。三个字段一起答「这件工作
        # 按哪一份规则在跑」，而**上面那个 scene 块给的是现在生效的那一份** ——
        # 两者不一定相同：编译之后有人改了 profile，正在飞的那件工作照样按老的样子
        # 读这张回执。写成两块而不是一块，是因为它们真的是两个时刻的东西。
        "guidance": {
            "pinned": snapshot.scene_version,
            "pinned_at": snapshot.pinned_at,
            "effective": bundle.scene_version,
            # 服务端算这一个比较，不留给读的人去比两个字符串：比错方向（拿 effective
            # 当钉住的）不会报错，只会让人按新规则去解释一批老工作。
            "pinned_is_effective": snapshot.scene_version == bundle.scene_version,
            # 上面那批文件**是从哪儿读到的**。``snapshot`` = 快照表里留下的那份字节
            # （钉住的是一个真正的版本）；``effective`` = 盘上现在这一份，只在它与
            # 钉住的那一版逐字节相同时才允许（见本函数上面的拒绝）。
            # 没有第三种取值，所以读到这一格的人不必自己去找那个拒绝。
            "content_from": "snapshot" if pinned_scene is not None else "effective",
            # 与 ``get_scene`` 的 files 同一形状、同一来源（``SceneFile.sha256``）：
            # 这里报的是**钉住的那一份**，那里报的是**现在生效的那一份**。
            "files": [
                {"path": entry.path, "sha256": entry.sha256, "bytes": len(entry.content)}
                for entry in (pinned_scene or bundle.scene_files).entries
            ],
            # §B16 关闭条件 7 逐字要的是「Serper / HTTP / Crawl4AI / Jev / 执行器的调用
            # 能反查是谁因哪一个 plan 步骤被选择」。反查的锚点是
            # ``EffectiveResearchPlan.source_methods``，而 **worker 拿不到那份计划**
            # （它在服务端，随受理那一刻的 ``ResearchContext`` 现编）—— 于是「这一步」在
            # 这一侧从来没有被交出去过，worker 手上的那一格只能靠猜。
            #
            # 交出去的是**这一份 Scene 允许从哪儿拿行**：声明开出来的那几条路，也就是
            # 计划里 ``strategy.source_methods`` 那一格在**没有场景块收窄**时的取值
            # （唯一的定义在 ``application/research_strategy.plan_source_methods``）。
            # 收下它的正是那三条研究工具与 ``decide``：它们拿同一个元组校验 worker
            # 声称的那一步（``tools.plan_step``）。**服务端 → worker 是唯一的方向**：
            # 这一格由服务端算，没有任何入口收下调用方送来的一份计划
            # （``runs.py`` 的模块 docstring：计划由服务端现编，不由调用方交一份）。
            #
            # 两处边界，写在这里免得被读大：
            #
            # * 它是**这一份 Scene** 的，不是**这一轮的 Run** 的。一次具体的 Run 还会
            #   按场景矩阵收窄一次，所以那一轮可能比这一格更窄 —— 拿宽的这一份比对
            #   不会放行声明没开过的步骤（只往严的一侧错）。
            # * 它读的是**现在生效**的 Scene，而这一块其余每一格读的都是**钉住**的那
            #   一份。这不是疏忽：收下这一步的那几条工具只读得到生效的那一份
            #   （它们拿到的是一条 ``Settings``，没有 work item 可以顺回去），两边
            #   用两份不同的字节会让「服务端交出的那一步」被工具拒掉 —— 而那种红在
            #   报告上看起来像接错了线。``pinned_is_effective`` 为 ``False`` 时，
            #   这一格与这一块的其余字段**不来自同一版字节**，读的人要自己接这一句。
            "plan_steps": list(steps),
        },
        # §3 把 ``decide`` 的输入写成「work item id, server-owned DecisionSpec id,
        # evidence/state references」。前两项在这里：work item id 是本回执的主语，
        # 题面 id 这一格就是它。第三项是研究员自己的活，所以不在这里。
        #
        # 没有这一块时，判定那条路**可达而无人指路** —— 工具接线齐全、``decide``
        # 能跑，而没有任何一处把 spec_id 交出去，于是台账上 decision_record 恒零行，
        # 与「这份 profile 没有声明过判定」不可区分。这是 §F 那条 FAIL 的根因。
        "decision_specs": decision_specs,
        "execution": {"dispatch_task_id": ref.dispatch_task_id, "mapped_at": ref.mapped_at},
        "attempt": {
            "attempt_id": attempt.attempt_id,
            "ordinal": attempt.ordinal,
            "holder": attempt.holder,
            "leased_at": attempt.leased_at,
        },
        # 与 ``RoleGuard`` 同源（见 docstring）。sorted 已经由 visible_tools 保证，
        # 这里不重排 —— 重排会掩盖「它来自另一处」这件事。
        "tool_rights": list(visible_tools(principal)),
        "budget": _job_usage(settings, ledger_factory, [item.subject_ref], 1),
        "notes": [
            "已受理的前置条件由**缺席时的那句话**回答，不由一个恒真的勾选清单回答："
            "哪一条不成立，返回的就是它的拒绝。",
            "这里没有「这次任务要做几件事」—— 一个 work item 就是一件事，"
            "针对 subject_ref；件数属于 get_job 的 progress，不属于这里。",
            "scene.summary 与装载层给的是同一份，不是这里另算的一遍。",
            "这一条租约可能是**这一次调用**刚建立的：没有活的租约时，它当场租给"
            "本次调用的主体（``attempt.leased_at`` 就是那一刻）。重发拿到的是同一条"
            "——同一个主体两次领活不会造出两条租约。",
            "``scene`` 块报的是**现在生效**的那一份规则；``guidance`` 块报的是这件工作"
            "**编译时钉住**的那一份，并且给出那批字节的逐文件摘要（``guidance.files``，"
            "``content_from`` 说明它们是从快照表读到的，还是由「盘上这一份恰好就是它」"
            "证成的）。``pinned_is_effective`` 为假时两份不是同一份 —— 那个 false 是要读的。\n\n"
            "``decision_specs`` 是**这件工作钉住的那一版**编译出来的全部判定题面"
            "（spec_id、版本、task，以及每道题的问法与选项 key 与描述）。``decide`` "
            "收的是其中的 ``spec_id``，题面本身仍然由服务端持有 —— 这一块给出的是"
            "**读得到**，不是**写得了**：没有任何入口收下调用方送来的一份题面（§4："
            "普通 worker 不得自造 DecisionSpec）。拿不到这一块时不要去猜题面，也不要"
            "把自己的判断当成一次判定写进结论 —— 那在台账上看起来与一次真的判定完全"
            "一样。\n\n"
            "`pinned_is_effective` 为假时，这一块与 `scene` 块**不是同一版字节**编译的；"
            "而 `decide` 那一侧也从钉住的那一版编译，所以这两块合起来是自洽的：**照这里"
            "给的 ``spec_id`` 去判，执行的仍然是你这一版题面**。",
            "``guidance.plan_steps`` 是**服务端**算出来的「这一轮允许从哪儿拿行」"
            "（计划里 `strategy.source_methods` 那一格在没有场景块收窄时的取值）。"
            "它是 worker 手上 `source_method` 那一格的**唯一**来路：`search_web` / "
            "`fetch_document` / `crawl_site` / `decide` 都拿它校验你声称的那一步，"
            "所以**照它填**、不要自己编一个步骤名，也不要留空 —— 留空说的是「这次调用"
            "不属于任何步骤」，而那是另一句话。\n\n"
            "两处边界：它是**这一份 Scene** 的，不是**这一轮的 Run** 的（那一轮可能"
            "更窄；拿宽的这一份比对不会放行声明没开过的步骤）；它读的是**现在生效**的"
            "Scene，而不是这一块其余字段所报的**钉住**那一份 —— 收下这一步的那几条工具"
            "只读得到生效的那一份，两边用两份字节会让交出去的那一步被工具拒掉。\n\n"
            "``plan_steps`` 读不出来时整次领活被拒（不退回一个空表，也不退回「都可以」）："
            "一整份读起来正常的 assignment 配一张空步骤表，与「这一轮没有步骤」同形，"
            "而后者是一个不可能的状态。",
        ],
    }


def _findings_from_arguments(
    observations: list[dict[str, Any]] | None,
    candidates: list[dict[str, Any]] | None,
    gaps: list[str] | None,
    relations: list[dict[str, Any]] | None = None,
) -> Findings:
    """把工具参数变成 ``Findings``，并把领域层的拒绝翻成工具结果。

    **校验只在领域层做一次。** 这里不重写「证据不能为空」「不能重复」那几条 ——
    重写一遍就是同一份规则的两个实现，而漂移的那一次会让工具收下一份领域层本来
    会拒绝的产出（或者反过来，拒绝一份合法的）。这一层只做一次翻译：
    ``ValueError`` → ``ToolError``，因为服务面的失败要走工具结果，不走异常链。

    ## ``relations`` 多了两格，而那两格由领域层强制

    责任结论的形状是 ``{"relation": …, "decision_id": …, "evidence_refs": […]}`` ——
    比另一类多一个 ``decision_id``（§3 要的是判定**真正被下游消费**）。它必填这件事
    在这里**不重判**：``RelationFinding.__post_init__`` 已经拒了空的那一个，而它的
    那句理由（「没有说它是哪一次判定得出的」）比这一层能编的任何一句都准。这里只
    做形状翻译，于是缺键或缺值都以 ``KeyError`` / ``ValueError`` 落到下面那段。
    """
    try:
        return Findings(
            observations=tuple(
                Finding(statement=entry["statement"], evidence_refs=tuple(entry["evidence_refs"]))
                for entry in observations or ()
            ),
            candidates=tuple(
                Finding(statement=entry["statement"], evidence_refs=tuple(entry["evidence_refs"]))
                for entry in candidates or ()
            ),
            gaps=tuple(gaps or ()),
            relations=tuple(
                RelationFinding(
                    relation=entry["relation"],
                    decision_id=entry["decision_id"],
                    evidence_refs=tuple(entry["evidence_refs"]),
                )
                for entry in relations or ()
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError(
            f"这份产出不合法：{exc}\n"
            "要交付出必须写成 observations / candidates 里的 "
            '{"statement": …, "evidence_refs": […]};缺口是 gaps 里的字符串 —— '
            "它按定义没有证据可挂。\n"
            "责任结论写成 relations 里的 "
            '{"relation": …, "decision_id": …, "evidence_refs": […]} —— '
            "decision_id 必填：一条指不出判定的关系结论与一条凭印象连起来的关系"
            "在盘上同形。"
        ) from exc


# ----------------------------------------------------------------------
# 一次研究里的责任结论（§3）
# ----------------------------------------------------------------------
#: 结算回执里 ``relations`` 那一块的读法。它是一个常量而不是拼在回执里，因为它要说
#: 的是一件**关于读法的事**：这一块与 ``record_relation`` 的回执回答的**不是同一个
#: 问题**（一个读这次的产出，一个读池子里那一行），而读的人若按后者来读，会把
#: 「这一次写了什么」读成「这条关系现在是什么样」。
_RELATIONS_NOTE = (
    "relations 这一块说的是**这次结算交上来的那些责任结论去了哪**，不是表里那一行"
    "现在是什么样：`record_relation` 的回执读的是池子（那一行在不在、证据是哪几条、"
    "什么时候第一次见到），这里读的是一次产出。两者都要有 —— 合成一份，总有一半人"
    "拿到的是一份关于另一个问题的答案。\n\n"
    "by_disposition 的四类：written 是这一次**写下去的关系**、folded 是与同一批里"
    "前面某一行合成了一条（证据取并集）、rejected 是没写（关系不在闭集里、账号 id "
    "不规范、或者它指的那次判定站不住 —— 逐行理由在 reason 里）、evidence_unknown "
    "是它挂的引用在这个库里解不开。后两类都是**那一行**没写，不是整次结算失败："
    "产出已经按 stop_reason 落库了。\n\n"
    "venue_asset_id 是这一件工作研究的**那一块场地**，由来源回执按账号 id 重新派生"
    "（不是从产出里读的）。它为 null 时每一条都被拒，为什么在 venue_refusal 里。"
)

#: 责任关系表没接线时每一行的那句话。它与应用层里「没接判定台账」那句是**两件事**
#: （一个是存储、一个是判定），处置也不同，所以各说各的。
_NO_RELATION_STORE = (
    "这一件工作写不了责任关系：本服务实例没有接责任关系表。\n"
    "§3 要的是每一条关系都由一次真实的判定支撑、并落到表里 —— 这里连那个表都没有，"
    "所以这一批一行都不写。这是一条**接线**事实（组合根没有构造那个存储），不是"
    "「这块场地没有负责方」—— 后者的处置是去研究，前者是去接线。"
)


def _researched_venue(
    *,
    settings: Settings,
    store: JobStore,
    item: Any,
    job: Any,
    assets: Callable[[], VenueAssetStore] | None,
) -> tuple[str, str]:
    """这件工作研究的是**哪一块场地**；说不出来时 ``("", 一条理由)``。

    ## 为什么由服务端算，而不是让研究员在产出里说一句

    §3 要的是一条「谁对**哪块**场地负责」的关系，而``venue_asset_id`` 是一个**派生**
    的句柄（``promote_receipt`` 那边从回执行号算出来），研究员手里没有它 —— 让它自己
    写一个，写错的那一次不会有任何东西变红：那块场地真的存在、那条句柄真的很像，
    于是关系落到一块不相干的场地上，而报告上它是一条正常的关系。所以这一格由服务端
    从**来源回执**按账号 id 重新派生（``promotion.venue_handle_for_account``），
    研究员写的任何东西都不参与。

    ## 四条到达方式，四条理由（合成一条会让处置去错地方）

    1. 这件工作没有冻住的快照、或者快照里没有主体 —— 这条正常路径上不会出现（领活
       之前就拒了），出现说明记录被改过，所以照实说。
    2. 这一类输入的主体不是从来源回执来的（``job.input_kind``）—— 它研究的不是一块
       场地，所以「是哪块场地」这个问题对它**不成立**。
    3. 回执本身读不出来 / 冻下来的来源引用不是这份回执（``receipt_for_subject`` 的
       三种）—— 处置是去核对受理那一刻写下的那一格。
    4. 回执里的行折不成这个账号，或者折出来的场地不在池子里 —— 处置是去
       ``promote_receipt``（池子里没有的 id，那一行在任何一份按场地读的报告里都
       出现不了，而它在表里躺着，看起来像研究过了）。

    ## 它不抛

    场地是一条**逐行**的理由，不是整次结算的失败：同一份产出里的观察、候选与其余
    结论与它无关。所以这里返回理由，由调用点把它交给 ``remember_researched_relations``
    逐行拒（那条理由比应用层能编的那句更具体 —— 它知道是哪一步说不出来的）。
    """
    snapshot = store.work_item_snapshot(item.work_item_id)
    if snapshot is None or snapshot.subject is None:
        return "", (
            "这件工作没有冻住的主体身份，所以说不出它研究的是哪一块场地。"
            "它在领活那一侧就该被拒（assignment 要求快照里有一个主体），走到这里说明"
            "这条记录被改过 —— 去核对这个 work item，而不是去重新受理。"
        )

    receipt, refusal = receipt_for_subject(
        snapshot.subject, inputs_dir=settings.inputs_dir, kind=job.input_kind
    )
    if receipt is None:
        return "", refusal

    handle = venue_handle_for_account(receipt, item.subject_ref)
    if not handle:
        return "", (
            f"这件工作的主体 {item.subject_ref!r} 折不出这一份来源回执里的任何一行"
            f"（{len(receipt.resolved_rows)} 行已研究）。\n"
            "三种到达方式，处置各不相同：这份回执不是当初编译这件工作的那一份、"
            "那一行在回执里不是研究件、或者这份回执根本没有这一行。"
            "不猜一块场地：猜错的那一次，关系会落到一块真的存在、句柄也真的像的场地"
            "上，而报告里那一行完全正常。"
        )
    if assets is None:
        return "", (
            "本服务实例没有接场地资产池，所以核不了这块场地在不在池子里。\n"
            "关系按场地 id 索引：池子里没有的 id，那一行在任何一份按场地读的报告里都"
            "出现不了 —— 而它在表里躺着，看起来像已经研究过了。这是一条**接线**事实。"
        )
    try:
        asset = assets().get_asset(handle)
    except StoreError as exc:
        return "", (
            f"读场地资产池失败，所以核不了这块场地在不在池子里：{exc}\n"
            "这是一条**存储**事实（本部署的状态库读不出来），与「这块场地不存在」"
            "的处置不同。"
        )
    if asset is None:
        return "", (
            f"场地资产池里没有 {handle!r} 这块场地（它由这份回执派生），"
            f"所以这一批关系挂不上去。\n"
            "处置是先把那份来源回执折进池子（`promote_receipt`）—— 折进去之后这块场地"
            "会带着同一份回执派生的 id 出现。不绕过池子写这一行，理由见上。"
        )
    return handle, ""


def _responsibility_spec(
    settings: Settings,
    *,
    records: Callable[[], DecisionRecordStore],
    work_item_id: str,
) -> DecisionSpec | None:
    """**这件工作钉住的那一版**声明的**责任关系**题面；没有声明就是 ``None``。

    题面的来源是 ``pinned_bundle``，不是盘上此刻那一份：结算核对的每一条关系都要
    由一次真实的判定支撑，而那条判定（``decide``）执行的是钉住的那一版题面 ——
    两边用两份字节时，这里会把一条**按老题面判出来的**结论判成「与题面对不上」，
    而报告上看起来像研究员判错了。

    Raises:
        ToolError: 钉住的字节读不到（``pinned_bundle``）、题面编译不过（声明的选项集
            与闭集对不上）、或者盘上那一份装载不出来（与钉住的是同一版时才会用到）。

    ## 为什么装载失败在这里**拒绝整份产出**（而不是逐行给理由）

    题面读不出来说的是这一份 profile 自己坏了，而不是「这一次研究的某一行有问题」：
    ``decide`` 那条路同样会撞上它（``decision_handlers`` 编译的是同一批题面），所以
    这不是一位研究员能处置的事。而它发生在 ``settle`` **之前** —— 于是**这一次什么
    都没有落下**，修好 profile 重交一次就是干净的。

    「这份 Scene 没声明这道题」是**另一件事**，它返回 ``None``：那时题面本来就不存在，
    每一行由应用层如实报「没有可核对的依据」。两者分开，是因为处置一个是修 profile、
    一个是去声明这道题 —— 或者承认这个 Scene 今天不产出责任关系。
    """
    try:
        # 盘上这一份只读一次（``require_bundle``），``pinned_bundle`` 收下它作为
        # 「现在生效的是哪一份」—— 各读一次会在同一次调用里造出两个时刻。
        pinned = pinned_bundle(
            settings,
            effective=require_bundle(settings),
            pinned=records().pinned_scene(work_item_id),
            work_item_id=work_item_id,
        )
        return compile_specs(pinned).get(DecisionTask.RESPONSIBILITY_RELATION)
    except StoreError as exc:
        raise ToolError(f"读这件工作钉住的 Scene 版本失败：{exc}") from exc
    except ConfigError as exc:
        raise ToolError(
            f"这一份产出带着责任结论，而本 Scene 的责任关系题面读不出来：{exc}\n"
            "§3 要的是每一条关系都由一次真实的判定支撑，而判定要有一道题面 —— "
            "题面读不出来就没有可核对的依据。\n"
            "**这一次没有落下任何东西**：这条判据在结算之前问，与「引用解不开就整份"
            "拒绝」在同一处。"
        ) from exc


def _researched_relations(
    *,
    settings: Settings,
    item: Any,
    findings: Findings,
    venue_asset_id: str,
    venue_refusal: str,
    spec: DecisionSpec | None,
    relation_store: Callable[[], AccountVenueRelationStore] | None,
    records: Callable[[], DecisionRecordStore] | None,
) -> dict[str, Any]:
    """这一次结算交上来的责任结论 → 回执里那一块 ``relations``。

    ## 它在 ``settle`` **之后**跑，而失败不升级成 ``ToolError``

    与 ``board_sync`` 同一条：产出已经落库了，把它报成一次失败会让调用方以为什么都
    没发生，于是重发 —— 而重发拿到的是 ``replayed: true`` 与**同一批关系**（写关系
    是幂等的：同一块场地、同一个账号、同一种关系折进同一行，证据取并集）。所以
    存储层的拒绝在这里变成回执里的一格，不是一次异常。

    顺序不能反（先写关系、后结算）：结算被拒时（换了结局、租约已经交还）表里会留下
    一批**属于一次没被受理的产出**的关系 —— 而它们在表里与一批正常的关系完全同形。
    """
    common: dict[str, Any] = {
        "venue_asset_id": venue_asset_id or None,
        "venue_refusal": venue_refusal,
    }
    if relation_store is None:
        outcomes = tuple(
            RelationOutcome(
                ordinal=ordinal,
                account_id=item.subject_ref,
                relation=finding.relation,
                disposition=RelationDisposition.REJECTED,
                reason=_NO_RELATION_STORE,
            )
            for ordinal, finding in enumerate(findings.relations)
        )
    else:
        # 证据库与关系表是**同一个库文件**，而这次结算带着责任结论 ⇒ 产出里至少有
        # 一条证据引用（``RelationFinding`` 强制非空）⇒ 上面那次 ``require_evidence_refs``
        # 已经证明了库读得出来（没有库时它整份拒绝）。这里按已经验过的事实写，见
        # ``product_loop._record_relation`` 的同一处。
        evidence = read_store(settings)
        assert evidence is not None  # noqa: S101 - 见上
        try:
            outcomes = remember_researched_relations(
                findings,
                store=relation_store(),
                evidence=evidence,
                decisions=None if records is None else records(),
                spec=spec,
                # 两格都取**本部署**的事实：Scene 取自配置、主体取自工作单元。产出里
                # 声称的主语不参与 —— 拿它自己声称的主语去核对它自己，等于没查
                # （与 ``require_evidence_refs`` 同一条）。
                workspace=settings.profile,
                account_id=item.subject_ref,
                work_item_id=item.work_item_id,
                venue_asset_id=venue_asset_id,
                venue_refusal=venue_refusal,
                seen_at=utc_now(),
            )
        except StoreError as exc:
            return {
                **common,
                "by_disposition": None,
                "outcomes": [],
                "error": str(exc),
                "note": (
                    f"{_RELATIONS_NOTE}\n\n"
                    "by_disposition 是 null：存储层在写的中途拒绝了这次调用（理由在 "
                    "error 里），所以「写下去了几行」没有答案 —— 报 0 会被读成"
                    "「一条都没写」，而那正是没有答案时最不该说的一句话。结算本身是"
                    "成功的：上面那几格与这一块无关。"
                ),
            }

    by_disposition = {member.value: 0 for member in RelationDisposition}
    for outcome in outcomes:
        by_disposition[outcome.disposition.value] += 1
    return {
        **common,
        "by_disposition": by_disposition,
        "outcomes": [_research_relation_row(outcome) for outcome in outcomes],
        "note": _RELATIONS_NOTE,
    }


def _research_relation_row(outcome: RelationOutcome) -> dict[str, Any]:
    """一条责任结论在**这一次结算**里的去向。

    比 ``product_loop._relation_outcome`` 少四格（证据、第一次见到、最后复核），而
    那不是省下来的：那四格读的是**表里那一行**（折过之后的证据是并集、时刻是更早的
    那个），而这里说的是这一次交上来的那一行。两处合成一份的代价是其中一边的人拿到
    一份关于另一个问题的答案 —— 而两份都读起来像在说同一件事。

    ``written`` 是 ``row is not None`` 的另一种说法，留着是因为读的人多半只想知道
    「这一条落下了没有」，而 ``disposition`` 的四个取值里有三个都是「没有」。
    """
    return {
        "ordinal": outcome.ordinal,
        "account_id": outcome.account_id,
        "relation": outcome.relation,
        "disposition": outcome.disposition.value,
        "written": outcome.row is not None,
        "reason": outcome.reason,
    }


#: ``submit_findings`` 回执里 ``routes`` 那一块的说明。与 ``_RELATIONS_NOTE`` 并列，
#: 而两块说的**不是**同一件事：那一块是这次交上来的责任结论（研究员写的），这一块是
#: 服务端从这次引用的证据里**绑出来的**联系人路线（材料自己写的字）。
_ROUTES_NOTE = (
    "这一块是服务端绑的：姓名、角色与邮箱都取自材料自己写的字，绑定层不认识任何"
    "业务词（角色词表与域名归属判据来自本部署的 Scene）。\n"
    "materials 是**逐份材料**的去向 —— 「绑定成功、什么都没绑出来」与「这份材料"
    "没绑成」是两件事，后者的那一份一行都不会落库。\n"
    "routes / opportunities 是这一次**算出来**的条数，不是表里的条数：表里那几条"
    "读 get_account。同一条路线上的 A/B/C/D 是**路线等级**，它在账号状态之外，"
    "两者互相不决定。\n"
    "rejections 是这一次**没收下**的那些：by_code 是分布，items 是逐条回执"
    "（候选原文、它属于哪一列、来源、证据、判据原文、规则版本、哪一趟）。"
    "分布回答「有多少」，items 回答「是哪几条」—— 只有分布时，"
    "「这一页没有联系人」与「域名判错了」在报告上是同一个数字。"
)


def _rejection_item(item: RouteRejection) -> dict[str, Any]:
    """一条回执的线上形状。**逐格映射，没有一格是这里判出来的。**"""
    return {
        "rejection_id": item.rejection_id,
        "account_id": item.account_id,
        "candidate": item.candidate,
        "field": item.field.value if item.field is not None else None,
        "code": item.code.value,
        "detail": item.detail,
        "rule": item.rule,
        "source_kind": item.source_kind.value if item.source_kind is not None else None,
        "source_url": item.source_url,
        "evidence_ids": list(item.evidence_ids),
        "run_id": item.run_id,
        "work_item_id": item.work_item_id,
        "occurred_at": item.occurred_at.isoformat(),
    }


def _rejections_block(tally_result: RejectionTally) -> dict[str, Any]:
    """拒收的分布 + 具体项（§6.3）—— **只是把用例的产物摆成 JSON**。

    分类、计数、排序、截断都在 :func:`~marketops.application.route_rejections.tally`
    里（§22：处理器只做协议映射）。这里曾经自己数过一遍，而那一遍与用例里那一遍
    是两份实现 —— 它们的差别只会在「同一批拒收在两处报出不同的数字」时暴露，那看
    起来像存储出了问题。

    ``by_run`` 与 ``by_code`` 并列摆出来，因为它们是两句不同的话：前者回答「是哪一类」，
    后者回答「是一趟还是一直在发生」—— 同一类拒收来自同一趟（那一页特别脏）与来自
    许多趟（判据本身坏了）处置完全不同，而只报前者时两者一模一样。
    """
    return {
        "total": tally_result.total,
        "by_code": dict(tally_result.by_code),
        "by_run": [{"run_id": run_id, "count": count} for run_id, count in tally_result.by_run],
        "items": [_rejection_item(item) for item in tally_result.items],
        "truncated": tally_result.truncated,
    }


def _no_rejections_yet() -> dict[str, Any]:
    """「算过了，一条都没拒」。**不是** ``None`` —— 那一格说的是「这一次没有算过」。"""
    return _rejections_block(tally(()))


def _routes_block(**overrides: Any) -> dict[str, Any]:
    """``routes`` 那一块的骨架。三种到达方式里有两格是 ``None`` 而不是 0。

    ``None`` 与 0 的分别在这块里是要紧的：``routes: null`` 说的是「这一次没有算
    **过**」，``routes: 0`` 说的是「算过了，一条都没绑出来」。把前者写成 0 会让
    「这一轮什么都没发生」变成一条关于材料的结论。
    """
    block: dict[str, Any] = {
        "evidence_refs": 0,
        "materials": [],
        "material_count": 0,
        "not_material": [],
        "routes": None,
        "by_grade": None,
        "opportunities": None,
        "evidence_written": None,
        # §6.3：拒收的**分布**与**具体项**。这一格此前不存在 —— 唯一读得出来的是
        # 一个总数，而「这一轮拒了 37 条」里的 37 条可能有三种完全不同的处置。
        # 与 ``routes`` 同一条纪律：``None`` 是「这一次没有算过」，
        # ``{"total": 0, ...}`` 才是「算过了，一条都没拒」。
        "rejections": None,
        "snippet_bindings_rejected": None,
        # 这一趟写进维护账的变更信号（§6：``put_signal`` 的生产路径）。``None``
        # 与 ``[]`` 同 ``routes`` 一条纪律：前者是「这一次没有算过」（没引证据 /
        # 重发 / 没有可绑材料），后者是「算过了，这一趟没有新来源」。
        "signals": None,
        "error": None,
        "note": _ROUTES_NOTE,
    }
    block.update(overrides)
    return block


def _researched_routes(
    *,
    settings: Settings,
    store: JobStore,
    item: Any,
    findings: Findings,
    replayed: bool,
    run_id: str,
    maintenance: Callable[[], MaintenanceStore] | None,
) -> dict[str, Any]:
    """这一次结算引用的证据 → 路线与路线机会。回执里那一块 ``routes``。

    ## 为什么这一格必须在**服务面**可达

    §4 的链是 ``submit_findings → validate → evidence binding → 姓名/角色/邮箱 →
    ContactRoute/RouteOpportunity → 定级 → 落库``。在接上这一趟之前，路线全仓只有
    一个生产者，而它的入口是 CLI 的 ``run-demo`` —— 于是研究员在服务面上把一轮
    工作做完、证据落了盘、账号池里有了行，**一条路线也产不出来**，而回执上不报
    任何异常。路线表的 0 与「这个市场里没有一个人有过联系方式」长得一样。

    ## 一趟在**应用层**，这里只做翻译

    ``application/contact_routes.produce_routes`` 是那唯一一趟（绑定 → 判时效 →
    定级 → 落盘），CLI 那条路走的是**同一个函数**。这一层做的是三件只有它做得了
    的事：主语从**这件工作冻住的快照**里取（不是让研究员在产出里声称）、材料从
    **这次引用的证据**里取、结局折成回执。MCP 侧再实现一遍绑定判据会让两条路各
    有一套规则，而漂移的那天两边都说自己是对的。

    ## 主语为什么取快照，而不是账号池那一行

    ``WorkItemSnapshot.subject`` 是受理那一刻冻住的 ``SubjectDossier``
    （``_researched_venue`` 读的是同一格），它的 ``account_id`` 就是 canonical
    账号 id（``application/inputs.py``），于是
    ``Evidence.account_id == Settlement.account_id == Route.account_id`` 成立。
    改读账号池那一行会把「这个组织**现在**叫什么」当成「这批材料当初交给谁」，
    而改名之后两者不同 —— 报告里那一行完全正常。

    ## 重发不重算

    ``settle`` 按内容判重（``replayed``）。重发的**理由**在这一版变了一半：
    路线与拒收回执现在是内容寻址的（观察 id、回执 id），重发一遍不会多出行 ——
    但 ``put_opportunity`` 仍然是一张只追加的表（``ports/store.py``：重复的一条
    机会更可能意味着一次没被合并的重复抓取，那件事应该被看见），重算会把同一批
    机会写成两批。所以在 ``replayed`` 为真时这一格照旧不跑，并如实报
    ``routes: null``：第一次结算已经绑过一遍，这一次没有算。

    ## 这一块**不抛**

    调用点在 ``settle`` **之后**，三行已经落库 —— 与 ``_researched_relations`` 和
    ``_sync_board`` 同一条。抛出去会让调用方以为产出没落盘，于是重发，拿到
    ``replayed: true``，而路线永远没有被绑过。所以装载失败、存储拒绝在这里都变成
    ``error`` 一格。

    ## ``maintenance`` 为什么在这里，以及 ``None`` 时回执要说什么

    这一趟绑定**真的**落下了新证据行时，``produce_routes`` 会当场给维护账记一条
    变更信号（§6：``put_signal`` 的生产路径）。这是这条结算路径唯一的写入 ——
    与 ``assets`` / ``relation_store`` / ``records`` 一样，一个**工厂**从组合根
    来，指向与产品环**同一个**库。

    ``None`` 时这一趟照常绑定，只是不记信号，而回执里**必须补一句**：空信号与
    「这一趟真的没有新来源」在产出上同形，回执是唯一分得开它们的地方。缺的是
    一格接线，不是一条业务结论 —— 两者的处置不同（前者去接线，后者去研究）。
    """
    refs = evidence_refs_of(findings)
    if not refs:
        return _routes_block(
            routes=0,
            by_grade=dict.fromkeys(_GRADE_ORDER, 0),
            opportunities=0,
            evidence_written=0,
            snippet_bindings_rejected=0,
            note=(
                "这次结算一条证据都没引用（产出是空的，或只有缺口），所以没有材料"
                f"可绑 —— 这不是「绑了但什么都没绑出来」。\n{_ROUTES_NOTE}"
            ),
        )

    snapshot = store.work_item_snapshot(item.work_item_id)
    if snapshot is None or snapshot.subject is None:
        return _routes_block(
            evidence_refs=len(refs),
            error=(
                "这件工作没有冻住的主体身份，所以说不出这些材料是关于谁的。"
                "它在领活那一侧就该被拒（assignment 要求快照里有一个主体），走到"
                "这里说明这条记录被改过 —— 去核对这个 work item，而不是重新受理。"
            ),
        )
    try:
        subject = BindingSubject.from_dossier(snapshot.subject)
    except ValueError as exc:
        return _routes_block(evidence_refs=len(refs), error=str(exc))

    evidence_store = read_store(settings)
    if evidence_store is None:
        # 上面那次 ``require_evidence_refs`` 已经证明库读得出来（引用解不开它整份
        # 拒绝），所以这一格经这条路径不可达。留着它是因为这一块**不抛**：一个
        # ``assert`` 在结算之后炸掉，代价比一句能读的理由大得多。
        return _routes_block(
            evidence_refs=len(refs),
            error=f"本服务实例现在读不到证据库（{settings.database_path.name}）。",
        )

    materials: list[RouteMaterial] = []
    not_material: list[dict[str, Any]] = []
    for ref in refs:
        row = evidence_store.get_evidence(ref)
        if row is None:
            not_material.append(
                {"evidence_id": ref, "reason": "这条引用现在解不开了（这次结算与那次核对之间被删掉）"}
            )
            continue
        if row.field is not None:
            not_material.append(
                {
                    "evidence_id": ref,
                    "reason": (
                        f"它挂在字段 {row.field.value!r} 上，是一条**字段依据**，不是一份材料"
                        "—— 绑定要的是材料本身（整页正文那一行）。"
                    ),
                }
            )
            continue
        if not row.binding_eligible:
            not_material.append(
                {
                    "evidence_id": ref,
                    "reason": (
                        f"来源层级是 {row.source_kind.value!r}，不在绑定许可集合里"
                        "（SOP L119：检索结果只能作补充证据）。"
                    ),
                }
            )
            continue
        materials.append(
            RouteMaterial(
                workspace=settings.profile,
                subject=subject,
                source_url=row.source_url,
                source_kind=row.source_kind,
                text=row.literal_value,
                content_sha256=row.content_sha256,
                fetched_at=row.fetched_at,
                parser=row.parser,
                http_status=row.http_status,
                tier=row.tier,
                origin=f"evidence:{ref}",
            )
        )

    if replayed:
        return _routes_block(
            evidence_refs=len(refs),
            material_count=len(materials),
            not_material=not_material,
            note=(
                "这一次是重发（replayed），第一次结算时已经绑过一遍，所以没有重算："
                "路线与拒收本身现在是幂等的（内容寻址的观察与回执），再算一遍不会多出行 "
                "—— 但**路线机会**仍然是一张只追加的表（见 ports/store.py：重复的一条"
                "更可能意味着一次没被合并的重复抓取，那件事应该被看见），再算一遍会把"
                "同一批机会写成两批。所以这一格照旧不重算，并如实报 null。\n"
                f"{_ROUTES_NOTE}"
            ),
        )

    if not materials:
        return _routes_block(
            evidence_refs=len(refs),
            not_material=not_material,
            routes=0,
            by_grade=dict.fromkeys(_GRADE_ORDER, 0),
            opportunities=0,
            evidence_written=0,
            rejections=_no_rejections_yet(),
            snippet_bindings_rejected=0,
            note=(
                "这次引用的证据里没有一份是可绑定的材料（逐条理由见 not_material），"
                f"所以绑定没有跑过。\n{_ROUTES_NOTE}"
            ),
        )

    try:
        bundle = require_bundle(settings)
        production = produce_routes(
            materials=materials,
            bundle=bundle,
            store=evidence_store,
            # 工厂在这里、且只在这里被调用一次：``produce_routes`` 需要的是
            # **存储本身**，而这一层拿到的是工厂（``sqlite3`` 的连接不能跨线程，
            # 与 ``jobs`` / ``scenes`` 同一条）。没有维护账时传 ``None`` ——
            # 出参那一句由下面那个 ``note`` 补上。
            maintenance=None if maintenance is None else maintenance(),
            # 这一趟的名字取**这次结算的租约号**：拒收回执要能回答「哪一趟拒的」
            # （§6.3），而这次执行在盘上的身份就是这条 attempt。取工作单元 id 会让
            # 「这件工作的每一次结算」共用一个名字 —— 而那正是「分不出是哪一趟」。
            context=RouteContext(run_id=run_id, work_item_id=item.work_item_id),
            vocabulary=RouteVocabulary.from_bundle(bundle),
            now=utc_now(),
        )
    except (ToolError, MarketOpsError) as exc:
        return _routes_block(
            evidence_refs=len(refs),
            material_count=len(materials),
            not_material=not_material,
            error=str(exc),
            note=(
                "这一次绑定没有跑成（理由在 error 里），所以 routes / opportunities / "
                "rejections 是 null 而不是 0 —— 0 会被读成「这些材料里一个人都没有」，"
                "而 rejections 的 0 会被读成「一条都没拒」。\n"
                f"{_ROUTES_NOTE}"
            ),
        )

    by_grade = dict.fromkeys(_GRADE_ORDER, 0)
    for route in production.routes:
        by_grade[route.grade.value] += 1
    return _routes_block(
        evidence_refs=len(refs),
        materials=[
            {
                "origin": outcome.origin,
                "status": outcome.status.value,
                "reason": outcome.reason,
                "routes": outcome.routes,
                "opportunities": outcome.opportunities,
                "evidence": outcome.evidence,
            }
            for outcome in production.outcomes
        ],
        material_count=len(materials),
        not_material=not_material,
        routes=len(production.routes),
        by_grade=by_grade,
        opportunities=len(production.opportunities),
        evidence_written=len(production.evidence),
        rejections=_rejections_block(tally(production.rejections, limit=MAX_ITEMS)),
        snippet_bindings_rejected=production.snippet_bindings_rejected,
        signals=[
            {
                "subject_id": signal.subject_id,
                "aspect": signal.aspect.value,
                "reason": signal.reason,
            }
            for signal in production.signals
        ],
        note=(
            "本次绑定落下的**新**来源已经给维护账记了变更信号（``signals`` 是"
            "那些主体），于是那一格的复核不必等满一个周期。\n"
            f"{_ROUTES_NOTE}"
            if maintenance is not None
            else (
                "本服务实例没有接维护账，所以这一趟没有记变更信号 —— 这是**接线"
                "事实**，不是「来源没有变化」：后者要等维护账接上之后由下一次结算"
                "回答。绑定、判时效、定级、落盘照常跑过。\n"
                f"{_ROUTES_NOTE}"
            )
        ),
    )


def _sync_board(
    *, jobs: JobStore, dispatcher: Dispatcher | None, work_item_id: str
) -> dict[str, Any]:
    """结算之后把结局回传到外部看板 —— 回执里那个 ``board_sync`` 块。

    ## 它为什么**不**抛

    调用点在结算**之后**。三行已经落库，回传失败不改变那个事实 —— 抛出去会让
    调用方以为产出没落盘，于是重发，拿到 ``replayed: true``，而看板永远没被同步
    过。一个「说错了但做完了」的中间态比一条明确的失败更坏。

    ## 执行器没接线也要**说那一句**

    那不是一次「同步失败」，是**这个进程**没有可同步的对象。两件事的处置不同
    （一个去看外面，一个去看服务面是怎么构造的），所以它有自己的那句话，与能力
    表 dispatch 行的 ``runtime_ready`` 同源 —— 都从 ``dispatcher`` 这个**对象**读，
    不重推 ``Settings``。这一格里的目标状态照报，来源与别处**同一个**
    （:func:`~marketops.application.dispatch.board_target`，读已落库的那条结局）。

    ## 回传失败之后由谁再来一次

    这一次回传失败不影响上面那句「结算成功」。失败之后的收敛走
    ``WorkDispatch.converge_settled`` —— 它在每一次派发轮次之后跑（见
    ``_dispatch_block``），从库里读目标、从外面读当下状态，所以**不需要**这一次
    在本地留下任何「待同步」记录。这里不再需要一段「本补丁不会让已经结算过的
    那几件变绿」的告示：那件事现在有名字、有入口、有判据。

    Args:
        jobs: 已经取出来的存储（不是工厂 —— 调用点已经取过一次了，再取一次会让
            「同一个进程里两次 ``jobs()`` 是不是同一个存储」变成一个要问的问题）。
        dispatcher: 这个实例构造出来的执行器；``None`` = 没接线。
        work_item_id: 刚结算的那件工作单元。**目标状态不是参数** —— 见上面
            ``board_target`` 那一条：要收敛到哪只从库里读。
    """
    if dispatcher is None:
        item = jobs.get_work_item(work_item_id)
        return {
            "task_id": None,
            "target_status": board_target(item) if item is not None else None,
            "observed_status": None,
            "synced": False,
            "changed": False,
            "error": (
                "外部执行器没有接线（这个进程没有把 Dispatcher 构造出来），"
                "所以这一次没有可同步的看板 —— 这不是「同步失败」。"
            ),
        }
    board = WorkDispatch(jobs=jobs, dispatcher=dispatcher).reconcile(work_item_id)
    return {
        "task_id": board.task_id,
        "target_status": board.target_status,
        "observed_status": board.observed_status,
        "synced": board.synced,
        "changed": board.changed,
        "error": board.error,
    }


def _research_terminals(
    *,
    store: JobStore,
    dispatcher: Dispatcher | None,
    worker_outcomes: Callable[[str], tuple[WorkerExitRecord, ...]] | None,
    items: Sequence[WorkItem],
) -> dict[str, Any]:
    """这个作业每一件工作单元的**研究终态回执** —— 报告里那个 ``research_terminals`` 块。

    ## 它为什么在报告里，而不是在一条新工具上

    回执是**逐件**的事实，而 ``get_job`` 就是逐件报那个作业的地方（``work_items``
    与它同一批主语）。另立一条工具会让同一件工作有两个读法，而两个读法漂移的那天，
    验收看哪一个就成了一个问题。

    ## 权威在 ``authoritative``，不在 ``external.dispatch_final_state``

    见 :mod:`marketops.application.research_terminal`。这里只做三件 I/O：读库、读
    看板（``dry_run=True``，**一次写入都不发**）、读外部那本退出账。**报告路径不得
    改外部状态** —— 一个「读一下报告就把看板推了一下」的接口会让报告与动作混起来。

    ## 读不出来 ≠ 没记过

    外部那本账读不出来（文件在、但有一行坏了）时 ``dispatch_records`` 报 ``None`` 并
    带上 ``dispatch_read_error``；``0`` 只表示「读成功了，关于这个任务一行都没有」。
    两者合成一个之后，「外面判了没落地」与「外面什么都没说」就长得一样了 —— 而
    这一格存在的全部理由就是让前者可见。

    这个块**不抛**：它是报告的一部分，而报告的其余部分已经读出来了。读不出来就
    如实报读不出来，别让一份报告因为一块附注而整份失败。

    Args:
        store: 已经取出来的作业存储。
        dispatcher: 这个实例的执行器；``None`` = 没接线，于是连外部都读不了。
        worker_outcomes: 按任务 id 读外部那本退出账的读器（由组合根注入 —— 这一层
            不认识任何具体的执行器适配器，见 ``ports/dispatch.py`` 的取向）。
        items: 要出回执的工作单元，顺序即报告顺序。
    """
    if dispatcher is None or worker_outcomes is None:
        return {
            "reported": False,
            "blocker": (
                "这个服务实例没有把外部执行器装起来（或者没有注入它的退出账读器），"
                "所以没有可核对的外部判决。业务终态本身照常由 work_items 那一段报出，"
                "缺的只是「外面说了什么」那一半。"
            ),
        }

    board = WorkDispatch(jobs=store, dispatcher=dispatcher)
    terminals: list[dict[str, Any]] = []
    for item in items:
        ref = store.execution_ref(item.work_item_id)
        attempts = store.attempts(item.work_item_id)
        sync = (
            None
            if ref is None
            else board.reconcile(item.work_item_id, dry_run=True)
        )
        read_error: str | None = None
        outcomes: tuple[WorkerExitRecord, ...] = ()
        if ref is not None:
            try:
                outcomes = worker_outcomes(ref.dispatch_task_id)
            except ConfigError as exc:
                # 读坏了。回执照常出，但**分母要改成「不知道」** —— 喂一个空元组
                # 进去会让它报 ``records: 0``，那是「外面一行都没记过」的意思，
                # 而这里是「读不出来」。唯一一处由这一层改写的格子。
                read_error = str(exc)

        payload = research_terminal(
            item=item,
            ref=ref,
            attempt=attempts[-1] if attempts else None,
            settlement=store.settlement(item.work_item_id),
            sync=sync,
            outcomes=outcomes,
        ).payload()
        if read_error is not None:
            payload["external"]["dispatch_records"] = None
            payload["external"]["dispatch_read_error"] = read_error
        terminals.append(payload)

    return {
        "reported": True,
        "authority": "marketops.settlement",
        "note": (
            "每一件的 ``authoritative`` 是本仓的完成判据（从已落库的停止理由读，"
            "与回传写下去的目标同一个来源）；``external`` 是外部那套自己的判决，"
            "原文照录、本仓不翻译。两者不一致时**以 authoritative 为准** —— "
            "外部那条落地判据问的是软件工程任务的问题，见回执里的 "
            "``code_landing_reason``。"
        ),
        "items": terminals,
    }


def _submit_findings(
    *,
    settings: Settings,
    jobs: Callable[[], JobStore],
    dispatcher: Dispatcher | None,
    work_item_id: str,
    attempt_id: str,
    stop_reason: str,
    observations: list[dict[str, Any]] | None,
    candidates: list[dict[str, Any]] | None,
    gaps: list[str] | None,
    relations: list[dict[str, Any]] | None,
    assets: Callable[[], VenueAssetStore] | None,
    relation_store: Callable[[], AccountVenueRelationStore] | None,
    records: Callable[[], DecisionRecordStore] | None,
    maintenance: Callable[[], MaintenanceStore] | None,
) -> dict[str, Any]:
    """受理一条租约的结果，并给出一张**说实话的回执**（§3 submit_findings）。

    ## 为什么要两个 id，而不是只收 attempt id

    端口上**刻意没有**「按 attempt id 读一条 attempt」的入口（见
    ``ports/jobs.py``：一条尝试不是一个独立存在的东西，``release`` / ``settle``
    收 attempt id 是因为它们内部查得到父记录）。所以只收 attempt id 的接口要么
    在存储层扫全表，要么在这里编一个反向索引 —— 两者都是把一件接口设计的事做
    成了查询。

    而两个 id 恰好都是 ``get_assignment`` 交出去的东西，所以调用方本来就两个都
    有。多要一个 id 还换来一件更重要的：**对不上的那对是一个拒绝，而不是一次
    静默的解析**。配错了 id 的调用方在这里拿到的是「这个 work item 上有哪几条
    尝试」，而它据此能自己看出自己拿的是别人的 assignment。

    ## 归属：只有持有这条租约的那个 worker 能结算它

    与分析面的每一处一样，判定取自传输层的身份，不取自参数 —— 而这里多一条：
    ``holder`` 必须等于调用方。放开这一步，任何研究侧主体都能结算别人的租约，
    而报告里留下的会是它自己的名字。

    ## 这一层不判「这条租约活不活」

    判它的是 ``settle``：活租约写下去、同结局的重发幂等返回、换了结局的拒绝、
    已经交还的拒绝。这一层只判**归属**，因为多判一步就会把「重发」判成
    「没有活的租约」—— 而重发正是这条路径最常见的到达方式（响应丢了）。

    ## 停止理由是闭集里的一个词

    六个成员一起列出来，理由与 ``submit_research`` 那份一样：调用方该知道
    「我写错了」还是「这个词不存在」。顺带 —— 停止理由**没有一个** ``completed``：
    §8B 逐字「No fixed positive A/B count is required」，而 ``goal_met`` 说的是
    「该覆盖的都覆盖了」，不是「找到了 N 个」。

    ## 回执里的 ``replayed`` 是这一格最要紧的

    幂等不是「静默返回同一个对象」：那样「又写了一遍」与「没有写」在回执里不可
    区分。``replayed: true`` 说的是「这一份已经在盘上，我没有再写一遍」。

    ## 引用解不开就整份拒绝，而这一步在**结算之前**

    领域层保证 ``evidence_refs`` 非空、去重、形如一条 id；那三条都是**形状**
    判据，对一个精心编造的 id 与一条真证据给出同一种回答。存在性查在
    ``application/evidence_refs``（那里住着那条规则的唯一实现，三个
    ``EvidenceStore`` 实现都要过同一组判据）。

    **而存在性只是一半。** §4 逐字还要求 ``evidence.workspace == work_item.workspace``
    与 ``evidence.account_id == work_item.account_id``，所以这一次调用把两格都交出去：

    * ``workspace`` 取 ``settings.profile`` —— 本部署服务的那一个 Scene。这里**没有**
      「工作单元自己的 workspace」可取：``WorkItem`` 上没有这一格（Scene 是作业的，
      见 ``_attempt_for``），而一个部署只服务一个 Scene 正是 ``require_scene`` 在上游
      已经判过的那件事。
    * ``account_id`` 取 ``item.subject_ref`` —— 一件工作**针对谁**。账号研究这条路上
      它就是 canonical 账号 id（``application/inputs.py`` 的 ``SubjectDossier.account_id``，
      ``adapters/memory.py::compile_work_items`` 逐字如此），所以「这条证据是不是这个
      账号的」有答案。

    两个方向都不是洁癖。一条解得开、却属于另一个账号的引用，在回执上与一条真依据
    同形，而它是**把 B 的材料写进了 A 的结论**（§8 的并发隔离要的正是这一条）；跨
    Scene 那一半在 sqlite 上连存储层都不拦（库的绑定记在库里，不来自配置），理由写在
    ``application/evidence_refs.require_evidence_refs`` 的 docstring。

    顺序不能反：先查引用、再 ``settle``。反过来的话，一份编了依据的产出会先
    落库、再被拒绝，于是「拒绝」与「落库」同时为真 —— 而那句话两边都不算数。

    一个**代价**要写下来：这次拒绝发生在幂等判断之前，所以一份已经结算过的
    产出，若它引用的证据在这之后从这台机器上消失了，重发会拿到这次拒绝而
    不是 ``replayed: true``。这是刻意的 —— 失败关闭，而且「这个部署的证据库
    不见了」本来就不该被报告成「这件事早就做完了」。那种故障要在部署那一侧
    被看见，不是在这里被静默。

    ## 回传看板发生在**结算成功之后**，而它的失败不改判决

    顺序不能反：回传成功而结算失败，会得到一张「看板说做完了、盘上没有产出」的
    作业 —— 那比不同步坏得多。所以 ``settle`` 先返回，``board_sync`` 后读。

    回传失败**不**升级成 ``ToolError``（``_sync_board`` 只报），理由写在那个函数
    里。因此读回执的纪律是：**先看 ``stop_reason``，再看 ``board_sync``** ——
    前者是这一次到底做成了什么，后者是关于外面那张看板的一件事。

    ## 路线在**这一步**绑出来（§4 那条链的收口）

    回执里那块 ``routes`` 是服务端从这次引用的证据里绑出来的联系人与路线机会
    （``application/contact_routes.produce_routes``，CLI 那条路走的是同一个函数）。
    在这一趟接上之前，路线全仓只有一个生产者，而它的入口是 CLI —— 研究员在服务面上
    把一轮工作做完、证据落了盘，一条路线也产不出来，而回执上什么异常都没有：路线表
    的 0 与「这个市场里没有一个人有过联系方式」长得一样。

    它排在 ``settle`` 之后、``board_sync`` 之前，理由与上面那两块完全相同：产出已经
    落库了，绑定失败不改变那个事实。所以它**不抛**，失败写在 ``error`` 那一格里，而
    那时 ``routes`` 是 ``null`` 而不是 0。

    重发时它不再算一遍（``replayed``）—— ``settle`` 按内容判重，而 ``put_route``
    是一次 ``INSERT``，不按 id 幂等。判据与那个后果都写在 ``_researched_routes`` 里。
    """
    store = jobs()
    item, job, attempt = _attempt_for(
        settings, store, (work_item_id or "").strip(), (attempt_id or "").strip()
    )
    _require_holder(attempt, current_principal())

    try:
        reason = StopReason((stop_reason or "").strip())
    except ValueError as exc:
        allowed = sorted(member.value for member in StopReason)
        raise ToolError(
            f"没有 {stop_reason!r} 这个停止理由；可用的是 {allowed}。\n"
            "这个集合里**没有** completed / success 这样的词：一次研究的结束是"
            "「为什么停」，不是「成功了」——零候选也是一个诚实的结果。"
        ) from exc

    findings = _findings_from_arguments(observations, candidates, gaps, relations)
    try:
        require_evidence_refs(
            read_store(settings),
            findings,
            workspace=settings.profile,
            account_id=item.subject_ref,
        )
    except EvidenceError as exc:
        raise ToolError(str(exc)) from exc

    # 责任结论那一段的两条前置**在 settle 之前**问，与上一条同一个位置、同一个
    # 理由：一次拒绝不该留下一份已经落库的产出。这里与上一条的差别是它们**不**
    # 拒绝整份产出 —— 场地说不出来是一条逐行的去向，题面装载失败才是拒绝
    # （那是这一份 profile 自己坏了，`decide` 那条路也走不通）。
    venue_asset_id = venue_refusal = ""
    spec: DecisionSpec | None = None
    if findings.relations:
        venue_asset_id, venue_refusal = _researched_venue(
            settings=settings, store=store, item=item, job=job, assets=assets
        )
        # 没有判定落点时**不去问钉住的题面**：这个部署交上来的每一条关系都会以
        # 「没有判定记录落点」被逐行拒掉（见 ``_researched_relations``），题面那一格
        # 根本不会被读到。硬要读会把这个合法部署变成一次**整份拒绝**，而那条拒绝
        # 说的是另一件事（这一份 profile 自己坏了）—— 两种部署的处置不同。
        if records is not None:
            spec = _responsibility_spec(
                settings, records=records, work_item_id=item.work_item_id
            )

    try:
        settlement = store.settle(attempt.attempt_id, stop_reason=reason, findings=findings)
    except JobStateError as exc:
        # 存储层的拒绝原样透出：它自己的措辞已经把那条规则说完（已交还 / 换了
        # 结局 / 作业已取消），在这里重编一句会让同一条规则有两个说法，而漂移的
        # 那一次表现为「工具说的」与「契约测试验的」不是同一件事。
        raise ToolError(str(exc)) from exc

    receipt: dict[str, Any] = {
        "attempt_id": settlement.attempt.attempt_id,
        "work_item_id": item.work_item_id,
        "job_id": item.job_id,
        "stop_reason": reason.value,
        "content_sha256": settlement.content_sha256,
        "recorded_at": settlement.recorded_at,
        "replayed": settlement.replayed,
        "counts": {
            "observations": len(findings.observations),
            "candidates": len(findings.candidates),
            "gaps": len(findings.gaps),
            "relations": len(findings.relations),
        },
    }
    if findings.relations:
        receipt["relations"] = _researched_relations(
            settings=settings,
            item=item,
            findings=findings,
            venue_asset_id=venue_asset_id,
            venue_refusal=venue_refusal,
            spec=spec,
            relation_store=relation_store,
            records=records,
        )

    receipt["routes"] = _researched_routes(
        settings=settings,
        store=store,
        item=item,
        findings=findings,
        replayed=settlement.replayed,
        run_id=settlement.attempt.attempt_id,
        maintenance=maintenance,
    )

    receipt["board_sync"] = _sync_board(
        jobs=store, dispatcher=dispatcher, work_item_id=item.work_item_id
    )
    notes = [
        "counts 数的是**结论**（观察 + 候选 + 责任结论），缺口不计入 —— 缺口是结论的"
        "缺席。全为 0 是一次诚实的空结果：跑到这里就说明有一次真实的运行。\n"
        "relations 是**交上来**的责任结论条数（产出里写了几条），不是写进表里的条数"
        "—— 后者在下面那一块里（没有交责任结论时那一块根本不出现）。",
        "replayed 为真表示这一份产出已经在盘上，本次没有再写一遍 —— 它是"
        "「重发是否幂等」唯一可区分的那种答案。",
        "这里没有「产出存在哪」—— 它按 work item 落盘，读它的是 get_job："
        "两处各报一次路径必然先漂移一处。",
        "routes 是服务端从这次引用的证据里**绑出来的**路线，不是交上来的："
        "materials 逐份给去向（绑定成功 ≠ 绑出了东西），routes 为 null 说的是"
        "「这一次没有算过」而不是「一条都没绑出来」。",
        "board_sync 是关于**外部看板**的一件事，它不影响上面任何一句：结算"
        "成功就是成功。synced 为假表示看板没跟上（原因在 error 里），"
        "changed 为假而 synced 为真表示上一次已经同步过、这一次没动它。",
    ]
    if findings.relations:
        notes.append(_RELATIONS_NOTE)
    receipt["notes"] = notes
    return receipt

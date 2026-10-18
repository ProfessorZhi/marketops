"""Phase F 的**决策**工具：``decide``（发起一次判定）与 ``get_decision``（读回一条）。

``docs/MCP_SERVICE.md`` §3 那一行的落点 —— 进：「服务端持有的 DecisionSpec id、
证据/状态引用」；出：「真实的已配置 Jev/Laya 结果，或显式的 unavailable /
abstained，外加 DecisionRecord id」。

`ports/decisions.py` 落下了**判定**这个概念，两个 adapter 落下了它的两个实现，
``application/decision_specs.py`` 落下了题面的生产者。本模块是第四块：**调用点**。
少了它，前三块都在，而 §4 的 MVP 验收条件（「一个可调用的决策工具」）不成立。

## 一条判定是**付过费**的，所以它必须落下来

``DecisionRecord`` 曾经只活在那一次工具调用的返回值里（本模块此前的
``_NOT_STORED_NOTE`` 逐字承认了这件事）。那有两个后果，都不是洁癖：

* **它会在重启后消失。** 服务重启、或者换一个客户端来读，就什么都没有了 ——
  而 §10 要的正是「回执 → DB 行 → 重启 / 新客户端 → 同一个 decision_id 读回」。
* **同一个问题会被问第二次，而第二次也付钱。** ``record_id`` 是内容寻址的，
  但配方里含 ``decided_at``，所以重放算出的是**另一个** id —— 内容寻址挡的是
  「同一件事被记成两条」，挡不住「同一件事被做两次」。花钱的是第二次调用。

所以这一层现在有两件事是它必须做的，而且都发生在**付费调用之前**：

* 按 ``(work_item_id, provider, case_id)`` 问一句「这个 backend 判过这个 case
  没有」。后两段由输入与**问谁**派生，都**不含时钟**，所以重放认得出来。命中就
  直接返回库里那一行，``replayed`` 为 true。``provider`` 那一段不能少：同一份
  输入被两个后端各判一次是**两次**付费，把 Laya 的答案当成 Jev 那次的重放，
  读起来会像「换后端不要钱」。
* 按 ``work_item_id`` 问一句「这件工作在不在、它针对谁、这些引用在不在它的范围
  内」。这一条同时挡住「B 的判定引用 A 的证据」（§8 的并发隔离）与「一条无主的
  记录」（§2 明确不要「通用模型记忆库」）。

两处都在调用之前，因为一条注定会被拒的判定不该付钱。它们**不是**判据的唯一实现
—— 存储在自己的写事务里再问一次，那一次才是承重的（见
``adapters/sqlite/decisions.py`` 的模块 docstring）。

## 输入里没有题面

调用方拿到的是 ``spec_id``，**不是**一份题面。题面由服务端从冻结的 profile 编译
（``application/decision_specs.py``），而这里做的是**精确匹配**：``spec_id`` 找不到
就报错，绝不「用一个默认题面兜着」。

这是「普通 worker 不得自造 DecisionSpec」（``docs/ARCHITECTURE.md`` §7 逐字）唯一
可能的执行器。一条写在文档里的规矩，如果服务端接受调用方送来的题目，就等于没有：
调用方少写一个选项、把 instructions 改得更「好答」，两次记录上的 ``spec_version``
会不同 —— 而**题面一改，旧标签就静默不可比了**，报告上却只显示「后端不一致」。

## 没有 ``mode`` 参数

``decide`` 恒以 ``DecisionMode.ASSIST`` 调用后端（``_MODE``）。这不是省事：

* §4 要求影子的答案**对研究侧隐藏**。一个 ``mode="shadow"`` 的参数，就是一条让
  调用方把影子答案要回去的入口 —— 而这条入口一旦存在，它迟早会被用上，且用它的
  那一次在记录上与 assist 完全同形。
* 影子的调用点是 ``application/shadow.py::run_shadow``，它同样**没有** ``mode``
  形参，理由写在那个函数上。两条路径用同一条纪律：模式是**路径**的属性，不是
  每次调用的参数。一个能传的模式参数，会同时是这两条路上的洞。

``DecisionProvider.decide`` 本身**有** ``mode``（必填）。那是端口对实现的约束，
与工具面对调用方的约束是两件事 —— 本模块是那条约束的执行点，而执行点有两处：
传下去的那个值恒为 ``_MODE``，以及**回执的模式必须是它**（``_decide`` 里那条
守卫）。少了第二处，一个回了别的模式的实现会把一条影子记录交到研究侧，而回执
上完全看不出来 —— 判断这件事的字段（``record.mode``）不在任何人的读数范围里。

## 上限住在这一层，且**拒绝不截断**

``fetch_document`` 的正文预览、``search_web`` 的查询文本都有服务端上限，这里也有
（``MAX_STATE_CHARS`` / ``MAX_EVIDENCE_REFS``），而且校验**全部发生在推理之前**。
§4 逐字：「Check evidence completeness, language and input length before
inference. No silent truncation.」—— 后半句正是不截断的理由：裁掉一段 state 之后
后端判的是**另一段文本**，而那条记录看起来完全正常。

这一层的界**不是**任何模型的窗口：两个后端的窗口各自不同，而且它们自己会判
（laya 算的是精确 token 数，窗口 512；jev 的窗口在服务端，我们不知道）。这里挡的是
「把一整页文档当 state 塞进来」那种调用 —— 它对两个后端都注定是弃权，而对 jev 是
一次**付过费的**弃权。

## 依赖是注入的

``advisory`` 由组合根传进来（``interfaces/mcp/__main__.py``）。理由与 ``research.py``
那一段相同（分层：``adapters/`` import ``interfaces/`` 会让分层反向），外加一条这
一族特有的：``LayaDecisionProvider`` 每个实例各持一个 421M 参数的 Router，所以
**一个进程里只能构造一次**，构造它的是 ``interfaces/composition.default_decisions``。

``advisory`` 为 ``None`` 时这个工具**仍然接线**（不是 stub），它明确回答「本部署
没有指定辅助后端」。这与 ``search_web`` 在 ``search=None`` 时的处置是同一条：
一个**说得清理由的拒绝**，好过一个「尚未实现」—— 后者的处置是等，而前者的处置
是加一个环境变量。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ...application.decision_specs import compile_specs
from ...config import Settings
from ...domain.enums import DecisionMode, QuestionKind
from ...domain.errors import ConfigError, StoreError
from ...domain.util import check_canonical_id
from ...ports.decisions import (
    ChoiceAnswer,
    DecisionAnswer,
    DecisionCase,
    DecisionProvider,
    DecisionRecord,
    DecisionRecordStore,
    DecisionSpec,
    YesNoAnswer,
)
from .tools import Handler, pinned_bundle, plan_step, plan_steps, require_bundle

__all__ = [
    "MAX_EVIDENCE_REFS",
    "MAX_STATE_CHARS",
    "decision_handlers",
]

#: ``state`` 的字符上限。见模块 docstring：**不是**模型窗口，是请求侧的资源界。
#:
#: 这个数是从上游推出来的，不是挑的：组装 state 的材料通常来自 ``fetch_document``
#: 的 ``text_preview``（``research.MAX_PREVIEW_CHARS`` = 4000），所以 8000 是
#: 「两页正文」，比任何一次正常的拼接都宽，而比「把一份文档整个塞进来」窄。
MAX_STATE_CHARS = 8000

#: 证据引用的条数上限。引用进 ``input_sha256``，也逐字进记录 —— 一份无界的清单
#: 让一次工具调用可以被用来构造一个任意大的回执。
MAX_EVIDENCE_REFS = 50

#: ``decide`` 恒以它调用后端。见模块 docstring —— 这不是一个可以传的参数。
_MODE = DecisionMode.ASSIST

#: 造 ``DecisionCase`` 时用的**过渡** case_id。它从不离开 ``_case()``（只活在两行
#: 之间），而它必须是一个合法的非空 id：``DecisionCase`` 在构造期就拒绝空值。
_PENDING_CASE = "pending"

#: 一条回答里“后端没报”的那两个字段。与 ``ports/decisions.py`` 一致：``None``
#: 不是 0、也不是均匀分布。
_NO_REPORT = "None 表示**后端没报**，不是 0、也不是均匀分布。"

_ASSIST_NOTE = (
    "这条回执是 **assist**：一个已配置的后端给出的咨询答案，轨迹因此标记为受过"
    "辅助（§4）。它**不是** shadow baseline —— 影子是另一条路径：两个后端跑同一"
    "批冻结样本、答案对研究侧隐藏，且**没有 MCP 入口**。它也不是 automated "
    "acceptance：``DecisionMode`` 里没有那个成员，这是刻意的。"
)

_OUTCOME_NOTE = (
    "outcome 的三个取值要分开读：answered 是一条结论；abstained 是**这个后端**对"
    "**这份输入**不肯答（处置：换输入或换后端）；unavailable 是**它没被接上**"
    "（缺凭据 / 缺 checkpoint —— §4 逐字：那是 blocker，不是一次伪造的成功推理）。"
    "后两者上 reason 必非空。"
)

_YES_NO_NOTE = (
    "yes_no 的题只回概率，**没有标签**：把它折成 yes/no 必须先选一个阈值，而 §4 要求"
    "任何阈值都要有独立留出评估的支撑 —— 本构建没有做过那次评估。要标签的调用方得"
    "自己写下那个阈值，而那件事会留在**它自己**的代码里，不会伪装成后端给的答案。"
)

_NO_RECORD_STORE = (
    "本实例没有判定记录的落点，所以这次判定不会发生。"
    "这不是一条注意事项，是一次拒绝：一条判定的记录是**付过费**的（Jev 是外部"
    "计费 API），而一个存不下来的判定会在服务重启或换一个客户端之后消失 —— "
    "「它发生过」这件事只留在那一次调用的返回值里。"
    "缺的是作业存储那一侧的接线（interfaces/mcp/__main__.py::wire_decisions）。"
    "判定的归属是**一件工作单元**，所以没有作业存储就没有可归属的记录。"
)

_STORED_NOTE = (
    "record_id 是**内容寻址**的（输入 + 后端 + 判定时刻），所以同一次判定的重试不会"
    "造出两条记录（§8：陈旧的尝试不得覆盖更新的已提交结果）。"
    "这条记录**是持久的**：可按 record_id 用 get_decision 取回，也挂在 "
    "work_item_id 那一行上（get_job 的 work_items 报的主语就是它）。"
    "**幂等键不是 record_id** 而是（work_item_id, provider, case_id）—— 配方里的 "
    "decided_at 让同一次调用的重放算出另一个 record_id，所以挡得住重复付费的是后者。"
    "用同一件工作、同一个后端与同一份输入再调一次，回执里 replayed 为 true 且内容"
    "逐键相同，而这一次**没有问过后端**（那正是「不付第二次费」的判据）。"
    "换一个后端**不是**重放：那是另一次判定、另一次付费，也是一条独立的记录。"
)

def decision_handlers(
    *,
    settings: Settings,
    advisory: DecisionProvider | None,
    records: Callable[[], DecisionRecordStore] | None = None,
) -> dict[str, Handler]:
    """本构建的决策处理器，按工具名索引。

    两条：``decide``（发起一次判定）与 ``get_decision``（按 id 读回一条）。

    ## 为什么要两条，以及为什么读回那条在**这一层**

    ``decide`` 的回执只活在**那一次调用**里。只靠重放读回来是不够的：重放要求
    调用方能把题面、状态、引用**逐字重新给一遍**，而一个只记下了 ``record_id`` 的
    审计者做不到这件事。少这一条，那份记录对「事后核对」是不可用的 —— 它只对
    「当时那个进程」可用。

    Args:
        settings: 配置。用来定位冻结的 profile —— **题面是从它编译的**，不是
            调用方给的。
        advisory: ``decide`` 会咨询的那**一个**后端，由组合根解析后传进来
            （``interfaces/composition.advisory_decision_provider``）。
            ``None`` 表示本部署没指定辅助后端；见模块 docstring 最后一段：
            那时工具照常接线，并明确说出这件事。
        records: 判定记录的存储**工厂**。与 ``research.py`` 的 ``ledger_factory``
            同一个理由：``sqlite3`` 的连接默认不允许跨线程使用，而工具处理器可能
            在别的线程上跑。``None`` 表示本实例没有落点 —— 那时两条工具照常接线，
            并明确回答「本实例没有判定记录的落点」。**不用内存实现兜底**：一个
            判了却在重启后消失的记录，比一条「没有落点」的拒绝坏得多，而它看起来
            完全正常（与 ``build_server`` 的 ``jobs`` 同一条判据）。
    """

    def decide(
        spec_id: str,
        work_item_id: str,
        state: str,
        evidence_refs: list[str] | None = None,
        source_method: str = "",
    ) -> dict[str, Any]:
        return _decide(
            settings=settings,
            advisory=advisory,
            records=records,
            spec_id=spec_id,
            work_item_id=work_item_id,
            state=state,
            evidence_refs=evidence_refs,
            source_method=source_method,
        )

    def get_decision(record_id: str) -> dict[str, Any]:
        return _get_decision(records=records, record_id=record_id)

    return {"decide": decide, "get_decision": get_decision}


# ----------------------------------------------------------------------
def _get_decision(
    *, records: Callable[[], DecisionRecordStore] | None, record_id: str
) -> dict[str, Any]:
    """按 ``record_id`` 读回一条判定记录。

    ``found: false`` 在这里是**一次拒绝**而不是一个正常的返回值：与
    ``get_evidence`` 的取向不同，那条接口问的是「这条引用解不解得开」（一条解不开
    的引用是那次研究的事实之一），这一条问的是「我刚交出去的那条记录还在不在」——
    而一个交出去过、现在读不回来的记录 id，只可能意味着记录没落盘、或者被指错了。

    读回来的形状与 ``decide`` 那次返回的**逐键相同**，除了 ``replayed``（那是
    「这一次调用有没有问后端」的事实，不是记录的一部分）。所以调用方不必判断
    「这是不是同一个形状」—— 一份「读回来的是另一个形状」的接口会让两边的字段
    慢慢漂开，而漂开之后没有任何一处会红。
    """
    if records is None:
        raise ToolError(_NO_RECORD_STORE)
    wanted = (record_id or "").strip()
    if not wanted:
        raise ToolError(
            "record_id 不能为空。它是 decide 回执里那一格 —— 内容寻址的，所以"
            "同一个 id 每次指向同一条记录。"
        )
    row = records().get_decision(wanted)
    if row is None:
        raise ToolError(
            f"没有 record_id 为 {wanted!r} 的判定记录。\n"
            "这与「那条记录没有结论」是两件事：后者会是一条 outcome 为 abstained "
            "或 unavailable 的记录，它照样读得回来。"
        )
    return {**row, "replayed": False}


def _decide(
    *,
    settings: Settings,
    advisory: DecisionProvider | None,
    records: Callable[[], DecisionRecordStore] | None,
    spec_id: str,
    work_item_id: str,
    state: str,
    evidence_refs: list[str] | None,
    source_method: str,
) -> dict[str, Any]:
    """校验 → 找幂等 → 查范围 → 编译题面 → 造 case → 判定 → 落盘 → 投影成回执。

    校验**全部**在任何后端调用之前，包括 ``advisory is None`` 与 ``records is
    None`` 那两条。它们在最前面，因为它们连 profile 都不必读：本部署没有辅助后端、
    或者没有落点时，这次调用注定不会有结果，而「注定不会有结果的一步」不该先去做
    别的事（``research.py`` 对 ``crawl_site`` 那六条校验写的是同一句话）。

    次序里有两处是**判据**，不是风格：

    * ``work_item_id`` 与 ``evidence_refs`` 的范围校验夹在**幂等检查之后、后端调用
      之前**。放在幂等之前会把一次合法的重放变成一次拒绝（那条记录的引用当时是
      合法的，而证据表此后可能被清理过）；放在后端之后，就是为一次注定被拒的判定
      付了费。
    * 落盘在**投影之前**没有区别，但必须在返回之前 —— 一个「跑完了、记录了、就是
      没存下来」的失败会让回执上那条 record_id 指向一个不存在的东西，而它看起来
      与一次成功的判定完全一样。
    """
    wanted = (spec_id or "").strip()
    if not wanted:
        raise ToolError(
            "spec_id 不能为空。题面由服务端持有 —— 这个工具收的是一个 **id**，"
            "不是一份题面（§4：普通 worker 不得自造 DecisionSpec）。"
        )
    if advisory is None:
        raise ToolError(
            "本部署没有指定辅助后端，所以这次判定不会发生。"
            "最常见的原因是没配 MARKETOPS_DECISION_PROVIDER（get_capabilities 的 "
            "integrations 里 jev / laya 两行的 wired 与 blocker 会说明），"
            "也可能是这个实例只接了控制面。"
            "这不是一次「判过了、后端弃权」—— 两者在报告上必须分得开："
            "前者的处置是加一个环境变量，后者要换输入或换后端。"
        )
    if records is None:
        raise ToolError(_NO_RECORD_STORE)

    item = (work_item_id or "").strip()
    if not item:
        raise ToolError(
            "work_item_id 不能为空。一条判定记录必须挂在**一件工作单元**上 —— "
            "无主的记录是「通用模型记忆库」的第一块砖：它没有主语，于是谁都可以"
            "往里写、谁都可以从里读。\n"
            "这一格同时是幂等键的一半（work_item_id + case_id），所以缺了它，"
            "「同一个问题问第二次不重复付费」也无从判定。"
        )

    text = (state or "").strip()
    if not text:
        # 与 ``DecisionCase`` 的构造期判据同一条，但这一句更早、也更说得清 ——
        # 一次没有输入的判定只会得到后端凭空的回答。
        raise ToolError(
            "state 不能为空：它是这次判定要判的那段**证据文本**。"
            "空 state 不是「没有可判的东西」，是「调用方忘了带输入」。"
        )
    if len(text) > MAX_STATE_CHARS:
        raise ToolError(
            f"state 超过 {MAX_STATE_CHARS} 字符（实际 {len(text)}）。"
            "这里**不截断**：裁掉之后后端判的是另一段文本，而那条记录看起来完全正常"
            "（§4：No silent truncation）。"
            f"这个上限不是模型的窗口 —— 两个后端的窗口各自不同、且它们自己会判；"
            f"它挡的是把一整页文档当 state 塞进来 —— 那种调用对两个后端都注定弃权，"
            "而对 jev 是一次**付过费的**弃权。要判一整页，先把要判的那几段挑出来。"
        )

    # 「这次判定服务计划里的哪一步」（§B16 关闭条件 7）。它排在这里、**在付费调用
    # 之前**，与 ``state`` 那几条同一条理由：一次注定被拒的判定不该先花掉一次
    # jev 的钱。校验用的是 ``plan_step`` —— 与三条研究工具**同一个**函数，所以
    # 「服务端交出的那一步」与「工具收下的那一步」不可能各按一套规则走。
    #
    # 读的是**生效**那一份 Scene 的计划，不是这件工作钉住的那一份（``_specs`` 用
    # 的是后者）。这不是疏忽：worker 手上的那一格来自 ``guidance.plan_steps``，而
    # 那个手交出去的就是生效的那一份（收下它的几条工具只拿得到一条 ``Settings``）。
    # 这里若跟着钉住的字节走，编译之后有人改过 profile 时，一个正确的步骤会被拒
    # —— 而那种红在报告上看起来像接错了线。
    step = plan_step(source_method, steps=plan_steps(require_bundle(settings)))

    refs = _evidence_refs(evidence_refs)
    # 落点在**编译题面之前**拿到手：题面来自**这件工作钉住的那一版**，而那批字节
    # 只有落点读得到（见 ``_specs``）。这里与「落盘在投影之前」那条次序无关 ——
    # 那一条说的是写，这一条说的是读。
    store = records()
    specs = _specs(settings, store=store, work_item_id=item)
    spec = specs.get(wanted)
    if spec is None:
        raise ToolError(
            f"没有 spec_id 为 {wanted!r} 的题面。这件工作**钉住的那一版**编译出来的"
            f"题面是 {sorted(specs)}。\n"
            "  这里不接受调用方送来的题面，也不回落到其中任何一个：题面是服务端从"
            "冻结的 profile 编译的，而一条「找不到就用默认的」回落会让两次记录上的 "
            "spec_version 不同而调用方毫不知情 —— 那正是「题面改过、标签却照旧比」"
            "的入口。\n"
            "  注意这一格说的是**钉住的那一版**，不是盘上此刻那一版：编译之后有人改了"
            "profile 时，两边能问的题面可以不同，而这一件工作该问的仍然是它编译时"
            "钉住的那一份。"
        )

    case = _case(spec, text, refs)

    # 幂等：**付费之前**先问一句这个 case 判过没有。见 :data:`_STORED_NOTE`：
    # record_id 含 decided_at，所以内容寻址挡不住重复付费；挡它的是
    # (work_item, provider, case_id) —— 三段都要，因为「同一个案子换一个提供方再判
    # 一次」是**第二次判断**、要花第二次钱，而 case_id 由输入派生、不含时钟。
    try:
        existing = store.decision_for_case(item, advisory.name, case.case_id)
    except StoreError as exc:
        raise ToolError(f"读判定记录失败：{exc}") from exc
    if existing is not None:
        # 返回的是**库里那一行**，不另写一句话。一次重放与一次新判定若在正文上有
        # 任何不同，同一个 record_id 就会有两种写法 —— 而那是「给同一个事实起两个
        # 名字」最直接的一种。这一次调用与那一次的区别只有 ``replayed`` 一格，
        # 因为它是「这一次有没有问后端」的事实，不是那条记录的一部分。
        return {**existing, "replayed": True}

    # 范围：这件工作存在吗、它针对谁、这些引用在不在它的范围内。**在付费之前**
    # —— 一条指向别人的账号或解不开的引用，在这里就已经能看出来。
    try:
        store.check_scope(item, refs)
    except StoreError as exc:
        raise ToolError(str(exc)) from exc

    record = advisory.decide(case, mode=_MODE)
    if record.provider != advisory.name:
        # 幂等键的三段里有一段是 ``provider``，而这个工具在**调用之前**只能用
        # ``advisory.name`` 去查（那时还没有记录）。两者若可以不同，这条链就断了：
        # 查的时候用一个名字、写的时候用另一个，于是重放查不中、照旧再付一次费，
        # 而写下去的那一行还挂在两个名字中的某一个上。
        #
        # 两个真实实现都取 ``self.name``（``adapters/jev.py`` / ``adapters/laya.py``），
        # 所以这不是在防一次手滑，是在防「同一个事实有两个来源」——一段可以被后端
        # 自报改写的查询键。回执如实写着它，所以这不是一次数据损坏。
        raise ToolError(
            f"{advisory.name!r} 回的记录声称 provider 是 {record.provider!r}，"
            f"而这次调用问的是 {advisory.name!r}。"
            "幂等键里含 provider，而这个工具在调用之前只能按它自己的名字去查 —— "
            "两个名字若可以不同，「同一份输入问第二次」就查不中，于是照旧再付一次费。"
        )
    if record.mode is not _MODE:
        # 后端回了一条**不是本次模式**的记录。回执如实写着模式，所以这不是一次数据
        # 损坏；被破坏的是 §4 的两条可见性规则，而它们都不在这一层之外可见：
        # 一条 shadow 记录被交给研究侧，就是把「影子答案不交给研究侧」这条规矩的执行
        # 交给调用方去读一个它没理由读的字段；一条别的模式则会以「assisted」之外的
        # 标记进轨迹。两种都在回执上看起来完全正常。
        raise ToolError(
            f"{advisory.name!r} 回的记录声称模式是 {record.mode.value!r}，"
            f"而这次调用用的是 {_MODE.value!r}。"
            "这里整次拒绝：影子与辅助的可见性不同（§4 —— 影子的答案不进研究轨迹），"
            "把一条模式不对的记录交出去，等于由调用方去读一个它没理由读的字段"
            "来决定要不要相信这一条。"
        )
    # ``source_method`` 那一格**不属于** ``DecisionRecord``（后端不知道这次判定
    # 服务计划里的哪一步，也不该知道），所以它由这里加上去，而不是塞进 ``_receipt``
    # 那份逐键投影里：``_receipt`` 的纪律是「记录里有一条不多、一条不少」，把一次
    # **调用的**上下文混进去，会让那条纪律变成一句无法核对的话。
    #
    # 它落盘，所以重放读回来的那一行**照样带着它** —— 一条记录说什么就是什么，
    # 而「这次调用服务了哪一步」正是条件 7 要反查的那一格（`docs/MCP_SERVICE.md`
    # §4 的回执清单里也写了它）。
    try:
        row = store.put_decision(item, {**_receipt(record), "source_method": step})
    except StoreError as exc:
        raise ToolError(f"判定记录没能落盘：{exc}") from exc
    return {**row, "replayed": False}


def _evidence_refs(values: list[str] | None) -> tuple[str, ...]:
    """校验证据引用清单。判据与 ``research._query_texts`` 同形：每条要么原样，
    要么整次调用被拒。

    **每条都要是一个规范 id**，不是一个随手写的字符串。理由不是洁癖：服务面的
    instructions 对调用方承诺「任何结论都应当能追到一条已抓取的公开文档」，而这些
    引用就是那条承诺在记录里的落点。收下一个解不开的引用，会让那条承诺变成一句
    无法验证的话 —— 而它在记录上看起来与一条真的引用一模一样。
    """
    if values is None:
        return ()
    if not isinstance(values, list):
        raise ToolError(f"evidence_refs 必须是一个字符串列表，实际拿到 {type(values).__name__}")
    if len(values) > MAX_EVIDENCE_REFS:
        raise ToolError(
            f"evidence_refs 最多 {MAX_EVIDENCE_REFS} 条，实际 {len(values)} 条。"
            "它们逐字进记录，所以条数上限就是这条记录的大小上限。"
        )
    cleaned: list[str] = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            raise ToolError(f"证据引用必须是非空字符串，实际拿到 {item!r}")
        try:
            cleaned.append(check_canonical_id(item.strip(), "evidence_refs"))
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
    return tuple(cleaned)


def _case(spec: DecisionSpec, state: str, refs: tuple[str, ...]) -> DecisionCase:
    """造一个 case，``case_id`` 由**输入本身**派生。

    为什么不是调用方给的：一个可以传进来的 case_id 能让两次不同的输入共用一个
    身份，而 ``record_id`` 里含 case_id —— 于是「同一件事的两次观测」与「两件事」
    在台账上分不开。为什么不是 ``utc_now()``：那样同一次判定的重试会得到两个
    case，而重试恰好是这里唯一会发生两次的事情。

    做法是**先按占位 id 造出来，再拿它的指纹改 id**：``input_sha256`` 是
    ``DecisionCase`` 自己算的（题面版本 + 题目清单 + 文本 + 引用），重写一遍它的
    配方就会有两份实现 —— 而两份实现里漂移的那一份，产出的是一条 case 与它自己
    的哈希对不上的记录。
    """
    draft = DecisionCase(case_id=_PENDING_CASE, spec=spec, state=state, evidence_refs=refs)
    return replace(draft, case_id=f"{spec.spec_id}#{draft.input_sha256[:16]}")


def _specs(
    settings: Settings,
    *,
    store: DecisionRecordStore,
    work_item_id: str,
) -> dict[str, DecisionSpec]:
    """这件工作**钉住的那一版**编译好的**全部**题面，按 ``spec_id`` 索引。

    每次都重新编译，不缓存：题面是从 profile 编译的，而 profile 是盘上的文件。
    缓存一份，会让「改了 profile 但服务没重启」表现为「题面没改」—— 而
    ``spec_version`` 的存在正是为了让题面改动能被看见。

    ## 为什么是**钉住的那一版**，不是盘上此刻那一版

    §6 逐字：Running tasks retain their pinned guidance。判定是**这件工作**在答题，
    所以执行的那一份题面必须是它编译时被钉住的那一版。从前这里读的是盘上此刻的
    profile：编译之后有人改了 profile，一件在飞的工作就会按**新题面**被判定，而
    记录里写着 ``pinned`` 的那一版 —— 回执上两个版本都在，行为却已经跟着新的走了。
    现在字节从 ``scene_snapshot`` 按 ``work_item_snapshot.scene_version`` 读回来，
    读不到就拒绝（``pinned_bundle``：那是唯一一条「这件工作按哪一份规则在跑」的
    来源，没有第三种回落）。

    这里与 ``get_assignment`` 的 ``decision_specs`` **必须同源**：那里交出去的
    ``spec_id`` 与版本，就是这里拿去匹配的那一份。两边各取一份，交出去的 id 会在
    这边匹配到另一个版本的题面（或者匹配不到），而回执上看起来完全正常。

    Raises:
        ToolError: 钉住的字节读不到、盘上那一份装载失败，或某个已声明的题面编译
            不过（闭集与声明的选项对不上）。都是**部署**问题，但要走工具结果回去：
            抛出去会被 SDK 换成一句通用的 ``UnexpectedToolError``，真实原因留不下来。
    """
    try:
        # 盘上这一份只读一次，作为「现在生效的是哪一份」交给 ``pinned_bundle``
        # —— 它自己再读一次会在同一次调用里造出两个时刻（见那个函数）。
        bundle = pinned_bundle(
            settings,
            effective=require_bundle(settings),
            pinned=store.pinned_scene(work_item_id),
            work_item_id=work_item_id,
        )
    except StoreError as exc:
        raise ToolError(f"读这件工作钉住的 Scene 版本失败：{exc}") from exc
    try:
        return {spec.spec_id: spec for spec in compile_specs(bundle).values()}
    except ConfigError as exc:
        raise ToolError(f"题面编译失败：{exc}") from exc


def _receipt(record: DecisionRecord) -> dict[str, Any]:
    """一条 ``DecisionRecord`` 的投影。**字段一条不多、一条不少**。

    ``ports/decisions.py`` 把 §4 那一句列举逐条落在了 ``DecisionRecord`` 上
    （spec 版本、输入哈希、provider/model/checkpoint 与 preprocessing、答案、
    概率、延迟、用量、失败或弃权理由）。这里不做取舍：任何一处「这个字段调用方
    大概不看」的省略，都是把一条可审计的记录变成一句结论。
    """
    return {
        "record_id": record.record_id,
        "case_id": record.case_id,
        "spec_id": record.spec_id,
        "spec_version": record.spec_version,
        "input_sha256": record.input_sha256,
        "provider": record.provider,
        "mode": record.mode.value,
        "outcome": record.outcome.value,
        "answered": record.answered,
        "answers": [_answer_view(answer) for answer in record.answers],
        "reason": record.reason,
        "model": record.model,
        "checkpoint": record.checkpoint,
        "preprocessing": {
            "language": record.preprocessing.language,
            "route": record.preprocessing.route,
            "input_chars": record.preprocessing.input_chars,
        },
        "usage": {
            "input_tokens": record.usage.input_tokens,
            "output_tokens": record.usage.output_tokens,
            "billed": record.usage.billed,
        },
        "latency_ms": record.latency_ms,
        "decided_at": record.decided_at,
        "evidence_refs": list(record.evidence_refs),
        "note": (
            _ASSIST_NOTE
            + _OUTCOME_NOTE
            + _YES_NO_NOTE
            + f"answers 里 choice 的 distribution 与 confidence：{_NO_REPORT}"
            "preprocessing.route 是**后端自己的话**（选了什么 checkpoint、为什么），"
            "不是我们重新措辞的。usage.billed 说的是这笔调用有没有产生外部计量成本"
            "（laya 本地 ⇒ false，jev ⇒ true）；金额刻意不在这里 —— §8 要求未知的"
            "外部用量在对账之前保持不确定。"
            + _STORED_NOTE
        ),
    }


def _answer_view(answer: DecisionAnswer) -> dict[str, Any]:
    """一条回答。两个成员的形状**按各自的类型**投影，不拉平。

    拉平（比如都出一组 ``key: probability``）看起来更整齐，代价是把「这是一道
    是否题」这件事从回执里抹掉 —— 而读的人接着就会把那个概率当成一个选项的得分。
    """
    if isinstance(answer, YesNoAnswer):
        return {
            "question_id": answer.question_id,
            "kind": QuestionKind.YES_NO.value,
            "probability": answer.probability,
            "confidence": answer.confidence,
        }
    if isinstance(answer, ChoiceAnswer):
        return {
            "question_id": answer.question_id,
            "kind": QuestionKind.CHOICE.value,
            "choice": answer.choice,
            "distribution": (
                None
                if answer.distribution is None
                else [
                    {"key": key, "probability": probability}
                    for key, probability in answer.distribution
                ]
            ),
            "confidence": answer.confidence,
        }
    # 端口只声明了两个成员，所以走到这里说明 ``ports/decisions.py`` 加了第三个而
    # 这一处没跟上。静默跳过它，会让那条回答**在回执里不存在**，而记录里明明有
    # —— 那正是「声明了却没人填」的反向形态。
    raise ToolError(
        f"不认识的回答类型 {type(answer).__name__}（题号 {getattr(answer, 'question_id', '?')}）。"
        "端口新增了成员，而回执的投影没有跟上 —— 这里不跳过它："
        "少一条回答的回执读起来与「后端只答了这些」完全一样。"
    )

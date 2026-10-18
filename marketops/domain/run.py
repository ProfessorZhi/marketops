"""Run —— 「同一个工作区、再走一轮」的那个单位（计划 §B17）。

分家情况：枚举在 ``domain/enums.py``（:class:`~marketops.domain.enums.ExecutionMode`
与 :class:`~marketops.domain.enums.RunStatus`），存储的契约在 ``ports/runs.py``，
实现分别在 ``adapters/sqlite/runs.py`` 与 ``adapters/memory.py``。本模块放的是
**两方都必须一致**的两样：:class:`RunSpec` / :class:`Run` 的完整形状，以及
:func:`run_id` / :func:`spec_fingerprint` 两条推导规则。

## 它为什么是独立于 ``ResearchJob`` 的一层

``ResearchJob`` 答的是「这个作业放不放行派发」。它**不答**「这一轮比上一轮多知道
了什么」—— 那是 §B17 要的东西：把一次增量做成有授权、有信封、有截止、结束时有
Delta 的一等记录。

两层的分界取自计划原话：「保留已受理 Run 内部自动续跑」。续跑已经是一个既有性质
（``application/continuation.py`` 的 ``ContinuationController``，§B21），Run 不去
重造执行器，它只圈范围、定信封、记账。

## 十二格，计划那十格一格不少

计划逐字列了 ``RunSpec`` 的十格：``workspace/workstream``、``objective``、
``selector/cohort``、``ResearchContext ref``、``EffectiveResearchPlan ref``、
``execution_mode``、``budget envelope``、``cutoff``、``policy versions``、
``idempotency key``。十样都在，其中**第一格是两个字段**（``workspace`` 与
``workstream``，见下面第一条差量），所以字段数是十二而不是十一。

**第十一格是 ``window``，它不是计划那十格里的。** 计划把授权窗口写成
``execution_mode`` 那一格的一部分（逐字：「``bounded_auto``：只有显式授权窗口存在
时，按累计对象数/预算/截止时间/stop conditions 创建后续 Run」），而受理那一刻需要
把它**原样写下来**才谈得上「只有……存在时」。所以它是单独一格，且由
:class:`AuthorizationWindow` 承载，见那里的 docstring。

**其中三格今天的取值与将来不同，逐一写清（不写清就是伪造）：**

* **第一格。** 计划把它写成 ``workspace/workstream`` 一格，而这是**两件事**：
  「这个 Run 属于哪个工作区」（授权与隔离的边界）与「这个 Run 是哪一次启动之下的
  第几轮」（生命周期的容器）。``workspace`` 那一格从第一天起就只答前一件，取值来自
  ``domain/scene.py::SceneVersion.workspace``；``workstream`` 是**另一个字段**
  （:attr:`RunSpec.workstream`），由启动路径写下，缺省 ``None`` 表示「这一轮不在
  任何一次启动之下」——那一格是**说出来的**，不是一个空串。改名那一半不成立：
  把 ``workspace`` 改叫 ``workstream`` 会让「这个 Run 属于哪个工作区」在盘上
  无处可查。
* **第三格。** ``selector`` 今天承载的是 **scene_id**：Scene 是盘上唯一的对象范围
  容器（``SceneVersion`` 的准入/排除那一族）。将来 selector/cohort 落地时它会被
  拆成两件事（选择规则、以及规则选出来的那批对象），同样不是改名。
* **第八格。** ``cutoff`` 是本次 Run 的**运行截止**。仓里另有两处同名的东西：
  ``domain/receipt.py::cutoff`` 是上游母池申报的截止时间，``application/refresh.py``
  的 ``cutoff`` 是刷新窗口的下界。**两处都不是「这次跑到什么时候为止」**，这是第一个。

## 没有「进行中」状态

照 ``domain/job.py::WorkItem`` 的形状：Run 上**没有** ``state`` 枚举，只有
``stop_reason``。「有没有人在跑」由作业层的租约回答（``AttemptState.LEASED``），
把它也编码成一个字段会让「已受理但还没人动」与「有人正在跑」有两个真相来源 ——
而 ``JobState`` 里已经有一个 ``QUEUED`` 在回答同一件事。

``stop_reason`` 为 ``None`` ⟺ 这个 Run 还在跑。它**不是**「还没开始」：受理成功
就进入这个状态，与 ``ResearchJob`` 从 ``QUEUED`` 起步是同一个道理。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import field_validator, model_validator

from .base import StrictModel
from .enums import BudgetLine, ExecutionMode, RunStatus
from .run_delta import RunDelta
from .util import SEP, check_canonical_id, sha256_text, to_utc
from .workstream import check_workstream_id

if TYPE_CHECKING:  # pragma: no cover - 只为注解，运行时不需要
    from .research_plan import EffectiveResearchPlan

#: 内容寻址 id 的截断长度。与 ``domain/job.py::_ID_CHARS`` 同一个数与同一条理由。
_ID_CHARS = 32


def _derive(kind: str, *parts: str) -> str:
    """``kind`` 参与哈希，是 id 空间不相交的**唯一**依据 —— 不是注释。

    与 ``domain/job.py::_derive`` 同一族（那里把理由讲全了）。Run 是第四个 id
    空间：``kind`` 那一段就是「这一行说的是谁」的答案，少一个前缀会让两个不同的
    东西拿到同一个 id。

    这条「两处各写一份」是刻意的，代价由判据兜着（``tests/test_run_contract.py``
    比对四个空间的截断长度，并断言同一组输入在四个空间里不撞）。
    """
    return sha256_text(SEP.join((kind, *parts)))[:_ID_CHARS]


def run_id(workspace: str, idempotency_key: str) -> str:
    """内容寻址：同一个 ``(workspace, key)`` 永远得到同一个 Run。

    幂等由**主键**保证，而不是靠「写之前先查一下」—— 与 ``domain/job.py::job_id``
    是同一条推导，理由也相同。

    ## 键由调用方给，服务端不生成

    这不是接口习惯。§B17 条件 3 要求「manual 下完成后保持 idle、绝不自行扩下
    一批」—— 服务端一旦能自己造一个键，它就能造出第二个 Run，而「自行扩下一批」
    正是从那里开始的。**键在调用方手里，这件事在结构上写不出来。**

    ``workspace`` 参与 id 意味着「同一个键在两个工作区下是两个 Run」。这是对的：
    Run 的作用域就是工作区，键只在作用域内可比。
    """
    return _derive("run", workspace, idempotency_key)


def policy_versions_of(plan: EffectiveResearchPlan) -> tuple[str, ...]:
    """一份计划读到的策略版本 —— ``RunSpec.policy_versions`` 的**唯一**生产者。

    元素是 ``<module_id>@<revision>``。两半都不能省：``module_id`` 答「是哪一个
    模块」，``revision`` 答「它那一版读到的声明长什么样」（见
    ``domain/research_plan.py::ModuleRevision`` —— 它是一个内容摘要，不是一个
    手写的版本号）。只留模块名回答不了「两次 Run 读的是不是同一份策略」，只留
    摘要则回答不了「是哪一块变了」。

    顺序照 :data:`~marketops.domain.research_plan.MODULE_PRIORITY`（计划已经把
    ``modules`` 按它排好了），所以同一份计划两次推导得到逐字相同的元组。
    """
    return tuple(f"{module.module_id.value}@{module.revision}" for module in plan.modules)


class RunBudgetLine(StrictModel):
    """Run 信封里的一条上限。

    **与 ``domain/scene.py::SceneBudgetLine`` 同形状，不是同一个类型。** 一条说的是
    「这个场景允许用多少」，落进 ``SceneVersion``，改它要**新写一版并留批准记录**；
    另一条说的是「这一次允许用多少」，在受理那一刻定下、此后不再改。合成一个类型
    会让「这次超支是场景上限还是本次信封」在回执上没有答案 —— 而处置完全不同：
    前者要人去改声明，后者只是这一轮到此为止。

    科目用既有的 ``BudgetLine``：搜索一次、抓一个文档、跑一次推理的量纲不同，
    合成一个总数字之后没人能回答「这次超支是搜索跑多了还是模型跑多了」。
    """

    line: BudgetLine
    ceiling: int

    @field_validator("ceiling")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError(
                "预算上限不得为负。0 是合法的（这一轮不走这条线），负数不是 —— "
                "它会让「还剩多少」在第一次扣减之前就成为一个没人算得清的数。"
            )
        return value


class AuthorizationWindow(StrictModel):
    """一次**自动授权**的范围 —— 三个阈值，横跨这条授权链下的**所有轮**。

    ## 它是「谁在什么时候决定下一轮」这件事的**载体**

    ``execution_mode`` 说「这一轮是被谁授权跑起来的」，而 ``BOUNDED_AUTO`` 那一句
    逐字是「在受理时写下的窗口（对象数 / 累计预算 / 截止时间）之内可以自己接下一
    轮」。**窗口就是那个「写下来」** —— 在本片之前，那句话在盘上无处成立，于是
    ``admission_refusal`` 只能受理即拒，而它自己写明「这不是缺陷，拒绝要比假装
    受理诚实」。

    ## 与 ``RunSpec.budget`` / ``Cutoff`` 不是一件事

    那两格说的是「**这一轮**允许用多少、跑到什么时候」；这里的三个说的是「**这条
    链**总共允许走多远」。两者都要在，而且缺一不可：只有信封的时候，每一轮各自
    够用而合起来可以无限；只有窗口的时候，一轮自己就能把整个窗口吃光，于是「下一
    轮还开不开得起来」要等它跑完才有答案 —— 而那已经不是授权了，是事后追认。

    ## 三个阈值都是必填的

    §B17 条件 4 逐字：「bounded_auto 在达到对象数/累计预算/截止时间**任一** stop 后
    不创建下一 Run」。「任一」要求三个都能被求值 —— 一个缺席的阈值会让那句话少判
    一条，而它在报告上与「那条判据没触发」同形。这与 ``RunSpec`` 上那条「budget
    不得为空」是同一条理由，连措辞都借它：**「没设过」与「还没到」必须分得开**。
    """

    #: 这条链累计最多走多少个对象。
    max_objects: int
    #: 这条链累计最多花多少 —— 逐科目。借用 :class:`RunBudgetLine` 的**形状**。
    budget: tuple[RunBudgetLine, ...]
    #: 这条链最迟到什么时候（含）。
    not_after: datetime

    @field_validator("max_objects")
    @classmethod
    def _at_least_one_object(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                f"max_objects 是 {value}，而「一个对象都不许走」的窗口**就是没有授权**。\n"
                "它与「没写窗口」在能做的事上完全一样，只在记录上不一样 —— 于是"
                "「这条链为什么一轮都没开」会被读成「窗口设小了」，而真实的原因是"
                "没有人授权过。0 在 ``RunBudgetLine.ceiling`` 上是合法的（那说的是"
                "「这一轮不走这条线」），而这里说的是整条链的范围，两句不是一句话。"
            )
        return value

    @field_validator("budget")
    @classmethod
    def _budget_is_an_envelope(cls, value: tuple[RunBudgetLine, ...]) -> tuple[RunBudgetLine, ...]:
        if not value:
            raise ValueError(
                "窗口的 budget 不得为空。§B17 条件 4 要求「达累计预算时 stop」—— "
                "一条线都没有的窗口在那个阈值上永远不触发，而「没设过」与「还没到」"
                "在回执上同形（与 ``RunSpec`` 那条「budget 不得为空」同一条理由）。"
            )
        lines = [line.line for line in value]
        repeated = sorted({line.value for line in lines if lines.count(line) > 1})
        if repeated:
            raise ValueError(
                f"同一个科目在窗口里出现了多次：{repeated}。\n"
                "两条上限谁说了算没有答案 —— 取小的会把另一条读成「够用」，取大的会"
                "把另一条读成「还没到」。与 ``RunSpec`` 的信封同一条纪律。"
            )
        return value

    @field_validator("not_after")
    @classmethod
    def _not_after_is_timezone_aware(cls, value: datetime) -> datetime:
        return to_utc(value)

    @property
    def digest(self) -> str:
        """这条链的**标识**：窗口值相同的两轮属于同一条授权链。

        ## 为什么是内容摘要，而不是一条链引用

        两种做法都能回答「这个窗口下已经跑了哪些轮」：一条 ``continuation_of``
        指针，或者这个摘要。**摘要赢在它不会断** —— 指针是一串引用，中间一轮的
        记录一丢，前后就分成两条链，而报告上看不出断过。摘要不依赖任何一行还在。

        这与 ``SceneVersion.refresh.policy_version``（内容摘要）是同一条做法。

        ## 预算线先排序，因为「换个位置」不是一次授权的改变

        摘要对**内容**敏感、对**写法**不敏感。同一个窗口的两条线换个顺序，算出来的
        摘要必须相同，否则「这两轮是不是同一条链」会因为一份等价的写法而答「不是」
        —— 而那会让累计在两条并行的链上各算一半，于是两个都没到阈值。

        （``policy_versions_of`` 用同一个手法：它按 ``MODULE_PRIORITY`` 排过序，
        所以同一份计划两次推导得到逐字相同的元组。）
        """
        lines = sorted(f"{line.line.value}:{line.ceiling}" for line in self.budget)
        return sha256_text(
            SEP.join(
                (
                    "objects",
                    str(self.max_objects),
                    "budget",
                    *lines,
                    "not_after",
                    self.not_after.isoformat(),
                )
            )
        )


class RunSpec(StrictModel):
    """**这一次增量要干什么** —— 计划 §B17 的十格，加承载授权窗口的第十一格。

    十二个字段：计划那十格里的第一格是**两个**（``workspace`` / ``workstream``），
    另加 ``window``。见模块 docstring 里逐格的对齐表与第十一格的来历。

    ## 它是不可变的，而且被摘要盖住

    受理之后这一份不再改。要换目标、换信封、换截止，是**另一个 Run**：同一个
    幂等键配另一份规格会撞在 :class:`~marketops.domain.errors.IdempotencyConflictError`
    上（由 :func:`spec_fingerprint` 判），而不是把这一份改掉。「这一轮当时是按什么
    跑起来的」因此永远读得回来 —— 这正是 ``WorkItemSnapshot`` 那条纪律在 Run 层的
    同一条。

    ## 两处刻意不做的校验

    * **不比较 ``cutoff`` 与当下。** 「一个从出生就过期的 Run」是受理面要拒的
      （``application/run_admission.py``），而这里不读时钟 —— 模型里出现
      ``utc_now()`` 会让同一份规格在两个时刻构造出两个不同的对象，于是
      「重发同一个请求」这件事在类型层就失去了判据。
    * **不检查引用指得到东西。** ``context_ref`` / ``plan_ref`` 是**内容摘要**，
      不是路径。它们答的是「这一次读的是哪一份」，能不能读回来由持有那份东西的
      那一层回答。
    """

    #: 这个 Run 属于哪个工作区。来源是 ``SceneVersion.workspace``，不是请求参数。
    #: **它只答「哪个工作区」** —— 计划那一格的后半截见 :attr:`workstream`。
    workspace: str
    #: 这一次启动之下的工作流。``None`` = **这一轮不在任何一次启动之下**（今天
    #: 所有既有生产者都是这个值：它们各自从别处被提交上来）。
    #:
    #: 由 ``start_workstream`` 那一条路径写下（经 ``spec_of`` 的 ``workstream``
    #: 参数），所以「哪一条路能造出一个属于某个工作流的 Run」在类型上只有一个答案。
    #: 取值是 ``Workstream.workstream_id``（``<scene_id>#<ordinal>``），**不是**一个
    #: 新造的名字 —— 它必须能按业务键读回那一行工作流。
    #:
    #: 缺省 ``None`` 而不是空串：``""`` 在盘上读起来像「有一个工作流，它的名字是
    #: 空」，而这两件事的处置相反（一个去查接线，一个是正常结果）。与 ``window``
    #: 同款 —— 那一格的「没有自动授权」也是 ``None`` 而不是一个空窗口。
    workstream: str | None = None
    #: 这一轮要弄明白什么。散文，非空。
    objective: str
    #: 对象范围。**今天承载的是 ``scene_id``** —— 见模块 docstring 的第二条差量。
    selector: str
    #: ``SceneFingerprint.digest`` —— 这一次读的是哪一份上下文。
    context_ref: str
    #: ``EffectiveResearchPlan.digest`` —— 这一次按哪一份计划跑。
    plan_ref: str
    #: 谁授权跑起来的。见 ``ExecutionMode``。
    execution_mode: ExecutionMode
    #: 这一次自动授权链的范围。**``manual`` / ``watch`` 必须为 ``None``** ——
    #: 它与 ``execution_mode`` 是一对，见下面的跨格校验器。**没有默认值**：
    #: 每一处构造都要说出它的选择，而不是继承一个「这次大概是手动」。
    #:
    #: **域层不要求 ``bounded_auto`` 必须带一个窗口**，尽管那样的一轮听起来缺了
    #: 边界。理由写在 :meth:`_window_agrees_with_the_rest` 里：那条判据是**受理**
    #: 的事，而它的唯一出处是 ``ports/runs.py::admission_refusal``。
    window: AuthorizationWindow | None
    #: 这一次的信封。**不得为空** —— 没有上限不是「随便用」，见校验器。
    budget: tuple[RunBudgetLine, ...]
    #: 这一次跑到什么时候为止。**全仓唯一的运行截止**，见模块 docstring。
    cutoff: datetime
    #: 这一次读到的策略版本，由 :func:`policy_versions_of` 生产。
    policy_versions: tuple[str, ...]
    #: 由调用方给。服务端不生成 —— 见 :func:`run_id`。
    idempotency_key: str

    @field_validator("workspace", "selector")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("workstream")
    @classmethod
    def _workstream_is_a_business_key(cls, value: str | None) -> str | None:
        """给了就必须是**读得回来的**那个业务键 —— 判据在它的生产者那一份里。

        ``None`` 原样放过：那是「这一轮不在任何一次启动之下」，而它不需要形状。
        空串不放过 —— 它在这一格上只有一个含义（「有一个名字是空的工作流」），而
        那个东西造不出来。判据借 ``domain/workstream.py::check_workstream_id``，
        不在这里再写一遍 ``rsplit``：形状有两个出处的那天，造得出来与读得回来会
        分家。
        """
        if value is None:
            return None
        if not value.strip():
            raise ValueError(
                "workstream 是空串。\n"
                "  这一格的两个取值是「一个业务键」与「``None``（这一轮不在任何一次"
                "启动之下）」。空串读起来像「有一个工作流，它的名字是空」—— 而那与"
                "「没接上」是两件事，处置相反。"
            )
        return check_workstream_id(value)

    @field_validator("objective")
    @classmethod
    def _objective_is_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "objective 不能为空。它答的是「这一轮要弄明白什么」—— 空着的时候，"
                "这个 Run 在册子上只剩一个 id 和一串预算，而下游每一处要判「这批"
                "对象跟这次目标相关吗」的地方都只能猜。"
            )
        return value

    @field_validator("idempotency_key")
    @classmethod
    def _key_identifies_this_submission(cls, value: str) -> str:
        """空键与带空白的键都不是键 —— 而两者的失败都是**看起来正常**的那种。

        空键是最贵的一格：``run_id`` 是 ``(workspace, 键)`` 的内容摘要，所以空键
        是一个**恒定**的 id —— 于是每一次不带键的提交都会拿回**第一次那一行**，而
        调用方以为自己又开了一轮。§B17 条件 1 后半句要的「不重复池子或成本」正是
        靠「键存在」成立的。

        首尾空白同理，但更隐晦：同一个键多一个空格就是**另一个键**，于是重发被
        受理成第二轮。这两条在这里只判，不**改写**（``strip()`` 一下收下它）——
        改写会让两个不同的输入指向同一行，而「报的是哪个键」与「用的是哪个键」
        从此不是同一个字符串。
        """
        if not value.strip():
            raise ValueError(
                "idempotency_key 不得为空。\n"
                "``run_id`` 是 ``(workspace, 键)`` 的内容摘要，所以空键是一个恒定的 "
                "id —— 每一次不带键的提交都会拿回**第一次那一行**，而调用方以为自己"
                "又开了一轮。键由调用方给、服务端不生成（见 run_id）：服务端一旦能"
                "自己造键，它就能造出第二个 Run，而「自行扩下一批」正是从那里开始的。"
            )
        if value != value.strip():
            raise ValueError(
                f"idempotency_key 首尾带了空白：{value!r}。\n"
                "同一个键多一个空格就是**另一个键**，于是重发被受理成第二轮 —— 而它"
                "在报告上看起来完全正常。"
            )
        return value

    @field_validator("context_ref", "plan_ref")
    @classmethod
    def _ref_is_a_digest(cls, value: str, info) -> str:
        digest = value.strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(
                f"{info.field_name} 不是 64 位十六进制摘要：{value!r}。\n"
                "它指的是「这一次读的是哪一份上下文 / 哪一份计划」—— 一个形状不对的"
                "值在那里等于没有指称对象，而在报告上它会看起来像有一个。"
            )
        return digest

    @field_validator("cutoff")
    @classmethod
    def _cutoff_is_timezone_aware(cls, value: datetime) -> datetime:
        return to_utc(value)

    @field_validator("policy_versions")
    @classmethod
    def _versions_are_present(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                "policy_versions 不得为空。一段 Run 不可能按零份策略跑起来 —— "
                "空元组读起来是「没有策略变更」，而它真实的含义只会是「没人取过」。"
                "生产者是 policy_versions_of。"
            )
        if any(not version.strip() for version in value):
            raise ValueError(
                "policy_versions 里不得有空元素。空版本与任何真版本都不相等，于是"
                "「两次 Run 读的是不是同一份策略」会因为没人填而恒答「不是」—— "
                "而恒响的告警与恒不响的告警是同一类东西：没人再看它。"
            )
        return value

    @field_validator("budget")
    @classmethod
    def _budget_is_an_envelope(cls, value: tuple[RunBudgetLine, ...]) -> tuple[RunBudgetLine, ...]:
        if not value:
            raise ValueError(
                "budget 不得为空。**没有上限不是「随便用」**：§B17 条件 4 要求"
                "「达累计预算时 stop」，而一条线都没有的 Run 在那个阈值上永远不触发 —— "
                "它在报告里长得像「预算充足」，实际是「没人设过」。"
            )
        lines = [line.line for line in value]
        repeated = sorted({line.value for line in lines if lines.count(line) > 1})
        if repeated:
            raise ValueError(
                f"同一个科目在信封里出现了多次：{repeated}。\n"
                "两条上限谁说了算没有答案 —— 取小的会把另一条读成「够用」，取大的会"
                "把另一条读成「还没到」。这与 SceneBudgetLine 是同一条纪律：分不开的"
                "两个数不要并排放。"
            )
        return value

    @model_validator(mode="after")
    def _window_agrees_with_the_rest(self) -> RunSpec:
        """``window`` 与 ``execution_mode`` / ``cutoff`` / ``budget`` 的一致性。

        **每一条都只能在跨格的地方判** —— 要的都是两个字段同时在场，而
        ``field_validator`` 只看得到一个。这与 ``RunSpec`` 上那些单格校验器分工
        清楚：单格的失败是「这一格的值不对」，这里的失败是「这两格说的不是一件事」。

        分成四条各自 raise，不是合成一条「规格不合法」：四条的**处置不同** ——
        模式配错了要改模式或改窗口（一次授权范围的改变），截止超窗口要挪窗口，
        信封缺科目要补窗口（授权范围），信封超窗口要改信封（参数调整）。合成一条
        会让四种处置在日志里同形。

        ## 这里**不判**「``bounded_auto`` 必须带一个窗口」

        那条判据今天住在 ``ports/runs.py::admission_refusal``，不在这里，理由不是
        「域层懒得判」，而是**同一句话不能有两个出处**：

        * 它答的是「**这次受理**走不走得通」，而那句话点名的东西**换过两次**：e2c
          之前没有一处写过窗口，于是每一次 ``bounded_auto`` 的受理都撞在它上面
          （拒词点的是**没有生产者**）；e2c 之后窗口有了生产者（``submit_run`` 的
          第七个参数），它答的变成「**这一份请求**没有写下窗口」。两个时期都是同一个
          函数在答，而域层若是也判，**先触发的那一处**就是用户看到的那一句：域层先炸，
          端口那句就永远到不了任何人眼前，于是报告上是「请求拼不成规格」而不是「这个
          模式这次受理不了」，而两者的处置不同（前者改参数，后者改这一次的请求）。
        * 域层**判不了它**。一份自动规格该不该带窗口，取决于**这一次提交有没有写下
          它** —— 域模型读不到部署，也读不到时钟（见模块 docstring 那条
          「不比较 ``cutoff`` 与当下」）。它能判的是**内部一致**：带了的那个窗口，
          与模式、截止、信封说不说得拢。那正是下面四条。

        代价要说清：``RunSpec`` 因此收得下一份没有窗口的 ``bounded_auto``。这在盘上
        造不出问题 —— 落一行 Run 的唯一入口是 ``RunStore.admit``，而它先问
        ``admission_refusal``（不带窗口的自动模式在那里被拒）。另外两份带窗口的自动
        规格今天**走得通**（``bounded_auto`` 带窗口；``watch`` 不带窗口），各有各的
        前提，而两份都**不是**「域层没判所以漏了」：它们各自要的那一样前提 —— 那条链
        的累计读数、这一批主体上的一条到期格子 —— 域模型都读不到（见上面第二个
        bullet 那条「域层判不了它」）。
        """
        if self.window is not None and self.execution_mode is not ExecutionMode.BOUNDED_AUTO:
            raise ValueError(
                f"execution_mode 是 {self.execution_mode.value!r}，而这一份规格"
                "带了授权窗口。\n"
                "窗口是「这一轮之后可以自己接下一轮」的授权，而 manual 逐字是"
                "「有人明确提交了这一次」—— 它带一个窗口，等于在记录里写下「这条链"
                "可以自己走」，而没有任何一处读过那个承诺（谁在什么时候接下一轮，"
                "manual 没有上游）。\n"
                "watch 的来源是到期或信号，不是窗口：它的三个阈值该由 due 判定回答"
                "（``application/run_admission.py`` 的 ``_require_due`` —— "
                "受理一次 watch 的前提正是那份判定），而不是由一次受理写下来。"
            )

        window = self.window
        if window is None:
            return self

        if window.not_after < self.cutoff:
            raise ValueError(
                f"这一轮的截止（{self.cutoff.isoformat()}）超出了它的授权窗口"
                f"（{window.not_after.isoformat()}）。\n"
                "窗口是这条链最远走到什么时候，而这一轮是链上的一轮 —— 一个截止晚于"
                "窗口的 Run，会在授权已经过期之后还在跑。那时「达到截止时间就停」"
                "这条判据已经成立（窗口到期），而这一轮自己还没到，于是它成了这条链"
                "上唯一一个没人管得住的后继者。"
            )

        envelope = {line.line: line.ceiling for line in self.budget}
        ceilings = {line.line: line.ceiling for line in window.budget}
        uncovered = sorted(line.value for line in envelope if line not in ceilings)
        if uncovered:
            raise ValueError(
                f"这一轮的信封里有窗口没有的科目：{uncovered}。\n"
                "窗口是这条链**累计**的上限，而「累计」对一个窗口没提到的科目无处"
                "可算 —— 那条线在链上没有上限，于是「达到累计预算就停」这句话对它"
                "永远不成立，而报告上这与「那条线还没花到」同形。\n"
                "处置是把窗口补全（那是一次**授权范围**的改变），而不是把这一轮的"
                "信封减一条：减掉的那条线上要做的事并没有变得不需要授权。"
            )
        over = sorted(
            (
                f"{line.value}（本轮 {envelope[line]}、窗口 {ceilings[line]}）"
                for line in envelope
                if envelope[line] > ceilings[line]
            )
        )
        if over:
            raise ValueError(
                f"这一轮的信封超过了它的授权窗口：{over}。\n"
                "窗口是这条链累计的上限，而这一轮是链上的一轮 —— 一条高于窗口的本轮"
                "上限，让这一轮自己就能把整条链花光。那时「累计预算达到就停」要等"
                "这轮跑完才成立，而它跑完时窗口已经超了。\n"
                "处置是把这一轮的信封调小（一次参数调整），这与「窗口里缺了这条线」"
                "不是同一件事。"
            )
        return self


def spec_fingerprint(spec: RunSpec) -> str:
    """一次受理请求的**内容**散列 —— ``RunStore.admit(spec_sha256=...)`` 的唯一生产者。

    它答的是「这两次提交是不是同一次」。幂等键答的是另一件事（「这是不是同一次
    意图」），两者必须分开 —— 与 ``domain/job.py::request_fingerprint`` 是同一条
    纪律、同一个理由：同一个键配不同的内容是一次冲突，而「内容变了没有」只能由这个
    值回答。两处各算一份，漂移的那天冲突检查开始漏报，而漏报的形态是「同一个键
    受理了两次」：报告上只显示研究做了两遍，没有任何一处会红。

    **幂等键不进散列。** 它不在十一格的「内容」那一半里：同一个键配同一个内容是重发，
    配另一个内容是冲突；如果键本身进了散列，这两件事都会算出一个新摘要 —— 于是
    冲突检查会变成恒不触发。

    **授权窗口进散列**（用的是它自己的 :attr:`AuthorizationWindow.digest`）。同一份
    规格配两个窗口是**两次不同的提交** —— 一个窗口是「这条链最多走 40 个对象」，另一
    个是 400，它们不是同一次授权，重发同一个键必须撞成冲突而不是静默拿回第一次那一行。
    缺窗口时编成 ``"none"`` 而不是空串：一个**说出来**的哨兵，读的人不必去猜
    「这里是没写还是没散出来」。

    摘要的算法与 ``SceneFingerprint.digest`` 同形：逐格先散一次，再把**定长**的
    每格摘要整体散一次。直接拼成一长串是有歧义的：``objective="a b"`` 与
    ``objective="a"`` + ``selector="b"`` 会拼成同一串。

    **工作流进散列**，理由与授权窗口逐字同款：同一个键配两个工作流是**两次不同的
    提交** —— 一个说「这是第 1 次启动之下的那一轮」，另一个说「第 2 次之下的」，
    静默拿回第一次那一行会让第二个工作流以为自己有了一轮 Run。缺工作流时编成
    ``"none"``（与窗口同一个哨兵）：一个**说出来**的「不在任何启动之下」。

    这一格是**新加的**，所以它加进 ``blocks`` 之后，``workstream=None`` 的那些既有
    规格算出来的摘要也变了。方向是对的：一份在加这一格**之前**落盘的规格重发时会
    撞成冲突（一句看得见的拒绝），而不是被静默判成同一个。反方向的代价更大 ——
    摘要不变意味着「同一份内容」这句话在升级前后指的是两套格子。
    """
    blocks = [
        sha256_text(spec.workspace),
        sha256_text(spec.workstream if spec.workstream is not None else "none"),
        sha256_text(spec.objective),
        sha256_text(spec.selector),
        sha256_text(spec.context_ref),
        sha256_text(spec.plan_ref),
        sha256_text(spec.execution_mode.value),
        sha256_text(spec.window.digest if spec.window is not None else "none"),
        sha256_text(SEP.join(f"{line.line.value}{SEP}{line.ceiling}" for line in spec.budget)),
        sha256_text(spec.cutoff.isoformat()),
        sha256_text(SEP.join(spec.policy_versions)),
    ]
    return sha256_text(SEP.join(blocks))


@dataclass(frozen=True)
class Run:
    """一次被受理的增量。**没有「进行中」状态**，见模块 docstring。

    ``spec`` 整份随行读取（不是只留一个摘要）：受理之后要回答「这一轮当时是按什么
    跑起来的」，而一个只存摘要的记录答不了 —— 摘要证明「是同一份」，不回答「是哪
    一份」。存储层把 ``spec`` 原样收下、原样吐回（``spec.model_dump_json()``），
    所以读回来的那一份与当时那一份逐字节相同。

    ``spec_sha256`` 与 ``spec`` 都留着，不是冗余：前者是**冲突判据**（同键重提交时
    比对的唯一依据），后者是**内容**。只留内容会让每次重提交都重新散一次，而散列
    函数一旦被改，历史行的判据就全部作废 —— 判据必须是一个落盘的、不再重算的值。

    ``delta`` / ``stopped_at`` 与 ``stop_reason`` **三者同时有或同时无**，由
    ``__post_init__`` 钉住（存储层另有一条等价的 ``CHECK``：那条管住别的进程，
    这条管住本进程里的构造点）。为什么不让 ``delta`` 单独先落：一个「已经结束、
    但产出还没写」的窗口里，读的人无法区分「它什么都没产出」与「产出丢了」——
    而这两件事的处置相反。所以结束是**一次**写（``RunStore.stop``）。
    """

    run_id: str
    spec: RunSpec
    spec_sha256: str
    created_at: str
    updated_at: str
    #: ``None`` ⟺ 还在跑。**必填**（不设缺省），因为受理那一刻必须显式写下
    #: 「这一轮还没有结束」—— 一个靠缺省值的 ``None`` 会让构造点看起来像忘了填。
    stop_reason: RunStatus | None
    #: 这一轮把每一件碰过的东西处置成了什么。见 ``domain/run_delta.py``。
    delta: RunDelta | None
    #: 结束时刻（UTC ISO 8601）。与 ``created_at`` 同一个格式。
    stopped_at: str | None

    def __post_init__(self) -> None:
        paired = (self.stop_reason is None, self.delta is None, self.stopped_at is None)
        if len(set(paired)) != 1:
            has_delta = "一份 Delta" if self.delta is not None else "None"
            raise ValueError(
                "Run 的结局、Delta 与结束时刻必须三者同时有或同时无，"
                f"现在是 stop_reason={self.stop_reason!r}、"
                f"delta={has_delta}、stopped_at={self.stopped_at!r}。\n"
                "一个「已经结束、但产出还没写」的窗口里，读的人无法区分「它什么都没"
                "产出」与「产出丢了」—— 而这两件事的处置相反（前者没事发生，后者要去"
                "追那一轮到底写了什么）。"
            )

    @property
    def is_stopped(self) -> bool:
        """停下来的一轮不再受理新的事实 —— 结局已经写下了，续跑会覆盖它。"""
        return self.stop_reason is not None


__all__ = [
    "Run",
    "RunBudgetLine",
    "RunSpec",
    "policy_versions_of",
    "run_id",
    "spec_fingerprint",
]

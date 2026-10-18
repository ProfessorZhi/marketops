"""**ActionItem** —— 跟进池里的一件工作，以及它**只能**被约束拦住的那条唯一性。

§5.4 逐字：

```text
跟进池由 ActionItem 构成：
action_id / workspace / scope_ref (Account/Route/Review/未来Campaign等) /
action_type / owner / reason/evidence refs / due_at? / state / blocked_by? /
expected_version / created_at / resolved_at
```

以及 §6 那两行：「Action | open / claimed / waiting / done / dismissed |
ActionItem」—— 状态的五个成员就是它，不多不少。

## 它与 ``ReviewCase`` **不是**同一件事（虽然挨着）

``domain/review.py`` 的 :class:`~marketops.domain.review.ReviewCase` 也住在「一件等人
做的事」这个位置，所以这里必须说清差别，否则下一个读的人会先问「为什么不是复用」：

* **ReviewCase 是一件要人做裁决的问题**：它带 ``options`` / ``recommendation``
  （``INTAKE`` 给的那份菜单）、``queue``、``reason`` 是一个
  :class:`~marketops.domain.review.ReviewReason`，而它的终态是一份
  :class:`~marketops.domain.review.HumanResolution`（``CORRECT_OBJECT`` / ``DISMISS``
  / ``REOPEN`` / ``PROPOSE_POLICY_CHANGE``）—— 它问的是「这一条该怎么判」。
* **ActionItem 是一件要人做掉的工作**：它带 ``action_type``（核实转介联系人 / 补查
  responsibility / 审核某条冲突 / 批准草稿，§5.4 的四个例子逐字）、``owner``、
  ``due_at``、``blocked_by``，而它的状态里有 ``claimed`` 与 ``waiting`` —— 一件事
  **被谁在做**、**在等什么**，这两个问题在 ReviewCase 上没有格子。

§5.4 末段把这条边写出来了：「ActionItem 可以由研究结果/Watch/人工裁决产生」——
人工裁决（一份 HumanResolution）**产生** ActionItem，那是两件事之间的一条边，
不是一个东西的两个名字。

## 唯一性：**插入失败**，不是一次查询的结论

``db.py`` 里那条 partial unique index 的注释逐字：

    ``action_item`` 的 **partial unique index**（只盖未结状态）—— 「同一主体上的同一类
    待办只应有一条当前项」。没有它，每个 Run 都会新增一条；有了它，重复创建是插入失败。
    索引带 ``WHERE state IN ('open','claimed','waiting')`` 是因为**已结的可以有任意多条**：
    同一个账号上「核实转介联系人」做过三次就是三条历史，那不是重复。

这个模块给出这条 ``WHERE`` 的**领域侧那一半**： :data:`OPEN_STATES` /
:data:`TERMINAL_STATES`，以及 :func:`scope_ref` 那个扁平列的编码。三个东西必须在
同一个集合上，而它们分家的那天会红在 ``tests/test_action_item_open_states.py``：
那条用例从 ``adapters/sqlite/db.py`` 的 DDL 文本里把 ``WHERE`` 里那几个字面量读出来，
与本模块的 :data:`OPEN_STATES` 逐字比 —— 因为那条索引住在 MAIN 独占的文件里，
而「两处说的是同一件事」不能靠一句注释保证。

**为什么判据必须是插入失败而不是「先查后插」**：先查后插在两个写者之间有一个窗口 ——
两边都查到「没有未结的」，两边都插，而约束只能拦住其中一个，于是第二个写者拿到的是
一次**主键/约束异常**（在服务面上读起来像故障），而它本来该拿到的是「已经有一条了，
它的 id 是这个」。判据落在约束上时，那个窗口不存在。

## 版本：**就地更新**并且把 ``revision`` 加一

``action_item`` 的主键是 ``(action_id, revision)``，而那条索引盖的是 ``state``。两件
事合起来只留下一条路：处置**就地更新那一行**并把 ``revision`` 加一 ——
因为一条**追加**的 ``done`` 版本会与仍然留在盘上的那条 ``open`` 版本（它是**另**一行）
一起躺在索引里，于是这一件在索引那侧**永远是未结的**，同一主体上的下一件就再也建不出来。

这不是本模块的选择，是那两条约束推出来的唯一解。它的代价如实记在这里：**处置的历史
只有一行**（``resolved_at`` / ``resolution`` / ``resolution_by`` 是当前值），要看
「这件被处置过几次」得读事件的流水，而不是这张表。要把它做成只增不改的历史，需要
``(action_id, revision)`` 上的**全表**唯一索引改成「只盖当前版」，那是 DDL 的事。
"""

from __future__ import annotations

import secrets
from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator

from marketops.domain.base import StrictModel
from marketops.domain.errors import ActionItemConflictError
from marketops.domain.util import (
    check_canonical_id,
    check_slug_safe,
    sha256_text,
    to_utc,
    utc_now,
)

__all__ = [
    "ACTION_ID_PREFIX",
    "OPEN_STATES",
    "TERMINAL_STATES",
    "ActionItem",
    "ActionItemState",
    "ActionType",
    "ScopeKind",
    "new_action_id",
    "require_creatable",
    "scope_ref",
    "split_scope_ref",
]


class ScopeKind(str, Enum):
    """``scope_ref`` 的主语是哪一类对象。§5.4 逐字：「Account/Route/Review/未来Campaign等」。

    **它不是 ``domain.saved_view.SubjectKind``**，两者刻意各是一个闭集：

    * ``SubjectKind`` 答的是「这个池子读得出来哪一类主体」—— 今天只有一个成员
      （账号池是唯一存在的那个投影）。
    * 这个答的是「一件工作可以挂在什么上」—— 四个成员，与池子读不读得出来无关
      （一条路线可以是一件待办的主语，而没有一个端口读得出一整条路线）。

    合成一个枚举的那天，``compile_view_query`` 会开始接受 ``subject_kind: "route"``，
    而那条查询在池子那一侧一个主体都筛不出来 —— 报出来的是「没有结果」，
    而真相是「这一类主体还没有读者」。
    """

    ACCOUNT = "account"
    ROUTE = "route"
    REVIEW = "review"
    CAMPAIGN = "campaign"


#: ``scope_ref`` 里分开「哪一类」与「哪一个」的那个字符。
#:
#: **不是** :data:`~marketops.domain.util.SEP`（内容寻址 id 用的控制字符），虽然那里
#: 的论证（「分段本身可能含分隔符，于是拼接有歧义」）在这里也成立。区别在于这一格
#: **是一个扁平列、进了一条唯一索引、要出现在报告和 sqlite 的命令行回显里**：
#: 一个控制字符在 ``sqlite3`` 的输出里是不可见的，于是「两条不同的 scope_ref」在
#: 屏幕上长得一模一样，而排查同名冲突的人正要读它。
#:
#: 左段是一个闭集枚举（:class:`ScopeKind`），它的成员里不可能含 ``#``，所以
#: 「只按第一个 ``#`` 切」是无歧义的 —— 这一条性质是「不转义也安全」的依据。
#: 右段过 :func:`~marketops.domain.util.check_slug_safe`（不许含路径分隔符与保留字符）。
SCOPE_SEP = "#"


def scope_ref(kind: ScopeKind, ref_id: str) -> str:
    """把「哪一类 + 哪一个」编成那个扁平列的值。**唯一的编码入口**。

    两处各拼一遍的那天，``(account, a#b)`` 与 ``(account#a, b)`` 会拼成同一个字符串，
    而它进的是那条唯一索引 —— 两件不同的工作静默地互相挡住。
    """
    check_slug_safe(ref_id, "scope_ref 的 ref_id")
    if SCOPE_SEP in ref_id:
        raise ValueError(
            f"scope_ref 的 ref_id 不得含 {SCOPE_SEP!r}（收到 {ref_id!r}）："
            "它是「哪一类」与「哪一个」的分界，含它就切不回来了。"
        )
    return f"{kind.value}{SCOPE_SEP}{ref_id}"


def split_scope_ref(value: str) -> tuple[ScopeKind, str]:
    """把那个扁平列读回来。读不出来时抛 —— **不猜**。

    一个「认不出就当成 account」的实现会让一条挂在路线上的待办与一条挂在账号上的
    待办共用一个命名空间，而报告上只显示「这一类的工作挺多的」。
    """
    kind_raw, separator, ref_id = value.partition(SCOPE_SEP)
    if not separator or not ref_id:
        raise ValueError(
            f"scope_ref 必须是 '<kind>{SCOPE_SEP}<ref_id>' 的形状（收到 {value!r}）。"
        )
    try:
        kind = ScopeKind(kind_raw)
    except ValueError:
        raise ValueError(
            f"scope_ref 的主语类别 {kind_raw!r} 不认识"
            f"（认识的：{[item.value for item in ScopeKind]}）。"
        ) from None
    check_slug_safe(ref_id, "scope_ref 的 ref_id")
    return kind, ref_id


#: ``action_id`` 的前缀。与 ``route-`` / ``coh-`` / ``case-`` 同一种命名：报告与
#: ``sqlite3`` 的命令行回显里，一个 id 的第一段就该说出它是哪一类东西。
ACTION_ID_PREFIX = "act"


def new_action_id() -> str:
    """开一件**新**待办用的 ``action_id``：``act-<16 位十六进制>``。

    它是**随机**的，不是内容寻址的 —— 这不是风格，是两条约束推出来的唯一解：

    * :meth:`ActionItem.opened` 的 docstring 逐字：「同一个主体上的同一类工作做过三次
      就是三条历史，内容寻址的函数会把这三次算成一个 id，于是第二条历史写不出来」。
      一个由 ``(主体, 类型)`` 算出来的 id，会在第一次做完之后**物理上**写不出第二件。
    * 唯一性的判据必须落在 ``(workspace, action_type, scope_ref)`` 那条 partial unique
      index 上（``ports/views.py::ActionItemStore.create`` 逐字）。内容寻址的 id 会让
      重放**先撞主键**（那一个 ``action_id`` 已经在盘上），而那条路返回的是「重发」——
      于是那条索引一次都不会响，「第二条未结的」在盘上再也测不出来。

    随机的代价如实记在这里：同一个主体上连开两次会得到**两个** id，第二个被索引拒掉
    （``OpenActionItemExistsError``）。那正是要的读数 —— 被拒的是「第二条未结的」，
    而不是「同一个 id 又写了一次」；两句话的处置不同（去处理那一条 / 去查那个 id）。
    """
    return f"{ACTION_ID_PREFIX}-{secrets.token_hex(8)}"


class ActionType(str, Enum):
    """这件工作**是做什么**。四个成员逐字取自 §5.4 的例子。

    「核实转介联系人」、「补查 responsibility」、「审核某条冲突」、「批准草稿」——
    这四个不是「其它」的一个兜底集合，它们各自的完成条件不同（核对一个联系方式 /
    补一格事实 / 做一个裁决 / 放行一份草稿），所以它们不能合成一个 ``TODO``。
    要加第五种工作，正确的动作是改这个枚举与它的完成条件，而不是让调用方塞一个
    自由字符串进来 —— 一个自由字符串会在跟进池里长出一类**没有完成条件**的工作，
    而它永远留在池子里，报告上显示「积压」。
    """

    VERIFY_REFERRAL_CONTACT = "verify_referral_contact"
    FILL_RESPONSIBILITY = "fill_responsibility"
    REVIEW_CONFLICT = "review_conflict"
    APPROVE_DRAFT = "approve_draft"


class ActionItemState(str, Enum):
    """§6 逐字那一行：「open / claimed / waiting / done / dismissed」。"""

    OPEN = "open"
    CLAIMED = "claimed"
    WAITING = "waiting"
    DONE = "done"
    DISMISSED = "dismissed"


#: **未结**的那几个状态。与 ``db.py`` 里那条 partial unique index 的
#: ``WHERE state IN ('open', 'claimed', 'waiting')`` 是**同一件事的两种写法**，
#: 而这一点由 ``tests/test_action_item_open_states.py`` 从 DDL 文本里读出来比。
OPEN_STATES: tuple[ActionItemState, ...] = (
    ActionItemState.OPEN,
    ActionItemState.CLAIMED,
    ActionItemState.WAITING,
)

#: **已结**的那几个。"已结的可以有任意多条（那是历史，不是重复）"这句话的主语。
TERMINAL_STATES: tuple[ActionItemState, ...] = (
    ActionItemState.DONE,
    ActionItemState.DISMISSED,
)


def require_creatable(item: "ActionItem") -> None:
    """创建的前置条件。**程序员的错**，所以抛 ``ValueError`` 而不是业务异常。

    三条都要：``revision == 1``（新的一件从第一版开始）、``expected_version == 0``
    （创建没有可基于的那一版 —— 见 :attr:`ActionItem.expected_version`）、
    ``state`` 是 ``OPEN``（创建一件**已经做完了**的工作不是创建，是补一条历史，
    而补历史会让 ``resolved_at`` 与 ``created_at`` 说两件不同的事）。
    """
    if item.revision != 1:
        raise ValueError(f"新的一件待办必须是 revision 1（收到 {item.revision}）")
    if item.expected_version != 0:
        raise ValueError(
            "新的一件待办 expected_version 必须是 0 —— 创建时没有任何一版可基于。"
            f"（收到 {item.expected_version}）"
        )
    if item.state is not ActionItemState.OPEN:
        raise ValueError(
            f"新的一件待办必须是 open（收到 {item.state.value}）：创建一件已经做完了的"
            "工作不是创建，是补一条历史 —— 而那条历史会带着一个它从没经历过的"
            "resolved_at。"
        )


class ActionItem(StrictModel):
    """跟进池里的一件工作。

    投影在 ``adapters/mcp_server/followups.py`` 的 ``list_action_items`` 那一侧：
    它是**当前状态**的读法（每一件只有一行），不是流水。
    """

    action_id: str
    revision: int = Field(ge=1)
    workspace: str
    #: 扁平列，进那条唯一索引。编码只有 :func:`scope_ref` 一个入口。
    scope_ref: str
    action_type: ActionType
    #: 谁来做。``None`` = 还没人认领（一件事刚进池子时的样子，不是「谁都不做」）。
    owner: str | None = None
    state: ActionItemState = ActionItemState.OPEN
    #: 什么时候该做完。``None`` = 没有期限（不是「今天就到期」）。
    due_at: datetime | None = None
    #: 在等什么。非空时它解释 ``state == WAITING``；而 ``WAITING`` 配空 ``blocked_by``
    #: 是一件**在等但不说等什么**的工作（见 :meth:`ActionItem.disposition_digest` 之后
    #: 那条模型级判据）。
    blocked_by: str = ""
    #: 为什么会有这一件。**必填**：一件说不出理由的工作，读的人只能去猜它是从哪一次
    #: 判定来的，而他猜错的代价是去处理一件本来不该做的事。
    reason: str
    #: 支撑这一件的证据（指针，不是正文）。与 ``Evidence.evidence_id`` 同空间。
    evidence_refs: tuple[str, ...] = ()
    #: 做完之后记下的结果。**终态必填**（§5.4「完成后记录结果」）。
    resolution: str = ""
    #: 做这件事的人（可信 principal，不是参数）。终态时必填。
    resolution_by: str = ""
    #: 这一行**基于**哪一版写下来的（``HumanResolution.expected_version`` 逐字那一条
    #: 纪律：「提交上来时如果盘上已经不是它了，这一件被拒」）。
    #:
    #: **创建时是 0**，因为创建没有可基于的那一版 —— 这是它比 ``HumanResolution``
    #: 那个 ``ge=1`` 宽的唯一理由，而它不是一处放水：处置那一侧仍然要求 ``>= 1``
    #: （见 :meth:`resolve` 的 ``expected_version``），因为处置总是基于某一版。
    expected_version: int = Field(ge=0)
    created_at: datetime
    resolved_at: datetime | None = None
    updated_at: datetime
    note: str = ""

    @field_validator("action_id")
    @classmethod
    def _action_id(cls, value: str) -> str:
        return check_canonical_id(value, "action_id")

    @field_validator("workspace")
    @classmethod
    def _workspace(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("scope_ref")
    @classmethod
    def _scope_ref(cls, value: str) -> str:
        split_scope_ref(value)  # 形状判据只有那一处
        return value

    @field_validator("owner")
    @classmethod
    def _owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip():
            raise ValueError("owner 不能是空字符串（没人认领时它应当是 None）")
        return value

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "reason 不能为空：一件说不出理由的工作，读的人只能去猜它是从哪一次"
                "判定来的，而他猜错的代价是去处理一件本来不该做的事。"
            )
        return value

    @field_validator("evidence_refs")
    @classmethod
    def _refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for ref in value:
            check_slug_safe(ref, "evidence_refs[]")
        return value

    @field_validator("created_at", "updated_at", "resolved_at", "due_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else to_utc(value)

    def model_post_init(self, _context: object) -> None:
        """两格之间的关系，两个字段各自的校验器都看不到对方。

        1. 终态必须带结果（§5.4「完成后记录结果并更新投影」）。一件 ``done`` 而
           ``resolution`` 为空的工作，在报告里与一件「被谁顺手关掉了」的工作同形。
        2. ``WAITING`` 必须说得清在等什么。``blocked_by`` 为空时那一格是
           「在等，但不说等什么」—— 它比 ``OPEN`` 更糟：``OPEN`` 至少还看得出没人
           在做，而它看起来像有人正在推进。
        """
        if self.state in TERMINAL_STATES:
            if not self.resolution.strip():
                raise ValueError(
                    f"state={self.state.value} 的工作必须带 resolution（§5.4 逐字"
                    "「完成后记录结果」）：一件做完了却没说做了什么的工作，与一件"
                    "被人顺手关掉的工作在报告上同形。"
                )
            if not self.resolution_by.strip():
                raise ValueError(
                    f"state={self.state.value} 的工作必须带 resolution_by："
                    "§12 逐字要求所有决定都记得住是谁做的。"
                )
        elif self.resolution or self.resolution_by or self.resolved_at is not None:
            raise ValueError(
                f"state={self.state.value} 是未结的，却带着 resolution / resolution_by /"
                " resolved_at。这三格说的是「已经做完了」—— 一个未结的工作带着它们，"
                "会在报告里显示成「做完了但还开着」。"
            )
        if self.state is ActionItemState.WAITING and not self.blocked_by.strip():
            raise ValueError(
                "state=waiting 的工作必须说清在等什么（blocked_by）："
                "它比 open 更糟 —— open 至少还看得出没人做，而一个不说什么的 waiting"
                "看起来像有人正在推进。"
            )

    # ------------------------------------------------------------------
    # 读法
    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        """未结 = 它占着那条唯一索引。**判据只有一个来源**（:data:`OPEN_STATES`）。"""
        return self.state in OPEN_STATES

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def subject_kind(self) -> ScopeKind:
        return split_scope_ref(self.scope_ref)[0]

    @property
    def subject_id(self) -> str:
        return split_scope_ref(self.scope_ref)[1]

    @property
    def disposition_digest(self) -> str:
        """这一次处置的**内容摘要**。重发判据的两个半边之一（另一半是版本）。

        它**不含 ``at``**，而这是刻意的：重发一次相同的处置会带一个新的时刻，
        而那个时刻不是「另一件事」的一部分。它含的每一格都是**人写下来的那句话**
        （状态、结果、谁做的、在等什么、期限、证据指针）—— 换了其中任何一格，
        这就是另一次处置，于是它不被当成重发。

        用内容摘要而不是「第几次」与 ``domain/refresh_plan.py`` 那一格逐字同源：
        计数住在库里，同一条内容在两次部署里会拿到两个号。
        """
        parts = [
            f"state={self.state.value}",
            f"resolution={self.resolution}",
            f"by={self.resolution_by}",
            f"owner={self.owner or ''}",
            f"blocked_by={self.blocked_by}",
            f"due_at={self.due_at.isoformat() if self.due_at else ''}",
        ]
        parts += [f"evidence={ref}" for ref in self.evidence_refs]
        return sha256_text("\n".join(parts))[:16]

    # ------------------------------------------------------------------
    # 纯转移
    # ------------------------------------------------------------------
    def resolve(
        self,
        *,
        at: datetime,
        state: ActionItemState,
        by: str,
        expected_version: int,
        resolution: str = "",
        owner: str | None = None,
        blocked_by: str = "",
        due_at: datetime | None = None,
        evidence_refs: tuple[str, ...] | None = None,
    ) -> "ActionItem":
        """处置之后的那一件：``revision = expected_version + 1``。

        ``expected_version`` **必填**（``>= 1``）：它不是一个可以由这一层补上的默认值 ——
        补上的那个默认值恰好是「盘上现在那一版」，于是「人在自己屏幕上看到的版本」这条
        判据在调用方不写的时候自己成立，而这条判据正是它存在的理由。

        ## 三条判据

        * **终态不许再被处置**。一件 ``done`` / ``dismissed`` 的工作是历史，重新打开
          它不是一次处置，是「又出现了同一件工作」—— 那一件的正确处置是**新建一条**
          （那条约束盖的只有未结状态，所以新建得出来）。就地重开的代价不是多了一行，
          而是那条历史被改写了：``resolved_at`` 会被清掉，于是「它上个月被处理过」
          这件事在盘上消失。
        * **``expected_version`` 必须等于这一版的 ``revision``**。不等时抛
          :class:`~marketops.domain.errors.ActionItemConflictError`，消息里两个版本
          都要有 —— 这一层先判一次是为了让**纯函数**自己也是对的，而真正的并发判据在
          存储层（``WHERE action_id = ? AND revision = ?`` 的 ``rowcount``）：一个只在
          这一层判的实现读不出「有没有人先写了」，因为它读到的那份是调用方手里的快照。
        * 终态的两格（``resolution`` / ``resolution_by``）由模型级的判据兜住，
          这里不重复判。

        ``owner`` 缺省 ``None`` = **不改**（一个人可以替别人关掉一件工作）；
        认领要显式传 ``owner=by``。缺省「自动认领」会让每一次关闭都顺手把 owner
        改成关闭它的人，而「这件事原本是谁在做」就此消失。
        """
        if self.is_terminal:
            raise ActionItemConflictError(
                f"action_id={self.action_id!r} 当前是 {self.state.value}（终态），"
                "终态的工作不再被处置。\n"
                "要重新打开同一件工作，正确动作是**新建一条**（那条唯一性约束只盖"
                "未结状态，所以新建得出来），而不是把这一条改回去 —— 改回去会清掉"
                "它的 resolved_at，于是「它上一次是什么时候被处理的」在盘上消失。"
            )
        if expected_version < 1:
            raise ActionItemConflictError(
                f"处置一件待办时 expected_version 必须 >= 1（收到 {expected_version}）："
                "处置总是基于某一版。"
            )
        if expected_version != self.revision:
            raise ActionItemConflictError(
                f"action_id={self.action_id!r} 手上的版本是 {expected_version}，"
                f"而这一版是 {self.revision}（当前状态 {self.state.value}）—— "
                "请重读这一件再决定。"
            )
        moment = to_utc(at)
        return ActionItem(
            action_id=self.action_id,
            revision=expected_version + 1,
            workspace=self.workspace,
            scope_ref=self.scope_ref,
            action_type=self.action_type,
            owner=self.owner if owner is None else owner,
            state=state,
            due_at=self.due_at if due_at is None else due_at,
            blocked_by=blocked_by,
            reason=self.reason,
            evidence_refs=(
                self.evidence_refs if evidence_refs is None else evidence_refs
            ),
            resolution=resolution,
            resolution_by=by if state in TERMINAL_STATES else "",
            expected_version=expected_version,
            created_at=self.created_at,
            resolved_at=(
                moment if state in TERMINAL_STATES else None
            ),
            updated_at=moment,
            note=self.note,
        )

    @classmethod
    def opened(
        cls,
        *,
        action_id: str,
        workspace: str,
        scope_ref_value: str,
        action_type: ActionType,
        reason: str,
        owner: str | None = None,
        due_at: datetime | None = None,
        blocked_by: str = "",
        evidence_refs: tuple[str, ...] = (),
        at: datetime | None = None,
        note: str = "",
    ) -> "ActionItem":
        """新开一件：``revision=1``、``expected_version=0``、``state=open``。

        三格都由这一处给出，不由调用方给 ——「新的一件是第几版」不该是一个调用方
        可以填错的参数。对照 ``ReviewCase`` 那一侧：它的 ``case_id`` 是内容寻址的
        （同一个问题算出来是同一个 id），而**这一件不能那样**：同一个主体上的同一类
        工作做过三次就是三条历史，内容寻址的函数会把这三次算成一个 id，于是第二条
        历史写不出来。可复用的只有 ``(主体, 类型)`` 那条约束，而它盖的只有未结状态。
        """
        moment = to_utc(at or utc_now())
        return cls(
            action_id=action_id,
            revision=1,
            workspace=workspace,
            scope_ref=scope_ref_value,
            action_type=action_type,
            owner=owner,
            state=ActionItemState.OPEN,
            due_at=due_at,
            blocked_by=blocked_by,
            reason=reason,
            evidence_refs=evidence_refs,
            expected_version=0,
            created_at=moment,
            updated_at=moment,
            note=note,
        )

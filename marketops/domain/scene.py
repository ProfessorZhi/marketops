"""Scene —— 「一次立项、持续经营一个市场」的那个单位。

## 它今天为什么必须成为一个对象

在本轮之前，「Scene」在本仓是一个**讲法**，落地物只有两样：``profiles/<id>/``
这个目录，以及按目录字节算出来的 ``scene_version``（``domain/profile.py:413``）。
两样都回答「这一版规则的字节是什么」，**没有一样回答**「这个场景现在什么状态、
哪一版在生效、谁批准过、上一版和新一版之间什么要重做」。

于是「暂停一个场景」这句话在代码里说不出来。这不是缺一个字段，是缺主语：
``RouteStatus`` 与 ``AccountStatus`` 都是**对象**的状态机，而它们上面那一层
（这些对象属于哪次立项）没有身份。

## 三个版本号，各自回答一个问题

* ``latest_version`` —— 作者写到第几版了。
* ``effective_version`` —— **哪一版在生效**。只有在 ``ACTIVE`` 上才有值。
* ``SceneVersion.version`` —— 某一版的编号。

三者分开，是因为「新写一版」与「让新版生效」是两件事，而合并它们会让
「改了一版但还没批准」这件事在类型上消失 —— 那正是 §4 逐字要挡的
「未获批准的新场景只能 draft/preview」。合并之后，一次编辑就自动生效，
而报告上看起来只是「版本号变大了」。

## in-flight 的东西钉在旧版上

新一版生效**不会**改动已经在途的 work item：它们钉的是自己当时的
``scene_version``（``work_item_snapshot``），这在制度上叫「不得中途换规则」。
本模块只负责让「新一版生效」与「旧版仍在跑」同时可表达；钉住是作业层的既有性质。

## 暂停域与仍然要跑的事

``PAUSED`` / ``ARCHIVED`` 停的是**新的**研究与外联派发。回信、退订、投诉、退信
**照常受理并执行安全动作** —— 一个暂停的场景不会因此停止收到别人的回信，
而把回信也一起停掉，等于在暂停期间静默攒下一批该抑制的地址。
所以状态判断分两个入口：:meth:`SceneRecord.accepts_new_work`（派发前看）与
「受理入口不看它」（受理侧不调用这个属性）。把两者做成同一个开关的地方，
迟早会把回信一起停掉。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import field_validator, model_validator

from marketops.domain.base import StrictModel
from marketops.domain.enums import BudgetLine
from marketops.domain.maintenance import RefreshPlan
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.util import SEP, check_canonical_id, sha256_text, to_utc


class SceneState(str, Enum):
    """一个 Scene 的生命周期。

    四个成员，没有第五个「已批准」—— 批准是 ``ACTIVE`` 的**前提条件**
    （见 :meth:`SceneRecord.activate`），不是它旁边的一个状态。
    独立成一个状态的话，一个「已批准但未启用」的 Scene 与「草稿」
    在派发判定上是同一格，而在报告上是两格。
    """

    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


#: 合法转移。做成一张表而不是散在各分支里：一张表能被遍历着测。
#:
#: ``ARCHIVED`` 的出边为空集 —— 终态是「没有边」，不是「有一条边但被拒绝」。
#: §4 的「归档」是「这个市场不再经营」，不是「暂时不看」；后者是 ``PAUSED``。
SCENE_TRANSITIONS: dict[SceneState, frozenset[SceneState]] = {
    SceneState.DRAFT: frozenset({SceneState.ACTIVE, SceneState.ARCHIVED}),
    SceneState.ACTIVE: frozenset({SceneState.PAUSED, SceneState.ARCHIVED}),
    SceneState.PAUSED: frozenset({SceneState.ACTIVE, SceneState.ARCHIVED}),
    SceneState.ARCHIVED: frozenset(),
}


class PauseReason(str, Enum):
    """为什么停。

    ``OPERATOR`` 与 ``POLICY`` 分开，是因为**谁能解除不同**：前者自己 resume，
    后者要等那条策略不再成立。这与 ``JobState.PAUSED`` / ``BLOCKED`` 的分法
    是同一条纪律（``domain/enums.py:482``）。
    """

    OPERATOR = "operator_pause"
    POLICY = "policy_hold"


class SceneBudgetLine(StrictModel):
    """Scene 版本里的一条预算上限。

    ``line`` 用既有的 :class:`~marketops.domain.enums.BudgetLine`：搜索一次、
    抓一个文档、跑一次推理的量纲不同，合成一个总数字之后没人能回答
    「这次超支是搜索跑多了还是模型跑多了」。
    """

    line: BudgetLine
    ceiling: int

    @field_validator("ceiling")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("预算上限不得为负")
        return value


class SceneVersion(StrictModel):
    """一版 Scene 的**全部**绑定。

    §4 要求一个 Scene 至少绑定：地区、业务场景、产品/用途、准入与排除、
    角色策略、来源计划、预算、复核计划。八样在这里各有一个字段，一个不少 ——
    少一样不会让构造失败，但会让「这次经营的范围是什么」在记录里没有答案，
    而下游每一处判定都要问这个问题。

    ``profile_id`` / ``profile_version`` 也在这儿：Scene 不是一个脱离规则的
    抽象层，它**绑定**着一份具体版本的 profile（grading / role-matrix / 排除词）。
    两者分开记，是因为「profile 换了一版而 Scene 没换」是一件必须看得见的事。
    """

    scene_id: str
    #: 这个 Scene 属于哪个工作区。**对象授权链的第一段**：一个 Scene 的对象
    #: 只对拥有它的工作区可见，而工作区来自令牌声明而非请求参数。
    workspace: str
    version: int
    region: tuple[str, ...]
    scenario: str
    product: str
    admissions: tuple[str, ...]
    exclusions: tuple[str, ...]
    role_policy: str
    source_plan: tuple[str, ...]
    budget: tuple[SceneBudgetLine, ...]
    refresh: RefreshPlan
    #: 触达节奏（§6）。与 ``refresh`` 同一个位置、同一条理由：节奏是一个**业务
    #: 决定**，改它必须留下版本边界与批准记录，所以它只能通过新写一版来改。
    #: 缺省是**未标定**的那一份（``OutreachPolicyDecl.uncalibrated_default``），
    #: 而每一份用到它的回执都会把 ``uncalibrated`` 报出来。
    outreach: OutreachPolicyDecl
    profile_id: str
    profile_version: str
    created_at: datetime
    note: str = ""
    #: 取代了哪一版。第一版为 None —— **不是 0**：0 是一个合法编号的邻居，
    #: 而「没有上一版」与「上一版是 0」是两件事。
    supersedes: int | None = None

    @field_validator("scene_id", "workspace")
    @classmethod
    def _canonical(cls, value: str, info) -> str:
        return check_canonical_id(value, info.field_name)

    @field_validator("version")
    @classmethod
    def _version_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("版本号从 1 开始。0 会被读成「还没写过」，与「第一版」撞车")
        return value

    @field_validator("region", "source_plan")
    @classmethod
    def _non_empty_list(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                f"{info.field_name} 不得为空。空列表在装载与派发两处都长得像"
                "「这个场景不做任何事」—— 而它真实的含义只会是配置漏了。"
            )
        if any(not item.strip() for item in value):
            raise ValueError(f"{info.field_name} 不得含空串")
        if len(set(value)) != len(value):
            raise ValueError(f"{info.field_name} 不得有重复项")
        return value

    @field_validator("scenario", "product", "role_policy", "profile_id", "profile_version")
    @classmethod
    def _non_empty_scalar(cls, value: str, info) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} 不得为空")
        return value

    @field_validator("exclusions")
    @classmethod
    def _exclusions_non_empty(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                "排除清单不得为空。一个什么都不排除的场景在装载层就装不出来"
                "（profile_loader 同样要求 exclusions 非空），而这里放它过去会让你"
                "构造出一个「范围无限大」的 Scene —— 那不是一个更宽松的配置，"
                "是一个没有边界的采集。"
            )
        if any(not item.strip() for item in value):
            raise ValueError("排除清单不得含空串")
        return value

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @model_validator(mode="after")
    def _supersedes_is_the_previous_one(self) -> "SceneVersion":
        if self.supersedes is None and self.version != 1:
            raise ValueError(f"第 {self.version} 版必须写明它取代哪一版（supersedes）")
        if self.supersedes is not None and self.supersedes < 1:
            # 这一条单列，是因为上面那条**恰好**放它过去：第 1 版配
            # ``supersedes=0`` 时 ``0 == 1 - 1``，于是「第 1 版取代第 0 版」
            # 被判为合法 —— 而第 0 版不是一个版本号，是一个「还没写过」
            # （``version`` 的校验器逐字拒绝了它）。字段注释里那句
            # 「不是 0，0 是一个合法编号的邻居」在修这一条之前只是自觉。
            raise ValueError(
                f"第 1 版没有可取代的上一版，supersedes 必须是 None，给的是 "
                f"{self.supersedes}。0 不是版本号，是「还没写过」的同义词。"
            )
        if self.supersedes is not None and self.supersedes != self.version - 1:
            # 允许跳过编号意味着存在一个「从未写过」的版本号，而历史里出现空洞
            # 时，没人能回答「第 3 版去哪了」—— 是丢了还是根本不存在。
            raise ValueError(
                f"supersedes 必须是上一版：第 {self.version} 版只能取代第 "
                f"{self.version - 1} 版，给的是 {self.supersedes}"
            )
        return self

    @property
    def frozen_sha256(self) -> str:
        """这一版**内容**的摘要。

        授权门与 wave manifest 绑的是它，不是 ``version`` 这个整数：
        整数可以被重新解释（「第 2 版」在两次部署里可能是两份不同的规则），
        而字节不会。它同时是「这一批东西是按哪份规则做的」这个问题的答案。
        """
        return sha256_text(
            SEP.join(
                [
                    "scene-version",
                    self.scene_id,
                    self.workspace,
                    str(self.version),
                    *self.region,
                    self.scenario,
                    self.product,
                    *self.admissions,
                    *self.exclusions,
                    self.role_policy,
                    *self.source_plan,
                    *(f"{b.line.value}={b.ceiling}" for b in self.budget),
                    *(
                        f"{r.aspect.value}:{r.after_days}:{int(r.calibrated)}"
                        for r in self.refresh.rules
                    ),
                    # 触达节奏**进摘要**。它是受控内容的一部分：改节奏就是改这一版
                    # 的规则，而「这一批是按哪份规则做的」这个问题的答案必须跟着变。
                    # ``calibrated`` 也进摘要 —— 它不是元数据：一份把同一个数字从
                    # 「未标定」升级成「已批准」的新版本，是一份**内容不同**的版本，
                    # 而摘要不变的实现会让那次升级在受控内容上无迹可寻。
                    f"outreach.max_per_wave={self.outreach.max_per_wave}",
                    f"outreach.max_per_recipient={self.outreach.max_per_recipient}",
                    f"outreach.max_per_account={self.outreach.max_per_account}",
                    *(
                        f"outreach.wait[{r.stage.value}]={r.after_hours}:"
                        f"{int(r.calibrated)}"
                        for r in self.outreach.waits
                    ),
                    *(
                        f"outreach.prior[{r.stage.value}]={r.allowed_prior}:"
                        f"{int(r.calibrated)}"
                        for r in self.outreach.prior_touches
                    ),
                    self.profile_id,
                    self.profile_version,
                ]
            )
        )[:32]

    def exclusions_cover(self, required: tuple[str, ...]) -> tuple[str, ...]:
        """哪些**必排除项**没有被这一版排除掉。空元组 = 覆盖。

        这是 §4 那条「不得借产品扩展撤销已被批准 profile 的排除」的**执行器**：
        新场景、新产品可以加排除，不可以减。做成一个返回缺失项的纯函数而不是
        一个布尔，是因为拒绝时要说的恰恰是「缺了哪几条」。
        """
        have = set(self.exclusions)
        return tuple(term for term in required if term not in have)


class SceneApproval(StrictModel):
    """谁在什么时候让哪一版生效。

    这是一条**记录**，不是一条授权：它记的是「发生过」，不是「可以做」。
    发送侧的授权是另一件事（见 outreach 的授权门），两者同名不同物 ——
    让一个「有人批准过这个场景」的记录能被当成「这批邮件可以发了」，
    正是要避免的那类混淆。
    """

    version: int
    at: datetime
    by: str

    @field_validator("at")
    @classmethod
    def _at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("批准时刻必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("by")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("批准人不得为空 —— 一条没有主体的批准记录无法被追问")
        return value


class SceneRecord(StrictModel):
    """一个 Scene 的**当前状态**（版本历史另存）。

    与 :class:`SceneVersion` 分开，是因为它们的读写模式不同：版本一旦写下
    就不再变（append-only），而状态每一次 pause / resume 都在变。合成一个
    对象，就得让「改状态」也造一个新版本 —— 于是暂停一个场景会污染它的
    规则历史。
    """

    scene_id: str
    workspace: str
    state: SceneState
    #: 作者写到第几版。
    latest_version: int
    #: **哪一版在生效。** 只有 ``ACTIVE`` 上有值；草稿与暂停/归档的取值
    #: 保留在它最后一次生效的那一版上 —— 暂停不会让「生效的是哪一版」这个问题
    #: 变成「没有」，恢复时也就不需要有人再猜一次。
    effective_version: int | None = None
    created_at: datetime
    updated_at: datetime
    pause_reason: PauseReason | None = None
    approvals: tuple[SceneApproval, ...] = ()
    # 三个变更方法各自把 ``at`` 先过一遍 ``to_utc``：``model_copy`` **不校验**，
    # 所以字段上的校验器管不到它们 —— 一个 naive 的 ``at`` 会安静地写进
    # ``updated_at``，而库里那一列随后按字符串参与排序。
    #: 每写一次加一。**它不是历史，是一个乐观锁的序号。**
    #:
    #: 存储层用 ``UPDATE ... WHERE scene_id = ? AND revision = ?`` 做 CAS：
    #: 两个控制端同时读到 PAUSED、同时决定 resume，只有一个能写成。
    #: 用「比较整条记录」或「比较 (state, latest_version)」代替它都不成立 ——
    #: 前者要逐字段比对（含时间与浮点），后者漏掉 approvals 的增长，
    #: 而「两次 activate 都写进去了」在报告上只显示 approvals 多了一条。
    revision: int = 0

    @field_validator("created_at", "updated_at")
    @classmethod
    def _record_times_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Scene 的时刻必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @model_validator(mode="after")
    def _state_matches_versions(self) -> "SceneRecord":
        if self.latest_version < 1:
            raise ValueError("latest_version 至少是 1")
        if self.effective_version is not None:
            if self.effective_version < 1 or self.effective_version > self.latest_version:
                raise ValueError(
                    f"effective_version ({self.effective_version}) 必须落在 "
                    f"1..latest_version ({self.latest_version}) 之内"
                )
        if self.state is SceneState.DRAFT and self.effective_version is not None:
            raise ValueError(
                "草稿态的 Scene 不得有生效版本 —— 一个从未被批准的场景若带着"
                "effective_version，派发侧就会认为它已经可以开工"
            )
        if self.state is SceneState.ACTIVE and self.effective_version is None:
            raise ValueError("ACTIVE 的 Scene 必须有生效版本")
        if self.state is SceneState.ACTIVE and self.pause_reason is not None:
            raise ValueError("ACTIVE 的 Scene 不得带暂停理由")
        if self.state is SceneState.PAUSED and self.pause_reason is None:
            raise ValueError(
                "PAUSED 必须写明理由。缺理由的暂停与「有人点了一下」在报告上"
                "无法区分，而 operator_pause 要自己 resume、policy_hold 要等策略失效"
            )
        return self

    @property
    def accepts_new_work(self) -> bool:
        """**只用于派发前判定。** 受理侧（回信/退信/退订/投诉）不看它。

        见模块 docstring 的「暂停域与仍然要跑的事」。
        """
        return self.state is SceneState.ACTIVE

    @property
    def dispatch_refusal(self) -> str:
        """**派发侧**的拒绝理由；可以派发时是空串。派发前判定的「为什么」。

        与 :meth:`accepts_new_work` 成对：那一格答「行不行」，这一格答「为什么不行」。
        分成两格而不是让这一格返回 ``None``，是因为这句话有两个读者，而他们要的
        形态不同：一个是 ``if record.dispatch_refusal:`` 里的判词，一个是回执里
        那一格要给调用方看的原文。两种都可以从同一个字符串拿到。

        ## 为什么这句话住在 domain 而不是某一个调用方里

        在被抽到这里之前，它只存在于 ``application/maintenance.py::_skip_reason``，
        而研究派发侧（``adapters/mcp_server/tools.py``）根本没有这个问题 —— 那正是
        本轮修掉的缺口：维护周期停了、研究照常受理。补上之后，同一个判定有了两个
        调用方，而**两处各写一份的后果不是文案不一致**，是同一秒里维护侧说「暂停」
        而研究侧说「可以开工」，读报告的人无从判断哪一个对。

        措辞里那三样（``pause_reason`` 的值、``updated_at``、``draft`` 那句）都是
        「谁能让它重新开工」的线索：``operator_pause`` 要有人点 resume，
        ``policy_hold`` 要等策略失效，``draft`` 要有人先批准。丢掉任何一样，
        这条拒绝就退化成一句「不行」。

        **空串表示可以派发**，所以调用方要写成 ``if refusal:`` —— 这一条写进
        docstring 是因为一个把「可以派发」误写成 ``None`` 的实现，在 ``if`` 与
        ``is not None`` 两种写法下都会读得通，而前者会静默放行。
        """
        if self.state is SceneState.PAUSED:
            reason = self.pause_reason.value if self.pause_reason else "未写明"
            return f"paused: {reason}（自 {self.updated_at.isoformat()}）"
        if self.state is SceneState.ARCHIVED:
            return f"archived（自 {self.updated_at.isoformat()}）"
        if self.state is SceneState.DRAFT:
            return "draft: 尚未批准生效，不派发"
        return ""  # pragma: no cover —— 上面已穷尽 ACTIVITY 之外的三个成员

    @property
    def pending_version(self) -> int | None:
        """写了一版但还没生效。没有则为 None。"""
        if self.effective_version is None:
            return None
        if self.latest_version > self.effective_version:
            return self.latest_version
        return None

    def with_version(self, version: SceneVersion, *, at: datetime) -> "SceneRecord":
        """把新写的一版登记进来（**不生效**）。

        版本号必须紧接上一版；``scene_id`` / ``workspace`` 必须一致。
        它在任何状态下都允许 —— 暂停的场景当然可以被编辑，只是编辑不会
        自动让它开工。
        """
        at = to_utc(at)
        if version.scene_id != self.scene_id or version.workspace != self.workspace:
            raise ValueError(
                f"版本 {version.scene_id}/{version.workspace} 不属于 "
                f"{self.scene_id}/{self.workspace}"
            )
        if version.version != self.latest_version + 1:
            raise ValueError(
                f"新版本号必须是 {self.latest_version + 1}，给的是 {version.version}。"
                "跳号会让历史里出现一个「从未写过」的编号，而没人能回答它是丢了"
                "还是不存在"
            )
        return self.model_copy(
            update={
                "latest_version": version.version,
                "updated_at": at,
                "revision": self.revision + 1,
            }
        )

    def activate(
        self, *, version: int, at: datetime, by: str, from_state: SceneState | None = None
    ) -> "SceneRecord":
        """让某一版生效。**这就是「批准」这个动作。**

        ``from_state`` 是可选的前置状态（CAS）：调用方读到 PAUSED、据此决定
        resume 时，中间有人把它归档了，这里必须整次拒绝而不是覆盖 ——
        与 ``set_state`` 的 ``expected`` 是同一条纪律。
        """
        at = to_utc(at)
        if from_state is not None and from_state is not self.state:
            raise SceneStateError(
                f"状态已变：读到的前置是 {from_state.value}，当前是 {self.state.value}"
            )
        if self.state is SceneState.ARCHIVED:
            raise SceneStateError("已归档的 Scene 不得激活 —— 归档是终态")
        if version < 1 or version > self.latest_version:
            raise SceneStateError(
                f"要让第 {version} 版生效，但它不在 1..{self.latest_version} 之内"
            )
        if not by.strip():
            raise SceneStateError("激活必须写明主体")
        approval = SceneApproval(version=version, at=at, by=by)
        return self.model_copy(
            update={
                "state": SceneState.ACTIVE,
                "effective_version": version,
                "pause_reason": None,
                "updated_at": at,
                "approvals": self.approvals + (approval,),
                "revision": self.revision + 1,
            }
        )

    def transition(
        self,
        target: SceneState,
        *,
        at: datetime,
        pause_reason: PauseReason | None = None,
        from_state: SceneState | None = None,
    ) -> "SceneRecord":
        """状态转移（不含激活那一条 —— 那条要版本号与主体，单独一个动词）。"""
        at = to_utc(at)
        if from_state is not None and from_state is not self.state:
            raise SceneStateError(
                f"状态已变：读到的前置是 {from_state.value}，当前是 {self.state.value}"
            )
        if target not in SCENE_TRANSITIONS[self.state]:
            raise SceneStateError(
                f"非法的 Scene 状态转移：{self.state.value} → {target.value}"
            )
        if target is SceneState.ACTIVE:
            # 恢复：生效版本**保持不变**。§4 逐字要求「恢复不得重发已发邮件或
            # 重吃整个母池」—— 那条的执行方式是「恢复不改任何游标」，而游标
            # 在版本之外，所以这里只需要保证 effective_version 不被重置。
            if self.effective_version is None:
                raise SceneStateError(
                    "这个 Scene 从未激活过，没有可恢复的生效版本；"
                    "首次开工请用 activate（它要求写明批准人）"
                )
        if target is SceneState.PAUSED and pause_reason is None:
            raise SceneStateError("暂停必须写明理由")
        if target is not SceneState.PAUSED and pause_reason is not None:
            raise SceneStateError(f"只有暂停带理由，{target.value} 不得带")
        return self.model_copy(
            update={
                "state": target,
                "pause_reason": pause_reason,
                "updated_at": at,
                "revision": self.revision + 1,
            }
        )


class ImpactScope(str, Enum):
    """一版变到什么的时候，哪一类东西要重做。"""

    #: 账号准入的判定（admissions / exclusions 变了）。
    ACCOUNT_ADMISSION = "account_admission"
    #: 责任关系（谁拥有/运营/维护）。
    RESPONSIBILITY = "responsibility"
    #: 场地资产事实本身。
    ASSET_FACT = "asset_fact"
    #: 角色与邮箱（谁在那个岗位上、那条路还通不通）。
    ROLE_EMAIL = "role_email"
    #: 产品适配。
    PRODUCT_FIT = "product_fit"
    #: 来源供给（该去看哪些来源）。
    SOURCE_SUPPLY = "source_supply"
    #: 已经定级过的路线。
    ROUTE_GRADE = "route_grade"
    #: 已经生成的草稿。
    DRAFT = "draft"
    #: 已经冻好的 wave。
    WAVE = "wave"


#: **值变了 → 什么要重做。** 这张表是模块里唯一一处「影响面」的知识。
#:
#: 它被写下来而不是被推导，因为推导不出来：为什么改 ``role_policy`` 要作废草稿，
#: 是一个业务判断（草稿里写着「致设施总监」，而角色策略刚把设施总监移出了
#: 有效路线）。把它写成数据，才能被逐条测试、逐条争辩。
#:
#: 缺任何一格都是**故意的**：``budget`` 与 ``refresh`` 不在表里，
#: 意味着改预算或改复核节奏**不会**让任何已有结论失效 —— 它们改变的是
#: 「以后做多少」与「什么时候再看一眼」，不是「已经看到的东西还成不成立」。
SCENE_IMPACT_RULES: dict[str, tuple[tuple[ImpactScope, ...], tuple[ImpactScope, ...]]] = {
    # 字段 → (必须重取, 必须重判)
    "region": (
        (ImpactScope.SOURCE_SUPPLY, ImpactScope.ASSET_FACT),
        (ImpactScope.ACCOUNT_ADMISSION, ImpactScope.RESPONSIBILITY),
    ),
    "scenario": ((), (ImpactScope.PRODUCT_FIT, ImpactScope.DRAFT, ImpactScope.WAVE)),
    "product": (
        (ImpactScope.SOURCE_SUPPLY,),
        (ImpactScope.PRODUCT_FIT, ImpactScope.DRAFT, ImpactScope.WAVE),
    ),
    "admissions": ((), (ImpactScope.ACCOUNT_ADMISSION,)),
    "exclusions": ((), (ImpactScope.ACCOUNT_ADMISSION, ImpactScope.ROUTE_GRADE)),
    "role_policy": ((), (ImpactScope.ROLE_EMAIL, ImpactScope.ROUTE_GRADE, ImpactScope.DRAFT)),
    "source_plan": ((ImpactScope.SOURCE_SUPPLY,), ()),
    "profile_id": (
        (ImpactScope.SOURCE_SUPPLY, ImpactScope.ASSET_FACT, ImpactScope.RESPONSIBILITY),
        (
            ImpactScope.ACCOUNT_ADMISSION,
            ImpactScope.ROLE_EMAIL,
            ImpactScope.PRODUCT_FIT,
            ImpactScope.ROUTE_GRADE,
            ImpactScope.DRAFT,
            ImpactScope.WAVE,
        ),
    ),
    "profile_version": (
        (ImpactScope.SOURCE_SUPPLY,),
        (
            ImpactScope.ACCOUNT_ADMISSION,
            ImpactScope.ROLE_EMAIL,
            ImpactScope.ROUTE_GRADE,
            ImpactScope.DRAFT,
        ),
    ),
}


class SceneImpact(StrictModel):
    """换一版会发生什么。§4 逐字要求「变更必须说明影响」。"""

    from_version: int
    to_version: int
    refetch: tuple[ImpactScope, ...]
    reassess: tuple[ImpactScope, ...]
    reusable: tuple[ImpactScope, ...]

    @property
    def is_empty(self) -> bool:
        return not self.refetch and not self.reassess


def scene_impact(before: SceneVersion, after: SceneVersion) -> SceneImpact:
    """对比两版，列出重取 / 重判 / 不受影响三类。

    ``reusable`` 不是「剩下没提到的那些」—— 它是**全部** :class:`ImpactScope`
    减去前两类。这样回执里那一栏永远是全的：读的人不需要知道表里有哪些字段，
    就能看出「这次改动没碰到的面」是哪几个。
    """
    if before.scene_id != after.scene_id:
        raise ValueError(
            f"不能跨 Scene 比对影响面：{before.scene_id} vs {after.scene_id}。"
            "两个场景的字段即使同名也不是同一件事"
        )
    refetch: set[ImpactScope] = set()
    reassess: set[ImpactScope] = set()
    for field, (fetch, assess) in SCENE_IMPACT_RULES.items():
        if getattr(before, field) == getattr(after, field):
            continue
        refetch |= set(fetch)
        reassess |= set(assess)
    # 重取蕴含重判：重新拿到的东西当然要重新判一遍。不减这一下，
    # 同一个 scope 会在两栏里各出现一次，而读的人会以为那是两件事。
    reassess -= refetch
    order = {scope: index for index, scope in enumerate(ImpactScope)}
    return SceneImpact(
        from_version=before.version,
        to_version=after.version,
        refetch=tuple(sorted(refetch, key=order.__getitem__)),
        reassess=tuple(sorted(reassess, key=order.__getitem__)),
        reusable=tuple(
            scope for scope in ImpactScope if scope not in refetch and scope not in reassess
        ),
    )


class SceneStateError(Exception):
    """Scene 状态上的非法动作。

    定义在这里而不是 ``domain/errors.py``：它只由本模块抛，而把它放进那个
    共享异常表会让「Scene 的状态机」与「作业的状态机」在 except 语句里
    长得一样 —— 两者的处置不同（一个是改配置/改审批，一个是改派发）。
    """


__all__ = [
    "SceneState",
    "SCENE_TRANSITIONS",
    "PauseReason",
    "SceneBudgetLine",
    "SceneVersion",
    "SceneApproval",
    "SceneRecord",
    "SceneStateError",
    "ImpactScope",
    "SCENE_IMPACT_RULES",
    "SceneImpact",
    "scene_impact",
]

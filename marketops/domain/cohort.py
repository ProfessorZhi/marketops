"""§B12：**一次冻结的成员名单** —— 从账号池里读一次，把它连同读数一起冻下来。

## 它为什么必须存在

在此之前，维护、起草与研究三条链的**主体**只有一个来源：operator 手写的
``inputs/<name>.yaml``（``application/inputs.py::account_subjects``）。那是一个
真实的人工桥 —— 系统自己发现并晋升出来的账号，**任何一条产品路径都读不到**，
因为它没有被写进那份 YAML。本轮要消掉的就是这一步。

「换成从池子里现读」是不够的：池子随时在变（新账号晋升进来、某一行被改判），
而一队已经派出去的、按 ``ordinal`` 编译过的工作单元**必须**对得回它当时读的是
哪一份名单。所以这一层交付的是一个**对象**：某一次读的成员、顺序、以及每一行
「为什么在」和「为什么不在」，全部冻在一起，内容寻址。

## 三条判据

### 1. 成员顺序不是风格问题

它决定 ``ordinal``，因而决定 ``work_item_id``（``application/inputs.py`` 逐字：
重排同一份清单会被 ``JobStore.compile_work_items`` 读成另一批工作）。所以顺序
必须来自一个**池子里本来就有的**确定性次序 —— :meth:`AccountPoolStore.list_accounts`
按 ``account_id`` 稳定排序，这里照抄它，不重排。

（YAML 那一侧的顺序是 operator 写在文件里的，是有意义的；池子这一侧没有operator，
**没有**一个「本来想按什么排」的答案。按 ``account_id`` 至少是那句「截断砍掉的是
尾部」能成立的前提。这不是质量排序，见第 3 条。）

### 2. 判据只有一条轴：账号行上那个 ``status``

名单要不要某个账号，**只看** ``MarketAccount.status``。地区、身份、官网那些判据
**不在这里重判** —— 它们已经由 §B10 的准入链落成 ``status`` 了。在装配时再筛一遍
地区，等于给同一句话第二个定义处：两者不一致的那一天，报告上是「名单里没有它」
与「它被判成 out_of_region」同时成立，而没有任何一栏说得清哪句对。

顺带一个后果要写下来：这条选择**不是一次质量筛选**。``limit`` 砍掉的是
``account_id`` 最大的那些，不是「最差的那些」—— 池子里今天没有一列说得清质量。
要按质量取，得先有那一列。

### 3. 排除要留下**原因码**，不是一个数字

与 §6.3 对拒收回执的要求逐字相同：``excluded = 37`` 里可能有 30 条是「这个账号
在等人裁决」（正常）、3 条是「它被判成与另一行重复」（要人看一眼）、4 条是
「这一轮的上限砍到了它」（下一轮就轮到）。同一个数字，三种处置。

所以 :class:`CohortExclusion` 逐条带 :class:`CohortExclusionCode`，而不是让调用
方去数一个整数。

## 这个模型**不**主张的事

- **不判断准入。** 它读的是已经写在账号行上的判定，不重新跑 ``grading.yaml``。
  那一侧（``list_accounts``）报的是「此刻的规则怎么看这一行」，与这里读的
  「写下来的那一次判定」是两句不同的话（``domain/account.py`` 逐字如此）。
- **不发明主体。** 池子里没有的就是没有 —— 本层不按域名、不按名字去猜一个账号
  该不该被加进来。``ports/pool.py`` 已经写过这条：一个会自己发现主体的选择器
  必然也会自己发明主体。
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum

from pydantic import ValidationInfo, field_validator, model_validator

from .account import MarketAccount
from .base import StrictModel as _Strict
from .enums import AccountStatus
from .util import check_canonical_id, sha256_text, to_utc

__all__ = [
    "COHORT_ID_PREFIX",
    "AccountCohort",
    "CohortExclusion",
    "CohortExclusionCode",
    "CohortMember",
    "CohortSelection",
    "WORKABLE_STATUSES",
    "cohort_id_for",
    "exclusion_reason",
    "freeze_cohort",
]

COHORT_ID_PREFIX = "coh"

#: 与 ``domain/route_records.py`` 同一个分隔符：两处拼的都是内容寻址的原像，
#: 用同一个不可打印字符，是为了让「这一段到那一段」在两边读起来是同一种拼接。
SEP = "\x1f"

#: 池子里哪些 ``status`` 算「这一批可以研究 / 维护 / 起草」。
#:
#: 三个进名单：
#:
#: * ``DISCOVERED`` —— 刚晋升进来，还没有任何判定。**没有判定不是一条坏判定**
#:   （``enums.py`` / ``grading/engine.py`` 都写着这一条），把它挡在外面会让
#:   每一个新账号都要先有人判一次才动得了，而那正是本轮要消掉的人工桥。
#: * ``ELIGIBLE`` —— 判过，可以研究。
#: * ``RESEARCHED`` —— 已经研究过，它的价值在**复核**与**起草**那一侧
#:   （§B22 的增量刷新问的正是「这一格什么时候该重看」）。
#:
#: 一个都不进名单的四个（``HOLD`` / ``IRRELEVANT`` / ``DUPLICATE`` / ``ARCHIVED``）
#: 各有各的原因码，见 :class:`CohortExclusionCode`。
WORKABLE_STATUSES: tuple[AccountStatus, ...] = (
    AccountStatus.DISCOVERED,
    AccountStatus.ELIGIBLE,
    AccountStatus.RESEARCHED,
)


class CohortExclusionCode(str, Enum):
    """一个账号**没有**进这一份名单的原因。闭集。

    每个成员对应一个 ``status``，理由是那一条的处置**不一样** —— 把它折成一句
    「不在名单里」会让「下一轮就轮到它」（``OVER_LIMIT``）与「它被判成不相关」
    （``NOT_RELEVANT``）在报告上同形。

    ``OVER_LIMIT`` 是唯一一个**不由** ``status`` 推出的成员：它说的是这一轮的
    上限砍到了它，下一轮（或者更大的上限）它就在名单里。把它与另外几个混在一起，
    读的人会把一次配置截断当成一次业务结论。
    """

    #: ``AccountStatus.HOLD`` —— 有人在等一次裁决。**不是**排除出市场。
    ON_HOLD = "on_hold"
    #: ``AccountStatus.IRRELEVANT`` —— 本 Scene 判过它不相关。
    NOT_RELEVANT = "not_relevant"
    #: ``AccountStatus.DUPLICATE`` —— 它被判成与另一个账号是同一个（§10 的第三类）。
    DUPLICATE = "duplicate"
    #: ``AccountStatus.ARCHIVED`` —— 退役。
    ARCHIVED = "archived"
    #: 它的状态**本来是可研究的**，但不在这一轮选择的状态集里。
    #:
    #: 与上面四个分开，是因为它与 ``OVER_LIMIT`` 同族：**这不是一条业务结论，是这一
    #: 轮的规则**。把它与 ``NOT_RELEVANT`` 合成一个码，读的人会把一次「这一轮我只想
    #: 复核已经研究过的那些」读成「系统判它们不相关」。
    STATUS_NOT_SELECTED = "status_not_selected"
    #: 这一轮的上限砍到了它。**下一轮就在名单里**，只要池子和上限不变。
    OVER_LIMIT = "over_limit"


#: ``status`` → 它**不能**进名单时的原因码。穷举 :data:`WORKABLE_STATUSES` 之外的
#: 每一个 ``AccountStatus`` 成员。
#:
#: 写成一张表而不是一串 ``if``：漏掉一个成员的实现形态是「它被静默丢出名单」，
#: 而报告上那一行只是「没出现在名单里」—— 与「池子里压根没有它」长得一样。
#: ``tests/test_domain_cohort.py`` 拿 ``AccountStatus` 的两个集合一起钉：
#: 每个成员要么在 :data:`WORKABLE_STATUSES` 里、要么在这张表里。
_EXCLUSION_FOR_STATUS: dict[AccountStatus, CohortExclusionCode] = {
    AccountStatus.HOLD: CohortExclusionCode.ON_HOLD,
    AccountStatus.IRRELEVANT: CohortExclusionCode.NOT_RELEVANT,
    AccountStatus.DUPLICATE: CohortExclusionCode.DUPLICATE,
    AccountStatus.ARCHIVED: CohortExclusionCode.ARCHIVED,
}


class CohortSelection(_Strict):
    """形成这套冻结成员的那条规则。**它是这套名单的一半身份。**

    它进 :func:`cohort_id_for` 的原像，所以「同一个池子、同一条规则」算出来的是
    同一个 id（重放不产生第二份名单）；而规则改一个字就是另一套名单，旧的还在盘上
    可读 —— 那正是「每批可回读」这句话要的形状。

    :attr:`limit` 是 ``None`` 表示不封顶。**负数不是一个「很小的上限」**，它是算错
    了，所以在这里就拒（与 ``application/caps.py::apply_cap`` 逐字同一条）——
    静默当成不封顶会让一次配置错误在报告上长得像「没设上限」。
    """

    #: 池子里哪些状态算「这一批可以动」。缺省是 :data:`WORKABLE_STATUSES`。
    #:
    #: 允许调用方给一个**子集**（比如这一轮只想复核 ``RESEARCHED`` 的那些）。
    #: 不允许给空元组之外的东西 —— 空元组是合法的，而且是有意义的：它是一份
    #: 「一个都不研究」的名单，落在报告上是一份**说得出为什么是空的**零结果
    #: （每个账号都有一条 ``status`` 原因码），而不是一次崩溃。
    statuses: tuple[AccountStatus, ...] = WORKABLE_STATUSES
    #: 成员上限。``None`` = 不封顶。
    limit: int | None = None

    @field_validator("statuses")
    @classmethod
    def _statuses_are_unique_and_ordered(
        cls, value: tuple[AccountStatus, ...]
    ) -> tuple[AccountStatus, ...]:
        """去重**并保持给定顺序**。

        重复不清掉的话，``statuses=(ELIGIBLE, ELIGIBLE)`` 与 ``(ELIGIBLE,)`` 会算出
        两个不同的 ``cohort_id``，而它们选出来的是同一批行 —— 于是同一个池子会被冻成
        两份内容一模一样的名单，报告上只显示「有两份名单」。
        """
        seen: set[AccountStatus] = set()
        ordered: list[AccountStatus] = []
        for status in value:
            if status not in seen:
                seen.add(status)
                ordered.append(status)
        return tuple(ordered)

    @field_validator("limit")
    @classmethod
    def _limit_is_not_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError(
                f"上限不能是负数（收到 {value}）。不封顶写成 None —— 一个负数不是一个"
                "「很小的上限」，它是算错了；静默当成不封顶会让一次配置错误变成"
                "「没设上限」，而两者在报告上长得一样。"
            )
        return value


class CohortMember(_Strict):
    """名单里的一行。**它的依据就是那一格 ``status``。**

    这里没有分数、没有权重、没有排序理由 —— 一个「综合评分」会把「它为什么在名单
    里」变成一句没人能复核的话，而池子里今天没有一列算得出它。顺序由池子给（见模块
    docstring 第 1 条），不由这里判。
    """

    account_id: str
    #: 照池子里那一行写的那个名字。**是快照，不是引用** —— 名单冻结的那一刻它是
    #: 什么，这里就记什么。后来的改名不该改写一份已经发出去的名单。
    organisation: str
    #: 它当时凭什么在名单里。这是**读回来的**那一次判定，不是「此刻规则怎么看」。
    status: AccountStatus

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "account_id")


class CohortExclusion(_Strict):
    """池子里有、但**没有**进这一份名单的一行，以及为什么。

    带有 ``organisation`` 与 ``status`` 是刻意的：只有 ``account_id`` 的话，读的人
    要为了「这个 id 是谁」去查一次池子，而池子已经变了 —— 他查到的名字不是当时那个。
    """

    account_id: str
    organisation: str
    status: AccountStatus
    code: CohortExclusionCode
    #: 给人读的那句话。**它是 ``code`` 的渲染，不是第二个判据** ——
    #: :func:`cohort_id_for` 的原像里放的是 ``code``，所以改一句措辞不会让已经冻好的
    #: 名单换一个 id（那会让每一次文案改动都造出一批内容一模一样的新名单）。
    reason: str

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "account_id")


class AccountCohort(_Strict):
    """一个 Scene 手上**某一刻**的成员名单。内容寻址，冻结不可变。

    ## ``cohort_id`` 覆盖到什么、不覆盖到什么

    原像里是：``workspace`` / ``scene_id`` / ``selection`` / 成员的 ``account_id``
    序列 / 每一个被排除者的 ``(account_id, code)``。于是：

    * **重放不产生第二份名单** —— 同一个池子、同一条规则，算出来是同一个 id，
      ``put_cohort`` 是一次覆盖写。这是 §B21「重放事件不重复昂贵工作」在主体这一
      侧的前提。
    * **新增账号会产生另一份名单** —— 成员序列变了，id 就变了，而旧的那一份还在
      盘上读得回来。于是「这一批当时研究的是谁」永远答得出来。
    * **措辞不进原像** —— ``reason`` 那一格改了不会换 id。见 :class:`CohortExclusion`。

    ## ``created_at`` 只往早走

    它是「我们第一次把这份读数冻下来」的时刻，与 ``VenueAsset.first_seen_at`` /
    ``MarketAccount.first_seen_at`` 逐字同一条纪律：重放一份旧名单不该把它推后 ——
    推后之后，一份早就冻好的名单会声称自己是刚冻的，而 §B22 的复用判定正是看这个
    时刻。
    """

    cohort_id: str
    workspace: str
    scene_id: str
    selection: CohortSelection
    members: tuple[CohortMember, ...]
    exclusions: tuple[CohortExclusion, ...] = ()
    created_at: datetime

    @field_validator("cohort_id", "workspace", "scene_id")
    @classmethod
    def _ids_are_canonical(cls, value: str, info: ValidationInfo) -> str:
        return check_canonical_id(value, str(info.field_name))

    @field_validator("created_at")
    @classmethod
    def _created_at_is_a_utc_instant(cls, value: datetime) -> datetime:
        """规范到 UTC，理由逐字见 ``domain/util.py::to_utc``：这个时刻会**参与排序**
        （``list_cohorts`` 的键就是它），而字符串的大小顺序只在偏移量相同时才等于
        瞬间的先后。一个 ``+08:00`` 写法的更晚时刻会排在一个 ``+00:00`` 的更早时刻
        **前面**，于是「哪一份名单是当前的」会答一个更早的 —— 而没有一处会红。
        """
        return to_utc(value)

    @model_validator(mode="after")
    def _the_id_is_the_content(self) -> "AccountCohort":
        """``cohort_id`` 必须**算得出来** —— 与 ``RouteObservation`` 的规范键、
        ``CanonicalContactRoute`` 的 ``canonical_key`` 逐字同一条。

        一个手工指定的 id 会让「同一个池子冻两次」写出两份 id 不同的名单，而它们是
        同一份 —— 于是 §B21 的「重放不重复昂贵工作」在主体这一侧静默失效，报告上
        只显示「有两份名单」。
        """
        expected = cohort_id_for(
            workspace=self.workspace,
            scene_id=self.scene_id,
            selection=self.selection,
            members=self.members,
            exclusions=self.exclusions,
        )
        if self.cohort_id != expected:
            raise ValueError(
                f"cohort_id 是 {self.cohort_id!r}，而按（工作区, 场景, 选择规则, "
                f"成员序列, 排除码）算出来的是 {expected!r}。\n"
                "这个 id 是内容寻址的：同一个池子读两次必须是同一个 id，否则重放会"
                "写出两份内容相同、id 不同的名单，而『重放不重复昂贵工作』在那之后"
                "就不成立了。"
            )
        return self

    @model_validator(mode="after")
    def _a_row_is_in_exactly_one_list(self) -> "AccountCohort":
        """一个账号要么在成员里、要么在排除里，**不许都在、也不许都在两个名单外**。

        两者都在时，读的人无法回答「这一行到底做不做」；而这一格是承重的 ——
        派发侧读成员、报告侧读排除，「既在又在」意味着报告说它被排除了，而工作
        单元里有它。这种不一致不会报错，只会让两份报告各自自洽。
        """
        inside = [member.account_id for member in self.members]
        outside = [row.account_id for row in self.exclusions]
        overlap = sorted(set(inside) & set(outside))
        if overlap:
            raise ValueError(
                f"这些账号同时在成员里和在排除里：{'、'.join(overlap)}。"
                "派发侧读成员、报告侧读排除，两者都在意味着报告说它被排除了，"
                "而工作单元里有它。"
            )
        if len(set(inside)) != len(inside):
            raise ValueError("成员里有重复的 account_id。")
        if len(set(outside)) != len(outside):
            raise ValueError("排除里有重复的 account_id。")
        return self


def cohort_id_for(
    *,
    workspace: str,
    scene_id: str,
    selection: CohortSelection,
    members: tuple[CohortMember, ...],
    exclusions: tuple[CohortExclusion, ...],
) -> str:
    """一份冻结名单的 id —— 内容寻址，所以重放不会写出第二份。

    原像与它的边界写在 :class:`AccountCohort` 的类 docstring 里。序列化用
    ``sort_keys`` + 紧凑分隔符，理由与 ``domain/receipt.py`` 的
    ``dispositions_digest`` / ``route_records.observation_id_for`` 逐字相同：
    默认输出带空格，而同一份值在不同 Python 版本上可能给出不同空白 —— 那时同一个
    池子会算出两个 id，一次重放被读成一份新名单。
    """
    payload = json.dumps(
        {
            "selection": selection.model_dump(mode="json"),
            "members": [member.account_id for member in members],
            # 只有 (账号, 原因码)：**措辞不进原像**（见 CohortExclusion.reason）。
            "excluded": [[row.account_id, row.code.value] for row in exclusions],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (
        f"{COHORT_ID_PREFIX}-"
        + sha256_text(
            SEP.join(
                (
                    check_canonical_id(workspace, "workspace"),
                    check_canonical_id(scene_id, "scene_id"),
                    payload,
                )
            )
        )[:16]
    )


def exclusion_reason(status: AccountStatus, code: CohortExclusionCode) -> str:
    """``code`` 的那句话。**唯一一处定义。**

    抽成函数是为了让「同一个码在两处给出两句不同的话」写不出来 —— 而更实际的理由
    是它让 :func:`CohortExclusion` 的 ``reason`` 变成 ``code`` 的一个纯函数，测试
    因此可以断言两者对得上，而不是断言一段文案。
    """
    return {
        CohortExclusionCode.ON_HOLD: (
            f"账号状态是 {status.value}：有人在它身上等一次裁决。这不是「排除出市场」，"
            "裁决下来之后它会自己回到名单里。"
        ),
        CohortExclusionCode.NOT_RELEVANT: (
            f"账号状态是 {status.value}：本 Scene 判过它不相关。"
        ),
        CohortExclusionCode.DUPLICATE: (
            f"账号状态是 {status.value}：它被判成与池子里另一个账号是同一个。"
        ),
        CohortExclusionCode.ARCHIVED: f"账号状态是 {status.value}：已退役。",
        CohortExclusionCode.STATUS_NOT_SELECTED: (
            f"账号状态是 {status.value}：它能研究，但不在这一轮选择的状态集里。"
            "这不是一条排除结论 —— 换一条选择规则它就回来了。"
        ),
        CohortExclusionCode.OVER_LIMIT: (
            "这一轮的上限砍到了它（名单按 account_id 升序，砍的是尾部）。"
            "下一轮只要池子与上限不变，它就在名单里 —— 这不是一条业务结论。"
        ),
    }[code]


def freeze_cohort(
    accounts: tuple[MarketAccount, ...],
    *,
    workspace: str,
    scene_id: str,
    selection: CohortSelection,
    at: datetime,
) -> AccountCohort:
    """池子里的那几行 + 一条规则 → 一份冻结名单。**纯函数：不读盘、不写盘、不碰时钟。**

    这是「池子 → 名单」这一段里唯一的一步。它按
    :meth:`AccountPoolStore.list_accounts` 给的顺序读（那一处已经按 ``account_id``
    稳定排序，见模块 docstring 第 1 条），逐个判，再截断。

    Args:
        accounts: 本工作区**全部**的账号行，已按 ``account_id`` 排序。
        workspace / scene_id: 这份名单属于谁。``accounts`` 是调用方给的，所以这两个
            是参数而不是从行里读 —— 一行属于别的工作区时那是调用方拿错了，
            由存储那一侧（``bind_workspace``）当场拒。
        selection: 那一条规则。见 :class:`CohortSelection`。
        at: 「我们何时读的这一次」。**由调用方给**，与 ``assets_from_receipt`` 的
            ``seen_at`` 逐字同一条：一条要能被逐条断言的规则不该自己去问现在几点。
    """
    workable = set(selection.statuses)
    members: list[CohortMember] = []
    exclusions: list[CohortExclusion] = []

    for account in accounts:
        if account.status not in workable:
            # 两张表各有各的意思：``_EXCLUSION_FOR_STATUS`` 说「这个状态本身就不能
            # 进名单」，缺省那一支说「它能进，只是这一轮没选它」。合成一句会让
            # 「系统判它不相关」与「这一轮我只想复核已经研究过的」在报告上同形。
            code = _EXCLUSION_FOR_STATUS.get(
                account.status, CohortExclusionCode.STATUS_NOT_SELECTED
            )
            exclusions.append(
                CohortExclusion(
                    account_id=account.account_id,
                    organisation=account.organisation,
                    status=account.status,
                    code=code,
                    reason=exclusion_reason(account.status, code),
                )
            )
            continue
        members.append(
            CohortMember(
                account_id=account.account_id,
                organisation=account.organisation,
                status=account.status,
            )
        )

    kept, dropped = _capped(members, selection.limit)
    for member in dropped:
        exclusions.append(
            CohortExclusion(
                account_id=member.account_id,
                organisation=member.organisation,
                status=member.status,
                code=CohortExclusionCode.OVER_LIMIT,
                reason=exclusion_reason(member.status, CohortExclusionCode.OVER_LIMIT),
            )
        )

    frozen = tuple(kept)
    outside = tuple(exclusions)
    return AccountCohort(
        cohort_id=cohort_id_for(
            workspace=workspace,
            scene_id=scene_id,
            selection=selection,
            members=frozen,
            exclusions=outside,
        ),
        workspace=workspace,
        scene_id=scene_id,
        selection=selection,
        members=frozen,
        exclusions=outside,
        created_at=at,
    )


def _capped(
    members: list[CohortMember], limit: int | None
) -> tuple[list[CohortMember], list[CohortMember]]:
    """按上限切。``None`` 就是不切（``application/caps.py::apply_cap`` 那一支的
    同一句话，而这里不能调它 —— 它是应用层的类型，域层不依赖应用层）。"""
    if limit is None:
        return list(members), []
    return members[:limit], members[limit:]

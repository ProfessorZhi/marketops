"""路线记录的**两层**：不可变的历史，与一个账号-邮箱一行的当前读法。

## 这一层要修的是什么

在此之前，``contact_route`` 表是一条**事件流**：每一个生产者跑一次就往里追加一批行，
``list_routes(account_id)`` 把历来写过的**全部**行原样返回。于是：

* 同一个 (账号, 邮箱) 被研究三遍，读的人看到三条路线 —— 而它不是三条路线，
  是同一条路线被看了三次；
* 「这条路线现在是什么等级」在盘上**没有一格可以回答**，只有一串按写入顺序排的行，
  而「最后一行」既不是定义也不是事实（两次结算的写入顺序由调度决定）；
* SOP 的规范键（账户 ID + 产品场景 + 小写邮箱）在盘上不存在，所以「去重」这件事
  落在每一个读者身上 —— 而读者各自实现一遍的那一天，两个读者对不同。

所以这里把两件事分开：

:class:`RouteObservation`
    **一次观察**。不可变、只追加。它就是 ``bind`` + ``judge`` + ``grade`` 那一趟的
    产出，加上「这是哪一次看的」。同一个邮箱被看两遍就有两条观察，而这两条**都是真的**
    —— 它们说的是两个不同时刻的事实，历史不该被压成一条。

:class:`CanonicalContactRoute`
    **当前读法**。每个规范键一行，由 :func:`fold_observation` 折出来。它回答的是
    「这个账号的这个邮箱，**现在**算哪一级、依据是什么」。

## 规范键取哪几格，以及为什么是 ``workspace``

SOP 的原文是「以账户 ID + 产品场景 + 小写邮箱为基础键」，本仓把「产品场景」这一维
叫 ``workspace`` —— 与 :attr:`~marketops.domain.evidence.Evidence.workspace` 逐字同一个
词、同一个理由（工作区是授权链的第一段，见那一格的 docstring）。这里**不新造一个
``scene_id`` 字段**：路线与机会上面都没有、也不该有 Scene 那一维，而一个永远等于
``workspace`` 的字段只是把同一个东西写成两处。

## 小写邮箱：**只**小写，不做 Gmail 式的点号与加号折叠

``a.b@x.co.uk`` / ``ab@x.co.uk`` 是**两个**不同的邮箱（点号只在 Gmail 那一家是别名，
在 RFC 5321 下是本地部分的一个普通字符），``a+tag@x.co.uk`` 同理。把它们折成一个键
是一次**静默合并**：两个不同的收件人变成一条路线，而报告上只显示「去重掉了几条」。

所以 :func:`route_email_key` 只做 SOP 写的那一件事 —— ``normalise_email``（去空白、
域名小写）之后**再整体小写**。整体小写与 ``normalise_email`` 刻意保留本地部分大小写
的处置不矛盾，它们是两个问题的两个答案：**键**要按 SOP 合并 ``Sales@x`` 与 ``sales@x``；
**存下来的那一格**（``ContactRoute.email``）保持页面上看到的原样，因为它是「这一页
写了什么」的证据，而一个按我们的规则改写过的收件人地址不是证据。

## 折叠**不是**取最高等级

这是这一层最容易写错的一条。``D + X + X`` 折出来**不是** ``D``：

* 取最高等级会让一条**旧的、看着更好**的读法在它的证据早就变了之后继续活着 ——
  而报告上它看起来完全正常（一条 D 级路线，无可指摘）。本仓对「未确认的事实不得决定
  任何子句」的那条纪律，在这里的模样就是它的反面；
* 当前读法取**最新那条观察**的读法，因为一条路线是关于世界的断言，「现在是什么」
  只能由**最近一次看**回答。更早的那些读法一条都没丢 —— 它们在观察表里，
  ``observation_ids`` 逐条指着它们。

时间相等时按 ``observation_id`` 定序（内容寻址，因此跨重放稳定）。这条定序是
**规则的定序**，不是关于世界的断言：它存在的唯一目的是让两个 worker 以任意顺序交上
两条同一瞬间的观察时，盘上落下来的那一行与提交顺序**无关**。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from .base import StrictModel as _Strict
from .entities import ContactRoute
from .enums import ContactField, Grade, SourceKind
from .util import check_canonical_id, normalise_email, sha256_text

#: 规范键的派生分隔符。与 ``domain/job.py`` 的 ``_derive`` 同一族。
SEP = "\x1f"

#: 规范键的前缀。它同时是一个**文件名片段**（存档那一侧按它分片）。
CANONICAL_ROUTE_PREFIX = "route"

#: 一条被拒的候选邮箱，**为什么**被拒。闭集 —— §6.3 要求至少分得出这几类，
#: 而一个开着口子的字符串字段迟早会长出「别的写法」。
class RouteRejectionCode(str, Enum):
    """拒收的类别。取值与 ``domain/bind.py`` 里那几个 ``REASON_*`` 常量逐字相同。

    它们是**结论**，不是错误码：``foreign_domain`` 说的是「这个邮箱不是这个账号的」，
    ``unbound_email`` 说的是「还看不出它是谁的」。两者的处置不同（前者到此为止，
    后者要回那一页找那张卡片），所以它们必须是两个值。
    """

    #: 邮箱域与**已证得**的账号域名不符（SOP L121）。
    FOREIGN_DOMAIN = "foreign_domain"
    #: 这一页上**有**具名/岗位，但没有一条落在这条邮箱的条目窗口里（§9）。
    ENTRY_ELSEWHERE = "entry_elsewhere"
    #: 条目窗口里没有唯一的一处岗位或姓名，也不是可识别的业务入口。
    UNBOUND_EMAIL = "unbound_email"
    #: 来源本身不合格（SERP snippet 一类，SOP L119）。
    INELIGIBLE_SOURCE = "ineligible_source"
    #: 这一页上有**多个**邮箱，同时还有具名/岗位 —— 无法判断谁是谁。
    #: 与 :attr:`ENTRY_ELSEWHERE` 分开：那一句说的是「人在别处」，而这里人在**同一页**
    #: 上，只是配不上号。两条不同的实话，处置也不同。
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    #: 命中的字面值是空的 —— 它不是一条候选，是一条坏行。
    EMPTY_VALUE = "empty_value"


#: 一张收据上属于**改写账**的那几格。
#:
#: 同一张收据（同一个 ``rejection_id``）被再写一次时，这几格换成**新的值**；
#: 其余每一格都是这张收据的**身份**，两次写入必须逐位相同 —— 不同就说明 id 是被
#: 手工指定的，而不是由 :func:`rejection_id_for` 算出来的。
#:
#: 为什么偏偏是这三格：
#:
#: * ``run_id`` / ``work_item_id`` 回答「这是哪一趟跑的」。同一次结算重跑一次、
#:   两个 worker 同时跑同一批、维护轮次重看同一页 —— 都是**同一件事被做了多遍**，
#:   收据只有一张，上面留的是最近那趟。
#: * ``rule`` 是**判它时用的那条规则**（SOP 的条款号 / policy 版本）。它不在
#:   ``rejection_id`` 的原像里，所以换了策略版本重判同一份材料算出的仍是同一张
#:   收据 —— 那也正是「同一份材料今天是这个理由被拒」这句实话的样子。
#:
#: 反过来说：``code`` **不是**改写账。``foreign_domain`` 变成 ``unbound_email``
#: 是**两张收据**（原像里有 ``code``），因为它们的处置不同 —— 一张到此为止，
#: 一张要回那一页找那张卡片。
REJECTION_BOOKKEEPING_FIELDS: tuple[str, ...] = ("run_id", "work_item_id", "rule")


def identity_mismatch(stored: "RouteRejection", incoming: "RouteRejection") -> tuple[str, ...]:
    """同一 ``rejection_id`` 的两张收据在**身份**上不一致的字段名。空元组 = 同一张。

    判定留在这一层，三个存储实现各自只负责把差异**说成自己的那句话**（SQLite 说
    「已有 rejection_id=… 的拒绝记录」，JSONL 说「第 N 行」，内存替身说它手上那一份）
    —— 一份判据写在三个适配器里，就是让它们各自回答一遍同一个业务问题，而它们会
    给出三个答案（``ports/store.py`` 里 ``record_route`` 那一段记的是同一件事）。

    返回**字段名**而不是一个布尔：一条只说「不一样」的拒绝，在排障时等于什么都没说
    —— 人要知道的是「哪一格被改写了」。
    """
    left, right = stored.identity(), incoming.identity()
    return tuple(sorted(name for name, value in left.items() if right.get(name) != value))


def route_email_key(email: str) -> str:
    """规范键里的那一半邮箱。**只小写，不折点号、不折加号。**

    理由见模块 docstring：点号与加号折叠会把两个不同的收件人合成一条路线，
    而合并之后**没有任何一处**留得下「这里合并过」。
    """
    return normalise_email(email).lower()


def canonical_route_key(*, workspace: str, account_id: str, email: str) -> str:
    """SOP 的规范键：``(工作区, 账号, 小写邮箱)`` → ``route-<16 位十六进制>``。

    三个分量都**必须**参与，各自挡掉一种合并：

    * 少了 ``workspace`` —— 两个部署关于同一个账号的路线会互相覆盖；
    * 少了 ``account_id`` —— 一个共享域下两个组织的同一条 ``info@`` 会变成一条；
    * ``email`` 不做 :func:`route_email_key` —— ``Sales@`` 与 ``sales@`` 是两条。

    返回值是内容寻址的，所以它同时是**幂等键**：同一次观察重放一次，算出来的是同一个
    键，写入是一次覆盖而不是多一行。
    """
    return f"{CANONICAL_ROUTE_PREFIX}-{sha256_text(SEP.join((check_canonical_id(workspace, 'workspace'), check_canonical_id(account_id, 'account_id'), route_email_key(email))))[:16]}"


def observation_id_for(
    *,
    workspace: str,
    account_id: str,
    canonical_key: str,
    observed_at: datetime,
    route: ContactRoute,
) -> str:
    """一条观察的 id —— 内容寻址，所以重放不会写出第二条。

    原像里**没有**「这一趟是谁跑的」（run / work item）：那是**记账**，不是这一条观察
    的内容。把它算进去的话，同一次结算重跑一次会得到两条 id 不同的观察，而它们是
    同一件事 —— 于是「重放幂等」这句话在这一层就不成立了。

    原像里有 ``route.model_dump()``（含 ``evidence_ids`` 与等级）：两条只在等级上不同的
    观察是**两条观察**，因为它们说的是两件不同的事。

    序列化用 ``sort_keys`` + 紧凑分隔符，理由与 ``domain/receipt.py`` 的
    ``dispositions_digest`` 逐字相同：默认输出带空格，而同一份值在不同 Python 版本上
    可能给出不同空白 —— 那时同一条观察会算出两个 id，一次重放被读成一条新观察。
    """
    payload = json.dumps(
        route.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        "obs-"
        + sha256_text(
            SEP.join(
                (
                    workspace,
                    account_id,
                    canonical_key,
                    observed_at.isoformat(),
                    payload,
                )
            )
        )[:16]
    )


def rejection_id_for(
    *,
    workspace: str,
    account_id: str,
    code: RouteRejectionCode,
    candidate: str,
    source_url: str,
    occurred_at: datetime,
) -> str:
    """一条拒收的 id —— 同样内容寻址，所以重放不会写出第二条。

    ``source_url`` 与 ``occurred_at`` 都在原像里，与观察那一条**相反**，而这正是它们
    该有的差别：一条拒收说的不是「世界上发生了什么」，而是「**这一趟在这一页上**没有
    收下这个候选」。同一页重看一次是同一件事（幂等）；两页各拒一次是两条（这一页、
    那一页，各自可以去看）。

    参数叫 ``candidate`` 而不是 ``email``：``ineligible_source`` 那一类拒的是**任意
    字段**上的字面值（见 ``domain/bind.py`` 的 ``attempted``），把它叫成邮箱就等于在
    收据上替一条姓名捏一个邮箱。键化仍然用 :func:`route_email_key` —— 它不是邮箱时
    ``normalise_email`` 退化为「去空白、去尾标点」，而这一层要的只是**同样两趟跑算出
    同样的 id**，不是「它一定是个邮箱」。
    """
    return (
        "rej-"
        + sha256_text(
            SEP.join(
                (
                    workspace,
                    account_id,
                    code.value,
                    route_email_key(candidate),
                    source_url,
                    occurred_at.isoformat(),
                )
            )
        )[:16]
    )


class RouteObservation(_Strict):
    """**一次**观察：某一刻我们在这个账号上看到了这条路线。

    它是不可变的历史。同一个 (账号, 邮箱) 被看两遍就是两条观察，而两条都对 ——
    它们说的是两个时刻。把它折成一条是 :class:`CanonicalContactRoute` 的事，
    不是这一层的事。

    :attr:`route` 装的就是 ``bind`` + ``judge`` + ``grade`` 的产出（``ContactRoute``），
    一个字都不多：这一层不重新解释它，只给它加上「这是哪一次看的」。
    """

    observation_id: str
    canonical_key: str
    workspace: str
    account_id: str
    #: 我们**看到这一页**的时刻 —— 取自材料的 ``fetched_at``，**不是**这一趟跑的墙钟
    #: 时刻。用后者会让「重跑一次旧材料」看起来比一次真的新观察更近，而那正好会
    #: 把新读法覆盖掉。
    observed_at: datetime
    route: ContactRoute

    @field_validator("observation_id")
    @classmethod
    def _observation_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "observation_id")

    @field_validator("canonical_key")
    @classmethod
    def _key_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "canonical_key")

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "account_id")

    @field_validator("observed_at")
    @classmethod
    def _observed_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("RouteObservation.observed_at 必须是带时区的时刻。")
        return value

    @model_validator(mode="after")
    def _the_key_route_and_owner_are_one_story(self) -> "RouteObservation":
        """四格必须说的是**同一件事**，逐条钉死。

        分开校验不够：一条 ``workspace`` 属于甲的观察、``route`` 属于乙，是「跨工作区
        读」在盘上的形状 —— 而那正是 ``Evidence.workspace`` 那一整套判据要挡的东西。
        键与 route 对不上同理：它会让**另一条路线**的投影按这一条的等级被改写。
        """
        if self.route.workspace != self.workspace:
            raise ValueError(
                f"观察的工作区是 {self.workspace!r}，而它装的路线说自己是"
                f" {self.route.workspace!r} —— 两者必须是同一个。"
            )
        if self.route.account_id != self.account_id:
            raise ValueError(
                f"观察的账号是 {self.account_id!r}，而它装的路线属于"
                f" {self.route.account_id!r}。"
            )
        expected = canonical_route_key(
            workspace=self.workspace, account_id=self.account_id, email=self.route.email
        )
        if self.canonical_key != expected:
            raise ValueError(
                f"观察的规范键是 {self.canonical_key!r}，而按（工作区, 账号, 邮箱）"
                f"算出来的是 {expected!r}：它会折进**另一条路线**的投影。"
            )
        return self


class CanonicalContactRoute(_Strict):
    """**当前读法**：一个规范键一行。

    它回答的是「这个账号的这个邮箱，现在算哪一级、依据是什么」。字段分两半：
    :attr:`route` 是**事实**（与观察里那一格同型），另外几格是**这份投影自己的账**
    （折了几次、上一次的等级是什么、上一次是谁）。

    :attr:`previous_grade` 在这里不是装饰：等级从 A 掉到 D 与一直是 D 在**动作**上
    完全不同（前者要去看那一页改了什么），而只留当前值会让两件事在报告上同形。
    它是 ``None`` 当且仅当只有一条观察 —— 「没有上一个」与「上一个也是 D」是两句话。

    ## 哪几格是「这个键的事实」，哪几格是「这一份投影的账」

    这个区分是承重的，因为两者的**稳定性不同**：

    * :attr:`route` / :attr:`email_key` / :attr:`first_observed_at` /
      :attr:`last_observed_at` / :attr:`last_observation_id` 由**那批观察的集合**
      决定 —— 与到达顺序无关。两个 worker 以任意顺序提交同一批观察，落下来的
      **当前读法逐位相同**（见 :func:`fold_observation`）。
    * :attr:`revision` / :attr:`observation_ids` / :attr:`previous_grade` 记的是
      **这个存储改写了几次**。它们与到达顺序**有关**：``{A@9, B@12}`` 按时间到达
      是两版、倒着到达是一版，当前读法却都是 B@12 那一条。

    这不是缺陷，是一句关于服务器的真话 —— 但把前者当后者用就会出错。要回答
    「这条路线被改过几次」用历史（``list_route_observations``，它与顺序无关地装着
    全部观察）；要回答「现在是什么」用这里。**报告里那句「掉级了」应该读历史**，
    而不是读 :attr:`previous_grade` —— 后者只在观察恰好按时间到达时才等于「上一次
    看到的等级」。
    """

    canonical_key: str
    workspace: str
    account_id: str
    #: 键的那一半：:func:`route_email_key` 的产物。
    email_key: str
    #: 这份投影被改写过几次。**不是**「折进来几条观察」—— 见类 docstring 那一节。
    revision: int = Field(ge=1)
    #: 改写过它的那几条观察（按提交先后）。**不是**历史的全集。
    observation_ids: tuple[str, ...]
    first_observed_at: datetime
    last_observed_at: datetime
    #: 定序的第二格。见 :func:`fold_observation`。
    last_observation_id: str
    #: 上一次改写**换掉**的那个等级。见类 docstring 那一节 —— 它是改写账，不是
    #: 「上一次观察的等级」。
    previous_grade: Grade | None = None
    route: ContactRoute
    #: 这份投影**最后一次被改写**的时刻。取值是**那一条观察的** ``observed_at``，
    #: 不是折叠那一刻的墙钟 —— 用墙钟的话，两个 worker 以不同顺序提交同一批观察会
    #: 得到两份 ``updated_at`` 不同的 payload，于是「当前读法与到达顺序无关」这句话
    #: 在字节层就不成立了（见类 docstring 那一节）。它因此等于
    #: :attr:`last_observed_at`，而这两格留着各自的理由：一格说「这一份是什么时候的
    #: 读法」，另一格说「这一份是什么时候写下去的」。
    updated_at: datetime

    @field_validator("canonical_key", "last_observation_id")
    @classmethod
    def _ids_are_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "id")

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "account_id")

    @field_validator("observation_ids")
    @classmethod
    def _observation_ids_are_canonical(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError(
                "CanonicalContactRoute.observation_ids 不能为空：一条当前读法必然来自"
                "至少一次观察，而空的清单读起来像「读过、什么都没有」。"
            )
        seen: set[str] = set()
        for item in value:
            check_canonical_id(item, "observation_id")
            if item in seen:
                raise ValueError(f"observation_ids 里有重复项 {item!r} —— 计数会因此偏大。")
            seen.add(item)
        return value

    @field_validator("first_observed_at", "last_observed_at", "updated_at")
    @classmethod
    def _moments_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("CanonicalContactRoute 的时刻必须带时区。")
        return value

    @model_validator(mode="after")
    def _the_projection_is_one_story(self) -> "CanonicalContactRoute":
        if self.last_observed_at < self.first_observed_at:
            raise ValueError("last_observed_at 早于 first_observed_at。")
        if self.last_observation_id not in self.observation_ids:
            raise ValueError(
                f"last_observation_id={self.last_observation_id!r} 不在 observation_ids 里"
                " —— 当前读法必须来自清单里的某一条观察。"
            )
        if self.revision != len(self.observation_ids):
            raise ValueError(
                f"revision={self.revision} 与 observation_ids 的条数"
                f"（{len(self.observation_ids)}）对不上：前者就是后者的长度。"
            )
        if self.route.workspace != self.workspace or self.route.account_id != self.account_id:
            raise ValueError(
                "当前读法那一行与这份投影的工作区/账号对不上 —— 它会被读成另一个账号的路线。"
            )
        expected_key = canonical_route_key(
            workspace=self.workspace, account_id=self.account_id, email=self.route.email
        )
        if self.canonical_key != expected_key:
            raise ValueError(
                f"投影的规范键是 {self.canonical_key!r}，而它装的路线按（工作区, 账号, "
                f"邮箱）算出来是 {expected_key!r}。"
            )
        if self.email_key != route_email_key(self.route.email):
            raise ValueError(
                f"email_key={self.email_key!r} 与 route.email 的键形式"
                f"（{route_email_key(self.route.email)!r}）不一致。"
            )
        return self


@dataclass(frozen=True)
class FoldResult:
    """一次折叠的账。**改写没改写都要说得出来** —— 这是幂等那条要求的执行器。

    ``applied=False`` 有两种成因，而它们在这一层分得开：这一条观察**已经在**清单里
    （重放），或者它**比当前读法旧**（迟到的写）。两者的处置相同（什么都不做），
    但报告里那句话不同，所以 ``reason`` 逐字写出来。
    """

    projection: CanonicalContactRoute
    applied: bool
    reason: str = ""


def fold_observation(
    current: CanonicalContactRoute | None,
    observation: RouteObservation,
    *,
    at: datetime,
) -> FoldResult:
    """把一条观察折进当前读法。**顺序无关、重放幂等。**

    三条规则，逐条都有它挡的那件事：

    1. **已经在清单里 ⇒ 什么都不做**（``applied=False``）。重放一次结算、两个 worker
       同时交了同一条观察 —— 都是一件事，而多折一次会让 ``revision`` 涨一格。
    2. **比当前读法旧 ⇒ 什么都不做**。两条观察谁新由 ``(observed_at,
       observation_id)`` 定序：第一格是我们**看到那一页**的时刻，第二格只在同一瞬间
       的比较里起作用（内容寻址，所以跨重放稳定）。这一格是并发安全的那一半 ——
       两个 worker 以任意顺序提交时，落下来的那一行与提交顺序无关。
    3. **否则折进来**：当前读法换成这一条观察的读法，``previous_grade`` 记下**被换掉
       的那一个**，清单追加这条 id。

    **不作 max。** ``D + X + X`` 折出来是最后那一条的读法，不是 D —— 理由写在模块
    docstring 里：取最高会让一条旧的、看着更好的读法在证据早就变了之后继续活着。

    ## 顺序无关的**是读数**，不是这几格账

    规则 1 与 2 一起让**当前读法**成为一个纯函数：它是这批观察里按
    ``(observed_at, observation_id)`` 最大的那一条。所以两个 worker 以任意顺序
    提交同一批观察，最终 ``route`` / ``last_observed_at`` 逐位相同 —— §6.2 要的
    并发安全就是这一条。

    ``revision`` / ``previous_grade`` **不**具备这个性质，因为它们记的是改写过程：
    ``{A@9, B@12}`` 正着到达是两版、倒着到达是一版。这一格是**故意**留着的
    （「这条路线在这个库里被改过几次」是一个真的、有人要问的问题），但它与
    「这批观察说了什么」是两件事，混用会出错 —— 见
    :class:`CanonicalContactRoute` 的 docstring 那一节。
    """
    if current is None:
        route = observation.route
        return FoldResult(
            projection=CanonicalContactRoute(
                canonical_key=observation.canonical_key,
                workspace=observation.workspace,
                account_id=observation.account_id,
                email_key=route_email_key(route.email),
                revision=1,
                observation_ids=(observation.observation_id,),
                first_observed_at=observation.observed_at,
                last_observed_at=observation.observed_at,
                last_observation_id=observation.observation_id,
                previous_grade=None,
                route=route,
                updated_at=at,
            ),
            applied=True,
        )

    if observation.observation_id in current.observation_ids:
        return FoldResult(
            projection=current,
            applied=False,
            reason=(
                f"观察 {observation.observation_id} 已经在这条路线里了 —— 重放，"
                "当前读法一个字都没改。"
            ),
        )

    incoming = (observation.observed_at, observation.observation_id)
    stored = (current.last_observed_at, current.last_observation_id)
    if incoming < stored:
        return FoldResult(
            projection=current,
            applied=False,
            reason=(
                f"这一条观察（{observation.observed_at.isoformat()}）比盘上那条"
                f"（{current.last_observed_at.isoformat()}）旧 —— 迟到的写不覆盖"
                "当前读法，它仍然进历史。"
            ),
        )

    return FoldResult(
        projection=CanonicalContactRoute(
            canonical_key=current.canonical_key,
            workspace=current.workspace,
            account_id=current.account_id,
            email_key=current.email_key,
            revision=current.revision + 1,
            observation_ids=(*current.observation_ids, observation.observation_id),
            first_observed_at=current.first_observed_at,
            last_observed_at=observation.observed_at,
            last_observation_id=observation.observation_id,
            # 被换掉的那一个。**不是**「上一条观察的等级」：上一格里可能已经是
            # 同一个值（连续两次都判 D），那时记的不是变化而是重复 —— 而这一格
            # 存在的全部意义就是让「掉级了」在报告里看得出来。
            previous_grade=current.route.grade,
            route=observation.route,
            updated_at=at,
        ),
        applied=True,
    )


class RouteRejection(_Strict):
    """一条**被拒的候选邮箱**的完整回执（§6.3）。

    它比 ``domain/bind.py`` 的 ``RejectedBinding`` 多出来的只有**记账**那几格：
    ``rejection_id`` / ``run_id`` / ``work_item_id`` / ``rule`` / ``occurred_at``。
    绑定那一层不知道本次是谁在跑，也不该知道 —— 那是调用点的上下文。

    ## 为什么必须有这一层，而不是把拒收数一下就丢

    在此之前「这一轮拒了 37 条」是唯一读得出来的东西，而 37 条拒收里可能有 30 条是
    「这一页没有联系人」（正常），3 条是「域名判错了」（要修），4 条是「有人把另一家
    组织的邮箱抄了过来」（要修 SOP 之外的接线）。同一个数字，三种处置。

    所以每一格都承重：``account_id`` / ``candidate`` / ``field`` 说的是**哪一条候选**，
    ``code`` 说的是**哪一类**，``evidence_ids`` 说明**依据还在**，``rule`` 指向
    **哪一条规则**（SOP 的哪一句 / 哪一版 policy），``run_id`` / ``work_item_id``
    回答「这是哪一趟跑的」，``occurred_at`` 回答「什么时候」。

    ## 为什么是 ``candidate`` 加 ``field``，而不是一格 ``email``

    §6.3 要的是「候选邮箱」，而**五类里有两类拒的不是邮箱**：``ineligible_source``
    拒的是**任意字段**上的字面值（``domain/bind.py`` 的 ``attempted`` 把姓名、岗位、
    邮箱一起交上来），``empty_value`` 拒的是一条坏行 —— 它的字面值按定义是空的，
    塞进一格叫 ``email`` 的字段就等于在收据上写「拒了一个空邮箱」，而事实是
    「这一格没有东西可拒」。

    一格 ``email: str`` 会把这两类逼成两种坏写法：要么捏一个邮箱，要么在报告里把它
    写成空串（读起来像「没有候选」，而它是「有一条不合格的候选」）。所以候选那一格
    按**它本来的样子**存（原文，可能是姓名、岗位、空串），它**是哪一列**由 ``field``
    回答。判定与去重仍然走键形式（见 :func:`rejection_id_for`）。

    ## 身份与改写账

    这张收据**不是**只追加的历史（那是 :class:`RouteObservation`），它是「这一条候选
    在本库里被拒过」的**唯一一行**。于是同一个 ``rejection_id`` 被写第二次时，两份
    内容必然在**记账**那几格上不同（``run_id`` 换了）—— 见
    :data:`REJECTION_BOOKKEEPING_FIELDS`。第二次写入因此是**覆盖**：身份那几格必须
    逐位相同（:meth:`identity`，判据是 :func:`identity_mismatch`），记账那几格换上
    新值。

    这与 :class:`CanonicalContactRoute` 的 ``revision`` / ``previous_grade`` 是同一条
    纪律：账可以改写，事实不行。**幂等那句话要的是「再算一遍不会多出一行」，不是
    「再算一遍会抛异常」** —— 抛异常的那一版会让重试、两个 worker、维护轮次各自
    撞上一个 ``EvidenceError``，而那看起来像存储坏了。
    """

    rejection_id: str
    workspace: str
    account_id: str
    #: 候选字面值**原文**（页面上看到的样子）。判定用它的键形式，报告用原文。
    #: 可能是邮箱、姓名、岗位，甚至是空串（``empty_value`` 那一类，见类 docstring）。
    candidate: str
    #: 这个字面值是从哪一列抽出来的。列表里没有它时是 ``None`` ——
    #: 「不知道它从哪来」与「它是邮箱」是两句不同的话。
    field: ContactField | None = None
    code: RouteRejectionCode
    #: 判据原文（不含类别前缀）—— 不是替它编的一句。
    detail: str
    #: 依据的那条规则。取得到时是 SOP 的条款号或 policy 版本，取不到是空串
    #: （空串是**没有**，不是「没有规则」）。
    rule: str = ""
    source_kind: SourceKind | None = None
    source_url: str = ""
    evidence_ids: tuple[str, ...] = ()
    run_id: str = ""
    work_item_id: str = ""
    occurred_at: datetime

    @property
    def reason(self) -> str:
        """人读的那一句（与 ``RejectedBinding.reason`` 同形）。"""
        return f"{self.code.value} —— {self.detail}" if self.detail else self.code.value

    def identity(self) -> dict[str, Any]:
        """这张收据的**身份**：除了 :data:`REJECTION_BOOKKEEPING_FIELDS` 之外的每一格。

        这就是「同一个 ``rejection_id`` 的两次写入是不是同一张收据」的判据 ——
        存储实现拿它来比对（见 :func:`identity_mismatch`），不自己挑字段：哪几格算
        身份是一个**业务判定**，让三个适配器各挑一遍就是让它们各自回答一遍。
        """
        return self.model_dump(mode="json", exclude=set(REJECTION_BOOKKEEPING_FIELDS))

    @field_validator("rejection_id")
    @classmethod
    def _rejection_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "rejection_id")

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "account_id")

    @model_validator(mode="after")
    def _blank_candidate_means_empty_value(self) -> "RouteRejection":
        """空的候选**只**允许出现在 ``empty_value`` 这一类上，反之亦然。

        这条是双向的，而两个方向各自挡掉一种误读：

        * 候选为空、类别却是别的 —— 收据上会是一行「foreign_domain 拒了 ``''``」，
          而真正发生的事是这一格没抽出东西来。两类拒收的处置不同（一类要回那一页
          重抽，一类要去看域名），所以这不是措辞问题；
        * 类别是 ``empty_value``、候选却非空 —— 那一类的定义就是「命中的字面值是空
          的」，非空的话它属于别的类别。

        今天三条抽取器（``extract_emails`` / ``extract_labelled_names`` /
        ``extract_roles``）都不会产出空字面值，所以这一格在生产上**取不到**空串：
        它挡的是「以后有人加了一条会产出空字面值的抽取器」，那时错的是类别，不是
        数据 —— 让它在这里炸，比让一条错分类的行静静地进报表好（§19：更少的真实
        结果可以，更多错误结果不可以）。
        """
        blank = not self.candidate.strip()
        if blank != (self.code is RouteRejectionCode.EMPTY_VALUE):
            raise ValueError(
                f"RouteRejection 的候选取值与类别对不上：candidate={self.candidate!r}、"
                f"code={self.code.value!r}。空的候选只属于 empty_value，而 empty_value "
                "的候选必须是空的 —— 见这一格 validator 的 docstring。"
            )
        return self

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("RouteRejection.occurred_at 必须带时区。")
        return value


__all__ = [
    "CANONICAL_ROUTE_PREFIX",
    "REJECTION_BOOKKEEPING_FIELDS",
    "CanonicalContactRoute",
    "FoldResult",
    "RouteObservation",
    "RouteRejection",
    "RouteRejectionCode",
    "canonical_route_key",
    "fold_observation",
    "identity_mismatch",
    "observation_id_for",
    "rejection_id_for",
    "route_email_key",
]

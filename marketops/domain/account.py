"""市场里的**一行账号** —— 一个 Scene 手上的账号池里的一行。

这是账号这一族的**第三个**名词，三个各有各的载具，谁都不替谁：

* :class:`~marketops.domain.evidence.AccountRecord` —— **一次研究作业的输入清单里
  的一行**。它的 ``cohort`` 是闭集且必填（``known_success`` / …），因为那是 §10
  的三类账号，由 operator 维护的 YAML 提供。
* :class:`~marketops.domain.candidate.AccountCandidate` —— **发现期的一行**。它
  没有 ``cohort``，也没有 ``account_id``：发现出来的东西还不是账号。
* 本模型 —— **这个市场现在有哪些账号**。它由研究写进来（不是由 YAML 装载），
  有血统、有证据、有新鲜度，而**同一个组织在第二个 Scene 下是第二行**。

## §6 清单里的「关联场地」与「责任关系」不在这一行上

§6 要求这一行至少答得出：``account_id`` / ``workspace`` / ``organisation`` /
``aliases`` / ``domains`` / ``addresses`` / ``country`` / 关联场地 / 责任关系 /
证据 / 新鲜度 / 状态。前面那些在这里是**字段**；**「关联场地」与「责任关系」不是**
—— 它们由 §5 的关系表回答（``AccountVenueRelationStore.relations_for_account``），
读账号池的那条路径把它接上。

存两遍就是同一句话的两个定义处：``这个账号管着哪几块场地`` 会有一份写在这行上、
另一份写在关系表里，而两者只有在**每一次写入都同时改两处**时才一致 —— 在那一天
之前它们都只是「看起来对」。而关系表那一份还会**折**（同一块场地会被反复判、
证据取并集、时刻只往一个方向走），这一份不会。两份数据里的同一句话，一份会变、
一份不变，读的人迟早会拿到过时的那一份，而报告上完全看不出来。

## 客观事实可以复用，Scene 判定不可以 —— 分界落在 ``status`` 那一格

§6 的另一半：账号的**客观事实**（这个组织叫什么、有哪些别名、域名与地址是什么、
在哪个国家）可以在多个 Scene 之间复用；而 ``eligibility`` / ``priority`` /
``product fit`` / ``role strategy`` 是**逐 Scene 的判定**。

分界落在 :attr:`status` 上，而它是**本 Scene 的**判定：同一份客观事实在两个 Scene
下是**两行**（``workspace`` 是行自己的，与 ``VenueAsset`` /
``AccountVenueRelation`` / ``Evidence`` 逐字同一条纪律），两个 ``status`` 互不影响；
库是绑工作区的（``adapters/sqlite/db.py::bind_workspace``），读另一个 Scene 的行
**报错**，而不是返回一个空结果。

所以「复用」在盘上的意思是：第二个 Scene 写**它自己的**行，可以照抄客观事实那些
格子的取值（它们说的是世界，不是某一次的结论），而它的 :attr:`status` 从
:attr:`~marketops.domain.enums.AccountStatus.DISCOVERED` 重新开始 —— 除非那个
Scene 自己判过。**没有任何一条代码路径把 ``status`` 从一个工作区搬到另一个工作区**，
而这是「一个 Scene 的结论不得渗进另一个」在这条链上唯一需要成立的事。

## ``status`` 与路线等级是两条轴，这一行只带其中一条

:class:`~marketops.domain.enums.AccountStatus` 的类文档已经写下了为什么两者正交
（组织对、人不对）。本模型里因此**没有** ``grade`` 这一格，也没有任何
「账号等级」的聚合：等级挂在 :class:`~marketops.domain.entities.ContactRoute` 上，
而一条路线属于某一次联系研究，不属于账号。这一行连路线那张表都不碰。

## 这一格 ``status`` 是**记下来的**判定，不是「现在跑一遍规则」

研究清单那一侧（``list_accounts``）每次都拿现行 ``grading.yaml`` 对
``AccountRecord`` 重跑一遍 ``grade_account``，报的是**此刻的规则怎么看这一行**。
本模型里的 ``status`` 是**写下来的那一次判定**：它有证据、有时刻，也可能已经过期。

两者可以合法地不同（规则改了版，或者那条事实后来被推翻），而它们不同这件事本身
有用。但**不许**把其中一个当成另一个的缺省值：那会把「记下来的那次判定」与
「现行的规则怎么看」合成一句话，而它们的处置相反 —— 前者要的是复核，后者要的是
改规则。

## 没有证据的账号行**写不出来**

与 :class:`~marketops.domain.entities.AccountVenueRelation` 逐字同一条：一条没有
任何证据的账号行，在下游读起来是「我们查到了」，而事实是「我们猜过」。空表在校验
期报错，正确的做法是**先不写这一行**。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .base import StrictModel as _Strict
from .candidate import PAIRING_KEY_MAX_LENGTH
from .enums import AccountStatus
from .util import check_canonical_id

__all__ = ["MarketAccount"]


class MarketAccount(_Strict):
    """这个市场里的一个账号：拥有 / 运营 / 维护 / 预约控制 / 采购某些场地的组织。

    ``account_id`` 是账号键，与 ``AccountRecord`` / ``ResponsibleEntity``
    同一个 id 空间（都过 :func:`~marketops.domain.util.check_canonical_id`），
    因为它们是同一个东西在链路上的两个阶段 —— 一个 id 在清单里与在池子里必须是
    同一个字符串，否则「这个账号在池子里吗」这个问题没有答案。

    **场地名不是键**，理由写在 ``AccountRecord`` 的类文档里（一个 Council 管很多
    场地）。这一行与场地之间的每一条边都是 §5 关系表上的一行，因此**一个账号可以
    同时以多种身份对多块场地负责** —— 这一格里没有任何东西逼它选一个。
    """

    #: 这一行属于哪个**工作区**（部署的 Scene，``Settings.profile``）。
    #:
    #: 必填、无默认值：一个「缺省 = 当前 Scene」的默认值会把**没写血统**变成一次
    #: 不留痕的放行，而跨 Scene 的复用正是止于这一格（模块 docstring 里那条「客观
    #: 事实可以复用、结论不可以」）。写法与 :class:`~marketops.domain.evidence.
    #: Evidence.workspace` 逐字相同。
    workspace: str
    account_id: str
    #: 这个组织的名字，**照来源写的那个**。改名不改写这里 —— 见 ``aliases``。
    organisation: str
    #: 同一个组织的**其余写法**：``Example Parish Council`` 与 ``Example PC``、
    #: 变更过的正式名。它们是**事实**（这些名字确实都指它），不是候选结论，
    #: 所以合并时取并集，什么都不删。
    #:
    #: 它与 :attr:`organisation` 分开，是因为后者是给人和报告读的**一个**名字，
    #: 而这一格回答「还叫过什么」。把别名混进 ``organisation`` 会让每一次合并
    #: 都要挑一个「更好的名字」，而池子里没有「谁的名字更权威」这条规则。
    aliases: list[str] = Field(default_factory=list)
    #: 这个组织的域名，**裸主机**（``client05.example.org``）。可以有多个：一个组织
    #: 常有主站与分区/子品牌站。
    #:
    #: 判据是 :func:`~marketops.domain.util.check_canonical_id`（也就是
    #: ``AccountCandidate.pairing_key`` 那条「canonical host」）—— 带 scheme、
    #: 带路径、含大写、含端口的值在这里就报错，而不是被规范化。同一台主机的两种
    #: 拼写会让「同一个组织」在数据上变成两个，而报告上只是「多了几行」。
    #:
    #: **这是形状判据，不是身份判据。** 两个账号可以同域名而仍是两行 —— §6 明令不得
    #: 因为同域名 / 同名 / 近地址而合并两个实体，池子的键只有 ``account_id``，
    #: 合并规则不看这一格。
    domains: list[str] = Field(default_factory=list)
    #: 这个组织的通讯地址，**照来源写的那个**（可能是门牌，也可能只是一个村或郡）。
    #: 本层不判断那份文本的粗细，也不去补全它 —— 与 ``VenueAsset.address`` 同一条。
    addresses: list[str] = Field(default_factory=list)
    #: 组织所在国家。``None`` = **还没查**，不是「不在英国」。
    #:
    #: 三个取值各有各的意思，与 :attr:`~marketops.domain.evidence.AccountRecord.
    #: country` 逐字相同：``None`` 是未确认，``""`` 是**证据**（查了，是没有），
    #: 非空字符串是查到了什么。合并规则里只有 ``None`` 不参与（见
    #: ``application/accounts.py::merge_accounts``）。
    country: str | None = None
    #: **本 Scene 对它的准入判定**。默认 ``discovered``：刚进池子，还没有任何判定。
    #:
    #: 与路线等级正交（见类文档），也与「清单那一侧现行规则跑出来的结论」不是同一
    #: 句话（见模块 docstring 末段）。
    status: AccountStatus = AccountStatus.DISCOVERED
    #: 支撑这一行的证据。与 ``Evidence.evidence_id`` 同空间，是**指针**不是正文：
    #: 一行账号读得懂，不依赖证据还能不能取到；而证据一旦被撤下，依赖它的判定必须
    #: **降级**，不是照旧成立。
    evidence_ids: list[str] = Field(default_factory=list)
    #: 这一行第一次落进池子的时刻。**是一次关于我们的事实**（我们何时记下了它），
    #: 因此永远可得，也永远由写路径当场给出 —— 不是从来源行里猜出来的。
    first_seen_at: datetime
    #: 最后一次**真的重新看过**它的时刻。``None`` = 只有它进池子那一次。
    #:
    #: 与 ``Observation.observed_at`` / ``VenueAsset.last_verified_at`` 同一条纪律：
    #: 读一遍本地记录**不是**一次观察。写入路径不填这一格（见
    #: ``application/accounts.py``），所以「这个账号多久没复核过」在池子刚建立时
    #: 是 ``None`` 而不是「刚刚」—— 后者会让下一个维护 tick 整批跳过它。
    last_verified_at: datetime | None = None

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        """见 :attr:`~marketops.domain.evidence.Evidence.workspace` —— 同一条判据、同一个理由。"""
        return check_canonical_id(value, "workspace")

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "account_id")

    @field_validator("organisation")
    @classmethod
    def _organisation_is_non_empty(cls, value: str) -> str:
        """名字空着的一行写不出来。

        ``""`` 在这里**没有**「还没查」这个意思（那是 :attr:`country` 的方言）：
        一行账号的身份就是它的名字，而池子里的行是**已经研究过的东西**，不是一条
        待研究的线索。没有名字的行在报告里是一行没有主语的判定。
        """
        if not value or value != value.strip():
            raise ValueError("organisation 不能为空或首尾带空白")
        return value

    @field_validator("domains")
    @classmethod
    def _every_domain_is_a_canonical_host(cls, value: list[str]) -> list[str]:
        """逐条过 canonical host 的判据 —— 理由见 :attr:`domains`。

        ``max_length`` 取 :data:`~marketops.domain.candidate.PAIRING_KEY_MAX_LENGTH`
        而不是那个 60 的文件名界：这两个格子里装的是同一种东西（一个 canonical
        host），而 60 会让一个合法但很长的域名在这一格上被拒、在候选那一格上被收
        —— 同一条判据在两个模型上给出两种命运，正是本仓「只留一个定义处」要防的。
        """
        return [
            check_canonical_id(domain, "domains", max_length=PAIRING_KEY_MAX_LENGTH)
            for domain in value
        ]

    @field_validator("aliases", "addresses")
    @classmethod
    def _every_alias_and_address_is_clean_text(cls, value: list[str]) -> list[str]:
        """逐条非空、且没有首尾空白。

        这两格是自由文本（``Example Parish Council``、``Main Road, Example Town One``），
        所以判据只能是这一条 —— 不能套 :func:`check_slug_safe` 那一类 id 判据。

        首尾空白在这里是**真的会咬人**的：合并规则用
        ``application/merge.py::ordered_union`` 去重，而 ``" Example PC"`` 与
        ``"Example PC"`` 是两个不相等的字符串 —— 同一个别名会被记成两条，报告上
        读起来像「它还有另一个名字」。
        """
        for item in value:
            if not item or item != item.strip():
                raise ValueError("aliases/addresses 的每一条都不能为空或首尾带空白")
        return value

    @field_validator("first_seen_at", "last_verified_at")
    @classmethod
    def _timestamps_are_aware(cls, value: datetime | None, info: ValidationInfo) -> datetime | None:
        """naive 时间戳在这里就报错 —— 判据与理由在 ``domain/entities.py`` 的同名函数上。"""
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return value

    @model_validator(mode="after")
    def _an_account_without_evidence_is_not_written(self) -> "MarketAccount":
        """**没有证据的账号行写不出来** —— 见模块 docstring 末段。

        这一格与 ``AccountVenueRelation`` 上那条判据是同一句话（「凭什么这么说」
        这一栏不能空着），而它的执行器在两个模型上各挂一次：模型校验器不是继承来
        的，一个模型上写过的判据在另一个模型上不会自动成立。
        """
        if not self.evidence_ids:
            raise ValueError(
                "一条证据都没有的账号行写不出来：它会读成「我们查到了」，而事实是"
                "「我们猜过」。正确的做法是先不写这一行，等有了证据再写。"
            )
        return self

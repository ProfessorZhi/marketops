"""增量维护：**什么时候该重新看一眼哪一格**。

``domain/temporal.py`` 回答的是「这条断言今天还能不能用」（它是一条判据）；
本模块回答的是另一件事：**「我们该不该现在去重新核一遍」**。
两者容易混成一个「过期」的概念，所以先把它们分开：

* 时新性（temporal）的输入是一条断言的**时间锚点**，输出是
  ``CURRENT`` / ``HISTORICAL`` / ``STALE`` / ``CONFLICTING`` / ``UNKNOWN``。
  它是**读**的那一刻的判据 —— 用在「这条证据还能不能拿来支持一句结论」上。
* 到期（本模块）的输入是**上一次真的观测到它的时刻**与**这个格子自己的节奏**，
  输出是「现在有没有一件该做的事」。它是**写**之前的调度判据。

把两者合一，会让「这条证据还可用但该复核了」无法表达 —— 而那个状态恰恰是
持续经营里最常见的一格。

## 逐面配置，而不是一个 TTL

七个 aspect 分开配，因为它们过期的速度**不是一回事**：一个场地还在不在，
与「某个人还在不在那个岗位」是两个数量级。合成一个数之后，维护报告上
「这次复核动了什么」就只剩一个数字，而运营的人需要知道的是哪一面在动。

拆成七格而不是五格的理由写在 :class:`Aspect` 里，实质只有一条：**绑在一起的两件事
会互相续期**。这里只补一句它为什么不与「别把架构做大」冲突 —— 这一层没有多出任何
机制，多出来的只是两行规则与两格 ``(主体, aspect)`` 主键；而少这两格，第一件真实
发生的「人还在、邮箱作废」就会以「这一格刚看过」的形式被记下来。

## 未标定 ≠ 业务事实

``RefreshRule.calibrated`` 是**承重字段**。讨论里出现过的 30 / 90 / 180 天
**不是业务事实** —— 它们没有一个来自获批策略或留出评估。所以：

* 缺省计划由 :meth:`RefreshPlan.uncalibrated_default` 产生，每条 ``calibrated=False``；
* ``calibrated=False`` 的格子照样会被调度（否则系统什么都不做），
  但它在报告里必须带着「未标定」被说出来；
* 本模块**不提供**把未标定悄悄读成「已批准」的入口。

一条边界：本模块是纯域 —— 没有时钟、没有存储、没有 I/O。``now`` 是参数，
这既让「重启后语义不变」可测，也让「用可控时钟验证长期稳定性」成为可能
（不必真的等几周）。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from enum import Enum

from pydantic import field_validator, model_validator

from marketops.domain.base import StrictModel
from marketops.domain.util import SEP, check_canonical_id, sha256_text, to_utc


class Aspect(str, Enum):
    """一个主体上**可以单独过期**的七个面。

    七个成员来自业务口径（§11 逐字列的就是这七个），不是实现细节：来源覆盖（该看的
    来源看全了没有、有没有新的来源出现）、资产身份（这是**哪一块**场地/设施）、
    资产属性（它**是什么样**）、账号关系（谁拥有 / 运营 / 维护它）、
    联系人与角色（那个岗位上是谁）、邮箱证据（那个邮箱真的属于那个人）、
    产品适配（这个产品对这个场地还成不成立）。

    ## 为什么「资产」与「联系人」各占两格

    这两块原来各占一格（``asset_fact`` / ``role_email``），拆开的理由不是想把事情
    做细，而是**这两格各自把两个会分别过期的东西捆在了一条记录里**：

    * 「这块场地是什么」（身份）与「它现在是什么样」（属性）的变化速度不同。身份
      多半定下就不再变，属性会变 —— 翻新、停用、设施增减。
    * 「那个人还在不在这个岗位上」（角色）与「那个邮箱还通不通」（邮箱证据）更是
      两件事，而「人在、邮箱作废」（离职后邮箱被回收）是常见结局。

    捆在一起的实际后果不是「粗」，而是**一次只看了一半的复核会给两半一起续期**：
    两半共用一个 ``observed_at``，于是查了属性就把角色也推到下一个周期。这是这七格
    必须各自独立的第一条理由，也是 ``what_is_due`` 按 ``(主体, aspect)`` 分格的全部
    意义 —— 它选的是「这一格该看了」，不是「这个主体该看了」。

    ## 它**不是** ``domain/scene.py`` 的 ``ImpactScope``

    两个枚举里有几个词长得一样（``product_fit``），但它们答的不是同一个问题：
    这一格答「**什么东西会各自过期**」（调度），``ImpactScope`` 答「换一版 Scene
    要让**什么重做**」（影响面）。后者刻意更粗 —— 改一次 ``region`` 要重取的是
    「资产事实」这整件事，不是要在身份与属性之间挑一个。所以两者**不互相推导**，
    也没有一张表把它们映起来：一个是「什么时候再看一眼」，一个是「什么已经不作数了」，
    动其中一个不需要动另一个。
    """

    SOURCE_COVERAGE = "source_coverage"
    ASSET_IDENTITY = "asset_identity"
    ASSET_ATTRIBUTE = "asset_attribute"
    ACCOUNT_RELATION = "account_relation"
    CONTACT_PERSON_ROLE = "contact_person_role"
    EMAIL_EVIDENCE = "email_evidence"
    PRODUCT_FIT = "product_fit"


#: 全部 aspect，**顺序固定**。遍历这张表而不是 ``Aspect`` 本身：枚举的迭代顺序
#: 是定义顺序（今天等价），而报告里的顺序必须是钉住的 —— 一份每次跑都换个次序的
#: 报告，人眼没法比对两次运行。
#:
#: 顺序取 §11 列出这七个的次序（来源 → 资产 → 关系 → 联系人 → 产品），也就是
#: 业务链的顺序，而不是实现里碰巧的顺序：读报告的人从上面往下看，看到的是
#: 「先把来源看全、再看这块场地、再看谁管它、再看找谁、最后看产品对不对得上」。
ALL_ASPECTS: tuple[Aspect, ...] = (
    Aspect.SOURCE_COVERAGE,
    Aspect.ASSET_IDENTITY,
    Aspect.ASSET_ATTRIBUTE,
    Aspect.ACCOUNT_RELATION,
    Aspect.CONTACT_PERSON_ROLE,
    Aspect.EMAIL_EVIDENCE,
    Aspect.PRODUCT_FIT,
)

#: **承诺要维护的**全部复核面 —— 也就是 :data:`ALL_ASPECTS` 本身。
#:
#: 它存在的唯一理由是让「承诺」与「本部署做得了」成为**两个可以说出口的量**，
#: 而不是同一个。另一个在这个仓库里：
#: ``application/refresh.py::REFRESHABLE_ASPECTS``（本部署有实现的那些）。
#:
#: ## 为什么必须有一个地方写下「全部七个」
#:
#: §11 逐字：「R2 必须给官网/责任关系/联系人提供真实刷新执行器；**未接的 aspect 报
#: coverage gap，不能通过 out_of_scope 把必需功能从验收分母删掉**」。
#: 一个把「做得了的」当分母的部署永远全绿，而它的绿来自减法：每少实现一个面，
#: 差集就小一分，而报告上什么都不显示。所以分母要有一个**不随实现变化**的定义处，
#: 而它就是这里 —— 改动它等于改产品承诺，那是业务决定，不是实现决定。
#:
#: 两个常量的差（``REQUIRED_ASPECTS`` 减去本部署声明得出来的那些）就是 coverage
#: gap，逐面列出、逐面计数。**它是一份欠账，不是一个应当被折平的数。**
REQUIRED_ASPECTS: tuple[Aspect, ...] = ALL_ASPECTS


class RefreshRule(StrictModel):
    """一个 aspect 的复核节奏。

    ``after_days`` 是「上次真的看到它之后，隔多少天该再看一眼」。它**不是**
    「证据在多少天之后失效」—— 那是 ``domain/temporal.py`` 的 ``FreshnessPolicy``。
    两个数可以不同，而且不同是正常的：一条三年前的公开页面可能仍然算
    ``HISTORICAL``（还能用），但责任关系这一面可能三个月就该核一次。
    """

    aspect: Aspect
    after_days: int
    #: 这个数字有没有标定过。默认 False —— 缺省是「未标定」，不是「已批准」。
    calibrated: bool = False

    @field_validator("after_days")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError(
                "after_days 必须 >= 1。0 或负数会让这个 aspect **永远**处于到期状态，"
                "于是每一次 tick 都重新核一遍同一格 —— 那是一次按秒计费的无底洞。"
            )
        return value


class RefreshPlan(StrictModel):
    """一个 Scene 版本上的复核计划。

    **每个 aspect 恰好一条。** 少一条不是「永不过期」，而是「没配」——
    两者在调度器眼里长得一样（都选不出工作），但处置完全不同：一个是决定，
    一个是遗漏。构造期就要求齐全，好过在报告里靠人去发现少了一行。
    """

    rules: tuple[RefreshRule, ...]

    @model_validator(mode="after")
    def _exactly_one_per_aspect(self) -> "RefreshPlan":
        seen = [rule.aspect for rule in self.rules]
        if len(set(seen)) != len(seen):
            duplicates = sorted({a.value for a in seen if seen.count(a) > 1})
            raise ValueError(f"同一个 aspect 配了多条复核规则：{duplicates}")
        missing = [aspect.value for aspect in ALL_ASPECTS if aspect not in set(seen)]
        if missing:
            raise ValueError(
                f"复核计划缺 aspect：{missing}。缺一条不是「永不过期」，是「没配」——"
                "调度器选不出它，而报告上看不出这是决定还是遗漏。"
            )
        if tuple(rule.aspect for rule in self.rules) != tuple(
            a for a in ALL_ASPECTS if a in set(seen)
        ):
            # 顺序不参与语义，但它会进报告。钉住它，两次运行的报告就能逐行比对。
            raise ValueError("复核计划必须按 ALL_ASPECTS 的顺序给出")
        return self

    @property
    def is_calibrated(self) -> bool:
        """**全部**规则都标定过才算标定过的计划。"""
        return all(rule.calibrated for rule in self.rules)

    @property
    def uncalibrated(self) -> tuple[Aspect, ...]:
        return tuple(rule.aspect for rule in self.rules if not rule.calibrated)

    @property
    def policy_version(self) -> str:
        """这一份复核计划**是哪一份**。内容的摘要，不是版本号。

        §11 要求每一格都带着 ``policy_version``，理由是「180 天」这四个字本身不是
        一条事实：它得跟着「谁定的这个数、定的是哪一版」一起读才成立。而这份计划
        只从 Scene 版本进来，所以那一版 Scene 就是它的出处 —— 这个摘要是那条出处
        在格子上的**指纹**。

        用**内容摘要**而不是一个递增计数，因为要回答的问题是「两次读到的策略是
        同一份吗」，而不是「这份策略是第几份」。计数做不到这件事：它住在库里，
        于是同一条内容在两次部署里会拿到两个号（看起来像变过），而两条不同的内容
        在别的库里可能拿到同一个号（看起来像没变）。摘要两件事都做对，而且不需要
        任何地方替它记着。

        **它不等于「已批准」。** 标定与否在 :attr:`RefreshRule.calibrated` 里，
        两者分开：一个说「是这三组数」，一个说「有人认过这三组数」。所以摘要里带上
        ``calibrated`` —— 「180 天、未标定」与「180 天、已标定」是两份不同的策略，
        而只有后者能说「有人认过」。
        """
        return sha256_text(
            SEP.join(
                f"{rule.aspect.value}={rule.after_days}"
                f"{'+calibrated' if rule.calibrated else ''}"
                for rule in self.rules
            )
        )[:16]

    def rule_for(self, aspect: Aspect) -> RefreshRule:
        for rule in self.rules:
            if rule.aspect is aspect:
                return rule
        raise KeyError(aspect)  # pragma: no cover —— 构造期已保证齐全

    @classmethod
    def uncalibrated_default(cls) -> "RefreshPlan":
        """未标定的缺省计划。

        **这三个数不是业务事实。** 它们是一组明确标注为「未标定」的起点，
        让系统在拿到获批策略之前也能运转，并且**在每一份报告里都说得出这一点**。
        改它们不需要改代码 —— 但只有通过一个 Scene 版本才改得动，所以改动会留痕。

        ## 拆出来的那两格为什么**照抄**上一格的天数

        从五个面拆成七个面时，``asset_identity`` / ``asset_attribute`` 都拿 180，
        ``contact_person_role`` / ``email_evidence`` 都拿 90 —— 也就是各自沿用被拆
        那一格原来的数，**没有**给两半各编一个不同的数。

        理由是这个类自己那条：这里的每一条都是声明，缺省是「未标定」。拆开是为了
        **将来能分别标定**，不是现在就假装已经标定过了 —— 「属性比身份过期快」这句话
        有一层道理（见 :class:`Aspect`），但那是一层定性的话，给不出 180 与 90 的区别。
        编两个不同的数会让报告里那一格看起来像已经有人想过一遍，而报告**没有**别的地方
        能让读者看出那是我编的。

        照抄的代价是报告里会出现两个相同的数（180/180、90/90），而那恰好是实话：
        这两格今天共用一个未标定的缺省值，还没人分别定过。等有人要分别标定的时候，
        改动会走 Scene 版本，留痕。
        """
        return cls(
            rules=(
                RefreshRule(aspect=Aspect.SOURCE_COVERAGE, after_days=180),
                RefreshRule(aspect=Aspect.ASSET_IDENTITY, after_days=180),
                RefreshRule(aspect=Aspect.ASSET_ATTRIBUTE, after_days=180),
                RefreshRule(aspect=Aspect.ACCOUNT_RELATION, after_days=90),
                RefreshRule(aspect=Aspect.CONTACT_PERSON_ROLE, after_days=90),
                RefreshRule(aspect=Aspect.EMAIL_EVIDENCE, after_days=90),
                RefreshRule(aspect=Aspect.PRODUCT_FIT, after_days=30),
            )
        )


class Observation(StrictModel):
    """**上一次真的看到这一格**是什么时候。

    主语用 ``(subject_kind, subject_id)`` 两段而不是一个字符串：``subject_kind``
    是对象类别（account / route / venue / …），把它拼进 id 会让将来想按类别
    统计时只能做字符串前缀匹配，而前缀匹配会把 ``route`` 和 ``route-opportunity``
    混在一起。

    ``observed_at`` 的语义**逐字**是「我们真的拿到了这一面的内容」，不是
    「这条记录被写过」。这正是 ``tests/test_domain_temporal.py`` 里那条
    ``test_observation_alone_never_counts_as_reverification`` 的意思 ——
    一次 re-read 不产生新的观测，一次真的抓取才产生。

    那条反例的**主语**要说准，否则它会被读成「本地读一律不算数」：它拦的是
    **重读一份关于对象的记录**、然后声称对象本身被重新核过了（读一条资产行、
    读一份回执）。对有的面，主语**就是**那份本地记录 —— ``SOURCE_COVERAGE``
    问的是「我们手上有哪些来源」，而把它们整个数一遍是一次满覆盖的测量，不是
    一次近似。**判据是「这一面的主语是不是那份本地记录」，不是「数据在本地还是
    远程」。** 落地在 ``application/refresh.py``（声明）与
    ``application/maintenance.run_once``（记账），两边各自的 docstring 写了这件事。

    ``workspace`` 是**授权链的第一段**：原始观测事实在一个已授权工作区内部可以
    跨 Scene 复用（这也是它不带 ``scene_id`` 的原因），但复用范围止于工作区边界。
    一个不带工作区的观测记录，读的时候就没有任何东西能回答「这条事实是谁的」。
    """

    workspace: str
    subject_kind: str
    subject_id: str
    aspect: Aspect
    observed_at: datetime
    #: 是哪一次运行看到的。没有来源的观测无法回溯，也就无法复核。
    source_ref: str = ""

    @field_validator("workspace")
    @classmethod
    def _workspace_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("subject_kind", "subject_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("subject_kind / subject_id 不能为空或首尾带空白")
        return value

    @field_validator("observed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError(
                "observed_at 必须带时区。naive 时间在两个部署之间会被读成不同的时刻，"
                "而到期判定正是拿它减 now —— 差 8 小时就足以让一格早一天或晚一天到期。"
            )
        return to_utc(value)

    @property
    def key(self) -> tuple[str, str, Aspect]:
        return (self.subject_kind, self.subject_id, self.aspect)


class ChangeSignal(StrictModel):
    """一条「这一格变了」的外部信号。

    周期复核是**够用**的，但不够**及时**：一个场地换了运营方，在 90 天的
    周期里会被当成旧的继续用。变更信号把「该看的时间」从日历上挪到事件上。

    它必须自带 ``signalled_at`` 与 ``reason``：信号没有理由，就等于「有人说变了」
    —— 而复核报告要回答的恰恰是「为什么这次会重跑这一格」。
    """

    workspace: str
    subject_kind: str
    subject_id: str
    aspect: Aspect
    signalled_at: datetime
    reason: str

    @field_validator("workspace")
    @classmethod
    def _workspace_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("reason")
    @classmethod
    def _reason_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "变更信号必须写明理由。没有理由的信号在报告里与「有人点了按钮」"
                "无法区分，而两者的处置不同：前者要复核那一格，后者要问为什么要点。"
            )
        return value

    @field_validator("signalled_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("signalled_at 必须带时区")
        return to_utc(value)


class DueReason(str, Enum):
    """这一格为什么到期。**两者处置相同，但报告必须分得开。**"""

    #: 距上次观测已超过该 aspect 的节奏。
    CADENCE_ELAPSED = "cadence_elapsed"
    #: 有人报了这一格变了。
    CHANGE_SIGNALLED = "change_signalled"
    #: 从来没有观测过这一格。
    NEVER_OBSERVED = "never_observed"


class RecheckState(str, Enum):
    """一次**外部重查**走到了哪一步。四格，各自对应一种处置。

    §11 要的是「无变化/变化/不可达三种状态分开」。这里是四格而不是三格，多的那一格
    不是含糊，是这句话的**另一半**：三种状态说的是**一次抓取**的三种结局，而第四格
    说的是**没有发生抓取**——它由同一条要求的前一句直接推出来（「获取前检查已有工件、
    使用权限、内容/解析版本、所需事实与时效；**可复用则返回引用，不调用供应商**」）。
    把它并进「无变化」的后果是：报告再也答不出「这一轮**花了钱没有**」，
    而两次都没变的一轮——一次抓了一次没抓——在账上完全相同。

    * :attr:`REUSED` —— **这一面这一趟没有花抓取的钱**：本工作区里已有仍然有效的
      证据（时效未过、解析版本是当前的），于是回它的引用；或者本轮另一个面已经把
      同一批页面取回来了（一次 tick 里两个面共用一批页面）。
      **它不写一条以本次跑的时刻为观察时刻的观测** —— 手上那份证据没有被重新看过
      一眼，而按时刻记账会把真正的复核推后一个周期（理由见 ``Observation``）。
      它写进账的 ``observed_at`` 是**那份证据被取回的时刻**，于是观测的时刻落在
      复核周期里它本该在的位置。
      （把这句话读成「``REUSED`` 一律不写观测」是错的，而错的那一读曾经写在这一行
      上：本轮共用页面那一支**有人**真的看过那些页面，它的观察时刻是这一趟。）
    * :attr:`UNCHANGED` —— 抓了，页面指纹与记录一致，**一行新证据都没有落**。
    * :attr:`CHANGED` —— 抓了，指纹不同**或**解析版本变了，于是落了新证据。
      两件事分开记在逐 URL 的那一格上（``cause``），因为「世界变了」与「我们换了
      解析器」是两句不同的话，而它们对业务的影响完全不同。
    * :attr:`UNREACHABLE` —— 没抓到**或者这一趟没看全**。**它不写观测**：这一格的
      真实状态是「不知道」，而写一条观测等于把一次失败读成一次刚验证（关闭条件逐字：
      「失败不写刚验证」）。

    ## 为什么 ``REUSED`` 不是 ``UNREACHABLE``

    两者的共同点是「没花这笔钱」，而处置相反：前者什么都不用做（手上的证据仍然成立），
    后者要去查为什么够不着（站点挂了、被拦了、URL 变了）。合成一格的后果不是报告
    读起来含混，而是**一次网络故障会被当成一次正常的复用**而永远没人看。
    """

    REUSED = "reused"
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    UNREACHABLE = "unreachable"


def due_dedupe_key(
    *,
    scene_id: str,
    scene_version: int,
    subject_kind: str,
    subject_id: str,
    aspect: str,
    period: str,
) -> str:
    """一格的幂等键。**这是它唯一的定义处。**

    回执那一侧并不自己算一遍：它照抄 :attr:`DueWork.dedupe_key`（见 :func:`claim_of`）。
    于是「到期选出来的键」与「写进主键的键」是同一个**值**，而不是两处算出来的同一个值。

    ``aspect`` 收的是**字符串**而不是 :class:`Aspect`，这不是随手写的宽类型。两处
    调用方各需要它的一半：

    * :attr:`DueWork.dedupe_key` 传今天的成员值（``self.aspect.value``）；
    * 迁移要**验**存量回执行的键 —— 那一行的取值是**旧词表**里的词，今天构造不出
      对应的成员，而「键对不对」恰恰要在重写之前问一次。

    收 ``Aspect`` 会让第二处调用方只能自己抄一遍拼接口令，而两份口令一旦漂开，
    表现是「迁移说这些行都没问题」而不是报错。
    """
    return sha256_text(
        SEP.join(
            [
                "due",
                scene_id,
                str(scene_version),
                subject_kind,
                subject_id,
                aspect,
                period,
            ]
        )
    )[:32]


#: 一格同时有多个理由时，谁算「最强」——以及 :func:`_merge_cell` 按哪个次序收集。
#: **从强到弱**，次序是写下来的判断：
#:
#: * ``never_observed`` 最强，因为它是**根因**：这一格没有基线，「到点了」这句话
#:   在它身上无从谈起（没有基线就没有节奏）。
#: * ``change_signalled`` 次之，因为它是一条**关于世界的陈述**：有人报了这一格变了。
#:   时限到点是一条**关于钟的陈述**，它不因世界变没变而成立 —— 世界已经变了的时候，
#:   读的人要看到的是那一句。
#: * ``cadence_elapsed`` 最弱，它是「按计划该看一眼了」，也就是没有别的信号的默认情形。
_STALE_PRECEDENCE: tuple[DueReason, ...] = (
    DueReason.NEVER_OBSERVED,
    DueReason.CHANGE_SIGNALLED,
    DueReason.CADENCE_ELAPSED,
)


class DueWork(StrictModel):
    """一件该做的维护工作。

    ``dedupe_key`` 是**幂等键**，不是描述：重启、重复 tick、并发驱动都靠它
    保证同一格在同一个周期里只被做一次。它把 scene / 版本 / 主体 / aspect /
    周期五样都编进去，因此：

    * 同一个 Scene 换了一版 → 是新工作（规则变了，旧结论不再成立）；
    * 同一个周期里 tick 两次 → 是同一件工作；
    * 同一条变更信号重放 → 是同一件工作。

    ## 一件工作可以有几个理由，而账上必须留全

    §11 逐字：「同一对象/面上同时出现首次观察与变更信号时**合并必要工作，保留全部
    原因**」。所以这一格是 :attr:`reasons`（**复数**），而 :attr:`reason` 是它的
    第一个元素 —— 那个「最强的那一个」。

    这两个问题必须分开，「合并」才不至于变成「丢信息」：

    * **有几件事要做** —— 一个 ``(主体, aspect)`` 上不管同时出现了几个理由，要做的
      都是**同一件事**（去看这一格）。所以它**是一件工作**：一个 ``dedupe_key``，
      一次认领，一次抓取。不合并的后果是「首次观察 + 一条信号」把同一格驱动两次，
      而第二次读到的正是第一次刚写下的东西 —— 关闭条件把这一条逐字点名了
      （「首次观察+信号只造成一次必要重查」）。
    * **为什么该做** —— 理由是一条**关于世界的陈述**，而报告要回答「为什么这次会
      重跑这一格」。所以全部理由留在 :attr:`reasons` 上，一个都不折掉。

    一条理由**不只是**「报告上多一行」：它可能是一条信号，而信号的 ``period`` 决定
    这一格下一次是不是一件新工作。所以合并时**周期与时刻取自最强的那一条理由**
    （见 :func:`_merge_cell`），否则「信号提前触发的那一次」会在账上消失 ——
    旧版本靠「不合并」来避免这件事，代价是每一格多驱动一次。两者都保住的写法是
    一条工作 + 全部理由。
    """

    scene_id: str
    scene_version: int
    subject_kind: str
    subject_id: str
    aspect: Aspect
    #: 这一格到期的**全部**理由，按 :data:`_STALE_PRECEDENCE` 从强到弱。非空。
    #:
    #: ``reason`` 是它的第一个元素而不是一个独立字段：后者是同一件事的两处定义，
    #: 而两处迟早会漂开（会出现「``reason=change_signalled`` 而 ``reasons`` 里
    #: 没有它」这种行，它在库里、在报告里都不会自己报错）。
    reasons: tuple[DueReason, ...]
    #: 这一次到期的**周期标识**。周期制的是「第几轮」，信号制的是信号时刻本身。
    period: str
    due_at: datetime
    #: 上次观测时刻；从未观测过时为 None。**不是** 0，也不是 epoch。
    last_observed_at: datetime | None = None
    source_ref: str = ""

    @field_validator("due_at")
    @classmethod
    def _due_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("due_at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("last_observed_at")
    @classmethod
    def _last_observed_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("last_observed_at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("reasons")
    @classmethod
    def _reasons_are_ordered(cls, value: tuple[DueReason, ...]) -> tuple[DueReason, ...]:
        """理由非空、不重复，且**按强弱排好了**。

        三条都校验而不是靠构造方自觉，是因为这一格的读法是「取第一个当最强」：
        一个没有排过序的元组会让「最强的那一个」变成「碰巧写在最前面的那一个」，
        而它在报告上完全正常。重复的取值同理 —— 它会让「这一格有几个理由」多算。
        """
        if not value:
            raise ValueError(
                "reasons 不得为空。一件没有理由的到期工作无法回答「为什么这次会重跑"
                "这一格」，而那个问题正是报告要答的。"
            )
        if len(set(value)) != len(value):
            raise ValueError(f"reasons 里有重复取值：{[r.value for r in value]}")
        if tuple(sorted(value, key=_STALE_PRECEDENCE.index)) != value:
            raise ValueError(
                f"reasons 必须按 _STALE_PRECEDENCE 从强到弱排列，实际是 "
                f"{[r.value for r in value]}。"
            )
        return value

    @property
    def reason(self) -> DueReason:
        """最强的那一个理由。**不是**「唯一的那一个**了**」——全部理由在 ``reasons`` 上。"""
        return self.reasons[0]

    @property
    def dedupe_key(self) -> str:
        return due_dedupe_key(
            scene_id=self.scene_id,
            scene_version=self.scene_version,
            subject_kind=self.subject_kind,
            subject_id=self.subject_id,
            aspect=self.aspect.value,
            period=self.period,
        )


def _latest_observations(
    observations: tuple[Observation, ...],
) -> dict[tuple[str, str, Aspect], Observation]:
    """``(主体, aspect)`` → 最近一次观测。

    同一格观测过多次时取**最近**那次：维护关心的是「上次看到它是什么时候」，
    不是「第一次看到它是什么时候」。

    抽出来是因为 :func:`what_is_due` 与 :func:`aspect_states` 都要这一份索引，
    而「取最近那次」是一个**决定**（另一个候选是取第一次），两处各写一遍迟早漂开：
    一处取最近，另一处取第一次，表现会是「到期的格子与报告里说旧了的格子对不上」。
    """
    latest: dict[tuple[str, str, Aspect], Observation] = {}
    for observation in observations:
        key = observation.key
        current = latest.get(key)
        if current is None or observation.observed_at > current.observed_at:
            latest[key] = observation
    return latest


def _known_subjects(
    observations: tuple[Observation, ...],
    subjects: tuple[tuple[str, str], ...],
) -> set[tuple[str, str]]:
    """这个工作区里**已知存在**的主体。见 :func:`what_is_due` 的 docstring。"""
    known = {(o.subject_kind, o.subject_id) for o in observations}
    return known | set(subjects)


def what_is_due(
    *,
    scene_id: str,
    scene_version: int,
    plan: RefreshPlan,
    observations: tuple[Observation, ...],
    signals: tuple[ChangeSignal, ...],
    now: datetime,
    subjects: tuple[tuple[str, str], ...] = (),
) -> tuple[DueWork, ...]:
    """选出到期的格子。纯函数，时序完全由 ``now`` 决定。

    §11 把这一个选择器叫 ``what_is_due(scene, now)``，返回到期的来源检查、
    到期的资产、到期的账号关系、到期的联系人 —— 也就是这个方法。

    签名里没有 ``scene`` 这个参数，是**分层**的结果而不是漏了：``domain/scene.py``
    导入本模块（它要 :class:`RefreshPlan`），所以本模块不能反过来导入它。调用方手里
    那个 Scene 版本里的三样东西 —— 身份、版本号、复核计划 —— 在这里各是一个入参，
    由调用方从**同一版** Scene 上取。把三样合成一个本模块不认识的类型，只会让
    「它们必须来自同一版」这条约束从类型上消失，而那是调用方本来就保证的。

    它是**纯的**：读的是传进来的事实，不看任何进程内状态。这就是 §11 那条
    「重启后仍知道谁到期」的全部机制 —— 重启之后从库里读回来的 observations 与
    signals 一模一样，于是选出来的格子一模一样，不需要任何东西从内存里活下来。

    **本模块不知道世界上有哪些主体。** 主体的存在性只有两个来源，都要调用方给：

    * ``observations`` 里出现过的 —— 至少看过一眼；
    * ``subjects`` 里显式声明的 ``(subject_kind, subject_id)`` 对 ——
      给「刚立项、一格都还没看过」的 Scene 一个起跑线。

    只从这两个来源取，是因为一个会自己发现主体的选择器必然也会自己**发明**
    主体：池子里混进一个拼错的 id，它就会被当成「一件从来没核过的工作」排进队，
    而账上只显示「多做了一件」。

    ``signals`` 只能**提前已有格子**的到期时间，不能凭空造主体 —— 一条指向
    不存在主体的信号是数据错误，处置是回去查它从哪来，不是顺手建一个主体出来。

    信号是**事件**，不是状态：一条已经有观测落在它之后（或同一时刻）的信号不再
    产生工作。这一格在那一刻之后被看过，看的就是变更之后的样子。反过来说，只要
    还没有人看过，这条信号就一直把这一格留在到期清单上 —— 那是它该做的事。

    返回值按 ``(due_at, subject_kind, subject_id, aspect)`` 排序：顺序稳定，
    截断才可复现 —— 一个「取前 N 件」的选择器如果不稳定，两次运行会做不同的
    事，而账上只会显示「各做了一次」。
    """

    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    now = to_utc(now)

    latest = _latest_observations(observations)
    known = _known_subjects(observations, subjects)

    items: list[DueWork] = []
    for subject_kind, subject_id in sorted(known):
        for aspect in ALL_ASPECTS:
            rule = plan.rule_for(aspect)
            seen = latest.get((subject_kind, subject_id, aspect))
            if seen is None:
                items.append(
                    DueWork(
                        scene_id=scene_id,
                        scene_version=scene_version,
                        subject_kind=subject_kind,
                        subject_id=subject_id,
                        aspect=aspect,
                        reasons=(DueReason.NEVER_OBSERVED,),
                        period="never",
                        due_at=now,
                    )
                )
                continue
            age = now - seen.observed_at
            if age < timedelta(days=rule.after_days):
                continue
            # 周期 = 从上次观测起算的第几轮。它**不含 now**，否则每次 tick 都是
            # 一个新周期，去重键形同虚设，而账单上只显示「核得比较勤」。
            # ``due_at`` 取的是**最近一次**本该到期的时刻，于是按它排序就是
            # 「拖得最久的一格排在前面」。
            cycle = int(age.days // rule.after_days)
            items.append(
                DueWork(
                    scene_id=scene_id,
                    scene_version=scene_version,
                    subject_kind=subject_kind,
                    subject_id=subject_id,
                    aspect=aspect,
                    reasons=(DueReason.CADENCE_ELAPSED,),
                    period=f"cycle-{cycle}",
                    due_at=seen.observed_at + timedelta(days=rule.after_days * cycle),
                    last_observed_at=seen.observed_at,
                    source_ref=seen.source_ref,
                )
            )

    for signal in signals:
        if (signal.subject_kind, signal.subject_id) not in known:
            continue
        seen = latest.get((signal.subject_kind, signal.subject_id, signal.aspect))
        # **已经应答过的不再排。** 信号是一句「这一格变了」，而一条落在信号之后
        # （或同一时刻）的观测，说的正是「这一格现在是什么样」—— 它包含了那次变更。
        # 不这样判的后果不是多做一次，而是**永远多做**：信号不会被消费，于是这一格
        # 此后每一轮计划都显示为到期，而 tick 每一次都拿不到认领（同一个 ``dedupe_key``
        # 已经 DONE 过）—— 计划说「该做」，同一时刻的 tick 说「没得做」，两份报告互相
        # 矛盾，而真相是这件事早就办完了。这一条在信号有生产者之前看不出来。
        if seen is not None and seen.observed_at >= signal.signalled_at:
            continue
        items.append(
            DueWork(
                scene_id=scene_id,
                scene_version=scene_version,
                subject_kind=signal.subject_kind,
                subject_id=signal.subject_id,
                aspect=signal.aspect,
                reasons=(DueReason.CHANGE_SIGNALLED,),
                # 周期就是信号时刻本身：同一条信号重放 → 同一个键 → 只做一次；
                # 两条不同时刻的信号 → 两件工作。这正是重放安全想要的行为。
                period=f"signal-{signal.signalled_at.isoformat()}",
                due_at=signal.signalled_at,
                last_observed_at=seen.observed_at if seen else None,
                source_ref=signal.reason,
            )
        )

    # 一个 ``(主体, aspect)`` 上的若干条理由 → **一件**工作。合并的判据、以及
    # 「周期取自哪一条」，都写在 :func:`_merge_cell` 上。
    cells: dict[tuple[str, str, Aspect], list[DueWork]] = {}
    for item in items:
        cells.setdefault((item.subject_kind, item.subject_id, item.aspect), []).append(item)
    merged = [_merge_cell(group) for group in cells.values()]
    return tuple(
        sorted(
            merged,
            key=lambda d: (d.due_at, d.subject_kind, d.subject_id, d.aspect.value),
        )
    )


def uncovered_aspects(implemented: Iterable[Aspect]) -> tuple[Aspect, ...]:
    """``implemented`` 里**没有**的、而承诺要维护的那些面，按 :data:`ALL_ASPECTS` 次序。

    §11 逐字：「R2 必须给官网/责任关系/联系人提供真实刷新执行器；**未接的 aspect 报
    coverage gap，不能通过 out_of_scope 把必需功能从验收分母删掉**」。

    所以这是一个**分母**，不是一个提示。``out_of_scope`` 说的是「这一轮这些格子
    没人管」，而本函数回答的是更早的一个问题：「这个部署**承诺**要维护的面里，
    有几面根本没有实现」。前者随库存变，后者只随部署变 —— 把它算进 ``out_of_scope``
    会得到一个每轮都在动的数字，而「有两面从来没实现过」是个静态事实。

    传进来的不是 :class:`Aspect` 成员时与 :func:`~marketops.application.maintenance.split_by_aspect`
    同处置（失败关闭）：一个不相等的值会让**每一个**面都被判成「没实现」，
    于是报告上写着「七面全缺」，而那看起来只是悲观，不是错误。
    """
    wanted: set[Aspect] = set()
    for aspect in implemented:
        if not isinstance(aspect, Aspect):
            raise ValueError(
                f"implemented 里出现了 {aspect!r}（{type(aspect).__name__}），不是 Aspect 成员。\n"
                "一个不属于闭集的值在这里不与任何面相等，于是七面全被判成「未接」——"
                "报告上那是七行 coverage gap，而真相是有人传错了一个字符串。"
            )
        wanted.add(aspect)
    return tuple(aspect for aspect in ALL_ASPECTS if aspect not in wanted)


def _merge_cell(group: Sequence[DueWork]) -> DueWork:
    """同一个 ``(主体, aspect)`` 上的若干条到期事实 → 一件工作，**保留全部理由**。

    三条判据，各回答一个「不这样会怎样」：

    * **周期与时刻取自最强的那一条理由**（同一理由有多条时取 ``due_at`` 最晚的那条）。
      取最晚的那条信号，是因为我们即将做的那一次观测发生在 ``now ≥ 它们的时刻``，
      它**回答了全部信号**；取最早的那条会让每来一条新信号都复用旧键，
      于是「信号提前触发的那一次」在第二次之后就不再是一件新工作。
    * **``due_at`` 取全部理由里最早的那个**。它回答的是「这一件从什么时候起该做」，
      而只要有任何一条理由说该做了，答案就是那个最早的时刻 —— 用最强理由的时刻
      会让一条早该做的时限工作在报告里看起来比实际晚。
      （``AspectState.next_due_at`` 取的就是它，于是「本轮等了多久」不会被
      一条后到的信号缩短。）
    * **``reasons`` 按强弱排好**，读法见 :data:`_STALE_PRECEDENCE`。

    单条的那一路原样返回：合并是**没有发生**的事，不该在账上留下任何痕迹
    （重新构造一遍会换掉 ``model_copy`` 才保得住的那些字段，而报告读的正是它们）。
    """
    if len(group) == 1:
        return group[0]
    strongest: DueWork | None = None
    for reason in _STALE_PRECEDENCE:
        peers = [item for item in group if item.reason is reason]
        if peers:
            strongest = max(peers, key=lambda item: item.due_at)
            break
    if strongest is None:  # pragma: no cover —— reasons 非空，且每条都在表里
        raise ValueError(
            f"{group[0].subject_kind}/{group[0].subject_id}/{group[0].aspect.value}："
            f"到期理由是 {sorted(r.value for item in group for r in item.reasons)}，"
            "而 domain.maintenance._STALE_PRECEDENCE 里一个都没有。"
        )
    reasons = tuple(
        reason
        for reason in _STALE_PRECEDENCE
        if any(reason in item.reasons for item in group)
    )
    return strongest.model_copy(
        update={"reasons": reasons, "due_at": min(item.due_at for item in group)}
    )


class AspectStatus(str, Enum):
    """一格现在**是什么状态**。三个取值各自对应一种处置，不是三档严重程度。

    * :attr:`CURRENT` —— 还没到下一次该看它的时间。处置：记着它，别动。
    * :attr:`DUE` —— 有基线，且现在该看。处置：排进这一轮。
    * :attr:`NEVER_OBSERVED` —— 从来没有核过。它也**到期**，但不与前者合并：
      前者是「按计划该复核了」，后者是「这一格还没有任何基线」。两者的处置都是
      「去看」，但报告上必须分得开 —— 一个说「维护跑得对不对」，一个说
      「这个市场建起来了没有」。
    """

    CURRENT = "current"
    DUE = "due"
    NEVER_OBSERVED = "never_observed"


class AspectState(StrictModel):
    """一个 ``(主体, aspect)`` 格子的当前状态。§11 逐字要的五个字段在这里。

    ## 它是**读法**，不是第二处判定

    「这格到期了没有」只有一处判定（:func:`what_is_due`）。这个模型是那份判定的
    **读法**：给每一格——包括**没到期**的那些——一个结论，好让人问「这个市场现在
    有哪些格子是旧的」，而不是只能问「下一轮要做什么」。两者由同一份判定导出，
    所以不会出现「报告说它新、选择器说它该做」这种对不上的局面（有一个测试专门
    压这一条：两边逐格同集合）。

    ## 一格一个结论，工作却是「一个理由一件」

    同一格可能**同时**有时限到点与一条变更信号，于是 :func:`what_is_due` 给出
    **两件**工作，而这里只留**一个** ``stale_reason``。这不是偷懒，是两个问题：
    选择器要回答「有几件事要做」（漏一件就漏一次复核），状态要回答「这一格为什么
    是旧的」（一个格子说两个理由，读的人还得自己排优先级）。

    留下的那一个按固定次序取，见 :data:`_STALE_PRECEDENCE`。
    """

    subject_kind: str
    subject_id: str
    aspect: Aspect
    #: 上一次**真的拿到这一面的内容**是什么时候。从未核过 ⇒ ``None``。
    #:
    #: §11 把这个字段叫 ``last_verified_at``，而本仓的观测记录叫
    #: ``Observation.observed_at``。两个名字在这里指同一件事，不是两件事：那一格的
    #: 语义**逐字**是「我们真的拿到了这一面的内容」（见 :class:`Observation`），
    #: 一次 re-read 不产生观测。复核节奏量的正是这个时刻，所以读法用它。
    last_verified_at: datetime | None = None
    #: 下一次该看它的时刻 —— 对一格**已经逾期的**来说，是**本轮逾期的起点**
    #: （最近一次本该到期的时刻，与 :attr:`DueWork.due_at` 同一个取值），于是它与
    #: 现在之差恒小于一个周期：「本轮等了多久」。跨轮的总时长不在这里，它在
    #: ``DueWork.period`` 的 ``cycle-N`` 上 —— 两个量分开，是因为读的人问的是
    #: 「现在要不要动手」（前者）与「这一格掉了多少轮」（后者）两件不同的事。
    #:
    #: 从未核过 ⇒ ``None``（见下面那条说明）。
    next_due_at: datetime | None = None
    status: AspectStatus
    #: 为什么它是旧的。``CURRENT`` 时为 ``None`` —— 不是因为「没有理由」，
    #: 而是因为**它不旧**。
    stale_reason: DueReason | None = None
    #: 这个结论是按**哪一份**复核策略算出来的（见
    #: :attr:`RefreshPlan.policy_version`）。
    policy_version: str = ""

    @field_validator("last_verified_at", "next_due_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("时刻必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @model_validator(mode="after")
    def _status_matches_reason(self) -> "AspectState":
        """状态与理由是**同一件事的两面**，自相矛盾的行不许被构造出来。

        读法这一侧最容易出的错不是算错，而是**两个字段各自算一遍**：于是会出现
        「``status=CURRENT`` 却带着一个 ``stale_reason``」这种行 —— 它读起来像
        「它既新又旧」，而它在库里、在报告里都不会自己报错。
        """
        if (self.status is AspectStatus.CURRENT) != (self.stale_reason is None):
            raise ValueError(
                f"{self.subject_kind}/{self.subject_id}/{self.aspect.value}："
                f"status={self.status.value} 与 stale_reason="
                f"{None if self.stale_reason is None else self.stale_reason.value} "
                "互相矛盾。CURRENT 是「不旧」，它没有理由；反过来，一个理由是旧的，"
                "那这一格就是旧的。"
            )
        if self.status is AspectStatus.NEVER_OBSERVED:
            if self.last_verified_at is not None:
                raise ValueError(
                    "status=never_observed 却有 last_verified_at —— 这一格到底核过没有，"
                    "两个字段说了两件事。"
                )
            if self.stale_reason is not DueReason.NEVER_OBSERVED:
                raise ValueError(
                    "status=never_observed 的理由只能是 never_observed，"
                    f"这里是 {self.stale_reason}。"
                )
        return self


def aspect_states(
    *,
    plan: RefreshPlan,
    observations: tuple[Observation, ...],
    signals: tuple[ChangeSignal, ...] = (),
    now: datetime,
    subjects: tuple[tuple[str, str], ...] = (),
) -> tuple[AspectState, ...]:
    """每一格的当前状态（**含没到期的那些**）。纯函数，与 :func:`what_is_due` 同源。

    输入比 :func:`what_is_due` 少 ``scene_id`` / ``scene_version``：那两个是幂等键的
    一部分（「这件事是哪一版 Scene 上的」），而「这一格现在旧不旧」与那一版无关 ——
    它只与「上次什么时候看的、策略说多久看一次、现在几点」有关。少这两样不是疏漏，
    是这个读法的结果里本来就没有它们的位置。

    返回值按 ``(subject_kind, subject_id, aspect)`` 排序（:data:`ALL_ASPECTS` 的次序
    在 aspect 那一段上），于是两次读的报告可以逐行比对。

    ``next_due_at`` 对**从未核过**的格子是 ``None``，尽管它**此刻就是到期的**。
    这里不写 ``now``：那样每读一次它就变一次，而「什么都没变」的东西不该有一个
    每秒都动的值。这一格没有过任何节奏，于是没有「下一次」可写 —— 它欠的是**第一次**。
    """
    if now.tzinfo is None:
        raise ValueError("now 必须带时区")
    now = to_utc(now)

    latest = _latest_observations(observations)
    known = _known_subjects(observations, subjects)

    # 判定只做一次：把选择器的结论按格子收好，这里只负责读。
    items: dict[tuple[str, str, Aspect], list[DueWork]] = {}
    for work in what_is_due(
        scene_id="",  # 本读法的结论与哪一版 Scene 无关，见 docstring
        scene_version=0,
        plan=plan,
        observations=observations,
        signals=signals,
        now=now,
        subjects=subjects,
    ):
        items.setdefault((work.subject_kind, work.subject_id, work.aspect), []).append(work)

    states: list[AspectState] = []
    for subject_kind, subject_id in sorted(known):
        for aspect in ALL_ASPECTS:
            seen = latest.get((subject_kind, subject_id, aspect))
            fired = items.get((subject_kind, subject_id, aspect), [])
            if seen is None:
                states.append(
                    AspectState(
                        subject_kind=subject_kind,
                        subject_id=subject_id,
                        aspect=aspect,
                        status=AspectStatus.NEVER_OBSERVED,
                        stale_reason=DueReason.NEVER_OBSERVED,
                        policy_version=plan.policy_version,
                    )
                )
                continue
            if not fired:
                states.append(
                    AspectState(
                        subject_kind=subject_kind,
                        subject_id=subject_id,
                        aspect=aspect,
                        last_verified_at=seen.observed_at,
                        next_due_at=seen.observed_at
                        + timedelta(days=plan.rule_for(aspect).after_days),
                        status=AspectStatus.CURRENT,
                        policy_version=plan.policy_version,
                    )
                )
                continue
            reasons = {item.reason for item in fired}
            stale_reason = next((r for r in _STALE_PRECEDENCE if r in reasons), None)
            if stale_reason is None:  # pragma: no cover —— 有测试钉住这张表覆盖全部理由
                raise ValueError(
                    f"{subject_kind}/{subject_id}/{aspect.value}："
                    f"到期理由是 {sorted(r.value for r in reasons)}，而 "
                    "domain.maintenance._STALE_PRECEDENCE 里一个都没有。"
                )
            states.append(
                AspectState(
                    subject_kind=subject_kind,
                    subject_id=subject_id,
                    aspect=aspect,
                    last_verified_at=seen.observed_at,
                    # 报的是**本轮逾期的起点**（与 :attr:`DueWork.due_at` 同一个取值，
                    # 也就是最近一次本该到期的时刻）。拖到第几轮不在这个字段里 ——
                    # 那由 ``period="cycle-N"`` 记着。于是按它排序就是「在本轮里
                    # 等得最久的排在前面」，而它与 now 的差恒小于一个周期。
                    next_due_at=min(item.due_at for item in fired),
                    status=AspectStatus.DUE,
                    stale_reason=stale_reason,
                    policy_version=plan.policy_version,
                )
            )
    # 构造次序**就是**返回值次序（``sorted(known)`` × ``ALL_ASPECTS``），所以这里不再
    # 排一遍：排两遍等于把「次序是什么」写成两处，而两处迟早会有一处被改动。
    return tuple(states)


__all__ = [
    "Aspect",
    "ALL_ASPECTS",
    "REQUIRED_ASPECTS",
    "RefreshRule",
    "RefreshPlan",
    "Observation",
    "ChangeSignal",
    "DueReason",
    "RecheckState",
    "DueWork",
    "due_dedupe_key",
    "what_is_due",
    "uncovered_aspects",
    "AspectStatus",
    "AspectState",
    "aspect_states",
    "MaintenanceState",
    "MaintenanceClaim",
    "ClaimOutcome",
    "claim_of",
    "SourceDelta",
]


class MaintenanceState(str, Enum):
    """一次复核走到哪一步。

    三个成员，**没有「跳过」**：跳过在调度器眼里就是「没做过」，
    而它会在报告里长成「做过了、没问题」。
    """

    CLAIMED = "claimed"
    DONE = "done"
    FAILED = "failed"


class MaintenanceClaim(StrictModel):
    """一次复核的**回执**（同时也是它的租约）。

    ## 为什么回执与租约是同一个对象

    派发侧要回答两个问题：「这件事有没有人在做」（租约）与「上次做成了什么」
    （回执）。做成两个对象，就得让它们保持同步 —— 而它们唯一必须同步的时刻
    正是**进程崩在中间**的那一刻，也就是同步最不可靠的那一刻。合成一个，
    崩溃留下的是「一条停在 ``CLAIMED`` 的回执」，而那一格因此显示为
    「有人在跑，或者上次跑挂了」—— 这正是真话。

    由此也定下「重试」的语义：``FAILED`` 的回执可以被再次认领，那次会产生
    **下一条**回执（``attempt`` 加一）。**``CLAIMED`` 不按时钟自动失效** ——
    与 ``AttemptState`` 同一条纪律：猜错「那个 worker 死了」的代价是两件昂贵的
    事同时在做，而账上只显示「做了两次」。

    ## 为什么同一次工作的多次尝试是**多条记录**

    一条回执一次性覆盖写（失败就改这一行）会让「这一格核过几次、失败过几次」
    这个问题永远回答不了 —— 那一列永远是 1。所以身份是
    ``(dedupe_key, attempt)``：去重身份完全由 ``dedupe_key`` 承载（同格同周期），
    而尝试次数由行数承载。

    ## ``result_ref`` 与 ``failure`` 是两个字段

    「做完了，什么都没变」是一个**成功**的回执（复核的意义之一就是确认没变），
    它与「失败了」在报告上必须分得开。合成一个 ``detail: str``，第一次有人
    把失败原因写进 ``detail`` 之后，统计里那一格就永远是绿的。
    """

    dedupe_key: str
    workspace: str
    scene_id: str
    scene_version: int
    subject_kind: str
    subject_id: str
    aspect: Aspect
    reason: DueReason
    period: str
    state: MaintenanceState
    claimed_at: datetime
    finished_at: datetime | None = None
    #: 这是第几次尝试。第一次就是 1 —— 不是 0：一条存在的回执一定是被认领过的。
    attempt: int = 1
    result_ref: str = ""
    failure: str = ""

    @field_validator("claimed_at")
    @classmethod
    def _claimed_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("claimed_at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("finished_at")
    @classmethod
    def _finished_at_aware(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("finished_at 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("dedupe_key")
    @classmethod
    def _key_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("dedupe_key 不得为空 —— 没有键的回执无法被去重")
        return value

    @field_validator("attempt")
    @classmethod
    def _attempt_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("attempt 从 1 起")
        return value

    @model_validator(mode="after")
    def _state_matches_receipt(self) -> "MaintenanceClaim":
        if self.state is MaintenanceState.CLAIMED:
            if self.finished_at is not None:
                raise ValueError("还在 CLAIMED 的回执不得有完成时刻")
            if self.result_ref or self.failure:
                raise ValueError("还在 CLAIMED 的回执不得带产出或失败原因")
        else:
            if self.finished_at is None:
                raise ValueError(f"{self.state.value} 的回执必须有完成时刻")
        if self.state is MaintenanceState.DONE and self.failure:
            raise ValueError("DONE 的回执不得带失败原因")
        if self.state is MaintenanceState.FAILED and not self.failure.strip():
            raise ValueError(
                "FAILED 必须写明原因。没有原因的失败与「有人放弃了这个任务」"
                "在报告上无法区分，而后者要追问是谁放弃的"
            )
        return self

    @property
    def retryable(self) -> bool:
        return self.state is not MaintenanceState.DONE


class ClaimOutcome(StrictModel):
    """一次认领的结果：**这条回执是不是我写的**。

    ## 为什么必须有这个字段，而不是让调用方比对时刻

    原来的判法是「``claimed_at`` 是否等于我请求的那个 ``at``」。它能挡住绝大多数
    并发，但挡不住[同一个瞬间]：两个驱动各自的 ``now`` 若恰好相同（同一微秒、
    同一秒的粗粒度时钟、或一个把 ``now`` 参数化的测试），**两个都会认为自己拿到了**，
    于是同一格被做两遍 —— 而账上是两行 ``attempt=1``，看起来像「各做了一次」。

    那不是一个概率问题：``now`` 是**参数**（这一层刻意没有时钟），所以「两个驱动
    传同一个 ``now``」在一个可控时钟的测试里是必然会发生的事。

    真正知道答案的只有存储：它执行了那一次插入，或者没有。所以由它回话。

    ## ``won=False`` 不是一个错误

    它有三种来源，且都不需要调用方补救：别人正在跑（``CLAIMED``）、别人已经跑完了
    （``DONE``）、或者我刚失败而别人先重试了（``attempt`` 已被占）。三者的处置相同
    ——**这一格不是我的**——所以这里只有一个布尔，没有原因码。要问道理就读
    ``claim``：它的 ``state`` 与 ``attempt`` 就是那三种情况的完整区分。
    """

    claim: MaintenanceClaim
    won: bool

    @property
    def state(self) -> MaintenanceState:
        return self.claim.state

    @property
    def attempt(self) -> int:
        return self.claim.attempt


def claim_of(due: DueWork, *, workspace: str, at: datetime) -> MaintenanceClaim:
    """把一件到期工作变成一条待认领的回执。纯函数。

    ``reason`` 逐字带过来，**不重新判定**：这一格为什么到期是 ``what_is_due``
    在看过观测与信号之后下的结论，到了这里已经没有它看过的那些输入了 ——
    重判只能靠猜。回执上留这一条是为了让「这一格是周期到了，还是被一条变更
    信号提前拉过来的」在事后可读，而两者的处置不同（前者是例行，后者要去
    看那条信号说了什么）。
    """
    at = to_utc(at)
    return MaintenanceClaim(
        dedupe_key=due.dedupe_key,
        workspace=workspace,
        scene_id=due.scene_id,
        scene_version=due.scene_version,
        subject_kind=due.subject_kind,
        subject_id=due.subject_id,
        aspect=due.aspect,
        reason=due.reason,
        period=due.period,
        state=MaintenanceState.CLAIMED,
        claimed_at=at,
    )


class SourceDelta(StrictModel):
    """一次来源供给复核的结果：**两件不能相加的事**。

    §4 逐字：「新来源缺口 ≠ 真实场地新增 —— 分开数」。

    这两件事在现象上长得几乎一样（都是「这次多出来一些东西」），但在业务上不同：

    * ``new_source_gaps`` —— 我们知道有个场地，但**没有任何来源覆盖它**。
      它是一个**待办**：要么去找来源，要么承认这条线走不通。
    * ``new_venues`` —— 来源里**真的出现了**一个此前不存在的场地。
      它是一次**发现**：母池该长大一个。

    把它们合成一个「新增 N 项」，会让「这次网络上一无所获，只是我们终于承认
    有一批场地没有任何来源」读成一次成功的发现 —— 而它真实的含义恰恰是
    **这一轮没有发现任何东西**。反过来也会错：一次真的发现会被当成一批新的欠账。

    ## 为什么没有 ``total``

    不是漏了。加一个 ``total`` 就等于把上面那个合成数提供了出来，而它只要存在，
    迟早会有人读它、写它、把它放进周报。两个计数各自都能是 ``None``（见下），
    一个 ``None`` 参与的求和只能靠「当成 0」来收场，而那正是 §3.4 禁止的
    「未知不得写成 0」。判据：``tests/test_domain_maintenance.py`` 里断言
    本类**没有**名为 ``total`` 的属性 —— 缺一个属性是自觉，断言它缺席才是判据。

    ## 两个 ``None`` 都不是 0

    ``None`` 的意思是「这一次没有测这一项」，与「测了，是 0」是两件事。
    ``unmeasured`` 把它们列出来，好让报告能说「这一格的来源增量没测」，
    而不是显示两个零。
    """

    new_source_gaps: int | None = None
    new_venues: int | None = None

    @field_validator("new_source_gaps", "new_venues")
    @classmethod
    def _non_negative(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError(
                "计数不得为负。负数在这里只能来自「把两个数相减」—— 而这两个数"
                "本就不可相减（见类 docstring）"
            )
        return value

    @property
    def unmeasured(self) -> tuple[str, ...]:
        """哪几项这次没测。空元组 = 两项都测了。"""
        return tuple(
            name
            for name, value in (
                ("new_source_gaps", self.new_source_gaps),
                ("new_venues", self.new_venues),
            )
            if value is None
        )

    @property
    def measured(self) -> bool:
        """两项都测了才算测过。**一项测了、另一项没测不是「部分可用」。**"""
        return not self.unmeasured

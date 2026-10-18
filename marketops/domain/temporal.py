"""时间性与新鲜度：一个事实**在什么时候**为真。

这个模块存在的理由是一个具体的、会真实发生的错误读法：

    一份 2021 年的理事会 PDF 指名 Jane 是场地主管。今天把它抓下来，
    系统于是认为「Jane 是场地主管」。

它证明的只是 **Jane 在 2021 年是场地主管**。中间隔着五年，她可能早已离任、
换岗，或者那个岗位本身已经被撤销。而如果数据类型上只有 ``fetched_at``，
上述两种读法在数据里**长得一模一样** —— 报告上分不出来。

所以时间被拆成四个互不相同的时刻：

=========================  ====================================================
``source_published_at``    来源自己声明的发布时间。**可能没有**（很多页面不写）
``observed_at``            我们第一次看到这条内容的时刻
``last_verified_at``       我们**最近一次确认它仍然成立**的时刻
``valid_from`` / ``valid_to``  事实自身的有效区间，若来源明说了
=========================  ====================================================

``observed_at`` 与 ``last_verified_at`` 的区别是最容易被抹掉的一条：
**今天抓到一个旧文档，不等于今天验证了那个事实。** 前者只写 ``observed_at``，
后者才写 ``last_verified_at``。把两者当同一个字段，就等于让「重新抓取」
自动变成「重新验证」。

## 反方向的第二个错误：没找到一个人，不等于这个人走了

上面那个错误是**把旧的读成新的**。还有一个方向相反、但同样会真实发生的：

    上一轮这条路线在联系页上。这一轮重新抓了那一页，没看到它。
    系统于是认为「这个人已经不在职了」。

而「这一轮没看到」与「这个人离开了」在证据上**同形**：页面改版、条目被挪到
另一页、抓到的是缓存的旧版本、JS 没跑起来、站点临时把一个栏目下线 —— 全都长成
「没看到」。所以 ``Currentness`` 里有专门的一档 :attr:`Currentness.NOT_SEEN`
（``docs/DATA_MODEL.md`` 的原话是「no longer visible but not proven false」），
它**不是** ``HISTORICAL``（那是来源自己划定了终点），也**不是** ``STALE``
（那是「没人说过它什么时候结束」）—— 它是一次**观测失败**，触发的是复查，
不是任何形式的结论。相应地，本模块**不产出**任何离职日期：``valid_to`` 只由
来源声明的区间写入（见 :func:`evaluate_freshness` 里读 ``valid_to`` 的那一支）。

判据在同一个函数里，但走的是**另一条输入**：时间锚点算不出「没看到」，
只有一次真的复查才能（``seen_on_recheck``）。缺省 ``None`` 的意思是
**这一轮没有做复查**，而不是「看到了」。这两件事在今天的判据上恰好同效
（「又看到了」本身不产生结论 —— 见 :func:`evaluate_freshness`），但在日志与
诊断里是两句不同的话：``True`` 在那里读起来是一句我们**没做过**的声明。
所以这一格今天真正的分辨力只有「``False`` 与其它」两档，这一点写在这里
而不是假装它有两条判据。

本模块只做两件事：承载这些时刻，以及**按策略算出 currentness**。
它不认识任何具体的时限 —— 时限是业务口径，住在 ``profiles/`` 里。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, model_validator

from .base import StrictModel as _Strict
from .util import sha256_text


class Currentness(str, Enum):
    """一条时间性断言**此刻**的有效性。

    :attr:`HISTORICAL` 与 :attr:`STALE` 的区别不是程度，是**原因**，也是下一步动作：

    * ``HISTORICAL`` —— **来源自己划定了这段时间的终点**。一份写明
      「Jane 于 2018–2021 年任场地主管」的交接记录，是在说这段任职已经结束。
      它是对的，不需要重新验证，只是需要按「那时」来读。
    * ``STALE`` —— **没人说过它什么时候结束**。一份 2021 年的页面只写
      「场地主管：Jane」，没有终止日期。它曾经是当前的，但我们没有任何依据说
      它今天还成立。它需要的不是历史解读，而是**重新验证**。

    这条界线决定了下游该做什么，所以合不成一个「旧」字。

    注意 ``HISTORICAL`` 的判据是**来源声明的区间**，不是谁给它贴的标签 ——
    否则本枚举的取值就会自我指认（见 :func:`evaluate_freshness` 的说明）。

    :attr:`NOT_SEEN` 是第三个方向，与上面两档都不同：**我们再去看了一次，
    那次没看到它**。``docs/DATA_MODEL.md`` 把它记作「no longer visible but not
    proven false」，本枚举照抄那句话。它既不是 ``HISTORICAL``（来源说了终点）
    也不是 ``STALE``（时间过去了而没人说终点），差别不在措辞而在**依据**：
    前两者的依据都在**材料**里，而它的依据是一次**观测**——观测失败的原因有
    很多种，它们与「这个人离开了」同形。所以它不支撑任何业务判定
    （:attr:`Freshness.is_actionable` 为假），只触发复查。
    """

    CURRENT = "current"
    HISTORICAL = "historical"
    STALE = "stale"
    #: 复查时**没有**再看到这条断言。不是「结束了」，见本枚举的说明。
    #:
    #: 它今天没有生产调用方（``seen_on_recheck`` 的缺省是「这一轮没做复查」），
    #: 与 :attr:`CONFLICTING` 一样是**刻意**留着的：没有这一档，第一个实现复查
    #: 的人就只能顺手挑一个更近的东西（``STALE``，或者一个 ``valid_to`` 日期），
    #: 而挑出来的那个会是错的。判据与它的测试现在就位，等 §11 的复查循环来接。
    NOT_SEEN = "not_seen"
    #: 存在与本断言不相容的另一条断言，且无法判定谁新谁对。
    CONFLICTING = "conflicting"
    #: 时间锚点不足，或来源没提供任何可用时刻 —— 无法判定。
    UNKNOWN = "unknown"
    #: **来源自述的发布时刻晚于我们观测到它的时刻** —— 这条断言的时刻不可用。
    #:
    #: 它与本枚举其余五档都不同，差别不在程度而在**种类**：那五档说的是「这条
    #: 断言的时间依据够不够」，本档说的是「这条断言声称的时间本身不成立」。一条
    #: 页面写着 2027 年发布、而我们在 2026-09 抓到了它，那个时刻不是旧、不是新、
    #: 也不是缺 —— 是**算出来的**：它多半是把排期、模板变量或另一个年号解析成了
    #: 发布时间。所以它既不能读成 ``STALE``（「时间过去了」—— 恰恰相反），也不
    #: 能读成 ``UNKNOWN``（「没有可用时刻」—— 我们有一个，只是它不可能是对的）。
    #:
    #: 这一档有生产者，而且它存在的理由就是那个生产者的处境：解析出这种时刻的是
    #: 一个坏页面，而坏页面不该把整轮研究打断（§5 的 fail-soft）。它不支撑任何
    #: 业务判定（:attr:`Freshness.is_actionable` 为假），但**要留下痕迹** ——
    #: 静默把那个日期钳到今天，会让一行本来说不清年纪的证据看着像刚刚验证过。
    INVALID_SOURCE_TIME = "invalid_source_time"


class FreshnessReason(str, Enum):
    """为什么算出这个 currentness。审计时比取值本身更重要。"""

    #: 来源发布时间在策略时限内。
    WITHIN_POLICY_WINDOW = "within_policy_window"
    #: 来源发布时间早于时限，但另有更新的验证时刻。
    RECENTLY_REVERIFIED = "recently_reverified"
    #: 来源发布时间早于时限，且没有更新的验证时刻。
    BEYOND_POLICY_WINDOW = "beyond_policy_window"
    #: 来源发布时间早于时限，且断言自述它描述的是过去的状态。
    DESCRIBES_A_PAST_STATE = "describes_a_past_state"
    #: 被另一条断言反驳，且两者都无法胜出。
    CONTRADICTED = "contradicted"
    #: 没有任何可用的时间锚点。
    NO_TIME_ANCHOR = "no_time_anchor"
    #: 复查过这一行，那次**没有**再看到它。
    #:
    #: 与 :attr:`BEYOND_POLICY_WINDOW`（「时间过去了，没人说终点」）分开，是因为
    #: 下游的动作不同：一个是重新验证，另一个是**回那个来源看它现在在哪一页**。
    #: 而与 :attr:`DESCRIBES_A_PAST_STATE` 分开是更要紧的一条 —— 后者说的是
    #: 「来源声明了终点」，那是材料里的事实；本档说的是「我们这次没看到」。
    #: 把两者合成一句，等于把一次观测失败写成一份离职通知。
    NOT_SEEN_ON_RECHECK = "not_seen_on_recheck"
    #: ``source_published_at`` 晚于 ``observed_at``：来源自述的发布时刻不可能。
    #:
    #: 这一档的措辞就是判据本身（``source_published_at > observed_at``），因为
    #: 下游读到的应当是可复核的那句话，而不是一个「日期错了」的说法 —— 后者会
    #: 被读成「解析器坏了」，而它同时是「这一页在说一件还没发生的事」。
    INVALID_SOURCE_TIME = "invalid_source_time"


class FreshnessPolicy(_Strict):
    """判定新鲜度的时限。**形状**在本层，**取值**在 ``profiles/``。

    两个阈值而不是一个，是因为「还能用」和「需要重新验证」不是同一条线。
    """

    policy_version: str
    #: 来源发布时间在这个天数以内 → 仍然算 current。
    current_within_days: int
    #: 超过这个天数 → 连「可能还成立」都不成立，直接 stale。
    historical_after_days: int
    #: 这两个数字有没有**标定**过。默认 ``False`` —— 缺省是「未标定」，不是「已批准」。
    #:
    #: 与 ``domain.maintenance.RefreshRule.calibrated`` 是同一条纪律、同一个字段名：
    #: 一组看起来合理的天数（讨论里出现过 180 / 365 / 730）**不是业务事实** ——
    #: 它们没有一个来自获批策略或留出评估。未标定的策略照样会被用来判（否则系统
    #: 什么都不做），但每一份产出都必须说得出它用的是未标定的那一份。
    calibrated: bool = False

    @model_validator(mode="after")
    def _windows_are_ordered(self) -> "FreshnessPolicy":
        if self.current_within_days < 0:
            raise ValueError("current_within_days 不得为负 —— 负数会让一切立刻过期")
        if self.historical_after_days <= self.current_within_days:
            raise ValueError(
                f"historical_after_days ({self.historical_after_days}) 必须大于 "
                f"current_within_days ({self.current_within_days})。"
                "否则两档窗口不可区分，「还能用」与「需要重验」会算出同一个值。"
            )
        return self


def assertion_id(*, subject_ref: str, predicate: str, value: str, observed_at: datetime) -> str:
    """断言的 id 是内容寻址的。

    ``observed_at`` 进哈希是**刻意**的：同一句「Jane 是场地主管」在 2021 年
    读到和在 2026 年读到是两条断言，不是同一条被更新了的断言。
    把它们合并，就等于让「她当年是」悄悄变成「她现在也是」。
    """
    return sha256_text(
        "\x1f".join(["assertion", subject_ref, predicate, value, observed_at.isoformat()])
    )[:32]


class TemporalAssertion(_Strict):
    """一条带时间的事实断言。"""

    assertion_id: str
    #: 断言的主语，形如 ``account:<id>`` / ``role:<role_id>`` / ``asset:<id>``。
    subject_ref: str
    #: 断言的谓语，如 ``holds_role`` / ``responsible_for`` / ``published_contact_email``。
    predicate: str
    value: str

    # --- 四个时刻（见模块 docstring）---
    source_published_at: datetime | None = None
    observed_at: datetime
    last_verified_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    #: 这条断言**自述**的时间状态。默认 ``UNKNOWN``（尚未判定）—— 这是刻意的：
    #: 一条刚被观测到、还没跑过 :func:`evaluate_freshness` 的断言，
    #: 诚实的取值就是「不知道」，而不是任何一个具体结论。
    #:
    #: 默认值选 ``UNKNOWN`` 还堵住了一条路径：不存在「忘了判 → 默认成了
    #: current」这回事，因为 ``UNKNOWN`` 在 :attr:`Freshness.is_actionable`
    #: 上是不作为的 —— 一条没判过的断言撑不起任何业务结论。
    currentness: Currentness = Currentness.UNKNOWN

    #: 本断言取代了哪些更早的断言。
    supersedes: list[str] = Field(default_factory=list)
    #: 哪些断言与本条不相容。
    contradicted_by: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _times_are_timezone_aware(self) -> "TemporalAssertion":
        for name in (
            "source_published_at",
            "observed_at",
            "last_verified_at",
            "valid_from",
            "valid_to",
        ):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(
                    f"TemporalAssertion.{name} 是 naive datetime。"
                    "跨时区的比较会静默给出错误的先后关系，所以全项目要求带时区。"
                )
        return self

    @staticmethod
    def impossible_source_time(
        source_published_at: datetime | None, observed_at: datetime
    ) -> str:
        """这条断言的来源时刻不可能吗？不可能时返回那句理由，否则返回空串。

        这是这条判据的**唯一**定义处，两个调用点各按自己的需要读它：下面的校验器
        要在错的时候抛，而 :func:`~marketops.application.freshness.assertion_for`
        要在构造**之前**问一次 —— 它那边要的是一个类型化的结果，不是一次异常
        （见 ``Currentness.INVALID_SOURCE_TIME``：一个坏页面的未来日期只该弄脏它
        自己那一行，不该让整轮研究停下来）。

        判据写两遍就会出现这样的后果：校验器说这行不合法，而调用点按自己那份副本
        判它合法 —— 于是要么继续抛（fail-soft 没做成），要么两处对同一个时刻给出
        不同答案。这里返回的是**理由文本**而不是布尔，是为了让两个调用点报出同一
        句话；校验器不再有自己的措辞。
        """
        if source_published_at is None or source_published_at <= observed_at:
            return ""
        return (
            f"source_published_at ({source_published_at.isoformat()}) 晚于 "
            f"observed_at ({observed_at.isoformat()}) —— 不可能在它发布之前就观测到它。"
            "出现这种数据通常是页面上的日期被解析错了（例如把未来的排期当成了发布时间）。"
        )

    @model_validator(mode="after")
    def _cannot_be_observed_before_it_existed(self) -> "TemporalAssertion":
        reason = self.impossible_source_time(self.source_published_at, self.observed_at)
        if reason:
            raise ValueError(reason)
        return self

    @model_validator(mode="after")
    def _validity_window_is_ordered(self) -> "TemporalAssertion":
        if (
            self.valid_from is not None
            and self.valid_to is not None
            and self.valid_to < self.valid_from
        ):
            raise ValueError("valid_to 早于 valid_from —— 这个有效区间是空的")
        return self

    @model_validator(mode="after")
    def _current_requires_a_time_anchor(self) -> "TemporalAssertion":
        """**本模块最重要的一条不变量。**

        声称一条断言是「当前的」，却既不知道来源何时发布、也从未重新验证过它，
        这不是一个可以成立的组合 —— 它正好就是那个会把 2021 年 PDF 读成
        2026 年事实的状态。
        """
        if self.currentness is Currentness.CURRENT and self.source_published_at is None:
            if self.last_verified_at is None:
                raise ValueError(
                    "currentness=current 但既没有 source_published_at 也没有 "
                    "last_verified_at —— 没有任何时间锚点能支持「它现在成立」。"
                    "今天抓到一个旧文档不构成当前的验证；"
                    "无法定时的断言应为 unknown 或 historical。"
                )
        return self

    @model_validator(mode="after")
    def _conflicting_requires_a_counterpart(self) -> "TemporalAssertion":
        if self.currentness is Currentness.CONFLICTING and not self.contradicted_by:
            raise ValueError(
                "currentness=conflicting 但没有 contradicted_by —— "
                "「有冲突」必须能指出和谁冲突，否则这个标签无法被复核。"
            )
        return self

    @property
    def has_literal_evidence(self) -> bool:
        return bool(self.evidence_ids)


class Freshness(_Strict):
    """一次新鲜度评估的**结论**，连同它的依据。

    存下来而不是现算，是因为同一个问题在不同的策略版本下会有不同的答案。
    只留一个 ``currentness`` 字段，会让「当时按什么标准判的」永久丢失。
    """

    assertion_id: str
    currentness: Currentness
    reason: FreshnessReason
    policy_version: str
    evaluated_at: datetime
    #: 来源发布到评估时刻的天数；没有发布时间时为 ``None``。
    age_days: int | None = None
    #: 算出这条结论的那份策略**当时**标定过没有（:attr:`FreshnessPolicy.calibrated`）。
    #:
    #: 它不重复 ``policy_version``：同一个版本号下的数字被一位人类**批准**之后，
    #: 版本号不必变（改的不是数字，是「有没有人认过这些数字」），而此前按同一批
    #: 数字算出的每一条结论的解读都变了。所以两个都要存：版本答「按什么算的」，
    #: 这一格答「那套东西当时算不算获批口径」。
    policy_calibrated: bool = False

    @property
    def is_actionable(self) -> bool:
        """这条事实能否直接支撑一个业务判定。

        ``CONFLICTING`` 与 ``UNKNOWN`` **不**算 —— 本仓库的纪律是
        宁可留空，不可猜（SOP v1 L121 只取字面证据、歧义必须保留）。

        ``NOT_SEEN`` 也不算，而且它的理由与那两个都不同：它是一条**观测**
        的结论，不是一条**事实**的结论。「我们这次没看到它」不能支撑任何关于
        这个人的判定 —— 包括「他还在」与「他走了」两个方向。

        ``STALE`` 同样不算，理由与上面三个又都不同：它既不是歧义、不是未知，
        也不是一次观测失败，而是**没有当下的依据** —— 这一格说的是「它曾经
        成立，而且没人说过它何时结束」（见 :class:`Currentness`）。
        「曾经是当前的」与「今天是当前的」是两句话，而这一格手里只有前一句，
        所以它触发的是重新验证，不是任何判定。

        真正的问题是这一格**没法诚实地读**：它不能读成「今天成立」（没有当下的
        依据），也不能读成「那时成立」（没人说过它何时结束）—— 而
        ``HISTORICAL`` 恰恰是可以读成后者的那一档，这也是两者必须分开的理由。

        ``INVALID_SOURCE_TIME`` 不算，理由比上面四个都短：这一行说的时刻**不成立**，
        于是它连「什么时候的事」都答不出来 —— 而任何判定都要先有那一刻。
        这一档与 ``UNKNOWN`` 的分别不在能不能用（都不能），在**要不要有人去看**：
        ``UNKNOWN`` 是材料本来就缺，``INVALID_SOURCE_TIME`` 是一个解析错误，而它
        在报告里必须显形，否则一次坏解析会安静地变成一个「时间不明」的行。
        """
        return self.currentness in (Currentness.CURRENT, Currentness.HISTORICAL)


def evaluate_freshness(
    assertion: TemporalAssertion,
    policy: FreshnessPolicy,
    *,
    now: datetime,
    seen_on_recheck: bool | None = None,
) -> Freshness:
    """按策略算出这条断言此刻的新鲜度。

    这是 :attr:`Freshness.currentness` 的**唯一合法产出途径**（模块 docstring
    说明了为什么）。

    ``seen_on_recheck`` 是**另一条输入**，与时间锚点无关 —— 时间算不出「没看到」：

    * ``None``（缺省）—— **这一轮没有做复查**，本参数不产生任何结论。
      缺省取 ``None`` 而不是取必填，是因为「没做复查」是常态，而忘记传它**失败
      的方向是安全的**（落到按时间判的 ``STALE`` / ``CURRENT``，那是另一句话，
      不是一句「他走了」）。
      它与 ``True`` 在判据上**同效**（下一条：「又看到了」不产生结论），所以这一
      格今天只有「``False`` 与其它」两档分辨力。仍然取 ``None`` 不取 ``True``，
      是因为这一格会出现在日志与诊断里，而那里的 ``True`` 是一句没发生过的声明 ——
      一个缺信息与一句假话，后者更难被发现。这一条**没有判据能分辨**，变异检查里
      那一格是预期存活的（记在提交说明里）。
    * ``False`` —— 复查过，那次没有再看到它 → :attr:`Currentness.NOT_SEEN`。
      **这是一次观测的失败，不是一条关于人的事实。**
    * ``True`` —— 复查时又看到了它。它**不产生新结论**，这句话是刻意的：
      「又看到一次」不等于「重新验证过」（见模块 docstring 里
      ``observed_at`` 与 ``last_verified_at`` 的分工）。真正的重新验证要写
      ``last_verified_at``，那一格不由抓取填。

    判定顺序即语义，先判的胜出：

    1. 被反驳 → ``CONFLICTING``（一件事有两个不相容的版本时，
       先去解决冲突，不要用「哪个更新」把它掩盖过去）
    2. **来源自己划定了终点**（``valid_to`` 已过，或上一轮已判历史）→
       ``HISTORICAL``。它排在 ``NOT_SEEN`` 之前，因为两者指向同一件事时
       材料里的那条更强：来源说「2018–2021 年任场地主管」，而复查时没看到它
       —— 后者在一次成功的复查里本来就该发生（那一页现在写的仍然是历史）。
       让观测失败盖过一条来源声明，等于用「我没看到」删掉一条材料事实。
    3. 复查过而没看到 → ``NOT_SEEN``（只在 ``seen_on_recheck is False`` 时）
    4. 完全没有时间锚点 → ``UNKNOWN``
    5. 在策略窗口内 → ``CURRENT``
    6. 窗口外但近期重新验证过 → ``CURRENT``（重新验证是比发布时间更强的证据）
    7. 其余窗口外 → ``STALE``

    第 3 条排在时间判据之前，因为「复查时没看到」与「时间过去了」是两件事，
    而前者的结论更弱、也更紧急：一条**上个月**发布的路线，复查时没看到它，
    绝不能因为发布时间还新就判成 ``CURRENT`` —— 那等于说「它还在」，
    而这句话正是这次复查没有证实的那一句。

    **本函数读** :attr:`TemporalAssertion.currentness` **，但只读两种情况**，
    且两者都不是本函数的产出：

    * ``HISTORICAL`` —— 一条已经判定为历史的断言，不因重新求值而降级成
      ``STALE``。历史是一条比「旧」更强的结论（来源当时说的是对的），
      重新求值没有理由把它丢掉。
    * 它**从不读** ``CURRENT``。当前性一律由本函数从时间锚点现算，
      所以「上次是 current」不构成「这次也是 current」的任何理由。

    这条区分不是洁癖：如果本函数回头认自己上一次的产出，那么一个过期的
    判定就会靠自我指认永远续期下去。
    """
    anchor = assertion.source_published_at or assertion.last_verified_at
    if assertion.contradicted_by:
        age = (now - anchor).days if anchor is not None else None
        return Freshness(
            assertion_id=assertion.assertion_id,
            currentness=Currentness.CONFLICTING,
            reason=FreshnessReason.CONTRADICTED,
            policy_version=policy.policy_version,
            policy_calibrated=policy.calibrated,
            evaluated_at=now,
            age_days=age,
        )

    # 来源自己划定了一个已经结束的区间（``valid_to`` 已过），或这条断言早已被判为历史。
    # 两者都是「有依据的历史」，而不是「谁给它贴的标签」—— 见本函数 docstring。
    #
    # 算在**这里**而不是等用到时再算，是因为下面有两处要用它：一次是决定「没看到」
    # 让不让位（判据 3），一次是超出窗口之后的 ``HISTORICAL``。两处各写一遍，就会有
    # 一天只改了其中一处 —— 而这两处的语义必须是同一个。否则同一份输入会在一条路径上
    # 判成 ``NOT_SEEN``、在另一条上判成 ``HISTORICAL``，而这两句话对下游的意思是相反的
    # （一次观测失败 / 一条已经读完的历史）。
    past_state = assertion.currentness is Currentness.HISTORICAL or (
        assertion.valid_to is not None and assertion.valid_to < now
    )

    if seen_on_recheck is False and not past_state:
        # 复查过，那次没有看到它。``age_days`` 照实算（算不出来就是 ``None``）：
        # 这条结论说的是**观测**，它不假装自己没有时间锚点，也不拿观测失败去
        # 顶替一个时间结论。
        return Freshness(
            assertion_id=assertion.assertion_id,
            currentness=Currentness.NOT_SEEN,
            reason=FreshnessReason.NOT_SEEN_ON_RECHECK,
            policy_version=policy.policy_version,
            policy_calibrated=policy.calibrated,
            evaluated_at=now,
            age_days=(now - anchor).days if anchor is not None else None,
        )

    if assertion.source_published_at is None:
        # 只有 last_verified_at 可用时，验证时刻本身就是锚点。
        if assertion.last_verified_at is None:
            return Freshness(
                assertion_id=assertion.assertion_id,
                currentness=Currentness.UNKNOWN,
                reason=FreshnessReason.NO_TIME_ANCHOR,
                policy_version=policy.policy_version,
                policy_calibrated=policy.calibrated,
                evaluated_at=now,
                age_days=None,
            )
        age = (now - assertion.last_verified_at).days
        within = 0 <= age <= policy.current_within_days
        return Freshness(
            assertion_id=assertion.assertion_id,
            currentness=Currentness.CURRENT if within else Currentness.STALE,
            reason=(
                FreshnessReason.WITHIN_POLICY_WINDOW
                if within
                else FreshnessReason.BEYOND_POLICY_WINDOW
            ),
            policy_version=policy.policy_version,
            policy_calibrated=policy.calibrated,
            evaluated_at=now,
            age_days=age,
        )

    age = (now - assertion.source_published_at).days
    if age <= policy.current_within_days:
        return Freshness(
            assertion_id=assertion.assertion_id,
            currentness=Currentness.CURRENT,
            reason=FreshnessReason.WITHIN_POLICY_WINDOW,
            policy_version=policy.policy_version,
            policy_calibrated=policy.calibrated,
            evaluated_at=now,
            age_days=age,
        )

    # 窗口外。重新验证时刻若比发布时间更新且仍在窗口内，则以验证为准 ——
    # 一个 2019 年发布、2026 年仍在官网现任名单上的岗位，是当前的。
    if assertion.last_verified_at is not None:
        verified_age = (now - assertion.last_verified_at).days
        if 0 <= verified_age <= policy.current_within_days:
            return Freshness(
                assertion_id=assertion.assertion_id,
                currentness=Currentness.CURRENT,
                reason=FreshnessReason.RECENTLY_REVERIFIED,
                policy_version=policy.policy_version,
                policy_calibrated=policy.calibrated,
                evaluated_at=now,
                age_days=age,
            )

    # 超出窗口之后的最后一支：来源划定了终点（第 2 条判据算出来的那一格）。
    if past_state:
        return Freshness(
            assertion_id=assertion.assertion_id,
            currentness=Currentness.HISTORICAL,
            reason=FreshnessReason.DESCRIBES_A_PAST_STATE,
            policy_version=policy.policy_version,
            policy_calibrated=policy.calibrated,
            evaluated_at=now,
            age_days=age,
        )

    return Freshness(
        assertion_id=assertion.assertion_id,
        currentness=Currentness.STALE,
        reason=FreshnessReason.BEYOND_POLICY_WINDOW,
        policy_version=policy.policy_version,
        evaluated_at=now,
        age_days=age,
    )


__all__ = [
    "Currentness",
    "FreshnessReason",
    "FreshnessPolicy",
    "TemporalAssertion",
    "Freshness",
    "assertion_id",
    "evaluate_freshness",
]

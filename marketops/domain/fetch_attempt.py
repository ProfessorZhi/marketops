"""``FetchAttempt`` —— 一次访问的**实测记录**。采集设计 §3 逐字要它承载的每一格。

    抓取尝试表示一次实测：workspace/run/work item、URL/finalURL/redirect trace、
    方法、内部 connection/egress profile id、开始/结束、status nullable、
    safe response headers、error code、body kind/bytes/hash、parser 版本、
    预算 reservation/settlement、retry_after、action_taken。原始敏感
    headers/cookies/key/IP 不出报告。

## 为什么它不是一个更大的 ``FetchResult``

:class:`~marketops.ports.acquisition.FetchResult` 是**一次抓取在进程内的返回值**：
它带 ``request``、带 ``body_path``、带升级来源，供编排层当场做决定。本模型是
**落盘之后的事后凭证**：它回答的是「三个月后有人问『这个 URL 那天到底怎么样了』」。

两者的差别决定了字段取舍：

* ``FetchResult`` 带路径（``body_path``），本模型带**内容哈希与字节数**。
  路径是存储布局，布局可以改；哈希是那批字节的身份，改布局不改身份。
* 本模型带 ``started_at`` / ``ended_at`` 两个**时刻**，而不是 ``elapsed_ms``。
  一个时长回答不了「这一跳是几点发的」，而跨来源的限流、共享配额与「两个账号
  是否同时撞上同一个挑战」全都要靠时刻。
* 本模型带 ``budget_reservation_id`` / ``budget_settlement`` 两格，而不是一个
  「花了多少钱」。设计 §9 要求「先原子 claim/reserve，再外部调用，再记录外部结果
  与 settlement」；把预留与结算合成一个数，超时未对账的那一笔会与已结算的那一笔
  长得一样。
* 那两格**必须互相说得通**（``_the_settlement_names_its_reservation``）：说走预算的
  那一格必须指得出一份预留，说不走预算的那一格必须什么都没指。理由写在那个校验器上。
  而今天**唯一会写这两格的路径**由 :func:`settlement_of` 把请求上那一格推出来 ——
  于是「哪一格该写什么」只有一处判断，两个抓取器不可能各判各的。

## 观测与根因在**同一个对象上的两个不同字段**

``observed_status`` / ``challenge_detected`` 是观测；``suspected_cause`` /
``cause_evidence_ref`` 是判断。它们并列在这里而不是分成两张表，因为一次实测
只能有一个结论 —— 分成两张表之后，「两者都有值」就成了一种可表达的坏状态。

## 借来的一条纪律：**没有值就是没有值**

``observed_status`` 是 ``int | None``，``None`` 表示「这一次没有观测到 HTTP 状态」。
它不是 ``0``、不是 ``200``、不是「还没填」。设计 §2.5 点名的那处 ``status_code
or 200`` 之所以是承重缺陷，正是因为那个 ``or`` 把「未观测」折成了一个**成功**的
读数；本模型的每一格都拒绝这种折算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator, model_validator

from .access import ActionTaken, SuspectedCause, safe_response_headers
from .base import StrictModel
from .util import sha256_text, to_utc

#: 分隔符。取 ``\x1f``（ASCII 的 unit separator）而不是 ``:`` 或 ``|``：
#: 它不出现在任何 URL、作用域名或 ISO 时刻里，所以「两个不同的组合拼出同一个种子」
#: 不可能发生。用 ``:`` 的话 ``("a:b", "c")`` 与 ``("a", "b:c")`` 会撞在一起，
#: 而撞在一起的后果是两条不同的尝试共用一个 id、被 ``put_attempt`` 当成同一条拒掉。
_SEED_SEPARATOR = "\x1f"


class BodyKind(str, Enum):
    """这一次拿到的正文**是什么**。

    ``NONE`` 与 ``UNKNOWN`` 分开：前者是「我们没有得到任何正文」（被拒绝、
    超时、判据拦下），后者是「得到了正文但我们没能归类它」（例如一个没有
    ``Content-Type`` 且不是 HTML/PDF/JSON 的字节流）。把两者合成一个「空」，
    会让「这次没有正文」与「这次有个奇怪的东西」在报告上同形。
    """

    NONE = "none"
    HTML = "html"
    PDF = "pdf"
    TEXT = "text"
    JSON = "json"
    XML = "xml"
    UNKNOWN = "unknown"


class MaterialProvenance(str, Enum):
    """这一次抓取拿到的**材料是从哪来的**。

    ``LIVE`` —— 正文来自一个真实的第三方公开页面，是那次抓取真的从网上取回来的。
    ``SYNTHETIC`` —— 正文是**我们自己造的**（一个受控夹具、一个本机立起来的靶子、
    或者一份被重放的录制件）。

    这一格的存在理由是 ``docs/MCP_SERVICE.md`` §8.C.2 逐字那两句：Crawl4AI 的渲染
    覆盖可以是「a normal public JS page」也可以是「a clearly labeled supplemental
    controlled fixture」，而紧接着的一句是「**Both kinds must be labeled.**」，
    再一句是「A controlled fixture proves integration behavior only and **cannot
    count as market yield or real-site improvement**」。

    也就是说：两种材料**都**是合法的验收输入，而它们能证明的东西**不同** ——
    一份受控夹具证明的是「这条集成链路是通的」，它证明不了「我们真的能从现场
    拿到东西」。报告上把两者混起来，读的人会把一次自造页面的成功读成一次现场
    覆盖，而那正是本仓反复要消灭的那类读数。

    ## 为什么缺省是 ``SYNTHETIC``

    因为 ``LIVE`` 是一条**肯定声明**，而这一格的消费者是一个**覆盖率**。
    「没有被声明为 live 的行不算 live 覆盖」是这一格的意思本身，不是一次猜测：
    缺省值在这里**收回**一个主张，而不是**编造**一个事实。

    这条区分是它与 ``AccountRecord.country`` 那个著名的缺省（曾经是 ``"UK"``）
    的关键差别 —— 那一个把「没人查过」变成一个**喂给规则求值的事实**，于是
    ``out_of_region`` 对它静默不触发，报告上的读法是「这个账号在区域内」。
    这里没有第二条规则读这一格：它只被数，而数错的**方向是可选的**。
    缺省成 ``SYNTHETIC`` 时，一次忘了声明的现场抓取会被读成「自造材料」——
    覆盖率被**低报**，有人会去看；缺省成 ``LIVE`` 时，一次自造页面会被读成现场
    覆盖 —— 而那正是这一格要挡的那句话。所以缺省取前者。

    ## 为什么是两个成员，不是三个

    第三个成员只能是「没有记录」。而这一格的读者问的是一道**二选一**的题
    （这次渲染算不算现场覆盖），给一个「不知道」的档会让每一处读它的人各自
    决定怎么处置它 —— 而那些决定会不一致。旧行（这一格出现之前写下的）
    因此读成 ``SYNTHETIC``：**低报**，与上面同一条方向。
    """

    #: 来自真实的第三方公开页面。
    LIVE = "live"
    #: 我们自己造的材料：受控夹具、本机靶子、或重放件。
    SYNTHETIC = "synthetic"


class SettlementState(str, Enum):
    """预算那一格的状态。**不是** ``ReservationState`` 的别名。

    ``ReservationState`` 说的是账本上那条预留走到了哪一步；本枚举说的是
    **这次尝试**与那份预留的关系：这次调用没花过钱（``NOT_APPLICABLE``）、
    预留了但还没结算（``RESERVED``）、已结算（``SETTLED``）、或
    **外部用量未知需要事后对账**（``UNKNOWN_PENDING_RECONCILIATION``）。

    最后一个成员是这一格存在的理由：设计 §9 与 §4 都要求「未知计费先对账」，
    而它既不是「已结算」也不是「未预留」—— 需要一个人拿供应商账单回来核。
    """

    #: 这一次不走预算（例如判据拦下，请求根本没发出）。
    NOT_APPLICABLE = "not_applicable"
    RESERVED = "reserved"
    SETTLED = "settled"
    UNKNOWN_PENDING_RECONCILIATION = "unknown_pending_reconciliation"


#: ``SettlementState`` 里**欠一份预留号**的那几个成员。
#:
#: 三者的共同点：这一行说「这次调用与一份额度有关系」—— 押着（``RESERVED``）、
#: 落定（``SETTLED``）、或者不知道供应商到底扣没有（``UNKNOWN_PENDING_RECONCILIATION``）。
#: 三种都要说得出是**哪一份**，否则那一行答不出「拿哪一笔去核」。
_OWES_A_RESERVATION = frozenset(
    {
        SettlementState.RESERVED,
        SettlementState.SETTLED,
        SettlementState.UNKNOWN_PENDING_RECONCILIATION,
    }
)

#: 剩下那一个：它**必须没有**预留号。写成一张以「其余成员」为键的表（照
#: ``domain/access_governance.py::NOT_A_SETBACK`` 的先例），而不是一句
#: ``is not NOT_APPLICABLE`` —— 枚举长出第五个成员时，这里会在**一张判据上**红，
#: 而不是静默地把它归进「欠一份预留号」那一侧。两个方向的覆盖由
#: ``tests/test_fetch_attempt.py`` 那张分区表钉住。
_DOES_NOT_OWE_A_RESERVATION: dict[SettlementState, str] = {
    SettlementState.NOT_APPLICABLE: (
        "这一次调用与任何一份预留都没有关系（它那个请求里没有预留名）。判据拦下、"
        "渲染失败、DNS 没问到这些**从来没走过预算**的出口都落在这里 —— 但一次"
        "**押着预留发起的**抓取不因为它们改判：额度是先扣后发的，被拦下的那一次"
        "留下来的是一份仍然扣着额度的预留，于是那一行要指得出它（见 "
        "settlement_of 的 docstring）。没有预留可言的那一行指着预留，就是在编一个。"
    ),
}


@dataclass(frozen=True)
class Settlement:
    """一次尝试与一份预留下的**两格** —— ``FetchAttempt`` 上那两个字段的同一个回答。

    两格分开传会造出一种可表达的坏状态（``budget_settlement=reserved`` 而
    ``budget_reservation_id`` 为空，见 :meth:`FetchAttempt._the_settlement_names_its_reservation`）。
    把它们绑成一个值之后，「谁欠谁一份预留号」在**构造点上**就已经定了，而不是等
    落到模型上再判一次 —— 后者只在有人真的写错时才说话，前者让那个状态写不出来。
    """

    state: SettlementState
    reservation_id: str = ""


def settlement_of(reservation_id: str) -> Settlement:
    """押着哪一份预留 → 账上那两格。**全函数：每一个输入都有答案。**

    ## 它答的是什么问题

    「这一次尝试与预算的关系」在今天只有一个诚实的来源：**这一次请求有没有押着
    一份预留**。押着就写 ``RESERVED`` 并把那一份的名字记下来；没押就写
    ``NOT_APPLICABLE`` 且什么都不指。两格由同一个输入推出来，所以不存在「一格改了、
    另一格忘了改」—— 而那个形状的行正是上面那个校验器要拒的。

    ## 为什么「没发出去」不改变这个答案

    判据拦下、渲染失败、DNS 没问到这些出口今天都取 ``NOT_APPLICABLE``，但那是
    **没有预留可指**时它们的答案。一次**押着预留发起的**抓取不因为它们改判：
    额度在 ``application/refresh.py::_recheck_page`` 里**先预留、后发请求**，而只有
    拿到字节才 ``spend`` —— 于是被拦下的那一次（连一跳都没发出去）留下来的是
    **一份仍然扣着额度的预留**。账上写一句「我没花钱」，这个泄漏在报告上就完全
    看不见，而它恰好是最该被看见的那一行。

    所以 ``RESERVED`` 在这里的意思是**「这一次尝试是在一份预留之下发生的」**，
    而不是「这一次尝试花掉了那份预留」。它后来是被结算、被退款还是就那么扣着，
    是**账本那一侧**的答案 —— 两本账按这一格的 id 对起来：按 id 取那一条是
    ``ports/budget.py::BudgetLedger.get``，而「还扣着额度的那些」另由
    ``open_reservations`` 挑出来（它只挑 ``RESERVED``，所以拿到字节的那一次
    ``spend`` 之后就不在里面了）。落在盘上的那一行记的是写它那一刻的关系，
    理由与 ``FetchAttempt`` 记 ``started_at``/``ended_at`` 而不是 ``elapsed_ms``
    同一条。

    ## 同一个预留号可以出现在多行上

    ``DocumentAcquisition.fetch_document`` 把**同一个请求对象**交给直连与浏览器
    两条路（``ports/acquisition.py`` 上写着为什么），所以一次 ``auto`` 抓取真的发了
    两跳时，账上会有两行指着同一个预留号。这是对的：那一页只有一份额度，两跳都是
    它的取回。对账按预留号**归并**，不按行数。

    ## ``SETTLED`` 与 ``UNKNOWN_PENDING_RECONCILIATION`` 今天没有生产者

    这是**事实**，不是待办，而且理由在账的这一侧：

    * ``SETTLED`` 需要一次**我们结算过**的抓取出口。抓取这一层今天不消费任何计费
      额度（本产品仅有的计费出口是检索与抽取，它们不经过 :class:`FetchAttempt`
      —— 见 ``application/billed_search.py``）。
    * ``UNKNOWN_PENDING_RECONCILIATION`` 需要一次**对我们自己未知**的计费出口。
      本账本里的预留永远不会对我们未知：它就是我们的账。

    所以设计 §9 那句「必要时记录 delivery/charge_unknown 待对账」的落点在那一天 ——
    某一层某天真的走一个有计费的抓取出口时，**要改的是这个函数**（它要加一个那个
    出口才拿得到的输入，例如「对方说它扣了没有」），而不是两个调用点各写一遍：
    两个调用点各写一遍正是这一格此前的失败形态（两处都写死 ``NOT_APPLICABLE``，
    而没有任何东西会因为漏改一处而变红）。判据
    （``tests/test_settlement_producer.py``）今天钉住的是**没有哪条抓取路径声称这
    两态**，于是「某天有人加了生产者而这一段没跟着改」会红。
    """
    if not reservation_id:
        return Settlement(SettlementState.NOT_APPLICABLE)
    return Settlement(SettlementState.RESERVED, reservation_id)


class MethodKind(str, Enum):
    """这次访问用的方法。

    闭集，且**只有本产品真的会用的三种**（设计 §5 的推荐顺序里那三种获取手段；
    「已批准结构化来源」不经过本模型，它是一次导入而不是一次抓取尝试）。
    """

    HTTP = "http"
    BROWSER = "browser"
    #: 站点明确允许的 API/导出/白名单路径（设计 §4「修自有授权配置与站点允许的
    #: API/导出/白名单路径可以进入运维待办」）。
    APPROVED_API = "approved_api"


def attempt_id(
    *,
    workspace: str,
    scope: str,
    method: MethodKind,
    url: str,
    started_at: datetime,
) -> str:
    """一次尝试的 id：**由「谁、哪一跳、什么时候开始的」决定**。

        att-<sha256(workspace ␟ scope ␟ method ␟ url ␟ started_at)[:16]>

    ## 为什么它算的是「这一次尝试」，而不是「这份记录的内容」

    ``Evidence.evidence_id`` 是**内容寻址**的，因为同一条证据写两遍没有新信息。
    尝试不是这样：两次真的发出去的请求是**两个事件**，哪怕它们的目的地一模一样。
    所以种子取的是事件的身份（归属 + 哪一跳 + 什么时候），而不是记录里每一个字段。

    这一格的含义写清楚，因为它决定了两个行为：

    * **同一个 ``(workspace, scope, method, url, started_at)`` 再写一遍**是幂等的
      —— 落库给的回答是「已经在账上了」。
    * **同一个 id 配不同的内容**是错误，而不是一次覆盖。它只可能意味着两个生产者
      对**同一次尝试**说了两套话（例如一个说拿到了 200、另一个说超时）。静默保留
      先写的那条，会让事后那句「这个 URL 那天到底怎么样了」有一个**看不出来**的
      错误答案。

    ## 为什么用 ``started_at`` 而不是 ``ended_at``

    开始的那一刻是这个事件的定义：结束时刻会随重试、超时与重定向的**数量**变化，
    于是「同一次尝试」会因为上游慢了一点而换一个 id。用开始时刻，``ended_at``
    仍然是记录里的一格（它答「这一跳花了多久」），只是不参与身份。

    ## 为什么它进 domain 而不是进抓取器

    这是记录自己的身份规则，不是某一种传输的性质。放进 ``adapters/http.py`` 之后，
    第二个抓取器（浏览器那一层）要么重写一遍，要么**编一个自己的拼法** —— 而两种
    拼法都能长出合法的 ``attempt_id``，报告上分不出来，只是同一个 URL 的两条尝试
    从此不在一个命名空间里。

    Raises:
        ValueError: ``started_at`` 不带时区。``FetchAttempt`` 自己也会拒一次，
            但那条拒绝发生在**已经算出一个 id 之后** —— 而那个 id 是按本地时区
            算出来的，于是同一瞬间在两台机器上会得到两个 id。
    """
    if started_at.tzinfo is None:
        raise ValueError(
            "attempt_id 需要带时区的 started_at —— 见 util.to_utc 的说明。"
            "按本地时区算出来的 id 会让同一瞬间在两台机器上得到两个不同的值，"
            "而报告上看不出这一点。"
        )
    seed = _SEED_SEPARATOR.join(
        (workspace, scope, method.value, url, to_utc(started_at).isoformat())
    )
    return f"att-{sha256_text(seed)[:16]}"


class FetchAttempt(StrictModel):
    """一次访问的实测记录。**不可变。**"""

    attempt_id: str
    #: 这一跳记在谁名下。空串 = 不属于任何主体（不是「随便哪个账号」）。
    #:
    #: 非空时**必须已经是文件名**，与 :class:`~marketops.ports.acquisition.FetchRequest`
    #: 同一条判据、同一个理由：这一次尝试的 ``scope`` 与那次抓取的 ``FetchRequest.scope``
    #: 是**同一个字符串**，而后者会逐字成为 ``Evidence.account_id`` 与页面镜像的目录名。
    #: 两处各判一遍而判得不一样的那一天，「这一页记在谁名下」在尝试账上是一个拼法、
    #: 在证据账上是另一个拼法 —— 而两份报告各自都完整。
    scope: str = ""
    workspace: str
    #: 业务归属。三者都可为空（一次不由作业发起的抓取），但**不为空时必须是
    #: 那个对象的名字** —— 本模型不校验它们存在，那是存储层的引用完整性。
    run_id: str = ""
    work_item_id: str = ""
    target_ref: str = ""

    method: MethodKind
    url: str
    final_url: str = ""
    #: 重定向链上**每一跳的地址**，按顺序。第一跳是 ``url``。
    redirect_trace: tuple[str, ...] = ()
    #: 内部连接/出口档位的名字（``EgressProfile`` 的标识）。**不是** IP：
    #: 设计 §3 逐字要求原始 IP 不出报告，而出网身份的引用要能对着配置查回来。
    egress_profile: str = ""

    started_at: datetime
    ended_at: datetime

    #: **nullable**。读不出来就是 ``None``，不折成 200，也不折成 0。
    observed_status: int | None = None
    #: 只留白名单里的响应头（见 ``domain/access.py::safe_response_headers``）。
    response_headers: dict[str, str] = Field(default_factory=dict)
    #: 错误码/错误文本。空串 = 没有错误。
    error_code: str = ""

    #: 这一行的正文来自现场还是我们自己造的（见 :class:`MaterialProvenance`）。
    #:
    #: 它与 ``body_*`` 那三格**贴在一起**而不是散在别处，因为它们是同一句话的两半：
    #: ``body_sha256`` 说「拿到的是这几个字节」，这一格说「这几个字节是从哪来的」。
    #: 一份有哈希的正文仍然答不出后者 —— 而 ``docs/MCP_SERVICE.md`` §8.C.2 要求
    #: 两种材料都被标注，正是因为**哈希对两者一视同仁**。
    #:
    #: 缺省 ``SYNTHETIC`` 的理由写在 :class:`MaterialProvenance` 上（一条肯定声明
    #: 与一次猜测的差别）。这里只补一条落点的事实：这一格**不由模型推导**，
    #: 它从 :class:`~marketops.ports.acquisition.FetchRequest` 逐字抄过来 ——
    #: 于是「这次抓取的调用方认为自己在抓什么」与「账上记的是什么」是同一个字符串，
    #: 而不是两处各自算一遍。
    material: MaterialProvenance = MaterialProvenance.SYNTHETIC
    body_kind: BodyKind = BodyKind.NONE
    body_bytes: int = 0
    body_sha256: str = ""
    #: 解析器/提取器的版本。设计 §2.5：「解析失败不得伪装无邮箱」的溯源点。
    parser_version: str = ""

    budget_reservation_id: str = ""
    budget_settlement: SettlementState = SettlementState.NOT_APPLICABLE
    retry_after_s: int | None = None

    # --- 观测与根因：四个字段，两个层次 -------------------------------------
    challenge_detected: bool = False
    suspected_cause: SuspectedCause = SuspectedCause.NONE
    cause_evidence_ref: str = ""
    action_taken: ActionTaken = ActionTaken.PROCEEDED

    @field_validator("attempt_id")
    @classmethod
    def _canonical(cls, value: str) -> str:
        from .util import check_canonical_id

        return check_canonical_id(value, "attempt_id")

    @field_validator("scope")
    @classmethod
    def _canonical_scope(cls, value: str) -> str:
        """空串合法（不属于任何主体），非空就必须已经是文件名。

        与 ``FetchRequest.__post_init__`` 逐字同一个判据 —— 那两处记的是同一个值。
        ``check_canonical_id`` 对空串会抛，所以空值在这里单独放行，而不是先去调它。
        """
        from .util import check_canonical_id

        return check_canonical_id(value, "FetchAttempt.scope") if value else ""

    @field_validator("started_at", "ended_at")
    @classmethod
    def _aware(cls, value: datetime, info) -> datetime:
        if value.tzinfo is None:
            raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
        return to_utc(value)

    @field_validator("response_headers")
    @classmethod
    def _only_safe_headers(cls, value: dict[str, str]) -> dict[str, str]:
        """**这一格再洗一遍**，而不是信任调用方洗过了。

        白名单在 ``domain/access.py`` 一处定义；这里再洗一次是因为「调用方洗过了」
        是一次假设，而它失效的后果是**凭据进报告**。多洗一次的代价是一次字典遍历。
        洗掉的键不报错 —— 报错会把一次正常的 ``set-cookie`` 变成一次抓取失败。
        """
        return safe_response_headers(value)

    @model_validator(mode="after")
    def _the_settlement_names_its_reservation(self) -> "FetchAttempt":
        """那一格说走预算，就必须指得出是哪一份；说不走，就不许指着任何一份。

        ## 它挡的是哪一种行

        设计 §9 与 §5 都要求「未知计费**先对账**」（``SettlementState`` 的 docstring
        把这一格的来由写全了），而对账的第一步是**从这一行找到那笔预留**。两格各自
        合法、合起来是假话的行因此是最坏的一种：

        * ``budget_settlement=reserved`` 而预留号为空 —— 这一行说「这次调用押着一份
          额度」，而它答不出押的是哪一份。§5 那条坏结局（同一个来源上二十个账号各自
          失败一次）在账上长了二十行这样的记录，而账本里找不出第二十份预留。
        * 预留号非空而那一格说 ``not_applicable`` —— 两者只有一句是真的，而「哪些
          尝试真的押了额度」这个问题的答案会因此多出几行。按那几行去核账的人核的
          是一笔不存在的账。

        ## 为什么它不是一条断言、也不在调用点上判

        这一行是**三个月后**唯一的凭据（模块 docstring），所以「写进去的那一行说得通」
        必须由模型自己保证，而不是由两个抓取器的调用点各自记得。断言在 ``-O`` 下会
        整条消失，而这条规则消失之后的表现是「账上多了几行对不了账的记录」——
        没有人会看见它消失。

        ## 不校验预留号的**形状**

        ``RESERVED`` 之外的两格记的可能是一份**供应商标单上的**引用（对账正是拿它去
        核），所以这里只管「有没有」，不管「长什么样」。一个形状规则会把一次正常的
        供应商引用变成一次落账失败。

        形状规则在**另一处**是有的，而且不矛盾：``FetchRequest.reservation_id``
        记的是**我们自己的账本**里那一条（``domain/budget.py::check_reservation_id``）。
        两处问的不是同一个问题 —— 差别是「这一格是谁算出来的」，那段论证写在
        ``check_reservation_id`` 上。
        """
        named = self.budget_reservation_id
        if self.budget_settlement in _OWES_A_RESERVATION:
            if not named:
                raise ValueError(
                    f"budget_settlement 是 {self.budget_settlement.value!r}，"
                    "而 budget_reservation_id 是空的。\n"
                    "这一行说的是「这次调用与一份额度有关系」，所以它必须指得出是"
                    "**哪一份** —— 否则「未知计费先对账」这件事无从开始：对账的人手上有"
                    "一行说押了额度的记录，而账本上找不到对应的那笔预留。"
                )
            return self
        # 走到这里说明那一格在「不欠」的那一侧。那张表里没有它，就是有人往枚举里
        # 加了成员而没在这里决定它归哪一边 —— 归「不欠」会在账上放过一行自相矛盾的
        # 记录，所以拒绝，而不是默认。
        if self.budget_settlement not in _DOES_NOT_OWE_A_RESERVATION:
            raise ValueError(
                f"{self.budget_settlement.value!r} 既不在 _OWES_A_RESERVATION 里，"
                "也不在 _DOES_NOT_OWE_A_RESERVATION 里。\n"
                "枚举新增成员时要在 domain/fetch_attempt.py 里决定它归哪一侧：欠一份"
                "预留号的往 _OWES_A_RESERVATION 放，不欠的往那张表里放。默认放行会让"
                "一行自相矛盾的记录落进账本。"
            )
        if named:
            raise ValueError(
                f"budget_reservation_id 是 {named!r}，而 budget_settlement 说是"
                f"{self.budget_settlement.value!r}（这一次不走预算）。\n"
                "两者只有一句是真的。一个既指着某份预留、又声明自己不花钱的行，会让"
                "「哪些尝试真的押了额度」这个问题的答案多出几行 —— 而按那几行去核账的"
                "人核的是一笔不存在的账。"
            )
        return self

    @field_validator("body_sha256")
    @classmethod
    def _sha_shape(cls, value: str) -> str:
        if not value:
            return ""
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("body_sha256 要么是空串（没有正文），要么是一个 sha256 十六进制串")
        return value

    @property
    def ok(self) -> bool:
        return self.observed_status is not None and 200 <= self.observed_status < 300

    @property
    def body_kept(self) -> bool:
        """这次尝试**留下了**一份可回读的正文。"""
        return bool(self.body_sha256) and self.body_bytes > 0

    @property
    def elapsed_ms(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds() * 1000)

    @property
    def status_unknown(self) -> bool:
        """状态码没有观测到。**刻意是一个具名属性**，让「未观测」在报告里可数。"""
        return self.observed_status is None


__all__ = [
    "BodyKind",
    "FetchAttempt",
    "MethodKind",
    "Settlement",
    "SettlementState",
    "attempt_id",
    "settlement_of",
]

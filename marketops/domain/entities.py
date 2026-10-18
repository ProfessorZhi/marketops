"""领域模型：账号、场地资产、责任实体、人、岗位、联系路线。

本模块与 ``domain/evidence.py`` 的分工是**概念**上的，不是分层洁癖：

* 这里回答「**是什么**」—— 谁拥有/运营/维护/预约控制/采购哪个场地；
  一条路线绑在哪个账号、哪个人、哪个岗位、哪条记录块上。
* ``domain/evidence.py`` 回答「**凭什么这么说**」—— 字面证据与运行记录。

因此 ``ContactRoute.evidence_ids`` 是字符串引用，不是对象引用：一条路线在
没有证据可读的情况下也应该能被读懂，而证据一旦被删掉，路线必须**降级**而不是
静静地继续成立。

本模块承载四条硬约束，全部由 pydantic 在校验期强制：

1. **账号 ≠ 联系路线**（SOP v1 L78）。一个 Council/Trust 可以管很多场地，
   一个场地可以由别的组织运营维护，一个人可以对多个场地负责 —— 所以
   ``AccountVenueRelation`` 是一张独立的关系表，键是
   (workspace, account_id, venue_asset_id, relation)，
   **不是** ``venue_name``。同名场地串号在这里表达不出来，因为场地从来不是键。
2. **A/B 要求字面公开邮箱**（SOP v1 L126）。:class:`ContactRoute` 的 ``email``
   是必填且非空的。没有邮箱的「具名相关角色」不是 B 级路线，它是
   :class:`RouteOpportunity` —— 一条未解决的路线机会，可以与证据一起保存。
3. **P3 没有邮箱**，所以它只可能出现在 :class:`RouteOpportunity` 上。
4. 路线与本阶段所有产出一样，``send_authorized`` 恒为 False。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .base import StrictModel as _Strict
from .enums import (
    UNREACHABLE_WITHOUT_AUTHORIZATION,
    BusinessFunction,
    DomainVerdict,
    EntryKind,
    Grade,
    GradeReason,
    RecordBlockPriority,
    RoleClass,
    RouteBinding,
    RouteStatus,
    VenueRelation,
)
from .temporal import Freshness, assertion_id
from .util import (
    FILENAME_ID_MAX_LENGTH,
    check_canonical_id,
    check_slug_safe,
    sha256_text,
)


def _person_id(account_id: str, literal_name: str) -> str:
    """人的 id 是内容寻址的：同一个账号下同一个字面姓名就是同一个人。"""
    return sha256_text("\x1f".join(["person", account_id, literal_name.strip().lower()]))[:32]


def _role_id(account_id: str, literal_title: str) -> str:
    """岗位的 id 同理。注意「同一个人换了岗位」因此是两条 ``Role``。"""
    return sha256_text("\x1f".join(["role", account_id, literal_title.strip().lower()]))[:32]


def venue_asset_id_for(*, source_identity: str, name: str, location: str) -> str:
    """这块场地的**句柄**：上游给了编号就按编号，没给就按内容。

    形状是 ``src-<16 位十六进制>`` / ``named-<16 位十六进制>``。两段都是
    ``[a-z0-9-]``，因此它既是 slug-safe 的，也是 ``slugify`` 的不动点 ——
    这一格会被当作文件名片段用（见 :func:`check_canonical_id` 的那一节理由）。

    ## 为什么不能拿 ``row_id`` 当它

    ``row_id_for``（``domain/receipt.py``）的输入里有 ``ordinal``，也就是**那一行
    在这份工件里的位置**。母池下一个季度多插一行，它后面每一行的 ``row_id`` 都会
    变 —— 而池子是**跨批次**的：拿行号当句柄，一次重新导出会让每块场地都看起来
    是新发现的，报告上是「本轮新增 N 块场地」，盘上却一块都没变。

    来源编号没有这个问题：``osm:node/123`` 明年还是 ``osm:node/123``。

    ## 为什么没有编号时按 ``(名字, 地点)``

    因为这是**回执自己也用的那个键**（``SourceReceipt.distinct_resolved_count``：
    「同名同地点是同一个东西」）。两处用两个键的话，「这份回执里有 25 个不同的
    东西」与「这一批写出了 25 块场地」会各说各话，而它们说的是同一件事。定义只有
    一处：``resolved_dedupe_key``。

    名字与地点都只在**取空白**之后参与 —— 上游导出里 ``"Main Pitch "`` 与
    ``"Main Pitch"`` 是同一块场地。**不做大小写折叠**：那是回执那一边已有的一条
    判断（它按原样比），而这里跟着它走，不另立一套。

    ## 编号非空时，名字与地点**不**参与

    同一条来源编号在两行里带着不同的名字（上游改过拼写），是**同一块场地**改过名，
    不是两块。把名字并进键里会让它在池子里裂成两块，而报告上那读起来像「这一轮
    新发现了 1 块场地」。

    这一条与存储那条「``source_identity`` 不是身份、两行可以有同一个它」不矛盾：
    存储管的是**已经躺在那里的行**（一次 ``cp -r state/``、一次手工编辑都会造出
    那种状态），这里是**生产者**给新行取句柄的规则。一份回执里同一个来源编号只会
    有一行是 ``resolved``（``application/motherpool.py`` 按它归并，重复的那些处置成
    ``merged``），所以这条规则在一批之内不会把两行折成一行。
    """
    identity = source_identity.strip()
    if identity:
        digest = sha256_text("\x1f".join(["venue-asset", identity]))
        return f"src-{digest[:16]}"
    digest = sha256_text("\x1f".join(["venue-asset", name.strip(), location.strip()]))
    return f"named-{digest[:16]}"


def _timestamps_must_be_aware(value: datetime | None, info: ValidationInfo) -> datetime | None:
    """只拒绝 naive，**不**归一成 UTC。本模块里每一个带时刻的行都共用这一条判据。

    naive 的时间戳的失败方式不在它落盘的时候，而在下一次折入：合并规则拿它
    跟已有那一行比先后（``min`` / ``max``），而 Python 对 naive 与 aware 的
    比较是 ``TypeError`` —— 一次**崩**，发生在离原因很远的地方（一个几小时前
    写下的池子行）。§9 把时刻带到路线那一族之后同理，那里还要跟 ``now`` 相减
    算「有多旧」。

    这里不做 ``to_utc``：池子里的时刻只用来比先后，而跨偏移量的比较本来就按
    瞬间走。把偏移量改写成 UTC 会让「写进去什么瞬间、读出来什么瞬间」这条
    判据变成一句空话（``tests/test_asset_store_contract.py`` 里那条正对照）。

    **抽成模块函数而不是各写一份**：本模块里带时刻的每一行（池子两行、路线
    两行）都调这里。这条判据松掉的后果是一样的（一次离原因很远的 ``TypeError``），
    所以它只该有一个定义处。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(f"{info.field_name} 必须带时区 —— 见 util.to_utc 的说明")
    return value


#: 路线这一族断言的谓语。取值是**闭集**：多一种「这一行在断言什么」就要在这里多
#: 写一行，于是 ``TemporalAssertion.predicate`` 的合法取值有一份可读的清单。
ROUTE_ASSERTION_PREDICATE = "published_contact_email"
OPPORTUNITY_ASSERTION_PREDICATE = "unresolved_contact_route"


def route_subject_ref(workspace: str, account_id: str) -> str:
    """断言的主语：**带工作区**的账号。

    ``account_id`` 只在工作区之内唯一 —— 它落盘成 ``<workspace>/<account_id>.jsonl``
    （见 :meth:`ContactRoute._account_id_is_canonical`）。只写 ``account:<id>`` 会让
    两个工作区里同名的账号得到同一条断言 id，而断言 id 是「这条结论是关于哪件事的」
    那个身份：撞上之后，两条互不相干的结论共用一个身份，且没有任何地方会报错。
    """
    return f"account:{workspace}:{account_id}"


def _asserted_value(*parts: str) -> str:
    """断言的值：几段字面里**非空**的那些，用一个单元分隔符（ASCII ``0x1F``）拼接。

    空的那几段不参与，因为 ``""`` 不是「一个空的字段」，它是「这一格没有值」。
    把它当一段拼进去，会让不同的组合（有岗位没姓名 / 有姓名没岗位）在某些取值下
    拼出同一个值。分隔符与 :func:`~marketops.domain.temporal.assertion_id` /
    :func:`~marketops.domain.evidence.evidence_id` 的原像用的是同一个单元分隔符
    —— 普通字符会在这些值里合法地出现，于是它分不开两段。
    """
    return "\x1f".join(part for part in parts if part)


def _expected_assertion_id(
    *,
    workspace: str,
    account_id: str,
    predicate: str,
    value: str,
    first_seen_at: datetime | None,
) -> str | None:
    """这一行**自己**的那条断言 id；没有时刻锚点时是 ``None``。

    两个调用方共用这一处：行上的校验器（判断挂上来的那一格是不是本行的结论），
    以及生产者（``application/freshness.py`` 按它填那一格）。分开写两份公式是
    本仓明确列为缺陷的那种「同一句话有两个出处」—— 而这一份漂移的方式特别隐蔽：
    两边各自自洽，只是**判的不是同一条断言**，于是校验永远通过、结论永远挂错行。
    """
    if first_seen_at is None:
        return None
    return assertion_id(
        subject_ref=route_subject_ref(workspace, account_id),
        predicate=predicate,
        value=value,
        observed_at=first_seen_at,
    )


def _freshness_belongs_to_this_row(
    freshness: Freshness | None,
    *,
    workspace: str,
    account_id: str,
    predicate: str,
    value: str,
    first_seen_at: datetime | None,
) -> None:
    """行上那一格 ``freshness`` 必须**能从这一行自己算出来**。

    ## 为什么这条判据在行上，而不只在产出它的那一层

    新鲜度是一个**结论**，而结论会走丢。最容易发生的一种走丢不需要谁写错什么：
    复制一行去改（改邮箱、改账号、改姓名）而把 ``freshness`` 一起带过去。带过去的
    那一格长得完全正常 —— 有 ``currentness``、有 ``reason``、有 ``policy_version``、
    有一个 32 位 id —— 于是它会被读成「这一行也是判过的」。而它判的是**另一行**。

    ``assertion_id`` 是内容寻址的（:func:`marketops.domain.temporal.assertion_id`），
    所以「这一格是不是关于这一行的」有一个不必依赖信任的判据：拿这一行自己的时刻
    重算一遍。两个方向都挡：

    * 有 ``freshness`` 而 ``first_seen_at`` 为空 —— 断言 id 的原像里有观测时刻，
      没有它就**算不出**这个 id。这一格因此无法被复核，只是看起来还行；
    * 算出来的 id 与存下来的不同 —— 这一格属于别的行。

    判定本身（``currentness`` 取值、``is_actionable``）在这里**不重复判**：
    它是 :func:`marketops.domain.temporal.evaluate_freshness` 的产出，再判一次就是
    同一个规则有两个答案。

    这条判据在**构造**上生效。``model_copy`` 不走校验（pydantic 的行为，本仓所有
    不变量都如此），所以改写一行的调用方要么按这一行自己的时刻重算这一格，要么
    重建这一行 —— 唯一不行的是把上一行的结论原样带过来。
    """
    if freshness is None:
        return
    if first_seen_at is None:
        raise ValueError(
            f"{type(freshness).__name__} 挂在一条没有 first_seen_at 的行上。"
            "断言 id 的原像里有观测时刻，所以这一格无从复核 —— "
            "它看起来像是判过了，实际判的是算不出 id 的另一行。"
        )
    expected = _expected_assertion_id(
        workspace=workspace,
        account_id=account_id,
        predicate=predicate,
        value=value,
        first_seen_at=first_seen_at,
    )
    if freshness.assertion_id != expected:
        raise ValueError(
            f"freshness.assertion_id={freshness.assertion_id} 与本行自己算出的 "
            f"{expected} 不一致 —— 这一格是关于另一行的结论（复制一行改内容最容易带上它）。"
            "要保留它就先删掉，再由生产者按本行的时刻重新判一次。"
        )


class VenueAsset(_Strict):
    """一块**场地 / 资产**：一个 pitch、一片 ground、一处 facility。

    场地**不是**账号。一个 Council 可以拥有一整个城市的场地，一个 Trust 可以
    运营其中几块，一家承包商可以维护另一些。把场地名当账号键，正是 SOP v1 L78
    要求拆开的那件事。

    ## 来源身份不是账号身份（本模型最容易读错的一处）

    :attr:`source_identity` 的形状是 ``<namespace>:<value>``，与
    ``domain/receipt.py`` 里母池行的来源标识**同一个约定**：一份 OSM 导出给出的
    是 ``osm:node/123``，一次检索给出的是 ``site:<主机>``。它回答的是「这一行是
    从哪儿认出来的」，**不回答**「它是谁的资产」。

    ``osm_type`` / ``osm_id``（也就是那一段里的 ``node`` 与 ``123``）在
    :meth:`source_parts` 上，与其余来源一视同仁地只是**上游的编号**。它的
    诱惑在于它稳定、唯一、好当键 —— 而它认的是**一块场地**：同一个 Council 的
    两百块场地会给出两百个这样的编号。拿它当账号键，就是 SOP v1 L72 明令拆开的
    「账号是实体，不是单个 pitch」被原样缝回去。因此本模型**没有**一个叫
    ``account_id`` 的槽位，也不提供从 ``source_identity`` 推账号 id 的函数。

    **它是可选的原因**：母池的行来自登记册（有来源编号），而一次检索发现的主机
    只有它自己的域名 —— 那一行的来源编号是空的，而空在这里是诚实的「这个来源
    没有给出编号」，不是「编号是空串」。两者由 ``""`` 与「非空但不合形状」分开：
    后者直接报错，不会被悄悄改写成空串。

    ## 域名这件事上的四个格子，以及它们为什么不能合并

    审计里那条「``official_domain`` 是 SERP 抓回来的主机，却被读成『这是它的官网』」
    被拆成四个格子。前三个是**我们的**三种认知阶段，第四个是上游的说法：

    * :attr:`claimed_official_domain` —— **上游这一行说的**官网是什么。它来自
      登记册/导出的那一列（``RowDisposition.claimed_official_domain``），是别人写下来的
      一句话，不是我们看到的任何东西。留它是因为：丢掉之后，一次「这一格有值、
      但我们从没验过」的状态就表达不出来，而下游（域名核验那一步）要么重新去读
      原始工件，要么把这些行当成「根本没有域名线索」跳过。
    * :attr:`discovered_url` —— 我们在**哪儿**看到的这一行。它是一次指路的
      观察，不主张任何归属；同一块场地会有很多条。
    * :attr:`candidate_domain` —— 从那个 URL 折出来的主机（``www.`` 与小写已
      归一）。它是**待验证的候选**：一台聚合站、一份目录页、一个托管商都会给出
      一个主机，而它们都不是「这个组织的官网」。
    * :attr:`verified_official_domains` —— **只有拿到域名证据的那一步才填**，
      并且由 :attr:`domain_verdict` 与 :attr:`domain_evidence_ids` 一起构成
      一个说得清的判定：``CONFIRMED`` 要求域名集合与证据都在，``ABSENT`` 要求
      「查过、没有」的证据在而域名集合为空，``UNKNOWN`` 要求一格都没有。这几格由
      ``_domain_verdict_agrees_with_the_fields`` 在校验期绑死 —— 一个「域名填了
      但判定是 UNKNOWN」的行在盘上写不出来。
      :attr:`verified_official_domain` 是那个集合在**恰好一个**元素时的读法，
      不是一个独立的格子（同上那条校验器绑死两格之间的一致性）。

    ``candidate_domain`` 与 ``verified_official_domain`` 都非空而**不相等**是合法
    且常见的一行（在目录页上看到它，官网在别处），它读作「候选被推翻过」。因此
    不许把前者当成后者的缺省值 —— 那是把一次没做过的验证写成一个做过的。

    **:attr:`claimed_official_domain` 与 :attr:`candidate_domain` 是两格而不是一格**，
    尽管两者的「还没验过」程度一样，理由只有一个但足够：它们回答的是**谁说的**。
    ``candidate_domain`` 的取值来自 ``discovered_url``，所以它自带一条我们能重放的
    观察（那条 URL）；上游那一列没有 URL，它在盘上就是一句话。把后者写进前者，
    会让「这一格是从哪儿来的」变成一句读不出来答案的话 —— 一个 ``candidate_domain``
    有值而 ``discovered_url`` 为空的行，看起来像一条写坏了的观察。

    ``claimed_official_domain`` 也**不是** ``verified_official_domain`` 的候选：
    合并规则（``application/pool.py``）不会把它提升成后者，无论两者是否相等。理由是
    「确认官网」这一步的全部价值在于**证据**，而这一格是别人写下的一句话 ——
    一句对的话与一次验证过的结论，在审计里不是同一件事。
    """

    #: 这一行属于哪个**工作区**（部署的 Scene，``Settings.profile``）。
    #:
    #: 与 :class:`~marketops.domain.evidence.Evidence.workspace` 逐字同一条理由、
    #: 同一个写法：工作区是授权链的第一段，跨 Scene 的复用止于工作区边界；而
    #: 「缺省 = 当前 Scene」的默认值会把**没写血统**变成一次不留痕的放行。
    workspace: str
    #: 本仓给这块场地的**句柄**（slug-safe，会落成文件名）。
    venue_asset_id: str
    #: 上游认这一行的方式（``<namespace>:<value>``），空 = 那个来源没给出编号。
    source_identity: str = ""
    name: str
    #: 场馆类型（组织形态的场地侧读法）、场地环境（室内 / 室外）、表面类别。
    #:
    #: **这三格的空缺有两种写法，而它们说的不是同一件事**：
    #:
    #: * ``""`` —— 这一份工件**没有这一栏**（母池那种形状的导出就没有），
    #:   与 :attr:`claimed_official_domain` 同一个约定；
    #: * ``"unknown"``（:data:`~marketops.domain.profile.CLASSIFICATION_UNKNOWN`）——
    #:   读过了，而这一页**没有说出词表认得的说法**。这是发现期那一侧的**正式结论**
    #:   （§2 逐字：「未确认字段必须为 ``unknown``，绝不能是冒充事实的空字符串」），
    #:   随回执一路走到这里。
    #:
    #: 两种都不是「这块场地是什么」，所以 ``application/pool.py`` 的合并规则
    #: **两种都不当作一次观察** —— 否则一次重导入会让上一次真的读出来的值消失，
    #: 而报告上只是「这一块的信息变少了」。那一条写在
    #: ``_CLASSIFICATION_FIELDS`` 上，是这一层唯一认识 ``unknown`` 的地方。
    #:
    #: 两者不合并成一种写法，是因为它们回答的问题不同：「这一份导出有没有这一栏」
    #: 是**输入的形状**，「这一页有没有说」是**这一页的内容**。合成一种之后，
    #: 「拿一份没有那一栏的导出重导」与「页面改口了」在池子里长得一样。
    venue_type: str = ""
    venue_environment: str = ""
    surface: str = ""
    #: 同上，而这里的「没有这一栏」写成 ``None`` —— 理由与那台伪造证据的机器写在
    #: ``AccountRecord.country`` 上：一台按国家排除的规则不该把缺省值当成证据。
    country: str | None = None
    address: str = ""
    notes: str = ""
    #: **上游说**这块场地的官网是哪一个。空 = 那份导出没写，或那一列解析不出主机。
    #:
    #: 它是一次**转述**，不是一次验证：我们不认识写它的人，也没有任何凭据。因此
    #: 它不进 :attr:`domain_verdict` 那三格，也不参与官网判定 —— 见类文档。
    claimed_official_domain: str = ""
    #: 我们在哪儿看到的它。**不主张归属** —— 见类文档。
    discovered_url: str = ""
    #: 从 :attr:`discovered_url` 折出来的主机，**待验证**。
    candidate_domain: str = ""
    #: 证得的官网域名，**恰好一个**的时候。空 = 还没证得（可能是查过没有，
    #: 见 :attr:`domain_verdict`），或者证得的**不止一个**（见下一格）。
    verified_official_domain: str = ""
    #: **证得的全部官网。** 这一格才是判定的本体，单数那一格是它的读法。
    #:
    #: 一个组织有两个官网（主站 + 分区/子品牌站）是常见的，而绑定那一层问的是
    #: 「这个邮箱的域是不是**它的**域之一」—— 那是一个集合问题。只留一个单数格，
    #: 第二个官网就在链路上消失，症状是**真实的企业邮箱被判成外来域**
    #: （SOP L121 那条拒信），而理由栏里的话听起来完全正当。
    verified_official_domains: list[str] = Field(default_factory=list)
    #: 官网问题查到了哪一步。**三值**，见 :class:`~marketops.domain.enums.DomainVerdict`。
    domain_verdict: DomainVerdict = DomainVerdict.UNKNOWN
    #: 支撑 :attr:`domain_verdict` 的证据。``CONFIRMED`` 与 ``ABSENT`` 都**要求**
    #: 非空 —— 「查过、确实没有」也是一个要凭据的判定。
    domain_evidence_ids: list[str] = Field(default_factory=list)
    #: 这一行的其余证据（场地本身的名字、位置、表面等）。与 ``Evidence.evidence_id``
    #: 同空间，是**指针**不是正文。
    evidence_ids: list[str] = Field(default_factory=list)
    #: 这一行第一次落进池子的时刻。**是一次关于我们的事实**（我们何时记下了它），
    #: 因此永远可得，也永远由写路径当场给出 —— 不是从上游行里猜出来的。
    first_seen_at: datetime
    #: 最后一次**真的重新看过**它的时刻。``None`` = 只有它进池子那一次，
    #: 与 ``Observation.observed_at`` 同一条纪律：读一遍本地记录不是一次观察。
    last_verified_at: datetime | None = None

    @field_validator("venue_asset_id")
    @classmethod
    def _id_is_slug_safe(cls, value: str) -> str:
        return check_slug_safe(value, "venue_asset_id")

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("source_identity")
    @classmethod
    def _source_identity_has_a_namespace(cls, value: str) -> str:
        """空合法；非空必须是 ``<namespace>:<value>``，两段都不许为空。

        「非空但不合形状」直接报错而不是改写成空串 —— 后者会让「这个来源没给出
        编号」与「编号写错了」在数据上长得一样（``AccountCandidate.pairing_key``
        那一格写下了同一条判据）。
        """
        if not value:
            return value
        if ":" not in value:
            raise ValueError(
                f"source_identity={value!r} 少了命名空间：形状是 "
                "<namespace>:<value>（例如 osm:node/123、site:example.org）。\n"
                "  一个没有命名空间的来源编号，与别的命名空间下的编号会撞成同一个"
                "字符串，而它们认的不是同一个东西。"
            )
        namespace, _, tail = value.partition(":")
        if not namespace or not tail:
            raise ValueError(
                f"source_identity={value!r} 的命名空间或取值是空的。空值请写空串，"
                "不要写一个半截的标识。"
            )
        return value

    @field_validator("claimed_official_domain")
    @classmethod
    def _the_claimed_domain_is_a_bare_host(cls, value: str) -> str:
        """一个**裸主机名**：带 scheme、端口或路径的值在这里就报错。

        理由与 ``RowDisposition.claimed_official_domain`` 上那条一样：这一格下游是拿去跟
        **别的域名**比的（「上游说的那个」对「我们证得的那个」），而
        ``https://example.org`` 与 ``example.org`` 永远不相等 —— 差别在报告上看不
        出来，只会读成「上游说的与我们证得的不是同一个」。所以判据是同一个函数
        (:func:`check_slug_safe`)，两处不能各写一份：它**是**从那一格过来的
        （生产者见 ``application/pool.py``），而一边接受、另一边拒绝，会让同一句话
        经过一次落盘之后消失。
        """
        if not value:
            return ""
        return check_slug_safe(value, "claimed_official_domain")

    @field_validator("verified_official_domains")
    @classmethod
    def _every_verified_domain_is_a_bare_host(cls, value: list[str]) -> list[str]:
        """``verified_official_domains`` **逐项**过同一条判据。

        与 ``RowDisposition`` 上那一格同一个理由、同一个函数，两处不能各写一份：
        这一格是绑定那一层判「这个邮箱域算不算外来」的依据（SOP L121），一项读不
        出来而无人在意，集合就少一个元素，于是本账号自己官网上登着的邮箱会被拒成
        外来域 —— 报告上那一行看起来只是「域名少了一个」。

        这里**不收 list 形态的宽松输入**：``field_validator`` 拿到的是整个列表，
        把它交给 ``check_slug_safe`` 只会得到「它不是字符串」，把**哪一项**坏了吞掉。
        """
        seen: list[str] = []
        for item in value:
            if not item:
                raise ValueError(
                    "verified_official_domains 里有一项是空的。\n"
                    "空串在这一格会被读成「没有这个域」，与「没查出来」不同。"
                )
            if item != item.strip() or item != item.lower():
                raise ValueError(
                    f"verified_official_domains 里的 {item!r} 不是规范形式"
                    "（去空白、全小写）。这一格会被逐字拿去与邮箱域**精确**比较，"
                    "比不相等时症状是「这个域不算官方」。"
                )
            check_slug_safe(item, "verified_official_domains")
            seen.append(item)
        if len(set(seen)) != len(seen):
            raise ValueError(
                f"verified_official_domains 里有重复项：{seen!r}。\n"
                "重复不是「两份证据」：单数格 verified_official_domain 只在**恰好一个**"
                "官方域时给值，而两元素的重复集合会让那条判据成立 —— 「唯一官方域」"
                "这句话于是由一句重复造出来。"
            )
        return value

    @field_validator("domain_evidence_ids")
    @classmethod
    def _every_domain_evidence_id_is_canonical(cls, value: list[str]) -> list[str]:
        """证据 id 逐项过 ``check_canonical_id``，理由与 ``RowDisposition`` 那一格同。"""
        for item in value:
            check_canonical_id(item, "domain_evidence_ids", max_length=FILENAME_ID_MAX_LENGTH)
        return value

    @field_validator("first_seen_at", "last_verified_at")
    @classmethod
    def _timestamps_are_aware(cls, value: datetime | None, info: ValidationInfo) -> datetime | None:
        """见 :func:`_timestamps_must_be_aware` —— 判据在那里，这里只是挂上去。"""
        return _timestamps_must_be_aware(value, info)

    def source_parts(self) -> tuple[str, str]:
        """``("osm", "node/123")`` / ``("site", "example.org")``；空来源给出两个空串。

        ``osm_type`` 与 ``osm_id`` 就是第二段的 ``partition("/")`` —— 它们**在这里
        只是上游编号的两半**（见类文档），不是账号身份。
        """
        if not self.source_identity:
            return ("", "")
        namespace, _, tail = self.source_identity.partition(":")
        return (namespace, tail)

    @model_validator(mode="after")
    def _domain_verdict_agrees_with_the_fields(self) -> "VenueAsset":
        """四格合成一个判定的唯一形状。**每一半都要**，理由见类文档。

        * ``CONFIRMED``：域名集合非空 + 证据非空；
        * ``ABSENT``：域名集合为空 + 证据非空（「查过、没有」要凭据）；
        * ``UNKNOWN``：域名集合为空 + 证据为空（没查过就不该有支持它的证据）。

        还有一条反向的：``domain_evidence_ids`` 有东西时判定**不许**是 ``UNKNOWN``
        —— 证据在那里而没人做判定，读起来像「查过了但忘了写结论」，而它会被
        下游读成「还没查」。

        以及单数那一格的**读法**判据：它等于集合在恰好一个元素时的那个元素，
        其余情况为空。少了这一条，一行可以在集合里写两个官网、同时把单数那格
        填成第三个 —— 而绑定那一层按集合判、报告按单数格读。
        """
        expected_singular = (
            self.verified_official_domains[0] if len(self.verified_official_domains) == 1 else ""
        )
        if self.verified_official_domain != expected_singular:
            raise ValueError(
                f"verified_official_domain={self.verified_official_domain!r} 与 "
                f"verified_official_domains={self.verified_official_domains!r} 不一致："
                f"单数那一格是集合的读法，恰好一个时是它、其余时候为空，"
                f"应当是 {expected_singular!r}。"
            )
        if self.domain_verdict is DomainVerdict.CONFIRMED:
            if not self.verified_official_domains or not self.domain_evidence_ids:
                raise ValueError(
                    "domain_verdict=confirmed 要求同时给出 verified_official_domains "
                    "与 domain_evidence_ids：一个「确认了官网」的判定，缺了域名或"
                    "缺了凭据都不是它。"
                )
        elif self.domain_verdict is DomainVerdict.ABSENT:
            if self.verified_official_domains or not self.domain_evidence_ids:
                raise ValueError(
                    "domain_verdict=absent 是「查过、确实没有」：它要求 "
                    "verified_official_domains 为空**且** domain_evidence_ids 非空。\n"
                    "  域名那格非空说明查到了东西，证据那格为空说明没人查过 —— 两者"
                    "都不是「确认不存在」。"
                )
        elif self.verified_official_domains or self.domain_evidence_ids:
            raise ValueError(
                "domain_verdict=unknown 是「还没查」：它要求 "
                "verified_official_domains 与 domain_evidence_ids 都是空的。\n"
                "  已经写下了域名或证据、却把判定留成 unknown，读起来是「还没查」，"
                "而事实是查过了 —— 换一个判定值或者把那两格去掉，不要两者都留着。"
            )
        return self


class ResponsibleEntity(_Strict):
    """一个**拥有 / 运营 / 维护 / 预约控制 / 采购**场地资产的责任组织。

    ``entity_id`` 与 ``AccountRecord.account_id`` 是**同一空间里的同一个标识**：
    被研究的目标组织在这里是责任实体，在名单里是账号。分成两个模型是刻意的 ——
    「谁负责这块场地」这件事可能超出当前名单，而名单里的组织也可能一块场地都不负责。
    """

    entity_id: str
    name: str
    entity_type: str = ""
    #: 同 ``VenueAsset.country``：``None`` 是「还没查」。
    country: str | None = None
    domain: str = ""
    notes: str = ""

    @field_validator("entity_id")
    @classmethod
    def _id_is_slug_safe(cls, value: str) -> str:
        return check_slug_safe(value, "entity_id")


class AccountVenueRelation(_Strict):
    """「谁对哪块场地负什么责」的一条关系。

    ## 键是四段，``workspace`` 那一段不是装饰

    键是 ``(workspace, account_id, venue_asset_id, relation)``。§5 要的是
    「Asset ↔ Account 的证据关系」，而一条责任关系是一个**判定**：同一块场地与
    同一个账号，在两个 Scene 下可以判出不同的关系（一个 Scene 在找场地运营方，
    另一个在找采购方，同一次抓取支持两条不同的结论）。少了 ``workspace`` 这
    一段，一个库里装着的行无法回答「这是哪个 Scene 判的」，写入路径也无从拒绝
    别的 Scene 的行 —— 与 :attr:`VenueAsset.workspace`、``Evidence.workspace``
    逐字同一条理由：**跨 Scene 的复用止于工作区边界**，而「缺省 = 当前 Scene」的
    默认值会把**没写血统**变成一次不留痕的放行。

    ## ``workspace`` 挡得住跨 Scene，挡不住换版

    ``region`` 或 ``profile_id`` 改了以后，``SCENE_IMPACT_RULES``
    （``domain/scene.py``）要求责任关系重判。而盘上**没有任何一格**能说明某一行是
    改之前判的。这一格故意没有在这一版里补上，理由有两半：

    * 它属于「什么该重做」那一层（§11 的 ``what_is_due(scene, now)``），今天
      还没有读者 —— 一列谁都不读的字段在本仓叫死模型，而它读起来像做完了；
    * 资产池与账号池缺的是**同一格**，只给关系表加会让三张表各有一半，而半套
      机制比没有更坏。

    所以当下这两格诚实的读法是：:attr:`first_seen_at` / :attr:`last_verified_at`
    说的是「我们什么时候看的」，**不是**「当时按哪一版规则判的」。这一格的去向
    写在 ``docs/DATA_MODEL.md`` 的那一节里。

    ## 没有证据的关系写不出来

    ``evidence_ids`` 至少一条，由
    :meth:`_a_relation_without_evidence_is_not_written` 在校验期挡。这不是一句
    自觉：``VenueRelation`` 的文档里写着「证据的要求对 ``UNRESOLVED`` 与对其余
    五个一样严」，而一条没有证据的关系行读起来是「我们查到了」，事实是「我们
    猜过」—— 这一格的错法与本仓那台伪造证据的机器是同一个。

    ## 一个账号可以同时有多种责任，所以键里含 ``relation``

    ``relation`` 不是「这对 (账号, 场地) 是什么关系」的**唯一**答案：同一个 Trust
    可以既 ``owns`` 又 ``maintains`` 一块场地，那就是两条行。反过来，
    ``UNRESOLVED`` 说的不是「没关系」，而是「有关系、但关系还没定下来」——
    它与「没有这一行」是两件事，不许悄悄合并（那条理由写在 ``VenueRelation`` 上）。

    ## 这一行指得出它是哪一次判定得出的

    ``decision_record_ids`` 是 ``decision_record`` 的主键（在 ``decide`` 的回执里
    那一格叫 ``record_id``，在一次研究的产出里那一格叫 ``decision_id`` —— 三个名字
    是**同一个字符串空间**，这里用表名，因为列的是那张表的主键）。§3 要的是判定
    真的被下游消费，而上一轮真实验收量到的「被消费」只能按最弱的那条判据成立：
    关系的证据集合与某条判定的 ``evidence_refs`` 恰相同、答案取值也恰相同。

    那条判据不是因果证明 —— 同一个账号对同一批页面可以问出两个不同的问题，两条
    判定引用同一批证据、给出同一个取值，而**集合相等**分不出这两件事。所以连线
    从「推出来的」换成「写下来的」：这一行说得出产生它的那次调用，而那个 id 能
    直接读回来（``DecisionRecordStore.get_decision``）。

    ## 为什么是复数、为什么取并集

    与 ``evidence_ids`` 逐字同一条：一条已经被判过的关系可以被**再判一次**（新的
    证据、更晚的一次研究），而第二次折进来时这一行的证据取并集。只留最新那一个
    id 的话，更早那次调用从结果上**反查不到**，而它带进来的证据还在这一行里 ——
    那是一条挂着无主证据的关系行。取并集之后仍然不是「完整因果证明」（一次判定
    里的推理过程不在盘上），但至少这一行指得出的每一次调用都真的发生过。

    ## 空表在**这一层**是合法的，在研究那条路径上不是

    这一格与 ``evidence_ids`` 的严格程度不同：没有证据的关系行写不出来（校验期
    直接报错），而没有判定支撑的关系行**写得出来** —— ``remember_responsibility``
    那条手工路径就是人把结论交上来，它手里没有、也不该有判定台账。它的行如实
    带着空表，而那正是「这一行不是判出来的」的读法。

    研究那条路径上不允许空：``remember_researched_relations`` 的每一条结论本来
    就必须带 ``decision_id`` 并当场回库核对，核对不过的那一行根本不写。
    """

    workspace: str
    account_id: str
    venue_asset_id: str
    relation: VenueRelation
    #: 这一条关系的证据（``Evidence.evidence_id`` 同空间的指针）。**至少一条** ——
    #: 见类文档；空表在校验期直接报错，不是一个「暂时还没有」的中间态。
    evidence_ids: list[str] = Field(default_factory=list)
    #: 产生 / 再次确认这一行的那些判定（``decision_record.record_id``）。
    #: **空表是合法的**，而它的意思只有一个：「这一行不是一次判定得出的」——
    #: 见类文档；手工那条路径写下来的行就是这一种。
    decision_record_ids: list[str] = Field(default_factory=list)
    #: 这一行第一次落进池子的时刻。同 :attr:`VenueAsset.first_seen_at`：它是一次
    #: **关于我们的事实**（我们何时记下了它），由写路径当场给出，不从上游行里猜。
    first_seen_at: datetime
    #: 最后一次**真的重新看过**它的时刻。``None`` = 只有它进池子那一次。
    last_verified_at: datetime | None = None

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "workspace")

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "account_id")

    @field_validator("venue_asset_id")
    @classmethod
    def _venue_id_is_slug_safe(cls, value: str) -> str:
        """场地 id 只要求 slug-safe：它**不**落盘成文件名。

        与 ``account_id`` 分开写不是洁癖：合在一个 ``@field_validator`` 里就只能
        用较松的那条，而较松的那条放过去的东西，正是「两个账号合并进一个文件」。
        """
        return check_slug_safe(value, "venue_asset_id")

    @field_validator("decision_record_ids")
    @classmethod
    def _every_decision_id_is_canonical(cls, value: list[str]) -> list[str]:
        """判定的 id 与产出上那一格（``RelationFinding.decision_id``）用**同一条**判据。

        这一格不落盘成文件名，所以严格的理由不是 ``account_id`` 那条（两个 id 会
        合并进一个文件）。理由是**同一条纪律要在两处是同一句话**：产出上收得下、
        表上写不下的值会让「交上去的」与「写下来的」对不上，而这里的错法是
        ``get_decision`` 拿着一个永远读不回来的 id 去查 —— 反查失败看起来像
        「这次判定没落盘」，不像「id 被改过一个字」。
        """
        return [check_canonical_id(ref, "decision_record_ids") for ref in value]

    @field_validator("first_seen_at", "last_verified_at")
    @classmethod
    def _timestamps_are_aware(cls, value: datetime | None, info: ValidationInfo) -> datetime | None:
        """见 :func:`_timestamps_must_be_aware` —— 判据在那里，这里只是挂上去。"""
        return _timestamps_must_be_aware(value, info)

    @model_validator(mode="after")
    def _a_relation_without_evidence_is_not_written(self) -> "AccountVenueRelation":
        """空证据表在校验期就报错，理由见类文档。

        报错而不是「先收下、等有证据再补」：这一行的错法是**读起来像查过了**，
        而一条等着补证据的行在下游与一条查过的行长得一模一样。
        """
        if not self.evidence_ids:
            raise ValueError(
                "AccountVenueRelation 至少要一条证据：说得出「有关系」也是一个要凭据"
                "的判定。\n"
                "  还没有证据时正确的做法是**先不写这一行**，而不是写一条空证据的 —— "
                "后者在下游读起来是「我们查到了」。"
            )
        return self

    def relation_key_parts(self) -> tuple[str, str, str, str]:
        """四段键的**结构**形态：``(workspace, account_id, venue_asset_id, relation)``。

        与 :meth:`relation_key` 是同一件事的两种形态，而不是两套判据：那个是给人读的
        串，这个是给字典当键用的元组。分成两个方法是因为**拼串会把两个不同的键拼成
        同一个串**（含分隔符的取值），而元组不会 —— 拿串当键时那件事由
        ``check_canonical_id`` 挡着，但两道判据叠在一起才安全的多一层运气，不如让
        字典的键从结构上来。
        """
        return (self.workspace, self.account_id, self.venue_asset_id, self.relation.value)

    def relation_key(self) -> str:
        """这一行的键，**算出来的**：``<workspace>/<account_id>/<venue_asset_id>/<relation>``。

        §5 的字段清单里第一项是 ``relation_id``。这里给它的是一个派生的键，不是
        一列存下来的字段，理由是本仓那条「两处写同一句话就是缺陷」：四个部件各自
        已经是规范形式（``workspace`` / ``account_id`` 过 ``check_canonical_id``、
        ``venue_asset_id`` 过 ``check_slug_safe``、``relation`` 是闭集枚举），所以
        键**就是**身份本身 —— 再存一个句柄就是同一句话的第二份拷贝，而它会在某一次
        只改了一处的写路径上漂开。

        与 :attr:`VenueAsset.venue_asset_id` 的对照值得写下来：那一格**是**字段，
        因为它的自然键（``source_identity`` 或「名字 + 位置」）要一次规范化才算得
        出来，而那一次计算必须只发生一次并且落盘（``venue_asset_id_for``）。
        这里没有这一步。

        它**不是**文件名（含 ``/``），也不该被当成一个 id 用。它是给人读的键：
        报告里「哪一条关系被写了」指的就是它。
        """
        return "/".join(self.relation_key_parts())


class Person(_Strict):
    """一个**具名**的自然人。

    人没有自己的 A/B/C/D/X —— 等级挂在路线上。同一个人可以有多条路线
    （工作邮箱、部门邮箱），它们各自的强度与语境不同。
    """

    person_id: str
    full_name: str
    account_id: str = ""
    notes: str = ""

    @field_validator("full_name")
    @classmethod
    def _name_is_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Person.full_name 不能为空 —— 没有名字的人不是一条证据")
        return value

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        """空值合法，含义明确：这个人**不属于名单上的任何账号**（还没归位）。

        非空时不规范就抛 —— 这个字段此前连 slug-safe 都没有，于是它是这一族里
        唯一一个「怎么填都行」的成员。
        """
        return check_canonical_id(value, "account_id") if value else value

    @classmethod
    def for_account(cls, *, account_id: str, full_name: str) -> "Person":
        return cls(
            person_id=_person_id(account_id, full_name),
            full_name=full_name,
            account_id=account_id,
        )


class Role(_Strict):
    """一个**字面岗位**，带它在路线里扮的角色与业务口径。

    ``role_class`` / ``business_function`` 由 profile 的 role matrix 编译而来
    （见 ``marketops/adapters/profile_loader.py``）—— 引擎不认识 ``grounds supervisor``，
    它只认识 ``RoleClass.DECISION``。业务词因此留在 ``profiles/`` 里。
    """

    role_id: str
    title: str
    account_id: str = ""
    person_id: str = ""
    role_class: RoleClass = RoleClass.UNKNOWN
    business_function: BusinessFunction = BusinessFunction.UNKNOWN

    @field_validator("title")
    @classmethod
    def _title_is_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Role.title 不能为空")
        return value

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        """与 ``Person`` 同一条判据、同一个空值语义。"""
        return check_canonical_id(value, "account_id") if value else value

    @classmethod
    def for_account(
        cls,
        *,
        account_id: str,
        title: str,
        role_class: RoleClass,
        business_function: BusinessFunction,
    ) -> "Role":
        return cls(
            role_id=_role_id(account_id, title),
            title=title,
            account_id=account_id,
            role_class=role_class,
            business_function=business_function,
        )


class ContactRoute(_Strict):
    """一条**可联系的路线**。定级的主语就是它。

    形式定义（SOP v1 L125–L129）::

        ContactRoute = Account + Person/Department + Role/Business Function
                       + literal public Email + Evidence

    ``email`` 必填且非空，这是 §11 的**类型层**落地：SOP 的 A 与 B **都**要求
    「真实公开业务邮箱」。旧 pipeline 把 B 读成「有具名角色但没有邮箱」，
    那是一个缺陷 —— 没有邮箱的具名角色是一条**未解决的路线机会**
    （:class:`RouteOpportunity`），不是一条 B 级路线。

    路线与账号是两个概念，所以这里**没有**账号级的聚合字段：账号有多少条路线、
    账号是否 eligible，都不是这一条路线能回答的。把账号聚合塞进路线判定，
    会让「组织对、人不对」这种情形无处安放。
    """

    workspace: str
    account_id: str
    #: 这条路线绑在哪块场地资产上（可空：组织级入口不对应单一场地）。
    venue_asset_id: str = ""
    organisation: str = ""

    # --- Person / Role ---
    person_id: str = ""
    person_name: str = ""
    role_id: str = ""
    role_title: str = ""
    role_class: RoleClass = RoleClass.UNKNOWN
    business_function: BusinessFunction = BusinessFunction.UNKNOWN

    # --- 入口性质与记录块绑定（SOP v1 L105）---
    entry_kind: EntryKind = EntryKind.NAMED_ROLE
    #: 记录块绑定层级。``None`` **是有含义的取值**：这条路线不属于 SOP 定义过的
    #: 任何一级（没有同块的具名人或岗位，也不是泛化官方入口）。把它强行读成
    #: P1 或 P4 会让「这是哪一级」变成实现细节；留成 ``None``，定级规则会因
    #: 没有匹配而 fail closed，读到的是「SOP 没定义这一级」这一事实。
    block_priority: RecordBlockPriority | None = None
    binding: RouteBinding = RouteBinding.NONE

    # --- literal public email：必填（SOP v1 L126）---
    email: str
    email_domain: str = ""

    #: 支撑本条路线的全部证据 id。空列表意味着「无证据」，非 X 判定不允许出现。
    evidence_ids: list[str] = Field(default_factory=list)
    grade: Grade = Grade.X
    grade_reason: GradeReason = GradeReason.NO_ELIGIBLE_EVIDENCE
    status: RouteStatus = RouteStatus.OBSERVED
    #: 证据之间是否存在冲突（同页多邮箱 + 具名角色，无法确定对应关系）。
    has_conflict: bool = False

    # --- §9 的四个时刻与当前性 ---
    #: 我们**第一次看到**这条路线所依据的材料的时刻，由写路径当场给出
    #: （``bind`` 从它手上那几行证据折出来），不是推理出来的。
    #:
    #: 与 :attr:`MarketAccount.first_seen_at` 同一个词、同一件事：这一族行是池子里的
    #: 对象。``TemporalAssertion.observed_at`` 说的是**断言**的观测时刻 —— 生产者把
    #: 这一格填进那一格，两个词指的是同一个瞬间，但各自描述的对象不同（一行的诞生
    #: 与一条断言的观测），所以这里没有第二个词可用。
    first_seen_at: datetime | None = None
    #: 我们**最近一次确认它仍然成立**的时刻。今天**没有生产者**：重新抓一次材料
    #: 不是重新验证（模块 :mod:`marketops.domain.temporal` 的全部理由），所以这一格
    #: 留给「真的去核过」的那一步，而那一步还没有落地。``None`` = 只有第一次看到那次。
    last_verified_at: datetime | None = None
    #: 来源自己声明的发布时刻。一条路线由多行材料支撑时取**唯一**的那一个值
    #: （:func:`~marketops.domain.evidence.moments_from_evidence`），分歧时为 ``None``。
    source_published_at: datetime | None = None
    #: 事实自身的有效区间，若来源明说了。今天同样没有生产者。
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    #: 新鲜度的**判定结论**（:func:`marketops.domain.temporal.evaluate_freshness`
    #: 的产出）。``None`` = 还没判过，与「判过、结论是 unknown」是两件事。
    freshness: Freshness | None = None

    # --- 本阶段硬约束：这两个字段不能被打开 ---
    research_only: Literal[True] = True
    send_authorized: Literal[False] = False

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        """见 :attr:`~marketops.domain.evidence.Evidence.workspace` —— 同一个字段名、
        同一条判据、同一个理由。

        路线与机会在这一格上不能与证据不同：一条路线挂的 ``evidence_ids``
        只可能在**它自己的工作区**里解得开（``EvidenceStore`` 的读路径对别的
        工作区的那一行是拒绝，不是空），所以一个跨工作区的 ``evidence_ids``
        在 ``application/evidence_refs.py`` 那条判据上会整份失败，而不是
        静默地少一半依据。
        """
        return check_canonical_id(value, "workspace")

    @field_validator("email")
    @classmethod
    def _email_must_be_literal_and_non_empty(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError(
                "ContactRoute.email 必填且不得带首尾空白。"
                "没有字面公开邮箱的具名角色是 RouteOpportunity，不是一条路线（SOP v1 L126）。"
            )
        return value

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        """账号 id 有一条比 slug-safe 更严的要求：它必须已经**是**文件名。

        位置 id 与实体 id 只要求 slug-safe（它们不落盘成文件名），而账号 id
        会 —— ``<account_id>.jsonl``、``mirrors/<scope>/``。同一个字段在两个
        模型里获得两种命运，是「两个不同的账号静默合并」的成因，所以这一族
        与 ``AccountRecord`` / ``Evidence`` 用同一条判据（``domain/util.py``）。
        """
        return check_canonical_id(value, "account_id")

    @field_validator(
        "first_seen_at",
        "last_verified_at",
        "source_published_at",
        "valid_from",
        "valid_to",
    )
    @classmethod
    def _timestamps_are_aware(cls, value: datetime | None, info: ValidationInfo) -> datetime | None:
        """见 :func:`_timestamps_must_be_aware` —— 判据在那里，这里只是挂上去。"""
        return _timestamps_must_be_aware(value, info)

    @model_validator(mode="after")
    def _freshness_is_about_this_row(self) -> "ContactRoute":
        """见 :func:`_freshness_belongs_to_this_row` —— 判据在那里，这里只是挂上去。"""
        _freshness_belongs_to_this_row(
            self.freshness,
            workspace=self.workspace,
            account_id=self.account_id,
            predicate=self.assertion_predicate,
            value=self.asserted_value,
            first_seen_at=self.first_seen_at,
        )
        return self

    @property
    def assertion_predicate(self) -> str:
        """这一行在断言什么（:attr:`~marketops.domain.temporal.TemporalAssertion.predicate`）。"""
        return ROUTE_ASSERTION_PREDICATE

    @property
    def assertion_subject_ref(self) -> str:
        """这一行断言的主语 —— 见 :func:`route_subject_ref`。"""
        return route_subject_ref(self.workspace, self.account_id)

    @property
    def expected_assertion_id(self) -> str | None:
        """**本行**那条断言的 id；没有 ``first_seen_at`` 时算不出来（``None``）。

        生产者按它填 :attr:`freshness` —— 它不是「另算一遍」，而是与行上校验器
        共用的同一处公式（:func:`_expected_assertion_id`），所以填出来的那一格
        一定过得了本行的判据。``None`` 是一个**结论**：这一行没有时刻锚点，
        因此它撑不起任何 ``currentness``，而不是「随便挑一个时刻顶上」。
        """
        return _expected_assertion_id(
            workspace=self.workspace,
            account_id=self.account_id,
            predicate=self.assertion_predicate,
            value=self.asserted_value,
            first_seen_at=self.first_seen_at,
        )

    @property
    def asserted_value(self) -> str:
        """这一行断言的**值**：那条字面公开邮箱。

        只有邮箱。姓名与岗位是这条断言的**依据**（它们决定它够不够格成为一条路线），
        不是它断言的内容：把姓名拼进值里，同一个邮箱会因为页面上换了一种写法而变成
        两条断言，而它在账号上仍然是同一条路线。
        """
        return self.email

    @model_validator(mode="after")
    def _p3_has_no_email_so_it_cannot_be_a_route(self) -> "ContactRoute":
        if self.block_priority is RecordBlockPriority.P3:
            raise ValueError(
                "P3 的定义是「有场地/责任关系证据但没有邮箱」（SOP v1 L105）。"
                "本模型要求 email 非空，两者不可能同时成立 —— "
                "P3 的情形应记为 RouteOpportunity。"
            )
        return self

    @model_validator(mode="after")
    def _evidence_required_for_non_x(self) -> "ContactRoute":
        if self.grade is not Grade.X and not self.evidence_ids:
            raise ValueError(
                f"grade={self.grade.value} 但没有 evidence_ids。"
                "任何非 X 的判定都必须挂证据（规则值未知时应返回 X）。"
            )
        return self

    @model_validator(mode="after")
    def _cannot_be_sent_without_authorization(self) -> "ContactRoute":
        if self.status in UNREACHABLE_WITHOUT_AUTHORIZATION:
            raise ValueError(
                f"status={self.status.value} 需要 send_authorized=true，"
                "而本阶段该值恒为 False —— 这条路径不可能被合法地构造出来。"
            )
        return self


class RouteOpportunity(_Strict):
    """一条**尚未解决的路线机会**：有相关人/岗位证据，但没有字面邮箱。

    它**不是**一条 B 级路线。SOP v1 L126 要求 A 与 B 都带真实公开业务邮箱；
    没有邮箱的具名角色仍然是有价值的产出 —— 它告诉我们「这个账号上有个
    场地负责人」，下一步是去找他的公开邮箱 —— 但把它标成 B 就等于声称
    我们取得了一条可联系的路线，而我们没有。

    默认 ``block_priority=P3``，因为「有场地/责任关系证据但无邮箱」正是它的定义。
    """

    workspace: str
    account_id: str
    organisation: str = ""
    venue_asset_id: str = ""
    person_id: str = ""
    person_name: str = ""
    role_id: str = ""
    role_title: str = ""
    role_class: RoleClass = RoleClass.UNKNOWN
    business_function: BusinessFunction = BusinessFunction.UNKNOWN
    block_priority: RecordBlockPriority = RecordBlockPriority.P3
    evidence_ids: list[str] = Field(default_factory=list)
    #: 为什么这条机会还不是路线。当前只可能有一种取值（见 REASON_NO_LITERAL_EMAIL）。
    reason: str = "no_literal_email"

    # --- §9 的四个时刻与当前性（与 :class:`ContactRoute` 同名、同义、同判据）---
    #: 我们第一次看到这条机会所依据的材料的时刻。见 ``ContactRoute.first_seen_at``。
    first_seen_at: datetime | None = None
    #: 最近一次确认它仍然成立。今天没有生产者 —— 见 ``ContactRoute.last_verified_at``。
    last_verified_at: datetime | None = None
    source_published_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    #: 新鲜度判定结论。机会同样会过期：一个人可能已经离任，或者已经有邮箱了。
    freshness: Freshness | None = None

    research_only: Literal[True] = True
    send_authorized: Literal[False] = False

    @field_validator("workspace")
    @classmethod
    def _workspace_is_canonical(cls, value: str) -> str:
        """见 :attr:`ContactRoute.workspace` —— 同一条判据、同一个理由。"""
        return check_canonical_id(value, "workspace")

    @field_validator("account_id")
    @classmethod
    def _account_id_is_canonical(cls, value: str) -> str:
        """账号 id 有一条比 slug-safe 更严的要求：它必须已经**是**文件名。

        位置 id 与实体 id 只要求 slug-safe（它们不落盘成文件名），而账号 id
        会 —— ``<account_id>.jsonl``、``mirrors/<scope>/``。同一个字段在两个
        模型里获得两种命运，是「两个不同的账号静默合并」的成因，所以这一族
        与 ``AccountRecord`` / ``Evidence`` 用同一条判据（``domain/util.py``）。
        """
        return check_canonical_id(value, "account_id")

    @field_validator(
        "first_seen_at",
        "last_verified_at",
        "source_published_at",
        "valid_from",
        "valid_to",
    )
    @classmethod
    def _timestamps_are_aware(cls, value: datetime | None, info: ValidationInfo) -> datetime | None:
        """见 :func:`_timestamps_must_be_aware` —— 判据在那里，这里只是挂上去。"""
        return _timestamps_must_be_aware(value, info)

    @model_validator(mode="after")
    def _freshness_is_about_this_row(self) -> "RouteOpportunity":
        """见 :func:`_freshness_belongs_to_this_row` —— 判据在那里，这里只是挂上去。"""
        _freshness_belongs_to_this_row(
            self.freshness,
            workspace=self.workspace,
            account_id=self.account_id,
            predicate=self.assertion_predicate,
            value=self.asserted_value,
            first_seen_at=self.first_seen_at,
        )
        return self

    @property
    def assertion_predicate(self) -> str:
        """这一行在断言什么：**这个账号上有这样一个人/岗位上还没有可联系的邮箱**。"""
        return OPPORTUNITY_ASSERTION_PREDICATE

    @property
    def assertion_subject_ref(self) -> str:
        """这一行断言的主语 —— 见 :func:`route_subject_ref`（与路线同一个主语）。"""
        return route_subject_ref(self.workspace, self.account_id)

    @property
    def expected_assertion_id(self) -> str | None:
        """**本行**那条断言的 id；没有 ``first_seen_at`` 时算不出来（``None``）。

        与 ``ContactRoute.expected_assertion_id`` 同一条：判据只有一处
        （:func:`_expected_assertion_id`），两族共用。谓语不同，所以同一个人
        在「有邮箱」与「没邮箱」两种状态下的断言 id 必然不同。
        """
        return _expected_assertion_id(
            workspace=self.workspace,
            account_id=self.account_id,
            predicate=self.assertion_predicate,
            value=self.asserted_value,
            first_seen_at=self.first_seen_at,
        )

    @property
    def asserted_value(self) -> str:
        """姓名与岗位**一起**才是这条断言的内容。

        与路线相反，机会没有邮箱可当值，它能说的就是「关于谁」；而「有姓名没岗位」
        与「有岗位没姓名」是两件不同的事（:meth:`_needs_something_to_be_about`
        只要求至少有一个），所以两格都进值，空的那些不进（见 :func:`_asserted_value`）。
        """
        return _asserted_value(self.person_name, self.role_title)

    @model_validator(mode="after")
    def _needs_something_to_be_about(self) -> "RouteOpportunity":
        if not (self.person_name or self.role_title):
            raise ValueError(
                "RouteOpportunity 必须至少有一个字面姓名或字面岗位 —— "
                "否则它不是「一条未解决的机会」，只是「这里什么都没有」。"
            )
        return self


__all__ = [
    "VenueAsset",
    "ResponsibleEntity",
    "AccountVenueRelation",
    "Person",
    "Role",
    "ContactRoute",
    "RouteOpportunity",
]

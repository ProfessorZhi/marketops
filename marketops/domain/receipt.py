"""母池回执 —— 一次「把上游母池读进来」之后，**每一行去了哪里**的唯一记录。

本模块是 ``contracts/source-receipt.schema.json`` 的来源：schema 由
``marketops.schema`` 从本文件导出，``tests/test_contracts_sync.py`` 断言两者一致。
契约**从代码导出**，不手写。

# 它填的是哪一个洞

``JobInputKind`` 有三种，``SOURCE_RECEIPT`` 是其中之一（§2 逐字：existing source
receipt / known Accounts / approved discovery scope）。在这一轮之前，它**没有生产者**
—— ``marketops/application/inputs.py`` 的 ``_NO_PRODUCER`` 里那句话今天仍然要点名
理由，只是理由变了：从前是「没有东西把一次抓取的结果写回成一份可再投喂的回执」，
现在那条路径存在了，而**回执是一份文件**（``inputs_dir/<receipt_id>.receipt.json``），
于是它带着一个名字、一个内容摘要与一份内容重新进入受理。

SOP L82 逐字：「搜索结果、单页新闻或单一 contact 页只能作补充证据，**不能当全量母池**。」
一份回执不改变这句话：它记的是**这一批行是怎么被读的**，不是「母池已经补齐」。
所以它不产出一个「合格账号数」，只产出**每一行的处置**。

# 为什么每一行都必须有一个处置

Member-C 的原话（``sources/member-c-private/marketops-messages.jsonl`` pos30）：
「纯 osm 只有代码 id，很少能跑到具体场地名称，**所以还需要在 osm 基础上补齐**」。
补齐意味着：读进来的那 300 行里，有的补成了可研究的对象，有的归并到了别的行上，
有的按 profile 的排除项被排除，有的**还缺名字**，有的压根读不出来。

这五种去向在报告上必须**分得开**，而且必须**加起来等于原始行数**。
一条静默丢掉的输入行，与「那一行本来就不在池子里」在报告里长得一样 ——
而前者的后果是：剩下 299 行被当成全池，于是「母池覆盖率」这个分母从一开始就是错的。
:class:`SourceReceipt` 的 ``ordinal`` 判据就是这条不变量的执行器：处置的序号必须是
``0..raw_row_count-1`` 的一个排列，**不多不少**。少一行、重一行都构造不出来。

同一件事的另一半是：**空池不是成功**。``raw_row_count >= 1`` 是硬约束，
而 ``blocker`` 在没有任何 ``resolved`` 行时返回一条说得出的理由 ——
「0 行」与「读进 300 行、一行都没成」在报告里必须长得不一样，而后者才是有信息量的那种。

# 来源身份**不是**账号身份

§4.3 逐字：「OSM 来源键包含对象类型与 ID；**它是来源标识，不直接成为 Account 主键**。」
落在字段上是两件东西：

* ``RawCandidateRow.source_identity`` —— 上游**它自己**的标识（``node/12345`` 这种），
  形状是 ``<namespace>:<value>``，取值对引擎**不透明**。
* :func:`row_id_for` —— 本仓**这一行**的身份（规范形式、内容寻址）。

**两者不得缝成一个，而这条缝的代价已经被量过一次。** 2026-09-23 那次真实运行里，
worker 拿到的是内部 slug ``kent-003``，于是它花掉 9 次 Serper 查询去搜这个 id，
十条命中全是无关的同名物 —— 而盘上没有任何一处告诉它这是在说 Example School One。
那条失败链的起点正是「把一个来源侧/内部侧的标识当成了可以用来检索的身份」。
所以这里有一条**机械**的判据，不是一句约定：

* ``source_identity`` **不得**是一个像邮箱的串（否则它会被下游读成联系方式的依据）；
* 行**只有** ``source_identity``、拿不出人类可读的指称对象时，它的处置**只能是**
  ``pending`` —— 而 ``pending`` 的行在类型层面就带不了 ``resolved_name``
  （见 :class:`RowDisposition` 的 ``non_resolved_rows_carry_no_subject``），
  因此在 ``subjects_for`` 那条路上**一个 work item 都建不出来**，
  于是**一次检索、一次模型调用都不会发生**。
  这正是 ``identity_refusal`` 在发现期的对应物：那里拒的是「只有 slug 的清单」，
  这里拒的是「只有 slug 的那一行」。

# 重放必须幂等，而幂等要落在**内容**上

``receipt_id`` 由四样东西派生：来源方法、批次标签、原始工件摘要、以及**处置序列本身的摘要**
（:func:`receipt_id_for`）。前两样说明「这是哪一批」，第三样说明「读的是哪些字节」，
第四样说明「读成了什么」。

第四样是关键，不是冗余：**同样的输入 + 同样的规则 → 同一个 id → 同一个文件 → 重放不新建任何东西**；
而**同样的输入 + 改过的规则 → 另一个 id → 另一个文件**，两份都留在盘上。
少了它，第二次导入会**覆盖**第一次的回执，于是「这批行被重新处置过一次、
两次的结论不一样」这件事在盘上不留痕迹 —— 而它是审计里最先要问的那一个问题。
把规则版本写成一个字段也能做到同样的事，但那需要维护一张版本表；
处置摘要不需要任何约定：改没改过，比对两个 id 就知道。

# 刻意**没有**的字段

* **没有 ``account_id``。** 回执是**发现期/母池期**的东西，而 ``AccountRecord`` 的
  ``cohort`` 是闭集、契约把 ``cohort`` 列进了 ``required``（见
  ``marketops/domain/candidate.py`` 的类文档）。一个恒为 ``None`` 的 ``account_id``
  槽位会读成「这一行的账号 id 还没填」，而事实是「这一层不产生账号记录」。
  从行程到账号那一步是一个**受控的映射**，由 ``AccountRecord`` 自己的 validator 重新校验，
  不在这一层悄悄发生。
* **没有 ``qualified`` / ``counts`` 之类的落盘计数。** 计数是**派生**的
  （:meth:`SourceReceipt.counts`）：同一个数存两遍，漂移的那一次没有人能看出哪个是错的。
* **没有 snippet。** 与 ``AccountCandidate`` 逐字同一条理由：让摘要文本进来，
  等于允许一个绕过检索工具的邮箱从旁路进入发现层，而 ``pipeline.py::_scan_snippet``
  的计数器会朝「一切正常」说谎。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from enum import Enum

from pydantic import Field, ValidationInfo, field_validator, model_validator

from .base import StrictModel as _Strict
from .enums import DomainVerdict
from .sources import resolve_source_method
from .util import (
    FILENAME_ID_MAX_LENGTH,
    SEP,
    check_canonical_id,
    check_slug_safe,
    is_plausible_email,
    sha256_text,
)

#: 回执的**形状**版本。它不是规则版本（那个由处置摘要承担，见模块 docstring）：
#: 它给读者的是「这份 JSON 用哪一套字段理解」。回执是**落盘后会被后来者读回**的物件，
#: 而形状变化（加字段、改语义）不会让任一个现有文件失效 —— 除非有一栏说得出来。
RECEIPT_VERSION = "v1"

#: ``source_identity`` 里用来分隔「命名空间」与「取值」的字符。
#:
#: **两个，不是刻意含糊**：上游真的两种都在用 —— 按对象类型编号的那些写
#: ``<type>/<id>``，而各类登记册导出常写 ``<namespace>:<key>``。本层不裁决哪一种，
#: 它只需要回答一个问题（见 :func:`identity_tail`）：这个名字是不是**就是**那个 id。
#:
#: 这两个字符在这里是**允许**的，而 ``row_id`` 里不允许（``check_canonical_id``
#: 把 ``:`` 与 ``/`` 都当不安全字符）—— 这不是不一致，是本模块的要点：
#: ``source_identity`` 是上游的字符串，**永远不变成**本仓的文件名；只有 ``row_id`` 会。
SOURCE_IDENTITY_SEPARATORS = (":", "/")


def identity_tail(source_identity: str) -> str:
    """来源标识里**取值**的那一段（最后一个分隔符之后）。

    它只服务一处判据：「名字那一格写的是不是就是这个 id」。上游的导出里，
    以标识回填名称**是常态**（那一格不能空着，而手快的人填的就是手边那个值），
    于是 ``node/12345`` 那一行的名称栏里写着 ``12345``。这样一个名字在报告里
    读起来像一个指称对象 —— 而它不是，它是**同一个标识换了个写法**。

    没有分隔符时原样返回：``12345`` 的取值段就是 ``12345``。
    """
    tail = source_identity.strip()
    for separator in SOURCE_IDENTITY_SEPARATORS:
        _head, found, rest = tail.rpartition(separator)
        if found:
            tail = rest
    return tail


class Disposition(str, Enum):
    """一行原始数据的**去向**。闭集，五个成员，没有第六个。

    ``resolved``
        变成了一个**可用于研究**的主体：这一行现在有一个人类可读的指称对象。
        只有它会产出一个 subject，因而只有它会让一次检索发生。
    ``merged``
        它就是另一行的同一个东西（同一个来源标识，或同一个名字 + 同一个地点）。
        它**没有消失** —— ``merged_into`` 指着那个赢家，于是「300 行进来的、
        270 行出去」这件事在报告上答得出「少了的那 30 行去哪了」。
    ``excluded``
        按 ``profiles/*/exclusions`` 里**已声明**的某一个排除项被排除。
        ``exclusion_ids`` 必填，而且由装载期校验它们确实是这个 profile 声明过的 id ——
        引擎自己一条排除规则都不认识（``tests/test_no_hardcoded_market_knowledge.py``
        对 ``marketops/`` 整包强制执行），所以「排除」这件事只能由来源行说、
        由 profile 声明来核对。
    ``pending``
        **还处置不了**，而且理由说得出来：最常见的正是 Member-C 说的那种行 ——
        只有来源标识、名字那一格是空的或就是把标识又写了一遍。它留在回执里、
        带着理由，但**不产生 subject**：身份补全还没发生，所以一次检索都不该发。
    ``failed``
        读不出来（字段缺失、编码坏了、行本身畸形）。它与 ``pending`` 分得开：
        ``pending`` 是「等一次补全」，``failed`` 是「这一行需要人看」。
        两者在报告上都不算成功，也都**不静默消失**。

    **没有 ``dropped`` / ``skipped`` 之类的成员，这是刻意的。** 那种成员是
    「我把它丢了、而且我不打算说为什么」的形状；``reason`` 在这五个成员上都是
    必填的（``resolved`` 除外），所以「为什么」无处可逃。
    """

    RESOLVED = "resolved"
    MERGED = "merged"
    EXCLUDED = "excluded"
    PENDING = "pending"
    FAILED = "failed"


class RawCandidateRow(_Strict):
    """母池里的一行原始数据 —— **读进来的那一行**，不是判定过的候选。

    这一层是「接收并保存原始行」（§4.3 处理次序第一步）的形状。它刻意很薄：
    它记的是**上游说**了什么，不是本仓据此**认为**了什么。判定走
    :class:`RowDisposition`，而两者由 ``row_id`` 一一对上。

    字段名与 ``AccountCandidate`` 有意**不重名**（``venue_name`` → ``name``，
    ``official_domain`` → 无对应）：原始行里根本不一定有「官方域名」这一栏，
    而给它一个同名槽位会让人以为这个值已经过 ``canonical_host`` 那一层。
    唯一一件两处同名的事是「来源方法」—— 因为它确实是同一件事，
    而它也确实是逐字抄过来的。
    """

    #: 本仓**这一行**的身份。规范形式（``check_canonical_id``）：它会变成证据文件的
    #: 名前缀，而一个需要被规范化的 id 会与另一个 id 落到同一个文件上。
    #: 由 :func:`row_id_for` 派生，不要手写 —— 手写的那一天，重放就不再是幂等的。
    row_id: str
    #: 这一行在**本次工件里的位置**（从 0 开始）。它是回执与原始工件之间**唯一**的
    #: 对照键：位置是工件的属性，名字和标识都不是（两者都可能重复）。
    ordinal: int = Field(ge=0)
    #: 上游**它自己**的标识，形状 ``<namespace>:<value>``（如 ``node/12345`` 配命名空间），
    #: 取值对本层不透明。**允许为空**：很多母池根本没有稳定主键（一份手工 CSV 就是）。
    #: **不是账号身份，也不得被当成检索身份** —— 见模块 docstring。
    source_identity: str = ""
    #: 这一行声称的**名称**。``""`` 是「上游没给」，不是「上游说它没有名字」——
    #: 前者要靠身份补全，后者是一条**结论**，而母池一行里表达不出结论。
    name: str = ""
    #: 这一行声称的地点/区域。同上，``""`` 是缺席。
    location: str = ""
    #: 其余列的**逐字**留存（键 = 上游的表头，值 = 上游的原文）。
    #:
    #: 为什么留：§4.3 的次序是「接收并保存原始行 → 规范化来源键 → …」，
    #: 而规范化与补全都要**回过头**看原始列（体育场表面、组织形态、场地类别
    #: 都可能藏在某个上游列里）。第一遍就丢掉它们，等于把「补齐」这件事
    #: 变成一次重新导出 —— 而上游的导出往往不可复现。
    #:
    #: 它**不是**一个能被判定的地方：这里面没有任何一格参与 `resolved` 的判定，
    #: 也没有任何一格会进 :attr:`RowDisposition.reason` 之外的任何下游物件。
    attributes: dict[str, str] = Field(default_factory=dict)

    @field_validator("row_id")
    @classmethod
    def _row_id_is_canonical(cls, value: str) -> str:
        """与 ``AccountCandidate.candidate_id`` / ``Evidence.account_id`` 同一条判据。

        它会被当作文件名片段用，所以必须与它落盘用的名字一一对应。
        """
        return check_canonical_id(value, "row_id", max_length=FILENAME_ID_MAX_LENGTH)

    @field_validator("source_identity")
    @classmethod
    def _source_identity_is_not_an_identity_it_is_not(cls, value: str) -> str:
        """来源标识不得是一个像邮箱的串，也不得含空白。

        这条判据挡的是一次**具体的**混淆：把上游某一列（常常是 contact 列）当成
        「来源标识」灌进来，然后下游拿它当联系方式的依据。来源标识回答的是
        「这一行来自上游的哪个对象」，它**不是**任何人的联系方式 ——
        而一个像邮箱的来源标识在报告里读起来正是那个东西。

        空白则是另一件事：带空白的标识不会被 ``slugify`` 当成两个词，而会被
        ``check_canonical_id`` 拒掉 —— 但那是 ``row_id`` 那条路，
        而 ``source_identity`` 不落盘。所以这条判据必须在这里再写一次。
        """
        if not value:
            return ""
        if value != value.strip() or any(ch.isspace() for ch in value):
            raise ValueError(
                "source_identity 首尾或中间含空白。\n"
                "来源标识是上游对象的句柄，不是一段文本 —— 带空白的标识在报告里"
                "与「两个不同的标识被写成一行」分不开。"
            )
        if is_plausible_email(value):
            raise ValueError(
                f"source_identity 不能像一个邮箱：{value!r}。\n"
                "来源标识回答的是「这一行来自上游的哪个对象」，**不是**任何人的联系"
                "方式。一个像邮箱的来源标识会被下游读成联系依据 —— 而它没有出处、"
                "没有抓取、没有绑定，是这一层最贵的一类静默错误。"
            )
        return value


class RowDisposition(_Strict):
    """一行原始数据的处置 —— **每一行都要有一个**，没有例外。

    五组字段按「这个处置需要什么才说得通」分：

    * 每个处置都要 :attr:`row_id` / :attr:`ordinal` / :attr:`source_identity`：
      前两个把处置对回工件，第三个让「同一个上游对象出现了两次」看得见。
    * 非 ``resolved`` 的都要 :attr:`reason`：一条说不出理由的处置，与
      「这一行被静默丢掉了」在报告上长得一样。
    * ``merged`` 要 :attr:`merged_into`，``excluded`` 要 :attr:`exclusion_ids`。
    * ``resolved`` 要 :attr:`resolved_name`，并且**只有它能带**那三个主体字段。

    那最后一条是本类存在的第二个理由（第一个是 ``reason``）：它让
    「处置是 pending、但名字那一格填着内容」**在构造期就写不出来**。
    一个这样的行会被下游读成「这是可以开工的主体」，而它的理由是「还缺身份」——
    两句话同时为真，读的人只会相信对自己方便的那一句。
    """

    row_id: str
    ordinal: int = Field(ge=0)
    disposition: Disposition
    #: 上游对象的身份，从原始行原样带过来。**允许重复**：同一个上游对象在一批里
    #: 出现两次是两行，第二行处置成 ``merged``。它不参与任何唯一性判据 ——
    #: 唯一的是 :attr:`row_id`（本仓这一行的身份）。
    source_identity: str = ""
    #: 为什么是这个处置。除 ``resolved`` 外**必填**。
    reason: str = ""
    #: **只有** ``merged`` 有：它归并到了哪一行（对方的 :attr:`row_id`）。
    merged_into: str = ""
    #: **只有** ``excluded`` 有：命中的是 ``profiles/*/exclusions`` 里**哪几个已声明**的
    #: 排除项 id。声明是否真的存在由装载期核对（引擎自己一条排除规则都不认识）。
    #:
    #: 是**一个元组**而不是一个字符串：一行可以同时命中两条（既是人工草皮又是
    #: 某项活动的场地），而「取第一条」会让报告上剩下那一条消失 —— 这一层的
    #: 全部工作就是让每一个去向都看得见，所以这里不做那种取舍。
    exclusion_ids: tuple[str, ...] = ()
    #: **只有** ``resolved`` 有：可用于研究的指称对象。这是「身份补全」这一步的**产出**，
    #: 因此它与 :attr:`RawCandidateRow.name` 允许不同 —— 补齐正是要让它们不同。
    resolved_name: str = ""
    #: 同上，地点/区域。可为空：有些场地名本身就够唯一了。
    resolved_location: str = ""
    #: 同上，官方域名。可为空，而「空」在这里是**缺席**：没有域名的身份发现照样能做
    #: （先搜名字、再确认官网）。它的形状由装载期过 ``canonical_host``；
    #: 本层只要求它不是一段 URL 文本（带 scheme 的串在这里就报错，
    #: 因为下游拿它当主机比较，而带 scheme 的值永远不相等 —— 差别看不出来）。
    #:
    #: **这一格是「别人说的」，不是「我们看到的」，也不是「我们证得的」。**
    #: 它来自上游导出/登记册的那一列。「我们在哪儿读到的这一行」是
    #: :attr:`discovered_host`，「我们证得的是哪几个」是
    #: :attr:`verified_official_domains` —— 三格分开，是因为把它们缝在一起，
    #: 正是审计里那条「SERP 抓回来的主机被读成『这是它的官网』」。
    #:
    #: 名字里带 ``claimed`` 不是啰嗦：这个字段此前叫 ``official_domain``，而那个
    #: 名字在三个调用点上被读成过一句**断言**（「这就是它的官网」），实际上它只是
    #: 一句话的**转述**。转述可以错，而断言错了没人会去查。
    claimed_official_domain: str = ""
    #: **我们在哪台主机上读到了这一行。** 一次观察，不主张归属。
    #:
    #: 它**不是**绑定那一层的账号域名权威（那是
    #: :attr:`verified_official_domain`）：一份目录页、一份社媒页都会给出一个主机，
    #: 而它们不是这个组织的官网。把它当权威用，两个方向都会错 ——
    #: 社媒页上那个真实域名会被判成「外来域」，而目录站的主机会被当成它自己的域名。
    discovered_host: str = ""
    #: 我们在**哪一页**读到的这一行（整条 URL）。与 :attr:`discovered_host` 一起构成
    #: 一条能重放的观察：只有一个主机名而没有 URL 的行，事后答不出「你在哪儿看到的」。
    discovered_url: str = ""
    #: **拿到证据之后**才填的那一格：这个主体的官网，**恰好一个**的时候。
    #: 空 = 没有证得（不是「没有官网」），或者证得的**不止一个**（见下一格）。
    #: 只有 ``resolved`` 的行能带它 —— 它是关于**主体**的结论，而其余四种处置说的
    #: 是「这一行去哪了」。
    verified_official_domain: str = ""
    #: **证得的全部官网。** 这一格才是判定的**本体**，单数那一格是它的读法。
    #:
    #: 为什么是两格而不是一格：一个组织有两个官网（主站 + 分区/子品牌站）是常见的，
    #: 而绑定那一层问的是「这个邮箱的域是不是**它的**域之一」—— 那是一个集合问题。
    #: 只留一个单数格，第二个官网就会在链路上消失，症状是**真实的企业邮箱被拒**
    #: （「邮箱域与账号域名不符」），而理由栏里写着一条听起来完全正当的话。
    #:
    #: 反过来，让单数格在两个官网时随便挑一个，会让「这个组织的官网」在两次运行里
    #: 给出两个不同的答案，而两次在报告上都看起来正常。所以：集合是本体，
    #: 单数格只在**恰好一个**时给值，由 :meth:`_the_domain_verdict_agrees_with_the_fields`
    #: 在构造期绑死。
    verified_official_domains: list[str] = Field(default_factory=list)
    #: 上面那一格的判定状态。三值与 ``VenueAsset`` 上同名那一格逐字相同：
    #: ``unknown`` 是「没判定」（**不是**「判定为假」），``confirmed`` 要域名与证据
    #: 都在，``absent`` 要「查过、没有」的证据在而域名为空。
    domain_verdict: DomainVerdict = DomainVerdict.UNKNOWN
    #: 支撑 :attr:`domain_verdict` 的那几行证据。``confirmed`` / ``absent`` 时非空。
    domain_evidence_ids: list[str] = Field(default_factory=list)
    #: 为什么是这个判定。**每一行都写**（含 ``unknown``），理由与四格分类读数同一条：
    #: 「我们看过哪几台主机、各自为什么不算」是审计那一步唯一读得到的东西 ——
    #: 只报一个 ``unknown``，读的人分不清「没看」「看了不像」和「看了、是目录站」。
    domain_reason: str = ""
    #: **这一行的资产事实**（§2 的「candidate URL → 抓取 → 抽取资产事实 → 场地资产」）。
    #:
    #: 四格与 ``AccountCandidate`` 上的同名四格是同一个东西（同一次分类的产出），
    #: 而它们必须**过得了回执这一关**才能到场地资产那一行 —— 上游到资产之间今天
    #: 只有这一条路（``application/pool.py::assets_from_receipt`` 只读回执，
    #: 不回头读工件）。所以这四格不是「顺便带上」：不带上，资产那一层就永远只有
    #: 名字与地点，而 §2 要的「国家/地区、场馆类型、环境、表面类别」一格都没有。
    #:
    #: 空串 = **这一行没有分类**（回执不是从发现期来的：母池那种形状的导出没有
    #: 这一栏）。它与 ``"unknown"`` 不是一件事，与 ``AccountCandidate`` 那条
    #: 纪律一致：那一层拒绝空串，是因为空串会被喂进规则引擎、在那里读作
    #: 「记了，是空」；而这一层不喂规则引擎，它的读者是资产池与档案投影。
    organisation_kind: str = ""
    venue_environment: str = ""
    surface_class: str = ""
    country: str = ""

    @field_validator("row_id")
    @classmethod
    def _row_id_is_canonical(cls, value: str) -> str:
        return check_canonical_id(value, "row_id", max_length=FILENAME_ID_MAX_LENGTH)

    @field_validator("merged_into")
    @classmethod
    def _merged_into_is_a_row_id(cls, value: str) -> str:
        if not value:
            return ""
        return check_canonical_id(value, "merged_into", max_length=FILENAME_ID_MAX_LENGTH)

    @field_validator("claimed_official_domain", "discovered_host", "verified_official_domain")
    @classmethod
    def _a_bare_host(cls, value: str, info: ValidationInfo) -> str:
        """空放行；非空就必须是一个**裸主机名**。

        ``check_slug_safe`` 恰好是这里需要的判据（与 ``AccountCandidate.official_domain``
        同一条）：它挡掉 ``:`` 与 ``/``，于是 ``https://host/path`` 存不进来 ——
        那个值下游会被当作 host 去比较，而它永远不相等，差别看不出来。

        三格共用一条判据是刻意的：'' 读过的人会把 ``discovered_host`` 写成一整条 URL
        （它旁边就有 :attr:`discovered_url`），而带 scheme 的主机在下面无论拿去
        比较还是拿去判据都**永远不相等** —— 症状是「这一行没有主机」，不是一条报错。
        """
        if not value:
            return ""
        return check_slug_safe(value, info.field_name)

    @field_validator("verified_official_domains")
    @classmethod
    def _every_verified_domain_is_a_bare_host(cls, value: list[str]) -> list[str]:
        """``verified_official_domains`` **逐项**过同一条判据。

        ``field_validator`` 默认拿到的是一整个列表（``mode="before"`` 之外），所以
        单独写一条而不是把这一格塞进上面那个装饰器 —— 把一整份 list 交给
        ``check_slug_safe`` 只会得到一句「它不是字符串」，把**哪一项**坏了吞掉。

        为什么不与 ``claimed_official_domain`` 那样宽容：集合就是绑定那一层判
        「这个邮箱域算不算外来」的依据（SOP L121）。一项读不出来而无人在意，
        集合就少一个元素，于是本账号自己官网上登着的邮箱会被拒成外来域 ——
        报告上那一行看起来只是「域名少了一个」。``violation`` 那一半也要挡：
        同一个 host 写两遍会让 ``len(...) == 1`` 那条单数格判据在一个**两元素**的
        集合上成立，而它是一句重复，不是两份证据。
        """
        seen: list[str] = []
        for item in value:
            if not item:
                raise ValueError(
                    "verified_official_domains 里有一项是空的。\n"
                    "写一个空项的人想说的多半是「有一项没填出来」—— 而空串在这一格"
                    "会被读成「没有这个域」，与「没查出来」不同。去掉它，或者补上主机名。"
                )
            if item != item.strip() or item != item.lower():
                raise ValueError(
                    f"verified_official_domains 里的 {item!r} 不是规范形式"
                    "（去空白、全小写）。\n"
                    "这一格会被逐字拿去与邮箱域比较，而比较是**精确**的 —— "
                    "'Example.COM' 与 'example.com' 比不相等，症状是「这个域不算官方」."
                )
            check_slug_safe(item, "verified_official_domains")
            seen.append(item)
        if len(set(seen)) != len(seen):
            raise ValueError(
                f"verified_official_domains 里有重复项：{seen!r}。\n"
                "重复在这一格不是「两份证据」。单数格 verified_official_domain 只在"
                "**恰好一个**官方域时给值，而一个两元素的重复集合会让那条判据成立 ——"
                "于是「唯一官方域」这句话由一句重复造出来。去重之后再读。"
            )
        return value

    @field_validator("domain_evidence_ids")
    @classmethod
    def _every_domain_evidence_id_is_canonical(cls, value: list[str]) -> list[str]:
        """证据 id 逐项过 ``check_canonical_id``。

        与 ``merged_into`` 同一条判据、同一个理由：这一格下游会被拿去找证据行
        （晋级时还要逐条重映射，见 ``promotion._remap_domain_evidence``）。一个
        不合规的 id 找不到任何一行，而 ``domain_verdict=confirmed`` 要求它非空 ——
        于是「证得了官网」这句话底下可以一条证据都没有。
        """
        for item in value:
            check_canonical_id(item, "domain_evidence_ids", max_length=FILENAME_ID_MAX_LENGTH)
        return value

    @field_validator("discovered_url")
    @classmethod
    def _discovered_url_carries_a_scheme(cls, value: str) -> str:
        """空放行；非空就必须是一条带 scheme 的地址。

        拦的是「把裸主机写进 URL 那一格」：那一格下游会被当成一条能重放的观察
        （「你在哪儿看到的」），而一个裸主机答不了这个问题 —— 它答的是
        :attr:`discovered_host` 那一格的问题。
        """
        if not value:
            return ""
        if "://" not in value:
            raise ValueError(
                f"discovered_url 必须是一条带 scheme 的地址，实际是 {value!r}。\n"
                "这一格回答的是「我们在哪一页读到的它」，而一个裸主机回答不了 ——"
                "那是 discovered_host 那一格的问题。"
            )
        return value

    @field_validator("source_identity", "reason", "resolved_name", "resolved_location")
    @classmethod
    def _text_is_stripped(cls, value: str, info: ValidationInfo) -> str:
        """这些格要么空、要么是一个已经去掉首尾空白的值。

        它们会被逐字写进报告、也会被逐字拿去比较（``resolved_name`` 的合并就是
        按它比的），而 ``" x"`` 与 ``"x"`` 差一个空格 —— 前者会配不上、
        报告上却只显示「没有归并」。
        """
        if value != value.strip():
            raise ValueError(f"{info.field_name} 首尾不得有空白：{value!r}")
        return value

    @model_validator(mode="after")
    def _the_disposition_and_its_fields_agree(self) -> RowDisposition:
        """处置与它带的字段必须自洽。下面七条，每一条都挡着一个具体的失败形态。

        它们写成**一个**校验器而不是七个 ``field_validator``，是因为判据是
        「处置是什么」与「哪些格被填了」之间的**关系** —— 逐字段的校验器拿不到
        另一边的值。

        理由逐条：

        1. ``resolved`` 必须有 ``resolved_name``。**只有标识的行不是一个主体。**
           这正是 Member-C 说的那类行（pos30），也正是那次 ``kent-003`` 事故的形状：
           一个拿不出指称对象的行被当成可以开工的主体发出去，worker 只能拿标识去检索。
        2. 非 ``resolved`` 必须**没有**那三个主体字段。见类文档：让「处置是 pending、
           名字那格却有内容」构造不出来。
        3. 非 ``resolved`` 必须有 ``reason``。说不出理由的处置与静默丢弃同形。
        4. ``merged`` 必须有 ``merged_into``，且不是自己。
        5. 非 ``merged`` 必须没有 ``merged_into``。
        6. ``excluded`` 必须有 ``exclusion_ids``。
        7. 非 ``excluded`` 必须没有 ``exclusion_ids``。
        """
        resolved = self.disposition is Disposition.RESOLVED
        merged = self.disposition is Disposition.MERGED
        excluded = self.disposition is Disposition.EXCLUDED

        if resolved and not self.resolved_name:
            raise ValueError(
                f"处置为 resolved 的第 {self.ordinal} 行没有 resolved_name。\n"
                "只有来源标识的行**不是**一个主体：拿它去检索的后果已经被量过一次 ——"
                "9 次查询落在无关的同名物上，而报告上看起来只是一次正常的、"
                "什么都没找到的检索。这一行的处置应该是 pending，"
                "并把理由写清楚（缺什么才能补出来）。"
            )
        if not resolved and (
            self.resolved_name or self.resolved_location or self.claimed_official_domain
        ):
            raise ValueError(
                f"处置为 {self.disposition.value} 的第 {self.ordinal} 行带上了主体字段"
                f"（resolved_name={self.resolved_name!r}、"
                f"resolved_location={self.resolved_location!r}、"
                f"claimed_official_domain={self.claimed_official_domain!r}）。\n"
                "这三个字段的含义是「**这个主体已经可以用来做身份发现了**」。"
                "把它们挂在其它处置上，下游会读成一个可以开工的主体 ——"
                "而这一行恰恰是**不能**开工的那一种。"
            )
        if not resolved and (
            self.verified_official_domain or self.verified_official_domains or self.domain_evidence_ids
        ):
            raise ValueError(
                f"处置为 {self.disposition.value} 的第 {self.ordinal} 行带上了官网判定"
                f"（verified_official_domain={self.verified_official_domain!r}、"
                f"verified_official_domains={self.verified_official_domains!r}、"
                f"domain_evidence_ids={self.domain_evidence_ids!r}）。\n"
                "「这个主体的官网是哪一个」是关于**主体**的结论，与上面三个主体字段"
                "同一类：挂在 merged 行上，它会与赢家那一行给出两个都叫「官网」的答案；"
                "挂在 excluded 行上，它是在为一个不进池子的东西下结论。\n"
                "观察那几格（discovered_host / discovered_url / domain_verdict / "
                "domain_reason）不受这条限制 —— 它们说的是「我们在哪一页读到了什么」，"
                "而每一种处置都有一页。"
            )
        if not resolved and not self.reason:
            raise ValueError(
                f"处置为 {self.disposition.value} 的第 {self.ordinal} 行没有理由。\n"
                "一条说不出理由的处置，与「这一行被静默丢掉了」在报告里长得一样；"
                "而母池的失败形态正是「少了的那几行没人说得清去哪了」。"
            )
        if merged and not self.merged_into:
            raise ValueError(
                f"处置为 merged 的第 {self.ordinal} 行没有 merged_into。\n"
                "归并表示「它就是另一行的同一个东西」，而另一行是谁必须说得出来 ——"
                "否则这一行在报告里就是一次无理由的消失。"
            )
        if merged and self.merged_into == self.row_id:
            raise ValueError(
                f"第 {self.ordinal} 行的 merged_into 指向它自己（{self.row_id}）。\n"
                "自指不是归并，是一条循环 —— 顺着它读下去会回到原处，"
                "而报告上它看起来像一次正常的合并。"
            )
        if not merged and self.merged_into:
            raise ValueError(
                f"处置为 {self.disposition.value} 的第 {self.ordinal} 行带上了 "
                f"merged_into={self.merged_into!r} —— 只有 merged 有这一格。"
            )
        if excluded and not self.exclusion_ids:
            raise ValueError(
                f"处置为 excluded 的第 {self.ordinal} 行没有 exclusion_ids。\n"
                "排除必须命中 ``profiles/*/exclusions`` 里**已声明**的那一项 ——"
                "一个没有名字的排除，事后无法回答「按什么排除的」，"
                "也无法回答「改了排除规则之后要重跑哪些」。"
            )
        if not excluded and self.exclusion_ids:
            raise ValueError(
                f"处置为 {self.disposition.value} 的第 {self.ordinal} 行带上了 "
                f"exclusion_ids={self.exclusion_ids!r} —— 只有 excluded 有这一格。"
            )
        return self

    @model_validator(mode="after")
    def _the_domain_verdict_agrees_with_the_fields(self) -> RowDisposition:
        """官网判定的三格必须自洽。**与 ``VenueAsset`` 上那条逐字同一条规则。**

        同一个判据写两遍是刻意的：回执是这一层唯一的入口，而**过了这一关不等于
        过了资产那一关** —— 中间还有 ``assets_from_receipt`` 与 ``merge_assets``
        两次搬运。两处各判一次，才有资格说「盘上写不出自相矛盾的那一行」。

        三值各自的含义见 :attr:`domain_verdict`。``unknown`` 一格都不许有：
        它说的是「没判定」，而一个「没判定」却带着域名或证据的行，读起来像
        「判定过、只是状态没更新」—— 两句话的处置完全相反（前者可以再查一次，
        后者是一次已经做过的结论）。

        另有一条**读法**的判据：单数那一格必须等于集合在**恰好一个**元素时的
        那个元素，其余情况为空。它是两个格子之间的换算，而不是第二种判据 ——
        少了它，一行可以在集合里写两个官网、同时把单数那格填成第三个。
        """
        expected_singular = (
            self.verified_official_domains[0] if len(self.verified_official_domains) == 1 else ""
        )
        if self.verified_official_domain != expected_singular:
            raise ValueError(
                f"verified_official_domain={self.verified_official_domain!r} 与 "
                f"verified_official_domains={self.verified_official_domains!r} 不一致："
                f"单数那一格是集合的**读法**，恰好一个时是它、其余时候为空，"
                f"应当是 {expected_singular!r}。\n"
                "两格各写各的，就是两个都叫「这个组织的官网」的答案并存 ——"
                "而绑定那一层按集合判、报告按单数格读。"
            )
        if self.domain_verdict is DomainVerdict.CONFIRMED:
            if not self.verified_official_domains or not self.domain_evidence_ids:
                raise ValueError(
                    "domain_verdict=confirmed 要求同时给出 verified_official_domains "
                    "与 domain_evidence_ids，实际是 "
                    f"域名={self.verified_official_domains!r}、"
                    f"证据={self.domain_evidence_ids!r}。\n"
                    "「确认官网」这一步的全部价值在于**证据**：一个没有出处的域名，"
                    "与一个随手写下的域名，在审计里不是同一件事。"
                )
        elif self.domain_verdict is DomainVerdict.ABSENT:
            if self.verified_official_domains or not self.domain_evidence_ids:
                raise ValueError(
                    "domain_verdict=absent 要求 verified_official_domains 为空**且** "
                    "domain_evidence_ids 非空，实际是 "
                    f"域名={self.verified_official_domains!r}、"
                    f"证据={self.domain_evidence_ids!r}。\n"
                    "absent 说的是「查过，确实没有」—— 它要证据，而它不许同时"
                    "给出一个域名（那就是 confirmed 了）。"
                )
        elif self.verified_official_domains or self.domain_evidence_ids:
            raise ValueError(
                "domain_verdict=unknown 时 verified_official_domains 与 "
                f"domain_evidence_ids 都必须为空，实际是 "
                f"域名={self.verified_official_domains!r}、"
                f"证据={self.domain_evidence_ids!r}。\n"
                "unknown 说的是「没判定」，而它不决定任何子句；一个带着域名的 "
                "unknown 行会让下游把「没查」读成「查过、结论待定」。"
            )
        return self


def row_id_for(*, ordinal: int, source_identity: str, payload: str) -> str:
    """这一行的身份（**本仓这一行的**，不是上游对象的）。

    ## 为什么有序号在里面

    序号让「同一批里的两次出现」成为两行。这不是形式主义：重复行是母池的常态
    （同一块场地在两个上游导出里各出现一次），而它们必须能被**分别**处置
    （一行 ``resolved``、一行 ``merged``）。如果 ``row_id`` 只由来源标识派生，
    这两行会拿到同一个 id —— 于是 ``merged_into`` 只能指向自己，
    而 :class:`RowDisposition` 明确拒绝自指。

    ## 为什么文本摘要也在里面

    没有来源标识的行是常态（一份手工 CSV 没有主键）。那时身份只能来自**内容**：
    两行逐字相同的内容就是同一个东西（``payload`` 相同 ⇒ 同一个 ``row_id``），
    内容不同就是两行。``payload`` 应该包含这一行的**全部**读到的字段，
    而不只是名字 —— 名字常常是空的，而那时「同名」什么也证明不了。

    ## 为什么它必须是纯函数、且只吃这三样

    「重放幂等」这条要求的全部内容就是：同样的输入给出同样的 ``row_id``。
    任何一点外界输入（时钟、运行 id、集合迭代顺序）都会让它失效，
    而失效的表现是**重放时多出一批行** —— 报告上看起来只是「这一批更大」。

    Args:
        ordinal: 这一行在工件里的位置。
        source_identity: 上游对象标识；为空时改用 ``payload``。
        payload: 这一行全部读到字段的规范化文本。

    Returns:
        规范形式的行 id（``check_canonical_id`` 的不动点），长度固定。
    """
    basis = source_identity if source_identity else payload
    return f"row-{sha256_text(f'{ordinal}{SEP}{basis}')[:16]}"


def resolved_dedupe_key(row: RowDisposition) -> tuple[str, str]:
    """两行 ``resolved`` 说的是**不是同一个东西**，靠 (名字, 地点) 判断。

    这个键有**两个**消费者，所以它必须只有一处定义：

    * :attr:`SourceReceipt.distinct_resolved_count` —— 「这份导出里有几个东西」；
    * ``domain/entities.py::venue_asset_id_for`` —— 没有来源编号的行，句柄按它算。

    两处各写一遍的话，漂移的表现是「回执说这批有 25 个东西」与「池子里落进 30 块
    场地」同时为真，而两个数字都看起来很正常。

    取 ``(名字, 地点)`` 而不是名字的理由写在 ``distinct_resolved_count`` 上：英国一个
    村子里两个不同单位同名是常见的，拿名字当键会把它们算成一个 —— 那是低估，
    而低估比高估更难被发现。**按原样比，不做大小写或空白折叠**：折叠是另一条判断
    （「``Main Pitch`` 与 ``main pitch`` 是同一块场地吗」），本层没有做它，
    也不该在一个去重键里偷偷做掉。
    """
    return (row.resolved_name, row.resolved_location)


def dispositions_digest(dispositions: Iterable[RowDisposition]) -> str:
    """处置序列的摘要 —— :func:`receipt_id_for` 的第四个输入。

    **位置进散列，但走的是 ``ordinal`` 那一格，不是元组自身的顺序。** 所以先按
    ``ordinal`` 排一次：处置的身份是「第几行 + 那一行是什么」，而元组的迭代顺序
    不是值的一部分。一个从 ``set`` 或从另一次读取顺序不同的列表进来的调用，
    说的还是同一件事，不该算出另一个 id。

    要表达「这一批的位置变了」，唯一的办法是让 ``ordinal`` 变 —— 而那确实会改变
    摘要。这与 ``known_account_dossiers`` 对清单顺序的态度是同一条：**位置有意义**
    （它决定 ``work_item_id``），只是承载它的那一格在值里，不在容器的顺序里。

    序列化用 ``model_dump(mode="json")`` + ``sort_keys`` + 紧凑分隔符，理由与
    ``inputs.py::row_sha256`` 相同：默认输出带空格，而同一份值在不同 Python 版本上
    可能给出不同空白 —— 那时同一份回执会算出两个 id，一次重放被读成一批新行。
    """
    payload = [item.model_dump(mode="json") for item in sorted(dispositions, key=lambda d: d.ordinal)]
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def receipt_id_for(
    *,
    source_method_id: str,
    batch_id: str,
    artifact_sha256: str,
    dispositions_digest_value: str,
) -> str:
    """回执 id —— **内容寻址**，因此重放幂等、改过的规则另立一份。

    四个输入各回答一个问题：

    * ``source_method_id`` —— 这一批是怎么来的（闭集，见 ``domain/sources.py``）；
    * ``batch_id`` —— 上游把它叫哪一批（同一次导出重跑两次，是两批还是一批，
      由调用方在这里说清楚）；
    * ``artifact_sha256`` —— 读的是**哪一份字节**（文件同名而内容换过，是两份）；
    * ``dispositions_digest_value`` —— 读成了什么。

    最后一个不能省：少了它，「同一份工件、改过规则、再导入一次」会覆盖上一份回执，
    于是两次结论不同这件事在盘上不留痕迹。**它也让 :class:`SourceReceipt` 的
    ``receipt_id`` 不可能由它的字段反推** —— 这不是缺陷，是刻意的：
    id 说的是「这一份回执是哪一次判定的产物」，而「你手里这份是不是原封没动过」
    由 :func:`verify_receipt_id` 回答。

    Args:
        dispositions_digest_value: :func:`dispositions_digest` 的返回值 ——
            **传值不传序列**，因为 id 要在 :class:`SourceReceipt` 构造之前算出来
            （它自己就是构造的输入之一）。
    """
    return f"receipt-{sha256_text(SEP.join((source_method_id, batch_id, artifact_sha256, dispositions_digest_value)))[:16]}"


def verify_receipt_id(receipt: SourceReceipt) -> bool:
    """这份回执的 ``receipt_id`` 是不是它自己内容算出来的那一个。

    判据**只**覆盖 :func:`receipt_id_for` 的四个输入里可以就地重算的那三个
    （``artifact_sha256`` 与 ``dispositions_digest`` 都在回执里，``batch_id`` 与
    ``source_method_id`` 也是）。重算得到同一个值 ⇒ 字段与 id 自洽。
    它**不**证明原始工件还在、也没被改过 —— 那是 ``artifact_sha256`` 与盘上文件
    比对的事，需要磁盘，不属于本层。
    """
    return receipt.receipt_id == receipt_id_for(
        source_method_id=receipt.source_method_id,
        batch_id=receipt.batch_id,
        artifact_sha256=receipt.artifact_sha256,
        dispositions_digest_value=dispositions_digest(receipt.dispositions),
    )


class SourceReceipt(_Strict):
    """一批母池行的回执 —— 「这一批读成了什么」的**唯一**权威。

    它是一份**可再投喂**的物件：写进 ``inputs_dir/<receipt_id>.receipt.json``，
    之后由 ``submit_research(input_kind=source_receipt, input_ref=<receipt_id>)``
    原样读回。所以它必须**自足** —— 一个需要另一份文件才能理解的回执，
    在另一份文件被删掉的那天变成不可读，而那一天正是要回答
    「这一批到底读成了什么」的那一天。

    ``resolved`` 的行因此把补全出来的指称对象带在自己身上
    （:attr:`RowDisposition.resolved_name`），而不是只留一个指向别处的 id。

    ## 字段

    ``receipt_id``
        :func:`receipt_id_for` 的产物。规范形式 —— 它会变成文件名片段
        （``<receipt_id>.receipt.json``），而需要被规范化的 id 会与另一个 id
        落到同一个文件上：两份不同的回执静默合并，报告上只是「这批行更多了」。

    ``version``
        形状版本（:data:`RECEIPT_VERSION`）。回执是会被后来者读回的物件，
        形状变化必须有一栏说得出来，否则旧文件会以新形状被读，而差别看不出来。

    ``source_method_id``
        这一批怎么来的。**闭集校验**（``resolve_source_method``，失败关闭）：
        一个未注册的 id 与一个拼错的已注册 id 在报告里长得一样，而前者会让
        「来源族多样性」这项指标变成一句空话。SOP L82 逐字：「搜索结果、单页新闻或
        单一 contact 页只能作补充证据，**不能当全量母池**」—— 每一行都带它，
        这一批才不会被读成母池。

    ``batch_id``
        上游对这一批的称呼（一次导出、一次抓取运行的标签）。它**不**决定回执 id
        的唯一性（那是 ``receipt_id`` 的事），它决定的是「这两份回执说的是不是
        上游的同一次交付」——所以它只要求 slug-safe，不要求规范形式。

    ``artifact_ref``
        **原始工件的名字**，不是路径。与 ``input_ref`` 逐字同一条纪律：
        收一个路径等于把「读哪个文件」交给调用方 —— 一次跨机器部署下，
        那就是一条越权读盘的接口。路径由服务自己拼在 ``Settings.inputs_dir`` 下。
        名字会变成文件名，因此必须是规范形式。

    ``artifact_sha256``
        那一份工件的字节摘要。**它是「读的是哪些字节」这个问题的全部答案** ——
        文件同名而内容换过是两份工件，而差别（一个字符）在报告上看不出来。

    ``created_at``
        这一次判定的时刻。必须带时区：naive datetime 与全项目其余部分的约定不符，
        而它在这一层的作用正是与 ``cutoff`` 比较 —— 一个不知道自己在哪个时区的
        时刻在这种比较里没有意义。

    ``scope`` / ``cutoff`` / ``cursor``
        上游的范围、时点与续读游标。``""`` 是**缺席**，不是「空的范围」：
        一份手工导出的 CSV 没有查询范围可写，而写一个空串进去会让
        「没有范围」与「范围是空的」在报告里长得一样。

    ``profile_id``
        这一批被读的时候，**哪一份 profile 的排除声明在生效**。它不参与判定
        （引擎一条排除规则都不认识），它回答的是那个事后一定会被问的问题：
        「这份回执里的 ``excluded`` 是按哪一版声明下的？」—— 与 ``receipt_id``
        里的处置摘要合起来，「改了排除规则之后要重跑哪些」才有依据。

    ``raw_row_count``
        **上游交给了多少行。**它是这次读取的**分母**，也是那条不变量的另一半：
        处置的序号必须恰好是 ``0..raw_row_count-1`` 的一个排列。
        一个静默丢掉几行的实现，在这里是构造不出来回执的 —— 而不是
        「报告上少了几行」。

    ``dispositions``
        每一行的去向，**按 ordinal**。计数由 :meth:`counts` 派生，不落第二份。
    """

    receipt_id: str
    version: str = RECEIPT_VERSION
    source_method_id: str
    batch_id: str
    artifact_ref: str
    artifact_sha256: str
    created_at: datetime
    scope: str = ""
    cutoff: str = ""
    cursor: str = ""
    profile_id: str = ""
    raw_row_count: int = Field(ge=1)
    dispositions: tuple[RowDisposition, ...]

    @field_validator("receipt_id", "artifact_ref")
    @classmethod
    def _filename_ids_are_canonical(cls, value: str, info: ValidationInfo) -> str:
        """这两个都会变成文件名片段（``<receipt_id>.receipt.json``、
        ``<artifact_ref>.<后缀>``），所以走同一条判据。"""
        return check_canonical_id(value, info.field_name, max_length=FILENAME_ID_MAX_LENGTH)

    @field_validator("batch_id", "profile_id")
    @classmethod
    def _labels_are_slug_safe(cls, value: str, info: ValidationInfo) -> str:
        """这两个只当标签用（不进文件名、不参与判定），所以走松的那一条。

        松，但仍然拒绝路径分隔符与保留字符：一个含 ``/`` 的批次标签会被
        写进报告的表格里，而它在任何一次「按标签找东西」的操作中都不是一个名字。
        """
        if not value:
            return ""
        return check_slug_safe(value, info.field_name)

    @field_validator("source_method_id")
    @classmethod
    def _source_method_is_registered(cls, value: str) -> str:
        """**失败关闭**：未注册的来源 id 直接抛（``ConfigError``，与
        ``new_candidate`` 同一型）。

        它没有走 ``ValidationError`` 那条通道，因为这条规则的权威在
        ``domain/sources.py``，而那里用的是 ``ConfigError``；两处各写一遍
        会让「加一个来源要走哪里」这个问题有两个答案。
        """
        resolve_source_method(value)
        return value

    @field_validator("version")
    @classmethod
    def _version_is_one_we_know(cls, value: str) -> str:
        if value != RECEIPT_VERSION:
            raise ValueError(
                f"回执形状版本 {value!r} 不认识（本代码认得 {RECEIPT_VERSION!r}）。\n"
                "回执是落盘后被读回的物件：用一套不认识的字段去理解它，"
                "失败形态不是报错，是把空处读成「上游没给」。"
            )
        return value

    @field_validator("artifact_sha256")
    @classmethod
    def _artifact_sha256_is_a_digest(cls, value: str) -> str:
        digest = value.strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(
                f"artifact_sha256 不是 64 位十六进制摘要：{value!r}。\n"
                "它是「读的是哪些字节」这个问题的全部答案 —— 一个形状不对的值"
                "在那里等于没有答案，而在报告上它会看起来像有一个。"
            )
        return digest

    @field_validator("created_at")
    @classmethod
    def _created_at_is_timezone_aware(cls, value: datetime) -> datetime:
        """带时区。与 ``domain/temporal.py`` 全项目同一条约定。

        这一格的作用正是与 ``cutoff`` 比较 —— 一个不知道自己在哪个时区的时刻
        在这种比较里没有意义，而它**不会报错**，它只会偶尔比较错。
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"created_at 必须带时区：{value!r}。\n"
                "它要与 cutoff 比先后，而 naive datetime 的比较是「按墙上时间猜」——"
                "猜错的那一次不会报错，只会把一批行算到错误的时点上。"
            )
        return value

    @model_validator(mode="after")
    def _every_raw_row_has_exactly_one_disposition(self) -> SourceReceipt:
        """**本模块存在的理由**：每一原始行恰好一个处置，不多不少。

        三条判据，各挡一个具体的失败形态：

        1. ``len(dispositions) == raw_row_count`` —— 挡「少了几行」。丢行是最贵的
           那一类失败：剩下的行会被当成全池，于是「母池覆盖率」的分母从一开始就是错的，
           而报告上只显示「候选更少」。
        2. 序号恰好是 ``0..raw_row_count-1`` 的排列 —— 挡「多了一行」与「序号重复」。
           重复序号让「这一行对应工件里的哪一行」没有答案；而回执与工件之间
           **唯一**的对照键就是序号。
        3. ``row_id`` 不重复 —— 挡「两行拿到同一个身份」。``merged_into`` 是按
           ``row_id`` 指的，重复的 id 会让一次归并指向两行，而「归并到了哪一个」
           这个问题的答案取决于谁先读 —— 那是一次不可复现的报告。
        """
        if len(self.dispositions) != self.raw_row_count:
            raise ValueError(
                f"回执声明读了 {self.raw_row_count} 行，却给出 {len(self.dispositions)} 条处置。\n"
                "每一行都必须有一个说得出来的去向：丢掉的那些行在报告里与"
                "「本来就不在池子里」长得一样，而剩下的行会被读成**全池**。"
            )
        ordinals = sorted(item.ordinal for item in self.dispositions)
        if ordinals != list(range(self.raw_row_count)):
            missing = sorted(set(range(self.raw_row_count)) - set(ordinals))
            repeated = sorted({value for value in ordinals if ordinals.count(value) > 1})
            raise ValueError(
                "处置的序号不是 0..raw_row_count-1 的一个排列："
                f"缺 {missing}、重复 {repeated}。\n"
                "序号是回执与原始工件之间**唯一**的对照键（名字与标识都可能重复）——"
                "它的排列性就是「每一原始行都有处置」这句话本身。"
            )
        seen: set[str] = set()
        duplicates: list[str] = []
        for item in self.dispositions:
            if item.row_id in seen:
                duplicates.append(item.row_id)
            seen.add(item.row_id)
        if duplicates:
            raise ValueError(
                f"这些 row_id 出现了不止一次：{sorted(duplicates)}。\n"
                "``merged_into`` 按 row_id 指向另一行，重复的 id 会让一次归并"
                "同时指向两行 —— 「归并到了哪一个」于是取决于谁先读，"
                "而报告上它看起来像一次正常的合并。"
            )
        return self

    # --- 派生：一个事实只落一次 ---------------------------------------------

    @property
    def counts(self) -> dict[str, int]:
        """按处置分类的行数。**派生**，不落第二个字段。

        同一件事存两遍，漂移的那一次没有人能看出哪个是错的。这里多算一次
        没有成本：处置序列就在手上。
        """
        result = {member.value: 0 for member in Disposition}
        for item in self.dispositions:
            result[item.disposition.value] += 1
        return result

    @property
    def resolved_rows(self) -> tuple[RowDisposition, ...]:
        """可用于研究的那些行，**按 ordinal**。

        顺序有意义：它决定 ``ordinal``，因而决定 ``work_item_id``，所以这里
        从头到尾用元组而不是集合 —— 与 ``known_account_dossiers`` 逐字同一条理由。
        """
        return tuple(
            sorted(
                (item for item in self.dispositions if item.disposition is Disposition.RESOLVED),
                key=lambda item: item.ordinal,
            )
        )

    @property
    def resolved_row_count(self) -> int:
        """``resolved`` 的**行数**。见 :attr:`distinct_resolved_count` 的说明。"""
        return len(self.resolved_rows)

    @property
    def distinct_resolved_count(self) -> int:
        """``resolved`` 里**不同的实体**有几个 —— 与行数是两个数。

        两者必须分开报，因为它们回答的是两个不同的问题：

        * 行数回答「这份导出我们读进去了多少条」；
        * 这个数回答「里面有多少**个东西**」。

        合成一个「有效 25 条」，会让一次「25 行、其中 22 行是同一块场地的三种
        写法」读成「有 25 个客户」。本仓把「同名同地点是同一个东西」明确判为
        **一个判断**（见 ``marketops/application/motherpool.py``：只在来源标识上
        归并，不在名字上归并），所以这些行**不会**被合并 —— 行的身份不动，
        只是这里多回答一个问题。

        去重键取 ``(名字, 地点)`` 而不是名字：英国一个村子里两个不同单位同名是
        常见的，拿名字当键会把它们算成一个 —— 那是**低估**，而低估的后果是
        「这批池子看起来比实际小」，比高估更难被发现。

        这个数**不是** Account 数：Account 要有责任实体、有准入判定，
        那是后面几步的事。这里回答的只是这一份回执。

        键由 :func:`resolved_dedupe_key` 给出 —— **不是这里现写的一对字段名**。
        池子那一边（``venue_asset_id_for``）要用同一个键，两处各写一遍就会漂移。
        """
        return len({resolved_dedupe_key(row) for row in self.resolved_rows})

    @property
    def counts_line(self) -> str:
        """一句给报告用的摘要。**只报事实，不判成败**。

        「成功了 N 行」这种说法在这里是不诚实的：``pending`` 不是失败，
        但它也绝不是产出 —— 它是一次**还没发生**的补全。所以这句话逐项列出，
        让读的人自己算。

        末尾那两个数（``resolved_rows`` / ``distinct_entities``）**并列出现**，
        理由见 :attr:`distinct_resolved_count`：它们回答的是两个问题，
        而把它们合成一个数正是这一行最容易被读错的方式。
        """
        order = (
            Disposition.RESOLVED,
            Disposition.MERGED,
            Disposition.EXCLUDED,
            Disposition.PENDING,
            Disposition.FAILED,
        )
        counts = self.counts
        parts = [f"{member.value}={counts[member.value]}" for member in order]
        return (
            f"raw={self.raw_row_count} "
            + " ".join(parts)
            + f" resolved_rows={self.resolved_row_count}"
            + f" distinct_entities={self.distinct_resolved_count}"
        )

    @property
    def blocker(self) -> str:
        """这份回执能不能拿去受理研究。空串 = 能；否则一条说得出的理由。

        与 ``SubjectResolution`` 同一型：调用点把这条理由原样落成作业的
        ``BLOCKED`` 状态，而不是让「0 个可用主体」在报告里变成一次完成。

        **只判一件事**：有没有 ``resolved`` 的行。``failed`` / ``pending`` /
        ``excluded`` 的行**不是**阻塞 —— 它们是被记录下来的事实，
        而报告会把它们分开列出（:meth:`counts_line`）。把它们也算成阻塞，
        等于说「一份有 5 行坏数据的 300 行母池不能开工」——
        而正确的处置是开工，并把这 5 行留在报告上。
        """
        if self.resolved_rows:
            return ""
        return (
            f"回执 {self.receipt_id}（来源 {self.source_method_id}，批次 {self.batch_id}）里"
            f"没有一行处置为 resolved：{self.counts_line}。\n"
            "一份没有任何可用主体的母池**不是**「已经做完」—— 它什么都没跑。\n"
            "每一行都应该在报告里说得出去哪了（归并 / 排除 / 待补全 / 读失败）。"
            "如果 ``pending`` 占多数，缺的是**身份补全**那一步：母池里只有来源标识、"
            "没有名字的行必须先在母池一侧补齐，再进研究 —— 拿标识去检索会把预算"
            "花在同名物上，而交回的结算与「这个主体真的没有公开信息」不可区分。"
        )


__all__ = [
    "RECEIPT_VERSION",
    "SOURCE_IDENTITY_SEPARATORS",
    "Disposition",
    "RawCandidateRow",
    "RowDisposition",
    "SourceReceipt",
    "dispositions_digest",
    "identity_tail",
    "receipt_id_for",
    "row_id_for",
    "verify_receipt_id",
]

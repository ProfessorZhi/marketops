"""``submit_research`` 的 ``input_ref`` 怎么变成工作单元的 ``subject_ref``。

## 受理那一刻要冻住**身份**，不只是主键

2026-09-23 那次真实运行里，worker 拿到 ``kent-003``，花掉 9 次 Serper 查询去搜这个
内部 slug，十条命中全是无关的同名物。它拿不到 Example School One —— 而这个模块从前
正是把身份丢掉的那一处：``known_accounts_subjects`` 读出一整行，只留下
``account_id``，``organisation`` / ``domain`` / ``country`` 读出来即弃。

现在这里产出的是 :class:`~marketops.domain.subject.SubjectDossier`（一份**身份线索**），
并且在返回之前问一次 :func:`~marketops.domain.subject.identity_refusal`：拿不出
可用来做身份发现的依据的行，**整份拒绝**，一个 work item 都不建。这一步的位置很要紧 ——
到了 ``compile_work_items`` 就已经有工作单元了，而工作单元的失败形态是「worker 领走它、
然后花掉真金白银」。

## 为什么 ``input_ref`` 是一个**名字**，不是一个路径

控制端能提交一次研究，而它的 ``input_ref`` 决定了服务去读哪一份清单。收一个路径
等于把「读哪个文件」交给控制端 —— 一次跨机器部署下，那就是一条越权读盘的接口。
所以这里只接受 slug-safe 的**名字**，路径由服务自己拼在 ``Settings.inputs_dir`` 下。
名字会变成文件名，因此它必须是 ``slugify`` 的**不动点**（``check_canonical_id``）：
不满足就拒绝，不替调用方规范化 —— 规范化会把两个不同的名字悄悄合并成一份清单。

## 还有**第四**条路：冻结成员名单（``JobInputKind.COHORT``）

``JobInputKind`` 那三类都有一个共同点 —— 主体来自 ``inputs_dir`` 下的一个**文件**，
而那个文件是 operator 写的。§B12 消掉的是这条前提：系统自己发现并晋升出来的账号，
``market_account`` 里有，而上面三条路径**一条都读不到**。

所以 :func:`cohort_subjects` 是第四条路：它读**账号池**与一份**冻结成员名单**
（``application/cohorts.py`` 下半部分），产出同样是 :class:`SubjectResolution`。

**它与另外三条一样过 :func:`subjects_for`，只是读的东西不是一份文件。** 这一层
曾经把它留在枚举之外，理由写在这里：「名字指向库里的一份名单」会是第四种 ``input_ref``。
那个理由今天不成立，两处都看得出来 —— ``DISCOVERY_SCOPE`` 的 ``input_ref`` **本来**
就不是文件名（它是一个内容寻址的工件名，提交的人手里没有那个值，所以按名字反查回执），
而 §B12 的实现逐字要求「维护/草稿/**研究**接受 Scene selector 或 durable cohort 引用」。
把它留在外面，代价是这一句只对前两条成立，而第三条在工具面上根本没有入口 ——
一份冻好的名单，谁都送不到研究那一步。

``input_ref`` 在这一类下是一个**名单 id**（``coh-<hex16>``，``freeze_cohort`` 的产物）。
它照旧过 ``check_canonical_id``：``submit_research`` 对 ``input_ref`` 只有一条规则
（它是一条名字，不是路径），按种类开例外会让「哪一类该验」变成一个要维护的清单 ——
而那个清单的第一项就已经是错的。

四条路最后都走同一个 :func:`identity_refusal`、都是整份拒绝。上面三条留着，
因为**显式导入兼容要保留**（一份 operator 手写的清单有时仍然是想要的东西，比如
一次性研究一批刚谈下来的账号）。

## 三类输入**各自**有一个生产者（第三类在 E2 之后补上）

下面说的是 ``inputs_dir`` 那三类（§2 逐字：existing source receipt / known Accounts /
approved discovery scope），今天三种都会真的去读 ``inputs_dir`` 下的东西。
第四类 ``COHORT`` 不在这里 —— 它的生产者是上面那个，读的是状态库：

* **known accounts** —— 一份清单，:func:`known_account_dossiers`；
* **source receipt** —— 一份母池读入的回执，:func:`source_receipt_subjects`。
  写下它的那一步在 ``marketops/application/motherpool.py``：一份上游交进来的导出
  （按对象类型编号的、表格导出的）读成一份回执之后，每一行都有一个说得出来的去向，
  而 ``resolved`` 的那些行就是这里的主体。
* **discovery scope** —— 一轮开放网络发现跑出来的池子，:func:`discovery_scope_subjects`。
  写下它的那一步在 ``marketops/application/discovery_run.py``；这一类**按工件名**
  反查回执（回执名由内容派生，提交作业的人手里没有那个值），所以它的 ``input_ref``
  与另外两类不同，见那个函数的文档。

``_NO_PRODUCER`` 因此是**空表**，而它留在这里不是为了好看：它与
``_NAME_ADDRESSED`` 的差集正是「哪一类今天没有生产者」，而那个差集**只会**在有人往
``JobInputKind`` 里加成员时非空。那一刻需要一句话，见 :func:`_no_producer_reason`。
一条覆盖判据盯着这件事（``tests/test_job_inputs.py`` 的
``test_every_input_kind_is_either_served_or_has_a_sentence``）：新增一个种类而不处置它，
会在那里红，而不会变成一个安静的 ``KeyError``。

三类都**不在这里退化成空清单**。空清单会让 ``compile_work_items`` 抛
``ValueError``，而那个端口逐字写着一句「一个没有任何 work item 的作业在报告里长得
像已经做完」—— 于是缺一个生产者会被读成一次完成。这里明确返回一条**说得出的理由**，
由调用点把它落成作业的 ``BLOCKED`` 状态（§2 逐字要求 ``submit_research`` 返回
``truthful queued/blocked state``，而 ``BLOCKED`` 在此之前没有任何生产者）。

## 三条路径，同一条形状

回执那两条路径**不是**给已知账号那条加分支，它们是同一件事的三种输入：

| | known accounts | source receipt | discovery scope |
|---|---|---|---|
| ``input_ref`` | 清单的名字 | 回执的名字 | **工件**的名字（范围名） |
| 一行是 | ``AccountRecord`` | ``RowDisposition``（``resolved``） | 同上（同一种回执） |
| ``account_id`` | 清单里那个 ``account_id`` | **canonical 账号 id**（由场地句柄派生） | 同上 |
| 身份线索 | ``organisation`` / ``domain`` | ``resolved_name`` / ``claimed_official_domain`` | 同上 |
| 域名**权威** | 无（清单那一格只是转述 ⇒ ``unknown``） | ``verified_official_domains`` + ``domain_verdict`` | 同上 |
| ``input_sha256`` | 整行 + 名字 | 整行 + 名字（同一条散列规则） | 同上 |

回执那两栏的 ``account_id`` **不再**是 ``row_id``。两个空间的交集曾经是空集，改法
与理由写在 :func:`_receipt_dossiers` 与 ``application/promotion.py`` 里 —— 一句话：
``row-…`` 是**行在某份工件里的位置**，工件一重排它就变，而账号是**组织**。

``source receipt`` 与 ``discovery scope`` 共用同一份「回执 → 主体」的实现
（:func:`_receipt_dossiers`）：**同一种回执**读出来的东西必须逐字一样，
而两处各写一份的话，差别只会在某一天于某一个字段上出现。

三条最后都走 :func:`~marketops.domain.subject.identity_refusal`，而且都是**整份拒绝**：
一个 work item 都不建，所以没有任何一次检索会为它发生。这一条位置上的对齐是要紧的 ——
``kent-003`` 那次事故的成因正是「身份在这一层被丢掉」，而它不会因为输入换了一种
来源就变得可以丢掉。

## 为什么是返回值而不是异常

``SubjectResolution`` 把「这一类输入本部署还没有生产者」与「你给的名字不是一份
清单」分开：前者是**部署的事实**（调用方解除不了，只能等前置条件被建出来），
后者是**调用方给错了**。异常这条通道在本仓留给后者。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from ..domain.cohort import AccountCohort
from ..domain.enums import DomainVerdict, JobInputKind
from ..domain.errors import ConfigError
from ..domain.evidence import AccountRecord
from ..domain.receipt import RowDisposition, SourceReceipt
from ..domain.subject import (
    SUBJECT_KIND_ACCOUNT as _SUBJECT_KIND_ACCOUNT,
    SubjectDossier,
    identity_refusal,
)
from ..domain.util import check_canonical_id, sha256_text
from ..ports.pool import AccountCohortStore, AccountPoolStore
from .cohorts import load_accounts, read_cohort
from .motherpool import RECEIPT_SUFFIX, load_receipt, receipt_path
from .openweb import OPEN_WEB_SOURCE_METHOD
from .promotion import canonical_account_id_for_row

#: 已知账号清单的扩展名。与 ``cohorts.ACCOUNTS_FILE`` 同源（那份是**文件名**，
#: 这里是调用方给的**名单名**之后再拼上的后缀）。
KNOWN_ACCOUNTS_SUFFIX = ".yaml"

#: 账号这一类主体的 ``subject_kind`` 取值。**定义在** ``domain/subject.py``。
#:
#: 2026-09-27 从这里搬走：续跑意图（``domain/continuation.py``）也要用它，而领域层
#: 不得 import 应用层 —— 留着第二个字面量，两处迟早会对不上（``Observation`` 的
#: 主键里就有这一列，对不上的后果是同一格被当成两格：一格「从来没看过」，另一格
#: 也是）。这里保留这个名字，是因为本模块与 ``contact_routes`` 是按它导入的。
SUBJECT_KIND_ACCOUNT = _SUBJECT_KIND_ACCOUNT


def accounts_path(inputs_dir: Path, name: str) -> Path:
    """一份已知账号清单在盘上的位置。

    ## 为什么要有一个函数，而不是就地拼一次字符串

    这条规则（``<inputs_dir>/<名字>.yaml``）有两个消费者了：本模块装载主体给作业用，
    产品环的 ``list_accounts`` 装载同一份清单给人看。**两边拼出来的必须是同一个
    文件** —— 各拼一遍的后果不是「多写了一行」，是「列出来的账号」与「派出去研究
    的账号」来自两份不同的清单，而两份清单在报告上长得一样。名字 → 路径这条映射
    改过一次（后缀就改过），那时这两处会各改一遍，漏掉的那一处不报错。

    名字走 :func:`~marketops.domain.util.check_canonical_id`，与
    :func:`subjects_for` **同一条**：它必须已经是 ``slugify`` 的不动点，而不是
    由这里顺手规范化 —— 规范化会把两个不同的名字悄悄合并成一份清单。
    """
    return inputs_dir / f"{check_canonical_id(name, 'input_ref')}{KNOWN_ACCOUNTS_SUFFIX}"


@dataclass(frozen=True)
class SubjectResolution:
    """一次 ``input_ref`` 的解析结果：要么一串主体，**要么**一条理由。

    ``subjects`` 的顺序**有意义** —— 它决定 ``ordinal``，因而决定
    ``work_item_id``，所以这里从头到尾用元组而不是集合。

    两者**恰好有一个**，由 ``__post_init__`` 守。空的那一种不是「没有结果」：
    调用点会把 ``blocker`` 原样落成作业的 ``BLOCKED`` 理由，而 ``JobStore.set_state``
    对 ``BLOCKED`` 要求理由非空 —— 一个既没有 subject、又没有理由的解析结果，
    在那里的表现是一次崩溃，而不是一条读得懂的拒绝。让这个状态构造不出来，
    比在每个调用点记得判一次要可靠。

    元素是 :class:`SubjectDossier` 而不是字符串：一个只有 ``account_id`` 的解析结果
    正是「worker 拿内部 slug 去检索」那个缺口的形状，所以那一型在**类型上**就不该
    存在（见 ``domain/subject.py``）。
    """

    subjects: tuple[SubjectDossier, ...] = ()
    blocker: str = ""
    #: 被**身份判据**拒掉的那几行：``(主体 id, identity_refusal 的正文)``。
    #:
    #: 它与 ``blocker`` 是两件事，所以它不能藏在 ``blocker`` 的措辞里：那一条理由
    #: 也覆盖「回执里一行都没有」「清单是空的」「名单读不出来」这些失败，而它们
    #: **不是**待办 —— §12 逐字说了不是每个 unknown 都找人。只有身份冲突那一类
    #: 「人拿得出答案」（那是他自己的名录），所以要开的是那一种。
    #:
    #: 空元组有两个来路，而它们在这条判据下同形、也**应该**同形：这一趟没有被
    #: 身份判据拒掉任何一行，或者它根本没走到身份判据那一步（成功的解析走的是
    #: ``subjects`` 那一半）。两者的处置是同一个：一件待办都不开。
    identity_refusals: tuple[tuple[str, str], ...] = ()
    #: 这一份解析**依的是哪一份冻住的读数** —— 读它的那一次读交出来的那个对象，
    #: 或者 ``None``（这条路读的本来就不是一份冻结物，例如 operator 手写的清单文件）。
    #:
    #: ## 为什么是整份名单，而不是其中的几格
    #:
    #: 调用方要的两样东西都从它身上出：``as_of``（那份读数**自己的**时刻）与
    #: ``cohort_id``（它是**哪一份**）。这里的纪律是「谁读的谁带上来」—— 让调用方
    #: 拿着一个 id 自己再去存储里查一遍时刻，那第二遍可以与这一遍分家（两次读之间
    #: 可以又冻一份），而两份回执各自自洽。
    #:
    #: ## 它与「这一次读发生在什么时候」是两格，不是一个
    #:
    #: ``cohort`` 那条路读的是一份内容寻址、冻结不可变的成员名单，所以它的
    #: ``created_at`` 在任何 session、任何时刻读回来都一样 —— 那正是 §B25 要的
    #: 「所有 session 读到同一个 ``as_of``」。读侧自己的时钟（「这一趟跑在何时」）
    #: 是另一格，由调用方按自己的需要另报，**不得**折叠进这里：拿一个冻住的时刻
    #: 去判新鲜度，会让「这一格多久没复核了」永远答 0，而报告上一切正常。
    #:
    #: ``None`` 不是「不知道」，是「这一条路没有一份冻住的东西可以当锚」——
    #: 与 ``blocker`` 那一支同理，一格如实说答不上来，好过替它编一个。
    frozen: AccountCohort | None = None

    @property
    def as_of(self) -> datetime | None:
        """那份读数**自己的**时刻；读的不是冻结物时是 ``None``。"""
        return None if self.frozen is None else self.frozen.created_at

    def __post_init__(self) -> None:
        if bool(self.subjects) == bool(self.blocker):
            raise ValueError(
                "SubjectResolution 必须恰好给出一半：一串 subject，或一条理由，"
                f"实际拿到 subjects={self.subjects!r}、blocker={self.blocker!r}。\n"
                "两样都没有的那种，会被调用点写成一条没有理由的 BLOCKED —— "
                "而「阻塞」与「解析成功但清单是空的」在报告里必须分得开。"
            )
        if self.subjects and self.identity_refusals:
            raise ValueError(
                f"这一份解析给出了 {len(self.subjects)} 个主体，同时又报"
                f"{len(self.identity_refusals)} 行身份冲突 —— 身份判据在拒绝那条路上"
                "整份拒绝（一个 work item 都不建），所以成功的那一半不可能带着它。\n"
                "两个都填会让「这一趟到底跑没跑」有两个说法，而它们都成立。"
            )

    @property
    def ok(self) -> bool:
        return bool(self.subjects)


#: 今天真的会去 **``inputs_dir``** 里读东西的输入种类。
#:
#: 它们的 ``input_ref`` 是一个**名字**（不是路径），所以它们都需要一个合法的名字
#: —— 名字会变成文件名（对 ``discovery_scope`` 是工件名，另外两类是各自文件的名字）。
#: 「按名字寻址」是这三类的共同点，而不是巧合：收一个路径等于把「服务去读哪一份
#: 文件」交给调用方，一次跨机器部署下那就是一条越权读盘的接口。
_NAME_ADDRESSED: frozenset[JobInputKind] = frozenset(
    {
        JobInputKind.KNOWN_ACCOUNTS,
        JobInputKind.SOURCE_RECEIPT,
        JobInputKind.DISCOVERY_SCOPE,
    }
)

#: 今天**还没有**生产者的那些类，各配一句说得出的理由。**现在它是空的。**
#:
#: 留一张空表而不是删掉它，是因为它与 ``_NAME_ADDRESSED`` 的差集就是「哪一类今天
#: 没有生产者」，而那个差集只在有人往 ``JobInputKind`` 里加成员时非空 —— 那一刻
#: :func:`_no_producer_reason` 需要有地方可以取一句话，而覆盖判据
#: （``tests/test_job_inputs.py``）需要在**装载期**就红，而不是等到某一天有人提交
#: 一个作业。
#:
#: **理由必须点名本仓**已经**有的载体**（``marketops/`` 下的模块路径），而不只是说
#: 什么东西「不在本仓库里」。这一条曾经有执行器（把理由里的模块路径逐个拿到盘上
#: 核对），它挡的是那次具体的失真：``discovery_scope`` 的理由写着「发现管线不在本
#: 仓库里」，而展开器与投影落地之后那句话就不再准确了（缺的是把它们串成一圈的
#: runner，不是零件）。表空了之后那条判据没有对象可核，于是它换成了「差集必须为空」
#: —— 同一件事的更强形式：**没有一句理由要写，因为每一类都有生产者了**。
_NO_PRODUCER: dict[JobInputKind, str] = {}

#: ``account_id`` 是**算出来的**那两类输入。
#:
#: 它们的 ``account_id`` 由 :func:`~marketops.application.promotion.canonical_account_id_for_row`
#: 派生（回执的一行 → 场地句柄 → canonical 账号 id），所以「算得出来」与「池子里真的
#: 有那一行」是两件事，调用点必须在下发之前核一次
#: （:func:`~marketops.application.promotion.unpromoted_subjects`）。
#:
#: **``known_accounts`` 不在这一集里，而这不是遗漏。** 那一类的主体 id 是从 operator
#: 维护的清单里**照抄**下来的：清单里写的是什么，主体就是什么，没有一步派生 ——
#: 所以它没有「算得出来但不存在」这个状态可核。把 roster 的 id 也拿去与账号池对账，
#: 是一次**另一件事**的改动（「清单里的账号要不要先落进池子」），它属于 §6/§7 那一族，
#: 而不属于 §1 这一个断点。混进来会让这一条判据的失败原因不再唯一。
RECEIPT_KINDS: frozenset[JobInputKind] = frozenset(
    {
        JobInputKind.SOURCE_RECEIPT,
        JobInputKind.DISCOVERY_SCOPE,
    }
)


def _no_producer_reason(kind: JobInputKind) -> str:
    """这一类今天为什么没有生产者。**表里没有时也给出一条理由，不抛 ``KeyError``。**

    走到这里意味着 ``JobInputKind`` 里有一个成员既不在 ``_NAME_ADDRESSED``、也不在
    ``_NO_PRODUCER`` 里 —— 那是**本模块的缺口**（有人往枚举里加了成员却没在这里
    处置），不是调用方给错了。所以它走阻塞那条通道：受理会把它落成作业的 ``BLOCKED``，
    而理由就是这一句，读的人能看出要补的是哪一处。
    """
    declared = _NO_PRODUCER.get(kind)
    if declared:
        return declared
    return (
        f"{kind.value} 这一类今天既没有生产者，也没有在 marketops/application/inputs.py "
        f"里写下一句话：它既不在 _NAME_ADDRESSED 里，也不在 _NO_PRODUCER 里。\n"
        "这是本模块的一条缺口（JobInputKind 多了一个成员，而这里没有处置它），"
        "不是调用方给错了。补一处：要么给它一个生产者（并加进 _NAME_ADDRESSED），"
        "要么在 _NO_PRODUCER 里写清缺的是什么。"
    )


def _input_row_sha256(row: BaseModel, *, name: str) -> str:
    """**这一整行输入**的字节摘要 —— :attr:`SubjectDossier.input_sha256` 的唯一生产者。

    散的是整张记录（包括本仓不放进任务包的那些格），不是身份那几个字段。理由：
    这个值回答的问题是「这一行被改过没有」，而任何一处改动都算改过 —— 运营者改了
    备注，也是一次新的输入，而它必须与重发分开（§6「Same request/result retries
    are idempotent」说的是**同一次**请求）。

    序列化用 ``model_dump(mode="json")`` + ``sort_keys`` + 紧凑分隔符，理由与
    ``findings_sha256`` 相同：默认输出带空格，而同一份记录在不同 Python 版本上
    可能给出不同空白 —— 那时同一个内容会算出两个散列，一次重发被读成一次冲突。

    ``name`` 进散列：同一个账号出现在两份清单里是两行输入。它同时也是出错时
    说得出来源的那一格。

    **收的是 ``BaseModel`` 而不是某一个具体型**：两种输入今天都会走到这里
    （已知账号清单的一行、一份来源回执的一行），而它们必须是**同一条**散列规则 ——
    两条规则会在某一个字段上悄悄分叉，那时同一次重发在两边的表现不一样。
    """
    payload = {"input_ref": name, "row": row.model_dump(mode="json")}
    return sha256_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def row_sha256(record: AccountRecord, *, name: str) -> str:
    """已知账号清单里一行的 ``input_sha256``。见 :func:`_input_row_sha256`。"""
    return _input_row_sha256(record, name=name)


def _receipt_dossiers(
    inputs_dir: Path, receipt: SourceReceipt, *, name: str, what: str
) -> SubjectResolution:
    """一份**来源回执** → 一串主体：``resolved`` 的那些行，**按 ``ordinal``**。

    ``source_receipt_subjects`` 与 :func:`discovery_scope_subjects` 共用这一份 ——
    两处收的是**同一种**回执，所以「回执 → 主体」这一步必须逐字相同。各写一份的话，
    差别只会在某一天落在某一个字段上，而两条路的报告长得一样。

    ``name`` 进 ``input_sha256``（同一个账号出现在两份输入里是两行输入），
    ``what`` 只进**错误消息**：两类的找法不同（一类按回执名、一类按工件名反查），
    所以「找的是哪一份」这句话必须由调用方给 —— 否则一次「按名字没找到」会被写成
    一句与它不符的话。

    顺序不是风格问题：它决定 ``ordinal``，因而决定 ``work_item_id`` —— 与已知账号
    那条路径逐字同一条理由。

    只有 ``resolved`` 的行会产出一个主体。另外四种去向**不是**被丢掉了，它们留在
    回执里带着理由（见 ``domain/receipt.py``），而这里要说的是它们**不该**变成
    工作单元：

    * ``pending`` 是「还缺一次身份补全」，不是「这一行坏了」。给它建一个工作单元
      就是 2026-09-23 那次事故的形状 —— worker 拿一个标识去检索，9 次查询落在无关
      的同名物上，而报告上看起来只是一次正常的、什么都没找到的检索。
    * ``merged`` 指向的那一行已经在序列里了，再建一个就是同一件事做两遍。
    * ``excluded`` 是按一份**已声明**的排除规则排除的；给它建工作单元等于把那条
      规则的意义取消掉。
    * ``failed`` 需要人看，不是需要一次检索。

    ## ``account_id`` 是 canonical 账号 id，**不是** ``row_id``

    ``account_id`` 走 :func:`~marketops.application.promotion.canonical_account_id_for_row`
    —— 也就是「这一行是哪一块场地」（``venue_handle_for_row``）再派生出来的那个
    账号 id，与 ``promote_receipt`` 折进 ``market_account`` 的**是同一个值**。

    **从前这里写的是** ``row.row_id``。那时两边的报告各自看起来都正常，而
    ``research_work_item.account_id ∩ market_account.account_id`` 是空集：worker
    拿了 ``row-…`` 去检索，证据落在 ``row-…`` 名下，账号池里却是另一套 slug ——
    检索真的发生了、证据真的落了盘，而没有一个账号读得出来。所以那一格不只是
    「换了个写法」，它是 §1 那条断点**原来所在的位置**。

    ``row_id`` 今天仍然有用，但它只是**出处**：它说的是「哪份工件里的第几行」，
    而 ``source_ref`` 已经指了工件、``row_id`` 会随回执行一起被引。把它当身份用，
    就是「一行在某份工件里的位置」当成了「一个组织」——工件一重排，身份就变了。

    一个推论要写下来：**这一层算得出 id，不等于池子里真有那一行。** 算得出来与真的
    折进去了差着一次 :func:`~marketops.application.promotion.promote_receipt`，所以
    调用点还必须在**下发之前**核一次（``unpromoted_subjects``），否则整条链会按一个
    不存在的账号安静地跑完。

    ``country`` 是 ``None``：回执这一层**不知道**国家。工件里可能有那么一列，
    但「这一列是国家」是一个判断，而这一层拿不出依据支持它 —— 一行按位置或按
    别名猜出来的国家，在报告里与一个真读出来的国家长得一样。留给发现侧那一轮
    按 profile 冻结的值填（那里它有出处）。``country`` 是
    :class:`SubjectDossier` 上唯一允许为 None 的字段，所以这是它被留空的地方。
    """
    rows = receipt.resolved_rows
    if not rows:
        # 一条空的主体序列会让 ``compile_work_items`` 抛，而那个端口逐字写着
        # 「一个没有任何 work item 的作业在报告里长得像已经做完」。整池都是
        # pending 与「这份回执本来就没有行」也必须分得开，所以两句话都带上。
        return SubjectResolution(
            blocker=(
                f"{what} 里没有一行可以拿去研究。\n{receipt.counts_line}\n{receipt.blocker}"
            )
        )

    dossiers = tuple(
        SubjectDossier(
            account_id=canonical_account_id_for_row(row),
            organisation=row.resolved_name,
            domain=_clue_domain(row),
            country=None,
            source_ref=str(receipt_path(inputs_dir, receipt.receipt_id)),
            input_sha256=_input_row_sha256(row, name=name),
            domains=_binding_domains(row),
            domain_verdict=_binding_verdict(row),
        )
        for row in rows
    )

    # 与已知账号那条路径同一个位置、同一条判据：**在派发之前**问一次
    # 「这一行有没有可用来做身份发现的依据」，缺一个就整份拒绝 —— 一个 work item
    # 都不建，所以没有任何一次检索会为它发生。
    #
    # 回执自己的校验已经要求 ``resolved`` 行必须带 ``resolved_name``，所以这一条
    # 今天**不会**响。留着它不是因为预计它会响，而是因为它把「dossier 有效」这个
    # 前提放在构造它的地方，而不是放在两个模块之间的一句口头约定上 ——
    # 那正是 ``kent-003`` 那次事故的成因。
    refusals = tuple(
        (dossier.account_id, reason)
        for dossier in dossiers
        if (reason := identity_refusal(dossier))
    )
    if refusals:
        lines = "\n".join(
            f"  - {account_id}：{reason.splitlines()[0]}" for account_id, reason in refusals
        )
        return SubjectResolution(
            blocker=(
                f"{what} 里有 {len(refusals)} 行没有可用来做身份发现的依据：\n{lines}\n"
                "\n整份拒绝，**不是**跳过那几行。这一次没有被花掉：一个 work item 都"
                "没有建出来，所以没有任何一次检索会发生。"
            ),
            identity_refusals=refusals,
        )
    return SubjectResolution(subjects=dossiers)


def receipt_for_subject(
    subject: SubjectDossier, *, inputs_dir: Path, kind: JobInputKind
) -> tuple[SourceReceipt | None, str]:
    """这件工作研究的那一块场地出自**哪一份回执**，或一条说得出的理由。

    ## 为什么这条路是「按 ``source_ref`` 反查」而不是「按 ``input_ref`` 重找」

    ``input_ref`` 有两类：``source_receipt`` 是回执名，``discovery_scope`` 是**工件
    名**（按它反查回执，见 :func:`discovery_scope_subjects`）。重找一遍会把那条
    反查**再写一遍** —— 而其中一种失败是「找到了两份」，它的判据只有一处定义。
    更根本的是：这个名字在这件工作被编译之后还可能被用于**另一次**作业，按它
    重找得到的不一定是当初编译这件的那个文件。

    ``source_ref`` 是受理那一刻冻下来的那一格（``_receipt_dossiers`` 写的就是它），
    而 ``input_sha256`` 冻的是**那一行**的字节。所以这里按它反查，并核一件事：
    那个路径必须正好是这个 ``inputs_dir`` 下、由回执 id 命名的那个文件。

    ## 这一条判据挡的是什么

    挡的是「一次调用把关系写到另一份输入所描述的东西上」。``source_ref`` 落在
    ``inputs_dir`` 之外（或名字不是这个目录的命名规则）时**不接受** ——
    接受它意味着服务端会去读一个由记录指定的路径，而那条路没有任何边界。

    ## 它不负责回答的两件事

    * **这一行是不是这块场地**：那是
      :func:`~marketops.application.promotion.venue_handle_for_account` 的活 ——
      它按账号 id 比，比不上就是空串。
    * **这份回执读不读得出来**：读不出来在这里就是一条理由（``load_receipt``
      自己会验文件名的 id 与内容自洽），不抛。
    """
    if kind not in RECEIPT_KINDS:
        return None, (
            f"这一类输入（{kind.value}）的主体不是从一份来源回执来的，所以这件工作"
            "说不出它研究的是哪一块场地。\n"
            "责任关系说的是「这个账号对**哪块场地**负哪种责」，而它只对"
            f"{sorted(item.value for item in RECEIPT_KINDS)} 这两类成立 —— "
            "那两类的主体 id 由场地句柄派生，所以「是哪块场地」答得出来。"
        )

    source_ref = (subject.source_ref or "").strip()
    name = Path(source_ref).name
    suffix = RECEIPT_SUFFIX
    if not name.endswith(suffix):
        return None, (
            f"主体冻下来的来源引用（{source_ref!r}）不是一个回执文件。\n"
            f"这一类的输入在受理那一刻把来源写成了回执路径（``*{suffix}``），"
            "而这一格不是 —— 它可能来自另一个版本的编译产物，也可能被手工改过。"
            "两种情况下「这一行出自哪一份回执」都没有答案，所以这一批关系不写。"
        )
    receipt_id = name[: -len(suffix)]
    try:
        path = receipt_path(inputs_dir, receipt_id)
    except (ValueError, ConfigError) as exc:
        return None, f"主体冻下来的来源引用（{source_ref!r}）不是一个回执 id：{exc}"
    if str(path) != source_ref:
        return None, (
            f"主体冻下来的来源引用（{source_ref!r}）不是这个部署的输入目录下的那一份"
            f"（应当是 {path}）。\n"
            "来源引用是受理那一刻写下的路径，而它是唯一一处说明「这一行出自哪一份"
            "回执」的地方 —— 它指向别处时**不猜**：接着往下走会把关系挂到另一份"
            "输入所描述的东西上，而报告里那一行看起来完全正常。"
        )
    try:
        return load_receipt(inputs_dir, receipt_id), ""
    except ConfigError as exc:
        return None, (
            f"这件工作的来源回执读不出来：{exc}\n"
            "回执是「这一行折成了哪个账号」唯一的权威，读不到它就说不出这个账号"
            "研究的是哪块场地 —— 所以这一批关系不写，而不是猜一块。"
        )


def source_receipt_subjects(inputs_dir: Path, name: str) -> SubjectResolution:
    """一份**来源回执** → 一串主体。``input_ref`` 是**回执的名字**。

    Raises:
        ValueError: 只有 :func:`subjects_for` 在派发**之前**抛的那一条（``name``
            不是规范形式）。这里自己不抛 —— 读回执的每一种失败都走 ``blocker``。
    """
    try:
        receipt = load_receipt(inputs_dir, name)
    except ConfigError as exc:
        return SubjectResolution(blocker=str(exc))
    return _receipt_dossiers(inputs_dir, receipt, name=name, what=f"回执 {name}")


def discovery_scope_subjects(inputs_dir: Path, name: str) -> SubjectResolution:
    """一轮**已批准的发现范围** → 一串主体。``input_ref`` 是**范围名**。

    ## 为什么这一类的 ``input_ref`` 与另外两类不同

    范围名就是那一轮写出的**工件名**（``DiscoveryScope.artifact_ref``），所以这里
    按 ``artifact_ref`` **反查**回执，而不是按回执名收一个名字。回执名由内容派生
    （来源方法 + 批次 + 工件摘要 + 处置摘要），提交作业的人手里没有那个值，
    也不该被要求先把它算出来 —— 要求他算，等于把一条内部推导搬进调用方的
    操作流程里，而抄错一个字符的表现是「找不到回执」，不是「你抄错了」。

    ## 为什么必须同时核对 ``source_method_id``

    只按 ``artifact_ref`` 找是不够的：**工件名与回执名是两个命名空间**，一个别的
    来源方法写的回执完全可能把 ``artifact_ref`` 记成同一个字符串。回执是「这一批
    读成了什么」的权威，而这一类输入说的是「这是那一轮发现的结果」—— 两者的差别
    正是 ``source_method_id``。不带它，一次名字撞车会让这一类输入读进一份
    **不是发现产出**的池子，而报告上每一行看上去都正常。

    ## 找不到 / 找到多份都拒绝

    找不到（或找到多份）**不降级成空清单**：空清单会让 ``compile_work_items`` 抛，
    而那个端口逐字写着「一个没有任何 work item 的作业在报告里长得像已经做完」。
    这里返回一条说得出的理由，由调用点落成作业的 ``BLOCKED``。

    找到多份是一次**歧义**，不是一个可以随便挑一个的情况：两份回执指着同一份工件
    意味着工件被读过两次（或工件被换过），而随便挑一个的后果是报告上出现一份
    **没人选过的**输入的读数。

    Raises:
        ValueError: 只有 :func:`subjects_for` 在派发**之前**抛的那一条（``name``
            不是规范形式）。这里自己不抛 —— 每一种失败都走 ``blocker``。
    """
    matches: list[SourceReceipt] = []
    unreadable: list[str] = []
    for path in sorted(inputs_dir.glob(f"*{RECEIPT_SUFFIX}")):
        try:
            receipt = load_receipt(inputs_dir, path.name[: -len(RECEIPT_SUFFIX)])
        except ConfigError as exc:
            # 盘上一份读不出来的回执**不是**「这里没有回执」。它单独记下来，
            # 因为一份被改过、或与自己的 id 对不上的回执，恰恰可能是**本来会
            # 匹配**的那一份 —— 静默跳过它，这一次的结果就会是「找不到」，
            # 而那一句话在这里是不准确的。
            unreadable.append(f"{path.name}：{str(exc).splitlines()[0]}")
            continue
        if (
            receipt.artifact_ref == name
            and receipt.source_method_id == OPEN_WEB_SOURCE_METHOD.id
        ):
            matches.append(receipt)

    if len(matches) > 1:
        return SubjectResolution(
            blocker=(
                f"范围 {name!r} 命中了 {len(matches)} 份回执："
                f"{sorted(match.receipt_id for match in matches)}。\n"
                "两份回执指着同一份工件意味着工件被读过两次（或被换过）—— "
                "「读的是哪一份」在这里没有答案，而随便挑一份的后果是报告上出现"
                "一份**没人选过的**输入的读数。\n"
                "请只留一份（盘上的回执按 id 内容寻址，重复往往来自一次手工复制）。"
            )
        )
    if not matches:
        tried = (
            f"在 {inputs_dir} 下没有一份回执的 artifact_ref 是 {name!r} 且来源方法是 "
            f"{OPEN_WEB_SOURCE_METHOD.id!r}。\n"
            "这一类的 input_ref 是**那一轮发现的范围名**，而范围名同时是它写出的"
            "工件名（见 marketops/application/discovery_run.py）。所以先确认那一轮"
            "真的跑过、且它写出的工件还在这个目录下。"
        )
        if unreadable:
            tried += (
                f"\n另外有 {len(unreadable)} 份回执读不出来（它们**不在**上面的"
                "「没有一份」之列 —— 其中可能有本来会匹配的那一份）：\n"
                + "\n".join(f"  - {line}" for line in unreadable)
            )
        return SubjectResolution(blocker=tried)

    return _receipt_dossiers(
        inputs_dir,
        matches[0],
        name=name,
        what=f"范围 {name}（回执 {matches[0].receipt_id}）",
    )


def _binding_domains(row: RowDisposition) -> tuple[str, ...]:
    """这一行**拿去判定**的域名集合。**证得的优先，没证过才退回转述的。**

    回执那一行上域名有三格，而它们不是同义词：

    * ``claimed_official_domain`` —— 别人说的（上游导出/登记册那一列）；
    * ``discovered_host`` —— 我们在哪台主机上读到这一行（一次观察）；
    * ``verified_official_domains`` —— 我们证得的（有证据指着）。

    绑定那一层只能拿最后一种当权威：前两种各自都会**两个方向都错** ——
    社媒页上那个真实域名会被判成外来域，而目录站的主机会被当成它自己的域名。
    证得过的行用证得的；没证过的行退回转述的，但那一份**不带权威**
    （:func:`_binding_verdict` 给 ``unknown``）：跨域邮箱不因此被拒。
    """
    verified = tuple(row.verified_official_domains)
    if verified:
        return verified
    claimed = row.claimed_official_domain
    return (claimed,) if claimed else ()


def _binding_verdict(row: RowDisposition) -> DomainVerdict:
    """那个集合到哪一步了。**只有 ``confirmed`` 给权威**，其余一律 ``unknown``。

    ``ABSENT``（查过、确实没有官网）**不**在这里翻成权威：一个没有官网的组织，
    它的邮箱在**任何一个**域上都不构成「外来域」—— 把 ABSENT 读成「任何域都不符」
    会让整批账号的邮箱被拒，而理由栏里的话听起来完全正当。
    """
    if row.verified_official_domains:
        return row.domain_verdict
    return DomainVerdict.UNKNOWN


def _clue_domain(row: RowDisposition) -> str:
    """给人（与检索）看的那一个域名线索：证得的优先，没有再退回转述的。

    它是**线索不是判定**：多官网时 :attr:`RowDisposition.verified_official_domain`
    自己就是空的（单数读法的定义），此时退回转述值比留空更有用 —— 全部集合在
    :func:`_binding_domains` 里，判定不看这一格。
    """
    return row.verified_official_domain or row.claimed_official_domain


def known_account_dossiers(
    inputs_dir: Path, name: str
) -> tuple[SubjectDossier, ...]:
    """已知账号清单里的**主体身份**序列，**按文件顺序**。

    顺序不是风格问题：它决定 ``ordinal``，因而决定 ``work_item_id``。重排同一份
    清单会被 ``JobStore.compile_work_items`` 读成另一批工作（它比对整个序列，
    而不只是集合），所以这里原样保留文件里的顺序。

    **从前这里只返回 ``account_id``。** 那正是 2026-09-23 那次真实运行的根因：
    worker 拿到 ``kent-003`` 之后拿它去检索，9 次查询全落在无关的同名物上。
    一个 slug 当不了身份 —— 它只是本仓内部的主键。

    带进任务包的是 :class:`SubjectDossier` 的六个字段，**不含** ``cohort`` /
    ``status`` / ``notes``：那是判定与自由文本，见 ``domain/subject.py`` 的理由。

    Raises:
        ConfigError: 清单不存在或读不出来。消息里带**看的是哪个目录**与应有的形状 ——
            「清单不存在」这句话没有告诉运维把文件放到哪里。
    """
    path = accounts_path(inputs_dir, name)
    accounts = load_accounts(path)
    return tuple(
        SubjectDossier(
            account_id=account.account_id,
            organisation=account.organisation,
            domain=account.domain,
            country=account.country,
            source_ref=str(path),
            input_sha256=row_sha256(account, name=name),
            # 清单里那一格是**转述**（谁写的那份 YAML），不是谁证得的 —— 所以集合
            # 照收，判定留 ``unknown``。这一对说的是「清单说它是 example.org，
            # 而没有人证过」，于是绑定那一层**不会**因为一个跨域邮箱就把路线拒掉。
            domains=(account.domain,) if account.domain else (),
        )
        for account in accounts
    )


def account_subjects(inputs_dir: Path, name: str) -> tuple[tuple[str, str], ...]:
    """账号清单 → 维护层的 ``(subject_kind, subject_id)`` 序列，**按文件顺序**。

    ``subject_kind`` 与 ``subject_id`` 是两段而不是一个拼起来的字符串
    （``Observation`` 的 docstring 逐字说了为什么）。这里的取值是
    :data:`SUBJECT_KIND_ACCOUNT` 与 ``account_id``。

    **顺序照抄文件顺序**，与 :func:`known_account_dossiers` 同一条理由：
    ``what_is_due`` 的返回值要按 ``(due_at, subject_kind, subject_id, aspect)``
    排序才可复现，而两份不同的顺序会让「取前 N 件」在两次运行里做不同的事 ——
    账上只显示「各做了一次」。

    这是维护 tick 的**主体来源**，而且是唯一的一个：``what_is_due`` 自己发现不了
    主体，也不该发现（它逐字写了「一个会自己发现主体的选择器必然也会自己发明
    主体」）。把这份清单交上去，等于说「这个市场认这些主体」—— 那是一个由人来
    下的判断，而它今天恰恰来自账号池。
    """
    return tuple(
        (SUBJECT_KIND_ACCOUNT, dossier.account_id)
        for dossier in known_account_dossiers(inputs_dir, name)
    )


def cohort_subjects(
    pool: AccountPoolStore,
    cohorts: AccountCohortStore,
    *,
    scene_id: str,
    cohort_ref: str,
) -> SubjectResolution:
    """一份**冻结成员名单** → 一串主体（§B12）。``cohort_ref`` 为空 = 当前那一份。

    这是 ``known_account_dossiers`` 那条路径在**没有 YAML** 时的替身：主体不再来自
    一个 operator 手写的文件，而是来自系统自己读池子冻下来的读数。两条路径的**形状
    逐字相同**（都是 ``SubjectResolution``、都在派发之前问一次身份判据、都是整份拒绝），
    这是有意的 —— 「同一种输入读出来的东西必须逐字一样，而两处各写一份的话，差别
    只会在某一天于某一个字段上出现」（模块 docstring 里那句话，这里多一个成员）。

    与另外三条路径的**唯一**差别是失败面：这条路径上的每一次失败都是一条 ``blocker``，
    不抛。读池子、读名单、核成员都属于「部署的事实」（名单还没冻、库被换过），
    调用方解除不了前两条，而第三条要的是一次查看而不是一次改正 —— 所以它们与
    ``subject_for`` 的 ``_no_producer_reason`` 走同一条通道。
    """
    try:
        reading = read_cohort(
            pool, cohorts, scene_id=scene_id, cohort_ref=cohort_ref
        )
    except ConfigError as exc:
        return SubjectResolution(blocker=str(exc))
    dossiers = reading.dossiers

    # 与回执那条路径同一个位置、同一条判据：**在派发之前**问一次「这一行有没有
    # 可用来做身份发现的依据」，缺一个就整份拒绝 —— 一个 work item 都不建，
    # 所以没有任何一次检索会为它发生。
    refusals = tuple(
        (dossier.account_id, reason)
        for dossier in dossiers
        if (reason := identity_refusal(dossier))
    )
    if refusals:
        lines = "\n".join(
            f"  - {account_id}：{reason.splitlines()[0]}" for account_id, reason in refusals
        )
        return SubjectResolution(
            blocker=(
                f"成员名单里有 {len(refusals)} 个主体没有可用来做身份发现的依据：\n"
                f"{lines}\n"
                "\n整份拒绝，**不是**跳过那几行：跳过会让一份十二行的名单编译出十一个"
                "工作单元，而报告里那个「11」与「名单里本来就只有 11 个」看起来一样。\n"
                "这一次没有被花掉：一个 work item 都没有建出来，所以没有任何一次检索、"
                "任何一次模型调用会发生。"
            ),
            identity_refusals=refusals,
        )
    # 那份名单**自己**跟着主体一起交出去：``as_of``（它自己的时刻）与 ``cohort_id``
    # （它是哪一份）都从这一个对象上出，不由调用方另读一遍、更不另取一次时钟。
    # 所以两个 session 读同一份名单时这两格逐字相同，读两份不同的名单时它们跟着变
    # —— 两条都由 ``CohortReading`` 保证。
    return SubjectResolution(subjects=dossiers, frozen=reading.cohort)


def _cohort_resolution(
    cohort_ref: str,
    *,
    scene_id: str,
    pool: AccountPoolStore | None,
    cohorts: AccountCohortStore | None,
) -> SubjectResolution:
    """``cohort`` 那一路的接线判据，与「名单读不出来」分开。

    两句话的处置不同：**没有接线**是部署的事实（补组合根那一行），**名单读不出来**
    是库里的读数（去 ``freeze_cohort`` 冻一份、或去查那个 id 是不是另一个 workspace
    的）。合成一句会让一个没装这一段存储的部署，看起来像一个还没冻过名单的市场
    ——「尚未接线」读成「这里是空的」，正是本仓反复拒绝的那种同形。
    """
    if pool is None or cohorts is None:
        return SubjectResolution(
            blocker=(
                "这个部署没有接账号池或冻结成员名单的存储，所以按 cohort 受理研究"
                "这条路走不通。\n"
                "  它需要两个工厂：``accounts``（读池子）与 ``cohorts``（读名单）。"
                "缺的是组合根那一行接线 —— 不是 ``input_ref`` 给错了。\n"
                "  另一个选择：换一个 input_kind（``discovery_scope`` / "
                "``known_accounts`` / ``source_receipt``），它们不读这两样。"
            )
        )
    return cohort_subjects(pool, cohorts, scene_id=scene_id, cohort_ref=cohort_ref)


def subjects_for(
    kind: JobInputKind,
    name: str,
    *,
    inputs_dir: Path,
    scene_id: str = "",
    pool: AccountPoolStore | None = None,
    cohorts: AccountCohortStore | None = None,
) -> SubjectResolution:
    """``(input_kind, input_ref)`` → 一组主体，或一条说得出的阻塞。

    Raises:
        ValueError: ``name`` 不是 ``slugify`` 的不动点（含路径分隔符、首尾空白、
            大写字母 —— 都会让「名字 → 文件名」这条映射不再一一对应）。
            这是**调用方给错了**，不是部署的事实，所以不走阻塞那条通道。
    """
    # ``cohort`` 那一路**先**判，因为它与下面三类读的不是同一样东西：它读的是
    # **账号池**与一份**冻结成员名单**（都在状态库里），不是一个按名字寻址的文件。
    # 放在 ``_NAME_ADDRESSED`` 那一道之后会让它被那句「今天没有生产者」挡下 ——
    # 而它**有**生产者，只是那个生产者不看 ``inputs_dir``。
    if kind is JobInputKind.COHORT:
        return _cohort_resolution(
            name, scene_id=scene_id, pool=pool, cohorts=cohorts
        )
    # 名字先验，**先于种类派发**。一个需要规范化的名字是**调用方给错了**
    # （异常那条通道），不是一条部署事实 —— 所以它不该因为「这一类今天没有生产者」
    # 而被跳过。
    #
    # 「没有生产者的那一种也照样验」这句话看着多余（它今天不拼任何路径），但那个
    # 时机很贵：``input_ref`` 一旦进了作业记录就写死了，而第一个生产者会照它去拼
    # ``inputs_dir`` 下的路径。校验放在这里，那个未来的实现就不会在某一天突然拿到
    # 一个从没被验过的名字 —— 而那一天炸的是一个**已经受理**的作业。
    checked = check_canonical_id(name, "input_ref")

    if kind not in _NAME_ADDRESSED:
        return SubjectResolution(blocker=_no_producer_reason(kind))
    if kind is JobInputKind.SOURCE_RECEIPT:
        return source_receipt_subjects(inputs_dir, checked)
    if kind is JobInputKind.DISCOVERY_SCOPE:
        return discovery_scope_subjects(inputs_dir, checked)

    try:
        dossiers = known_account_dossiers(inputs_dir, checked)
    except ConfigError as exc:
        return SubjectResolution(blocker=str(exc))
    if not dossiers:
        # 空清单不是「受理成功、只是没有活干」。编译空清单会被
        # ``compile_work_items`` 拒绝（逐字：「一个没有任何 work item 的作业在报告里
        # 长得像已经做完」），所以这里要说的正是那句话 —— 而不是让调用点去猜
        # 该报 0 还是该报错。
        return SubjectResolution(
            blocker=(
                f"清单 {accounts_path(inputs_dir, checked)} 里一个账号都没有。\n"
                "一个没有任何 work item 的作业在报告里长得像『已经做完』—— "
                "它其实什么都没跑。请往里写账号，或换一个非空的 input_ref。"
            )
        )

    # **在派发之前**把整份包验一遍，缺一个就不编译。这里的位置是本模块的要点：
    # 到了 ``compile_work_items`` 就已经有工作单元了，而工作单元的失败形态是
    # 「worker 领走它、然后花掉真金白银」。所以判据必须在**编译之前**成立 ——
    # 没有任何一个 work item 被建出来，也就没有任何一次检索会为它发生。
    #
    # 整份拒绝而不是跳过那一行：跳过会让一份五行的清单编译出四个工作单元，而
    # 报告里那个「4」与「清单里本来就只有 4 个」长得一样。要部分研究，就写一份
    # 部分的清单。
    refusals = tuple(
        (dossier.account_id, reason)
        for dossier in dossiers
        if (reason := identity_refusal(dossier))
    )
    if refusals:
        lines = "\n".join(
            f"  - {account_id}：{reason.splitlines()[0]}" for account_id, reason in refusals
        )
        return SubjectResolution(
            blocker=(
                f"清单 {accounts_path(inputs_dir, checked)} 里有 "
                f"{len(refusals)} 个主体没有可用来做身份发现的依据：\n{lines}\n"
                "\n整份拒绝，**不是**跳过那几行：跳过会让一份五行的清单编译出四个工作"
                "单元，而报告里那个「4」与「清单里本来就只有 4 个」看起来一样。\n"
                "这一次没有被花掉：一个 work item 都没有建出来，所以没有任何一次检索、"
                "任何一次模型调用会发生。补齐这几行（真实名称必需，域名可以没有），"
                "或者换一份只含完整行的清单。"
            ),
            identity_refusals=refusals,
        )
    return SubjectResolution(subjects=dossiers)


__all__ = [
    "KNOWN_ACCOUNTS_SUFFIX",
    "SUBJECT_KIND_ACCOUNT",
    "SubjectResolution",
    "account_subjects",
    "accounts_path",
    "cohort_subjects",
    "discovery_scope_subjects",
    "known_account_dossiers",
    "row_sha256",
    "source_receipt_subjects",
    "subjects_for",
]

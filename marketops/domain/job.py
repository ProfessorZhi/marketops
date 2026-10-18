"""作业、可执行单元、Quay 映射与租约的值对象，以及**身份的推导规则**。

分家情况：枚举与状态转移表在 ``domain/enums.py``，存储的契约在
``ports/jobs.py``，实现分别在 ``adapters/sqlite/jobs.py`` 与
``adapters/memory.py``。本模块放的是**两方都必须一致**的三样：

* :class:`ResearchJob` / :class:`WorkItem` / :class:`ExecutionRef` /
  :class:`WorkItemSnapshot` / :class:`JobAttempt` / :class:`Settlement` ——
  六条记录的完整形状；
* :func:`job_id` / :func:`work_item_id` / :func:`attempt_id` —— 内容寻址的 id。

## 这四个概念不是这里发明的

``docs/DATA_MODEL.md`` §0 已经把血缘写下来了：

    ResearchJob
      → WorkItem[]
      → ExecutionRef (Quay task/run)
      → artifacts
      → deterministic validation
      → business-state mutation

同一节逐字写着「Quay state is execution metadata, not business truth」，而
Phase G 的事实基础（``docs/MVP_MIGRATION_PLAN.md`` §2.9）证实了 Quay 那侧
**连 attempt 和 lease 都没有** —— 所以这四个概念必须是 MarketOps 自己的一等
记录，Quay 侧只留映射。这一节是本模块存在的理由，不是装饰。

## 为什么 id 的推导在领域层

与 ``domain/budget.py`` 同一个理由，且同一条纪律在这里要用三次：id 若由实现
自己造（自增、UUID、行号），「同一个 ``(work_item, holder)`` 永远得到同一条租约」
这条幂等承诺就退化成某个实现的巧合。

三个 id 空间**必须互不相交**，所以每次推导都带一个 ``kind`` 前缀。少了它，
``work_item_id("j", 1)`` 与 ``attempt_id("j", 1)`` 会拼出同一个字符串 ——
两个不同的东西获得同一个 id，而 id 决定的是「这一行说的是谁」。这不是假想的
风险：``tests/test_budget_contract.py`` 里那条「``("a:b", "c")`` 与 ``("a", "b:c")``
不是同一条」守的是同一类失败，只是那一处是分隔符歧义，这一处是字段数相同。

## 刻意**没有**的东西

* **没有结果修订号**。§6 说「stale attempts cannot overwrite later versions」。
  这条要求由**「哪一个 attempt 是活的」**满足，不由一个计数器满足：一个 work
  item 同时最多一条租约，而 ``settle`` 只接受活租约，所以「陈旧」在结构上写不
  进去。一个与「谁是活的」并存的计数器会有两个真相来源，而它们不一致时谁都
  看不出来。
* **没有 ``lease_expires_at``**。理由见 ``domain/enums.py::AttemptState``：猜错
  「那个 worker 死了」的代价是两个 worker 跑同一件昂贵的事。
* **没有作业级的 ``quay_task_id``**。映射挂在 work item 上，因为
  ``docs/DATA_MODEL.md`` 的血缘就是这么写的（``WorkItem → ExecutionRef``）。
  挂一份在作业上会让「这个 task 是哪个 work item 的」变成一个可以答错的问题。
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import AttemptState, JobInputKind, JobState, StopReason
from .findings import Findings, findings_sha256
from .subject import SubjectDossier
from .util import SEP, sha256_text

#: 内容寻址 id 的截断长度。32 个十六进制字符 = 128 位，远大于本规模的生日界。
_ID_CHARS = 32


def _derive(kind: str, *parts: str) -> str:
    """``kind`` 参与哈希，是三个 id 空间不相交的**唯一**依据 —— 不是注释。

    ``kind`` 里的下划线不会与分段混淆：它出现在**第一个**分段，而调用方给的
    分段都是 id/序号，不含这个字面量。真正防歧义的是 :data:`SEP`，见它的
    docstring。
    """
    return sha256_text(SEP.join((kind, *parts)))[:_ID_CHARS]


def job_id(scene_id: str, idempotency_key: str) -> str:
    """内容寻址：同一个 ``(scene, key)`` 永远得到同一个作业 id。

    幂等由**主键**保证，而不是靠「写之前先查一下」—— 与
    ``domain/budget.py::reservation_id`` 是同一条推导，理由也相同。

    ``scene_id`` 参与 id 意味着「同一个键在两个 Scene 下是两个作业」。这是对的：
    §2 说 ``submit_research`` 引用的是一组「Scene + 一种输入」，Scene 是这部份
    身份的一部分，而不是请求的一个字段。
    """
    return _derive("job", scene_id, idempotency_key)


def work_item_id(job_id: str, ordinal: int) -> str:
    """``ordinal`` 是它在作业内的**稳定顺序**（从 0 起），不是时间戳。

    用序号而不是内容的哈希：同一个作业重复编译出同一批 work item 时，id 必须
    逐字相同，否则重复编译会被读成「多了一批新工作」。
    """
    return _derive("work_item", job_id, str(ordinal))


def attempt_id(work_item_id: str, ordinal: int) -> str:
    return _derive("attempt", work_item_id, str(ordinal))


def request_fingerprint(*, scene_id: str, input_kind: JobInputKind, input_ref: str) -> str:
    """一次受理请求的**内容**散列 —— ``admit(request_sha256=...)`` 的唯一生产者。

    它答的是「这两个请求是不是同一个请求」。幂等键答的是另一件事（「这是不是
    同一次意图」），而两者必须分开：同一个键配**不同的内容**是一次冲突，而
    「内容变了没有」只能由这个值回答。所以它只能有一个生产者 —— 两处各算一份，
    漂移的那天冲突检查开始漏报，而漏报的形态是「同一个键受理了两次」：报告上
    只显示研究做了两遍，没有任何一处会红。

    ``input_kind`` 进散列：同一个场景、同一个 ``input_ref``、两种输入种类是
    两个请求（一次「已知账号」与一次「发现范围」可能恰好指向同一个字符串）。

    分隔符用 ``SEP``，理由见 ``domain/util.py``。不加它，``("a", "b:c")`` 与
    ``("a:b", "c")`` 会撞成同一个散列 —— 而那次撞车把「不同」读成「相同」：
    一次本该报冲突的受理被当成重发吞掉，调用方以为自己在追加工作，实际什么
    都没发生。

    参数一律关键字化：三个都是字符串，位置传参会让「场景」与「输入引用」在
    调用点上长得一样，而这两样搞反了不会报错，只会给一个指向别处的作业算出
    一个正确的散列。
    """
    return sha256_text(SEP.join(("research_request", scene_id, input_kind.value, input_ref)))


@dataclass(frozen=True)
class ResearchJob:
    """一次被受理的研究请求 —— §2 ``submit_research`` 的持久记录。

    ``request_sha256`` 是**唯一**的冲突判据：同一个幂等键配了不同的请求时报错。
    逐字段比较做不到这件事，因为「哪些字段算输入」是工具接口的知识，不是存储层
    的知识；一个散列把它们一起覆盖，并且逼调用方先把请求规范化。

    成功受理的作业永远从 ``QUEUED`` 起步 —— 包括一个立刻就该阻塞的作业。受理
    时判断得了的东西（缺 key、缺额度）写在 ``reason`` 里由调用方随后 ``block``，
    因为「受理成功但立刻阻塞」与「受理失败」在调用方那里处置不同：前者已经占了
    一个 id，重试同一个键会拿回同一个作业。
    """

    job_id: str
    scene_id: str
    input_kind: JobInputKind
    input_ref: str
    idempotency_key: str
    request_sha256: str
    state: JobState
    reason: str
    created_at: str
    updated_at: str

    @property
    def admits_dispatch(self) -> bool:
        """这个作业现在放不放行**新**的派发。

        ``PAUSED`` 为假而 ``accepts_results`` 为真 —— 这一对属性就是 §6 那句
        「Pause normally prevents new dispatch and lets accepted in-flight results
        settle」的全部内容。两个问题分开回答，因为它们是两条不同的规则。
        """
        return self.state is JobState.QUEUED

    @property
    def accepts_results(self) -> bool:
        """这个作业现在受不受理结果。取消之后才到的 findings 是陈旧的。"""
        return self.state is not JobState.CANCELLED


@dataclass(frozen=True)
class WorkItem:
    """一个可执行单元 —— §3 ``get_assignment`` 的主语。

    ``subject_ref`` 是这件事**针对谁**（一个账号 id、一个发现范围里的一段），
    不是它的内容。内容由 ``ExecutionRef`` 指向的 Quay 任务携带 —— 于是
    「同一批输入编译两次得到什么」这个问题只需要一个答案：同样的 work item。

    ``stop_reason`` 为 ``None`` 表示还没停。**没有单独的「进行中」状态**：
    「有没有人在跑」由租约回答（``attempts``），把它也编码成一个字段会让
    「已派发但没人领」和「有人正在跑」这两件事有两个真相来源。

    ``stop_reason`` 非空 ⟺ 有一条 :class:`Settlement`。两半在同一个写事务里
    产生（``JobStore.settle``），所以「停了吗」与「产出了什么」不可能分家 ——
    一条停止理由没有对应的产出，是读的那一侧会拒绝的状态，不是一种正常情形。
    """

    work_item_id: str
    job_id: str
    ordinal: int
    subject_ref: str
    stop_reason: StopReason | None
    created_at: str
    updated_at: str

    @property
    def is_stopped(self) -> bool:
        """停下来的 work item 不再出租 —— 停止理由已经记下了，重跑会覆盖它。"""
        return self.stop_reason is not None


@dataclass(frozen=True)
class ExecutionRef:
    """``WorkItem`` → Quay 任务的映射。

    ``docs/DATA_MODEL.md`` 写的是「Quay task/run/gate」，本记录**只有 task**。
    这是刻意的：只有 task 有生产者（派发时那一次 CAS 写入），而 run 与 gate
    今天还没有——``quay adjudicate`` 是只读的、永远退 0、不碰状态（§2.9 实测），
    worker driver 的 ``run_id`` 只出现在运行期遥测里。先声明两列没人写的字段，
    会让「这个映射完整吗」有一个永远为空的答案。

    映射是**写一次**的：主键就是 ``work_item_id``。两个派发者抢同一个 work item
    时，第二个会撞在主键上而不是悄悄建出第二个 Quay 任务 —— 那正是 §8B 说的
    「no duplicate expensive work」。
    """

    work_item_id: str
    quay_task_id: str
    mapped_at: str


@dataclass(frozen=True)
class WorkItemSnapshot:
    """一件工作**被发出去时是什么样** —— 针对谁，按哪一份规则（§6）。

    两半必须在同一条记录里，因为它们回答的是同一个问题：「这一次派发到底派了
    什么」。分开成两条记录（一条说身份、一条说规则）就有两个真相来源，而它们
    不一致时没有一处会红 —— 读到的是「这个人的规则是那一份」这种谁也说不清的话。

    ## 为什么它不叫 ``GuidancePin`` 了

    它曾经只装 ``scene_version`` 一个事实，名字因此叫「钉住的指引」。现在它装的
    是整个任务包的两半，其中**身份那一半才是被报上来的那个缺口**（worker 拿到
    ``kent-003``，拿不到 Example School One）。名字跟着内容改，是因为一个叫
    「guidance pin」的记录里装着主体的身份，会让下一个读它的人先去别处找身份。

    ## ``subject`` 为什么可以是 ``None``

    这不是「解析失败」，是一个**真实的历史状态**：身份快照这个机制出现之前编译的
    work item 在盘上只有规则的版本。它们的 ``scene_version`` 是真的（那批工作确实
    按某一份规则跑过），身份却没有记录过。编一个身份出来是假话；让整张表读不出来
    则会让一次与此无关的读操作不可用。所以这一格可以缺席，由读的那一侧决定怎么
    面对缺席 —— ``get_assignment`` 选择拒绝（见 ``adapters/mcp_server/tools.py``）。

    ## ``scene_version``

    是 ``ProfileBundle.scene_version``（装载器对 profile 目录里每一个文件的字节
    算出的摘要，见 ``adapters/profile_loader.py``）。本层把它当**不透明的字符串**：
    不解析、不比较大小、不假设它是某种格式。存储层也一样 —— 它不认识「版本」这个
    概念，只认识一个它原样收下、原样吐回的字符串。

    **内容另存。** 版本号是这批字节的指称对象，而指称对象本身要读得回来
    （§6 的 "retain their pinned guidance" 不是「保留一个号码」）。字节存在
    与版本号同名的那张表里，写它的入口只有 ``compile_work_items`` 的写事务。

    ``pinned_at`` 有读者：它答「规则是在这一刻冻住的」，于是「profile 在那之后被
    改过」这件事在回执里有一个时间可以对照。这与 ``ExecutionRef.mapped_at`` 是
    同一种字段。
    """

    work_item_id: str
    scene_version: str
    pinned_at: str
    #: ``None`` = 这份快照是在身份机制出现之前写下的（见类 docstring）。
    subject: SubjectDossier | None


@dataclass(frozen=True)
class JobAttempt:
    """一次租约 —— §6 的「attempt/lease protection」，Quay 那侧不存在的东西。

    ``holder`` 是**谁**在跑，必须由调用方给出（worker 的稳定标识）。Quay 那侧
    没有 attempt，所以这里没有「租约号」可以继承；holder 是我们唯一的归属依据，
    而 §6 要求「real process/task ownership」可核。

    ``note`` 在 ``RELEASED`` 时是交还理由（必填非空），其余为空。交还理由必填
    的理由与 ``BudgetLedger.refund`` 相同：让「我不知道为什么没结果，先把它放掉」
    在代码里写不出来，比在文档里禁止它可靠。
    """

    attempt_id: str
    work_item_id: str
    ordinal: int
    holder: str
    state: AttemptState
    leased_at: str
    settled_at: str | None
    note: str

    @property
    def is_live(self) -> bool:
        """活租约 —— 一个 work item 同时最多一条，这条判据是排他性的载体。"""
        return self.state is AttemptState.LEASED


@dataclass(frozen=True)
class Settlement:
    """一次结算 —— §3 ``submit_findings`` 落进存储的那一条记录。

    ``attempt`` 与 ``findings`` 在这条记录里是**同一件事的两半**：``settle``
    在同一个写事务里既定局那条租约、又写下 work item 的停止理由、又落盘这份
    产出。所以「一个停下来的 work item 必有产出」在结构上成立，不需要任何人
    记得去维持它 —— 一个停了却没有产出的 work item 会在读的那一侧被拒绝，
    见 ``ports/jobs.py``。

    ## ``replayed`` 回答的是「这次调用真的写了吗」

    §6 要求「Same request/result retries are idempotent」。幂等有两种写法：
    静默返回同一条，或者**说出来**「这一份已经在盘上，我没有再写一遍」。
    取后者，因为前者与「又写了一遍完全一样的内容」在回执里不可区分 ——
    而调用方据此判断要不要重试，或者要不要去看为什么服务端说「又收了一次」。
    """

    attempt: JobAttempt
    findings: Findings
    recorded_at: str
    replayed: bool

    @property
    def content_sha256(self) -> str:
        """从 ``findings`` 现算，不另存一列。

        另存一列就是同一件事的两个真相来源，而它们不一致时**没有一处会红**：
        落盘的是散列、读回来的是那份产出，两者对不上时报告照常打印。
        """
        return findings_sha256(self.findings)


__all__ = [
    "job_id",
    "work_item_id",
    "attempt_id",
    "request_fingerprint",
    "ResearchJob",
    "WorkItem",
    "ExecutionRef",
    "WorkItemSnapshot",
    "JobAttempt",
    "Settlement",
]

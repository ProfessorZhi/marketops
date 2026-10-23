"""``JobStore`` 抽象：持久作业记录 —— 受理、编译、派发、租约、受理结果。

## 它守的是哪一条

§6 把持久化的要求写成一句话：**「Store admission and dispatch intent
transactionally, reconcile dispatch task mapping, use attempt/lease protection, and
commit findings through one controlled writer.」** 这句话里有四个动作，所以这个
接口上有四组动词，而不是一个笼统的 ``save()``。

它守的失败只有一种，但长着五张脸，**每一张都不报错**：

1. 两个控制端同时受理同一个请求 —— 两次研究、两份账单；
2. 同一批输入被编译两次 —— 报告上显示「候选多了一倍」；
3. 两个派发者给同一个 work item 建了两个 派发任务 —— §8B 逐字禁止的
   duplicate expensive work；
4. 两个 worker 领到同一个 work item —— 同上，而且两边都会以为自己是对的；
5. 一个被取消的作业还在收 findings —— 报告里多出一批没人授权的工作。

五条各有一组判据，见 ``tests/test_job_contract.py``。

## 为什么端口上**没有**这些东西

* **没有 ``result_revision``**。理由见 ``domain/job.py``：陈旧性由「哪一条租约是
  活的」回答，一个并存的计数器会有两个真相来源。
* **没有 ``lease_expires_at`` / ``reap``**。租约不按时钟自动失效 —— 猜错「那个
  worker 死了」的代价正是第 3、4 条失败。要收回，得走 ``release(reason=...)``
  并写明理由。这与 ``BudgetLedger`` 不自动退款是同一条纪律，不是巧合。
* **没有 ``delete``**。作业是**账**：一次受理在报告里被引用之后，删掉它等于让
  报告指向一个不存在的受理。取消是一个状态（``CANCELLED``），不是一个删除。
* **没有 ``list_jobs``**。今天没有任何一处需要「把作业列出来」——``get_job``
  收的是 id。一个只被测试用到的列举入口会让人以为有一个作业队列在那里。
* **没有按 id 读一条 attempt 的入口**。一条尝试只从它所属的 work item 出发被
  列举（``attempts``）：它不是一个独立存在的东西。``release`` / ``settle`` 收
  attempt id，但内部查得到父记录，不需要调用方先做一次读。
* **没有按 attempt id 读 findings 的入口**。产出挂在 work item 上
  （``settlement``），与上一条同一条理由 —— 而且这条是承重的：产出是
  「这个 work item 的结果」，不是「某次尝试的结果」。有了按 attempt 取的入口，
  一个被交还过的 attempt 就会成为一条可以单独读出来的产出候选，而它其实
  什么都没产。

## 序号一律从 0 起

``WorkItem.ordinal`` 与 ``JobAttempt.ordinal`` 用同一个基准。它们是同一种东西
（同一个父记录下的第 n 个），两套基准（一个 0 起一个 1 起）在代码里迟早会有一处
差一 —— 而差一在这四处（``work_item_id`` / ``attempt_id`` / 稳定排序 / 下一号）
各自都会安静地给出一个「看起来对」的结果。

## 两个实现，同一份判据

``adapters/sqlite/jobs.py``（跨进程正确，靠 ``BEGIN IMMEDIATE``）与
``adapters/memory.py``（单进程测试替身）。B3 立的判据是**端口在第二个实现出现时
才提取**，这里两个都跑 ``tests/test_job_contract.py``。

本模块**不得**出现任何存储引擎或厂商名（``sqlite3`` / ``redis`` / 任何执行器实现 …），
由 ``tests/test_ports_are_contracts_only.py`` 执行。特别是「执行器」这个词：映射的
内容是一个字符串 id，端口不需要知道它是谁给的。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from datetime import datetime

from ..domain.continuation import (
    BlockedOn,
    Continuation,
    ContinuationClaim,
    ContinuationState,
)
from ..domain.enums import JobInputKind, JobState, StopReason
from ..domain.findings import Findings, findings_sha256
from ..domain.job import (
    ExecutionRef,
    JobAttempt,
    ResearchJob,
    Settlement,
    WorkItem,
    WorkItemSnapshot,
)
from ..domain.profile import SceneFiles
from ..domain.subject import SubjectDossier
from ..domain.util import SEP


def settlement_outcome(stop_reason: StopReason, findings: Findings) -> str:
    """一次结算的**结局指纹**，两个实现比对重发时用同一个 —— 所以它住在这里。

    **两半都必须进。** 只比停止理由，一份换了产出的重发会被当成幂等接受 ——
    于是「同一个 attempt 结了两次，产出的东西不一样」这件事在存储层看不出来，
    而它正是 §6 那句「stale attempts cannot overwrite later versions」要挡的。
    只比产出，换一个停止理由的重发会被当成冲突 —— 而 §6 要求它幂等。
    """
    return f"{stop_reason.value}{SEP}{findings_sha256(findings)}"


def repin_refusal(pin: WorkItemSnapshot | None, scene_version: str, job_id: str) -> str:
    """重新编译一批**已有的**工作单元时，钉住的版本必须还是那一个。

    返回理由（空串 = 放行）而不是抛，与 :func:`settlement_outcome` 同一条理由：
    这是两个实现共用的**同一份判定**，住在这里才不会各写一遍 —— 而各写一遍的
    后果不是文案不一致，是两份实现放行/拒绝的**条件**先分家，然后在某一个实现
    上静默地把一批工作单元重钉到别的规则上。

    ## 为什么不是「重钉成新的那个版本」

    §6 逐字：Running tasks retain their pinned guidance。这批工作单元可能已经
    派发、已经有人在跑、甚至已经结算 —— 重钉会把它们的历史改成一份它们没跑过
    的规则，而**没有任何一处会因此报错**。

    ## 为什么也不是「静默返回已有那一批」

    返回的话调用方会以为自己刚写下的那份规则生效了，而盘上钉的还是旧的那一份。
    两条路径在回执里长得一样，区别只在于「按哪一份规则跑」—— 正是这个字段存在
    的理由。要按现在这份规则跑，是**另一个作业**。

    ## 没有钉（``None``）也是一种拒绝

    这个机制出现之前编译的工作单元在盘上就是没有钉。它们说不出自己按哪一份规则
    在跑，所以既不能拿它们继续，也不能就地补一个版本上去（那会让它们看起来像是
    这一次编译出来的）。
    """
    if pin is None:
        return (
            f"作业 {job_id} 的 work item 没有钉住的 Scene 版本 —— 它们是在钉住这个"
            "机制出现之前编译的。\n"
            "§6 逐字：Running tasks retain their pinned guidance。这一次编译说不出"
            "它们按哪一份规则在跑，所以既不能拿它们继续，也不能就地补一个版本上去 ——"
            "那会让它们看起来像是**这一次**编译出来的。要按现在这份规则跑，是另一个作业。"
        )
    if pin.scene_version == scene_version:
        return ""
    return (
        f"作业 {job_id} 的 work item 钉在 Scene 版本 {pin.scene_version}"
        f"（{pin.pinned_at}），而现在生效的是 {scene_version}。\n"
        "§6 逐字：Running tasks retain their pinned guidance —— 所以这里既不重新钉"
        "（那些工作单元可能已经派发、已经有人在跑、甚至已经结算，重钉会把它们的历史"
        "改成一份它们没跑过的规则，而没有任何一处会因此报错），也不静默返回"
        "（返回的话调用方会以为刚才那份规则生效了）。要按现在这份规则跑，是**另一个"
        "作业**。"
    )


def resnapshot_refusal(
    stored: Sequence[str | None], subjects: Sequence[SubjectDossier], job_id: str
) -> str:
    """重新编译一批**已有的**工作单元时，身份必须还是那一份。

    与 :func:`repin_refusal` 是同一个形状、同一条理由，只是主语从「按哪一份规则」
    换成了「针对谁」：重新编译是一个**幂等**动作，它只能在输入逐位相同时放行。
    返回理由（空串 = 放行）而不是抛，理由同上 —— 两个实现共用同一份判定。

    ## 为什么改一个名字也要拒绝

    因为编译是写一次的：盘上那份快照不会跟着变。放行的话，调用方以为自己新给的
    名字生效了，而 worker 拿到的是旧的那一份 —— 两条路径在回执里长得一样，
    区别只在于「说的是谁」。要换一个主体，是**另一个作业**（它的
    ``request_fingerprint`` 会不一样，因为输入清单是另一个文件或另一份内容）。

    ``stored[i]`` 是第 ``i`` 个工作单元落盘时的身份载荷（``None`` = 那一行在身份
    快照出现之前就编译了）。**``None`` 也是一种拒绝**：那些行没有记录过针对谁，
    就地补一个上去会让它们看起来像是这一次编译出来的。
    """
    if len(stored) != len(subjects):
        return (
            f"作业 {job_id} 已有 {len(stored)} 个工作单元，这一次给了 {len(subjects)} 个主体。\n"
            "同一份输入必须编译出同一批，否则「多了一倍候选」与「多了一批工作」"
            "在报告上不可区分。要改工作集，是另一个作业。"
        )
    for ordinal, (raw, subject) in enumerate(zip(stored, subjects, strict=True)):
        if raw is None:
            return (
                f"作业 {job_id} 的第 {ordinal} 个工作单元是在身份快照出现之前编译的，"
                "它没有记录过针对谁。\n"
                "就地补一个身份上去会让它看起来像是**这一次**编译出来的 —— 而它已经"
                "可能被派发、被领走、被结算过，那一段历史说的是另一个包。要按现在这份"
                "身份跑，是另一个作业。"
            )
        before = json.loads(raw)
        after = subject.payload()
        changed = sorted(
            name for name in after if before.get(name) != after[name]
        )
        if changed:
            detail = "、".join(
                f"{name}（{before.get(name)!r} → {after[name]!r}）" for name in changed
            )
            return (
                f"作业 {job_id} 的第 {ordinal} 个工作单元已经按另一份身份冻住了：{detail}。\n"
                "编译是写一次的 —— 盘上那份快照不会跟着变。放行的话你会以为新给的输入"
                "生效了，而 worker 拿到的是旧的那一份，两条路径在回执里长得一样。\n"
                "要换一个主体，是**另一个作业**。"
            )
    return ""


class JobStore(ABC):
    """持久作业记录。返回值类型与 id 的推导都在 ``domain/job.py``。

    参数一律关键字化（除 id 与序号），因为这里每一个都是**语义上**必填的：
    ``admit`` 有五个字符串，位置传参会让「场景」和「输入引用」在调用点上长得
    一样 —— 而那两样搞反了不会报错，只会受理一个指向别处的作业。
    """

    # ------------------------------------------------------------------
    # 受理（§2 submit_research）
    # ------------------------------------------------------------------
    @abstractmethod
    def admit(
        self,
        *,
        scene_id: str,
        input_kind: JobInputKind,
        input_ref: str,
        idempotency_key: str,
        request_sha256: str,
    ) -> ResearchJob:
        """受理一个作业。**幂等**：同一个 ``(scene, key)`` 拿回同一个作业。

        **它在同一个写事务里欠下下一步**：``domain/continuation.py`` 的
        ``intents_for_admission`` 给出的那些行一起落账（见本接口的「续跑意图」一节）。
        幂等重放**不写第二遍** —— 内容寻址 id 让第二次落在同一个主键上，而「同一个事件
        欠下的下一步只有一行」正是「重启之后重新执行的是同一行」的全部依据。

        Raises:
            IdempotencyConflictError: 同一个键配了不同的 ``request_sha256``。
            ValueError: 某个字段为空（含 ``request_sha256`` 不是 64 位小写十六
                进制）。空幂等键会让「重试」与「新请求」不可区分。
        """

    @abstractmethod
    def get(self, job_id: str) -> ResearchJob | None:
        """按 id 取一个作业；不存在时返回 ``None``（不抛）。"""

    @abstractmethod
    def set_state(
        self, job_id: str, state: JobState, *, expected: JobState, reason: str = ""
    ) -> ResearchJob:
        """``control_job`` 的落点：类型化的 pause / resume / cancel。

        ``expected`` 必填，不一致整次拒绝 —— 这是**一次 CAS**，与 执行器 自己的
        ``--expect-status`` 是同一条纪律（§2.9 记着它逐字回的那句
        ``refusing to overwrite (write() aborted, nothing written to disk)``）。
        读到的状态与据此写下的状态必须在同一个写事务里；没有它，两个控制端
        同时 pause / cancel 时最后一个写的赢，而**两边都以为自己成功了**。

        ``reason`` 对 ``PAUSED`` / ``BLOCKED`` / ``CANCELLED`` 必填非空。
        §6 要求「Cancel reports what actually stopped」—— 一个没有理由的取消
        无法回答那句话。``QUEUED``（resume）则要求 ``reason`` 为空：一条
        恢复不出自任何人的判断。

        **它也是停在路上的那些意图的去处**，并且在同一个写事务里完成：

        * ``PAUSED`` —— 这个场景下所有未决的意图记 ``BLOCKED(PAUSE)``，于是
          「暂停」不只是一个新的作业状态，而是**每一条欠着的下一步都知道自己为什么
          不动**。不这样做的话，暂停期间控制器看到的是一批到期的 ``PENDING``，
          每 60 秒领一次、每 60 秒还挡着 —— 账上很好，读起来像「系统在忙」。
        * ``QUEUED``（resume）—— 被 ``PAUSE`` 挡住的重排回 ``PENDING``。``attempts``
          一格不动：``BLOCKED`` 不是一次尝试（见 ``ContinuationState``），暂停一周
          之后再恢复，不该看起来像「重试了 10080 次」。
        * ``CANCELLED`` —— 这个作业欠下的派发意图判 ``DONE``：受理那件事的下一步
          已经是「什么都不做」，继续挂着它会让控制器一遍遍去问一个已取消的作业。

        Raises:
            JobStateError: 作业不存在；``expected`` 不匹配；转移不合法
                （见 ``JOB_TRANSITIONS``）；或理由该有而没有 / 该没有而有。
        """

    # ------------------------------------------------------------------
    # 编译（§2 的输入 → §3 的可执行单元）
    # ------------------------------------------------------------------
    @abstractmethod
    def compile_work_items(
        self, job_id: str, subjects: Sequence[SubjectDossier], *, scene: SceneFiles
    ) -> tuple[WorkItem, ...]:
        """把一个作业的输入编译成可执行单元。**幂等**：同样的清单拿回同样的行。

        一次编译写下三样东西，**在同一个写事务里**（§6：Running tasks retain
        their pinned guidance）：

        * ``work_item`` —— 可执行单元本身；
        * ``work_item_snapshot`` —— 它针对谁（``SubjectDossier``）与它按哪一份
          规则在跑（``scene.version``）；
        * ``scene_snapshot`` —— 那个版本号**指称的字节**，按版本内容寻址（同一个
          版本写第二次是幂等的：它是这批字节的摘要，所以「同号不同内容」不存在）。

        ``subjects`` 的顺序**有意义** —— 它决定 ``ordinal``，因而决定
        ``work_item_id``。重复编译时顺序不同会被读成另一批工作，所以这里比对
        整个序列而不只是集合。

        ## 为什么是一组 ``SubjectDossier`` 而不是一组字符串

        从前它是 ``Sequence[str]``，收的是 ``account_id``。于是 worker 拿到
        ``kent-003`` 之后只能拿这个内部 slug 去检索 —— 而那一次真的花了 9 次
        Serper。身份必须在**受理那一刻**冻下来，因为那是本部署最后一次看得见
        输入文件全貌的时刻。

        **不接 ``str`` 的兼容分支。** 一个「给字符串就现场包成一个只有 id 的
        身份」的入口，会把「这一次编译有没有身份」变成一个调用点可以选的东西 ——
        而那种开关正是「同一份输入、两个结果」的成因。要编译就得拿出身份，
        拿不出身份是 :func:`~marketops.domain.subject.identity_refusal` 的活，
        不是这里的兜底。

        ## 为什么是 ``scene`` 而不是 ``scene_version: str``

        ``SceneFiles`` 里同时住着版本与那批字节（见 ``domain/profile.py``）。
        收一个字符串就够的话，存储层会得到一个**说不出指称对象**的版本号 ——
        那正是收口之前的状态：盘上钉着版本、谁也没法回答那次派发用的是哪一份
        指引。所以这里要的是那个值：**给不出字节，就给不出版本**。忘记传的
        调用点拿到 ``TypeError``，而不是一批「钉在空版本上」的工作单元。

        Raises:
            JobStateError: 作业不存在；作业已取消；这个作业已经有了一批**不同**
                的 work item（同一份输入必须编译出同一批，否则「多了一倍候选」
                与「多了一批工作」不可区分）；已有那一批钉住的版本不是
                ``scene.version``（那些工作单元可能已经派发、已经有人在跑、
                甚至已经结算，重钉会把它们的历史改成一份它们没跑过的规则）；
                或已有那一批的身份摘要与这一次给的对不上 —— **同一个作业、同一
                个序号、换了一个主体**，那是另一个作业，不是在原地改一件工作。
            ValueError: ``subjects`` 为空；某个 subject 的 ``account_id`` 不是
                规范形式；或 ``scene`` 的版本为空。
        """

    @abstractmethod
    def work_items(self, job_id: str) -> tuple[WorkItem, ...]:
        """一个作业的 work item，按 ``ordinal`` 排序。作业不存在时返回空元组。"""

    @abstractmethod
    def get_work_item(self, work_item_id: str) -> WorkItem | None:
        """按 id 取一个 work item；不存在时返回 ``None``。"""

    # ------------------------------------------------------------------
    # 派发：WorkItem → 执行器 task（§6 reconcile dispatch task mapping）
    # ------------------------------------------------------------------
    @abstractmethod
    def map_execution(self, work_item_id: str, *, dispatch_task_id: str) -> ExecutionRef:
        """把 work item 映射到一个已在 执行器 建好的任务。**写一次**。

        映射同一个 ``dispatch_task_id`` 是幂等的（重发拿回同一行）；映射到**另一个**
        task 报错 —— 那意味着同一个 work item 有两个 派发任务，也就是同一件
        昂贵的事被派发了两遍，而两边都会正常跑完。

        作业必须处于放行派发的状态，否则报错 —— 这是 §6 那句「Pause normally
        prevents new dispatch」的可执行形式。**派发就是这里**，没有第二个入口：
        §6 的另一句「The controller cannot substitute an outer-shell worker
        launch for 执行器 dispatch」由 ``lease`` 反过来守（见下）。

        Raises:
            JobStateError: work item 不存在；作业不放行派发；或已经映射到别的 task。
            ValueError: ``dispatch_task_id`` 为空。
        """

    @abstractmethod
    def execution_ref(self, work_item_id: str) -> ExecutionRef | None:
        """work item 的 派发映射；没有时返回 ``None``。"""

    @abstractmethod
    def work_item_snapshot(self, work_item_id: str) -> WorkItemSnapshot | None:
        """work item 编译时冻住的那个包：针对谁 + 按哪一份规则；没有时返回 ``None``。

        **没有入口可以单独写它。** 冻住只发生在 ``compile_work_items`` 的写事务
        里 —— 一个「事后补一份身份上去」的入口，正是「那些工作单元看起来像是
        这一次编译出来的」的成因。

        ``None`` 是一个**真实的、要处理**的状态，不是「查不到」：这个机制出现
        之前编译的 work item 在盘上就是没有快照。读的那一侧必须为它选一个处置，
        而不是把它读成一个缺省版本或一个编出来的身份 —— 那会让「这一件工作派了
        什么出去」有一个看起来有答案的空答案。

        记录里的 ``subject`` 也可能单独为 ``None``（身份机制之前、但钉住机制
        之后的那些行）。那是第二个缺席状态，读的那一侧同样要面对它。
        """

    @abstractmethod
    def scene_files(self, scene_version: str) -> SceneFiles | None:
        """一个 Scene 版本**指称的那批字节**；没有时返回 ``None``。

        **没有入口可以单独写它。** 内容与快照在 ``compile_work_items`` 的同一个
        写事务里落盘：分开写的话，「版本在、内容不在」这个窗口是真实的，而那时
        回执手里有一个说不出指称对象的号码。

        ``None`` 要读成「本部署没有留下这一份内容」，**不是**「这一份内容与盘上
        现在的那一份相同」—— 后者是一个在报告上看起来完全正常的错误答案。
        """

    # ------------------------------------------------------------------
    # 租约（§6 attempt/lease protection）
    # ------------------------------------------------------------------
    @abstractmethod
    def lease(self, work_item_id: str, *, holder: str) -> JobAttempt:
        """把 work item 租给 ``holder``。**幂等**：同一个 holder 重复要，拿回同一条。

        Raises:
            LeaseError: 已经有**别的** holder 持着；这个 work item 已经停止；
                或者它**还没有派发到执行器**。最后一种是刻意的：没有
                ``ExecutionRef`` 就没有租约，于是「绕过派发 直接开一个 worker」
                在代码里写不出来（§6 逐字：the controller cannot substitute an
                outer-shell worker launch for 执行器 dispatch）。
            JobStateError: work item 不存在；作业不存在或已取消。
            ValueError: ``holder`` 为空。
        """

    @abstractmethod
    def release(self, attempt_id: str, *, reason: str) -> JobAttempt:
        """交还一条租约。``reason`` 必填非空。

        **这是唯一的收回路径**，因为租约不按时钟自动失效。做成一「不存在的
        超时分支」而不是「存在的超时参数」，理由与 ``BudgetLedger`` 相同：默认
        行为（什么都不做）恰好就是正确行为，而要写错得显式写出理由。

        重发同一个理由幂等；换一个理由报错（一条 attempt 的交还理由是一次性
        写下的）。

        Raises:
            JobStateError: attempt 不存在；或它不是活租约（已结算 / 已交还）；
                或已经以**别的**理由交还过。
            ValueError: ``reason`` 为空。
        """

    @abstractmethod
    def settle(
        self, attempt_id: str, *, stop_reason: StopReason, findings: Findings
    ) -> Settlement:
        """受理一条租约的**结果**。§3 ``submit_findings`` 的落点，§6 那句
        「commit findings through one controlled writer」就是这一个方法。

        三件事在**同一个写事务**里：``attempt`` 结算、work item 写下停止理由、
        这份产出落盘。分开写会让「这个 work item 停了吗」与「它产出了什么」有
        两个互相矛盾的答案，而两个答案在报告里都读得通。

        ## 这是**唯一**的结果写者，而且 ``findings`` 是必填的

        ``release`` 是不能写：交还一条租约说的是「我不知道为什么没结果，先把它
        放掉」，它**不**给 work item 写停止理由。所以「有没有结果」这条边界由
        ``release`` 与 ``settle`` 两个动词分掉，而不是靠一个可选参数 ——
        一个 ``findings=None`` 的缺省会让「跑过了、什么都没找到」与「根本没报」
        落成同一条记录。

        ``Findings()`` 是合法的，而且正是「跑过了、什么都没找到」那种诚实结果：
        报告说 ``stored: 0`` 在那时才是真的，因为那个 0 背后有一次真实的运行。

        ## 幂等

        重发**同一份**产出加同一个 ``stop_reason`` 幂等，回执里
        ``Settlement.replayed`` 为真（§6：Same request/result retries are
        idempotent）。换一个理由、或换一份产出，都报错 —— 同一条租约的结局是
        一次性写下的，允许改写它，「重发」就成了一条改结论的旁路。

        ## 它也欠下下一步，而且**重放不欠**

        一次**新的**结算在同一个写事务里落下 ``intents_for_settlement`` 的那些行：
        这一件刚把新证据写进池子，有关的主体因此欠一次复核。**被拒绝的结算不留行**
        —— 一次因为作业已取消而被拒的 ``settle`` 根本不是一个业务事件，它欠不下任何
        东西；把意图写在事务之外的话，盘上会多出一条为「一件没发生的事」而存在的
        欠账，而报告上只显示「多了一次复核」。这是本接口原子性那条判据的执行器，见
        ``tests/test_sqlite_continuation.py``。

        幂等的**重放**不写下第二遍（``business_key`` 是 ``work_item_id``，不是
        attempt id —— 用 attempt id 会让同一次结算的重发算成两个事件）。这里与受理
        那条是同一个形状：同一个业务事件欠下的下一步只有一行。

        Raises:
            JobStateError: attempt 不存在；不是活租约；已经以别的产出结算过；
                或者**作业已取消** —— 取消之后到的 findings 是陈旧的，受理它
                等于让报告里多出一批没人授权的工作。
        """

    @abstractmethod
    def attempts(self, work_item_id: str) -> tuple[JobAttempt, ...]:
        """一个 work item 的全部尝试，按 ``ordinal`` 排序。"""

    @abstractmethod
    def settlement(self, work_item_id: str) -> Settlement | None:
        """一个 work item 的结算；还没停时返回 ``None``。

        **单数**，因为一个 work item 最多有一条结算：``settle`` 只接受活租约，
        而 ``lease`` 不给已经停下的 work item 发新租约。所以这不是「今天恰好
        只有一条」，是一个结构性的事实 —— 它让 ``get_job`` 不必回答
        「多条结算里哪一条算数」，那个问题没有诚实的答案。

        从 work item 出发而不是从 attempt id 出发，与 ``attempts`` 同一条理由：
        一条尝试不是独立存在的东西（见模块 docstring）。
        """

    # ------------------------------------------------------------------
    # 续跑意图（§10 continuation/outbox）
    # ------------------------------------------------------------------
    #: 上面那三组动词**欠下**的东西。它为什么不另开一个端口，是这一节唯一要
    #: 说清的事：**受理与结算要落下的那一行，必须和它们自己在同一个写事务里。**
    #:
    #: 分家的两种形态都不可接受。写在事务**之前**：一次被拒绝的结算会留下一行
    #: 「欠着一次复核」——而那次结算根本没发生，于是控制器会为一件不存在的事去
    #: 复核，报告上只显示「多了一次复核」。写在事务**之后**：进程在两次写之间死掉
    #: 时，事件在盘上而意图不在，于是「跑完之后还有下一批」这件事**永久丢失**，
    #: 而它正是 R2 那一行「关掉聊天仍续下一批」的全部内容。
    #:
    #: 所以这个端口从「持久作业记录」变成「持久**业务事件**记录」：事件与它欠下的
    #: 下一步是同一件事的两半。写只有那几处（``admit`` / ``settle`` /
    #: ``set_state``，以及 ``ReviewCaseStore.record_resolution`` —— 见下），而它们各自
    #: 要欠什么由 ``domain/continuation.py`` 的三个 ``intents_for_*`` 决定 ——
    #: **策略在领域，事务在实现**，adapter 不发明规则。
    #:
    #: ## 第四个产出者在**另一个端口**上，而这不是破例
    #:
    #: 一次裁决落账要**同时**欠下一次对受影响对象的重评（架构 §9 逐字：「裁决后自动
    #: 重评受影响对象」），而裁决那一对写入（``review_case`` 与 ``human_resolution``）
    #: 是 ``ReviewCaseStore`` 的事。所以那一行由 ``ports/review.py::record_resolution``
    #: 在**它自己的**写事务里写进这张表 —— 与上面两段同一条判据的两个方向：写在事务之前
    #: 会为一件被拒的裁决欠账，写在之后会在一次崩溃里永久丢掉欠账。
    #:
    #: 落点仍然是「与事件同生共死」，变的只是**哪一个事务**：主存的两张表在同一个库里
    #: （``settings.database_path``），所以那一个 ``BEGIN IMMEDIATE`` 同时盖住两半。
    #: 这条路径没有在这里开一个动词，正因为「裁决要欠什么」是领域的知识，而
    #: 「怎么写进那个事务」是实现的 —— 与 ``admit`` / ``settle`` 逐字同一分工。

    @abstractmethod
    def continuations(
        self,
        *,
        scene_ids: Sequence[str],
        states: Iterable[ContinuationState] | None = None,
    ) -> tuple[Continuation, ...]:
        """若干场景的续跑意图，**按 ``due_at`` 升序、同刻按 id**。

        ``states`` 为 ``None`` 表示不筛。顺序是契约的一部分：控制器每一轮从最该
        做的那一件开始，而一个由存储层随便给的顺序会让两次 tick 在**上限截断**
        之下做出不同的选择 —— 报告上只会显示「这一轮做了别的几件」。

        ``scene_ids`` 为空元组时返回空元组（**不是一个空动作**：调用方说的就是
        「这些场景」，而一个都不给就是没有场景），而不是「全部场景」——「不给」
        与「给空」在别处已经是一对必须分开的东西（见 ``split_by_aspect``）。

        终态的行走也**可能**出现在结果里（``states=None`` 时），但控制器的每一次
        调用都带着显式的 ``states`` —— 一条只增不改的执行记录会无界增长，而
        「这一轮要做哪些」从来不需要那些。
        """
        raise NotImplementedError

    @abstractmethod
    def continuation(self, continuation_id: str) -> Continuation | None:
        """按 id 读一条；不存在时返回 ``None``（``None`` 是「没有」，不是读失败）。"""
        raise NotImplementedError

    @abstractmethod
    def continuation_counts(self, *, scene_ids: Sequence[str]) -> dict[ContinuationState, int]:
        """若干场景的续跑意图**按状态计数**。

        单独一个方法的理由只有一条：``ControllerState`` 里 ``COMPLETE`` 与
        ``NO_WORK`` 的差别是**历史**（「做完过」对「从来没记过」），而那一位只能从
        终态的行读出来 —— 把终态的行整批读回来只为一个布尔，会让一次 tick 的代价
        随这个市场做过多少件事增长。

        五格**恒在**（没有的状态计数为 0）。缺格与 0 是两件事：报告里
        「这一格没有」与「没人知道这一格」读起来一样，而后者会让
        ``classify`` 的输入少一格。
        """
        raise NotImplementedError

    @abstractmethod
    def claim_continuation(
        self, continuation_id: str, *, holder: str, epoch: int, now: datetime
    ) -> ContinuationClaim:
        """把一条意图领走 —— **这是一个 CAS**，与 ``MaintenanceStore.claim`` 同形。

        可领的条件是**三件一起**：那一行存在、它还在 ``{PENDING, BLOCKED}``、
        并且 ``due_at <= now``。

        ## 为什么连 ``BLOCKED`` 也能领

        因为「挡它的那件事还在不在」这个问题，只有**走一遍**才答得出来：执行器
        缺位的部署补上执行器之后，盘上没有任何一个事件会说「现在可以了」——
        那不是一次业务事件，是一次接线。所以挡住的行按一个下界（``BLOCKED_BACKOFF``）
        排进队列，被领起来**重新问一遍**；还挡着就再记一次 ``BLOCKED``（``attempts``
        不动，见 ``ContinuationState``）。

        领起来**不消耗** attempts：``attempts`` 只数真的试过并失败了的次数。

        ## ``epoch`` 为什么一起写进那一行

        见 ``domain/continuation.py::Continuation``。一句话：一次崩溃重启的控制器的
        ``holder`` 可能与上一次逐字相同，于是「上一代留下的 ``CLAIMED``」与「我自己
        刚领的」不可区分 —— ``epoch`` 是让陈旧**可判**的那一格。

        Returns:
            :class:`ContinuationClaim`。``won`` 为假时 ``continuation`` 是**盘上
            那一行**（可能是别人刚领走的），不是调用方以为的那一行。丢失一次认领
            不是失败，是「别人在做」—— 报告里两者必须能分开（见
            ``application/maintenance.py`` 的三段账）。

        Raises:
            ValueError: ``holder`` 为空，或 ``epoch`` 不是正整数。
        """
        raise NotImplementedError

    @abstractmethod
    def finish_continuation(
        self,
        continuation_id: str,
        *,
        state: ContinuationState,
        holder: str,
        epoch: int,
        now: datetime,
        checkpoint: str = "",
        reason: str = "",
        blocked_on: BlockedOn | None = None,
    ) -> Continuation:
        """把一条 ``CLAIMED`` 的意图推到下一个状态。**一次写事务，带 CAS。**

        CAS 的两格是 ``(holder, epoch)``：一次换代之后才回来的旧控制器**写不进**
        任何一行 —— 它以为自己还在做的那一件，盘上已经归别人了。没有这两格，
        一个卡了十分钟的旧控制器回来时会把它那一代的结果盖在别人的上面，而两边
        都以为自己成功了。

        ``state`` 只能是 ``CLAIMED`` 的出边之一（见 ``CONTINUATION_TRANSITIONS``），
        外加一条不是迁移的**原地写**：``state == CLAIMED`` 时只更新 ``checkpoint``
        与 ``reason`` —— 一次长跑的执行器要能报出进度而不必先离开再回来。

        ``checkpoint`` 为 ``""`` 时**保留原来那一格**，不是清空：「走到哪儿了」不是一个
        会被清掉的东西，而一次只报 ``reason`` 的调用不该顺手把进度抹掉。要改它就给新值。

        ## ``state=FAILED`` 说的是**这一次**失败了，落到盘上是什么由次数决定

        存储层把 ``attempts`` 加一，然后由 ``domain/continuation.py::after_failure``
        决定落点：没过 ``MAX_ATTEMPTS`` 就退回 ``PENDING``（并按 ``retry_delay`` 排
        下一次），过了才是终态 ``FAILED``。所以**调用方报 ``FAILED``，拿回来的可能是一个
        ``PENDING`` 的行** —— 那不是纠正，是两件不同的事：

        * 执行器报的是「**这一次**失败了」；
        * 盘上记的是「**这件事**现在到哪儿了」。

        合成一个词会让「试过三次、要人看一眼」与「刚挂了第一次、等 60 秒再试」在报告里
        长得一样，而它们的处置完全不同。

        ``attempts`` 的推进**由存储层算**，不由调用方给 —— 让调用方传一个数，两个调用点
        迟早会对「什么算一次尝试」给出两个答案，而漂移的形态是「这一件试过几次」在报告上
        比实际少。``BLOCKED`` 与 ``DONE`` 都不加：前者不是一次尝试，后者不是一次失败。

        Raises:
            ContinuationError: 那一行不存在、不在 ``CLAIMED``、``holder``/``epoch``
                对不上、或这条迁移不合法。
            ValueError: ``state`` 为 ``BLOCKED`` 而 ``blocked_on`` 为空（或反之），
                或者 ``state`` 为 ``FAILED`` 而 ``reason`` 为空 ——
                一次失败说不出原因时，报告里它只能是「没做成」。
        """
        raise NotImplementedError

"""研究件在外部执行器那里的终态 —— **权威在本仓**。

## 这一条修的是哪一种不一致

外部那套（Quay）判一件工作「做完了没有」，问的是**软件工程**的问题：worker 的
产出有没有合进 ``develop``、有没有留下 worktree（``computeLandingState``：状态是
``done`` **且**查不到 worktree 才算落地）。本仓的研究工作单元不产出提交、不跑
fan-in、也没有 worktree，于是那个判据在它身上**恒为假**：

    2026-09-23 实测（``artifacts/marketops-mvp-quay/.quay/worker-outcome.jsonl``）：
    {"task": "MOPS-11ed121a-688a5fe8", "final_state": "exited-not-landed",
     "failure_reason": "worktree state unreadable (git worktree list failed)"}

而**同一件**工作在那之前 22.7 秒已经结算落库、看板也已经收敛到 ``done``。业务上
它做完了；外部对「有没有落地」那一格的判决**没有对象**。

## 为什么不改外部

改外部的落地语义要动 ``computeLandingState``，那是 Quay 的上游核心。它有一处
formula 上的扩展点（``landedSha``），而用它的前提是**真的有一个提交落在了
``develop`` 上** —— 研究件没有提交，能填进去的只有一个编出来的 sha。§1 逐字禁止
伪造 worktree 与 commit，也禁止改 Quay 上游核心。所以这里不加一个「让判据通过」
的开关，而是**换一个说话算数的主体**：本仓写自己的 execution receipt，报告以它
为准，外部那一格标 **N/A**。

## 完成的判据是这条链，每一环都在库里或看板上

``work_item``（``stop_reason`` 已落库）→ ``attempt``（那条尝试已定局）→
``submit_findings accepted`` + ``settlement persisted`` → ``board_sync``（看板已
收敛到终态）。

第三、第四环在本仓是**同一次写入**（``JobStore.settle`` 在一个写事务里既定局
租约、又写停止理由、又落盘产出），所以它们报成**一个**链接 ``settlement`` ——
拆成两个字段是给同一个事实起两个名字，而两个名字迟早会有一个开始说谎。
链上的环因此是四个，不是五个；这个合并写在 ``chain_note`` 里，读报告的人不必
自己去代码里找答案。

## N/A 不是 FAIL，而这个区别要**写下来**

``code_landing`` 恒为 ``not-applicable``，且带一句点名「那个判据问的是什么问题」
的理由。报成 ``failed`` 会让每一次正常收尾在报告里多一条假失败 —— 与
``needs-human`` 那盏灯同一条理由：假失败亮久了就没人看了。

## 「不会重新派发」由谁保证

**只由看板上那个状态保证**：外部 driver 每一轮扫的是 ``status: ready`` 的任务
（``application/dispatch.py::INITIAL_EXTERNAL_STATUS`` 的 docstring 记着这条实测）。
所以 ``board_sync.synced`` 为真 —— 外面的状态已经是终态 —— 就是「它不会被再派
一次」的全部内容。本模块不重复这个保证，只把 ``synced`` 与它读到的那个状态
如实印出来，让读报告的人能自己核。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..domain.job import ExecutionRef, JobAttempt, Settlement, WorkItem
from ..ports.dispatch import WorkerExitRecord
from .dispatch import BoardSync, board_target

__all__ = [
    "CODE_LANDING_NOT_APPLICABLE",
    "CODE_LANDING_REASON",
    "ExternalVerdict",
    "ResearchTerminal",
    "research_terminal",
]

#: 外部那条落地判据在本仓研究工作单元上的取值。**不是** ``failed``，也不是
#: ``landed`` —— 它是一个第三值：判据**没有对象**。
CODE_LANDING_NOT_APPLICABLE = "not-applicable"

#: 为什么是 N/A。这段话会进回执，读者是**没有本仓**的人（看板前的人、别的
#: agent），所以它要点名那个判据问的是什么问题，否则「不适用」是一句核不动的话。
CODE_LANDING_REASON = (
    "外部那套的落地判据问的是「worker 的产出有没有合进 develop、有没有留下 "
    "worktree」—— 那是软件工程任务的判据。本仓的研究工作单元不产出提交、不跑 "
    "fan-in、也没有 worktree，所以那条判据在它身上**没有对象**：这一格是"
    "「不适用」（N/A），不是 FAIL。一件研究做完没做完，判据是 chain 那几环，"
    "权威在本仓。"
)

#: 第三、第四环为什么合成一个字段。写进回执，理由同上（读者没有本仓）。
CHAIN_NOTE = (
    "链条逐字是 work_item → active attempt → submit_findings accepted → "
    "settlement persisted → board_sync synced to terminal Quay state。中间两环在"
    "本仓是同一次写入（settle 在一个写事务里定局租约、写停止理由、落盘产出），"
    "所以报成一个 settlement 链接 —— 给同一个事实起两个名字，两个名字迟早会有"
    "一个开始说谎。"
)


@dataclass(frozen=True)
class ExternalVerdict:
    """外部执行器对**同一件事**的判决：读回来的原文，加一句适用性判断。

    ## 原文一格都不删

    ``final_state`` / ``reason`` 是外部自己的词与自己的理由，逐字带出来。本仓
    **不翻译**它们：两套语义不是同一个，折成一个共同域只会让两边的取值都变得
    不可信。报告把两边都印出来，读的人自己判 —— 这比一个由本仓代劳的判决诚实。

    ## ``records`` 是这一格最容易被忽略的那个数字

    ``final_state`` 为 ``None`` 而 ``records`` 为 0 说的是「外面一次都没记过这件
    事」；``final_state`` 为 ``None`` 而 ``records`` 非零**不可能**（每一行都有
    ``final_state``，见 ``read_worker_outcomes``），所以这里不做那种区分：
    ``records`` 是分母。一个没有分母的「没有记录」与一个「读了 3 行、最后一行是
    正常收尾」在报告里长得一样，而后者是**跑过三次**这件事。
    """

    task_id: str | None
    final_state: str | None
    reason: str | None
    exit_code: int | None
    wall_clock_ms: int | None
    recorded_at: str | None
    records: int
    code_landing: str
    code_landing_reason: str

    def payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "quay_final_state": self.final_state,
            "quay_failure_reason": self.reason,
            "quay_exit_code": self.exit_code,
            "quay_wall_clock_ms": self.wall_clock_ms,
            "quay_recorded_at": self.recorded_at,
            "quay_records": self.records,
            "code_landing": self.code_landing,
            "code_landing_reason": self.code_landing_reason,
        }


@dataclass(frozen=True)
class ResearchTerminal:
    """一件研究工作单元的终态回执。**固定形状**，每一格都可以单独核。

    ``authoritative`` 与 ``external.state`` 是两个**不同主语**的判决，而它们同时
    出现在一份回执里是刻意的：报告要能回答「外面说没落地，为什么你还说它做完了」，
    而那个回答就是这两格并排放在一起，加上 ``chain``。

    ``complete`` 是链条的合取：结算落库 **且** 看板收敛到终态。它与
    ``authoritative`` 不是一回事 —— 前者说「这条链走完了吗」，后者说「走完的
    话是什么终态」。一件结算了但看板没收敛的件，``authoritative`` 已经成立而
    ``complete`` 是假，而那一格正是 2026-09-22 那次运行停在的地方。
    """

    work_item_id: str
    job_id: str
    #: 本仓的权威终态（看板上的目标状态）。**没结算就是 ``None``** —— 一件还没
    #: 做完的工作没有终态，而 ``None`` 不是空串、也不是某个「待定」取值。
    authoritative: str | None
    chain: Mapping[str, Any]
    external: ExternalVerdict
    #: 链条走完了没有。见类 docstring 里它与 ``authoritative`` 的分工。
    complete: bool

    def payload(self) -> dict[str, Any]:
        return {
            "work_item_id": self.work_item_id,
            "job_id": self.job_id,
            "authoritative": self.authoritative,
            "authoritative_source": "marketops.settlement",
            "complete": self.complete,
            "chain": dict(self.chain),
            "chain_note": CHAIN_NOTE,
            "external": self.external.payload(),
        }


def research_terminal(
    *,
    item: WorkItem,
    ref: ExecutionRef | None,
    attempt: JobAttempt | None,
    settlement: Settlement | None,
    sync: BoardSync | None,
    outcomes: Sequence[WorkerExitRecord],
) -> ResearchTerminal:
    """把一条链上的事实组装成一份回执。**纯函数**：不读盘、不外呼。

    三个 I/O 都在调用点（``adapters/mcp_server/tools.py``）：读库、读看板、读
    外部那本退出账。这一层只做一件事 —— 决定**谁说话算数**，并把另一方的原文
    按它本来的样子放在旁边。

    Args:
        item: 那件工作单元。终态从它的 ``stop_reason`` 读（``board_target``）——
            与回传写下去的目标**同一个来源**，所以「报告说 done」与「看板写成
            done」不可能分叉。
        ref: 外部映射。``None`` = 从来没派发过，于是外面没有可读的东西。
        attempt: 那一件上**最后一条**尝试。``None`` = 一次都没领过。
        settlement: 已落库的结算。``None`` = 还没做完。
        sync: 一次只读的看板读数（``WorkDispatch.reconcile(dry_run=True)``）。
            ``None`` = 这一件没有映射，或者这个进程没有执行器可读。
        outcomes: 外部那本账里关于这个任务的行，按写下的顺序。**空元组是「一次都
            没记过」，不是「没有这一格」。**

    Returns:
        :class:`ResearchTerminal`。最后一条外部记录进 ``external`` —— 一次退出的
        结局由**最后一次**退出决定，而 ``records`` 把「一共几次」一起带出来。
    """
    last = outcomes[-1] if outcomes else None
    target = board_target(item)
    return ResearchTerminal(
        work_item_id=item.work_item_id,
        job_id=item.job_id,
        authoritative=target,
        chain={
            "work_item": {
                "subject_ref": item.subject_ref,
                "stop_reason": None if item.stop_reason is None else item.stop_reason.value,
            },
            "attempt": (
                None
                if attempt is None
                else {
                    "attempt_id": attempt.attempt_id,
                    "ordinal": attempt.ordinal,
                    "holder": attempt.holder,
                    "state": attempt.state.value,
                    "settled_at": attempt.settled_at,
                }
            ),
            "settlement": (
                None
                if settlement is None
                else {
                    "accepted": True,
                    "recorded_at": settlement.recorded_at,
                    "stop_reason": settlement.attempt.state.value,
                    "content_sha256": settlement.content_sha256,
                }
            ),
            "board_sync": (
                None
                if sync is None
                else {
                    "task_id": sync.task_id,
                    "target_status": sync.target_status,
                    "observed_status": sync.observed_status,
                    "synced": sync.synced,
                    "changed": sync.changed,
                    "error": sync.error,
                }
            ),
        },
        external=ExternalVerdict(
            task_id=None if ref is None else ref.quay_task_id,
            final_state=None if last is None else last.final_state,
            reason=None if last is None else last.reason,
            exit_code=None if last is None else last.exit_code,
            wall_clock_ms=None if last is None else last.wall_clock_ms,
            recorded_at=None if last is None else last.recorded_at,
            records=len(outcomes),
            code_landing=CODE_LANDING_NOT_APPLICABLE,
            code_landing_reason=CODE_LANDING_REASON,
        ),
        complete=settlement is not None and sync is not None and sync.synced,
    )

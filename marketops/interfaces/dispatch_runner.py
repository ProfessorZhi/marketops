"""本机任务板的 runner：把 ``ready`` 的任务领走、跑一轮、把结局写下来。

## 它为什么是一个独立进程

派发发生在服务进程（``submit_research`` / ``control_job``），领取**必须**发生在另一个
进程里：一个正在处理 MCP 请求的进程去等一段可能跑几十分钟的研究，会把整条服务面拖在
那一段上，而「服务面还活着」正是调用方判断「研究是否还在进行」的依据。于是这一格与
``controller.py`` 同一条理由 —— 进程模型（何时醒、何时停、谁拉起它）是**这个部署**的
性质，只能住在组合根这一层。

## 一轮就是四步，顺序是承重的

1. **领取**（``claim_next``）—— 一次原子读+写，两个 runner 不会领到同一件。
2. **读回工作单元**（``work_item_id_from_labels``）—— 身份只从 label 读，不从
   ``-p`` 里那句话猜。读不出来不是「跳过」，是这一件不该由我们跑：它被移到
   ``needs-human``，而不是反复被试。
3. **跑一轮**（``research_worker.run_round``）—— 构建核对、隔离目录、身份、delegate
   全在那一个函数里；这里不再写第二遍。
4. **收敛看板**（``WorkDispatch.reconcile``）—— 目标从**已落库的结局**读，不由本进程
   的记忆决定。没结算就没有目标，那说明这一轮没有产出，任务回到 ``ready`` 待下一次。

## 退出账写在收敛**之后**

``outcomes.jsonl`` 的那一行回答「这一件被派出去过几次、每一次外面怎么判的」。一次
收敛失败比「delegate 退了 0」更重要 —— 它意味着看板可能还停在 ``ready``，于是外部那
一套会**再派一次**。所以先收敛、后记账，且 ``failure_reason`` 里带上收敛的原文。

## 它只跑本机任务板

``MARKETOPS_DISPATCH_EXECUTOR=none`` 的部署没有板可领，本进程退 2 并说清是哪一个
环境变量决定的；别的实现目前不存在，遇到就拒绝而不是猜一个。与
``composition.default_dispatcher`` 同一组取值，一个字都不多。
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..adapters.dispatch_outcomes import append_outcome
from ..adapters.local_board import LocalBoardDispatcher, ReadyTask
from ..adapters.sqlite.jobs import SqliteJobStore
from ..application.dispatch import WorkDispatch, work_item_id_from_labels
from ..config import Settings, load_settings
from ..domain.errors import ConfigError
from .composition import default_dispatcher
from .research_worker import EXIT_REFUSED, run_round

__all__ = [
    "EXIT_NO_BOARD",
    "RoundOutcome",
    "main",
    "run_once",
]

#: 没有板可领时的退出码。与 ``research_worker.EXIT_REFUSED`` 分开：那是「这一轮不跑」
#: （delegate 与构建的核对没过），这是「这个部署根本没有可领的东西」。运维看到的处置
#: 不同 —— 前者去补 worker 的配置，后者去改执行器。
EXIT_NO_BOARD = 2

#: 一轮的结局词表。**四个词，各自有处置**：
#:
#: * ``settled`` —— 结算落库了；看板是否收敛成功写在 ``failure_reason`` 里；
#: * ``unsettled`` —— delegate 退了 0，但库里没有结算；任务回 ``ready`` 再试；
#: * ``refused`` —— 这一轮在碰 delegate 之前就被拒（构建/配置/身份）；``needs-human``；
#: * ``failed`` —— delegate 自己退了非零；任务回 ``ready``，这一行留下证据。
SETTLED = "settled"
UNSETTLED = "unsettled"
REFUSED = "refused"
FAILED = "failed"

#: 领到之后读不出工作单元的那一件要被移到哪。这不是终态 —— 运维补好 label 或修好派发
#: 方之后可以移回来；但一个 runner **不许**在这种任务上反复试，那会把一件写坏的
#: 任务变成一条永远在转的循环。
NEEDS_HUMAN = "needs-human"


@dataclass(frozen=True)
class RoundOutcome:
    """一轮跑完的读数。**字段恒在**，测试与日志都按这个形状读。"""

    task_id: str
    work_item_id: str | None
    final_state: str
    exit_code: int | None = None
    reason: str | None = None
    wall_clock_ms: int | None = None


def _default_round(work_item_id: str, *, task_id: str) -> int:
    """``run_round`` 的一层间接：测试注入替身时不必改动生产签名。"""
    return run_round(work_item_id, task_id=task_id)


def _record(root: Path, outcome: RoundOutcome) -> None:
    append_outcome(
        root,
        task_id=outcome.task_id,
        final_state=outcome.final_state,
        reason=outcome.reason,
        exit_code=outcome.exit_code,
        wall_clock_ms=outcome.wall_clock_ms,
    )


def _release(board: LocalBoardDispatcher, task: ReadyTask) -> None:
    """把一件没跑完的任务放回 ``ready``。

    放不回去不是「这一行没写」那种局部失败：它会留在 ``running``，而下一次
    ``claim_next`` 再也见不到它。所以这里不吞异常 —— 抛出去，让调用方看见。
    """
    board.release_to_ready(task.external_id)


def _mark_human(board: LocalBoardDispatcher, task: ReadyTask) -> None:
    """把一件不该由 runner 跑的任务移到 ``needs-human``；移不动就抛。

    不用 ``release_to_ready``：那会让下一轮原样再领到它。一个写坏的任务留在
    ``running`` 与回到 ``ready`` 都不对，前者看不见、后者无限循环。
    """
    board.transition(task.external_id, expected="running", status=NEEDS_HUMAN)


def run_once(
    *,
    board: LocalBoardDispatcher,
    jobs: SqliteJobStore,
    root: Path,
    round_runner: Callable[..., int] = _default_round,
) -> tuple[RoundOutcome, ...]:
    """领**一件**、跑完、记账。返回这一轮的结果（没有可领的就是空元组）。

    Args:
        board: 本机任务板。
        jobs: 服务面读写的同一份作业存储。收敛的目标从它里面读 —— 传另一份库会
            让「已结算」这件事在两个进程里各有一个答案。
        root: 退出账所在目录（``dispatch_root``）。与 ``board`` 是同一个根。
        round_runner: 跑一轮的实现。缺省是 ``research_worker.run_round``；测试注入
            替身，于是「runner 的编排」与「worker 的执行」可以分开断言。

    Raises:
        ConfigError: 退出账写不进去。**不吞**：那一行读不出来等于这一轮没有结局。
    """
    claimed = board.claim_next(limit=1)
    if not claimed:
        return ()

    outcomes: list[RoundOutcome] = []
    for task in claimed:
        started = time.monotonic()
        try:
            work_item_id: str | None = work_item_id_from_labels(task.labels)
        except ValueError as exc:
            _mark_human(board, task)
            outcome = RoundOutcome(
                task_id=task.external_id,
                work_item_id=None,
                final_state=REFUSED,
                reason=f"读不出工作单元：{exc}",
                wall_clock_ms=int((time.monotonic() - started) * 1000),
            )
            _record(root, outcome)
            outcomes.append(outcome)
            continue

        try:
            exit_code = round_runner(work_item_id, task_id=task.external_id)
        except Exception as exc:  # noqa: BLE001 - 一轮的意外必须落成一条结局
            # 一个异常既不是「delegate 说失败」也不是「没结算」，它是一个没有结局的
            # 轮次。放回 ready 让下一轮重试，同时把原文留在账上 —— 丢掉它等于让
            # 「runner 崩了」与「这一件没人领」在报告里长得一样。
            _release(board, task)
            outcome = RoundOutcome(
                task_id=task.external_id,
                work_item_id=work_item_id,
                final_state=FAILED,
                reason=f"runner 抛出 {type(exc).__name__}：{exc}",
                wall_clock_ms=int((time.monotonic() - started) * 1000),
            )
            _record(root, outcome)
            outcomes.append(outcome)
            continue

        wall = int((time.monotonic() - started) * 1000)
        if exit_code == EXIT_REFUSED:
            # 碰 delegate 之前就被拒：补配置之前重试没有意义。
            _mark_human(board, task)
            outcome = RoundOutcome(
                task_id=task.external_id,
                work_item_id=work_item_id,
                final_state=REFUSED,
                exit_code=exit_code,
                reason="这一轮在碰 delegate 之前被拒（构建/配置/身份）。",
                wall_clock_ms=wall,
            )
            _record(root, outcome)
            outcomes.append(outcome)
            continue

        if exit_code != 0:
            _release(board, task)
            outcome = RoundOutcome(
                task_id=task.external_id,
                work_item_id=work_item_id,
                final_state=FAILED,
                exit_code=exit_code,
                reason=f"delegate 退出码 {exit_code}。",
                wall_clock_ms=wall,
            )
            _record(root, outcome)
            outcomes.append(outcome)
            continue

        sync = WorkDispatch(jobs=jobs, dispatcher=board).reconcile(work_item_id)
        if sync.target_status is None:
            # 没有结算就没有目标：这一轮没有产出。放回 ready 是有意的 —— 目标从库里
            # 读，不由这里补一个，所以「再试一次」与「已经做完」不会混在一起。
            _release(board, task)
            outcome = RoundOutcome(
                task_id=task.external_id,
                work_item_id=work_item_id,
                final_state=UNSETTLED,
                exit_code=exit_code,
                reason=sync.error,
                wall_clock_ms=wall,
            )
            _record(root, outcome)
            outcomes.append(outcome)
            continue

        outcome = RoundOutcome(
            task_id=task.external_id,
            work_item_id=work_item_id,
            final_state=SETTLED,
            exit_code=exit_code,
            # 收敛失败时 board 可能还停在 ready，外部会再派一次 —— 这句原文必须带上。
            reason=None if sync.synced else sync.error,
            wall_clock_ms=wall,
        )
        _record(root, outcome)
        outcomes.append(outcome)

    return tuple(outcomes)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本机任务板的 runner")
    parser.add_argument(
        "--once",
        action="store_true",
        help="领一件（如果有）就跑完退出；缺省是一个循环。",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=15.0,
        help="板上没有 ready 任务时两次领取之间的秒数（循环模式）。",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=0,
        help="循环模式下最多跑几轮，0 表示不设上限。",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    settings: Settings = load_settings()
    dispatcher = default_dispatcher(settings)
    if dispatcher is None:
        print(
            "没有可领的任务板：本部署选择不派发"
            f"（MARKETOPS_DISPATCH_EXECUTOR={settings.dispatch_executor!r}）。",
            file=sys.stderr,
        )
        return EXIT_NO_BOARD
    if not isinstance(dispatcher, LocalBoardDispatcher):
        raise ConfigError(
            "runner 只跑本机任务板。这个部署的执行器不是 local —— "
            "runner 与派发必须看同一张板，否则两边各记一半而两边都正常。"
        )

    settings.ensure_data_dirs()
    jobs = SqliteJobStore(settings.database_path)
    root = settings.dispatch_root_resolved

    rounds = 0
    while True:
        outcomes = run_once(board=dispatcher, jobs=jobs, root=root)
        for outcome in outcomes:
            print(
                f"{outcome.task_id} → {outcome.work_item_id or '(unknown)'}："
                f"{outcome.final_state}"
                + (f"（{outcome.reason}）" if outcome.reason else ""),
                file=sys.stderr,
            )
        rounds += 1
        if args.once:
            return 0
        if args.max_rounds and rounds >= args.max_rounds:
            return 0
        if not outcomes:
            time.sleep(args.interval)


if __name__ == "__main__":  # pragma: no cover
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        raise SystemExit(130) from None

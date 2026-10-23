"""本机任务板（``adapters/local_board.py``）与 runner（``interfaces/dispatch_runner.py``）。

## 这个文件测的是「两个进程共用一张板」的那几条性质

派发在服务进程、领取在 runner 进程。字典实现回答不了这件事，而能回答它的实现必须
在四条上成立，本文件每条都有对应用例：

| 判据 | 它挡的失败 |
|---|---|
| ``find`` 是 **AND** 语义 | 少给一个 label 就把别人的活查进自己名下 |
| ``create`` 幂等 | 并发的两个派发者各建一条，账单付两遍 |
| ``transition`` 是真 CAS | 「先读再写」在两个人同时到达时都成功 |
| ``claim_next`` 原子 | 两个 runner 领同一件，同一份钱花两遍 |

runner 那一半测的是**编排**：领到之后先读回工作单元、再跑、再收敛、最后记账；每一
种结局（结算 / 没结算 / 被拒 / 失败）在板上留下不同的状态，而账上都有一行。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from marketops.adapters.dispatch_outcomes import read_worker_outcomes
from marketops.adapters.local_board import READY, RUNNING, LocalBoardDispatcher, ReadyTask
from marketops.adapters.memory import InMemoryJobStore
from marketops.application.dispatch import DispatchOutcome, WorkDispatch, link_labels
from marketops.domain.enums import JobInputKind, StopReason
from marketops.domain.errors import ExternalStateError
from marketops.domain.findings import Findings
from marketops.domain.util import sha256_text
from marketops.interfaces.dispatch_runner import (
    FAILED,
    REFUSED,
    SETTLED,
    UNSETTLED,
    run_once,
)
from marketops.interfaces.research_worker import EXIT_REFUSED
from marketops.ports.dispatch import ExecutionUnit, ExternalTask
from tests.fakes import synthetic_dossiers, synthetic_scene_files

SCENE = "uk-acme-natural-grass"
KEY = "board-v1"
SCENE_FILES = synthetic_scene_files()


# ----------------------------------------------------------------------
# 脚手架
# ----------------------------------------------------------------------
@pytest.fixture
def board(tmp_path: Path) -> LocalBoardDispatcher:
    return LocalBoardDispatcher(root=tmp_path / "board")


def _unit(unit_id: str = "item-a") -> ExecutionUnit:
    return ExecutionUnit(
        unit_id=unit_id,
        job_id="job-a",
        title=f"{SCENE}: {unit_id}",
        body="正文",
    )


def _compiled(
    store: InMemoryJobStore, *, subjects: Sequence[str] = ("devon-01",), key: str = KEY
) -> tuple[str, str]:
    """受理 + 编译，返回 ``(job_id, 第一个 work_item_id)``。**不**派发。"""
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026-09",
        idempotency_key=key,
        request_sha256=sha256_text(f"devon-2026-09:{key}"),
    )
    items = store.compile_work_items(
        job.job_id, synthetic_dossiers(*subjects), scene=SCENE_FILES
    )
    return job.job_id, items[0].work_item_id


def _rows(board: LocalBoardDispatcher) -> list[tuple[str, str]]:
    with board._connect() as conn:  # noqa: SLF001 - 判据要读板子的原样
        return [(row[0], row[1]) for row in conn.execute(
            "SELECT external_id, status FROM dispatch_task ORDER BY external_id"
        )]


# ----------------------------------------------------------------------
# 端口合同
# ----------------------------------------------------------------------
def test_find_is_an_and_over_the_whole_label_set(board: LocalBoardDispatcher) -> None:
    """两条任务各命中同一个 label 的一半；完整的一组只命中一条。"""
    shared = "job:shared"
    first = board.create(
        _unit("item-1"), labels=(shared, "item:item-1"), status=READY
    )
    board.create(_unit("item-2"), labels=(shared, "item:item-2"), status=READY)

    found = board.find(labels=(shared, "item:item-1"))

    assert [task.external_id for task in found] == [first.external_id]


def test_find_with_no_match_is_an_answer_not_a_failure(
    board: LocalBoardDispatcher,
) -> None:
    board.create(_unit(), labels=("job:j", "item:i"), status=READY)

    assert board.find(labels=("job:j", "item:other")) == ()


def test_find_refuses_an_unfiltered_query(board: LocalBoardDispatcher) -> None:
    with pytest.raises(ValueError):
        board.find(labels=())


def test_create_twice_leaves_exactly_one_row(board: LocalBoardDispatcher) -> None:
    """同一个单元算出的外部 id 是确定性的，第二次 create 是原地对齐。"""
    labels = ("job:j", "item:i")

    first = board.create(_unit(), labels=labels, status=READY)
    second = board.create(_unit(), labels=labels, status=READY)

    assert first.external_id == second.external_id
    assert len(_rows(board)) == 1


def test_create_returns_the_labels_it_actually_wrote(
    board: LocalBoardDispatcher,
) -> None:
    labels = ("job:j", "item:i")

    task = board.create(_unit(), labels=labels, status=READY)

    assert isinstance(task, ExternalTask)
    assert task.labels == labels
    assert task.status == READY


def test_transition_is_a_compare_and_set(board: LocalBoardDispatcher) -> None:
    task = board.create(_unit(), labels=("job:j", "item:i"), status=READY)

    moved = board.transition(task.external_id, expected=READY, status=RUNNING)

    assert moved.status == RUNNING
    with pytest.raises(ExternalStateError):
        board.transition(task.external_id, expected=READY, status=RUNNING)


def test_claim_next_takes_one_and_hides_it_from_the_next_claim(
    board: LocalBoardDispatcher,
) -> None:
    first = board.create(_unit("item-1"), labels=("job:j", "item:item-1"), status=READY)
    board.create(_unit("item-2"), labels=("job:j", "item:item-2"), status=READY)

    claimed = board.claim_next(limit=1)

    assert [task.external_id for task in claimed] == [first.external_id]
    assert isinstance(claimed[0], ReadyTask)
    assert claimed[0].labels == ("job:j", "item:item-1")
    remaining = board.claim_next(limit=1)
    assert len(remaining) == 1
    assert remaining[0].external_id != first.external_id


def test_claim_next_is_empty_when_nothing_is_ready(
    board: LocalBoardDispatcher,
) -> None:
    board.create(_unit(), labels=("job:j", "item:i"), status=RUNNING)

    assert board.claim_next(limit=1) == ()


def test_release_puts_a_claimed_task_back(board: LocalBoardDispatcher) -> None:
    task = board.create(_unit(), labels=("job:j", "item:i"), status=READY)
    board.claim_next(limit=1)

    board.release_to_ready(task.external_id)

    again = board.claim_next(limit=1)
    assert [item.external_id for item in again] == [task.external_id]


def test_constructing_a_board_writes_nothing(tmp_path: Path) -> None:
    """``--check`` 会构造它。构造期落盘会让一次自检在盘上留下一个目录。"""
    root = tmp_path / "not-yet"

    LocalBoardDispatcher(root=root)

    assert not root.exists()


# ----------------------------------------------------------------------
# 端到端：派发 → 领取 → 租约 → 结算 → 收敛
# ----------------------------------------------------------------------
def test_a_settled_round_moves_the_board_to_done(
    board: LocalBoardDispatcher, tmp_path: Path
) -> None:
    """没有外部执行器进程的一整条路：派发、领取、结算、收敛全部走本机。"""
    store = InMemoryJobStore()
    job_id, work_item_id = _compiled(store)
    dispatch = WorkDispatch(jobs=store, dispatcher=board)

    receipt = dispatch.dispatch(work_item_id)
    assert receipt.outcome is DispatchOutcome.CREATED
    task_id = receipt.external.external_id

    claimed = board.claim_next(limit=1)
    assert claimed[0].external_id == task_id
    assert claimed[0].labels == link_labels(job_id, work_item_id)

    attempt = store.lease(work_item_id, holder="worker-a")
    store.settle(
        attempt.attempt_id,
        stop_reason=StopReason.GOAL_MET,
        findings=Findings(gaps=("只找到两个联系人",)),
    )

    sync = dispatch.reconcile(work_item_id)

    assert sync.synced is True
    assert sync.changed is True
    assert sync.target_status == "done"
    assert _rows(board) == [(task_id, "done")]


def test_reconcile_is_idempotent_on_the_board(
    board: LocalBoardDispatcher,
) -> None:
    store = InMemoryJobStore()
    _, work_item_id = _compiled(store)
    dispatch = WorkDispatch(jobs=store, dispatcher=board)
    dispatch.dispatch(work_item_id)
    attempt = store.lease(work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=Findings())

    first = dispatch.reconcile(work_item_id)
    second = dispatch.reconcile(work_item_id)

    assert (first.changed, first.synced) == (True, True)
    assert (second.changed, second.synced) == (False, True), "第二次不该再造一次并发"


# ----------------------------------------------------------------------
# runner 的编排
# ----------------------------------------------------------------------
def _enqueue(board: LocalBoardDispatcher, store: InMemoryJobStore) -> tuple[str, str]:
    """把一件工作真的派发到板上，返回 ``(work_item_id, task_id)``。"""
    _, work_item_id = _compiled(store)
    receipt = WorkDispatch(jobs=store, dispatcher=board).dispatch(work_item_id)
    return work_item_id, receipt.external.external_id


def test_runner_settles_and_records_one_line(
    board: LocalBoardDispatcher, tmp_path: Path
) -> None:
    store = InMemoryJobStore()
    work_item_id, task_id = _enqueue(board, store)

    def settle_round(unit: str, *, task_id: str) -> int:
        attempt = store.lease(unit, holder="worker-a")
        store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=Findings())
        return 0

    outcomes = run_once(
        board=board,
        jobs=store,  # type: ignore[arg-type] - 替身按端口鸭子类型即可
        root=tmp_path / "board",
        round_runner=settle_round,
    )

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert (outcome.final_state, outcome.work_item_id, outcome.exit_code) == (
        SETTLED,
        work_item_id,
        0,
    )
    assert outcome.reason is None
    assert read_worker_outcomes(tmp_path / "board", task_id)[0].final_state == SETTLED


def test_runner_releases_a_round_that_did_not_settle(
    board: LocalBoardDispatcher, tmp_path: Path
) -> None:
    """delegate 退了 0 但库里没有结算：任务回 ``ready``，账上写 ``unsettled``。"""
    store = InMemoryJobStore()
    _, task_id = _enqueue(board, store)

    outcomes = run_once(
        board=board,
        jobs=store,  # type: ignore[arg-type]
        root=tmp_path / "board",
        round_runner=lambda unit, *, task_id: 0,
    )

    assert [outcome.final_state for outcome in outcomes] == [UNSETTLED]
    assert _rows(board) == [(task_id, READY)]
    assert read_worker_outcomes(tmp_path / "board", task_id)[0].final_state == UNSETTLED


def test_runner_parks_a_refused_round_with_a_human(
    board: LocalBoardDispatcher, tmp_path: Path
) -> None:
    """被拒不是可重试的失败：重试只会让同一个配置缺口再报一次。"""
    store = InMemoryJobStore()
    _, task_id = _enqueue(board, store)

    outcomes = run_once(
        board=board,
        jobs=store,  # type: ignore[arg-type]
        root=tmp_path / "board",
        round_runner=lambda unit, *, task_id: EXIT_REFUSED,
    )

    assert [outcome.final_state for outcome in outcomes] == [REFUSED]
    assert _rows(board) == [(task_id, "needs-human")]
    assert read_worker_outcomes(tmp_path / "board", task_id)[0].final_state == REFUSED


def test_runner_releases_a_failed_delegate_run(
    board: LocalBoardDispatcher, tmp_path: Path
) -> None:
    store = InMemoryJobStore()
    _, task_id = _enqueue(board, store)

    outcomes = run_once(
        board=board,
        jobs=store,  # type: ignore[arg-type]
        root=tmp_path / "board",
        round_runner=lambda unit, *, task_id: 7,
    )

    assert [outcome.final_state for outcome in outcomes] == [FAILED]
    assert outcomes[0].exit_code == 7
    assert _rows(board) == [(task_id, READY)]


def test_runner_releases_when_the_round_raises(
    board: LocalBoardDispatcher, tmp_path: Path
) -> None:
    """一个异常不是「没结算」，它是一个没有结局的轮次 —— 放回去，并把原文留在账上。"""
    store = InMemoryJobStore()
    _, task_id = _enqueue(board, store)

    def boom(unit: str, *, task_id: str) -> int:
        raise RuntimeError("delegate 的输出读不懂")

    outcomes = run_once(
        board=board,
        jobs=store,  # type: ignore[arg-type]
        root=tmp_path / "board",
        round_runner=boom,
    )

    assert [outcome.final_state for outcome in outcomes] == [FAILED]
    assert "RuntimeError" in (outcomes[0].reason or "")
    assert _rows(board) == [(task_id, READY)]


def test_runner_parks_a_task_it_cannot_map(
    board: LocalBoardDispatcher, tmp_path: Path
) -> None:
    """读不出工作单元不是「跳过」：这一件不该由 runner 跑，它被移到人工。"""
    task = board.create(_unit(), labels=("job:j",), status=READY)

    outcomes = run_once(
        board=board,
        jobs=InMemoryJobStore(),  # type: ignore[arg-type]
        root=tmp_path / "board",
        round_runner=lambda unit, *, task_id: 0,
    )

    assert [outcome.final_state for outcome in outcomes] == [REFUSED]
    assert _rows(board) == [(task.external_id, "needs-human")]


def test_runner_does_nothing_when_the_board_is_empty(
    board: LocalBoardDispatcher, tmp_path: Path
) -> None:
    assert run_once(
        board=board,
        jobs=InMemoryJobStore(),  # type: ignore[arg-type]
        root=tmp_path / "board",
    ) == ()

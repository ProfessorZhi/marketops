"""§1：研究件在外部执行器那里的终态 —— **以本仓的完成语义为准**。

## 这一条修的是哪一种不一致

外部那套（执行器）判一件工作「做完了没有」，问的是**软件工程**的问题：worker 的
产出有没有合进 ``develop``、有没有留下 worktree。本仓的研究工作单元不产出提交、
不跑 fan-in、也没有 worktree，于是那个判据在它身上**恒为假**：

    2026-09-23 实测（外部执行器写下的 ``worker-outcome.jsonl``）：
    {"task": "MOPS-11ed121a-688a5fe8", "final_state": "exited-not-landed",
     "failure_reason": "worktree state unreadable (git worktree list failed)"}

而**同一件**工作在那之前 22.7 秒已经结算落库、看板也已经收敛到 ``done``。业务上
它做完了；外部对「有没有落地」那一格的判决是**无对象**的 —— 不是 FAIL。

## 两条纪律，各自都有一条判据

1. **权威在本仓。** 一件研究的完成判据是
   ``work_item → active attempt → submit_findings accepted → settlement persisted
   → board_sync synced to terminal``，这条链上的每一环都落在库里或在看板上读得回来。
   ``authoritative`` 从 ``board_target`` 读 —— 与回传写下去的目标**同一个来源**，
   所以「报告说 done」与「看板写成 done」不可能分叉。
2. **不适用的判据必须报 N/A，不能报 FAIL。** ``code_landing`` 恒为
   ``not-applicable``；把它报成 ``failed`` 会让每一次正常收尾在报告里多一条假失败，
   而假失败亮久了就没人看了（与 ``needs-human`` 那盏灯同一条理由）。

## 「不会重新派发」是怎么证的

重派**只由外部那个状态决定**：worker-driver 每一轮扫的是 ``status: ready`` 的
任务（``application/dispatch.py::INITIAL_EXTERNAL_STATUS`` 的 docstring 记着这条
实测）。所以判据落在**池子里有没有它**，而不是落在回执的自述上：

* 结算 + ``board_sync`` 之后，池子是**空的**；
* 对照组：同一件、同样有 ``exited-not-landed`` 那一行，但看板没收敛 —— 池子里
  **仍然有它**。

没有对照组的「池子是空的」是一句恒真的话：它在任何实现下都绿。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from marketops.adapters.memory import InMemoryJobStore
from marketops.adapters.dispatch_outcomes import read_worker_outcomes
from marketops.application.dispatch import (
    INITIAL_EXTERNAL_STATUS,
    WorkDispatch,
)
from marketops.application.research_terminal import (
    CODE_LANDING_NOT_APPLICABLE,
    research_terminal,
)
from marketops.domain.enums import JobInputKind, StopReason
from marketops.domain.findings import Findings
from marketops.domain.job import WorkItem
from marketops.domain.util import sha256_text
from marketops.ports.jobs import JobStore
from tests.fakes import FakeDispatcher, synthetic_dossiers, synthetic_scene_files

SCENE = "uk-acme-natural-grass"
KEY = "research-terminal-v1"
HOLDER = "claude-worker-1"
SCENE_FILES = synthetic_scene_files()

#: 2026-09-23 那次真实运行落盘的那一行（逐字）。测试用它而不是自己编一句：
#: 这一条要复现的是**生产里真的出现过的形状**，编一句会得到一个只在这个文件里
#: 存在的失败理由。
#:
#: ``task`` 那一格由 :func:`_outcome_line` 换成**这次真的派出去的那个 id**：它是
#: 这本账的索引，``read_worker_outcomes`` 按它收窄。留一个生产里的 id，读取会
#: 诚实地返回 0 行 —— 而那会让这一条测试变成在测「读不到东西的时候回执长什么样」，
#: 与它自己的名字无关。
REAL_OUTCOME_LINE = {
    "ts": "2026-09-23T18:36:45.724Z",
    "task": "MOPS-11ed121a-688a5fe8",
    "selector_reason": "ready",
    "exit_code": 0,
    "signal": None,
    "wall_clock_ms": 220458,
    "final_state": "exited-not-landed",
    "failure_reason": "worktree state unreadable (git worktree list failed)",
    "started_at": "2026-09-23T18:32:59.000Z",
    "ended_at": "2026-09-23T18:36:45.724Z",
    "worker_pid": 990554,
    "run_id": "1f0c",
}


def _outcome_line(task_id: str, **overrides: object) -> dict[str, object]:
    """上面那一行，换成这次的任务 id。"""
    return {**REAL_OUTCOME_LINE, "task": task_id, **overrides}


# ----------------------------------------------------------------------
# 脚手架
# ----------------------------------------------------------------------
def _admit_and_dispatch(
    store: JobStore, outside: FakeDispatcher, *, subjects: Sequence[str]
) -> tuple[str, tuple[WorkItem, ...]]:
    """受理 → 编译 → 派发。**派发是真的** —— 池子里的那一行就是它建出来的。"""
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026-09",
        idempotency_key=KEY,
        request_sha256=sha256_text(f"devon-2026-09:{KEY}"),
    )
    items = store.compile_work_items(
        job.job_id, synthetic_dossiers(*subjects), scene=SCENE_FILES
    )
    WorkDispatch(jobs=store, dispatcher=outside).dispatch_job(job.job_id)
    return job.job_id, items


def _settle(store: JobStore, item: WorkItem, reason: StopReason) -> None:
    attempt = store.lease(item.work_item_id, holder=HOLDER)
    store.settle(attempt.attempt_id, stop_reason=reason, findings=Findings(gaps=("就到这",)))


def _write_outcomes(root: Path, rows: Sequence[dict[str, object]]) -> None:
    """把派发那一侧的退出账写下来 —— 一行一条 JSON，形状与读器一致。"""
    path = root / "outcomes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _pool(outside: FakeDispatcher) -> list[str]:
    """外部执行器**下一轮会捡谁**。

    判据是它自己扫的那个状态，不是本仓的任何东西 —— 这一条要证的正是「外面不会
    再捡它」，所以池子必须从**外面**读。
    """
    return sorted(
        task_id
        for task_id, task in outside.tasks.items()
        if task.status == INITIAL_EXTERNAL_STATUS
    )


def _receipt(
    store: JobStore,
    outside: FakeDispatcher,
    item: WorkItem,
    *,
    outcomes: Sequence[dict[str, object]] = (),
    outcomes_root: Path | None = None,
    dry_run: bool = True,
):
    """把工具的接线原样走一遍：**读库、读外面、读外部那本账**，然后组装回执。"""
    ref = store.execution_ref(item.work_item_id)
    settlement = store.settlement(item.work_item_id)
    attempts = store.attempts(item.work_item_id)
    sync = (
        None
        if ref is None
        else WorkDispatch(jobs=store, dispatcher=outside).reconcile(
            item.work_item_id, dry_run=dry_run
        )
    )
    rows = (
        ()
        if outcomes_root is None or ref is None
        else read_worker_outcomes(outcomes_root, ref.dispatch_task_id)
    )
    assert len(rows) == len(outcomes), "外部那本账没被读成预期的行数"
    return research_terminal(
        item=item,
        ref=ref,
        attempt=attempts[-1] if attempts else None,
        settlement=settlement,
        sync=sync,
        outcomes=rows,
    )


# ----------------------------------------------------------------------
# 1. 主判据：业务完成 ⇒ 出池，即便外部判了 exited-not-landed
# ----------------------------------------------------------------------
def test_a_settled_research_task_leaves_the_pool_even_though_the_worker_judged_it_not_landed(
    tmp_path: Path,
) -> None:
    """这一条就是 §1 要的那条测试本身。"""
    store = InMemoryJobStore()
    outside = FakeDispatcher()
    _, (item,) = _admit_and_dispatch(store, outside, subjects=("kent-004",))
    _settle(store, item, StopReason.GOAL_MET)
    WorkDispatch(jobs=store, dispatcher=outside).reconcile(item.work_item_id)

    outcomes_root = tmp_path / "outcomes"
    line = _outcome_line(ref_task_id(store, item))
    _write_outcomes(outcomes_root, [line])

    after = store.get_work_item(item.work_item_id)
    assert after is not None
    receipt = _receipt(store, outside, after, outcomes=[line], outcomes_root=outcomes_root)

    assert receipt.authoritative == "done"
    assert receipt.complete is True
    assert receipt.external.final_state == "exited-not-landed"
    assert receipt.external.records == 1, "读了几个分母要一起报出来"
    assert _pool(outside) == [], (
        "结算并收敛之后池子必须是空的 —— 外面按 ready 认「还没做」，留一个 ready "
        "就是下一轮再派一次这件事"
    )


# ----------------------------------------------------------------------
# 2. 对照组：看板没收敛的那一件**仍然在池子里**
# ----------------------------------------------------------------------
def test_an_item_whose_board_never_converged_stays_in_the_pool(tmp_path: Path) -> None:
    """没有对照组，上面那条「池子是空的」是一句恒真的话。

    构造的是**2026-09-22 那次运行的形状**：结算落库了，回传一次都没发生。业务上
    它做完了，而外面仍然按 ``ready`` 认「还没开工」—— 于是它会**再派一次**。
    这一格说明「出池」这件事由 ``board_sync`` 达成，不是由结算自动达成的。
    """
    store = InMemoryJobStore()
    outside = FakeDispatcher()
    _, (item,) = _admit_and_dispatch(store, outside, subjects=("kent-005",))
    _settle(store, item, StopReason.GOAL_MET)

    after = store.get_work_item(item.work_item_id)
    assert after is not None
    receipt = _receipt(store, outside, after, dry_run=True)

    assert receipt.authoritative == "done", "本仓的业务判据已经成立"
    assert receipt.complete is False, "而链上的最后一环还没到位"
    assert _pool(outside) == [ref_task_id(store, item)], (
        "看板没收敛 ⇒ 它还在池子里 ⇒ 外面会再派一次。这条与上面那条一起才说明"
        "判据有判别力"
    )


def ref_task_id(store: JobStore, item: WorkItem) -> str:
    ref = store.execution_ref(item.work_item_id)
    assert ref is not None
    return ref.dispatch_task_id


# ----------------------------------------------------------------------
# 3. N/A 不是 FAIL
# ----------------------------------------------------------------------
def test_the_code_landing_criterion_is_labelled_not_applicable_and_never_failed() -> None:
    """不适用的判据必须报 N/A。

    把它报成 ``failed`` 会让每一次正常收尾在报告里多一条假失败 —— 而这一条的不
    同取值必须是**被写下来的那一个词**，不是「假的」：一个布尔丢掉了「为什么不
    适用」，读报告的人据此会去找一个不存在的失败。
    """
    store = InMemoryJobStore()
    outside = FakeDispatcher()
    _, (item,) = _admit_and_dispatch(store, outside, subjects=("kent-004",))
    _settle(store, item, StopReason.GOAL_MET)

    after = store.get_work_item(item.work_item_id)
    assert after is not None
    receipt = _receipt(store, outside, after)

    assert receipt.external.code_landing == CODE_LANDING_NOT_APPLICABLE
    assert "不适用" in receipt.external.code_landing_reason
    assert "worktree" in receipt.external.code_landing_reason, (
        "理由要点名那个判据问的是什么问题，否则「不适用」是一句谁也核不动的话"
    )


# ----------------------------------------------------------------------
# 4. 权威来自结算，不是来自外部那本账
# ----------------------------------------------------------------------
def test_the_authoritative_terminal_comes_from_the_settlement_not_from_the_worker() -> None:
    """两个方向各有一格：结算了的是终态，没结算的**没有**终态。

    「没结算就没有终态」那一格是判据的判别力所在：把它写成 ``None`` 之外的东西
    （空串、``"pending"``），或者让 ``authoritative`` 从外面那本账读，
    上面第 1 条会在「外面说 exited-not-landed」时退成一句谎言。
    """
    store = InMemoryJobStore()
    outside = FakeDispatcher()
    job_id, items = _admit_and_dispatch(store, outside, subjects=("kent-004", "kent-005"))

    open_item, settled_item = items
    _settle(store, settled_item, StopReason.BLOCKED)

    fresh = store.get_work_item(settled_item.work_item_id)
    assert fresh is not None
    settled = _receipt(store, outside, fresh)
    assert settled.authoritative == "needs-human", "blocked 欠人一个动作"

    still_open = store.get_work_item(open_item.work_item_id)
    assert still_open is not None
    assert still_open.stop_reason is None, "这一格要的是**还没结算**的那一件"
    pending = _receipt(store, outside, still_open)
    assert pending.authoritative is None, "没结算就没有终态 —— 不是空串，也不是某个取值"
    assert pending.complete is False


# ----------------------------------------------------------------------
# 5. 接线之后：验收真的从 ``get_job`` 读得到这份回执
# ----------------------------------------------------------------------
def test_the_job_report_carries_the_authoritative_research_terminal(settings, tmp_path) -> None:
    """§1 的另一半：回执不只要算得对，还要**在验收读的那个接口上**。

    前四条走的是纯函数。这一条走真实的 MCP 会话（Bearer 令牌 + RoleGuard），
    走 ``get_job``，而它正是 §14 的验收要读的那个有界报告。少了它，「权威在本仓」
    这句话只是本文件里的一个对象。
    """
    import json as _json

    from marketops.adapters.sqlite.jobs import SqliteJobStore
    from marketops.security.catalog import Role
    from marketops.security.tokens import TokenRing, mint
    from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload, write_accounts

    write_accounts(settings, "wired-2026", ("kent-004",))
    outside = FakeDispatcher()
    outcomes_root = tmp_path / "outcomes"

    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring_path = settings.data_root / "wired-tokens.json"
    ring_path.write_text(
        _json.dumps(
            ring_payload(ring_entry(digest, "claude-control", ["controller"]))
        ),
        encoding="utf-8",
    )
    ring = TokenRing.from_file(ring_path)
    store = lambda: SqliteJobStore(settings.database_path)  # noqa: E731 — 与工具表同一形状的工厂

    def session(tool: str, arguments: dict) -> dict:
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            calls=((tool, arguments),),
            want_tool_list=False,
            dispatcher=outside,
            jobs=store,
            worker_outcomes=lambda task_id: read_worker_outcomes(outcomes_root, task_id),
        )
        assert not outcome.handshake_error, outcome.handshake_error
        call = outcome.call(tool)
        assert not call.is_error, call.text
        return call.as_json()

    accepted = session(
        "submit_research",
        {
            "scene_id": SCENE,
            "input_kind": "known_accounts",
            "input_ref": "wired-2026",
            "idempotency_key": "research-terminal-wired-v1",
        },
    )
    job_id = accepted["job_id"]

    # 让那件工作走完整条链：领活 → 结算（回传在结算里）→ 外部那本账记下它没落地。
    jobs = store()
    item = jobs.work_items(job_id)[0]
    attempt = jobs.lease(item.work_item_id, holder=HOLDER)
    jobs.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=Findings(gaps=("就到这",)))
    WorkDispatch(jobs=jobs, dispatcher=outside).reconcile(item.work_item_id)

    ref = jobs.execution_ref(item.work_item_id)
    assert ref is not None
    line = _outcome_line(ref.dispatch_task_id)
    _write_outcomes(outcomes_root, [line])

    body = session("get_job", {"job_id": job_id})
    block = body["research_terminals"]

    assert block["reported"] is True
    assert block["authority"] == "marketops.settlement"
    terminal = block["items"][0]
    assert terminal["authoritative"] == "done"
    assert terminal["complete"] is True
    assert terminal["external"]["dispatch_final_state"] == "exited-not-landed"
    assert terminal["external"]["code_landing"] == CODE_LANDING_NOT_APPLICABLE
    assert terminal["external"]["dispatch_records"] == 1


def test_an_unreadable_worker_ledger_is_not_reported_as_zero_records(settings, tmp_path) -> None:
    """读不出来与「一行都没有」必须**不是同一个数**。

    外部那本账有一行坏掉时，读器抛 ``ConfigError``。把那个异常吞成空元组会让回执
    报 ``records: 0`` —— 而 ``0`` 的意思是「外面一次都没记过这件事」，与「外面判了
    它没落地但我读不出来」正好相反。这一条断言那个分母是 ``None`` 而不是 0，并且
    报告**没有因此整份失败**（附注读坏了不该让正文也读不到）。
    """
    import json as _json

    from marketops.adapters.sqlite.jobs import SqliteJobStore
    from marketops.security.catalog import Role
    from marketops.security.tokens import TokenRing, mint
    from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload, write_accounts

    write_accounts(settings, "wired-bad-2026", ("kent-004",))
    outside = FakeDispatcher()
    outcomes_root = tmp_path / "outcomes"
    _write_outcomes(outcomes_root, [])  # 目录存在、账本存在

    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring_path = settings.data_root / "wired-bad-tokens.json"
    ring_path.write_text(
        _json.dumps(
            ring_payload(ring_entry(digest, "claude-control", ["controller"]))
        ),
        encoding="utf-8",
    )
    ring = TokenRing.from_file(ring_path)
    store = lambda: SqliteJobStore(settings.database_path)  # noqa: E731

    def session(tool: str, arguments: dict) -> dict:
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            calls=((tool, arguments),),
            want_tool_list=False,
            dispatcher=outside,
            jobs=store,
            worker_outcomes=lambda task_id: read_worker_outcomes(outcomes_root, task_id),
        )
        assert not outcome.handshake_error, outcome.handshake_error
        call = outcome.call(tool)
        assert not call.is_error, call.text
        return call.as_json()

    accepted = session(
        "submit_research",
        {
            "scene_id": SCENE,
            "input_kind": "known_accounts",
            "input_ref": "wired-bad-2026",
            "idempotency_key": "research-terminal-wired-v2",
        },
    )
    job_id = accepted["job_id"]
    jobs = store()
    item = jobs.work_items(job_id)[0]
    attempt = jobs.lease(item.work_item_id, holder=HOLDER)
    jobs.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=Findings(gaps=("就到这",)))
    ref = jobs.execution_ref(item.work_item_id)
    assert ref is not None

    # 结算之后把那一行弄坏 —— 这是「文件在、读不动」，不是「文件不在」。
    (outcomes_root / "outcomes.jsonl").write_text("{ 这不是 JSON\n", encoding="utf-8")

    terminal = session("get_job", {"job_id": job_id})["research_terminals"]["items"][0]

    assert terminal["authoritative"] == "done", "附注读坏了，正文照常"
    assert terminal["external"]["dispatch_records"] is None, "报 0 就是把它说成「外面没记过」"
    assert terminal["external"]["dispatch_read_error"]
    del job_id

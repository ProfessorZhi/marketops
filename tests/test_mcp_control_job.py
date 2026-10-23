"""Phase G4：``control_job`` —— 类型化的 pause / resume / cancel（``MCP_SERVICE.md`` §2）。

## 这条工具真正难的地方不是转移，是两句话

§6 对它的要求只有两句，而两句都是在禁止一种「成功」：

* 「Cancel reports what actually stopped」—— 回执里要有**实际发生了什么**，
  不能只有「你的意图受理了」；
* 「Pause normally prevents new dispatch and lets accepted in-flight results
  settle」—— pause **不**收回在途租约，所以回执不能假装它们停了。

所以这个文件里每一条用例都在问同一个问题：**这份回执能不能与另一种情况分开？**

## 七条判据

| 判据 | 它挡的失败 |
|---|---|
| ``intent`` 与 ``job`` 分列 | 「我按下了暂停」被读成「它真的暂停了」 |
| CAS 不匹配整次拒绝 | 两个控制端同时按下时最后一个写的赢，两边都以为成功了 |
| 非法转移拒绝并列出可去的 | 一块恒假的「控制面」 |
| ``observed`` 把在途的点名 | 一个「已暂停」的作业里继续跑着没人叫停的东西 |
| ``blocked`` 不是一个动词 | 调用方声明一个没有任何证据的阻塞 |
| ``resume`` 真的**重新派发** | 状态写回 ``queued``、外面一个任务都没有，而报告说「已恢复」 |
| ``resume`` 带上计划步骤（§B16 关闭条件 7） | 一次派发在计划上悬空；而拒绝写在状态写入**之后**时，调用方读到一次失败、盘上是一次已生效的写入 |

外加一条**跨工具的**：``pause`` 之后 ``map_execution`` / ``lease`` 都得拒绝
（§6 逐字：Pause normally prevents new dispatch）。那条规则住在存储层，这里是它
在**服务面**上的执行器 —— 一条只写在存储层的边界，从工具这边看是看不见的。

## 最后一条判据为什么必须落在**外部**上

``resume`` 只把状态写回 ``queued`` 的实现，与真的把工作交出去的那个，在
``job.state`` 上逐字相同。所以那几条用例同时断 ``outside.calls``（``tests/fakes.py``
的记账替身）与存储里那几条映射 —— 一个「碰了但结果是幂等的」实现会让所有基于
返回值的断言照旧全绿。
"""

from __future__ import annotations

import json

import pytest

from marketops.adapters.profile_loader import load_profile
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.config import Settings
from marketops.domain.enums import JobInputKind, StopReason
from marketops.domain.profile import SceneFiles
from marketops.domain.errors import JobStateError
from marketops.domain.findings import Findings
from marketops.domain.util import sha256_text
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.fakes import FakeDispatcher, synthetic_dossiers
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload, write_accounts

SCENE = "uk-acme-natural-grass"
SUBJECT = "acme-devon-01"
HOLDER = "worker-a"


def _scene(settings: Settings) -> SceneFiles:
    """手搓编译时读的那批字节 —— 与工具面读的是**同一份**。

    几条用例先直接调 ``compile_work_items`` 摆好落点、再走 ``control_job``。
    它们冻的必须是**真的**那一份：``resume`` 会重新编译同一批工作单元，而钉着
    另一份规则的批次会被 ``repin_refusal`` 拒绝（那是 H3b 的判据，住在
    ``tests/test_job_contract.py``，不是这里要测的东西）。用一个编出来的版本会
    把这些用例变成「resume 一律失败」的测试，而它们自己看起来还是绿的 ——
    它们断的是 resume 之后的**状态**。
    """
    return load_profile(settings).scene_files


INPUT_REF = "devon-2026-09"


@pytest.fixture
def store_factory(settings: Settings):
    return lambda: SqliteJobStore(settings.database_path)


@pytest.fixture
def inputs(settings: Settings):
    """``_admit`` 用的那份清单，**恰好一个账号**（``SUBJECT``）。

    一个账号而不是两个：``compile_work_items`` 的幂等判据比对**整个序列**，
    而本文件里有几条用例先手搓 ``compile_work_items(job_id, […], scene=...)`` 再走
    resume —— 那时 resume 会重新解析这份清单。清单里多一个账号，那些用例就会
    撞上「这个作业已经有一批**不同**的 work item」，而它们测的本来不是那件事。
    """
    return write_accounts(settings, INPUT_REF, (SUBJECT,))


@pytest.fixture
def outside() -> FakeDispatcher:
    return FakeDispatcher()


@pytest.fixture
def control(
    settings: Settings, store_factory, inputs, outside: FakeDispatcher
):
    token, digest = mint("cli", [Role.CONTROLLER])
    path = settings.data_root / "test-tokens-cli.json"
    path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, "cli", ["controller"]))
        ),
        encoding="utf-8",
    )
    ring = TokenRing.from_file(path)

    def call(
        tool: str,
        arguments: dict | None = None,
        *,
        with_dispatcher: bool = False,
    ):
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            calls=((tool, arguments or {}),),
            want_tool_list=False,
            dispatcher=outside if with_dispatcher else None,
            jobs=store_factory,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome.call(tool)

    return call


def _admit(settings: Settings, store_factory, *, key: str = "discovery-v1-arm-b"):
    return store_factory().admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref=INPUT_REF,
        idempotency_key=key,
        request_sha256=sha256_text(key),
    )


def _state(settings: Settings, store_factory, job_id: str) -> str:
    return store_factory().get(job_id).state.value


# ----------------------------------------------------------------------
# intent 与 job 是两件事
# ----------------------------------------------------------------------
def test_the_receipt_separates_the_intent_from_the_state(
    settings, store_factory, control
) -> None:
    """回执里**两列**：你要求了什么，它现在是什么。

    合成一个 ``state`` 字段之后，「我按下了暂停」与「它真的暂停了」不可区分 ——
    而这两种情况在报告里长得一样，处置却完全不同（一个要重试，一个要去看为什么
    没生效）。
    """
    job = _admit(settings, store_factory)

    body = control(
        "control_job",
        {
            "job_id": job.job_id,
            "action": "pause",
            "expected_state": "queued",
            "reason": "操作员看到上游配额异常，先停住",
        },
    ).as_json()

    assert body["intent"] == {
        "action": "pause",
        "target_state": "paused",
        "expected_state": "queued",
        "reason": "操作员看到上游配额异常，先停住",
    }
    assert body["job"]["state"] == "paused"
    assert body["job"]["reason"] == "操作员看到上游配额异常，先停住"


def test_resume_goes_back_to_queued(settings, store_factory, control) -> None:
    job = _admit(settings, store_factory)
    control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    )

    body = control(
        "control_job",
        {"job_id": job.job_id, "action": "resume", "expected_state": "paused"},
    ).as_json()

    assert body["job"]["state"] == "queued"
    assert body["job"]["reason"] == ""


# ----------------------------------------------------------------------
# resume 不是一次纯状态写入：它要把暂停期间没交出去的工作交出去
# ----------------------------------------------------------------------
def test_pause_and_cancel_receipts_have_no_dispatch_block(
    settings, store_factory, control
) -> None:
    """``dispatch`` 那一格**只在 resume 的回执里**出现。

    给它一个恒为空的格子是不行的：那会被读成「派发过了，一件都没成」—— 与
    「这个动词不派发」在报告里是两件事，而前者的处置是去看执行器。
    """
    job = _admit(settings, store_factory)

    paused = control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    ).as_json()
    assert "dispatch" not in paused

    resumed = control(
        "control_job", {"job_id": job.job_id, "action": "resume", "expected_state": "paused"}
    ).as_json()
    assert "dispatch" in resumed

    cancelled = control(
        "control_job",
        {
            "job_id": job.job_id,
            "action": "cancel",
            "expected_state": "queued",
            "reason": "不做了",
        },
    ).as_json()
    assert "dispatch" not in cancelled


def test_resume_hands_out_what_the_pause_held_back(
    settings, store_factory, control, outside: FakeDispatcher
) -> None:
    """暂停挡住派发，所以 resume 必须**真的把它们交出去**。

    判别力在 ``outside`` 上：一个只把状态写回 ``queued`` 的实现，回执里的
    ``job.state`` 与这里逐字相同 —— 而外面一个任务都没有，作业就那样挂着，
    报告上写着「已恢复」。
    """
    job = _admit(settings, store_factory)
    control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    )
    assert outside.calls == []

    body = control(
        "control_job",
        {"job_id": job.job_id, "action": "resume", "expected_state": "paused"},
        with_dispatcher=True,
    ).as_json()

    handed = body["dispatch"]
    assert handed["executor_configured"] is True
    assert handed["counts"] == {"already_mapped": 0, "adopted": 0, "created": 1}
    assert handed["handed_out"] == 1
    assert outside.calls == ["find", "create"]
    store = store_factory()
    (item,) = store.work_items(job.job_id)
    assert store.execution_ref(item.work_item_id) is not None


def test_resume_carries_the_plan_step_and_refuses_one_this_plan_never_opened(
    settings, store_factory, control, outside: FakeDispatcher
) -> None:
    """§B16 关闭条件 7 在 **resume** 上的落点：回执回显那一步，而且拒绝发生在写状态之前。

    正控：``resume`` 是 派发那一锚的第二条来路（``submit_research`` 是第一条）——
    它同样真的去外部建任务，所以同样要说得出服务的是计划里的哪一步。只断「回执里有
    这一格」会让一个恒填常量的实现全绿，所以这里同时断 ``outside.calls``。

    反例：``research.skill.v1`` 注册过、而这一份 Scene 的声明**没有**给它入口。它被
    拒的时候，``_admit`` 那个作业必须**还是 ``paused``** —— 一次注定被拒的 resume
    不该先把状态写成 ``queued`` 再报错，那样调用方读到的是一次失败、盘上却是一次
    已经生效的状态写入。断言「工具报了个错」对这一条是无力的：写完之后才校验的实现
    照样报错，而它与门口那种实现在**状态**上分得开。
    """
    opened = _admit(settings, store_factory, key="discovery-v1-arm-step")
    control(
        "control_job",
        {"job_id": opened.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    )
    body = control(
        "control_job",
        {
            "job_id": opened.job_id,
            "action": "resume",
            "expected_state": "paused",
            "source_method": "search.engine.v1",
        },
        with_dispatcher=True,
    ).as_json()

    assert body["dispatch"]["source_method"] == "search.engine.v1"
    assert body["dispatch"]["handed_out"] == 1
    assert outside.calls == ["find", "create"]

    refused = _admit(settings, store_factory, key="discovery-v1-arm-step-b")
    control(
        "control_job",
        {"job_id": refused.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    )
    taken = control(
        "control_job",
        {
            "job_id": refused.job_id,
            "action": "resume",
            "expected_state": "paused",
            "source_method": "research.skill.v1",
        },
        with_dispatcher=True,
    )

    assert taken.is_error, "计划里没有这一步，resume 却还是发生了"
    assert "计划" in taken.text, taken.text
    assert _state(settings, store_factory, refused.job_id) == "paused", (
        "被拒的 resume 把状态写回去了 —— 调用方读到一次失败，盘上却是一次生效的写入"
    )
    assert outside.calls == ["find", "create"], "被拒的 resume 碰了外部"


def test_resume_that_hands_out_nothing_says_so(settings, store_factory, control) -> None:
    """没有执行器的部署里 resume：状态真的回去了，而 ``dispatch`` 如实说没交出去。

    这一格与上一条合起来才是完整的判据 —— 少了它，「resume 会派发」与
    「resume 什么也不做但回执照旧好看」在**有**执行器的环境里才分得开，
    而那个环境不是每个部署都有。
    """
    job = _admit(settings, store_factory)
    control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    )

    body = control(
        "control_job", {"job_id": job.job_id, "action": "resume", "expected_state": "paused"}
    ).as_json()

    assert body["job"]["state"] == "queued"
    assert body["dispatch"]["executor_configured"] is False
    assert body["dispatch"]["handed_out"] == 0
    assert "没有把执行器构造出来" in body["dispatch"]["blocker"]


def test_resume_rereads_the_inputs_that_were_missing(settings, store_factory, control) -> None:
    """被阻塞的作业只有 resume 一条路 —— 而 resume 会**重新读一次**输入。

    这是 ``BLOCKED`` 唯一的解除路径：受理不会往回写状态（受理侧有一条判据钉着
    这一点），所以补齐清单之后必须有第二个动作能把作业救回来。少了这条，
    ``BLOCKED`` 是一个终态，而它被写出来的时候说的是「等条件具备」。
    """
    admitted = control(
        "submit_research",
        {
            "scene_id": SCENE,
            "input_kind": "known_accounts",
            "input_ref": "arrives-later",
            "idempotency_key": "blocked-then-resumed",
        },
    ).as_json()
    assert admitted["state"] == "blocked", "先要真的被阻塞住，否则这条判据测的是别的事"
    store = store_factory()
    assert store.work_items(admitted["job_id"]) == ()

    write_accounts(settings, "arrives-later", (SUBJECT,))
    body = control(
        "control_job",
        {"job_id": admitted["job_id"], "action": "resume", "expected_state": "blocked"},
    ).as_json()

    assert body["job"]["state"] == "queued"
    assert store.get(admitted["job_id"]).reason == "", "恢复要把那条理由清掉"
    assert len(store.work_items(admitted["job_id"])) == 1, "resume 重新解析并编译了这份清单"


def test_resume_that_still_cannot_compile_goes_back_to_blocked(
    settings, store_factory, control
) -> None:
    """输入仍然产不出来：作业回到 ``BLOCKED``，理由是**那条**理由。

    一个照常 ``queued``、然后什么都没发生的作业是这里最坏的形态 —— 报告上它
    「已恢复」，而工作单元一件都没有。判别力在这一格：状态真的回去了，而且
    理由与阻塞那一次是同一句话（同一处产生，见 ``_resolve_and_compile``）。
    """
    job = _admit(settings, store_factory)
    control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    )
    (settings.inputs_dir / f"{INPUT_REF}.yaml").unlink()

    body = control(
        "control_job", {"job_id": job.job_id, "action": "resume", "expected_state": "paused"}
    ).as_json()

    assert body["job"]["state"] == "blocked"
    assert str(settings.inputs_dir / f"{INPUT_REF}.yaml") in body["job"]["reason"]


# ----------------------------------------------------------------------
# CAS：两个控制端同时按下
# ----------------------------------------------------------------------
def test_a_stale_expected_state_is_refused_whole(settings, store_factory, control) -> None:
    """CAS 不匹配 ⇒ 整次拒绝，并把**读到的那一个**报回去。

    没有这一条，两个控制端同时 pause / cancel 时最后一个写的赢，而两边都以为
    自己成功了 —— 报告里留下的是第二次按下，第一次按下的那个人看不到自己的命令
    被吃掉了。

    「整次拒绝」这半句同样承重：一个「部分受理」的答案在这条路径上没有意义，
    因为状态只有一个。
    """
    job = _admit(settings, store_factory)
    control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "先停"},
    )

    outcome = control(
        "control_job",
        {"job_id": job.job_id, "action": "cancel", "expected_state": "queued", "reason": "改主意了"},
    )

    assert outcome.is_error
    assert "CAS" in outcome.text
    assert "paused" in outcome.text
    # 状态没被动过 —— 拒绝是真的拒绝。
    assert _state(settings, store_factory, job.job_id) == "paused"


def test_expected_state_must_come_from_the_closed_set(settings, store_factory, control) -> None:
    """``expected_state`` 是形状，不是自由文本。

    一个拼错的状态名如果被当成「不匹配」，调用方拿到的会是一句 CAS 冲突 ——
    而它据此会去重读状态、再试一次，永远试不对。
    """
    job = _admit(settings, store_factory)

    outcome = control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "running", "reason": "停"},
    )

    assert outcome.is_error
    assert "queued" in outcome.text
    assert "cancelled" in outcome.text


# ----------------------------------------------------------------------
# 三个动词，以及为什么没有第四个
# ----------------------------------------------------------------------
def test_blocked_is_not_a_control_action(settings, store_factory, control) -> None:
    """``blocked`` 在转移表里，但**不是**控制端动词。

    一个作业被阻塞是**观察到**的一件事，不是谁的一句命令。把它做成一个可调的
    动作，调用方就能声明一个没有任何证据的阻塞 —— 而它在报告里与一次真的阻塞
    长得一样。§6 对 cancel 要求说明「what actually stopped」，同一条纪律对
    blocked 只会更严。
    """
    job = _admit(settings, store_factory)

    outcome = control(
        "control_job",
        {"job_id": job.job_id, "action": "block", "expected_state": "queued", "reason": "上游说不"},
    )

    assert outcome.is_error
    assert "block" in outcome.text
    assert "pause" in outcome.text
    assert _state(settings, store_factory, job.job_id) == "queued"


def test_cancelled_is_terminal(settings, store_factory, control) -> None:
    """``CANCELLED`` 没有出边 —— 取消之后不能恢复。

    一个能被恢复的取消不是取消：报告里那个作业会时隐时现，而「它到底跑没跑完」
    成了一个取决于读的时机的问题。
    """
    job = _admit(settings, store_factory)
    control(
        "control_job",
        {"job_id": job.job_id, "action": "cancel", "expected_state": "queued", "reason": "不做了"},
    )

    outcome = control(
        "control_job",
        {"job_id": job.job_id, "action": "resume", "expected_state": "cancelled"},
    )

    assert outcome.is_error
    assert "不是合法转移" in outcome.text
    assert "（终态）" in outcome.text


def test_an_unknown_job_is_refused(settings, store_factory, control) -> None:
    outcome = control(
        "control_job",
        {"job_id": "0" * 32, "action": "cancel", "expected_state": "queued", "reason": "不做了"},
    )

    assert outcome.is_error
    assert "没有这个作业" in outcome.text


# ----------------------------------------------------------------------
# observed：实际停了什么
# ----------------------------------------------------------------------
def test_the_receipt_says_what_is_still_running(settings, store_factory, control) -> None:
    """**pause 不收回在途租约** —— 所以回执必须把在途的点名。

    §6 逐字「Pause normally prevents new dispatch and lets accepted in-flight
    results settle」。回执如果只说「已暂停」，读的人会以为那些在途的工作停了 ——
    而它们还在跑，且没有任何一处会告诉他。
    """
    job = _admit(settings, store_factory)
    store = store_factory()
    (item,) = store.compile_work_items(
        job.job_id, synthetic_dossiers(SUBJECT), scene=_scene(settings)
    )
    store.map_execution(item.work_item_id, dispatch_task_id="dispatch-task-0001")
    store.lease(item.work_item_id, holder=HOLDER)

    body = control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停住"},
    ).as_json()

    observed = body["observed"]
    assert observed["work_items"] == 1
    assert observed["in_flight"] == 1
    assert observed["in_flight_holders"] == [HOLDER]
    assert observed["settled"] == 0
    assert observed["not_started"] == 0
    # 租约**还在** —— 一句「已暂停」不改变这个事实。
    assert store.attempts(item.work_item_id)[0].is_live


def test_observed_separates_settled_from_in_flight(settings, store_factory, control) -> None:
    """三类工作必须分开数：停过的、在途的、还没开始的。

    合成一个「进行中」之后，一个跑完了 8 件、还剩 1 件在途的作业与一个刚开始
    1 件的作业会报同一个数 —— 而「已完成多少」正是取消时唯一要紧的那个数。
    """
    job = _admit(settings, store_factory)
    store = store_factory()
    first, second, third = store.compile_work_items(
        job.job_id,
        synthetic_dossiers(SUBJECT, "acme-devon-02", "acme-devon-03"),
        scene=_scene(settings),
    )
    store.map_execution(first.work_item_id, dispatch_task_id="dispatch-task-0001")
    store.map_execution(second.work_item_id, dispatch_task_id="dispatch-task-0002")
    attempt = store.lease(first.work_item_id, holder=HOLDER)
    store.settle(
        attempt.attempt_id,
        stop_reason=StopReason.GOAL_MET,
        findings=Findings(gaps=("没能确认它的主场容量",)),
    )
    store.lease(second.work_item_id, holder="worker-b")
    # third 既不派发也不领 —— 它是「还没开始」。

    body = control(
        "control_job",
        {"job_id": job.job_id, "action": "cancel", "expected_state": "queued", "reason": "够了"},
    ).as_json()

    observed = body["observed"]
    assert observed["work_items"] == 3
    assert observed["settled"] == 1
    assert observed["in_flight"] == 1
    assert observed["in_flight_holders"] == ["worker-b"]
    assert observed["not_started"] == 1
    assert observed["settled_work_item_ids"] == [first.work_item_id]
    assert observed["truncated"] is False


def test_observed_reports_the_holder_not_a_count_of_leases(settings, store_factory, control) -> None:
    """在途那一格带**持有者**。

    一个数字答不了「我该去叫停谁」—— 而收回一条租约需要 ``release(reason=...)``，
    它是按 attempt 走的，所以「谁持有」是能不能行动的分界线。
    """
    job = _admit(settings, store_factory)
    store = store_factory()
    first, second = store.compile_work_items(
        job.job_id, synthetic_dossiers(SUBJECT, "acme-devon-02"), scene=_scene(settings)
    )
    for item, task in ((first, "dispatch-task-0001"), (second, "dispatch-task-0002")):
        store.map_execution(item.work_item_id, dispatch_task_id=task)
    store.lease(first.work_item_id, holder=HOLDER)
    store.lease(second.work_item_id, holder="worker-b")

    observed = control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停住"},
    ).as_json()["observed"]

    assert observed["in_flight_holders"] == [HOLDER, "worker-b"]


# ----------------------------------------------------------------------
# 跨工具：pause 真的挡住了新派发
# ----------------------------------------------------------------------
def test_pause_actually_blocks_new_dispatch(settings, store_factory, control) -> None:
    """§6「Pause normally prevents new dispatch」在**服务面**上的执行器。

    规则住在存储层（``ResearchJob.admits_dispatch``），而一条只写在存储层的边界
    从工具这边是看不见的。这里按下暂停，然后**真的去派发一次** —— 它必须拒绝。
    一条不挡事的「暂停」不是暂停，是一个字段。
    """
    job = _admit(settings, store_factory)
    store = store_factory()
    (item,) = store.compile_work_items(
        job.job_id, synthetic_dossiers(SUBJECT), scene=_scene(settings)
    )
    control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停住"},
    )

    with pytest.raises(JobStateError) as refused:
        store.map_execution(item.work_item_id, dispatch_task_id="dispatch-task-0001")

    # 消息里带**理由**（"停住"）—— 一句只说「不放行」的拒绝会让人去查配置。
    assert "不放行新的派发" in str(refused.value)
    assert "停住" in str(refused.value)
    # 而恢复之后同一次派发**能**过 —— 否则上面那条断言可能只是「永远是拒的」。
    control(
        "control_job",
        {"job_id": job.job_id, "action": "resume", "expected_state": "paused"},
    )
    assert store.map_execution(item.work_item_id, dispatch_task_id="dispatch-task-0001")


def test_cancel_blocks_new_leases_too(settings, store_factory, control) -> None:
    """取消之后领不到活 —— 而且理由说的是**取消**，不是别的。

    拒绝的类型是 ``JobStateError`` 而不是 ``LeaseError``，这是对的：出问题的不是
    这条租约（它本来完全合法），是这个作业的状态。断言这一点，是因为把它当成一次
    「租约冲突」会让调用方去重试，而这条路径上重试永远不会成功。
    """
    job = _admit(settings, store_factory)
    store = store_factory()
    (item,) = store.compile_work_items(
        job.job_id, synthetic_dossiers(SUBJECT), scene=_scene(settings)
    )
    store.map_execution(item.work_item_id, dispatch_task_id="dispatch-task-0001")
    control(
        "control_job",
        {"job_id": job.job_id, "action": "cancel", "expected_state": "queued", "reason": "不做了"},
    )

    with pytest.raises(JobStateError) as refused:
        store.lease(item.work_item_id, holder=HOLDER)

    assert "已取消" in str(refused.value)


# ----------------------------------------------------------------------
# 结算之后没能回传的那些件：谁来收场
# ----------------------------------------------------------------------
def test_the_sweep_does_not_read_items_that_are_still_running(
    settings, store_factory, control, outside: FakeDispatcher
) -> None:
    """刚交出去、还没结算的件**一次都不该被读**。

    收敛判的是「看板对不对」，而一个还在做的件的「对」还没写出来 —— 替它下一个
    结论就是抢在它前面写结局。判别力在 ``outside.calls`` 上：一个「把每一件都读
    一遍再挑」的实现，回执里的那几个数字与本条逐字相同，而外部多出了 N 次查询。

    顺带钉住 ``unsettled`` 那一格的存在理由：``considered`` 是 0 而 ``unsettled``
    是 1，两个数字一起才是「这一趟什么都没读，因为还没有东西可收敛」。
    """
    job = _admit(settings, store_factory)
    control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    )
    outside.calls.clear()

    body = control(
        "control_job",
        {"job_id": job.job_id, "action": "resume", "expected_state": "paused"},
        with_dispatcher=True,
    ).as_json()

    sweep = body["dispatch"]["board_convergence"]
    assert sweep["ran"] is True
    assert sweep["considered"] == 0
    assert sweep["unsettled"] == 1
    assert sweep["items"] == []
    assert outside.calls == ["find", "create"], (
        "派发那一趟之外，收敛扫描不该再去读任何一件还没结算的工作"
    )


def test_resume_lands_a_settlement_whose_write_back_never_happened(
    settings, store_factory, control, outside: FakeDispatcher
) -> None:
    """**2026-09-22 那次运行的形状**：结算落库了，回传一次都没发生。

    构造的就是那次真实的过程 —— 工作单元在外面有一条 ``ready`` 的任务，而本地的
    结算**已经落库**（那次是进程死在结算与回传之间，外部因此判了
    ``exited-not-landed``）。于是库里有结局、外面还是 ``ready``，而外部那套按
    ``ready`` 认「还没做」。

    这一次收场的是 ``resume`` 之后的收敛扫描，而它收的是**一个 job id**：没有
    任何人带着上一次的理由回来。判据落在外面的那行真的变成的样子上
    （``outside.tasks``）与那次 CAS 的两端上，不是落在回执的自述上。
    """
    job = _admit(settings, store_factory)
    store = store_factory()
    control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    )
    control(
        "control_job",
        {"job_id": job.job_id, "action": "resume", "expected_state": "paused"},
        with_dispatcher=True,
    )
    (item,) = store.work_items(job.job_id)
    ref = store.execution_ref(item.work_item_id)
    assert ref is not None
    task_id = ref.dispatch_task_id
    attempt = store.lease(item.work_item_id, holder=HOLDER)
    store.settle(
        attempt.attempt_id,
        stop_reason=StopReason.BLOCKED,
        findings=Findings(gaps=("上游要人工过一遍",)),
    )
    assert outside.tasks[task_id].status == "ready", "这一格要的是结算了、外面还不知道"

    control(
        "control_job",
        {"job_id": job.job_id, "action": "pause", "expected_state": "queued", "reason": "停"},
    )
    body = control(
        "control_job",
        {"job_id": job.job_id, "action": "resume", "expected_state": "paused"},
        with_dispatcher=True,
    ).as_json()

    sweep = body["dispatch"]["board_convergence"]
    assert sweep["considered"] == 1
    assert sweep["pushed"] == 1
    assert sweep["pending"] == 0
    assert outside.transitions == [(task_id, "ready", "needs-human")]
    assert outside.tasks[task_id].status == "needs-human"

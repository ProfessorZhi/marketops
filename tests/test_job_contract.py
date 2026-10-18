"""``JobStore`` 的契约判据 —— §6「持久执行」的可执行版本。

要守的失败只有一种（同一件事被做两遍），但它有**六张脸，而且每一张都不报错**：

1. 两个控制端同时受理同一个请求 —— 两次研究、两份账单；
2. 同一批输入被编译两次 —— 报告上显示「候选多了一倍」；
3. 两个派发者给同一个 work item 建了两个 Quay 任务 —— §8B 逐字禁止的
   duplicate expensive work；
4. 两个 worker 领到同一个 work item —— 同上，而且**两边都会以为自己是对的**；
5. 一个被取消的作业还在收 findings —— 报告里多出一批没人授权的工作；
6. 同一个 attempt 结了两次、产出的东西不一样 —— 第二次拿到 ``replayed: true``，
   调用方读到「我的结果已经在盘上了」，而盘上是另一份；
7. 一批工作单元被按**另一份**规则重新编译 —— §6 逐字要 Running tasks retain
   their pinned guidance，而重钉之后盘上只剩一个版本，没人能说出那件正在飞的工作
   当初是按哪一份开始的。

七条各有一组判据，外加**正对照**证明这些判据真的会红 —— 一条在判据恒假时仍然
全绿的测试，看起来和一条真的在守事的测试一模一样。

* ``test_the_exclusivity_criterion_actually_discriminates`` —— 排他租约；
* ``test_the_evidence_criterion_actually_discriminates`` —— 结论必须挂证据；
* ``test_the_count_criterion_actually_discriminates`` —— 重复项不能变成计数。

## 为什么两个实现跑同一份

``SqliteJobStore``（跨进程，靠 ``BEGIN IMMEDIATE``）与 ``InMemoryJobStore``（测试
替身）。它在这里通过而在 SQLite 那里失败，才是「这是一个契约」有意义的样子 ——
B3 立的判据是**端口在第二个实现出现时才提取**，这里正好两个。

## 刻意**不**在这里的判据

* **并发**。真的守得住靠的是 ``BEGIN IMMEDIATE``，那是 SQLite 的性质，不是契约
  的性质（内存那份没有锁，也不打算有）。它在
  ``tests/test_budget_ledger.py`` 那一族里，连同一条把「读-改-写会超支」复现出来
  的正对照。这里的判据全部是单进程就能判死的：**两个实现都必须拒绝第二次写入**，
  与它们有没有并发无关。
* **版本与迁移**。那是关于**库文件**的，与契约无关。

## 为什么记录对着临时文件测，而不是对着 ``Settings``

``SqliteJobStore`` 收 ``db_path``。用 ``Settings`` 会让每条测试先构造一份完整配置
（还要读环境变量），而作业记录是纯状态机 —— 它不该需要知道自己是哪个 profile
跑出来的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryJobStore
from marketops.adapters.sqlite.db import session, write_transaction
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.domain.enums import (
    JOB_TRANSITIONS,
    AttemptState,
    JobInputKind,
    JobState,
    StopReason,
)
from marketops.domain.errors import IdempotencyConflictError, JobStateError, LeaseError
from marketops.domain.findings import Finding, Findings
from marketops.domain.job import (
    ExecutionRef,
    ResearchJob,
    WorkItem,
    attempt_id,
    job_id,
    request_fingerprint,
    work_item_id,
)
from marketops.domain.subject import SubjectDossier
from marketops.domain.util import sha256_text
from marketops.ports.jobs import JobStore
from tests.fakes import synthetic_dossiers, synthetic_scene_files

SCENE = "uk-acme-natural-grass"
KEY = "discovery-v1-arm-b"
SUBJECTS = ("acct-0001", "acct-0002")

#: 编译时冻住的规则。**这个文件里它是合成的**，而且刻意的：
#:
#: 真快照是 profile 目录那一批文件（``tests/test_profiles.py`` 那一节），而本文件的
#: 判据全部是关于**存储层是不是一个忠实的载体** —— 「传进去的那一个是不是原样
#: 回得来」，不是「那个值算得对不对」。去读真目录会让这些判据依赖一个与它们无关
#: 的东西，而那个东西一变（往 profile 里加一行注释就会变），十几条存储判据会一起
#: 红，红的原因却与被测的行为无关。
#:
#: 但**它不能是一串编出来的十六进制**。``SCENE_FILES.version`` 是那批字节的摘要，
#: 而「版本」这件事的全部意义就在这里：一个说来路不明的版本号，正是收口之前盘上
#: 钉着的那种东西 —— 说不出指称对象。合成快照给的是**另外一批字节**（不来自任何
#: profile 目录），所以它算出的版本同样不等于任何真 profile 的版本，同时又是真的。
SCENE_FILES = synthetic_scene_files()
SCENE_VERSION = SCENE_FILES.version

#: 另一批字节。与上一份**必须不同** —— 重新编译那几条判据要的是一条真的分歧，
#: 而拿同一份快照比两次证明不了任何事。
OTHER_SCENE_FILES = synthetic_scene_files(tag="other")
OTHER_SCENE_VERSION = OTHER_SCENE_FILES.version

#: 编译的输入：``SUBJECTS`` 那一串 id 的**身份**。两者分开留着，是因为判据里
#: 要断的是 ``subject_ref``（存的是 id）而不是身份本身。
DOSSIERS = synthetic_dossiers(*SUBJECTS)

#: 两条 32 位十六进制的证据引用 —— 形状与真实的证据 id 一致（内容寻址的
#: ``sha256[...][:32]``）。
EVIDENCE_A = sha256_text("page-1")[:32]
EVIDENCE_B = sha256_text("page-2")[:32]

#: 「跑过了，什么都没找到」。§8B 逐字：zero is an honest business outcome。
NOTHING = Findings()

#: 一次有产出的结算。
FOUND = Findings(
    observations=(Finding(statement="这个场地用的是天然草", evidence_refs=(EVIDENCE_A,)),),
    candidates=(Finding(statement="可能是 Example FA 的注册俱乐部", evidence_refs=(EVIDENCE_B,)),),
    gaps=("没能确认它的主场容量",),
)


def _request(tag: str = "r1") -> str:
    """一个形状合法的请求散列。``admit`` 会校验它是 64 位小写十六进制。"""
    return sha256_text(tag)


#: 契约判据要跑过哪些实现。
IMPLEMENTATION_PARAMS = ("sqlite", "memory")


def test_the_contract_runs_against_every_implementation() -> None:
    """实现清单缩水时「两个实现都通过」会变成「一个实现通过」。

    ``params`` 是一个可以被删掉的名字：删掉 ``memory`` 之后整份契约照样全绿，
    而「这是一个契约」这句话就没有依据了 —— 与
    ``tests/test_budget_contract.py`` 里那条同一个道理。
    """
    assert set(IMPLEMENTATION_PARAMS) == {"sqlite", "memory"}


@pytest.fixture(params=IMPLEMENTATION_PARAMS)
def store(request: pytest.FixtureRequest, tmp_path: Path) -> JobStore:
    """两个实现各跑一遍。新增实现时这里是唯一要改的地方。"""
    if request.param == "sqlite":
        return SqliteJobStore(tmp_path / "marketops.db")
    return InMemoryJobStore()


# ----------------------------------------------------------------------
# 夹具：一个已经派发好的 work item
# ----------------------------------------------------------------------
def _admitted(store: JobStore, *, key: str = KEY, tag: str = "r1") -> ResearchJob:
    return store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.DISCOVERY_SCOPE,
        input_ref="devon-2026-09",
        idempotency_key=key,
        request_sha256=_request(tag),
    )


def _dispatched(store: JobStore, *, holder: str = "worker-a") -> tuple[ResearchJob, WorkItem]:
    """受理 → 编译 → 派发。返回第一个 work item（还没出租）。"""
    job = _admitted(store)
    items = store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)
    store.map_execution(items[0].work_item_id, quay_task_id="TASK-001")
    return job, items[0]


# ----------------------------------------------------------------------
# 一、受理：同一个键不会受理两次
# ----------------------------------------------------------------------
def test_the_same_key_returns_the_same_job(store: JobStore) -> None:
    first = _admitted(store)
    again = _admitted(store)

    assert first == again
    assert first.job_id == job_id(SCENE, KEY)


def test_reusing_a_key_with_different_input_is_a_conflict(store: JobStore) -> None:
    """幂等键的承诺是「重发不执行两次」，不是「同一个键可以改内容」。"""
    _admitted(store)
    with pytest.raises(IdempotencyConflictError):
        _admitted(store, tag="r2")


def test_the_same_key_under_another_scene_is_another_job(store: JobStore) -> None:
    """Scene 是这部份身份的一部分，不是请求的一个字段（§2 逐字：references a
    Scene plus one input kind）。

    反过来（两个 Scene 共用一个键就报冲突）会让「同一个流程在两个国家跑」
    永远只能用一次那个键，而调用方别无选择只能另造键名 —— 那正是幂等键开始
    退化的地方。
    """
    first = _admitted(store)
    other = store.admit(
        scene_id="de-acme-natural-grass",
        input_kind=JobInputKind.DISCOVERY_SCOPE,
        input_ref="devon-2026-09",
        idempotency_key=KEY,
        request_sha256=_request(),
    )
    assert other.job_id != first.job_id


def test_a_malformed_request_hash_is_refused(store: JobStore) -> None:
    """形状不对的散列不会报错，它只会让**每一个**请求都与别的请求不同。

    于是「重发」被读成「新请求」—— 而这条旁路没有任何一处会红。所以形状在入口
    就判死。
    """
    with pytest.raises(ValueError):
        store.admit(
            scene_id=SCENE,
            input_kind=JobInputKind.DISCOVERY_SCOPE,
            input_ref="devon-2026-09",
            idempotency_key="k",
            request_sha256=_request().upper(),
        )


@pytest.mark.parametrize("field", ["scene_id", "input_ref", "idempotency_key"])
def test_an_empty_identity_field_is_refused(store: JobStore, field: str) -> None:
    """空幂等键会让「重试」与「新请求」不可区分；空场景会让两组研究共用一个 id。"""
    kwargs = dict(
        scene_id=SCENE,
        input_kind=JobInputKind.DISCOVERY_SCOPE,
        input_ref="devon-2026-09",
        idempotency_key=KEY,
        request_sha256=_request(),
    )
    kwargs[field] = "   "
    with pytest.raises(ValueError):
        store.admit(**kwargs)


def test_an_unknown_job_reads_as_none_not_an_error(store: JobStore) -> None:
    assert store.get("0" * 32) is None


# ----------------------------------------------------------------------
# 二、状态：CAS
# ----------------------------------------------------------------------
def test_a_stale_expected_state_is_refused_and_nothing_is_written(store: JobStore) -> None:
    """读到的状态与据此写下的状态必须在同一个写事务里。

    没有这一条，两个控制端同时 pause / cancel 时最后一个写的赢，而**两边都以为
    自己成功了**。同一个形状 Quay 自己也逐字回：``refusing to overwrite
    (write() aborted, nothing written to disk)``。
    """
    job = _admitted(store)
    with pytest.raises(JobStateError):
        store.set_state(job.job_id, JobState.CANCELLED, expected=JobState.PAUSED, reason="ops")

    current = store.get(job.job_id)
    assert current is not None and current.state is JobState.QUEUED


@pytest.mark.parametrize("target", sorted({s for v in JOB_TRANSITIONS.values() for s in v}))
def test_cancelled_is_terminal_because_it_has_no_outgoing_edges(
    store: JobStore, target: JobState
) -> None:
    """终态是「没有边」，不是「有一条边但被拒绝」。

    遍历转移表而不写死几个取值：以后给 ``CANCELLED`` 添一条出边时，这条会红 ——
    而写死取值的版本不会。
    """
    job = _admitted(store)
    store.set_state(job.job_id, JobState.CANCELLED, expected=JobState.QUEUED, reason="ops")
    with pytest.raises(JobStateError):
        store.set_state(job.job_id, target, expected=JobState.CANCELLED, reason="想复活")


def test_resume_carries_no_reason(store: JobStore) -> None:
    """恢复不出自任何人的判断，而恢复是可以被随便按下很多次的那个动作。"""
    job = _admitted(store)
    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="ops 手动暂停")
    with pytest.raises(JobStateError):
        store.set_state(job.job_id, JobState.QUEUED, expected=JobState.PAUSED, reason="我觉得可以了")


@pytest.mark.parametrize(
    "state", [JobState.PAUSED, JobState.BLOCKED, JobState.CANCELLED]
)
def test_a_reason_is_mandatory_on_every_stopping_transition(
    store: JobStore, state: JobState
) -> None:
    """§6 逐字：Cancel reports **what actually stopped**。一个没有理由的取消
    无法回答那句话，而它和「跑完了」在报告里长得一样。"""
    job = _admitted(store)
    with pytest.raises(JobStateError):
        store.set_state(job.job_id, state, expected=JobState.QUEUED)

    current = store.get(job.job_id)
    assert current is not None and current.state is JobState.QUEUED


def test_pausing_does_not_close_the_door_on_results(store: JobStore) -> None:
    """§6 逐字：Pause normally prevents new dispatch and **lets accepted
    in-flight results settle**。两个问题分开回答。"""
    job, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="ops")

    settled = store.settle(attempt.attempt_id, stop_reason=StopReason.MANUAL_PAUSE, findings=NOTHING)
    assert settled.attempt.state is AttemptState.SETTLED


def test_setting_the_state_of_an_unknown_job_is_refused(store: JobStore) -> None:
    with pytest.raises(JobStateError):
        store.set_state("0" * 32, JobState.QUEUED, expected=JobState.QUEUED)


# ----------------------------------------------------------------------
# 三、编译
# ----------------------------------------------------------------------
def test_compiling_twice_gives_the_same_work_items(store: JobStore) -> None:
    job = _admitted(store)
    first = store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)
    again = store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)

    assert first == again
    assert [item.ordinal for item in first] == [0, 1]
    assert first[0].work_item_id == work_item_id(job.job_id, 0)


def test_the_subject_order_is_part_of_the_identity(store: JobStore) -> None:
    """顺序决定 ``ordinal``，因而决定 id。**集合相同而顺序不同**是另一批工作，
    不是同一批 —— 静默接受它会让「这一批跑的是哪两个账号」有两个答案。
    """
    job = _admitted(store)
    store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)
    with pytest.raises(JobStateError):
        store.compile_work_items(job.job_id, tuple(reversed(DOSSIERS)), scene=SCENE_FILES)


def test_an_empty_subject_list_is_refused(store: JobStore) -> None:
    """一个没有任何 work item 的作业在报告里长得像「已经做完」。"""
    job = _admitted(store)
    with pytest.raises(ValueError):
        store.compile_work_items(job.job_id, [], scene=SCENE_FILES)


def test_a_cancelled_job_compiles_nothing(store: JobStore) -> None:
    """取消是终态：此后受理的任何工作都不会有人验收。"""
    job = _admitted(store)
    store.set_state(job.job_id, JobState.CANCELLED, expected=JobState.QUEUED, reason="ops")
    with pytest.raises(JobStateError):
        store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)


def test_work_items_are_ordered_and_scoped_to_their_job(store: JobStore) -> None:
    first = _admitted(store, key="k1")
    second = _admitted(store, key="k2", tag="r2")
    store.compile_work_items(first.job_id, DOSSIERS, scene=SCENE_FILES)

    assert [i.subject_ref for i in store.work_items(first.job_id)] == list(SUBJECTS)
    assert store.work_items(second.job_id) == ()
    assert store.get_work_item("0" * 32) is None


# ----------------------------------------------------------------------
# 三之二、编译时钉住的 Scene 版本
# ----------------------------------------------------------------------
def test_the_compiled_batch_records_the_version_it_was_compiled_under(
    store: JobStore,
) -> None:
    """§6 逐字：Running tasks retain their pinned guidance。

    这条断的是**载体**：编译时给的那个版本，事后按 work item 取得回来。它很薄，
    但它是下面三条的前提 —— 一个不收版本的存储层，下面三条都会以「返回 None」
    的方式静默通过（``None`` 比较起来不抛，只是永远不等）。
    """
    job = _admitted(store)
    (item,) = store.compile_work_items(
        job.job_id, DOSSIERS[:1], scene=SCENE_FILES
    )

    pin = store.work_item_snapshot(item.work_item_id)
    assert pin is not None
    assert pin.work_item_id == item.work_item_id
    assert pin.scene_version == SCENE_VERSION
    assert pin.pinned_at, "钉住的时间要读得到 —— 报告里那句「从哪一刻起按这一份跑」靠它"


def test_every_work_item_in_the_batch_carries_its_own_pin(store: JobStore) -> None:
    """钉是**逐件**的，不是整个作业一个。

    一个「一个作业一个版本」的形状读起来更省事，而它挡不住这一件事：同一个作业
    的工作单元是在不同时刻被领走的，如果有人改了 profile 之后又编译了第二批
    （今天被 ``repin_refusal`` 拒了，但那是**拒绝**，不是结构上不可能），那么
    按作业记一个版本时，先编译的那一批会跟着后一批一起换版本 —— 而这正是
    「正在飞的工作静默地换了规则」。

    这条判据是上面那条的**覆盖**：只钉 ``ordinal == 0`` 的实现会在上面那条上
    通过（第一件确实钉了），在这里红。
    """
    job = _admitted(store)
    items = store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)

    assert len(items) == 2
    for item in items:
        pin = store.work_item_snapshot(item.work_item_id)
        assert pin is not None, f"{item.work_item_id} 没有钉住的版本"
        assert pin.work_item_id == item.work_item_id
        assert pin.scene_version == SCENE_VERSION


def test_recompiling_under_the_same_version_is_still_idempotent(store: JobStore) -> None:
    """**正对照**：同版本重新编译照旧幂等。

    没有这一条，下面那条「换版本被拒」可能只是「重新编译一律被拒」—— 而一个
    永远拒绝重新编译的实现会让 resume 那条路径整条断掉，却在下面那条判据上
    看起来完全正确。
    """
    job = _admitted(store)
    first = store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)
    again = store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)

    assert first == again


def test_recompiling_under_another_version_is_refused_and_pins_nothing_new(
    store: JobStore,
) -> None:
    """换一个版本重新编译**被拒**，而且**盘上那条钉没动**。

    两条断言各挡一件事：

    * 拒绝 —— 静默重钉之后，一件正在飞的工作会按新规则跑完，而它自己、以及
      任何读报告的人都不知道这件事发生过；
    * 没动 —— 一个「先重钉、再发现自己该拒绝」的实现会把拒绝退回去，而它的
      **返回**看起来与正确实现一模一样。

    为什么不重钉成新的那个版本：钉住的意义就是**不跟着变**。重新编译一批已有的
    工作单元，本身就是要按现在这份规则跑**另一件工作** —— 那是一个新的作业，
    不是对这一个的修改。
    """
    job = _admitted(store)
    (item,) = store.compile_work_items(
        job.job_id, DOSSIERS[:1], scene=SCENE_FILES
    )

    with pytest.raises(JobStateError) as refused:
        store.compile_work_items(
            job.job_id, DOSSIERS[:1], scene=OTHER_SCENE_FILES
        )

    # 理由要说出**两个版本**：只说「版本不对」会让人去翻代码找一个期望值。
    assert SCENE_VERSION in str(refused.value)
    assert OTHER_SCENE_VERSION in str(refused.value)
    pin = store.work_item_snapshot(item.work_item_id)
    assert pin is not None and pin.scene_version == SCENE_VERSION


def test_a_batch_with_no_pin_is_reported_as_unpinned_not_guessed(tmp_path: Path) -> None:
    """这一格复现的是**钉住机制出现之前**编译出来的库。

    没有公开入口能造出「没钉的工作单元」—— ``compile_work_items`` 的写事务把
    工作单元与它的钉写在一起（这正是要它做的事）。所以这里用与真实情况相同的
    方式造它：让那一行**不在**。一条 ``DELETE`` 不是在伪造数据，它是在复现一个
    更早的进程写下的库。

    两件事必须成立：读的时候**返回 None**（不是编一个版本），重新编译的时候
    **拒绝并说明原因**。两件都在，才叫「缺席被面对了」；只做第一件的话，那批
    工作单元会照常 resume，然后按一份没人能指认的规则跑完。
    """
    db_path = tmp_path / "marketops.db"
    store = SqliteJobStore(db_path)
    job = _admitted(store)
    (item,) = store.compile_work_items(
        job.job_id, DOSSIERS[:1], scene=SCENE_FILES
    )
    with session(db_path) as conn, write_transaction(conn):
        conn.execute(
            "DELETE FROM work_item_snapshot WHERE work_item_id = ?", (item.work_item_id,)
        )

    assert store.work_item_snapshot(item.work_item_id) is None

    with pytest.raises(JobStateError) as refused:
        store.compile_work_items(
            job.job_id, DOSSIERS[:1], scene=SCENE_FILES
        )
    assert "钉" in str(refused.value)


def test_a_batch_pinned_but_without_a_subject_is_its_own_absence(tmp_path: Path) -> None:
    """**第二个缺席状态**：钉住了规则，没记下针对谁。

    它与上面那条不是同一件事，所以不能共用一个处置：上面那批说不出「按哪一份
    规则跑」，这一批说得出来（``scene_version`` 就在那一行里），只是从没记过
    「这次研究的是谁」。合成一条拒绝会让处置的人去错地方 —— 一个去找规则、一个
    去找身份，而两处的修法完全不同。

    这一格也复现真实历史：``work_item_guidance`` 那张遗留表回填进新表时，
    ``subject_json`` 就是 ``NULL``（见 ``sqlite/db.py`` 的 DDL）。所以它是**盘上
    真实的取值**，不是构造出来的边界。
    """
    db_path = tmp_path / "marketops.db"
    store = SqliteJobStore(db_path)
    job = _admitted(store)
    (item,) = store.compile_work_items(job.job_id, DOSSIERS[:1], scene=SCENE_FILES)
    with session(db_path) as conn, write_transaction(conn):
        conn.execute(
            "UPDATE work_item_snapshot SET subject_json = NULL WHERE work_item_id = ?",
            (item.work_item_id,),
        )

    snapshot = store.work_item_snapshot(item.work_item_id)
    assert snapshot is not None
    assert snapshot.subject is None
    # 规则那一半**还在** —— 这正是它与上一条的区别所在，也是这里要断的东西：
    # 一个「没有身份就当整条快照不存在」的实现会把版本一起吞掉。
    assert snapshot.scene_version == SCENE_VERSION

    with pytest.raises(JobStateError) as refused:
        store.compile_work_items(job.job_id, DOSSIERS[:1], scene=SCENE_FILES)
    assert "身份" in str(refused.value)


def test_recompiling_under_another_identity_is_refused(store: JobStore) -> None:
    """同一个作业、同一个序号、**换了主体**：那是另一个作业。

    ``account_id`` 相同的检查（上面那条）挡不住这一件事：id 没变、organisation
    从 ``Example School One`` 变成了别的。而身份恰恰是这次要冻住的东西 ——
    如果它能被重编译悄悄改掉，那「受理时冻住的身份」就只是一个说法。
    """
    job = _admitted(store)
    store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)

    renamed = (
        SubjectDossier(
            account_id=DOSSIERS[0].account_id,
            organisation="另一个机构",
            domain=DOSSIERS[0].domain,
            country=DOSSIERS[0].country,
            source_ref=DOSSIERS[0].source_ref,
            input_sha256=DOSSIERS[0].input_sha256,
        ),
        DOSSIERS[1],
    )
    with pytest.raises(JobStateError) as refused:
        store.compile_work_items(job.job_id, renamed, scene=SCENE_FILES)

    # 理由要说出**变了哪一个字段、从什么变成什么** —— 只说「身份不对」会让读的人
    # 去逐字段比对一份他手里没有的输入。
    message = str(refused.value)
    assert "organisation" in message
    assert DOSSIERS[0].organisation in message
    assert "另一个机构" in message


def test_an_unknown_work_item_has_no_pin(store: JobStore) -> None:
    """``None`` 是这一格的**正常**取值，不是「查不到就是坏」。

    没有这一条，一个「未知 id 就抛」的实现会在上面几条上全绿 —— 而它在
    ``get_assignment`` 那条路径上会把一个未知 work item 变成一次 500。
    """
    assert store.work_item_snapshot("0" * 32) is None


# ----------------------------------------------------------------------
# 四、派发：映射写一次
# ----------------------------------------------------------------------
def test_the_mapping_to_quay_is_written_once(store: JobStore) -> None:
    """两个派发者抢同一个 work item 时，第二个撞在已有映射上，而不是悄悄建出
    第二个 Quay 任务 —— 同一件昂贵的事被派发两遍时，**两边都会正常跑完**。
    """
    job = _admitted(store)
    (item,) = store.compile_work_items(job.job_id, DOSSIERS[:1], scene=SCENE_FILES)
    first = store.map_execution(item.work_item_id, quay_task_id="TASK-001")

    assert store.map_execution(item.work_item_id, quay_task_id="TASK-001") == first
    assert isinstance(first, ExecutionRef)
    with pytest.raises(JobStateError):
        store.map_execution(item.work_item_id, quay_task_id="TASK-002")


def test_a_paused_job_dispatches_nothing(store: JobStore) -> None:
    """§6 逐字：Pause normally prevents new dispatch。派发就是这里，没有第二个入口。"""
    job = _admitted(store)
    (item,) = store.compile_work_items(job.job_id, DOSSIERS[:1], scene=SCENE_FILES)
    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="ops")

    with pytest.raises(JobStateError):
        store.map_execution(item.work_item_id, quay_task_id="TASK-001")
    assert store.execution_ref(item.work_item_id) is None


def test_an_unknown_work_item_cannot_be_mapped(store: JobStore) -> None:
    with pytest.raises(JobStateError):
        store.map_execution("0" * 32, quay_task_id="TASK-001")
    assert store.execution_ref("0" * 32) is None


# ----------------------------------------------------------------------
# 五、租约：排他，且只有派发过的才出租
# ----------------------------------------------------------------------
def test_a_work_item_without_a_quay_task_cannot_be_leased(store: JobStore) -> None:
    """§6 逐字：the controller cannot substitute an outer-shell worker launch for
    Quay dispatch。没有 ``ExecutionRef`` 就没有租约 —— 于是「绕过 Quay 直接开一个
    worker」在代码里写不出来。
    """
    job = _admitted(store)
    (item,) = store.compile_work_items(job.job_id, DOSSIERS[:1], scene=SCENE_FILES)

    with pytest.raises(LeaseError):
        store.lease(item.work_item_id, holder="worker-a")
    assert store.attempts(item.work_item_id) == ()


def test_the_same_holder_asking_again_gets_the_same_attempt(store: JobStore) -> None:
    """丢响应之后重发不能变成「第二次尝试」—— 那会凭空多出一条 attempt，
    而它在报告里长得像一次重试。"""
    _, item = _dispatched(store)
    first = store.lease(item.work_item_id, holder="worker-a")
    again = store.lease(item.work_item_id, holder="worker-a")

    assert first == again
    assert len(store.attempts(item.work_item_id)) == 1


def test_another_holder_cannot_take_a_leased_work_item(store: JobStore) -> None:
    _, item = _dispatched(store)
    store.lease(item.work_item_id, holder="worker-a")

    with pytest.raises(LeaseError):
        store.lease(item.work_item_id, holder="worker-b")
    assert len(store.attempts(item.work_item_id)) == 1


def test_a_stopped_work_item_is_not_leased_again(store: JobStore) -> None:
    """停止理由是一次性写下的；重跑会覆盖它。"""
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=NOTHING)

    with pytest.raises(LeaseError):
        store.lease(item.work_item_id, holder="worker-b")


def test_a_released_attempt_frees_the_work_item_again(store: JobStore) -> None:
    """交还是**唯一**的收回路径（租约不按时钟自动失效），所以它必须真的让这个
    work item 能再被领走 —— 否则一个崩溃的 worker 会永久占住它。"""
    _, item = _dispatched(store)
    first = store.lease(item.work_item_id, holder="worker-a")
    store.release(first.attempt_id, reason="worker 崩溃，进程已不在了")

    second = store.lease(item.work_item_id, holder="worker-b")
    assert second.ordinal == 1
    assert [a.state for a in store.attempts(item.work_item_id)] == [
        AttemptState.RELEASED,
        AttemptState.LEASED,
    ]


def test_a_paused_job_leases_nothing(store: JobStore) -> None:
    job, item = _dispatched(store)
    store.set_state(job.job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="ops")

    with pytest.raises(LeaseError):
        store.lease(item.work_item_id, holder="worker-a")


# ----------------------------------------------------------------------
# 六、定局：重发幂等，改内容拒绝
# ----------------------------------------------------------------------
def test_settling_records_the_stop_reason_on_the_work_item(store: JobStore) -> None:
    """三件事在同一个写事务里，因为它们是同一件事 —— 一条结算了却没有停止理由的
    attempt 会让「这个 work item 停了吗」有两个互相矛盾的答案，而一条有停止理由
    却没有产出的 work item 会让「它产出了什么」有两个答案。"""
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    settled = store.settle(
        attempt.attempt_id, stop_reason=StopReason.BUDGET_EXHAUSTED, findings=FOUND
    )

    assert settled.attempt.state is AttemptState.SETTLED
    assert settled.attempt.settled_at is not None
    assert settled.findings == FOUND
    assert settled.replayed is False
    current = store.get_work_item(item.work_item_id)
    assert current is not None and current.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert current.is_stopped
    recorded = store.settlement(item.work_item_id)
    assert recorded is not None and recorded.findings == FOUND


def test_the_same_result_twice_is_idempotent(store: JobStore) -> None:
    """§6 逐字：Same request/result retries are idempotent。

    **幂等不等于不可区分。** 两次返回的产出、租约、落盘时刻都相同，只有
    ``replayed`` 不同 —— 它是「这次调用真的写了吗」的答案。把它抹掉（让两次
    返回完全相等的对象），「又写了一遍」与「没有写」在回执里就不可区分了，
    而调用方正是据此决定要不要再试一次。
    """
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    first = store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)
    again = store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)

    assert first.replayed is False
    assert again.replayed is True
    assert again.recorded_at == first.recorded_at
    assert again.findings == first.findings
    assert again.attempt == first.attempt
    assert again.content_sha256 == first.content_sha256


def test_the_same_stop_reason_with_different_findings_is_refused(store: JobStore) -> None:
    """**结局指纹的另一半。** 只比停止理由，一份换了产出的重发会被当成幂等接受 ——
    于是「同一个 attempt 结了两次，产出的东西不一样」这件事在存储层看不出来。

    它的失败形态是静默的：第二次提交返回 ``replayed: true``，调用方读到的是
    「我的结果已经在盘上了」，而盘上是另一份。
    """
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)

    other = Findings(candidates=(Finding(statement="另一批候选", evidence_refs=(EVIDENCE_A,)),))
    with pytest.raises(JobStateError):
        store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=other)

    recorded = store.settlement(item.work_item_id)
    assert recorded is not None and recorded.findings == FOUND


def test_the_same_findings_with_a_different_stop_reason_is_refused(store: JobStore) -> None:
    """结局指纹的第三半：只比产出，换一个停止理由的重发会被当成冲突 —— 而 §6
    要求它幂等。两半都必须进那个指纹（``ports/jobs.py::settlement_outcome``）。"""
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)

    with pytest.raises(JobStateError):
        store.settle(attempt.attempt_id, stop_reason=StopReason.BLOCKED, findings=FOUND)

    current = store.get_work_item(item.work_item_id)
    assert current is not None and current.stop_reason is StopReason.GOAL_MET


def test_an_empty_result_is_a_recorded_result(store: JobStore) -> None:
    """``Findings()`` 落得下盘，而且与「还没有结算」是两件事。

    这是 ``get_job`` 报告 ``stored: 0`` 唯一诚实的情形：那个 0 背后有一次真实的
    运行。少了这条判据，「跑过了、什么都没找到」与「还没跑」在存储层长得一样，
    于是读的那一侧只能靠猜。
    """
    _, item = _dispatched(store)
    assert store.settlement(item.work_item_id) is None

    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=NOTHING)

    recorded = store.settlement(item.work_item_id)
    assert recorded is not None
    assert recorded.findings.is_empty
    assert recorded.findings.claim_count == 0
    assert store.get_work_item(item.work_item_id).is_stopped  # type: ignore[union-attr]


def test_releasing_leaves_no_settlement(store: JobStore) -> None:
    """交还**不**产生产出，也不给 work item 写停止理由 —— 这正是「有没有结果」
    那条边界由两个动词分掉的证据。

    做成一个 ``findings=None`` 的缺省参数，「跑过了、什么都没找到」与「根本没报」
    会落成同一条记录。
    """
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.release(attempt.attempt_id, reason="worker 崩溃，进程已不在了")

    assert store.settlement(item.work_item_id) is None
    current = store.get_work_item(item.work_item_id)
    assert current is not None and current.stop_reason is None
    assert current.is_stopped is False

    # 而且交还之后还能再领一次 —— 一条产出的缺席不是这个 work item 的结局。
    second = store.lease(item.work_item_id, holder="worker-b")
    store.settle(second.attempt_id, stop_reason=StopReason.GOAL_MET, findings=NOTHING)
    assert store.settlement(item.work_item_id) is not None


def test_the_same_attempt_cannot_change_its_conclusion(store: JobStore) -> None:
    """允许改写结论，『重发』就成了一条改结论的旁路。"""
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)

    with pytest.raises(JobStateError):
        store.settle(attempt.attempt_id, stop_reason=StopReason.BLOCKED, findings=FOUND)
    current = store.get_work_item(item.work_item_id)
    assert current is not None and current.stop_reason is StopReason.GOAL_MET


def test_a_released_attempt_cannot_then_settle(store: JobStore) -> None:
    """一条已经交还的租约不是「再结一次」，而是另一个决定 —— 而它已经有结论了。"""
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.release(attempt.attempt_id, reason="超时，先交还")

    with pytest.raises(JobStateError):
        store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=NOTHING)
    current = store.get_work_item(item.work_item_id)
    assert current is not None and current.stop_reason is None


def test_release_requires_a_reason(store: JobStore) -> None:
    """让「我不知道为什么没结果，先把它放掉」在代码里写不出来。"""
    _, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")

    with pytest.raises(ValueError):
        store.release(attempt.attempt_id, reason="   ")
    assert store.attempts(item.work_item_id)[0].state is AttemptState.LEASED


def test_a_cancelled_job_takes_no_more_results(store: JobStore) -> None:
    """§6：取消报告 what actually stopped。受理一个取消之后才到的 findings，会让
    报告里多出一批没人授权的工作。"""
    job, item = _dispatched(store)
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.set_state(job.job_id, JobState.CANCELLED, expected=JobState.QUEUED, reason="ops")

    with pytest.raises(JobStateError):
        store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=NOTHING)
    current = store.get_work_item(item.work_item_id)
    assert current is not None and current.stop_reason is None


def test_settling_an_unknown_attempt_is_refused(store: JobStore) -> None:
    with pytest.raises(JobStateError):
        store.settle("0" * 32, stop_reason=StopReason.GOAL_MET, findings=NOTHING)


# ----------------------------------------------------------------------
# 七、id 空间不相交
# ----------------------------------------------------------------------
def test_the_three_id_spaces_are_disjoint() -> None:
    """三个 id 空间**必须**互不相交，而它们的分段数相同。

    ``work_item_id("j", 1)`` 与 ``attempt_id("j", 1)`` 若拼出同一个字符串，两个
    不同的东西就获得了同一个 id —— 而 id 决定的是「这一行说的是谁」。这不是假想
    的风险：``tests/test_budget_contract.py`` 里那条「``("a:b", "c")`` 与
    ``("a", "b:c")`` 不是同一条」守的是同一类失败，只是那一处是分隔符歧义。
    """
    ids = {job_id("x", "1"), work_item_id("x", 1), attempt_id("x", 1)}
    assert len(ids) == 3


def test_a_job_state_carries_no_done_member() -> None:
    """「这个作业做完了吗」由 work item 回答，不由作业状态回答。

    这条断言的是**没有**：加一个 ``DONE`` 成员会让一个多 work item 的作业在第一个
    work item 停止时就被读成「完成」，而另一个还在跑 —— 而那个错误在读代码时看不
    出来，只有在报告里少了一批候选。

    完成只在一个轴上有词，那个词是 ``StopReason.GOAL_MET``。
    """
    assert {s.value for s in JobState} == {"queued", "paused", "blocked", "cancelled"}


def test_the_request_fingerprint_separates_every_field_it_takes() -> None:
    """请求指纹必须对**每一个**输入字段敏感。

    它唯一的用途是判「同一个幂等键配的内容变了吗」。漏掉任何一个字段的后果
    都是同一个：该报冲突的受理被当成重发吞掉，调用方以为自己追加了工作，
    实际什么都没发生 —— 而报告上只显示研究只做了一遍。
    """
    base = request_fingerprint(
        scene_id="uk-x", input_kind=JobInputKind.DISCOVERY_SCOPE, input_ref="devon"
    )
    assert base == request_fingerprint(
        scene_id="uk-x", input_kind=JobInputKind.DISCOVERY_SCOPE, input_ref="devon"
    )
    assert base != request_fingerprint(
        scene_id="uk-y", input_kind=JobInputKind.DISCOVERY_SCOPE, input_ref="devon"
    )
    assert base != request_fingerprint(
        scene_id="uk-x", input_kind=JobInputKind.KNOWN_ACCOUNTS, input_ref="devon"
    )
    assert base != request_fingerprint(
        scene_id="uk-x", input_kind=JobInputKind.DISCOVERY_SCOPE, input_ref="cornwall"
    )
    assert len(base) == 64


def test_the_fingerprint_criterion_actually_discriminates() -> None:
    """正对照：一个用 ``:`` 当分隔符的实现会撞车，上面那条判据必须抓到它。

    ``("a", "b:c")`` 与 ``("a:b", "c")`` —— 两段拼成 ``a:b:c``，三段也拼成
    ``a:b:c``。这不是假想的风险：``:`` 正是最顺手会写下去的那个分隔符
    （``domain/util.py`` 的 ``SEP`` 就是为它存在的），而没有这条正对照，
    值分隔符被换成 ``:`` 或 ``/`` 时上面那条判据依然可能全绿 —— 只要没有哪条
    测试恰好用到会撞的两个值。
    """

    def naive(*parts: str) -> str:
        return sha256_text(":".join(parts))

    assert naive("a", "b:c") == naive("a:b", "c")
    assert request_fingerprint(
        scene_id="a", input_kind=JobInputKind.DISCOVERY_SCOPE, input_ref="b:c"
    ) != request_fingerprint(
        scene_id="a:b", input_kind=JobInputKind.DISCOVERY_SCOPE, input_ref="c"
    )


def test_blocked_is_the_one_word_shared_by_both_axes() -> None:
    """``blocked`` 在两个闭集里都有，而且**不是**重复：它们回答两个不同的问题 ——
    「这个 work item 为什么停了」与「新工作还派不派」。合成一个字段会让「操作者
    暂停」与「客观阻塞」不可区分，而这两者**谁能解除**不同。

    写下来是因为这是唯一一处重叠，下一个读者会怀疑它是笔误，然后「顺手统一」成
    一个枚举 —— 那正是上面那条要守的失败。这条同时是它的执行器：改名或合并哪个
    成员，这里会红。
    """
    overlap = {s.value for s in JobState} & {s.value for s in StopReason}
    assert overlap == {"blocked"}


# ----------------------------------------------------------------------
# 正对照
# ----------------------------------------------------------------------
def _two_workers_can_take_one_work_item(store: JobStore) -> bool:
    """「排他租约」这条判据的本体。

    抽成函数是为了让正对照能证明它**真的**会红 —— 见下。
    返回 ``True`` 表示第二个 worker 也拿到了同一个 work item。
    """
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.DISCOVERY_SCOPE,
        input_ref="devon-2026-09",
        idempotency_key="probe",
        request_sha256=_request("probe"),
    )
    (item,) = store.compile_work_items(job.job_id, DOSSIERS[:1], scene=SCENE_FILES)
    store.map_execution(item.work_item_id, quay_task_id="TASK-999")
    store.lease(item.work_item_id, holder="worker-a")
    try:
        store.lease(item.work_item_id, holder="worker-b")
    except LeaseError:
        return False
    return True


class _LeasesAreAdvisory(InMemoryJobStore):
    """一个**错**的实现：不看有没有活租约就直接发。

    它的行为是「排他性只是建议」，而它看起来完全正常 —— 串行跑、没有两个 worker
    的时候，它的记录和正确实现一模一样。正对照用它证明上面那条判据不是恒真。
    """

    def _live_attempt(self, work_item_id: str):  # type: ignore[override]
        return None


def test_the_exclusivity_criterion_actually_discriminates() -> None:
    """正对照：这条判据在一个「排他性只是建议」的实现上必须变红。

    没有这一条，上面那些租约判据在「它们其实什么都没守」的情况下依然全绿 ——
    而 §8B 逐字要求的正是「no duplicate expensive work」。
    """
    assert _two_workers_can_take_one_work_item(InMemoryJobStore()) is False
    assert _two_workers_can_take_one_work_item(_LeasesAreAdvisory()) is True


# ----------------------------------------------------------------------
# 八、产出（§3 submit_findings）：类型层能挡的与挡不住的
# ----------------------------------------------------------------------
def test_a_conclusion_without_evidence_cannot_be_built() -> None:
    """一条追不到依据的结论在类型上就写不出来。

    与 ``ContactRoute._evidence_required_for_non_x`` 同一条判据（那里是「非 X 的
    判定必须挂 evidence_ids」）。这里是它的发现期版本 —— §4 逐字：「an extracted
    summary does not replace the evidence used for literal occurrence and
    person/role/Account binding」。
    """
    with pytest.raises(ValueError):
        Finding(statement="这个场地用的是天然草", evidence_refs=())


class _FindingWithoutTheEvidenceRule(Finding):
    """正对照：**去掉那条判据**的 ``Finding``。

    它看起来和真的那个一模一样 —— 同样的字段、同样的 ``as_dict()``。区别只有在
    「不挂证据」时才显形，而那正是这条判据唯一要挡的形状。
    """

    def __post_init__(self) -> None:
        object.__setattr__(self, "statement", (self.statement or "").strip())
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))


def test_the_evidence_criterion_actually_discriminates() -> None:
    """正对照：上面那条判据在一个不检查证据的 ``Finding`` 上必须失效。

    没有它，「结论必须挂证据」在判据恒假时依然全绿 —— 而那意味着一条凭空写下的
    结论与一条有依据的结论在存储里长得完全一样。
    """
    bare = _FindingWithoutTheEvidenceRule(statement="凭空写的", evidence_refs=())
    assert bare.as_dict() == {"statement": "凭空写的", "evidence_refs": []}
    assert Findings(observations=(bare,)).claim_count == 1

    with pytest.raises(ValueError):
        Finding(statement="凭空写的", evidence_refs=())


def test_the_same_statement_twice_is_refused() -> None:
    """同一个 statement 写两遍会直接变成一个虚高的条数 —— 而报告上的「候选 N 个」
    正是从这些元组的长度算出来的。"""
    same = Finding(statement="候选甲", evidence_refs=(EVIDENCE_A,))

    with pytest.raises(ValueError):
        Findings(candidates=(same, Finding(statement="候选甲", evidence_refs=(EVIDENCE_B,))))


class _FindingsWithoutTheCountCheck(Findings):
    """正对照：**去掉去重检查**的 ``Findings``（同一条结论写两遍）。"""

    def __post_init__(self) -> None:
        object.__setattr__(self, "gaps", tuple(self.gaps))


def test_the_count_criterion_actually_discriminates() -> None:
    """正对照：重复的结论在一个不检查的实现里会被数成两条。

    这就是那个谎言的完整形状 —— 一次价值为零的重复，变成报告上的一个业务数字。
    """
    same = Finding(statement="候选甲", evidence_refs=(EVIDENCE_A,))
    lie = _FindingsWithoutTheCountCheck(candidates=(same, same))
    assert lie.claim_count == 2
    assert len({finding.statement for finding in lie.candidates}) == 1


def test_gaps_are_not_claims() -> None:
    """缺口是**结论的缺席**，所以不计入条数、也不需要证据。

    把它做成 ``Finding`` 会逼每一个缺口伪造一条依据 —— 于是「有依据的结论」与
    「没有结论」在数据里长得一样。
    """
    findings = Findings(gaps=("没能确认它的主场容量", "网页上没有场地照片"))

    assert findings.claim_count == 0
    assert findings.is_empty is False
    assert findings.as_dict()["gaps"] == ["没能确认它的主场容量", "网页上没有场地照片"]


def test_the_payload_round_trips() -> None:
    """规范形状与它的逆必须成对 —— 两个适配器共用同一份（``findings_payload`` /
    ``findings_from_payload``），漂移的那天同一份产出会有两个内容散列，于是
    「同一次重发」被读成「一次新的产出」。"""
    from marketops.domain.findings import findings_from_payload, findings_sha256

    assert findings_from_payload(FOUND.as_dict()) == FOUND
    assert findings_from_payload(NOTHING.as_dict()) == NOTHING
    assert findings_sha256(FOUND) == findings_sha256(findings_from_payload(FOUND.as_dict()))
    assert len(findings_sha256(FOUND)) == 64
    assert findings_sha256(FOUND) != findings_sha256(NOTHING)

"""§B21 的第三个落点：**受管唤醒入口** —— 谁去争归属、争不到怎么办、什么时候放手，
以及客户端不在了之后下一批是怎么被推起来的。

## 为什么这一份与 `test_continuation_controller.py` 分开

那一份测的是**一轮 tick 做了什么**（应用层：七段、六个数、闸门、上限）。这一份测的是
**谁来跑那些轮**，而那是三件应用层结构上问不了的事：

1. **顺序**：``acquire`` → 驱动 → ``release``。一个「先跑一轮看看、跑完再争」的实现
   在应用层看不出任何毛病 —— 它的每一轮都对，只是可能与本工作区里另一个控制器同时
   在跑（§10 逐字禁止的并行双触发器）。所以这里断言的是**调用顺序**，不是返回值。
2. **拿不到归属时的行为**：不驱动，且**等**（常驻）或**退 3**（``--once``）。前一格
   的错误形态是「空转地推着一批不属于自己的资源」；后一格的错误形态是「回 0，于是
   脚本里『我跑了一轮』与『我没跑』长得一样」。
3. **心跳回 ``False`` 之后停手**：那一格不是日志，是回滚信号 —— 停手之前驱动过的
   每一轮都是并行双触发器。

这三格都要求一个**记账的**归属替身与一个记账的执行器，而它们把事件写进**同一条**
顺序账（``events``）—— 「先争后驱动」这件事只有一条共用的账答得出来。

## 这一份里另有两组判据

* **执行器那三处 ``BLOCKED``**：暂停 / 没装执行器 / 这一条意图说不出主体。它们与
  ``FAILED`` 的分别不是措辞：``BLOCKED`` **不吃** ``attempts``（一个暂停了一周的
  场景不该因为每 30 秒 tick 一次而被判成重试了一万次），``FAILED`` 吃，而那条次数
  界限正是「不能用持续运行绕无限预算」的执行器。所以暂停那一条同时判三格：状态、
  ``attempts`` 还是 0、以及恢复之后同一行真的被推起来。
* **纵向那一条**：「受理之后客户端不在了 → 控制器把活交出去 → 结算 → 下一次唤醒
  把复核跑掉」。它用**真的** SQLite 存储、真的场景闸门、真的 ``run_once``，只把
  外部执行器换成记账替身 —— 外面那一步本来就是别人的进程，而这一条要证的是
  「触发者是盘上的那一行，不是某个人」。

## 时刻与轮次

``run_once_cli`` / ``run_resident`` 内部一律取 ``utc_now()``（它们是进程入口，本来就
有自己的时钟）。所以这里不做「把 now 钉死」那一套，判据落在**相对**关系上：
``due_at == updated_at + BLOCKED_BACKOFF``、代际号、顺序账。
"""

from __future__ import annotations

import io
import json

import pytest

from marketops.adapters.page_mirror import page_path
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.scenes import (
    SqliteMaintenanceStore,
    SqliteSceneStore,
)
from marketops.adapters.sqlite.wakeup import SqliteWakeupRegistry
from marketops.application.continuation import (
    ContinuationExecutor,
    ContinuationOutcome,
)
from marketops.application.scenes import activate, create_draft, pause
from marketops.domain.continuation import (
    BLOCKED_BACKOFF,
    BlockedOn,
    ContinuationKind,
    ContinuationState,
    WakeupOwner,
    continuation_id,
)
from marketops.domain.enums import JobInputKind, JobState, StopReason
from marketops.domain.findings import Finding, Findings
from marketops.domain.scene import PauseReason
from marketops.domain.util import sha256_text, utc_now
from marketops.interfaces import controller
from marketops.ports.continuation import WakeupRegistry
from tests.fakes import FakeDispatcher, synthetic_dossiers, synthetic_scene_files
from tests.test_continuation_controller import _bundle as synthetic_bundle
from tests.test_scene_store_contract import make_version

#: 场景与主体。主体用一个 ``acct-`` 形状的规范 id：复核的盘点**按 id 落文件名**，而
#: 需要被 ``slugify`` 改写的 id 会被拒（``check_canonical_id``）。
SCENE = "scene-resident"
SUBJECTS = ("acct-0001", "acct-0002")
DOSSIERS = synthetic_dossiers(*SUBJECTS)
SCENE_FILES = synthetic_scene_files()
KEY = "discovery-v1-arm-b"
HOLDER = "marketops-controller"
OTHER = "another-controller"
#: 接线那一条判据要真的落一份字节，所以需要一对 URL 与一份素材。它们与
#: ``tests/test_page_mirror.py`` 里那一对同形 —— 这一条不用真站名。
MIRROR_URL = "https://example.co.uk/contact"
MIRROR_BODY = b"<html><body>grounds@example.co.uk</body></html>"

#: 一次有产出的结算。与 ``test_sqlite_continuation.py`` 里那条同形 —— 判据判的是
#: 「结算落账了、findings 回读得出来」，不是这一句话的内容。
FOUND = Findings(
    observations=(
        Finding(statement="这一轮确认了天然草", evidence_refs=(sha256_text("p")[:32],)),
    ),
)


# ----------------------------------------------------------------------
# 夹具与替身
# ----------------------------------------------------------------------
@pytest.fixture
def events() -> list[str]:
    """**一条**顺序账，归属与执行器都往里写。

    分成两条账（``registry.calls`` 与 ``executor.calls``）也能各判各的，但「先争后驱动」
    是一个**跨两者**的事实 —— 两条账合起来看只能得到「都发生过」，而顺序正是这一格
    的全部内容。
    """
    return []


@pytest.fixture
def fake() -> FakeDispatcher:
    """外部执行器。它是这一份里唯一被换掉的真件（外面那一步本来就是别人的进程）。"""
    return FakeDispatcher()


def _scene(settings) -> None:
    """在本部署的工作区（``settings.profile``）下立一个**放行派发**的场景。

    工作区必须是 ``settings.profile``：``controller._build`` 就是用那个值构造执行器的
    工作区，而 ``_gate`` 只看得见本工作区的场景 —— 一个建在别的工作区下的场景会让每一
    轮都「没有到期的工作」，而那看起来与「没有工作」逐字相同。
    """
    store = SqliteSceneStore(settings.database_path)
    create_draft(
        store=store,
        version=make_version(scene_id=SCENE, workspace=settings.profile),
        bundle=synthetic_bundle(),
    )
    activate(store=store, scene_id=SCENE, version=1, at=utc_now(), by="operator-1")


def _admitted(settings, *, tag: str = "r1") -> str:
    """受理一个作业并编译出工作单元，返回 ``job_id``。

    这两步就是 ``submit_research`` 在派发**之前**做的那两步。本文件把它们单独拿出来，
    因为「客户端断开」的准确含义是：**此后没有任何第三方再去调那个工具**，而受理与它
    欠下的那一行已经落了账。
    """
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.DISCOVERY_SCOPE,
        input_ref="devon-2026-09",
        idempotency_key=f"{KEY}-{tag}",
        request_sha256=sha256_text(tag),
    )
    store.compile_work_items(job.job_id, DOSSIERS, scene=SCENE_FILES)
    return job.job_id


def _jobs(settings) -> SqliteJobStore:
    return SqliteJobStore(settings.database_path)


def _dispatch_id(job_id: str) -> str:
    return continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, job_id)


def _row(settings, cid: str):
    row = _jobs(settings).continuation(cid)
    assert row is not None, f"{cid} 应当还在盘上"
    return row


def _capture() -> io.StringIO:
    return io.StringIO()


def _payloads(text: str) -> list[dict]:
    """把 ``_emit`` 写进同一个流里的若干 JSON 对象逐个解出来。

    ``raw_decode`` 而不是 ``split``：``indent=2`` 的输出里带换行，按行切会把一个对象
    切成好几段 —— 而那段切错的输出在断言里看起来像「没有这一段」。
    """
    decoder = json.JSONDecoder()
    out: list[dict] = []
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            return out
        obj, index = decoder.raw_decode(text, index)
        out.append(obj)


def _executor(settings, fake) -> controller.LiveContinuationExecutor:
    """一个接上真存储的执行器 —— 「直接调动词」的那几条判据用它。"""
    return controller.LiveContinuationExecutor(
        settings=settings,
        workspace=settings.profile,
        jobs=_jobs(settings),
        scenes=SqliteSceneStore(settings.database_path),
        dispatcher=fake,
    )


class RecordingExecutor(ContinuationExecutor):
    """记账的执行器：每一次调用进 ``events``，结果由队列给出（空则报 ``done``）。"""

    def __init__(self, events: list[str], results=()) -> None:
        self.calls: list[dict] = []
        self._events = events
        self._results = list(results)

    def dispatch_batch(self, *, scene_id, job_id, checkpoint, now):
        return self._record("dispatch_batch", scene_id=scene_id, job_id=job_id)

    def maintenance_tick(self, *, scene_id, subject_kind, subject_ref, checkpoint, now):
        return self._record(
            "maintenance_tick",
            scene_id=scene_id,
            subject_kind=subject_kind,
            subject_ref=subject_ref,
        )

    def _record(self, verb: str, **fields) -> ContinuationOutcome:
        self.calls.append({"verb": verb, **fields})
        self._events.append("drive")
        if self._results:
            return self._results.pop(0)
        return ContinuationOutcome.done()

    @property
    def verbs(self) -> list[str]:
        return [call["verb"] for call in self.calls]


class FakeRegistry(WakeupRegistry):
    """记账的归属：``acquire`` 与 ``heartbeat`` 的结果各由一条队列给出。

    ``heartbeat`` 返回 ``False`` 这一格是本类存在的理由：真的注册表要先制造一次换代
    （另一条连接顶掉本代）才答得出 ``False``，而那会把「常驻循环收到 ``False`` 之后
    停不停手」这条判据与存储层的 CAS 缠在一起。这里直接编排那个返回值，于是判据只问
    循环自己做了什么。队列用尽之后 ``acquire`` 给 ``None``、``heartbeat`` 给 ``False``
    —— 两个都用**保守**的那一格：一个默默把「没有下一个结果」读成「我拿到了」的替身，
    会让判据在它自己的编排错误上变绿。
    """

    def __init__(self, events: list[str], *, owners=(), beats=(), current=None) -> None:
        self._events = events
        self._owners = list(owners)
        self._beats = list(beats)
        self._current = current
        self.acquires: list[str] = []
        self.heartbeats: list[tuple[str, int]] = []
        self.releases: list[tuple[str, int]] = []

    def acquire(self, workspace, *, holder, now):
        self._events.append("acquire")
        self.acquires.append(holder)
        return self._owners.pop(0) if self._owners else None

    def heartbeat(self, workspace, *, holder, epoch, now):
        self._events.append("heartbeat")
        self.heartbeats.append((holder, epoch))
        return bool(self._beats.pop(0)) if self._beats else False

    def release(self, workspace, *, holder, epoch):
        self._events.append("release")
        self.releases.append((holder, epoch))
        return True

    def current(self, workspace):
        return self._current


class FakeStopper:
    """一个可调用的停手信号 —— ``run_resident`` 要把它交给 ``signal.signal``。

    ``asked`` 由 ``slept`` 置位：本文件里循环的退出点统一是「睡过一次之后」，于是
    「跑了几轮」这件事由 ``sleep`` 的次数决定，而不是由信号到得多及时决定。
    """

    def __init__(self, *, after: int) -> None:
        self.asked = False
        self.sleeps = 0
        self._after = after

    def __call__(self, _signum, _frame) -> None:  # pragma: no cover —— 本文件不发信号
        self.asked = True

    def slept(self) -> None:
        self.sleeps += 1
        if self.sleeps >= self._after:
            self.asked = True


def _owner(workspace: str, *, holder: str = HOLDER, epoch: int = 1) -> WakeupOwner:
    """一条归属。``workspace`` 必填 —— ``WakeupOwner`` 自己不接受空串（空工作区的
    归属没有任何一条判据能核对），所以这里也不给缺省值。"""
    at = utc_now()
    return WakeupOwner(
        workspace=workspace, holder=holder, epoch=epoch, acquired_at=at, heartbeat_at=at
    )


def _wire(monkeypatch, *, settings, registry, executor) -> SqliteJobStore:
    """把 ``_build`` 换成「用这几个件」。**只换这一处** —— 入口那一层原样跑。

    四件里三件（作业存储、场景存储、执行器）是真的；被换掉的只有归属注册表（编排心跳
    与换代）与执行器（在常驻循环那几条判据里只关心它有没有被调用）。
    """
    jobs = _jobs(settings)
    scenes = SqliteSceneStore(settings.database_path)
    monkeypatch.setattr(
        controller, "_build", lambda _settings: (jobs, registry, scenes, executor)
    )
    return jobs


class SimpleTime:
    """``controller`` 只用 ``time`` 里的 ``sleep``。一个只有它的壳就够了。"""

    def __init__(self, sleep) -> None:
        self.sleep = sleep


def _stopper_after(monkeypatch, *, sleeps: int = 1) -> FakeStopper:
    """跑完 ``sleeps`` 次 ``sleep`` 就让循环退出，并且**不真的睡**。

    换的是 ``controller`` 名字空间里的 ``time``（一个只有 ``sleep`` 的壳），不是
    ``time.sleep`` 本身：后者是全局对象，改它会影响这个进程里所有别的调用方。
    """
    stopper = FakeStopper(after=sleeps)

    def sleep(_seconds):
        stopper.slept()

    monkeypatch.setattr(controller, "time", SimpleTime(sleep))
    return stopper


# ----------------------------------------------------------------------
# 一、执行器：三处「说得出理由的等待」，以及一处要人对账的失败
# ----------------------------------------------------------------------
def test_a_missing_dispatcher_is_blocked_and_the_reason_says_the_work_is_still_there(
    monkeypatch, settings
):
    """没装外部执行器 → ``BLOCKED(CAPACITY)``，**不报 DONE**。

    报 DONE 的那一版在报告上是「做完了」，而那一行会从盘上消失 —— 于是执行器到位
    之后**没有任何东西记得还欠着一批活**。所以这里同时判四格：状态、``blocked_on``、
    那句话里有没有「没有被交出去」，以及 ``attempts`` 还是 0。

    ``CAPACITY`` 而不是 ``HUMAN``：它等的是**运维补一样东西**（把执行器接上），不是
    等一个人做决定 —— 两者的处置不同，而合成一个会让「去接线」看起来像「去审批」。
    """
    _scene(settings)
    job_id = _admitted(settings)
    monkeypatch.setattr(controller, "default_dispatcher", lambda _s: None)
    out = _capture()

    assert controller.run_once_cli(
        settings=settings, holder=HOLDER, limit=None, out=out
    ) == 0

    payload = _payloads(out.getvalue())[0]
    assert payload["done"] == []
    assert [row["kind"] for row in payload["blocked"]] == [
        ContinuationKind.DISPATCH_BATCH.value
    ]
    assert payload["blocked"][0]["on"] == BlockedOn.CAPACITY.value
    assert "没有装外部执行器" in payload["blocked"][0]["reason"]
    assert "没有被交出去" in payload["blocked"][0]["reason"]

    row = _row(settings, _dispatch_id(job_id))
    assert row.state is ContinuationState.BLOCKED
    assert row.blocked_on is BlockedOn.CAPACITY
    # **不吃 attempts 的那一半**：它没试过 —— 只是门没开。一个每一轮都记一次尝试的
    # 实现会把一次缺接线在 3 轮之后变成一条终态失败，而那时它要人看的东西从「去接
    # 执行器」变成了「这件挂了」。
    assert row.attempts == 0
    assert row.due_at == row.updated_at + BLOCKED_BACKOFF


def test_a_paused_job_is_blocked_on_pause_and_resuming_puts_the_same_row_back_to_work(
    monkeypatch, settings, fake
):
    """暂停 → 那一行 ``BLOCKED(PAUSE)`` 且**一件都不碰外部**；恢复 → 同一行被推起来。

    暂停那一半走的是**真的**路：``control_job(pause)`` 的落点把这条未决意图记成
    ``BLOCKED``，控制器因此领得到它（``_CLAIMABLE`` 含 ``BLOCKED``），而执行器读一次
    作业状态、报 ``BLOCKED(PAUSE)`` 并把理由写清「恢复它是 control_job(action=resume)」。
    外面那本账必须**一条记录都没有** —— 不是「碰了但是幂等」：§6 逐字要求 pause 挡住
    **新**派发，而一次读了外部再决定不建任务，每一次 tick 都在外面留下一道痕迹。

    那一句理由不是执行器写的：``WorkDispatch.dispatch_job`` 在碰外部**之前**按
    ``admits_dispatch`` 就拒了，并把「为什么拒」写在 ``_dispatch_refused`` 一处；
    执行器只把那个 ``blocker`` **分类**成 ``BLOCKED(PAUSE)`` 而不是 ``FAILED``。
    这条分工是变异测试发现的：执行器里原本另有一处 ``if job.state is PAUSED``，
    把那一处改成 ``if False:`` 之后这一条判据**照样绿** —— 它是一句从不生效的第二个
    产地，已删。

    恢复那一半是同一行：``resume`` 把它重排回 ``PENDING``，下一轮就真的交出去了。
    两半合起来才是 §B21 的「暂停与恢复正确」—— 只判前一半的实现，可以让恢复永远
    不发生而每一格都绿。
    """
    _scene(settings)
    job_id = _admitted(settings)
    cid = _dispatch_id(job_id)
    monkeypatch.setattr(controller, "default_dispatcher", lambda _s: fake)
    store = _jobs(settings)
    store.set_state(job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="人工暂停")

    out = _capture()
    assert controller.run_once_cli(
        settings=settings, holder=HOLDER, limit=None, out=out
    ) == 0

    payload = _payloads(out.getvalue())[0]
    assert payload["blocked"][0]["on"] == BlockedOn.PAUSE.value
    assert "control_job(action=resume)" in payload["blocked"][0]["reason"]
    assert fake.calls == []
    paused = _row(settings, cid)
    assert paused.state is ContinuationState.BLOCKED
    assert paused.blocked_on is BlockedOn.PAUSE
    assert paused.attempts == 0

    store.set_state(job_id, JobState.QUEUED, expected=JobState.PAUSED)

    out = _capture()
    assert controller.run_once_cli(
        settings=settings, holder=HOLDER, limit=None, out=out
    ) == 0

    resumed = _payloads(out.getvalue())[0]
    assert [row["kind"] for row in resumed["done"]] == [
        ContinuationKind.DISPATCH_BATCH.value
    ]
    assert len(fake.created_units) == len(DOSSIERS)
    assert _row(settings, cid).state is ContinuationState.DONE


def test_an_intent_pointing_at_a_job_that_is_not_on_disk_is_a_failure_to_reconcile(
    settings, fake
):
    """指向不存在作业的意图 → ``FAILED``（**不是**三种 ``BLOCKED`` 之一）。

    ``BLOCKED`` 的三个理由说的都是「等一等就好」，而这一格等不来任何东西：受理与它欠下
    的那一行在同一次写事务里落下（``tests/test_sqlite_continuation.py`` 有一条故障注入
    判据钉着它），所以盘上出现这种行说明有人动过盘 —— 那要人看一眼。
    """
    outcome = _executor(settings, fake).dispatch_batch(
        scene_id=SCENE, job_id="job-does-not-exist", checkpoint="", now=utc_now()
    )

    assert outcome.state is ContinuationState.FAILED
    assert outcome.blocked_on is None
    assert "对账" in outcome.reason
    assert fake.calls == []


def test_dispatching_the_same_batch_twice_hands_out_nothing_the_second_time(
    settings, fake
):
    """同一个动词跑两遍：第二遍**一件都不交出去**，外面一个任务都不多。

    这是 §B21「控制器崩溃后恢复不重复昂贵工作」在派发路径上的全部内容，而它的执行者
    不是这个类：``WorkDispatch.dispatch`` 的第 2 步按**本地映射**判「派发过没有」。所以
    这一条判据判的是那个开关真的接上了 —— 换成一个「每次按 label 重查外部」的实现，
    第一遍之后本地映射也是对的，而第二遍会**静默地再建一批任务**，账上只显示
    「工作多了一倍」。
    """
    _scene(settings)
    job_id = _admitted(settings)
    executor = _executor(settings, fake)
    now = utc_now()

    first = executor.dispatch_batch(
        scene_id=SCENE, job_id=job_id, checkpoint="", now=now
    )
    assert first.state is ContinuationState.DONE
    assert first.checkpoint == f"handed_out={len(DOSSIERS)};pushed=0"
    created = fake.calls.count("create")
    assert created == len(DOSSIERS)

    second = executor.dispatch_batch(
        scene_id=SCENE, job_id=job_id, checkpoint=first.checkpoint, now=now
    )

    assert second.state is ContinuationState.DONE
    assert second.checkpoint == "handed_out=0;pushed=0"
    assert fake.calls.count("create") == created
    assert len(fake.tasks) == len(DOSSIERS)


def test_a_maintenance_intent_without_a_subject_is_blocked_and_no_empty_set_is_ticked(
    monkeypatch, settings, fake
):
    """两格都空 → ``BLOCKED(CAPACITY)``，且**没有**去 tick 一个空主体集。

    「tick 一个空集」看起来是这条路径上最省事的处置，而它的后果是静默的：``run_once``
    会照常跑完并报「0 格到期」，那句话与「这一格核过了、没有到期的」逐字相同 —— 一件
    **没做的**事在账上变成了一件做完的事。所以这条判据一多半在钉那句
    「``run_once`` 一次都没被调用」。

    两格都空是**存得下、今天写不出**的状态：身份快照机制之前编译的 work item 在盘上
    没有 ``subject``（``WorkItemSnapshot.subject`` 逐字如此），而今天没有任何写入口
    构造得出那种 work item。所以这里直接调那个动词 —— 这不是绕开生产路径，而是如实
    说明这一格今天只有一种到达方式。
    """
    calls: list[dict] = []

    def run_once(**kwargs):  # pragma: no cover —— 它被调用就是这条判据红了
        calls.append(kwargs)
        raise AssertionError("两格都空时不该 tick 任何主体集")

    monkeypatch.setattr(controller, "run_once", run_once)

    outcome = _executor(settings, fake).maintenance_tick(
        scene_id=SCENE,
        subject_kind="",
        subject_ref="",
        checkpoint="from=earlier",
        now=utc_now(),
    )

    assert calls == []
    assert outcome.state is ContinuationState.BLOCKED
    assert outcome.blocked_on is BlockedOn.CAPACITY
    assert "没有主体" in outcome.reason
    assert "身份快照" in outcome.reason
    # checkpoint 原样带回去：一条被挡住的意图没有推进，而「推进到哪儿了」这一格只能
    # 还是原来那个值 —— 换成一个新的会让恢复时的起点悄悄往下挪。
    assert outcome.checkpoint == "from=earlier"


def test_a_maintenance_tick_with_a_subject_records_an_observation(settings, fake):
    """点得出主体的那一格：真的跑完 ``run_once``，真的落一条观测。

    ``SOURCE_COVERAGE`` 那一面盘点读的是**本地**证据行，所以它不联网、不花钱，而它
    落的观测是「这一格被核过了」的凭据 —— 下一轮 ``what_is_due`` 于是不再排它。判据
    同时读那个观测（而不是只读回执里那句话）：回执说「记下 1 条」而库里没有，正是这
    一格最该被抓住的样子。

    而这一趟**整体报 FAILED**，而这不是这一格的缺陷：§B22 之后它认领三个面，另外
    两个（角色 / 邮箱证据）要看**已知的页面**，而这个主体名下一条证据都没有 —— 空
    页面集是不可达，不写观测、下一轮再领。一件三面里只做成一面的事在账上不许写成
    「做完了」（``_resolve_subjects`` 的 docstring 逐字如此：tick 一个空集让它报
    「0 格到期」，读起来与「核过了、没有到期的」逐字相同）。
    """
    _scene(settings)

    outcome = _executor(settings, fake).maintenance_tick(
        scene_id=SCENE,
        subject_kind="account",
        subject_ref=SUBJECTS[0],
        checkpoint="",
        now=utc_now(),
    )

    assert outcome.state is ContinuationState.FAILED
    assert "复核失败（共 2 格）" in outcome.reason, outcome.reason
    # 做成的那一面**照旧**落下了观测：整趟失败与「这一面没做成」是两件事，
    # 而把前者折进后者会让一次真的复核被回退掉。
    observations = SqliteMaintenanceStore(settings.database_path).observations(
        workspace=settings.profile
    )
    assert [row.subject_id for row in observations] == [SUBJECTS[0]]
    assert [row.aspect.value for row in observations] == ["source_coverage"]


def test_a_maintenance_tick_on_a_paused_scene_is_blocked_not_failed(settings, fake):
    """场景被暂停 → ``BLOCKED(PAUSE)``，**不是** ``FAILED``。

    ``run_once`` 对暂停的场景是**跳过并说出来**（``not_ticking``），不是抛异常 ——
    所以这一格在报告里本来就有准确的措辞，而执行器的责任是把它**原样抬上来**并且
    报 ``BLOCKED``：一个把它折进 ``FAILED`` 的实现会让暂停三分钟变成一条终态失败
    （``attempts`` 三次用完），而恢复之后那条复核再也不跑。
    """
    _scene(settings)
    pause(
        store=SqliteSceneStore(settings.database_path),
        scene_id=SCENE,
        at=utc_now(),
        reason=PauseReason.OPERATOR,
    )

    outcome = _executor(settings, fake).maintenance_tick(
        scene_id=SCENE,
        subject_kind="account",
        subject_ref=SUBJECTS[0],
        checkpoint="",
        now=utc_now(),
    )

    assert outcome.state is ContinuationState.BLOCKED
    assert outcome.blocked_on is BlockedOn.PAUSE
    assert outcome.reason, "跳过必须带一句人读得懂的理由"
    assert SqliteMaintenanceStore(settings.database_path).observations(
        workspace=settings.profile
    ) == ()


def test_a_maintenance_tick_whose_refresh_raised_is_a_failure_not_a_block(
    monkeypatch, settings, fake
):
    """复核回调抛了 → ``FAILED``，那条意图带着**次数**走重试。

    与三处 ``BLOCKED`` 的分别在这里最清楚：这一趟**真的试过了**（``run_once`` 认领了
    那一格、回调抛了、格子被结算成 ``FAILED``），所以它该吃 ``attempts``。报 ``BLOCKED``
    的那一版会让一格永远修不好的复核每一轮重来一次 —— 而「每一轮重来」正是 §B21
    禁止持续运行绕预算时最该防的形态。
    """

    def boom(_due):  # pragma: no cover —— 它被调用就是这一条判据要测的那一格
        raise RuntimeError("盘点读证据时炸了")

    monkeypatch.setattr(controller, "build_refresher", lambda **_kw: boom)
    _scene(settings)

    outcome = _executor(settings, fake).maintenance_tick(
        scene_id=SCENE,
        subject_kind="account",
        subject_ref=SUBJECTS[0],
        checkpoint="",
        now=utc_now(),
    )

    assert outcome.state is ContinuationState.FAILED
    assert outcome.blocked_on is None
    # §B22 之后这一趟认领的是**三个面**（来源覆盖 + 角色 + 邮箱证据），而这个替身
    # 对每一格都抛 —— 所以是三格，不是一格。
    assert "复核失败" in outcome.reason and "3 格" in outcome.reason
    # 那三格在维护账上留了痕：认领过、并且以 FAILED 收尾（不是留在 CLAIMED 上假装
    # 还有人跑）。留痕是这三格与「整轮直接抛出去」的分别。
    claims = SqliteMaintenanceStore(settings.database_path).claims(SCENE)
    assert [row.state.value for row in claims] == ["failed"] * 3


def test_the_controller_hands_the_refresher_a_mirror_writer(
    monkeypatch, settings, fake
) -> None:
    """控制器这一侧真的把「落第二份字节」的能力接进了复核回调。

    这一条判的是**接线**，不是机制：机制那一半在 ``tests/test_page_reuse.py``（台账判
    得对、字节落进接收方目录）与 ``tests/test_page_mirror.py``（复制的两道守卫）里用
    替身判过。而组合根漏接一样东西的形态是**静默**的 —— ``build_refresher`` 的缺省
    ``copy_page=None`` 含义是「这个部署落不下第二份」，于是每一轮照常跑完、报告照常
    好看，只是同一个工作区里两个主体抓到同一个 URL 时各付一次取回的费用，而这一点在
    报告上长得像「覆盖面更全」。

    所以断言分两步：建回调时**收到了**那个能力，以及拿它落一份字节的落点在本部署的
    镜像根下 —— 一个绑到了别处 ``Settings`` 的复制方会在第二条上露出来，而「传了个
    什么东西进去」这种形状断言对它是放行的。
    """
    seen: dict = {}

    def recorder(**kwargs):
        seen.update(kwargs)

        def boom(_due):  # pragma: no cover —— 它被调用就是这一条判据要测的那一格
            raise RuntimeError("接线已经记下了，这一趟不必真的跑")

        return boom

    monkeypatch.setattr(controller, "build_refresher", recorder)
    _scene(settings)

    _executor(settings, fake).maintenance_tick(
        scene_id=SCENE,
        subject_kind="account",
        subject_ref=SUBJECTS[0],
        checkpoint="",
        now=utc_now(),
    )

    copier = seen.get("copy_page")
    assert callable(copier), (
        "这一趟没有把镜像写入能力接进复核回调 —— 缺省那一支是「本部署落不下第二份」，"
        "于是两个主体抓同一个 URL 会各付一次，而账上只会显示「取回次数变多了」"
    )

    source = page_path(settings, scope="acct-a", url=MIRROR_URL, extension=".html")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(MIRROR_BODY)

    landed = copier(source, "acct-b", MIRROR_URL)

    assert landed == page_path(settings, scope="acct-b", url=MIRROR_URL, extension=".html")
    assert landed.read_bytes() == MIRROR_BODY


# ----------------------------------------------------------------------
# 二、`--once`：先争、再驱动、然后放手
# ----------------------------------------------------------------------
def test_a_once_run_acquires_drives_and_releases_in_that_order(
    monkeypatch, settings, events
):
    """``--once`` 也会先 acquire，跑完 release，而**顺序**是这条判据的全部内容。

    ``--once`` 是最容易写成「跑一轮看看」的那一种跑法，而它与常驻的那一个推的是同一批
    共享资源（模型预算、执行器、Quay 任务）。两种跑法都从同一处 CAS 进来，「不能并行
    双触发器」才是一个性质，而不是常驻模式自己的一条自觉。
    """
    _scene(settings)
    job_id = _admitted(settings)
    registry = FakeRegistry(events, owners=[_owner(settings.profile)])
    executor = RecordingExecutor(events)
    _wire(monkeypatch, settings=settings, registry=registry, executor=executor)
    out = _capture()

    assert controller.run_once_cli(
        settings=settings, holder=HOLDER, limit=None, out=out
    ) == 0

    assert events == ["acquire", "drive", "release"]
    assert executor.verbs == ["dispatch_batch"]
    assert registry.releases == [(HOLDER, 1)]
    # 驱动的是**盘上那一行**：业务键就是这次受理的作业。
    assert executor.calls[0]["job_id"] == job_id
    assert _row(settings, _dispatch_id(job_id)).state is ContinuationState.DONE


def test_a_once_run_that_cannot_acquire_drives_nothing_and_exits_three(
    monkeypatch, settings, events
):
    """归属在别人手里 → 退 ``3``，执行器一次都没被调用，盘上那一行**没动**。

    退 ``3`` 而不是 ``0``：它是这五个退出码里唯一一个「什么都没做错」的 —— 另一个
    控制器正在驱动同一个工作区，而本进程唯一正确的动作是什么都不做。并进 ``0`` 会让
    ``--once`` 在一台已经有控制器的机器上回「成功」，于是「我跑了一轮」与「我没跑」
    在脚本里长得一样。回执里那一格也要读得到**是谁**在推，否则「等一会儿再来」这句话
    没有对象。
    """
    _scene(settings)
    job_id = _admitted(settings)
    registry = FakeRegistry(
        events,
        owners=[None],
        current=_owner(settings.profile, holder=OTHER, epoch=3),
    )
    executor = RecordingExecutor(events)
    _wire(monkeypatch, settings=settings, registry=registry, executor=executor)
    out = _capture()

    code = controller.run_once_cli(
        settings=settings, holder=HOLDER, limit=None, out=out
    )

    assert code == controller.EXIT_NOT_OWNER == 3
    assert events == ["acquire"]
    assert executor.calls == []
    payload = _payloads(out.getvalue())[0]
    assert payload["drove"] is False
    assert payload["owner"]["holder"] == OTHER
    assert payload["owner"]["epoch"] == 3
    assert payload["owner"]["stale"] is False
    assert _row(settings, _dispatch_id(job_id)).state is ContinuationState.PENDING


def test_a_scene_the_gate_holds_shut_is_named_in_the_receipt(settings, fake):
    """被闸门挡住的场景 → 回执的 ``not_ticking`` 里**点名**说出是它、以及为什么。

    「这一轮什么都没做」有两条来路，而它们的下一步相反：盘上**没有到期的活**
    （什么都不用做）与**这个场景被暂停**（要人去按一个按钮）。省掉 ``not_ticking``，
    两者在一份回执上逐字相同 —— ``touched: 0`` 配几张空表，而运维只能从这两件事里
    猜一件去做。所以这一条同时判三格：那一格在、它点名的是 ``SCENE``、以及理由里带着
    那个暂停原因（``dispatch_refusal`` 的原文，逐字与派发侧同一句）。

    被挡住的是**这一轮**，不是这件事：那一行还在盘上、还是 ``PENDING``。

    ``truncation`` 在这一轮是 ``None``，而它**不能省**：``None`` 是「这一轮没有上限」，
    与「设了上限、一条都没砍」是两个值（``TruncationDecision.cap`` 的 ``None`` 逐字
    同一个含义）。少了这一格，上面那个 ``touched: 0`` 就再也说不出「没设上限」。
    """
    _scene(settings)
    job_id = _admitted(settings)
    pause(
        store=SqliteSceneStore(settings.database_path),
        scene_id=SCENE,
        at=utc_now(),
        reason=PauseReason.OPERATOR,
    )
    out = _capture()

    assert controller.run_once_cli(
        settings=settings, holder=HOLDER, limit=None, out=out
    ) == 0

    payload = _payloads(out.getvalue())[0]
    assert payload["touched"] == 0, "闸门关着，一行都不该被驱动"
    assert payload["done"] == [] and payload["failed"] == [] and payload["blocked"] == []
    assert [row["scene_id"] for row in payload["not_ticking"]] == [SCENE], (
        f"被关在外面的场景要点名，否则「没跑」这件事没有主语：{payload['not_ticking']!r}"
    )
    reason = payload["not_ticking"][0]["reason"]
    assert "operator_pause" in reason, (
        f"「没跑」要带上**为什么** —— 它答的是下一步该做什么（点 resume、等策略、"
        f"还是先去批准）：{reason!r}"
    )
    assert payload["truncation"] is None, (
        "这一轮没有上限：None 与「设了上限、一条都没砍」是两个值"
    )
    assert _row(settings, _dispatch_id(job_id)).state is ContinuationState.PENDING


def test_the_cap_that_dropped_work_is_in_the_receipt(monkeypatch, settings, fake):
    """上限砍掉了尾巴 → 回执报出 ``cap`` / ``kept`` / ``dropped`` 与**丢的是哪几条**。

    这一格的全部理由是 ``application/caps.py`` 的那句话：「没找到」与「找到了但被上限
    砍了」在报告里长得一模一样 —— 两个都只是 ``touched`` 变小。判据因此两边都钉：
    ``truncation`` 在（不然它与「这一轮没有上限」同形），以及它点名的那一条**还在盘上**
    且仍是 ``PENDING`` —— 被这一轮砍掉不等于被做掉，也不等于被丢掉。

    ``dropped_items`` 里的名字是**人能读**的那一种（``场景/种类/业务键``），不是那个
    哈希 id：人的下一条问题永远是「砍掉的是哪几条」。
    """
    _scene(settings)
    _admitted(settings, tag="r1")
    _admitted(settings, tag="r2")
    monkeypatch.setattr(controller, "default_dispatcher", lambda _s: fake)
    out = _capture()

    assert controller.run_once_cli(
        settings=settings, holder=HOLDER, limit=1, out=out
    ) == 0

    payload = _payloads(out.getvalue())[0]
    assert payload["touched"] == 1, f"上限是 1，动的该是 1 行：{payload['touched']!r}"
    decision = payload["truncation"]
    assert decision is not None, "砍了东西而回执不说，与这一轮没设上限同形"
    assert (decision["cap"], decision["kept"], decision["dropped"]) == (1, 1, 1), decision
    assert decision["truncated"] is True, decision
    assert len(decision["dropped_items"]) == 1, decision
    dropped = decision["dropped_items"][0]
    scene_id, kind, business_key = dropped.split("/", 2)
    assert (scene_id, kind) == (SCENE, ContinuationKind.DISPATCH_BATCH.value), dropped
    assert business_key, f"那几条的名字要人能读（场景/种类/业务键）：{dropped!r}"
    # 被砍掉的那一条**还在盘上、还在队列里** —— 砍掉不是做掉，也不是丢掉。
    row = _jobs(settings).continuation(
        continuation_id(SCENE, ContinuationKind.DISPATCH_BATCH, business_key)
    )
    assert row is not None and row.state is ContinuationState.PENDING, (
        f"被上限砍掉的那一行该原样留在队列里：{row!r}"
    )


# ----------------------------------------------------------------------
# 三、常驻：拿到什么就驱动什么，被顶掉就停手，干净退出就放手
# ----------------------------------------------------------------------
def test_a_resident_round_that_lost_its_epoch_stops_before_it_drives(
    monkeypatch, settings, events
):
    """心跳回 ``False`` → **不驱动**，回去重新争。

    心跳在驱动**之前**：上一轮结束时我还拿着归属，而这一轮醒来时它可能已经被别人顶掉了
    （我卡了一轮太久）。``False`` 不是一条日志，是回滚信号 —— 继续做下去就是 §10 逐字
    禁止的并行双触发器。这一格同时在钉收尾：被顶掉之后手里没有归属，那句 ``release``
    不该发生（真的注册表会因为它判 epoch 不符而拒，但那时「谁在推」已经在一次无意义的
    写里被搅过一次）。
    """
    _scene(settings)
    job_id = _admitted(settings)
    registry = FakeRegistry(
        events, owners=[_owner(settings.profile, epoch=2), None], beats=[False]
    )
    executor = RecordingExecutor(events)
    _wire(monkeypatch, settings=settings, registry=registry, executor=executor)
    stopper = _stopper_after(monkeypatch, sleeps=1)
    out = _capture()

    code = controller.run_resident(
        settings=settings,
        holder=HOLDER,
        limit=None,
        interval=0.0,
        out=out,
        stopper=stopper,
    )

    assert code == 0
    assert events == ["acquire", "heartbeat", "acquire"]
    assert executor.calls == []
    assert registry.releases == []
    assert out.getvalue() == ""
    assert _row(settings, _dispatch_id(job_id)).state is ContinuationState.PENDING


def test_a_resident_round_with_nothing_due_does_not_call_the_executor(
    monkeypatch, settings, events
):
    """没有到期的工作 → 一轮什么都不做，**执行器一次都没被调用**。

    §B21 逐字：「没有工作不调用模型」。这一格是它的落点，而它在报告上的样子是
    ``touched == 0`` 与 ``state == no_work`` —— 「还没有过事」。判据同时钉住那两格，
    因为一个把空轮记成「处理了 0 件」或把 ``no_work`` 报成 ``waiting_due`` 的实现，
    会让一次空转看起来像一次推进。
    """
    _scene(settings)
    registry = FakeRegistry(events, owners=[_owner(settings.profile)], beats=[True])
    executor = RecordingExecutor(events)
    _wire(monkeypatch, settings=settings, registry=registry, executor=executor)
    stopper = _stopper_after(monkeypatch, sleeps=1)
    out = _capture()

    assert controller.run_resident(
        settings=settings,
        holder=HOLDER,
        limit=None,
        interval=0.0,
        out=out,
        stopper=stopper,
    ) == 0

    payload = _payloads(out.getvalue())[0]
    assert executor.calls == []
    assert payload["touched"] == 0
    assert payload["state"] == "no_work"
    assert payload["ok"] is True
    assert registry.releases == [(HOLDER, 1)]


def test_a_resident_process_releases_cleanly_so_the_next_generation_starts_at_one(
    monkeypatch, settings, fake
):
    """常驻收到信号后跑完手头这一轮，**干净让出**：下一任从 ``epoch=1`` 开始。

    用**真的** ``SqliteWakeupRegistry``：这条判据问的是那一行还在不在。让出是删掉它
    （而不是把 ``heartbeat_at`` 改旧），理由是可观测的 —— 后者会让下一任以为自己在顶掉
    一个崩溃的进程，于是白等一个 ``OWNER_STALE_AFTER``（那 90 秒的空白会被读成
    「系统卡了」）。
    """
    _scene(settings)
    job_id = _admitted(settings)
    monkeypatch.setattr(controller, "default_dispatcher", lambda _s: fake)
    stopper = _stopper_after(monkeypatch, sleeps=1)
    out = _capture()

    assert controller.run_resident(
        settings=settings,
        holder=HOLDER,
        limit=None,
        interval=0.0,
        out=out,
        stopper=stopper,
    ) == 0

    assert _payloads(out.getvalue())[0]["done"] != []
    registry = SqliteWakeupRegistry(settings.database_path)
    assert registry.current(settings.profile) is None
    successor = registry.acquire(settings.profile, holder="next", now=utc_now())
    assert successor is not None
    assert successor.epoch == 1
    # 这一轮的产出是真的：外面确实多出了那两件任务。
    assert len(fake.created_units) == len(DOSSIERS)
    assert _row(settings, _dispatch_id(job_id)).state is ContinuationState.DONE


def test_a_resident_process_without_ownership_waits_and_does_not_steal(
    monkeypatch, settings, fake
):
    """归属在别人手里 → 等（不驱动、也不抢），而那一行**还是他的**。

    用真的注册表、真的竞争：另一位持有者的心跳是新鲜的，所以本进程的 ``acquire`` 得到
    ``None``。判据落在三格上：外部一件都没被交出去、盘上那一行还是 ``PENDING``（没被
    驱动）、以及那一位持有者的行原样还在（既有 ``holder``，也是既有 ``epoch``）——
    第三格排除的实现是「抢不到就把它顶掉」，而那正是并行双触发器。第四格是「等」本身：
    循环回来又试了一次 ``acquire``，而不是转身退出（常驻的那一个没有下一次就是它的
    全部用处）。
    """
    _scene(settings)
    job_id = _admitted(settings)
    monkeypatch.setattr(controller, "default_dispatcher", lambda _s: fake)
    held = SqliteWakeupRegistry(settings.database_path).acquire(
        settings.profile, holder=OTHER, now=utc_now()
    )
    assert held is not None and held.epoch == 1
    stopper = _stopper_after(monkeypatch, sleeps=1)
    out = _capture()

    assert controller.run_resident(
        settings=settings,
        holder=HOLDER,
        limit=None,
        interval=0.0,
        out=out,
        stopper=stopper,
    ) == 0

    assert out.getvalue() == ""
    assert fake.calls == []
    assert _row(settings, _dispatch_id(job_id)).state is ContinuationState.PENDING
    still = SqliteWakeupRegistry(settings.database_path).current(settings.profile)
    assert still is not None
    assert (still.holder, still.epoch) == (OTHER, 1)


# ----------------------------------------------------------------------
# 四、纵向：受理之后客户端不在了，下一趟由盘上那一行推起来
# ----------------------------------------------------------------------
def test_the_work_after_the_client_is_gone_is_driven_by_the_row_on_disk(
    monkeypatch, settings, fake
):
    """受理（并编译）→ **没有任何客户端** → 两趟控制器把这批活交出去、把复核跑掉。

    这一条要证的就是 §B21 的关闭条件那半句：「正常 MCP 提交后断开客户端，第二批确实
    自动执行并回池」。本文件里「断开客户端」的准确含义是：**受理与编译之后，再也没有
    任何第三方调用**，接下来发生的一切都由 ``run_once_cli`` 触发 —— 而它推的是盘上那
    两行（受理欠下的派发、结算欠下的复核）。

    ## 两趟是**两个进程**

    每一趟都是一次独立的 ``_build``（新的存储、新的注册表、新的执行器），并且两趟之间
    注册表是空的 —— 上一趟让出了归属，下一趟从 ``epoch=1`` 开始。这正是「重启之后接着
    跑」的形状，只是这里是干净退出而不是崩溃（崩溃那一条在
    ``tests/test_sqlite_continuation.py`` 与 ``test_continuation_controller.py`` 的
    ``_reclaim`` 判据里）。

    ## 「回池」这个词按本仓实际可核的那一半写

    可核的是：结算落账、findings 回读得出来（研究结果回到了账上），以及它欠下的复核被
    下一趟跑掉。**账号池**那一半（``remember_accounts``）是另一条工具，本片不经过它 ——
    所以这里不声称「账号回到了池子里」，那不是这一条判据量到的东西。
    """
    _scene(settings)
    job_id = _admitted(settings)
    monkeypatch.setattr(controller, "default_dispatcher", lambda _s: fake)
    first = _capture()

    assert controller.run_once_cli(
        settings=settings, holder=HOLDER, limit=None, out=first
    ) == 0

    round_one = _payloads(first.getvalue())[0]
    assert [row["kind"] for row in round_one["done"]] == [
        ContinuationKind.DISPATCH_BATCH.value
    ]
    assert len(fake.created_units) == len(DOSSIERS)
    # 一轮跑完之后「为什么此刻没有更多工作在跑」是 ``complete``（曾经做完过至少一件，
    # 而此刻一件都不欠）：那两件工作在**外面**跑，而它们不产生本地续跑意图。这一格不是
    # 为了好看，它是「盘上没有欠账」这句话的判词 —— 下一趟由结算欠下的那一行重新把它
    # 填上。
    assert round_one["state"] == "complete"
    assert SqliteWakeupRegistry(settings.database_path).current(settings.profile) is None

    # 外部执行器跑完一件：worker 领租约、结算。这一步在部署里是另一个进程（research
    # 侧），所以这里直接落账 —— 结果是同一条记录。
    store = _jobs(settings)
    item = store.work_items(job_id)[0]
    attempt = store.lease(item.work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=FOUND)
    settled = store.settlement(item.work_item_id)
    assert settled is not None
    assert settled.findings == FOUND

    second = _capture()
    assert controller.run_once_cli(
        settings=settings, holder=HOLDER, limit=None, out=second
    ) == 0

    round_two = _payloads(second.getvalue())[0]
    # 那一行**被驱动了**，而它这一次报的是「还要再试」而不是「做完了」：§B22 之后
    # 一次复核认领三个面，另外两个（角色 / 邮箱证据）要看**已知的页面**，而这个主体
    # 名下一条证据都没有 —— 空页面集是不可达，不写观测。
    #
    # 这一格在这一份回执上原来**看不见**（``retrying`` 没有被转发，于是
    # ``touched: 1`` 配四个空表），而那正是这个函数开头那句要防的形态。现在它在
    # ``retrying`` 里，并且与 ``failed`` 分得开：前者还要再试，后者等人看一眼。
    assert [row["kind"] for row in round_two["retrying"]] == [
        ContinuationKind.MAINTENANCE_TICK.value
    ]
    assert round_two["done"] == [] and round_two["failed"] == []
    assert round_two["touched"] == 1, "这一轮真的动过一行 —— 而它现在列得出来"
    assert round_two["retrying"][0]["attempts"] == 1
    assert SUBJECTS[0] in round_two["retrying"][0]["reason"]
    # 复核跑掉了，而**外部一件都没被再碰**：第二趟不存在派发意图（那一行已经 DONE），
    # 所以「恢复正常之后不重复昂贵工作」在这一条链上是结构性的，不是靠一张去重表。
    assert fake.calls.count("create") == len(DOSSIERS)
    assert len(fake.tasks) == len(DOSSIERS)
    observations = SqliteMaintenanceStore(settings.database_path).observations(
        workspace=settings.profile
    )
    assert [row.subject_id for row in observations] == [SUBJECTS[0]]
    # 两趟都没留下持有者，且下一次接手仍是第一代：干净退出与崩溃是两件事。
    assert SqliteWakeupRegistry(settings.database_path).current(settings.profile) is None


# ----------------------------------------------------------------------
# 入口参数
# ----------------------------------------------------------------------
def test_the_limit_zero_means_uncapped_and_that_translation_happens_in_main(
    monkeypatch, settings
):
    """``--limit 0`` 是「不封顶」这句人话，而 ``None`` 是那个参数的取值。

    翻译只在 ``main`` 那一处，所以这一条判据**走 ``main`` 而不是只读 ``argv``**：
    ``parser.parse_args(...).limit == 0`` 与 ``(0 or None) is None`` 两句判的都是
    Python 自己，把 ``limit = args.limit or None`` 整行删掉它们照样绿。这里把
    ``run_once_cli`` 换成一个记账的壳，读的是它**真的收到**了什么。
    """
    seen: list[dict] = []
    monkeypatch.setattr(controller, "load_settings", lambda: settings)
    monkeypatch.setattr(
        controller, "run_once_cli", lambda **kwargs: seen.append(kwargs) or 0
    )

    assert controller.main(["--once", "--limit", "0"]) == 0
    assert controller.main(["--once", "--limit", "3"]) == 0
    assert [call["limit"] for call in seen] == [None, 3]
    assert [call["holder"] for call in seen] == [HOLDER] * 2
    # 缺省是**有界**的：§B21 逐字禁止用「持续运行」绕无限预算，而常驻模式的「不小心」
    # 是**一直**多花。
    assert controller.build_parser().parse_args([]).limit == controller.DEFAULT_LIMIT
    # 常驻那条分支也走同一处翻译：``--once`` 与常驻共用一个 ``limit``。
    monkeypatch.setattr(
        controller,
        "run_resident",
        lambda **kwargs: seen.append(kwargs) or 0,
    )
    assert controller.main(["--limit", "0", "--interval", "0"]) == 0
    assert seen[-1]["limit"] is None
    assert seen[-1]["interval"] == 0.0

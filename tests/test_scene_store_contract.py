"""``SceneStore`` / ``MaintenanceStore`` 契约：**同一组判据，对着两个实现各跑一遍**。

## 为什么这两个端口各自只有一个实现是**不够**的

``ports/scenes.py`` 抽出端口时点名的理由是「写频率差一个数量级、守的失败不同」，
但端口本身不构成契约 —— 构成契约的是**同一组判据同时看着两个实现**。本仓已有的
先例是 ``tests/test_evidence_store_contract.py``（三个实现）与
``tests/test_budget_contract.py``（两个实现）：判据写在这里、实现各自在文件里，
一个实现改了语义另一个不会红，才叫「这是一个契约」。

两个实现：

* ``adapters/sqlite/scenes.py`` —— 主存。CAS 由 ``BEGIN IMMEDIATE`` 背书。
* ``adapters/memory.py`` —— 测试替身。**没有锁**。

## 有一条判据**刻意不在这里**

「写完重开一个 store 还能读到」只对持久化实现成立，内存实现按定义做不到。把它写
进来就只能给它开例外，而契约里出现第一个例外之后第二个就不会有人再争论了 ——
与 ``test_evidence_store_contract.py`` 同一条处置。那条判据在
``tests/test_sqlite_scenes.py``。

## 这里有两处判据是**时刻规范化的执行器**

``to_utc`` 把每个时刻字段规范到 UTC（``domain/util.py``）。没有它，ISO-8601 字符串
的大小顺序就等于不了瞬间的先后，而「取最近一次观测」会静默取到一个更早的。两处
判据（``test_signals_order_by_instant_not_by_offset`` 与
``test_same_instant_in_two_offsets_is_one_row``）是那条修复的**唯一**执行器 ——
去掉它们，``to_utc`` 就退回成一段自觉。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryMaintenanceStore, InMemorySceneStore
from marketops.adapters.sqlite.scenes import (
    SqliteMaintenanceStore,
    SqliteSceneStore,
)
from marketops.domain.enums import BudgetLine
from marketops.domain.errors import SceneConflictError, StoreError
from marketops.domain.maintenance import (
    Aspect,
    ChangeSignal,
    MaintenanceClaim,
    MaintenanceState,
    Observation,
    RefreshPlan,
    claim_of,
    what_is_due,
)
from marketops.domain.outreach import OutreachPolicyDecl
from marketops.domain.scene import (
    PauseReason,
    SceneBudgetLine,
    SceneRecord,
    SceneState,
    SceneVersion,
)

UTC = timezone.utc
#: 一个**非** UTC 的偏移量。用它构造的时刻在字符串排序与瞬间排序下顺序相反 ——
#: 这正是下面两条判据要抓的东西。
PLUS_EIGHT = timezone(timedelta(hours=8))

T0 = datetime(2026, 9, 23, 1, 0, tzinfo=UTC)
WORKSPACE = "ws-natural-grass"


@pytest.fixture(params=["sqlite", "memory"])
def scene_store(request, tmp_path: Path):
    """两个实现之一。名字进测试 id —— 红了的时候一眼看得见是哪一个。"""
    if request.param == "sqlite":
        return SqliteSceneStore(tmp_path / "state" / "marketops.db")
    return InMemorySceneStore()


@pytest.fixture(params=["sqlite", "memory"])
def maintenance_store(request, tmp_path: Path):
    if request.param == "sqlite":
        return SqliteMaintenanceStore(tmp_path / "state" / "marketops.db")
    return InMemoryMaintenanceStore()


# --- 构造器 -------------------------------------------------------------------


def make_version(scene_id: str = "scene-a", version: int = 1, **overrides) -> SceneVersion:
    payload = {
        "scene_id": scene_id,
        "workspace": WORKSPACE,
        "version": version,
        "region": ("GB-England-Devon",),
        "scenario": "sports-surface-maintenance",
        "product": "acme-natural-grass",
        "admissions": ("sports-club",),
        "exclusions": ("golf-course", "bowling-green"),
        "role_policy": "role-policy-v1",
        "source_plan": ("mother-pool-import",),
        "budget": (SceneBudgetLine(line=BudgetLine.SEARCH, ceiling=20),),
        "refresh": RefreshPlan.uncalibrated_default(),
        "outreach": OutreachPolicyDecl.uncalibrated_default(),
        "profile_id": "uk-acme-natural-grass",
        "profile_version": "2026-09-01",
        "created_at": T0,
        "supersedes": None if version == 1 else version - 1,
    }
    payload.update(overrides)
    return SceneVersion(**payload)


def make_record(scene_id: str = "scene-a", **overrides) -> SceneRecord:
    payload = {
        "scene_id": scene_id,
        "workspace": WORKSPACE,
        "state": SceneState.DRAFT,
        "latest_version": 1,
        "created_at": T0,
        "updated_at": T0,
    }
    payload.update(overrides)
    return SceneRecord(**payload)


def make_observation(
    subject_id: str = "asset-a", *, aspect: Aspect = Aspect.SOURCE_COVERAGE, at: datetime = T0, **o
) -> Observation:
    payload = {
        "workspace": WORKSPACE,
        "subject_kind": "asset",
        "subject_id": subject_id,
        "aspect": aspect,
        "observed_at": at,
        "source_ref": "receipt-1",
    }
    payload.update(o)
    return Observation(**payload)


def make_signal(
    subject_id: str = "asset-a", *, aspect: Aspect = Aspect.ASSET_ATTRIBUTE, at: datetime = T0, **o
) -> ChangeSignal:
    payload = {
        "workspace": WORKSPACE,
        "subject_kind": "asset",
        "subject_id": subject_id,
        "aspect": aspect,
        "signalled_at": at,
        "reason": "operator-reported-change",
    }
    payload.update(o)
    return ChangeSignal(**payload)


# --- 版本：往返与不可覆盖 -----------------------------------------------------


def test_version_round_trip(scene_store) -> None:
    scene_store.put_version(make_version())

    loaded = scene_store.get_version("scene-a", 1)
    assert loaded is not None
    assert loaded.region == ("GB-England-Devon",)
    assert loaded.budget[0].line is BudgetLine.SEARCH
    assert loaded.frozen_sha256 == make_version().frozen_sha256


def test_missing_version_is_none_not_an_error(scene_store) -> None:
    """「没有这一版」是一次正常的读取结果。抛异常会让每个调用点都要写 try。"""
    assert scene_store.get_version("scene-a", 7) is None
    assert scene_store.versions("scene-a") == ()


def test_versions_come_back_ascending(scene_store) -> None:
    """顺序是判据的一部分：报告与影响比对都按版本先后读。"""
    scene_store.put_version(make_version(version=2))
    scene_store.put_version(make_version(version=1))

    assert [v.version for v in scene_store.versions("scene-a")] == [1, 2]


def test_rewriting_the_same_version_identical_is_idempotent(scene_store) -> None:
    scene_store.put_version(make_version())
    scene_store.put_version(make_version())

    assert len(scene_store.versions("scene-a")) == 1


def test_rewriting_the_same_version_with_other_content_is_refused(scene_store) -> None:
    """版本号是这一版规则的摘要。同号不同内容只可能是版本号被手工指定过。

    **不覆盖** —— 覆盖会让按版本号引用它的工作单元静默换了规则。
    """
    scene_store.put_version(make_version())
    with pytest.raises(StoreError):
        scene_store.put_version(make_version(region=("GB-Scotland",)))

    assert scene_store.get_version("scene-a", 1).region == ("GB-England-Devon",)


def test_a_version_cannot_change_workspace(scene_store) -> None:
    """同一个 scene_id 出现在两个工作区，是 §3.2 那条授权链断掉的样子。"""
    scene_store.put_version(make_version())
    with pytest.raises(StoreError):
        scene_store.put_version(make_version(workspace="ws-other"))

    assert scene_store.get_version("scene-a", 1).workspace == WORKSPACE


# --- 状态：CAS ---------------------------------------------------------------


def test_record_round_trip(scene_store) -> None:
    scene_store.put_record(make_record(), expected_revision=None)

    loaded = scene_store.get_record("scene-a")
    assert loaded is not None
    assert loaded.state is SceneState.DRAFT
    assert loaded.latest_version == 1
    assert loaded.effective_version is None
    assert loaded.revision == 0


def test_missing_record_is_none(scene_store) -> None:
    assert scene_store.get_record("scene-a") is None


def test_expected_revision_none_means_must_not_exist(scene_store) -> None:
    """``None`` 是「必须不存在」，不是「不做检查」—— 它挡的是并发重复立项。"""
    scene_store.put_record(make_record(), expected_revision=None)
    with pytest.raises(SceneConflictError):
        scene_store.put_record(make_record(), expected_revision=None)


def test_create_with_nonzero_revision_is_refused(scene_store) -> None:
    with pytest.raises(StoreError):
        scene_store.put_record(
            make_record(revision=3), expected_revision=None
        )


def test_update_bumps_the_revision(scene_store) -> None:
    scene_store.put_record(make_record(), expected_revision=None)
    paused = make_record(
        state=SceneState.PAUSED,
        pause_reason=PauseReason.OPERATOR,
        effective_version=1,
        updated_at=T0 + timedelta(days=1),
        revision=1,
    )
    scene_store.put_record(paused, expected_revision=0)

    loaded = scene_store.get_record("scene-a")
    assert loaded.state is SceneState.PAUSED
    assert loaded.pause_reason is PauseReason.OPERATOR
    assert loaded.revision == 1


def test_stale_revision_writes_nothing(scene_store) -> None:
    """两个控制端同时读到 PAUSED、同时决定 resume —— 只有一个能写成。

    这条判据的**后半句**（库里还是旧值）与前半句同样重要：一个先写后判的实现
    会抛对但已经落盘，而调用方按「一个字都没写」去处置，就会把一个人的决定
    当成没发生过。
    """
    scene_store.put_record(make_record(), expected_revision=None)
    scene_store.put_record(make_record(revision=1, updated_at=T0), expected_revision=0)

    with pytest.raises(SceneConflictError):
        scene_store.put_record(
            make_record(state=SceneState.ARCHIVED, revision=1, updated_at=T0 + timedelta(days=2)),
            expected_revision=0,
        )

    assert scene_store.get_record("scene-a").state is SceneState.DRAFT


def test_update_of_a_missing_record_is_refused(scene_store) -> None:
    with pytest.raises(SceneConflictError):
        scene_store.put_record(make_record(revision=1), expected_revision=0)


def test_update_must_advance_exactly_one_revision(scene_store) -> None:
    """挡的是「改完对象直接写回」：那跳过了一次自增，并发的另一个写者会**成功**。"""
    scene_store.put_record(make_record(), expected_revision=None)
    with pytest.raises(StoreError):
        scene_store.put_record(
            make_record(revision=0, state=SceneState.PAUSED, pause_reason=PauseReason.POLICY),
            expected_revision=0,
        )


def test_records_are_listed_by_id_and_filterable_by_workspace(scene_store) -> None:
    scene_store.put_record(make_record("scene-b"), expected_revision=None)
    scene_store.put_record(make_record("scene-a"), expected_revision=None)
    scene_store.put_record(
        make_record("scene-c", workspace="ws-other"), expected_revision=None
    )

    assert [r.scene_id for r in scene_store.records()] == ["scene-a", "scene-b", "scene-c"]
    assert [r.scene_id for r in scene_store.records(workspace=WORKSPACE)] == [
        "scene-a",
        "scene-b",
    ]
    assert [r.scene_id for r in scene_store.records(workspace="ws-nobody")] == []


def test_approvals_round_trip_with_the_record(scene_store) -> None:
    """``approvals`` 是「谁批准了第几版」这个问题的唯一答案。丢了它，激活就成了
    一次没有主体的状态变化。"""
    scene_store.put_record(make_record(), expected_revision=None)
    activated = make_record().activate(version=1, at=T0, by="controller-1")
    scene_store.put_record(activated, expected_revision=0)

    loaded = scene_store.get_record("scene-a")
    assert loaded.state is SceneState.ACTIVE
    assert len(loaded.approvals) == 1
    assert loaded.approvals[0].by == "controller-1"
    assert loaded.approvals[0].version == 1


# --- 观测 --------------------------------------------------------------------


def test_observation_round_trip(maintenance_store) -> None:
    maintenance_store.put_observation(make_observation())

    loaded = maintenance_store.observations(workspace=WORKSPACE)
    assert len(loaded) == 1
    assert loaded[0].subject_id == "asset-a"
    assert loaded[0].aspect is Aspect.SOURCE_COVERAGE
    assert loaded[0].observed_at == T0


def test_identical_observation_twice_is_idempotent(maintenance_store) -> None:
    maintenance_store.put_observation(make_observation())
    maintenance_store.put_observation(make_observation())

    assert len(maintenance_store.observations(workspace=WORKSPACE)) == 1


def test_same_cell_at_two_instants_is_two_rows(maintenance_store) -> None:
    """**不去重同一格的不同时刻** —— 合并等于让「它变了」从记录里消失。"""
    maintenance_store.put_observation(make_observation(at=T0))
    maintenance_store.put_observation(make_observation(at=T0 + timedelta(days=90)))

    loaded = maintenance_store.observations(workspace=WORKSPACE)
    assert [o.observed_at for o in loaded] == [T0, T0 + timedelta(days=90)]


def test_observations_are_scoped_by_workspace(maintenance_store) -> None:
    """原始事实在一个工作区内可复用，**跨工作区不可见** —— 这是 §3.2 那条链的
    第一段，而它只有在这条判据下才是一条链而不是一句声明。"""
    maintenance_store.put_observation(make_observation())
    maintenance_store.put_observation(make_observation(workspace="ws-other"))

    assert len(maintenance_store.observations(workspace=WORKSPACE)) == 1
    assert len(maintenance_store.observations(workspace="ws-other")) == 1


def test_observations_can_be_narrowed_by_subject_kind(maintenance_store) -> None:
    maintenance_store.put_observation(make_observation("asset-a"))
    maintenance_store.put_observation(
        make_observation("account-a", subject_kind="account")
    )

    only_assets = maintenance_store.observations(workspace=WORKSPACE, subject_kind="asset")
    assert [o.subject_id for o in only_assets] == ["asset-a"]


def test_observations_order_by_subject_then_aspect_then_instant(maintenance_store) -> None:
    maintenance_store.put_observation(make_observation("asset-b"))
    maintenance_store.put_observation(make_observation("asset-a", aspect=Aspect.PRODUCT_FIT))
    maintenance_store.put_observation(make_observation("asset-a", aspect=Aspect.ASSET_ATTRIBUTE))

    loaded = maintenance_store.observations(workspace=WORKSPACE)
    assert [(o.subject_id, o.aspect.value) for o in loaded] == [
        ("asset-a", "asset_attribute"),
        ("asset-a", "product_fit"),
        ("asset-b", "source_coverage"),
    ]


# --- 变更信号 ----------------------------------------------------------------


def test_signal_round_trip(maintenance_store) -> None:
    maintenance_store.put_signal(make_signal())

    loaded = maintenance_store.signals(workspace=WORKSPACE)
    assert len(loaded) == 1
    assert loaded[0].reason == "operator-reported-change"


def test_identical_signal_twice_is_idempotent(maintenance_store) -> None:
    maintenance_store.put_signal(make_signal())
    maintenance_store.put_signal(make_signal())

    assert len(maintenance_store.signals(workspace=WORKSPACE)) == 1


def test_signals_order_by_instant_not_by_offset(maintenance_store) -> None:
    """**时刻规范化的执行器。**

    ``2026-09-23T09:00:00+08:00`` 与 ``2026-09-23T05:00:00+00:00`` 是两个瞬间，
    就差在前者在后。按 ISO 字符串排序会把它们**读反**，而按瞬间排序不会。
    去掉 ``to_utc`` 这条判据立刻红。
    """
    later = make_signal(subject_id="asset-later", at=datetime(2026, 9, 23, 5, 0, tzinfo=UTC))
    earlier_in_instant = make_signal(
        subject_id="asset-earlier", at=datetime(2026, 9, 23, 9, 0, tzinfo=PLUS_EIGHT)
    )
    maintenance_store.put_signal(later)
    maintenance_store.put_signal(earlier_in_instant)

    loaded = maintenance_store.signals(workspace=WORKSPACE)
    assert [s.subject_id for s in loaded] == ["asset-earlier", "asset-later"]
    assert loaded[0].signalled_at == datetime(2026, 9, 23, 1, 0, tzinfo=UTC)


def test_same_instant_in_two_offsets_is_one_row(maintenance_store) -> None:
    """同一个瞬间的两种写法是**同一条信号**，不是两条。

    没有规范化时它们是两行，而报告上只显示「信号多了一条」。
    """
    maintenance_store.put_signal(make_signal(at=datetime(2026, 9, 23, 9, 0, tzinfo=PLUS_EIGHT)))
    maintenance_store.put_signal(make_signal(at=datetime(2026, 9, 23, 1, 0, tzinfo=UTC)))

    assert len(maintenance_store.signals(workspace=WORKSPACE)) == 1


def test_signals_are_scoped_by_workspace(maintenance_store) -> None:
    maintenance_store.put_signal(make_signal())
    maintenance_store.put_signal(make_signal(workspace="ws-other"))

    assert len(maintenance_store.signals(workspace=WORKSPACE)) == 1


# --- 回执：认领 ---------------------------------------------------------------


def _real_claim(**o) -> MaintenanceClaim:
    """走 ``claim_of`` 产生一条真回执 —— 键是算出来的，不是编的。"""
    plan = RefreshPlan.uncalibrated_default()
    due = what_is_due(
        scene_id="scene-a",
        scene_version=1,
        plan=plan,
        observations=(),
        signals=(),
        now=T0,
        subjects=(("asset", "asset-a"),),
    )[0]
    return claim_of(due, workspace=WORKSPACE, at=T0)


def _claim_it(store, receipt) -> MaintenanceClaim:
    """认领并断言**本次确实拿到了**。

    下面这些用例考的是回执本身（去重、幂等、结账），不是争抢；把 ``won`` 的
    断言摊在每一处会让它们看起来都在考并发。争抢那一半由
    ``test_a_second_claim_does_not_create_a_row`` 与
    ``tests/test_sqlite_scenes.py`` 的跨连接那条承载。
    """
    outcome = store.claim(receipt)
    assert outcome.won, f"本次没有拿到 {receipt.dedupe_key!r}：{outcome.claim.state.value}"
    return outcome.claim


def test_claim_round_trip(maintenance_store) -> None:
    """一条回执写进去再读出来，**逐字**是原来那条。

    ``reason`` 在这里被单独点出来：它是从 ``DueWork`` 带过来的，「这一格是周期
    到了还是被信号拉过来的」靠它区分，而它是最容易被存储层漏掉的一列 ——
    漏掉之后 ``claims`` 读回来会在 ``DueReason`` 上直接炸，但如果实现选了一个
    默认值，就会读成一条**看起来正常但理由错了**的回执。
    """
    claim = _real_claim()
    got = _claim_it(maintenance_store, claim)

    assert got.state is MaintenanceState.CLAIMED
    assert got.attempt == 1
    assert got.reason is claim.reason
    assert maintenance_store.claims("scene-a") == (got,)


def test_a_second_claim_does_not_create_a_row(maintenance_store) -> None:
    """**重启或重复 tick 不得重复计费** —— 这一条的全部内容。

    第二次认领不是「它又跑了一遍」：还在 ``CLAIMED`` 意味着有人在跑，本次调用
    **没有**拿到它。库里的行数必须还是 1。
    """
    claim = _real_claim()
    first = maintenance_store.claim(claim)
    # 第二次带着**不同的**时刻来 —— 时刻不同都不该让它拿到，因为那一格是别人的。
    second = maintenance_store.claim(
        claim.model_copy(update={"claimed_at": T0 + timedelta(hours=1)})
    )

    assert first.won is True
    assert second.won is False
    assert second.claim.attempt == first.claim.attempt == 1
    assert second.claim.claimed_at == T0, "库里那一行没被改写"
    assert len(maintenance_store.claims("scene-a")) == 1


def test_the_same_instant_twice_still_only_one_of_them_won(maintenance_store) -> None:
    """两个驱动带**同一个时刻**来抢同一格：仍然只有一个拿到。

    改这条判据之前，答案是「两个都拿到」—— 判法是「``claimed_at`` 是否等于我请求的
    时刻」，而 ``now`` 在这一层是**参数**而不是时钟，所以两个驱动传同一个时刻不是
    巧合，是可构造的。真正知道答案的只有执行那次插入的存储。
    """
    claim = _real_claim()
    first = maintenance_store.claim(claim)
    second = maintenance_store.claim(claim)

    assert first.won is True
    assert second.won is False
    assert first.claim == second.claim
    assert len(maintenance_store.claims("scene-a")) == 1


def test_finish_done_is_recorded(maintenance_store) -> None:
    claim = _claim_it(maintenance_store, _real_claim())
    maintenance_store.finish(
        claim.model_copy(
            update={
                "state": MaintenanceState.DONE,
                "finished_at": T0 + timedelta(minutes=5),
                "result_ref": "receipt:abc",
            }
        )
    )

    stored = maintenance_store.claims("scene-a")[0]
    assert stored.state is MaintenanceState.DONE
    assert stored.result_ref == "receipt:abc"
    assert stored.failure == ""


def test_finishing_the_same_way_twice_is_idempotent(maintenance_store) -> None:
    claim = _claim_it(maintenance_store, _real_claim())
    done = claim.model_copy(
        update={
            "state": MaintenanceState.DONE,
            "finished_at": T0 + timedelta(minutes=5),
            "result_ref": "receipt:abc",
        }
    )
    maintenance_store.finish(done)
    maintenance_store.finish(done)

    assert len(maintenance_store.claims("scene-a")) == 1


def test_finishing_differently_twice_is_refused(maintenance_store) -> None:
    """两个执行者都以为自己在跑同一次尝试 —— 静默取一个会让另一次的产出连同它
    花掉的钱一起消失。"""
    claim = _claim_it(maintenance_store, _real_claim())
    maintenance_store.finish(
        claim.model_copy(
            update={
                "state": MaintenanceState.DONE,
                "finished_at": T0 + timedelta(minutes=5),
                "result_ref": "receipt:first",
            }
        )
    )
    with pytest.raises(StoreError):
        maintenance_store.finish(
            claim.model_copy(
                update={
                    "state": MaintenanceState.FAILED,
                    "finished_at": T0 + timedelta(minutes=6),
                    "failure": "第二个执行者的结论",
                }
            )
        )


def test_finishing_something_never_claimed_is_refused(maintenance_store) -> None:
    """一条「凭空结掉」的记录会让一件没做过的工作在报告里显示为做完。"""
    with pytest.raises(StoreError):
        maintenance_store.finish(
            _real_claim().model_copy(
                update={
                    "state": MaintenanceState.DONE,
                    "finished_at": T0,
                    "result_ref": "receipt:x",
                }
            )
        )


def test_claiming_with_a_terminal_receipt_is_refused(maintenance_store) -> None:
    with pytest.raises(StoreError):
        maintenance_store.claim(
            _real_claim().model_copy(update={"state": MaintenanceState.DONE, "finished_at": T0})
        )


def test_a_failed_cell_can_be_claimed_again_and_the_rows_accumulate(
    maintenance_store,
) -> None:
    """失败过几次是运维要看的数。就地改这一行会让它永远是 1。"""
    claim = _claim_it(maintenance_store, _real_claim())
    maintenance_store.finish(
        claim.model_copy(
            update={
                "state": MaintenanceState.FAILED,
                "finished_at": T0 + timedelta(minutes=5),
                "failure": "抓取超时",
            }
        )
    )

    outcome = maintenance_store.claim(
        claim.model_copy(update={"claimed_at": T0 + timedelta(hours=1)})
    )
    assert outcome.won is True, "FAILED 的格子可以被重做 —— 那次是新的一行"
    retried = outcome.claim
    assert retried.attempt == 2
    assert retried.state is MaintenanceState.CLAIMED
    assert retried.claimed_at == T0 + timedelta(hours=1)

    rows = maintenance_store.claims("scene-a")
    assert [(r.attempt, r.state) for r in rows] == [
        (1, MaintenanceState.FAILED),
        (2, MaintenanceState.CLAIMED),
    ]


def test_a_key_that_points_at_another_cell_is_refused(maintenance_store) -> None:
    """键是内容寻址的。同一个键指了两格只可能意味着记录是手工构造的 —— 而那一次
    认领会安静地落在**别人的**格子上，报告里那一格显示为「刚核过」。"""
    claim = _real_claim()
    _claim_it(maintenance_store, claim)

    with pytest.raises(StoreError):
        maintenance_store.claim(claim.model_copy(update={"subject_id": "asset-b"}))


def test_claims_are_listed_per_scene(maintenance_store) -> None:
    _claim_it(maintenance_store, _real_claim())
    assert maintenance_store.claims("scene-b") == ()

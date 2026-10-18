"""§G2' 的纵向验收：**断开全部控制客户端之后**的自动执行、重放、暂停与三类时间。

## 这一条证的是哪一段

``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md`` 的 **G2** 那一节（约 ``:383-393``）逐字：

> 在 G1 相同状态上再做多会话/持续经营：…… → **断开全部控制客户端** →
> **下一批/Watch按Schedule自动执行** → **重启控制器** → **重放事件** →
> **暂停UK且France继续** → 恢复 → 模拟到期 → ……

以及同一段里那三句承重的话：

> 区分三种时间：**即时重复 tick**（应无重复工作）、**来源新证据产生后的 tick**（可能
> 合理有工作）、**真实到期后外部 revalidation**（需要获取/验证回执）。
> **单次 ``claimed=0`` 不能证明后两者。**
>
> **业务 Event 不可重复，sink 失败不能触发研究重跑**。

## 「断开全部控制客户端」在这一份里落在哪一行上

这一句**不是**修辞。它的执行器是 ``marketops/interfaces/trigger_driver.py`` —— 一个
**进程**，它的模块 docstring 逐字论证了为什么承载它的不能是一条 MCP 工具：「工具要有
客户端来调，而此刻客户端全断了」。所以这一份里最承重的那几条判据，**走的是驱动进程
的入口**（:func:`~marketops.interfaces.trigger_driver.run_once`），不是 MCP 调用 ——
而它们的**盘上读数**仍然走同一个数据根。

## 三类时间：这一份**分开**了哪两种，哪一种分不开

计划逐字：「区分三种时间：**即时重复 tick**（应无重复工作）、**来源新证据产生后的
tick**（可能合理有工作）、**真实到期后外部 revalidation**（需要获取/验证回执）。
**单次 ``claimed=0`` 不能证明后两者。**」这一份逐条如实写清：

1. **即时重复 tick —— 判到了。** 同一个受控时刻醒来第二次、以及同一个 ``fire_id``
   再点一次：回执 ``replayed=true``，而盘上 ``research_run`` / ``schedule_fire``
   **一行都不涨**（:func:`test_replaying_the_same_fire_at_the_same_moment_writes_no_second_run`、
   :func:`test_an_same_fire_replayed_through_the_use_case_reads_back_the_same_row`）。
2. **真实到期后外部 revalidation —— 只判到了「到期」那一半。** 受控时钟走过
   ``next_due_at`` ⇒ 点火、受理、盘上多一行 Run（:func:`test_a_due_schedule_fires_with_no_control_client_connected`）。
   「**获取/验证回执**」那半（真去外调一次并拿回一张回执）在本部署**取不到读数** ——
   没有 provider，见下面那一节。**所以这一条不许被读成「revalidation 验过了」。**
3. **来源新证据产生后的 tick —— 判不到。** 它的承载者是维护面
   （``interfaces/controller.py`` → ``application/maintenance.run_once``），而「新证据」
   要有提供方才产生得出来。本片既没有那个面、也没有那个提供方。
   **因此：一份 ``claimed=0`` 的回执在这里只说明「这一刻没有到期的东西」，
   它不证明上面第 2、3 两类时间下会发生什么** —— 这一句是这一节存在的全部理由。

## 两个量具

按 ``TARGET-CONVERGENCE-B01B02/TASK-J.md`` §3 的形状要求：**回执 + 盘上的表**。
一条只比回执的判据会放过一个「编了一个 ``run_id`` 回给你」的实现 —— 回执里那一格
与真的受理过一轮**逐字同形**。所以每一条承重断言都同时读 ``research_run`` 那几行的
**行数与行内容**。

## 这一份**判不到**什么（写在这里，不写成一句自觉）

* **Codex A=UK / Codex B=France 两个 Scene。** ``_shared.require_scene`` 只认
  ``settings.profile``（``tests/test_g0_vertical_acceptance.py:744`` 把这条拦路判据
  钉住了），而 ``profiles/`` 里只有一份 profile。这一份用的近似是「**同一 Scene 上的
  两条调度**」（:func:`test_disabling_one_schedule_leaves_the_other_due`），它少证的是
  「两个**部署**各自的凭据与预算互不可见」。见 ``J/REPORT.md``。
* **Feishu 429/5xx、sink 未知投递、客户端不支持 push。** 本部署**零个生产 sink**
  （``composition.py:950``），投递面只有测试替身可走。这一份量到的是**空表部署下
  「这个 sink 没注册」被记成一条在案的读数**，不是「通知验证过了」。
* **digest 的汇总器与 session Inbox 的落点文件。** ``delivery_mode=digest`` 今天只被
  记成 ``not_immediate``（见 :func:`test_two_consumers_read_the_same_event_cursor`）；
  「团队 digest 算出来了」与 ``inbox.jsonl`` 真的落了盘这两格**取不到读数**。
* **某 provider 缺席 / 跨 Scene 同源 / timeout 后未知计费 / 真实到期后的外部
  revalidation。** ``trial preflight`` 的 ``provider`` 行是 ``BLOCKED_EXTERNAL``
  （本机缺 ``serper`` / ``quay``）⇒ 没有能发起外调的提供方，这一半取不到真读数。
* **长期运行稳定性。** 计划逐字另行标注「未观测」。**不**读成通过。

## fixture 身份

这一份里出现的每一个 id 都带 ``g2-fixture`` 前缀，只落在本次 ``tmp_path`` 的数据根里。
没有任何真实账号、真实域或真人。

## 判据**走一个真的 MCP**

装配是 ``interfaces/mcp/__main__._compose``（与 ``python -m marketops.interfaces.mcp``
起来的那个进程同一份），控制面的每一次调用从工具进、从工具出 —— 照
``tests/test_g0_vertical_acceptance.py`` 的先例。``drive_with_settings`` 那些槽
**一格都不注入**：那会把「组合根漏传了一个工厂」这一类缺陷挡在测试之外，而这一片要
的恰恰是**组合根真的接上了那一个驱动**。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from marketops.application.scheduling import fire_schedule as _fire_schedule
from marketops.config import Settings
from marketops.domain.enums import RunStatus
from marketops.domain.run_delta import DeltaCell, PoolEffect, RunDelta
from marketops.domain.scheduling import (
    ActionReport,
    FireOutcome,
    ScheduleFire,
    due_fire_id,
)
from marketops.interfaces.composition import (
    default_notification_store,
    default_run_store,
    default_scene_store,
    default_schedule_store,
)
from marketops.interfaces.mcp.__main__ import _compose
from marketops.interfaces.trigger_driver import run_once as driver_run_once
from marketops.security.tokens import mint
from tests.mcp_harness import drive, ring_entry, ring_payload

#: 本部署的 Scene（``conftest.DEFAULT_PROFILE``）。``require_scene`` 只认它。
PROFILE = "uk-acme-natural-grass"

#: §4 那条线：这一份文件自己造的一切都带这个前缀。它不是命名习惯，是判据。
FIXTURE_PREFIX = "g2-fixture"

#: 受控时钟。**长周期语义用受控时钟测，不等一个月** —— 计划逐字允许，不是绕过。
#: ``application/scheduling.py`` 逐字说 ``at`` 是参数、「本函数不读时钟之外的时间」。
BEFORE_DUE = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
DUE_AT = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)
AFTER_DUE = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

#: 一条调度上写的到期时刻。它在 ``DUE_AT`` 与 ``AFTER_DUE`` 之间。
DECLARED_DUE = "2026-10-01T09:00:00+00:00"

SCENE_PAYLOAD: dict[str, Any] = {
    "region": ["GB-England-Devon"],
    "scenario": "sports-surface-maintenance",
    "product": f"{FIXTURE_PREFIX} legacy free-text product",
    "role_policy": "role-policy-v1",
    "source_plan": ["mother-pool-import"],
    "budget": {"search": 20},
}

#: 一轮研究「要什么」。**空着不是「不限」，是「没说」**（``ResearchContext`` 的
#: docstring 逐字），所以这里每一格都写满。
CONTEXT: dict[str, Any] = {
    "geo": "GB",
    "entity_classes": ["school"],
    "asset_classes": ["pitch"],
    "organization_classes": ["trust"],
    "scenario_candidates": [
        {
            "name": "secondary school",
            "reading": "k12",
            "provenance": "declared",
            "source": f"{FIXTURE_PREFIX}-request",
        }
    ],
    "objective": f"{FIXTURE_PREFIX}: find accounts for the fixture research",
    "evidence_requirements": ["official_site"],
}

SCHEDULE_GOOD = f"{FIXTURE_PREFIX}-sched-good"
SCHEDULE_BAD = f"{FIXTURE_PREFIX}-sched-bad"
#: 专供「收尾没做完」那一格：它的 fire 行由测试**在存储层**结掉，第 2 版从不写下。
SCHEDULE_CUT_SHORT = f"{FIXTURE_PREFIX}-sched-cut-short"
SINK_NAME = f"{FIXTURE_PREFIX}-feishu-sink"
#: 「同一个游标、两个消费者」那一格用的第二个 sink：它**一次也不会被调用**
#: （``digest`` 不即时推），名字不同只是为了让那两条 outcome 读得出是两条订阅。
SINK_DIGEST = f"{FIXTURE_PREFIX}-digest-sink"
SUBSCRIPTION_ID = f"{FIXTURE_PREFIX}-subscription"
SUBSCRIPTION_DIGEST = f"{FIXTURE_PREFIX}-subscription-digest"


# ----------------------------------------------------------------------
# 量具
# ----------------------------------------------------------------------
def _count(path: Path, table: str) -> int:
    """盘上那张表有几行。**这是「顺手做了一件事」唯一说得出口的量具。**"""
    with sqlite3.connect(path) as conn:
        try:
            return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        except sqlite3.Error as exc:  # pragma: no cover - 表名写错时红在这里
            raise AssertionError(f"{table} 这张表读不出来：{exc}") from exc


def _column(path: Path, table: str, column: str) -> list[Any]:
    """那一列的全部值，按行序。用于读**行内容**，而不只是行数。"""
    with sqlite3.connect(path) as conn:
        try:
            return [row[0] for row in conn.execute(f"SELECT {column} FROM {table}")]
        except sqlite3.Error as exc:  # pragma: no cover
            raise AssertionError(f"{table}.{column} 读不出来：{exc}") from exc


def _ok(outcome, index: int = -1) -> dict[str, Any]:
    """上一次调用的回执，**按错误通道先判**：``is_error`` 为真时把正文原样带出来。"""
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _fires(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """驱动回执里的 ``fired``，按 ``schedule_id`` 索引。"""
    return {item["schedule_id"]: item for item in receipt["fired"]}


def _delivery_states(receipt: dict[str, Any]) -> list[str]:
    """这一轮投递里**每一条收据**的状态。一个都投不出去时是空表。"""
    return [
        outcome["state"]
        for item in receipt["delivered"]
        for outcome in item["outcomes"]
    ]


# ----------------------------------------------------------------------
# 部署：真正的组合根
# ----------------------------------------------------------------------
@dataclass
class Deployment:
    """一个**真的**服务实例：``_compose`` 装配出来的那一份，外加一枚令牌。

    与 G0 那一份的差别只有一个：它多带 ``settings``，因为这一片的承重判据里有一半
    **不走 MCP** —— 它们走触发驱动那个进程的入口（G2 逐字要求「断开全部控制客户端」，
    而那句话在构造上排除了「由某个客户端顺手调一次」）。
    """

    settings: Settings
    server: Any
    token: str

    def run(self, *calls: tuple[str, dict[str, Any]]):
        outcome = drive(
            server=self.server, token=self.token, calls=calls, want_tool_list=False
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    def one(self, tool: str, **payload: Any) -> dict[str, Any]:
        return _ok(self.run((tool, payload)))

    # -- 盘数（不是回执）------------------------------------------------------
    def rows(self, table: str) -> int:
        return _count(self.settings.database_path, table)

    def column(self, table: str, column: str) -> list[Any]:
        return _column(self.settings.database_path, table, column)

    # -- 驱动那一半（**没有客户端参与**）--------------------------------------
    def wake_up(self, at: datetime, *, limit: int = 20) -> dict[str, Any]:
        """触发驱动醒来一次。**这是 G2「断开全部控制客户端」那一句的执行器。**

        走的是 ``interfaces/trigger_driver.py`` 的公开入口 —— 与
        ``python -m marketops.interfaces.trigger_driver`` 同一个函数，只是把受控
        时刻直接递进去（那条 CLI 的 ``--at`` 走的是同一个参数）。
        """
        return driver_run_once(self.settings, at=at, limit=limit)


@pytest.fixture
def deployment(settings: Settings, tmp_path: Path) -> Deployment:
    """``_compose`` 起来的一个实例。**数据根就是本次 ``tmp_path``。**"""
    assert not settings.database_path.is_file(), "干净数据根：这个库不该预先存在"
    token, digest = mint(f"{FIXTURE_PREFIX}-operator", ["controller", "researcher"])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(
            ring_payload(
                ring_entry(
                    digest,
                    f"{FIXTURE_PREFIX}-operator",
                    ["controller", "researcher"],
                )
            )
        ),
        encoding="utf-8",
    )
    composed = _compose(replace(settings, mcp_tokens_path=ring_path))
    assert composed.blocker == "", composed.blocker
    return Deployment(settings=settings, server=composed.server, token=token)


# ----------------------------------------------------------------------
# 夹具的生产者（不是被测对象）
# ----------------------------------------------------------------------
def _activated_scene(deployment: Deployment) -> dict[str, Any]:
    """一次成立的立项。``activate=True`` 是必须的：没有生效版本的 Scene 不接新活。"""
    return deployment.one("create_scene", **{**SCENE_PAYLOAD, "activate": True})


def _action_spec(**overrides: Any) -> dict[str, Any]:
    """一份指得出东西的动作规格。``execution_mode`` 只能是 ``bounded_auto``/``watch``。

    ``manual`` 在这里是被**拒**的（``schedule_action.py`` 逐字：「一条写着 ``manual``
    的调度不是『跑不起来的调度』，是一条试图自动化一件没有自动化授权的事的调度」）。
    """
    params: dict[str, Any] = {
        "context": CONTEXT,
        "cutoff_hours": 24,
        "execution_mode": "bounded_auto",
        # ``budget`` 必填而不是可选：``RunSpec`` 逐字说「没有上限不是『随便用』」——
        # 一条线都没有的 Run 在「达累计预算时 stop」那个阈值上永远不触发。
        "budget": {"search": 20},
        # ``bounded_auto`` 还要一份**写下来的授权窗口**（``AuthorizationWindow`` 的
        # 三项都必填）：没有窗口的自动模式与「没有人授权过」在能做的事上完全一样，
        # 而它会在记录里写下「这一轮之后可以自己接下一轮」。
        "window": {
            "max_objects": 5,
            "budget": {"search": 20},
            "not_after": "2026-10-08T00:00:00+00:00",
        },
    }
    params.update(overrides.pop("params", {}))
    return {
        "kind": "research_run",
        "target_ref": PROFILE,
        "params": params,
        **overrides,
    }


def _put_schedule(
    deployment: Deployment,
    schedule_id: str,
    *,
    next_due_at: str = DECLARED_DUE,
    revision: int = 1,
    **overrides: Any,
) -> dict[str, Any]:
    """写一版调度声明。``next_due_at`` 落在受控时钟的射程里。"""
    return deployment.one(
        "put_schedule_definition",
        schedule_id=schedule_id,
        schedule_revision=revision,
        scope_ref=PROFILE,
        trigger_type="one_shot",
        timezone="UTC",
        action_spec=overrides.pop("action_spec", _action_spec()),
        next_due_at=next_due_at,
        **overrides,
    )


def _one_schedule_due(deployment: Deployment) -> None:
    """起一个部署 + 一条到期的调度。**这是这一份里每一格的共同起点。**"""
    _activated_scene(deployment)
    _put_schedule(deployment, SCHEDULE_GOOD)


def _runs_in(path: Path, workspace: str) -> int:
    with sqlite3.connect(path) as conn:
        return int(
            conn.execute(
                "SELECT count(*) FROM research_run WHERE workspace = ?", (workspace,)
            ).fetchone()[0]
        )


# ======================================================================
# 一、断开全部控制客户端之后，下一批按 Schedule 自动执行
# ======================================================================
def test_a_due_schedule_fires_with_no_control_client_connected(
    deployment: Deployment,
) -> None:
    """**G2 那一句的执行器**：客户端全断了，点火仍然发生，而且盘上多一行 Run。

    这一条的**两个量具**都必须绿，缺一不可：

    * 回执说 ``status=admitted`` 且 ``replayed=false`` 且 ``outcome=run_admitted``；
    * ``research_run`` 真的多了一行。

    ``replayed=false`` 那一格是承重的：一个恒回「我重放过了」的实现会让回执看着
    一样正常，而它一次都没点火。
    """
    _one_schedule_due(deployment)
    assert deployment.rows("research_run") == 0, "起点：一行 Run 都还没有"

    receipt = deployment.wake_up(AFTER_DUE)

    fired = _fires(receipt)
    assert set(fired) == {SCHEDULE_GOOD}, receipt
    one = fired[SCHEDULE_GOOD]
    assert one["status"] == "admitted", one
    assert one["replayed"] is False, one
    assert one["outcome"] == "run_admitted", one
    assert one["advanced_to"] == 2, one

    # 盘数：**恰好一行**，而且在**本部署服务的那个工作区**里。
    assert deployment.rows("research_run") == 1, deployment.column(
        "research_run", "workspace"
    )
    assert _runs_in(deployment.settings.database_path, PROFILE) == 1


def test_the_driver_writes_nothing_when_the_schedule_is_not_yet_due(
    deployment: Deployment,
) -> None:
    """**三类时间里的第一类**：还没到点的一次醒来，什么都不做。

    这一条与上一条**必须成对读** —— 单独一条 ``fired == ()`` 什么也证明不了，
    它既可能是「没到期」，也可能是「驱动根本没接上」。两条一起读才分得开。
    """
    _one_schedule_due(deployment)

    receipt = deployment.wake_up(BEFORE_DUE)

    assert receipt["fired"] == [], receipt
    assert deployment.rows("research_run") == 0
    assert deployment.rows("schedule_fire") == 0, "没到期不该写 fire 行"


# ======================================================================
# 二、重启控制器 → 重放事件：同一条链不重复做事
# ======================================================================
def test_replaying_the_same_fire_at_the_same_moment_writes_no_second_run(
    deployment: Deployment,
) -> None:
    """**幂等那一格**：同一个受控时刻醒来第二次，不产生第二份工作。

    守卫是 ``claim_fire`` 的那条复合主键（``fire_id`` 由**盘上那条调度说的到期
    时刻**推出来，不是由墙上时钟推），所以两个驱动同时醒来也只会点一次火。

    两个量具：回执 ``replayed=true`` + **盘上仍然一行**。
    """
    _one_schedule_due(deployment)
    first = deployment.wake_up(AFTER_DUE)
    assert _fires(first)[SCHEDULE_GOOD]["replayed"] is False
    run_ids = deployment.column("research_run", "run_id")
    assert len(run_ids) == 1

    again = deployment.wake_up(AFTER_DUE)

    # 第二次醒来：这条调度已经被第一轮推到第 2 版（``fired:no_next_due``），
    # 所以它**不再到期** ⇒ ``fired`` 是空的。这是「不重复做事」在回执上的形态。
    assert again["fired"] == [], again
    # 盘上：一行 Run、一行 fire，一个都没多。
    assert deployment.rows("research_run") == 1
    assert deployment.column("research_run", "run_id") == run_ids
    assert deployment.rows("schedule_fire") == 1


def test_an_same_fire_replayed_through_the_use_case_reads_back_the_same_row(
    deployment: Deployment,
) -> None:
    """同一次 fire 走**用例**再来一遍：回**同一行**，且盘上不新增。

    与上一条不同：上一条走的是「驱动再醒一次」（那时它已不再到期，所以什么都
    不点）。这一条**直接点名同一个 ``fire_id``** 调一次点火用例 —— 那正是外部
    cron / Hosting / ServerOS 会做的事（``scheduling.py:10-13`` 逐字：它们不走
    MCP，带 ``fire_id`` 调这一个用例）。

    三条守卫里挡在这里的是**幂等键**：回执 ``replayed=true``、同一个 ``run_id``、
    ``advanced_to`` 为 ``None``（那一版已经被第一轮推过了，不该被再推一次）。
    """
    _one_schedule_due(deployment)
    first = deployment.wake_up(AFTER_DUE)
    assert _fires(first)[SCHEDULE_GOOD]["outcome"] == "run_admitted"
    original = deployment.column("research_run", "run_id")

    store = default_schedule_store(deployment.settings)()
    definition = store.get_definition(SCHEDULE_GOOD, 1)
    assert definition is not None
    fire_id = due_fire_id(definition)
    assert fire_id is not None

    receipt = _fire_schedule(
        store,
        default_notification_store(deployment.settings)(),
        _driver_action(deployment),
        schedule_id=SCHEDULE_GOOD,
        schedule_revision=1,
        fire_id=fire_id,
        at=AFTER_DUE,
    )

    assert receipt.status.value == "admitted", receipt
    assert receipt.replayed is True, receipt
    assert receipt.advanced_to is None, receipt
    assert receipt.fire is not None and receipt.fire.outcome is FireOutcome.RUN_ADMITTED
    # 盘数：**一行没多**，而且是**同一个 run_id**。
    assert deployment.column("research_run", "run_id") == original
    assert deployment.rows("schedule_fire") == 1


def test_an_interrupted_settle_is_finished_by_the_next_wake_up(
    deployment: Deployment,
) -> None:
    """**``_finish`` 那一半**：重放要把**没做完的收尾**做完，且**不重跑动作**。

    ``application/scheduling.py`` 的 ``_finish`` 逐字：判据只有一条 ——
    「下一版还没被写下（``get_definition(schedule_id, revision + 1) is None``）」。
    一次进程在 ``settle_fire`` 与 ``_advance`` 之间死掉，留下的正是这个形状。

    这一条**造出那个形状**（fire 行结掉、第 2 版不写下），再走一次点火用例：

    * 动作**没有被重跑** —— 盘上 ``research_run`` **一行都没有**（0 就是 0）。
      这是这一条最承重的一格：一个「重放时顺手再跑一遍动作」的实现会在这里
      把 0 变成 1，而它的回执与正确的那个**逐字同形**；
    * 收尾**被做完了** —— 第 2 版被写下（``advanced_to == 2``）。

    造这个形状用的是**存储层**（claim → settle，绕开 ``_advance``），因为「进程在
    两步之间死掉」在测试里没有别的造法。这一点如实写在这里，不写成一句「模拟了
    崩溃」—— 它是**构造出来的同一形状**，不是一次真的崩溃复现。
    """
    _activated_scene(deployment)
    _put_schedule(deployment, SCHEDULE_CUT_SHORT)

    store = default_schedule_store(deployment.settings)()
    notifications = default_notification_store(deployment.settings)()
    definition = store.get_definition(SCHEDULE_CUT_SHORT, 1)
    assert definition is not None
    fire_id = due_fire_id(definition)
    assert fire_id is not None

    # 「收尾没做完」：fire 行结掉，第 2 版**从不写下**。
    written = store.claim_fire(
        ScheduleFire(
            schedule_id=SCHEDULE_CUT_SHORT,
            schedule_revision=1,
            fire_id=fire_id,
            workspace=definition.workspace,
            fired_at=definition.next_due_at,
            outcome=FireOutcome.PENDING,
            detail={},
        )
    )
    assert written.recorded, "起点：这一次 fire 还没有人认领过"
    store.settle_fire(
        written.fire.settled(
            ActionReport(
                outcome=FireOutcome.RUN_ADMITTED,
                message="收尾没做完的那一次（第 2 版从未写下）",
                next_due_at=None,
            ),
            at=AFTER_DUE,
        )
    )
    assert store.get_definition(SCHEDULE_CUT_SHORT, 2) is None, "起点：第 2 版不存在"
    assert deployment.rows("research_run") == 0, "起点：一行 Run 都没有"

    receipt = _fire_schedule(
        store,
        notifications,
        _driver_action(deployment),
        schedule_id=SCHEDULE_CUT_SHORT,
        schedule_revision=1,
        fire_id=fire_id,
        at=AFTER_DUE,
    )

    assert receipt.replayed is True, receipt
    assert receipt.status.value == "admitted", receipt
    # 收尾被做完了：第 2 版被写下（``advanced_to`` 是它的号）。
    assert receipt.advanced_to == 2, receipt
    assert store.get_definition(SCHEDULE_CUT_SHORT, 2) is not None, "收尾没被做完"
    # 而动作**没有被重跑**：盘上仍然一行 Run 都没有。
    assert deployment.rows("research_run") == 0, "重放把动作又跑了一遍"


# ======================================================================
# 三、暂停一条且另一条继续 → 恢复
# ======================================================================
def test_disabling_one_schedule_leaves_the_other_due(
    deployment: Deployment,
) -> None:
    """**「暂停UK且France继续」→「恢复」**在这一台部署上的形态：同一 Scene 上的两条调度。

    两条调度都对同一个 Scene 指同一份声明，两条 ``next_due_at`` **都在射程内**
    （07:00 与 08:00，受控时钟在 12:00 醒来）。把其中一条 ``pause`` 掉之后：
    **它还是一条合法路径**（版本照旧在、只是写着不跑），而另一条照旧点火。

    三个读数缺一不可，因为它们各自排掉一种「看起来像」的实现：

    * 醒来之后 ``fired`` 里**只有** ``SCHEDULE_GOOD``，且 ``schedule_fire`` 盘上
      **恰好一行** —— 被暂停的那条**一行都没落**。少了这一格，一个「暂停只是记个
      状态、照旧点火」的实现会全绿。
    * ``research_run`` 恰好一行 —— 暂停的那条**没有顺手做出第二份工作**。
    * ``enable`` 之后同一次醒来，``SCHEDULE_BAD`` **真的被点着并且被受理**，盘上
      两行。少了这一格，「恢复」在回执上会与「什么都没发生」同形。

    ## 中间那一次存储层收尾，以及为什么它是**必须**的

    恢复那一半要走到 ``run_admitted``，而 §B17 串行守卫（``ports/runs.py`` 的
    ``open_runs`` 那一条）逐字拒绝「同一条链上同时开两轮」。GOOD 那一轮在本部署里
    **不会自己结束**（没有 provider，没有任何东西替它停表），于是不先结掉它，
    恢复那一次读到的会是一条**正确地**被拒的点火（报文点名「还有 1 轮没有结束」）
    —— 那是一条真读数，但它证的是串行守卫，不是「恢复」。

    所以这里**在存储层**把那一轮结掉（``RunStore.stop``，与 ``test_mcp_runs.py``
    同一条用法），把「上一轮结束了」这件事真的写到盘上，再恢复。**如实写**：这不是
    一次真的研究跑完，是**构造出来的同一个形状**（与 ``test_an_interrupted_settle``
    那一格同一条纪律）；本部署没有能自己把一轮跑完的执行体。

    少证的那一半**逐字写在这里**：两个**部署**（Codex A / Codex B）各自的凭据、
    预算与身份互不可见，这一条证不到 —— 本部署一部署一 Scene（``require_scene``）。
    这里用的是**同一 Scene 上的两条调度**，它证的是「暂停/恢复的粒度到得了单条调度」。
    """
    _activated_scene(deployment)
    _put_schedule(deployment, SCHEDULE_GOOD, next_due_at="2026-10-01T07:00:00+00:00")
    _put_schedule(deployment, SCHEDULE_BAD, next_due_at="2026-10-01T08:00:00+00:00")

    # 暂停 SCHEDULE_BAD（它「还是我的，只是现在不跑」）。
    deployment.one(
        "control_schedule",
        schedule_id=SCHEDULE_BAD,
        schedule_revision=1,
        control="pause",
        reason=f"{FIXTURE_PREFIX}: pause one and let the other run",
    )

    receipt = deployment.wake_up(AFTER_DUE)

    fired = _fires(receipt)
    assert set(fired) == {SCHEDULE_GOOD}, "被暂停的那条不该点火"
    assert fired[SCHEDULE_GOOD]["outcome"] == "run_admitted", fired
    assert deployment.rows("research_run") == 1
    assert deployment.rows("schedule_fire") == 1, "被暂停那条在盘上留了点火行"

    # 上一轮结束（见 docstring：这是构造出来的同一形状，不是一次真的跑完）。
    runs = default_run_store(deployment.settings)()
    run_ids = deployment.column("research_run", "run_id")
    assert len(run_ids) == 1, run_ids
    runs.stop(
        run_ids[0],
        delta=RunDelta(
            effects=(
                PoolEffect(
                    cell=DeltaCell.DISCOVERED,
                    key=f"{FIXTURE_PREFIX}-venue-1",
                    label=f"{FIXTURE_PREFIX} venue 1",
                ),
            )
        ),
        status=RunStatus.COMPLETED,
        at=AFTER_DUE,
    )

    # 恢复：``control`` 走 ``enable``（它是一次**新的版本写入**）。
    deployment.one(
        "control_schedule",
        schedule_id=SCHEDULE_BAD,
        schedule_revision=2,
        control="enable",
        reason=f"{FIXTURE_PREFIX}: resume",
    )

    resumed = deployment.wake_up(AFTER_DUE)

    fired_again = _fires(resumed)
    assert set(fired_again) == {SCHEDULE_BAD}, resumed
    assert fired_again[SCHEDULE_BAD]["outcome"] == "run_admitted", fired_again
    assert deployment.rows("research_run") == 2, deployment.column(
        "research_run", "workspace"
    )
    assert deployment.rows("schedule_fire") == 2


# ======================================================================
# 四、一条路径失败不让整条线停下（budget / 有限成本）
# ======================================================================
def test_a_refused_fire_does_not_stop_the_other_path_in_the_same_tick(
    deployment: Deployment,
) -> None:
    """**「有限成本继续其他合法路径」**：一条被拒，另一条在同一轮里照常走完。

    早到期的那条（``SCHEDULE_BAD``）写着一份**这一版不认识的参数**，所以它是
    ``UNRESOLVED`` 那一档 —— 一次**正常的拒绝**，不是一次崩溃
    （``schedule_action.py`` 逐字：多一个键**不忽略**）。

    这一条的承重点：``UNRESOLVED`` **不**是一轮醒来的终点。整轮一起停下会让
    「这条声明写错了一个键」的效果是**这个部署从此不再点火**，而回执上只显示
    一条要人看的记录。
    """
    _activated_scene(deployment)
    _put_schedule(
        deployment,
        SCHEDULE_BAD,
        next_due_at="2026-10-01T07:00:00+00:00",
        action_spec=_action_spec(params={"unknown_key": "not in this build"}),
    )
    _put_schedule(deployment, SCHEDULE_GOOD, next_due_at="2026-10-01T08:00:00+00:00")

    receipt = deployment.wake_up(AFTER_DUE)

    fired = _fires(receipt)
    assert set(fired) == {SCHEDULE_BAD, SCHEDULE_GOOD}, (
        "两条都该被走到 —— 一条被拒不等于这一轮结束",
        receipt,
    )
    assert fired[SCHEDULE_BAD]["outcome"] == "unresolved", fired[SCHEDULE_BAD]
    assert fired[SCHEDULE_GOOD]["outcome"] == "run_admitted", fired[SCHEDULE_GOOD]
    assert deployment.rows("research_run") == 1


# ======================================================================
# 五、受理出来的 Run 的 workspace 来自盘上那一版
# ======================================================================
def test_the_admitted_run_carries_the_workspace_of_the_scene_on_disk(
    deployment: Deployment,
) -> None:
    """**事实串 workspace**：Run 落在**盘上那一版生效 Scene** 的工作区里。

    两个量具：``research_run.workspace``（**行内容**，不是行数）与**从存储里读回来
    的那一版生效 Scene 的 ``workspace``** 逐字相等；行数恰好一。

    **这一条少证什么（如实写）**：本部署 ``require_scene`` 只认 ``settings.profile``，
    而 ``SceneVersion.workspace`` 也是它 ⇒ 「来自盘上那一版」与「来自部署配置」在这台
    部署上是**同一个字符串**，这一条**分不开**这两者。它能红的那一半是「这行落在
    一个**别处来的**值上」。见 ``J/REPORT.md`` 的「做不到的格子」。
    """
    _one_schedule_due(deployment)
    scenes = default_scene_store(deployment.settings)()
    record = scenes.get_record(PROFILE)
    assert record is not None, "生效 Scene 的记录不在盘上"
    version = scenes.get_version(PROFILE, record.effective_version)
    assert version is not None
    on_disk = version.workspace
    assert on_disk == PROFILE

    receipt = deployment.wake_up(AFTER_DUE)

    one = _fires(receipt)[SCHEDULE_GOOD]
    assert one["status"] == "admitted"
    assert deployment.column("research_run", "workspace") == [on_disk]
    assert _runs_in(deployment.settings.database_path, on_disk) == 1


# ======================================================================
# 六、基础设施停机与业务无新数据必须可区分
# ======================================================================
def test_an_unregistered_sink_is_recorded_and_is_not_read_as_no_new_data(
    deployment: Deployment,
) -> None:
    """**「基础设施停机与业务无新数据必须可区分」**（计划逐字）。

    本部署**零个生产 sink**（``composition.py:950``），所以订阅上写着的那个 sink
    在空表里找不到 —— 端口逐字说这要记成一条**在案的** ``unknown``（理由「这个
    部署没有注册这个 sink」），**不是静默跳过**。

    两个量具，两边都读：

    * **回执**：空表时 ``delivered`` 是**空表**（那是「没有新数据」）；订阅了却投不
      出去时 ``delivered`` 里**有**一条收据，而它的 ``note`` 点名「这个部署没有注册
      这个 sink」。
    * **盘上**：``delivery_receipt`` 那张表——没有新数据时**零行**，投不出去时**一行**
      且那一行落在 ``outcome=unknown`` 上。**只读回执会放过一个「回了你一句、盘上
      什么都没有」的实现。**

    ``note`` 为什么承重（不是装饰）：``DeliveryState`` 的成员是 ``retrying``，而
    「这个部署根本没有这个 sink」与「这个 sink 这轮暂时投不出去」**在 state 上同形**
    —— 端口逐字把 ``DEFERRED`` / ``UNKNOWN_SINK`` / ``EXHAUSTED`` 的分开放在
    **那句话**里。所以这一条判的是那句话与那行账，不是 state 的字面。

    **这一条少证什么（如实写）**：本部署零个生产 sink、零个能抛 429/5xx 的对端，
    所以「未知 sink」是这里**唯一**取得到读数的那一类基础设施停机；真实的
    Feishu 429/5xx 与客户端不支持 push 这两格见 ``J/REPORT.md``（``BLOCKED_EXTERNAL``）。
    """
    _one_schedule_due(deployment)
    deployment.one(
        "put_notification_subscription",
        subscription_id=SUBSCRIPTION_ID,
        revision=1,
        scope_kind="workspace",
        scope_ref=PROFILE,
        sink=SINK_NAME,
    )

    # 还没有任何业务变化 ⇒ 没有事件 ⇒ 投递这一半**一条收据都不该有**，
    # 而盘上也**一行都不该有**。这一半是「业务无新数据」的那一极。
    quiet = deployment.wake_up(BEFORE_DUE)
    assert quiet["delivered"] == [], quiet
    assert _delivery_states(quiet) == []
    assert deployment.rows("delivery_receipt") == 0, "没有新数据却在盘上留了收据"

    # 一次业务变化之后，同一个 sink 上出现一条**说得清自己为什么没投出去**的收据。
    loud = deployment.wake_up(AFTER_DUE)
    assert len(loud["delivered"]) == 1, loud
    states = _delivery_states(loud)
    assert states == ["retrying"], loud
    notes = [
        outcome["note"] for item in loud["delivered"] for outcome in item["outcomes"]
    ]
    assert len(notes) == 1, notes
    # **分开那一格就是这句话**：它点名缺的是哪个 sink，而不是一句「失败了」。
    assert "没有注册 sink=" in notes[0] and SINK_NAME in notes[0], notes

    # 盘上的那一行：**有账、且账上是 ``unknown``**（不是 ``accepted``）。
    receipts = [
        json.loads(raw)
        for raw in deployment.column("delivery_receipt", "payload")
    ]
    assert len(receipts) == 1, receipts
    assert receipts[0]["outcome"] == "unknown", receipts[0]
    assert receipts[0]["sink"] == SINK_NAME, receipts[0]
    assert deployment.rows("delivery_receipt") == 1


# ======================================================================
# 七、业务 Event 不可重复；sink 失败不触发研究重跑
# ======================================================================
def test_a_delivery_retry_adds_receipts_and_never_reruns_the_research(
    deployment: Deployment,
) -> None:
    """**「业务 Event 不可重复，sink 失败不能触发研究重跑」**（计划逐字）。

    连着醒三次，三个盘上的数必须**各自走向不同的方向**：

    * ``research_run`` —— **一次都不再涨**（投递失败不是重跑研究的理由）；
    * ``notification_event`` —— 同样**不再涨**（一条业务变化只产生一条事件）；
    * ``delivery_receipt`` —— **可以涨**（重试产生**新的一行**，``attempt`` 递增，
      不覆盖）。

    三条断言缺一条，这一族最贵的那种假绿就成立：一个「投不出去就重跑一轮研究」的
    实现会让第一格涨；一个「每次醒来重新记一条事件」的实现会让第二格涨。
    """
    _one_schedule_due(deployment)
    deployment.one(
        "put_notification_subscription",
        subscription_id=SUBSCRIPTION_ID,
        revision=1,
        scope_kind="workspace",
        scope_ref=PROFILE,
        sink=SINK_NAME,
    )
    deployment.wake_up(AFTER_DUE)

    runs = deployment.rows("research_run")
    events = deployment.rows("notification_event")
    receipts = deployment.rows("delivery_receipt")
    assert runs == 1, deployment.column("research_run", "workspace")
    assert events >= 1, "一次业务变化至少要记一条事件"
    assert receipts >= 1, "投不出去也要留一张收据"

    for _ in range(2):
        deployment.wake_up(AFTER_DUE)

    assert deployment.rows("research_run") == runs, "投递失败不许重跑研究"
    assert deployment.rows("notification_event") == events, "业务事件不可重复"
    assert deployment.rows("delivery_receipt") > receipts, "重试要留新的收据行"

    attempts = deployment.column("delivery_receipt", "attempt")
    assert len(set(attempts)) == len(attempts), (
        "重试产生新的一行（attempt 递增），不覆盖旧行",
        attempts,
    )


def test_two_consumers_read_the_same_event_cursor(
    deployment: Deployment,
) -> None:
    """**「团队 digest 与 session Inbox 都能从相同 event cursor 重算」**（计划逐字）。

    计划那一句在这一仓里的形态：**事件流只有一个读法**
    （``NotificationStore.events(workspace=...)``，按 ``(occurred_at, event_id)`` 升序），
    而 ``delivery_mode`` 说的是**什么时候投**，不是**投不投**（``domain/notification.py``
    的 ``DeliveryMode`` 逐字：「把它做成一个『投不投』的开关，会让 ``digest`` 订阅在
    事件那一刻被读成『这条不订阅』，而那些事件此后永远不会被汇总」）。所以：

    * 一个 ``immediate`` 订阅与一个 ``digest`` 订阅，**读的是同一个游标上的同一条
      事件**（回执上两条 outcome 挂在**同一个** ``event_id`` 下）；
    * ``digest`` 那一格**不写收据**（``state=not_immediate``、``attempt=None``）——
      「一次没有发生的投递不是一次尝试」，而事件本身**没有被丢**，它还在流里；
    * 盘上的读数把这两条分开：``delivery_receipt`` 一行（只有真发生的那一次尝试）、
      ``notification_event`` 一行、``schedule_fire`` 一行 —— 多读一次游标**一行都不涨**。

    **这一条少证什么（如实写）**：本部署零个生产 sink ⇒ 没有真的 ``inbox.jsonl``
    落点，也没有 digest 的**汇总器**（``delivery_mode=digest`` 今天只被记成
    ``not_immediate``）。这里量到的是「同一个游标、两个消费者、可重算」，不是
    「digest 报表算出来了」。见 ``J/REPORT.md``。
    """
    _one_schedule_due(deployment)
    deployment.one(
        "put_notification_subscription",
        subscription_id=SUBSCRIPTION_ID,
        revision=1,
        scope_kind="workspace",
        scope_ref=PROFILE,
        sink=SINK_NAME,
    )
    deployment.one(
        "put_notification_subscription",
        subscription_id=SUBSCRIPTION_DIGEST,
        revision=1,
        scope_kind="workspace",
        scope_ref=PROFILE,
        sink=SINK_DIGEST,
        delivery_mode="digest",
    )

    receipt = deployment.wake_up(AFTER_DUE)

    assert len(receipt["delivered"]) == 1, "一条业务变化只产生一条事件"
    item = receipt["delivered"][0]
    outcomes = {out["subscription_id"]: out for out in item["outcomes"]}
    assert set(outcomes) == {SUBSCRIPTION_ID, SUBSCRIPTION_DIGEST}, outcomes
    assert outcomes[SUBSCRIPTION_ID]["state"] == "retrying", outcomes
    assert outcomes[SUBSCRIPTION_DIGEST]["state"] == "not_immediate", outcomes
    assert outcomes[SUBSCRIPTION_DIGEST]["attempt"] is None, (
        "一次没有发生的投递不是一个 attempt",
        outcomes,
    )
    assert "delivery_mode=digest" in outcomes[SUBSCRIPTION_DIGEST]["note"], outcomes

    # 盘上：真发生过的尝试一行，事件一行；digest 那一格不留收据。
    assert deployment.rows("delivery_receipt") == 1
    assert deployment.rows("notification_event") == 1
    assert deployment.rows("schedule_fire") == 1

    # 同一个游标**可重算**：再读一次拿到逐字相同的一组，而且再醒一次也不涨。
    store = default_notification_store(deployment.settings)()
    first = [event.event_id for event in store.events(workspace=PROFILE)]
    assert first == [item["event_id"]], (first, item["event_id"])
    deployment.wake_up(AFTER_DUE)
    assert [event.event_id for event in store.events(workspace=PROFILE)] == first
    assert deployment.rows("notification_event") == 1


# ----------------------------------------------------------------------
# 驱动那一半要用真的那个动作（与 ``_compose`` 递进去的是同一个类）
# ----------------------------------------------------------------------
def _driver_action(deployment: Deployment):
    """``_compose`` 里那一个真动作的同一个构造 —— 用于绕开 MCP 直接调用例。

    这里**不**另造一套装配：它逐字抄 ``interfaces/trigger_driver.py::run_once``
    的那六本工厂。抄错一处会让这一条测的是另一个部署，所以它与那个函数一起读。
    """
    from functools import partial

    from marketops.adapters.mcp_server.schedule_action import ResearchScheduleAction
    from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
    from marketops.interfaces.composition import (
        default_account_cohort_store,
        default_account_pool_store,
        default_maintenance_store,
        default_run_store,
        default_scene_store,
    )

    settings = deployment.settings
    return ResearchScheduleAction(
        settings=settings,
        scenes=default_scene_store(settings),
        runs=default_run_store(settings),
        maintenance=default_maintenance_store(settings),
        ledger=partial(SqliteBudgetLedger, settings.database_path),
        accounts=default_account_pool_store(settings),
        cohorts=default_account_cohort_store(settings),
    )


# ----------------------------------------------------------------------
# 这一份自己造的东西**没有一件是本仓的**
# ----------------------------------------------------------------------
def test_the_fixture_identities_live_only_in_this_module() -> None:
    """§4 那条不许越的线：这一份里的 id 一个都不在产物里。

    与 G0 那一条同款。它挡的是「一次功能验证顺手把夹具写进了 ``profiles/`` 或
    ``contracts/``」，那种漂移在任何一次回执上都看不见。
    """
    from marketops.config import REPO_ROOT

    for folder in ("marketops", "profiles", "contracts", "docs"):
        for path in (REPO_ROOT / folder).rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".json", ".yaml", ".yml", ".toml"}:
                continue
            if path.name == Path(__file__).name:
                continue
            assert FIXTURE_PREFIX not in path.read_text(
                encoding="utf-8", errors="ignore"
            ), f"{path} 里有夹具身份"

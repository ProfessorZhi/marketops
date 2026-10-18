"""立项闸门：一个**没在生效**的 Scene 不受理新的研究派发（§1 的生命周期语义）。

## 这个文件补的是哪一段

``test_mcp_product_loop.py`` 证明了 ``control_scene(pause)`` 会把记录改成
``paused``，也证明了那一格里 ``accepts_new_work`` 是 ``False``。但「记录说不能派发」
与「派发真的被挡下」是两件事 —— 本轮之前，后一件事**没有任何一处代码在做**：
派发侧唯一的场景判定是 ``require_scene``，它问的是名字对不对。于是暂停一个市场
停的是维护周期，而 ``submit_research`` 照常受理，回执里一个字都不提这件事。

所以这里每一条判据问的都是**跨了两个子系统**的那一问：产品环把状态改掉之后，
研究那条路还进不进得来。两条路各自单测都绿，而合起来是错的 —— 这正是本文件
存在的理由。

## 每条拒绝都配一条「同一把钥匙后来能开」

拒绝的判据不是「返回了错误」，而是「**在库里什么都没留下**」：``store.admit``
会消耗掉幂等键，所以一个「先受理再拒绝」的实现也会返回错误，而调用方改好场景
重发时会撞上幂等冲突 —— 那个键连同它本该代表的意图一起废掉。因此下面每一条
都有一条配对的用例：同一个幂等键，在场景恢复之后**必须**受得进去。这条配对
比任何一句「拒绝发生在 admit 之前」的注释都硬。

## ``not_wired`` 那一组为什么必须有

``scenes=None`` 的部署判不了这一问，而它今天照旧受理。这是**有意的**降级，
也是一个必须被钉住的形状：降级的代价是回执里如实写 ``not_wired``，而不是让
那一格长得像一次放行。一个把「没接线」答成「判成可以」的实现在上面那几条
用例里全绿。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.adapters.mcp_server.tools import _SCENE_GATE_ADMITTED, _SCENE_GATE_NOT_WIRED
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.scenes import SqliteSceneStore
from marketops.config import Settings
from marketops.domain.scene import SceneRecord
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_asset_store,
    default_maintenance_store,
    default_relation_store,
    default_scene_store,
)
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload, write_accounts

#: 一次成立的立项。与 ``test_mcp_product_loop.py`` 的 ``BASE_CREATE`` 同源，
#: 但**刻意不复用**那个名字：那份是产品环的形状判据，这一份是派发闸门的输入，
#: 将来它们各自要改的理由不一样。
BASE_CREATE: dict[str, Any] = {
    "region": ["Devon"],
    "scenario": "natural-grass",
    "product": "acme",
    "role_policy": "role-matrix-v1",
    "source_plan": ["serper-search"],
    "budget": {"search": 20},
}

#: 工作单元的输入清单 —— 与 ``test_mcp_job_tools.py`` 同形：受理之后立刻编译，
#: 所以这份清单必须真的在盘上，否则每条判据测的都是阻塞路径。
DEFAULT_INPUTS = ("devon-2026", ("acct-0001", "acct-0002"))

#: 一条**研究侧**的请求。``scene_id`` 是本部署服务的那个场景 —— 这里测的不是
#: 名字（那一条在 ``require_scene`` 的判据里），而是**它现在让不让开工**。
_SUBMIT: dict[str, Any] = {
    "scene_id": "uk-acme-natural-grass",
    "input_kind": "known_accounts",
    "input_ref": DEFAULT_INPUTS[0],
    "idempotency_key": "gate-2026",
}


@pytest.fixture
def inputs(settings: Settings) -> Path:
    return write_accounts(settings, *DEFAULT_INPUTS)


@pytest.fixture
def session(settings: Settings, inputs: Path):
    """一个装着产品环**与**研究派发的会话驱动器。

    两个存储工厂只建一次并交给三处（产品环的 Scene 工具、``get_scene``、
    ``submit_research`` 的闸门）—— 与组合根 ``_compose`` 同款。三处各建一份的话，
    「刚暂停」与「判成可以」可能来自三个不同的库。
    """
    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring_path = settings.data_root / "gate-tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "claude-control", ["controller"]))),
        encoding="utf-8",
    )
    ring = TokenRing.from_file(ring_path)
    scenes = default_scene_store(settings)
    handlers = product_loop_handlers(
        settings=settings,
        scenes=scenes,
        maintenance=default_maintenance_store(settings),
        assets=default_asset_store(settings),
        relations=default_relation_store(settings),
        accounts=default_account_pool_store(settings),
        cohorts=default_account_cohort_store(settings),
        review=None,
        acquisition=None,
        router=None,
        documents=None,
    )

    def run(*calls: tuple[str, dict[str, Any]]):
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            product_loop=handlers,
            scenes=scenes,
            jobs=lambda: SqliteJobStore(settings.database_path),
            calls=calls,
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    return run


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = 0) -> str:
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却成功了：{call.as_json()!r}"
    return call.text


def _record(settings: Settings, scene_id: str) -> SceneRecord:
    """库里那一行 —— 用来把拒绝文与原话对齐（见 ``test_..._not_reworded``）。"""
    record = SqliteSceneStore(settings.database_path).get_record(scene_id)
    assert record is not None
    return record


# ----------------------------------------------------------------------
# 草稿 / 暂停 / 归档：三种「现在不让开工」
# ----------------------------------------------------------------------
def test_a_draft_scene_does_not_admit_research(session) -> None:
    """立项而**未生效**的场景不受理派发 —— 一次受理要有一个批准过的市场。"""
    outcome = session(
        ("create_scene", BASE_CREATE),
        ("submit_research", _SUBMIT),
    )

    created = _ok(outcome, 0)
    assert created["scene"]["state"] == "draft", "对照：立项不顺手让场景生效"

    text = _refused(outcome, 1)
    assert "draft" in text
    assert "尚未批准生效" in text


def test_the_same_idempotency_key_still_works_once_the_scene_is_activated(session) -> None:
    """**拒绝没有消耗那把钥匙。**

    这条才是「判定在门口」的判据。一个先 ``admit`` 再判场景的实现同样会返回
    拒绝，而它的幂等键已经落进库里 —— 调用方激活场景之后用同一个键重发，撞上的
    是幂等冲突（同一个键、不同的……在实现眼里是同一个请求），于是那个键连同
    它代表的意图一起废掉。契约上两次调用的参数逐字相同，所以除了「库里有没有
    那一行」之外没有别的观察面。
    """
    refused = session(
        ("create_scene", BASE_CREATE),
        ("submit_research", _SUBMIT),
    )
    _refused(refused, 1)

    after = session(
        ("control_scene", {"action": "activate", "version": 1}),
        ("submit_research", _SUBMIT),
    )
    body = _ok(after, 1)

    assert body["state"] == "queued"
    assert body["work_items"]["count"] == len(DEFAULT_INPUTS[1])


def test_a_paused_scene_does_not_admit_research_and_says_why(session) -> None:
    """暂停中的场景不受理派发，理由逐字来自记录（含「谁能让它重新开工」）。"""
    outcome = session(
        ("create_scene", dict(BASE_CREATE, activate=True)),
        ("control_scene", {"action": "pause", "reason": "operator_pause"}),
        ("submit_research", _SUBMIT),
    )

    _ok(outcome, 1)
    text = _refused(outcome, 2)
    assert "paused" in text and "operator_pause" in text
    # 暂停**只停新派发**：这一句在拒绝里必须说出来，否则读的人会以为在途的工作
    # 单元与回信也一起停了 —— 那是两件相反的事（后者会让该抑制的地址攒下来）。
    assert "在途" in text and "回信" in text


def test_an_archived_scene_does_not_admit_research(session) -> None:
    """归档是终态，且它连研究也一起关掉 —— 「不再经营」不是「暂时不看」。"""
    outcome = session(
        ("create_scene", dict(BASE_CREATE, activate=True)),
        ("control_scene", {"action": "archive"}),
        ("submit_research", _SUBMIT),
    )

    _ok(outcome, 1)
    text = _refused(outcome, 2)
    assert "archived" in text


def test_resuming_lets_research_in_again(session) -> None:
    """恢复之后同一把钥匙受得进去 —— 暂停没有把这条作业的意图用掉。"""
    paused = session(
        ("create_scene", dict(BASE_CREATE, activate=True)),
        ("control_scene", {"action": "pause", "reason": "policy_hold"}),
        ("submit_research", _SUBMIT),
    )
    _refused(paused, 2)

    after = session(
        ("control_scene", {"action": "resume"}),
        ("submit_research", _SUBMIT),
    )
    body = _ok(after, 1)
    assert body["state"] == "queued"


# ----------------------------------------------------------------------
# 放行时的读数
# ----------------------------------------------------------------------
def test_the_receipt_says_which_version_was_in_force_when_it_admitted(session) -> None:
    """放行也要留读数：哪一版在生效、场景当时是什么状态。

    只报「受理成功」的回执，在事后回答不了「这份作业是在哪一版规则下受理的」——
    而问这个问题的时候通常已经改过一版了。
    """
    outcome = session(
        ("create_scene", dict(BASE_CREATE, activate=True)),
        ("add_scene_version", {"budget": {"search": 5}, "note": "缩预算"}),
        ("control_scene", {"action": "activate", "version": 2}),
        ("submit_research", _SUBMIT),
    )

    body = _ok(outcome, 3)
    gate = body["dispatch_gate"]

    assert gate["decision"] == _SCENE_GATE_ADMITTED
    assert gate["scene_state"] == "active"
    assert gate["effective_version"] == 2, "生效的是第 2 版，不是最新写过的那一版"
    assert gate["reason"] == ""
    assert "当时" in gate["note"], (
        "这一格说的是「受理那一刻哪一版在生效」，不是「这份作业以后按哪一版跑」——"
        "把两者混成一句话，会让一次改版看起来像在途作业换了规则"
    )


def test_the_refusal_is_the_records_own_sentence_not_a_second_copy(session, settings) -> None:
    """拒绝文里的那一句**就是**记录上那一句，不是在这里重写的第二份。

    两处各写一份的后果不是文案不一致：``application/maintenance.py`` 用同一句话
    说明维护周期为什么跳过这个场景。两份漂移的那天，同一秒里维护侧说「暂停」而
    研究侧说「可以开工」，读报告的人无从判断哪一句对。这条判据把两句话钉在一起。
    """
    outcome = session(
        ("create_scene", dict(BASE_CREATE, activate=True)),
        ("control_scene", {"action": "pause", "reason": "operator_pause"}),
        ("submit_research", _SUBMIT),
    )

    text = _refused(outcome, 2)
    assert _record(settings, settings.profile).dispatch_refusal in text


def test_a_scene_that_was_never_created_refuses_and_names_the_next_step(session) -> None:
    """库里读不到记录时拒绝，且那句拒绝里说得出下一步。

    立项记录是「有人批准过这个市场」在代码里唯一的形态。没有它就受理，等于把
    「先立项」当成可选项 —— 而 §1 要的恰恰是这一步不是可选项。
    """
    outcome = session(("submit_research", _SUBMIT))

    text = _refused(outcome)
    assert "还没有立项过" in text
    assert "create_scene" in text, "一次不动手的拒绝等于一堵墙"


# ----------------------------------------------------------------------
# 降级：判不了的那一格必须自己说出来
# ----------------------------------------------------------------------
def test_an_instance_without_a_scene_store_admits_and_says_it_did_not_check(
    settings: Settings, inputs: Path
) -> None:
    """``scenes=None`` 时受理照旧，但回执里那一格是 ``not_wired``，不是「放行」。

    这是本轮**唯一**一处「判不了也放过」的地方，而它是有意的：答不出来有两个
    写法，这里选「当作可以」并**把它写出来**。另一个写法（当作不可以）会让每一个
    还没接 ``SceneStore`` 的部署连研究都做不了 —— 那是接线事故，不是场景判定。

    反面同样被钉住：这一格**不得**是 ``admitted``。一个把降级答成放行的实现，
    会让上面每一条用例都还是绿的，而报告上「这个市场被批准过」是编出来的。
    """
    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring_path = settings.data_root / "gate-tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "claude-control", ["controller"]))),
        encoding="utf-8",
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        scenes=None,
        jobs=lambda: SqliteJobStore(settings.database_path),
        calls=(("submit_research", _SUBMIT),),
        want_tool_list=False,
    )
    assert not outcome.handshake_error, outcome.handshake_error

    body = _ok(outcome)
    gate = body["dispatch_gate"]

    assert gate["decision"] == _SCENE_GATE_NOT_WIRED
    assert gate["decision"] != _SCENE_GATE_ADMITTED
    assert gate["scene_state"] is None and gate["effective_version"] is None
    assert "没有被问过" in gate["note"]

"""产品环那些工具走真实 MCP 会话时的行为（``docs/MCP_PRODUCT_LOOP.md`` §2）。

## 这一层测的是「接线之后它还对不对」

``application/scenes.py`` 与 ``domain/scene.py`` 各自的判据已经存在
（``tests/test_application_scenes.py`` / ``test_domain_scene.py``）。本文件不重测
那些 —— 它测的是**只有把三段接起来才会出现**的那几件事：主体从哪来、哪些参数
根本不存在、没接线时回执长什么样、研究身份能不能看见它们。

## 四组判据，各自可能失败

* **形状**：回执里的字段与状态转移是真实的那一套（不是按想象编的期望值）。
* **不存在的参数**：``by`` / ``calibrated`` / 时间戳一律不是入参。这一组比的是
  **集合相等** —— 多一个参数与少一个都会红，所以将来没人能悄悄加回一个。
* **池**：``import_motherpool`` 的每一行去向与整份拒绝、``promote_receipt`` 折的是
  resolved 而不是「能读到的行」（且折不是一次复核）、``list_accounts`` /
  ``list_assets`` 的行序、未知与封顶的两种写法。这一组测的是**产物与读数**，
  与场景那一组不共用任何判据。
* **没接线**：``not_wired`` 与 ``absent`` 是两句不同的话。一个把「没接存储」答成
  「这个市场没立项过」的部署，会让运维去查一份不存在的数据。

## 为什么这一组里每条「能过」的用例都配了一条「过不去」的

只有正常路径的用例无法区分「拒绝对了」与「拒绝根本没跑」。所以激活不写版本、
暂停不给理由、给暂停配一个版本、两次立项 —— 每一条都钉住了它自己的拒绝。
池那一组同理：封顶配一条不封顶、库不存在配一条库存在、读得进去配一条整份拒绝。
"""

from __future__ import annotations

import hashlib
import inspect
import json
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.budget import budget_handlers
from marketops.adapters.mcp_server.continuation import continuation_handlers
from marketops.adapters.mcp_server.fetch_attempt import fetch_attempt_handlers
from marketops.adapters.mcp_server.launch import launch_handlers
from marketops.adapters.mcp_server.outreach import OUTREACH_TOOLS
from marketops.adapters.mcp_server.product_catalog import product_catalog_handlers
from marketops.adapters.mcp_server.product_loop import (
    _outcome_row,
    _source_namespace,
    product_loop_handlers,
)
from marketops.adapters.mcp_server.product_readiness import product_readiness_handlers
from marketops.adapters.mcp_server.review import review_handlers
from marketops.adapters.mcp_server.runs import run_handlers
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.application.document_router import DocumentRouter
from marketops.adapters.page_mirror import page_path
from marketops.application import refresh as refresh_use_cases
from marketops.application.refresh import content_digest
from marketops.application.motherpool import receipt_path
from marketops.config import Settings
from marketops.domain.enums import ContactField, SourceKind
from marketops.domain.errors import AssetConflictError, MarketOpsError, StoreError
from marketops.domain.evidence import Evidence
from marketops.domain.receipt import RECEIPT_VERSION
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_acquisition,
    default_action_item_store,
    default_asset_store,
    default_attempt_store,
    default_document_budget,
    default_maintenance_store,
    default_outreach_store,
    default_product_catalog_store,
    default_relation_store,
    default_review_store,
    default_run_store,
    default_scene_store,
    default_wakeup_registry,
)
from marketops.security.catalog import TOOL_CATALOG, Role
from marketops.security.tokens import TokenRing, mint
from tests.fakes import FakeHttp, PageParser
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

#: 一次成立的立项。**不是业务事实** —— 区域、产品、来源计划都是测试输入，
#: 真实的那一套住在 ``profiles/`` 里，由 profile 的排除清单与预算规则约束。
BASE_CREATE: dict[str, Any] = {
    "region": ["Devon"],
    "scenario": "natural-grass",
    "product": "acme",
    "role_policy": "role-matrix-v1",
    "source_plan": ["serper-search"],
    "budget": {"search": 20},
}

#: 每一版的 ``refresh`` 都必须标着这一条。天数可以由调用方给，**标定不能**。
_UNCALIBRATED = "calibrated"

#: 任何一条产品环工具都**不准**有的入参。
#:
#: 前三样是三条不同的判据，写在一起是因为它们的失败方式相同 —— 都会让一次调用
#: 把一句**没有事实基础的话**写进持久状态：
#:
#: * ``by`` —— 批准人。取自传输层的令牌，不取自载荷（§7 逐字反对「拿模型自己填的
#:   字符串当认证」）。
#: * ``calibrated`` —— 刷新周期是否已标定。标定是一个经验主张，一次调用不是它的
#:   来源；能由参数写真的话，``uncalibrated_aspects`` 那份清单就永远可以是空的。
#: * ``at`` / ``created_at`` —— 时间。能倒填的审计记录不是审计记录。
#: * ``profile_id`` / ``profile_version`` / ``supersedes`` —— 从装载层与版本号推出来，
#:   给调用方填就是让「按 A 规则承诺、按 B 规则执行」这件事在载荷里合法化。
#: * ``exclusions``（复数、不带 additional_）—— 一个能**整体替换**排除清单的参数。
#:   排除清单只能并、不能减，所以那件事必须表达不出来。
_FORBIDDEN_PARAMETERS = frozenset(
    {
        "at",
        "by",
        "calibrated",
        "created_at",
        "exclusions",
        "frozen_sha256",
        "profile_id",
        "profile_version",
        "revision",
        "state",
        "supersedes",
        "updated_at",
        "workspace",
    }
)


def _ring(tmp_path: Path, principal: str, roles: list[Role]):
    """种一个令牌并落成环文件，返回 ``(环文件, 令牌)``。

    本文件里这段被逐字写过四遍（控制身份两处、研究身份一处、夹具一处）。写错的
    方式是静默的：``ring_entry`` 里的摘要与 ``mint`` 返回的那个必须配成一对，
    配错了会话在握手期就失败 —— 而「一处改对、另外三处照旧」正是重写四遍的代价。
    """
    token, digest = mint(principal, roles)
    path = tmp_path / "tokens.json"
    path.write_text(
        json.dumps(ring_payload(ring_entry(digest, principal, roles))),
        encoding="utf-8",
    )
    return path, token


def _controller_session(tmp_path: Path, settings: Settings, **wiring):
    """一个 controller 会话的驱动器；``wiring`` 就是产品环那三样页面接线。

    **存储工厂只建一次**并交给两个地方（产品环那三条与 ``get_scene``），与组合根
    ``_compose`` 里同款 —— 两处各建一份的话，「刚立项」与「读不到那个场景」可能
    来自两个不同的库。

    接线做成参数而不是在这里写死，是因为**同一个局面要在两个部署形状下各测一遍**
    （接了抓取的 / 没接抓取的）：这两条判据的差别只在那三样，而各写一份会话构造
    会让「两次调用之间还有别的不同」永远看不出来。
    """
    ring_path, token = _ring(tmp_path, "claude-control", [Role.CONTROLLER])
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
        **wiring,
    )

    def run(*calls: tuple[str, dict[str, Any]], want_tool_list: bool = False):
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            product_loop=handlers,
            scenes=scenes,
            calls=calls,
            want_tool_list=want_tool_list,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    return run


@pytest.fixture
def session_factory(settings: Settings, tmp_path: Path):
    """一个 controller 会话的驱动器，接上了产品环与场景存储。

    返回的是 ``run(*calls) -> SessionOutcome``。

    **这一份部署没接页面那三样接线**（``acquisition`` / ``router`` / ``documents``
    都是 ``None``）。那不是「不能 tick」：``source_coverage`` 那一面照常复核，而
    页面那几格的处置是「这一趟没做成，因为缺 X」—— 逐页点名，见本文件最后那一组。
    """
    return _controller_session(
        tmp_path, settings, acquisition=None, router=None, documents=None
    )


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    """第 ``index`` 次调用必须成功，并返回它的结构化结果。

    失败时把工具自己那句话带进断言消息 —— 一个只报 ``is_error is True`` 的用例
    在排查时要先手工重跑一遍才知道它到底说了什么。
    """
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = 0) -> str:
    """第 ``index`` 次调用必须**作为结果被拒**（不是把会话炸掉），返回那句话。"""
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却成功了：{call.as_json()!r}"
    return call.text


# ----------------------------------------------------------------------
# 走一遍完整的生命周期
# ----------------------------------------------------------------------
def test_the_whole_lifecycle_runs_from_one_session(session_factory) -> None:
    """立项 → 激活 → 改版 → 换一版生效 → 暂停 → 恢复 → 归档，一条会话走完。

    断言的是**记录里那两个版本号**，而不是「调用没报错」：``latest_version`` 与
    ``effective_version`` 分开是这套东西存在的理由，而只看 ``state`` 的话，
    「改完没批」与「改完生效了」在回执上长得一样。
    """
    outcome = session_factory(
        ("create_scene", BASE_CREATE),
        ("control_scene", {"action": "activate", "version": 1}),
        ("add_scene_version", {"budget": {"search": 5}, "note": "缩预算"}),
        ("control_scene", {"action": "activate", "version": 2}),
        ("control_scene", {"action": "pause", "reason": "operator_pause"}),
        ("control_scene", {"action": "resume"}),
        ("control_scene", {"action": "archive"}),
    )

    created = _ok(outcome, 0)
    assert created["scene"]["state"] == "draft"
    assert created["scene"]["latest_version"] == 1
    assert created["scene"]["effective_version"] is None, "立项不该顺手让它生效"
    assert created["scene"]["accepts_new_work"] is False

    active = _ok(outcome, 1)
    assert active["scene"]["state"] == "active"
    assert active["scene"]["effective_version"] == 1
    assert active["scene"]["accepts_new_work"] is True
    assert active["effective"]["version"] == 1, "回执里要带上生效的那一版本身"

    revised = _ok(outcome, 2)
    assert revised["scene"]["latest_version"] == 2, "改版让最新版前进"
    assert revised["scene"]["effective_version"] == 1, (
        "改版**不生效**：在途的运行不能在一半的时候换掉规则"
    )
    assert revised["version"]["supersedes"] == 1, "新的一版必须指称它取代的那一版"
    assert revised["version"]["budget"] == {"search": 5}
    # 没给的字段逐字段继承 —— 回执给的是改动后**完整**的一版。
    assert revised["version"]["region"] == BASE_CREATE["region"]
    assert revised["version"]["role_policy"] == BASE_CREATE["role_policy"]
    assert revised["impact"]["from_version"] == 1
    assert revised["impact"]["to_version"] == 2

    switched = _ok(outcome, 3)
    assert switched["scene"]["effective_version"] == 2
    assert switched["effective"]["frozen_sha256"] == revised["version"]["frozen_sha256"]

    paused = _ok(outcome, 4)
    assert paused["scene"]["state"] == "paused"
    assert paused["scene"]["pause_reason"] == "operator_pause"
    assert paused["scene"]["accepts_new_work"] is False, "暂停必须停掉新派发"
    # §4：暂停停的是**新的**派发与外联，回信那一侧不看这个状态。
    assert "回信" in paused["note"]

    resumed = _ok(outcome, 5)
    assert resumed["scene"]["state"] == "active"
    assert resumed["scene"]["accepts_new_work"] is True
    assert resumed["scene"]["effective_version"] == 2, (
        "恢复不得改 effective_version —— §4 要求恢复不重发已发邮件、不重吃整个母池"
    )

    archived = _ok(outcome, 6)
    assert archived["scene"]["state"] == "archived"
    assert archived["scene"]["accepts_new_work"] is False


def test_activating_records_the_principal_from_the_token_not_from_the_payload(
    session_factory,
) -> None:
    """批准人来自那一枚 Bearer 令牌，且回执把它写出来了。

    这条是 ``by`` 不在入参里的**运行时**另一半：schema 上少一个参数只证明了
    「填不进来」，这里证明的是「填进来的那个东西不是它」—— 记录里的 ``by``
    逐字等于令牌上的主体名。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
    )
    created = _ok(outcome)
    approvals = created["scene"]["approvals"]
    assert [item["by"] for item in approvals] == ["claude-control"]
    assert [item["version"] for item in approvals] == [1]


def test_a_second_create_on_the_same_scene_does_not_overwrite(session_factory) -> None:
    """重复立项被拒，且**已有记录一个字节都没动**。

    只断言「第二次失败了」是不够的：一个「先覆盖再报错」的实现同样能通过。
    所以第二次之后要读回第一次写下的那一版，逐字比对它的摘要。
    """
    outcome = session_factory(
        ("create_scene", BASE_CREATE),
        ("create_scene", dict(BASE_CREATE)),
        ("get_scene", {}),
    )
    first = _ok(outcome, 0)
    text = _refused(outcome, 1)
    assert "已经立项过了" in text
    assert "第 1 版" in text, "拒绝里要读得出它现在是什么状态，否则调用方只能重读一次"

    after = _ok(outcome, 2)
    assert after["record"]["latest_version"] == 1
    assert after["record"]["state"] == "draft", "第二次立项不该留下任何状态变化"
    assert after["record"]["revision"] == first["scene"]["revision"]


def test_a_refused_create_says_why_instead_of_returning_a_bare_error(session_factory) -> None:
    """改一个字段再立项一次 —— 拒绝**带着理由**回来，而且什么都没落盘。

    这条与上一条走的是**两条不同的路**，值得分开钉：内容与库里第 1 版逐字相同时，
    冲突由记录层报出（``SceneConflictError``，上面那条）；内容不同时，``put_version``
    先发现「这个版本号上躺着一份不同摘要的版本」并拒绝（``StoreError``）。

    第二条路曾经**整个丢掉理由**：``StoreError`` 没有被接住，逃到 SDK 之后调用方
    读到的是一句光秃秃的 ``Error executing tool create_scene`` —— 一个字的解释都没有。
    而存储层原本给的话很准（「同号不同内容只可能意味着版本号被手工指定过。**不覆盖**
    —— 覆盖会让按版本号引用它的工作单元静默换了规则」）。所以这里断言的是**那句话
    在场**，而不是「它失败了」。
    """
    outcome = session_factory(
        ("create_scene", BASE_CREATE),
        ("create_scene", {**BASE_CREATE, "budget": {"search": 1}}),
        ("get_scene", {}),
    )
    first = _ok(outcome, 0)
    text = _refused(outcome, 1)
    assert "不覆盖" in text, "存储层说的那句话必须真的到得了调用方"
    assert "摘要" in text

    after = _ok(outcome, 2)
    assert after["record"]["revision"] == first["scene"]["revision"], (
        "被拒的那次立项改了记录 —— 它必须在 put_version 那一步就停下"
    )
    assert after["record"]["latest_version"] == 1
    assert after["record"]["state"] == "draft"


@pytest.mark.parametrize(
    ("method", "calls"),
    [
        (
            "put_version",
            (("create_scene", BASE_CREATE), ("add_scene_version", {"budget": {"search": 1}})),
        ),
        (
            "put_record",
            (
                ("create_scene", BASE_CREATE),
                ("control_scene", {"action": "activate", "version": 1}),
            ),
        ),
    ],
    ids=["add_scene_version", "control_scene"],
)
def test_a_store_refusal_becomes_a_readable_result_at_every_write_site(
    session_factory, monkeypatch, method: str, calls: tuple[tuple[str, dict[str, Any]], ...]
) -> None:
    """存储层说「不」的时候，第二次写的那条工具要把它**说出来**，而不是让它逃出去。

    ## 为什么放弃的是第二次写

    桩打在**类方法**上，而第一次写（``create_scene``）也要走同一个方法 —— 让它第一次
    就拒绝，测到的是一条「场景根本不存在」的路径，被测的那条 except 压根不会被执行
    （第一版就是这么写的，红的理由是 ``没有 scene_id=... 这个场景``，与映射无关）。
    所以计数落在**第二条工具**上：夹具先正常立项，再让那次改版/暂停撞上存储层的拒绝。

    ``create_scene`` 自己的那个写点由上面那条**端到端**的摘要冲突覆盖（真的能到达，
    不需要桩）；这里覆盖的是另外两条。

    ## 为什么用桩而不是造一个真的冲突

    ``add_scene_version`` 与 ``control_scene`` 的写点在**顺序调用**下撞不到冲突
    （它们每次都在调用内部重读）。真实的碰撞需要一个交错窗口，而那件事由
    ``tests/test_application_scenes.py`` 在用例层用两条独立读写的用例覆盖 ——
    这里不重复模拟那个窗口，只钉住「存储层开口说了话，服务面就得把它转出去」。
    """
    from marketops.adapters.sqlite.scenes import SqliteSceneStore

    original = getattr(SqliteSceneStore, method)
    seen = {"n": 0}

    def refuse(self, *args, **kwargs):
        seen["n"] += 1
        if seen["n"] >= 2:
            raise StoreError("存储层的一句话：这一版不覆盖。")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SqliteSceneStore, method, refuse)
    try:
        outcome = session_factory(*calls)
    finally:
        monkeypatch.setattr(SqliteSceneStore, method, original)

    assert seen["n"] == 2, f"桩只该被走到两次（立项一次、被测的那次一次），实际 {seen['n']}"
    text = _refused(outcome, len(calls) - 1)
    assert "存储层的一句话" in text, (
        "存储层说的那句话没有到达调用方 —— 它被折成了一句读不出原因的失败"
    )
    # SDK 会给每一条工具错误加上 ``Error executing tool <名字>: `` 前缀，那是协议
    # 外壳、不是内容。**没有**这句理由时，这句话整个长成光秃秃的一行：
    assert text.strip() != f"Error executing tool {calls[-1][0]}", (
        "回执里只有工具名、没有理由 —— 这正是修复前那条路径的形状"
    )


def test_get_scene_reads_the_record_back_next_to_the_profile_summary(session_factory) -> None:
    """``get_scene`` 同时答两件会不一致的事：规则目录里写着什么、立项成了哪一版。

    分两个工具读，调用方就得自己对齐两次读的时刻 —— 那件事它做不到。所以这一条
    断言的是**两块同时在场**，而不是某一格的值。
    """
    outcome = session_factory(
        ("create_scene", BASE_CREATE),
        ("get_scene", {}),
    )
    created = _ok(outcome, 0)
    read = _ok(outcome, 1)

    assert read["record_state"] == "present"
    assert read["record"]["scene_id"] == created["scene"]["scene_id"]
    assert read["record"]["latest_version"] == 1
    # 上面那一块是装载层给的同一份摘要（不是这里另算的一遍）。
    assert read["summary"]["profile_id"] == read["profile_id"]
    assert read["scene_version"], "profile 目录的字节摘要必须回"


# ----------------------------------------------------------------------
# 参数面上不存在的东西
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "tool_name",
    [
        "create_scene",
        "add_scene_version",
        "control_scene",
        "import_motherpool",
        "promote_receipt",
        "list_assets",
        "remember_responsibility",
        "list_responsibilities",
        "list_accounts",
        "remember_accounts",
        "list_account_pool",
        "freeze_cohort",
        "list_cohorts",
        "get_cohort",
        "get_maintenance_plan",
        "run_maintenance",
        "get_ops_report",
    ],
)
def test_no_product_loop_parameter_can_certify_or_backdate_anything(
    settings: Settings, tool_name: str
) -> None:
    """每一条工具的入参集合**恰好**是它自己那一组，多一个都没有。

    比的是集合相等而不是「不含某几个」：后者只在有人加回一个**已知**的坏名字时
    才红，而这一条在任何一个新参数出现时都要先被看到一眼 —— 那个参数是不是又一条
    「让调用方替事实说话」的入口，正是要在这里回答的问题。
    """
    handler = product_loop_handlers(
        settings=settings,
        scenes=default_scene_store(settings),
        maintenance=default_maintenance_store(settings),
        assets=default_asset_store(settings),
        relations=default_relation_store(settings),
        accounts=default_account_pool_store(settings),
        cohorts=default_account_cohort_store(settings),
        review=None,
        acquisition=None,
        router=None,
        documents=None,
    )[tool_name]
    parameters = set(inspect.signature(handler).parameters)

    forbidden = parameters & _FORBIDDEN_PARAMETERS
    assert not forbidden, (
        f"{tool_name} 收下了 {sorted(forbidden)} —— 这些名字承载的事实"
        "（谁批的、标定过没有、什么时候、按哪份 profile、哪一版取代了哪一版）"
        "都从传输层或版本号推出来，不由载荷提供"
    )

    expected = {
        "create_scene": {
            "region",
            "scenario",
            "product",
            "role_policy",
            "source_plan",
            "budget",
            "scene_id",
            "admissions",
            "additional_exclusions",
            "refresh_days",
            # ``outreach`` **在**这里，与 ``refresh_days`` 同型：节奏天数是一个
            # 业务判断，可以由操作者给。**不在**这里的是「这一项已标定」——
            # 见下面那条判据：给了数字的那几项照样 ``calibrated: false``。
            "outreach",
            "note",
            "activate",
        },
        "add_scene_version": {
            "scene_id",
            "region",
            "scenario",
            "product",
            "admissions",
            "additional_exclusions",
            "role_policy",
            "source_plan",
            "budget",
            "refresh_days",
            "outreach",
            "note",
        },
        "control_scene": {"action", "scene_id", "version", "reason"},
        # ``source_method_id`` 与 ``declared_exclusion_ids`` **在**这一组里被
        # 排除，理由与 ``by`` / ``calibrated`` 同型，写在
        # ``docs/MCP_PRODUCT_LOOP.md`` §3：前者是工件的事实（登记册导出就是
        # ``import.register.v1``），后者是 profile 的声明。两个都是「让调用方
        # 替事实说话」的入口 —— 一个能让调用方把登记册的行标成检索来的，
        # 另一个能让一次调用发明一条会静默删行的规则。
        "import_motherpool": {"artifact_ref", "batch_id", "scope", "cutoff", "cursor"},
        # 折那一条只有 ``receipt_id`` 与 ``run_id``，**这是刻意的**（理由写在
        # ``docs/MCP_PRODUCT_LOOP.md`` §3）：没有 ``limit``（截断一次读是少看几行，
        # 截断一次写是**少写几行**，而池子上没有哪一栏会显示「本来还有 20 行」）、
        # 没有 ``at``（时刻由服务端取，写进 ``first_seen_at``）、没有 ``workspace``
        # （血统由本部署的 profile 定 —— 能填的话，一次调用就能把行写进另一个部署的
        # 池子，而那两个池子在同一个库文件里）。
        #
        # ``run_id`` **在**这里，与 ``judgements`` 同型：**这一批折入属于哪一轮 Run**
        # 只有调用方能给 —— Run 的 id 形状由本部署产出（``domain/run.py::run_id``），
        # 但「这一次导入是替哪一轮做的」不在盘上任何一处。它不是一条「让调用方替事实
        # 说话」的入口：给了它之后，池子那一行的写与那一笔账在**同一个事务**里落下，
        # 而这个参数影响不了任何一行的事实，只影响这一笔归谁。
        "promote_receipt": {"receipt_id", "run_id"},
        # ``limit`` 在这里，与 ``list_accounts`` 同型：它是调用方的输入，不是事实。
        "list_assets": {"limit"},
        # ``judgements`` **在**这里：一块场地由谁负责是一个业务判断，只有调用方能给
        # （与 ``budget`` / ``refresh_days`` 同型）。**不在**这里的是 ``workspace``
        # （血统由本部署的 profile 定）、``at``（时刻由服务端取，写进 first_seen_at）
        # 和 ``last_verified_at``（记下一条判定不是重新证实了它）—— 三条都写在
        # ``_remember_responsibility`` 的 docstring 与 ``docs/MCP_PRODUCT_LOOP.md`` §3 里。
        "remember_responsibility": {"venue_asset_id", "judgements"},
        # ``venue_asset_id`` / ``account_id`` 是**两个问题**（谁负责这块 / 这个账号在管
        # 哪些），两个一起给是拒绝（见 ``_list_responsibilities``）。``limit`` 与
        # ``list_assets`` 同型：调用方的输入，不是事实。
        "list_responsibilities": {"venue_asset_id", "account_id", "limit"},
        # ``limit`` **在**这里：它是调用方的输入，不是事实。缺省封顶（见
        # ``_MAX_ACCOUNTS``），显式 ``None`` 不封顶 —— 两条都由 ``truncation``
        # 报出来，所以「封了多少」不是一次静默的截断。
        "list_accounts": {"name", "limit"},
        # 账号**池**那两条：``accounts_rows`` **在**这里（一个账号是谁、叫什么、域名
        # 是什么、判成什么，都是只有调用方能给的业务判断，与 ``judgements`` / ``budget``
        # 同型）。**不在**这里的是 ``workspace``（血统由本部署的 profile 定）、``at``
        # （时刻由服务端取，写进 first_seen_at）与 ``last_verified_at``（记下一个账号
        # 不是重新核实了它）—— 三条都写在 ``_remember_accounts`` 的 docstring 与
        # ``docs/MCP_PRODUCT_LOOP.md`` §3 里。它同样**没有** ``venue_asset_id``：
        # 一个账号不挂在某一块场地上（``remember_responsibility`` 有它，因为一条责任
        # 关系说的正是「谁对**哪块**场地负责」）。
        "remember_accounts": {"accounts_rows"},
        # ``account_id`` 在这里是 §14 的 ``get_account``（同一个行构造器，只是多一层
        # 过滤），``limit`` 与 ``list_assets`` 同型：调用方的输入，不是事实。
        "list_account_pool": {"account_id", "limit"},
        # 冻结成员名单那三条（§B12）：``statuses`` 与 ``limit`` **在**这里，它们是
        # 调用方的**选择规则**（这一批只要哪几种状态的账号、最多几个），与
        # ``budget`` / ``judgements`` 同型；``max_rows`` 同样在，它是回执的长度，
        # 与 ``list_assets`` 的 ``limit`` 同型。**不在**这里的：``workspace``
        # （名单落在哪个 Scene 由本部署的 profile 与场景定）、``frozen_sha256``
        # （身份是内容寻址算出来的，能填就是让调用方替内容说话）、``created_at``
        # （时刻由服务端取）。``scene_id`` 在，与其余各条同型：它是「这一个还是
        # 当前那一个」的选择，不是一个事实。
        "freeze_cohort": {"scene_id", "statuses", "limit", "max_rows"},
        "list_cohorts": {"scene_id", "limit"},
        # ``cohort_id`` 与 ``account_id`` 同型（§14 的读一条），``limit`` 是调用方
        # 的输入；空串与 ``scene_id`` 一起表达「这个 Scene 最近冻的那一份」。
        "get_cohort": {"cohort_id", "scene_id", "limit"},
        # 维护那三条：``aspects`` **不**在这里 —— 本部署复核哪几个面是一个
        # **声明**（``application/refresh.py::REFRESHABLE_ASPECTS``），不是调用方
        # 的选择。一个能传这个参数的接口让调用方自己划定范围，而「这一面本部署
        # 做不了」与「这一面这一次没被选中」在报告上就会变成同一句话。
        # ``time`` / ``now`` 同样不在：时刻由服务端取（``utc_now``）。
        #
        # ``cohort`` 在这里，与 ``name`` 同型：§B12 之后这一批主体有**两条路**
        # （一份冻结成员名单 / 一份手写清单），给哪一个是调用方的选择。它不替任何
        # 事实说话 —— 「恰好给一个」由 ``_subject_source`` 判，两个都给与两个都不给
        # 都是一条具名拒绝（见 ``docs/MCP_PRODUCT_LOOP.md`` §2）。
        "get_maintenance_plan": {"scene_id", "name", "cohort", "limit"},
        "run_maintenance": {"scene_id", "name", "cohort", "limit"},
        "get_ops_report": {"scene_id", "name", "cohort"},
    }[tool_name]
    assert parameters == expected


def test_every_refresh_rule_stays_uncalibrated_even_when_days_are_given(
    session_factory,
) -> None:
    """天数可以给，「已标定」不能 —— 给了天数的那些面照样是 ``calibrated: false``。

    这条与上面那条按签名判的判据不同：那条钉的是「参数不存在」，这条钉的是
    **实现也没有从别处推**。一个 ``calibrated = days is not None`` 的实现能通过
    签名判据，而它会让 ``uncalibrated_aspects`` 永远为空。

    给的天数是**五个**（不是七个）：另外两格落缺省。所以这里断言的是
    「七个面都在未标定名单上」（而不是「等于我给的那五个」）—— 后者在拆成七个面
    之后会把「缺省补全了另外两格」读成一次失败，而缺省补全恰恰是这条测试的邻居
    在量的事。给了天数的那些面还要求天数**逐条等于**给的值：一次静默丢键的
    实现会让它们落回缺省，而 ``calibrated`` 照样是 ``False``。
    """
    aspects = {
        "source_coverage": 180,
        "asset_attribute": 7,
        "account_relation": 90,
        "contact_person_role": 90,
        "product_fit": 30,
    }
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "refresh_days": aspects}),
    )
    created = _ok(outcome)

    assert created["version"]["refresh"], "refresh 不能是空表 —— 空表读起来像「永不过期」"
    for rule in created["version"]["refresh"]:
        assert rule[_UNCALIBRATED] is False, (
            f"{rule['aspect']} 被标成已标定 —— 标定是一个经验主张，一次调用不是它的来源"
        )
    # 未标定**不是** problem：它被报出来，但不阻断（§4）。
    assert created["validation"]["ok"] is True
    assert created["validation"]["problems"] == []
    uncalibrated = set(created["validation"]["uncalibrated_aspects"])
    assert uncalibrated == {rule["aspect"] for rule in created["version"]["refresh"]}, (
        "未标定名单应当覆盖**全部**七个面（给过的与落缺省的都算），"
        "而它要的是刷新计划里真的有的那些键 —— 不是我去掉两个之后剩下的那五个"
    )
    assert len(uncalibrated) == 7, "七个面一个不少"
    given = {
        rule["aspect"]: rule["after_days"] for rule in created["version"]["refresh"]
    }
    for name, days in aspects.items():
        assert given[name] == days, f"{name} 给的是 {days}，实际落的是 {given[name]}"


def test_given_days_replace_the_default_without_touching_other_aspects(
    session_factory,
) -> None:
    """只给一两个面时，其余面回落到**未标定的缺省**，而不是消失。

    少报一个面与「这个面永不过期」在调度器眼里长得一样（都选不出工作），而两者的
    处置完全不同：一个是遗漏，一个是决定。所以缺省必须补全，且必须标着未标定。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "refresh_days": {"asset_attribute": 7}}),
    )
    created = _ok(outcome)
    rules = {rule["aspect"]: rule for rule in created["version"]["refresh"]}

    assert rules["asset_attribute"]["after_days"] == 7
    assert rules["asset_attribute"]["calibrated"] is False
    others = {name: rule for name, rule in rules.items() if name != "asset_attribute"}
    assert others, "只给一个面不该让其余的面消失"
    assert all(rule["calibrated"] is False for rule in others.values())


def test_given_outreach_numbers_stay_uncalibrated(session_factory) -> None:
    """触达节奏可以给数字，「已标定」照样给不了 —— 与 ``refresh_days`` 逐条同型。

    §6 逐字：「touch 2–4 必须严格读**已确认**的上一阶段与允许的等待期/次数」。
    「已确认」在这里不是一句空话：它是 ``calibrated`` 那一格，而这一格没有
    任何入参能置真。一个 ``calibrated = outreach is not None`` 的实现能通过
    签名判据，而它会让每一份回执都声称自己按一份获批的策略在发信。
    """
    outcome = session_factory(
        (
            "create_scene",
            {
                **BASE_CREATE,
                "outreach": {
                    "max_per_wave": 3,
                    "waits": {"stage-2": 24, "stage-3": 72, "stage-4": 168},
                },
            },
        )
    )
    created = _ok(outcome)
    declared = created["version"]["outreach"]

    assert declared["max_per_wave"] == 3, "给了的数字必须生效"
    assert declared["waits_hours"]["stage-2"] == 24
    assert declared["waits_hours"]["stage-3"] == 72
    assert declared["waits_hours"]["stage-4"] == 168
    # 没给的那一格回落到缺省，且**逐项**报出「还没标定」。
    assert declared["max_per_recipient"] > 0
    assert sorted(declared["uncalibrated"]) == [
        "outreach.prior_touches[stage-2]",
        "outreach.prior_touches[stage-3]",
        "outreach.prior_touches[stage-4]",
        "outreach.waits[stage-2]",
        "outreach.waits[stage-3]",
        "outreach.waits[stage-4]",
    ], "给了几项不等于那几项标定过 —— 未标定的那一份清单必须逐项点出来"


def test_an_unknown_outreach_key_is_refused_on_the_spot(session_factory) -> None:
    """不认识的触达规则键当场拒绝。

    与「不认识的复核面」同一条：一个静默落回缺省值的键，症状只是「我设的值没
    生效」—— 而缺省本身也是未标定的，于是「拼错了」与「确实没标定」在报告上
    逐字相同。第一段 ``stage-1`` 也是一个不认识的段名：它没有上一段。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "outreach": {"max_per_week": 3}}),
        ("create_scene", {**BASE_CREATE, "outreach": {"waits": {"stage-1": 24}}}),
    )
    assert "max_per_week" in _refused(outcome, 0)
    assert "stage-1" in _refused(outcome, 1)


def test_an_unknown_budget_line_or_refresh_aspect_is_refused_on_the_spot(
    session_factory,
) -> None:
    """不认识的科目与不认识的复核面都当场拒绝，并把闭集列出来。

    两条都要过：一个静默忽略未知键的实现会让调用方以为额度写进去了，而额度是
    §6 里那套「工具强制」的东西 —— 一个不存在的科目被静默吞掉，等于预算少了一条。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "budget": {"searches": 20}}),
        ("create_scene", {**BASE_CREATE, "refresh_days": {"everything": 1}}),
    )
    budget_text = _refused(outcome, 0)
    assert "searches" in budget_text
    assert "search" in budget_text, "拒绝里要列出本部署真的有的那几个科目"

    aspect_text = _refused(outcome, 1)
    assert "everything" in aspect_text
    assert "asset_attribute" in aspect_text, "拒绝里要列出本部署真的有的那几个面"


def test_additional_exclusions_can_only_add(session_factory) -> None:
    """追加的排除词与 profile 已批准的那几条**取并集**，一个都减不掉。

    这一条的问法很具体：先记下列表，再追加一个，然后断言原来那些**逐条还在**。
    只断言「新的那个在列表里」的实现可以让一个整体替换的语义通过 —— 而在这一侧，
    整体替换恰好是那个会把人邮箱放进名单的方向。
    """
    outcome = session_factory(
        ("create_scene", BASE_CREATE),
        ("add_scene_version", {"additional_exclusions": ["probe-extra"]}),
    )
    first = _ok(outcome, 0)
    second = _ok(outcome, 1)

    assert first["version"]["exclusions"], "profile 必须本来就带着已批准的排除清单"
    assert set(first["version"]["exclusions"]) < set(second["version"]["exclusions"])
    assert set(first["version"]["exclusions"]) <= set(second["version"]["exclusions"]), (
        "追加不能删掉 profile 已批准的任何一条"
    )
    assert "probe-extra" in second["version"]["exclusions"]
    assert second["version"]["exclusions"] == sorted(second["version"]["exclusions"]), (
        "顺序必须确定 —— 它进 frozen_sha256，而一个抖动的清单是一份抖动的授权依据"
    )


# ----------------------------------------------------------------------
# 拒绝的形状
# ----------------------------------------------------------------------
def test_activate_without_a_version_is_refused(session_factory) -> None:
    """``activate`` 不写版本就拒 —— 「最新那一版」不是可以推断的默认。

    最新版可能是刚写下的、还没看过的那一版，而让一个没人读过的版本静默生效正是
    这条判据要防的事。所以这里先写下第 2 版（**不激活**），再试一次不带版本的
    激活：它必须被拒，而 ``effective_version`` 必须还停在第 1 版。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("add_scene_version", {"note": "还没人看过"}),
        ("control_scene", {"action": "activate"}),
        ("get_scene", {}),
    )
    text = _refused(outcome, 2)
    assert "第几版" in text

    read = _ok(outcome, 3)
    assert read["record"]["effective_version"] == 1, "被拒的那次激活改了状态"
    assert read["record"]["latest_version"] == 2


def test_pause_without_a_reason_is_refused_and_the_state_does_not_move(
    session_factory,
) -> None:
    """``pause`` 必须给理由，且理由必须是那个闭集里的一个。

    两个理由的处置不同（``operator_pause`` 由操作者自己解除，``policy_hold`` 要等
    那条策略不再成立），所以它们不能合并成一个布尔 —— 而「以人为由停的」与
    「以规则为由停的」在只有 ``paused`` 这个状态时读不出来。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("control_scene", {"action": "pause"}),
        ("control_scene", {"action": "pause", "reason": "because-i-said-so"}),
        ("get_scene", {}),
    )
    assert "理由" in _refused(outcome, 1)
    unknown = _refused(outcome, 2)
    assert "because-i-said-so" in unknown
    assert "operator_pause" in unknown, "拒绝里要列出本部署真的有的那几个理由"

    read = _ok(outcome, 3)
    assert read["record"]["state"] == "active", "两次被拒的暂停都改了状态"


def test_a_parameter_the_action_does_not_take_is_refused_not_ignored(session_factory) -> None:
    """``version`` 挂在 ``pause`` 上、``reason`` 挂在 ``resume`` 上，都是**错误**。

    多给一个参数不会被忽略，它会被读成「你指定了某样东西」—— 而这次调用里没有
    任何一处会用到它。静默接受的表现是调用方以为自己指定了「暂停哪一版」。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("control_scene", {"action": "pause", "reason": "operator_pause", "version": 1}),
        ("control_scene", {"action": "resume", "reason": "operator_pause"}),
    )
    assert "version" in _refused(outcome, 1)
    assert "reason" in _refused(outcome, 2)


def test_an_unknown_action_is_refused_and_the_closed_set_is_named(session_factory) -> None:
    """自由字符串的动作名当场拒绝，并列出闭集。

    一个自由字符串会被实现静默当成某一条分支处理 —— 于是 ``action='Activate'``
    与 ``action='activate'`` 得到两种不同的结果，而调用方以为它们一样。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("control_scene", {"action": "Activate"}),
        ("control_scene", {"action": "delete"}),
    )
    for index in (1, 2):
        text = _refused(outcome, index)
        assert "delete" in text or "Activate" in text
        for known in ("activate", "pause", "resume", "archive"):
            assert known in text, "拒绝里要列出本部署真的有的那几个动作"


def test_an_illegal_transition_comes_back_as_a_result_not_a_crash(session_factory) -> None:
    """非法转移是一条**结果**。``draft → paused`` 过不去，而回执读得出来。"""
    outcome = session_factory(
        ("create_scene", BASE_CREATE),
        ("control_scene", {"action": "pause", "reason": "operator_pause"}),
        ("control_scene", {"action": "resume"}),
    )
    assert "draft" in _refused(outcome, 1)
    assert "activate" in _refused(outcome, 2)


def test_archived_is_terminal(session_factory) -> None:
    """归档之后**没有**出边：再想激活或暂停都是拒绝。

    ``SCENE_TRANSITIONS[ARCHIVED]`` 是空集。这里不在前面再加一道判断，所以这条
    用例证明的是那台状态机本身 —— 它若被改成「归档可以恢复」，这条会红。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("control_scene", {"action": "archive"}),
        ("control_scene", {"action": "activate", "version": 1}),
        ("control_scene", {"action": "pause", "reason": "operator_pause"}),
        ("get_scene", {}),
    )
    assert _ok(outcome, 1)["scene"]["state"] == "archived"
    _refused(outcome, 2)
    _refused(outcome, 3)
    assert _ok(outcome, 4)["record"]["state"] == "archived"


def test_add_scene_version_before_any_create_is_refused_with_the_next_step(
    session_factory,
) -> None:
    """没有第 1 版可继承时，改版拒绝并点名该做什么。"""
    outcome = session_factory(("add_scene_version", {"note": "无中生有"}))
    text = _refused(outcome)
    assert "还没有立项过" in text
    assert "create_scene" in text, "拒绝必须给出下一步，否则调用方只能猜"


# ----------------------------------------------------------------------
# 没接线 / 没建库：两句不同的话
# ----------------------------------------------------------------------
def test_an_instance_without_product_loop_answers_not_wired_not_unknown(settings, tmp_path) -> None:
    """没接产品环的实例里，这五个名字**仍在** ``tools/list`` 上，而调用得到一句
    「在目录里，但当前服务实例尚未接线（Phase J）」。

    **不是** ``Unknown tool``。那句话说「没有这个工具」，而这里的事实是「有，这个
    实例没接」—— 两者的处置不同（一个要改调用方，一个要改部署）。这条判据来自
    ``guard.unbacked_message``，与 ``research=None`` 时那几条工具同款。

    所以这里比的是**两句话不一样**：接线的缺失不能被答成目录里没有这个名字。
    """
    ring_path, token = _ring(tmp_path, "claude-control", [Role.CONTROLLER])
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        product_loop=None,
        scenes=None,
        calls=(("create_scene", BASE_CREATE), ("definitely_not_a_tool", {})),
        want_tool_list=True,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    for name in (
        "create_scene",
        "add_scene_version",
        "control_scene",
        "import_motherpool",
        "list_accounts",
    ):
        assert name in outcome.tool_names, f"{name} 在目录里，就该出现在 tools/list 上"

    unbacked = _refused(outcome, 0)
    assert "尚未接线" in unbacked
    assert "Phase J" in unbacked, "这句话要带上阶段号：调用方据此判断该等还是该改"
    assert "Unknown tool" not in unbacked

    assert "Unknown tool" in _refused(outcome, 1), (
        "不存在的名字仍然是 Unknown tool —— 两句话必须不一样"
    )


def test_get_scene_says_not_wired_rather_than_absent(settings, tmp_path) -> None:
    """``scenes=None`` 时那格是 ``not_wired``，**不是** ``absent``。

    前者是接线事实，后者是业务结论。把前者答成后者，会让一个没接存储的部署
    看起来像一个空的市场 —— 而这两件事的处置是「去接线」与「去立项」。
    """
    ring_path = tmp_path / "tokens.json"
    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "claude-control", [Role.CONTROLLER]))),
        encoding="utf-8",
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        scenes=None,
        calls=(("get_scene", {}),),
        want_tool_list=False,
    )
    payload = _ok(outcome)
    assert payload["record_state"] == "not_wired"
    assert payload["record"] is None
    # 那句话要**明确否认**它等于「没有立项过」，而不是只换个说法。
    assert "没有接场景存储" in payload["record_note"]
    assert "不是「没有立项过」" in payload["record_note"]


def test_get_scene_says_no_store_when_nothing_has_ever_been_written(
    session_factory, settings: Settings
) -> None:
    """库还没建时是 ``no_store``，并且**如实**说那是「还没写过任何东西」。

    这一格与 ``not_wired`` 的区别是「接线了、但还没建库」。它与 ``absent`` 的
    区别更细：``absent`` 是「库在，但没有这个 Scene 的立项记录」—— 一个读起来像
    「你把 Scene 名写错了」的答案，而这里根本没有库可查。
    """
    assert not settings.database_path.is_file(), (
        "这条用例的前提是盘上还没有库 —— 夹具若改了这一点，它测的就不是这件事了"
    )
    outcome = session_factory(("get_scene", {}))
    payload = _ok(outcome)
    assert payload["record_state"] == "no_store"
    assert payload["record"] is None


# ----------------------------------------------------------------------
# 研究身份：看不见，也调不动
# ----------------------------------------------------------------------
def test_a_research_principal_cannot_see_or_call_the_product_loop(
    settings: Settings, tmp_path: Path
) -> None:
    """研究身份**看不见**产品环（J 段中产品环那一半），调它们拿到的是「工具不存在」。

    两件事都要做。只做「调不动」时，研究侧 worker 仍然能列出控制目录 —— 那既是
    一次信息泄漏（它知道了这套服务能经营什么），也是一条把「不属于你的工具」与
    「不存在的工具」区分开的旁路。所以这里比的还有**措辞同形**：拒绝的那句话与
    「这个名字根本不存在」逐字一致。

    ## 名单从目录里**推**出来，不是手写的

    这份名单原本是手写的，写着七条而上面那句话说的是「八条」—— 没有任何机器检查
    散文里的算术，所以它落后了也没人知道。现在它由 ``TOOL_CATALOG`` 里 J 段、
    控制者角色、且不属于外联那一半的名字推出来，下面那张载荷表必须**恰好**覆盖
    它们。于是「新加一条控制者工具」这件事会当场变红，且红的话里点名的是那一条 ——
    而「研究身份看不见它」这句话就不再依赖谁的记性。

    外联那一半（``OUTREACH_TOOLS``）由 ``tests/test_mcp_outreach.py`` 用同一个形状
    钉住，两个文件各管一半：这里不重复测它，也不假装它不存在（减去它才是产品环）。
    """
    ring_path, token = _ring(tmp_path, "worker-1", [Role.RESEARCHER])
    scenes = default_scene_store(settings)
    #: 收件箱那七条与激活闸门**共用**的一个案件存储 —— 组合根是这么接的，这里也
    #: 只有一份。两处各建一份的话，「控制者读到的收件箱」与「闸门看的收件箱」
    #: 会是两个库，而两处各自都正常。
    review_cases = default_review_store(settings)
    #: 维护账**同一个**工厂交给两处，与组合根逐字同款（``__main__.py`` 里
    #: ``maintenance = default_maintenance_store(settings)`` 那一行）。两处各建一份
    #: 的形态是「维护 tick 记了观测、增量面读不到」，而两边的报告各自都正常。
    maintenance = default_maintenance_store(settings)

    #: 每一条控制者工具的一个**形状正确**的载荷。这里不指望它们跑成功（研究身份
    #: 根本到不了处理器），但不能是随手编的字典 —— 一份载荷在别处会变成「这条工具
    #: 收这两个参数」，而那是 ``test_no_product_loop_parameter_...`` 在管的事。
    payloads: dict[str, dict[str, Any]] = {
        "create_scene": BASE_CREATE,
        "add_scene_version": {"note": "研究身份看不见这一条"},
        "control_scene": {"action": "archive"},
        "import_motherpool": {"artifact_ref": "motherpool"},
        "run_discovery": {"scope": "devon-discovery-v1", "region": "Devon"},
        "promote_receipt": {"receipt_id": "receipt-0123456789abcdef"},
        "list_assets": {},
        "remember_responsibility": {
            "venue_asset_id": "named-0123456789abcdef",
            "judgements": [{"account_id": "a", "relation": "owns", "evidence_ids": ["e1"]}],
        },
        "list_responsibilities": {},
        "list_accounts": {"name": "known-accounts"},
        "remember_accounts": {
            "accounts_rows": [
                {
                    "account_id": "a",
                    "organisation": "A",
                    "evidence_ids": ["e1"],
                }
            ]
        },
        "list_account_pool": {},
        "freeze_cohort": {},
        "list_cohorts": {},
        "get_cohort": {},
        "get_maintenance_plan": {"name": "known-accounts"},
        "run_maintenance": {"name": "known-accounts"},
        "get_ops_report": {"name": "known-accounts"},
        # §B21 的续跑状态页。它不收参数 —— 它读的是**本部署**（``settings.profile``）
        # 那一个场景的未结意图，而「读哪一个市场」不是调用方能挑的（挑得动就等于
        # 这条只读工具能去读别人的市场）。同一条理由写在 ``continuation_handlers``
        # 的签名上：没有 ``scene_id`` 这个参数。
        "get_continuation_status": {},
        # §B15 的访问尝试账读面。它读的那本账**跨主体**（``scope`` 就是主体），
        # 所以它落在控制面这一半上；这里要断言的正是「研究身份连名字都看不见」，
        # 而不是「它读不到自己的那几行」—— 后者会把一次越权读答成一个空结果。
        "list_fetch_attempts": {},
        # §8/§9 的对账读面（``open_reservations`` 的生产消费者）。它读的那本账同样
        # **跨主体** —— ``scope`` 就是主体，而且它读的是那批主体**还剩多少额度**，
        # 比访问尝试账更直接地是经营信息。理由与上一条逐字相同：必须真的接线，
        # 否则「不可见」的理由会从「角色不对」漂成「没接线」。
        "list_open_reservations": {},
        # §12 的收件箱那七条里有六条落在这一半上（第七条 ``approve_policy_change``
        # 只对获授权者可见，所以不在这里；它的参数面由 ``tests/test_mcp_review.py``
        # 自己钉住）。**这一组必须真的接线**才作数：``review=None`` 时那几条虽然
        # 照旧对研究身份不可见，但不可见的理由变成了「没接线」—— 那条路径与
        # 「角色不对」是两件事，而这里要断言的正是后者。
        "list_review_cases": {},
        "get_review_case": {"case_id": "rc-0123456789abcdef"},
        "get_review_flow": {},
        "claim_review_case": {"case_id": "rc-0123456789abcdef", "expected_version": 1},
        "resolve_review_case": {
            "case_id": "rc-0123456789abcdef",
            "outcome": "correct_object",
            "answer": "研究身份看不见这一条",
            "expected_version": 1,
        },
        "get_scene_impact": {"from_version": 1, "to_version": 2},
        # §B17 的 Run 受理面（切片 m1）。载荷形状正确即可，理由与这一整表逐字相同
        # —— 研究身份到不了处理器。而它比别的几条更值得摆在这里：**工作区与对象
        # 范围不在这份载荷里**（它们取自盘上那个场景的生效版本），计划也由服务端
        # 现编。所以下面这两样是「谁给的」的样板：``cutoff`` 与 ``idempotency_key``
        # 由调用方给，服务端不补缺省 —— 一个能替你挑幂等键的服务，重发就不再是
        # 「同一件事」，而它会照样返回一行。
        "submit_run": {
            "context": {
                "geo": "GB",
                "entity_classes": ["school"],
                "asset_classes": ["pitch"],
                "organization_classes": ["trust"],
                "scenario_candidates": [
                    {
                        "name": "secondary school",
                        "reading": "k12",
                        "provenance": "declared",
                        "source": "研究身份看不见这一条",
                    }
                ],
                "objective": "研究身份看不见这一条",
                "evidence_requirements": ["official_site"],
            },
            "cutoff": "2026-10-01T09:00:00+00:00",
            "idempotency_key": "run-2026-09-28-a",
        },
        # 切片 g 的读面：与 ``submit_run`` **共用同一段前奏**的那一份读数。载荷给的
        # 是与上面那一格**同一组**三样（``context`` / ``cutoff`` / ``idempotency_key``）
        # —— 这正是那一节要说的事：两个面收的是同一组入参，编出的是同一份规格。
        # 另外几样（``budget`` / ``execution_mode`` / ``scene_id`` / ``window`` /
        # ``name`` / ``cohort``）与 ``submit_run`` 逐字相同，都有缺省，所以这里不写。
        "preview_run": {
            "context": {
                "geo": "GB",
                "entity_classes": ["school"],
                "asset_classes": ["pitch"],
                "organization_classes": ["trust"],
                "scenario_candidates": [
                    {
                        "name": "secondary school",
                        "reading": "k12",
                        "provenance": "declared",
                        "source": "研究身份看不见这一条",
                    }
                ],
                "objective": "研究身份看不见这一条",
                "evidence_requirements": ["official_site"],
            },
            "cutoff": "2026-10-01T09:00:00+00:00",
            "idempotency_key": "run-2026-09-28-a",
        },
        # 读回面（切片 m2）。它的入参只有一格，而那一格是**内容寻址**的 id ——
        # 32 位小写十六进制。取这里这个值不是随手编的：``run-20260926t101530``
        # 那种标签形状是另一个 id 空间（``_derived_run_id()``），把它填进这一格
        # 在别处是一眼看得出的错（``RunIncrement.run_id`` 的校验器正是为它写的，
        # 见 ``docs/B17_DESIGN_SLICES_2026-09-28.md`` 的差量 3）。
        "get_run": {"run_id": "0123456789abcdef0123456789abcdef"},
        # 与 ``get_run`` 同形：读回面的两条入参都是**一个内容寻址的 id**。这里给的
        # 是一个形状正确、而库里没有的值 —— 它不会跑到处理器（研究身份在闸门那一层
        # 就被挡下了），所以这个值的作用只有一个：让「这一条工具收哪几个参数」在
        # 载荷表上有一份写下来的形状。
        "get_run_increment": {"run_id": "0123456789abcdef0123456789abcdef"},
        # 结束面（切片 e4）。四格，其中 ``folded`` 可省 —— 缺省是「这一轮一行都没折」
        # 这份**主张**，它照旧要过账那一关。这里给 ``delta`` 一份最小而完整的形状
        # （``effects`` 一格，所以它不是一份空产出），所以它撞不上 ``ending_refusal``
        # —— 那不是这一条判据要判的事（这一条判的是权限面：研究身份看不见它）。
        "close_run": {
            "run_id": "0123456789abcdef0123456789abcdef",
            "delta": {
                "effects": [
                    {
                        "cell": "held",
                        "key": "src-0123456789abcdef",
                        "why": "研究身份看不见这一条",
                    }
                ]
            },
            "status": "failed",
            "folded": [],
        },
        # B01 的**登记面**（切片 b01-b，``mcp_server/product_catalog.py``）。三条各给
        # 一个最小而完整的形状：必填的那几格都填上，可选的一格不填（域层有缺省）。
        # 三条工具**都不产生外部调用**，而那不是这里判的事 —— 那一条由
        # ``tests/test_registration_is_offline.py`` 从 import 闭包上判（这里是权限面）。
        #
        # 它们的载荷里**没有** ``workspace`` / ``scene_id``：工作区取自
        # ``settings.profile``，不是调用方能挑的（理由写在那个模块的 docstring 上）。
        # 这一格在权限面上尤其要紧 —— 一个能挑工作区的登记接口，会让一次越权登记在
        # 回执上完全看不出来。
        "put_product_version": {
            "product_id": "probe-product",
            "version": 1,
            "display_name": "研究身份看不见这一条",
            "owner": "研究身份看不见这一条",
        },
        "put_offer_version": {
            "offer_id": "probe-offer",
            "version": 1,
            "name": "研究身份看不见这一条",
            "purpose": "研究身份看不见这一条",
            "owner": "研究身份看不见这一条",
        },
        "put_product_line": {
            "line_id": "probe-line",
            "name": "研究身份看不见这一条",
            "owner": "研究身份看不见这一条",
        },
        # B01 的**查缺面**（切片 b01-c，``mcp_server/product_readiness.py``）。两条都是
        # 只读，各收主语的坐标。这里尤其值得写下来的是一格**缺席**：载荷里没有 ``at``
        # —— 时刻取自服务端时钟（那一族的三张清单每一行都拿同一个时刻判，一个由调用方
        # 挑的时刻会让同一条盘上事实在两个调用方那里得到相反的两份读数）。这一条与
        # 上面 ``_FORBIDDEN_PARAMETERS`` 里那个 ``at`` 是同一件事，而那一张表只跑
        # ``product_loop_handlers``，所以这两条的名字由本表自己的判据守着，参数面由
        # ``tests/test_mcp_product_readiness.py`` 自己钉。
        "get_product_version_readiness": {"product_id": "probe-product", "version": 1},
        "get_offer_version_readiness": {"offer_id": "probe-offer", "version": 1},
        # B01 的**启动层**（切片 b01-d，``mcp_server/launch.py``）。只读，收主语的坐标；
        # 载荷里没有 ``at``，与上面两条同一个理由（时刻取自服务端时钟）。它比上面两条
        # 多出**四格可选**（产品/方案各自的 id 与版本），而这一份载荷**一格都不给** ——
        # 不是随手省事：那四格缺省时这一条仍然是一次完整的预览（「这一版没点名要卖什么」
        # 是一份合法声明，不是缺参数），把可选格塞进载荷会让「可选」在表上看起来像必填。
        # 同理没有 ``workspace``：工作区取自 ``settings.profile``。
        "preview_launch": {"scene_id": "probe-scene", "version": 1},
    }
    product_loop_half = {
        name
        for name, spec in TOOL_CATALOG.items()
        if spec.phase == "J"
        and Role.CONTROLLER in spec.roles
        and name not in OUTREACH_TOOLS
    }
    assert set(payloads) == product_loop_half, (
        "目录里产品环那一半与控制者工具变了：多出来的那条没有载荷、"
        "少掉的那条载荷已经过期 —— 而这两件事都要在这里被看见"
    )
    controller_calls: tuple[tuple[str, dict[str, Any]], ...] = tuple(
        (name, payloads[name]) for name in sorted(product_loop_half)
    )
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        product_loop=product_loop_handlers(
            settings=settings,
            scenes=scenes,
            maintenance=maintenance,
            assets=default_asset_store(settings),
            relations=default_relation_store(settings),
            accounts=default_account_pool_store(settings),
            cohorts=default_account_cohort_store(settings),
            review=review_cases,
            acquisition=None,
            router=None,
            documents=None,
        ),
        review=review_handlers(settings=settings, scenes=scenes, review=review_cases),
        review_store=review_cases,
        # §B21 的那一条也必须**真的接线**才作数 —— 理由与上面收件箱那条逐字相同：
        # 没接线时它虽然照旧对研究身份不可见，但不可见的理由变成了「没接线」，而这
        # 一条路径与「角色不对」是两件事。三个工厂与 ``product_loop`` 收到的是同一
        # 组，这也正是组合根 ``_compose`` 里的接法。
        continuation=continuation_handlers(
            settings=settings,
            jobs=lambda: SqliteJobStore(settings.database_path),
            registry=default_wakeup_registry(settings),
            scenes=scenes,
        ),
        # §B15 的访问尝试账读面同一条：不接线时它对研究身份照样不可见，但那时候
        # 不可见的理由是「这一条根本没注册」，而这里要断言的是「注册了、按角色
        # 挡住了」。两个理由在``tools/list`` 上同形 —— 所以必须真的接线才作数。
        attempts=fetch_attempt_handlers(
            settings=settings, attempts=default_attempt_store(settings)
        ),
        # §8/§9 的对账面同一条：工厂的形状与 ``_compose`` 里那一份逐字相同
        # （``partial(SqliteBudgetLedger, settings.database_path)``）—— 用一本建好的
        # 账会让「组合期就把库建出来了」这件事在判据上消失。
        reservations=budget_handlers(
            settings=settings,
            ledger_factory=partial(SqliteBudgetLedger, settings.database_path),
        ),
        # §B17 的受理面同一条：不接线时 ``submit_run`` 对研究身份照样看不见，但
        # 那时候它落的是「尚未接线」那一句，而这一组要断言的是「注册了、按角色
        # 挡住了」。两个理由在 ``tools/list`` 上同形 —— 所以必须真的接线才作数。
        # 三个工厂与 ``_compose`` 里那一份逐字同形（``scenes`` 是**同一个**对象，
        # 受理读的场景与写下那一行的库必须是同一个，理由写在 ``server.py`` 的
        # ``runs:`` 参数上；``ledger`` 与上面 ``reservations=`` 收到的是**同一句**
        # ``partial``，因为条件 4 的「达累计预算」读的必须是扣款的那本账）。
        # ``assets`` 是 e4 多出来的第五样（``close_run`` 按业务键逐条问池子「这一行在
        # 不在」）：这一处与 ``_compose`` 里那一份逐字同形 —— 同一个
        # ``Settings.database_path`` 上的``default_asset_store(settings)``。
        runs=run_handlers(
            settings=settings,
            scenes=scenes,
            maintenance=maintenance,
            runs=default_run_store(settings),
            ledger=partial(SqliteBudgetLedger, settings.database_path),
            assets=default_asset_store(settings),
            # 跟进池同款（``close_run`` 要把这一轮开出来的待办写回 ``actions`` 那一
            # 格）：同一个 ``Settings.database_path`` 上的
            # ``default_action_item_store(settings)``。
            followups=default_action_item_store(settings),
            # 切片 f 的两条：``submit_run`` 在 watch 上按 ``name`` / ``cohort`` 取
            # 主体。这里的调用面与它们无关（这一条判的是「工具名表」），所以照
            # 「组合根说什么就说什么」给 ``None`` —— 那两个参数**必须说出来**正是
            # 这条纪律本身。
            accounts=None,
            cohorts=None,
        ),
        scenes=scenes,
        # B01 的两面（登记面 + 查缺面）。**接上它们不是为了下面那几条断言更强** ——
        # 实测：``guard._refuse`` 的顺序是角色 → 工作区 → ``unavailable`` → 背书，
        # 而 ``_unauthorized`` 给的就是 ``unknown_tool_result``，所以「接没接线」在
        # 「研究身份看见的是一句 Unknown tool」这件事上同结果。接上是因为这个文件
        # 是 J 段控制面的**全套缩影**：它与组合根 ``_compose`` 的差别应当只剩「给谁看」，
        # 而上面那几组「不接线就答『尚未接线』」的论证在这里**不成立** —— 那正是
        # 把它写下来的理由，免得下一个照着抄的人以为这一处也在判接线。
        product_catalog=product_catalog_handlers(
            settings=settings, catalogs=default_product_catalog_store(settings)
        ),
        product_readiness=product_readiness_handlers(
            settings=settings,
            catalogs=default_product_catalog_store(settings),
            claims=default_outreach_store(settings),
        ),
        # B01 的启动层（切片 b01-d）：与上面那一面同一条，必须**真的接线**才作数。
        # 三样工厂与 ``_compose`` 里那一份逐字同形，``scenes`` 尤其 —— 它与上面
        # ``product_loop=`` / ``runs=`` 收到的是**同一个** ``scenes``，因为「这一版
        # 声明在不在盘上」只能有一个答案。
        launch=launch_handlers(
            settings=settings,
            scenes=scenes,
            catalogs=default_product_catalog_store(settings),
            claims=default_outreach_store(settings),
        ),
        calls=(*controller_calls, ("definitely_not_a_tool", {})),
        want_tool_list=True,
    )
    assert not outcome.handshake_error, outcome.handshake_error

    for name, _ in controller_calls:
        assert name not in outcome.tool_names

    # 同形：不属于你的工具与不存在的工具，答案里**只有工具名**不一样。
    # 逐条比，而不是只比第一条 —— 只比第一条的实现漏得掉后面几条的措辞分歧。
    unknown = _refused(outcome, len(controller_calls))
    for index, (name, _) in enumerate(controller_calls):
        text = _refused(outcome, index)
        assert "Unknown tool" in text
        assert text.replace(name, "X") == unknown.replace("definitely_not_a_tool", "X")


# ----------------------------------------------------------------------
# 池：import_motherpool
# ----------------------------------------------------------------------
#: 一份母池工件（``.jsonl``，一行一条）。**不是业务事实** —— 形状取自上游登记的
#: 常态：按对象类型编号的导出、名称那一格里回填标识、以及一个地点列。
#:
#: 五行的去向**故意**各不相同 —— resolved / pending / merged / excluded / failed。
#: 「每一原始行都有一个处置」这句话只有在五种都出现过的时候才真的被考验到：
#: 只有一种去向的数据上，任何实现都是对的。
_MOTHERPOOL_LINES: tuple[str, ...] = (
    # 0 → resolved（这一行现在可以去研究）
    '{"type": "way", "id": "101", "name": "Example Village Hall", "location": "Devon"}',
    # 1 → pending：名称那一格写的就是标识，以标识回填名称是导出的常态
    '{"type": "node", "id": "202", "name": "202", "location": "Devon"}',
    # 2 → merged：与第 0 行是同一个上游对象（``way:101``）
    '{"type": "way", "id": "101", "name": "Example Village Hall", "location": "Devon"}',
    # 3 → excluded：命中一条 profile **已声明**的排除项
    '{"type": "node", "id": "303", "name": "Example Golf Club", "exclusions_hit": ["golf"]}',
    # 4 → failed：这一行读不出来，但它照样占一个序号
    "这一行不是对象",
)

#: 五种去向各一行时的计数。写成一份字面量而不是从回执里推 —— 推出来的期望值
#: 与实现同步地错。
_MOTHERPOOL_COUNTS: dict[str, int] = {
    "resolved": 1,
    "merged": 1,
    "excluded": 1,
    "pending": 1,
    "failed": 1,
}


def _write_artifact(settings: Settings, name: str, lines: tuple[str, ...]) -> Path:
    """把一份工件写进 ``inputs_dir``。

    工具收的是**名字**不是路径（``find_artifact`` 的判据：收路径等于把「读哪个
    文件」交给调用方），所以这里也只给名字。
    """
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    path = settings.inputs_dir / f"{name}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


def _write_list(settings: Settings, name: str, body: str) -> Path:
    """把一份已知账号清单写进 ``inputs_dir``（形状见 ``write_accounts``）。"""
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    path = settings.inputs_dir / f"{name}.yaml"
    path.write_text(body, encoding="utf-8", newline="\n")
    return path


def test_import_motherpool_gives_every_raw_row_a_disposition(
    session_factory, settings: Settings
) -> None:
    """读一份工件 → 五行各得一个去向，五个数**加起来**等于原始行数。

    报的还有 ``counts_line`` 末尾那两个**并列**的数。行数与不同的实体数是两个问题
    （「读进去多少条」与「里面有多少个东西」），合成一个会让「25 行是同一块场地的
    三种写法」读成「有 25 个客户」—— 所以这里断言两个数都出现，而**不是**断言它们
    相等。
    """
    _write_artifact(settings, "motherpool", _MOTHERPOOL_LINES)
    outcome = session_factory(("import_motherpool", {"artifact_ref": "motherpool"}))
    payload = _ok(outcome)
    receipt = payload["receipt"]

    assert receipt["counts"] == _MOTHERPOOL_COUNTS
    assert receipt["raw_row_count"] == len(_MOTHERPOOL_LINES)
    assert sum(receipt["counts"].values()) == receipt["raw_row_count"], (
        "五种处置之和必须等于原始行数 —— 差了就是有一行没有去向"
    )
    assert "distinct_entities" in receipt["counts_line"]
    assert receipt["resolved_row_count"] == 1
    assert receipt["distinct_resolved_count"] == 1

    # 第一次读：盘上本来没有这一份，所以这一次真的写了。
    assert payload["preexisting"] is False
    landed = Path(payload["receipt_path"])
    assert landed.is_file()
    assert landed.name == f"{receipt['receipt_id']}.receipt.json", (
        "id 与文件名的主干是同一个名字 —— 盘上「有哪几份回执」靠文件名回答"
    )

    # 下游会看到的东西：从**盘上那一份**读回来。
    assert payload["research"]["ok"] is True
    assert payload["research"]["subject_count"] == 1
    assert payload["research"]["blocker"] == ""


def test_import_motherpool_declares_how_the_batch_was_obtained(
    session_factory, settings: Settings
) -> None:
    """``source_method_id`` 不由调用方给，而且**写下去的就是那一个**。

    签名判据（``test_no_product_loop_parameter_can_certify_or_backdate_anything``）
    只证明「这个参数不存在」。一个从别处推出错误值、或者干脆回落到空串的实现照样
    能过那一条 —— 而 ``source_method_id`` 是「来源族多样性」唯一算得出来的依据，
    也是这份池子唯一说不出口的那句话（SOP L82：检索来的池子按补充证据读）。
    """
    _write_artifact(settings, "motherpool", _MOTHERPOOL_LINES)
    outcome = session_factory(("import_motherpool", {"artifact_ref": "motherpool"}))
    receipt = _ok(outcome)["receipt"]

    assert receipt["source_method_id"] == "import.register.v1"
    # profile 的声明进了回执 —— 整份拒绝那条路径的依据就是它。
    assert receipt["profile_id"] == settings.profile
    assert receipt["version"] == RECEIPT_VERSION
    # 调用方申报的出处：**没给就是缺席（空串），不是「范围是空的」**。
    assert receipt["batch_id"] == ""
    assert receipt["scope"] == ""
    assert receipt["cutoff"] == ""
    assert receipt["cursor"] == ""


def test_replaying_the_same_artifact_writes_nothing_and_says_so(
    session_factory, settings: Settings
) -> None:
    """同一份工件读两次：回执 **id 相同、盘上那一份逐字节没动**，第二次什么都没写。

    两次调用分成**两个会话**，中间在盘上取一次快照 —— 这是一个会话里连调两次做不到
    的事（第二次跑完再读盘，读到的是同一个结果，无论它写没写）。比的是字节而不只是
    id，因为重放幂等有两种坏法：第二种是落成另一个 id，第一种是**重写**（``created_at``
    被刷新 —— 这一格刻意不进 id，所以它变了 id 也变）。只查 id 的实现挡不住第一种。
    """
    _write_artifact(settings, "motherpool", _MOTHERPOOL_LINES)
    first = _ok(session_factory(("import_motherpool", {"artifact_ref": "motherpool"})))
    landed = Path(first["receipt_path"])
    before = landed.read_bytes()

    second = _ok(session_factory(("import_motherpool", {"artifact_ref": "motherpool"})))

    assert first["preexisting"] is False
    assert second["preexisting"] is True, (
        "第二次必须报「已经有一份」—— 「这一次真的写了吗」只能在写之前问："
        "事后比对 mtime 会把一次重放读成一次写入"
    )
    assert second["receipt"]["receipt_id"] == first["receipt"]["receipt_id"]
    assert second["receipt_path"] == first["receipt_path"]
    assert landed.read_bytes() == before, "重放不重写任何一格"
    # 盘上那一份的出处仍是**第一次**读的时刻。
    #
    # 比的是时刻而不是字符串：盘上是 ``…Z``，回执里是 ``…+00:00`` —— 同一个瞬间的
    # 两种 ISO-8601 写法（一个来自 ``model_dump(mode="json")``，一个来自
    # ``datetime.isoformat``）。两个都对，所以这条判据不能拿字符串比。
    disk = json.loads(landed.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(disk["created_at"]) == datetime.fromisoformat(
        first["receipt"]["created_at"]
    )
    receipts = sorted(settings.inputs_dir.glob("*.receipt.json"))
    assert len(receipts) == 1, f"重放不该落出第二份回执：{[p.name for p in receipts]}"


def test_an_undeclared_exclusion_refuses_the_whole_artifact(
    session_factory, settings: Settings
) -> None:
    """命中了 profile **没声明过**的排除项 → 整份拒绝，而且盘上什么都不留。

    这一条钉的是三件事，逐条都是别的实现会做错的：

    * **不是跳过那一行**：收下它等于让输入发明一条会静默删行的规则，而删掉的那一行
      在报告上只剩一个 ``excluded`` 计数。所以这里断言**好行也没被收下** —— 盘上
      没有回执。跳过那一行的实现会落下一份 receipt，``resolved=1``。
    * **不是记成 ``failed``**：那会把「导出与 profile 版本不匹配」渲染成「这一行
      读坏了」，而两者要修的东西相反（改词表 vs 改数据）。
    * 拒绝的话里要**列出 profile 真的声明过哪几条** —— 运维据此知道该往词表里补什么。
    """
    _write_artifact(
        settings,
        "motherpool-undeclared",
        (
            '{"type": "way", "id": "101", "name": "Example Village Hall"}',
            '{"type": "node", "id": "303", "name": "X", "exclusions_hit": ["golf_simulator"]}',
        ),
    )
    outcome = session_factory(
        ("import_motherpool", {"artifact_ref": "motherpool-undeclared"}),
    )
    text = _refused(outcome)

    assert "golf_simulator" in text, "拒绝里要点名到底是哪一条没声明过"
    assert "golf" in text, "也要列出 profile 声明过的那些 —— 否则不知道该补什么"
    assert not list(settings.inputs_dir.glob("*.receipt.json")), (
        "整份拒绝不能在盘上留下回执：留下一份就等于收下了那几行"
    )


def test_an_artifact_that_is_not_there_is_refused_by_name(
    session_factory, settings: Settings
) -> None:
    """找不到工件是一条**说得出的结果**，不是一个异常。

    它同时要说清去哪找（``inputs_dir``）与按什么名字找 —— 「找不到」这句话没有
    告诉运维把文件放到哪里。
    """
    outcome = session_factory(("import_motherpool", {"artifact_ref": "no-such-artifact"}))
    text = _refused(outcome)
    assert "no-such-artifact" in text
    assert str(settings.inputs_dir) in text


# ----------------------------------------------------------------------
# 池：list_accounts
# ----------------------------------------------------------------------
#: 一份已知账号清单。三行**故意**各占一种情形：有域名、名称那格只是内部 id、
#: 另一种 cohort。``org-is-the-id`` 那一行是 2026-09-23 那次真实运行的形状 ——
#: 内部 slug 被填进了名称那一格。
_ACCOUNTS_YAML = """accounts:
  - account_id: example-one
    organisation: Example One Ltd
    cohort: known_success
    domain: example-one.co.uk
  - account_id: example-two
    organisation: example-two
    cohort: known_success
  - account_id: example-three
    organisation: Example Three Ltd
    cohort: accessible_no_core_email
"""


def _write_accounts_list(settings: Settings) -> Path:
    return _write_list(settings, "known-accounts", _ACCOUNTS_YAML)


def test_list_accounts_reports_file_order_and_the_ordinal_that_carries_work_ids(
    session_factory, settings: Settings
) -> None:
    """逐行、按**文件顺序**，并把 ``ordinal`` 报出来。

    顺序不是排版：它决定 ``work_item_id``（``compile_work_items`` 比对整个序列），
    所以按 ``account_id`` 排一次序会让每一行换一个工作 id，而报告上看不出发生过
    什么。这里断言的是**顺序本身**。
    """
    _write_accounts_list(settings)
    outcome = session_factory(("list_accounts", {"name": "known-accounts"}))
    payload = _ok(outcome)

    assert payload["account_count"] == 3
    assert [row["ordinal"] for row in payload["accounts"]] == [0, 1, 2]
    assert [row["account_id"] for row in payload["accounts"]] == [
        "example-one",
        "example-two",
        "example-three",
    ]
    assert payload["input_ref"] == "known-accounts"
    # 清单没被按 cohort 或状态重排 —— 文件里第一个仍是第一个。
    assert payload["accounts"][0]["cohort"] == "known_success"
    assert payload["accounts"][2]["cohort"] == "accessible_no_core_email"
    assert payload["summary"]["by_cohort"] == {
        "known_success": 2,
        "accessible_no_core_email": 1,
    }


def test_list_accounts_says_null_when_there_is_no_store_yet(
    session_factory, settings: Settings
) -> None:
    """库还没建时那三个数是 ``null``，**不是 0** —— 并且另一条用例证明库在了就读得出 0。

    两个状态的读数必须**不同**：「还没建库」与「建了库、这个账号什么都没跑」写成
    同一个数，会让一个全新的部署看起来像一个跑过而颗粒无收的部署（§3.4）。
    所以这一条与下一条要一起读：只有这一条的话，``null`` 与 0 的差别没被证明是真的
    差别。
    """
    _write_accounts_list(settings)
    assert not settings.database_path.is_file(), (
        "这条用例的前提是盘上还没有库 —— 夹具若改了这一点，它测的就不是这件事了"
    )
    outcome = session_factory(("list_accounts", {"name": "known-accounts"}))
    payload = _ok(outcome)

    assert payload["store_present"] is False
    for row in payload["accounts"]:
        assert row["stored"] == {
            "route_count": None,
            "opportunity_count": None,
            "evidence_count": None,
        }, "库不存在时这三个数必须是 null —— 写成 0 会把『还没建库』答成『什么都没有』"
    assert "null" in payload["note"]


def test_list_accounts_reads_zero_once_the_store_exists(session_factory, settings: Settings) -> None:
    """库建起来之后同样的清单读到的是 **0**，而 ``store_present`` 是 ``True``。

    与上一条配成一对：这两条一起才说明那三个格子里的 ``null`` 是「读不出来」而不是
    「实现偷懒不查」。
    """
    from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore

    _write_accounts_list(settings)
    SqliteEvidenceStore(settings.database_path)  # 建库：构造点就会跑 ensure_schema

    outcome = session_factory(("list_accounts", {"name": "known-accounts"}))
    payload = _ok(outcome)
    assert payload["store_present"] is True
    for row in payload["accounts"]:
        assert row["stored"] == {
            "route_count": 0,
            "opportunity_count": 0,
            "evidence_count": 0,
        }


def test_list_accounts_flags_the_rows_that_cannot_be_dispatched(
    session_factory, settings: Settings
) -> None:
    """``identity_refusal`` **逐行**报，而汇总只数得出「几行能跑」。

    逐行报是必要的：整份派发是全拒绝（``subjects_for`` 逐字：跳过会让一份五行的
    清单编译出四个工作单元，而报告里那个 4 与「清单里本来就只有 4 个」看起来一样），
    所以「这一份清单能跑」与「这一行能跑」是两个问题。这一条把用法钉下来：
    先看汇总知道有几行要补，再逐行看是谁。
    """
    _write_accounts_list(settings)
    outcome = session_factory(("list_accounts", {"name": "known-accounts"}))
    payload = _ok(outcome)

    by_id = {row["account_id"]: row for row in payload["accounts"]}
    assert by_id["example-one"]["identity_refusal"] == ""  # 有域名
    assert by_id["example-three"]["identity_refusal"] == ""  # 有真名字
    refusal = by_id["example-two"]["identity_refusal"]
    assert refusal, "组织名那一格写的就是内部 id，这一行没有可用来做身份发现的依据"
    assert "example-two" in refusal, "逐行的理由要点名是哪一个主体"
    assert payload["summary"]["dispatchable"] == 2


def test_list_accounts_truncates_by_default_and_reports_it(
    session_factory, settings: Settings
) -> None:
    """缺省**封顶**，而封顶被报出来；``summary`` 与 ``account_count`` 仍覆盖整份清单。

    三件事一起钉：

    * 缺省不是「不封顶」—— 一份账号清单没有天然的大小，而不封顶的缺省会让一次调用
      返回一个由清单决定大小的响应。
    * 封了多少要报（``truncation``）—— 静默截断会让「池子有 51 行」与「池子有 50 行」
      长得一样。
    * 汇总**不跟着截断变**。跟着变的话，「池子有多大」就变成一个取决于调用参数的数。
    """
    count = 51  # 比缺省上限多一行 —— 恰好装满不算截断，所以这里要真的多出来
    _write_list(
        settings,
        "big-accounts",
        "accounts:\n"
        + "".join(
            f"  - account_id: example-{index:03d}\n"
            f"    organisation: Example {index} Ltd\n"
            "    cohort: known_success\n"
            for index in range(count)
        ),
    )
    outcome = session_factory(("list_accounts", {"name": "big-accounts"}))
    payload = _ok(outcome)

    truncation = payload["truncation"]
    assert truncation["truncated"] is True
    assert truncation["cap"] == 50
    assert truncation["kept"] == 50
    assert truncation["dropped"] == 1
    assert truncation["dropped_items"] == ["example-050"], (
        "丢的是**尾部**，顺序由文件顺序定死 —— 所以「砍掉的是哪几条」答得出来"
    )
    assert len(payload["accounts"]) == 50
    # 截断的是那一列，不是清单。
    assert payload["account_count"] == count
    assert payload["summary"]["by_cohort"] == {"known_success": count}


def test_list_accounts_can_be_asked_for_everything_on_purpose(
    session_factory, settings: Settings
) -> None:
    """``limit=None`` 是不封顶（``apply_cap`` 里 ``None`` 只有这一个意思）。

    与缺省封顶配成一对：两个方向都要有，否则「缺省封顶」与「实现不考虑清单大小」
    分不开 —— 后者在有人传 ``None`` 时会返回整个池子。
    """
    count = 51
    _write_list(
        settings,
        "big-accounts",
        "accounts:\n"
        + "".join(
            f"  - account_id: example-{index:03d}\n"
            f"    organisation: Example {index} Ltd\n"
            "    cohort: known_success\n"
            for index in range(count)
        ),
    )
    outcome = session_factory(("list_accounts", {"name": "big-accounts", "limit": None}))
    payload = _ok(outcome)

    assert len(payload["accounts"]) == count
    assert payload["truncation"]["cap"] is None
    assert payload["truncation"]["truncated"] is False


def test_a_negative_limit_comes_back_as_a_result_not_a_crash(
    session_factory, settings: Settings
) -> None:
    """负数上限是**调用方算错了**，所以它要作为一条结果回到调用方。

    ``apply_cap`` 对负数是失败关闭的（一个负数不是一个「很小的上限」）。让它从
    处理器里抛出去，服务面看到的是一次内部错误 —— 本仓「失败是一个结果而不是崩溃」
    的反面，而且那句话里连「上限」两个字都没有。
    """
    _write_accounts_list(settings)
    outcome = session_factory(("list_accounts", {"name": "known-accounts", "limit": -1}))
    text = _refused(outcome)
    assert "负数" in text
    assert "None" in text, "拒绝里要给出正确写法，而不只是说「不行」"


def test_a_list_that_is_not_there_is_refused_by_name(
    session_factory, settings: Settings
) -> None:
    """清单不存在是一条结果，且要说清去哪个目录找、按什么名字找。"""
    outcome = session_factory(("list_accounts", {"name": "no-such-list"}))
    text = _refused(outcome)
    assert "no-such-list" in text


# ----------------------------------------------------------------------
# 池：promote_receipt / list_assets
# ----------------------------------------------------------------------
#: 一份能折出**两行**场地资产的母池工件。与 ``_MOTHERPOOL_LINES`` 的差别只有一处，
#: 而那一处是这一节的全部理由：第 0 行多了一个 ``official_domain`` 列。
#: 「上游说的那个域名」要真的出现在池子里，才谈得上「四格域名分开」。
#:
#: 第 2 行**故意**是 pending（名称那一格写的就是标识）：折的是 ``resolved``，不是
#: 「工件里有的行」—— 「三行进去、两行出来」这件事，只有在有一行不该进去的时候
#: 才被考验到。
_ASSET_LINES: tuple[str, ...] = (
    '{"type": "way", "id": "101", "name": "Example Village Hall", '
    '"location": "Devon", "official_domain": "example-village.example"}',
    '{"type": "node", "id": "202", "name": "Example Playing Field", "location": "Devon"}',
    '{"type": "node", "id": "303", "name": "303", "location": "Devon"}',
)

#: 这份工件该读出来的五个去向。手写而不是从回执里推 —— 推出来的期望值与实现同步地错。
_ASSET_COUNTS: dict[str, int] = {
    "resolved": 2,
    "merged": 0,
    "excluded": 0,
    "pending": 1,
    "failed": 0,
}


def _many_asset_lines(count: int) -> tuple[str, ...]:
    """``count`` 块场地，一块一行，每一行的编号都不重复（所以每一行都是 resolved）。

    名字那一格**故意**填得跟编号不同：名称与标识相同会被判成 pending，
    那是 ``_ASSET_LINES`` 第 2 行在测的事，这里要的是能进池子的行。
    """
    return tuple(
        json.dumps(
            {
                "type": "node",
                "id": f"{index:03d}",
                "name": f"Example Venue {index:03d}",
                "location": "Devon",
            },
            ensure_ascii=False,
        )
        for index in range(1, count + 1)
    )


def _fold(
    session_factory,
    settings: Settings,
    lines: tuple[str, ...] = _ASSET_LINES,
    name: str = "motherpool",
) -> dict[str, Any]:
    """写一份工件 → 读成回执 → 折进池子，返回**折**那一步的读数。

    两步是两条工具，所以这里走两次调用：夹具不把它合成一条调用，因为「读一份工件
    不改变业务状态」与「折一次池子是一次读—改—写」正是这一节要分别看见的那两件事。
    折那一步收的是**回执 id**（不是工件名），所以要从读的结果里把它取出来。
    """
    _write_artifact(settings, name, lines)
    imported = _ok(session_factory(("import_motherpool", {"artifact_ref": name})))
    return _ok(
        session_factory(
            ("promote_receipt", {"receipt_id": imported["receipt"]["receipt_id"]})
        )
    )


def _not_wired_driver(settings: Settings, tmp_path: Path, *calls, missing: str):
    """一个**少了某个存储**的部署：组合根那一处漏传时的形状。

    ``missing`` 是**哪一个**没接。它的取值就是 :func:`product_loop_handlers` 上那几个
    可以为 ``None`` 的工厂名，而要说的不是同一句话：写一条关系时**两个都要有**（关系挂
    在一块已经在池子里的场地上，而那一行落在关系表里），所以少哪个决定它点谁的名字；
    账号池与成员名单则各自缺席 —— 账号行上不存场地，而一份冻结名单读的就是那个池子，
    所以它们少了谁都不牵连别人。

    除那一处外与 ``session_factory`` 逐字相同 —— 另起一份夹具会让人以为这两条路径在
    别处也该不同，而它们该在的差别只有那一处。
    """
    ring_path, token = _ring(tmp_path, "claude-control", [Role.CONTROLLER])
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        product_loop=product_loop_handlers(
            settings=settings,
            scenes=default_scene_store(settings),
            maintenance=default_maintenance_store(settings),
            assets=None if missing == "assets" else default_asset_store(settings),
            relations=None if missing == "relations" else default_relation_store(settings),
            accounts=None if missing == "accounts" else default_account_pool_store(settings),
            cohorts=None if missing == "cohorts" else default_account_cohort_store(settings),
            review=None,
            acquisition=None,
            router=None,
            documents=None,
        ),
        calls=calls,
        want_tool_list=False,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    return outcome


def test_promote_receipt_folds_the_resolved_rows_and_only_them(
    session_factory, settings: Settings
) -> None:
    """回执 → 池子：**一条 resolved 行一块场地**，另外四种去向一条都不折。

    三行的工件折完池子里是两行，而这个数只有把 pending 那一行排除掉才成立。所以
    断言里把它旁边的那个数也一并看着：``promotion.rows`` 是折进去的，``receipt.counts``
    是这一批读成了什么 —— 两个数并列出现，读的人才能一眼看出「另外那一行去了哪」。

    ## 这份工件折出的是 ``asset_only``，而那是对的

    它是**母池**（一行一座场地、带着来源编号），不是发现那一轮写下的池子 ——
    没有任何一次抓取挂在它身上，所以这一层拿不出证据，也就写不出账号
    （``MarketAccount`` 那条「没有证据的账号不落盘」）。场地照样折进去：场地是存在
    的，它只是还没被看过。发现那一条路径折出账号的样子在
    ``tests/test_mcp_discovery_chain.py`` 里测 —— 那一份的池子是**真的跑过一轮发现**
    写出来的，所以它有取回来的材料，也就折得出账号。
    """
    folded = _fold(session_factory, settings)

    assert folded["receipt"]["counts"] == _ASSET_COUNTS
    promotion = folded["promotion"]
    assert promotion["rows"] == 2, "折的是 resolved 的两行"
    assert promotion["handles"] == 2, "两行是两个句柄（没有同一块场地写了两次）"
    assert promotion["counts"] == {"promoted": 0, "asset_only": 2, "rejected": 0}, (
        "counts **恒有三格**：``{'asset_only': 2}`` 与 ``{'asset_only': 2, "
        "'promoted': 0, 'rejected': 0}`` 在读者那里是两件事，后者说得出"
        "「这一批一行都没被拒、也一行都没折成账号」"
    )
    assert (promotion["assets_before"], promotion["assets_after"]) == (0, 2)
    assert (promotion["accounts_before"], promotion["accounts_after"]) == (0, 0), (
        "母池的行没有发现期证据，所以账号池**一行都不写** —— 编一条证据去凑一个账号"
        "正是这一层唯一不能做的事"
    )
    for row in promotion["rows_detail"]:
        assert row["disposition"] == "asset_only"
        # 两种 ``asset_only`` 说的是两句不同的话，而这一份走的是「不是发现那一轮
        # 的形状」那一种：读的人据此知道要做的是去跑一轮发现，而不是去重抓。
        assert "不是发现那一轮" in row["reason"]
        assert row["evidence_count"] == 0
    assert folded["workspace"] == settings.profile
    # 不折的那四种要**逐一点名**：一份 40 行的回执只折进 12 行时，读的人要能立刻
    # 知道另外 28 行去了哪，而不是以为池子漏了行。
    for disposition in ("merged", "excluded", "pending", "failed"):
        assert disposition in folded["note"]

    listed = _ok(session_factory(("list_assets", {})))
    assert listed["pool_state"] == "present"
    assert listed["asset_count"] == 2
    assert listed["workspace"] == settings.profile

    by_name = {row["name"]: row for row in listed["assets"]}
    assert set(by_name) == {"Example Village Hall", "Example Playing Field"}
    hall = by_name["Example Village Hall"]
    assert hall["address"] == "Devon", "地点那一格是来源给的原文，本层不判断它的粗细"
    assert hall["source_identity"] == "way:101"
    assert hall["venue_asset_id"].startswith("src-"), (
        "句柄按来源编号派生（``src-<sha256[:16]>``）—— 按行号派生的话，同一块场地"
        "在两次导入里会拿到两个句柄，而池子里会长出第二行"
    )


def test_the_four_domain_cells_survive_the_fold_apart(
    session_factory, settings: Settings
) -> None:
    """上游说的那个域名进 ``claimed_by_source``，**不进** ``verified``。

    §4 那条「四格域名分开放」在这一层的落点。合成一格之后，「这个市场里有多少块
    场地已经有官网了」与「有多少块只有别人说的一句话」答成同一个数 —— 而后者恰好
    是核验那一步的**待办队列**。所以下面断言的既有「说了什么」，也有「判定没有
    跟着动」：一次转述不是一次核验。
    """
    _fold(session_factory, settings)
    listed = _ok(session_factory(("list_assets", {})))
    by_name = {row["name"]: row for row in listed["assets"]}
    hall = by_name["Example Village Hall"]
    field = by_name["Example Playing Field"]

    assert hall["domain"] == {
        "claimed_by_source": "example-village.example",
        "discovered_url": "",
        "candidate": "",
        "verified": "",
        "verdict": "unknown",
        "evidence_count": 0,
    }
    assert field["domain"]["claimed_by_source"] == "", "这一行的上游什么都没说"
    assert listed["summary"]["with_verified_domain"] == 0, (
        "上游说过一句话不等于我们证过 —— 判定仍是 unknown"
    )
    assert listed["summary"]["with_an_unchecked_upstream_claim"] == 1
    assert listed["summary"]["with_no_domain_lead_at_all"] == 1
    assert listed["summary"]["by_domain_verdict"] == {"unknown": 2}
    assert listed["summary"]["by_source_namespace"] == {"way": 1, "node": 1}
    assert "上游说的" in listed["note"], "四格各自是什么，回执里要说出来"


def test_a_fold_is_not_a_recheck(session_factory, settings: Settings) -> None:
    """折完 ``last_verified_at`` 仍是空的、证据仍是 0 —— **读一份回执不是重新看过一眼**。

    填上它，「这块场地多久没复核了」这个只在池子建起来之后才有意义的问句，会在第一次
    导入那天就得到「刚刚复核过」，而下一轮维护会把它们整批跳过整整一个周期。与
    ``application/refresh.py`` 那边「**重读一份关于对象的记录**不算重新核过」是同一条
    纪律，也是同一个陷阱：一个**几乎**正确的值，正好落在将来那次真实测量的位置上。

    （``run_maintenance`` 现在**会**写 ``Observation``，所以这条纪律的落点变了，而它
    本身没有变 —— 判据是**这一面的主语是不是那份本地记录**，不是「本地读一律不算数」。
    导入一份回执读的是**那份回执**，它不是说「这块场地被重新看过一眼」；而
    ``SOURCE_COVERAGE`` 的主语就是本地那份证据记录，数一遍就是一次满覆盖测量。两边
    各有自己的用例，见 ``test_a_tick_records_one_observation_per_claimed_cell``。）
    """
    _fold(session_factory, settings)
    listed = _ok(session_factory(("list_assets", {})))

    for row in listed["assets"]:
        assert row["last_verified_at"] is None
        assert row["evidence_count"] == 0
        assert row["domain"]["evidence_count"] == 0
        assert row["first_seen_at"].endswith("+00:00"), (
            "只有「何时第一次记下」那一个时刻被填上 —— 两个时刻一起填，会让"
            "「刚折进来」与「刚复核过」长得一样"
        )
    assert listed["summary"]["never_re_checked"] == listed["asset_count"]


def test_folding_the_same_receipt_twice_adds_no_row_and_moves_no_timestamp(
    session_factory, settings: Settings
) -> None:
    """折两次 = 折一次。第二次的 ``rows_before`` 就是第一次的 ``rows_after``。

    幂等在这里不靠「先查一次折过没有」：句柄由来源编号派生（同一块场地两次拿到同一个
    句柄），而合并规则对 ``first_seen_at`` 只往早走。一条靠前置查询的实现，在并发下
    读到的那份答案可能已经不是真的（``ports/pool.py`` 逐字写了这件事）—— 而这条用例
    在单线程里对两种实现都是绿的，所以它钉的是**结果**，不是手段。
    """
    folded = _fold(session_factory, settings)
    receipt_id = folded["receipt"]["receipt_id"]

    first = session_factory(
        ("promote_receipt", {"receipt_id": receipt_id}), ("list_assets", {})
    )
    _ok(first, 0)
    before = _ok(first, 1)

    second = session_factory(
        ("promote_receipt", {"receipt_id": receipt_id}), ("list_assets", {})
    )
    refolded = _ok(second, 0)
    after = _ok(second, 1)

    assert refolded["promotion"]["rows"] == 2, "第二次折进去的还是那两行"
    assert refolded["promotion"]["handles"] == 2
    assert (refolded["promotion"]["assets_before"], refolded["promotion"]["assets_after"]) == (
        2,
        2,
    ), "池子没有长"
    assert (
        refolded["promotion"]["accounts_before"],
        refolded["promotion"]["accounts_after"],
    ) == (0, 0), "账号池两次都是空的（这份工件没有发现期证据）"
    assert before["asset_count"] == after["asset_count"] == 2
    assert [row["first_seen_at"] for row in after["assets"]] == [
        row["first_seen_at"] for row in before["assets"]
    ], "重放把 first_seen_at 推后了 —— 那个时刻是「我们何时第一次记下这块场地」"


def test_promote_receipt_refuses_a_receipt_that_is_not_on_disk(
    session_factory, settings: Settings
) -> None:
    """折一份盘上没有的回执是一条结果，且要点名是哪一个。

    「回执不在盘上」与「回执里的行折不进去」是两件事，处置也不同：前者是把
    ``import_motherpool`` 那一步跑一遍，后者是看那份回执自己说了什么。所以这一句
    里必须有名字，且池子那边**什么都没发生**。
    """
    text = _refused(
        session_factory(
            ("promote_receipt", {"receipt_id": "receipt-0123456789abcdef"})
        )
    )
    assert "receipt-0123456789abcdef" in text
    assert not settings.database_path.is_file(), (
        "一次找不到回执的调用建出了库 —— 读回执必须在动存储之前"
    )


def test_promote_receipt_refuses_a_receipt_that_was_edited_on_disk(
    session_factory, settings: Settings
) -> None:
    """**折的是盘上那一份**：手工改过的回执被拒，而不是被当成权威折进去。

    这条判据证的正是「回执是从盘上读回来」这件事。一个拿调用方手里那几行来折的实现
    在这条用例里依然全绿 —— 它没有读盘这个动作，也就碰不上这个守卫；而「读进去的」
    与「折进去的」可能来自两份不同的东西，正是这条要挡的。
    """
    folded = _fold(session_factory, settings)
    receipt_id = folded["receipt"]["receipt_id"]

    path = receipt_path(settings.inputs_dir, receipt_id)
    body = json.loads(path.read_text(encoding="utf-8"))
    body["dispositions"][0]["resolved_name"] = "改过的名字"
    path.write_text(
        json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )

    text = _refused(session_factory(("promote_receipt", {"receipt_id": receipt_id})))
    assert receipt_id in text

    listed = _ok(session_factory(("list_assets", {})))
    assert listed["asset_count"] == 2, (
        "被拒的那一次还是折进去了 —— 改过的名字会取到**另一个句柄**（句柄由名字与"
        "地点派生），于是池子里多出第三行，而报告上看起来只是「多了块场地」"
    )
    assert "改过的名字" not in {row["name"] for row in listed["assets"]}


@pytest.mark.parametrize(
    "error", [StoreError, AssetConflictError], ids=["StoreError", "AssetConflictError"]
)
def test_a_store_refusal_at_the_fold_comes_back_as_a_result(
    session_factory, settings: Settings, monkeypatch, error: type[MarketOpsError]
) -> None:
    """存储层说「不」时，折的那一条要把它**说出来**，而不是让它逃出去。

    两种错都要测，因为它们是**兄弟**（``domain/errors.py``）：``StoreError`` 是
    「库里已经有一样东西，而它不是你要写的那份」，``AssetConflictError`` 是「两边
    对这块场地有没有官网下的判定说不到一块儿」。处置不同，但两条都是调用方要据它改
    东西的结局 —— 逃出去之后读到的是一句光秃秃的
    ``Error executing tool promote_receipt``，存储层原本那句话整个丢掉。

    ``AssetConflictError`` 这一支今天**到不了**（本函数折进去的行都带
    ``domain_verdict=unknown``，合并规则对 unknown 是不动的），所以这里必须用桩
    把它造出来。留着它的理由是那条规则不止这一条路径会用到（同一个库上的第二个写者），
    而一条在具体实现里到不了的 except 一旦错了，没有人会发现。
    """
    from marketops.adapters.sqlite.pool import SqliteVenueAssetStore

    folded = _fold(session_factory, settings)
    receipt_id = folded["receipt"]["receipt_id"]

    seen = {"n": 0}

    def refuse(self, incoming, *, fold=None):
        # ``fold`` 那一格必须收下：``upsert_asset`` 的签名在 §B17 条件 2 之后多了一个
        # 只关键字参数（``application/promotion.py`` 逐行传 ``fold=``）。不写它，
        # 这个桩会在**签名**上就炸，而这一条判据要看的是存储层那句话有没有到达调用方
        # —— 一个 TypeError 会让这条判据在一条与它无关的路径上变红。
        seen["n"] += 1
        raise error("存储层的一句话：这一行不覆盖。")

    monkeypatch.setattr(SqliteVenueAssetStore, "upsert_asset", refuse)
    outcome = session_factory(("promote_receipt", {"receipt_id": receipt_id}))

    assert seen["n"] == 1, "桩只该被走到一次 —— 第一行就拒绝了，后面那些行没轮到"
    text = _refused(outcome)
    assert "存储层的一句话" in text, (
        "存储层说的那句话没有到达调用方 —— 它被折成了一句读不出原因的失败"
    )
    assert text.strip() != "Error executing tool promote_receipt", (
        "回执里只有工具名、没有理由 —— 这正是修复前那条路径的形状"
    )


def test_promote_receipt_says_not_wired_rather_than_nothing_to_fold(
    settings: Settings, tmp_path: Path
) -> None:
    """``assets=None`` 时是**接线事实**，不是「这份回执里没有可折的行」。

    两者指向的下一件事不同：一个去接线，一个去看回执的 counts。答成后者的话，一个
    忘了装配组合根的部署看起来像一个跑过而颗粒无收的部署。
    """
    text = _refused(
        _not_wired_driver(
            settings,
            tmp_path,
            ("promote_receipt", {"receipt_id": "receipt-0123456789abcdef"}),
            missing="assets",
        )
    )
    assert "接线" in text
    assert "不是「这份回执里没有" in text


def test_list_assets_says_no_store_and_does_not_create_one(
    session_factory, settings: Settings
) -> None:
    """库不存在时是 ``no_store``，``summary`` 是 ``None``，而**读一次不建库**。

    ``no_store`` 与「这个市场一块场地都没有」是两件事：抽掉这一格，一个刚起出来的
    部署会得到一个「池子是空的」，而它的处置是去折一份回执，不是去查为什么没有场地。
    ``summary`` 给 ``None`` 而不是一份全 0 的汇总 —— 后者会把「还没建库」写成一份
    测量结果。
    """
    assert not settings.database_path.is_file()
    body = _ok(session_factory(("list_assets", {})))

    assert body["pool_state"] == "no_store"
    assert body["assets"] == [] and body["asset_count"] == 0
    assert body["summary"] is None
    assert not settings.database_path.is_file(), "读一次建出一个空库 —— 那不是一次读"


def test_list_assets_says_not_wired_rather_than_empty(
    settings: Settings, tmp_path: Path
) -> None:
    """``assets=None`` 时是 ``not_wired``，而它与 ``no_store`` 是两句不同的话。

    两者都在 ``assets`` 那一格给空列表，所以那一格分不开它们；分开它们的是
    ``pool_state``。这一条与上一条配成一对：只有一条的话，「没接存储」与「还没写过
    东西」的差别没被证明是真的差别。
    """
    body = _ok(_not_wired_driver(settings, tmp_path, ("list_assets", {}), missing="assets"))

    assert body["pool_state"] == "not_wired"
    assert body["pool_state"] != "no_store"
    assert body["summary"] is None
    assert "接线" in body["note"] and "不是「一块场地都没有」" in body["note"]


def test_list_assets_truncates_by_default_and_reports_it(
    session_factory, settings: Settings
) -> None:
    """缺省**封顶**、封了多少报出来、汇总**不跟着截断变**。

    三条一起钉，理由与 ``list_accounts`` 逐字相同：缺省不封顶会让一次调用的响应大小
    由池子决定；静默截断会让 101 行的池子与 100 行的池子长得一样；跟着截断变的汇总会
    把「池子有多大」变成一个取决于调用参数的数。
    """
    folded = _fold(session_factory, settings, _many_asset_lines(101), "many-venues")
    assert folded["promotion"]["handles"] == 101

    capped = _ok(session_factory(("list_assets", {})))
    assert capped["truncation"]["cap"] == 100
    assert capped["truncation"]["kept"] == 100
    assert capped["truncation"]["dropped"] == 1
    assert len(capped["assets"]) == 100
    dropped = capped["truncation"]["dropped_items"]
    assert len(dropped) == 1
    assert dropped[0] not in {row["venue_asset_id"] for row in capped["assets"]}, (
        "丢的是**尾部**，顺序由 ``venue_asset_id`` 定死 —— 所以「砍掉的是哪一块」"
        "答得出来"
    )
    # 截断的是那一列，不是池子。
    assert capped["asset_count"] == 101
    assert capped["summary"]["never_re_checked"] == 101

    everything = _ok(session_factory(("list_assets", {"limit": None})))
    assert len(everything["assets"]) == 101
    assert everything["truncation"]["cap"] is None
    assert everything["truncation"]["truncated"] is False
    assert everything["truncation"]["dropped"] == 0


@pytest.mark.parametrize("pool", ["written", "no_store", "not_wired"])
def test_a_negative_limit_in_the_asset_pool_comes_back_as_a_result(
    session_factory, settings: Settings, tmp_path: Path, pool: str
) -> None:
    """负数上限是**调用方算错了**，所以在池子的三种状态下它都作为一条结果回来。

    ``apply_cap`` 对负数是失败关闭的（一个负数不是一个「很小的上限」）。三条状态都要
    走一遍，因为那个拒绝在实现里是**一处**、而要经过三处分支才到得了：只测「池子里有
    行」那一条的话，另外两条分支上的拒绝会从处理器里逃出去成一次内部错误 ——
    那句话里连「上限」两个字都没有。空池子上的负数同样是一次算错，不因为它没有行
    而侥幸通过。
    """
    if pool == "not_wired":
        outcome = _not_wired_driver(
            settings, tmp_path, ("list_assets", {"limit": -1}), missing="assets"
        )
    else:
        if pool == "written":
            _fold(session_factory, settings)
        outcome = session_factory(("list_assets", {"limit": -1}))

    text = _refused(outcome)
    assert "负数" in text
    assert "None" in text, "拒绝里要给出正确写法，而不只是说「不行」"


@pytest.mark.parametrize(
    ("identity", "namespace"),
    [
        ("way:101", "way"),
        ("node:202", "node"),
        ("site:example-village.example", "site"),
        ("", "(none)"),
        (":101", "(none)"),
    ],
)
def test_a_row_without_a_source_number_gets_its_own_group(
    identity: str, namespace: str
) -> None:
    """汇总按**命名空间**分组，而没有来源编号的行合成一格 ``(none)``。

    ``(none)`` 是一个**值**而不是缺席：只按名字与地点取句柄的那些行合成一格，是为了让
    「这个池子里有几行认不出出处」有一个数 —— 空键 ``""`` 在报告里看起来像排版。
    ``:101`` 那一格是这条判据的另一半：有冒号而没有前半截，与没有冒号一样不算命名
    空间（一个空的前缀不是一个来源）。
    """
    assert _source_namespace(identity) == namespace


# ----------------------------------------------------------------------
# 增量维护：get_maintenance_plan / run_maintenance / get_ops_report
# ----------------------------------------------------------------------
#: 一次维护 tick 的完整顺序：立项并生效 → 读计划 → 跑一轮 → 读经营报告。
#: ``name`` 是那份账号清单的名字；维护层的主体来自它，与 ``list_accounts``
#: 读的是同一个文件（``what_is_due`` 自己发现不了主体，也不该发现）。
_MAINTENANCE_SEQUENCE: tuple[tuple[str, dict[str, Any]], ...] = (
    ("create_scene", {**BASE_CREATE, "activate": True}),
    ("get_maintenance_plan", {"name": "known-accounts"}),
    ("run_maintenance", {"name": "known-accounts"}),
    ("get_ops_report", {"name": "known-accounts"}),
)


def test_the_maintenance_tool_hands_the_refresher_a_mirror_writer(
    session_factory, settings: Settings, monkeypatch
) -> None:
    """服务面这一侧也把「落第二份字节」的能力接进了复核回调。

    与 ``tests/test_resident_controller.py`` 里那一条同一条判据、另一个入口：这一仓有
    **两个**生产调用方建复核回调（常驻控制器与这一条工具），而「漏接一样东西」在两边
    的形态都是静默的 —— ``copy_page=None`` 的含义是「这个部署落不下第二份」，于是
    同一个工作区里两个主体抓到同一个 URL 时各付一次取回的费用，报告上只显示「取回
    次数变多了」。一个入口接上、另一个漏掉时，两边的报告都自洽，只有把两份放在一起
    才看得出差别 —— 而没有人会把两份报告放在一起。

    落点断言与控制器那一条相同，理由也相同：绑到了别处 ``Settings`` 的复制方过得了
    「传进去没有」，过不了「落点在本部署的镜像根下」。
    """
    seen: dict = {}

    def recorder(**kwargs):
        seen.update(kwargs)

        def boom(_due):  # pragma: no cover —— 它被调用就是这一条判据要测的那一格
            raise RuntimeError("接线已经记下了，这一趟不必真的跑")

        return boom

    monkeypatch.setattr(refresh_use_cases, "build_refresher", recorder)
    _write_accounts_list(settings)

    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
    )

    assert _ok(outcome, 0)["scene"]["state"] == "active", (
        "先确认这一趟真的走到建回调那一步，而不是在前一步就被拒了"
    )

    copier = seen.get("copy_page")
    assert callable(copier), (
        "服务面这一趟没有把镜像写入能力接进复核回调 —— 缺省那一支是「本部署落不下"
        "第二份」，于是两个主体抓同一个 URL 会各付一次，而账上只会显示「取回次数变多了」"
    )

    url = "https://example.co.uk/contact"
    body = b"<html><body>grounds@example.co.uk</body></html>"
    source = page_path(settings, scope="acct-a", url=url, extension=".html")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(body)

    landed = copier(source, "acct-b", url)

    assert landed == page_path(settings, scope="acct-b", url=url, extension=".html")
    assert landed.read_bytes() == body


def test_the_maintenance_tick_walks_from_a_plan_to_a_receipt(session_factory, settings: Settings) -> None:
    """计划 → 跑一轮 → 报告，三个工具各自答一段，中间没有断裂。

    断言的是**三段连起来才对得上的那几个数**：计划里说有几格到期、tick 做成几格、
    报告里读到几格回执。三个数各测各的时，任何一个都可能因为「过滤位置挪了一格」
    而与另外两个分家，而三份报告各自看起来都自洽。
    """
    _write_accounts_list(settings)
    outcome = session_factory(*_MAINTENANCE_SEQUENCE)

    created = _ok(outcome, 0)
    assert created["scene"]["state"] == "active"

    plan = _ok(outcome, 1)
    assert plan["ticking"] is True
    assert plan["subjects"]["count"] == 3, "三个账号，与 list_accounts 读到的是同一份"
    assert plan["refreshable_aspects"] == [
        "contact_person_role",
        "email_evidence",
        "source_coverage",
    ]

    # 三个面在本部署的范围内：3 个主体 × 3 面 = 9 格。其中两个页面面在**这一份**
    # 部署里没接抓取（``session_factory`` 传的三样是 ``None``）—— 而那**不是**
    # 「不在范围内」：它们照样到期、照样被认领，然后如实报出「缺哪几样接线」。
    # 两者的分别是这一片的一个承重结论（§11：未接的面报 coverage gap，不能靠
    # out_of_scope 把它从分母里删掉）。
    assert [row["aspect"] for row in plan["due"]].count("source_coverage") == 3
    assert [row["aspect"] for row in plan["due"]].count("contact_person_role") == 3
    assert [row["aspect"] for row in plan["due"]].count("email_evidence") == 3
    assert plan["due_count"] == 9
    # 另外四个面**逐面报出来** —— 它们不是「到期了但没做」，是「本部署做不了」。
    assert plan["out_of_scope_by_aspect"] == [
        ["asset_identity", 3],
        ["asset_attribute", 3],
        ["account_relation", 3],
        ["product_fit", 3],
    ]
    assert all(rule["calibrated"] is False for rule in plan["refresh"]["rules"])
    assert plan["refresh"]["uncalibrated_aspects"] == [
        "source_coverage",
        "asset_identity",
        "asset_attribute",
        "account_relation",
        "contact_person_role",
        "email_evidence",
        "product_fit",
    ]
    # 承诺的七个面 / 本部署答得出的三个 / 没接的四个 —— 三个数一起给。
    assert plan["coverage"]["required_count"] == 7
    assert plan["coverage"]["implemented_aspects"] == [
        "contact_person_role",
        "email_evidence",
        "source_coverage",
    ]
    assert plan["coverage"]["coverage_gaps"] == [
        "asset_identity",
        "asset_attribute",
        "account_relation",
        "product_fit",
    ]
    assert plan["coverage"]["missing_wiring"] == ["acquisition", "router", "documents"]

    tick = _ok(outcome, 2)
    assert tick["claimed_count"] == 9
    assert len(tick["done"]) == 3
    assert tick["out_of_scope_count"] == 12, "四个面 × 三个主体，一格都没被认领"
    for claim in tick["done"]:
        assert claim["state"] == "done"
        assert claim["outcome"]["readable"] is True
        assert claim["outcome"]["kind"] == "census"
        assert claim["outcome"]["store_present"] is True, (
            "走到这里库一定在了（立项就建了它）—— 所以这一格读的是**一个空的库**，"
            "「库不存在」那一支在 tests/test_application_refresh.py 里单测"
        )
        assert claim["outcome"]["rows"] == 0
        assert claim["outcome"]["new_source_gaps"] == 1

    # 页面那六格**做不成**：这三个账号名下一条证据都没有，所以「这一格该看哪几页」
    # 是空集 —— 空集在页面复核里是**不可达**，不是「页面没变」（后者会给整格续期，
    # 而这里根本没有人看过任何东西）。它照样被认领、照样留下一条说得出理由的失败，
    # 并且**不写观测**（下一轮会再领一次）。
    #
    # 「缺接线」那条路要走**另一个**部署形状才到得了：它排在「有没有页面可看」之后，
    # 所以只有名下有页面的主体才会走到那里（见本文件最后那两条 §B22 用例）。
    assert len(tick["failed"]) == 6
    for claim in tick["failed"]:
        assert claim["aspect"] in ("contact_person_role", "email_evidence")
        assert claim["state"] == "failed"
        assert claim["outcome"]["readable"] is True
        assert claim["outcome"]["kind"] == "recheck"
        assert claim["outcome"]["state"] == "unreachable"
        assert claim["outcome"]["note"] == "这一格名下没有可复核的页面。"
    assert [row["aspect"] for row in tick["observations"]] == ["source_coverage"] * 3

    report = _ok(outcome, 3)
    assert report["ticks"]["claims"] == 9
    assert report["ticks"]["by_state"] == {"done": 3, "failed": 6}
    assert report["ticks"]["done_by_aspect"] == {"source_coverage": 3}
    # 来源供给那一格只数**数本地记录**的那个面：一个页面面回答不了「手上有没有
    # 可绑定的来源」，而把两种记录混着数会让分母悄悄变成两倍。
    assert report["source_supply"]["aspects"] == ["source_coverage"]
    assert report["source_supply"]["subjects_with_bindable_source"] == 0
    assert len(report["source_supply"]["subjects_without_any_bindable_source"]) == 3
    assert report["source_supply"]["subjects_never_ticked"] == []
    # 页面复核按状态单独数一遍：六格全在 unreachable 上，而**零个的照列**。
    assert report["page_rechecks"]["aspects"] == [
        "contact_person_role",
        "email_evidence",
    ]
    assert report["page_rechecks"]["by_state"] == [
        ["reused", 0],
        ["unchanged", 0],
        ["changed", 0],
        ["unreachable", 6],
        ["unreadable", 0],
    ]
    assert report["coverage"]["missing_wiring"] == ["acquisition", "router", "documents"]


def test_the_plan_says_which_aspects_this_deployment_will_not_re_check(
    session_factory, settings: Settings
) -> None:
    """``refresh.rules`` 写着七个面的节奏，而 ``refreshable_aspects`` 只有三个。

    两句都要在。只给节奏的话，读的人会以为七个面这一轮都会被动；只给范围的话，
    「多久看一次」这条规则就无处可查 —— 而它是要走改版留痕的那一格。

    而「三个」还要再分一次，因为第三种情形在这一片里第一次出现：**代码里有这一面
    的复核，而这份部署没接它要的接线**。它与「这一面根本没写」在报告上必须分得开
    —— 前者是 ``missing_wiring``（去接上），后者是 ``coverage_gaps``（去写）。
    把两者合成一句「本部署做不了」，会让一个忘了接抓取器的组合根看起来像一份
    还没实现的功能。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("get_maintenance_plan", {"name": "known-accounts"}),
    )
    plan = _ok(outcome, 1)

    cadences = {rule["aspect"]: rule["after_days"] for rule in plan["refresh"]["rules"]}
    assert set(cadences) == {
        "source_coverage",
        "asset_identity",
        "asset_attribute",
        "account_relation",
        "contact_person_role",
        "email_evidence",
        "product_fit",
    }, "七个面每个恰好一条规则（RefreshPlan 的判据），与复核面是两件事"
    assert plan["refreshable_aspects"] == [
        "contact_person_role",
        "email_evidence",
        "source_coverage",
    ]
    coverage = plan["coverage"]
    assert coverage["required_aspects"] == [
        "source_coverage",
        "asset_identity",
        "asset_attribute",
        "account_relation",
        "contact_person_role",
        "email_evidence",
        "product_fit",
    ], "分母是**承诺要维护的七个面**，按 ALL_ASPECTS 的次序，不随实现变"
    assert coverage["implemented_aspects"] == plan["refreshable_aspects"]
    assert coverage["census_backed_aspects"] == ["source_coverage"]
    assert coverage["page_backed_aspects"] == ["contact_person_role", "email_evidence"]
    assert coverage["coverage_gaps"] == [
        "asset_identity",
        "asset_attribute",
        "account_relation",
        "product_fit",
    ]
    assert coverage["coverage_gap_count"] == 4
    # 这一份部署三样接线一样都没接，逐样点名并给出各自的意思。
    assert coverage["missing_wiring"] == ["acquisition", "router", "documents"]
    assert coverage["wiring_meaning"]["acquisition"]
    # ``plan`` **不在** missing_wiring 里：它不是部署的性质，是某一版 Scene 的一格。
    assert "plan" not in coverage["missing_wiring"]
    assert coverage["plan_present"] is True
    assert "本部署" in plan["note"] and "refreshable" not in plan["note"].lower()


def test_a_draft_scene_has_a_record_but_no_cadence(
    session_factory, settings: Settings
) -> None:
    """立项但没 activate：记录在、生效版本**不在** —— 那不是错误，是「还没开张」。

    与「场景不存在」是两句话：前者要说「去激活第 1 版」，后者要说「先立项」。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", BASE_CREATE),  # 不 activate
        ("get_maintenance_plan", {"name": "known-accounts"}),
        ("run_maintenance", {"name": "known-accounts"}),
    )

    plan = _ok(outcome, 1)
    assert plan["state"] == "draft"
    assert plan["ticking"] is False
    assert plan["effective_version"] is None
    assert plan["refresh"] is None, "没有生效版本就没有节奏可读 —— 不是空规则表"
    assert plan["due"] == []
    assert plan["due_count"] == 0

    tick = _ok(outcome, 2)
    assert tick["ticking"] is False, "没生效就没有节奏，没有节奏就没有该复核的格子"
    assert tick["claimed_count"] == 0
    assert tick["not_ticking"], "要说出**为什么**这一轮没跑，不能只回一个 0 件"


def test_a_paused_scene_still_reports_its_plan_but_ticks_nothing(
    session_factory, settings: Settings
) -> None:
    """§4：暂停停的是**新的派发**。计划照给（恢复后要补哪些格子答得出来），
    但这一轮一格都不领。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("control_scene", {"action": "pause", "reason": "operator_pause"}),
        ("get_maintenance_plan", {"name": "known-accounts"}),
        ("run_maintenance", {"name": "known-accounts"}),
    )

    plan = _ok(outcome, 2)
    assert plan["ticking"] is False
    assert plan["due"] == [], "暂停的场景不派发新工作"
    assert plan["refresh"] is not None, "节奏照旧给出来 —— 恢复时要知道按什么补"
    assert plan["out_of_scope_by_aspect"] == [], "没派发就没有范围外的格子"

    tick = _ok(outcome, 3)
    assert tick["ticking"] is False
    assert tick["not_ticking"] and tick["not_ticking"][0]["reason"], (
        "跳过一格必须带一句理由 —— 「这一轮 0 件」与「被状态挡住了」是两件事"
    )
    assert tick["claimed_count"] == 0


def test_a_scene_that_was_never_created_is_refused_with_the_next_step(
    session_factory, settings: Settings
) -> None:
    """维护的节奏写在某一版 Scene 上 —— 没有那个场景时没有可复核的东西。"""
    _write_accounts_list(settings)
    outcome = session_factory(("get_maintenance_plan", {"name": "known-accounts"}))

    text = _refused(outcome)
    assert "create_scene" in text, "拒绝里要给出下一步，而不只是说「不行」"


def test_a_tick_records_one_observation_per_claimed_cell(
    session_factory, settings: Settings
) -> None:
    """一轮复核**真的记下**了观测：逐格一条，且主语就是那一格。

    这是 §6 要的那条生产路径，而它是 ``record_observation`` 在全仓唯一的生产
    调用点（``application/maintenance.run_once``）。回执里专门有一格 ``observations``
    —— 它与 ``done`` 数的**不是同一件事**：``done`` 是「这一格做过了」，而回调可以
    跑完却不声明自己看到了什么（那时 ``observations`` 是空的）。

    这一片里两者的差第一次**真的出现**：九格里三格做成了（数本地记录），另外六格
    是页面面、没看成（这三个账号名下一条证据都没有），于是 ``done`` 与
    ``observations`` 都只有三格 —— 而它们不是「同一件事的两种写法」，
    是**这一趟真的看过什么**恰好与做成的那些重合。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
    )

    first = _ok(outcome, 1)
    assert first["claimed_count"] == 9
    assert len(first["done"]) == 3
    assert len(first["failed"]) == 6, "页面那六格没看成：这一趟没有可复核的页面"
    assert len(first["observations"]) == 3, (
        "三格都声明了这一趟真的看到了那一面（来源覆盖的主语就是本地那份记录），"
        "所以三行观测都要落下来"
    )
    assert {row["subject_id"] for row in first["observations"]} == {
        "example-one",
        "example-two",
        "example-three",
    }
    assert {row["aspect"] for row in first["observations"]} == {"source_coverage"}
    assert all(row["source_ref"] for row in first["observations"]), (
        "观测要指向这一次真的看的东西（result_ref 那一份盘点）—— 没有指东西的"
        "观测在报告里与「有人点了按钮」同形"
    )


def test_a_second_tick_after_the_observations_does_no_work(
    session_factory, settings: Settings
) -> None:
    """第二次 tick 里，**做过的那三格不再排，没做成的那六格再领一次** —— 两件事。

    这一条是上一版判据的**加强版**，而两者说的不是同一件事。旧版依赖
    「从来不写观测 ⇒ ``period`` 恒为 ``"never"`` ⇒ 第二次的 ``dedupe_key`` 与
    第一次相同 ⇒ 存储回 ``won=False``」——它证的是「账上不会多第二行」。

    现在的局面是三格记下了观测、六格没有（页面面没看成）。于是第二次 tick 里：

    * ``source_coverage`` 那三格**连排都不排** —— 有了基线（``observed_at``），
      ``next_due_at`` 落在 ``after_days`` 之后，``due_count`` 里没有它们。
    * 页面那六格**再领一次**（``attempt`` 从 1 变 2）—— 存储层的判据是「只有
      ``FAILED`` 产生下一条」，而它们从来没有过观测。**一次没做成的复核不该
      因此变成「已经做过了」**：那正是这一片要挡的那种话。

    「没到该看它的时候」与「上一趟没做成、这一趟再来」是**两句话**，回执里
    那几个数因此必须分得开 —— 把它们读成一件，会让「一轮失败」看起来像「收敛」。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
        ("run_maintenance", {"name": "known-accounts"}),
    )

    first = _ok(outcome, 1)
    second = _ok(outcome, 2)
    assert first["claimed_count"] == 9
    assert {row["attempt"] for row in first["failed"]} == {1}
    assert second["due_count"] == 6, (
        "这三格（来源覆盖）已经记下观测，要到 after_days 之后才再到期；"
        "另外六格（页面）从来没记下过观测，所以下一次 tick 照旧排在计划上"
    )
    assert second["claimed_count"] == 6, "上一趟没做成，这一趟再领一次"
    assert {row["attempt"] for row in second["failed"]} == {2}
    assert {row["aspect"] for row in second["failed"]} == {
        "contact_person_role",
        "email_evidence",
    }
    assert second["done"] == []
    assert second["not_claimed"] == []
    assert second["observations"] == []


def test_a_subject_with_a_fetched_page_stops_being_a_gap(
    session_factory, settings: Settings
) -> None:
    """给一个主体落一条抓下来的页面：它从「没有任何可绑定来源」那一栏里出去。

    两个数**都要看**：``subjects_with_bindable_source`` 从 0 变成 1，而
    ``subjects_without_any_bindable_source`` 从 3 变成 2。只看第一格时，一个
    把两边都数错的实现照样绿。
    """
    from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore

    _write_accounts_list(settings)
    store = SqliteEvidenceStore(settings.database_path)
    store.put_evidence(
        Evidence(
            # 血统必填。这一行是**这个部署**抓下来的，而这一条路径上「这个部署」
            # 就是配置里的那个工作区（场景的 ``workspace`` 也取自它）。
            workspace=settings.profile,
            evidence_id="e1",
            account_id="example-one",
            source_url="https://example-one.co.uk/about",
            source_kind=SourceKind.FETCHED_HTML,
            literal_value="Grounds Manager",
            content_sha256="sha-one",
            fetched_at=datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc),
            field=ContactField.EMAIL,
        )
    )

    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
        ("get_ops_report", {"name": "known-accounts"}),
    )

    tick = _ok(outcome, 1)
    by_subject = {claim["subject_id"]: claim["outcome"] for claim in tick["done"]}
    assert by_subject["example-one"]["new_source_gaps"] == 0
    assert by_subject["example-one"]["bindable_kinds"] == ["fetched_html"]
    assert by_subject["example-two"]["new_source_gaps"] == 1, "另外两个主体不受影响"

    report = _ok(outcome, 2)
    assert report["source_supply"]["subjects_with_bindable_source"] == 1
    # 名单按 (subject_kind, subject_id) 排序 —— 是**名字**的次序，不是文件里
    # 那三行的次序。报告的这一栏是给人照着去处理的，所以它得是稳定的次序，
    # 而不是「碰巧与这次调用的输入同序」。
    assert [
        row["subject_id"]
        for row in report["source_supply"]["subjects_without_any_bindable_source"]
    ] == ["example-three", "example-two"]


def test_the_deltas_never_report_zero_new_venues(session_factory, settings: Settings) -> None:
    """``new_venues`` 是 ``null`` 带一句理由，**不是 0**。

    ``0`` 读作「这一轮找过新场地、一个都没有」，而这一层根本没有联网找过。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
    )
    tick = _ok(outcome, 1)

    assert len(tick["source_deltas"]) == 3
    for delta in tick["source_deltas"]:
        assert delta["new_venues"] is None
        assert delta["unmeasured"] == ["new_venues"]
        assert delta["measured"] is False
    assert "null" in tick["note"] and "未测" in tick["note"]


def test_an_unreadable_receipt_is_not_counted_as_a_good_news(
    session_factory, settings: Settings
) -> None:
    """把一格回执指向的盘点从盘上删掉：它进 ``subjects_unreadable``。

    既不算「有来源」也不算「没来源」—— 那一格的产出无法复核。写成 0 个缺口就是
    把一次损坏读成一次好消息。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
    )
    tick = _ok(outcome, 1)
    first = tick["done"][0]
    assert first["outcome"]["readable"] is True, "先确认这一格本来是读得出来的"

    Path(first["result_ref"]).unlink()

    after = session_factory(("get_ops_report", {"name": "known-accounts"}))
    report = _ok(after)

    assert len(report["source_supply"]["subjects_unreadable"]) == 1
    unreadable = report["source_supply"]["subjects_unreadable"][0]
    assert unreadable["subject_id"] == first["subject_id"]
    assert unreadable["outcome"]["readable"] is False
    assert "0 个缺口" in unreadable["outcome"]["reason"]
    # 读不出来的那一格**不**落进另两栏 —— 两边都不是它该去的地方。
    assert report["source_supply"]["subjects_with_bindable_source"] == 0
    assert len(report["source_supply"]["subjects_without_any_bindable_source"]) == 2


def test_the_report_writes_nothing(session_factory, settings: Settings) -> None:
    """报告**不做任何写操作**：跑一次之后账上的回执数与盘上的盘点数都不变。

    一个会顺手补缺口的报告让「报告跑过」与「缺口被处理过」在盘上长得一样。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
        ("get_ops_report", {"name": "known-accounts"}),
        ("get_ops_report", {"name": "known-accounts"}),
    )
    before = _ok(outcome, 2)
    after = _ok(outcome, 3)

    assert before["ticks"] == after["ticks"]
    assert before["source_supply"] == after["source_supply"]
    assert after["at"] != before["at"], "两次读的时刻不同 —— 内容相同不是因为没跑"


def test_a_maintenance_run_is_refused_when_the_account_list_is_not_there(
    session_factory, settings: Settings
) -> None:
    """主体来自那份清单，清单不在就没有主体 —— 拒绝，不是「这一轮没有到期的」。"""
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "no-such-list"}),
    )
    text = _refused(outcome, 1)
    assert "no-such-list" in text


# ----------------------------------------------------------------------
# list_scenes
# ----------------------------------------------------------------------
def test_list_scenes_shows_the_lifecycle_of_every_scene(session_factory) -> None:
    """一个部署的立项清单：状态、哪一版在生效、让不让派发以及不让的理由。

    ``get_scene`` 要求调用方**先知道那个场景的名字**，所以「这个部署里有哪些
    立项」在任何工具上都问不出来 —— 运维只能去翻数据库，而那是本层明令不许的。
    """
    outcome = session_factory(
        ("create_scene", BASE_CREATE),
        ("list_scenes", {}),
        ("control_scene", {"action": "activate", "version": 1}),
        ("list_scenes", {}),
    )
    created = _ok(outcome, 0)
    listed = _ok(outcome, 1)

    assert listed["scene_count"] == 1
    assert listed["record_state"] == "present"
    assert listed["workspace"] == "uk-acme-natural-grass"
    assert listed["truncation"]["dropped"] == 0

    row = listed["scenes"][0]
    assert row["scene_id"] == created["scene"]["scene_id"]
    assert row["state"] == "draft"
    assert row["effective_version"] is None and row["effective"] is None
    assert row["accepts_new_work"] is False
    assert row["dispatch_refusal"] == "draft: 尚未批准生效，不派发"
    assert listed["summary"]["awaiting_approval"] == 1
    assert listed["summary"]["dispatchable"] == 0

    row = _ok(outcome, 3)["scenes"][0]

    assert row["state"] == "active"
    assert row["accepts_new_work"] is True and row["dispatch_refusal"] == ""
    assert row["effective_version"] == 1
    # 生效那一版给的是**字节摘要**：授权门与 wave manifest 绑的是它，而版本号
    # 回答的是「第几次编辑」。只报版本号会让「现在生效的是哪一套规则」无解。
    assert row["effective"]["frozen_sha256"] == created["version"]["frozen_sha256"]
    assert row["effective"]["profile_version"] == created["version"]["profile_version"]
    # 批准人是**从令牌来的**，这一格只是把它读出来。
    assert [item["by"] for item in row["approvals"]] == ["claude-control"]


def test_a_pending_version_is_visible_and_is_not_the_effective_one(session_factory) -> None:
    """改完没批：``pending_version`` 非空而 ``effective_version`` 不动。

    这一格是 ``list_scenes`` 存在的第二个理由 —— 一份「已改版但没批准」的场景在
    任何工具上都读不出「有一版在等着批」，而它正是一个待办。
    """
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("add_scene_version", {"budget": {"search": 5}, "note": "缩预算"}),
        ("list_scenes", {}),
    )
    row = _ok(outcome, 2)["scenes"][0]

    assert row["latest_version"] == 2
    assert row["effective_version"] == 1, "改版不生效：在途的运行不能中途换规则"
    assert row["pending_version"] == 2
    assert _ok(outcome, 2)["summary"]["with_pending_version"] == 1


def test_list_scenes_does_not_create_the_store(
    session_factory, settings: Settings
) -> None:
    """库不存在时如实报 ``no_store``，而**读一次不建库**。

    与 ``get_scene`` 那一格同一组话（``tools._SCENE_RECORD_*``）：两处各编一组的话，
    「还没写过任何东西」与「写了、但没有这个场景」会在两份报告里各说一套。
    """
    assert not settings.database_path.exists()
    body = _ok(session_factory(("list_scenes", {})))

    assert body["record_state"] == "no_store"
    assert body["scenes"] == [] and body["scene_count"] == 0
    assert not settings.database_path.exists(), "读一次建出一个空库 —— 那不是一次读"


def test_list_scenes_says_not_wired_rather_than_empty(
    settings: Settings, tmp_path: Path
) -> None:
    """``scenes=None`` 时是 ``not_wired``：这一句与「一个都没有」指向两件事。"""
    ring_path, token = _ring(tmp_path, "claude-control", [Role.CONTROLLER])
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        product_loop=product_loop_handlers(
            settings=settings,
            scenes=None,
            maintenance=default_maintenance_store(settings),
            assets=default_asset_store(settings),
            relations=default_relation_store(settings),
            accounts=default_account_pool_store(settings),
            cohorts=default_account_cohort_store(settings),
            review=None,
            acquisition=None,
            router=None,
            documents=None,
        ),
        scenes=None,
        calls=(("list_scenes", {}),),
        want_tool_list=False,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    body = _ok(outcome)

    assert body["record_state"] == "not_wired"
    assert body["scenes"] == [] and body["summary"] is None
    assert "接线" in body["note"] and "不是「一个都没有」" in body["note"]


def test_list_scenes_lists_rows_this_tool_could_not_have_created(
    session_factory, settings: Settings
) -> None:
    """库里别的行照样被列出来，截断**被报出来**。

    ``create_scene`` 只写本部署那一个 ``scene_id``（``require_scene``），所以正常
    情况下这份清单只有一行。但库是**共享的落点**：同一批人换过
    ``MARKETOPS_PROFILE``、或有人直接写过库，都会让同一个 workspace 下出现第二行。
    读的时候把它们藏起来，等于让「这个库现在有什么」有一个工具回答不了 ——
    而报告上看起来只是「只有一个场景」。
    """
    _ok(session_factory(("create_scene", {**BASE_CREATE, "activate": True})), 0)

    # 第二行**不走工具**写进去，而且**在新的一条会话里**写：走工具它会被
    # ``require_scene`` 挡下，而这里要测的正是「工具挡不下的那些行在库里怎么办」。
    # 跨会话也正是一次读该验证的东西 —— 一条会话里现造现读测不出「库里存着」。
    store = default_scene_store(settings)()
    first = store.get_record("uk-acme-natural-grass")
    assert first is not None
    # ``revision`` 归零是 ``put_record`` 对**新建**的要求（非零 revision 会被读成
    # 一次「改」），而这一行确实是一次新建。
    store.put_record(
        first.model_copy(update={"scene_id": "another-scene", "revision": 0}),
        expected_revision=None,
    )

    outcome = session_factory(
        ("list_scenes", {"limit": 1}),
        ("list_scenes", {"limit": None}),
    )
    capped = _ok(outcome, 0)
    assert capped["scene_count"] == 2
    assert len(capped["scenes"]) == 1
    assert capped["truncation"] == {
        "truncated": True,
        "kept": 1,
        "dropped": 1,
        "cap": 1,
        "dropped_items": ["uk-acme-natural-grass"],
    }, "截断要报出**丢了哪几条**，不只是一个数"

    everything = _ok(outcome, 1)
    assert [row["scene_id"] for row in everything["scenes"]] == [
        "another-scene",
        "uk-acme-natural-grass",
    ]
    assert everything["truncation"]["cap"] is None
    # 汇总覆盖**整份清单**，与截断无关：跟着截断变的汇总会让「有几个场景」
    # 变成一个取决于调用参数的数。
    assert everything["summary"]["dispatchable"] == 2


# ----------------------------------------------------------------------
# 责任关系：remember_responsibility / list_responsibilities
# ----------------------------------------------------------------------
#: 一条责任判定的形状。证据引用指向 ``_write_evidence`` 真的落过的那些行 ——
#: 引用一条不存在的证据是这一层要拒绝的东西（``evidence_unknown``），所以「正常的
#: 那一条」必须挂一条在库里的证据，否则每一条判据都在测拒绝。
def _judgement(
    account_id: str = "example-one",
    relation: str = "maintains",
    evidence_ids: tuple[str, ...] = ("ev-rel-1",),
) -> dict[str, Any]:
    return {"account_id": account_id, "relation": relation, "evidence_ids": list(evidence_ids)}


def _write_evidence(settings: Settings, *evidence_ids: str) -> None:
    """往这个部署的证据库里落几行。

    关系表与证据库是**同一个库文件**，所以这一行顺带把库建出来（先建库还是先折母池
    都不影响：两次都走 ``ensure_schema``）。
    """
    from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore

    store = SqliteEvidenceStore(settings.database_path)
    for index, evidence_id in enumerate(evidence_ids):
        store.put_evidence(
            Evidence(
                workspace=settings.profile,
                evidence_id=evidence_id,
                account_id="example-one",
                source_url=f"https://example-village.example/trust/{index}",
                source_kind=SourceKind.FETCHED_HTML,
                literal_value="Example Village Trust",
                content_sha256=f"sha-{index}",
                fetched_at=datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc),
                field=ContactField.EMAIL,
            )
        )


def _asset_id(session_factory, name: str) -> str:
    """按**名字**取那块场地的句柄 —— 句柄是派生的，测试里不该自己拼。"""
    listed = _ok(session_factory(("list_assets", {})))
    return {row["name"]: row["venue_asset_id"] for row in listed["assets"]}[name]


def test_a_judgement_lands_and_the_read_joins_the_venue_name(
    session_factory, settings: Settings
) -> None:
    """一条判定写下去、读回来，而读回来的那一行**带着场地的名字**。

    ``venue_asset_id`` 是一个派生的句柄（``src-<hex16>``）：它认得出「是不是同一块
    场地」，认不出「是哪一块」。所以读的时候要拿池子里那一行的名字来对上 —— 一份
    只有句柄的关系表读起来是一串十六进制，而 §5 的问题是「**谁**对**哪块场地**负责」。

    这一条同时钉住「记下一条判定**不是**重新证实了它」：写进去的那一行
    ``last_verified_at`` 仍然是空的，读回来也是空的，而汇总把它数进
    ``relations_never_re_verified``。填上它的话，「这条关系多久没复核了」这个只在表
    建起来之后才有意义的问句，会在第一次记录那天就得到「刚刚复核过」。
    """
    _fold(session_factory, settings)
    hall = _asset_id(session_factory, "Example Village Hall")
    _write_evidence(settings, "ev-rel-1")

    written = _ok(
        session_factory(
            (
                "remember_responsibility",
                {"venue_asset_id": hall, "judgements": [_judgement()]},
            )
        )
    )
    assert written["venue_asset_id"] == hall
    assert written["venue_name"] == "Example Village Hall"
    assert written["workspace"] == settings.profile
    # 四类去向**都**报出来，零个也报：只报非零的那几类时，「这一次没有折」与
    # 「这一栏我今天没算」在读的人眼里一样。
    assert written["by_disposition"] == {
        "written": 1,
        "folded": 0,
        "evidence_unknown": 0,
        "rejected": 0,
    }
    assert written["relations_in_the_pool_before"] == 0
    assert written["relations_in_the_pool_after"] == 1
    assert written["outcomes"][0]["last_verified_at"] is None
    # 这一条路径手里**没有**判定台账，所以它写下来的行如实带着空表 —— 空表说的是
    # 「这一行不是判出来的」，而不是「今天没算」。有判定支撑的那条路是
    # ``submit_findings``：它每一条结论都要带 ``decision_id`` 并当场回库核对。
    assert written["outcomes"][0]["decision_record_ids"] == [], (
        "手工记下来的那一行说自己有判定支撑 —— 那一格是判出来的行与手工行在表里"
        "唯一的差别，填上它，「关系由真实判定支撑」就成了自觉"
    )

    read = _ok(session_factory(("list_responsibilities", {})))
    assert read["responsibility_state"] == "present"
    assert read["relation_count"] == 1
    row = read["relations"][0]
    assert row["venue_name"] == "Example Village Hall"
    assert row["relation"] == "maintains"
    assert row["account_id"] == "example-one"
    assert row["evidence_ids"] == ["ev-rel-1"], (
        "证据给的是 id 列表而不是条数：读者的下一步**就是**去看那几条（get_evidence）"
    )
    assert row["decision_record_ids"] == [], (
        "写的时候报了、读回来没有 —— 那份差别就只在回执里存在过一次，而读表的人"
        "（§5 那张表就是给他看的）分不出这一行是判出来的还是手工记的"
    )
    assert row["last_verified_at"] is None
    assert read["summary"]["relations_never_re_verified"] == 1
    # 六个成员都列出来，零个也列 —— 关系是一个闭集，「这一种一条都没有」是一个
    # **测出来的**事实，而不是没有数据。
    assert read["summary"]["by_relation"] == {
        "owns": 0,
        "operates": 0,
        "maintains": 1,
        "booking_control": 0,
        "procures": 0,
        "unresolved": 0,
    }


def test_the_rows_that_did_not_land_are_named_by_disposition(
    session_factory, settings: Settings
) -> None:
    """一批四行、只落一行时，另外三行**各自**说得出自己去了哪。

    ``folded`` 是同一批里第二次说同一条关系（表里仍然只有一行），``evidence_unknown``
    与 ``rejected`` 是一行都没写。三者合成一个「失败」数会让「这一行是重复」与
    「这一行的证据是假的」读成同一句话 —— 而两者的处置正相反（一个是没事，一个是
    回去核对那份产出）。
    """
    _fold(session_factory, settings)
    hall = _asset_id(session_factory, "Example Village Hall")
    # 两条都在库里 —— 第 1 行要走到 ``folded``（说的是同一条关系），而不是走到
    # ``evidence_unknown``（那是第 3 行的事，而两类的处置正相反）。
    _write_evidence(settings, "ev-rel-1", "ev-rel-2")

    written = _ok(
        session_factory(
            (
                "remember_responsibility",
                {
                    "venue_asset_id": hall,
                    "judgements": [
                        _judgement(evidence_ids=("ev-rel-1",)),
                        _judgement(evidence_ids=("ev-rel-2",)),
                        _judgement(relation="runs"),
                        _judgement(account_id="example-two", evidence_ids=("never-stored",)),
                    ],
                },
            )
        )
    )
    assert written["by_disposition"] == {
        "written": 1,
        "folded": 1,
        "evidence_unknown": 1,
        "rejected": 1,
    }
    assert written["relation_count"] == 4, "relation_count 数的是**交上来**的行"
    assert written["relations_in_the_pool_after"] == 1, "四行说的是一个账号的一条关系"
    assert written["outcomes"][1]["reason"].startswith("同一批里第 0 行"), (
        "折进来的那一行要指名**第几行** —— 否则交二十行的人不知道折的是哪两行"
    )
    for index in (2, 3):
        assert written["outcomes"][index]["evidence_ids"] is None
        assert written["outcomes"][index]["evidence_count"] is None, (
            "没写进去的行「有几条证据」没有答案 —— 写 0 会被读成「这条关系一条证据都没有」"
        )
        assert written["outcomes"][index]["decision_record_ids"] is None, (
            "没写进去的行给的是空表 —— 空表有答案（「这一行不是判出来的」），而这一行"
            "根本不在表里，它没有那一格可答"
        )
    # 拒绝的措辞：理由里列的是**可用的取值**（渲染不把交上来的原值补进消息里），
    # 而「我交的是什么」在 ``relation`` 那一格 —— 它回显的是交来的原文，只用于对账。
    assert "unresolved" in written["outcomes"][2]["reason"], "理由要说得出现成的取值"
    assert written["outcomes"][2]["relation"] == "runs"


def test_a_hand_written_row_cannot_claim_a_judgement_backed_it(
    session_factory, settings: Settings
) -> None:
    """手工那条路径**填不了**「这一行是哪一次判定得出的」这一格 —— 那一行整行不写。

    这一格是表里判出来的行与手工行**唯一**的差别，所以它只能由承重的那条路写下去
    （``submit_findings``：每条结论带 ``decision_id``，且当场拿 id 回台账核对工作单元、
    主语、题面、答案）。让它变成 ``RelationClaim`` 上的一格，等于让交判定行的人自己
    声明「这一行有判定支撑」—— 而这一层手里没有判定台账，核不了，于是那句话就是自觉。

    所以这里交一行**看起来完全正常**的判定、只多带一格：整行落在 ``rejected``，
    一行都不写。与 ``ev-*`` 那条负例不同 —— 证据那一格是**真的**，被拒的是多出来的
    那一格，所以通过不能来自「拒绝一切带额外键的行」。
    """
    _fold(session_factory, settings)
    hall = _asset_id(session_factory, "Example Village Hall")
    _write_evidence(settings, "ev-rel-1")

    forged = dict(_judgement(), decision_record_ids=["rec-1"])
    written = _ok(
        session_factory(
            ("remember_responsibility", {"venue_asset_id": hall, "judgements": [forged]})
        )
    )
    assert written["by_disposition"] == {
        "written": 0,
        "folded": 0,
        "evidence_unknown": 0,
        "rejected": 1,
    }
    assert written["relations_in_the_pool_after"] == 0, (
        "这一行写进表里了 —— 它在表里与一条真判出来的关系一字不差，而它没有任何判定"
    )
    assert written["outcomes"][0]["decision_record_ids"] is None, "没写进去的行没有那一格可答"
    assert "decision_record_ids" in written["outcomes"][0]["reason"], (
        "拒绝的理由要点名是多出来的哪一格 —— 交行的人手里只有这一行，不说就不知道删什么"
    )


def test_a_second_judgement_about_the_same_relation_reports_what_is_now_in_the_table(
    session_factory, settings: Settings
) -> None:
    """第二次记同一条关系时，回执里那一行是**表里现在那一行**，不是刚交上来的那一份。

    两者在第一次写入时看不出差别，而第二次不一样：证据是并集、``first_seen_at`` 是更早
    的那个。报出交上来的那一份，读的人会以为这条关系只剩刚交的这条凭据 —— 而
    ``evidence_ids`` 正是他下一步要拿去 ``get_evidence`` 逐条读的那一格。
    """
    _fold(session_factory, settings)
    hall = _asset_id(session_factory, "Example Village Hall")
    _write_evidence(settings, "ev-rel-1", "ev-rel-2")

    outcome = session_factory(
        (
            "remember_responsibility",
            {"venue_asset_id": hall, "judgements": [_judgement(evidence_ids=("ev-rel-1",))]},
        ),
        (
            "remember_responsibility",
            {"venue_asset_id": hall, "judgements": [_judgement(evidence_ids=("ev-rel-2",))]},
        ),
    )
    second = _ok(outcome, 1)
    assert second["outcomes"][0]["disposition"] == "written"
    assert second["outcomes"][0]["evidence_ids"] == ["ev-rel-1", "ev-rel-2"]
    assert second["relations_in_the_pool_before"] == 1
    assert second["relations_in_the_pool_after"] == 1, "第二次说的是同一条关系，不是第二条"


def test_a_relation_is_never_hung_on_a_venue_that_is_not_in_the_pool(
    session_factory, settings: Settings
) -> None:
    """池子里没有这块场地时**拒绝整次调用**，并点名两条处置。

    绕过池子把这一行写进表里的话，它在任何一份按场地读的报告里**永远出现不了**
    （读的时候要拿场地的名字去对上），而它在表里躺着，看起来像已经研究过了。
    """
    _fold(session_factory, settings)
    _write_evidence(settings, "ev-rel-1")

    text = _refused(
        session_factory(
            (
                "remember_responsibility",
                {
                    "venue_asset_id": "src-0000000000000000",
                    "judgements": [_judgement()],
                },
            )
        )
    )
    assert "promote_receipt" in text, "处置之一：先把那份母池回执折进来"
    assert "派生" in text, "处置之二：这个 id 是一个句柄，不是场地的名字"

    read = _ok(session_factory(("list_responsibilities", {})))
    assert read["relation_count"] == 0, "拒绝之后表里不该留下任何一行"


def test_an_empty_venue_id_is_refused_before_anything_is_touched(
    session_factory, settings: Settings
) -> None:
    """``venue_asset_id`` 是必填的：少了它不是一条更宽的判定，是一条**无主**的判定。"""
    _fold(session_factory, settings)

    text = _refused(
        session_factory(
            ("remember_responsibility", {"venue_asset_id": "   ", "judgements": [_judgement()]})
        )
    )
    assert "必填" in text
    assert "无主" in text


def test_remembering_on_an_empty_deployment_names_the_two_steps_that_are_missing(
    session_factory, settings: Settings
) -> None:
    """还没建库时给的是「先折回执、再折资产」，而不是「这块场地没有负责方」。"""
    assert not settings.database_path.is_file()

    text = _refused(
        session_factory(
            (
                "remember_responsibility",
                {"venue_asset_id": "src-0000000000000000", "judgements": [_judgement()]},
            )
        )
    )
    assert "import_motherpool" in text and "promote_receipt" in text
    assert not settings.database_path.is_file(), (
        "为了回答「库里没有这块场地」而建出一个空库 —— 那不是一个拒绝该留下的东西"
    )


def test_remember_responsibility_says_not_wired_rather_than_nothing_to_do(
    settings: Settings, tmp_path: Path
) -> None:
    """少了任何一个存储都是**接线事实**：它说的是组合根，不是这批判定。

    两个方向都要走一遍，而且要点名**少的是哪一个**：写一条关系要两个都有，而处置取决
    于缺的那一个（去接线池子，还是去接线关系表）。这一句与「这块场地没有负责方」指向
    的下一件事不同 —— 后者是去研究。
    """
    for missing in ("assets", "relations"):
        text = _refused(
            _not_wired_driver(
                settings,
                tmp_path,
                (
                    "remember_responsibility",
                    {"venue_asset_id": "src-0000000000000000", "judgements": [_judgement()]},
                ),
                missing=missing,
            )
        )
        assert "接线" in text
        assert ("场地资产池" if missing == "assets" else "责任关系表") in text, (
            "点的是**少的那个**名字 —— 一句「某个存储没接」让人不知道该去接哪一处"
        )
        assert "两个都要有" in text, "它还要说清另一个为什么在场：关系挂在池子里的场地上"


def test_list_responsibilities_says_no_store_and_does_not_create_one(
    session_factory, settings: Settings
) -> None:
    """库不存在时是 ``no_store``，而它**不是**「这个市场没有任何责任关系」。"""
    assert not settings.database_path.is_file()

    body = _ok(session_factory(("list_responsibilities", {})))
    assert body["responsibility_state"] == "no_store"
    assert body["relation_count"] == 0 and body["relations"] == []
    assert body["summary"] is None
    assert "remember_responsibility" in body["note"], "拒绝里要给出该走哪一步"
    assert not settings.database_path.is_file(), "读一次建出一个空库 —— 那不是一次读"


def test_list_responsibilities_says_not_wired_rather_than_empty(
    settings: Settings, tmp_path: Path
) -> None:
    """少了任何一个存储都是 ``not_wired``，而它点名**少的那一个**。

    与 ``no_store`` 配成一对：两者都在 ``relations`` 那一格给空列表，分开它们的是
    ``responsibility_state``。
    """
    for missing in ("assets", "relations"):
        body = _ok(
            _not_wired_driver(settings, tmp_path, ("list_responsibilities", {}), missing=missing)
        )
        assert body["responsibility_state"] == "not_wired"
        assert "接线" in body["note"] and "不是「一条关系都没有」" in body["note"]
        named = "场地资产池" if missing == "assets" else "责任关系表"
        assert named in body["note"]


def test_reading_by_venue_and_by_account_are_two_questions(
    session_factory, settings: Settings
) -> None:
    """两个方向各是一条读法，而**同时给两个参数是拒绝**。

    「这个账号对这块场地负什么责」是**一行**的答案，而按场地读一次就把它给全了 ——
    再发明一条「场地 × 账号」的读法，是让同一句话有两个出处。所以拒绝的措辞里点名
    那条现成的读法。

    按账号读是 §6 那个方向的问题（这个账号在管哪些场地），而它是
    ``relations_for_account`` 在生产路径上的第一个读者。
    """
    _fold(session_factory, settings, _many_asset_lines(2), "two-venues")
    _write_evidence(settings, "ev-rel-1", "ev-rel-2")
    first = _asset_id(session_factory, "Example Venue 001")
    second = _asset_id(session_factory, "Example Venue 002")
    for asset_id, evidence_id in ((first, "ev-rel-1"), (second, "ev-rel-2")):
        _ok(
            session_factory(
                (
                    "remember_responsibility",
                    {
                        "venue_asset_id": asset_id,
                        "judgements": [_judgement(evidence_ids=(evidence_id,))],
                    },
                )
            )
        )

    by_account = _ok(session_factory(("list_responsibilities", {"account_id": "example-one"})))
    assert {row["venue_asset_id"] for row in by_account["relations"]} == {first, second}
    assert by_account["summary"]["assets_with_relations"] == 2

    by_venue = _ok(session_factory(("list_responsibilities", {"venue_asset_id": first})))
    assert [row["venue_asset_id"] for row in by_venue["relations"]] == [first]

    text = _refused(
        session_factory(
            ("list_responsibilities", {"venue_asset_id": first, "account_id": "example-one"})
        )
    )
    assert "只能给一个" in text
    assert "已经把这块场地上的每一行" in text, "拒绝要指出那条现成的读法"


def test_the_summary_covers_the_whole_table_while_the_page_is_truncated(
    session_factory, settings: Settings
) -> None:
    """缺省封顶、封了多少报出来、汇总**不跟着截断变**。

    一份 3 条关系的表在 ``limit=1`` 下仍然是一张 3 条关系的表：跟着截断变的汇总会把
    「这个市场有多少条责任」变成一个取决于调用参数的数。
    """
    _fold(session_factory, settings, _many_asset_lines(3), "three-venues")
    _write_evidence(settings, "ev-rel-1", "ev-rel-2", "ev-rel-3")
    for index, evidence_id in enumerate(("ev-rel-1", "ev-rel-2", "ev-rel-3"), start=1):
        _ok(
            session_factory(
                (
                    "remember_responsibility",
                    {
                        "venue_asset_id": _asset_id(session_factory, f"Example Venue {index:03d}"),
                        "judgements": [
                            _judgement(
                                account_id=f"example-{index}", evidence_ids=(evidence_id,)
                            )
                        ],
                    },
                )
            )
        )

    capped = _ok(session_factory(("list_responsibilities", {"limit": 1})))
    assert capped["relation_count"] == 3
    assert len(capped["relations"]) == 1
    assert capped["truncation"]["kept"] == 1
    assert capped["truncation"]["dropped"] == 2
    assert capped["summary"]["accounts_named"] == 3, "汇总覆盖整张表，与截断无关"

    everything = _ok(session_factory(("list_responsibilities", {"limit": None})))
    assert len(everything["relations"]) == 3
    assert everything["truncation"]["cap"] is None


def test_assets_without_a_relation_is_null_when_the_read_is_filtered(
    session_factory, settings: Settings
) -> None:
    """「池子里有多少块场地一条关系都没有」只有**不设过滤**的那一次读答得出来。

    按场地读时它测不出来：那时它是 ``null`` 加一句理由，而**不是 0** —— 0 会被读成
    「每一块场地都有负责方」，而它恰好是这个数唯一会被用错的方向（它是待办队列，
    不是「这些场地没有人负责」）。
    """
    _fold(session_factory, settings, _many_asset_lines(2), "two-venues")
    _write_evidence(settings, "ev-rel-1")
    first = _asset_id(session_factory, "Example Venue 001")
    _ok(
        session_factory(
            (
                "remember_responsibility",
                {"venue_asset_id": first, "judgements": [_judgement()]},
            )
        )
    )

    everything = _ok(session_factory(("list_responsibilities", {})))
    assert everything["summary"]["assets_without_a_relation"] == 1
    assert everything["summary"]["assets_with_relations"] == 1
    assert everything["summary"]["summary_gap"] == ""

    filtered = _ok(session_factory(("list_responsibilities", {"venue_asset_id": first})))
    assert filtered["summary"]["assets_without_a_relation"] is None
    assert "只有" in filtered["summary"]["summary_gap"]


def test_a_relation_whose_venue_left_the_pool_is_still_read(
    session_factory, settings: Settings
) -> None:
    """池子里没有那块场地时 ``venue_name`` 是 ``null``，而那一行**照样列出来**。

    这不是排版的缺失，是一条真事实（关系写下来了，而那块场地不在池子里），它的处置与
    「名字是空的」不同。写入口不许挂这种关系，但库是共享的落点：换过
    ``MARKETOPS_PROFILE``、或有人直接写过库，都会让它出现 —— 藏起来等于让「这个库现在
    有什么」有一个工具回答不了。
    """
    from marketops.adapters.sqlite.pool import SqliteAccountVenueRelationStore
    from marketops.application.relations import merge_relations
    from marketops.domain.entities import AccountVenueRelation
    from marketops.domain.enums import VenueRelation

    _fold(session_factory, settings)
    SqliteAccountVenueRelationStore(settings.database_path, merge_relations).upsert_relation(
        AccountVenueRelation(
            workspace=settings.profile,
            account_id="example-one",
            venue_asset_id="src-0000000000000000",
            relation=VenueRelation.MAINTAINS,
            evidence_ids=["ev-elsewhere"],
            first_seen_at=datetime(2026, 9, 23, 1, 0, tzinfo=timezone.utc),
        )
    )

    read = _ok(session_factory(("list_responsibilities", {})))
    assert read["relation_count"] == 1
    assert read["relations"][0]["venue_name"] is None
    assert read["relations"][0]["venue_asset_id"] == "src-0000000000000000"


@pytest.mark.parametrize("pool", ["written", "no_store", "not_wired"])
def test_a_negative_limit_on_the_relation_table_comes_back_as_a_result(
    session_factory, settings: Settings, tmp_path: Path, pool: str
) -> None:
    """与资产池那一条逐字同形：负数在三种状态下都作为一条结果回来。

    那个拒绝在实现里是**一处**（``capped``），而它要经过三条分支才到得了。
    """
    if pool == "not_wired":
        outcome = _not_wired_driver(
            settings, tmp_path, ("list_responsibilities", {"limit": -1}), missing="relations"
        )
    else:
        if pool == "written":
            _fold(session_factory, settings)
        outcome = session_factory(("list_responsibilities", {"limit": -1}))

    text = _refused(outcome)
    assert "负数" in text
    assert "None" in text, "拒绝里要给出正确写法，而不只是说「不行」"


# ----------------------------------------------------------------------
# 账号池（§6）：remember_accounts / list_account_pool
# ----------------------------------------------------------------------
#: 一行账号的形状。证据引用指向 ``_write_evidence`` 真的落过的那些行 ——
#: 引用一条不存在的证据是这一层要拒绝的东西（``evidence_unknown``），所以「正常的那
#: 一条」必须挂一条在库里的证据，否则每一条判据都在测拒绝。
def _account_row(
    account_id: str = "example-one",
    organisation: str = "Example Village Trust",
    evidence_ids: tuple[str, ...] = ("ev-acct-1",),
    **overrides: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "account_id": account_id,
        "organisation": organisation,
        "evidence_ids": list(evidence_ids),
    }
    row.update(overrides)
    return row


def test_an_account_lands_and_the_read_gives_the_row_back(
    session_factory, settings: Settings
) -> None:
    """一个账号写下去、读回来，而读回来的那一行**带着它的判定与证据 id**。

    这一条同时钉住三件事，而它们各自是一条纪律：

    * **``status`` 是记下来的那一次判定**（``eligible``），不是读的时候拿现行规则
      重算一遍 —— 后者属于另一条轴（``list_accounts`` 那条准入判定），两条轴互不
      决定，在这一行上合成一格就读不出「当时判成什么」。
    * **``last_verified_at`` 是空的**：记下一个账号**不是**重新核实了它（与
      ``promote_receipt`` / ``remember_responsibility`` 同一条），填上它会让「这个
      账号多久没复核了」在第一次记录那天就得到「刚刚复核过」。
    * **``country`` 的三态原样保留**：这里给的是 ``GB``，而「还没查」（``null``）与
      「查了、是没有」（``""``）在别处各有各的用例，这一格不把三者压成两者。
    """
    _write_evidence(settings, "ev-acct-1")

    written = _ok(
        session_factory(
            (
                "remember_accounts",
                {
                    "accounts_rows": [
                        _account_row(
                            aliases=["Example PC", "Example Parish Council"],
                            domains=["example-village.example"],
                            addresses=["1 High Street, Example"],
                            country="GB",
                            status="eligible",
                        )
                    ]
                },
            )
        )
    )
    assert written["workspace"] == settings.profile
    # 四类去向**都**报出来，零个也报：只报非零的那几类时，「这一次没有折」与
    # 「这一栏我今天没算」在读的人眼里一样。
    assert written["by_disposition"] == {
        "written": 1,
        "folded": 0,
        "evidence_unknown": 0,
        "rejected": 0,
    }
    assert written["account_count"] == 1
    assert written["accounts_in_the_pool_before"] == 0
    assert written["accounts_in_the_pool_after"] == 1
    assert written["outcomes"][0]["status"] == "eligible"
    assert written["outcomes"][0]["evidence_count"] == 1
    assert written["outcomes"][0]["reason"] == "", (
        "顺利写入的行没有理由要说 —— 填一句「成功」会让读的人去分辨「有理由」与「有内容」"
    )

    read = _ok(session_factory(("list_account_pool", {})))
    assert read["pool_state"] == "present"
    assert read["account_count"] == 1
    row = read["accounts"][0]
    assert row["account_id"] == "example-one"
    assert row["organisation"] == "Example Village Trust"
    assert row["aliases"] == ["Example PC", "Example Parish Council"]
    assert row["domains"] == ["example-village.example"]
    assert row["addresses"] == ["1 High Street, Example"]
    assert row["country"] == "GB"
    assert row["status"] == "eligible"
    assert row["evidence_ids"] == ["ev-acct-1"], (
        "证据给的是 id 列表而不是条数：读者的下一步**就是**去看那几条（get_evidence）"
    )
    assert row["first_seen_at"], "进池子的时刻由服务端取，且总是有值"
    assert row["last_verified_at"] is None
    assert row["linked_assets"] == [], (
        "关系表接上了、这个账号一条关系都没有 —— 那是**空列表**（池子里确实没有它的行），"
        "与「这一栏读不出来」的 null 是两件事"
    )
    # 七个成员都列出来，零个也列 —— 账号状态是一个闭集，「这一种一个都没有」是一个
    # **测出来的**事实，而不是没有数据。
    assert read["summary"]["by_status"] == {
        "discovered": 0,
        "eligible": 1,
        "hold": 0,
        "irrelevant": 0,
        "duplicate": 0,
        "researched": 0,
        "archived": 0,
    }
    assert read["summary"]["accounts_never_re_verified"] == 1
    assert read["summary"]["accounts_without_a_relation"] == 1
    assert read["summary"]["summary_gap"] == ""


def test_a_country_nobody_looked_up_is_not_written_as_an_empty_string(
    session_factory, settings: Settings
) -> None:
    """``country`` 的三态在读数上原样保留：``null`` / ``""`` / 一个值，三个都不一样。

    这不是排版问题：「还没查」与「查了、是没有」的下一步**相反**（一个是去查，一个是别
    再查了），而这两个取值只有在 ``null`` 被渲染成 ``""`` 的时候才会在纸上长得一样。
    两个方向都要有 —— 只钉「空串照原样回」的话，一个把两者都写成 ``""`` 的实现照样通过。
    """
    _write_evidence(settings, "ev-acct-1", "ev-acct-2")

    outcome = session_factory(
        # 第 1 行没说国家（还没查）。
        ("remember_accounts", {"accounts_rows": [_account_row()]}),
        # 第 2 行说的是「查了，没有」。
        (
            "remember_accounts",
            {
                "accounts_rows": [
                    _account_row(account_id="example-two", country="", evidence_ids=("ev-acct-2",))
                ]
            },
        ),
        ("list_account_pool", {}),
    )
    read = _ok(outcome, 2)
    assert {row["account_id"]: row["country"] for row in read["accounts"]} == {
        "example-one": None,
        "example-two": "",
    }


def test_the_recorded_verdict_survives_a_research_that_gives_no_verdict(
    session_factory, settings: Settings
) -> None:
    """第二次记同一个账号、而这一次**没说判定**时，池子里那一行判定的还是 ``eligible``。

    这是 ``AccountClaim.status`` 缺省取 ``discovered``（§6·1 的「还没有判定」）与合并
    规则第 4 条（``discovered`` 不许覆盖真判定）两处合起来的结果 —— 而它与
    ``RelationClaim.relation`` 的缺省方向**相反**：那边连 ``unresolved`` 都是一次要凭据
    的判定，所以「没给」是给错了，走拒绝。

    第三次显式给 ``hold`` 时要真的改得动 —— 只钉「不会被抹掉」的话，「这一格写不进去」
    的实现同样能通过，而那会让判定永远停在第一次的值。
    """
    _write_evidence(settings, "ev-acct-1", "ev-acct-2")

    outcome = session_factory(
        (
            "remember_accounts",
            {"accounts_rows": [_account_row(status="eligible", evidence_ids=("ev-acct-1",))]},
        ),
        (
            "remember_accounts",
            # 第二行只说了「我又看到它了、又找到一条凭据」——没有判定。
            {
                "accounts_rows": [
                    _account_row(
                        aliases=["Example PC"], evidence_ids=("ev-acct-2",), status="discovered"
                    )
                ]
            },
        ),
        # 读**夹在两次写之间**（同一条会话里），否则读的时候第三次写已经落过了 ——
        # 那样这一条测的就不是「没给判定会不会抹掉已有的」。
        ("list_account_pool", {}),
        (
            "remember_accounts",
            {"accounts_rows": [_account_row(status="hold", evidence_ids=("ev-acct-1",))]},
        ),
        ("list_account_pool", {}),
    )
    second = _ok(outcome, 1)
    assert second["outcomes"][0]["disposition"] == "written"
    assert second["outcomes"][0]["status"] == "eligible", (
        "回执里的 status 是**池子里现在那一行**的值，不是交上来的那一份"
    )
    assert second["accounts_in_the_pool_after"] == 1, "第二次说的是同一个账号，不是第二个"

    between = _ok(outcome, 2)["accounts"][0]
    assert between["status"] == "eligible", "「没有判定」不覆盖判定"
    assert between["aliases"] == ["Example PC"], "别名取并集"

    assert _ok(outcome, 3)["outcomes"][0]["status"] == "hold", "显式给的判定要改得动"
    after = _ok(outcome, 4)["accounts"][0]
    assert after["status"] == "hold"
    assert after["evidence_ids"] == ["ev-acct-1", "ev-acct-2"], "证据取并集，一次都没丢"


def test_the_account_rows_that_did_not_land_are_named_by_disposition(
    session_factory, settings: Settings
) -> None:
    """一批四行、只落一行时，另外三行**各自**说得出自己去了哪。

    ``folded`` 是同一批里第二次说同一个账号（池子里仍然只有一行），``evidence_unknown``
    与 ``rejected`` 是一行都没写。三者合成一个「失败」数会让「这一行是重复」与「这一行的
    证据是假的」读成同一句话 —— 而两者的处置正相反（一个是没事，一个是回去核对那份产出）。

    名字与责任关系表那一条**故意不同**：那一条测的是关系表，这一条测的是账号池，两个
    函数同名会让后定义的那一个把前一个顶掉 —— 而 pytest 只按模块属性收集，被顶掉的那
    一条**静静地不再跑**（``ruff`` 的 F811 是当时唯一说得出话的地方）。
    """
    _write_evidence(settings, "ev-acct-1", "ev-acct-2")

    written = _ok(
        session_factory(
            (
                "remember_accounts",
                {
                    "accounts_rows": [
                        _account_row(evidence_ids=("ev-acct-1",)),
                        _account_row(organisation="Example Village Trust Ltd"),
                        _account_row(account_id="example-two", status="runs"),
                        _account_row(account_id="example-three", evidence_ids=("never-stored",)),
                    ]
                },
            )
        )
    )
    assert written["by_disposition"] == {
        "written": 1,
        "folded": 1,
        "evidence_unknown": 1,
        "rejected": 1,
    }
    assert written["account_count"] == 4, "account_count 数的是**交上来**的行"
    assert written["accounts_in_the_pool_after"] == 1, "四行说的是一个账号"
    assert written["outcomes"][1]["reason"].startswith("同一批里第 0 行"), (
        "折进来的那一行要指名**第几行** —— 否则交二十行的人不知道折的是哪两行"
    )
    # 拒绝的措辞里列的是**可用的取值**，而「我交的是什么」在 ``status`` 那一格回显
    # ——它只用于对账（「我交的第 3 行为什么没进去」）。
    assert "discovered" in written["outcomes"][2]["reason"], "理由要说得出现成的取值"
    for index in (2, 3):
        assert written["outcomes"][index]["status"] is None
        assert written["outcomes"][index]["evidence_count"] is None, (
            "没写进去的行「判定是什么、有几条证据」没有答案 —— 写 discovered / 0 会被读成"
            "「这个账号还没有判定、一条证据都没有」，而事实是这一行根本没落进池子"
        )


def test_reading_one_account_is_a_lookup_and_absent_is_not_an_error(
    session_factory, settings: Settings
) -> None:
    """给 ``account_id`` 就是 §14 的 ``get_account``：同一个行构造器，只是多一层过滤。

    「池子里没有这个账号」是一个**正常答案**（处置是去研究），与「读不出来」不是一句
    话 —— 所以它作为一条结果回来，并把 ``found`` 写出来。两个方向都要有：只有「找到了」
    的用例分不出「查过了」与「这一格永远等于 true」。
    """
    _write_evidence(settings, "ev-acct-1")
    _ok(
        session_factory(
            ("remember_accounts", {"accounts_rows": [_account_row(country="GB")]})
        )
    )

    found = _ok(session_factory(("list_account_pool", {"account_id": "example-one"})))
    assert found["lookup"] == {"account_id": "example-one", "found": True}
    assert [row["account_id"] for row in found["accounts"]] == ["example-one"]
    assert found["account_count"] == 1

    absent = _ok(session_factory(("list_account_pool", {"account_id": "example-none"})))
    assert absent["lookup"] == {"account_id": "example-none", "found": False}
    assert absent["accounts"] == [] and absent["account_count"] == 0
    assert absent["pool_state"] == "present", "池子在，只是没有这一行 —— 与 no_store 不是一句话"
    # 「池子里有多少个账号一条关系都没有」是整个池子的事，所以按 id 读的那一次答不出来
    # —— 给 null 加理由，而不是 0（0 会被读成「每一个账号都挂上了场地」）。
    assert absent["summary"]["accounts_without_a_relation"] is None
    assert "只有" in absent["summary"]["summary_gap"]


def test_list_account_pool_says_no_store_and_does_not_create_one(
    session_factory, settings: Settings
) -> None:
    """库不存在时是 ``no_store``，而它**不是**「这个市场研究出过零个账号」。"""
    assert not settings.database_path.is_file()

    body = _ok(session_factory(("list_account_pool", {})))
    assert body["pool_state"] == "no_store"
    assert body["account_count"] == 0 and body["accounts"] == []
    assert body["summary"] is None
    assert "remember_accounts" in body["note"], "要给出该走哪一步"
    assert not settings.database_path.is_file(), "读一次建出一个空库 —— 那不是一次读"


def test_list_account_pool_says_not_wired_rather_than_empty(
    settings: Settings, tmp_path: Path
) -> None:
    """少了账号池是 ``not_wired``，而它点名**是账号池没接**。

    与 ``no_store`` 配成一对：两者都在 ``accounts`` 那一格给空列表，分开它们的是
    ``pool_state``。这一条还钉住 ``lookup.found`` 是 ``null`` 而不是 ``false`` ——
    池子不在**这个**部署里，而「这个市场没有这个账号」是另一句话。
    """
    body = _ok(
        _not_wired_driver(settings, tmp_path, ("list_account_pool", {"account_id": "a"}), missing="accounts")
    )
    assert body["pool_state"] == "not_wired"
    assert "接线" in body["note"] and "不是「一个账号都没有」" in body["note"]
    assert body["lookup"] == {"account_id": "a", "found": None}


def test_remember_accounts_says_not_wired_rather_than_nothing_to_do(
    settings: Settings, tmp_path: Path
) -> None:
    """**写**工具那一族反过来：没接线是一次拒绝，不是一条空结果。

    ``_write_evidence`` 落过的库在这里无关 —— 少了账号池时这一批根本没有落点，而
    「去接线」与「这一批里没有可记的账号」是两条不同的处置。
    """
    text = _refused(
        _not_wired_driver(
            settings,
            tmp_path,
            ("remember_accounts", {"accounts_rows": [_account_row()]}),
            missing="accounts",
        )
    )
    assert "接线" in text
    assert "账号池" in text
    assert "不是「这一批里没有可记的账号」" in text


def test_remembering_an_account_on_an_empty_deployment_names_the_step_that_comes_first(
    session_factory, settings: Settings
) -> None:
    """库还不存在时要先让证据落盘，而不是报「这一行的证据解不开」。

    每一行的 ``evidence_ids`` 都要在库里解得开，而库里一条证据都没有时**每一行**都会落在
    ``evidence_unknown`` 上 —— 那不是一个可以开始的状态，所以这里直接停下并说清处置。
    """
    assert not settings.database_path.is_file()

    text = _refused(
        session_factory(("remember_accounts", {"accounts_rows": [_account_row()]}))
    )
    assert "证据" in text
    assert not settings.database_path.is_file(), (
        "为了回答「这一行的证据解不开」而建出一个空库 —— 那不是一个拒绝该留下的东西"
    )


def test_the_venues_an_account_manages_are_joined_with_their_names_and_relations(
    session_factory, settings: Settings
) -> None:
    """账号池那一行上的 ``linked_assets`` 是**接起来的**：句柄翻成名字，关系种类照写。

    账号行上不存第二份「它管哪几块场地」（那是 §5 那张表答的事），而 §5 要求同一个账号
    对同一块场地可以**同时**有几种身份。所以这一栏是一个列表，里面的每一条都带着场地的
    名字 —— 一份只有句柄的报告读起来是一串十六进制。
    """
    _fold(session_factory, settings, _many_asset_lines(2), "two-venues")
    _write_evidence(settings, "ev-acct-1", "ev-rel-1", "ev-rel-2")
    first = _asset_id(session_factory, "Example Venue 001")
    second = _asset_id(session_factory, "Example Venue 002")
    for asset_id, evidence_id, relation in (
        (first, "ev-rel-1", "owns"),
        (first, "ev-rel-2", "maintains"),
        (second, "ev-rel-1", "operates"),
    ):
        _ok(
            session_factory(
                (
                    "remember_responsibility",
                    {
                        "venue_asset_id": asset_id,
                        "judgements": [_judgement(relation=relation, evidence_ids=(evidence_id,))],
                    },
                )
            )
        )

    _ok(session_factory(("remember_accounts", {"accounts_rows": [_account_row()]})))

    read = _ok(session_factory(("list_account_pool", {})))
    linked = read["accounts"][0]["linked_assets"]
    # 比**集合**而不是列表：这一栏的顺序是关系表自己的（按它的键排），而那是存储
    # 契约管的事（``list_responsibilities`` 那边钉着）。这里要钉的是「每一行都带着
    # 场地的名字与关系种类」，不是「这一栏的次序由 join 决定」。
    assert {(item["venue_name"], item["relation"]) for item in linked} == {
        ("Example Venue 001", "owns"),
        ("Example Venue 001", "maintains"),
        ("Example Venue 002", "operates"),
    }, "同一块场地上的两种身份并存 —— §5 不强制单选"
    assert [item for item in linked if item["relation"] == "maintains"][0]["evidence_ids"] == [
        "ev-rel-2"
    ], "每一行各自带自己的凭据，不是把账号的证据摊到每一条关系上"
    assert read["summary"]["accounts_without_a_relation"] == 0, (
        "这个账号挂了场地 —— 那个数数的是**一条关系都没有**的账号"
    )


@pytest.mark.parametrize("missing", ["assets", "relations"])
def test_linked_assets_is_null_when_the_join_cannot_be_made(
    session_factory, settings: Settings, tmp_path: Path, missing: str
) -> None:
    """少了关系表或资产池时 ``linked_assets`` 是 ``null`` 加一句理由，而**不是空列表**。

    空列表会被读成「这个账号一块场地都不管」，而那是 §5 不许从「没有行」推出来的结论。
    两个方向都要走一遍，而且要点名**少的是哪一个** —— 处置是去接那一处。
    """
    _write_evidence(settings, "ev-acct-1")
    _ok(session_factory(("remember_accounts", {"accounts_rows": [_account_row()]})))

    body = _ok(_not_wired_driver(settings, tmp_path, ("list_account_pool", {}), missing=missing))
    assert body["accounts"][0]["linked_assets"] is None
    assert ("场地资产池" if missing == "assets" else "责任关系表") in body["linked_assets_gap"]


def test_reading_one_account_goes_through_the_ports_own_get(
    settings: Settings, tmp_path: Path
) -> None:
    """按 id 读走的是 ``AccountPoolStore.get_account``，不是「整张表读回来再筛」。

    两条路的**返回值逐字相同**，所以任何一条只比结果的判据都分不出它们 —— 而分不出就
    意味着「端口上那个读入口在生产路径上有没有读者」这件事，在盘上与「已经做完了」长得
    一样（``ports/pool.py`` 的 ``get_account``）。所以要数的不是结果，是**走了哪条路**。
    """
    from marketops.adapters.sqlite.pool import SqliteAccountPoolStore
    from marketops.application.accounts import merge_accounts

    _write_evidence(settings, "ev-acct-1")

    class _CountingPool(SqliteAccountPoolStore):
        """同一个实现，只是把走了哪两个读入口记下来。"""

        def __init__(self) -> None:
            super().__init__(settings.database_path, merge_accounts)
            self.calls: list[str] = []

        def list_accounts(self):
            self.calls.append("list_accounts")
            return super().list_accounts()

        def get_account(self, account_id: str):
            self.calls.append("get_account")
            return super().get_account(account_id)

    pool = _CountingPool()
    ring_path, token = _ring(tmp_path, "claude-control", [Role.CONTROLLER])
    outcome = drive_with_settings(
        settings=settings,
        tokens=TokenRing.from_file(ring_path),
        token=token,
        product_loop=product_loop_handlers(
            settings=settings,
            scenes=default_scene_store(settings),
            maintenance=default_maintenance_store(settings),
            assets=default_asset_store(settings),
            relations=default_relation_store(settings),
            accounts=lambda: pool,
            cohorts=default_account_cohort_store(settings),
            review=None,
            acquisition=None,
            router=None,
            documents=None,
        ),
        calls=(
            ("remember_accounts", {"accounts_rows": [_account_row()]}),
            ("list_account_pool", {"account_id": "example-one"}),
        ),
        want_tool_list=False,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    _ok(outcome, 0)
    read = _ok(outcome, 1)

    assert read["lookup"] == {"account_id": "example-one", "found": True}
    assert "get_account" in pool.calls, (
        "按 id 读把整张表读回来再筛 —— 结果一样，而端口上那个读入口就没有读者了"
    )


def test_the_account_pool_truncates_by_default_and_reports_it(
    session_factory, settings: Settings
) -> None:
    """缺省**封顶**、封了多少报出来、汇总**不跟着截断变**。

    三条一起钉，理由与 ``list_assets`` 逐字相同：缺省不封顶会让一次调用的响应大小由池子
    决定；静默截断会让 201 行的池子与 200 行的池子长得一样；跟着截断变的汇总会把「池子
    有多大」变成一个取决于调用参数的数。行数比 ``list_assets`` 那一条多一倍，是因为这一
    族的缺省上限就是两倍（``_MAX_POOL_ACCOUNTS``）。
    """
    _write_evidence(settings, "ev-acct-1")
    rows = [
        _account_row(account_id=f"example-{index:03d}", organisation=f"Example {index} Ltd")
        for index in range(201)
    ]
    written = _ok(session_factory(("remember_accounts", {"accounts_rows": rows})))
    assert written["by_disposition"] == {
        "written": 201,
        "folded": 0,
        "evidence_unknown": 0,
        "rejected": 0,
    }

    capped = _ok(session_factory(("list_account_pool", {})))
    assert capped["truncation"]["cap"] == 200
    assert capped["truncation"]["kept"] == 200
    assert capped["truncation"]["dropped"] == 1
    assert len(capped["accounts"]) == 200
    dropped = capped["truncation"]["dropped_items"]
    assert dropped == ["example-200"], (
        "丢的是**尾部**，顺序由 ``account_id`` 定死（两个实现都这么排）—— 所以「砍掉的"
        "是哪一条」答得出来"
    )
    # 截断的是那一列，不是池子。
    assert capped["account_count"] == 201
    assert capped["summary"]["by_status"]["discovered"] == 201
    assert capped["summary"]["accounts_never_re_verified"] == 201

    everything = _ok(session_factory(("list_account_pool", {"limit": None})))
    assert len(everything["accounts"]) == 201
    assert everything["truncation"]["cap"] is None
    assert everything["truncation"]["truncated"] is False


@pytest.mark.parametrize("pool", ["written", "no_store", "not_wired"])
def test_a_negative_limit_on_the_account_pool_comes_back_as_a_result(
    session_factory, settings: Settings, tmp_path: Path, pool: str
) -> None:
    """与资产池、关系表那两条逐字同形：负数在三种状态下都作为一条结果回来。

    那个拒绝在实现里是**一处**（``capped``），而它要经过三条分支才到得了 —— 一个只在
    「读到了行」那一支里拒绝的实现，会让一个空池子上的算错侥幸通过。
    """
    if pool == "not_wired":
        outcome = _not_wired_driver(
            settings, tmp_path, ("list_account_pool", {"limit": -1}), missing="accounts"
        )
    else:
        if pool == "written":
            _write_evidence(settings, "ev-acct-1")
            _ok(session_factory(("remember_accounts", {"accounts_rows": [_account_row()]})))
        outcome = session_factory(("list_account_pool", {"limit": -1}))

    text = _refused(outcome)
    assert "负数" in text
    assert "None" in text, "拒绝里要给出正确写法，而不只是说「不行」"


# ----------------------------------------------------------------------
# §11：报告里的 freshness —— 每一格现在是不是旧的
# ----------------------------------------------------------------------


def _write_many_accounts(settings: Settings, count: int) -> Path:
    """一份有 ``count`` 个账号的清单 —— 只为了把逐格名单顶到上限以上。"""
    rows = "\n".join(
        f"  - account_id: example-{index:03d}\n"
        f"    organisation: Example {index:03d} Ltd\n"
        f"    cohort: known_success\n"
        for index in range(count)
    )
    return _write_list(settings, "many-accounts", "accounts:\n" + rows)


def test_the_report_carries_a_cell_for_every_aspect_of_every_subject(
    session_factory, settings: Settings
) -> None:
    """``freshness`` 是**逐格**的，而且没到期的格子也在里面。

    21 格这个数不是从「七个面」推出来的，是「三个主体 × 七个面」算出来的：这一节
    要回答的是「这个市场现在有哪些格子是旧的」，而一份只有到期格子的清单答不了
    「有没有哪一格根本不旧」这件事 —— 空列表与「全都旧」在报告上长得一样。

    三个状态**每次都列全**（零也列）在这一条里也是判据：缺一个键会有两种读法，
    「这一档是 0」与「这一档没算」。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("get_ops_report", {"name": "known-accounts"}),
    )
    report = _ok(outcome, 1)
    freshness = report["freshness"]

    assert freshness["cells"] == 21, "三个主体 × 七个面"
    assert set(freshness["by_status"]) == {"current", "due", "never_observed"}
    assert freshness["by_status"] == {"current": 0, "due": 0, "never_observed": 21}
    assert len(freshness["policy_version"]) == 16

    assert freshness["stale_count"] == 21
    assert freshness["stale_truncated"] == 0
    assert freshness["stale_by_aspect"] == [
        ["source_coverage", 3],
        ["asset_identity", 3],
        ["asset_attribute", 3],
        ["account_relation", 3],
        ["contact_person_role", 3],
        ["email_evidence", 3],
        ["product_fit", 3],
    ], "逐面计数按 ALL_ASPECTS 的次序"

    for row in freshness["stale"]:
        assert row["status"] == "never_observed"
        assert row["stale_reason"] == "never_observed"
        # 两个时刻都是 ``None`` —— 不是 epoch、不是 0、也不是 ``now``。
        assert row["last_verified_at"] is None
        assert row["next_due_at"] is None


def test_the_freshness_cells_and_the_plan_agree_cell_by_cell(
    session_factory, settings: Settings
) -> None:
    """同一份判定的两个面：**本部署做不了**的话，是「没做」，不是「不旧」。

    计划那一侧九个格在本部署的范围内（三个面 × 三个主体），而 freshness 那一侧
    二十一格全是旧的。两句都要在，且必须**指向同一批格子**：范围外的四个面在这一节
    里照样是 ``never_observed`` —— 把「做不了」读成「不旧」会让一份报告在「这个市场
    还没建起来」这件事上给出一个漂亮数字。

    同一批格子的另一半是两个理由必须一致：计划说这一格到期的理由，与状态说它旧的
    理由，是同一个词。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("get_maintenance_plan", {"name": "known-accounts"}),
        ("get_ops_report", {"name": "known-accounts"}),
    )
    plan = _ok(outcome, 1)
    freshness = _ok(outcome, 2)["freshness"]

    plan_cells = {
        (row["subject_kind"], row["subject_id"], row["aspect"]): row["reason"]
        for row in plan["due"]
    }
    state_cells = {
        (row["subject_kind"], row["subject_id"], row["aspect"]): row["stale_reason"]
        for row in freshness["stale"]
    }

    assert plan["due_count"] == 9
    assert len(plan_cells) == 9, "九格各不相同（主体 × 面），没有撞在一起"
    assert set(plan_cells) <= set(state_cells), "计划里该做的每一格，在状态里都必须在"
    for cell, reason in plan_cells.items():
        assert state_cells[cell] == reason, f"{cell} 两份读法给了两个理由"

    outside = {
        (row["subject_kind"], row["subject_id"], row["aspect"]) for row in plan["out_of_scope"]
    }
    assert outside, "范围外的格子必须**被点出来**，不是被省略"
    assert len(outside) == 12, "四个面 × 三个主体"
    assert outside <= set(state_cells), "范围外不等于不旧：它们在状态里照样是旧的"
    assert len(set(state_cells) - set(plan_cells)) == 12, "二十一格减去本部署会动的那九格"


def test_a_draft_scene_reports_no_freshness_rather_than_a_fresh_market(
    session_factory, settings: Settings
) -> None:
    """没有生效版本 ⇒ ``freshness`` 是 ``None``，与 ``refresh`` 同一处置。

    ``None`` 在这里**不是**「一格都不旧」：``DRAFT`` 的场景还没有「该多久看一次」
    这句话，于是「旧不旧」无从谈起。写成一份全 ``current`` 的表会让一个还没开张的
    场景在报告里看起来像一个刚刚维护得好好的市场。
    """
    _write_accounts_list(settings)
    outcome = session_factory(
        ("create_scene", BASE_CREATE),  # 不 activate
        ("get_ops_report", {"name": "known-accounts"}),
    )
    report = _ok(outcome, 1)

    assert report["scene"]["state"] == "draft"
    assert report["scene"]["effective_version"] is None
    assert report["freshness"] is None
    assert report["refresh"] is None, "两处同一处置：没有节奏就没有新鲜度"


def test_a_freshness_list_that_was_cut_says_how_many_it_dropped(
    session_factory, settings: Settings
) -> None:
    """逐格名单封顶五十行，被砍掉的**逐格说清**。

    判据是「一份被砍过的名单读起来必须不像一份全的」：``stale_count`` 与
    ``len(stale)`` 分家而 ``stale_truncated`` 缺席的话，读的人会以为那就是全部。
    计数那一列（``stale_by_aspect``）算的是**全部**旧格子，不是被列出来的那些 ——
    所以它与名单长度也不该相等。
    """
    _write_many_accounts(settings, 9)
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("get_ops_report", {"name": "many-accounts"}),
    )
    freshness = _ok(outcome, 1)["freshness"]

    assert freshness["cells"] == 63, "九个主体 × 七个面"
    assert freshness["stale_count"] == 63
    assert len(freshness["stale"]) == 50
    assert freshness["stale_truncated"] == 13
    assert freshness["stale_by_aspect"][0] == ["source_coverage", 9], (
        "计数算的是全部，不是名单里那五十行"
    )
    assert sum(count for _aspect, count in freshness["stale_by_aspect"]) == 63


# ----------------------------------------------------------------------
# §B22 · 接线之后：同一批页面在一轮 tick 里只取一次
# ----------------------------------------------------------------------

#: 两个页面面共同要复核的那一批页面。域名属于 ``example-one``（清单里唯一有域名的
#: 那一行），而站点是 ``.co.uk`` 下的合成名字 —— 一个不指向任何真站点的主机名。
_PAGE_OLD = "https://example-one.co.uk/contact"
_PAGE_NEW = "https://example-one.co.uk/team"

#: 逐页的字节。第一行是标题（``PageParser`` 的约定），后面是正文 —— 两页的内容
#: **不同**，所以它们不是同一页的两种写法（否则「按 URL 去重」那条判据测不到东西）。
_PAGE_OLD_BODY = b"Contact Us\nGrounds manager: Emily Stone\n"
_PAGE_NEW_BODY = b"Our Team\nThe grounds team at Example One\n"

#: 两份证据的取回时刻：**都远在窗口之外**（页面面 90 天），所以这一轮必须真的去取。
#: 两个时刻不同，是为了让「先看哪一页」这条次序有确定的答案：旧的先看。
_SEEDED_OLD = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
_SEEDED_NEW = datetime(2025, 6, 2, 9, 0, tzinfo=timezone.utc)

_PAGE_ASPECTS = ("contact_person_role", "email_evidence")


def _seed_pages(
    settings: Settings, subject_id: str, pages: tuple[tuple[str, bytes, datetime], ...]
) -> None:
    """把「我们记得这几页」写进证据库 —— 页面复核读的就是这些行。

    走的是**生产的存储**（``SqliteEvidenceStore``），不是内存替身：这一条要证的
    正是「库里有这些行时，tick 会怎么做」，而把库换掉会让「库这一层答得出这个
    问题吗」变成第二个被测量对象（``application`` 那一层已经单测过它）。

    ``workspace`` 用 ``settings.profile`` —— 与 ``create_scene`` 落下的 Scene 逐字
    同一个值（那是 ``record.workspace``，复核用的也是它）。写成别的值会让这些行属于
    另一个工作区，于是这一格名下「一页都没有」。
    """
    store = SqliteEvidenceStore(settings.database_path)
    for index, (url, raw, fetched_at) in enumerate(pages):
        store.put_evidence(
            Evidence(
                workspace=settings.profile,
                evidence_id=f"seed-{index}",
                account_id=subject_id,
                source_url=url,
                source_kind=SourceKind.FETCHED_HTML,
                literal_value="Grounds manager",
                content_sha256=hashlib.sha256(raw).hexdigest(),
                fetched_at=fetched_at,
                field=ContactField.EMAIL,
                parser="html",
            )
        )


@pytest.fixture
def page_session_factory(settings: Settings, tmp_path: Path):
    """一个**接上了抓取**的产品环会话。返回 ``(run, http)``。

    与 ``session_factory`` 只差那三样接线，而它们正是这一片新接上的那一层：

    * ``acquisition`` —— ``default_acquisition(settings, http=…)``，抓取器换成
      ``FakeHttp``（离线）。浏览器那一侧**关掉**（``allow_browser=False``）：这一条
      问的是编排，而一次真的浏览器拉起会让它在 CI 上时红时绿。
    * ``router`` —— ``DocumentRouter([PageParser()])``。用替身而不是真 HTML 解析器：
      真解析器对这几行合成字节给出的答案取决于它自己（多半是「解析不了」），那时红的
      是解析器形状而不是编排。
    * ``documents`` —— **真的** ``default_document_budget(settings)``，刻意不换。
      它把「上限从哪来」接在**这份部署的 profile** 上：于是「一次复核最多抓 12 份」
      这句话在这一条里是从 ``profiles/`` 那一份文件读来的，而不是从测试里写下来的。

    ``http`` 要交回给调用方：``bodies`` 得在跑之前按测试摆好，而 ``calls`` 是这一条
    最承重的那个断言（「取了几次」只在那里读得出来）。
    """
    http = FakeHttp({}, root=settings.mirrors_dir / "page-session")
    run = _controller_session(
        tmp_path,
        settings,
        acquisition=default_acquisition(settings, http=http, allow_browser=False),
        router=DocumentRouter([PageParser("html")]),
        documents=default_document_budget(settings),
    )
    return run, http


def test_a_wired_tick_fetches_each_page_once_for_both_page_aspects(
    page_session_factory, settings: Settings
) -> None:
    """**关闭条件之一**：一轮 tick 里两个页面面共用同一批页面 ⇒ 每一页恰好抓一次。

    这是这一片最贵的那个数，而它只在 ``http.calls`` 上读得出来：抓两次的那一轮，
    账上写着「看了 2 页 × 2 面 = 4 页」，而实际只有两页 —— 它不会表现为一个错误，
    只会表现为一个**更好看的覆盖数字**。所以判据是逐次记账的**长度**，不是
    「有没有报错」。

    同时钉住的还有两件事：

    * 两个面这一轮**都做成了**，而其中一个的结论是「与本轮另一个面共用」——
      ``REUSED`` 不是失败，也不是「没变」：它是「这一趟没为它花钱」。
    * 库里那两页的指纹与本轮取回的**逐字节相同**，所以是 ``UNCHANGED`` 而不是
      ``CHANGED``：变了就得落新证据，而这一条不该顺手改库。
    """
    _write_accounts_list(settings)
    _seed_pages(
        settings,
        "example-one",
        (
            (_PAGE_OLD, _PAGE_OLD_BODY, _SEEDED_OLD),
            (_PAGE_NEW, _PAGE_NEW_BODY, _SEEDED_NEW),
        ),
    )
    run, http = page_session_factory
    http.bodies.update({_PAGE_OLD: _PAGE_OLD_BODY, _PAGE_NEW: _PAGE_NEW_BODY})

    outcome = run(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
        ("get_ops_report", {"name": "known-accounts"}),
    )

    tick = _ok(outcome, 1)
    assert tick["claimed_count"] == 9, "三个主体 × 三个在本部署范围内的面"
    assert http.calls == [_PAGE_OLD, _PAGE_NEW], (
        "两页各取一次 —— 不是 2 页 × 2 面 = 4 次；次序是「最久没看的先看」"
        "（两份证据的取回时刻不同，所以这条次序有确定答案）"
    )

    page_done = [claim for claim in tick["done"] if claim["aspect"] in _PAGE_ASPECTS]
    assert len(page_done) == 2
    assert {claim["subject_id"] for claim in page_done} == {"example-one"}
    assert {claim["outcome"]["state"] for claim in page_done} == {"unchanged", "reused"}
    assert {claim["outcome"]["note"] for claim in page_done} == {
        "2 页的指纹与记录一致。",
        "shared_with_another_aspect_in_this_tick",
    }
    for claim in page_done:
        assert claim["outcome"]["kind"] == "recheck"
        assert claim["outcome"]["page_set_size"] == 2
        assert claim["outcome"]["examined"] == 2, "两页都有了结论，才给整格续期"
        assert claim["outcome"]["changed_pages"] == []
        assert claim["outcome"]["evidence_ids"] == [], "没变就一行新证据都不落"

    # 另外两个主体的四个页面格**没看成**：它们名下一条证据都没有。这与「缺接线」是
    # 两句不同的话，各自的理由在下面那一条里。
    assert len(tick["failed"]) == 4
    assert {claim["subject_id"] for claim in tick["failed"]} == {"example-two", "example-three"}
    assert all(
        claim["outcome"]["note"] == "这一格名下没有可复核的页面。"
        for claim in tick["failed"]
    )
    # 六个页面格各自的结论都在这张表上（含没看成的四格）—— 它是「这一轮花了抓取的
    # 钱没有」的那个数：只有 ``unchanged`` 那一格真的抓了两次里的第二页以外的部分，
    # 而 ``reused`` 那一格一分钱没花。
    assert tick["rechecks_by_state"] == [
        ["reused", 1],
        ["unchanged", 1],
        ["unreachable", 4],
    ]

    # 五条观测：三个主体的来源覆盖，外加 example-one 的两个页面面 —— **含复用的那
    # 一格**。它写观测是有判据的：本轮有人在共享那个批次之前真的看过这些页面，而那一
    # 趟的观察时刻就是这一轮（见 ``RecheckState.REUSED`` 的 docstring）。
    assert len(tick["observations"]) == 5
    assert [row["aspect"] for row in tick["observations"]].count("source_coverage") == 3
    assert sorted(
        row["aspect"] for row in tick["observations"] if row["aspect"] != "source_coverage"
    ) == list(_PAGE_ASPECTS)

    report = _ok(outcome, 2)
    assert report["ticks"]["by_state"] == {"done": 5, "failed": 4}
    assert report["page_rechecks"]["by_state"] == [
        ["reused", 1],
        ["unchanged", 1],
        ["changed", 0],
        ["unreachable", 4],
        ["unreadable", 0],
    ]
    # 三样接线这一份都接上了 ⇒ ``missing_wiring`` 是空的。它与 ``coverage_gaps``
    # （那四个面本部署没实现）是两件事：一个部署可以接线齐备而实现不全。
    assert report["coverage"]["missing_wiring"] == []
    assert report["coverage"]["coverage_gaps"] == [
        "asset_identity",
        "asset_attribute",
        "account_relation",
        "product_fit",
    ]
    # 手上真的有可绑定来源的只有一个主体 —— 而「页面没变」不是它的来源：
    # 来源那一节数的是本地证据行，两个页面面答的是另一件事。
    #
    # 名单的次序是 ``(subject_kind, subject_id)`` 的**字典序**（不是清单的文件序）：
    # 这一节答的是「哪些主体没来源」，与「这一轮按什么次序跑」是两件事 ——
    # 按文件序排会让读者以为它是那一份名单的次序，而文件换了它就换。
    assert report["source_supply"]["subjects_with_bindable_source"] == 1
    assert [
        row["subject_id"] for row in report["source_supply"]["subjects_without_any_bindable_source"]
    ] == ["example-three", "example-two"]


def test_pages_without_the_wiring_are_reported_per_page_not_skipped(
    session_factory, settings: Settings
) -> None:
    """同一个「名下有页面」的局面，换一个**没接抓取**的部署。

    页面集非空 ⇒ 这一格走到了「接线齐不齐」那一步（它排在「有没有页面可看」之后，
    所以只有名下有页面的主体才到得了）。三样都缺就三样都点名，逐页写
    ``not_wired:<名字>``，外加 ``coverage.missing_wiring`` —— 于是「没接」是可读的，
    而不是静默跳过。

    这一条的**反面**是「把没接的格子划进 ``out_of_scope`` 让它消失」：那正是 §11
    逐字禁止的（未接的 aspect 报 coverage gap，不能通过 out_of_scope 把必需功能从
    验收分母删掉）。九个格子照样被认领，其中六格挂着失败，而**一格都没有**落进
    ``out_of_scope``（那里只有那四个本部署根本没实现的面）。
    """
    _write_accounts_list(settings)
    _seed_pages(
        settings,
        "example-one",
        (
            (_PAGE_OLD, _PAGE_OLD_BODY, _SEEDED_OLD),
            (_PAGE_NEW, _PAGE_NEW_BODY, _SEEDED_NEW),
        ),
    )
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
    )

    tick = _ok(outcome, 1)
    assert tick["claimed_count"] == 9, "没接线也照样认领 —— 认领之后才谈得上做成做不成"
    assert [claim["aspect"] for claim in tick["done"]] == ["source_coverage"] * 3

    unwired = [claim for claim in tick["failed"] if claim["subject_id"] == "example-one"]
    assert sorted(claim["aspect"] for claim in unwired) == list(_PAGE_ASPECTS)
    for claim in unwired:
        assert claim["outcome"]["state"] == "unreachable"
        assert claim["outcome"]["note"] == "缺接线：acquisition、router、documents"
        assert [page["deferred"] for page in claim["outcome"]["pages"]] == [
            "not_wired:acquisition,router,documents"
        ] * 2, "缺的三样**全列出来**，不是只报第一个"
        assert claim["failure"].startswith("复核页面事实要的四样接线里缺 ")
        assert claim["state"] == "failed" and claim["attempt"] == 1

    # 剩下四格是另外两个主体：它们名下没有页面，那是**另一句**理由。
    assert len(tick["failed"]) == 6
    assert tick["rechecks_by_state"] == [["unreachable", 6]]
    assert tick["coverage"]["missing_wiring"] == ["acquisition", "router", "documents"]
    assert tick["coverage"]["wiring_meaning"]["acquisition"]
    assert tick["out_of_scope_count"] == 12, "四个未实现的面 × 三个主体 —— 页面那六格不在这里"

    # 一趟没看成的复核**不写观测**：写一条就等于把一次失败读成一次刚验证过。
    assert len(tick["observations"]) == 3
    assert {row["aspect"] for row in tick["observations"]} == {"source_coverage"}


def test_only_the_page_that_changed_gets_new_evidence(page_session_factory, settings: Settings) -> None:
    """**关闭条件之四**：一页真的变了 ⇒ 那一页落新证据，另一页一行都不动。

    这一条把上一条里那个 ``changed_pages: []`` 变成一次**非空**的读数：一个永远
    读得出来的空列表与一个真的空列表在报告上一模一样，而它们说的是两件相反的事
    （「这一轮没有一页变」与「这一格没有人算过哪些页变了」）。所以这一条要的不是
    「没报错」，是那条记录里**点得出是哪一页**。

    「只更新受影响的对象」的那一半落在这个断言上：**没变的那一页在库里仍是原来那
    一行**（同一个 evidence_id、同一个指纹）。一次「整批重写」会让两页各多一行，
    而报告上只会显示「这一轮落了新证据」—— 那正是复核记录里 ``observed`` 与
    ``expected`` 分两格的原因。
    """
    _write_accounts_list(settings)
    _seed_pages(
        settings,
        "example-one",
        (
            (_PAGE_OLD, _PAGE_OLD_BODY, _SEEDED_OLD),
            (_PAGE_NEW, _PAGE_NEW_BODY, _SEEDED_NEW),
        ),
    )
    run, http = page_session_factory
    # 只有 ``/contact`` 那一页变了；``/team`` 逐字节与库里的那行一致。
    http.bodies.update({_PAGE_OLD: b"Contact Us\nGrounds manager: J. Okafor\n"})
    http.bodies[_PAGE_NEW] = _PAGE_NEW_BODY

    outcome = run(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("run_maintenance", {"name": "known-accounts"}),
    )

    tick = _ok(outcome, 1)
    assert http.calls == [_PAGE_OLD, _PAGE_NEW]

    page_done = [claim for claim in tick["done"] if claim["aspect"] in _PAGE_ASPECTS]
    assert len(page_done) == 2
    assert {claim["outcome"]["state"] for claim in page_done} == {"changed", "reused"}
    changed = next(
        claim for claim in page_done if claim["outcome"]["state"] == "changed"
    )
    assert changed["outcome"]["changed_pages"] == [_PAGE_OLD], (
        "变的那一页要点得出来 —— 而且只点那一页"
    )
    assert changed["outcome"]["page_set_size"] == 2
    assert changed["outcome"]["examined"] == 2, "另一页有结论（没变），所以整格看全了"
    assert changed["outcome"]["evidence_ids"], "变了就得落新证据 —— 落不下去是错误不是「变了」"

    # 库里那一页的行数变了、另一页没有。判据落在**逐 URL** 上：整批重写会让两页各多
    # 一行，而那时的报告与这一条长得一样（都是「这一轮落了新证据」）。
    store = SqliteEvidenceStore(settings.database_path)
    rows = store.list_evidence("example-one")
    by_url: dict[str, list[Any]] = {}
    for row in rows:
        by_url.setdefault(row.source_url, []).append(row)
    assert len(by_url[_PAGE_NEW]) == 1
    assert by_url[_PAGE_NEW][0].evidence_id == "seed-1"
    assert by_url[_PAGE_NEW][0].content_sha256 == hashlib.sha256(_PAGE_NEW_BODY).hexdigest()
    landed = [row for row in by_url[_PAGE_OLD] if row.evidence_id != "seed-0"]
    assert landed, "变了的那一页要落新行"
    assert {row.content_sha256 for row in landed} == {
        hashlib.sha256(b"Contact Us\nGrounds manager: J. Okafor\n").hexdigest()
    }, "落下的指纹是**这一趟取回来那一份**的，不是库里那行的"

    assert tick["rechecks_by_state"] == [
        ["reused", 1],
        ["changed", 1],
        ["unreachable", 4],
    ]


# ----------------------------------------------------------------------
# §B22 · 产出读回来时「它是哪一种」由记录自己说
# ----------------------------------------------------------------------


class _Claim:
    """只带 ``_outcome_row`` 读得到的那一格 —— 它读的就是 ``result_ref``。"""

    def __init__(self, result_ref: str) -> None:
        self.result_ref = result_ref


def _hand_written(root: Path, name: str, **fields: Any) -> Path:
    """手写一份合成记录：内容与它自己声明的摘要**真的相符**。

    摘要按 ``marketops/application/refresh.py::content_digest`` 算，文件名里带上它
    （``_read_verified`` 的第一条核对就是这一条）。于是这几份文件会**通过**全部三次
    核对 —— 这一条要测的正是「核对都过了、而它是第三种」那个局面：它不是损坏。

    刻意不写 ``taken_at`` / ``new_source_gaps`` / ``not_measured``（那三样不进摘要，
    见 ``_PROVENANCE_KEYS``）：手写一份摘要算错的记录会让这一条变成在测「损坏」，
    而损坏那一路由下面第二条钉住（文件根本不在）。
    """
    payload = {"kind": fields.pop("kind"), **fields}
    digest = content_digest(payload)
    path = root / f"{name}.{digest}.json"
    payload["digest"] = digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def test_the_outcome_row_reads_the_kind_the_record_itself_declares(tmp_path: Path) -> None:
    """五种产出各读回一句不同的话，而**分派读的是记录自己写的 ``kind``**。

    ``result_ref`` 只是一条路径，而同一个目录下住着两种记录。按回执上的 ``aspect``
    猜是错的（B22 之后两者不再一一对应），先试一种、失败再试另一种也是错的（那样
    「读不出来」就不再是结论，而是「两种都不是」的余数）。

    第三格是这一条的要害：一份**通过了摘要核对**、而 ``kind`` 谁也不认识的记录。
    它不是损坏，处置却相同 —— **不许当成某一种来读**。缺省成盘点的话，同一份文件会
    在报告里变成「这一格有 0 行证据」，一句它从没说过的话。
    """
    census = _hand_written(
        tmp_path,
        "census--acct--source_coverage",
        kind="census",
        workspace="ws",
        store_present=True,
        rows=0,
    )
    recheck = _hand_written(
        tmp_path,
        "recheck--acct--email_evidence",
        kind="recheck",
        workspace="ws",
        state="changed",
        page_set_size=2,
        examined=2,
        pages=[
            {"url": "https://example.co.uk/contact", "cause": ["content"]},
            {"url": "https://example.co.uk/team", "cause": []},
        ],
        evidence_ids=["e-new"],
    )
    foreign = _hand_written(tmp_path, "ledger--acct", kind="ledger", entries=3)

    # 1. 没有产出引用 —— 抛异常那一支没有 result_ref，而那不是「读不出来」。
    assert _outcome_row(_Claim("")) == {
        "readable": False,
        "reason": "这一格没有产出引用（result_ref 为空）。",
    }

    # 2. 引用了、但指不到东西 —— 损坏。**不是 0 个缺口。**
    missing = _outcome_row(_Claim(str(tmp_path / "not-there.json")))
    assert missing["readable"] is False
    assert "读不出来" in missing["reason"]

    # 3. 摘要过了、而它是第三种。
    unknown = _outcome_row(_Claim(str(foreign)))
    assert unknown["readable"] is False
    assert unknown["kind"] == "ledger", "它自己写的那个字要**逐字**报出来"
    assert "ledger" in unknown["reason"]
    assert "0 个缺口" not in unknown["reason"]

    # 4. 两种认得的分派到各自的形状上。
    assert _outcome_row(_Claim(str(census)))["kind"] == "census"
    assert _outcome_row(_Claim(str(census)))["rows"] == 0
    landed = _outcome_row(_Claim(str(recheck)))
    assert landed["kind"] == "recheck"
    assert landed["state"] == "changed"
    # 变了的那几页**由记录自己逐页说的 ``cause`` 得出**（与 ``PageRecheck.changed``
    # 同一条判据）—— 一个恒为 None 的空位分不出「没有一页变」与「没人算过」。
    assert landed["changed_pages"] == ["https://example.co.uk/contact"]
    assert landed["pages"][1]["cause"] == [], "没变的那一页在记录里也逐页写着"


# ----------------------------------------------------------------------
# §B22 · 生效版本这一格自己也分三态
# ----------------------------------------------------------------------


def test_an_active_scene_whose_effective_version_is_gone_is_refused(
    session_factory, settings: Settings, monkeypatch
) -> None:
    """记录说有生效版本、而库里读不到那一版 ⇒ **拒绝**，不是「还没开张」。

    B22 之前这一支与 ``DRAFT`` 落在同一个 ``None`` 上：报告给出 ``refresh: null``、
    ``ticking: false``，读起来是「这个市场还没开张」。而它开着 —— 只是此刻正在执行
    的那份规则不见了（库被改过、迁移做了一半、库被换过）。一句自洽而错误的话比一条
    拒绝糟，因为**没有人会去查它**。

    反过来那一半（记录自己说没有生效版本）**不是**拒绝：那是一条正常的开局状态，
    报告要说的是「去 activate」（见 ``test_a_draft_scene_has_a_record_but_no_cadence``）。
    两条一起在，才说明这里分的是两件事而不是「一律报错」。
    """
    from marketops.adapters.sqlite.scenes import SqliteSceneStore

    _write_accounts_list(settings)
    monkeypatch.setattr(
        SqliteSceneStore, "get_version", lambda self, scene_id, version: None
    )
    outcome = session_factory(
        ("create_scene", {**BASE_CREATE, "activate": True}),
        ("get_maintenance_plan", {"name": "known-accounts"}),
    )

    assert not outcome.calls[0].is_error, "立项与激活读不到版本，所以这一条真的能跑到那里"
    text = _refused(outcome, 1)
    assert "第 1 版" in text, "拒绝要说得出**哪一版**读不到"
    assert "还没有生效版本" in text and "读不出来" in text, (
        "两句不同的话都要在拒绝里出现 —— 只给其中一句，读的人分不出这是哪一种"
    )

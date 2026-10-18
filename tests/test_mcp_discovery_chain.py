"""§1 的验收：**回执 → 场地 → canonical 账号 → 研究件的主语**，一条 MCP 链走通。

## 这一条要证明的只有一句话

``research_work_item.account_id`` 与 ``market_account.account_id`` 是**同一个空间**。

在这一轮之前它们没有交点：研究件的主语是 ``row-<hex16>``（「这是一份工件里的第几
行」），而 ``market_account.account_id`` 是 operator 写在清单里的 slug。于是整条链
**看起来**跑通了 —— 检索真的发生了、证据真的落了盘、路线真的算出来了 —— 而
``get_account`` 一个账号都找不到。两份报告各自都正常，交点为空。

那正是「把一个东西在某份材料里的位置当成它本身」的失效形态：工件一重排，``row-*``
就换了，而一个组织没有换。所以这一条判据的主语不是「接口通不通」，是**那两个 id
是不是同一个东西**。

## 为什么必须走 MCP 面，而不是直接调 application

``promote_receipt`` 与 ``submit_research`` 各自都是薄薄一层，而「它们接的是同一份
存储」这件事只有走一遍真的会话才看得见：中间件、``RoleGuard``、``tools/list`` 的
过滤、以及「这一次写下的，是不是下一次读得到的那一批」。直接调函数会把这些全绕过
去，而绕过去之后，一个把两份存储指向两个库的接线错误仍然会绿。

## 它按顺序做的五件事

1. 真的跑一轮发现（假检索 + 假 HTTP，**零网络、零 key**）—— 它写出工件与回执，
   并把证据落进这个部署的证据库；
2. 从 MCP 面立项并激活那个 Scene（受理要看它，见 ``tools._scene_gate``）；
3. 从 MCP 面折那份回执（``promote_receipt``）—— 场地与 **canonical 账号**一起
   写进去；
4. 从 MCP 面受理一次研究（``submit_research``，``input_kind="discovery_scope"``）；
5. 把第 4 步的工作单元主语与第 3 步写下的账号逐个对账，并用 ``get_account``
   把每一个都读回来。

## 这一条不是「每样东西各测一遍」

每一步各自的判据已经在别处（``test_discovery_run`` / ``test_application_promotion``
/ ``test_mcp_product_loop`` / ``test_mcp_job_tools``）。这里一条都不重测 —— 它测的
是**只有把五步串起来才会出现**的那一件事，而它可以单独失败：任何一步的 id 派生规则
漂移一处，这一个文件就红，而上面四个文件全都照旧绿。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.application.discovery_run import materialize_pool
from marketops.application.openweb import build_open_web_queries
from marketops.config import Settings
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_asset_store,
    default_maintenance_store,
    default_relation_store,
    default_scene_store,
)
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload
from tests.test_discovery_run import (
    _GARDEN,
    _Replies,
    _bundle,
    _hit,
    _run,
    _scope,
    _site_page,
)

#: 这一轮发现的名字。它同时是工件的名字，也就是 ``submit_research`` 的 ``input_ref``
#: （见 ``inputs.discovery_scope_subjects``：范围名按 ``artifact_ref`` 反查回执）。
SCOPE = "discovery-chain-test"

#: 冻结的区域。发现那一轮与立项那一份**都**要写它，而它们今天必须一致 —— 否则
#: 报告上会出现一个「按 Devon 找的场地、立在 Somerset 的市场」的形状，而两半各自
#: 都正常。
REGION = "Devon"

#: 立项。与 ``test_mcp_product_loop.BASE_CREATE`` 同形，但不从那里 import ——
#: 那一份是**产品环**那条链的输入，这一份是 §1 这条链的。两者今天恰好一样，
#: 抄一份进来会让「某一处放宽了立项要求」在这个文件里静默地不成立。
CREATE: dict[str, Any] = {
    "region": [REGION],
    "scenario": "natural-grass",
    "product": "acme",
    "role_policy": "role-matrix-v1",
    "source_plan": ["serper-search"],
    "budget": {"search": 20},
}


def _ok(call) -> dict[str, Any]:
    """一次调用必须成功，并返回它的结构化结果（失败时带上工具自己那句话）。"""
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _search_query_text(settings: Settings) -> str:
    """profile 真实展开出来的**第一条**检索式。

    不硬编一条文本：检索式的形状由 ``openweb`` 与 profile 共同决定，抄一条进来会让
    这些判据在词表变化时静默地测一个不存在的检索式（它回空结果，而一切都绿）。
    与 ``test_discovery_run.query_text`` 同一份来源 —— 那一份是那条链的夹具，
    这一份是这条链的。
    """
    plan = build_open_web_queries(_bundle(settings), frozen_region=REGION)
    return plan.queries[0].text


@pytest.fixture
def discovered(settings: Settings) -> tuple[str, str]:
    """跑一轮发现，返回 ``(范围名, 回执 id)``。

    零网络：检索是 ``_Replies`` 摆的一张表，HTTP 是 ``FakeHttp``。而它写下的证据是
    **真的** —— 真的过 ``assess_http_response``、真的落进 ``settings.database_path``
    那个库、真的带着 ``workspace=settings.profile``。所以下一步折进去的时候，那些
    证据读得回来（这正是「算得出账号 id」与「池子里真有那一行」能分开的前提）。
    """
    query = _search_query_text(settings)
    run = _run(
        settings,
        scope=build_scope(),
        search=_Replies({query: [_hit(_GARDEN, query=query)]}),
        replies={_GARDEN: _site_page("Example Garden Club")},
    )
    pool = materialize_pool(settings.inputs_dir, run)
    assert pool is not None, "这一轮没有产出工件 —— 上面那一条命中没被折进池子"
    assert pool.receipt.artifact_ref == SCOPE
    return SCOPE, pool.receipt.receipt_id


def build_scope():
    """这一轮的冻结范围。

    直接用 ``test_discovery_run`` 的构造器（而不是在这里重写一遍 ``DiscoveryScope``）：
    两份范围定义会在某一天的一个默认值上分叉，那时两边各自都正常，而比较出来的差别
    是**夹具**的差别，不是实现的。
    """
    return _scope(scope=SCOPE, region=REGION)


@pytest.fixture
def session(settings: Settings, tmp_path: Path):
    """一个 controller 会话：产品环 + 控制面（``submit_research``）**接同一份存储**。

    三样存储只建一次并交给两个地方，与组合根 ``interfaces/mcp/__main__.py`` 同款：

    * ``scenes`` —— 产品环那三条工具与 ``get_scene`` / ``_scene_gate`` 共用。两处各
      建一份的话，「刚立项」与「读不到那个场景」可能来自两个不同的库。
    * ``jobs`` —— 工厂形式（每次调用开一条新连接）。**这一个每次调用都是新的**，
      而这正是「持久」要被真的测到的形状：同一个幂等键在第二段会话里仍然指向同一个
      作业。
    """
    token, digest = mint("claude-control", ["controller"])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, "claude-control", ["controller"]))),
        encoding="utf-8",
    )
    scenes = default_scene_store(settings)

    def run(*calls: tuple[str, dict[str, Any]]):
        outcome = drive_with_settings(
            settings=settings,
            tokens=TokenRing.from_file(ring_path),
            token=token,
            product_loop=product_loop_handlers(
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
            ),
            scenes=scenes,
            jobs=lambda: SqliteJobStore(settings.database_path),
            calls=calls,
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    return run


def test_the_research_subject_is_the_canonical_account_the_pool_holds(
    session, settings: Settings, discovered: tuple[str, str]
) -> None:
    """**§1 的判据**：研究件的主语就是账号池里那一行，两处是同一个值。

    这条链上被断言的是**两个集合相等**，不是「都不为空」：两边各有一个账号而它们
    不同（``row-…`` 与 slug 那种形状）恰好是这一轮要消灭的那件事，而「都不为空」
    对它毫无反应。
    """
    scope, receipt_id = discovered

    outcome = session(
        ("create_scene", CREATE),
        ("control_scene", {"action": "activate", "version": 1}),
        ("promote_receipt", {"receipt_id": receipt_id}),
        ("list_account_pool", {}),
        (
            "submit_research",
            {
                "scene_id": settings.profile,
                "input_kind": "discovery_scope",
                "input_ref": scope,
                "idempotency_key": "discovery-chain-1",
            },
        ),
    )

    # --- 1. 折回执：场地与账号一起写进去 ---------------------------------
    promoted = _ok(outcome.calls[2])["promotion"]
    assert promoted["counts"] == {"promoted": 1, "asset_only": 0, "rejected": 0}, (
        "发现那一轮的回执行带着取回来的材料，所以它折得出账号 —— 一份只折得出"
        "asset_only 的回执（母池那种形状）说明证据没有跟着一起进来"
    )
    assert (promoted["assets_before"], promoted["assets_after"]) == (0, 1)
    assert (promoted["accounts_before"], promoted["accounts_after"]) == (0, 1)

    # --- 2. 账号池那一侧：账号 id 是**派生**的，不是 ``row-…`` -----------
    pool = _ok(outcome.calls[3])
    assert pool["account_count"] == 1
    (account,) = pool["accounts"]
    account_id = account["account_id"]
    assert account_id.startswith("acct-"), (
        f"账号 id 应当是 canonical 派生的（acct-<hex16>），拿到的是 {account_id!r} —— "
        "一个 row-… 形状的主语说的是「某份工件里的第几行」，而工件一重排它就换了"
    )

    # --- 3. 受理：主语就是上面那一行 --------------------------------------
    submitted = _ok(outcome.calls[4])
    assert submitted["state"] == "queued", (
        f"作业被阻塞了：{submitted['reason']}\n"
        "（这一句在 §1 这条链上最常见的成因是：折进去的账号与研究件算出来的主语"
        "不是同一个值，于是 unpromoted_subjects 整份拒绝）"
    )
    assert submitted["work_items"]["count"] == 1

    # ``subject_ref`` **就是** ``research_work_item.account_id``
    # （``compile_work_items`` 拿 dossier 的 account_id 当主语）。
    (item,) = SqliteJobStore(settings.database_path).work_items(submitted["job_id"])
    assert item.subject_ref == account_id, (
        f"研究件的主语是 {item.subject_ref!r}，而账号池里那一行是 {account_id!r} —— "
        "这正是本轮要消灭的那个空集：检索会发生、证据会落盘，而 get_account 找不到它"
    )

    # --- 4. 用 MCP 面把它读回来 -------------------------------------------
    # 前三条断言的是**值相等**，这一条断言的是**读得回来**：一个值相等但按 id 查不
    # 到的池子，在报告上与前三条完全一样。
    read_back = _ok(
        session(("get_account", {"account_id": item.subject_ref})).calls[0]
    )
    assert read_back["account_id"] == account_id
    assert read_back["evidence_count"] > 0, (
        "账号是「有证据所以存在」的（MarketAccount 那条『没有证据的账号不落盘』），"
        "所以读回一个 evidence_count 为 0 的账号说明折的时候证据没跟着重新锚定"
    )


def test_a_receipt_that_was_never_folded_blocks_instead_of_running_on(
    session, settings: Settings, discovered: tuple[str, str]
) -> None:
    """**负控**：跳过第 3 步，受理必须**拒绝**，而不是按一个不存在的账号跑下去。

    少了这一条，上面那个文件里的绿灯是被「反正都能跑」满足的 —— 一个不核账号池的
    实现同样会让它全绿，而它证明不了「研究件的主语真的落进了池子」。

    拒绝必须是**整份**而不是跳过那几行：一个 work item 都不该建出来，所以没有任何
    一次检索会为它们发生（检索是要花钱的，而"跳过"在报告上看不见）。
    """
    scope, _ = discovered

    outcome = session(
        ("create_scene", CREATE),
        ("control_scene", {"action": "activate", "version": 1}),
        (
            "submit_research",
            {
                "scene_id": settings.profile,
                "input_kind": "discovery_scope",
                "input_ref": scope,
                "idempotency_key": "discovery-chain-unfolded",
            },
        ),
    )

    submitted = _ok(outcome.calls[2])
    assert submitted["state"] == "blocked"
    assert submitted["work_items"]["count"] == 0, (
        "被阻塞的作业手里不该有工作单元 —— 有的话，「整份拒绝」就没有做到"
    )
    assert "promote_receipt" in submitted["reason"], (
        "拒绝要说得出下一步该做什么：折一次那份回执。"
        f"拿到的是：{submitted['reason']}"
    )
    assert submitted["reason"].strip(), "阻塞理由不能是空的（空理由读起来像一次正常受理）"

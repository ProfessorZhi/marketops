"""§G0 的纵向验收：**两个** Workstream、**可选**产品上下文、跨任务复用。

## 这一条证的是 §G0 那一段

``docs/MEMBER_A_AUTOMATION_EXECUTION_PLAN.md`` 约 ``:365-372`` 逐字：

> 用两个 Workstream（其中一个**不绑定**产品、一个绑定**有批准出处 / 明确 fixture
> 身份的** Product/Offer）、其中一个 **Account-driven**、一个**共享 Account** 和一个
> **区域反例**：MCP 预览目标/范围/可选产品资料 → 批准 research → 持久 run。

| 原文 | 在这一份里落在哪一条上 |
|---|---|
| 1 旧 product 字符串迁移 | :func:`test_the_legacy_product_string_is_not_migrated_it_is_overridden` |
| 2 产品撤回 | :func:`test_a_withdrawn_claim_stops_being_read_as_an_approved_source` |
| 3 同一 Account 不同 Scene 评分 | **做不到** —— :func:`test_a_second_scene_is_refused_by_name` 钉住的是那条拦路的判据本身，理由写在那一份的 docstring 里 |
| 4 某任务暂停 / 另一任务继续 | :func:`test_pausing_one_workstream_leaves_the_other_and_the_runs_untouched` |
| 5 同源额度共享 | :func:`test_the_two_workstreams_resolve_one_declared_budget_source`（**结构那一半**；花出去那一半 ``BLOCKED_EXTERNAL``，见模块末尾） |
| 6 launch 幂等 | :func:`test_a_replayed_launch_writes_no_second_row` |
| §2 无价格不猜价 | :func:`test_a_price_is_never_invented_and_never_becomes_a_scene_fact` |
| §2 资料 / claims 缺失 → draft / hold | :func:`test_a_missing_plan_returns_hold_and_a_bare_offer_stays_a_draft` |
| §2 纯身份研究不被迫创建 Offer | :func:`test_an_identity_only_research_is_never_forced_to_name_an_offer` |
| §3 未批不外调 | :func:`test_nothing_outbound_happens_and_the_ledger_stays_empty` |
| §3 不复制客户库 | :func:`test_the_two_workstreams_read_one_customer_pool_not_two_copies` |

## 判据**走一个真的 MCP**

形态照 ``tests/test_r1_vertical_acceptance.py``：装配是 ``interfaces/mcp/__main__._compose``
（与 ``python -m marketops.interfaces.mcp`` 起来的那个进程同一份），每一次调用从工具
进、从工具出。**不**手搓处理器表 —— 后者能过而生产起不来，是这一族测试最贵的那种假绿。

盘数的量具（``research_run`` / ``launch_record`` / ``workstream`` / ``budget_ceiling``
那几张表的行数）与回执**两个都要**：「预览顺手受理了一轮」这件事在回执上与「预览了一次」
同形，只有行数说得出来。

## fixture 身份（§4 那条不许越的线）

G0 是**功能验证**，不是一场公司业务立项授权。这一份里出现的每一个 Product / Offer /
Claim / Account 都是**这一份文件自己造的**：

* id 一律带 ``g0-fixture`` 前缀，名称里逐字写着 ``NOT A REAL PRODUCT`` / ``NOT A REAL
  OFFER``；
* 账号域一律是 RFC 2606 的保留域（``.example``），那份上游工件由
  :func:`_write_motherpool_export` 现写进本次 ``tmp_path`` 的 ``inputs_dir``；
* 它们**只**落在 ``tmp_path`` 那个数据根里，:func:`test_the_fixture_identities_live_only_in_this_module`
  把这件事量出来：``marketops/`` / ``profiles/`` / ``contracts/`` / ``docs/`` 里一个
  字都搜不到；
* ``status="ready"`` 的 ``approved_by`` 是**令牌里那个主体**（``_compose`` 的接线
  顺手写下的），不是某个真人的名字。

## 这一条**判不到**什么（写在这里，不写成一句自觉）

* **第 3 条（同一 Account 不同 Scene 评分）今天做不到。** ``_shared.require_scene``
  只认 ``settings.profile``，``profiles/`` 里也只有一份 profile —— 一个部署只服务一个
  Scene（见 :func:`test_a_second_scene_is_refused_by_name`）。
* **第 5 条花出去的那一半做不到。** 「两个 Workstream 共用一个额度源 ⇒ 花的总量不翻倍」
  要一次**真的预留**，而本机 ``serper_enabled`` 为假、``SERPER_ENDPOINT`` 是写死的、
  ``_compose`` 没有留下注入 ``SearchProvider`` 的口。所以这里只钉结构那一半，并把它
  记成 ``BLOCKED_EXTERNAL``（release 条件与证据在 ``G/REPORT.md``）。
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server import product_loop as _product_loop
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.application.discovery_run import candidate_id_for
from marketops.config import Settings
from marketops.domain.util import sha256_text, utc_now
from marketops.interfaces.mcp.__main__ import _compose
from marketops.security.tokens import mint
from tests.mcp_harness import drive, ring_entry, ring_payload
from tests.test_evidence_store_contract import make_evidence

#: 本部署的 Scene（``conftest.DEFAULT_PROFILE``）。``require_scene`` 只认它。
PROFILE = "uk-acme-natural-grass"

#: §4 那条线：这一份文件自己造的 Product / Offer / 说法 / 账号，**没有一个是盘上的**。
#: 前缀是判据的一部分（:func:`test_the_fixture_identities_live_only_in_this_module`），
#: 不是命名习惯。
FIXTURE_PREFIX = "g0-fixture"
PRODUCT_ID = f"{FIXTURE_PREFIX}-product"
OFFER_ID = f"{FIXTURE_PREFIX}-offer"
BARE_OFFER_ID = f"{FIXTURE_PREFIX}-offer-bare"
CLAIM_ID = f"{FIXTURE_PREFIX}-claim"
SECOND_SCENE_ID = f"{FIXTURE_PREFIX}-second-scene"

#: 老 ``SceneVersion.product`` 是一个**裸字符串**（这一句就是它的内容）。
LEGACY_PRODUCT_TEXT = "G0 fixture legacy free-text product"

#: 一份上游交进来的导出。两行、两家组织 —— 一行的话「池子里有几个账号」与「折进去了
#: 几个」是同一个数，而这一份要的正是它们**可以**不同（共享 Account 与区域反例都从
#: 这两行里读）。形状与 ``tests/test_r1_vertical_acceptance.py`` 的 ``ARTIFACT_LINES``
#: 逐字同源：``source_identity`` 写 ``site:<主机>``，``promote_receipt`` 只把发现那一轮
#: 写下的形状折成账号。
ARTIFACT_LINES: tuple[str, ...] = (
    '{"source_identity": "site:g0-example-school.example",'
    ' "name": "G0 Example School", "location": "Devon",'
    ' "official_domain": "g0-example-school.example"}',
    '{"source_identity": "site:g0-example-golf.example",'
    ' "name": "G0 Example Golf Club", "location": "Devon",'
    ' "official_domain": "g0-example-golf.example"}',
    # §G0 那个「区域反例」：一件**不在这一轮范围里**的场地（Scene 那一格写的是
    # ``GB-England-Devon``，而这一行在 Cornwall）。它不参与这一份的任何一次受理；
    # 它的作用是让「池子里有几个账号」与「这一轮范围里有几个」**可以**不是同一个数 ——
    # 少了它，「两个 Workstream 看到的是同一批」会与「池子里恰好只有两个」同义。
    '{"source_identity": "site:g0-example-cornwall.example",'
    ' "name": "G0 Example Cornwall Pitch", "location": "Cornwall",'
    ' "official_domain": "g0-example-cornwall.example"}',
)

#: ``product_loop`` 的源码文本。上面那条「池子那一族不收工作流这一格」的判据走的是
#: 源码而不是 ``inspect``：那两个处理器在 ``_compose`` 之后是**闭包**，闭包的签名里
#: 读得出参数名，而这一条要的正是参数表。
loop_source = inspect.getsource(_product_loop)

SRC_REFS: list[dict[str, Any]] = [
    {
        "artifact_id": f"{FIXTURE_PREFIX}-artifact",
        "locator": f"fixture://{FIXTURE_PREFIX}-artifact",
        "content_sha256": "0" * 64,
        "fetched_at": "2026-09-29T00:00:00+00:00",
        "media_type": "text/plain",
        "note": "G0 fixture source (NOT A REAL SOURCE)",
    }
]

#: 一次成立的立项。``product`` 那一格是**裸字符串** —— 它正是第 1 条要读的那一格。
#: 其余与 ``tests/test_mcp_cohorts.py`` 的 ``BASE_CREATE`` 同形；它是**测试输入**，
#: 真实那套住在 ``profiles/`` 里。
BASE_CREATE: dict[str, Any] = {
    "region": ["GB-England-Devon"],
    "scenario": "sports-surface-maintenance",
    "product": LEGACY_PRODUCT_TEXT,
    "role_policy": "role-policy-v1",
    "source_plan": ["mother-pool-import"],
    "budget": {"search": 20},
}

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
            "source": "g0-fixture-request",
        }
    ],
    "objective": "find accounts for the fixture research",
    "evidence_requirements": ["official_site"],
}


def _ok(outcome, index: int = -1) -> dict[str, Any]:
    """上一次调用的回执，**按错误通道先判**：``is_error`` 为真时把正文原样带出来。"""
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


def _refused(outcome, index: int = -1) -> str:
    call = outcome.calls[index]
    assert call.is_error, f"{call.name} 本该被拒，却成功了：{call.as_json()!r}"
    return call.text


def _rows(path: Path, table: str) -> int:
    """盘上那张表有几行。**这是「顺手做了一件事」唯一说得出口的量具。**"""
    with sqlite3.connect(path) as conn:
        try:
            return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
        except sqlite3.Error as exc:  # pragma: no cover - 表名写错时红在这里
            raise AssertionError(f"{table} 这张表读不出来：{exc}") from exc


# ----------------------------------------------------------------------
# 部署：真正的组合根
# ----------------------------------------------------------------------
@dataclass
class Deployment:
    """一个**真的**服务实例：``_compose`` 装配出来的那一份，外加一枚令牌。

    ## 为什么是 ``_compose``

    ``drive_with_settings`` 让每个测试自己列接线清单 —— 那是**装置**，不是**部署**：
    它把「组合根漏传了一个工厂」这一类缺陷挡在测试之外，而 §G0 要求的是「全程通过
    一个 MCP」。这一份走组合根，所以这一趟走通的链，生产上那个进程也走得通。

    ## 一枚令牌、两个角色

    ``controller`` 与 ``researcher`` 在同一枚令牌上：角色是**能力**边界，不是会话边界。
    环里没有 ``authorizer`` —— ``security/tokens.py`` 拒绝同一主体同时持 controller 与
    authorizer，而 §G0 那条链止于工作的受理，不签发对外授权。
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
        """一条**成功**的调用。``tool`` 起这个名字是有意的：``put_offer_version`` 自己
        就有一格叫 ``name``，两格同名会让一次调用变成一次 ``TypeError``。"""
        return _ok(self.run((tool, payload)))

    # -- 盘数（不是回执）------------------------------------------------------
    def rows(self, table: str) -> int:
        return _rows(self.settings.database_path, table)


@pytest.fixture
def deployment(settings: Settings, tmp_path: Path) -> Deployment:
    """``_compose`` 起来的一个实例。**数据根就是本次 ``tmp_path``。**"""
    assert not settings.database_path.is_file(), "干净数据根：这个库不该预先存在"
    token, digest = mint(f"{FIXTURE_PREFIX}-operator", ["controller", "researcher"])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(ring_payload(ring_entry(digest, f"{FIXTURE_PREFIX}-operator", ["controller", "researcher"]))),
        encoding="utf-8",
    )
    composed = _compose(replace(settings, mcp_tokens_path=ring_path))
    assert composed.blocker == "", composed.blocker
    return Deployment(settings=settings, server=composed.server, token=token)


# ----------------------------------------------------------------------
# 夹具的生产者（不是被测对象）
# ----------------------------------------------------------------------
def _write_motherpool_export(settings: Settings) -> None:
    """把上游那份导出落进 ``inputs_dir``。**工具收名字，不收路径。**"""
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    (settings.inputs_dir / "motherpool.jsonl").write_text(
        "\n".join(ARTIFACT_LINES) + "\n", encoding="utf-8", newline="\n"
    )


def _seed_discovery_evidence(settings: Settings) -> None:
    """发现那一轮取回的那几条证据。**上游产出的既有事实，不是这一层的动作。**

    ``promote_receipt`` 只在**有证据**时才写账号，而发现期的证据挂在**候选**身份上。
    这里用**生产的**那个函数（``candidate_id_for``）把它挂回去 —— 自己拼一个「看起来
    像」的 id 永远不会被读到，而报告上只会看到池子是空的。
    """
    store = SqliteEvidenceStore(settings.database_path)
    for line in ARTIFACT_LINES:
        host = json.loads(line)["source_identity"].split(":", 1)[1]
        store.put_evidence(
            make_evidence(
                evidence_id=f"evidence-{host}",
                account_id=candidate_id_for(host),
                source_url=f"https://{host}/",
                field=None,
                content_sha256=sha256_text(f"<html>{host}</html>"),
                parser="html",
            )
        )


def _scene(deployment: Deployment, **overrides: Any) -> dict[str, Any]:
    """一次成立的立项。``activate=True`` 是必须的：没有生效版本的 Scene 不接新活。"""
    return deployment.one("create_scene", **{**BASE_CREATE, "activate": True, **overrides})


def _register_fixture_catalog(deployment: Deployment, *, price: dict[str, Any] | None = None) -> None:
    """把夹具 Product / Offer / 说法登记进去。

    ``status="ready"`` 是本部署里**唯一**会写下 ``approved_by`` 的那一档（
    ``adapters/mcp_server/product_catalog.py::_verifier``）。``approved_by`` 写的是
    令牌里那个主体，而它不是某个人名 —— 见模块 docstring「fixture 身份」那一节。
    """
    deployment.one(
        "put_product_version",
        product_id=PRODUCT_ID,
        version=1,
        display_name="G0 fixture product (NOT A REAL PRODUCT)",
        owner=f"{FIXTURE_PREFIX}-owner",
        status="ready",
        capabilities=["fixture capability"],
        limits=["fixture limitation"],
        units="fixture unit",
        source_refs=SRC_REFS,
    )
    deployment.one(
        "put_claim",
        claim_id=CLAIM_ID,
        revision=1,
        text="G0 fixture claim (NOT A REAL APPROVED CLAIM)",
        source_ref=f"fixture://{FIXTURE_PREFIX}-artifact",
    )
    deployment.one(
        "put_offer_version",
        offer_id=OFFER_ID,
        version=1,
        name="G0 fixture offer (NOT A REAL OFFER)",
        purpose="fixture only: exercise the launch chain, never a real engagement",
        owner=f"{FIXTURE_PREFIX}-owner",
        status="ready",
        product_refs=[{"product_id": PRODUCT_ID, "version": 1}],
        markets=["GB"],
        locales=["en"],
        approved_claim_refs=[{"claim_id": CLAIM_ID, "revision": 1}],
        **({} if price is None else {"price": price}),
    )


def _two_workstreams(deployment: Deployment, **shared: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """在**同一个** ``scene_version`` 上启动两个 Workstream。

    两边的编译输入**逐字相同**，只有幂等键与名字不同 —— 这是这一份里最要紧的一处
    布置：冻结的有效场景是**按 ``(scene_id, version)`` 唯一**的，所以「两个
    Workstream」在今天只有这一种形态（编第二版 Scene 由 ``create_scene`` 拒掉，
    见 :func:`test_a_second_scene_is_refused_by_name`）。
    """
    cutoff = (utc_now() + timedelta(days=1)).isoformat()
    body = {
        "context": CONTEXT,
        "cutoff": cutoff,
        "scene_version": 1,
        "budget": {"search": 20},
        **shared,
    }
    first = _ok(
        deployment.run(
            (
                "start_workstream",
                {**body, "idempotency_key": f"{FIXTURE_PREFIX}-ws-one", "workstream_name": "g0 ws one"},
            )
        )
    )
    second = _ok(
        deployment.run(
            (
                "start_workstream",
                {**body, "idempotency_key": f"{FIXTURE_PREFIX}-ws-two", "workstream_name": "g0 ws two"},
            )
        )
    )
    return first, second


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["workstream_id"]: row for row in rows}


# ----------------------------------------------------------------------
# §4 · fixture 身份
# ----------------------------------------------------------------------
def test_the_fixture_identities_live_only_in_this_module() -> None:
    """§4 逐字「fixture 不变成真实已批准产品资料」。**这一条就是那句话的量具。**

    ## 为什么量的是「这一份文件之外一个字都搜不到」

    「它凭什么算 fixture」不能靠命名习惯答（一个叫 ``fixture`` 的真产品仍然是真产品），
    也不能靠「它写在测试里」（一个从盘上借来的对象也可以被写进测试）。答得出来的是：
    **这些 id 在整个实现面、profile 面、契约面、文档面上一次都不出现**。它们只存在于
    这一份文件与本次运行的 ``tmp_path`` 里，所以读到它们的人只可能是在跑这一份文件。

    反面也量：搜到的字符串里必须包含 ``NOT A REAL`` 那几句 —— 一个把 id 起的像夹具、
    内容却从别处抄来的对象会在这一条上留不下来。

    ## 量具自己活着

    下面同时断言**这一份文件里搜得到**。少了这一句，一个读不到任何文件的实现会
    「零命中」地全绿 —— 而那正是这一族判据最贵的一种假绿。
    """
    root = Path(__file__).resolve().parent.parent
    searched = ("marketops", "profiles", "contracts", "docs")
    hits: list[str] = []
    files = 0
    for name in searched:
        base = root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".md", ".yaml", ".yml", ".json"}:
                continue
            files += 1
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if FIXTURE_PREFIX in text or "g0-example-" in text:
                hits.append(str(path.relative_to(root)))

    assert files > 50, f"量具没读到文件（只读到 {files} 个）—— 这一条是空过的"
    assert hits == [], f"夹具身份出现在这一份文件之外：{hits!r}"
    mine = Path(__file__).read_text(encoding="utf-8")
    assert FIXTURE_PREFIX in mine and "NOT A REAL PRODUCT" in mine, (
        "这一份文件里搜不到夹具身份 —— 上面那句「零命中」是空的"
    )


# ----------------------------------------------------------------------
# 那条链：预览 → 批准 research → 持久 run
# ----------------------------------------------------------------------
def test_the_whole_g0_chain_runs_through_one_mcp(deployment: Deployment) -> None:
    """§G0 那句「MCP 预览目标/范围/可选产品资料 → 批准 research → 持久 run」。

    七格逐一走：立项 → 池子 → 登记夹具资料 → 预览启动（``await_approval``）→
    预览有效场景 → 两个 Workstream（一个**不点名**产品、一个点名）→ 读回两个 run。

    「持久 run」那一句在**两个量具**下才成立：回执上有 ``run_id``，而盘上
    ``research_run`` 真的多了一行。只比回执会放过一条「编了个 run_id 回了你」的实现。
    """
    created = _scene(deployment)
    assert created["scene"]["state"] == "active", created

    _write_motherpool_export(deployment.settings)
    _seed_discovery_evidence(deployment.settings)
    imported = deployment.one("import_motherpool", artifact_ref="motherpool")
    promoted = deployment.one(
        "promote_receipt", receipt_id=imported["receipt"]["receipt_id"]
    )
    assert promoted["promotion"]["counts"]["promoted"] == len(ARTIFACT_LINES), promoted

    pool = deployment.one("list_account_pool")
    accounts = tuple(row["account_id"] for row in pool["accounts"])
    assert len(accounts) == len(ARTIFACT_LINES), pool

    _register_fixture_catalog(deployment)

    assert deployment.rows("research_run") == 0, "预览之前就有 Run 了"
    preview = deployment.one(
        "preview_launch",
        product_id=PRODUCT_ID,
        product_version=1,
        offer_id=OFFER_ID,
        offer_version=1,
    )
    assert preview["next_action"] == "await_approval", preview
    assert preview["state"] == "active" and preview["accepts_new_work"] is True
    assert deployment.rows("research_run") == 0, "预览建了一行 Run"

    reading = deployment.one("preview_effective_scene", product_id=PRODUCT_ID, product_version=1)
    assert reading["digest"] and reading["cells"], reading
    assert deployment.rows("effective_scene") == 0, "预览落了一版有效场景"

    # 纯身份那一份的预览：**它**才是接下来那两次启动要冻结的东西。
    identity_reading = deployment.one("preview_effective_scene")
    assert identity_reading["digest"] != reading["digest"], (
        "点没点名产品编出来的摘要是同一个 —— 那这一条后面比摘要就什么也没判"
    )

    # 两个 Workstream。**两边都不点名产品** —— 这不是省事，是今天唯一走得通的形态：
    # 有效场景按 ``(scene_id, version)`` 唯一且不可变，点名产品的那一次编译会写下
    # **另一份**内容，于是第二次启动被拒（见
    # :func:`test_a_product_bound_start_cannot_join_an_identity_only_scene_version`）。
    first, second = _two_workstreams(deployment)
    assert first["queue"] == "run_admitted" and first["why"] == ""
    assert second["queue"] == "run_admitted" and second["why"] == ""
    assert first["run_id"] != second["run_id"], "两个 Workstream 共用一个 Run"
    assert first["scene_version"] == second["scene_version"] == 1

    frozen = deployment.one("get_effective_scene", version=1)
    assert frozen["digest"] == identity_reading["digest"], "冻结的那一份与预览那一份不是同一份"
    assert frozen["compiled_from"] == identity_reading["compiled_from"]
    assert frozen["compiler"] == "start_workstream"
    assert identity_reading["compiler"] == "preview_effective_scene"
    assert deployment.rows("effective_scene") == 1
    assert deployment.rows("research_run") == 2
    assert deployment.rows("launch_record") == 2
    assert deployment.rows("workstream") == 2

    listed = _by_id(deployment.one("list_workstreams")["workstreams"])
    for receipt in (first, second):
        row = listed[receipt["workstream_id"]]
        assert row["run_id"] == receipt["run_id"]
        assert row["scene_id"] == PROFILE
        assert row["workspace"] == PROFILE, "两个 Workstream 落在两个工作区上了"


def test_a_product_bound_workstream_starts_and_persists(deployment: Deployment) -> None:
    """§G0 那半句「一个绑定**有批准出处 / 明确 fixture 身份**的 Product/Offer」。

    这一条在**另一个数据根**上跑（``tmp_path`` 每个测试各一份），因为它是这一次
    启动的**第一次冻结** —— 而第一次冻结决定了这一版 Scene 此后读出来是什么样子。
    三格一起看：回执上是 ``run_admitted``；冻结读数里多出 ``product`` 与 ``offer``
    那两格，出处是 ``ready``（**核对过**，不是声明过）；盘上真的多了一行 Run、
    一行 LaunchRecord、一行 Workstream。

    「批准出处」这一句在这个部署里的形状：``status="ready"`` 是**唯一**会写下
    ``approved_by`` 的那一档，写的是**令牌里那个主体**（``_verifier`` 从
    ``current_principal()`` 取），所以它记的是「这一趟是谁签的」，不是某个真人的名字。
    """
    _scene(deployment)
    _register_fixture_catalog(deployment)

    receipt = deployment.one(
        "start_workstream",
        context=CONTEXT,
        cutoff=(utc_now() + timedelta(days=1)).isoformat(),
        idempotency_key=f"{FIXTURE_PREFIX}-ws-product-bound",
        scene_version=1,
        workstream_name="g0 product-bound",
        budget={"search": 20},
        product_id=PRODUCT_ID,
        product_version=1,
        offer_id=OFFER_ID,
        offer_version=1,
    )
    assert receipt["queue"] == "run_admitted", receipt["why"]
    assert receipt["run_id"], receipt

    frozen = deployment.one("get_effective_scene", version=1)
    cells = {cell["field"]: cell for cell in frozen["cells"]}
    assert cells["product"]["value"] == f"{PRODUCT_ID}@1"
    assert cells["offer"]["value"] == f"{OFFER_ID}@1"
    assert cells["product"]["provenance"] == "ready", cells["product"]
    assert cells["offer"]["owner"] == f"{FIXTURE_PREFIX}-owner", cells["offer"]
    assert frozen["compiler"] == "start_workstream"

    assert deployment.rows("research_run") == 1
    assert deployment.rows("launch_record") == 1
    assert deployment.rows("workstream") == 1


def test_a_product_bound_start_cannot_join_an_identity_only_scene_version(deployment: Deployment) -> None:
    """§G0 那半句里的**一对括号**（一个绑定产品、一个不绑定）今天走不通，这一条钉住它。

    ## 量到的是什么

    第一个 Workstream 不点名产品 ⇒ 冻结的第 1 版有效场景里只有 ``product_use``
    （那句裸字符串）。第二个 Workstream 点名夹具产品 ⇒ 这一次要编译出**另一份**内容
    （多出 ``product`` / ``offer`` 两格、``product_use`` 被压住），于是第 3 步冻结
    当场拒绝，判词逐字：「``effective_scene`` … 第 1 版已经写过了，而这一次的内容不是
    同一份」，处置写着「发一版新的 Scene 再编译」。而被拒之后盘上仍然是**一行**
    有效场景、**一个** Workstream、**一行** Run。

    反过来的顺序同样不通（先点名产品、再纯身份）：两次编译输入不同，判据是对称的。

    ## 为什么这一条是「如实报」，不是「实现坏了」

    「有效场景一旦生成就不再被就地改写」是本片（B02-b）的承重不变量之一：同一
    ``(scene, version)`` 在两次部署之间成了两份不同的东西时，引用它的每一处都看不出
    来。所以拒绝是对的。今天缺的是**发一版新的 Scene** 那条路：``create_scene`` 对
    同一个部署只认已经立项的那一版（``:func:`test_a_second_scene_is_refused_by_name`）
    —— 于是「一个部署里两个编译输入不同的 Workstream」**没有形态**。

    这一条钉住的是那两句判词本身，所以它不是「一条看起来像第 5 条的判据顶上」：它
    报的是缺口的位置与形状，而缺口本身记在 ``G/REPORT.md``。
    """
    _scene(deployment)
    _register_fixture_catalog(deployment)

    _two_workstreams(deployment)

    outcome = deployment.run(
        (
            "start_workstream",
            {
                "context": CONTEXT,
                "cutoff": (utc_now() + timedelta(days=1)).isoformat(),
                "idempotency_key": f"{FIXTURE_PREFIX}-ws-product",
                "scene_version": 1,
                "workstream_name": "g0 product-bound",
                "budget": {"search": 20},
                "product_id": PRODUCT_ID,
                "product_version": 1,
                "offer_id": OFFER_ID,
                "offer_version": 1,
            },
        )
    )
    text = _refused(outcome)
    assert "已经写过了" in text and "不是同一份" in text, text
    assert "发一版新的 Scene" in text, text

    #: 被拒之后盘上一样都没多。
    frozen_versions = deployment.one("get_effective_scene", version=1)
    assert frozen_versions["compiler"] == "start_workstream"
    assert "product" not in {cell["field"] for cell in frozen_versions["cells"]}
    assert deployment.rows("effective_scene") == 1
    assert deployment.rows("workstream") == 2
    assert deployment.rows("research_run") == 2
    assert deployment.rows("launch_record") == 2


# ----------------------------------------------------------------------
# 1 · 旧 product 字符串
# ----------------------------------------------------------------------
def test_the_legacy_product_string_is_not_migrated_it_is_overridden(deployment: Deployment) -> None:
    """第 1 条逐字「旧 product 字符串迁移」。**今天真实发生的是「覆盖」，不是「迁移」。**

    ## 量出来的是两格并存

    立项时那一格是**裸字符串**（``SceneVersion.product``，历史形状）。点名一版登记过的
    夹具产品之后，编译出来的读数里同时有：

    * ``product_use`` —— 值**逐字是那句旧字符串**，``provenance="declared"``（谁说的：
      这一次声明），``precedence=0``；
    * ``product`` —— 值 ``g0-fixture-product@1``，``provenance="ready"``（**核对过**），
      ``precedence=2``，而且它的 ``overrides`` **点名**它压住的是哪一格。

    判据是这三条一起：旧值**还在**（没被就地改写、没被删）、新值**是另一格**（没有把
    旧字符串原地换成 id）、压住这件事**被写下来了**（``overrides`` 逐字点名）。

    ## 为什么不能写成「旧值被迁移成 id」

    仓里没有那条路：``application/effective_scene.py`` 把两格分开编译，从不互相比较
    （那一份的 docstring 逐字写着理由）。写成「迁移了」的判据会在今天直接红，而它红的
    是一个**根本不存在的机制**；写成「旧值消失了」的判据同样红，而它红的是**实现本来
    就做对的那一件事**（旧值保留下来，读得出来它曾被说过）。所以这一条钉的是
    **两格并存 + 覆盖被点名**。
    """
    _scene(deployment)
    deployment.one(
        "put_product_version",
        product_id=PRODUCT_ID,
        version=1,
        display_name="G0 fixture product (NOT A REAL PRODUCT)",
        owner=f"{FIXTURE_PREFIX}-owner",
        status="ready",
        capabilities=["fixture capability"],
        limits=["fixture limitation"],
        units="fixture unit",
        source_refs=SRC_REFS,
    )

    declared_only = deployment.one("preview_effective_scene")
    legacy = {
        cell["field"]: cell
        for cell in declared_only["cells"]
        if cell["field"] in {"product_use", "product"}
    }
    assert set(legacy) == {"product_use"}, (
        "没点名产品版本时，读数里应当只有 scene 声明的那一格（``product_use``）—— "
        f"多出来的：{sorted(set(legacy) - {'product_use'})!r}"
    )
    assert legacy["product_use"]["value"] == LEGACY_PRODUCT_TEXT
    assert legacy["product_use"]["provenance"] == "declared"
    assert legacy["product_use"]["precedence"] == 0

    reading = deployment.one(
        "preview_effective_scene", product_id=PRODUCT_ID, product_version=1
    )
    cells = {cell["field"]: cell for cell in reading["cells"]}
    assert set(cells) >= {"product_use", "product"}, sorted(cells)

    # 旧值还在，而且**没有被改写**。
    assert cells["product_use"]["value"] == LEGACY_PRODUCT_TEXT
    assert cells["product_use"]["provenance"] == "declared"
    assert cells["product_use"]["precedence"] == 0
    # 新值是**另一格**，它的出处是「核对过」，不是「声明过」。
    assert cells["product"]["value"] == f"{PRODUCT_ID}@1"
    assert cells["product"]["provenance"] == "ready"
    assert cells["product"]["precedence"] > cells["product_use"]["precedence"]
    # 压住这件事被**写下来**了：点名是哪一格、值是什么。
    assert cells["product"]["overrides"] == [f"product_use={LEGACY_PRODUCT_TEXT}"], (
        "``product`` 那一格没有点名它压住了哪一格 —— 少了这一句，"
        "「两份说法并存而其中一份说了算」这件事在读数上就消失了"
    )
    # 旧那一格**没有**反过来声称自己压住了谁：它不是赢家，而它也不是被删掉的。
    assert cells["product_use"]["overrides"] == []
    # 没有一格是「旧字符串 + ready」—— 那才是「迁移」该留下的形状，而它不存在。
    assert not any(
        cell["value"] == LEGACY_PRODUCT_TEXT and cell["provenance"] == "ready"
        for cell in reading["cells"]
    )


# ----------------------------------------------------------------------
# 2 · 产品撤回
# ----------------------------------------------------------------------
def test_a_withdrawn_claim_stops_being_read_as_an_approved_source(deployment: Deployment) -> None:
    """第 2 条逐字「``withdraw_claim`` 之后**不**再拿它当已批准出处」。

    ## 这一条钉住的**是**什么

    一条被引用的说法撤了之后，「不再当已批准出处」在读数上是**两件事**，这一条两件
    都判：

    1. **被引那一版的作用域与期限读不出来** —— 它逐条落进 ``not_reported``，那里点着名
       说「第 1 版」，并且**明写不拿最新版顶替**。
    2. **这条方案因此不再 ``usable``** —— 撤回是**退场**：指着一条已经退场的说法启动
       这件事本身不成立，所以它进 ``invalid``（处置是「去改这一版方案里的引用」）。
       它**不**进 ``missing``（退场不是「补一份」），也**不**进 ``awaiting_approval``
       （退场不是「等一次签字」）—— 三种处置相反。

    撤回回执自己也要对得上：追加的是第 N+1 版，``expires_at`` 落在撤回那一刻。

    ``preview_launch`` 上因此真的多出一行缺口：外层 ``invalid`` 多一条指着 ``OFFER_ID``
    的行、``next_action`` 随之变成 ``fix_citations``；「哪条说法、哪一版、为什么不算
    活的」逐字嵌在同一份回执的 ``cited`` 里（内层读数整份嵌进来，``launch_preview._rollup``
    不折平、不改写 —— 内层那行有自己的主语，折上来会让「这一版缺东西」与「它引的那一
    版缺东西」在报告上同形）。

    ## 这一条顺带钉住「判的是哪一版」

    盘上留下的**只有**追加的那一版：``adapters/sqlite/claim_withdrawal.py`` 往
    ``outreach_claim`` 里 insert 一行，形状与一次普通的再批准逐字相同，而
    ``approved_claims()`` 只回每个 ``claim_id`` 的**最新**一版、并且不按期限筛。所以
    「这条说法这一刻还活着吗」在盘上**只能**对最新那一版问 —— ``ports/claim_withdrawal.py``
    逐字「每一个消费者都按 ``ApprovedClaim.is_live_at(moment)`` 筛」，而今天唯一一处真
    的这么读的生产代码（``application/outreach.py`` 的 ``why_not_applicable``）读的也
    是**最新**那一版。所以缺口的 ``subject`` 指**被引的那一版**（与
    ``approved_claim_refs`` 那两支既有写法同形），``why`` 里写清**盘上哪一版**不算活的。

    这一格与 ``tests/test_product_readiness.py::test_a_superseded_citation_is_unreported_and_the_latest_is_not_substituted``
    说的**不是同一句话**（那边说「被引那一版读不出来」，这边说「最新那一版不活了」），
    但两份夹具在盘上是同一个形状 —— 那个文件里最新的第 2 版比被引的第 1 版**先**过期，
    于是本条判 ``invalid``、那条判 ``usable``。**这件事已如实写进报告交给 MAIN 裁决**，
    不在这里替另一份文件改判。

    ## 反例（这一条自己守着，免得「一律判不活」在这里绿）

    撤之前那一读仍然 ``usable: true``（引的是一条**活**的说法）；「没价格」那一路上
    ``missing`` / ``invalid`` 都空 —— 后者由
    :func:`test_a_price_is_never_invented_and_never_becomes_a_scene_fact` 钉住。
    """
    _scene(deployment)
    _register_fixture_catalog(deployment)

    before = deployment.one("get_offer_version_readiness", offer_id=OFFER_ID, version=1)
    assert before["status"] == "ready"
    # 反例之一，写在这里因为它是这一条的对照：引的是一条**活**的说法时，读数必须是
    # ``usable``。少了这一句，一个「一律判不活」的实现会在这一条上绿。
    assert before["usable"] is True, before
    assert before["missing"] == [] and before["invalid"] == [], before
    assert not any(CLAIM_ID in item["item"] for item in before["not_reported"]), (
        "撤之前就有「被引那一版读不出来」—— 那这一条后面判不出撤回的效果"
    )

    receipt = deployment.one("withdraw_claim", claim_id=CLAIM_ID)
    assert receipt["claim_id"] == CLAIM_ID
    assert receipt["retired_revision"] == 2, receipt
    assert receipt["expires_at"] == receipt["withdrawn_at"], (
        "退场那一版的期限就该落在撤回那一刻 —— 两个值不同意味着它还是活的"
    )

    after = deployment.one("get_offer_version_readiness", offer_id=OFFER_ID, version=1)

    # 承重那一句：被它引用的说法撤了之后，这条方案不再报 ``usable``。
    assert after["usable"] is False, (
        "撤回了被它引用的说法，这一版方案仍然报 usable —— 那「撤回」在这条链上等于没"
        "发生"
    )

    hits = [item for item in after["not_reported"] if CLAIM_ID in item["item"]]
    assert len(hits) == 1, f"被引那一版没有被报成「读不出来」：{after['not_reported']!r}"
    assert "第 1 版" in hits[0]["item"], hits[0]
    assert "不拿最新版顶替" in hits[0]["reason"], (
        "那一句「不拿最新版顶替」是这一条的承重句：少了它，把最新版悄悄换上会在这里绿"
    )

    # 退场**不是**「补一份」，也**不是**「等一次签字」：那两张清单里一个被撤的字都
    # 不能有（三种处置相反 —— 补一份 / 等签字 / 去改引用）。
    assert after["missing"] == [], after["missing"]
    assert after["awaiting_approval"] == [], after["awaiting_approval"]
    assert not any(CLAIM_ID in str(item) for item in after["missing"]), (
        "被撤的那条说法出现在 missing 里 —— 撤回是退场，不是「补一份」"
    )
    assert not any(CLAIM_ID in str(item) for item in after["awaiting_approval"]), (
        "被撤的那条说法出现在 awaiting_approval 里 —— 撤回是退场，不是「等一次签字」"
    )

    # 它进 ``invalid``：处置是「去改这一版方案里的引用」。多出来的**只**这一条，而且
    # 它点名了那条说法、指出了盘上哪一版不算活的、说了这一刻为什么不在期限里。
    assert len(after["invalid"]) == 1, after["invalid"]
    gap = after["invalid"][0]
    assert gap["field"] == "approved_claim_refs", gap
    assert gap["subject"] == f"claim:{CLAIM_ID}@1", (
        "缺口的主语不是被引的那一版 —— 与 approved_claim_refs 那两支既有写法不同形"
    )
    assert CLAIM_ID in gap["why"] and "第 2 版" in gap["why"], gap
    assert "expires_at" in gap["why"] and "这次读的时刻是" in gap["why"], (
        f"缺口没写清「凭什么说它不活了」：{gap!r}"
    )
    assert "退场" in gap["why"], (
        "缺口没说这一步的处置是「退场 ⇒ 去改引用」—— 少了它，「补一份」与「等签字」"
        "两种处置会看起来同样合理"
    )

    # 而它在**启动预览**上真的多出一行：外层 ``invalid`` 多一条指着 OFFER_ID 的行，
    # ``next_action`` 随之变成 ``fix_citations``（去改引用），而不是 ``supply_missing``
    # 或 ``await_approval`` —— 那两句会让调用方去补一份、去等一次签字。
    preview = deployment.one(
        "preview_launch",
        product_id=PRODUCT_ID,
        product_version=1,
        offer_id=OFFER_ID,
        offer_version=1,
    )
    assert preview["next_action"] == "fix_citations", preview["next_action"]
    assert preview["missing"] == [], preview["missing"]
    outer = [row for row in preview["invalid"] if row["field"] == "offer_id"]
    assert len(outer) == 1, f"预览里没有多出指着这一版方案的那一行：{preview['invalid']!r}"
    assert OFFER_ID in outer[0]["why"], outer[0]
    cited = [row for row in preview["cited"] if row["kind"] == "offer"]
    assert len(cited) == 1, preview["cited"]
    inner = [row for row in cited[0]["invalid"] if row["field"] == "approved_claim_refs"]
    assert len(inner) == 1, cited[0]
    assert inner[0]["subject"] == f"claim:{CLAIM_ID}@1", inner[0]
    assert "第 2 版" in inner[0]["why"], (
        "预览里那一行没有点名是哪一版不算活的 —— 报告上「多出一行缺口」于是只是"
        f"「多出一行」：{inner[0]!r}"
    )


# ----------------------------------------------------------------------
# 3 · 同一 Account 不同 Scene 评分 —— **做不到**，钉住的是拦路那条判据本身
# ----------------------------------------------------------------------
def test_a_second_scene_is_refused_by_name(deployment: Deployment) -> None:
    """第 3 条**今天做不到**，而这一条钉住的是「为什么做不到」那句判词本身。

    ## 第 3 条要的是什么

    「两个 Scene 对**同一个** Account 给出各自的判定」。它要的是同一个部署里**并存**
    两个 Scene，好让同一批账号在两份规则下各被读一次。

    ## 今天拦在哪

    ``_shared.require_scene`` 只认 ``settings.profile``，而 ``create_scene`` 走的是同一
    条判据：给一个别的 ``scene_id`` 不是「建一个新的」，是**一次拒绝**，判词里逐字写着
    「一个部署只服务一个 Scene —— 证据、预算与凭据都属于它」。``profiles/`` 里也只有
    这份部署自己那一份。

    所以「两个 Scene 并存」在一个部署里**根本没有形态**：两个 Scene 只能是两个部署，
    而两个部署各有各的数据根、证据与凭据 —— 那时「同一个 Account」这句话也没有形态了。

    ## 为什么把拒绝写成判据

    它是一条**可执行的**读数（判词、被点名的那个 id、以及 ``profiles/`` 里确实只有
    一份），比一句散文能守住的东西多：哪天这个部署真的支持了第二个 Scene，这一条会
    当场红，而红的那一天正是第 3 条第一次**可以**被测的那一天。
    """
    created = _scene(deployment)

    outcome = deployment.run(
        ("create_scene", {**BASE_CREATE, "scene_id": SECOND_SCENE_ID, "activate": True})
    )
    text = _refused(outcome)
    assert SECOND_SCENE_ID in text, text
    assert PROFILE in text, f"判词里没有点名这个部署真正服务的 Scene：{text}"
    assert "一个部署只服务一个 Scene" in text, text

    scenes = deployment.one("list_scenes")
    assert [row["scene_id"] for row in scenes["scenes"]] == [created["scene"]["scene_id"]]

    profiles = sorted(
        path.name for path in deployment.settings.profiles_dir.iterdir() if path.is_dir()
    )
    assert profiles == [PROFILE], (
        f"``profiles/`` 里不止一份 profile（{profiles!r}）—— 第 3 条也许从今天起可测了"
    )


# ----------------------------------------------------------------------
# 4 · 某任务暂停 / 另一任务继续
# ----------------------------------------------------------------------
def test_pausing_one_workstream_leaves_the_other_and_the_runs_untouched(deployment: Deployment) -> None:
    """第 4 条逐字「``control_workstream`` 只影响那一个」。

    两把量具都用上：**行**（被控那一个真的变了）与**逐格相等**（另一个一个字节都没变）。
    只判「另一个还在跑」会放过一条「顺手把整批重写了一遍、内容恰好相同」的实现 ——
    它在回执上与这一条断言一样，而 ``updated_at`` / ``revision`` 会说出真相。

    Run 那一张表另算一次：``control_workstream`` 的暂停/继续**不许**改 Run。
    """
    _scene(deployment)
    first, second = _two_workstreams(deployment)
    before = _by_id(deployment.one("list_workstreams")["workstreams"])
    runs_before = deployment.rows("research_run")
    launches_before = deployment.rows("launch_record")

    paused = deployment.one(
        "control_workstream",
        workstream_id=first["workstream_id"],
        action="pause",
        expected_revision=first["revision"],
        reason=f"{FIXTURE_PREFIX}_pause",
    )
    assert paused["state"] == "paused"
    assert paused["revision"] == first["revision"] + 1
    assert paused["pause_reason"] == f"{FIXTURE_PREFIX}_pause"
    assert paused["run_id"] == first["run_id"], "暂停改了它自己的 Run"

    after = _by_id(deployment.one("list_workstreams")["workstreams"])
    #: 回执与清单比的是**回执上有的那几格**：清单多出四格是列表专有的投影
    #: （``scene_state`` / ``scene_pending_version`` / ``dispatch_allowed`` /
    #: ``dispatch_refusal``），它们不在控制回执上，而这一条不比缺席的那几格。
    echo = {"action", "expected_revision"}
    row = after[first["workstream_id"]]
    for key, value in paused.items():
        if key in echo:
            continue
        assert row[key] == value, (
            f"控制回执上的 {key!r} 与清单读回来的不是同一个值："
            f"{value!r} ≠ {row[key]!r}"
        )
    assert second["workstream_id"] in after and after[second["workstream_id"]] == before[second["workstream_id"]], (
        "停第一个工作流改动了第二个的任何一格：\n"
        f"  之前：{before[second['workstream_id']]!r}\n"
        f"  之后：{after[second['workstream_id']]!r}"
    )

    resumed = deployment.one(
        "control_workstream",
        workstream_id=first["workstream_id"],
        action="resume",
        expected_revision=paused["revision"],
    )
    assert resumed["state"] == "running"
    assert resumed["revision"] == paused["revision"] + 1
    assert resumed["pause_reason"] == "", "继续之后那句暂停理由还挂着"

    final = _by_id(deployment.one("list_workstreams")["workstreams"])
    assert final[second["workstream_id"]] == before[second["workstream_id"]], (
        "继续第一个工作流改动了第二个的任何一格"
    )

    assert deployment.rows("research_run") == runs_before, (
        "暂停/继续建了或删了一行 Run —— 控制一个工作流是改它自己的那一行，不是受理"
    )
    assert deployment.rows("launch_record") == launches_before


# ----------------------------------------------------------------------
# 5 · 同源额度共享（结构那一半）
# ----------------------------------------------------------------------
def test_the_two_workstreams_resolve_one_declared_budget_source(deployment: Deployment) -> None:
    """第 5 条的结构那一半：两个 Workstream 解的是**同一个**额度源，而不是各拿一份。

    ## 今天量得到的三格

    1. **冻结读数里 ``budget`` 科目只有一格**，出处是**这一版 Scene**（
       ``scene:<profile>@1.budget``），``provenance="declared"``。额度源是 Scene 的属性，
       不是 Workstream 的属性 —— 多一份预算在这里会以**第二格**的形状出现，这一条当场红。
    2. **两个 Workstream 的 ``scene_version`` 相同**。有效场景是按 ``(scene_id, version)``
       唯一的，所以两条链解到的是同一格读数，不是两份等价物。
    3. **账本上一个按工作流分的上限都没有**（``budget_ceiling`` 零行）：手动那一轮
       （``window is None``）不种子任何上限，于是「两个工作流各记一份」这件事在盘上
       留不下痕迹 —— 正是这一点让下面那句 ``BLOCKED`` 成为一句真话，而不是一句偷懒。

    ## 花出去那一半是 ``BLOCKED_EXTERNAL``

    「共用一个源 ⇒ 花的总量不翻倍」要一次**真的预留**，而预留只有两个写者
    （``application/billed_search.py:121`` / ``application/refresh.py:1117``），两个都在
    **外部调用之前**。本机 ``serper_enabled`` 为假、``SERPER_ENDPOINT`` 写死、``_compose``
    没有留下注入 ``SearchProvider`` 的口，所以这条路在这台机器上走不出一次预留。
    这一份**不拿一个假后端冒充**，那一条记 ``BLOCKED_EXTERNAL``（release 条件在报告里）。
    """
    _scene(deployment)
    _two_workstreams(deployment)

    frozen = deployment.one("get_effective_scene", version=1)
    budget_cells = [cell for cell in frozen["cells"] if cell["field"].startswith("budget.")]
    assert [cell["field"] for cell in budget_cells] == ["budget.search"], (
        "额度源不止一格 —— 「两个 Workstream 各一份预算」正是以这个形状出现的："
        f"{[cell['field'] for cell in budget_cells]!r}"
    )
    cell = budget_cells[0]
    assert cell["source"] == f"scene:{PROFILE}@1.budget", cell["source"]
    assert cell["provenance"] == "declared", cell
    assert cell["value"] == "20", cell

    rows = list(deployment.one("list_workstreams")["workstreams"])
    assert len({row["scene_version"] for row in rows}) == 1, rows
    assert {row["scene_id"] for row in rows} == {PROFILE}, rows

    assert deployment.rows("budget_ceiling") == 0, (
        "手动那一轮不该种子任何上限 —— 上限是窗口（``bounded_auto``）那一档的事"
    )
    assert deployment.rows("budget_reservation") == 0
    #: 账本这一页读得出来，而且它按**主体**筛、不按工作流 —— ``scope`` 是
    #: ``<profile>:<主体>`` 的形状（``RunBudget.for_account``），上面压根没有
    #: 「哪个工作流」这一格。一个按工作流分账的实现会在这里多出一格筛法。
    page = deployment.one("list_open_reservations")
    assert page["reservations"] == [] and page["counts"]["held_units"] == 0
    assert page["filters"] == {"scope": None}, page["filters"]


# ----------------------------------------------------------------------
# 6 · launch 幂等
# ----------------------------------------------------------------------
def test_a_replayed_launch_writes_no_second_row(deployment: Deployment) -> None:
    """第 6 条逐字「同一 launch 重放**不产生第二条**」。

    两个量具缺一不可：**回执**（三个 id 逐字相同、``replayed`` 为真、``created_at`` /
    ``revision`` 报的是**第一次**那一组）与**行数**（``launch_record`` / ``workstream`` /
    ``research_run`` / ``effective_scene`` 四张表一张都不许多）。

    只比回执会放过一条「又建了一行、然后把新行读出来」的实现 —— 它的回执与这一条断言
    逐字相同，而盘上那两行里有一行没人要。

    最后再走一次「同一个键、另一份内容」：那**不是**重放，是同键冲突，必须拒绝，
    而且拒绝之后行数照旧。
    """
    _scene(deployment)
    first, second = _two_workstreams(deployment)

    counts = {
        table: deployment.rows(table)
        for table in ("launch_record", "workstream", "research_run", "effective_scene")
    }

    cutoff = (utc_now() + timedelta(days=1)).isoformat()
    replay = _ok(
        deployment.run(
            (
                "start_workstream",
                {
                    "context": CONTEXT,
                    "cutoff": cutoff,
                    "idempotency_key": f"{FIXTURE_PREFIX}-ws-one",
                    "scene_version": 1,
                    "workstream_name": "g0 ws one",
                    "budget": {"search": 20},
                },
            )
        )
    )
    assert replay["replayed"] is True
    assert replay["launch_id"] == first["launch_id"]
    assert replay["workstream_id"] == first["workstream_id"]
    assert replay["run_id"] == first["run_id"]
    assert replay["created_at"] == first["created_at"], (
        "重放报的是**这一次**的时刻 —— 那一刻盘上什么都没发生"
    )
    assert replay["revision"] == first["revision"] == 1
    assert replay["ordinal"] == first["ordinal"] == 1

    for table, before in counts.items():
        assert deployment.rows(table) == before, f"重放又写了 {table}"

    # 第二个工作流那一行也不许被这次重放碰到。
    assert _by_id(deployment.one("list_workstreams")["workstreams"])[
        second["workstream_id"]
    ]["run_id"] == second["run_id"]

    conflict = deployment.run(
        (
            "start_workstream",
            {
                "context": CONTEXT,
                "cutoff": cutoff,
                "idempotency_key": f"{FIXTURE_PREFIX}-ws-one",
                "scene_version": 1,
                "workstream_name": "g0 ws one renamed",
                "budget": {"search": 20},
            },
        )
    )
    text = _refused(conflict)
    assert "已经用这个键记过了" in text and "不是同一份" in text, text
    for table, before in counts.items():
        assert deployment.rows(table) == before, f"同键冲突之后还是写了 {table}"


# ----------------------------------------------------------------------
# §2 · 三条功能约束
# ----------------------------------------------------------------------
def test_a_price_is_never_invented_and_never_becomes_a_scene_fact(deployment: Deployment) -> None:
    """§2 第一句逐字「无价格不猜价」。

    两件事一起判，因为放任哪一件都会让这一条变成一句自觉：

    * **不猜**：一版**没写价格**的 ready 方案，读数里一个跟价有关的字段都没有 ——
      不进 ``missing``（不必去补）、不进 ``invalid``（不是坏了）、也不给一个缺省价。
      价格可缺省**而照样可以研究**（那一版仍然是 ``usable``）。
    * **不变成场景事实**：写了一条**有出处**的报价的那一版，读数里也**没有** ``price``
      那一格。报价属于那一版方案，不属于这一版 Scene —— 它一旦被编译进场景，就会在
      方案换版之后继续被当成本地事实用下去。

    一条真的报价值得过校验器：``QuotedPrice`` 没有 ``source_ref`` / ``quoted_at`` 就
    造不出来（「不可编造」是**构造**，不是一句约定）。
    """
    _scene(deployment)
    _register_fixture_catalog(deployment)

    without = deployment.one("get_offer_version_readiness", offer_id=OFFER_ID, version=1)
    assert without["usable"] is True, without
    assert without["missing"] == [] and without["invalid"] == [], without
    for field in ("missing", "invalid", "awaiting_approval", "not_reported"):
        assert not any("price" in str(item).lower() for item in without[field]), (
            f"{field} 里出现了「价格」—— 价格缺席不是一条要补的缺，也不是一条坏的引用"
        )
    assert "amount_minor" not in json.dumps(without) and "currency" not in json.dumps(without)

    with_price = deployment.one(
        "put_offer_version",
        offer_id=f"{FIXTURE_PREFIX}-offer-priced",
        version=1,
        name="G0 fixture priced offer (NOT A REAL OFFER)",
        purpose="fixture only",
        owner=f"{FIXTURE_PREFIX}-owner",
        status="ready",
        product_refs=[{"product_id": PRODUCT_ID, "version": 1}],
        markets=["GB"],
        locales=["en"],
        approved_claim_refs=[{"claim_id": CLAIM_ID, "revision": 1}],
        price={
            "currency": "GBP",
            "amount_minor": 1250,
            "basis": "per litre",
            "source_ref": f"fixture://{FIXTURE_PREFIX}-artifact",
            "quoted_at": "2026-09-29T00:00:00+00:00",
        },
    )
    assert with_price["registered"], with_price

    reading = deployment.one(
        "preview_effective_scene", product_id=PRODUCT_ID, product_version=1,
        offer_id=f"{FIXTURE_PREFIX}-offer-priced", offer_version=1,
    )
    assert not any("price" in cell["field"] for cell in reading["cells"]), (
        "报价被编译进了这一版场景 —— 它属于方案那一版，不属于 Scene"
    )
    assert "1250" not in json.dumps(reading), reading["digest"]


def test_a_missing_plan_returns_hold_and_a_bare_offer_stays_a_draft(deployment: Deployment) -> None:
    """§2 第二句逐字「需要文案时，资料 / claims 缺失返回 ``draft`` / ``hold``」。

    两半各有一条链，而且两半都**不是一次拒绝**：

    * **``hold``** —— 一份编不出计划的声明（这里：一条可信的场景候选都没有）。
      这一次启动**照样建出工作流**，而第一轮 Run **不被受理**：``queue=idle``、
      ``why`` 非空、``run_id`` 是空串，盘上 ``research_run`` 一行都不多。把 ``HOLD``
      读成一次拒绝，会让「这个市场开了、但今天这一轮没什么可跑」与「这个市场没开」
      在报告上同形，而两者的下一步相反。
    * **``draft``** —— 一版没引任何说法的方案。它读出来是 ``usable: false``，缺的是
      ``approved_claim_refs``（**要补**），等的是 ``approved_by``（**要签**）——
      两档分开，因为「去补一版内容一样的重复记录」与「去找人签字」是两件相反的事。
      一个替它编一句文案的实现会在这里以 ``usable: true`` 的形状出现。
    """
    _scene(deployment)

    held = _ok(
        deployment.run(
            (
                "start_workstream",
                {
                    "context": dict(CONTEXT, scenario_candidates=[]),
                    "cutoff": (utc_now() + timedelta(days=1)).isoformat(),
                    "idempotency_key": f"{FIXTURE_PREFIX}-ws-held",
                    "scene_version": 1,
                    "workstream_name": "g0 held",
                    "budget": {"search": 20},
                },
            )
        )
    )
    assert held["queue"] == "idle", held
    assert held["run_id"] == "", "第一轮没被受理，却回了 run_id"
    assert held["why"].strip(), "IDLE 上没有 why —— 读的人分不清是等还是被拒了"
    assert "这一次什么都没写" in held["why"], held["why"]
    assert deployment.rows("research_run") == 0, "HOLD 那一轮还是受理了"
    assert deployment.rows("workstream") == 1, "HOLD 拦下了这一次**启动**，而它该拦的是第一轮 Run"

    bare = deployment.one(
        "put_offer_version",
        offer_id=BARE_OFFER_ID,
        version=1,
        name="G0 fixture bare offer (NOT A REAL OFFER)",
        purpose="fixture only",
        owner=f"{FIXTURE_PREFIX}-owner",
        status="draft",
        approved_claim_refs=[],
    )
    assert bare["registered"], bare

    reading = deployment.one("get_offer_version_readiness", offer_id=BARE_OFFER_ID, version=1)
    assert reading["status"] == "draft" and reading["usable"] is False
    assert {gap["field"] for gap in reading["missing"]} >= {"approved_claim_refs"}, reading["missing"]
    assert {gap["field"] for gap in reading["awaiting_approval"]} == {"approved_by"}, reading
    assert reading["invalid"] == [], reading["invalid"]


def test_an_identity_only_research_is_never_forced_to_name_an_offer(deployment: Deployment) -> None:
    """§2 第三句逐字「纯身份研究**不被迫**创建 Offer」。

    两格：

    * 一次**没有**点名产品/方案的启动照样被受理（``queue=run_admitted``），它的回执里
      没有产品/方案那几格，冻结读数里也**没有** ``product`` / ``offer`` 那两格。
      「先建一版 Offer 才能研究」如果成立，这一条会红在 ``queue`` 上。
    * 一次**没有**点名产品/方案的**预览**照样成立：它不拒绝，也**不替你挑一个赢家**，
      而是把「产品 / 方案 / 产品契合」那三格逐条写进 ``not_reported``（缺什么都说得
      出来）。一个「顺手替你选定默认方案」的实现会在这里以缺口消失的形状出现。
    """
    _scene(deployment)
    receipt = _ok(
        deployment.run(
            (
                "start_workstream",
                {
                    "context": CONTEXT,
                    "cutoff": (utc_now() + timedelta(days=1)).isoformat(),
                    "idempotency_key": f"{FIXTURE_PREFIX}-ws-identity-only",
                    "scene_version": 1,
                    "workstream_name": "g0 identity only",
                    "budget": {"search": 20},
                },
            )
        )
    )
    assert receipt["queue"] == "run_admitted", receipt["why"]
    assert "product_id" not in receipt and "offer_id" not in receipt, receipt

    frozen = deployment.one("get_effective_scene", version=1)
    fields = {cell["field"] for cell in frozen["cells"]}
    assert "product_use" in fields, fields
    assert "product" not in fields and "offer" not in fields, (
        f"没点名产品/方案，读数里却有目录那一族的格子：{sorted(fields)!r}"
    )

    preview = deployment.one("preview_effective_scene")
    assert preview["digest"], "预览被拒了 —— 一次纯身份研究不该被迫先登记一版方案"
    assert preview["conflicts"] == [], preview["conflicts"]
    assert not any("winner" in key for key in preview), "回执里出现了取过赢家的痕迹"
    gaps = preview["not_reported"]
    assert any("product" in gap for gap in gaps), (
        f"「没有点名产品」这件事没有被写下来：{gaps!r}"
    )
    assert any("offer" in gap for gap in gaps), gaps


# ----------------------------------------------------------------------
# §3 · 两条边界
# ----------------------------------------------------------------------
def test_nothing_outbound_happens_and_the_ledger_stays_empty(deployment: Deployment) -> None:
    """§3 前半逐字「``scope`` / 预算**未批不外调**」。

    ## 未批：这一台机器上没有检索提供方

    ``get_capabilities`` 的 ``serper.configured`` 为**假**，而 ``search_web`` 在**预留
    任何额度之前**就拒了，判词逐字写着「本部署没有接线检索提供方」以及那句要紧的
    「这不是一次『检索过、没有结果』——两者在报告上必须分得开」。少了「先看有没有
    提供方」这一步，一个没接线的部署会在每次调用里先扣住一笔额度、再让 provider 抛错，
    而那笔预留按 §8 不会被退回 —— 额度被一次次**不存在的**检索吃光。

    ## 不外调：整条链走完之后账本上零行

    ``budget_reservation`` 与 ``budget_ceiling`` 两张表都空。这是这一条真正的量具：
    「没有外调」这句话在回执上说不出区别，只有账本说得出来 —— 一次预留**必定**留下
    一行（两个写者都在外部调用**之前**预留）。
    """
    _scene(deployment)
    _two_workstreams(deployment)

    caps = deployment.one("get_capabilities")
    serper = next(item for item in caps["integrations"] if item["name"] == "serper")
    assert serper["configured"] is False, serper
    assert serper["blocker"], "没配却不说为什么 —— 读的人只能去猜"

    refusal = _refused(deployment.run(("search_web", {"account_id": "acct-x", "queries": ["g0 fixture query"]})))
    assert "没有接线检索提供方" in refusal, refusal
    assert "这不是一次「检索过、没有结果」" in refusal, refusal

    assert deployment.rows("budget_reservation") == 0, (
        "整条链走完账上有一条预留 —— 那意味着有一次真的外调（预留写在调用之前）"
    )
    assert deployment.rows("budget_ceiling") == 0

    page = deployment.one("list_open_reservations")
    assert page["store_state"] == "present"
    assert page["counts"]["held_units"] == 0 and page["reservations"] == []


def test_the_two_workstreams_read_one_customer_pool_not_two_copies(deployment: Deployment) -> None:
    """§3 后半逐字「不同 Workstream **不复制**客户库」。

    量的是**同一个集合**，不是「两个集合恰好一样大」：

    1. 两个 Workstream 存在之后，``list_account_pool`` 读回来的还是那两个账号 ——
       多出来的第二个工作流没有让池子多出一份副本。
    2. ``freeze_cohort`` 冻出来的成员**就是**池子那一批（逐 id 相等）。冻结读的是
       池子，而池子是按**工作区**分的，不是按工作流分的。
    3. ``list_account_pool`` / ``freeze_cohort`` 的签名里**没有**工作流那一格 ——
       一个能由载荷挑工作流的读，会让一次读去翻别人的库（这一格是「不复制」这句
       话在服务面上的形状）。
    4. 盘上 ``market_account`` 的行数就是账号数：两个工作流各一份副本会让它是四。
    """
    _scene(deployment)
    _write_motherpool_export(deployment.settings)
    _seed_discovery_evidence(deployment.settings)
    imported = deployment.one("import_motherpool", artifact_ref="motherpool")
    deployment.one("promote_receipt", receipt_id=imported["receipt"]["receipt_id"])

    pool = deployment.one("list_account_pool")
    accounts = tuple(row["account_id"] for row in pool["accounts"])
    assert len(accounts) == len(ARTIFACT_LINES), pool
    accounts_before = deployment.rows("market_account")

    _two_workstreams(deployment)

    pool_after = deployment.one("list_account_pool")
    assert tuple(row["account_id"] for row in pool_after["accounts"]) == accounts, (
        "多了一个 Workstream 之后池子变了 —— 客户库被按工作流分开了"
    )
    assert deployment.rows("market_account") == accounts_before, (
        f"账号行从 {accounts_before} 变成了 {deployment.rows('market_account')}："
        "第二个工作流复制了一份客户库"
    )

    frozen = deployment.one("freeze_cohort")
    assert {row["account_id"] for row in frozen["members"]} == set(accounts), (
        "冻出来的名单不是池子里那一批"
    )
    assert frozen["member_count"] == len(accounts)
    assert frozen["exclusion_count"] == 0

    #: 盘上那一批账号在两处之间**是同一批**：池子读得到、名单冻得出、而两边都不收
    #: 「哪一个工作流」这一格 —— 见了它，同一个账号就会有两份判定。
    for marker in ("def list_account_pool", "def freeze_cohort"):
        signature = _signature_of(loop_source, marker)
        assert "workstream" not in signature, (
            f"``{marker}`` 收了工作流那一格 —— 一次读会去翻别人那一份：{signature!r}"
        )


def _signature_of(source: str, marker: str) -> str:
    """从源码里截出 ``marker`` 那一个 ``def`` 的签名（到冒号为止）。

    走源码文本而不是 ``inspect``，是因为这两个处理器在 ``_compose`` 之后是**闭包**：
    闭包的签名里读得出参数名，而这一条要的正是「参数表里有没有工作流那一格」。
    """
    at = source.find(marker)
    assert at >= 0, f"源码里找不到 {marker!r}（量具指错了地方，不是被测对象）"
    end = source.index(":", at)
    return source[at:end]

"""§3 的验收：**责任关系由一次真实的 Research 结算写出来**，而那次结算的判定来自 ``decide``。

## 这一条要证明的一句话

``account_venue_relation`` 里有行，而那些行**只能**由一次真实的判定得出。

在这一轮之前那张表是 0 行，而 0 行不是「这个市场没有负责方」——它是「这条链没有
生产者」。三种到达方式都会让它恒为 0，而报告上三者同形：

* 契约在、类型在、**没有一处会写它**（本仓此前就是这一种）；
* 有人能写它，但写它的人手里没有判定 —— 于是「谁负责」这句话由研究员凭印象连起来；
* 判定能发生，而**判定与关系之间没有连线** —— 台账上有几条记录，表里一行都没有。

第三种最像成功：``decision_record`` 非零，任何按「Jev 被调用过没有」读的报告都是绿的，
而它答的那个问题（模型跑没跑）与 §3 问的那个问题（关系是谁定的）不是同一个。

## 为什么必须走 MCP 面，而不是直接调 application

``decide`` 与 ``submit_findings`` 各自都是薄薄一层，而「它们接的是**同一份**判定台账」
这件事只有走一遍真的会话才看得见：两个不同的工厂会让一份刚刚落地的判定在下一步读成
「查无此判定」，而两边各自都正常。``build_server`` 把这两个工厂接在同一个参数上，
这一条是那个参数的执行器。

## 它按顺序做的六件事

1. 真的跑一轮发现（假检索 + 假 HTTP，**零网络、零 key**），折一份回执 ——
   场地与 canonical 账号一起写进池子（这一段与 §1 那条链**共用夹具**，判据不重测）；
2. 立项并激活那个 Scene；
3. 受理一次研究（``input_kind="discovery_scope"``）并让它派发出去；
4. 从 MCP 面**领活**（``get_assignment``）—— 拿到的 attempt 是结算要用的那一格；
5. 从 MCP 面**判一次**（``decide``，后端是替身，题面是 profile 编译出来的那一份）；
6. 带着上一步的 ``record_id`` **结算**（``submit_findings``，``relations=[…]``），
   再回池子里读那几行。

## 后端是替身，而链路里其余每一格都是真的

``_Backend`` 是 ``tests/test_mcp_decision_tools`` 里那个记账的替身 —— 真 Jev 要在
验收运行里接（那一条在验收记录里，不在这里）。这一层要测的是**调用点**：判定落进
哪个台账、它被读回来时形状对不对、答案与声称对不上时表会不会多一行。替身**不**
参与任何一个断言的结论 —— 它只决定「这一次答了什么」。

## 每条「不许写」旁边都有一条「写下去了」

``test_a_settlement_without_a_decision_writes_nothing`` 这一族断言的是表里**一行都没有**，
而那在「这一层整个坏掉」时同样成立。所以正控（一次真的判定 → 一行真的落表）与它们
在同一个文件里，且形状相同。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.decisions import decision_handlers
from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.adapters.profile_loader import load_profile
from marketops.adapters.sqlite.decisions import SqliteDecisionRecordStore
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.application.decision_specs import compile_specs
from marketops.config import Settings
from marketops.domain.enums import DecisionMode, DecisionOutcome, DecisionTask, VenueRelation
from marketops.domain.util import sha256_text
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
from tests.fakes import FakeDispatcher
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload
from tests import test_mcp_discovery_chain as chain
from tests.test_evidence_store_contract import make_evidence
from tests.test_mcp_decision_tools import _Backend

#: 研究这一轮判出来的关系。**不是**闭集里的第一项（``owns``）：替身答的是闭集首项
#: 的话，「答案被读成了第一项」与「答案被原样读出来」这两件事分不开。
ANSWERED_RELATION = "maintains"

#: 一条属于**另一个**账号的证据。跨账号负例要用它 —— 它真的在盘上，所以任何只按
#: 「存在性」做的核对都会放行它。
FOREIGN_EVIDENCE = sha256_text("page-elsewhere")[:32]


@pytest.fixture
def session(settings: Settings, tmp_path: Path):
    """一个把产品环、判定、领活、结算接在**同一份存储**上的会话驱动器。

    五样存储只建一次并交给每一处，与组合根 ``interfaces/mcp/__main__.py`` 同款：

    * ``assets`` / ``relations`` —— 产品环的 ``promote_receipt`` 与结算那一步的
      场地派生、关系写入共用。两处各建一份的话，「折进去的场地」与「关系挂上去的
      场地」可能来自两个库，而那一行在任何一份按场地读的报告里都出现不了。
    * ``records`` —— ``decide`` 的落点与 ``submit_findings`` 去查判定的那一张**必须**
      是同一个工厂。这是本文件存在的主要理由之一。
    * ``dispatcher`` —— 整个用例共用**一个**：受理那一步真的会派发，而结算之后的
      看板同步读的是同一个执行器里的任务。每次调用新造一个的话，「交出去的那件活
      还在不在」这个问题在第二步上就会答错。
    """
    # 一个同时持 controller 与 researcher 的主体。两个角色不是图省事：受理
    # （controller）与领活、判定、结算（researcher）是这条链上前后相继的四步，
    # 而它们今天各自要求一个角色。拆成两个主体就得在两段会话之间搬运 work item id
    # 与 attempt id —— 那会把「同一条链」变成「两个测试拼起来」，而它们之间的接缝
    # 恰好是本条判据要测的东西。
    token, digest = mint("claude-both", [Role.CONTROLLER, Role.RESEARCHER])
    ring_path = tmp_path / "tokens.json"
    ring_path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, "claude-both", ["controller", "researcher"]))
        ),
        encoding="utf-8",
    )
    ring = TokenRing.from_file(ring_path)

    scenes = default_scene_store(settings)
    assets = default_asset_store(settings)
    relations = default_relation_store(settings)
    accounts = default_account_pool_store(settings)
    records = lambda: SqliteDecisionRecordStore(settings.database_path)  # noqa: E731
    dispatcher = FakeDispatcher()

    def run(*calls: tuple[str, dict[str, Any]], backend: Any = None):
        """一段**全新的**会话（含一次新的握手），跑完给定的这几次调用。

        ``backend`` 缺省是一个答 :data:`ANSWERED_RELATION` 的 Jev 替身。要摆
        「判定没给出结论」那种情形时传 ``_Backend("jev", _cannot(...))``。
        """
        provider = (
            _Backend("jev", lambda case: ANSWERED_RELATION) if backend is None else backend
        )
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            product_loop=product_loop_handlers(
                settings=settings,
                scenes=scenes,
                maintenance=default_maintenance_store(settings),
                assets=assets,
                relations=relations,
                accounts=accounts,
                cohorts=default_account_cohort_store(settings),
                review=None,
                acquisition=None,
                router=None,
                documents=None,
            ),
            # 与 ``submit_findings`` 拿到的是**同一个** ``records`` —— 本文件要钉住
            # 的接线事实就是这一条。
            research=decision_handlers(settings=settings, advisory=provider, records=records),
            scenes=scenes,
            jobs=lambda: SqliteJobStore(settings.database_path),
            quay=dispatcher,
            decisions={"jev": provider},
            advisory=provider,
            assets=assets,
            relation_store=relations,
            records=records,
            calls=calls,
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    return run


#: §1 那条链跑出来的那一轮发现 —— ``(范围名, 回执 id)``。
#:
#: 借过来而不是在这里再跑一轮：两边各跑一轮会得到**两条独立的事实**，而本文件要的
#: 正是「同一个账号、同一块场地」—— 两条独立的事实凑不出这一条（它们今天内容相同，
#: 差别只在下一次改动里现形）。
#:
#: 绑成一个模块级的名字，是因为 pytest 按**名字**在这个模块里找夹具。写成
#: ``from … import discovered`` 会被 ruff 判成与下面 ``researched`` 的同名形参互相
#: 遮蔽（F811），而那条 import 就得挂一个 ``noqa`` —— 一行看起来像有问题的 import，
#: 下一个读的人会去查它到底哪里错了。
discovered = chain.discovered
_ok = chain._ok
CREATE = chain.CREATE


@pytest.fixture
def researched(session, settings: Settings, discovered: tuple[str, str]):
    """走到「已派发、已领活、已判定」为止的那条链，交出后面几步要用的那几格。

    它是一次**真的**受理：账号来自折进去的那份回执，工作单元的主语是 canonical
    账号（§1 那条链已经把「两个 id 是同一个空间」钉住了，这里只取用，不重测）。
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
                "idempotency_key": "responsibility-relations-1",
            },
        ),
    )
    pool = _ok(outcome.calls[3])
    assert pool["account_count"] == 1
    account_id = pool["accounts"][0]["account_id"]

    submitted = _ok(outcome.calls[4])
    assert submitted["state"] == "queued", (
        f"作业被阻塞了：{submitted['reason']}\n"
        "（这一句在这条链上最常见的成因是：折进去的账号与研究件算出来的主语不是"
        "同一个值，于是 unpromoted_subjects 整份拒绝）"
    )
    job_id = submitted["job_id"]
    items = SqliteJobStore(settings.database_path).work_items(job_id)
    assert len(items) == 1
    work_item_id = items[0].work_item_id
    assert items[0].subject_ref == account_id, (
        "研究件的主语不是账号池里那一行 —— 后者是 §1 那条链的主题，这里只取用"
    )

    # 领活走 MCP 面：它当场租一条给**本次调用的主体**，而结算要的那一格就是它。
    assignment = _ok(session(("get_assignment", {"work_item_id": work_item_id})).calls[0])
    attempt_id = assignment["attempt"]["attempt_id"]

    spec = compile_specs(load_profile(settings)).get(DecisionTask.RESPONSIBILITY_RELATION)
    assert spec is not None, "这份 profile 没有声明责任关系题面 —— §3 那条链因此不成立"

    # 证据先取：判定要带着它引用的那几条材料去判（``decide`` 的 ``evidence_refs``
    # 进输入指纹、逐字进记录），而下面那一条「结果指得出是哪次调用」要读回这一格。
    account = _ok(session(("get_account", {"account_id": account_id})).calls[0])
    usable = [ref for ref in account["evidence_refs"] if ref["binding_eligible"]]
    assert usable, (
        "这个账号一条可作依据的证据都没有 —— 发现那一轮的材料没跟着折进来，"
        f"而下面的结算会以「引用解不开」红，红在一个与被测行为无关的理由上。"
        f"拿到的是：{account['evidence_refs']}"
    )
    evidence_id = usable[0]["evidence_id"]

    decided = _ok(
        session(
            (
                "decide",
                {
                    "work_item_id": work_item_id,
                    "spec_id": spec.spec_id,
                    "state": "Clubhouse page: 'Ground maintained by Example Garden Club.'",
                    "evidence_refs": [evidence_id],
                },
            )
        ).calls[0]
    )
    assert decided["outcome"] == DecisionOutcome.ANSWERED.value
    assert decided["answers"][0]["choice"] == ANSWERED_RELATION, (
        "替身答的必须就是这一条判据要用的那个关系，否则下面每一步都在测别的东西"
    )

    # 一件**另外的**工作：同一个账号、同一个场景、换一个幂等键。它只为一件事存在 ——
    # 「跨任务引用被拒」需要一个真的跨任务引用，而那份引用要能从真台账里读得回来。
    other_job = _ok(
        session(
            (
                "submit_research",
                {
                    "scene_id": settings.profile,
                    "input_kind": "discovery_scope",
                    "input_ref": scope,
                    "idempotency_key": "responsibility-relations-2",
                },
            )
        ).calls[0]
    )
    assert other_job["state"] == "queued", other_job["reason"]
    other_items = SqliteJobStore(settings.database_path).work_items(other_job["job_id"])
    assert len(other_items) == 1
    other_work_item_id = other_items[0].work_item_id
    assert other_work_item_id != work_item_id, "两次受理算出了同一个工作单元 —— 那样跨任务负例测不到"

    # 那个「长得一模一样」的判定：**同一批证据、同一个答案、另一个工作单元**。
    # 它正是上一轮真实验收那条弱判据（「证据集合恰相同 + 答案取值恰相同」）分不出来的
    # 形状，所以它同时是「连线是按 id 走的」与「跨任务的引用被拒」两条判据的载体。
    # 输入文本刻意不同：同文本同工作单元会因 (work_item, provider, case) 幂等而重放。
    look_alike = _ok(
        session(
            (
                "decide",
                {
                    "work_item_id": other_work_item_id,
                    "spec_id": spec.spec_id,
                    "state": "Another page, judged under another piece of work.",
                    "evidence_refs": [evidence_id],
                },
            )
        ).calls[0]
    )
    assert look_alike["answers"][0]["choice"] == ANSWERED_RELATION
    assert look_alike["record_id"] != decided["record_id"]

    return {
        "session": session,
        "account_id": account_id,
        "work_item_id": work_item_id,
        "attempt_id": attempt_id,
        "record_id": decided["record_id"],
        "evidence_id": evidence_id,
        "spec_id": spec.spec_id,
        "other_work_item_id": other_work_item_id,
        "look_alike_record_id": look_alike["record_id"],
    }


def _settle(researched, **overrides) -> Any:
    """带着 ``researched`` 那几格结算一次。``overrides`` 换掉要摆的那一处。

    每次都是**一段新的会话**：这是刻意的 —— 结算在 MCP 面上从来就是一次独立调用，
    而「上一次会话里那些对象还在」不是这条链的保证。
    """
    arguments: dict[str, Any] = {
        "work_item_id": researched["work_item_id"],
        "attempt_id": researched["attempt_id"],
        "stop_reason": "goal_met",
    }
    arguments.update(overrides)
    return researched["session"](("submit_findings", arguments)).calls[0]


def _relation_row(**overrides) -> dict[str, Any]:
    """一条交上去的责任结论。``evidence_refs`` / ``decision_id`` 由调用方摆。"""
    row: dict[str, Any] = {"relation": ANSWERED_RELATION}
    row.update(overrides)
    return row


def _relation_rows(settings: Settings) -> tuple[Any, ...]:
    """关系表现在那几行。

    ``default_relation_store`` 是**工厂**（组合根只交出「怎么建一个」，不交出实例），
    所以这里要调一次。合成一处的理由是读法只有一份：哪一张表、按什么顺序，
    与 ``list_relations`` 自己钉住的那一条。
    """
    return default_relation_store(settings)().list_relations()


# ----------------------------------------------------------------------
# 正控：一次判定 → 一行关系
# ----------------------------------------------------------------------
def test_a_research_settlement_writes_the_responsibility_relation(
    researched, settings: Settings
) -> None:
    """**§3 的判据**：那条判定真的在台账里，而那条关系真的因此落表。

    四格一起断言，因为任何一格单独成立都说明不了这件事：

    * ``decision_record`` 里**按 id 读得回来**那条判定（不是「调用过 decide」——
      回执上有一个 ``record_id`` 与台账里真的有那一行是两件事）；
    * 回执里那份产出**写下来了**一行（而不是一条 ``REJECTED``）；
    * 表里那一行的主语**就是** canonical 账号，场地**就是**折进去的那一块；
    * 那一行挂的证据引用就是这条链上真的落盘的那一条。
    """
    submitted = _ok(
        _settle(
            researched,
            observations=[
                {
                    "statement": "俱乐部页面写明球场由 Example Garden Club 维护",
                    "evidence_refs": [researched["evidence_id"]],
                }
            ],
            relations=[
                _relation_row(
                    decision_id=researched["record_id"],
                    evidence_refs=[researched["evidence_id"]],
                )
            ],
        )
    )

    # --- 1. 判定真的在台账里（一次**按 id 读**的库，不是回执上抄下来的那一格）--
    stored = SqliteDecisionRecordStore(settings.database_path).get_decision(
        researched["record_id"]
    )
    assert stored is not None, "判定没落进台账 —— 回执上的 record_id 会是一个悬空的 id"
    assert stored["spec_id"] == researched["spec_id"]
    assert stored["subject_ref"] == researched["account_id"]

    # --- 2. 那一块逐行说它去哪了 ---------------------------------------------
    block = submitted["relations"]
    assert block["by_disposition"]["written"] == 1, (
        f"这一行没写进去：{block['outcomes']}\n"
        "（``by_disposition`` 里的 ``rejected`` 说的是依据不成立，那要看 outcomes 里"
        "那一句理由；``by_disposition`` 为 ``null`` 说的是这一格读不出来）"
    )
    assert sum(block["by_disposition"].values()) == 1
    (outcome,) = block["outcomes"]
    assert outcome["disposition"] == "written"
    assert outcome["written"] is True
    assert outcome["account_id"] == researched["account_id"]

    # --- 3. 表里那一行，主语与场地都要对得上 --------------------------------
    (row,) = _relation_rows(settings)
    assert row.account_id == researched["account_id"], (
        "关系挂到了另一个账号上 —— 而这一行在任何一份按账号读的报告里都是一条正常的关系"
    )
    assert row.relation is VenueRelation.MAINTAINS
    assert row.venue_asset_id, "关系没有场地 —— 那样它按场地读不出来，等于没写"
    assert list(row.evidence_ids) == [researched["evidence_id"]]
    assert row.first_seen_at is not None

    # 场地真的在池子里，而且就是折进去的那一块（不是「非空即可」）。
    assert default_asset_store(settings)().get_asset(row.venue_asset_id) is not None


# ----------------------------------------------------------------------
# 结果 → 那一次调用：连线要**写下来**，不是推出来
# ----------------------------------------------------------------------
def test_the_result_names_the_call_that_produced_it(
    researched, settings: Settings
) -> None:
    """**§B14 的判据**：从落表的那一行反查得出产生它的那次调用，而反查走的是 **id**。

    上一轮真实验收从关系表反查产生它的调用，只能走「证据集合恰相同 **且** 答案取值
    恰相同」—— 那一节自己写下了这条判据的弱处：同一个账号对同一批页面可以问出两个
    不同的问题，两条判定引用同一批证据、给出同一个取值，**集合相等分不出这两件事**。

    所以这一条要的是一个**长得一模一样的干扰项**：``look_alike`` 与产生这一行的
    那次判定引用同一批证据、答同一个关系，只有工作单元不同。它从台账里读得回来，
    也在「证据集合 + 答案取值」那条弱判据下与真的那一条不可分 —— 而这一行**只**
    指得出真的那一条。
    """
    _ok(
        _settle(
            researched,
            relations=[
                _relation_row(
                    decision_id=researched["record_id"],
                    evidence_refs=[researched["evidence_id"]],
                )
            ],
        )
    )
    (row,) = _relation_rows(settings)
    assert list(row.decision_record_ids) == [researched["record_id"]], (
        "这一行没说清它是哪一次判定得出的 —— 而「哪一次」不是从证据集合推得出来的"
        "（同一个账号的两条判定可以引用同一批证据、给出同一个取值）"
    )

    # --- 那一次调用读得回来，而它的四格关联都对得上 -------------------------
    records = SqliteDecisionRecordStore(settings.database_path)
    record = records.get_decision(row.decision_record_ids[0])
    assert record is not None, "行上写着一个读不回来的 id —— 那条连线是悬空的"
    assert record["work_item_id"] == researched["work_item_id"]
    assert record["subject_ref"] == researched["account_id"]
    assert record["spec_id"] == researched["spec_id"]
    assert record["spec_version"], "题面版本没跟着记录走 —— 改版之后分不出是哪一版判的"
    assert record["outcome"] == DecisionOutcome.ANSWERED.value
    assert record["mode"] == DecisionMode.ASSIST.value, "assist 是 §4 的边界，不是可选项"
    assert record["evidence_refs"] == [researched["evidence_id"]], (
        "判定引用的那几条材料与这一行挂的证据要是同一批 —— 否则「依据」在两条记录里"
        "各说各的"
    )
    assert record["answers"][0]["choice"] == row.relation.value, "答案是这一行的取值"

    # --- 干扰项：证据相同、答案相同、工作单元不同 ---------------------------
    look_alike = records.get_decision(researched["look_alike_record_id"])
    assert look_alike is not None
    assert look_alike["evidence_refs"] == record["evidence_refs"]
    assert look_alike["answers"] == record["answers"]
    assert look_alike["work_item_id"] != record["work_item_id"]
    assert researched["look_alike_record_id"] not in row.decision_record_ids, (
        "那一条长得一模一样的判定被写进了这一行的连线里 —— 按「证据集合 + 答案取值」"
        "反查的实现会把它一并算上，而它并不是产生这一行的那次调用"
    )


def test_a_decision_from_another_work_item_is_refused(
    researched, settings: Settings
) -> None:
    """**跨任务**引用（真台账、真存储）：那一条判定真的在台账里，只是记在别的工作上。

    与「id 不存在」那条负例分开，是因为它们红在两个不同的理由上：那一条是「指一个
    没发生过的调用」（笔误 / 伪造），这一条是「拿另一件工作的判断来支撑这一件」——
    后者在任何只核「这条记录在不在」的实现下都会被放行，而表里那一行会以「判定
    得出的」身份存在。

    这一条**不**沿用 ``researched`` 平时那次判定：它交的正是那个干扰项。
    """
    submitted = _ok(
        _settle(
            researched,
            relations=[
                _relation_row(
                    decision_id=researched["look_alike_record_id"],
                    evidence_refs=[researched["evidence_id"]],
                )
            ],
        )
    )
    assert submitted["relations"]["by_disposition"]["rejected"] == 1
    (outcome,) = submitted["relations"]["outcomes"]
    assert outcome["written"] is False
    assert "不是为这一件工作做的" in outcome["reason"]
    assert researched["other_work_item_id"] in outcome["reason"], (
        "理由里要指名那一条判定**实际**记在哪件工作上下 —— 只说「不是这一件」"
        "让人无从核对"
    )
    assert _relation_rows(settings) == ()


def test_a_decision_about_another_question_is_refused(
    researched, settings: Settings
) -> None:
    """**跨题面**的引用（真台账、真存储）：页档位那道题的记录读起来同样正常。

    它是第 4 条判据的端到端形态：另外两道题（页面档位、角色口径）的判定与被判的那
    一次在盘上完全同形，而拿它们当依据等于把「这一页是几档」当成了「谁负责」。
    """
    page_triage = compile_specs(load_profile(settings)).get(DecisionTask.PAGE_TRIAGE)
    assert page_triage is not None, "这份 profile 没有声明页档位题面 —— 这一条测不到"

    other = _ok(
        researched["session"](
            (
                "decide",
                {
                    "work_item_id": researched["work_item_id"],
                    "spec_id": page_triage.spec_id,
                    "state": "A staff page with names, roles and addresses.",
                },
            ),
            # 缺省替身答的是责任关系闭集里的 ``maintains`` —— 那道题里没有这个取值，
            # 拿它当页档位的答案是在测一个构造不出来的记录。``_Backend("jev")``
            # 答的是**题面自己**的第一项。
            backend=_Backend("jev"),
        ).calls[0]
    )
    assert other["spec_id"] == page_triage.spec_id

    submitted = _ok(
        _settle(
            researched,
            relations=[
                _relation_row(
                    decision_id=other["record_id"],
                    evidence_refs=[researched["evidence_id"]],
                )
            ],
        )
    )
    assert submitted["relations"]["by_disposition"]["rejected"] == 1
    assert "不是责任关系那道题" in submitted["relations"]["outcomes"][0]["reason"]
    assert _relation_rows(settings) == ()


# ----------------------------------------------------------------------
# 负控：四种「有结论、没有依据」
# ----------------------------------------------------------------------
def test_a_settlement_without_a_decision_writes_nothing(researched, settings: Settings) -> None:
    """判定 id 是编的 → 整次结算**照常成功**，而表里一行都没有。

    这正是 §3 逐字禁止的那几件事的形状（「看到官网就默认 owner」）：那条关系说得通、
    证据真的在盘上、产出里每一格都填了 —— 只是**没有任何一次判定发生过**。

    整次调用**不**失败是对的：一份产出里其余结论与这一条无关（§5 逐行判）。
    """
    submitted = _ok(
        _settle(
            researched,
            relations=[
                _relation_row(
                    decision_id="rec-nobody-ever-made-this",
                    evidence_refs=[researched["evidence_id"]],
                )
            ],
        )
    )
    assert submitted["relations"]["by_disposition"]["rejected"] == 1
    (outcome,) = submitted["relations"]["outcomes"]
    assert outcome["written"] is False
    assert "解不开" in outcome["reason"]
    assert _relation_rows(settings) == ()


def test_the_answer_cannot_be_swapped_for_another_relation(
    researched, settings: Settings
) -> None:
    """判定答的是 ``maintains``，而结论写的是 ``owns`` → 不写。

    这一格最隐蔽：那条记录**真的存在**、主语对、题面对、结论也是闭集里的合法取值
    —— 唯一不对的是「判定说的是另一件事」。只核前四步的实现会放行它，而表里那一行
    会以「判定得出的」身份存在。
    """
    submitted = _ok(
        _settle(
            researched,
            relations=[
                _relation_row(
                    relation="owns",
                    decision_id=researched["record_id"],
                    evidence_refs=[researched["evidence_id"]],
                )
            ],
        )
    )
    assert submitted["relations"]["by_disposition"]["rejected"] == 1
    assert "答的不是这个关系" in submitted["relations"]["outcomes"][0]["reason"]
    assert _relation_rows(settings) == ()


@pytest.mark.parametrize(
    "outcome", [DecisionOutcome.ABSTAINED, DecisionOutcome.UNAVAILABLE]
)
def test_an_abstention_is_not_a_relation_conclusion(
    researched, settings: Settings, outcome: DecisionOutcome
) -> None:
    """判定**真的发生过**、只是没给结论 → 同样不写。**两种**都不写。

    这一条与上一条不是同一件事：那一条是「拿错了判定」，这一条是「判定诚实地说
    它不知道」。两者都不该产出一行 —— 而从弃权推出一个取值，表里那一行读起来与
    一次正常的关系完全一样。

    两种结局都要过一遍，因为它们**处置是反的**（``abstained`` 是「这份输入答不了」，
    ``unavailable`` 是「这个部署缺一个后端 / 缺 checkpoint」），而两头都必须走到
    「不写」这一格：只测一头的话，另一头的实现可以是「照写，反正回执上有 outcome」。
    真台账里也确实能躺着这两种记录 —— 上一轮验收那 57 行全是 ``answered``，而
    「全都是 answered」**不是**「全部被确认」。
    """
    # 输入文本必须与 ``researched`` 里那一次**不同**：``decide`` 先按
    # ``(work_item, provider, case_id)`` 查一次台账，判过的同一个 case 直接返回那条
    # 记录、不碰后端（那是防重复付费的那一格）。复用同一段文本，这一条会拿到上一次
    # 那个 ``answered`` 的记录，而它测的东西就悄悄变成了「缓存命中」。
    backend = _Backend("jev", _cannot(outcome))
    decided = _ok(
        researched["session"](
            (
                "decide",
                {
                    "work_item_id": researched["work_item_id"],
                    "spec_id": researched["spec_id"],
                    "state": f"Nothing on this site says who is responsible for it ({outcome.value}).",
                },
            ),
            backend=backend,
        ).calls[0]
    )
    assert decided["outcome"] == outcome.value
    assert decided["answered"] is False

    submitted = _ok(
        _settle(
            researched,
            relations=[
                _relation_row(
                    decision_id=decided["record_id"],
                    evidence_refs=[researched["evidence_id"]],
                )
            ],
        )
    )
    assert submitted["relations"]["by_disposition"]["rejected"] == 1
    (refusal,) = submitted["relations"]["outcomes"]
    assert "没有给出结论" in refusal["reason"]
    assert outcome.value in refusal["reason"], (
        "理由里要报出**哪一种**没有结论 —— 弃权与不可用的处置是反的（一个是去换输入，"
        "一个是去接后端），而两句话读起来都只是「没写成」"
    )
    assert _relation_rows(settings) == ()


def test_evidence_belonging_to_another_account_refuses_the_whole_settlement(
    researched, settings: Settings
) -> None:
    """**§14 要的那个真实的跨账号负例**：证据属于另一个账号 → 整份产出被拒。

    它比「引用不存在」更值得留一条判据：那条证据**真的在盘上**、id 也是真的，任何
    只按存在性做的核对都会放行它 —— 而报告上那一行读起来是有依据的。

    整份拒绝（而不是丢掉那一条引用）是这条判据的一部分：丢掉它等于**改写**了这份
    结论的依据里属于谁。
    """
    SqliteEvidenceStore(settings.database_path).put_evidence(
        make_evidence(evidence_id=FOREIGN_EVIDENCE, account_id="acct-elsewhere")
    )
    call = _settle(
        researched,
        relations=[
            _relation_row(
                decision_id=researched["record_id"],
                evidence_refs=[FOREIGN_EVIDENCE],
            )
        ],
    )

    assert call.is_error, "跨账号的证据被放行了 —— 那条关系会带着别人的依据落表"
    assert "acct-elsewhere" in call.text
    assert researched["account_id"] in call.text
    assert _relation_rows(settings) == ()


def test_a_settlement_without_relations_has_no_relations_block(
    researched, settings: Settings
) -> None:
    """一条责任结论都没交 → 回执里**没有**那一块，而计数里 ``relations`` 是 0。

    这两件事不重复：计数答「交了几条」，那一块答「交的那几条去哪了」。一条都没交时
    后一个问题没有对象 —— 给它一个空块，读的人会把它读成「交了、但一条都没写」。
    """
    submitted = _ok(_settle(researched, gaps=["这一页没有说谁负责"]))
    assert submitted["counts"]["relations"] == 0
    assert "relations" not in submitted
    assert _relation_rows(settings) == ()


def _cannot(outcome: DecisionOutcome):
    """一个永远答不上来的后端 —— ``tests/test_mcp_decision_tools`` 里那个的复刻。

    不 import 它：那边是**私有**名字，而两份在这一层漂开时，红的是本文件的
    「没有给出结论」那一条 —— 一个指向实现而不是指向复刻件的方向。
    """

    def plan(case: Any) -> DecisionOutcome:
        return outcome

    return plan

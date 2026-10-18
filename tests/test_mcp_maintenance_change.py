"""§6 的端到端：研究真的落下新来源 ⇒ 维护账上多一条信号 ⇒ 下一次 tick 提前复核那一格。

## 这一条为什么值得单独一个文件

两个生产者与一个消费者分别落在两个**角色**上：写信号的是研究侧的
``submit_findings``（``_ROLE_RESEARCHER``），读信号的是控制侧的三条维护工具
（``_ROLE_CONTROLLER``）。所以它必然跨两个会话 —— 一份只铸一种令牌的用例写不出
这件事，而写不出的后果是：两个改动各自有自己的判据、都绿，接起来却是断的。

## 它证的是一条链，每一步都单独可断

1. 立项、复核一轮（``run_maintenance``）⇒ 三个主体各记下一条 ``SOURCE_COVERAGE``
   观测（§6 的第一条生产路径）。
2. 研究员把一件工作结算掉，引用的证据是一条**此前不在库里**的页面
   （``submit_findings`` → ``produce_routes``）⇒ 维护账上多一条变更信号
   （§6 的第二条生产路径）。
3. 再复核一轮 ⇒ 那一个主体的来源供给格**提前到期**，理由是 ``change_signalled``。

第 3 步与第 1 步的时序是要紧的：第一轮的观测把这一格推到 ``after_days`` 之后，
所以第二轮的到期**只可能**来自那条信号。没有第一轮的话，「反正从来没看过」
（``never_observed``）会让这一格本来就在到期清单里 —— 那条断言就白写了。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from marketops.adapters.mcp_server.product_loop import product_loop_handlers
from marketops.adapters.profile_loader import load_profile
from marketops.adapters.sqlite.evidence_store import SqliteEvidenceStore
from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.application.inputs import account_subjects
from marketops.config import Settings
from marketops.domain.enums import JobInputKind, SourceKind
from marketops.domain.evidence import Evidence
from marketops.domain.subject import SubjectDossier
from marketops.domain.util import sha256_text, utc_now
from marketops.interfaces.composition import (
    default_account_cohort_store,
    default_account_pool_store,
    default_asset_store,
    default_maintenance_store,
    default_relation_store,
    default_scene_store,
)
from marketops.security.catalog import Principal, Role, visible_tools
from marketops.security.tokens import TokenRing, mint
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload

#: 一次成立的立项。与 ``tests/test_mcp_product_loop.py`` 的 ``BASE_CREATE`` 同形 ——
#: 它是**测试输入**，真实那套住在 ``profiles/`` 里。
BASE_CREATE: dict[str, Any] = {
    "region": ["Devon"],
    "scenario": "natural-grass",
    "product": "acme",
    "role_policy": "role-matrix-v1",
    "source_plan": ["serper-search"],
    "budget": {"search": 20},
}

ACCOUNTS = """accounts:
  - account_id: example-one
    organisation: Example One Ltd
    cohort: known_success
    domain: example-one.co.uk
  - account_id: example-two
    organisation: Example Two Ltd
    cohort: known_success
  - account_id: example-three
    organisation: Example Three Ltd
    cohort: accessible_no_core_email
"""

#: 结算引用的那一页。**它此前不在库里** —— 那一格正是本条用例的全部内容。
#: 邮箱写在正文里而不是挂在字段上：``_researched_routes`` 只把 ``field is None``
#: 的行当材料（挂字段的那一条是**依据**，不是材料），所以这条也要长成一份材料。
PAGE_TEXT = """<html><body>
  <h1>Example One Ltd</h1>
  <p>Grounds Manager: Jane Doe</p>
  <p>Email: jane.doe@example-one.co.uk</p>
</body></html>"""

PAGE_EVIDENCE = "page-example-one-1"
WORKER = "worker-a"


def _ring(tmp_path: Path, principal: str, roles: list[Role]):
    token, digest = mint(principal, roles)
    path = tmp_path / f"tokens-{principal}.json"
    path.write_text(
        json.dumps(ring_payload(ring_entry(digest, principal, roles))), encoding="utf-8"
    )
    return TokenRing.from_file(path), token


def _ok(outcome, index: int = 0) -> dict[str, Any]:
    call = outcome.calls[index]
    assert not call.is_error, f"{call.name} 失败了：{call.text}"
    payload = call.as_json()
    assert isinstance(payload, dict), f"{call.name} 没有回结构化对象：{payload!r}"
    return payload


@pytest.fixture
def maintenance(settings: Settings):
    """**同一份**维护账工厂，两个会话共用。

    这正是组合根里的那一行（``interfaces/mcp/__main__.py``）：结算写的信号与维护
    tick 读的账必须是同一本。各建一份的形态是「结算写了、tick 永远看不见」，而
    两边的报告各自都正常 —— 那种断法在这条用例之外看不出来。
    """
    return default_maintenance_store(settings)


@pytest.fixture
def control(settings: Settings, tmp_path: Path, maintenance):
    """控制面会话：产品环那三条维护工具 + 场景存储。"""
    ring, token = _ring(tmp_path, "claude-control", [Role.CONTROLLER])
    scenes = default_scene_store(settings)
    handlers = product_loop_handlers(
        settings=settings,
        scenes=scenes,
        maintenance=maintenance,
        assets=default_asset_store(settings),
        relations=default_relation_store(settings),
        accounts=default_account_pool_store(settings),
        cohorts=default_account_cohort_store(settings),
        review=None,
        acquisition=None,
        router=None,
        documents=None,
    )

    def call(tool: str, arguments: dict[str, Any] | None = None):
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            product_loop=handlers,
            scenes=scenes,
            maintenance=maintenance,
            calls=((tool, arguments or {}),),
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    return call


@pytest.fixture
def researcher(settings: Settings, tmp_path: Path, maintenance):
    """研究面会话：只有 ``submit_findings`` 这一条路要用到它。

    ``maintenance`` 与上面那个 fixture **是同一个对象**（pytest 按名缓存），所以
    两个会话指向的是同一本账 —— 否则这条用例测的是「各写各的、各读各的」。
    """
    ring, token = _ring(tmp_path, WORKER, [Role.RESEARCHER])
    jobs = lambda: SqliteJobStore(settings.database_path)  # noqa: E731

    def call(tool: str, arguments: dict[str, Any] | None = None):
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            jobs=jobs,
            maintenance=maintenance,
            calls=((tool, arguments or {}),),
            want_tool_list=False,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome

    return call


def _file_the_page(settings: Settings) -> Evidence:
    """把那一页写进证据库 —— 「这一条此前不在库里」是本条用例的前提。

    ``field`` 缺省为 ``None``：挂了字段的那一条是**字段依据**，不是材料，绑定层
    会把它逐条列进 ``not_material``。
    """
    row = Evidence(
        workspace=settings.profile,
        evidence_id=PAGE_EVIDENCE,
        account_id="example-one",
        source_url="https://example-one.co.uk/contact",
        source_kind=SourceKind.FETCHED_HTML,
        literal_value=PAGE_TEXT,
        content_sha256=sha256_text(PAGE_TEXT),
        fetched_at=utc_now(),
    )
    assert row.field is None
    SqliteEvidenceStore(settings.database_path).put_evidence(row)
    return row


def _dispatched(settings: Settings, *, subject: str, key: str = "one") -> tuple[str, str]:
    """摆好「受理 → 编译 → 派发 → 领租约」，返回 ``(work_item_id, attempt_id)``。

    ``key`` 让同一个账号可以领到**两件**不同的工作。这不是为了凑数：上面那条
    「同一页第二次结算」的判据只有在两件工作之间才问得出来 —— 同一件工作的第二次
    结算会被 ``settle`` 按内容判成 ``replayed``，绑定根本不会跑。

    走存储动词而不是 ``submit_research`` 工具：本条用例要的是一个**指定的**主体
    （``example-one``），而走工具面就得先铸一个 controller 令牌再写一份清单，
    摆出来的东西却没多测到任何一样。四个动词各自的判据在
    ``tests/test_job_contract.py``，这里只用它们。
    """
    store = SqliteJobStore(settings.database_path)
    job = store.admit(
        scene_id=settings.profile,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="known-accounts",
        idempotency_key=f"maintenance-change-e2e-{key}",
        request_sha256=sha256_text(f"known-accounts:{key}"),
    )
    dossier = SubjectDossier(
        account_id=subject,
        organisation="Example One Ltd",
        domain="example-one.co.uk",
        country="GB",
        source_ref="tests/fixtures/known-accounts.yaml",
        input_sha256=sha256_text(f"row:{subject}"),
    )
    (item,) = store.compile_work_items(
        job.job_id, (dossier,), scene=_scene_files(settings)
    )
    store.map_execution(item.work_item_id, quay_task_id=f"quay-task-{key}")
    attempt = store.lease(item.work_item_id, holder=WORKER)
    return item.work_item_id, attempt.attempt_id


def _scene_files(settings: Settings):
    """编译时钉住的那批字节 —— 与工具面读到的是**同一份**（盘上此刻的 profile）。"""
    return load_profile(settings).scene_files


def _subjects(settings: Settings) -> tuple[str, ...]:
    """账号池那份清单里的主体 —— 与 ``get_maintenance_plan`` 读的是同一份。"""
    return tuple(
        subject_id
        for _, subject_id in account_subjects(settings.inputs_dir, "known-accounts")
    )


def test_a_settlement_that_adds_sources_pulls_the_next_tick_forward(
    settings: Settings, control, researcher
) -> None:
    """一次结算真的落了新来源 ⇒ 下一轮复核以 ``change_signalled`` 提前到期。"""
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    (settings.inputs_dir / "known-accounts.yaml").write_text(ACCOUNTS, encoding="utf-8")
    assert set(_subjects(settings)) == {"example-one", "example-two", "example-three"}

    # --- 第一轮：立项 → 复核。三格各记下一条观测。 ---------------------------
    created = _ok(control("create_scene", {**BASE_CREATE, "activate": True}))
    assert created["scene"]["state"] == "active"
    first = _ok(control("run_maintenance", {"name": "known-accounts"}))
    assert first["claimed_count"] == 9
    assert {row["subject_id"] for row in first["observations"]} == {
        "example-one",
        "example-two",
        "example-three",
    }
    assert len(first["done"]) == 3, "数本地记录的那三格做成了"

    # 这一格现在**不该**到期：它刚刚被看过。下面那条断言全靠这一步。
    #
    # 而**整份计划**并不因此是空的：另外六格（页面面）从来没有记下过观测 ——
    # 这三个账号名下一条证据都没有，所以那两格这一趟没有可看的页面。它们照旧
    # 排在计划上，这是对的行为（一条没做成的复核不该变成「做过了」），但它意味着
    # 「计划空了」不再是这一格「刚看过」的证据。
    between = _ok(control("get_maintenance_plan", {"name": "known-accounts"}))
    assert ("example-one", "source_coverage") not in {
        (row["subject_id"], row["aspect"]) for row in between["due"]
    }, "第一轮记下观测之后，这一格被推到 after_days 之后 —— 它如果到期，只可能来自信号"
    assert between["due_count"] == 6, "剩在计划上的只有页面那六格"

    # --- 中间：研究员结算一件工作，引用的是一条**新**来源。 -------------------
    page = _file_the_page(settings)
    work_item_id, attempt_id = _dispatched(settings, subject="example-one")
    settled = _ok(
        researcher(
            "submit_findings",
            {
                "work_item_id": work_item_id,
                "attempt_id": attempt_id,
                "stop_reason": "goal_met",
                "observations": [
                    {"statement": "页面上写着场地经理与邮箱", "evidence_refs": [page.evidence_id]}
                ],
                "candidates": [],
                "gaps": [],
            },
        )
    )

    routes = settled["routes"]
    assert routes["error"] is None, routes["error"]
    assert routes["evidence_written"] > 0, (
        "这一趟真的落下了新证据行 —— 没有它就没有「来源变了」这回事"
    )
    assert [row["subject_id"] for row in routes["signals"]] == ["example-one"]
    assert routes["signals"][0]["aspect"] == "source_coverage"
    assert "新落了" in routes["signals"][0]["reason"]

    # --- 第二轮：那一格提前到期。 -------------------------------------------
    second = _ok(control("run_maintenance", {"name": "known-accounts"}))
    pulled = [
        row
        for row in second["done"]
        if row["subject_id"] == "example-one" and row["aspect"] == "source_coverage"
    ]
    assert len(pulled) == 1, (
        f"这一格该被那条信号提前拉出来。第二轮做了：{second['done']!r}"
    )
    assert pulled[0]["reason"] == "change_signalled", (
        "理由必须是**信号**那一档，不是 cadence —— 两者处置相同，但报告要分得开："
        "前者说「有人报了这一格变了」，后者说「按计划该复核了」"
    )


def test_the_settlement_says_so_when_there_is_no_maintenance_account(
    settings: Settings, tmp_path: Path
) -> None:
    """没接维护账的实例：结算照常跑，回执里**明写**那是接线事实。

    这条与上面那条是一对。只有正常路径的话，一个「信号永远写不进去」的实现在
    两份报告上都正常 —— 而 ``routes.signals`` 为空与「这一趟真的没有新来源」
    在产出上同形，回执里那一句是唯一分得开它们的地方。
    """
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    (settings.inputs_dir / "known-accounts.yaml").write_text(ACCOUNTS, encoding="utf-8")
    _file_the_page(settings)
    work_item_id, attempt_id = _dispatched(settings, subject="example-one")

    ring, token = _ring(tmp_path, WORKER, [Role.RESEARCHER])
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        jobs=lambda: SqliteJobStore(settings.database_path),
        calls=(
            (
                "submit_findings",
                {
                    "work_item_id": work_item_id,
                    "attempt_id": attempt_id,
                    "stop_reason": "goal_met",
                    "observations": [
                        {
                            "statement": "页面上写着场地经理与邮箱",
                            "evidence_refs": [PAGE_EVIDENCE],
                        }
                    ],
                    "candidates": [],
                    "gaps": [],
                },
            ),
        ),
        want_tool_list=False,
    )
    assert not outcome.handshake_error, outcome.handshake_error
    routes = _ok(outcome)["routes"]

    assert routes["error"] is None, "少一本账不该让绑定这一趟停下来"
    assert routes["evidence_written"] > 0, "绑定、判时效、定级、落盘照常跑过"
    assert routes["signals"] == []
    assert "没有接维护账" in routes["note"], (
        "空信号必须**说出口** —— 缺的是一格接线，不是一条业务结论，两者的处置不同"
    )


def test_a_second_settlement_of_the_same_page_is_not_a_change(
    settings: Settings, control, researcher
) -> None:
    """第二件工作引用了**同一页**：绑定照跑、行照写，但一条新行都没有 ⇒ 没有信号。

    这一条与第一条合起来才钉得住判据。只测「有新来源时有信号」的话，一个
    「只要绑出东西就报变化」的实现照样绿 —— 而那样的实现会让每一格在每一次结算
    之后都提前到期一次：复核退化成空转，理由还是一句假话。

    所以这里刻意让第二趟**看起来很像**第一趟：同一个账号、同一页、同一条引用，
    差别只在「这一页早就在库里了」。
    """
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    (settings.inputs_dir / "known-accounts.yaml").write_text(ACCOUNTS, encoding="utf-8")
    _ok(control("create_scene", {**BASE_CREATE, "activate": True}))
    _ok(control("run_maintenance", {"name": "known-accounts"}))

    # 第一件工作：这一页是新的 ⇒ 一条信号 ⇒ 下一轮把它拉出来、做掉、再记一条观测。
    page = _file_the_page(settings)
    findings = {
        "stop_reason": "goal_met",
        "observations": [
            {"statement": "页面上写着场地经理与邮箱", "evidence_refs": [page.evidence_id]}
        ],
        "candidates": [],
        "gaps": [],
    }
    first_item, first_attempt = _dispatched(settings, subject="example-one", key="one")
    first = _ok(researcher("submit_findings", {**findings, "work_item_id": first_item, "attempt_id": first_attempt}))
    assert [row["subject_id"] for row in first["routes"]["signals"]] == ["example-one"]

    pulled = _ok(control("run_maintenance", {"name": "known-accounts"}))
    assert [row["subject_id"] for row in pulled["done"]] == ["example-one"], (
        "第一趟的信号该把这一格拉出来做掉 —— 不做掉的话，下面那条断言分不出"
        "「信号已应答」与「信号根本没生效」"
    )
    quiet = _ok(control("get_maintenance_plan", {"name": "known-accounts"}))
    # 刚复核过的那一格不该还挂在计划上。**整份计划里只剩页面那六格**：它们
    # 从来没有记下过观测，所以照旧排在计划上（三个账号这一轮只有 example-one
    # 名下有页面，而它那两格缺接线）。
    assert ("example-one", "source_coverage") not in {
        (row["subject_id"], row["aspect"]) for row in quiet["due"]
    }, "刚复核过的格子不该还挂在计划上"
    assert quiet["due_count"] == 6

    # 第二件工作：同一个账号、**同一页** ⇒ 一条新行都没有。
    second_item, second_attempt = _dispatched(settings, subject="example-one", key="two")
    second = _ok(
        researcher("submit_findings", {**findings, "work_item_id": second_item, "attempt_id": second_attempt})
    )
    routes = second["routes"]
    assert routes["error"] is None, routes["error"]
    assert routes["evidence_written"] > 0, (
        "绑定跑过了，行也照写（``put_evidence`` 按内容寻址，重写同一行是幂等的）—— "
        "所以「这一趟落了行」与「这一趟有**新**来源」在这一格上必须分开看"
    )
    assert routes["signals"] == [], (
        "一行都不是新的 —— 判据是「这一条此前在不在库里」，不是「这一趟绑没绑出东西」"
    )

    after = _ok(control("get_maintenance_plan", {"name": "known-accounts"}))
    assert ("example-one", "source_coverage") not in {
        (row["subject_id"], row["aspect"]) for row in after["due"]
    }, "没有新来源 ⇒ 没有信号 ⇒ 没有格子被提前拉出来"
    assert after["due_count"] == quiet["due_count"], (
        "两趟之间的计划**一格都没变**（那六格页面面从头到尾都排在计划上）—— 这一条"
        "比「前者是 0」更强：它说的是「这一趟结算没有让任何一格提前到期」"
    )


def test_the_two_tools_list_separately_for_the_two_roles(control, researcher) -> None:
    """两个角色各自看得见自己那一条 —— 这条用例跨两个面才写得出来。

    它挡的是一种**看不见**的接错法：把 ``submit_findings`` 铸到 controller 上
    （或反之），上面两条用例会在**权限**上红，而红的理由看起来像被测的行为坏了。
    """
    control_tools = visible_tools(Principal("claude-control", frozenset({Role.CONTROLLER})))
    research_tools = visible_tools(Principal(WORKER, frozenset({Role.RESEARCHER})))

    assert "run_maintenance" in control_tools
    assert "submit_findings" not in control_tools
    assert "submit_findings" in research_tools
    assert "run_maintenance" not in research_tools

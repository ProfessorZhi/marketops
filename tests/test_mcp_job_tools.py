"""Phase G 的两个控制面工具：``submit_research`` 与 ``get_job``（``MCP_SERVICE.md`` §2）。

## 为什么这些用例真的走一遍 MCP

见 ``tests/mcp_harness.py``：直接调 ``_submit_research`` 会绕过认证、中间件与
``tools/list`` 的过滤。本文件里每一条都经过真实的 Bearer 令牌与真实的
``RoleGuard``，而不是绕开它们。

## 每条用例都起一个**新的**服务实例

``submit_research`` 的成功判据不是「返回了一个 id」，而是**断开重连之后同一个
幂等键还是同一个作业**（§8B 逐字：Disconnect/reconnect the controller and query
the same job）。``drive_with_settings`` 每次调用都重新 ``build_server``，而作业
存储是同一个文件 —— 这是本文件里唯一能让「持久」这件事被真的测到的形状。用
一个进程内的服务连着调三次，三次都会通过，而那个作业可能根本不在盘上。

## 三件「报 0 就是撒谎」的事

``get_job`` 有三格最容易写成一个漂亮的数字：产出条数、用量、被截断的条数。
三格在本文件里各有一条判据，而且都断言**不是一个数字**：

* 产出在**还没有人结算**时报 ``available: false`` + 原因（报 0 会被读成一次
  诚实的空结果 —— 而那条判据的另一侧在 ``test_mcp_assignment_tools.py``）；
* 用量在没有 work item 时**不报 0**（0 会被读成「一次检索都没花」）；
* 某个账号的额度没配置时**不报 0**（账本自己立过这条判据：``remaining`` 对
  没配置的科目抛，不返回 0 —— 而调用方看到 0 的正常反应是「停下来报告预算
  耗尽」，于是忘写配置会伪装成一次正常的预算耗尽）。

## 受理之后立刻编译**并派发**，所以这份清单必须真的在盘上

``_submit`` 的缺省输入是一份**真的**已知账号清单（``inputs`` fixture 写进
``Settings.inputs_dir``），因为 ``submit_research`` 受理之后就会把 ``input_ref``
编译成 work item。用一个读不出来的 ``input_ref`` 当缺省，会让这份文件里每一条
判据都建在一个 ``BLOCKED`` 的作业上 —— 那时它们测的是阻塞路径，而不是读取路径。
写不出来的那两种输入（``source_receipt`` / ``discovery_scope``）各有一条自己的
用例，见文件末尾。

## 缺省的执行器是**没有**

``control`` 那条驱动路径缺省不传 ``dispatcher`` —— 那是一个合法部署，也是一个需要被
测的对象（``dispatch`` 那一格如实报 ``executor_configured: false``）。派发那几条
判据要的是**记账的外部执行器**（``tests/fakes.py``）：它们问的不是「工具返回了
什么」，而是「**外部被碰了几次**」—— 一个每次都重试的实现会让前者照旧全绿。

## 派发那一格还要说得出服务的是**计划里的哪一步**（§B16 关闭条件 7）

条件 7 的五个被选者里 执行器 是最后一段。``dispatch.source_method`` 那一格给了这条
工具面一个锚点，而它的判别力同样落在 ``outside`` 上：一个恒填常量的实现能让「回显」
全绿，一个先建任务后校验的实现能让「拒绝」全绿。两条判据（一条正控、一条反例）
在文件末尾，与 ``control_job`` 的 resume 那条一起构成 派发这一锚的两条来路。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from marketops.adapters.sqlite.jobs import SqliteJobStore
from marketops.adapters.sqlite.ledger import SqliteBudgetLedger
from marketops.config import Settings
from marketops.domain.errors import BudgetNotConfiguredError
from marketops.domain.enums import BudgetLine
from marketops.security.catalog import Role
from marketops.security.tokens import TokenRing, mint
from tests.fakes import FakeDispatcher
from tests.mcp_harness import drive_with_settings, ring_entry, ring_payload, write_accounts

SCENE_NOTE = "MARKETOPS_PROFILE"

#: 缺省输入清单里的两个账号。清单里的 id 与顺序**就是**工作单元的
#: ``subject_ref`` 与 ``ordinal``，所以这份清单同时是下面几条判据的期望值。
DEFAULT_INPUTS = ("devon-2026", ("acct-0001", "acct-0002"))


@pytest.fixture
def store_factory(settings: Settings):
    """同一个库文件上的工厂 —— 每次调用开一个新连接，见 ``tools.control_handlers``。"""
    return lambda: SqliteJobStore(settings.database_path)


@pytest.fixture
def ledger_factory(settings: Settings):
    return lambda: SqliteBudgetLedger(settings.database_path)


@pytest.fixture
def inputs(settings: Settings) -> Path:
    return write_accounts(settings, *DEFAULT_INPUTS)


@pytest.fixture
def outside() -> FakeDispatcher:
    """记账的外部执行器 —— 派发那几条判据的观察面（见 ``tests/fakes.py``）。"""
    return FakeDispatcher()


@pytest.fixture
def control(
    settings: Settings, store_factory, ledger_factory, inputs: Path, outside: FakeDispatcher
):
    """一个 controller 会话的驱动器。每次调用都是一段全新的会话。

    ``outside`` 也进来了：派发那几条判据要问的不是「工具返回了什么」，而是
    「**外部被碰了几次**」—— 一个每次都重试的实现会让前者照旧全绿。
    """
    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring = TokenRing.from_file(_ring_file(settings, digest))

    def call(
        tool: str,
        arguments: dict | None = None,
        *,
        with_ledger: bool = False,
        with_dispatcher: bool = False,
    ):
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            calls=((tool, arguments or {}),),
            want_tool_list=False,
            dispatcher=outside if with_dispatcher else None,
            jobs=store_factory,
            ledger_factory=ledger_factory if with_ledger else None,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        return outcome.call(tool)

    return call


def _ring_file(settings: Settings, digest: str):
    """写一个只含一枚令牌的环，并返回路径。"""
    path = settings.data_root / "test-tokens.json"
    path.write_text(
        json.dumps(
            ring_payload(ring_entry(digest, "claude-control", ["controller"]))
        ),
        encoding="utf-8",
    )
    return path


def _submit(call, *, with_dispatcher: bool = False, **overrides) -> dict:
    arguments = {
        "scene_id": "uk-acme-natural-grass",
        "input_kind": "known_accounts",
        "input_ref": DEFAULT_INPUTS[0],
        "idempotency_key": "discovery-v1-arm-b",
    }
    arguments.update(overrides)
    outcome = call("submit_research", arguments, with_dispatcher=with_dispatcher)
    assert not outcome.is_error, outcome.text
    return outcome.as_json()


# ----------------------------------------------------------------------
# 受理
# ----------------------------------------------------------------------
def test_submit_research_returns_a_durable_job_id(control) -> None:
    body = _submit(control)

    assert len(body["job_id"]) == 32
    assert body["state"] == "queued"
    assert body["input_kind"] == "known_accounts"
    assert body["input_ref"] == DEFAULT_INPUTS[0]
    assert body["scene_id"] == "uk-acme-natural-grass"
    # 受理之后立刻编译：清单里的账号在这一刻变成 durable 的 work item，
    # 否则「受理了」与「有活干」之间没有任何东西把它们连起来。
    assert body["work_items"]["count"] == len(DEFAULT_INPUTS[1])
    assert body["work_items"]["blocker"] == ""


def test_reconnecting_returns_the_same_job_for_the_same_key(control) -> None:
    """§8B 逐字：Disconnect/reconnect the controller and query the same job。

    三次调用是三段**独立**的会话（三次 ``build_server``、三次握手、三次调用），
    只有 ``job_id`` 的相等能证明这个作业真的落在了盘上。
    """
    first = _submit(control)
    again = _submit(control)

    assert again["job_id"] == first["job_id"]

    read = control("get_job", {"job_id": first["job_id"]})
    assert not read.is_error, read.text
    assert read.as_json()["job"]["job_id"] == first["job_id"]


def test_the_same_key_with_different_input_is_a_conflict(control) -> None:
    _submit(control)
    outcome = control("submit_research", {
        "scene_id": "uk-acme-natural-grass",
        "input_kind": "discovery_scope",
        "input_ref": "cornwall-2026-09",
        "idempotency_key": "discovery-v1-arm-b",
    })

    assert outcome.is_error
    assert "已经用于另一个请求" in outcome.text


def test_a_different_input_kind_under_the_same_key_is_a_conflict(control) -> None:
    """同一个 ``input_ref`` 字符串、不同的输入种类，是两个请求。

    少了 ``input_kind`` 进散列这条判据，上面那条会过而这条不会 —— 而它们的
    失败形态是同一个：调用方以为追加了一次研究，实际什么都没发生。
    """
    _submit(control, input_kind="discovery_scope")
    outcome = control("submit_research", {
        "scene_id": "uk-acme-natural-grass",
        "input_kind": "known_accounts",
        "input_ref": DEFAULT_INPUTS[0],
        "idempotency_key": "discovery-v1-arm-b",
    })

    assert outcome.is_error
    assert "已经用于另一个请求" in outcome.text


def test_an_unknown_input_kind_names_the_closed_set(control) -> None:
    outcome = control("submit_research", {
        "scene_id": "uk-acme-natural-grass",
        "input_kind": "the_whole_internet",
        "input_ref": "devon-2026-09",
        "idempotency_key": "k1",
    })

    assert outcome.is_error
    for kind in ("discovery_scope", "known_accounts", "source_receipt"):
        assert kind in outcome.text


def test_another_scene_is_refused_by_the_job_tools_too(control) -> None:
    """与 ``get_scene`` 是同一条判据（``tools.require_scene``）。

    两处各写一份的后果是受理时放行的场景与读取时放行的场景先分家，而分家的
    形态是一个读得回来、却永远看不到自己规则的作业。
    """
    outcome = control("submit_research", {
        "scene_id": "de-acme-natural-grass",
        "input_kind": "discovery_scope",
        "input_ref": "devon-2026-09",
        "idempotency_key": "k1",
    })

    assert outcome.is_error
    assert SCENE_NOTE in outcome.text


@pytest.mark.parametrize("field", ["input_ref", "idempotency_key"])
def test_an_empty_field_is_refused(control, field: str) -> None:
    outcome = control("submit_research", {
        "scene_id": "uk-acme-natural-grass",
        "input_kind": "discovery_scope",
        "input_ref": "devon-2026-09",
        "idempotency_key": "k1",
        field: "   ",
    })
    assert outcome.is_error


def test_the_admission_reports_where_the_budget_ceiling_comes_from(control) -> None:
    """受理时把**额度从哪来**一并说清，而且说的是每个账号而不是整个作业。

    一次运行的总额度是「每账号上限 × 实际跑起来的账号数」，本层不假装知道
    后面那个数 —— 报一个作业级总额度会是一个**没有人算出来过**的数字。
    """
    budget = _submit(control)["budget"]

    assert budget["per_account_ceiling"] == 20
    assert budget["source"].endswith("search.max_queries")
    assert "<account_id>" in budget["scope_shape"]


# ----------------------------------------------------------------------
# 读取：坏输入是结果，不是崩溃
# ----------------------------------------------------------------------
def test_get_job_on_an_unknown_id_is_a_result_not_a_crash(control) -> None:
    outcome = control("get_job", {"job_id": "0" * 32})

    assert outcome.is_error
    assert "取消是一个状态" in outcome.text


# ----------------------------------------------------------------------
# 读取：三件「报 0 就是撒谎」的事
# ----------------------------------------------------------------------
def test_get_job_does_not_report_zero_results(control) -> None:
    """**没有人结算过**时不报 0 条产出，并说明原因。

    ``available: false`` 曾是「``submit_findings`` 还没落地」的同义词。G3 落地
    之后它换了一个更站得住的意思：**还没有任何 work item 结算**。判据因此不再
    引用阶段号（那是一句会过期的散文），只断言这一格现在真的在说那件事。

    另一侧在 ``tests/test_mcp_assignment_tools.py``：结算过、产出为空时
    ``available`` 必须转真，且 ``totals`` 全为 0 —— 那条才是「诚实的零」。
    两条合起来，「跑过了、什么都没找到」与「还没有人结算过」才分得开；只有
    这一条的话，一个恒假的 ``available`` 也能让它变绿。
    """
    results = control("get_job", {"job_id": _submit(control)["job_id"]}).as_json()["results"]

    assert results["available"] is False
    assert "结算" in results["blocker"]
    assert "stored" not in results


def test_get_job_reports_usage_as_unavailable_when_there_is_no_ledger(control) -> None:
    usage = control("get_job", {"job_id": _submit(control)["job_id"]}).as_json()["usage"]

    assert usage["reported"] is False
    assert "ledger_factory" in usage["blocker"]


def test_get_job_reports_no_usage_before_any_work_item_exists(control) -> None:
    """还没有 work item 时**不报 0**。

    ``spent: 0`` 会被读成「一次检索都没花」，而真实情况是「还不知道该问谁的
    额度」。这两件事在报告里长得一样，处置却不同。

    这个作业一件工作都没有，是因为它的输入在受理时被判为 ``blocked``（那个范围
    名下没有一份发现回执）—— 受理之后编译那一步没有东西可编译。
    """
    job_id = _submit(control, input_kind="discovery_scope", idempotency_key="no-work")[
        "job_id"
    ]
    body = control("get_job", {"job_id": job_id}, with_ledger=True).as_json()

    assert body["work_items_count"] == 0
    assert body["usage"]["reported"] is False
    assert "accounts" not in body["usage"]
    assert "一次检索都没花" in body["usage"]["blocker"]


def test_get_job_reports_usage_per_account_and_marks_the_unconfigured_one(
    control, settings: Settings, store_factory, ledger_factory
) -> None:
    """两个账号，只有一个配了额度。

    没配的那个报 ``configured: false``，**不报 0** —— 与账本自己的判据同源
    （``adapters/sqlite/ledger.py::remaining`` 对没配置的科目抛，不返回 0）。
    """
    job = _submit(control)
    items = store_factory().work_items(job["job_id"])
    # 只给第一个账号配额度；第二个什么都不配。
    ledger_factory().set_ceiling(
        f"uk-acme-natural-grass:{items[0].subject_ref}",
        BudgetLine.SEARCH,
        20,
        source="profile:uk-acme-natural-grass:search.max_queries",
    )

    usage = control(
        "get_job", {"job_id": job["job_id"]}, with_ledger=True
    ).as_json()["usage"]

    assert usage["reported"] is True
    by_subject = {row["subject_ref"]: row for row in usage["accounts"]}
    assert by_subject["acct-0001"]["configured"] is True
    assert by_subject["acct-0001"]["lines"][0]["remaining"] == 20
    assert by_subject["acct-0002"]["configured"] is False
    assert by_subject["acct-0002"]["reason"] == "no_ceiling"
    assert "lines" not in by_subject["acct-0002"]


class _RemainingIsZeroWhenUnconfigured(SqliteBudgetLedger):
    """一个**错**的账本：没配置的科目返回 0，而不是抛。

    这正是 ``adapters/sqlite/ledger.py::remaining`` 的 docstring 逐字禁止的
    形状 —— 「返回 0 会让『这个科目没配置』与『这个科目已经用完』变成同一个
    数字，而调用方看到 0 的正常反应是『停下来报告预算耗尽』，于是忘写配置
    会伪装成一次正常的预算耗尽」。
    """

    def remaining(self, scope: str, line: BudgetLine) -> int:  # type: ignore[override]
        try:
            return super().remaining(scope, line)
        except BudgetNotConfiguredError:
            return 0


def test_the_unconfigured_account_criterion_actually_discriminates(
    settings: Settings, store_factory, ledger_factory, control
) -> None:
    """正对照：上面那条 ``configured: false`` 在一个「回空表」的账本上必须变红。

    没有它，那条判据在「账本其实没在回答这个问题」时依然全绿 —— 而它守的
    正是「没配置 ≠ 用完了」。两件事的处置不同：一个要去看配置，一个要去看
    这次跑得对不对。
    """
    job = _submit(control)
    item = store_factory().work_items(job["job_id"])[0]

    def usage_of(ledger) -> dict:
        token, digest = mint("claude-control", [Role.CONTROLLER])
        ring = TokenRing.from_file(_ring_file(settings, digest))
        outcome = drive_with_settings(
            settings=settings,
            tokens=ring,
            token=token,
            calls=(("get_job", {"job_id": job["job_id"]}),),
            want_tool_list=False,
            jobs=store_factory,
            ledger_factory=lambda: ledger,
        )
        assert not outcome.handshake_error, outcome.handshake_error
        body = outcome.call("get_job").as_json()
        return {row["subject_ref"]: row for row in body["usage"]["accounts"]}[item.subject_ref]

    assert usage_of(ledger_factory())["configured"] is False
    wrong = usage_of(_RemainingIsZeroWhenUnconfigured(settings.database_path))
    assert wrong["configured"] is True
    assert wrong["lines"] == []  # 一个空列表，读起来就是「配了、还没花」


# ----------------------------------------------------------------------
# 读取：真实的 派发映射与租约
# ----------------------------------------------------------------------
def test_get_job_reports_the_dispatch_task_and_the_lease(
    control, store_factory
) -> None:
    """``get_job`` 要读的是**真实的** task/attempt 链接，不是作业自己的复述。

    这里直接从存储侧派发（``map_execution`` + ``lease``）—— 那是 Phase G5
    的派发路径今天还没有的入口，而读的那一侧必须现在就能如实读出来。
    """
    job = _submit(control)
    store = store_factory()
    item = store.work_items(job["job_id"])[0]
    store.map_execution(item.work_item_id, dispatch_task_id="TASK-777")
    store.lease(item.work_item_id, holder="worker-a")

    body = control("get_job", {"job_id": job["job_id"]}).as_json()
    row = body["work_items"][0]

    assert row["execution"] == {"dispatch_task_id": "TASK-777", "mapped_at": row["execution"]["mapped_at"]}
    assert row["attempts"][0]["holder"] == "worker-a"
    assert row["attempts"][0]["state"] == "leased"
    assert row["stop_reason"] is None
    # 两个 work item、只派发了一个：三个桶各自要有数，而 ``not_started`` 那格
    # 正是「不许把没开始的读成 0」—— 一个恒为 0 的桶与没有这个桶是同一件事。
    assert body["progress"] == {
        "work_items": len(DEFAULT_INPUTS[1]),
        "dispatching": 1,
        "stopped": 0,
        "not_started": len(DEFAULT_INPUTS[1]) - 1,
        "note": body["progress"]["note"],
    }


def test_truncation_of_work_items_is_visible(control, settings: Settings) -> None:
    """§11：禁止静默截断。51 条里读 50 条必须自报。

    一条读回 50 条而不说的接口，会让「这个作业只有 50 个候选」成为报告里的
    一个事实。
    """
    from marketops.adapters.mcp_server.tools import MAX_WORK_ITEMS

    subjects = tuple(f"acct-{index:04d}" for index in range(MAX_WORK_ITEMS + 1))
    write_accounts(settings, "big-list", subjects)
    job = _submit(
        control, input_ref="big-list", idempotency_key="truncation-v1"
    )

    body = control("get_job", {"job_id": job["job_id"]}).as_json()

    assert body["work_items_count"] == MAX_WORK_ITEMS + 1
    assert len(body["work_items"]) == MAX_WORK_ITEMS
    assert body["work_items_truncated"] is True


# ----------------------------------------------------------------------
# 阻塞：输入产不出工作单元时，受理不许说成正常
# ----------------------------------------------------------------------
def test_an_input_that_yields_nothing_blocks_the_job_and_says_why(control) -> None:
    """§2 逐字：submit_research 返回 truthful queued/blocked state。

    受理一个产不出工作单元的作业却报 queued，等于说「排上了」—— 而它永远排不上：
    没有任何东西会把它编译出工作单元。

    这条判据原来用的理由是「这一类没有生产者」。三类都有生产者之后，这一格换了
    一条阻塞理由（盘上没有那一轮发现写出的回执），而**判据本身一字未改** ——
    它问的一直是同一件事：一个零工作单元的作业必须是 ``blocked``，而且状态与理由
    **是作业自己的**，不是回执现编的（读回来必须逐字相同）。
    """
    body = _submit(control, input_kind="discovery_scope", idempotency_key="blocked-v1")

    assert body["state"] == "blocked"
    assert "生产者" not in body["reason"]
    assert body["work_items"]["count"] == 0
    assert body["work_items"]["blocker"] == body["reason"]

    # 读回来的是同一件事：状态与理由是作业自己的，不是回执现编的。
    reread = control("get_job", {"job_id": body["job_id"]}).as_json()["job"]
    assert reread["state"] == "blocked"
    assert reread["reason"] == body["reason"]


def test_a_missing_list_blocks_the_job_instead_of_crashing(control, settings: Settings) -> None:
    """清单不存在是**部署的事实**（把文件放上去就没了），不是调用方给错了。

    所以它走阻塞那条通道，理由里带着服务去看的那个路径 —— 只说「清单不存在」
    的话，运维不知道该把文件放到哪儿。
    """
    body = _submit(control, input_ref="never-written", idempotency_key="missing-v1")

    assert body["state"] == "blocked"
    assert str(settings.inputs_dir / "never-written.yaml") in body["reason"]
    assert body["work_items"]["count"] == 0


def test_a_list_that_appears_later_does_not_need_a_new_job(
    control, settings: Settings
) -> None:
    """补齐清单之后，用**同一个幂等键**重发就该拿到同一个作业，而且它编译出来了。

    这条判据的一半在受理侧：阻塞不许把那个键废掉。少了它，「先受理、发现缺文件、
    补上、重发」这条最正常的运维路径会撞上幂等冲突，而调用方唯一的出路是换一个
    键 —— 于是报告里出现两个作业，一个是空跑的。
    """
    first = _submit(control, input_ref="late-2026", idempotency_key="late-v1")
    assert first["state"] == "blocked"

    write_accounts(settings, "late-2026", ("acct-0001",))
    again = _submit(control, input_ref="late-2026", idempotency_key="late-v1")

    assert again["job_id"] == first["job_id"]
    assert again["work_items"]["count"] == 1
    assert again["work_items"]["blocker"] == ""
    # 状态**没有**跟着从 blocked 变成 queued：解除阻塞是控制端的一个动作
    # （``control_job`` 的 resume），受理不是往回写状态的地方。
    assert again["state"] == "blocked"


def test_a_row_that_is_only_a_slug_blocks_before_any_work_item_exists(
    control, settings: Settings
) -> None:
    """输入里有一行拿不出身份 ⇒ **一个 work item 都不编译**，而理由是它的。

    这一条是 2026-09-23 那次运行的正面处置。那次 worker 领到 ``kent-003``、花掉
    9 次 Serper 去搜这个内部 id，然后交回一份「什么都没找到」的结算 —— 而盘上
    从来没有任何一处说得清它在找谁。名字那一格写内部 id 是**已经发生过**的形态，
    所以这里造的就是它，不是别的形状。

    判别力在两半：

    * 编译**零**件 —— 不是「跳过那一行、编译其余一行」。跳过会让报告里的「1」
      与「清单里本来就只有 1 行」长得一样，而这两件事的处置完全相反；
    * 补上名字之后，**同一个幂等键**重发就通 —— 阻塞不许把这个键废掉，否则最
      正常的运维路径（补清单、重发）只能靠换键绕过，而报告里会多出一个空跑作业。
    """
    settings.inputs_dir.mkdir(parents=True, exist_ok=True)
    (settings.inputs_dir / "half-named.yaml").write_text(
        "accounts:\n"
        "  - account_id: acct-0001\n"
        "    organisation: Example 0 Ltd\n"
        "    cohort: known_success\n"
        "  - account_id: acct-0002\n"
        "    organisation: acct-0002\n"
        "    cohort: known_success\n",
        encoding="utf-8",
    )

    body = _submit(control, input_ref="half-named", idempotency_key="half-named-v1")

    assert body["state"] == "blocked"
    assert body["work_items"]["count"] == 0
    assert "acct-0002" in body["reason"]
    # 而且要说清**一次都没花**：这个回执是给运维读的，而「阻塞了但也许试过几次」
    # 与「一个 work item 都没建出来」在报告里必须分得开。
    assert "没有被花掉" in body["reason"]

    write_accounts(settings, "half-named", ("acct-0001", "acct-0002"))
    again = _submit(control, input_ref="half-named", idempotency_key="half-named-v1")

    assert again["job_id"] == body["job_id"]
    assert again["work_items"]["count"] == 2
    assert again["work_items"]["blocker"] == ""


def test_blocking_never_rewrites_a_job_that_already_has_work(
    control, settings: Settings, inputs: Path
) -> None:
    """已经编译出工作单元的作业，不因为「今天读不出那个 input_ref」而变成被阻塞。

    这一条的判别力全在后半句：把清单删掉、用同一个键重发，作业仍是 queued、
    工作单元仍是两条，而回执如实报出这一次读输入失败了。少了这一半，一条
    「解析失败就落 BLOCKED」的实现也过 —— 它会把一次读盘失败说成一件没做过的事。
    """
    first = _submit(control)
    assert first["work_items"]["count"] == len(DEFAULT_INPUTS[1])

    inputs.unlink()
    again = _submit(control)

    assert again["job_id"] == first["job_id"]
    assert again["state"] == "queued"
    assert again["work_items"]["count"] == len(DEFAULT_INPUTS[1])
    assert "不存在" in again["work_items"]["blocker"]


def test_a_row_edited_after_the_work_item_exists_is_refused_not_repinned(
    control, settings: Settings, inputs: Path
) -> None:
    """清单还在、名字被改了 ⇒ **拒绝**，而且盘上钉着的仍是旧的那一份。

    这一条与上一条是两个不同的失败：上一条是文件**读不出来**（作业照旧、如实报
    读盘失败），这一条是文件读得出来、而且**内容变了**。两者都必须不重钉，但
    只有后者有「悄悄换成新的身份」这条出路 —— 而那条出路的形态是：worker 按
    旧名字干的活，结算挂在新名字上；两条路径在回执里长得完全一样。

    判别力在两半，缺一条就退化成同义反复：

    * 这次受理**报错**，而且理由里出现 ``organisation`` 与两个值 —— 一句笼统的
      「重新编译被拒绝」读的人不知道该去哪一格改；
    * 盘上那条 ``work_item_snapshot`` 的 ``subject`` 仍是**旧**组织名。少了这一半，
      一个「先写新的、再抛出去」的实现照旧全绿，而它恰恰是最糟的一种：拒绝是
      假的，重钉是真的。

    改的是 ``acct-0001`` 那一行（``Example 0 Ltd`` → ``Renamed 0 Ltd``），
    ``account_id`` 与顺序都不动：这样被拒的只可能是身份，不可能是「换了一批」。
    """
    first = _submit(control)
    assert first["work_items"]["count"] == len(DEFAULT_INPUTS[1])

    inputs.write_text(
        "accounts:\n"
        "  - account_id: acct-0001\n"
        "    organisation: Renamed 0 Ltd\n"
        "    cohort: known_success\n"
        "  - account_id: acct-0002\n"
        "    organisation: Example 1 Ltd\n"
        "    cohort: known_success\n",
        encoding="utf-8",
    )
    outcome = control("submit_research", {
        "scene_id": "uk-acme-natural-grass",
        "input_kind": "known_accounts",
        "input_ref": DEFAULT_INPUTS[0],
        "idempotency_key": "discovery-v1-arm-b",
    })

    assert outcome.is_error
    assert "organisation" in outcome.text
    assert "Example 0 Ltd" in outcome.text and "Renamed 0 Ltd" in outcome.text

    store = SqliteJobStore(settings.database_path)
    items = store.work_items(first["job_id"])
    assert len(items) == len(DEFAULT_INPUTS[1])
    pinned = store.work_item_snapshot(items[0].work_item_id)
    assert pinned is not None and pinned.subject is not None
    assert pinned.subject.organisation == "Example 0 Ltd"


def test_an_unusable_name_is_refused_before_any_job_exists(control) -> None:
    """名字先校验、再受理 —— 反过来的话这个幂等键会连同作业一起废掉。

    判别力在下半段：被拒绝之后，用**同一个键**、一个合法名字重发必须成功。
    如果那次拒绝已经落下过一个把非法名字写死的作业，重发会撞上幂等冲突。
    """
    outcome = control("submit_research", {
        "scene_id": "uk-acme-natural-grass",
        "input_kind": "known_accounts",
        "input_ref": "../devon-2026",
        "idempotency_key": "refused-v1",
    })

    assert outcome.is_error
    assert "不是规范形式" in outcome.text

    body = _submit(control, idempotency_key="refused-v1")
    assert body["state"] == "queued"
    assert body["work_items"]["count"] == len(DEFAULT_INPUTS[1])


@pytest.mark.parametrize(
    "kind", ["known_accounts", "source_receipt", "discovery_scope"]
)
def test_the_name_rule_covers_every_kind(control, kind: str) -> None:
    """名字是**名字**这条约定三种输入共有，校验不能只挂在某一种上。

    名字校验如果写在种类分支**之后**，那些分支的 ``input_ref`` 就**没有任何东西
    在校验**。不拼路径时这不炸；而每一个生产者都会照它去拼 ``inputs_dir`` 下的
    路径 —— 那时炸的是一个**已经受理、``input_ref`` 已经写死**的作业，也就是最贵的
    那个时机。所以校验放在派发**之前**，三种一起过。

    这条判据三种都跑，正是因为它守的不是某一个分支：今天三个分支都在拼路径了，
    凭「哪一个现在需要」来决定校验位置的写法在这里会漏掉第三种。
    """
    refused = control(
        "submit_research",
        {
            "scene_id": "uk-acme-natural-grass",
            "input_kind": kind,
            "input_ref": "../secrets",
            "idempotency_key": f"refused-{kind}",
        },
    )

    assert refused.is_error
    assert "不是规范形式" in refused.text


@pytest.mark.parametrize(
    "kind", ["known_accounts", "source_receipt", "discovery_scope"]
)
def test_a_valid_name_on_every_kind_is_blocked_by_what_is_missing_on_disk(
    control, settings: Settings, kind: str
) -> None:
    """正对照：合法名字**受理**，阻塞来自「盘上没有那样东西」，而不是「没有生产者」。

    这条判据的前身分成两条，因为 ``discovery_scope`` 曾经阻塞在**另一条**理由上
    （「这一类没有生产者」），而那时候「有没有生产者」这件事只在两条判据的**对照**
    里读得出来。三类都有生产者之后，那个对照没有对象了 —— 换成一个更强的形式：
    三类**每一条**理由都必须做三件事。

    * 说出**看的是哪个目录** —— 运维照着它才能修。
    * **不**说「生产者」—— 这一半挡的是回归：生产者落地了而受理那一侧还在说没有，
      两边都自称正常，那种缺口没有任何东西能看见。
    * 说得出**它找的是什么**（一类是清单文件、两类是回执）—— 一句「找不到输入」
      没有告诉运维该放什么。

    两件事仍然必须分开：名字那一关**过了**（否则这条会红在 ``is_error`` 上），
    阻塞来自另一条判据。把它们混在一起读，会得出「这一类输入什么都做不了」这个
    **更弱**的结论，而它不对：它做得了的事是正确地受理、然后如实地说出卡在哪。
    """
    # 名字合法，而盘上**没有**它对应的东西。用 ``DEFAULT_INPUTS[0]`` 之外的
    # 一个名字：那份清单由 ``inputs`` fixture 写下了，拿它当「不存在」会得到
    # 一个 ``queued`` 的作业 —— 判据会在一个与它无关的地方红。
    missing = "devon-2026-missing"
    body = _submit(
        control,
        input_kind=kind,
        input_ref=missing,
        idempotency_key=f"missing-{kind}",
    )

    assert body["state"] == "blocked", body
    assert "生产者" not in body["reason"]
    assert str(settings.inputs_dir) in body["reason"]
    expected = {
        "known_accounts": f"{missing}.yaml",
        "source_receipt": f"{missing}.receipt.json",
        # 这一类的 ``input_ref`` 是**工件名**，所以它找的是「哪一份回执的
        # artifact_ref 是这个值」—— 理由里点名的是那一格，不是某个文件名。
        "discovery_scope": "artifact_ref",
    }[kind]
    assert expected in body["reason"]


# ----------------------------------------------------------------------
# 派发：受理之后这一批工作交出去了几件
# ----------------------------------------------------------------------
def test_without_an_executor_the_receipt_says_nothing_was_handed_out(control) -> None:
    """没有执行器的部署：受理与编译照常，回执里**如实**说一件都没交出去。

    这一格不能写成沉默或一个空的 ``dispatch`` 块。读回执的人接着要判断的是
    「现在到哪了」—— 一个没有这一格的回执会让「工作单元都在、外面没有任务」
    看起来像一次正常的受理。
    """
    body = _submit(control)

    handed = body["dispatch"]
    assert handed["executor_configured"] is False
    assert handed["counts"] == {"already_mapped": 0, "adopted": 0, "created": 0}
    assert handed["handed_out"] == 0
    assert handed["stopped_at"] == ""
    # 理由说的是**这个进程没有把执行器构造出来**，不是一句「执行器不可用」
    # （与能力表 dispatch 行同源：那一行同样分开「没配」与「没装起来」）。
    assert "没有把执行器构造出来" in handed["blocker"]
    # 收敛扫描这一趟**没有跑** —— 而四个计数全 0 与「跑了、一切到位」在数字上
    # 长得一样，所以 ``ran`` 是那一格唯一的判词（同 `executor_configured`）。
    sweep = handed["board_convergence"]
    assert sweep["ran"] is False
    assert sweep["items"] == []
    assert {key: sweep[key] for key in ("considered", "pushed", "pending", "unsettled")} == {
        "considered": 0,
        "pushed": 0,
        "pending": 0,
        "unsettled": 0,
    }
    # 而工作单元仍然是持久的：执行器到位之后 resume 会把它们派出去。
    assert body["work_items"]["count"] == len(DEFAULT_INPUTS[1])
    assert body["state"] == "queued"


def test_with_an_executor_the_receipt_counts_what_really_went_out(
    control, outside: FakeDispatcher, store_factory
) -> None:
    """有执行器：三格与 ``handed_out`` 报的是**真的碰过外部**的那几件。

    判别力在 ``outside`` 与存储上：一个每次都重试的实现会让上面那几格照旧全绿，
    而外部真的多了任务。所以这里同时断回执、**外部被碰了几次**，以及本地那几条
    映射真的写下去了（回执里的数字与存储里的事实是同一条）。
    """
    body = _submit(control, with_dispatcher=True)

    handed = body["dispatch"]
    assert handed["executor_configured"] is True
    assert handed["counts"] == {
        "already_mapped": 0,
        "adopted": 0,
        "created": len(DEFAULT_INPUTS[1]),
    }
    assert handed["handed_out"] == len(DEFAULT_INPUTS[1])
    assert handed["blocker"] == ""
    assert outside.calls == ["find", "create"] * len(DEFAULT_INPUTS[1]), (
        "受理之后的收敛扫描不该在外部多留一次调用 —— 这一批一件都没结算"
    )
    assert len(outside.tasks) == len(DEFAULT_INPUTS[1])
    store = store_factory()
    items = store.work_items(body["job_id"])
    refs = [store.execution_ref(item.work_item_id) for item in items]
    assert all(ref is not None for ref in refs), "交出去的每一件都要有本地映射"
    # 这一批刚交出去、一件都还没结算 —— 收敛扫描因此读了零件，而「没读」与
    # 「读了、都到位」在 ``pushed``/``pending`` 上不可区分，所以 ``unsettled``
    # 那一格必须与 ``considered`` 一起读。
    sweep = handed["board_convergence"]
    assert (sweep["ran"], sweep["considered"], sweep["unsettled"]) == (True, 0, len(items))
    assert sweep["unmapped"] == 0


# ----------------------------------------------------------------------
# 派发 —— 「这一批是被计划里的哪一步交出去的」（§B16 关闭条件 7）
# ----------------------------------------------------------------------
#: 这一份 Scene 的声明**真的开出来**的那一步：注册过（``domain/sources.py``），
#: 且 ``search.query_templates`` 给了它入口。
HANDED_STEP = "search.engine.v1"

#: 注册过、**没有任何声明给它入口**的那一步 —— 这一对判据的可判别性在它身上。
UNPLANNED_STEP = "research.skill.v1"


def test_the_dispatch_receipt_says_which_plan_step_it_was_issued_for(
    control, outside: FakeDispatcher
) -> None:
    """正控：给了这一步，派发照常发生，而且回执里那一格**逐字**回显。

    条件 7 的五个被选者里 执行器 是最后一段：这一趟真的去外部建了任务，那件事要
    说得出服务的是哪一步。只断言「回执里有这一格」会让一个恒填常量的实现全绿，
    所以这里同时断 ``outside.calls`` —— 合法的步骤不该改变派发做不做。

    没给这一格是另一条路：``None``（没有声称），不是 ``""``、更不是一个默认步骤。
    """
    body = _submit(control, with_dispatcher=True, source_method=HANDED_STEP)

    handed = body["dispatch"]
    assert handed["source_method"] == HANDED_STEP
    assert handed["handed_out"] == len(DEFAULT_INPUTS[1]), (
        "合法的步骤不该改变这一次交出去了几件"
    )
    assert outside.calls == ["find", "create"] * len(DEFAULT_INPUTS[1])

    plain = _submit(
        control,
        with_dispatcher=True,
        idempotency_key="discovery-v1-arm-c",
        source_method="",
    )
    assert plain["dispatch"]["source_method"] is None


def test_a_step_this_plan_never_opened_is_refused_before_the_dispatcher_is_touched(
    control, outside: FakeDispatcher
) -> None:
    """反例：一个**注册过、但这一份声明没给它入口**的步骤 ⇒ 拒绝，且**外部零调用**。

    与三条研究工具上那一条同形，只是零 I/O 的观察面换成了 ``outside.calls``：
    执行器的任务是外部副作用，一次注定被拒的派发不该先在对面留下一条。断言「工具
    报了个错」会让一个先建任务后校验的实现照样绿。

    最后那一次重发钉的是**拒绝发生在门口**：同一个幂等键、换一个 ``input_ref``
    重发 —— 如果刚才那次被拒的调用已经受理并落盘，这一次会撞上幂等冲突（同一个
    键、不同的请求），而门口那种实现什么都不拦。换一个键重发不算证据：两种实现
    都会过。
    """
    outcome = control(
        "submit_research",
        {
            "scene_id": "uk-acme-natural-grass",
            "input_kind": "known_accounts",
            "input_ref": DEFAULT_INPUTS[0],
            "idempotency_key": "discovery-v1-arm-b",
            "source_method": UNPLANNED_STEP,
        },
        with_dispatcher=True,
    )

    assert outcome.is_error, "计划里没有这一步，派发却还是发生了"
    assert "计划" in outcome.text, outcome.text
    assert outside.calls == [], "被拒的派发碰了外部 —— 那一边是要真的建任务的"

    retry = control(
        "submit_research",
        {
            "scene_id": "uk-acme-natural-grass",
            "input_kind": "known_accounts",
            "input_ref": "devon-2027",
            "idempotency_key": "discovery-v1-arm-b",
            "source_method": HANDED_STEP,
        },
        with_dispatcher=True,
    )
    assert not retry.is_error, (
        "被拒的那次调用在盘上留下了东西 —— 同一个键换一个请求重发撞上了幂等冲突："
        f"{retry.text}"
    )


def test_resubmitting_the_same_key_hands_out_nothing_twice(
    control, outside: FakeDispatcher
) -> None:
    """幂等键重发：同一个作业、外面一件都不多。

    §8B 逐字禁止 duplicate expensive work，而重发正是断线重连之后最正常的动作。
    判据落在 ``outside.calls`` 上：一个「先建一遍再看」的实现在这两次回执里长得
    一模一样。
    """
    first = _submit(control, with_dispatcher=True)
    after_first = list(outside.calls)

    again = _submit(control, with_dispatcher=True)

    assert again["job_id"] == first["job_id"]
    assert again["dispatch"]["counts"] == {
        "already_mapped": len(DEFAULT_INPUTS[1]),
        "adopted": 0,
        "created": 0,
    }
    assert again["dispatch"]["handed_out"] == 0
    assert outside.calls == after_first, "第二轮一次外部都不该碰"
    assert len(outside.tasks) == len(DEFAULT_INPUTS[1])


def test_a_blocked_job_hands_out_nothing(control, outside: FakeDispatcher) -> None:
    """被阻塞的作业不放行派发 —— 而且这一格在**碰外部之前**就返回了。

    断 ``outside.calls == []`` 而不是只断计数：一个先查一遍再拒绝的实现，返回值
    一模一样，却在外部留下一次真实调用（以及与别人任务的匹配）。
    """
    body = _submit(
        control,
        input_kind="discovery_scope",
        idempotency_key="blocked-dispatch-v1",
        with_dispatcher=True,
    )

    assert body["state"] == "blocked"
    assert body["dispatch"]["counts"] == {"already_mapped": 0, "adopted": 0, "created": 0}
    assert body["dispatch"]["handed_out"] == 0
    assert "不放行派发" in body["dispatch"]["blocker"]
    assert outside.calls == []


# ----------------------------------------------------------------------
# 没有落点时不提供入口
# ----------------------------------------------------------------------
def test_an_instance_without_a_job_store_does_not_offer_the_tools(
    settings: Settings,
) -> None:
    """「没有的入口」优于「有的入口 + 一条注意事项」。

    不传 ``jobs`` 时这两条工具不在这张表里，于是注册上去的是 stub，调用方
    拿到的是「这个服务实例尚未接线（Phase G）」—— 而不是一个受理了、重启后
    消失的作业。
    """
    token, digest = mint("claude-control", [Role.CONTROLLER])
    ring = TokenRing.from_file(_ring_file(settings, digest))
    outcome = drive_with_settings(
        settings=settings,
        tokens=ring,
        token=token,
        calls=(("submit_research", {"scene_id": "uk-acme-natural-grass",
                                    "input_kind": "discovery_scope",
                                    "input_ref": "devon-2026-09",
                                    "idempotency_key": "k1"}),),
        want_tool_list=False,
    )

    assert not outcome.handshake_error, outcome.handshake_error
    result = outcome.call("submit_research")
    assert result.is_error
    assert "尚未接线" in result.text

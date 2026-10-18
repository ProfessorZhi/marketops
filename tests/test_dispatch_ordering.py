"""Phase G5b：``application/dispatch.py`` 的**顺序**与它的三条出口。

## 这个文件测的不是「能不能建成」，是「什么时候不该建」

派发的正常路径很短（读 → 建 → 记），短到几乎可以不用测。会出事的是它**周围的**
那几条路，而它们每一条都不报错：

| 这条判据 | 它挡的失败 |
|---|---|
| 第一次派发真的建了、也真的记了 | 通路本身 |
| 同一个单元派发两次，外部**一次都没被碰** | 「每次重查」把一次外部删除变成第二次 create |
| 外面已经有了就**认领**，不再建 | create 之后崩溃留下的孤儿永远找不回来 |
| 同一组 label 上有两条 → **拒绝**，不挑一条 | §8B 的 duplicate expensive work 被静默收敛 |
| 作业不放行派发时，外部调用计数为 **0** | 留下一个外面有、本地没有的孤儿 |
| 两个 label 缺一不可 | 少一个 label 就把别人的活查进自己名下 |
| 已停止的 work item 一定已映射 | 那条**不存在**的分支为什么不存在 |

## 替身是一个**记账的** Dispatcher

替身是 ``tests/fakes.FakeDispatcher``（模块 docstring 说明它为什么**记账**、
以及为什么只有一份）。
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from pathlib import Path

import pytest

from marketops.adapters.memory import InMemoryJobStore
from marketops.application.dispatch import (
    INITIAL_EXTERNAL_STATUS,
    DispatchOutcome,
    WorkDispatch,
    external_task_id,
    link_labels,
    work_item_id_from_labels,
)
from marketops.domain.enums import JobInputKind, JobState, StopReason
from marketops.domain.errors import ExternalStateError, JobStateError
from marketops.domain.findings import Findings
from marketops.domain.util import sha256_text
from marketops.interfaces.research_worker import RESEARCH_WORKER_PROMPT
from marketops.ports.jobs import JobStore
from tests.fakes import FakeDispatcher, synthetic_dossiers, synthetic_scene_files

SCENE = "uk-acme-natural-grass"
KEY = "discovery-v1-arm-b"

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 编译时冻住的规则。这里也是**合成的**，理由同 ``tests/test_job_contract.py``：
#: 本文件测的是派发的顺序与出口，快照只是 ``compile_work_items`` 的一个必填输入，
#: 不是被测对象 —— 但它必须是真的一批字节，因为版本号是那批字节算出来的。
SCENE_FILES = synthetic_scene_files()

#: 反向读取那几条用的两个 id，取**实测值**（``marketops-runtime`` 那个库里第一行与
#: 第三行）：作业与工作单元都是 32 位十六进制，两个值不同是刻意的 —— 完全对称的
#: 假值会让「读错了哪一半」在断言上看起来一样。
#:
#: **一处陷阱写在这里**：``kent-003`` 是同一个工作单元的 ``subject_ref``，**不是**
#: ``work_item_id`` —— 库里那两列是分开的，而 label 里写的是后者（实测那一行：
#: ``work_item_id = 6d783321…``、``subject_ref = kent-003``）。拿 subject 去问
#: ``get_assignment`` 会查一件不存在的记录，而它看着完全像一个 id。
_JOB_ID = "1b1b7f8209c19491e1a26c34ad2207a8"
_ITEM_ID = "6d783321a9df9b2f2c6bf4ace0cd9b26"


# ----------------------------------------------------------------------
# 替身
# ----------------------------------------------------------------------
# ``FakeDispatcher`` 住在 ``tests/fakes.py``：服务面那两条路径也要拿它回答
# 「外部被碰了几次」，两个文件各写一份会在「什么叫碰过」上漂移。


class _JoblessStore:
    """一个**这个端口产生不出来**的存储：work item 在，它指向的作业不在。

    它只为一个目的存在：把 ``dispatch`` 里那条防御分支走一遍。``JobStore`` 没有
    删除作业的动词，两个实现也都不会出现这种状态 —— 所以这条判据证明的是那条
    分支**会怎么表现**，不是在说这种状态会发生。这个区别要留在代码里，不然它读起来
    像一条真的防线。
    """

    def __init__(self, inner: JobStore) -> None:
        self._inner = inner

    def get(self, job_id: str):  # noqa: ANN201 - 明确地恒为 None
        return None

    def __getattr__(self, name: str):  # noqa: ANN401
        return getattr(self._inner, name)


# ----------------------------------------------------------------------
# 脚手架
# ----------------------------------------------------------------------
def _compiled(
    store: JobStore, *, subjects: Sequence[str] = ("devon-01",), key: str = KEY
) -> tuple[str, str]:
    """受理 + 编译，返回 ``(job_id, 第一个 work_item_id)``。**不**派发。

    ``key`` 可换：id 是内容寻址的，同一个 ``(scene, key)`` 在两个 store 上会算出
    同一个 ``job_id`` —— 要造一个**真的不同**的作业，只能换键。
    """
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026-09",
        idempotency_key=key,
        request_sha256=sha256_text(f"devon-2026-09:{key}"),
    )
    items = store.compile_work_items(
        job.job_id, synthetic_dossiers(*subjects), scene=SCENE_FILES
    )
    return job.job_id, items[0].work_item_id


@pytest.fixture
def store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def outside() -> FakeDispatcher:
    return FakeDispatcher()


@pytest.fixture
def dispatch(store: InMemoryJobStore, outside: FakeDispatcher) -> WorkDispatch:
    return WorkDispatch(jobs=store, dispatcher=outside)


# ----------------------------------------------------------------------
# 正常路径
# ----------------------------------------------------------------------
def test_a_first_dispatch_creates_and_writes_the_mapping(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    job_id, work_item_id = _compiled(store)

    receipt = dispatch.dispatch(work_item_id)

    assert receipt.outcome is DispatchOutcome.CREATED
    assert receipt.job_id == job_id
    assert receipt.external is not None, "建过就一定读过外面，external 不该是 None"
    assert receipt.external.external_id in outside.tasks
    assert outside.create_statuses == [INITIAL_EXTERNAL_STATUS]
    # 外部那张表里只有标题是给人读的；一个只写 id 的标题等于让人去查一次本仓。
    assert outside.created_units[0].title == f"{SCENE}: devon-01"
    assert outside.created_units[0].job_id == job_id
    # 本地那一行真的落下了 —— 回执里的 ``ref`` 与存储里的是同一条。
    assert store.execution_ref(work_item_id) == receipt.ref
    assert receipt.ref.quay_task_id == receipt.external.external_id
    assert outside.calls == ["find", "create"], "顺序是先问后建，不是先建后问"


def test_the_caller_never_has_to_invent_the_labels(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """``find`` 与 ``create`` 拿到的是 ``link_labels`` 那**同一组**，两个都在。

    两个都断：只断「find 的 label 在 create 的 label 里」，会让「两边各算一遍但
    恰好算得一样」也绿；只断「有两个 label」，会让「两个都写成同一个前缀」也绿。
    """
    job_id, work_item_id = _compiled(store)

    dispatch.dispatch(work_item_id)

    expected = link_labels(job_id, work_item_id)
    assert len(expected) == 2, "一个 label 认不出一件事（见 link_labels 的 docstring）"
    assert outside.find_labels == [expected]
    assert outside.create_labels == [expected]


def test_a_query_narrowed_to_one_label_would_take_somebody_elses_work(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """**正对照**：少给一个 label 会认领到别人的活，而这条判据要能看见它。

    摆两行在外面，各自命中「这一组 label 里的一半」：

    * 同一个作业的**另一个** work item（命中 job label）；
    * **另一个**作业里 ordinal 相同的 work item（命中 item label）。

    完整的一组 label 一个都不命中，所以正确的实现是 ``CREATED``。任何只按其中一个
    label 查的版本会 ``ADOPTED`` 到错误的那一行 —— 而它不会报错，只会让两条线
    绑到同一件外部任务上。
    """
    job_id, work_item_id = _compiled(store, subjects=("devon-01", "devon-02"))
    job_label, item_label = link_labels(job_id, work_item_id)
    other_job_id, other_item_id = _compiled(InMemoryJobStore(), key="somebody-elses-run")
    assert other_job_id != job_id and other_item_id != work_item_id

    outside.seed("EXT-sibling-unit", [job_label])
    outside.seed("EXT-other-job", [link_labels(other_job_id, other_item_id)[1]])

    receipt = dispatch.dispatch(work_item_id)

    assert receipt.outcome is DispatchOutcome.CREATED
    assert receipt.external is not None
    assert receipt.external.external_id not in {"EXT-sibling-unit", "EXT-other-job"}
    assert set(receipt.external.labels) == {job_label, item_label}
    assert outside.find_labels == [(job_label, item_label)]


# ----------------------------------------------------------------------
# 外部那一条上写的**正文**
# ----------------------------------------------------------------------
#: worker 被 prompt 要求去调的工具里，**除了那个指针**之外的那几个。正文里出现
#: 任何一个，就是把指引抄了第二遍 —— 而第二遍与 ``get_assignment`` 会各自漂移，
#: 漂移的那一份（正文）没人调用、却有人读（下面那条正对照钉着这份名单本身）。
_NON_POINTER_WORKER_TOOLS = (
    "search_web",
    "fetch_document",
    "crawl_site",
    "get_evidence",
    "submit_findings",
    "decide",
)

#: 正文里**允许**出现的全部小节名。允许清单**只有一条**，而它不是格式偏好 ——
#: 这一节是派发器判断「两件工作能不能同时跑」的唯一输入。实测（2026-09-23，
#: Quay 0.11.0 的 ``concurrent-batch-scheduler``，喂的是本仓真的写下去的那两条
#: 任务正文）：
#:
#:   * 没有这一节 → ``BATCH (0-wide, concurrent): (none)``，两件都被
#:     ``deferred: … ill-declared touches: conservative: side A declares no/empty
#:     ## Touches → serialize`` 挡下 —— cap=2 的验收因此一件都并行不起来；
#:   * 补上自身声明 → 同一个执行器报 ``BATCH (2-wide, concurrent)``。
#:
#: 名单**不增长**的理由与原来那条「正文里没有小节」的禁令是同一个：多出来的每一节
#: 都是没人要求的一节，而几个 ``##`` 一凑，这一行在外部那套机械闸门里就长得像一件
#: 软件开发任务（``## Finding`` / ``## AC`` / ``## Plan``），会被别人的看板流程当成
#: 工单接走。所以判据从「一个小节都不许有」收窄成「**只许有这一节**」——那是同一条
#: 理由下更准的一条判据，而不是把它放宽。
_ALLOWED_BODY_SECTIONS = ("Touches",)


def _touches_entries(body: str) -> list[str]:
    """正文那一节里的条目，已去掉列表符号与首尾空白。

    写法刻意**照着派发器的读法来**（它只认一个标题行、到下一个小节为止、按行取
    条目）：本文件要判的是「派发器会读到什么」，而不是「这句话排版好不好看」。
    所以这里不引一个通用的 Markdown 解析器 —— 那会解析出派发器不读的东西，于是
    一条派发器读不到的声明也能让这条判据变绿。
    """
    lines = body.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("## Touches")), None)
    if start is None:
        return []
    entries: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        stripped = line.strip()
        if stripped.startswith("- "):
            entries.append(stripped[2:].strip())
    return entries


def test_the_body_says_what_this_is_and_names_no_tool_but_the_pointer(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """§2.14 H3a：指引走 ``get_assignment``，**不**走外部条目的正文。

    正文的读者是**外部那张表前的人**（以及别的 agent），而这个人手里没有本仓。
    所以它必须自足到能回答「这是哪一件事」，又必须不自足到「不必再问市场侧
    就能开工」：

    * 三个身份都在（作业 / 工作单元 / 主体）—— 缺一个，读的人就得回来查一次；
    * 工具名**恰好**是那个指针 ``get_assignment`` —— 把 ``search_web`` /
      ``submit_findings`` 这些写进正文，等于让这一行成为一份**会过期的**指引；
    * 正文的小节**恰好只有**那一节派发器要读的（见 ``_ALLOWED_BODY_SECTIONS``）——
      少掉它，两件工作就并行不起来；多出任何一节，就让这一行在外部那套机械闸门里
      长得像一件软件开发任务（``## Finding`` / ``## AC`` / ``## Plan``），于是被
      别人的看板流程当成一件工单接走。它不是工单：它由市场侧创建、由市场侧收口。
    """
    job_id, work_item_id = _compiled(store)

    dispatch.dispatch(work_item_id)

    body = outside.created_units[0].body
    assert job_id in body and work_item_id in body
    assert SCENE in body and "devon-01" in body
    assert "get_assignment" in body
    named = [name for name in _NON_POINTER_WORKER_TOOLS if name in body]
    assert named == [], f"正文里出现了这些工具名，指引被抄了第二遍：{named!r}"
    sections = [line[2:].strip() for line in body.splitlines() if line.startswith("## ")]
    assert sections == list(_ALLOWED_BODY_SECTIONS), (
        f"正文的小节是 {sections!r}，允许的只有 {list(_ALLOWED_BODY_SECTIONS)!r}。"
        "少一节 ⇒ 派发器无法判断这件工作能不能与另一件同时跑（cap=2 会退化成"
        "一次一件）；多一节 ⇒ 这一行开始像一件软件开发任务。"
    )


def test_the_declared_touch_is_this_task_s_own_file(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """那一节声明的**恰好是这一件自己的文件**，而且是一条字面路径。

    三件事一起判，因为少判任何一件都会静默退化成一条**看着合法**的声明：

    * **恰好一条**条目 —— 多一条就是替别的文件声明了写面；
    * 它是 ``tasks/<外部任务id>.md`` —— 外部 id 由 ``(job_id, work_item_id)``
      确定性派生，所以两件不同的工作必然算出两个不同的路径，互斥是**算出来的**，
      不是约定出来的；
    * 它**不含通配符** —— 通配符要展开，而展开出来的集合是**当下盘上的文件**，
      ``tasks/*`` 这种声明在两件之间必然相交：它看着非空、看着像声明，判据却退回
      串行。宁可在这里红，也不要在派发轮次里退化成一次一件。

    这一节的存在理由、以及它为什么只许有一条，写在 ``_ALLOWED_BODY_SECTIONS``。
    """
    job_id, work_item_id = _compiled(store)

    dispatch.dispatch(work_item_id)

    body = outside.created_units[0].body
    section = _touches_entries(body)
    assert section == [f"tasks/{external_task_id(job_id, work_item_id)}.md"], (
        f"那一节的条目是 {section!r} —— 它必须是这一件自己的任务文件，"
        "而且是字面路径（见本条的 docstring）。"
    )
    assert not [entry for entry in section if "*" in entry or "?" in entry]


#: 这一对 id 取**实测形状**（32 位十六进制、两位不同），与 ``tests/test_quay_cli_adapter.py``
#: 里那对同源。它们只是外部 id 的输入，不是任何作业的身份。
_JOB = "0123456789abcdef"
_UNIT = "fedcba9876543210"


def test_the_external_id_is_deterministic_and_readable() -> None:
    """两个并发的派发者必须算出同一个字符串 —— 这是 ``create`` 幂等的**全部**机制。

    一个带时间戳或随机的 id 会把「同一件事被交出去两次」重新放回来，而那时两边
    都会正常跑完、都会付钱。

    「可读」那半也一起判：两个 id 各取一段，所以一个人拿着 ``MOPS-01234567-…``
    能认出它是哪个作业下的哪个单元。再散一次哈希也能确定性，但那条判据要的是
    「确定性 **且** 认得出来」—— 少判后半句，一个 ``sha256`` 就能让这条变绿。
    """
    first = external_task_id(_JOB, _UNIT)
    assert first == external_task_id(_JOB, _UNIT)
    assert first == "MOPS-01234567-fedcba98"
    assert external_task_id(_JOB, "0000000000000001") != first
    assert external_task_id("ffffffffffffffff", _UNIT) != first


def test_the_external_id_refuses_a_non_canonical_input() -> None:
    """两个输入都先过 ``check_canonical_id`` —— 返回值会被当作外部的任务 id 用（M9）。

    调用方**能**构造 ``ExecutionUnit``，所以这里不能假设「传进来的一定是 hash」。
    一个含 ``/`` 的 id 在对面是一个路径穿越告警（``ADV-004`` 会拒它），而在本地
    这条链上它**已经**错了 —— 所以在发出去之前抛，不是让对面替我们抛。
    """
    with pytest.raises(ValueError):
        external_task_id("JOB/UP", _UNIT)
    with pytest.raises(ValueError):
        external_task_id(_JOB, "UNIT/UP")


def _defining_modules(function_name: str) -> set[str]:
    """``marketops/`` 下**定义**了某个顶层函数的模块（相对路径，posix）。

    用 ``ast`` 而不是 ``grep``：注释与 docstring 里出现这个名字是**正常**的
    （``contracts/`` 与示例报文里就有 ``MOPS-…`` 这种串），把它们算成「又一份实现」
    会让判据变成噪声；反过来，一条注释也永远不该让判据变绿。
    """
    found: set[str] = set()
    for path in sorted((REPO_ROOT / "marketops").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    found.add(path.relative_to(REPO_ROOT).as_posix())
    return found


def test_the_naming_rule_has_exactly_one_definition() -> None:
    """前缀 + 取几位 + 校验，全仓**只有一份**，而且它住在应用层。

    这不是整洁问题。任务正文那一节声明的是 ``tasks/<外部 id>.md``，派发器按那条
    声明判断两件工作能不能同时跑 —— 所以**写正文的那一层必须能算出那个 id**。
    应用层 import 不到 ``adapters/``（那是这条分层的方向），于是这条规则只能住在
    「两层都读得到」的地方，由两边调同一个函数。

    两份实现的那天不会立刻出红：正文里写 ``tasks/A.md``，外面建出来的是 ``B``，
    而两边各自都正常 —— 只是那条声明从此是一句假话，静默地把并发退回一次一件。
    所以这里判的是**定义的数量**，不是某一次调用的结果。

    正对照先跑：扫描器必须能在同一个包里找到另一个**确实存在**的函数，否则
    「只有一个定义」在扫描器读不到文件时也会绿。
    """
    assert _defining_modules("board_target") == {"marketops/application/dispatch.py"}, (
        "扫描器读不到文件 —— 下面那条判据的绿灯没有意义。"
    )
    assert _defining_modules("external_task_id") == {"marketops/application/dispatch.py"}, (
        "外部 id 的命名规则的定义位置变了。它必须住在一个**应用层读得到**的模块里："
        "写任务正文的是应用层，而它 import 不到 adapters/。"
    )



def test_the_banned_names_are_the_ones_the_worker_is_told_to_call() -> None:
    """正对照：上一条禁掉的那六个名字，确实是 prompt 让 worker 去调的六个。

    没有这一条，「正文里没有 ``search_web``」在某个工具改名之后会变成一句**对任何
    正文都成立**的空话 —— 而它仍然显示为绿。名单的来源是 prompt 自己，所以
    prompt 改了名字，这条会先红（红在「名单过期」上，不是红在派发上）。
    """
    for name in _NON_POINTER_WORKER_TOOLS:
        assert name in RESEARCH_WORKER_PROMPT, f"{name} 已不在 prompt 里，名单过期了"


# ----------------------------------------------------------------------
# 不碰外部的那两条路
# ----------------------------------------------------------------------
def test_dispatching_the_same_unit_twice_does_not_touch_the_outside(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """第二次是 ``ALREADY_MAPPED``，而且 ``calls`` 一次都没长。

    这条判据存在的理由：**每次都按 label 重查**看起来更稳，实际更贵 —— 外部那个
    任务被谁删掉之后，重查会得到零条，于是静默地再建一个。本地那张映射是
    「派发过没有」的权威。断了 ``calls``，一个「重查但碰巧没建」的实现照样全绿。
    """
    _, work_item_id = _compiled(store)
    first = dispatch.dispatch(work_item_id)
    before = list(outside.calls)

    second = dispatch.dispatch(work_item_id)

    assert second.outcome is DispatchOutcome.ALREADY_MAPPED
    assert second.ref == first.ref, "回执要给的是同一条映射，不是一条新的"
    assert outside.calls == before, "已经映射过就一次外部都不该碰（连 find 都不该）"
    assert second.external is None, "没读过外面，external 必须是 None 而不是一个空壳"
    assert len(outside.tasks) == 1


def test_a_paused_job_never_touches_the_outside(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """§6「Pause normally prevents new dispatch」在**碰外部之前**生效。

    断了 ``outside.calls == []`` 才说明这件事：一个先 ``create`` 再让
    ``map_execution`` 去拒的实现，返回的错误**一模一样**，区别是外面多了一个
    永远不会被领走的孤儿任务。这条判据是「不要留下孤儿」的执行器。
    """
    job_id, work_item_id = _compiled(store)
    store.set_state(job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="控制端暂停")

    with pytest.raises(JobStateError) as caught:
        dispatch.dispatch(work_item_id)

    assert "不放行派发" in str(caught.value)
    assert "paused" in str(caught.value), "消息要点名是哪个状态，否则处置只能靠猜"
    assert outside.calls == [], "拒在碰外部之前 —— 否则留下来的孤儿是谁都看不见的"
    assert store.execution_ref(work_item_id) is None


def test_an_unknown_work_item_is_refused(dispatch: WorkDispatch, outside: FakeDispatcher) -> None:
    with pytest.raises(JobStateError) as caught:
        dispatch.dispatch("0" * 32)

    assert "没有这个 work item" in str(caught.value)
    assert outside.calls == []


def test_a_work_item_whose_job_is_missing_is_refused(
    store: InMemoryJobStore, outside: FakeDispatcher
) -> None:
    """防御分支：work item 在、它指向的作业不在。

    ``_JoblessStore`` 建模的是一个**这个端口产生不出来**的状态（见它的
    docstring）。这条判据钉的是那条分支的**表现** —— 一句话说得清的
    ``JobStateError``，而不是一个 ``NoneType`` 上的 ``AttributeError``。
    """
    _, work_item_id = _compiled(store)
    dispatch = WorkDispatch(jobs=_JoblessStore(store), dispatcher=outside)

    with pytest.raises(JobStateError) as caught:
        dispatch.dispatch(work_item_id)

    assert "不在" in str(caught.value)
    assert outside.calls == []


# ----------------------------------------------------------------------
# 认领：create 之后崩溃的那条路
# ----------------------------------------------------------------------
def test_a_task_that_is_already_out_there_is_adopted_not_created_again(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """外面已经有了（上一次派发死在 create 与 map_execution 之间）→ 认领。

    这是那条崩溃**唯一**能被找回的路径，所以它是一条正常出口，不是容错。
    断了 ``"create" not in calls``：一个「先建一遍再看」的实现在这里会留下第二个
    任务，而它返回的回执与认领长得一样。
    """
    job_id, work_item_id = _compiled(store)
    labels = link_labels(job_id, work_item_id)
    # 外面那一份带着一个**多出来的** label —— 别人也在跟这件事。回执要给的是
    # 读回来的那一份，不是我们送出去的那一组。
    outside.seed("EXT-already-there", (*labels, "somebody-elses-tag"), status="ready")

    receipt = dispatch.dispatch(work_item_id)

    assert receipt.outcome is DispatchOutcome.ADOPTED
    assert "create" not in outside.calls
    assert len(outside.tasks) == 1, "认领不该在外部留下第二行"
    assert receipt.ref.quay_task_id == "EXT-already-there"
    assert store.execution_ref(work_item_id) == receipt.ref
    assert receipt.external is not None
    assert "somebody-elses-tag" in receipt.external.labels, (
        "回执要保留外部报回来的 label 全量 —— 多出来的那一个就是「别人也在跟」的证据"
    )


def test_two_tasks_on_the_same_labels_are_refused_not_picked(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """查到两条 → 拒绝。**不挑一条**，也**不再建第三条**。

    挑一条是把「同一件事被派发了两遍」的证据扔掉，而那正是 §8B 逐字禁止的
    duplicate expensive work —— 两边都会正常跑完，账单付两遍，报告上只显示
    「工作多了一倍」。
    """
    job_id, work_item_id = _compiled(store)
    labels = link_labels(job_id, work_item_id)
    outside.seed("EXT-first", labels)
    outside.seed("EXT-second", labels)

    with pytest.raises(ExternalStateError) as caught:
        dispatch.dispatch(work_item_id)

    message = str(caught.value)
    assert "EXT-first" in message and "EXT-second" in message, (
        "两个 id 都要在消息里 —— 否则「先看清哪一条是真的」这件事无从下手"
    )
    assert "2" in message, "总数要说出来"
    assert outside.calls == ["find"], "拒绝之后不该再建第三条"
    assert store.execution_ref(work_item_id) is None, "拒绝就是什么都没写下去"


def test_a_refusal_does_not_depend_on_how_many_ids_fit_in_the_message(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """一次静默失效的查询会拿回**整张表** —— 消息被截短，判定不受影响。

    §2.9 实测过列表查询的过滤旗标有一种退 0 的静默失效法。那时 ``find`` 返回的
    不是两条而是几十条，而错误消息里列的 id 有上限。这条判据断的是：**判据本身**
    不看条数以外的任何东西，所以消息截短之后「拒绝」照样成立，且总数照旧说对。
    """
    job_id, work_item_id = _compiled(store)
    for index in range(30):
        outside.seed(f"EXT-{index:02d}", link_labels(job_id, work_item_id))

    with pytest.raises(ExternalStateError) as caught:
        dispatch.dispatch(work_item_id)

    message = str(caught.value)
    assert "30" in message
    assert "另有" in message, "截短了就要说出来，否则这句话读起来像「一共就这几条」"
    assert store.execution_ref(work_item_id) is None


# ----------------------------------------------------------------------
# 作业级入口：``dispatch_job`` 遍历一个作业的工作单元
# ----------------------------------------------------------------------
def test_a_round_hands_out_every_item_and_says_how_many(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """一趟把该派的都派了，而且回执里的数字**对得上**。

    三格的和等于这一轮处理过的件数，``handed_out`` 只数真的碰过外部的那两格。
    这两个数字混用，会让一次重发看起来像一次派发 —— 而重发是正常动作
    （受理、resume、断线重连都会走到这里）。
    """
    job_id, _ = _compiled(store, subjects=("devon-01", "devon-02", "devon-03"))

    round_ = dispatch.dispatch_job(job_id)

    assert round_.job_id == job_id
    assert len(round_.receipts) == 3
    assert round_.stopped_at == "" and round_.blocker == ""
    assert round_.by_outcome == {
        DispatchOutcome.CREATED: 3,
        DispatchOutcome.ADOPTED: 0,
        DispatchOutcome.ALREADY_MAPPED: 0,
    }
    assert round_.handed_out == 3
    # 按 ``ordinal`` 逐件交出去 —— 顺序就是清单里的顺序（``compile_work_items`` 里
    # 顺序决定 ordinal），所以这里断的是「走了整份清单」，不是「碰巧碰了三件」。
    assert [unit.unit_id for unit in outside.created_units] == [
        item.work_item_id for item in store.work_items(job_id)
    ]


def test_a_second_round_is_a_no_op_on_the_outside(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """重发同一个作业：``find`` 会再读一次，``create`` **一次都不该再有**。

    §8B 逐字禁止 duplicate expensive work。判据必须落在 ``outside.calls`` 上：
    一个「先建一遍再看」的实现在返回值上与这个完全一样，而它每一次重发都在外部
    留下一个真的会跑完的任务。
    """
    job_id, _ = _compiled(store, subjects=("devon-01", "devon-02"))
    dispatch.dispatch_job(job_id)
    before = list(outside.calls)

    again = dispatch.dispatch_job(job_id)

    assert again.by_outcome == {
        DispatchOutcome.CREATED: 0,
        DispatchOutcome.ADOPTED: 0,
        DispatchOutcome.ALREADY_MAPPED: 2,
    }
    assert again.handed_out == 0, "already_mapped 连读都没读，不该算进交出去的件数"
    # **连 ``find`` 都没有**：本地映射就是「派发过没有」的权威，所以已经映射的
    # 那一件在碰外部之前就返回了。一次多出来的 ``find`` 不算无害 —— 它是一次真的
    # 外部调用，而 §8B 逐字要求「重连之后不要重复昂贵的工作」。
    assert outside.calls == before, "第二轮一次外部都不该碰"
    assert len(outside.tasks) == 2, "第二轮不该在外部留下第三行"


def test_a_round_stops_at_the_first_failure_and_keeps_what_it_handed_out(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """途中失败：停在那一件上，**前面已经交出去的留着**，后面的一件都不碰。

    两个方向都要断。继续往下派是真的把活交出去（代价是钱与重复工作）；把前面
    那几件「撤回」则做不到 —— 端口上没有 ``delete``（同 ``dispatch`` 的 TOCTOU
    那一段）。所以这条路径的正确答案是「停下 + 说清停在哪」，而不是一个看起来
    干净的原子性。
    """
    job_id, first_item = _compiled(store, subjects=("devon-01", "devon-02", "devon-03"))
    items = store.work_items(job_id)
    # 第二件在外部有**两条**同 label 的任务 —— §8B 的 duplicate expensive work，
    # 单件派发在那一格上拒绝（``test_two_tasks_on_the_same_labels_are_refused_not_picked``）。
    for external_id in ("EXT-dup-a", "EXT-dup-b"):
        outside.seed(external_id, link_labels(job_id, items[1].work_item_id))

    round_ = dispatch.dispatch_job(job_id)

    assert round_.stopped_at == items[1].work_item_id
    assert "EXT-dup-a" in round_.blocker and "EXT-dup-b" in round_.blocker
    assert [receipt.work_item_id for receipt in round_.receipts] == [first_item]
    assert round_.by_outcome[DispatchOutcome.CREATED] == 1
    assert round_.handed_out == 1
    # 第一件真的留在了外面与本地 —— 停下不撤销已经做完的事。
    assert store.execution_ref(first_item) is not None
    # 第三件一次都没被碰过：它连 ``find`` 都没有。
    assert store.execution_ref(items[2].work_item_id) is None
    assert outside.find_labels == [
        link_labels(job_id, first_item),
        link_labels(job_id, items[1].work_item_id),
    ], "第三件不该被查到 —— 停在第二件上的意思是**后面一件都不碰**"


def test_a_job_that_does_not_admit_dispatch_hands_out_nothing(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """暂停中的作业：``receipts`` 为空、外部**一次都没被读过**、理由说得出。

    这一格在碰外部**之前**返回。它不是异常：调用方要的是「这一次派发做了什么」
    的如实回答，而「按规矩什么都没做」就是那个回答的一种。断 ``calls == []``
    而不是只断 ``receipts == ()`` —— 一个先查一遍再拒绝的实现，在外部留下的是
    一次真实调用（以及与别人任务的匹配），而返回值一模一样。
    """
    job_id, _ = _compiled(store)
    store.set_state(job_id, JobState.PAUSED, expected=JobState.QUEUED, reason="上游配额异常")

    round_ = dispatch.dispatch_job(job_id)

    assert round_.receipts == ()
    assert round_.handed_out == 0
    assert "不放行派发" in round_.blocker
    assert "paused" in round_.blocker, "状态要说出来 —— 它决定了下一步是 resume 还是别的"
    # ``stopped_at`` 为空是这条判据的**判别力所在**：一个「逐件去派、让第一件撞上
    # 拒绝」的实现给出的 blocker 是**逐字相同**的一句话（两处共用
    # ``_dispatch_refused``），只有这一格说得出「它没有停在某一件事上，它压根
    # 没开始」。而这一格的差别不是文风：前者意味着外部已经被读过。
    assert round_.stopped_at == ""
    assert outside.calls == []


def test_a_job_with_no_work_items_is_not_a_refusal(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """有作业、一件工作都没有：``blocker`` **为空**，而这不是一次拒绝。

    「一件都没派」有两种成因 —— 作业不放行派发，还是作业手里一件活都没有？
    合成一句话会让前者被处置成「这个作业是空的」。经由工具进来的作业拿不到这一格
    （``compile_work_items`` 拒绝空清单），所以这里直接调存储来造它。
    """
    job = store.admit(
        scene_id=SCENE,
        input_kind=JobInputKind.KNOWN_ACCOUNTS,
        input_ref="devon-2026-09",
        idempotency_key=KEY,
        request_sha256=sha256_text(KEY),
    )

    round_ = dispatch.dispatch_job(job.job_id)

    assert round_.receipts == ()
    assert round_.blocker == "", "它不是拒绝 —— 没有理由要报"
    assert round_.stopped_at == ""
    assert round_.handed_out == 0
    assert outside.calls == []


def test_an_unknown_job_is_refused_by_the_round_too(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """作业不存在是**异常**，不是一格空的回执。

    与「不放行派发」那条的分别在于处置：一个是这次调用不合法（键错了），一个是
    一次合法的调用按规矩什么都没做。把前者也写成返回值，会让一个拼错的 job_id
    看起来像一次成功的空跑。
    """
    with pytest.raises(JobStateError) as caught:
        dispatch.dispatch_job("no-such-job")

    assert "no-such-job" in str(caught.value)
    assert outside.calls == []


# ----------------------------------------------------------------------
# 那条**不存在**的分支，以及它为什么不存在
# ----------------------------------------------------------------------
def test_a_stopped_work_item_is_always_already_mapped(
    store: InMemoryJobStore, outside: FakeDispatcher, dispatch: WorkDispatch
) -> None:
    """「已停止」蕴含「已映射」—— 所以派发里**没有**「已停止就拒」那一格。

    停止只有一个写者（``settle``），``settle`` 只收活租约，租约要求先有
    ``ExecutionRef``，而映射是写一次的。于是那条分支没有任何输入走得到 ——
    一条走不到的判据与一条恒真的健康旗是同一类东西：读代码的人会以为那里有防线。

    这条判据钉的是那个蕴含关系本身。哪天有人给映射加了「解除」入口（或者让
    ``settle`` 不收租约），它会红 —— 那一刻起那条分支才真的需要被写出来。
    """
    _, work_item_id = _compiled(store)
    dispatch.dispatch(work_item_id)
    attempt = store.lease(work_item_id, holder="worker-a")
    store.settle(attempt.attempt_id, stop_reason=StopReason.GOAL_MET, findings=Findings())
    item = store.get_work_item(work_item_id)
    assert item is not None and item.is_stopped, "这条路必须先真的停下来"

    assert store.execution_ref(work_item_id) is not None, (
        "已停止却没有映射 —— 派发里那条被省掉的分支现在需要真的写出来了"
    )
    # 再派发一次拿到的是既有的映射，而不是一条「已经停止」的拒绝。
    assert dispatch.dispatch(work_item_id).outcome is DispatchOutcome.ALREADY_MAPPED
    assert outside.calls == ["find", "create"], "停止之后一次外部都不该再碰"


# ----------------------------------------------------------------------
# 关联约定本身
# ----------------------------------------------------------------------
@pytest.mark.parametrize(("job_id", "work_item_id"), [("", "w"), ("j", ""), ("", "")])
def test_an_empty_id_is_refused_rather_than_share_a_bucket(job_id: str, work_item_id: str) -> None:
    """空 id 拼出的 label 非空，所以它会被读写两面接受 —— 那是个共用的桶。"""
    with pytest.raises(ValueError):
        link_labels(job_id, work_item_id)


def test_the_two_prefixes_are_different() -> None:
    """两个前缀必须不同 —— 用**同一个 id** 传两遍才测得出来。

    同一个 id 在同一个前缀下拼出的是同一个字符串，所以这一条断的是前缀本身。
    光看 `(job_id, work_item_id)` 的实际取值是测不出来的：它们本来就不同，
    哪怕两个前缀一模一样，拼出来的两个 label 也会不一样 —— 而那种版本里
    「有两个 label」是一句空话，所有基于 ``len() == 2`` 的断言照样全绿。
    """
    same = "a" * 32
    job_label, item_label = link_labels(same, same)
    assert job_label != item_label, (
        f"同一个 id 拼出了两个相同的 label（{job_label!r}）—— 两个前缀没有区分"
        "「这是哪一类 id」，而 label 是唯一能把一件事查回来的东西"
    )


def test_the_two_arguments_are_not_interchangeable() -> None:
    """``(job, item)`` 反着传必须得到**另一组** label —— 参数真的被用上了。"""
    forward = link_labels("j" * 8, "w" * 8)
    backward = link_labels("w" * 8, "j" * 8)
    assert set(forward) != set(backward)


# ----------------------------------------------------------------------
# 入站的反向：从一组 label 读回那个工作单元 id
#
# 出站那半（``link_labels``）只保证「写下去的是两个」。反向读是**第三个**消费者：
# ``marketops-research-worker`` 收到一条 Quay 任务，手上只有它的 label，而它要回答
# 的是「这件工作单元是谁」。读错的表现是 worker 去领一件不属于这条任务的工作，
# 而两次调用各自的日志都正常 —— 所以下面每一条都拒绝，不回落。
# ----------------------------------------------------------------------
def test_the_inverse_reads_back_the_work_item_id_link_labels_wrote() -> None:
    """**正对照**：``link_labels`` 写下去的，反着读回来是同一个 id。

    两个 id 用**实测值**而不是两个对称的假值（见模块里那两个常量与那段陷阱）。
    """
    labels = link_labels(_JOB_ID, _ITEM_ID)

    assert work_item_id_from_labels(labels) == _ITEM_ID
    assert work_item_id_from_labels(tuple(reversed(labels))) == _ITEM_ID


def test_the_inverse_reads_the_item_half_and_not_the_job_half() -> None:
    """两个前缀共享 ``marketops-``。

    按 ``marketops-`` 开头读、或者按「后端那一段」读，都会安静地读出**作业** id ——
    它同样非空、同样是个真 id，所以后面每一道存在性检查都会放它过去。
    """
    assert (
        work_item_id_from_labels([f"marketops-job:{_JOB_ID}", f"marketops-item:{_ITEM_ID}"])
        == _ITEM_ID
    )


@pytest.mark.parametrize(
    "label",
    [
        f"marketops-itemgroup:{_ITEM_ID}",
        "marketops-item:",
        f" marketops-item:{_ITEM_ID}",
        f"marketops-item:{_ITEM_ID} ",
        f"marketops-item-{_ITEM_ID}",
    ],
)
def test_a_label_that_is_not_exactly_one_of_ours_does_not_count(label: str) -> None:
    """五种「像是有、其实不是」的 label **全都**不算命中。

    * 前缀必须连**冒号**一起比 —— 否则一个未来的前缀（``marketops-item-group``）
      会被当成这一半；
    * 空尾巴（``marketops-item:``）是一个**残缺**的 label，不是「没有 label」——
      把它归进「没有」那一类，报出来的是另一件事（前者是有人写坏了，后者是这条
      任务不该由我们派发）；
    * 首尾空白**不 strip**：静默规范化的表现是一条本不该认的 label 被认了，而
      读出来的 id 看着完全正常（带尾空白的 id 会一路当成「查不到这件工作单元」，
      而那是个看起来像数据问题的症状）。
    """
    with pytest.raises(ValueError):
        work_item_id_from_labels([label])


def test_a_task_with_no_item_label_at_all_is_refused() -> None:
    """空的一组、以及只有作业那一半的 —— 都读不出工作单元 id。"""
    with pytest.raises(ValueError):
        work_item_id_from_labels([])
    with pytest.raises(ValueError):
        work_item_id_from_labels([f"marketops-job:{_JOB_ID}"])


def test_two_different_item_labels_are_refused_rather_than_resolved() -> None:
    """两条不同的工作单元 ⇒ 挑一条就是猜。

    这条任务到底是哪一件，这件事在**入站**这边没有第二个来源：Quay 的 ``-p`` 里
    那句身份只有 task id，没有 work item id。
    """
    with pytest.raises(ValueError):
        work_item_id_from_labels([f"marketops-item:{_ITEM_ID}", "marketops-item:kent-002"])


def test_the_same_item_label_twice_is_still_one_id() -> None:
    """「同一个 id 出现两次」不是「两个 id」—— 去重之后只有一个，读得出来。"""
    assert (
        work_item_id_from_labels([f"marketops-item:{_ITEM_ID}", f"marketops-item:{_ITEM_ID}"])
        == _ITEM_ID
    )
